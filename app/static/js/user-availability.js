/**
 * User Profile → My Availability tab
 * ===================================
 * Member-level blackout dates (sibling to artist-level blackouts).
 * Backed by /api/me/availability (CRUD) — see backend/routes/availability.py.
 *
 * Soft warning model: blackouts here do NOT hard-block bookings — they
 * surface at booking time as "Jim Smith has these days blocked. Book anyway?"
 * Hard blocks remain at the artist level (artist_availability).
 */
(function () {
  'use strict';

  let _uaMyArtists = [];
  // ID of the blackout currently in edit mode (null when adding new).
  let _uaEditingId = null;
  // GfDatePicker instances — created lazily on first form open so
  // gf-date-picker.js has time to define window.GfDatePicker.
  let _uaStartPicker = null;
  let _uaEndPicker = null;

  function _uaBootPickers() {
    if (typeof window.GfDatePicker !== 'function') return;
    const s = document.getElementById('uaStartDate');
    const e = document.getElementById('uaEndDate');
    if (s && !_uaStartPicker) _uaStartPicker = new window.GfDatePicker(s);
    if (e && !_uaEndPicker)   _uaEndPicker   = new window.GfDatePicker(e);
    // 2026-08-07: when the user picks a From date, jump the To
    // picker's initial month to the same month so they don't have to
    // page forward from today.
    if (s && !s._gfSyncBound) {
      s._gfSyncBound = true;
      s.addEventListener('change', () => {
        const iso = _uaStartPicker && _uaStartPicker.getISO ? _uaStartPicker.getISO() : '';
        if (!iso) return;
        // Auto-default End to the same day when it's empty or before Start
        const endIso = _uaEndPicker && _uaEndPicker.getISO ? _uaEndPicker.getISO() : '';
        if (!endIso || endIso < iso) {
          if (_uaEndPicker && _uaEndPicker.setISO) _uaEndPicker.setISO(iso);
        }
        // Force End picker to open on the From month next time.
        if (_uaEndPicker) {
          const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
          if (m) _uaEndPicker.viewDate = new Date(+m[1], +m[2] - 1, 1);
        }
      });
    }
  }

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
    _uaBootPickers();
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
      wrap.innerHTML = '<p style="color:var(--text-gray);font-size:0.78rem;margin:0;">You\'re not a member of any artists yet. Add the blackout against "All My Artists" and it will apply once you join an artist.</p>';
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

  // Cache the last-fetched rows so the Edit button can find the source
  // row by id without re-querying the DOM/backend.
  let _uaRowsCache = [];

  function _renderUserBlackouts(rows) {
    _uaRowsCache = rows.slice();
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
                  ? '🎸 ' + _esc(r.artist_name || 'Artist #' + r.artist_id)
                  : 'All My Artists'}
                ${r.reason ? ' · ' + _esc(r.reason) : ''}
              </div>
            </div>
            <button onclick="uaEdit(${r.id})"
              style="background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.35);color:var(--cyan);border-radius:4px;padding:4px 10px;font-size:0.78rem;cursor:pointer;">
              Edit
            </button>
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
      wrap.innerHTML = '<p style="color:var(--text-gray);">No artist-wide blackouts.</p>';
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

  // Legacy hook kept for any inline onchange handler that still calls
  // it. The GfDatePicker 'change' listener bound in _uaBootPickers now
  // handles both the End auto-default and the To-picker month sync.
  window.uaSyncEndDate = function () { /* handled by picker listener */ };

  // Two checkboxes acting as mutually-exclusive options (styled as
  // checkboxes per UI request — semantically a radio pair). Clicking
  // either makes it the active one and unchecks the other; clicking
  // the active one re-asserts it (can't leave both unchecked).
  window.uaSetScope = function (which) {
    const all  = document.getElementById('uaScopeAll');
    const spec = document.getElementById('uaScopeSpecific');
    const list = document.getElementById('uaArtistChecks');
    if (!all || !spec) return;
    if (which === 'all') {
      all.checked = true;
      spec.checked = false;
    } else {
      all.checked = false;
      spec.checked = true;
    }
    if (list) list.style.display = spec.checked ? '' : 'none';
  };

  // Reveal / hide the "Other" free-text field based on the dropdown
  // selection. Called from the <select onchange="uaReasonChanged()">.
  window.uaReasonChanged = function () {
    const preset = document.getElementById('uaReasonPreset');
    const other  = document.getElementById('uaReasonOther');
    if (!preset || !other) return;
    other.style.display = (preset.value === 'Other') ? '' : 'none';
    if (preset.value === 'Other') { other.focus(); }
    else { other.value = ''; }
  };

  // Read the current ISO value from either the GfDatePicker (preferred)
  // or the raw input (fallback for tests / old code paths).
  function _isoFrom(pickerRef, inputId) {
    if (pickerRef && typeof pickerRef.getISO === 'function') return pickerRef.getISO();
    const v = (document.getElementById(inputId)?.value || '').trim();
    // Accept the mm/dd/yyyy display format the picker writes.
    const m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (m) return `${m[3]}-${String(m[1]).padStart(2, '0')}-${String(m[2]).padStart(2, '0')}`;
    return v.match(/^\d{4}-\d{2}-\d{2}$/) ? v : '';
  }

  window.uaAddBlackout = async function () {
    _uaBootPickers();  // idempotent — safe if pickers already exist
    const start = _isoFrom(_uaStartPicker, 'uaStartDate');
    const end   = _isoFrom(_uaEndPicker,   'uaEndDate');
    // Reason composed from the preset dropdown + optional freetext.
    // Backend stores it as a single reason string (unchanged schema).
    const preset = (document.getElementById('uaReasonPreset')?.value || '').trim();
    const other  = (document.getElementById('uaReasonOther')?.value || '').trim();
    const reason = preset === 'Other'
      ? other
      : preset;
    const status = document.getElementById('uaAddStatus');
    const scope = document.getElementById('uaScopeSpecific')?.checked ? 'specific' : 'all';
    let artistIds = null;
    if (scope === 'specific') {
      artistIds = Array.from(document.querySelectorAll('.ua-artist-cb:checked'))
        .map(cb => parseInt(cb.value, 10)).filter(Boolean);
      if (!artistIds.length) {
        status.textContent = 'Pick at least one artist, or switch to "All My Artists".';
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
      // 2026-08-07: unified add/edit path. When _uaEditingId is set,
      // PUT the update; otherwise POST a new blackout. Scope changes
      // aren't part of the PUT contract (backend only takes dates +
      // reason on edit) — that's an intentional narrowing: change the
      // scope by deleting and re-adding. Keeps the edit UX simple.
      const isEdit = !!_uaEditingId;
      const url = isEdit
        ? `/api/me/availability/${_uaEditingId}`
        : '/api/me/availability';
      const body = isEdit
        ? { blackout_start: start, blackout_end: end || start, reason: reason }
        : {
            blackout_start: start,
            blackout_end: end || start,
            reason: reason,
            artist_ids: artistIds,  // null = all my artists
          };
      const res = await fetch(url, {
        method: isEdit ? 'PUT' : 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || 'Save failed');
      }
      status.textContent = isEdit ? '✓ Updated.' : '✓ Saved.';
      status.style.color = '#22c55e';
      _uaResetForm();
      await uaLoad();
      setTimeout(() => { status.textContent = ''; }, 2500);
    } catch (e) {
      status.textContent = '✗ ' + e.message;
      status.style.color = '#ef4444';
    }
  };

  // Populate the form with an existing row's values so the user can
  // save changes in place. Editing scope (artist_ids) is intentionally
  // not supported on the PUT contract — the note next to the button
  // tells the user to delete + re-add if they want to change scope.
  window.uaEdit = function (id) {
    // 2026-08-26: defensive lookup + visible feedback so the Edit
    // button is never a silent no-op. Common failure modes:
    //   • row cache empty because the initial fetch is still in flight
    //     when the user clicks Edit → surface a message instead of dying
    //   • id type drift (JSON string vs number) → compare via Number()
    //   • date pickers not yet booted (GfDatePicker script race) →
    //     bail out with a message rather than a picker no-op
    _uaBootPickers();
    const row = _uaRowsCache.find(r => Number(r.id) === Number(id));
    const status = document.getElementById('uaAddStatus');
    if (!row) {
      if (status) {
        status.textContent = 'Blackout not loaded yet — try again in a moment.';
        status.style.color = '#ef4444';
      }
      console.warn('[user-availability] uaEdit: row not found for id', id, '— cache size', _uaRowsCache.length);
      return;
    }
    _uaEditingId = Number(id);
    if (_uaStartPicker && _uaStartPicker.setISO) {
      _uaStartPicker.setISO(row.blackout_start || '');
    } else {
      const s = document.getElementById('uaStartDate');
      if (s) s.value = _fmtDateUS(row.blackout_start || '');
    }
    if (_uaEndPicker && _uaEndPicker.setISO) {
      _uaEndPicker.setISO(row.blackout_end || row.blackout_start || '');
    } else {
      const e = document.getElementById('uaEndDate');
      if (e) e.value = _fmtDateUS(row.blackout_end || row.blackout_start || '');
    }
    // Reason: try to match a preset; anything unknown → "Other" +
    // populate the freetext so the user sees exactly what's stored.
    const presetEl = document.getElementById('uaReasonPreset');
    const otherEl  = document.getElementById('uaReasonOther');
    const KNOWN = ['Out of Town','Family Commitment','Sick','Work Conflict','Other Gig','Vacation'];
    if (presetEl) {
      if (!row.reason) { presetEl.value = ''; }
      else if (KNOWN.indexOf(row.reason) !== -1) { presetEl.value = row.reason; }
      else { presetEl.value = 'Other'; }
    }
    if (otherEl) {
      if (presetEl && presetEl.value === 'Other') {
        otherEl.value = row.reason || '';
        otherEl.style.display = '';
      } else {
        otherEl.value = '';
        otherEl.style.display = 'none';
      }
    }
    _uaUpdateEditChrome();
    if (status) {
      status.textContent = `Editing ${_rangeLabel(row.blackout_start, row.blackout_end)} — change dates or reason above and click Save Changes.`;
      status.style.color = 'var(--cyan)';
    }
    // Scroll the whole Add-a-Blackout form card into view (not just
    // the input) so the user sees both the populated pickers AND the
    // Save Changes button in one glance — using `block: 'start'`
    // instead of 'center' so the header + fields stay visible.
    const card = document.getElementById('uaStartDate')?.closest('div[style*="padding:18px"]')
              || document.getElementById('uaStartDate');
    if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  window.uaCancelEdit = function () {
    _uaResetForm();
  };

  // Clears form + resets edit state. Called by cancel + after save.
  function _uaResetForm() {
    _uaEditingId = null;
    if (_uaStartPicker && _uaStartPicker.setISO) _uaStartPicker.setISO('');
    if (_uaEndPicker   && _uaEndPicker.setISO)   _uaEndPicker.setISO('');
    const presetEl = document.getElementById('uaReasonPreset');
    const otherEl  = document.getElementById('uaReasonOther');
    if (presetEl) presetEl.value = '';
    if (otherEl)  { otherEl.value = ''; otherEl.style.display = 'none'; }
    if (typeof window.uaSetScope === 'function') window.uaSetScope('all');
    _uaUpdateEditChrome();
  }

  // Swap the Add button label + show/hide the Cancel button depending
  // on edit mode. Called after each state change.
  function _uaUpdateEditChrome() {
    const btn = document.querySelector('button[onclick="uaAddBlackout()"]');
    if (btn) btn.textContent = _uaEditingId ? 'Save Changes' : 'Add Blackout Date';
    let cancel = document.getElementById('uaCancelEditBtn');
    if (_uaEditingId && !cancel) {
      cancel = document.createElement('button');
      cancel.id = 'uaCancelEditBtn';
      cancel.className = 'btn ghost';
      cancel.style.cssText = 'padding:8px 18px;';
      cancel.textContent = 'Cancel';
      cancel.onclick = window.uaCancelEdit;
      btn?.after(cancel);
    } else if (!_uaEditingId && cancel) {
      cancel.remove();
    }
  }

  window.uaDelete = function (id) {
    const doDelete = async () => {
      try {
        const res = await fetch('/api/me/availability/' + id, {
          method: 'DELETE', credentials: 'include'
        });
        if (!res.ok) throw new Error('Delete failed');
        await uaLoad();
      } catch (e) {
        if (window.showErrorModal) {
          window.showErrorModal('Delete failed', e.message || 'Could not delete blackout.');
        } else {
          alert('Failed to delete: ' + e.message);
        }
      }
    };
    if (window.showConfirm) {
      window.showConfirm(
        'Delete Blackout Date?',
        'This blackout date will be removed from your availability.',
        doDelete,
        null,
        { tone: 'warning', confirmLabel: 'Delete', cancelLabel: 'Cancel', confirmStyle: 'danger' }
      );
    } else if (confirm('Delete this blackout date?')) {
      doDelete();
    }
  };

  // Lazy-load on tab activation
  document.addEventListener('click', (e) => {
    if (e.target && e.target.closest && e.target.closest('button[onclick*="availability"]')) {
      setTimeout(uaLoad, 50);
    }
  });
})();
