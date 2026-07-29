"""Public "Send us a note" contact form for pre-signup visitors.

Sits alongside demo_requests as the second pre-launch capture surface —
demo_requests forces a meeting commitment, this is for "just have a
question." Data model is minimal (name, email, message); the same
rate-limit + honeypot pattern as demo_requests keeps bot traffic out.

Admin gets an inbox-style tab in the admin panel with status
transitions: new → replied / archived (both terminal). Replies are
sent through the platform SMTP and logged into `replies_json` on the
row so the thread stays visible in the panel across visits.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text
from datetime import datetime
import json
import logging
import os
import re

from backend.db import get_db
from backend.routes.admin import check_admin
from backend.rate_limiter import limiter
from backend.utils import log_admin_action

# HMAC-signed reply token embedded in every admin-reply email. Lets
# the prospect click back into a branded reply page on gigsfill.com
# without any login — same pattern as demo_requests' cancel/reschedule
# tokens. 90-day TTL is long enough for a real conversation to breathe
# but not indefinite. Key comes from GIGSFILL_SECRET_KEY, same source
# as auth.py.
_SECRET_KEY = os.environ.get("GIGSFILL_SECRET_KEY", "")
if not _SECRET_KEY:
    try:
        from backend.routes.auth import _SECRET_KEY as _auth_key
        _SECRET_KEY = _auth_key
    except Exception:
        pass
_reply_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="contact-reply")
# Distinct salt for admin-reply tokens so a leaked prospect token
# can't be used to reply *as* admin. Access to admin's inbox is the
# implicit auth boundary — same trust model as the mailto: Reply-To.
_admin_reply_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="contact-admin-reply")
_REPLY_MAX_AGE = 60 * 60 * 24 * 90


def _sign_reply_token(msg_id: int) -> str:
    return _reply_serializer.dumps({"id": int(msg_id)})


def _verify_reply_token(token: str) -> int:
    try:
        payload = _reply_serializer.loads(token, max_age=_REPLY_MAX_AGE)
        return int(payload.get("id") or 0)
    except SignatureExpired:
        raise HTTPException(410, "This reply link has expired.")
    except BadSignature:
        raise HTTPException(400, "Invalid reply link.")


def _sign_admin_reply_token(msg_id: int) -> str:
    return _admin_reply_serializer.dumps({"id": int(msg_id)})


def _verify_admin_reply_token(token: str) -> int:
    try:
        payload = _admin_reply_serializer.loads(token, max_age=_REPLY_MAX_AGE)
        return int(payload.get("id") or 0)
    except SignatureExpired:
        raise HTTPException(410, "This reply link has expired.")
    except BadSignature:
        raise HTTPException(400, "Invalid reply link.")

logger = logging.getLogger("gigsfill.contact")
router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ADMIN_EMAIL_DEFAULT = "support@gigsfill.com"


def _get_admin_email(db) -> str:
    """Routes contact-form notifications to the SUPPORT mailbox
    (support@gigsfill.com), not the demo-pipeline admin address. These
    are two different inboxes on purpose: demo requests are a sales
    signal, contact form notes are a general-questions channel.

    Prefers the `support_email` platform_settings row (already used by
    the support-ticket outbound SMTP FROM address, so semantic reuse),
    falls back to the hard-coded `support@gigsfill.com` if unset.
    """
    try:
        row = db.execute(text(
            "SELECT setting_value FROM platform_settings WHERE setting_key='support_email'"
        )).scalar()
        if row and _EMAIL_RE.match(row):
            return row
    except Exception:
        pass
    return _ADMIN_EMAIL_DEFAULT


def _get_smtp():
    """Contact-form outbound SMTP. Reuses the platform mail session
    (server/port/authenticated username) but overrides the `From:`
    header to appear as **GigsFill Support <support@gigsfill.com>**
    instead of the platform booking address — semantic distinction
    between "GigsFill Booking" (transactional booking/payout emails)
    and "GigsFill Support" (contact-form replies).

    Envelope-from stays as the authenticated `platform_email`
    (booked@…) so SPF / auth stay valid; only the visible header
    changes. If `support_email` / `support_email_from_name` are
    configured in platform_settings, those override the hard-coded
    defaults."""
    from backend.db import get_db_connection as _c
    conn = _c()
    try:
        cur = conn.cursor()
        from backend.scheduler import get_smtp_settings
        smtp = dict(get_smtp_settings(cur))
        cur.execute(
            "SELECT setting_key, setting_value FROM platform_settings "
            "WHERE setting_key IN ('support_email', 'support_email_from_name')"
        )
        overrides = {k: v for k, v in cur.fetchall()}
        smtp['from_email'] = (overrides.get('support_email') or '').strip() or 'support@gigsfill.com'
        smtp['from_name']  = (overrides.get('support_email_from_name') or '').strip() or 'GigsFill Support'
        return smtp
    finally:
        conn.close()


def _send_admin_notification(smtp, admin_email: str, msg: dict, site_url: str) -> bool:
    """Small alert email so admin doesn't have to poll the panel to see
    new messages. Contains the full message, a `Reply-To` header set to
    the sender, AND a branded **Reply in admin panel** button that deep-
    links straight to the reply modal for this message so the whole
    conversation is captured in-app."""
    if not smtp or not smtp.get("username") or not smtp.get("password"):
        return False
    try:
        import html as _h
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.utils import formataddr
        import smtplib

        subject = f"[Contact] {msg['name']} — {msg['message'][:60]}"
        panel_url = f"{site_url}/app/admin.html?tab=support&reply={msg['id']}"
        # Signed no-login URL — mirrors the prospect's "Continue conversation"
        # button. Clicking opens a branded reply page in-browser without
        # touching the admin panel; access to admin's inbox is the auth.
        quick_reply_url = f"{site_url}/contact/admin-reply/{_sign_admin_reply_token(msg['id'])}"
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">
<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;opacity:0.9;">New contact message</div>
  <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;">{_h.escape(msg['name'])}</h1>
</td></tr>
<tr><td style="padding:24px 32px;color:#e5e7eb;font-size:14px;line-height:1.6;">
  <div style="margin:6px 0;"><strong>Email:</strong> <a href="mailto:{_h.escape(msg['email'])}" style="color:#7dd3fc !important;text-decoration:underline;">{_h.escape(msg['email'])}</a></div>
  <div style="margin-top:16px;padding:14px 16px;background:#0f172a;border-left:3px solid #06b6d4;border-radius:4px;font-size:14px;color:#e5e7eb;white-space:pre-wrap;line-height:1.5;">{_h.escape(msg['message'])}</div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:22px 0 0;">
    <tr>
      <td align="center" style="padding:0 6px 0 0;" width="50%">
        <a href="{quick_reply_url}"
           style="display:inline-block;width:92%;padding:11px 0;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-sizing:border-box;">
          ✉ Reply from browser
        </a>
      </td>
      <td align="center" style="padding:0 0 0 6px;" width="50%">
        <a href="{panel_url}"
           style="display:inline-block;width:92%;padding:10px 0;background:transparent;border:1px solid rgba(148,163,184,0.4);color:#94a3b8;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-sizing:border-box;">
          🏠 Open admin panel
        </a>
      </td>
    </tr>
  </table>
  <p style="margin:12px 0 0;font-size:12px;color:#6b7280;text-align:center;line-height:1.5;">
    Browser reply skips the admin login — same flow as the prospect's Continue-conversation button. Or hit Reply — this email's Reply-To goes back to the sender.
  </p>
</td></tr>
</table></td></tr></table></body></html>"""
        mime = MIMEMultipart("alternative")
        mime["Subject"] = subject
        _fn = smtp.get("from_name") or "GigsFill"
        _fe = smtp.get("from_email") or smtp.get("username") or "noreply@gigsfill.com"
        mime["From"] = formataddr((_fn, _fe))
        mime["To"] = admin_email
        # Reply-To → sender's email so admin can hit Reply once and go
        # directly. Reduces "wait what was their address" friction.
        mime["Reply-To"] = msg["email"]
        mime.attach(MIMEText(html, "html"))

        server_host = smtp.get("server") or "smtp.gmail.com"
        port = int(smtp.get("port") or 587)
        user = smtp.get("username") or ""
        pw = smtp.get("password") or ""
        if port == 465:
            with smtplib.SMTP_SSL(server_host, port, timeout=15) as srv:
                srv.login(user, pw); srv.send_message(mime)
        elif port in (587, 2587):
            with smtplib.SMTP(server_host, port, timeout=15) as srv:
                srv.ehlo(); srv.starttls(); srv.ehlo()
                srv.login(user, pw); srv.send_message(mime)
        else:
            with smtplib.SMTP(server_host, port, timeout=15) as srv:
                srv.ehlo()
                try: srv.starttls(); srv.ehlo()
                except Exception: pass
                srv.login(user, pw); srv.send_message(mime)
        return True
    except Exception as e:
        logger.error(f"contact admin notify failed to {admin_email}: {e}", exc_info=True)
        return False


