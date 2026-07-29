/* series-hold-modal.js — Artist bulk-book / decline-all modal for a
 * series hold offer (Jul 2026 — Phase 5).
 *
 * Trigger paths:
 *   1. Hash deep-link from email: /app/artist-book-gigs.html#series-hold=TOKEN
 *      → window.openSeriesHoldModal(token) on page load.
 *   2. Future: a button on the Pending Offers banner (currently the
 *      banner renders per-gig offers; the series offer carries the
 *      same token across multiple gigs — banner skip handled by the
 *      "Pick all dates" CTA we'll add separately).
 *
 * Modal renders:
 *   - Header: "Pick your dates at <venue>"
 *   - Frequency reminder line
 *   - List of eligible gigs (date · time · pay) each with a checkbox
 *   - Each pick greys out other dates within the venue's freq window
 *     (clear tooltip explaining WHY: "Within 28 days of Aug 1")
 *   - Footer: live total, [Decline All] [Book Selected & Sign]
 *
 * If venue requires a contract, the Sign step opens a sub-modal with
 * a single signature input that covers all selected gigs in one
 * bundled POST to /api/series-hold/respond/{token}.
 */
(function () {
  'use strict';

  const _esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  function _fmtTime(t) {
    if (!t) return '';
    try {
      const [h, m] = String(t).split(':').map(Number);
      const ampm = h >= 12 ? 'PM' : 'AM';
      const h12 = (h % 12) || 12;
      return `${h12}:${String(m).padStart(2, '0')} ${ampm}`;
    } catch (_) { return String(t); }
  }
  function _fmtDate(d) {
    if (!d) return '';
    try {
      const [y, m, day] = String(d).split('-').map(Number);
      const dt = new Date(y, m - 1, day);
      return dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    } catch (_) { return String(d); }
  }
  function _fmtPay(s) {
    const v = Number(s.pay) || 0;
    const base = v === Math.round(v) ? `$${Math.round(v)}` : `$${v.toFixed(2)}`;
    if ((s.deal_type || 'flat').toLowerCase() === 'door') {
      return `${base} + ${parseInt(s.door_pct, 10) || 0}% door`;
    }
    return base;
  }
  function _daysBetween(d1, d2) {
    try {
      const a = new Date(d1 + 'T00:00:00').getTime();
      const b = new Date(d2 + 'T00:00:00').getTime();
      return Math.abs(Math.round((a - b) / 86400000));
    } catch (_) { return 999; }
  }

  // Compute which eligible rows are greyed-out given the current
  // selection. Keyed by composite `${gig_id}_${slot_id}` so two slots
  // on the same gig (a Live Band + a DJ slot on a multi-type gig)
  // grey/select independently. Three rules:
  //   (a) Same-gig: picking ANY slot on a gig greys other slots on
  //       that same gig — one slot per artist per gig (backend rule).
  //   (b) Frequency vs existing OUT-OF-series bookings at this venue
  //       (#11 audit fix). If the artist has a one-off booking at
  //       this venue, any series date within freq_days of it is
  //       greyed BEFORE the user picks anything — they can't book it
  //       at all without breaking the rule.
  //   (c) Frequency vs OTHER selected picks (the original rule).
  function _computeGreyed(eligible, selectedKeys, freqDays, existingBookings) {
    const greyed = new Map(); // key -> reason
    const selectedRows = eligible.filter(g => selectedKeys.has(`${g.gig_id}_${g.slot_id}`));
    const selectedGigIds = new Set(selectedRows.map(g => g.gig_id));
    const existing = Array.isArray(existingBookings) ? existingBookings : [];
    eligible.forEach(g => {
      const key = `${g.gig_id}_${g.slot_id}`;
      if (selectedKeys.has(key)) return;
      // (a) Same-gig dedupe
      if (selectedGigIds.has(g.gig_id)) {
        greyed.set(key, `You've already picked another slot on ${_fmtDate(g.date)} — only one slot per gig.`);
        return;
      }
      // (b) Existing OUT-OF-series bookings at this venue.
      // Use `gap <= freqDays` to match the backend `_run_prebooking_checks`
      // gate (gigs.py:2482) which BLOCKS when days_apart <= freq_days.
      // Strict-less-than here would let the user pick a date the backend
      // then rejects — visible mismatch.
      if (freqDays && freqDays > 0) {
        for (const eb of existing) {
          const gap = _daysBetween(g.date, eb.date);
          if (gap <= freqDays) {
            greyed.set(key, `You already have a gig at this venue on ${_fmtDate(eb.date)} — ${gap} day${gap === 1 ? '' : 's'} apart (venue needs more than ${freqDays} between bookings).`);
            return;
          }
        }
      }
      // (c) Frequency vs other picks in this modal — same `<=` rule.
      if (freqDays && freqDays > 0) {
        for (const sd of selectedRows) {
          if (sd.gig_id === g.gig_id) continue;
          const gap = _daysBetween(g.date, sd.date);
          if (gap <= freqDays) {
            greyed.set(key, `Frequency conflict — ${gap} day${gap === 1 ? '' : 's'} from your selected ${_fmtDate(sd.date)} pick (venue needs more than ${freqDays} between bookings).`);
            break;
          }
        }
      }
    });
    return greyed;
  }

  async function openSeriesHoldModal(token) {
    if (!token) return;
    let data;
    try {
      const res = await fetch(`/api/series-hold/offer/${encodeURIComponent(token)}`, { credentials: 'include' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (window.showErrorModal) {
          window.showErrorModal('Offer unavailable', err.detail || 'This series-hold offer has expired or already been responded to.');
        }
        return;
      }
      data = await res.json();
    } catch (e) {
      if (window.showErrorModal) window.showErrorModal('Could not load offer', e.message || 'Try refreshing.');
      return;
    }
    if (!data || !data.ok) return;
    _renderModal(token, data);
  }

  function _renderModal(token, data) {
    // Sort eligible by date
    const eligible = (data.eligible || []).slice().sort((a, b) => (a.date || '').localeCompare(b.date || ''));
    if (eligible.length === 0) {
      if (window.showErrorModal) window.showErrorModal('No matching dates', 'None of the gigs in this series match your artist type, or they\'ve all been booked. The offer will move on automatically.');
      return;
    }
    const freqDays = data.frequency_days || 0;
    const existingBookings = data.existing_bookings || [];
    // Composite-key selection (Jul 2026 audit RED #7 fix). Same gig
    // can expose multiple slot rows (Live Band + DJ on a multi-type
    // gig). We track each row independently and let the same-gig
    // dedupe rule grey out siblings once the first is picked.
    const selected = new Set();  // Set<string "${gig_id}_${slot_id}">
    function _refresh() {
      const greyed = _computeGreyed(eligible, selected, freqDays, existingBookings);
      const listEl = document.getElementById('shmList');
      const summaryEl = document.getElementById('shmSummary');
      const bookBtn = document.getElementById('shmBookBtn');
      if (!listEl) return;
      let totalPay = 0;
      let html = '';
      eligible.forEach(g => {
        const key = `${g.gig_id}_${g.slot_id}`;
        const isSelected = selected.has(key);
        const greyReason = greyed.get(key);
        const disabled = !!greyReason && !isSelected;
        const rowBg = isSelected
          ? 'background:rgba(34,197,94,0.10);border:1px solid rgba(34,197,94,0.45);'
          : disabled
            ? 'background:rgba(0,0,0,0.20);border:1px solid rgba(255,255,255,0.04);'
            : 'background:rgba(124,107,255,0.06);border:1px solid rgba(124,107,255,0.20);';
        const opacity = disabled ? '0.45' : '1';
        const tooltip = disabled ? ` title="${_esc(greyReason)}"` : '';
        if (isSelected) totalPay += Number(g.pay) || 0;
        // 4-column layout with fixed widths so Pay always aligns
        // vertically (Jul 2026 — user request). Reason column is
        // always present; empty for non-greyed rows.
        const reasonHtml = disabled
          ? `<span style="flex:0 0 220px;width:220px;font-size:0.7rem;color:#fbbf24;font-style:italic;text-align:right;">${_esc(greyReason)}</span>`
          : `<span style="flex:0 0 220px;width:220px;"></span>`;
        html += `<label class="shm-row" data-key="${_esc(key)}"${tooltip}
          style="display:flex;align-items:center;gap:14px;${rowBg}border-radius:8px;padding:10px 14px;cursor:${disabled?'not-allowed':'pointer'};opacity:${opacity};">
          <input type="checkbox" class="shm-check" data-key="${_esc(key)}"
            ${isSelected ? 'checked' : ''} ${disabled ? 'disabled' : ''}
            style="margin:0;width:18px;height:18px;cursor:${disabled?'not-allowed':'pointer'};accent-color:#22c55e;">
          <span style="flex:0 0 auto;font-weight:600;font-size:0.9rem;color:var(--text);min-width:120px;">${_fmtDate(g.date)}</span>
          <span style="flex:1;font-size:0.84rem;color:var(--text-gray);">${_fmtTime(g.start_time)} – ${_fmtTime(g.end_time)}</span>
          <span style="flex:0 0 110px;width:110px;text-align:right;font-size:0.86rem;font-weight:700;color:#22c55e;">${_fmtPay(g)}</span>
          ${reasonHtml}
        </label>`;
      });
      listEl.innerHTML = html;
      if (summaryEl) {
        if (selected.size === 0) {
          summaryEl.innerHTML = '<span style="color:var(--text-gray);">No dates selected.</span>';
        } else {
          const payStr = totalPay === Math.round(totalPay) ? `$${Math.round(totalPay)}` : `$${totalPay.toFixed(2)}`;
          const conflictNote = greyed.size > 0
            ? ` <span style="color:#fbbf24;">· ${greyed.size} other date${greyed.size === 1 ? '' : 's'} greyed out</span>`
            : '';
          summaryEl.innerHTML = `<strong style="color:#22c55e;">${selected.size} date${selected.size === 1 ? '' : 's'} selected</strong> · ${payStr} total${conflictNote}`;
        }
      }
      if (bookBtn) {
        bookBtn.disabled = selected.size === 0;
        bookBtn.style.opacity = selected.size === 0 ? '0.5' : '1';
        bookBtn.style.cursor = selected.size === 0 ? 'not-allowed' : 'pointer';
        bookBtn.textContent = selected.size === 0
          ? (data.requires_contract ? 'Book Selected & Sign Contract' : 'Book Selected')
          : (data.requires_contract ? `Book ${selected.size} & Sign Contract` : `Book ${selected.size} Date${selected.size === 1 ? '' : 's'}`);
      }
      // Re-bind handlers — keyed by composite key.
      listEl.querySelectorAll('.shm-check').forEach(cb => {
        cb.addEventListener('change', (e) => {
          e.stopPropagation();
          const key = cb.dataset.key;
          if (cb.checked) selected.add(key); else selected.delete(key);
          _refresh();
        });
      });
      listEl.querySelectorAll('.shm-row').forEach(row => {
        row.addEventListener('click', (e) => {
          if (e.target.classList.contains('shm-check')) return;
          const cb = row.querySelector('.shm-check');
          if (!cb || cb.disabled) return;
          e.preventDefault();
          cb.checked = !cb.checked;
          cb.dispatchEvent(new Event('change'));
        });
      });
    }

    const freqLine = freqDays > 0
      ? `<p style="margin:0 0 14px 0;font-size:0.82rem;color:#fcd34d;background:rgba(245,158,11,0.10);border:1px solid rgba(245,158,11,0.35);border-radius:6px;padding:8px 12px;line-height:1.45;">⏱ Frequency rule: <strong>${freqDays} days between bookings</strong>. Pick a date and we'll grey out anything inside that window.</p>`
      : '';
    const contractLine = data.requires_contract
      ? `<p style="margin:0 0 8px 0;font-size:0.78rem;color:var(--text-gray);font-style:italic;">This venue requires a contract on booking. You'll type your name once on the next step — it covers every date you pick.</p>`
      : '';
    const body = `
      <p style="margin:0 0 8px 0;font-size:0.92rem;color:var(--text);"><strong>${_esc(data.venue_name)}</strong> is offering you ${eligible.length} date${eligible.length === 1 ? '' : 's'} in their recurring series.</p>
      ${contractLine}
      ${freqLine}
      <div id="shmList" style="display:flex;flex-direction:column;gap:6px;max-height:50vh;overflow-y:auto;margin-bottom:14px;"></div>
      <div id="shmSummary" style="font-size:0.86rem;color:var(--text);padding:8px 12px;background:rgba(0,0,0,0.25);border-radius:6px;"></div>
    `;
    window.showStyledModal(
      `📅 Pick Your Dates — ${_esc(data.venue_name)}`,
      body,
      [
        // Cancel = close the modal, take no action. Offer stays
        // active, artist still has 24h to revisit (Jul 2026 fix).
        { text: 'Cancel', style: 'ghost' },
        { text: 'Decline All', style: 'danger',
          onClick: async () => { await _declineAll(token); }
        },
        { text: 'Book Selected', style: 'primary', id: 'shmBookBtn',
          onClick: async () => {
            if (selected.size === 0) return false;
            if (data.requires_contract) {
              _openContractSubmodal(token, data, selected, eligible);
              return; // submodal takes over
            }
            await _submitBulk(token, selected, eligible, null);
          }
        },
      ],
      { size: 'lg', tone: 'info' }
    );
    // Wire after modal mounts. showStyledModal renders synchronously
    // but the button id we set on the action object isn't always
    // applied — fall back to a queryAll.
    setTimeout(() => {
      const btns = document.querySelectorAll('.gfm-modal-footer .btn');
      // Assume primary is the last one — gf-modals renders in order.
      const last = btns[btns.length - 1];
      if (last && !last.id) last.id = 'shmBookBtn';
      _refresh();
    }, 30);
  }

  async function _openContractSubmodal(token, data, selected, eligible) {
    const pickedDates = eligible
      .filter(g => selected.has(`${g.gig_id}_${g.slot_id}`))
      .sort((a, b) => (a.date || '').localeCompare(b.date || ''));
    // Fetch the real contract body using the FIRST picked gig as the
    // template source. The contract template has fields like
    // {{gig_date}} that get filled per-gig — for the multi-date series
    // accept we show the first date's rendered body so the artist sees
    // the actual venue contract language, then prepend a "This contract
    // covers ALL of these dates" schedule table so they know what
    // they're signing for.
    let rendered_body = '';
    let _fetch_error = '';
    try {
      const first = pickedDates[0];
      if (!first) {
        _fetch_error = 'No pick selected.';
      } else {
        const url = `/api/gigs/${first.gig_id}/contract-preview?artist_id=${data.artist_id}&slot_id=${first.slot_id}`;
        const pr = await fetch(url, { credentials: 'include' });
        if (!pr.ok) {
          let detail = '';
          try { const ej = await pr.json(); detail = ej.detail || ''; } catch (_) {}
          _fetch_error = `Contract fetch failed (${pr.status}${detail ? ' · ' + detail : ''}). Refresh and try again.`;
        } else {
          const pj = await pr.json();
          rendered_body = pj.rendered_body || pj.body || '';
          if (!rendered_body) {
            _fetch_error = pj.contract_type === 'pdf_upload'
              ? 'PDF contracts can’t be batch-signed for a series. Open each gig individually.'
              : 'Venue contract is empty — contact the venue.';
          }
        }
      }
    } catch (e) {
      _fetch_error = `Network error loading contract: ${(e && e.message) || 'unknown'}`;
    }

    const datesHtml = pickedDates.map(g =>
      `<tr><td style="padding:5px 14px 5px 0;font-size:0.85rem;color:var(--text);white-space:nowrap;">${_fmtDate(g.date)}</td>
       <td style="padding:5px 14px 5px 0;font-size:0.85rem;color:var(--text-gray);white-space:nowrap;">${_fmtTime(g.start_time)} – ${_fmtTime(g.end_time)}</td>
       <td style="padding:5px 0;font-size:0.85rem;color:#22c55e;font-weight:600;text-align:right;white-space:nowrap;">${_fmtPay(g)}</td></tr>`
    ).join('');

    const totalPay = pickedDates.reduce((s, g) => s + (Number(g.pay) || 0), 0);
    const totalStr = totalPay === Math.round(totalPay) ? `$${Math.round(totalPay)}` : `$${totalPay.toFixed(2)}`;

    const body = `
      <p style="margin:0 0 8px 0;font-size:0.9rem;color:var(--text);">Read the contract below carefully. Your signature covers <strong>all ${pickedDates.length} date${pickedDates.length === 1 ? '' : 's'}</strong> listed.</p>
      <div style="background:rgba(124,107,255,0.10);border:1px solid rgba(124,107,255,0.35);border-radius:6px;padding:10px 12px;margin-bottom:14px;">
        <div style="font-size:0.66rem;color:#a78bfa;text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin-bottom:6px;">This contract covers ${pickedDates.length} date${pickedDates.length === 1 ? '' : 's'} · Total ${totalStr}</div>
        <table style="border-collapse:collapse;">
          ${datesHtml}
        </table>
      </div>
      <div class="gf-panel" style="background:var(--bg);font-size:0.85rem;line-height:1.65;color:#e5e5e5;max-height:42vh;overflow-y:auto;padding:14px;border:1px solid var(--border);border-radius:6px;">
        ${rendered_body || `<p style="color:#ef4444;font-style:italic;">${_esc(_fetch_error || 'Could not load the venue&#39;s contract. Please close this and try again.')}</p>`}
      </div>
      <label style="font-size:0.85rem;color:var(--cyan);display:block;margin:18px 0 8px;">Sign by typing your full legal name:</label>
      <input type="text" id="shmSigInput" placeholder="Your Full Legal Name" autocomplete="off"
        style="width:100%;font-style:italic;font-size:1rem;padding:10px 12px;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:6px;color:var(--text);box-sizing:border-box;">
      <div id="shmSigStatus" style="font-size:0.82rem;margin-top:10px;text-align:right;min-height:20px;"></div>
    `;
    window.showStyledModal(
      `📋 Contract — Review & Sign to Book ${pickedDates.length} Date${pickedDates.length === 1 ? '' : 's'}`,
      body,
      [
        { text: 'Back', style: 'ghost' },
        { text: `Sign & Book ${pickedDates.length}`, style: 'primary',
          onClick: async () => {
            // Find the TOPMOST overlay (the contract sub-modal) to read
            // the input from. querySelector returns the first match —
            // when modals stack, that can be the picker behind us, not
            // the sub-modal. Iterate from the last to find ours.
            const overlays = document.querySelectorAll('.gfm-modal-overlay');
            const overlay = overlays[overlays.length - 1] || document.querySelector('.gfm-modal-overlay');
            const inp = overlay && overlay.querySelector('#shmSigInput');
            const status = overlay && overlay.querySelector('#shmSigStatus');
            const btns = overlay && overlay.querySelectorAll('.gfm-modal-footer .btn');
            const signBtn = btns && btns[btns.length - 1];
            const sig = (inp?.value || '').trim();
            if (!sig || sig.length < 2) {
              if (status) { status.textContent = 'Please type your full legal name to sign.'; status.style.color = '#ef4444'; }
              return false;
            }
            // Visible in-flight state so the user knows the click landed.
            if (signBtn) { signBtn.disabled = true; signBtn.textContent = 'Signing & Booking…'; }
            if (status) { status.textContent = 'Submitting…'; status.style.color = '#fbbf24'; }
            const ok = await _submitBulk(token, selected, eligible, sig);
            if (!ok) {
              // Re-enable so the user can retry. _submitBulk already
              // showed an inline / showErrorModal failure message.
              if (signBtn) { signBtn.disabled = false; signBtn.textContent = `Sign & Book ${pickedDates.length}`; }
              if (status) { status.textContent = ''; }
              return false;  // keep this modal open
            }
            // On success, _submitBulk handles closeAllModals + success
            // toast. Returning undefined here lets the modal close
            // naturally on top of that.
          }
        },
      ],
      { size: 'lg', tone: 'info' }
    );
  }

  async function _submitBulk(token, selected, eligible, signatureName) {
    const picks = eligible
      .filter(g => selected.has(`${g.gig_id}_${g.slot_id}`))
      .map(g => ({ gig_id: g.gig_id, slot_id: g.slot_id }));
    try {
      const res = await fetch(`/api/series-hold/respond/${encodeURIComponent(token)}`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'accept', slot_picks: picks, signature_name: signatureName || undefined }),
      });
      const data = await res.json().catch(() => ({}));
      // #13 audit fix: backend may return needs_signature=true if the
      // venue toggled their contract requirement on AFTER the modal
      // loaded. Re-prompt with the sub-modal instead of showing a
      // generic "Booking failed".
      if (!res.ok || !data.ok) {
        if (data && data.needs_signature) {
          // Build a minimal `data`-shape for the sub-modal — we don't
          // have the original `data` here but the sub-modal only needs
          // venue_name + date/pay info per pick, which is in `eligible`.
          // Refetch the offer to get venue name + contract type.
          let fresh = null;
          try {
            const fr = await fetch(`/api/series-hold/offer/${encodeURIComponent(token)}`, { credentials: 'include' });
            if (fr.ok) fresh = await fr.json();
          } catch (_) {}
          if (fresh && fresh.ok) {
            _openContractSubmodal(token, fresh, selected, eligible);
            return false;
          }
        }
        if (window.showErrorModal) window.showErrorModal('Booking failed', data.message || data.detail || 'Try again.');
        return false;
      }
      if (window.closeAllModals) window.closeAllModals();
      if (window.showSuccessModal) {
        window.showSuccessModal('Booked!', data.message || `Booked ${data.booked_count} gig(s). The venue will be notified.`);
      }
      // Refresh artist calendar + banner
      if (typeof window.loadGigs === 'function') await window.loadGigs();
      if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
      if (typeof window.renderCalendar === 'function') window.renderCalendar();
      if (window.loadHoldOffersBanner) try { window.loadHoldOffersBanner(); } catch (_) {}
      // Clear hash so reload doesn't reopen
      if (location.hash.includes('series-hold=')) history.replaceState(null, '', location.pathname + location.search);
      return true;
    } catch (e) {
      if (window.showErrorModal) window.showErrorModal('Booking failed', e.message || 'Try again.');
      return false;
    }
  }

  async function _declineAll(token) {
    const ok = window.showConfirm
      ? await new Promise(res => window.showConfirm(
          'Decline all dates?',
          'This passes the whole series to the next artist on the venue\'s hold list. You won\'t see these dates again unless the venue invites you another way.',
          () => res(true),
          () => res(false),
          { tone: 'warning', confirmStyle: 'danger', confirmLabel: 'Decline All', cancelLabel: 'Keep Open' }
        ))
      : confirm('Decline all dates?');
    if (!ok) return false;
    try {
      const res = await fetch(`/api/series-hold/respond/${encodeURIComponent(token)}`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'decline' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        if (window.showErrorModal) window.showErrorModal('Failed', data.message || 'Try again.');
        return false;
      }
      if (window.closeAllModals) window.closeAllModals();
      if (window.showSuccessModal) window.showSuccessModal('Declined', data.message || 'The venue has been notified.');
      if (window.loadHoldOffersBanner) try { window.loadHoldOffersBanner(); } catch (_) {}
      if (location.hash.includes('series-hold=')) history.replaceState(null, '', location.pathname + location.search);
    } catch (e) {
      if (window.showErrorModal) window.showErrorModal('Failed', e.message || 'Try again.');
    }
  }

  // Auto-open if the page loads with #series-hold=TOKEN
  function _initHashHandler() {
    const m = (location.hash || '').match(/#series-hold=([^&]+)/);
    if (m && m[1]) {
      // Defer until other init has run
      setTimeout(() => openSeriesHoldModal(decodeURIComponent(m[1])), 200);
    }
  }

  window.openSeriesHoldModal = openSeriesHoldModal;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initHashHandler);
  } else {
    _initHashHandler();
  }
})();
