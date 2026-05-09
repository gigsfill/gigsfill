// admin-affiliate.js — Admin Affiliates tab logic

let _affAccountingData = [];
let _affAccountingPage = 1;
const AFF_PER_PAGE = 20;
let _affSelectedVenueId = null;

// ── Tab Entry ─────────────────────────────────────────────────────────────────

async function loadAffiliatesTab() {
  switchAffTab('accounting');  // default to accounting tab
  await Promise.all([loadAffAccounting(), loadAffSettings()]);
}

// ── Subtab Switching ──────────────────────────────────────────────────────────

function switchAffTab(name) {
  document.querySelectorAll('.ps-subtab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.ps-subtab-content').forEach(c => c.classList.remove('active'));
  const btn = document.querySelector(`.ps-subtab[onclick*="${name}"]`);
  if (btn) btn.classList.add('active');
  const panel = document.getElementById(`aff-${name}`);
  if (panel) panel.classList.add('active');
}

// ── Accounting ────────────────────────────────────────────────────────────────

async function loadAffAccounting() {
  const tbl = document.getElementById('affAccountingTable');
  if (!tbl) return;

  try {
    const r = await fetch('/api/admin/affiliate/accounting', { credentials: 'include' });
    if (!r.ok) { renderAffTable([]); return; }
    _affAccountingData = await r.json();
  } catch(e) {
    renderAffTable([]);
    return;
  }

  _affAccountingPage = 1;
  // Clear expand cache so stale pre-payout data doesn't show after a payout run
  Object.keys(_affExpandCache).forEach(k => delete _affExpandCache[k]);

  // Summary bubbles
  const statsEl = document.getElementById('affAdminStats');
  if (statsEl) {
    const totalEarned    = _affAccountingData.reduce((s, a) => s + (a.total_earned_cents || 0), 0);
    const totalUnpaid    = _affAccountingData.reduce((s, a) => s + (a.unpaid_cents || 0), 0);
    const totalAffiliates = _affAccountingData.length;
    const totalVenues    = _affAccountingData.reduce((s, a) => s + (a.venue_count || 0), 0);
    statsEl.innerHTML = [
      ['Affiliates',    totalAffiliates,                          '#8b5cf6'],
      ['Linked Venues', totalVenues,                              '#06b6d4'],
      ['Total Earned',  '$' + (totalEarned / 100).toFixed(2),    '#10b981'],
      ['Unpaid',        '$' + (totalUnpaid / 100).toFixed(2),    '#f59e0b'],
    ].map(([label, val, color]) => `
      <div style="background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:8px;padding:12px 16px;flex:1;min-width:120px;">
        <div style="font-size:1.1rem;font-weight:700;color:${color};">${val}</div>
        <div style="font-size:0.7rem;color:var(--text-gray);margin-top:2px;">${label}</div>
      </div>`).join('');
    statsEl.style.display = 'flex';
    statsEl.style.gap = '8px';
    statsEl.style.flexWrap = 'wrap';
  }

  renderAffTable(_affAccountingData);
}

function renderAffTable(data) {
  const tbl = document.getElementById('affAccountingTable');
  const pagEl = document.getElementById('affAccountingPagination');
  if (!tbl) return;

  const totalPages = Math.max(1, Math.ceil(data.length / AFF_PER_PAGE));
  if (_affAccountingPage > totalPages) _affAccountingPage = totalPages;
  const slice = data.slice((_affAccountingPage - 1) * AFF_PER_PAGE, _affAccountingPage * AFF_PER_PAGE);

  // Always render header even if empty
  const tbody = slice.length
    ? slice.map(a => `
      <tr id="aff-row-${a.user_id}" style="border-bottom:1px solid rgba(255,255,255,0.04);">
        <td style="padding:8px 10px;color:var(--text);">
          <span onclick="toggleAffExpand(${a.user_id})" style="cursor:pointer;display:inline-flex;align-items:center;gap:5px;">
            <span id="aff-arrow-${a.user_id}" style="font-size:0.7rem;color:var(--cyan);transition:transform .2s;">▶</span>
            <span style="font-weight:500;">${esc(((a.first_name||'')+' '+(a.last_name||'')).trim()) || '—'}</span>
          </span>
          <div style="font-size:0.68rem;color:var(--text-gray);padding-left:16px;">${esc(a.email)}</div>
        </td>
        <td style="padding:8px 10px;font-family:monospace;font-size:0.72rem;color:#c4b5fd;">${esc(a.affiliate_code||'—')}</td>
        <td style="padding:8px 4px;text-align:center;color:var(--text);">${a.venue_count||0}</td>
        <td style="padding:8px 4px;text-align:center;color:var(--text);">${a.total_gigs||0}</td>
        <td style="padding:8px 10px;text-align:right;color:#10b981;font-weight:600;">$${((a.total_earned_cents||0)/100).toFixed(2)}</td>
        <td style="padding:8px 10px;text-align:right;color:${(a.unpaid_cents||0)>0?'#f59e0b':'var(--text-gray)'};">$${((a.unpaid_cents||0)/100).toFixed(2)}</td>
        <td style="padding:8px 10px;text-align:right;color:var(--text);">$${((a.ytd_cents||0)/100).toFixed(2)}</td>
        <td style="padding:8px 6px;"></td>
      </tr>
      <tr id="aff-expand-${a.user_id}" style="display:none;">
        <td colspan="8" style="padding:0 0 8px 16px;">
          <div id="aff-expand-content-${a.user_id}" style="background:rgba(6,182,212,0.04);border:1px solid rgba(6,182,212,0.15);border-radius:6px;padding:10px 12px;">
            <div style="color:var(--text-gray);font-size:0.72rem;">Loading…</div>
          </div>
        </td>
      </tr>`).join('')
    : `<tr><td colspan="8" style="padding:32px;text-align:center;color:var(--text-gray);font-size:0.8rem;">No affiliates yet</td></tr>`;

  tbl.innerHTML = `
  <table style="width:100%;border-collapse:collapse;font-size:0.78rem;">
    <thead>
      <tr style="border-bottom:1px solid var(--border);">
        <th style="padding:8px 10px;text-align:left;color:var(--text-gray);font-weight:600;font-size:0.7rem;text-transform:uppercase;">User</th>
        <th style="padding:8px 10px;text-align:left;color:var(--text-gray);font-weight:600;font-size:0.7rem;text-transform:uppercase;">Code</th>
        <th style="padding:8px 4px;text-align:center;color:var(--text-gray);font-weight:600;font-size:0.7rem;text-transform:uppercase;">Venues</th>
        <th style="padding:8px 4px;text-align:center;color:var(--text-gray);font-weight:600;font-size:0.7rem;text-transform:uppercase;">Gigs</th>
        <th style="padding:8px 10px;text-align:right;color:var(--text-gray);font-weight:600;font-size:0.7rem;text-transform:uppercase;">Total Earned</th>
        <th style="padding:8px 10px;text-align:right;color:var(--text-gray);font-weight:600;font-size:0.7rem;text-transform:uppercase;">Unpaid</th>
        <th style="padding:8px 10px;text-align:right;color:var(--text-gray);font-weight:600;font-size:0.7rem;text-transform:uppercase;">YTD</th>
        <th style="padding:8px 6px;"></th>
      </tr>
    </thead>
    <tbody>${tbody}</tbody>
  </table>`;

  // Pagination
  if (pagEl) {
    const bs = 'background:rgba(255,255,255,0.05);border:1px solid var(--border);color:var(--text);padding:4px 10px;border-radius:4px;font-size:0.75rem;cursor:pointer;';
    const ds = bs + 'opacity:0.3;cursor:default;';
    pagEl.innerHTML = data.length > AFF_PER_PAGE ? `
      <span style="font-size:0.75rem;color:var(--text-gray);">Page ${_affAccountingPage} of ${totalPages} (${data.length} total)</span>
      <button onclick="affGoPage(${_affAccountingPage-1})" style="${_affAccountingPage<=1?ds:bs}" ${_affAccountingPage<=1?'disabled':''}>◀ Prev</button>
      <button onclick="affGoPage(${_affAccountingPage+1})" style="${_affAccountingPage>=totalPages?ds:bs}" ${_affAccountingPage>=totalPages?'disabled':''}>Next ▶</button>` : '';
  }
}

