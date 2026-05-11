/**
 * gig-modal.js — Unified gig modal renderer
 * 
 * Used by both artist-book-gigs.html and venue-create-gigs.html.
 * 
 * Usage:
 *   const data = await fetchModalData(gigId, 'artist', artistId);
 *   await renderGigModal(data, { onBook, onCancelSlot, onCancelGig, onCountersign,
 *                                 onMessage, onJoinWaitlist, onLeaveWaitlist });
 */

/* ── Helpers ──────────────────────────────────────────────────────────────── */
function _esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function _slotIcon(artistType) {
  return {'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠'}[artistType] || '🎵';
}

function _fmtDate(dateStr) {
  if (!dateStr) return '';
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m-1, d).toLocaleDateString('en-US',
    {weekday:'long', year:'numeric', month:'long', day:'numeric'});
}

function _hoursUntilExpiry(expiresAt) {
  if (!expiresAt) return null;
  const d = new Date(expiresAt.endsWith('Z') ? expiresAt : expiresAt + 'Z');
  return Math.max(0, Math.round((d - Date.now()) / 3600000));
}

function _expiryText(expiresAt) {
  const h = _hoursUntilExpiry(expiresAt);
  if (h === null) return '';
  if (h === 0) return ' (expires soon!)';
  if (h < 1) return ' (expires in under an hour!)';
  return ` (expires in ~${h} hour${h !== 1 ? 's' : ''})`;
}

/* ── Fetch modal data from backend ───────────────────────────────────────── */
async function fetchModalData(gigId, viewerType, viewerId) {
  const res = await fetch(
    `/api/gigs/${gigId}/modal-data?viewer_type=${viewerType}&viewer_id=${viewerId}`,
    { credentials: 'include' }
  );
  if (!res.ok) throw new Error(`Modal data fetch failed: ${res.status}`);
  return res.json();
}

