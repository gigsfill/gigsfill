"""Zoho Mail HTTPS API transport.

DigitalOcean blocks outbound SMTP (25/465/587) from this droplet to every
destination — Zoho, Gmail and Bluehost all time out identically, and the
local firewall is clean, so the block is at DO's edge. No SMTP port or
provider gets around it. Zoho's Mail API runs over HTTPS/443, which is
not blocked, so it is the transport we actually use in production.

`email_service._smtp_send` routes here whenever this module is configured
and falls back to real SMTP otherwise, so local/dev environments that can
reach an SMTP server keep working unchanged.

MULTIPLE SENDERS
----------------
GigsFill sends as more than one identity — `booking@gigsfill.com` for
platform/transactional mail and `support@gigsfill.com` for support — and
in Zoho those are separate user accounts, not aliases. A Zoho Self Client
is authorized by whichever user creates it and can only send as that
user, so each sending identity needs its own credential set. Zoho rejects
a `fromAddress` the authorizing account doesn't own, so without this the
transport would have to rewrite every From to a single address.

Each account is configured as a prefixed triple in `.env`:

    ZOHO_BOOKING_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN
    ZOHO_SUPPORT_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN

plus an optional unprefixed set (`ZOHO_CLIENT_ID`, ...) used as the
fallback for any From that matches none of the others. Shared settings:

    ZOHO_ACCOUNT_REGION     data-center suffix: com | eu | in | com.au (default com)
    ZOHO_FROM_ADDRESS       optional; overrides the message's own From

On send, the From address is matched against the addresses each account
reports it can send as (discovered once per account via /accounts, then
cached). Adding another identity later means adding another prefixed
triple — no code change.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("gigsfill.zoho_mail")

# Refresh a little early so a token doesn't expire mid-send.
_TOKEN_SAFETY_MARGIN_SEC = 120
_HTTP_TIMEOUT_SEC = 30


def _region():
    return os.getenv("ZOHO_ACCOUNT_REGION", "com").strip() or "com"


class _ZohoAccount:
    """One authorized Zoho mailbox: its OAuth creds, cached access token,
    account id, and the set of addresses it may send as."""

    def __init__(self, name, client_id, client_secret, refresh_token):
        self.name = name
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._lock = threading.Lock()
        self._access_token = None
        self._expires_at = 0.0
        self.account_id = None
        self.primary_address = None
        self.allowed_from = set()
        self._resolved = False

    # ── auth ─────────────────────────────────────────────────────────

    def _refresh(self):
        """Exchange the refresh token for an access token.

        Caller must hold `self._lock`.
        """
        payload = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
        }).encode()
        req = urllib.request.Request(
            f"https://accounts.zoho.{_region()}/oauth/v2/token", data=payload
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode())
        token = body.get("access_token")
        if not token:
            raise RuntimeError(
                f"Zoho token refresh for {self.name} returned no access_token: {body.get('error')}"
            )
        self._access_token = token
        self._expires_at = time.time() + int(body.get("expires_in", 3600)) - _TOKEN_SAFETY_MARGIN_SEC
        return token

    def _token(self):
        with self._lock:
            if self._access_token and time.time() < self._expires_at:
                return self._access_token
            return self._refresh()

    def api(self, path, method="GET", body=None, _retry_on_401=True):
        url = f"https://mail.zoho.{_region()}/api{path}"
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
                    self._expires_at = 0.0
                return self.api(path, method, body, _retry_on_401=False)
            raise RuntimeError(
                f"Zoho API {method} {path} [{self.name}] -> {e.code}: {e.read().decode()[:300]}"
            ) from None

    # ── discovery ────────────────────────────────────────────────────

    def resolve(self):
        """Look up the account id and sendable addresses. Cached; a failure
        is remembered as 'resolved with no addresses' so one broken account
        doesn't retry on every single send."""
        if self._resolved:
            return
        self._resolved = True
        try:
            data = (self.api("/accounts") or {}).get("data") or []
        except Exception as e:
            logger.warning("[ZOHO] account lookup failed for %s: %s", self.name, e)
            return
        if not data:
            logger.warning("[ZOHO] no mail accounts returned for %s", self.name)
            return
        acct = data[0]
        self.account_id = acct.get("accountId")
        self.primary_address = acct.get("primaryEmailAddress")
        allowed = {self.primary_address} if self.primary_address else set()
        for entry in (acct.get("emailAddress") or []):
            if entry.get("mailId"):
                allowed.add(entry["mailId"])
        self.allowed_from = {a.lower() for a in allowed if a}
        logger.info(
            "[ZOHO] %s -> account=%s primary=%s sends_as=%s",
            self.name, self.account_id, self.primary_address, sorted(self.allowed_from),
        )

    def can_send_as(self, address):
        self.resolve()
        return bool(address) and address.lower() in self.allowed_from

    @property
    def usable(self):
        self.resolve()
        return bool(self.account_id)


