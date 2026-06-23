"""
GET /api/gigs/{gig_id}/modal-data?viewer_type=artist&viewer_id=3
GET /api/gigs/{gig_id}/modal-data?viewer_type=venue&viewer_id=1

Returns everything needed to render the unified gig modal in a single call.
No more N+1 fetches from the frontend.
"""
import logging
from datetime import datetime, timedelta, timezone
from backend.utils import utcnow_naive
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from backend.routes.auth import get_current_user
from backend.db import get_db
from backend.services.email_dispatch import format_pay_summary_with_sign, slot_has_door_terms

logger = logging.getLogger("gigsfill.gig_modal")
router = APIRouter(tags=["gig_modal"])


def _fmt_time(t):
    """Convert HH:MM to 12-hour display string."""
    if not t:
        return ""
    try:
        h, m = str(t)[:5].split(":")
        h, m = int(h), int(m)
        suffix = "AM" if h < 12 else "PM"
        h = h % 12 or 12
        return f"{h}:{m:02d} {suffix}"
    except Exception:
        return str(t)


def _slot_status_for_viewer(slot, viewer_type, viewer_id, contract_rows,
                             preferred_status, is_banned, freq_check,
                             waitlist_status, gig_is_blast_open, gig_freq_exempt,
                             has_active_waitlist, my_slots):
    """
    Compute the viewer's relationship to a single slot.
    Returns a dict with everything the modal row needs.
    """
    sid = slot["id"]
    s_status = slot["status"]       # open | booked | pending_contract | awaiting_venue_contract | pending_venue_approval
    s_artist = slot.get("artist_id")
    is_my_slot = (viewer_type == "artist" and s_artist and int(s_artist) == int(viewer_id))
    is_my_venue = (viewer_type == "venue")

    # Contract info for this slot
    contract = next((c for c in contract_rows if c.get("slot_id") == sid or
                     (not c.get("slot_id") and s_artist and c.get("artist_id") == s_artist)), None)
    if not contract:
        # Fall back to any contract for this gig+artist
        contract = next((c for c in contract_rows if s_artist and c.get("artist_id") == s_artist), None)

    contract_status = contract["status"] if contract else None
    contract_id     = contract["id"]     if contract else None
    contract_pdf    = contract.get("pdf_file_path") or contract.get("signed_pdf_path") if contract else None
    contract_type   = contract.get("contract_type") if contract else None
    hold_expires    = contract.get("hold_expires_at") if contract else None
    artist_sig_name = contract.get("artist_signature_name") if contract else None
    artist_sig_date = contract.get("artist_signature_date") if contract else None
    contract_body   = contract.get("rendered_body") or contract.get("contract_body", "") if contract else ""

    # Slot timing
    slotStarted = False
    slotEnded   = False

    # Determine viewer_relationship for this slot
    if is_my_slot or (is_my_venue and s_status in ("pending_contract", "awaiting_venue_contract", "booked", "pending_venue_approval")):
        if s_status == "booked":
            relationship = "mine_booked" if is_my_slot else "venue_booked"
        elif s_status == "pending_contract":
            relationship = "mine_pending_contract" if is_my_slot else "venue_pending_contract"
        elif s_status == "awaiting_venue_contract":
            # Bug fix (Jun 2026): `awaiting_venue_contract` can mean two
            # very different things depending on contract_type:
            #   - pdf_upload      → artist downloaded, signed, uploaded
            #     back; venue must now upload their countersigned PDF
            #     (the legacy "venue_awaiting_upload" UI).
            #   - auto_generated / custom_builder → artist clicked Sign
            #     on the digital contract; venue must COUNTERSIGN with
            #     ONE click (no PDF upload involved).
            # Routing both to venue_awaiting_upload caused the gig modal
            # to show "Upload Contract PDF" for digital contracts the
            # artist had already signed — confusing and wrong.
            if contract_type == "pdf_upload":
                relationship = "mine_awaiting_venue" if is_my_slot else "venue_awaiting_upload"
            else:
                # Digital flow — countersign UI lives inside the
                # venue_pending_contract branch (gig-modal.js renders
                # _countersignBlock when contract_status == 'artist_signed').
                relationship = "mine_awaiting_venue" if is_my_slot else "venue_pending_contract"
        elif s_status == "pending_venue_approval":
            relationship = "mine_pending_approval" if is_my_slot else "venue_pending_approval"
        else:
            relationship = "mine_open"
    elif s_status == "open":
        if viewer_type == "venue":
            relationship = "open"
        elif is_banned:
            relationship = "banned"
        elif preferred_status == "pending":
            relationship = "preferred_pending"
        elif preferred_status in ("revoked", "denied") and not gig_is_blast_open:
            relationship = "preferred_denied"
        elif preferred_status not in ("approved", None) and preferred_status is not None and preferred_status != "pending":
            relationship = "no_access"
        elif preferred_status is None and not gig_is_blast_open:
            # Audit fix (May 2026 part 10g): frequency_exempt was previously
            # part of this condition (`and not gig_freq_exempt`), which let
            # NON-PREFERRED artists book any frequency-exempt gig. But
            # frequency_exempt only waives the booking-FREQUENCY limit — it
            # must NOT bypass the preferred-status requirement. Only a valid
            # blast (gig_is_blast_open) opens a gig to non-preferred artists.
            relationship = "not_preferred"
        elif freq_check and freq_check.get("blocked") and not gig_is_blast_open and not gig_freq_exempt:
            relationship = "freq_blocked"
        else:
            # Check slot type match
            artist_type = slot.get("artist_type") or ""
            viewer_artist_type = freq_check.get("artist_type", "") if isinstance(freq_check, dict) else ""
            relationship = "open_bookable"
    elif s_status in ("booked", "pending_contract", "awaiting_venue_contract"):
        relationship = "other_booked"
    elif s_status == "pending_venue_approval":
        relationship = "other_pending_approval"
    else:
        relationship = "open"

    # Already have a different slot on this gig
    already_have_slot = viewer_type == "artist" and any(
        ms.get("artist_id") and int(ms["artist_id"]) == int(viewer_id) and ms.get("id") != sid
        for ms in my_slots
    )
    if relationship == "open_bookable" and already_have_slot:
        relationship = "already_have_slot"

    return {
        "id":               sid,
        "slot_number":      slot.get("slot_number"),
        "start_time":       slot.get("start_time"),
        "end_time":         slot.get("end_time"),
        "start_time_fmt":   _fmt_time(slot.get("start_time")),
        "end_time_fmt":     _fmt_time(slot.get("end_time")),
        "pay":              slot.get("pay"),
        # Door-deal fields surfaced so the modal can render "$X guarantee
        # + Y% of door" — without these the artist sees only the flat
        # guarantee on the booking-decision screen and misses the upside.
        "deal_type":        slot.get("deal_type"),
        "door_pct":         slot.get("door_pct"),
        "guarantee_cents":  slot.get("guarantee_cents"),
        "pay_summary":      format_pay_summary_with_sign(slot),
        "is_door_deal":     slot_has_door_terms(slot),
        "status":           s_status,
        "artist_id":        s_artist,
        "artist_name":      slot.get("artist_name"),
        "artist_type":      slot.get("artist_type"),
        "relationship":     relationship,
        "is_my_slot":       is_my_slot,
        "contract_id":      contract_id,
        "contract_status":  contract_status,
        "contract_type":    contract_type,
        "contract_pdf_url": contract_pdf,
        "contract_body":    contract_body,
        "artist_sig_name":  artist_sig_name,
        "artist_sig_date":  artist_sig_date,
        "hold_expires_at":  hold_expires,
    }