/* ── Main renderer ────────────────────────────────────────────────────────── */
async function renderGigModal(data, callbacks = {}) {
  const {
    onBook, onCancelSlot, onCancelGig, onCountersign, onUploadContract,
    onMessage, onJoinWaitlist, onLeaveWaitlist, onRequestPreferred,
    onClose, onRate, onUploadVenueContractPdf,
  } = callbacks;

  // Register callbacks globally so slot-row inline onclick handlers can reach them
  // without fragile .toString() serialization
  window._gmCbs = {
    message:      onMessage      || null,
    joinWaitlist:  onJoinWaitlist  || null,
    leaveWaitlist: onLeaveWaitlist || null,
    requestPref:   onRequestPreferred || null,
    rate:          onRate          || null,
    countersign:   onCountersign   || null,
  };

  // Register close function globally so inline onclick handlers can call it without toString()
  window._gmClose = () => onClose ? onClose() : document.getElementById('modalOverlay')?.classList.add('hidden');
  const close = window._gmClose;

  let html = '';
  let actionsHtml = '';

  const isPast       = data.is_past;
  const isInProgress = data.is_in_progress;
  const vType        = data.viewer_type;  // 'artist' | 'venue'
  const gigState     = data.gig_state;

  /* ── Header: date, time, venue, location ──────────────────────────────── */
  // For multi-slot: if artist has a slot, show that slot's time. Otherwise omit time row.
  let displayStart = data.start_time_fmt;
  let displayEnd   = data.end_time_fmt;
  let titleExtra   = '';

  if (vType === 'artist') {
    const mySlot = (data.slots || []).find(s => s.is_my_slot);
    if (mySlot) {
      displayStart = mySlot.start_time_fmt;
      displayEnd   = mySlot.end_time_fmt;
    } else if (data.is_multi_slot) {
      // No slot yet — show nothing; slot times shown in the slot rows below
      displayStart = '';
      displayEnd   = '';
    }
  } else {
    // Venue viewer: omit combined time — each slot row shows its own time
    displayStart = '';
    displayEnd   = '';
  }

  html += `<div style="display:grid;grid-template-columns:auto 1fr;gap:8px 16px;font-size:0.95rem;line-height:1.6;margin-bottom:16px;">`;
  html += `<div style="font-weight:600;color:var(--text-primary);">Date:</div>
           <div style="color:var(--text-primary);">${_fmtDate(data.date)}</div>`;
  if (displayStart) {
    html += `<div style="font-weight:600;color:var(--text-primary);">Time:</div>
             <div style="color:var(--text-primary);">${displayStart} – ${displayEnd}</div>`;
  }
  html += `<div style="font-weight:600;color:var(--text-primary);">Venue:</div>
           <div style="color:var(--text-primary);">
             <a href="/app/venue-profile.html?venue_id=${data.venue_id}" target="_blank"
                style="color:var(--accent-cyan,#06b6d4);text-decoration:none;"
                onmouseover="this.style.textDecoration='underline'"
                onmouseout="this.style.textDecoration='none'">${_esc(data.venue_name)}</a>
           </div>`;
  if (data.address_line_1 || data.city) {
    html += `<div style="font-weight:600;color:var(--text-primary);">Location:</div>
             <div style="color:var(--text-primary);">
               ${data.address_line_1 ? _esc(data.address_line_1) + '<br>' : ''}
               ${data.address_line_2 ? _esc(data.address_line_2) + '<br>' : ''}
               ${_esc(data.city || '')}${data.state ? ', ' + _esc(data.state) : ''}
             </div>`;
  }
  if (data.artist_type) {
    html += `<div style="font-weight:600;color:var(--text-primary);">Artist Type:</div>
             <div style="color:var(--text-primary);">${_esc(data.artist_type)}</div>`;
    if (data.artist_type === 'Live Band' && data.band_formats) {
      html += `<div style="font-weight:600;color:var(--text-primary);">Lineup:</div>
               <div style="color:var(--text-primary);">${_esc(data.band_formats.split(',').map(s=>s.trim()).join(', '))}</div>`;
    }
    if (data.artist_type === 'Live Band' && data.styles) {
      html += `<div style="font-weight:600;color:var(--text-primary);">Styles:</div>
               <div style="color:var(--text-primary);">${_esc(data.styles.split(',').map(s=>s.trim()).join(', '))}</div>`;
    }
  }
  if (data.notes) {
    html += `<div style="font-weight:600;color:var(--text-primary);">Notes:</div>
             <div style="color:var(--text-primary);">${_esc(data.notes)}</div>`;
  }
  html += `</div>`;

  /* ── Gig-level banners ────────────────────────────────────────────────── */

  // BANNED
  let _hasActiveOffer = false;
  if (vType === 'artist' && data.is_banned) {
    html += _banner('red', '🚫 Booking Not Permitted',
      `You are not permitted to book gigs at ${_esc(data.venue_name)}.`);
    actionsHtml = _closeBtn(close);
    return _commit(html, actionsHtml);
  }

  // PAST GIG
  if (isPast) {
    html += _slotsSection(data, vType, {isPast: true, isInProgress: false, close, callbacks});
    const myBookedSlot = (data.slots || []).find(s => s.is_my_slot && s.status === 'booked');
    if (myBookedSlot && vType === 'artist') {
      const msgBtn = (onMessage && data.can_message !== false) ? `<button class="_gig-btn _gig-btn-cyan" onclick="window._gmCbs&&window._gmCbs.message&&window._gmCbs.message(${data.id},'${_esc(data.venue_name)}',${data.viewer_id})">Message Venue</button>` : '';
      const rateBtn = `<button class="_gig-btn _gig-btn-cyan _rate-venue-btn"
        data-gig-id="${data.id}" data-venue-id="${data.venue_id}"
        data-venue-name="${_esc(data.venue_name)}" data-artist-id="${data.viewer_id}">⭐ Rate Venue</button>`;
      actionsHtml = `<div class="_gig-btn-row">${msgBtn}${rateBtn}${_closeBtn(close)}</div>`;
    } else {
      actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
    }
    return _commit(html, actionsHtml);
  }

  // IN PROGRESS
  if (isInProgress) {
    html += _slotsSection(data, vType, {isPast: false, isInProgress: true, close, callbacks});
    const mySlot = (data.slots||[]).find(s=>s.is_my_slot);
    if (mySlot && vType === 'artist') {
      const msgBtn = (onMessage && data.can_message !== false) ? `<button class="_gig-btn _gig-btn-cyan" onclick="window._gmCbs&&window._gmCbs.message&&window._gmCbs.message(${data.id},'${_esc(data.venue_name)}',${data.viewer_id})">Message Venue</button>` : '';
      actionsHtml = `<div class="_gig-btn-row">${msgBtn}${_closeBtn(close)}</div>`;
    } else {
      actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
    }
    return _commit(html, actionsHtml);
  }

  /* ── Artist: preferred/access banner ─────────────────────────────────── */
  if (vType === 'artist') {
    const pref = data.preferred_status;
    const isBlastOpen = data.is_blast_open || data.frequency_exempt;

    if (pref === null && !isBlastOpen) {
      // Not preferred and not a blast — only show request button, no slots
      html += _banner('purple', '🎵 Preferred Status Required',
        `You need Preferred Artist status at ${_esc(data.venue_name)} to book gigs here.`);
      const reqId = `reqPref_${data.id}`;
      const reqClick = onRequestPreferred ? `onclick="window._gmCbs&&window._gmCbs.requestPref&&window._gmCbs.requestPref(${data.venue_id}, '${reqId}')"` : '';
      actionsHtml = `<div class="_gig-btn-row">
        <button id="${reqId}" class="_gig-btn _gig-btn-cyan" ${reqClick}>Ask Venue for Preferred Status</button>
        ${_closeBtn(close)}
      </div>`;
      return _commit(html, actionsHtml);
    }

    if (pref === 'pending' && !isBlastOpen) {
      html += _banner('yellow', '⏳ Preferred Status Pending',
        'Your request is awaiting venue response. You can book once approved.');
      actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
      return _commit(html, actionsHtml);
    }

    if ((pref === 'revoked' || pref === 'denied') && !isBlastOpen) {
      const label = pref === 'revoked' ? 'Revoked' : 'Denied';
      const msg   = pref === 'revoked'
        ? `${_esc(data.venue_name)} has revoked your preferred status.`
        : 'This venue denied your preferred artist request.';
      html += _banner('red', `⛔ Preferred Status ${label}`, msg);
      actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
      return _commit(html, actionsHtml);
    }

    // Waitlist LOCKED banner — only show if there are still open bookable slots
    const _hasBookableSlots = (data.slots || []).some(s => s.relationship === 'open_bookable');
    const _allSlotsTaken = (data.slots || []).every(s => s.status !== 'open' || s.relationship === 'freq_blocked');
    if (data.has_active_waitlist && !data.waitlist_status?.has_offer && _hasBookableSlots && !_allSlotsTaken) {
      const wls = data.waitlist_status || {};
      const amOnWl = wls.on_waitlist;
      html += _banner('red', '🔒 Booking Locked',
        `Another waitlisted artist has first right of refusal. They'll lose the offer if they don't respond.
         ${amOnWl ? "You're on the waitlist and will be notified if they decline." : ''}`);
      if (amOnWl && onLeaveWaitlist) {
        actionsHtml = `<div class="_gig-btn-row">
          <button class="_gig-btn _gig-btn-primary" onclick="window._gmCbs&&window._gmCbs.leaveWaitlist&&window._gmCbs.leaveWaitlist(${data.id},${data.viewer_id})">
            Leave Waitlist (${wls.position||'?'} of ${wls.total||1})
          </button>
          ${_closeBtn(close)}
        </div>`;
      } else {
        actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
      }
      return _commit(html, actionsHtml);
    }

    // Waitlist OFFER banner (this artist has the offer) — hide if already booked a slot
    const _alreadyBooked = (data.slots || []).some(s => s.is_my_slot);
    if (data.waitlist_status?.has_offer && !_alreadyBooked) {
      const exp = data.waitlist_status.offer_expires_at;
      // Normalize timezone: replace +00:00 with Z, or append Z if no tz info
      const expNorm = exp ? exp.replace(/\.\d+/, '').replace(/([+-]\d{2}:\d{2}|Z)$/, 'Z') : null;
      const expFmt = expNorm ? (() => { try { return new Date(expNorm).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}); } catch(e) { return null; } })() : null;
      html += _banner('green', '🎯 YOU\'RE NEXT ON THE WAITLIST!',
        `This gig just opened and ${expFmt ? `<strong>you have until ${expFmt} to book it!</strong>` : '<strong>you have been offered this slot!</strong>'}
         Check your email for the booking link, or book directly below.`);
      _hasActiveOffer = true;
    }
  }
  // If artist has active offer, always show Not Available — set AFTER slots to prevent overwrite
  if (_hasActiveOffer) {
    actionsHtml = `<div class="_gig-btn-row">
      <button class="_gig-btn _gig-btn-ghost"
        onclick="window._gmCbs&&window._gmCbs.leaveWaitlist?window._gmCbs.leaveWaitlist(${data.id},${data.viewer_id}):leaveWaitlist(${data.id},${data.viewer_id})">
        Not Available
      </button>
      ${_closeBtn(close)}
    </div>`;
  }

  /* ── Venue: contract required notice ─────────────────────────────────── */
  const _artistAlreadyHasSlot = (data.slots || []).some(s => s.is_my_slot);
  if (vType === 'artist' && data.venue_contract_required && !_artistAlreadyHasSlot) {
    html += `<div style="margin-bottom:12px;padding:10px 14px;background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.3);border-radius:8px;">
      <p style="color:#a78bfa;margin:0;font-size:0.85rem;line-height:1.5;">
        📋 <strong>Contract Required</strong> — This venue requires a signed contract. You'll review and sign during the booking process.
      </p>
    </div>`;
  }

  /* ── Frequency warning banner ─────────────────────────────────────────── */
  if (vType === 'artist' && data.freq_check && !data.is_blast_open && !data.frequency_exempt) {
    const fc = data.freq_check;
    const dir = fc.isBeforeBookedGig ? 'before' : 'after';
    html += _banner('red', '⚠️ Frequency Limitation',
      `This gig is ${fc.absDaysBetween} day${fc.absDaysBetween!==1?'s':''} ${dir} your booked gig on ${fc.lastGigDate}. This venue requires at least ${fc.daysRequired} days between bookings.`);
  }

  /* ── Slots section ────────────────────────────────────────────────────── */
  html += _slotsSection(data, vType, {isPast: false, isInProgress: false, close, callbacks});

  /* ── Actions row ──────────────────────────────────────────────────────── */
  const mySlot = (data.slots||[]).find(s => s.is_my_slot);

  if (vType === 'artist') {
    const myBooked = mySlot && (mySlot.status === 'booked');
    const myPending = mySlot && (mySlot.status === 'pending_contract' || mySlot.status === 'awaiting_venue_contract');
    const myApproval = mySlot && mySlot.status === 'pending_venue_approval';

    if (myBooked) {
      const slotIdField = mySlot.id;
      const msgBtn = (onMessage && data.can_message !== false)
        ? `<button class="_gig-btn _gig-btn-cyan" onclick="window._gmCbs&&window._gmCbs.message&&window._gmCbs.message(${data.id},'${_esc(data.venue_name)}',${data.viewer_id})">Message Venue</button>`
        : '';
      if (onCancelSlot) {
        actionsHtml = `<div class="_gig-btn-row">
          ${msgBtn}
          <button id="cancelSlotBtn" class="_gig-btn _gig-btn-primary"
            data-slot-id="${slotIdField}" data-slot-num="${mySlot.slot_number}">Cancel My Slot</button>
          ${_closeBtn(close)}
        </div>`;
      } else {
        actionsHtml = `<div class="_gig-btn-row">${msgBtn}${_closeBtn(close)}</div>`;
      }
    } else if (myPending) {
      if (mySlot.contract_status !== 'artist_signed' && onCancelGig) {
        actionsHtml = `<div class="_gig-btn-row">
          <button id="cancelGig" class="_gig-btn _gig-btn-primary">Cancel</button>
          ${_closeBtn(close)}
        </div>`;
      } else {
        actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
      }
    } else if (myApproval) {
      actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
    } else {
      // Open gig with no slot yet — book buttons are inline per slot
      actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
    }
  } else {
    // Venue viewer — the venue modal already has its own Close button in the modal footer
    // Only add actions row if there's something meaningful to show (not just a redundant Close)
    actionsHtml = '';
  }

  return _commit(html, actionsHtml);
}

