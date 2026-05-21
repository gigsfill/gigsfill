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

      if (!members.length) {
        wrap.innerHTML = '<p style="color:var(--text-gray);">No member blackouts in the next few months. (Anything members add in their profile will show up here.)</p>';
        return;
      }

      wrap.innerHTML = members.map(m => `
        <div style="margin-bottom:14px;padding:12px 14px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:8px;">
          <div style="font-weight:700;color:var(--text);font-size:0.9rem;margin-bottom:8px;">
            👤 ${_esc(m.name)}
          </div>
          <div style="display:flex;flex-direction:column;gap:5px;">
            ${m.blackouts.map(b => `
              <div style="display:flex;align-items:center;gap:10px;font-size:0.82rem;color:var(--text-gray);">
                <span style="color:var(--text);font-weight:500;min-width:160px;">
                  ${_esc(_rangeLabel(b.blackout_start, b.blackout_end))}
                </span>
                <span style="font-size:0.72rem;color:#f59e0b;background:rgba(245,158,11,0.1);padding:1px 7px;border-radius:4px;border:1px solid rgba(245,158,11,0.25);">
                  ${b.artist_id ? 'This artist only' : 'All their artists'}
                </span>
                ${b.reason ? '<span style="font-style:italic;">' + _esc(b.reason) + '</span>' : ''}
              </div>
            `).join('')}
          </div>
        </div>
      `).join('');
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
