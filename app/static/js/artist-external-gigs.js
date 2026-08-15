/**
 * Artist external-gig CRUD + calendar injection
 * ==============================================
 * Artists log gigs they booked at venues that AREN'T on GigsFill so their
 * calendar + public profile reflect a full schedule. Never surfaces on
 * venue calendars or the city public calendar — purely artist-owned.
 *
 * State: window._extGigs = array of external-gig rows for the current
 * artist. Refreshed via loadExternalGigs() on init + after every save/
 * delete. The artist.book-gigs.js day-cell click handler defers to our
 * openAddExternalGigModal() (2026-08-01 swap: replaced openDayGigsModal).
 *
 * Modal (2026-08-01):
 *   • .modal-clean chrome matches the venue Create Gig modal so the
 *     visual language is consistent (purple/cyan gradient separator,
 *     .modal-row grid layout).
 *   • Date: GfDatePicker (shared dark popup — see gf-date-picker.js).
 *   • State: <select> populated from window.US_STATES once on first open.
 *   • City: initCityAutocomplete → auto-fills state on match.
 *   • Venue Name: local autocomplete against /api/venues/public — on pick
 *     auto-fills address/city/state.
 *   • Times default to 19:00/22:00; end-blur silently +12h if end < start
 *     AND end < 12 (PM typo).
 *   • Artist Type / Lineup / Styles: pill selectors mirroring venue create
 *     modal's artist-type filter row. Lineup + Styles reveal only when
 *     Live Band is active.
 *
 * Rendering: after the main calendar renders, injectExternalBubbles()
 * walks .calendar-day cells and appends a bubble per external gig with
 * a distinct grey/muted style + "EXT" tag so users can tell them apart
 * from real GigsFill gigs.
 */
