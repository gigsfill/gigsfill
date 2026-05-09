# GigsFill — Complete System Reference

**Purpose of this document.** This is a self-contained reference for the GigsFill codebase. If you're starting a new chat with Claude, paste this whole file in your first message and Claude will have a working understanding of the entire system without re-reading the code.

**Last updated:** May 9, 2026.

## Changelog

The list below tracks meaningful changes after the initial sync from the codebase. Each entry covers what changed in the code AND the doc sections updated to reflect it. Whenever code changes, update the relevant doc sections AND add an entry here.

- **2026-05-09 — Stripe idempotency keys on every charge/transfer site.** Of 6 PaymentIntent/Transfer creation sites, only 1 used `idempotency_key=` (the main scheduler venue charge). The other 5 were exposed to duplicate-charge risk if a network hiccup made us retry an already-succeeded Stripe call, or if a downstream race fired the same trigger twice. Added idempotency keys to:
  - [payout_scheduler.py:680](backend/payout_scheduler.py) — main artist payout transfer (`payout_{id}_transfer`)
  - [payout_scheduler.py:429](backend/payout_scheduler.py) — stalled-transfer retry path (same key as original — Stripe returns the existing transfer if the original succeeded but our DB wasn't updated)
  - [affiliate.py:824](backend/routes/affiliate.py) — quarterly affiliate payout (`aff_payout_{id}`)
  - [stripe_connect.py:531](backend/routes/stripe_connect.py) — venue charge from booking flow (`gig_{id}_slot_{id}_artist_{id}_charge`)
  - [stripe_connect.py:854](backend/routes/stripe_connect.py) — payment-cancellation platform fee (`gig_{id}_cancel_fee`)
  - [stripe_connect.py:1083](backend/routes/stripe_connect.py) — payment reinstatement (`txn_{id}_reinstate`)
  Stripe enforces idempotency by key for 24 hours after first use, so retries within that window get the original result instead of a duplicate operation.
- **2026-05-09 — Production hardening pass: webhook replay, log rotation, SMTP rate limits, fail2ban.**
  - **Stripe webhook signature/replay**: [stripe_connect.py](backend/routes/stripe_connect.py) used `stripe.Webhook.construct_event` (which already enforces both HMAC verification AND a 5-minute timestamp tolerance, so replay protection was implicit). But the previous code had a fallback `else: event = json.loads(payload)` when `webhook_secret` was empty — meaning if the secret was ever cleared (admin mistake, env var unset, migration wipe), the endpoint silently accepted unsigned webhooks. An attacker could then forge `payment_intent.succeeded` to mark a charge paid that wasn't. Removed the fallback; the endpoint now refuses (503) any webhook when the secret isn't configured.
  - **Log rotation**: new `/etc/logrotate.d/gigsfill` rotates `/var/log/gigsfill-backup.log` weekly with 12-week retention, gzipped. Logrotate dry-run validates clean. Explicit `su root syslog` directive because `/var/log` is group-writable on this box.
  - **Health check endpoint**: `/health` already existed at [main.py:421](backend/main.py) — does a `SELECT 1` on the DB, checks the signing key is loaded, returns 200 with `{"status":"ok"}` or 503 with details. Operator should point any uptime monitor (UptimeRobot, Better Uptime, etc.) at `https://gigsfill.com/health`. No code change needed; just verified live.
  - **SMTP rate limits per user**: existing `RATE_EMAIL_SEND = "10/minute"` constant in [rate_limiter.py](backend/rate_limiter.py) was defined but never wired in. Now applied to `/api/affiliate/recommend`, `/api/affiliate/resend-recommend/{email_id}`, `/api/gigs/{gig_id}/messages` (each message triggers an email notification), `/api/entity-users/artist/{artist_id}/invite`, `/api/entity-users/venue/{venue_id}/invite`, and `/api/entity-invitations/{invitation_id}/reinvite`. Without this, a single authenticated account could blast hundreds of emails through our Bluehost SMTP, exhausting daily quota and harming sender reputation. 10/minute per IP is enough for legitimate batch invites without enabling abuse.
  - **fail2ban on auth endpoints**: installed `fail2ban` package, added `/etc/fail2ban/filter.d/gigsfill-auth.conf` matching repeated 401/429 responses on `/api/login`, `/api/reset-password`, `/api/forgot-password`, `/api/change-password`, `/api/signup` in nginx access log. New jail at `/etc/fail2ban/jail.d/gigsfill.conf`: 15 retries within 5 minutes → 1 hour ban at the kernel firewall layer. Layered defense: slowapi already rate-limits per IP at the app level, but slowapi state lives in Redis and the in-memory lockout resets on process restart. fail2ban survives restarts and blocks before the request reaches uvicorn at all. Smoke-tested filter regex against synthetic log lines: matches 401/429 on auth paths, ignores 200s on /health.
- **2026-05-09 — DMARC tightened from monitor-only to quarantine (conservative ramp).** DMARC was at `p=none` since launch, with rua aggregate reports going to jcarta@gigsfill.com. Operator confirmed reports show only legitimate sources passing (mailgun, bluehost, droplet IP 50.87.222.88 — all in the SPF record). Updated the `_dmarc.gigsfill.com` TXT record at GoDaddy from:
  ```
  v=DMARC1; p=none; adkim=r; aspf=r; rua=mailto:jcarta@gigsfill.com;
  ```
  to:
  ```
  v=DMARC1; p=quarantine; pct=25; sp=quarantine; adkim=r; aspf=r; rua=mailto:jcarta@gigsfill.com;
  ```
  - `p=quarantine` — failing mail goes to the recipient's spam folder instead of inbox
  - `pct=25` — only 25% of failing mail gets quarantined initially. Conservative ramp so an overlooked legit sender doesn't kill mail flow on day one
  - `sp=quarantine` — same policy applies to subdomains (e.g. mg.gigsfill.com) so attackers can't bypass via a subdomain spoof
  - alignment kept at `adkim=r aspf=r` (relaxed) — strict alignment would risk breaking mail where the From: header and Return-Path differ in subdomain (mailgun's bounce path is on a subdomain)
  - rua= unchanged so reports still flow

  **Confirmed live** on the authoritative GoDaddy nameserver after operator save. Public resolvers may serve the old `p=none` value until the 1h TTL expires, then start picking up `p=quarantine`.

  **Next step (after 7 clean days):** drop the `pct=25` (= pct=100, full enforcement). After another 30 clean days, consider `p=reject` for the strongest enforcement.

- **2026-05-09 — Backup self-test automation: daily decrypt + integrity check.** We had encrypted offsite backups running but had never validated the round-trip. Classic "backups exist but nobody tested restore" disaster scenario. New script `/usr/local/bin/gigsfill-backup-verify.sh` runs at 04:30 UTC daily (1h after the offsite push at 03:30):
  1. Picks the latest `.gz.enc` from `/var/lib/gigsfill-backups/`
  2. Decrypts with the passphrase at `/opt/gigsfill/.backup_passphrase`
  3. Gunzips
  4. `PRAGMA integrity_check;` — must equal `ok`
  5. Compares row counts of users/gigs/venues/artists/transactions vs the live DB. Backup must have <= live counts (live grows during the day; backup having MORE rows would imply corruption)
  6. On any failure, sends an alert to `admin_alert_email` (jcarta@gigsfill.com) via the platform SMTP config and exits non-zero
  7. Cleans up the temp restore directory via shell trap
  Smoke-tested on the live droplet: verifier returned `[verify] ✓ Restored backend.db.2026-05-09.gz.enc OK (28M, integrity_check=ok, row counts sane)`. Full backup pipeline now: 03:00 local → 03:30 encrypt+push offsite → 04:30 verify restore.
- **2026-05-09 — Admin XSS hardening: escape user-controlled data before innerHTML.** [admin-init.js](app/static/js/admin-init.js) and [admin-db.js](app/static/js/admin-db.js) rendered registered-user data (artist names, venue names, emails, ticket subjects/descriptions/replies, generic DB cell values) directly via innerHTML template literals with no escaping. A malicious user could register an artist name like `<img src=x onerror=fetch('/api/admin/...')>` and that script would execute the next time any admin opened the dashboard or DB tools — privilege escalation to admin via the analytics widgets, recent-bookings list, top-artists/top-venues lists, recent-signups list, support-ticket modal (header + thread + reply form), accounting table, and the DB cell renderer.
  - Wrapped every user-data interpolation with the global `esc()` helper from [security.js](app/static/js/security.js) (already loaded before admin scripts in admin.html, so no new file load).
  - The DB tools cell renderer was a particularly broad surface (every column of every table). The literal NULL marker (`<em>null</em>`) is the only HTML the renderer should produce; everything else flows through `escAttr` now.
  - Cache busters bumped: `admin-init.js?v=2`, `admin-db.js?v=2`.
- **2026-05-09 — Admin gigs list shows all booked artists for multi-slot.** [admin.py:get_gigs](backend/routes/admin.py) (both `has_split_pay` branches) showed `--` for the artist column on multi-slot gigs because the LEFT JOIN to artists used `g.artist_id` (NULL on multi-slot). Added a COALESCE with a GROUP_CONCAT subquery on `gig_slots` so multi-slot rows show comma-joined booked-artist names. Smoke-tested: gig 507 (multi-slot, 2 booked) now shows "Fridays Past, Fifty Proof" instead of "--". Also fixed [stripe_connect.py:get_artist_earnings_summary](backend/routes/stripe_connect.py): `gigs_completed` and per-venue `gig_count` counted transactions, not distinct gigs — multi-slot artists who took two slots on one gig saw inflated counts. Switched to `COUNT(DISTINCT t.gig_id)`.
- **2026-05-09 — Multi-slot data-integrity sweep: account deletion + admin analytics.** Same `g.artist_id`-only query pattern that broke 1099 generation also broke two more places where it matters:
  - **Account deletion** ([me.py:delete_preview](backend/routes/me.py), [me.py:delete_account](backend/routes/me.py)). The booked-gig count for the delete-account modal used `g.artist_id = a.id` only, so an artist with multi-slot bookings saw "0 upcoming gigs" and could delete their account thinking nothing would be cancelled. Worse, the actual cancellation cleanup in `delete_account` had the same query — so on deletion, multi-slot bookings stayed live, the venue kept waiting for an artist that no longer existed, and no cancellation email/notification fired. Fixed both queries to LEFT JOIN gig_slots and accept `(g.artist_id = :eid OR gs.artist_id = :eid)`. DISTINCT to avoid double-counting on multi-slot gigs where the same artist took two slots.
  - **Admin analytics dashboard** ([analytics.py](backend/routes/analytics.py)). `top_venues_booked`, `top_artists_booked`, and `recent_bookings` all filtered on `g.artist_id IS NOT NULL` — multi-slot bookings invisible. An artist who only does multi-slot work would never appear in the top-artists list. Rewrote each as a UNION ALL of `gigs.artist_id` rows + `gig_slots.artist_id` rows. For recent_bookings, multi-slot rows order by `gs.created_at` (no booked_at column exists; same imprecision the original single-slot path had with `g.created_at` vs actual booking time).
- **2026-05-09 — Tax-critical: 1099 generation fixed for multi-slot + cents truncation.** [tax.py:generate_1099s](backend/routes/tax.py) had two bugs that would have caused tax-compliance failures at year-end:
  - **Multi-slot bookings entirely excluded.** Query was `FROM gigs g JOIN artists a ON a.id = g.artist_id` — but for multi-slot gigs, `gigs.artist_id` is NULL (booked artists live on `gig_slots.artist_id`), so every multi-slot booking was silently dropped from 1099 totals. Confirmed on prod data: 8 multi-slot bookings across 2 artists were being missed by the existing query.
  - **Cents truncated to whole dollars.** `int(e["total_pay"]) * 100` for the `total_earnings_cents` storage took int() of the dollar amount before scaling — so $10.50 became 1000 cents = $10.00. Lost cents on every 1099.
  - **Fixed by switching the source from `gigs.pay` to `transactions`.** New query sums `transactions.amount_cents WHERE transaction_type IN ('artist_payout', 'single') AND status = 'paid'` joined to gigs for the venue+year filter. This handles both single-slot (legacy 'single' rows + new model 'artist_payout' rows) and multi-slot (multiple 'artist_payout' children per gig). Money is in cents from the start, no truncation. `COUNT(DISTINCT t.gig_id)` so a multi-slot gig where one artist booked two slots counts as one gig in `gig_count`. $600 IRS threshold expressed as `total_cents >= 60000`.
  - **No migration needed**: zero 1099s have been generated yet (`SELECT COUNT(*) FROM tax_1099s` = 0). Future runs will use the corrected query.
- **2026-05-09 — Multi-slot day-list improvements: show all booked artists + open-slot count.** Both venue and artist day-list views previously rendered a single artist for multi-slot gigs (`(g.slots || []).find(...)` returned the first match, hiding the rest). Updated to:
  - All booked: render every booked artist comma-separated, each linked to their profile.
  - Mixed: append "· N open" badge so partial bookings are obvious at a glance.
  - All open multi-slot: "OPEN · N slots" instead of bare "OPEN".
  Single-slot paths and OPEN/Waitlist Active/Booked branches unchanged. Cache busters bumped: `venue.create-gigs.js?v=97`, `artist.book-gigs.js?v=137`.
- **2026-05-09 — Booking notifications disambiguate slot for multi-slot.** [notification_service.py:notify_gig_booked](backend/services/notification_service.py) now appends `. Slot N` to the booking message when the gig has more than one slot. Activity Center already splits messages on "Slot" into a styled second line, so the venue immediately sees which of their slots was just filled. Single-slot path unchanged (no slot suffix).
- **2026-05-09 — Multi-slot pay legibility pass: venue + artist modals, day-list, open-gig blast emails.** Multi-slot gigs were rendering a single `gig.pay` value at the top of the Gig Details modal — but that value is just slot 1's pay, so a 2-slot gig with $10/$20 read as a $10 gig everywhere. Fixed across every surface that exposes per-slot money:
  - **Venue Gig Details modal** ([venue.create-gigs.js:_showBookedGigModal](backend/routes/gigs.py)): top "Pay" row dropped for multi-slot (it was misleading); per-slot pay rendered inline as a green pill (`$X.XX` with rgba bg + border) on each slot row, alongside time. Slot row reflowed from one cluttered line into three: Slot N · time · pay · ✕  /  italic type info  /  Artist line + Message + Rate. Single-slot UX preserved (top Pay line + effective-pay override resolution unchanged). Cache buster `venue.create-gigs.js?v=96`.
  - **Visual polish**: slot label colored purple `#a855f7` (matches the "Gig Details" gradient anchor); 3px purple left-edge stripe on each slot card so cards read as distinct units; "Open" status upgraded from plain text to a green pill matching the pay treatment; time tinted slate `#cbd5e1` so the colored chips pop; type/styles line italicized.
  - **Artist-side gig modal** ([gig-modal.js:_slotRow](app/static/js/gig-modal.js)): same three-line layout + colors applied for parity. `typeInfoHtml` extracted from the cramped header line and rendered on its own line. Cache buster `gig-modal.js?v=2`.
  - **Artist day-list pay column** ([artist.book-gigs.js:1132](app/static/js/artist.book-gigs.js)): for multi-slot, `gig.pay` was just slot 1's. Now: if the artist booked into a slot (`booked-mine`), shows that slot's actual pay; otherwise shows a `$min – $max` range across slots when they differ, single value when all slots have the same pay. Single-slot path unchanged. Cache buster `artist.book-gigs.js?v=136`.
  - **Open-gig blast email templates** (`venue_open_gig_1w`, `venue_open_gig_36h`): hard-coded slot-1 Time/Pay/Type/Lineup/Styles rows replaced with `{{slots_html}}`, which `_build_slots_html_for_scheduler` (already exists) renders as a separator-divided block per open slot. Sibling templates `venue_open_gig_4w` + `venue_open_gig_2w` already had this — fixed the gap. Confirmed DB sync after restart: both templates now have `{{slots_html}}` per `SELECT instr(body, '{{slots_html}}')`.
  - Cancellation emails (`artist_gig_cancelled`, `venue_gig_cancelled`) already use `{{slot_times}}` and don't reference `pay` — no change needed. Booking emails (`artist_gig_booked`, `venue_gig_booked`) iterate per-slot in `send_booking_emails`, sending one email per booked artist with their slot's pay — already correct. Public artist/venue profile pages don't render pay.
- **2026-05-09 — Audit log expansion + auth hardening (H1/H2/H8/H9) + off-host backups + git repo.**
  - **Audit log wired into 6 more mutation endpoints.** Previously the table existed but only `update_settings`, `db_tools_update/delete/insert` wrote to it. Added: `update_email_template` (admin.py:603 — captures previous subject/body, writes new), `update_payment_settings` (admin.py:794 — diff per key, secrets recorded as `••••••••` to prove a rotation happened without leaking the value), `toggle_venue_payment_override` (admin.py:1026 — before/after suspended-state with venue context in metadata), `manual_link_affiliate` (affiliate.py:1049 — captures any pre-existing referral being overwritten), `delete_referral` (affiliate.py:1086 — full row snapshot before DELETE), `run_payouts_manual` (affiliate.py:1109 — quarter recorded). Every audit write is best-effort wrapped (helper itself catches all exceptions and logs WARN), so audit gaps never break the underlying admin action.
  - **H1/H2 — session invalidation on password change/reset.** New `users.password_changed_at TIMESTAMP` column (added via `_add_columns` in `db.py:1508`, mirrored in `models.py:User`). `verify_session_token_with_iat` extracts the issued-at from itsdangerous tokens via `return_timestamp=True`. New `_reject_if_password_rotated` helper compares token-iat vs `password_changed_at` (with a 5-second clock-skew grace) and raises 401 if the token predates the rotation. Wired through `get_current_user` and `get_optional_user`. Both `change_password` and `reset_password` stamp `password_changed_at = utcnow_naive()` on success — so every other device the account is logged in on is immediately kicked. `change_password` also re-issues a fresh session cookie on the requesting browser so the user doesn't lock themselves out. Legacy users with NULL `password_changed_at` skip the check (predates the column).
  - **H8 — bcrypt 72-byte truncation cap.** New `validate_password_or_raise(password, *, min_chars=6)` helper in `auth.py` rejects passwords whose UTF-8 encoded length exceeds `BCRYPT_MAX_BYTES = 72`. bcrypt silently truncates input past 72 bytes — without this guard a user with a 100-char password thinks they have entropy past byte 72 but doesn't, and a server-side hash collides with the truncated form. The check is at signup (`/api/signup`), password change, password reset, and entity-user invitation accept. Verify path is unchanged so existing users with >72-byte passwords still log in (bcrypt.checkpw keeps doing what it always did). Smoke-tested: 73-char ASCII rejected, 20× musical-note emoji (80 bytes UTF-8) rejected.
  - **H9 — single-use reset tokens (JTI).** New `used_reset_tokens(jti TEXT PRIMARY KEY, used_at DATETIME)` table with index on `used_at`. `forgot_password` now embeds a random `secrets.token_urlsafe(16)` `jti` claim in the reset-token payload. `reset_password` checks the jti against `used_reset_tokens` before processing — already-consumed tokens are rejected with `"This reset link has already been used. Please request a new one."`. On success, the jti is INSERTed and rows older than 2h are pruned opportunistically. Pre-H9 tokens (no jti) remain replayable until they expire (1h), which is the small window that existed before this fix shipped. Combined with H1/H2 above, this gives belt-and-suspenders: replay-prevention via jti, plus device-wide invalidation via `password_changed_at`.
  - **Off-host backup wiring.** New `/var/lib/gigsfill-backups` git repo pointing at private `github.com/gigsfill/gigsfill-backups`. Daily script `/usr/local/bin/gigsfill-backup-offsite.sh` (cron 03:30 UTC, 30 min after the local 03:00 backup) AES-256-CBC encrypts the latest gz with PBKDF2 KDF using a 256-bit passphrase at `/opt/gigsfill/.backup_passphrase` (mode 600, gitignored), commits to the offsite repo, prunes working-tree blobs older than 30 days, pushes. Round-trip decrypt + gunzip integrity verified. Passphrase recorded off-host (operator). Source-of-truth git repo also pushed: `github.com/gigsfill/gigsfill` (private). Credentials stored at `/root/.git-credentials` with `git config --global credential.helper store`.
  - **Files updated**: `backend/routes/admin.py`, `backend/routes/affiliate.py`, `backend/routes/auth.py`, `backend/routes/me.py` (no-op for password_changed_at — me.py only verifies, doesn't rotate), `backend/routes/entity_users.py`, `backend/db.py`, `backend/models.py`. New: `/usr/local/bin/gigsfill-backup-offsite.sh`, `/var/lib/gigsfill-backups/`, `.gitignore` updated to exclude `.backup_passphrase`.
- **2026-05-09 — Admin audit-log table + Stripe end-to-end verification.**
  - **Audit log feature.** New `admin_audit_log` table (id, admin_user_id, admin_email, action, target_table, target_id, before_json, after_json, metadata_json, ip_address, created_at) with three indexes (admin_user_id, created_at, target_table+target_id). Helper `log_admin_action(db, admin, action, *, target_table, target_id, before, after, metadata, request)` in `backend/utils.py` — best-effort writer that NEVER raises; failures log a WARN and continue so audit gaps don't break admin actions. Wired into the highest-impact mutation sites: `update_settings` (with before/after diff per setting key, secrets redacted), generic DB tools `update_row` / `delete_row` / `insert_row` (with before-state snapshots from `SELECT * WHERE rowid=...`). New `GET /api/admin/audit-log` endpoint with filters (`action`, `target_table`, `admin_user_id`) + pagination (default 50/page, max 200). Endpoint gated via `check_admin`. Smoke-tested: helper writes successfully, table queryable, anonymous GET returns 401.
  - **Stripe end-to-end verification.** Traced the live system without spending money:
    1. **API connectivity**: `sk_live_…` valid, `Balance.retrieve()` returns $103.18 available + $14.26 pending
    2. **Last real charge cross-verified**: txn 287 (gig 505) DB row matches Stripe PI `pi_3TUbqcGTPqz6PmNX2kr8tOcF` to the cent — `credit_card_fee_cents=$0.74` matches Stripe's actual `balance_transaction.fee` exactly (the May 8 fix is working). Child txn 288 confirms `tr_1TUHEkGTPqz6PmNX6Xp6QQwQ` paid out $5.00 to `acct_1T0W4iKDMuJmcAli`.
    3. **Connect onboarding state**: 4 artists have Connect accounts; 3 are `payouts_enabled=True`; 1 (Stage 5 Clinger) is `payouts_enabled=False, disabled=requirements.past_due` and `db_onboarded=0` — natural test of the new gate from audit fix #10. The webhook handler we improved would email this artist when their account state next changes.
    4. **Venue payment method**: 14 Cannons has `cus_TyTApDLGIpNNM6` + `pm_…` (visa ****4738) on file.
    5. **Webhook endpoint live**: rejects bad signatures with 400.
    6. **Scheduler sweeping hourly**: last sweep 02:00 UTC, no pending payouts.
    7. **Audit log writes confirmed**.
  - **Pipeline summary**: 7 of 7 stages pass. The system is live-money-ready: real Stripe live keys, real charges have already cleared (gig 505 yesterday), real-fee capture working, real Connect transfers paying out, real webhook signing verifying. **Ready to take real bookings.**
- **2026-05-08 — Auth + signup audit: 9 fixes across critical, high, and medium tiers.** Same playbook as the venue/artist/admin surfaces. Two account-takeover vectors closed.
  - **C2 (CRITICAL — open redirect → phishing)**: `app/static/js/index-init.js:32` decoded `?redirect=` and assigned to `window.location.href` with no origin validation. `gigsfill.com/?redirect=https://evil.com/login` → user logs in successfully then lands on attacker page mimicking GigsFill. Fixed: `_safeRedirect` only accepts paths starting with `/app/` and rejects scheme indicators (`//`, `\`).
  - **C1 (CRITICAL — account takeover)**: `PUT /api/me` (`backend/routes/me.py:75`) silently overwrote `users.email` with no password reconfirmation, no notification to old address, `email_verified` left at 1. Stolen-session attacker → swap email → forgot-password emails go to attacker → permanent account takeover. Fix: require `current_password` (verified via bcrypt), reject if new email collides with another account (generic phrasing — anti-enumeration), reset `email_verified=0`, email the OLD address an alert ("Your account email was changed"), re-fire `_send_verification_email` to the NEW address. Returns `email_changed` flag in the response so the frontend can show a "check your inbox to verify your new email" message.
  - **C3 (HIGH — email enumeration)**: signup at `auth.py:309` hard-failed with `"Email already exists"` — paired with the deliberately-anonymous forgot-password endpoint, the inconsistency let attackers enumerate accounts via signup. Fix: send the colliding address an "account already exists" notice (so they know to log in or reset) and return a generic `"Could not create account. If you already have one, please log in or reset your password."` 400. Automated enumeration can no longer distinguish.
  - **H3 (HIGH — auth brute-force)**: `change-password` and `PUT /api/me` had no rate limit. Authenticated `current_password` brute-force was unrestricted. Added `@limiter.limit("5/minute")` on `change-password` and `@limiter.limit("10/minute")` on `PUT /api/me`. Both endpoints now require a `request: Request` parameter for slowapi to read the IP.
  - **H4 (HIGH — login DoS)**: lockout was keyed on `email` alone — attacker could lock out a victim by submitting 10 wrong logins for their email from any IP. Refactored `_check_lockout` / `_record_failed_login` / `_clear_failed_logins` to use `(email, ip)` tuple. New `_client_ip(request)` helper handles `X-Forwarded-For` for the nginx-fronted deployment. Login endpoint passes the IP through. Attacker now locks only their own (email, ip) pair; legitimate user from a different IP is unaffected. Successful login clears every entry for that email across all IPs (covers dynamic-IP users).
  - **H7 (HIGH — silent SMTP failures on reset)**: forgot-password's reset-email path used to log generic errors that admin alerting couldn't match. Tagged the two `logger.error` lines at `auth.py:928,933` with `[AUTH][RESET_FAIL]` prefix so a future log-watcher can grep for them and surface SMTP outages.
  - **M11 (MEDIUM)**: `delete_account` now explicitly clears the session cookie on successful deletion via `clear_session_cookie` helper. Subsequent requests would have 401'd anyway (user row is gone) but the cookie should be cleared properly so the browser stops sending a stale token.
  - **Deferred**: H1/H2 (session/token invalidation on password change/reset via `password_changed_at` column), H6 (cleanup of remaining 4× `except Exception: pass` in signup — only 1 actually present in current code, others were already cleaned), H8 (bcrypt 100→72 cap collision), H9 (single-use reset tokens via JTI table), M1/M2/M3/M5/M8/M9/M10/M12 (varied — UX, infra, low-risk dev).
- **2026-05-08 — Admin surface audit: 7 fixes across critical, high, and medium tiers.**
  - **C1 (CRITICAL — public BI/PII leak)**: `/api/analytics/stats/admin-dashboard` (`backend/routes/analytics.py:470`) was completely unauthenticated. Anonymous request returned: total_revenue, total_payouts, total_commission, every signup email with timestamps, recent bookings with artist+venue names, top venues/artists/cities, gig counts by status. Anyone on the internet had real-time admin BI access. Fix: added `Depends(check_admin)` (imported from `routes/admin`). Verified — anon GET now returns `{"detail":"Not logged in"}` 401.
  - **C2 (CRITICAL — admin page UI exposure)**: `app/admin.html` had no admin-status gate — only `auth.guard.js` ran, which checks email-verification but NOT admin role. Any logged-in non-admin could load the admin UI (most API calls 403'd, but combined with C1 they had a clear path to data). Fix: inline pre-paint guard at top of `<head>` that hides visibility, calls `/api/me`, redirects non-admins to `/app/user-profile.html`. Tolerant `is_admin` check matches `true`/`'true'`/`1`/`'1'`.
  - **C3 (CRITICAL — UX misleading)**: frontend `_PROTECTED_TABLES` mirror at `app/static/js/admin-db.js:120` was stale (`['users','platform_settings']` only). Showed Edit/Delete buttons on `gigs`, `transactions`, etc. that backend correctly 403'd, leading to confusing red-error UX. Synced to the full backend list including the affiliate tables added in this same audit.
  - **C4 (CRITICAL — money math)**: `_recalculate_venue_pending_transactions` at `admin.py:1089` used legacy per-slot fee math (replicating the per-slot min-fee bug closed May 7) AND iterated EVERY transaction row including artist_payout children, rewriting `venue_charge_cents=0` rows meaninglessly. Triggered from free-trial toggle paths, so admin actions silently corrupted multi-slot pending transactions. Rewrote to collect distinct gig_ids with `transaction_type='venue_charge'` parents and call the canonical `_recompute_gig_fees` from `routes.gigs` — single source of truth for the gig-level + proportional split model.
  - **H2 (high)**: `admin.py:1022` `payments_enabled` check `settings_row in ('1','true')` failed on JSON `true` writing string `'True'` (capital), silently demoting restored transactions to `'test'` so the scheduler skipped charging them. Now `str(...).strip().lower() in ('1','true')`.
  - **H3 (high)**: `update_settings` had no input validation. Admin could persist `platform_fee_percent='-50'`, `'abc'`, `'1000'`; `platform_min_fee=''`; `payment_processing_hour='99'`. Each silently broke downstream math. Added per-key validation: fee_percent in [0,100], min_fee >= 0, processing_hour in [0,23], split in {split, venue_only, artist_only}; raise 400 with a clear message on violation.
  - **H7 (high — security)**: support-ticket access tokens (`backend/main.py:593`) had no expiry encoded — leaked email gave permanent ticket access. Added a 30-day TTL gate via `_validate_support_token` helper using `support_tickets.created_at`. Existing tokens still work but stop working 30 days after ticket creation. Two call sites (GET + POST reply) refactored to use the helper.
  - **M7 (medium)**: affiliate tables (`affiliate_referrals`, `affiliate_earnings`, `affiliate_payouts`) added to `_PROTECTED_TABLES` so admin can't bypass the proper deletion endpoints (which validate state) via the generic DB tools.
  - **Deferred (intentional)**: H1 (admin audit log table — proper feature, multi-day), H4 (email-template auto-export — needs trace verification), H5 (frontend silent-fetch migration to apiSafe family — large), H8/H9 (test_smtp + recommend rate limit, template var sanitization — minor surface).
- **2026-05-08 — Artist-side audit: 13 fixes across critical, high, and medium tiers.** Same playbook used on the venue side surface earlier; same severity tiering.
  - **Probe Critical #3 (regression)**: `me.py:delete_account` called `utcnow_naive()` without importing it — introduced by the morning's utcnow sweep. Wrapped in try/except so deletion succeeded but the counterparty got no notification or cancellation email. Fixed: added module-level `from backend.utils import utcnow_naive`.
  - **Probe Critical #1 (security)**: `preferred_artists.{approve,deny,revoke,override}` had **NO authz check** at all. Any authenticated user could approve themselves into preferred status at any venue, deny rivals, revoke approvals, or rewrite pay overrides. Frontend swallowed responses so the bug was invisible during venue testing. Added `check_venue_access(db, request_info["venue_id"], user.id)` to all four endpoints (`backend/routes/preferred_artists.py:573, 703, 830, 951`).
  - **Probe Critical #2 (data integrity)**: `book_with_contract` had the same slot-claim race we closed in `book_gig`/`book_slot` last night. `_apply_slot_booking` (`contracts.py:1880`) did `UPDATE gig_slots SET status='pending_contract' WHERE id=:sid` with no status guard. Two artists hitting the contract booking flow simultaneously could both pass the prior status check, both UPDATE — last write wins but the loser is mid-contract-creation. Fixed: conditional UPDATE `WHERE id=:sid AND status='open'` + rowcount check + 409 `SLOT_TAKEN`. Same pattern applied to single-slot path.
  - **Probe High #4 (multi-user authz)**: `waitlist.{join,leave,status,artist_list}` (`waitlist.py:131, 186, 247, 322`) used `WHERE a.user_id=:uid` — secondary entity_users got 403. Replaced with `check_artist_access`. Co-managers can now manage waitlists.
  - **Probe High #5 (multi-user authz)**: `create_artist_connect_account` (`stripe_connect.py:303`) and `create_artist_dashboard_link` (`stripe_connect.py:411`) used `artist["user_id"] != user.id` — secondary users couldn't onboard or open the Express dashboard. Replaced with `check_artist_access`.
  - **Probe High #7 + #8 (silent failures)**: artist whole-gig cancel UI (`artist.book-gigs.js:1513`) and waitlist leave UI (`:1539`) swallowed FastAPI's `{detail}` body — slot-cancel sibling was upgraded earlier but these two were missed. Added the same defensive read + `showStyledModal` error path. Wrapped success path in `try/finally` so the button always re-enables even if a downstream await hangs.
  - **Probe High #9 (data + multi-user)**: `tax.py:send_1099` notification INSERT used wrong column `type` (schema is `notification_type`) and only notified `artist.user_id`. Both bugs wrapped in try/except so they failed silently — venue saw "Send 1099" succeed while the artist got only the email and no in-app notification, and co-managers got nothing. Replaced with `notification_service.create_notification` and `get_all_entity_users` fan-out.
  - **Probe High #10 (money flow)**: `book_gig`, `book_slot`, and `_run_prebooking_checks` (which `book_with_contract` uses) accepted bookings even when the artist's `entity_payment_settings.stripe_connect_onboarding_complete=0`. A direct API call (or stale frontend state) produced a confirmed booking whose payout would silently `transfer_failed` next day. Added a 402 `STRIPE_ONBOARDING_INCOMPLETE` gate to all three booking paths. Skipped when `payments_enabled='0'` (admin/test).
  - **Probe High #11**: `get_artist_venues` waitlist subquery (`artists.py:419`) filtered out re-listed gigs (`'open'`, `'cancelled_blast'`) — the artist's "Venues" tab silently dropped their position when a waitlist-trigger gig was re-listed. Widened the IN-clause.
  - **Probe High #12**: hardened the frontend `is_admin` defensive checks at `auth.guard.js:31` and `user-profile.js:120` to handle `1`/`'1'` in addition to `true`/`'true'`. Post-migration the JSON always carries a real bool, but multiple paths to the same value need belt-and-suspenders.
  - **Probe Medium #13**: `delete_account` artist branch left orphan `gig_waitlist` and `waitlist_offered` rows — future waitlist offers would FK-reference a deleted artist. Added DELETEs alongside the other cleanup.
  - **Probe Medium #19**: affiliate Stripe onboard URLs (`affiliate.py:478`) were hardcoded to `gigsfill.com`. Now read from `platform_settings.base_url` so staging / custom-domain deploys land back at themselves.
  - **Probe Medium #20**: `affiliate_stripe_onboard` had no role check — any logged-in user could spawn empty Stripe Express accounts. Added a check for `users.affiliate_code IS NOT NULL`.
  - **Probe Medium #21**: `get_artist_w9_status` (`tax.py:194`) was unauthenticated — any visitor could probe whether a specific artist had filed a W9. Added `Depends(get_current_user)`.
  - **Skipped #16** (local `_check_artist_access` in tax.py duplicates `utils.check_artist_access`): the local helper returns truthy/falsy while the canonical raises — replacing requires touching all call sites and offers no behavioral benefit. Stylistic duplication only.
  - Bumped `artist.book-gigs.js?v=135`. Restored `www-data:www-data` ownership on every touched file. API + scheduler restart clean; `/docs` 200; auth gate still rejects unauthenticated probes correctly.
- **2026-05-08 — `datetime.utcnow()` sweep across all backend files (73 occurrences in 18 files).** Python 3.12 deprecates `datetime.utcnow()` in favor of timezone-aware `datetime.now(timezone.utc)`. The codebase stores naive UTC everywhere, so a deprecation-silencer that preserves naive-UTC semantics was needed. Last night's pass cleared 9 sites in `gigs.py`. This pass:
  - Promoted `_utcnow_naive()` from a `gigs.py` local to a canonical `utcnow_naive()` in `backend/utils.py`. `gigs.py` keeps a `_utcnow_naive` alias for back-compat with the call sites already in place.
  - Swept all remaining 17 files (`payout_scheduler.py` + 16 in `routes/`): added `from backend.utils import utcnow_naive` and `s|datetime.utcnow()|utcnow_naive()|g`.
  - Mid-pass repair: the initial sed pattern split each file's `from datetime import ...` line in two — written a Python repair that detected the broken pair and rejoined them. All 14 affected files repaired and confirmed parseable.
  - Restored `www-data:www-data` ownership on every touched file per the CLAUDE.md ownership rule (keeps the email-template auto-export and any other app-side writes working).
  - **Verified**: zero `datetime.utcnow()` calls remain anywhere in `backend/`; api + scheduler restart cleanly; both workers up; `/docs` 200; no import errors in logs.
- **2026-05-08 — `is_admin` migration: TEXT 'true'/'false' → INTEGER 0/1 (Known issue #2).** The fragility documented in CLAUDE.md is closed. Six users in production: 1 admin (id=1), 5 non-admin. Migration steps:
  - **Data**: `UPDATE users SET is_admin = CASE WHEN LOWER(...) IN ('true','1') THEN 1 ELSE 0 END`. Note: SQLite stores integers in a VARCHAR column with TEXT affinity, so the values are now `'1'`/`'0'` as strings rather than true integers — but that's transparent to the codebase via the new `to_admin_bool` helper.
  - **Schema declaration**: `backend/db.py:217` changed to `is_admin INTEGER DEFAULT 0` so new deployments get clean integer typing.
  - **ORM**: `backend/models.py:24` is now `Column(Boolean, default=False)`. SQLAlchemy reads existing TEXT `'1'`/`'0'` values correctly.
  - **Helper**: new `to_admin_bool(v)` in `backend/utils.py` tolerates every form the column has had — `bool`, `int`, `'true'`/`'false'`, `'1'`/`'0'`, `None`, garbage. Use this anywhere `is_admin` is read in Python or serialized to JSON.
  - **Critical fix**: `routes/admin.py:check_admin` previously matched ONLY the literal string `'true'` for string values — would have locked the admin out the moment values normalized to `'1'`. Now uses `to_admin_bool`. Same fix applied to canonical str-based checks in `routes/affiliate.py:55`, `routes/tax.py:550`, `routes/stripe_connect.py:621`, `routes/emails.py:39`.
  - **Serialization**: `/api/me` (`routes/me.py:43,46`) and admin user-list (`routes/admin.py:269`) now coerce `is_admin` to a real bool before returning JSON. Frontend defensive checks (`auth.guard.js:31`, `user-profile.js:120`) keep working since they handle both `true` and `'true'` — but the JSON now always carries true booleans.
  - **Frontend hardening**: `index-init.js:40` had a latent bug — `if (!me.is_admin)` returned False for the legacy string `'false'` (truthy non-empty string), so non-admins could be redirected to admin.html if the redirect URL contained it. Now uses the same defensive multi-form check `auth.guard.js` uses.
  - **Write site**: `routes/auth.py:380` (auto-promote first user to admin) now writes `1` instead of `'true'`.
  - **Verified**: 12-case smoke test of `to_admin_bool` against every legacy + new form passes; API restarts cleanly; both workers up; `/docs` 200.
- **2026-05-08 — Pre-launch config audit + Stripe webhook surface audit.**
  - **Config audit verdict: clean.** Walked the documented going-live checklist against actual production state — all items set correctly: `GIGSFILL_SECRET_KEY` and `SESSION_SECRET_KEY` in systemd drop-ins (mirrored across api + scheduler units), `GIGSFILL_ENV=production`, `CORS_ORIGINS=https://gigsfill.com`, `WEB_CONCURRENCY=2` (matches 1-vCPU droplet), `RATELIMIT_STORAGE_URI=redis://localhost:6379` (Redis active), Stripe keys are LIVE (`sk_live_`/`pk_live_`), webhook secret set + endpoint rejects bad signatures with 400, all SMTP creds set under `platform_email_*` naming (mail.gigsfill.com:26) plus separate `support_email_*` set, base/site URLs match, hardcoded URLs in `routes/stripe_connect.py:348-349` match `base_url`, `robots.txt` + `sitemap.xml` reference correct domain, `auth.guard.js` `VERIFY_EXEMPT` is tight (only `verify-email.html` + `user-profile.html`) with proper admin override pattern that handles both boolean and TEXT `is_admin`.
  - **Webhook audit verdict: status filters correct, two notification gaps closed.** The other three Stripe webhook handlers (after the May 8 dispute fix) all use correct status filters: `transfer.created` matches `'transferred'` as a backstop for crash-mid-tick scenarios (`payout_scheduler.py:166/430/681` set it briefly before `'paid'` is confirmed); `payment_intent.payment_failed` matches `('processing','scheduled','charge_retry')` — all three are real states used in the scheduler; `account.updated` correctly flips `stripe_connect_onboarding_complete=0` which the scheduler's transfer query at `payout_scheduler.py:569` honors. Two UX gaps closed:
    - `payment_intent.payment_failed` previously only sent an admin alert. The scheduler's synchronous-decline path (`_handle_charge_failure`) emails the venue, but the async webhook-caught decline left venues unaware their card had failed until the next attempt also bounced. Now sends the venue a "Card declined" email with reason + link to update their card.
    - `account.updated` previously only sent an admin alert when Stripe restricted an artist's Connect account. The artist would discover the restriction only when their next payout silently failed. Now emails the artist directly with a reconnect link to their artist Payments tab.
  - **Soft note**: only one Stripe webhook secret is configured (`admin_stripe_webhook_secret`). If a separate platform vs Connect webhook endpoint is ever added in the Stripe dashboard with a different signing secret, only one will verify — would need an `admin_stripe_connect_webhook_secret` setting + secret-selection logic. Current single-endpoint setup is fine.
- **2026-05-08 — IDE diagnostics cleanup in `backend/routes/gigs.py`.** Cleared the actionable hints surfaced by pyright on the file:
  - **9 × `datetime.utcnow()` deprecation hints** — replaced via a new `_utcnow_naive()` helper at module top that returns a naive UTC datetime (`datetime.now(timezone.utc).replace(tzinfo=None)`). Drop-in identical semantics; storage everywhere in this codebase remains naive UTC. The other 10 backend files using `datetime.utcnow()` (payout_scheduler.py, main.py, etc.) are out of scope for this pass — handle when next opened in the IDE.
  - **Duplicate import** — removed redundant `from backend.services.email_dispatch import format_email_date` inside `cancel_gig`'s waitlist branch (already imported at module top).
  - **Unreachable code** — `if True: ... else: result["slots"] = []` inside `get_gig_detail` (~line 5349). Removed the dead `else` branch and the `if True:` wrapper; slot fetch is unconditional.
  - **Skipped intentionally**: every `user=Depends(get_current_user)` "not accessed" hint — those are FastAPI dependency injections that ENFORCE auth on the route. Removing the parameter would silently disable authentication. Same for underscore-prefixed locals (Python convention for intentionally unused). `_run_prebooking_checks` is called cross-module from `contracts.py:1959` — pyright's "not accessed" was scoped to gigs.py only.
- **2026-05-08 — Five-probe deep-dive: contract holds, approval replay, dispute SQL, recurring rollback/cap, admin protected-tables.** Follow-up to last night's audit; all five concerns are now fixed.
  - **Probe 1 (CRITICAL — multi-slot orphans)**: `cleanup_expired_holds` (`backend/routes/contracts.py:2454`) filtered on `g.status IN ('pending_contract','awaiting_venue_contract')`. Multi-slot gigs keep `g.status='open'` and only flip the slot row, so multi-slot holds NEVER matched and the artist was pinned forever. Rewrote to: match on `contract_hold_expires_at < now` (status-agnostic) + LEFT JOIN to `gig_slots` so multi-slot is caught; reset the held artist's slot row(s) (status='open', artist_id=NULL, pay restored); set `last_cancelled_artist_id` so the released artist isn't immediately re-blasted; strip the held artist's logo from the flyer (or delete the flyer if no bookings remain); send cancellation emails via the same dispatcher cancel paths use; fire `fire_cancelled_gig_blast` for short-lead gigs. cleanup_gig_records is also called defensively in case any txn rows exist.
  - **Probe 2 (CRITICAL — duplicate emails on token replay)**: `approve_booking` (`gigs.py:3559`) and `deny_booking` (`gigs.py:3713`) had unconditional UPDATEs. A double-clicked email link or refresh would re-fire the entire post-block — venue + artist got duplicate booking-confirmation or denial emails plus duplicate notifications, and deny additionally double-pinged the waitlist. Fix: conditional UPDATE `WHERE id = :sid AND status = 'pending_venue_approval'` + rowcount check at every claim point (slot UPDATE, gigs UPDATE, gig-level backstop UPDATE). On rowcount=0 the request short-circuits with `{"ok": True, "already_approved": True}` / `"already_denied"` and skips the email/notification block.
  - **Probe 3 (CRITICAL — chargebacks unflagged)**: `charge.dispute.created` webhook in `routes/stripe_connect.py:1812` had a broken IN-subquery `SELECT payment_intent FROM transactions ...` referencing a non-existent column. The query threw on every dispute, the except swallowed it, txn was always None, every chargeback fell through to "Transaction Not Found" — venue never auto-suspended, GigsFill profit accounting never updated, admin alert misleading. Fix: resolve the PaymentIntent id from the dispute's `payment_intent` field directly (newer Stripe API) or fall back to `stripe.Charge.retrieve(charge_id).payment_intent`; query `WHERE t.stripe_payment_intent_id = :pi OR t.stripe_transfer_id = :ch`, biased to `transaction_type='venue_charge'` so multi-slot disputes hit the parent. Admin alert claw-back figure now SUMs the children's `artist_payout_cents` (parent's own is 0 by design on multi-slot).
  - **Probe 4 (medium — partial-failure UX + cap + N+1)**: three smaller items in the recurring-gig path.
    - Frontend `createRecurringGigs` (`venue.create-gigs.js:2982`) now wraps the per-occurrence loop in try/catch; failed dates accumulate into `_failedDates` and the user sees "X created, Y skipped" with the first 6 reasons listed instead of a silent return.
    - `generateRecurringDates` hard-caps `maxWeeks` at 104 (2 years) — prevents fat-fingered "1000 occurrences" from spawning hundreds of gigs.
    - Calendar endpoint `list_venue_gigs` (`gigs.py:985`) was N+1: one slot SELECT per gig. Refactored to a single `WHERE gs.gig_id IN :gids` (using `bindparam(expanding=True)`) + Python grouping. At a venue with a 100-occurrence series this drops 100 queries off every calendar render.
  - **Probe 5 (medium — admin direct mutation bypass)**: generic admin DB-tools (`routes/admin.py:1862, 1907, 1929`) protected only `users` and `platform_settings`. Admin could DELETE/UPDATE/INSERT directly into `gigs`, `gig_slots`, `transactions`, `gig_contracts`, `flyers`, etc. — bypassing every cleanup helper. Centralized `_PROTECTED_TABLES` includes those plus `payment_cancellations`, `venue_payment_overrides`, `entity_payment_settings`. Applied the guard to all three endpoints (was missing on update_row entirely).
  - Bumped `venue.create-gigs.js?v=93`.
- **2026-05-08 — Race condition closed on simultaneous slot booking.** `book_gig` (`gigs.py:1689`) and `book_slot` (`gigs.py:3380`) both did a status SELECT, then later UPDATEd `gig_slots SET status='booked' WHERE id=:sid` with no status guard between the two. Two artists hitting "Book this slot" within the same few ms could both pass the prior status check and both write — last write wins, first artist already had a booking-confirmation email and a transaction row pointing at a slot that got reassigned to the other artist (the `_create_booking_transaction` existing-payout guard catches some of the txn collision but not the slot-pointing-elsewhere bug). Fix: conditional `WHERE id = :sid AND status = 'open'` (book_gig) and `WHERE id = :sid AND (status='open' OR (status='pending_venue_approval' AND artist_id=:aid))` (book_slot, which still supports re-submitting a pending slot per the existing logic at ~line 2971). Check `rowcount`; if 0, raise 409 `SLOT_TAKEN: This slot was just booked by someone else`. Frontend already surfaces FastAPI's `{detail}` body so users see a clear "refresh and try a different slot" message.
- **2026-05-08 — Audit fix #12–#19: medium-tier hardening sweep.** Eight items closed in one pass; all small but each addresses a real fragility surfaced in tonight's audit.
  - **#12 (medium)**: `cleanup_gig_records` slot-level "single-type, no parent" branch in `services/gig_cleanup.py:84` previously deleted ALL `venue_charge`/`single` rows on the gig regardless of artist. Now scoped with `artist_id = :aid OR artist_id IS NULL` so unrelated rows are not collateral damage.
  - **#13 (medium)**: `_recompute_gig_fees` (`gigs.py:177`) silently no-op'd when parent status wasn't `scheduled`/`test`. Now logs a warning so future incidents leave a trail (the no-op is intentional safety, but if recompute is REQUESTED on a charged parent something upstream may be wrong).
  - **#14 (medium)**: legacy `DELETE /gigs/{id}` (`gigs.py:2138`) silently deleted charged transactions and only notified the primary artist user. Now (a) refuses with 409 `CHARGED_TRANSACTION_EXISTS` if any txn is in `charged`/`paid`/`transferred`/`transfer_failed`/`pending_transfer`; (b) fans out via `notify_gig_cancelled` to all entity users; (c) sends cancellation emails via `send_cancellation_emails` with the slot times included.
  - **#15 (medium)**: `_open_blast_bypass_active` (`gigs.py:1089`) granted the bypass on time-window alone — non-preferred artists could book the moment the gig entered the window even before any blast email had fired. Now requires evidence: `gig_email_log` row for the matching `notification_key` on this gig must exist before the bypass activates.
  - **#16 (medium)**: legacy `update_recurring_gigs` (`gigs.py:2548`) updated `gigs.start_time/end_time/pay` but never propagated to `gig_slots`, leaving slot rows stale. Added a parallel UPDATE on open slots within affected gigs so downstream reads (gig list, public flyer, slot booking) see consistent values.
  - **#17 (medium)**: duplicate `isGigEndPassed` definition in `app/static/js/venue.create-gigs.js:694` — identical bodies; deleted the second copy. Bumped cache to `?v=92`.
  - **#18 (medium)**: `_create_booking_transaction` early-return on free-trial venues (`gigs.py:276`) left no audit trail — analytics joining gigs ⨝ transactions treated free-trial gigs as missing data. Now inserts a `transaction_type='free_trial'` row with `payment_method_type='free_trial'`, `status='free_trial'`, and `amount_cents` set so reporting can show "what would have been charged" for direct-pay bookings.
  - **#19 (medium)**: `delete_gig_with_slots` keep_open branch (`gigs.py:4044`) raw-deleted transactions, bypassing `payment_cancellations` cleanup, contract PDF file deletion, `gig_contracts` cleanup, and contract-related notifications. Replaced with per-artist `cleanup_gig_records(db, gig_id, artist_id)` calls (which also fire `_recompute_gig_fees` on any remaining parent venue_charge). Falls back to gig-level cleanup when no booked slots exist.
- **2026-05-08 — Audit fix #5–#10: blackout check, multi-user notify, error surfacing, pending-status dedupe, error-swallow removal, in-progress edit guard.** Six findings from tonight's audit.
  - **#5 (HIGH)**: `book_gig` and `_run_prebooking_checks` (used by `book_with_contract` too) had no artist-blackout check. `book_slot` enforced it inline (`gigs.py:3091`); the single-slot and contract paths let blacked-out artists through. Fix: added the same `artist_availability` lookup in both — book_gig before the pay-override block (~line 1490) and `_run_prebooking_checks` as check #6 right before its return.
  - **#6 (HIGH)**: `book_slot` notifications skipped secondary entity users. Raw INSERTs at `gigs.py:3247-3281` notified only the primary `artist_user_id` / `venue_user_id`. Multi-user accounts got booking emails (which fan out via `get_all_entity_users`) but no in-app notification. Fix: replaced the raw INSERT block with a single `notify_gig_booked(...)` call — same helper `book_gig` already uses. Pass the slot's `start_time` so the notification message includes the right time.
  - **#7 (HIGH)**: artist slot-cancel UI silently swallowed errors. `artist.book-gigs.js:1467` catch block reset the button text without surfacing anything — once the cancel_slot authz from audit #1 lands, legitimate failures would be invisible. Fix: read FastAPI's `{detail}` body, throw with that text, show via `showStyledModal('Cancellation Failed', ...)`. Bumped `?v=134`.
  - **#8 (HIGH)**: `book_slot` "already booked" check matched only `status='booked'`. Artist with a `pending_venue_approval` / `pending_contract` / `awaiting_venue_contract` slot could request a second slot on the same gig. On approval, `_create_booking_transaction`'s existing-payout guard would silently refuse the second insert, leaving a slot booked without an artist_payout child → fee imbalance. Fix: widened the check to include those in-transit statuses, and excluded the caller's own `slot_id` so re-submitting a still-pending slot still works.
  - **#9 (HIGH)**: `delete_gig_with_slots` loaded `gig` info inside a try/except that swallowed errors. If the SELECT failed, `gig=None` and the entire post-cleanup block (notifications, emails, blast) was skipped — yet slot/gig data had already been mutated. Fix: removed the swallow; load now raises 500 on failure BEFORE any mutation, or 404 if missing.
  - **#10 (HIGH)**: `update_gig` (PUT /gigs/{id}) had no backend in-progress guard. The frontend hides Save Changes when a gig is mid-window (Changelog 2026-05-08), but a stale tab or direct API call could still corrupt an in-progress gig. Fix: extended the initial gig-load to include `date`, `start_time`, `end_time`; refuse with 409 `GIG_IN_PROGRESS` if `now >= start_time`.
- **2026-05-08 — Audit fix #3 + #4: same-day-approval slot-update + book_slot clears last_cancelled_artist_id.** Two more findings from tonight's audit.
  - **#3 (CRITICAL — data integrity)**: `book_gig` same-day path (`gigs.py:1507`) UPDATEd `gigs` to `pending_venue_approval` but never touched `gig_slots`. On approval, `approve_booking`'s gig-level branch (`gigs.py:3392`) flipped `gigs.status` to `booked` without updating the slot — slot stayed `open`, transactions got `slot_id=None`, downstream cancel paths matching by `gig_slots.artist_id` couldn't find the booking. Fix has two pieces: (a) `book_gig` now also marks the open slot `pending_venue_approval` with `artist_id=aid` and `approval_requested_at=now`; (b) `approve_booking`'s gig-level backstop branch promotes the open slot to `booked` and passes `slot_id` to `_create_booking_transaction` so legacy pre-fix data (gigs flagged pending but no matching slot row) still heals on approval.
  - **#4 (HIGH — silent data corruption)**: `book_slot` (`gigs.py:2922`) didn't clear `gigs.last_cancelled_artist_id` when a new booking lands. After an artist cancels a slot on a multi-slot gig, then a different artist books a slot, the original canceller's id stayed pinned. Any future cancellation triggering a blast on this gig silently filters out the original canceller forever. Fix: mirror `book_gig`'s line ~1586 — clear `last_cancelled_artist_id` right after marking the slot booked.
  - **Bonus**: dropped the dead `:gig` SQL parameter binding at `gigs.py:1516` (audit issue #11) since I was already in that block.
- **2026-05-08 — Audit fix #1 + #2: cancel_slot authz + cancel_gig over-deletion on multi-slot.** Two findings from tonight's full booking/cancel audit, both verified and fixed.
  - **#1 (CRITICAL — security)**: `cancel_slot` (`backend/routes/gigs.py:3527`) had no authorization check. Any authenticated user could DELETE any slot booking, wipe transactions, fire cancellation emails, and (with `remove_slot=True`) delete slot rows or the entire gig. Fixed by mirroring `cancel_gig`'s authz pattern (lines 1693-1718): caller must have access to either the venue or the slot's booked artist; otherwise 403. Also force-correct `cancelled_by` to match the caller's actual access (a venue user can't mislabel a cancellation as artist-initiated to spoof email subjects).
  - **#2 (CRITICAL — money)**: `cancel_gig` venue-cancel branch at line 1788 called `cleanup_gig_records(db, gig_id)` with no `artist_id` BEFORE the safety check at line 1812 that forces `keep_open=True` on multi-slot gigs. The unscoped cleanup deletes ALL transactions on the gig — wiping other booked artists' transaction rows even though their slots survive. Fixed by passing `effective_result["artist_id"]` so cleanup is scoped to just the cancelled artist. The "delete entire gig" branch below still runs full cleanup via `delete_gig_completely` so nothing leaks.
- **2026-05-08 — Slot time row added to every email template that has a Date.** Audit found 6 templates with a Date field but no time information: `artist_gig_cancelled`, `venue_gig_cancelled`, `artist_payment_sent`, `venue_payment_charged`, `artist_venue_payment_issue`, `venue_contract_sign_needed`. Added a new `{{slot_times}}` placeholder that resolves to a human-readable string — for single-slot or per-artist contexts: `"7:00 PM - 9:00 PM"`; for multi-slot venue-wide contexts: `"7:00 PM - 9:00 PM | 9:00 PM - 11:00 PM"`. Implementation:
  - Templates: 4 table-style emails got a new `<tr>` "Time" row right after the Date row; 2 prose-style emails (`artist_venue_payment_issue`, `venue_contract_sign_needed`) had the inline date mention extended to `on {{date}} ({{slot_times}})`.
  - Helper `compute_slot_times(db, gig_id, artist_id=None)` added to `backend/services/email_dispatch.py` (SQLAlchemy version) and a parallel `_compute_slot_times_sqlite(conn, gig_id, artist_id=None)` added to `backend/payout_scheduler.py` (raw sqlite version). Both query `gig_slots WHERE status='booked'` first; per-artist returns that one slot's window, multi returns slot summary joined by `" | "`, fallback to `gigs.start_time/end_time`.
  - Wired into 5 dispatch sites: cancellation `cancel_vars` in `email_dispatch.send_cancellation_emails` (prefers passed-in slot times when present, falls back to helper); `email_dispatch.send_contract_sign_email` `email_vars`; `payout_scheduler._send_payout_email` (per-artist); `payout_scheduler._send_venue_charged_email` (gig-wide); `routes/stripe_connect._notify_artists_payment_issue` (per-artist).
  - File→DB sync: relied on `_populate_email_templates` UPDATE behavior — confirmed all 6 rows received the change after restart. Verified via audit: every template with `{{date}}` now also carries time information (`{{slot_times}}`, `{{slots_html}}`, or explicit `{{start_time}}`/`{{end_time}}`). `chown www-data:www-data` on `email_templates.py` so the admin auto-export path keeps working.
- **2026-05-08 — Stale flyer thumbnail kept showing cancelled artist's logo (artist cancel path).** Reported: when an artist cancels their slot on a multi-slot gig, the venue's flyer still showed the cancelled artist's logo on the public view. Audited the full artist-cancel flow (`cancel_slot` → `cleanup_gig_records` → `_recompute_gig_fees` → notifications → cancellation emails → `_delete_flyer_if_no_bookings_remain` → `_remove_artist_logo_from_flyer`) and confirmed every step ran correctly — backend logs explicitly showed `[FLYER] Removed 1 object(s) tagged artist_id=3 from gig 507's flyer`, and the canvas_data in DB had the cancelled artist's logo stripped. Root cause was downstream: the public flyer endpoint at `backend/routes/gigs.py:4920` PREFERS `thumbnail_data` (a JPEG snapshot) over `canvas_data` for fast rendering, but `_remove_artist_logo_from_flyer` only updated `canvas_data` — the JPEG taken at the venue's last save still contained the cancelled artist's logo, so the public view kept showing it until the venue manually re-saved the flyer. Fix: `_remove_artist_logo_from_flyer` now also sets `thumbnail_data = ''` when it modifies the canvas. With no thumbnail, the public endpoint falls through to canvas_data live-rendering (Fabric in browser), which now reflects the post-cancel state. Cleared the existing stale thumbnail on flyer 213 as a one-time backfill so the user sees the correct render immediately. Other parts of the artist-cancel chain audited intact: transaction child cleanup + parent venue_charge recompute (via `_recompute_gig_fees`), slot reset to open, parent gig re-opened, `last_cancelled_artist_id` set, waitlist/blast triggered, cancellation emails sent with `cancelled_by='artist'` for correct subject template.
- **2026-05-08 — Edit-gig dialog showed Save Changes / Delete Gig on in-progress gigs.** Reported: a multi-slot gig that was in progress (turned black on the calendar) still let the venue open Edit Gig and see both Save Changes and Delete Gig — should be blocked just like the booked-gig view already does. Cause: the prior in-progress gating only existed in two places — the `_showBookedGigModal` view (which adds an "in progress" notice and hides the Edit Gig button) and the recurring/edit-recurring flow. The regular open-gig edit path in `app/static/js/venue.create-gigs.js:1985-1994` (the branch hit when clicking an open gig that has no bookings yet) unconditionally showed Save Changes + Delete Gig regardless of whether the gig was happening. Fix: added an `isGigStartedToday(gig) && !isGigEndPassed(gig)` check before that block — when in-progress, hide both buttons, set the modal title to "Gig In Progress", and render the same grey "⏰ This gig is in progress" notice block used elsewhere. Also added the same defense-in-depth guard inside `openBookedGigEdit` (~line 2228) so even if the user reaches the booked-edit path through an alternate entry or race condition, Save Changes is hidden when the gig is currently happening. Bumped `venue.create-gigs.js?v=91`.
- **2026-05-08 — Slot cancel silently failed (`slot.id` was undefined) + added Keep-Open vs Remove-Slot choice.** Reported: clicking ✕ on a multi-slot booked slot showed the right modal but didn't actually cancel the booking — artist stayed on the slot, no email. API logs revealed the request URL was `DELETE /api/gigs/507/slots/undefined/cancel → 422`. Two bugs:
  - **Field-name mismatch**: `/venues/{vid}/gigs` (cached gig list) aliases `gs.id AS slot_id` in the SQL at `backend/routes/gigs.py:943` (and 4 other read endpoints). The standalone `/api/gigs/{id}/slots` returns `gs.id` un-aliased. Frontend reads `slot.id` from the cached list — which is `undefined` — and the cancel button rendered with `slots/undefined/cancel`. The frontend's fetch had no `if (!resp.ok)` check, so the 422 was swallowed; modal closed with success alert despite failure.
  - **Missing UX option**: the slot-cancel modal only offered re-list-as-open; venues had no way to actually remove a slot from a multi-slot gig.
  - **Fix**:
    1. Frontend: normalize `slot.id ||= slot.slot_id` at all 3 sites that read cached `gig.slots` (`venue.create-gigs.js:1960, 2125, 2321`).
    2. Frontend: added `if (!resp.ok)` checks to both slot-cancel paths (CASE 2 and CASE 3) so errors surface to the user instead of silently appearing to succeed.
    3. Frontend: extended `_showCancelOverlay` with a new `slotMode` flag that renders two radio options — "Keep slot open (re-list as available to book)" vs "Remove this slot (slot deleted from gig)". The Remove option is gated on `canRemove: totalSlots > 1` to prevent removing the last slot via this modal (single-slot gigs go through the gig-level cancel path).
    4. Backend: `cancel_slot` (`backend/routes/gigs.py:3520`) now reads `remove_slot: bool` from the request body. After running the existing cleanup + emails + waitlist notifications (so the artist is notified either way), if `remove_slot=True` the slot row is DELETEd and remaining slots are renumbered contiguously sorted by start_time. If 0 slots remain, the gig itself is deleted via `cleanup_gig_records` + `DELETE FROM gigs`.
    5. Bumped `venue.create-gigs.js?v=90`.
- **2026-05-08 — Multi-slot ✕ button on a non-`booked`-status slot showed the wrong modal.** Reported: venue clicks the red ✕ on Slot 2 of a multi-slot gig (Fifty Proof booked it). The "Remove Slot 2?" modal appeared with body "This open slot has no artist booked" — wrong for a slot with a real artist on it. Cause: `cancelSlotBooking` in `app/static/js/venue.create-gigs.js:2546` decided the booked-vs-open branch with `slot.status === 'booked'`. But `gig_slots.status` can also be `pending_contract`, `awaiting_venue_contract`, `awaiting_venue_upload`, `pending`, etc. during the contract flow — every one of those has an artist assigned but isn't literally `'booked'`. Plus, if the `/api/gigs/{id}/slots` fetch ever fails (network blip, auth glitch), `slots` is `[]`, `slot` is undefined, `isBooked` is false, and the user falls into the same wrong branch. Fix: (1) check `slot.artist_id != null` instead — that's the actual "is there an artist on this slot" question. (2) Pass `slot.artist_id` from the button onclick context as a fallback hint so the modal stays correct even if the slots fetch fails. (3) Bumped `venue.create-gigs.js?v=89` so browsers fetch the new code.
- **2026-05-08 — Flyer editor save-success was silent — added a green toast.** Reported: clicking any of the 3 Save options in the flyer editor menu (Save Gig Flyer, Save as Default Template, Save as New Template) gave no visible confirmation, so the user couldn't tell if the save worked. Cause: every save handler was correctly calling `setStatus('✓ ... saved')`, but `setStatus()` in `app/static/js/flyer-editor.js:1889` had `if(msg.startsWith('✗'))` and silently dropped any message that didn't start with the error glyph. Fix: rewrote `setStatus()` to render BOTH success and error states (success auto-dismisses after 2.5s, errors stay 5s), and added a new `feShowToast()` helper that displays a clearly-visible green/red floating popup near the top of the editor for the same duration. Mounted inside `flyerEditorOverlay` so the toast sits above the canvas. Bumped `flyer-editor.js?v=3` on `venue-create-gigs.html` and `admin.html`.
- **2026-05-08 — Multi-slot artist-logo persistence broken (`_tplArtistId` was being stripped on save).** Reported on the venue Create Gigs flyer editor: changing an artist's logo on a multi-slot gig, clicking Save Gig Flyer (this gig only), then closing/reopening — the change appeared to revert. Investigation showed the SAVE was succeeding (PUT 200, canvas_data updated in DB) but the LOAD path was overwriting the user's selections. Two related bugs:
  - **Save bug**: `getCanvasJSON()` in `app/static/js/flyer-editor.js:942` calls `canvas.toJSON([...])` with an explicit allowlist of custom Fabric properties to serialize. The list omitted `_tplArtistId` — the property the multi-slot artist-logo picker tags each image with (added 2026-05-07). So every save silently dropped the artist binding. Two follow-on consequences: (a) on reopen, hydrate couldn't tell which artist each image was for, and (b) the backend `_remove_artist_logo_from_flyer` cancel-cleanup helper in `routes/gigs.py:106` looks for `_tplArtistId` in the saved JSON to identify the cancelled artist's logo — that whole feature has been silently broken since it was added because the property was never persisted.
  - **Hydrate bug**: `hydrateTemplateVars()` line ~777 used `gigInfo.artist_picture_url` for ALL `_tplVar='artist_logo'` images. On a multi-slot gig with two artists, both logos got overwritten with the same primary-artist picture on every reopen.
  - **Fix**: (1) Added `_tplArtistId` to the toJSON allowlist in `getCanvasJSON()`. (2) `hydrateTemplateVars()` now resolves the URL per image: if an `artist_logo` object carries `_tplArtistId`, look up that artist's `artist_picture_url` in `gigInfo.slots`; if the tagged artist is no longer booked, leave the saved image as-is (cancel-cleanup is the canonical removal path); if no `_tplArtistId`, fall back to the existing single-artist behavior. (3) When hydrate replaces an image with a fresh `fabric.Image`, copy `_tplArtistId` onto the new object so subsequent saves keep the binding. (4) Backfilled flyer 213 (the reported gig 507 row): each artist_logo image had its `_tplArtistId` set by parsing the artist id out of the existing `src` URL pattern `/artist/<id>/profile/...`. (5) Bumped script cache-bust to `?v=2` on `app/venue-create-gigs.html` and `app/admin.html` so the browser pulls the new JS.
- **2026-05-08 — Stripe fee on admin Accounting now matches Stripe to the cent.** Reported on the admin Payment Accounting view: gig 505's row showed Stripe Fee `$0.73` but Stripe actually charged `$0.74`. Cause: the calc was a formula estimate `int(actual_charge * 0.029 + 30)` which truncates the half-cent — Stripe rounds half-up. Fix has two pieces. (1) Capture the real fee at charge time: `payout_scheduler.py` now expands the PaymentIntent retrieve with `latest_charge.balance_transaction` and stores `bt.fee` into the existing `transactions.credit_card_fee_cents` column. (2) `admin.py:get_admin_accounting` prefers the stored real fee when present and falls back to the formula estimate only when 0 (legacy rows that never had it captured, or charge-time fetch failures). Backfilled all 10 historic charged transactions by re-fetching from Stripe — 9 were under-estimated by exactly $0.01 (the truncation gap), one refunded row (txn 33, gig 319) showed $0.45 actual vs $0.73 estimate because Stripe credited part of the fee on the refund, which the formula couldn't model. Files: `backend/payout_scheduler.py:240-275` (capture + UPDATE), `backend/routes/admin.py:1473-1500` (prefer stored).
- **2026-05-08 — Venue-charged receipt email broken (no such column: notification_type).** Caught when gig 505's $15 charge fired at 2026-05-08 00:00:53 UTC. Charge succeeded (PI `pi_3TUbqcGTPqz6PmNX2kr8tOcF`), parent set to `charged`, transfer step correctly skipped (child txn 288 was already `paid` from the May 7 incident — defense-in-depth guard worked exactly as designed, no double-pay). But the receipt email crashed: `_send_venue_charged_email` in `backend/payout_scheduler.py:1024` queried `email_templates` with `WHERE template_key = 'venue_payment_charged' OR notification_type = 'venue_payment_charged'` — and `email_templates` has no `notification_type` column (schema is just `id, template_key, subject, body, updated_at`). Likely a copy-paste from the `notifications` table query. The bare `OR <bad-column> = ...` threw, the function fell through to the except block, no email sent. Fix: removed the bogus OR clause, lookup is now `template_key` only (which is the correct unique column anyway). Manually re-fired the receipt email for txn 287 after the fix so the venue still got their notification. Scheduler restarted.
- **2026-05-07 — Multi-slot fee model: gig-level + proportional artist split + multi-name billing display.** Reported on the venue Payments tab: a 2-slot gig with $10 + $20 artist pays showed `Gig Fee=$10, Platform Fee=$30, Total Paid=$40`. Investigation surfaced TWO bugs and one design defect.
  - **Bug 1 (display-only)**: `_create_booking_transaction` updated parent `venue_charge.venue_charge_cents` + `commission_cents` on each new slot but never `amount_cents`, so it stayed pinned at slot 1's pay. The venue billing UI and the "you were charged" email both derive `gig_fee=amount_cents` and `platform_fee=venue_charge_cents-amount_cents` from the parent. Stripe charges off `venue_charge_cents` directly, so money flow was unaffected — only the breakdown was wrong.
  - **Design defect**: per-slot fee math caused the `platform_min_fee` ($10) to fire ONCE PER SLOT instead of once per gig. A 2-slot $10+$20 gig was hit with $20 in fees instead of $10. Multi-slot gigs were systematically over-charged in the min-fee regime.
  - **Fix — new fee model.** New helper `_recompute_gig_fees(db, gig_id)` in `backend/routes/gigs.py`. Computes `total_fee = max(SUM(slot pays) * platform_fee_percent, platform_min_fee)` ONCE for the gig, splits per `platform_fee_split` into venue + artist halves, then **distributes the artist half proportionally by pay** so each artist nets the same % of their slot pay (rounding remainder absorbed by the last child to tie sums exactly). Single-slot is the trivial case of the same math. Helper guards against running on parents past `'scheduled'/'test'` status (so already-charged gigs are never retroactively repainted). `_create_booking_transaction` now calls the helper after inserting the child; `services/gig_cleanup.py` slot-cancel branch calls it too after deleting the cancelled child (replaced the old `subtract` logic which assumed per-slot fees and would have produced incorrect totals after a cancel). Existing scheduled gigs are NOT bulk-backfilled; the new model takes effect on the next slot booking or cancellation. Gig 507 (the reported row) was manually run through the helper as a verification: $40 → $35 venue charge, $5 / $15 → $8.34 / $16.66 artist payouts, both artists now ~16.66% effective fee rate.
  - **Multi-name artist column.** `routes/stripe_connect.py:get_venue_transactions` SELECT for `artist_name` changed from `LIMIT 1` to `GROUP_CONCAT(a_slot.name, ', ')` so the venue billing row lists every booked artist on a multi-slot gig (was showing only slot 1's artist). Frontend `venue-stripe-payment.js:renderVenueBillingTable` now detects comma in the artist field and renders plain text rather than a single-artist profile link (only one `resolved_artist_id` is returned, so multi-name cells can't be cleanly per-artist clickable; left as a future enhancement).
  - Files: `backend/routes/gigs.py` (new `_recompute_gig_fees`, simplified `_create_booking_transaction` parent-update branch), `backend/services/gig_cleanup.py` (replaced subtract with delete+recompute), `backend/routes/stripe_connect.py` (GROUP_CONCAT artist names), `app/static/js/venue-stripe-payment.js` (multi-name plain render).
- **2026-05-07 — Per-venue timezone REGRESSION fix (re-applies the original 2026-05-04 fix).** Discovered while investigating tonight's gig 505 charge: the 2026-05-07 money-bug fix at 05:06 UTC inadvertently reverted the 2026-05-04 per-venue-tz fix in TWO places.
  - **Symptom 1**: `payout_scheduler.scheduler_loop()` stopped sweeping hourly. Journal showed "Running payouts sweep at <local time>" hourly through May 07 05:00 UTC, then ZERO sweep activity for ~17 hours afterward. The loop was gated to fire `process_payouts_now()` ONLY at the platform's `payment_processing_hour` in platform tz (=17:00 LA, once per day) instead of every hour.
  - **Symptom 2**: `routes/gigs.py:_create_booking_transaction` (line ~200) was just storing `hour=17` naively with no venue-tz conversion. So txn 287 for a Pacific venue was stored as `2026-05-07 17:00:00` (interpreted as 17:00 UTC = 10am Pacific) instead of `2026-05-08 00:00:00` (= 5pm Pacific = the user-intended payout time).
  - **Why it didn't show up sooner**: the two regressions exactly cancelled out FOR PACIFIC VENUES on a Pacific platform. The broken booking wrote 17:00 naive, the broken scheduler fired at 17:00 LA = 00:00 UTC May 8 = 5pm Pacific. NY/Eastern venues would have been 3 hours late, Hawaii venues 3 hours early — but no one noticed.
  - **Fix**: (a) `scheduler_loop` now tracks `last_swept_hour` and calls `process_payouts_now()` once per hour. The SQL gate `scheduled_process_at <= now` already filters to due txns, so hourly sweeps are idempotent. (b) `_create_booking_transaction` now reads `payment_processing_hour` from platform_settings and uses `get_venue_timezone_str(db, venue_id)` to compute "5pm next day in venue's local tz", converts to UTC, stores naive UTC. (c) txn 287's `scheduled_process_at` was manually corrected from `2026-05-07 17:00:00` to `2026-05-08 00:00:00` so it fires at the user-intended 5pm Pacific tonight, not at the (already-passed) wrong-tz time.
  - **Amount breakdown for txn 287 (clarifying note)**: gig 505 base pay was $10. With `platform_min_fee=10` and 50/50 split, total fee = $10 (the $10 minimum dominates the 1% calc). Venue's share = $5 added on top → `venue_charge_cents = 1500` ($15 charged to card). Artist's share = $5 deducted → `artist_payout_cents = 500` ($5 paid out via txn 288). The `amount_cents = 1000` field is the BASE pay only — the actual venue charge is `venue_charge_cents`. Common tripping point in conversation: saying "venue charge is $10" reads `amount_cents` instead of `venue_charge_cents`. Backups: `payout_scheduler.py.bak-20260507-2152-tzfix`, `routes/gigs.py.bak-20260507-2152-tzfix`. Verified post-restart: scheduler logged "Running payouts sweep at 2026-05-07 15:01 PDT / No pending payouts at 22:01:37" — hourly sweep working, txn 287 correctly waiting for 00:00 UTC. Note: `get_payout_time()` in `payout_scheduler.py` is now dead code (no callers in the live file) but left in place to minimize change surface near a live money flow.
- **2026-05-07 — Launch-readiness: `base_url`/`site_url` set in DB + `CORS_ORIGINS` set in env.** Both `base_url` and `site_url` were missing from `platform_settings` — code worked because of hardcoded `or "https://gigsfill.com"` fallbacks scattered across `auth.py`, `main.py`, `waitlist.py`, `messages.py`, `gigs.py`. Now explicitly set to `https://gigsfill.com`. `CORS_ORIGINS` was unset, so the code default of `http://127.0.0.1:8001` (from `backend/main.py:242`) applied — only matters if any browser fetch hits the API cross-origin. Added `CORS_ORIGINS=https://gigsfill.com` to `/opt/gigsfill/.env`. Both services restarted; verified `CORS_ORIGINS` is visible in the API process env, and `GIGSFILL_RUN_SCHEDULERS=1` remains set ONLY on the scheduler process (NOT API) — confirms the dual-process split is intact.
- **2026-05-07 — `/health` endpoint actually checks DB + secret config.** Was previously returning a static `{"status":"ok"}` with no real check — uptime monitors would lie if the DB was down. Now performs a `SELECT 1` against the DB and verifies `GIGSFILL_SECRET_KEY` is loaded into the env. Returns HTTP 503 with a `failed:` array listing which check(s) tripped. Stripe is intentionally NOT pinged — health-check coupling to an external service is its own footgun. If a deeper check is ever needed, add a `/health/deep` variant rather than slowing this one down. Resolves Known Issue #14. (`backend/main.py:421`)
- **2026-05-07 — Multi-slot artist logo picker + cancel-time logo cleanup.** Two improvements to the flyer editor for multi-slot gigs.
  - **Picker**: when a venue clicks "+ Gig Variables → Artist Logo" on a multi-slot gig with 2+ booked artists, a modal lists each artist with their profile pic. Click an artist → adds THEIR logo to the canvas (not just slot 1's). Single-slot or single-artist gigs skip the picker (existing direct-add behavior). Each added image gets a `_tplArtistId` Fabric.js property so we can find and remove it later. New helpers: `_showArtistLogoPicker(slots)` and `_addArtistLogoForSlot(slot)` in `app/static/js/flyer-editor.js` near `addGigVar`.
  - **Cancel-time cleanup**: when an artist is cancelled from a multi-slot gig and the flyer is preserved (because other slots remain booked), the cancelled artist's logo is automatically stripped from the saved canvas JSON, leaving everything else intact. New backend helper `_remove_artist_logo_from_flyer(db, gig_id, artist_id)` in `backend/routes/gigs.py` parses `flyers.canvas_data`, drops objects matching `_tplArtistId == artist_id`, saves back. Wired into all 4 flyer-cleanup callsites: artist-cancel branch, venue-keep-open branch, `cancel_slot`, and `delete_gig_with_slots`. Existing `_delete_flyer_if_no_bookings_remain` was extended to take an optional `cancelled_artist_id` and call the new helper when the flyer is being preserved (not deleted).
- **2026-05-07 — Multi-slot artist sees their own slot time on Earnings page.** `backend/routes/stripe_connect.py:get_artist_transactions` was returning `g.start_time` (the parent gig's start, which equals slot 1's time). For an artist booked on slot 2 (e.g., 9pm-11pm) on a 7pm-11pm multi-slot gig, the Earnings page showed 7pm. Fixed via subquery: `COALESCE((SELECT gs.start_time FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.artist_id = t.artist_id LIMIT 1), g.start_time) as gig_time`. Multi-slot artists now see the time they're actually performing.
- **2026-05-07 — Email Center: clickable recipients list + per-row Delete + venue_message_to_artists template fixes.** Several Email Center improvements stacked together.
  - **Recipients tracked + clickable**: `venue_email_history.recipients_json` (TEXT) added (idempotent ALTER TABLE on first send). `routes/venue_emails.py` send-email loop now collects each successful recipient's `{name, email}` and stores serialized JSON. Modal "To:" field renders as a chevron-toggle `▸ N artists` — click to expand to a name+email list. Old rows without `recipients_json` show non-clickable count (graceful degradation).
  - **Per-row Delete button**: 4th column `90px` added to history grid (was `180px 160px 1fr`). New `DELETE /api/venue-emails/history/{email_id}` endpoint with `check_venue_access` auth (so secondary venue users can also clean up shared history). Two-click confirm pattern (Delete → Confirm? red filled → actual delete with 3-second auto-revert) — avoids the ugly browser `confirm()` popup. **Important**: the email history UI lives inside `app/venue-create-gigs.html` (embedded as a tab), NOT the standalone `app/venue-email-center.html`. Two CSS rules and one inline `<style>` had to be updated in `venue-create-gigs.html` for the 4-column grid to apply. (Standalone `venue-email-center.html` got the same treatment for completeness but isn't actually loaded by the live UI.)
  - **Template substitution fix**: WYSIWYG editor in admin had split the `{{venue_name}}` placeholder with a font-size `<span>`, breaking literal-string substitution in `email_service.render_template`. Fixed `venue_message_to_artists` body in `email_templates.py`. Added a To: row showing `{{artist_name}}` between From: and Subject: rows. Added `artist_name` to per-recipient template_vars (loop builds dict per artist instead of sharing one dict across all recipients).
  - **Email export 500 fix**: `/api/email-templates/export` was failing silently because `/opt/gigsfill/backend/email_templates.py` had drifted to `root:root` ownership during a deploy. The export endpoint writes to that path and www-data couldn't. Fix was `chown www-data:www-data`. Also wrapped the export's bare-`except` to log the actual exception (preserved as the new pattern for future debug).
- **2026-05-07 — Status lifecycle on artist Earnings + venue Billing pages.** Both pages had inconsistent or wrong status labels for non-terminal txns.
  - Now: future gig (`gig_date+start_time > now`) shows **"Upcoming"** purple. Past gig with non-terminal status shows **"Processing"** orange (gig started, payout pending). Terminal statuses (`paid`, `payment_cancelled`, `payment_failed`, `transfer_failed`) use status map (Paid green, Cancelled red).
  - Same logic on both `app/static/js/artist-stripe-payment.js` and `app/static/js/venue-stripe-payment.js`.
  - Cancellation handling: pre-gig cancellations DELETE the transaction entirely (in `cleanup_gig_records`) so they never appear in history. Post-gig payment cancellations keep the row with `status=payment_cancelled` and show as "Cancelled" red. The frontend just renders whatever rows it gets — no client-side filter.
  - **`effective_status` for venue parents**: venue-side billing endpoint returns a computed column. Parent `venue_charge` rows stay `status='charged'` even after children are paid out — the literal status doesn't reflect the actual settled state. New CASE expression promotes parent to `effective_status='paid'` when all non-cancelled children are paid. Frontend uses `t.effective_status || t.status`.
  - **Edit Gig button hidden on in-progress gigs**: `venue.create-gigs.js` now hides the Edit Gig button when `_multiHasStarted` is true. Editing a gig mid-performance makes no sense (would send "we changed your gig" emails while artist is on stage).
- **2026-05-07 — CRITICAL money bug: payout scheduler firing transfers BEFORE venue is charged.** Caught while testing tonight's gig 505. Symptom: `artist_payout` child txn 288 was created at 01:39 UTC and transferred $5 to the artist's Stripe account at 02:00 UTC, even though the parent `venue_charge` txn 287 was still `status='scheduled'` for the next day's 17:00 UTC charge window. **Real money moved on the wrong schedule.** Root cause: artist_payout children were inserted with `status='pending_transfer'` at booking time (in `_create_booking_transaction` in `backend/routes/gigs.py`). The scheduler's hourly "retry stalled transfers" sweep matches on `status IN ('pending_transfer', 'transfer_failed')` — it caught freshly-created children and treated them as legitimately-stalled retries. Three-layer fix in `backend/payout_scheduler.py` and `backend/routes/gigs.py`:
  1. **Initial state**: child INSERT changed `'pending_transfer'` → `'scheduled'`. Now matches parent's initial state and indicates "waiting for normal processing flow."
  2. **Post-charge transfer query** in `_transfer_to_artists`: changed predicate to `status IN ('scheduled', 'pending_transfer')` so the legitimate post-charge fire path still works for both initial state and retry state.
  3. **Defense-in-depth retry-stalled query**: added `EXISTS` clause requiring the parent to be in `('charged', 'paid', 'transferred')` for `artist_payout` children, OR `stripe_payment_intent_id` set for legacy `'single'` rows. Even if a future code path creates a child with `pending_transfer` while parent is `scheduled`, this guard prevents pre-charge transfer.
  - Status lifecycle now: child created `scheduled` → parent fires charge → child set to `paid` by `_transfer_to_artists`. The `pending_transfer` status is now ONLY set when an actual transfer attempt was blocked (e.g., artist not onboarded), reserved for legitimate retry.
  - The orphan paid txn 288 + scheduled txn 287 was left in place as a controlled test case. Tomorrow's 17:00 UTC scheduler tick should fire the venue charge, find no children in `('scheduled', 'pending_transfer')` to transfer (288 is already `paid`), no double-pay. If the venue's card declines, GigsFill is out the $5 + Stripe processing fee — but the test artist (Fridays Past) IS the venue owner of 14 Cannons in this self-deal scenario, so net loss is contained to processing fees.
- **2026-05-07 — Frequency override now respected in modal-data endpoint.** Pre-existing bug surfaced when testing close-together gigs. The artist's gig modal showed a "⚠️ Frequency Limitation" banner based on the venue's default `artist_frequency_days` (28), ignoring the per-artist `preferred_artists.frequency_days_override` (set to 0 to allow unlimited). Cause: `backend/routes/gig_modal.py` lines 262-269 read `v.artist_frequency_days` then queried `pa.pay_dollars_override` (the WRONG column name) into a variable that was never used. The actual booking endpoint (`book_gig`) uses the correct `COALESCE(pa.frequency_days_override, v.artist_frequency_days)` — but the warning banner blocks the user from clicking book in the first place. Fixed `gig_modal.py` to use the same COALESCE pattern as `book_gig`. File last touched March 28 — bug had been there since then but didn't trip until tonight when test gigs were close in date.
- **2026-05-07 — Cancellation cleanup on the actual UI cancel endpoint (`/with-slots`).** Discovered after multiple wrong-fix iterations. Three different cancellation endpoints exist in `routes/gigs.py`: `cancel_gig` (line ~1311), `cancel_slot` (line ~3297), and `delete_gig_with_slots` (line ~3491, `DELETE /api/gigs/{id}/with-slots`). The venue UI's "Cancel Gig" button hits `delete_gig_with_slots`, NOT the others. That endpoint sent emails and reset slot/gig status to open, but did NOT delete transactions, set `last_cancelled_artist_id`, or delete the flyer. Result: cancelled gig still showed in artist's earnings (orphan transactions), the cancelled artist still got blasted on the re-opened gig, and the custom flyer persisted. Added all three cleanups to the `keep_open=True` path of `delete_gig_with_slots`. Diagnostic that pinpointed it: journalctl showed the venue's "Cancel Gig" click fired `DELETE /api/gigs/503/with-slots`, not `DELETE /api/gigs/503/cancel`.
- **2026-05-07 — Smart flyer preservation on multi-slot cancel.** Earlier flyer cleanup was too aggressive: cancelling ANY single slot wiped the flyer entirely, even when the venue had custom-designed it with multiple artists. New helper `_delete_flyer_if_no_bookings_remain(db, gig_id, cancelled_artist_id=None)` in `backend/routes/gigs.py` counts surviving booked slots — only deletes the flyer if zero remain. Multi-slot gigs with one slot cancelled keep their flyer (other artists' info intact). Single-slot gigs naturally hit the "zero bookings remain" condition and the flyer is deleted as before. Called from all 3 cancellation paths (`cancel_gig`, `cancel_slot`, `delete_gig_with_slots`). The `cancelled_artist_id` parameter (added later same day) lets the helper also strip just that artist's logo from the preserved canvas — see the multi-slot logo entry above.
- **2026-05-07 — Cancellation flow: subjects, last_cancelled, pay format, Time row.** Five small fixes that surfaced during testing.
  - `email_dispatch.py:send_cancellation_emails` accepts new `cancelled_by` param. Venue subject becomes "You cancelled your gig on {{date}}" when venue cancelled, defaults to "{{artist_name}} cancelled their gig" when artist cancelled. All 3 callers updated.
  - `routes/gigs.py:cancel_gig` venue-cancel branch now sets `last_cancelled_artist_id` (was artist-cancel only — venue-cancelled gigs were blasting back to the cancelled artist).
  - Pay formatting in cancellation blasts changed from `str(pay)` to `f"{pay:,.2f}"` at lines ~3928, 4091. "Pay: 200" → "Pay: $200.00".
  - Cancellation emails (artist + venue templates) got a new "Time" row between Date and Reason. `email_dispatch.py:cancel_vars` includes `start_time`/`end_time` formatted via `format_time_12hr`.
  - `routes/waitlist.py` "Hours Away" calculation uses a proper formatter handling 3 cases (<1hr → minutes only, exact hours → "X hour(s)", mixed → "X hours and Y minutes"). Template label `waitlist_exhausted_venue` updated from "Hours Away" → "Time Until Start".
- **2026-05-07 — booked_edit_gig pay sync to parent gig record.** When venue edits a multi-slot booked gig, `routes/gigs.py:booked_edit_gig` was syncing `MIN(start_time)` and `MAX(end_time)` from slots to the parent `gigs` row but NOT pay. Result: parent `gigs.pay` got stale after slot pay changes. Added `MAX(pay)` to the sync block, included `pay` in the UPDATE. Same fix applied to `update_gig` (different endpoint, same sync pattern). Validated by SQL check that all multi-slot gigs now have `gig.pay = MAX(slot.pay)`.
- **2026-05-07 — Email logo URL absolute + slots_html substitution + email_templates.py auto-export ownership.** Three smaller email fixes. (1) `artist_gig_booked` template had relative URL `static/img/gigsfill-logo_light.png` that broke when the email client rendered it — replaced with absolute `https://gigsfill.com/app/static/img/gigsfill-logo_light.png`. Audit query confirmed all 45+ templates with logos now use absolute URLs. (2) `artist_gig_edited` template: `email_dispatch.py` now builds `<tr>` rows for Time and Pay inline before sending so `{{slots_html}}` placeholder substitutes correctly. (3) The auto-export feature added 2026-05-04 broke when www-data couldn't write to `email_templates.py` — file ownership had drifted to root. Reminder to run `chown www-data:www-data` on every backend file deploy.
- **2026-05-07 — Admin Accounting view: complete polish.** Multi-touch fix to the admin Accounting tab.
  - Frontend (`admin-init.js`): renamed "Gig Fee" → "Gig Paid". Color coding on amount columns (Venue Charged green bold, Venue Fee/Artist Fee orange, Artist Payout/Stripe Fee red, GF Profit conditional green/red). Final column order: Date, Time, Venue, Artist, Status, Gig Paid, Venue Fee, Venue Charged, Artist Fee, Artist Payout, Stripe Fee, GF Profit. Same order in CSV/print export.
  - Backend (`admin.py`): Artist Payout column was always $0 because parent `venue_charge` has `artist_payout_cents=0` by design. Subquery now sums children's `artist_payout_cents`. Artist name resolution uses 4-tier fallback: `t.artist_id → g.artist_id → t.to_user_id-via-artists → GROUP_CONCAT via artist_payout child rows` (handles multi-slot). Phantom-cancelled detection: when a row has `payment_cancelled` + `stripe_pi_id` set + `venue_charge>0` + `platform_fee_on_cancel=0`, compute stripe_fee on original venue_charge with `profit = -stripe_fee`. Summary card math fixed: cancelled rows count only `platform_fee_on_cancel` (no double-count), aligned filters, "5 successful" sub-label. Validated math identity: `Total Fees ($75) - Stripe Costs ($6.86) = Net Profit ($68.14)`.
- **2026-05-06 — Operational lessons: terminal-line-wrapping, file ownership drift, missing backups.** Three recurring issues during the day's deploys, documented for future sessions.
  - **Terminal mangling**: bundled multi-line `systemctl restart` commands repeatedly got eaten by the terminal copy-paste. Best practice: ONE LINE COMMANDS ONLY. Verify via `sudo systemctl status SERVICE --no-pager | grep "Active:"` showing fresh "since" timestamp.
  - **File ownership drift**: deploys via `sudo cp` create root-owned files. Always follow with `chown www-data:www-data` on every touched file. Caught email export 500 (file at `/opt/gigsfill/backend/email_templates.py` was root-owned).
  - **Backup files inconsistent**: `ls /opt/gigsfill/backend/*.bak* /opt/gigsfill/backend/routes/*.bak* /opt/gigsfill/backend/services/*.bak* 2>/dev/null | wc -l` returned 1 at end of day despite ~14 deploys. Some `cp /old /old.bak` commands failed silently or got mangled. Best mitigation: snapshot the whole tree before sleeping with `sudo tar czf /opt/gigsfill-snapshot-$(date +%Y%m%d-%H%M).tar.gz /opt/gigsfill/backend /opt/gigsfill/app/static/js /opt/gigsfill/backend.db`.
- **2026-05-05 — Open-gig blast: non-preferred artists can now book.** Found while testing the blackout flow: a non-preferred artist who received the "any artist can book this gig" 36h-blast email hit a 403 "Artist is not approved for this venue" when they tried to book — the email lied. Cause: the booking endpoint's preferred-status bypass relied on `gigs.radius_blast_token`, but that token is only set by cancellation blasts (in `fire_cancelled_gig_blast`), not by open-gig blasts (in `process_open_gig_notifications`). Original author intentionally avoided setting the token in open-gig blasts to keep the calendar's "blast open" yellow-bubble visual reserved for cancellation blasts. Fix: added `_open_blast_bypass_active(db, venue_id, gig_id)` helper in `routes/gigs.py` that returns True if the venue has `blast_all_enabled=1` for any of `open_gig_36h`/`_1w`/`_2w`/`_4w` AND the gig is within that notification's window. Applied to all three booking-endpoint preferred-status checks (lines ~930, ~1145, ~2770). Bypass is purely backend — calendar visuals unchanged.
- **2026-05-05 — Frontend error message surfacing.** Fix wave from the same testing session. `app/static/js/api.js` (used by ESM-style code) was throwing generic `"<METHOD> <url> failed: <status>"` on non-ok responses, discarding FastAPI's `{"detail": "..."}` body. So a 403 with the exact human-readable reason ("You have a blackout on this date: Vacation") reached the frontend but the wrapper threw it away. Fix: rewrote `apiGet`/`apiPost`/`apiPut`/`apiDelete` to read the response body via `_readErrorMessage()` and throw with that text. Also added `app/static/js/api-globals.js` exposing `window.apiGetSafe`/`window.apiPostSafe`/`window.apiPutSafe`/`window.apiDeleteSafe` for the ~18 IIFE-style files that can't use ESM imports. Added the script tag to 10 HTML pages (admin, artist-book-gigs, artist-edit, contract-sign, notifications-all, user-profile, venue-create-gigs, venue-discovery, venue-edit, venue-email-center) right after `auth.guard.js`. Existing IIFE files still use raw `fetch()` with hardcoded `throw new Error('Failed to send')` — those weren't bulk-converted (Section 16 item #22) and can be migrated as testing reveals which actually surface bad messages to users.
- **2026-05-05 — Honor artist blackout dates across all blast/waitlist paths.** Discovered the `Block Dates` feature on artist-edit was half-built: it gated booking (artist couldn't book a gig on a blocked date) but every other system ignored blackouts. Result: blacked-out artists got spammed with blast emails for dates they couldn't take, waitlist offers went to artists on tour, and an artist could add a blackout that overlapped their own waitlist position with no warning. Fix scope: (1) added `NOT EXISTS (SELECT 1 FROM artist_availability ...)` filter to all 6 artist-blast queries — three in `scheduler.py` (preferred-artist branch + blast-all branch in `process_open_gig_notifications`, plus `process_radius_blast`) and two in `routes/gigs.py:fire_cancelled_gig_blast` (preferred + nearby-radius queries) and one in `routes/waitlist.py:_send_sequential_offer`; (2) widened the booked-gig conflict check in `availability.add_blackout` to cover both `gigs.artist_id` (single-slot) and `gig_slots.artist_id` (multi-slot) — the original only checked gig_slots; (3) added waitlist-conflict detection in `add_blackout` that returns a structured 409 with `conflict_type='waitlist'` listing the conflicting waitlisted gigs (allowing `force=true` to override and remove the artist from those waitlists, also advancing the waitlist if they were the current offer holder); (4) frontend `artist-availability.js` shows a confirmation modal on 409 with two buttons: "Keep waitlist position (cancel blackout)" and "Remove from waitlist and add blackout". Net effect: artists set blackouts and the entire system respects them — no more emails about gigs they can't take, no more wasted waitlist offer windows on unavailable artists.
- **2026-05-04 — Per-venue timezone for payout scheduling.** Discovered while validating payment flow for a real test gig: `transactions.scheduled_process_at` was being written as a naive datetime with the literal hour `17` (e.g. `2026-05-05 17:00:00`) and the payout scheduler was treating it as UTC, so a Pacific venue's "5pm payout" actually fired at 17:00 UTC = 10am Pacific (7-8 hours early). Fix scope: (1) added `venues.timezone TEXT` column with auto-derivation from `venues.state` via a US state-to-IANA mapping in `backend/utils.py` (`US_STATE_TIMEZONES` covers all 50 states + DC + 5 territories); (2) `routes/gigs.py:_create_booking_transaction` now computes payout time as 5pm in venue's local tz, converts to UTC, stores naive UTC string; (3) `payout_scheduler.scheduler_loop()` now sweeps hourly (was: only at platform payout hour) so per-venue UTC times are honored within ~1h; (4) `_handle_charge_failure` retry path uses venue tz via new `_compute_retry_at_utc()` helper. Behavior change: payouts now fire at 5pm in each venue's local time, not at platform-wide 5pm. The platform-wide `payment_processing_hour` setting is still respected — it's the hour applied in each venue's local tz. Read from `platform_settings` instead of being hardcoded as `17`.
- **2026-05-04 — Aligned blast email default-ON/OFF policy across UI, email_service, and scheduler.** Discovered investigating why a 36h-out test gig sent 0 emails despite the venue having `open_gig_36h` enabled. Root cause: 3-layer drift between `app/static/js/user-profile.js` (UI defaults `_36h, _1w, cancelled_gig_*` to ON), `backend/email_service.py:user_has_email_enabled()` (only `_4w, _2w` default OFF — agreed with UI), and `backend/scheduler.py` (had its own duplicated 5-element set with ALL the blast keys default-OFF, including `_36h` and `_1w`). The user-profile UI told artists they had `_36h: ON` by default, but the scheduler silently dropped them. Fix: promoted `BLAST_OFF_DEFAULTS = frozenset({'venue_open_gig_4w', 'venue_open_gig_2w'})` to a module-level constant in `email_service.py`, removed the two inline sets in `scheduler.py` and replaced with `from backend.email_service import BLAST_OFF_DEFAULTS`. Now all three layers agree: long-lead-time blasts (4w, 2w) are opt-in; urgent blasts (1w, 36h, cancellation) default ON.
- **2026-05-04 — Admin email-template edits now persist across restarts.** When admin saves a template via the Admin → Email Templates UI, the PUT `/api/email-templates` endpoint now auto-writes the full template set to `backend/email_templates.py` on disk in the same request. Previously, edits were saved to the DB but `_populate_email_templates()` would clobber them on next API restart by re-syncing from the in-code `TEMPLATES` dict. Workaround was a manual "Export All" click that was easy to forget. Auto-export eliminates the footgun. Refactored the file-write logic into a private `_write_templates_file(db)` helper so both the PUT (auto) and the GET `/api/email-templates/export` (manual fallback) share the same code. Updated the admin UI banner from a yellow warning to a green confirmation, and added a JS toast for the rare case where auto-export fails (e.g. file permissions). Doc sections updated: 16 (Known issues — item #20 marked).
- **2026-05-04 — Affiliate URL in recommend_gigsfill template.** The previous "credit affiliate on header recommend" fix correctly routed POSTs to `/api/affiliate/recommend` and logged the affiliate code, but the actual email link in the recipient's inbox still went to `https://gigsfill.com` (no `?aff=...`) because the `recommend_gigsfill` template body in `backend/email_templates.py:1735` had the URL hardcoded. Replaced the hardcoded URL with `{{aff_url}}` (the variable the endpoint already passes). On API restart, `_populate_email_templates` syncs the new template body to the DB. Recipients now get a signup link with the affiliate code embedded. Discovered new known issue (#20): the auto-sync overwrites admin-UI template edits on every restart.
- **2026-05-04 — Three known-issue cleanups.** (1) **Affiliate credit bug fix** — the header dropdown's "Recommend GigsFill" button was POSTing to the legacy `/api/recommend` endpoint (in `backend/main.py`), which sends a recommendation email but does NOT include the user's affiliate code in the signup link. Result: referrals via the convenient header button were going uncredited. Changed `app/static/js/user-dropdown.js:submitRecommendation()` to POST to `/api/affiliate/recommend` instead (the same endpoint the user-profile Affiliates tab uses). Field rename: `message` → `personal_note`. Also added proper handling of the `already_claimed` and `{ok: false}` response shapes. The legacy `/api/recommend` endpoint is left in place as a no-op safety net. (2) **Deleted `app/static/js/states.js`** — ES-module file with `US_STATES` export, never imported anywhere (twin of `us-states.js` which IS used). (3) **`gig_messages` schema migration** — the `sender_entity_id` and `target_artist_id` columns (which scope messages per-artist on multi-slot gigs, fixing a multi-artist message-leak bug) were being added lazily on every API request via `messages.py:_ensure_gig_messages_table`. Moved to `db.py`'s `_add_columns()` so they're part of the canonical schema. The lazy function is kept as a safety net (becomes no-op after `_TABLE_CREATED` flag set on first call). Doc sections updated: 16 (Known issues — items #4, #7, #12 marked).
- **2026-05-04 — Three cleanup fixes (one security, two dead code).** (1) Fixed a buggy admin gate in `backend/routes/emails.py:28` — the check `if not admin_row["is_admin"]` was failing-open because `is_admin` is stored as the literal string `'false'`, and `not 'false'` is False in Python. Any logged-in user could PUT `/api/email-templates/{notification_type}` and rewrite arbitrary email templates (including password-reset). Replaced with the same string-aware pattern used everywhere else: `str(value or "").lower() not in ("true", "1")`. (2) Deleted `app/static/js/venue_edit.js` — older copy of `venue.edit.js`, no HTML loaded it. (3) Deleted `backend/routes/main.py` — broken fragment with no router declaration, never imported. Note: the `/api/coming-soon-notify` endpoint it tried to define is NOT wired up anywhere else either; if the coming-soon homepage is ever activated, that endpoint must be added to `backend/main.py`. Doc sections updated: 16 (Known issues — items #1, #2, #5).
- **2026-05-04 — Fixed five scheduler bugs.** Single-file change to `backend/scheduler.py`:
  1. Removed `process_radius_blast` from the hourly loop (was duplicating with `process_open_gig_notifications('open_gig_36h')` — same template, same window). Function kept in file for back-compat.
  2. Per-artist dedup for `process_review_requests`: encoded `artist_id` into `gig_email_log.notification_key` (e.g. `artist_review_request:42`) so multi-slot gigs send "rate the venue" emails to ALL artists, not just the first. The user-facing template/preference key (`artist_review_request`) is unchanged.
  3. Removed `sent_for_date` from dedup SELECTs in `process_gig_confirmation` and `process_open_gig_notifications`. Once an email has been sent for `(gig_id, notification_key)`, it never re-fires — even if the venue changes their lead time. The column is still populated on INSERT for historical record, just not used for dedup.
  4. `process_gig_confirmation` now uses `INSERT ... ON CONFLICT DO UPDATE SET recipient_count = recipient_count + 1` so multi-slot gigs reflect actual N artists in the count column instead of always showing 1.
  5. Fixed wrong dict key in `_run_contract_hold_cleanup` log message — was reading `result.get("released")` but the function returns `released_count`. Cosmetic only.
  Doc sections updated: 10 (Background services per-function audit), 16 (Known issues — items 16-19 are now resolved and the entries note this).
- **2026-05-04 — Drop-ins must be mirrored.** Discovered during deploy: the `gigsfill.service` had two systemd drop-ins (`/etc/systemd/system/gigsfill.service.d/secret.conf` with `GIGSFILL_SECRET_KEY` and `override.conf` with `SESSION_SECRET_KEY`). Without the same env vars, the new `gigsfill-scheduler.service` couldn't sign tokens (contract hold cleanup failed). Fix was to mirror both files to `/etc/systemd/system/gigsfill-scheduler.service.d/`. Doc sections updated: 17 (Deployment) — added explicit drop-in mirroring callout.
- **2026-05-04 — Full audit of all scheduled email functions.** Walked through every function in `run_scheduled_emails()` and `_scheduler_loop()`. Found two additional latent bugs documented in §16 (Known issues): (a) review-request emails on multi-slot gigs only send to the first artist due to UNIQUE constraint mismatch, and (b) if a venue changes their open-gig-blast timing after a gig has already been notified, the gig would re-fire on every hourly tick. Neither is currently biting; both are documented for later fix. Doc sections updated: 16 (Known issues), 10 (Background services — added per-function audit notes).
- **2026-05-04 — Schedulers moved to a dedicated systemd service.** Added `backend/scheduler_main.py` and `scripts/gigsfill-scheduler.service`. The API service no longer starts the schedulers; they run in a single dedicated process. Removed the racy `fcntl` file lock from `start_scheduler()`. This fixes a duplicate-email bug where both uvicorn workers were running the scheduler simultaneously. Doc sections updated: 2 (Tech stack), 3 (Repo layout), 7 (main.py + middleware), 10 (Background services), 17 (Deployment), 16 (Known issues — removed the "fcntl POSIX-only" note since the lock is gone).
- **2026-05-03 — Initial sync.** Full codebase walkthrough produced this document.

---

## 1. What GigsFill is

GigsFill is a two-sided marketplace connecting **live music artists** with **venues** that book them.

- **Venues** post gigs (with optional multi-slot lineups, recurring schedules, contracts, and per-artist pay overrides), maintain a list of preferred artists, message booked artists, send blast emails for open slots, and are charged the day after the gig via Stripe.
- **Artists** browse gigs, request preferred status at venues, book open slots (subject to W9, frequency, and ban checks), sign contracts, and get paid out via Stripe Connect Express the day after the gig.
- **Admin** runs the platform: configures Stripe, SMTP, email templates, fees, supports tickets, manages affiliate payouts, browses the database, and pulls analytics.
- **Affiliates** are users who refer venues and earn a percentage of gig fees (paid quarterly via Stripe Connect).

The product is live-music-specific: the UI talks about "artists" and "venues," not "providers" and "buyers."

---

## 2. Tech stack

| Layer | What it is |
|---|---|
| **Backend** | Python 3.12 + FastAPI, served by `uvicorn` with 2 workers behind systemd. SQLAlchemy 2.x for ORM, raw SQL via `sqlalchemy.text()` is also used heavily |
| **Database** | SQLite by default (`backend.db` next to the `backend/` package, WAL mode), with full PostgreSQL support via `DATABASE_URL` env var. Connection-pooled when on PG. A compatibility shim translates `?` placeholders to `%s` so the same raw SQL runs on both engines |
| **Frontend** | Vanilla JS + HTML + CSS (no build step). 26 HTML pages, ~63 JS files, all served as static files. Stripe.js loaded from `js.stripe.com` |
| **Auth** | Signed session cookies via `itsdangerous` (HMAC-signed, 7-day rolling expiry). Passwords hashed with `bcrypt` |
| **Payments** | Stripe — SetupIntents for venue cards, Connect Express for artist payouts, manual `Transfer` from charge `source_transaction` to bypass pending balance |
| **SMS** | Carrier email-to-SMS gateways via the same SMTP — no Twilio. User picks carrier from a dropdown |
| **Email** | SMTP (Gmail by default, configurable per-port: 465 SSL, 587 STARTTLS). Templates stored in DB and synced from `email_templates.py` on startup |
| **Background jobs** | Two background threads — `payout_scheduler` (continuous, runs at configured hour) and `scheduler` (hourly email blasts + 10-min waitlist sweep). Both run in a **dedicated systemd service** (`gigsfill-scheduler.service`) as a single process, NOT inside the API workers. Started by `backend/scheduler_main.py`. The API service (uvicorn workers) does not start them — gated by `GIGSFILL_RUN_SCHEDULERS` env var |
| **Rate limiting** | `slowapi` backed by Redis (preferred) or in-memory fallback. Storage URI from `RATELIMIT_STORAGE_URI` env var |
| **Hosting** | DigitalOcean droplet, systemd-managed (`scripts/gigsfill.service`), env from `/opt/gigsfill/.env` |

### Key dependencies (`requirements.txt`)
`fastapi`, `uvicorn[standard]`, `sqlalchemy`, `bcrypt`, `pydantic`, `stripe`, `itsdangerous`, `slowapi`, `email-validator`, `redis`, `psycopg2-binary`, `alembic` (declared but no migrations yet — schema is managed by `setup_database()` in `db.py` which uses `CREATE TABLE IF NOT EXISTS` + a custom `_add_columns()` helper for additive migrations).

---

## 3. Repo layout

```
gigsfill/
├── app/                           ← Frontend (served at /app/...)
│   ├── *.html                     ← 26 pages
│   └── static/
│       ├── css/                   ← gigsfill.css (main), gigsfill-modern.css, mobile.css
│       ├── img/                   ← logos, placeholders, default flyer bg
│       ├── icons/                 ← PWA icons
│       ├── js/                    ← 63 JS files (page inits + shared modules)
│       ├── uploads/               ← user uploads (artist/venue media, contracts, flyers)
│       ├── manifest.json          ← PWA manifest
│       ├── robots.txt             ← public pages allowed, app pages disallowed
│       └── sitemap.xml
│
├── backend/                       ← Python backend
│   ├── main.py                    ← FastAPI entrypoint, middleware, ~18 inline routes
│   ├── db.py                      ← Engine, sessions, setup_database() (~50 tables)
│   ├── models.py                  ← SQLAlchemy ORM models (kept in sync with db.py)
│   ├── email_service.py           ← EmailService class (template lookup + render + send)
│   ├── email_templates.py         ← In-code template definitions, synced to DB on startup
│   ├── payout_scheduler.py        ← Background charge/transfer worker
│   ├── scheduler.py               ← Background email blast + waitlist worker
│   ├── scheduler_main.py          ← Entrypoint for gigsfill-scheduler.service
│   │                                 (boots both schedulers, blocks on sleep loop,
│   │                                 handles SIGTERM. NOT loaded by the API service.)
│   ├── rate_limiter.py            ← slowapi limiter w/ Redis fallback
│   ├── log_buffer.py              ← In-memory ring buffer for admin Logs tab
│   ├── sms_service.py             ← Email-to-SMS gateway sender
│   ├── us_cities.py               ← Hard-coded list of US cities + lat/lon (for validation)
│   ├── utils.py                   ← check_venue_access, check_artist_access, get_all_entity_users
│   ├── routes/                    ← 27 route modules, ~360 endpoints
│   └── services/                  ← Higher-level cross-route helpers
│       ├── notification_service.py    ← create_notification, notify_gig_booked/cancelled/edited
│       ├── email_dispatch.py          ← send_booking_emails, send_cancellation_emails, etc.
│       └── gig_cleanup.py             ← cleanup_gig_records, delete_gig_completely
│
├── scripts/                       ← Deploy scripts
│   ├── gigsfill.service           ← systemd unit for the API (uvicorn workers)
│   ├── gigsfill-scheduler.service ← systemd unit for the scheduler service
│   │                                 (sets GIGSFILL_RUN_SCHEDULERS=1, runs scheduler_main.py)
│   ├── setup_do.sh                ← DigitalOcean provisioning
│   ├── env_template.txt           ← env vars template
│   ├── fix_1gb_droplet.sh
│   ├── migrate_sqlite_to_postgres.py
│   └── reset_gigs_db.py
│
├── tests/
│   ├── conftest.py
│   ├── test_data_integrity.py
│   └── test_services.py
├── test_cancel_flow.py            ← Standalone integration test
└── requirements.txt
```

**File size hot spots** (to know what's big when navigating):
- `backend/routes/gigs.py` — 4,754 lines (booking, cancel, recurring, blast, slots, calendar export)
- `backend/routes/contracts.py` — 3,155 lines (templates, signing, countersigning, PDF generation)
- `backend/routes/stripe_connect.py` — 2,030 lines (cards, Connect, webhooks)
- `backend/routes/admin.py` — 1,989 lines
- `backend/email_templates.py` — 2,577 lines (~80+ HTML email templates)
- `backend/db.py` — 1,641 lines (one giant `setup_database()`)
- `app/static/js/venue.create-gigs.js` — 252 KB (calendar + gig modal + recurring + bulk blast)
- `app/static/js/flyer-editor.js` — 130 KB (Fabric.js canvas editor)
- `app/static/js/artist.book-gigs.js` — 127 KB

---

## 4. Data model — core entities

The schema lives in `backend/db.py` (~50 tables created in one `setup_database()` function) and is mirrored in `backend/models.py` as SQLAlchemy ORM. All models share a `created_at` defaulting to `datetime.utcnow`.

### Identity & ownership

```
users
  id, email (unique), password (bcrypt), first_name, last_name, phone,
  is_admin (TEXT 'true'/'false' — historical typing quirk),
  affiliate_code (unique, "AFF-XXXXXXXX" format, auto-generated),
  email_verified (added via _add_columns), sms_carrier, last_login,
  created_at

artists                              ← belongs_to user (a.user_id = u.id)
  id, user_id, name, city, state, latitude, longitude, bio,
  artist_type ('Solo Artist'|'Live Band'|'DJ'|...),
  band_formats (CSV: 'Duo,Trio,Quartet,...'),
  styles (CSV of music styles),
  booking_contact (free-text, defaults to "name - email - phone"),
  spotify_url, instagram_url, facebook_url, youtube_url, twitter_url, tiktok_url, website_url,
  display_order

venues                               ← belongs_to user (v.user_id = u.id)
  id, user_id, venue_name, description,
  address_line_1, address_line_2, city, state, postal_code, latitude, longitude,
  venue_size (capacity), has_stage, stage_width_ft, stage_depth_ft, setup_location_description,
  has_sound_equipment, sound_equipment_description,
  has_sound_engineer, sound_engineer_details,
  has_lighting, lighting_description,
  load_in_out_details,
  arrival_time_type ('flexible'|'no_earlier_than'),
  arrival_no_earlier_than_hour, arrival_no_earlier_than_period,
  default_pay_dollars, default_pay_cents,
  bar_tab_details, food_tab_details,
  artist_frequency_days (default minimum days between bookings of same artist),
  website_url, facebook_url, instagram_url, twitter_url, yelp_url, google_maps_url,
  display_order,
  pro_certified, pro_certified_at,
  payment_status ('active'|'suspended'), payment_suspended_at, payment_suspension_reason
```

### Multi-user access (Users tab on each profile)

```
entity_users      ← non-owner users granted access to an artist or venue
  id, entity_type ('artist'|'venue'), entity_id, user_id,
  role ('owner'|'member'), added_by_user_id, created_at
  UNIQUE(entity_type, entity_id, user_id)

entity_invitations ← pending email invites to join an artist/venue team
  id, entity_type, entity_id, entity_name, invited_email,
  invited_by_user_id, inviter_first_name, inviter_last_name,
  token (unique), status ('pending'|'accepted'|'declined'|...),
  created_at, responded_at
```

`utils.check_venue_access(db, vid, uid)` and `check_artist_access(db, aid, uid)` are the centralized authorization checks: pass if user is the direct owner OR has an `entity_users` row. `get_all_entity_users(db, type, id)` returns the full list including the owner — used everywhere notifications/emails go out so all team members are notified.

### Gigs and slots

A gig is a date+venue. Every gig is multi-slot under the hood — slots store the actual times, pay, artist type, and booked artist. Single-slot gigs just have one row in `gig_slots`.

```
gigs
  id, venue_id, artist_id (NULL for slot-based until all slots booked),
  date (YYYY-MM-DD string), start_time (HH:MM string), end_time, title, pay (int),
  notes, styles, status ('open'|'booked'|'cancelled'|'pending_venue_approval'),
  artist_type, band_formats,
  is_recurring, recurring_group_id (UUID shared by series),
  recurrence_pattern, recurring (legacy 0/1),
  recurring_interval_weeks, recurring_days_of_week,
  recurring_end_type ('after'|'by_date'|'never'),
  recurring_end_after, recurring_end_by_date,
  is_multi_slot (0/1),
  frequency_exempt (0/1 — disables the artist_frequency_days check),
  contract_hold_artist_id, contract_hold_expires_at,
  radius_blast_token (set when an "open gig" blast email goes out — booking via that link bypasses preferred-only and frequency checks)

gig_slots                            ← the actual bookable units
  id, gig_id (FK CASCADE), slot_number, start_time, end_time, pay,
  artist_id (NULL until booked),
  status ('open'|'booked'|'pending_contract'|'pending_venue_approval'|'cancelled'),
  artist_type, band_formats, styles
```

### Preferred artists (the access-control mechanism)

Most venues only let approved artists book directly. The exception is "blast window" — when a gig is close to its date and the venue has open-gig blast notifications enabled, frequency limits and preferred-only restrictions are waived.

```
preferred_artists
  id, venue_id, artist_id,
  status ('pending'|'approved'|'denied'|'revoked'),
  frequency_days_override (NULL = use venue default),
  pay_dollars_override, pay_cents_override (NULL = use gig listed pay)
  UNIQUE(venue_id, artist_id)

venue_artist_bans   ← permanent ban; overrides everything
  id, venue_id, artist_id, banned_by, reason, created_at
  UNIQUE(venue_id, artist_id)
```

### Waitlist (when a gig is fully booked)

```
gig_waitlist                          ← artists in line for an open slot
  id, gig_id, artist_id, notified, notified_at, created_at,
  offer_sent, offer_sent_at, offer_expires_at, offer_token, offer_declined
  UNIQUE(gig_id, artist_id)

waitlist_offered                      ← persists offer tokens after the
                                        gig_waitlist row is removed (so the
                                        respond-to-offer link still works)
  id, gig_id, artist_id, user_id,
  offer_token (unique), offer_expires_at, created_at
```

The waitlist is **sequential**: when a slot reopens, the #1 waitlist artist gets a 24h offer (or 2h if 36h–1wk to gig, or 30min if <36h to gig). If they decline or the offer expires, the next artist in line gets the offer. Within 36h of the gig, if the venue has blast notifications enabled, the system blasts to all preferred artists in radius instead of waiting on a sequential offer.

### Contracts

```
venue_contracts                       ← templates a venue defines once
  id, venue_id, contract_type ('pdf_upload'|'custom_builder'|'auto_generated'),
  name, is_active, require_for_booking, per_gig_pdf,
  pdf_file_path, contract_body (HTML), custom_fields (JSON), created_at, updated_at

gig_contracts                         ← per-booking instances
  id, gig_id, venue_contract_id, venue_id, artist_id, contract_type,
  rendered_body, filled_fields, pdf_file_path, signed_pdf_path,
  status ('pending'|'artist_signed'|'countersigned'|'cancelled'),
  artist_signature_name, artist_signature_date, artist_signature_ip,
  venue_signature_name, venue_signature_date, venue_signature_ip,
  hold_expires_at,                    ← contract holds the slot for 24h after artist signs
                                        until venue countersigns or hold expires
  created_at
```

### Payments

```
entity_payment_settings       ← per-entity payment config (artist or venue)
  id, entity_type, entity_id, default_payment_method,
  stripe_account_id, stripe_publishable_key, stripe_secret_key, stripe_onboarding_complete,
  stripe_customer_id, stripe_payment_method_id,        ← venue card on file
  stripe_connect_account_id, stripe_connect_onboarding_complete,  ← artist payout account
  affiliate_stripe_connect_account_id, affiliate_stripe_connect_onboarding_complete,
  paypal_email, venmo_username, zelle_email, cashapp_cashtag,
  bank_account_last4, bank_routing_last4
  UNIQUE(entity_type, entity_id)

payment_methods                       ← legacy/future per-user methods
  id, user_id, payment_type, account_identifier, account_display_name,
  is_preferred, is_verified

transactions                          ← every charge + transfer
  id, gig_id, from_user_id, to_user_id, artist_id,
  amount_cents, venue_charge_cents, artist_payout_cents, commission_cents,
  credit_card_fee_cents, platform_fee_charged_cents,
  payment_method_type, payment_method_from, payment_method_to,
  status ('scheduled'|'test'|'processing'|'charged'|'pending_transfer'|
          'transferred'|'transfer_failed'|'paid'|'cancelled'|'suspended'|
          'charge_retry'),
  scheduled_process_at, processed_at, created_at,
  charge_attempts, last_charge_attempt_at, charge_failure_reason,
  cancel_reason, cancelled_at, notes,
  stripe_payment_intent_id, stripe_transfer_id, external_transaction_id,
  transaction_type ('venue_charge'|'artist_payout'|'single'),  ← multi-slot model
  parent_transaction_id                ← child artist_payout points to parent venue_charge

payment_cancellations                 ← audit log when a tx is cancelled
  id, transaction_id, gig_id, cancelled_by_user_id,
  cancellation_reason, cancelled_at

venue_payment_overrides               ← admin-only; suspends payments OR
                                        marks venue as free trial (direct-pay,
                                        no Stripe involvement)
  id, venue_id (unique), payments_suspended (1=suspended/free trial),
  suspended_by, suspended_at, notes
```

The transaction model handles two cases:
- **Multi-slot gig**: ONE `venue_charge` parent row (cumulative venue total for all slots), plus ONE `artist_payout` child per booked artist (linked via `parent_transaction_id`). The venue is charged once for everything; each artist is paid separately.
- **Single-slot gig (legacy + simple)**: ONE row with `transaction_type = 'single'` representing both sides.

**Status lifecycle (post-2026-05-07 fix)**:
- Booking creates parent `venue_charge` with `status='scheduled'` (or `'test'` if test mode) AND each `artist_payout` child with `status='scheduled'` too. **Children must NOT be created with `'pending_transfer'`** — that status now exclusively means "transfer was attempted and is awaiting retry" (e.g., artist not Stripe-onboarded). See changelog 2026-05-07 entry on the payout scheduler bug.
- At the gig+1day platform_payout_hour (in venue's local tz), `payout_scheduler` picks up parent rows whose `scheduled_process_at` has passed → charges venue's card → parent goes to `'charged'`.
- Immediately after a successful charge, `_transfer_to_artists` runs in the same loop iteration, queries children with `status IN ('scheduled', 'pending_transfer')` AND `parent_transaction_id = <this parent>` → fires Stripe transfer → child goes to `'paid'` once Stripe confirms bank settlement.
- The retry-stalled-transfers sweep on each tick has a defense-in-depth guard: only matches children whose parent is in `('charged', 'paid', 'transferred')`. Without this, a child in `pending_transfer` while parent is still `scheduled` could fire prematurely.
- For display purposes, the venue's billing endpoint computes `effective_status` for parent rows: when ALL non-cancelled children are `paid`, parent is shown as `paid` (parent's literal status remains `charged`).

### Cancellation paths (3 different endpoints!)
There are **three** cancellation endpoints in `routes/gigs.py`, each behaving slightly differently — easy to fix one and miss the others:
- `cancel_gig` (`DELETE /api/gigs/{gig_id}/cancel`, line ~1311) — the "official" cancellation API. Used by some flows.
- `cancel_slot` (`POST /api/gigs/{gig_id}/slots/{slot_id}/cancel`, line ~3297) — slot-level cancel.
- `delete_gig_with_slots` (`DELETE /api/gigs/{gig_id}/with-slots`, line ~3491) — **this is the one the venue UI's "Cancel Gig" button hits**. Critical to remember when fixing cancellation behavior.

All three should: delete transactions (or mark `payment_cancelled` for post-gig), delete or strip the flyer, set `last_cancelled_artist_id`. Helper `_delete_flyer_if_no_bookings_remain(db, gig_id, cancelled_artist_id=None)` handles the flyer logic — preserves multi-slot gigs' custom flyers when other slots remain booked, and strips just the cancelled artist's logo via `_remove_artist_logo_from_flyer`.

### Tax (W9 + 1099)

```
w9_forms
  id, entity_type ('artist'), entity_id (artist_id),
  tax_name, business_name, tax_classification, other_classification,
  exempt_payee_code, fatca_exemption_code,
  address_line_1, address_line_2, city, state, zip_code,
  tin_type ('SSN'|'EIN'), tin_encrypted, tin_last4,
  certified_at, tax_year (year W9 is valid for),
  created_at, updated_at
  UNIQUE(entity_type, entity_id, tax_year)

venue_tax_settings
  id, venue_id (unique), require_w9 (0/1), updated_at
                                       ← if 1, the booking flow blocks
                                         artists without a current-year W9

tax_1099s                              ← generated yearly per venue+artist
  id, venue_id, artist_id, tax_year,
  total_earnings_cents, gig_count,
  artist_name, artist_tin_last4, artist_address,
  venue_name, venue_address, venue_tin_last4,
  status ('generated'|'sent'|...), sent_at, created_at
  UNIQUE(venue_id, artist_id, tax_year)

pro_licenses          ← Performance Rights Org licenses (ASCAP, BMI, SESAC)
  id, venue_id, pro_name, license_number, expiration_date, license_file_path
  UNIQUE(venue_id, pro_name)
```

### Affiliate program

```
affiliate_recommend_emails
  id, sender_user_id, recipient_email (case-insensitive),
  recipient_name, sent_at, affiliate_code, clicked, clicked_at

affiliate_referrals                   ← affiliate user → referred venue
  id, affiliate_user_id, venue_id (unique — a venue has at most one affiliate),
  link_method ('cookie'|'email_click'|'email_match'|'manual'),
  initial_rate_percent (default 1.0),
  reduced_rate_percent (default 0.5),
  reduced_after_days (default 365),
  linked_at, manually_linked_by

affiliate_earnings                    ← per-transaction accrual
  id, affiliate_user_id, venue_id, transaction_id (unique),
  gig_fee_cents, rate_percent, earned_cents, quarter ('2026-Q1'),
  payout_id (NULL until paid), accrued_at

affiliate_payouts                     ← quarterly payout records
  id, affiliate_user_id, quarter, total_cents,
  status ('processing'|'paid'|'failed'),
  stripe_transfer_id, paid_at, notes, created_at
  UNIQUE(affiliate_user_id, quarter)
```

Affiliate code is captured three ways during signup, in order of priority:
1. `?aff=CODE` URL param (stored in `aff_code` cookie on landing for 30 days)
2. `aff_code` cookie set by the `/api/affiliate/track/{code}` redirect endpoint
3. Match by `affiliate_recommend_emails.recipient_email` (the earliest sender of a recommend email to this address wins)

### Notifications, messages, reviews, support

```
notifications                          ← in-app notification list
  id, user_id, notification_type, title, message,
  gig_id, venue_id, artist_id, cancellation_reason,
  entity_type, entity_id, action_token,
  is_read, created_at

gig_messages                           ← per-gig chat between venue & artist
  id, gig_id, sender_user_id, sender_type ('venue'|'artist'),
  sender_name, body, is_read, created_at,
  sender_entity_id (artist_id or venue_id),
  target_artist_id (for venue→artist messages on multi-slot gigs)

artist_reviews                         ← venue rating an artist
  id, venue_id, artist_id, gig_id, rating, body, visible, created_at

venue_reviews                          ← artist rating a venue
  id, venue_id, artist_id, gig_id, rating, body, visible, created_at

review_link_tokens                     ← one-time email links for reviews

support_tickets                        ← user-submitted support tickets
  id, user_id, user_email, user_name, category, subject,
  description, status ('open'|'closed'), created_at

support_ticket_replies                 ← thread on a ticket
  id, ticket_id, sender_type ('user'|'admin'),
  sender_name, sender_email, body, created_at

recommendations                        ← user-to-friend recommendation emails
  id, user_id, user_name, recipient_email, recipient_name,
  message, sent_at

artist_invitations                     ← venue inviting non-GigsFill emails
                                         to sign up (separate from entity_invitations)
  id, venue_id, venue_name, invited_email, invited_by_user_id,
  inviter_name, message, status ('pending'|'signed_up'|'deleted'),
  sent_at, signed_up_at, signed_up_user_id, resent_count, last_resent_at
```

### Email and notification preferences

```
email_templates                        ← admin-editable HTML email templates
  id, template_key (unique), subject, body, updated_at

email_preferences                      ← per-user opt-in/out per notification type
  id, user_id, notification_type, enabled (default TRUE)
  UNIQUE(user_id, notification_type)
                          ← long-lead-time blasts ('venue_open_gig_4w',
                            'venue_open_gig_2w') default OFF; urgent blasts
                            ('_1w', '_36h', 'cancelled_gig_*') default ON.
                            Canonical: BLAST_OFF_DEFAULTS in email_service.py

sms_preferences                        ← same shape but for SMS
  id, user_id, notification_type, enabled (default FALSE)

user_settings                          ← arbitrary key-value per user
  user_id, setting_key, setting_value (composite PK)

venue_email_notifications              ← venue-side blast schedule config
  id, venue_id, notification_key,
  enabled, time_value, time_unit ('hours'|'days'|'weeks'),
  radius_miles, updated_at
                          ← keys: 'open_gig_36h', 'open_gig_1w',
                            'open_gig_2w', 'open_gig_4w' — each fires
                            an email blast to preferred + radius artists
                            at that lead time before an unbooked gig

venue_email_history                    ← log of bulk venue→artist emails
  id, venue_id, venue_name, user_id, subject, body,
  recipient_count, sent_at, recipients_json
```

### Platform-wide

```
platform_settings                      ← admin-tunable config
  id, setting_key (unique), setting_value, description, updated_at, updated_by

cities                                 ← (mostly unused — us_cities.py is canonical)
  id, city, state, lat, lon
  UNIQUE(city, state)

flyers                                 ← per-venue flyer templates and per-gig flyers
                                         (Fabric.js JSON in DB)

public_activity                        ← analytics event log
  id, event_type, event_data, city, state, venue_id, artist_id, gig_id,
  ip_hash, user_agent, session_id, referrer, created_at

artist_availability                    ← artist blackout dates
                                         (date ranges artist can't perform)

email_settings                         ← legacy/unused — settings now live in
                                         platform_settings
```

The full list of `platform_settings` keys (defaults defined in `db.py`) and what they control:

| Key | Default | Purpose |
|---|---|---|
| `commission_percentage` | `5` | (Legacy — `platform_fee_percent` is what's used now) |
| `credit_card_fee_percentage` | `3.5` | Credit card processing fee % |
| `payment_processing_hour` | `17` | Hour of day (24h) to charge venue cards, applied in each venue's local timezone. 17 = 5pm local. |
| `payment_processing_delay_days` | `1` | Days after gig before processing |
| `platform_email` | `""` | SMTP username, also used as From address |
| `platform_email_password` | `""` | SMTP password |
| `platform_smtp_server` | `smtp.gmail.com` | SMTP host |
| `platform_smtp_port` | `587` | SMTP port |
| `platform_email_from_name` | (added later) | Display name for From: header |
| `support_email` | `""` | Address that receives support ticket replies |
| `support_email_password`, `support_smtp_server`, `support_smtp_port`, `support_display_name` | — | Optional separate SMTP for support (otherwise uses platform_*) |
| `admin_alert_email` | `""` | Where chargeback/payout-failure alerts go |
| `admin_stripe_publishable_key`, `admin_stripe_secret_key`, `admin_stripe_webhook_secret` | `""` | Stripe credentials |
| `platform_fee_percent` | `10` | Platform fee % charged on each gig |
| `platform_fee_split` | `split` | `split` (50/50) \| `venue_only` \| `artist_only` |
| `platform_min_fee` | `20` | Minimum fee in dollars (overrides percentage if higher) |
| `payments_enabled` | `0` | Master kill-switch: `1` = real Stripe charges, `0` = test mode (no charges) |
| `payout_time` | `17:00` | Daily payout time (legacy — `payment_processing_hour` takes priority) |
| `platform_timezone` | `America/Los_Angeles` | Fallback IANA timezone for venues without one set. Most scheduling now uses per-venue tz (see `venues.timezone`). |
| `admin_paypal_email`, `admin_paypal_client_id`, `admin_paypal_client_secret` | `""` | PayPal config (planned/partial) |
| `admin_venmo_username`, `admin_venmo_link`, `admin_zelle_email`, `admin_zelle_phone`, `admin_cashapp_cashtag` | `""` | Alternative payout methods (manual/display only) |
| `affiliate_rate_percent` | `1.0` | Initial affiliate rate |
| `affiliate_reduced_rate_percent` | `0.5` | Rate after `reduced_after_days` |
| `affiliate_reduced_after_days` | `365` | Days before rate drops |
| `affiliate_min_payout_cents` | `5000` | Minimum quarterly payout ($50) |
| `affiliate_1099_threshold_cents` | `60000` | Annual 1099 threshold ($600) |
| `affiliate_enabled` | `true` | Master switch for affiliate program |
| `signups_enabled` | (default open) | Set to `false`/`0` to close new signups |
| `maintenance_mode` | (unset) | Set to `true`/`1` to return 503 from non-admin API routes |
| `maintenance_message` | "GigsFill is currently undergoing maintenance..." | Banner text shown when maintenance is on |
| `site_url` / `base_url` | `https://gigsfill.com` | Used in email links |


---

## 5. Frontend pages — what each HTML file does

All pages live in `app/`. Each one usually pairs with one `static/js/<page>-init.js` file (auto-extracted from inline scripts for CSP compliance — comment "Phase 5") plus possibly a heavier shared JS module.

### Public / unauthenticated

| Page | Purpose | Key JS |
|---|---|---|
| `index.html` | Homepage. Login form + city search "Find Music" button. Captures `?aff=CODE` into a 30-day cookie | `index-init.js`, `modals.js`, `city-autocomplete.js`, `maintenance-banner.js`, `sw-register.js` |
| `signup-new.html` | Multi-step signup wizard. Step 1: role (artist/venue), Step 2: personal info, Step 3+: role-specific (artist type/lineup/styles for artists; address/amenities/pay/PRO for venues). Validates city against `us_cities`, shows duplicate-name modal with "Request Access" if a profile already exists in that city/state | `signup-new-init.js` |
| `index-comingsoon.html` / `index_Placeholder.html` | "Coming soon" landing page (currently unused but available for swap-in) | inline |
| `public-gigs.html` | Public-facing gig calendar at `/app/public-gigs.html?city=X`. View-only — copies most of artist-book-gigs.js but no booking. Tracks analytics events | `public-gigs.js`, `gig-modal.js`, `flyer-overlay.js`, `city-autocomplete.js` |
| `venue-discovery.html` | Public venue search/browse | `venue-discovery-init.js`, `venue.discovery.js` |
| `artist-profile.html` | Public artist profile page (not the edit page). Tabs: Artist Info, Calendar, Videos, Pictures, Audio, Social Media, Reviews. Reads `?artist_id=N` | `artist-profile-init.js`, `artist-reviews.js` |
| `venue-profile.html` | Public venue profile. Tabs: Venue Info, Calendar, Videos, Pictures, Social Media, Reviews. Reads `?venue_id=N` | `venue-profile-init.js` |
| `legal.html` | Terms of service. 13 sections (A–M): Platform Role Disclaimer, Venue Responsibilities, Artist Representations, Indemnification, No Agency, Limitation of Liability, Dispute Resolution, Tax Disclaimer, User Content & Data, Right to Suspend, Modification of Terms, Governing Law, Contact | inline |
| `reset_password.html` | Set new password from emailed token | `reset_password-init.js` |
| `verify-email.html` | Landing for email verification token | (handled server-side at `/api/verify-email`) |
| `invited_user_create_user.html` | Accept an `entity_invitations` token; either creates a new user account or attaches an existing logged-in user to the entity | `invited_user_create_user-init.js` |
| `invited_user_declined.html` | Confirms a declined invitation | `invited_user_declined-init.js` |
| `support-ticket.html` | View / reply to a single support ticket via the token-authenticated link from email (`?token=...`) | `support-ticket-init.js` |
| `review.html` | Public token-auth review page (artist→venue or venue→artist) | `review-modal.js` (also used in app), inline |
| `contract-sign.html` | Token-auth contract signing page for an unsigned `gig_contracts` row | `contract-sign-init.js` |

### Authenticated app — user-facing

| Page | Purpose | Key JS |
|---|---|---|
| `user-profile.html` | The "home" of every logged-in user. 5 tabs: User Settings (name/email/phone/SMS carrier/password), My Artists (list + drag-to-reorder + delete), My Venues (same), Notifications (email + SMS prefs per notification type), Affiliates (Recommend GigsFill + earnings/payouts) | `user-profile-init.js`, `user-profile.js`, `user-affiliate.js`, `user-dropdown.js`, `email-verify-banner.js` |
| `artist-edit.html` | Edit artist profile. Header info, social links, media (photos/videos/audio), availability/blackout dates, users (entity_users), delete | `artist-edit-init.js`, `artist.edit.js`, `artist-availability.js`, `entity-users.js` |
| `venue-edit.html` | Edit venue profile + amenities + PRO licenses + tax settings + auto-contract content | `venue-edit-init.js`, `venue.edit.js`/`venue_edit.js` (two files — see "Known issues" below), `entity-users.js` |
| `artist-book-gigs.html` | **The main artist hub.** 7 tabs: Calendar (search + book), Activity Center (notifications/messages), My Venues (preferred status per venue), Payments (Stripe Connect), Legal/Taxes (W9, contracts), Users (team), Analytics. Reads `?artist_id=N` | `artist-book-gigs-init.js`, `artist.book-gigs.js`, `gig-modal.js`, `activity-center.js`, `my-venues-redesign.js`, `artist-stripe-payment.js`, `messages.js`, `flyer-overlay.js` |
| `venue-create-gigs.html` | **The main venue hub.** 8 tabs: Calendar (create/edit/cancel gigs, recurring), Activity Center, My Artists (preferred mgmt + invite), Email Center, Payments (Stripe card), Users, Analytics, Legal/Taxes (contracts, W9 requirement, 1099s, PRO). Reads `?venue_id=N` | `venue-create-gigs-init.js`, `venue.create-gigs.js`, `gig-modal.js`, `my-artists.js`, `flyer-editor.js`, `activity-center.js`, `venue.contracts.js`, `venue-stripe-payment.js`, `venue-payment-guard.js`, `messages.js` |
| `venue-email-center.html` | Compose+send mass email to artists, with history. Targets: preferred artists, all artists in radius, custom list | `venue-email-center.js` |
| `notifications-all.html` | Full-page notification list (mobile-friendly). Filters, mark-read, delete | `notifications-all.js` |
| `admin.html` | Admin dashboard. 8 tabs: Platform Settings (Stripe, SMTP, fees, kill-switches), Support (ticket inbox + reply), Email Templates (TinyMCE editor), Flyer Templates, Affiliates (referrals, accounting, manual link, run quarterly payouts), Analytics, Logs (in-memory ring buffer), Database (table browser + row editor + CSV export) | `admin-init.js`, `admin-platform.js`, `admin-templates.js`, `admin-affiliate.js`, `admin-logs.js`, `admin-db.js` |
| `diagnostics.html` | Self-test page that hits `/api/me`, checks service worker, fetches signup page — used to debug client-side caching / SW issues | inline |
| `index-app.html` | Alternate entry that pre-detects logged-in user and routes them | inline |

### Theme

`gigsfill.css` defines CSS variables in `:root`:
- `--bg: #0a0e17` (dark navy)
- `--card: #151b28`
- `--purple: #8b5cf6`
- `--cyan: #06b6d4`
- `--text: #f8fafc`
- `--text-gray: #94a3b8`
- `--border: rgba(148, 163, 184, 0.1)`

Brand gradients use `linear-gradient(135deg, #8b5cf6, #06b6d4)` (purple → cyan). Inter font from Google Fonts. Dark-mode-only.

`mobile.css` and `gigsfill-modern.css` are layered on top. Page-specific tweaks are inline in each HTML head.

### Service Worker / PWA

- `sw.js` — service worker: network-first for `/api/`, cache-first for `/app/static/`. Caches the app shell.
- `sw-register.js` — registers the SW on every page; forces update check on each load.
- `manifest.json` — PWA manifest with icons in `static/icons/` (192×192 and 512×512).

---

## 6. Shared frontend modules (the most important ones)

These are loaded across many pages. Behavior changes here affect everything.

| File | Role |
|---|---|
| `api.js` | Tiny ES-module fetch wrappers: `apiGet`, `apiPost`, `apiDelete`. Used by ES-module pages (`artist.book-gigs.js`, `notifications-all.js`, `venue.discovery.js`) |
| `auth.guard.js` | Runs on every authenticated page. Hits `/api/me`. If not logged in → redirect to `/app/index.html?redirect=...`. If `email_verified == 0` → redirect to a "verify required" page **except** for a small allowlist (user-profile and verify-email) so the user can resend the verification email. **Critical** — this is the main auth gate. |
| `security.js` | Global XSS helpers (`esc()`, etc.) — included before any other JS |
| `modals.js` | `showModal(title, body, buttons)`, `showSuccess()`, `showError()`. The custom modal system used everywhere. Builds an overlay div with the global ID `modalOverlay` |
| `event-delegate.js` | Phase 6 (in progress) — replaces inline `onclick=` with delegated handlers so we can drop `unsafe-inline` from CSP |
| `gig-modal.js` | **The shared gig modal.** Used by both `artist-book-gigs.html` and `venue-create-gigs.html`. `fetchModalData(gigId, role, entityId)` then `renderGigModal(data, callbacks)` where callbacks include `onBook`, `onCancelSlot`, `onCancelGig`, `onCountersign`, `onMessage`, `onJoinWaitlist`, `onLeaveWaitlist`. The single source of truth for what a gig looks like in a popup |
| `activity-center.js` | The "Activity Center" tab — clickable filter bubbles, lists all notifications + messages with deep-link open-the-gig actions |
| `flyer-editor.js` | Fabric.js canvas-based gig flyer editor. `window.flyerEditor.open(venueId, gigId)`. ~130KB |
| `flyer-overlay.js` | Lightweight read-only flyer renderer (no editing). Used on public-gigs and the gig modal |
| `city-autocomplete.js` | Shared autocomplete dropdown for city inputs. Has a page-blocking overlay when the entered city isn't in the US cities list. `initCityAutocomplete({ inputId, ... })` |
| `time-format.js` | Global `formatTime12Hour(time)` |
| `timezone-utils.js` | SQLite returns `CURRENT_TIMESTAMP` without 'Z' suffix; this normalizes timezone-aware display |
| `states.js` / `us-states.js` | US state lists (two files; `states.js` is ES-module export, `us-states.js` is global const — depending on which page needs which) |
| `maintenance-banner.js` | Polls `/api/maintenance-status` and shows a full-screen overlay when maintenance mode is on |
| `email-verify-banner.js` | On user-profile.html: dismissible top banner if `email_verified == 0`, with a "Resend verification email" button |
| `user-dropdown.js` | The header user menu (profile link, logout, switch artist/venue, notifications badge). Injected into every authenticated page |
| `onboarding-checklist.js` | Setup walkthrough modal shown on artist-book-gigs and venue-create-gigs until all setup tasks are done. Backend tracks task completion via `/api/onboarding/...` |
| `messages.js` | In-app gig messaging UI: per-gig thread, send, polling for new, header badge |
| `entity-users.js` | The Users tab on artist/venue edit pages — invite by email, set role, remove |
| `review-modal.js` | Shared rating/review modal used by both venue→artist and artist→venue review submission |
| `artist-reviews.js` | Star ratings + review cards + submit-review form rendered on artist-profile and venue-profile |
| `artist-availability.js` | Blackout date picker on artist-edit (and a venue-side "is artist available?" helper) |
| `venue-payment-guard.js` | On venue-create-gigs: if venue payments are suspended, blocks all tabs except Payments and shows an explanatory modal |
| `artist-stripe-payment.js`, `venue-stripe-payment.js` | The Stripe SetupIntent / Connect onboarding UI for each side |
| `my-artists.js` (venue side) | Preferred artist mgmt panel: pending requests, approved list, denied list, invite, search by city/state/styles |
| `my-venues-redesign.js` (artist side) | Preferred status panel from artist's POV: which venues approved/denied them, request preferred status |
| `venue.contracts.js` | Contract template manager on venue-create-gigs Legal tab. Three template types: PDF upload, custom builder (HTML form-style), auto-generated |

---

## 7. Backend — main.py and middleware

`backend/main.py` (1,361 lines) is where the FastAPI app is constructed. Order matters here — middleware is applied in the **reverse** order of `add_middleware` calls.

### Startup sequence (in order)

1. Configure root `logging` to stdout/journalctl, set `gigsfill.*` to INFO.
2. Install `_ErrorEmailHandler` on the `gigsfill` logger — sends an admin alert email on any `logger.error()` or `logger.critical()`. Throttled: max 1 alert per unique `(logger_name, message[:120])` per 5 minutes. SMTP creds are read fresh from `platform_settings` at send time; sends in a daemon thread so it never blocks request handling.
3. `ensure_database()` — runs `setup_database()` from `db.py` which creates all tables `IF NOT EXISTS` and runs `_add_columns()` for additive migrations. Also populates default `platform_settings` rows.
4. `ensure_email_templates()` — runs `run_migration()` from `email_templates.py` which upserts every template defined in code into the `email_templates` table.
5. **Schedulers — gated by env var.** If `GIGSFILL_RUN_SCHEDULERS` env var is set (`1`/`true`/`yes`), starts `start_payout_scheduler()` and `start_scheduler()` daemon threads. Otherwise logs a "schedulers not started" line and skips. The API service (`gigsfill.service`) does NOT set this var, so schedulers stay inert there. The dedicated scheduler service (`gigsfill-scheduler.service`) sets it, so schedulers run there. This guarantees exactly one process runs the schedulers regardless of how many uvicorn workers are running.

   The schedulers are normally started via `backend/scheduler_main.py` (the entrypoint for `gigsfill-scheduler.service`), not via main.py — but main.py honors the env var if set, useful for dev/local-testing where you might want everything in one process.
6. Register all routers (`auth`, `artists`, `venues`, `gigs`, `me`, `media`, `preferred_artists`, `notifications`, `cities`, `admin`, `emails`, `venue_emails`, `entity_users`, `analytics`, `tax`, `contracts`, `stripe_connect`, `flyers`, `onboarding`, `reviews`, `review_links`, `messages`, `availability`, `waitlist`, `gig_modal`, `affiliate`).

### Middleware stack (outer → inner)

1. **`StaticCacheMiddleware`** — sets `Cache-Control` headers: `no-cache` for HTML, `max-age=604800` (7d) for images, `must-revalidate` for JS/CSS/fonts.
2. **`MaintenanceModeMiddleware`** — if `platform_settings.maintenance_mode` is `true`/`1`, returns 503 JSON for any `/api/*` route except `/api/admin/*`, `/api/login`, `/api/logout`, `/api/me`, `/api/maintenance-status`. Static files always pass.
3. **`SecurityHeadersMiddleware`** — adds:
   - CSRF protection: blocks any cross-origin `POST/PUT/DELETE/PATCH` (except `/api/stripe/webhook`).
   - `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `X-XSS-Protection: 1; mode=block`.
   - `Referrer-Policy: strict-origin-when-cross-origin`.
   - `Permissions-Policy: camera=(), microphone=(), geolocation=()`.
   - `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
   - **Content Security Policy** — currently includes `'unsafe-inline'` for both script-src and style-src because of ~200 inline `onclick=` handlers in JS-generated HTML. The plan ("Phase 6") is to migrate these to `event-delegate.js` so a nonce-based CSP can replace `unsafe-inline`. Allowed external sources: `js.stripe.com`, `cdnjs.cloudflare.com`, `fonts.googleapis.com`, `fonts.gstatic.com`, `youtube.com`, `api.stripe.com`.
4. **`RollingSessionMiddleware`** — on every authenticated request, checks `should_renew_token(token)` — if more than 50% through its 7-day lifetime, re-issues a fresh cookie. Active users never get unexpectedly logged out.
5. **`GZipMiddleware`** — gzip responses larger than 500 bytes.
6. **`CORSMiddleware`** — origins from `CORS_ORIGINS` env var (default `http://127.0.0.1:8001`), allows credentials, all methods, all headers.

### Top-level routes in `main.py` (outside the routers)

- `GET /` → 302 redirect to `/app/index.html`
- `GET /robots.txt`, `GET /sitemap.xml`, `GET /sw.js` → static file responses
- `GET /health` → `{"status": "ok"}` (used by uptime checks)
- `GET /api/maintenance-status` → public maintenance state
- `GET /api/validate-city?city=X&state=Y` → checks city against `us_cities.US_CITIES`
- `POST /api/check-duplicate` (rate-limited 10/min) — duplicate-name check during signup
- `POST /api/request-access` (rate-limited 3/min) — emails the existing profile owner asking for access
- `POST /api/support/ticket` (rate-limited 2/min) — submit a new support ticket; sends both user-confirmation and admin-notification emails
- `GET /api/support/ticket/{ticket_id}?token=...` — token-authenticated ticket view (HMAC of `support-{id}-{email}` signed with `_SECRET_KEY`)
- `POST /api/support/ticket/{ticket_id}/reply?token=...` — user reply (rate-limited 5/min)
- `POST /api/recommend` — send a "recommend GigsFill" email to a friend
- `POST /api/coming-soon-notify` — collects emails on the coming-soon page (table `coming_soon_emails`)
- `POST /api/venues/{venue_id}/invite-artists` — bulk invite artists (max 50 at a time)
- `GET/POST/DELETE /api/venues/{venue_id}/invitations/...` — manage `artist_invitations` (resend, delete)
- `POST /api/gigs/{gig_id}/notes` — quick update of just the gig notes (works on booked gigs too)


---

## 8. Auth — `routes/auth.py`

### Sessions
- Library: `itsdangerous.URLSafeTimedSerializer`
- Secret: `GIGSFILL_SECRET_KEY` env var. Hard-fails in production if missing. In dev, persists a generated key to `.secret_key` file (must be in `.gitignore`).
- Token format: signed payload `{"uid": <user_id>}` with embedded timestamp.
- Cookie: `session_token`, HttpOnly, SameSite=Lax, Secure in production, 7-day max-age (configurable via `SESSION_MAX_AGE` env var).
- `set_session_cookie(response, user_id)`, `clear_session_cookie(response)` are the helpers.
- `get_current_user` is the FastAPI dependency that all authenticated routes depend on. `get_optional_user` returns `None` instead of raising.
- **Rolling expiry**: `should_renew_token(token)` returns True if >50% through lifetime. The middleware re-issues a fresh cookie automatically.

### Brute-force protection
- In-memory dict `_login_attempts: {email: {count, locked_until}}`, thread-locked.
- After **10 failed attempts**, account is locked for **15 minutes**. Successful login clears the counter.
- This is in addition to slowapi rate limiting at `5/minute` per IP for `/api/login`.

### Password hashing
- `bcrypt.hashpw(...)` for hashing, `bcrypt.checkpw(...)` for verify.
- Minimum password length: 6 characters (enforced in Pydantic).

### Endpoints

| Method | Path | What it does |
|---|---|---|
| POST | `/api/signup` | Create user + auto-create artist or venue profile in one transaction. Validates city against `us_cities.find_city()`. Server-side duplicate guard on `(name, city, state)`. On success: generates affiliate code, marks first-ever user as admin, links affiliate referral if `?aff=` cookie present, sends welcome email + verification email (in background thread), auto-logs in. Rate: `3/minute`. Honors `signups_enabled` setting. |
| POST | `/api/login` | Email + password → session cookie. Lockout-aware. Rate: `5/minute` |
| POST | `/api/logout` | Clears session cookie |
| POST | `/api/change-password` | Requires current password |
| POST | `/api/forgot-password` | Sends signed reset token (1h expiry, salt='password-reset'). Always returns success message even when email doesn't exist (anti-enumeration). Rate: `3/minute` |
| POST | `/api/reset-password` | Uses token to set new password |
| GET | `/api/verify-email?token=...` | Verifies email via signed token (72h expiry, salt='email-verify'). Renders a styled success/error HTML page with auto-redirect to `/app/user-profile.html` |
| POST | `/api/resend-verification-email` | Re-sends verification (rate: `3/hour`) |

### Authentication wrapper for state-changing endpoints
The `SecurityHeadersMiddleware` checks `Origin` header on POST/PUT/DELETE/PATCH against the request `Host`. If they don't match (and it's not a Stripe webhook), returns 403. This is the CSRF defense layer.

---

## 9. Routes — module-by-module summary

There are 27 route modules totaling ~360 endpoints. This section is a navigation guide rather than an exhaustive list.

### `routes/auth.py` (8 endpoints, 1,216 lines)
Covered in section 8.

### `routes/me.py` (10 endpoints)
The "me" endpoints — what's relevant to the current user.
- `GET /api/me` — user info + venue_id + first 10 artists/venues (ownership and entity_users access merged)
- `PUT /api/me` — update first/last/email/phone/sms_carrier
- `GET /api/my/artists`, `GET /api/my/venues` — full lists with display_order
- `PUT /api/my/artists/order`, `PUT /api/my/venues/order` — drag-and-drop reorder
- `GET /api/my-artist`, `GET /api/my-venue` — single primary artist/venue (legacy/convenience)
- `GET /api/me/delete-preview`, `DELETE /api/me/delete` — account deletion (preview shows what will be cascade-deleted)

### `routes/artists.py` (9 endpoints, 641 lines)
- `POST /api/artists` — create new artist for current user
- `GET /api/artists/search?city=&state=&styles=&type=` — public search (used by venues to find artists for preferred-status invitations)
- `GET /artists/{artist_id}` — public profile data
- `GET /api/artists/{artist_id}` — full artist data (auth required)
- `GET /api/artists/{artist_id}/access-check` — used by `artist.book-gigs.js` on page load to gate the page
- `PUT /artists/{artist_id}` — update artist (auth + access check)
- `GET /api/artists/{artist_id}/venues` — list of venues this artist has any relationship with (preferred status + booked gigs)
- `GET /api/artists/{artist_id}/venues/{venue_id}/gigs` — past + upcoming gigs at one venue
- `DELETE /api/artists/{artist_id}` — delete artist (with cascade)

### `routes/venues.py` (19 endpoints, 1,052 lines)
- `POST /api/venues` — create venue
- `GET /api/venues/public` — public venue list (with city/state filter)
- `GET /api/venues/{venue_id}/public` — single public venue
- `GET /api/venues/{venue_id}` — full venue data
- `GET /api/venues/{venue_id}/frequency` — current frequency_days setting
- `PUT /api/venues/{venue_id}` (and the legacy `/venues/{venue_id}`) — update venue
- `DELETE /api/venues/{venue_id}` — delete venue
- `GET /venues/{venue_id}/preferred-requests` — pending preferred requests this venue has received
- `POST /venues/{venue_id}/preferred-requests/{artist_id}` — venue accepts/denies a request
- `GET /venues/{venue_id}/preferred-status` — preferred status summary
- `GET /api/venues/{venue_id}/preferred-artists` — list of approved artists
- `POST /api/venues/{venue_id}/preferred-artists/{artist_id}/approve` — approve a request
- `GET/PUT /api/venues/{venue_id}/pro-licenses` — Performance Rights Org licenses (ASCAP/BMI/SESAC)
- `POST /api/venues/{venue_id}/pro-licenses/{pro_name}/upload` — upload license PDF
- `GET/PUT /api/venues/{venue_id}/settings/default-template` — default flyer template

### `routes/gigs.py` (34 endpoints, 4,754 lines)
The biggest file. Highlights:

- `POST /venues/{venue_id}/gigs` — create gig (single or recurring; if recurring, generates the full series with a shared `recurring_group_id`)
- `GET /gigs` — current user's gigs
- `GET /api/artists/{artist_id}/gigs/public` — artist's public gig calendar
- `GET /api/gigs/public?city=&state=&from=&to=` — public-gigs page calendar
- `GET /venues/{venue_id}/gigs` — venue calendar (auth)
- `GET /api/gigs/{gig_id}/detail` — full gig detail for the modal (now superseded by gig_modal.py)
- `GET /api/gigs/{gig_id}/effective-pay?artist_id=N` — returns `MAX(gig.pay, preferred_override)`
- `POST /api/gigs/{gig_id}/book?artist_id=N&blast_token=X` — book a single-slot gig (full pre-booking pipeline runs)
- `POST /api/gigs/{gig_id}/slots/{slot_id}/book?artist_id=N` — book one slot of a multi-slot gig
- `DELETE /api/gigs/{gig_id}/cancel` — artist cancels their booking (with reason; triggers blast to other artists if enabled)
- `DELETE /api/gigs/{gig_id}/slots/{slot_id}/cancel` — cancel one slot
- `DELETE /gigs/{gig_id}` — venue deletes an unbooked gig
- `DELETE /api/gigs/{gig_id}/with-slots` — venue force-deletes a multi-slot gig (cancels all bookings)
- `PUT /gigs/{gig_id}` — venue edits an unbooked gig
- `PUT /api/gigs/{gig_id}/booked-edit` — venue edits a booked gig (notes/details only — no pay/date changes that would invalidate contracts)
- `POST /api/gigs/{gig_id}/detach-series` — turn a recurring instance into a one-off
- `PUT /venues/{venue_id}/gigs/recurring/{recurring_group_id}` — update one occurrence in a series
- `PUT /venues/{venue_id}/gigs/recurring/{recurring_group_id}/update-series` — bulk update entire series (with options for "this and future")
- `DELETE /venues/{venue_id}/gigs/recurring/{recurring_group_id}` — delete entire series
- `GET /api/my/gigs` — current user's gigs (artists+venues merged, filtered by upcoming/past/cancelled)
- `GET /api/gigs/{gig_id}/slots` — list slots
- `GET /api/gigs/{gig_id}/approve-booking?artist_id=&token=` — landing page for the venue's email approval link (same-day bookings need approval). HTML response.
- `POST /api/gigs/{gig_id}/approve-booking`, `POST /api/gigs/{gig_id}/deny-booking` — approve/deny actions
- `POST /api/gigs/{gig_id}/new-gig-blast` — fire the blast email manually
- `POST /api/venues/{venue_id}/batch-blast` — bulk blast for a venue (background task)
- `GET /api/gigs/{gig_id}/flyer`, `GET /api/gigs/{gig_id}/flyer/public` — flyer rendering
- `GET /api/flyers/site-default-template`, `GET /api/flyers/{flyer_id}/detail`, `GET /api/gig-info-for-flyer/{gig_id}` — flyer helpers
- `GET /api/artists/{artist_id}/calendar.ics`, `GET /api/venues/{venue_id}/calendar.ics` — iCal export

### Pre-booking pipeline (`_run_prebooking_checks` and inline checks in `book_gig`)
Every booking goes through these checks **in this order**:
1. **Ban check** (`venue_artist_bans`) — always blocks, no exceptions
2. **Blast token / preferred check** — if a valid `radius_blast_token` is presented (or the gig already has one set, meaning it's "open blast"), the preferred-only restriction is waived. Otherwise the artist must have `preferred_artists.status = 'approved'` for this venue.
3. **W9 check** — if `venue_tax_settings.require_w9 = 1` and the artist has no W9 with `tax_year >= current_year`, error code `W9_REQUIRED`
4. **Frequency check** — if `preferred_artists.frequency_days_override` (or venue `artist_frequency_days`) is > 0 and there's another booked gig within that many days, error 403 with helpful message ("You have a gig X days later on..."). Waived inside the blast window or with a valid blast token.
5. **Waitlist lock** — if there's an active sequential offer to a different artist (in `gig_waitlist` or `waitlist_offered`), error code `WAITLIST_LOCKED`

### `routes/preferred_artists.py` (12 endpoints)
- `POST /api/venues/{venue_id}/preferred/request` — artist requests preferred status
- `GET /api/venues/{venue_id}/preferred/status` — check status for current artist
- `GET /api/artist/preferred-venues` — all venues that approved current artist
- `GET /api/venues/{venue_id}/preferred-artists-with-gigs` — preferred list with each artist's gig history
- `GET /api/artists/{artist_id}/gigs-at-venue/{venue_id}` — past gigs
- `PUT /api/preferred-artists/{id}/approve` — venue approves a request
- `PUT /api/preferred-artists/{id}/deny` — venue denies
- `PUT /api/preferred-artists/{id}/revoke` — venue revokes after approval

### `routes/contracts.py` (21 endpoints, 3,155 lines)
Three contract types:
- **`pdf_upload`** — venue uploads a PDF contract template; artist signs digitally on `contract-sign.html`; system generates a separate signature page PDF and merges it onto the original.
- **`custom_builder`** — venue defines fields (text, date, dropdown) in `custom_fields` JSON; artist fills + signs.
- **`auto_generated`** — venue provides HTML body; system substitutes `{{venue_name}}`, `{{artist_name}}`, `{{date}}`, `{{pay}}`, `{{start_time}}`, etc. and renders to PDF using ReportLab.

Endpoints group by:
- Templates (CRUD on `venue_contracts`): list, create, update, delete, upload-pdf, get-active
- Per-gig contract (`gig_contracts`): create, get-by-gig, get-by-id, sign, countersign, upload-signed (manual override)
- Booking-with-contract: `POST /api/gigs/{gig_id}/book-with-contract` — atomic book+contract creation; if venue requires contract for booking, this is the path.
- Hold cleanup: `POST /api/contract-holds/cleanup` — also runs every hour from the scheduler. Releases holds older than `hold_expires_at`.
- PDF download: `GET /api/gig-contracts/{contract_id}/download-pdf`

The contract hold flow: when an artist signs a contract on a `pending_contract` slot, the slot is held for them for 24 hours (recorded in `gig_contracts.hold_expires_at`). The venue must countersign within 24h or the hold is released and the slot reopens. The hourly `_run_contract_hold_cleanup` job releases expired holds.

### `routes/stripe_connect.py` (21 endpoints, 2,030 lines)
The full payment plumbing.

**Venue side (cards on file):**
- `POST /api/stripe/venue/{venue_id}/setup-intent` — create SetupIntent so the venue can save a card
- `POST /api/stripe/venue/{venue_id}/save-payment-method` — confirm setup, store `stripe_customer_id` + `stripe_payment_method_id` in `entity_payment_settings`
- `GET /api/stripe/venue/{venue_id}/payment-method` — get card-on-file info (last 4, brand)
- `DELETE /api/stripe/venue/{venue_id}/payment-method` — remove card

**Artist side (Connect Express for payouts):**
- `POST /api/stripe/artist/{artist_id}/create-connect-account` — creates an Express account (US, individual) and returns the onboarding URL with refresh/return URLs pointing back to `/app/artist-book-gigs.html?...&stripe_return=1`
- `GET /api/stripe/artist/{artist_id}/connect-status` — checks `charges_enabled` + `payouts_enabled` from Stripe; updates DB; returns bank info (last 4)
- `POST /api/stripe/artist/{artist_id}/dashboard-link` — creates an Express dashboard login link

**Misc:**
- `GET /api/stripe/config` — returns publishable key + fee split for the frontend
- `POST /api/stripe/charge-booking` — used by the legacy "charge at booking" path (currently mostly handled by the scheduler instead)
- `POST /api/stripe/process-payouts` — admin-trigger to run the payout scheduler immediately (also called by the scheduler itself)
- `POST /api/stripe/cancel-gig-payment`, `POST /api/stripe/reinstate-gig-payment` — cancel/reinstate a scheduled tx
- `GET /api/stripe/gig/{gig_id}/transaction-status` — current tx state for the modal
- `GET /api/stripe/venue/{venue_id}/transactions`, `GET /api/stripe/artist/{artist_id}/transactions` — transaction history pages
- `GET /api/stripe/venue/{venue_id}/upcoming-charges`, `GET /api/stripe/artist/{artist_id}/upcoming-payouts` — future-dated tx
- `GET /api/stripe/venue/{venue_id}/payment-status` — overall status (active/suspended/free trial)
- `GET /api/stripe/artist/{artist_id}/earnings-summary` — totals
- `POST /api/stripe/webhook` — Stripe webhook handler (verifies signature using `admin_stripe_webhook_secret`)
- `GET /api/payment-info?venue_id=N` — used by the frontend to detect free-trial mode and show the trial badge

**Fee calculation** (centralized in `_create_booking_transaction` in `gigs.py`):
- `total_fee_cents = max(amount_cents * fee_pct, min_fee_cents)`
- Split:
  - `split` (default): venue and artist each pay 50% of the fee
  - `venue_only`: venue covers all of it (artist gets the listed pay in full)
  - `artist_only`: artist's payout = listed pay - full fee
- `artist_payout_cents = max(0, amount_cents - artist_fee_share)` — never negative.

### `routes/availability.py` (6 endpoints)
Artist blackout dates (date ranges the artist is unavailable).
- `GET /api/artists/{artist_id}/availability` — list blackouts
- `GET /api/artists/{artist_id}/available?date=YYYY-MM-DD` — single-date check
- `POST /api/artists/{artist_id}/availability` — add blackout
- `DELETE /api/artists/{artist_id}/availability/{blackout_id}`
- `PUT /api/artists/{artist_id}/availability/{blackout_id}`
- `POST /api/artists/{artist_id}/availability/check-bulk` — bulk-check many dates (used by the calendar)

### `routes/notifications.py` (5 endpoints)
- `GET /api/notifications?limit=` — list
- `GET /api/notifications/unread-count` — for header badge
- `POST /api/notifications/{id}/read` — mark one
- `POST /api/notifications/mark-all-read`
- `DELETE /api/notifications/{id}`

### `routes/messages.py` (6 endpoints, 1,051 lines)
Per-gig venue↔artist chat.
- `GET /api/gigs/{gig_id}/messages` — load thread (auth checks role for this gig)
- `POST /api/gigs/{gig_id}/messages` — send message (also creates a notification + email to the recipient)
- `PUT /api/gigs/{gig_id}/messages/read` — mark all read for current user
- `GET /api/me/messages` — list of all gig threads with latest message + unread count
- `GET /api/me/messages/unread-count` — for header badge

For multi-slot gigs, venue→artist messages can target a specific artist via `target_artist_id` (so when the venue messages "Artist A", "Artist B" doesn't see it).

### `routes/waitlist.py` (7 endpoints)
The sequential-offer waitlist. See section 4 for the data model. Key endpoints:
- `POST /api/gigs/{gig_id}/waitlist/join` (and the legacy `POST /api/gigs/{gig_id}/waitlist`) — artist joins
- `DELETE /api/gigs/{gig_id}/waitlist?artist_id=N` — leave
- `GET /api/gigs/{gig_id}/waitlist/status?artist_id=N`
- `GET /api/venues/{venue_id}/gigs/{gig_id}/waitlist` — venue view of the line
- `GET /api/artists/{artist_id}/waitlist` — artist's active waitlist entries
- `GET /api/waitlist/respond?token=...&action=accept|decline` — token-authed response link from email

### `routes/reviews.py` (19 endpoints)
Two-way reviews: venue→artist and artist→venue.
- After a gig completes, both sides can leave a rating + body.
- Reviews can be made `visible=false` by admin.
- Email reminders sent via `routes/review_links.py` and the scheduler.

### `routes/review_links.py` (2 endpoints)
- `GET /api/review-link?token=` — token-authed page
- `POST /api/review-link/submit` — submit review without logging in

### `routes/flyers.py` (15 endpoints)
- `GET /api/venues/{venue_id}/flyers` — list flyers for a venue
- `GET /api/venues/{venue_id}/flyer-templates` — venue + admin global templates
- `POST /api/venues/{venue_id}/flyers/upload-image` — image upload for the editor
- `PUT /api/venues/{venue_id}/flyers/default-template` — set default template
- `POST/PUT/DELETE /api/venues/{venue_id}/flyers/{flyer_id}` — CRUD
- `POST /api/flyers/ai-generate` — AI image generation for backgrounds (planned/partial)
- `GET /api/flyers/proxy-image?url=` — CORS-proxy for external images so the canvas can include them

Flyers are stored as Fabric.js JSON in `flyers.canvas_json` and rendered both in the in-browser editor and server-side as PNG (via headless rendering / cached). The "site default" template is configured by admin in the Flyer Templates tab.

### `routes/tax.py` (17 endpoints)
- `GET/PUT /api/artists/{artist_id}/w9` — W9 form CRUD
- `POST /api/artists/{artist_id}/w9/recertify` — re-certify for new tax year
- `GET /api/artists/{artist_id}/w9-status` — does this artist have a current-year W9?
- `GET/PUT /api/venues/{venue_id}/tax-settings` — toggle `require_w9`
- `GET /api/venues/{venue_id}/requires-w9` — public flag for the booking flow
- `POST /api/venues/{venue_id}/generate-1099s` — generate 1099 forms for all artists who earned >= $600 in a tax year
- Plus admin endpoints for 1099 review/sending

### `routes/admin.py` (40 endpoints, 1,989 lines)
All admin-only (`check_admin` dep). Covers:
- Stats/system health (`/api/admin/stats`, `/api/admin/system-health`)
- User/artist/venue/gig admin tables (CRUD + search)
- Settings (`/api/admin/settings`, `/api/admin/payment-settings`)
- Venue payment overrides (`/api/admin/venue-payment-overrides` — set free-trial / suspend)
- Support tickets (list, update status, post admin reply)
- Email templates (list, edit, export)
- Accounting reports (`/api/admin/accounting`)
- Flyer templates (admin-global templates)
- Logs (read/clear in-memory ring buffer)
- Database browser (`/api/admin/db/tables`, schema, rows, edit/delete/insert/export-CSV)
- SMTP test (`/api/admin/test-smtp`)

### `routes/affiliate.py` (24 endpoints, 1,247 lines)
- `GET /api/affiliate/track/{code}` — affiliate click tracking; sets `aff_code` cookie (90d) and redirects
- `POST /api/affiliate/recommend` — send recommend email to friends
- `GET /api/affiliate/my-emails` — sent recommend emails + click tracking
- `POST /api/affiliate/resend-recommend/{email_id}`
- `GET /api/affiliate/my-referrals` — venues this user has referred
- `GET /api/affiliate/my-summary` — earnings totals (current quarter, lifetime, paid, pending)
- `GET /api/affiliate/program-settings` — public rates
- `POST /api/affiliate/stripe/onboard` — Stripe Connect Express for affiliate payouts
- `GET /api/affiliate/stripe/status` — onboarding status
- `POST /api/affiliate/use-artist-stripe` — reuse the artist's already-onboarded Connect account for affiliate payouts (avoids onboarding twice)
- `GET /api/affiliate/artist-stripe-accounts` — list of user's artist Connect accounts
- `GET /api/affiliate/my-venue-earnings/{venue_id}` — per-venue earnings detail
- `GET /api/affiliate/check-new-venues` — banner: "you have new referred venues that signed up!"
- `POST /api/affiliate/dismiss-w9-prompt`
- Admin: `GET /api/admin/affiliate/payout-preview`, `GET /api/admin/affiliate/settings`, `GET /api/admin/affiliate/accounting`, `GET /api/admin/affiliate/accounting/{user_id}`, `GET /api/admin/affiliate/referrals`, `POST /api/admin/affiliate/manual-link`, `DELETE /api/admin/affiliate/referrals/{referral_id}`, `GET /api/admin/affiliate/venue-search`, `POST /api/admin/affiliate/run-payouts`

`accrue_affiliate_earnings(db, transaction_id)` is called from `payout_scheduler` after a tx is paid. It looks up the venue's affiliate referral, computes earned cents at the current rate, and inserts an `affiliate_earnings` row tagged with the current quarter.

`run_quarterly_affiliate_payouts(db)` is called by the scheduler on Apr 1, Jul 1, Oct 1, Dec 31 — but the **default flow now sends an admin reminder email instead of auto-running**, so the admin reviews the data and clicks "Run Quarterly Payouts Now" in the admin panel. This was a deliberate change to give admin a manual review step before money moves.

### `routes/analytics.py` (9 endpoints)
- `POST /track` — fire-and-forget analytics event recorder (no auth, used by `public-gigs.js`)
- `GET /stats/cities` — gigs by city heatmap
- `GET /stats/gigs` — gig totals
- `GET /stats/summary`, `GET /stats/details`, `GET /stats/visitors` — admin dashboard stats
- `GET /stats/venue/{venue_id}`, `GET /stats/artist/{artist_id}` — per-entity analytics
- `GET /api/analytics/stats/admin-dashboard` — used by `admin-init.js`

### `routes/onboarding.py` (2 endpoints)
- `GET /api/onboarding/{entity_type}/{entity_id}` — list of setup tasks + completion status
- `POST /api/onboarding/{entity_type}/{entity_id}/{task_key}/visit` — mark a task as visited (some are auto-completed by visiting the relevant tab)

### `routes/entity_users.py` (12 endpoints)
The Users tab on artist/venue pages.
- `GET /api/entity-users/artist/{artist_id}` (and venue equivalent) — list members + pending invitations
- `POST /api/entity-users/artist/{artist_id}/invite` — invite by email; creates `entity_invitations` token; emails the invite
- `POST /api/entity-invitations/{invitation_id}/reinvite` — re-send
- `DELETE /api/entity-users/artist/{artist_id}/remove/{target_user_id}` — remove a member
- `GET /api/users/lookup-by-email?email=` — for the invite UI

### `routes/cities.py` (3 endpoints)
- `GET /api/cities/search?q=` — autocomplete from `us_cities`
- `GET /api/cities/all`
- `GET /api/cities/distance?from_city=&to_city=` — haversine

### `routes/emails.py` (7 endpoints)
- `PUT /api/email-templates/{notification_type}` — admin update a template (also exists in admin.py)
- `GET/PUT /api/user-email-preferences` — per-user email opt-in/out
- `GET /api/sms-carriers` — list of supported carriers
- `GET/PUT /api/user-sms-preferences` — per-user SMS opt-in/out
- `PUT /api/user-sms-carrier` — set the user's SMS carrier

### `routes/venue_emails.py` (6 endpoints)
- `POST /api/venues/send-email` — send a custom email blast from a venue to a recipient list
- `GET /api/venues/email-history`, `GET /api/venue-emails/history` — bulk email history
- `GET/POST /api/venues/{venue_id}/email-notifications` — venue-side blast schedule (`open_gig_36h`, `open_gig_1w`, `open_gig_2w`, `open_gig_4w`)
- `GET /api/venues/{venue_id}/blast-settings/public` — public flag for whether this venue does blasts (artists see this on the venue page)

### `routes/media.py` (8 endpoints)
- `GET /api/artists/{artist_id}/media`, `GET /api/venues/{venue_id}/media`
- `POST /api/artists/{artist_id}/media/{media_type}`, `POST /api/venues/{venue_id}/media/{media_type}` — upload (photo/video file or video URL)
- `PUT /api/media/{media_id}`, `PUT /api/venues/media/{media_id}` — edit (title, display_order)
- `DELETE /api/media/{media_id}`, `DELETE /api/venues/media/{media_id}`

### `routes/gig_modal.py` (1 endpoint)
- `GET /api/gigs/{gig_id}/modal-data?role=&entity_id=` — single endpoint that consolidates everything the gig modal needs (gig info, venue info, artist info, slots, contract status, message thread, waitlist, etc.). Replaces ~5 separate fetches.


---

## 10. Background services

**Architecture (since May 2026).** The two scheduler threads — `payout_scheduler` (charges venues + transfers payouts) and `scheduler` (hourly email blasts + waitlist sweeps + cleanup) — run in a **dedicated systemd service**: `gigsfill-scheduler.service`. The entrypoint is `backend/scheduler_main.py`, which is invoked as `python -m backend.scheduler_main`. That service runs as a single process; the API service (`gigsfill.service`, multiple uvicorn workers) does NOT start the schedulers. The split is gated by the `GIGSFILL_RUN_SCHEDULERS` env var (set only in the scheduler unit's `Environment=` directive).

This eliminates the "two-uvicorn-workers-both-running-the-scheduler" duplicate-email problem that existed when the schedulers ran inside the API process. There used to be a `fcntl` file lock at `/tmp/gigsfill_scheduler.lock` to coordinate workers, but it had a race condition (truncation on `open(path, 'w')` confused stale-lock detection) and is now removed entirely. Operationally, if you ever see duplicate emails again, check `systemctl status gigsfill-scheduler` and `ps aux | grep scheduler_main` — there should be exactly one process.

`backend/scheduler_main.py` does:
1. Configure logging (same handlers as `main.py`).
2. Run `setup_database()` and `email_templates.run_migration()` (idempotent; safe even if the API service also ran them on startup).
3. Call `start_payout_scheduler()` and `start_scheduler()` to spawn the two daemon threads.
4. Install SIGTERM/SIGINT handlers and block on a 60-second sleep loop. On signal, sets a flag that breaks the loop and exits cleanly (daemon threads die with the process). systemd handles restart-on-crash.

Both `start_payout_scheduler()` and `start_scheduler()` have an in-process `_*_started` guard that no-ops on a second call within the same process — defensive belt-and-suspenders even though only one process runs them now.

### `payout_scheduler.py` — daily charge & transfer worker

**Thread:** `PayoutScheduler` daemon thread, started by `scheduler_main.py`.

**Loop:** runs every minute. On each tick: (1) settles any test-mode transactions that have been "transferred" for ≥ 2 hours; (2) once per UTC hour, runs `process_payouts_now()` which sweeps for any transactions whose `scheduled_process_at` UTC time has passed. The hourly sweep means each venue's payouts fire within ~1 hour of their local-tz scheduled time — a venue in Pacific has its 5pm local payout fire at 00:00-01:00 UTC, a venue in Eastern has its 5pm local fire at 21:00-22:00 UTC, etc. Per-venue scheduling is encoded in the UTC timestamp at booking time (see `venues.timezone` and `backend/utils.get_venue_timezone()`).

**`process_payouts_now()` flow:**

1. **Fetch pending parent transactions** — selects `transactions` rows where `status IN ('scheduled', 'test', 'charge_retry')`, `transaction_type IN ('venue_charge', 'single')`, and `scheduled_process_at <= now`. Joins to `gigs` for context.

2. **For each transaction:**
   - **Atomic claim**: `UPDATE transactions SET status = 'processing' WHERE id = ? AND status = ?`. If 0 rows updated → already claimed (this is now a defense-in-depth guard, not the primary one — only one process runs this loop).
   - **Free-trial check** (applies to test AND live): if `venue_payment_overrides.payments_suspended = 1` for this venue, mark tx `suspended` with note "Free trial venue — direct payment", skip.
   - **Test mode** (`payments_enabled = 0`): mark `transferred` with `stripe_transfer_id = 'test_transfer'` and a 2-hour delay before final settlement. Send the payout email to the artist (so they can see the flow). Accrue affiliate earnings. Continue.
   - **Live mode**:
     - If no Stripe key configured, send admin alert "No Stripe Key — Payments Cannot Process" and `break` (don't try any more — they'd all fail).
     - If venue has no card on file → `_handle_charge_failure` (increments `charge_attempts`, schedules retry next day, on attempt 3 → `suspended`), send admin alert.
     - Create `PaymentIntent` with `off_session=True, confirm=True`, idempotency key `gig_{gig_id}_txn_{txn_id}_charge`, customer + payment method from `entity_payment_settings`.
     - On `CardError`: `_handle_charge_failure` + admin alert + venue email warning ("attempt N of 3, please update card").
     - On other Exception: same.
     - On success: store `stripe_payment_intent_id`, retrieve the underlying `charge_id` via `expand=["latest_charge"]`, mark parent as `charged`, send venue-charged email.
   - **Transfer to artists**: get all child `artist_payout` rows (or the parent itself if `transaction_type = 'single'`) and call `_transfer_to_artists()`. This creates `Transfer` objects with `source_transaction=charge_id` to bypass pending balance, sends payout email per artist on success, marks rows `transferred`. On failure, marks `transfer_failed` and sends a transfer-failed email to both artist and venue (templates `transfer_failed_artist`, `transfer_failed_venue`).

3. **Retry stalled transfers**: query for `pending_transfer` and `transfer_failed` rows, re-attempt them if the artist's Connect onboarding is now complete.

4. **Auto-settle test transactions**: 2 hours after marking `transferred`, mark them `paid` so the artist sees the final state.

**Failure escalation:**
- 3 failed charge attempts → set `venues.payment_status = 'suspended'`, send venue-suspended email and admin alert. Suspended venues are hidden from search.
- The frontend (`venue-payment-guard.js`) detects suspension and shows a blocking modal with only the Payments tab accessible.

**Free-trial venues:**
- Set via Admin → Venue Payment Overrides. `venue_payment_overrides.payments_suspended = 1` with a note like "Free trial — Q1 2026".
- All bookings at that venue **skip transaction creation entirely** (in `_create_booking_transaction`) — direct artist↔venue payment outside the platform.
- The frontend shows a "🎟 Free Trial" badge in the venue header.

### `scheduler.py` — hourly email blast + waitlist worker

**Thread:** `EmailScheduler` daemon thread, started by `scheduler_main.py`. Used to coordinate across uvicorn workers via `fcntl.flock`; that lock has been removed since only one process (`gigsfill-scheduler.service`) ever runs the scheduler now.

**Loop schedule:**
- Every 10 minutes: `process_waitlist_expirations()` (advance sequential offers, prune rows for past gigs)
- Every 1 hour (gated by `last_email_run` timestamp): `run_scheduled_emails()` plus `_run_contract_hold_cleanup()`, `_run_started_gig_waitlist_cleanup()`, `_run_wal_checkpoint()`
- On quarterly dates (Apr 1 / Jul 1 / Oct 1 / Dec 31): `send_quarterly_affiliate_reminder()` — admin email summarizing eligible affiliate payouts; admin must then manually click "Run Quarterly Payouts Now" in the admin panel to actually disburse

**Per-function audit (verified May 2026):**

| Function | Triggers | Dedup mechanism | Status |
|---|---|---|---|
| `process_gig_confirmation` | Booked gigs at venue's configured lead time before gig (default 1 week) | `gig_email_log` keyed `(gig_id, 'gig_confirmation')`. Uses `INSERT ... ON CONFLICT DO UPDATE` so multi-slot gigs increment `recipient_count` correctly | ✅ Working (May 2026 fix). |
| `process_open_gig_notifications('open_gig_4w' / '2w' / '1w' / '36h')` | Open gigs at the configured lead time (4w / 2w / 1w / 36h before gig start). Sends to preferred + (if `blast_all_enabled`) all artists in radius. Stamps `gigs.frequency_exempt = 1` so any approved artist can book. | `gig_email_log` keyed `(gig_id, notification_key)` via `INSERT OR IGNORE`. Once sent, never re-fires regardless of venue setting changes. | ✅ Working (May 2026 fix). |
| `process_radius_blast` | (No longer scheduled.) Function still exists in file for back-compat with manual callers, but removed from `run_scheduled_emails` because it overlapped with `open_gig_36h`. | n/a | 🚫 Disabled (May 2026 fix). |
| `process_review_requests` | 12+ hours after end_time of `booked`/`completed`/`closed` gigs from the past 7 days; sends one venue→artist email per gig and one artist→venue email **per artist on the gig** (multi-slot). Includes a one-time signed token link. | Venue side: `gig_email_log` keyed `(gig_id, 'venue_review_request')`. Artist side: `gig_email_log` keyed `(gig_id, 'artist_review_request:{artist_id}')` — per-artist suffix encoded in notification_key. | ✅ Working (May 2026 fix). |
| `process_waitlist_expirations` | Every 10 min. Finds offers where `offer_expires_at < now`, deletes the waitlist row, calls `advance_waitlist_offer` for the next artist in line. Also calls `fire_cancelled_gig_blast` when waitlist exhausts. | `gig_waitlist.offer_sent` flag prevents same-row reprocessing | ✅ Working |
| `_run_contract_hold_cleanup` | Calls `cleanup_expired_holds()` from `routes/contracts.py`. Releases gigs in `pending_contract`/`awaiting_venue_contract` past their `contract_hold_expires_at`. | DB state-based (gig status); not log-based | ✅ Working (May 2026 fix to log message wording) |
| `_run_started_gig_waitlist_cleanup` | Hourly. Deletes `gig_waitlist` and `waitlist_offered` rows for gigs whose start_time has passed. | DB state-based | ✅ Working. Minor: uses SQLite `date('now', 'localtime')` which on a UTC server means UTC, not platform timezone — so cleanup happens up to ~8h late vs platform tz. No user-visible impact. |
| `_run_wal_checkpoint` | Hourly. Runs `PRAGMA wal_checkpoint(TRUNCATE)` if `backend.db-wal` exceeds 10 MB. | N/A — pure housekeeping | ✅ Working |

**Email preference defaults** (from `email_service.user_has_email_enabled` and the canonical `BLAST_OFF_DEFAULTS` constant in `email_service.py`): notifications default ON for transactional emails (booking, cancellation, contract signed, etc.). Blast emails are split: **long-lead-time blasts default OFF** (`venue_open_gig_4w`, `venue_open_gig_2w`) — artists must explicitly opt in via the user-profile Notifications tab. **Urgent blasts default ON** (`venue_open_gig_1w`, `venue_open_gig_36h`, `cancelled_gig_radius_blast`, `cancelled_gig_preferred_blast`) — these are time-sensitive "this gig is starting soon / opened up" emails where missing one is a real cost. The scheduler's `process_open_gig_notifications` and the email_service code share the same `BLAST_OFF_DEFAULTS` constant so all paths agree.

**SMTP gate**: if `platform_settings.platform_email`/`platform_email_password` are not configured, `run_scheduled_emails` logs a warning and exits without running any of the per-function processors. So a half-configured platform won't fire any blasts.

### `services/notification_service.py`
Centralized notification creators:
- `create_notification(db, user_id, type, title, message, gig_id=, venue_id=, artist_id=, cancellation_reason=)` — single insert
- `notify_gig_booked(db, gig_details, gig_id, venue_id, artist_id)` — notifies all entity_users for both artist and venue, deduping if same user owns both
- `notify_gig_cancelled(db, gig_details, ..., cancelled_by='venue', cancellation_reason='', slot_info='')` — direction-aware messages
- `notify_all_entity_users_cancelled(...)` — wider broadcast for venue-initiated cancellations
- `notify_gig_edited(db, gig_id, venue_id, venue_name, date)` — tells booked artists the gig was edited (with slot details)
- `format_time_12hr(time_str)` — utility used everywhere

### `services/email_dispatch.py`
The "send the right email to the right people" service. Major functions:
- `send_booking_emails(db, gig_id_or_details, slot_id=None)` — for each booked artist, sends `artist_gig_booked` template (respecting prefs) + sends `venue_gig_booked` to all venue users (bypasses prefs because venues must always know about bookings)
- `send_cancellation_emails(db, gig_details, cancellation_reason='', slot_info='', skip_venue_email=False)` — symmetric for cancellations
- `send_contract_sign_email(db, venue_id, artist_id, gig_id, gig_date)` — when artist signs a contract, notify the venue to countersign
- `send_gig_edited_emails(db, gig_id)` — venue edited a booked gig
- `send_approval_request_emails(db, gig_details, artist_id, slot_info='')` — same-day booking by non-preferred artist requires venue approval; this sends the email with approve/deny links
- `send_approval_decision_emails(db, gig_details, artist_id, decision, ...)` — venue approved or denied the same-day request
- `format_email_date(date_val)` — converts date string/object to "Friday, March 6, 2026"
- `_fetch_venue_detail_vars(db, venue_id, gig_notes)` — returns the "venue address / capacity / arrival / stage / sound / engineer / lighting / bar tab / food tab" template variables used in many emails
- `_get_effective_pay_for_slot(db, venue_id, artist_id, base_pay)` — `MAX(base_pay, preferred_override)` for display

### `services/gig_cleanup.py`
The single source of truth for "what to delete when a gig or slot goes away":
- `cleanup_gig_records(db, gig_id, artist_id=None)` — removes related transactions, contracts, payment_cancellations, contract notifications. If `artist_id` given (slot-level), only that artist's records; also adjusts the parent `venue_charge` transaction's amounts so it stays accurate.
- `delete_gig_completely(db, gig_id)` — for venue-initiated full deletes; cleans everything including messages, waitlist, flyers, file uploads.

The `CONTRACT_NOTIFICATION_TYPES` constant lists the notification types tied to a booking (`contract_signed`, `gig_booked`, etc.) — these are removed when a booking is undone.

---

## 11. Email system

### Architecture
- Templates defined in code (`backend/email_templates.py` — 2,577 lines, ~80+ templates) as `TEMPLATES = {key: {subject, body}, ...}`.
- On startup, `_populate_email_templates()` upserts all of them into the `email_templates` table (`ON CONFLICT(template_key) DO UPDATE`). This means edits to `email_templates.py` will overwrite admin DB edits on next restart unless the admin re-edits via the admin UI.
- The `EmailService` class (in `email_service.py`) is the standard send path:
  - `__init__` reads SMTP config from `platform_settings` (`platform_email`, `platform_email_password`, `platform_smtp_server`, `platform_smtp_port`, `platform_email_from_name`)
  - `get_template(notification_type)` — DB lookup, falls back to in-memory `TEMPLATES` dict
  - `render_template(template, variables)` — handles `{{var}}` substitution AND `{{#var}}...{{/var}}` conditional blocks (rendered only when var truthy)
  - `user_has_email_enabled(user_id, notification_type)` — checks `email_preferences`. Default ON for transactional emails. Default OFF only for the long-lead-time blasts in the module-level `BLAST_OFF_DEFAULTS` constant (`venue_open_gig_4w`, `venue_open_gig_2w`). Urgent blasts (`_1w`, `_36h`, cancellation blasts) default ON.
  - `send_notification_email(user_email, user_id, notification_type, variables)` — orchestrates all of the above + actual SMTP send via `_smtp_send`
- `_smtp_send` handles port 465 (SSL_), 587 (STARTTLS), and others (plain w/ try-STARTTLS).
- On SMTP failure, throttled admin alert via `_alert_admin_smtp_failure` (1 per 15 min).

### Template variable conventions
Most templates use these standard variables:
- `{{user_name}}`, `{{user_email}}`, `{{first_name}}`
- `{{venue_name}}`, `{{artist_name}}`, `{{venue_id}}`, `{{artist_id}}`, `{{gig_id}}`
- `{{date}}`, `{{start_time}}`, `{{end_time}}`, `{{pay}}`
- `{{title}}`, `{{artist_type}}`, `{{band_formats}}`, `{{styles}}`
- Venue detail vars from `_fetch_venue_detail_vars`: `{{venue_address}}`, `{{venue_capacity}}`, `{{arrival_info}}`, `{{stage_info}}`, `{{sound_info}}`, `{{engineer_info}}`, `{{lighting_info}}`, `{{bar_tab}}`, `{{food_tab}}`, `{{venue_notes}}`

Admin can edit any template via the admin Email Templates tab (TinyMCE editor). Variables list per template is hardcoded in `admin-templates.js`.

### SMS (carrier email-to-SMS gateways)
`backend/sms_service.py` defines `CARRIER_GATEWAYS` (e.g. `att → txt.att.net`, `verizon → vtext.com`, `tmobile → tmomail.net`, plus 9 more US carriers) and `SMS_TEMPLATES` (~14 short-form templates ≤155 chars each). Sends via the same SMTP — `phone@gateway` is the recipient. Users opt-in per notification type via the SMS preferences UI.

---

## 12. Booking flow — end to end

This is the main flow worth understanding. Walking through "artist books an open slot":

1. **Artist sees the gig** on `/app/artist-book-gigs.html?artist_id=N`. Calendar fetches `/api/gigs/public` (for the search calendar) or filtered queries.
2. **Artist clicks the gig** → `gig-modal.js` loads `/api/gigs/{gig_id}/modal-data` and renders the unified modal with a "Book" button if eligible.
3. **Artist clicks Book** → POST `/api/gigs/{gig_id}/book?artist_id=N` (or `/api/gigs/{gig_id}/slots/{slot_id}/book?artist_id=N` for multi-slot).
4. **Backend pre-booking pipeline** (`book_gig` in `routes/gigs.py`):
   - Auth: `get_current_user` + verify the user owns/has access to this artist
   - `_run_prebooking_checks` (or inline equivalent for `book_gig`): ban, blast token / preferred status, W9, frequency, waitlist lock — all in order, first failure = HTTP 403 with code (e.g. `WAITLIST_LOCKED`, `W9_REQUIRED`)
   - **Same-day booking gate**: if the gig is today AND the artist is non-preferred (got in via blast), the booking goes to `pending_venue_approval` status instead of `booked`. Venue gets an email with approve/deny links → on approve, runs the rest of the booking; on deny, marks gig back to open.
5. **Contract gate**: if `venue_contracts.require_for_booking = 1` for any active contract, the booking endpoint returns a "contract required" response. The frontend then redirects to `/app/contract-sign.html` for the artist to sign first. After signing, slot status = `pending_contract` with a 24h hold until venue countersigns.
6. **Booking commit** (when no contract gate or after both signatures):
   - Update `gig_slots` (or `gigs` for single-slot) → status `booked`, `artist_id = N`
   - If multi-slot and all slots now booked → also set `gigs.status = 'booked'`
   - Apply pay override at slot level (don't write to `gigs.pay` — that would corrupt other slots' listed pay)
7. **Post-booking side effects**:
   - `_create_booking_transaction(db, gig_id, venue_id, artist_id, pay, gig_date, slot_id)` — creates/updates transaction rows scheduled for the day after the gig at the configured hour. Skipped entirely for free-trial venues. Multi-slot gigs accumulate into a single venue_charge parent + per-artist children.
   - `notify_gig_booked(...)` — in-app notifications to all artist + venue entity users
   - `send_booking_emails(...)` — emails to all artist users + all venue users
   - Cancel any active waitlist offer for this slot (`waitlist_offered` cleanup)
   - Auto-create a flyer if the venue has a default template (`auto_create_flyer`)
   - Affiliate accrual is **not** done here — it's done after the transaction is paid in `payout_scheduler`
8. **The day after the gig at 5pm** (configurable):
   - `payout_scheduler` charges the venue (one charge per multi-slot gig, summed)
   - On success, transfers each artist's payout to their Stripe Connect account
   - Sends "venue charged" + "artist payout" emails
   - Calls `accrue_affiliate_earnings()` — if the venue has an affiliate referral, records earnings in `affiliate_earnings`

### Cancellation flow
- **Artist cancels**: `DELETE /api/gigs/{gig_id}/cancel` or `/slots/{slot_id}/cancel`. Slot returns to `open`. Cleanup runs. Notifications + emails fire. If the venue has open-gig blasts enabled and the gig is now within the blast window, fire a blast (or advance the waitlist).
- **Venue cancels**: same endpoints, but with venue auth path. Sends blast + advances waitlist as appropriate. Triggers transaction cancellation via Stripe (refund the captured charge if already charged).
- **Frequency-exempt re-bookings**: if a slot is cancelled-and-rebooked within seconds (artist mistake), the `frequency_exempt = 1` flag can be set on the gig to bypass the frequency check on the next booking attempt.

### Recurring gigs
- Created by including `is_recurring=true` + `recurring_*` fields in the create-gig payload
- `generate_recurring_dates_backend()` produces the date series based on `interval_weeks`, `days_of_week` (CSV like "Mon,Wed,Fri"), `end_type` (`after`/`by_date`/`never`), `end_after` (count) or `end_by_date`
- All gigs in the series share a `recurring_group_id` (UUID)
- Editing one occurrence: `POST /api/gigs/{gig_id}/detach-series` first (turns it into a standalone), then edit normally
- Editing all in the series: `PUT /venues/{venue_id}/gigs/recurring/{recurring_group_id}/update-series`
- Deleting: `DELETE /venues/{venue_id}/gigs/recurring/{recurring_group_id}` (with options for "this only" / "this and future")

---

## 13. Affiliate program

A user becomes an affiliate by sending recommend emails. Every user has an `affiliate_code` (auto-generated on signup, format `AFF-XXXXXXXX`).

### Linking a venue to an affiliate
When a venue signs up, the auth.py signup handler tries (in order):
1. `data['affiliate_code']` from the signup form
2. `aff_code` cookie (set by either `?aff=` URL param on landing or `/api/affiliate/track/{code}` redirect)
3. `Referer` URL param
4. Match by `affiliate_recommend_emails.recipient_email` matching the new user's email (earliest sender wins)

If matched and not the same user, inserts an `affiliate_referrals` row with `link_method='email_click'` (or `'email_match'` if matched by email) and current platform rates.

### Earnings flow
1. Gig booked → tx scheduled
2. Day after gig → `payout_scheduler` charges venue, transfers to artist
3. After successful payment → `accrue_affiliate_earnings(db, txn_id)` is called
4. Looks up `affiliate_referrals` for `txn.gig.venue_id`. If found, computes `earned_cents = txn.amount_cents * current_rate / 100`
5. Current rate = `initial_rate_percent` if days since `linked_at` < `reduced_after_days`, else `reduced_rate_percent`
6. Inserts `affiliate_earnings` row tagged with current quarter (`2026-Q1`), `payout_id = NULL`

### Quarterly payouts
- On Apr 1 / Jul 1 / Oct 1 / Dec 31 the scheduler sends the **admin** a reminder email summarizing eligible payouts (≥`affiliate_min_payout_cents` = $50 default).
- Admin reviews via `/api/admin/affiliate/payout-preview`, then clicks "Run Quarterly Payouts Now" which triggers `run_quarterly_affiliate_payouts(db)`:
  - For each affiliate with unpaid earnings ≥ minimum, create an `affiliate_payouts` row, then `stripe.Transfer.create(...)` to their Stripe Connect account
  - On success, mark earnings `payout_id = <new payout id>` and payout `status = 'paid'`, `paid_at = now`
- Below-minimum balances roll over to next quarter.
- 1099 threshold: $600/year cumulative earnings flags an affiliate for 1099 generation.

### Affiliate Stripe Connect reuse
`POST /api/affiliate/use-artist-stripe` — if the user is already onboarded as an artist, they can reuse that Connect account for affiliate payouts (avoids onboarding twice). Stored in `entity_payment_settings.affiliate_stripe_connect_account_id`.

---

## 14. Admin panel

Path: `/app/admin.html`. Requires `users.is_admin = 'true'`. The first user to ever sign up is auto-made admin.

### Tabs

1. **Platform Settings** (`admin-platform.js`)
   - Stripe credentials (publishable, secret, webhook secret)
   - SMTP config (platform email, password, server, port, from-name)
   - Support email config (separate or same as platform)
   - Admin alert email
   - Platform fee % + split (split/venue_only/artist_only) + minimum fee
   - Payment processing hour
   - Platform timezone
   - Maintenance mode toggle + custom message
   - Signups enabled toggle
   - Payments enabled toggle (master test/live switch)
   - Test SMTP button (`/api/admin/test-smtp` sends a test email)
   - Stats overview: total users, artists, venues, gigs, open tickets, etc. (clickable, deep-link to other tabs)
   - Venue payment overrides: search venues, suspend payments / mark as free trial / clear status

2. **Support** (`admin-init.js` — same file but separate logic block)
   - Inbox of `support_tickets` with status filter (open/closed)
   - Click ticket → see thread → reply (admin reply emails the user, marks ticket back to open)
   - Sortable, paginated

3. **Email Templates** (`admin-templates.js`)
   - List of templates by key
   - TinyMCE rich-text editor for `body`, plain input for `subject`
   - Variable reference per template (which `{{vars}}` are available)
   - "Reset to default" reverts to in-code template

4. **Flyer Templates**
   - Manage admin-global flyer templates that all venues can pick from
   - Set the "site default" template

5. **Affiliates** (`admin-affiliate.js`)
   - Settings: rates, reduced rate, reduced after days, min payout, 1099 threshold, enabled toggle
   - Accounting: per-affiliate earnings, paid/pending/lifetime, click-through to detail
   - Referrals list with manual linking (override automatic)
   - Venue search → manually link a venue to an affiliate (or unlink)
   - Payout preview
   - "Run Quarterly Payouts Now" button

6. **Analytics** (`admin-init.js`)
   - Gig totals, recent activity, top cities, top venues
   - Drill-down detail tables, paginated, exportable

7. **Logs** (`admin-logs.js`)
   - In-memory ring buffer of last 2,000 log lines (from `log_buffer.py`)
   - Filter by level (DEBUG/INFO/WARNING/ERROR/CRITICAL) and substring
   - Clear button

8. **Database** (`admin-db.js`)
   - Browse any table
   - View schema, paginated rows
   - Edit a row, delete a row, insert a new row
   - Export table as CSV
   - **Caution**: this is a real direct DB editor. Be careful.

---

## 15. Security posture

### Defense in depth (current)
- Bcrypt password hashing with salts
- Signed session cookies (HMAC, can't be forged)
- 7-day rolling expiry — active users stay logged in indefinitely without long-lived static tokens
- Account lockout (10 failed attempts → 15 min) + slowapi `5/min` rate limit on `/api/login`
- Rate limits on signup (`3/min`), password reset (`3/min`), support ticket (`2/min`), recommend (`3/min`)
- Anti-enumeration on forgot-password (always returns same success message)
- HMAC-signed tokens for password reset (1h), email verify (72h), support ticket access, review links, waitlist offers
- CSRF: middleware blocks cross-origin POST/PUT/DELETE/PATCH (except Stripe webhook)
- Security headers: HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy
- Content-Security-Policy (currently with `'unsafe-inline'` due to legacy inline handlers — Phase 6 will remove)
- Stripe webhook signature verification (`admin_stripe_webhook_secret`)
- TIN encryption: W9 forms store TINs encrypted at rest (`tin_encrypted` column), only `tin_last4` shown plaintext

### Known gaps / "going live" items to verify
This is what to check before launch (you mentioned a few things to fix — these are likely candidates):

| Concern | Where to check |
|---|---|
| `GIGSFILL_SECRET_KEY` set in production env (hard-fails otherwise — but verify the systemd file actually has it) | `scripts/gigsfill.service` references `EnvironmentFile=/opt/gigsfill/.env`; that file must contain it |
| `GIGSFILL_ENV=production` set so cookies are Secure-only | `.env` file |
| `CORS_ORIGINS` set to the real domain (not `127.0.0.1:8001`) | env var |
| Stripe keys set to **live** keys (not test) and `payments_enabled = '1'` | admin panel |
| Stripe webhook endpoint registered in Stripe dashboard pointing at `/api/stripe/webhook` with the secret matching `admin_stripe_webhook_secret` | Stripe dashboard + admin panel |
| `platform_email_from_name` and SMTP creds set, "Test SMTP" button works | admin panel |
| `support_email` set (so support tickets actually go somewhere) | admin panel |
| `admin_alert_email` set (so payout failures are seen) | admin panel |
| `base_url` / `site_url` setting matches the live domain (used in email links) | admin panel |
| `platform_timezone` correct for the audience | admin panel |
| `signups_enabled` actually enabled (defaults open but worth verifying) | admin panel |
| `maintenance_mode` is OFF | admin panel |
| Stripe Connect Express onboarding URLs in `stripe_connect.py` (lines ~348-349) are **hardcoded to `https://gigsfill.com/...`** — verify this is correct for the live domain or move to a setting | `routes/stripe_connect.py:348` |
| Robots.txt and sitemap.xml have correct domain | `app/static/robots.txt`, `sitemap.xml` |
| The `.secret_key` file pattern is for **dev only** — production should error if `GIGSFILL_SECRET_KEY` not set, which it does — verify the env var is actually loaded |  |
| Two separate `venue.edit.js` and `venue_edit.js` files exist — verify only one is loaded by `venue-edit.html` (see Known issues below) |  |
| CSP `unsafe-inline` is still active — known accepted risk pending Phase 6 |  |
| Redis is running so the rate limiter has persistent storage (otherwise falls back to in-memory and resets per worker) | `systemctl status redis` on the droplet |
| Email verification: confirm the verify-email banner / hard-redirect on `auth.guard.js` is the experience you want; users without verified email are blocked from most pages | `app/static/js/auth.guard.js` VERIFY_EXEMPT list |

---

## 16. Known issues / quirks observed in the code

These are things I noticed while reading. Not bugs you've necessarily filed — just facts about the current state.

1. ~~**Two venue-edit JS files**~~ **FIXED 2026-05-04** — `app/static/js/venue_edit.js` was deleted (older copy, never loaded). Only `app/static/js/venue.edit.js` remains, loaded by `venue-edit.html`.

2. **`is_admin` is a TEXT column with values `'true'` / `'false'`** instead of a boolean. This is fragile — the canonical check pattern is `str(user.is_admin).lower() in ('true', '1')`. **Do not** compare with `==` or use Python truthiness (`not user.is_admin`) — the literal string `'false'` is truthy in Python, so `not 'false'` is `False`, which would PASS an admin gate. One such buggy gate at `routes/emails.py:28` was fixed 2026-05-04. The wider cleanup (migrate column to INTEGER, update ORM to `Column(Boolean)`, replace all string-matching) remains for a future pass.

3. **Two complementary time utilities, not duplicates.** `time-format.js` (22 lines) provides `formatTime12Hour(time)` — converts `"19:00"` → `"7:00 PM"` for gig time display. `timezone-utils.js` (236 lines) provides `formatUTC(timestampStr, mode)` — converts SQLite UTC timestamps to user's local timezone for "created at" / "sent at" / relative times. Both files serve different purposes; not a consolidation target. (Earlier doc note that conflated them was incorrect.)

4. ~~**`states.js` (ES module) and `us-states.js` (global const)** — same data, two access patterns.~~ **FIXED 2026-05-04** — `app/static/js/states.js` deleted (zero imports anywhere). `us-states.js` (loaded by `signup-new.html` and `artist-book-gigs.html` as a global const) is the live version.

5. ~~**`backend/routes/main.py` duplicate registration**~~ **FIXED 2026-05-04** — deleted. It was a broken fragment with no router declaration, never imported by `backend/main.py` (the real FastAPI entrypoint). Note: the `/api/coming-soon-notify` endpoint it tried to define is NOT wired up anywhere — `app/index-comingsoon.html` and `app/index_Placeholder.html` POST to that path but `backend/main.py` doesn't define it. If the coming-soon homepage is ever activated, the endpoint must be added to `backend/main.py`.

6. **Inline JS `unsafe-inline` in CSP** — there's a clear "Phase 6" plan to migrate to `event-delegate.js` so the CSP can be tightened. ~200 inline `onclick=` handlers remain.

7. ~~**Two recommendation systems**~~ **FIXED 2026-05-04** — the header dropdown's "Recommend GigsFill" button now POSTs to `/api/affiliate/recommend` (the same affiliate-aware endpoint used by the user-profile Affiliates tab), so all recommendation paths credit the user as affiliate if their friend signs up. The legacy `/api/recommend` endpoint in `backend/main.py` (with the `recommendations` table) is no longer called by any frontend code but is kept as a no-op safety net for any external integration. Future cleanup can remove it.

8. **SQLAlchemy session vs raw `get_db_connection()`**: most code uses `db: Session = Depends(get_db)` (SQLAlchemy), but some older code (especially in `main.py` and the schedulers) uses `get_db_connection()` which returns a raw connection. Both work on both SQLite and PostgreSQL thanks to the `_PgCompatConn` shim, but it's two paradigms in one codebase.

9. **`backend/main.py` is huge and contains many inline routes** that should arguably live in route modules (artist invitations, support tickets, recommendations, etc.). It's grown organically. Refactoring isn't urgent but it would make the codebase cleaner.

10. **`v73`, `v75`, `v88`, `v91`, `v93`, `v96`, `v97`, `v015 FIX` comments scattered through code** — version markers from past fixes. They don't correspond to git tags; they're informal "fix #97" markers.

11. **`affiliate_recommend_emails.recipient_email` is `COLLATE NOCASE`** in SQLite. PostgreSQL doesn't have a direct equivalent — when migrating to PG, this column should use `CITEXT` or all comparisons should `LOWER()` both sides. Current code does use `LOWER(...) = LOWER(...)` so it should be fine.

12. ~~**`gig_messages` table is created lazily**~~ **FIXED 2026-05-04** — the `sender_entity_id` and `target_artist_id` columns (which scope messages per-artist on multi-slot gigs — historically added lazily by `messages.py:_ensure_gig_messages_table` to fix a multi-artist message-leak bug) are now in `db.py`'s `_add_columns()` migration. Fresh deploys get them in the canonical schema. The lazy creation function is **kept** as a safety net for any DB that pre-dates this migration — it short-circuits on the `_TABLE_CREATED` flag after the first request, so it's effectively free.

13. **`PRAGMA wal_autocheckpoint=500`** is set — SQLite WAL is checkpointed every 500 pages automatically, plus the scheduler runs `wal_checkpoint(TRUNCATE)` hourly.

14. ~~**`/health` endpoint returns static `{"status":"ok"}`**~~ **FIXED 2026-05-07** — `/health` now runs a `SELECT 1` against the DB and verifies `GIGSFILL_SECRET_KEY` is loaded; returns HTTP 503 with a `failed:` array if any check trips. Stripe deliberately not pinged (health → external coupling is its own bug source). Add `/health/deep` if a more thorough check is ever needed.

15. **`payout_scheduler` and `scheduler` use `sqlite3` directly** in some places (`_raw_db_conn()`) while also having `_IS_POSTGRES` awareness via `get_db_connection()`. The mix means PostgreSQL migration has been considered but not fully completed in these files. Verify these paths if migrating to PG.

16. ~~**Latent overlap between `process_open_gig_notifications('open_gig_36h')` and `process_radius_blast`**~~ **FIXED 2026-05-04** — `process_radius_blast` removed from the hourly loop. Function still in file for back-compat with manual callers.

17. ~~**Review-request emails for multi-slot gigs only go to the first artist.**~~ **FIXED 2026-05-04** — `gig_email_log.notification_key` now encodes the artist_id as a suffix (`artist_review_request:42`) for per-artist dedup. Each artist on a multi-slot gig now correctly gets a "rate the venue" email.

18. ~~**`gig_email_log` UNIQUE constraint mismatch with `sent_for_date` column.**~~ **FIXED 2026-05-04** — dedup SELECTs in `process_gig_confirmation` and `process_open_gig_notifications` no longer reference `sent_for_date`. Once an email has been sent for `(gig_id, notification_key)`, it never re-fires, regardless of venue setting changes. The `sent_for_date` column remains in the schema and is still populated on INSERT for historical record, just not used for dedup.

19. ~~**`_run_contract_hold_cleanup` log message says "released 0" when it actually released N.**~~ **FIXED 2026-05-04** — corrected to read `result.get("released_count")`.

20. ~~**Admin email-template edits get overwritten on every API restart.**~~ **FIXED 2026-05-04** — the PUT `/api/email-templates` endpoint in `routes/admin.py` now auto-writes the full template set to `backend/email_templates.py` on disk after every save. So admin edits persist across restarts in a single click. The "Export All" button is kept as a manual fallback. The mechanism: file is the canonical persistence layer, DB is the runtime source of truth, and the two stay in sync via `_populate_email_templates` (file → DB on startup) and the auto-export (DB → file on save).

21. **Single-slot vs multi-slot gigs branch in ~70 places.** Single-slot gigs store the booked artist on `gigs.artist_id`; multi-slot gigs store one artist per row in `gig_slots`. Pay, start/end times, etc. live on the parent `gigs` row for single-slot but can vary per row in `gig_slots` for multi-slot. Result: ~50 UNION queries and many `if is_multi_slot:` branches across schedulers, routes, frontend. The branching is correct (the data is genuinely shaped differently in the two cases), but it's structural complexity that increases bug surface area. **Future refactor (post-launch, ~2-3 day project): always use `gig_slots`** — single-slot gigs would just have one slot row. Migration would create a slot row from each existing single-slot gig's parent fields, and every read path using `gigs.artist_id` directly would update to read from `gig_slots`. Eliminates the UNIONs, the `is_multi_slot` flag, and the branching. Not blocking launch — defer.

22. **~37 frontend `throw new Error('hardcoded string')` sites discard backend error messages.** When a `fetch()` returns 4xx/5xx, code patterns like `if (!res.ok) throw new Error('Failed to send')` discard FastAPI's response-body `detail` field. The user sees "Error sending: Failed to send" instead of "Error sending: <actual reason>". Counted 37 such sites across 18 files via `grep -rE "throw new Error\('[A-Za-z][^']*'\)" app/static/js/`. Top files: `signup-new-init.js` (12), `artist.book-gigs.js` (9), `user-profile-init.js` (5), `venue-email-center.js` (5). **Mitigation already in place**: the new `window.apiGetSafe`/`window.apiPostSafe`/`window.apiPutSafe`/`window.apiDeleteSafe` helpers in `app/static/js/api-globals.js` (loaded on all 10 main pages) read the response body and throw with the real message. New code should use these instead of raw `fetch`. **Future cleanup**: gradually migrate the 37 sites to the helpers — but only when testing reveals a specific case shows a bad message to a user. Don't bulk-sweep; the marginal benefit is small and regression risk is real.

23. **Three cancellation endpoints; easy to fix one and miss others.** See Section 4 / cancellation paths note. `cancel_gig` (DELETE `/api/gigs/{id}/cancel`), `cancel_slot` (POST `/slots/{slot_id}/cancel`), and `delete_gig_with_slots` (DELETE `/with-slots`) all need to behave consistently re: transaction cleanup, flyer cleanup, and `last_cancelled_artist_id`. The venue UI's "Cancel Gig" button uses the third one. When changing cancellation behavior, search for all three. Diagnostic for "did the right cleanup run?" — `journalctl -u gigsfill --since "5 minutes ago" | grep -E "/api/gigs/.+/(cancel|with-slots)"` reveals which endpoint actually fired.

24. **Email Center UI lives in `venue-create-gigs.html`, NOT `venue-email-center.html`.** The standalone `venue-email-center.html` exists but isn't loaded by the live UI — it's been a source of wasted-edit confusion. The actual Email Center is an embedded tab inside `venue-create-gigs.html` (look for `<div id="emailcenter-tab" class="tab-content">` around line 1655). When fixing Email Center bugs, edit `venue-create-gigs.html`. To verify which page the user is actually viewing, look at the DevTools breadcrumb — `div.book-gigs-container` = the venue-create-gigs page, NOT venue-email-center.

25. **Templates can be silently broken by the WYSIWYG editor.** Admin → Email Templates uses a contenteditable WYSIWYG. When admins change formatting (font size, bold) on text that contains `{{variable}}` placeholders, the editor wraps a `<span>` around HALF the placeholder, splitting the `{{` from the `}}`. The substitution function `email_service.render_template` does literal string match for `{{name}}` — won't find `{<span...>{name}}</span>`. Result: the variable name appears verbatim in sent emails. **Mitigation**: when an admin says "the {{variable}} is showing in my email", check the DB body via `sqlite3 /opt/gigsfill/backend.db "SELECT substr(body, instr(body, 'From:'), 200) FROM email_templates WHERE template_key='X';"`. Look for unexpected `<span>` tags inside the placeholder. **Long-term fix**: either swap the WYSIWYG for a source-mode editor, or post-process saved templates to repair split placeholders, or use a smarter substitution that strips inline tags from within placeholder boundaries.

26. **Status of `pending_transfer` was being abused.** Pre-2026-05-07: artist_payout child rows were created with `status='pending_transfer'` at booking time, which collided with the scheduler's "retry stalled transfers" sweep — caused real-money transfers to fire BEFORE venue charges. Now: children are created with `status='scheduled'`, and `pending_transfer` is reserved for "transfer was attempted and is awaiting retry" (e.g., artist not Stripe-onboarded). The retry sweep also has a defense-in-depth guard requiring parent status to be in `('charged','paid','transferred')`. See changelog 2026-05-07 entry. Anyone touching the payout flow needs to preserve these invariants.

27. **Operational lesson: terminal mangles multi-line bash bundles.** Pasting a heredoc-style Python sync command or a sequence of bundled `sudo cp; sudo chown; sudo systemctl restart` would frequently get truncated or merged together by the terminal copy-paste. Best practice: ONE LINE COMMANDS ONLY when prepping for the user, especially `systemctl restart` which goes on its own line. Verify success via `sudo systemctl status SERVICE --no-pager | grep "Active:"` showing fresh "since" timestamp.

28. **Operational lesson: `.bak-*` backup files inconsistent.** Many deploys this session were supposed to create `.bak-<tag>` rollback files via `sudo cp /opt/.../X.py /opt/.../X.py.bak-tag`, but `ls /opt/gigsfill/backend/*.bak* /opt/gigsfill/backend/routes/*.bak* /opt/gigsfill/backend/services/*.bak* 2>/dev/null | wc -l` came up at `1` after ~14 deploys. Some commands silently failed or got mangled. Best mitigation: take a tarball snapshot at the end of each session: `sudo tar czf /opt/gigsfill-snapshot-$(date +%Y%m%d-%H%M).tar.gz /opt/gigsfill/backend /opt/gigsfill/app/static/js /opt/gigsfill/backend.db`. One file = one rollback point.

29. **Operational lesson: file ownership drift on deploy.** `sudo cp /tmp/X /opt/gigsfill/X` creates a root-owned file. The API and scheduler run as `www-data`. Most files are read-only at runtime so this often doesn't show up — but `email_templates.py` is WRITTEN by the auto-export feature, and `recipients_json` in `venue_email_history` requires write access on first ALTER TABLE. Symptom: 500 errors that journal-grep doesn't make obvious because the exception-handler swallows them. **Always run `sudo chown www-data:www-data <file>` after `cp`** in deploy scripts.

---

## 17. Deployment

### DigitalOcean droplet setup
- `scripts/setup_do.sh` — provisioning script for a fresh Ubuntu droplet (Python, Redis, systemd setup)
- `scripts/gigsfill.service` — systemd unit for the **API only** (User=www-data, 2 uvicorn workers on port 8001, EnvironmentFile=/opt/gigsfill/.env, Restart=always). Does NOT set `GIGSFILL_RUN_SCHEDULERS`, so schedulers stay inert here.
- `scripts/gigsfill-scheduler.service` — systemd unit for the **scheduler service** (single process running `python -m backend.scheduler_main`). Sets `Environment=GIGSFILL_RUN_SCHEDULERS=1`. Both schedulers (payout + email) run in this single process.
- `scripts/env_template.txt` — template for `.env` (copy to `/opt/gigsfill/.env`)
- `scripts/fix_1gb_droplet.sh` — script to add swap + tune for 1GB RAM
- Reverse proxy (nginx) is presumed in front of port 8001, terminating TLS

**Both services** read the same `/opt/gigsfill/.env` file. Manage them independently:
```
sudo systemctl status  gigsfill            # API
sudo systemctl status  gigsfill-scheduler  # Schedulers
sudo systemctl restart gigsfill            # Restart only the API (no scheduler downtime)
sudo systemctl restart gigsfill-scheduler  # Restart only the schedulers (no API downtime)
sudo journalctl -u gigsfill           -f   # API logs
sudo journalctl -u gigsfill-scheduler -f   # Scheduler logs
```

If `gigsfill-scheduler` is down, no automated emails or payouts go out — the API still works fine, but blast emails won't fire and the day-after-gig charge won't happen. Set up monitoring on this service.

**Critical: drop-ins must be mirrored to BOTH services.** systemd drop-ins live in `/etc/systemd/system/<unit-name>.service.d/*.conf` and let you add env vars without modifying the canonical unit file. The current production setup has:
- `/etc/systemd/system/gigsfill.service.d/secret.conf` — `GIGSFILL_SECRET_KEY=...`
- `/etc/systemd/system/gigsfill.service.d/override.conf` — `SESSION_SECRET_KEY=...`

The scheduler service needs the SAME env vars (it imports from `routes/auth.py` which signs tokens), so the same files MUST be mirrored at:
- `/etc/systemd/system/gigsfill-scheduler.service.d/secret.conf`
- `/etc/systemd/system/gigsfill-scheduler.service.d/override.conf`

If you ever run `sudo systemctl edit gigsfill` to add a new env var, **also run `sudo systemctl edit gigsfill-scheduler`** with the same content (or copy the resulting drop-in file). Otherwise the scheduler will fail or behave incorrectly. Symptom of missing drop-in: scheduler logs show `⛔ GIGSFILL_SECRET_KEY is not set!` and contract-hold cleanup fails.

### Required env vars (`/opt/gigsfill/.env`)
```
GIGSFILL_ENV=production
GIGSFILL_SECRET_KEY=<64-char hex>
RATELIMIT_STORAGE_URI=redis://localhost:6379
CORS_ORIGINS=https://gigsfill.com
DATABASE_URL=                          # blank = SQLite; set to postgresql:// for PG
SESSION_MAX_AGE=604800                 # optional, defaults to 7 days
GIGSFILL_BASE_URL=https://gigsfill.com # optional, also stored in platform_settings.base_url
```

`GIGSFILL_RUN_SCHEDULERS` is NOT set in `.env` — it's set only inside `gigsfill-scheduler.service` via `Environment=GIGSFILL_RUN_SCHEDULERS=1`. Don't add it to `.env` because then the API service would also start the schedulers and the duplicate-email problem would come back.

### Database
- SQLite default: `backend.db` next to the `backend/` package (e.g. `/opt/gigsfill/backend.db`)
- WAL mode enabled (busy_timeout=10000ms, foreign_keys=ON, synchronous=NORMAL)
- Migration: `scripts/migrate_sqlite_to_postgres.py` — moves data from SQLite to PostgreSQL when ready
- Reset: `scripts/reset_gigs_db.py` — wipes and re-creates (DANGEROUS in prod)

### Static files
- Mounted at `/app` from the `app/` directory: `app.mount("/app", StaticFiles(directory="app", html=True))`
- User uploads go to `app/static/uploads/{artist|venue|contracts|flyers}/...`
- Server cache headers: 7 days for images, no-cache for HTML, must-revalidate for JS/CSS

### Logs
- All Python logging goes to stdout via `logging.basicConfig(StreamHandler)` → captured by systemd → journalctl
- Plus the in-memory ring buffer (`log_buffer.py`) viewable in admin Logs tab
- `_ErrorEmailHandler` emails the admin on any ERROR/CRITICAL log line (5-min throttled)

---

## 18. Tests

There are tests but coverage is partial:
- `tests/conftest.py` — pytest fixtures
- `tests/test_data_integrity.py` — schema/data invariants
- `tests/test_services.py` — service-layer unit tests (notification_service, email_dispatch, gig_cleanup)
- `test_cancel_flow.py` (root) — end-to-end cancellation test, runs against a live local server

No test currently covers:
- Booking pipeline pre-flight checks (the long sequence in `book_gig`)
- Payout scheduler charge → transfer flow
- Affiliate accrual + quarterly payout
- Frontend interaction (no JS tests at all)

If adding tests for the "going live" hardening, the highest-value targets are:
1. The 5-step pre-booking pipeline in `_run_prebooking_checks`
2. `_create_booking_transaction` fee math (split, venue_only, artist_only, with min_fee)
3. `accrue_affiliate_earnings` rate calculation (initial vs reduced)
4. The waitlist sequential-offer state machine (offer → expire → advance)

---

## 19. How to ask Claude for changes (re-priming a fresh chat)

When you start a new chat, paste this whole document. Then describe what you want. Recommended phrasing:

> Here is the GigsFill reference doc. I need to <do thing>. Before writing any code, summarize back to me which files you'll touch and what the change entails.

This forces Claude to confirm understanding before generating code. Useful especially for cross-cutting changes (anything that touches both frontend and backend, or anything in the booking/payment flow).

For very localized changes ("change the wording on this email template"), you can skip the doc and just paste the relevant template.

For changes to anything in: **booking pipeline, payout scheduler, transactions table, contract flow, affiliate accrual** — always paste this doc. Those areas have many invariants that aren't visible from a small slice of code.

### Files most likely to need re-reading for any change
- For UI tweaks: the relevant page's HTML + its `*-init.js` + the shared module (`gig-modal.js`, `activity-center.js`, etc.)
- For a backend route change: the route file + `services/notification_service.py` + `services/email_dispatch.py` (since most changes have email/notification side effects)
- For a schema change: `db.py` (schema) + `models.py` (ORM) + run a search to find all SQL that touches the column
- For a new email: `email_templates.py` (define) + the dispatch site (where to call `send_notification_email`) + `email_preferences` defaulting logic in `email_service.py`

---

## 20. Quick reference: where to find common things

| Task | File(s) |
|---|---|
| Change platform fee % | Admin → Platform Settings (`platform_fee_percent`). Code: `routes/stripe_connect.py:get_stripe_keys`, `routes/gigs.py:_create_booking_transaction` |
| Change fee split mode | Admin → Platform Settings (`platform_fee_split`). Same code paths |
| Add a new email template | Define in `email_templates.py` (key + subject + body), restart for DB sync, then call `email_service.send_notification_email(user_email, user_id, template_key, vars)` from the right dispatch site |
| Add a new notification type | Append to `notification_service.create_notification` callers + ensure the type appears in user prefs UI + add a default in the `email_preferences` defaults if it's a blast type |
| Add a new admin setting | Add a row to `default_settings` in `db.py:setup_database()`, add UI to admin Platform Settings tab (`admin-platform.js`), read via `db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key=:k"), {"k":"..."}).scalar()` |
| Modify the booking pre-flight checks | `routes/gigs.py:_run_prebooking_checks` (or the inline block in `book_gig`) — keep the order: ban → preferred/blast → W9 → frequency → waitlist |
| Modify the payout flow | `payout_scheduler.py:process_payouts_now` |
| Modify what happens on slot cancel | `routes/gigs.py:cancel_slot` + `services/gig_cleanup.py:cleanup_gig_records` |
| Modify the gig modal | `app/static/js/gig-modal.js` (UI) + `routes/gig_modal.py` (data) |
| Modify the calendar | `app/static/js/venue.create-gigs.js` (venue side) and `app/static/js/artist.book-gigs.js` (artist side) — they have separate calendar implementations |
| Add a new tab to artist-book-gigs | Edit `app/artist-book-gigs.html` (add `<button>` and `<div class="tab-content">`), wire in `artist-book-gigs-init.js:switchTab` |
| Change auth behavior | `routes/auth.py` + `app/static/js/auth.guard.js` (frontend gate) |
| Change theme colors | `app/static/css/gigsfill.css` `:root` |

---

*End of GigsFill reference doc.*
