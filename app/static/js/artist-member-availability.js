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
          });
        });
      });
      flat.sort((a, b) => String(a.blackout_start).localeCompare(String(b.blackout_start)));

      if (!flat.length) {
        wrap.innerHTML = '<p style="color:var(--text-gray);">No upcoming member blackouts. (Anything members add in their profile will show up here.)</p>';
        return;
      }

      wrap.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:4px;font-size:0.85rem;">
          ${flat.map(b => `
            <div style="display:flex;align-items:center;gap:18px;padding:6px 10px;background:rgba(255,255,255,0.02);border-radius:6px;">
              <span style="color:var(--text);font-weight:500;min-width:210px;">
                ${_esc(_rangeLabel(b.blackout_start, b.blackout_end))}
              </span>
              <span style="color:var(--text);">${_esc(b.name)}</span>
            </div>
          `).join('')}
        </div>
      `;
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
