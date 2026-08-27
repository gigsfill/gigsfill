/**
 * GigsFill Service Worker
 * Caches app shell for offline launch + faster repeat loads.
 * Uses network-first for API calls, cache-first for static assets.
 */

// Audit fix (May 2026 part 6): bump so PWA users with a stale SW get the
// fresh shell on activate. Bump whenever precached assets change.
// v7 (2026-06-16): forces eviction of any cached artist-profile.html that
// pre-dates the audio-caption + audio-entry reorder changes.
// v8 (2026-06-17): forces eviction of any cached venue-create-gigs.html
// that pre-dates the templates-in-header relocation + door-pill restyle.
// v10 (2026-08-11): sticky-tabs work. Evicts stale gigsfill.css that the
// SW was serving from cache with the old translucent header + tabs so
// scrolling content ghosted through. New CSS is opaque + flush.
// v11 (2026-08-11): CSS/JS switched to network-first — cache-first was
// making every CSS edit require two reloads before it took effect.
// v12 (2026-08-11): tabs switched to position:fixed for bulletproof
// pinning. Bump to evict stale cached CSS in case v11 didn't take.
// v13 (2026-08-27): eviction sweep. A user reported logging in
// successfully (200) but landing back on the login page every time.
// v14 (2026-08-27): stop intercepting /api/ entirely and repair
// the CSS/JS + HTML handlers so a fetch failure with no cached
// entry no longer resolves respondWith(undefined), which the browser
// surfaces as a bare TypeError: NetworkError. The auth guard treated
// that as an auth failure and bounced the user to login → exact
// symptom the user was seeing. Not returning from /api/ means the
// browser handles the request directly, no SW involvement, so the
// SW can never break the auth-critical fetch path again.
// v15 (2026-08-27): SKIP_WAITING message handler so a fresh SW
// can be promoted the moment sw-register.js asks — no more
// waiting for every controlled tab to close.
const CACHE_NAME = 'gigsfill-v15';

// App shell — core files needed to launch
const APP_SHELL = [
  '/app/index.html',
  '/app/static/css/gigsfill.css',
  '/app/static/css/mobile.css',
  '/app/static/favicon.svg',
  '/app/static/js/city-autocomplete.js',
  '/app/static/js/time-format.js',
  '/app/static/js/timezone-utils.js',
  '/app/static/js/user-dropdown.js',
  '/app/static/js/auth.guard.js',
];

// Install: pre-cache app shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

// Activate: clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// Aug 27 2026: sw-register.js can post {type: 'SKIP_WAITING'} to
// force a newly-installed SW to take over immediately, bypassing
// the default "wait until all controlled clients close" behavior.
// Needed so users hit by a broken older SW (respondWith(undefined))
// self-heal on their next visit instead of being stuck until they
// visit DevTools and unregister manually.
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// Fetch strategy
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Skip non-GET requests
  if (event.request.method !== 'GET') return;

  // Skip cross-origin requests (CDNs, Google Fonts, Stripe, etc.)
  if (url.origin !== self.location.origin) return;

  // API calls: DO NOT intercept. Let the browser make the request
  // directly. There is no useful offline behavior for API data (a
  // cached /api/me stamped on install would return the wrong user
  // for anyone else on the machine), and the previous handler could
  // resolve respondWith(undefined) when both network AND cache
  // missed, which the browser surfaces as `TypeError: NetworkError
  // when attempting to fetch resource`. auth.guard.js caught that,
  // treated it as an auth failure, and bounced the user to login.
  if (url.pathname.startsWith('/api/')) return;

  // CSS + JS: network-first (Aug 11 2026). Previously cache-first with
  // background revalidate — that returned STALE CSS on every load and
  // only updated the cache for the NEXT visit, so a CSS edit needed
  // two reloads to take effect (and users often never scrolled that
  // far). Switch to network-first: fetch fresh every time, fall back
  // to cache only when offline. Small perf cost, huge deploy-freshness
  // win. Images/fonts still cache-first below since they rarely change.
  if (url.pathname.match(/\.(css|js)$/)) {
    event.respondWith(
      fetch(event.request).then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      }).catch(async () => {
        // Aug 27 fix: fall through to a real network error Response
        // rather than resolving respondWith(undefined) when the cache
        // misses — undefined trips the browser into `NetworkError` and
        // the page can't tell "actually offline" from "SW broke".
        const cached = await caches.match(event.request);
        return cached || Response.error();
      })
    );
    return;
  }

  // Images / fonts: cache-first (rarely change; freshness cost > perf cost).
  if (url.pathname.match(/\.(svg|png|jpg|jpeg|gif|webp|woff2?|ttf|eot)$/)) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        const fetchPromise = fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        }).catch(() => cached);
        return cached || fetchPromise;
      })
    );
    return;
  }

  // HTML pages: network-first with cache fallback
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(async () => {
        // Aug 27 fix: same defensive fallthrough as CSS/JS. Never
        // resolve with undefined — that's what surfaces as
        // TypeError: NetworkError on the calling page.
        const cached = await caches.match(event.request);
        if (cached) return cached;
        const shell = await caches.match('/app/index.html');
        return shell || Response.error();
      })
  );
});