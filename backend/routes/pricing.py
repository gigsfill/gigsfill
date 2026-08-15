"""
Public pricing endpoint — surfaces the platform fee settings so the
marketing homepage (and any other public page) can render current
pricing without hard-coding it.

Aug 12 2026: added so the "For Artists" / "For Venues" homepage
modals can pull the actual admin-configured fee % / min fee, instead
of the pre-launch hard-coded "5%" that would silently drift if admin
changed the platform_fee_percent in Admin → Platform Settings.

Endpoint is intentionally unauthenticated + cheap (single 3-row
platform_settings SELECT, no per-user data). Response is safe to
cache aggressively client-side.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from backend.db import get_db
from backend.rate_limiter import limiter

router = APIRouter()


@router.get("/api/pricing")
@limiter.limit("120/minute")
def get_public_pricing(request: Request, db=Depends(get_db)):
    """Return the current platform fee settings + per-side computed
    breakdown so the homepage doesn't have to duplicate the fee-split
    math. Keeps display in perfect sync with the payout math in
    routes/stripe_connect.py / payout_scheduler.py.

    Response shape:
      {
        "fee_percent":         10,           // total platform fee %
        "min_fee_dollars":     10.0,         // total minimum fee $
        "fee_split":           "split",      // "split" | "venue_only" | "artist_only"
        "venue_fee_percent":   5,            // what the venue actually pays
        "artist_fee_percent":  5,            // what the artist actually pays
        "venue_min_fee_dollars": 5.0,
        "artist_min_fee_dollars": 5.0,
      }
    """
    settings = {}
    try:
        for r in db.execute(text(
            "SELECT setting_key, setting_value FROM platform_settings "
            "WHERE setting_key IN ('platform_fee_percent', "
            "'platform_fee_split', 'platform_min_fee')"
        )).fetchall():
            settings[r[0]] = r[1]
    except Exception:
        pass

    def _f(k, default):
        try:
            return float(settings.get(k, default))
        except Exception:
            return default

    fee_percent = _f("platform_fee_percent", 10.0)
    min_fee_dollars = _f("platform_min_fee", 0.0)
    fee_split = settings.get("platform_fee_split") or "split"

    # Same math the payout scheduler uses. Kept in sync so what the
    # marketing page shows matches what the artist/venue actually pays.
    if fee_split == "venue_only":
        venue_pct = fee_percent
        artist_pct = 0.0
        venue_min = min_fee_dollars
        artist_min = 0.0
    elif fee_split == "artist_only":
        venue_pct = 0.0
        artist_pct = fee_percent
        venue_min = 0.0
        artist_min = min_fee_dollars
    else:  # 'split' (default) — half each, integer division on cents matches production
        venue_pct = fee_percent / 2.0
        artist_pct = fee_percent - venue_pct
        # min-fee split mirrors the cents-based integer split
        # payout_scheduler does: venue = min//2, artist = min - venue
        _min_cents = int(round(min_fee_dollars * 100))
        _v_min_cents = _min_cents // 2
        _a_min_cents = _min_cents - _v_min_cents
        venue_min = _v_min_cents / 100.0
        artist_min = _a_min_cents / 100.0

    return {
        "fee_percent":            fee_percent,
        "min_fee_dollars":        min_fee_dollars,
        "fee_split":              fee_split,
        "venue_fee_percent":      venue_pct,
        "artist_fee_percent":     artist_pct,
        "venue_min_fee_dollars":  venue_min,
        "artist_min_fee_dollars": artist_min,
    }
