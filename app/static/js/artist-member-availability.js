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

      // Flatten grouped-by-member response into a single chronological list.
      // Scope (this artist only vs all their artists) is intentionally not
      // displayed — that's private to the member.
      const flat = [];
      members.forEach(m => {
        (m.blackouts || []).forEach(b => {
          flat.push({
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
            </tr></thead>
            <tbody>
              ${flat.map(b => `
                <tr>
                  <td style="${td}white-space:nowrap;">${_esc(_rangeLabel(b.blackout_start, b.blackout_end))}</td>
                  <td style="${td}">${_esc(b.name)}</td>
                  <td style="${td}color:var(--text-gray);">${_esc(b.reason) || '<span style="opacity:0.4;">—</span>'}</td>
                </tr>`).join('')}
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
      };
      render();
    } catch (e) {
      console.error('loadMemberAvailability:', e);
      section.style.display = '';
      wrap.innerHTML = '<p style="color:#ef4444;">Failed to load.</p>';
    }
  }

  // Load when the page is ready — artist-edit's main load is async, so we
  // also fire on a slight delay to ensure the artist_id is in URL.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(loadMemberAvailability, 300));
  } else {
    setTimeout(loadMemberAvailability, 300);
  }
})();
