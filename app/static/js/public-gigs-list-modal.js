/**
 * Public "All Gigs" list modal — shared across the artist public
 * profile, city public calendar, and venue public profile.
 *
 * Same visual language as artist-booked-gigs-modal.js (the artist's
 * private "Show All Booked Gigs" list on book-gigs.html) minus the
 * Pay column, Cancel action, and any authenticated actions. This is
 * a read-only surface for visitors — see-at-a-glance list view + CSV
 * / PDF export of what would otherwise take scrolling through the
 * month grid to find.
 *
 * Usage:
 *   window.openPublicGigsListModal({
 *     title: '📅 All Booked Gigs — Fridays Past',
 *     rows: [{date, venue_name, venue_id, city, state, start_time,
 *             end_time, title?, is_multi_slot?, slot_number?,
 *             _link_target?: 'venue' | 'artist' | 'gig',
 *             _link_id?: number}],
 *     exportBasename: 'fridays_past_gigs',
 *   });
 *
 * `rows` shape: normalize before calling — this module doesn't fetch;
 * callers pull from whichever endpoint they already use (artist gigs,
 * venue gigs, /api/gigs/public) and shape it. Keeps the module data-
 * agnostic and safe on any page.
 */
(function () {
  'use strict';

  // Column keys the caller can arrange in any order. Default keeps
  // backwards compat with earlier callers who didn't pass `columns`.
  // 2026-08-05: added `address` (full "123 Main St, City, ST") and
  // `time` (combined Start – End) column keys. Old `city` / `start` /
  // `end` keys still work for any lingering caller.
  const _DEFAULT_COLUMNS = ['date', 'venue', 'city', 'start', 'end'];
  const _COLUMN_LABELS = {
    date: 'Date', venue: 'Venue', city: 'City / State',
    address: 'Address',
    artist: 'Artist', start: 'Start', end: 'End',
    time: 'Time',
  };

  const STATE = {
    rows: [],
    title: '📅 All Gigs',
    exportBasename: 'gigs',
    columns: _DEFAULT_COLUMNS.slice(),
    sortCol: 'date',
    sortDir: 1,
    // Past gigs collapse behind an expandable divider by default —
    // most viewers care about upcoming shows, past gigs are context.
    showPast: false,
  };

  function todayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  function isPast(r) { return (r.date || '') < todayStr(); }

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
    const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    const mos  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
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

  function sortRows() {
    const { sortCol, sortDir } = STATE;
    if (sortCol === 'date') {
      STATE.rows.sort((a, b) => {
        const ap = isPast(a), bp = isPast(b);
        if (ap !== bp) return sortDir === 1 ? (ap ? 1 : -1) : (ap ? -1 : 1);
        const ka = (a.date || '') + (a.start_time || '');
        const kb = (b.date || '') + (b.start_time || '');
        if (ap) {
          if (ka < kb) return  1 * sortDir;
          if (ka > kb) return -1 * sortDir;
          return 0;
        }
        if (ka < kb) return -1 * sortDir;
        if (ka > kb) return  1 * sortDir;
        return 0;
      });
      return;
    }
    const val = (r) => {
      if (sortCol === 'venue')   return (r.venue_name  || '').toLowerCase();
      if (sortCol === 'city')    return _cityState(r).toLowerCase();
      if (sortCol === 'address') return _fullAddress(r).toLowerCase();
      if (sortCol === 'artist')  return (r.artist_name || '').toLowerCase();
      if (sortCol === 'start')   return r.start_time || '';
      if (sortCol === 'end')     return r.end_time   || '';
      if (sortCol === 'time')    return r.start_time || '';
      return '';
    };
    STATE.rows.sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va < vb) return -1 * sortDir;
      if (va > vb) return  1 * sortDir;
      return 0;
    });
  }

  function _cityState(r) {
    return [r.city, r.state].filter(Boolean).join(', ');
  }

  // Full "123 Main St, Thousand Oaks, CA". Falls back to just city/state
  // when the street address isn't on the row (e.g. anonymous /api/gigs/
  // public payloads may not carry address_line_2, and external gigs use
  // `venue_address` / `venue_city` / `venue_state` instead of the
  // canonical `city` / `state`).
  function _fullAddress(r) {
    const line1 = r.address_line_1 || r.venue_address || '';
    const line2 = r.address_line_2 || '';
    const cs = _cityState(r);
    return [line1, line2, cs].filter(Boolean).join(', ');
  }

  // Combined "7:00 PM – 10:00 PM" for the merged Time column.
  function _timeRange(r) {
    const s = fmtTime(r.start_time);
    const e = fmtTime(r.end_time);
    if (s && e) return `${s} – ${e}`;
    return s || e || '';
  }

  function setSort(col) {
    if (STATE.sortCol === col) STATE.sortDir *= -1;
    else { STATE.sortCol = col; STATE.sortDir = 1; }
    render();
  }

  function render() {
    const body = document.getElementById('publicGigsListBody');
    if (!body) return;
    if (!STATE.rows.length) {
      body.innerHTML = `<div style="text-align:center;color:#94a3b8;padding:40px 20px;font-size:0.9rem;">
        No gigs to show.
      </div>`;
      return;
    }
    sortRows();

    const arrow = (col) => STATE.sortCol === col ? (STATE.sortDir === 1 ? ' ▲' : ' ▼') : '';
    const _thBase = 'text-align:left;font-size:0.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.03em;cursor:pointer;user-select:none;white-space:nowrap;border-bottom:1px solid #2a3040;background:#151b28;position:sticky;top:0;z-index:1;';
    // Column-width classes:
    //   • date: `min-width:150px` gives the header + arrow room to
    //     breathe ("Sun, Aug 4 2026 ▲" needs ~150px at 0.7rem).
    //   • time (combined): `min-width:150px` fits "7:00 PM – 10:00 PM".
    //   • city / start / end (legacy single-time columns): compact
    //     (width:1% shrinks to content).
    //   • address / venue / artist: flex — no width constraint, so
    //     they grow to fill the remaining table width.
    const _thDate    = _thBase + 'padding:9px 10px;width:1%;min-width:150px;';
    const _thTime    = _thBase + 'padding:9px 10px;width:1%;min-width:150px;';
    const _thAddress = _thBase + 'padding:9px 10px;max-width:220px;';
    const _thCompact = _thBase + 'padding:9px 6px;width:1%;';
    const _thFlex    = _thBase + 'padding:9px 10px;';
    const _thFor = (col) => {
      if (col === 'date')    return _thDate;
      if (col === 'time')    return _thTime;
      if (col === 'address') return _thAddress;
      if (col === 'venue' || col === 'artist') return _thFlex;
      return _thCompact;
    };
    const th = (col, label) => `<th onclick="window._publicGigsListSort('${col}')" style="${_thFor(col)}">${label}${arrow(col)}</th>`;

    const cols = STATE.columns.slice();
    const hasArtistCol = cols.includes('artist');
    const showSplitHeader = STATE.sortCol === 'date';
    const pastCount = STATE.rows.filter(isPast).length;
    // Divider row spans all columns dynamically now that columns are configurable.
    const chev = STATE.showPast ? '▲' : '▼';
    const dividerLabel = STATE.showPast ? 'Hide past gigs' : 'Show past gigs';
    const pastDividerHtml = (showSplitHeader && pastCount > 0)
      ? `<tr><td colspan="${cols.length}" onclick="window._publicGigsListTogglePast()" style="padding:10px 10px 6px;font-size:0.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;background:rgba(255,255,255,0.03);border-top:1px solid rgba(255,255,255,0.06);cursor:pointer;user-select:none;" onmouseover="this.style.color='#e2e8f0'" onmouseout="this.style.color='#94a3b8'">${chev} ${dividerLabel} (${pastCount})</td></tr>`
      : '';

    // ─── Cell renderers per column key ────────────────────────────────
    // Venue and Artist cells hyperlink to their public profiles when
    // the corresponding id is present on the row. Callers can suppress
    // the venue link with `_no_venue_link` (used when the list is
    // already scoped to a single venue — nowhere new to click to).
    const _cellDate = (r, past, dateColor) =>
      `<td style="padding:9px 10px;width:1%;min-width:150px;font-size:0.82rem;color:${dateColor};white-space:nowrap;vertical-align:top;font-weight:600;">${esc(fmtDate(r.date))}</td>`;
    const _cellVenue = (r, past) => {
      const venueLink = (r.venue_id && !r._no_venue_link)
        ? `<a href="/app/venue-profile.html?venue_id=${r.venue_id}" target="_blank" rel="noopener" style="color:${past ? '#94a3b8' : '#7c6bff'};text-decoration:none;font-weight:600;border-bottom:1px solid rgba(124,107,255,0.35);">${esc(r.venue_name || '')}</a>`
        : `<span style="color:${past ? '#94a3b8' : '#e2e8f0'};font-weight:600;">${esc(r.venue_name || '')}</span>`;
      const slotChip = r.is_multi_slot
        ? `<span style="display:inline-block;margin-left:6px;padding:1px 6px;background:rgba(139,92,246,0.18);border:1px solid rgba(139,92,246,0.35);border-radius:4px;font-size:0.65rem;color:#c4b5fd;vertical-align:middle;">Slot ${esc(r.slot_number)}</span>`
        : '';
      // If the caller opted for a dedicated Artist column we don't
      // repeat the artist chip inline under the venue — that'd
      // duplicate the same name in two cells on the same row.
      let artistInline = '';
      if (!hasArtistCol && r.artist_name) {
        artistInline = r.artist_id
          ? `<div style="font-size:0.72rem;color:${past ? '#94a3b8' : '#22c55e'};margin-top:2px;">🎤 <a href="/app/artist-profile.html?artist_id=${r.artist_id}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;font-weight:600;">${esc(r.artist_name)}</a></div>`
          : `<div style="font-size:0.72rem;color:${past ? '#94a3b8' : '#22c55e'};margin-top:2px;font-weight:600;">🎤 ${esc(r.artist_name)}</div>`;
      }
      const title = r.title && r.title.trim()
        ? `<div style="font-size:0.7rem;color:#94a3b8;font-style:italic;margin-top:2px;">${esc(r.title)}</div>`
        : '';
      return `<td style="padding:9px 10px;font-size:0.82rem;vertical-align:top;">${venueLink}${slotChip}${artistInline}${title}</td>`;
    };
    const _cellCity = (r) =>
      `<td style="padding:9px 8px;width:1%;font-size:0.8rem;color:#cbd5e1;white-space:nowrap;vertical-align:top;">${esc(_cityState(r))}</td>`;
    // Address cell — max-width caps how much table width it consumes so
    // Venue + Artist have room to grow. word-break lets long addresses
    // wrap to a second line inside the cell rather than pushing the
    // table wider.
    const _cellAddress = (r) => {
      const s = _fullAddress(r) || '';
      return `<td style="padding:9px 10px;max-width:220px;font-size:0.8rem;color:#cbd5e1;vertical-align:top;line-height:1.35;word-break:break-word;">${esc(s)}</td>`;
    };
    // Combined start–end time cell.
    const _cellTimeRange = (r) =>
      `<td style="padding:9px 10px;width:1%;min-width:150px;font-size:0.8rem;color:#cbd5e1;white-space:nowrap;vertical-align:top;">${esc(_timeRange(r))}</td>`;
    const _cellArtist = (r, past) => {
      const openColor = past ? '#94a3b8' : '#22c55e';
      let inner;
      if (r.artist_name) {
        inner = r.artist_id
          ? `<a href="/app/artist-profile.html?artist_id=${r.artist_id}" target="_blank" rel="noopener" style="color:${openColor};text-decoration:none;font-weight:600;border-bottom:1px solid rgba(34,197,94,0.35);">${esc(r.artist_name)}</a>`
          : `<span style="color:${openColor};font-weight:600;">${esc(r.artist_name)}</span>`;
      } else {
        inner = `<span style="color:#64748b;">—</span>`;
      }
      return `<td style="padding:9px 10px;font-size:0.82rem;vertical-align:top;white-space:nowrap;">${inner}</td>`;
    };
    const _cellTime = (r, key) =>
      `<td style="padding:9px 6px;width:1%;font-size:0.8rem;color:#cbd5e1;white-space:nowrap;vertical-align:top;">${esc(fmtTime(r[key === 'start' ? 'start_time' : 'end_time']))}</td>`;

    const _cellFor = (key, r, past, dateColor) => {
      switch (key) {
        case 'date':    return _cellDate(r, past, dateColor);
        case 'venue':   return _cellVenue(r, past);
        case 'city':    return _cellCity(r);
        case 'address': return _cellAddress(r);
        case 'artist':  return _cellArtist(r, past);
        case 'start':   return _cellTime(r, 'start');
        case 'end':     return _cellTime(r, 'end');
        case 'time':    return _cellTimeRange(r);
        default:        return '<td></td>';
      }
    };

    let sawPast = false;
    const rowsHtml = STATE.rows.map(r => {
      const past = isPast(r);
      let splitHeader = '';
      if (past && !sawPast) {
        sawPast = true;
        splitHeader = pastDividerHtml;
      }
      if (past && !STATE.showPast) return splitHeader;
      const dateColor = past ? '#94a3b8' : '#e2e8f0';
      const cellOpacity = past ? 'opacity:0.78;' : '';
      return `${splitHeader}
        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);${cellOpacity}">
          ${cols.map(k => _cellFor(k, r, past, dateColor)).join('')}
        </tr>`;
    }).join('');

    const theadHtml = cols.map(k => th(k, _COLUMN_LABELS[k] || k)).join('');

    body.innerHTML = `
      <div style="overflow:auto;max-height:calc(88vh - 140px);border:1px solid #2a3040;border-radius:8px;">
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr>${theadHtml}</tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
      <div style="margin-top:12px;font-size:0.75rem;color:#64748b;text-align:right;">
        ${STATE.rows.length} ${STATE.rows.length === 1 ? 'gig' : 'gigs'} total${pastCount > 0 && !STATE.showPast ? ` · ${pastCount} past hidden` : ''}
      </div>`;
  }

  // ---------- Export: CSV ----------
  function exportCsv() {
    if (!STATE.rows.length) return;
    sortRows();
    const withArtist = STATE.rows.some(r => r.artist_name);
    const headers = ['Date', 'Venue', 'City', 'State', 'Start', 'End'];
    if (withArtist) headers.push('Artist');
    headers.push('Title', 'Slot');
    const escCsv = (s) => {
      const v = String(s == null ? '' : s);
      return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
    };
    const lines = [headers.join(',')];
    STATE.rows.forEach(r => {
      const row = [
        fmtDate(r.date), r.venue_name || '', r.city || '', r.state || '',
        fmtTime(r.start_time), fmtTime(r.end_time),
      ];
      if (withArtist) row.push(r.artist_name || '');
      row.push(r.title || '', r.is_multi_slot ? `Slot ${r.slot_number || ''}` : '');
      lines.push(row.map(escCsv).join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${STATE.exportBasename || 'gigs'}.csv`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  // ---------- Export: PDF ----------
  function exportPdf() {
    if (!STATE.rows.length) return;
    sortRows();
    const today = (new Date()).toLocaleDateString();
    const withArtist = STATE.rows.some(r => r.artist_name);
    const rowsHtml = STATE.rows.map(r => `
      <tr>
        <td>${esc(fmtDate(r.date))}</td>
        <td>${esc(r.venue_name || '')}${r.is_multi_slot ? ` <span class="slot">Slot ${esc(r.slot_number)}</span>` : ''}${withArtist && r.artist_name ? `<div class="artist">🎤 ${esc(r.artist_name)}</div>` : ''}${r.title ? `<div class="title">${esc(r.title)}</div>` : ''}</td>
        <td>${esc(_cityState(r))}</td>
        <td>${esc(fmtTime(r.start_time))}</td>
        <td>${esc(fmtTime(r.end_time))}</td>
      </tr>`).join('');
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<title>${esc(STATE.title)} — GigsFill</title>
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
  .slot { display: inline-block; padding: 1px 5px; font-size: 9px; background: #ede9fe; color: #6d28d9; border-radius: 3px; margin-left: 4px; }
  .title { font-size: 10px; color: #6b7280; font-style: italic; margin-top: 1px; }
  .artist { font-size: 10px; color: #059669; font-weight: 600; margin-top: 1px; }
  .print-btn { position: fixed; top: 10px; right: 10px; padding: 8px 16px; background: #2563eb; color: white; border: none; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; }
  @media print { .print-btn { display: none; } }
</style></head><body>
  <button class="print-btn" onclick="window.print()">Print / Save as PDF</button>
  <div class="hdr">
    <h1>${esc((STATE.title || '').replace(/^[^\w]+/, ''))}</h1>
    <div class="meta">Generated ${esc(today)} · ${STATE.rows.length} ${STATE.rows.length === 1 ? 'gig' : 'gigs'}</div>
  </div>
  <table>
    <thead><tr>
      <th>Date</th><th>Venue</th><th>City / State</th><th>Start</th><th>End</th>
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
    w.document.open(); w.document.write(html); w.document.close();
  }

  function close() {
    const o = document.getElementById('publicGigsListOverlay');
    if (o) o.remove();
  }

  function open(config) {
    STATE.rows = Array.isArray(config.rows) ? config.rows.slice() : [];
    STATE.title = config.title || '📅 All Gigs';
    STATE.exportBasename = config.exportBasename || 'gigs';
    // Filter caller-supplied columns down to the known set so a typo
    // can't break render. Empty / missing → fall back to default.
    if (Array.isArray(config.columns) && config.columns.length) {
      STATE.columns = config.columns.filter(k => k in _COLUMN_LABELS);
      if (!STATE.columns.length) STATE.columns = _DEFAULT_COLUMNS.slice();
    } else {
      STATE.columns = _DEFAULT_COLUMNS.slice();
    }
    STATE.sortCol = 'date';
    STATE.sortDir = 1;
    STATE.showPast = false;  // collapsed by default on each open

    let overlay = document.getElementById('publicGigsListOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'publicGigsListOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,0.72);display:flex;align-items:center;justify-content:center;padding:20px;';
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    overlay.innerHTML = `
      <div style="background:#1a1f2e;border:1px solid #2a3040;border-radius:14px;width:100%;max-width:1100px;max-height:92vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.55);">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 22px;border-bottom:1px solid #2a3040;flex-wrap:wrap;gap:10px;">
          <div style="display:flex;align-items:baseline;gap:12px;">
            <div style="font-size:1.05rem;font-weight:700;color:#e2e8f0;">${esc(STATE.title)}</div>
            <div style="font-size:0.75rem;color:#94a3b8;">Chronological · closest first</div>
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <button onclick="window._publicGigsListExportCsv()" style="font-size:0.75rem;color:#94a3b8;background:transparent;padding:4px 10px;border:1px solid rgba(255,255,255,0.12);border-radius:6px;cursor:pointer;" onmouseover="this.style.borderColor='rgba(34,197,94,0.4)';this.style.color='#22c55e'" onmouseout="this.style.borderColor='rgba(255,255,255,0.12)';this.style.color='#94a3b8'">📊 Export to Excel (.csv)</button>
            <button onclick="window._publicGigsListExportPdf()" style="font-size:0.75rem;color:#94a3b8;background:transparent;padding:4px 10px;border:1px solid rgba(255,255,255,0.12);border-radius:6px;cursor:pointer;" onmouseover="this.style.borderColor='rgba(6,182,212,0.4)';this.style.color='#06b6d4'" onmouseout="this.style.borderColor='rgba(255,255,255,0.12)';this.style.color='#94a3b8'">📄 Export to PDF</button>
            <button onclick="window._publicGigsListClose()" style="background:transparent;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;line-height:1;padding:0 6px;" title="Close">✕</button>
          </div>
        </div>
        <div id="publicGigsListBody" style="overflow:auto;padding:14px 18px 18px;flex:1;">
        </div>
      </div>`;
    document.body.appendChild(overlay);
    render();
  }

  function togglePast() {
    STATE.showPast = !STATE.showPast;
    render();
  }

  window.openPublicGigsListModal = open;
  window._publicGigsListClose     = close;
  window._publicGigsListSort      = setSort;
  window._publicGigsListExportCsv = exportCsv;
  window._publicGigsListExportPdf = exportPdf;
  window._publicGigsListTogglePast = togglePast;
})();