/* ── Slots section renderer ───────────────────────────────────────────────── */
function _slotsSection(data, vType, { isPast, isInProgress, close, callbacks }) {
  const { onBook, onCancelSlot, onCountersign, onMessage,
          onJoinWaitlist, onLeaveWaitlist, onUploadContract,
          onUploadVenueContractPdf } = callbacks || {};

  const slots = (data.slots || []);
  if (!slots.length) return '';

  let html = `<div style="border-top:1px solid rgba(255,255,255,0.1);padding-top:12px;margin-top:4px;">`;

  for (const slot of slots) {
    html += _slotRow(slot, data, vType, isPast, isInProgress, callbacks);
  }

  html += `</div>`;
  return html;
}

/* ── Single slot row ──────────────────────────────────────────────────────── */
function _slotRow(slot, data, vType, isPast, isInProgress, callbacks) {
  const { onBook, onJoinWaitlist, onLeaveWaitlist, onCountersign,
          onMessage, onUploadContract, onUploadVenueContractPdf } = callbacks || {};

  const rel        = slot.relationship;
  const isMySlot   = slot.is_my_slot;
  const isBooked   = slot.status === 'booked';
  const isPending  = slot.status === 'pending_contract' || slot.status === 'awaiting_venue_contract';
  const isOpen     = slot.status === 'open';
  const icon       = _slotIcon(slot.artist_type || data.artist_type);
  const slotTime   = slot.start_time_fmt && slot.end_time_fmt
    ? `${slot.start_time_fmt} – ${slot.end_time_fmt}` : '';

  // Slot started/ended? Resolve overnight slots correctly.
  //
  // PROD BUG (May 10 2026): a gig with slots 11pm-1am + 1am-3am was
  // shown as "Ended" because the 01:00 end time, treated as same-day,
  // is already past at 10pm of the gig date. Fix: compare the slot's
  // start time to the GIG's overall start_time. If the slot starts
  // chronologically before the gig (e.g. 01:00 < 23:00), it's the
  // morning AFTER the gig date. Similarly, if a slot's end is before
  // its own start, end is one day later than start.
  function _slotDateOffsets(gigStartTime, slotStartTime, slotEndTime) {
    const toMin = (t) => {
      if (!t) return null;
      const [h, m] = String(t).split(':').map(Number);
      return h * 60 + m;
    };
    const gs = toMin(gigStartTime);
    const ss = toMin(slotStartTime);
    const se = toMin(slotEndTime);
    let startOffset = 0;
    if (gs != null && ss != null && ss < gs) startOffset = 1;
    let endOffset = startOffset;
    if (ss != null && se != null && se < ss) endOffset = startOffset + 1;
    return { startOffset, endOffset };
  }
  function _slotDate(dateStr, time, dayOffset) {
    if (!dateStr || !time) return null;
    const [y, m, d] = dateStr.split('-').map(Number);
    const [h, min] = String(time).split(':').map(Number);
    return new Date(y, m - 1, d + (dayOffset || 0), h, min, 0);
  }
  const _offsets = _slotDateOffsets(data.start_time, slot.start_time, slot.end_time);
  const _slotStartDt = _slotDate(data.date, slot.start_time, _offsets.startOffset);
  const _slotEndDt   = _slotDate(data.date, slot.end_time,   _offsets.endOffset);
  const _now = new Date();
  const slotStarted = _slotStartDt ? _now >= _slotStartDt : false;
  const slotEnded   = _slotEndDt   ? _now >= _slotEndDt   : false;

  // Color coding
  let borderColor = 'rgba(255,255,255,0.1)';
  let bgColor     = 'rgba(255,255,255,0.02)';
  let opacity     = '1';

  if (isMySlot) {
    borderColor = 'rgba(6,182,212,0.4)';
    bgColor     = 'rgba(6,182,212,0.1)';
  } else if (isBooked || isPending) {
    borderColor = 'rgba(34,197,94,0.3)';
    bgColor     = 'rgba(34,197,94,0.07)';
  } else if (rel === 'freq_blocked' || rel === 'no_access' || rel === 'banned') {
    opacity = '0.4';
  }

  // Pay display — rendered as a green pill matching the venue-side modal so
  // multi-slot gigs read consistently across both views. Visibility rules
  // unchanged: artists see pay only for their own slot or open & bookable
  // slots (other slots' negotiated pay isn't artist-facing).
  let payHtml = '';
  if (slot.pay) {
    const showPay = isMySlot || (isOpen && rel === 'open_bookable');
    if (showPay) {
      payHtml = `<span style="color:#22c55e;font-weight:700;font-size:0.8rem;background:rgba(34,197,94,0.12);padding:1px 8px;border-radius:4px;border:1px solid rgba(34,197,94,0.25);white-space:nowrap;">$${parseFloat(slot.pay).toFixed(2)}</span>`;
    }
  }

  // Type / formats / styles for slot.artist_type — shown on its own line so
  // the header row stays uncluttered. Mirrors the venue-side three-line
  // layout (Slot N · time · pay  /  type info  /  artist row).
  const _slotType = slot.artist_type || data.artist_type || '';
  let typeInfoText = '';
  if (_slotType) {
    typeInfoText = `${icon} ${_esc(_slotType)}`;
    if (_slotType === 'Live Band') {
      const fmts = slot.band_formats || data.band_formats || '';
      const stls = slot.styles || data.styles || '';
      if (fmts) typeInfoText += ` · ${_esc(fmts)}`;
      if (stls) typeInfoText += ` · ${_esc(stls)}`;
    }
  }
  const typeInfoHtml = typeInfoText
    ? `<div style="margin-top:5px;color:var(--text-muted);font-size:0.78rem;line-height:1.4;font-style:italic;">${typeInfoText}</div>`
    : '';

  // Right-side content per relationship
  let rightHtml = '';
  let extraHtml = '';  // Below the main row (countersign form, waitlist, etc.)

  if (isPast || slotEnded) {
    if (isMySlot) {
      rightHtml = `<span style="color:#06b6d4;font-weight:600;font-size:0.8rem;">✓ Your Slot</span>`;
    } else if (isBooked) {
      rightHtml = `<a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
        style="color:#22c55e;font-size:0.8rem;text-decoration:none;font-weight:500;">${_esc(slot.artist_name||'Booked')}</a>`;
    } else {
      rightHtml = `<span style="color:var(--text-muted);font-size:0.75rem;">Ended</span>`;
    }
  } else if (isInProgress) {
    if (isMySlot) {
      rightHtml = `<span style="color:#06b6d4;font-weight:600;font-size:0.8rem;">✓ Your Slot (In Progress)</span>`;
    } else if (isBooked) {
      rightHtml = `<a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
        style="color:#22c55e;font-size:0.8rem;text-decoration:none;">${_esc(slot.artist_name||'Booked')}</a>`;
    } else if (!slotStarted) {
      rightHtml = `<span style="color:var(--text-muted);font-size:0.8rem;">Open</span>`;
    } else {
      rightHtml = `<span style="color:#6b7280;font-size:0.75rem;">Started</span>`;
    }
  } else {
    // Active gig
    switch (rel) {
      case 'mine_booked':
        rightHtml = `<span style="color:#06b6d4;font-weight:600;font-size:0.8rem;">✓ Your Slot</span>`;
        break;

      case 'mine_pending_contract': {
        const cs = slot.contract_status;
        if (cs === 'artist_signed') {
          rightHtml = `<span style="color:#22c55e;font-size:0.8rem;font-weight:600;">✓ Signed — awaiting venue</span>`;
          extraHtml += `<div style="margin-top:8px;padding:8px 12px;background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.2);border-radius:6px;">
            <p style="margin:0;font-size:0.82rem;color:#22c55e;line-height:1.5;">
              ✓ <strong>Contract Signed</strong> — Waiting for the venue to countersign and confirm your booking.
            </p>
          </div>`;
        } else if (cs === 'pending' && slot.contract_pdf_url) {
          rightHtml = `<span style="color:#eab308;font-size:0.8rem;font-weight:600;">📋 Sign Required</span>`;
          const hrs = _hoursUntilExpiry(slot.hold_expires_at);
          extraHtml += `<div style="margin-top:8px;padding:10px 12px;background:rgba(234,179,8,0.08);border:1px solid rgba(234,179,8,0.25);border-radius:6px;">
            <p style="margin:0 0 8px;font-size:0.85rem;color:#eab308;font-weight:600;">📋 Contract Signature Required</p>
            <p style="margin:0 0 8px;font-size:0.8rem;color:var(--text-muted);">
              Download, sign, and upload to confirm your booking.
              ${hrs ? `Hold expires in ~${hrs} hours.` : ''}
            </p>
            <a href="${slot.contract_pdf_url}" download class="_gig-btn _gig-btn-ghost" style="font-size:0.8rem;padding:4px 12px;display:inline-block;margin-right:8px;">⬇ Download</a>
            <label class="_gig-btn _gig-btn-primary" style="font-size:0.8rem;padding:4px 12px;cursor:pointer;display:inline-block;">
              ⬆ Upload Signed PDF
              <input type="file" accept=".pdf" style="display:none;"
                onchange="window._uploadSignedPdf && window._uploadSignedPdf(this, ${slot.contract_id})">
            </label>
            <div id="uploadSignedStatus" style="font-size:0.75rem;margin-top:6px;"></div>
          </div>`;
        } else if (cs === 'pending') {
          rightHtml = `<span style="color:#eab308;font-size:0.8rem;font-weight:600;">📋 Sign Required</span>`;
          extraHtml += `<div style="margin-top:8px;padding:8px 12px;background:rgba(234,179,8,0.08);border:1px solid rgba(234,179,8,0.25);border-radius:6px;">
            <p style="margin:0 0 6px;font-size:0.85rem;color:#eab308;font-weight:600;">📋 Contract Signature Required</p>
            <a href="/app/contract-sign.html?contract_id=${slot.contract_id}" class="_gig-btn _gig-btn-primary" style="font-size:0.8rem;padding:4px 14px;">Review & Sign Contract</a>
          </div>`;
        }
        break;
      }

      case 'mine_awaiting_venue':
        rightHtml = `<span style="color:#eab308;font-size:0.8rem;font-weight:600;">⏳ Awaiting Venue Contract</span>`;
        extraHtml += _banner('yellow', '⏳ Awaiting Contract From Venue',
          'The venue is preparing a contract. You\'ll be notified when it\'s ready.');
        break;

      case 'mine_pending_approval':
        rightHtml = `<span style="color:#fbbf24;font-size:0.8rem;font-weight:600;">⏳ Awaiting Approval</span>`;
        extraHtml += _banner('yellow', '⏳ Awaiting Venue Approval',
          'Your same-day booking request has been sent. You\'ll be notified when approved.');
        break;

      case 'venue_booked':
        if (slot.artist_id) {
          const _vbName = _esc(slot.artist_name || 'Booked');
          const _msgCb = `typeof openMessageModal==='function'&&openMessageModal(${data.id},'${_esc(data.venue_name)}',${slot.artist_id})`;
          rightHtml = `<div style="display:flex;align-items:center;gap:8px;">
            <a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
              style="color:#22c55e;font-size:0.8rem;text-decoration:none;font-weight:500;">${_vbName}</a>
            <button onclick="${_msgCb}"
              style="background:transparent;border:1px solid rgba(6,182,212,0.4);color:#06b6d4;border-radius:4px;padding:2px 8px;font-size:0.72rem;cursor:pointer;white-space:nowrap;">
              Message
            </button>
          </div>`;
        } else {
          rightHtml = `<span style="color:#22c55e;font-size:0.8rem;font-weight:500;">Booked</span>`;
        }
        break;

      case 'venue_pending_contract': {
        const cs2 = slot.contract_status;
        rightHtml = slot.artist_id
          ? `<a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
              style="color:#a78bfa;font-size:0.8rem;text-decoration:none;font-weight:500;">
              ${_esc(slot.artist_name||'Artist')}</a>`
          : `<span style="color:#a78bfa;font-size:0.8rem;">Pending Contract</span>`;

        if (cs2 === 'artist_signed') {
          extraHtml += _countersignBlock(slot, onCountersign, data.id);
        } else if (cs2 === 'pending' && slot.contract_pdf_url) {
          extraHtml += `<div style="margin-top:8px;padding:8px 12px;background:rgba(234,179,8,0.08);border:1px solid rgba(234,179,8,0.25);border-radius:6px;">
            <p style="margin:0;font-size:0.82rem;color:#eab308;">⏳ Waiting for artist to sign and upload the contract PDF.</p>
          </div>`;
        } else if (cs2 === 'pending') {
          extraHtml += `<div style="margin-top:8px;padding:8px 12px;background:rgba(234,179,8,0.08);border:1px solid rgba(234,179,8,0.25);border-radius:6px;">
            <p style="margin:0;font-size:0.82rem;color:#eab308;">⏳ Waiting for artist to sign the digital contract.</p>
          </div>`;
        }
        break;
      }

      case 'venue_awaiting_upload':
        rightHtml = slot.artist_id
          ? `<a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
              style="color:#67e8f9;font-size:0.8rem;text-decoration:none;">
              ${_esc(slot.artist_name||'Artist')} ⏳</a>`
          : `<span style="color:#67e8f9;font-size:0.8rem;">⏳ Upload Needed</span>`;
        extraHtml += `<div style="margin-top:8px;padding:10px 12px;background:rgba(6,182,212,0.07);border:1px solid rgba(6,182,212,0.2);border-radius:6px;">
          <p style="margin:0 0 8px;font-size:0.82rem;color:#67e8f9;line-height:1.5;">
            Upload a PDF contract for this specific gig. The artist will have 24 hours to download, sign, and upload it back.
          </p>
          <label class="_gig-btn _gig-btn-primary" style="font-size:0.8rem;padding:4px 14px;cursor:pointer;display:inline-block;">
            Upload Contract PDF
            <input type="file" accept=".pdf" style="display:none;"
              onchange="window._uploadVenueGigPdf && window._uploadVenueGigPdf(this, ${data.id}, ${data.venue_id})">
          </label>
          <div id="venueGigPdfStatus" style="font-size:0.75rem;margin-top:6px;"></div>
        </div>`;
        break;

      case 'venue_pending_approval':
        rightHtml = `<span style="color:#fbbf24;font-size:0.8rem;font-weight:600;">⏳ Pending Approval</span>`;
        extraHtml += `<div style="margin-top:8px;padding:8px 12px;background:rgba(234,179,8,0.08);border:1px solid rgba(234,179,8,0.25);border-radius:6px;">
          <p style="margin:0;font-size:0.82rem;color:#fbbf24;">
            ${_esc(slot.artist_name||'An artist')} has a same-day booking request pending your approval.
          </p>
        </div>`;
        break;

      case 'other_booked':
      case 'other_pending_approval': {
        // Another artist's slot — show name + waitlist option for artist viewers
        rightHtml = slot.artist_id
          ? `<a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
              style="color:${rel==='other_booked'?'#22c55e':'#fbbf24'};font-size:0.8rem;text-decoration:none;font-weight:500;">
              ${_esc(slot.artist_name||'Booked')}</a>`
          : `<span style="color:#22c55e;font-size:0.8rem;">Booked</span>`;

        // Waitlist join/leave for artist viewer on booked slot
        const _hasOpenSlot = (data.slots || []).some(s =>
          s.status === 'open' && s.relationship === 'open_bookable'
        );
        const _artistHasSlot = (data.slots || []).some(s => s.is_my_slot);
        if (vType === 'artist' && !isPast && !slotStarted && !_artistHasSlot) {
          const wls = data.waitlist_status || {};
          if (data.preferred_status === 'approved') {
            if (wls.on_waitlist) {
              extraHtml += `<div style="display:flex;align-items:center;gap:8px;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.07);">
                <button onclick="window._gmCbs&&window._gmCbs.leaveWaitlist?window._gmCbs.leaveWaitlist(${data.id},${data.viewer_id}):leaveWaitlist(${data.id},${data.viewer_id})"
                  class="_gig-btn _gig-btn-primary" style="font-size:0.75rem;padding:3px 10px;">
                  Leave Waitlist (${wls.position||'?'} of ${wls.total||1})
                </button>
                <span style="color:var(--text-muted);font-size:0.75rem;">You'll be notified if this slot opens.</span>
              </div>`;
            } else if (!data.freq_check?.blocked) {
              // Store gig/artist ids for the waitlist modal
              window._gmWlGigId = data.id;
              window._gmWlArtistId = data.viewer_id;
              const _wlOnclick = _hasOpenSlot
                ? "window._gmShowWlConfirm()"
                : "window._gmCbs&&window._gmCbs.joinWaitlist?window._gmCbs.joinWaitlist(window._gmWlGigId,window._gmWlArtistId):joinWaitlist(window._gmWlGigId,window._gmWlArtistId)";
              extraHtml += `<div style="display:flex;align-items:center;gap:8px;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.07);">
                <button onclick="${_wlOnclick}"
                  class="_gig-btn _gig-btn-cyan" style="font-size:0.75rem;padding:3px 10px;">
                  Join Waitlist
                </button>
                <span style="color:var(--text-muted);font-size:0.75rem;">${_hasOpenSlot ? 'Other slots still open — or join the waitlist for this one.' : 'Get notified if this slot opens up.'}</span>
              </div>`;
            }
          }
        }
        break;
      }

      case 'open_bookable': {
        if (slotStarted) {
          rightHtml = `<span style="color:#6b7280;font-size:0.75rem;">Started</span>`;
        } else if (onBook) {
          const _hasOffer = data.waitlist_status?.has_offer;
          // Only show Not Available on the first open slot to avoid repeating it
          const _openBookable = (data.slots || []).filter(s => s.relationship === 'open_bookable');
          const _isFirstOpen = _openBookable.length > 0 && _openBookable[0].id === slot.id;
          const _notAvailBtn = (_hasOffer && _isFirstOpen)
            ? `<button class="_gig-btn _gig-btn-ghost" style="font-size:0.8rem;padding:4px 10px;"
                onclick="window._gmCbs&&window._gmCbs.leaveWaitlist?window._gmCbs.leaveWaitlist(${data.id},${data.viewer_id}):leaveWaitlist(${data.id},${data.viewer_id})">
                Not Available
              </button>` : '';
          rightHtml = `<div style="display:flex;gap:6px;align-items:center;">
            ${_notAvailBtn}
            <button class="_gig-btn _gig-btn-primary book-slot-btn"
              data-slot-id="${slot.id}" data-slot-num="${slot.slot_number}"
              style="font-size:0.8rem;padding:4px 12px;">Book</button>
          </div>`;
        }
        break;
      }

      case 'already_have_slot':
        rightHtml = `<button class="_gig-btn" disabled
          style="font-size:0.8rem;padding:4px 12px;opacity:0.4;cursor:not-allowed;">Book</button>`;
        break;

      case 'freq_blocked':
        rightHtml = `<button class="_gig-btn" disabled
          style="font-size:0.8rem;padding:4px 12px;opacity:0.4;cursor:not-allowed;background:#333;"
          title="Frequency limit at this venue">Book</button>`;
        break;

      case 'not_preferred':
        rightHtml = `<button class="_gig-btn" disabled
          style="font-size:0.8rem;padding:4px 12px;opacity:0.4;cursor:not-allowed;background:#333;"
          title="Preferred status required">Book</button>`;
        break;

      default:
        rightHtml = isBooked
          ? `<span style="color:#22c55e;font-size:0.8rem;">${_esc(slot.artist_name||'Booked')}</span>`
          : `<span style="color:var(--text-muted);font-size:0.8rem;">Open</span>`;
    }
  }

  return `
    <div style="padding:10px 12px 10px 10px;background:${bgColor};border:1px solid ${borderColor};border-left:3px solid #a855f7;border-radius:6px;margin-bottom:6px;opacity:${opacity};">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <span style="font-weight:700;color:#a855f7;font-size:0.85rem;letter-spacing:0.3px;">Slot ${slot.slot_number}</span>
        <span style="color:#cbd5e1;font-size:0.85rem;">${slotTime}</span>
        ${payHtml}
        <span style="flex:1;"></span>
        <div>${rightHtml}</div>
      </div>
      ${typeInfoHtml}
      ${extraHtml}
    </div>`;
}

