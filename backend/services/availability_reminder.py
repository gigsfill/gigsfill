"""Weekly "update your availability" reminder.

2026-10-07. Band members mark the days they cannot play on the band calendar.
That data only helps if it stays ahead of the dates venues are booking into,
and nothing was prompting anyone to keep it current.

**What triggers a reminder: the coverage horizon**, not the last time someone
touched the calendar. Those differ in ways that matter:

  • Last-activity would nag a member who sat down last month and marked a
    whole year ahead. Their availability is in excellent shape; the metric
    just cannot see it.
  • Coverage horizon asks the question that actually matters — "do we know
    whether this member is free over the window venues are booking?" — and a
    member with nothing marked at all answers it the same way as a member
    whose marks run out next week: we do not know.

**The always-available problem.** A member who is genuinely free for months
has nothing to mark, so their horizon never moves and the reminder would
chase them forever on exactly the evidence that they are available. The email
therefore carries a one-click "I'm all clear" link that stamps `last_ack_at`
and buys ACK_QUIET_DAYS of silence. Without it this is nagware.

The job is idempotent and safe to call every scheduler tick: it only sends on
the configured weekday+hour, and `last_sent_at` rate-limits regardless.
"""
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger("gigsfill.availability_reminder")

# A member is "covered" when their marked days reach at least this far ahead.
# Venues commonly book 1–2 months out, so a horizon inside this window means
# the band genuinely does not know whether they are free for gigs on offer.
HORIZON_DAYS = 60
# Never twice in a week, however many bands they play in.
MIN_GAP_DAYS = 7
# Silence bought by clicking "I'm all clear".
ACK_QUIET_DAYS = 30
# Stop after this many unanswered reminders. Someone who has ignored six
# weekly emails is not going to act on the seventh, and continuing only
# teaches them to filter us.
MAX_UNANSWERED = 6


def _rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchall()


