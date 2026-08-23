"""
GigsFill Backend - Main Application
====================================
FastAPI application entry point.
Start the server with: uvicorn backend.main:app --reload --port 8001
"""

import os
import logging
from pathlib import Path

# Configure logging so all gigsfill.* loggers output to stdout/journalctl
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    handlers=[logging.StreamHandler()]
)
logging.getLogger("gigsfill").setLevel(logging.INFO)


# ── Error Email Alert Handler ─────────────────────────────────────────────────
# Sends an email to the admin whenever logger.error() or logger.critical() fires
# anywhere in the gigsfill.* logger hierarchy.
# Config is read from platform_settings at emit-time so it always uses current creds.
import threading as _alert_threading
import time as _alert_time

class _ErrorEmailHandler(logging.Handler):
    """Throttled email handler: max 1 alert per unique message per 5 minutes."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self._seen: dict = {}   # message_key -> last_sent_timestamp
        self._lock = _alert_threading.Lock()
        self._throttle_secs = 300  # 5 minutes between identical alerts

    def _get_smtp(self):
        """Read SMTP config from DB at send time (always fresh)."""
        try:
            import sqlite3 as _sq
            from backend.db import DB_PATH as _dbp
            _c = _sq.connect(str(_dbp))
            _c.row_factory = _sq.Row
            rows = _c.execute(
                "SELECT setting_key, setting_value FROM platform_settings "
                "WHERE setting_key IN ('platform_email','platform_email_password',"
                "'platform_smtp_server','platform_smtp_port','support_email')"
            ).fetchall()
            _c.close()
            cfg = {r["setting_key"]: r["setting_value"] for r in rows}
            return cfg
        except Exception:
            return {}

    def emit(self, record: logging.LogRecord):
        try:
            msg_key = f"{record.name}:{record.getMessage()[:120]}"
            now = _alert_time.time()
            with self._lock:
                last = self._seen.get(msg_key, 0)
                if now - last < self._throttle_secs:
                    return
                self._seen[msg_key] = now
            # Send in background so it never blocks the request thread
            _alert_threading.Thread(
                target=self._send,
                args=(record,),
                daemon=True
            ).start()
        except Exception:
            pass

    def _send(self, record: logging.LogRecord):
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            import traceback as _tb

            cfg = self._get_smtp()
            smtp_user = cfg.get("platform_email", "")
            smtp_pass = cfg.get("platform_email_password", "")
            smtp_host = cfg.get("platform_smtp_server", "smtp.gmail.com")
            smtp_port = int(cfg.get("platform_smtp_port", 587))
            to_addr   = cfg.get("support_email", "") or smtp_user

            if not smtp_user or not smtp_pass or not to_addr:
                return  # SMTP not configured yet — silent skip

            level   = record.levelname
            logger_name = record.name
            message = record.getMessage()
            exc_txt = ""
            if record.exc_info:
                exc_txt = "".join(_tb.format_exception(*record.exc_info))

            subject = f"[GigsFill ERROR] {logger_name}: {message[:80]}"
            body_html = f"""
<html><body style="font-family:monospace;font-size:13px;">
<h2 style="color:#dc2626;">⚠️ GigsFill Server Error</h2>
<table style="border-collapse:collapse;width:100%">
  <tr><td style="padding:4px 8px;font-weight:700;width:120px;">Level</td>
      <td style="padding:4px 8px;color:#dc2626;">{level}</td></tr>
  <tr style="background:#f9fafb;"><td style="padding:4px 8px;font-weight:700;">Logger</td>
      <td style="padding:4px 8px;">{logger_name}</td></tr>
  <tr><td style="padding:4px 8px;font-weight:700;">Message</td>
      <td style="padding:4px 8px;">{message}</td></tr>
  <tr style="background:#f9fafb;"><td style="padding:4px 8px;font-weight:700;">File</td>
      <td style="padding:4px 8px;">{record.pathname}:{record.lineno}</td></tr>
  <tr><td style="padding:4px 8px;font-weight:700;">Time</td>
      <td style="padding:4px 8px;">{_alert_time.strftime("%Y-%m-%d %H:%M:%S UTC", _alert_time.gmtime())}</td></tr>
</table>
{"<h3>Traceback</h3><pre style='background:#fee2e2;padding:12px;border-radius:6px;overflow:auto;'>" + exc_txt + "</pre>" if exc_txt else ""}
<p style="color:#6b7280;font-size:11px;">This alert is throttled to once per 5 minutes per unique error message.</p>
</body></html>"""

            msg = MIMEMultipart("alternative")
            msg["From"]    = smtp_user
            msg["To"]      = to_addr
            msg["Subject"] = subject
            msg.attach(MIMEText(body_html, "html"))

            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as s:
                    s.login(smtp_user, smtp_pass)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                    s.ehlo()
                    try: s.starttls(); s.ehlo()
                    except Exception: pass
                    s.login(smtp_user, smtp_pass)
                    s.send_message(msg)
        except Exception:
            pass  # Never let the alert handler itself crash the app


_error_alert_handler = _ErrorEmailHandler()
logging.getLogger("gigsfill").addHandler(_error_alert_handler)
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.routes import (
    auth, artists, venues, gigs, me, media,
    preferred_artists, notifications,
    cities, admin, admin_payments, emails, venue_emails, entity_users,
    analytics, tax, contracts, stripe_connect, flyers, onboarding,
    reviews, messages, availability, waitlist, affiliate, vanity,
    unsubscribe, demo_requests, contact,
    artist_external_gigs, pricing,
)

def ensure_database():
    """Create database and tables if they don't exist"""
    from backend.db import DB_PATH, setup_database
    
    if not DB_PATH.exists():
        setup_database()
    else:
        # Run setup anyway to ensure all tables/columns exist (migrations)
        setup_database()

def ensure_email_templates():
    """Sync email templates from email_templates.py into database"""
    from backend.email_templates import run_migration
    
    try:
        run_migration()
    except Exception as e:
        logger.error(f"⚠️ Email templates sync failed: {e}")

# Initialize database on startup
ensure_database()

# Populate email templates on startup
ensure_email_templates()


def ensure_vanity_backfill():
    """Generate a vanity slug for every artist/venue that doesn't have one
    yet. Idempotent — safe to run on every startup. Cheap query when the
    backfill is already done."""
    try:
        from backend.db import SessionLocal
        from backend.routes.vanity import backfill_existing_entities
        db = SessionLocal()
        try:
            inserted = backfill_existing_entities(db)
            if inserted["artists"] or inserted["venues"]:
                logger.info(
                    f"✅ Vanity URLs backfilled: "
                    f"{inserted['artists']} artists, {inserted['venues']} venues"
                )
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Vanity backfill skipped: {e}", exc_info=True)


ensure_vanity_backfill()

