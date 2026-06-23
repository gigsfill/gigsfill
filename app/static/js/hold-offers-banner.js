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

  function _fmtHours(h) {
    if (h == null) return '';
    if (h < 1) return Math.round(h * 60) + 'm';
    if (h < 24) return h + 'h';
    return Math.round(h / 24) + 'd';
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
    const offers = (data && data.offers) || [];
    if (!offers.length) {
      banner.innerHTML = '';
      banner.style.marginBottom = '';
      return;
    }
    banner.style.marginBottom = '20px';
    banner.innerHTML = `
      <div style="background:rgba(245,158,11,0.08);border:1.5px solid rgba(245,158,11,0.5);border-radius:10px;padding:14px 18px;">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
          <span style="font-size:1.2rem;">🔒</span>
          <span style="font-size:0.92rem;font-weight:700;color:#fcd34d;text-transform:uppercase;letter-spacing:0.06em;">
            ${offers.length} pending offer${offers.length === 1 ? '' : 's'} from venue${offers.length === 1 ? '' : 's'}
          </span>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;">
          ${offers.map(_cardHtml).join('')}
        </div>
      </div>
    `;
    banner.querySelectorAll('.hob-decline').forEach(btn => {
      btn.addEventListener('click', () => _decline(btn.dataset.token, btn));
    });
    // Multi-slot: clicking a slot button opens a confirmation modal
    // [Book] [Cancel]. Cancel just closes — doesn't decline the
    // offer. The artist can pick a different slot or hit Decline all.
    banner.querySelectorAll('.hob-slot').forEach(btn => {
      btn.addEventListener('click', () => _confirmBook({
        token: btn.dataset.token, slotId: btn.dataset.slot,
        venue: btn.dataset.venue, date: btn.dataset.date,
        slotNum: btn.dataset.slotNum, time: btn.dataset.time, pay: btn.dataset.pay,
      }, btn));
    });
    // Single-slot: 'Book' button → same confirmation flow.
    banner.querySelectorAll('.hob-book-one').forEach(btn => {
      btn.addEventListener('click', () => _confirmBook({
        token: btn.dataset.token, slotId: null,
        venue: btn.dataset.venue, date: btn.dataset.date,
        time: btn.dataset.time, pay: btn.dataset.pay,
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

  function _cardHtml(o) {
    const title = o.gig_title ? ` · "${_esc(o.gig_title)}"` : '';
    const expires = o.hours_remaining != null
      ? `<span style="color:${o.hours_remaining < 6 ? '#ef4444' : '#fcd34d'};font-weight:600;">${_fmtHours(o.hours_remaining)} left</span>`
      : '';
    let slotsHtml = '';
    if (!o.slots || o.slots.length === 0) {
      // Shouldn't happen — the backend already auto-declines empty-match
      // offers — but render defensively just in case.
      slotsHtml = '<div style="font-size:0.78rem;color:var(--text-gray);font-style:italic;">No slots match your artist type.</div>';
    } else if (o.slots.length === 1) {
      const s = o.slots[0];
      const payStr = s.pay && s.pay === Math.round(s.pay) ? `$${Math.round(s.pay)}` : `$${Number(s.pay).toFixed(2)}`;
      // Stash venue/date on the button so the confirm modal can show
      // them in the prompt without re-fetching.
      slotsHtml = `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <span style="font-size:0.85rem;color:var(--text);">
          ${_typeIcon(s.artist_type)} ${_esc(s.time)} <span style="color:#22c55e;font-weight:600;">${payStr}</span>
        </span>
        <button type="button" class="hob-book-one" data-token="${_esc(o.offer_token)}"
          data-venue="${_esc(o.venue_name)}" data-date="${_esc(o.gig_date)}" data-time="${_esc(s.time)}" data-pay="${_esc(payStr)}"
          style="padding:6px 16px;background:#16a34a;border:none;border-radius:5px;color:#fff;cursor:pointer;font-size:0.8rem;font-weight:600;">Book</button>
        <button type="button" class="hob-decline" data-token="${_esc(o.offer_token)}"
          style="padding:6px 14px;background:transparent;border:1px solid #dc2626;border-radius:5px;color:#f87171;cursor:pointer;font-size:0.8rem;font-weight:600;">Decline</button>
      </div>`;
    } else {
      slotsHtml = `<div style="font-size:0.74rem;color:var(--text-gray);margin-bottom:5px;">Pick the slot you want:</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;">
          ${o.slots.map(s => {
            const payStr = s.pay && s.pay === Math.round(s.pay) ? `$${Math.round(s.pay)}` : `$${Number(s.pay).toFixed(2)}`;
            return `<button type="button" class="hob-slot" data-token="${_esc(o.offer_token)}" data-slot="${parseInt(s.id, 10)}"
              data-venue="${_esc(o.venue_name)}" data-date="${_esc(o.gig_date)}"
              data-slot-num="${s.slot_number}" data-time="${_esc(s.time)}" data-pay="${_esc(payStr)}"
              style="padding:6px 12px;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.45);border-radius:5px;color:#86efac;cursor:pointer;font-size:0.78rem;font-weight:600;">
              ${_typeIcon(s.artist_type)} Slot ${s.slot_number} · ${_esc(s.time)} · ${payStr}
            </button>`;
          }).join('')}
        </div>
        <button type="button" class="hob-decline" data-token="${_esc(o.offer_token)}"
          style="padding:5px 14px;background:transparent;border:1px solid #dc2626;border-radius:5px;color:#f87171;cursor:pointer;font-size:0.78rem;font-weight:600;">Decline all</button>`;
    }
    return `<div style="background:rgba(0,0,0,0.2);border-radius:8px;padding:12px 14px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:8px;">
        <div>
          <div style="font-size:0.95rem;color:var(--text);font-weight:600;">${_esc(o.venue_name)}${title}</div>
          <div style="font-size:0.78rem;color:var(--text-gray);margin-top:2px;">
            ${_fmtDate(o.gig_date)} · offered to <b>${_esc(o.artist_name)}</b>
          </div>
        </div>
        <div style="font-size:0.78rem;">${expires}</div>
      </div>
      ${slotsHtml}
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
    const ok = window.showConfirm
      ? await window.showConfirm('Decline this offer? The venue will be told and they\'ll move on to the next artist on their list.')
      : confirm('Decline this offer?');
    if (!ok) return;
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '…';
    try {
      const res = await fetch(`/hold/decline/${encodeURIComponent(token)}`, { credentials: 'include' });
      if (res.ok) {
        await load();
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
})();
