# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Authoritative reference

`gigsfill-claude-doc.md` (in repo root) is a hand-maintained reference covering data model, every route module, schedulers, email system, booking pipeline, payment flow, known issues, and deploy. **Read it first** for any non-trivial change — especially anything touching booking, payments, contracts, schedulers, or the affiliate program. It also has a Changelog at the top tracking recent fixes; check there before assuming current state.

When you make a meaningful code change, also update the relevant section of `gigsfill-claude-doc.md` and add a Changelog entry (this is the project convention — see existing entries for tone/format).

## Commands

```bash
# Activate the project virtualenv
source /opt/gigsfill/venv/bin/activate

# Run API locally (production uses systemd + 2 workers; see Deployment below)
uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload

# Run the schedulers locally (separate process; do NOT also enable in API)
GIGSFILL_RUN_SCHEDULERS=1 python -m backend.scheduler_main

# Tests (pytest, in-memory SQLite via tests/conftest.py)
pytest                                  # all tests
pytest tests/test_services.py           # one file
pytest tests/test_services.py::test_x   # one test
pytest -k cancel                        # by name match

# End-to-end cancel flow (requires a running local server)
python test_cancel_flow.py

# Production service control (DigitalOcean droplet)
sudo systemctl status  gigsfill            # API
sudo systemctl status  gigsfill-scheduler  # Schedulers (single process)
sudo systemctl restart gigsfill            # API only
sudo systemctl restart gigsfill-scheduler  # Schedulers only
sudo journalctl -u gigsfill           -f
sudo journalctl -u gigsfill-scheduler -f
```

There is no JS build step or linter — frontend is vanilla JS served as static files from `app/`.

## Architecture (the things you can't infer from one file)

**Two-sided marketplace**: venues post gigs, artists book them. Live music–specific (the UI says "artists" and "venues", not generic terms). Stripe handles charges (venues) and Connect Express transfers (artists), with payouts firing the day after the gig.

**Process topology**: the API (`gigsfill.service`, 2 uvicorn workers on port 8001) and the schedulers (`gigsfill-scheduler.service`, single process running `python -m backend.scheduler_main`) are **separate systemd units**. The split is gated by the `GIGSFILL_RUN_SCHEDULERS` env var — set only in the scheduler unit. This is deliberate: when the schedulers ran inside uvicorn, both workers fired them and emails double-sent. Do not move them back into the API process. Both services read the same `/opt/gigsfill/.env`. Drop-ins in `/etc/systemd/system/<unit>.service.d/` (`secret.conf`, `override.conf`) **must be mirrored to both units** — otherwise the scheduler can't sign tokens.

**Dual-engine SQL**: SQLite by default (`backend.db` in repo root, WAL mode), PostgreSQL via `DATABASE_URL`. A `_PgCompatConn` shim in `backend/db.py` translates `?` placeholders to `%s` so the same raw SQL runs on both. Schema lives in `backend/db.py:setup_database()` (one giant function with `CREATE TABLE IF NOT EXISTS` + an additive `_add_columns()` helper). `backend/models.py` mirrors the schema as SQLAlchemy ORM but is kept in sync **manually** — when adding/changing a column, update both. Alembic is declared in requirements but unused.

**Single-slot vs multi-slot gigs branch in ~70 places.** Single-slot gigs store the booked artist on `gigs.artist_id`. Multi-slot gigs store one artist per row in `gig_slots`. Pay/start/end live on `gigs` for single-slot, on `gig_slots` rows for multi-slot. Result: many UNION queries and `if is_multi_slot:` branches. When changing a query, check whether both shapes are handled.

**Three cancellation endpoints** in `routes/gigs.py`, easy to fix one and miss others:
- `cancel_gig`           → `DELETE /api/gigs/{id}/cancel`
- `cancel_slot`          → `POST  /api/gigs/slots/{slot_id}/cancel`
- `delete_gig_with_slots`→ `DELETE /api/gigs/{id}/with-slots`  ← the venue UI's "Cancel Gig" button uses this one

