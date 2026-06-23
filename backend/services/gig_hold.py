"""Gig Hold feature — venue-initiated private offer cycle at gig creation.

Phase 1 (foundation) + Phase 2 (offers + email + scheduler advancement +
multi-slot fill-as-you-go).

Sibling to the existing post-cancellation waitlist. Shares the
gig_waitlist table via the `source` column:
  - source='cancellation' (legacy) — fires when a booked artist cancels
  - source='hold' (new) — venue picks N artists at gig creation,
    sequentially offers the gig 24h each. The same artist who accepts
    one slot can be re-offered remaining slots if the gig has more
    than one open slot.

State on gigs:
  gigs.status       stays 'open' throughout (so the normal booking flow,
                    contract flow, transaction flow apply on acceptance
                    unchanged)
  gigs.hold_status  'active'    : currently in offer cycle
                    'exhausted' : everyone declined, venue must decide
                    NULL        : not a held gig (or hold cleared)

Visibility: gigs.status='open' AND hold_status IS NULL → visible to
artist search. Active+exhausted holds hidden until venue resolves.
"""
import logging
import re
import secrets
from typing import Optional

logger = logging.getLogger("gigsfill.gig_hold")


# ──────────────────────────────────────────────────────────────────────
# Public API used by routes/gigs.py:create_gig + the scheduler
# ──────────────────────────────────────────────────────────────────────


def create_hold_waitlist(db, gig_id: int, artist_ids: list, send_email_now: bool,
                         offer_window_hours: int = 24):
    """Called from create_gig after the gig row + slot rows are inserted.

    artist_ids: ordered list of preferred-artist ids (length 1 = single-
                artist hold; length N = waitlist). Position 0 = first offer.
    send_email_now: True → send the first offer immediately. False → rows
                    staged but no one notified (venue can trigger later).
    offer_window_hours: default 24.
    """
    from sqlalchemy import text
    if not artist_ids:
        return
    db.execute(
        text("""UPDATE gigs SET hold_status = 'active',
                                hold_offer_window_hours = :hrs,
                                hold_email_artists = :em,
                                hold_created_at = CURRENT_TIMESTAMP
                WHERE id = :gid"""),
        {"gid": gig_id, "hrs": int(offer_window_hours), "em": 1 if send_email_now else 0},
    )
    for i, aid in enumerate(artist_ids):
        try:
            db.execute(
                text("""INSERT INTO gig_waitlist
                          (gig_id, artist_id, source, position, notified)
                        VALUES (:gid, :aid, 'hold', :pos, 0)"""),
                {"gid": gig_id, "aid": int(aid), "pos": i},
            )
        except Exception as e:
            logger.warning(f"[HOLD] skipped duplicate artist={aid} on gig={gig_id}: {e}")
    db.commit()
    if send_email_now:
        send_next_hold_offer(db, gig_id)


def send_next_hold_offer(db, gig_id: int) -> Optional[int]:
    """Find the next hold-waitlist row with no offer in flight, mark
    offered (token + 24h window), and fire the hold_offer_artist email.

    Multi-slot semantics: this is called whenever (a) the venue creates
    a new hold, (b) a previous offer expires/declines, OR (c) a previous
    offer accepts but the gig still has open slots remaining. The same
    artist who accepted one slot on a multi-slot gig won't be re-offered
    their own remaining slots (they could just go book them directly);
    the next position in the waitlist gets them.

    Returns the artist_id offered, or None if the waitlist is exhausted.
    On exhaustion, marks hold_status='exhausted' + fires the venue email.
    """
    from sqlalchemy import text
    row = db.execute(
        text("""SELECT id, artist_id, position FROM gig_waitlist
                WHERE gig_id = :gid
                  AND source = 'hold'
                  AND offer_sent = 0
                  AND offer_declined = 0
                ORDER BY position ASC LIMIT 1"""),
        {"gid": gig_id},
    ).mappings().first()
    if not row:
        _mark_exhausted_and_notify(db, gig_id)
        return None
    win = db.execute(
        text("SELECT COALESCE(hold_offer_window_hours, 24) FROM gigs WHERE id = :gid"),
        {"gid": gig_id},
    ).scalar()
    token = secrets.token_urlsafe(24)
    db.execute(
        text("""UPDATE gig_waitlist
                SET offer_sent = 1,
                    offer_sent_at = CURRENT_TIMESTAMP,
                    offer_expires_at = datetime('now', '+' || :hrs || ' hours'),
                    offer_token = :tok,
                    notified = 1,
                    notified_at = CURRENT_TIMESTAMP
                WHERE id = :id"""),
        {"id": row["id"], "tok": token, "hrs": int(win or 24)},
    )
    db.commit()
    aid = int(row["artist_id"])
    logger.info(f"[HOLD] gig={gig_id} offered to artist={aid} (position={row['position']}, token={token[:8]}...)")
    _send_hold_offer_email(db, gig_id=gig_id, artist_id=aid, token=token, is_reminder=False)
    return aid


