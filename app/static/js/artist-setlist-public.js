/**
 * Setlist tab — public view (artist-profile.html)
 * ================================================
 * Fetches /api/artists/{aid}/setlist and renders:
 *   • header row: song count, sort dropdown, search box (when list >= 20)
 *   • multi-column song grid — auto-sized based on total count
 *   • pagination (50 rows/col × col-count = per-page count)
 *
 * Exposes:
 *   window.renderSetlistPublic(rootId, artistId)
 *     Called by switchTab('setlist') from artist-profile.html. Idempotent —
 *     first call fetches + renders; subsequent calls repaint from cache.
 *   window._setlistPrimeBadge(artistId)
 *     Fetches the count once and paints "(N)" into #setlistBadge next to
 *     the tab name. Called on page load so visitors see the count without
 *     having to open the tab.
 *
 * Smart columns (desktop):
 *      total <= 50  → 1 col
 *   51..100         → 2 col
 *   101+            → 3 col
 * Mobile (≤ 720 px) always renders 1 col regardless of count.
 * Per-page = 50 × col-count, so a 300-song setlist paginates
 * every 150 songs on desktop.
 */
(function () {
  'use strict';

  const _cache = {};  // artistId → {songs, total, fetchedAt}
  const _state = {};  // artistId → {sort, page, query}

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function _columnsFor(total) {
    if (window.matchMedia && window.matchMedia('(max-width: 720px)').matches) return 1;
    if (total <= 50) return 1;
    if (total <= 100) return 2;
    return 3;
  }

  function _perPage(total) { return _columnsFor(total) * 50; }

  function _sortSongs(songs, mode) {
    const copy = songs.slice();
    const norm = s => (s || '').toLocaleLowerCase();
    switch (mode) {
      case 'title':
        copy.sort((a, b) => norm(a.song_title).localeCompare(norm(b.song_title)));
        break;
      case 'artist':
        // Songs missing an original_artist land at the bottom under "—"
        copy.sort((a, b) => {
          const aa = norm(a.original_artist);
          const ba = norm(b.original_artist);
          if (!aa && ba) return 1;
          if (aa && !ba) return -1;
          const byA = aa.localeCompare(ba);
          return byA !== 0 ? byA : norm(a.song_title).localeCompare(norm(b.song_title));
        });
        break;
      case 'custom':
      default:
        copy.sort((a, b) => (a.display_order || 0) - (b.display_order || 0));
    }
    return copy;
  }

  function _filterSongs(songs, query) {
    if (!query) return songs;
    const q = query.toLocaleLowerCase();
    return songs.filter(s =>
      (s.song_title || '').toLocaleLowerCase().includes(q) ||
      (s.original_artist || '').toLocaleLowerCase().includes(q)
    );
  }

  function _renderRow(song) {
    const title = _esc(song.song_title);
    const artist = song.original_artist ? _esc(song.original_artist) : '';
    return `
      <div style="display:flex;align-items:baseline;gap:10px;padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.05);break-inside:avoid;">
        <div style="flex:1;min-width:0;font-weight:500;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${title}</div>
        <div style="flex-shrink:0;font-size:0.78rem;color:var(--text-gray);max-width:45%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${artist}</div>
      </div>`;
  }

  function _render(rootId, artistId) {
    const root = document.getElementById(rootId);
    if (!root) return;
    const data = _cache[artistId];
    if (!data) return;
    const st = _state[artistId];

    if (!data.songs.length) {
      root.innerHTML = `
        <div style="text-align:center;padding:40px 20px;color:var(--text-gray);">
          <div style="font-size:2rem;margin-bottom:8px;">🎵</div>
          <div>No songs added yet.</div>
        </div>`;
      return;
    }

    const sorted = _sortSongs(data.songs, st.sort);
    const filtered = _filterSongs(sorted, st.query);
    const cols = _columnsFor(filtered.length || data.total);
    const per = _perPage(filtered.length || data.total);
    const pageCount = Math.max(1, Math.ceil(filtered.length / per));
    if (st.page > pageCount) st.page = 1;
    const start = (st.page - 1) * per;
    const pageSongs = filtered.slice(start, start + per);

    const showSearch = data.total >= 20;
    const rangeLabel = filtered.length === 0
      ? 'No matches'
      : `${start + 1}–${Math.min(start + per, filtered.length)} of ${filtered.length}`;

    root.innerHTML = `
      <div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--border);">
        <div style="font-size:0.85rem;color:var(--text);">
          <strong>${data.total}</strong> <span style="color:var(--text-gray);">song${data.total === 1 ? '' : 's'}</span>
        </div>
        ${showSearch ? `
          <input type="search" id="setlistSearch_${artistId}" placeholder="Search title or artist…"
            value="${_esc(st.query)}"
            style="flex:1;min-width:180px;max-width:260px;background:#151b28;border:1px solid #333;border-radius:6px;padding:5px 10px;color:var(--text-white);font-size:0.8rem;">
        ` : ''}
        <div style="margin-left:auto;display:flex;align-items:center;gap:8px;font-size:0.78rem;color:var(--text-gray);">
          <span>Sort:</span>
          <select id="setlistSort_${artistId}"
            style="background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:6px;padding:4px 8px;font-size:0.78rem;">
            <option value="custom" ${st.sort === 'custom' ? 'selected' : ''}>Artist's Order</option>
            <option value="title"  ${st.sort === 'title'  ? 'selected' : ''}>Song Title (A-Z)</option>
            <option value="artist" ${st.sort === 'artist' ? 'selected' : ''}>Original Artist (A-Z)</option>
          </select>
        </div>
      </div>

      <div style="column-count:${cols};column-gap:24px;">
        ${pageSongs.map(_renderRow).join('')}
      </div>

      ${pageCount > 1 ? `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:16px;padding-top:12px;border-top:1px solid var(--border);font-size:0.78rem;color:var(--text-gray);">
          <span>${rangeLabel}</span>
          <div style="display:flex;gap:6px;align-items:center;">
            <button ${st.page <= 1 ? 'disabled' : ''}
              onclick="window._setlistPageChange(${artistId}, ${st.page - 1}, '${rootId}')"
              style="padding:4px 10px;background:rgba(255,255,255,0.06);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.75rem;cursor:${st.page <= 1 ? 'not-allowed' : 'pointer'};opacity:${st.page <= 1 ? 0.4 : 1};">◀ Prev</button>
            <span>Page ${st.page} of ${pageCount}</span>
            <button ${st.page >= pageCount ? 'disabled' : ''}
              onclick="window._setlistPageChange(${artistId}, ${st.page + 1}, '${rootId}')"
              style="padding:4px 10px;background:rgba(255,255,255,0.06);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.75rem;cursor:${st.page >= pageCount ? 'not-allowed' : 'pointer'};opacity:${st.page >= pageCount ? 0.4 : 1};">Next ▶</button>
          </div>
        </div>
      ` : `
        <div style="margin-top:12px;font-size:0.72rem;color:var(--text-muted);text-align:right;">${rangeLabel}</div>
      `}
    `;

    // Wire the interactive controls.
    const sortEl = document.getElementById(`setlistSort_${artistId}`);
    if (sortEl) {
      sortEl.addEventListener('change', () => {
        st.sort = sortEl.value;
        st.page = 1;
        _render(rootId, artistId);
      });
    }
    if (showSearch) {
      const searchEl = document.getElementById(`setlistSearch_${artistId}`);
      if (searchEl) {
        // Debounce so every keystroke doesn't repaint a big list.
        let t = null;
        searchEl.addEventListener('input', () => {
          clearTimeout(t);
          t = setTimeout(() => {
            st.query = searchEl.value.trim();
            st.page = 1;
            _render(rootId, artistId);
            const again = document.getElementById(`setlistSearch_${artistId}`);
            if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
          }, 150);
        });
      }
    }
  }

  window._setlistPageChange = function (artistId, page, rootId) {
    if (!_state[artistId]) return;
    _state[artistId].page = page;
    _render(rootId, artistId);
    const card = document.getElementById(rootId);
    if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  async function _fetch(artistId) {
    const res = await fetch(`/api/artists/${artistId}/setlist`, { credentials: 'include' });
    if (!res.ok) throw new Error('Failed to load setlist');
    return res.json();
  }

  window.renderSetlistPublic = async function (rootId, artistId) {
    const root = document.getElementById(rootId);
    if (!root) return;
    if (!_state[artistId]) {
      _state[artistId] = { sort: 'custom', page: 1, query: '' };
    }
    // Cache-miss path shows a spinner; cache-hit re-renders instantly.
    if (!_cache[artistId]) {
      root.innerHTML = '<div style="text-align:center;padding:32px;color:var(--text-gray);font-size:0.85rem;">Loading setlist…</div>';
      try {
        const data = await _fetch(artistId);
        _cache[artistId] = { ...data, fetchedAt: Date.now() };
        _updateBadge(artistId, data.total);
      } catch (e) {
        root.innerHTML = '<div style="text-align:center;padding:32px;color:#ef4444;">Couldn\'t load setlist.</div>';
        return;
      }
    }
    _render(rootId, artistId);
  };

  function _updateBadge(artistId, total) {
    const el = document.getElementById('setlistBadge');
    if (!el) return;
    el.textContent = total > 0 ? `(${total})` : '';
  }

  // Prime the tab badge count on page load without opening the tab.
  window._setlistPrimeBadge = async function (artistId) {
    if (!artistId) return;
    try {
      const data = await _fetch(artistId);
      _cache[artistId] = { ...data, fetchedAt: Date.now() };
      _updateBadge(artistId, data.total);
    } catch (_) { /* silent — badge just stays blank */ }
  };

  // Auto-prime once the page settles. artist-profile.html sets
  // window._resolvedArtistId after loadArtist() completes; if that's
  // already there we prime now, otherwise poll briefly.
  document.addEventListener('DOMContentLoaded', () => {
    let tries = 0;
    const tick = () => {
      const aid = window._resolvedArtistId
                || (window._VANITY && window._VANITY.type === 'artist' ? window._VANITY.id : null)
                || (new URLSearchParams(window.location.search)).get('artist_id');
      if (aid) { window._setlistPrimeBadge(parseInt(aid, 10)); return; }
      if (++tries < 20) setTimeout(tick, 250);
    };
    tick();
  });
})();
