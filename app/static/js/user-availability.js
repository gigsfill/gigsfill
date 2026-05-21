/**
 * User Profile → My Availability tab
 * ===================================
 * Member-level blackout dates (sibling to artist-level blackouts).
 * Backed by /api/me/availability (CRUD) — see backend/routes/availability.py.
 *
 * Soft warning model: blackouts here do NOT hard-block bookings — they
 * surface at booking time as "Jim Smith has these days blocked. Book anyway?"
 * Hard blocks remain at the band level (artist_availability).
 */
(function () {
  'use strict';

  let _uaMyArtists = [];

  function _fmtDateUS(s) {
    if (!s) return '';
    const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return s;
    return `${m[2]}/${m[3]}/${m[1]}`;
  }

  function _rangeLabel(start, end) {
    if (!end || start === end) return _fmtDateUS(start);
    return `${_fmtDateUS(start)} – ${_fmtDateUS(end)}`;
  }

  async function uaLoad() {
    try {
      const res = await fetch('/api/me/availability', { credentials: 'include' });
      if (!res.ok) throw new Error('Failed to load');
      const data = await res.json();
      _uaMyArtists = data.my_artists || [];
      _renderArtistChecks();
      _renderUserBlackouts(data.user_blackouts || []);
      _renderBandBlackouts(data.band_blackouts || []);
    } catch (e) {
      console.error('uaLoad:', e);
      const u = document.getElementById('uaUserBlackouts');
      const b = document.getElementById('uaBandBlackouts');
      if (u) u.innerHTML = '<p style="color:#ef4444;">Failed to load.</p>';
      if (b) b.innerHTML = '<p style="color:#ef4444;">Failed to load.</p>';
    }
  }
  window.uaLoad = uaLoad;

  function _renderArtistChecks() {
    const wrap = document.getElementById('uaArtistChecks');
    if (!wrap) return;
    if (!_uaMyArtists.length) {
      wrap.innerHTML = '<p style="color:var(--text-gray);font-size:0.78rem;margin:0;">You\'re not a member of any artists yet. Add the blackout against "All my bands" and it will apply once you join an artist.</p>';
      return;
    }
    wrap.innerHTML = _uaMyArtists.map(a => `
      <label style="display:flex;align-items:center;gap:8px;margin-bottom:5px;cursor:pointer;font-size:0.82rem;color:var(--text);">
        <input type="checkbox" class="ua-artist-cb" value="${a.id}">
        ${_esc(a.name)}
      </label>
    `).join('');
  }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function _renderUserBlackouts(rows) {
    const wrap = document.getElementById('uaUserBlackouts');
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = '<p style="color:var(--text-gray);">No personal blackouts yet.</p>';
      return;
    }
    wrap.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:6px;">
        ${rows.map(r => `
          <div style="display:flex;align-items:center;gap:10px;padding:8px 10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;">
            <div style="flex:1;min-width:0;">
              <div style="font-weight:600;color:var(--text);">${_esc(_rangeLabel(r.blackout_start, r.blackout_end))}</div>
              <div style="font-size:0.78rem;color:var(--text-gray);">
                ${r.artist_id
                  ? '🎸 ' + _esc(r.artist_name || 'Band #' + r.artist_id)
                  : 'All my bands'}
                ${r.reason ? ' · ' + _esc(r.reason) : ''}
              </div>
            </div>
            <button onclick="uaDelete(${r.id})"
              style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:4px 10px;font-size:0.78rem;cursor:pointer;">
              Delete
            </button>
          </div>
        `).join('')}
      </div>
    `;
  }

  function _renderBandBlackouts(rows) {
    const wrap = document.getElementById('uaBandBlackouts');
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = '<p style="color:var(--text-gray);">No band-wide blackouts.</p>';
      return;
    }
    wrap.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:6px;">
        ${rows.map(r => `
          <div style="padding:8px 10px;background:rgba(245,158,11,0.04);border:1px solid rgba(245,158,11,0.18);border-radius:6px;">
            <div style="font-weight:600;color:var(--text);">${_esc(_rangeLabel(r.blackout_start, r.blackout_end))}</div>
            <div style="font-size:0.78rem;color:var(--text-gray);">
              🎸 ${_esc(r.artist_name)}${r.reason ? ' · ' + _esc(r.reason) : ''}
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }

  window.uaToggleScope = function () {
    const scope = document.querySelector('input[name="uaScope"]:checked')?.value;
    const div = document.getElementById('uaArtistChecks');
    if (div) div.style.display = scope === 'specific' ? '' : 'none';
  };

  window.uaAddBlackout = async function () {
    const start = document.getElementById('uaStartDate').value;
    const end   = document.getElementById('uaEndDate').value;
    const reason = (document.getElementById('uaReason').value || '').trim();
    const status = document.getElementById('uaAddStatus');
    const scope = document.querySelector('input[name="uaScope"]:checked')?.value;
    let artistIds = null;
    if (scope === 'specific') {
      artistIds = Array.from(document.querySelectorAll('.ua-artist-cb:checked'))
        .map(cb => parseInt(cb.value, 10)).filter(Boolean);
      if (!artistIds.length) {
        status.textContent = 'Pick at least one band, or switch to "All my bands".';
        status.style.color = '#ef4444';
        return;
      }
    }
    if (!start) {
      status.textContent = 'Pick a start date.';
      status.style.color = '#ef4444';
      return;
    }
    status.textContent = 'Saving…';
    status.style.color = 'var(--text-gray)';
    try {
      const res = await fetch('/api/me/availability', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          blackout_start: start,
          blackout_end: end || start,
          reason: reason,
          artist_ids: artistIds,  // null = all my bands
        })
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || 'Save failed');
      }
      status.textContent = '✓ Saved.';
      status.style.color = '#22c55e';
      // Clear form
      document.getElementById('uaStartDate').value = '';
      document.getElementById('uaEndDate').value = '';
      document.getElementById('uaReason').value = '';
      document.querySelector('input[name="uaScope"][value="all"]').checked = true;
      window.uaToggleScope();
      await uaLoad();
      setTimeout(() => { status.textContent = ''; }, 2500);
    } catch (e) {
      status.textContent = '✗ ' + e.message;
      status.style.color = '#ef4444';
    }
  };

  window.uaDelete = async function (id) {
    if (!confirm('Delete this blackout?')) return;
    try {
      const res = await fetch('/api/me/availability/' + id, {
        method: 'DELETE', credentials: 'include'
      });
      if (!res.ok) throw new Error('Delete failed');
      await uaLoad();
    } catch (e) {
      alert('Failed to delete: ' + e.message);
    }
  };

  // Lazy-load on tab activation
  document.addEventListener('click', (e) => {
    if (e.target && e.target.closest && e.target.closest('button[onclick*="availability"]')) {
      setTimeout(uaLoad, 50);
    }
  });
})();
