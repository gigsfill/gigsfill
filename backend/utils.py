"""
Shared utility functions for GigsFill backend
"""
from sqlalchemy import text
from fastapi import HTTPException
from datetime import datetime, timezone as _dt_tz
from zoneinfo import ZoneInfo


def log_admin_action(db, admin_user, action: str, *,
                     target_table: str = None, target_id=None,
                     before: dict = None, after: dict = None,
                     metadata: dict = None, request=None) -> None:
    """Write an admin audit row. Best-effort — must NEVER raise.

    Audit fix (May 2026): every admin mutation that touches user / financial
    state should call this so future incidents can reconstruct manual
    interventions. `before` / `after` are JSON-serializable dicts capturing
    the row(s) before and after. Use `metadata` for flow-specific context
    (reason, target_user_id, etc.).
    """
    try:
        import json
        from sqlalchemy import text as _t
        ip = ""
        if request is not None:
            try:
                xff = request.headers.get("x-forwarded-for") if hasattr(request, "headers") else None
                if xff:
                    ip = xff.split(",")[0].strip()
                elif getattr(request, "client", None):
                    ip = request.client.host
            except Exception:
                pass

        def _json_safe(o):
            try:
                return json.dumps(o, default=str)
            except Exception:
                return None

        db.execute(_t("""
            INSERT INTO admin_audit_log
                (admin_user_id, admin_email, action, target_table, target_id,
                 before_json, after_json, metadata_json, ip_address)
            VALUES (:uid, :email, :action, :tt, :tid, :before, :after, :meta, :ip)
        """), {
            "uid":    getattr(admin_user, "id", None) if admin_user is not None else None,
            "email":  getattr(admin_user, "email", None) if admin_user is not None else None,
            "action": action,
            "tt":     target_table,
            "tid":    str(target_id) if target_id is not None else None,
            "before": _json_safe(before)   if before   is not None else None,
            "after":  _json_safe(after)    if after    is not None else None,
            "meta":   _json_safe(metadata) if metadata is not None else None,
            "ip":     ip,
        })
        db.commit()
    except Exception as _e:
        import logging
        logging.getLogger("gigsfill.admin.audit").warning(f"audit log write failed: {_e}")


def utcnow_naive():
    """Drop-in replacement for the deprecated `datetime.utcnow()`.

    Returns a naive UTC datetime (timezone-stripped). Storage everywhere in
    this codebase is naive UTC — we keep that semantics and just stop tripping
    Python 3.12's deprecation warning."""
    return datetime.now(_dt_tz.utc).replace(tzinfo=None)


def to_admin_bool(v):
    """Coerce a stored `is_admin` value to a real Python bool.

    Tolerates every form the column has had over the codebase's history:
      - VARCHAR with literal `'true'`/`'false'` (legacy, pre-2026-05-08)
      - VARCHAR with `'1'`/`'0'` (post-migration values; SQLite stored
        them as TEXT due to VARCHAR column affinity even when written
        as ints)
      - INTEGER `1`/`0` (new deploys after 2026-05-08 migration)
      - Python `bool` (when read via SQLAlchemy `Column(Boolean)`)
      - `None` → False
    Use this anywhere `is_admin` is serialized to JSON or compared in
    Python — `not user.is_admin` is unsafe on the raw value because the
    string `'false'` is truthy.
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ('true', '1')
    return bool(v)


# ─── Per-venue timezone support ────────────────────────────────────────────────
#
# Each venue has its own IANA timezone (column `venues.timezone`). Used for:
#   - Computing payout `scheduled_process_at` (5pm in venue's local time, then
#     converted to UTC for storage)
#   - Any other "do this at X o'clock local time for this venue" logic
#
# If a venue doesn't have a timezone set yet, it's auto-derived from `venues.state`
# using the US state → IANA mapping below. The result is then persisted to the
# venue row so subsequent calls are O(1) lookups.
#
# This is US-only for now. International expansion later would need either
# proper lat/lng-based lookup (timezonefinder library) or country-aware
# mapping. Both are easy upgrades when needed.

# US state → IANA timezone. For states that span multiple zones, picks the
# dominant/capital zone. Edge cases (parts of FL, IN, KY, MI, ND, NE, OR, SD,
# TN, TX) are < 0.1% of population and may need manual override on the venue.
US_STATE_TIMEZONES = {
    'AL': 'America/Chicago',     'AK': 'America/Anchorage',
    'AZ': 'America/Phoenix',     'AR': 'America/Chicago',
    'CA': 'America/Los_Angeles', 'CO': 'America/Denver',
    'CT': 'America/New_York',    'DE': 'America/New_York',
    'DC': 'America/New_York',    'FL': 'America/New_York',     # most of FL is Eastern
    'GA': 'America/New_York',    'HI': 'Pacific/Honolulu',
    'ID': 'America/Boise',       'IL': 'America/Chicago',
    'IN': 'America/Indiana/Indianapolis',
    'IA': 'America/Chicago',     'KS': 'America/Chicago',
    'KY': 'America/New_York',    'LA': 'America/Chicago',
    'ME': 'America/New_York',    'MD': 'America/New_York',
    'MA': 'America/New_York',    'MI': 'America/Detroit',
    'MN': 'America/Chicago',     'MS': 'America/Chicago',
    'MO': 'America/Chicago',     'MT': 'America/Denver',
    'NE': 'America/Chicago',     'NV': 'America/Los_Angeles',
    'NH': 'America/New_York',    'NJ': 'America/New_York',
    'NM': 'America/Denver',      'NY': 'America/New_York',
    'NC': 'America/New_York',    'ND': 'America/Chicago',
    'OH': 'America/New_York',    'OK': 'America/Chicago',
    'OR': 'America/Los_Angeles', 'PA': 'America/New_York',
    'RI': 'America/New_York',    'SC': 'America/New_York',
    'SD': 'America/Chicago',     'TN': 'America/Chicago',     # most of TN is Central
    'TX': 'America/Chicago',     'UT': 'America/Denver',
    'VT': 'America/New_York',    'VA': 'America/New_York',
    'WA': 'America/Los_Angeles', 'WV': 'America/New_York',
    'WI': 'America/Chicago',     'WY': 'America/Denver',
    # US territories
    'PR': 'America/Puerto_Rico', 'VI': 'America/Puerto_Rico',
    'GU': 'Pacific/Guam',        'AS': 'Pacific/Pago_Pago',
    'MP': 'Pacific/Saipan',
}


def get_platform_timezone(db) -> str:
    """Return platform-wide IANA timezone string from platform_settings (default America/Los_Angeles)."""
    row = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_timezone'"
    )).scalar()
    return row or 'America/Los_Angeles'


def get_venue_timezone_str(db, venue_id: int) -> str:
    """Return the IANA timezone string for a venue.

    Resolution order:
      1. venues.timezone column if set
      2. Derived from venues.state via US_STATE_TIMEZONES (and persisted back
         to the row so subsequent lookups are direct)
      3. Platform timezone fallback
    """
    row = db.execute(text(
        "SELECT timezone, state FROM venues WHERE id = :vid"
    ), {"vid": venue_id}).mappings().first()

    if row and row.get("timezone"):
        return row["timezone"]

    state = (row.get("state") if row else "") or ""
    state = state.strip().upper()
    derived = US_STATE_TIMEZONES.get(state)

    if derived:
        # Persist the derived value so we don't have to re-derive every call
        try:
            db.execute(text(
                "UPDATE venues SET timezone = :tz WHERE id = :vid AND (timezone IS NULL OR timezone = '')"
            ), {"tz": derived, "vid": venue_id})
            db.commit()
        except Exception:
            # Non-fatal — the derivation still works, we just didn't cache it
            pass
        return derived

    return get_platform_timezone(db)


def get_venue_timezone(db, venue_id: int) -> ZoneInfo:
    """Return the venue's timezone as a ZoneInfo object (ready to use with datetime.replace(tzinfo=...))."""
    return ZoneInfo(get_venue_timezone_str(db, venue_id))