# ─── Public submit endpoint ────────────────────────────────────────

@router.post("/api/contact")
@limiter.limit("3/hour")
async def submit_contact(request: Request, data: dict, db=Depends(get_db)):
    """Public form submit. Rate-limited to 3/hour per IP (same as
    /api/demo-request) so bots can't drown the queue. Honeypot field
    silent-succeeds on non-empty."""
    name = (data.get("name") or "").strip()[:120]
    email = (data.get("email") or "").strip()[:200]
    message = (data.get("message") or "").strip()[:4000]
    hp = (data.get("_hp") or "").strip()

    # Silent-succeed on honeypot fill so bots don't know they were blocked.
    if hp:
        return {"ok": True, "message": "Thanks — we'll get back to you shortly."}

    if not name or len(name) < 2:
        raise HTTPException(400, "Please enter your name.")
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address.")
    if not message or len(message) < 5:
        raise HTTPException(400, "Please write a short message (at least a few words).")

    src_ip = request.client.host if request.client else None
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    # 2026-07-25: RETURNING id inline. Old pattern (commit + separate
    # last_insert_rowid) is per-connection so it can return a stale id
    # from another pool connection — same bug that mis-linked a demo
    # request's admin email.
    msg_id = db.execute(
        text("""INSERT INTO contact_messages
                (name, email, message, status, source_ip, created_at)
                VALUES (:n, :e, :m, 'new', :ip, :ca) RETURNING id"""),
        {"n": name, "e": email, "m": message, "ip": src_ip, "ca": now_iso}
    ).scalar()
    db.commit()

    # Best-effort admin notification
    try:
        smtp = _get_smtp()
        admin_email = _get_admin_email(db)
        # Resolve site_url from platform_settings; fall back to hosted URL.
        try:
            site_row = db.execute(text(
                "SELECT setting_value FROM platform_settings WHERE setting_key='site_url'"
            )).scalar()
            site_url = str(site_row).rstrip("/") if site_row else "https://gigsfill.com"
        except Exception:
            site_url = "https://gigsfill.com"
        _send_admin_notification(smtp, admin_email,
                                  {"id": msg_id, "name": name, "email": email, "message": message},
                                  site_url)
    except Exception as e:
        logger.error(f"contact notify failed for message #{msg_id}: {e}", exc_info=True)

    logger.info(f"[CONTACT] new #{msg_id} from {name} <{email}> from {src_ip}")
    return {"ok": True, "message": "Thanks — we'll get back to you shortly."}


# ─── Admin management endpoints ─────────────────────────────────────

def _brand_page(title: str, subtitle: str, body_html: str = "") -> str:
    """Small branded dark landing page — used for the prospect-facing
    reply pages so the visitor stays inside the GigsFill look and feel
    without a login."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title} — GigsFill</title>
<link href="/app/static/css/gigsfill.css" rel="stylesheet"></head>
<body style="margin:0;padding:0;background:#0f1419;font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;color:#e5e7eb;min-height:100vh;">
<div style="max-width:640px;margin:60px auto;padding:32px 32px 40px;background:#151b28;border:1px solid rgba(255,255,255,0.08);border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.4);">
  <h1 style="font-size:22px;font-weight:700;background:linear-gradient(135deg,#8b5cf6,#06b6d4);background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:0 0 8px;">{title}</h1>
  <p style="font-size:14px;color:#9ca3af;line-height:1.6;margin:0 0 20px;">{subtitle}</p>
  {body_html}
  <hr style="border:0;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0 18px;">
  <p style="font-size:12px;color:#6b7280;margin:0;text-align:center;">GigsFill — live music, booked right.</p>
</div></body></html>"""


