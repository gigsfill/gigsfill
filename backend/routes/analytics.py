"""
Analytics routes for tracking public user activity
"""

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from typing import Optional
import sqlite3
from backend.db import get_db_connection as _analytics_conn
from backend.routes.admin import check_admin
import hashlib
import json
import os

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

DATABASE_PATH = os.environ.get("DATABASE_PATH", "backend.db")

class TrackEventRequest(BaseModel):
    event_type: str  # 'city_search', 'venue_search', 'artist_search', 'gig_click', 'filter_apply', 'page_view'
    event_data: Optional[dict] = None
    city: Optional[str] = None
    state: Optional[str] = None
    venue_id: Optional[int] = None
    artist_id: Optional[int] = None
    gig_id: Optional[int] = None
    session_id: Optional[str] = None

def get_db():
    conn = _analytics_conn()
    conn.row_factory = sqlite3.Row
    return conn

def hash_ip(ip: str) -> str:
    """Hash IP address for privacy - can still count unique visitors"""
    if not ip:
        return None
    # Add a salt for extra privacy
    salted = f"gigsfill_salt_{ip}"
    return hashlib.sha256(salted.encode()).hexdigest()[:16]

@router.post("/track")
async def track_event(event: TrackEventRequest, request: Request):
    """
    Track a public user activity event.
    No authentication required - this is for anonymous tracking.
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Get request metadata
        ip = request.client.host if request.client else None
        ip_hash = hash_ip(ip)
        user_agent = request.headers.get("user-agent", "")[:500]  # Limit length
        referrer = request.headers.get("referer", "")[:500]
        
        # Convert event_data dict to JSON string
        event_data_json = json.dumps(event.event_data) if event.event_data else None
        
        cursor.execute("""
            INSERT INTO public_activity 
            (event_type, event_data, city, state, venue_id, artist_id, gig_id, 
             ip_hash, user_agent, session_id, referrer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.event_type,
            event_data_json,
            event.city,
            event.state,
            event.venue_id,
            event.artist_id,
            event.gig_id,
            ip_hash,
            user_agent,
            event.session_id,
            referrer
        ))
        
        conn.commit()
        conn.close()
        
        return {"status": "ok"}
    
    except Exception as e:
        # Don't fail the request if tracking fails
        return {"status": "error", "message": str(e)}

@router.get("/stats/cities")
async def get_city_stats():
    """Get aggregated city search statistics (for admin/dashboard)"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT city, state, COUNT(*) as search_count,
               COUNT(DISTINCT ip_hash) as unique_visitors
        FROM public_activity
        WHERE event_type = 'city_search' AND city IS NOT NULL
        GROUP BY city, state
        ORDER BY search_count DESC
        LIMIT 50
    """)
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return results

@router.get("/stats/gigs")
async def get_gig_stats():
    """Get aggregated gig click statistics"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT gig_id, venue_id, COUNT(*) as click_count,
               COUNT(DISTINCT ip_hash) as unique_visitors
        FROM public_activity
        WHERE event_type = 'gig_click' AND gig_id IS NOT NULL
        GROUP BY gig_id
        ORDER BY click_count DESC
        LIMIT 50
    """)
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return results

