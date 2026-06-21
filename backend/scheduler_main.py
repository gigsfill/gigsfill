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


def _maybe_init_sentry():
    """Mirror main.py: init Sentry only when SENTRY_DSN is set. The
    scheduler is the most important process to instrument — when an
    email blast or payout job throws at 3 AM, we want to know without
    grepping journalctl. No-op when DSN is unset."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        sentry_sdk.init(
            dsn=dsn,
            integrations=[SqlalchemyIntegration()],
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES", "0.0")),
            send_default_pii=False,
            environment=os.environ.get("GIGSFILL_ENV", "production"),
            release=os.environ.get("GIGSFILL_RELEASE") or None,
            # Tag the process so we can split scheduler errors from
            # API errors in the Sentry UI.
            server_name="gigsfill-scheduler",
        )
        logger.info("[SENTRY] scheduler error tracking initialized")
    except Exception as e:
        logger.warning(f"[SENTRY] scheduler init failed: {e}")


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

    # Audit fix (May 2026 part 4): explicit guard. main.py only starts
    # schedulers when GIGSFILL_RUN_SCHEDULERS=1. This service is the
    # designated process for them, so refuse to start unless the same
    # flag is set — that way an accidental run of this module under a
    # systemd unit that's missing the env var fails loudly instead of
    # quietly running schedulers in parallel with the API workers.
    # Audit fix (May 2026 part 7): main.py accepts ("1","true","yes") for this
    # flag — align this check so a user who set `=true` on the scheduler
    # systemd unit doesn't refuse-to-start here AND trigger schedulers inside
    # the API workers (which DO accept "true").
    if os.environ.get("GIGSFILL_RUN_SCHEDULERS", "0").strip().lower() not in ("1", "true", "yes"):
        logger.error(
            "GIGSFILL_RUN_SCHEDULERS is not set to a truthy value — refusing to start. "
            "This service is the only process allowed to run schedulers. "
            "Set GIGSFILL_RUN_SCHEDULERS=1 in the gigsfill-scheduler systemd unit."
        )
        sys.exit(2)

    _maybe_init_sentry()
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
