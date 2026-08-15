/**
 * gf-logo-link.js — make the GigsFill logo (`.logo` element) navigate
 * to the user's primary calendar.
 *
 * Behavior (in priority order):
 *   1. Current URL has ?artist_id=N → /app/artist-book-gigs.html?artist_id=N
 *   2. Current URL has ?venue_id=N  → /app/venue-create-gigs.html?venue_id=N
 *   3. Fetch /api/me → logged-in user
 *        - Has at least one artist          → that artist's calendar
 *        - Otherwise has at least one venue → that venue's calendar
 *   4. Not logged in / neither role         → /app/index.html
 *
 * Auto-detecting from the URL first means an entity_users team member
 * viewing a specific artist's page lands back on THAT artist's calendar
 * on logo-click, even if their own primary role is a venue.
 *
 * Idempotent: pages that already wrapped their `.logo` in an `<a>` (e.g.
 * admin.html → venue-create-gigs, support-ticket.html → /) are left
 * alone. Every other `.logo` gets a cursor:pointer + click handler.
 *
 * The destination is computed once on DOMContentLoaded; the /api/me
 * response is cached in sessionStorage so subsequent page loads within
 * the same session don't re-hit the endpoint.
 */
(function () {
  'use strict';

  var HOME = '/app/index.html';
  var CACHE_KEY = 'gf_logo_link_me';
  var CACHE_TTL_MS = 60 * 1000;  // one minute — long enough for a
                                 // typical session's navigation, short
                                 // enough that role changes reflect fast

  function _readMeCache() {
    try {
      var raw = sessionStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || !parsed.ts) return null;
      if (Date.now() - parsed.ts > CACHE_TTL_MS) return null;
      return parsed.body || null;
    } catch (_) { return null; }
  }
  function _writeMeCache(body) {
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify({
        ts: Date.now(), body: body || null,
      }));
    } catch (_) { /* private mode / storage full — silent */ }
  }

  async function _fetchMe() {
    var cached = _readMeCache();
    if (cached !== null) return cached;
    try {
      var res = await fetch('/api/me', { credentials: 'include' });
      if (!res.ok) { _writeMeCache(null); return null; }
      var body = await res.json();
      _writeMeCache(body);
      return body;
    } catch (_) {
      _writeMeCache(null);
      return null;
    }
  }

  function _urlParamInt(name) {
    try {
      var v = new URLSearchParams(window.location.search).get(name);
      var n = parseInt(v, 10);
      return isNaN(n) ? null : n;
    } catch (_) { return null; }
  }

  async function _resolveHref() {
    // Priority 1: scoped to a specific entity via URL param.
    var artistId = _urlParamInt('artist_id');
    if (artistId) return '/app/artist-book-gigs.html?artist_id=' + artistId;
    var venueId = _urlParamInt('venue_id');
    if (venueId) return '/app/venue-create-gigs.html?venue_id=' + venueId;

    // Priority 2: authenticated user's primary role.
    var me = await _fetchMe();
    if (me && me.id) {
      var artists = Array.isArray(me.artists) ? me.artists : [];
      if (artists.length) {
        // Match the signup redirect convention: artist-first for
        // multi-role users. Uses the LOWEST id (first-created) as the
        // stable "primary" artist.
        return '/app/artist-book-gigs.html?artist_id=' + artists[0].id;
      }
      var venues = Array.isArray(me.venues) ? me.venues : [];
      if (venues.length) {
        return '/app/venue-create-gigs.html?venue_id=' + venues[0].id;
      }
    }

    // Priority 3: fallback — logged out or roleless.
    return HOME;
  }

  function _wireOne(el, href) {
    if (!el) return;
    // Already wrapped in an <a>? Leave the existing link alone.
    if (el.closest && el.closest('a')) return;
    el.style.cursor = 'pointer';
    el.setAttribute('role', 'link');
    el.setAttribute('tabindex', '0');
    el.setAttribute('title', 'Go to your calendar');
    var navigate = function () { window.location.href = href; };
    el.addEventListener('click', navigate);
    el.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(); }
    });
  }

  async function _init() {
    var logos = document.querySelectorAll('.logo');
    if (!logos.length) return;
    var href = await _resolveHref();
    logos.forEach(function (el) { _wireOne(el, href); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }
})();
