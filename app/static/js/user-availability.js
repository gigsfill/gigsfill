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
  // GfDatePicker instances — created lazily on first form open so
  // gf-date-picker.js has time to define window.GfDatePicker.
  // These are for the ADD form at the top only. Each row's inline
  // edit panel manages its own pickers (attached to elements by id).
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
      // Cache the last-loaded row set so uaSaveEdit can compare the
      // current scope (row.artist_id) against the user's new scope
      // pick without re-fetching. Rendered rows carry dates + reason
      // inline via jsAttr, but scope isn't safe to embed in the
      // onclick attribute (int/null distinction), so we look it up
      // here instead.
      window._uaLastRows = data.user_blackouts || [];
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

  // 2026-08-26: switched to inline row-expand editing (matches the
  // sibling artist-availability.js pattern) so Edit visibly opens a
  // form UNDER the row instead of silently repurposing the top
  // "Add a Blackout Date" form.
  function _renderUserBlackouts(rows) {
    const wrap = document.getElementById('uaUserBlackouts');
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = '<p style="color:var(--text-gray);">No personal blackouts yet.</p>';
      return;
    }
    const jsA = window.jsAttr || JSON.stringify;
    wrap.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:6px;">
        ${rows.map(r => `
          <div id="uaBlackoutRow_${r.id}" style="padding:8px 10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;">
            <div style="display:flex;align-items:center;gap:10px;">
              <div style="flex:1;min-width:0;">
                <div style="font-weight:600;color:var(--text);">${_esc(_rangeLabel(r.blackout_start, r.blackout_end))}</div>
                <div style="font-size:0.78rem;color:var(--text-gray);">
                  ${r.artist_id
                    ? '🎸 ' + _esc(r.artist_name || 'Artist #' + r.artist_id)
                    : 'All My Artists'}
                  ${r.reason ? ' · ' + _esc(r.reason) : ''}
                </div>
              </div>
              <button onclick="uaEdit(${r.id}, ${jsA(r.blackout_start||'')}, ${jsA(r.blackout_end||'')}, ${jsA(r.reason||'')})"
                style="background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.35);color:var(--cyan);border-radius:4px;padding:4px 10px;font-size:0.78rem;cursor:pointer;">
                Edit
              </button>
              <button onclick="uaDelete(${r.id})"
                style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:4px 10px;font-size:0.78rem;cursor:pointer;">
                Delete
              </button>
            </div>
            <div id="uaBlackoutEdit_${r.id}" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--border);">
              <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;">
                <div>
                  <label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:3px;">Start</label>
                  <input type="text" readonly class="gf-date-input" id="uaEditStart_${r.id}" placeholder="mm/dd/yyyy"
                    style="background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:6px;padding:6px 10px;font-size:0.82rem;cursor:pointer;">
                </div>
                <div>
                  <label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:3px;">End</label>
                  <input type="text" readonly class="gf-date-input" id="uaEditEnd_${r.id}" placeholder="mm/dd/yyyy"
                    style="background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:6px;padding:6px 10px;font-size:0.82rem;cursor:pointer;">
                </div>
                <div style="flex:1;min-width:180px;">
                  <label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:3px;">Reason (optional)</label>
                  <input type="text" id="uaEditReason_${r.id}" maxlength="200"
                    style="width:100%;box-sizing:border-box;background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:6px;padding:6px 10px;font-size:0.82rem;">
                </div>
                <button onclick="uaSaveEdit(${r.id})"
                  style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.35);color:#10b981;border-radius:6px;padding:6px 14px;font-size:0.8rem;cursor:pointer;">
                  Save
                </button>
                <button onclick="uaCancelEdit(${r.id})"
                  style="background:none;border:1px solid var(--border);color:var(--text-gray);border-radius:6px;padding:6px 12px;font-size:0.8rem;cursor:pointer;">
                  Cancel
                </button>
              </div>
              <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-top:10px;">
                <span style="font-size:0.72rem;color:var(--text-gray);font-weight:600;">Applies to:</span>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:0.8rem;color:var(--text);">
                  <input type="radio" name="uaEditScope_${r.id}" value="all" ${!r.artist_id ? 'checked' : ''} onchange="uaEditScopeChange(${r.id})">
                  All My Artists
                </label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:0.8rem;color:var(--text);">
                  <input type="radio" name="uaEditScope_${r.id}" value="specific" ${r.artist_id ? 'checked' : ''} onchange="uaEditScopeChange(${r.id})">
                  Specific Artist:
                </label>
                <select id="uaEditArtist_${r.id}"
                  style="background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:6px;padding:4px 8px;font-size:0.78rem;${r.artist_id ? '' : 'opacity:0.5;'}"
                  ${r.artist_id ? '' : 'disabled'}>
                  ${_uaMyArtists.map(a => `<option value="${a.id}" ${Number(r.artist_id) === Number(a.id) ? 'selected' : ''}>${_esc(a.name)}</option>`).join('')}
                </select>
              </div>
              <div style="font-size:0.68rem;color:var(--text-muted);margin-top:6px;line-height:1.4;">
                To spread a blackout across multiple specific artists, use the Add form above (it supports multi-select).
              </div>
              <div id="uaEditMsg_${r.id}" style="font-size:0.75rem;margin-top:6px;"></div>
            </div>
          </div>
        `).join('')}
      </div>
    `;
    // Boot per-row date pickers now. They lazy-render their popup on
    // click, so instantiating on hidden inputs is fine — no wasted DOM.
    _uaBootRowPickers(rows);
  }

  // Instantiate a GfDatePicker on each row's Start/End inputs (once).
  // Wires a From→To auto-fill listener so choosing Start pushes End to
  // the same day when End is empty or behind Start.
  function _uaBootRowPickers(rows) {
    if (typeof window.GfDatePicker !== 'function') return;
    (rows || []).forEach(r => {
      const s = document.getElementById(`uaEditStart_${r.id}`);
      const e = document.getElementById(`uaEditEnd_${r.id}`);
      if (!s || !e) return;
      if (!s._gfP) s._gfP = new window.GfDatePicker(s);
      if (!e._gfP) e._gfP = new window.GfDatePicker(e);
      if (!s._syncBound) {
        s._syncBound = true;
        s.addEventListener('change', () => {
          const iso = s._gfP.getISO();
          if (!iso) return;
          const endIso = e._gfP.getISO();
          if (!endIso || endIso < iso) e._gfP.setISO(iso);
          const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
          if (m) e._gfP.viewDate = new Date(+m[1], +m[2] - 1, 1);
        });
      }
    });
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
      const res = await fetch('/api/me/availability', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          blackout_start: start,
          blackout_end: end || start,
          reason: reason,
          artist_ids: artistIds,  // null = all my artists
        }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || 'Save failed');
      }
      status.textContent = '✓ Saved.';
      status.style.color = '#22c55e';
      _uaResetAddForm();
      await uaLoad();
      setTimeout(() => { status.textContent = ''; }, 2500);
    } catch (e) {
      status.textContent = '✗ ' + e.message;
      status.style.color = '#ef4444';
    }
  };

  // Clear the top Add form after a successful POST. (Inline edit uses
  // its own per-row inputs — nothing to reset here for edit.)
  function _uaResetAddForm() {
    if (_uaStartPicker && _uaStartPicker.setISO) _uaStartPicker.setISO('');
    if (_uaEndPicker   && _uaEndPicker.setISO)   _uaEndPicker.setISO('');
    const presetEl = document.getElementById('uaReasonPreset');
    const otherEl  = document.getElementById('uaReasonOther');
    if (presetEl) presetEl.value = '';
    if (otherEl)  { otherEl.value = ''; otherEl.style.display = 'none'; }
    if (typeof window.uaSetScope === 'function') window.uaSetScope('all');
  }

  // Expand the inline edit panel under a row and pre-fill its inputs.
  // The row's Start/End/Reason values are passed straight from the
  // render (attribute-escaped via jsAttr) so this never needs to
  // consult a cache or re-fetch — click-time is deterministic.
  window.uaEdit = function (id, startIso, endIso, reason) {
    // Collapse any other open edit panel so only one is open at a time.
    document.querySelectorAll('[id^="uaBlackoutEdit_"]').forEach(el => {
      if (el.id !== `uaBlackoutEdit_${id}`) el.style.display = 'none';
    });
    const panel = document.getElementById(`uaBlackoutEdit_${id}`);
    if (!panel) return;
    panel.style.display = 'block';
    const sEl = document.getElementById(`uaEditStart_${id}`);
    const eEl = document.getElementById(`uaEditEnd_${id}`);
    const rEl = document.getElementById(`uaEditReason_${id}`);
    if (sEl) { if (sEl._gfP && sEl._gfP.setISO) sEl._gfP.setISO(startIso || ''); else sEl.value = startIso || ''; }
    if (eEl) { if (eEl._gfP && eEl._gfP.setISO) eEl._gfP.setISO(endIso || startIso || ''); else eEl.value = endIso || startIso || ''; }
    if (rEl) rEl.value = reason || '';
    const msg = document.getElementById(`uaEditMsg_${id}`);
    if (msg) { msg.textContent = ''; msg.style.color = ''; }
    // Bring the panel into view so the user sees the inputs + Save
    // button without having to scroll.
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  // Collapse the inline edit panel for this row without saving.
  window.uaCancelEdit = function (id) {
    const panel = document.getElementById(`uaBlackoutEdit_${id}`);
    if (panel) panel.style.display = 'none';
  };

  // Enable / disable the per-row artist <select> based on which scope
  // radio was picked. When "All My Artists" is selected the select is
  // greyed out; when "Specific Artist" is selected the select becomes
  // active. Keeps the intent obvious visually.
  window.uaEditScopeChange = function (id) {
    const radios = document.getElementsByName(`uaEditScope_${id}`);
    const specific = Array.from(radios).find(r => r.value === 'specific' && r.checked);
    const sel = document.getElementById(`uaEditArtist_${id}`);
    if (!sel) return;
    if (specific) {
      sel.disabled = false;
      sel.style.opacity = '1';
    } else {
      sel.disabled = true;
      sel.style.opacity = '0.5';
    }
  };

  // Save the row's dates + reason + scope. Scope changes require a
  // delete + re-post since the DB row's artist_id is part of the key
  // ("All My Artists" = NULL, "specific" = artist id). If scope hasn't
  // changed we just PUT dates + reason, preserving the row id.
  window.uaSaveEdit = async function (id) {
    const sEl = document.getElementById(`uaEditStart_${id}`);
    const eEl = document.getElementById(`uaEditEnd_${id}`);
    const rEl = document.getElementById(`uaEditReason_${id}`);
    const msg = document.getElementById(`uaEditMsg_${id}`);
    const start = _isoFrom(sEl && sEl._gfP, `uaEditStart_${id}`);
    const end   = _isoFrom(eEl && eEl._gfP, `uaEditEnd_${id}`) || start;
    const reason = (rEl?.value || '').trim();
    if (!start) {
      if (msg) { msg.textContent = 'Pick a start date.'; msg.style.color = '#ef4444'; }
      return;
    }
    if (end < start) {
      if (msg) { msg.textContent = 'End must be on or after Start.'; msg.style.color = '#ef4444'; }
      return;
    }

    // Read the new scope from the radio + select.
    const scopeRadio = Array.from(document.getElementsByName(`uaEditScope_${id}`)).find(r => r.checked);
    const scope = scopeRadio ? scopeRadio.value : 'all';
    const newArtistId = scope === 'specific'
      ? parseInt(document.getElementById(`uaEditArtist_${id}`)?.value || '', 10) || null
      : null;
    if (scope === 'specific' && !newArtistId) {
      if (msg) { msg.textContent = 'Pick a specific artist.'; msg.style.color = '#ef4444'; }
      return;
    }

    // Look up the row's current artist_id in the last-loaded snapshot
    // (we have `_uaMyArtists` for names; the actual scope came in via
    // the API response, which uaLoad() cached on window as _uaLastRows).
    const cur = (window._uaLastRows || []).find(x => Number(x.id) === Number(id));
    const curArtistId = cur ? (cur.artist_id || null) : null;
    const scopeChanged = Number(curArtistId || 0) !== Number(newArtistId || 0);

    if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-gray)'; }
    try {
      if (!scopeChanged) {
        // Dates + reason only — one PUT.
        const res = await fetch(`/api/me/availability/${id}`, {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ blackout_start: start, blackout_end: end, reason }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || 'Save failed');
        }
      } else {
        // Scope switched — the backend PUT can't move a row across
        // (NULL ↔ specific) or between specific artists because
        // artist_id is set at INSERT time. DELETE + POST atomically
        // from the user's perspective: if the DELETE succeeds and the
        // POST fails, we surface the error and the user can retry
        // (they'll see the row gone and can re-add via the top form).
        const del = await fetch(`/api/me/availability/${id}`, {
          method: 'DELETE', credentials: 'include',
        });
        if (!del.ok) {
          const err = await del.json().catch(() => ({}));
          throw new Error(err.detail || 'Delete step failed');
        }
        const post = await fetch('/api/me/availability', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            blackout_start: start,
            blackout_end: end,
            reason,
            artist_ids: newArtistId ? [newArtistId] : null,
          }),
        });
        if (!post.ok) {
          const err = await post.json().catch(() => ({}));
          throw new Error(err.detail || 'Save step failed (scope changed; original row already removed — please re-add)');
        }
      }
      await uaLoad();
    } catch (e) {
      if (msg) { msg.textContent = '✗ ' + e.message; msg.style.color = '#ef4444'; }
    }
  };

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