def respond_to_hold_offer(db, token: str, action: str, slot_id: Optional[int] = None):
    """Token-based accept/decline endpoint handler. Called by
    routes/gigs.py:respond_hold (added in this phase).

    action='accept' + slot_id: book the specified slot for this artist.
                                If the gig has more open slots after,
                                advance to the next artist on the waitlist
                                (same artist isn't re-offered).
    action='decline':           mark the offer declined, advance to
                                next artist.

    Returns a dict with `ok` + a human-readable `message` + (on accept)
    the booked slot_id. Idempotent: a token that's been used returns
    a friendly "already responded" message instead of erroring.
    """
    from sqlalchemy import text
    row = db.execute(
        text("""SELECT wl.id as wl_id, wl.gig_id, wl.artist_id, wl.offer_expires_at,
                       wl.offer_declined, wl.notified_at,
                       g.hold_status, g.status as gig_status, g.is_multi_slot,
                       g.venue_id
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                WHERE wl.offer_token = :tok AND wl.source = 'hold'
                LIMIT 1"""),
        {"tok": token}
    ).mappings().first()
    if not row:
        return {"ok": False, "message": "This offer link isn't valid. It may have been replaced by a newer offer."}
    if row["offer_declined"]:
        return {"ok": False, "message": "You already responded to this offer."}
    # Token expired?
    from datetime import datetime as _dt
    if row["offer_expires_at"]:
        exp_str = str(row["offer_expires_at"]).replace("T", " ").split(".")[0]
        try:
            exp_dt = _dt.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
            if _dt.utcnow() > exp_dt:
                return {"ok": False, "message": "This offer has expired. The gig has been offered to the next artist."}
        except Exception:
            pass
    if row["hold_status"] != "active":
        return {"ok": False, "message": "This hold is no longer active."}

    if action == "decline":
        db.execute(
            text("""UPDATE gig_waitlist SET offer_declined = 1,
                                            offer_expires_at = CURRENT_TIMESTAMP
                    WHERE id = :wlid"""),
            {"wlid": row["wl_id"]}
        )
        db.commit()
        logger.info(f"[HOLD] gig={row['gig_id']} artist={row['artist_id']} declined")
        _notify_venue_decline(db, gig_id=row["gig_id"], artist_id=row["artist_id"],
                              reason="declined")
        send_next_hold_offer(db, row["gig_id"])
        return {"ok": True, "message": "Thanks — we've let the venue know."}

    if action == "accept":
        # Multi-slot gigs with mixed types: filter to only slots this
        # specific artist can actually fill. A DJ artist offered a gig
        # with a Live Band slot + a DJ slot only sees the DJ slot.
        open_slots = _list_matching_open_slots(db, row["gig_id"], row["artist_id"])
        if not open_slots:
            # Either no open slots at all OR none match this artist's
            # type. Both are dead-ends for this offer.
            any_open = _list_open_hold_slots(db, row["gig_id"])
            if not any_open:
                return {"ok": False, "message": "All slots on this gig have already been booked."}
            return {"ok": False, "message": "None of the open slots match your artist type."}
        chosen = None
        if slot_id:
            for s in open_slots:
                if int(s["id"]) == int(slot_id):
                    chosen = s
                    break
            if chosen is None:
                return {"ok": False, "message": "That slot is no longer available for your type — pick a different one."}
        elif len(open_slots) == 1:
            chosen = open_slots[0]
        else:
            return {"ok": False, "message": "This gig has more than one open slot — pick which one you want."}

        # Book the slot atomically (status='open' guard).
        result = db.execute(
            text("""UPDATE gig_slots SET artist_id = :aid, status = 'booked'
                    WHERE id = :sid AND status = 'open'"""),
            {"aid": row["artist_id"], "sid": chosen["id"]}
        )
        if result.rowcount == 0:
            db.commit()
            return {"ok": False, "message": "Someone else grabbed that slot first. Try a different one."}
        # Mark this waitlist row as no longer in flight (offer consumed)
        db.execute(
            text("""UPDATE gig_waitlist SET offer_declined = 1,
                                            offer_expires_at = CURRENT_TIMESTAMP
                    WHERE id = :wlid"""),
            {"wlid": row["wl_id"]}
        )

        # Are there still open slots? If yes, hold stays active and we
        # advance to the next artist (excluding the one who just
        # booked — they could come back via the standard search if
        # they want another slot, but the hold rotation moves on).
        # If no open slots: gig is fully booked; promote gigs.status =
        # 'booked' and clear hold_status.
        more_open = db.execute(
            text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
            {"gid": row["gig_id"]}
        ).scalar()
        if more_open and more_open > 0:
            db.commit()
            logger.info(f"[HOLD] gig={row['gig_id']} artist={row['artist_id']} accepted slot={chosen['id']}; {more_open} slot(s) still open — advancing waitlist")
            _notify_venue_accept(db, gig_id=row["gig_id"], artist_id=row["artist_id"],
                                 slot=chosen, remaining_open=int(more_open))
            send_next_hold_offer(db, row["gig_id"])
            return {"ok": True, "message": "Booked! We've notified the venue.", "slot_id": int(chosen["id"])}
        else:
            db.execute(
                text("UPDATE gigs SET status = 'booked', hold_status = NULL WHERE id = :gid"),
                {"gid": row["gig_id"]}
            )
            db.commit()
            logger.info(f"[HOLD] gig={row['gig_id']} artist={row['artist_id']} accepted final slot={chosen['id']} — gig fully booked")
            _notify_venue_accept(db, gig_id=row["gig_id"], artist_id=row["artist_id"],
                                 slot=chosen, remaining_open=0)
            return {"ok": True, "message": "Booked! We've notified the venue.", "slot_id": int(chosen["id"])}

    return {"ok": False, "message": "Unknown action."}


