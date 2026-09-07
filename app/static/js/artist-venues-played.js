/**
 * Venues I've Played — public tab on artist-profile.html
 * ========================================================
 * Fetches GET /api/artists/{aid}/venues-played and renders a grid of
 * venue cards (hero image + name + city, state + gig count + last
 * played date). Ordered most-recent-first.
 *
 * Exposes:
 *   window.renderVenuesPlayed(rootId, artistId)
 *     Called by switchTab('venues') from artist-profile.html. Idempotent:
 *     first call fetches + renders; subsequent calls repaint from cache.
 *   window._venuesPlayedPrimeBadge(artistId)
 *     Fetches count once and paints "(N)" into #venuesPlayedBadge next
 *     to the tab name. Runs on page load so visitors see the count
 *     without having to open the tab — same pattern the Setlist and
 *     Reviews tabs use.
 *
 * Cards link to /<vanity_slug> when the venue has one, otherwise to
 * /app/venue-profile.html?venue_id=N. The card is one <a>; the whole
 * tile is clickable.
 */
(function () {
  'use strict';

  const _cache = {};  // artistId → {venues, total}

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  // "3 gigs" / "1 gig"
  function _gigLabel(n) {
    n = parseInt(n, 10) || 0;
    return n === 1 ? '1 gig' : `${n} gigs`;
  }

  // "Aug 23, 2026" — dates come back as YYYY-MM-DD strings; construct
  // via local-timezone Date so we don't drift a day on UTC parsing.
  function _fmtDate(iso) {
    if (!iso) return '';
    const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return '';
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function _venueHref(v) {
    if (v.vanity_slug) return `/${encodeURIComponent(v.vanity_slug)}`;
    return `/app/venue-profile.html?venue_id=${v.venue_id}`;
  }

  function _card(v) {
    const name = _esc(v.venue_name || 'Unnamed venue');
    const loc = [v.city, v.state].filter(Boolean).map(_esc).join(', ');
    const hero = v.hero_image ? _esc(v.hero_image) : '';
    // Placeholder tile when no hero — a subtle purple/cyan gradient
    // with the first letter of the venue name, so cards never look
    // "broken" for venues that haven't uploaded a photo yet.
    const initial = _esc((v.venue_name || '?').trim().charAt(0).toUpperCase());
    const heroBlock = hero
      ? `<div style="width:100%;aspect-ratio:16/9;background:#0d1220 center/cover no-repeat url('${hero}');"></div>`
      : `<div style="width:100%;aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,rgba(124,107,255,0.15),rgba(6,182,212,0.15));color:rgba(255,255,255,0.55);font-size:2.2rem;font-weight:700;letter-spacing:0.02em;">${initial}</div>`;

    return `
      <a href="${_venueHref(v)}"
        style="display:flex;flex-direction:column;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:10px;overflow:hidden;text-decoration:none;color:inherit;transition:transform 0.15s, border-color 0.15s, box-shadow 0.15s;"
        onmouseenter="this.style.transform='translateY(-2px)';this.style.borderColor='var(--cyan)';this.style.boxShadow='0 6px 18px rgba(6,182,212,0.15)';"
        onmouseleave="this.style.transform='';this.style.borderColor='var(--border)';this.style.boxShadow='';">
        ${heroBlock}
        <div style="padding:10px 12px;">
          <div style="font-weight:600;color:var(--text);font-size:0.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${name}</div>
          ${loc ? `<div style="font-size:0.75rem;color:var(--text-gray);margin-top:2px;">${loc}</div>` : ''}
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:6px;font-size:0.72rem;color:var(--text-muted);">
            <span>${_gigLabel(v.gig_count)}</span>
            ${v.last_played_date ? `<span>Last: ${_fmtDate(v.last_played_date)}</span>` : ''}
          </div>
        </div>
      </a>
    `;
  }

  function _render(rootId, artistId) {
    const root = document.getElementById(rootId);
    if (!root) return;
    const data = _cache[artistId];
    if (!data) return;

    if (!data.venues.length) {
      root.innerHTML = `
        <div style="text-align:center;padding:40px 20px;color:var(--text-gray);">
          <div style="font-size:2rem;margin-bottom:8px;">🎤</div>
          <div>No past gigs on GigsFill yet.</div>
        </div>`;
      return;
    }

    root.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid var(--border);">
        <div style="font-size:0.85rem;color:var(--text);">
          <strong>${data.total}</strong> <span style="color:var(--text-gray);">venue${data.total === 1 ? '' : 's'} played</span>
        </div>
        <div style="font-size:0.72rem;color:var(--text-muted);">Most recent first</div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;">
        ${data.venues.map(_card).join('')}
      </div>
    `;
  }

  async function _fetch(artistId) {
    const res = await fetch(`/api/artists/${artistId}/venues-played`, { credentials: 'include' });
    if (!res.ok) throw new Error('Failed to load venues played');
    return res.json();
  }

  window.renderVenuesPlayed = async function (rootId, artistId) {
    const root = document.getElementById(rootId);
    if (!root) return;
    if (!_cache[artistId]) {
      root.innerHTML = '<div style="text-align:center;padding:32px;color:var(--text-gray);font-size:0.85rem;">Loading venues…</div>';
      try {
        const data = await _fetch(artistId);
        _cache[artistId] = data;
        _updateBadge(artistId, data.total);
      } catch (e) {
        root.innerHTML = '<div style="text-align:center;padding:32px;color:#ef4444;">Couldn\'t load venues.</div>';
        return;
      }
    }
    _render(rootId, artistId);
  };

  function _updateBadge(artistId, total) {
    const el = document.getElementById('venuesPlayedBadge');
    if (!el) return;
    el.textContent = total > 0 ? `(${total})` : '';
  }

  window._venuesPlayedPrimeBadge = async function (artistId) {
    if (!artistId) return;
    try {
      const data = await _fetch(artistId);
      _cache[artistId] = data;
      _updateBadge(artistId, data.total);
    } catch (_) { /* silent — badge stays blank */ }
  };

  // Auto-prime on page load so the (N) count appears without opening
  // the tab. Same wait-for-artist-id pattern as artist-setlist-public.
  document.addEventListener('DOMContentLoaded', () => {
    let tries = 0;
    const tick = () => {
      const aid = window._resolvedArtistId
                || (window._VANITY && window._VANITY.type === 'artist' ? window._VANITY.id : null)
                || (new URLSearchParams(window.location.search)).get('artist_id');
      if (aid) { window._venuesPlayedPrimeBadge(parseInt(aid, 10)); return; }
      if (++tries < 20) setTimeout(tick, 250);
    };
    tick();
  });
})();
