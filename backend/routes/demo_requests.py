"""Demo request queue.

Prospects hit `POST /api/demo-request` from the homepage modal with up
to 3 preferred time slots (date + morning/afternoon/evening bucket).
Admin gets an email with 3 one-click HMAC-signed accept links AND can
manage from the admin panel. When admin accepts a slot, the prospect
gets a confirmation email + ICS attachment.

Time slots are Pacific. ICS invite is generated
server-side so any calendar app can consume it.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text
from datetime import datetime, timedelta, timezone as _tz
import json
import logging
import os
import re

from backend.db import get_db, get_db_connection
from backend.routes.admin import check_admin
from backend.rate_limiter import limiter
from backend.utils import log_admin_action

logger = logging.getLogger("gigsfill.demo_requests")

router = APIRouter()

# HMAC-signed accept-link tokens. 30-day TTL — plenty for a prospect
# scheduling 1-2 weeks out. Signing key comes from GIGSFILL_SECRET_KEY,
# same rule as auth.py.
_SECRET_KEY = os.environ.get("GIGSFILL_SECRET_KEY", "")
if not _SECRET_KEY:
    try:
        from backend.routes.auth import _SECRET_KEY as _auth_key
        _SECRET_KEY = _auth_key
    except Exception:
        pass
_accept_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="demo-accept")
_ACCEPT_MAX_AGE = 60 * 60 * 24 * 30

# Jul 18 2026: prospect-side cancel + reschedule links live in the
# confirmation email. TTL = 60 days so a demo scheduled 45 days out
# can still be modified up to 15 days after; if a request has been
# rescheduled several times the token is minted fresh each round.
_cancel_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="demo-cancel")
_reschedule_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="demo-reschedule")
_PROSPECT_LINK_MAX_AGE = 60 * 60 * 24 * 60

# Signed one-click accept link from the DECLINE email — different salt
# from the admin's own accept links so a leaked prospect-accept token
# can't be used to accept from a preferred_slots array. Encodes
# (req_id, slot_index_into_admin_suggested_slots).
_prospect_accept_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="demo-prospect-accept")

# Legacy bucket labels retained ONLY for rendering historic rows submitted
# before Jul 16 2026 (when we switched to specific hour+minute pickers).
# New rows carry `{"date": "YYYY-MM-DD", "time": "HH:MM"}` in 24-hour
# format (Pacific). Bucket path is a defensive fallback — no new writes.
_BUCKETS = {
    "morning":   {"label": "Morning (9:00 AM – 12:00 PM PT)",   "start_hour": 10},
    "afternoon": {"label": "Afternoon (12:00 – 4:00 PM PT)",     "start_hour": 13},
    "evening":   {"label": "Evening (4:00 – 7:00 PM PT)",        "start_hour": 17},
}

_ALLOWED_START_HOURS = tuple(range(9, 18))  # 9 AM – 5 PM inclusive
_ALLOWED_MINUTES = (0, 15, 30, 45)


def _slot_hour_minute(slot: dict) -> tuple[int, int]:
    """Extract (hour, minute) from a slot dict, supporting both the new
    `{date, time}` shape AND the legacy `{date, bucket}` shape."""
    t = (slot.get("time") or "").strip()
    if t:
        try:
            hh, mm = t.split(":")
            return int(hh), int(mm)
        except Exception:
            pass
    b = slot.get("bucket")
    if b in _BUCKETS:
        return _BUCKETS[b]["start_hour"], 0
    return 10, 0


def _slot_is_past(slot: dict) -> bool:
    """True when the slot's (date, time) is earlier than now in Pacific time.
    Used by admin UI to grey out slots and by the accept endpoint as a
    defense-in-depth guard. Missing/invalid dates → not past (fail-open so
    a bad row doesn't silently disable an accept button)."""
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo("America/Los_Angeles")
        d = (slot.get("date") or "").strip()
        if not d:
            return False
        hh, mm = _slot_hour_minute(slot)
        slot_dt = _dt.strptime(d, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=pacific)
        return slot_dt < _dt.now(pacific)
    except Exception:
        return False

_ADMIN_EMAIL_DEFAULT = "jcarta@gigsfill.com"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ─────────────────────────── helpers ───────────────────────────

def _sign_accept_token(req_id: int, slot_index: int, slots_version: int = 1) -> str:
    # slots_version pins the token to the current preferred_slots — a
    # reschedule bumps the row's slots_version, invalidating stale
    # accept links from earlier admin emails.
    return _accept_serializer.dumps({
        "req_id": int(req_id),
        "slot": int(slot_index),
        "v": int(slots_version),
    })


def _verify_accept_token(token: str) -> dict:
    try:
        return _accept_serializer.loads(token, max_age=_ACCEPT_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(410, "This accept link has expired.")
    except BadSignature:
        raise HTTPException(400, "Invalid accept link.")


def _sign_cancel_token(req_id: int) -> str:
    return _cancel_serializer.dumps({"req_id": int(req_id)})


def _verify_cancel_token(token: str) -> int:
    try:
        payload = _cancel_serializer.loads(token, max_age=_PROSPECT_LINK_MAX_AGE)
        return int(payload.get("req_id") or 0)
    except SignatureExpired:
        raise HTTPException(410, "This link has expired.")
    except BadSignature:
        raise HTTPException(400, "Invalid link.")


def _sign_reschedule_token(req_id: int) -> str:
    return _reschedule_serializer.dumps({"req_id": int(req_id)})


def _verify_reschedule_token(token: str) -> int:
    try:
        payload = _reschedule_serializer.loads(token, max_age=_PROSPECT_LINK_MAX_AGE)
        return int(payload.get("req_id") or 0)
    except SignatureExpired:
        raise HTTPException(410, "This link has expired.")
    except BadSignature:
        raise HTTPException(400, "Invalid link.")


def _sign_prospect_accept_token(req_id: int, slot_index: int) -> str:
    """Sign a link that lets the prospect accept ONE of admin's
    counter-proposed slots (indexed into `admin_suggested_slots_json`).
    Slot index is bound to the token to prevent slot-swapping shenanigans."""
    return _prospect_accept_serializer.dumps({
        "req_id": int(req_id),
        "slot": int(slot_index),
    })


def _verify_prospect_accept_token(token: str) -> tuple[int, int]:
    try:
        payload = _prospect_accept_serializer.loads(token, max_age=_PROSPECT_LINK_MAX_AGE)
        return int(payload.get("req_id") or 0), int(payload.get("slot") or 0)
    except SignatureExpired:
        raise HTTPException(410, "This link has expired.")
    except BadSignature:
        raise HTTPException(400, "Invalid link.")


def _append_history(db, req_id: int, entry: dict) -> None:
    """Append one action entry to the row's history_json array.
    Safe if the column is null or contains malformed JSON."""
    try:
        cur = db.execute(
            text("SELECT history_json FROM demo_requests WHERE id = :id"),
            {"id": req_id}
        ).scalar()
        existing = []
        if cur:
            try:
                existing = json.loads(cur) or []
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        entry = dict(entry)
        entry.setdefault("ts", datetime.utcnow().isoformat(timespec="seconds"))
        existing.append(entry)
        db.execute(
            text("UPDATE demo_requests SET history_json = :h WHERE id = :id"),
            {"h": json.dumps(existing), "id": req_id}
        )
    except Exception as e:
        logger.warning(f"history append failed for demo #{req_id}: {e}")


def _get_site_url(db) -> str:
    try:
        row = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key='site_url'")).scalar()
        if row:
            return str(row).rstrip("/")
    except Exception:
        pass
    return "https://gigsfill.com"


def _fmt_slot_human(slot: dict) -> str:
    """Render a slot for display in emails / admin panel.

    New shape `{date, time: "HH:MM"}` → 'Mon, Jul 20 · 1:15 PM PT'
    Legacy   `{date, bucket}`         → 'Mon, Jul 20 · Morning (9:00 AM – 12:00 PM PT)'
    """
    try:
        d = datetime.strptime(slot["date"], "%Y-%m-%d")
        date_str = d.strftime("%a, %b %d")
    except Exception:
        date_str = slot.get("date") or "?"
    if slot.get("time"):
        hh, mm = _slot_hour_minute(slot)
        h12 = hh - 12 if hh > 12 else (12 if hh == 0 else hh)
        ampm = "AM" if hh < 12 else "PM"
        return f"{date_str} · {h12}:{mm:02d} {ampm} PT"
    bucket = _BUCKETS.get(slot.get("bucket"), {"label": slot.get("bucket") or "?"})
    return f"{date_str} · {bucket['label']}"


def _validate_slots(raw) -> list[dict]:
    """Normalize + validate up to 3 slot dicts. Raises 400 on any issue."""
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "Please pick at least one preferred time slot.")
    if len(raw) > 3:
        raise HTTPException(400, "Please pick at most 3 preferred time slots.")
    out = []
    today = datetime.now(_tz.utc).date()
    max_date = today + timedelta(days=45)  # loose ceiling
    seen = set()
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            raise HTTPException(400, f"Slot #{i+1} is malformed.")
        date_str = (s.get("date") or "").strip()
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(400, f"Slot #{i+1} needs a valid date (YYYY-MM-DD).")
        if d < today:
            raise HTTPException(400, f"Slot #{i+1} is in the past.")
        if d > max_date:
            raise HTTPException(400, f"Slot #{i+1} is too far out (max 45 days).")

        # NEW shape (as of Jul 16 2026): `{date, time: "HH:MM"}` in
        # 24-hour Pacific, hour 9-17 inclusive, minutes 00/15/30/45.
        # LEGACY shape `{date, bucket}` still accepted for pre-migration
        # clients but no browser sends it after the modal update.
        time_str = (s.get("time") or "").strip()
        bucket = (s.get("bucket") or "").strip().lower()
        key = None
        if time_str:
            _m = re.match(r"^(\d{1,2}):(\d{2})$", time_str)
            if not _m:
                raise HTTPException(400, f"Slot #{i+1} has an invalid time (need HH:MM).")
            hh, mm = int(_m.group(1)), int(_m.group(2))
            if hh not in _ALLOWED_START_HOURS:
                raise HTTPException(400, f"Slot #{i+1} start hour must be between 9 AM and 5 PM PT.")
            if mm not in _ALLOWED_MINUTES:
                raise HTTPException(400, f"Slot #{i+1} minutes must be 00, 15, 30, or 45.")
            norm_time = f"{hh:02d}:{mm:02d}"
            key = (date_str, norm_time)
            out.append({"date": date_str, "time": norm_time})
        elif bucket:
            if bucket not in _BUCKETS:
                raise HTTPException(400, f"Slot #{i+1} has an invalid time bucket.")
            key = (date_str, bucket)
            out.append({"date": date_str, "bucket": bucket})
        else:
            raise HTTPException(400, f"Slot #{i+1} needs a start time.")

        if key in seen:
            raise HTTPException(400, f"Duplicate slot on {date_str}.")
        seen.add(key)
    return out


def _get_admin_email(db) -> str:
    try:
        row = db.execute(text(
            "SELECT setting_value FROM platform_settings WHERE setting_key='demo_request_admin_email'"
        )).scalar()
        if row and _EMAIL_RE.match(row):
            return row
    except Exception:
        pass
    return _ADMIN_EMAIL_DEFAULT


def _get_platform_meeting_url(db) -> str:
    """Platform-wide default video-call URL (Teams / Zoom / Meet).
    Returned as-is; validation happens in the settings PUT handler."""
    try:
        row = db.execute(text(
            "SELECT setting_value FROM platform_settings WHERE setting_key='demo_meeting_url'"
        )).scalar()
        if row:
            return str(row).strip()
    except Exception:
        pass
    return ""


def _get_meeting_url(row: dict, db) -> str:
    """Resolve the video-call URL for a specific demo row. Per-row
    `meeting_url` wins, then platform default. Returns "" if neither is
    set — callers render "we'll send the link before the demo" copy in
    that case so the email is never awkward."""
    per = (row.get("meeting_url") or "").strip() if isinstance(row, dict) else ""
    if per:
        return per
    return _get_platform_meeting_url(db)


def _fmt_meeting_line(meeting_url: str) -> str:
    """Small HTML fragment used inside every confirmation/reminder body
    so all emails treat the join link consistently — either a bold
    "Join demo" panel with the URL, or fallback copy that keeps the
    body sane when no URL is configured yet.

    Jul 22 2026: when the URL is a Microsoft Teams link, append an
    Apple-user warning — Teams meetings genuinely do NOT work in iOS
    Safari (iPad/iPhone force the App Store install prompt), and Mac
    Safari has partial support with unreliable audio/screen-share.
    Skipped for Zoom (works fine in Safari) and Meet (also fine in
    Safari). Detection is a plain substring check on the two hostnames
    Microsoft actually uses for Teams meeting links."""
    import html as _h
    if not meeting_url:
        return ""
    esc = _h.escape(meeting_url)
    _mu_low = meeting_url.lower()
    is_teams = ("teams.microsoft.com" in _mu_low) or ("teams.live.com" in _mu_low)
    apple_note = ""
    if is_teams:
        apple_note = (
            '<div style="margin-top:10px;padding:10px 12px;background:rgba(245,158,11,0.08);'
            'border:1px solid rgba(245,158,11,0.3);border-radius:6px;font-size:12px;color:#fbbf24;line-height:1.5;">'
            '🍎 <strong>Apple device?</strong> Teams meetings don\'t work in Safari on iPhone/iPad — '
            'please install the free '
            '<a href="https://apps.apple.com/us/app/microsoft-teams/id1113153706" '
            'style="color:#fbbf24;text-decoration:underline;">Microsoft Teams app</a> '
            'from the App Store before your demo. On a Mac, we recommend Chrome, Edge, or the Teams desktop app '
            '(Safari partially works but audio and screen-share are unreliable).'
            '</div>'
        )
    return (
        '<div style="margin:16px 0;padding:14px 18px;background:linear-gradient(135deg,rgba(139,92,246,0.06),rgba(6,182,212,0.06));border:1px solid rgba(6,182,212,0.3);border-radius:8px;">'
        '<div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:6px;">Join the demo</div>'
        f'<a href="{esc}" style="color:#7dd3fc;font-size:14px;font-weight:600;text-decoration:none;word-break:break-all;">{esc}</a>'
        '<div style="margin-top:6px;font-size:12px;color:#94a3b8;">No account required — click the link at your scheduled time. Opens in your browser (Teams / Zoom / Meet).</div>'
        f'{apple_note}'
        '</div>'
    )


def _get_smtp(db):
    """Reuse the app's SMTP path. Returns a dict compatible with scheduler.send_email."""
    from backend.db import get_db_connection as _c
    conn = _c()
    try:
        cur = conn.cursor()
        from backend.scheduler import get_smtp_settings
        return get_smtp_settings(cur)
    finally:
        conn.close()


def _send_email_via_smtp(smtp, to_email: str, subject: str, html_body: str,
                          attachments: list[tuple[str, str, str]] = None) -> bool:
    """attachments: list of (filename, mime_type, content_str).

    Uses the same key shape as `backend.scheduler.get_smtp_settings`
    (`server`, `port`, `username`, `password`, `from_email`, `from_name`)
    and mirrors its port-handling (465 SSL, 587/2587 STARTTLS, else
    plain with optional STARTTLS fallback).
    """
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders as _enc
        from email.utils import formataddr
        import smtplib

        if not smtp or not smtp.get("username") or not smtp.get("password"):
            logger.warning("SMTP not configured — skipping demo-request email")
            return False

        msg = MIMEMultipart("mixed")
        _fn = smtp.get("from_name") or ""
        _fe = smtp.get("from_email") or smtp.get("username") or "noreply@gigsfill.com"
        msg["From"] = formataddr((_fn or "GigsFill", _fe)) if _fn else _fe
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["X-Mailer"] = "GigsFill"

        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(html_body, "html"))
        msg.attach(alt)

        for (fname, mtype, content) in (attachments or []):
            mtype_main, mtype_sub = (mtype.split("/", 1) + ["octet-stream"])[:2]
            part = MIMEBase(mtype_main, mtype_sub)
            part.set_payload(content.encode("utf-8"))
            _enc.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
            msg.attach(part)

        server_host = smtp.get("server") or "smtp.gmail.com"
        port = int(smtp.get("port") or 587)
        user = smtp.get("username") or ""
        pw = smtp.get("password") or ""

        if port == 465:
            with smtplib.SMTP_SSL(server_host, port, timeout=15) as srv:
                srv.login(user, pw)
                srv.send_message(msg)
        elif port in (587, 2587):
            with smtplib.SMTP(server_host, port, timeout=15) as srv:
                srv.ehlo(); srv.starttls(); srv.ehlo()
                srv.login(user, pw)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(server_host, port, timeout=15) as srv:
                srv.ehlo()
                try:
                    srv.starttls(); srv.ehlo()
                except Exception:
                    pass
                srv.login(user, pw)
                srv.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"demo-request email send failed to {to_email}: {e}", exc_info=True)
        return False