# ──────────────────────────────────────────────────────────────────────
# Scheduler hook — runs every 10 minutes inside _scheduler_loop
# ──────────────────────────────────────────────────────────────────────


def process_hold_offers(db):
    """Two responsibilities per tick:
      1. Send the 12h reminder for any active offer that passed the
         half-way mark and hasn't been reminded yet.
      2. Expire any offer past offer_expires_at, mark declined,
         advance to next artist on the waitlist.
    """
    from sqlalchemy import text
    # 1. Reminders. Find offers where >= 50% of the window has elapsed
    #    but reminder_sent_at IS NULL and decline=0 and not yet expired.
    #    The "50% of window" calc reads hold_offer_window_hours from gigs.
    reminders = db.execute(
        text("""SELECT wl.id as wl_id, wl.gig_id, wl.artist_id, wl.offer_token,
                       wl.offer_sent_at, wl.offer_expires_at,
                       g.hold_offer_window_hours
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                WHERE wl.source = 'hold'
                  AND wl.offer_sent = 1
                  AND wl.offer_declined = 0
                  AND wl.reminder_sent_at IS NULL
                  AND wl.offer_expires_at > CURRENT_TIMESTAMP
                  AND datetime('now', '-' || (COALESCE(g.hold_offer_window_hours, 24) / 2) || ' hours')
                      > wl.offer_sent_at""")
    ).mappings().all()
    for r in reminders:
        try:
            _send_hold_offer_email(db, gig_id=r["gig_id"], artist_id=r["artist_id"],
                                   token=r["offer_token"], is_reminder=True)
            db.execute(
                text("UPDATE gig_waitlist SET reminder_sent_at = CURRENT_TIMESTAMP WHERE id = :id"),
                {"id": r["wl_id"]}
            )
            db.commit()
        except Exception as e:
            logger.warning(f"[HOLD] reminder send failed for wl={r['wl_id']}: {e}")

    # 2. Expirations. Anything past expiry that wasn't already declined →
    #    treat as decline, advance next artist.
    expired = db.execute(
        text("""SELECT id, gig_id, artist_id FROM gig_waitlist
                WHERE source = 'hold'
                  AND offer_sent = 1
                  AND offer_declined = 0
                  AND offer_expires_at <= CURRENT_TIMESTAMP""")
    ).mappings().all()
    advanced_gigs = set()
    for r in expired:
        db.execute(
            text("UPDATE gig_waitlist SET offer_declined = 1 WHERE id = :id"),
            {"id": r["id"]}
        )
        db.commit()
        logger.info(f"[HOLD] gig={r['gig_id']} artist={r['artist_id']} offer expired — advancing")
        _notify_venue_decline(db, gig_id=r["gig_id"], artist_id=r["artist_id"],
                              reason="expired")
        if r["gig_id"] not in advanced_gigs:
            send_next_hold_offer(db, r["gig_id"])
            advanced_gigs.add(r["gig_id"])


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────