@router.get("/stats/summary")
async def get_summary_stats():
    """Get overall activity summary"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Total events by type
    cursor.execute("""
        SELECT event_type, COUNT(*) as count
        FROM public_activity
        GROUP BY event_type
        ORDER BY count DESC
    """)
    events_by_type = {row['event_type']: row['count'] for row in cursor.fetchall()}
    
    # Unique visitors (by ip_hash)
    cursor.execute("""
        SELECT COUNT(DISTINCT ip_hash) as unique_visitors
        FROM public_activity
        WHERE ip_hash IS NOT NULL
    """)
    unique_visitors = cursor.fetchone()['unique_visitors']
    
    # Activity last 24 hours
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM public_activity
        WHERE created_at > datetime('now', '-1 day')
    """)
    last_24h = cursor.fetchone()['count']
    
    # Activity last 7 days
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM public_activity
        WHERE created_at > datetime('now', '-7 days')
    """)
    last_7d = cursor.fetchone()['count']
    
    # Activity last 30 days
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM public_activity
        WHERE created_at > datetime('now', '-30 days')
    """)
    last_30d = cursor.fetchone()['count']
    
    # Top cities last 7 days
    cursor.execute("""
        SELECT city, state, COUNT(*) as searches
        FROM public_activity
        WHERE event_type = 'city_search' 
          AND city IS NOT NULL
          AND created_at > datetime('now', '-7 days')
        GROUP BY city, state
        ORDER BY searches DESC
        LIMIT 10
    """)
    top_cities = [dict(row) for row in cursor.fetchall()]
    
    # Top searched artist types
    cursor.execute("""
        SELECT json_extract(event_data, '$.artist_types') as artist_types, COUNT(*) as count
        FROM public_activity
        WHERE event_type = 'filter_apply' 
          AND event_data IS NOT NULL
          AND created_at > datetime('now', '-30 days')
        GROUP BY artist_types
        ORDER BY count DESC
        LIMIT 10
    """)
    top_artist_types = [dict(row) for row in cursor.fetchall()]
    
    # Recent activity (last 20 events)
    cursor.execute("""
        SELECT event_type, city, state, created_at,
               json_extract(event_data, '$.venue_name') as venue_name,
               json_extract(event_data, '$.artist_search') as artist_search
        FROM public_activity
        ORDER BY created_at DESC
        LIMIT 20
    """)
    recent_activity = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return {
        "events_by_type": events_by_type,
        "unique_visitors": unique_visitors,
        "activity_last_24h": last_24h,
        "activity_last_7d": last_7d,
        "activity_last_30d": last_30d,
        "top_cities_7d": top_cities,
        "top_artist_types": top_artist_types,
        "recent_activity": recent_activity
    }

@router.get("/stats/details")
async def get_detail_events(event_type: Optional[str] = None, period: Optional[str] = None):
    """Get detailed events filtered by type or time period"""
    conn = get_db()
    cursor = conn.cursor()
    
    conditions = []
    params = []
    
    if event_type:
        conditions.append("pa.event_type = ?")
        params.append(event_type)
    
    if period == '24h':
        conditions.append("pa.created_at > datetime('now', '-1 day')")
    elif period == '7d':
        conditions.append("pa.created_at > datetime('now', '-7 days')")
    elif period == '30d':
        conditions.append("pa.created_at > datetime('now', '-30 days')")
    
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    
    cursor.execute(f"""
        SELECT pa.id, pa.event_type, pa.city, pa.state, pa.created_at, pa.ip_hash, pa.session_id,
               json_extract(pa.event_data, '$.venue_name') as venue_name,
               json_extract(pa.event_data, '$.artist_search') as artist_search,
               json_extract(pa.event_data, '$.artist_types') as artist_types,
               pa.user_agent, pa.referrer,
               json_extract(pa.event_data, '$.page') as page_name,
               a.name as artist_name
        FROM public_activity pa
        LEFT JOIN gigs g ON g.id = pa.gig_id
        LEFT JOIN artists a ON a.id = g.artist_id
        {where}
        ORDER BY pa.created_at DESC
        LIMIT 500
    """, params)
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return results

@router.get("/stats/visitors")
async def get_visitor_details():
    """Get unique visitor details with location info"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT ip_hash, 
               COUNT(*) as total_events,
               MIN(created_at) as first_seen,
               MAX(created_at) as last_seen,
               COUNT(DISTINCT event_type) as event_types,
               GROUP_CONCAT(DISTINCT event_type) as events,
               (SELECT pa2.city FROM public_activity pa2 WHERE pa2.ip_hash = public_activity.ip_hash AND pa2.city IS NOT NULL AND pa2.city != '' ORDER BY pa2.created_at DESC LIMIT 1) as city,
               (SELECT pa3.state FROM public_activity pa3 WHERE pa3.ip_hash = public_activity.ip_hash AND pa3.state IS NOT NULL AND pa3.state != '' ORDER BY pa3.created_at DESC LIMIT 1) as state
        FROM public_activity
        WHERE ip_hash IS NOT NULL
        GROUP BY ip_hash
        ORDER BY last_seen DESC
        LIMIT 200
    """)
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return results

