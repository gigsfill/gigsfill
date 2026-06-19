"""
Open-gig daily digest pipeline.

OWNERSHIP:
  Detection runs hourly in scheduler.py:process_open_gig_notifications.
  When platform_setting `open_gig_daily_digest_enabled` is true (the
  default), that detector now ENQUEUES into artist_email_digest_queue
  instead of sending immediately. This module provides:

      enqueue_open_gig_for_artist(cursor, user_id, artist_id, gig_id,
                                  venue_id, notification_key, via_radius)
          Idempotent INSERT (the unique constraint on (user_id, gig_id,
          notification_key) handles re-detection across hours/days).

      send_daily_artist_digest(cursor, smtp_config)
          Runs once per hour (from the scheduler loop). Selects users
          whose local hour equals `open_gig_daily_digest_hour` AND
          who have unsent queued entries. Renders the daily digest
          (grouped by venue), sends one consolidated email per user,
          marks the queue rows sent_at=now.

      prune_old_queue_rows(cursor, days=30)
          Cleanup. Drops sent rows older than N days so the queue
          doesn't accrete forever.

DESIGN NOTES:
  - Per-artist per-window email_preferences continue to filter at
    enqueue time, so an artist who disabled venue_open_gig_4w never
    gets 4w gigs in their digest. The detector applies that filter
    before calling enqueue_open_gig_for_artist.
  - The digest also picks up the `notification_key` 'open_gig_36h' as
    the "urgent" badge — gigs with that key render with a 🚨 prefix
    in the email body. No separate 36h send path.
  - Radius-blast (blast_all_enabled) gigs are enqueued with
    via_radius=1; the email adds an "in your area" badge so the
    artist understands why they got it even without a preferred
    relationship.
  - Cancellation blasts (fire_cancelled_gig_blast in routes/gigs.py)
    DO NOT go through this digest — they're synchronous, urgent, and
    keep their own send path. They're a separate notification class.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("gigsfill.open_gig_digest")


def enqueue_open_gig_for_artist(cursor, *, user_id: int, artist_id: int,
                                gig_id: int, venue_id: int,
                                notification_key: str, via_radius: bool = False) -> bool:
    """Idempotent enqueue. Returns True on insert, False on dup."""
    try:
        cursor.execute(
            """INSERT OR IGNORE INTO artist_email_digest_queue
                (user_id, artist_id, gig_id, venue_id, notification_key, via_radius)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (int(user_id), int(artist_id), int(gig_id), int(venue_id),
             notification_key, 1 if via_radius else 0)
        )
        return (cursor.rowcount or 0) > 0
    except Exception as e:
        logger.warning(f"enqueue_open_gig_for_artist failed: {e}")
        return False


def _due_users(cursor, digest_hour: int) -> list[dict]:
    """Return users whose local-time hour == digest_hour AND who have
    pending queue rows. Resolves each user's timezone via their artists
    record (state → US_STATE_TIMEZONES → platform fallback).
    """
    # Pull every distinct user with unsent rows + their primary artist's
    # state. One user can own multiple artists; we use the first one's
    # state to pick a timezone — close enough for "is it 9am for them".
    rows = cursor.execute("""
        SELECT DISTINCT q.user_id, q.artist_id, a.state
        FROM artist_email_digest_queue q
        JOIN artists a ON a.id = q.artist_id
        WHERE q.sent_at IS NULL
    """).fetchall()
    if not rows:
        return []

    # Lazy imports — these utilities don't ship with the raw scheduler
    # process_open_gig path, so we only pay the cost when there's
    # actually work to do.
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return []
    from backend.utils import US_STATE_TIMEZONES

    # Read platform tz fallback once.
    platform_tz = "America/Los_Angeles"
    try:
        row = cursor.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_timezone'"
        ).fetchone()
        if row and row[0]:
            platform_tz = row[0]
    except Exception:
        pass

    due = []
    seen_users = set()
    for r in rows:
        user_id = r[0]
        if user_id in seen_users:
            continue
        seen_users.add(user_id)
        state = (r[2] or "").strip().upper()
        tz_str = US_STATE_TIMEZONES.get(state) or platform_tz
        try:
            tz = ZoneInfo(tz_str)
            local_hour = datetime.now(tz).hour
        except Exception:
            continue
        if local_hour == digest_hour:
            due.append({"user_id": user_id, "tz_str": tz_str})
    return due


