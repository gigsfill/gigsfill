# backend/routes/admin.py - HANDLES BOTH PAY COLUMN SCENARIOS

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import logging
from sqlalchemy import text
from datetime import date
from backend.utils import utcnow_naive
from backend.db import get_db, _IS_POSTGRES
from backend.routes.auth import get_current_user
from backend.rate_limiter import limiter

router = APIRouter()


# ────────────────────────────────────────────────────────────────────────
# Schema-introspection helpers — cross-engine (SQLite + PostgreSQL).
# ────────────────────────────────────────────────────────────────────────
# Audit fix Auth-R1 (Jul 1 2026): admin.py had 19 raw `PRAGMA table_info`
# / `sqlite_master` calls scattered across endpoints. On PostgreSQL these
# throw `function pragma_table_info does not exist` / `relation
# sqlite_master does not exist`, 500-ing the entire admin dashboard the
# moment DATABASE_URL is flipped. These helpers centralize the branching
# so every caller gets the right dialect automatically.

def _list_tables(db) -> list:
    """Return list of user-defined table names in the current DB. Works
    on both engines."""
    if _IS_POSTGRES:
        rows = db.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )).fetchall()
    else:
        rows = db.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )).fetchall()
    return [r[0] for r in rows]


def _table_exists(db, table_name: str) -> bool:
    """True if `table_name` exists. Works on both engines."""
    if _IS_POSTGRES:
        return bool(db.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name = :t LIMIT 1"
        ), {"t": table_name}).first())
    return bool(db.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = :t LIMIT 1"
    ), {"t": table_name}).first())


def _column_info(db, table_name: str) -> list:
    """Return column metadata for `table_name` in a shape close to
    SQLite's PRAGMA table_info output: list of tuples
    (cid, name, type, notnull, dflt_value, pk).

    Callers that only need column NAMES should use `_column_names`.
    """
    if _IS_POSTGRES:
        rows = db.execute(text(
            "SELECT ordinal_position, column_name, data_type, is_nullable, "
            "       column_default "
            "FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name = :t "
            "ORDER BY ordinal_position"
        ), {"t": table_name}).fetchall()
        # Postgres primary-key detection is a separate query — cheap to
        # fold in for accuracy.
        pk_rows = db.execute(text(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = (SELECT oid FROM pg_class WHERE relname = :t) "
            "AND i.indisprimary"
        ), {"t": table_name}).fetchall()
        pk_names = {r[0] for r in pk_rows}
        return [
            (r[0], r[1], r[2], 1 if r[3] == 'NO' else 0,
             r[4], 1 if r[1] in pk_names else 0)
            for r in rows
        ]
    return db.execute(text(f'PRAGMA table_info("{table_name}")')).fetchall()


def _column_names(db, table_name: str) -> list:
    """Convenience: names only. Works on both engines."""
    return [c[1] for c in _column_info(db, table_name)]

def check_admin(user=Depends(get_current_user), db=Depends(get_db)):
    """Verify user is admin.

    Audit fix (May 2026): centralized via `to_admin_bool` so this gate handles
    every form the column has had — TEXT 'true'/'false' (legacy), TEXT '1'/'0'
    (post-migration), INTEGER 1/0 (clean deploy), Python bool. Previously this
    only matched the string 'true', so when the migration normalized values
    to '1'/'0' it would have locked the admin out.
    """
    from backend.utils import to_admin_bool
    result = db.execute(
        text("SELECT is_admin FROM users WHERE id = :uid"),
        {"uid": user.id}
    ).scalar()
    if not to_admin_bool(result):
        raise HTTPException(403, "Admin access required")
    return user

@router.get("/api/admin/stats")
def get_stats(admin=Depends(check_admin), db=Depends(get_db)):
    """Get dashboard statistics"""
    users_count = db.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
    artists_count = db.execute(text("SELECT COUNT(*) FROM artists")).scalar() or 0
    venues_count = db.execute(text("SELECT COUNT(*) FROM venues")).scalar() or 0
    total_gigs = db.execute(text("SELECT COUNT(*) FROM gigs")).scalar() or 0
    booked_gigs = db.execute(text("SELECT COUNT(*) FROM gigs WHERE status = 'booked'")).scalar() or 0
    
    return {
        "total_users": users_count,
        "total_artists": artists_count,
        "total_venues": venues_count,
        "total_gigs": total_gigs,
        "booked_gigs": booked_gigs
    }

@router.get("/api/admin/system-health")
def get_system_health(admin=Depends(check_admin), db=Depends(get_db)):
    """
    Returns real-time server resource metrics for the admin dashboard.
    Signals when the server is approaching capacity so admin knows when to upgrade.
    """
    import os, time

    result = {
        "db_type": "postgresql" if os.environ.get("DATABASE_URL", "").startswith("postgresql") else "sqlite",
        "workers": int(os.environ.get("WEB_CONCURRENCY", 1)),
        "redis": False,
        "memory_pct": None,
        "memory_used_mb": None,
        "memory_total_mb": None,
        "swap_pct": None,
        "cpu_pct": None,
        "disk_pct": None,
        "db_size_mb": None,
        "alerts": [],
        "warnings": [],
    }

    # ── Memory ──────────────────────────────────────────────────────────────
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                k, v = line.split(":")
                mem[k.strip()] = int(v.strip().split()[0])  # kB
        total_kb  = mem.get("MemTotal", 0)
        avail_kb  = mem.get("MemAvailable", 0)
        used_kb   = total_kb - avail_kb
        swap_total = mem.get("SwapTotal", 0)
        swap_free  = mem.get("SwapFree", 0)
        swap_used  = swap_total - swap_free

        result["memory_total_mb"] = round(total_kb / 1024)
        result["memory_used_mb"]  = round(used_kb / 1024)
        result["memory_pct"]      = round((used_kb / total_kb) * 100) if total_kb else 0
        if swap_total > 0:
            result["swap_pct"] = round((swap_used / swap_total) * 100)

        # 2026-08-09: softened alert copy. The previous "server is
        # about to crash" wording was alarmist — it reads total system
        # RAM without knowing what's driving it. On a droplet where the
        # operator SSHes in with VSCode Remote / language servers /
        # dev tools, those tools can dwarf the app's footprint. Now
        # points at the diagnostic step (top consumers via ps) so the
        # operator can distinguish "app under real pressure" from "dev
        # tools consuming RAM on the same host."
        if result["memory_pct"] >= 90:
            result["alerts"].append(
                f"🔴 RAM at {result['memory_pct']}% ({result['memory_used_mb']}MB / {result['memory_total_mb']}MB). "
                "Check top RAM consumers first (`ps aux --sort=-rss | head`) — dev tooling "
                "on the same host (VSCode Remote, language servers) can dominate. "
                "If it's the app itself, resize to a larger droplet."
            )
        elif result["memory_pct"] >= 75:
            result["warnings"].append(
                f"🟡 RAM at {result['memory_pct']}% ({result['memory_used_mb']}MB / {result['memory_total_mb']}MB). "
                "Approaching capacity — check top consumers to see whether it's the app or "
                "dev tools running on this droplet."
            )
        if result["swap_pct"] is not None and result["swap_pct"] >= 50:
            result["warnings"].append(
                f"🟡 Swap at {result['swap_pct']}%. Linux uses swap freely for cold pages "
                "and this isn't automatically bad — check `vmstat 1 5` to see whether "
                "swap-in/out is sustained (thrashing) or a one-time cold-page park."
            )
    except Exception as e:
        result["warnings"].append(f"Could not read memory stats: {e}")

    # ── CPU ─────────────────────────────────────────────────────────────────
    try:
        # Read /proc/stat twice 0.5s apart for a real CPU usage sample
        def _read_cpu():
            with open("/proc/stat") as f:
                line = f.readline()
            vals = list(map(int, line.split()[1:]))
            idle = vals[3]
            total = sum(vals)
            return idle, total

        idle1, total1 = _read_cpu()
        time.sleep(0.5)
        idle2, total2 = _read_cpu()
        cpu_pct = round(100 * (1 - (idle2 - idle1) / (total2 - total1)))
        result["cpu_pct"] = cpu_pct
        if cpu_pct >= 90:
            result["alerts"].append(
                f"🔴 CRITICAL: CPU at {cpu_pct}%. Server is overloaded — upgrade to a 2-CPU droplet."
            )
        elif cpu_pct >= 70:
            result["warnings"].append(
                f"🟡 WARNING: CPU at {cpu_pct}%. Getting busy — monitor closely."
            )
    except Exception as e:
        result["warnings"].append(f"Could not read CPU stats: {e}")

    # ── Disk ────────────────────────────────────────────────────────────────
    try:
        stat = os.statvfs("/opt/gigsfill")
        disk_total = stat.f_blocks * stat.f_frsize
        disk_free  = stat.f_bavail * stat.f_frsize
        disk_used  = disk_total - disk_free
        disk_pct   = round((disk_used / disk_total) * 100) if disk_total else 0
        result["disk_pct"] = disk_pct
        if disk_pct >= 90:
            result["alerts"].append(
                f"🔴 CRITICAL: Disk at {disk_pct}% full. Add a volume or resize droplet immediately."
            )
        elif disk_pct >= 75:
            result["warnings"].append(
                f"🟡 WARNING: Disk at {disk_pct}% full. Clean up or resize soon."
            )
    except Exception as e:
        result["warnings"].append(f"Could not read disk stats: {e}")

    # ── Database file size (SQLite only) ────────────────────────────────────
    try:
        from backend.db import DB_PATH, _IS_POSTGRES
        if not _IS_POSTGRES and DB_PATH.exists():
            db_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 1)
            result["db_size_mb"] = db_mb
            if db_mb >= 500:
                result["alerts"].append(
                    f"🔴 CRITICAL: SQLite database is {db_mb}MB. "
                    "Migrate to PostgreSQL immediately — SQLite degrades above 500MB."
                )
            elif db_mb >= 200:
                result["warnings"].append(
                    f"🟡 WARNING: SQLite database is {db_mb}MB. "
                    "Plan PostgreSQL migration soon (recommended before 500MB)."
                )
    except Exception:
        pass

    # ── Redis ───────────────────────────────────────────────────────────────
    try:
        import redis as _redis
        _r = _redis.from_url("redis://localhost:6379", socket_connect_timeout=1)
        _r.ping()
        result["redis"] = True
    except Exception:
        result["redis"] = False
        result["warnings"].append(
            "🟡 WARNING: Redis is not reachable. Rate limiting is using per-worker memory "
            "(less effective). Run: systemctl start redis-server"
        )

    # ── Concurrent users estimate (active DB connections proxy) ─────────────
    try:
        from backend.db import engine
        pool = engine.pool
        checked_out = pool.checkedout() if hasattr(pool, 'checkedout') else None
        if checked_out is not None:
            result["db_connections_active"] = checked_out
            if checked_out >= 8:
                result["warnings"].append(
                    f"🟡 WARNING: {checked_out} active DB connections — high load. "
                    "Consider upgrading droplet or migrating to PostgreSQL."
                )
    except Exception:
        pass

    # ── Upgrade recommendation ───────────────────────────────────────────────
    # 2026-08-09: only recommend an upgrade when the app itself is the
    # dominant consumer — i.e. sustained high memory AND meaningful
    # swap usage. Prevents nagging when the operator is just running
    # dev tools on the droplet during a session.
    total_mb = result.get("memory_total_mb") or 0
    if total_mb > 0 and total_mb <= 1100:
        result["droplet_size"] = "1GB"
    elif total_mb <= 2200:
        result["droplet_size"] = "2GB"
    elif total_mb <= 4400:
        result["droplet_size"] = "4GB"
    else:
        result["droplet_size"] = f"{total_mb}MB"

    # Recommend upgrade only when BOTH RAM and swap are high — a
    # transient dev-tool spike shows up as high RAM but low sustained
    # swap; a genuinely under-provisioned app shows up as both.
    _mem_high = result["memory_pct"] is not None and result["memory_pct"] >= 80
    _swap_high = result["swap_pct"] is not None and result["swap_pct"] >= 50
    result["upgrade_recommended"] = _mem_high and _swap_high
    if result["upgrade_recommended"] and total_mb <= 1100:
        result["upgrade_path"] = "Resize to a 2GB droplet in the DigitalOcean control panel."
    elif result["upgrade_recommended"] and total_mb <= 2200:
        result["upgrade_path"] = "Resize to a 4GB droplet in the DigitalOcean control panel."
    else:
        result["upgrade_path"] = None

    return result


