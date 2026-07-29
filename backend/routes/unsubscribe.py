"""One-click unsubscribe (RFC 8058).

Jul 2026 audit fix (E-H2). Gmail/Yahoo bulk-sender enforcement (Feb 2024)
requires transactional / bulk email to carry:

    List-Unsubscribe: <https://gigsfill.com/api/unsubscribe/{token}>
    List-Unsubscribe-Post: List-Unsubscribe=One-Click

Without both, high-volume senders get folded to Promotions or Spam.

Design:
  - Token is an HMAC-signed payload `{user_id, notification_type}`
    (URLSafeTimedSerializer, salt="email-unsub", 90-day TTL).
  - Signature is verified server-side — no DB lookup needed. Token
    can't be forged without SECRET_KEY.
  - POST `/api/unsubscribe/{token}` flips the matching
    email_preferences row to disabled, returns 200.
  - GET  `/api/unsubscribe/{token}` renders a plain confirmation page
    for humans who click the visible footer link (some clients don't
    fire the one-click POST).
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text
import logging
import os

from backend.db import get_db_connection

logger = logging.getLogger("gigsfill.unsubscribe")

router = APIRouter()

# Same SECRET_KEY sourcing rule as auth.py — production hard-fails without it.
_SECRET_KEY = os.environ.get("SECRET_KEY") or ""
if not _SECRET_KEY:
    try:
        from backend.routes.auth import _SECRET_KEY as _auth_key
        _SECRET_KEY = _auth_key
    except Exception:
        pass

_unsub_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="email-unsub")
_UNSUB_MAX_AGE = 60 * 60 * 24 * 90  # 90 days — links in older emails stay valid


def sign_unsubscribe_token(user_id: int, notification_type: str) -> str:
    """Mint an unsubscribe token for a specific (user, notification_type)
    pair. Callable from email_service so the header + footer link are
    generated at send time."""
    return _unsub_serializer.dumps({
        "uid": int(user_id),
        "nt":  str(notification_type or ""),
    })


def _verify_token(token: str) -> dict:
    try:
        return _unsub_serializer.loads(token, max_age=_UNSUB_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(410, "Unsubscribe link has expired")
    except BadSignature:
        raise HTTPException(400, "Invalid unsubscribe link")


def _flip_email_pref_off(user_id: int, notification_type: str) -> tuple[bool, str]:
    """Set email_preferences.enabled=0 for this (user, type). Idempotent:
    ok=True if we flipped OR if it was already off. Returns (ok, human_msg).
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # user_has_email_enabled treats a missing row as ENABLED. So we
        # UPSERT: if the row exists, flip; if not, insert as disabled.
        row = cur.execute(
            "SELECT enabled FROM email_preferences WHERE user_id = ? AND notification_type = ?",
            (int(user_id), notification_type)
        ).fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO email_preferences (user_id, notification_type, enabled) VALUES (?, ?, 0)",
                (int(user_id), notification_type)
            )
            conn.commit()
            return True, "unsubscribed"
        if row[0] in (0, "0", False):
            return True, "already unsubscribed"
        cur.execute(
            "UPDATE email_preferences SET enabled = 0 WHERE user_id = ? AND notification_type = ?",
            (int(user_id), notification_type)
        )
        conn.commit()
        return True, "unsubscribed"
    finally:
        conn.close()


@router.post("/api/unsubscribe/{token}")
async def unsubscribe_one_click(token: str, request: Request):
    """RFC 8058 one-click unsubscribe endpoint.

    Gmail/Yahoo POST this with `List-Unsubscribe=One-Click` in the body
    when the recipient clicks the header-level unsubscribe. We flip the
    matching email_preferences row and return 200.
    """
    payload = _verify_token(token)
    uid = int(payload.get("uid") or 0)
    nt = str(payload.get("nt") or "").strip()
    if not uid or not nt:
        raise HTTPException(400, "Malformed unsubscribe token")
    ok, msg = _flip_email_pref_off(uid, nt)
    logger.info(f"[UNSUB] one-click POST user={uid} type={nt} → {msg}")
    return JSONResponse({"ok": ok, "status": msg, "notification_type": nt})


@router.get("/api/unsubscribe/{token}")
def unsubscribe_visible(token: str):
    """Human-clickable footer link. Flips the pref and renders a
    minimal confirmation page. Same signature as the POST — one link
    handles both one-click bulk-header and body-footer clicks."""
    payload = _verify_token(token)
    uid = int(payload.get("uid") or 0)
    nt = str(payload.get("nt") or "").strip()
    if not uid or not nt:
        raise HTTPException(400, "Malformed unsubscribe token")
    ok, msg = _flip_email_pref_off(uid, nt)
    logger.info(f"[UNSUB] visible GET user={uid} type={nt} → {msg}")

    import html as _html
    _pretty_type = _html.escape(nt.replace("_", " ").title())
    _msg = "You've been unsubscribed." if msg == "unsubscribed" else "You were already unsubscribed."
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Unsubscribed — GigsFill</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:520px;margin:60px auto;padding:32px;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);text-align:center;">
  <div style="font-size:56px;line-height:1;margin-bottom:12px;">✅</div>
  <h1 style="font-size:22px;font-weight:700;color:#0f172a;margin:0 0 12px;">{_msg}</h1>
  <p style="font-size:14px;color:#64748b;line-height:1.6;margin:0 0 20px;">
    You will no longer receive <strong>{_pretty_type}</strong> emails from GigsFill.
  </p>
  <p style="font-size:13px;color:#94a3b8;line-height:1.6;margin:0 0 24px;">
    Want to change other preferences?
    <a href="https://gigsfill.com/app/user-profile.html?tab=email" style="color:#2563eb;text-decoration:none;">Manage all email preferences</a>
  </p>
  <hr style="border:0;border-top:1px solid #e5e7eb;margin:24px 0;">
  <p style="font-size:12px;color:#94a3b8;margin:0;">GigsFill — book gigs, fill dates.</p>
</div>
</body></html>"""
    return HTMLResponse(html)
