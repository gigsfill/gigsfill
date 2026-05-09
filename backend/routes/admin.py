# backend/routes/admin.py - HANDLES BOTH PAY COLUMN SCENARIOS

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import logging
from sqlalchemy import text
from datetime import date
from backend.utils import utcnow_naive
from backend.db import get_db
from backend.routes.auth import get_current_user

router = APIRouter()

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

        if result["memory_pct"] >= 90:
            result["alerts"].append(
                f"🔴 CRITICAL: RAM at {result['memory_pct']}% ({result['memory_used_mb']}MB / {result['memory_total_mb']}MB). "
                "Upgrade your droplet immediately — the server is about to crash."
            )
        elif result["memory_pct"] >= 75:
            result["warnings"].append(
                f"🟡 WARNING: RAM at {result['memory_pct']}% ({result['memory_used_mb']}MB / {result['memory_total_mb']}MB). "
                "Consider upgrading to a 2GB droplet soon."
            )
        if result["swap_pct"] is not None and result["swap_pct"] >= 50:
            result["warnings"].append(
                f"🟡 WARNING: Swap at {result['swap_pct']}% — server is under memory pressure. "
                "Upgrade your droplet."
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
    total_mb = result.get("memory_total_mb") or 0
    if total_mb > 0 and total_mb <= 1100:  # 1GB droplet
        result["upgrade_recommended"] = (
            result["memory_pct"] is not None and result["memory_pct"] >= 70
        ) or (
            result["swap_pct"] is not None and result["swap_pct"] >= 25
        )
        result["droplet_size"] = "1GB"
        result["upgrade_path"] = (
            "Resize to $12/mo 2GB droplet + add $15/mo DigitalOcean Managed PostgreSQL = $27/mo total. "
            "Handles 1,000+ venues and 20,000+ artists."
        )
    else:
        result["upgrade_recommended"] = False
        result["droplet_size"] = f"{total_mb}MB"
        result["upgrade_path"] = None

    return result


@router.get("/api/admin/users")
def get_users(admin=Depends(check_admin), db=Depends(get_db)):
    """Get all users"""
    # Check if last_login column exists
    cols = db.execute(text("PRAGMA table_info(users)")).fetchall()
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
    cols = db.execute(text("PRAGMA table_info(users)")).fetchall()
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
    cols = db.execute(text("PRAGMA table_info(users)")).fetchall()
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
    columns = db.execute(text("PRAGMA table_info(gigs)")).fetchall()
    has_split_pay = any(col[1] == 'pay_dollars' for col in columns)
    
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
                a.name as artist_name,
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
                a.name as artist_name,
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
    }
    
    SENSITIVE_KEYS = {'platform_email_password', 'support_email_password'}
    # Audit fix (May 2026): capture before-state for the audit log so a
    # future incident can answer "what did the platform_fee look like
    # before admin X changed it on date Y?"
    _audit_before = {}
    _audit_after  = {}
    for frontend_key, db_key in key_mapping.items():
        if frontend_key in data:
            value = data[frontend_key]
            # Skip masked placeholder values — don't overwrite real password with mask
            if db_key in SENSITIVE_KEYS and str(value).startswith("•"):
                continue
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

            if existing:
                db.execute(
                    text("UPDATE platform_settings SET setting_value = :val WHERE setting_key = :key"),
                    {"val": value_str, "key": db_key}
                )
            else:
                db.execute(
                    text("INSERT INTO platform_settings (setting_key, setting_value) VALUES (:key, :val)"),
                    {"key": db_key, "val": value_str}
                )

    db.commit()

    if _audit_after:
        from backend.utils import log_admin_action
        log_admin_action(db, admin, "update_settings", target_table="platform_settings",
                         before=_audit_before, after=_audit_after, request=request)
    return {"ok": True}

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
            {'type': template_type, 'subj': data.get('subject'), 'body': data.get('body')}
        )
        
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Operation failed. Please try again.")

