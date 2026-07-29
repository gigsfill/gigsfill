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
      <div style="font-size:0.68rem;color:var(--text-gray);margin-top:4px;font-style:italic;">Share this link anywhere — it always opens your ${labelType.toLowerCase()}'s public profile page.</div>
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

  // Public helper: set href on one or more links to the vanity URL for the
  // given entity. Falls back to the legacy ?-query URL if the vanity lookup
  // fails, so the link always works.
  // Usage: window.applyVanityToLinks('artist', 7, ['#artistProfileBtn']);
  window.applyVanityToLinks = function (entityType, entityId, selectors) {
    if (entityType !== 'artist' && entityType !== 'venue') return;
    if (!entityId || !selectors || !selectors.length) return;

    const legacy = entityType === 'artist'
      ? `/app/artist-profile.html?artist_id=${entityId}`
      : `/app/venue-profile.html?venue_id=${entityId}`;

    function setAll(href) {
      selectors.forEach((sel) => {
        document.querySelectorAll(sel).forEach((a) => { a.href = href; });
      });
    }
    // Set the legacy URL immediately so the link is never broken, then
    // upgrade to the vanity URL once the lookup returns.
    setAll(legacy);

    fetch(`/api/vanity/${entityType}/${entityId}`, { credentials: 'include' })
      .then((res) => res.ok ? res.json() : null)
      .then((data) => {
        if (data && data.url) setAll(data.url);
      })
      .catch(() => { /* keep legacy fallback */ });
  };

  // ─── Site-wide legacy → vanity URL rewriter ────────────────────────────────
  // Anywhere the app renders `<a href="/app/artist-profile.html?artist_id=N">`
  // or `<a href="/app/venue-profile.html?venue_id=N">`, this helper rewrites
  // the href to the entity's vanity URL (e.g. `/14cannons`) so users always
  // see + share the pretty URL. The legacy URL still works as a fallback —
  // we only upgrade it; we never break it. Uses `/api/vanity-lookup` which is
  // public + unauth so it works on any page.
  //
  // Strategy: one initial DOM sweep on load, plus a MutationObserver to catch
  // anchors added dynamically (renderers like the calendar, hover cards, gig
  // modals all inject anchors after page load). In-memory cache so we never
  // refetch the same (type, id) twice.
  const _vanityCache = {};  // key "type:id" -> Promise<string|null> (url or null)
  const _LEGACY_RE = /\/app\/(artist|venue)-profile\.html\?(artist_id|venue_id)=(\d+)/i;

  function _lookupVanity(entityType, entityId) {
    const key = entityType + ':' + entityId;
    if (_vanityCache[key]) return _vanityCache[key];
    _vanityCache[key] = fetch(
      `/api/vanity-lookup/${entityType}/${entityId}`,
      { credentials: 'omit' }
    )
      .then(r => r.ok ? r.json() : null)
      .then(d => (d && d.url) ? d.url : null)
      .catch(() => null);
    return _vanityCache[key];
  }

  function _rewriteAnchor(a) {
    if (a.dataset.vanityProcessed) return;
    const href = a.getAttribute('href') || '';
    const m = href.match(_LEGACY_RE);
    if (!m) return;
    a.dataset.vanityProcessed = '1';
    const entityType = m[1];
    const entityId = m[3];
    _lookupVanity(entityType, entityId).then((slugUrl) => {
      if (slugUrl) a.setAttribute('href', slugUrl);
    });
  }

  function _rewriteAllInScope(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('a[href*="profile.html?"]').forEach(_rewriteAnchor);
  }

  function _initVanityRewriter() {
    if (window._vanityRewriterStarted) return;
    window._vanityRewriterStarted = true;
    _rewriteAllInScope(document);
    // MutationObserver catches anchors added by client-side renderers (calendar,
    // gig hover cards, lists, modals, etc.) after the initial paint. We listen
    // for both new nodes AND href attribute changes (some pages reuse the same
    // <a> and just swap href).
    const obs = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === 'childList') {
          m.addedNodes.forEach((n) => {
            if (!(n instanceof Element)) return;
            if (n.tagName === 'A') _rewriteAnchor(n);
            else _rewriteAllInScope(n);
          });
        } else if (m.type === 'attributes' && m.attributeName === 'href' && m.target instanceof Element) {
          // Only re-process if the new href looks legacy (don't loop on our own writes)
          const h = m.target.getAttribute('href') || '';
          if (_LEGACY_RE.test(h)) {
            delete m.target.dataset.vanityProcessed;
            _rewriteAnchor(m.target);
          }
        }
      }
    });
    obs.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['href'],
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initVanityRewriter);
  } else {
    _initVanityRewriter();
  }

  window.initVanityUrlEditor = function (entityType, entityId, opts) {
    if (entityType !== 'artist' && entityType !== 'venue') return;
    if (!entityId) return;

    const host = document.getElementById('vanityUrlSection');
    if (!host) return;

    // Optional: selectors for sibling links (e.g. the "Artist Profile" header
    // button) whose href should track the current vanity URL.
    const linkSelectors = (opts && opts.linkSelectors) || [];
    function syncLinkHrefs() {
      const target = state.fullUrl || (state.slug ? (PUBLIC_BASE + '/' + state.slug) : '');
      if (!target) return;
      linkSelectors.forEach((sel) => {
        document.querySelectorAll(sel).forEach((a) => { a.href = target; });
      });
    }

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
        syncLinkHrefs();
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
            syncLinkHrefs();
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

    // Jul 2026: expose a keyed reload hook so the venue-edit / artist-edit
    // rename autosave can re-fetch the vanity URL immediately after the
    // backend regenerates the slug from the new name.
    window._vanityReloaders = window._vanityReloaders || {};
    window._vanityReloaders[entityType + ':' + entityId] = load;
  };

  // Public helper — called from venue.edit.js / artist.edit.js right
  // after a name field successfully autosaves, so the URL display
  // updates without a page reload.
  window.reloadVanityUrl = function (entityType, entityId) {
    var key = entityType + ':' + entityId;
    var fn = (window._vanityReloaders || {})[key];
    if (typeof fn === 'function') fn();
  };
})();
