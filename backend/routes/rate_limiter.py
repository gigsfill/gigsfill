"""
Rate Limiting for GigsFill API
===============================
Centralized rate limiter configuration using slowapi.
Import `limiter` in any route module to apply rate limits.
"""

import os
from slowapi import Limiter
from slowapi.util import get_remote_address

# Use real client IP when behind a reverse proxy (Nginx, Cloudflare, etc.)
# Falls back to direct IP for development
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/minute"],  # Global default: generous for normal use
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
)

# Named rate limit strings for easy reference
RATE_LOGIN = "5/minute"         # Login attempts
RATE_SIGNUP = "3/minute"        # Account creation
RATE_PASSWORD_RESET = "3/minute"  # Password reset requests
RATE_SUPPORT = "2/minute"       # Support ticket submissions
RATE_EMAIL_SEND = "10/minute"   # Email-sending actions
