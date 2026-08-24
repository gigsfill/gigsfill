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
    } else if ((data.slots || []).length > 1) {
      // Truly multi-slot with no artist slot found — omit the umbrella
      // time; each slot row below shows its own time. (Was gated on the
      // deprecated is_multi_slot flag pre-Jul-2026 unification.)
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

  // FIX (May 21 2026): for VENUE viewers, the modal now matches the
  // Past Event Details shape — just Date + Event at the top. The venue
  // owns the venue, so Venue / Location / Artist Type / Lineup / Styles /
  // Notes are either redundant (venue knows) or per-slot data shown in
  // the slot rows below. Artists still see the full header since they
  // need the where/what context.
  const _isMultiSlotHdr = Array.isArray(data.slots) && data.slots.length > 1;
  const _isVenueView    = vType === 'venue';

  if (_isVenueView) {
    if (displayStart) {
      html += `<div style="font-weight:600;color:var(--text-primary);">Time:</div>
               <div style="color:var(--text-primary);">${displayStart} – ${displayEnd}</div>`;
    }
    if (data.title) {
      html += `<div style="font-weight:600;color:var(--text-primary);">Event:</div>
               <div style="color:var(--text-primary);">${_esc(data.title)}</div>`;
    }
  } else {
    // Artist view — keep the full header (they need where/what).
    if (displayStart) {
      html += `<div style="font-weight:600;color:var(--text-primary);">Time:</div>
               <div style="color:var(--text-primary);">${displayStart} – ${displayEnd}</div>`;
    }
    html += `<div style="font-weight:600;color:var(--text-primary);">Venue:</div>
             <div style="color:var(--text-primary);">
               <a href="/app/venue-profile.html?venue_id=${data.venue_id}" target="_blank"
                  style="color:var(--accent-cyan,#06b6d4);text-decoration:none;"
                  onmouseover="this.style.textDecoration='underline'"
                  onmouseout="this.style.textDecoration='none'">${_esc(data.venue_name)}</a><span class="vgd-link-slot" data-vgd-vid="${parseInt(data.venue_id,10)||0}" data-vgd-name="${_esc(data.venue_name||'')}"></span>
             </div>`;
    if (data.address_line_1 || data.city) {
      html += `<div style="font-weight:600;color:var(--text-primary);">Location:</div>
               <div style="color:var(--text-primary);">
                 ${data.address_line_1 ? _esc(data.address_line_1) + '<br>' : ''}
                 ${data.address_line_2 ? _esc(data.address_line_2) + '<br>' : ''}
                 ${_esc(data.city || '')}${data.state ? ', ' + _esc(data.state) : ''}
               </div>`;
    }
    // For MULTI-SLOT gigs, skip the gig-level Artist Type / Lineup / Styles
    // rows — each slot can have its own values, and they're rendered per
    // slot below.
    if (data.artist_type && !_isMultiSlotHdr) {
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

  // 2026-08-21: Free Trial venue banner. Rendered for BOTH artist +
  // venue viewers on any non-past gig so both parties know before an
  // action fires that GigsFill won't process payment. Placed above the
  // preferred-status gates so an artist requesting preferred at a
  // free-trial venue still sees the trial context. Skipped on past /
  // in-progress gigs since it's history at that point (post-facto is
  // covered by the "🎟 Free Trial" pill in the Payments dashboards).
  if (data.is_free_trial && !isPast && !isInProgress) {
    const _ftMsg = (vType === 'artist')
      ? `<strong>${_esc(data.venue_name)}</strong> is on GigsFill Free Trial. If you book this gig, <strong>${_esc(data.venue_name)}</strong> will pay you directly &mdash; GigsFill won't process the payment or send anything to your Stripe account.`
      : `You're on GigsFill Free Trial. If an artist books this gig, your card will not be charged &mdash; please arrange payment directly with them.`;
    html += _banner('yellow', '🎟 Free Trial Venue', _ftMsg);
  }

  // PAST GIG
  if (isPast) {
    html += _slotsSection(data, vType, {isPast: true, isInProgress: false, close, callbacks});
    const myBookedSlot = (data.slots || []).find(s => s.is_my_slot && s.status === 'booked');
    if (myBookedSlot && vType === 'artist') {
      const msgBtn = (onMessage && data.can_message !== false) ? `<button class="_gig-btn _gig-btn-cyan" onclick="window._gmCbs&&window._gmCbs.message&&window._gmCbs.message(${parseInt(data.id,10)||0},${(window.jsAttr||JSON.stringify)(data.venue_name)},${parseInt(data.viewer_id,10)||0})">Message Venue</button>` : '';
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
      const msgBtn = (onMessage && data.can_message !== false) ? `<button class="_gig-btn _gig-btn-cyan" onclick="window._gmCbs&&window._gmCbs.message&&window._gmCbs.message(${parseInt(data.id,10)||0},${(window.jsAttr||JSON.stringify)(data.venue_name)},${parseInt(data.viewer_id,10)||0})">Message Venue</button>` : '';
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

    // 2026-08-15: mirror the "Preferred Status Required" banner for the
    // opposite case — the artist ISN'T preferred at this venue, but the
    // gig is open-blasted (or the venue marked this specific gig freq-
    // exempt), so the "Required" branch above was skipped and the Book
    // button appears with no explanation of why. Without this banner
    // artists were confused: "Am I preferred here? Why can I book?"
    // Skipped when the Frequency Rule Lifted banner below already covers
    // the same reason (freq_waiver.reason === 'blast' says essentially
    // the same thing to an artist with prior history at this venue).
    if (pref === null && isBlastOpen
        && (!data.freq_waiver || data.freq_waiver.reason !== 'blast')) {
      const _openTitle = '🎉 Open to All Artists';
      const _openMsg = data.frequency_exempt
        ? `${_esc(data.venue_name)} marked this gig frequency-exempt — you don't need Preferred Artist status to book it.`
        : `${_esc(data.venue_name)} has opened this gig to all nearby artists — you don't need Preferred Artist status to book it.`;
      html += _banner('green', _openTitle, _openMsg);
    }

    // 2026-08-23: same-day booking banner. Fires for any artist viewing
    // a gig whose start is within 36h (venue-local, per _is_same_day_
    // booking on the backend). Copy varies by (a) whether the venue
    // requires approval for same-day bookings, and (b) whether THIS
    // artist bypasses that gate (preferred artists always do — see
    // gigs.py:5288 `not _is_preferred_slot`). Skipped for the artist's
    // own booked slot (they've already booked, no new context needed).
    // 2026-08-24 fix: was showing "Requires Approval" to preferred
    // artists too, but their bookings actually go straight to `booked`.
    if (data.is_same_day && !isPast && !isInProgress) {
      const _mySlot = (data.slots || []).find(s => s && s.is_my_slot);
      const _isPreferred = pref === 'approved';
      if (!_mySlot) {
        if (data.same_day_requires_approval && !_isPreferred) {
          html += _banner('yellow', '🕐 Same-Day Booking Requires Approval',
            `This gig is <strong>today</strong>. <strong>${_esc(data.venue_name)}</strong> requires venue approval for same-day bookings by non-preferred artists — if you book, your slot will be marked <em>pending venue approval</em> and you'll be notified once the venue accepts or declines.`);
        } else {
          html += _banner('yellow', '🕐 Same-Day Booking',
            `Heads up — this gig is <strong>today</strong>. Book now if you can perform on this short notice.`);
        }
      }
    }

    // Freq-waiver banner (Jun 2026): render BEFORE the Hold-feature
    // panel so artists with an active hold offer (or queued / declined
    // state) still see the "Frequency Rule Lifted" notice. The same
    // banner block lower in this file is unreachable when a hold panel
    // takes the early-return path.
    if (vType === 'artist' && data.freq_waiver) {
      const fw = data.freq_waiver;
      // daysBetween = current_gig_date - last_gig_date. Positive means
      // current gig is AFTER last gig → last gig was BEFORE this one.
      const dir = (fw.daysBetween > 0) ? 'before' : 'after';
      // Jul 2026: rewrote the "window" branch to be unambiguous. The prior
      // copy said "inside the venue's open-window for last-minute bookings"
      // which conflated (a) how far apart the two gigs are with (b) how
      // close today is to the gig date. Venues + artists were misreading it
      // as "1 week gap between gigs" when the actual math is "today is
      // inside the 1-week-before-gig-date reminder window".
      let _waiverNote;
      if (fw.reason === 'exempt') {
        _waiverNote = 'the venue marked this specific gig frequency-exempt';
      } else if (fw.reason === 'blast') {
        _waiverNote = 'this gig is currently open-blasted to all artists in the area';
      } else {
        // reason === 'window' — cite the specific window + days-until so
        // the artist sees exactly which policy the venue set is now active.
        const dU = fw.daysUntilGig;
        let _windowLabel;
        if (fw.activeWindow === 'open_gig_36h') {
          _windowLabel = "the venue's 36-hour last-minute reminder is active";
        } else if (fw.activeWindow === 'open_gig_1w') {
          _windowLabel = "the venue's 1-week reminder is active";
        } else {
          _windowLabel = "the venue's reminder window is active";
        }
        const _dUphrase = (dU != null)
          ? `the gig is ${dU} day${dU !== 1 ? 's' : ''} away, so `
          : '';
        _waiverNote = `${_dUphrase}${_windowLabel} — the venue has chosen to lift frequency limits during this window so any Preferred Artist can fill the slot`;
      }
      html += _banner('yellow', 'Frequency Rule Lifted',
        `Your last gig here was ${fw.absDaysBetween} day${fw.absDaysBetween!==1?'s':''} ${dir} this one (venue normally requires ${fw.daysRequired} days between bookings), but ${_waiverNote}. You can book this one.`);
    }

    // ── Hold-feature panel (Jun 2026) ────────────────────────────────
    // For held gigs, render the appropriate state inline in the modal
    // so the calendar bubble click experience mirrors the Pending
    // Offers banner at the top of the page. Short-circuits the legacy
    // waitlist UI for held gigs.
    if (data.hold_info && data.hold_info.is_held) {
      const hi = data.hold_info;
      // Series-hold safety net: if for some reason a series-hold gig
      // modal was rendered here (e.g. opened from a path that didn't
      // intercept), surface a clear CTA to open the bundled picker
      // instead of the per-gig book/decline UI. The PRIMARY redirect
      // happens in artist.book-gigs.js openGigModal before this code
      // runs — this is a fallback for the inline modal-data path.
      if (hi.my_state === 'current_offer' && hi.is_series && hi.series_offer_token) {
        html += `<div style="margin-bottom:14px;background:rgba(124,107,255,0.10);border:1px solid rgba(124,107,255,0.40);border-radius:10px;padding:14px 18px;">
          <div style="font-size:0.85rem;font-weight:700;color:#a78bfa;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;">📅 Series Offer</div>
          <p style="margin:0 0 12px 0;font-size:0.86rem;color:var(--text);">This date is part of a recurring series offered to you. Pick this and other dates together — frequency rules apply automatically as you select.</p>
          <button type="button" onclick="window.openSeriesHoldModal && window.openSeriesHoldModal('${hi.series_offer_token}')"
            style="padding:8px 18px;background:#7c6bff;border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:0.84rem;font-weight:600;">Pick Your Dates →</button>
        </div>`;
        actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
        return _commit(html, actionsHtml);
      }
      if (hi.my_state === 'current_offer') {
        // Same vocabulary as the Pending Offers banner: time-left,
        // per-slot Book/Decline. Click flows through to the same
        // /hold/respond endpoints via window.loadHoldOffersBanner
        // (re-fetches + scrolls to top after action).
        const hrs = hi.hours_remaining;
        let timeLeft = '';
        if (hrs != null) {
          const totalMins = Math.round(hrs * 60);
          if (totalMins <= 0) timeLeft = 'expiring now';
          else if (totalMins < 60) timeLeft = `${totalMins} ${totalMins===1?'minute':'minutes'} left to book`;
          else if (hrs < 24) { const h = Math.round(hrs); timeLeft = `${h} ${h===1?'hour':'hours'} left to book`; }
          else { const d = Math.round(hrs/24); timeLeft = `${d} ${d===1?'day':'days'} left to book`; }
        }
        const _typeIcon = t => ({'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'❓'}[t] || '🎵');
        // Pay string respects door-deal terms so the artist sees the
        // CURRENT slot config when the venue swaps a slot to door
        // mid-cycle. '$10 + 50% door' instead of just '$10'.
        const _payStr = (s) => {
          if (s && typeof s === 'object') {
            // Backend's /api/gigs/{id}/modal-data.hold_info.my_matching_slots
            // already applies the per-artist pay override per the
            // unified rule (flat → always; door → only when slot.apply_override=1).
            // For door slots opted-in, s.pay = effective guarantee; for
            // door slots NOT opted-in, s.pay still reflects the published
            // guarantee value. Either way, s.pay is the right dollar amount.
            const p = Number(s.pay) || 0;
            const pFmt = p % 1 === 0 ? `$${Math.round(p)}` : `$${p.toFixed(2)}`;
            if (s.deal_type === 'door') {
              return `${pFmt} + ${parseInt(s.door_pct, 10) || 0}% door`;
            }
            return pFmt;
          }
          // Backward compat: scalar pay
          const p = Number(s) || 0;
          return p % 1 === 0 ? `$${Math.round(p)}` : `$${p.toFixed(2)}`;
        };
        const slots = hi.my_matching_slots || [];
        let slotsHtml = '';
        if (slots.length === 0) {
          slotsHtml = '<div style="font-size:0.82rem;color:var(--text-gray);font-style:italic;">No slots match your artist type.</div>';
        } else {
          // Vertical layout: each slot on its own row with fixed-width
          // columns so icon / slot-label / time / pay / buttons line
          // up across rows. tabular-nums + min-width on the time column
          // (sized to '12:00 PM – 12:00 PM') keeps the pay column
          // aligned even when one slot is '1:00 PM' and another is
          // '12:00 PM'.
          // Vertical layout with generous column widths. Time column
          // uses 18ch so 'XX:XX PM – XX:XX PM' (max 18 chars) fits
          // without bleeding into the pay column. Pay is centered
          // between time and buttons via flex distribution + auto
          // margin on both sides.
          // Render EVERY open slot. Slots the viewing artist can fill
          // (bookable_by_me=true) get the existing purple Book + red
          // Decline buttons. Slots the artist can't fill (e.g. a DJ
          // slot offered to a Live Band artist) are dimmed to 50%,
          // get a dark slate background instead of purple, and show
          // a "<Type> only" hint in place of the buttons. This way
          // the artist sees the full shape of the gig instead of
          // wondering why "the 3rd slot disappeared."
          slotsHtml = '<div style="display:flex;flex-direction:column;gap:6px;">'
            + slots.map(s => {
              const slotLabel = slots.length > 1 ? `Slot ${s.slot_number}` : '';
              const bookable = s.bookable_by_me !== false;  // default true for backwards-compat
              const containerBg = bookable
                ? 'background:rgba(124,107,255,0.10);border:1px solid rgba(124,107,255,0.35);'
                : 'background:rgba(0,0,0,0.30);border:1px solid rgba(148,163,184,0.18);';
              const opacity = bookable ? '1' : '0.45';
              const textColor = bookable ? 'var(--text)' : '#94a3b8';
              const payColor = bookable ? '#22c55e' : '#94a3b8';
              const actionsHtml = bookable
                ? `<button type="button" data-token="${hi.offer_token}" data-slot="${s.id}"
                      data-gig="${data.id || ''}" data-vid="${data.venue_id || ''}" data-aid="${data.viewer_id || ''}"
                      data-venue="${(data.venue_name||'').replace(/"/g,'&quot;')}" data-date="${data.date||''}"
                      data-slot-num="${s.slot_number}" data-time="${s.time}" data-pay="${_payStr(s)}"
                      onclick="window.gmHoldBook && window.gmHoldBook(this)"
                      title="Book this slot now."
                      style="padding:5px 18px;background:#7c6bff;border:none;border-radius:4px;color:#fff;cursor:pointer;font-size:0.74rem;font-weight:600;min-width:72px;">Book</button>
                    <button type="button" data-token="${hi.offer_token}"
                      onclick="window.gmHoldDecline && window.gmHoldDecline(this)"
                      title="Decline if you are unable to perform on this day."
                      style="padding:5px 14px;background:transparent;border:1px solid #dc2626;border-radius:4px;color:#f87171;cursor:pointer;font-size:0.74rem;font-weight:600;min-width:78px;">Decline</button>`
                : `<span title="This slot is for ${_esc(s.artist_type || 'a different artist type')}, so it isn't bookable by you."
                       style="padding:4px 12px;font-size:0.72rem;color:#94a3b8;background:rgba(148,163,184,0.10);border:1px solid rgba(148,163,184,0.25);border-radius:4px;font-weight:600;white-space:nowrap;min-width:152px;text-align:center;">
                       ${_esc(s.artist_type || 'Other type')} only
                     </span>`;
              return `<div style="display:flex;align-items:center;gap:14px;${containerBg}border-radius:8px;padding:8px 14px;opacity:${opacity};">
                <span style="display:inline-block;width:18px;text-align:center;flex:0 0 18px;font-size:0.95rem;">${_typeIcon(s.artist_type)}</span>
                ${slotLabel ? `<span style="display:inline-block;width:60px;flex:0 0 60px;font-size:0.84rem;color:${textColor};font-weight:600;white-space:nowrap;">${slotLabel}</span>` : ''}
                <span style="display:inline-block;width:18ch;flex:0 0 18ch;font-size:0.84rem;color:${textColor};white-space:nowrap;font-variant-numeric:tabular-nums;">${s.time}</span>
                <span style="display:inline-block;flex:1;text-align:center;font-size:0.86rem;color:${payColor};font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap;">${_payStr(s)}</span>
                <span style="display:inline-flex;gap:6px;flex:0 0 auto;">${actionsHtml}</span>
              </div>`;
            }).join('') + '</div>';
        }
        html += `<div style="margin-bottom:14px;background:rgba(245,158,11,0.08);border:1.5px solid rgba(245,158,11,0.5);border-radius:10px;padding:12px 16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
            <span style="font-size:0.85rem;font-weight:700;color:#fcd34d;text-transform:uppercase;letter-spacing:0.05em;">🔒 Pending Offer</span>
            ${timeLeft ? `<span style="color:${hrs < 6 ? '#ef4444' : '#fcd34d'};font-weight:600;font-size:0.8rem;">${timeLeft}</span>` : ''}
          </div>
          <div>${slotsHtml}</div>
        </div>`;
        actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
        return _commit(html, actionsHtml);
      }
      if (hi.my_state === 'queued') {
        // Generic message — don't reveal queue position (might feel
        // discouraging if they're #5 on a list). Per user request.
        html += `<div style="margin-bottom:14px;background:rgba(139,92,246,0.10);border:1px solid rgba(139,92,246,0.4);border-radius:10px;padding:14px 18px;">
          <div style="font-size:0.85rem;font-weight:700;color:#a78bfa;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">🔒 Hold in Progress</div>
          <p style="margin:0;font-size:0.86rem;line-height:1.5;color:var(--text);">
            This gig is currently in the process of being booked.
          </p>
        </div>`;
        actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
        return _commit(html, actionsHtml);
      }
      if (hi.my_state === 'declined') {
        html += `<div style="margin-bottom:14px;background:rgba(148,163,184,0.10);border:1px solid rgba(148,163,184,0.3);border-radius:10px;padding:14px 18px;">
          <p style="margin:0;font-size:0.86rem;color:var(--text);">You previously declined this offer. The venue has been notified.</p>
        </div>`;
        actionsHtml = `<div class="_gig-btn-row" style="justify-content:flex-end;">${_closeBtn(close)}</div>`;
        return _commit(html, actionsHtml);
      }
      // my_state === 'accepted' or null → fall through to normal flow
    }

    // Waitlist LOCKED banner — only show if there are still open bookable slots
    const _hasBookableSlots = (data.slots || []).some(s => s.relationship === 'open_bookable');
    // "all taken" = no open slot this artist could book — wrong-type
    // slots count as effectively taken from THIS artist's POV, same
    // as freq_blocked. Without this the "Waitlist? Not Available?"
    // CTA would show on a 3-slot mixed gig where the only open slot
    // left was a DJ slot the Live Band artist can't book.
    const _allSlotsTaken = (data.slots || []).every(s =>
      s.status !== 'open' || s.relationship === 'freq_blocked' || s.relationship === 'wrong_type'
    );
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
  // Suppressed when the artist ALREADY has a slot on this gig — the
  // limit is a pre-booking gate, not a post-booking restriction. If they
  // got in, they got in (via blast / open-window / frequency_exempt
  // flag at booking time). Showing "⚠️ Frequency Limitation" on a gig
  // they've already booked reads as if the venue is going to retract
  // the booking, which is wrong. Also kept the existing skip when the
  // gig is currently blast-open or marked frequency_exempt now.
  if (vType === 'artist' && data.freq_check && !data.is_blast_open && !data.frequency_exempt) {
    const _alreadyOnGig = (data.slots || []).some(s => s.is_my_slot);
    if (!_alreadyOnGig) {
      const fc = data.freq_check;
      const dir = fc.isBeforeBookedGig ? 'before' : 'after';
      html += _banner('red', '⚠️ Frequency Limitation',
        `This gig is ${fc.absDaysBetween} day${fc.absDaysBetween!==1?'s':''} ${dir} your booked gig on ${fc.lastGigDate}. This venue requires at least ${fc.daysRequired} days between bookings.`);
    } else {
      // Friendly confirmation that the booking is legit despite being
      // within the venue's normal frequency window. Reassures the
      // artist that the spot they grabbed under a blast / waiver is
      // theirs and won't be retracted.
      const fc = data.freq_check;
      html += _banner('green', 'Frequency Waived',
        `This gig is within the venue's usual ${fc.daysRequired}-day window between your bookings, but you booked it during an open-blast / waiver — your spot is locked in.`);
    }
  }
  // (Freq-waiver banner moved up — now rendered before the Hold-feature
  // panel so the Pending-Offer / queued / declined early-return paths
  // still surface it. Was unreachable here for those states.)

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
        ? `<button class="_gig-btn _gig-btn-cyan" onclick="window._gmCbs&&window._gmCbs.message&&window._gmCbs.message(${parseInt(data.id,10)||0},${(window.jsAttr||JSON.stringify)(data.venue_name)},${parseInt(data.viewer_id,10)||0})">Message Venue</button>`
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
      // Show the Cancel button in BOTH pending states (Jun 2026):
      //   - pending_contract            (artist hasn't signed yet)
      //   - awaiting_venue_contract     (artist signed, venue hasn't countersigned)
      // Previously the button was hidden once contract_status='artist_signed',
      // which left the artist with no way to back out before the venue
      // countersigns. cancel_gig handles both states server-side and clears
      // the slot + contract record so the gig becomes available again.
      if (onCancelGig) {
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

  // PROD BUG FIX (May 10 2026): the gig's chronological baseline for
  // overnight-slot detection must come from the slots themselves, not
  // gigs.start_time which can be unreliable (some saved gigs have
  // parent start_time set to a later slot's start, breaking the
  // "slot < gig => next day" heuristic). Use the slot with the lowest
  // slot_number as the baseline.
  const _orderedSlots = [...slots].sort((a, b) =>
    (a.slot_number || 0) - (b.slot_number || 0));
  const _gigBaseline = (_orderedSlots[0] && _orderedSlots[0].start_time)
    || data.start_time;

  let html = `<div style="border-top:1px solid rgba(255,255,255,0.1);padding-top:12px;margin-top:4px;">`;

  for (const slot of slots) {
    html += _slotRow(slot, data, vType, isPast, isInProgress, callbacks, _gigBaseline);
  }

  html += `</div>`;
  return html;
}

/* ── Single slot row ──────────────────────────────────────────────────────── */
function _slotRow(slot, data, vType, isPast, isInProgress, callbacks, gigBaseline) {
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
  // Use the gig baseline computed by _slotsSection (first slot's start)
  // — not data.start_time, which can be set to a later slot's value
  // on some saved gigs.
  const _offsets = _slotDateOffsets(gigBaseline || data.start_time, slot.start_time, slot.end_time);
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
  } else if (rel === 'freq_blocked' || rel === 'no_access' || rel === 'banned' || rel === 'wrong_type') {
    // wrong_type (Jun 2026): same blacked-out treatment as the other
    // hard-no relationships so a Live Band artist looking at a 3-slot
    // mixed gig sees the DJ slot greyed instead of styled like an
    // open bookable slot.
    opacity = '0.4';
    borderColor = 'rgba(148,163,184,0.18)';
    bgColor     = 'rgba(0,0,0,0.30)';
  }

  // Pay display — rendered as a green pill matching the venue-side modal so
  // multi-slot gigs read consistently across both views.
  //   - Artists: only on their own slot or open & bookable slots (other
  //     artists' negotiated pay isn't artist-facing).
  //   - Venues: on every slot (it's their gig — they always know the pay).
  let payHtml = '';
  if (slot.pay || slot.pay_summary || slot.deal_type === 'door') {
    const isVenue = vType === 'venue';
    const showPay = isVenue || isMySlot || (isOpen && rel === 'open_bookable');
    if (showPay) {
      // Door-deal aware. window.formatPaySummary returns "$60.00" for flat
      // or "$50.00 guarantee + 20% of door" for door split, using either
      // the backend-supplied pay_summary or the deal_type/door_pct/
      // guarantee_cents fields. Falls back to "$X.XX" gracefully.
      const payStr = (window.formatPaySummary ? window.formatPaySummary(slot) : `$${parseFloat(slot.pay || 0).toFixed(2)}`);
      payHtml = `<span style="color:#22c55e;font-weight:700;font-size:0.8rem;background:rgba(34,197,94,0.12);padding:1px 8px;border-radius:4px;border:1px solid rgba(34,197,94,0.25);white-space:nowrap;">${payStr}</span>`;
    }
  }

  // Add Door Receipts button — sits right of the pay pill on the slot
  // top line for venue users, on a door-deal slot that's booked and
  // whose start time has passed (gig in progress or done). Opens the
  // door settle modal for this specific slot via the existing
  // venue.create-gigs.js handler. Was previously in modal-actions which
  // the user found awkward — inline next to pay matches the slot row
  // it's actually settling.
  if (vType === 'venue' && slot.deal_type === 'door' && slot.status === 'booked') {
    const _slotStarted = _slotStartDt && _slotStartDt <= new Date();
    if (_slotStarted) {
      payHtml += ` <button type="button" onclick="event.stopPropagation(); if (window._openDealSettle) window._openDealSettle();"
        style="margin-left:6px;padding:2px 10px;background:rgba(245,158,11,0.10);border:1px solid rgba(245,158,11,0.35);border-radius:4px;color:#fbbf24;font-size:0.72rem;cursor:pointer;font-weight:600;white-space:nowrap;"
        title="Open the door settlement window for this slot. Enter the receipts collected at the door, we'll compute the artist's total (guarantee + door share) and queue the Stripe charge for tomorrow's 5 PM payout sweep.">
        Add Door Receipts
      </button>`;
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
          'The venue is preparing a contract. You\'ll be notified when it\'s ready. If you need to back out, hit Cancel below before the venue countersigns.');
        break;

      case 'mine_pending_approval':
        rightHtml = `<span style="color:#fbbf24;font-size:0.8rem;font-weight:600;">⏳ Awaiting Approval</span>`;
        extraHtml += _banner('yellow', '⏳ Awaiting Venue Approval',
          'Your same-day booking request has been sent. You\'ll be notified when approved.');
        break;

      case 'venue_booked':
        if (slot.artist_id) {
          // FIX (May 15 2026): match the _showBookedGigModal booked-slot
          // layout. Add the ✕ Cancel button and Rate Artist button so
          // every booked-slot view (whether the gig is in pending-contract
          // state with siblings or fully booked) looks identical.
          // Audit fix (May 2026 part 8): JSON-stringify-based JS-string literals
          // replace the broken `_esc()`-inside-onclick pattern. `_esc()` produced
          // `&#39;` for apostrophes, but the HTML parser decoded that back to
          // `'` before JS saw the string — letting malicious names break out.
          const _vbName = _esc(slot.artist_name || 'Booked');
          const _jsa = window.jsAttr || JSON.stringify;
          const _msgCb  = `typeof openMessageModal==='function'&&openMessageModal(${parseInt(data.id,10)||0},${_jsa(data.venue_name)},${parseInt(slot.artist_id,10)||0})`;
          const _rateCb = `typeof openReviewModal==='function'&&openReviewModal({artistId:${parseInt(slot.artist_id,10)||0},artistName:${_jsa(slot.artist_name||'Artist')},gigId:${parseInt(data.id,10)||0},gigDate:${_jsa(data.date||'')},gigTitle:${_jsa(data.title||'')}})`;
          const _cancelCb = `window.cancelSlotBooking&&cancelSlotBooking(${data.id}, ${slot.id}, ${slot.slot_number}, ${slot.artist_id || 'null'})`;
          const _cancelBtnHtml = (!isPast && !slotStarted)
            ? `<button onclick="${_cancelCb}"
                style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:2px 8px;font-size:0.72rem;cursor:pointer;white-space:nowrap;"
                title="Cancel this slot booking">✕</button>`
            : '';
          rightHtml = `<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
              style="color:#22c55e;font-size:0.8rem;text-decoration:none;font-weight:500;">${_vbName}</a>
            <button onclick="${_msgCb}"
              style="background:transparent;border:1px solid rgba(6,182,212,0.4);color:#06b6d4;border-radius:4px;padding:2px 8px;font-size:0.72rem;cursor:pointer;white-space:nowrap;">
              Message
            </button>
            <button onclick="${_rateCb}"
              style="background:transparent;border:1px solid rgba(245,158,11,0.4);color:#f59e0b;border-radius:4px;padding:2px 8px;font-size:0.72rem;cursor:pointer;white-space:nowrap;">
              Rate Artist
            </button>
            ${_cancelBtnHtml}
          </div>`;
        } else {
          rightHtml = `<span style="color:#22c55e;font-size:0.8rem;font-weight:500;">Booked</span>`;
        }
        break;

      case 'venue_pending_contract': {
        const cs2 = slot.contract_status;
        // FIX (May 15 2026): include a per-slot Cancel button so the venue
        // can drop the slot even while a contract is in flight — same
        // affordance the booked-slot view (_showBookedGigModal) gives.
        // Wires to the same window.cancelSlotBooking() the booked view uses.
        const _vpcAname = (slot.artist_name || 'Artist').replace(/['"]/g, '');
        const _vpcCancelBtn = (!isPast && !slotStarted && slot.artist_id)
          ? `<button onclick="window.cancelSlotBooking&&cancelSlotBooking(${data.id}, ${slot.id}, ${slot.slot_number}, ${slot.artist_id || 'null'})"
              style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:2px 8px;font-size:0.72rem;cursor:pointer;white-space:nowrap;"
              title="Cancel this slot booking">✕</button>`
          : '';
        rightHtml = slot.artist_id
          ? `<div style="display:flex;align-items:center;gap:8px;">
              <a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
                style="color:#a78bfa;font-size:0.8rem;text-decoration:none;font-weight:500;">
                ${_esc(slot.artist_name||'Artist')}</a>
              ${_vpcCancelBtn}
            </div>`
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

      case 'wrong_type': {
        // The artist viewing this gig has a different artist_type (or
        // missing lineup/styles overlap) than what THIS slot requires.
        // They can see the gig because another slot DOES match — but
        // this row is not bookable by them. Render a clear "<Type>
        // only" chip in place of the Book button.
        const _slotType = slot.artist_type || data.artist_type || 'Other';
        rightHtml = `<span title="This slot is for ${_esc(_slotType)}, so it isn't bookable by you."
          style="padding:4px 12px;font-size:0.72rem;color:#94a3b8;background:rgba(148,163,184,0.10);border:1px solid rgba(148,163,184,0.25);border-radius:4px;font-weight:600;white-space:nowrap;">
          ${_esc(_slotType)} only
        </span>`;
        break;
      }

      case 'open': {
        // Venue viewer on an unbooked slot — show Open pill + ✕ Cancel
        // (remove slot) so the venue can drop an empty slot from a
        // multi-slot gig. Same handler the booked-slot delete uses;
        // for null artist_id, cancelSlotBooking routes through the
        // open-slot delete flow.
        const _opCancelHtml = (!isPast && !slotStarted && vType === 'venue')
          ? `<button onclick="window.cancelSlotBooking&&cancelSlotBooking(${data.id}, ${slot.id}, ${slot.slot_number}, null)"
              style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:2px 8px;font-size:0.72rem;cursor:pointer;white-space:nowrap;"
              title="Remove this slot from the gig">✕</button>`
          : '';
        rightHtml = `<div style="display:flex;align-items:center;gap:8px;">
          <span style="color:#22c55e;font-size:0.78rem;font-weight:700;background:rgba(34,197,94,0.12);padding:2px 10px;border-radius:10px;border:1px solid rgba(34,197,94,0.3);">Open</span>
          ${_opCancelHtml}
        </div>`;
        break;
      }

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
  // Per-contract DOM ids so multiple pending countersign blocks in the same
  // modal don't collide. data-countersign-block lets _doCountersign find
  // and replace just THIS slot's block on success while leaving siblings
  // intact (multi-slot gigs where the venue signs slots one at a time).
  return `
    <div data-countersign-block="${slot.contract_id}" style="margin-top:10px;padding:10px 12px;background:rgba(139,92,246,0.07);border:1px solid rgba(139,92,246,0.2);border-radius:6px;">
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
            id="modalCountersignBtn_${slot.contract_id}"
            style="padding:6px 16px;font-size:0.85rem;background:#3b82f6;border:1px solid #3b82f6;color:#fff;border-radius:6px;cursor:pointer;font-weight:600;transition:background 0.2s;"
            onmouseover="this.style.background='#2563eb'" onmouseout="this.style.background='#3b82f6'">
            Countersign & Confirm Booking
          </button>
          ${slot.artist_id ? `<button onclick="typeof openMessageModal==='function'&&openMessageModal(${parseInt(gigId,10)||0},${(window.jsAttr||JSON.stringify)(slot.artist_name||'Artist')},${parseInt(slot.artist_id,10)||0})"
            style="padding:6px 14px;font-size:0.82rem;background:transparent;border:1px solid rgba(6,182,212,0.4);color:#06b6d4;border-radius:6px;cursor:pointer;">
            💬 Message Artist
          </button>` : ''}
        </div>
        <span id="modalCountersignStatus_${slot.contract_id}" style="font-size:0.82rem;"></span>
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
  // 2026-08-07: fill the (Venue Gig Details) link slot when the viewer
  // is an artist. venueGigDetails.mountLinks is idempotent + async;
  // safe to no-op when the module isn't loaded on this page.
  if (window.venueGigDetails && window.venueGigDetails.mountLinks) {
    window.venueGigDetails.mountLinks(bodyEl);
  }
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