def _fetch_user_queue(cursor, user_id: int) -> list[dict]:
    """Pull unsent queue rows for a user with all the gig+venue context
    the digest needs. Filters out gigs that are no longer 'open' (got
    booked between enqueue and send) — those rows are still marked
    sent_at so the queue stays clean.
    """
    rows = cursor.execute("""
        SELECT q.id, q.gig_id, q.venue_id, q.notification_key, q.via_radius,
               g.date, g.start_time, g.end_time, g.pay, g.title, g.status,
               g.artist_type, g.band_formats, g.styles,
               v.venue_name, v.city, v.state
        FROM artist_email_digest_queue q
        JOIN gigs g ON g.id = q.gig_id
        JOIN venues v ON v.id = q.venue_id
        WHERE q.user_id = ? AND q.sent_at IS NULL
        ORDER BY g.date ASC, q.venue_id ASC
    """, (user_id,)).fetchall()
    return [
        {
            "queue_id": r[0], "gig_id": r[1], "venue_id": r[2],
            "notification_key": r[3], "via_radius": bool(r[4]),
            "date": r[5], "start_time": r[6], "end_time": r[7],
            "pay": r[8], "title": r[9], "status": r[10],
            "artist_type": r[11], "band_formats": r[12], "styles": r[13],
            "venue_name": r[14], "city": r[15], "state": r[16],
        }
        for r in rows
    ]


def _mark_queue_rows_sent(cursor, queue_ids: list[int]) -> None:
    if not queue_ids:
        return
    placeholders = ",".join(["?"] * len(queue_ids))
    cursor.execute(
        f"UPDATE artist_email_digest_queue SET sent_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
        queue_ids
    )


def _render_digest_email(*, artist_name: str, rows: list[dict]) -> tuple[str, str]:
    """Render subject + HTML body. Groups by venue, sorts gigs within
    each venue by date. Tags 36h gigs with 🚨 and via_radius gigs with
    a small "in your area" badge.
    """
    # Group by venue_id
    by_venue: dict[int, dict] = {}
    for r in rows:
        v = by_venue.setdefault(r["venue_id"], {
            "venue_name": r["venue_name"],
            "city": r["city"], "state": r["state"],
            "gigs": []
        })
        v["gigs"].append(r)

    # Sort gigs within each venue
    for v in by_venue.values():
        v["gigs"].sort(key=lambda g: (str(g["date"] or ""), str(g["start_time"] or "")))

    # Subject
    gig_count = len(rows)
    venue_count = len(by_venue)
    subj = f"{gig_count} open gig{'s' if gig_count != 1 else ''} at {venue_count} venue{'s' if venue_count != 1 else ''} — book yours today"

    # Body
    def _esc(s):
        s = str(s if s is not None else "")
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def _fmt_date(d):
        try:
            dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
            return dt.strftime("%a, %b %d, %Y")
        except Exception:
            return str(d or "")

    def _fmt_time(t):
        try:
            h, m = (str(t)[:5]).split(":")
            h = int(h)
            ampm = "PM" if h >= 12 else "AM"
            h12 = h % 12 or 12
            return f"{h12}:{m} {ampm}"
        except Exception:
            return str(t or "")

    def _fmt_pay(p):
        try:
            return f"${float(p):.2f}"
        except Exception:
            return ""

    # Render each venue as a section. Inside the section, gigs sit in a
    # 4-column table so date / time / pay / urgency-tag line up
    # vertically across rows. Column widths are explicit so longer pay
    # strings ($1,250.00) don't push the urgency tag around.
    sections = []
    for v in by_venue.values():
        loc = ", ".join(filter(None, [_esc(v.get("city")), _esc(v.get("state"))]))
        venue_header = f"<strong>{_esc(v['venue_name'])}</strong>" + (
            f" <span style='color:#6b7280;'>· {loc}</span>" if loc else "")
        gig_rows = []
        for g in v["gigs"]:
            tag_html = ""
            if g["notification_key"] == "open_gig_36h":
                tag_html = "<span style='color:#dc2626;font-weight:600;'>Less Than 36 Hours!</span>"
            link_open = (
                f"<a href='https://gigsfill.com/app/artist-book-gigs.html?gig={g['gig_id']}' "
                f"style='color:#111827;text-decoration:none;'>"
            )
            gig_rows.append(
                "<tr>"
                f"<td style='padding:5px 14px 5px 0;font-size:14px;color:#111827;white-space:nowrap;'>{link_open}{_esc(_fmt_date(g['date']))}</a></td>"
                f"<td style='padding:5px 14px 5px 0;font-size:14px;color:#374151;white-space:nowrap;'>{link_open}{_esc(_fmt_time(g['start_time']))}</a></td>"
                f"<td style='padding:5px 14px 5px 0;font-size:14px;color:#374151;white-space:nowrap;'>{link_open}{_esc(_fmt_pay(g['pay']))}</a></td>"
                f"<td style='padding:5px 0;font-size:13px;'>{tag_html}</td>"
                "</tr>"
            )
        sections.append(
            "<div style='margin:18px 0;padding-bottom:14px;border-bottom:1px solid #e5e7eb;'>"
            f"<div style='font-size:15px;margin-bottom:8px;'>{venue_header}</div>"
            "<table role='presentation' cellspacing='0' cellpadding='0' border='0' style='border-collapse:collapse;'>"
            f"{''.join(gig_rows)}"
            "</table>"
            "</div>"
        )

    body = f"""\
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 28px 40px;">
<p style="margin: 0 0 12px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">{_esc(artist_name)}, here's a list of open gigs within the next month:</p>
{''.join(sections)}
<p style="margin: 20px 0 0 0; font-size: 13px; color: #6b7280; line-height: 1.5;">You're getting this because you have notifications enabled. Update your preferences any time on <a href="https://gigsfill.com/app/user-profile.html?tab=settings" style="color: #3b82f6;">your profile</a>.</p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
"""
    return subj, body