All three need consistent transaction cleanup, flyer cleanup, and `last_cancelled_artist_id` handling. To verify which fired in production: `journalctl -u gigsfill --since "5 minutes ago" | grep -E "/api/gigs/.+/(cancel|with-slots)"`.

**`is_admin` is TEXT (`'true'`/`'false'`), not boolean.** Canonical check: `str(user.is_admin).lower() in ('true', '1')`. Never `not user.is_admin` — the literal string `'false'` is truthy in Python and would pass an admin gate. (One such bug shipped to production; see Changelog 2026-05-04.)

**Payment status invariants** (in `transactions` rows): artist_payout child rows are created with `status='scheduled'`. The scheduler transitions them `scheduled → paid` after the parent venue charge clears. `pending_transfer` is reserved for "transfer was attempted and is awaiting retry" — do **not** create child rows in that state at booking time, or the hourly retry sweep will fire transfers before venues are charged. Defense-in-depth guard in `payout_scheduler.py` requires parent status in `('charged','paid','transferred')` before transferring a child. See Changelog 2026-05-07 for the full incident.

**Email Center UI lives in `app/venue-create-gigs.html`**, embedded as a tab — NOT in `app/venue-email-center.html` (which exists but isn't loaded by the live UI). Edit the former for Email Center bugs.

**Email templates: file is canonical, DB is runtime.** `backend/email_templates.py` defines templates in code; `_populate_email_templates()` syncs file → DB on startup. Admin UI edits go DB → file via auto-export in the PUT endpoint, so admin edits survive restarts. After deploying changes via `sudo cp`, **always run `chown www-data:www-data`** on touched files — auto-export breaks silently if `email_templates.py` is root-owned. Same for any file the app writes to.

**Frontend**: vanilla JS, no build step. Use the `window.apiGetSafe`/`apiPostSafe`/`apiPutSafe`/`apiDeleteSafe` helpers in `app/static/js/api-globals.js` (loaded on the 10 main app pages) — they read FastAPI's `{"detail": "..."}` body and surface real error messages. Raw `fetch()` patterns like `if (!res.ok) throw new Error('Failed')` discard backend messages; many older sites still do this and can be migrated incrementally as bugs surface.

**Auth**: signed session cookies via `itsdangerous` (HMAC, 7-day rolling). `routes/auth.py` is the source of truth; `app/static/js/auth.guard.js` is the frontend gate. State-changing endpoints use `check_venue_access` / `check_artist_access` helpers from `backend/utils.py` so secondary entity users (multi-user accounts) are authorized correctly.

## Where to look first

Hot spots — these files are large and hold most of the surface area:

| Concern                          | File                                          |
|----------------------------------|-----------------------------------------------|
| Booking, cancel, recurring, blast| `backend/routes/gigs.py` (~4.7k lines)        |
| Contracts (sign, countersign, PDF)| `backend/routes/contracts.py` (~3.1k)        |
| Stripe (cards, Connect, webhooks)| `backend/routes/stripe_connect.py` (~2k)      |
| Admin endpoints                  | `backend/routes/admin.py` (~2k)               |
| Schema                           | `backend/db.py` (~1.6k, one big function)     |
| Email templates                  | `backend/email_templates.py` (~2.6k, ~80 templates) |
| Venue calendar + gig modal       | `app/static/js/venue.create-gigs.js` (~252 KB)|
| Flyer canvas editor              | `app/static/js/flyer-editor.js` (~130 KB, Fabric.js)|
| Artist calendar                  | `app/static/js/artist.book-gigs.js` (~127 KB) |

Cross-cutting helpers worth knowing:
- `backend/services/notification_service.py` — `create_notification`, `notify_gig_booked/cancelled/edited`
- `backend/services/email_dispatch.py` — `send_booking_emails`, `send_cancellation_emails`, etc.
- `backend/services/gig_cleanup.py` — `cleanup_gig_records`, `delete_gig_completely`
- `backend/utils.py` — `check_venue_access`, `check_artist_access`, `get_all_entity_users`, `US_STATE_TIMEZONES`

When you change a backend route, the dispatch layer (`notification_service`, `email_dispatch`) almost always needs touching too — most actions have email/notification side effects.
