#!/usr/bin/env bash
# ============================================================
# GigsFill — Fix for 1GB RAM Droplet
# ============================================================
# Run this on your server if you're on a 1GB / 1 CPU droplet:
#   bash scripts/fix_1gb_droplet.sh
#
# Sets workers to 2 (safe for 1GB), adds swap space

set -euo pipefail
APP_DIR="/opt/gigsfill"

echo "▶ Configuring for 1GB RAM / 1 CPU droplet..."

# Fix worker count in .env
if [ -f "${APP_DIR}/.env" ]; then
    sed -i 's/^WEB_CONCURRENCY=.*/WEB_CONCURRENCY=2/' "${APP_DIR}/.env"
    echo "✅ Set WEB_CONCURRENCY=2"
else
    echo "WEB_CONCURRENCY=2" >> "${APP_DIR}/.env"
    echo "✅ Added WEB_CONCURRENCY=2 to .env"
fi

# Add 1GB swap file (critical for 1GB RAM machines — prevents OOM kills)
if [ ! -f /swapfile ]; then
    echo "▶ Creating 1GB swap file (prevents out-of-memory crashes)..."
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    # Make permanent
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "✅ Swap file created and enabled"
else
    echo "✅ Swap file already exists"
fi

# Tune Redis to use less memory on small machines
if [ -f /etc/redis/redis.conf ]; then
    # Limit Redis to 64MB max (it caches rate limit data only)
    grep -q "^maxmemory " /etc/redis/redis.conf || echo "maxmemory 64mb" >> /etc/redis/redis.conf
    grep -q "^maxmemory-policy" /etc/redis/redis.conf || echo "maxmemory-policy allkeys-lru" >> /etc/redis/redis.conf
    systemctl restart redis-server
    echo "✅ Redis memory capped at 64MB"
fi

# Restart the app with new worker count
systemctl restart gigsfill
sleep 2
systemctl status gigsfill --no-pager | head -5

echo ""
echo "✅ Done. Running with 2 workers on 1GB RAM."
echo ""
echo "Memory budget:"
echo "  2x uvicorn workers: ~200-300MB"
echo "  Redis:              ~30-64MB"
echo "  OS + misc:          ~150-200MB"
echo "  1GB swap (overflow):  available if needed"
echo ""
echo "IMPORTANT: For production with real users, consider upgrading:"
echo "  Droplet resize: \$6/mo → \$12/mo (2GB RAM)"
echo "  + DO Managed Postgres: \$15/mo"
echo "  Total: \$27/mo — handles 1000 venues + 20000 artists"
