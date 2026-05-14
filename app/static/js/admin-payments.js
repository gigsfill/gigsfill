/**
 * Admin Payments Console — Tier 1 (read-only)
 * ============================================
 * Loads /api/admin/payments/search + /stats, renders filterable table.
 * Click row → drill into detail modal via /api/admin/payments/{id}.
 */
(function() {
  'use strict';

  let apPage = 1;
  let apPerPage = 50;
  let apTotal = 0;
  let apDebounceTimer = null;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function dollars(cents) {
    if (cents == null) return '—';
    return '$' + (Number(cents) / 100).toFixed(2);
  }
  function fmtDate(s) {
    if (!s) return '—';
    const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return s;
    return `${m[2]}/${m[3]}/${m[1]}`;
  }
  function fmtDateTime(s) {
    if (!s) return '—';
    // YYYY-MM-DD[T ]HH:MM:SS
    const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
    if (!m) return s;
    return `${m[2]}/${m[3]}/${m[1]} ${m[4]}:${m[5]}`;
  }

  // ── Status styling ────────────────────────────────────────────────────────
  const STATUS_STYLE = {
    paid:                { label: 'Paid ✓',     color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
    charged:             { label: 'Charged',    color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
    transferred:         { label: 'Transferred',color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
    scheduled:           { label: 'Scheduled',  color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)' },
    pending:             { label: 'Pending',    color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)' },
    pending_transfer:    { label: '⚠ Pending',  color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
    transfer_failed:     { label: '✗ Failed',   color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
    payment_failed:      { label: '✗ Failed',   color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
    charge_retry:        { label: '↻ Retrying', color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
    payment_cancelled:   { label: 'Cancelled',  color: '#94a3b8', bg: 'rgba(148,163,184,0.10)' },
    suspended:           { label: 'Suspended',  color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
    free_trial:          { label: '🎟 Free Trial', color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
    test:                { label: 'Test',       color: '#60a5fa', bg: 'rgba(96,165,250,0.12)' },
    processing:          { label: '⟳ Processing',color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
    disputed:            { label: '⚠ Disputed', color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
    dispute_lost:        { label: '✗ Dispute lost', color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
    dispute_won:         { label: 'Dispute won', color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
  };
  function statusPill(s) {
    const cfg = STATUS_STYLE[s] || { label: s || '—', color: '#94a3b8', bg: 'rgba(148,163,184,0.10)' };
    return `<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:600;color:${cfg.color};background:${cfg.bg};white-space:nowrap;">${esc(cfg.label)}</span>`;
  }

  const TYPE_LABEL = {
    venue_charge:      'Venue charge',
    artist_payout:     'Artist payout',
    single:            'Single',
    free_trial:        'Free trial',
    payment_cancelled: 'Cancelled',
  };

  // ── KPI stats ────────────────────────────────────────────────────────────
  function renderStats(stats) {
    if (!stats) { $('apStats').innerHTML = ''; return; }
    const card = (label, val, color) =>
      `<div style="padding:8px 10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;">
         <div style="font-size:0.62rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px;">${esc(label)}</div>
         <div style="font-size:0.95rem;font-weight:700;color:${color};">${esc(val)}</div>
       </div>`;
    const s = stats.by_status || {};
    $('apStats').innerHTML = [
      card('Transactions',   String(stats.count || 0),                  'var(--text)'),
      card('Revenue (gross)', dollars(stats.revenue_cents || 0),         '#10b981'),
      card('Commission',     dollars(stats.commission_cents || 0),       '#a78bfa'),
      card('Payouts sent',   dollars(stats.payouts_cents || 0),          '#06b6d4'),
      card('Needs attention',String(s.needs_attention || 0),
           (s.needs_attention || 0) > 0 ? '#ef4444' : 'var(--text-gray)'),
      card('Disputed',       String(s.disputed || 0),
           (s.disputed || 0) > 0 ? '#ef4444' : 'var(--text-gray)'),
      card('Cancelled',      String(s.payment_cancelled || 0),           'var(--text-gray)'),
      card('Free trial',     String(s.free_trial || 0),                  '#f59e0b'),
    ].join('');
  }

  // ── Filters → query params ───────────────────────────────────────────────
  function buildParams() {
    const p = new URLSearchParams();
    const q = ($('apSearch').value || '').trim();
    if (q) p.set('q', q);
    const st = $('apStatus').value;
    if (st) p.set('status', st);
    const tp = $('apType').value;
    if (tp) p.set('transaction_type', tp);
    const fd = $('apFromDate').value;
    if (fd) p.set('from_date', fd);
    const td = $('apToDate').value;
    if (td) p.set('to_date', td);
    const min = parseFloat($('apMinAmount').value);
    if (!isNaN(min) && min >= 0) p.set('min_amount_cents', Math.round(min * 100));
    const max = parseFloat($('apMaxAmount').value);
    if (!isNaN(max) && max >= 0) p.set('max_amount_cents', Math.round(max * 100));
    p.set('page', apPage);
    p.set('per_page', apPerPage);
    return p;
  }

  // ── Main load ────────────────────────────────────────────────────────────
  async function apReload() {
    const $table = $('apTable');
    $table.innerHTML = '<p style="color:var(--text-gray);text-align:center;padding:24px;font-size:0.8rem;">Loading…</p>';
    try {
      const params = buildParams();
      // Stats use only the date filters (not status/type — they tell us TOTAL
      // health within the window, regardless of the slice the user is viewing).
      const statsParams = new URLSearchParams();
      if (params.get('from_date')) statsParams.set('from_date', params.get('from_date'));
      if (params.get('to_date'))   statsParams.set('to_date',   params.get('to_date'));
      const [searchRes, statsRes] = await Promise.all([
        fetch('/api/admin/payments/search?' + params.toString(), { credentials: 'include' }),
        fetch('/api/admin/payments/stats?'  + statsParams.toString(), { credentials: 'include' }),
      ]);
      if (!searchRes.ok) throw new Error('HTTP ' + searchRes.status);
      const data  = await searchRes.json();
      const stats = statsRes.ok ? await statsRes.json() : null;
      apTotal = data.total || 0;
      renderStats(stats);
      renderTable(data.items || []);
      renderPagination();
    } catch (e) {
      $table.innerHTML = `<p style="color:#ef4444;text-align:center;padding:24px;font-size:0.8rem;">Failed to load: ${esc(e.message)}</p>`;
    }
  }
  window.apReload = apReload;

  function apDebouncedReload() {
    clearTimeout(apDebounceTimer);
    apDebounceTimer = setTimeout(() => { apPage = 1; apReload(); }, 350);
  }
  window.apDebouncedReload = apDebouncedReload;

  function renderTable(items) {
    if (!items.length) {
      $('apTable').innerHTML = '<p style="color:var(--text-gray);text-align:center;padding:24px;font-size:0.8rem;">No transactions match these filters.</p>';
      return;
    }
    const rows = items.map(r => {
      const amount = r.venue_charge_cents || r.amount_cents || 0;
      const isChild = r.parent_transaction_id != null;
      const indent = isChild ? '↳&nbsp;' : '';
      const venue  = r.venue_name  ? `<a href="javascript:void(0)" onclick="event.stopPropagation();apFilterVenue(${r.venue_id})" style="color:var(--cyan);text-decoration:none;">${esc(r.venue_name)}</a>` : '—';
      const artist = r.artist_name ? `<a href="javascript:void(0)" onclick="event.stopPropagation();apFilterArtist(${r.artist_id || ''})" style="color:#a78bfa;text-decoration:none;">${esc(r.artist_name)}</a>` : '—';
      const stripeRef = r.stripe_payment_intent_id || r.stripe_transfer_id || '';
      const stripeBadge = stripeRef
        ? `<a href="https://dashboard.stripe.com/${stripeRef.startsWith('pi_') ? 'payments/' : 'connect/transfers/'}${esc(stripeRef)}" target="_blank" onclick="event.stopPropagation()" style="font-family:monospace;font-size:0.65rem;color:#94a3b8;text-decoration:none;border-bottom:1px dashed rgba(148,163,184,0.4);" title="Open in Stripe Dashboard">${esc(stripeRef.slice(0,16))}…</a>`
        : '<span style="color:var(--text-gray);font-size:0.7rem;">—</span>';
      return `
        <tr onclick="apShowDetail(${r.id})" style="cursor:pointer;border-bottom:1px solid var(--border);">
          <td style="padding:6px 8px;font-size:0.72rem;color:var(--text-gray);white-space:nowrap;">${indent}#${r.id}</td>
          <td style="padding:6px 8px;font-size:0.72rem;color:var(--text);white-space:nowrap;">${esc(TYPE_LABEL[r.transaction_type] || r.transaction_type)}</td>
          <td style="padding:6px 8px;">${statusPill(r.status)}</td>
          <td style="padding:6px 8px;font-size:0.72rem;">${venue}</td>
          <td style="padding:6px 8px;font-size:0.72rem;">${artist}</td>
          <td style="padding:6px 8px;font-size:0.72rem;color:var(--text-gray);white-space:nowrap;">${fmtDate(r.gig_date)}</td>
          <td style="padding:6px 8px;font-size:0.78rem;font-weight:600;color:var(--text);text-align:right;white-space:nowrap;">${dollars(amount)}</td>
          <td style="padding:6px 8px;">${stripeBadge}</td>
          <td style="padding:6px 8px;font-size:0.7rem;color:var(--text-gray);white-space:nowrap;">${fmtDateTime(r.processed_at || r.scheduled_process_at || r.created_at)}</td>
        </tr>`;
    }).join('');
    $('apTable').innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:0.72rem;">
        <thead>
          <tr style="background:rgba(255,255,255,0.03);border-bottom:1px solid var(--border);">
            <th style="padding:7px 8px;text-align:left;font-weight:600;color:var(--text-gray);">ID</th>
            <th style="padding:7px 8px;text-align:left;font-weight:600;color:var(--text-gray);">Type</th>
            <th style="padding:7px 8px;text-align:left;font-weight:600;color:var(--text-gray);">Status</th>
            <th style="padding:7px 8px;text-align:left;font-weight:600;color:var(--text-gray);">Venue</th>
            <th style="padding:7px 8px;text-align:left;font-weight:600;color:var(--text-gray);">Artist</th>
            <th style="padding:7px 8px;text-align:left;font-weight:600;color:var(--text-gray);">Gig date</th>
            <th style="padding:7px 8px;text-align:right;font-weight:600;color:var(--text-gray);">Amount</th>
            <th style="padding:7px 8px;text-align:left;font-weight:600;color:var(--text-gray);">Stripe</th>
            <th style="padding:7px 8px;text-align:left;font-weight:600;color:var(--text-gray);">Processed</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function renderPagination() {
    const totalPages = Math.max(1, Math.ceil(apTotal / apPerPage));
    const from = apTotal === 0 ? 0 : (apPage - 1) * apPerPage + 1;
    const to   = Math.min(apPage * apPerPage, apTotal);
    const btn  = (label, onclick, disabled) =>
      `<button onclick="${disabled ? '' : onclick}" ${disabled ? 'disabled' : ''}
        style="padding:4px 10px;background:rgba(255,255,255,0.05);border:1px solid var(--border);border-radius:5px;color:var(--text-white);font-size:0.7rem;cursor:${disabled ? 'not-allowed' : 'pointer'};opacity:${disabled ? '0.4' : '1'};">${label}</button>`;
    $('apPagination').innerHTML = `
      <span>${from}–${to} of ${apTotal.toLocaleString()}</span>
      ${btn('‹ Prev', 'apGoPage(' + (apPage - 1) + ')', apPage <= 1)}
      <span>Page ${apPage} of ${totalPages}</span>
      ${btn('Next ›', 'apGoPage(' + (apPage + 1) + ')', apPage >= totalPages)}`;
  }
  window.apGoPage = function(p) { apPage = Math.max(1, p); apReload(); };

  window.apFilterVenue  = function(id) { $('apSearch').value = ''; apPage = 1; apReload(); /* TODO: dedicated filter for venue_id */ };
  window.apFilterArtist = function(id) { $('apSearch').value = ''; apPage = 1; apReload(); /* TODO: dedicated filter for artist_id */ };

  // ── Detail modal ─────────────────────────────────────────────────────────
  window.apShowDetail = async function(txnId) {
    if (typeof window.showStyledModal !== 'function') {
      alert('Modal system not loaded'); return;
    }
    window.showStyledModal('Transaction #' + txnId,
      '<p style="color:var(--text-gray);text-align:center;">Loading…</p>',
      [{ text: 'Close', style: 'ghost' }], { size: 'lg' });
    try {
      const res = await fetch('/api/admin/payments/' + txnId, { credentials: 'include' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      const t = data.transaction;
      const cell = (label, val) =>
        `<div><div style="font-size:0.65rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:.04em;">${esc(label)}</div>
         <div style="font-size:0.82rem;color:var(--text);margin-top:2px;">${val}</div></div>`;
      const stripeRef = t.stripe_payment_intent_id || t.stripe_transfer_id || '';
      const stripeLink = stripeRef
        ? `<a href="https://dashboard.stripe.com/${stripeRef.startsWith('pi_') ? 'payments/' : 'connect/transfers/'}${esc(stripeRef)}" target="_blank" style="color:var(--cyan);">${esc(stripeRef)} ↗</a>`
        : '<span style="color:var(--text-gray);">—</span>';

      const sibsHtml = (data.siblings && data.siblings.length)
        ? `<div style="margin-top:14px;">
             <div style="font-size:0.7rem;color:var(--cyan);font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Related rows (parent + siblings)</div>
             <table style="width:100%;border-collapse:collapse;font-size:0.7rem;">
               <thead><tr style="background:rgba(255,255,255,0.03);">
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">ID</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Type</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Status</th>
                 <th style="padding:5px;text-align:right;color:var(--text-gray);">Amount</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Stripe</th>
               </tr></thead><tbody>
               ${data.siblings.map(s => {
                 const amt = s.transaction_type === 'artist_payout' ? s.artist_payout_cents : (s.venue_charge_cents || s.amount_cents);
                 const sStripe = s.stripe_transfer_id || '';
                 return `<tr style="border-bottom:1px solid var(--border);${s.id == txnId ? 'background:rgba(6,182,212,0.07);' : ''}">
                   <td style="padding:5px;">#${s.id}${s.id == txnId ? ' (this)' : ''}</td>
                   <td style="padding:5px;">${esc(TYPE_LABEL[s.transaction_type] || s.transaction_type)}</td>
                   <td style="padding:5px;">${statusPill(s.status)}</td>
                   <td style="padding:5px;text-align:right;">${dollars(amt)}</td>
                   <td style="padding:5px;font-family:monospace;font-size:0.65rem;color:var(--text-gray);">${esc((sStripe || '').slice(0,18))}${sStripe ? '…' : '—'}</td>
                 </tr>`;
               }).join('')}
             </tbody></table>
           </div>` : '';

      const slotsHtml = (data.slots && data.slots.length > 1)
        ? `<div style="margin-top:14px;">
             <div style="font-size:0.7rem;color:var(--cyan);font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Gig slots (${data.slots.length})</div>
             <table style="width:100%;border-collapse:collapse;font-size:0.7rem;">
               <thead><tr style="background:rgba(255,255,255,0.03);">
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Slot</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Time</th>
                 <th style="padding:5px;text-align:right;color:var(--text-gray);">Pay</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Status</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Artist</th>
               </tr></thead><tbody>
               ${data.slots.map(sl => `<tr style="border-bottom:1px solid var(--border);">
                 <td style="padding:5px;">${esc(sl.slot_number)}</td>
                 <td style="padding:5px;">${esc(sl.start_time || '—')}–${esc(sl.end_time || '—')}</td>
                 <td style="padding:5px;text-align:right;">$${Number(sl.pay || 0).toFixed(2)}</td>
                 <td style="padding:5px;">${esc(sl.status)}</td>
                 <td style="padding:5px;">${esc(sl.artist_name || '—')}</td>
               </tr>`).join('')}
             </tbody></table>
           </div>` : '';

      const auditHtml = (data.audit && data.audit.length)
        ? `<div style="margin-top:14px;">
             <div style="font-size:0.7rem;color:var(--cyan);font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Recent admin actions (${data.audit.length})</div>
             ${data.audit.slice(0,5).map(a => `<div style="padding:6px 8px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:4px;margin-bottom:4px;font-size:0.7rem;">
               <span style="color:var(--text);font-weight:600;">${esc(a.action_type)}</span>
               <span style="color:var(--text-gray);margin-left:8px;">${esc(fmtDateTime(a.created_at))}</span>
               <span style="color:var(--text-gray);margin-left:8px;">admin #${esc(a.admin_user_id)}</span>
             </div>`).join('')}
           </div>` : '';

      const body = `
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;">
          ${cell('Status',          statusPill(t.status))}
          ${cell('Type',            esc(TYPE_LABEL[t.transaction_type_resolved] || t.transaction_type_resolved))}
          ${cell('Venue',           esc(t.venue_name || '—'))}
          ${cell('Artist',          esc(t.artist_name || '—'))}
          ${cell('Gig',             '#' + t.gig_id + (t.gig_title ? ' — ' + esc(t.gig_title) : ''))}
          ${cell('Gig date',        fmtDate(t.gig_date) + (t.gig_start_time ? ' ' + esc(t.gig_start_time) : ''))}
          ${cell('Amount (gross)',  dollars(t.amount_cents))}
          ${cell('Venue charge',    dollars(t.venue_charge_cents))}
          ${cell('Artist payout',   dollars(t.artist_payout_cents))}
          ${cell('Commission',      dollars(t.commission_cents))}
          ${cell('Scheduled',       fmtDateTime(t.scheduled_process_at))}
          ${cell('Processed',       fmtDateTime(t.processed_at))}
          ${cell('Stripe ref',      stripeLink)}
          ${cell('Created',         fmtDateTime(t.created_at))}
        </div>
        ${t.notes ? `<div style="margin-top:12px;padding:8px 10px;background:rgba(255,255,255,0.02);border-left:3px solid var(--cyan);font-size:0.72rem;color:var(--text-gray);"><strong style="color:var(--text);">Notes:</strong> ${esc(t.notes)}</div>` : ''}
        ${sibsHtml}
        ${slotsHtml}
        ${auditHtml}
        <div style="margin-top:12px;padding:8px 10px;background:rgba(245,158,11,0.07);border:1px solid rgba(245,158,11,0.25);border-radius:6px;font-size:0.7rem;color:#fbbf24;">
          🛠 <strong>Tier 1 (read-only).</strong> Refund / reverse / re-route actions will come in Tier 2.
        </div>`;

      // Re-render body by querying the open modal's body element directly
      const overlay = document.querySelector('.gfm-modal-overlay');
      const bodyEl  = overlay && overlay.querySelector('.gfm-modal-body');
      if (bodyEl) bodyEl.innerHTML = body;
    } catch (e) {
      const overlay = document.querySelector('.gfm-modal-overlay');
      const bodyEl  = overlay && overlay.querySelector('.gfm-modal-body');
      if (bodyEl) bodyEl.innerHTML = `<p style="color:#ef4444;text-align:center;padding:24px;">Failed to load: ${esc(e.message)}</p>`;
    }
  };

  // ── Lazy-load when the tab is shown ──────────────────────────────────────
  // The existing switchTab() doesn't know about per-tab loaders, so we hook
  // via the tab button's onclick after switchTab fires.
  let _apLoaded = false;
  function maybeLoad() {
    const tab = $('payments-tab');
    if (tab && tab.classList.contains('active') && !_apLoaded) {
      _apLoaded = true;
      apReload();
    }
  }
  // Re-check whenever the user clicks any tab button
  document.addEventListener('click', (e) => {
    if (e.target && e.target.closest && e.target.closest('button.tab')) {
      setTimeout(maybeLoad, 50);
    }
  });
  // And once on initial page load (in case the tab is the default)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(maybeLoad, 200));
  } else {
    setTimeout(maybeLoad, 200);
  }

})();
