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
from backend.rate_limiter import limiter, RATE_LOGIN, RATE_SIGNUP, RATE_PASSWORD_RESET

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

def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
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

def get_current_user(session_token: str | None = Cookie(default=None)):
    """Dependency to get current authenticated user from signed session cookie."""
    if not session_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user_id = verify_session_token(session_token)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid session")
        return user
    finally:
        db.close()


def get_optional_user(session_token: str | None = Cookie(default=None)):
    """Like get_current_user but returns None instead of raising if not logged in."""
    if not session_token:
        return None
    try:
        user_id = verify_session_token(session_token)
        db = SessionLocal()
        try:
            return db.query(User).filter(User.id == user_id).first()
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
_login_attempts = {}  # {(email, ip): {"count": int, "locked_until": datetime}}
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
    """Check if (email, ip) is locked out. Raises HTTPException if locked."""
    key = (email, ip)
    with _login_lock:
        record = _login_attempts.get(key)
        if record and record.get("locked_until"):
            if utcnow_naive() < record["locked_until"]:
                remaining = (record["locked_until"] - utcnow_naive()).seconds // 60 + 1
                raise HTTPException(
                    429,
                    f"Too many failed login attempts. Try again in {remaining} minutes."
                )
            else:
                # Lockout expired — reset
                _login_attempts.pop(key, None)


def _record_failed_login(email: str, ip: str = ""):
    """Record a failed login attempt. Lock the (email, ip) pair if threshold exceeded."""
    key = (email, ip)
    with _login_lock:
        record = _login_attempts.setdefault(key, {"count": 0, "locked_until": None})
        record["count"] += 1
        if record["count"] >= MAX_LOGIN_ATTEMPTS:
            record["locked_until"] = utcnow_naive() + LOCKOUT_DURATION
            logger.warning(f"Login locked for ({email}, {ip}) after {record['count']} failed attempts")


def _clear_failed_logins(email: str, ip: str = ""):
    """Clear failed attempts on successful login. Clears every IP for this email."""
    with _login_lock:
        # Clear every entry that matches the email — covers legit user across
        # IPs and prevents stale lockouts for users on dynamic IPs.
        for k in list(_login_attempts.keys()):
            if isinstance(k, tuple) and k[0] == email:
                _login_attempts.pop(k, None)

# ============================================
# SIGN UP
# ============================================