function affGoPage(p) { _affAccountingPage = p; renderAffTable(_affAccountingData); }

// ── Export ────────────────────────────────────────────────────────────────────

function toggleAffExportMenu() {
  const m = document.getElementById('affExportMenu');
  if (m) m.style.display = m.style.display === 'none' ? 'block' : 'none';
  // Close on outside click
  if (m && m.style.display === 'block') {
    setTimeout(() => document.addEventListener('click', _closeAffExportMenu, { once: true }), 10);
  }
}
function _closeAffExportMenu() {
  const m = document.getElementById('affExportMenu');
  if (m) m.style.display = 'none';
}

function affExport(fmt) {
  _closeAffExportMenu();
  const data = _affAccountingData;
  if (!data.length) { window._adminToast('No affiliate data to export', 'rgba(245,158,11,0.8)'); return; }

  const headers = ['Name', 'Email', 'Affiliate Code', 'Linked Venues', 'Total Gigs', 'Total Earned ($)', 'Unpaid ($)', 'YTD ($)', 'Last Earning'];
  const rows = data.map(a => [
    ((a.first_name||'') + ' ' + (a.last_name||'')).trim() || '',
    a.email || '',
    a.affiliate_code || '',
    a.venue_count || 0,
    a.total_gigs || 0,
    ((a.total_earned_cents||0)/100).toFixed(2),
    ((a.unpaid_cents||0)/100).toFixed(2),
    ((a.ytd_cents||0)/100).toFixed(2),
    a.last_earning_at ? new Date(a.last_earning_at).toLocaleDateString() : '',
  ]);

  if (fmt === 'csv') {
    let csv = headers.join(',') + '\n';
    rows.forEach(r => { csv += r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',') + '\n'; });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = `gigsfill_affiliates_${new Date().toISOString().slice(0,10)}.csv`; a.click();
  } else {
    const w = window.open('', '_blank');
    w.document.write('<html><head><title>GigsFill Affiliate Report</title><style>body{font-family:Arial,sans-serif;padding:20px;font-size:12px;}table{width:100%;border-collapse:collapse;margin-top:12px;}th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;}th{background:#f4f4f4;font-weight:bold;font-size:11px;}tr:nth-child(even){background:#fafafa;}.r{text-align:right;}</style></head><body>');
    w.document.write(`<h2>GigsFill Affiliate Report</h2><p>Exported: ${new Date().toLocaleDateString()} | ${data.length} affiliates</p>`);
    w.document.write('<table><tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>');
    rows.forEach(r => { w.document.write('<tr>' + r.map((v,i) => `<td${i>=4?' class="r"':''}>${v}</td>`).join('') + '</tr>'); });
    // Totals
    const totEarned = data.reduce((s,a)=>s+(a.total_earned_cents||0),0);
    const totUnpaid = data.reduce((s,a)=>s+(a.unpaid_cents||0),0);
    const totYtd    = data.reduce((s,a)=>s+(a.ytd_cents||0),0);
    w.document.write(`<tr style="font-weight:bold;background:#e8e8e8;"><td colspan="5" style="text-align:right;">TOTALS:</td><td class="r">$${(totEarned/100).toFixed(2)}</td><td class="r">$${(totUnpaid/100).toFixed(2)}</td><td class="r">$${(totYtd/100).toFixed(2)}</td><td></td></tr>`);
    w.document.write('</table></body></html>');
    w.document.close();
    w.print();
  }
}

// ── Inline expand toggle ──────────────────────────────────────────────────────

const _affExpandCache = {};

async function toggleAffExpand(userId) {
  const expandRow = document.getElementById(`aff-expand-${userId}`);
  const arrow = document.getElementById(`aff-arrow-${userId}`);
  if (!expandRow) return;

  const isOpen = expandRow.style.display !== 'none';
  if (isOpen) {
    expandRow.style.display = 'none';
    if (arrow) arrow.style.transform = '';
    return;
  }

  expandRow.style.display = '';
  if (arrow) arrow.style.transform = 'rotate(90deg)';

  if (_affExpandCache[userId]) {
    renderAffExpandContent(userId, _affExpandCache[userId]);
    return;
  }

  try {
    const r = await fetch(`/api/admin/affiliate/accounting/${userId}`, { credentials: 'include' });
    const d = await r.json();
    _affExpandCache[userId] = d;
    renderAffExpandContent(userId, d);
  } catch(e) {
    const el = document.getElementById(`aff-expand-content-${userId}`);
    if (el) el.innerHTML = '<div style="color:#ef4444;font-size:0.75rem;">Error loading details</div>';
  }
}