def _list_open_hold_slots(db, gig_id: int):
    """Return the list of currently-open slots on this gig as dicts
    with id, slot_number, start_time, end_time, pay + deal info (so
    door-deal slots render correctly in the artist's banner / modal)
    + type fields for the match filter."""
    from sqlalchemy import text
    return db.execute(
        text("""SELECT id, slot_number, start_time, end_time, pay,
                       deal_type, door_pct, guarantee_cents,
                       artist_type, band_formats, styles
                FROM gig_slots WHERE gig_id = :gid AND status = 'open'
                ORDER BY slot_number ASC"""),
        {"gid": gig_id}
    ).mappings().all()


def _csv_set(s):
    """CSV-or-semicolon-separated string → set of tokens (lowercased
    for case-insensitive compare). Used by _artist_matches_slot to
    match the frontend's _csvSet behavior."""
    if not s:
        return set()
    return {t.strip().lower() for t in str(s).replace(";", ",").split(",") if t.strip()}


def _artist_matches_slot(artist_row, slot_row) -> bool:
    """Mirror of the frontend _artistMatchesSlot. An artist can only
    book a slot when:
      - slot.artist_type is set AND artist.artist_type equals it
      - if slot specifies band_formats, artist has at least one overlap
      - if slot specifies styles, artist has at least one overlap
    Used to filter the open-slot list on hold respond so a DJ artist
    offered a multi-slot gig with a DJ slot + a Live Band slot only
    sees the DJ slot as bookable."""
    if not artist_row or not slot_row:
        return False
    s_type = (slot_row.get("artist_type") or "").strip()
    a_type = (artist_row.get("artist_type") or "").strip()
    if not s_type or not a_type:
        return False
    if s_type != a_type:
        return False
    slot_fmt = _csv_set(slot_row.get("band_formats"))
    if slot_fmt:
        if not (slot_fmt & _csv_set(artist_row.get("band_formats"))):
            return False
    slot_styles = _csv_set(slot_row.get("styles"))
    if slot_styles:
        if not (slot_styles & _csv_set(artist_row.get("styles"))):
            return False
    return True


def _list_matching_open_slots(db, gig_id: int, artist_id: int):
    """Filter _list_open_hold_slots to only slots the artist matches.
    Pulls the artist's profile fields and runs each slot through
    _artist_matches_slot. Returns the list (possibly empty)."""
    from sqlalchemy import text
    artist = db.execute(
        text("""SELECT id, artist_type, band_formats, styles
                FROM artists WHERE id = :aid"""),
        {"aid": artist_id}
    ).mappings().first()
    open_slots = _list_open_hold_slots(db, gig_id)
    return [s for s in open_slots if _artist_matches_slot(artist, dict(s))]