def _fmt_ts_human(iso: str) -> str:
    """Format a **naive-UTC** ISO timestamp (as the endpoints write it
    via `datetime.utcnow().isoformat()`) as `"Jul 19, 8:00 AM PT"` —
    converted to Pacific Time + labeled `PT` so the display matches
    what the admin panel shows (viewer-local, which for the Pacific-
    based admin is Pacific) AND is unambiguous when the same email
    lands in an out-of-TZ prospect's inbox.

    Uses `%-I` (Linux glibc) for hour without leading zero. The server
    is Linux so this is safe. Falls back to the raw string on failure.
    """
    if not iso:
        return ""
    try:
        from datetime import datetime as _dt, timezone as _tz_utc
        from zoneinfo import ZoneInfo
        # Timestamps land here as naive UTC — attach the UTC tzinfo and
        # convert. Trim to seconds precision + strip trailing Z if any.
        s = str(iso).rstrip("Z")
        naive = _dt.fromisoformat(s[:19])
        pacific = naive.replace(tzinfo=_tz_utc.utc).astimezone(ZoneInfo("America/Los_Angeles"))
        return pacific.strftime("%b %d, %-I:%M %p PT")
    except Exception:
        return str(iso)


def _render_thread_for_reply_page(row: dict) -> str:
    """Render the running conversation (original note + all replies)
    as a threaded read-only view above the reply textarea. Rendered
    **newest-first** so the message being responded to sits right
    above the textarea — no scrolling to find what to reply to.
    Prospect messages are cyan, admin replies are purple; same colour
    coding the admin panel uses."""
    import html as _h
    try:
        replies = json.loads(row.get("replies_json") or "[]") or []
        if not isinstance(replies, list):
            replies = []
    except Exception:
        replies = []
    parts = []
    # Newest replies first (reverse-chronological)
    for rep in reversed(replies):
        actor = (rep.get("actor") or ("admin" if rep.get("actor_user_id") is not None else "prospect")).lower()
        is_admin = actor == "admin"
        color = "#a78bfa" if is_admin else "#06b6d4"
        bg = "rgba(139,92,246,0.06)" if is_admin else "rgba(6,182,212,0.06)"
        label = "GigsFill Team" if is_admin else "You"
        label_color = "#c4b5fd" if is_admin else "#7dd3fc"
        indent = "margin-left:24px;" if is_admin else ""
        parts.append(
            f'<div style="padding:12px 14px;background:{bg};border-left:3px solid {color};border-radius:0 4px 4px 0;margin-bottom:8px;{indent}">'
              f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:{label_color};font-weight:700;margin-bottom:6px;">'
                f'{label} · {_h.escape(_fmt_ts_human(rep.get("ts", "")))}'
              '</div>'
              f'<div style="font-size:14px;color:#e5e7eb;white-space:pre-wrap;line-height:1.5;">{_h.escape(rep.get("message", ""))}</div>'
            '</div>'
        )
    # Original note at the BOTTOM (oldest message)
    parts.append(
        '<div style="padding:12px 14px;background:rgba(6,182,212,0.06);border-left:3px solid #06b6d4;border-radius:0 4px 4px 0;margin-bottom:8px;">'
          '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:#7dd3fc;font-weight:700;margin-bottom:6px;">'
            f'You · {_h.escape(_fmt_ts_human(row.get("created_at", "")))} · Original note'
          '</div>'
          f'<div style="font-size:14px;color:#e5e7eb;white-space:pre-wrap;line-height:1.5;">{_h.escape(row.get("message", ""))}</div>'
        '</div>'
    )
    return '<div style="max-height:340px;overflow-y:auto;margin:0 -4px 20px;padding:0 4px;">' + "".join(parts) + '</div>'


@router.get("/contact/reply/{token}", response_class=HTMLResponse)
def prospect_reply_landing(token: str, db=Depends(get_db)):
    """Public landing page for the prospect's `Continue conversation`
    button in the admin's reply email. Renders the full thread + a
    textarea; POSTs to the same URL to append."""
    msg_id = _verify_reply_token(token)
    row = db.execute(
        text("SELECT id, name, email, message, status, replies_json, created_at FROM contact_messages WHERE id = :id"),
        {"id": msg_id}
    ).mappings().first()
    if not row:
        return HTMLResponse(_brand_page("Conversation not found",
            "This reply link no longer points at an active conversation. If this looks wrong, reply to the last email you received from us directly."))
    row = dict(row)
    thread_html = _render_thread_for_reply_page(row)
    form_html = f"""
    <form method="post" action="/contact/reply/{token}" style="margin:0;">
      <label style="display:block;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Your reply</label>
      <textarea name="message" required minlength="3" maxlength="8000" placeholder="Type your reply…"
        style="width:100%;padding:12px 14px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#e5e7eb;font-size:14px;font-family:inherit;box-sizing:border-box;min-height:140px;resize:vertical;"></textarea>
      <div style="margin-top:14px;text-align:right;">
        <button type="submit"
          style="padding:11px 26px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border:0;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;">
          ✉ Send reply
        </button>
      </div>
      <p style="margin:12px 0 0;font-size:12px;color:#6b7280;line-height:1.5;">Sends directly to our team. We'll respond by email.</p>
    </form>"""
    return HTMLResponse(_brand_page(
        f"Reply to GigsFill",
        f"Continuing the conversation you started as <strong style='color:#e5e7eb;'>{row.get('name','you')}</strong> &lt;{row.get('email','')}&gt;",
        thread_html + form_html
    ))


