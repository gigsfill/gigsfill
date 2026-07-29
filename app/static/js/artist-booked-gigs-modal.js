/**
 * Artist — "Show All Booked Gigs" modal.
 *
 * Opens a sortable list of every confirmed booking the artist holds.
 * Multi-slot gigs expand into one row per booked slot (each slot may
 * have its own time / pay / deal). Two export options in the header:
 * CSV (Excel-friendly) and PDF (browser print-to-PDF, landscape).
 *
 * Reuses /api/my/gigs (already powers the artist calendar) so no new
 * backend endpoint is needed. Cancel actions route through the same
 * endpoints the calendar's gig-detail modal uses.
 *
 * Exposes window.openArtistBookedGigsModal() — wired from
 * artist-book-gigs-init.js next to the iCal export link.
 */
(function () {
  'use strict';

  const STATE = {
    artistId: null,
    rows: [],            // expanded (gig + slot) rows
    sortCol: 'date',     // 'date' | 'venue' | 'city' | 'start' | 'end' | 'pay'
    sortDir: 1,          // 1 asc, -1 desc — only used for non-date columns.
                         // Date column is a custom split-sort: future ascending
                         // (soonest first), then past descending (most recent
                         // first, oldest very last).
  };

  // YYYY-MM-DD string for today, for splitting past vs future.
  function todayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  function isPast(r) {
    return (r.date || '') < todayStr();
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtDate(ymd) {
    if (!ymd) return '';
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(ymd));
    if (!m) return String(ymd);
    const dt = new Date(parseInt(m[1]), parseInt(m[2]) - 1, parseInt(m[3]));
    if (isNaN(dt.getTime())) return String(ymd);
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const mos = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${days[dt.getDay()]}, ${mos[dt.getMonth()]} ${dt.getDate()} ${dt.getFullYear()}`;
  }

  function fmtTime(t) {
    if (!t) return '';
    const m = /^(\d{1,2}):(\d{2})/.exec(String(t));
    if (!m) return String(t);
    let h = parseInt(m[1], 10);
    const min = m[2];
    const ap = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return `${h}:${min} ${ap}`;
  }

  // Expand /api/my/gigs response into one row per (gig × booked slot).
  // Single-slot gig with status='booked' → 1 row pulled from gig fields.
  // Multi-slot gig → 1 row per slot where slot.artist_id == artistId AND
  // slot.status='booked'. Pending / awaiting-countersign rows are excluded
  // — the modal title says "Booked Gigs", not "Pending Gigs".
  function expandRows(myGigs, artistId) {
    const aid = parseInt(artistId, 10);
    const out = [];
    (myGigs || []).forEach(g => {
      const cityState = [g.city, g.state].filter(Boolean).join(', ');
      // Jul 2026: post single→multi backfill every gig has a slots array,
      // so we always take this branch. The is_multi_slot flag on the emitted
      // row reflects TRUE multi-slot-ness (2+ slots) so the "Slot N" chip
      // and CSV column only render for gigs where slot identity matters.
      if (Array.isArray(g.slots) && g.slots.length) {
        const isReallyMulti = g.slots.length > 1;
        g.slots.forEach(s => {
          if (s.status !== 'booked') return;
          if (parseInt(s.artist_id, 10) !== aid) return;
          out.push({
            gig_id: g.id,
            slot_id: s.slot_id || s.id,
            slot_number: s.slot_number,
            date: g.date,
            venue_id: g.venue_id,
            venue_name: g.venue_name || '',
            city: g.city || '',
            state: g.state || '',
            city_state: cityState,
            start_time: s.start_time || g.start_time,
            end_time: s.end_time || g.end_time,
            pay: s.pay,
            pay_summary: s.pay_summary,
            is_door_deal: !!s.is_door_deal,
            title: g.title || '',
            is_multi_slot: isReallyMulti,
          });
        });
      } else if (g.status === 'booked' && parseInt(g.artist_id, 10) === aid) {
        out.push({
          gig_id: g.id,
          slot_id: null,
          slot_number: null,
          date: g.date,
          venue_id: g.venue_id,
          venue_name: g.venue_name || '',
          city: g.city || '',
          state: g.state || '',
          city_state: cityState,
          start_time: g.start_time,
          end_time: g.end_time,
          pay: g.pay,
          pay_summary: g.pay_summary,
          is_door_deal: !!g.is_door_deal,
          title: g.title || '',
          is_multi_slot: false,
        });
      }
    });
    return out;
  }

  function sortRows() {
    const { sortCol, sortDir } = STATE;
    // Special case: the date column does a split sort — future gigs come
    // first (closest-to-today at the top), past gigs come after (oldest
    // very last, i.e. past descending). Flipping the sort direction
    // mirrors this: past first ascending, then future descending.
    if (sortCol === 'date') {
      STATE.rows.sort((a, b) => {
        const ap = isPast(a), bp = isPast(b);
        if (ap !== bp) {
          // Future first when sortDir=1, past first when sortDir=-1.
          return sortDir === 1 ? (ap ? 1 : -1) : (ap ? -1 : 1);
        }
        const ka = (a.date || '') + (a.start_time || '');
        const kb = (b.date || '') + (b.start_time || '');
        if (ap) {
          // Both past — oldest last when sortDir=1 (i.e. recent past first,
          // descending date); flipped when sortDir=-1.
          if (ka < kb) return  1 * sortDir;
          if (ka > kb) return -1 * sortDir;
          return 0;
        }
        // Both future — soonest first when sortDir=1.
        if (ka < kb) return -1 * sortDir;
        if (ka > kb) return  1 * sortDir;
        return 0;
      });
      return;
    }
    const val = (r) => {
      if (sortCol === 'venue')  return (r.venue_name || '').toLowerCase();
      if (sortCol === 'city')   return (r.city_state || '').toLowerCase();
      if (sortCol === 'start')  return r.start_time || '';
      if (sortCol === 'end')    return r.end_time || '';
      if (sortCol === 'pay')    return Number(r.pay || 0);
      return '';
    };
    STATE.rows.sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va < vb) return -1 * sortDir;
      if (va > vb) return 1 * sortDir;
      return 0;
    });
  }

  function setSort(col) {
    if (STATE.sortCol === col) {
      STATE.sortDir *= -1;
    } else {
      STATE.sortCol = col;
      // Date defaults to ascending (closest-to-today first); the rest
      // default to ascending alphabetically/numerically.
      STATE.sortDir = 1;
    }
    render();
  }

  function render() {
    const body = document.getElementById('artistBookedGigsBody');
    if (!body) return;
    if (!STATE.rows.length) {
      body.innerHTML = `<div style="text-align:center;color:#94a3b8;padding:40px 20px;font-size:0.9rem;">
        You have no confirmed booked gigs yet.
      </div>`;
      return;
    }
    sortRows();

    const arrow = (col) => STATE.sortCol === col ? (STATE.sortDir === 1 ? ' ▲' : ' ▼') : '';
    const thStyle = 'text-align:left;padding:9px 10px;font-size:0.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.03em;cursor:pointer;user-select:none;white-space:nowrap;border-bottom:1px solid #2a3040;background:#151b28;position:sticky;top:0;z-index:1;';
    const thStyleRight = thStyle + 'text-align:right;';
    const th = (col, label, right) => `<th onclick="window._artistBookedGigsSort('${col}')" style="${right ? thStyleRight : thStyle}">${label}${arrow(col)}</th>`;

    // Track the first past row when the date sort is active so we can
    // drop a thin section header between the two groups — makes the
    // future/past split obvious without crowding the table.
    let sawPast = false;
    const showSplitHeader = STATE.sortCol === 'date';
    const rowsHtml = STATE.rows.map(r => {
      const past = isPast(r);
      let splitHeader = '';
      if (showSplitHeader && past && !sawPast) {
        sawPast = true;
        splitHeader = `
          <tr><td colspan="7" style="padding:10px 10px 6px;font-size:0.7rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;background:rgba(255,255,255,0.02);border-top:1px solid rgba(255,255,255,0.06);">Past gigs</td></tr>`;
      }
      const venueLink = r.venue_id
        ? `<a href="/app/venue-profile.html?venue_id=${r.venue_id}" target="_blank" rel="noopener" style="color:${past ? '#94a3b8' : '#7c6bff'};text-decoration:none;font-weight:600;">${esc(r.venue_name)}</a>`
        : esc(r.venue_name);
      const slotChip = r.is_multi_slot
        ? `<span style="display:inline-block;margin-left:6px;padding:1px 6px;background:rgba(139,92,246,0.18);border:1px solid rgba(139,92,246,0.35);border-radius:4px;font-size:0.65rem;color:#c4b5fd;vertical-align:middle;">Slot ${esc(r.slot_number)}</span>`
        : '';
      const title = r.title && r.title.trim()
        ? `<div style="font-size:0.7rem;color:#94a3b8;font-style:italic;margin-top:2px;">${esc(r.title)}</div>`
        : '';
      const payDisp = r.pay_summary || (r.pay != null ? '$' + Number(r.pay).toFixed(2) : '');
      // Past gigs are dimmed and the Action column shows a small
      // "Played" tag instead of a Cancel button — past dates can't be
      // un-booked from this UI (and the artist already played them).
      const dateColor = past ? '#94a3b8' : '#e2e8f0';
      const cellOpacity = past ? 'opacity:0.78;' : '';
      const actionCell = past
        ? `<span style="display:inline-block;padding:2px 8px;font-size:0.7rem;background:rgba(148,163,184,0.12);border:1px solid rgba(148,163,184,0.25);border-radius:4px;color:#94a3b8;">Played</span>`
        : `<button onclick="window._artistBookedGigsCancel(${r.gig_id}, ${r.slot_id || 'null'}, ${r.slot_number == null ? 'null' : r.slot_number})"
                    style="padding:4px 10px;font-size:0.72rem;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.4);border-radius:5px;color:#fca5a5;cursor:pointer;font-weight:600;"
                    onmouseover="this.style.background='rgba(239,68,68,0.2)'"
                    onmouseout="this.style.background='rgba(239,68,68,0.12)'">Cancel</button>`;
      return `${splitHeader}
        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);${cellOpacity}">
          <td style="padding:9px 10px;font-size:0.82rem;color:${dateColor};white-space:nowrap;vertical-align:top;font-weight:600;">${esc(fmtDate(r.date))}</td>
          <td style="padding:9px 10px;font-size:0.82rem;vertical-align:top;">
            ${venueLink}${slotChip}
            ${title}
          </td>
          <td style="padding:9px 10px;font-size:0.8rem;color:#cbd5e1;white-space:nowrap;vertical-align:top;">${esc(r.city_state)}</td>
          <td style="padding:9px 10px;font-size:0.8rem;color:#cbd5e1;white-space:nowrap;vertical-align:top;">${esc(fmtTime(r.start_time))}</td>
          <td style="padding:9px 10px;font-size:0.8rem;color:#cbd5e1;white-space:nowrap;vertical-align:top;">${esc(fmtTime(r.end_time))}</td>
          <td style="padding:9px 10px;font-size:0.82rem;color:#22c55e;font-weight:600;white-space:nowrap;text-align:right;vertical-align:top;">${esc(payDisp)}</td>
          <td style="padding:9px 10px;vertical-align:top;white-space:nowrap;text-align:center;">${actionCell}</td>
        </tr>`;
    }).join('');

    body.innerHTML = `
      <div style="overflow:auto;max-height:calc(88vh - 140px);border:1px solid #2a3040;border-radius:8px;">
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr>
            ${th('date', 'Date')}
            ${th('venue', 'Venue')}
            ${th('city', 'City / State')}
            ${th('start', 'Start')}
            ${th('end', 'End')}
            ${th('pay', 'Pay', true)}
            <th style="${thStyle}text-align:center;cursor:default;">Action</th>
          </tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
      <div style="margin-top:12px;font-size:0.75rem;color:#64748b;text-align:right;">
        ${STATE.rows.length} booked ${STATE.rows.length === 1 ? 'slot' : 'slots'}
      </div>`;
  }

  // ---------- Export: CSV (Excel-friendly) ----------
  function exportCsv() {
    if (!STATE.rows.length) return;
    sortRows();
    const headers = ['Date', 'Venue', 'City', 'State', 'Start', 'End', 'Pay', 'Title', 'Slot'];
    const escCsv = (s) => {
      const v = String(s == null ? '' : s);
      return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
    };
    const lines = [headers.join(',')];
    STATE.rows.forEach(r => {
      lines.push([
        fmtDate(r.date), r.venue_name, r.city, r.state,
        fmtTime(r.start_time), fmtTime(r.end_time),
        r.pay_summary || (r.pay != null ? '$' + Number(r.pay).toFixed(2) : ''),
        r.title || '',
        r.is_multi_slot ? `Slot ${r.slot_number || ''}` : '',
      ].map(escCsv).join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'gigsfill_booked_gigs.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  // ---------- Export: PDF (browser print, landscape) ----------
  // Opens a fresh tab containing only the table with a landscape print
  // stylesheet. The user picks "Save as PDF" from their browser's print
  // dialog — no jsPDF dependency needed, and the output matches whatever
  // PDF engine the user already trusts.
  function exportPdf() {
    if (!STATE.rows.length) return;
    sortRows();
    const today = (new Date()).toLocaleDateString();
    const rowsHtml = STATE.rows.map(r => `
      <tr>
        <td>${esc(fmtDate(r.date))}</td>
        <td>${esc(r.venue_name)}${r.is_multi_slot ? ` <span class="slot">Slot ${esc(r.slot_number)}</span>` : ''}${r.title ? `<div class="title">${esc(r.title)}</div>` : ''}</td>
        <td>${esc(r.city_state)}</td>
        <td>${esc(fmtTime(r.start_time))}</td>
        <td>${esc(fmtTime(r.end_time))}</td>
        <td class="pay">${esc(r.pay_summary || (r.pay != null ? '$' + Number(r.pay).toFixed(2) : ''))}</td>
      </tr>`).join('');
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Booked Gigs — GigsFill</title>
<style>
  @page { size: landscape; margin: 0.5in; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #111; margin: 0; padding: 16px; }
  .hdr { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; border-bottom: 2px solid #111; padding-bottom: 8px; }
  .hdr h1 { font-size: 18px; margin: 0; }
  .hdr .meta { font-size: 11px; color: #555; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; page-break-inside: auto; }
  thead { display: table-header-group; }
  tr { page-break-inside: avoid; page-break-after: auto; }
  th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #ddd; vertical-align: top; }
  th { background: #f3f4f6; font-size: 10px; text-transform: uppercase; letter-spacing: 0.03em; }
  td.pay, th.pay { text-align: right; white-space: nowrap; font-weight: 600; color: #059669; }
  .slot { display: inline-block; padding: 1px 5px; font-size: 9px; background: #ede9fe; color: #6d28d9; border-radius: 3px; margin-left: 4px; }
  .title { font-size: 10px; color: #6b7280; font-style: italic; margin-top: 1px; }
  .print-btn { position: fixed; top: 10px; right: 10px; padding: 8px 16px; background: #2563eb; color: white; border: none; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; }
  @media print { .print-btn { display: none; } }
</style></head><body>
  <button class="print-btn" onclick="window.print()">Print / Save as PDF</button>
  <div class="hdr">
    <h1>Booked Gigs</h1>
    <div class="meta">Generated ${esc(today)} · ${STATE.rows.length} booked ${STATE.rows.length === 1 ? 'slot' : 'slots'}</div>
  </div>
  <table>
    <thead><tr>
      <th>Date</th><th>Venue</th><th>City / State</th><th>Start</th><th>End</th><th class="pay">Pay</th>
    </tr></thead>
    <tbody>${rowsHtml}</tbody>
  </table>
  <script>window.addEventListener('load', function(){ setTimeout(function(){ window.print(); }, 300); });</` + `script>
</body></html>`;
    const w = window.open('', '_blank');
    if (!w) {
      alert('Could not open print preview — please allow pop-ups for GigsFill.');
      return;
    }
    w.document.open();
    w.document.write(html);
    w.document.close();
  }

  // ---------- Cancel a booking ----------
  // Styled confirm modal first; on confirm, hit the right cancel endpoint
  // (slot-level for multi-slot, gig-level for single-slot) and refresh
  // both the table and the underlying calendar.
  async function performCancel(gigId, slotId, slotNum, reasonText) {
    try {
      if (slotId) {
        await window.apiDeleteSafe(`/api/gigs/${gigId}/slots/${slotId}/cancel`, {
          cancelled_by: 'artist', cancellation_reason: reasonText,
        });
      } else {
        await window.apiDeleteSafe(`/api/gigs/${gigId}/cancel`, {
          cancelled_by: 'artist', cancellation_reason: reasonText,
          artist_id: parseInt(STATE.artistId, 10),
        });
      }
      await reload();
      if (typeof window.refreshArtistGigs === 'function') {
        try { await window.refreshArtistGigs(); } catch (_) {}
      }
      if (typeof window.showSuccessModal === 'function') {
        window.showSuccessModal(
          slotId ? `Slot ${slotNum} Cancelled` : 'Gig Cancelled',
          slotId ? `Slot ${slotNum} has been released.` : 'The gig has been cancelled.'
        );
      }
    } catch (e) {
      const msg = (e && e.message) || 'Could not cancel — please try again.';
      if (typeof window.showErrorModal === 'function') {
        window.showErrorModal('Cancellation Failed', msg);
      } else {
        alert(msg);
      }
    }
  }

  function cancelRow(gigId, slotId, slotNum) {
    const headline = slotId
      ? `Cancel your Slot ${slotNum} booking?`
      : 'Cancel this booking?';
    const reasonInputId = '_cancelBookedReasonInput_' + gigId + '_' + (slotId || 0);
    const content = `
      <p style="margin:0 0 10px;color:var(--text,#e2e8f0);">${esc(headline)}</p>
      <p style="margin:0 0 10px;font-size:0.82rem;color:#94a3b8;">The venue will be notified. Please share an optional reason.</p>
      <textarea id="${reasonInputId}" rows="3" placeholder="Reason (optional)"
                style="width:100%;background:rgba(21,27,40,0.8);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#e2e8f0;padding:8px 10px;font-family:inherit;font-size:0.85rem;resize:vertical;"></textarea>`;
    if (typeof window.showStyledModal !== 'function') {
      const reason = prompt(headline + ' Optional reason:');
      if (reason === null) return;
      performCancel(gigId, slotId, slotNum, (reason || '').trim() || 'No reason provided');
      return;
    }
    window.showStyledModal('Confirm Cancellation', content, [
      { text: 'Keep Booking', style: 'ghost' },
      {
        text: slotId ? 'Cancel Slot' : 'Cancel Gig',
        style: 'danger',
        onClick: () => {
          const ta = document.getElementById(reasonInputId);
          const reasonText = ((ta && ta.value) || '').trim() || 'No reason provided';
          performCancel(gigId, slotId, slotNum, reasonText);
        },
      },
    ], { tone: 'warning', size: 'sm' });
  }

  async function reload() {
    try {
      const res = await fetch(`/api/my/gigs?artist_id=${STATE.artistId}`, { credentials: 'include' });
      const data = res.ok ? await res.json() : [];
      STATE.rows = expandRows(data, STATE.artistId);
      render();
    } catch (e) {
      STATE.rows = [];
      render();
    }
  }

  function close() {
    const o = document.getElementById('artistBookedGigsOverlay');
    if (o) o.remove();
  }

  function open(artistId) {
    if (!artistId) return;
    STATE.artistId = artistId;
    STATE.sortCol = 'date';
    STATE.sortDir = 1;
    let overlay = document.getElementById('artistBookedGigsOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'artistBookedGigsOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,0.72);display:flex;align-items:center;justify-content:center;padding:20px;';
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    overlay.innerHTML = `
      <div style="background:#1a1f2e;border:1px solid #2a3040;border-radius:14px;width:100%;max-width:1100px;max-height:92vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.55);">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 22px;border-bottom:1px solid #2a3040;flex-wrap:wrap;gap:10px;">
          <div style="display:flex;align-items:baseline;gap:12px;">
            <div style="font-size:1.05rem;font-weight:700;color:#e2e8f0;">📅 All Booked Gigs</div>
            <div style="font-size:0.75rem;color:#94a3b8;">Chronological · closest first</div>
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <button onclick="window._artistBookedGigsExportCsv()" style="font-size:0.75rem;color:var(--text-muted,#94a3b8);background:transparent;text-decoration:none;padding:4px 10px;border:1px solid rgba(255,255,255,0.12);border-radius:6px;transition:all 0.2s;cursor:pointer;" onmouseover="this.style.borderColor='rgba(34,197,94,0.4)';this.style.color='#22c55e'" onmouseout="this.style.borderColor='rgba(255,255,255,0.12)';this.style.color='var(--text-muted,#94a3b8)'">📊 Export to Excel (.csv)</button>
            <button onclick="window._artistBookedGigsExportPdf()" style="font-size:0.75rem;color:var(--text-muted,#94a3b8);background:transparent;text-decoration:none;padding:4px 10px;border:1px solid rgba(255,255,255,0.12);border-radius:6px;transition:all 0.2s;cursor:pointer;" onmouseover="this.style.borderColor='rgba(6,182,212,0.4)';this.style.color='#06b6d4'" onmouseout="this.style.borderColor='rgba(255,255,255,0.12)';this.style.color='var(--text-muted,#94a3b8)'">📄 Export to PDF</button>
            <button onclick="window._artistBookedGigsClose()" style="background:transparent;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;line-height:1;padding:0 6px;" title="Close">✕</button>
          </div>
        </div>
        <div id="artistBookedGigsBody" style="overflow:auto;padding:14px 18px 18px;flex:1;">
          <div style="text-align:center;color:#94a3b8;padding:40px;font-size:0.85rem;">Loading…</div>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    reload();
  }

  // Public hooks
  window.openArtistBookedGigsModal = open;
  window._artistBookedGigsClose = close;
  window._artistBookedGigsSort = setSort;
  window._artistBookedGigsCancel = cancelRow;
  window._artistBookedGigsExportCsv = exportCsv;
  window._artistBookedGigsExportPdf = exportPdf;
})();