class ZohoMailTransport:
    def __init__(self):
        self._lock = threading.Lock()
        self._accounts = None

    # ── configuration ────────────────────────────────────────────────

    @staticmethod
    def _discover_from_env():
        """Build the account list from environment variables.

        Finds every `ZOHO_<NAME>_CLIENT_ID` and pairs it with the matching
        secret and refresh token, then appends the unprefixed set last so
        it acts as the fallback.
        """
        found = []
        for key in os.environ:
            m = re.fullmatch(r"ZOHO_([A-Z0-9]+)_CLIENT_ID", key)
            if not m:
                continue
            name = m.group(1)
            # ACCOUNT_REGION / API_DOMAIN etc. are settings, not accounts.
            cid = os.getenv(f"ZOHO_{name}_CLIENT_ID")
            sec = os.getenv(f"ZOHO_{name}_CLIENT_SECRET")
            ref = os.getenv(f"ZOHO_{name}_REFRESH_TOKEN")
            if cid and sec and ref:
                found.append(_ZohoAccount(name.lower(), cid, sec, ref))
        found.sort(key=lambda a: a.name)

        cid, sec, ref = (os.getenv("ZOHO_CLIENT_ID"), os.getenv("ZOHO_CLIENT_SECRET"),
                         os.getenv("ZOHO_REFRESH_TOKEN"))
        if cid and sec and ref:
            found.append(_ZohoAccount("default", cid, sec, ref))
        return found

    def accounts(self):
        with self._lock:
            if self._accounts is None:
                self._accounts = self._discover_from_env()
                logger.info(
                    "[ZOHO] configured accounts: %s",
                    [a.name for a in self._accounts] or "none",
                )
            return self._accounts

    def is_configured(self):
        return bool(self.accounts())

    def reset(self):
        """Drop cached accounts and tokens — used by tests and after a
        credential change without a restart."""
        with self._lock:
            self._accounts = None

    # ── routing ──────────────────────────────────────────────────────

    def _pick(self, from_addr):
        """Choose the account that may send as `from_addr`.

        Returns `(account, rewritten_from)`. When nothing matches we fall
        back to the last usable account and report the address it will
        actually send as, so the caller can log the substitution.
        """
        accounts = self.accounts()
        if not accounts:
            raise RuntimeError("Zoho transport has no configured accounts")
        for acct in accounts:
            if acct.can_send_as(from_addr):
                return acct, from_addr
        for acct in accounts:
            if acct.usable:
                return acct, acct.primary_address
        raise RuntimeError("Zoho transport: no usable accounts (all lookups failed)")

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

    @staticmethod
    def _bare(address):
        """Strip a display name: 'GigsFill <a@b.c>' -> 'a@b.c'."""
        if not address:
            return ""
        m = re.search(r"<([^>]+)>", address)
        return (m.group(1) if m else address).strip()

    def send_message(self, msg):
        """Send a `email.message.Message` through the Zoho Mail API.

        Returns True on success; raises on failure so callers keep their
        existing try/except behaviour around SMTP errors.
        """
        to_addrs = self._addr_list(msg.get("To"))
        if not to_addrs:
            raise RuntimeError("Zoho send: message has no To address")

        configured_from = (os.getenv("ZOHO_FROM_ADDRESS") or "").strip()
        wanted_from = self._bare(configured_from or (msg.get("From") or ""))

        acct, from_addr = self._pick(wanted_from)
        if wanted_from and from_addr.lower() != wanted_from.lower():
            logger.warning(
                "[ZOHO] no account can send as %r; sending as %r via %s. "
                "Authorize a Self Client as %r to send as it directly.",
                wanted_from, from_addr, acct.name, wanted_from,
            )

        # Zoho rejects an *unverified* replyTo with a 500 and "You need to
        # verify the ReplyTo address". Only pass one through when the
        # sending account already owns it — a missing Reply-To is a
        # cosmetic loss, a failed send is an outage.
        reply_to = self._bare(msg.get("Reply-To") or "")
        if reply_to and not acct.can_send_as(reply_to):
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

        result = acct.api(f"/accounts/{acct.account_id}/messages", method="POST", body=payload)
        # Zoho can return HTTP 200 with a failure code in the body.
        status = (result or {}).get("status") or {}
        code = status.get("code")
        if code is not None and str(code) != "200":
            raise RuntimeError(f"Zoho send failed: {status} {(result or {}).get('data')}")
        return True


# Module-level singleton so access tokens and account lookups are shared
# across every caller in the process.
transport = ZohoMailTransport()


def is_configured():
    return transport.is_configured()


def send_message(msg):
    return transport.send_message(msg)


def reset():
    transport.reset()