function renderAffExpandContent(userId, d) {
  const el = document.getElementById(`aff-expand-content-${userId}`);
  if (!el) return;

  const venueRows = d.venues.map(v => `
    <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
      <td style="padding:5px 8px;color:var(--text);font-size:0.73rem;padding-left:20px;">${esc(v.venue_name)}<span style="color:var(--text-gray);font-size:0.67rem;margin-left:6px;">${esc(v.city||'')}${v.state?', '+esc(v.state):''}</span></td>
      <td style="padding:5px 8px;text-align:center;font-size:0.7rem;color:var(--text-gray);">${esc(v.link_method||'—')}</td>
      <td style="padding:5px 4px;text-align:center;font-size:0.73rem;">${v.gig_count||0}</td>
      <td style="padding:5px 8px;text-align:right;font-size:0.73rem;color:#10b981;">$${((v.total_earned_cents||0)/100).toFixed(2)}</td>
      <td style="padding:5px 8px;text-align:right;font-size:0.7rem;color:#f59e0b;">$${((v.unpaid_cents||0)/100).toFixed(2)}</td>
      <td style="padding:5px 4px;text-align:right;">
        <button onclick="confirmDeleteReferral(${v.referral_id})"
          style="padding:2px 7px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);border-radius:4px;color:#f87171;font-size:0.65rem;cursor:pointer;">Unlink</button>
      </td>
    </tr>`).join('');

  el.innerHTML = `
    <table style="width:100%;border-collapse:collapse;">
      <thead><tr style="border-bottom:1px solid var(--border);">
        <th style="padding:4px 8px 4px 20px;text-align:left;font-size:0.67rem;color:var(--text-gray);">Venue</th>
        <th style="padding:4px 8px;text-align:center;font-size:0.67rem;color:var(--text-gray);">Method</th>
        <th style="padding:4px 4px;text-align:center;font-size:0.67rem;color:var(--text-gray);">Gigs</th>
        <th style="padding:4px 8px;text-align:right;font-size:0.67rem;color:var(--text-gray);">Earned</th>
        <th style="padding:4px 8px;text-align:right;font-size:0.67rem;color:var(--text-gray);">Unpaid</th>
        <th></th>
      </tr></thead>
      <tbody>${venueRows || '<tr><td colspan="6" style="padding:10px 8px;color:var(--text-gray);font-size:0.73rem;text-align:center;">No linked venues</td></tr>'}</tbody>
    </table>`;
}

// ── Detail drilldown (legacy panel — kept for payout detail) ──────────────────

async function loadAffDetail(userId, label) {
  const panel = document.getElementById('affDetailPanel');
  const title = document.getElementById('affDetailTitle');
  const content = document.getElementById('affDetailContent');
  if (!panel) return;

  title.textContent = label || `Affiliate #${userId}`;
  content.innerHTML = '<div style="color:var(--text-gray);padding:12px 0;font-size:0.78rem;">Loading…</div>';
  panel.style.display = 'block';

  try {
    const r = await fetch(`/api/admin/affiliate/accounting/${userId}`, { credentials: 'include' });
    const d = await r.json();

    const venueRows = d.venues.map(v => `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
        <td style="padding:6px 8px;color:var(--text);font-size:0.75rem;">${esc(v.venue_name)}<div style="font-size:0.68rem;color:var(--text-gray);">${esc(v.city||'')}${v.state?', '+esc(v.state):''}</div></td>
        <td style="padding:6px 8px;text-align:center;font-size:0.72rem;color:var(--text-gray);">${esc(v.link_method||'—')}</td>
        <td style="padding:6px 4px;text-align:center;font-size:0.75rem;">${v.gig_count||0}</td>
        <td style="padding:6px 8px;text-align:right;font-size:0.75rem;color:#10b981;">$${((v.total_earned_cents||0)/100).toFixed(2)}</td>
        <td style="padding:6px 8px;text-align:right;font-size:0.72rem;color:#f59e0b;">$${((v.unpaid_cents||0)/100).toFixed(2)}</td>
        <td style="padding:6px 4px;text-align:right;">
          <button onclick="confirmDeleteReferral(${v.referral_id})"
            style="padding:2px 8px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);border-radius:4px;color:#f87171;font-size:0.65rem;cursor:pointer;">Unlink</button>
        </td>
      </tr>`).join('');

    const payoutRows = d.payouts.map(p => `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
        <td style="padding:5px 8px;font-size:0.73rem;color:var(--text);">${esc(p.quarter)}</td>
        <td style="padding:5px 8px;text-align:right;font-size:0.73rem;color:#10b981;">$${((p.total_cents||0)/100).toFixed(2)}</td>
        <td style="padding:5px 8px;text-align:center;">
          <span style="padding:2px 7px;border-radius:10px;font-size:0.65rem;font-weight:700;background:${statusColor(p.status)};color:#fff;">${esc(p.status)}</span>
        </td>
        <td style="padding:5px 8px;font-size:0.68rem;color:var(--text-gray);">${p.paid_at ? new Date(p.paid_at).toLocaleDateString() : '—'}</td>
      </tr>`).join('');

    content.innerHTML = `
      <div style="margin-bottom:12px;">
        <div style="font-size:0.72rem;font-weight:700;color:var(--text-gray);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Linked Venues</div>
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr style="border-bottom:1px solid var(--border);">
            <th style="padding:5px 8px;text-align:left;font-size:0.68rem;color:var(--text-gray);">Venue</th>
            <th style="padding:5px 8px;text-align:center;font-size:0.68rem;color:var(--text-gray);">Method</th>
            <th style="padding:5px 4px;text-align:center;font-size:0.68rem;color:var(--text-gray);">Gigs</th>
            <th style="padding:5px 8px;text-align:right;font-size:0.68rem;color:var(--text-gray);">Earned</th>
            <th style="padding:5px 8px;text-align:right;font-size:0.68rem;color:var(--text-gray);">Unpaid</th>
            <th></th>
          </tr></thead>
          <tbody>${venueRows || '<tr><td colspan="6" style="padding:12px 8px;color:var(--text-gray);font-size:0.75rem;text-align:center;">No linked venues</td></tr>'}</tbody>
        </table>
      </div>
      ${d.payouts.length ? `
      <div>
        <div style="font-size:0.72rem;font-weight:700;color:var(--text-gray);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Payouts</div>
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr style="border-bottom:1px solid var(--border);">
            <th style="padding:5px 8px;text-align:left;font-size:0.68rem;color:var(--text-gray);">Quarter</th>
            <th style="padding:5px 8px;text-align:right;font-size:0.68rem;color:var(--text-gray);">Amount</th>
            <th style="padding:5px 8px;text-align:center;font-size:0.68rem;color:var(--text-gray);">Status</th>
            <th style="padding:5px 8px;font-size:0.68rem;color:var(--text-gray);">Paid</th>
          </tr></thead>
          <tbody>${payoutRows}</tbody>
        </table>
      </div>` : ''}`;
  } catch(e) {
    content.innerHTML = '<div style="color:#ef4444;font-size:0.78rem;">Error loading detail</div>';
  }
}