def _ics_endpoint_serializer():
    # Lazy-defined so it picks up the module-level _SECRET_KEY after
    # the auth-fallback runs. Salt is dedicated so a compromised
    # ics-link doesn't leak reschedule/cancel authority.
    return URLSafeTimedSerializer(_SECRET_KEY, salt="demo-ics")


def _sign_ics_token(req_id: int, slot_index: int) -> str:
    return _ics_endpoint_serializer().dumps({
        "req_id": int(req_id),
        "slot": int(slot_index),
    })


def _verify_ics_token(token: str) -> tuple[int, int]:
    try:
        payload = _ics_endpoint_serializer().loads(token, max_age=_PROSPECT_LINK_MAX_AGE)
        return int(payload.get("req_id") or 0), int(payload.get("slot") or 0)
    except SignatureExpired:
        raise HTTPException(410, "This calendar link has expired.")
    except BadSignature:
        raise HTTPException(400, "Invalid calendar link.")


def _build_calendar_links(req_row: dict, slot: dict, site_url: str,
                            meeting_url: str = "") -> dict:
    """Return `{google, outlook, ics}` deep-link URLs for a slot.

    - **Google**: `calendar.google.com/render?action=TEMPLATE` with
      naive-Pacific dates + `ctz=America/Los_Angeles` — Google renders
      the event in the viewer's local TZ but the underlying wall-clock
      time is anchored to Pacific, so DST rollovers stay honest.
    - **Outlook**: `outlook.office.com/calendar/action/compose` with
      UTC ISO dates. This host works for personal outlook.com AND
      corporate Office 365; if the user isn't logged in Outlook
      redirects to the appropriate sign-in screen and then completes
      the add. `outlook.live.com` is a personal-only alternative but
      the `.office.com` path is friendlier for admin's mixed use.
    - **ICS**: signed link to `/demo/ics/{token}` which returns the
      exact same VCALENDAR content the email attaches — useful for
      Apple Calendar / Fantastical / anything that doesn't have a
      first-class web deep-link.
    """
    from urllib.parse import urlencode, quote
    from datetime import datetime as _dt, timedelta as _td
    try:
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo("America/Los_Angeles")
    except Exception:
        pacific = None

    try:
        d = _dt.strptime(slot["date"], "%Y-%m-%d")
        hh, mm = _slot_hour_minute(slot)
        start_local = d.replace(hour=hh, minute=mm, second=0)
    except Exception:
        start_local = _dt.now().replace(hour=10, minute=0, second=0, microsecond=0)
    end_local = start_local + _td(minutes=45)

    subject = f"GigsFill Demo — {req_row.get('name','')}".strip()
    # Meeting URL — if set, becomes the calendar entry's Location (so
    # Google/Outlook/Apple render a clickable "join here" link) AND is
    # prepended to the body so it's easy to grab from any calendar UI.
    location = meeting_url if meeting_url else "Video call (link sent before the demo)"
    body_prefix = f"Join: {meeting_url}\n\n" if meeting_url else ""
    body = (
        f"{body_prefix}"
        f"Live demo of GigsFill.\n\n"
        f"Requested by: {req_row.get('name','')} <{req_row.get('email','')}>\n"
        f"Entity: {req_row.get('entity_name','') or '—'} "
        f"({req_row.get('entity_type','') or '—'})\n"
        f"Notes: {req_row.get('notes','') or '—'}\n\n"
        f"Manage: {site_url or 'https://gigsfill.com'}"
    )

    # Google — naive Pacific with ctz. Format: YYYYMMDDTHHmmss (no Z).
    g_start = start_local.strftime("%Y%m%dT%H%M%S")
    g_end = end_local.strftime("%Y%m%dT%H%M%S")
    google_qs = urlencode({
        "action": "TEMPLATE",
        "text": subject,
        "dates": f"{g_start}/{g_end}",
        "details": body,
        "location": location,
        "ctz": "America/Los_Angeles",
    }, quote_via=quote)
    google_url = f"https://calendar.google.com/calendar/render?{google_qs}"

    # Outlook — UTC ISO. Use `outlook.office.com` so both personal
    # outlook.com AND corporate O365 flows work off one URL.
    if pacific:
        start_utc = start_local.replace(tzinfo=pacific).astimezone(_tz.utc)
        end_utc = end_local.replace(tzinfo=pacific).astimezone(_tz.utc)
    else:
        # Fallback: pretend Pacific was UTC-8 (worst case: 1h off during DST).
        start_utc = start_local - _td(hours=8)
        end_utc = end_local - _td(hours=8)
    o_start = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    o_end = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    outlook_qs = urlencode({
        "path": "/calendar/action/compose",
        "rru": "addevent",
        "subject": subject,
        "startdt": o_start,
        "enddt": o_end,
        "body": body,
        "location": location,
        "allday": "false",
    }, quote_via=quote)
    outlook_url = f"https://outlook.office.com/calendar/deeplink/compose?{outlook_qs}"

    # ICS — signed direct download so a forwarded email doesn't
    # leak a permanent world-readable link (still anyone-with-token,
    # but the token expires with the 60-day window).
    try:
        # slot_index isn't in the slot dict; caller doesn't know it.
        # But every slot the prospect sees is the one just accepted,
        # so we can key by scheduled_slot_index if set, otherwise 0.
        slot_index = int(req_row.get("scheduled_slot_index") or 0)
    except Exception:
        slot_index = 0
    ics_token = _sign_ics_token(int(req_row.get("id") or 0), slot_index)
    ics_url = f"{(site_url or 'https://gigsfill.com').rstrip('/')}/demo/ics/{ics_token}"

    return {"google": google_url, "outlook": outlook_url, "ics": ics_url}


def _build_calendar_buttons_html(links: dict) -> str:
    """Three-button strip used inside every prospect + admin email that
    needs 'add this to my calendar'. Buttons render side-by-side on
    desktop; email clients that block the CSS `table-layout` fall
    back to a stacked column. Wide-brand color coding: Google red,
    Outlook blue, ICS neutral (Apple / other)."""
    g_url = links.get("google") or "#"
    o_url = links.get("outlook") or "#"
    i_url = links.get("ics") or "#"
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0 6px;">
  <tr>
    <td align="center" style="padding:0 4px 0 0;" width="33%">
      <a href="{g_url}" target="_blank"
         style="display:block;padding:10px 0;background:#ea4335;color:#fff;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;text-align:center;">
        Google
      </a>
    </td>
    <td align="center" style="padding:0 4px;" width="33%">
      <a href="{o_url}" target="_blank"
         style="display:block;padding:10px 0;background:#0078d4;color:#fff;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;text-align:center;">
        Outlook
      </a>
    </td>
    <td align="center" style="padding:0 0 0 4px;" width="33%">
      <a href="{i_url}" target="_blank"
         style="display:block;padding:10px 0;background:#374151;color:#fff;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;text-align:center;">
        Apple / Other
      </a>
    </td>
  </tr>
</table>
<p style="margin:6px 0 0;font-size:11px;color:#6b7280;text-align:center;line-height:1.5;">
  One click adds this to your calendar. Apple / Other downloads a <code style="font-size:11px;color:#6b7280;">.ics</code> file.
</p>
""".strip()


def _build_ics(req_row: dict, slot: dict, meeting_url: str = "") -> str:
    """Minimal RFC 5545 VCALENDAR — 45-minute block in Pacific, exported
    with a floating VTIMEZONE so calendar apps localize correctly. Uses
    the exact hour+minute from the new `{date, time}` shape, or falls
    back to the legacy bucket midpoint hour for pre-migration rows.

    When a meeting URL is provided, it becomes both the `LOCATION`
    (which most calendar apps render as clickable in the event popover)
    AND is prepended into the DESCRIPTION so it stays reachable from
    calendar UIs that don't linkify LOCATION.
    """
    try:
        d = datetime.strptime(slot["date"], "%Y-%m-%d")
        hh, mm = _slot_hour_minute(slot)
        start_local = d.replace(hour=hh, minute=mm, second=0)
        end_local = start_local + timedelta(minutes=45)
    except Exception:
        # Fallback: today at 10 AM
        start_local = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(minutes=45)

    def _dt_ics(dt):
        return dt.strftime("%Y%m%dT%H%M%S")

    def _ics_esc(s):
        return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    uid = f"demo-request-{req_row['id']}@gigsfill.com"
    summary = f"GigsFill Demo — {req_row.get('name','')}".strip()
    join_prefix = f"Join: {meeting_url}\n\n" if meeting_url else ""
    body = (
        f"{join_prefix}"
        f"Live demo of GigsFill.\n\n"
        f"Requested by: {req_row.get('name','')} <{req_row.get('email','')}>\n"
        f"Entity: {req_row.get('entity_name','') or '—'} ({req_row.get('entity_type','') or '—'})\n"
        f"Location: {req_row.get('city','') or '—'}, {req_row.get('state','') or '—'}\n"
        f"Notes: {req_row.get('notes','') or '—'}\n"
    )
    body_esc = _ics_esc(body)
    location_ics = _ics_esc(meeting_url or "Video call (link sent before the demo)")
    url_line = f"URL:{meeting_url}\r\n" if meeting_url else ""

    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//GigsFill//DemoRequest//EN\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:REQUEST\r\n"
        "BEGIN:VTIMEZONE\r\n"
        "TZID:America/Los_Angeles\r\n"
        "BEGIN:STANDARD\r\n"
        "DTSTART:19701101T020000\r\n"
        "TZOFFSETFROM:-0700\r\n"
        "TZOFFSETTO:-0800\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\n"
        "TZNAME:PST\r\n"
        "END:STANDARD\r\n"
        "BEGIN:DAYLIGHT\r\n"
        "DTSTART:19700308T020000\r\n"
        "TZOFFSETFROM:-0800\r\n"
        "TZOFFSETTO:-0700\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
        "TZNAME:PDT\r\n"
        "END:DAYLIGHT\r\n"
        "END:VTIMEZONE\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTSTART;TZID=America/Los_Angeles:{_dt_ics(start_local)}\r\n"
        f"DTEND;TZID=America/Los_Angeles:{_dt_ics(end_local)}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"LOCATION:{location_ics}\r\n"
        f"{url_line}"
        f"DESCRIPTION:{body_esc}\r\n"
        "STATUS:CONFIRMED\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


# ─────────────────────────── UI helpers ───────────────────────────

def _brand_page(icon: str, color: str, title: str, subtitle: str, cta_html: str = "") -> str:
    """Consistent branded status page for the accept-link landing."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title} — GigsFill</title>
<link href="/app/static/css/gigsfill.css" rel="stylesheet"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;color:#e5e7eb;">
<div style="max-width:560px;margin:60px auto;padding:40px 32px;background:#151b28;border:1px solid rgba(255,255,255,0.08);border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.4);text-align:center;">
  <div style="font-size:64px;line-height:1;margin-bottom:16px;">{icon}</div>
  <h1 style="font-size:24px;font-weight:700;background:linear-gradient(135deg,#8b5cf6,#06b6d4);background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:0 0 12px;">{title}</h1>
  <p style="font-size:15px;color:#9ca3af;line-height:1.6;margin:0 0 24px;">{subtitle}</p>
  {cta_html}
  <hr style="border:0;border-top:1px solid rgba(255,255,255,0.08);margin:32px 0 20px;">
  <p style="font-size:12px;color:#6b7280;margin:0;">GigsFill — live music, booked right.</p>
</div>
</body></html>"""


def _build_admin_email_html(req_row: dict, slots: list[dict], site_url: str,
                              slots_version: int = 1) -> str:
    """Admin notification email. Renders 3 big accept buttons +
    a link back to the admin panel. Brand-consistent (purple→cyan gradient).
    Tokens carry the row's current `slots_version` so a reschedule
    invalidates every button in a prior email."""
    import html as _h
    tokens = [_sign_accept_token(req_row["id"], i, slots_version) for i in range(len(slots))]

    slot_cards_html = ""
    for i, (slot, tok) in enumerate(zip(slots, tokens)):
        accept_url = f"{site_url}/api/demo-request/{tok}/accept"
        slot_cards_html += f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px;">
          <tr>
            <td style="background:#0f172a;border:1px solid rgba(139,92,246,0.35);border-radius:8px;padding:16px 18px;">
              <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Preferred #{i+1}</div>
              <div style="font-size:16px;color:#e5e7eb;font-weight:600;margin-bottom:12px;">{_h.escape(_fmt_slot_human(slot))}</div>
              <a href="{accept_url}"
                 style="display:inline-block;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;padding:10px 22px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">
                ✓ Accept this slot
              </a>
            </td>
          </tr>
        </table>"""

    entity_line = ""
    if req_row.get("entity_name") or req_row.get("entity_type"):
        _t = req_row.get("entity_type") or ""
        _n = req_row.get("entity_name") or ""
        entity_line = f"<div style='margin:6px 0;'><strong>{_h.escape(_t.title())}:</strong> {_h.escape(_n)}</div>"

    location_line = ""
    if req_row.get("city") or req_row.get("state"):
        loc = ", ".join([p for p in [req_row.get("city"), req_row.get("state")] if p])
        location_line = f"<div style='margin:6px 0;'><strong>Location:</strong> {_h.escape(loc)}</div>"

    phone_line = ""
    if req_row.get("phone"):
        phone_line = f"<div style='margin:6px 0;'><strong>Phone:</strong> {_h.escape(req_row['phone'])}</div>"

    notes_html = ""
    if req_row.get("notes"):
        notes_html = f"""<div style='margin-top:16px;padding:14px 16px;background:#0f172a;border-left:3px solid #06b6d4;border-radius:4px;'>
        <div style='font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;'>Notes</div>
        <div style='font-size:14px;color:#e5e7eb;white-space:pre-wrap;line-height:1.5;'>{_h.escape(req_row['notes'])}</div>
        </div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;">
<tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">

<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;opacity:0.9;">New Demo Request</div>
  <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;">{_h.escape(req_row.get('name') or 'Someone')} wants a live demo</h1>
</td></tr>

<tr><td style="padding:24px 32px;color:#e5e7eb;font-size:14px;line-height:1.6;">
  <div style="margin:6px 0;"><strong>Email:</strong> <a href="mailto:{_h.escape(req_row.get('email','') or '')}" style="color:#7dd3fc !important;text-decoration:underline;">{_h.escape(req_row.get('email','') or '(no email)')}</a></div>
  {phone_line}
  {entity_line}
  {location_line}
  {notes_html}
</td></tr>

<tr><td style="padding:0 32px 8px;color:#94a3b8;font-size:13px;">
  <p style="margin:0 0 12px;">Click a button below to accept one of their preferred times. The prospect will be emailed a confirmation + calendar invite immediately.</p>
</td></tr>

<tr><td style="padding:0 32px 28px;">
  {slot_cards_html}
  <div style="margin-top:20px;padding-top:16px;border-top:1px solid rgba(255,255,255,0.06);text-align:center;">
    <a href="{site_url}/app/admin.html?tab=demos" style="color:#94a3b8;font-size:13px;text-decoration:none;border-bottom:1px dashed rgba(148,163,184,0.4);">Or manage from the admin panel →</a>
  </div>
</td></tr>

</table></td></tr></table></body></html>"""


