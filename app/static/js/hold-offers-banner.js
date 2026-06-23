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
        weekday: 'short', month: 'short', day: 'numeric'
      });
    } catch (_) { return d; }
  }

  function _typeIcon(t) {
    return { 'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤', 'Trivia Host': '❓' }[t] || '🎵';
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
    let data;
    try {
      const res = await fetch('/api/me/hold-offers', { credentials: 'include' });
      if (!res.ok) return;
      data = await res.json();
    } catch (_) {
      return;
    }
    const aid = _currentArtistId();
    let offers = (data && data.offers) || [];
    if (aid != null) {
      offers = offers.filter(o => parseInt(o.artist_id, 10) === aid);
    }
    if (!offers.length) {
      banner.innerHTML = '';
      banner.style.marginBottom = '';
      return;
    }
    banner.style.marginBottom = '20px';
    banner.innerHTML = `
      <div style="background:rgba(245,158,11,0.08);border:1.5px solid rgba(245,158,11,0.5);border-radius:10px;padding:10px 14px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
          <span style="font-size:1rem;">🔒</span>
          <span style="font-size:0.78rem;font-weight:700;color:#fcd34d;text-transform:uppercase;letter-spacing:0.06em;">
            ${offers.length} pending offer${offers.length === 1 ? '' : 's'}
          </span>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;">
          ${offers.map(_rowHtml).join('')}
        </div>
      </div>
    `;
    banner.querySelectorAll('.hob-decline').forEach(btn => {
      btn.addEventListener('click', () => _decline(btn.dataset.token, btn));
    });
    // Book button per slot. Confirmation modal stays as the safety net
    // (the click directly books — modal asks "are you sure?" with
    // [Book] / [Cancel]) since this IS a destructive action.
    banner.querySelectorAll('.hob-slot-book').forEach(btn => {
      btn.addEventListener('click', () => _confirmBook({
        token: btn.dataset.token, slotId: btn.dataset.slot,
        venue: btn.dataset.venue, date: btn.dataset.date,
        slotNum: btn.dataset.slotNum, time: btn.dataset.time, pay: btn.dataset.pay,
      }, btn));
    });
  }

  // Show [Book] [Cancel] confirmation before doing the actual booking.
  // Cancel just closes the dialog — doesn't decline the offer. Safety
  // net for misclicks per user request.
  function _confirmBook(args, btn) {
    const slotPart = args.slotNum ? `<b>Slot ${args.slotNum}</b> · ` : '';
    const msg = `${slotPart}${_esc(args.time)} at <b>${_esc(args.venue)}</b> on ${_esc(args.date)}<br><span style="color:#22c55e;font-weight:600;">${_esc(args.pay)}</span>`;
    if (typeof window.showConfirm === 'function') {
      window.showConfirm(
        'Book this slot?',
        // showConfirm's content is HTML-escaped via its _esc — pass
        // a plain string. The simple version below loses the bold
        // formatting but the modal still reads clearly.
        `${args.slotNum ? `Slot ${args.slotNum} · ` : ''}${args.time} at ${args.venue} on ${args.date} for ${args.pay}.`,
        () => _accept(args.token, args.slotId, btn),
        () => { /* Cancel = dismiss, no decline */ },
        { confirmLabel: 'Book', cancelLabel: 'Cancel', confirmStyle: 'success' }
      );
    } else {
      // Fallback if gf-modals.js failed to load
      if (confirm(`Book ${args.slotNum ? 'Slot ' + args.slotNum + ' ' : ''}at ${args.venue} on ${args.date} for ${args.pay}?`)) {
        _accept(args.token, args.slotId, btn);
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
      const payStr = s.pay && s.pay === Math.round(s.pay) ? `$${Math.round(s.pay)}` : `$${Number(s.pay).toFixed(2)}`;
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
      // Server returns HTML page; we just need 2xx + reload
      const res = await fetch(url, { method: slotId ? 'POST' : 'GET', credentials: 'include' });
      if (res.ok) {
        // Re-fetch + the offer should disappear from the list
        await load();
        // Also bump the gig calendar so the newly-booked gig appears
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
        // it (gmHoldDecline path). The page's calendar/banner refresh
        // below handles the rest.
        if (typeof window.closeAllModals === 'function') {
          try { window.closeAllModals(); } catch (_) {}
        }
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
      venue: btn.dataset.venue, date: btn.dataset.date,
      slotNum: btn.dataset.slotNum, time: btn.dataset.time, pay: btn.dataset.pay,
    }, btn);
  };
  window.gmHoldDecline = function (btn) {
    _decline(btn.dataset.token, btn);
  };
})();
