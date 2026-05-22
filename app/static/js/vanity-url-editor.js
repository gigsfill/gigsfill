/**
 * Vanity URL editor — drops into the artist-edit + venue-edit pages.
 * Manages the public slug shown above the profile fields.
 *
 * Usage on the host page:
 *   1. Drop in <div id="vanityUrlSection"></div> wherever you want it.
 *   2. <script src="/app/static/js/vanity-url-editor.js?v=1"></script>
 *   3. Call window.initVanityUrlEditor('artist', artistId) on page load.
 *
 * The editor fetches GET /api/vanity/{type}/{id}, renders the current
 * slug + Copy + Edit buttons, and surfaces a live availability check on
 * /api/vanity/check while the user types a new slug.
 */
(function () {
  'use strict';

  const PUBLIC_BASE = (location.host && location.host !== 'localhost')
    ? location.protocol + '//' + location.host
    : 'https://gigsfill.com';

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Debounce so the availability check fires once per ~300ms of typing
  // rather than per-keystroke.
  function _debounce(fn, ms) {
    let t = null;
    return function () {
      const args = arguments;
      clearTimeout(t);
      t = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  // Inline styles since the host pages already have their own CSS systems.
  // Mirrors the look of other field sections (cyan label, dark inputs).
  function _render(state) {
    const { type, id, slug, fullUrl, editing, draftSlug, availability, savingError } = state;
    const labelType = type === 'artist' ? 'Artist' : 'Venue';
    const url = fullUrl || (PUBLIC_BASE + '/' + slug);

    let availabilityNote = '';
    if (editing && draftSlug) {
      if (availability === 'checking') {
        availabilityNote = '<span style="color:var(--text-gray);font-size:0.72rem;">checking…</span>';
      } else if (availability === 'available') {
        availabilityNote = '<span style="color:#22c55e;font-size:0.72rem;">✓ available</span>';
      } else if (availability === 'taken') {
        availabilityNote = '<span style="color:#ef4444;font-size:0.72rem;">✗ taken — try another</span>';
      } else if (availability === 'invalid_format') {
        availabilityNote = '<span style="color:#f59e0b;font-size:0.72rem;">⚠ 2–60 chars, lowercase + numbers + hyphens (no leading/trailing hyphen)</span>';
      }
    }

    const errorNote = savingError
      ? `<div style="color:#ef4444;font-size:0.72rem;margin-top:5px;">${_esc(savingError)}</div>` : '';

    const inner = !editing ? `
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <span style="color:var(--text-gray);font-size:0.78rem;">${labelType} URL:</span>
        <code style="color:var(--cyan);font-size:0.82rem;font-family:monospace;background:rgba(6,182,212,0.06);padding:3px 8px;border-radius:4px;border:1px solid rgba(6,182,212,0.2);">${_esc(url)}</code>
        <button type="button" id="vanityCopyBtn"
          style="padding:4px 12px;background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.4);color:var(--cyan);border-radius:5px;font-size:0.72rem;font-weight:600;cursor:pointer;">📋 Copy</button>
        <button type="button" id="vanityEditBtn"
          style="padding:4px 12px;background:rgba(139,92,246,0.12);border:1px solid rgba(139,92,246,0.4);color:#c4b5fd;border-radius:5px;font-size:0.72rem;font-weight:600;cursor:pointer;">✎ Edit</button>
      </div>
      <div style="font-size:0.68rem;color:var(--text-gray);margin-top:4px;">Share this link anywhere — it always opens this ${labelType.toLowerCase()}'s public page.</div>
    ` : `
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
        <span style="color:var(--text-gray);font-size:0.78rem;font-family:monospace;">${PUBLIC_BASE}/</span>
        <input type="text" id="vanitySlugInput" value="${_esc(draftSlug)}" maxlength="60"
          autocomplete="off" spellcheck="false"
          style="flex:1;min-width:160px;padding:6px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.82rem;font-family:monospace;">
        <button type="button" id="vanitySaveBtn"
          style="padding:5px 14px;background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.45);color:#22c55e;border-radius:5px;font-size:0.72rem;font-weight:600;cursor:pointer;">Save</button>
        <button type="button" id="vanityCancelBtn"
          style="padding:5px 12px;background:rgba(255,255,255,0.04);border:1px solid var(--border);color:var(--text-gray);border-radius:5px;font-size:0.72rem;cursor:pointer;">Cancel</button>
      </div>
      <div style="margin-top:5px;min-height:18px;">${availabilityNote}</div>
      ${errorNote}
    `;

    return `<div style="padding:10px 12px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px;margin-bottom:12px;">${inner}</div>`;
  }

  window.initVanityUrlEditor = function (entityType, entityId) {
    if (entityType !== 'artist' && entityType !== 'venue') return;
    if (!entityId) return;

    const host = document.getElementById('vanityUrlSection');
    if (!host) return;

    const state = {
      type: entityType,
      id: entityId,
      slug: '',
      fullUrl: '',
      editing: false,
      draftSlug: '',
      availability: null,    // null | 'checking' | 'available' | 'taken' | 'invalid_format'
      savingError: '',
    };

    function rerender() {
      host.innerHTML = _render(state);
      _wire();
    }

    async function load() {
      try {
        const res = await fetch(`/api/vanity/${entityType}/${entityId}`, { credentials: 'include' });
        if (!res.ok) {
          host.innerHTML = `<div style="font-size:0.7rem;color:var(--text-gray);padding:8px;">Public URL unavailable.</div>`;
          return;
        }
        const data = await res.json();
        state.slug = data.slug || '';
        state.fullUrl = data.url || '';
        state.editing = false;
        rerender();
      } catch (e) { /* silent — section just won't show */ }
    }

    const checkAvailability = _debounce(async function () {
      const v = state.draftSlug;
      if (!v) { state.availability = null; rerender(); return; }
      // If they typed back to their current slug, mark as available without a round-trip.
      if (v === state.slug) {
        state.availability = 'available';
        rerender();
        return;
      }
      state.availability = 'checking';
      rerender();
      try {
        const res = await fetch('/api/vanity/check', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ slug: v }),
        });
        const json = await res.json();
        if (json.available) state.availability = 'available';
        else if (json.reason === 'invalid_format') state.availability = 'invalid_format';
        else state.availability = 'taken';
        rerender();
      } catch (e) {
        state.availability = null;
        rerender();
      }
    }, 300);

    function _wire() {
      const copyBtn = document.getElementById('vanityCopyBtn');
      if (copyBtn) {
        copyBtn.onclick = () => {
          const url = state.fullUrl || (PUBLIC_BASE + '/' + state.slug);
          navigator.clipboard.writeText(url).then(() => {
            copyBtn.textContent = '✓ Copied!';
            setTimeout(() => { copyBtn.textContent = '📋 Copy'; }, 1500);
          }).catch(() => {
            // Fallback for browsers without clipboard API
            const ta = document.createElement('textarea');
            ta.value = url; document.body.appendChild(ta);
            ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
            copyBtn.textContent = '✓ Copied!';
            setTimeout(() => { copyBtn.textContent = '📋 Copy'; }, 1500);
          });
        };
      }
      const editBtn = document.getElementById('vanityEditBtn');
      if (editBtn) {
        editBtn.onclick = () => {
          state.editing = true;
          state.draftSlug = state.slug;
          state.availability = null;
          state.savingError = '';
          rerender();
          // Focus the input so admin can type immediately
          const input = document.getElementById('vanitySlugInput');
          if (input) { input.focus(); input.select(); }
        };
      }
      const input = document.getElementById('vanitySlugInput');
      if (input) {
        input.oninput = (e) => {
          // Force-lowercase as they type so the live check matches the
          // server's normalization.
          const raw = e.target.value;
          const cleaned = raw.toLowerCase();
          if (raw !== cleaned) {
            e.target.value = cleaned;
          }
          state.draftSlug = cleaned;
          state.savingError = '';
          checkAvailability();
        };
        input.onkeydown = (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            const saveBtn = document.getElementById('vanitySaveBtn');
            if (saveBtn) saveBtn.click();
          } else if (e.key === 'Escape') {
            e.preventDefault();
            const cancelBtn = document.getElementById('vanityCancelBtn');
            if (cancelBtn) cancelBtn.click();
          }
        };
      }
      const cancelBtn = document.getElementById('vanityCancelBtn');
      if (cancelBtn) {
        cancelBtn.onclick = () => {
          state.editing = false;
          state.draftSlug = '';
          state.availability = null;
          state.savingError = '';
          rerender();
        };
      }
      const saveBtn = document.getElementById('vanitySaveBtn');
      if (saveBtn) {
        saveBtn.onclick = async () => {
          const v = state.draftSlug;
          if (!v) return;
          if (state.availability === 'taken') {
            state.savingError = 'That URL is already taken.';
            rerender();
            return;
          }
          if (state.availability === 'invalid_format') {
            state.savingError = 'URL has invalid characters.';
            rerender();
            return;
          }
          saveBtn.disabled = true;
          saveBtn.textContent = 'Saving…';
          try {
            const res = await fetch(`/api/vanity/${entityType}/${entityId}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              credentials: 'include',
              body: JSON.stringify({ slug: v }),
            });
            const json = await res.json();
            if (!res.ok) {
              state.savingError = json.detail || 'Save failed.';
              saveBtn.disabled = false;
              saveBtn.textContent = 'Save';
              rerender();
              return;
            }
            state.slug = json.slug;
            state.fullUrl = json.url;
            state.editing = false;
            state.draftSlug = '';
            state.availability = null;
            state.savingError = '';
            rerender();
          } catch (e) {
            state.savingError = e.message || 'Network error';
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save';
            rerender();
          }
        };
      }
    }

    load();
  };
})();
