/**
 * Shared hover-card preview for calendar gig bubbles.
 * =====================================================
 * The calendar bubbles across the site (public-gigs, artist-book-gigs,
 * venue-create-gigs, artist-profile, venue-profile) are tight one-liners
 * that truncate. Hovering one shows a small floating card anchored to the
 * bubble with full info, without changing layout density.
 *
 * Two ways to enable it for a bubble:
 *
 *   1) DOM-built bubbles (most calendar JS files):
 *        window.attachGigHoverCard(divEl, gigObjOrPayload);
 *      Pass the raw gig object — the helper extracts the right fields.
 *
 *   2) HTML-string bubbles (artist-profile + venue-profile):
 *        <div class="cal-gig booked"
 *             data-gig-hover='{"artist":"...","time":"7pm",...}'>...</div>
 *      The payload is read from the data-gig-hover attribute.
 *
 * Card behavior:
 *   - 200ms hover delay so a quick mouse sweep doesn't trigger
 *   - Anchored next to the bubble; auto-flips above/left to stay on-screen
 *   - Skipped entirely on coarse-pointer devices (taps open the modal instead)
 *   - Click on the bubble closes the card so the existing detail modal opens
 *     cleanly with no orphan hover card lingering
 */
(function () {
  'use strict';

  // Coarse-pointer = touch primary (phone/tablet). Skip hover UI; the
  // bubble's existing click handler will still open the detail modal.
  if (window.matchMedia &&
      window.matchMedia('(hover: none), (pointer: coarse)').matches) {
    window.attachGigHoverCard = function () { /* no-op on touch */ };
    return;
  }

  const HOVER_DELAY_MS = 200;
  const HIDE_DELAY_MS = 180;  // grace window so the user can move into the card
  const CARD_WIDTH = 300;
  const GAP = 10;  // px between bubble and card

  let currentCard = null;
  let hoverTimer = null;
  let hideTimer = null;
  let activeAnchor = null;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Format a time string (HH:MM or HH:MM:SS) as 12-hour with am/pm.
  function fmtTime(t) {
    if (!t) return '';
    const m = /^(\d{1,2}):(\d{2})/.exec(t);
    if (!m) return t;
    let h = parseInt(m[1], 10);
    const min = m[2];
    const ampm = h >= 12 ? 'pm' : 'am';
    h = h % 12; if (h === 0) h = 12;
    return min === '00' ? `${h}${ampm}` : `${h}:${min}${ampm}`;
  }

  // Format YYYY-MM-DD → "Fri, May 22 2026".
  function fmtDate(d) {
    if (!d) return '';
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d);
    if (!m) return d;
    const dt = new Date(parseInt(m[1]), parseInt(m[2]) - 1, parseInt(m[3]));
    if (isNaN(dt.getTime())) return d;
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const mos = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${days[dt.getDay()]}, ${mos[dt.getMonth()]} ${dt.getDate()} ${dt.getFullYear()}`;
  }

  // Build a Google/Apple-friendly maps URL. The same /maps/search/ URL
  // works on both — iOS Safari hands it off to Apple Maps if installed.
  function mapsUrl(parts) {
    const q = parts.filter(Boolean).join(', ');
    if (!q) return null;
    return 'https://www.google.com/maps/search/?api=1&query=' +
           encodeURIComponent(q);
  }

  // Tiny in-memory cache so we only hit /api/vanity-lookup once per
  // artist/venue regardless of how many gig bubbles reference them.
  const _vanityCache = {}; // key "artist:7" → Promise<string|null>
  function fetchVanitySlug(entityType, entityId) {
    if (!entityType || !entityId) return Promise.resolve(null);
    const k = entityType + ':' + entityId;
    if (_vanityCache[k]) return _vanityCache[k];
    _vanityCache[k] = fetch(`/api/vanity-lookup/${entityType}/${entityId}`,
                            { credentials: 'omit' })
      .then(r => r.ok ? r.json() : null)
      .then(d => (d && d.slug) ? d.slug : null)
      .catch(() => null);
    return _vanityCache[k];
  }

  // Given a gig-shaped object from any of the calendar pages, build the
  // hover-card payload. We normalize the field names so the renderer below
  // doesn't need to know which page sourced the data.
  function payloadFromGig(g) {
    if (!g) return null;
    if (g.__card) return g;  // HTML-string bubbles pre-flatten
    const start = g.start_time || g.startTime || (g.slots && g.slots[0] && g.slots[0].start_time);
    const end   = g.end_time   || g.endTime;
    const date  = g.date || g.gig_date;
    const venue = g.venue_name || g.venueName;
    const city  = g.venue_city || g.city;
    const state = g.venue_state || g.state;
    const addr1 = g.address_line_1 || g.addressLine1 || g.address1 || g.venue_address_1 || '';
    const addr2 = g.address_line_2 || g.addressLine2 || g.address2 || g.venue_address_2 || '';
    const streetAddress = [addr1, addr2].filter(Boolean).join(' ');

    // Header content: the artist name(s) get split out so the renderer
    // can turn them into vanity-URL links once /api/vanity-lookup resolves.
    let header;            // headline prefix without the linkable artists
    let artistChips = [];  // [{ id, name }] — one per linkable artist
    let isOpenHere = false;
    let openSlots = 0;
    let totalSlots = 0;
    if (g.is_multi_slot && Array.isArray(g.slots)) {
      totalSlots = g.slots.length;
      const bookedSlots = g.slots.filter(s => s.artist_id || s.artist_name);
      openSlots = totalSlots - bookedSlots.length;
      if (bookedSlots.length === 0) {
        header = `Open — ${totalSlots} slots`;
        isOpenHere = true;
      } else {
        header = 'Booked — ';
        artistChips = bookedSlots.map(s => ({
          id: s.artist_id || null, name: s.artist_name || '',
        }));
        if (openSlots > 0) header = 'Booked — ';  // suffix appended below
      }
    } else {
      isOpenHere = (g.status === 'open' || !g.artist_id) && !g.artist_name;
      if (isOpenHere) {
        header = 'Open';
      } else {
        header = 'Booked — ';
        artistChips = [{
          id: g.artist_id || null,
          name: g.artist_name || g.artistName || '',
        }];
      }
    }

    let statusLabel = '';
    if (g.is_blast_open) statusLabel = 'Blast — first to book';
    else if (g.waitlist_pending || g.contract_status === 'waitlist_pending')
      statusLabel = 'Waitlist · pending';
    else if (g.contract_status === 'pending' || g.contract_status === 'awaiting_venue')
      statusLabel = 'Contract pending';
    else if (g.status === 'cancelled') statusLabel = 'Cancelled';

    return {
      __card: true,
      headerPrefix: header,           // e.g. "Booked — " or "Open"
      artistChips: artistChips,       // [{id, name}] — render as links once resolved
      openSlotsSuffix: openSlots > 0 && artistChips.length > 0 ? ` (+${openSlots} open)` : '',
      isOpen: isOpenHere,
      venue: venue,
      streetAddress: streetAddress,
      cityState: city && state ? `${city}, ${state}` : (city || state || ''),
      date: fmtDate(date),
      time: fmtTime(start) + (end ? ` – ${fmtTime(end)}` : ''),
      mapsUrl: mapsUrl([venue, streetAddress, city, state]),
      statusLabel: statusLabel,
      artistType: g.artist_type,
      styles: g.styles || g.artist_styles,
    };
  }

  function renderCard(p) {
    const el = document.createElement('div');
    el.className = 'gf-gig-hover-card';
    el.setAttribute('role', 'tooltip');
    const rows = [];

    // Backward-compat: older payloads used a flat `header` string.
    if (p.header && !p.headerPrefix && !(p.artistChips && p.artistChips.length)) {
      rows.push(`<div class="gf-ghc-header${p.isOpen ? ' gf-ghc-open-header' : ''}">${esc(p.header)}</div>`);
    }
    // Header: "Open" or "Booked — Fridays Past". Artist names render with
    // a data-artist-id sentinel so we can swap them for vanity-URL links
    // once /api/vanity-lookup resolves.
    if (p.headerPrefix || (p.artistChips && p.artistChips.length)) {
      const chipsHtml = (p.artistChips || []).map((c, i) => {
        const sep = i > 0 ? ' · ' : '';
        if (!c.name) return '';
        if (c.id) {
          return `${sep}<span class="gf-ghc-artist-link" data-artist-id="${c.id}">${esc(c.name)}</span>`;
        }
        return `${sep}${esc(c.name)}`;
      }).join('');
      const suffix = p.openSlotsSuffix ? esc(p.openSlotsSuffix) : '';
      rows.push(
        `<div class="gf-ghc-header${p.isOpen ? ' gf-ghc-open-header' : ''}">` +
        esc(p.headerPrefix || '') + chipsHtml + suffix +
        `</div>`
      );
    }

    // Date / time block (no clock icon).
    const dt = [];
    if (p.date) dt.push(`<div class="gf-ghc-date">${esc(p.date)}</div>`);
    if (p.time) dt.push(`<div class="gf-ghc-time">${esc(p.time)}</div>`);
    if (dt.length) rows.push(`<div class="gf-ghc-dt">${dt.join('')}</div>`);

    // Venue + street address — both clickable to open Google/Apple maps.
    const loc = [];
    const venueText = p.venue ? esc(p.venue) : '';
    const linesParts = [];
    if (venueText) {
      const link = p.mapsUrl
        ? `<a href="${esc(p.mapsUrl)}" target="_blank" rel="noopener" class="gf-ghc-maplink">${venueText}</a>`
        : venueText;
      linesParts.push(`<div class="gf-ghc-venue">${link}</div>`);
    }
    if (p.streetAddress || p.cityState) {
      const addr = [p.streetAddress, p.cityState].filter(Boolean).join(', ');
      if (addr) {
        const link = p.mapsUrl
          ? `<a href="${esc(p.mapsUrl)}" target="_blank" rel="noopener" class="gf-ghc-maplink gf-ghc-dim">${esc(addr)}</a>`
          : `<span class="gf-ghc-dim">${esc(addr)}</span>`;
        linesParts.push(`<div class="gf-ghc-addr">${link}</div>`);
      }
    }
    if (linesParts.length) rows.push(`<div class="gf-ghc-loc">${linesParts.join('')}</div>`);

    // Optional secondary badges (state flags + style tags).
    const badges = [];
    if (p.statusLabel) {
      const cls = /open|blast/i.test(p.statusLabel) ? 'gf-ghc-badge gf-ghc-open'
                : /cancelled/i.test(p.statusLabel) ? 'gf-ghc-badge gf-ghc-cancel'
                : 'gf-ghc-badge';
      badges.push(`<span class="${cls}">${esc(p.statusLabel)}</span>`);
    }
    if (p.artistType) badges.push(`<span class="gf-ghc-badge gf-ghc-dim">${esc(p.artistType)}</span>`);
    if (p.styles)     badges.push(`<span class="gf-ghc-badge gf-ghc-dim">${esc(p.styles)}</span>`);
    if (badges.length) rows.push(`<div class="gf-ghc-badges">${badges.join('')}</div>`);

    el.innerHTML = rows.join('');

    // Lazy-upgrade each artist chip into a vanity-URL link. The fetch is
    // cached, so a calendar with the same artist on multiple days only
    // hits the API once.
    el.querySelectorAll('.gf-ghc-artist-link[data-artist-id]').forEach((chip) => {
      const id = parseInt(chip.getAttribute('data-artist-id'), 10);
      if (!id) return;
      fetchVanitySlug('artist', id).then((slug) => {
        if (!slug) return;
        const a = document.createElement('a');
        a.href = '/' + slug;
        a.target = '_blank';
        a.rel = 'noopener';
        a.className = 'gf-ghc-artist-anchor';
        a.textContent = chip.textContent;
        chip.replaceWith(a);
      });
    });

    // Keep the card alive while the user mouses into it; hide as soon as
    // they leave. Without this, mouseleave on the anchor would yank the
    // card before they can click the address / artist links.
    el.addEventListener('mouseenter', () => {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    });
    el.addEventListener('mouseleave', () => { scheduleHide(); });
    return el;
  }

  function positionCard(card, anchor) {
    const r = anchor.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    card.style.width = CARD_WIDTH + 'px';
    document.body.appendChild(card);  // append now to measure height
    const h = card.offsetHeight;
    let left = r.right + GAP;
    if (left + CARD_WIDTH > vw - 8) left = r.left - CARD_WIDTH - GAP;
    if (left < 8) left = 8;
    let top = r.top + (r.height / 2) - (h / 2);
    if (top + h > vh - 8) top = vh - 8 - h;
    if (top < 8) top = 8;
    card.style.left = (left + window.scrollX) + 'px';
    card.style.top  = (top  + window.scrollY) + 'px';
  }

  function show(anchor, payload) {
    hide();
    const card = renderCard(payload);
    card.style.opacity = '0';
    positionCard(card, anchor);
    requestAnimationFrame(() => { card.style.opacity = '1'; });
    currentCard = card;
    activeAnchor = anchor;
  }

  function hide() {
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
    if (hideTimer)  { clearTimeout(hideTimer);  hideTimer  = null; }
    if (currentCard) { currentCard.remove(); currentCard = null; }
    activeAnchor = null;
  }

  // Delayed hide: give the user ~180ms to move from the bubble into the
  // card (so they can click the venue / address links inside it).
  function scheduleHide() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, HIDE_DELAY_MS);
  }

  function bindElement(el, payload) {
    if (!el || !payload) return;
    // Suppress the native title tooltip so the browser doesn't pop a
    // second (uglier) tooltip after ~1.5s on top of our card. Stash it
    // on a data-attr so accessibility scrapers can still recover it.
    if (el.title) { el.dataset.gfTitle = el.title; el.title = ''; }
    el.addEventListener('mouseenter', () => {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      if (hoverTimer) clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => show(el, payload), HOVER_DELAY_MS);
    });
    el.addEventListener('mouseleave', () => {
      if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
      scheduleHide();
    });
    // Clicking the bubble opens the existing detail modal — close the card
    // so it doesn't sit over the modal.
    el.addEventListener('click', hide, true);
  }

  // Public API for DOM-built bubbles.
  window.attachGigHoverCard = function (el, gig) {
    const p = payloadFromGig(gig);
    if (p) bindElement(el, p);
  };

  // Event delegation for HTML-string bubbles that carry data-gig-hover.
  document.addEventListener('mouseover', (e) => {
    const target = e.target.closest && e.target.closest('[data-gig-hover]');
    if (!target || target === activeAnchor) return;
    const raw = target.getAttribute('data-gig-hover');
    if (!raw) return;
    let parsed;
    try { parsed = JSON.parse(raw); } catch (_) { return; }
    parsed.__card = true;  // already in card shape
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (hoverTimer) clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => show(target, parsed), HOVER_DELAY_MS);
  });
  document.addEventListener('mouseout', (e) => {
    const target = e.target.closest && e.target.closest('[data-gig-hover]');
    if (!target) return;
    if (target.contains(e.relatedTarget)) return;
    // If they're moving into the card itself, the card's own mouseenter
    // cancels the hide.
    scheduleHide();
  });
  // Hide on scroll / window blur so the card never strands.
  window.addEventListener('scroll', hide, true);
  window.addEventListener('blur', hide);
})();