@router.post("/api/signup")
@limiter.limit(RATE_SIGNUP)
def signup(request: Request, data: dict, response: Response):
    """Create a new user account with hashed password and auto-create artist/venue profile"""
    from backend.us_cities import find_city
    from backend.email_service import EmailService
    
    db = SessionLocal()
    try:
        # ── Check signups_enabled kill switch ────────────────────────────────
        try:
            signups_on = db.execute(
                text("SELECT setting_value FROM platform_settings WHERE setting_key = 'signups_enabled'")
            ).scalar()
            # Default is open (None means key not set yet)
            if signups_on is not None and str(signups_on).lower() in ('false', '0'):
                raise HTTPException(503, "New signups are temporarily closed. Please check back soon.")
        except HTTPException:
            raise
        except Exception:
            pass  # If settings table missing, allow signup

        # Validate required fields
        email = (data.get("email") or "").strip().lower()
        password = data.get("password")
        role = data.get("role")
        
        if not email or not password:
            raise HTTPException(400, "Email and password required")
        
        if len(password) < 6:
            raise HTTPException(400, "Password must be at least 6 characters")
            
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
            try:
                from backend.email_service import EmailService
                _es = EmailService(db)
                if _es.enabled:
                    _first = existing.first_name or ""
                    _es._send_raw_email(
                        to_email=existing.email,
                        subject="Someone tried to create a GigsFill account with your email",
                        html_body=(
                            f"<p>Hi {_first},</p>"
                            f"<p>An account already exists at GigsFill with this email address. "
                            f"Someone just tried to sign up using it.</p>"
                            f"<p>If this was you, just <a href=\"https://gigsfill.com/login.html\">log in</a> "
                            f"or <a href=\"https://gigsfill.com/forgot-password.html\">reset your password</a>.</p>"
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

        # Generate unique affiliate code
        import secrets as _sec
        for _ in range(20):
            aff_code = "AFF-" + _sec.token_hex(4).upper()
            exists = db.execute(text("SELECT id FROM users WHERE affiliate_code = :c"), {"c": aff_code}).first()
            if not exists:
                db.execute(text("UPDATE users SET affiliate_code = :c WHERE id = :uid"), {"c": aff_code, "uid": user.id})
                db.commit()
                break
        

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

            # Create artist profile
            artist = Artist(
                user_id=user.id,
                name=artist_name,
                artist_type=artist_type,
                band_formats=band_formats,
                styles=styles,
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
            
            # Check for duplicate venue name + city + state
            if venue_name and city and state:
                dup_v = db.execute(text("""
                    SELECT id FROM venues
                    WHERE LOWER(venue_name) = LOWER(:n) AND LOWER(city) = LOWER(:c) AND UPPER(state) = UPPER(:s)
                """), {"n": venue_name, "c": city, "s": state}).first()
                if dup_v:
                    db.delete(user)
                    db.commit()
                    raise HTTPException(409, f"A venue named '{venue_name}' already exists in {city}, {state}. Log in to that account, or request access from the profile page.")

            # Server-side duplicate guard
            _dup_v = db.execute(text("""
                SELECT v.id, v.venue_name, v.city, v.state FROM venues v
                WHERE LOWER(v.venue_name) = LOWER(:n) AND LOWER(v.city) = LOWER(:c) AND UPPER(v.state) = UPPER(:s)
                LIMIT 1
            """), {"n": venue_name, "c": city or "", "s": state or ""}).mappings().first()
            if _dup_v:
                # Roll back the user we just created, then raise
                db.delete(user); db.commit()
                raise HTTPException(409, f"A venue named '{_dup_v['venue_name']}' already exists in {_dup_v['city']}, {_dup_v['state']}. If this is your venue, use 'Request Access' on the duplicate alert.")

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
            except Exception as e:
                pass  # Don't fail the whole signup if some fields can't be saved
            
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
            try:
                aff_code = data.get("affiliate_code") or (request.cookies.get("aff_code") or "")
                # Also check Referer header for ?aff= param as last resort
                if not aff_code:
                    referer = request.headers.get("referer", "")
                    import urllib.parse as _up
                    _qs = _up.urlparse(referer).query
                    aff_code = _up.parse_qs(_qs).get("aff", [""])[0]
                aff_code = aff_code.strip().upper()
                logger.info(f"Affiliate signup check: aff_code='{aff_code}' user={user.id}")
                affiliate_uid = None

                if aff_code:
                    row = db.execute(text("SELECT id FROM users WHERE affiliate_code = :c"), {"c": aff_code}).first()
                    if row and row[0] != user.id:
                        affiliate_uid = row[0]

                if not affiliate_uid:
                    # Match by earliest recommend email to this email address
                    rec = db.execute(text("""
                        SELECT sender_user_id FROM affiliate_recommend_emails
                        WHERE LOWER(recipient_email) = LOWER(:email)
                          AND sender_user_id != :uid
                        ORDER BY sent_at ASC LIMIT 1
                    """), {"email": email, "uid": user.id}).first()
                    if rec:
                        affiliate_uid = rec[0]

                if affiliate_uid:
                    # Read current affiliate settings
                    def _aff_setting(key, default):
                        r = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key = :k"), {"k": key}).scalar()
                        try: return float(r) if r else default
                        except: return default

                    init_rate    = _aff_setting("affiliate_rate_percent", 1.0)
                    reduced_rate = _aff_setting("affiliate_reduced_rate_percent", 0.5)
                    reduced_days = int(_aff_setting("affiliate_reduced_after_days", 365))
                    method = "email_click" if aff_code else "email_match"

                    db.execute(text("""
                        INSERT OR IGNORE INTO affiliate_referrals
                            (affiliate_user_id, venue_id, link_method, initial_rate_percent, reduced_rate_percent, reduced_after_days)
                        VALUES (:auid, :vid, :method, :init, :red, :days)
                    """), {"auid": affiliate_uid, "vid": venue.id, "method": method,
                           "init": init_rate, "red": reduced_rate, "days": reduced_days})
                    db.commit()
            except Exception as _ae:
                logger.error(f"Affiliate link error on signup: {_ae}")

        # Send welcome email
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
            pass  # Don't fail signup if email fails

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
@limiter.limit(RATE_LOGIN)
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
def change_password(request: Request, data: ChangePasswordRequest, user=Depends(get_current_user)):
    """Change user password.

    Audit fix (May 2026): rate-limited to 5/minute. Authenticated brute-force
    of `current_password` was previously unrestricted (the in-memory login
    lockout only fires on `/api/login`). With a stolen session cookie an
    attacker could grind through current_password attempts to lock in
    account takeover before the user noticed.
    """
    db = SessionLocal()
    try:
        # Verify current password
        if not verify_password(data.current_password, user.password):
            raise HTTPException(401, "Current password is incorrect")

        # Hash new password
        new_hashed = hash_password(data.new_password)

        # Update password
        db.query(User).filter(User.id == user.id).update({
            "password": new_hashed
        })
        db.commit()

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
      2. 'base_url' key in platform_settings table
      3. Hard-coded production domain as last resort
    Never returns localhost or 127.0.0.1.
    """
    url = os.environ.get("GIGSFILL_BASE_URL", "").strip().rstrip("/")
    if url and "127.0.0.1" not in url and "localhost" not in url:
        return url
    if db is not None:
        try:
            row = db.execute(
                text("SELECT setting_value FROM platform_settings WHERE setting_key = 'base_url' LIMIT 1")
            ).first()
            if row and row[0] and "127.0.0.1" not in row[0] and "localhost" not in row[0]:
                return row[0].strip().rstrip("/")
        except Exception:
            pass
    return "https://gigsfill.com"


@router.post("/api/forgot-password")
@limiter.limit(RATE_PASSWORD_RESET)
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

        # Generate signed reset token
        reset_token = _reset_serializer.dumps({"uid": user.id, "email": email})

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
    msg = MIMEMultipart()
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
@limiter.limit(RATE_PASSWORD_RESET)
def reset_password(request: Request, data: dict):
    """Reset password using a signed token from the forgot-password email."""
    token = data.get("token", "")
    new_password = data.get("new_password", "")

    if not token or not new_password:
        raise HTTPException(400, "Token and new password required")

    if len(new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    # Verify token
    try:
        payload = _reset_serializer.loads(token, max_age=RESET_TOKEN_MAX_AGE)
        user_id = int(payload["uid"])
        token_email = payload.get("email", "")
    except SignatureExpired:
        raise HTTPException(400, "Reset link has expired. Please request a new one.")
    except (BadSignature, KeyError, TypeError, ValueError):
        raise HTTPException(400, "Invalid reset link. Please request a new one.")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(400, "Invalid reset link")

        # Extra check: email in token matches current user email
        if token_email and user.email != token_email:
            raise HTTPException(400, "Invalid reset link")

        # Hash and update password
        new_hashed = hash_password(new_password)
        db.query(User).filter(User.id == user_id).update({"password": new_hashed})
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
    """Add email_verified column to users table if missing (zero-downtime migration)."""
    try:
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

        msg = MIMEMultipart()
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