def members_needing_reminder(cursor, today=None):
    """Members whose availability does not reach HORIZON_DAYS ahead.

    Returns [{user_id, email, name, horizon, days_covered, marked_ahead}].
    `horizon` is None when they have never marked a future day — which is not
    the same as being unavailable, only that we cannot tell.
    """
    today = today or date.today()
    cutoff = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    gap_before = (datetime.utcnow() - timedelta(days=MIN_GAP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    ack_before = (datetime.utcnow() - timedelta(days=ACK_QUIET_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

    # Only people who actually play in a band — a venue-only user has no
    # availability to keep current.
    rows = _rows(cursor, """
        SELECT u.id,
               u.email,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) AS name,
               (SELECT MAX(d.day) FROM member_days_off d
                 WHERE d.user_id = u.id AND date(d.day) >= date(?)) AS horizon,
               (SELECT COUNT(*) FROM member_days_off d
                 WHERE d.user_id = u.id AND date(d.day) >= date(?)) AS marked_ahead,
               r.last_sent_at, r.last_ack_at, COALESCE(r.send_count, 0)
        FROM users u
        LEFT JOIN availability_reminders r ON r.user_id = u.id
        WHERE u.email IS NOT NULL AND TRIM(u.email) != ''
          AND u.id IN (
            SELECT user_id FROM artists WHERE user_id IS NOT NULL AND deleted_at IS NULL
            UNION
            SELECT e.user_id FROM entity_users e
            JOIN artists a ON a.id = e.entity_id AND a.deleted_at IS NULL
            WHERE e.entity_type = 'artist'
          )
    """, (today.isoformat(), today.isoformat()))

    out = []
    for uid, email, name, horizon, marked_ahead, last_sent, last_ack, sends in rows:
        if horizon and str(horizon)[:10] >= cutoff:
            continue                                  # covered far enough ahead
        if last_sent and str(last_sent) > gap_before:
            continue                                  # reminded recently
        if last_ack and str(last_ack) > ack_before:
            continue                                  # said they are all clear
        if int(sends or 0) >= MAX_UNANSWERED:
            continue                                  # stop pestering
        days_covered = None
        if horizon:
            try:
                days_covered = (date.fromisoformat(str(horizon)[:10]) - today).days
            except ValueError:
                days_covered = None
        out.append({
            "user_id": uid, "email": email, "name": name,
            "horizon": str(horizon)[:10] if horizon else None,
            "days_covered": days_covered,
            "marked_ahead": int(marked_ahead or 0),
        })
    return out


def bands_for(cursor, user_id):
    rows = _rows(cursor, """
        SELECT a.name FROM artists a
        WHERE a.user_id = ? AND a.deleted_at IS NULL
        UNION
        SELECT a.name FROM artists a
        JOIN entity_users e ON e.entity_id = a.id AND e.entity_type = 'artist'
        WHERE e.user_id = ? AND a.deleted_at IS NULL
    """, (user_id, user_id))
    return [r[0] for r in rows if r[0]]


def upcoming_ranges(cursor, user_id, today=None, limit=6):
    """The member's own marked days ahead, consecutive runs merged.

    Same shape the calendar's "All dates" sheet shows, so the email agrees
    with the screen it links to.
    """
    today = today or date.today()
    days = sorted({str(r[0])[:10] for r in _rows(cursor, """
        SELECT day FROM member_days_off
        WHERE user_id = ? AND date(day) >= date(?)
        ORDER BY day
    """, (user_id, today.isoformat()))})
    runs = []
    for d in days:
        try:
            cur = date.fromisoformat(d)
        except ValueError:
            continue
        if runs and (cur - runs[-1][1]).days == 1:
            runs[-1][1] = cur
        else:
            runs.append([cur, cur])
    def fmt(d):
        return d.strftime("%a, %b ") + str(d.day)
    return [(fmt(a) if a == b else f"{fmt(a)} – {fmt(b)}") for a, b in runs[:limit]], len(runs)


def mark_sent(cursor, user_id):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO availability_reminders (user_id, last_sent_at, send_count)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET
            last_sent_at = excluded.last_sent_at,
            send_count = availability_reminders.send_count + 1
    """, (user_id, now))


def record_ack(cursor, user_id):
    """They clicked "I'm all clear". Resets send_count too: the reminder did
    its job, so the next lapse starts from a clean slate rather than inheriting
    a count that would silence them early."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO availability_reminders (user_id, last_ack_at, send_count)
        VALUES (?, ?, 0)
        ON CONFLICT(user_id) DO UPDATE SET
            last_ack_at = excluded.last_ack_at,
            send_count = 0
    """, (user_id, now))


def clear_on_update(cursor, user_id):
    """Called when a member marks a day. Same reasoning as record_ack: they
    engaged, so the unanswered counter should not carry forward."""
    cursor.execute("""
        INSERT INTO availability_reminders (user_id, send_count) VALUES (?, 0)
        ON CONFLICT(user_id) DO UPDATE SET send_count = 0
    """, (user_id,))


# ── sending ─────────────────────────────────────────────────────────────────
# The ack link is signed rather than carrying a raw user id, so clicking it
# cannot be forged for someone else. Its own salt, so a token minted here can
# never be replayed against the email-verify or password-reset flows, and a
# max_age so a leaked link in an old mailbox stops working.
_ACK_SALT = "availability-all-clear"
_ACK_MAX_AGE = 60 * 60 * 24 * 60      # 60 days


def _serializer():
    from itsdangerous import URLSafeTimedSerializer
    from backend.routes.auth import _SECRET_KEY
    return URLSafeTimedSerializer(_SECRET_KEY, salt=_ACK_SALT)


def make_ack_token(user_id: int) -> str:
    return _serializer().dumps({"uid": int(user_id)})


def read_ack_token(token: str):
    """Returns the user id, or None for anything invalid, tampered or expired."""
    try:
        data = _serializer().loads(token, max_age=_ACK_MAX_AGE)
        return int(data["uid"])
    except Exception:
        return None


def _horizon_sentence(m):
    """Say what we actually know, which is not the same as "you are busy"."""
    if not m["horizon"]:
        return ("you haven't marked any days you're unavailable, so your band "
                "has no idea which dates to avoid.")
    d = m["days_covered"]
    if d is None or d < 0:
        return "your availability looks out of date."
    if d <= 7:
        return (f"your availability only runs {d} more day{'s' if d != 1 else ''}. "
                "Anything past that, your band is guessing.")
    return (f"your availability runs out in about {d} days. Venues often book "
            "further ahead than that.")


def send_availability_reminders(cursor, smtp_config, site_url="https://gigsfill.com",
                                today=None, limit=200):
    """Send to everyone whose coverage has lapsed. Returns the count sent.

    Idempotent per member via last_sent_at, so calling it twice in an hour
    sends nothing the second time.
    """
    from backend.scheduler import get_template, render_template, send_email

    # get_template falls back to a `notification_type` column that does not
    # exist on this schema, so a missing row raises rather than returning None
    # and the `if not tpl` below would never run.
    try:
        tpl = get_template(cursor, "member_availability_reminder")
    except Exception:
        tpl = None
    if not tpl:
        logger.warning("[AVAIL] template member_availability_reminder missing; nothing sent")
        return 0

    due = members_needing_reminder(cursor, today=today)[:limit]
    sent = 0
    for m in due:
        # Respect the member's own email settings. Unknown == opted in, which
        # matches how the other notification types here behave.
        cursor.execute(
            "SELECT enabled FROM email_preferences WHERE user_id = ? AND notification_type = ?",
            (m["user_id"], "member_availability_reminder"))
        pref = cursor.fetchone()
        if pref and str(pref[0]).lower() in ("0", "false", "no"):
            continue

        bands = bands_for(cursor, m["user_id"])
        if not bands:
            continue
        ranges, total_runs = upcoming_ranges(cursor, m["user_id"], today=today)

        if ranges:
            items = "".join(
                f'<li style="margin:0 0 4px 0;">{r}</li>' for r in ranges)
            more = ("" if total_runs <= len(ranges)
                    else f'<li style="margin:0;color:#9ca3af;">and {total_runs - len(ranges)} more</li>')
            marked_block = (
                '<p style="margin:0 0 8px 0;font-size:14px;color:#4b5563;">'
                "Days you've already marked:</p>"
                '<ul style="margin:0 0 20px 0;padding-left:20px;font-size:14px;'
                'line-height:1.6;color:#4b5563;">' + items + more + "</ul>")
        else:
            marked_block = ""

        token = make_ack_token(m["user_id"])
        variables = {
            "subject_line": ("Your GigsFill availability is running out"
                             if m["horizon"] else "Let your band know when you're free"),
            "member_name": m["name"],
            "band_names": ", ".join(bands),
            "horizon_sentence": _horizon_sentence(m),
            "marked_block": marked_block,
            "calendar_url": f"{site_url}/app/user-profile.html?tab=availability",
            "all_clear_url": f"{site_url}/api/availability/all-clear?token={token}",
            "prefs_url": f"{site_url}/app/user-profile.html?tab=notifications",
        }
        subject = render_template(tpl["subject"], variables)
        body = render_template(tpl["body"], variables)
        if send_email(smtp_config, m["email"], subject, body):
            mark_sent(cursor, m["user_id"])
            sent += 1
        else:
            logger.warning("[AVAIL] send failed for user %s", m["user_id"])
    if sent:
        logger.info("[AVAIL] availability reminders sent: %s", sent)
    return sent