@router.post("/contact/reply/{token}", response_class=HTMLResponse)
async def prospect_reply_submit(token: str, request: Request, db=Depends(get_db)):
    """Public POST — prospect's reply from the branded page. Body comes
    in as form-urlencoded (native <form> submit), NOT JSON. Appends to
    `replies_json` with `actor='prospect'`, flips status back to 'new'
    so admin sees it in the "New only" filter + tab badge, and emails
    admin a heads-up with a deep-link to the reply modal."""
    msg_id = _verify_reply_token(token)
    row = db.execute(
        text("SELECT id, name, email, message, status, replies_json FROM contact_messages WHERE id = :id"),
        {"id": msg_id}
    ).mappings().first()
    if not row:
        return HTMLResponse(_brand_page("Conversation not found",
            "This reply link no longer points at an active conversation."))
    row = dict(row)

    # Read form field
    form = await request.form()
    body_text = (form.get("message") or "").strip()[:8000]
    if not body_text or len(body_text) < 3:
        # Re-render landing with an error prepended
        thread_html = _render_thread_for_reply_page(row)
        err = '<div style="padding:10px 12px;background:rgba(248,113,113,0.1);border:1px solid rgba(248,113,113,0.35);border-radius:6px;color:#fca5a5;font-size:13px;margin-bottom:14px;">Please write at least a few words before sending.</div>'
        form_html = f"""
        <form method="post" action="/contact/reply/{token}" style="margin:0;">
          {err}
          <label style="display:block;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Your reply</label>
          <textarea name="message" required minlength="3" maxlength="8000" placeholder="Type your reply…"
            style="width:100%;padding:12px 14px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#e5e7eb;font-size:14px;font-family:inherit;box-sizing:border-box;min-height:140px;resize:vertical;"></textarea>
          <div style="margin-top:14px;text-align:right;">
            <button type="submit" style="padding:11px 26px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border:0;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;">✉ Send reply</button>
          </div>
        </form>"""
        return HTMLResponse(_brand_page("Reply to GigsFill", "", thread_html + form_html))

    # Append to thread + flip status back to 'new' so admin re-sees it.
    try:
        thread = json.loads(row.get("replies_json") or "[]") or []
        if not isinstance(thread, list):
            thread = []
    except Exception:
        thread = []
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    thread.append({
        "ts": now_iso,
        "message": body_text,
        "actor": "prospect",
    })
    db.execute(text("""UPDATE contact_messages
                        SET replies_json = :thread,
                            status = 'new',
                            responded_at = NULL
                       WHERE id = :id"""),
                {"thread": json.dumps(thread), "id": msg_id})
    db.commit()

    # Notify admin
    try:
        smtp = _get_smtp()
        admin_email = _get_admin_email(db)
        try:
            site_row = db.execute(text(
                "SELECT setting_value FROM platform_settings WHERE setting_key='site_url'"
            )).scalar()
            site_url = str(site_row).rstrip("/") if site_row else "https://gigsfill.com"
        except Exception:
            site_url = "https://gigsfill.com"
        _send_admin_prospect_reply_notification(smtp, admin_email, dict(row), body_text, site_url)
    except Exception as e:
        logger.error(f"contact prospect-reply notify failed for #{msg_id}: {e}", exc_info=True)

    logger.info(f"[CONTACT] prospect_reply #{msg_id} from {row['email']} ({len(body_text)} chars)")

    # Thank-you page
    return HTMLResponse(_brand_page(
        "Reply sent ✓",
        "Thanks — your reply landed with our team. We'll get back to you shortly.",
        '<div style="text-align:center;margin-top:10px;">'
          '<a href="https://gigsfill.com" style="display:inline-block;padding:10px 22px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">Back to gigsfill.com</a>'
        '</div>'
    ))


# ─── Admin quick-reply from notification email ────────────────────
# Same UX as the prospect page — no login needed, just a branded page
# with the thread + textarea. Anyone with access to the admin inbox
# can reply; that's the implicit auth boundary (same trust as the
# mailto: Reply-To fallback we've had all along).

def _render_admin_reply_page(row: dict, token: str, error: str = "") -> str:
    import html as _h
    err_block = (
        '<div style="padding:10px 12px;background:rgba(248,113,113,0.1);border:1px solid rgba(248,113,113,0.35);border-radius:6px;color:#fca5a5;font-size:13px;margin-bottom:14px;">'
        + _h.escape(error) +
        '</div>'
    ) if error else ""
    thread_html = _render_thread_for_reply_page(row)
    form_html = f"""
    <form method="post" action="/contact/admin-reply/{token}" style="margin:0;">
      {err_block}
      <label style="display:block;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Your reply</label>
      <textarea name="message" required minlength="3" maxlength="8000" placeholder="Hi {_h.escape((row.get('name','') or '').split()[0] if row.get('name') else 'there')},&#10;&#10;"
        style="width:100%;padding:12px 14px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#e5e7eb;font-size:14px;font-family:inherit;box-sizing:border-box;min-height:180px;resize:vertical;"></textarea>
      <div style="margin-top:14px;text-align:right;">
        <button type="submit"
          style="padding:11px 26px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border:0;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;">
          ✉ Send reply
        </button>
      </div>
      <p style="margin:12px 0 0;font-size:12px;color:#6b7280;line-height:1.5;">
        Sends as GigsFill, logs into the thread, and marks the row as replied. Same effect as replying from the admin panel.
      </p>
    </form>"""
    return _brand_page(
        f"Reply to {row.get('name','a note')}",
        f"Replying to <strong style='color:#e5e7eb;'>{_h.escape(row.get('name',''))}</strong> &lt;{_h.escape(row.get('email',''))}&gt;",
        thread_html + form_html
    )


@router.get("/contact/admin-reply/{token}", response_class=HTMLResponse)
def admin_reply_landing(token: str, db=Depends(get_db)):
    """Public GET for admin's quick-reply link from the notification
    email. No auth check — the signed token IS the auth artifact (only
    someone with access to admin's inbox has it)."""
    msg_id = _verify_admin_reply_token(token)
    row = db.execute(
        text("SELECT id, name, email, message, status, replies_json, created_at FROM contact_messages WHERE id = :id"),
        {"id": msg_id}
    ).mappings().first()
    if not row:
        return HTMLResponse(_brand_page("Message not found",
            "This conversation no longer exists — probably deleted from the admin panel."))
    return HTMLResponse(_render_admin_reply_page(dict(row), token))