def _mark_exhausted_and_notify(db, gig_id: int):
    from sqlalchemy import text
    db.execute(
        text("UPDATE gigs SET hold_status = 'exhausted' WHERE id = :gid"),
        {"gid": gig_id}
    )
    db.commit()
    logger.info(f"[HOLD] gig={gig_id} waitlist exhausted")
    # Fire the venue email + Activity Center notification
    try:
        _notify_venue_exhausted(db, gig_id=gig_id)
    except Exception as e:
        logger.warning(f"[HOLD] exhausted notify failed gig={gig_id}: {e}")


def _send_hold_offer_email(db, gig_id: int, artist_id: int, token: str, is_reminder: bool):
    """Render + send the offer (or reminder) email to the artist via
    SMTP. Template key: hold_offer_artist / hold_offer_reminder_artist.
    Falls back silently if SMTP unconfigured (test/dev)."""
    from sqlalchemy import text
    gig = db.execute(
        text("""SELECT g.id, g.date, g.title, g.venue_id, g.pay,
                       g.is_multi_slot, g.start_time, g.end_time,
                       v.venue_name
                FROM gigs g JOIN venues v ON v.id = g.venue_id
                WHERE g.id = :gid"""),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        return
    artist = db.execute(
        text("""SELECT a.name, u.email FROM artists a
                LEFT JOIN users u ON u.id = a.user_id WHERE a.id = :aid"""),
        {"aid": artist_id}
    ).mappings().first()
    if not artist or not artist["email"]:
        logger.warning(f"[HOLD] no email on file for artist={artist_id}")
        return

    # Filter open slots to ones the artist actually matches. Showing
    # all open slots in the offer email would be misleading for a DJ
    # artist offered a multi-type gig — they'd see a Live Band slot
    # they can't book.
    open_slots = _list_matching_open_slots(db, gig_id, artist_id)
    is_multi = bool(gig.get("is_multi_slot"))

    # If no slots match this artist's type, don't send a useless offer.
    # Mark the waitlist row declined and advance to the next artist.
    if not open_slots:
        from sqlalchemy import text as _t
        logger.warning(
            f"[HOLD] gig={gig_id} artist={artist_id} has no matching "
            f"open slot — auto-declining + advancing"
        )
        db.execute(
            _t("""UPDATE gig_waitlist SET offer_declined = 1
                  WHERE gig_id = :gid AND artist_id = :aid AND source = 'hold'
                    AND offer_token = :tok"""),
            {"gid": gig_id, "aid": artist_id, "tok": token}
        )
        db.commit()
        send_next_hold_offer(db, gig_id)
        return

    # Build the dynamic template vars
    base_url = "https://gigsfill.com"
    respond_url = f"{base_url}/hold/respond/{token}"  # served via /hold redirect, set up in routes
    decline_url = f"{base_url}/hold/decline/{token}"

    # Compose the slot/pitch text variants
    if is_multi and len(open_slots) > 1:
        slots_pitch = f"This gig has {len(open_slots)} open slots — pick the one you want."
        accept_label = "Pick a slot →"
        multi_slot_pick_note = " (you'll choose your slot on the next page)"
    else:
        slots_pitch = "Confirm if you can play it."
        accept_label = "Accept"
        multi_slot_pick_note = ""

    # Build the slots table rows for the offer card
    if is_multi and len(open_slots) > 1:
        slot_rows_html = ""
        for s in open_slots:
            time_str = f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}"
            slot_rows_html += (
                f'<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Slot {s["slot_number"]}</td>'
                f'<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">'
                f'{time_str}  ·  ${int(s["pay"]) if s["pay"] and s["pay"] == int(s["pay"]) else s["pay"]}</td></tr>'
            )
    else:
        # Single-slot or fall-through gig — one Time + Pay row
        s = open_slots[0] if open_slots else None
        if s:
            time_str = f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}"
            pay = int(s["pay"]) if s["pay"] and s["pay"] == int(s["pay"]) else s["pay"]
        else:
            time_str = f"{_fmt_time(gig['start_time'])} – {_fmt_time(gig['end_time'])}"
            pay = int(gig["pay"]) if gig["pay"] and gig["pay"] == int(gig["pay"]) else gig["pay"]
        slot_rows_html = (
            f'<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>'
            f'<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{time_str}</td></tr>'
            f'<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Pay</td>'
            f'<td style="padding: 6px 0; font-size: 14px; color: #059669; font-weight: 600;">${pay}</td></tr>'
        )

    # Human-readable expiry timestamp (UTC for now; can localize later)
    exp_row = db.execute(
        text("SELECT offer_expires_at FROM gig_waitlist WHERE offer_token = :t"),
        {"t": token}
    ).scalar()
    offer_expires_human = str(exp_row).replace("T", " ").split(".")[0] + " UTC" if exp_row else "in 24 hours"

    template_key = "hold_offer_reminder_artist" if is_reminder else "hold_offer_artist"
    vars_dict = {
        "artist_name": artist["name"] or "there",
        "venue_name": gig["venue_name"] or "the venue",
        "date": str(gig["date"]),
        "title": gig.get("title") or "",
        "slots_pitch": slots_pitch,
        "multi_slot_pick_note": multi_slot_pick_note,
        "accept_label": accept_label,
        "respond_url": respond_url,
        "decline_url": decline_url,
        "slots_table_rows": slot_rows_html,
        "offer_expires_human": offer_expires_human,
    }
    _dispatch_template(db, to_email=artist["email"], template_key=template_key, vars=vars_dict)
    # Activity Center notification — drop a record for every user with
    # artist access so they see "1 pending offer" in their dropdown even
    # when they're not on the book-gigs page. Banner on book-gigs is the
    # primary surface; this is the backup channel.
    if not is_reminder:
        _create_artist_notification(
            db, artist_id,
            f"Offer from {gig['venue_name']} — {gig['date']}",
            f"You've got 24 hours to accept or decline. Open the Calendar page to respond.",
            gig_id=gig_id
        )