function statusColor(s) {
  if (!s) return '#6b7280';
  if (s === 'paid') return '#10b981';
  if (s === 'pending' || s === 'processing') return '#3b82f6';
  if (s === 'below_threshold') return '#f59e0b';
  if (s === 'no_stripe') return '#8b5cf6';
  if (s === 'transfer_failed') return '#ef4444';
  return '#6b7280';
}

// ── Venue search for manual link ──────────────────────────────────────────────

let _venueSearchTimeout = null;
function searchAffVenues(q) {
  clearTimeout(_venueSearchTimeout);
  const res = document.getElementById('affVenueResults');
  if (!q || q.length < 2) { if (res) res.innerHTML = ''; return; }
  _venueSearchTimeout = setTimeout(async () => {
    try {
      const r = await fetch(`/api/admin/affiliate/venue-search?q=${encodeURIComponent(q)}`, { credentials: 'include' });
      const venues = await r.json();
      if (!res) return;
      if (!venues.length) { res.innerHTML = '<div style="font-size:0.72rem;color:var(--text-gray);padding:4px 0;">No results</div>'; return; }
      res.innerHTML = venues.map(v => `
        <div onclick="selectAffVenue(${v.id},'${esc(v.venue_name)}')"
          style="padding:5px 8px;font-size:0.75rem;cursor:pointer;border-radius:4px;background:rgba(255,255,255,0.04);margin-bottom:2px;display:flex;justify-content:space-between;align-items:center;"
          onmouseover="this.style.background='rgba(6,182,212,0.12)'" onmouseout="this.style.background='rgba(255,255,255,0.04)'">
          <span>${esc(v.venue_name)} <span style="font-size:0.68rem;color:var(--text-gray);">${esc(v.city||'')}${v.state?', '+esc(v.state):''}</span></span>
          ${v.affiliate_user_id ? '<span style="font-size:0.65rem;color:#f59e0b;">linked</span>' : ''}
        </div>`).join('');
    } catch(e) {}
  }, 300);
}

function selectAffVenue(id, name) {
  _affSelectedVenueId = id;
  const inp = document.getElementById('affVenueSearch');
  if (inp) inp.value = name;
  const res = document.getElementById('affVenueResults');
  if (res) res.innerHTML = '';
}

async function adminManualLinkAffiliate() {
  const code = (document.getElementById('affCodeInput')?.value || '').trim().toUpperCase();
  const resultEl = document.getElementById('affLinkResult');
  if (!_affSelectedVenueId || !code) {
    if (resultEl) { resultEl.style.color='#ef4444'; resultEl.textContent='Select a venue and enter an affiliate code.'; resultEl.style.opacity='1'; }
    return;
  }
  try {
    const r = await fetch('/api/admin/affiliate/manual-link', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ venue_id: _affSelectedVenueId, affiliate_code: code })
    });
    const d = await r.json();
    if (resultEl) {
      resultEl.style.color = d.ok ? '#10b981' : '#ef4444';
      resultEl.textContent  = d.ok ? '✓ Linked' : (d.detail || 'Error');
      resultEl.style.opacity = '1';
      setTimeout(() => { resultEl.style.opacity = '0'; }, 3000);
    }
    if (d.ok) {
      _affSelectedVenueId = null;
      document.getElementById('affVenueSearch').value = '';
      document.getElementById('affCodeInput').value = '';
      await loadAffAccounting();
    }
  } catch(e) {
    if (resultEl) { resultEl.style.color='#ef4444'; resultEl.textContent='Request failed'; resultEl.style.opacity='1'; }
  }
}

async function confirmDeleteReferral(referralId) {
  const existing = document.getElementById('affDeleteRefModal');
  if (existing) existing.remove();
  const backdrop = document.createElement('div');
  backdrop.id = 'affDeleteRefModal';
  backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:10000;display:flex;align-items:center;justify-content:center;padding:20px;box-sizing:border-box;';
  backdrop.innerHTML = `
    <div style="background:#1a2235;border:1px solid var(--border);border-radius:10px;padding:28px 32px;max-width:420px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5);">
      <div style="font-size:1rem;font-weight:700;color:#ef4444;margin-bottom:12px;">🗑 Remove Affiliate Link</div>
      <p style="font-size:0.83rem;color:var(--text-gray);line-height:1.6;margin:0 0 20px;">
        Remove this affiliate referral link?<br><br>
        <span style="color:var(--text-white);">Existing earned amounts will remain in the ledger</span>, but no future earnings will accrue for this venue.
      </p>
      <div style="display:flex;gap:10px;justify-content:flex-end;">
        <button onclick="document.getElementById('affDeleteRefModal').remove()"
          style="padding:8px 20px;background:transparent;border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:var(--text-gray);font-size:0.8rem;cursor:pointer;">Cancel</button>
        <button id="affDeleteRefConfirmBtn"
          style="padding:8px 22px;background:rgba(239,68,68,0.2);border:1px solid rgba(239,68,68,0.5);border-radius:6px;color:#f87171;font-size:0.8rem;font-weight:700;cursor:pointer;">Remove Link</button>
      </div>
    </div>`;
  document.body.appendChild(backdrop);
  backdrop.addEventListener('click', e => { if (e.target === backdrop) backdrop.remove(); });
  document.getElementById('affDeleteRefConfirmBtn').onclick = async () => {
    backdrop.remove();
    try {
      const r = await fetch(`/api/admin/affiliate/referrals/${referralId}`, { method: 'DELETE', credentials: 'include' });
      const d = await r.json();
      if (d.ok) {
        document.getElementById('affDetailPanel').style.display = 'none';
        await loadAffAccounting();
      }
    } catch(e) {}
  };
}