@router.post("/contact/admin-reply/{token}", response_class=HTMLResponse)
async def admin_reply_submit(token: str, request: Request, db=Depends(get_db)):
    """Public POST — admin's browser reply. Mirrors the admin panel's
    reply endpoint (append to thread with actor='admin', flip status
    → 'replied', dispatch the outbound email to the prospect with
    the Continue-conversation button + full thread block)."""
    msg_id = _verify_admin_reply_token(token)
    row = db.execute(
        text("SELECT id, name, email, message, status, replies_json FROM contact_messages WHERE id = :id"),
        {"id": msg_id}
    ).mappings().first()
    if not row:
        return HTMLResponse(_brand_page("Message not found",
            "This conversation no longer exists."))
    row = dict(row)

    form = await request.form()
    body_text = (form.get("message") or "").strip()[:8000]
    if not body_text or len(body_text) < 3:
        return HTMLResponse(_render_admin_reply_page(row, token,
            error="Please write at least a few words before sending."))

    # Append + flip status → replied. Same DB moves as the admin panel
    # reply endpoint above (kept in sync manually — the shared thread
    # rendering helpers dedupe the interesting parts).
    try:
        thread = json.loads(row.get("replies_json") or "[]") or []
        if not isinstance(thread, list):
            thread = []
    except Exception:
        thread = []
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    thread.append({
        "ts": now_iso,
        "message": body_text,
        "actor": "admin",
        "actor_source": "quick_reply_link",
    })
    db.execute(text("""UPDATE contact_messages
                        SET replies_json = :thread,
                            status = 'replied',
                            responded_at = COALESCE(responded_at, :ra)
                       WHERE id = :id"""),
                {"thread": json.dumps(thread), "ra": now_iso, "id": msg_id})
    db.commit()

    # Send the outbound email to the prospect. Uses the PRE-write row
    # (as loaded above) so the just-sent turn threads in cleanly via
    # current_reply_body without appearing twice.
    try:
        smtp = _get_smtp()
        admin_email = _get_admin_email(db)
        try:
            site_row = db.execute(text(
                "SELECT setting_value FROM platform_settings WHERE setting_key='site_url'"
            )).scalar()
            site_url = str(site_row).rstrip("/") if site_row else "https://gigsfill.com"
        except Exception:
            site_url = "https://gigsfill.com"
        reply_url = f"{site_url}/contact/reply/{_sign_reply_token(msg_id)}"
        _send_reply_email(smtp, row, row["email"], row.get("name") or "",
                            body_text, admin_email, reply_url)
    except Exception as e:
        logger.error(f"admin quick-reply send failed for #{msg_id}: {e}", exc_info=True)

    logger.info(f"[CONTACT] admin_quick_reply #{msg_id} ({len(body_text)} chars)")
    return HTMLResponse(_brand_page(
        "Reply sent ✓",
        f"Your reply was sent to <strong style='color:#e5e7eb;'>{row.get('email','')}</strong> and logged in the admin panel.",
        '<div style="text-align:center;margin-top:10px;">'
          '<a href="/app/admin.html?tab=support" style="display:inline-block;padding:10px 22px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">Open admin panel</a>'
        '</div>'
    ))


def _send_admin_prospect_reply_notification(smtp, admin_email: str, row: dict,
                                              reply_body: str, site_url: str) -> bool:
    """Admin alert when a prospect replies via the branded page. Similar
    shape to the initial 'new contact message' email but leads with
    '{name} replied' and links to the reply modal directly."""
    if not smtp or not smtp.get("username") or not smtp.get("password"):
        return False
    try:
        import html as _h
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.utils import formataddr
        import smtplib

        subject = f"[Contact reply] {row.get('name','A prospect')} replied"
        panel_url = f"{site_url}/app/admin.html?tab=support&reply={row['id']}"
        # In-browser quick-reply URL — same signed-token flow as the
        # initial "new contact" notification. Lets admin type a reply
        # from any browser without hitting the admin panel login.
        quick_reply_url = f"{site_url}/contact/admin-reply/{_sign_admin_reply_token(row['id'])}"
        # Full running thread in the notification email, so a Gmail-side
        # search across your contact-support inbox surfaces the whole
        # conversation on any one message. Row here is PRE-write (as
        # loaded before the append), and the helper injects the new
        # prospect reply at the top via current_reply_body.
        thread_html = _build_thread_html_block(row, "prospect", reply_body)
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#151b28;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">
<tr><td style="padding:28px 32px 20px;background:linear-gradient(135deg,#06b6d4,#8b5cf6);color:#fff;">
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;font-weight:600;opacity:0.9;">Prospect reply</div>
  <h1 style="margin:6px 0 0;font-size:22px;font-weight:700;">{_h.escape(row.get('name',''))} replied</h1>
</td></tr>
<tr><td style="padding:24px 32px;color:#e5e7eb;font-size:14px;line-height:1.6;">
  <div style="margin:6px 0;"><strong>Email:</strong> <a href="mailto:{_h.escape(row.get('email',''))}" style="color:#7dd3fc !important;text-decoration:underline;">{_h.escape(row.get('email',''))}</a></div>
  <div style="margin-top:16px;padding:14px 16px;background:#0f172a;border-left:3px solid #06b6d4;border-radius:4px;font-size:14px;color:#e5e7eb;white-space:pre-wrap;line-height:1.5;">{_h.escape(reply_body)}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:22px 0 0;">
    <tr>
      <td align="center" style="padding:0 6px 0 0;" width="50%">
        <a href="{quick_reply_url}"
           style="display:inline-block;width:92%;padding:11px 0;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-sizing:border-box;">
          ✉ Reply from browser
        </a>
      </td>
      <td align="center" style="padding:0 0 0 6px;" width="50%">
        <a href="{panel_url}"
           style="display:inline-block;width:92%;padding:10px 0;background:transparent;border:1px solid rgba(148,163,184,0.4);color:#94a3b8;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-sizing:border-box;">
          🏠 Open admin panel
        </a>
      </td>
    </tr>
  </table>
  <p style="margin:12px 0 0;font-size:12px;color:#6b7280;text-align:center;">Message status has moved back to <strong style="color:#f59e0b;">New</strong> so it re-appears in the queue.</p>
  <div style="background:#f8fafc;color:#0f172a;padding:16px 20px;margin:22px -32px -24px;border-top:1px solid rgba(255,255,255,0.06);">
    {thread_html}
  </div>