def _create_artist_notification(db, artist_id: int, title: str, body: str, gig_id: int = None):
    """Activity Center entry for every user with access to this artist."""
    try:
        from backend.services.notification_service import create_notification
        from backend.utils import get_all_entity_users
        users = get_all_entity_users(db, "artist", artist_id)
        for u in users:
            create_notification(db, u["user_id"], "hold_offer", title, body, gig_id=gig_id)
        db.commit()
    except Exception as e:
        logger.warning(f"[HOLD] artist notification failed for artist={artist_id}: {e}")


def _notify_venue_accept(db, gig_id: int, artist_id: int, slot, remaining_open: int):
    """Email venue + drop in-app notification when an artist accepts."""
    from sqlalchemy import text
    gig = db.execute(
        text("""SELECT g.date, g.venue_id, v.venue_name FROM gigs g
                JOIN venues v ON v.id = g.venue_id WHERE g.id = :gid"""),
        {"gid": gig_id}
    ).mappings().first()
    artist = db.execute(
        text("SELECT name FROM artists WHERE id = :aid"), {"aid": artist_id}
    ).mappings().first()
    venue_users = _venue_emails(db, gig["venue_id"])
    if not venue_users:
        return
    slot_info = ""
    slot_info_phrase = ""
    if slot:
        time_str = f"{_fmt_time(slot['start_time'])} – {_fmt_time(slot['end_time'])}"
        slot_info = f"Slot {slot['slot_number']}: {time_str}"
        slot_info_phrase = f" (Slot {slot['slot_number']}, {time_str})"
    if remaining_open > 0:
        remaining_slots_note = (
            f"This gig still has {remaining_open} open slot{'s' if remaining_open != 1 else ''} — "
            "the hold is moving to the next artist on your list."
        )
    else:
        remaining_slots_note = "All slots on this gig are now booked. The hold is complete."
    vars_dict = {
        "artist_name": artist["name"] if artist else "An artist",
        "date": str(gig["date"]),
        "venue_id": gig["venue_id"],
        "slot_info": slot_info,
        "slot_info_phrase": slot_info_phrase,
        "pay": int(slot["pay"]) if slot and slot["pay"] and slot["pay"] == int(slot["pay"]) else (slot["pay"] if slot else 0),
        "remaining_slots_note": remaining_slots_note,
    }
    for to_email in venue_users:
        _dispatch_template(db, to_email=to_email, template_key="hold_accept_venue", vars=vars_dict)
    _create_venue_notification(db, gig["venue_id"],
        f"{vars_dict['artist_name']} accepted hold",
        f"Booked {gig['date']}{slot_info_phrase}. {remaining_slots_note}",
        gig_id=gig_id)


