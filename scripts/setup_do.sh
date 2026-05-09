#!/usr/bin/env bash
# ============================================================
# GigsFill — DigitalOcean Scale-Up Setup Script
# ============================================================
# Run this ONCE on your DigitalOcean droplet as root:
#   bash setup_do.sh
#
# What it does:
#   1. Installs Redis + PostgreSQL client tools
#   2. Installs Python dependencies
#   3. Creates PostgreSQL database (if using managed DO Postgres)
#   4. Installs systemd service with 4 workers
#   5. Configures environment variables
#
# Prerequisites:
#   - Ubuntu 24.04 droplet
#   - GigsFill deployed to /opt/gigsfill
#   - If using DO Managed Postgres: have your connection string ready

set -euo pipefail

APP_DIR="/opt/gigsfill"
SERVICE_USER="www-data"
VENV="${APP_DIR}/venv"

echo "=============================================="
echo " GigsFill Scale-Up Setup"
echo "=============================================="

# ── 1. System packages ──────────────────────────────────────
echo ""
echo "▶ Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    redis-server \
    postgresql-client \
    python3-venv \
    python3-pip \
    build-essential \
    libpq-dev         # required for psycopg2-binary to compile

# Enable and start Redis
systemctl enable redis-server
systemctl start redis-server
redis-cli ping && echo "✅ Redis is running" || echo "⚠️  Redis ping failed"

# ── 2. Python environment ───────────────────────────────────
echo ""
echo "▶ Setting up Python virtual environment..."
cd "${APP_DIR}"
python3 -m venv "${VENV}" --upgrade
"${VENV}/bin/pip" install --upgrade pip -q
"${VENV}/bin/pip" install -r requirements.txt -q
echo "✅ Python dependencies installed"

# ── 3. Environment config ───────────────────────────────────
echo ""
echo "▶ Configuring environment variables..."

ENV_FILE="${APP_DIR}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    cat > "${ENV_FILE}" << 'ENVEOF'
# GigsFill Environment Configuration
# ====================================
# REQUIRED: Set DATABASE_URL to your PostgreSQL connection string.
# Leave blank to keep using SQLite (safe fallback during transition).
#
# DigitalOcean Managed PostgreSQL example:
#   DATABASE_URL=postgresql://doadmin:PASSWORD@db-postgresql-xxx.db.ondigitalocean.com:25060/gigsfill?sslmode=require
#
# Self-hosted PostgreSQL example:
#   DATABASE_URL=postgresql://gigsfill_user:PASSWORD@localhost:5432/gigsfill
#
DATABASE_URL=

# Redis for rate limiting (auto-detected, no change needed if Redis is on same server)
RATELIMIT_STORAGE_URI=redis://localhost:6379

# Environment flag
GIGSFILL_ENV=production

# Number of uvicorn workers (2 * CPU cores + 1 is the standard formula)
# Your droplet has 2 CPUs so 4 workers is optimal
WEB_CONCURRENCY=4
ENVEOF
    echo "✅ Created ${ENV_FILE} — edit it to set DATABASE_URL"
else
    echo "✅ ${ENV_FILE} already exists — skipping (not overwriting)"
fi

# ── 4. Systemd service ──────────────────────────────────────
echo ""
echo "▶ Installing systemd service..."

cat > /etc/systemd/system/gigsfill.service << SVCEOF
[Unit]
Description=GigsFill FastAPI Application
After=network.target redis.service
Wants=redis.service

[Service]
Type=exec
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env

# Load env vars from .env file
ExecStart=${VENV}/bin/uvicorn backend.main:app \\
    --host 0.0.0.0 \\
    --port 8001 \\
    --workers \${WEB_CONCURRENCY:-4} \\
    --loop uvloop \\
    --log-level info \\
    --access-log \\
    --no-use-colors

# Restart on crash with backoff
Restart=always
RestartSec=5
StartLimitInterval=60
StartLimitBurst=3

# Resource limits
LimitNOFILE=65536
LimitNPROC=4096

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gigsfill

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable gigsfill
echo "✅ Systemd service installed"

# ── 5. If DATABASE_URL is set, run migration ────────────────
echo ""
if grep -q "^DATABASE_URL=postgresql" "${ENV_FILE}" 2>/dev/null; then
    DB_URL=$(grep "^DATABASE_URL=" "${ENV_FILE}" | cut -d= -f2-)
    echo "▶ DATABASE_URL is set — running data migration..."
    "${VENV}/bin/python" scripts/migrate_sqlite_to_postgres.py \
        --sqlite "${APP_DIR}/backend.db" \
        --postgres "${DB_URL}"
else
    echo "▶ DATABASE_URL not set yet — keeping SQLite for now."
    echo "  Edit ${ENV_FILE}, set DATABASE_URL, then run:"
    echo "  python scripts/migrate_sqlite_to_postgres.py \\"
    echo "    --sqlite ${APP_DIR}/backend.db \\"
    echo "    --postgres \"your_postgres_url\""
fi

# ── 6. Start / restart service ──────────────────────────────
echo ""
echo "▶ Starting GigsFill service..."
if systemctl is-active --quiet gigsfill; then
    systemctl restart gigsfill
    echo "✅ Service restarted"
else
    systemctl start gigsfill
    echo "✅ Service started"
fi

sleep 2
systemctl status gigsfill --no-pager -l || true

echo ""
echo "=============================================="
echo " Setup complete!"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. Edit ${ENV_FILE} and set DATABASE_URL to your PostgreSQL URL"
echo "  2. Run: python scripts/migrate_sqlite_to_postgres.py"
echo "  3. Then: systemctl restart gigsfill"
echo "  4. Monitor: journalctl -u gigsfill -f"
echo ""
echo "Redis status:    systemctl status redis-server"
echo "App logs:        journalctl -u gigsfill -f"
echo "App status:      systemctl status gigsfill"