</td></tr>
</table></td></tr></table></body></html>"""

        mime = MIMEMultipart("alternative")
        mime["Subject"] = subject
        _fn = smtp.get("from_name") or "GigsFill"
        _fe = smtp.get("from_email") or smtp.get("username") or "noreply@gigsfill.com"
        mime["From"] = formataddr((_fn, _fe))
        mime["To"] = admin_email
        mime["Reply-To"] = row.get("email") or admin_email
        mime.attach(MIMEText(html, "html"))

        server_host = smtp.get("server") or "smtp.gmail.com"
        port = int(smtp.get("port") or 587)
        user = smtp.get("username") or ""
        pw = smtp.get("password") or ""
        if port == 465:
            with smtplib.SMTP_SSL(server_host, port, timeout=15) as srv:
                srv.login(user, pw); srv.send_message(mime)
        elif port in (587, 2587):
            with smtplib.SMTP(server_host, port, timeout=15) as srv:
                srv.ehlo(); srv.starttls(); srv.ehlo()
                srv.login(user, pw); srv.send_message(mime)
        else:
            with smtplib.SMTP(server_host, port, timeout=15) as srv:
                srv.ehlo()
                try: srv.starttls(); srv.ehlo()
                except Exception: pass
                srv.login(user, pw); srv.send_message(mime)
        return True
    except Exception as e:
        logger.error(f"prospect-reply admin notify failed to {admin_email}: {e}", exc_info=True)
        return False


@router.get("/api/admin/contact-messages")
def admin_list_contact_messages(status: str = "all",
                                  admin=Depends(check_admin),
                                  db=Depends(get_db)):
    """Admin inbox — filterable by status. Same ordering rule as demo
    requests: `new` on top (needs action), then newest first. Returns
    the full reply thread parsed from `replies_json` alongside each row
    so the panel can render conversation history without a second call."""
    where = ""
    params = {}
    if status and status != "all":
        where = "WHERE status = :st"
        params["st"] = status
    rows = db.execute(text(f"""
        SELECT id, name, email, message, status, admin_notes,
               source_ip, created_at, responded_at, replies_json
        FROM contact_messages
        {where}
        ORDER BY
          CASE status WHEN 'new' THEN 0 ELSE 1 END,
          created_at DESC
        LIMIT 200
    """), params).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["replies"] = json.loads(d.pop("replies_json") or "[]") or []
            if not isinstance(d["replies"], list):
                d["replies"] = []
        except Exception:
            d["replies"] = []
        out.append(d)
    return {"messages": out}


def _build_thread_html_block(row: dict, current_reply_actor: str = "admin",
                               current_reply_body: str | None = None) -> str:
    """Bottom-of-email thread block that renders EVERY message in the
    conversation (original note + all prior replies), color-coded by
    actor, most-recent first. Included in both the admin→prospect reply
    email and the prospect→admin notification email so each side always
    has full context in one place — no scrolling through prior emails.

    Callers pass the row *before* the current send's DB write happens
    (so `replies_json` doesn't include the message being sent), and we
    inject `current_reply_body` at the top as the "just-now" turn.
    """
    import html as _h
    try:
        replies = json.loads(row.get("replies_json") or "[]") or []
        if not isinstance(replies, list):
            replies = []
    except Exception:
        replies = []

    # Build combined timeline: original + prior replies + the current-send
    # entry (if provided). Actor labels drive the display side.
    turns = []
    if current_reply_body:
        # Stamp the just-sent turn with the actual send time (converted
        # to Pacific + `PT` by `_fmt_ts_human`) so it's consistent with
        # every other entry in the thread. Prior versions read "Just now"
        # which looked out-of-place next to timestamped entries — and
        # became misleading once the email had been sitting in the
        # inbox for hours.
        from datetime import datetime as _dt_now
        turns.append({
            "actor": current_reply_actor,
            "message": current_reply_body,
            "label": _fmt_ts_human(_dt_now.utcnow().isoformat(timespec="seconds")),
        })
    # Prior replies newest-first
    for rep in reversed(replies):
        actor = (rep.get("actor")
                 or ("admin" if rep.get("actor_user_id") is not None else "prospect")).lower()
        turns.append({
            "actor": actor,
            "message": rep.get("message") or "",
            "label": _fmt_ts_human(rep.get("ts") or ""),
        })
    # Original note last (it's the OLDEST message, at the bottom)
    turns.append({
        "actor": "prospect",
        "message": row.get("message") or "",
        "label": "Original note · " + _fmt_ts_human(row.get("created_at") or ""),
        "is_original": True,
    })

    parts = []
    for t in turns:
        is_admin = t["actor"] == "admin"
        bg    = "#eef2ff" if is_admin else "#f0f9ff"
        color = "#6d28d9" if is_admin else "#0369a1"
        label_prefix = "GigsFill Team" if is_admin else "Sender"
        parts.append(
            f'<div style="padding:12px 14px;background:{bg};border-left:3px solid {color};'
            f'border-radius:0 4px 4px 0;margin:0 0 8px;font-size:13px;color:#374151;">'
              f'<div style="font-size:10px;text-transform:uppercase;letter-spacing:0.08em;'
              f'color:{color};font-weight:700;margin-bottom:4px;">'
                f'{label_prefix} · {_h.escape(t.get("label",""))}'
              '</div>'
              f'<div style="white-space:pre-wrap;line-height:1.5;">{_h.escape(t["message"])}</div>'
            '</div>'
        )
    if not parts:
        return ""
    return (
        '<div style="margin-top:22px;padding-top:14px;border-top:1px solid #e5e7eb;">'
          '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;'
          'color:#9ca3af;margin-bottom:10px;font-weight:600;">Full conversation</div>'
          + "".join(parts) +
        '</div>'
    )


def _build_thread_text_block(row: dict, current_reply_actor: str = "admin",
                               current_reply_body: str | None = None) -> str:
    """Plain-text version of the thread block for the text/plain MIME
    part. Same ordering: newest first."""
    try:
        replies = json.loads(row.get("replies_json") or "[]") or []
        if not isinstance(replies, list):
            replies = []
    except Exception:
        replies = []
    lines = []
    if current_reply_body:
        # Real timestamp instead of "just now" — see HTML block for rationale.
        from datetime import datetime as _dt_now
        now_ts = _fmt_ts_human(_dt_now.utcnow().isoformat(timespec="seconds"))
        label = "GigsFill Team" if current_reply_actor == "admin" else "Sender"
        lines.append(f"--- {label} ({now_ts}) ---")
        lines.extend(current_reply_body.splitlines())
        lines.append("")
    for rep in reversed(replies):
        actor = (rep.get("actor")
                 or ("admin" if rep.get("actor_user_id") is not None else "prospect")).lower()
        label = "GigsFill Team" if actor == "admin" else "Sender"
        ts = _fmt_ts_human(rep.get("ts") or "")
        lines.append(f"--- {label} ({ts}) ---")
        lines.extend((rep.get("message") or "").splitlines())
        lines.append("")
    orig_ts = _fmt_ts_human(row.get("created_at") or "")
    lines.append(f"--- Original note ({orig_ts}) ---")
    lines.extend((row.get("message") or "").splitlines())
    return "\n".join(lines)


def _send_reply_email(smtp, row: dict, to_email: str, to_name: str,
                        reply_body: str, admin_reply_to: str,
                        reply_url: str = "") -> bool:
    """Send admin's reply to the contact-form sender. FROM is the
    platform's configured sender (so it comes from `booked@gigsfill.com`
    or whatever admin has set) with `Reply-To` set to the admin's own
    address so a subsequent reply from the prospect lands in admin's
    inbox. Original message is quoted at the bottom in the classic
    "> " reply-quote style so both sides have context.

    When `reply_url` is provided, embeds a branded **"Continue on
    GigsFill"** button pointing at a signed public reply page — the
    prospect's response there lands directly in the message thread
    on the admin panel, keeping the whole conversation logged in-app
    (mirrors venue↔artist messaging behavior)."""
    if not smtp or not smtp.get("username") or not smtp.get("password"):
        return False
    try:
        import html as _h
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.utils import formataddr
        import smtplib

        subject = "Re: Your note to GigsFill"
        # Full running thread — original + every prior reply — appended
        # to each outbound email so both sides always have complete
        # context without hunting through prior messages. Passed the
        # PRE-DB-write row here so the just-sent reply doesn't already
        # live in row['replies_json']; the helper injects it at the top.
        thread_text = _build_thread_text_block(row, "admin", reply_body)
        thread_html = _build_thread_html_block(row, "admin", reply_body)
        text_body = f"""{reply_body}

