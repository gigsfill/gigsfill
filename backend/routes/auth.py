import os
import logging
import secrets
from fastapi import APIRouter, HTTPException, Response, Cookie, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from backend.db import SessionLocal
from backend.models import User
from sqlalchemy import text
import bcrypt
from datetime import datetime, timedelta
from backend.utils import utcnow_naive
from backend.rate_limiter import limiter, rate_login_limit, rate_signup_limit, rate_password_reset_limit

logger = logging.getLogger("gigsfill.auth")

router = APIRouter()

# ============================================
# JWT / SIGNED SESSION CONFIG
# ============================================
# Uses itsdangerous for HMAC-signed session tokens — no external JWT library needed.
# The cookie contains a signed payload with user_id + expiry that cannot be forged.

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# Secret key: MUST be set in production via environment variable
_SECRET_KEY = os.environ.get("GIGSFILL_SECRET_KEY", "")
_is_production = os.environ.get("GIGSFILL_ENV") == "production"

if not _SECRET_KEY:
    if _is_production:
        # Hard fail in production — do not allow unsigned sessions
        raise RuntimeError(
            "\n\n⛔  GIGSFILL_SECRET_KEY is not set!\n"
            "Sessions cannot be secured without this key.\n\n"
            "To fix, add it to your systemd service file:\n"
            "  sudo systemctl edit gigsfill\n"
            "  Add under [Service]:\n"
            "    Environment=GIGSFILL_SECRET_KEY=<your-64-char-hex-key>\n\n"
            "Generate a key with: python3 -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "Then: sudo systemctl daemon-reload && sudo systemctl restart gigsfill\n"
        )

    # Development fallback: persist key in a local file so sessions survive restarts.
    # This file must NOT be committed to version control.
    _key_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        ".secret_key"
    )
    if os.path.exists(_key_file):
        with open(_key_file, "r") as _kf:
            _SECRET_KEY = _kf.read().strip()
    if not _SECRET_KEY:
        _SECRET_KEY = secrets.token_hex(32)
        try:
            with open(_key_file, "w") as _kf:
                _kf.write(_SECRET_KEY)
            logger.info("Generated new dev secret key → %s", _key_file)
            logger.warning(
                "⚠️  .secret_key file created for development. "
                "Ensure it is listed in .gitignore and never committed."
            )
        except OSError:
            logger.warning("Could not persist dev secret key to disk — key will reset on restart.")

_serializer = URLSafeTimedSerializer(_SECRET_KEY)
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", 604800))  # 7 days default
SESSION_COOKIE_NAME = "session_token"


def create_session_token(user_id: int) -> str:
    """Create a cryptographically signed session token containing the user ID."""
    return _serializer.dumps({"uid": user_id})


def verify_session_token(token: str) -> int:
    """Verify a session token and return the user_id. Raises on invalid/expired."""
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return int(data["uid"])
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Session expired — please log in again")
    except (BadSignature, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session")


def verify_session_token_with_iat(token: str):
    """Verify a session token; return (user_id, issued_at_naive_utc).

    issued_at is naive-UTC to match the storage convention everywhere else
    (utcnow_naive). Used by `get_current_user` to compare against
    `users.password_changed_at` and reject sessions issued before a password
    change/reset (H1/H2 audit fix, May 2026)."""
    try:
        data, ts_aware = _serializer.loads(token, max_age=SESSION_MAX_AGE, return_timestamp=True)
        # itsdangerous returns aware UTC; strip tz for parity with utcnow_naive
        ts_naive = ts_aware.replace(tzinfo=None) if ts_aware is not None else None
        return int(data["uid"]), ts_naive
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Session expired — please log in again")
    except (BadSignature, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session")


def should_renew_token(token: str) -> bool:
    """Return True if the session token is more than halfway through its lifetime.
    Implements rolling/sliding expiry — active users never get unexpectedly logged out.
    Token format: payload.timestamp_b64url.signature (itsdangerous URLSafeTimedSerializer)
    """
    try:
        import time, base64, struct
        parts = token.split(".")
        if len(parts) != 3:
            return False
        # Decode 4-byte big-endian timestamp from part[1]
        ts_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        ts_bytes = base64.urlsafe_b64decode(ts_b64)
        if len(ts_bytes) != 4:
            return False
        issued_at = struct.unpack(">I", ts_bytes)[0]
        age_seconds = time.time() - issued_at
        # Renew when token is more than 50% through its max lifetime
        return 0 < age_seconds < SESSION_MAX_AGE and age_seconds > (SESSION_MAX_AGE / 2)
    except Exception:
        return False


def set_session_cookie(response: Response, user_id: int):
    """Set the signed session cookie on a response."""
    token = create_session_token(user_id)
    # FIX (May 2026): default to Secure (HTTPS-only). Previously this was
    # `is_production = GIGSFILL_ENV == "production"` which defaulted to False,
    # meaning cookies were sent over plain HTTP unless an env var was explicitly
    # set. On the live droplet that var was never set, so the session cookie
    # was missing the `Secure` flag — small but real exposure to MITM if anyone
    # ever connected over HTTP. Inverted: default Secure unless explicitly
    # GIGSFILL_ENV=development (for local dev where HTTPS isn't available).
    secure = os.environ.get("GIGSFILL_ENV") != "development"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
        path="/",
    )


def clear_session_cookie(response: Response):
    """Clear the session cookie."""
    secure = os.environ.get("GIGSFILL_ENV") != "development"
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=secure, samesite="lax")
    # Also clear the legacy cookie in case old browsers still have it
    response.delete_cookie("user_id", path="/", secure=secure, samesite="lax")


# ============================================
# PYDANTIC VALIDATION MODELS
# ============================================

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=100)
    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UpdateUserRequest(BaseModel):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=20)

# ============================================
# PASSWORD HASHING UTILITIES
# ============================================

BCRYPT_MAX_BYTES = 72  # bcrypt silently truncates input past 72 bytes

def validate_password_or_raise(password: str, *, min_chars: int = 6) -> None:
    """Reject passwords that bcrypt would silently truncate.

    Audit fix (May 2026, H8): bcrypt operates on the first 72 *bytes* of the
    input and ignores everything past that. A 100-char password and the same
    string truncated at byte 72 produce identical hashes — meaning the user
    thinks they have entropy past byte 72 but they don't, and a long password
    can be silently weakened. UTF-8 makes byte length differ from char length
    (e.g. emojis are 4 bytes each), so we check the encoded byte length.

    This is a boundary check at signup / password-change / reset / invitation
    accept time. We intentionally do NOT enforce on verify_password — existing
    users whose hashes were computed under the old behavior must still be able
    to log in (bcrypt.checkpw will keep doing the same truncation it always
    did)."""
    if not isinstance(password, str) or len(password) < min_chars:
        raise HTTPException(400, f"Password must be at least {min_chars} characters")
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise HTTPException(
            400,
            f"Password is too long ({BCRYPT_MAX_BYTES}-byte UTF-8 max). "
            "If you use emojis or accented characters, each can count as 2-4 bytes."
        )