def _notify_venue_decline(db, gig_id: int, artist_id: int, reason: str):
    from sqlalchemy import text
    gig = db.execute(
        text("""SELECT g.date, g.venue_id, v.venue_name FROM gigs g
                JOIN venues v ON v.id = g.venue_id WHERE g.id = :gid"""),
        {"gid": gig_id}
    ).mappings().first()
    artist = db.execute(
        text("SELECT name FROM artists WHERE id = :aid"), {"aid": artist_id}
    ).mappings().first()
    venue_users = _venue_emails(db, gig["venue_id"])
    if not venue_users:
        return
    # Is there a next artist queued?
    next_q = db.execute(
        text("""SELECT a.name FROM gig_waitlist wl
                JOIN artists a ON a.id = wl.artist_id
                WHERE wl.gig_id = :gid AND wl.source='hold'
                  AND wl.offer_sent = 0 AND wl.offer_declined = 0
                ORDER BY wl.position ASC LIMIT 1"""),
        {"gid": gig_id}
    ).mappings().first()
    if next_q:
        next_step_note = f"We've moved on to {next_q['name']} on your list. They have 24h to respond."
    else:
        next_step_note = "That was the last artist on your list. We'll let you know when the offer cycle finishes."
    vars_dict = {
        "artist_name": artist["name"] if artist else "An artist",
        "date": str(gig["date"]),
        "venue_id": gig["venue_id"],
        "decline_reason_phrase": "declined" if reason == "declined" else "didn't respond within the 24-hour window for",
        "next_step_note": next_step_note,
    }
    for to_email in venue_users:
        _dispatch_template(db, to_email=to_email, template_key="hold_decline_venue", vars=vars_dict)
    _create_venue_notification(db, gig["venue_id"],
        f"{vars_dict['artist_name']} {vars_dict['decline_reason_phrase']} the hold",
        next_step_note, gig_id=gig_id)


def _notify_venue_exhausted(db, gig_id: int):
    from sqlalchemy import text
    gig = db.execute(
        text("""SELECT g.date, g.venue_id, v.venue_name FROM gigs g
                JOIN venues v ON v.id = g.venue_id WHERE g.id = :gid"""),
        {"gid": gig_id}
    ).mappings().first()
    venue_users = _venue_emails(db, gig["venue_id"])
    if not venue_users:
        return
    open_slots = _list_open_hold_slots(db, gig_id)
    tried = db.execute(
        text("""SELECT COUNT(*) FROM gig_waitlist
                WHERE gig_id = :gid AND source='hold' AND offer_sent = 1"""),
        {"gid": gig_id}
    ).scalar()
    if not open_slots:
        # All slots filled but waitlist still ran out — shouldn't happen
        # but be safe; nothing more to do.
        return
    if len(open_slots) == 1:
        s = open_slots[0]
        summary = f"Slot {s['slot_number']} ({_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])})"
    else:
        summary = ", ".join(f"Slot {s['slot_number']}" for s in open_slots)
    vars_dict = {
        "venue_name": gig["venue_name"],
        "date": str(gig["date"]),
        "venue_id": gig["venue_id"],
        "open_slot_summary": summary,
        "artists_tried_count": int(tried or 0),
    }
    for to_email in venue_users:
        _dispatch_template(db, to_email=to_email, template_key="hold_exhausted_venue", vars=vars_dict)
    _create_venue_notification(db, gig["venue_id"],
        f"Your hold for {gig['date']} ran out",
        f"{tried} artist{'s' if tried != 1 else ''} tried, {summary} still open. Open it to all or cancel — your call.",
        gig_id=gig_id)