# ─── Error tracking (Sentry) ─────────────────────────────────────────
# Gated on SENTRY_DSN env var. When unset (default for dev + current
# prod), the SDK is imported but never initialized — runtime overhead
# is one missed env var read per startup. The day we provision a
# Sentry project, set SENTRY_DSN in the systemd drop-in and it lights
# up automatically. No code change needed at cutover.
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES", "0.0")),
            send_default_pii=False,
            environment=os.environ.get("GIGSFILL_ENV", "production"),
            release=os.environ.get("GIGSFILL_RELEASE") or None,
        )
        logger.info(f"[SENTRY] initialized — env={os.environ.get('GIGSFILL_ENV','production')}")
    except Exception as _se:
        logger.warning(f"[SENTRY] init failed: {_se} — continuing without error tracking")

# Create FastAPI app
app = FastAPI(title="GigsFill API", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)

# Stripe SDK global config (Jul 1 2026 audit RED fix). Without a timeout,
# the Stripe SDK's default is 80s per request with 0 retries — a Stripe
# outage during checkout, onboarding, or the payout scheduler would hang
# uvicorn workers for 80s each. With only 2 workers, 4 concurrent hangs
# = total site outage. 15s per request + 2 retries strikes a balance
# between "resilient to a Stripe blip" and "don't wedge the worker."
import logging as _logging_boot
_logger_boot = _logging_boot.getLogger("gigsfill.boot")
try:
    import stripe as _stripe_global
    # Stripe SDK 14.x moved the http_client module to _http_client.
    from stripe._http_client import RequestsClient as _StripeRequestsClient
    _stripe_global.default_http_client = _StripeRequestsClient(timeout=15)
    _stripe_global.max_network_retries = 2
    _logger_boot.info("[STRIPE] SDK configured with 15s timeout + 2 retries")
except Exception as _stripe_cfg_e:
    _logger_boot.warning(f"[STRIPE] SDK config failed (falling back to defaults): {_stripe_cfg_e}")

# Rate limiting
from backend.rate_limiter import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Background schedulers (payout + email blasts) only run when explicitly enabled
# via GIGSFILL_RUN_SCHEDULERS=1. The dedicated `gigsfill-scheduler.service`
# systemd unit sets this; the main API service does NOT, so the schedulers
# run in exactly one process — never duplicated across uvicorn workers.
if os.environ.get("GIGSFILL_RUN_SCHEDULERS", "0") in ("1", "true", "yes"):
    from backend.payout_scheduler import start_payout_scheduler
    start_payout_scheduler()

    from backend.scheduler import start_scheduler
    start_scheduler()
    logging.getLogger("gigsfill.main").info(
        "✅ Background schedulers started (GIGSFILL_RUN_SCHEDULERS enabled)"
    )
else:
    logging.getLogger("gigsfill.main").info(
        "⏭️  Background schedulers NOT started in this process "
        "(GIGSFILL_RUN_SCHEDULERS not set — handled by gigsfill-scheduler.service)"
    )

# Include all routers
app.include_router(auth.router)
app.include_router(artists.router)
app.include_router(venues.router)
app.include_router(media.router)
app.include_router(gigs.router)
app.include_router(me.router)
app.include_router(preferred_artists.router)
app.include_router(notifications.router)
app.include_router(cities.router)
app.include_router(admin.router)
app.include_router(admin_payments.router)
app.include_router(pricing.router)
app.include_router(emails.router)
app.include_router(venue_emails.router)
app.include_router(entity_users.router)
app.include_router(analytics.router)
app.include_router(tax.router)
app.include_router(contracts.router)
app.include_router(stripe_connect.router)
app.include_router(flyers.router)
app.include_router(onboarding.router)
app.include_router(reviews.router)
from backend.routes import review_links
from backend.routes.gig_modal import router as gig_modal_router
app.include_router(review_links.router)
app.include_router(messages.router)
from backend.routes import calendar_feed
app.include_router(calendar_feed.router)
from backend.routes import gig_templates
app.include_router(gig_templates.router)
from backend.routes import door_settle
app.include_router(door_settle.router)
app.include_router(availability.router)
app.include_router(waitlist.router)
app.include_router(gig_modal_router)
app.include_router(affiliate.router)

# Vanity URL management endpoints (/api/vanity/...) — the resolver catch-all
# `GET /{slug}` is registered AFTER all other @app.* decorators below so it
# doesn't shadow /health, /robots.txt, /sitemap.xml, etc. See bottom of file.

# CORS: from env in production (e.g. CORS_ORIGINS=https://gigsfill.com)
_cors_origins = os.environ.get("CORS_ORIGINS", "http://127.0.0.1:8001").strip()
if _cors_origins:
    _origins_list = [o.strip() for o in _cors_origins.split(",") if o.strip()]
else:
    _origins_list = ["http://127.0.0.1:8001"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GZip compression for responses > 500 bytes
app.add_middleware(GZipMiddleware, minimum_size=500)

# Security headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ─── Request-ID middleware ───────────────────────────────────────────
# Stamps every request with a short uuid that propagates into log
# records via a contextvar. Lets you grep journalctl for one user's
# trail without thread-id guessing. Echoed back to the client as
# `X-Request-ID` so support can paste it from a screenshot and we
# pull the matching log lines. Negligible overhead.
import contextvars
import uuid as _uuid_mod

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gigsfill_request_id", default="-"
)


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id_ctx.get()
        return True


# Attach the filter to the gigsfill logger tree and the root uvicorn
# loggers so existing log lines pick up `%(request_id)s` automatically
# once the formatter is changed (formatter change is opt-in via env).
for _ln in ("gigsfill", "uvicorn.access", "uvicorn.error"):
    try:
        logging.getLogger(_ln).addFilter(_RequestIdFilter())
    except Exception:
        pass


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or _uuid_mod.uuid4().hex[:12]
        token = _request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _request_id_ctx.reset(token)