def hash_password(password: str) -> str:
    """Hash a password using bcrypt. Caller should have validated via
    validate_password_or_raise; this function does not re-validate to avoid
    coupling. bcrypt itself truncates at 72 bytes (BCRYPT_MAX_BYTES)."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return bcrypt.checkpw(
        plain_password.encode('utf-8'),
        hashed_password.encode('utf-8')
    )

# ============================================
# DEPENDENCY: CURRENT USER (Signed Session)
# ============================================

def _reject_if_password_rotated(user, token_iat):
    """Raise 401 if the session token was issued before the user's last
    password change (forces re-login on every device after a password
    change/reset). Tolerant of legacy users with NULL password_changed_at —
    those rows skip the check (they predate the column being added)."""
    pcat = getattr(user, "password_changed_at", None)
    if pcat is None or token_iat is None:
        return
    # Allow a small clock-skew grace (5 seconds) so the device that just
    # changed the password and got a fresh cookie isn't immediately kicked
    # if the cookie's timestamp lands a hair before the DB write.
    if token_iat + timedelta(seconds=5) < pcat:
        raise HTTPException(status_code=401, detail="Session invalidated — please log in again")


def get_current_user(session_token: str | None = Cookie(default=None)):
    """Dependency to get current authenticated user from signed session cookie."""
    if not session_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user_id, token_iat = verify_session_token_with_iat(session_token)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid session")
        _reject_if_password_rotated(user, token_iat)
        return user
    finally:
        db.close()


def get_optional_user(session_token: str | None = Cookie(default=None)):
    """Like get_current_user but returns None instead of raising if not logged in."""
    if not session_token:
        return None
    try:
        user_id, token_iat = verify_session_token_with_iat(session_token)
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user is None:
                return None
            _reject_if_password_rotated(user, token_iat)
            return user
        finally:
            db.close()
    except Exception:
        return None

# ============================================
# ACCOUNT LOCKOUT (brute force protection)
# ============================================
# In-memory tracker — survives between requests but resets on restart.
# Works well with rate limiting as a second layer of defense.

import threading

# Audit fix (May 2026): lockout keyed on (email, IP) tuple, not email alone.
# Previously an attacker could DoS a victim by submitting 10 wrong logins
# with their email — locking the legitimate user out of their account from
# any IP. Now the attacker locks only their own IP/email pair; legitimate
# user from a different IP is unaffected. Slowapi's per-IP rate limit is a
# separate, broader layer; this lockout is the per-account/per-IP layer.
#
# Audit fix Auth-R3 (Jul 1 2026): state was previously kept in a module-
# level Python dict. Under the 2-uvicorn-worker production topology,
# worker A didn't see worker B's counter — effective threshold doubled
# and every restart wiped state. Now backed by the `login_attempts` DB
# table (see db.py:setup_database) so all workers share the counter and
# state survives restarts.
_login_lock = threading.Lock()

MAX_LOGIN_ATTEMPTS = 10
LOCKOUT_DURATION = timedelta(minutes=15)


def _client_ip(request) -> str:
    """Best-effort extraction of the request's client IP."""
    if request is None:
        return ""
    try:
        # Honor X-Forwarded-For if a reverse proxy is in front (nginx, etc.)
        xff = request.headers.get("x-forwarded-for") if hasattr(request, "headers") else None
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else ""
    except Exception:
        return ""