—
The GigsFill Team

{('Continue the conversation: ' + reply_url) if reply_url else 'Reply to this email and it will reach us directly.'}


---- FULL CONVERSATION ----
{thread_text}
"""
        # Branded button — only rendered when reply_url is present.
        button_block = ""
        if reply_url:
            button_block = f"""
  <div style="margin:22px 0 8px;text-align:center;">
    <a href="{reply_url}"
       style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;border-radius:6px;text-decoration:none;font-size:15px;font-weight:600;">
      💬 Continue conversation
    </a>
  </div>
  <p style="margin:12px 0 0;font-size:12px;color:#6b7280;text-align:center;line-height:1.5;">
    Opens a page on gigsfill.com — your reply lands directly with our team, keeping the whole thread in one place. Or just hit Reply — either works.
  </p>"""

        html_body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden;">
<tr><td style="padding:22px 30px 16px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;">
  <h1 style="margin:0;font-size:20px;font-weight:700;">Re: Your note to GigsFill</h1>
</td></tr>
<tr><td style="padding:24px 30px;color:#0f172a;font-size:15px;line-height:1.6;">
  <p style="margin:0 0 12px;">Hi {_h.escape(to_name or 'there')},</p>
  <div style="white-space:pre-wrap;">{_h.escape(reply_body)}</div>
  <p style="margin:22px 0 0;color:#6b7280;font-size:13px;">— The GigsFill Team</p>
  {button_block}
  {thread_html}
</td></tr>
<tr><td style="padding:14px 30px;background:#f9fafb;border-top:1px solid #e5e7eb;text-align:center;font-size:12px;color:#94a3b8;">
  GigsFill — live music, booked right.
</td></tr>
</table></td></tr></table></body></html>"""

        mime = MIMEMultipart("alternative")
        mime["Subject"] = subject
        _fn = smtp.get("from_name") or "GigsFill"
        _fe = smtp.get("from_email") or smtp.get("username") or "noreply@gigsfill.com"
        mime["From"] = formataddr((_fn, _fe))
        mime["To"] = to_email
        # Reply-To → admin so the prospect's next reply lands there
        # directly, not in the platform noreply box.
        if admin_reply_to:
            mime["Reply-To"] = admin_reply_to
        mime.attach(MIMEText(text_body, "plain"))
        mime.attach(MIMEText(html_body, "html"))

        server_host = smtp.get("server") or "smtp.gmail.com"
        port = int(smtp.get("port") or 587)
        user = smtp.get("username") or ""
        pw = smtp.get("password") or ""
        if port == 465:
            with smtplib.SMTP_SSL(server_host, port, timeout=15) as srv:
                srv.login(user, pw); srv.send_message(mime)
        elif port in (587, 2587):
            with smtplib.SMTP(server_host, port, timeout=15) as srv:
                srv.ehlo(); srv.starttls(); srv.ehlo()
                srv.login(user, pw); srv.send_message(mime)
        else:
            with smtplib.SMTP(server_host, port, timeout=15) as srv:
                srv.ehlo()
                try: srv.starttls(); srv.ehlo()
                except Exception: pass
                srv.login(user, pw); srv.send_message(mime)
        return True
    except Exception as e:
        logger.error(f"contact reply send failed to {to_email}: {e}", exc_info=True)
        return False