def _detect_confirm_mode(req_row: dict) -> tuple[str, dict | None]:
    """Decide which flavor of confirmation email to send at accept time.

    Reads the row's `history_json` and looks at what happened between
    the last `scheduled` action and now:
      - No prior `scheduled` entry           → `'new'` (first booking ever)
      - Last event was `reverted_to_pending` → `'admin_changed'` (admin
        cancelled a booked time and picked a new one — "sorry we had to
        change" copy)
      - Last event was `rescheduled_by_prospect` → `'prospect_changed'`
        (prospect asked for new times — "you're re-confirmed for X" copy)

    Returns `(mode, prior_slot)` where `prior_slot` is the previously
    scheduled slot dict (used for the strike-through display) or None
    when we can't recover it.
    """
    try:
        hist = json.loads(req_row.get("history_json") or "[]")
        if not isinstance(hist, list):
            return ("new", None)
    except Exception:
        return ("new", None)

    # Find the most recent 'scheduled' entry — that's the prior slot.
    # Anything after it (chronologically) that isn't 'scheduled'
    # itself tells us HOW we got out of that state.
    prior_scheduled = None
    latest_transition = None
    for entry in hist:
        act = (entry or {}).get("action")
        if act == "scheduled":
            prior_scheduled = entry
            latest_transition = None  # reset — a new scheduled resets the trail
        elif act in ("reverted_to_pending", "rescheduled_by_prospect", "cancelled_by_prospect", "cancelled_by_admin"):
            latest_transition = entry

    if not prior_scheduled:
        return ("new", None)

    prior_slot = prior_scheduled.get("slot") if isinstance(prior_scheduled, dict) else None
    if latest_transition and latest_transition.get("action") == "reverted_to_pending":
        return ("admin_changed", prior_slot)
    if latest_transition and latest_transition.get("action") == "rescheduled_by_prospect":
        return ("prospect_changed", prior_slot)
    # A prior 'scheduled' with no subsequent transition means we're
    # re-scheduling from an already-scheduled state without going
    # through revert or reschedule (shouldn't happen given the guards,
    # but treat defensively as 'admin_changed').
    return ("admin_changed", prior_slot)


def _build_prospect_confirmation_html(req_row: dict, slot: dict, site_url: str = "",
                                         mode: str = "new", prior_slot: dict | None = None,
                                         meeting_url: str = "") -> str:
    """Confirmation email sent to the prospect after admin picks a slot.

    Three flavors selected by `mode`:

    - `'new'`               → "Your demo is scheduled ✓" (first booking)
    - `'admin_changed'`     → "Sorry — we had to change your demo time"
      (admin reverted a scheduled slot and re-accepted a new one; the
      prospect didn't ask for this, so lead with an apology and show
      the old time struck through above the new time)
    - `'prospect_changed'`  → "Your new demo time is confirmed" (prospect
      hit the Change-time button in their previous confirmation email
      and admin just re-accepted; friendly, no apology needed)

    All 3 variants include the same calendar buttons + change/cancel
    footer so the loop can keep going.
    """
    import html as _h
    slot_human = _fmt_slot_human(slot)
    if not site_url:
        site_url = "https://gigsfill.com"
    reschedule_tok = _sign_reschedule_token(int(req_row["id"]))
    cancel_tok = _sign_cancel_token(int(req_row["id"]))
    reschedule_url = f"{site_url}/?reschedule={reschedule_tok}#request-demo"
    cancel_url = f"{site_url}/demo/cancel/{cancel_tok}"
    cal_links = _build_calendar_links(req_row, slot, site_url, meeting_url=meeting_url)
    cal_buttons = _build_calendar_buttons_html(cal_links)

    if mode == "admin_changed":
        header_title = "We had to change your demo time"
        intro_line = "Sorry for the shuffle — something came up on our end and we had to move your GigsFill demo to a different one of your preferred times."
        new_time_label = "New confirmed time"
    elif mode == "prospect_changed":
        header_title = "Your new demo time is confirmed ✓"
        intro_line = "Thanks for the update — we've confirmed one of your new preferred times."
        new_time_label = "Updated time"
    else:
        header_title = "Your demo is scheduled ✓"
        intro_line = "Your GigsFill demo is scheduled for:"
        new_time_label = "Confirmed time"

    # Prior slot strike-through — only render when we actually have one
    # and the mode reflects a change. Uses the same red-tinted dark
    # panel as the admin confirmation email for visual consistency.
    prior_html = ""
    if prior_slot and mode in ("admin_changed", "prospect_changed"):
        prior_human = _fmt_slot_human(prior_slot)
        prior_html = f"""
  <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:14px 20px;margin:0 0 12px;text-align:center;">
    <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:#fca5a5;margin-bottom:4px;">Was scheduled for</div>
    <div style="font-size:16px;font-weight:600;color:#e5e7eb;text-decoration:line-through;opacity:0.75;">{_h.escape(prior_human)}</div>
  </div>"""

    # Jul 2026 dark-theme rebrand — matches _build_admin_email_html /
    # _build_admin_confirmation_html so every email in the demo-request
    # thread has the same #151b28 card + purple→cyan gradient header.
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;"><tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.4);overflow:hidden;">

<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <h1 style="margin:0;font-size:22px;font-weight:700;">{_h.escape(header_title)}</h1>
</td></tr>

<tr><td style="padding:28px 32px;color:#e5e7eb;font-size:15px;line-height:1.6;">
  <p style="margin:0 0 16px;">Hi {_h.escape(req_row.get('name') or 'there')},</p>
  <p style="margin:0 0 20px;">{_h.escape(intro_line)}</p>
  {prior_html}
  <div style="background:#0f172a;border:1px solid rgba(139,92,246,0.35);border-radius:8px;padding:20px;margin:0 0 20px;text-align:center;">
    <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;margin-bottom:4px;">{_h.escape(new_time_label)}</div>
    <div style="font-size:20px;font-weight:700;color:#e5e7eb;">{_h.escape(slot_human)}</div>
  </div>
  {_fmt_meeting_line(meeting_url)}

  <p style="margin:0 0 8px;font-size:13px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;">Add to your calendar</p>
  {cal_buttons}

  <div style="margin:24px 0 20px;padding-top:20px;border-top:1px solid rgba(255,255,255,0.08);">
    {"" if meeting_url else '<p style="margin:0 0 12px;font-size:13px;color:#94a3b8;">We\'ll follow up with the video call link before the demo.</p>'}
    <p style="margin:0 0 12px;font-size:13px;color:#94a3b8;">Need to change something?</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0;">
      <tr>
        <td align="center" style="padding:0 6px 0 0;" width="50%">
          <a href="{reschedule_url}"
             style="display:inline-block;width:92%;padding:11px 0;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-sizing:border-box;">
            📅 Change time
          </a>
        </td>
        <td align="center" style="padding:0 0 0 6px;" width="50%">
          <a href="{cancel_url}"
             style="display:inline-block;width:92%;padding:10px 0;background:transparent;border:1px solid #ef4444;color:#fca5a5;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-sizing:border-box;">
            ✗ Cancel demo
          </a>
        </td>
      </tr>
    </table>
  </div>
</td></tr>

<tr><td style="padding:16px 32px;background:#0f172a;border-top:1px solid rgba(255,255,255,0.06);text-align:center;font-size:12px;color:#6b7280;">
  GigsFill — live music, booked right.
</td></tr>

</table></td></tr></table></body></html>"""


def _build_admin_confirmation_html(req_row: dict, slot: dict, site_url: str,
                                      mode: str = "new",
                                      prior_slot: dict | None = None,
                                      meeting_url: str = "") -> str:
    """Fired to the admin address right after they click Accept (either
    from the email button or the admin panel). Purpose: give admin a
    single 'this is on your calendar now' email with the same 3
    add-to-calendar buttons the prospect sees, plus prospect contact
    details so admin can prep. Prevents the "wait, when did I say
    yes to that?" scramble two weeks later.

    Three modes track the prospect email — a change (admin OR prospect
    initiated) also relabels the admin side so it's clear the calendar
    entry admin just added replaces one they may already have on
    their calendar.
    """
    import html as _h
    slot_human = _fmt_slot_human(slot)
    cal_links = _build_calendar_links(req_row, slot, site_url, meeting_url=meeting_url)
    cal_buttons = _build_calendar_buttons_html(cal_links)

    if mode == "admin_changed":
        header_kicker = "Demo time changed"
        header_line = f"You rebooked {req_row.get('name') or 'a demo'}"
        time_label = "New scheduled time"
    elif mode == "prospect_changed":
        header_kicker = "Demo rebooked"
        header_line = f"{req_row.get('name') or 'A prospect'} — new time confirmed"
        time_label = "Updated time"
    else:
        header_kicker = "Demo confirmed"
        header_line = f"You accepted {req_row.get('name') or 'a demo'}"
        time_label = "Scheduled for"

    prior_html = ""
    if prior_slot and mode in ("admin_changed", "prospect_changed"):
        prior_human = _fmt_slot_human(prior_slot)
        prior_html = f"""
  <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:12px 18px;margin:0 0 12px;text-align:center;">
    <div style="font-size:11px;color:#fca5a5;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:2px;">Was scheduled for</div>
    <div style="font-size:15px;font-weight:600;color:#e5e7eb;text-decoration:line-through;opacity:0.75;">{_h.escape(prior_human)}</div>
    <div style="margin-top:6px;font-size:11px;color:#94a3b8;">Remove this from your calendar and add the new one below.</div>
  </div>"""

    entity_line = ""
    if req_row.get("entity_name") or req_row.get("entity_type"):
        _t = req_row.get("entity_type") or ""
        _n = req_row.get("entity_name") or ""
        entity_line = (
            f"<div style='margin:6px 0;'><strong>{_h.escape(_t.title())}:</strong> "
            f"{_h.escape(_n)}</div>"
        )
    phone_line = ""
    if req_row.get("phone"):
        phone_line = f"<div style='margin:6px 0;'><strong>Phone:</strong> {_h.escape(req_row['phone'])}</div>"
    location_line = ""
    if req_row.get("city") or req_row.get("state"):
        loc = ", ".join([p for p in [req_row.get("city"), req_row.get("state")] if p])
        location_line = f"<div style='margin:6px 0;'><strong>Location:</strong> {_h.escape(loc)}</div>"
    notes_html = ""
    if req_row.get("notes"):
        notes_html = f"""<div style='margin-top:16px;padding:14px 16px;background:#0f172a;border-left:3px solid #06b6d4;border-radius:4px;'>
        <div style='font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;'>Their notes</div>
        <div style='font-size:14px;color:#e5e7eb;white-space:pre-wrap;line-height:1.5;'>{_h.escape(req_row['notes'])}</div>
        </div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;">
<tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">

<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#10b981,#06b6d4);color:#fff;">
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;opacity:0.9;">{_h.escape(header_kicker)}</div>
  <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;">{_h.escape(header_line)}</h1>
</td></tr>

<tr><td style="padding:24px 32px;color:#e5e7eb;font-size:14px;line-height:1.6;">
  {prior_html}
  <div style="background:#0f172a;border:1px solid rgba(139,92,246,0.35);border-radius:8px;padding:16px 18px;margin:0 0 20px;text-align:center;">
    <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">{_h.escape(time_label)}</div>
    <div style="font-size:18px;font-weight:700;color:#e5e7eb;">{_h.escape(slot_human)}</div>
  </div>

  {_fmt_meeting_line(meeting_url)}

  <div style="margin:0 0 12px;font-size:13px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;">Add to your calendar</div>
  {cal_buttons}

  <div style="margin-top:20px;padding-top:16px;border-top:1px solid rgba(255,255,255,0.06);">
    <div style='margin:6px 0;'><strong>Prospect:</strong> {_h.escape(req_row.get('name','') or '')}</div>
    <div style='margin:6px 0;'><strong>Email:</strong> <a href="mailto:{_h.escape(req_row.get('email','') or '')}" style="color:#7dd3fc !important;text-decoration:underline;">{_h.escape(req_row.get('email','') or '')}</a></div>
    {phone_line}
    {entity_line}
    {location_line}
    {notes_html}
  </div>

  <div style="margin-top:20px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06);text-align:center;">
    <a href="{site_url}/app/admin.html?tab=demos" style="color:#94a3b8;font-size:13px;text-decoration:none;border-bottom:1px dashed rgba(148,163,184,0.4);">View in admin panel →</a>
  </div>
</td></tr>

</table></td></tr></table></body></html>"""


def _build_decline_email_html(req_row: dict, message: str, site_url: str = "",
                                 admin_suggested_slots: list[dict] | None = None) -> str:
    """Prospect-facing email when admin declines their requested times.

    Two variants — determined by whether admin filled in the optional
    "here are some times that WOULD work for us" slots in the decline
    modal:

    - **With admin-suggested slots**: renders each as a one-click green
      accept button (per-slot HMAC-signed link → `/demo/prospect-accept`
      endpoint which moves the row to `scheduled` and fires the normal
      confirmation-to-both-sides flow). The "Pick new times" button is
      still included below as a fallback for when none of admin's
      suggestions work either.
    - **Without**: just the "Pick new times" button — the loop-back
      into the same 3-slot picker (reschedule endpoint accepts
      `declined` rows as revival).
    """
    import html as _h
    site_url = site_url or "https://gigsfill.com"
    revive_tok = _sign_reschedule_token(int(req_row["id"]))
    revive_url = f"{site_url}/?reschedule={revive_tok}#request-demo"

    msg_block = ""
    if message:
        msg_block = (
            '<div style="background:#0f172a;border-left:3px solid #06b6d4;'
            'padding:12px 16px;margin:16px 0;color:#e5e7eb;">'
            f'{_h.escape(message)}'
            '</div>'
        )

    # Admin's counter-proposal block (only when they filled in 1-3 slots)
    suggested_block = ""
    if admin_suggested_slots:
        cards = ""
        for i, slot in enumerate(admin_suggested_slots):
            accept_tok = _sign_prospect_accept_token(int(req_row["id"]), i)
            accept_url = f"{site_url}/demo/prospect-accept/{accept_tok}"
            cards += f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 10px;">
          <tr>
            <td style="background:#0f172a;border:1px solid rgba(16,185,129,0.35);border-radius:8px;padding:14px 18px;">
              <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Option {i+1}</div>
              <div style="font-size:16px;color:#e5e7eb;font-weight:600;margin-bottom:10px;">{_h.escape(_fmt_slot_human(slot))}</div>
              <a href="{accept_url}"
                 style="display:inline-block;background:linear-gradient(135deg,#10b981,#06b6d4);color:#fff;padding:9px 20px;border-radius:5px;text-decoration:none;font-size:14px;font-weight:600;">
                ✓ Book this time
              </a>
            </td>
          </tr>
        </table>"""
        suggested_block = f"""
  <p style="margin:16px 0 8px;font-size:13px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;">Would any of these work instead?</p>
  {cards}
  <p style="margin:16px 0 8px;font-size:13px;color:#94a3b8;">If none of those fit — no problem, pick a few new times below:</p>"""

    # Jul 2026 dark-theme rebrand — consistent with the rest of the demo thread.
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;"><tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.4);overflow:hidden;">
<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <h1 style="margin:0;font-size:22px;font-weight:700;">A quick note on your demo request</h1>
</td></tr>
<tr><td style="padding:28px 32px;color:#e5e7eb;font-size:15px;line-height:1.6;">
  <p style="margin:0 0 16px;">Hi {_h.escape(req_row.get('name') or 'there')},</p>
  <p style="margin:0 0 16px;">Thanks for your interest in a GigsFill demo. Unfortunately none of the times you picked worked on our end.</p>
  {msg_block}
  {suggested_block}
  {"" if suggested_block else '<p style="margin:16px 0 12px;">Pick a few new times below and we\'ll find a fit.</p>'}
  <div style="margin:16px 0 8px;text-align:center;">
    <a href="{revive_url}"
       style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:15px;font-weight:600;">
      📅 Pick new times
    </a>
  </div>
  <p style="margin:14px 0 0;font-size:12px;color:#94a3b8;text-align:center;">
    Opens the same 3-slot picker on gigsfill.com. Your details are pre-filled.
  </p>
  <p style="margin:20px 0 0;color:#94a3b8;font-size:13px;">— The GigsFill Team</p>
</td></tr>
<tr><td style="padding:16px 32px;background:#0f172a;border-top:1px solid rgba(255,255,255,0.06);text-align:center;font-size:12px;color:#6b7280;">
  GigsFill — live music, booked right.