@router.get("/stats/venue/{venue_id}")
async def get_venue_stats(venue_id: int):
    """Get analytics for a specific venue"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Total gig clicks for this venue
    cursor.execute("""
        SELECT COUNT(*) as total_clicks,
               COUNT(DISTINCT ip_hash) as unique_visitors
        FROM public_activity
        WHERE venue_id = ? AND event_type = 'gig_click'
    """, (venue_id,))
    row = cursor.fetchone()
    total_clicks = row['total_clicks']
    unique_visitors = row['unique_visitors']
    
    # Clicks last 7 days
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM public_activity
        WHERE venue_id = ? AND event_type = 'gig_click'
          AND created_at > datetime('now', '-7 days')
    """, (venue_id,))
    clicks_7d = cursor.fetchone()['count']
    
    # Clicks last 30 days
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM public_activity
        WHERE venue_id = ? AND event_type = 'gig_click'
          AND created_at > datetime('now', '-30 days')
    """, (venue_id,))
    clicks_30d = cursor.fetchone()['count']
    
    # Clicks by gig
    cursor.execute("""
        SELECT gig_id, 
               json_extract(event_data, '$.gig_date') as gig_date,
               json_extract(event_data, '$.gig_status') as gig_status,
               COUNT(*) as clicks,
               COUNT(DISTINCT ip_hash) as unique_clicks
        FROM public_activity
        WHERE venue_id = ? AND event_type = 'gig_click' AND gig_id IS NOT NULL
        GROUP BY gig_id
        ORDER BY clicks DESC
        LIMIT 20
    """, (venue_id,))
    clicks_by_gig = [dict(row) for row in cursor.fetchall()]
    
    # Cities where people searched and then clicked this venue's gigs
    cursor.execute("""
        SELECT city, state, COUNT(*) as count
        FROM public_activity
        WHERE venue_id = ? AND event_type = 'gig_click'
          AND city IS NOT NULL
        GROUP BY city, state
        ORDER BY count DESC
        LIMIT 10
    """, (venue_id,))
    visitor_cities = [dict(row) for row in cursor.fetchall()]
    
    # Recent clicks
    cursor.execute("""
        SELECT created_at, city, state,
               json_extract(event_data, '$.gig_date') as gig_date,
               json_extract(event_data, '$.gig_status') as gig_status
        FROM public_activity
        WHERE venue_id = ? AND event_type = 'gig_click'
        ORDER BY created_at DESC
        LIMIT 20
    """, (venue_id,))
    recent_clicks = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return {
        "total_clicks": total_clicks,
        "unique_visitors": unique_visitors,
        "clicks_last_7d": clicks_7d,
        "clicks_last_30d": clicks_30d,
        "clicks_by_gig": clicks_by_gig,
        "visitor_cities": visitor_cities,
        "recent_clicks": recent_clicks
    }

@router.get("/stats/artist/{artist_id}")
async def get_artist_stats(artist_id: int):
    """Get analytics for a specific artist"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Total gig clicks for booked gigs by this artist
    cursor.execute("""
        SELECT COUNT(*) as total_clicks,
               COUNT(DISTINCT ip_hash) as unique_visitors
        FROM public_activity
        WHERE artist_id = ? AND event_type = 'gig_click'
    """, (artist_id,))
    row = cursor.fetchone()
    total_clicks = row['total_clicks']
    unique_visitors = row['unique_visitors']
    
    # Clicks last 7 days
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM public_activity
        WHERE artist_id = ? AND event_type = 'gig_click'
          AND created_at > datetime('now', '-7 days')
    """, (artist_id,))
    clicks_7d = cursor.fetchone()['count']
    
    # Clicks last 30 days
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM public_activity
        WHERE artist_id = ? AND event_type = 'gig_click'
          AND created_at > datetime('now', '-30 days')
    """, (artist_id,))
    clicks_30d = cursor.fetchone()['count']
    
    # Clicks by gig
    cursor.execute("""
        SELECT gig_id,
               json_extract(event_data, '$.gig_date') as gig_date,
               json_extract(event_data, '$.venue_name') as venue_name,
               COUNT(*) as clicks,
               COUNT(DISTINCT ip_hash) as unique_clicks
        FROM public_activity
        WHERE artist_id = ? AND event_type = 'gig_click' AND gig_id IS NOT NULL
        GROUP BY gig_id
        ORDER BY clicks DESC
        LIMIT 20
    """, (artist_id,))
    clicks_by_gig = [dict(row) for row in cursor.fetchall()]
    
    # Cities where people are viewing this artist's booked gigs
    cursor.execute("""
        SELECT city, state, COUNT(*) as count
        FROM public_activity
        WHERE artist_id = ? AND event_type = 'gig_click'
          AND city IS NOT NULL
        GROUP BY city, state
        ORDER BY count DESC
        LIMIT 10
    """, (artist_id,))
    viewer_cities = [dict(row) for row in cursor.fetchall()]
    
    # Recent clicks
    cursor.execute("""
        SELECT created_at, city, state,
               json_extract(event_data, '$.gig_date') as gig_date,
               json_extract(event_data, '$.venue_name') as venue_name
        FROM public_activity
        WHERE artist_id = ? AND event_type = 'gig_click'
        ORDER BY created_at DESC
        LIMIT 20
    """, (artist_id,))
    recent_clicks = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return {
        "total_clicks": total_clicks,
        "unique_visitors": unique_visitors,
        "clicks_last_7d": clicks_7d,
        "clicks_last_30d": clicks_30d,
        "clicks_by_gig": clicks_by_gig,
        "viewer_cities": viewer_cities,
        "recent_clicks": recent_clicks
    }