@router.post("/api/admin/contact-messages/{msg_id}/reply")
def admin_reply_contact_message(msg_id: int, data: dict,
                                  admin=Depends(check_admin),
                                  db=Depends(get_db)):
    """Send admin's reply through platform SMTP + append to the row's
    reply thread + auto-transition status to `replied`. Prospect gets a
    branded email with the reply body + a quote of their original note;
    everything is logged in `replies_json` so the modal thread shows
    the full history on subsequent opens."""
    body_text = (data.get("message") or "").strip()[:8000]
    if not body_text or len(body_text) < 3:
        raise HTTPException(400, "Reply body is empty.")

    row = db.execute(
        text("SELECT id, name, email, message, status, replies_json FROM contact_messages WHERE id = :id"),
        {"id": msg_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Message not found")
    row = dict(row)

    # Append this reply to the JSON thread history. Actor is the admin
    # user id so a future multi-admin setup can show who replied.
    try:
        thread = json.loads(row.get("replies_json") or "[]") or []
        if not isinstance(thread, list):
            thread = []
    except Exception:
        thread = []
    entry = {
        "ts": datetime.utcnow().isoformat(timespec="seconds"),
        "message": body_text,
        "actor": "admin",
        "actor_user_id": getattr(admin, "id", None) if admin else None,
    }
    thread.append(entry)

    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    db.execute(text("""UPDATE contact_messages
                        SET replies_json = :thread,
                            status = 'replied',
                            responded_at = COALESCE(responded_at, :ra)
                       WHERE id = :id"""),
                {"thread": json.dumps(thread), "ra": now_iso, "id": msg_id})
    db.commit()

    # Send the email + mint a fresh reply-URL each time so the
    # prospect always has a valid link back into the thread.
    try:
        smtp = _get_smtp()
        admin_email = _get_admin_email(db)
        # Resolve site URL for the reply-page link.
        try:
            site_row = db.execute(text(
                "SELECT setting_value FROM platform_settings WHERE setting_key='site_url'"
            )).scalar()
            site_url = str(site_row).rstrip("/") if site_row else "https://gigsfill.com"
        except Exception:
            site_url = "https://gigsfill.com"
        reply_url = f"{site_url}/contact/reply/{_sign_reply_token(msg_id)}"
        # Pass the PRE-DB-write row snapshot so its `replies_json` still
        # holds only the prior turns; the just-sent reply is threaded
        # in by `_build_thread_*_block` via the `current_reply_body`
        # arg. This way we don't render the current reply twice.
        ok = _send_reply_email(smtp, row, row["email"], row.get("name") or "",
                                 body_text, admin_email, reply_url)
    except Exception as e:
        logger.error(f"contact reply send loop failed for #{msg_id}: {e}", exc_info=True)
        ok = False

    try:
        log_admin_action(db, admin, "contact_message_reply",
                          target_table="contact_messages", target_id=msg_id,
                          metadata={"reply_len": len(body_text),
                                    "email_sent": ok,
                                    "prospect_email": row["email"]})
    except Exception:
        pass
    logger.info(f"[CONTACT] reply sent #{msg_id} to {row['email']} — ok={ok}")
    return {"ok": True, "email_sent": ok, "reply_count": len(thread)}


@router.put("/api/admin/contact-messages/{msg_id}")
def admin_update_contact_message(msg_id: int, data: dict,
                                   admin=Depends(check_admin),
                                   db=Depends(get_db)):
    """Update status (`new` / `replied` / `archived`) and/or admin_notes.
    Both fields optional — only sends the ones present in the payload,
    so a partial update doesn't clobber the other."""
    row = db.execute(
        text("SELECT id, status FROM contact_messages WHERE id = :id"),
        {"id": msg_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Message not found")
    row = dict(row)

    sets = []
    params = {"id": msg_id}

    if "status" in data:
        new_status = (data.get("status") or "").strip().lower()
        if new_status not in ("new", "replied", "archived"):
            raise HTTPException(400, "status must be 'new', 'replied', or 'archived'.")
        sets.append("status = :status")
        params["status"] = new_status
        # Stamp responded_at when moving away from `new`.
        if new_status != "new" and row["status"] == "new":
            sets.append("responded_at = :ra")
            params["ra"] = datetime.utcnow().isoformat(timespec="seconds")

    if "admin_notes" in data:
        notes = data.get("admin_notes")
        notes = (str(notes) if notes is not None else "").strip()[:4000]
        sets.append("admin_notes = :notes")
        params["notes"] = notes or None

    if not sets:
        return {"ok": True, "unchanged": True}

    db.execute(text(f"UPDATE contact_messages SET {', '.join(sets)} WHERE id = :id"),
               params)
    db.commit()

    try:
        log_admin_action(db, admin, "contact_message_update",
                          target_table="contact_messages", target_id=msg_id,
                          metadata={"prior_status": row.get("status"),
                                    "updates": list(data.keys())})
    except Exception:
        pass
    return {"ok": True}


@router.delete("/api/admin/contact-messages/{msg_id}")
def admin_delete_contact_message(msg_id: int, admin=Depends(check_admin),
                                    db=Depends(get_db)):
    """Hard delete — for spam / duplicates. No email fires; use
    'archived' status if you want to keep the row for reference."""
    row = db.execute(
        text("SELECT id, name, email, status FROM contact_messages WHERE id = :id"),
        {"id": msg_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Message not found")
    row = dict(row)
    db.execute(text("DELETE FROM contact_messages WHERE id = :id"), {"id": msg_id})
    db.commit()

    try:
        log_admin_action(db, admin, "contact_message_delete",
                          target_table="contact_messages", target_id=msg_id,
                          metadata={"prior_status": row.get("status"),
                                    "email": row.get("email"),
                                    "name": row.get("name")})
    except Exception:
        pass
    return {"ok": True}
