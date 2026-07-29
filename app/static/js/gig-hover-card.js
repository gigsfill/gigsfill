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
  // Card width is applied INLINE via positionCard() so the flip-to-left
  // logic can know the exact size before measurement. Hard-set (not a
  // cap) — shrink-to-fit via max-content didn't work because the badges
  // row's display:flex container reports its UNWRAPPED sum as max-
  // content, inflating the card. Sized tight so the type row
  // (🎸 Live Band + Solo + Duo + Trio + Full Band) fits on one line
  // with only the symmetric ~12px padding to the right of Full Band.
  const CARD_WIDTH = 336;
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
    // Status categorization — a slot with artist_id is NOT necessarily
    // "Booked". It might be in pending_contract (artist hasn't signed),
    // awaiting_venue_contract (artist signed, venue must countersign),
    // or pending_venue_approval (same-day request). The header now
    // reflects ACTUAL slot status, not just slot ownership.
    const PENDING_STATUSES = new Set([
      'pending_contract',         // artist needs to sign
      'awaiting_venue_contract',  // venue needs to countersign
      'pending_venue_approval',   // same-day approval pending
    ]);
    if (Array.isArray(g.slots) && g.slots.length > 1) {
      totalSlots = g.slots.length;
      const takenSlots = g.slots.filter(s => s.artist_id || s.artist_name);
      const reallyBooked = takenSlots.filter(s => s.status === 'booked');
      const pending      = takenSlots.filter(s => PENDING_STATUSES.has(s.status));
      openSlots = totalSlots - takenSlots.length;
      // Multi-slot: header shows the STATE only — never the artist
      // names. The per-slot breakdown below already lists each name
      // next to its slot time, so repeating them in the header is
      // redundant and crowds the card.
      if (takenSlots.length === 0) {
        header = `Open — ${totalSlots} slots`;
        isOpenHere = true;
      } else if (reallyBooked.length === takenSlots.length) {
        header = 'Booked';
      } else if (pending.length === takenSlots.length) {
        header = 'Pending Venue Countersign';
      } else {
        const parts = [];
        if (reallyBooked.length) parts.push(`Booked — ${reallyBooked.length}`);
        if (pending.length)      parts.push(`Pending — ${pending.length}`);
        header = parts.join(' · ');
      }
      artistChips = []; // names are in the per-slot breakdown below
    } else if ((parseInt(g.total_slots_count) || 0) > 1) {
      // Multi-slot gig whose .slots array hasn't been hydrated yet
      // (slot fetch failed, race condition, or an endpoint we missed).
      // Without this branch we'd fall through to the single-slot
      // logic below — which keys off g.artist_id (NULL on multi-slot
      // parents) and renders "Open" even when the gig is fully booked.
      // booked_slots_count / total_slots_count come from the standard
      // calendar endpoints, so use those to summarize state.
      // Jul 2026: was gated on the deprecated is_multi_slot flag.
      const _booked = parseInt(g.booked_slots_count) || 0;
      const _total  = parseInt(g.total_slots_count) || 0;
      if (_booked <= 0) {
        header = _total > 0 ? `Open — ${_total} slots` : 'Open';
        isOpenHere = true;
      } else if (_total > 0 && _booked < _total) {
        header = `Booked — ${_booked}/${_total}`;
      } else {
        header = 'Booked';
      }
      artistChips = [];
    } else {
      isOpenHere = (g.status === 'open' || !g.artist_id) && !g.artist_name;
      if (isOpenHere) {
        header = 'Open';
      } else {
        // Single-slot: pick label by gig.status. The artist_id check
        // above already routed us here, so we know a slot is taken.
        if (g.status === 'awaiting_venue_contract' || g.status === 'pending_contract'
            || g.status === 'pending_venue_approval') {
          header = 'Pending Venue Countersign — ';
        } else {
          header = 'Booked — ';
        }
        artistChips = [{
          id: g.artist_id || null,
          name: g.artist_name || g.artistName || '',
        }];
      }
    }

    let statusLabel = '';
    // Compute pending-contract presence from slot statuses too — for
    // multi-slot gigs the parent g.contract_status can lag behind what
    // the slots actually say, and single-slot gigs may carry the state
    // on g.status instead. Belt-and-suspenders so the badge fires
    // whenever ANY slot is mid-flow.
    const _anySlotPending = Array.isArray(g.slots) && g.slots.some(
      s => s.status === 'pending_contract' || s.status === 'awaiting_venue_contract'
    );
    const _gigPending = g.status === 'pending_contract' || g.status === 'awaiting_venue_contract';
    if (g.is_blast_open) statusLabel = 'Blast — first to book';
    else if (g.waitlist_pending || g.contract_status === 'waitlist_pending')
      statusLabel = 'Waitlist · pending';
    else if (_anySlotPending || _gigPending
             || g.contract_status === 'pending' || g.contract_status === 'awaiting_venue')
      statusLabel = 'Contract pending';
    else if (g.status === 'cancelled') statusLabel = 'Cancelled';

    // Per-slot breakdown for multi-slot gigs — one row per slot with its
    // own time range + artist (or "Open") + small status tag. Renders
    // below the date in renderCard. Only emitted when there are 2+ slots
    // so single-slot (and venue calendar's per-slot bubble payloads,
    // which trim slots to [slot]) don't get redundant rows.
    let slotLines = null;
    if (Array.isArray(g.slots) && g.slots.length >= 2) {
      const _sorted = [...g.slots].sort(
        (a, b) => (a.start_time || '').localeCompare(b.start_time || '')
      );
      // Type icon per slot — falls back to the gig's umbrella
      // artist_type when the slot doesn't carry its own. Gives the
      // viewer an at-a-glance signal that a mixed-type multi-slot gig
      // has e.g. 2 Live Band rows + 1 DJ row, so they know why some
      // rows would be bookable for a DJ and others wouldn't. (Jun 2026
      // user request.)
      const _slotIconMap = {'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠', 'Open Mic MC':'🎙️', 'Karaoke MC':'🎶'};
      slotLines = _sorted.map(s => {
        const tStart = fmtTime(s.start_time);
        const tEnd   = s.end_time ? fmtTime(s.end_time) : '';
        const time   = tEnd ? `${tStart} – ${tEnd}` : tStart;
        const slotType = (s.artist_type && String(s.artist_type).trim()) || g.artist_type || '';
        const typeIcon = _slotIconMap[slotType] || '🎵';
        let who = '', tag = '', tagClass = '';
        const st = s.status || '';
        if (st === 'booked') {
          who = s.artist_name || 'Booked';
          tag = 'Booked'; tagClass = 'gf-ghc-slot-tag-booked';
        } else if (PENDING_STATUSES.has(st)) {
          who = s.artist_name || 'Pending';
          tag = st === 'pending_venue_approval' ? 'Pending Approval' : 'Pending Countersign';
          tagClass = 'gf-ghc-slot-tag-pending';
        } else if (st === 'open') {
          who = 'Open'; tag = ''; tagClass = '';
        } else {
          // Cancelled / unknown — show whatever artist info we have plus the raw status.
          who = s.artist_name || st || '—';
          tag = st; tagClass = '';
        }
        return { time, who, tag, tagClass, artistId: s.artist_id || null, typeIcon, slotType };
      });
    }

    // Split comma-separated values into trimmed unique tokens — used for
    // the lineup (band_formats) and styles chip rows. Drops empties and
    // collapses duplicates while preserving the venue-set order. Bonus:
    // no matter how the raw string was formatted ("Solo,Duo", "Solo,
    // Duo", or "Solo , Duo"), the rendered chips read the same.
    const splitChips = (s) => {
      if (!s) return [];
      const seen = new Set();
      const out = [];
      for (const raw of String(s).split(',')) {
        const t = raw.trim();
        if (t && !seen.has(t.toLowerCase())) { seen.add(t.toLowerCase()); out.push(t); }
      }
      return out;
    };
    // For BOOKED slots/gigs, we want to show only the chips that
    // represent what the booked artist ACTUALLY brings (intersection of
    // gig requirements + artist profile). For OPEN gigs we show the full
    // requirement list so visitors know what's acceptable.
    //
    // Data sources:
    //   - Gig requirements: g.band_formats / g.styles (always present)
    //   - Booked artist's profile: g.artist_band_formats / g.artist_styles
    //     (single-slot gigs) or pulled from a slot when present
    //     (multi-slot — venue calendar's per-slot bubbles merge slot
    //     data into g, so g.artist_band_formats reflects THAT slot's
    //     booked artist).
    //
    // Falls back to "show all requirements" when artist's profile data
    // isn't on hand (older API responses, public unauth views, etc).
    const reqLineup = splitChips(g.band_formats || g.lineup);
    const reqStyles = splitChips(g.styles);
    const artistLineup = splitChips(g.artist_band_formats);
    const artistStyles = splitChips(g.artist_styles);
    const intersect = (req, artistOwn) => {
      if (!artistOwn.length) return req;     // no artist data → show full req list
      if (!req.length) return artistOwn;     // no req filter → show artist's full list
      const lc = new Set(artistOwn.map(x => x.toLowerCase()));
      return req.filter(x => lc.has(x.toLowerCase()));
    };
    const isBooked = !!(g.artist_id || (g.slots && g.slots[0] && g.slots[0].artist_id));
    const lineupChips = isBooked ? intersect(reqLineup, artistLineup) : reqLineup;
    const styleChips  = isBooked ? intersect(reqStyles, artistStyles) : reqStyles;

    // Optional gig title — venues sometimes set one ("Acoustic Tuesdays",
    // "Indie Night"). Show it as a small dimmed subhead under the status
    // row so the viewer knows what the event is called without crowding
    // the artist/booking line.
    const titleText = (g.title && String(g.title).trim()) || '';
    return {
      __card: true,
      title: titleText,
      headerPrefix: header,           // e.g. "Booked — " or "Open"
      artistChips: artistChips,       // [{id, name}] — render as links once resolved
      openSlotsSuffix: openSlots > 0 && artistChips.length > 0 ? ` (+${openSlots} open)` : '',
      isOpen: isOpenHere,
      venue: venue,
      streetAddress: streetAddress,
      cityState: city && state ? `${city}, ${state}` : (city || state || ''),
      date: fmtDate(date),
      // For multi-slot gigs the overall time would just repeat slot-1's
      // start through slot-N's end (the umbrella) — redundant with the
      // per-slot breakdown below. Hide it.
      time: slotLines ? '' : fmtTime(start) + (end ? ` – ${fmtTime(end)}` : ''),
      slotLines: slotLines,
      lineupChips: lineupChips,       // green chips — "Solo", "Duo", etc.
      styleChips: styleChips,         // purple chips — "Country", "Hip-Hop", etc.
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

    // Optional gig title — shown as a small dimmed subhead between the
    // status header and the date block.
    if (p.title) {
      rows.push(`<div class="gf-ghc-title">${esc(p.title)}</div>`);
    }

    // Date / time block (no clock icon).
    const dt = [];
    if (p.date) dt.push(`<div class="gf-ghc-date">${esc(p.date)}</div>`);
    if (p.time) dt.push(`<div class="gf-ghc-time">${esc(p.time)}</div>`);
    if (dt.length) rows.push(`<div class="gf-ghc-dt">${dt.join('')}</div>`);

    // Per-slot breakdown — one line per slot with time, name (clickable
    // via artist-link sentinel), and small status tag. Renders only when
    // payloadFromGig built slotLines (multi-slot gigs).
    if (p.slotLines && p.slotLines.length) {
      // Layout note (Jun 2026): .gf-ghc-slots is a CSS grid with three
      // columns — time | name | tag. Each .gf-ghc-slot-line uses
      // display:contents so its three children participate directly in
      // the parent grid, which means the name column starts at the
      // SAME x-position across every row regardless of how wide each
      // row's time string is. No dot separator any more — vertical
      // alignment does the visual separation.
      const linesHtml = p.slotLines.map(s => {
        const whoHtml = s.artistId
          ? `<span class="gf-ghc-artist-link" data-artist-id="${s.artistId}">${esc(s.who)}</span>`
          : `<span class="gf-ghc-slot-who">${esc(s.who)}</span>`;
        const tagHtml = s.tag
          ? `<span class="gf-ghc-slot-tag ${esc(s.tagClass || '')}">${esc(s.tag)}</span>`
          : '<span class="gf-ghc-slot-tag-empty"></span>';
        // Type icon sits in its own grid cell BEFORE the time so the
        // viewer can scan a vertical icon column to spot the type
        // mix of a multi-slot gig.
        const iconHtml = s.typeIcon
          ? `<span class="gf-ghc-slot-icon" title="${esc(s.slotType || '')}">${s.typeIcon}</span>`
          : '<span class="gf-ghc-slot-icon"></span>';
        return `<div class="gf-ghc-slot-line">
          ${iconHtml}
          <span class="gf-ghc-slot-time">${esc(s.time)}</span>
          ${whoHtml}
          ${tagHtml}
        </div>`;
      }).join('');
      rows.push(`<div class="gf-ghc-slots">${linesHtml}</div>`);
    }

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
    // Artist type stays as a single neutral badge (Live Band / DJ /
    // Comedian / etc.) — it's a single value, not a comma-list. Add
    // the matching emoji icon from the site-wide convention.
    if (p.artistType) {
      const _typeIcons = {'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠', 'Open Mic MC':'🎙️', 'Karaoke MC':'🎶'};
      const _icon = _typeIcons[p.artistType] || '🎵';
      badges.push(`<span class="gf-ghc-badge gf-ghc-dim">${_icon} ${esc(p.artistType)}</span>`);
    }
    // Lineup chips render as separate GREEN pills (Solo / Duo / Trio /
    // Full Band) matching the venue create-gig page convention. Styles
    // render as separate PURPLE pills (Country / Hip-Hop / Rock / etc.).
    // Each item is its own chip — the comma-and-space spacing concern
    // is moot because we drop the commas entirely.
    (p.lineupChips || []).forEach(t => {
      badges.push(`<span class="gf-ghc-chip gf-ghc-chip-lineup">${esc(t)}</span>`);
    });
    (p.styleChips || []).forEach(t => {
      badges.push(`<span class="gf-ghc-chip gf-ghc-chip-style">${esc(t)}</span>`);
    });
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
    // Hard width — see CARD_WIDTH comment for why shrink-to-fit didn't
    // work. The badge row's flex-wrap behavior handles overflow by
    // wrapping; the type row at ~320-330px content fits at this width
    // with just the symmetric ~12px padding to either side.
    card.style.width = CARD_WIDTH + 'px';
    card.style.maxWidth = CARD_WIDTH + 'px';
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