def venue_local_to_utc_naive(local_year: int, local_month: int, local_day: int,
                              local_hour: int, local_minute: int,
                              venue_tz: ZoneInfo) -> datetime:
    """Build a TZ-aware datetime in venue's local time, convert to UTC, return as naive UTC datetime
    (because the DB stores naive ISO strings; the entire codebase treats `scheduled_process_at`
    as naive-UTC).

    Example: venue in Pacific, want 5pm local on May 5
      → tz-aware (2026-05-05 17:00:00-07:00)
      → UTC (2026-05-06 00:00:00+00:00)
      → returned naive (2026-05-06 00:00:00) — this is what gets stored.
    """
    local_dt = datetime(local_year, local_month, local_day, local_hour, local_minute, tzinfo=venue_tz)
    utc_dt = local_dt.astimezone(_dt_tz.utc)
    # Strip tzinfo for storage as naive UTC
    return utc_dt.replace(tzinfo=None)


def check_venue_access(db, venue_id: int, user_id: int):
    """Verify user has access to this venue (owner or entity_user). Raises HTTPException 403 if not."""
    row = db.execute(
        text("""
            SELECT 1 FROM venues v
            WHERE v.id = :vid AND (
                v.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid
                )
            )
        """),
        {"vid": venue_id, "uid": user_id}
    ).first()
    if not row:
        raise HTTPException(403, "You don't have access to this venue")


def check_artist_access(db, artist_id: int, user_id: int):
    """Verify user has access to this artist (owner or entity_user). Raises HTTPException 403 if not."""
    row = db.execute(
        text("""
            SELECT 1 FROM artists a
            WHERE a.id = :aid AND (
                a.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                )
            )
        """),
        {"aid": artist_id, "uid": user_id}
    ).first()
    if not row:
        raise HTTPException(403, "You don't have access to this artist")


def get_all_entity_users(db, entity_type: str, entity_id: int):
    """
    Get ALL users who have access to an entity (owner + entity_users).
    Returns list of dicts with user_id, email, phone, sms_carrier.
    """
    if entity_type == 'artist':
        owner = db.execute(
            text("""
                SELECT a.user_id, u.email, u.phone, u.sms_carrier
                FROM artists a 
                JOIN users u ON a.user_id = u.id 
                WHERE a.id = :eid
            """),
            {"eid": entity_id}
        ).mappings().first()
    else:  # venue
        owner = db.execute(
            text("""
                SELECT v.user_id, u.email, u.phone, u.sms_carrier
                FROM venues v 
                JOIN users u ON v.user_id = u.id 
                WHERE v.id = :eid
            """),
            {"eid": entity_id}
        ).mappings().first()
    
    users = []
    if owner:
        users.append(dict(owner))
    
    # Get entity_users
    entity_users = db.execute(
        text("""
            SELECT eu.user_id, u.email, u.phone, u.sms_carrier
            FROM entity_users eu
            JOIN users u ON eu.user_id = u.id
            WHERE eu.entity_type = :etype AND eu.entity_id = :eid
        """),
        {"etype": entity_type, "eid": entity_id}
    ).mappings().all()
    
    for eu in entity_users:
        if not any(u["user_id"] == eu["user_id"] for u in users):
            users.append(dict(eu))
    
    return users
