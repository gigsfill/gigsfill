/**
 * GigsFill Service Worker
 * Caches app shell for offline launch + faster repeat loads.
 * Uses network-first for API calls, cache-first for static assets.
 */

const CACHE_NAME = 'gigsfill-v5';

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

// Fetch strategy
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Skip non-GET requests
  if (event.request.method !== 'GET') return;

  // Skip cross-origin requests (CDNs, Google Fonts, Stripe, etc.)
  if (url.origin !== self.location.origin) return;

  // API calls: network-first (never serve stale API data)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request)
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Static assets (CSS, JS, images, fonts): cache-first
  if (url.pathname.match(/\.(css|js|svg|png|jpg|jpeg|gif|webp|woff2?|ttf|eot)$/)) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        // Return cached version, but also update cache in background
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
      .catch(() => caches.match(event.request).then(cached =>
        cached || caches.match('/app/index.html')
      ))
  );
});