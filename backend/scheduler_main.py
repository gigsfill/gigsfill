"""
GigsFill Scheduler Service — standalone entrypoint.

Run via: `python -m backend.scheduler_main`
Used by: scripts/gigsfill-scheduler.service (systemd unit).

This process:
  1. Configures logging (same as main.py).
  2. Ensures the database exists and email templates are populated (idempotent
     on top of what the API service already does — safe if both start at once).
  3. Starts the payout scheduler thread (charges venues + transfers to artists).
  4. Starts the email scheduler thread (gig blasts, reminders, waitlist sweeps,
     review requests, contract-hold cleanup, WAL checkpoint).
  5. Blocks forever; systemd controls lifecycle.

The API service (uvicorn workers) does NOT start either scheduler — that path
is gated by GIGSFILL_RUN_SCHEDULERS in main.py. This guarantees that no matter
how many uvicorn workers run, exactly one process ever runs the schedulers.

If THIS service is ever down, no automated emails or payouts go out. Monitor
with:  systemctl status gigsfill-scheduler
       journalctl -u gigsfill-scheduler -f
"""

import logging
import os
import signal
import sys
import time

# ── Logging — identical setup to main.py ─────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    handlers=[logging.StreamHandler()],
)
logging.getLogger("gigsfill").setLevel(logging.INFO)
logger = logging.getLogger("gigsfill.scheduler_main")


def _ensure_db_and_templates():
    """Run the same setup main.py runs — safe if the API service is also up."""
    try:
        from backend.db import setup_database
        setup_database()
    except Exception as e:
        logger.error(f"Database setup failed: {e}", exc_info=True)
        # Don't exit — the DB is probably already initialized by the API service.
        # If it really is broken, the schedulers will fail loudly on first DB hit.

    try:
        from backend.email_templates import run_migration
        run_migration()
    except Exception as e:
        logger.warning(f"Email template sync failed (non-fatal): {e}")


_running = True


def _handle_signal(signum, frame):
    """Graceful shutdown on SIGTERM/SIGINT."""
    global _running
    logger.info(f"Received signal {signum}, shutting down scheduler service")
    _running = False


def main():
    logger.info("=" * 60)
    logger.info("GigsFill Scheduler Service starting")
    logger.info(f"  PID: {os.getpid()}")
    logger.info(f"  GIGSFILL_ENV: {os.environ.get('GIGSFILL_ENV', '<unset>')}")
    logger.info(f"  DATABASE_URL set: {bool(os.environ.get('DATABASE_URL'))}")
    logger.info("=" * 60)

    _ensure_db_and_templates()

    # Start the two scheduler threads (both daemon=True, so they die when this
    # process exits — that's fine, systemd will restart us if we crash).
    try:
        from backend.payout_scheduler import start_payout_scheduler
        start_payout_scheduler()
        logger.info("✅ Payout scheduler thread started")
    except Exception as e:
        logger.error(f"Failed to start payout scheduler: {e}", exc_info=True)
        sys.exit(1)

    try:
        from backend.scheduler import start_scheduler
        start_scheduler()
        logger.info("✅ Email scheduler thread started")
    except Exception as e:
        logger.error(f"Failed to start email scheduler: {e}", exc_info=True)
        sys.exit(1)

    # Install signal handlers AFTER threads start so we don't race with startup
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("Scheduler service running. Send SIGTERM/SIGINT to stop.")

    # Block forever. The scheduler threads are daemons — they'll keep ticking
    # while this main thread sleeps. When a signal comes in, _handle_signal
    # flips _running to False and we fall out of the loop.
    while _running:
        time.sleep(60)

    logger.info("Scheduler service shutting down cleanly")
    sys.exit(0)


if __name__ == "__main__":
    main()