@router.get("/stats/admin-dashboard")
async def get_admin_dashboard_stats(admin=Depends(check_admin)):
    """Comprehensive admin analytics dashboard — all site activity in one call.

    Audit fix (May 2026): was unauthenticated. Anyone on the internet could
    pull total revenue, every signup email, recent bookings with names, top
    venues, top artists. Public BI + PII leak. Now gated via check_admin.
    """
    conn = get_db()
    c = conn.cursor()

    def q(sql, params=()):
        try:
            c.execute(sql, params)
            row = c.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def qa(sql, params=()):
        try:
            c.execute(sql, params)
            return [dict(r) for r in c.fetchall()]
        except Exception:
            return []

    # ── Platform totals ──────────────────────────────────────────────
    total_users      = q("SELECT COUNT(*) FROM users")
    total_artists    = q("SELECT COUNT(*) FROM artists")
    total_venues     = q("SELECT COUNT(*) FROM venues")
    total_gigs       = q("SELECT COUNT(*) FROM gigs")
    open_gigs        = q("SELECT COUNT(*) FROM gigs WHERE status='open' AND artist_id IS NULL")
    booked_gigs      = q("SELECT COUNT(*) FROM gigs WHERE status='booked' OR artist_id IS NOT NULL")
    cancelled_gigs   = q("SELECT COUNT(*) FROM gigs WHERE status IN ('cancelled','payment_cancelled')")
    try:
        import pytz as _an_pytz
        _an_tz_str = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key='platform_timezone'")).scalar() or "America/Los_Angeles"
        _an_today = __import__('datetime').datetime.now(_an_pytz.timezone(_an_tz_str)).strftime("%Y-%m-%d")
    except Exception:
        _an_today = __import__('datetime').date.today().isoformat()
    past_gigs        = q(f"SELECT COUNT(*) FROM gigs WHERE date < '{_an_today}' AND (status='booked' OR artist_id IS NOT NULL)")
    upcoming_gigs    = q(f"SELECT COUNT(*) FROM gigs WHERE date >= '{_an_today}' AND (status='booked' OR artist_id IS NOT NULL)")

    # New this week / month
    new_users_7d     = q("SELECT COUNT(*) FROM users WHERE datetime(created_at,'localtime') > datetime('now','localtime','-7 days')")
    new_users_30d    = q("SELECT COUNT(*) FROM users WHERE datetime(created_at,'localtime') > datetime('now','localtime','-30 days')")
    new_artists_7d   = q("SELECT COUNT(*) FROM artists WHERE created_at > datetime('now','-7 days')")
    new_venues_7d    = q("SELECT COUNT(*) FROM venues WHERE created_at > datetime('now','-7 days')")
    new_gigs_7d      = q("SELECT COUNT(*) FROM gigs WHERE created_at > datetime('now','-7 days')")
    new_gigs_30d     = q("SELECT COUNT(*) FROM gigs WHERE created_at > datetime('now','-30 days')")
    bookings_7d      = q("SELECT COUNT(*) FROM gigs WHERE (status='booked' OR artist_id IS NOT NULL) AND created_at > datetime('now','-7 days')")
    bookings_30d     = q("SELECT COUNT(*) FROM gigs WHERE (status='booked' OR artist_id IS NOT NULL) AND created_at > datetime('now','-30 days')")

    # ── Revenue / Payments ───────────────────────────────────────────
    _pt = "AND COALESCE(transaction_type,'single') IN ('venue_charge','single')"  # parent rows only
    _cp = "AND COALESCE(transaction_type,'single') = 'artist_payout'"  # child rows for payouts
    total_revenue_cents      = q(f"SELECT COALESCE(SUM(venue_charge_cents),0) FROM transactions WHERE status IN ('paid','transferred','completed') {_pt}")
    total_payouts_cents      = q(f"SELECT COALESCE(SUM(artist_payout_cents),0) FROM transactions WHERE status IN ('paid','transferred','completed') {_cp}")
    total_commission_cents   = q(f"SELECT COALESCE(SUM(commission_cents),0) FROM transactions WHERE status IN ('paid','transferred','completed') {_pt}")
    pending_payments_cents   = q(f"SELECT COALESCE(SUM(venue_charge_cents),0) FROM transactions WHERE status IN ('pending','scheduled','charged') {_pt}")
    revenue_30d_cents        = q(f"SELECT COALESCE(SUM(venue_charge_cents),0) FROM transactions WHERE status IN ('paid','transferred','completed') AND created_at > datetime('now','-30 days') {_pt}")
    revenue_7d_cents         = q(f"SELECT COALESCE(SUM(venue_charge_cents),0) FROM transactions WHERE status IN ('paid','transferred','completed') AND created_at > datetime('now','-7 days') {_pt}")
    failed_payments          = q(f"SELECT COUNT(*) FROM transactions WHERE status IN ('failed','charge_failed') {_pt}")
    cancelled_payments       = q(f"SELECT COUNT(*) FROM transactions WHERE status IN ('cancelled','payment_cancelled') {_pt}")
    total_transactions       = q(f"SELECT COUNT(*) FROM transactions WHERE status IN ('paid','transferred','completed') {_pt}")

    # ── Contracts ────────────────────────────────────────────────────
    total_contracts          = q("SELECT COUNT(*) FROM gig_contracts")
    signed_contracts         = q("SELECT COUNT(*) FROM gig_contracts WHERE artist_signature_date IS NOT NULL AND venue_signature_date IS NOT NULL")
    pending_contracts        = q("SELECT COUNT(*) FROM gig_contracts WHERE artist_signature_date IS NOT NULL AND venue_signature_date IS NULL")

    # ── Support Tickets ──────────────────────────────────────────────
    open_tickets             = q("SELECT COUNT(*) FROM support_tickets WHERE status='open' OR status IS NULL")
    closed_tickets           = q("SELECT COUNT(*) FROM support_tickets WHERE status='closed'")
    tickets_7d               = q("SELECT COUNT(*) FROM support_tickets WHERE created_at > datetime('now','-7 days')")

    # ── Preferred Artists ────────────────────────────────────────────
    preferred_pairs          = q("SELECT COUNT(*) FROM preferred_artists WHERE status='approved'")
    pending_preferred        = q("SELECT COUNT(*) FROM preferred_artists WHERE status IN ('pending','scheduled','charged')")

    # ── Waitlist ─────────────────────────────────────────────────────
    active_waitlist          = q("SELECT COUNT(*) FROM gig_waitlist WHERE offer_declined=0 OR offer_declined IS NULL")

    # ── Public activity ──────────────────────────────────────────────
    unique_visitors          = q("SELECT COUNT(DISTINCT ip_hash) FROM public_activity WHERE ip_hash IS NOT NULL")
    activity_24h             = q("SELECT COUNT(*) FROM public_activity WHERE created_at > datetime('now','-1 day')")
    activity_7d              = q("SELECT COUNT(*) FROM public_activity WHERE created_at > datetime('now','-7 days')")
    activity_30d             = q("SELECT COUNT(*) FROM public_activity WHERE created_at > datetime('now','-30 days')")
    gig_clicks_total         = q("SELECT COUNT(*) FROM public_activity WHERE event_type='gig_click'")
    gig_clicks_7d            = q("SELECT COUNT(*) FROM public_activity WHERE event_type='gig_click' AND created_at > datetime('now','-7 days')")

    # ── Top lists ────────────────────────────────────────────────────
    top_venues_booked = qa("""
        SELECT v.venue_name, COUNT(g.id) as bookings
        FROM gigs g JOIN venues v ON g.venue_id=v.id
        WHERE g.artist_id IS NOT NULL
        GROUP BY v.id ORDER BY bookings DESC LIMIT 8
    """)
    top_artists_booked = qa("""
        SELECT a.name, COUNT(g.id) as bookings
        FROM gigs g JOIN artists a ON g.artist_id=a.id
        GROUP BY a.id ORDER BY bookings DESC LIMIT 8
    """)
    top_cities = qa("""
        SELECT city, state, COUNT(*) as searches
        FROM public_activity WHERE event_type='city_search' AND city IS NOT NULL
          AND created_at > datetime('now','-30 days')
        GROUP BY city, state ORDER BY searches DESC LIMIT 8
    """)
    recent_bookings = qa("""
        SELECT g.id, g.date, g.pay, a.name as artist_name, v.venue_name
        FROM gigs g
        JOIN artists a ON g.artist_id=a.id
        JOIN venues v ON g.venue_id=v.id
        WHERE g.artist_id IS NOT NULL
        ORDER BY g.created_at DESC LIMIT 10
    """)
    recent_signups = qa("""
        SELECT id, email, created_at,
               CASE WHEN EXISTS(SELECT 1 FROM artists WHERE user_id=users.id) THEN 'artist'
                    WHEN EXISTS(SELECT 1 FROM venues WHERE user_id=users.id) THEN 'venue'
                    ELSE 'user' END as role
        FROM users ORDER BY created_at DESC LIMIT 10
    """)
    events_by_type = qa("""
        SELECT event_type, COUNT(*) as count FROM public_activity
        GROUP BY event_type ORDER BY count DESC
    """)
    gigs_by_status = qa("""
        SELECT COALESCE(status,'open') as status, COUNT(*) as count
        FROM gigs GROUP BY status ORDER BY count DESC
    """)
    revenue_by_month = qa("""
        SELECT strftime('%Y-%m', datetime(created_at,'localtime')) as month,
               ROUND(SUM(venue_charge_cents)/100.0,2) as total
        FROM transactions WHERE status IN ('paid','transferred','completed')
          AND COALESCE(transaction_type,'single') IN ('venue_charge','single')
          AND created_at > datetime('now','-12 months')
        GROUP BY month ORDER BY month ASC
    """)

    conn.close()

    return {
        # Totals
        "total_users": total_users, "total_artists": total_artists,
        "total_venues": total_venues, "total_gigs": total_gigs,
        "open_gigs": open_gigs, "booked_gigs": booked_gigs,
        "cancelled_gigs": cancelled_gigs, "past_gigs": past_gigs,
        "upcoming_gigs": upcoming_gigs,
        # Growth
        "new_users_7d": new_users_7d, "new_users_30d": new_users_30d,
        "new_artists_7d": new_artists_7d, "new_venues_7d": new_venues_7d,
        "new_gigs_7d": new_gigs_7d, "new_gigs_30d": new_gigs_30d,
        "bookings_7d": bookings_7d, "bookings_30d": bookings_30d,
        # Revenue (in dollars)
        "total_revenue": round(total_revenue_cents / 100, 2),
        "total_payouts": round(total_payouts_cents / 100, 2),
        "total_commission": round(total_commission_cents / 100, 2),
        "pending_payments": round(pending_payments_cents / 100, 2),
        "revenue_7d": round(revenue_7d_cents / 100, 2),
        "revenue_30d": round(revenue_30d_cents / 100, 2),
        "failed_payments": failed_payments, "cancelled_payments": cancelled_payments,
        "total_transactions": total_transactions,
        # Engagement
        "total_contracts": total_contracts, "signed_contracts": signed_contracts,
        "pending_contracts": pending_contracts,
        "open_tickets": open_tickets, "closed_tickets": closed_tickets,
        "tickets_7d": tickets_7d,
        "preferred_pairs": preferred_pairs, "pending_preferred": pending_preferred,
        "active_waitlist": active_waitlist,
        # Public traffic
        "unique_visitors": unique_visitors,
        "activity_24h": activity_24h, "activity_7d": activity_7d, "activity_30d": activity_30d,
        "gig_clicks_total": gig_clicks_total, "gig_clicks_7d": gig_clicks_7d,
        # Lists
        "top_venues_booked": top_venues_booked,
        "top_artists_booked": top_artists_booked,
        "top_cities": top_cities,
        "recent_bookings": recent_bookings,
        "recent_signups": recent_signups,
        "events_by_type": events_by_type,
        "gigs_by_status": gigs_by_status,
        "revenue_by_month": revenue_by_month,
    }