@router.get("/api/email-templates/export")
def export_email_templates(admin=Depends(check_admin), db=Depends(get_db)):
    """Export all email templates from DB back to backend/email_templates.py on disk"""
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
        
        # Build TEMPLATES dict entries
        template_entries = []
        for row in rows:
            key = row[0]
            subject = row[1] or ""
            body = row[2] or ""
            
            # Escape subject for single-quoted string
            subject_escaped = subject.replace("\\", "\\\\").replace("'", "\\'")
            
            # For body, use triple quotes — just need to escape any ''' inside
            body_escaped = body.replace("\\", "\\\\")
            # Use a unique delimiter if body contains '''
            if "'''" in body_escaped:
                body_escaped = body_escaped.replace("'''", "' ' '")
            
            template_entries.append(
                f'    "{key}": {{\n'
                f"        \"subject\": '{subject_escaped}',\n"
                f"        \"body\": '''{body_escaped}'''\n"
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
    
    # Check columns
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
        
        return {"status": "ok", "message": f"Exported {len(rows)} templates to backend/email_templates.py", "count": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Operation failed. Please try again.")

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
    
    db.commit()
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
        from backend.db import get_db
        from sqlalchemy import text as _text
        settings_row = db.execute(_text(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'payments_enabled'"
        )).scalar()
        # Audit fix (May 2026): tolerant comparison. JSON `true` writes the
        # string 'True' (capital), failing the literal `in ('1','true')`
        # check and silently demoting restored transactions to 'test' so the
        # scheduler skips charging them.
        restore_status = 'scheduled' if str(settings_row or '').strip().lower() in ('1', 'true') else 'test'
        db.execute(_text("""
            UPDATE transactions SET
                status = :rs,
                notes = COALESCE(notes || ' | ', '') || 'Free trial ended — restored to queue'
            WHERE status = 'suspended'
              AND gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid)
        """), {"rs": restore_status, "vid": venue_id})
        db.commit()

    # Recalculate all pending transactions for this venue's gigs
    _recalculate_venue_pending_transactions(db, venue_id, suspend)

    return {"ok": True, "payments_suspended": suspend, "venue_name": venue[1]}


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
              AND t.status IN ('scheduled', 'test')
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
def remove_venue_payment_override(venue_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    """Remove payment override for a venue (re-enable payments)"""
    db.execute(
        text("DELETE FROM venue_payment_overrides WHERE venue_id = :vid"),
        {"vid": venue_id}
    )
    db.commit()
    # Re-enable means free trial OFF — recalculate to add venue fee back
    _recalculate_venue_pending_transactions(db, venue_id, False)
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
    """Update a support ticket status"""
    data = await request.json()
    status = data.get("status", "open")
    
    db.execute(
        text("UPDATE support_tickets SET status = :status WHERE id = :tid"),
        {"status": status, "tid": ticket_id}
    )
    db.commit()
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
                reply_url = f"https://gigsfill.com/app/support-ticket.html?id={ticket_id}&token={ticket_token}"
                
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
    return {"message": "Template updated"}

@router.delete("/api/admin/flyers/templates/{tpl_id}")
def delete_admin_template(tpl_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    existing = db.execute(text(
        "SELECT id, name FROM flyers WHERE id = :id AND venue_id IS NULL AND is_template = 1"
    ), {"id": tpl_id}).fetchone()
    if not existing:
        raise HTTPException(404, "Template not found")
    if existing[1].lower() == "default template":
        raise HTTPException(400, "Cannot delete the site-wide Default Template — overwrite it instead")
    db.execute(text("DELETE FROM flyers WHERE id = :id"), {"id": tpl_id})
    db.commit()
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
def clear_log_buffer(admin=Depends(check_admin)):
    """Clear the in-memory log buffer"""
    try:
        from backend.log_buffer import log_buffer
        log_buffer.clear()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ============================================================
# DATABASE BROWSER — list tables
# ============================================================

@router.get("/api/admin/db/tables")
def list_tables(admin=Depends(check_admin), db=Depends(get_db)):
    """Return all table names and their row counts."""
    rows = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ).fetchall()
    result = []
    for (name,) in rows:
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
    valid = {r[0] for r in db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()}
    if table not in valid:
        raise HTTPException(404, "Table not found")
    cols = db.execute(text(f"PRAGMA table_info(\"{table}\")")).fetchall()
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
    valid = {r[0] for r in db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()}
    if table not in valid:
        raise HTTPException(404, "Table not found")

    cols_raw = db.execute(text(f"PRAGMA table_info(\"{table}\")")).fetchall()
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
    valid = {r[0] for r in db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()}
    if table not in valid:
        raise HTTPException(404, "Table not found")

    cols_raw = db.execute(text(f"PRAGMA table_info(\"{table}\")")).fetchall()
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

    valid = {r[0] for r in db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()}
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

    valid = {r[0] for r in db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()}
    if table not in valid:
        raise HTTPException(404, "Table not found")

    cols_raw = db.execute(text(f"PRAGMA table_info(\"{table}\")")).fetchall()
    col_names = {c[1] for c in cols_raw}

    inserts = {k: v for k, v in data.items() if k in col_names and k != "id"}
    if not inserts:
        raise HTTPException(400, "No valid columns provided")

    cols_sql = ", ".join(f'"{k}"' for k in inserts)
    vals_sql = ", ".join(f':col_{k}' for k in inserts)
    params = {f"col_{k}": v for k, v in inserts.items()}

    result = db.execute(text(f'INSERT INTO "{table}" ({cols_sql}) VALUES ({vals_sql})'), params)
    db.commit()
    new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()

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

    valid = {r[0] for r in db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()}
    if table not in valid:
        raise HTTPException(404, "Table not found")

    cols_raw = db.execute(text(f"PRAGMA table_info(\"{table}\")")).fetchall()
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
def test_smtp(data: dict, admin=Depends(check_admin), db=Depends(get_db)):
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
        msg = MIMEMultipart()
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

        return {"ok": True, "message": f"Test email sent to {to_email} via {smtp_server}:{smtp_port}"}

    except Exception as e:
        return {"ok": False, "error": str(e),
                "smtp_server": smtp_server, "smtp_port": smtp_port, "smtp_email": smtp_email}