/* ── Countersign block for venue ─────────────────────────────────────────── */
function _countersignBlock(slot, onCountersign, gigId) {
  const sigDate = slot.artist_sig_date
    ? new Date(slot.artist_sig_date).toLocaleDateString() : '';
  const contractBodyHtml = slot.contract_body
    ? `<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:6px;padding:12px;max-height:200px;overflow-y:auto;margin-bottom:10px;font-size:0.8rem;line-height:1.7;color:var(--text);">${slot.contract_body}</div>`
    : '';
  return `
    <div style="margin-top:10px;padding:10px 12px;background:rgba(139,92,246,0.07);border:1px solid rgba(139,92,246,0.2);border-radius:6px;">
      ${contractBodyHtml}
      <div style="background:rgba(34,197,94,0.07);border:1px solid rgba(34,197,94,0.2);border-radius:6px;padding:8px;margin-bottom:10px;">
        <p style="margin:0;font-size:0.8rem;color:#22c55e;">
          ✓ Signed by <strong>${_esc(slot.artist_sig_name||'Artist')}</strong>${sigDate?' on '+sigDate:''}
        </p>
      </div>
      <label style="display:block;font-size:0.85rem;color:var(--text-muted);margin-bottom:6px;font-weight:600;">
        Your Full Legal Name (Countersignature)
      </label>
      <input type="text" id="modalCountersignName_${slot.contract_id}"
        placeholder="Type your full legal name"
        style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:#fff;font-size:0.9rem;box-sizing:border-box;">
      <div style="margin-top:10px;display:flex;align-items:center;gap:10px;">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <button onclick="window._doCountersign && window._doCountersign(${slot.contract_id})"
            id="modalCountersignBtn"
            style="padding:6px 16px;font-size:0.85rem;background:#3b82f6;border:1px solid #3b82f6;color:#fff;border-radius:6px;cursor:pointer;font-weight:600;transition:background 0.2s;"
            onmouseover="this.style.background='#2563eb'" onmouseout="this.style.background='#3b82f6'">
            Countersign & Confirm Booking
          </button>
          ${slot.artist_id ? `<button onclick="typeof openMessageModal==='function'&&openMessageModal(${gigId},'${_esc(slot.artist_name||'Artist')}',${slot.artist_id})"
            style="padding:6px 14px;font-size:0.82rem;background:transparent;border:1px solid rgba(6,182,212,0.4);color:#06b6d4;border-radius:6px;cursor:pointer;">
            💬 Message Artist
          </button>` : ''}
        </div>
        <span id="modalCountersignStatus" style="font-size:0.82rem;"></span>
      </div>
    </div>`;
}