@router.get("/api/admin/users")
def get_users(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all users"""
    # Check if last_login column exists
    cols = _column_info(db, "users")
    has_last_login = any(c[1] == 'last_login' for c in cols)
    
    if has_last_login:
        rows = db.execute(text("""
            SELECT 
                u.id,
                u.first_name,
                u.last_name,
                u.email,
                u.phone,
                u.is_admin,
                u.created_at,
                (SELECT COUNT(*) FROM artists WHERE user_id = u.id) as artist_count,
                (SELECT COUNT(*) FROM venues WHERE user_id = u.id) as venue_count,
                u.last_login
            FROM users u
            ORDER BY u.created_at DESC
        """)).fetchall()
    else:
        rows = db.execute(text("""
            SELECT 
                u.id,
                u.first_name,
                u.last_name,
                u.email,
                u.phone,
                u.is_admin,
                u.created_at,
                (SELECT COUNT(*) FROM artists WHERE user_id = u.id) as artist_count,
                (SELECT COUNT(*) FROM venues WHERE user_id = u.id) as venue_count,
                NULL as last_login
            FROM users u
            ORDER BY u.created_at DESC
        """)).fetchall()
    
    from backend.utils import to_admin_bool
    users = []
    for row in rows:
        users.append({
            'id': row[0],
            'first_name': row[1] or '',
            'last_name': row[2] or '',
            'email': row[3] or '',
            'phone': row[4] or '',
            'is_admin': to_admin_bool(row[5]),
            'created_at': row[6] if row[6] else None,
            'artist_count': row[7] or 0,
            'venue_count': row[8] or 0,
            'last_login': row[9] if row[9] else None
        })
    
    return users

@router.get("/api/admin/artists")
def get_artists(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all artists"""
    # Check if last_login column exists
    cols = _column_info(db, "users")
    has_last_login = any(c[1] == 'last_login' for c in cols)
    
    login_col = "u.last_login" if has_last_login else "NULL as last_login"
    rows = db.execute(text(f"""
        SELECT 
            a.id,
            a.name,
            a.artist_type,
            a.city,
            a.state,
            a.created_at,
            u.email as owner_email,
            {login_col}
        FROM artists a
        LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.created_at DESC
    """)).fetchall()
    
    artists = []
    for row in rows:
        artists.append({
            'id': row[0],
            'name': row[1] or '',
            'artist_type': row[2] or '',
            'city': row[3] or '',
            'state': row[4] or '',
            'created_at': row[5] if row[5] else None,
            'owner_email': row[6] or '',
            'last_login': row[7] if row[7] else None
        })
    
    return artists

@router.get("/api/admin/venues")
def get_venues(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all venues"""
    # Check if last_login column exists
    cols = _column_info(db, "users")
    has_last_login = any(c[1] == 'last_login' for c in cols)
    
    login_col = "u.last_login" if has_last_login else "NULL as last_login"
    rows = db.execute(text(f"""
        SELECT 
            v.id,
            v.venue_name,
            v.city,
            v.state,
            v.created_at,
            u.email as owner_email,
            {login_col}
        FROM venues v
        LEFT JOIN users u ON u.id = v.user_id
        ORDER BY v.created_at DESC
    """)).fetchall()
    
    venues = []
    for row in rows:
        venues.append({
            'id': row[0],
            'venue_name': row[1] or '',
            'city': row[2] or '',
            'state': row[3] or '',
            'created_at': row[4] if row[4] else None,
            'owner_email': row[5] or '',
            'last_login': row[6] if row[6] else None
        })
    
    return venues

@router.get("/api/admin/gigs")
def get_gigs(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all gigs in the system with effective pay (venue override applied)"""
    # Check if pay_dollars column exists
    columns = _column_info(db, "gigs")
    has_split_pay = any(col[1] == 'pay_dollars' for col in columns)
    
    # Multi-slot gigs have gigs.artist_id=NULL, so the LEFT JOIN to artists
    # used to leave artist_name as '--' even when slots had booked artists.
    # COALESCE with a GROUP_CONCAT subquery on gig_slots surfaces booked
    # artist names (comma-joined) for multi-slot. Single-slot path
    # (g.artist_id set) takes precedence.
    if has_split_pay:
        rows = db.execute(text("""
            SELECT
                g.id,
                g.date,
                g.start_time,
                g.end_time,
                g.pay_dollars,
                g.pay_cents,
                g.status,
                COALESCE(
                    a.name,
                    (SELECT GROUP_CONCAT(a2.name, ', ')
                     FROM gig_slots gs
                     JOIN artists a2 ON a2.id = gs.artist_id
                     WHERE gs.gig_id = g.id AND gs.status = 'booked' AND gs.artist_id IS NOT NULL)
                ) as artist_name,
                v.venue_name,
                v.city,
                v.state,
                a.id as artist_id,
                v.id as venue_id,
                pa.pay_dollars_override
            FROM gigs g
            LEFT JOIN artists a ON a.id = g.artist_id
            LEFT JOIN venues v ON v.id = g.venue_id
            LEFT JOIN preferred_artists pa ON pa.venue_id = g.venue_id AND pa.artist_id = g.artist_id AND pa.status = 'approved'
            ORDER BY g.date DESC, g.start_time ASC
        """)).fetchall()
        
        gigs = []
        for row in rows:
            gig_pay_dollars = row[4] or 0
            gig_pay_cents = row[5] or 0
            override_pay = row[13]
            
            # Compute effective pay: max of gig pay vs venue override
            gig_total_cents = gig_pay_dollars * 100 + gig_pay_cents
            if override_pay is not None:
                override_cents = int(float(override_pay) * 100)
                effective_cents = max(gig_total_cents, override_cents)
            else:
                effective_cents = gig_total_cents
            
            gigs.append({
                'id': row[0],
                'date': row[1] or '',
                'start_time': row[2] or '',
                'end_time': row[3] or '',
                'pay_dollars': effective_cents // 100,
                'pay_cents': effective_cents % 100,
                'status': row[6] or '',
                'artist_name': row[7] or '--',
                'venue_name': row[8] or '',
                'city': row[9] or '',
                'state': row[10] or '',
                'artist_id': row[11],
                'venue_id': row[12]
            })
    else:
        rows = db.execute(text("""
            SELECT
                g.id,
                g.date,
                g.start_time,
                g.end_time,
                g.pay,
                g.status,
                COALESCE(
                    a.name,
                    (SELECT GROUP_CONCAT(a2.name, ', ')
                     FROM gig_slots gs
                     JOIN artists a2 ON a2.id = gs.artist_id
                     WHERE gs.gig_id = g.id AND gs.status = 'booked' AND gs.artist_id IS NOT NULL)
                ) as artist_name,
                v.venue_name,
                v.city,
                v.state,
                a.id as artist_id,
                v.id as venue_id,
                pa.pay_dollars_override
            FROM gigs g
            LEFT JOIN artists a ON a.id = g.artist_id
            LEFT JOIN venues v ON v.id = g.venue_id
            LEFT JOIN preferred_artists pa ON pa.venue_id = g.venue_id AND pa.artist_id = g.artist_id AND pa.status = 'approved'
            ORDER BY g.date DESC, g.start_time ASC
        """)).fetchall()
        
        gigs = []
        for row in rows:
            pay_value = float(row[4]) if row[4] else 0
            override_pay = row[12]
            if override_pay is not None:
                pay_value = max(pay_value, float(override_pay))
            gigs.append({
                'id': row[0],
                'date': row[1] or '',
                'start_time': row[2] or '',
                'end_time': row[3] or '',
                'pay_dollars': int(pay_value),
                'pay_cents': int((pay_value % 1) * 100),
                'status': row[5] or '',
                'artist_name': row[6] or '--',
                'venue_name': row[7] or '',
                'city': row[8] or '',
                'state': row[9] or '',
                'artist_id': row[10],
                'venue_id': row[11]
            })
    
    return gigs

@router.get("/api/admin/settings")
def get_settings(admin=Depends(check_admin), db=Depends(get_db)):
    """Get platform settings"""
    results = db.execute(
        text("SELECT setting_key, setting_value FROM platform_settings")
    ).fetchall()
    
    settings = {}
    for row in results:
        key, value = row[0], row[1]
        if value and value.lower() in ['true', '1']:
            settings[key] = True
        elif value and value.lower() in ['false', '0']:
            settings[key] = False
        else:
            try:
                settings[key] = float(value)
            except:
                settings[key] = value
    
    # Never return passwords/secrets to the frontend — return masked indicators instead
    def _mask(val):
        return "••••••••" if val else ""

    return {
        'commission': settings.get('commission_percentage', 0),
        'platform_email': settings.get('platform_email', ''),
        'platform_email_password': _mask(settings.get('platform_email_password', '')),
        'platform_smtp_server': settings.get('platform_smtp_server', 'smtp.gmail.com'),
        'platform_smtp_port': int(settings.get('platform_smtp_port', 587)) if settings.get('platform_smtp_port') else 587,
        'platform_email_from_name': settings.get('platform_email_from_name', ''),
        'support_email': settings.get('support_email', ''),
        'support_email_password': _mask(settings.get('support_email_password', '')),
        'support_smtp_server': settings.get('support_smtp_server', 'smtp.gmail.com'),
        'support_smtp_port': int(settings.get('support_smtp_port', 587)) if settings.get('support_smtp_port') else 587,
        'support_email_from_name': settings.get('support_email_from_name', ''),
        'support_display_name': settings.get('support_display_name', ''),
        'admin_alert_email': settings.get('admin_alert_email', ''),
        'signups_enabled': settings.get('signups_enabled', True),
        'maintenance_mode': settings.get('maintenance_mode', False),
        'maintenance_message': settings.get('maintenance_message', ''),
        # Distance (miles) beyond which a booking triggers a "far-away artist"
        # heads-up to the venue + a soft notice on the artist's confirmation.
        'far_booking_alert_miles': int(settings.get('far_booking_alert_miles', 50)) if settings.get('far_booking_alert_miles') else 50,
        # Rate limits (integer requests/minute per IP). Defaults mirror
        # backend/rate_limiter.py:_DEFAULTS so the form populates with the
        # live values instead of greyed-out placeholders.
        'rate_login':          int(settings.get('rate_login', 5))           if settings.get('rate_login')          else 5,
        'rate_signup':         int(settings.get('rate_signup', 3))          if settings.get('rate_signup')         else 3,
        'rate_password_reset': int(settings.get('rate_password_reset', 3))  if settings.get('rate_password_reset') else 3,
        'rate_support':        int(settings.get('rate_support', 2))         if settings.get('rate_support')        else 2,
        'rate_email_send':     int(settings.get('rate_email_send', 10))     if settings.get('rate_email_send')     else 10,
        'rate_aff_track':      int(settings.get('rate_aff_track', 30))      if settings.get('rate_aff_track')      else 30,
        # Part 10p Phase 3: bounce-check (password masked like the other SMTP secrets)
        'bounce_check_enabled':       str(settings.get('bounce_check_enabled', False)).lower() in ('true', '1', 'yes'),
        'bounce_check_imap_server':   settings.get('bounce_check_imap_server', ''),
        'bounce_check_imap_port':     int(settings.get('bounce_check_imap_port', 993)) if settings.get('bounce_check_imap_port') else 993,
        'bounce_check_imap_username': settings.get('bounce_check_imap_username', ''),
        'bounce_check_imap_password': _mask(settings.get('bounce_check_imap_password', '')),
        'bounce_check_last_run_at':   settings.get('bounce_check_last_run_at', ''),
        'bounce_check_last_result':   settings.get('bounce_check_last_result', ''),
        # Bounce-check credentials source — 'platform' (default) / 'support' /
        # 'custom'. When platform/support the scheduler uses that account's
        # stored email + password for IMAP login; when custom it falls back
        # to bounce_check_imap_username + _password.
        'bounce_check_source':        settings.get('bounce_check_source', 'platform'),
        # Texting (SMS) global on/off — defaults to false until a real
        # SMS provider (Twilio etc.) is wired up. While false, all
        # user-facing SMS UI is hidden site-wide and dispatch is no-op'd.
        'texting_enabled':            str(settings.get('texting_enabled', False)).lower() in ('true', '1', 'yes'),
        # Demo pipeline — email address that receives new demo request
        # notifications, and the default video-call URL (Teams / Zoom /
        # Meet) embedded into confirmation + reminder emails when a demo
        # is scheduled and no per-row override is set.
        'demo_request_admin_email':   settings.get('demo_request_admin_email', ''),
        'demo_meeting_url':           settings.get('demo_meeting_url', ''),
    }

@router.put("/api/admin/settings")
async def update_settings(request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Update platform settings"""
    data = await request.json()
    
    key_mapping = {
        'commission': 'commission_percentage',
        'platform_email': 'platform_email',
        'platform_email_password': 'platform_email_password',
        'platform_smtp_server': 'platform_smtp_server',
        'platform_smtp_port': 'platform_smtp_port',
        'platform_email_from_name': 'platform_email_from_name',
        'support_email': 'support_email',
        'support_email_password': 'support_email_password',
        'support_smtp_server': 'support_smtp_server',
        'support_smtp_port': 'support_smtp_port',
        'support_email_from_name': 'support_email_from_name',
        'support_display_name': 'support_display_name',
        'admin_alert_email': 'admin_alert_email',
        'signups_enabled': 'signups_enabled',
        'maintenance_mode': 'maintenance_mode',
        'maintenance_message': 'maintenance_message',
        'far_booking_alert_miles': 'far_booking_alert_miles',
        # Rate limits (integer requests/minute) — see backend/rate_limiter.py for
        # the callable getters that read these live. invalidate_cache() below
        # forces immediate re-read instead of waiting for the 30s TTL.
        'rate_login':          'rate_login',
        'rate_signup':         'rate_signup',
        'rate_password_reset': 'rate_password_reset',
        'rate_support':        'rate_support',
        'rate_email_send':     'rate_email_send',
        'rate_aff_track':      'rate_aff_track',
        # Part 10p Phase 3: async bounce-check via IMAP (settings only;
        # actual polling happens in the scheduler).
        'bounce_check_enabled':       'bounce_check_enabled',
        'bounce_check_imap_server':   'bounce_check_imap_server',
        'bounce_check_imap_port':     'bounce_check_imap_port',
        'bounce_check_imap_username': 'bounce_check_imap_username',
        'bounce_check_imap_password': 'bounce_check_imap_password',
        # Bounce-check credentials source — 'platform' / 'support' / 'custom'.
        'bounce_check_source':        'bounce_check_source',
        # Texting (SMS) global on/off — see GET handler for docstring.
        'texting_enabled':            'texting_enabled',
        # Demo pipeline settings — admin notify address + default video URL.
        'demo_request_admin_email':   'demo_request_admin_email',
        'demo_meeting_url':           'demo_meeting_url',
    }
    _RATE_KEYS = {'rate_login','rate_signup','rate_password_reset','rate_support','rate_email_send','rate_aff_track'}

    # bounce_check_imap_password is treated same as the other email passwords —
    # never echoed back to the frontend in GET, masked in audit log on PUT.
    SENSITIVE_KEYS = {'platform_email_password', 'support_email_password', 'bounce_check_imap_password'}
    # Audit fix (May 2026): capture before-state for the audit log so a
    # future incident can answer "what did the platform_fee look like
    # before admin X changed it on date Y?"
    _audit_before = {}
    _audit_after  = {}
    _rate_keys_changed = False
    for frontend_key, db_key in key_mapping.items():
        if frontend_key in data:
            value = data[frontend_key]
            # Skip masked placeholder values — don't overwrite real password with mask
            if db_key in SENSITIVE_KEYS and str(value).startswith("•"):
                continue
            # Validate rate-limit settings — must be a positive integer. Reject
            # garbage rather than silently storing it (the rate_limiter falls
            # back to defaults on a parse miss, but the admin should know).
            if db_key in _RATE_KEYS:
                try:
                    _n = int(str(value).strip())
                    if _n < 1 or _n > 100000:
                        raise ValueError("out of range")
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{db_key} must be a positive integer (requests per minute)")
                value = _n
                _rate_keys_changed = True
            value_str = str(value) if not isinstance(value, bool) else ('true' if value else 'false')

            existing = db.execute(
                text("SELECT setting_value FROM platform_settings WHERE setting_key = :key"),
                {"key": db_key}
            ).mappings().first()

            # Don't audit-leak secrets — record only that the key changed.
            _before_val = (existing.get("setting_value") if existing else None) if db_key not in SENSITIVE_KEYS else "(redacted)"
            _after_val  = value_str if db_key not in SENSITIVE_KEYS else "(redacted)"
            if existing and existing.get("setting_value") != value_str:
                _audit_before[db_key] = _before_val
                _audit_after[db_key]  = _after_val
            elif not existing:
                _audit_after[db_key] = _after_val

            # Audit fix (May 2026 part 7): SELECT-then-INSERT is racy. Two
            # concurrent admin PUTs creating the same new key both hit the
            # INSERT branch and the loser raises IntegrityError on the
            # `setting_key UNIQUE` constraint → 500 to the user. Use upsert.
            if existing:
                db.execute(
                    text("UPDATE platform_settings SET setting_value = :val WHERE setting_key = :key"),
                    {"val": value_str, "key": db_key}
                )
            else:
                try:
                    db.execute(
                        text("INSERT INTO platform_settings (setting_key, setting_value) VALUES (:key, :val)"),
                        {"key": db_key, "val": value_str}
                    )
                except Exception:
                    # Loser of the race — convert to an UPDATE.
                    db.execute(
                        text("UPDATE platform_settings SET setting_value = :val WHERE setting_key = :key"),
                        {"val": value_str, "key": db_key}
                    )

    db.commit()

    if _audit_after:
        from backend.utils import log_admin_action
        log_admin_action(db, admin, "update_settings", target_table="platform_settings",
                         before=_audit_before, after=_audit_after, request=request)

    # Invalidate the rate-limiter cache so admins see their new limits take
    # effect immediately instead of waiting up to 30s for the TTL to expire.
    if _rate_keys_changed:
        try:
            from backend.rate_limiter import invalidate_cache as _rl_invalidate
            _rl_invalidate()
        except Exception:
            pass

    return {"ok": True}


@router.post("/api/admin/bounce-check/run-now")
def bounce_check_run_now(admin=Depends(check_admin), db=Depends(get_db)):
    """Run the bounce-inbox poll synchronously and return the result. Used by
    the 'Test Connection' button in Admin → Email Settings → Bounce Detection.

    The scheduler runs this same function every 30 minutes when enabled, but
    this endpoint lets the admin verify their IMAP config without waiting.
    """
    from backend.scheduler import process_bounce_inbox
    from backend.db import get_db_connection
    conn = get_db_connection()
    try:
        result = process_bounce_inbox(conn)
        return {"ok": True, **result}
    finally:
        try: conn.close()
        except Exception: pass


@router.get("/api/email-templates")
def get_email_templates(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all email templates — merges DB rows with code-defined TEMPLATES so nothing is ever missing."""
    from backend.email_templates import TEMPLATES as _CODE_TEMPLATES

    # Start with all templates defined in code (source of truth)
    merged = {}
    for key, tpl in _CODE_TEMPLATES.items():
        merged[key] = {'template_type': key, 'subject': tpl['subject'], 'body': tpl['body']}

    # Overlay with any DB customisations (admin edits take precedence over defaults)
    try:
        try:
            rows = db.execute(text("SELECT notification_type as key, subject, body FROM email_templates")).fetchall()
        except Exception:
            rows = db.execute(text("SELECT template_key as key, subject, body FROM email_templates")).fetchall()
        for row in rows:
            key = row[0]
            if key:
                merged[key] = {'template_type': key, 'subject': row[1] or '', 'body': row[2] or ''}
    except Exception:
        pass

    # Return sorted alphabetically for a clean pulldown
    return sorted(merged.values(), key=lambda t: t['template_type'])

@router.put("/api/email-templates")
async def update_email_template(request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Update or create email template — upserts by template_key"""
    data = await request.json()
    template_type = data.get('template_type')
    new_subject = data.get('subject')
    new_body = data.get('body')

    # Capture before-state for audit
    before_row = None
    try:
        before_row = db.execute(
            text("SELECT subject, body FROM email_templates WHERE template_key = :k OR notification_type = :k LIMIT 1"),
            {"k": template_type}
        ).mappings().first()
    except Exception:
        pass

    try:
        db.execute(
            text("""
                INSERT INTO email_templates (template_key, subject, body)
                VALUES (:type, :subj, :body)
                ON CONFLICT(template_key) DO UPDATE SET
                    subject = excluded.subject,
                    body = excluded.body,
                    updated_at = CURRENT_TIMESTAMP
            """),
            {'type': template_type, 'subj': new_subject, 'body': new_body}
        )

        db.commit()

        # Audit fix (May 2026 part 5): the audit row used to store the
        # full HTML body (multi-KB per template × frequent edits =
        # ballooning admin_audit_log table). Keep the subject in full
        # but store a length + truncated preview of the body. The full
        # body lives in `email_templates.body` already; the audit just
        # needs to record "was edited" + key metadata for the trail.
        def _trunc(s, n=400):
            s = s or ""
            return s if len(s) <= n else s[:n] + f"… (+{len(s) - n} chars)"
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "update_email_template",
            target_table="email_templates", target_id=template_type,
            before={
                "subject": (before_row["subject"] if before_row else None),
                "body_len": len(before_row["body"] or "") if before_row else 0,
                "body_preview": _trunc(before_row["body"]) if before_row else None,
            } if before_row else None,
            after={
                "subject": new_subject,
                "body_len": len(new_body or ""),
                "body_preview": _trunc(new_body),
            },
            request=request,
        )

        # Audit fix (May 2026 part 2): auto-export to disk so admin edits
        # survive the next deploy (run_migration repopulates DB from file).
        # If the disk write fails (permission, full disk), surface it as
        # a soft warning — the DB write already succeeded.
        ok, info = _export_email_templates_to_disk(db)
        if not ok:
            return {"ok": True, "export_error": str(info)}
        return {"ok": True, "exported": info}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Operation failed. Please try again.")

def _export_email_templates_to_disk(db):
    """Internal helper: re-generate backend/email_templates.py from the DB.
    Returns (ok: bool, count_or_message). Called by the public export
    endpoint AND by update_email_template's auto-export. Audit fix
    (May 2026 part 2): admin PUTs were only writing to DB, so on the
    next deploy the file-side TEMPLATES dict would overwrite all admin
    edits via run_migration(). Now every PUT triggers this sync."""
    from pathlib import Path
    try:
        # Read all templates from DB
        try:
            rows = db.execute(text(
                "SELECT template_key, subject, body FROM email_templates ORDER BY id"
            )).fetchall()
        except:
            rows = db.execute(text(
                "SELECT notification_type as template_key, subject, body FROM email_templates ORDER BY id"
            )).fetchall()
        
        if not rows:
            raise HTTPException(400, "No templates found in database")
        
        # Audit fix (May 2026 part 5): use repr() so Python handles all
        # escaping deterministically. The previous hand-rolled escape only
        # touched `\` and `'''`, so:
        #   - A body ending in `'` produced four consecutive quotes
        #     (`''''...'''`), a SyntaxError on next import → service crash.
        #   - A body containing `'''` was silently corrupted to `' ' '`
        #     (mangled output) instead of escaping.
        # repr() handles quotes, backslashes, newlines, and unicode cleanly
        # and yields a valid Python string literal in all cases.
        template_entries = []
        for row in rows:
            key = row[0]
            subject = row[1] or ""
            body = row[2] or ""

            template_entries.append(
                f'    {repr(str(key))}: {{\n'
                f"        \"subject\": {repr(subject)},\n"
                f"        \"body\": {repr(body)}\n"
                f'    }}'
            )

        templates_block = ",\n\n".join(template_entries)
        
        # Generate complete file
        python_code = f'''"""
Email Templates for GigsFill
=============================
Auto-generated from database via Admin > Export All.
Do not edit manually — changes will be overwritten on next export.
"""
import logging
import sqlite3
from datetime import datetime
logger = logging.getLogger("gigsfill.admin")

TEMPLATES = {{

{templates_block},

}}

def run_migration():
    """Populate email templates in database"""
    from backend.db import get_db_connection as _admin_raw_conn, _IS_POSTGRES
    conn = _admin_raw_conn()
    cursor = conn.cursor()

    # Check if table exists (syntax differs between SQLite and PostgreSQL)
    if _IS_POSTGRES:
        cursor.execute("SELECT 1 FROM information_schema.tables WHERE table_name='email_templates'")
    else:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='email_templates'")
    if not cursor.fetchone():
        cursor.execute("""
            CREATE TABLE email_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_type TEXT UNIQUE NOT NULL,
                template_key TEXT,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    
    # Check columns.
    # Audit fix (May 2026 part 6): branch on _IS_POSTGRES; PRAGMA is SQLite-only.
    if _IS_POSTGRES:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='email_templates'")
        columns = [col[0] for col in cursor.fetchall()]
    else:
        cursor.execute("PRAGMA table_info(email_templates)")
        columns = [col[1] for col in cursor.fetchall()]
    key_column = 'notification_type' if 'notification_type' in columns else 'template_key'
    
    for notification_type, template in TEMPLATES.items():
        cursor.execute(f"SELECT id FROM email_templates WHERE {{key_column}} = ?", (notification_type,))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute(f"""
                UPDATE email_templates SET subject = ?, body = ?, updated_at = CURRENT_TIMESTAMP
                WHERE {{key_column}} = ?
            """, (template['subject'], template['body'], notification_type))
        else:
            try:
                cursor.execute("""
                    INSERT INTO email_templates (template_key, notification_type, subject, body)
                    VALUES (?, ?, ?, ?)
                """, (notification_type, notification_type, template['subject'], template['body']))
            except:
                cursor.execute(f"""
                    INSERT INTO email_templates ({{key_column}}, subject, body)
                    VALUES (?, ?, ?)
                """, (notification_type, template['subject'], template['body']))
    
    conn.commit()
    conn.close()
    logger.info(f"Email templates populated ({{len(TEMPLATES)}} templates)")

if __name__ == "__main__":
    run_migration()
'''
        
        # Write directly to backend/email_templates.py
        email_templates_path = Path(__file__).parent.parent / "email_templates.py"
        with open(email_templates_path, 'w', encoding='utf-8') as f:
            f.write(python_code)

        return (True, len(rows))
    except Exception as e:
        logger.warning(f"_export_email_templates_to_disk failed: {e}", exc_info=True)
        return (False, str(e))


@router.get("/api/email-templates/export")
def export_email_templates(admin=Depends(check_admin), db=Depends(get_db)):
    """Manual trigger for the file-export helper above."""
    ok, info = _export_email_templates_to_disk(db)
    if not ok:
        raise HTTPException(500, f"Operation failed: {info}")
    return {"status": "ok", "message": f"Exported {info} templates to backend/email_templates.py", "count": info}

# ==========================================
# PAYMENT SETTINGS
# ==========================================

@router.get("/api/admin/payment-settings")
def get_payment_settings(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all payment-related platform settings"""
    payment_keys = [
        'payments_enabled',
        'payment_processing_delay_days',
        'payment_processing_hour',
        'platform_fee_percent',
        'platform_fee_split',
        'platform_min_fee',
        'admin_stripe_publishable_key',
        'admin_stripe_secret_key',
        'admin_stripe_webhook_secret',
    ]
    
    SENSITIVE_PAYMENT_KEYS = {'admin_stripe_secret_key', 'admin_stripe_webhook_secret'}
    settings = {}
    for key in payment_keys:
        result = db.execute(
            text("SELECT setting_value FROM platform_settings WHERE setting_key = :key"),
            {"key": key}
        ).scalar()
        # Mask secrets — never return raw keys to browser
        if key in SENSITIVE_PAYMENT_KEYS:
            settings[key] = "••••••••" if result else ''
        else:
            settings[key] = result if result else ''
    
    return settings

@router.put("/api/admin/payment-settings")
async def update_payment_settings(request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Update payment-related platform settings"""
    data = await request.json()
    
    payment_keys = [
        'payments_enabled',
        'payment_processing_delay_days',
        'payment_processing_hour',
        'platform_fee_percent',
        'platform_fee_split',
        'platform_min_fee',
        'admin_stripe_publishable_key',
        'admin_stripe_secret_key',
        'admin_stripe_webhook_secret',
    ]
    
    SENSITIVE_PAYMENT_KEYS = {'admin_stripe_secret_key', 'admin_stripe_webhook_secret'}

    # Capture before-state for audit (mask sensitive values; we record the
    # fact a secret changed without ever storing the secret itself)
    _audit_before, _audit_after = {}, {}
    for key in payment_keys:
        existing_val = db.execute(
            text("SELECT setting_value FROM platform_settings WHERE setting_key = :k"),
            {"k": key}
        ).scalar()
        _audit_before[key] = '••••••••' if (key in SENSITIVE_PAYMENT_KEYS and existing_val) else (existing_val or '')

    # Audit fix (May 2026): validate numeric / enum settings before write.
    # Without this, admin could persist 'platform_fee_percent=-50' (negative
    # fees turn into 0 via max(), so commissions silently go to zero) or
    # 'payment_processing_hour=99' (scheduler tick never fires), etc.
    def _validate(key, raw):
        s = str(raw if raw is not None else '').strip()
        if key == 'platform_fee_percent':
            try:
                f = float(s)
            except ValueError:
                raise HTTPException(400, f"{key} must be a number 0-100")
            if not (0.0 <= f <= 100.0):
                raise HTTPException(400, f"{key} must be between 0 and 100")
        elif key == 'platform_min_fee':
            try:
                f = float(s)
            except ValueError:
                raise HTTPException(400, f"{key} must be a non-negative number")
            if f < 0:
                raise HTTPException(400, f"{key} must be >= 0")
        elif key == 'stripe_processing_fee_percent':
            try:
                f = float(s)
            except ValueError:
                raise HTTPException(400, f"{key} must be a number 0-100")
            if not (0.0 <= f <= 100.0):
                raise HTTPException(400, f"{key} must be between 0 and 100")
        elif key == 'stripe_per_transaction_fee':
            try:
                f = float(s)
            except ValueError:
                raise HTTPException(400, f"{key} must be a non-negative number")
            if f < 0:
                raise HTTPException(400, f"{key} must be >= 0")
        elif key == 'platform_fee_split':
            if s not in ('split', 'venue_only', 'artist_only'):
                raise HTTPException(400, f"{key} must be one of split / venue_only / artist_only")
        elif key == 'payment_processing_hour':
            try:
                h = int(s)
            except ValueError:
                raise HTTPException(400, f"{key} must be an integer 0-23")
            if not (0 <= h <= 23):
                raise HTTPException(400, f"{key} must be between 0 and 23")

    for key in payment_keys:
        if key in data:
            value = str(data[key]) if data[key] is not None else ''
            # Skip masked placeholder values
            if key in SENSITIVE_PAYMENT_KEYS and value.startswith("•"):
                continue
            _validate(key, value)

            existing = db.execute(
                text("SELECT id FROM platform_settings WHERE setting_key = :key"),
                {"key": key}
            ).first()

            if existing:
                db.execute(
                    text("UPDATE platform_settings SET setting_value = :val WHERE setting_key = :key"),
                    {"val": value, "key": key}
                )
            else:
                db.execute(
                    text("INSERT INTO platform_settings (setting_key, setting_value) VALUES (:key, :val)"),
                    {"key": key, "val": value}
                )
            _audit_after[key] = '••••••••' if key in SENSITIVE_PAYMENT_KEYS else value

    db.commit()

    if _audit_after:
        from backend.utils import log_admin_action
        # Trim before-state to only the keys actually changed, and only
        # record entries where value differs from before
        changed_before = {k: _audit_before.get(k, '') for k in _audit_after.keys()
                          if _audit_before.get(k, '') != _audit_after[k]}
        changed_after = {k: v for k, v in _audit_after.items()
                         if _audit_before.get(k, '') != v}
        if changed_after:
            log_admin_action(
                db, admin, "update_payment_settings",
                target_table="platform_settings",
                before=changed_before, after=changed_after,
                request=request,
            )
    return {"ok": True}

# ==========================================
# VENUE PAYMENT OVERRIDES (Free Trial)
# ==========================================

@router.get("/api/admin/venue-payment-overrides")
def get_venue_payment_overrides(admin=Depends(check_admin), db=Depends(get_db)):
    """Get ALL venues with their free-trial (payment suspension) status"""
    rows = db.execute(text("""
        SELECT
            v.id,
            v.venue_name,
            v.city,
            v.state,
            u.email as owner_email,
            CASE WHEN vpo.payments_suspended = 1 THEN 1 ELSE 0 END as payments_suspended,
            vpo.notes
        FROM venues v
        LEFT JOIN users u ON u.id = v.user_id
        LEFT JOIN venue_payment_overrides vpo ON vpo.venue_id = v.id
        ORDER BY (CASE WHEN vpo.payments_suspended = 1 THEN 0 ELSE 1 END), v.venue_name ASC
    """)).fetchall()

    return [
        {
            'id': row[0],
            'venue_name': row[1] or '',
            'city': row[2] or '',
            'state': row[3] or '',
            'owner_email': row[4] or '',
            'payments_suspended': bool(row[5]),
            'notes': row[6] or '',
        }
        for row in rows
    ]

@router.get("/api/admin/venues/search")
def search_venues_admin(q: str = "", letter: str = "", offset: int = 0, limit: int = 50, admin=Depends(check_admin), db=Depends(get_db)):
    """Search venues by name or browse by letter, with pagination. Suspended venues sort first."""
    
    if letter:
        # Browse by letter — # means non-alpha (numbers, symbols)
        if letter == '#':
            count_row = db.execute(text("""
                SELECT COUNT(*) FROM venues 
                WHERE UPPER(SUBSTR(venue_name, 1, 1)) NOT BETWEEN 'A' AND 'Z'
            """)).fetchone()
            total = count_row[0] if count_row else 0
            
            rows = db.execute(text("""
                SELECT 
                    v.id, v.venue_name, v.city, v.state,
                    u.email as owner_email,
                    CASE WHEN vpo.payments_suspended = 1 THEN 1 ELSE 0 END as is_suspended,
               vpo.notes
                FROM venues v
                LEFT JOIN users u ON u.id = v.user_id
                LEFT JOIN venue_payment_overrides vpo ON vpo.venue_id = v.id
                WHERE UPPER(SUBSTR(v.venue_name, 1, 1)) NOT BETWEEN 'A' AND 'Z'
                ORDER BY (CASE WHEN vpo.payments_suspended = 1 THEN 0 ELSE 1 END), v.venue_name ASC
                LIMIT :limit OFFSET :offset
            """), {"limit": limit, "offset": offset}).fetchall()
        else:
            count_row = db.execute(text("""
                SELECT COUNT(*) FROM venues 
                WHERE UPPER(SUBSTR(venue_name, 1, 1)) = :letter
            """), {"letter": letter.upper()}).fetchone()
            total = count_row[0] if count_row else 0
            
            rows = db.execute(text("""
                SELECT 
                    v.id, v.venue_name, v.city, v.state,
                    u.email as owner_email,
                    CASE WHEN vpo.payments_suspended = 1 THEN 1 ELSE 0 END as is_suspended,
               vpo.notes
                FROM venues v
                LEFT JOIN users u ON u.id = v.user_id
                LEFT JOIN venue_payment_overrides vpo ON vpo.venue_id = v.id
                WHERE UPPER(SUBSTR(v.venue_name, 1, 1)) = :letter
                ORDER BY (CASE WHEN vpo.payments_suspended = 1 THEN 0 ELSE 1 END), v.venue_name ASC
                LIMIT :limit OFFSET :offset
            """), {"letter": letter.upper(), "limit": limit, "offset": offset}).fetchall()
    elif q and len(q) >= 1:
        count_row = db.execute(text("""
            SELECT COUNT(*) FROM venues WHERE LOWER(venue_name) LIKE LOWER(:q)
        """), {"q": f"%{q}%"}).fetchone()
        total = count_row[0] if count_row else 0
        
        rows = db.execute(text("""
            SELECT 
                v.id, v.venue_name, v.city, v.state,
                u.email as owner_email,
                CASE WHEN vpo.payments_suspended = 1 THEN 1 ELSE 0 END as is_suspended,
                vpo.notes
            FROM venues v
            LEFT JOIN users u ON u.id = v.user_id
            LEFT JOIN venue_payment_overrides vpo ON vpo.venue_id = v.id
            WHERE LOWER(v.venue_name) LIKE LOWER(:q)
            ORDER BY (CASE WHEN vpo.payments_suspended = 1 THEN 0 ELSE 1 END), v.venue_name ASC
            LIMIT :limit OFFSET :offset
        """), {"q": f"%{q}%", "limit": limit, "offset": offset}).fetchall()
    else:
        return []
    
    venues = []
    for row in rows:
        venues.append({
            'id': row[0],
            'venue_name': row[1] or '',
            'city': row[2] or '',
            'state': row[3] or '',
            'owner_email': row[4] or '',
            'payments_suspended': bool(row[5]),
            'notes': row[6] if len(row) > 6 else '',
        })

    return venues

@router.get("/api/admin/venue-payment-overrides/letters")
def get_suspended_venue_letters(admin=Depends(check_admin), db=Depends(get_db)):
    """Return all A-Z + # letters; suspended-venue letters are flagged for glowing."""
    # Get letters that have at least one venue
    all_rows = db.execute(text("""
        SELECT DISTINCT UPPER(SUBSTR(venue_name, 1, 1)) as ch FROM venues ORDER BY ch
    """)).fetchall()
    
    # Get letters with free-trial / suspended venues
    suspended_rows = db.execute(text("""
        SELECT DISTINCT UPPER(SUBSTR(v.venue_name, 1, 1)) as ch
        FROM venue_payment_overrides vpo
        JOIN venues v ON v.id = vpo.venue_id
        WHERE vpo.payments_suspended = 1
    """)).fetchall()
    suspended_letters = set(r[0] for r in suspended_rows if r[0])

    result = []
    # Always include A-Z and #
    for letter in list('ABCDEFGHIJKLMNOPQRSTUVWXYZ') + ['#']:
        result.append({"letter": letter, "active": letter in suspended_letters})
    
    return result

@router.post("/api/admin/venue-payment-overrides")
async def toggle_venue_payment_override(request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Toggle payment suspension for a venue (add or update override)"""
    from datetime import datetime
    
    data = await request.json()
    venue_id = data.get("venue_id")
    suspend = data.get("payments_suspended", True)
    notes = data.get("notes", "")
    
    if not venue_id:
        raise HTTPException(400, "venue_id is required")
    
    # Verify venue exists
    venue = db.execute(
        text("SELECT id, venue_name FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).fetchone()
    
    if not venue:
        raise HTTPException(404, "Venue not found")
    
    # Check if override already exists
    existing = db.execute(
        text("SELECT id FROM venue_payment_overrides WHERE venue_id = :vid"),
        {"vid": venue_id}
    ).fetchone()
    
    if existing:
        if suspend:
            db.execute(
                text("""
                    UPDATE venue_payment_overrides 
                    SET payments_suspended = 1, suspended_by = :uid, suspended_at = :now, notes = :notes
                    WHERE venue_id = :vid
                """),
                {"vid": venue_id, "uid": admin.id, "now": utcnow_naive(), "notes": notes}
            )
        else:
            db.execute(
                text("DELETE FROM venue_payment_overrides WHERE venue_id = :vid"),
                {"vid": venue_id}
            )
    else:
        if suspend:
            db.execute(
                text("""
                    INSERT INTO venue_payment_overrides (venue_id, payments_suspended, suspended_by, suspended_at, notes)
                    VALUES (:vid, 1, :uid, :now, :notes)
                """),
                {"vid": venue_id, "uid": admin.id, "now": utcnow_naive(), "notes": notes}
            )
    
    db.commit()

    # When ending free trial: restore any 'suspended' transactions to 'scheduled'/'test'
    # so the payout scheduler picks them up for real charging
    if not suspend:
        from sqlalchemy import text as _text
        # Test Mode removed (Jul 1 2026): restore always goes to 'scheduled'.
        restore_status = 'scheduled'
        db.execute(_text("""
            UPDATE transactions SET
                status = :rs,
                notes = COALESCE(notes || ' | ', '') || 'Free trial ended — restored to queue'
            WHERE status = 'suspended'
              AND gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid)
        """), {"rs": restore_status, "vid": venue_id})
        db.commit()

        # MAY 11 2026: convert upcoming free-trial audit rows into real
        # venue_charge transactions so the venue gets billed for future gigs
        # that were booked while on free trial. The override has just been
        # deleted above, so _create_booking_transaction will follow the
        # normal billing path. Past free-trial gigs are LEFT AS-IS — we
        # don't retroactively bill for gigs that already happened.
        _convert_upcoming_free_trial_bookings(db, venue_id)
    else:
        # 2026-08-24 (symmetric): trial JUST turned ON. Convert existing
        # future scheduled txns → free_trial audit rows so the venue
        # isn't charged for gigs that haven't happened yet. Past +
        # in-flight-payout gigs stay on the normal payout track — same
        # precise gig-start-time cutoff as the OFF path. Matches user
        # spec: "All gigs that haven't happened yet are affected by
        # the Trial Mode option."
        _convert_upcoming_scheduled_to_free_trial(db, venue_id)

    # Recalculate all pending transactions for this venue's gigs
    _recalculate_venue_pending_transactions(db, venue_id, suspend)

    try:
        from backend.utils import log_admin_action
        # Override rows only exist when suspended (DELETE on un-suspend),
        # so before-state is just "did a row exist?"
        log_admin_action(
            db, admin,
            "toggle_venue_payment_override",
            target_table="venue_payment_overrides",
            target_id=venue_id,
            before={"payments_suspended": bool(existing)},
            after={"payments_suspended": bool(suspend)},
            metadata={"venue_id": venue_id, "venue_name": venue[1], "notes": notes},
            request=request,
        )
    except Exception:
        pass

    return {"ok": True, "payments_suspended": suspend, "venue_name": venue[1]}


def _convert_upcoming_free_trial_bookings(db, venue_id):
    """Toggle-off helper: convert free-trial audit rows on FUTURE gigs into
    real venue_charge / artist_payout transactions, so the venue starts getting
    billed for bookings that were created while on free trial.

    Strategy:
      1. Compute now-in-venue-local-tz.
      2. Find every (gig_id, artist_id) pair with a 'free_trial' transaction
         row where the gig HAS NOT STARTED YET (combined date + earliest-slot
         start_time in venue-local tz is strictly in the future).
      3. Resolve the artist's slot (for multi-slot) and the effective pay.
      4. Delete the audit row.
      5. Call _create_booking_transaction(...) — the override row was already
         deleted by the caller, so this takes the normal billing path and
         creates a proper venue_charge + artist_payout with the right
         scheduled_process_at (5pm venue-local on day-after-gig).

    Gigs that have ALREADY STARTED (even by a minute) stay on trial: they
    were booked and (partially) performed under the trial promise, so
    retroactively charging the venue for them would break that promise.
    Per user spec 2026-08-21: "a gig that just happened and the payment
    process is happening (like before 5pm the day after the gig happened),
    that gig should continue in Trial mode."

    Past free-trial rows are left intact — they're audit history and the venue
    has presumably already paid the artist directly outside the platform.
    """
    from datetime import datetime, time as _dt_time
    from backend.utils import get_venue_timezone
    from backend.routes.gigs import _create_booking_transaction
    log = logging.getLogger("gigsfill.admin")
    try:
        venue_tz = get_venue_timezone(db, venue_id)
        now_local = datetime.now(venue_tz).replace(tzinfo=None)
    except Exception as _tze:
        log.warning(f"free-trial convert: tz lookup failed for venue {venue_id}: {_tze}")
        # Fall back to naive UTC now — slight risk of tz-edge miscount near
        # boundaries, but better than failing the whole conversion.
        now_local = datetime.utcnow()

    # Pull every free-trial row for this venue on or after today (venue-local
    # date); apply the precise start-time comparison in Python because SQLite
    # can't easily combine date + time into a comparable datetime.
    rows = db.execute(text("""
        SELECT t.id as txn_id, t.gig_id, t.artist_id, t.amount_cents,
               g.date as gig_date, g.start_time as gig_start_time,
               (SELECT MIN(gs.start_time) FROM gig_slots gs
                 WHERE gs.gig_id = g.id AND gs.artist_id = t.artist_id
                   AND gs.status IN ('booked','pending_contract','pending_venue_approval')
               ) AS slot_start_time
        FROM transactions t
        JOIN gigs g ON g.id = t.gig_id
        WHERE g.venue_id = :vid
          AND t.transaction_type = 'free_trial'
          AND t.status = 'free_trial'
          AND date(g.date) >= date(:today)
    """), {"vid": venue_id, "today": now_local.date().isoformat()}).mappings().all()

    def _parse_gig_start(gig_date_val, start_time_val):
        """Combine gig date + start_time into a naive datetime (venue-local
        clock). Returns None if either piece is missing / unparseable so the
        caller can fall back to end-of-day (keeping the gig on trial rather
        than accidentally cutting over)."""
        try:
            _d_str = str(gig_date_val or "")[:10]
            if not _d_str:
                return None
            _t_raw = str(start_time_val or "").strip()
            if not _t_raw:
                return None
            # start_time in this DB is stored as HH:MM (24h) or HH:MM:SS
            _parts = _t_raw.split(":")
            _h = int(_parts[0]) if _parts and _parts[0].isdigit() else None
            _m = int(_parts[1]) if len(_parts) > 1 and _parts[1].isdigit() else 0
            if _h is None or not (0 <= _h <= 23) or not (0 <= _m <= 59):
                return None
            _y, _mo, _da = _d_str.split("-")
            return datetime(int(_y), int(_mo), int(_da), _h, _m)
        except Exception:
            return None

    converted = 0
    skipped_started = 0
    for r in rows:
        gig_id = r["gig_id"]
        artist_id = r["artist_id"]
        if not artist_id:
            continue

        # Precise cutover: convert ONLY if the gig's start time (venue-local)
        # is strictly in the future. Prefer the slot's start_time (multi-slot
        # correctness); fall back to the gig-level start_time; if both are
        # missing / unparseable, default to keeping the row on trial.
        _gig_start_dt = _parse_gig_start(
            r["gig_date"],
            r.get("slot_start_time") or r.get("gig_start_time")
        )
        if _gig_start_dt is None or _gig_start_dt <= now_local:
            skipped_started += 1
            continue

        # Resolve slot_id (NULL for single-slot gigs)
        slot_row = db.execute(text("""
            SELECT id, pay FROM gig_slots
            WHERE gig_id = :gid AND artist_id = :aid
              AND status IN ('booked','pending_contract','pending_venue_approval')
            ORDER BY slot_number LIMIT 1
        """), {"gid": gig_id, "aid": artist_id}).mappings().first()
        slot_id = slot_row["id"] if slot_row else None
        # Effective pay: prefer the slot's pay; fall back to the audit row's
        # amount_cents (which captured pay-at-booking-time, surviving any
        # later edits we didn't track).
        if slot_row and slot_row["pay"] is not None:
            pay_amount = float(slot_row["pay"])
        else:
            pay_amount = float(r["amount_cents"] or 0) / 100.0

        # Drop the audit row before recreating — otherwise the gig would have
        # both a free_trial row AND a venue_charge row for the same booking.
        db.execute(text("DELETE FROM transactions WHERE id = :tid"), {"tid": r["txn_id"]})

        try:
            _create_booking_transaction(
                db, gig_id, venue_id, artist_id,
                pay_amount, r["gig_date"], slot_id=slot_id
            )
            converted += 1
        except Exception as _cbte:
            log.error(
                f"free-trial convert FAILED gig {gig_id} artist {artist_id}: {_cbte}"
            )
            # Re-insert a marker so we don't silently lose the row. Best-effort.
            try:
                db.execute(text("""
                    INSERT INTO transactions
                        (gig_id, artist_id, amount_cents, status, transaction_type,
                         payment_method_type, created_at, notes)
                    VALUES (:gid, :aid, :amt, 'free_trial', 'free_trial',
                            'free_trial', :now, 'Restore failed — see logs')
                """), {"gid": gig_id, "aid": artist_id, "amt": r["amount_cents"],
                       "now": utcnow_naive()})
            except Exception:
                pass

    db.commit()
    log.info(
        f"Free trial OFF for venue {venue_id}: converted {converted} future "
        f"booking(s), skipped {skipped_started} already-started gig(s) "
        f"(examined {len(rows)} free-trial audit rows on/after "
        f"{now_local.date().isoformat()} venue-local)"
    )


def _convert_upcoming_scheduled_to_free_trial(db, venue_id):
    """Toggle-ON symmetric helper (2026-08-24): when admin flips a venue
    INTO free trial, convert any existing `scheduled` transactions for
    FUTURE gigs at that venue into free_trial audit rows. Symmetric with
    _convert_upcoming_free_trial_bookings.

    Boundary matches toggle-off: gig start (venue-local) must be strictly
    in the future. Gigs already begun (even by a minute) stay on the
    normal payout track — the artist was booked under the normal-billing
    promise for that show and shouldn't have their payout unilaterally
    swapped out mid-performance / mid-payout-window.

    Also flips any `pending_transfer` / `charge_retry` rows for future
    gigs since those are ancillary states of the same billing pipeline
    (a payout that was about to retry shouldn't fire after trial flips ON).
    """
    from datetime import datetime
    from backend.utils import get_venue_timezone
    log = logging.getLogger("gigsfill.admin")
    try:
        venue_tz = get_venue_timezone(db, venue_id)
        now_local = datetime.now(venue_tz).replace(tzinfo=None)
    except Exception as _tze:
        log.warning(f"free-trial ON convert: tz lookup failed for venue {venue_id}: {_tze}")
        now_local = datetime.utcnow()

    rows = db.execute(text("""
        SELECT t.id as txn_id, t.gig_id, t.artist_id, t.amount_cents,
               t.transaction_type, t.status,
               g.date as gig_date, g.start_time as gig_start_time,
               (SELECT MIN(gs.start_time) FROM gig_slots gs
                 WHERE gs.gig_id = g.id AND gs.artist_id = t.artist_id
                   AND gs.status IN ('booked','pending_contract','pending_venue_approval')
               ) AS slot_start_time
        FROM transactions t
        JOIN gigs g ON g.id = t.gig_id
        WHERE g.venue_id = :vid
          AND t.status IN ('scheduled', 'pending_transfer', 'charge_retry')
          AND date(g.date) >= date(:today)
    """), {"vid": venue_id, "today": now_local.date().isoformat()}).mappings().all()

    def _parse_gig_start(gig_date_val, start_time_val):
        try:
            _d_str = str(gig_date_val or "")[:10]
            _t_raw = str(start_time_val or "").strip()
            if not _d_str or not _t_raw:
                return None
            _parts = _t_raw.split(":")
            _h = int(_parts[0]) if _parts and _parts[0].isdigit() else None
            _m = int(_parts[1]) if len(_parts) > 1 and _parts[1].isdigit() else 0
            if _h is None or not (0 <= _h <= 23) or not (0 <= _m <= 59):
                return None
            _y, _mo, _da = _d_str.split("-")
            return datetime(int(_y), int(_mo), int(_da), _h, _m)
        except Exception:
            return None

    # Group txn rows by (gig_id, artist_id) so we handle parent + child pair
    # atomically. The venue_charge parent gets DELETEd, the artist_payout
    # child gets rewritten to a free_trial audit row.
    grouped = {}
    for r in rows:
        key = (r["gig_id"], r["artist_id"])
        grouped.setdefault(key, []).append(r)

    converted = 0
    skipped_started = 0
    for (gig_id, artist_id), txn_rows in grouped.items():
        # Precise cutover — use slot start if available, else gig-level.
        _first = txn_rows[0]
        _gig_start_dt = _parse_gig_start(
            _first["gig_date"],
            _first.get("slot_start_time") or _first.get("gig_start_time")
        )
        if _gig_start_dt is None or _gig_start_dt <= now_local:
            skipped_started += 1
            continue

        # Amount for the audit row: prefer the artist_payout child's
        # amount_cents (equals the artist's pay). Fall back to whatever
        # first row has.
        _amt = 0
        for tr in txn_rows:
            if tr.get("transaction_type") in ("artist_payout", "single"):
                _amt = int(tr.get("amount_cents") or 0)
                break
        if not _amt:
            _amt = int(_first.get("amount_cents") or 0)

        # Wipe ALL existing transaction rows for this (gig, artist) —
        # parent venue_charge + artist_payout child(ren). Same as if the
        # booking had happened under trial from the start.
        try:
            db.execute(
                text("DELETE FROM transactions WHERE gig_id = :gid AND artist_id = :aid AND status IN ('scheduled','pending_transfer','charge_retry')"),
                {"gid": gig_id, "aid": artist_id}
            )
            # Also delete the venue_charge parent (has artist_id=NULL)
            # if this was the last payout for the parent.
            _remaining = db.execute(text("""
                SELECT COUNT(*) FROM transactions
                WHERE gig_id = :gid AND transaction_type = 'artist_payout' AND status = 'scheduled'
            """), {"gid": gig_id}).scalar() or 0
            if _remaining == 0:
                db.execute(text("""
                    DELETE FROM transactions
                    WHERE gig_id = :gid AND transaction_type = 'venue_charge'
                      AND status IN ('scheduled','pending_transfer','charge_retry')
                """), {"gid": gig_id})

            # Insert the free_trial audit row (same shape as
            # _create_booking_transaction's trial branch).
            _venue_user = db.execute(text("SELECT user_id FROM venues WHERE id = :vid"),
                                     {"vid": venue_id}).mappings().first()
            _artist_user = db.execute(text("SELECT user_id FROM artists WHERE id = :aid"),
                                      {"aid": artist_id}).mappings().first()
            if _venue_user and _artist_user:
                db.execute(text("""
                    INSERT INTO transactions
                        (gig_id, from_user_id, to_user_id, artist_id,
                         amount_cents, venue_charge_cents, artist_payout_cents, commission_cents,
                         credit_card_fee_cents, payment_method_type, status,
                         created_at, notes, transaction_type)
                    VALUES
                        (:gig_id, :from_uid, :to_uid, :artist_id,
                         :amount, 0, :amount, 0,
                         0, 'free_trial', 'free_trial',
                         :now, :notes, 'free_trial')
                """), {
                    "gig_id": gig_id,
                    "from_uid": _venue_user["user_id"],
                    "to_uid": _artist_user["user_id"],
                    "artist_id": artist_id,
                    "amount": _amt,
                    "now": utcnow_naive(),
                    "notes": f"Converted from scheduled — venue {venue_id} flipped to Free Trial (venue pays artist directly)",
                })
            converted += 1
        except Exception as _cve:
            log.error(f"free-trial ON convert FAILED gig {gig_id} artist {artist_id}: {_cve}")

    db.commit()
    log.info(
        f"Free trial ON for venue {venue_id}: converted {converted} future "
        f"booking(s) to free_trial audit rows, skipped {skipped_started} "
        f"already-started gig(s)"
    )


def _recalculate_venue_pending_transactions(db, venue_id, is_free_trial):
    """When free trial is toggled, recompute pending transactions for this venue.

    Audit fix (May 2026): the previous implementation was wrong on TWO axes.
      1. It iterated EVERY transaction row including artist_payout children
         and rewrote `venue_charge_cents` on them — meaningless on a child
         (children have venue_charge_cents=0 by design under the new fee model).
      2. It used the legacy per-slot fee math that the May 7 changelog fixed
         in `_create_booking_transaction`. Multi-slot gigs got per-slot
         min-fees applied independently, double-charging.
    Now: collect distinct gig_ids that have a scheduled/test parent
    venue_charge for this venue, then call the canonical _recompute_gig_fees
    on each. The canonical recompute is the single source of truth — it
    knows the gig-level + proportional split model and skips children
    automatically. is_free_trial is intentionally unused: free-trial venues
    have their venue_charge child rows skipped at booking time entirely
    (see _create_booking_transaction free-trial early-return), so toggling
    OFF doesn't need to "add fees back" — there are no pending charge rows.
    Toggling ON suspends rows; this helper just normalizes whatever's left.
    """
    try:
        from backend.routes.gigs import _recompute_gig_fees
        gig_ids = db.execute(text("""
            SELECT DISTINCT t.gig_id
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            WHERE g.venue_id = :vid
              AND t.transaction_type = 'venue_charge'
              AND t.status = 'scheduled'
        """), {"vid": venue_id}).fetchall()
        for row in gig_ids:
            try:
                _recompute_gig_fees(db, row[0])
            except Exception as _ge:
                logging.getLogger("gigsfill.admin").warning(
                    f"recompute skipped for gig {row[0]}: {_ge}"
                )
        db.commit()
        logging.getLogger("gigsfill.admin").info(
            f"Recomputed fees on {len(gig_ids)} gig(s) for venue {venue_id} (free_trial={is_free_trial})"
        )
    except Exception as e:
        logging.getLogger("gigsfill.admin").warning(f"Error recalculating transactions for venue {venue_id}: {e}")

@router.delete("/api/admin/venue-payment-overrides/{venue_id}")
def remove_venue_payment_override(venue_id: int, request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Remove payment override for a venue (re-enable payments).

    Audit fix (May 2026 part 3): writes an admin_audit_log row so this
    destructive path (re-enables payments + re-adds venue fees to
    pending transactions) leaves a trail. Toggle endpoint already logged;
    this DELETE was missed.
    """
    before = db.execute(
        text("SELECT * FROM venue_payment_overrides WHERE venue_id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    db.execute(
        text("DELETE FROM venue_payment_overrides WHERE venue_id = :vid"),
        {"vid": venue_id}
    )
    db.commit()
    # Re-enable means free trial OFF — recalculate to add venue fee back
    _recalculate_venue_pending_transactions(db, venue_id, False)
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "remove_venue_payment_override",
            target_table="venue_payment_overrides", target_id=venue_id,
            before=(dict(before) if before else None), after=None,
            request=request,
        )
    except Exception:
        pass
    return {"ok": True}


# ============================================
# SUPPORT TICKETS
# ============================================

def _ensure_support_replies_table(db):
    """Create support_ticket_replies table if it doesn't exist"""
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS support_ticket_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                sender_type TEXT NOT NULL DEFAULT 'admin',
                sender_name TEXT,
                sender_email TEXT,
                body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES support_tickets(id)
            )
        """))
        db.commit()
    except Exception:
        pass

@router.get("/api/admin/support-tickets")
def get_support_tickets(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all support tickets with reply counts"""
    _ensure_support_replies_table(db)
    
    rows = db.execute(text("""
        SELECT t.id, t.user_id, t.user_email, t.user_name, t.category, t.subject, t.description, t.status, t.created_at,
               (SELECT COUNT(*) FROM support_ticket_replies r WHERE r.ticket_id = t.id) as reply_count
        FROM support_tickets t
        ORDER BY t.created_at DESC
    """)).fetchall()
    
    tickets = []
    for row in rows:
        tickets.append({
            'id': row[0],
            'user_id': row[1],
            'user_email': row[2] or '',
            'user_name': row[3] or '',
            'category': row[4] or '',
            'subject': row[5] or '',
            'description': row[6] or '',
            'status': row[7] or 'open',
            'created_at': row[8] if row[8] else None,
            'reply_count': row[9] or 0
        })
    
    return tickets


@router.put("/api/admin/support-tickets/{ticket_id}")
async def update_support_ticket(ticket_id: int, request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Update a support ticket status.

    Audit fix (May 2026 part 3): admin status changes are now audited.
    """
    data = await request.json()
    status = data.get("status", "open")

    # Audit fix (May 2026 part 7): validate status against the allowed enum.
    # Previously the body was written raw — admin could set arbitrary strings
    # ("foo") that downstream filters silently mistreated as open or skipped.
    _ALLOWED_TICKET_STATUSES = ('open', 'pending', 'closed')
    if status not in _ALLOWED_TICKET_STATUSES:
        raise HTTPException(400, f"status must be one of: {', '.join(_ALLOWED_TICKET_STATUSES)}")

    before = db.execute(
        text("SELECT status FROM support_tickets WHERE id = :tid"),
        {"tid": ticket_id}
    ).mappings().first()

    db.execute(
        text("UPDATE support_tickets SET status = :status WHERE id = :tid"),
        {"status": status, "tid": ticket_id}
    )
    db.commit()
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "update_support_ticket",
            target_table="support_tickets", target_id=ticket_id,
            before=(dict(before) if before else None),
            after={"status": status},
            request=request,
        )
    except Exception:
        pass
    return {"ok": True}


@router.delete("/api/admin/support-tickets/{ticket_id}")
def admin_delete_support_ticket(ticket_id: int, request: Request,
                                admin=Depends(check_admin), db=Depends(get_db)):
    """Hard delete a support ticket + all its replies. For spam / test rows
    that shouldn't clutter the queue — normal resolved tickets should be
    marked 'closed' via PUT so the audit trail stays intact."""
    _ensure_support_replies_table(db)
    row = db.execute(
        text("SELECT id, subject, user_email, status FROM support_tickets WHERE id = :tid"),
        {"tid": ticket_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Ticket not found")
    row = dict(row)
    db.execute(text("DELETE FROM support_ticket_replies WHERE ticket_id = :tid"), {"tid": ticket_id})
    db.execute(text("DELETE FROM support_tickets WHERE id = :tid"), {"tid": ticket_id})
    db.commit()
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "delete_support_ticket",
            target_table="support_tickets", target_id=ticket_id,
            before=row,
            request=request,
        )
    except Exception:
        pass
    return {"ok": True}


@router.get("/api/admin/support-tickets/{ticket_id}/replies")
def get_ticket_replies(ticket_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    """Get all replies for a support ticket"""
    _ensure_support_replies_table(db)
    
    rows = db.execute(text("""
        SELECT id, ticket_id, sender_type, sender_name, sender_email, body, created_at
        FROM support_ticket_replies
        WHERE ticket_id = :tid
        ORDER BY created_at ASC
    """), {"tid": ticket_id}).fetchall()
    
    replies = []
    for row in rows:
        replies.append({
            'id': row[0],
            'ticket_id': row[1],
            'sender_type': row[2] or 'admin',
            'sender_name': row[3] or '',
            'sender_email': row[4] or '',
            'body': row[5] or '',
            'created_at': row[6] if row[6] else None
        })
    
    return replies


@router.post("/api/admin/support-tickets/{ticket_id}/reply")
async def reply_to_ticket(ticket_id: int, request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Send a reply to a support ticket — stores in DB and sends email"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from datetime import datetime
    
    _ensure_support_replies_table(db)
    
    data = await request.json()
    body = (data.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "Reply body is required")
    
    # Get ticket info
    ticket = db.execute(text("""
        SELECT id, user_email, user_name, subject, category, description
        FROM support_tickets WHERE id = :tid
    """), {"tid": ticket_id}).fetchone()
    
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    
    user_email = ticket[1] or ''
    user_name = ticket[2] or ''
    ticket_subject = ticket[3] or ''
    ticket_category = ticket[4] or ''
    original_description = ticket[5] or ''
    
    # Get display name for support replies (from settings, or fallback to support email, then admin name)
    display_name_row = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'support_display_name'"
    )).scalar()
    support_email_row = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'support_email'"
    )).scalar()
    admin_row = db.execute(text(
        "SELECT first_name, last_name, email FROM users WHERE id = :uid"
    ), {"uid": admin.id}).fetchone()
    admin_email_addr = admin_row[2] if admin_row else ''
    
    if display_name_row and display_name_row.strip():
        admin_name = display_name_row.strip()
    elif support_email_row and support_email_row.strip():
        admin_name = support_email_row.strip()
    elif admin_row:
        admin_name = f"{admin_row[0] or ''} {admin_row[1] or ''}".strip() or "GigsFill Support"
    else:
        admin_name = "GigsFill Support"
    
    # Get previous replies for the email thread
    prev_replies = db.execute(text("""
        SELECT sender_type, sender_name, body, created_at
        FROM support_ticket_replies
        WHERE ticket_id = :tid
        ORDER BY created_at ASC
    """), {"tid": ticket_id}).fetchall()
    
    # Store reply in DB
    db.execute(text("""
        INSERT INTO support_ticket_replies (ticket_id, sender_type, sender_name, sender_email, body)
        VALUES (:tid, 'admin', :name, :email, :body)
    """), {"tid": ticket_id, "name": admin_name, "email": admin_email_addr, "body": body})
    
    # Auto-reopen if closed, or keep open
    db.execute(text(
        "UPDATE support_tickets SET status = 'open' WHERE id = :tid AND status = 'closed'"
    ), {"tid": ticket_id})
    
    db.commit()
    
    # Send email to user
    email_sent = False
    if user_email:
        try:
            # Get SMTP settings
            smtp_rows = db.execute(text(
                "SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN "
                "('platform_email', 'platform_email_password', 'platform_smtp_server', 'platform_smtp_port', 'support_email', 'support_email_from_name')"
            )).fetchall()
            settings = {r[0]: r[1] for r in smtp_rows}
            
            smtp_email = settings.get('platform_email', '')
            smtp_password = settings.get('platform_email_password', '')
            smtp_server = settings.get('platform_smtp_server', 'smtp.gmail.com')
            smtp_port = int(settings.get('platform_smtp_port', '587'))
            from_email = settings.get('support_email', smtp_email)
            support_from_name = settings.get('support_email_from_name', '')
            
            if smtp_email and smtp_password:
                # Generate access token for user reply link
                import hmac, hashlib
                from backend.routes.auth import _SECRET_KEY
                token_msg = f"support-{ticket_id}-{(user_email or '').lower().strip()}"
                ticket_token = hmac.new(_SECRET_KEY.encode(), token_msg.encode(), hashlib.sha256).hexdigest()[:32]
                # Audit fix (May 2026 part 5): pull from platform_settings.site_url
                # so staging / custom-domain deploys email users a link that points
                # back at the same deploy, not production.
                try:
                    _su = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key='site_url'")).scalar()
                    if not _su:
                        _su = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key='base_url'")).scalar()
                    _base = (_su or "https://gigsfill.com").rstrip("/")
                    if "127.0.0.1" in _base or "localhost" in _base:
                        _base = "https://gigsfill.com"
                except Exception:
                    _base = "https://gigsfill.com"
                reply_url = f"{_base}/app/support-ticket.html?id={ticket_id}&token={ticket_token}"
                
                # Build thread HTML for email
                thread_html = ""
                for r in prev_replies:
                    r_type = r[0]
                    r_name = r[1] or ('Support' if r_type == 'admin' else user_name)
                    r_body = (r[2] or '').replace('\n', '<br>')
                    r_date = r[3] or ''
                    r_color = '#e0f2fe' if r_type == 'admin' else '#f3f4f6'
                    r_label = 'Support' if r_type == 'admin' else user_name
                    thread_html += f"""
                    <div style="background:{r_color};border-radius:6px;padding:12px 16px;margin-bottom:8px;">
                      <div style="font-size:11px;color:#6b7280;margin-bottom:4px;"><strong>{r_label}</strong> &middot; {r_date}</div>
                      <div style="font-size:13px;color:#374151;line-height:1.5;">{r_body}</div>
                    </div>"""
                
                # Build template variables
                reply_vars = {
                    'ticket_id': str(ticket_id),
                    'ticket_subject': ticket_subject,
                    'user_name': user_name or 'there',
                    'admin_name': admin_name,
                    'reply_body': body.replace(chr(10), '<br>'),
                    'previous_thread': thread_html,
                    'reply_url': reply_url,
                    'category': ticket_category,
                    'description': original_description.replace(chr(10), '<br>'),
                }

                # Use EmailService to send via template
                from sqlalchemy import create_engine
                from sqlalchemy.orm import sessionmaker
                from backend.db import DATABASE_URL
                from backend.email_service import EmailService as _ES
                _engine = create_engine(DATABASE_URL)
                _Session = sessionmaker(bind=_engine)
                _db = _Session()
                try:
                    _es = _ES(_db)
                    user_id_row = db.execute(text("SELECT id FROM users WHERE email = :em"), {"em": user_email}).first()
                    if user_id_row:
                        result = _es.send_notification_email(user_email, user_id_row[0], 'support_ticket_reply', reply_vars)
                    else:
                        html = _es._render_template_key('support_ticket_reply', reply_vars)
                        result = _es._send_raw(user_email, f"Re: [GigsFill Support #{ticket_id}] {ticket_subject}", html)
                    email_sent = bool(result)
                finally:
                    _db.close()
        except Exception as e:
            logging.getLogger("gigsfill.admin").error(f"Support reply email failed: {e}")

    # Audit fix (May 2026 part 3): log the admin reply. Replies email
    # the user; admin doing so without a trail was the gap.
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "support_ticket_reply",
            target_table="support_tickets", target_id=ticket_id,
            before=None,
            after={"reply_length": len(body), "email_sent": email_sent},
            request=request,
        )
    except Exception:
        pass

    return {"ok": True, "email_sent": email_sent}


# ============================================
# LAST LOGIN MIGRATION
# ============================================

def ensure_last_login_column():
    """Add last_login column to users table if it doesn't exist"""
    import sqlite3, os
    db_path = os.environ.get("DATABASE_PATH", "backend.db")
    try:
        from backend.db import get_db_connection as _admin_raw_conn2
        conn = _admin_raw_conn2()
        cursor = conn.cursor()
        if _IS_POSTGRES:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='users'"
            )
            columns = [row[0] for row in cursor.fetchall()]
        else:
            cursor.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in cursor.fetchall()]
        if 'last_login' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")
            conn.commit()
            logging.getLogger("gigsfill.admin").info("Added last_login column to users table")
        conn.close()
    except Exception as e:
        logging.getLogger("gigsfill.admin").warning(f"last_login migration: {e}")

# To add the last_login column, run on the server:
#   sqlite3 backend.db "ALTER TABLE users ADD COLUMN last_login TIMESTAMP"


# ============================================
# ACCOUNTING — Full Money Trail
# ============================================

@router.get("/api/admin/accounting")
def get_accounting(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all transactions with full money trail for admin accounting view"""
    # Note: For 'venue_charge' parents, artist_payout_cents is stored as 0 on
    # the parent row — the actual artist payout amount lives on the child
    # 'artist_payout' rows. We sum the children's amounts via a subquery so the
    # accounting view shows the real artist payout for each gig (May 2026 fix).
    # For legacy 'single' transactions, the value is on the row itself.
    rows = db.execute(
        text("""
            SELECT t.id as txn_id, t.gig_id, t.status,
                   t.amount_cents, t.venue_charge_cents,
                   CASE
                     WHEN COALESCE(t.transaction_type, 'single') = 'venue_charge' THEN
                       COALESCE(
                         (SELECT SUM(c.artist_payout_cents) FROM transactions c
                          WHERE c.parent_transaction_id = t.id
                            AND c.transaction_type = 'artist_payout'
                            AND c.status NOT IN ('payment_cancelled','account_deleted')),
                         0
                       )
                     ELSE COALESCE(t.artist_payout_cents, 0)
                   END as artist_payout_cents,
                   t.commission_cents, t.credit_card_fee_cents,
                   t.platform_fee_charged_cents,
                   t.stripe_payment_intent_id, t.stripe_transfer_id,
                   t.cancel_reason, t.cancelled_at, t.processed_at,
                   t.created_at, t.notes, t.charge_attempts,
                   g.date as gig_date, g.start_time, g.end_time, g.title as gig_title,
                   v.venue_name, v.id as venue_id,
                   COALESCE(
                     a.name,
                     a2.name,
                     (SELECT a3.name FROM artists a3 WHERE a3.user_id = t.to_user_id LIMIT 1),
                     -- For venue_charge parents: child artist_payout rows store the actual artist_id.
                     -- Comma-join names if multi-slot gig has multiple artists. (May 2026 fix.)
                     (SELECT GROUP_CONCAT(a4.name, ', ')
                        FROM transactions c
                        JOIN artists a4 ON a4.id = c.artist_id
                        WHERE c.parent_transaction_id = t.id
                          AND c.transaction_type = 'artist_payout'
                          AND c.status NOT IN ('payment_cancelled','account_deleted'))
                   ) as artist_name,
                   COALESCE(
                     a.id,
                     a2.id,
                     (SELECT a3.id FROM artists a3 WHERE a3.user_id = t.to_user_id LIMIT 1),
                     (SELECT c.artist_id
                        FROM transactions c
                        WHERE c.parent_transaction_id = t.id
                          AND c.transaction_type = 'artist_payout'
                          AND c.status NOT IN ('payment_cancelled','account_deleted')
                        LIMIT 1)
                   ) as artist_id
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON a.id = t.artist_id
            LEFT JOIN artists a2 ON a2.id = g.artist_id
            WHERE COALESCE(t.transaction_type, 'single') IN ('venue_charge', 'single')
            ORDER BY g.date DESC, g.start_time DESC
        """)
    ).mappings().all()

    # Get platform fee settings for calculating splits
    settings = {}
    for r in db.execute(text("""
        SELECT setting_key, setting_value FROM platform_settings
        WHERE setting_key IN ('platform_fee_percent', 'platform_fee_split', 'platform_min_fee', 'stripe_processing_fee_percent', 'stripe_per_transaction_fee')
    """)).fetchall():
        settings[r[0]] = r[1]

    fee_split = settings.get("platform_fee_split", "split")
    stripe_pct = float(settings.get("stripe_processing_fee_percent", "2.9")) / 100
    stripe_flat = int(float(settings.get("stripe_per_transaction_fee", "0.30")) * 100)

    result = []
    for r in rows:
        r = dict(r)
        commission = r.get("commission_cents") or 0
        amount = r.get("amount_cents") or 0
        venue_charge = r.get("venue_charge_cents") or 0
        artist_payout = r.get("artist_payout_cents") or 0
        platform_fee_on_cancel = r.get("platform_fee_charged_cents") or 0
        status = r.get("status") or ""

        # Calculate fee split
        if fee_split == "venue_only":
            venue_fee = commission
            artist_fee = 0
        elif fee_split == "artist_only":
            venue_fee = 0
            artist_fee = commission
        else:
            venue_fee = commission // 2
            artist_fee = commission - venue_fee

        # Stripe processing fee:
        #   1. PREFER the real fee captured from balance_transaction at charge time
        #      (stored in credit_card_fee_cents). This is what Stripe actually billed,
        #      to the cent — matches the dashboard exactly.
        #   2. FALLBACK to 2.9% + $0.30 estimate when the real fee wasn't captured
        #      (legacy rows charged before May 2026, or balance_transaction fetch
        #      failed at charge time).
        # Three cancellation cases use the same preference:
        #   (a) Cancel fee was charged → fee on that small charge
        #   (b) Original charge fired and was later refunded ("phantom") → Stripe
        #       doesn't refund processing fees, so we still ate the fee
        #   (c) Cancelled before any charge fired → no stripe fee
        stripe_pi_id = r.get("stripe_payment_intent_id") or ""
        if status == "payment_cancelled":
            if platform_fee_on_cancel > 0:
                actual_charge = platform_fee_on_cancel  # case (a)
            elif stripe_pi_id and venue_charge > 0:
                actual_charge = venue_charge  # case (b) — phantom: original charge fired, refunded
            else:
                actual_charge = 0  # case (c)
        elif status in ("paid", "charged", "transfer_failed", "pending_transfer"):
            actual_charge = venue_charge
        else:
            actual_charge = 0

        real_fee = r.get("credit_card_fee_cents") or 0
        if real_fee > 0:
            stripe_fee = real_fee
        else:
            stripe_fee = int(actual_charge * stripe_pct + stripe_flat) if actual_charge > 0 else 0

        # GigsFill net profit = platform revenue - stripe fees
        # For cancelled with cancel-fee: profit = cancel fee - stripe fee on it
        # For cancelled phantom: profit = -stripe fee (negative — we ate the cost)
        # For cancelled with no charges: profit = 0
        if status == "payment_cancelled":
            gigsfill_profit = platform_fee_on_cancel - stripe_fee
        elif status in ("paid", "charged", "transfer_failed", "pending_transfer"):
            gigsfill_profit = commission - stripe_fee
        else:
            gigsfill_profit = 0

        result.append({
            "txn_id": r["txn_id"],
            "gig_id": r["gig_id"],
            "gig_date": r["gig_date"],
            "start_time": r.get("start_time") or "",
            "end_time": r.get("end_time") or "",
            "gig_title": r.get("gig_title") or "",
            "venue_name": r.get("venue_name") or "",
            "venue_id": r.get("venue_id"),
            "artist_name": r.get("artist_name") or "",
            "artist_id": r.get("artist_id"),
            "status": status,
            "gig_fee_cents": amount,
            "venue_charge_cents": venue_charge,
            "venue_fee_cents": venue_fee,
            "artist_fee_cents": artist_fee,
            "artist_payout_cents": artist_payout,
            "commission_cents": commission,
            "stripe_fee_cents": stripe_fee,
            "platform_fee_on_cancel_cents": platform_fee_on_cancel,
            "gigsfill_profit_cents": gigsfill_profit,
            "stripe_pi_id": r.get("stripe_payment_intent_id") or "",
            "stripe_transfer_id": r.get("stripe_transfer_id") or "",
            "cancel_reason": r.get("cancel_reason") or "",
            "processed_at": r.get("processed_at") or "",
            "created_at": r.get("created_at") or "",
        })

    return result


import json as _json

# ─── ADMIN FLYER TEMPLATE ENDPOINTS ───────────────────────────────────────────

@router.get("/api/admin/flyers/templates")
def list_admin_templates(admin=Depends(check_admin), db=Depends(get_db)):
    """List all admin-level flyer templates (venue_id IS NULL, is_template=1)."""
    rows = db.execute(text("""
        SELECT id, name, thumbnail_data, size_preset, width, height, updated_at
        FROM flyers
        WHERE venue_id IS NULL AND is_template = 1
        ORDER BY
            CASE WHEN LOWER(name) = 'default template' THEN 0 ELSE 1 END,
            updated_at DESC
    """)).fetchall()
    return [dict(r._mapping) for r in rows]

@router.get("/api/admin/flyers/templates/{tpl_id}")
def get_admin_template(tpl_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    row = db.execute(text(
        "SELECT * FROM flyers WHERE id = :id AND venue_id IS NULL AND is_template = 1"
    ), {"id": tpl_id}).fetchone()
    if not row:
        raise HTTPException(404, "Template not found")
    return dict(row._mapping)

@router.get("/api/admin/flyers/default-template")
def get_admin_default_template(admin=Depends(check_admin), db=Depends(get_db)):
    """Get the site-wide Default Template."""
    row = db.execute(text("""
        SELECT * FROM flyers
        WHERE venue_id IS NULL AND is_template = 1 AND LOWER(name) = 'default template'
        ORDER BY updated_at DESC LIMIT 1
    """)).fetchone()
    if not row:
        return {"canvas_data": "{}", "name": "Default Template", "id": None}
    return dict(row._mapping)

@router.put("/api/admin/flyers/default-template")
async def upsert_admin_default_template(request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Save/overwrite the site-wide 'Default Template' (venue_id IS NULL)."""
    body = await request.json()
    canvas_data = body.get("canvas_data", "{}")
    if isinstance(canvas_data, dict):
        canvas_data = _json.dumps(canvas_data)
    tpl_name = "Default Template"

    existing = db.execute(text("""
        SELECT id FROM flyers
        WHERE venue_id IS NULL AND is_template = 1 AND LOWER(name) = 'default template'
        ORDER BY updated_at DESC LIMIT 1
    """)).fetchone()

    # Audit fix (May 2026 part 3): log admin flyer-template mutations
    from backend.utils import log_admin_action
    if existing:
        db.execute(text("""
            UPDATE flyers SET canvas_data = :canvas, thumbnail_data = :thumb,
                size_preset = :preset, width = :w, height = :h,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :fid
        """), {"fid": existing[0], "canvas": canvas_data, "thumb": body.get("thumbnail_data", ""),
               "preset": body.get("size_preset", "instagram_post"),
               "w": body.get("width", 1080), "h": body.get("height", 1350)})
        db.commit()
        try:
            log_admin_action(
                db, admin, "update_admin_default_flyer_template",
                target_table="flyers", target_id=existing[0],
                before=None, after={"size_preset": body.get("size_preset")},
                request=request,
            )
        except Exception:
            pass
        return {"id": existing[0], "message": "Site default template updated"}
    else:
        result = db.execute(text("""
            INSERT INTO flyers (venue_id, gig_id, artist_id, name, canvas_data, thumbnail_data,
                                is_template, size_preset, width, height)
            VALUES (NULL, NULL, NULL, :name, :canvas, :thumb, 1, :preset, :w, :h)
        """), {"name": tpl_name, "canvas": canvas_data, "thumb": body.get("thumbnail_data", ""),
               "preset": body.get("size_preset", "instagram_post"),
               "w": body.get("width", 1080), "h": body.get("height", 1350)})
        db.commit()
        try:
            log_admin_action(
                db, admin, "create_admin_default_flyer_template",
                target_table="flyers", target_id=result.lastrowid,
                before=None, after={"name": tpl_name, "size_preset": body.get("size_preset")},
                request=request,
            )
        except Exception:
            pass
        return {"id": result.lastrowid, "message": "Site default template created"}

@router.post("/api/admin/flyers/templates")
async def create_admin_template(request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Save a new named admin template."""
    body = await request.json()
    canvas_data = body.get("canvas_data", "{}")
    if isinstance(canvas_data, dict):
        canvas_data = _json.dumps(canvas_data)
    result = db.execute(text("""
        INSERT INTO flyers (venue_id, gig_id, artist_id, name, canvas_data, thumbnail_data,
                            is_template, size_preset, width, height)
        VALUES (NULL, NULL, NULL, :name, :canvas, :thumb, 1, :preset, :w, :h)
    """), {"name": body.get("name", "Admin Template"), "canvas": canvas_data,
           "thumb": body.get("thumbnail_data", ""),
           "preset": body.get("size_preset", "instagram_post"),
           "w": body.get("width", 1080), "h": body.get("height", 1350)})
    db.commit()
    # Audit fix (May 2026 part 3)
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "create_admin_flyer_template",
            target_table="flyers", target_id=result.lastrowid,
            before=None,
            after={"name": body.get("name"), "size_preset": body.get("size_preset")},
            request=request,
        )
    except Exception:
        pass
    return {"id": result.lastrowid, "message": "Admin template created"}

@router.put("/api/admin/flyers/templates/{tpl_id}")
async def update_admin_template(tpl_id: int, request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    body = await request.json()
    canvas_data = body.get("canvas_data", "{}")
    if isinstance(canvas_data, dict):
        canvas_data = _json.dumps(canvas_data)
    existing = db.execute(text(
        "SELECT id FROM flyers WHERE id = :id AND venue_id IS NULL AND is_template = 1"
    ), {"id": tpl_id}).fetchone()
    if not existing:
        raise HTTPException(404, "Template not found")
    fields, params = ["updated_at = CURRENT_TIMESTAMP"], {"fid": tpl_id}
    for key, val in [("name", body.get("name")), ("canvas_data", canvas_data),
                     ("thumbnail_data", body.get("thumbnail_data")),
                     ("size_preset", body.get("size_preset")),
                     ("width", body.get("width")), ("height", body.get("height"))]:
        if val is not None:
            fields.append(f"{key} = :{key}")
            params[key] = val
    db.execute(text(f"UPDATE flyers SET {', '.join(fields)} WHERE id = :fid"), params)
    db.commit()
    # Audit fix (May 2026 part 3)
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "update_admin_flyer_template",
            target_table="flyers", target_id=tpl_id,
            before=None,
            after={k: v for k, v in params.items()
                   if k not in ("canvas_data", "thumbnail_data")},
            request=request,
        )
    except Exception:
        pass
    return {"message": "Template updated"}

@router.delete("/api/admin/flyers/templates/{tpl_id}")
def delete_admin_template(tpl_id: int, request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    existing = db.execute(text(
        "SELECT id, name FROM flyers WHERE id = :id AND venue_id IS NULL AND is_template = 1"
    ), {"id": tpl_id}).fetchone()
    if not existing:
        raise HTTPException(404, "Template not found")
    if existing[1].lower() == "default template":
        raise HTTPException(400, "Cannot delete the site-wide Default Template — overwrite it instead")
    db.execute(text("DELETE FROM flyers WHERE id = :id"), {"id": tpl_id})
    db.commit()
    # Audit fix (May 2026 part 3)
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "delete_admin_flyer_template",
            target_table="flyers", target_id=tpl_id,
            before={"name": existing[1]}, after=None,
            request=request,
        )
    except Exception:
        pass
    return {"message": "Template deleted"}


# ============================================================
# LOGS VIEWER
# ============================================================

import os
import glob
import io
import re

@router.get("/api/admin/logs")
def get_logs(
    level: str = "ALL",
    search: str = "",
    limit: int = 500,
    offset: int = 0,
    admin=Depends(check_admin),
    db=Depends(get_db)
):
    """
    Return recent log lines from the in-process logging system.
    Falls back to uvicorn/gunicorn log files if found.
    """
    lines = []

    # --- 1. Try to pull from our in-memory ring buffer (registered at startup) ---
    try:
        from backend.log_buffer import log_buffer
        lines = list(log_buffer.get_all())
    except Exception:
        lines = []

    # --- 2. If buffer is empty, scan log files on disk ---
    if not lines:
        log_paths = []
        # Common DigitalOcean / uvicorn log file paths
        candidates = [
            "/var/log/gigsfill/*.log",
            "/var/log/gigsfill.log",
            "/home/gigsfill/*.log",
            "gigsfill.log",
            "app.log",
        ]
        for pattern in candidates:
            log_paths.extend(glob.glob(pattern))

        for lp in log_paths[:3]:
            try:
                with open(lp, "r", errors="replace") as f:
                    for raw in f.readlines()[-2000:]:
                        lines.append(raw.rstrip())
            except Exception:
                pass

    # --- 3. Filter by level ---
    if level and level != "ALL":
        lv = level.upper()
        lines = [l for l in lines if lv in l.upper()]

    # --- 4. Filter by search ---
    if search:
        s = search.lower()
        lines = [l for l in lines if s in l.lower()]

    # --- 5. Most-recent first ---
    lines = list(reversed(lines))

    total = len(lines)
    page_lines = lines[offset: offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "lines": page_lines,
    }


@router.delete("/api/admin/logs/clear")
def clear_log_buffer(request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """Clear the in-memory log buffer.

    Audit fix (May 2026 part 5): also record an admin_action_log row so we
    have an immutable trail of who wiped the buffer (and from where) — this
    was previously a quiet endpoint with no audit at all, so a rogue admin
    could clear logs to cover their tracks."""
    try:
        from backend.log_buffer import log_buffer
        _before_size = len(getattr(log_buffer, "buf", []) or [])
        log_buffer.clear()
        try:
            from backend.utils import log_admin_action
            log_admin_action(
                db, admin, "clear_log_buffer",
                target_table="log_buffer", target_id=None,
                before={"buffer_size": _before_size},
                after={"buffer_size": 0},
                request=request
            )
        except Exception as _le:
            logger.warning(f"clear_log_buffer: audit-log write failed: {_le}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ============================================================
# DATABASE BROWSER — list tables
# ============================================================

@router.get("/api/admin/db/tables")
def list_tables(admin=Depends(check_admin), db=Depends(get_db)):
    """Return all table names and their row counts."""
    result = []
    for name in _list_tables(db):
        try:
            cnt = db.execute(text(f"SELECT COUNT(*) FROM \"{name}\"")).scalar()
        except Exception:
            cnt = 0
        result.append({"name": name, "rows": cnt})
    return result


@router.get("/api/admin/db/tables/{table}/schema")
def table_schema(table: str, admin=Depends(check_admin), db=Depends(get_db)):
    """Return column definitions for a table."""
    # Whitelist: only allow real table names that exist
    valid = set(_list_tables(db))
    if table not in valid:
        raise HTTPException(404, "Table not found")
    cols = _column_info(db, table)
    return [
        {"cid": c[0], "name": c[1], "type": c[2], "notnull": c[3], "pk": c[5]}
        for c in cols
    ]


@router.get("/api/admin/db/tables/{table}/rows")
def table_rows(
    table: str,
    page: int = 1,
    page_size: int = 50,
    sort_col: str = "",
    sort_dir: str = "asc",
    search: str = "",
    admin=Depends(check_admin),
    db=Depends(get_db)
):
    """Return paginated rows from any table with optional search and sort."""
    # Validate table name
    valid = set(_list_tables(db))
    if table not in valid:
        raise HTTPException(404, "Table not found")

    cols_raw = _column_info(db, table)
    col_names = [c[1] for c in cols_raw]

    # Validate sort column
    if sort_col and sort_col not in col_names:
        sort_col = ""
    sort_dir_safe = "DESC" if sort_dir.lower() == "desc" else "ASC"

    # Build WHERE clause for search (searches all TEXT/VARCHAR columns)
    where_parts = []
    params: dict = {}
    if search:
        text_cols = [c[1] for c in cols_raw if any(t in c[2].upper() for t in ("TEXT", "VARCHAR", "CHAR"))]
        for i, col in enumerate(text_cols[:8]):  # cap at 8 cols to avoid huge queries
            pk = f"s{i}"
            where_parts.append(f"\"{col}\" LIKE :{pk}")
            params[pk] = f"%{search}%"

    where_sql = f"WHERE ({' OR '.join(where_parts)})" if where_parts else ""
    order_sql = f'ORDER BY "{sort_col}" {sort_dir_safe}' if sort_col else "ORDER BY rowid DESC"

    # Count
    count_q = f'SELECT COUNT(*) FROM "{table}" {where_sql}'
    total = db.execute(text(count_q), params).scalar() or 0

    # Fetch page
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset
    data_q = f'SELECT * FROM "{table}" {where_sql} {order_sql} LIMIT :limit OFFSET :offset'
    rows = db.execute(text(data_q), params).fetchall()

    return {
        "table": table,
        "columns": col_names,
        "rows": [list(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


# =============================================================================
# ADMIN AUDIT LOG — READ ENDPOINT
# =============================================================================
# Audit fix (May 2026): admin actions on user/financial state were leaving no
# trace beyond systemd journals. The `admin_audit_log` table (created in db.py)
# now records every high-impact mutation; this endpoint reads it back with
# pagination + filters so admins can review their own (and others') history.

@router.get("/api/admin/audit-log")
def get_admin_audit_log(
    admin=Depends(check_admin),
    db=Depends(get_db),
    page: int = 1,
    page_size: int = 50,
    action: str = "",
    target_table: str = "",
    admin_user_id: int = None,
):
    """List admin audit log entries with optional filters and pagination."""
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    where = ["1=1"]
    params = {}
    if action:
        where.append("action = :action")
        params["action"] = action
    if target_table:
        where.append("target_table = :tt")
        params["tt"] = target_table
    if admin_user_id is not None:
        where.append("admin_user_id = :auid")
        params["auid"] = admin_user_id
    where_sql = " AND ".join(where)
    total = db.execute(
        text(f"SELECT COUNT(*) FROM admin_audit_log WHERE {where_sql}"),
        params
    ).scalar() or 0
    params["lim"] = page_size
    params["off"] = (page - 1) * page_size
    rows = db.execute(
        text(f"""SELECT id, admin_user_id, admin_email, action, target_table, target_id,
                        before_json, after_json, metadata_json, ip_address,
                        datetime(created_at) as created_at
                 FROM admin_audit_log
                 WHERE {where_sql}
                 ORDER BY id DESC
                 LIMIT :lim OFFSET :off"""),
        params
    ).mappings().all()
    return {
        "page": page, "page_size": page_size, "total": total,
        "total_pages": max(1, -(-total // page_size)),
        "rows": [dict(r) for r in rows],
    }


# Audit fix (May 2026): protect all tables that the user-facing cancel/delete
# flows touch. Direct row mutation via the generic admin tool bypasses
# transaction cleanup, contract PDF deletion, flyer cleanup, notification
# fan-out, cancellation emails, and cancelled-gig blast — all of which the
# canonical cancel paths run for free. Admins must use the dedicated admin UI
# / cancel endpoints for these tables.
_PROTECTED_TABLES = {
    "users", "platform_settings",
    "gigs", "gig_slots", "transactions", "gig_contracts", "flyers",
    "payment_cancellations", "venue_payment_overrides", "entity_payment_settings",
    # Audit fix (May 2026): affiliate tables. Admin could otherwise DELETE
    # referrals/earnings/payouts via DB tools, bypassing delete_referral /
    # payout reversal endpoints that validate state.
    "affiliate_referrals", "affiliate_earnings", "affiliate_payouts",
    # Audit fix (May 2026 part 5): the audit log itself MUST be uneditable
    # from the admin DB browser — a compromised or rogue admin could otherwise
    # `DELETE FROM admin_audit_log` to erase their own trail. The user-
    # management surfaces (entity_users, entity_invitations) should go through
    # the dedicated endpoints which enforce permissions / re-invite flows.
    # vanity_urls is reserved-name-checked at the endpoint; allowing raw edits
    # would let an admin hijack public profiles. email_templates has an
    # auto-export-to-disk side effect (`_export_email_templates_to_disk`) — raw
    # DB writes wouldn't trigger the export, so the file/DB would silently
    # diverge.
    "admin_audit_log", "entity_users", "entity_invitations",
    "vanity_urls", "email_templates",
    # Idempotency tables — admin writes here could silently drop dedup state
    # and let a duplicate webhook or approval link replay.
    "stripe_webhook_events", "pending_approval_tokens",
    # Audit fix (Jul 2026 delete-audit): raw DELETE of an artist/venue row
    # via this tool bypasses services.entity_delete — no tombstone, no
    # in-flight gig cancellation, no notification fan-out, no file
    # cleanup, and preferred_artists/entity_users rows for other entities
    # get orphaned. Force use of the dedicated DELETE endpoints. Also
    # protect the historical review / notification / venue-contract
    # tables since raw DELETE would leave stale aggregate ratings and
    # dangling FK references from gig_contracts.
    "artists", "venues", "venue_contracts",
    "artist_reviews", "venue_reviews", "notifications",
    "preferred_artists", "venue_artist_bans",
    "connect_account_health",
}


@router.put("/api/admin/db/tables/{table}/rows/{rowid}")
def update_row(
    request: Request,
    table: str,
    rowid: int,
    data: dict,
    admin=Depends(check_admin),
    db=Depends(get_db)
):
    """Update a single row by rowid."""
    # Audit fix (May 2026): protect financial / booking tables — direct
    # UPDATE bypasses transaction recompute, contract state machines, etc.
    # Use the dedicated admin endpoints / user-facing flows instead.
    if table in _PROTECTED_TABLES:
        raise HTTPException(403, f"Direct update of '{table}' is not allowed through this tool.")
    valid = set(_list_tables(db))
    if table not in valid:
        raise HTTPException(404, "Table not found")

    cols_raw = _column_info(db, table)
    col_names = {c[1] for c in cols_raw}

    # Only update columns that actually exist
    updates = {k: v for k, v in data.items() if k in col_names}
    if not updates:
        raise HTTPException(400, "No valid columns to update")

    # Audit fix (May 2026): capture before-state so the audit log shows the
    # diff. Admin direct-edit on `referrals` etc. now leaves a trail.
    before_row = None
    try:
        before_row = db.execute(text(f'SELECT * FROM "{table}" WHERE rowid = :rowid'), {"rowid": rowid}).mappings().first()
        before_row = dict(before_row) if before_row else None
    except Exception:
        pass

    set_parts = ", ".join(f'"{k}" = :col_{k}' for k in updates)
    params = {f"col_{k}": v for k, v in updates.items()}
    params["rowid"] = rowid

    db.execute(text(f'UPDATE "{table}" SET {set_parts} WHERE rowid = :rowid'), params)
    db.commit()

    from backend.utils import log_admin_action
    log_admin_action(db, admin, "db_tools_update", target_table=table, target_id=rowid,
                     before=before_row, after=updates, request=request)
    return {"ok": True}


@router.delete("/api/admin/db/tables/{table}/rows/{rowid}")
def delete_row(
    request: Request,
    table: str,
    rowid: int,
    admin=Depends(check_admin),
    db=Depends(get_db)
):
    """Delete a single row by rowid. Forbidden on critical tables."""
    if table in _PROTECTED_TABLES:
        raise HTTPException(403, f"Direct deletion from '{table}' is not allowed through this tool. Use the dedicated admin UI.")

    valid = set(_list_tables(db))
    if table not in valid:
        raise HTTPException(404, "Table not found")

    # Audit fix (May 2026): capture row before deletion for the audit log.
    before_row = None
    try:
        before_row = db.execute(text(f'SELECT * FROM "{table}" WHERE rowid = :rowid'), {"rowid": rowid}).mappings().first()
        before_row = dict(before_row) if before_row else None
    except Exception:
        pass

    db.execute(text(f'DELETE FROM "{table}" WHERE rowid = :rowid'), {"rowid": rowid})
    db.commit()

    from backend.utils import log_admin_action
    log_admin_action(db, admin, "db_tools_delete", target_table=table, target_id=rowid,
                     before=before_row, request=request)
    return {"ok": True}


@router.post("/api/admin/db/tables/{table}/rows")
def insert_row(
    request: Request,
    table: str,
    data: dict,
    admin=Depends(check_admin),
    db=Depends(get_db)
):
    """Insert a new row into a table."""
    if table in _PROTECTED_TABLES:
        raise HTTPException(403, f"Direct insertion into '{table}' is not allowed through this tool.")

    valid = set(_list_tables(db))
    if table not in valid:
        raise HTTPException(404, "Table not found")

    cols_raw = _column_info(db, table)
    col_names = {c[1] for c in cols_raw}

    inserts = {k: v for k, v in data.items() if k in col_names and k != "id"}
    if not inserts:
        raise HTTPException(400, "No valid columns provided")

    cols_sql = ", ".join(f'"{k}"' for k in inserts)
    vals_sql = ", ".join(f':col_{k}' for k in inserts)
    params = {f"col_{k}": v for k, v in inserts.items()}

    # 2026-07-25 bug fix: RETURNING id inline instead of a separate
    # last_insert_rowid() after commit — that pattern is per-connection
    # and can return the wrong id when the pool swaps connections.
    # See demo_requests.py:1191 for the incident that surfaced this class.
    new_id = db.execute(
        text(f'INSERT INTO "{table}" ({cols_sql}) VALUES ({vals_sql}) RETURNING id'),
        params
    ).scalar()
    db.commit()

    from backend.utils import log_admin_action
    log_admin_action(db, admin, "db_tools_insert", target_table=table, target_id=new_id,
                     after=inserts, request=request)
    return {"ok": True, "id": new_id}


@router.get("/api/admin/db/export/{table}")
def export_table_csv(
    table: str,
    admin=Depends(check_admin),
    db=Depends(get_db)
):
    """Export full table as CSV."""
    import csv, io
    from fastapi.responses import StreamingResponse

    valid = set(_list_tables(db))
    if table not in valid:
        raise HTTPException(404, "Table not found")

    cols_raw = _column_info(db, table)
    col_names = [c[1] for c in cols_raw]
    rows = db.execute(text(f'SELECT * FROM "{table}"')).fetchall()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(col_names)
    for row in rows:
        w.writerow(list(row))
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'}
    )


# ── SMTP TEST ENDPOINT ──────────────────────────────────────────────────────
@router.post("/api/admin/test-smtp")
# Audit fix (May 2026 part 5): cap test-smtp at 10/hour. Admin can script
# this with arbitrary `to` addresses → blasts Gmail's per-recipient cap.
@limiter.limit("10/hour")
def test_smtp(data: dict, request: Request, admin=Depends(check_admin), db=Depends(get_db)):
    """
    Admin utility: send a test email to verify SMTP is configured correctly.
    Body: { "to": "email@example.com" }
    Returns detailed result including any error message.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.utils import formataddr
    from sqlalchemy import text

    to_email = (data.get("to") or "").strip()
    if not to_email:
        raise HTTPException(400, "to email required")

    # Load SMTP settings
    rows = db.execute(
        text("SELECT setting_key, setting_value FROM platform_settings "
             "WHERE setting_key IN ('platform_email','platform_email_password',"
             "'platform_smtp_server','platform_smtp_port','platform_email_from_name')")
    ).fetchall()
    settings = {r[0]: r[1] for r in rows}

    smtp_email    = settings.get("platform_email", "")
    smtp_password = settings.get("platform_email_password", "")
    smtp_server   = settings.get("platform_smtp_server", "smtp.gmail.com")
    smtp_port     = int(settings.get("platform_smtp_port", "587"))
    from_name     = settings.get("platform_email_from_name", "GigsFill")

    if not smtp_email or not smtp_password:
        return {"ok": False, "error": "SMTP not configured — platform_email or platform_email_password missing from platform_settings"}

    try:
        msg = MIMEMultipart('alternative')
        msg["From"]    = formataddr((from_name, smtp_email))
        msg["To"]      = to_email
        msg["Subject"] = "GigsFill SMTP Test"
        msg.attach(MIMEText(
            f"<p>This is a test email from GigsFill.</p>"
            f"<p>SMTP: {smtp_server}:{smtp_port}<br>From: {smtp_email}</p>",
            "html"
        ))

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as s:
                s.login(smtp_email, smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as s:
                s.starttls()
                s.login(smtp_email, smtp_password)
                s.send_message(msg)

        # Audit fix (May 2026 part 5): log every test-smtp call so abuse
        # is traceable (admin-controlled recipient + arbitrary body).
        try:
            from backend.utils import log_admin_action
            log_admin_action(
                db, admin, "test_smtp",
                target_table="platform_settings", target_id=None,
                before=None,
                after={"recipient": to_email, "smtp_server": smtp_server, "smtp_port": smtp_port},
                request=request,
            )
        except Exception:
            pass
        return {"ok": True, "message": f"Test email sent to {to_email} via {smtp_server}:{smtp_port}"}

    except Exception as e:
        return {"ok": False, "error": str(e),
                "smtp_server": smtp_server, "smtp_port": smtp_port, "smtp_email": smtp_email}


# ==========================================================================
# Off-platform-pay flagging — admin tools for venues that route real pay
# off-site via $0 listings (flat-$0 or pure-door no-guarantee). The booking
# code in routes/gigs.py:_flag_zero_pay_booking auto-bumps the venue's
# zero_pay_booking_count and notifies admins. These endpoints expose the
# data + let admin raise this venue's fee_pct_override to compensate.
# ==========================================================================

@router.get("/api/admin/zero-pay-flagged-venues")
def list_zero_pay_flagged_venues(admin=Depends(check_admin), db=Depends(get_db)):
    """List venues that have booked $0 gigs, sorted by recency. Returns
    each venue's running count, last-flagged timestamp, and current
    fee_pct_override (NULL = uses platform default).
    """
    rows = db.execute(
        text("""
            SELECT v.id as venue_id, v.venue_name, v.city, v.state,
                   vpo.zero_pay_booking_count, vpo.last_zero_pay_at,
                   vpo.fee_pct_override
            FROM venue_payment_overrides vpo
            JOIN venues v ON v.id = vpo.venue_id
            WHERE COALESCE(vpo.zero_pay_booking_count, 0) > 0
            ORDER BY vpo.last_zero_pay_at DESC NULLS LAST
        """)
    ).mappings().all()
    return [dict(r) for r in rows]


@router.put("/api/admin/venues/{venue_id}/fee-pct-override")
def set_venue_fee_pct_override(venue_id: int, data: dict,
                                 admin=Depends(check_admin), db=Depends(get_db)):
    """Set or clear a per-venue platform fee percentage override.

    Body:
      { "fee_pct_override": 25.0 }  - set venue to 25% platform fee
      { "fee_pct_override": null }  - clear override, revert to platform default

    The override applies to NEW bookings + any settle/recompute on existing
    'scheduled' transactions for this venue. Already-charged transactions
    are not retroactively re-rated.
    """
    raw = data.get("fee_pct_override")
    if raw is not None:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "fee_pct_override must be a number or null")
        if v < 0 or v > 100:
            raise HTTPException(400, "fee_pct_override must be between 0 and 100")
        new_val = v
    else:
        new_val = None

    venue = db.execute(
        text("SELECT id FROM venues WHERE id = :vid"), {"vid": venue_id}
    ).mappings().first()
    if not venue:
        raise HTTPException(404, "Venue not found")

    # Upsert: try UPDATE first, INSERT if no row.
    _r = db.execute(
        text("UPDATE venue_payment_overrides SET fee_pct_override = :v WHERE venue_id = :vid"),
        {"v": new_val, "vid": venue_id}
    )
    if (_r.rowcount or 0) == 0:
        db.execute(
            text("""INSERT INTO venue_payment_overrides
                    (venue_id, payments_suspended, fee_pct_override, notes)
                    VALUES (:vid, 0, :v, :note)"""),
            {"vid": venue_id, "v": new_val,
             "note": "Admin set fee_pct_override"}
        )
    db.commit()
    return {"ok": True, "venue_id": venue_id, "fee_pct_override": new_val}


# ==========================================================================
# Open-gig daily digest — admin visibility (Jun 19 2026). The digest
# pipeline enqueues per-artist notifications hourly and sends one email
# per artist per day at their local 9 AM. These endpoints expose the
# queue state so admin can see "what got bundled today / will be sent /
# was rejected" without having to grep the scheduler logs.
# ==========================================================================

@router.get("/api/admin/digest-stats")
def get_digest_stats(admin=Depends(check_admin), db=Depends(get_db)):
    """Snapshot of the open-gig digest queue.

    Returns:
      - master flag state (enabled/disabled)
      - send-time setting (the local-hour each artist gets their digest)
      - pending: rows queued but not yet sent
      - sent_today / sent_7d: counts of digest items consumed
      - recent_sends: last 25 user-level send batches
    """
    out = {}
    out["digest_enabled"] = (db.execute(
        text("SELECT setting_value FROM platform_settings WHERE setting_key='open_gig_daily_digest_enabled'")
    ).scalar() or "true").lower() in ("true", "1")
    out["digest_hour"] = int((db.execute(
        text("SELECT setting_value FROM platform_settings WHERE setting_key='open_gig_daily_digest_hour'")
    ).scalar() or "6"))
    out["pending_count"] = db.execute(
        text("SELECT COUNT(*) FROM artist_email_digest_queue WHERE sent_at IS NULL")
    ).scalar() or 0
    out["pending_users"] = db.execute(
        text("SELECT COUNT(DISTINCT user_id) FROM artist_email_digest_queue WHERE sent_at IS NULL")
    ).scalar() or 0
    out["sent_today"] = db.execute(
        text("SELECT COUNT(*) FROM artist_email_digest_queue WHERE sent_at >= datetime('now', '-1 day')")
    ).scalar() or 0
    out["sent_7d"] = db.execute(
        text("SELECT COUNT(*) FROM artist_email_digest_queue WHERE sent_at >= datetime('now', '-7 days')")
    ).scalar() or 0

    # Per-user recent sends. Group by (user_id, sent_at within the same
    # minute) so a single send-batch shows as one row, not N items.
    recent = db.execute(
        text("""
            SELECT q.user_id,
                   u.email,
                   COALESCE((SELECT a.name FROM artists a WHERE a.user_id = u.id ORDER BY a.id LIMIT 1), '') as artist_name,
                   COUNT(*) as gig_count,
                   COUNT(DISTINCT q.venue_id) as venue_count,
                   strftime('%Y-%m-%d %H:%M', MAX(q.sent_at)) as sent_at
            FROM artist_email_digest_queue q
            JOIN users u ON u.id = q.user_id
            WHERE q.sent_at IS NOT NULL
            GROUP BY q.user_id, strftime('%Y-%m-%d %H:%M', q.sent_at)
            ORDER BY MAX(q.sent_at) DESC
            LIMIT 25
        """)
    ).mappings().all()
    out["recent_sends"] = [dict(r) for r in recent]

    # Pending breakdown by user (so admin can see "user X has 8 queued
    # waiting for their 9 AM tick").
    pending = db.execute(
        text("""
            SELECT q.user_id,
                   u.email,
                   COALESCE((SELECT a.name FROM artists a WHERE a.user_id = u.id ORDER BY a.id LIMIT 1), '') as artist_name,
                   COUNT(*) as gig_count,
                   COUNT(DISTINCT q.venue_id) as venue_count,
                   MIN(q.queued_at) as oldest_queued
            FROM artist_email_digest_queue q
            JOIN users u ON u.id = q.user_id
            WHERE q.sent_at IS NULL
            GROUP BY q.user_id
            ORDER BY MIN(q.queued_at) ASC
            LIMIT 50
        """)
    ).mappings().all()
    out["pending_per_user"] = [dict(r) for r in pending]
    return out


@router.post("/api/admin/digest-resend")
def admin_resend_digest(data: dict, admin=Depends(check_admin), db=Depends(get_db)):
    """Resend the EXACT digest a user already received in a given minute.
    The queue's sent_at column groups send-batches by minute (matches
    the digest-stats `recent_sends` rows). We re-fetch those rows
    (sent or not), re-render the email, and send it with [ADMIN-RESEND]
    in the subject. sent_at is not touched — the original send timestamp
    stays so the audit trail isn't disturbed.

    Body: {user_id: int, sent_at_minute: 'YYYY-MM-DD HH:MM'}
    """
    from backend.scheduler import get_smtp_settings, send_email
    from backend.services.open_gig_digest import build_digest_for_user

    user_id = int(data.get("user_id") or 0)
    minute = (data.get("sent_at_minute") or "").strip()
    if not user_id or not minute:
        raise HTTPException(400, "user_id and sent_at_minute required")

    from backend.db import get_db_connection


    conn = get_db_connection()
    c = conn.cursor()
    try:
        # 2026-08-24: switched from _render_digest_email (legacy, queue-only
        # rows) to build_digest_for_user — the same path the daily digest
        # uses. Two bugs the legacy path had:
        #   1. External artist-logged gigs (artist_external_gigs) were
        #      completely absent — the queue only holds GigsFill open gigs,
        #      so a user's own manual bookings never showed.
        #   2. Subject "0 Venues" for multi-artist users: the legacy
        #      renderer set by_venue={} for multi_artist and used
        #      len(by_venue) as venue_count. A user with two artist
        #      profiles always saw "N Open Gigs at 0 Venues."
        # build_digest_for_user rebuilds fresh (external + booked + open),
        # handles multi-artist correctly, and uses the enriched
        # _render_digest_email_live subject format ("N Open + M Bookings").
        # Verify the batch actually existed before rendering, so admin
        # gets a helpful error if they clicked resend on a stale row.
        _batch_exists = c.execute(
            "SELECT COUNT(*) FROM artist_email_digest_queue WHERE user_id=? "
            "AND strftime('%Y-%m-%d %H:%M', sent_at) = ?",
            (user_id, minute)
        ).fetchone()
        if not _batch_exists or not _batch_exists[0]:
            return {"ok": False, "error": f"No batch found for user={user_id} at {minute}"}

        # build_digest_for_user takes a sqlite3 cursor (same as scheduler).
        digest = build_digest_for_user(c, user_id)
        if not digest.get("ok"):
            return {"ok": False, "user_id": user_id,
                    "error": digest.get("reason") or "Nothing live to send"}

        ok = send_email(get_smtp_settings(c), digest["email"],
                        "[ADMIN-RESEND] " + digest["subject"], digest["body"])
        if ok:
            try:
                from backend.utils import log_admin_action
                log_admin_action(
                    db, admin, "digest_resend",
                    target_table="artist_email_digest_queue",
                    target_id=user_id,
                    metadata={"sent_at_minute": minute,
                              "counts": digest.get("counts"),
                              "email": digest["email"]},
                )
            except Exception:
                pass
            return {"ok": True, "user_id": user_id, "sent_at_minute": minute,
                    "counts": digest.get("counts"), "email": digest["email"]}
        return {"ok": False, "user_id": user_id, "error": "send_email returned False"}
    finally:
        conn.close()


@router.post("/api/admin/digest-force-send/{user_id}")
def admin_force_send_digest(user_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    """Force a digest send for one user, bypassing the local-hour gate.
    Uses the SAME live-render pathway as the scheduler tick (Jul 2026)
    so what the admin sees in their inbox is identical to what real
    users receive at 6 AM local — including the new per-slot per-artist
    variant format for multi-artist users. Returns a summary of what
    got sent (open/booked/nearby counts).
    """
    from backend.services.open_gig_digest import build_digest_for_user
    from backend.scheduler import get_smtp_settings, send_email
    from backend.db import get_db_connection

    conn = get_db_connection()
    c = conn.cursor()
    try:
        result = build_digest_for_user(c, user_id)
        if not result["ok"]:
            return {"ok": False, "user_id": user_id, "sent": 0,
                    "reason": result["reason"]}
        smtp = get_smtp_settings(c)
        ok = send_email(smtp, result["email"],
                        "[ADMIN-FORCE] " + result["subject"], result["body"])
        if ok:
            try:
                from backend.utils import log_admin_action
                log_admin_action(
                    db, admin, "digest_force_send",
                    target_table="users",
                    target_id=user_id,
                    metadata={"counts": result["counts"], "email": result["email"]},
                )
            except Exception:
                pass
            return {"ok": True, "user_id": user_id,
                    "counts": result["counts"], "email": result["email"]}
        return {"ok": False, "user_id": user_id,
                "error": "send_email returned False — check journalctl"}
    finally:
        conn.close()



# ============================================================
# SYSTEM ALERTS — operational alerts surfaced to admin banner
# ============================================================
# Self-clearing: when the underlying condition is detected as fixed
# (e.g. stripe webhook signature verifies successfully again) the
# alert auto-resolves and the banner clears. Admin can also manually
# acknowledge to dismiss an alert that hasn't auto-cleared.

@router.get("/api/admin/system-alerts")
def admin_system_alerts(admin=Depends(check_admin), db=Depends(get_db)):
    """Return unresolved alerts + recent resolved ones (last 7 days).
    The polling banner on admin.html hits this every 60s."""
    active = db.execute(
        text("""SELECT id, alert_type, severity, message, details,
                       first_seen_at, last_seen_at, count,
                       acknowledged_at, acknowledged_by
                FROM system_alerts
                WHERE resolved_at IS NULL
                ORDER BY
                  CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                  last_seen_at DESC""")
    ).mappings().all()
    recent_resolved = db.execute(
        text("""SELECT id, alert_type, severity, message,
                       first_seen_at, resolved_at, resolved_by, count
                FROM system_alerts
                WHERE resolved_at IS NOT NULL
                  AND resolved_at >= datetime('now', '-7 days')
                ORDER BY resolved_at DESC
                LIMIT 20""")
    ).mappings().all()
    # 2026-07-25: recently-dismissed (still-active) alerts — the ones
    # admin hid from the banner but the underlying condition may or
    # may not have cleared. Surfaced in the Account Health history
    # panel so dismissed alerts aren't invisible.
    recent_dismissed = db.execute(
        text("""SELECT id, alert_type, severity, message, details,
                       first_seen_at, last_seen_at, count,
                       acknowledged_at, acknowledged_by
                FROM system_alerts
                WHERE resolved_at IS NULL
                  AND acknowledged_at IS NOT NULL
                  AND acknowledged_at >= datetime('now', '-30 days')
                ORDER BY acknowledged_at DESC
                LIMIT 20""")
    ).mappings().all()
    return {
        "active": [dict(r) for r in active],
        "recent_resolved": [dict(r) for r in recent_resolved],
        "recent_dismissed": [dict(r) for r in recent_dismissed],
    }


@router.post("/api/admin/system-alerts/{alert_id}/acknowledge")
def admin_acknowledge_alert(alert_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    """Manually dismiss an alert. Sets acknowledged_at + acknowledged_by
    on the row but leaves resolved_at NULL — the banner respects
    acknowledged status (hides the row) but the alert is still
    'active' for diagnostic purposes. Next fire of the same alert_type
    will re-surface it (clears acknowledged_at via record_alert's
    UPDATE)."""
    row = db.execute(
        text("SELECT id, alert_type FROM system_alerts WHERE id = :id AND resolved_at IS NULL"),
        {"id": alert_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Alert not found or already resolved")
    db.execute(
        text("""UPDATE system_alerts
                SET acknowledged_at = CURRENT_TIMESTAMP,
                    acknowledged_by = :who
                WHERE id = :id"""),
        {"id": alert_id, "who": admin.email or f"user_id={admin.id}"}
    )
    db.commit()
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, admin, "acknowledge_alert",
            target_table="system_alerts",
            target_id=alert_id,
            metadata={"alert_type": row["alert_type"]},
        )
    except Exception:
        pass
    return {"ok": True, "alert_id": alert_id}


# ============================================================
# CONNECT ACCOUNT HEALTH — admin visibility for Stripe Connect
# ============================================================
# Cached snapshot updated daily by the scheduler
# (services/connect_health.audit_all_accounts). Avoids manual
# Stripe Dashboard checks at scale — one screen shows every
# artist whose account needs attention + remediation actions.

@router.get("/api/admin/connect-health")
def admin_connect_health(admin=Depends(check_admin), db=Depends(get_db)):
    """Returns aggregated Connect account health + per-artist
    detail of unhealthy accounts. Healthy accounts collapsed into
    a count so the response stays small at 1000+ artists."""
    rows = db.execute(text("""
        SELECT
            cah.artist_id,
            cah.stripe_connect_account_id,
            cah.charges_enabled, cah.payouts_enabled, cah.details_submitted,
            cah.disabled_reason, cah.requirements_count,
            cah.currently_due_json, cah.past_due_json, cah.errors_json,
            cah.last_polled_at, cah.last_changed_at, cah.artist_emailed_at,
            cah.unhealthy_since, cah.admin_alerted_at, cah.auto_suspended_at,
            a.name as artist_name, u.email as artist_email
        FROM connect_account_health cah
        LEFT JOIN artists a ON a.id = cah.artist_id
        LEFT JOIN users u ON u.id = a.user_id
        ORDER BY
          CASE WHEN cah.payouts_enabled = 0 OR cah.disabled_reason IS NOT NULL THEN 0 ELSE 1 END,
          cah.requirements_count DESC,
          cah.last_changed_at DESC NULLS LAST
    """)).mappings().all()
    import json as _json
    healthy = 0
    unhealthy = []
    for r in rows:
        is_healthy = (
            bool(r["payouts_enabled"])
            and not (r["disabled_reason"] or "")
            and int(r["requirements_count"] or 0) == 0
        )
        if is_healthy:
            healthy += 1
            continue
        d = dict(r)
        for k in ("currently_due_json", "past_due_json", "errors_json"):
            try:
                d[k.replace("_json", "")] = _json.loads(d.get(k) or "[]")
            except Exception:
                d[k.replace("_json", "")] = []
            d.pop(k, None)
        unhealthy.append(d)
    last_audit = db.execute(text(
        "SELECT MAX(last_polled_at) FROM connect_account_health"
    )).scalar()
    return {
        "healthy_count": healthy,
        "unhealthy_count": len(unhealthy),
        "total": healthy + len(unhealthy),
        "last_audit_at": last_audit,
        "unhealthy": unhealthy,
    }


@router.post("/api/admin/connect-health/audit-now")
def admin_connect_health_audit_now(admin=Depends(check_admin), db=Depends(get_db)):
    """Force a fresh audit cycle. Useful when an admin just took an
    action and wants to confirm the state without waiting for the
    daily tick. Runs synchronously — at 1000+ artists this takes a
    few seconds, but admin endpoints are expected to block."""
    try:
        from backend.services.connect_health import audit_all_accounts
        audit_all_accounts()
        try:
            from backend.utils import log_admin_action
            log_admin_action(db, admin, "connect_health_audit_now",
                             target_table="connect_account_health")
        except Exception:
            pass
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Audit failed: {e}")


@router.post("/api/admin/connect-health/{artist_id}/email-onboarding")
def admin_email_onboarding_link(artist_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    """Force-send the Stripe onboarding email to this artist
    immediately, bypassing the per-artist debounce. Useful when the
    artist says "I never got the email"."""
    import sqlite3 as _sq
    from backend.db import DB_PATH
    conn = _sq.connect(str(DB_PATH))
    try:
        # Clear the debounce timer so _maybe_email_artist actually fires
        conn.execute(
            "UPDATE connect_account_health SET artist_emailed_at = NULL WHERE artist_id = ?",
            (artist_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT stripe_connect_account_id, payouts_enabled, charges_enabled, "
            "disabled_reason, requirements_count "
            "FROM connect_account_health WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "No health record for this artist")
        snap = {
            "artist_id": artist_id,
            "connect_account_id": row[0],
            "payouts_enabled": bool(row[1]),
            "charges_enabled": bool(row[2]),
            "disabled_reason": row[3],
            "requirements_count": int(row[4] or 0),
            "is_healthy": False,
        }
        from backend.services.connect_health import _maybe_email_artist
        sent = _maybe_email_artist(conn, artist_id, snap)
        try:
            from backend.utils import log_admin_action
            log_admin_action(
                db, admin, "connect_email_onboarding",
                target_table="connect_account_health", target_id=artist_id,
                metadata={"sent": bool(sent)},
            )
        except Exception:
            pass
        return {"ok": bool(sent), "artist_id": artist_id}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Admin: force-transfer ownership of any venue/artist to any user
# (Jul 22 2026). Wraps the same helper used by the owner self-serve
# endpoint in entity_users.py, but bypasses the "must be current owner"
# guard and the "target must be a team member" guard. Logs to
# admin_audit_log so every override is traceable.
@router.post("/api/admin/artist/{artist_id}/transfer-owner")
def admin_transfer_artist_owner(artist_id: int, data: dict, request: Request,
                                 admin=Depends(check_admin), db=Depends(get_db)):
    return _admin_transfer_owner('artist', artist_id, data, admin, request, db)

@router.post("/api/admin/venue/{venue_id}/transfer-owner")
def admin_transfer_venue_owner(venue_id: int, data: dict, request: Request,
                                admin=Depends(check_admin), db=Depends(get_db)):
    return _admin_transfer_owner('venue', venue_id, data, admin, request, db)

def _admin_transfer_owner(entity_type: str, entity_id: int, data: dict,
                          admin, request, db):
    from backend.routes.entity_users import _do_transfer_owner
    new_owner_id = int(data.get("new_owner_user_id") or 0)
    if not new_owner_id:
        raise HTTPException(400, "new_owner_user_id required")
    # Capture the pre-transfer owner id for the audit log.
    if entity_type == 'artist':
        prior_owner = db.execute(
            text("SELECT user_id FROM artists WHERE id = :i"), {"i": entity_id}
        ).scalar()
    else:
        prior_owner = db.execute(
            text("SELECT user_id FROM venues  WHERE id = :i"), {"i": entity_id}
        ).scalar()
    result = _do_transfer_owner(db, entity_type, entity_id, new_owner_id, admin.id)
    try:
        log_admin_action(
            db, admin, f"transfer_{entity_type}_owner",
            target_table=(entity_type + 's'), target_id=entity_id,
            before={"user_id": prior_owner},
            after={"user_id": new_owner_id},
            request=request,
        )
    except Exception:
        pass
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Admin Directory endpoints (Jul 21 2026)
# ─────────────────────────────────────────────────────────────────────────────
# Richer-column payloads for the new top-level "Directory" tab. Separate from
# the existing /api/admin/users|artists|venues so the Platform Settings
# drill-downs stay unchanged. Aggregations (gig counts, lifetime totals,
# ratings) are computed via correlated subqueries — fine for the low-thousand
# row counts we're dealing with; would move to materialized views if data
# grew past ~50k entities per type.

@router.get("/api/admin/directory/users")
def directory_users(admin=Depends(check_admin), db=Depends(get_db)):
    """All users with rich activity + revenue columns."""
    from backend.utils import to_admin_bool
    cols = _column_info(db, "users")
    has_last_login = any(c[1] == 'last_login' for c in cols)
    login_col = "u.last_login" if has_last_login else "NULL as last_login"
    rows = db.execute(text(f"""
        SELECT
            u.id, u.first_name, u.last_name, u.email, u.phone, u.is_admin,
            u.created_at, {login_col},
            (SELECT COUNT(*) FROM artists WHERE user_id = u.id AND deleted_at IS NULL) as artist_count,
            (SELECT COUNT(*) FROM venues  WHERE user_id = u.id AND deleted_at IS NULL) as venue_count,
            -- Total gigs where this user's artists were booked
            (SELECT COUNT(*)
               FROM gigs g
               JOIN artists a ON a.id = g.artist_id
              WHERE a.user_id = u.id AND g.status = 'booked') as gigs_booked_as_artist,
            -- Total gigs this user's venues posted
            (SELECT COUNT(*)
               FROM gigs g
               JOIN venues v ON v.id = g.venue_id
              WHERE v.user_id = u.id) as gigs_posted_as_venue,
            -- Lifetime venue charges (sum of venue_charge_cents on cleared parent rows)
            COALESCE((SELECT SUM(COALESCE(t.venue_charge_cents, 0))
               FROM transactions t
               JOIN gigs g  ON g.id = t.gig_id
               JOIN venues v ON v.id = g.venue_id
              WHERE v.user_id = u.id
                AND COALESCE(t.transaction_type, 'single') IN ('venue_charge','single')
                AND t.status IN ('paid','transferred','charged')), 0) as lifetime_venue_spend_cents,
            -- Lifetime artist payouts (sum of artist_payout_cents on cleared payout rows)
            COALESCE((SELECT SUM(COALESCE(t.artist_payout_cents, 0))
               FROM transactions t
               JOIN artists a ON a.id = t.artist_id
              WHERE a.user_id = u.id
                AND COALESCE(t.transaction_type, 'single') IN ('artist_payout','single')
                AND t.status IN ('paid','transferred')), 0) as lifetime_artist_earnings_cents
        FROM users u
        ORDER BY u.created_at DESC
    """)).mappings().all()
    return [
        {
            "id": r["id"],
            "first_name": r["first_name"] or '',
            "last_name":  r["last_name"] or '',
            "email":      r["email"] or '',
            "phone":      r["phone"] or '',
            "is_admin":   to_admin_bool(r["is_admin"]),
            "created_at": r["created_at"],
            "last_login": r["last_login"],
            "artist_count": r["artist_count"] or 0,
            "venue_count":  r["venue_count"] or 0,
            "gigs_booked_as_artist": r["gigs_booked_as_artist"] or 0,
            "gigs_posted_as_venue":  r["gigs_posted_as_venue"] or 0,
            "lifetime_venue_spend_cents":    r["lifetime_venue_spend_cents"] or 0,
            "lifetime_artist_earnings_cents": r["lifetime_artist_earnings_cents"] or 0,
        }
        for r in rows
    ]


@router.get("/api/admin/directory/artists")
def directory_artists(admin=Depends(check_admin), db=Depends(get_db)):
    """All artists with band setup, ratings, gig totals, lifetime earnings."""
    rows = db.execute(text("""
        SELECT
            a.id, a.name, a.artist_type, a.city, a.state,
            a.band_formats, a.styles,
            a.avg_rating, a.review_count, a.created_at,
            u.id as owner_user_id, u.email as owner_email,
            u.first_name as owner_first_name, u.last_name as owner_last_name,
            -- Actual booked gigs (past + future)
            (SELECT COUNT(*) FROM gigs g
              WHERE (g.artist_id = a.id AND g.status = 'booked')
                 OR EXISTS (SELECT 1 FROM gig_slots gs
                              WHERE gs.gig_id = g.id
                                AND gs.artist_id = a.id
                                AND gs.status IN ('booked','pending_contract','awaiting_venue_contract'))) as gigs_booked,
            -- Lifetime payouts to this artist
            COALESCE((SELECT SUM(COALESCE(t.artist_payout_cents, 0))
                        FROM transactions t
                       WHERE t.artist_id = a.id
                         AND COALESCE(t.transaction_type, 'single') IN ('artist_payout','single')
                         AND t.status IN ('paid','transferred')), 0) as lifetime_payouts_cents,
            -- Stripe Connect onboarding state (via entity_payment_settings)
            (SELECT COALESCE(eps.stripe_connect_onboarding_complete, 0)
               FROM entity_payment_settings eps
              WHERE eps.entity_type = 'artist' AND eps.entity_id = a.id
              LIMIT 1) as stripe_connect_ok
        FROM artists a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.deleted_at IS NULL
        ORDER BY a.created_at DESC
    """)).mappings().all()
    return [
        {
            "id": r["id"],
            "name": r["name"] or '',
            "artist_type": r["artist_type"] or '',
            "city": r["city"] or '',
            "state": r["state"] or '',
            "band_formats": r["band_formats"] or '',
            "styles": r["styles"] or '',
            "avg_rating": float(r["avg_rating"]) if r["avg_rating"] is not None else None,
            "review_count": int(r["review_count"]) if r["review_count"] is not None else 0,
            "created_at": r["created_at"],
            "owner_user_id": r["owner_user_id"],
            "owner_email": r["owner_email"] or '',
            "owner_name": (f"{r['owner_first_name'] or ''} {r['owner_last_name'] or ''}").strip(),
            "gigs_booked": r["gigs_booked"] or 0,
            "lifetime_payouts_cents": r["lifetime_payouts_cents"] or 0,
            "stripe_connect_ok": bool(r["stripe_connect_ok"]),
        }
        for r in rows
    ]


@router.get("/api/admin/directory/venues")
def directory_venues(admin=Depends(check_admin), db=Depends(get_db)):
    """All venues with size, default pay, ratings, gig totals, lifetime spend, flags."""
    # `zero_pay_booking_count` is added by _add_columns() on startup, but
    # older dev DBs may not have run that migration yet. Detect at query
    # time and expression-swap (`... as zero_pay_booking_count` from 0
    # literal) so this endpoint never 500s on a fresh DB.
    _vcols = _column_info(db, "venues")
    _has_zpc = any(c[1] == 'zero_pay_booking_count' for c in _vcols)
    _zpc_expr = "COALESCE(v.zero_pay_booking_count, 0)" if _has_zpc else "0"
    rows = db.execute(text(f"""
        SELECT
            v.id, v.venue_name, v.city, v.state,
            v.venue_size, v.default_pay_dollars, v.default_pay_cents,
            v.avg_rating, v.review_count, v.created_at,
            {_zpc_expr} as zero_pay_booking_count,
            v.payment_status,
            u.id as owner_user_id, u.email as owner_email,
            u.first_name as owner_first_name, u.last_name as owner_last_name,
            (SELECT COUNT(*) FROM gigs g WHERE g.venue_id = v.id) as gigs_posted,
            (SELECT COUNT(*) FROM gigs g WHERE g.venue_id = v.id AND g.status = 'booked') as gigs_booked,
            COALESCE((SELECT SUM(COALESCE(t.venue_charge_cents, 0))
                        FROM transactions t
                        JOIN gigs g ON g.id = t.gig_id
                       WHERE g.venue_id = v.id
                         AND COALESCE(t.transaction_type, 'single') IN ('venue_charge','single')
                         AND t.status IN ('paid','transferred','charged')), 0) as lifetime_spend_cents,
            -- Does the venue have a saved payment method?
            (SELECT COUNT(*) FROM entity_payment_settings eps
              WHERE eps.entity_type = 'venue' AND eps.entity_id = v.id
                AND eps.stripe_customer_id IS NOT NULL
                AND eps.stripe_payment_method_id IS NOT NULL) as payment_method_ok
        FROM venues v
        LEFT JOIN users u ON u.id = v.user_id
        WHERE v.deleted_at IS NULL
        ORDER BY v.created_at DESC
    """)).mappings().all()
    return [
        {
            "id": r["id"],
            "venue_name": r["venue_name"] or '',
            "city": r["city"] or '',
            "state": r["state"] or '',
            "venue_size": r["venue_size"],
            "default_pay_dollars": r["default_pay_dollars"],
            "default_pay_cents": r["default_pay_cents"],
            "avg_rating": float(r["avg_rating"]) if r["avg_rating"] is not None else None,
            "review_count": int(r["review_count"]) if r["review_count"] is not None else 0,
            "created_at": r["created_at"],
            "zero_pay_booking_count": r["zero_pay_booking_count"] or 0,
            "payment_status": r["payment_status"] or 'active',
            "owner_user_id": r["owner_user_id"],
            "owner_email": r["owner_email"] or '',
            "owner_name": (f"{r['owner_first_name'] or ''} {r['owner_last_name'] or ''}").strip(),
            "gigs_posted": r["gigs_posted"] or 0,
            "gigs_booked": r["gigs_booked"] or 0,
            "lifetime_spend_cents": r["lifetime_spend_cents"] or 0,
            "payment_method_ok": bool(r["payment_method_ok"]),
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Admin: force-delete a user (Users tab per-row trash — Jul 21 2026)
# ─────────────────────────────────────────────────────────────────────────────
# Mirrors the self-delete cascade in routes/me.py delete_account, but auth is
# admin-only and the target is any user_id (not the authenticated user). The
# core cascade helpers live in services.entity_delete and are shared with the
# self-delete flow, so behavior stays identical: per-entity ownership audit,
# refuse if charged transactions still exist, notify booked-gig counterparties,
# mark in-flight txns as `account_deleted`, wipe every child table, then delete
# the user row + filesystem media. Admin cannot delete themselves via this
# endpoint (use /api/me/delete for that) — protects against self-lockout.
@router.delete("/api/admin/users/{target_user_id}")
def admin_delete_user(target_user_id: int, request: Request,
                      admin=Depends(check_admin), db=Depends(get_db)):
    if int(target_user_id) == int(admin.id):
        raise HTTPException(400, "Cannot delete your own admin account via this endpoint. Use /api/me/delete instead.")

    # Snapshot the user for the audit log BEFORE the cascade wipes them.
    target = db.execute(
        text("SELECT id, first_name, last_name, email FROM users WHERE id = :uid"),
        {"uid": target_user_id}
    ).mappings().first()
    if not target:
        raise HTTPException(404, "User not found")
    target_snapshot = dict(target)

    from backend.services.entity_delete import (
        assert_no_charged_transactions as _assert_no_charged,
        delete_artist as _delete_artist,
        delete_venue as _delete_venue,
    )
    import shutil as _shutil
    from pathlib import Path as _Path

    try:
        # Gather every LIVE entity the target owns — admin-force-delete
        # always takes them all (no per-entity opt-in like the self-delete
        # flow; if admin is wiping the user, orphaned entities can't stay).
        owned_artists = db.execute(
            text("SELECT id FROM artists WHERE user_id = :uid AND deleted_at IS NULL"),
            {"uid": target_user_id}
        ).fetchall()
        owned_venues = db.execute(
            text("SELECT id FROM venues WHERE user_id = :uid AND deleted_at IS NULL"),
            {"uid": target_user_id}
        ).fetchall()
        entity_targets = (
            [{"type": "artist", "id": aid} for (aid,) in owned_artists]
          + [{"type": "venue",  "id": vid} for (vid,) in owned_venues]
        )

        # Jul 22 2026 bug fix: TOMBSTONED entities (deleted_at IS NOT NULL)
        # still carry the departing user's user_id — the FK to users
        # blocks the final DELETE FROM users. Reassign those tombstones
        # to the acting admin so historical audit rows (reviews, past
        # transactions, invoices) that link to "[Deleted] X" remain
        # referentially valid. Cannot NULL user_id — column is NOT NULL.
        try:
            db.execute(
                text("""UPDATE venues  SET user_id = :aid
                        WHERE user_id = :uid AND deleted_at IS NOT NULL"""),
                {"aid": admin.id, "uid": target_user_id}
            )
            db.execute(
                text("""UPDATE artists SET user_id = :aid
                        WHERE user_id = :uid AND deleted_at IS NOT NULL"""),
                {"aid": admin.id, "uid": target_user_id}
            )
        except Exception as _e:
            import logging as _log
            _log.getLogger("gigsfill.admin").warning(
                f"tombstone reassignment failed for user {target_user_id}: {_e}"
            )

        # Refuse if charged/inflight transactions still exist on any owned
        # entity — matches self-delete guard; admin should refund first.
        for ent in entity_targets:
            _assert_no_charged(db, ent["type"], ent["id"])

        # Per-entity cascade. Each call returns filesystem paths to
        # clean up AFTER the DB commit (same pattern as self-delete).
        rm_paths_pending: list[str] = []
        for ent in entity_targets:
            if ent["type"] == "artist":
                _paths = _delete_artist(db, ent["id"], target_user_id) or []
            else:
                _paths = _delete_venue(db, ent["id"], target_user_id) or []
            rm_paths_pending.extend(_paths)

        # User-level table wipes — mirror of routes/me.py delete_account
        # step 3. Keep this list in sync when that flow gets new tables.
        for _stmt in (
            "DELETE FROM email_preferences        WHERE user_id = :uid",
            "DELETE FROM support_tickets          WHERE user_id = :uid",
            "DELETE FROM recommendations          WHERE user_id = :uid",
            "DELETE FROM notifications            WHERE user_id = :uid",
            "DELETE FROM entity_users             WHERE user_id = :uid",
            "DELETE FROM payment_methods          WHERE user_id = :uid",
            "DELETE FROM sms_preferences          WHERE user_id = :uid",
            "DELETE FROM affiliate_recommend_emails WHERE sender_user_id = :uid",
            "DELETE FROM affiliate_referrals      WHERE affiliate_user_id = :uid",
            "DELETE FROM affiliate_earnings       WHERE affiliate_user_id = :uid",
            "DELETE FROM affiliate_payouts        WHERE affiliate_user_id = :uid",
            "DELETE FROM entity_invitations       WHERE invited_by_user_id = :uid AND status = 'pending'",
            "DELETE FROM w9_forms                 WHERE entity_type = 'user' AND entity_id = :uid",
            "DELETE FROM gig_messages             WHERE sender_user_id = :uid",
            "DELETE FROM gig_message_hides        WHERE user_id = :uid",
            "DELETE FROM user_settings            WHERE user_id = :uid",
            "DELETE FROM user_availability        WHERE user_id = :uid",
            "DELETE FROM artist_email_digest_queue WHERE user_id = :uid",
        ):
            try:
                db.execute(text(_stmt), {"uid": target_user_id})
            except Exception:
                pass  # per-statement tolerance for older schemas

        # Reviews authored by this user — PRESERVE with reviewer_user_id
        # NULLed out, so aggregated ratings on venues/artists survive.
        try:
            db.execute(text("UPDATE artist_reviews SET reviewer_user_id = NULL WHERE reviewer_user_id = :uid"), {"uid": target_user_id})
            db.execute(text("UPDATE venue_reviews  SET reviewer_user_id = NULL WHERE reviewer_user_id = :uid"), {"uid": target_user_id})
        except Exception:
            # NOT NULL on older deployments → fall back to delete.
            try:
                db.execute(text("DELETE FROM artist_reviews WHERE reviewer_user_id = :uid"), {"uid": target_user_id})
                db.execute(text("DELETE FROM venue_reviews  WHERE reviewer_user_id = :uid"), {"uid": target_user_id})
            except Exception:
                pass

        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": target_user_id})
        db.commit()

        # Filesystem cleanup — after the commit so a commit-fail doesn't
        # leave the entities live with their media already wiped.
        try:
            from backend.services.entity_delete import _rm_tree
            for _p in rm_paths_pending:
                try: _rm_tree(_p)
                except Exception: pass
            _media = _Path(f"media/user_{target_user_id}")
            if _media.exists():
                try: _shutil.rmtree(_media)
                except Exception: pass
        except Exception:
            pass

        try:
            log_admin_action(
                db, admin, "delete_user",
                target_table="users", target_id=target_user_id,
                before=target_snapshot,
                metadata={
                    "artists_deleted": len(owned_artists),
                    "venues_deleted": len(owned_venues),
                },
                request=request,
            )
        except Exception:
            pass

        return {"ok": True, "artists_deleted": len(owned_artists), "venues_deleted": len(owned_venues)}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        import logging as _log
        _log.getLogger("gigsfill.admin").exception(
            f"admin_delete_user failed for target user {target_user_id} (admin {admin.id}): {e}"
        )
        db.rollback()
        raise HTTPException(500, f"Failed to delete user: {e}")
