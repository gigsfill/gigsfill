# GigsFill — Complete System Reference

**Purpose of this document.** This is a self-contained reference for the GigsFill codebase. If you're starting a new chat with Claude, paste this whole file in your first message and Claude will have a working understanding of the entire system without re-reading the code.

**Last updated:** May 25, 2026.

## Changelog

The list below tracks meaningful changes after the initial sync from the codebase. Each entry covers what changed in the code AND the doc sections updated to reflect it. Whenever code changes, update the relevant doc sections AND add an entry here.

- **2026-06-17 (deep audit fixes) — File ownership + 3 backend bugs + 2 UX gaps + test conftest catch-up:**
  1. **chown www-data:www-data on `backend/email_templates.py`, `db.py`, `email_service.py`.** Per CLAUDE.md, admin email-template edits write to DB *and* tries to write the file from the PUT endpoint at [admin.py:915](backend/routes/admin.py#L915). When the file was root-owned, the file write silently failed and changes were lost at the next deploy when `_populate_email_templates()` reloaded from disk. Verified: file now owned by www-data, edit + restart round-trip works.
  2. **`sign_contract()` type-check bug** ([contracts.py:935](backend/routes/contracts.py#L935)). The guard compared `contract_type` to the literal string `"digital"` — which is not a value the schema ever produces (`CHECK` constraint at [contracts.py:471](backend/routes/contracts.py#L471) restricts it to `pdf_upload | custom_builder | auto_generated`). Net effect: the gate fired for *every* typed contract and silently blocked `custom_builder` + `auto_generated` from ever being signed digitally via this endpoint. Inverted the check to block ONLY `pdf_upload`. The vulnerability path is now closed AND the legitimate digital-sign paths work.
  3. **`charge_booking()` INSERT missing `artist_id` + `transaction_type`** ([stripe_connect.py:628](backend/routes/stripe_connect.py#L628)). Transactions written by this endpoint omitted both fields, leaving `_transfer_to_artists()` (payout_scheduler) to fall back to legacy artist-routing which could pay the wrong artist on multi-user accounts. Both columns now bound explicitly (`'single'` for `transaction_type`).
  4. **Auth guard no longer shows a 12s blank page on slow networks** ([auth.guard.js](app/static/js/auth.guard.js)). The pre-paint script sets `visibility:hidden` on `<html>` to prevent content flash; if `/api/me` hangs, the existing 12s timeout would silently leave that hidden until the redirect fired. Added a small "Checking session…" overlay (purple spinner + label) that appears after 600ms if auth hasn't resolved, with `visibility:visible !important` to break out of the inherited hidden state. Removed on success/fail/timeout.
  5. **High-traffic raw `fetch()` error swallows migrated to `apiPostSafe`** in [messages.js](app/static/js/messages.js) (load + send) and [user-dropdown.js](app/static/js/user-dropdown.js) (Help + Feedback ticket submissions). Users now see the real backend `detail` ("Venue access revoked", "Frequency limit reached", etc.) in inline status text or a toast — instead of `"Failed to send"`. Total raw-fetch error swallows site-wide remain at ~118, migration can continue incrementally as bugs surface.
  6. **`_findNextBlast()` filter inversion** ([venue.create-gigs.js](app/static/js/venue.create-gigs.js)). The banner on a gig that had already fired a 2-week blast always said "No further automated blast emails are scheduled" even when 1-week / 36-hour notices were enabled. The filter required `t.wh > _firedH` ("only blasts with LARGER threshold than what already fired") — but blasts fire in *decreasing* threshold order (4w → 2w → 1w → 36h), so larger means already-fired. Flipped to `t.wh < _firedH`. Banner now correctly says e.g. "Next scheduled blast: 1 week notice — will fire automatically if this gig is still open." Also widened the `_lbl()` formatter to render `36 hours` instead of `36h`.
  7. **Test conftest catch-up** ([tests/conftest.py](tests/conftest.py)). 17 tests were failing because the in-memory SQLite schema had drifted from production. Added missing columns (`users.phone`, `users.sms_carrier`, `transactions.parent_transaction_id`, `transactions.transaction_type`, `gig_contracts.signed_pdf_path` + 8 others), and added six missing satellite tables touched by `gig_cleanup` (`gig_messages`, `gig_email_log`, `public_activity`, `gig_waitlist`, `waitlist_offered`, `artist_reviews`). Full test suite: **37/37 passing** (was 20/37).
  8. **Audit finding `_cleanup_expired_holds_impl` "orphaned payment transaction" closed as NOT-A-BUG** — the May 2026 part 5 audit fix intentionally defers `_create_booking_transaction()` to `countersign_contract()` so that if the venue ghosts within 48h, no charge is ever taken. Confirmed by reading both [contracts.py:1011-1017](backend/routes/contracts.py#L1011) and [contracts.py:1667](backend/routes/contracts.py#L1667). The flow is consistent: artist-sign-only → no transaction, both-sign → transaction; expiry sweep has nothing to clean up because there's nothing to clean up.

- **2026-06-16 (social reorder save bug + button-width parity) — Two small follow-ups:**
  1. **Social reorder wasn't persisting** on artist edit. The dragend handler PUT to `/api/artists/{id}` — but the artist update endpoint is `/artists/{id}` (no `/api` prefix). The `/api/artists/{id}` path is a GET-only public route, so the PUT was silently 405'd and the save dropped on the floor. Verified: old URL → 405, new URL `/artists/{id}` → 401 unauth (endpoint exists). Switched the fetch to `/artists/${artistId}` to match the `bindAutosave` pattern already used elsewhere in the same file. Venue side was already correct (`/api/venues/{id}` is a real PUT route there).
  2. **"+ Add MP3 File" and "+ Add Audio Link" buttons now have matching widths** (170px min-width on both, centered text). Without the min-width, the MP3 button's text changed length as the count badge ("(2/3)") updated, and the two buttons sat at different widths. 170px fits the longest state ("+ Add MP3 File (2/3)") with comfortable padding and matches "+ Add Audio Link" exactly. Cache-buster: `artist.edit.js?v=15`.

- **2026-06-16 (Reviews badge + MP3 layout + sortable Social Media) — Three polish items:**
  1. **Reviews tab on artist public profile now shows count badge** ("Reviews (1)") like the venue tab does. Added `<span id="reviewsBadge">` to the tab button, fetched the summary once on page load to populate it eagerly, and updated `renderRatingSummary` in [artist-reviews.js](app/static/js/artist-reviews.js) to also set the badge when the user clicks the tab (covers both paths).
  2. **Audio section layout** on artist-edit: the "+ Add MP3 File" button now sits on the same line as the help text, right-aligned, so it vertically lines up with the "+ Add Audio Link" button on the row below. Wrapped both in a flexbox; help text gets `flex:1`, button gets `flex-shrink:0`.
  3. **Social media is now drag-sortable** on both artist and venue edit pages — and the chosen order applies to the public profile. New `social_order TEXT` column on `artists` and `venues` (comma-separated brand keys), migrated live + added to [db.py](backend/db.py) `_add_columns` for fresh installs + ORM models. Backend GET endpoints (`/api/artists/{id}`, `/api/artists/{id}/public`, `/api/venues/{id}/public`, plus the auth `/api/{id}` for artists) return it; PUT endpoints accept it via COALESCE so it can be updated independently of the URL fields. Edit pages: each `.social-row` got a `<span class="drag-handle">☰</span>` and `data-brand="..."`; new `setupSocialReorder(artistId)` / `setupVenueSocialReorder(venueId)` helpers wire drag-to-reorder on `#socialGrid`; on `dragend` the brand keys in current DOM order are joined and PUT to the artist/venue endpoint. On load, the saved order rearranges DOM rows before drag wiring fires. Public profiles ([artist-profile.html](app/artist-profile.html), [venue-profile.html](app/venue-profile.html)) read `social_order` and sort the `links[]` array before rendering; brands not in the saved order keep their natural position relative to each other at the end (so a user adding a new social field later just appends).
  - Cache-busters bumped: `artist.edit.js?v=14`, `venue.edit.js?v=6`. API restarted to pick up the new GET response field + PUT param.
  - End-to-end verified: setting `social_order='instagram,spotify,facebook,website'` on Fifty Proof persists, returns via `/api/artists/3`, surfaces on the rendered HTML via `/fiftyproof`. Same on `/api/venues/1/public` and `/14cannons`.

- **2026-06-16 (social tiles) — Branded social-media grid replaces emoji link list.** The social media tab on both public profiles was a left-aligned vertical list of pill buttons with emoji icons (🎵 Spotify, 📷 Instagram, etc.). Replaced with a **centered grid of branded tiles**, mirroring the new video/picture grid pattern:
  - Each tile is a 110px square card with the brand's real **inline SVG logo** above the brand name. Single-color paths so the icon tints to the brand's iconic color (Spotify green, Instagram pink, Facebook blue, YouTube red, X mono, TikTok cyan, Yelp red, Google Maps red, Website cyan).
  - Hover lifts the tile, scales the icon by 1.1×, and shifts the border to the brand color via `--brand` CSS variable set inline per-tile.
  - Grid uses `repeat(auto-fit, 110px) + justify-content: center` so a row with 3 of 6 visible (the common case for venues — many don't fill all 6 social fields) renders centered, not left-aligned.
  - Mobile breakpoint (<480px): tiles shrink to 90px, icons 28px.
  - **No external dependencies** — all 9 SVG paths are embedded inline (~2.5kb total). No Font Awesome, no CDN, no extra HTTP request. Brand keys: spotify, instagram, facebook, youtube, twitter, tiktok, website, yelp, google_maps. Helper `brandIcon(name)` + map `BRAND_COLORS{name: hex}` defined at the top of each page's script block.
  - Same component on both [artist-profile.html](app/artist-profile.html) and [venue-profile.html](app/venue-profile.html). Verified each SVG parses (viewBox + path with d-length 100+ chars).

- **2026-06-16 (pictures + centered grids + centered tabs) — Symmetric polish on both public profiles.** Three small changes that make the whole page look balanced:
  1. **Centered video + picture grids.** Switched from `grid-template-columns: repeat(4, 1fr)` (which always left-aligned partial rows) to `repeat(auto-fit, 210px)` + `justify-content: center`. Now a row with 3 of 4 columns full renders centered, and a row with just 1 card sits in the middle of the container. Cleanest grid-native approach — no flex/wrap tricks needed.
  2. **Centered header tabs.** Added `justify-content: center` to `.profile-tabs` on both [artist-profile.html](app/artist-profile.html) and [venue-profile.html](app/venue-profile.html). Tabs center when they all fit; horizontal scroll still kicks in on narrow viewports where content overflows.
  3. **Pictures get the video treatment.** Same 4-per-row 210px cards, same 130px-tall thumbnails, same big title + multi-line caption row. Merged `#videos.media-grid` and `#pictures.media-grid` into one shared rule. Picture caption field added to both edit pages: `<textarea class="picture-caption">` inside the existing card overlay, autosaves on blur, Enter commits (no newline). New `.picture-caption` class added to the shared keydown/blur handler matchers in [artist.edit.js](app/static/js/artist.edit.js) and [venue.edit.js](app/static/js/venue.edit.js). Public profiles render the caption only when non-empty — plain pictures look identical to before this change.
  - End-to-end verified: picture caption persists to DB, returns via public GET, surfaces on the rendered card (sample: "Backstage at the Echo · Sept 9 2026" round-tripped through `gigsfill.com/fridayspast`).
  - Cache-busters: `artist.edit.js?v=13`, `venue.edit.js?v=5`. No backend restart needed.

- **2026-06-16 (video grid bigger) — Responsive 4-column video grid with room for longer captions.** The pictures grid stays as-is (quantity-first gallery); videos get bigger cards because captions were truncating at 2 lines in the narrow 120-px-wide cells. Added `#videos.media-grid` override on both [artist-profile.html](app/artist-profile.html) and [venue-profile.html](app/venue-profile.html):
  - 4 cols on desktop (>1000px) → 3 cols (700-1000px) → 2 cols (480-700px) → 1 full-width col (<480px, with a taller 180px thumbnail at that size since it's the only one on screen).
  - Thumbnails: 130px tall by default (consistent height for the row).
  - Title: bumped to 0.78rem, **bold**, allowed to wrap to 2 lines, left-aligned padding kept centered text.
  - Caption: bumped to 0.72rem, left-aligned, allowed up to **5 lines** before ellipsis. Background made transparent so caption visually belongs to the card body rather than an isolated dark strip.
  - Selector specificity (`#videos.media-grid` is id+class) cleanly overrides the existing `.media-grid` mobile rule without modifying that rule, so the pictures gallery layout is unchanged.

- **2026-06-16 (video captions) — Per-video caption on artist + venue profiles.** Same `caption` column we added for audio (already on `artist_media` and `venue_media`); just wired up for `media_type='video'` rows on all four media-handling pages. No new schema, no new endpoints — the `/api/media/{id}` (artist) and `/api/venues/media/{id}` (venue) PUTs already accept any column via `hasattr(m, k)`, and the GET responses already include `caption`.
  - **Edit pages** — caption sits inside the existing `.media-overlay` block below the title input. Full-width `<textarea class="video-caption">` with placeholder "Add a caption (e.g. Live at the Roxy · Aug 14)…", max 500 chars, Enter commits (blurs → save) so it behaves like a one-line label. Same shared handler also covers `.audio-caption` and `.media-title`. Added `escapeHtml` helper to venue.edit.js (artist.edit.js already had one) so a user-typed `</textarea>` can't break out.
  - **Public profiles** — caption rendered as `<div class="media-caption-label">` *below* the existing title label, only when non-empty. Plain videos look identical to before this change (no blank row). 2-line max via `-webkit-line-clamp` so a long caption doesn't blow up the grid cell — full text shows in the modal player's title bar if needed.
  - **Verified end-to-end**: set caption on a Fridays Past video → `/api/artists/1/media` returns it → vanity URL `/fridayspast` serves HTML containing the new render code → empty caption returns null and frontend correctly skips the row.
  - Cache-busters: `artist.edit.js?v=12`, `venue.edit.js?v=4`. No backend restart needed (no Python changes).

- **2026-06-16 (vanity HTML cache fix + MP3-only) — Root cause of "edits don't show up on gigsfill.com/bandname".** Two related fixes:
  1. **The vanity URL resolver was caching `artist-profile.html` / `venue-profile.html` in-memory forever.** `_load_profile_html()` in [vanity.py](backend/routes/vanity.py) used a simple `{name: contents}` dict that only invalidated on API restart. Effect: any edit to those HTML files wasn't visible at `gigsfill.com/<slug>` until `sudo systemctl restart gigsfill`. This presented to the user as "the audio caption isn't showing on the public profile" and "I reordered tracks but the public page still shows the old order" — both looked like browser-side caching but were actually server-side. **Fix:** swapped to `{name: (mtime_ns, contents)}` with an `os.stat()` mtime check on each request. Cache hit when file unchanged; cache miss + re-read when file mtime advances. One extra `stat()` per request, no real perf cost.
  2. **Audio uploads are now MP3-only** (was: mp3/wav/m4a/ogg/flac/aac). Tightened `ALLOWED_EXTENSIONS['audio']` and `ALLOWED_MIME_TYPES` in [routes/media.py](backend/routes/media.py), tightened the file picker's `accept="audio/mpeg,.mp3"` on artist-edit.html, and added a client-side extension check that fires **before** the size check so a `.wav` upload gets a clear "MP3 only" message immediately. Backend still enforces the format check defensively. Users with non-MP3 files can convert or use the existing "+ Add Audio Link" field for external hosting.
  - Service worker `CACHE_NAME` bumped `gigsfill-v6 → gigsfill-v7` to force eviction of any client-side cached HTML that pre-dates the audio-entry rewrite.
  - Verified end-to-end: `gigsfill.com/fridayspast` now serves HTML containing `audio-caption-display` + `audio-entry` (9 references; previously 0); reorder roundtrip persists to DB and GET returns rows in the new order. API restarted to load the fixed vanity cache.

- **2026-06-16 (audio caption polish) — Enter commits + public profile shows caption.** Three small follow-ups to the audio-caption feature:
  - **Enter in the caption now commits** (blurs → save). The previous build used Ctrl/Cmd+Enter because the field is a textarea (Enter would naturally insert a newline). User feedback: captions are short labels (e.g. "Live at the Roxy · Aug 14, 2026"), not paragraphs, so single-line behavior is the right default. `preventDefault` stops the newline.
  - **Blur autosave was already working** — verified end-to-end against a real row the user had captioned during testing ("This is Q1 song...Type anything here." round-tripped via the public GET).
  - **Caption now displays on artist public profile** ([artist-profile.html](app/artist-profile.html)) — wrapped each audio row in a `.audio-entry` div with a `.audio-caption-display` above the player, rendered **only when caption is non-empty**. Empty captions leave the layout looking identical to the pre-feature version (no blank row). Both `audio` and `audio_link` media types covered.
  - Cache-buster: `artist.edit.js?v=11`.

- **2026-06-16 — Audio uploads on artist-edit: 5 MB cap + per-track caption.** Two related changes to the Audio section:
  - **5 MB size cap** (was 50 MB). Lowered `MAX_FILE_SIZES['audio']` in [routes/media.py](backend/routes/media.py); enough for a ~5-minute 128 kbps demo clip. UI text now reads "Upload up to **3 MP3 files** (max **5 MB each**)". Client-side check in [artist.edit.js](app/static/js/artist.edit.js) catches oversize files before upload and shows: "That file is X MB — the limit is 5 MB. Try re-encoding at a lower bitrate (128 kbps is plenty for a demo clip), trimming the track, or linking to it on SoundCloud/Bandcamp instead." Backend still enforces in case the check is bypassed.
  - **Per-track caption textarea above each audio row.** New `caption TEXT` column on both `artist_media` and `venue_media` (migrated live + added to `db.py` CREATE TABLE + ORM models so the existing `/api/media/{id}` PUT accepts it via `hasattr(m, k)`). GET responses now include `caption`. The audio render in `artist.edit.js` wraps each row in a new `.audio-entry` card containing the full-width textarea on top + the existing (drag-handle, title, audio player, Delete) row below. Saves on blur (mirror of the title-save handler); Ctrl/Cmd+Enter commits without inserting a newline. Drag-reorder + delete updated to target `.audio-entry` so the caption moves/deletes with its row.
  - Cache-buster: `artist.edit.js?v=10`. API restarted to pick up the new MAX size + GET-response field.

- **2026-06-16 — artist-profile.html tab underline bug: Pictures and Videos were swapped.** `switchTab(name)` highlights the active button via `btns[TAB_NAMES.indexOf(name)]`, but `TAB_NAMES` had `videos` BEFORE `pictures` while the actual button DOM had Pictures before Videos. Result: clicking Pictures correctly opened the Pictures panel but the underline appeared under the Videos button (and vice versa). Reads as "contents not always correct" because users use the underline to tell which tab is active. One-line fix: reordered `TAB_NAMES` to match button DOM order (`info, calendar, pictures, videos, audio, social, reviews`). Verified all 7 tabs now light up the matching button. Added a comment warning future edits to keep the two in sync. Venue-profile.html uses the same pattern but its array+DOM already match, so no bug there.

- **2026-06-14 (part 10p audit) — Multi-agent audit of today's invitation feature surfaced 10 real bugs (out of ~30 reported); all fixed.** Spawned 3 parallel Explore agents over backend Phase 1, backend Phase 2/3, frontend, and cross-cutting code. Triaged false positives directly (no UNIQUE constraint needed on token: 256-bit entropy ⇒ collision-free; LIMIT 1 on artist lookup: data model implies one artist per user; `_pendingInviteHookFired` doesn't persist across navigation; etc.). The real findings, and what shipped:
  1. **`db.py` was missing the `artist_invitations` indexes on token / invite_group_id / LOWER(invited_email)** — the migration created them on the live DB via ALTER, but a fresh install from `setup_database()` would have skipped them. Added `CREATE INDEX IF NOT EXISTS` statements after the `CREATE TABLE` block. By-token lookups now scale.
  2. **The decline endpoint was a state-mutating GET** — email-link prefetchers (Outlook safe-link checking, antivirus URL scanners, link previewers) trigger automatic GET requests on every URL in an inbox, so invitations could be marked `declined` without the invitee ever clicking. Split into two endpoints: `GET /api/invitations/{token}/decline` now returns a confirmation page with a button that fires `POST /api/invitations/{token}/decline`. The POST does the actual state change. Prefetchers can never auto-decline.
  3. **The decline state machine guard was missing `bounced` and `expired`** — a row could theoretically be flipped from `bounced` → `declined`. Added both to the `NOT IN (…)` clause; verified with a unit test that all six terminal states (`signed_up`, `preferred_requested`, `preferred_approved`, `preferred_denied`, `declined`, `bounced`, `expired`) are correctly preserved.
  4. **DSN fallback regex could mark our own platform_email as bounced** — the body-text fallback pattern in [`scheduler.py:_parse_dsn_failed_recipients`](backend/scheduler.py) was greedy. If a DSN quoted back our own From header (which is common) the regex matched the OUR address near the "550" code. Added an exclusion set built from the DSN's `From` / `Reply-To` / `Return-Path` / `Sender` / `Errors-To` headers plus daemon-address prefixes (`mailer-daemon@`, `postmaster@`, `noreply@`, `no-reply@`). Verified end-to-end: a synthetic bounce that names `noreply@gigsfill.com` in the body produces zero matches.
  5. **Bounce check was overwriting `signed_up` rows with `bounced`** — the SQL filter `status IN ('pending', 'signed_up')` would clobber a successful signup if a late DSN arrived for the original invite email weeks later. Tightened to `status = 'pending'` only — once the invitee has moved past pending, the original invite's deliverability is irrelevant.
  6. **Resend cooldown error was hidden in `title` attribute** — when [`POST /api/artist-invitations/{token}/resend`](backend/routes/me.py) returned a 429 with "Please wait about 21h 4m before resending…", the My Invites page only set `btn.title = e.message` (browser tooltip on hover). Users wouldn't see the cooldown duration without hovering. Now surfaces an inline `⚠ …` red box below the card; 429 specifically resets the button to its default state so the user can retry later.
  7. **Invite POST committed only at the end of the batch** — if a DB exception fired mid-iteration (after emails had already been sent), all prior rows would roll back even though the recipients had received the email. Now commits per email (with rollback-on-failure logging). Partial success is preserved.
  8. **Bounce-Detection "Test & Poll Now" button raced with debounced autosave** — admin could toggle the enable checkbox and click Test immediately; the server would see the not-yet-saved state and return `reason: 'disabled'`. Fixed by awaiting `window.autoSaveSettings()` before the POST.
  9. **Email count >50 turned RED** in the invite modal — but the user explicitly removed the 50-email cap, so red implied a limit that doesn't exist. Switched to amber (`#f59e0b`) so the color reads as "double-check" instead of "blocked".
  10. **`_save_bounce_check_result` UPDATE-then-INSERT race** — concurrent run-now calls could both reach INSERT and the loser would crash on the `setting_key` UNIQUE constraint. Wrapped INSERT in try/except → UPDATE fallback, mirroring the pattern used in `admin.py:update_settings`.
  - **False positives I disproved before acting**: token UNIQUE constraint (256 bits is collision-free), the popup popup's window-flag persistence across navigation (window globals reset per page in vanilla nav), `_invToggleAllVenues` defined after modal HTML (functions only need to exist before user clicks, not when HTML is injected), get_my_invitations leaking the `message` field (the inviter is the only caller — and it's their own message), bounce-check clock starting at 0 (intentional fast-start so admins see results immediately).
  - **Restart + smoke**: API + scheduler both active, all 11 endpoints respond correctly (unauth → 401/200, legacy → 410, decline GET → 200 confirmation page, decline POST → 200 valid:false on bogus tokens). Cache-busters bumped: `user-dropdown.js?v=6`, `my-invites.js?v=2`, `admin-platform.js?v=5`.

- **2026-06-14 (part 10p Phase 3) — Closed both Phase 1 open items: async bounce detection + legacy invite endpoint removed.**
  - **Async bounce detection via IMAP DSN polling.** Synchronous SMTP refusals are already caught at send time; many bounces are asynchronous — the destination MTA accepts the message and then returns a Delivery Status Notification later. New `process_bounce_inbox()` in [scheduler.py](backend/scheduler.py) connects to the platform email's IMAP inbox every 30 minutes, walks UNSEEN messages, filters DSNs by From (MAILER-DAEMON / postmaster / Mail Delivery System) and Subject (undeliverable / failure notice / etc.) and Content-Type (`multipart/report; report-type=delivery-status`), then parses the failed recipient(s) from the standard RFC 3464 `message/delivery-status` part. Matched rows in `artist_invitations` (LOWER(invited_email) within last 30 days, status in `pending`/`signed_up`) get flipped to `bounced` with the SMTP diagnostic preserved in `bounce_reason`. Processed messages are marked Seen so they aren't re-read; non-DSN unread messages are left untouched. Tolerates malformed DSNs via a regex fallback over the body text.
    - **Off by default.** Settings: `bounce_check_enabled`, `bounce_check_imap_server`, `bounce_check_imap_port` (default 993), `bounce_check_imap_username` (defaults to platform_email), `bounce_check_imap_password` (defaults to platform_email_password, masked in API). Plus `bounce_check_last_run_at` + `bounce_check_last_result` that the scheduler writes for the admin UI to surface as "Last poll: …".
    - **New endpoint** `POST /api/admin/bounce-check/run-now` — admin-only, synchronous trigger that calls the same code path. Returns `{scanned, bounced, skipped, errors, reason?}` so the "Test & Poll Now" button gives immediate feedback.
    - **Admin UI** in [admin.html](app/admin.html) Email Settings → "Bounce Detection (async)": enable checkbox, IMAP server/port/username/password inputs in a 3-column grid (matching the Rate Limits layout), "Test & Poll Now" button, last-run status line. Hover tooltips explain each field. [admin-platform.js](app/static/js/admin-platform.js) v=4 loads + autosaves + runs the Test handler.
    - **Cadence**: a new `last_bounce_run` clock in `_scheduler_loop()` fires every 1800s. The disabled check is inside the function so the cost when disabled is one settings read + early return.
    - **Tested**: DSN parser correctly extracts single and multi-recipient RFC 3464 messages; disabled path returns `{reason: 'disabled'}` (no IMAP connect); enabled-but-unconfigured path returns a clear `'missing IMAP server/username/password — configure in Admin → Email Settings'` reason instead of stack-tracing.
  - **Legacy `POST /api/venues/{vid}/invite-artists` removed** ([main.py](backend/main.py)). The endpoint now returns **410 Gone** with a message pointing to the canonical `POST /api/me/invite-artists`. The original ~170-line body was deleted (last good version: pre-part-10p git history). The companion GET `/api/venues/{vid}/invitations`, POST `/api/venues/{vid}/resend-invitation/{id}`, and DELETE `/api/venues/{vid}/invitations/{id}` endpoints remain — they back the per-venue tracker UI in Email Center which is still useful for venue-scoped views. (Also fixed: `HTTPException` was being imported inside the legacy function body, so removing it caused a NameError at request time on the new stub — added `HTTPException` to the module-level `from fastapi import …` import.)
  - **What's working end-to-end after both items**: send invitations from User dropdown → multi-venue email goes out → if recipient's mailbox is dead, the platform email gets a DSN ~minutes later → next scheduler tick (≤30 min) parses it → matching `artist_invitations` rows flip to `bounced` with the SMTP reason → both the global My Invites page and the per-venue tracker render the bounce reason inline. No more silent "stuck pending forever" rows.

- **2026-06-14 (part 10p) — Multi-venue artist invitations + post-signup "Request Preferred Status" popup + global My Invites tracker.** Major UX flow: when a venue user invites artists from the User dropdown, the modal now (a) lists every venue they control as checkboxes (all pre-checked, can deselect), (b) sends ONE email per recipient mentioning every selected venue, and (c) plants a single-use token in `artist_invitations` so the invitee's signup or next login is tied back to the invite. After signup or login, a "Request Preferred Status at:" popup fires automatically — checkboxes for every venue on the token, with venues where the artist is already preferred greyed out as "✓ Already preferred". One click batch-creates all the preferred requests via the existing flow; "Maybe later" dismisses without creating any. If every venue on the token is already preferred, the popup just says "you're all set" and dismisses.
  - **Schema migration** ([db.py](backend/db.py)): added `token TEXT`, `token_expires_at`, `invite_group_id TEXT`, `bounce_reason TEXT`, `declined_at`, `preferred_requested_at` to `artist_invitations`, plus indexes on `token`, `invite_group_id`, and `LOWER(invited_email)`. Live DB migrated via `ALTER TABLE`. Old rows preserved with NULL token (per-venue tracker still handles them; My Invites page renders them as legacy with no resend button).
  - **New endpoints** (all in [me.py](backend/routes/me.py)):
    - `POST /api/me/invite-artists` — `{emails, venue_ids, message?}`. Writes N×M rows (one per email × venue pair), all sharing one token+invite_group_id per recipient email. Validates `check_venue_access` for every venue. Sends one email per recipient with consolidated venue list. Captures `SMTPRecipientsRefused`/`SMTPDataError` synchronously → marks row `status='bounced'` with `bounce_reason`. No per-batch email cap (per user request). 24h "already-pending" per (email, venue) dedup so accidental double-sends don't spam.
    - `GET /api/artist-invitations/by-token/{token}` — public; returns inviter name, invited_email, venue list (with `already_preferred` and `preferred_status` per venue), `is_existing_user` flag. Returns 410 if token expired (90 days). The signup page redirects to login if `is_existing_user` so users don't get a duplicate-account collision.
    - `POST /api/artist-invitations/{token}/accept-preferred` — authenticated; requires `user.email == invited_email`. Body `{venue_ids: [...]}` (defaults to all eligible). Skips already-approved/pending venues. Creates `preferred_artists` rows and fires the existing notification flow. Stamps all sibling rows `status='preferred_requested'` with `preferred_requested_at`.
    - `POST /api/artist-invitations/{token}/dismiss` — authenticated; "Maybe later" path. Stamps `preferred_requested_at` so popup doesn't reappear.
    - `GET /api/me/pending-artist-invite` — returns the oldest unconsumed token for the current user (matched on email, case-insensitive). Drives the popup fire-on-load logic in `user-dropdown.js`.
    - `GET /api/invitations/{token}/decline` — public; returns a styled HTML confirmation page. Marks all rows for the token `status='declined'` with `declined_at`. Idempotent — clicking the link twice or pasting a bad token both show a generic confirmation (no 404 to confuse a casual user).
    - `POST /api/artist-invitations/{token}/resend` — authenticated; original inviter only. 24-hour cooldown against max(sent_at, last_resent_at). Bumps `resent_count`, sets `last_resent_at`, captures bounce same way as initial send.
    - `GET /api/me/invitations` — aggregated cross-venue listing for the current user. One row per (email, venue), grouped on the client by token.
  - **Frontend rewrites:**
    - [user-dropdown.js](app/static/js/user-dropdown.js): invite modal now shows multi-venue checkboxes (loaded from `/api/my/venues`) with Select-all/none controls; `POST /api/me/invite-artists` replaces the per-venue endpoint. Submit returns `{sent_count, bounced_count, skipped_already_pending_count, invalid_count, bounced[], skipped[], invalid[]}` — UI renders all four buckets. New "My Invites" link added to the dropdown for any user with ≥1 venue. Pending-invite popup logic appended (fires on every authenticated page except signup/login/legal/verify). Cache-buster `?v=5`.
    - [signup-new-init.js](app/static/js/signup-new-init.js): reads `?invite=<token>` from URL → calls by-token, redirects to `/app/index.html?invite=<token>` if `is_existing_user`; otherwise prefills + locks email field, force-selects 'artist' role, shows a cyan banner with the inviter + venues phrase. Cache-buster `?v=7`.
    - [index-init.js](app/static/js/index-init.js): `?invite=<token>` on login page → banner above the form, prefills email. Cache-buster `?v=7`.
    - [venue-email-center.js](app/static/js/venue-email-center.js): per-venue tracker now renders 8 statuses with distinct badges (pending, signed_up, preferred_requested, preferred_approved, preferred_denied, bounced, declined, expired). Bounce reason shown inline as a red box below the row. Cache-buster `?v=5`.
    - New page [my-invites.html](app/my-invites.html) + [my-invites.js](app/static/js/my-invites.js): aggregated cross-venue invite tracker. Filter chips for each status, cards grouped by token (one card = one multi-venue invite) with per-venue pills, resend button with cooldown error handling, bounce display.
  - **Email template** is inline in [me.py](backend/routes/me.py) `_invite_render_email` (mirror of the existing pattern in main.py:invite_artists for the legacy per-venue endpoint, but now multi-venue-aware). Subject says "First-name from N venues invited you to join GigsFill!" (or specific venue name if N=1). Body has the personal message in a styled blockquote, an "Already-a-user" path that swaps Create-account for Log-in, and a small "Not interested? Decline" link in the footer.
  - **The legacy `POST /api/venues/{vid}/invite-artists` endpoint in main.py is untouched** — kept working for any callers that haven't been migrated. The new endpoint is the canonical path going forward.
  - **Smoke tested:** API + scheduler restarted, both active. Unauth GET pending-invite/my-invitations/POST accept-preferred/POST resend → 401. Decline link with bogus token → 200 friendly page. New page → 200. Existing legacy row (pre-migration, no token) preserved.
  - **Open follow-up for v2:** real async bounce parsing (right now we only catch synchronous SMTP refusal — delivered-then-rejected bounces from the mailbox provider aren't captured); analytics widget on the admin dashboard for invite → signup → preferred-approval conversion rate.

- **2026-06-14 (part 10o) — Admin-configurable rate limits (no restart).** The six named rate limits (login, signup, password reset, support, email-send, affiliate-track) are now editable from **Admin → Platform Settings → Email Settings → Rate Limits**. Each input is an integer "requests per minute per source IP"; defaults match the prior hardcoded values (5/3/3/2/10/30).
  - **Why callables, not constants:** slowapi's `@limiter.limit("5/minute")` is a decorator evaluated at import time, so changing a module constant at runtime would be invisible until restart. Instead [`backend/rate_limiter.py`](backend/rate_limiter.py) now exposes callable getters (`rate_login_limit()` etc.) that slowapi re-evaluates on every request. The callables read from `platform_settings` via a 30-second TTL cache (thread-safe double-checked locking) so admin changes show up within 30s of save, or immediately if `invalidate_cache()` fires.
  - **Cache invalidation hook:** [`admin.py:update_settings`](backend/routes/admin.py) detects rate-key changes and calls `rate_limiter.invalidate_cache()` after the commit — so the next inbound request sees the new value with no TTL wait. Also validates each rate setting is a positive integer in [1, 100000] before storing; rejects garbage with a 400.
  - **Backwards compat:** `RATE_LOGIN`, `RATE_SIGNUP`, etc. string constants still exist with the default values so any decorator that imports them as strings keeps working. New code should use the callables.
  - **Files changed:** [rate_limiter.py](backend/rate_limiter.py) (rewrite — callable getters + TTL cache), all consumers switched to callables ([auth.py](backend/routes/auth.py), [affiliate.py](backend/routes/affiliate.py), [entity_users.py](backend/routes/entity_users.py), [messages.py](backend/routes/messages.py)), [db.py:default_settings](backend/db.py) seeds `rate_*` defaults, [admin.py:update_settings](backend/routes/admin.py) accepts the new keys + invalidates cache, [admin.html](app/admin.html) Email Settings sub-tab now has a Rate Limits grid, [admin-platform.js](app/static/js/admin-platform.js) (`?v=2`) loads + autosaves the fields. Live DB seeded with the same defaults.
  - **Verified end-to-end:** updating the DB row from 5 → 42 + invalidate_cache → `rate_login_limit()` returns "42/minute" immediately; both uvicorn workers came up Redis-backed; unauth GET on the admin endpoint still returns 401.
  - **Note:** the older `RATE_SUPPORT` constant is still consumed by a hardcoded support endpoint that wasn't touched (no `@limiter.limit(RATE_SUPPORT)` exists yet in the codebase) — the UI knob is wired and ready for future use.

- **2026-06-14 (part 10n) — Pre-demo audit fixes: closed an authorization gap, finished the pay-override sweep, hardened blast-email rendering against stored HTML.** Multi-agent audit run pre-demo surfaced three real bugs (and several false positives that were disproved before acting):
  1. **Authorization gap on `GET /api/venues/{venue_id}/preferred-artists-with-gigs`** ([preferred_artists.py:300](backend/routes/preferred_artists.py)) — endpoint was missing `check_venue_access`, so any authenticated user could enumerate any venue's preferred-artist roster, per-artist pay overrides, frequency overrides, gig history, and private venue notes. Verified data leak; one-line fix calling `check_venue_access(db, venue_id, user.id)` at the top. Other ~10 endpoints in the same file already gate correctly; this was an outlier missed when the My Artists feature was added.
  2. **Pay-override audit (part 10m) miss** — [`contracts.py:_apply_slot_pay_override`](backend/routes/contracts.py) at line 63 read the override without `status='approved'` filter. Called from booking paths at lines 1570, 2408, 2479, 2523 — meant a revoked artist with stale override columns could still get the old override applied at slot booking time, writing the wrong amount onto `gig_slots.pay`. Mirror of the bug fixed elsewhere in 10m, slipped through that sweep. Added the filter.
  3. **Blast emails didn't HTML-escape user-controlled fields** — [`scheduler.py:render_template`](backend/scheduler.py) did raw `str.replace()` substitution while [`email_service.py:render_template`](backend/email_service.py) uses `html.escape()` + `_HTML_SAFE_KEYS` allowlist. Effect: a venue named `<a href="http://phish.example/">Click</a> Bar` would render as a live link in every nearby-artist blast email — phishing vector via stored content. Added a matching `_SCHED_HTML_SAFE_KEYS` allowlist (must stay in sync with email_service.py's) and `html.escape()` everywhere else. Verified end-to-end: `<a href=...>` in `venue_name` → `&lt;a href=...&gt;`, but `slots_html` and `far_notice_*` still pass through unchanged.
  - **False positives the audit caught (no changes needed)**: countersign_contract DOES guard contract_type ([contracts.py:1457](backend/routes/contracts.py)); only one real `INSERT OR REPLACE` in codebase (platform_settings, low-risk); models.py partial ORM mirror is by-design per CLAUDE.md.
  - Operational reminder before demo: `rate_limiter.py` defaults are tight (signup 3/min, login 5/min, 15-min `(email, IP)` lockout). Bump for demo day or test from a private IP.
  - API + scheduler restarted, both active. Smoke test: `curl -s /api/venues/1/preferred-artists-with-gigs` returns 401 unauth (was returning 200 + data).

- **2026-05-29 (part 10m) — Pay-override audit: status='approved' filter added across every override lookup; analytics + dormant Stripe endpoint hardened.** Discovered during the part-10l investigation that multiple `WHERE venue_id=? AND artist_id=?` lookups on `preferred_artists` were missing a `status='approved'` clause. Because the override columns (`pay_dollars_override`, `pay_cents_override`) are not cleared when a venue revokes (status='revoked'), denies, or before approving (pending), those lookups would silently re-apply an inactive override to a non-preferred artist. Audited every site that reads override fields and fixed each:
  - **Money flow** — [`gigs.py:_get_effective_pay`](backend/routes/gigs.py) (called by `_create_booking_transaction` at booking time, by `_recompute_gig_fees`, and by the `/api/gigs/{id}/effective-pay` endpoint used by the venue calendar) now filters status='approved'. Booking-time `amount_cents` written into `transactions` is now guaranteed correct; downstream payouts read from `transactions` so they inherit the fix.
  - **Stripe endpoints** — [`stripe_connect.py`](backend/routes/stripe_connect.py): both override fetches (cancel-payment record at ~759, recompute on refund-or-charge at ~1069) now filter status='approved'. Also hardened the dormant `POST /api/stripe/charge-booking` to **recompute `amount_cents` server-side** via `_get_effective_pay(db, venue_id, artist_id, slot.pay-or-gig.pay)` instead of trusting the client-supplied value — defense-in-depth against any future caller that might send a manipulated amount.
  - **Display paths** — [`email_dispatch.py:_get_effective_pay_for_slot`](backend/services/email_dispatch.py), [`gig_modal.py`](backend/routes/gig_modal.py) (artist-viewer slot pay override), [`waitlist.py`](backend/routes/waitlist.py) (both the offer-email override fetch ~835 and the inline `_get_effective_pay` helper ~1114), [`artists.py`](backend/routes/artists.py) (`get_artist_gigs` effective_pay annotation ~616), and [`preferred_artists.py:get_preferred_artists_with_gigs`](backend/routes/preferred_artists.py) (My Artists per-row effective_pay annotation: now also gated on `preferred_status == 'approved'` in Python). All now apply the override only when the relationship is actively approved.
  - **Admin analytics** — [`analytics.py`](backend/routes/analytics.py) "Recent bookings" widget on the admin dashboard joined `gigs ⨝ gig_slots` only and rendered raw `b.pay`, ignoring override entirely. Now `LEFT JOIN preferred_artists pa ... AND pa.status='approved'` with a portable `CASE WHEN override > pay THEN override ELSE pay END` expression so admins see what artists are actually being paid.
  - Sites already correct: `contracts.py:_get_effective_pay_str` (~166, ~231), `admin.py` reports (~394, ~452), `scheduler.py` preferred-artist blast loop (~655, now also passes `artist_override_pay` into `_build_slots_html_for_scheduler` per part 10m precursor), and frontend (`artist.book-gigs.js` builds `venuePayOverrides{}` via `status === 'approved'` filter at ~742).
  - Verified end-to-end with live data: Fifty Proof approved at 14 Cannons with $15 override + $10 gig → returns $15; Fridays Past revoked at 14 Cannons with stale $15 override columns → returns $10 (override correctly ignored); no preferred row → returns base pay; $20 base with $15 override → returns $20 (max wins). API + scheduler restarted, both active.

- **2026-05-29 — Scheduler pre-gig blast emails now apply per-artist pay override on multi-slot gigs.** Symptom: Fifty Proof (preferred at 14 Cannons with a $15 pay_dollars_override) received a `venue_open_gig_1w` "Last Chance to Book" blast showing each slot's pay as $10 — 14 Cannons' published per-slot rate — instead of the $15 override. Root cause: [`_build_slots_html_for_scheduler`](backend/scheduler.py) accepted only a single `gig_pay` scalar and rendered `s[2] or gig_pay or '0'` per slot, so the slot's published pay always won and the override was silently ignored. The post-creation new-gig blast ([`gigs.py:_build_slots_html`](backend/routes/gigs.py)) already handled this via an `artist_override_pay` parameter using `max(slot_pay, override)`; the scheduler helper hadn't been kept in sync. Fix: added `artist_override_pay=None` parameter to the scheduler helper and apply `max(base_pay, artist_override_pay)` per slot, then wired the preferred-artist branch of `process_open_gig_notifications` to pass the artist's `override_amt` (cleanly initialized + captured during the existing override lookup). Affects the 4w/2w/1w/36h pre-gig blast templates (`venue_open_gig_*`). Scheduler restarted; new blasts pick up the fix.

- **2026-05-28 (part 10l) — Hide Pay/Frequency chip on non-approved rows (both sides).** The Pay/Frequency display on the artist's My Venues tab and the Override Settings chip on the venue's My Artists tab were rendering for every status — including revoked/denied/non-preferred/banned. Since `pay_dollars_override` / `frequency_days_override` only apply while the relationship is actively `approved`, the chip was falling back to `venues.default_pay_dollars` for non-preferred rows and showing a number that looked like a negotiated rate. Example: Fridays Past (revoked at 14 Cannons) showed "Pay: $200.00" — that was just 14 Cannons' current default pay, not anything specific to the artist. Fix: render the chip only when `status === 'approved'`; otherwise emit an empty `<div>` to keep the 3-column grid stable. ([my-venues-redesign.js:404](app/static/js/my-venues-redesign.js#L404), [my-artists.js:518](app/static/js/my-artists.js#L518)) Cache-busters bumped (my-venues-redesign.js?v=11, my-artists.js?v=6).

- **2026-05-28 — venue-profile.html script-tag XSS-of-self bug.** A `//` JS comment block in [venue-profile.html](app/venue-profile.html) explained the part-5 stored-XSS fix and contained the literal string `<script>...</script>`. The HTML parser doesn't honor JS comments inside `<script>` — it scans for the literal `</script>` end-tag and terminated the script element mid-function, dumping the rest of the JS (calendar render, gig modal, flyer modal, media loader, reviews) into the DOM as visible text on the venue public profile page. Symptom seen on artist's My Venues tab → click venue link → page showed "Sun Mon Tue Wed Thu Fri Sat" then raw JS source. Fix: rewrote the comment without the literal closing tag. ([venue-profile.html:379-380](app/venue-profile.html#L379-L380))

- **2026-05-27 (part 10k) — Bucket overhaul: 5 status bubbles + renames + Non-Preferred + Banned (both sides).**
  - **Renames** (both My Artists + My Venues): "Preferred" → "Preferred Artists"/"Preferred Venues"; "Preferred Revoked" → "Preferred Status Revoked"; "Denied" → "Preferred Status Denied".
  - **New "Non-Preferred Artists" / "Non-Preferred Venues" bubble** (4th, cyan) — artists/venues with gig history but NO preferred relationship and not banned (e.g. last-minute open-blast-window bookings). Backend: venue endpoint ([preferred_artists.py:get_preferred_artists_with_gigs](backend/routes/preferred_artists.py)) now appends these tagged `preferred_status='non_preferred'`; artist endpoint already tagged them `status='normal'` and the frontend treats that as the non-preferred bucket. Rows open the same Past Gigs modal. Venue-side action buttons for these: **Rate Artist / Make Preferred / Ban** (no Revoke).
  - **New `POST /api/venues/{vid}/artists/{aid}/make-preferred`** — promotes a non-preferred or revoked artist to `approved` (upsert; refuses if banned). Wired to the "Make Preferred" button + `makePreferred()` in my-artists.js.
  - **"Banned Artists" / "Banned Venues" bubble** (5th, dark red) — already existed venue-side as a conditional "Banned" bubble; relabeled. **Artist side now detects bans too**: `get_artist_venues` ([artists.py](backend/routes/artists.py)) overlays `status='banned'` for any venue that banned the artist, surfaced in the new Banned Venues bubble.
  - **Ban→unban now restores `revoked`** ([preferred_artists.py:unban_artist](backend/routes/preferred_artists.py)) — if the unbanned artist has gig history, a `preferred_artists` row is re-created with `status='revoked'` so their history lands in "Preferred Status Revoked" instead of vanishing. Backfilled Fridays Past (orphaned by an earlier ban→unban) to revoked.
  - All five bubbles except Preferred/Denied render only when their count > 0 (consistent with prior conditional pattern — no empty "0" clutter). Buckets are mutually exclusive. Verified data for 14 Cannons (Preferred: Fifty Proof; Revoked: Fridays Past w/ 13 gigs), endpoints auth-gated, both JS files + Python parse clean.

- **2026-05-27 (part 10j) — Artist My Venues Past Gigs modal + "Preferred Revoked" bubble (both sides).** Mirrored the part-10i feature to the artist side and added a new status bubble to solve the "lost preferred status → past gigs have no home" gap on both pages.
  - **Artist My Venues** ([my-venues-redesign.js](app/static/js/my-venues-redesign.js)): removed the "Past Gigs" bubble; each venue row is now clickable (name link, override box, action buttons, inline-gigs block all `stopPropagation`) and shows "📅 Past Gigs ›" under the venue name; opens `openPastGigsModal(venueId, venueName)` — same sortable/paginated/editable-notes table as the venue side, with Message Venue + 🎨 Flyer (view) per row. Flyer reuses the existing document-level `.gig-flyer-btn` delegation, so modal rows work automatically.
  - **"Preferred Revoked" bubble — BOTH sides** ([my-artists.js](app/static/js/my-artists.js), [my-venues-redesign.js](app/static/js/my-venues-redesign.js)): `revoke` sets `preferred_status='revoked'`, which was previously lumped into the Denied count/filter. Split it out: Denied now = `denied` only; a new amber **Preferred Revoked** bubble sits between Preferred and Denied and shows revoked entities **that have gigs** (history that needs a home). Only renders when count > 0. Added a matching "Preferred Revoked" status badge on the rows. Since rows are clickable, revoked artists/venues open the same Past Gigs modal.
  - **New `artist_gig_notes` table** ([db.py](backend/db.py)) — the artist's private per-gig notes (mirror of `venue_gig_notes`), UNIQUE on (artist_id, gig_id, venue_id).
  - **New endpoints** ([preferred_artists.py](backend/routes/preferred_artists.py)): `GET /api/artists/{aid}/venues/{vid}/past-gigs` (artist-auth'd, same gigs/slots/contracts/transactions union so cancellations appear) and `PUT /api/artists/{aid}/gigs/{gid}/venue-note/{vid}` (upsert artist note).
  - Verified: artist past-gigs query returns 13 rows for a test artist, note upsert collapses to one row, endpoints auth-gated (401 unauth), both JS files + Python parse clean.

- **2026-05-27 (part 10i) — My Artists → Past Gigs modal.** Replaced the global "Past Gigs" stat-bubble on the venue My Artists tab with a per-artist Past Gigs modal.
  - **Removed** the "Past Gigs" bubble (kept Preferred / Denied / Banned). Each artist row is now clickable (except the hyperlinked name, the override-settings inputs, the action buttons, and the inline upcoming-gigs block — all `event.stopPropagation()`), and shows a "📅 Past Gigs ›" affordance under the artist name. ([my-artists.js](app/static/js/my-artists.js))
  - **New modal** `openPastGigsModal(artistId, artistName)` — dark-styled overlay matching the app's modal look, backdrop-click + ✕ to close. Table with sortable column headers (Date, Time, Pay, Status — click to sort, toggles asc/desc, default Date-descending = most recent first), 10-per-page pagination, an editable auto-growing Notes `<textarea>` per row (autosaves on blur), and per-row Message / 🎨 Flyer buttons reusing the existing `openMessageModal` / `flyerEditor.open`.
  - **New table `venue_gig_notes`** (venue_id, gig_id, artist_id, notes; UNIQUE on the triple) — the venue's private per-gig notes, distinct from public `gigs.notes`. ([db.py](backend/db.py))
  - **New endpoints** ([preferred_artists.py](backend/routes/preferred_artists.py)):
    - `GET /api/venues/{vid}/artists/{aid}/past-gigs` — past-dated gigs where the artist is linked via ANY of gigs.artist_id / gig_slots / gig_contracts / transactions (so cancellations, where the live link is cleared but the contract/txn survives, still show). Returns derived display status (Booked / Cancelled / Contract Pending / …), effective pay (venue override-aware), this-artist's slot times, and the venue note. Venue-local "today" boundary.
    - `PUT /api/venues/{vid}/gigs/{gid}/artist-note/{aid}` — upsert the venue's private note (ON CONFLICT on the unique triple). Verified idempotent.
  - Verified: query returns 13 past gigs for a test artist (date-desc), note upsert collapses to a single updated row, endpoint is auth-gated (401 unauth), all JS/PY syntax clean.

- **2026-05-27 (part 10h) — Far-away booking alerts.** The blast-email radius (20mi default) only gates who's NOTIFIED — any artist on the platform can book during an open-blast window (intentional: a touring band in town can grab an opening). Added awareness without blocking:
  - **New admin setting `far_booking_alert_miles`** (default 50) — seeded in [db.py](backend/db.py) default_settings, added to `/api/admin/settings` GET + PUT allowlist ([admin.py:518](backend/routes/admin.py#L518)), and a "Far-Away Booking Alert" field in admin.html Platform Settings → Email/General sub-tab, wired through `loadEmailSettings`/`autoSaveSettings` in admin-platform.js.
  - **Distance computed at booking-email time** ([email_dispatch.py:send_booking_emails](backend/services/email_dispatch.py)) — haversine between the artist's and venue's lat/lng. If beyond the threshold, two pre-styled HTML notice blocks are built and injected as `far_notice_artist` / `far_notice_venue` template vars (added to `_HTML_SAFE_KEYS`).
  - **Venue email** gets a blue heads-up: "{artist} is based in {city, state}, about {N} miles away. They booked through your open-gig window. If this looks like a mistake, you can cancel from the gig details." (`{{far_notice_venue}}` in `venue_gig_booked`).
  - **Artist email** gets a soft amber notice: "this venue is in {city, state}, about {N} miles from your listed location. Just making sure you can perform in person — if you booked by mistake, please cancel." (`{{far_notice_artist}}` in `artist_gig_booked`). Soft/informational only — booking is never blocked.
  - Both notices are empty strings when the artist is within threshold, so normal local bookings are unaffected. Verified haversine math (San Diego 139mi → flagged, same-city 0mi → not flagged) and confirmed both template placeholders synced to the DB.
  - **Activity Center notifications too** ([notification_service.py:notify_gig_booked](backend/services/notification_service.py)) — the same far-away check now appends a heads-up suffix to the in-app `gig_booked` notification for both sides (not just the emails). Artist sees "⚠️ Note: this venue is in {city, state}, ~{N} mi away — make sure you can perform in person." Venue sees "⚠️ Note: {artist} is based in {city, state}, ~{N} mi away (booked via your open-gig window). Cancel from the gig if this looks wrong." Distance is computed once and reused across all entity_users; the shared-owner case (one user owns both venue + artist) gets the venue-side framing + suffix. Empty when within threshold.

- **2026-05-27 (part 10g) — Ban notification email + non-preferred artist could see Book buttons.**
  - **Ban email** ([preferred_artists.py:ban_artist](backend/routes/preferred_artists.py)) — banning an artist created an in-app "Booking Access Removed" notification but sent NO email. Added a neutral-toned ("booking access updated", not punitive) HTML email via `EmailService._send_raw_email`, fanned out to all of the artist's entity_users. Uses the same inline-logo, `multipart/alternative` path as all other emails (no paperclip).
  - **Non-preferred artist saw Book buttons on frequency-exempt gigs** — two compounding bugs:
    1. **Data inconsistency**: `frequency_exempt=1` is only ever set together with `radius_blast_token` during a blast (gigs.py:5496/5954/6045). The part-of-startup cleanup that nulls stale `radius_blast_token` on non-blast gigs left `frequency_exempt=1` behind — an impossible state. Extended the [db.py:1441](backend/db.py#L1441) cleanup to clear `frequency_exempt` in the same sweep (cleared 23 gigs on first run).
    2. **Logic bug**: the gig modal's `not_preferred` relationship check ([gig_modal.py:92](backend/routes/gig_modal.py#L92)) was gated on `and not gig_freq_exempt`, so any frequency-exempt gig was treated as bookable by non-preferred artists. `frequency_exempt` only waives the booking-FREQUENCY limit — it must NOT bypass the preferred-status requirement. Removed `gig_freq_exempt` from that condition; only a valid active blast (`gig_is_blast_open`, which needs a live `radius_blast_token`) opens a gig to non-preferred artists.

    The backend `book_gig`/`book_slot` were already safe — they reject non-preferred artists with 403 regardless of `frequency_exempt` — so this was a UI-only exposure (and the stale data). Verified: Fridays Past (preferred_status=None, unbanned) on an open 14 Cannons gig now resolves to `not_preferred` → modal shows "request preferred status," not Book buttons.

  - **Follow-up: modal now honors the open-gig "blast all nearby artists" window.** After the above fix, a non-preferred artist couldn't book even when the venue's "any artist within 20mi can book within 36h/1week" setting (`blast_all_enabled=1`) made them eligible. Two parts:
    1. **Modal wasn't checking it** — `get_gig_modal_data` only treated a gig as open-to-non-preferred when it had a live `radius_blast_token`. Now it also calls the booking backend's authoritative `_open_blast_bypass_active(db, venue_id, gig_id)` and folds the result into `gig_is_blast_open`, so the modal and the booking endpoints can never disagree. ([gig_modal.py:421](backend/routes/gig_modal.py#L421))
    2. **Bypass was too strict on email evidence** — `_open_blast_bypass_active` required a `gig_email_log` row for the EXACT `open_gig_*` notification key. A recurring gig created INSIDE an already-passed window gets a `new_gig_blast` row instead (which itself sends to non-preferred nearby artists within radius when blast_all is on — batch_blast at gigs.py:6252), never an `open_gig_1w` row. Broadened the accepted keys to `open_gig_36h/1w/2w/4w`, `new_gig_blast`, `radius_blast`, `cancelled_blast` — all of which mean "this gig was advertised to non-preferred artists." ([gigs.py:1387](backend/routes/gigs.py#L1387)) Verified: Fridays Past → `open_bookable` on this Friday's gig (within 1-week window), `not_preferred` on a gig 9 days out (outside the window).

- **2026-05-27 (part 10f) — Overnight-time correction prompt was dismissible.** In the venue Edit Gig modal, entering an end time before the start (e.g. 9:00 PM – 12:00 PM, where the user meant 12:00 AM) pops a "Did you mean...?" prompt with "Yes, fix it" / "No, keep it". But the prompt used the default `dismissible: true`, so clicking the backdrop or pressing Esc closed it without a choice — silently leaving the wrong next-day time in place. Added `dismissible: false` to that `showStyledModal` call ([venue.create-gigs.js:393](app/static/js/venue.create-gigs.js#L393)) so the user must pick one of the two buttons. (`dismissible:false` in gf-modals.js hides the X, disables Esc, and disables backdrop-click — buttons still close normally.)

- **2026-05-27 (part 10e) — Entity-user invitation emails never delivered.** Venue/Artist → Users tab → Invite Users → "Send Email" returned 200 OK and the UI said "Email Sent!", but no email arrived (same for the Re-invite action). Root cause: `_send_invitation_email` in [entity_users.py](backend/routes/entity_users.py) hand-rolled its own smtplib block that called `starttls()` unconditionally with no preceding `ehlo()` and no fault tolerance — fragile against the production SMTP server (`mail.gigsfill.com:26`). Every other email in the app routes through the shared `_smtp_send()` helper ([email_service.py:32](backend/email_service.py#L32)), which does `ehlo()` + best-effort STARTTLS and handles port 26 in its catch-all branch. Fix: `_send_invitation_email` now delegates to `EmailService._send_raw_email()` (which uses `_smtp_send`), the identical proven path used by recommend/notification emails. Also fixed the existing-user invite branch which previously claimed "sent via email" regardless of the actual send result — now surfaces the real outcome (the in-app notification still fires either way, so the invite is never lost). Verified: test invite logs `Raw email sent to ...`.

  **Bonus — phantom-attachment (paperclip) fix.** The old invitation send used a bare `MIMEMultipart()` which defaults to subtype `multipart/mixed` — Gmail/Outlook render that with a paperclip / phantom "attachment" even though the only part is the HTML body (the logo is a remote `<img src="https://gigsfill.com/.../gigsfill-logo_light.png">`, never actually attached). Routing through `_send_raw_email` (which uses `MIMEMultipart('alternative')`) already eliminated it for invitations. Then swept the rest of the codebase: converted 9 more bare `MIMEMultipart()` → `MIMEMultipart('alternative')` across `main.py` (5: recommend, health, admin alerts), `routes/auth.py` (2: verify, password reset), `routes/admin.py` (1), `routes/tax.py` (1: 1099 email — verified it's HTML-only, no PDF attached, so 'alternative' is correct). Now every email in the app uses the inline-logo, no-paperclip structure. None of these paths attach real files, so no legitimate attachment was affected.

- **2026-05-27 (part 10d) — "New Gigs Available" email indentation hierarchy.** The multi-slot new-gig blast rendered Date, Slot headers, and slot fields all left-justified at the same margin — hard to scan. Added a visual hierarchy via per-row `padding-left`: Date/Event at base (0px), "Slot N" headers indented 16px, and each slot's Time/Pay/Type/Lineup/Styles fields indented 32px (single-slot gigs indent fields 16px since there's no Slot header to nest under). Label column width shrinks as indent grows so value columns don't drift too far right. Applied in both [gigs.py:_build_gigs_html](backend/routes/gigs.py) (the new-gig / batch blast) and [scheduler.py:_build_slots_html_for_scheduler](backend/scheduler.py) (the scheduled 4w/2w/1w/36h open-gig blasts) — and the scheduler builder now also emits "Slot N" headers for multi-slot gigs, which it previously omitted entirely.

- **2026-05-27 (part 10c) — Postgres compatibility + remaining Lows (~200 latent issues centrally translated + 5 nits).**

  - **Centralized SQLite → PostgreSQL SQL translation** ([db.py:_sqlite_to_pg](backend/db.py)) — instead of rewriting ~190 individual queries scattered across the codebase, added a translation layer at the boundary. Two attach points:
    - `_PgCompatConn._translate` (raw `conn.execute(...)` callers — payout_scheduler, scheduler, webhook handlers).
    - SQLAlchemy `before_cursor_execute` event listener (ORM `db.execute(text(...))` callers — the bulk of the codebase).

    Patterns translated automatically on Postgres:
    - `?` placeholders → `%s` (existing — kept)
    - `INSERT OR IGNORE INTO X (...) VALUES (...)` → `INSERT INTO ... ON CONFLICT DO NOTHING`
    - `INSERT OR REPLACE` — keyword stripped + admin warning logged (manual ON CONFLICT DO UPDATE rewrite needed per call site)
    - `datetime('now', '-N days|hours|minutes|seconds|months|years')` → `(CURRENT_TIMESTAMP - INTERVAL 'N <unit>')`, same for `+N`
    - `datetime('now')` → `CURRENT_TIMESTAMP`
    - `date('now')` and `date('now', '+N days')` → `CURRENT_DATE` and `(CURRENT_DATE + INTERVAL '...')`
    - `julianday(a) - julianday(b)` → `EXTRACT(EPOCH FROM ((a)::timestamp - (b)::timestamp))/86400.0`
    - `julianday('now')` special-cased → `julianday(CURRENT_TIMESTAMP)` BEFORE subtraction pattern matches
    - `last_insert_rowid()` → `lastval()`
    - `strftime('%Y', x)` → `to_char((x)::timestamp, 'YYYY')`, same for `'%Y-%m'` and `'%Y-%m-%d'`
    - `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY`

    NOT translated (require manual rewrite when migrating):
    - `INSERT OR REPLACE` (needs column-list-aware ON CONFLICT DO UPDATE — admin warning logged at use)
    - SQLite-specific `JSON_*` functions (not used in this codebase)
    - Other `strftime` formats outside the three common cases above

    The translation is a no-op when running on SQLite (the event listener and conn shim are never instantiated). Verified the seven highest-volume patterns roundtrip correctly via unit-test in db.py docstring sanity tests.

  - **Lows batch** (5 nits closed):
    - **`GROUP_CONCAT` dead code** ([affiliate.py:1084](backend/routes/affiliate.py#L1084)) — `GROUP_CONCAT(DISTINCT ae.venue_id) as venue_ids` was selected and never read. Removed; also Postgres-incompatible (PG uses `string_agg`).
    - **Affiliate-code generation race** ([auth.py:537](backend/routes/auth.py#L537)) — the SELECT-then-UPDATE pattern could 500 on race-losers when two parallel signups picked the same hex by chance (4.3B namespace, vanishingly rare but possible). Now catches the unique-index IntegrityError, retries up to 20 times, logs an `error` line if all attempts collide so the bug is findable instead of silent.
    - **notification_sent_log cleanup in `delete_recurring_gigs`** ([gigs.py:3693](backend/routes/gigs.py#L3693)) — added so a re-created gig with a reused id doesn't inherit suppression flags from the deleted ancestor.
    - **`_send_quarterly_affiliate_email` AttributeError fallback** ([affiliate.py:1292](backend/routes/affiliate.py#L1292)) — when `_send_raw_email` is missing, the fallback used to call `send_notification_email(variables={})` — the template would render literal `{{user_name}}` and `{{headline}}` in the recipient's inbox. Now passes the real values into the fallback path.
    - **PG-incompatibility audit** — every `INSERT OR IGNORE`, `datetime('now', '-N days')`, `strftime`, `julianday`, and `last_insert_rowid()` call site in the codebase is now silently translated when running on PG. No further per-call-site rewrites needed for migration.

  - **Verified**: services restart clean, public API responds 200, PDF guard still 403s, translator unit-tested on 7 highest-volume patterns (all pass after the `julianday('now')` edge-case fix).

- **2026-05-27 (part 10b) — Part-10 finalization (5 fixes).** Closed the deferred items from part 10.
  - **Same-day-approval contract auto-execution** ([gigs.py:approve_booking](backend/routes/gigs.py)) — when a contract-required venue approves a same-day non-preferred booking, a digital `gig_contracts` row is now auto-created in `fully_signed` status. The artist's "Book Now" click and the venue's "Approve" click ARE the signatures: both are recorded with timestamps and IPs as `artist_signature_*` and `venue_signature_*`. Body of the contract captures the venue's standard contract_body. Audit trail is preserved; same-day flow stays fast. Previously this path silently bypassed `require_for_booking=1` — bookings happened that were contractually unenforceable.
  - **PDF countersign page wording strengthened** ([contracts.py:_generate_signature_page_pdf](backend/routes/contracts.py)) — the appended countersignature page now explicitly states "This page is the venue countersignature of the contract on the preceding pages" and "The artist signature on the prior pages and this countersignature together constitute the fully-executed contract." Closes the legal ambiguity from part-10 finding C3 without requiring coordinate-hunt overlay surgery.
  - **27 debug `print()` statements removed** from PDF stamping + download paths in [contracts.py](backend/routes/contracts.py) — bulk-replaced with `logger.debug` so diagnostic info is preserved but no longer spams stdout / journald.
  - **Unicode in PDF signatures** ([contracts.py:_generate_signature_page_pdf](backend/routes/contracts.py)) — names containing accented chars ("José García", "Beyoncé", "Café") were rendering as "?" on signature pages because of `latin-1, errors='replace'` encoding. Now: NFKD-normalize and strip combining marks to closest ASCII before encoding, so "José" renders as "Jose" — readable, even if not character-perfect. Defensive fallback only kicks in when non-ASCII detected.
  - **SQL LIKE wildcard escape in admin venue search** ([affiliate.py:venue_search_for_affiliate](backend/routes/affiliate.py)) — `%` and `_` in user input were treated as LIKE wildcards, expanding search unexpectedly. Now: backslash-escape + `ESCAPE '\\'` clause. A venue named "100% Live" can now be searched literally.

- **2026-05-27 (part 10) — Four-area deep dive: Contracts, Messages, Recurring Gigs, Vanity URLs (~25 fixes from ~80 findings).** Four parallel audit agents combed feature areas that had never been deep-dived as units. Critical security/data-loss findings landed first; High tier followed; key Mediums included.

  - **Critical**:
    - **Signed contract PDFs world-readable via StaticFiles** ([main.py:444](backend/main.py#L444)) — the `/app` StaticFiles mount served `app/static/uploads/contracts/signed/*.pdf` directly with no auth. Filenames followed a guessable pattern (venue + artist + date). Added a `@app.get("/app/static/uploads/contracts/{rest:path}")` shadow route registered BEFORE the mount that returns 403; all PDF access now flows through the auth-gated `download_contract_pdf` endpoint.
    - **`sign_contract` bypassable for `pdf_upload` contracts** ([contracts.py:921](backend/routes/contracts.py#L921)) — the endpoint had no `contract_type` gate. A malicious artist who knew the contract_id could POST signature_name and silently flip a pdf_upload contract to `artist_signed` WITHOUT uploading a signed PDF. Venue countersign then "fully executed" a legal document the artist had not signed. Now: 400 with explanation if contract_type != 'digital'.
    - **PDF stamp failure left contracts "fully_signed"** ([contracts.py:countersign_contract](backend/routes/contracts.py)) — the DB UPDATE ran BEFORE `_stamp_venue_signature_on_pdf`. If stamping raised, exception was swallowed, contract was already marked executed, gig became booked. Artist could later download a "fully signed" PDF that physically lacked the venue signature. Now: stamp first, only UPDATE on success. Admin alert email fires on stamp failure so an operator can intervene. Also pre-validates `signed_pdf_path` is set (refuses 409 if not, instead of attempting stamp on nothing).
    - **Cross-artist message thread disclosure** ([messages.py:236](backend/routes/messages.py#L236)) — `GET /api/gigs/{gig_id}/messages?artist_id=<OTHER>` accepted the param without ownership check. Two artists on the same multi-slot gig could read each other's private threads with the venue. Now: when caller is an artist, force `filter_entity_id = entity_id` (their own); honor an explicit `artist_id` only if they own that artist (e.g. user with multiple acts). Same fix applied to `mark_read`.
    - **Venue deletion orphaned all gig-bound child tables** ([me.py:666](backend/routes/me.py#L666)) — `DELETE FROM gigs WHERE venue_id=...` ran without first cascading `gig_messages`, `gig_slots`, `gig_contracts`, `gig_waitlist`, `gig_email_log`, `gig_cancelled_artists`. Orphan rows lingered forever with broken FKs. Added explicit cascade deletes in the right order.
    - **`delete_recurring_gigs` hard-deleted active bookings** ([gigs.py:3583](backend/routes/gigs.py#L3583)) — filtered only on `parent.status='open'`, but every multi-slot gig stays 'open' until all slots fill. A series occurrence with 1 of 3 slots booked passed the gate and got nuked along with the booked artist's contract + signed PDF + transaction row. One venue click on "Delete All in Series" = silent data loss across all future occurrences. Now: per-row check for in_flight_slots (`booked/pending_contract/awaiting_venue_contract/pending_venue_approval`) + charged_txns; skipped rows surfaced in `skipped_dates`.
    - **`update_recurring_series` desynced slot times** ([gigs.py:3462](backend/routes/gigs.py#L3462)) — the gig-level UPDATE pushed new `start_time`/`end_time`/`pay` to every open parent, but the slot-level rewrite only touched `status='open'` slots. Booked slots kept their original times. Artist whose slot was booked at 7-9pm now found the venue page saying 8-10pm. Now: UPDATE existing slots' times/pay by `slot_number` (regardless of status), then DELETE + re-INSERT only the open slots if count changed.

  - **High — Messages**:
    - **Duplicate paste-error in `_notify_other_party`** ([messages.py](backend/routes/messages.py)) — the function body was pasted twice (lines 930-1110 were a verbatim duplicate). Every message-send re-ran the gig lookup, opened an extra sqlite3 connection, and attempted a second SMTP send against undefined locals. Removed ~180 lines of dead code + the unauth'd `/api/gigs/{gig_id}/messages/debug` info-disclosure endpoint.
    - **Mark-read race auto-marked new inbound** — `last_message_id` query param added; the UPDATE now caps at `id <= :lid` so messages arriving during the polling gap aren't silently marked read.
    - **Inbox unread count vs header badge disagreed** ([messages.py:563](backend/routes/messages.py#L563)) — only the badge was patched in part 9 to exclude `g.status='cancelled'`; the inbox aggregation still counted them. Added the same filter to the inbox subquery.
    - **No in-app notification on new message** — only email fired. Now `create_notification(...)` fires for every message and fans out to `entity_users`. Recipients with email muted finally get a live indicator.
    - **Email coalescing** (5-min throttle per thread per sender) — a chatty exchange of 10 quick replies used to fire 10 separate emails. Now: skip the email if an unread message from the same sender on the same thread was sent in the last 5 minutes. The in-app notification still fires.
    - **Refuse sends on cancelled gigs** — 409 with a clear message; threads remain readable for post-cancel follow-up.

  - **High — Vanity URLs**:
    - **`/api/vanity/check` unauthenticated + unrate-limited** — added `@limiter.limit("30/minute")`. Also: returns a new `reserved` reason separately from `invalid_format` so the UI can say "that name is reserved by GigsFill"; checks the cooldown table to surface `cooldown` reason.
    - **Slug renames broke all old URLs with no redirect + immediate reclaim** ([vanity.py:746](backend/routes/vanity.py#L746)) — every previously-shared link 404'd the moment a user picked a new slug, and an attacker could swoop in and re-claim the freed slug to hijack the old audience. New `vanity_url_redirects` table parks every old slug as a 90-day 301 redirect, and the 30-day reclaim_after lockout blocks re-claim during the redirect window. Old slugs added automatically on every PUT rename.
    - **PUT was a non-atomic DELETE + INSERT** — race-loser would crash with IntegrityError → user got 500. Now: UPDATE the existing row in one statement (preserves `created_at`), catch IntegrityError → return 409 "just claimed by another user". If renaming, the old slug is parked as a redirect in the same transaction.

  - **High — Recurring**:
    - **Backend max-occurrences cap** ([gigs.py:3540](backend/routes/gigs.py#L3540)) — `_HARD_CAP_WEEKS = 104` enforced in `generate_recurring_dates_backend`. Direct API callers could previously pass 500 with 7 days/week and create 3,500 rows in one call.
    - **Catch-up notification thread bloat** ([gigs.py:820](backend/routes/gigs.py#L820)) — a 104-week series used to spawn 104 daemon threads in a 30-second window, each opening its own SQLite connection. Now: recurring children with a sooner occurrence in the same series skip catch-up; only the nearest occurrence fires it.

  - **High — Contracts**:
    - **Charge failure on `fully_signed` contracts now notifies the artist** ([payout_scheduler.py:933](backend/payout_scheduler.py#L933) `_handle_charge_failure`) — when a venue's card fails permanently AND the gig has a fully signed contract, every artist with a `gig_contracts` row gets an in-app notification (fanned out to `entity_users`). Previously the contract was "legally executed" in our DB but money never moved; the artist would show up to perform without any warning.
    - **Same-day-approval bypass logged** — when the contract-required venue accepts a same-day non-preferred booking (which routes through `pending_venue_approval` without contract creation), the approval handler now writes a loud `[CONTRACT_BYPASS]` log line. Full structural fix (route same-day-approved bookings through pending_contract instead of booked) deferred — multi-touchpoint surgery; logging surfaces the cases for admin follow-up.

  - **Medium**:
    - **`debug_contract_pdf` endpoint now admin-only** ([contracts.py:3723](backend/routes/contracts.py#L3723)) — previously any authenticated user could poll any contract_id and get server filesystem paths + file sizes.
    - **Recurring picker UI label** ([venue-create-gigs.html:2332](app/venue-create-gigs.html#L2332)) — `<span>occurrences</span>` → `<span>weeks (cap 104)</span>`. The frontend has counted weeks since the v97 refactor but the visible label still said "occurrences"; venues entering "10" on a Mon+Fri series expected 10 gigs total and got 20.
    - **`max="104"` attribute added to the endAfter input** as a defense-in-depth alongside the new backend cap.

  - **New DB tables / columns**:
    - `vanity_url_redirects` (old_slug PK, new_slug, expires_at, reclaim_after).
    - No other schema additions.

  - **Verified live**: direct PDF URL returns 403, tracking endpoint still sets cookie and redirects, vanity/check returns 200 with rate-limit headers, all 8 touched Python files parse and import cleanly, services restart clean. ~25 fixes total across 4 feature areas.

- **2026-05-26 (part 9c) — Affiliate program end-to-end hardening (16 fixes).** Deep-dive audit by sub-agent flagged 3 bugs I'd just shipped in 9b + a long tail of pre-existing security / correctness gaps. Everything actionable landed.

  - **Self-inflicted fixes from 9b**:
    - **Affiliate to-do checklist endpoint was 500ing** ([onboarding.py:171](backend/routes/onboarding.py#L171)) — query referenced `users.affiliate_stripe_connect_account_id`, but those columns live on `entity_payment_settings` (entity_type='user'). The entire banner silently never showed. Now joins the correct table.
    - **Free-trial 10× re-trigger** ([affiliate.py:643](backend/routes/affiliate.py#L643)) — the `if fee_base <= 0: fee_base = amount_cents` fallback I added in 9b for legacy rows ALSO fired for free-trial venues where `commission_cents` is legitimately 0, re-triggering the same 10× overpay bug 9b was meant to fix. Now uses `is None` to detect truly-legacy NULL rows; real-zero stays zero.
    - **1099 history table never rendered** ([user-affiliate.js:737](app/static/js/user-affiliate.js#L737)) — `histRows.length` was reading from the API response object `{records, threshold_cents}`, not the records array. And the row field is `total_cents`, not `total_earned_cents`. Both fixed.

  - **Critical (pre-existing)**:
    - **Recommend email never tracked clicks** ([email_templates.py:1966](backend/email_templates.py#L1966)) — the `<a>` button was hardcoded to `https://gigsfill.com`; the `{{aff_url}}` variable was passed in but never substituted. Cookie attribution from emails was completely dead; sole survival was the email-match retroactive fallback at signup. Template now uses `{{aff_url}}`, and the URL routes through `/api/affiliate/track/{code}?redirect_to=/` so the recommend's `clicked` flag also fires.
    - **Open redirect on `/api/affiliate/track`** ([affiliate.py:120](backend/routes/affiliate.py#L120)) — `redirect_to` query param accepted any URL. New `_safe_internal_redirect()` enforces same-origin relative paths only (no `//`, no `scheme://`, must start with `/`, max 200 chars). Verified: `?redirect_to=https://evil.com` now coerces to `/`.
    - **Track endpoint unrate-limited** — added `@limiter.limit(RATE_AFF_TRACK)` (30/min/IP). Also added code-shape validation (`^[A-Z0-9-]{4,20}$`) before any DB query and kill-switch check.
    - **TIN "encryption" was XOR with a default key** ([tax.py:23](backend/routes/tax.py#L23)) — replaced with Fernet (AES-128-CBC + HMAC-SHA256, random IV per encryption). Legacy XOR blobs auto-detected by absence of the `gAAAAA` Fernet prefix and decrypted via the old routine; new writes use Fernet. Roundtripped both paths in tests.
    - **Affiliate 1099 generator built in-memory list and never persisted** ([tax.py:617](backend/routes/tax.py#L617)) — new `affiliate_tax_1099s` table (added in [db.py:1252](backend/db.py#L1252)) with `UNIQUE(affiliate_user_id, tax_year)`, `ON CONFLICT DO UPDATE` keeps "sent" status. Reads platform_name/EIN/address from `platform_settings`. User-side history endpoint now prefers persisted rows + falls back to live aggregation for pre-persistence years.

  - **High (security / abuse / sybil)**:
    - **Recommend email body XSS** — `sender_name`, `recipient_name`, `personal_note` interpolated raw into HTML. Now all three are `html.escape()`-d before substitution.
    - **Per-affiliate 24h cap on recommend emails** ([affiliate.py:223](backend/routes/affiliate.py#L223)) — per-IP `RATE_EMAIL_SEND` (10/min) alone let a determined user blast ~14k/day. New cap: 50 sends/24h per sender_user_id (sends + resends combined). Admin override via `platform_settings.affiliate_daily_send_cap`.
    - **Resend caps** — max 3 resends per recipient row, ≥24h between resends. Added `resend_count` + `last_resent_at` columns to `affiliate_recommend_emails`.
    - **Quarterly scheduler ran payouts AND reminder together** ([payout_scheduler.py:1031](backend/payout_scheduler.py#L1031)) — the reminder is for the manual "due today" prompt path; calling it AFTER auto-payouts ran left admin staring at "$0 eligible" and tempted to click "Run Quarterly Payouts Now" thinking nothing had happened. Reminder call removed; the auto-payout function already emails each affiliate individually.
    - **aff_code cookie persisted 90 days post-signup** ([auth.py:867](backend/routes/auth.py#L867)) — cross-attribution risk on shared computers. Now `response.delete_cookie("aff_code")` after any signup that processes affiliate logic.
    - **Sybil self-referral via two accounts** ([auth.py:813](backend/routes/auth.py#L813)) — A1 sends recommend to A2's future email, A2 signs up as venue, A1 earns commission forever. Added check: if affiliate's email == signup email, refuse the link.
    - **Admin manual_link silently overwrote existing referrals + reset linked_at** ([affiliate.py:1424](backend/routes/affiliate.py#L1424)) — `INSERT OR REPLACE` blew away `linked_at`, restarting the full-rate window. Now 409 with current_link payload unless `force: true` in body; even then UPDATEs (preserves linked_at) instead of REPLACE. Also blocks link if the affiliate owns the venue (self-referral check).
    - **delete_referral was silent to the affiliate** ([affiliate.py:1535](backend/routes/affiliate.py#L1535)) — venue would vanish from their dashboard with no explanation. Now creates an `affiliate_link_removed` in-app notification explaining that paid commissions are unaffected and N unpaid earnings were voided.

  - **Medium (correctness / display / admin)**:
    - **Linked Venues "Rate" column now shows LIVE rate** ([affiliate.py:548](backend/routes/affiliate.py#L548), [user-affiliate.js:464](app/static/js/user-affiliate.js#L464)) — `/api/affiliate/my-referrals` includes a `current_rate_percent` computed by `_current_rate()` (live admin settings); frontend prefers it over the row snapshot. Closes the user-visible half of the "everything flows through Admin settings" rule.
    - **Admin Affiliate Settings save now range-validates** ([affiliate.py:1313](backend/routes/affiliate.py#L1313)) — rate 0-50, days 0-36500, min/threshold 0-$100k. A typo of `15.0` instead of `1.5` previously quadrupled every affiliate's earnings live across the platform.
    - **Kill switch (`affiliate_enabled='false'`) now gates EVERY entry point**:
      - Click tracking ([affiliate.py:159](backend/routes/affiliate.py#L159)) — still redirects, sets no cookie, no DB writes.
      - Recommend send + resend — refuses with 403.
      - Signup-time linking ([auth.py:786](backend/routes/auth.py#L786)) — no ghost referral rows accumulate.
      - Previously accruals + payouts were the only paths checked.
    - **New `affiliate_daily_send_cap` platform setting** ([db.py:1750](backend/db.py#L1750), default 50).
    - **New `affiliate_tax_1099s` table** for persisted year-end 1099 records.

  - **Verified end-to-end**: open-redirect blocked (curl test against `?redirect_to=https://evil.com` returns `307 → /`), Fernet roundtrip works, legacy XOR decryption still works for backward compat, all 8 touched Python modules parse and import cleanly, services restarted clean.

- **2026-05-26 (part 9b) — Affiliate compliance + admin-settings-as-source-of-truth.** Five small but high-leverage UX/correctness fixes around the affiliate flow.
  - **Stripe-setup copy** ([user-profile.html:491](app/user-profile.html#L491), [user-affiliate.js:121](app/static/js/user-affiliate.js#L121)) — banner now reads "Please set up Stripe to receive your Affiliate payouts…" instead of the generic "Set up Stripe to receive payouts."
  - **Stuck "Loading…" after browser-back from Stripe** ([user-affiliate.js:594](app/static/js/user-affiliate.js#L594)) — clicking Connect Stripe → going to Stripe → hitting browser-back left the button frozen on "Loading…" because BFCache restored the page exactly as it was. Added `pageshow` listener that re-renders the Stripe block on `e.persisted = true` so the button resets cleanly.
  - **Affiliate onboarding checklist** — new `affiliate` entity type added to [onboarding.py](backend/routes/onboarding.py). Two mandatory tasks: `stripe_setup` (verifies `users.affiliate_stripe_connect_account_id` + `affiliate_stripe_connect_onboarding_complete=1`) and `w9_filed` (verifies a `w9_forms` row with `entity_type='user'` for the current tax year). The checklist only renders when at least one `affiliate_referrals` row points at the user — pre-referral users see nothing. New banner on [user-profile.html](app/user-profile.html) Affiliates tab shows pending steps, click-throughs scroll to and open the right sub-section. Tracks completed steps live (e.g. "1 of 2 steps complete"). [user-affiliate.js:`loadAffChecklist`](app/static/js/user-affiliate.js).
  - **Admin Affiliate Settings now flow live through every accrual** ([affiliate.py:_current_rate](backend/routes/affiliate.py)) — previously the rate, reduced rate, and reduction window were snapshotted onto each `affiliate_referrals` row at venue-signup time. Admin changing the platform rate in Admin → Affiliates → Affiliate Settings had NO effect on existing affiliates — they kept earning the rate that was active when their referral row was first written. Now `_current_rate` reads live values from `platform_settings` (`affiliate_rate_percent`, `affiliate_reduced_rate_percent`, `affiliate_reduced_after_days`) and only falls back to the row snapshot when the live setting is missing. This is the rule the user wants enforced across the affiliate program: "everything flows through the Admin settings."
  - The other two admin-settings reads (`affiliate_min_payout_cents`, `affiliate_enabled`) were already live-fetched on every call.

- **2026-05-26 (part 9) — Behavioral / state-sync audit (~35 fixes across 6 sweep agents focused on real-world correctness rather than code quality).** The May 26 Processing/Succeeded production incident (artist saw `transferred`/"Processing" for days after Stripe already paid them) revealed that earlier passes hadn't run behavioral lenses — only structural ones. Pass 9 was tightly scoped: "does the DB state actually match the real money state at Stripe, and do the numbers users see match the truth." 77 findings surfaced; everything actionable landed.

  - **P0 — Real money / state-sync**:
    - **`countersign_contract` charged wrong amount on multi-slot gigs** ([contracts.py:1555](backend/routes/contracts.py#L1555)) — for multi-slot gigs `gigs.pay` is `MAX(slot.pay)`. Using it meant every artist on a 2+ slot gig got billed at the highest slot's pay rate, not their own. Now sums `gig_slots.pay WHERE artist_id = :aid AND status = 'booked'` and falls back to `gigs.pay` only for single-slot.
    - **`booked_edit_gig` never recomputed fees** ([gigs.py:3050](backend/routes/gigs.py#L3050)) — venue could edit a booked slot's pay; the slot pay updated but the `artist_payout` child txn's `amount_cents` did not. Net: emails showed new pay, but charging fired on the old amount. Now syncs each booked child's `amount_cents` from `gig_slots.pay`, then calls `_recompute_gig_fees`. Logs loudly if parent has already moved past `scheduled/test` (money committed — recompute refuses by design).
    - **Partial refund could exceed un-transferred remainder** ([admin_payments.py:715](backend/routes/admin_payments.py#L715)) — admin could refund $80 of a $100 charge after $50 had already transferred to an artist, putting the platform $30 underwater. Now hard-caps partial refunds at `charge_cents - sum(transferred children)` and tells the admin to use Tier-3 reverse-transfer first if they need to refund more.
    - **`gig_cancelled_artists` table** (new in [db.py:1525](backend/db.py#L1525)) — `gigs.last_cancelled_artist_id` was a single int; on a multi-slot gig where 3 artists cancelled, only the most-recent canceller was excluded by the next blast. New join table tracks every cancellation. Helper `_record_artist_cancellation()` in [gigs.py:138](backend/routes/gigs.py#L138) writes both the legacy column and the new table. The 6 writer sites (gigs.py + contracts.py release path) now go through the helper; both blast queries ([gigs.py:5453](backend/routes/gigs.py#L5453), [5648](backend/routes/gigs.py#L5648)) added `AND a.id NOT IN (SELECT artist_id FROM gig_cancelled_artists ...)`. Existing rows backfilled from the legacy column on first restart.
    - **Affiliate accrual paid 10× too much** ([affiliate.py:604](backend/routes/affiliate.py#L604)) — `earned_cents = amount_cents * rate / 100`. `amount_cents` on the parent venue_charge is the SUM of artist pays, not platform commission. A 5% affiliate rate on a $100 gig earning gigsfill $10 commission was accruing $5 to the affiliate — 50% of platform revenue. Now uses `commission_cents` as the fee base (the actual platform take). Falls back to `amount_cents` on legacy rows that never populated commission.
    - **Refund/dispute didn't claw back affiliate earnings** — added `claw_back_affiliate_earnings()` ([affiliate.py:654](backend/routes/affiliate.py#L654)). Called from both the in-process admin refund ([admin_payments.py:828](backend/routes/admin_payments.py#L828)) and the `charge.refunded`/`charge.dispute.closed lost` webhooks ([stripe_connect.py:2316](backend/routes/stripe_connect.py#L2316), [2421](backend/routes/stripe_connect.py#L2421)). If earnings already paid out (payout_id is set), money is gone — admin alert fires for manual netting.
    - **Dispute-lost didn't claw back pending child payouts** ([stripe_connect.py:2417](backend/routes/stripe_connect.py#L2417)) — when the venue won a chargeback, the platform was about to pay artists out of money Stripe just took back. Now cancels `scheduled/pending_transfer/charge_retry/test` children on `dispute_lost`; already-transferred children still need Tier-3 reverse_transfer manually (admin alerted).

  - **P0 — Polling / external state sync**:
    - **Webhook dedup race re-fix** ([stripe_connect.py:1867](backend/routes/stripe_connect.py#L1867)) — part-6's "INSERT after handler" pattern fixed handler-crash silent-drops but left a race between two concurrent retries of the same event_id. Restructured to atomic `INSERT OR IGNORE` upfront: only the winning row's owner proceeds; on handler exception the owner DELETEs its row so Stripe's retry can re-process. The duplicate-loser short-circuits cleanly.
    - **Stale `transferred` (= "Processing" on artist UI) sweep** ([payout_scheduler.py:624](backend/payout_scheduler.py#L624)) — after the May 26 incident, added a sweep that alerts admin (rate-limited to 24h) when any artist_payout has been stuck in `transferred` for > 14 days. The dest_payment polling normally flips to `paid` within a day; >14d means something's wrong and admin needs to look.
    - **Reconcile endpoint blind to dest_payment.succeeded** ([admin_payments.py:2592](backend/routes/admin_payments.py#L2592)) — when our row says `transferred` and Stripe's `destination_payment` has succeeded, reconcile previously declared "OK" because the transfer itself wasn't reversed. Now flags as mismatch with a "should have advanced to paid" note.
    - **`_recompute_gig_fees` rebalance audit log** ([gigs.py:248](backend/routes/gigs.py#L248)) — multi-slot adds rebalance every existing child's commission/payout. Now logs the before/after per child so "why did my pay change" is greppable.

  - **P1 — Display correctness**:
    - **Artist earnings KPIs double-counted `transferred`** ([stripe_connect.py:2547](backend/routes/stripe_connect.py#L2547)) — `transferred` appeared in BOTH "total earned" (paid+transferred) AND "pending payout" (scheduled,charged,pending,pending_transfer,transferred,test). Every in-flight payout inflated both tiles. Removed from `pending_payout` since `transferred` now means "dest_payment succeeded on Stripe" (the artist sees Paid in their Express dashboard).
    - **Waitlist position counted declined rows** ([waitlist.py:106](backend/routes/waitlist.py#L106)) — `_get_position` and the inline subqueries used `COUNT(*) WHERE id <= :rid` without filtering `offer_declined=0`. An artist behind 3 declined rows showed up as #4. Fixed in 3 places.
    - **Public gigs partial multi-slot shown as fully booked** ([public-gigs.js:120](app/static/js/public-gigs.js#L120)) — `booked_slots_count > 0` mapped to "booked" (red). For multi-slot gigs with some slots still open, returns new `partial` class (amber). Added amber CSS in [public-gigs.html](app/public-gigs.html).
    - **`transfer_failed` rendered as innocuous "Processing"** ([artist-stripe-payment.js:153](app/static/js/artist-stripe-payment.js#L153)) — failure rows wore the same orange "Processing" pill as healthy in-flight rows. Artist couldn't tell their payout was actually broken. Now: `transfer_failed` → "Transfer Issue" (red), `payment_failed` → "Payment Issue" (red), `charge_retry` → "Retrying" (orange). Tooltips updated to tell the artist to contact support.
    - **Unread message badge counted cancelled-gig threads** ([messages.py:643](backend/routes/messages.py#L643)) — old conversations on cancelled gigs never get re-read but stuck around in the badge. Added `g.status NOT IN ('cancelled')` to the count query.

  - **P1 — Timezone unification**:
    - **`_is_same_day_booking` now takes `venue_id` and uses venue tz** ([gigs.py:610](backend/routes/gigs.py#L610)) — for venues far from platform tz, the 36h same-day-approval check was hours off. Both callers updated.
    - **Open-gig blast scheduler localizes per-venue** ([scheduler.py:550](backend/scheduler.py#L550)) — `gig_dt_naive.replace(tzinfo=tz)` used platform tz; now looks up each venue's tz via `get_venue_timezone_str`. A venue 3h ahead of platform no longer gets its "36h before" blast 3h early.
    - **Waitlist offer deadline formatted in venue tz** ([waitlist.py:61](backend/routes/waitlist.py#L61)) — `_format_deadline` now takes optional `venue_id` and renders in that venue's tz with the abbreviation suffix (`2:43 PM PST`). Three call sites updated.

  - **P1 — Notifications gaps**:
    - **In-app notification on `transferred → paid` transition** ([payout_scheduler.py:614](backend/payout_scheduler.py#L614)) — artist now sees a "Paid ✓" Activity Center entry when bank settles, not just a silent Payments-tab status flip. Fans out across artist's `entity_users`.
    - **Dispute opened fans out to venue + artist** ([stripe_connect.py:2014](backend/routes/stripe_connect.py#L2014)) — both parties get an in-app notification; previously only admin was told.
    - **`affiliate_quarterly` template added** ([email_templates.py:960](backend/email_templates.py#L960)) — referenced by the AttributeError fallback in `_send_quarterly_affiliate_email` but never existed. Now a real fallback template that renders if the inline-build path fails.

  - **P2 — Scheduler reliability + plumbing**:
    - **`_get_quarter` boundary fix** ([affiliate.py:49](backend/routes/affiliate.py#L49)) — used `utcnow_naive()`, so 11:30 PM PT on Dec 31 returned `2027-Q1` even though the platform was still in Q4. Now computes in platform-local tz.
    - **Audit-table pruning sweep** ([scheduler.py:1296](backend/scheduler.py#L1296)) — new `_run_audit_table_prune()` runs hourly: `stripe_webhook_events` >90d, `pending_approval_tokens` >30d, `gig_email_log` >180d, `admin_audit_log` >365d. None of these are needed for current-state decisions past their TTL; they were just growing forever.

  - **Total**: ~35 fixes shipped in pass 9. Major behavioral wins are: multi-slot now charges the right amount (countersign), edits actually recompute fees, affiliate commission is no longer 10× overpay, refund/dispute paths claw back affiliate earnings, dispute-lost cancels pending payouts, blast/same-day/waitlist all use venue tz, webhook dedup is race-free, and the May 26 Processing-stuck pattern now has both prevention (poll updated last week) and detection (14-day sweep + reconcile mismatch flag).

- **2026-05-26 (part 8) — Sixth-audit punch list (~70 fixes from ~470 findings across 6 exhaustive sweep agents).** Six agents ran category-focused sweeps (inline-onclick XSS, Postgres-compat, race conditions, state-machine drift, raw fetch + innerHTML, corner cases). Fixed every SQLite-affecting finding; deferred the 187 PG-only patterns since production runs on SQLite.

  - **P0 — Critical correctness**:
    - **`payout_scheduler.process_payouts_now` NameError on `tz`** ([payout_scheduler.py:91](backend/payout_scheduler.py#L91)) — `tz` was referenced at 3 call sites inside the function but only ever set in `scheduler_loop()`. Every card decline / no-card-on-file / Stripe exception hit `NameError: tz`, swallowed by the outer except as a generic warning. **Charge retries were silently broken in production.** Defined locally. Single highest-impact production bug across all 9 audit passes.
    - **`_handle_charge_failure` `retry_at` written in local tz** ([payout_scheduler.py:847](backend/payout_scheduler.py#L847)) — scheduler SELECT compares as UTC. Retries fired 3-8h early depending on platform tz offset.
    - **`/api/venues/{venue_id}/batch-blast` unthrottled** ([gigs.py:5654](backend/routes/gigs.py#L5654)) — fans out hundreds of emails per call. Added `@limiter.limit("4/minute")`. Also rate-limited `/new-gig-blast`.
    - **State-machine drift in venue cancel paths** — `cancel_gig` "other booked slots" guard ([gigs.py:2371](backend/routes/gigs.py#L2371)) and `delete_gig_with_slots` "snapshot booked" ([gigs.py:4832](backend/routes/gigs.py#L4832)) both filtered only `status='booked'`. Artists mid-contract (pending_contract / awaiting_venue_contract / pending_venue_approval) got NO cancellation email, NO notification, NO transaction cleanup when venue cancelled. Silent contract abandonment + orphan money rows. Both now include all in-flight states.
    - **6 booked_slots_count subqueries across gigs.py** missed in-flight states → UI mislabeled multi-slot fullness ("1 of 3 booked" when really 3 of 3 committed). Bulk-updated.
    - **3 frequency-between-performances queries** ([gigs.py:1591, 1885, 3902](backend/routes/gigs.py)) only checked `status='booked'`. An artist with a contract-pending booking could bypass the venue's freq policy. All extended.
    - **`accrue_affiliate_earnings` double-payout race** ([affiliate.py:600+](backend/routes/affiliate.py#L600)) — SELECT-then-INSERT pattern racy under concurrent caller; two parallel calls both passed the "don't double-accrue" gate. Added `UNIQUE INDEX` on `transaction_id` + race-loser catch.
    - **`create_venue_contract` two-active-rows race** ([contracts.py:476](backend/routes/contracts.py#L476)) + **`update_venue_contract`** ([contracts.py:560](backend/routes/contracts.py#L560)) — two concurrent activations both ran DEACTIVATE-then-INSERT, leaving two active rows; downstream booking flow non-deterministically picked one. Added post-write race-loser deactivation.
    - **`_cleanup_expired_holds_impl` UPDATE without status guard** ([contracts.py:2854](backend/routes/contracts.py#L2854)) — if a venue countersigned mid-sweep, this regressed the contract from `fully_signed` to `expired`, blanking the booking from the artist's UI. Added atomic claim guard `AND status IN ('pending', 'awaiting_venue_upload', 'artist_signed')`.
    - **`venue_address_link` missing from `_HTML_SAFE_KEYS` allowlist** ([email_service.py:194](backend/email_service.py#L194)) — `email_dispatch` built it as pre-rendered HTML; without allowlist, ~8 templates showed literal `<a href="...">address</a>` text instead of clickable link.
    - **me.py status coverage extended to `pending_venue_approval`** — 8 sites in `delete-preview` / `delete_account` previously only handled 3 in-flight states.
    - **SVG flyer upload + size cap + magic bytes** ([flyers.py:294](backend/routes/flyers.py#L294)) — SVG removed from allowlist (could contain `<script>` tags, same-origin XSS when served from `/app/static/uploads/`). Added 10 MB size cap + magic-byte verification.
    - **PRO license upload validation** ([venues.py:1008](backend/routes/venues.py#L1008)) — added PRO-name allowlist (was used in filename → path traversal risk), extension whitelist (`.pdf` only), 10 MB cap, `%PDF-` magic-byte check.
    - **`venue-email-center.js:802` raw `${email.body}` rendered as HTML** — moved into a sandboxed iframe (`sandbox="allow-same-origin"`) so any script tags or event handlers in the venue's saved email body can't execute when viewing history.
    - **Frontend XSS systemic fix** — added `jsAttr()` helper to `security.js`. The recurring sink across the codebase was `onclick="someFn('${esc(name)}')"` — `esc()` encodes `'` to `&#39;`, but the HTML attribute parser decodes that back to `'` BEFORE JS parses the string, so the apostrophe still breaks out. `jsAttr()` uses `JSON.stringify()` (handles `\`, `'`, `"`, control chars, `</script>`) + HTML-attribute escape. Migrated 15+ sites: `gig-modal.js` (4), `entity-users.js` (3), `venue.create-gigs.js` (Slot card + Search-Gigs autocomplete), `artist.book-gigs.js` (preferred-venues lists + venue autocomplete), `my-venues-redesign.js` (3), `venue.contracts.js` (contract list + PDF status), `artist-book-gigs-init.js` (contract list), `venue-email-center.js` (artist list + history rows), `venue-stripe-payment.js` (billing list + print export), `artist-stripe-payment.js` (earnings list + print export), `admin-affiliate.js` (venue search), `artist-availability.js` (editBlackout).

  - **P0 — Stripe orphan-account races** — added idempotency keys to 3 places: artist Connect (`stripe_connect.py:340`), venue Customer (`stripe_connect.py:114`), affiliate Connect (`affiliate.py:494`). Two concurrent clicks no longer create orphan Stripe accounts.

  - **P1 — State-machine drift continued**:
    - `notify_gig_edited` recipient lookup ([notification_service.py:160](backend/services/notification_service.py#L160)) only joined `status='booked'`; artists mid-contract didn't get the edited-gig notification. Extended.
    - `_notify_other_party` in messages.py (twice) used `status='booked'` only for slot lookup — venue couldn't resolve artist email when messaging during contract flow. Extended.

  - **Total**: ~70 fixes shipped in this pass. The Postgres-compat findings (~187 patterns: `datetime('now')`, `INSERT OR IGNORE`, `last_insert_rowid()`, `PRAGMA`, etc.) are documented but deferred — production runs on SQLite and these are all latent future-state. The frontend XSS systemic fix is the most architecturally important change: a centralized `jsAttr()` helper now exists for any future inline-onclick string args.

- **2026-05-26 (part 7) — Fifth-audit punch list (~40 fixes: 4 P0 + 19 P1 + ~17 P2).** Six-agent fifth-pass sweep caught XSS via inline-onclick string escaping on multiple pages (the new pattern: even with HTML escape, raw apostrophe/backslash escape on JS-string-in-attr args is exploitable), a critical multi-slot hold-tracking bug (B's slot stuck forever when C signs later), broken signup collision links, missing entity_users fan-out on contract states, and a webhook idempotency ordering bug. All landed.

  - **P0 — XSS + critical correctness**:
    - **`public-gigs.js` search autocomplete XSS** ([public-gigs.js:828, 880](app/static/js/public-gigs.js#L828)) — `onclick="selectVenue('${venue.replace(/'/g, "\\'")}')"` only escaped apostrophes. A venue name like `Foo\'); alert(1);//` would inject JS. Rebuilt as DOM nodes with `addEventListener`.
    - **`my-artists.js` raw interpolation across 12 sinks** ([my-artists.js](app/static/js/my-artists.js)) — file had ZERO escape helpers. Artist names, cities, states, gig titles all interpolated raw. Added `_ma_esc` (HTML) + `_ma_attr` (JS-string-in-HTML-attr) helpers; coerced all IDs to int before href interpolation.
    - **`my-venues-redesign.js` + `venue.create-gigs.js` ban-search rows same pattern** ([my-venues-redesign.js:357](app/static/js/my-venues-redesign.js#L357), [venue.create-gigs.js:4676](app/static/js/venue.create-gigs.js#L4676)) — same fix.
    - **Contracts `artist_signed` rows orphaned by expiry sweep** ([contracts.py:2858](backend/routes/contracts.py#L2858)) — after part-5/6 the digital sign path leaves contract in `artist_signed`. The expiry sweep only marked `('pending','awaiting_venue_upload')` as expired, so when the slot was released the contract row sat in `artist_signed` forever with a stale `hold_expires_at` — artist could still download a "signed" PDF and re-book attempts collided. Added `'artist_signed'` to the IN clause.

  - **P1 — Multi-slot hold tracking refactor** ([contracts.py:_cleanup_expired_holds_impl](backend/routes/contracts.py)) — the sweep used the parent gig's single `(contract_hold_artist_id, contract_hold_expires_at)` pair. When two artists signed different slots at different times, the second overwrote the parent column → first artist's slot stayed stuck forever. Rewrote sweep to iterate per `gig_contracts` row (each artist has their own `hold_expires_at`). Also fixed `countersign_contract` so its multi-slot branch no longer NULLs the parent hold tracker when other artists are still awaiting countersign.

  - **P1 — `pending_approval_tokens` cleanup on cancel paths** ([gigs.py:2455](backend/routes/gigs.py#L2455), [gigs.py:2718](backend/routes/gigs.py#L2718), [gigs.py:4983](backend/routes/gigs.py#L4983)) — three cancel paths now DELETE the token rows so future email-link clicks land on the friendly "Already Cancelled" page instead of "no pending approval" + 404.

  - **P1 — `charge.refunded` widen child-cancel filter** ([stripe_connect.py:2293](backend/routes/stripe_connect.py#L2293)) — children stuck in `pending_transfer` / `charge_retry` / `test` on a full-refunded parent now also cancel.

  - **P1 — `api-globals.js` write timeout 90s + abort-message** ([api-globals.js:80](app/static/js/api-globals.js#L80)) — GET keeps 45s, POST/PUT/DELETE use 90s (Stripe round-trips + PDF render + email blasts can exceed 45s). Abort message now warns "the operation may still have completed on the server — refresh before retrying."

  - **P1 — `_create_booking_notifications` entity_users fan-out across ALL states** ([contracts.py:_create_booking_notifications](backend/routes/contracts.py)) — previously only the `'booked'` branch fanned out via `get_all_entity_users`; the `pending_contract`, `awaiting_venue_contract`, `artist_signed`, `fully_signed` branches only notified the primary `venues.user_id` / `artists.user_id`. Multi-user accounts missed every contract-flow step. Refactored with `_ins_venue` / `_ins_artist` helpers used by every branch.

  - **P1 — `book_with_contract` digital slot status alignment** ([contracts.py:2339](backend/routes/contracts.py#L2339)) — `_apply_slot_booking` left slot at `pending_contract` but the contract row was created at `artist_signed`. Now flips slot to `awaiting_venue_contract` post-INSERT to match the part-5 invariant (slot=awaiting_venue_contract pairs with contract=artist_signed).

  - **P1 — Signup-collision email links pointed at nonexistent pages** ([auth.py:430](backend/routes/auth.py#L430)) — `/login.html` and `/forgot-password.html` don't exist; recipients clicked through to 404. Now point to `/app/index.html` (which hosts both flows).

  - **P1 — `delete_account` handles `awaiting_venue_contract` + `pending_contract`** ([me.py:481](backend/routes/me.py#L481), [me.py:496](backend/routes/me.py#L496), [me.py:570](backend/routes/me.py#L570), [me.py:574](backend/routes/me.py#L574), [me.py:587](backend/routes/me.py#L587)) — `delete-preview` warning, booked-gig discovery, and the status-reset all now include the new part-5/6 contract-flow states. Without this an account deletion left gigs stuck in `awaiting_venue_contract` with the now-deleted entity referenced.

  - **P1 — `slots_html` on `_HTML_SAFE_KEYS` allowlist** ([email_service.py:194](backend/email_service.py#L194)) — contract-sign dispatcher uses `slots_html` (vs older `slot_times_html`); without allowlist the render_template HTML-escape turned the slot table into literal markup in email.

  - **P1 — `_add_columns` catches `Exception` (PG DuplicateColumn)** ([db.py:1739](backend/db.py#L1739)) — previous `sqlite3.OperationalError`-only catch crashed every PG re-deploy. Also rolls back the cursor's transaction so subsequent statements aren't poisoned.

  - **P1 — Affiliate `INSERT OR IGNORE` branched on `_IS_POSTGRES`** ([affiliate.py:805](backend/routes/affiliate.py#L805)) — SQLite-only syntax used `ON CONFLICT (...) DO NOTHING` on PG.

  - **P1 — Vanity city-page `</script>` injection** ([vanity.py:266](backend/routes/vanity.py#L266)) — `json.dumps()` doesn't escape `</script>`. A venue with `city = "</script><script>alert(1)//"` would execute JS in every city-page visitor's browser. Now post-processes JSON output with `.replace("</", "<\\/")`.

  - **P1 — Admin-payments filter chip XSS** ([admin-payments.js:274](app/static/js/admin-payments.js#L274)) — `venueSafe` only escaped apostrophes, missed backslash → trailing `\` in a name would escape the closing apostrophe and let attacker JS follow. Added backslash escape + int-cast for `venue_id`/`artist_id`.

  - **P1 — Notification renderers** — `messages.js` inbox-row onclick (escape apostrophe + backslash + coerce id to int); `venue.create-gigs.js` conflict modal raw `existing_title`/`existing_times` interpolation (now `_vcg_esc`); `update_recurring_gigs` UI now surfaces `skipped_inflight` count to the venue.

  - **P2 — Polish (~17 items)**:
    - `PUT /api/me` normalizes phone to `(XXX) XXX-XXXX` (matches signup); was accepting any garbage.
    - `DELETE /api/me/delete` rate-limited `3/hour` so a double-click doesn't fire concurrent cascades.
    - Signup endpoint validates email format inline (was bypassing `SignupRequest`'s `EmailStr`).
    - `upload_signed_pdf` unlinks the prior signed PDF on re-upload (suffix-incrementing was orphaning old files).
    - `cancel_slot` marks gig_contracts rows as `'cancelled'` before cleanup_gig_records deletes them (auditable trail).
    - Admin refund modal auto-reloads + re-opens detail on 409 ("status changed during this request").
    - `GIGSFILL_RUN_SCHEDULERS` env-parse aligned: both `main.py` and `scheduler_main.py` accept `("1","true","yes")`. Previously `=true` triggered schedulers in API workers AND refused to start the scheduler service.
    - `update_settings` race fix: INSERT now wraps the unique-constraint loser in a fallback UPDATE so concurrent admin PUTs don't 500 on the new-key race.
    - `update_support_ticket` validates status against `('open','pending','closed')` enum.
    - `auth.guard.js` 12s timeout fallback: if `/api/me` hangs (offline / broken backend), the protected page no longer stays invisible forever — falls through to the login redirect.
    - `flyer-editor.js` feModal: title + message rendered via existing `esc()` helper so venue-set template names can't inject HTML into the delete-confirm modal.
    - Cache busters bumped: `api-globals.js?v=4`, `auth.guard.js?v=2`, `public-gigs.js?v=4`, `admin-payments.js?v=25`, `flyer-editor.js?v=17`, `my-venues-redesign.js?v=8`, `messages.js?v=5`, `my-artists.js?v=4`, `venue.create-gigs.js?v=112`, `activity-center.js?v=101/?v=3`, `notifications-all.js?v=2`, `admin-templates.js?v=2`.

- **2026-05-26 (part 6) — Fourth-audit punch list (~35 fixes: 5 P0 + 13 P1 + ~17 P2).** Sixth six-agent sweep caught regressions from the part-5 `awaiting_venue_contract` flow change PLUS a wide Postgres-compatibility surface that all prior audits had treated as latent. All landed.

  - **P0 — Part-5 flow-change regressions + critical security**:
    - **`awaiting_venue_contract` missing from artist "My Gigs" calendar query** ([gigs.py:3582](backend/routes/gigs.py#L3582)) — a multi-slot artist who electronic-signed saw their gig DISAPPEAR until countersign. Added the new state to the IN clause.
    - **Same state missing from radius-blast / preferred-artist candidate filter** ([gigs.py](backend/routes/gigs.py)) — an artist mid-sign was re-emailed as a candidate for another slot on the same gig. Filter now excludes `('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')`.
    - **`contract_hold_expires_at = NULL` on sign/upload permanently locked the slot** ([contracts.py:986](backend/routes/contracts.py#L986), [contracts.py:1690](backend/routes/contracts.py#L1690)) — `_cleanup_expired_holds_impl` keys off `< now` (NULL never matches). If the venue never countersigned, the slot stayed `awaiting_venue_contract` forever. Now sets a fresh 48h deadline.
    - **`account.updated` Stripe webhook ignored affiliate Connect accounts** ([stripe_connect.py:2169](backend/routes/stripe_connect.py#L2169)) — restricted affiliate accounts kept `affiliate_stripe_connect_onboarding_complete=1`; next quarterly sweep fired Transfer.create against dead destinations. Added affiliate branch with admin alert + reconnect email.
    - **Stored XSS in notification rendering** ([activity-center.js](app/static/js/activity-center.js), [notifications-all.js](app/static/js/notifications-all.js)) — `notification.title`, `.message`, `.venue_name`, `.artist_name`, `.gig_title`, `.cancellation_reason` were interpolated raw into `innerHTML` across notification panels and modals. A venue named `<img onerror=...>` would execute JS in every artist's session that viewed a notification involving them. Added `_ac_esc` / `_na_esc` helpers, fixed `linkify` to escape-then-substitute, coerced IDs to int before href interpolation. Cache busters bumped (`activity-center.js?v=101` / `?v=3`, `notifications-all.js?v=2`).

  - **P1 — `awaiting_venue_contract` IN-clause sweep across 5 files** ([waitlist.py:166](backend/routes/waitlist.py#L166), [messages.py:287](backend/routes/messages.py#L287), [gig_modal.py:337](backend/routes/gig_modal.py#L337), [notification_service.py:107](backend/services/notification_service.py#L107), [email_dispatch.py:217](backend/services/email_dispatch.py#L217)) — every query that filters by slot-active state now also includes `awaiting_venue_contract` + `pending_venue_approval`. Was breaking: waitlist-join check, messages-tab "include in conversation" join, conflicting-slot detection in modal, slot-number lookup in notification helper, booked-slot enumeration in email dispatch.

  - **P1 — Postgres compatibility (six SQLite-isms that 500'd on PG)**:
    - `email_verified` and `signup_collision_last_at` columns now declared in `db.py:setup_database()` via `_add_columns(cursor, "users", [...])` ([db.py:220](backend/db.py#L220)) — previously only lazy SQLite-PRAGMA paths added them, so email-change PUT, verify-email landing, and the collision throttle all 500'd on PG.
    - `pending_approval_tokens` + `stripe_webhook_events` tables now declared in `setup_database()` ([db.py:1709](backend/db.py#L1709)) — previously lazy creates in route handlers that ran SQLite-only PRAGMA introspection first.
    - `_ensure_email_verified_column`, `_ensure_sms_carrier_column`, `_ensure_approval_columns` now branch on `_IS_POSTGRES` and use `information_schema.columns` on PG.
    - `_export_email_templates_to_disk` generated `run_migration()` body now branches on `_IS_POSTGRES` for column-introspection ([admin.py:781](backend/routes/admin.py#L781)) — PG deploys silently lost admin template edits on every restart.
    - Password reset `DELETE FROM used_reset_tokens WHERE used_at < datetime('now', '-2 hours')` ([auth.py:1240](backend/routes/auth.py#L1240)) — SQLite-only `datetime('now', ...)` aborted the transaction on PG; the catch logged but the follow-up `db.commit()` failed → user's new password silently never persisted. Now computes the cutoff in Python and binds as a param.
    - `GET /api/me/delete-preview` `date('now')` ([me.py:384](backend/routes/me.py#L384)) — same pattern. Delete-account modal 500'd on PG. Now binds a Python date string.

  - **P1 — Stripe webhook idempotency ORDERING fix** ([stripe_connect.py:1842, 2418](backend/routes/stripe_connect.py#L1842)) — the part-5 dedup INSERT happened BEFORE the handler ran, so a handler crash mid-flight let Stripe's retry short-circuit as "duplicate" and the event was permanently dropped (venue never suspended, admin never alerted). Now the up-front block only CHECKS for an existing row; the INSERT moved AFTER the handler block completes, with `INSERT OR IGNORE` for safety.

  - **P1 — Admin bypass on contract endpoints** ([contracts.py:813](backend/routes/contracts.py#L813), [contracts.py:3428](backend/routes/contracts.py#L3428)) — `get_gig_contract` and `download_contract_pdf` lacked the admin escape that `get_gig_contract_by_gig` (part-5) and `get_contract_preview` (part-5) have. Admins doing support/audit got 403. Added `to_admin_bool(user.is_admin)` as third allowed condition.

  - **P1 — `delete_referral` orphan affiliate_earnings** ([affiliate.py:1164](backend/routes/affiliate.py#L1164)) — admin-revoked referrals left unpaid earnings rows; next quarterly sweep summed them via `WHERE ae.payout_id IS NULL` and paid out the affiliate anyway. Now drops earnings in the same transaction and returns `earnings_voided` count.

  - **P1 — `assert_no_charged_transactions` gig-scope on multi-slot delete** ([gigs.py:2433](backend/routes/gigs.py#L2433)) — the venue-cancel "delete entirely" branch only asserted at artist-scope (which excludes the multi-slot parent venue_charge with artist_id=NULL). Charged parent rows could slip past and be wiped along with the audit trail. Added gig-scope re-assert before `delete_gig_completely`.

  - **P1 — Frontend XSS + UX**:
    - `activity-center.js` + `notifications-all.js` notification XSS (P0 above, listed here for completeness).
    - `auth.guard.js` flash protection — added inline pre-paint `document.documentElement.style.visibility = 'hidden'` script to the `<head>` of all 9 protected pages (artist-book-gigs.html, artist-edit.html, contract-sign.html, notifications-all.html, user-profile.html, venue-discovery.html, venue-email-center.html, venue-edit.html, venue-create-gigs.html). Without this, unauthenticated visitors saw protected DOM flash before the redirect lands. admin.html already had the pattern.
    - `api-globals.js` — added 45s `AbortController` timeout and `_parseBodyOr204` helper for empty 204/no-content responses ([api-globals.js:80](app/static/js/api-globals.js#L80)). A hung backend used to leave "Saving…" buttons stuck forever. Cache buster bumped to `?v=3` across all 6 pages.
    - `admin-templates.js` cache buster — added `?v=2`; was loaded without a version, cached admins missed today's UI fixes.

  - **P2 — Polish (~17)**:
    - `update_recurring_gigs` returns `skipped_inflight` count so the venue UI can warn "we preserved N in-flight gigs" instead of pretending the series was rebuilt cleanly.
    - `RESERVED_SLUGS` extended with `track`, `stats`, `r`, `ref`, `go`, `verify-email`, `reset-password`, `forgot-password`, `unsubscribe`, `webhook(s)` — affiliate / analytics / system routes that vanity slugs would otherwise shadow.
    - `sw.js CACHE_NAME` bumped `gigsfill-v5` → `gigsfill-v6` so PWA users with stale SW pick up the new splash.
    - `delete_gig` switched to canonical `assert_no_charged_transactions` instead of its own inline tuple — keeps dispute/processing statuses in sync.
    - Waitlist-error banner now HTML-escapes the message before `innerHTML` interpolation.
    - Add-video error in `artist.edit.js` + `venue.edit.js` now surfaces `{detail}` in a styled modal instead of `console.error`-and-silent.
    - `accept_invitation_existing_user` now requires `get_current_user` AND verifies `user.email == invitation.invited_email` — previously anyone with the token could bind the entity to the legitimate account without consent.

  - **Other**:
    - All Python files re-chowned to `www-data` after edits to keep email-template auto-export working.

- **2026-05-25 (part 5) — Third-audit punch list (~44 fixes: 9 P0 + 23 P1 + ~12 P2).** Sixth agent sweep surfaced critical security gaps the prior two audits missed: unauth contract PII reads, an unauth support-ticket impersonation endpoint, stored XSS on public profiles, Stripe idempotency entirely defeated, and a webhook gap that paid artists on refunded charges. All landed in one continuous pass.

  - **P0 — Security / impersonation / PII leaks**:
    - **`accept_invitation` accepted arbitrary body email** ([entity_users.py:818](backend/routes/entity_users.py#L818)) — anyone with a leaked invitation token could sign up with their own email and silently get member access to the invited entity. Now the body email is ignored; we bind to `invitation["invited_email"]`. Added 14-day token expiry (`_check_invitation_fresh`) and entity-existence check (`_verify_entity_exists`) on all three accept/decline endpoints, plus `@limiter.limit("10/hour")` on each.
    - **`GET /api/gigs/{gig_id}/contract` had no auth** ([contracts.py:821](backend/routes/contracts.py#L821)) — any logged-in user could read any contract's body, signature names, IPs, and signed PDF paths. Now gated through `check_venue_access` / `check_artist_access` (admin bypass via `to_admin_bool`).
    - **`GET /api/gigs/{gig_id}/contract-preview` had no auth** ([contracts.py:2479](backend/routes/contracts.py#L2479)) — anyone could pass any `artist_id` and receive that artist's email/phone/city/state interpolated into the preview. Same auth gate added.
    - **`POST /api/support/ticket` was unauthenticated + trusted body identity** ([main.py:541](backend/main.py#L541)) — anonymous callers could file tickets attributed to any `user_id` and direct admin replies anywhere. Now requires `get_current_user` and derives identity from the session, ignoring body fields.
    - **`main.py:585` `AttributeError` swallowed every support ticket email** — `__import__('datetime').utcnow_naive()` raised (the helper lives in `backend.utils`), the surrounding bare `except` swallowed it, and confirmation/admin notification emails silently never sent. Now imports `utcnow_naive` correctly.
    - **`_idem()` defeated Stripe idempotency** ([admin_payments.py:53](backend/routes/admin_payments.py#L53)) — the prior implementation appended `uuid.uuid4().hex` to every key, so admin double-clicks generated TWO distinct keys, both passed Stripe's no-duplicate check, and both refund/reversal calls succeeded. Now deterministic: the key IS the prefix. All five callsites updated to encode `_amt{cents}` so partial refunds at different amounts still get distinct keys.
    - **`charge.refunded` webhook left scheduled child payouts intact** ([stripe_connect.py:2134](backend/routes/stripe_connect.py#L2134)) — when a venue refunded a charge via the Stripe Dashboard (not via our admin console), the next hourly sweep would still transfer artist payouts on the refunded charge. Now on full refund we cancel still-scheduled children, mirroring `admin_payments.refund_payment`.
    - **Stored XSS on public venue profile** ([venue-profile.html:380-407](app/venue-profile.html#L380)) — 8 venue-controlled fields (setup_location_description, sound_equipment_description, lighting_description, sound_engineer_details, bar_tab_details, food_tab_details, venue_size, load_in_out_details) were interpolated into `innerHTML` without escaping. A venue typing `<script>` or `<img onerror>` would execute on every visitor's browser. All fields now pass through `esc()`.
    - **Attribute-injection XSS via social URLs** ([artist-profile.html:379](app/artist-profile.html#L379), [venue-profile.html:427](app/venue-profile.html#L427)) — saved URLs were interpolated into `href` raw. A value like `" onmouseover="alert(1)` broke out and ran JS. URLs are now `escAttr()`d and non-http(s) schemes are rejected before the link is rendered.

  - **P1 — Booking pipeline races & state**:
    - **`_create_booking_transaction` double-charge race** ([gigs.py:381](backend/routes/gigs.py#L381)) — two concurrent slot bookings on the same multi-slot gig could both pass `existing_charge` SELECT and both INSERT a parent `venue_charge`. Now post-INSERT we re-SELECT for all venue_charge parents on the gig, keep the lowest id, and delete our insert if we lost the race. Logs `[BOOKING_RACE]` warning so we can spot the contention.
    - **`send_approval_request_emails` single shared token** ([email_dispatch.py:925](backend/services/email_dispatch.py#L925)) — `gigs.approval_token` was overwritten on every same-day request, killing prior pending artists' email links. New `pending_approval_tokens` table stores per-(gig, artist) tokens; `approve_booking` / `deny_booking` look them up first, falling back to the legacy column for in-flight pre-deploy emails ([gigs.py:4100, 4324](backend/routes/gigs.py#L4100)).
    - **`book_with_contract` blocked other artists on multi-slot** ([contracts.py:2153](backend/routes/contracts.py#L2153)) — same-day approval branch unconditionally set parent gig to `pending_venue_approval`, blocking other artists from still-open slots. Now mirrors `book_slot`: only flip parent when no other open slots remain.
    - **`book_with_contract` missing one-slot-per-artist guard** ([contracts.py:2120](backend/routes/contracts.py#L2120)) — entity_user with access to multiple bands could book multiple slots of the same multi-slot gig via the contract path, breaking the payout invariant. Mirrors the existing guard in `book_slot`.
    - **`update_recurring_gigs` destroyed in-flight contracts** ([gigs.py:3217](backend/routes/gigs.py#L3217)) — the extras-deletion loop filtered only on `status != 'booked'`, silently destroying `pending_contract`, `awaiting_venue_contract`, and `pending_venue_approval` gigs plus their contracts/transactions. Now whitelisted: only `open` or `cancelled` parents AND no in-flight slots. Counts skipped in-flight gigs separately.
    - **`delete_gig` SQL bind-param typo** ([gigs.py:2683](backend/routes/gigs.py#L2683)) — placeholder was `:gid` but dict key was `gig_id`, so SQLAlchemy raised, the surrounding bare `except: pass` swallowed it, and `notification_sent_log` was never cleaned. Stale rows suppressed emails when a gig was re-created with the same id. Fixed.
    - **`assert_no_charged_transactions` missed dispute/processing** ([gig_cleanup.py:35](backend/services/gig_cleanup.py#L35), [me.py:415](backend/routes/me.py#L415)) — extended the tuple to include `disputed`, `dispute_won`, `dispute_lost`, `processing`. A gig with an open chargeback or in-flight PI would otherwise pass the assert and the dispute audit trail would vanish.
    - **`sign_contract` created payment before countersign** ([contracts.py:962](backend/routes/contracts.py#L962)) — electronic-contract artist-sign path flipped slots directly to `booked` and created the venue charge. If the venue never countersigned, the scheduler still charged the venue on payout day. Now slots transition to `awaiting_venue_contract` (matching the PDF path), and `countersign_contract` is the single place that flips to `booked` + creates the venue charge + sends booking emails. `countersign_contract` IN-clause widened to include the new `awaiting_venue_contract` state.

  - **P1 — Payments / webhooks / atomicity**:
    - **Stripe webhook event_id idempotency** ([stripe_connect.py:1829](backend/routes/stripe_connect.py#L1829)) — Stripe redeliveries used to re-run the full handler (re-suspend venues, re-alert admin, re-zero artist Connect flags). New `stripe_webhook_events` table dedupes by `event.id`; duplicate deliveries return `{received: true, duplicate: true}` and skip processing.
    - **`bulk_action` per-row rate-limit death** ([admin_payments.py:2358](backend/routes/admin_payments.py#L2358)) — `mark_resolved` / `refire_payment` / `resend_email` are `@limiter.limit(30/minute)` decorated; calling them in a 100-row bulk loop exhausted the budget at row ~30 with 429. Now uses `inspect.unwrap()` to bypass the slowapi decorator on inner calls; only the bulk endpoint itself burns a rate slot.
    - **`refund_payment` / `reverse_transfer` lacked status re-read** ([admin_payments.py:741, 1299](backend/routes/admin_payments.py#L741)) — two admins clicking different refund/reversal amounts could both pass the Python-side status check and both fire Stripe (since `_idem` now uses `_amt{N}` keys). Added a status re-read immediately before the Stripe call; second admin sees the row already moved and gets a 409.

  - **P1 — Auth / authz / orphan cleanup**:
    - **`delete_account` orphan entity_invitations** ([me.py:632](backend/routes/me.py#L632)) — pending invitations sent by the deleted user used to leave dangling `invited_by_user_id` rows; the entity-user management UI rendered ghost names. Now cleaned in Step 3.
    - **`_PROTECTED_TABLES` extended** ([admin.py:2356-2370](backend/routes/admin.py#L2356)) — added `admin_audit_log` (so admins can't wipe their trail), `entity_users` + `entity_invitations` (force the permission-checking endpoints), `vanity_urls`, `email_templates` (the auto-export-to-disk hook only fires on the proper PUT route), and the new `stripe_webhook_events` + `pending_approval_tokens` idempotency tables.
    - **`_export_email_templates_to_disk` corrupted bodies** ([admin.py:691](backend/routes/admin.py#L691)) — hand-rolled quote escape failed on bodies ending in `'` (produced four consecutive quotes → SyntaxError on next deploy) and silently mangled `'''` to `' ' '`. Now uses `repr()` which handles all escapes deterministically.
    - **`vanity_urls` never cleaned on entity delete** — added cascade deletes in `me.py` self-delete (both artist + venue branches), [artists.py:633](backend/routes/artists.py#L633), [venues.py:813](backend/routes/venues.py#L813), plus a defense-in-depth check in `resolve_vanity` ([vanity.py:587](backend/routes/vanity.py#L587)) that lazy-deletes orphan rows pointing at non-existent entities.
    - **Contract PDFs never unlinked on gig delete** ([gigs.py:3463](backend/routes/gigs.py#L3463), [gigs.py:4918](backend/routes/gigs.py#L4918)) — `gig_contracts` rows were deleted but the actual signed PDFs on disk persisted forever, growing unbounded and remaining world-readable. Now `os.remove` runs on every `signed_pdf_path` before the row delete. Never touches `pdf_file_path` (the shared venue template).

  - **P1 — Frontend UX / cache**:
    - **`user-dropdown.js?v=2` → `?v=3`** across all 9 HTML pages — cached browsers were missing the Feedback link added in commit d979f11.
    - **`batch-blast` showed success on failure** ([venue.create-gigs.js:5384](app/static/js/venue.create-gigs.js#L5384)) — `await fetch(...)` ignored `res.ok` and showed "Emails sent" regardless. Now distinguishes "Gig Created" from "Gig Created (blast failed)" with the actual error message.
    - **Ban/unban dropped backend `{detail}`** ([venue.create-gigs.js:4764, 4800](app/static/js/venue.create-gigs.js#L4764)) — generic "Could not ban artist" hid real reasons. Now extracts `{detail}` from the JSON body.
    - **Login button not disabled during submit** ([index-init.js:13](app/static/js/index-init.js#L13)) — rapid double-clicks could fire two POSTs and race the brute-force lockout. Now disables + shows "Signing in…" until either success or restored error.

  - **P2 — Polish (~12 fixes)**:
    - **Signup-collision email spam throttle** ([auth.py:411](backend/routes/auth.py#L411)) — added a per-user `signup_collision_last_at` column (lazily added via ALTER) so repeated signup attempts with the same victim email only send the notice once per 24h. Prevents distributed mailbomb attacks since per-IP `RATE_SIGNUP` doesn't stop them.
    - **Contract list filters widened** ([contracts.py:1733, 1759](backend/routes/contracts.py#L1733)) — artist/venue Contracts pages now include `pending`, `artist_signed`, `awaiting_venue_upload`, `fully_signed`. Previously artists couldn't find the "Upload Signed PDF" link from this view.
    - **`upload_signed_pdf` status pre-check before body read** ([contracts.py:1579](backend/routes/contracts.py#L1579)) — moved the status check ahead of `_read_and_validate_pdf` so a stale upload on a fully_signed/cancelled contract doesn't consume 20 MB of bandwidth.
    - **`upload_signed_pdf` filename for multi-slot** — JOIN now binds on `contract["artist_id"]` not `g.artist_id` (which is NULL on multi-slot). Filenames stop degrading to "...Artist.pdf".
    - **`countersign_contract` clears `last_cancelled_artist_id`** ([contracts.py:1521](backend/routes/contracts.py#L1521)) — without this, a previously-cancelled artist stayed permanently excluded from future blast emails on the gig after a re-booking via contract flow.
    - **`list_public_gigs` date filter + rate limit** ([gigs.py:940](backend/routes/gigs.py#L940)) — now returns only `today-7d .. today+90d` and is `@limiter.limit("30/minute")`. Previously a scraper could pull the entire historical catalog anonymously.
    - **`notify_gig_cancelled` fans out to entity_users** ([notification_service.py:230](backend/services/notification_service.py#L230)) — previously only the primary `artist_user_id` / `venue_user_id` got notifications; now uses `get_all_entity_users` for both sides with dedup for users who own both.
    - **Stripe Connect onboarding URLs from `site_url`** ([stripe_connect.py:353](backend/routes/stripe_connect.py#L353)) — `refresh_url` / `return_url` no longer hardcode `gigsfill.com`; staging deploys route artists back to themselves.
    - **Support reply URL from `site_url`** ([admin.py:1619](backend/routes/admin.py#L1619)) — same pattern; ticket reply emails on staging stop linking to production.
    - **`admin-templates.js` export-error check** ([admin-templates.js:170](app/static/js/admin-templates.js#L170)) — the predicate `data.exported === false` literally never matched (backend returns `{exported:<int>}` on success and `{export_error:<str>}` on failure, with no `exported` key). Admins silently lost the post-deploy template-sync warning. Now checks `data.export_error` directly.
    - **`flyer-editor` save/delete error swallowing** ([flyer-editor.js:1813, 1829, 1842](app/static/js/flyer-editor.js#L1813)) — save threw `r.text()` (raw HTML 500 page) instead of parsing `{detail}`; deletes ignored `res.ok` entirely so 403s silently "succeeded". Both now parse JSON detail and surface it.

- **2026-05-25 (part 4) — Second-audit punch list (16 fixes, P0 regressions + P1 + P2).** After running the six-agent test sweep a second time we caught both regressions from the part-1/2/3 batches AND a fresh set of P2-level holes. All fixed in one pass.

  - **P0 regressions caught & fixed**:
    - **`approve_booking` GET decorator misplaced** ([backend/routes/gigs.py:4019](backend/routes/gigs.py#L4019)) — earlier P0 fix added `@router.get` but a stray blank line bound it to `_styled_page` instead of `approve_booking`. Venue-approval email links 404'd. Now the GET decorator sits directly above the handler with no gap.
    - **`deny_booking` GET mutation** ([backend/routes/gigs.py:4179](backend/routes/gigs.py#L4179)) — email-scanner pre-fetches and forwarded URLs could auto-deny a booking. Now mirrors the waitlist-decline pattern: GET renders a styled confirmation page; only the explicit POST mutates state.
    - **`book_with_contract` blast bypass** ([backend/routes/contracts.py:2080](backend/routes/contracts.py#L2080)) — the same logic flaw `book_gig` got fixed for in part 1: synthetic `_check_result["pref"]` could let a non-preferred artist book during the head-start window. Now re-reads the `preferred_artists` row directly via `_real_pref_bwc`.
    - **`mark_resolved` wrong amount column** ([backend/routes/admin_payments.py:858](backend/routes/admin_payments.py#L858)) — used `amount_cents` (frequently NULL on parents) instead of `venue_charge_cents`. Now re-reads the parent and falls back through `venue_charge_cents → amount_cents` so refund/transfer math doesn't compute against NULL.
    - **`payout_scheduler` HTML-escape gaps** ([backend/payout_scheduler.py](backend/payout_scheduler.py)) — part-1 escape only covered three hardcoded helpers; the four template-substituted senders (`_send_payout_email`, `_send_venue_charged_email`, `_send_transfer_failed_emails`, plus fallback bodies) were still raw. Added `_esc()` everywhere.
    - **`api-globals.js` missing cache-buster** on `app/user-profile.html` and `app/venue-discovery.html` — fixed via `?v=2`.
    - **`email_templates.py` ownership reverted to root** — Edit-tool writes run as root and stripped the www-data ownership; re-chowned. Recurring pattern; flagged in the chown rules.
    - **`admin_payments.py` `logger` NameError** at two locations — earlier P1 fixes referenced `logger.warning(...)` but the module has no module-level `logger`. Now use `logging.getLogger("gigsfill.admin_payments")` directly at both call sites.

  - **P1 — atomic / safety guards**:
    - **Same-day approval atomic claim** ([backend/routes/contracts.py](backend/routes/contracts.py), [backend/routes/gigs.py:3811](backend/routes/gigs.py#L3811)) — added `UPDATE … WHERE status='open'` + rowcount check on the same-day approval path so two simultaneous approvals can't double-book a slot. 409 `SLOT_TAKEN` returned to the loser.
    - **`mark_resolved` atomic status guard** ([backend/routes/admin_payments.py](backend/routes/admin_payments.py)) — `UPDATE … WHERE id=:tid AND status=:expected`, rowcount==0 → 409, with the failure surfaced in admin UI. Mirrored in `mark_resolved_batch` (per-row skip with reason) and `reverse_batch`.
    - **`reverse_batch` refund safety re-read** ([backend/routes/admin_payments.py:1730](backend/routes/admin_payments.py#L1730)) — refund branch now re-reads `parent_now` instead of trusting the snapshot. Stripe-webhook sync or another admin advancing the row to `payment_cancelled` mid-batch could otherwise have triggered a duplicate refund.
    - **`reverse_batch` half-completion tracking** — when the Stripe transfer-reversal succeeds but the refund call fails, we now write a `payments_refund_failed_after_reverse` audit row AND tag the parent with a `⚠️ NEEDS REFUND` note + `needs_refund: true` flag. Mirrors the single-row path.
    - **`bulk_action` forwards `send_email`** ([backend/routes/admin_payments.py:2255](backend/routes/admin_payments.py#L2255)) — previously dropped the param, so admin's opt-out got ignored on bulk operations.

  - **P1 — output safety**:
    - **`EmailService.render_template` centralized HTML-escape** ([backend/email_service.py](backend/email_service.py)) — every `{{key}}` value is `html.escape()`d unless the key is in the new module-level `_HTML_SAFE_KEYS` allowlist (~20 keys: rendered HTML fragments, signed URLs, etc.). One fix closes the whole class of injection bugs in email bodies.
    - **`_alert_admin_smtp_failure` body escape** ([backend/email_service.py:240](backend/email_service.py#L240)) — failed-recipient, type, and error text now go through `html.escape()` so a malicious email address or downstream SMTP error containing markup can't render as live HTML in the admin inbox.
    - **`PUT /api/email-templates` audit body truncation** ([backend/routes/admin.py](backend/routes/admin.py)) — long HTML bodies (10+ KB) were being stored whole in the audit log JSON; now truncated via `_trunc(s, n=400)` with `body_len` + `body_preview`. Stops the table from ballooning.

  - **P1 — rate-limiting + audit**:
    - `POST /api/admin/run-payouts` ([backend/routes/affiliate.py:1177](backend/routes/affiliate.py#L1177)) — `@limiter.limit("2/minute")`. Manual payout runs can move real money; this stops an accidental double-tap from firing twice.
    - `POST /api/admin/test-smtp` ([backend/routes/admin.py](backend/routes/admin.py)) — `@limiter.limit("10/hour")` + writes an `admin_action_log` row on success. Prevents admin-credentialed SMTP-relay abuse.
    - `DELETE /api/admin/logs/clear` ([backend/routes/admin.py:2152](backend/routes/admin.py#L2152)) — now records an `admin_action_log` row with the pre-clear buffer size so an admin can't quietly wipe their tracks.

  - **P1 — UX error surfacing**:
    - `cancel_gig` artist-cancel branch ([backend/routes/gigs.py:2247-2266](backend/routes/gigs.py#L2247)) — `UPDATE gigs SET artist_id = CASE WHEN artist_id = :aid OR :aid IS NULL THEN NULL ELSE artist_id END`. Mirrors the venue-cancel keep_open branch fix from part 3. Multi-slot gigs with other artists still booked no longer lose the `gigs.artist_id` link when one artist cancels.
    - `leaveWaitlist` modal button ([app/static/js/artist.book-gigs.js:1582](app/static/js/artist.book-gigs.js#L1582)) — migrated from raw `fetch` to `window.apiDeleteSafe` so FastAPI's `{detail}` surfaces consistently.
    - Booking-Contact dropdown ([app/static/js/artist.edit.js:550](app/static/js/artist.edit.js#L550)) — `change` handler now uses `apiPutSafe`, shows the error in a styled modal, and reverts the dropdown to the previous selection on failure. Previously a silent `console.error`.
    - `apReload` ([app/static/js/admin-payments.js:225](app/static/js/admin-payments.js#L225)) — surfaces `{detail}` instead of `HTTP 500` when payments search fails.

  - **P1 — URL configurability**:
    - `_get_base_url` ([backend/routes/auth.py:963](backend/routes/auth.py#L963)) — now reads `site_url` (canonical key everywhere else) first, falls back to `base_url` (legacy), then hardcoded domain. Affected password-reset and email-verification links.
    - `affiliate.py` ([backend/routes/affiliate.py:25-37](backend/routes/affiliate.py#L25)) — new `_site_base_url(db)` helper. All four hardcoded `https://gigsfill.com` URL constructions (signup_url, aff_url, Stripe Connect onboarding) now use it.
    - `entity_users.py` ([backend/routes/entity_users.py](backend/routes/entity_users.py)) — same helper, applied to invitation `accept_url` / `decline_url` and the legacy `create_invitation_email_html` fallback.
    - `scheduler.py` review-request emails ([backend/scheduler.py:915](backend/scheduler.py#L915)) — read site_url with base_url fallback.
    - `auth.py` "already exists" notification email ([backend/routes/auth.py:419](backend/routes/auth.py#L419)) — login / forgot-password links go through `_get_base_url(db)`.

  - **P2 — orphan files / leaks / fallbacks**:
    - `upload_signed_pdf` ([backend/routes/contracts.py:1583](backend/routes/contracts.py#L1583)) — pre-checks contract status before writing the PDF to disk; if the atomic rowcount guard loses the race, we now `os.unlink` the orphan before raising 409.
    - `download_contract_pdf` redirect fallback ([backend/routes/contracts.py:3359](backend/routes/contracts.py#L3359)) — previously `RedirectResponse(url=pdf_path)` would send users to whatever the DB row contained, including absolute off-site URLs and `javascript:` schemes. Now only redirects when the path is app-local AND realpath-resolves under `UPLOAD_DIR`.
    - `payout_scheduler._send_html_email` ([backend/payout_scheduler.py:958](backend/payout_scheduler.py#L958)) — `server.quit()` moved into a `finally` block. Previously a `sendmail` exception left the SMTP socket open until kernel cleanup; over a long run the scheduler could leak hundreds of half-open connections.
    - `delete_gig_with_slots` cleanup propagation ([backend/routes/gigs.py:4710](backend/routes/gigs.py#L4710)) — keep_open branch's wrapping `try/except` now re-raises `HTTPException` instead of swallowing it into a logged warning, and adds `exc_info=True` to the catch-all so FK / table-missing errors surface in journalctl rather than vanishing.

  - **P2 — auth / authorization tightening**:
    - `me.py` self-delete charged-transaction guard ([backend/routes/me.py:411](backend/routes/me.py#L411)) — refuses to delete a venue or artist account when transactions in `('charged','paid','transferred','pending_transfer','transfer_failed')` exist. Returns 409 `CHARGED_TRANSACTION_EXISTS` with instructions to refund/reverse from Admin → Payments first.
    - `entity_invitation` HTML escape ([backend/routes/entity_users.py:1140](backend/routes/entity_users.py#L1140)) — `create_invitation_email_html` now `html.escape()`s `inviter_first`, `inviter_last`, and `entity_name` before interpolation. A venue named `<script>` no longer injects markup into every invite.

  - **P2 — static asset caching**:
    - `venue-email-center.js` cache-buster ([app/venue-create-gigs.html:2423](app/venue-create-gigs.html#L2423)) — `?v=2` added.

- **2026-05-25 (part 3) — Pre-demo P2 polish sweep (16 fixes).** Last tier of the audit punch list.

  - **Signup hardening** ([app/signup-new.html](app/signup-new.html), [app/static/js/signup-new-init.js](app/static/js/signup-new-init.js)):
    - Added Confirm Password field + live password-strength meter (5-segment scoring on length + casing + digits + symbols). Mismatch blocks `nextStep`. `signup-new-init.js?v=6`.
    - Consolidated the two near-identical duplicate-venue checks ([routes/auth.py:617-637](backend/routes/auth.py#L617)) into one — the first was dead code that diverged from the second only in copy.
    - Welcome email now skipped when the venue UPDATE silently fails during signup ([routes/auth.py:778-810](backend/routes/auth.py#L778)) — a half-populated venue no longer gets a "Welcome!" message.
  - **Artist-side polish** ([app/static/js/artist.book-gigs.js](app/static/js/artist.book-gigs.js)):
    - Migrated cancel-slot, cancel-gig, and `leaveWaitlist` from hand-rolled `fetch` + try-parse-detail blocks to `window.apiDeleteSafe` (which now accepts an optional body — see api-globals.js change below). Half the boilerplate, same error-surfacing. `artist.book-gigs.js?v=148`.
    - `window.apiDeleteSafe(url, body?)` ([app/static/js/api-globals.js](app/static/js/api-globals.js)) extended to accept an optional JSON payload. `api-globals.js?v=2` on all four pages that load it.
  - **Notification + email dedup**:
    - `notify_gig_edited` ([backend/services/notification_service.py](backend/services/notification_service.py)) now dedupes by `user_id` across booked artists. A user who owns multiple booked acts on the same gig used to receive one notification per artist; now gets one combined.
    - `send_booking_emails` ([backend/services/email_dispatch.py](backend/services/email_dispatch.py)) tracks the artist-side recipient list and skips the venue-side email for any shared user. Eliminates the "two emails for one booking" case for artist-AND-venue owners.
  - **Configurable site URL** ([backend/services/email_dispatch.py](backend/services/email_dispatch.py)) — `send_approval_request_emails` now reads `platform_settings.site_url` for the `base_url`, falling back to `https://gigsfill.com`. Staging / test environments stop linking to production.
  - **Scheduler robustness**:
    - `scheduler_main.py` now refuses to start unless `GIGSFILL_RUN_SCHEDULERS=1` is set. The systemd unit already sets it; this guards against an accidental `python -m backend.scheduler_main` from a unit that's missing the env var. Failure path: `sys.exit(2)` with a loud log line.
    - `cancel_gig` ([backend/routes/gigs.py:2347-2364](backend/routes/gigs.py#L2347)) — the five `except Exception: pass` blocks in the "delete entirely" branch now log with `exc_info=True`. Best-effort behavior preserved (one missing table doesn't block the rest), but failures are no longer invisible.
  - **Admin email + audit**:
    - `_alert_admin_smtp_failure` ([backend/email_service.py:214-269](backend/email_service.py#L214)) now also sends to a `platform_settings.admin_alert_email` if configured (an out-of-band address) so a Gmail-side outage that prevents the alert from reaching `self.smtp_username` still has a fallback inbox.
    - `_send_admin_email` ([backend/routes/admin_payments.py:86-160](backend/routes/admin_payments.py#L86)) now opens ONE SMTP session and reuses it across recipients. Multi-user venue/artist (5+ recipients) used to trip Gmail per-connection caps; pooled connection is 3-4× faster.
    - `mark_resolved` ([backend/routes/admin_payments.py:858-905](backend/routes/admin_payments.py#L858)) now accepts an optional `send_email: true` flag in the request body. When set and `new_status='payment_cancelled'`, fires the same venue-refund email as the refund path. Off by default — admin opts in.
    - `download_contract_pdf` ([backend/routes/contracts.py:3253-3260](backend/routes/contracts.py#L3253)) now logs the original exception with full stack trace instead of swallowing it before raising the generic 500.
  - **Email Center fan-out** ([backend/routes/venue_emails.py:92-122](backend/routes/venue_emails.py#L92)):
    - The artist-email query now UNIONs `artists.user_id` and `entity_users.user_id` so multi-user artist accounts receive the broadcast at every member's address (was only sending to the primary user). Deduped by email.
  - **Vanity URL** — verified 1-char slugs already serve the branded 404 page correctly; fuzzy suggestion just naturally doesn't fire for too-short input (no useful signal from difflib). No change.
  - **Affiliate audit IP** — verified all four affiliate `log_admin_action` call sites already pass `request=request`. The audit's outdated line numbers had this looking missing.

- **2026-05-25 (part 2) — Pre-demo P1 punch-list sweep (19 fixes).** Follow-up to the morning's P0 batch — same six-agent audit, P1 tier this time.

  - **Cancel flow integrity** ([backend/routes/gigs.py](backend/routes/gigs.py)):
    - `cancel_gig` now computes `has_venue_access` + `has_artist_access` up front, requires *either*, and FORCES `cancelled_by` to match whichever the caller actually has. Same hardening `cancel_slot` got earlier. A venue user can no longer flip `cancelled_by="artist"` and fire wrong-subject emails.
    - `cancel_gig` keep-open branch now NULLs `gigs.artist_id` only when it actually points at the cancelling artist (CASE expression). Multi-slot gigs with other artists still booked no longer lose the link. Stray `:gig` bound parameter dropped.
    - `delete_gig_with_slots` query for the cancellation-emails snapshot now includes `g.start_time, g.end_time` — previous code returned None, blanking out the "Time" field and breaking waitlist deadline math.
  - **Contract sign-path race guards** ([backend/routes/contracts.py](backend/routes/contracts.py)):
    - `sign_contract`: UPDATE constrained to `status='pending'` + rowcount check. Double-clicks return `{ok: True, already_signed: True}` instead of re-firing `_create_booking_transaction` + booking emails.
    - `countersign_contract`: same atomic guard against `status='artist_signed'`. Returns idempotently on lost-race. Eliminates double-charge from a double-clicked countersign link.
    - `upload_signed_pdf`: UPDATE constrained to `status IN ('pending', 'awaiting_venue_upload')`. An artist re-uploading after the venue countersigned no longer regresses the contract to `artist_signed`.
    - `download_contract_pdf`: realpath()-based containment check rejects file paths that resolve outside `UPLOAD_DIR`. Defense-in-depth against a corrupted-DB / malicious-import value in `signed_pdf_path` / `pdf_file_path`.
  - **Waitlist decline link** ([backend/routes/waitlist.py](backend/routes/waitlist.py)):
    - `respond_to_offer` now `api_route(["GET", "POST"])`. The decline action shows an "Are you sure?" confirmation page on GET; only POST executes the decline. Defeats email-scanner pre-fetches AND adds friction for forwarded URLs. Book action stays GET → 302 since it just redirects to the login-gated booking page.
  - **Multi-user (entity_users) coverage** ([backend/routes/venues.py](backend/routes/venues.py)):
    - `delete_venue`: now allows owner OR entity_users with `role='owner'` (co-owners). Lower-privilege roles (manager/booker) still 403.
    - `list_preferred_requests`: now uses `check_venue_access`. Venue managers can finally see pending preferred-artist requests in the UI.
  - **Frontend UX polish**:
    - Artist autosave ([app/static/js/artist.edit.js](app/static/js/artist.edit.js)) — the two paths that never checked `res.ok` (URL/social field autosave, `saveArtistType`) now surface FastAPI `{"detail": "..."}` via `showErrorModal`. `artist.edit.js?v=8`.
    - Cancel-gig + with-slots dialogs ([app/static/js/venue.create-gigs.js](app/static/js/venue.create-gigs.js)) parse `{"detail": "..."}` on failure instead of throwing bare "Server returned 409". Frequency-policy / charged-transaction errors are now actionable. `venue.create-gigs.js?v=110`.
    - `apShowDetail` + `apEnsureDetailFor` ([app/static/js/admin-payments.js](app/static/js/admin-payments.js)) surface detail body in the admin payments detail-modal load path. `admin-payments.js?v=24`.
  - **Rate limiting + audit log**:
    - `PUT /api/venues/{venue_id}` is the autosave target (hit every keystroke). Capped at `60/minute` per IP.
    - `GET /api/admin/payments/reports/reconcile` capped at `6/minute` — each call fans out up to 200 live Stripe API requests, so a UI loop could blow the Stripe rate budget.
    - Audit-log entries added to: `PUT /api/admin/affiliate/settings`, `DELETE /api/admin/venue-payment-overrides/{id}`, `PUT /api/admin/support-tickets/{id}`, `POST /api/admin/support-tickets/{id}/reply`, and all 4 admin flyer-template endpoints (default-template PUT, templates POST/PUT/DELETE).
  - **Email & scheduler hardening**:
    - `send_cancellation_emails` ([backend/services/email_dispatch.py](backend/services/email_dispatch.py)) — `_cancel_send` now uses the pre-opened pooled `_smtp` connection from the enclosing scope instead of opening a fresh connection per recipient via `_smtp_send`. The "ONE SMTP session" comment in the code was a lie; now it's true. Falls back to per-call open if the pooled login failed.
    - `payout_scheduler.start_payout_scheduler()` ([backend/payout_scheduler.py](backend/payout_scheduler.py)) — added `_payout_scheduler_started` module-level guard so a duplicate call (test code, import order quirks) no-ops instead of starting a second thread that would double-charge venues.
    - `payout_scheduler` hardcoded HTML email helpers now HTML-escape user-controlled fields (`venue_name`, `reason`, `gig_date`, `artist_name`) before f-string interpolation. TODO comment left for the full DB-template migration.
    - `scheduler.run_scheduled_emails` SMTP gate now logs at `ERROR` level (was `WARNING`) and prints to stdout so a missing SMTP config doesn't silently disappear blasts.
  - **Refund / reverse half-completion tracking** ([backend/routes/admin_payments.py](backend/routes/admin_payments.py)):
    - The reverse-with-refund path now lands an `admin_audit_log` row (`payments_refund_failed_after_reverse`) AND writes a `⚠️ NEEDS REFUND` note onto the parent transaction when the optional refund fails after the reversal succeeded. Response carries `needs_refund: true` so the UI can flag it. Previously the only signal was the error string in the JSON response — easy to lose if the admin browser closed before reading.
    - `mark_resolved` `dry_run` now propagated through bulk path (carried over P0 #8, this one ensures the flag actually no-ops the write).

- **2026-05-25 — Pre-demo P0 punch-list sweep (16 fixes).** Six parallel review agents audited the entire app the night before the venue sales meeting; this entry covers all P0s landed in one coordinated patch.

  - **Money-handling / audit-trail integrity** ([backend/services/gig_cleanup.py](backend/services/gig_cleanup.py), [backend/routes/gigs.py](backend/routes/gigs.py)):
    - New `assert_no_charged_transactions(db, gig_id, artist_id=None)` helper raises `409 CHARGED_TRANSACTION_EXISTS` when any transaction tied to the gig (or one artist) is in `charged / paid / transferred / transfer_failed / pending_transfer`. Forces the caller through the explicit refund / reverse flow instead of silently dropping audit rows.
    - Applied the guard to **every** delete/cancel path that calls `cleanup_gig_records`: `delete_gig_with_slots` (venue UI's "Cancel Gig" button), `cancel_slot`, both branches of `cancel_gig` (artist-cancels + venue-cancels), and the "all slots removed" tail in `cancel_slot`. Previously only `delete_gig` had it.
    - `cleanup_gig_records` no longer silently swallows DB errors. Logs with `exc_info=True` and re-raises so callers don't keep mutating on a half-cleaned state.
  - **Contracts — multi-slot fixes** ([backend/routes/contracts.py](backend/routes/contracts.py)):
    - `sign_contract` now mirrors `countersign_contract`'s multi-slot handling: flips the held slot(s) to `booked`, applies pay overrides, partial-books the gig when other slots remain open, creates the booking transaction slot-scoped, and fires `send_booking_emails` with `slot_id`. Previously single-slot-only — multi-slot artist signs left the slot stuck in `pending_contract` forever with no transaction.
    - `upload_gig_pdf` now refuses multi-slot gigs at the route boundary (the blind `UPDATE gigs SET status = 'pending_contract'` would corrupt the parent and hide other slots from search). Also adds `WHERE status = 'awaiting_venue_contract'` + rowcount check so a stale request can't re-bind a cancelled gig.
    - `POST /api/contract-holds/cleanup` is now admin-gated (was anonymous). Scheduler bypasses the auth gate by calling the new `_cleanup_expired_holds_impl(db)` helper directly.
  - **Auth / multi-user (entity_users) coverage** ([backend/routes/gigs.py](backend/routes/gigs.py), [app/static/js/artist.edit.js](app/static/js/artist.edit.js)):
    - `approve_booking` and `deny_booking` UI paths now use `check_venue_access` instead of `venues.user_id = :uid`, so venue managers / bookers added via `entity_users` can act on pending requests.
    - Artist "Booking Contact" dropdown now loads `/api/entity-users/artist/{id}` and lists every owner + entity_user (was hard-coded to the logged-in user). Bumped `artist.edit.js?v=7`.
  - **Booking integrity** ([backend/routes/gigs.py](backend/routes/gigs.py)):
    - `book_gig` same-day-approval branch now re-reads the underlying `preferred_artists` row to determine preferred status, rather than inspecting the local `pref` dict (which gets reassigned to a synthetic `{"status":"approved"}` when a valid blast token is used, or `{"status":"blast"}` for open-blast bypass). The previous logic let a blast-bypassed non-preferred artist skip the same-day approval flow entirely.
  - **Email Center hardening** ([backend/routes/venue_emails.py](backend/routes/venue_emails.py)):
    - `POST /api/venues/send-email` (Email Center broadcast) is now rate-limited at `BROADCAST_RATE = "5/minute"`, recipient-capped at `MAX_BROADCAST_RECIPIENTS = 100`, and HTML-escapes `subject`, `venue_name`, and `body` before substitution (newline → `<br>` is re-introduced AFTER escape). Previously a venue user could embed arbitrary HTML / phishing payloads in the body via the unescaped `{{body}}` placeholder.
  - **Email templates / admin** ([backend/email_templates.py](backend/email_templates.py), [backend/routes/admin.py](backend/routes/admin.py), [backend/routes/emails.py](backend/routes/emails.py)):
    - Added the missing `password_reset` template (was referenced by `auth.py:1002` but absent from `TEMPLATES`, so resets silently logged "SKIPPED" and fell through to `_send_reset_email_direct`). Template count went 47 → 48.
    - `email_templates.py` chowned to `www-data:www-data` (was root-owned, so admin export endpoint would PermissionError despite the DB write succeeding).
    - Refactored `/api/email-templates/export` into a `_export_email_templates_to_disk(db)` helper, then call it automatically from `PUT /api/email-templates` so admin edits sync to disk on save. The previous flow only wrote to DB; the next deploy's `run_migration()` would overwrite all admin edits from the file-side `TEMPLATES` dict.
    - Fixed `NameError: utcnow_naive` in `routes/emails.py:55` (referenced but not imported — every PUT call 500'd).
  - **Admin Payments — dry_run fidelity** ([backend/routes/admin_payments.py](backend/routes/admin_payments.py)):
    - `mark_resolved` now accepts `dry_run` and returns early without writes or audit row when true. `bulk_action` for the `mark-resolved` operation forwards the toggle. Previously the UI "Test (dry run)" toggle silently wrote for real on the bulk path.
  - **Signup UX** ([app/static/js/signup-new-init.js](app/static/js/signup-new-init.js)):
    - Signup failures now parse FastAPI's `{"detail":"..."}` body and surface only the message. Previously users saw `Signup failed: {"detail":"Password must be at least 6 characters"}` — raw JSON. Bumped `signup-new-init.js?v=5`.

- **2026-05-22 — Admin Payments Console: Tier 2-4 single-row + batch actions + gig-centric detail modal + admin-action emails.** Multi-day expansion of the Admin > Payments console. Tier 1 (read-only) shipped 2026-05-13; this slice adds every mutation tier plus a major UX redesign so admin can act on a whole gig from one modal.

  - **Tier 2 — single-row mutations** ([backend/routes/admin_payments.py](backend/routes/admin_payments.py)):
    - `POST /api/admin/payments/{id}/refund` — full or partial venue refund. Validates parent type / status / PI presence; refuses full refund when any child already transferred. Optional auto-cancel of still-scheduled siblings. `dry_run` mode skips both Stripe call and DB writes.
    - `POST /api/admin/payments/{id}/mark-resolved` — force status with required reason (min 5 chars). Whitelist: paid / transferred / payment_cancelled / suspended / dispute_won / dispute_lost. No Stripe call. Audit-logged.
    - `POST /api/admin/payments/{id}/refire` — reset stuck/failed row to `scheduled` so the next scheduler sweep retries. Defense-in-depth: the scheduler is the only place that mints Stripe writes, so refire can't double-charge.
    - `POST /api/admin/payments/{id}/resend-email` — replay venue_charged / artist_payout_sent template via existing scheduler senders (raw sqlite Row connection from `backend.db.get_db_connection()`).
  - **Tier 3 — destructive single-row** plus the multi-row reverse batch:
    - `POST /api/admin/payments/{id}/reverse-transfer` — single-child reversal via `stripe.Transfer.create_reversal`. Optional combined venue refund (`also_refund_venue=true`) runs after the reversal succeeds. If the refund fails after the reversal succeeded, response carries `refund.error` and admin retries the refund manually — we don't auto-rollback the reversal because the funds in platform balance is the safe place to sit while we figure out what went wrong.
    - `POST /api/admin/payments/{parent_id}/reverse-batch` — reverse N child payouts of one parent venue charge in one call, with optional combined venue refund. Per-row failures don't abort the batch. Hard-capped at 50 reversals/call. The frontend always uses this endpoint now (even for one reversal) for consistency.
    - `POST /api/admin/payments/{id}/reroute-payout` — change destination artist on a still-scheduled payout. Validates the destination's Connect onboarding is complete.
  - **Tier 4 — bulk + reconciliation**:
    - `POST /api/admin/payments/bulk` — apply mark-resolved / resend-email / refire across up to 100 rows. Money-moving actions (refund / reverse) are intentionally NOT bulk-able.
    - `POST /api/admin/payments/mark-resolved-batch` — gig-wide variant: per-row `new_status` with a shared reason. Each row gets its own audit-log entry.
    - `POST /api/admin/payments/resend-email-batch` — gig-wide variant: template auto-derived per row (venue_charge → venue_charged, artist_payout → artist_payout_sent).
    - `GET /api/admin/payments/reports/reconcile` — diff `transactions` against Stripe (PaymentIntents + Transfers) for a gig-date window. Read-only; surfaces status mismatches + amount divergences. **The path is under `/reports/` because FastAPI route order matters**: `GET /api/admin/payments/{txn_id}` (txn_id: int) is registered earlier and was eating `GET /api/admin/payments/reconcile` with a 422 "Input should be a valid integer." Renaming the path was a smaller diff than reordering all the route registrations.
  - **Admin-action email auto-send** ([backend/email_templates.py](backend/email_templates.py)):
    - Two new templates: `payment_refunded_venue` and `payment_transfer_reversed_artist`. Both follow the existing `venue_payment_charged` / `artist_payment_sent` layout.
    - Helpers `_send_venue_refund_email` and `_send_artist_reversal_email` in `admin_payments.py` fire after every successful refund / reverse-transfer (single-row and batch). **They bypass the per-user `user_has_email_enabled` notification-preference check** — admin moving money is always-notify, by product decision. Email failures are best-effort and never fail the action.
  - **Gig-centric detail modal redesign** ([app/static/js/admin-payments.js](app/static/js/admin-payments.js), `apShowDetail`):
    - Replaced the 14-cell per-txn header grid with a 4-line Gig Overview card (Gig · Venue · Date · Selected row).
    - Replaced separate "Related Rows" + "Gig Slots" tables with one unified "Transactions on this gig" table: ID · Slot · Time · Type · Artist (venue name on the parent row, artist name on child rows) · Status · Gig $ · Paid · Processed. Clicked row highlighted in cyan.
    - **Gig Actions panel** (purple) above the table. Single `↺ Refund / Reverse` button covers both flows in one modal: Step 1 = pick artist payouts to reverse (defaults all reversible checked), Step 2 = refund the venue (auto-fills amount from Step 1 total, falls back to full venue charge when Step 1 empty). Plus `✉ Resend Emails` (batch) and `✓ Mark Rows Resolved` (batch with per-row status). All buttons uniform width via CSS grid `repeat(auto-fit, minmax(260px, 1fr))`.
    - **Row Actions panel** (cyan) below the table only carries genuinely-per-row ops: `↪ Re-route Payout` (per-row destination) and `↻ Re-fire` (one stuck row). Hides entirely when neither applies. Refund / Reverse / Mark Resolved / Resend Email used to live here too — moved to gig-wide batches above.
    - Action buttons render ONLY when they apply to the current row's (type + status) combo. No more disabled-with-tooltip buttons that did nothing when clicked.
  - **Find Artist modal** for the Re-route flow — stacked over the Re-route modal, modeled on Payment Settings' Venue Free Trials search (letter filter + free-text search). Click an artist row to autopopulate the destination artist id. Underlying `apCloseTopModal()` helper closes only the topmost gfm-modal-overlay so the Re-route modal beneath stays open.
  - **Toast helper** `window.apToast(message, type)` — non-blocking confirmation for clean success states. Used by the four batch flows; failure paths still use `window.showErrorModal` so admin reads the per-row errors before they go away.
  - **CSP** updates (FastAPI middleware + `/etc/nginx/sites-enabled/gigsfill` server block, kept in sync): `media-src 'self' blob: https:` so `<audio>` plays artist-uploaded external URLs; `frame-src` adds `w.soundcloud.com` and `*.bandcamp.com` for audio_link iframe embeds. Reminder: nginx serves `/app/*` static files directly with its own CSP and `proxy_hide_header Content-Security-Policy` for the proxied responses — both sources must be updated together.
  - **Subtle bugs fixed in passing**:
    - `audit_log` table query in Tier 1 detail endpoint pointed at a nonexistent table name; fixed to `admin_audit_log` with the correct columns so recent admin actions actually surface.
    - `apShowDetail` was writing the loaded body via `document.querySelector('.gfm-modal-overlay')` (oldest) instead of `querySelectorAll(...)[last]` (topmost). From inside Reconcile, the detail body would land in the Reconcile modal underneath and the new Loading modal stayed stuck.
    - `fmtDateTime` switched to 12-hour AM/PM (`hour12: true`) and parses DB UTC strings with explicit `Z` so all detail-modal times render in the admin's local timezone.
    - Reverse Transfer modal: parent lookup now handles being opened from a parent venue_charge row, not just from a child payout (`t.parent_transaction_id` is null in that case — fall back to scanning siblings for the venue_charge row without a parent).
- **2026-05-21 — Audio Links + MP3 cap, themed modals on edit pages, CSP relaxation, Member Availability past-date filter, Affiliates tab reorder.** Big polish pass across artist-edit, venue-edit, user-profile, and artist-profile.
  - **Audio_link media type (new).** Artists can now attach external audio URLs (SoundCloud, Bandcamp, self-hosted MP3s) in addition to MP3 uploads. Backend [backend/routes/media.py](backend/routes/media.py): added `"audio_link"` to the allowed `media_type` whitelist, routed it through the URL branch alongside `"video"` (URL stored in the existing `video_url` column — no schema change). MP3 uploads now capped at **3 per artist** (server-side `HTTPException(400)` + client-side pre-click guard); audio links uncapped. Frontend renders smart players in both [app/static/js/artist.edit.js](app/static/js/artist.edit.js) (`renderAudioLinkPlayer`) and the public [app/artist-profile.html](app/artist-profile.html) audio tab: SoundCloud URL → widget iframe; direct audio file (`.mp3`/`.wav`/`.ogg`/`.m4a`/`.aac`/`.flac`) → `<audio controls>`; otherwise clickable 🔗 link fallback. MP3 upload now auto-populates the Title field from the source filename (extension stripped, `_`/`-` → spaces). Running `(n/3)` count badge on the upload button, updated immediately on add/delete.
  - **Member Availability (new feature, was added 5-19 to 5-21).** Per-user blackout dates that scope to specific artists, surfacing as **soft warnings** at booking time (hard block stays at the artist level via existing `artist_availability`). New `user_availability` table (`user_id`, `artist_id` nullable, `blackout_start`, `blackout_end`, `reason`, `created_at` — indexed on user_id, artist_id, dates). `artist_id IS NULL` means "applies to every artist this user is on"; specific id means that artist only. New endpoints in [backend/routes/availability.py](backend/routes/availability.py): `GET/POST/PUT/DELETE /api/me/availability` (member self-service) and `GET /api/artists/{aid}/member-availability` (aggregated read for artist-edit). Helper `_member_blackouts_for_gig(db, artist_id, gig_date)` returns conflicts for the booking-precheck. Frontend: new "My Availability" tab in [app/user-profile.html](app/user-profile.html) with form to add member blackouts + scope (All My Artists / Specific Artists Only — implemented as **checkboxes**, not radios, because gigsfill.css's `input { width:100% }` rule has no `input[type="radio"]` override and stretches radios across the row; mutual exclusion is enforced in JS via `window.uaSetScope`). New "Member Availability" section on [app/artist-edit.html](app/artist-edit.html) showing a flat chronological list of all members' upcoming blackouts (scope is intentionally hidden — it's private to the member). Booking precheck in [backend/routes/gigs.py](backend/routes/gigs.py) (`/api/gigs/{gig_id}/booking-precheck`) returns `member_blackouts[]` when there are conflicts; [app/static/js/artist.book-gigs.js](app/static/js/artist.book-gigs.js) `runBookingPrecheck` shows a "Member Unavailable — book anyway?" modal (uses `showStyledModal`, not `showConfirm`, because the latter escapes HTML and we need a `<ul>` of names + dates).
  - **Past-date filter on all blackout displays.** Three GET endpoints in [backend/routes/availability.py](backend/routes/availability.py) — `/api/artists/{aid}/availability`, `/api/me/availability` for `user_blackouts`, and `/api/me/availability` for `band_blackouts` (artist_availability rows surfaced to members) — now filter with `date(blackout_end) >= date('now', '-1 day')`. The -1 day buffer matches the existing pattern in the member-availability endpoint and handles the UTC-server / venue-local-date edge case. Without this, past blackouts (e.g. May 6-8 still visible on May 21) cluttered the UI.
  - **Themed delete confirmations everywhere.** Replaced browser `confirm()` / `alert()` with `window.showConfirm` / `window.showErrorModal` from [gf-modals.js](app/static/js/gf-modals.js) in: [user-availability.js](app/static/js/user-availability.js) `uaDelete`, [artist.edit.js](app/static/js/artist.edit.js) media delete (with kind-specific labels: "Delete this MP3 file?" / "Delete this audio link?"), [venue.edit.js](app/static/js/venue.edit.js) media delete. **Pitfall worth noting**: `artist-edit.html` and `venue-edit.html` were both missing the `<link href="/app/static/css/modals.css">` even after `gf-modals.js` was wired in — the JS happily built `.gfm-modal-overlay` nodes but they rendered as plain flow-layout text at the bottom of the page because the overlay/card CSS wasn't loaded. **Always pair `gf-modals.js` with `modals.css` on any new page.**
  - **CSP relaxation for external audio.** Both [backend/main.py](backend/main.py) `SecurityHeadersMiddleware` AND `/etc/nginx/sites-enabled/gigsfill` (the nginx server block, which sets its OWN `$CSP` for `/app/` static files and overrides the proxied FastAPI header) updated: `media-src` now `'self' blob: https:` (was `'self' blob:`) so `<audio>` can fetch from any HTTPS host — without this artists' external MP3 URLs played in Chrome devtools but produced no sound on the page. `frame-src` adds `https://w.soundcloud.com https://bandcamp.com https://*.bandcamp.com` so SoundCloud widget iframes load. **Heads-up**: nginx serves `/app/*` static files directly with its own CSP — editing FastAPI's CSP alone won't fix static-file pages. Both must be kept in sync.
  - **Affiliates tab reorder + Recommend below W-9.** New order on [app/user-profile.html](app/user-profile.html) Affiliates tab: Your Affiliate Code → Tax Information (W-9) → Recommend GigsFill → Stats → Referred Venues → Payout History.
  - **Block Dates button alignment.** The "+ Block Dates" button in artist-edit's Artist Availability section was sitting slightly below the input row because each `.field` wrapper carries `margin-bottom:20px` from gigsfill.css. With `align-items:flex-end`, the field's outer bottom (input + 20px margin) was aligning instead of the visible input bottom. Zeroed `margin-bottom` on the three `.field` wrappers in the inline form ([artist-availability.js](app/static/js/artist-availability.js) `_renderAvailabilityUI`). Also restyled the button to use `.btn.primary` for visual consistency with the other Add buttons on the page.
  - **My Availability tab fix.** The "📅 My Availability" tab panel rendered blank because its `<div class="tab-content">` had an inline `style="display:none;"` that overrode the `.tab-content.active` CSS rule, and `switchTab()` only had a special-case unhide for `#affiliates-tab`. Dropped the inline style so the CSS class alone controls visibility.
  - **artist-profile audio tab now displays audio_link entries.** Was hard-coded to only render `media_type==="audio"`; added a parallel branch for `"audio_link"` so links show up next to MP3 uploads. Also reordered the public profile's media tabs: Pictures · Videos · Audio (was Videos · Pictures · Audio).
- **2026-05-13 — Admin Payments Console (Tier 1, read-only) + Stripe Dashboard webhook sync.** First slice of the admin payments tooling. Unified searchable view of every transaction across all venues and artists, plus webhook handlers so Stripe Dashboard actions (refund, transfer reversal, dispute resolution) auto-sync into our DB.
  - **New module `backend/routes/admin_payments.py`** (auth via existing `check_admin`):
    - `GET /api/admin/payments/search` — paginated transaction list with filters: free-text (venue/artist/gig/Stripe ID), status, type, gig-date range, amount range, venue id, artist id. Returns parent + child rows ordered by most-recent-action first.
    - `GET /api/admin/payments/stats` — aggregate KPIs for the date window: revenue, commission, payouts, needs-attention count, disputed count, free-trial count.
    - `GET /api/admin/payments/{txn_id}` — full detail including sibling rows (parent/children), gig slots, and recent `audit_log` entries.
  - **New admin tab** in [admin.html](app/admin.html): "💳 Payments" between Platform Settings and Support. KPI cards at the top, filter bar, paginated table, row-click opens a `showStyledModal` detail panel with deep links to Stripe Dashboard. All read-only — banner at the bottom of the detail modal notes Tier 2 (refund/reverse/re-route) is coming. JS in [app/static/js/admin-payments.js](app/static/js/admin-payments.js); lazy-loads on first tab activation.
  - **Stripe webhook handler patched** in [stripe_connect.py](backend/routes/stripe_connect.py) to sync admin actions taken in Stripe Dashboard back to our DB:
    - `charge.refunded` — sync to `status='payment_cancelled'` (full refund) or leave + note (partial). Admin email alert.
    - `transfer.reversed` — sync to `status='payment_cancelled'` for the artist_payout row. Admin email alert.
    - `charge.dispute.closed` — map Stripe's `won`/`lost` to our `dispute_won`/`dispute_lost`.
    This closes the manual-sync gap — admin can use Stripe Dashboard as a fallback and our DB stays consistent without manual intervention.
  - **Tier 2 (refund/mark-resolved/re-fire/resend-email)** is the next slice. Tier 3 (reverse-transfer, re-route, status-override) and Tier 4 (bulk + reconciliation report) follow.
- **2026-05-13 — Go-live audit: 3 more user_id-based artist lookups in display + email paths.** Comprehensive pre-launch audit of the payment pipeline (full report in chat history). The critical bug fix earlier today (commit `35df328`) was the most severe instance — money routed to the wrong Connect account. The audit found three additional sibling sites where the same user_id pattern was wrong but lower-severity (wrong artist NAME in display/email, no money misrouted):
  - **[stripe_connect.py:1478](backend/routes/stripe_connect.py)** `get_venue_upcoming_charges`: COALESCE fallback subqueries `(SELECT a3.name FROM artists a3 WHERE a3.user_id = t.to_user_id LIMIT 1)` for `artist_name` and `resolved_artist_id` showed the wrong artist on the venue's upcoming-charges UI. Removed the user_id fallback — the primary JOINs on `a.id = t.artist_id` and `a2.id = g.artist_id` already cover every txn under the current code.
  - **[payout_scheduler.py:1162](backend/payout_scheduler.py)** `_send_venue_charged_email`: the initial gig_info query used `LEFT JOIN artists a ON a.user_id = ?` to set `artist_name`. The multi-slot override path below correctly used `a.id = t.artist_id` and would overwrite, but single-slot/legacy txns relied on the buggy initial lookup. Switched to `txn.artist_id`-first with user_id legacy fallback.
  - **[payout_scheduler.py:709](backend/payout_scheduler.py)** onboarding-incomplete email lookup: `SELECT a.id, a.name FROM artists a WHERE a.user_id = ?` could email the wrong artist about completing Stripe Connect onboarding. Switched to `WHERE id = txn.artist_id` with the user_id fallback.
  - **Audit also confirmed OK** (no fix needed): idempotency keys on all Stripe writes, money math correctness, cancellation path cleanup, free-trial toggle safety (no race conditions), scheduler atomic-claim, transaction status invariants, webhook signature verification, `from_user_id`/`to_user_id` field usage, frontend SQL filtering on artist Payments tab, multi-slot booking math.
- **2026-05-13 — CRITICAL: payout routing bug — Connect account chosen by user_id, not artist_id.** `_transfer_to_artists` and the stalled-retry path in `payout_scheduler.py` both looked up the destination Stripe Connect account by joining `artists.user_id = txn.to_user_id`. For multi-artist users (one account owning >1 artist), this returns multiple rows; `.fetchone()` picks the artist with the lowest id. Result: payouts intended for an artist with a higher id were silently delivered to a sibling artist's Connect account. Stripe accepted them, no error fired, the bal_tx poller threw hourly resource_missing warnings because it couldn't find the destination charge under the expected account but that was just noise. Production impact: two transfers ($16.66 each) for artist_id=3 (Fifty Proof) on gigs 507 and 496 landed in artist_id=1 (Fridays Past)'s account — both artists owned by user 1, so no money movement was needed to recover, just DB sync.
  - **Fix** (commit `35df328`): both queries now do `SELECT … FROM entity_payment_settings WHERE entity_type='artist' AND entity_id = txn.artist_id` (with the legacy `user_id` join kept as a fallback for very old rows that pre-date the `artist_id` column). This is the same bug pattern fixed in the payout email path on 2026-05-11 (commit `2ea4a1d`); we missed the transfer-destination lookup at the time.
  - **DB sync**: txns 296 + 305 marked `status='paid'` (was `'transferred'`) so the bank-settlement poller stops trying to look up the destination charge under the wrong account. `artist_id` left at 3 — preserves the booking attribution. Notes column annotated with the full explanation.
- **2026-05-12 — Modal system Phases 3–5 + UX polish on contract flows.**
  - **Phase 3** (`168bb53`): migrated four custom modals to gf-modals — `showPdfContractModal` (two-state hold→download/upload), `showPerGigPdfModal`, `openReviewModal` (venue→artist star rating), `openVenueRateModal` (artist→venue star rating). Two-state PDF modal uses `closeAllModals` + reopen to transition between states; star modals wire hover/click handlers post-mount via `document.querySelector('.gfm-modal-overlay')`.
  - **Phase 4** (`cb96e27`): removed dead `#alertModal` static markup from venue-create-gigs.html. Survey of the other static modals (`#gigModal`, `#paymentRequiredModal`, `#seriesModal`, `#contractRemoveOverlay`, `#emailDetailModal`) confirmed all are still actively rendered into from JS — kept inline.
  - **Phase 5** (`7d57bf7`): extracted 31 byte-identical legacy `.modal-*` CSS rules from artist-book-gigs.html, venue-create-gigs.html, and public-gigs.html into a new `/app/static/css/modals-legacy.css` shared stylesheet. Rules with diverging bodies left inline. Net: -239 lines of duplicate inline CSS across the three pages.
  - **Contract flow polish** (`cb96e27`): venue's Pending-Contract slot now shows the green Pay pill (added `vType === 'venue'` to gig-modal.js `showPay` gate — venues own the gig, no reason to hide pay). Venue countersign success and artist contract-upload success both extended from 1.5s to 5s auto-close, with a "(Closing in 5 seconds, or click Close.)" hint so users can read the confirmation before dismissal. Background data refresh (calendar, activity center, Payments tab) now fires immediately on success rather than waiting for the timeout.
  - **Remove-contract modal** (`37754de`): the "Remove" button in `#contractRemoveOverlay` called `removePdf()` which only opens the modal (loops back to itself, did nothing). Exported `confirmRemove` on `window.venueContracts` and pointed the button there.
  - **Single-slot venue Pay layout** (`37754de`): dropped the top-of-modal "Pay:" row that was single-slot-only. Pay now renders inline on the slot row for both single + multi-slot gigs so the layout is identical regardless of slot count. `slot.pay` falls back to `gig.pay` for legacy rows.
- **2026-05-11 — Onboarding checklist popup wired to venue page.** `venue-payment-guard.js` was correctly deferring to the onboarding checklist when onboarding is incomplete ("let the checklist handle it"), but `onboarding-checklist.js` was only loaded on `artist-book-gigs.html`. Net effect: venues with no payment card on file saw no popup at all. One-line fix: added `<script src="onboarding-checklist.js">` to `venue-create-gigs.html`. (`034b04f`)
- **2026-05-11 — Free Trial: surface audit rows on Payments tabs + convert upcoming bookings on toggle-off.** Free-trial venues skip Stripe at booking time and write an audit row with `transaction_type='free_trial'`. These rows were invisible on both venue and artist Payments tabs because the SELECT filters excluded that type/status. Added it to both queries. Frontend statusMap + colorMap + TERMINAL set on `venue-stripe-payment.js` and `artist-stripe-payment.js` now render `'free_trial'` as 🎟 Free Trial (amber). When admin toggles Free Trial OFF, new helper `_convert_upcoming_free_trial_bookings(db, venue_id)` walks every gig at the venue with `gig.date >= today-in-venue-tz`, deletes the free-trial audit row, and calls `_create_booking_transaction()` again — the override is gone so this runs the normal billing path. Past free-trial gigs stay as audit history. (`d73234f`)
- **2026-05-11 — Waitlist email-decline path + venue Payments stale-row fix + Activity Center notification cleanup.** Multiple related fixes:
  - **Email decline DELETED the waitlist row** instead of marking `offer_declined=1`. `fire_cancelled_gig_blast`'s exclusion subquery (`SELECT artist_id FROM gig_waitlist WHERE offer_declined=1`) then found nothing → emailed the artist a "Gig Just Opened Up!" blast they had just declined. Fixed `respond_to_offer` decline path in `routes/waitlist.py` to mark the row declined (or insert one if the artist was already in `waitlist_offered`).
  - **"Waitlist Spot Available" notification didn't clear** on decline OR accept. New helper `_clear_waitlist_offer_notification(db, gig_id, artist_id)` wired into `leave_waitlist`, `respond_to_offer` decline, `respond_to_offer` book (cleared at redirect time), `book_gig`, and `book_slot`. Targets all users of the artist via `artist_id`, so multi-user accounts are handled.
  - **Venue Payments tab didn't refresh after cancel** — pre-gig cancels DELETE the transaction (`cleanup_gig_records`), but the multi-slot `with-slots` cancel handler, the `/cancel` single-slot path, and the slot-cancel path all skipped `loadVenueBillingHistory()`. Added the call to all three so the cancelled row clears immediately. (`c9b3057`)
- **2026-05-11 — Multi-slot waitlist join handler + Activity Center slot phrasing/time.** Three things:
  - **Waitlist join was silently no-op'ing on multi-slot gigs.** The confirm modal in `gig-modal.js` passed `action: doJoin` to `window.showStyledModal`, but gf-modals' `_buildModal` only looked for `b.onClick`. The handler dropped on the floor — modal closed, no network call, no console activity. Made `gf-modals.js` accept both `onClick:` (new convention) and `action:` (legacy used by gig-modal.js + the local adapter in `artist.book-gigs.js`).
  - **Activity Center multi-slot phrasing inconsistency.** Single-slot rendered "X booked a gig at Y on Date at Time" using structured fields; multi-slot fell through the "has Slot" branch and just linkified the raw DB message, giving "X booked your gig on 2026-05-11 at 7:00 PM. Slot 1". Now both build the rich main message from structured fields and use "Slot N" as the detail.
  - **Slot 2 at 9pm displayed as 7pm** because the frontend used the parent gig's `start_time`. `notifications.py` now parses "Slot N" from the message, looks up that slot's start_time, and surfaces it as `slot_start_time` on the payload. Activity Center prefers `slot_start_time` when present. Same JS on artist + venue pages so both views are fixed. (`359e3d8`)
- **2026-05-11 — Site-wide timezone audit + fixes.** Production server runs UTC but gig start_time/end_time/date are stored as venue-LOCAL strings (e.g. "19:00" = 7pm Pacific). Any naive `datetime.now()` or `date.today()` compared against those strings was off by up to 8 hours / 1 day. Reported symptom: venue tried to save edits to a 7pm-Pacific gig at 12:50pm Pacific (= 19:50 UTC), backend's `GIG_IN_PROGRESS` guard saw "now" as 19:50 vs gig "19:00" naive and returned 409. Frontend's Save click silently swallowed the error.
  - **[backend/routes/gigs.py](backend/routes/gigs.py) `update_gig`**: in-progress guard now uses `get_venue_timezone(db, venue_id)` for both "now" and the gig start interpretation.
  - **[backend/routes/gigs.py](backend/routes/gigs.py) `book_slot` + `book_slot_direct`**: W-9 year check and frequency-window `days_until` now use venue tz instead of `date.today()` (which returns UTC date on the server).
  - **[backend/routes/gigs.py](backend/routes/gigs.py) `create_gig` catchup thread**: switched from platform tz to venue tz when computing `_cup_today` to pick which post-create blasts have already missed their window.
  - **[backend/routes/gig_modal.py](backend/routes/gig_modal.py) `get_gig_modal_data`**: `is_past` and `is_in_progress` now use venue tz, not platform tz. Also fixed overnight-slot wrap (`et < st → et += 1d`).
  - **[backend/routes/stripe_connect.py](backend/routes/stripe_connect.py) `charge_booking`** (currently dead/unused but still exposed): `payout_date` now computed in venue tz then converted to naive UTC, matching the canonical path in `_create_booking_transaction` (gigs.py:362).
  - **[backend/routes/preferred_artists.py](backend/routes/preferred_artists.py) `get_preferred_artists_with_gigs`**: `today` for filtering upcoming gigs uses venue tz instead of platform tz.
  - **[app/static/js/venue.create-gigs.js](app/static/js/venue.create-gigs.js) Save Changes handler**: added a pre-flight `isGigStartedToday`/`isGigEndPassed` check at the top of the save handler (before detach-from-series fires, so we don't leave the DB half-mutated). Replaced the silent `catch(e){console.error(...)}` with a parsed `{"detail":"..."}` extraction + `showAlert` so all PUT/POST failures are visible to the user.
  - **The audit also confirmed [backend/payout_scheduler.py](backend/payout_scheduler.py) and [backend/scheduler.py](backend/scheduler.py) were already correct** — they use platform tz/UTC consistently and never cross venue-local strings with naive UTC `now`.
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

  **Freshness guard added**: verifier now also checks that the latest backup file is < 26 hours old. Without this, if cron daemon stops or the backup script crashes, the verifier would silently keep validating yesterday's (or older) backup without ever raising. 26h gives a 2h grace window beyond the 24h cycle in case of clock drift or briefly delayed cron.
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

artist_availability                    ← artist-level blackout dates (hard-block).
                                         No gig can be booked for the artist on
                                         these dates.

user_availability                      ← member-level blackout dates (soft-warn).
  id, user_id, artist_id NULLABLE,       artist_id=NULL → applies to every artist
  blackout_start, blackout_end,          the user is a member of; specific id →
  reason, created_at                     just that artist. Surfaced as a confirm
                                         modal at booking time (artist can still
                                         book "perform without them"). Indexed
                                         on user_id, artist_id, dates.

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
   - **Content Security Policy** — currently includes `'unsafe-inline'` for both script-src and style-src because of ~200 inline `onclick=` handlers in JS-generated HTML. The plan ("Phase 6") is to migrate these to `event-delegate.js` so a nonce-based CSP can replace `unsafe-inline`. Allowed external sources: `js.stripe.com`, `cdnjs.cloudflare.com`, `fonts.googleapis.com`, `fonts.gstatic.com`, `youtube.com`, `api.stripe.com`. `media-src 'self' blob: https:` (broad `https:` so artist `audio_link` URLs play); `frame-src` adds `w.soundcloud.com` and `*.bandcamp.com` for audio-link iframe embeds.
   - **TWO sources of CSP, must be kept in sync.** This `SecurityHeadersMiddleware` only fires for proxied API responses. `/app/*` static files (HTML, JS, CSS) are served **directly by nginx**, which sets its own `$CSP` in `/etc/nginx/sites-enabled/gigsfill` (search for `set $CSP`) and explicitly hides the proxied one (`proxy_hide_header Content-Security-Policy`). When you change the FastAPI CSP, also update the nginx variable and `sudo nginx -t && sudo systemctl reload nginx`. nginx static caching (`max-age=86400`) means browsers will hold the old policy until cache-bust or hard-refresh.
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
- `POST /api/artists/{artist_id}/media/{media_type}` — `media_type ∈ {profile, picture, audio, video, audio_link}`. File-based for `profile/picture/audio`; URL-based (form field `video_url`) for `video/audio_link`. **MP3 cap**: `audio` uploads return HTTP 400 once an artist has 3. **No schema change for audio_link** — the URL is stored in the existing `video_url` column.
- `POST /api/venues/{venue_id}/media/{media_type}` — venues only support `profile/picture/video` (no audio).
- `PUT /api/media/{media_id}`, `PUT /api/venues/media/{media_id}` — edit (title, display_order)
- `DELETE /api/media/{media_id}`, `DELETE /api/venues/media/{media_id}` — removes the DB row AND `os.remove`s `file_path` from disk when present. For URL-based rows (`video`/`audio_link`), `file_path` is NULL so only the DB row is deleted.

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
