"""Zoho Mail HTTPS API transport.

DigitalOcean blocks outbound SMTP (25/465/587) from this droplet to every
destination — Zoho, Gmail and Bluehost all time out identically, and the
local firewall is clean, so the block is at DO's edge. No SMTP port or
provider gets around it. Zoho's Mail API runs over HTTPS/443, which is
not blocked, so it is the transport we actually use in production.

`email_service._smtp_send` routes here whenever this module is configured
and falls back to real SMTP otherwise, so local/dev environments that can
reach an SMTP server keep working unchanged.

Configuration (all in `.env`):
    ZOHO_CLIENT_ID          Self Client id from the Zoho API console
    ZOHO_CLIENT_SECRET      Self Client secret
    ZOHO_REFRESH_TOKEN      long-lived, obtained once by exchanging a grant code
    ZOHO_ACCOUNT_REGION     data-center suffix: com | eu | in | com.au (default com)
    ZOHO_FROM_ADDRESS       optional; overrides the message's own From

The OAuth grant belongs to whichever Zoho user authorized the Self Client,
and Zoho rejects a `fromAddress` that account can't send as. If the app
hands us a From the account doesn't own, we rewrite it to the authorized
address and preserve the original as Reply-To, so replies still reach the
intended mailbox instead of the send failing outright.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("gigsfill.zoho_mail")

# Refresh a little early so a token doesn't expire mid-send.
_TOKEN_SAFETY_MARGIN_SEC = 120
_HTTP_TIMEOUT_SEC = 30


class ZohoMailTransport:
    def __init__(self):
        self._lock = threading.Lock()
        self._access_token = None
        self._access_token_expires_at = 0.0
        self._account_id = None
        self._allowed_from = set()
        self._primary_address = None

    # ── configuration ────────────────────────────────────────────────

    @property
    def region(self):
        return os.getenv("ZOHO_ACCOUNT_REGION", "com").strip() or "com"

    def is_configured(self):
        return all(
            os.getenv(k)
            for k in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")
        )

    # ── auth ─────────────────────────────────────────────────────────

    def _fetch_access_token(self):
        """Exchange the refresh token for a short-lived access token.

        Caller must hold `self._lock`.
        """
        payload = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": os.getenv("ZOHO_CLIENT_ID"),
            "client_secret": os.getenv("ZOHO_CLIENT_SECRET"),
            "refresh_token": os.getenv("ZOHO_REFRESH_TOKEN"),
        }).encode()
        req = urllib.request.Request(
            f"https://accounts.zoho.{self.region}/oauth/v2/token", data=payload
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode())
        token = body.get("access_token")
        if not token:
            raise RuntimeError(f"Zoho token refresh returned no access_token: {body.get('error')}")
        self._access_token = token
        self._access_token_expires_at = (
            time.time() + int(body.get("expires_in", 3600)) - _TOKEN_SAFETY_MARGIN_SEC
        )
        return token

    def _token(self):
        with self._lock:
            if self._access_token and time.time() < self._access_token_expires_at:
                return self._access_token
            return self._fetch_access_token()

    def _api(self, path, method="GET", body=None, _retry_on_401=True):
        url = f"https://mail.zoho.{self.region}/api{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Authorization": f"Zoho-oauthtoken {self._token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # A cached token can be revoked server-side before its nominal
            # expiry; drop it and try once with a fresh one.
            if e.code == 401 and _retry_on_401:
                with self._lock:
                    self._access_token = None
                    self._access_token_expires_at = 0.0
                return self._api(path, method, body, _retry_on_401=False)
            raise RuntimeError(f"Zoho API {method} {path} -> {e.code}: {e.read().decode()[:300]}") from None

    # ── account discovery ────────────────────────────────────────────

    def _account(self):
        """Resolve and cache the sending account id plus the addresses it
        is permitted to send as."""
        if self._account_id:
            return self._account_id
        data = (self._api("/accounts") or {}).get("data") or []
        if not data:
            raise RuntimeError("Zoho returned no mail accounts for this token")
        acct = data[0]
        self._account_id = acct.get("accountId")
        self._primary_address = acct.get("primaryEmailAddress")
        allowed = {self._primary_address} if self._primary_address else set()
        for entry in (acct.get("emailAddress") or []):
            if entry.get("mailId"):
                allowed.add(entry["mailId"])
        self._allowed_from = {a.lower() for a in allowed if a}
        logger.info(
            "[ZOHO] account=%s primary=%s sends_as=%s",
            self._account_id, self._primary_address, sorted(self._allowed_from),
        )
        return self._account_id

    # ── sending ──────────────────────────────────────────────────────

    @staticmethod
    def _body(msg):
        """Pull the body out of a MIME message.

        Returns `(content, is_html)`. Most email here is a multipart whose
        payload is a single `MIMEText(body, 'html')`, but SMS goes out as
        bare `MIMEText(text, 'plain')` through carrier email-to-SMS
        gateways — mislabelling that as HTML risks carriers rendering or
        stripping it oddly, so the format is reported honestly.
        """
        html = plain = None
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                try:
                    content = part.get_payload(decode=True)
                    content = content.decode(part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    content = str(part.get_payload())
                if part.get_content_type() == "text/html" and html is None:
                    html = content
                elif part.get_content_type() == "text/plain" and plain is None:
                    plain = content
        else:
            try:
                raw = msg.get_payload(decode=True)
                content = raw.decode(msg.get_content_charset() or "utf-8", "replace") if raw else ""
            except Exception:
                content = str(msg.get_payload())
            if msg.get_content_type() == "text/plain":
                plain = content
            else:
                html = content
        if html is not None:
            return html, True
        return (plain or ""), False

    @staticmethod
    def _addr_list(value):
        if not value:
            return []
        return [a.strip() for a in str(value).split(",") if a.strip()]

    def send_message(self, msg):
        """Send a `email.message.Message` through the Zoho Mail API.

        Returns True on success; raises on failure so callers keep their
        existing try/except behaviour around SMTP errors.
        """
        account_id = self._account()

        to_addrs = self._addr_list(msg.get("To"))
        if not to_addrs:
            raise RuntimeError("Zoho send: message has no To address")

        configured_from = (os.getenv("ZOHO_FROM_ADDRESS") or "").strip()
        msg_from = (msg.get("From") or "").strip()
        from_addr = configured_from or msg_from or self._primary_address

        # Zoho rejects a From the authorized account can't send as. Rather
        # than fail the send, fall back to the authorized address.
        reply_to = (msg.get("Reply-To") or "").strip()
        if self._allowed_from and from_addr.lower() not in self._allowed_from:
            logger.warning(
                "[ZOHO] From %r not sendable by this account; sending as %r instead. "
                "To send as %r, authorize a Self Client as that user.",
                from_addr, self._primary_address, from_addr,
            )
            from_addr = self._primary_address

        # Zoho also rejects an *unverified* replyTo with a 500 and
        # "You need to verify the ReplyTo address". Only pass one through
        # when the account already owns it, otherwise drop it — a missing
        # Reply-To is a cosmetic loss, a failed send is an outage.
        if reply_to and self._allowed_from and reply_to.lower() not in self._allowed_from:
            logger.warning(
                "[ZOHO] dropping unverified Reply-To %r (verify it in Zoho Mail to keep it)",
                reply_to,
            )
            reply_to = ""

        content, is_html = self._body(msg)
        payload = {
            "fromAddress": from_addr,
            "toAddress": ",".join(to_addrs),
            "subject": msg.get("Subject") or "",
            "content": content,
            "mailFormat": "html" if is_html else "plaintext",
        }
        cc = self._addr_list(msg.get("Cc"))
        if cc:
            payload["ccAddress"] = ",".join(cc)
        bcc = self._addr_list(msg.get("Bcc"))
        if bcc:
            payload["bccAddress"] = ",".join(bcc)
        if reply_to:
            payload["replyTo"] = reply_to

        result = self._api(f"/accounts/{account_id}/messages", method="POST", body=payload)
        # Zoho can return HTTP 200 with a failure code in the body.
        status = (result or {}).get("status") or {}
        code = status.get("code")
        if code is not None and str(code) != "200":
            raise RuntimeError(
                f"Zoho send failed: {status} {(result or {}).get('data')}"
            )
        return True


# Module-level singleton so the access token and account lookup are shared
# across every caller in the process.
transport = ZohoMailTransport()


def is_configured():
    return transport.is_configured()


def send_message(msg):
    return transport.send_message(msg)
