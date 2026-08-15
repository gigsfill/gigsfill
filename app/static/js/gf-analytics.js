/**
 * GigsFill shared public-page analytics helper.
 *
 * Exposes:
 *   window.gfSessionId()            — stable per-tab session id
 *   window.gfTrackEvent(type, data) — fire-and-forget POST to /api/analytics/track
 *   window.gfTrackPageView(kind, extra)  — sugar for {event_type:'page_view', page_type: kind, ...}
 *   window.gfTrackGigClick(gig, extra)   — sugar for gig-bubble clicks; extracts
 *                                          gig_id / venue_id / artist_id / city / state
 *                                          from the row shape used by public
 *                                          calendars so callers don't have to hand-shape.
 *
 * Design:
 *   • Idempotent — safe to include on any page even alongside a page-local
 *     helper. Uses window.gfTrackEvent === undefined guard so a second
 *     include doesn't clobber a prior definition.
 *   • Silent on failure — never throws; a network error can't break the app.
 *   • Session id lives in sessionStorage. Same key ('gf_session_id') as
 *     public-gigs.js was using, so historical joins across pages still work.
 *
 * Backend contract: POST /api/analytics/track with
 *   { event_type, session_id, gig_id?, venue_id?, artist_id?, city?, state?,
 *     event_data? (JSON blob for anything else) }
 * Server anonymizes IP + rate-limits — see [analytics.py `track_event`].
 */
(function () {
  'use strict';
  if (typeof window.gfTrackEvent === 'function') return;  // idempotent

  function gfSessionId() {
    try {
      let id = sessionStorage.getItem('gf_session_id');
      if (!id) {
        id = 'sess_' + Math.random().toString(36).slice(2, 15) + Date.now().toString(36);
        sessionStorage.setItem('gf_session_id', id);
      }
      return id;
    } catch (_) {
      // Private-mode fallback — sessionStorage can throw. Return a
      // best-effort id that lasts for the JS execution context.
      if (!window._gfMemSess) {
        window._gfMemSess = 'sess_' + Math.random().toString(36).slice(2, 15) + Date.now().toString(36);
      }
      return window._gfMemSess;
    }
  }

  function gfTrackEvent(eventType, data) {
    try {
      const body = Object.assign(
        { event_type: eventType, session_id: gfSessionId() },
        data || {}
      );
      fetch('/api/analytics/track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).catch(function () { /* swallow */ });
    } catch (_) { /* never break the app */ }
  }

  function gfTrackPageView(pageKind, extra) {
    var data = Object.assign({}, extra || {});
    data.event_data = Object.assign(
      { page_type: pageKind || 'unknown' },
      (extra && extra.event_data) || {}
    );
    gfTrackEvent('page_view', data);
  }

  // Extract the standard analytics fields off a gig row from any of the
  // public calendars. Handles both real GigsFill gigs and artist
  // external (non-GigsFill) gigs — the latter don't carry a numeric
  // gig_id, so we send it as null and stash the ext_id in event_data
  // so backend can still tell apart individual external gigs.
  function gfTrackGigClick(gig, extra) {
    if (!gig) return;
    var isExternal = !!gig._is_external;
    var gigIdRaw = gig.id;
    // Backend expects `gig_id` as int-or-null; ids like "ext_4" would
    // be rejected. External gigs go through as null + tagged.
    var gigIdInt = (isExternal || typeof gigIdRaw !== 'number')
      ? null
      : gigIdRaw;
    var data = {
      gig_id:    gigIdInt,
      venue_id:  gig.venue_id  || null,
      artist_id: gig.artist_id || null,
      city:      gig.venue_city || gig.city  || null,
      state:     gig.venue_state || gig.state || null,
      event_data: Object.assign({
        gig_status:  gig.status,
        artist_type: gig.artist_type,
        venue_name:  gig.venue_name,
        gig_date:    gig.date,
        is_external: isExternal,
        ext_id:      isExternal ? (gig.ext_id || gig.id) : null,
        source:      (extra && extra.source) || null,
      }, (extra && extra.event_data) || {}),
    };
    gfTrackEvent('gig_click', data);
  }

  window.gfSessionId       = gfSessionId;
  window.gfTrackEvent      = gfTrackEvent;
  window.gfTrackPageView   = gfTrackPageView;
  window.gfTrackGigClick   = gfTrackGigClick;
})();