</td></tr>
</table></td></tr></table></body></html>"""


# ─────────────────────────── endpoints ───────────────────────────

@router.post("/api/demo-request")
@limiter.limit("3/hour")
async def submit_demo_request(request: Request, data: dict, db=Depends(get_db)):
    """Public — form submits here. Rate-limited to 3/hour per IP so bots
    can't drown the queue."""
    first_name = (data.get("first_name") or "").strip()[:60]
    last_name  = (data.get("last_name")  or "").strip()[:60]
    # Support legacy single-name callers too; if only `name` is present,
    # split on the first space to derive first/last for later columns.
    name = (data.get("name") or (first_name + " " + last_name)).strip()[:120]
    if not first_name and " " in name:
        first_name, last_name = name.split(" ", 1)
        first_name, last_name = first_name.strip()[:60], last_name.strip()[:60]
    email = (data.get("email") or "").strip()[:200]
    phone = (data.get("phone") or "").strip()[:40] or None
    entity_type = (data.get("entity_type") or "").strip().lower() or None
    entity_name = (data.get("entity_name") or "").strip()[:200] or None
    city = (data.get("city") or "").strip()[:100] or None
    state = (data.get("state") or "").strip()[:2] or None
    notes = (data.get("notes") or "").strip()[:2000] or None

    # Honeypot: hidden `_hp` field should be empty. Silent-succeed on
    # non-empty so bots don't detect the block.
    if data.get("_hp"):
        return {"ok": True, "message": "Thanks — we'll be in touch soon."}

    if not name or len(name) < 2:
        raise HTTPException(400, "Please enter your name.")
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address.")
    if entity_type and entity_type not in ("venue", "artist", "other"):
        raise HTTPException(400, "Entity type must be venue, artist, or other.")

    slots = _validate_slots(data.get("preferred_slots"))

    # Insert
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    result = db.execute(
        text("""INSERT INTO demo_requests
                (name, first_name, last_name, email, phone, entity_type, entity_name,
                 city, state, notes, preferred_slots_json, status, created_at)
                VALUES (:n, :fn, :ln, :e, :p, :et, :en, :c, :s, :nt, :sl, 'pending', :ca)"""),
        {"n": name, "fn": first_name or None, "ln": last_name or None,
         "e": email, "p": phone, "et": entity_type, "en": entity_name,
         "c": city, "s": state, "nt": notes, "sl": json.dumps(slots), "ca": now_iso}
    )
    # 2026-07-25 bug fix: pull id from the result cursor BEFORE commit.
    # Previously called `SELECT last_insert_rowid()` after commit, but
    # SQLAlchemy may hand that follow-up query a different pooled
    # connection — and last_insert_rowid() is per-connection, so it
    # returned some OTHER connection's last insert (a rate-limiter row,
    # a notification, etc.). Result: token was signed with the wrong
    # req_id and admin's email link 404'd ("Request not found").
    req_id = result.lastrowid
    db.commit()
    req_row = {
        "id": req_id, "name": name, "first_name": first_name, "last_name": last_name,
        "email": email, "phone": phone,
        "entity_type": entity_type, "entity_name": entity_name,
        "city": city, "state": state, "notes": notes,
    }

    # Fire the admin email async-ish (best-effort, don't hold the client)
    try:
        smtp = _get_smtp(db)
        site_url = _get_site_url(db)
        admin_email = _get_admin_email(db)
        subject = f"[Demo Request] {name} — {entity_name or entity_type or 'GigsFill'}"
        html = _build_admin_email_html(req_row, slots, site_url, slots_version=1)
        _send_email_via_smtp(smtp, admin_email, subject, html)
    except Exception as e:
        logger.error(f"admin notify failed for demo request {req_id}: {e}", exc_info=True)

    logger.info(f"[DEMO_REQUEST] new #{req_id} from {name} <{email}>, {len(slots)} slots")
    return {"ok": True, "message": "Thanks! A GigsFill team member will get back to you shortly."}


