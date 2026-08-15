/**
 * Artist Edit → Member Availability (read-only summary)
 * ======================================================
 * Lists each member and their upcoming personal blackouts that
 * apply to this artist (member-level user_availability rows where
 * artist_id IS NULL or matches this artist).
 *
 * Soft warnings only — booking attempts surface these in a confirm
 * modal but don't hard-block. Hard-block lives at the artist level
 * (artist_availability), edited above this section.
 */
(function () {
  'use strict';

  // Current user's id — fetched once so we can decide which rows to
  // decorate with Edit/Delete buttons (only rows the caller owns).
  let _mavCurrentUserId = null;
  async function _mavFetchMe() {
    if (_mavCurrentUserId != null) return _mavCurrentUserId;
    try {
      const r = await fetch('/api/me', { credentials: 'include' });
      if (r.ok) {
        const j = await r.json();
        _mavCurrentUserId = parseInt(j.id, 10) || null;
      }
    } catch (_) { /* silent */ }
    return _mavCurrentUserId;
  }

  function _fmtDateUS(s) {
    if (!s) return '';
    const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return s;
    return `${m[2]}/${m[3]}/${m[1]}`;
  }
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function _rangeLabel(start, end) {
    if (!end || start === end) return _fmtDateUS(start);
    return `${_fmtDateUS(start)} – ${_fmtDateUS(end)}`;
  }

  async function loadMemberAvailability() {
    const wrap = document.getElementById('memberAvailabilityContainer');
    const section = document.getElementById('memberAvailabilitySection');
    if (!wrap || !section) return;
    const params = new URLSearchParams(window.location.search);
    const artistId = params.get('artist_id');
    if (!artistId) return;

    try {
      const res = await fetch(`/api/artists/${artistId}/member-availability`,
                              { credentials: 'include' });
      if (!res.ok) {
        section.style.display = '';
        wrap.innerHTML = '<p style="color:#ef4444;">Failed to load member availability.</p>';
        return;
      }
      const data = await res.json();
      section.style.display = '';
      const members = data.members || [];
      // Resolve the current user id so we can gate Edit/Delete on
      // rows the caller actually owns.
      const meId = await _mavFetchMe();

      // Flatten grouped-by-member response into a single chronological list.
      // Scope (this artist only vs all their artists) is intentionally not
      // displayed — that's private to the member.
      const flat = [];
      members.forEach(m => {
        (m.blackouts || []).forEach(b => {
          flat.push({
            id: b.id,
            user_id: m.user_id,
            name: m.name,
            blackout_start: b.blackout_start,
            blackout_end: b.blackout_end,
            reason: b.reason || '',
          });
        });
      });

      if (!flat.length) {
        wrap.innerHTML = '<p style="color:var(--text-gray);font-size:0.82rem;margin:0;">No upcoming member blackouts. (Anything members add in their profile will show up here.)</p>';
        return;
      }

      // 2026-07-25 rewrite: compact sortable table with Dates | Member |
      // Reason columns. Previous flat list wrapped awkwardly and had no
      // reason column. Sort state persists on the wrap element so click-to-
      // sort round-trips through render() work.
      let sortCol = wrap.dataset.sortCol || 'blackout_start';
      let sortDir = parseInt(wrap.dataset.sortDir || '1', 10);
      const sortIt = () => {
        flat.sort((a, b) => {
          const av = String(a[sortCol] || '').toLowerCase();
          const bv = String(b[sortCol] || '').toLowerCase();
          if (av < bv) return -1 * sortDir;
          if (av > bv) return 1 * sortDir;
          return 0;
        });
      };
      const arrow = c => c !== sortCol ? '' : (sortDir === 1 ? ' ▲' : ' ▼');
      const render = () => {
        sortIt();
        const th = 'padding:5px 10px;text-align:left;font-size:0.68rem;font-weight:700;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;';
        const td = 'padding:5px 10px;font-size:0.78rem;color:var(--text);border-bottom:1px solid rgba(255,255,255,0.03);vertical-align:middle;';
        wrap.innerHTML = `
          <table style="width:100%;border-collapse:collapse;">
            <thead><tr>
              <th style="${th}" data-sort="blackout_start">Dates${arrow('blackout_start')}</th>
              <th style="${th}" data-sort="name">Member${arrow('name')}</th>
              <th style="${th}" data-sort="reason">Reason${arrow('reason')}</th>
              <th style="${th}cursor:default;text-align:right;"></th>
            </tr></thead>
            <tbody>
              ${flat.map((b, i) => {
                const isMine = meId != null && parseInt(b.user_id, 10) === meId;
                // Edit/Delete only render for rows the caller owns —
                // the PUT / DELETE backend endpoints check user_id
                // ownership, so buttons on others' rows would just
                // 403 anyway.
                const actions = isMine
                  ? `<button data-mav-edit="${i}" style="background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.35);color:var(--cyan);border-radius:4px;padding:3px 10px;font-size:0.72rem;cursor:pointer;">Edit</button>
                     <button data-mav-del="${i}" style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:3px 10px;font-size:0.72rem;cursor:pointer;margin-left:4px;">Delete</button>`
                  : '';
                return `
                <tr>
                  <td style="${td}white-space:nowrap;">${_esc(_rangeLabel(b.blackout_start, b.blackout_end))}</td>
                  <td style="${td}">${_esc(b.name)}${isMine ? ' <span style="font-size:0.65rem;color:var(--cyan);opacity:0.7;">(you)</span>' : ''}</td>
                  <td style="${td}color:var(--text-gray);">${_esc(b.reason) || '<span style="opacity:0.4;">—</span>'}</td>
                  <td style="${td}text-align:right;white-space:nowrap;">${actions}</td>
                </tr>`;
              }).join('')}
            </tbody>
          </table>
        `;
        wrap.querySelectorAll('th[data-sort]').forEach(h => {
          h.onclick = () => {
            const col = h.dataset.sort;
            if (col === sortCol) sortDir *= -1;
            else { sortCol = col; sortDir = 1; }
            wrap.dataset.sortCol = sortCol;
            wrap.dataset.sortDir = String(sortDir);
            render();
          };
        });
        wrap.querySelectorAll('button[data-mav-edit]').forEach(btn => {
          btn.onclick = () => _mavEditBlackout(flat[+btn.dataset.mavEdit]);
        });
        wrap.querySelectorAll('button[data-mav-del]').forEach(btn => {
          btn.onclick = () => _mavDeleteBlackout(flat[+btn.dataset.mavDel]);
        });
      };
      render();
    } catch (e) {
      console.error('loadMemberAvailability:', e);
      section.style.display = '';
      wrap.innerHTML = '<p style="color:#ef4444;">Failed to load.</p>';
    }
  }

  // ---------- Delete flow ----------
  async function _mavDeleteBlackout(row) {
    if (!row || !row.id) return;
    const label = _rangeLabel(row.blackout_start, row.blackout_end);
    const doDelete = async () => {
      try {
        const res = await fetch('/api/me/availability/' + row.id, {
          method: 'DELETE', credentials: 'include',
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        loadMemberAvailability();
      } catch (e) {
        alert('Delete failed: ' + (e.message || 'unknown'));
      }
    };
    if (typeof window.showStyledModal === 'function') {
      window.showStyledModal(
        'Delete blackout?',
        '<p style="margin:0 0 12px;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
        'Remove your blackout for <strong>' + _esc(label) + '</strong>?' +
        '</p><p style="margin:0;font-size:0.82rem;color:var(--text-gray);"><strong style="color:#fca5a5;">This cannot be undone.</strong></p>',
        [
          { text: 'Keep it', style: 'ghost' },
          { text: '🗑 Delete', style: 'danger', onClick: () => doDelete() },
        ],
        { size: 'sm', tone: 'error' }
      );
    } else if (confirm('Delete blackout ' + label + '? Cannot be undone.')) {
      doDelete();
    }
  }

  // ---------- Edit flow ----------
  // Small in-place modal with two GfDatePicker fields + reason select.
  // Backend PUT /api/me/availability/{id} is scoped to the row owner,
  // which is enforced upstream by the meId gate on button visibility.
  function _mavEditBlackout(row) {
    if (!row || !row.id) return;
    const OV = 'mavEditOverlay';
    let ov = document.getElementById(OV);
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = OV;
    ov.style.cssText = 'position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,0.72);display:flex;align-items:center;justify-content:center;padding:20px;';
    ov.innerHTML = `
      <div style="background:#1a1f2e;border:1px solid #2a3040;border-radius:14px;width:100%;max-width:460px;box-shadow:0 20px 60px rgba(0,0,0,0.55);">
        <div style="padding:14px 18px;border-bottom:1px solid #2a3040;display:flex;align-items:center;justify-content:space-between;">
          <div style="font-size:0.95rem;font-weight:700;background:linear-gradient(135deg,#8b5cf6,#06b6d4);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;">Edit blackout</div>
          <button id="mavEditClose" style="background:transparent;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;line-height:1;padding:0 4px;" title="Close">✕</button>
        </div>
        <div style="padding:16px 18px;">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
            <label style="display:block;">
              <span style="font-size:0.78rem;color:var(--text-gray);display:block;margin-bottom:4px;">Start date</span>
              <input type="text" readonly class="gf-date-input" id="mavEditStart" placeholder="mm/dd/yyyy" style="width:100%;padding:8px 10px;background:#151b28;border:1px solid #333;border-radius:6px;color:var(--text-white);font-size:0.85rem;cursor:pointer;">
            </label>
            <label style="display:block;">
              <span style="font-size:0.78rem;color:var(--text-gray);display:block;margin-bottom:4px;">End date</span>
              <input type="text" readonly class="gf-date-input" id="mavEditEnd" placeholder="mm/dd/yyyy" style="width:100%;padding:8px 10px;background:#151b28;border:1px solid #333;border-radius:6px;color:var(--text-white);font-size:0.85rem;cursor:pointer;">
            </label>
          </div>
          <label style="display:block;margin-bottom:12px;">
            <span style="font-size:0.78rem;color:var(--text-gray);display:block;margin-bottom:4px;">Reason <span style="font-weight:400;color:var(--text-muted);">(optional)</span></span>
            <input type="text" id="mavEditReason" maxlength="300" placeholder="What's the reason?" style="width:100%;padding:8px 10px;background:#151b28;border:1px solid #333;border-radius:6px;color:var(--text-white);font-size:0.85rem;">
          </label>
          <div id="mavEditStatus" style="font-size:0.78rem;min-height:16px;margin-bottom:8px;"></div>
          <div style="display:flex;gap:10px;justify-content:flex-end;">
            <button id="mavEditCancel" class="btn ghost" style="padding:7px 14px;">Cancel</button>
            <button id="mavEditSave" class="btn primary" style="padding:7px 14px;">Save Changes</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(ov);
    const close = () => { ov.remove(); document.removeEventListener('keydown', esc); };
    const esc = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', esc);
    ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
    document.getElementById('mavEditClose').onclick = close;
    document.getElementById('mavEditCancel').onclick = close;

    // Instantiate pickers, seed with existing values, wire From→To sync.
    let startPicker = null, endPicker = null;
    if (typeof window.GfDatePicker === 'function') {
      const sEl = document.getElementById('mavEditStart');
      const eEl = document.getElementById('mavEditEnd');
      startPicker = new window.GfDatePicker(sEl);
      endPicker   = new window.GfDatePicker(eEl);
      startPicker.setISO(row.blackout_start || '');
      endPicker.setISO(row.blackout_end || row.blackout_start || '');
      sEl.addEventListener('change', () => {
        const iso = startPicker.getISO();
        if (!iso) return;
        const endIso = endPicker.getISO();
        if (!endIso || endIso < iso) endPicker.setISO(iso);
        const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (m) endPicker.viewDate = new Date(+m[1], +m[2] - 1, 1);
      });
    }
    document.getElementById('mavEditReason').value = row.reason || '';

    document.getElementById('mavEditSave').onclick = async () => {
      const status = document.getElementById('mavEditStatus');
      const startIso = startPicker ? startPicker.getISO() : row.blackout_start;
      const endIso   = endPicker   ? endPicker.getISO()   : (row.blackout_end || row.blackout_start);
      const reason   = (document.getElementById('mavEditReason').value || '').trim();
      if (!startIso) {
        status.textContent = 'Pick a start date.';
        status.style.color = '#ef4444';
        return;
      }
      status.textContent = 'Saving…';
      status.style.color = 'var(--text-gray)';
      try {
        const res = await fetch('/api/me/availability/' + row.id, {
          method: 'PUT', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            blackout_start: startIso,
            blackout_end: endIso || startIso,
            reason: reason,
          }),
        });
        if (!res.ok) {
          const j = await res.json().catch(() => ({}));
          throw new Error(j.detail || ('HTTP ' + res.status));
        }
        close();
        loadMemberAvailability();
      } catch (e) {
        status.textContent = '✗ ' + (e.message || 'Save failed');
        status.style.color = '#ef4444';
      }
    };
  }

  // Load when the page is ready — artist-edit's main load is async, so we
  // also fire on a slight delay to ensure the artist_id is in URL.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(loadMemberAvailability, 300));
  } else {
    setTimeout(loadMemberAvailability, 300);
  }
})();