// ── Settings ──────────────────────────────────────────────────────────────────

async function loadAffSettings() {
  try {
    const r = await fetch('/api/admin/affiliate/settings', { credentials: 'include' });
    if (!r.ok) return;
    const d = await r.json();

    const enabled = d.affiliate_enabled === 'true' || d.affiliate_enabled === '1';
    const checkbox = document.getElementById('affEnabled');
    if (checkbox) checkbox.checked = enabled;
    _updateAffToggleUI(enabled);

    const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
    set('affRatePercent',   d.affiliate_rate_percent);
    set('affReducedRate',   d.affiliate_reduced_rate_percent);
    set('affReducedDays',   d.affiliate_reduced_after_days);
    set('affMinPayout',     d.affiliate_min_payout_cents ? (parseFloat(d.affiliate_min_payout_cents) / 100).toFixed(0) : '50');
    set('aff1099Threshold', d.affiliate_1099_threshold_cents ? (parseFloat(d.affiliate_1099_threshold_cents) / 100).toFixed(0) : '600');
  } catch(e) {}
}

function _updateAffToggleUI(enabled) {
  const label = document.getElementById('affEnabledLabel');
  const track = document.getElementById('affEnabledTrack');
  const thumb = document.getElementById('affEnabledThumb');
  if (label) { label.textContent = enabled ? 'ON' : 'OFF'; label.style.color = enabled ? '#10b981' : '#ef4444'; }
  if (track) track.style.background = enabled ? '#10b981' : '#4b5563';
  if (thumb) thumb.style.left = enabled ? '19px' : '3px';
}

function toggleAffEnabled() {
  const checkbox = document.getElementById('affEnabled');
  if (checkbox) _updateAffToggleUI(checkbox.checked);
  saveAffSettings();
}