# ──────────────────────────────────────────────────────────────────────
# Lower-level helpers
# ──────────────────────────────────────────────────────────────────────


def _venue_emails(db, venue_id: int):
    """Return the venue owner + entity_users emails for notifications."""
    from sqlalchemy import text
    rows = db.execute(
        text("""SELECT u.email FROM users u
                JOIN venues v ON v.user_id = u.id WHERE v.id = :vid
                UNION
                SELECT u.email FROM users u
                JOIN entity_users eu ON eu.user_id = u.id
                WHERE eu.entity_type = 'venue' AND eu.entity_id = :vid"""),
        {"vid": venue_id}
    ).fetchall()
    return [r[0] for r in rows if r and r[0]]


def _create_venue_notification(db, venue_id: int, title: str, body: str, gig_id: int = None):
    """Drop an Activity Center notification for every user with venue access."""
    try:
        from backend.services.notification_service import create_notification
        from backend.utils import get_all_entity_users
        users = get_all_entity_users(db, "venue", venue_id)
        for u in users:
            create_notification(db, u["user_id"], "hold_update", title, body, gig_id=gig_id)
        db.commit()
    except Exception as e:
        logger.warning(f"[HOLD] in-app notification failed for venue={venue_id}: {e}")


def _dispatch_template(db, to_email: str, template_key: str, vars: dict):
    """Render a template from the email_templates table + send via SMTP.
    Logs failures loudly so the existing log→email alert pipeline can
    pick them up."""
    from sqlalchemy import text
    try:
        tpl = db.execute(
            text("SELECT subject, body FROM email_templates WHERE template_key = :k"),
            {"k": template_key}
        ).mappings().first()
        if not tpl:
            logger.error(f"[HOLD] template missing: {template_key}")
            return
        subject = _render(tpl["subject"], vars)
        body = _render(tpl["body"], vars)
        from backend.scheduler import get_smtp_settings, send_email
        # SMTP settings reader wants a raw cursor; sqlalchemy session
        # connection has one via .connection().cursor()
        import sqlite3
        from backend.db import DB_PATH
        rc = sqlite3.connect(str(DB_PATH))
        try:
            smtp = get_smtp_settings(rc.cursor())
            if not smtp:
                logger.error(f"[HOLD] SMTP not configured — can't send {template_key} to {to_email}")
                return
            ok = send_email(smtp, to_email, subject, body)
            if not ok:
                logger.error(f"[HOLD] SMTP send FAILED for {template_key} → {to_email}")
        finally:
            rc.close()
    except Exception as e:
        logger.error(f"[HOLD] dispatch failed for {template_key} → {to_email}: {e}")


# Tiny Mustache-style renderer. Supports {{var}} and {{#var}}…{{/var}}
# blocks for conditional sections (used by templates like {{#title}}…).
def _render(template: str, vars: dict) -> str:
    if template is None:
        return ""
    out = template
    # Conditional blocks first so {{var}} substitutions inside are
    # respected.
    def _block(match):
        key = match.group(1)
        inner = match.group(2)
        v = vars.get(key)
        if v:
            return _render(inner, vars)
        return ""
    out = re.sub(r"\{\{#([a-zA-Z0-9_]+)\}\}([\s\S]*?)\{\{/\1\}\}", _block, out)
    for k, v in vars.items():
        out = out.replace("{{" + k + "}}", str(v) if v is not None else "")
    return out


def _fmt_time(t):
    """'19:00' or '19:00:00' → '7:00 PM'. Returns input on parse error."""
    if not t:
        return ""
    s = str(t).strip()
    if not s or ":" not in s:
        return s
    parts = s.split(":")
    try:
        h = int(parts[0])
        m = parts[1] if len(parts) > 1 else "00"
        ampm = "PM" if h >= 12 else "AM"
        h12 = h % 12 or 12
        return f"{h12}:{m} {ampm}"
    except Exception:
        return s