@router.get("/api/gigs/{gig_id}/modal-data")
def get_gig_modal_data(
    gig_id: int,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Single endpoint that returns everything needed to render the gig modal.
    viewer_type: 'artist' or 'venue'
    viewer_id:   artist_id or venue_id
    """
    viewer_type = request.query_params.get("viewer_type", "artist")
    viewer_id   = request.query_params.get("viewer_id")
    if not viewer_id:
        raise HTTPException(400, "viewer_id required")
    viewer_id = int(viewer_id)

    # ── Auth check ────────────────────────────────────────────────────────
    if viewer_type == "artist":
        ok = db.execute(text("""
            SELECT 1 FROM artists a
            WHERE a.id = :aid AND (a.user_id = :uid
              OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid))
        """), {"aid": viewer_id, "uid": user.id}).first()
        if not ok:
            raise HTTPException(403, "Not your artist")
    else:
        ok = db.execute(text("""
            SELECT 1 FROM venues v
            WHERE v.id = :vid AND (v.user_id = :uid
              OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid))
        """), {"vid": viewer_id, "uid": user.id}).first()
        if not ok:
            raise HTTPException(403, "Not your venue")

    # ── Load gig ──────────────────────────────────────────────────────────
    gig = db.execute(text("""
        SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.title, g.notes,
               g.status, g.artist_id, g.venue_id, g.artist_type, g.band_formats, g.styles,
               g.is_multi_slot, g.frequency_exempt,
               g.radius_blast_token, g.contract_hold_artist_id, g.contract_hold_expires_at,
               g.recurring_group_id, g.is_recurring,
               CASE WHEN g.radius_blast_token IS NOT NULL AND g.status='open' THEN 1 ELSE 0 END as is_blast_open,
               -- has_active_waitlist excludes hold-source rows (Jun 2026).
               -- The legacy gig_modal 'Booking Locked' UI keyed off this
               -- and was firing for held gigs incorrectly.
               CASE WHEN (
                 EXISTS (SELECT 1 FROM gig_waitlist wl WHERE wl.gig_id=g.id AND wl.offer_sent=1
                   AND (wl.offer_declined=0 OR wl.offer_declined IS NULL)
                   AND (wl.offer_expires_at IS NULL OR wl.offer_expires_at > datetime('now'))
                   AND (wl.source IS NULL OR wl.source = 'cancellation'))
                 OR EXISTS (SELECT 1 FROM waitlist_offered wo WHERE wo.gig_id=g.id AND wo.offer_expires_at > datetime('now'))
               ) THEN 1 ELSE 0 END as has_active_waitlist,
               v.venue_name, v.address_line_1, v.address_line_2, v.city, v.state,
               v.latitude as venue_lat, v.longitude as venue_lon,
               v.has_stage, v.has_sound_equipment, v.has_lighting,
               a.name as artist_name
        FROM gigs g
        JOIN venues v ON v.id = g.venue_id
        LEFT JOIN artists a ON a.id = g.artist_id
        WHERE g.id = :gid
    """), {"gid": gig_id}).mappings().first()

    if not gig:
        raise HTTPException(404, "Gig not found")
    gig = dict(gig)

    # ── Load slots ────────────────────────────────────────────────────────
    # Door-deal columns included so the gig modal can render the same
    # "$X guarantee + Y% of door" terms an artist sees in the booking email
    # / contract / dashboard. Enriched with pay_summary below.
    slot_rows = db.execute(text("""
        SELECT gs.id, gs.slot_number, gs.start_time, gs.end_time, gs.pay,
               gs.status, gs.artist_id, gs.artist_type, gs.band_formats, gs.styles,
               gs.deal_type, gs.door_pct, gs.guarantee_cents,
               gs.door_receipts_cents, gs.settled_pay_cents, gs.settled_at,
               a.name as artist_name
        FROM gig_slots gs
        LEFT JOIN artists a ON a.id = gs.artist_id
        WHERE gs.gig_id = :gid
        ORDER BY gs.slot_number ASC
    """), {"gid": gig_id}).mappings().all()
    slots = [dict(s) for s in slot_rows]

    # GO-LIVE FIX (May 15 2026): apply per-artist pay override on the slot's
    # `pay` value when the viewer is an artist who has an override at this
    # venue. Same logic the new-gig blast email uses (and that booking time
    # uses via _get_effective_pay). Without this the modal showed the raw
    # published pay (e.g. $10) while the email correctly showed the override
    # ($20) — confusing for the artist when deciding whether to book.
    if viewer_type == "artist" and viewer_id:
        try:
            _ov = db.execute(text(
                "SELECT pay_dollars_override, pay_cents_override "
                "FROM preferred_artists "
                "WHERE venue_id = :vid AND artist_id = :aid AND status = 'approved'"
            ), {"vid": gig["venue_id"], "aid": viewer_id}).mappings().first()
            if _ov and _ov.get("pay_dollars_override") is not None:
                _override_pay = float(_ov["pay_dollars_override"]) + float(_ov.get("pay_cents_override") or 0) / 100
                for _s in slots:
                    _slot_pay = float(_s.get("pay") or 0)
                    if _override_pay > _slot_pay:
                        _s["pay"] = _override_pay
        except Exception:
            # Non-fatal — fall back to published pay
            pass

    # ── Load contracts for this gig ───────────────────────────────────────
    contract_rows = db.execute(text("""
        SELECT gc.id, gc.artist_id, gc.contract_type, gc.status,
               gc.rendered_body,
               gc.artist_signature_name, gc.artist_signature_date,
               gc.hold_expires_at, gc.signed_pdf_path, gc.pdf_file_path
        FROM gig_contracts gc
        WHERE gc.gig_id = :gid
        ORDER BY gc.id DESC
    """), {"gid": gig_id}).mappings().all()
    contracts = [dict(c) for c in contract_rows]

    # ── Artist-specific checks ────────────────────────────────────────────
    preferred_status = None
    is_banned        = False
    freq_check       = None
    waitlist_status  = {"on_waitlist": False, "position": None, "total": 0, "has_offer": False}
    artist_data      = None
    my_slots         = []
    venue_contract_required = False
    venue_contract_type     = None

    if viewer_type == "artist":
        # Artist info
        a_row = db.execute(text("""
            SELECT a.id, a.name, a.artist_type, a.band_formats, a.styles,
                   a.city, a.state, a.latitude, a.longitude
            FROM artists a WHERE a.id = :aid
        """), {"aid": viewer_id}).mappings().first()
        if a_row:
            artist_data = dict(a_row)

        # Ban check
        ban = db.execute(text(
            "SELECT 1 FROM venue_artist_bans WHERE venue_id=:vid AND artist_id=:aid"),
            {"vid": gig["venue_id"], "aid": viewer_id}).first()
        is_banned = bool(ban)

        # Preferred status
        if not is_banned:
            pref = db.execute(text(
                "SELECT status FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid"),
                {"vid": gig["venue_id"], "aid": viewer_id}).mappings().first()
            preferred_status = pref["status"] if pref else None

        # Frequency check
        # FIX (May 2026): the prior code read v.artist_frequency_days only and
        # then fetched pa.pay_dollars_override (the WRONG column) into a variable
        # that was never used. Result: per-artist frequency overrides set by the
        # venue (e.g. override=0 to allow unlimited bookings) were ignored —
        # artists saw the venue-default-based "Frequency Limitation" banner
        # despite having an override that should have unblocked them.
        # Now uses the same COALESCE pattern as book_gig at routes/gigs.py:1095.
        _freq_row = db.execute(text("""
            SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days) as freq_days
            FROM preferred_artists pa
            JOIN venues v ON v.id = pa.venue_id
            WHERE pa.venue_id = :vid AND pa.artist_id = :aid
        """), {"vid": gig["venue_id"], "aid": viewer_id}).mappings().first()
        if _freq_row:
            freq_days = _freq_row["freq_days"]
        else:
            # Artist isn't on the preferred list at this venue — fall back to venue default
            freq_days = db.execute(text(
                "SELECT artist_frequency_days FROM venues WHERE id=:vid"),
                {"vid": gig["venue_id"]}).scalar()

        # Check if blast window waives frequency (same logic as frontend isGigBlockedByFrequency)
        _blast_waives_freq = bool(gig.get("frequency_exempt")) or bool(gig.get("radius_blast_token"))
        if not _blast_waives_freq:
            # Check venue blast notification windows
            try:
                _blast_settings = db.execute(text("""
                    SELECT notification_key, time_value, time_unit, enabled, blast_all_enabled
                    FROM venue_email_notifications
                    WHERE venue_id=:vid AND notification_key IN ('open_gig_36h','open_gig_1w')
                      AND enabled=1
                """), {"vid": gig["venue_id"]}).mappings().all()
                _gig_date = datetime.strptime(str(gig["date"])[:10], "%Y-%m-%d").date()
                _today = utcnow_naive().date()
                _days_until = (_gig_date - _today).days
                for _bs in _blast_settings:
                    _tv = _bs["time_value"] or 1
                    _tu = _bs["time_unit"] or "weeks"
                    _window = _tv / 24 if _tu == "hours" else (_tv if _tu == "days" else _tv * 7)
                    if 0 <= _days_until <= _window:
                        _blast_waives_freq = True
                        break
            except Exception:
                pass

        if not _blast_waives_freq and freq_days and int(freq_days) > 0:
            # Find closest booked gig at same venue
            booked_at_venue = db.execute(text("""
                SELECT g2.date FROM gigs g2
                JOIN gig_slots gs2 ON gs2.gig_id = g2.id
                WHERE g2.venue_id = :vid AND gs2.artist_id = :aid
                  -- Audit fix (May 2026 part 6): include awaiting_venue_contract + pending_venue_approval
                  AND gs2.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                  AND g2.id != :gid
                ORDER BY ABS(julianday(g2.date) - julianday(:date)) ASC
                LIMIT 1
            """), {"vid": gig["venue_id"], "aid": viewer_id,
                   "gid": gig_id, "date": gig["date"]}).mappings().first()
            if booked_at_venue:
                try:
                    d1 = datetime.strptime(str(gig["date"])[:10], "%Y-%m-%d").date()
                    d2 = datetime.strptime(str(booked_at_venue["date"])[:10], "%Y-%m-%d").date()
                    diff = (d1 - d2).days
                    if abs(diff) <= int(freq_days):
                        freq_check = {
                            "blocked": True,
                            "lastGigDate": str(booked_at_venue["date"])[:10],
                            "daysBetween": diff,
                            "absDaysBetween": abs(diff),
                            "daysRequired": int(freq_days),
                            "isBeforeBookedGig": diff < 0,
                            "artist_type": artist_data["artist_type"] if artist_data else "",
                        }
                except Exception:
                    pass

        # Waitlist status — exclude hold-source rows (Jun 2026). Hold
        # offers are surfaced via the Pending Offers banner at the top
        # of artist-book-gigs and shouldn't trigger the OLD
        # 'You're next on the waitlist' / 'Not Available' UX in the
        # gig modal. (User report: clicking 'Not Available' on a held
        # gig declined it under confusing legacy labeling.)
        wl_row = db.execute(text("""
            SELECT id, offer_sent, offer_declined, offer_expires_at
            FROM gig_waitlist WHERE gig_id=:gid AND artist_id=:aid
            AND (offer_declined=0 OR offer_declined IS NULL)
            AND (source IS NULL OR source = 'cancellation')
        """), {"gid": gig_id, "aid": viewer_id}).mappings().first()

        offered_row = db.execute(text("""
            SELECT offer_expires_at FROM waitlist_offered
            WHERE gig_id=:gid AND artist_id=:aid AND offer_expires_at > datetime('now')
        """), {"gid": gig_id, "aid": viewer_id}).mappings().first()

        if wl_row or offered_row:
            wl_total = db.execute(text(
                "SELECT COUNT(*) FROM gig_waitlist WHERE gig_id=:gid AND (offer_declined=0 OR offer_declined IS NULL)"),
                {"gid": gig_id}).scalar() or 1
            has_offer = bool(offered_row) or bool(wl_row and wl_row.get("offer_sent"))
            wl_expires = str(offered_row["offer_expires_at"]) if offered_row else (
                str(wl_row["offer_expires_at"]) if wl_row and wl_row.get("offer_expires_at") else None)
            # Compute actual queue position
            wl_position = None
            if wl_row:
                try:
                    wl_position = db.execute(text(
                        """SELECT COUNT(*) FROM gig_waitlist
                           WHERE gig_id=:gid AND id <= :wid
                             AND (offer_declined=0 OR offer_declined IS NULL)"""),
                        {"gid": gig_id, "wid": wl_row["id"]}).scalar() or 1
                except Exception:
                    wl_position = 1
            elif offered_row:
                wl_position = 1
            waitlist_status = {
                "on_waitlist": True,
                "has_offer": has_offer,
                "offer_expires_at": wl_expires,
                "position": wl_position,
                "total": wl_total,
            }

        # My slots on this gig
        my_slots = [s for s in slots if s.get("artist_id") == viewer_id]

        # Hold-feature state for the gig modal (Jun 2026). The artist
        # gig modal needs to know:
        #   - Is this a held gig?
        #   - What's my position in the hold waitlist?
        #   - If I'm the current offer holder, my token + expiry +
        #     filtered list of slots I can actually fill.
        # The frontend renders 'Pending Offer' + Book/Decline per slot
        # for current_offer state; 'in process' notice for queued; nothing
        # special for accepted/declined (normal flow).
        hold_info = {"is_held": False}
        if gig.get("hold_status") in ("active", "exhausted"):
            from backend.services.gig_hold import (
                _list_matching_open_slots as _hl, _fmt_time as _hfmt,
            )
            wl_hold = db.execute(text("""
                SELECT offer_sent, offer_declined, offer_token,
                       offer_expires_at, position
                FROM gig_waitlist
                WHERE gig_id = :gid AND artist_id = :aid AND source = 'hold'
                LIMIT 1
            """), {"gid": gig_id, "aid": viewer_id}).mappings().first()
            my_state = None
            if wl_hold:
                # accepted = booked into a slot already
                if any(s.get("artist_id") == viewer_id and s.get("status") == "booked" for s in slots):
                    my_state = "accepted"
                elif wl_hold["offer_declined"]:
                    my_state = "declined"
                elif wl_hold["offer_sent"]:
                    my_state = "current_offer"
                else:
                    my_state = "queued"
            hold_info = {
                "is_held": True,
                "hold_status": gig["hold_status"],
                "my_state": my_state,
                "my_position": int(wl_hold["position"]) if wl_hold and wl_hold.get("position") is not None else None,
                "offer_token": wl_hold["offer_token"] if (wl_hold and my_state == "current_offer") else None,
                "offer_expires_at": str(wl_hold["offer_expires_at"]) if (wl_hold and my_state == "current_offer") else None,
            }
            # Compute hours_remaining for current_offer
            if my_state == "current_offer" and hold_info["offer_expires_at"]:
                try:
                    from datetime import datetime as _dt
                    _exp = _dt.strptime(
                        hold_info["offer_expires_at"].replace("T", " ").split(".")[0],
                        "%Y-%m-%d %H:%M:%S",
                    )
                    _hr = max(0, round((_exp - _dt.utcnow()).total_seconds() / 3600, 1))
                    hold_info["hours_remaining"] = _hr
                except Exception:
                    hold_info["hours_remaining"] = None
                # Matching open slots for THIS artist
                _matching = _hl(db, gig_id, viewer_id)
                hold_info["my_matching_slots"] = [
                    {
                        "id": int(s["id"]),
                        "slot_number": s["slot_number"],
                        "time": f"{_hfmt(s['start_time'])} – {_hfmt(s['end_time'])}",
                        "pay": s["pay"],
                        "artist_type": s["artist_type"],
                    } for s in _matching
                ]

        # Venue contract requirement
        vc = db.execute(text("""
            SELECT contract_type, require_for_booking
            FROM venue_contracts WHERE venue_id=:vid AND is_active=1 LIMIT 1
        """), {"vid": gig["venue_id"]}).mappings().first()
        if vc:
            venue_contract_required = bool(vc["require_for_booking"])
            venue_contract_type     = vc["contract_type"]

    # ── Build per-slot viewer context ─────────────────────────────────────
    gig_is_blast_open = bool(gig.get("is_blast_open"))
    gig_freq_exempt   = bool(gig.get("frequency_exempt"))
    has_wl            = bool(gig.get("has_active_waitlist"))

    # Audit fix (May 2026 part 10g): the modal previously only treated a gig as
    # open-to-non-preferred when it had a live radius_blast_token (cancellation
    # blast). It missed the venue's open-gig "blast all nearby artists within
    # 36h/1week" feature, which is the SAME bypass the booking backend honors
    # via _open_blast_bypass_active. Without this, a non-preferred artist who
    # received the open-gig blast email saw "request preferred status" while
    # the backend would have happily accepted their booking. Fold the backend's
    # authoritative check into the modal so the two never disagree.
    try:
        from backend.routes.gigs import _open_blast_bypass_active
        if not gig_is_blast_open and _open_blast_bypass_active(db, gig["venue_id"], gig_id):
            gig_is_blast_open = True
    except Exception:
        pass

    slot_data = []
    for slot in slots:
        sd = _slot_status_for_viewer(
            slot, viewer_type, viewer_id, contracts,
            preferred_status, is_banned, freq_check,
            waitlist_status, gig_is_blast_open, gig_freq_exempt,
            has_wl, my_slots
        )
        slot_data.append(sd)

    # ── Gig-level status for header ───────────────────────────────────────
    # "gig_state" summarises the overall state for the viewer
    all_open      = all(s["status"] == "open" for s in slots)
    any_mine      = any(s["is_my_slot"] for s in slot_data)
    any_pending   = any(s["status"] in ("pending_contract","awaiting_venue_contract") for s in slots)
    any_booked    = any(s["status"] == "booked" for s in slots)
    all_booked    = all(s["status"] == "booked" for s in slots)

    if viewer_type == "artist":
        if any_mine:
            my_slot = next(s for s in slot_data if s["is_my_slot"])
            if my_slot["status"] == "booked":
                gig_state = "my_booked"
            elif my_slot["status"] in ("pending_contract", "awaiting_venue_contract"):
                gig_state = "my_pending_contract"
            elif my_slot["status"] == "pending_venue_approval":
                gig_state = "my_pending_approval"
            else:
                gig_state = "my_open"
        elif is_banned:
            gig_state = "banned"
        elif has_wl:
            gig_state = "waitlist_locked"
        elif all_booked:
            gig_state = "fully_booked"
        else:
            gig_state = "open"
    else:  # venue
        if any_pending:
            gig_state = "pending_contract"
        elif all_booked:
            gig_state = "fully_booked"
        elif any_booked:
            gig_state = "partially_booked"
        else:
            gig_state = "open"

    # ── Timing ────────────────────────────────────────────────────────────
    # TZ FIX (May 11 2026): use the VENUE's timezone, not platform tz. Gig
    # date/start_time/end_time are stored as venue-local strings; comparing
    # against platform-tz "now" is wrong whenever venue tz != platform tz
    # (e.g. a NY venue when platform tz is America/Los_Angeles would appear
    # in-progress 3h early). Use venue tz so gigs in any state correctly
    # transition past/in-progress in their own local time.
    try:
        from backend.utils import get_venue_timezone
        _tz = get_venue_timezone(db, gig["venue_id"])
        _now = datetime.now(_tz)
    except Exception:
        _now = datetime.now()

    # is_past must compare against the gig's actual END datetime, not
    # just its DATE. A gig ending at 11pm tonight was previously treated
    # as "not past" until midnight rolled over — so an artist clicking
    # their gig at 11:30pm saw the live/upcoming modal with "Cancel My
    # Slot", even though the show ended 30 min ago.
    # For multi-slot gigs we use the LAST slot's end so the modal stays
    # in "in progress" until every slot has finished.
    try:
        gig_date_obj = datetime.strptime(str(gig["date"])[:10], "%Y-%m-%d").date()
    except Exception:
        gig_date_obj = None

    is_past = False
    try:
        from datetime import time as _dt_time, timedelta as _dt_td
        _now_naive = _now.replace(tzinfo=None) if _now.tzinfo else _now
        _last_end_dt = None
        for s in slots:
            if not s.get("end_time"):
                continue
            try:
                _eh, _em = map(int, str(s["end_time"]).split(":")[:2])
                _end_dt = datetime.combine(gig_date_obj, _dt_time(hour=_eh, minute=_em))
                # Overnight slot — if end < start, end is the next day
                _sh, _sm = (None, None)
                if s.get("start_time"):
                    try:
                        _sh, _sm = map(int, str(s["start_time"]).split(":")[:2])
                    except Exception:
                        pass
                if _sh is not None and (_eh, _em) < (_sh, _sm):
                    _end_dt += _dt_td(days=1)
                if _last_end_dt is None or _end_dt > _last_end_dt:
                    _last_end_dt = _end_dt
            except Exception:
                continue
        # Fallback to gig.end_time when slots have no usable end_time
        if _last_end_dt is None and gig.get("end_time") and gig_date_obj:
            try:
                _eh, _em = map(int, str(gig["end_time"]).split(":")[:2])
                _last_end_dt = datetime.combine(gig_date_obj, _dt_time(hour=_eh, minute=_em))
                if gig.get("start_time"):
                    _sh, _sm = map(int, str(gig["start_time"]).split(":")[:2])
                    if (_eh, _em) < (_sh, _sm):
                        _last_end_dt += _dt_td(days=1)
            except Exception:
                pass
        if _last_end_dt is not None:
            is_past = _last_end_dt < _now_naive
        elif gig_date_obj is not None:
            # No usable end time — fall back to date-based check (legacy).
            is_past = gig_date_obj < _now.date()
    except Exception:
        is_past = gig_date_obj is not None and gig_date_obj < _now.date()

    # Is any slot currently in progress?
    is_in_progress = False
    try:
        _now_naive = _now.replace(tzinfo=None) if _now.tzinfo else _now
        for s in slots:
            if s.get("start_time") and s.get("end_time"):
                st = datetime.strptime(f"{gig['date']}T{str(s['start_time'])[:5]}", "%Y-%m-%dT%H:%M")
                et = datetime.strptime(f"{gig['date']}T{str(s['end_time'])[:5]}", "%Y-%m-%dT%H:%M")
                # Overnight slots: if end < start, end is next day
                if et < st:
                    et = et + timedelta(days=1)
                if st <= _now_naive <= et:
                    is_in_progress = True
                    break
    except Exception:
        pass

    # ── Blast window info ─────────────────────────────────────────────────
    blast_info = {}

    # Can artist message this venue? Yes if they have a slot or prior messages
    _can_message = False
    if viewer_type == 'artist':
        _has_slot = any(s.get("is_my_slot") for s in slots)
        _has_prior_msg = False
        if not _has_slot:
            try:
                _has_prior_msg = bool(db.execute(text(
                    "SELECT 1 FROM gig_messages WHERE gig_id=:gid AND sender_entity_id=:aid AND sender_type='artist' LIMIT 1"
                ), {"gid": gig_id, "aid": viewer_id}).first())
            except Exception:
                pass
        _can_message = _has_slot or _has_prior_msg
    else:
        _can_message = True
    if viewer_type == "artist":
        last_notif = db.execute(text("""
            SELECT notification_key FROM gig_email_log
            WHERE gig_id=:gid AND notification_key IN
              ('open_gig_4w','open_gig_2w','open_gig_1w','open_gig_36h','cancelled_blast','radius_blast')
            ORDER BY sent_at DESC LIMIT 1
        """), {"gid": gig_id}).scalar()
        blast_info["last_notification_key"] = last_notif

    return {
        # Gig header
        "id":            gig["id"],
        "date":          gig["date"],
        "start_time":    gig["start_time"],
        "end_time":      gig["end_time"],
        "start_time_fmt": _fmt_time(gig["start_time"]),
        "end_time_fmt":  _fmt_time(gig["end_time"]),
        "title":         gig.get("title"),
        "notes":         gig.get("notes"),
        "status":        gig["status"],
        "artist_type":   gig.get("artist_type"),
        "band_formats":  gig.get("band_formats"),
        "styles":        gig.get("styles"),
        "pay":           gig.get("pay"),
        "is_multi_slot": bool(gig.get("is_multi_slot")),
        "is_blast_open": gig_is_blast_open,
        "frequency_exempt": gig_freq_exempt,
        "has_active_waitlist": has_wl,
        "recurring_group_id": gig.get("recurring_group_id"),
        "is_recurring":  bool(gig.get("is_recurring")),
        # Venue info
        "venue_id":      gig["venue_id"],
        "venue_name":    gig["venue_name"],
        "address_line_1": gig.get("address_line_1"),
        "address_line_2": gig.get("address_line_2"),
        "city":          gig.get("city"),
        "state":         gig.get("state"),
        "venue_lat":     gig.get("venue_lat"),
        "venue_lon":     gig.get("venue_lon"),
        # Timing
        "is_past":       is_past,
        "is_in_progress": is_in_progress,
        # Viewer context
        "gig_state":     gig_state,
        "viewer_type":   viewer_type,
        "viewer_id":     viewer_id,
        # Artist checks (only populated for artist viewer)
        "preferred_status":   preferred_status,
        "is_banned":          is_banned,
        "freq_check":         freq_check,
        "waitlist_status":    waitlist_status,
        "hold_info":          hold_info,
        "can_message":        _can_message,
        "artist_data":        artist_data,
        "venue_contract_required": venue_contract_required,
        "venue_contract_type":     venue_contract_type,
        # Slots with full per-viewer context
        "slots":         slot_data,
        # Blast info
        "blast_info":    blast_info,
    }