@router.get("/api/demo-request/{token}/accept")
def accept_via_link(token: str, request: Request, db=Depends(get_db)):
    """One-click accept from admin email. HMAC-verified — anyone with the
    link can accept (that's the point — admin doesn't want to login on
    mobile to click a button). Idempotent: accepting the same slot twice
    is fine; accepting a different slot after one was already scheduled
    returns a "already scheduled" page (no state change)."""
    payload = _verify_accept_token(token)
    req_id = int(payload.get("req_id") or 0)
    slot_index = int(payload.get("slot") or 0)
    token_v = int(payload.get("v") or 1)
    if not req_id:
        raise HTTPException(400, "Malformed accept link.")

    row = db.execute(
        text("SELECT * FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    if not row:
        return HTMLResponse(_brand_page("❓", "#94a3b8", "Request not found",
            "This demo request no longer exists. If this looks wrong, reply to the original email."))

    row = dict(row)
    slots = json.loads(row.get("preferred_slots_json") or "[]")

    # Version mismatch = prospect rescheduled since this admin email was
    # sent. Reject clearly instead of silently accepting a slot the
    # prospect may not have picked.
    row_v = int(row.get("slots_version") or 1)
    if token_v != row_v:
        return HTMLResponse(_brand_page("↻", "#f59e0b", "This request was rescheduled",
            f"{row.get('name','The prospect')} sent a new set of preferred times after this email. "
            f"Open the newer 'Demo Request' email in your inbox, or manage from the admin panel."))

    if slot_index >= len(slots):
        return HTMLResponse(_brand_page("❌", "#ef4444", "Invalid slot",
            "This link refers to a slot that doesn't exist. Reply to the request email to reschedule."))

    slot = slots[slot_index]

    # 2026-07-25: block accept of a slot whose datetime has already passed.
    # Same rule as the panel accept — protects one-click email links that
    # sat in the inbox past the requested time.
    if _slot_is_past(slot):
        return HTMLResponse(_brand_page("⏰", "#f59e0b", "That time has passed",
            f"The {_fmt_slot_human(slot)} slot is now in the past. Reply to the request "
            f"email or use the admin panel to counter-propose new times."))

    # Already-scheduled? Idempotent behavior — show the confirmation page for
    # the ORIGINAL scheduled slot, not for the one this link refers to.
    if row["status"] == "scheduled" and row.get("scheduled_slot_index") is not None:
        already_slot = slots[int(row["scheduled_slot_index"])] if row["scheduled_slot_index"] < len(slots) else slot
        return HTMLResponse(_brand_page("✓", "#10b981", "Already scheduled",
            f"You already accepted the {_fmt_slot_human(already_slot)} slot for this request. "
            f"The prospect has been emailed."))
    if row["status"] == "declined":
        return HTMLResponse(_brand_page("↩︎", "#f59e0b", "Already declined",
            "This request was already declined. If that's a mistake, reply to the original email."))
    if row["status"] == "cancelled":
        return HTMLResponse(_brand_page("✗", "#ef4444", "Prospect cancelled",
            f"{row.get('name','The prospect')} cancelled this demo. No action needed."))

    # Do the accept
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    _hh, _mm = _slot_hour_minute(slot)
    _sat = f"{slot['date']}T{_hh:02d}:{_mm:02d}:00"
    db.execute(text("""UPDATE demo_requests
                       SET status = 'scheduled',
                           scheduled_slot_index = :i,
                           scheduled_at = :sat,
                           responded_at = :ra
                       WHERE id = :id AND status = 'pending'"""),
               {"i": slot_index, "sat": _sat, "ra": now_iso, "id": req_id})
    db.commit()

    # Detect whether this is a first-time schedule, an admin-driven
    # change, or a prospect-driven reschedule — the prospect email
    # copy differs materially between the three. `row` here still
    # holds the PRE-accept state (history not yet appended with the
    # new 'scheduled' entry), so the detector correctly sees a prior
    # scheduled entry when this is a re-book.
    confirm_mode, prior_slot = _detect_confirm_mode(row)

    accepted_row = dict(row); accepted_row["scheduled_slot_index"] = slot_index
    meeting_url = _get_meeting_url(accepted_row, db)
    try:
        smtp = _get_smtp(db)
        site_url = _get_site_url(db)
        admin_email = _get_admin_email(db)
        prospect_html = _build_prospect_confirmation_html(
            accepted_row, slot, site_url, mode=confirm_mode,
            prior_slot=prior_slot, meeting_url=meeting_url
        )
        admin_html = _build_admin_confirmation_html(
            accepted_row, slot, site_url, mode=confirm_mode,
            prior_slot=prior_slot, meeting_url=meeting_url
        )
        ics = _build_ics(accepted_row, slot, meeting_url=meeting_url)
        subj_prefix = {
            "admin_changed": "Your GigsFill demo — time changed",
            "prospect_changed": "Your new GigsFill demo time is confirmed",
        }.get(confirm_mode, "Your GigsFill demo is confirmed")
        admin_subj_prefix = {
            "admin_changed": "[Demo Time Changed]",
            "prospect_changed": "[Demo Rebooked]",
        }.get(confirm_mode, "[Demo Confirmed]")
        _send_email_via_smtp(
            smtp, row["email"],
            f"{subj_prefix} — {_fmt_slot_human(slot)}",
            prospect_html,
            attachments=[("gigsfill-demo.ics", "text/calendar", ics)]
        )
        _send_email_via_smtp(
            smtp, admin_email,
            f"{admin_subj_prefix} {row.get('name','A prospect')} — {_fmt_slot_human(slot)}",
            admin_html,
            attachments=[("gigsfill-demo.ics", "text/calendar", ics)]
        )
    except Exception as e:
        logger.error(f"confirmation emails failed for req {req_id}: {e}", exc_info=True)

    _append_history(db, req_id, {
        "action": "scheduled",
        "actor": "admin_email_link",
        "confirm_mode": confirm_mode,
        "prior_slot": prior_slot,
        "slot_index": slot_index,
        "slot": slot,
    })
    db.commit()
    logger.info(f"[DEMO_REQUEST] accepted #{req_id} slot={slot_index} mode={confirm_mode}")
    return HTMLResponse(_brand_page(
        "🎉", "#10b981", "Demo scheduled!",
        f"You accepted the <strong>{_fmt_slot_human(slot)}</strong> slot for "
        f"<strong>{row.get('name','')}</strong>. A confirmation email + calendar invite "
        f"was just sent to {row.get('email','')}.",
        cta_html=f'<a href="/app/admin.html?tab=demos" style="display:inline-block;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;padding:12px 26px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;margin-top:16px;">View all demo requests →</a>'
    ))


# ─────────────────────────── admin surface ───────────────────────────

@router.get("/api/admin/demo-requests")
def admin_list_demo_requests(status: str = "all", admin=Depends(check_admin), db=Depends(get_db)):
    """Admin queue — filterable by status."""
    where = ""
    params = {}
    if status and status != "all":
        where = "WHERE status = :st"
        params["st"] = status
    # Sort priority:
    #   1. Pending first (needs admin action).
    #   2. Scheduled second, soonest scheduled_at first (upcoming demos
    #      surface at the top so admin can scan the queue by "what's
    #      next" at a glance).
    #   3. Everything else (completed / no_show / declined / cancelled)
    #      by created_at DESC — most recent first.
    rows = db.execute(text(f"""
        SELECT id, name, email, phone, entity_type, entity_name, city, state,
               notes, preferred_slots_json, status, scheduled_slot_index,
               scheduled_at, admin_notes, created_at, responded_at,
               meeting_url, admin_suggested_slots_json, outcome_notes
        FROM demo_requests
        {where}
        ORDER BY
          CASE status WHEN 'pending' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END,
          CASE WHEN status = 'scheduled' THEN scheduled_at END ASC,
          created_at DESC
        LIMIT 200
    """), params).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["preferred_slots"] = json.loads(d.pop("preferred_slots_json") or "[]")
            d["preferred_slots_human"] = [_fmt_slot_human(s) for s in d["preferred_slots"]]
            # 2026-07-25: expose per-slot is_past so admin UI can grey out
            # slots whose datetime has already passed. Prevents accidentally
            # accepting a slot that's now in the past.
            d["preferred_slots_past"] = [_slot_is_past(s) for s in d["preferred_slots"]]
        except Exception:
            d["preferred_slots"] = []
            d["preferred_slots_human"] = []
            d["preferred_slots_past"] = []
        try:
            d["admin_suggested_slots"] = json.loads(d.pop("admin_suggested_slots_json") or "[]")
            d["admin_suggested_slots_human"] = [_fmt_slot_human(s) for s in d["admin_suggested_slots"]]
            d["admin_suggested_slots_past"] = [_slot_is_past(s) for s in d["admin_suggested_slots"]]
        except Exception:
            d["admin_suggested_slots"] = []
            d["admin_suggested_slots_human"] = []
            d["admin_suggested_slots_past"] = []
        out.append(d)
    return {"requests": out, "platform_meeting_url": _get_platform_meeting_url(db)}


@router.post("/api/admin/demo-requests/{req_id}/accept")
def admin_accept_from_panel(req_id: int, data: dict, request: Request,
                             admin=Depends(check_admin), db=Depends(get_db)):
    """Admin panel accept — takes a slot_index."""
    slot_index = int(data.get("slot_index") or 0)
    row = db.execute(
        text("SELECT * FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Request not found")
    row = dict(row)
    if row["status"] != "pending":
        raise HTTPException(409, f"Request is already {row['status']}.")
    slots = json.loads(row.get("preferred_slots_json") or "[]")
    if slot_index >= len(slots):
        raise HTTPException(400, "Invalid slot_index.")

    slot = slots[slot_index]
    # 2026-07-25 defense-in-depth: reject accept on a slot whose datetime
    # has already passed. UI blackens these, but a stale tab could still
    # POST an old index, and email-link accepts hit /accept/{token} paths
    # that also check via the same _slot_is_past helper.
    if _slot_is_past(slot):
        raise HTTPException(400, "That time slot has already passed. Ask the prospect for new times.")
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    _hh, _mm = _slot_hour_minute(slot)
    _sat = f"{slot['date']}T{_hh:02d}:{_mm:02d}:00"
    db.execute(text("""UPDATE demo_requests
                       SET status = 'scheduled', scheduled_slot_index = :i,
                           scheduled_at = :sat, responded_at = :ra
                       WHERE id = :id"""),
               {"i": slot_index, "sat": _sat, "ra": now_iso, "id": req_id})
    db.commit()

    confirm_mode, prior_slot = _detect_confirm_mode(row)

    accepted_row = dict(row); accepted_row["scheduled_slot_index"] = slot_index
    meeting_url = _get_meeting_url(accepted_row, db)
    try:
        smtp = _get_smtp(db)
        site_url = _get_site_url(db)
        admin_email = _get_admin_email(db)
        prospect_html = _build_prospect_confirmation_html(
            accepted_row, slot, site_url, mode=confirm_mode,
            prior_slot=prior_slot, meeting_url=meeting_url
        )
        admin_html = _build_admin_confirmation_html(
            accepted_row, slot, site_url, mode=confirm_mode,
            prior_slot=prior_slot, meeting_url=meeting_url
        )
        ics = _build_ics(accepted_row, slot, meeting_url=meeting_url)
        subj_prefix = {
            "admin_changed": "Your GigsFill demo — time changed",
            "prospect_changed": "Your new GigsFill demo time is confirmed",
        }.get(confirm_mode, "Your GigsFill demo is confirmed")
        admin_subj_prefix = {
            "admin_changed": "[Demo Time Changed]",
            "prospect_changed": "[Demo Rebooked]",
        }.get(confirm_mode, "[Demo Confirmed]")
        _send_email_via_smtp(
            smtp, row["email"],
            f"{subj_prefix} — {_fmt_slot_human(slot)}",
            prospect_html,
            attachments=[("gigsfill-demo.ics", "text/calendar", ics)]
        )
        _send_email_via_smtp(
            smtp, admin_email,
            f"{admin_subj_prefix} {row.get('name','A prospect')} — {_fmt_slot_human(slot)}",
            admin_html,
            attachments=[("gigsfill-demo.ics", "text/calendar", ics)]
        )
    except Exception as e:
        logger.error(f"confirmation emails (admin panel) failed for {req_id}: {e}", exc_info=True)

    _append_history(db, req_id, {
        "action": "scheduled",
        "actor": "admin_panel",
        "actor_user_id": getattr(admin, "id", None) if admin else None,
        "confirm_mode": confirm_mode,
        "prior_slot": prior_slot,
        "slot_index": slot_index,
        "slot": slot,
    })
    db.commit()

    try:
        log_admin_action(db, admin, "demo_request_accept",
                          target_table="demo_requests", target_id=req_id,
                          metadata={"slot_index": slot_index, "slot": slot,
                                    "prospect_email": row["email"]})
    except Exception:
        pass
    return {"ok": True, "scheduled_slot": slot}


@router.post("/api/admin/demo-requests/{req_id}/decline")
def admin_decline_from_panel(req_id: int, data: dict, admin=Depends(check_admin), db=Depends(get_db)):
    """Admin panel decline — optional custom message + optional 1-3
    counter-proposed time slots. When slots are provided, the decline
    email surfaces them as one-click green accept buttons AND the
    "Pick new times" fallback; when omitted, only the fallback shows.
    Same terminal state (`declined`) either way — prospect can revive
    the row via reschedule endpoint if they don't accept any suggestion.
    """
    message = (data.get("message") or "").strip()[:1000]

    # Optional counter-slots — same validation as the prospect-side
    # submit so we can't end up with malformed slot dicts in storage.
    raw_slots = data.get("admin_suggested_slots") or []
    suggested_slots = []
    if raw_slots:
        try:
            suggested_slots = _validate_slots(raw_slots)
        except HTTPException:
            # Re-raise with clearer admin-facing wording so the decline
            # modal knows this is about counter-slots, not the prospect's.
            raise HTTPException(400, "One or more of your suggested times is invalid — check date + time.")

    row = db.execute(
        text("SELECT * FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Request not found")
    row = dict(row)
    if row["status"] != "pending":
        raise HTTPException(409, f"Request is already {row['status']}.")

    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    db.execute(text("""UPDATE demo_requests
                       SET status = 'declined',
                           admin_notes = :m,
                           admin_suggested_slots_json = :sug,
                           responded_at = :ra
                       WHERE id = :id"""),
               {"m": message or None,
                "sug": json.dumps(suggested_slots) if suggested_slots else None,
                "ra": now_iso, "id": req_id})
    _append_history(db, req_id, {
        "action": "declined_by_admin",
        "actor": "admin_panel",
        "actor_user_id": getattr(admin, "id", None) if admin else None,
        "message_len": len(message),
        "suggested_slots": suggested_slots or None,
    })
    db.commit()

    try:
        smtp = _get_smtp(db)
        site_url = _get_site_url(db)
        html = _build_decline_email_html(row, message, site_url,
                                            admin_suggested_slots=suggested_slots or None)
        _send_email_via_smtp(smtp, row["email"],
            "About your GigsFill demo request", html)
    except Exception as e:
        logger.error(f"decline email failed for {req_id}: {e}", exc_info=True)

    try:
        log_admin_action(db, admin, "demo_request_decline",
                          target_table="demo_requests", target_id=req_id,
                          metadata={"message_len": len(message),
                                    "prospect_email": row["email"],
                                    "suggested_slot_count": len(suggested_slots)})
    except Exception:
        pass
    return {"ok": True, "suggested_slot_count": len(suggested_slots)}


def _build_admin_side_cancel_email_html(req_row: dict, prior_slot: dict | None,
                                          message: str, site_url: str) -> str:
    """Prospect-facing email when *admin* cancels a scheduled demo. Similar
    tone to the decline email but leads with the cancellation of a
    booked time, not the "we couldn't fit you in" of decline. Instead
    of "reply with new times" we surface the same reschedule modal via
    a signed link — cancelled rows are accepted by the reschedule
    endpoint (treated as a `revived_by_prospect` flow), so the whole
    loop stays inside one row / one thread.
    """
    import html as _h
    site_url = site_url or "https://gigsfill.com"
    revive_tok = _sign_reschedule_token(int(req_row["id"]))
    revive_url = f"{site_url}/?reschedule={revive_tok}#request-demo"

    prior_line = ""
    if prior_slot:
        prior_line = (
            '<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:8px;'
            'padding:16px 20px;margin:0 0 20px;text-align:center;">'
            '<div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:#fca5a5;margin-bottom:4px;">Was scheduled for</div>'
            f'<div style="font-size:18px;font-weight:600;color:#e5e7eb;text-decoration:line-through;opacity:0.75;">{_h.escape(_fmt_slot_human(prior_slot))}</div>'
            '</div>'
        )
    msg_block = ""
    if message:
        msg_block = (
            '<div style="background:#0f172a;border-left:3px solid #06b6d4;'
            'padding:12px 16px;margin:16px 0;color:#e5e7eb;">'
            f'{_h.escape(message)}'
            '</div>'
        )
    # Jul 2026 dark-theme rebrand.
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;"><tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.4);overflow:hidden;">
<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <h1 style="margin:0;font-size:22px;font-weight:700;">Your GigsFill demo has been cancelled</h1>
</td></tr>
<tr><td style="padding:28px 32px;color:#e5e7eb;font-size:15px;line-height:1.6;">
  <p style="margin:0 0 16px;">Hi {_h.escape(req_row.get('name') or 'there')},</p>
  <p style="margin:0 0 16px;">Something came up on our end and we had to cancel your GigsFill demo. Sorry for the short notice.</p>
  {prior_line}
  {msg_block}
  <p style="margin:16px 0 12px;">Want to reschedule? Click below to pick a few new times — we'll confirm one shortly.</p>
  <div style="margin:16px 0 8px;text-align:center;">
    <a href="{revive_url}"
       style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:15px;font-weight:600;">
      📅 Pick new times
    </a>
  </div>
  <p style="margin:14px 0 0;font-size:12px;color:#94a3b8;text-align:center;">
    Opens the same 3-slot picker on gigsfill.com. Your details are pre-filled.
  </p>
  <p style="margin:20px 0 0;color:#94a3b8;font-size:13px;">— The GigsFill Team</p>
</td></tr>
<tr><td style="padding:16px 32px;background:#0f172a;border-top:1px solid rgba(255,255,255,0.06);text-align:center;font-size:12px;color:#6b7280;">
  GigsFill — live music, booked right.
</td></tr>
</table></td></tr></table></body></html>"""


@router.post("/api/admin/demo-requests/{req_id}/revert-to-pending")
def admin_revert_to_pending(req_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    """Admin picked the wrong slot (or needs to change) — revert a
    scheduled request back to pending so the 3 accept buttons come back.
    Does NOT email the prospect: they only get a "your time changed to X"
    email when admin re-accepts a new slot. Clears scheduled fields +
    reminder flag; the row's `preferred_slots_json` + `slots_version`
    are untouched, so old admin-email accept links remain valid.
    """
    row = db.execute(
        text("SELECT * FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Request not found")
    row = dict(row)
    if row["status"] != "scheduled":
        raise HTTPException(409, f"Only scheduled requests can be reverted (currently {row['status']}).")

    prior_slot = _prior_scheduled_slot(row)
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    db.execute(text("""UPDATE demo_requests
                       SET status = 'pending',
                           scheduled_slot_index = NULL,
                           scheduled_at = NULL,
                           reminder_sent_at = NULL,
                           responded_at = NULL
                       WHERE id = :id"""),
               {"id": req_id})
    _append_history(db, req_id, {
        "action": "reverted_to_pending",
        "actor": "admin_panel",
        "actor_user_id": getattr(admin, "id", None) if admin else None,
        "prior_slot": prior_slot,
        "note": "Admin changed their mind before final booking; no prospect email fired.",
    })
    db.commit()

    try:
        log_admin_action(db, admin, "demo_request_revert",
                          target_table="demo_requests", target_id=req_id,
                          metadata={"prior_slot": prior_slot,
                                    "prospect_email": row.get("email")})
    except Exception:
        pass
    logger.info(f"[DEMO_REQUEST] reverted #{req_id} back to pending (was scheduled)")
    _iso = datetime.utcnow().isoformat(timespec="seconds")
    return {"ok": True, "reverted_at": _iso}


@router.post("/api/admin/demo-requests/{req_id}/cancel")
def admin_cancel_demo(req_id: int, data: dict, admin=Depends(check_admin), db=Depends(get_db)):
    """Admin-side cancel — used when admin can't make a scheduled demo
    at all (not just wants a different slot). Optional `message`
    textarea gets copied into the prospect-facing email. Row moves
    to `cancelled`; the prospect can still request a new demo via
    homepage. Both admin and prospect end up with a clean paper trail
    via `history_json`.
    """
    message = (data.get("message") or "").strip()[:1000]
    row = db.execute(
        text("SELECT * FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Request not found")
    row = dict(row)
    if row["status"] not in ("pending", "scheduled"):
        raise HTTPException(409, f"Only active requests can be cancelled (currently {row['status']}).")

    prior_slot = _prior_scheduled_slot(row)
    prior_status = row["status"]
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    db.execute(text("""UPDATE demo_requests
                       SET status = 'cancelled',
                           scheduled_slot_index = NULL,
                           scheduled_at = NULL,
                           reminder_sent_at = NULL,
                           admin_notes = COALESCE(:m, admin_notes),
                           responded_at = :ra
                       WHERE id = :id"""),
               {"m": message or None, "ra": now_iso, "id": req_id})
    _append_history(db, req_id, {
        "action": "cancelled_by_admin",
        "actor": "admin_panel",
        "actor_user_id": getattr(admin, "id", None) if admin else None,
        "prior_status": prior_status,
        "prior_slot": prior_slot,
        "message": message or None,
    })
    db.commit()

    # Email the prospect (only if they were previously scheduled — a
    # cancel from `pending` before the prospect even had a confirmed
    # time reuses the decline email path via `admin_decline_from_panel`,
    # so this endpoint is really for scheduled → cancelled).
    if prior_status == "scheduled" and row.get("email"):
        try:
            smtp = _get_smtp(db)
            site_url = _get_site_url(db)
            html = _build_admin_side_cancel_email_html(row, prior_slot, message, site_url)
            _send_email_via_smtp(
                smtp, row["email"],
                "Your GigsFill demo has been cancelled",
                html,
            )
        except Exception as e:
            logger.error(f"admin-side cancel email failed for #{req_id}: {e}", exc_info=True)

    try:
        log_admin_action(db, admin, "demo_request_cancel",
                          target_table="demo_requests", target_id=req_id,
                          metadata={"prior_status": prior_status,
                                    "prior_slot": prior_slot,
                                    "message_len": len(message),
                                    "prospect_email": row.get("email")})
    except Exception:
        pass
    logger.info(f"[DEMO_REQUEST] cancelled_by_admin #{req_id} prior={prior_status}")
    return {"ok": True}


@router.put("/api/admin/demo-requests/{req_id}/outcome")
def admin_set_demo_outcome(req_id: int, data: dict,
                             admin=Depends(check_admin), db=Depends(get_db)):
    """Admin marks a past demo as `completed` (default) or `no_show`.

    Called from the admin panel when the auto-mark scheduler transitions
    a scheduled row to `completed` after its scheduled time + 60 min
    has passed, and admin clicks the pill to correct the outcome.

    Valid transitions:
      scheduled  → completed | no_show   (admin marks before auto-mark)
      completed  → no_show                (correction)
      no_show    → completed              (correction)
    """
    outcome = (data.get("outcome") or "").strip().lower()
    if outcome not in ("completed", "no_show"):
        raise HTTPException(400, "outcome must be 'completed' or 'no_show'.")

    # Optional free-text internal notes about how the demo went. Sent
    # from the outcome modal alongside the chip click. Empty string
    # explicitly clears any previously-saved notes so admin can wipe
    # a stale note by just deleting the textarea contents.
    notes_raw = data.get("outcome_notes")
    notes_provided = notes_raw is not None
    outcome_notes = (str(notes_raw) if notes_provided else "").strip()[:4000]

    row = db.execute(
        text("SELECT id, name, status, outcome_notes FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Request not found")
    row = dict(row)
    if row["status"] not in ("scheduled", "completed", "no_show"):
        raise HTTPException(409, f"Only past scheduled rows can have an outcome (currently {row['status']}).")

    prior_status = row["status"]
    prior_notes = row.get("outcome_notes") or ""
    unchanged = (prior_status == outcome) and (not notes_provided or outcome_notes == prior_notes)
    if unchanged:
        return {"ok": True, "status": outcome, "unchanged": True}

    if notes_provided:
        db.execute(
            text("UPDATE demo_requests SET status = :o, outcome_notes = :n WHERE id = :id"),
            {"o": outcome, "n": (outcome_notes or None), "id": req_id}
        )
    else:
        db.execute(
            text("UPDATE demo_requests SET status = :o WHERE id = :id"),
            {"o": outcome, "id": req_id}
        )
    _append_history(db, req_id, {
        "action": "outcome_set",
        "actor": "admin_panel",
        "actor_user_id": getattr(admin, "id", None) if admin else None,
        "prior_status": prior_status,
        "new_status": outcome,
        "notes_updated": bool(notes_provided and outcome_notes != prior_notes),
    })
    db.commit()

    try:
        log_admin_action(db, admin, "demo_request_outcome",
                          target_table="demo_requests", target_id=req_id,
                          metadata={"prior_status": prior_status,
                                    "new_status": outcome,
                                    "notes_len": len(outcome_notes) if notes_provided else -1,
                                    "name": row.get("name")})
    except Exception:
        pass
    logger.info(f"[DEMO_REQUEST] outcome #{req_id} {prior_status} → {outcome} notes_updated={notes_provided}")
    return {"ok": True, "status": outcome, "outcome_notes": outcome_notes or None}


def mark_completed_demos(cursor) -> int:
    """Scheduler tick — transitions `scheduled` demos to `completed`
    once their scheduled time + 60 min has passed (60 min = the demo's
    45 min block + a 15 min cushion, so we never flip status mid-demo).
    Admin can subsequently click the Completed pill in the panel and
    switch to `no_show` if the prospect didn't join.

    Comparison uses a naive Pacific timestamp against `scheduled_at`
    (also naive Pacific in the same shape), matching how the reminder
    scanner reads it. String comparison works because both are in
    `YYYY-MM-DDTHH:MM:SS` format.
    """
    try:
        from datetime import datetime as _dt, timezone as _dt_tz, timedelta as _td
        from zoneinfo import ZoneInfo
    except Exception as e:
        logger.error(f"mark_completed deps import failed: {e}")
        return 0

    try:
        pacific = ZoneInfo("America/Los_Angeles")
        now_pacific = _dt.now(_dt_tz.utc).astimezone(pacific)
        cutoff = (now_pacific - _td(minutes=60)).strftime("%Y-%m-%dT%H:%M:%S")
        cursor.execute("""
            SELECT id FROM demo_requests
             WHERE status = 'scheduled'
               AND scheduled_at IS NOT NULL
               AND scheduled_at < ?
        """, (cutoff,))
        rows = cursor.fetchall()
        if not rows:
            return 0
        stamp = _dt.now(_dt_tz.utc).strftime("%Y-%m-%dT%H:%M:%S")
        ids = [r[0] if not isinstance(r, dict) else r["id"] for r in rows]
        for rid in ids:
            cursor.execute(
                "UPDATE demo_requests SET status = 'completed' WHERE id = ? AND status = 'scheduled'",
                (rid,)
            )
            # History entry so a later click-to-change on the pill has
            # a clear audit trail for who / what / when.
            cur_hist = cursor.execute(
                "SELECT history_json FROM demo_requests WHERE id = ?", (rid,)
            ).fetchone()
            try:
                existing = json.loads((cur_hist[0] if cur_hist and cur_hist[0] else "[]") or "[]") or []
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
            existing.append({
                "action": "auto_completed",
                "actor": "scheduler",
                "ts": stamp,
                "prior_status": "scheduled",
                "new_status": "completed",
            })
            cursor.execute(
                "UPDATE demo_requests SET history_json = ? WHERE id = ?",
                (json.dumps(existing), rid)
            )
        try:
            cursor.connection.commit()
        except Exception:
            pass
        logger.info(f"[DEMO_COMPLETED] auto-marked {len(ids)} scheduled demos as completed: {ids}")
        return len(ids)
    except Exception as e:
        logger.error(f"mark_completed_demos failed: {e}", exc_info=True)
        return 0


@router.put("/api/admin/demo-requests/{req_id}/meeting-url")
def admin_set_meeting_url(req_id: int, data: dict,
                            admin=Depends(check_admin), db=Depends(get_db)):
    """Per-row Teams / Zoom / Meet URL override. Empty string clears
    the override and falls back to platform default. Value is stored
    as-is; a minimal well-formed URL check keeps out garbage without
    being over-fussy (Teams / Zoom / Meet all use slightly different
    URL shapes, and admin might paste a URL with tracking params).
    """
    row = db.execute(
        text("SELECT id, meeting_url, status FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Request not found")
    row = dict(row)

    url = (data.get("meeting_url") or "").strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "Meeting URL must start with http:// or https://.")
    if len(url) > 2000:
        raise HTTPException(400, "Meeting URL is too long (max 2000 characters).")

    db.execute(
        text("UPDATE demo_requests SET meeting_url = :u WHERE id = :id"),
        {"u": (url or None), "id": req_id}
    )
    _append_history(db, req_id, {
        "action": "meeting_url_updated",
        "actor": "admin_panel",
        "actor_user_id": getattr(admin, "id", None) if admin else None,
        "prior_url": row.get("meeting_url") or "",
        "new_url": url,
    })
    db.commit()

    try:
        log_admin_action(db, admin, "demo_request_meeting_url",
                          target_table="demo_requests", target_id=req_id,
                          metadata={"prior_url_set": bool(row.get("meeting_url")),
                                    "new_url_set": bool(url)})
    except Exception:
        pass
    logger.info(f"[DEMO_REQUEST] meeting_url updated #{req_id} set={bool(url)}")
    return {"ok": True, "meeting_url": url or None}


@router.delete("/api/admin/demo-requests/{req_id}")
def admin_delete_demo_request(req_id: int, admin=Depends(check_admin), db=Depends(get_db)):
    """Admin hard-deletes a demo request. Used for spam / test rows /
    duplicates that never got actioned. Does NOT email the prospect —
    if they need to be told the request is gone, use decline first."""
    row = db.execute(
        text("SELECT id, name, email, status FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Request not found")
    row = dict(row)
    db.execute(text("DELETE FROM demo_requests WHERE id = :id"), {"id": req_id})
    db.commit()

    try:
        log_admin_action(db, admin, "demo_request_delete",
                          target_table="demo_requests", target_id=req_id,
                          metadata={"prior_status": row.get("status"),
                                    "prospect_email": row.get("email"),
                                    "name": row.get("name")})
    except Exception:
        pass
    return {"ok": True}


# ─────────────────────────── prospect self-serve (cancel + reschedule) ───

def _build_admin_cancel_alert_html(req_row: dict, prior_slot: dict | None,
                                    site_url: str) -> str:
    """Admin heads-up email fired when a prospect cancels their scheduled demo."""
    import html as _h
    prior_slot_line = ""
    if prior_slot:
        prior_slot_line = (
            f"<div style='margin:6px 0;'><strong>Was scheduled for:</strong> "
            f"{_h.escape(_fmt_slot_human(prior_slot))}</div>"
        )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;">
<tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">

<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#ef4444,#f59e0b);color:#fff;">
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;opacity:0.9;">Demo cancelled</div>
  <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;">{_h.escape(req_row.get('name') or 'A prospect')} cancelled their demo</h1>
</td></tr>

<tr><td style="padding:24px 32px;color:#e5e7eb;font-size:14px;line-height:1.6;">
  <div style="margin:6px 0;"><strong>Email:</strong> <a href="mailto:{_h.escape(req_row.get('email','') or '')}" style="color:#7dd3fc !important;text-decoration:underline;">{_h.escape(req_row.get('email','') or '')}</a></div>
  {prior_slot_line}
  <p style="margin:16px 0 0;color:#94a3b8;font-size:13px;">The request is now marked <strong style="color:#fca5a5;">cancelled</strong> in the admin panel. No further emails will be sent to this prospect unless you re-engage manually.</p>
  <div style="margin-top:20px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06);text-align:center;">
    <a href="{site_url}/app/admin.html?tab=demos" style="color:#94a3b8;font-size:13px;text-decoration:none;border-bottom:1px dashed rgba(148,163,184,0.4);">View in admin panel →</a>
  </div>
</td></tr>

</table></td></tr></table></body></html>"""


def _build_prospect_cancel_ack_html(req_row: dict, site_url: str = "") -> str:
    """Short 'we heard you' email sent to the prospect after they cancel
    themselves via the confirmation-email button. Includes a 'Pick new
    times' button that reuses the reschedule endpoint — since prospect
    cancels land on `status='cancelled'`, that endpoint now revives the
    row instead of rejecting."""
    import html as _h
    site_url = site_url or "https://gigsfill.com"
    revive_tok = _sign_reschedule_token(int(req_row["id"]))
    revive_url = f"{site_url}/?reschedule={revive_tok}#request-demo"
    # Jul 2026 dark-theme rebrand.
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;"><tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.4);overflow:hidden;">
<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <h1 style="margin:0;font-size:22px;font-weight:700;">Your demo is cancelled</h1>
</td></tr>
<tr><td style="padding:28px 32px;color:#e5e7eb;font-size:15px;line-height:1.6;">
  <p style="margin:0 0 16px;">Hi {_h.escape(req_row.get('name') or 'there')},</p>
  <p style="margin:0 0 16px;">Your GigsFill demo has been cancelled — no calendar invite will be sent, and we've cleared the time on our end.</p>
  <p style="margin:16px 0 12px;">Changed your mind? Pick a few new times below and we'll rebook.</p>
  <div style="margin:16px 0 8px;text-align:center;">
    <a href="{revive_url}"
       style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:15px;font-weight:600;">
      📅 Pick new times
    </a>
  </div>
  <p style="margin:14px 0 0;font-size:12px;color:#94a3b8;text-align:center;">
    Opens the same 3-slot picker on gigsfill.com. Your details are pre-filled.
  </p>
  <p style="margin:20px 0 0;color:#94a3b8;font-size:13px;">— The GigsFill Team</p>
</td></tr>
<tr><td style="padding:16px 32px;background:#0f172a;border-top:1px solid rgba(255,255,255,0.06);text-align:center;font-size:12px;color:#6b7280;">
  GigsFill — live music, booked right.
</td></tr>
</table></td></tr></table></body></html>"""


def _build_admin_reschedule_alert_html(req_row: dict, prior_slot: dict | None,
                                        new_slots: list[dict], site_url: str,
                                        slots_version: int) -> str:
    """Admin heads-up + fresh accept buttons for the prospect's new times."""
    import html as _h
    tokens = [_sign_accept_token(req_row["id"], i, slots_version)
              for i in range(len(new_slots))]

    prior_slot_line = ""
    if prior_slot:
        prior_slot_line = (
            f"<div style='margin:0 0 12px;color:#94a3b8;font-size:13px;'>"
            f"Was scheduled for: <strong style='color:#e5e7eb;'>"
            f"{_h.escape(_fmt_slot_human(prior_slot))}</strong> — that time has been released.</div>"
        )

    slot_cards_html = ""
    for i, (slot, tok) in enumerate(zip(new_slots, tokens)):
        accept_url = f"{site_url}/api/demo-request/{tok}/accept"
        slot_cards_html += f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px;">
          <tr>
            <td style="background:#0f172a;border:1px solid rgba(139,92,246,0.35);border-radius:8px;padding:16px 18px;">
              <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">New preferred #{i+1}</div>
              <div style="font-size:16px;color:#e5e7eb;font-weight:600;margin-bottom:12px;">{_h.escape(_fmt_slot_human(slot))}</div>
              <a href="{accept_url}"
                 style="display:inline-block;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;padding:10px 22px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">
                ✓ Accept this slot
              </a>
            </td>
          </tr>
        </table>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;">
<tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">

<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;opacity:0.9;">Demo rescheduled</div>
  <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;">{_h.escape(req_row.get('name') or 'A prospect')} picked new times</h1>
</td></tr>

<tr><td style="padding:24px 32px 8px;color:#e5e7eb;font-size:14px;line-height:1.6;">
  <div style="margin:6px 0;"><strong>Email:</strong> <a href="mailto:{_h.escape(req_row.get('email','') or '')}" style="color:#7dd3fc !important;text-decoration:underline;">{_h.escape(req_row.get('email','') or '')}</a></div>
  {prior_slot_line}
  <p style="margin:12px 0 6px;color:#94a3b8;font-size:13px;">Pick one of the new preferred times below. Old accept-links from the previous email no longer work.</p>
</td></tr>

<tr><td style="padding:0 32px 28px;">
  {slot_cards_html}
  <div style="margin-top:20px;padding-top:16px;border-top:1px solid rgba(255,255,255,0.06);text-align:center;">
    <a href="{site_url}/app/admin.html?tab=demos" style="color:#94a3b8;font-size:13px;text-decoration:none;border-bottom:1px dashed rgba(148,163,184,0.4);">Or manage from the admin panel →</a>
  </div>
</td></tr>

</table></td></tr></table></body></html>"""


def _load_req_row(db, req_id: int) -> dict | None:
    row = db.execute(
        text("SELECT * FROM demo_requests WHERE id = :id"),
        {"id": req_id}
    ).mappings().first()
    return dict(row) if row else None


def _prior_scheduled_slot(row: dict) -> dict | None:
    try:
        if row.get("scheduled_slot_index") is None:
            return None
        slots = json.loads(row.get("preferred_slots_json") or "[]")
        i = int(row["scheduled_slot_index"])
        return slots[i] if 0 <= i < len(slots) else None
    except Exception:
        return None


@router.get("/demo/ics/{token}")
def prospect_download_ics(token: str, db=Depends(get_db)):
    """Signed ICS download for prospect + admin 'Add to Apple / Other'
    button. Rebuilds the VCALENDAR fresh each call — that way if we
    ever fix a bug in `_build_ics` (say, DST edge case), every issued
    link picks up the fix without a token re-mint.
    """
    from fastapi.responses import Response
    req_id, slot_index = _verify_ics_token(token)
    row = _load_req_row(db, req_id)
    if not row:
        raise HTTPException(404, "Demo request not found.")
    try:
        slots = json.loads(row.get("preferred_slots_json") or "[]")
    except Exception:
        slots = []
    # Prefer the row's currently-scheduled slot if the URL slot_index
    # is stale after a reschedule; falls back to the URL slot for
    # not-yet-accepted requests where scheduled_slot_index is null.
    if row.get("scheduled_slot_index") is not None:
        try:
            slot_index = int(row["scheduled_slot_index"])
        except Exception:
            pass
    if not (0 <= slot_index < len(slots)):
        raise HTTPException(410, "This calendar link is no longer valid.")
    slot = slots[slot_index]
    ics = _build_ics(row, slot)
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="gigsfill-demo.ics"',
            "Cache-Control": "no-store, must-revalidate",
        },
    )


@router.get("/demo/prospect-accept/{token}", response_class=HTMLResponse)
def prospect_accept_admin_slot(token: str, db=Depends(get_db)):
    """Prospect clicks one of admin's counter-proposed slots from the
    decline email. Verifies token, moves row from `declined` → `scheduled`
    with the picked slot, sends the full confirmation-to-both-sides
    flow (branded email + calendar buttons + ICS). Idempotent — a
    second click on the same or a different suggested slot after the
    demo is already scheduled shows the "already scheduled" page instead
    of overriding.
    """
    req_id, slot_index = _verify_prospect_accept_token(token)
    row = _load_req_row(db, req_id)
    if not row:
        return HTMLResponse(_brand_page("❓", "#94a3b8", "Request not found",
            "This demo request no longer exists. If this looks wrong, reply to the decline email."))

    try:
        suggested = json.loads(row.get("admin_suggested_slots_json") or "[]")
    except Exception:
        suggested = []
    if not (0 <= slot_index < len(suggested)):
        return HTMLResponse(_brand_page("❌", "#ef4444", "Invalid slot",
            "This link refers to a slot that no longer exists. Reply to the decline email to try again."))
    slot = suggested[slot_index]

    if row["status"] == "scheduled" and row.get("scheduled_slot_index") is not None:
        # Show the actually-scheduled slot; only preferred_slots_json is
        # indexed by scheduled_slot_index (that's how the admin accept
        # flow stores it), so re-render from that if possible.
        try:
            pref = json.loads(row.get("preferred_slots_json") or "[]")
            already = pref[int(row["scheduled_slot_index"])] if row["scheduled_slot_index"] < len(pref) else slot
        except Exception:
            already = slot
        return HTMLResponse(_brand_page("✓", "#10b981", "Already scheduled",
            f"You already booked the {_fmt_slot_human(already)} slot. A confirmation email was sent to you and our team."))
    if row["status"] == "cancelled":
        return HTMLResponse(_brand_page("✗", "#ef4444", "Demo cancelled",
            "This demo was cancelled after the decline. Please request a new one from the homepage."))

    # Move the row to scheduled. Copy ALL of admin's suggested slots
    # into `preferred_slots_json` (not just the accepted one) so a
    # later admin "Change time" revert-to-pending gives admin the same
    # 3 slots they originally proposed to pick between, not just the
    # one the prospect happened to click. `scheduled_slot_index` points
    # to the specifically-accepted one. Bump `slots_version` so any
    # stale admin accept-links from before the decline are invalidated.
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    hh, mm = _slot_hour_minute(slot)
    sched_at = f"{slot['date']}T{hh:02d}:{mm:02d}:00"
    new_version = int(row.get("slots_version") or 1) + 1
    db.execute(text("""UPDATE demo_requests
                       SET status = 'scheduled',
                           preferred_slots_json = :sl,
                           slots_version = :sv,
                           scheduled_slot_index = :idx,
                           scheduled_at = :sat,
                           reminder_sent_at = NULL,
                           responded_at = :ra
                       WHERE id = :id"""),
               {"sl": json.dumps(suggested),
                "sv": new_version,
                "idx": slot_index,
                "sat": sched_at, "ra": now_iso, "id": req_id})
    _append_history(db, req_id, {
        "action": "scheduled",
        "actor": "prospect_from_decline_email",
        "confirm_mode": "prospect_accepted_admin_suggestion",
        "slot": slot,
        "picked_from_admin_suggested_index": slot_index,
    })
    db.commit()

    # Fresh row for template rendering (scheduled_slot_index=slot_index now)
    fresh = _load_req_row(db, req_id) or row
    meeting_url = _get_meeting_url(fresh, db)
    try:
        smtp = _get_smtp(db)
        site_url = _get_site_url(db)
        admin_email = _get_admin_email(db)
        # Use the 'new' mode intentionally — from prospect's perspective
        # this is a fresh confirmation ("we found a time that works"),
        # not a change. Admin also sees a normal Demo Confirmed email.
        prospect_html = _build_prospect_confirmation_html(
            fresh, slot, site_url, mode="new", meeting_url=meeting_url
        )
        admin_html = _build_admin_confirmation_html(
            fresh, slot, site_url, mode="new", meeting_url=meeting_url
        )
        ics = _build_ics(fresh, slot, meeting_url=meeting_url)
        _send_email_via_smtp(
            smtp, fresh["email"],
            f"Your GigsFill demo is confirmed — {_fmt_slot_human(slot)}",
            prospect_html,
            attachments=[("gigsfill-demo.ics", "text/calendar", ics)]
        )
        _send_email_via_smtp(
            smtp, admin_email,
            f"[Demo Confirmed] {fresh.get('name','A prospect')} — {_fmt_slot_human(slot)}",
            admin_html,
            attachments=[("gigsfill-demo.ics", "text/calendar", ics)]
        )
    except Exception as e:
        logger.error(f"prospect-accept confirmation emails failed for #{req_id}: {e}", exc_info=True)

    logger.info(f"[DEMO_REQUEST] prospect_accepted_admin_suggestion #{req_id} slot_index={slot_index}")
    return HTMLResponse(_brand_page(
        "🎉", "#10b981", "You're booked!",
        f"Your GigsFill demo is scheduled for <strong>{_fmt_slot_human(slot)}</strong>. A confirmation email + calendar invite are on the way to <strong>{fresh.get('email','')}</strong>.",
        cta_html='<a href="https://gigsfill.com" style="display:inline-block;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;padding:12px 26px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;margin-top:8px;">Back to gigsfill.com</a>'
    ))


@router.get("/demo/cancel/{token}", response_class=HTMLResponse)
def prospect_cancel_landing(token: str, db=Depends(get_db)):
    """Public GET — prospect clicks 'Cancel demo' in the confirmation
    email. Shows a branded confirmation page with a POST button so a
    reflexive click doesn't nuke a demo they meant to keep. GET does
    NOT mutate state.
    """
    req_id = _verify_cancel_token(token)
    row = _load_req_row(db, req_id)
    if not row:
        return HTMLResponse(_brand_page("❓", "#94a3b8", "Request not found",
            "This demo request no longer exists. If this looks wrong, reply to the confirmation email."))
    if row["status"] == "cancelled":
        return HTMLResponse(_brand_page("✓", "#94a3b8", "Already cancelled",
            "This demo was already cancelled — you're all set. No further action needed."))
    if row["status"] == "declined":
        return HTMLResponse(_brand_page("↩︎", "#94a3b8", "Nothing to cancel",
            "This request was already closed on our end. Nothing more to do."))

    prior_slot = _prior_scheduled_slot(row)
    slot_line = ""
    if prior_slot:
        slot_line = (
            '<div style="background:#0f172a;border:1px solid rgba(255,255,255,0.08);'
            'border-radius:8px;padding:16px 18px;margin:0 auto 20px;max-width:360px;">'
            '<div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px;">Currently scheduled for</div>'
            f'<div style="font-size:16px;color:#e5e7eb;font-weight:600;">{_h_escape(_fmt_slot_human(prior_slot))}</div>'
            '</div>'
        )
    cta_html = (
        slot_line +
        '<form method="post" action="/demo/cancel/' + token + '" style="margin:0;">'
        '<button type="submit" style="display:inline-block;background:#ef4444;color:#fff;padding:12px 28px;border:0;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;">'
        '✗ Yes, cancel my demo'
        '</button>'
        '</form>'
        '<div style="margin-top:14px;font-size:13px;">'
        f'<a href="/?reschedule={_sign_reschedule_token(req_id)}#request-demo" style="color:#7dd3fc;text-decoration:none;border-bottom:1px dashed rgba(6,182,212,0.5);">'
        'Or pick a different time instead →</a>'
        '</div>'
    )
    return HTMLResponse(_brand_page(
        "✗", "#ef4444", "Cancel your demo?",
        "This releases the time on our end and lets our team know you can't make it. You can always request a new demo later.",
        cta_html=cta_html
    ))


@router.post("/demo/cancel/{token}", response_class=HTMLResponse)
def prospect_cancel_submit(token: str, db=Depends(get_db)):
    """Public POST — actually cancel the demo. Idempotent."""
    req_id = _verify_cancel_token(token)
    row = _load_req_row(db, req_id)
    if not row:
        return HTMLResponse(_brand_page("❓", "#94a3b8", "Request not found",
            "This demo request no longer exists."))
    if row["status"] == "cancelled":
        return HTMLResponse(_brand_page("✓", "#94a3b8", "Already cancelled",
            "This demo was already cancelled — you're all set."))

    prior_slot = _prior_scheduled_slot(row)
    prior_status = row["status"]

    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    db.execute(text("""UPDATE demo_requests
                       SET status = 'cancelled',
                           scheduled_slot_index = NULL,
                           scheduled_at = NULL,
                           reminder_sent_at = NULL,
                           responded_at = :ra
                       WHERE id = :id"""),
               {"ra": now_iso, "id": req_id})
    _append_history(db, req_id, {
        "action": "cancelled_by_prospect",
        "actor": "prospect_link",
        "prior_status": prior_status,
        "prior_slot": prior_slot,
    })
    db.commit()

    # Best-effort admin alert + prospect ack email
    try:
        smtp = _get_smtp(db)
        site_url = _get_site_url(db)
        admin_email = _get_admin_email(db)
        admin_html = _build_admin_cancel_alert_html(row, prior_slot, site_url)
        _send_email_via_smtp(
            smtp, admin_email,
            f"[Demo Cancelled] {row.get('name','A prospect')}",
            admin_html
        )
        if row.get("email"):
            ack_html = _build_prospect_cancel_ack_html(row, site_url)
            _send_email_via_smtp(
                smtp, row["email"],
                "Your GigsFill demo is cancelled", ack_html
            )
    except Exception as e:
        logger.error(f"cancel notify failed for demo #{req_id}: {e}", exc_info=True)

    logger.info(f"[DEMO_REQUEST] cancelled_by_prospect #{req_id} prior={prior_status}")
    return HTMLResponse(_brand_page(
        "✓", "#10b981", "Demo cancelled",
        "Your GigsFill demo has been cancelled and our team has been notified. Thanks for letting us know.",
        cta_html='<a href="https://gigsfill.com" style="display:inline-block;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;padding:12px 26px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;margin-top:8px;">Back to gigsfill.com</a>'
    ))


@router.get("/api/demo-request/reschedule/{token}")
def prospect_reschedule_context(token: str, db=Depends(get_db)):
    """Public JSON — the homepage modal calls this to prime itself with
    the prospect's existing name/email/entity when they click 'Change
    time' in the confirmation email. Also validates the token before
    the modal even opens.
    """
    req_id = _verify_reschedule_token(token)
    row = _load_req_row(db, req_id)
    if not row:
        raise HTTPException(404, "Request not found")
    # Both `cancelled` and `declined` are allowed — the Pick-new-times
    # button on each of those emails loops back through this same
    # endpoint, treating the click as a revival of the existing thread
    # so we don't fragment one prospect into multiple demo_requests rows.
    prior_slot = _prior_scheduled_slot(row)
    return {
        "ok": True,
        "prefill": {
            "first_name": row.get("first_name") or "",
            "last_name": row.get("last_name") or "",
            "name": row.get("name") or "",
            "email": row.get("email") or "",
            "phone": row.get("phone") or "",
            "entity_type": row.get("entity_type") or "",
            "entity_name": row.get("entity_name") or "",
            "city": row.get("city") or "",
            "state": row.get("state") or "",
            "notes": row.get("notes") or "",
        },
        "current_status": row.get("status"),
        "prior_slot_human": _fmt_slot_human(prior_slot) if prior_slot else None,
    }


@router.post("/api/demo-request/reschedule/{token}")
@limiter.limit("6/hour")
async def prospect_reschedule_submit(request: Request, token: str, data: dict,
                                       db=Depends(get_db)):
    """Public POST — prospect submits 3 new preferred times. Replaces
    the row's preferred_slots, bumps slots_version (invalidating stale
    admin accept-links), moves status back to pending, and re-sends the
    admin email with fresh accept buttons.

    Editable fields limited to name/phone/notes so the same person is
    still identifiable (email is the identity here; changing entity_type
    or email would essentially be a new request).
    """
    req_id = _verify_reschedule_token(token)
    row = _load_req_row(db, req_id)
    if not row:
        raise HTTPException(404, "Request not found")
    # Cancelled + declined rows are both allowed — see prospect_reschedule_context
    # for the rationale (keeps prospect in one row, one thread).

    new_slots = _validate_slots(data.get("preferred_slots"))
    new_notes = (data.get("notes") or "").strip()[:2000] or row.get("notes")
    new_phone = (data.get("phone") or "").strip()[:40] or row.get("phone")

    prior_slot = _prior_scheduled_slot(row)
    prior_status = row["status"]
    prior_slots = []
    try:
        prior_slots = json.loads(row.get("preferred_slots_json") or "[]")
    except Exception:
        pass

    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    new_version = int(row.get("slots_version") or 1) + 1
    # Terminal-state (cancelled OR declined) → revival, active → reschedule.
    # Same DB moves for both, different history label so downstream
    # reporting can distinguish "prospect changed their mind mid-flow"
    # from "prospect came back after we told them no / cancelled".
    # Both land on status='pending' for admin.
    is_revival = prior_status in ("cancelled", "declined")
    hist_action = "revived_by_prospect" if is_revival else "rescheduled_by_prospect"
    db.execute(text("""UPDATE demo_requests
                       SET status = 'pending',
                           preferred_slots_json = :sl,
                           slots_version = :sv,
                           scheduled_slot_index = NULL,
                           scheduled_at = NULL,
                           reminder_sent_at = NULL,
                           responded_at = NULL,
                           notes = :nt,
                           phone = :ph
                       WHERE id = :id"""),
               {"sl": json.dumps(new_slots), "sv": new_version,
                "nt": new_notes, "ph": new_phone, "id": req_id})
    _append_history(db, req_id, {
        "action": hist_action,
        "actor": "prospect_link",
        "prior_status": prior_status,
        "prior_slot": prior_slot,
        "prior_preferred_slots": prior_slots,
        "new_preferred_slots": new_slots,
        "new_slots_version": new_version,
    })
    db.commit()

    # Re-notify admin with fresh accept-links pinned to the new version
    fresh_row = _load_req_row(db, req_id) or row
    try:
        smtp = _get_smtp(db)
        site_url = _get_site_url(db)
        admin_email = _get_admin_email(db)
        subject_tag = "[Demo Revived]" if is_revival else "[Demo Rescheduled]"
        subject = (
            f"{subject_tag} {fresh_row.get('name','A prospect')} — "
            f"{fresh_row.get('entity_name') or fresh_row.get('entity_type') or 'GigsFill'}"
        )
        html = _build_admin_reschedule_alert_html(
            fresh_row, prior_slot, new_slots, site_url, slots_version=new_version
        )
        _send_email_via_smtp(smtp, admin_email, subject, html)
    except Exception as e:
        logger.error(f"reschedule notify failed for demo #{req_id}: {e}", exc_info=True)

    logger.info(f"[DEMO_REQUEST] {hist_action} #{req_id} v{new_version} slots={len(new_slots)}")
    return {
        "ok": True,
        "message": ("Thanks for coming back — our team will confirm one of your new times shortly."
                    if is_revival
                    else "Thanks! Our team will confirm one of your new times shortly."),
    }


def _h_escape(s: str) -> str:
    """Local escape used by prospect_cancel_landing so we don't import html
    every call. Small enough to inline."""
    import html as _h
    return _h.escape(s or "")


# ─────────────────────────── morning-of reminder ───────────────────────────

def _build_prospect_reminder_html(req_row: dict, slot: dict, local_tz_label: str,
                                    site_url: str = "", meeting_url: str = "") -> str:
    """Prospect-side reminder — friendly, brief, includes the same
    add-to-calendar buttons + change/cancel buttons the confirmation
    email carries so if something comes up last-minute prospect can act
    without hunting for the older email."""
    import html as _h
    slot_human = _fmt_slot_human(slot)
    if not site_url:
        site_url = "https://gigsfill.com"
    cal_links = _build_calendar_links(req_row, slot, site_url, meeting_url=meeting_url)
    cal_buttons = _build_calendar_buttons_html(cal_links)
    # Mint fresh signed tokens on every reminder — same salts + TTLs as
    # the confirmation email uses, so clicking either button lands on
    # the exact same flows already validated.
    reschedule_tok = _sign_reschedule_token(int(req_row["id"]))
    cancel_tok = _sign_cancel_token(int(req_row["id"]))
    reschedule_url = f"{site_url}/?reschedule={reschedule_tok}#request-demo"
    cancel_url = f"{site_url}/demo/cancel/{cancel_tok}"
    # Jul 2026 dark-theme rebrand.
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;"><tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.4);overflow:hidden;">

<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <h1 style="margin:0;font-size:22px;font-weight:700;">Your GigsFill demo is today ⏰</h1>
</td></tr>

<tr><td style="padding:28px 32px;color:#e5e7eb;font-size:15px;line-height:1.6;">
  <p style="margin:0 0 16px;">Hi {_h.escape(req_row.get('name') or 'there')},</p>
  <p style="margin:0 0 20px;">Quick reminder — your GigsFill demo is scheduled for later today:</p>
  <div style="background:#0f172a;border:1px solid rgba(139,92,246,0.35);border-radius:8px;padding:20px;margin:0 0 20px;text-align:center;">
    <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;margin-bottom:4px;">Today at</div>
    <div style="font-size:20px;font-weight:700;color:#e5e7eb;">{_h.escape(slot_human)}</div>
  </div>

  {_fmt_meeting_line(meeting_url)}

  <p style="margin:0 0 4px;font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;">Not on your calendar yet?</p>
  {cal_buttons}

  <p style="margin:20px 0 0;">{"Nothing to prepare — just click the link above at your scheduled time." if meeting_url else "We'll send the video call link an hour before we meet. Nothing to prepare — just show up."}</p>

  <div style="margin:24px 0 0;padding-top:20px;border-top:1px solid rgba(255,255,255,0.08);">
    <p style="margin:0 0 12px;font-size:13px;color:#94a3b8;">Something come up? Change your time or let us know you can't make it:</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0;">
      <tr>
        <td align="center" style="padding:0 6px 0 0;" width="50%">
          <a href="{reschedule_url}"
             style="display:inline-block;width:92%;padding:11px 0;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-sizing:border-box;">
            📅 Change time
          </a>
        </td>
        <td align="center" style="padding:0 0 0 6px;" width="50%">
          <a href="{cancel_url}"
             style="display:inline-block;width:92%;padding:10px 0;background:transparent;border:1px solid #ef4444;color:#fca5a5;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-sizing:border-box;">
            ✗ Cancel demo
          </a>
        </td>
      </tr>
    </table>
  </div>
</td></tr>

<tr><td style="padding:16px 32px;background:#0f172a;border-top:1px solid rgba(255,255,255,0.06);text-align:center;font-size:12px;color:#6b7280;">
  GigsFill — live music, booked right.
</td></tr>

</table></td></tr></table></body></html>"""


def _build_admin_reminder_html(req_row: dict, slot: dict, site_url: str,
                                  meeting_url: str = "") -> str:
    """Admin-side heads-up — the same details the accept email had so
    you can prep without hunting through the queue. Includes the 3
    add-to-calendar buttons in case admin never actioned the original
    'Demo Confirmed' email."""
    import html as _h
    slot_human = _fmt_slot_human(slot)
    cal_links = _build_calendar_links(req_row, slot, site_url or "https://gigsfill.com",
                                        meeting_url=meeting_url)
    cal_buttons = _build_calendar_buttons_html(cal_links)
    entity_line = ""
    if req_row.get("entity_name") or req_row.get("entity_type"):
        _t = req_row.get("entity_type") or ""
        _n = req_row.get("entity_name") or ""
        entity_line = f"<div style='margin:6px 0;'><strong>{_h.escape(_t.title())}:</strong> {_h.escape(_n)}</div>"
    location_line = ""
    if req_row.get("city") or req_row.get("state"):
        loc = ", ".join([p for p in [req_row.get("city"), req_row.get("state")] if p])
        location_line = f"<div style='margin:6px 0;'><strong>Location:</strong> {_h.escape(loc)}</div>"
    phone_line = ""
    if req_row.get("phone"):
        phone_line = f"<div style='margin:6px 0;'><strong>Phone:</strong> {_h.escape(req_row['phone'])}</div>"
    notes_html = ""
    if req_row.get("notes"):
        notes_html = f"""<div style='margin-top:16px;padding:14px 16px;background:#0f172a;border-left:3px solid #06b6d4;border-radius:4px;'>
        <div style='font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;'>Their notes</div>
        <div style='font-size:14px;color:#e5e7eb;white-space:pre-wrap;line-height:1.5;'>{_h.escape(req_row['notes'])}</div>
        </div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;">
<tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">

<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;opacity:0.9;">Demo reminder</div>
  <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;">{_h.escape(req_row.get('name') or 'A prospect')} — demo later today</h1>
</td></tr>

<tr><td style="padding:24px 32px;color:#e5e7eb;font-size:14px;line-height:1.6;">
  <div style="background:#0f172a;border:1px solid rgba(139,92,246,0.35);border-radius:8px;padding:16px 18px;margin:0 0 20px;text-align:center;">
    <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Scheduled for</div>
    <div style="font-size:18px;font-weight:700;color:#e5e7eb;">{_h.escape(slot_human)}</div>
  </div>
  <div style="margin:6px 0;"><strong>Email:</strong> <a href="mailto:{_h.escape(req_row.get('email','') or '')}" style="color:#7dd3fc !important;text-decoration:underline;">{_h.escape(req_row.get('email','') or '(no email)')}</a></div>
  {phone_line}
  {entity_line}
  {location_line}
  {notes_html}

  {_fmt_meeting_line(meeting_url)}

  <div style="margin-top:20px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06);">
    <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:4px;">Not on your calendar yet?</div>
    {cal_buttons}
  </div>

  <div style="margin-top:20px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06);">
    <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:8px;">Something come up on our end?</div>
    <p style="margin:0 0 10px;color:#94a3b8;font-size:13px;">Both actions open the admin panel where you can act on the row. The prospect will be emailed accordingly.</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0;">
      <tr>
        <td align="center" style="padding:0 6px 0 0;" width="50%">
          <a href="{site_url}/app/admin.html?tab=demos#row-{req_row.get('id','')}"
             style="display:inline-block;width:92%;padding:10px 0;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;text-align:center;box-sizing:border-box;">
            ↻ Change time
          </a>
        </td>
        <td align="center" style="padding:0 0 0 6px;" width="50%">
          <a href="{site_url}/app/admin.html?tab=demos#row-{req_row.get('id','')}"
             style="display:inline-block;width:92%;padding:9px 0;background:transparent;border:1px solid rgba(239,68,68,0.6);color:#fca5a5;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;text-align:center;box-sizing:border-box;">
            ✗ Cancel demo
          </a>
        </td>
      </tr>
    </table>
  </div>

  <div style="margin-top:20px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06);text-align:center;">
    <a href="{site_url}/app/admin.html?tab=demos" style="color:#94a3b8;font-size:13px;text-decoration:none;border-bottom:1px dashed rgba(148,163,184,0.4);">View in admin panel →</a>
  </div>
</td></tr>

</table></td></tr></table></body></html>"""


def send_pending_demo_reminders(cursor) -> int:
    """Fire morning-of reminders for scheduled demos. Called every scheduler
    tick — idempotent (reminder_sent_at column prevents re-fire). Rule:
    fire at 6 AM in the prospect's local timezone on the calendar day of
    the demo. If the tick misses 6:00 exactly, catch up any time before
    the demo start.

    Returns the number of (prospect, admin) email pairs fired.
    """
    try:
        from datetime import datetime as _dt, timezone as _dt_tz
        from zoneinfo import ZoneInfo
        from backend.utils import US_STATE_TIMEZONES
    except Exception as e:
        logger.error(f"reminder deps import failed: {e}")
        return 0

    try:
        cursor.execute("""
            SELECT id, name, first_name, last_name, email, phone, entity_type,
                   entity_name, city, state, notes,
                   preferred_slots_json, scheduled_slot_index, scheduled_at,
                   meeting_url
              FROM demo_requests
             WHERE status = 'scheduled'
               AND reminder_sent_at IS NULL
               AND scheduled_at IS NOT NULL
        """)
        rows = cursor.fetchall()
    except Exception as e:
        logger.warning(f"reminder scan failed: {e}")
        return 0

    if not rows:
        return 0

    # Build a lightweight `db`-like wrapper so we can reuse the smtp/site
    # helpers that take the SQLAlchemy Session. The two helpers only call
    # `db.execute(...).scalar()`, so a shim over cursor is enough.
    class _CursorDbShim:
        def __init__(self, c): self._c = c
        def execute(self, sql_obj, params=None):
            _sql_str = str(sql_obj) if not isinstance(sql_obj, str) else sql_obj
            # Convert :name placeholders → ? for sqlite when needed. Our helpers
            # use no params on these queries so this branch is defensive.
            class _Res:
                def __init__(self, cur): self._cur = cur
                def scalar(self):
                    r = self._cur.fetchone()
                    if r is None: return None
                    return r[0] if not isinstance(r, dict) else next(iter(r.values()))
            self._c.execute(_sql_str)
            return _Res(self._c)

    _shim = _CursorDbShim(cursor)
    smtp = None
    try:
        from backend.scheduler import get_smtp_settings
        smtp = get_smtp_settings(cursor)
    except Exception as e:
        logger.error(f"reminder: SMTP unavailable — {e}")
        return 0
    try:
        site_url = _get_site_url(_shim)
    except Exception:
        site_url = "https://gigsfill.com"
    try:
        admin_email = _get_admin_email(_shim)
    except Exception:
        admin_email = _ADMIN_EMAIL_DEFAULT
    try:
        platform_meeting_url = _get_platform_meeting_url(_shim)
    except Exception:
        platform_meeting_url = ""

    now_utc = _dt.now(_dt_tz.utc)
    fired = 0

    for r in rows:
        row = dict(r) if not isinstance(r, dict) else r
        try:
            state = (row.get("state") or "").strip().upper()
            tz_name = US_STATE_TIMEZONES.get(state) if state else None
            tz_name = tz_name or "America/Los_Angeles"
            try:
                prospect_tz = ZoneInfo(tz_name)
            except Exception:
                prospect_tz = ZoneInfo("America/Los_Angeles")

            # scheduled_at is naive Pacific ("YYYY-MM-DDTHH:MM:SS")
            try:
                sched_naive = _dt.strptime(row["scheduled_at"], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                logger.warning(f"reminder: unparseable scheduled_at for #{row['id']}: {row.get('scheduled_at')!r}")
                continue
            pacific = ZoneInfo("America/Los_Angeles")
            sched_pacific = sched_naive.replace(tzinfo=pacific)
            sched_local = sched_pacific.astimezone(prospect_tz)
            now_local = now_utc.astimezone(prospect_tz)

            # Only fire on the calendar day of the demo, at/after 6 AM
            # local, and before the demo has actually started (don't
            # send a "today" reminder for a demo that's already begun).
            if now_local.date() != sched_local.date():
                continue
            if now_local.hour < 6:
                continue
            if now_local >= sched_local:
                continue

            # Load the chosen slot (for accurate rendering)
            try:
                slots = json.loads(row.get("preferred_slots_json") or "[]")
                idx = int(row.get("scheduled_slot_index") or 0)
                slot = slots[idx] if 0 <= idx < len(slots) else slots[0]
            except Exception:
                logger.warning(f"reminder: bad slots json for #{row['id']}")
                continue

            # Send prospect + admin emails
            meeting_url = (row.get("meeting_url") or "").strip() or platform_meeting_url
            p_html = _build_prospect_reminder_html(row, slot, tz_name, site_url, meeting_url)
            a_html = _build_admin_reminder_html(row, slot, site_url, meeting_url)
            p_subj = f"Your GigsFill demo is today — {_fmt_slot_human(slot)}"
            a_subj = f"[Demo Today] {row.get('name','')} — {_fmt_slot_human(slot)}"

            p_ok = _send_email_via_smtp(smtp, row["email"], p_subj, p_html)
            a_ok = _send_email_via_smtp(smtp, admin_email, a_subj, a_html)

            if p_ok or a_ok:
                # Stamp so we never re-fire, even on partial success.
                stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
                cursor.execute(
                    "UPDATE demo_requests SET reminder_sent_at = ? WHERE id = ?",
                    (stamp, row["id"])
                )
                try:
                    cursor.connection.commit()
                except Exception:
                    pass
                fired += 1
                logger.info(
                    f"[DEMO_REMINDER] fired #{row['id']} → prospect={row['email']}(ok={p_ok}) "
                    f"admin={admin_email}(ok={a_ok})  local_tz={tz_name}  slot={sched_local.strftime('%Y-%m-%d %H:%M %Z')}"
                )
            else:
                logger.error(f"[DEMO_REMINDER] both sends failed for #{row['id']} — leaving unstamped for retry")
        except Exception as e:
            logger.error(f"[DEMO_REMINDER] loop error on row #{row.get('id')}: {e}", exc_info=True)
            continue

    return fired


# ─── Daily admin digest for unresponded demo requests (2026-07-25) ─────────
# Fired at 6 AM Pacific by the scheduler when there are pending demo
# requests admin hasn't responded to. Email contains every pending row +
# per-slot Accept buttons (same signed tokens as the original "new demo
# request" email), so admin can respond directly from the digest.
# Idempotent per-day via a platform_settings row.
def send_admin_pending_demo_digest(cursor) -> int:
    """Send one summary email to admin listing every pending demo request.
    Only slots that are still in the future render as green Accept buttons —
    past slots are shown greyed out with a "past" tag. Returns 1 if sent,
    0 if nothing pending, -1 on error.
    """
    try:
        from datetime import datetime as _dt, timezone as _dt_tz
        from zoneinfo import ZoneInfo
        import html as _h
    except Exception as e:
        logger.error(f"[ADMIN_DIGEST] import failed: {e}")
        return -1

    try:
        cursor.execute("""
            SELECT id, name, first_name, last_name, email, phone, entity_type,
                   entity_name, city, state, notes, preferred_slots_json,
                   slots_version, created_at
              FROM demo_requests
             WHERE status = 'pending'
             ORDER BY created_at ASC
        """)
        rows = cursor.fetchall()
    except Exception as e:
        logger.warning(f"[ADMIN_DIGEST] scan failed: {e}")
        return -1

    if not rows:
        logger.info("[ADMIN_DIGEST] no pending demo requests — skipping")
        return 0

    class _CursorDbShim:
        def __init__(self, c): self._c = c
        def execute(self, sql_obj, params=None):
            _sql_str = str(sql_obj) if not isinstance(sql_obj, str) else sql_obj
            class _Res:
                def __init__(self, cur): self._cur = cur
                def scalar(self):
                    r = self._cur.fetchone()
                    if r is None: return None
                    return r[0] if not isinstance(r, dict) else next(iter(r.values()))
            self._c.execute(_sql_str)
            return _Res(self._c)

    _shim = _CursorDbShim(cursor)
    smtp = None
    try:
        from backend.scheduler import get_smtp_settings
        smtp = get_smtp_settings(cursor)
    except Exception as e:
        logger.error(f"[ADMIN_DIGEST] SMTP unavailable: {e}")
        return -1
    try:
        site_url = _get_site_url(_shim)
    except Exception:
        site_url = "https://gigsfill.com"
    try:
        admin_email = _get_admin_email(_shim)
    except Exception:
        admin_email = _ADMIN_EMAIL_DEFAULT

    # Build one section per pending row
    sections = []
    total_pending = 0
    total_actionable = 0  # rows with at least one future slot
    for r in rows:
        row = dict(r) if not isinstance(r, dict) else r
        try:
            slots = json.loads(row.get("preferred_slots_json") or "[]")
        except Exception:
            slots = []
        if not slots:
            continue
        total_pending += 1
        row_id = int(row["id"])
        slots_v = int(row.get("slots_version") or 1)

        try:
            created_dt = _dt.fromisoformat(str(row.get("created_at") or "").replace("Z", "+00:00"))
            age_hours = int((_dt.now(_dt_tz.utc) - created_dt.replace(tzinfo=_dt_tz.utc)).total_seconds() // 3600)
            age_str = f"{age_hours}h ago" if age_hours < 48 else f"{age_hours // 24}d ago"
        except Exception:
            age_str = "recently"

        # Per-slot card (accept button OR "past" pill)
        slot_cards = ""
        any_future = False
        for i, slot in enumerate(slots):
            is_past = _slot_is_past(slot)
            if not is_past:
                any_future = True
            slot_label = _h.escape(_fmt_slot_human(slot))
            if is_past:
                slot_cards += f"""
        <div style="margin:0 0 8px;padding:10px 14px;background:#0b0d12;border:1px solid rgba(255,255,255,0.06);border-radius:6px;color:#4b5563;font-size:13px;">
          <span style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#4b5563;margin-right:6px;">#{i+1}</span>
          <span style="text-decoration:line-through;">{slot_label}</span>
          <span style="float:right;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#6b7280;font-weight:600;">Past</span>
        </div>"""
            else:
                tok = _sign_accept_token(row_id, i, slots_v)
                accept_url = f"{site_url}/api/demo-request/{tok}/accept"
                slot_cards += f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 8px;">
          <tr><td style="background:#0f172a;border:1px solid rgba(139,92,246,0.35);border-radius:6px;padding:12px 14px;">
            <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Preferred #{i+1}</div>
            <div style="font-size:14px;color:#e5e7eb;font-weight:600;margin-bottom:10px;">{slot_label}</div>
            <a href="{accept_url}" style="display:inline-block;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;padding:8px 18px;border-radius:5px;text-decoration:none;font-size:13px;font-weight:600;">✓ Accept this slot</a>
          </td></tr>
        </table>"""

        if any_future:
            total_actionable += 1

        # Contact bits
        phone_line = f'<div style="margin:4px 0;color:#94a3b8;font-size:13px;"><strong style="color:#cbd5e1;">Phone:</strong> {_h.escape(row.get("phone") or "")}</div>' if row.get("phone") else ""
        entity_line = ""
        if row.get("entity_name") or row.get("entity_type"):
            _t = (row.get("entity_type") or "").title()
            _n = row.get("entity_name") or ""
            entity_line = f'<div style="margin:4px 0;color:#94a3b8;font-size:13px;"><strong style="color:#cbd5e1;">{_h.escape(_t)}:</strong> {_h.escape(_n)}</div>'
        loc_line = ""
        if row.get("city") or row.get("state"):
            _loc = ", ".join([p for p in [row.get("city"), row.get("state")] if p])
            loc_line = f'<div style="margin:4px 0;color:#94a3b8;font-size:13px;"><strong style="color:#cbd5e1;">Location:</strong> {_h.escape(_loc)}</div>'
        notes_line = ""
        if row.get("notes"):
            notes_line = f'<div style="margin-top:10px;padding:10px 12px;background:#0f172a;border-left:3px solid #06b6d4;border-radius:0 4px 4px 0;font-size:13px;color:#e5e7eb;white-space:pre-wrap;line-height:1.5;">{_h.escape(row["notes"])}</div>'
        # Warn banner if all slots past
        warn_banner = ""
        if not any_future:
            warn_banner = ('<div style="margin:0 0 12px;padding:10px 14px;background:rgba(239,68,68,0.12);'
                           'border:1px solid rgba(239,68,68,0.35);border-radius:6px;color:#fca5a5;'
                           'font-size:13px;font-weight:600;">'
                           '⚠ All preferred times have passed — reply to the prospect or open the admin panel to counter-propose new times.</div>')

        sections.append(f"""
    <tr><td style="padding:20px 32px;border-top:1px solid rgba(255,255,255,0.06);">
      <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px;">
        <div style="font-size:16px;font-weight:700;color:#e5e7eb;">{_h.escape(row.get("name") or "(no name)")}</div>
        <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.05em;">Received {age_str}</div>
      </div>
      <div style="margin:4px 0;color:#94a3b8;font-size:13px;"><strong style="color:#cbd5e1;">Email:</strong> <a href="mailto:{_h.escape(row.get("email") or "")}" style="color:#7dd3fc;text-decoration:underline;">{_h.escape(row.get("email") or "")}</a></div>
      {phone_line}
      {entity_line}
      {loc_line}
      {notes_line}
      {warn_banner}
      <div style="margin-top:14px;">{slot_cards}</div>
    </td></tr>""")

    if not sections:
        logger.info("[ADMIN_DIGEST] pending rows had no valid slots — skipping")
        return 0

    subject_prefix = f"[GigsFill] {total_pending} pending demo request" + ("" if total_pending == 1 else "s") + " awaiting your response"

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f1419;">
<tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;margin:0 auto;background:#151b28;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.35);">
<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;opacity:0.9;">Daily Reminder</div>
  <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;">{total_pending} pending demo request{"" if total_pending == 1 else "s"}</h1>
  <div style="margin-top:6px;font-size:13px;opacity:0.9;">{total_actionable} still ha{"s" if total_actionable == 1 else "ve"} at least one future time slot you can accept.</div>
</td></tr>
<tr><td style="padding:20px 32px;color:#cbd5e1;font-size:14px;line-height:1.55;">
  Click any Accept button below to schedule that slot directly (no login needed), or open the panel to counter-propose new times.
</td></tr>
{"".join(sections)}
<tr><td style="padding:20px 32px 28px;text-align:center;border-top:1px solid rgba(255,255,255,0.06);">
  <a href="{site_url}/app/admin.html?tab=demos" style="display:inline-block;padding:10px 22px;background:rgba(139,92,246,0.15);border:1px solid rgba(139,92,246,0.4);border-radius:6px;color:#c4b5fd;text-decoration:none;font-size:14px;font-weight:600;">Open the admin panel →</a>
  <div style="margin-top:14px;font-size:11px;color:#6b7280;">This reminder is sent every morning at 6&nbsp;AM Pacific while there are pending demo requests.</div>
</td></tr>
</table></td></tr></table></body></html>"""

    ok = _send_email_via_smtp(smtp, admin_email, subject_prefix, html)
    logger.info(f"[ADMIN_DIGEST] sent to {admin_email}: ok={ok} pending={total_pending} actionable={total_actionable}")
    return 1 if ok else -1