class RollingSessionMiddleware(BaseHTTPMiddleware):
    """Re-issue session cookie on every authenticated request when token is >50% through lifetime.
    This implements rolling/sliding session expiry so active users are never unexpectedly logged out."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Only process HTML or API responses (skip static files)
        path = request.url.path
        if path.startswith('/app/static/') or path in ('/robots.txt', '/sitemap.xml', '/sw.js'):
            return response
        token = request.cookies.get("session_token")
        if token:
            try:
                from backend.routes.auth import should_renew_token, set_session_cookie, verify_session_token
                if should_renew_token(token):
                    user_id = verify_session_token(token)
                    set_session_cookie(response, user_id)
            except Exception:
                pass  # Don't break the response on renewal failure
        return response


app.add_middleware(RollingSessionMiddleware)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # CSRF protection: state-changing requests must come from our origin
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            content_type = request.headers.get("content-type", "")
            # API calls with JSON body must include Content-Type header
            # (browsers won't send application/json from cross-origin forms)
            origin = request.headers.get("origin", "")
            referer = request.headers.get("referer", "")
            host = request.headers.get("host", "")
            
            # Allow requests with no origin (same-origin, curl, etc.)
            # Block requests from foreign origins
            if origin and host:
                from urllib.parse import urlparse
                origin_host = urlparse(origin).netloc
                if origin_host != host and origin_host != host.split(":")[0]:
                    # Allow Stripe webhooks
                    if not request.url.path.startswith("/api/stripe/webhook"):
                        from starlette.responses import JSONResponse
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Cross-origin request blocked"}
                        )

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        # Content Security Policy
        # unsafe-inline required for onclick= handlers in JS-generated HTML (~200 instances).
        # Google Fonts added to style-src and font-src.
        # Phase 6 will migrate inline handlers to event delegation enabling nonce-based CSP.
        csp = "; ".join([
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' https://js.stripe.com https://cdnjs.cloudflare.com https://www.instagram.com",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            # Jul 25 2026: allow oEmbed lookups to fetch video thumbnails
            # for Vimeo + TikTok links (Instagram/FB oEmbed requires an
            # app token — not attempted). Client-side thumbnail resolver
            # lives in artist.edit.js / venue.edit.js getVideoThumbnail.
            "connect-src 'self' https://api.stripe.com "
            "https://vimeo.com https://www.tiktok.com",
            # Jul 25 2026: added Instagram / TikTok / Vimeo / Facebook
            # so the lightbox video-embed builder can iframe social posts
            # (reels, shorts, videos). Without these hosts in frame-src
            # the iframe silently fails to load and the modal shows blank.
            "frame-src 'self' https://js.stripe.com "
            "https://www.youtube.com https://youtube.com "
            "https://w.soundcloud.com https://bandcamp.com https://*.bandcamp.com "
            "https://www.instagram.com https://instagram.com "
            "https://www.tiktok.com https://tiktok.com "
            "https://player.vimeo.com "
            "https://www.facebook.com https://web.facebook.com",
            # media-src: allow any https source so artist-uploaded audio_link URLs
            # (direct MP3s on artists' own sites, etc.) play in <audio> elements.
            # Without this the player renders but the browser blocks playback.
            "media-src 'self' blob: https:",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ])
        response.headers["Content-Security-Policy"] = csp
        
        return response

app.add_middleware(SecurityHeadersMiddleware)

class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    """
    When maintenance_mode = 'true' in platform_settings:
    - All API routes return 503 with a JSON message (except /api/admin/* and /health)
    - Static files and the main HTML shell pass through so admin can still log in
    Checked once per request from DB — fast on SQLite with an indexed key lookup.
    """
    async def dispatch(self, request, call_next):
        path = request.url.path
        # Always allow: static files, health check, admin API, login/logout
        if (path.startswith('/app/static/')
                or path.startswith('/api/admin/')
                or path in ('/health', '/robots.txt', '/sitemap.xml', '/sw.js', '/', '/api/maintenance-status')
                or path in ('/api/login', '/api/logout', '/api/me')):
            return await call_next(request)
        # Only block API routes
        if path.startswith('/api/'):
            try:
                import sqlite3, os
                db_path = os.environ.get('GIGSFILL_DB_PATH', '/opt/gigsfill/backend.db')
                conn = sqlite3.connect(db_path, timeout=2)
                row = conn.execute(
                    "SELECT setting_value FROM platform_settings WHERE setting_key = 'maintenance_mode'"
                ).fetchone()
                conn.close()
                if row and str(row[0]).lower() in ('true', '1'):
                    from starlette.responses import JSONResponse
                    return JSONResponse(
                        status_code=503,
                        content={"detail": "GigsFill is temporarily down for maintenance. We'll be back shortly!"}
                    )
            except Exception:
                pass  # If DB unreachable, allow through
        return await call_next(request)

app.add_middleware(MaintenanceModeMiddleware)

# 2026-08-15: enforce email verification at the HTTP layer.
# The frontend already redirects unverified users to /app/verify-email.html
# (see app/static/js/auth.guard.js:102) — but that's client-side only.
# Anyone with a valid session cookie could hit backend endpoints directly
# and mutate the platform before verifying. This middleware is the real
# lock: authenticated + unverified users get 403 on any state-changing
# API call. Read-only calls (GET/HEAD) and the small set of endpoints the
# user actually needs while unverified (view own profile, resend verify,
# change email, delete account, verify link, logout) stay open. Admins
# bypass entirely so they can never be locked out.
class EmailVerificationMiddleware(BaseHTTPMiddleware):
    # Exact paths that stay open while unverified.
    # PUT /api/me handles email-change (there is no separate change-email
    # endpoint — see me.py:76), DELETE /api/me/delete removes the account,
    # POST /api/resend-verification-email refires the verify link, etc.
    # 2026-08-23: added the AUTH-FLOW endpoints — signup / login /
    # forgot-password / reset-password / check-duplicate / request-access.
    # Bug: an unverified user (say, an artist who just signed up but
    # hasn't clicked verify yet) trying to CREATE A DIFFERENT ACCOUNT
    # (a venue) was blocked with EMAIL_NOT_VERIFIED because their session
    # cookie identified them as unverified — but /api/signup creates a
    # NEW user and about to replace the session, and login/forgot are
    # auth-flow endpoints that shouldn't ever care about the current
    # session's verify state. Same rationale for the pre-signup helpers
    # (check-duplicate, validate-city, request-access).
    EXEMPT_EXACT = frozenset({
        '/api/me',
        '/api/me/delete',
        '/api/me/delete-preview',
        '/api/logout',
        '/api/verify-email',
        '/api/resend-verification-email',
        # Auth flow — never gate on the current session's verify state.
        '/api/signup',
        '/api/login',
        '/api/forgot-password',
        '/api/reset-password',
        '/api/check-duplicate',
        '/api/request-access',
    })
    # Prefixes: notification polling has path params (/api/notifications/{id}/read).
    EXEMPT_PREFIX = ('/api/notifications',)

    async def dispatch(self, request, call_next):
        path = request.url.path
        # Non-API traffic and read-only methods bypass entirely — this
        # middleware only blocks WRITES from unverified sessions.
        if not path.startswith('/api/'):
            return await call_next(request)
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return await call_next(request)
        if path in self.EXEMPT_EXACT or any(path.startswith(p) for p in self.EXEMPT_PREFIX):
            return await call_next(request)
        # No session cookie? Downstream handlers will 401 on auth-required
        # endpoints; public POST endpoints (login, signup, forgot-password,
        # reset-password, demo-request) reach their handlers unhindered.
        session_token = request.cookies.get('session_token')
        if not session_token:
            return await call_next(request)
        try:
            from backend.routes.auth import verify_session_token_with_iat
            from backend.db import SessionLocal
            from sqlalchemy import text as _sa_text
            user_id, _ = verify_session_token_with_iat(session_token)
            _db = SessionLocal()
            try:
                row = _db.execute(
                    _sa_text("SELECT is_admin, COALESCE(email_verified,0) AS email_verified FROM users WHERE id = :uid"),
                    {"uid": user_id},
                ).mappings().first()
            finally:
                _db.close()
            if not row:
                return await call_next(request)  # invalid session → let handler 401
            # Admins bypass — canonical check per CLAUDE.md handles both
            # legacy 'true'/'false' TEXT and current '0'/'1' TEXT.
            if str(row.get('is_admin', '')).lower() in ('true', '1'):
                return await call_next(request)
            if not row.get('email_verified'):
                from starlette.responses import JSONResponse
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "EMAIL_NOT_VERIFIED",
                        "message": "Please verify your email address to continue. Check your inbox for the verification link, or request a new one from your profile.",
                    },
                )
        except Exception:
            # Never invent new failure modes here — a bad token or DB blip
            # falls through to the normal handler chain, which handles 401
            # on its own.
            pass
        return await call_next(request)

app.add_middleware(EmailVerificationMiddleware)
# Request-ID — registered LAST so it runs FIRST (Starlette inverts
# middleware order). Every other middleware then sees the contextvar
# populated.
app.add_middleware(RequestIdMiddleware)

# Cache headers for static assets
class StaticCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith('/app/static/'):
            if path.endswith(('.js', '.css', '.woff2', '.woff', '.ttf')):
                response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
            elif path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico')):
                response.headers['Cache-Control'] = 'public, max-age=604800'  # 7 days
        elif path.endswith('.html') or path.startswith('/app/') and '.' not in path.split('/')[-1]:
            response.headers['Cache-Control'] = 'no-cache'  # Always revalidate HTML
        return response

app.add_middleware(StaticCacheMiddleware)

# SEO: Serve robots.txt and sitemap.xml from root
from fastapi.responses import FileResponse as _FileResponse
logger = logging.getLogger("gigsfill.main")
@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    return _FileResponse("app/static/robots.txt", media_type="text/plain")

# /sitemap.xml is served dynamically by backend/routes/vanity.py so it
# always includes the current set of artist/venue/city vanity URLs.

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return _FileResponse("app/static/js/sw.js", media_type="application/javascript",
                         headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


# ─── Health check ────────────────────────────────────────────────────
# Cheap endpoint for uptime monitors (UptimeRobot, Better Uptime,
# DigitalOcean's load balancer, etc). Pings the DB with SELECT 1 so
# we catch the "API up but DB unreachable" failure mode that a TCP
# port check would miss. Returns 200 + JSON on success, 503 + JSON
# on failure so monitors can route on status code OR JSON body.
@app.get("/healthz", include_in_schema=False)
def healthz():
    from starlette.responses import JSONResponse
    try:
        import sqlite3
        # Jul 1 2026 Postgres R1: use get_db_connection so healthz works
        # on both engines. Still skips SQLAlchemy's connection pool so a
        # blocked pool doesn't trip the healthz 5s timeout.
        from backend.db import get_db_connection
        c = get_db_connection()
        c.execute("SELECT 1").fetchone()
        c.close()
        db_ok = True
        db_err = None
    except Exception as e:
        db_ok = False
        db_err = str(e)[:120]
    payload = {
        "ok": db_ok,
        "version": os.environ.get("GIGSFILL_RELEASE", "dev"),
        "db": "ok" if db_ok else "fail",
    }
    if db_err:
        payload["db_error"] = db_err
    return JSONResponse(status_code=200 if db_ok else 503, content=payload)

# Audit fix (May 2026 part 10): block direct browser access to legal-contract
# PDFs. They live under app/static/uploads/contracts/ which would otherwise be
# served by the StaticFiles mount below. The auth-gated download endpoint
# (contracts.download_contract_pdf) is the ONLY supported way to fetch them.
# Filenames are guessable (venue_name + artist_name + gig_date) so leaving the
# directory open was a real legal/privacy risk. Registered BEFORE the mount so
# the route match wins.
@app.get("/app/static/uploads/contracts/{rest:path}", include_in_schema=False)
def _block_direct_contract_pdf_access(rest: str):
    from fastapi import HTTPException as _HE
    raise _HE(403, "Contract PDFs must be downloaded via the authenticated download endpoint.")

# Mount static files
app.mount("/app", StaticFiles(directory="app", html=True), name="app")

@app.get("/")
def root():
    """Redirect to main application"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/app/index.html")


@app.get("/favicon.ico")
def favicon():
    """Serve the brand PNG for the well-known /favicon.ico path. Every page
    already declares <link rel="icon"> pointing at the SVG + PNG, but a lot
    of browsers/tabs/bots still hit /favicon.ico unconditionally regardless
    of the declared link, producing ~10 404s/hour in the access log. Route
    resolves those to the existing small brandmark PNG (104x78, RGBA).
    Browsers accept PNG data at any filename. Added 2026-07-29."""
    from fastapi.responses import FileResponse
    return FileResponse(
        "app/static/img/GIGSFILL-LOGO-BRANDMARK-FULLCOLOR-SMALL.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )

@app.get("/health")
def health():
    """Health check: 200 if DB reachable + critical config loaded, 503 otherwise.

    Cheap enough for uptime monitors (single SELECT 1). Does not call external
    services like Stripe — that would couple our health to theirs.
    """
    from fastapi.responses import JSONResponse
    checks = {}

    try:
        from backend.db import get_db_connection
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            checks["db"] = "ok"
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        checks["db"] = f"error: {type(e).__name__}: {str(e)[:120]}"

    checks["secret_key"] = "ok" if os.environ.get("GIGSFILL_SECRET_KEY") else "missing"

    failed = [k for k, v in checks.items() if v != "ok"]
    if failed:
        return JSONResponse(status_code=503, content={"status": "degraded", "checks": checks, "failed": failed})
    return {"status": "ok", "checks": checks}


@app.get("/api/maintenance-status")
def maintenance_status():
    """Public endpoint — returns current maintenance mode state.
    Always allowed through MaintenanceModeMiddleware (path whitelisted).
    Used by the frontend banner to show/hide the maintenance overlay."""
    try:
        import sqlite3, os as _os
        _db_path = _os.environ.get('GIGSFILL_DB_PATH', '/opt/gigsfill/backend.db')
        _conn = sqlite3.connect(_db_path, timeout=2)
        _row = _conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'maintenance_mode'"
        ).fetchone()
        _msg_row = _conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'maintenance_message'"
        ).fetchone()
        _conn.close()
        active = _row and str(_row[0]).lower() in ('true', '1')
        message = (_msg_row and _msg_row[0]) or "GigsFill is currently undergoing maintenance. We'll be back shortly!"
        return {"maintenance": active, "message": message}
    except Exception:
        return {"maintenance": False, "message": ""}


@app.get("/api/public/texting-status")
def public_texting_status():
    """Public endpoint — returns whether the platform's texting (SMS)
    feature is globally enabled. Defaults to false until admin flips the
    toggle in Platform Settings → Text Settings. Frontend uses this to
    show / hide the SMS Setup UI in user-profile.html and any other
    surface that asks the user about texting. Cached for ~60s on the
    client side; no auth required."""
    try:
        import sqlite3, os as _os
        _db_path = _os.environ.get('GIGSFILL_DB_PATH', '/opt/gigsfill/backend.db')
        _conn = sqlite3.connect(_db_path, timeout=2)
        _row = _conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'texting_enabled'"
        ).fetchone()
        _conn.close()
        enabled = bool(_row and str(_row[0]).lower() in ('true', '1'))
        return {"enabled": enabled}
    except Exception:
        return {"enabled": False}

@app.get("/api/validate-city")
def validate_city(city: str = "", state: str = ""):
    """Check if a city+state exists in our US cities database. Returns matching state if only city provided."""
    from backend.us_cities import find_city, US_CITIES
    if not city:
        return {"valid": False}
    city_trimmed = city.strip()
    # If state provided, do exact match
    if state:
        result = find_city(city_trimmed, state.strip())
        return {"valid": result is not None, "state": state.strip() if result else None}
    # If no state, find the first matching city and return its state
    city_lower = city_trimmed.lower()
    for c in US_CITIES:
        if c["city"].lower() == city_lower:
            return {"valid": True, "state": c["state"]}
    return {"valid": False}

@app.post("/api/gigs/{gig_id}/notes")
def update_gig_notes(gig_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Update just the notes on a gig (works for booked gigs too). Requires auth and venue access."""
    from fastapi import HTTPException
    from sqlalchemy import text
    from backend.utils import check_venue_access
    gig = db.execute(text("SELECT id, venue_id FROM gigs WHERE id = :gid"), {"gid": gig_id}).first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig[1], user.id)
    notes = (data.get("notes") or "").strip()
    db.execute(text("UPDATE gigs SET notes = :notes WHERE id = :gid"), {"notes": notes, "gid": gig_id})
    db.commit()
    return {"ok": True, "notes": notes}

@app.post("/api/support/ticket")
@limiter.limit("2/minute")
def submit_support_ticket(request: Request, data: dict, user=Depends(get_current_user)):
    """Submit a support/help ticket.

    Audit fix (May 2026 part 5): now requires authentication and derives the
    submitter identity from the session — previously the endpoint was anonymous
    and read `user_id`/`user_email`/`user_name` from the request body, allowing
    an unauthenticated caller to file tickets attributed to any user and pivot
    admin replies to any email address.
    """
    from fastapi import HTTPException
    from backend.db import get_db_connection
    from backend.utils import utcnow_naive as _utcnow_naive

    category = data.get("category", "").strip()
    subject = data.get("subject", "").strip()
    description = data.get("description", "").strip()
    # Audit fix (May 2026 part 5): derive identity from the session, ignore body.
    user_id = user.id
    user_email = (getattr(user, "email", "") or "").strip()
    user_name = (f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}").strip() or user_email

    if not category or not subject or not description:
        raise HTTPException(400, "Category, subject, and description are required")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Save ticket to DB
        cursor.execute("""
            INSERT INTO support_tickets (user_id, user_email, user_name, category, subject, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, user_email, user_name, category, subject, description))
        ticket_id = cursor.lastrowid
        conn.commit()

        # Send emails using templates
        try:
            from backend.email_service import EmailService
            from backend.db import SessionLocal as _SL
            _db2 = _SL()
            try:
                _es = EmailService(_db2)
                base_url = _db2.execute(__import__('sqlalchemy').text(
                    "SELECT setting_value FROM platform_settings WHERE setting_key='site_url'"
                )).scalar() or "https://gigsfill.com"
                admin_url = f"{base_url}/app/admin.html"
                reply_url = f"{base_url}/api/support/ticket/{ticket_id}"
                # Audit fix (May 2026 part 5): use the canonical utils.utcnow_naive
                # — the previous `__import__('datetime').utcnow_naive()` raised
                # AttributeError (datetime module has no utcnow_naive), the
                # exception was swallowed by the surrounding try/except, and
                # every ticket-confirmation email silently never sent.
                submitted_at = _utcnow_naive().strftime("%B %d, %Y at %I:%M %p UTC")
                ticket_vars = {
                    "ticket_id": str(ticket_id),
                    "user_name": user_name or "User",
                    "user_email": user_email or "",
                    "category": category,
                    "subject": subject,
                    "description": description.replace("\n", "<br>"),
                    "submitted_at": submitted_at,
                    "admin_url": admin_url,
                    "reply_url": reply_url,
                }
                # Send confirmation to user
                if user_email:
                    _es.send_notification_email(user_email, user_id, "support_ticket_received", ticket_vars)
                # Send notification to admin (support_email or platform_email)
                admin_email = _db2.execute(__import__('sqlalchemy').text(
                    "SELECT setting_value FROM platform_settings WHERE setting_key='support_email'"
                )).scalar()
                if not admin_email:
                    admin_email = _db2.execute(__import__('sqlalchemy').text(
                        "SELECT setting_value FROM platform_settings WHERE setting_key='platform_email'"
                    )).scalar()
                if admin_email:
                    _es.send_notification_email(admin_email, None, "support_ticket_admin_notification", ticket_vars)
            finally:
                _db2.close()
        except Exception as e:
            logger.error(f"Support ticket email failed: {e}")
        
        return {"ok": True, "ticket_id": ticket_id}
    finally:
        conn.close()


# ============================================
# SUPPORT TICKET — USER-FACING (TOKEN AUTH)
# ============================================

def _support_token(ticket_id, user_email):
    """Generate a secure token for ticket access (no login required)"""
    import hmac, hashlib
    from backend.routes.auth import _SECRET_KEY
    msg = f"support-{ticket_id}-{(user_email or '').lower().strip()}"
    return hmac.new(_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]


def _validate_support_token(ticket, supplied_token):
    """Validate the support-ticket access token AND enforce a 30-day TTL.

    Audit fix (May 2026): tokens previously had no expiry encoded — a leaked
    email or shared inbox archive granted permanent ticket access. We bound
    the leak window to 30 days from ticket creation (`ticket[7]` = created_at).
    Older tickets need a fresh login flow.

    Raises HTTPException on any failure; returns None on success.
    """
    from fastapi import HTTPException
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    expected = _support_token(ticket[0], ticket[1])
    if not supplied_token or supplied_token != expected:
        raise HTTPException(403, "Invalid or expired link")
    try:
        from datetime import datetime, timedelta
        created = ticket[7]
        if isinstance(created, str):
            created_dt = datetime.fromisoformat(created.replace('Z', '').split('.')[0])
        else:
            created_dt = created
        if created_dt and (datetime.utcnow() - created_dt) > timedelta(days=30):
            raise HTTPException(403, "TICKET_TOKEN_EXPIRED: Support links expire after 30 days. Please open a new ticket or log in.")
    except HTTPException:
        raise
    except Exception:
        # Malformed created_at — fall through; allow access rather than 500.
        pass


@app.get("/api/support/ticket/{ticket_id}")
def get_user_ticket(ticket_id: int, token: str = ""):
    """User views their support ticket + replies (token-authenticated from email link)"""
    from fastapi import HTTPException
    from backend.db import get_db_connection
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        ticket = cursor.execute(
            "SELECT id, user_email, user_name, category, subject, description, status, created_at FROM support_tickets WHERE id = ?",
            (ticket_id,)
        ).fetchone()
        
        _validate_support_token(ticket, token)
        
        replies = cursor.execute(
            "SELECT id, sender_type, sender_name, body, created_at FROM support_ticket_replies WHERE ticket_id = ? ORDER BY created_at ASC",
            (ticket_id,)
        ).fetchall()
        
        return {
            "ticket": {
                "id": ticket[0], "user_email": ticket[1], "user_name": ticket[2],
                "category": ticket[3], "subject": ticket[4], "description": ticket[5],
                "status": ticket[6], "created_at": ticket[7]
            },
            "replies": [
                {"id": r[0], "sender_type": r[1], "sender_name": r[2], "body": r[3], "created_at": r[4]}
                for r in replies
            ]
        }
    finally:
        conn.close()


@app.post("/api/support/ticket/{ticket_id}/reply")
@limiter.limit("5/minute")
def user_reply_to_ticket(ticket_id: int, request: Request, data: dict, token: str = ""):
    """User replies to their support ticket (token-authenticated)"""
    from fastapi import HTTPException
    from backend.db import get_db_connection
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    body = (data.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "Reply body is required")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        ticket = cursor.execute(
            "SELECT id, user_email, user_name, subject, category, description FROM support_tickets WHERE id = ?",
            (ticket_id,)
        ).fetchone()
        
        _validate_support_token(ticket, token)
        
        user_email = ticket[1] or ''
        user_name = ticket[2] or ''
        ticket_subject = ticket[3] or ''
        
        # Save reply
        cursor.execute(
            "INSERT INTO support_ticket_replies (ticket_id, sender_type, sender_name, sender_email, body) VALUES (?, 'user', ?, ?, ?)",
            (ticket_id, user_name, user_email, body)
        )
        
        # Auto-reopen if closed
        cursor.execute("UPDATE support_tickets SET status = 'open' WHERE id = ? AND status = 'closed'", (ticket_id,))
        conn.commit()
        
        # Email the admin/support
        email_sent = False
        try:
            settings = {r[0]: r[1] for r in cursor.execute(
                "SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_email', 'platform_email_password', 'platform_smtp_server', 'platform_smtp_port', 'support_email')"
            ).fetchall()}
            
            smtp_email = settings.get('platform_email', '')
            smtp_password = settings.get('platform_email_password', '')
            smtp_server = settings.get('platform_smtp_server', 'smtp.gmail.com')
            smtp_port = int(settings.get('platform_smtp_port', '587'))
            admin_email = settings.get('support_email', smtp_email)
            
            if smtp_email and smtp_password and admin_email:
                # Get previous replies for thread
                prev = cursor.execute(
                    "SELECT sender_type, sender_name, body, created_at FROM support_ticket_replies WHERE ticket_id = ? ORDER BY created_at ASC",
                    (ticket_id,)
                ).fetchall()
                
                thread_html = ""
                # Skip the last one (it's the one we just inserted)
                for r in prev[:-1]:
                    r_type, r_name, r_body, r_date = r
                    r_label = 'Support' if r_type == 'admin' else user_name or 'User'
                    r_color = '#e0f2fe' if r_type == 'admin' else '#f3f4f6'
                    thread_html += f"""
                    <div style="background:{r_color};border-radius:6px;padding:12px 16px;margin-bottom:8px;">
                      <div style="font-size:11px;color:#6b7280;margin-bottom:4px;"><strong>{r_label}</strong> &middot; {r_date or ''}</div>
                      <div style="font-size:13px;color:#374151;line-height:1.5;">{(r_body or '').replace(chr(10), '<br>')}</div>
                    </div>"""
                
                body_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" style="background:#f8f9fa;padding:40px 20px;">
<tr><td>
<table role="presentation" width="100%" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:24px 32px 16px;border-bottom:1px solid #eee;">
<span style="font-size:18px;font-weight:700;letter-spacing:0.15em;color:#1a1a2e;">GIGSFILL</span>
<span style="font-size:13px;color:#6b7280;margin-left:12px;">Support Ticket #{ticket_id} — User Reply</span>
</td></tr>
<tr><td style="padding:24px 32px;">
<p style="font-size:14px;color:#374151;margin:0 0 4px;"><strong>{user_name or 'User'}</strong> ({user_email}) replied:</p>
<div style="background:#f3f4f6;border-radius:6px;padding:16px;margin:16px 0;font-size:14px;line-height:1.6;color:#1e293b;">
{body.replace(chr(10), '<br>')}
</div>
{('<hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0 16px;"><p style="font-size:12px;color:#9ca3af;margin:0 0 8px;">Previous messages:</p>' + thread_html) if thread_html else ''}
</td></tr>
<tr><td style="padding:16px 32px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:11px;text-align:center;">Reply from the Admin panel &middot; &copy; 2026 GigsFill</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""
                
                msg = MIMEMultipart('alternative')
                msg['From'] = smtp_email
                msg['To'] = admin_email
                msg['Subject'] = f"Re: [GigsFill Support #{ticket_id}] {ticket_subject}"
                msg['Reply-To'] = user_email
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
                email_sent = True
        except Exception as e:
            logger.error(f"User reply email to admin failed: {e}")
        
        return {"ok": True, "email_sent": email_sent}
    finally:
        conn.close()

@app.post("/api/recommend")
def recommend_gigsfill(data: dict):
    """Send a recommendation email to a friend"""
    from fastapi import HTTPException
    from backend.db import get_db_connection
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    recipient_email = data.get("recipient_email", "").strip().lower()
    recipient_name = data.get("recipient_name", "").strip()
    message = data.get("message", "").strip()
    user_id = data.get("user_id")
    user_name = data.get("user_name", "")
    
    if not recipient_email or '@' not in recipient_email:
        raise HTTPException(400, "Valid email address is required")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Save recommendation to DB
        cursor.execute("""
            INSERT INTO recommendations (user_id, user_name, recipient_email, recipient_name, message)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, user_name, recipient_email, recipient_name, message))
        conn.commit()
        
        # Send recommendation email
        try:
            cursor.execute("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_email', 'platform_email_password', 'platform_smtp_server', 'platform_smtp_port')")
            settings = {row[0]: row[1] for row in cursor.fetchall()}
            
            smtp_email = settings.get('platform_email', '')
            smtp_password = settings.get('platform_email_password', '')
            smtp_server = settings.get('platform_smtp_server', 'smtp.gmail.com')
            smtp_port = int(settings.get('platform_smtp_port', '587'))
            
            if smtp_email and smtp_password:
                msg = MIMEMultipart('alternative')
                msg['From'] = smtp_email
                msg['To'] = recipient_email
                msg['Subject'] = f"{user_name} thinks you should check out GigsFill!"
                
                personal_note = ""
                if message:
                    personal_note = f"""<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:16px;margin-bottom:24px;font-size:14px;line-height:1.6;color:#374151;font-style:italic;">
"{message}"
<div style="margin-top:8px;font-style:normal;font-weight:500;color:#0369a1;">— {user_name}</div>
</div>"""
                
                greeting = f"Hi{' ' + recipient_name if recipient_name else ''}!"
                
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
<h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#1a1a2e;">You've been recommended! 🎶</h1>
<p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#4b5563;">{greeting} <strong>{user_name}</strong> is using GigsFill and thought you'd love it too.</p>
{personal_note}
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#4b5563;">GigsFill connects <strong>musicians</strong> with <strong>venues</strong> to make booking gigs simple, fast, and hassle-free. Whether you're an artist looking for your next gig or a venue searching for the perfect act — GigsFill has you covered.</p>
<div style="text-align:center;margin:32px 0;">
<a href="https://gigsfill.com" style="display:inline-block;background:#06b6d4;color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;">Check Out GigsFill</a>
</div>
<p style="margin:0;font-size:13px;color:#9ca3af;text-align:center;">Free to sign up &middot; No commitment required</p>
</td></tr>
<tr><td style="padding:24px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color:#1a1a2e;text-decoration:none;">gigsfill.com</a></p>
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
        except Exception as e:
            logger.error(f"Email send failed: {e}")
        
        return {"ok": True, "message": "Recommendation sent!"}
    finally:
        conn.close()


# ============================
# ARTIST INVITATIONS (Venue invites artists to join GigsFill)
# ============================
#
# The legacy single-venue POST /api/venues/{venue_id}/invite-artists was
# removed in part 10p (2026-06-14). The canonical path is now
# POST /api/me/invite-artists in routes/me.py, which writes one row per
# (email, venue) pair sharing a token+invite_group_id, supports any number
# of venues per invite, captures synchronous SMTP bounces, and ties signup
# back via the token. The GET / resend / delete endpoints below remain —
# they back the per-venue tracker UI inside Email Center which works for
# both legacy (single-venue, no token) and current (multi-venue, tokened) rows.

@app.post("/api/venues/{venue_id}/invite-artists")
def invite_artists_legacy_removed(venue_id: int):
    """REMOVED in part 10p (2026-06-14). Use POST /api/me/invite-artists instead.

    Returns 410 Gone so any old client tooling or scripts get a clear signal to
    migrate. The new canonical endpoint accepts {emails, venue_ids, message?}
    and supports multi-venue invitations with token-based signup tracking,
    bounce capture, and the post-signup preferred-status popup.
    """
    raise HTTPException(410, "This endpoint was replaced by POST /api/me/invite-artists. See gigsfill-claude-doc.md changelog part 10p.")


def _UNUSED_legacy_invite_artists(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """REMOVED — body deleted; kept as a private stub so any python import that
    referenced the symbol won't ImportError. No callers exist."""
    raise NotImplementedError("Removed in part 10p — use POST /api/me/invite-artists")
    # Original body (~170 lines) removed; see git history before part 10p.


@app.get("/api/venues/{venue_id}/invitations")
def get_venue_invitations(venue_id: int):
    """Get all artist invitations for a venue with auto-detected signup status"""
    from backend.db import get_db_connection
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Ensure table exists (safety net)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS artist_invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venue_id INTEGER NOT NULL,
                venue_name TEXT NOT NULL,
                invited_email TEXT NOT NULL,
                invited_by_user_id INTEGER NOT NULL,
                inviter_name TEXT,
                message TEXT,
                status TEXT DEFAULT 'pending',
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                signed_up_at TIMESTAMP,
                signed_up_user_id INTEGER,
                resent_count INTEGER DEFAULT 0,
                last_resent_at TIMESTAMP,
                FOREIGN KEY (venue_id) REFERENCES venues(id),
                FOREIGN KEY (invited_by_user_id) REFERENCES users(id)
            )
        """)
        conn.commit()
        
        # Auto-detect signups: check if any pending invitations match new users
        cursor.execute("""
            UPDATE artist_invitations 
            SET status = 'signed_up', 
                signed_up_user_id = (SELECT u.id FROM users u WHERE LOWER(u.email) = LOWER(artist_invitations.invited_email) LIMIT 1),
                signed_up_at = CURRENT_TIMESTAMP
            WHERE venue_id = ? AND status = 'pending'
              AND EXISTS (SELECT 1 FROM users u WHERE LOWER(u.email) = LOWER(artist_invitations.invited_email))
        """, (venue_id,))
        if cursor.rowcount > 0:
            conn.commit()
        
        cursor.execute("""
            SELECT id, invited_email, inviter_name, message, status, 
                   sent_at, signed_up_at, resent_count, last_resent_at
            FROM artist_invitations 
            WHERE venue_id = ? AND status != 'deleted'
            ORDER BY sent_at DESC
        """, (venue_id,))
        
        rows = cursor.fetchall()
        invitations = []
        for r in rows:
            invitations.append({
                "id": r[0], "email": r[1], "inviter_name": r[2],
                "message": r[3], "status": r[4], "sent_at": r[5],
                "signed_up_at": r[6], "resent_count": r[7], "last_resent_at": r[8]
            })
        
        pending = sum(1 for i in invitations if i["status"] == "pending")
        signed_up = sum(1 for i in invitations if i["status"] == "signed_up")
        
        return {"invitations": invitations, "pending": pending, "signed_up": signed_up, "total": len(invitations)}
    finally:
        conn.close()


@app.post("/api/venues/{venue_id}/resend-invitation/{invitation_id}")
def resend_invitation(venue_id: int, invitation_id: int):
    """Resend an artist invitation email"""
    from fastapi import HTTPException
    from backend.db import get_db_connection
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT ai.invited_email, ai.venue_name, ai.inviter_name, ai.message,
                   v.city, v.state
            FROM artist_invitations ai
            JOIN venues v ON v.id = ai.venue_id
            WHERE ai.id = ? AND ai.venue_id = ? AND ai.status = 'pending'
        """, (invitation_id, venue_id))
        inv = cursor.fetchone()
        if not inv:
            raise HTTPException(404, "Invitation not found or already signed up")
        
        email, venue_name, inviter_name, message, city, state = inv
        location = f"{city}, {state}" if city else ""
        sender_display = venue_name
        
        # Get SMTP settings
        cursor.execute("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_email', 'platform_email_password', 'platform_smtp_server', 'platform_smtp_port')")
        settings = {row[0]: row[1] for row in cursor.fetchall()}
        smtp_email = settings.get('platform_email', '')
        smtp_password = settings.get('platform_email_password', '')
        smtp_server = settings.get('platform_smtp_server', 'smtp.gmail.com')
        smtp_port = int(settings.get('platform_smtp_port', '587'))
        
        if smtp_email and smtp_password:
            msg = MIMEMultipart('alternative')
            msg['From'] = smtp_email
            msg['To'] = email
            msg['Subject'] = f"Reminder: {sender_display} invited you to join GigsFill!"
            
            personal_note = ""
            if message:
                personal_note = f'''<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:16px;margin-bottom:24px;font-size:14px;line-height:1.6;color:#374151;font-style:italic;">
"{message}"
<div style="margin-top:8px;font-style:normal;font-weight:500;color:#0369a1;">— {sender_display}</div>
</div>'''
            
            location_line = f' in {location}' if location else ''
            
            body_html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" style="background:#f8f9fa;padding:40px 20px;">
<tr><td>
<table role="presentation" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<span style="font-size:18px;font-weight:700;letter-spacing:0.15em;color:#1a1a2e;">GIGSFILL</span>
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#1a1a2e;">Friendly Reminder 🎶</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#4b5563;"><strong>{venue_name}</strong>{location_line} is using <strong>GigsFill</strong> for all their gig booking. Don't miss out — create your free artist account to get booked, request preferred status, and start landing gigs.</p>
{personal_note}
<div style="text-align:center;margin:32px 0;">
<a href="https://gigsfill.com/app/signup-new.html" style="display:inline-block;background:#06b6d4;color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;">Create Your Free Artist Account</a>
</div>
<p style="margin:0;font-size:13px;color:#9ca3af;text-align:center;">Free to sign up &middot; No commitment required</p>
</td></tr>
<tr><td style="padding:24px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color:#1a1a2e;text-decoration:none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr></table>
</body></html>'''
            
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
        
        # Update resend count
        cursor.execute("""
            UPDATE artist_invitations SET resent_count = resent_count + 1, last_resent_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (invitation_id,))
        conn.commit()
        
        return {"ok": True, "message": "Reminder sent!"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(500, "Failed to resend invitation")
    finally:
        conn.close()

@app.delete("/api/venues/{venue_id}/invitations/{invitation_id}")
def delete_venue_invitation(venue_id: int, invitation_id: int, current_user=Depends(get_current_user)):
    """Delete (cancel) a pending artist invitation.

    Audit fix (Jul 2026): endpoint had ZERO authorization check — any
    authenticated user could cancel any venue's pending invitations
    just by knowing the ids. Now requires venue access.
    """
    from backend.db import get_db_connection
    from backend.utils import check_venue_access
    from backend.db import get_db

    # Access check uses the SQLAlchemy session (`get_db`) that
    # check_venue_access expects. We keep the raw sqlite conn below for
    # the UPDATE so the existing flow is undisturbed.
    _db_gen = get_db()
    _db_sess = next(_db_gen)
    try:
        check_venue_access(_db_sess, venue_id, current_user.id)
    finally:
        try:
            next(_db_gen, None)
        except Exception:
            pass

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Verify invitation belongs to this venue and is still pending
        cursor.execute(
            "SELECT id, status FROM artist_invitations WHERE id = ? AND venue_id = ?",
            (invitation_id, venue_id)
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Invitation not found")
        if row[1] == 'signed_up':
            raise HTTPException(400, "Cannot delete an invitation that has already been accepted")

        cursor.execute(
            "UPDATE artist_invitations SET status = 'deleted' WHERE id = ? AND venue_id = ?",
            (invitation_id, venue_id)
        )
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Failed to delete invitation")
    finally:
        conn.close()


@app.post("/api/check-duplicate")
@limiter.limit("10/minute")
def check_duplicate(request: Request, data: dict):
    """Check if an artist/venue name already exists in the same city+state"""
    from fastapi import HTTPException
    from backend.db import get_db_connection
    
    entity_type = data.get("type", "").strip()  # "artist" or "venue"
    name = data.get("name", "").strip()
    city = data.get("city", "").strip()
    state = data.get("state", "").strip()
    
    if not entity_type or not name or not city or not state:
        return {"duplicate": False}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        if entity_type == "artist":
            cursor.execute("""
                SELECT a.id, a.name, a.city, a.state, a.user_id, u.email as owner_email,
                       u.first_name || ' ' || u.last_name as owner_name
                FROM artists a
                JOIN users u ON a.user_id = u.id
                WHERE LOWER(a.name) = LOWER(?) AND LOWER(a.city) = LOWER(?) AND UPPER(a.state) = UPPER(?)
                LIMIT 1
            """, (name, city, state))
        else:
            cursor.execute("""
                SELECT v.id, v.venue_name as name, v.city, v.state, v.user_id, u.email as owner_email,
                       u.first_name || ' ' || u.last_name as owner_name
                FROM venues v
                JOIN users u ON v.user_id = u.id
                WHERE LOWER(v.venue_name) = LOWER(?) AND LOWER(v.city) = LOWER(?) AND UPPER(v.state) = UPPER(?)
                LIMIT 1
            """, (name, city, state))
        
        row = cursor.fetchone()
        if row:
            return {
                "duplicate": True,
                "entity_id": row[0],
                "name": row[1],
                "city": row[2],
                "state": row[3],
                "owner_id": row[4],
                "type": entity_type
            }
        
        return {"duplicate": False}
    finally:
        conn.close()

@app.post("/api/request-access")
@limiter.limit("3/minute")
def request_access(request: Request, data: dict):
    """Request access to an existing artist/venue profile - emails the owner"""
    from fastapi import HTTPException
    from backend.db import get_db_connection
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    entity_type = data.get("type", "")
    entity_id = data.get("entity_id")
    requester_name = data.get("requester_name", "")
    requester_email = data.get("requester_email", "")
    
    if not entity_type or not entity_id or not requester_email:
        raise HTTPException(400, "Missing required fields")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get entity and owner info
        if entity_type == "artist":
            cursor.execute("""
                SELECT a.name, a.city, a.state, u.email, u.first_name
                FROM artists a JOIN users u ON a.user_id = u.id
                WHERE a.id = ?
            """, (entity_id,))
        else:
            cursor.execute("""
                SELECT v.venue_name, v.city, v.state, u.email, u.first_name
                FROM venues v JOIN users u ON v.user_id = u.id
                WHERE v.id = ?
            """, (entity_id,))
        
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Entity not found")
        
        entity_name, entity_city, entity_state, owner_email, owner_first = row
        
        # Send email to owner
        try:
            cursor.execute("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_email', 'platform_email_password', 'platform_smtp_server', 'platform_smtp_port')")
            settings = {r[0]: r[1] for r in cursor.fetchall()}
            
            smtp_email = settings.get('platform_email', '')
            smtp_password = settings.get('platform_email_password', '')
            smtp_server = settings.get('platform_smtp_server', 'smtp.gmail.com')
            smtp_port = int(settings.get('platform_smtp_port', '587'))
            
            if smtp_email and smtp_password:
                msg = MIMEMultipart('alternative')
                msg['From'] = smtp_email
                msg['To'] = owner_email
                msg['Subject'] = f"Access Request for {entity_name} on GigsFill"
                
                type_label = entity_type.capitalize()
                
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
<h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#1a1a2e;">Profile Access Request</h1>
<p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#4b5563;">Hi {owner_first},</p>
<p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#4b5563;"><strong>{requester_name}</strong> ({requester_email}) tried to create a new {type_label.lower()} account and found that <strong>{entity_name}</strong> in {entity_city}, {entity_state} already exists on GigsFill.</p>
<p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#4b5563;">They are requesting permission to access this {type_label.lower()} profile. If you know this person, you can grant them access from your profile settings by inviting them using their email address:</p>
<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:16px;margin-bottom:24px;text-align:center;">
<span style="font-size:15px;font-weight:600;color:#0369a1;">{requester_email}</span>
</div>
<p style="margin:0;font-size:13px;color:#9ca3af;">If you don't recognize this person, you can safely ignore this email.</p>
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
        except Exception as e:
            logger.error(f"Email send failed: {e}")
        
        return {"ok": True, "message": "Access request sent to the profile owner."}
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# Vanity URL resolver — registered LAST so the catch-all `GET /{slug}` route
# doesn't shadow any other top-level path defined above (/health, /, /api/...,
# /robots.txt, /sitemap.xml, /sw.js, etc). The resolver's own reserved-words
# denylist is a defense-in-depth backup; this ordering is the primary guard.
# ────────────────────────────────────────────────────────────────────────────
app.include_router(vanity.router)
app.include_router(unsubscribe.router)
app.include_router(demo_requests.router)
app.include_router(contact.router)
app.include_router(artist_external_gigs.router)