/* ── Small helpers ────────────────────────────────────────────────────────── */
function _banner(color, title, body) {
  const colors = {
    red:    { bg: 'rgba(239,68,68,0.12)',   border: 'rgba(239,68,68,0.4)',   text: '#ef4444',  sub: '#fca5a5' },
    yellow: { bg: 'rgba(234,179,8,0.1)',    border: 'rgba(234,179,8,0.3)',   text: '#eab308',  sub: 'var(--text-muted)' },
    green:  { bg: 'rgba(34,197,94,0.12)',   border: 'rgba(34,197,94,0.5)',   text: '#22c55e',  sub: '#86efac' },
    purple: { bg: 'rgba(139,92,246,0.1)',   border: 'rgba(139,92,246,0.3)',  text: '#a78bfa',  sub: 'var(--text-muted)' },
  };
  const c = colors[color] || colors.yellow;
  return `<div style="margin-bottom:14px;padding:14px;background:${c.bg};border:1px solid ${c.border};border-radius:8px;">
    <p style="margin:0 0 4px;font-size:0.9rem;font-weight:700;color:${c.text};">${title}</p>
    <p style="margin:0;font-size:0.83rem;color:${c.sub};line-height:1.5;">${body}</p>
  </div>`;
}

function _closeBtn(close) {
  return `<button class="_gig-btn _gig-btn-ghost" onclick="window._gmClose && window._gmClose()">Close</button>`;
}