def send_daily_artist_digest(cursor, smtp_config) -> int:
    """Send the morning digest for any user whose local hour matches the
    configured digest_hour. Returns the number of emails sent.
    """
    # Feature flag — honored at every level so admins can flip it off
    # mid-day without restarting the scheduler.
    enabled = cursor.execute(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'open_gig_daily_digest_enabled'"
    ).fetchone()
    if not (enabled and (enabled[0] or "").lower() in ("true", "1")):
        return 0
    hour_row = cursor.execute(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'open_gig_daily_digest_hour'"
    ).fetchone()
    try:
        digest_hour = int((hour_row[0] if hour_row else "9").strip())
    except Exception:
        digest_hour = 9

    due = _due_users(cursor, digest_hour)
    if not due:
        return 0

    from backend.scheduler import send_email  # reuse the same SMTP path

    sent = 0
    for u in due:
        user_id = u["user_id"]
        rows = _fetch_user_queue(cursor, user_id)
        if not rows:
            continue

        # Last-second filter: skip rows whose gig is no longer 'open'
        # (booked between enqueue and send). Mark them sent so they
        # drop out of the queue.
        stale_ids = [r["queue_id"] for r in rows if r["status"] != "open"]
        live_rows = [r for r in rows if r["status"] == "open"]
        if stale_ids:
            _mark_queue_rows_sent(cursor, stale_ids)
            cursor.connection.commit()

        if not live_rows:
            continue

        # Look up the user's email + first artist's display name.
        meta = cursor.execute(
            """SELECT u.email, a.name
                 FROM users u
                 JOIN artists a ON a.user_id = u.id
                WHERE u.id = ?
                ORDER BY a.id ASC LIMIT 1""",
            (user_id,)
        ).fetchone()
        if not meta or not meta[0]:
            # No email on the user — mark queue rows sent so we don't
            # keep trying; admin can re-queue manually if needed.
            _mark_queue_rows_sent(cursor, [r["queue_id"] for r in live_rows])
            cursor.connection.commit()
            continue
        artist_email, artist_name = meta[0], (meta[1] or "there")

        # Render + send
        subj, body = _render_digest_email(artist_name=artist_name, rows=live_rows)
        try:
            ok = send_email(smtp_config, artist_email, subj, body)
        except Exception as e:
            logger.error(f"digest send failed for user={user_id}: {e}", exc_info=True)
            continue
        if ok:
            _mark_queue_rows_sent(cursor, [r["queue_id"] for r in live_rows])
            cursor.connection.commit()
            sent += 1
            logger.info(
                f"[DIGEST] sent to user={user_id} email={artist_email} "
                f"venues={len({r['venue_id'] for r in live_rows})} gigs={len(live_rows)}"
            )
    return sent


def prune_old_queue_rows(cursor, days: int = 30) -> int:
    """Drop sent rows older than `days` so the queue doesn't grow
    forever. Unsent rows are NEVER pruned — they'll get sent the next
    time their owner's local hour matches.
    """
    try:
        cursor.execute(
            "DELETE FROM artist_email_digest_queue "
            "WHERE sent_at IS NOT NULL AND sent_at < datetime('now', ?)",
            (f"-{int(days)} days",)
        )
        return cursor.rowcount or 0
    except Exception as e:
        logger.warning(f"prune_old_queue_rows failed: {e}")
        return 0
