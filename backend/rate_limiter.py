"""
GigsFill Rate Limiter
=====================
Uses slowapi backed by Redis (preferred) or in-memory (fallback).

Set env var in production:
    RATELIMIT_STORAGE_URI=redis://localhost:6379

If Redis is not reachable, automatically falls back to in-memory with a warning.

Runtime config
--------------
Each named limit is exposed as a *callable* (e.g. `rate_login_limit()`), which
slowapi re-evaluates on every request. Callables read from `platform_settings`
(keys `rate_login`, `rate_signup`, etc.) so admins can change limits from the
Admin Platform Settings UI without restarting the API. A 30s in-memory TTL
cache avoids hammering the DB on hot paths. If a setting is missing or
malformed, the callable falls back to the hardcoded default below.

Each setting stores an integer "requests per minute" (e.g. "5" means 5/minute).
"""

import os
import time
import logging
import threading

logger = logging.getLogger("gigsfill.rate_limiter")

from slowapi import Limiter
from slowapi.util import get_remote_address

_storage_uri = os.environ.get("RATELIMIT_STORAGE_URI", "redis://localhost:6379")

# Try Redis first; fall back to memory if unavailable
try:
    import redis as _redis
    _r = _redis.from_url(_storage_uri, socket_connect_timeout=2)
    _r.ping()
    logger.info(f"✅ Rate limiter using Redis: {_storage_uri}")
except Exception as _re:
    logger.warning(
        f"⚠️  Redis not reachable ({_re}). "
        "Rate limiter falling back to in-memory storage. "
        "Run: apt install redis-server && systemctl enable --now redis"
    )
    _storage_uri = "memory://"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/minute"],
    storage_uri=_storage_uri,
)

# Hardcoded fallback defaults — used if the DB row is missing or malformed.
# Keep these in sync with backend/db.py:default_settings.
_DEFAULTS = {
    "rate_login":          5,
    "rate_signup":         3,
    "rate_password_reset": 3,
    "rate_support":        2,
    "rate_email_send":     10,
    "rate_aff_track":      30,
}

# 30s TTL cache: avoids hammering platform_settings on every request, but is
# short enough that admin changes show up almost immediately.
_CACHE_TTL_S = 30
_cache_value = {}     # key -> int (per-minute)
_cache_expires_at = 0.0
_cache_lock = threading.Lock()


def _refresh_cache():
    """Reload all rate-limit values from platform_settings. Called when the
    TTL expires. Safe to call from multiple threads — last writer wins, no
    serialization needed beyond the lock."""
    global _cache_value, _cache_expires_at
    try:
        from backend.db import SessionLocal
        from sqlalchemy import text as _text
        db = SessionLocal()
        try:
            rows = db.execute(_text(
                "SELECT setting_key, setting_value FROM platform_settings "
                "WHERE setting_key IN ('rate_login','rate_signup','rate_password_reset','rate_support','rate_email_send','rate_aff_track')"
            )).fetchall()
            new_values = {}
            for k, v in rows:
                try:
                    n = int(str(v).strip())
                    if n > 0:
                        new_values[k] = n
                except (TypeError, ValueError):
                    continue
            # Fill any missing keys from defaults so callers always get a value
            for k, dv in _DEFAULTS.items():
                new_values.setdefault(k, dv)
            _cache_value = new_values
            _cache_expires_at = time.time() + _CACHE_TTL_S
        finally:
            db.close()
    except Exception as _e:
        logger.warning(f"rate_limiter cache refresh failed, using defaults: {_e}")
        _cache_value = dict(_DEFAULTS)
        _cache_expires_at = time.time() + _CACHE_TTL_S


def _get(key: str) -> str:
    """Return the current limit string for `key` (e.g. '5/minute')."""
    global _cache_expires_at
    now = time.time()
    if now >= _cache_expires_at:
        with _cache_lock:
            # Double-checked locking — another thread may have refreshed already
            if now >= _cache_expires_at:
                _refresh_cache()
    n = _cache_value.get(key, _DEFAULTS.get(key, 5))
    return f"{n}/minute"


def invalidate_cache():
    """Called by the admin /api/admin/settings PUT after saving rate-limit
    changes so the next request reads fresh values immediately instead of
    waiting up to 30s for the TTL to expire."""
    global _cache_expires_at
    _cache_expires_at = 0.0


# Callable getters — pass these directly to @limiter.limit(...). slowapi
# re-evaluates the callable on each request, so changing platform_settings
# rows immediately affects subsequent requests (after the cache expires or
# invalidate_cache() is called).
def rate_login_limit():          return _get("rate_login")
def rate_signup_limit():         return _get("rate_signup")
def rate_password_reset_limit(): return _get("rate_password_reset")
def rate_support_limit():        return _get("rate_support")
def rate_email_send_limit():     return _get("rate_email_send")
def rate_aff_track_limit():      return _get("rate_aff_track")


# Backwards-compat string constants — still work for any decorator that
# imports them as strings, but won't pick up runtime changes. Prefer the
# callables above for any new code.
RATE_LOGIN          = f"{_DEFAULTS['rate_login']}/minute"
RATE_SIGNUP         = f"{_DEFAULTS['rate_signup']}/minute"
RATE_PASSWORD_RESET = f"{_DEFAULTS['rate_password_reset']}/minute"
RATE_SUPPORT        = f"{_DEFAULTS['rate_support']}/minute"
RATE_EMAIL_SEND     = f"{_DEFAULTS['rate_email_send']}/minute"
RATE_AFF_TRACK      = f"{_DEFAULTS['rate_aff_track']}/minute"