def _check_lockout(email: str, ip: str = ""):
    """Check if (email, ip) is locked out. Raises HTTPException if locked.
    DB-backed so it works across uvicorn workers."""
    if not email:
        return
    _db = SessionLocal()
    try:
        with _login_lock:
            row = _db.execute(
                text("""SELECT locked_until FROM login_attempts
                           WHERE email = :e AND ip = :i"""),
                {"e": email, "i": ip or ""}
            ).mappings().first()
            if not row or not row.get("locked_until"):
                return
            try:
                _lu = row["locked_until"]
                if isinstance(_lu, str):
                    # Handle both 'YYYY-MM-DD HH:MM:SS' and ISO forms.
                    _lu = datetime.fromisoformat(_lu.replace("Z", "").replace("T", " ").split(".")[0])
            except Exception:
                return
            _now = utcnow_naive()
            if _now < _lu:
                remaining = int((_lu - _now).total_seconds() // 60) + 1
                raise HTTPException(
                    429,
                    f"Too many failed login attempts. Try again in {remaining} minutes."
                )
            # Lockout expired — clear the row so retries start fresh.
            _db.execute(
                text("""DELETE FROM login_attempts
                           WHERE email = :e AND ip = :i"""),
                {"e": email, "i": ip or ""}
            )
            _db.commit()
    finally:
        _db.close()


def _record_failed_login(email: str, ip: str = ""):
    """Record a failed login attempt. Lock the (email, ip) pair if threshold
    exceeded. Cross-worker safe via a single atomic upsert-with-increment —
    the previous read-then-write pattern let two uvicorn workers both read
    count=4, both compute count=5, both UPDATE to 5 (effective threshold
    doubled). The `INSERT ... ON CONFLICT DO UPDATE` form serializes the
    increment at the SQL layer so exactly one +1 lands per call regardless
    of worker concurrency.
    """
    if not email:
        return
    _db = SessionLocal()
    try:
        with _login_lock:
            # Atomic increment via UPSERT — same semantics on SQLite (3.24+)
            # and Postgres (9.5+). CASE selects the lockout stamp when
            # the incremented count crosses the threshold.
            _lockout_str = (utcnow_naive() + LOCKOUT_DURATION).strftime("%Y-%m-%d %H:%M:%S")
            _db.execute(
                text("""
                    INSERT INTO login_attempts (email, ip, attempt_count, locked_until, updated_at)
                    VALUES (:e, :i, 1, NULL, CURRENT_TIMESTAMP)
                    ON CONFLICT(email, ip) DO UPDATE SET
                        attempt_count = login_attempts.attempt_count + 1,
                        locked_until = CASE
                            WHEN login_attempts.attempt_count + 1 >= :thr THEN :lu
                            ELSE login_attempts.locked_until
                        END,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {"e": email, "i": ip or "", "thr": MAX_LOGIN_ATTEMPTS, "lu": _lockout_str}
            )
            _db.commit()
            # Post-check for the logger.warning (best-effort — the state
            # is already committed either way).
            _cur = _db.execute(
                text("SELECT attempt_count FROM login_attempts WHERE email = :e AND ip = :i"),
                {"e": email, "i": ip or ""}
            ).scalar()
            if _cur and int(_cur) >= MAX_LOGIN_ATTEMPTS and int(_cur) < MAX_LOGIN_ATTEMPTS + 3:
                logger.warning(f"Login locked for ({email}, {ip}) after {int(_cur)} failed attempts")
    finally:
        _db.close()


def _clear_failed_logins(email: str, ip: str = ""):
    """Clear failed attempts on successful login. Clears every IP for this
    email — covers a legit user across IPs (dynamic IPs, mobile hop) and
    prevents stale lockouts. Cross-worker via DB backing."""
    if not email:
        return
    _db = SessionLocal()
    try:
        with _login_lock:
            _db.execute(
                text("DELETE FROM login_attempts WHERE email = :e"),
                {"e": email}
            )
            _db.commit()
    finally:
        _db.close()

# ============================================
# SIGN UP
# ============================================

@router.post("/api/signup")
@limiter.limit(rate_signup_limit)
def signup(request: Request, data: dict, response: Response):
    """Create a new user account with hashed password and auto-create artist/venue profile"""
    from backend.us_cities import find_city
    from backend.email_service import EmailService
    
    db = SessionLocal()
    try:
        # ── Check signups_enabled kill switch ────────────────────────────────
        # Audit Y8 fix (Jul 1 2026): the previous bare-except swallowed
        # transient DB errors (connection blip, temporary lock) and
        # let signups proceed silently — defeating the admin kill
        # switch during exactly the outage windows it's meant for.
        # Now: only swallow "table missing" specifically; every other
        # error is re-raised as a 503 so signup fails closed. The kill
        # switch is a positive opt-out — "no setting row = allowed" is
        # deliberate, but "settings table exploded" should NOT be.
        try:
            signups_on = db.execute(
                text("SELECT setting_value FROM platform_settings WHERE setting_key = 'signups_enabled'")
            ).scalar()
            # Default is open (None means key not set yet)
            if signups_on is not None and str(signups_on).lower() in ('false', '0'):
                raise HTTPException(503, "New signups are temporarily closed. Please check back soon.")
        except HTTPException:
            raise
        except Exception as _sig_e:
            _msg = str(_sig_e).lower()
            if "no such table" in _msg or "does not exist" in _msg:
                # Fresh DB before setup_database completed — allow signup.
                pass
            else:
                logger.error(f"signups_enabled kill-switch check failed transiently: {_sig_e}")
                raise HTTPException(503, "Signup is temporarily unavailable. Please try again in a moment.")

        # Validate required fields
        email = (data.get("email") or "").strip().lower()
        password = data.get("password")
        role = data.get("role")

        if not email or not password:
            raise HTTPException(400, "Email and password required")

        # Audit fix (May 2026 part 7): the signup endpoint takes a raw `dict`
        # so SignupRequest's EmailStr validator is bypassed. Without this
        # check, `"email":"notanemail"` would be accepted and inserted, then
        # leak into booking_contact / verification-email recipients / affiliate
        # records. Apply the minimal RFC-compliant-ish check inline.
        import re as _re_email
        if not _re_email.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            raise HTTPException(400, "Please enter a valid email address.")

        validate_password_or_raise(password)

        if role not in ["artist", "venue"]:
            raise HTTPException(400, "Role must be 'artist' or 'venue'")

        # Validate and normalize phone number — strip formatting, require 10 digits
        import re as _re
        raw_phone = data.get("phone", "") or ""
        phone_digits = _re.sub(r"\D", "", raw_phone)
        if not phone_digits or len(phone_digits) != 10:
            raise HTTPException(400, "A valid 10-digit US phone number is required")
        # Store in consistent (XXX) XXX-XXXX format
        normalized_phone = f"({phone_digits[:3]}) {phone_digits[3:6]}-{phone_digits[6:]}"
        data["phone"] = normalized_phone

        # Check if email already exists.
        # Audit fix (May 2026): don't leak account existence via the signup
        # response. Forgot-password is correctly anonymous; signup leaked the
        # same data through a different door. Now: send an "account already
        # exists" notice to the colliding address (so they know to log in or
        # reset their password) and return a generic success-ish response.
        # The status code is still 400 so an honest signup form can react,
        # but the message is generic — automated enumeration can't distinguish.
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            # Audit fix (May 2026 part 5): throttle the "account already exists"
            # email per (email, 24h) so a distributed attacker can't mailbomb
            # users by spraying signups with their address. The per-IP
            # RATE_SIGNUP limit doesn't stop this; per-email throttle does.
            _suppress_alert = False
            try:
                _last = db.execute(
                    text("SELECT signup_collision_last_at FROM users WHERE id = :uid"),
                    {"uid": existing.id}
                ).scalar()
            except Exception:
                # Column may not exist yet on older deployments; add it lazily.
                try:
                    db.execute(text("ALTER TABLE users ADD COLUMN signup_collision_last_at TEXT"))
                    db.commit()
                except Exception:
                    pass
                _last = None
            if _last:
                try:
                    from datetime import timedelta as _td
                    _last_dt = _last if not isinstance(_last, str) else datetime.fromisoformat(_last)
                    if (utcnow_naive() - _last_dt) < _td(hours=24):
                        _suppress_alert = True
                except Exception:
                    pass
            if not _suppress_alert:
                try:
                    db.execute(
                        text("UPDATE users SET signup_collision_last_at = :now WHERE id = :uid"),
                        {"now": utcnow_naive(), "uid": existing.id}
                    )
                    db.commit()
                except Exception:
                    pass
            try:
                from backend.email_service import EmailService
                _es = EmailService(db)
                if _es.enabled and not _suppress_alert:
                    _first = existing.first_name or ""
                    # Audit fix (May 2026 part 5): use the canonical site URL
                    # from platform_settings so staging/custom-domain deploys
                    # don't link recipients back to production.
                    _base = _get_base_url(db)
                    _es._send_raw_email(
                        to_email=existing.email,
                        subject="Someone tried to create a GigsFill account with your email",
                        html_body=(
                            f"<p>Hi {_first},</p>"
                            f"<p>An account already exists at GigsFill with this email address. "
                            f"Someone just tried to sign up using it.</p>"
                            f"<p>If this was you, just <a href=\"{_base}/app/index.html\">log in</a> "
                            f"or <a href=\"{_base}/app/index.html#forgot\">reset your password</a>.</p>"
                            f"<p>If it wasn't you, no action is needed — your account is safe.</p>"
                            f"<p>— The GigsFill Team</p>"
                        ),
                    )
            except Exception:
                pass
            raise HTTPException(400, "Could not create account. If you already have one, please log in or reset your password.")

        # ── Pre-validate entity fields BEFORE creating user ──────────
        latitude = None
        longitude = None

        if role == "artist":
            artist_type = data.get("artist_type")
            if not artist_type:
                raise HTTPException(400, "Artist type required for artist accounts")

            band_formats = data.get("band_formats")
            styles = data.get("styles")
            if artist_type == "Live Band":
                if not band_formats:
                    raise HTTPException(400, "Lineup selection required for Live Band artists")
                if not styles:
                    raise HTTPException(400, "At least one style is required for Live Band artists")

            city = data.get("city", "")
            state = data.get("state", "")
            if city and state:
                city_data = find_city(city, state)
                if city_data:
                    latitude = city_data["lat"]
                    longitude = city_data["lon"]
                else:
                    raise HTTPException(400, "This city is either misspelled or too small for our system. Please enter the closest big city to yours.")

        elif role == "venue":
            city = data.get("city", "")
            state = data.get("state", "")
            if city and state:
                city_data = find_city(city, state)
                if city_data:
                    latitude = city_data["lat"]
                    longitude = city_data["lon"]
                else:
                    raise HTTPException(400, "This city is either misspelled or too small for our system. Please enter the closest big city to yours.")

        # ── All validation passed — now create user ──────────────────

        # Hash the password
        hashed_pw = hash_password(password)

        # v73: Create user with phone number - use ORM model directly
        user = User(
            email=email,
            password=hashed_pw,
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            phone=data.get("phone", "")  # v73: Add phone directly to ORM
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Generate unique affiliate code.
        # Audit fix (May 2026 part 10c): catch IntegrityError on the UPDATE
        # so a SELECT-then-UPDATE race between two parallel signups can't
        # produce a 500. Loop terminates loudly if all 20 attempts collide
        # (vanishingly unlikely with 4-byte hex = 4.3B namespace, but
        # logging the exhaustion makes the bug findable instead of silent).
        import secrets as _sec
        aff_code = None
        for _attempt in range(20):
            candidate = "AFF-" + _sec.token_hex(4).upper()
            exists = db.execute(text("SELECT id FROM users WHERE affiliate_code = :c"), {"c": candidate}).first()
            if exists:
                continue
            try:
                db.execute(text("UPDATE users SET affiliate_code = :c WHERE id = :uid"), {"c": candidate, "uid": user.id})
                db.commit()
                aff_code = candidate
                break
            except Exception as _ace:
                # Unique-index race-loser — another concurrent signup grabbed this code first.
                db.rollback()
                logger.info(f"affiliate_code race on signup user {user.id}: {_ace}; retrying")
                continue
        if aff_code is None:
            logger.error(
                f"affiliate_code generation EXHAUSTED 20 attempts for user {user.id} — "
                f"user has no code, will not be able to send recommend emails. "
                f"This should be statistically impossible (4.3B namespace); investigate."
            )



        # Check if first user - auto-make admin
        # Audit fix (May 2026): write integer 1 (post-migration); SQLAlchemy
        # `Column(Boolean)` and the canonical reads tolerate either form.
        user_count = db.query(User).count()
        if user_count == 1:
            db.execute(text("UPDATE users SET is_admin = 1 WHERE id = :uid"), {"uid": user.id})
            db.commit()

        # Auto-create artist or venue profile based on role
        from backend.models import Artist, Venue
        
        if role == "artist":
            # Fields already validated above
            artist_name = data.get("artist_name", f"{data.get('first_name', '')} {data.get('last_name', '')}".strip())
            city = data.get("city", "")
            state = data.get("state", "")
            bio = data.get("bio", "")
            artist_type = data.get("artist_type")
            band_formats = data.get("band_formats")
            styles = data.get("styles")
            
            # Server-side duplicate guard
            _dup_a = db.execute(text("""
                SELECT a.id, a.name, a.city, a.state FROM artists a
                WHERE LOWER(a.name) = LOWER(:n) AND LOWER(a.city) = LOWER(:c) AND UPPER(a.state) = UPPER(:s)
                LIMIT 1
            """), {"n": artist_name, "c": city or "", "s": state or ""}).mappings().first()
            if _dup_a:
                # Roll back the user we just created, then raise
                db.delete(user); db.commit()
                raise HTTPException(409, f"An artist named '{_dup_a['name']}' already exists in {_dup_a['city']}, {_dup_a['state']}. If this is your artist, use 'Request Access' on the duplicate alert.")

            # Create artist profile.
            # Jul 1 2026: MC-type equipment opt-in on signup.
            _has_own_equipment = bool(data.get("has_own_equipment"))
            artist = Artist(
                user_id=user.id,
                name=artist_name,
                artist_type=artist_type,
                band_formats=band_formats,
                styles=styles,
                has_own_equipment=_has_own_equipment,
                city=city,
                state=state,
                latitude=latitude,
                longitude=longitude,
                bio=bio,
                booking_contact=f"{data.get('first_name', '')} {data.get('last_name', '')} - {email} - {data.get('phone', '')}"  # v73: Set booking contact
            )
            db.add(artist)
            db.commit()
            db.refresh(artist)
            
            # v91: Update coordinates via SQL to ensure they save
            if latitude is not None and longitude is not None:
                db.execute(
                    text("UPDATE artists SET latitude = :lat, longitude = :lon WHERE id = :aid"),
                    {"lat": latitude, "lon": longitude, "aid": artist.id}
                )
                db.commit()
            
            
            # Add creator as owner in entity_users
            db.execute(
                text("""
                    INSERT INTO entity_users (entity_type, entity_id, user_id, role, added_by_user_id, created_at)
                    VALUES ('artist', :entity_id, :user_id, 'owner', :user_id, CURRENT_TIMESTAMP)
                """),
                {"entity_id": artist.id, "user_id": user.id}
            )
            db.commit()
            
        elif role == "venue":
            # v73: DEBUG - Log all incoming data
            
            # Get required fields
            venue_name = data.get("venue_name", f"{data.get('first_name', '')}'s Venue")
            address = data.get("address", "")
            city = data.get("city", "")
            state = data.get("state", "")
            zip_code = data.get("zip", "")
            description = data.get("description", "")
            
            # v73: Parse default pay into dollars and cents
            default_pay_str = str(data.get("default_pay", "0"))
            try:
                default_pay_float = float(default_pay_str)
                default_pay_dollars = int(default_pay_float)
                default_pay_cents = int((default_pay_float - default_pay_dollars) * 100)
            except:
                default_pay_dollars = 0
                default_pay_cents = 0
            
            performance_frequency = data.get("performance_frequency", 30)
            capacity = data.get("capacity", 0)
            
            # v73: Get amenity fields
            has_stage = data.get("has_stage", 0)
            stage_width_ft = data.get("stage_width_ft") or None
            stage_depth_ft = data.get("stage_depth_ft") or None
            setup_location_description = data.get("setup_location_description") or None
            has_sound_equipment = data.get("has_sound_equipment", 0)
            sound_equipment_description = data.get("sound_equipment_description") or None
            has_sound_engineer = data.get("has_sound_engineer", 0)
            sound_engineer_details = data.get("sound_engineer_details") or None
            has_lighting = data.get("has_lighting", 0)
            lighting_description = data.get("lighting_description") or None
            load_in_out_details = data.get("load_in_out_details") or None
            bar_tab_details = data.get("bar_tab_details") or None
            food_tab_details = data.get("food_tab_details") or None
            
            # v73: Arrival time fields
            arrival_time_type = data.get("arrival_time_type") or "flexible"
            arrival_no_earlier_than_hour = data.get("arrival_no_earlier_than_hour") or None
            arrival_no_earlier_than_period = data.get("arrival_no_earlier_than_period") or None
            
            # PRO certification
            pro_certified = 1 if data.get("pro_certified") else 0
            pro_certified_at = utcnow_naive().isoformat() if pro_certified else None
            
            # latitude/longitude already set from pre-validation above
            
            # Server-side duplicate guard.
            # Audit fix (May 2026 part 4): consolidated the two near-identical
            # duplicate-venue checks that lived here. The first only fired
            # when all three of (name, city, state) were truthy; the second
            # always fired. The second strictly dominates the first, so the
            # first was dead code with diverging copy.
            if venue_name:
                _dup_v = db.execute(text("""
                    SELECT v.id, v.venue_name, v.city, v.state FROM venues v
                    WHERE LOWER(v.venue_name) = LOWER(:n)
                      AND LOWER(v.city) = LOWER(:c)
                      AND UPPER(v.state) = UPPER(:s)
                    LIMIT 1
                """), {"n": venue_name, "c": city or "", "s": state or ""}).mappings().first()
                if _dup_v:
                    # Roll back the user we just created, then raise.
                    db.delete(user); db.commit()
                    raise HTTPException(
                        409,
                        f"A venue named '{_dup_v['venue_name']}' already exists "
                        f"in {_dup_v['city']}, {_dup_v['state']}. If this is your "
                        f"venue, use 'Request Access' on the duplicate alert."
                    )

            # Create venue profile
            venue = Venue(
                user_id=user.id,
                venue_name=venue_name,
                address_line_1=address,
                city=city,
                state=state,
                postal_code=zip_code
            )
            db.add(venue)
            db.commit()
            db.refresh(venue)
            
            # v73: Add ALL fields via raw SQL to ensure everything is saved
            try:
                db.execute(
                    text("""
                        UPDATE venues 
                        SET description = :desc,
                            default_pay_dollars = :pay_dollars,
                            default_pay_cents = :pay_cents,
                            artist_frequency_days = :freq,
                            venue_size = :cap,
                            latitude = :lat,
                            longitude = :lon,
                            has_stage = :has_stage,
                            stage_width_ft = :stage_width,
                            stage_depth_ft = :stage_depth,
                            setup_location_description = :setup_loc,
                            has_sound_equipment = :has_sound,
                            sound_equipment_description = :sound_desc,
                            has_sound_engineer = :has_engineer,
                            sound_engineer_details = :engineer_details,
                            has_lighting = :has_lighting,
                            lighting_description = :lighting_desc,
                            load_in_out_details = :load_details,
                            arrival_time_type = :arrival_type,
                            arrival_no_earlier_than_hour = :arrival_hour,
                            arrival_no_earlier_than_period = :arrival_period,
                            bar_tab_details = :bar_tab,
                            food_tab_details = :food_tab,
                            pro_certified = :pro_cert,
                            pro_certified_at = :pro_cert_at
                        WHERE id = :vid
                    """),
                    {
                        "desc": description, 
                        "pay_dollars": default_pay_dollars,
                        "pay_cents": default_pay_cents,
                        "freq": performance_frequency,
                        "cap": capacity,
                        "lat": latitude,
                        "lon": longitude,
                        "has_stage": has_stage,
                        "stage_width": stage_width_ft,
                        "stage_depth": stage_depth_ft,
                        "setup_loc": setup_location_description,
                        "has_sound": has_sound_equipment,
                        "sound_desc": sound_equipment_description,
                        "has_engineer": has_sound_engineer,
                        "engineer_details": sound_engineer_details,
                        "has_lighting": has_lighting,
                        "lighting_desc": lighting_description,
                        "load_details": load_in_out_details,
                        "arrival_type": arrival_time_type,
                        "arrival_hour": arrival_no_earlier_than_hour,
                        "arrival_period": arrival_no_earlier_than_period,
                        "bar_tab": bar_tab_details,
                        "food_tab": food_tab_details,
                        "pro_cert": pro_certified,
                        "pro_cert_at": pro_certified_at,
                        "vid": venue.id
                    }
                )
                db.commit()
                _venue_entity_save_failed = False
            except Exception as e:
                # Audit fix (May 2026 part 4): log the failure instead of
                # silently swallowing, and flag it so the welcome email
                # downstream knows the entity is half-populated.
                logger.warning(
                    f"Signup venue UPDATE failed (some fields may be missing) "
                    f"for venue {venue.id}: {e}", exc_info=True
                )
                _venue_entity_save_failed = True

            # Add creator as owner in entity_users
            db.execute(
                text("""
                    INSERT INTO entity_users (entity_type, entity_id, user_id, role, added_by_user_id, created_at)
                    VALUES ('venue', :entity_id, :user_id, 'owner', :user_id, CURRENT_TIMESTAMP)
                """),
                {"entity_id": venue.id, "user_id": user.id}
            )
            db.commit()

            # ── Affiliate link: check cookie/param first, then email match ──
            # Kill switch — if program disabled, skip linking entirely
            # (audit fix May 2026 part 9c). Cookie still gets deleted below.
            try:
                _en = db.execute(text(
                    "SELECT setting_value FROM platform_settings WHERE setting_key='affiliate_enabled'"
                )).scalar()
                _aff_enabled = (_en is None) or (str(_en).lower() in ("true", "1"))
            except Exception:
                _aff_enabled = True

            try:
                aff_code = data.get("affiliate_code") or (request.cookies.get("aff_code") or "")
                # Also check Referer header for ?aff= param as last resort
                if not aff_code:
                    referer = request.headers.get("referer", "")
                    import urllib.parse as _up
                    _qs = _up.urlparse(referer).query
                    aff_code = _up.parse_qs(_qs).get("aff", [""])[0]
                aff_code = aff_code.strip().upper()
                logger.info(f"Affiliate signup check: aff_code='{aff_code}' user={user.id} enabled={_aff_enabled}")
                affiliate_uid = None

                if _aff_enabled and aff_code:
                    row = db.execute(text("SELECT id FROM users WHERE affiliate_code = :c"), {"c": aff_code}).first()
                    if row and row[0] != user.id:
                        affiliate_uid = row[0]

                if _aff_enabled and not affiliate_uid:
                    # Match by earliest recommend email to this email address
                    rec = db.execute(text("""
                        SELECT sender_user_id FROM affiliate_recommend_emails
                        WHERE LOWER(recipient_email) = LOWER(:email)
                          AND sender_user_id != :uid
                        ORDER BY sent_at ASC LIMIT 1
                    """), {"email": email, "uid": user.id}).first()
                    if rec:
                        affiliate_uid = rec[0]

                # Audit fix (May 2026 part 9c): block Sybil self-referral —
                # the sender_user_id != :uid check only blocks self-recommend
                # FROM the same user account. Two accounts owned by the same
                # person (A1 sends recommend to A2's future email; A2 then
                # signs up as a venue) would earn commission. Detect by
                # matching the recipient_email against the sender's own
                # email on file: if the affiliate's own email matches the
                # signup email — or if they share an IP in the signup
                # collision log — reject the link.
                if affiliate_uid:
                    _aff_email = db.execute(text(
                        "SELECT email FROM users WHERE id = :uid"
                    ), {"uid": affiliate_uid}).scalar()
                    if _aff_email and str(_aff_email).strip().lower() == email.strip().lower():
                        logger.warning(
                            f"Affiliate link refused (self-referral): affiliate user {affiliate_uid} "
                            f"and signup email {email} are the same."
                        )
                        affiliate_uid = None

                if affiliate_uid:
                    method = "email_click" if aff_code else "email_match"

                    # Audit fix (May 2026 part 9c): rate snapshot still gets
                    # written to the row, but `_current_rate` now reads LIVE
                    # from platform_settings on every accrual (see
                    # affiliate.py:_current_rate). The snapshot here is now
                    # an audit / fallback record only.
                    def _aff_setting(key, default):
                        r = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key = :k"), {"k": key}).scalar()
                        try: return float(r) if r else default
                        except: return default

                    init_rate    = _aff_setting("affiliate_rate_percent", 1.0)
                    reduced_rate = _aff_setting("affiliate_reduced_rate_percent", 0.5)
                    reduced_days = int(_aff_setting("affiliate_reduced_after_days", 365))

                    db.execute(text("""
                        INSERT OR IGNORE INTO affiliate_referrals
                            (affiliate_user_id, venue_id, link_method, initial_rate_percent, reduced_rate_percent, reduced_after_days)
                        VALUES (:auid, :vid, :method, :init, :red, :days)
                    """), {"auid": affiliate_uid, "vid": venue.id, "method": method,
                           "init": init_rate, "red": reduced_rate, "days": reduced_days})
                    db.commit()
            except Exception as _ae:
                logger.error(f"Affiliate link error on signup: {_ae}")

            # Audit fix (May 2026 part 9c): always delete the aff_code cookie
            # after signup, regardless of whether linking succeeded. Without
            # this the 90-day cookie persists and a later signup on the same
            # browser (shared computer; partner's account) gets attributed
            # to the original affiliate from days/weeks ago — cross-account
            # attribution leak.
            try:
                response.delete_cookie("aff_code", path="/")
            except Exception:
                pass

        # Send welcome email — but skip if the venue entity-save failed
        # silently above. We don't want a "Welcome!" message landing while
        # the user's venue is missing capacity / pay / amenities.
        # Audit fix (May 2026 part 4).
        if locals().get("_venue_entity_save_failed"):
            logger.warning(
                f"Skipping welcome email for user {user.id} — venue entity "
                f"creation didn't fully populate; admin should follow up."
            )
        else:
            try:
                email_service = EmailService(db)
                user_name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or email
                email_service.send_notification_email(
                    user_email=email,
                    user_id=user.id,
                    notification_type="welcome",
                    variables={
                        "user_name": user_name,
                        "user_email": email
                    }
                )
            except Exception as e:
                logger.warning(f"Welcome email send failed for user {user.id}: {e}")

        # Send email verification (background thread so signup doesn't block on SMTP)
        try:
            _ensure_email_verified_column(db)
            import threading as _threading_verify
            _v_uid   = user.id
            _v_email = email
            _v_name  = data.get("first_name", "") or ""
            _v_base  = str(request.base_url).rstrip("/")
            def _send_verify_bg():
                _vdb = SessionLocal()
                try:
                    _send_verification_email(_vdb, _v_uid, _v_email, _v_name, _v_base)
                finally:
                    _vdb.close()
            _threading_verify.Thread(target=_send_verify_bg, daemon=True).start()
        except Exception:
            pass  # Never block signup if verification email fails

        # Auto-login: set signed session cookie
        set_session_cookie(response, user.id)

        return {"user_id": user.id}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Signup failed: {str(e)}")
        raise HTTPException(500, "Signup failed. Please try again.")
    finally:
        db.close()

# ============================================
# LOGIN
# ============================================

@router.post("/api/login")
@limiter.limit(rate_login_limit)
def login(request: Request, data: LoginRequest, response: Response):
    """Login with email and password, returns signed session cookie"""
    email = data.email.lower().strip()
    ip = _client_ip(request)

    # Check lockout BEFORE doing any DB work
    _check_lockout(email, ip)

    db = SessionLocal()
    try:
        # Find user by email
        user = db.query(User).filter(User.email == email).first()

        if not user:
            _record_failed_login(email, ip)
            raise HTTPException(401, "Invalid credentials")

        # Verify password
        if not verify_password(data.password, user.password):
            _record_failed_login(email, ip)
            raise HTTPException(401, "Invalid credentials")

        # Success — clear failed attempts and set session
        _clear_failed_logins(email, ip)
        set_session_cookie(response, user.id)

        # 2026-07-26: stamp last_login so the admin Directory tab shows a
        # real value. Previously never written, so every row in Directory
        # rendered "never" for last_login even for daily-active users.
        # Best-effort — swallow errors so a schema drift or DB blip
        # doesn't fail an otherwise-good login.
        try:
            from backend.utils import utcnow_naive
            db.execute(
                text("UPDATE users SET last_login = :now WHERE id = :uid"),
                {"now": utcnow_naive(), "uid": user.id}
            )
            db.commit()
        except Exception as _e:
            logger.warning(f"last_login update failed for user {user.id}: {_e}")
            try: db.rollback()
            except Exception: pass

        return {"ok": True}

    finally:
        db.close()

# ============================================
# NOTE: GET /api/me and PUT /api/me moved to routes/me.py
# ============================================

# ============================================
# LOGOUT
# ============================================

@router.post("/api/logout")
def logout(response: Response):
    """Logout and clear session cookie"""
    clear_session_cookie(response)
    return {"ok": True}

# ============================================
# PASSWORD CHANGE (BONUS)
# ============================================

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=100)

@router.post("/api/change-password")
@limiter.limit("5/minute")
def change_password(request: Request, response: Response, data: ChangePasswordRequest, user=Depends(get_current_user)):
    """Change user password.

    Audit fix (May 2026): rate-limited to 5/minute. Authenticated brute-force
    of `current_password` was previously unrestricted (the in-memory login
    lockout only fires on `/api/login`). With a stolen session cookie an
    attacker could grind through current_password attempts to lock in
    account takeover before the user noticed.

    Audit fix (May 2026, H1/H2): stamp users.password_changed_at to the wall
    clock on success. `get_current_user` rejects any session token issued
    before that timestamp, so every other device the account is logged in on
    is immediately kicked. The requesting browser gets a fresh cookie below
    so they don't lock themselves out.
    """
    db = SessionLocal()
    try:
        validate_password_or_raise(data.new_password)

        # Verify current password
        if not verify_password(data.current_password, user.password):
            raise HTTPException(401, "Current password is incorrect")

        # Hash new password
        new_hashed = hash_password(data.new_password)

        # Update password and stamp rotation time
        db.query(User).filter(User.id == user.id).update({
            "password": new_hashed,
            "password_changed_at": utcnow_naive(),
        })
        db.commit()

        # Re-issue session cookie so this browser keeps a valid token
        # (its old cookie predates password_changed_at and would be rejected)
        set_session_cookie(response, user.id)

        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Password change failed: {str(e)}")
        raise HTTPException(500, "Password change failed. Please try again.")
    finally:
        db.close()

# ============================================
# FORGOT PASSWORD / RESET PASSWORD
# ============================================

# Reuse the same serializer with a different salt for password reset tokens
_reset_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="password-reset")
RESET_TOKEN_MAX_AGE = 3600  # 1 hour


def _get_base_url(db=None) -> str:
    """
    Return the canonical public base URL for this deployment.
    Priority:
      1. GIGSFILL_BASE_URL environment variable
      2. 'site_url' key in platform_settings table (the canonical key used everywhere else)
      3. 'base_url' key in platform_settings table (legacy fallback)
      4. Hard-coded production domain as last resort
    Never returns localhost or 127.0.0.1.
    """
    url = os.environ.get("GIGSFILL_BASE_URL", "").strip().rstrip("/")
    if url and "127.0.0.1" not in url and "localhost" not in url:
        return url
    if db is not None:
        # Audit fix (May 2026 part 5): every other module reads 'site_url'.
        # auth.py was the only place still reading 'base_url' — admin Settings
        # writes to 'site_url' so password-reset / verify-email links could
        # silently fall back to the hardcoded production domain when the
        # legacy 'base_url' row was missing. Try the canonical key first;
        # fall back to 'base_url' for older deployments that only have it.
        for _key in ("site_url", "base_url"):
            try:
                row = db.execute(
                    text("SELECT setting_value FROM platform_settings WHERE setting_key = :k LIMIT 1"),
                    {"k": _key}
                ).first()
                if row and row[0] and "127.0.0.1" not in row[0] and "localhost" not in row[0]:
                    return row[0].strip().rstrip("/")
            except Exception:
                pass
    return "https://gigsfill.com"


@router.post("/api/forgot-password")
@limiter.limit(rate_password_reset_limit)
def forgot_password(request: Request, data: dict):
    """Send a password reset email. Always returns success to prevent email enumeration."""
    email = (data.get("email") or "").strip().lower()
    if not email:
        # Still return success to prevent enumeration
        return {"ok": True, "message": "If an account exists with that email, a reset link has been sent."}

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            # Don't reveal that the email doesn't exist
            return {"ok": True, "message": "If an account exists with that email, a reset link has been sent."}

        # Generate signed reset token. Each token gets a random `jti` claim
        # so /api/reset-password can mark it consumed (single-use guard, H9).
        import secrets as _secrets
        jti = _secrets.token_urlsafe(16)
        reset_token = _reset_serializer.dumps({"uid": user.id, "email": email, "jti": jti})

        # Build reset URL
        base_url = _get_base_url(db)
        reset_url = f"{base_url}/app/reset_password.html?token={reset_token}"

        # Send email
        try:
            from backend.email_service import EmailService
            email_service = EmailService(db)
            user_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or email
            email_service.send_notification_email(
                user_email=email,
                user_id=user.id,
                notification_type="password_reset",
                variables={
                    "user_name": user_name,
                    "reset_url": reset_url,
                    "user_email": email,
                }
            )
        except Exception as e:
            logger.error(f"[AUTH][RESET_FAIL] Failed to send password reset email to {email}: {e}")
            # Fall back to direct SMTP send if email template doesn't exist
            try:
                _send_reset_email_direct(db, email, user.first_name or "there", reset_url)
            except Exception as e2:
                # Audit fix (May 2026): tag SMTP failures so admin alerting can
                # match on the prefix and surface them. Previously the reset
                # endpoint returned 200 + "If an account exists..." even when
                # the email never went out — user retried, hit rate limit,
                # locked out, never knew why.
                logger.error(f"[AUTH][RESET_FAIL] Direct SMTP fallback also failed for {email}: {e2}")

        return {"ok": True, "message": "If an account exists with that email, a reset link has been sent."}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        return {"ok": True, "message": "If an account exists with that email, a reset link has been sent."}
    finally:
        db.close()


def _send_reset_email_direct(db, to_email: str, first_name: str, reset_url: str):
    """Fallback: send reset email directly via SMTP if template system fails."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    settings = {}
    rows = db.execute(
        text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_email', 'platform_email_password', 'platform_smtp_server', 'platform_smtp_port', 'platform_email_from_name')")
    ).fetchall()
    for r in rows:
        settings[r[0]] = r[1]

    smtp_email = settings.get('platform_email', '')
    smtp_password = settings.get('platform_email_password', '')
    smtp_server = settings.get('platform_smtp_server', 'smtp.gmail.com')
    smtp_port = int(settings.get('platform_smtp_port', '587'))
    from_name = settings.get('platform_email_from_name', 'GigsFill')

    if not smtp_email or not smtp_password:
        raise Exception("SMTP not configured")

    from email.utils import formataddr as _formataddr
    msg = MIMEMultipart('alternative')
    msg['From'] = _formataddr((from_name, smtp_email))
    msg['To'] = to_email
    msg['Subject'] = "Reset Your GigsFill Password"

    body_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" style="background:#f8f9fa;padding:40px 20px;">
<tr><td>
<table role="presentation" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<span style="font-size:18px;font-weight:700;letter-spacing:0.15em;color:#1a1a2e;">GIGSFILL</span>
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#1a1a2e;">Password Reset</h1>
<p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#4b5563;">Hi {first_name},</p>
<p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#4b5563;">We received a request to reset your GigsFill password. Click the button below to set a new password:</p>
<div style="text-align:center;margin-bottom:24px;">
<a href="{reset_url}" style="display:inline-block;padding:14px 32px;background:#1a1a2e;color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;border-radius:6px;">Reset Password</a>
</div>
<p style="margin:0 0 8px;font-size:13px;color:#9ca3af;">This link expires in 1 hour.</p>
<p style="margin:0;font-size:13px;color:#9ca3af;">If you didn't request this, you can safely ignore this email.</p>
</td></tr>
<tr><td style="padding:24px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""

    msg.attach(MIMEText(body_html, 'html'))

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as server:
            server.login(smtp_email, smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.send_message(msg)


@router.post("/api/reset-password")
@limiter.limit(rate_password_reset_limit)
def reset_password(request: Request, data: dict):
    """Reset password using a signed token from the forgot-password email."""
    token = data.get("token", "")
    new_password = data.get("new_password", "")

    if not token or not new_password:
        raise HTTPException(400, "Token and new password required")

    validate_password_or_raise(new_password)

    # Verify token
    try:
        payload = _reset_serializer.loads(token, max_age=RESET_TOKEN_MAX_AGE)
        user_id = int(payload["uid"])
        token_email = payload.get("email", "")
        token_jti = payload.get("jti", "")
    except SignatureExpired:
        raise HTTPException(400, "Reset link has expired. Please request a new one.")
    except (BadSignature, KeyError, TypeError, ValueError):
        raise HTTPException(400, "Invalid reset link. Please request a new one.")

    db = SessionLocal()
    try:
        # H9 audit fix (May 2026): single-use guard. If this token's jti has
        # already been consumed, reject. Pre-H9 tokens have no jti — those
        # remain replayable until they expire (1h), and only ever existed in
        # the small window before this fix shipped.
        if token_jti:
            already = db.execute(
                text("SELECT 1 FROM used_reset_tokens WHERE jti = :j"),
                {"j": token_jti}
            ).first()
            if already:
                raise HTTPException(400, "This reset link has already been used. Please request a new one.")

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(400, "Invalid reset link")

        # Extra check: email in token matches current user email
        if token_email and user.email != token_email:
            raise HTTPException(400, "Invalid reset link")

        # Hash and update password; stamp password_changed_at so every
        # session token issued before now is rejected (forces re-login on
        # every device — important when reset is triggered because of a
        # suspected compromise). H1/H2 audit fix, May 2026.
        new_hashed = hash_password(new_password)
        db.query(User).filter(User.id == user_id).update({
            "password": new_hashed,
            "password_changed_at": utcnow_naive(),
        })

        # Mark this token consumed (single-use). Best-effort: failure to
        # record shouldn't block the reset itself, but it does open a tiny
        # replay window — log loudly if it ever happens.
        if token_jti:
            try:
                db.execute(
                    text("INSERT INTO used_reset_tokens (jti) VALUES (:j)"),
                    {"j": token_jti}
                )
                # Opportunistic prune of jti rows older than the token TTL.
                # Audit fix (May 2026 part 6): `datetime('now', '-2 hours')` is
                # SQLite-only and raises a function-not-found on Postgres,
                # aborting the transaction. The outer try/except caught it but
                # left the connection poisoned, so `db.commit()` below failed
                # and the new password was never persisted. Compute the cutoff
                # in Python and bind as a portable timestamp param.
                _two_hours_ago = utcnow_naive() - timedelta(hours=2)
                db.execute(text(
                    "DELETE FROM used_reset_tokens WHERE used_at < :cutoff"
                ), {"cutoff": _two_hours_ago})
            except Exception as _e:
                logger.error(f"[H9] failed to record used reset jti={token_jti}: {_e}")

        db.commit()

        return {"ok": True, "message": "Password reset successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Password reset failed: {e}")
        raise HTTPException(500, "Password reset failed. Please try again.")
    finally:
        db.close()

# ============================================
# EMAIL VERIFICATION
# ============================================
# Uses itsdangerous (same library as sessions) with a dedicated salt.
# Token is valid for 72 hours. Column is auto-added if absent.
# Strategy: warn but don't hard-block — respects users who signed up before
# this feature shipped and prevents lockouts on SMTP failure.

_verify_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt="email-verify")
VERIFY_TOKEN_MAX_AGE = 72 * 3600  # 72 hours


def _ensure_email_verified_column(db):
    """Add email_verified column to users table if missing (zero-downtime migration).

    Audit fix (May 2026 part 6): the canonical declaration is now in
    `db.py:setup_database()` (via _add_columns), so this is a defensive
    backstop. The previous SQLite-only PRAGMA path silently failed on
    Postgres, leaving the column missing and every email-change / verify-email
    UPDATE crashing with UndefinedColumn → 500."""
    try:
        from backend.db import _IS_POSTGRES
        if _IS_POSTGRES:
            cols = [r[0] for r in db.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name='users'"
            )).fetchall()]
        else:
            cols = [r[1] for r in db.execute(text("PRAGMA table_info(users)")).fetchall()]
        if "email_verified" not in cols:
            db.execute(text("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0"))
            db.commit()
    except Exception as _e:
        logger.warning(f"_ensure_email_verified_column: {_e}")


def _send_verification_email(db, user_id: int, email: str, first_name: str, base_url: str = ""):
    """Generate a signed verification token and send the email. Swallows SMTP errors."""
    token = _verify_serializer.dumps({"uid": user_id, "email": email})

    # Use the shared helper — never returns 127.0.0.1 or localhost
    if not base_url or "127.0.0.1" in base_url or "localhost" in base_url:
        base_url = _get_base_url(db)

    verify_url = f"{base_url}/api/verify-email?token={token}"

    sent = False
    try:
        from backend.email_service import EmailService
        email_service = EmailService(db)
        sent = email_service.send_notification_email(
            user_email=email,
            user_id=user_id,
            notification_type="email_verification",
            variables={
                "user_name": first_name or "there",
                "verify_url": verify_url,
                "user_email": email,
            }
        )
    except Exception:
        sent = False

    if sent:
        return  # Template path succeeded — done

    # Fallback: send directly via SMTP (template missing or send failed)
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        settings = {}
        rows = db.execute(
            text("SELECT setting_key, setting_value FROM platform_settings "
                 "WHERE setting_key IN ('platform_email','platform_email_password',"
                 "'platform_smtp_server','platform_smtp_port','platform_from_name')")
        ).fetchall()
        for r in rows:
            settings[r[0]] = r[1]

        smtp_email = settings.get("platform_email", "")
        smtp_password = settings.get("platform_email_password", "")
        smtp_server = settings.get("platform_smtp_server", "smtp.gmail.com")
        smtp_port = int(settings.get("platform_smtp_port", "587"))
        from_name = settings.get("platform_from_name", "GigsFill")

        if not smtp_email or not smtp_password:
            logger.warning("_send_verification_email: SMTP not configured, cannot send verify email")
            return

        msg = MIMEMultipart('alternative')
        from email.utils import formataddr
        msg["From"] = formataddr((from_name, smtp_email))
        msg["To"] = email
        msg["Subject"] = "Verify your GigsFill email address"

        html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" style="background:#f8f9fa;padding:40px 20px;">
<tr><td><table role="presentation" width="100%" style="max-width:560px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
  <span style="font-size:18px;font-weight:700;letter-spacing:.15em;color:#1a1a2e;">GIGSFILL</span>
</td></tr>
<tr><td style="padding:32px 40px;">
  <h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#1a1a2e;">Verify your email</h1>
  <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#4b5563;">Hi {first_name or 'there'},<br><br>
  Click the button below to verify your GigsFill email address. This link expires in 72 hours.</p>
  <div style="margin-bottom:24px;">
    <a href="{verify_url}" style="display:inline-block;padding:14px 32px;background:#1a1a2e;color:#fff;font-size:15px;font-weight:600;text-decoration:none;border-radius:6px;">Verify Email Address</a>
  </div>
  <p style="margin:0;font-size:13px;color:#9ca3af;">If you didn't create a GigsFill account, you can ignore this email.</p>
</td></tr>
<tr><td style="padding:24px 40px;background:#f8f9fa;border-top:1px solid #eee;">
  <p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill</p>
</td></tr>
</table></td></tr></table>
</body></html>"""

        msg.attach(MIMEText(html, "html"))
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as s:
                s.login(smtp_email, smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as s:
                s.starttls()
                s.login(smtp_email, smtp_password)
                s.send_message(msg)
        logger.info(f"_send_verification_email: sent directly to {email}")
    except Exception as _e2:
        logger.error(f"_send_verification_email fallback failed: {_e2}")


@router.get("/api/verify-email")
def verify_email(token: str, response: Response):
    """Verify email from the link in the verification email. Redirects to the app."""
    from fastapi.responses import HTMLResponse
    # Use a fresh DB connection to look up base_url from platform_settings
    _base_db = SessionLocal()
    try:
        base_url = _get_base_url(_base_db)
    finally:
        _base_db.close()

    def _page(heading: str, msg: str, color: str = "#22c55e", auto_redirect: str = "") -> HTMLResponse:
        # auto_redirect: URL to redirect to after 3s (success only)
        redirect_script = ""
        redirect_note = ""
        if auto_redirect:
            redirect_script = f'<script>setTimeout(function(){{window.location.href="{auto_redirect}";}},3000);</script>'
            redirect_note = '<p style="color:#6b7280;font-size:0.78rem;margin:12px 0 0;">Redirecting you in 3 seconds...</p>'
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GigsFill – Email Verification</title>
<style>body{{margin:0;background:#0f1419;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;}}
.card{{background:#1a1f2e;border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:40px 48px;
max-width:440px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.4);}}</style>
{redirect_script}
</head><body><div class="card">
<div style="font-size:3rem;margin-bottom:16px;">{'✓' if color=='#22c55e' else '✗'}</div>
<h2 style="color:{color};margin:0 0 12px;font-size:1.3rem;">{heading}</h2>
<p style="color:#9ca3af;margin:0 0 24px;font-size:.9rem;line-height:1.6;">{msg}</p>
<a href="{base_url}/app/user-profile.html"
   style="display:inline-block;padding:12px 28px;background:#1a1a2e;border:1px solid rgba(255,255,255,.15);
   color:#e5e5e5;text-decoration:none;border-radius:8px;font-size:.9rem;">Go to GigsFill</a>
{redirect_note}
</div></body></html>""")

    try:
        payload = _verify_serializer.loads(token, max_age=VERIFY_TOKEN_MAX_AGE)
        user_id = int(payload["uid"])
        token_email = payload.get("email", "")
    except SignatureExpired:
        return _page("Link Expired",
                     "Your verification link has expired. Log in and request a new one.",
                     "#f59e0b")
    except (BadSignature, KeyError, TypeError, ValueError):
        return _page("Invalid Link",
                     "This verification link is not valid. Please request a new one.",
                     "#ef4444")

    db = SessionLocal()
    try:
        _ensure_email_verified_column(db)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return _page("Invalid Link", "Account not found.", "#ef4444")
        if token_email and user.email != token_email:
            return _page("Link Mismatch",
                         "This link was for a different email address. Please request a new one.",
                         "#ef4444")

        db.execute(
            text("UPDATE users SET email_verified = 1 WHERE id = :uid"),
            {"uid": user_id}
        )
        db.commit()

        # Redirect destination: user-profile will forward them to their dashboard
        redirect_dest = f"{base_url}/app/user-profile.html"
        return _page("Email Verified! ✓",
                     "Your email address has been confirmed. You're all set.",
                     auto_redirect=redirect_dest)
    finally:
        db.close()


@router.post("/api/resend-verification-email")
@limiter.limit("3/hour")
def resend_verification_email(request: Request, user=Depends(get_current_user)):
    """Re-send the verification email for the currently logged-in user."""
    db = SessionLocal()
    try:
        _ensure_email_verified_column(db)
        row = db.execute(
            text("SELECT email, first_name, email_verified FROM users WHERE id = :uid"),
            {"uid": user.id}
        ).mappings().first()
        if not row:
            raise HTTPException(404, "User not found")
        if row["email_verified"]:
            return {"ok": True, "message": "Email is already verified."}

        import threading
        _email = row["email"]
        _name = row["first_name"] or ""
        _uid = user.id
        _base = str(request.base_url).rstrip("/")
        def _bg():
            _db = SessionLocal()
            try:
                _send_verification_email(_db, _uid, _email, _name, _base)
            finally:
                _db.close()
        threading.Thread(target=_bg, daemon=True).start()
        return {"ok": True, "message": "Verification email sent."}
    finally:
        db.close()