(function () {
  'use strict';

  const STYLE_OPTIONS = ['Country','Hip-Hop','Indie','Jazz','Latin','Pop','Reggae','Rock'];

  let _artistId = null;
  let _artistName = null;
  let _artistDefaults = null;     // { artist_type, lineup[], styles[] } from artist profile
  let _extGigs = [];              // cached: current artist's external gigs
  let _editingId = null;          // non-null when the modal is in edit mode
  let _initedChrome = false;      // one-shot: state dropdown + autocompletes
  let _venuesCache = null;        // GigsFill venues, fetched lazily
  let _datePicker = null;         // GfDatePicker instance on extGigDate

  // Flyer modal state — populated in _initChrome() bindings + reset on modal
  // open. Three concerns:
  //   • _flyerPending: File object staged by the user but not yet uploaded
  //     (upload deferred until Save fires — new gigs don't have an id yet,
  //     and even for edits we want save-atomicity in the perceived flow).
  //   • _flyerRemovePending: user hit "Remove flyer" on an existing gig
  //     — we DELETE the flyer after the main save succeeds.
  //   • _flyerCurrentUrl: the currently-persisted flyer URL for this gig
  //     (populated when opening the edit modal), so the preview shows the
  //     existing image without a re-fetch.
  let _flyerPending = null;
  let _flyerRemovePending = false;
  let _flyerCurrentUrl = null;

  // ── PUBLIC INIT ─────────────────────────────────────────────────────
  window.initExternalGigs = async function (artistId, artistName, defaults) {
    _artistId = parseInt(artistId, 10);
    _artistName = artistName || '';
    _artistDefaults = defaults || null;
    if (!_artistId) return;
    await loadExternalGigs();
    _watchCalendar();
  };

  // ── LOAD ────────────────────────────────────────────────────────────
  async function loadExternalGigs() {
    if (!_artistId) return;
    try {
      const r = await fetch(`/api/artists/${_artistId}/external-gigs`, { credentials: 'include' });
      if (!r.ok) { _extGigs = []; return; }
      _extGigs = await r.json();
    } catch (e) {
      console.warn('[extGigs] load failed:', e);
      _extGigs = [];
    }
    injectExternalBubbles();
  }
  window.reloadExternalGigs = loadExternalGigs;

  // ── CALENDAR INJECTION ──────────────────────────────────────────────
  // Watch ONLY the direct children of the #calendar element (subtree:false).
  // artist.book-gigs.js re-renders by clearing calendarEl and appending
  // fresh cells — a direct-children mutation — so we still catch it.
  // Watching the whole subtree would fire on our own appendChild inside
  // .gigs-container, kicking off an inject → mutation → inject loop at
  // 60fps that constantly tore down + re-created the bubble, eating clicks.
  let _mo = null;
  let _injecting = false;
  function _watchCalendar() {
    const cal = document.getElementById('calendar');
    if (!cal || _mo) return;
    _mo = new MutationObserver(() => {
      if (_injecting) return;  // ignore our own mutations
      if (_mo._raf) cancelAnimationFrame(_mo._raf);
      _mo._raf = requestAnimationFrame(injectExternalBubbles);
    });
    _mo.observe(cal, { childList: true, subtree: false });
    injectExternalBubbles();
  }

  function injectExternalBubbles() {
    if (!_artistId) return;
    const byDate = {};
    _extGigs.forEach(g => {
      if (!g.date) return;
      (byDate[g.date] = byDate[g.date] || []).push(g);
    });
    _injecting = true;
    try {
      ['calendar', 'calendar2'].forEach(calId => {
        const cal = document.getElementById(calId);
        if (!cal) return;
        // Wipe ALL existing external bubbles calendar-wide first, then
        // re-inject only the current set. Previously this only removed
        // bubbles on cells whose dates were in the fresh list — so a
        // deleted gig's bubble stayed in the DOM until a full calendar
        // re-render fired. Also had an early-return when _extGigs was
        // empty which left the last remaining bubble stranded after
        // deleting the sole external gig.
        cal.querySelectorAll('.ext-gig-bubble').forEach(el => el.remove());
        if (!_extGigs.length) return;
        const cells = cal.querySelectorAll('.calendar-day[data-date]');
        cells.forEach(cell => {
          const date = cell.getAttribute('data-date');
          if (!date || !byDate[date]) return;
          const container = cell.querySelector('.gigs-container') || cell;
          byDate[date].forEach(g => container.appendChild(_bubble(g)));
        });
      });
    } finally {
      _injecting = false;
    }
  }

  // Local copy of formatTime12Hour — artist.book-gigs.js keeps its own
  // inside an IIFE (not global) so we can't reuse it. Same logic:
  // 19:00 → 7:00 PM; 00:00 → 12:00 AM.
  function _fmtTime12(timeStr) {
    if (!timeStr) return '';
    const [h, m] = timeStr.split(':').map(Number);
    const period = h >= 12 ? 'PM' : 'AM';
    const hour = ((h + 11) % 12) + 1;
    return `${hour}:${String(m).padStart(2, '0')} ${period}`;
  }

  // Icon lookup mirrors artist.book-gigs.js:1083 exactly so external-gig
  // bubbles pick the same emoji per Artist Type as booked-mine bubbles.
  const _ICONS = {
    'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤',
    'Trivia Host': '🧠', 'Open Mic MC': '🎙️', 'Karaoke MC': '🎶',
  };

  function _bubble(g) {
    const d = document.createElement('div');
    // `gig booked-mine` classes → picks up the electric-cyan gradient
    // from artist-book-gigs.html:656 (.calendar .gig.booked-mine). Also
    // tag the element as ext-gig-bubble so injectExternalBubbles() can
    // find and dedupe on re-render.
    d.className = 'gig booked-mine ext-gig-bubble';
    const time = _fmtTime12(g.start_time || '');
    const icon = _ICONS[g.artist_type] || '🎵';
    const venueLine = g.venue_name || '(venue)';
    d.textContent = `${icon} ${time ? time + ' · ' : ''}${venueLine}`;
    d.title = [
      venueLine + (time ? '  ' + time : ''),
      [g.venue_city, g.venue_state].filter(Boolean).join(', '),
      g.notes ? '\n' + g.notes : '',
    ].filter(Boolean).join('\n');
    // Explicit window. reference — a bare identifier is looked up via
    // the global object in browsers but was unreliable in some contexts
    // during earlier iterations. `window.openEditExternalGigModal` is
    // always defined by the time this bubble is clicked.
    d.addEventListener('click', (e) => {
      e.stopPropagation();
      if (typeof window.openViewExternalGigModal === 'function') {
        window.openViewExternalGigModal(g);
      }
    });
    // Same hover card as regular booked bubbles — pass a pre-built
    // __card payload so gig-hover-card.js renders the identical shape
    // (header / date-time / venue / location / lineup + style chips).
    if (typeof window.attachGigHoverCard === 'function') {
      window.attachGigHoverCard(d, _hoverPayload(g));
    }
    return d;
  }

  // Build a __card:true payload matching the shape gig-hover-card.js
  // emits from payloadFromGig(). Kept in sync with the same helper on
  // artist-profile.html. Header renders as "Booked — {ArtistName}" so
  // external gigs read identically to real booked gigs in the hover.
  function _hoverPayload(g) {
    const start = g.start_time || '';
    const end   = g.end_time   || '';
    const city  = g.venue_city || '';
    const state = g.venue_state || '';
    return {
      __card: true,
      headerPrefix: 'Booked — ',
      artistChips: _artistName ? [{ id: _artistId, name: _artistName }] : [],
      openSlotsSuffix: '',
      isOpen: false,
      venue: g.venue_name || '',
      streetAddress: g.venue_address || '',
      cityState: city && state ? `${city}, ${state}` : (city || state || ''),
      date: _fmtDateLong(g.date),
      time: _fmtTime12(start) + (end ? ` – ${_fmtTime12(end)}` : ''),
      slotLines: null,
      lineupChips: Array.isArray(g.lineup) ? g.lineup : [],
      styleChips:  Array.isArray(g.styles) ? g.styles : [],
      mapsUrl: '',
      artistType: g.artist_type || '',
    };
  }

  // ── VIEW MODAL ──────────────────────────────────────────────────────
  // Read-only details view that mirrors the visual language of the
  // regular gig-modal (gig-modal.js renderGigModal). Populated at open
  // time from the passed row. Edit / Delete buttons defer to the
  // existing edit modal + delete handler.
  let _viewingGig = null;

  window.openViewExternalGigModal = function (g) {
    _viewingGig = g;
    const body = document.getElementById('extGigViewBody');
    const title = document.getElementById('extGigViewTitle');
    if (!body || !title) return;
    title.textContent = g.venue_name || 'Gig Details';
    body.innerHTML = _renderExtGigBody(g);

    // Configure the flyer footer button based on whether the gig has one.
    // With flyer → "🎨 View Event Flyer" opens lightbox.
    // Without → "📎 Add Event Flyer" opens the edit modal (artist can
    // upload from the flyer section there).
    const flyerBtn = document.getElementById('extGigViewFlyerBtn');
    if (flyerBtn) {
      if (g.flyer_url) {
        const bust = g.flyer_uploaded_at ? `?t=${encodeURIComponent(g.flyer_uploaded_at)}` : '';
        const src = g.flyer_url + bust;
        flyerBtn.textContent = 'View Event Flyer';
        flyerBtn.onclick = () => window.showExtFlyerModal(src);
      } else {
        flyerBtn.textContent = 'Add Event Flyer';
        flyerBtn.onclick = () => window.editExternalGigFromView();
      }
    }
    document.getElementById('extGigViewModal').classList.remove('hidden');
  };

  // Body HTML for the ext-gig detail view. Matches gig-modal.js
  // renderGigModal exactly (rows + slot card visual chrome) minus the
  // pay pill and Cancel/Countersign actions. Same helper is duplicated
  // on artist-profile.html for the public visitor view — kept in sync
  // by hand since the two pages don't share a module.
  function _renderExtGigBody(g) {
    const dateStr = _fmtDateLong(g.date);
    const start12 = _fmtTime12(g.start_time || '');
    const end12   = _fmtTime12(g.end_time   || '');
    const timeStr = (start12 && end12) ? `${start12} – ${end12}` : (start12 || '');
    const loc = [g.venue_city, g.venue_state].filter(Boolean).join(', ');
    const lineup = Array.isArray(g.lineup) ? g.lineup : [];
    const styles = Array.isArray(g.styles) ? g.styles : [];
    const formats = lineup.join(', ');
    const stylesStr = styles.join(', ');

    // Row grid — matches gig-modal.js:108 exactly (grid template, gap,
    // font-size 0.95rem, line-height 1.6, mb 16px).
    let html = `<div style="display:grid;grid-template-columns:auto 1fr;gap:8px 16px;font-size:0.95rem;line-height:1.6;margin-bottom:16px;">`;
    html += _gmRow('Date',    _esc(dateStr));
    if (timeStr)        html += _gmRow('Time',        _esc(timeStr));
    html += _gmRow('Venue',   _esc(g.venue_name || ''));
    if (g.venue_address || loc) {
      const addrHtml = [
        g.venue_address ? _esc(g.venue_address) + '<br>' : '',
        _esc(loc || ''),
      ].join('');
      html += _gmRow('Location', addrHtml);
    }
    if (g.artist_type)  html += _gmRow('Artist Type', _esc(g.artist_type));
    if (g.artist_type === 'Live Band' && formats)   html += _gmRow('Lineup', _esc(formats));
    if (g.artist_type === 'Live Band' && stylesStr) html += _gmRow('Styles', _esc(stylesStr));
    if (g.notes)        html += _gmRow('Notes',      _esc(g.notes).replace(/\n/g, '<br>'));
    html += `</div>`;

    // Slot card — matches gig-modal.js:1054 (padding, cyan bg for
    // "my slot", purple left border stripe, purple "Slot 1" label,
    // gray time, no pay pill, "✓ Your Slot" indicator, second-line
    // italic type summary).
    const slotTime = (start12 && end12) ? `${start12} – ${end12}` : (start12 || end12 || '—');
    const icon = _ICONS[g.artist_type] || '🎵';
    const summaryParts = [];
    if (g.artist_type) summaryParts.push(`${icon} ${g.artist_type}`);
    if (formats)   summaryParts.push(formats);
    if (stylesStr) summaryParts.push(stylesStr);
    const summary = summaryParts.join(' · ');

    html += `<div style="border-top:1px solid rgba(255,255,255,0.1); padding-top:12px; margin-top:4px;">
      <div style="padding:10px 12px 10px 10px; background:rgba(6,182,212,0.1); border:1px solid rgba(6,182,212,0.4); border-left:3px solid #a855f7; border-radius:6px; margin-bottom:6px;">
        <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
          <span style="font-weight:700; color:#a855f7; font-size:0.85rem; letter-spacing:0.3px;">Slot 1</span>
          <span style="color:#cbd5e1; font-size:0.85rem;">${_esc(slotTime)}</span>
          <span style="flex:1;"></span>
          <span style="color:#06b6d4; font-weight:600; font-size:0.8rem;">✓ Your Slot</span>
        </div>
        ${summary ? `<div style="margin-top:5px; color:var(--text-muted); font-size:0.78rem; line-height:1.4; font-style:italic;">${_esc(summary)}</div>` : ''}
      </div>
    </div>`;
    // View/Add Event Flyer moved into the modal FOOTER (extGigViewFlyerBtn)
    // to match the regular gig-modal button-row convention.
    return html;
  }

  // Lightbox overlay for the flyer image. Dark backdrop with image
  // centered + a Delete Flyer / Close button row underneath. Backdrop
  // click still dismisses; the inner container swallows the click so
  // interacting with the image or buttons doesn't collapse the modal.
  window.showExtFlyerModal = function (src) {
    let ov = document.getElementById('extGigFlyerLightbox');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'extGigFlyerLightbox';
      ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.9);z-index:100010;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;gap:16px;';
      ov.innerHTML = `
        <img id="extGigFlyerLightboxImg" style="max-width:100%;max-height:calc(100vh - 120px);border-radius:8px;box-shadow:0 20px 60px rgba(0,0,0,0.5);object-fit:contain;">
        <div id="extGigFlyerLightboxActions" style="display:flex;gap:10px;">
          <button id="extGigFlyerDeleteBtn" style="padding:10px 20px;background:transparent;border:1px solid rgba(239,68,68,0.4);border-radius:6px;color:#fca5a5;cursor:pointer;font-weight:500;font-size:0.9rem;">Delete Flyer</button>
          <button id="extGigFlyerCloseBtn" style="padding:10px 20px;background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer;font-weight:500;font-size:0.9rem;">Close</button>
        </div>
      `;
      // Backdrop click closes; clicks inside the image + buttons don't.
      ov.addEventListener('click', (e) => { if (e.target === ov) ov.style.display = 'none'; });
      ov.querySelector('#extGigFlyerCloseBtn').addEventListener('click', () => { ov.style.display = 'none'; });
      ov.querySelector('#extGigFlyerDeleteBtn').addEventListener('click', () => {
        if (!_viewingGig || !_artistId) return;
        const gid = _viewingGig.id;
        // Hide the lightbox BEFORE opening the confirm — gf-modals uses
        // z-index:10000 and our lightbox uses 100010, so leaving the
        // lightbox on-screen buries the confirm behind it (user reported
        // "took a minute" because the confirm was rendering unreachable
        // underneath). Confirm is a short-lived choice; either way the
        // lightbox was about to go away.
        ov.style.display = 'none';
        _confirmDeleteFlyer(async () => {
          try {
            const r = await fetch(`/api/artists/${_artistId}/external-gigs/${gid}/flyer`, {
              method: 'DELETE', credentials: 'include',
            });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            await loadExternalGigs();
            // Refresh the view modal with the updated row so the flyer
            // footer button switches back to "Add Event Flyer".
            const fresh = (_extGigs || []).find(x => x.id === gid);
            if (fresh) window.openViewExternalGigModal(fresh);
          } catch (e) {
            alert('Delete failed: ' + (e.message || 'unknown error'));
          }
        });
      });
      document.body.appendChild(ov);
    }
    document.getElementById('extGigFlyerLightboxImg').src = src;
    ov.style.display = 'flex';
  };

  // Branded delete-flyer confirmation — same shape as _confirmDelete
  // for the gig itself, but tone/wording scoped to the flyer only.
  function _confirmDeleteFlyer(doDelete) {
    if (typeof window.showStyledModal !== 'function') {
      if (confirm('Delete this flyer? This cannot be undone.')) doDelete();
      return;
    }
    window.showStyledModal(
      'Delete this flyer?',
      '<p style="margin:0 0 12px;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
        'Remove the flyer from this gig? The gig itself stays — you can upload a new flyer later.' +
      '</p>' +
      '<p style="margin:0;color:var(--text-gray,#94a3b8);font-size:0.82rem;line-height:1.5;">' +
        '<strong style="color:#fca5a5;">This cannot be undone.</strong>' +
      '</p>',
      [
        { text: 'Keep it', style: 'ghost' },
        { text: '🗑 Delete Flyer', style: 'danger', onClick: function () { doDelete(); } },
      ],
      { size: 'sm', tone: 'error' }
    );
  }

  // gig-modal.js row style — bold label + value, both text-primary color.
  function _gmRow(label, valueHtml) {
    return `<div style="font-weight:600;color:var(--text-primary);">${label}:</div>
            <div style="color:var(--text-primary);">${valueHtml}</div>`;
  }

  window.closeExtGigViewModal = function () {
    const m = document.getElementById('extGigViewModal');
    if (m) m.classList.add('hidden');
    _viewingGig = null;
  };

  window.editExternalGigFromView = function () {
    if (!_viewingGig) return;
    const g = _viewingGig;
    closeExtGigViewModal();
    openEditExternalGigModal(g);
  };

  window.deleteExternalGigFromView = async function () {
    if (!_viewingGig) return;
    const gid = _viewingGig.id;
    _confirmDelete(gid, async () => {
      try {
        const r = await fetch(`/api/artists/${_artistId}/external-gigs/${gid}`, {
          method: 'DELETE', credentials: 'include',
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await loadExternalGigs();
        closeExtGigViewModal();
      } catch (e) {
        alert('Delete failed: ' + (e.message || 'unknown error'));
      }
    });
  };

  function _row(label, valueHtml) {
    return `<div style="font-weight:600;color:var(--text);">${label}:</div>
            <div style="color:var(--text);">${valueHtml}</div>`;
  }
  function _fmtDateLong(iso) {
    if (!iso) return '';
    const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return iso;
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  // ── MODAL CHROME (one-shot init on first open) ──────────────────────
  function _initChrome() {
    if (_initedChrome) return;
    _initedChrome = true;

    // Populate US state dropdown. `US_STATES` is declared with `const` at
    // the top level of us-states.js, so it's a lexical script global —
    // NOT a property of `window`. Access it via `typeof` guard, not
    // `window.US_STATES` (silent miss). This was the "state pulldown empty"
    // bug through v2–v5 of this file.
    const stateSel = document.getElementById('extGigVenueState');
    if (stateSel && typeof US_STATES !== 'undefined') {
      // 2026-08-07: display 2-letter code (e.g. "CA") not the full
      // state name. Matches venue-edit.html + artist-edit.html which
      // are the closest analog forms and use abbreviations.
      stateSel.innerHTML = '<option value="">State</option>' +
        US_STATES.map(s => `<option value="${s.code}">${s.code}</option>`).join('');
    }

    // City autocomplete → auto-fills state on match
    if (window.initCityAutocomplete) {
      window.initCityAutocomplete({ inputId: 'extGigVenueCity', stateId: 'extGigVenueState' });
    }

    // Dark date picker on the date field
    if (window.GfDatePicker) {
      const dateEl = document.getElementById('extGigDate');
      if (dateEl && !dateEl._gfDatePicker) {
        _datePicker = new window.GfDatePicker(dateEl);
        dateEl._gfDatePicker = _datePicker;
      }
    }

    // Populate Styles pills (shared list with venue create-gig modal)
    const stylesTags = document.getElementById('extGigStylesTags');
    if (stylesTags && !stylesTags.dataset.populated) {
      stylesTags.innerHTML = STYLE_OPTIONS.map(s =>
        `<button type="button" class="ext-tag" data-active="false" data-style="${s}">${s}</button>`
      ).join('');
      stylesTags.dataset.populated = '1';
    }

    // Wire tag-pill toggles
    _wireTagRow('extGigArtistTypeRow', 'type', /* singleSelect */ true);
    _wireTagRow('extGigLineupTags',    'lineup', false);
    _wireTagRow('extGigStylesTags',    'style',  false);

    // Venue name autocomplete against GigsFill venues
    _wireVenueAutocomplete();

    // Flyer drag/drop + file-picker + Replace + Remove wiring. Kept
    // one-shot in _initChrome so the listeners survive across modal
    // open/close cycles. State is reset per-open in _resetForm().
    _wireFlyerUI();

    // PM auto-flip on end-time blur (mirrors venue create-gig modal)
    const endEl = document.getElementById('extGigEnd');
    if (endEl) endEl.addEventListener('blur', _maybeFlipEndToPM);
  }

  // ── FLYER UPLOAD UI ─────────────────────────────────────────────────
  function _wireFlyerUI() {
    const drop    = document.getElementById('extGigFlyerDrop');
    const input   = document.getElementById('extGigFlyerInput');
    const preview = document.getElementById('extGigFlyerPreview');
    const img     = document.getElementById('extGigFlyerImg');
    const replace = document.getElementById('extGigFlyerReplaceBtn');
    const remove  = document.getElementById('extGigFlyerRemoveBtn');
    if (!drop || !input) return;

    drop.addEventListener('click', () => input.click());
    replace.addEventListener('click', () => input.click());
    remove.addEventListener('click', () => {
      // If the gig has a persisted flyer, mark for server-side deletion
      // when Save fires. If it's just a client-side staged File, drop
      // the staged file. Either way, hide the preview.
      if (_flyerCurrentUrl) _flyerRemovePending = true;
      _flyerPending = null;
      _flyerCurrentUrl = null;
      _showFlyerPreview(null);
    });

    input.addEventListener('change', (e) => {
      const f = e.target.files && e.target.files[0];
      _stageFlyer(f);
    });

    // Drag/drop styling + handling on the drop zone.
    ['dragenter', 'dragover'].forEach(evt => {
      drop.addEventListener(evt, (e) => {
        e.preventDefault();
        drop.style.background = 'rgba(168,85,247,0.15)';
        drop.style.borderColor = 'rgba(168,85,247,0.7)';
      });
    });
    ['dragleave', 'drop'].forEach(evt => {
      drop.addEventListener(evt, (e) => {
        e.preventDefault();
        drop.style.background = 'rgba(168,85,247,0.05)';
        drop.style.borderColor = 'rgba(168,85,247,0.35)';
      });
    });
    drop.addEventListener('drop', (e) => {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      _stageFlyer(f);
    });
  }

  function _stageFlyer(file) {
    if (!file) return;
    if (!/^image\/(jpeg|png)$/i.test(file.type)) {
      _status('Flyer must be a JPG or PNG.', '#ef4444');
      return;
    }
    if (file.size > 3 * 1024 * 1024) {
      _status('Flyer must be 3 MB or smaller.', '#ef4444');
      return;
    }
    _flyerPending = file;
    _flyerRemovePending = false;
    _status('');
    // Client-side thumbnail preview via FileReader so the user sees the
    // image before it's uploaded (upload doesn't fire until Save).
    const rd = new FileReader();
    rd.onload = () => _showFlyerPreview(rd.result);
    rd.readAsDataURL(file);
  }

  function _showFlyerPreview(src) {
    const drop = document.getElementById('extGigFlyerDrop');
    const preview = document.getElementById('extGigFlyerPreview');
    const img = document.getElementById('extGigFlyerImg');
    if (!drop || !preview || !img) return;
    if (src) {
      img.src = src;
      preview.style.display = '';
      drop.style.display = 'none';
    } else {
      img.src = '';
      preview.style.display = 'none';
      drop.style.display = '';
    }
  }

  // Toggle .ext-tag pills. If singleSelect, clicking one deselects the
  // others in the row (radio-style — used for Artist Type).
  function _wireTagRow(rowId, dataKey, singleSelect) {
    const row = document.getElementById(rowId);
    if (!row) return;
    row.addEventListener('click', (e) => {
      const btn = e.target.closest('.ext-tag');
      if (!btn) return;
      const wasActive = btn.getAttribute('data-active') === 'true';
      if (singleSelect) {
        row.querySelectorAll('.ext-tag').forEach(b => b.setAttribute('data-active', 'false'));
        btn.setAttribute('data-active', wasActive ? 'false' : 'true');
        // Artist Type: show/hide Lineup + Styles rows depending on Live Band state
        if (rowId === 'extGigArtistTypeRow') _syncLiveBandRows();
      } else {
        btn.setAttribute('data-active', wasActive ? 'false' : 'true');
      }
    });
  }

  function _syncLiveBandRows() {
    const activeType = _selectedTag('extGigArtistTypeRow', 'type');
    const isLive = activeType === 'Live Band';
    const lineupRow = document.getElementById('extGigLineupRow');
    const stylesRow = document.getElementById('extGigStylesRow');
    if (lineupRow) lineupRow.style.display = isLive ? '' : 'none';
    if (stylesRow) stylesRow.style.display = isLive ? '' : 'none';
    // When Live Band is deactivated, clear any lineup/style selections so
    // they don't silently persist into the payload for a Solo / DJ / etc gig.
    if (!isLive) {
      _clearTagRow('extGigLineupTags');
      _clearTagRow('extGigStylesTags');
    }
  }

  function _selectedTag(rowId, dataAttr) {
    const row = document.getElementById(rowId);
    if (!row) return null;
    const active = row.querySelector('.ext-tag[data-active="true"]');
    return active ? active.getAttribute('data-' + dataAttr) : null;
  }
  function _selectedTags(rowId, dataAttr) {
    const row = document.getElementById(rowId);
    if (!row) return [];
    return Array.from(row.querySelectorAll('.ext-tag[data-active="true"]'))
      .map(b => b.getAttribute('data-' + dataAttr));
  }
  function _clearTagRow(rowId) {
    const row = document.getElementById(rowId);
    if (!row) return;
    row.querySelectorAll('.ext-tag').forEach(b => b.setAttribute('data-active', 'false'));
  }
  function _setTagRow(rowId, dataAttr, values, singleSelect) {
    const row = document.getElementById(rowId);
    if (!row) return;
    const set = new Set(Array.isArray(values) ? values : (values ? [values] : []));
    row.querySelectorAll('.ext-tag').forEach(b => {
      const v = b.getAttribute('data-' + dataAttr);
      b.setAttribute('data-active', set.has(v) ? 'true' : 'false');
    });
    if (singleSelect && rowId === 'extGigArtistTypeRow') _syncLiveBandRows();
  }

  // ── VENUE NAME AUTOCOMPLETE ─────────────────────────────────────────
  async function _loadVenues() {
    if (_venuesCache) return _venuesCache;
    try {
      const r = await fetch('/api/venues/public', { credentials: 'include' });
      if (!r.ok) { _venuesCache = []; return _venuesCache; }
      _venuesCache = await r.json();
    } catch (e) {
      _venuesCache = [];
    }
    return _venuesCache;
  }

  function _wireVenueAutocomplete() {
    const input = document.getElementById('extGigVenueName');
    const dd = document.getElementById('extGigVenueDropdown');
    if (!input || !dd) return;

    input.setAttribute('autocomplete', 'gf-no-autofill-' + Math.random().toString(36).slice(2, 6));

    let matches = [];
    let activeIdx = -1;

    const render = () => {
      if (!matches.length) { dd.style.display = 'none'; dd.innerHTML = ''; return; }
      // 2026-08-07: one-line format — bold cyan venue name followed by
      // muted "(City, ST)". Puts each match on its own single row so
      // multiple hits stack cleanly and scan quickly.
      dd.innerHTML = matches.map((v, i) => {
        const loc = [v.city, v.state].filter(Boolean).join(', ');
        return `<div data-idx="${i}" style="padding:8px 10px;cursor:pointer;font-size:0.85rem;${i === activeIdx ? 'background:rgba(168,85,247,0.18);' : ''}">
          <span style="color:var(--cyan);font-weight:700;">${_escape(v.venue_name || '')}</span>${loc ? ` <span style="color:var(--text-muted);font-size:0.78rem;">(${_escape(loc)})</span>` : ''}
        </div>`;
      }).join('');
      dd.style.display = 'block';
    };

    const pick = (v) => {
      input.value = v.venue_name || '';
      const addrEl = document.getElementById('extGigVenueAddress');
      const cityEl = document.getElementById('extGigVenueCity');
      const stateEl = document.getElementById('extGigVenueState');
      if (addrEl && v.address_line_1) addrEl.value = v.address_line_1;
      if (cityEl && v.city) cityEl.value = v.city;
      if (stateEl && v.state) {
        stateEl.value = v.state;
        // If the option isn't in the dropdown (shouldn't happen — v.state is
        // stored as a 2-letter code), fall back gracefully by injecting it.
        if (stateEl.value !== v.state) {
          const opt = document.createElement('option');
          opt.value = v.state; opt.textContent = v.state;
          stateEl.appendChild(opt);
          stateEl.value = v.state;
        }
      }
      matches = []; activeIdx = -1; render();
    };

    input.addEventListener('input', async () => {
      const q = input.value.trim().toLowerCase();
      if (q.length < 2) { matches = []; render(); return; }
      const venues = await _loadVenues();
      matches = venues
        .filter(v => (v.venue_name || '').toLowerCase().includes(q))
        .slice(0, 8);
      activeIdx = -1;
      render();
    });

    input.addEventListener('keydown', (e) => {
      if (!matches.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, matches.length - 1); render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); render(); }
      else if (e.key === 'Enter' && activeIdx >= 0) { e.preventDefault(); pick(matches[activeIdx]); }
      else if (e.key === 'Escape') { matches = []; render(); }
    });

    dd.addEventListener('mousedown', (e) => {
      const row = e.target.closest('[data-idx]');
      if (!row) return;
      e.preventDefault();
      pick(matches[+row.getAttribute('data-idx')]);
    });

    document.addEventListener('mousedown', (e) => {
      if (!dd.contains(e.target) && e.target !== input) {
        matches = []; render();
      }
    });
  }

  function _escape(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
  }

  // ── END-TIME FLIP PROMPT ────────────────────────────────────────────
  // Ported verbatim from venue.create-gigs.js:1279 — same "Did you mean..."
  // modal, same tone/dismissibility. When end < start we offer to flip end
  // by ±12h. eh<12 → PM typo path (add 12h). eh>=12 → overnight path
  // (subtract 12h — e.g. 9 PM → 12 PM entered means user meant 12 AM).
  function _fmt12(hhmm) {
    const [h, m] = hhmm.split(':').map(Number);
    const hr = h > 12 ? h - 12 : (h === 0 ? 12 : h);
    return `${hr}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;
  }
  function _addHours(hhmm, delta) {
    const [h, m] = hhmm.split(':').map(Number);
    let nh = (h + delta + 24) % 24;
    return `${String(nh).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
  }
  function _maybeFlipEndToPM() {
    const startEl = document.getElementById('extGigStart');
    const endEl = document.getElementById('extGigEnd');
    if (!startEl || !endEl || !startEl.value || !endEl.value) return;
    const s = startEl.value, e = endEl.value;
    const [sh, sm] = s.split(':').map(Number);
    const [eh, em] = e.split(':').map(Number);
    const startMins = sh * 60 + sm;
    const endMins = eh * 60 + em;
    if (endMins >= startMins) return;  // ok — end is after start
    if (endMins === startMins) return; // exact equal — handled elsewhere

    let suggested, hint;
    if (eh < 12) {
      // e.g. end=11:00 (AM), start=19:00 → suggest 23:00 (11 PM)
      suggested = _addHours(e, 12);
      hint = 'Looks like you meant PM — end time is before start.';
    } else {
      // e.g. end=12:00 (noon) after start=21:00 → suggest 00:00 (12 AM overnight)
      suggested = _addHours(e, -12);
      hint = 'Looks like an overnight gig — end time is before start.';
    }

    if (typeof window.showStyledModal !== 'function') {
      // Fallback: silent flip if the modal helper isn't loaded for some reason
      endEl.value = suggested;
      return;
    }
    window.showStyledModal(
      '🕐 Did you mean...',
      `<p style="text-align:center;color:#a78bfa;font-size:1.05rem;font-weight:600;">${_fmt12(s)} to ${_fmt12(suggested)}?</p>` +
      `<p style="text-align:center;color:var(--text-gray);font-size:0.85rem;margin-top:8px;">${hint}</p>`,
      [
        { text: 'No, keep it', style: 'ghost' },
        { text: 'Yes, fix it', style: 'primary',
          onClick: () => { endEl.value = suggested; } },
      ],
      { size: 'sm', tone: 'info', dismissible: false }
    );
  }

  // ── MODAL OPEN/CLOSE ────────────────────────────────────────────────
  window.openAddExternalGigModal = function (dateStr) {
    _initChrome();
    _editingId = null;
    _resetForm();
    if (dateStr && _datePicker) _datePicker.setISO(dateStr);
    // Pre-fill Artist Type / Lineup / Styles from the artist's saved
    // profile so a rock band doesn't have to re-tag every gig. User can
    // override for one-off gigs.
    if (_artistDefaults) {
      _setTagRow('extGigArtistTypeRow', 'type',   _artistDefaults.artist_type || null, true);
      _setTagRow('extGigLineupTags',    'lineup', _artistDefaults.lineup || []);
      _setTagRow('extGigStylesTags',    'style',  _artistDefaults.styles || []);
    }
    _setModalTitle('ADD GIG NOT BOOKED WITH GIGSFILL');
    _setSaveLabel('Add Gig');
    _showDeleteBtn(false);
    _open();
  };

  window.openEditExternalGigModal = function (g) {
    _initChrome();
    _editingId = g.id;
    _resetForm();
    document.getElementById('extGigVenueName').value    = g.venue_name    || '';
    document.getElementById('extGigVenueCity').value    = g.venue_city    || '';
    document.getElementById('extGigVenueState').value   = g.venue_state   || '';
    document.getElementById('extGigVenueAddress').value = g.venue_address || '';
    if (_datePicker) _datePicker.setISO(g.date || '');
    document.getElementById('extGigStart').value        = (g.start_time || '').slice(0, 5);
    document.getElementById('extGigEnd').value          = (g.end_time   || '').slice(0, 5);
    document.getElementById('extGigNotes').value        = g.notes         || '';
    _setTagRow('extGigArtistTypeRow', 'type',   g.artist_type || null, true);
    _setTagRow('extGigLineupTags',    'lineup', g.lineup || []);
    _setTagRow('extGigStylesTags',    'style',  g.styles || []);
    // Restore flyer state: if this gig has a saved flyer, show it in the
    // preview panel (cache-bust with uploaded_at so a replace-then-edit
    // cycle doesn't show a stale image).
    const pubEl = document.getElementById('extGigFlyerPublic');
    if (pubEl) pubEl.checked = g.flyer_public !== false; // default on
    if (g.flyer_url) {
      const bust = g.flyer_uploaded_at ? `?t=${encodeURIComponent(g.flyer_uploaded_at)}` : '';
      _flyerCurrentUrl = g.flyer_url + bust;
      _showFlyerPreview(_flyerCurrentUrl);
    }
    _setModalTitle('EDIT GIG NOT BOOKED WITH GIGSFILL');
    // Match the venue Create Gig modal's edit-mode convention: primary
    // button reads "Save Changes" when editing an existing row.
    _setSaveLabel('Save Changes');
    _showDeleteBtn(true);
    _open();
  };

  function _setSaveLabel(text) {
    const b = document.getElementById('extGigSaveBtn');
    if (b) b.textContent = text;
  }

  window.closeExtGigModal = function () {
    const m = document.getElementById('extGigModal');
    if (m) m.classList.add('hidden');
    const dd = document.getElementById('extGigVenueDropdown');
    if (dd) { dd.style.display = 'none'; dd.innerHTML = ''; }
    if (_datePicker) _datePicker.close();
  };

  function _open() {
    document.getElementById('extGigArtistName').textContent = _artistName || 'This artist';
    document.getElementById('extGigStatus').textContent = '';
    document.getElementById('extGigModal').classList.remove('hidden');
    setTimeout(() => document.getElementById('extGigVenueName').focus(), 60);
  }

  function _resetForm() {
    ['extGigVenueName','extGigVenueCity','extGigVenueAddress','extGigNotes'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    const st = document.getElementById('extGigVenueState');
    if (st) st.value = '';
    if (_datePicker) _datePicker.setISO('');
    const s = document.getElementById('extGigStart');
    const e = document.getElementById('extGigEnd');
    if (s) s.value = '19:00';
    if (e) e.value = '22:00';
    _clearTagRow('extGigArtistTypeRow');
    _clearTagRow('extGigLineupTags');
    _clearTagRow('extGigStylesTags');
    _syncLiveBandRows();
    // Flyer state — reset staging + preview. Public-visibility checkbox
    // defaults ON per the "yes, checked" ask.
    _flyerPending = null;
    _flyerRemovePending = false;
    _flyerCurrentUrl = null;
    _showFlyerPreview(null);
    const input = document.getElementById('extGigFlyerInput');
    if (input) input.value = '';
    const pub = document.getElementById('extGigFlyerPublic');
    if (pub) pub.checked = true;
  }

  function _setModalTitle(t) {
    const el = document.getElementById('extGigModalTitle');
    if (el) el.textContent = t;
  }
  function _showDeleteBtn(show) {
    const b = document.getElementById('extGigDeleteBtn');
    if (b) b.style.display = show ? '' : 'none';
  }

  function _status(msg, color) {
    const el = document.getElementById('extGigStatus');
    if (el) { el.textContent = msg; el.style.color = color || 'var(--text-gray)'; }
  }

  // ── SAVE / DELETE ───────────────────────────────────────────────────
  window.saveExternalGig = async function () {
    if (!_artistId) return;
    const dateISO = _datePicker ? _datePicker.getISO() : (document.getElementById('extGigDate').value || '');
    const flyerPublicEl = document.getElementById('extGigFlyerPublic');
    const flyerPublic = flyerPublicEl ? flyerPublicEl.checked : true;
    const payload = {
      venue_name:    document.getElementById('extGigVenueName').value.trim(),
      venue_city:    document.getElementById('extGigVenueCity').value.trim(),
      venue_state:   document.getElementById('extGigVenueState').value.trim().toUpperCase(),
      venue_address: document.getElementById('extGigVenueAddress').value.trim(),
      date:          dateISO,
      start_time:    document.getElementById('extGigStart').value,
      end_time:      document.getElementById('extGigEnd').value,
      notes:         document.getElementById('extGigNotes').value.trim(),
      artist_type:   _selectedTag('extGigArtistTypeRow', 'type'),
      lineup:        _selectedTags('extGigLineupTags', 'lineup'),
      styles:        _selectedTags('extGigStylesTags', 'style'),
      flyer_public:  flyerPublic,
    };
    if (!payload.venue_name)    return _status('Venue name is required.', '#ef4444');
    if (!payload.venue_address) return _status('Address is required.', '#ef4444');
    if (!payload.date)          return _status('Date is required.', '#ef4444');
    const btn = document.getElementById('extGigSaveBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    try {
      const url = _editingId
        ? `/api/artists/${_artistId}/external-gigs/${_editingId}`
        : `/api/artists/${_artistId}/external-gigs`;
      const method = _editingId ? 'PUT' : 'POST';
      const r = await fetch(url, {
        method, credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${r.status}`);
      }
      // Determine the row id so flyer upload/delete can target it.
      // POST returns {id}; PUT returns {id: _editingId}.
      const saveResp = await r.json().catch(() => ({}));
      const gigId = _editingId || saveResp.id;

      // Flyer side-effects — fire only after the main save committed
      // successfully. Order matters: delete first, then upload, so a
      // "replace" (remove then pick a new file, then save) does both.
      if (_flyerRemovePending && gigId) {
        if (btn) btn.textContent = 'Removing flyer…';
        await fetch(`/api/artists/${_artistId}/external-gigs/${gigId}/flyer`, {
          method: 'DELETE', credentials: 'include',
        }).catch(() => {});
      }
      if (_flyerPending && gigId) {
        if (btn) btn.textContent = 'Uploading flyer…';
        const fd = new FormData();
        fd.append('file', _flyerPending);
        const upr = await fetch(`/api/artists/${_artistId}/external-gigs/${gigId}/flyer`, {
          method: 'POST', credentials: 'include', body: fd,
        });
        if (!upr.ok) {
          const err = await upr.json().catch(() => ({}));
          throw new Error(err.detail || 'Flyer upload failed');
        }
      }
      _status('✓ Saved', '#22c55e');
      await loadExternalGigs();
      setTimeout(closeExtGigModal, 400);
    } catch (e) {
      _status('✗ ' + (e.message || 'Save failed'), '#ef4444');
    } finally {
      if (btn) {
        btn.disabled = false;
        // Preserve the label the modal opened with (Add Gig vs Save Changes).
        btn.textContent = _editingId ? 'Save Changes' : 'Add Gig';
      }
    }
  };

  window.deleteExternalGig = async function () {
    if (!_artistId || !_editingId) return;
    _confirmDelete(_editingId, async () => {
      try {
        const r = await fetch(`/api/artists/${_artistId}/external-gigs/${_editingId}`, {
          method: 'DELETE', credentials: 'include',
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await loadExternalGigs();
        closeExtGigModal();
      } catch (e) {
        _status('✗ Delete failed: ' + e.message, '#ef4444');
      }
    });
  };

  // Branded delete confirmation — matches admin-init.js:680 (Delete ticket)
  // pattern: title + red-highlighted "cannot be undone" body, Keep it (ghost)
  // + red danger button. Falls back to native confirm if gf-modals isn't
  // loaded for some reason.
  function _confirmDelete(_id, doDelete) {
    if (typeof window.showStyledModal !== 'function') {
      if (confirm('Delete this external gig? This cannot be undone.')) doDelete();
      return;
    }
    window.showStyledModal(
      'Delete this gig?',
      '<p style="margin:0 0 12px;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
        'Remove this external gig from your calendar? It will no longer appear on your public profile.' +
      '</p>' +
      '<p style="margin:0;color:var(--text-gray,#94a3b8);font-size:0.82rem;line-height:1.5;">' +
        '<strong style="color:#fca5a5;">This cannot be undone.</strong>' +
      '</p>',
      [
        { text: 'Keep it', style: 'ghost' },
        // Sync fire-and-forget wrapper — see admin-init.js:691 comment for why.
        { text: '🗑 Delete', style: 'danger', onClick: function () { doDelete(); } },
      ],
      { size: 'sm', tone: 'error' }
    );
  }

  // Backdrop click closes; Escape closes. Both modals covered.
  document.addEventListener('click', (e) => {
    if (!e.target) return;
    if (e.target.id === 'extGigModal')     closeExtGigModal();
    if (e.target.id === 'extGigViewModal') closeExtGigViewModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const edit = document.getElementById('extGigModal');
    if (edit && !edit.classList.contains('hidden')) { closeExtGigModal(); return; }
    const view = document.getElementById('extGigViewModal');
    if (view && !view.classList.contains('hidden')) closeExtGigViewModal();
  });
})();
