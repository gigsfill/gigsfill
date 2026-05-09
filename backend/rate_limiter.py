"""
GigsFill Rate Limiter
=====================
Uses slowapi backed by Redis (preferred) or in-memory (fallback).

Set env var in production:
    RATELIMIT_STORAGE_URI=redis://localhost:6379

If Redis is not reachable, automatically falls back to in-memory with a warning.
"""

import os
import logging

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

# Named rate limit constants
RATE_LOGIN          = "5/minute"
RATE_SIGNUP         = "3/minute"
RATE_PASSWORD_RESET = "3/minute"
RATE_SUPPORT        = "2/minute"
RATE_EMAIL_SEND     = "10/minute"