function _commit(html, actionsHtml) {
  return { html, actionsHtml };
}

/* ── Mount into DOM ───────────────────────────────────────────────────────── */
function mountGigModal(result, bodyEl, titleText) {
  if (!result || !bodyEl) return;
  const { html, actionsHtml } = result;
  bodyEl.innerHTML = html + `<div id="gigModalActionsRow">${actionsHtml}</div>`;
  const ma = document.getElementById('modalActions');
  if (ma) { ma.innerHTML = ''; ma.style.display = 'none'; }
  const titleEl = document.getElementById('modalTitle');
  if (titleEl && titleText) titleEl.textContent = titleText;
}

/* ── Waitlist open-slot confirmation modal ────────────────────────────────── */
window._gmShowWlConfirm = function() {
  const gigId    = window._gmWlGigId;
  const artistId = window._gmWlArtistId;
  const doJoin   = () => {
    window._gmCbs && window._gmCbs.joinWaitlist
      ? window._gmCbs.joinWaitlist(gigId, artistId)
      : (typeof joinWaitlist === 'function' && joinWaitlist(gigId, artistId));
  };
  if (typeof showStyledModal === 'function') {
    showStyledModal(
      'Other Slots Available',
      `<p style="color:#e5e5e5;text-align:center;line-height:1.7;">
        There are still open slots on this gig you can book directly.<br><br>
        Do you want to join the waitlist for this specific slot instead?
      </p>`,
      [
        { text: 'Book an Open Slot', style: 'ghost', action: () => {} },
        { text: 'Join Waitlist Anyway', style: 'primary', action: doJoin }
      ]
    );
  } else {
    doJoin();
  }
};

window.fetchModalData   = fetchModalData;
window.renderGigModal   = renderGigModal;
window.mountGigModal    = mountGigModal;
