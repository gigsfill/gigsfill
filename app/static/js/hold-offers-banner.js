/* hold-offers-banner.js — Pending hold-offers banner on artist-book-gigs.
 *
 * Polls /api/me/hold-offers on page load + every 60s. Renders one card
 * per active offer at the top of the page (above the tabs). Each card:
 *   - 🔒 venue name + gig date + title
 *   - "Offered to <artist-name>" (in case the user owns multiple)
 *   - Hours remaining
 *   - Slot list (only slots this artist actually matches)
 *   - One-click Accept (single-slot) or Pick a Slot (multi)
 *   - Decline
 *
 * Accept/decline go through /hold/respond/<token> and /hold/decline/<token>
 * — same token-based endpoints the email uses. We surface them in-app
 * so the artist can act without grubbing through their inbox.
 *
 * The banner is impossible to miss — it sits above the tabs, has an
 * amber accent border, and stays until the artist responds. Per user
 * spec.
 */
(function () {
  const POLL_MS = 60_000;
  const BANNER_ID = 'holdOffersBanner';

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  // Human-friendly "time left to book" string. Rounded to nearest unit.
  // Per user spec: 'X hours left to book' / 'Y minutes left to book',
  // round up or down to nearest integer.
  function _fmtTimeLeft(h) {
    if (h == null) return '';
    const totalMins = Math.round(h * 60);
    if (totalMins <= 0) return 'expiring now';
    if (totalMins < 60) {
      return `${totalMins} ${totalMins === 1 ? 'minute' : 'minutes'} left to book`;
    }
    const hrs = Math.round(h);
    if (hrs < 24) {
      return `${hrs} ${hrs === 1 ? 'hour' : 'hours'} left to book`;
    }
    const days = Math.round(h / 24);
    return `${days} ${days === 1 ? 'day' : 'days'} left to book`;
  }

  function _fmtDate(d) {
    if (!d) return '';
    try {
      const dt = new Date(d + 'T00:00:00');
      if (isNaN(dt.getTime())) return d;
      return dt.toLocaleDateString(undefined, {
        weekday: 'long', month: 'long', day: 'numeric'
      });
    } catch (_) { return d; }
  }

  function _typeIcon(t) {
    return { 'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤', 'Trivia Host': '❓', 'Open Mic MC':'🎙️', 'Karaoke MC':'🎶' }[t] || '🎵';
  }

  // Read current artist_id from URL — banner only shows offers for the
  // artist whose page the user is viewing. Avoids the confusion of an
  // offer for Artist B appearing on Artist A's page (per user feedback).
  function _currentArtistId() {
    try {
      const params = new URLSearchParams(window.location.search);
      const v = parseInt(params.get('artist_id'), 10);
      return Number.isFinite(v) && v > 0 ? v : null;
    } catch (_) { return null; }
  }

  async function load() {
    const banner = document.getElementById(BANNER_ID);
    if (!banner) return;
    let data, seriesData;
    try {
      // Fetch both per-gig and series-hold offers in parallel.
      const [r1, r2] = await Promise.all([
        fetch('/api/me/hold-offers', { credentials: 'include' }),
        fetch('/api/me/series-hold-offers', { credentials: 'include' }),
      ]);
      if (!r1.ok) return;
      data = await r1.json();
      seriesData = r2.ok ? await r2.json() : { offers: [] };
    } catch (_) {
      return;
    }
    const aid = _currentArtistId();
    let offers = (data && data.offers) || [];
    let seriesOffers = (seriesData && seriesData.offers) || [];
    if (aid != null) {
      offers = offers.filter(o => parseInt(o.artist_id, 10) === aid);
      seriesOffers = seriesOffers.filter(o => parseInt(o.artist_id, 10) === aid);
    }
    if (!offers.length && !seriesOffers.length) {
      banner.innerHTML = '';
      banner.style.marginBottom = '';
      return;
    }
    banner.style.marginBottom = '20px';
    // Series cards render ABOVE the per-gig list. Each series offer is
    // ONE card with a count + open-modal button (no per-date cards —
    // the modal handles the dates).
    const seriesCardsHtml = seriesOffers.map(s => {
      const hrs = s.hours_remaining;
      let timeLeft = '';
      if (hrs != null) {
        const totalMins = Math.round(hrs * 60);
        if (totalMins <= 0) timeLeft = 'expiring now';
        else if (totalMins < 60) timeLeft = `${totalMins}m left`;
        else if (hrs < 24) { const h = Math.round(hrs); timeLeft = `${h}h left`; }
        else { const d = Math.round(hrs / 24); timeLeft = `${d}d left`; }
      }
      const venueName = (s.venue_name || 'Venue').replace(/</g, '&lt;');
      return `<div style="display:flex;align-items:center;gap:10px;background:rgba(124,107,255,0.10);border:1px solid rgba(124,107,255,0.40);border-radius:8px;padding:8px 12px;flex-wrap:wrap;">
        <span style="font-weight:700;color:#c4b5fd;font-size:0.84rem;">📅 Series offer</span>
        <span style="font-size:0.84rem;color:var(--text);">${venueName} · <b>${s.gig_count} dates</b> available</span>
        <span style="color:#fcd34d;font-weight:600;font-size:0.78rem;margin-left:auto;">${timeLeft}</span>
        <button type="button" class="hob-series-open" data-token="${s.offer_token}"
          style="padding:5px 14px;background:#7c6bff;border:none;border-radius:5px;color:#fff;cursor:pointer;font-size:0.76rem;font-weight:600;">
          Pick Your Dates →
        </button>
      </div>`;
    }).join('');
    const perGigSectionHtml = offers.length
      ? `<div style="display:flex;flex-direction:column;gap:4px;${seriesCardsHtml ? 'margin-top:8px;' : ''}">${offers.map(_rowHtml).join('')}</div>`
      : '';
    banner.innerHTML = `
      <div style="background:rgba(245,158,11,0.08);border:1.5px solid rgba(245,158,11,0.5);border-radius:10px;padding:10px 14px;">
        <div style="margin-bottom:8px;">
          <span style="font-size:0.88rem;font-weight:600;color:#fcd34d;">
            Venue(s) have specifically requested you for the following gigs:
          </span>
        </div>
        ${seriesCardsHtml ? `<div style="display:flex;flex-direction:column;gap:6px;">${seriesCardsHtml}</div>` : ''}
        ${perGigSectionHtml}
      </div>
    `;
    // Wire series-card buttons → open the series modal (defined in
    // series-hold-modal.js, included on artist-book-gigs.html).
    banner.querySelectorAll('.hob-series-open').forEach(btn => {
      btn.addEventListener('click', () => {
        const tok = btn.dataset.token;
        if (window.openSeriesHoldModal) window.openSeriesHoldModal(tok);
      });
    });
    banner.querySelectorAll('.hob-decline').forEach(btn => {
      btn.addEventListener('click', () => _decline(btn.dataset.token, btn));
    });
    // Book button per slot. Confirmation modal stays as the safety net
    // (the click directly books — modal asks "are you sure?" with
    // [Book] / [Cancel]) since this IS a destructive action.
    banner.querySelectorAll('.hob-slot-book').forEach(btn => {
      btn.addEventListener('click', () => _confirmBook({
        token: btn.dataset.token, slotId: btn.dataset.slot,
        gigId: parseInt(btn.dataset.gig, 10) || null,
        venueId: parseInt(btn.dataset.vid, 10) || null,
        artistId: parseInt(btn.dataset.aid, 10) || null,
        venue: btn.dataset.venue, date: btn.dataset.date,
        slotNum: btn.dataset.slotNum, time: btn.dataset.time, pay: btn.dataset.pay,
      }, btn));
    });
    // Deep-link auto-trigger (Jun 2026): when the artist clicks an
    // email Accept link AND the venue requires a contract, the backend
    // redirects them here with `#hold_book=<token>[:<slot_id>]`. We
    // simulate a Book click on the matching button so the in-app
    // contract signing flow opens automatically — no need for them to
    // hunt for the right bubble.
    _autoTriggerHoldBookFromHash();
  }

  function _autoTriggerHoldBookFromHash() {
    try {
      const hash = (window.location.hash || '').replace(/^#/, '');
      if (!hash.startsWith('hold_book=')) return;
      const raw = hash.slice('hold_book='.length);
      const [token, slotIdStr] = raw.split(':');
      if (!token) return;
      // Clear the hash so a page refresh doesn't re-fire the click.
      try { history.replaceState(null, '', window.location.pathname + window.location.search); } catch (_) {}
      const banner = document.getElementById(BANNER_ID);
      if (!banner) return;
      // Find the right Book button. Prefer exact slot match; fall
      // back to the first matching token (multi-slot path where the
      // backend didn't pin a slot — picker is in-app).
      let btn = null;
      if (slotIdStr) {
        btn = banner.querySelector(
          `.hob-slot-book[data-token="${token}"][data-slot="${parseInt(slotIdStr, 10)}"]`
        );
      }
      if (!btn) {
        btn = banner.querySelector(`.hob-slot-book[data-token="${token}"]`);
      }
      if (btn) {
        // Defer a tick so the listener attached above has bound first.
        setTimeout(() => btn.click(), 50);
      }
    } catch (_) {}
  }

  // Show [Book] [Cancel] confirmation before doing the actual booking.
  // Cancel just closes the dialog — doesn't decline the offer. Safety
  // net for misclicks per user request.
  function _confirmBook(args, btn) {
    if (typeof window.showConfirm === 'function') {
      window.showConfirm(
        'Book this slot?',
        // showConfirm's content is HTML-escaped via its _esc — pass
        // a plain string. The simple version below loses the bold
        // formatting but the modal still reads clearly.
        `${args.slotNum ? `Slot ${args.slotNum} · ` : ''}${args.time} at ${args.venue} on ${args.date} for ${args.pay}.`,
        // The bare arrow returns undefined (NOT the Promise from
        // _accept), so the gf-modals stayOpen detection sees a
        // sync handler and closes the modal immediately on click.
        // _acceptWithContractCheck branches: if the venue requires a
        // contract, open the existing contract-signing modal (which
        // POSTs to /book-with-contract with hold_token); otherwise
        // fall through to the direct /hold/accept path.
        () => { _acceptWithContractCheck(args, btn); },
        () => { /* Cancel = dismiss, no decline */ },
        { confirmLabel: 'Book', cancelLabel: 'Cancel', confirmStyle: 'success' }
      );
    } else {
      // Fallback if gf-modals.js failed to load
      if (confirm(`Book ${args.slotNum ? 'Slot ' + args.slotNum + ' ' : ''}at ${args.venue} on ${args.date} for ${args.pay}?`)) {
        _acceptWithContractCheck(args, btn);
      }
    }
  }

  // Contract-aware accept. If the venue requires a contract on
  // booking AND any of the three contract-signing modals is exposed
  // (artist.book-gigs.js sets these globals), fetch the contract
  // preview and open the right signing modal. The modal POSTs to
  // /book-with-contract with our hold_token so book-with-contract
  // can clean up the hold waitlist + advance rotation.
  //
  // When no contract is required, fall back to the direct /hold/accept
  // path (faster, no signing step).
  async function _acceptWithContractCheck(args, btn) {
    if (!args || !args.token) return;
    // No venue id (legacy data attribute missing) → skip contract check
    if (!args.venueId) return _accept(args.token, args.slotId, btn);
    let contractInfo = null;
    try {
      const cRes = await fetch(`/api/venues/${args.venueId}/contracts/active`, { credentials: 'include' });
      if (cRes.ok) {
        const cd = await cRes.json();
        if (cd.has_contract && cd.require_for_booking) {
          contractInfo = { contractType: cd.contract_type };
        }
      }
    } catch (_) {}
    if (!contractInfo) {
      // No contract requirement → fast path.
      return _accept(args.token, args.slotId, btn);
    }
    // Contract required. Need the contract-signing helpers — those
    // are exposed by artist.book-gigs.js. If they aren't loaded
    // (shouldn't happen since this banner only runs on the artist
    // calendar page), surface a clear message and fall back.
    const hasSign = typeof window.showContractSigningModal === 'function';
    const hasPdf  = typeof window.showPdfContractModal === 'function';
    const hasPerGig = typeof window.showPerGigPdfModal === 'function';
    if (!hasSign && !hasPdf && !hasPerGig) {
      if (typeof window.showErrorModal === 'function') {
        window.showErrorModal(
          'Contract signing unavailable',
          'This venue requires a contract on booking, but the signing modal couldn\'t load. Open this gig from your calendar and try Book again.'
        );
      }
      return;
    }
    // Stash booking context in the global the contract modals read.
    // hold_token is the new piece — book-with-contract picks it up
    // and runs the hold cleanup after the slot is booked.
    window._pendingSlotBooking = {
      gigId: args.gigId, slotId: args.slotId,
      slotNum: args.slotNum, artistId: args.artistId,
      holdToken: args.token,
    };
    try {
      // Fetch the preview for the signing modal.
      const prevRes = await fetch(
        `/api/gigs/${args.gigId}/contract-preview?artist_id=${args.artistId}${args.slotId ? '&slot_id=' + args.slotId : ''}`,
        { credentials: 'include' }
      );
      if (!prevRes.ok) throw new Error('Failed to load contract');
      const preview = await prevRes.json();
      const gigStub = { id: args.gigId, venue_id: args.venueId, date: args.date };
      if ((preview.contract_type === 'custom_builder' || preview.contract_type === 'auto_generated') && hasSign) {
        window.showContractSigningModal(gigStub, preview, args.artistId);
      } else if (preview.contract_type === 'pdf_upload' && preview.per_gig_pdf && hasPerGig) {
        window.showPerGigPdfModal(gigStub, args.artistId);
      } else if (preview.contract_type === 'pdf_upload' && hasPdf) {
        window.showPdfContractModal(gigStub, preview, args.artistId);
      } else if (hasSign) {
        // Unknown type but signing helper exists — show it (best effort)
        window.showContractSigningModal(gigStub, preview, args.artistId);
      }
    } catch (e) {
      window._pendingSlotBooking = null;
      if (typeof window.showErrorModal === 'function') {
        window.showErrorModal('Could not load contract', e.message || 'Please try again or open the gig from your calendar.');
      }
    }
  }

  // Bubble-style row (Jun 2026): each slot is its own pill-card with
  // inline Book + Decline buttons. Bubbles flow left-to-right and wrap
  // to a second row when too many to fit. Within each bubble, the time
  // and pay use fixed-width columns so they align across bubbles.
  //
  // Decline on any slot declines the whole offer (only one response is
  // supported per offer); the dual buttons in each bubble are a UX
  // convenience.
  function _rowHtml(o) {
    const title = o.gig_title ? ` <span style="color:var(--text-gray);font-weight:400;">"${_esc(o.gig_title)}"</span>` : '';
    const expires = o.hours_remaining != null
      ? `<span style="color:${o.hours_remaining < 6 ? '#ef4444' : '#fcd34d'};font-weight:600;font-size:0.78rem;white-space:nowrap;">${_fmtTimeLeft(o.hours_remaining)}</span>`
      : '';
    // Venue name → purple/blue, hyperlinked to public profile page.
    // Matches the site's existing purple accent vocabulary
    // (rgba(124,107,255,...) / #a78bfa) used on My Artists / venue
    // chips elsewhere.
    const venueLink = o.venue_id
      ? `<a href="/app/venue-profile.html?venue_id=${parseInt(o.venue_id, 10)}" target="_blank" rel="noopener"
            style="color:#a78bfa;text-decoration:none;font-weight:700;border-bottom:1px dashed rgba(167,139,250,0.45);">${_esc(o.venue_name)}</a>`
      : `<span style="color:#a78bfa;font-weight:700;">${_esc(o.venue_name)}</span>`;

    const header = `<div style="display:flex;align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap;margin-bottom:10px;">
      <span style="font-size:0.88rem;color:var(--text);">
        ${venueLink}${title} · ${_fmtDate(o.gig_date)}
      </span>
      ${expires}
    </div>`;

    if (!o.slots || o.slots.length === 0) {
      return `<div style="background:rgba(0,0,0,0.2);border-radius:6px;padding:10px 14px;">
        ${header}
        <div style="font-size:0.78rem;color:var(--text-gray);font-style:italic;display:flex;align-items:center;justify-content:space-between;gap:10px;">
          <span>No slots match your artist type</span>
          <button type="button" class="hob-decline" data-token="${_esc(o.offer_token)}"
            title="Decline if you are unable to perform on this day."
            style="padding:5px 12px;background:transparent;border:1px solid #dc2626;border-radius:4px;color:#f87171;cursor:pointer;font-size:0.74rem;font-weight:600;">Decline</button>
        </div>
      </div>`;
    }

    // Pick the widest time string so each bubble's time column matches.
    // Lets the pay column line up vertically across bubbles in the row.
    const widestTime = Math.max(...o.slots.map(s => (s.time || '').length));

    const slotBubbles = o.slots.map(s => {
      // Door deal slots render as '$X + Y% door'. The backend's
      // /api/me/hold-offers endpoint already applies the per-artist
      // pay override to BOTH flat and door slots (s.pay is the
      // effective dollar amount — guarantee for door, flat for flat).
      // So we use s.pay as the displayed amount in both branches.
      let payStr;
      if (s.deal_type === 'door') {
        const gua = Number(s.pay) || 0;
        const guaFmt = gua % 1 === 0 ? `$${Math.round(gua)}` : `$${gua.toFixed(2)}`;
        payStr = `${guaFmt} + ${parseInt(s.door_pct, 10) || 0}% door`;
      } else {
        payStr = s.pay && s.pay === Math.round(s.pay) ? `$${Math.round(s.pay)}` : `$${Number(s.pay).toFixed(2)}`;
      }
      const slotLabel = o.slots.length > 1 ? `Slot ${s.slot_number}` : '';
      return `<div class="hob-slot-bubble"
        style="display:inline-flex;align-items:center;gap:10px;background:rgba(124,107,255,0.10);border:1px solid rgba(124,107,255,0.35);border-radius:8px;padding:7px 12px;flex-wrap:nowrap;">
        <span style="display:inline-flex;align-items:center;gap:6px;font-size:0.82rem;color:var(--text);white-space:nowrap;">
          <span style="font-size:0.95em;">${_typeIcon(s.artist_type)}</span>
          ${slotLabel ? `<span style="color:var(--text);font-weight:600;">${slotLabel}</span><span style="color:var(--text-muted);">·</span>` : ''}
          <span style="font-variant-numeric:tabular-nums;display:inline-block;min-width:${widestTime}ch;">${_esc(s.time)}</span>
          <span style="color:var(--text-muted);">·</span>
          <span style="color:#22c55e;font-weight:600;font-variant-numeric:tabular-nums;">${payStr}</span>
        </span>
        <span style="display:inline-flex;gap:5px;flex-shrink:0;">
          <button type="button" class="hob-slot-book" data-token="${_esc(o.offer_token)}" data-slot="${parseInt(s.id, 10)}"
            data-gig="${parseInt(o.gig_id, 10)}" data-vid="${parseInt(o.venue_id, 10)}" data-aid="${parseInt(o.artist_id, 10)}"
            data-venue="${_esc(o.venue_name)}" data-date="${_esc(o.gig_date)}"
            data-slot-num="${s.slot_number}" data-time="${_esc(s.time)}" data-pay="${_esc(payStr)}"
            title="Book this slot now."
            style="padding:5px 14px;background:#7c6bff;border:none;border-radius:4px;color:#fff;cursor:pointer;font-size:0.74rem;font-weight:600;">Book</button>
          <button type="button" class="hob-decline" data-token="${_esc(o.offer_token)}"
            title="Decline if you are unable to perform on this day."
            style="padding:5px 12px;background:transparent;border:1px solid #dc2626;border-radius:4px;color:#f87171;cursor:pointer;font-size:0.74rem;font-weight:600;">Decline</button>
        </span>
      </div>`;
    }).join('');

    return `<div style="background:rgba(0,0,0,0.2);border-radius:6px;padding:10px 14px;">
      ${header}
      <div style="display:flex;flex-wrap:wrap;gap:8px;">${slotBubbles}</div>
    </div>`;
  }

  async function _accept(token, slotId, btn) {
    if (!token) return;
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '…';
    try {
      const url = slotId
        ? `/hold/accept/${encodeURIComponent(token)}?slot_id=${parseInt(slotId, 10)}`
        : `/hold/respond/${encodeURIComponent(token)}`;
      // Server returns HTML for the GET-landing flow and a body that
      // may include `ok:false, message:...` JSON for failures. We
      // peek at content-type to decide whether to parse JSON or treat
      // any 2xx as success (HTML page = success landing).
      const res = await fetch(url, {
        method: slotId ? 'POST' : 'GET',
        credentials: 'include',
        // Ask for JSON so the backend's content-negotiated endpoint
        // returns {ok, message} we can show in the modal instead of an
        // HTML page we'd have to parse.
        headers: { 'Accept': 'application/json' },
      });
      // Surface backend-supplied messages (e.g. "Stripe not set up",
      // "Frequency conflict") instead of the generic "✗ Failed" toast.
      // The accept-landing endpoint returns HTML on success; only
      // parse JSON when the response declares it.
      let payload = null;
      const ct = (res.headers.get('content-type') || '').toLowerCase();
      if (ct.includes('application/json')) {
        try { payload = await res.json(); } catch (_) {}
      }
      const explicitFail = payload && payload.ok === false;
      if (res.ok && !explicitFail) {
        // Close any open Gig Details modal that's still showing the
        // pre-book state (with the now-stale Pending Offer panel,
        // disabled '…' Book button, lingering Decline). Without this
        // the artist sees a stuck modal after the success toast.
        // `_gmClose` is exposed by gig-modal.js as the universal
        // closer; the modalOverlay fallback covers the case where
        // the close override hasn't been wired up yet.
        try {
          if (typeof window._gmClose === 'function') window._gmClose();
          else document.getElementById('modalOverlay')?.classList.add('hidden');
        } catch (_) {}
        // Re-fetch + the offer should disappear from the list
        await load();
        // Also bump the gig calendar so the newly-booked gig appears
        if (typeof window.refreshArtistGigs === 'function') {
          try { window.refreshArtistGigs(); } catch (_) {}
        }
        // Visible success feedback so the artist knows it worked.
        if (typeof window.showSuccessModal === 'function') {
          try { window.showSuccessModal('Booked!', 'You\'re confirmed for this gig. The venue has been notified.'); } catch (_) {}
        }
      } else {
        btn.textContent = '✗ Failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
        // Prefer the backend's specific message (e.g. "Your payout
        // account isn't set up yet…") over a generic catch-all. Falls
        // back when no JSON body was returned.
        const specificMsg = (payload && payload.message) ||
          (payload && payload.detail) ||
          'Something went wrong — please refresh and try again.';
        if (typeof window.showErrorModal === 'function') {
          try { window.showErrorModal('Could not book', specificMsg); } catch (_) {}
        }
      }
    } catch (e) {
      btn.textContent = '✗ Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
    }
  }

  async function _decline(token, btn) {
    if (!token) return;
    // Promise-wrap showConfirm so we actually wait for the user's
    // click. tone:'warning' paints the modal red-toned;
    // confirmStyle:'danger' makes the Confirm button red.
    // Bug before: showConfirm was awaited as if it returned a Promise,
    // but it uses callbacks — the await resolved to the modal element
    // (truthy) and the decline fired regardless of what the user
    // clicked in the dialog.
    const ok = window.showConfirm
      ? await new Promise(res => window.showConfirm(
          'Decline this offer?',
          'The venue will be told and they\'ll move on to the next artist on their list.',
          () => res(true),
          () => res(false),
          { tone: 'warning', confirmStyle: 'danger',
            confirmLabel: 'Decline Offer', cancelLabel: 'Keep Offer' }
        ))
      : confirm('Decline this offer?');
    if (!ok) return;
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '…';
    try {
      const res = await fetch(`/hold/decline/${encodeURIComponent(token)}`, { credentials: 'include' });
      if (res.ok) {
        // Close the parent gig modal if this decline came from inside
        // it (gmHoldDecline path). closeAllModals only tracks the
        // gfm-modal stack — the legacy #modalOverlay where the
        // Pending Offer panel lives needs an explicit .hidden flip,
        // otherwise the Gig Details modal stays open after the
        // confirmation toast. (Jun 2026 user report.)
        if (typeof window.closeAllModals === 'function') {
          try { window.closeAllModals(); } catch (_) {}
        }
        try { document.getElementById('modalOverlay')?.classList.add('hidden'); } catch (_) {}
        await load();
        // Refresh the artist calendar so the gig bubble flips from
        // blinking blue → black (it no longer has an active offer
        // for this artist). Drives shouldBlinkForArtist via the
        // refreshed holdOfferGigIds set.
        if (typeof window.refreshArtistGigs === 'function') {
          try { window.refreshArtistGigs(); } catch (_) {}
        }
      } else {
        btn.textContent = '✗ Failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
      }
    } catch (e) {
      btn.textContent = '✗ Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
    }
  }

  function start() {
    load();
    setInterval(load, POLL_MS);
    // Refresh on tab focus / visibility change so artists see updated
    // slot info immediately after the venue edits the gig (e.g. loads
    // a template that swaps a slot to a door deal). Without this, the
    // banner stays stale until the next 60s tick.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') load();
    });
    window.addEventListener('focus', () => load());
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  window.loadHoldOffersBanner = load;

  // Exposed for the gig-modal Pending-Offer rendering (Jun 2026).
  // Same Book/Decline plumbing as the banner; lets the inline modal
  // act on offers without duplicating the fetch + confirmation logic.
  window.gmHoldBook = function (btn) {
    _confirmBook({
      token: btn.dataset.token, slotId: btn.dataset.slot,
      // Same contract context the banner passes — gig-modal's
      // Pending Offer panel sets data-gig/vid/aid attributes too.
      gigId: parseInt(btn.dataset.gig, 10) || null,
      venueId: parseInt(btn.dataset.vid, 10) || null,
      artistId: parseInt(btn.dataset.aid, 10) || null,
      venue: btn.dataset.venue, date: btn.dataset.date,
      slotNum: btn.dataset.slotNum, time: btn.dataset.time, pay: btn.dataset.pay,
    }, btn);
  };
  window.gmHoldDecline = function (btn) {
    _decline(btn.dataset.token, btn);
  };
})();