async function saveAffSettings() {
  const checkbox = document.getElementById('affEnabled');
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };

  const payload = {
    affiliate_enabled:               checkbox?.checked ? 'true' : 'false',
    affiliate_rate_percent:          get('affRatePercent'),
    affiliate_reduced_rate_percent:  get('affReducedRate'),
    affiliate_reduced_after_days:    get('affReducedDays'),
    affiliate_min_payout_cents:      String(Math.round(parseFloat(get('affMinPayout') || '50') * 100)),
    affiliate_1099_threshold_cents:  String(Math.round(parseFloat(get('aff1099Threshold') || '600') * 100)),
  };

  try {
    const r = await fetch('/api/admin/affiliate/settings', {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (r.ok) {
      const el = document.getElementById('affSettingsSaved');
      if (el) { el.style.opacity = '1'; setTimeout(() => el.style.opacity = '0', 2500); }
    }
  } catch(e) {}
}

// ── Generate Affiliate 1099s ──────────────────────────────────────────────────

function adminGenAffiliate1099s() {
  const year = new Date().getFullYear();
  const yearEl = document.getElementById('aff1099ModalYear');
  if (yearEl) yearEl.textContent = year;
  document.getElementById('aff1099Modal').classList.remove('hidden');
}

async function confirmGenAffiliate1099s() {
  document.getElementById('aff1099Modal').classList.add('hidden');
  const year = new Date().getFullYear();

  const resultEl = document.getElementById('aff1099Result');
  if (resultEl) { resultEl.style.display = 'block'; resultEl.style.color = 'var(--text-gray)'; resultEl.textContent = 'Generating…'; }

  try {
    const r = await fetch('/api/admin/affiliate/generate-1099s', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tax_year: year }),
    });
    const d = await r.json();
    if (r.ok) {
      const threshold = `$${(d.threshold_dollars || 600).toFixed(0)}`;
      if (!d.records || d.records.length === 0) {
        if (resultEl) { resultEl.style.color = '#f59e0b'; resultEl.textContent = `No affiliates exceeded the ${threshold} threshold for ${year}.`; }
        return;
      }
      const rows = d.records.map(rec => {
        const dollars = ((rec.total_earned_cents || 0) / 100).toFixed(2);
        const w9Icon = rec.has_w9 ? '✅' : '⚠️ No W-9';
        return `<div style="display:flex;align-items:center;gap:12px;padding:6px 8px;border-bottom:1px solid var(--border);font-size:0.75rem;">
          <span style="flex:2;color:var(--text);">${esc(rec.full_name)}</span>
          <span style="flex:2;color:var(--text-gray);">${esc(rec.email)}</span>
          <span style="flex:1;font-weight:600;color:#10b981;">$${dollars}</span>
          <span style="flex:1;font-size:0.7rem;">${rec.tin_last4 ? `TIN ••••${rec.tin_last4}` : '—'}</span>
          <span style="flex:1;">${w9Icon}</span>
        </div>`;
      }).join('');
      if (resultEl) {
        resultEl.style.color = 'var(--text)';
        resultEl.innerHTML = `<div style="font-weight:600;margin-bottom:8px;color:#10b981;">${d.count} affiliate(s) need 1099-NECs for ${year} (threshold: ${threshold}):</div>
          <div style="display:flex;gap:12px;padding:4px 8px;font-size:0.65rem;font-weight:700;color:var(--text-gray);text-transform:uppercase;letter-spacing:.04em;">
            <span style="flex:2;">Name</span><span style="flex:2;">Email</span><span style="flex:1;">Earnings</span><span style="flex:1;">TIN</span><span style="flex:1;">W-9</span>
          </div>${rows}
          <div style="margin-top:8px;font-size:0.72rem;color:var(--text-gray);">File 1099-NECs directly with the IRS using the information above. Affiliates without W-9s should be contacted to file before year-end.</div>`;
      }
    } else {
      if (resultEl) { resultEl.style.color = '#ef4444'; resultEl.textContent = d.detail || 'Error generating 1099s'; }
    }
  } catch(e) {
    if (resultEl) { resultEl.style.color = '#ef4444'; resultEl.textContent = `Error: ${e.message}`; }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── Full-Page Payout Preview Modal ─────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let _payoutPreviewData = null;
let _payoutSortCol = 'unpaid_cents';
let _payoutSortDir = -1; // descending

async function adminRunAffPayouts() {
  let modal = document.getElementById('affPayoutPreviewModal');
  if (!modal) {
    modal = _buildPayoutPreviewModal();
    document.body.appendChild(modal);
  }
  modal.style.display = 'flex';
  _renderPayoutPreviewLoading();
  await _loadPayoutPreviewData();
}

function _closePayoutPreviewModal() {
  const modal = document.getElementById('affPayoutPreviewModal');
  if (modal) modal.style.display = 'none';
}

function _buildPayoutPreviewModal() {
  const el = document.createElement('div');
  el.id = 'affPayoutPreviewModal';
  // Full-screen backdrop, click outside inner panel to close
  el.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:9000;align-items:flex-start;justify-content:center;overflow-y:auto;padding:24px 16px;box-sizing:border-box;';
  el.addEventListener('click', e => { if (e.target === el) _closePayoutPreviewModal(); });

  el.innerHTML = `
    <!-- Centered inner panel — matches admin page max-width 1100px -->
    <div style="width:100%;max-width:1100px;background:#0d1117;border:1px solid var(--border);border-radius:10px;display:flex;flex-direction:column;position:relative;">

      <!-- Header -->
      <div style="background:#0d1117;border-bottom:1px solid var(--border);padding:14px 20px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;">
        <div>
          <div style="font-size:0.95rem;font-weight:700;color:var(--text);">Quarterly Affiliate Payout Review</div>
          <div id="affPreviewSubtitle" style="font-size:0.73rem;color:var(--text-gray);margin-top:2px;">Loading…</div>
        </div>
        <button onclick="window._closePayoutPreviewModal()" style="padding:5px 14px;background:rgba(255,255,255,0.06);border:1px solid var(--border);border-radius:6px;color:var(--text-gray);font-size:0.78rem;cursor:pointer;">✕ Close</button>
      </div>

      <!-- Summary bubbles -->
      <div id="affPreviewBubbles" style="display:flex;gap:8px;padding:12px 20px;background:#0a0f1a;border-bottom:1px solid var(--border);flex-shrink:0;flex-wrap:wrap;"></div>

      <!-- Sort bar -->
      <div style="display:flex;align-items:center;gap:8px;padding:8px 20px;background:#0a0f1a;border-bottom:1px solid var(--border);flex-shrink:0;">
        <span style="font-size:0.7rem;color:var(--text-gray);">Sort by:</span>
        ${[
          ['unpaid_cents','Unpaid Balance'],
          ['total_gigs','# Gigs'],
          ['name','Name'],
          ['stripe','Stripe Status'],
        ].map(([col,label]) => `<button onclick="window.sortPayoutPreview('${col}')" id="ppSort_${col}"
          style="padding:3px 10px;border-radius:4px;font-size:0.72rem;cursor:pointer;border:1px solid var(--border);background:rgba(255,255,255,0.04);color:var(--text-gray);">${label}</button>`).join('')}
      </div>

      <!-- Affiliate list (not flex:1 since modal scrolls externally) -->
      <div id="affPreviewList" style="padding:12px 20px 8px;"></div>

      <!-- Bottom action bar -->
      <div style="background:#0d1117;border-top:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;flex-shrink:0;">
        <div style="font-size:0.75rem;color:var(--text-gray);">
          Only affiliates marked <span style="color:#10b981;font-weight:600;">Eligible</span> will be paid.
        </div>
        <div style="display:flex;align-items:center;gap:10px;">
          <span id="affPreviewRunResult" style="font-size:0.75rem;opacity:0;transition:opacity .3s;color:#10b981;"></span>
          <button onclick="window._closePayoutPreviewModal()" style="padding:7px 16px;background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text-gray);font-size:0.78rem;cursor:pointer;">Cancel</button>
          <button id="affPreviewRunBtn" onclick="window._showPayoutConfirm()"
            style="padding:8px 20px;background:rgba(245,158,11,0.2);border:1px solid rgba(245,158,11,0.5);border-radius:6px;color:#fbbf24;font-size:0.82rem;font-weight:700;cursor:pointer;">
            ▶ Run Quarterly Payouts Now
          </button>
        </div>
      </div>

    </div>`;

  // Confirm overlay appended to body so it's never clipped
  const confirm = document.createElement('div');
  confirm.id = 'affPayoutInnerConfirm';
  confirm.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:10000;align-items:center;justify-content:center;';
  confirm.innerHTML = `
    <div style="background:#1a2235;border:1px solid var(--border);border-radius:10px;padding:28px 32px;max-width:420px;width:90%;text-align:center;">
      <div style="font-size:1rem;font-weight:700;color:var(--text);margin-bottom:10px;">Confirm Payout Run</div>
      <p id="affConfirmDesc" style="font-size:0.83rem;color:var(--text-gray);line-height:1.6;margin:0 0 16px;"></p>
      <p style="font-size:0.78rem;color:#f59e0b;margin:0 0 20px;">⚠️ This sends real Stripe transfers. Cannot be undone.</p>
      <div style="display:flex;gap:12px;justify-content:center;">
        <button onclick="document.getElementById('affPayoutInnerConfirm').style.display='none'"
          style="padding:8px 18px;background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text-gray);font-size:0.8rem;cursor:pointer;">
          Go Back
        </button>
        <button onclick="window._doRunAffPayoutsFromPreview()"
          style="padding:8px 22px;background:rgba(245,158,11,0.25);border:1px solid rgba(245,158,11,0.6);border-radius:6px;color:#fbbf24;font-size:0.8rem;font-weight:700;cursor:pointer;">
          ✓ Yes, Run Payouts
        </button>
      </div>
    </div>`;
  document.body.appendChild(confirm);

  return el;
}

function _renderPayoutPreviewLoading() {
  const list = document.getElementById('affPreviewList');
  if (list) list.innerHTML = '<div style="text-align:center;padding:48px;color:var(--text-gray);font-size:0.9rem;">Loading affiliate data…</div>';
  const bubbles = document.getElementById('affPreviewBubbles');
  if (bubbles) bubbles.innerHTML = '';
}

async function _loadPayoutPreviewData() {
  try {
    const r = await fetch('/api/admin/affiliate/payout-preview', { credentials: 'include' });
    if (!r.ok) throw new Error('Failed to load');
    _payoutPreviewData = await r.json();
  } catch(e) {
    const list = document.getElementById('affPreviewList');
    if (list) list.innerHTML = `<div style="text-align:center;padding:48px;color:#ef4444;font-size:0.85rem;">Error loading payout data: ${e.message}</div>`;
    return;
  }
  _renderPayoutPreview();
}

function sortPayoutPreview(col) {
  if (_payoutSortCol === col) {
    _payoutSortDir *= -1;
  } else {
    _payoutSortCol = col;
    _payoutSortDir = col === 'name' ? 1 : -1;
  }
  _renderPayoutPreview();
}

function _renderPayoutPreview() {
  if (!_payoutPreviewData) return;
  const d = _payoutPreviewData;

  // Update subtitle
  const subtitle = document.getElementById('affPreviewSubtitle');
  if (subtitle) subtitle.textContent = `Quarter: ${d.quarter} · ${d.eligible_count} eligible · $${(d.eligible_total_cents/100).toFixed(2)} to be paid`;

  // Summary bubbles
  const bubbles = document.getElementById('affPreviewBubbles');
  if (bubbles) {
    const totalAff = d.affiliates.length;
    const ineligible = totalAff - d.eligible_count;
    bubbles.innerHTML = [
      ['Total Affiliates',    totalAff,                                         '#8b5cf6'],
      ['Eligible for Payout', d.eligible_count,                                 '#10b981'],
      ['Below Minimum',       ineligible,                                       '#f59e0b'],
      ['Total Payout',        '$' + (d.eligible_total_cents/100).toFixed(2),   '#06b6d4'],
      ['Min Threshold',       '$' + (d.min_payout_cents/100).toFixed(2),       '#6b7280'],
    ].map(([label,val,color]) => `
      <div style="background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:8px;padding:10px 16px;min-width:110px;">
        <div style="font-size:1rem;font-weight:700;color:${color};">${val}</div>
        <div style="font-size:0.68rem;color:var(--text-gray);margin-top:2px;">${label}</div>
      </div>`).join('');
  }

  // Update sort button highlights
  ['unpaid_cents','total_gigs','name','stripe'].forEach(col => {
    const btn = document.getElementById(`ppSort_${col}`);
    if (btn) {
      const active = col === _payoutSortCol;
      btn.style.background = active ? 'rgba(6,182,212,0.15)' : 'rgba(255,255,255,0.04)';
      btn.style.borderColor = active ? 'rgba(6,182,212,0.4)' : 'var(--border)';
      btn.style.color = active ? 'var(--cyan)' : 'var(--text-gray)';
    }
  });

  // Sort affiliates
  const sorted = [...d.affiliates].sort((a, b) => {
    if (_payoutSortCol === 'name') {
      const an = ((a.first_name||'')+(a.last_name||'')).toLowerCase();
      const bn = ((b.first_name||'')+(b.last_name||'')).toLowerCase();
      return an < bn ? -_payoutSortDir : an > bn ? _payoutSortDir : 0;
    }
    if (_payoutSortCol === 'stripe') {
      return (b.has_stripe ? 1 : 0) - (a.has_stripe ? 1 : 0);
    }
    return (_payoutSortDir) * ((b[_payoutSortCol]||0) - (a[_payoutSortCol]||0));
  });

  const list = document.getElementById('affPreviewList');
  if (!list) return;

  if (!sorted.length) {
    list.innerHTML = '<div style="text-align:center;padding:48px;color:var(--text-gray);font-size:0.85rem;">No affiliates with pending balances.</div>';
    const btn = document.getElementById('affPreviewRunBtn');
    if (btn) { btn.disabled = true; btn.style.opacity = '0.4'; }
    return;
  }

  list.innerHTML = sorted.map(aff => {
    const name = ((aff.first_name||'') + ' ' + (aff.last_name||'')).trim() || aff.email;
    const eligible = aff.eligible;
    const hasStripe = aff.has_stripe;
    const borderColor = eligible ? 'rgba(16,185,129,0.25)' : 'rgba(245,158,11,0.15)';
    const bgColor = eligible ? 'rgba(16,185,129,0.04)' : 'rgba(255,255,255,0.02)';

    // Status badge
    let statusBadge;
    if (eligible && hasStripe) {
      statusBadge = '<span style="padding:3px 9px;border-radius:10px;background:rgba(16,185,129,0.15);color:#10b981;font-size:0.68rem;font-weight:700;">✓ Eligible</span>';
    } else if (eligible && !hasStripe) {
      statusBadge = '<span style="padding:3px 9px;border-radius:10px;background:rgba(139,92,246,0.15);color:#c4b5fd;font-size:0.68rem;font-weight:700;">⚠ No Stripe</span>';
    } else {
      statusBadge = `<span style="padding:3px 9px;border-radius:10px;background:rgba(245,158,11,0.12);color:#f59e0b;font-size:0.68rem;">Below $${(d.min_payout_cents/100).toFixed(0)} min</span>`;
    }

    // Venue rows
    const venueRows = aff.venues.map(v => {
      const linkedDays = v.linked_at ? Math.floor((Date.now() - new Date(v.linked_at)) / 86400000) : 0;
      const curRate = linkedDays >= (v.reduced_after_days || 365) ? v.reduced_rate_percent : v.initial_rate_percent;
      return `<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
        <td style="padding:5px 10px 5px 32px;color:var(--text);font-size:0.75rem;">
          ${esc(v.venue_name)}
          <span style="font-size:0.67rem;color:var(--text-gray);margin-left:6px;">${esc(v.city||'')}${v.state?', '+esc(v.state):''}</span>
        </td>
        <td style="padding:5px 10px;text-align:center;font-size:0.73rem;color:var(--text);">${v.gig_count||0}</td>
        <td style="padding:5px 10px;text-align:right;font-size:0.73rem;color:var(--text-gray);">$${((v.total_gig_fees_cents||0)/100).toFixed(2)}</td>
        <td style="padding:5px 10px;text-align:right;font-size:0.73rem;color:#10b981;font-weight:600;">$${((v.all_time_earned_cents||0)/100).toFixed(2)}</td>
        <td style="padding:5px 10px;text-align:right;font-size:0.73rem;color:#f59e0b;font-weight:600;">$${((v.unpaid_venue_cents||0)/100).toFixed(2)}</td>
        <td style="padding:5px 10px;text-align:center;font-size:0.68rem;color:var(--text-gray);">${curRate}%</td>
      </tr>`;
    }).join('');

    return `
    <div style="margin-bottom:10px;border:1px solid ${borderColor};border-radius:8px;background:${bgColor};overflow:hidden;">
      <!-- Affiliate header row -->
      <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;gap:10px;flex-wrap:wrap;border-bottom:1px solid rgba(255,255,255,0.04);">
        <div style="display:flex;align-items:center;gap:10px;min-width:0;">
          ${statusBadge}
          <div style="min-width:0;">
            <span style="font-size:0.85rem;font-weight:700;color:var(--text);">${esc(name)}</span>
            <span style="font-size:0.72rem;color:var(--text-gray);margin-left:8px;">${esc(aff.email)}</span>
            <span style="font-family:monospace;font-size:0.7rem;color:#c4b5fd;margin-left:8px;">${esc(aff.affiliate_code||'')}</span>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:16px;flex-shrink:0;">
          <div style="text-align:right;">
            <div style="font-size:0.68rem;color:var(--text-gray);">Total Gigs</div>
            <div style="font-size:0.85rem;font-weight:700;color:var(--text);">${aff.total_gigs||0}</div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:0.68rem;color:var(--text-gray);">Unpaid Balance</div>
            <div style="font-size:0.95rem;font-weight:700;color:${eligible?'#10b981':'#f59e0b'};">$${(aff.unpaid_cents/100).toFixed(2)}</div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:0.68rem;color:var(--text-gray);">Stripe</div>
            <div style="font-size:0.78rem;font-weight:600;color:${hasStripe?'#10b981':'#8b5cf6'};">${hasStripe?'✅ Connected':'⚠ Not set up'}</div>
          </div>
        </div>
      </div>
      <!-- Venue breakdown table -->
      ${aff.venues.length ? `
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:rgba(255,255,255,0.02);">
            <th style="padding:5px 10px 5px 32px;text-align:left;font-size:0.65rem;color:var(--text-gray);font-weight:600;text-transform:uppercase;letter-spacing:.04em;">Venue</th>
            <th style="padding:5px 10px;text-align:center;font-size:0.65rem;color:var(--text-gray);font-weight:600;text-transform:uppercase;">Gigs</th>
            <th style="padding:5px 10px;text-align:right;font-size:0.65rem;color:var(--text-gray);font-weight:600;text-transform:uppercase;">Gig Fees</th>
            <th style="padding:5px 10px;text-align:right;font-size:0.65rem;color:var(--text-gray);font-weight:600;text-transform:uppercase;">All-Time Earned</th>
            <th style="padding:5px 10px;text-align:right;font-size:0.65rem;color:var(--text-gray);font-weight:600;text-transform:uppercase;">Unpaid</th>
            <th style="padding:5px 10px;text-align:center;font-size:0.65rem;color:var(--text-gray);font-weight:600;text-transform:uppercase;">Rate</th>
          </tr>
        </thead>
        <tbody>${venueRows}</tbody>
      </table>` : `<div style="padding:8px 14px 10px 32px;font-size:0.73rem;color:var(--text-gray);">No venue earnings yet</div>`}
    </div>`;
  }).join('');

  // Enable run button always
  const btn = document.getElementById('affPreviewRunBtn');
  if (btn) {
    btn.disabled = false;
    btn.style.opacity = '1';
    btn.style.cursor = 'pointer';
  }
}

function _showPayoutConfirm() {
  if (!_payoutPreviewData) return;
  const d = _payoutPreviewData;
  const descEl = document.getElementById('affConfirmDesc');
  if (descEl) {
    if (d.eligible_count === 0) {
      descEl.innerHTML = `<span style="color:#f59e0b;">No affiliates are currently eligible for payout.</span><br><span style="font-size:0.78rem;color:var(--text-gray);">Balances may be below the $${(d.min_payout_cents/100).toFixed(0)} minimum, or no earnings have been recorded yet.</span>`;
    } else {
      descEl.innerHTML = `This will send <strong style="color:var(--text);">${d.eligible_count} payout(s)</strong> totaling <strong style="color:#10b981;">$${(d.eligible_total_cents/100).toFixed(2)}</strong> for quarter <strong style="color:var(--text);">${d.quarter}</strong>.`;
    }
  }
  const confirm = document.getElementById('affPayoutInnerConfirm');
  if (confirm) confirm.style.display = 'flex';
}

async function _doRunAffPayoutsFromPreview() {
  document.getElementById('affPayoutInnerConfirm').style.display = 'none';

  const runBtn = document.getElementById('affPreviewRunBtn');
  if (runBtn) { runBtn.disabled = true; runBtn.textContent = 'Processing…'; }

  const resultEl = document.getElementById('affPreviewRunResult');
  if (resultEl) { resultEl.textContent = 'Running payouts…'; resultEl.style.color = 'var(--text-gray)'; resultEl.style.opacity = '1'; }

  try {
    const r = await fetch('/api/admin/affiliate/run-payouts', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' }
    });
    const d = await r.json();

    if (resultEl) {
      resultEl.textContent = d.message || (d.ok ? '✓ Payouts complete!' : 'Error — check logs');
      resultEl.style.color = d.ok ? '#10b981' : '#ef4444';
    }
    if (d.ok) {
      await loadAffAccounting();
      setTimeout(() => {
        _closePayoutPreviewModal();
        if (resultEl) resultEl.style.opacity = '0';
      }, 2500);
    } else {
      if (runBtn) { runBtn.disabled = false; runBtn.textContent = '▶ Run Quarterly Payouts Now'; }
    }
  } catch(e) {
    if (resultEl) { resultEl.textContent = 'Request failed: ' + e.message; resultEl.style.color = '#ef4444'; }
    if (runBtn) { runBtn.disabled = false; runBtn.textContent = '▶ Run Quarterly Payouts Now'; }
  }
}

// Expose modal functions to global scope (called from innerHTML onclick attributes)
window._closePayoutPreviewModal = _closePayoutPreviewModal;
window._showPayoutConfirm       = _showPayoutConfirm;
window._doRunAffPayoutsFromPreview = _doRunAffPayoutsFromPreview;
window.sortPayoutPreview        = sortPayoutPreview;
window.affGoPage                = affGoPage;
window.affExport                = affExport;
window.toggleAffExportMenu      = toggleAffExportMenu;
