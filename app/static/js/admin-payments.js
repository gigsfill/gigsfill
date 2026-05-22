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

  // Non-blocking confirmation banner shown center-screen after admin
  // actions complete. The detail modal + table reload underneath, so the
  // toast is purely visual confirmation — auto-dismisses after 5s, or
  // click to dismiss early. Centered + bigger so admin can't miss it.
  window.apToast = function(message, type) {
    type = type || 'success';
    const palette = {
      success: { rgb: '34,197,94',  icon: '✓' },
      error:   { rgb: '239,68,68',  icon: '✕' },
      info:    { rgb: '6,182,212',  icon: 'ⓘ' },
      warning: { rgb: '245,158,11', icon: '⚠' },
    };
    const cfg = palette[type] || palette.info;
    const host = (() => {
      let h = document.getElementById('apToastHost');
      if (!h) {
        h = document.createElement('div');
        h.id = 'apToastHost';
        // Center-of-viewport stack: each toast is its own card; multiple
        // toasts stack vertically inside this centered host.
        h.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);'
                       + 'z-index:99999;display:flex;flex-direction:column;gap:10px;'
                       + 'align-items:center;pointer-events:none;';
        document.body.appendChild(h);
      }
      return h;
    })();
    const toast = document.createElement('div');
    toast.style.cssText = `pointer-events:auto;padding:18px 28px;
      background:rgba(15,23,42,0.97);
      border:2px solid rgba(${cfg.rgb},0.7);border-radius:10px;color:rgb(${cfg.rgb});
      font-size:1rem;font-weight:600;line-height:1.4;
      max-width:520px;min-width:280px;text-align:center;
      box-shadow:0 12px 40px rgba(0,0,0,0.6),0 0 0 4px rgba(${cfg.rgb},0.08);
      opacity:0;transform:scale(0.92);
      transition:opacity 0.18s,transform 0.18s;cursor:pointer;
      display:flex;align-items:center;gap:12px;`;
    toast.innerHTML = `<span style="font-size:1.5rem;line-height:1;flex-shrink:0;">${cfg.icon}</span>
      <span style="flex:1;color:var(--text);">${esc(message)}</span>
      <span style="font-size:0.65rem;color:var(--text-gray);font-weight:400;flex-shrink:0;">click to dismiss</span>`;
    toast.addEventListener('click', () => toast.remove());
    host.appendChild(toast);
    requestAnimationFrame(() => {
      toast.style.opacity = '1';
      toast.style.transform = 'scale(1)';
    });
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'scale(0.96)';
      setTimeout(() => toast.remove(), 200);
    }, 5000);
  };
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
  // Backend stores datetimes in UTC ('2026-05-22 00:00:00') without a TZ
  // marker. Parsing those with `new Date()` is browser-dependent — Chrome
  // assumes local, Safari assumes UTC. Explicitly append 'Z' so the result
  // is unambiguously UTC, then format in the user's local TZ using 12-hour
  // clock (h:mm AM/PM) since that's what the rest of the app uses.
  function fmtDateTime(s) {
    if (!s) return '—';
    const str = String(s);
    const m = str.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/);
    if (!m) return str;
    const d = new Date(`${m[1]}T${m[2]}Z`);
    if (isNaN(d.getTime())) return str;
    return d.toLocaleString(undefined, {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: 'numeric', minute: '2-digit', hour12: true,
    });
  }
  // HH:MM (24h, stored as venue-local) → "h:MM AM/PM". Used for plain time
  // strings (gig_start_time, slot start/end) where there's no date attached.
  function fmtTime12(s) {
    if (!s) return '';
    const m = String(s).match(/^(\d{1,2}):(\d{2})/);
    if (!m) return String(s);
    let h = parseInt(m[1], 10);
    const min = m[2];
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return `${h}:${min} ${ampm}`;
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
      const isSelected = window._apSelected && window._apSelected.has(r.id);
      return `
        <tr onclick="apShowDetail(${r.id})" style="cursor:pointer;border-bottom:1px solid var(--border);${isSelected ? 'background:rgba(6,182,212,0.06);' : ''}">
          <td style="padding:6px 8px;width:24px;" onclick="event.stopPropagation()">
            <input type="checkbox" class="ap-row-cb" data-txn-id="${r.id}" ${isSelected ? 'checked' : ''}
              onchange="apToggleRow(${r.id}, this.checked)" style="cursor:pointer;">
          </td>
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
            <th style="padding:7px 8px;text-align:left;width:24px;" onclick="event.stopPropagation()">
              <input type="checkbox" onchange="apToggleAllOnPage(this.checked)" title="Select all on this page" style="cursor:pointer;">
            </th>
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
    apUpdateBulkBar();
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

  // Common helper for the action openers — make sure window._apLastDetail
  // holds the detail for the *target* txn. When the user invokes an action
  // from the Gig Actions panel of a different row (e.g. clicked an artist
  // payout, then hit ↩ Refund Venue), the target txn is the parent
  // venue_charge — different id from t.id in _apLastDetail. Re-fetch when
  // they don't match. Returns the detail object.
  async function apEnsureDetailFor(txnId) {
    const cached = window._apLastDetail;
    if (cached && cached.transaction && cached.transaction.id === txnId) {
      return cached;
    }
    const res = await fetch('/api/admin/payments/' + txnId, { credentials: 'include' });
    if (!res.ok) throw new Error('Could not load transaction #' + txnId);
    const data = await res.json();
    window._apLastDetail = data;
    return data;
  }

  // ── Tier 2: Refund ───────────────────────────────────────────────────────
  // Opens a themed prompt inside the existing gf-modals stack. The user can
  // edit the amount (default = full venue charge), pick a Stripe reason, and
  // add free-text notes. On submit, POSTs to /api/admin/payments/{id}/refund
  // and reloads both the detail view and the table.
  window.apOpenRefund = async function(txnId) {
    let data;
    try { data = await apEnsureDetailFor(txnId); }
    catch (e) {
      window.showErrorModal && window.showErrorModal('Refund', e.message || 'Failed to load.');
      return;
    }
    const t = data.transaction;
    const chargeCents = t.venue_charge_cents || t.amount_cents || 0;
    const chargeDollars = (chargeCents / 100).toFixed(2);

    // Roll up the per-child payouts so the summary reflects total artist take
    // on a multi-slot gig (parent.artist_payout_cents is 0 in that shape).
    const siblings = data.siblings || [];
    const childPayouts = siblings
      .filter(s => s.parent_transaction_id === t.id)
      .reduce((sum, s) => sum + (s.artist_payout_cents || 0), 0);
    const payoutTotal = childPayouts || t.artist_payout_cents || 0;
    const commission  = t.commission_cents || 0;
    const ccFee       = t.credit_card_fee_cents || 0;
    const platformNet = Math.max(commission - ccFee, 0);

    const transferredChildren = siblings.filter(
      s => s.id !== t.id && (s.status === 'transferred' || s.status === 'paid')
    );
    const warning = transferredChildren.length
      ? `<div style="padding:8px 10px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.35);border-radius:5px;font-size:0.72rem;color:#fca5a5;margin-bottom:10px;">
           ⚠ Child payout(s) ${transferredChildren.map(c => '#' + c.id).join(', ')} have already transferred to artists. A <strong>full</strong> refund will be blocked until those are reversed (Tier 3). A partial refund up to $${chargeDollars} is OK.
         </div>` : '';

    // Transaction Summary — what the venue actually paid and how it broke
    // out across artist payouts, Stripe fee, and the platform's net cut.
    const summaryRow = (label, val, opts = {}) => `
      <tr>
        <td style="padding:4px 8px;color:var(--text-gray);font-size:0.78rem;">${esc(label)}</td>
        <td style="padding:4px 8px;text-align:right;font-size:0.82rem;color:${opts.color || 'var(--text)'};font-weight:${opts.bold ? '700' : '500'};font-family:monospace;">${val}</td>
      </tr>`;
    const summaryHtml = `
      <div style="margin-bottom:12px;padding:10px 12px;background:rgba(6,182,212,0.05);border:1px solid rgba(6,182,212,0.18);border-radius:6px;">
        <div style="font-size:0.7rem;color:var(--cyan);font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Transaction summary</div>
        <table style="width:100%;border-collapse:collapse;">
          ${summaryRow('Venue paid (total)',    dollars(chargeCents), { bold: true })}
          ${summaryRow('  Artist payout total', dollars(payoutTotal))}
          ${summaryRow('  Stripe processing fee (kept by Stripe)', dollars(ccFee), { color: 'var(--text-gray)' })}
          ${summaryRow('  GigsFill fee (commission)', dollars(commission), { color: '#a78bfa', bold: true })}
          ${ccFee ? summaryRow('     ↳ platform net after Stripe fee', dollars(platformNet), { color: 'var(--text-gray)' }) : ''}
        </table>
      </div>`;

    const formHtml =
      warning +
      summaryHtml +
      `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;">
        <label>
          <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Amount (USD)</div>
          <input id="apRefundAmount" type="number" step="0.01" min="0.01" max="${chargeDollars}" value="${chargeDollars}"
            style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
          <div style="font-size:0.65rem;color:var(--text-gray);margin-top:3px;">Charge total: $${chargeDollars}</div>
        </label>
        <label>
          <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Reason</div>
          <select id="apRefundReason"
            style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
            <option value="requested_by_customer">Requested by customer</option>
            <option value="duplicate">Duplicate</option>
            <option value="fraudulent">Fraudulent</option>
          </select>
        </label>
      </div>
      <label style="display:block;margin-bottom:6px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Notes (optional, internal — saved on the txn + audit log)</div>
        <textarea id="apRefundNotes" rows="2" maxlength="500" placeholder="e.g. Venue called about double-charge after Stripe retry"
          style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.82rem;box-sizing:border-box;resize:vertical;"></textarea>
      </label>
      <label style="display:flex;align-items:center;gap:8px;font-size:0.75rem;color:var(--text-gray);margin-top:8px;">
        <input id="apRefundCancelKids" type="checkbox" checked style="width:auto;">
        Also cancel any still-scheduled child artist payouts (full refunds only)
      </label>
      <label style="display:flex;align-items:flex-start;gap:10px;font-size:0.85rem;color:#fbbf24;margin-top:10px;padding:10px 12px;background:rgba(245,158,11,0.12);border:2px solid rgba(245,158,11,0.5);border-radius:6px;cursor:pointer;">
        <input id="apRefundDryRun" type="checkbox" style="width:18px;height:18px;margin-top:2px;flex-shrink:0;">
        <span><strong>Dry run (safe test mode)</strong><br>
          <span style="font-size:0.72rem;color:var(--text-gray);">Validates everything end-to-end (auth, status, amounts, Stripe credentials) but skips both <code>stripe.Refund.create</code> AND the DB write. Use this to verify the wiring before issuing a real refund.</span>
        </span>
      </label>
      <div style="font-size:0.65rem;color:var(--text-gray);margin-top:8px;line-height:1.4;">
        <strong>Stripe fees:</strong> the original processing fee is NOT returned by Stripe. The venue is refunded the amount above; the platform absorbs the credit-card fee from the original charge.
      </div>`;

    window.showStyledModal(
      '↩ Refund Transaction #' + txnId,
      formHtml,
      [
        { text: 'Cancel', style: 'ghost', onClick: () => {} },
        { text: 'Refund', style: 'danger', onClick: () => apSubmitRefund(txnId) },
      ],
      { tone: 'warning', size: 'md' }
    );
  };

  async function apSubmitRefund(txnId) {
    const amountStr = (document.getElementById('apRefundAmount')?.value || '').trim();
    const reason    = document.getElementById('apRefundReason')?.value || 'requested_by_customer';
    const notes     = (document.getElementById('apRefundNotes')?.value || '').trim();
    const cancelKids = !!document.getElementById('apRefundCancelKids')?.checked;
    const amountDollars = parseFloat(amountStr);
    if (!Number.isFinite(amountDollars) || amountDollars <= 0) {
      if (window.showErrorModal) window.showErrorModal('Refund', 'Enter a valid amount.');
      return;
    }
    const dryRun = !!document.getElementById('apRefundDryRun')?.checked;
    const body = {
      amount_cents: Math.round(amountDollars * 100),
      reason,
      notes,
      cancel_pending_payouts: cancelKids,
      dry_run: dryRun,
    };
    try {
      const res = await fetch('/api/admin/payments/' + txnId + '/refund', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = json.detail || ('HTTP ' + res.status);
        if (window.showErrorModal) window.showErrorModal('Refund failed', msg);
        else alert(msg);
        return;
      }
      // Success — toast + immediate reload. No blocking modal so admin
      // can confirm at-a-glance and the table state updates underneath.
      const prefix = json.dry_run ? '[DRY RUN] ' : '';
      const amt = '$' + (json.amount_cents / 100).toFixed(2);
      const word = json.is_full ? 'Refund' : 'Partial refund';
      window.apToast(prefix + word + ' completed — ' + amt, 'success');
      if (!json.dry_run) { apReload(); window.apShowDetail(txnId); }
    } catch (e) {
      const msg = (e && e.message) || 'Network error';
      if (window.showErrorModal) window.showErrorModal('Refund failed', msg);
      else alert(msg);
    }
  }

  // ── Detail modal ─────────────────────────────────────────────────────────
  window.apShowDetail = async function(txnId) {
    if (typeof window.showStyledModal !== 'function') {
      alert('Modal system not loaded'); return;
    }
    window.showStyledModal('Transaction #' + txnId,
      '<p style="color:var(--text-gray);text-align:center;">Loading…</p>',
      [{ text: 'Close', style: 'ghost' }], { size: 'xl' });
    try {
      const res = await fetch('/api/admin/payments/' + txnId, { credentials: 'include' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      const t = data.transaction;
      const cell = (label, val) =>
        `<div><div style="font-size:0.65rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:.04em;">${esc(label)}</div>
         <div style="font-size:0.82rem;color:var(--text);margin-top:2px;">${val}</div></div>`;
      const stripeRef = t.stripe_payment_intent_id || t.stripe_transfer_id || '';
      // Stripe IDs (pi_1T0WEf… ~27 chars, no break opportunities) overflow
      // the narrow grid cell and visually crash into the next field. Truncate
      // to ~14 chars in the visible link; full id stays in the title= tooltip
      // and at the href.
      const stripeShort = stripeRef ? (stripeRef.slice(0, 14) + (stripeRef.length > 14 ? '…' : '')) : '';
      const stripeLink = stripeRef
        ? `<a href="https://dashboard.stripe.com/${stripeRef.startsWith('pi_') ? 'payments/' : 'connect/transfers/'}${esc(stripeRef)}" target="_blank" title="${esc(stripeRef)}" style="color:var(--cyan);font-family:monospace;font-size:0.78rem;">${esc(stripeShort)} ↗</a>`
        : '<span style="color:var(--text-gray);">—</span>';

      // Combined per-gig transactions table — replaces the old separate
      // "Related rows" and "Gig slots" sections. Each row shows: ID, Slot,
      // Time, Type, Artist, Status, Gig $ (the gig's gross pay for that
      // slot), Paid (what changed hands on Stripe), Processed timestamp.
      // The clicked row is highlighted in cyan with a "(this)" tag.
      const allTxns = data.siblings || [t];
      const slots   = (data.slots || []).slice().sort((a, b) =>
        (a.slot_number || 0) - (b.slot_number || 0));
      const childTxns = allTxns
        .filter(s => s.transaction_type === 'artist_payout')
        .sort((a, b) => a.id - b.id);
      const sameCount = slots.length === childTxns.length && childTxns.length > 0;
      // Map child txn id → matching slot. If slot count matches child count,
      // zip by id ASC ↔ slot_number ASC; otherwise fall back to artist_id.
      const slotByChildId = {};
      childTxns.forEach((c, i) => {
        slotByChildId[c.id] = sameCount
          ? slots[i]
          : slots.find(s => s.artist_id === c.artist_id);
      });

      function gigDollarsFor(s) {
        // Parent (venue_charge / single) — amount_cents is the gross gig pay
        // (sum of slot pays). Child (artist_payout) — slot.pay.
        if (s.transaction_type === 'artist_payout') {
          const sl = slotByChildId[s.id];
          return sl ? `$${Number(sl.pay || 0).toFixed(2)}` : '—';
        }
        return dollars(s.amount_cents);
      }
      function paidFor(s) {
        // Venue side: what the venue actually paid (incl fees).
        // Artist side: what the artist actually received (net of commission).
        if (s.transaction_type === 'artist_payout') return dollars(s.artist_payout_cents);
        return dollars(s.venue_charge_cents || s.amount_cents);
      }
      function rowMarkup(s) {
        const isThis = s.id == txnId;
        const sl = slotByChildId[s.id];
        const slotN = sl ? esc(String(sl.slot_number)) : '—';
        const time  = (sl && sl.start_time && sl.end_time)
          ? `${esc(fmtTime12(sl.start_time))}–${esc(fmtTime12(sl.end_time))}`
          : '—';
        // "Artist" column doubles as the counterparty column: venue's name
        // on the venue_charge row (the venue is the payer), artist's name
        // on artist_payout rows (the artist is the payee). The clicked row
        // falls back to t.venue_name / t.artist_name if the sibling rows
        // don't carry the name lookup.
        let counterparty;
        if (s.transaction_type === 'artist_payout') {
          counterparty = sl && sl.artist_name ? esc(sl.artist_name)
                       : (isThis ? esc(t.artist_name || '—') : '—');
        } else {
          counterparty = esc(t.venue_name || '—');
        }
        return `<tr style="border-bottom:1px solid var(--border);${isThis ? 'background:rgba(6,182,212,0.07);' : ''}">
          <td style="padding:5px;">#${s.id}${isThis ? ' (this)' : ''}</td>
          <td style="padding:5px;">${slotN}</td>
          <td style="padding:5px;">${time}</td>
          <td style="padding:5px;">${esc(TYPE_LABEL[s.transaction_type] || s.transaction_type)}</td>
          <td style="padding:5px;">${counterparty}</td>
          <td style="padding:5px;">${statusPill(s.status)}</td>
          <td style="padding:5px;font-family:monospace;">${gigDollarsFor(s)}</td>
          <td style="padding:5px;font-family:monospace;font-weight:600;">${paidFor(s)}</td>
          <td style="padding:5px;font-size:0.7rem;color:var(--text-gray);">${esc(fmtDateTime(s.processed_at || s.scheduled_process_at))}</td>
        </tr>`;
      }
      // Order: parent venue_charge first, then child payouts in slot order.
      const orderedRows = allTxns
        .slice()
        .sort((a, b) => {
          const av = (a.transaction_type === 'venue_charge' || a.transaction_type === 'single') ? 0 : 1;
          const bv = (b.transaction_type === 'venue_charge' || b.transaction_type === 'single') ? 0 : 1;
          if (av !== bv) return av - bv;
          return a.id - b.id;
        });

      const sibsHtml = orderedRows.length
        ? `<div style="margin-top:14px;">
             <div style="font-size:0.7rem;color:var(--cyan);font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Transactions on this gig</div>
             <table style="width:100%;border-collapse:collapse;font-size:0.72rem;">
               <thead><tr style="background:rgba(255,255,255,0.03);">
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">ID</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Slot</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Time</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Type</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Artist</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Status</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Gig $</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Paid</th>
                 <th style="padding:5px;text-align:left;color:var(--text-gray);">Processed</th>
               </tr></thead><tbody>
               ${orderedRows.map(rowMarkup).join('')}
             </tbody></table>
           </div>` : '';

      // Old separate "Gig slots" section is now merged into the table above.
      const slotsHtml = '';

      // ── Gig-level action shortcuts ──────────────────────────────────────
      // Always-present buttons that operate on the gig as a whole, no matter
      // which row admin clicked to open this modal. Per-row Actions toolbar
      // (further down) handles surgical operations on the clicked row only.
      const parentTxn = orderedRows.find(s =>
        s.transaction_type === 'venue_charge' || s.transaction_type === 'single'
      ) || null;
      const gigCanRefundVenue = parentTxn
        && ['charged','paid','transferred'].includes(parentTxn.status)
        && !!parentTxn.stripe_payment_intent_id;
      const firstReversibleChild = orderedRows.find(s =>
        s.transaction_type === 'artist_payout'
        && ['transferred','paid'].includes(s.status)
        && !!s.stripe_transfer_id
      );
      const gigCanReverseTransfers = !!firstReversibleChild;

      // Uniform-width buttons via grid: each cell is 1fr so every button
      // matches the longest one. Buttons themselves stretch (width:100%)
      // to fill their cell.
      const gigBtn = (label, color, onclick, sub) => `
        <button onclick="${onclick}"
          style="display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:10px 14px;background:rgba(${color},0.15);border:1px solid rgba(${color},0.45);color:rgb(${color});border-radius:6px;font-size:0.82rem;font-weight:700;cursor:pointer;text-align:left;width:100%;box-sizing:border-box;">
          <span>${label}</span>
          <span style="font-size:0.65rem;font-weight:400;color:var(--text-gray);line-height:1.4;">${esc(sub || '')}</span>
        </button>`;

      // ONE button covers both the venue-side (refund) and the artist-side
      // (reverse transfers). The modal it opens has both Step 1 (reverse
      // artists) and Step 2 (refund venue) — admin checks whatever
      // combination they actually need. So a single button is enough.
      const gigCanUndo = gigCanRefundVenue || gigCanReverseTransfers;
      const undoTargetId = (firstReversibleChild && firstReversibleChild.id)
                       || (parentTxn && parentTxn.id)
                       || t.id;

      const gigActionsButtons = [
        gigCanUndo ? gigBtn(
          '↺ Refund / Reverse',
          '239,68,68',
          `apOpenReverseTransfer(${undoTargetId})`,
          'Reverse one or more artist transfers, refund the venue, or both — all in one submit.'
        ) : '',
        gigBtn(
          '✉ Resend Emails',
          '139,92,246',
          `apOpenResendEmailBatch(${t.id})`,
          'Pick which rows to resend the payment-event email for. Template is auto-picked per row.'
        ),
        gigBtn(
          '✓ Mark Rows Resolved',
          '6,182,212',
          `apOpenMarkResolvedBatch(${t.id})`,
          'Force a status on one or more rows with a shared reason. Admin override — no Stripe call.'
        ),
      ].filter(Boolean).join('');

      const gigActionsHtml = gigActionsButtons
        ? `<div style="margin-top:14px;padding:10px 12px;background:rgba(139,92,246,0.05);border:1px solid rgba(139,92,246,0.25);border-radius:6px;">
             <div style="font-size:0.7rem;color:#c4b5fd;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Gig Actions</div>
             <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:8px;">Operate on the gig as a whole — same end result whether admin clicked the venue charge row or an artist payout row.</div>
             <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;">${gigActionsButtons}</div>
           </div>`
        : '';

      const auditHtml = (data.audit && data.audit.length)
        ? `<div style="margin-top:14px;">
             <div style="font-size:0.7rem;color:var(--cyan);font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Recent admin actions (${data.audit.length})</div>
             ${data.audit.slice(0,5).map(a => `<div style="padding:6px 8px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:4px;margin-bottom:4px;font-size:0.7rem;">
               <span style="color:var(--text);font-weight:600;">${esc(a.action)}</span>
               <span style="color:var(--text-gray);margin-left:8px;">${esc(fmtDateTime(a.created_at))}</span>
               <span style="color:var(--text-gray);margin-left:8px;">${esc(a.admin_email || 'admin #' + a.admin_user_id)}</span>
             </div>`).join('')}
           </div>` : '';

      // Tier 2 actions toolbar — show only the actions that make sense for
      // Per-row "Row Actions" toolbar — only LESS-COMMON, row-specific
      // operations. The big money moves (Refund / Reverse) live in the
      // Gig Actions panel above so the same buttons don't show up twice.
      // What stays here:
      //   Re-route Payout  payout rows in {scheduled}
      //                    (artist's Connect closed; admin redirects)
      //   Re-fire         venue rows in {payment_failed, charge_retry},
      //                    OR payout rows in {transfer_failed, pending_transfer}
      //                    (scheduler gave up; admin nudges the queue)
      //   Resend Email    venue rows in {charged, paid}  → venue_charged
      //                    OR payout rows in {transferred, paid}
      //                                     → artist_payout_sent
      //                    (SMTP hiccup; replay the same email)
      //   Mark Resolved   always — admin override for stuck rows.
      //
      // Net effect for the common states:
      //   scheduled payout         → Re-route + Mark Resolved
      //   scheduled venue/single   → Mark Resolved
      //   paid (either)            → Resend Email + Mark Resolved
      //   payment_failed venue     → Re-fire + Mark Resolved
      //   transfer_failed payout   → Re-fire + Mark Resolved
      //   payment_cancelled (any)  → Mark Resolved
      const isChild     = t.parent_transaction_id != null;
      const isVenueRow  = (t.transaction_type_resolved === 'venue_charge'
                        || t.transaction_type_resolved === 'single') && !isChild;
      const isPayoutRow = t.transaction_type_resolved === 'artist_payout' || isChild;

      // Mark Resolved + Resend Email moved up to Gig Actions (gig-wide
      // batch). Only genuinely-per-row operations live here now:
      //   Re-route Payout — each row may need a different destination
      //   Re-fire         — narrowly scoped to one failed row
      const showReroute = isPayoutRow && t.status === 'scheduled';
      const showRefire  = (isVenueRow  && ['payment_failed','charge_retry'].includes(t.status))
                       || (isPayoutRow && ['transfer_failed','pending_transfer'].includes(t.status));

      const btn = (label, color, onclick, sub) => `
        <button onclick="${onclick}"
          style="display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:7px 14px;background:rgba(${color},0.15);border:1px solid rgba(${color},0.45);color:rgb(${color});border-radius:5px;font-size:0.75rem;font-weight:600;cursor:pointer;margin:0 6px 6px 0;text-align:left;">
          <span>${label}</span>
          ${sub ? `<span style="font-size:0.62rem;font-weight:400;color:var(--text-gray);">${esc(sub)}</span>` : ''}
        </button>`;

      const buttons = [
        showReroute ? btn('↪ Re-route Payout',  '6,182,212',  `apOpenReroute(${txnId})`, 'Send this payout to a different artist Connect account.') : '',
        showRefire  ? btn('↻ Re-fire',          '245,158,11', `apOpenRefire(${txnId})`,  'Reset this failed row to scheduled; scheduler will retry.') : '',
      ].filter(Boolean).join('');

      const actionsHtml = buttons
        ? `<div style="margin-top:14px;padding:10px 12px;background:rgba(6,182,212,0.05);border:1px solid rgba(6,182,212,0.18);border-radius:6px;">
             <div style="font-size:0.7rem;color:var(--cyan);font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">Row Actions — #${t.id}</div>
             <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:8px;">Operations that genuinely apply to only this row. Gig-wide actions (Refund Venue, Reverse Transfers, Resend Emails, Mark Rows Resolved) all live in <strong>Gig Actions</strong> above.</div>
             <div style="display:flex;flex-wrap:wrap;align-items:flex-start;">${buttons}</div>
           </div>` : '';

      // Gig-level overview replaces the old 14-cell grid that mixed gig
      // info with single-txn info. Per-txn detail lives in the table below;
      // the clicked row is flagged there.
      const venueLine = t.venue_name
        ? esc(t.venue_name) + (t.venue_city ? ` <span style="color:var(--text-gray);">· ${esc(t.venue_city)}${t.venue_state ? ', ' + esc(t.venue_state) : ''}</span>` : '')
        : '—';
      const gigTime = t.gig_start_time ? ' · ' + esc(fmtTime12(t.gig_start_time)) : '';
      const gigLine = '#' + t.gig_id + (t.gig_title ? ' — ' + esc(t.gig_title) : '');
      const selectedLine = `#${t.id} (${esc(TYPE_LABEL[t.transaction_type_resolved] || t.transaction_type_resolved)} · ${statusPill(t.status)})`
        + (stripeRef ? ` · ${stripeLink}` : '');

      const overviewRow = (label, val) => `
        <tr>
          <td style="padding:3px 8px 3px 0;color:var(--text-gray);font-size:0.7rem;white-space:nowrap;width:1%;">${esc(label)}</td>
          <td style="padding:3px 0;font-size:0.82rem;color:var(--text);">${val}</td>
        </tr>`;

      const body = `
        <div style="padding:10px 12px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;">
          <table style="border-collapse:collapse;">
            ${overviewRow('Gig',      gigLine)}
            ${overviewRow('Venue',    venueLine)}
            ${overviewRow('Date',     esc(fmtDate(t.gig_date)) + gigTime)}
            ${overviewRow('Selected', selectedLine)}
          </table>
        </div>
        ${gigActionsHtml}
        ${sibsHtml}
        ${t.notes ? `<div style="margin-top:12px;padding:8px 10px;background:rgba(255,255,255,0.02);border-left:3px solid var(--cyan);font-size:0.72rem;color:var(--text-gray);"><strong style="color:var(--text);">Notes (selected row):</strong> ${esc(t.notes)}</div>` : ''}
        ${slotsHtml}
        ${actionsHtml}
        ${auditHtml}`;

      // Stash the loaded txn so the refund modal can read amounts off it.
      window._apLastDetail = data;

      // Re-render body by querying the open modal's body element. When this
      // is opened from inside another modal (e.g. clicking a txn id in the
      // Reconcile results), gf-modals stacks the new overlay on top —
      // querySelector('.gfm-modal-overlay') would return the OLDEST overlay
      // (Reconcile) and we'd write the detail body into that, leaving the
      // top "Loading…" modal forever stuck. Pick the LAST overlay so we
      // always update the topmost modal.
      const overlays = document.querySelectorAll('.gfm-modal-overlay');
      const overlay = overlays.length ? overlays[overlays.length - 1] : null;
      const bodyEl  = overlay && overlay.querySelector('.gfm-modal-body');
      if (bodyEl) bodyEl.innerHTML = body;
    } catch (e) {
      const overlays = document.querySelectorAll('.gfm-modal-overlay');
      const overlay = overlays.length ? overlays[overlays.length - 1] : null;
      const bodyEl  = overlay && overlay.querySelector('.gfm-modal-body');
      if (bodyEl) bodyEl.innerHTML = `<p style="color:#ef4444;text-align:center;padding:24px;">Failed to load: ${esc(e.message)}</p>`;
    }
  };

  // ── Tier 2: Mark-resolved ────────────────────────────────────────────────
  // Pure DB override — no Stripe, no email. Use for stuck rows (e.g.
  // dispute_won that didn't sync from a webhook).
  // ── Tier 2 (gig-wide): Mark Rows Resolved — batch ───────────────────────
  // Lists every row in the gig with a per-row "new status" dropdown plus a
  // single shared reason field. Submit batches the changes — same audit
  // logging as the single-row variant.
  window.apOpenMarkResolvedBatch = async function(anyTxnId) {
    let data;
    try { data = await apEnsureDetailFor(anyTxnId); }
    catch (e) {
      window.showErrorModal && window.showErrorModal('Mark Rows Resolved', e.message || 'Failed to load.');
      return;
    }
    const t = data.transaction;
    const siblings = data.siblings || [];
    const slots = (data.slots || []).slice().sort((a, b) => (a.slot_number||0) - (b.slot_number||0));
    const sameCount = slots.length === siblings.filter(s => s.transaction_type === 'artist_payout').length;
    const slotByChildId = {};
    siblings.filter(s => s.transaction_type === 'artist_payout')
      .sort((a, b) => a.id - b.id)
      .forEach((c, i) => {
        slotByChildId[c.id] = sameCount ? slots[i]
          : slots.find(s => s.artist_id === c.artist_id);
      });

    const ordered = siblings.slice().sort((a, b) => {
      const av = (a.transaction_type === 'venue_charge' || a.transaction_type === 'single') ? 0 : 1;
      const bv = (b.transaction_type === 'venue_charge' || b.transaction_type === 'single') ? 0 : 1;
      if (av !== bv) return av - bv;
      return a.id - b.id;
    });

    const STATUS_OPTS = ['paid','transferred','payment_cancelled','suspended','dispute_won','dispute_lost'];
    function rowMarkup(r) {
      const sl = slotByChildId[r.id];
      const isPayout = r.transaction_type === 'artist_payout';
      const label = isPayout
        ? `Slot ${sl ? sl.slot_number : '?'} · ${(sl && sl.artist_name) || (r.id === t.id ? t.artist_name : 'Artist')}`
        : `Venue charge · ${esc(t.venue_name || '')}`;
      // Pick a sensible default for the new status — keep current selected
      // so admin doesn't accidentally force an unintended change.
      const opts = STATUS_OPTS.map(s =>
        `<option value="${s}" ${s === r.status ? 'disabled' : ''}>${s}</option>`
      ).join('');
      return `
        <div style="display:flex;align-items:center;gap:10px;padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);">
          <input type="checkbox" class="apMrkCb" data-cid="${r.id}" style="width:auto;flex-shrink:0;cursor:pointer;">
          <span style="flex:0 0 56px;font-size:0.72rem;color:var(--text-gray);">#${r.id}</span>
          <span style="flex:1;min-width:0;font-size:0.78rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(label)}</span>
          <span style="flex:0 0 110px;font-size:0.72rem;">${statusPill(r.status)}</span>
          <span style="font-size:0.7rem;color:var(--text-gray);">→</span>
          <select class="apMrkNew" data-cid="${r.id}"
            style="flex:0 0 170px;padding:5px 8px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.75rem;box-sizing:border-box;">
            <option value="">— pick status —</option>
            ${opts}
          </select>
        </div>`;
    }

    const formHtml = `
      <p style="font-size:0.75rem;color:var(--text);margin:0 0 10px;line-height:1.5;">
        Force a status on one or more rows. Reason is required and shared across the batch. This is an admin override — no Stripe call is made. Audit-logged per row.
      </p>
      <label style="display:block;margin-bottom:10px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Reason (required, min 5 chars, applied to every checked row)</div>
        <textarea id="apMrkReason" rows="2" maxlength="1000" placeholder="e.g. Stripe Dashboard shows these were refunded 2026-05-22 — webhook never fired; syncing manually."
          style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.82rem;box-sizing:border-box;resize:vertical;"></textarea>
      </label>
      <div style="border:1px solid var(--border);border-radius:6px;overflow:hidden;background:rgba(0,0,0,0.15);">
        ${ordered.map(rowMarkup).join('')}
      </div>
      <label style="display:flex;align-items:flex-start;gap:10px;font-size:0.85rem;color:#fbbf24;margin-top:12px;padding:10px 12px;background:rgba(245,158,11,0.12);border:2px solid rgba(245,158,11,0.5);border-radius:6px;cursor:pointer;">
        <input id="apMrkDryRun" type="checkbox" style="width:18px;height:18px;margin-top:2px;flex-shrink:0;">
        <span><strong>Dry run (safe test mode)</strong><br>
          <span style="font-size:0.72rem;color:var(--text-gray);">Validates without writing the status changes.</span>
        </span>
      </label>`;

    window.showStyledModal(
      '✓ Mark Rows Resolved — Gig #' + t.gig_id,
      formHtml,
      [
        { text: 'Cancel', style: 'ghost',   onClick: () => {} },
        { text: 'Apply',  style: 'primary', onClick: () => apSubmitMarkResolvedBatch(t.id) },
      ],
      { tone: 'info', size: 'lg' }
    );
  };

  async function apSubmitMarkResolvedBatch(txnId) {
    const reason = (document.getElementById('apMrkReason')?.value || '').trim();
    const dry_run = !!document.getElementById('apMrkDryRun')?.checked;
    if (reason.length < 5) {
      window.showErrorModal && window.showErrorModal('Mark Resolved', 'Reason required (min 5 chars).');
      return;
    }
    const rows = [];
    document.querySelectorAll('.apMrkCb').forEach(cb => {
      if (!cb.checked) return;
      const cid = parseInt(cb.dataset.cid, 10);
      const sel = document.querySelector('.apMrkNew[data-cid="' + cid + '"]');
      const ns  = sel ? sel.value : '';
      if (!ns) return;  // skip rows where admin didn't pick a new status
      rows.push({ txn_id: cid, new_status: ns });
    });
    if (!rows.length) {
      window.showErrorModal && window.showErrorModal('Mark Resolved',
        'Tick at least one row and pick its new status.');
      return;
    }
    try {
      const res = await fetch('/api/admin/payments/mark-resolved-batch', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows, reason, dry_run }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Mark Resolved failed',
          json.detail || ('HTTP ' + res.status));
        return;
      }
      const prefix = json.dry_run ? '[DRY RUN] ' : '';
      const failRows = (json.results || []).filter(r => !r.ok);
      if (failRows.length) {
        // Failures still block — admin needs to see per-row errors.
        const lines = [`${json.ok_count} row${json.ok_count===1?'':'s'} updated, ${json.fail_count} failed.`];
        lines.push('Failures:\n' + failRows.slice(0, 10).map(r => `  • #${r.txn_id}: ${r.error}`).join('\n'));
        (window.showErrorModal || window.showStyledModal)(
          prefix + 'Mark Resolved', lines.join('\n\n'),
          () => { if (!json.dry_run) { apReload(); window.apShowDetail(txnId); } }
        );
      } else {
        window.apToast(`${prefix}Mark Resolved — ${json.ok_count} row${json.ok_count===1?'':'s'} updated`, 'success');
        if (!json.dry_run) { apReload(); window.apShowDetail(txnId); }
      }
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Mark Resolved failed', e.message || 'Network error');
    }
  }

  // ── Tier 2 (gig-wide): Resend Emails — batch ────────────────────────────
  // Lists each row, auto-derives the template per row (venue_charge →
  // venue_charged email; artist_payout → artist_payout_sent email). Rows
  // whose status doesn't fit a template are greyed and not pickable.
  window.apOpenResendEmailBatch = async function(anyTxnId) {
    let data;
    try { data = await apEnsureDetailFor(anyTxnId); }
    catch (e) {
      window.showErrorModal && window.showErrorModal('Resend Emails', e.message || 'Failed to load.');
      return;
    }
    const t = data.transaction;
    const siblings = data.siblings || [];
    const slots = (data.slots || []).slice().sort((a, b) => (a.slot_number||0) - (b.slot_number||0));
    const sameCount = slots.length === siblings.filter(s => s.transaction_type === 'artist_payout').length;
    const slotByChildId = {};
    siblings.filter(s => s.transaction_type === 'artist_payout')
      .sort((a, b) => a.id - b.id)
      .forEach((c, i) => {
        slotByChildId[c.id] = sameCount ? slots[i]
          : slots.find(s => s.artist_id === c.artist_id);
      });
    const ordered = siblings.slice().sort((a, b) => {
      const av = (a.transaction_type === 'venue_charge' || a.transaction_type === 'single') ? 0 : 1;
      const bv = (b.transaction_type === 'venue_charge' || b.transaction_type === 'single') ? 0 : 1;
      if (av !== bv) return av - bv;
      return a.id - b.id;
    });

    function rowMarkup(r) {
      const sl = slotByChildId[r.id];
      const isPayout = r.transaction_type === 'artist_payout';
      const label = isPayout
        ? `Slot ${sl ? sl.slot_number : '?'} · ${(sl && sl.artist_name) || (r.id === t.id ? t.artist_name : 'Artist')}`
        : `Venue charge · ${esc(t.venue_name || '')}`;
      const isVenue = r.transaction_type === 'venue_charge' || r.transaction_type === 'single';
      const canResend = isVenue
        ? ['paid','charged'].includes(r.status)
        : ['transferred','paid'].includes(r.status);
      const template = isVenue ? 'venue_charged (to venue)' : 'artist_payout_sent (to artist)';
      const tmplDisplay = canResend
        ? `<span style="color:var(--text);font-size:0.72rem;">${esc(template)}</span>`
        : `<span style="color:var(--text-gray);font-size:0.7rem;font-style:italic;">no template for status "${esc(r.status)}"</span>`;
      return `
        <div style="display:flex;align-items:center;gap:10px;padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);${canResend ? '' : 'opacity:0.5;'}">
          <input type="checkbox" class="apResendCb" data-cid="${r.id}"
            ${canResend ? 'checked' : 'disabled'}
            style="width:auto;flex-shrink:0;cursor:${canResend ? 'pointer' : 'not-allowed'};">
          <span style="flex:0 0 56px;font-size:0.72rem;color:var(--text-gray);">#${r.id}</span>
          <span style="flex:0 0 220px;font-size:0.78rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(label)}</span>
          <span style="flex:0 0 110px;font-size:0.72rem;">${statusPill(r.status)}</span>
          <span style="flex:1;min-width:0;">→ ${tmplDisplay}</span>
        </div>`;
    }

    const formHtml = `
      <p style="font-size:0.75rem;color:var(--text);margin:0 0 10px;line-height:1.5;">
        Pick which rows to re-send the payment-event email for. Template is auto-picked: venue rows get the "Venue charged" email, artist payout rows get the "Artist payout sent" email. Rows in a status that has no template are greyed out.
      </p>
      <div style="border:1px solid var(--border);border-radius:6px;overflow:hidden;background:rgba(0,0,0,0.15);">
        ${ordered.map(rowMarkup).join('')}
      </div>
      <label style="display:flex;align-items:flex-start;gap:10px;font-size:0.85rem;color:#fbbf24;margin-top:12px;padding:10px 12px;background:rgba(245,158,11,0.12);border:2px solid rgba(245,158,11,0.5);border-radius:6px;cursor:pointer;">
        <input id="apResendDryRun" type="checkbox" style="width:18px;height:18px;margin-top:2px;flex-shrink:0;">
        <span><strong>Dry run (safe test mode)</strong><br>
          <span style="font-size:0.72rem;color:var(--text-gray);">Validates wiring without sending email.</span>
        </span>
      </label>`;

    window.showStyledModal(
      '✉ Resend Emails — Gig #' + t.gig_id,
      formHtml,
      [
        { text: 'Cancel', style: 'ghost',   onClick: () => {} },
        { text: 'Send',   style: 'primary', onClick: () => apSubmitResendEmailBatch(t.id) },
      ],
      { tone: 'info', size: 'lg' }
    );
  };

  async function apSubmitResendEmailBatch(txnId) {
    const dry_run = !!document.getElementById('apResendDryRun')?.checked;
    const txn_ids = [];
    document.querySelectorAll('.apResendCb').forEach(cb => {
      if (cb.checked && !cb.disabled) txn_ids.push(parseInt(cb.dataset.cid, 10));
    });
    if (!txn_ids.length) {
      window.showErrorModal && window.showErrorModal('Resend Emails', 'Tick at least one row.');
      return;
    }
    try {
      const res = await fetch('/api/admin/payments/resend-email-batch', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ txn_ids, dry_run }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Resend failed',
          json.detail || ('HTTP ' + res.status));
        return;
      }
      const prefix = json.dry_run ? '[DRY RUN] ' : '';
      const failRows = (json.results || []).filter(r => !r.ok);
      if (failRows.length) {
        const lines = [`${json.ok_count} email${json.ok_count===1?'':'s'} sent, ${json.fail_count} failed.`];
        lines.push('Failures:\n' + failRows.slice(0, 10).map(r => `  • #${r.txn_id}: ${r.error}`).join('\n'));
        (window.showErrorModal || window.showStyledModal)(
          prefix + 'Resend Emails', lines.join('\n\n'),
          () => { if (!json.dry_run) window.apShowDetail(txnId); }
        );
      } else {
        window.apToast(`${prefix}Resend Emails — ${json.ok_count} sent`, 'success');
        if (!json.dry_run) window.apShowDetail(txnId);
      }
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Resend failed', e.message || 'Network error');
    }
  }

  // ── Single-row Mark Resolved (still wired but not surfaced in UI now;
  //    kept around for direct curl/external use) ─────────────────────────
  window.apOpenMarkResolved = function(txnId) {
    const data = window._apLastDetail;
    const t = data && data.transaction;
    if (!t || t.id !== txnId) return;
    const targets = ['paid','transferred','payment_cancelled','suspended','dispute_won','dispute_lost']
      .filter(s => s !== t.status);
    const formHtml = `
      <p style="font-size:0.75rem;color:var(--text-gray);margin:0 0 12px;">
        Force this transaction into a new status without touching Stripe.
        Recorded in <code>admin_audit_log</code> with the reason you give.
      </p>
      <label style="display:block;margin-bottom:10px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Current status</div>
        <div style="padding:6px 10px;font-size:0.85rem;color:var(--text);background:#151b28;border:1px solid var(--border);border-radius:5px;">
          ${esc(t.status)}
        </div>
      </label>
      <label style="display:block;margin-bottom:10px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">New status</div>
        <select id="apMarkNewStatus"
          style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
          ${targets.map(s => `<option value="${s}">${esc(s)}</option>`).join('')}
        </select>
      </label>
      <label style="display:block;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Reason (required, min 5 chars)</div>
        <textarea id="apMarkReason" rows="3" maxlength="1000" placeholder="e.g. Dispute won on Stripe Dashboard 2026-05-21 but webhook never fired; syncing manually."
          style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.82rem;box-sizing:border-box;resize:vertical;"></textarea>
      </label>`;
    window.showStyledModal(
      '✓ Mark Resolved — Transaction #' + txnId,
      formHtml,
      [
        { text: 'Cancel', style: 'ghost', onClick: () => {} },
        { text: 'Apply',  style: 'primary', onClick: () => apSubmitMarkResolved(txnId) },
      ],
      { tone: 'info', size: 'md' }
    );
  };
  async function apSubmitMarkResolved(txnId) {
    const new_status = document.getElementById('apMarkNewStatus')?.value || '';
    const reason     = (document.getElementById('apMarkReason')?.value || '').trim();
    if (reason.length < 5) {
      window.showErrorModal && window.showErrorModal('Mark Resolved', 'Reason is required (min 5 chars).');
      return;
    }
    try {
      const res = await fetch('/api/admin/payments/' + txnId + '/mark-resolved', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_status, reason }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Mark Resolved failed', json.detail || ('HTTP ' + res.status));
        return;
      }
      window.showSuccessModal && window.showSuccessModal(
        'Status updated', `Transaction #${txnId}: ${json.from} → ${json.to}`,
        () => { apReload(); window.apShowDetail(txnId); }
      );
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Mark Resolved failed', e.message || 'Network error');
    }
  }

  // ── Tier 2: Re-fire ──────────────────────────────────────────────────────
  // Reset a stuck/failed row back to 'scheduled' so the next scheduler sweep
  // retries it. Defense-in-depth: the scheduler is the only place that mints
  // real Stripe writes — re-fire just nudges the queue, can't double-charge.
  window.apOpenRefire = function(txnId) {
    const data = window._apLastDetail;
    const t = data && data.transaction;
    if (!t || t.id !== txnId) return;
    const formHtml = `
      <p style="font-size:0.75rem;color:var(--text-gray);margin:0 0 12px;">
        Reset this row to <code>scheduled</code> with <code>scheduled_process_at = now</code>.
        The next scheduler sweep (top of each hour) will retry the charge or
        transfer. Stripe idempotency uses <code>charge_attempts</code> — re-firing
        bumps it so a fresh key is generated and there's no double-charge risk.
      </p>
      <div style="font-size:0.72rem;color:var(--text-gray);margin-bottom:10px;">
        Current status: <strong style="color:var(--text);">${esc(t.status)}</strong>
        ${t.charge_failure_reason ? `<br>Last failure: <em>${esc(t.charge_failure_reason)}</em>` : ''}
      </div>
      <label style="display:block;margin-bottom:10px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Notes (optional)</div>
        <textarea id="apRefireNotes" rows="2" maxlength="500" placeholder="e.g. Card was declined as 'insufficient_funds' — venue confirmed they topped up the account."
          style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.82rem;box-sizing:border-box;resize:vertical;"></textarea>
      </label>
      <label style="display:flex;align-items:center;gap:8px;font-size:0.75rem;color:#fbbf24;padding:6px 8px;background:rgba(245,158,11,0.07);border:1px solid rgba(245,158,11,0.25);border-radius:5px;">
        <input id="apRefireDryRun" type="checkbox" style="width:auto;">
        <span><strong>Dry run</strong> — validate only; don't reset the row.</span>
      </label>`;
    window.showStyledModal(
      '↻ Re-fire — Transaction #' + txnId,
      formHtml,
      [
        { text: 'Cancel',  style: 'ghost',   onClick: () => {} },
        { text: 'Re-fire', style: 'primary', onClick: () => apSubmitRefire(txnId) },
      ],
      { tone: 'warning', size: 'md' }
    );
  };
  async function apSubmitRefire(txnId) {
    const notes = (document.getElementById('apRefireNotes')?.value || '').trim();
    const dry_run = !!document.getElementById('apRefireDryRun')?.checked;
    try {
      const res = await fetch('/api/admin/payments/' + txnId + '/refire', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes, dry_run }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Re-fire failed', json.detail || ('HTTP ' + res.status));
        return;
      }
      const prefix = json.dry_run ? '[DRY RUN] ' : '';
      const msg = json.dry_run
        ? `Validation passed. No DB write performed.`
        : `Row reset: ${json.from} → ${json.to}. Next scheduler sweep will retry.`;
      window.showSuccessModal && window.showSuccessModal(prefix + 'Re-fire',
        msg,
        () => { if (!json.dry_run) { apReload(); window.apShowDetail(txnId); } }
      );
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Re-fire failed', e.message || 'Network error');
    }
  }

  // ── Tier 2: Resend Email ─────────────────────────────────────────────────
  // Re-fires the same email the scheduler would have sent on the original
  // payout-time event. Useful when an SMTP hiccup means the venue never got
  // their "charged" notice, or an artist's spam filter ate their payout email.
  window.apOpenResend = function(txnId) {
    const data = window._apLastDetail;
    const t = data && data.transaction;
    if (!t || t.id !== txnId) return;
    // Offer only the templates that match this row shape.
    const opts = [];
    const parentTypes = new Set(['venue_charge', 'single']);
    if (parentTypes.has(t.transaction_type_resolved) && (t.status === 'paid' || t.status === 'charged')) {
      opts.push({ value: 'venue_charged',      label: 'Venue charged (to venue)' });
    }
    if (t.transaction_type_resolved === 'artist_payout' && (t.status === 'transferred' || t.status === 'paid')) {
      opts.push({ value: 'artist_payout_sent', label: 'Artist payout sent (to artist)' });
    }
    if (!opts.length) {
      window.showErrorModal && window.showErrorModal('Resend email',
        `No suitable template for type "${t.transaction_type_resolved}" + status "${t.status}".`);
      return;
    }
    const formHtml = `
      <p style="font-size:0.75rem;color:var(--text-gray);margin:0 0 12px;">
        Re-sends the same email the scheduler sent (or would have sent) when
        this transaction first completed. Renders the live template from the
        DB — byte-identical to the original.
      </p>
      <label style="display:block;margin-bottom:10px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Template</div>
        <select id="apResendTemplate"
          style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
          ${opts.map(o => `<option value="${o.value}">${esc(o.label)}</option>`).join('')}
        </select>
      </label>
      <label style="display:flex;align-items:center;gap:8px;font-size:0.75rem;color:#fbbf24;padding:6px 8px;background:rgba(245,158,11,0.07);border:1px solid rgba(245,158,11,0.25);border-radius:5px;">
        <input id="apResendDryRun" type="checkbox" style="width:auto;">
        <span><strong>Dry run</strong> — validate the template wiring; don't actually send.</span>
      </label>`;
    window.showStyledModal(
      '✉ Resend Email — Transaction #' + txnId,
      formHtml,
      [
        { text: 'Cancel', style: 'ghost',   onClick: () => {} },
        { text: 'Send',   style: 'primary', onClick: () => apSubmitResend(txnId) },
      ],
      { tone: 'info', size: 'md' }
    );
  };
  async function apSubmitResend(txnId) {
    const template = document.getElementById('apResendTemplate')?.value || '';
    const dry_run  = !!document.getElementById('apResendDryRun')?.checked;
    try {
      const res = await fetch('/api/admin/payments/' + txnId + '/resend-email', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template, dry_run }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Resend failed', json.detail || ('HTTP ' + res.status));
        return;
      }
      const prefix = json.dry_run ? '[DRY RUN] ' : '';
      const msg = json.dry_run
        ? 'Endpoint validation passed. No email sent.'
        : `Template "${json.template}" re-sent.`;
      window.showSuccessModal && window.showSuccessModal(
        prefix + 'Resend Email', msg,
        () => { if (!json.dry_run) window.apShowDetail(txnId); }
      );
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Resend failed', e.message || 'Network error');
    }
  }

  // ── Tier 3: Reverse Transfer ─────────────────────────────────────────────
  // Pulls a child artist_payout's funds from the artist's Connect balance
  // back to the platform. Doesn't refund the venue — that's a separate
  // Refund action on the parent.
  window.apOpenReverseTransfer = async function(txnId) {
    let data;
    try { data = await apEnsureDetailFor(txnId); }
    catch (e) {
      window.showErrorModal && window.showErrorModal('Reverse Transfer', e.message || 'Failed to load.');
      return;
    }
    const t = data.transaction;

    // ── Gather the parent + sibling payouts so admin can pick which slots
    // to reverse in one batch, and/or refund the venue. Works whether the
    // modal was opened from a child payout row OR from the gig-level
    // "↺ Refund / Reverse" button passing the parent's id.
    const siblings = data.siblings || [];
    // Parent is the venue_charge / single row with no parent_transaction_id.
    // Find it regardless of which row admin clicked.
    const parent = siblings.find(s =>
      !s.parent_transaction_id
      && (s.transaction_type === 'venue_charge' || s.transaction_type === 'single')
    ) || (
      // Fall back: if t itself is the parent, siblings might just contain t.
      (!t.parent_transaction_id && (t.transaction_type_resolved === 'venue_charge' || t.transaction_type_resolved === 'single'))
        ? t : null
    );
    if (!parent) {
      window.showErrorModal && window.showErrorModal('Refund / Reverse',
        'Could not resolve the venue charge for this gig. Reopen the transaction and try again.');
      return;
    }
    const parentCharge = parent.venue_charge_cents || parent.amount_cents || 0;
    const parentDollars = (parentCharge / 100).toFixed(2);
    const parentStatus = parent.status;
    const parentRefundable = (parent.transaction_type === 'venue_charge' || parent.transaction_type === 'single')
      && ['charged','paid','transferred'].includes(parentStatus);

    // All artist_payout children of this parent (ordered by id ASC so the
    // slot ↔ child pairing below is stable).
    const allChildren = siblings
      .filter(s => s.parent_transaction_id === parent.id
                && s.transaction_type === 'artist_payout')
      .sort((a, b) => a.id - b.id);

    // Pair each child with its corresponding gig_slot. Strategy: if slot
    // count matches child count, zip by slot_number ASC ↔ id ASC. Otherwise
    // (unusual — single artist on multiple slots, etc.) fall back to
    // matching by artist_id.
    const slots = (data.slots || []).slice().sort((a, b) => (a.slot_number || 0) - (b.slot_number || 0));
    const sameCount = slots.length === allChildren.length && allChildren.length > 0;
    function slotFor(child, idx) {
      if (sameCount) return slots[idx];
      return slots.find(s => s.artist_id === child.artist_id);
    }

    const REVERSIBLE = new Set(['transferred', 'paid']);
    const ALREADY_REVERSED = new Set(['payment_cancelled']);

    // Build a render-ready list with per-row reversibility flag.
    const rows = allChildren.map((c, i) => {
      const slot = slotFor(c, i) || null;
      const reversible = REVERSIBLE.has(c.status) && !!c.stripe_transfer_id;
      const alreadyReversed = ALREADY_REVERSED.has(c.status);
      const status = c.status;
      let reasonNotEnabled = '';
      if (!reversible) {
        if (alreadyReversed)               reasonNotEnabled = 'already reversed';
        else if (!c.stripe_transfer_id)    reasonNotEnabled = 'no transfer to reverse (scheduler hasn\'t fired yet)';
        else                                reasonNotEnabled = `status "${status}" can't be reversed`;
      }
      return {
        child_id: c.id,
        artist_id: c.artist_id,
        artist_name: (slot && slot.artist_name) || (c.id === t.id ? t.artist_name : null) || 'Artist',
        slot_number: slot ? slot.slot_number : (i + 1),
        start_time: slot ? slot.start_time : null,
        end_time: slot ? slot.end_time : null,
        payout_cents: c.artist_payout_cents || 0,
        status,
        reversible,
        reasonNotEnabled,
        // No "current row" concept anymore — modal is gig-level. Every
        // reversible row defaults to checked so admin can untick the ones
        // they want to keep.
        is_current: false,
      };
    });

    const reversibleCount = rows.filter(r => r.reversible).length;

    // Scheduled siblings that the "cancel pending payouts" checkbox in
    // Step 2 would clean up if the venue refund is full.
    const schedSibCount = allChildren.filter(s => s.status === 'scheduled').length;

    const gigTime = t.gig_start_time ? ' ' + fmtTime12(t.gig_start_time) : '';
    const gigLine = `#${t.gig_id}` + (t.gig_title ? ' — ' + t.gig_title : '')
                  + ' (' + fmtDate(t.gig_date) + gigTime + ')';

    const stepLabelCss = 'font-size:0.72rem;color:var(--cyan);font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px;';
    const stepCardCss  = 'margin-bottom:12px;padding:10px 12px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;';

    const sumRow = (label, val, color) => `
      <tr>
        <td style="padding:3px 8px 3px 0;color:var(--text-gray);font-size:0.72rem;white-space:nowrap;">${esc(label)}</td>
        <td style="padding:3px 0;font-size:0.78rem;color:${color || 'var(--text)'};">${val}</td>
      </tr>`;

    // Build the per-slot row markup.
    function rowHtml(r) {
      const payoutDollars = (r.payout_cents / 100).toFixed(2);
      const time = (r.start_time && r.end_time)
        ? `${fmtTime12(r.start_time)} – ${fmtTime12(r.end_time)}`
        : (r.start_time ? fmtTime12(r.start_time) : '');
      // Default all reversible rows to CHECKED — admin opened the gig-level
      // "↺ Refund / Reverse" so they likely want to undo most/all of it.
      // They can untick individual rows to keep them.
      const checked = r.reversible ? 'checked' : '';
      const disabled = r.reversible ? '' : 'disabled';
      const greyed = r.reversible
        ? `id="apRevRow_${r.child_id}" data-cid="${r.child_id}" data-max="${payoutDollars}"`
        : `data-disabled="1" style="opacity:0.45;"`;
      const statusBadge = r.reversible ? '' :
        `<span style="font-size:0.65rem;color:var(--text-gray);background:rgba(255,255,255,0.04);padding:2px 7px;border-radius:4px;border:1px solid var(--border);white-space:nowrap;">${esc(r.reasonNotEnabled)}</span>`;
      return `
        <div ${greyed} style="display:flex;align-items:center;gap:10px;padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.04);${r.reversible ? '' : 'opacity:0.45;'}">
          <input type="checkbox" class="apRevRowCb" data-cid="${r.child_id}"
            ${checked} ${disabled}
            onchange="apReverseRowToggle(${r.child_id}, this.checked)"
            style="width:auto;flex-shrink:0;cursor:${disabled ? 'not-allowed' : 'pointer'};">
          <span style="flex:0 0 56px;font-size:0.72rem;color:var(--text-gray);font-weight:600;">Slot ${esc(r.slot_number)}</span>
          <span style="flex:1;min-width:0;font-size:0.78rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
            <span style="color:var(--text-gray);">${esc(time)}</span>
            <span style="margin-left:8px;font-weight:500;">${esc(r.artist_name)}</span>
            <span style="color:var(--text-gray);margin-left:8px;font-size:0.7rem;">(payout #${r.child_id})</span>
          </span>
          ${r.reversible ? `
          <span style="font-size:0.72rem;color:var(--text-gray);">$${payoutDollars} →</span>
          <input type="number" class="apRevRowAmount" data-cid="${r.child_id}"
            step="0.01" min="0.01" max="${payoutDollars}" value="${payoutDollars}"
            ${checked ? '' : 'disabled'}
            oninput="apReverseRecomputeTotal()"
            style="width:90px;padding:5px 8px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.78rem;box-sizing:border-box;text-align:right;font-family:monospace;">`
          : statusBadge}
        </div>`;
    }

    const stepOneHasAny = rows.length > 0;
    const noReversibleMsg = (stepOneHasAny && reversibleCount === 0)
      ? `<div style="padding:10px;font-size:0.74rem;color:#fbbf24;background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.25);border-radius:5px;margin-bottom:10px;">
           None of these payouts are in a reversible state (need <code>transferred</code> or <code>paid</code> with a Stripe transfer id). If admin needs to back out the gig anyway, use ↩ Refund on the parent venue charge instead.
         </div>` : '';

    const formHtml = `
      <div style="margin-bottom:12px;padding:10px 12px;background:rgba(239,68,68,0.05);border:1px solid rgba(239,68,68,0.25);border-radius:6px;">
        <div style="font-size:0.7rem;color:#fca5a5;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">⚠ Destructive — moves money</div>
        <div style="font-size:0.75rem;color:var(--text);line-height:1.5;">
          Reversing pulls funds from selected artists' Stripe Connect balances back to the platform balance. The optional refund below sends funds from the platform back to the venue's card. If you skip the refund, the money stays in the GigsFill Stripe account.
        </div>
      </div>

      <div style="${stepCardCss}">
        <div style="${stepLabelCss}">Transaction Details</div>
        <table style="width:100%;border-collapse:collapse;">
          ${sumRow('Gig',              esc(gigLine))}
          ${sumRow('Venue',            esc(t.venue_name || '—'))}
          ${sumRow('Parent venue charge',
                   `<strong style="font-family:monospace;">$${parentDollars}</strong> · #${parent.id} (${esc(parentStatus)})`)}
          ${sumRow('Artist payouts on this gig', `${rows.length} (of which ${reversibleCount} reversible)`)}
        </table>
      </div>

      ${stepOneHasAny ? `
      <div style="${stepCardCss}">
        <div style="${stepLabelCss}">Step 1 — Select artist payouts to reverse</div>
        ${noReversibleMsg}
        <div style="border:1px solid var(--border);border-radius:6px;overflow:hidden;background:rgba(0,0,0,0.15);">
          ${rows.map(rowHtml).join('')}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;font-size:0.8rem;color:var(--text);">
          <span style="color:var(--text-gray);font-size:0.72rem;">Tick the checkbox to include a slot; edit the amount to partial-reverse.</span>
          <span><strong>Total reverse amount:</strong> <span id="apReverseTotal" style="font-family:monospace;font-weight:700;">$0.00</span></span>
        </div>
        <label style="display:block;margin-top:10px;">
          <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Notes (optional, applied to every reversal)</div>
          <textarea id="apReverseNotes" rows="2" maxlength="500" placeholder="e.g. Artist no-showed; venue wants payment back"
            style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.82rem;box-sizing:border-box;resize:vertical;"></textarea>
        </label>
      </div>` : `
      <div style="${stepCardCss}">
        <div style="${stepLabelCss}">Step 1 — Reverse artist transfers</div>
        <div style="font-size:0.72rem;color:var(--text-gray);">
          No artist payouts on this gig (single-slot row, or all children already handled). Use Step 2 below to refund the venue.
        </div>
      </div>`}

      ${parentRefundable ? `
      <div style="${stepCardCss}">
        <div style="${stepLabelCss}">Step 2 — Refund the venue</div>
        <div style="border:1px solid var(--border);border-radius:6px;overflow:hidden;background:rgba(0,0,0,0.15);">
          <div id="apReverseVenueRow" style="display:flex;align-items:center;gap:10px;padding:7px 10px;${(!stepOneHasAny || reversibleCount === 0) ? '' : 'opacity:0.55;'}">
            <input id="apReverseAlsoRefund" type="checkbox"
              ${(!stepOneHasAny || reversibleCount === 0) ? 'checked' : ''}
              onchange="apReverseToggleRefundSection()"
              style="width:auto;flex-shrink:0;cursor:pointer;">
            <span style="flex:0 0 56px;font-size:0.72rem;color:var(--text-gray);font-weight:600;">Venue</span>
            <span style="flex:1;min-width:0;font-size:0.78rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
              ${esc(t.venue_name || 'Venue')}
              <span style="color:var(--text-gray);margin-left:8px;font-size:0.7rem;">(charge #${parent.id})</span>
            </span>
            <span style="font-size:0.72rem;color:var(--text-gray);">$${parentDollars} →</span>
            <input id="apReverseRefundAmount" type="number"
              step="0.01" min="0.01" max="${parentDollars}" value="${parentDollars}"
              ${(!stepOneHasAny || reversibleCount === 0) ? '' : 'disabled'}
              style="width:90px;padding:5px 8px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.78rem;box-sizing:border-box;text-align:right;font-family:monospace;">
          </div>
        </div>
        <div id="apReverseRefundFields" style="display:${(!stepOneHasAny || reversibleCount === 0) ? '' : 'none'};margin-top:10px;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <label style="font-size:0.7rem;color:var(--text-gray);white-space:nowrap;">Refund reason:</label>
            <select id="apReverseRefundReason"
              style="flex:0 0 220px;padding:5px 8px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.78rem;box-sizing:border-box;">
              <option value="requested_by_customer">Requested by customer</option>
              <option value="duplicate">Duplicate</option>
              <option value="fraudulent">Fraudulent</option>
            </select>
          </div>
          ${schedSibCount > 0 ? `
          <label style="display:flex;align-items:flex-start;gap:8px;font-size:0.72rem;color:var(--text-gray);line-height:1.5;">
            <input id="apReverseRefundCancelKids" type="checkbox" checked style="width:auto;flex-shrink:0;margin-top:2px;">
            <span>Also cancel <strong>${schedSibCount}</strong> other scheduled payout${schedSibCount === 1 ? '' : 's'} on this gig.<br>
              <span style="color:var(--text-gray);">(Only takes effect if the refund is for the full charge. Prevents the scheduler from sending those payouts later for a gig that's being refunded.)</span>
            </span>
          </label>` : `
          <input type="hidden" id="apReverseRefundCancelKids" value="off">`}
          ${(parent.credit_card_fee_cents || 0) > 0 ? `
          <div style="margin-top:10px;padding:8px 10px;background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.25);border-radius:5px;font-size:0.7rem;color:#fbbf24;line-height:1.5;">
            <strong>Stripe fee absorbed:</strong> $${((parent.credit_card_fee_cents || 0) / 100).toFixed(2)}
            — Stripe keeps the original processing fee on any refund regardless of partial/full. The platform paid this on the original charge and does not recover it.
          </div>` : ''}
        </div>
      </div>` : `
      <div style="${stepCardCss}">
        <div style="${stepLabelCss}">Step 2 (optional) — Also refund the venue</div>
        <div style="font-size:0.72rem;color:var(--text-gray);">
          Combined venue refund unavailable: parent venue charge #${parent.id} is in status '${esc(parent.status)}'. To refund the venue separately, open the parent venue_charge row and use ↩ Refund.
        </div>
      </div>`}

      <label style="display:flex;align-items:flex-start;gap:10px;font-size:0.85rem;color:#fbbf24;margin-top:6px;padding:10px 12px;background:rgba(245,158,11,0.12);border:2px solid rgba(245,158,11,0.5);border-radius:6px;cursor:pointer;">
        <input id="apReverseDryRun" type="checkbox" style="width:18px;height:18px;margin-top:2px;flex-shrink:0;">
        <span><strong>Dry run (safe test mode)</strong><br>
          <span style="font-size:0.72rem;color:var(--text-gray);">Validates both steps end-to-end without calling Stripe or modifying the DB.</span>
        </span>
      </label>
      <div style="font-size:0.65rem;color:var(--text-gray);margin-top:8px;line-height:1.4;">
        <strong>Stripe fees:</strong> the original processing fee is NOT returned on a refund. The venue receives the refund amount above; the platform absorbs the CC fee from the original charge.
      </div>`;

    // Stash for the submit handler — needs to know parent_id + which child
    // IDs map to which inputs.
    window._apReverseCtx = {
      parent_id: parent.id,
      parentChargeCents: parentCharge,  // for Step-2 fallback when Step 1 empty
      child_ids: rows.filter(r => r.reversible).map(r => r.child_id),
      // Track whether admin manually edited Step 2 amount so auto-sync stops.
      refundManuallyEdited: false,
    };

    window.showStyledModal(
      '↺ Refund / Reverse — Gig #' + t.gig_id,
      formHtml,
      [
        { text: 'Cancel',  style: 'ghost',  onClick: () => {} },
        { text: 'Apply',   style: 'danger', onClick: () => apSubmitReverseTransfer(txnId) },
      ],
      { tone: 'warning', size: 'lg' }
    );

    // Initial total based on default-checked rows.
    setTimeout(apReverseRecomputeTotal, 0);
  };

  // Toggle a per-row checkbox: disable/enable that row's amount input and
  // recompute totals + grey-out styling.
  window.apReverseRowToggle = function(childId, checked) {
    const wrap  = document.getElementById('apRevRow_' + childId);
    const input = document.querySelector('.apRevRowAmount[data-cid="' + childId + '"]');
    if (input) input.disabled = !checked;
    if (wrap) wrap.style.opacity = checked ? '1' : '0.55';
    apReverseRecomputeTotal();
  };

  // Recompute Step 1 total and (if not manually edited) mirror it into the
  // Step 2 refund amount. When Step 1 has nothing checked, fall back to the
  // parent's full venue charge so the venue-refund-only flow still has a
  // sensible default (admin doesn't have to type the amount manually).
  window.apReverseRecomputeTotal = function() {
    let total = 0;
    document.querySelectorAll('.apRevRowCb').forEach(cb => {
      if (!cb.checked) return;
      const input = document.querySelector('.apRevRowAmount[data-cid="' + cb.dataset.cid + '"]');
      const v = input ? parseFloat(input.value || '0') : 0;
      if (Number.isFinite(v)) total += Math.round(v * 100);
    });
    const totalEl = document.getElementById('apReverseTotal');
    if (totalEl) totalEl.textContent = '$' + (total / 100).toFixed(2);
    const refund = document.getElementById('apReverseRefundAmount');
    if (refund && !window._apReverseCtx?.refundManuallyEdited) {
      const fallback = window._apReverseCtx?.parentChargeCents || 0;
      const cents = total > 0 ? total : fallback;
      refund.value = (cents / 100).toFixed(2);
    }
  };

  window.apReverseToggleRefundSection = function() {
    const cb = document.getElementById('apReverseAlsoRefund');
    const fields = document.getElementById('apReverseRefundFields');
    const row = document.getElementById('apReverseVenueRow');
    const amt = document.getElementById('apReverseRefundAmount');
    if (!cb) return;
    if (fields) fields.style.display = cb.checked ? '' : 'none';
    if (amt) amt.disabled = !cb.checked;
    if (row) row.style.opacity = cb.checked ? '1' : '0.55';
    if (cb.checked && window._apReverseCtx) {
      window._apReverseCtx.refundManuallyEdited = false;
      apReverseRecomputeTotal();
      if (amt && !amt._apEditWatch) {
        amt._apEditWatch = true;
        amt.addEventListener('input', () => {
          if (window._apReverseCtx) window._apReverseCtx.refundManuallyEdited = true;
        });
      }
    }
  };

  async function apSubmitReverseTransfer(txnId) {
    const ctx = window._apReverseCtx;
    if (!ctx || !ctx.parent_id) {
      window.showErrorModal && window.showErrorModal('Reverse', 'Modal state lost — reopen the transaction.');
      return;
    }

    // Gather every ticked row + its current amount.
    const reversals = [];
    document.querySelectorAll('.apRevRowCb').forEach(cb => {
      if (!cb.checked) return;
      const cid = parseInt(cb.dataset.cid, 10);
      const input = document.querySelector('.apRevRowAmount[data-cid="' + cid + '"]');
      const amt = input ? parseFloat(input.value || '0') : 0;
      if (!Number.isFinite(amt) || amt <= 0) return;
      reversals.push({ child_id: cid, amount_cents: Math.round(amt * 100) });
    });
    const alsoRefund = !!document.getElementById('apReverseAlsoRefund')?.checked;
    if (!reversals.length && !alsoRefund) {
      // Neither step is ticked — nothing to do. Tell admin so they don't
      // silently submit an empty action.
      window.showErrorModal && window.showErrorModal('Refund / Reverse',
        'Tick at least one payout to reverse, or tick "Refund the venue" to refund the venue.');
      return;
    }
    const body = {
      reversals,
      notes: (document.getElementById('apReverseNotes')?.value || '').trim(),
      dry_run: !!document.getElementById('apReverseDryRun')?.checked,
      also_refund_venue: alsoRefund,
    };
    if (alsoRefund) {
      const refundStr = (document.getElementById('apReverseRefundAmount')?.value || '').trim();
      const refundDollars = parseFloat(refundStr);
      if (!Number.isFinite(refundDollars) || refundDollars <= 0) {
        window.showErrorModal && window.showErrorModal('Reverse',
          'Enter a valid venue refund amount, or uncheck "Send funds back to the venue".');
        return;
      }
      body.refund_amount_cents = Math.round(refundDollars * 100);
      body.refund_reason       = document.getElementById('apReverseRefundReason')?.value || 'requested_by_customer';
      body.refund_cancel_kids  = !!document.getElementById('apReverseRefundCancelKids')?.checked;
    }

    try {
      const res = await fetch('/api/admin/payments/' + ctx.parent_id + '/reverse-batch', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Reverse failed',
          json.detail || ('HTTP ' + res.status));
        return;
      }
      const prefix = json.dry_run ? '[DRY RUN] ' : '';
      const failRows = (json.reversals || []).filter(r => !r.ok);
      const refundErr = json.refund && json.refund.error;
      const hasError = failRows.length > 0 || refundErr;
      if (hasError) {
        // Failures need admin attention — keep blocking modal with details.
        const lines = [`${json.ok_count} reversal${json.ok_count === 1 ? '' : 's'} processed, ${json.fail_count} failed.`];
        if (failRows.length) {
          lines.push('Failures:\n' + failRows.slice(0, 5).map(r => `  • payout #${r.child_id}: ${r.error}`).join('\n'));
        }
        if (json.refund) {
          if (refundErr) lines.push(`⚠ Refund step: ${refundErr}`);
          else lines.push(`Venue refund ${json.refund.stripe_refund_id || ''} for $${(json.refund.amount_cents/100).toFixed(2)} (${json.refund.is_full ? 'full' : 'partial'}) applied to parent #${json.refund.parent_txn_id}.`);
        }
        (window.showErrorModal || window.showStyledModal)(
          prefix + 'Refund / Reverse', lines.join('\n\n'),
          () => { if (!json.dry_run) { apReload(); window.apShowDetail(txnId); } }
        );
      } else {
        // Clean success → toast + reload.
        const parts = [];
        if (json.ok_count > 0) parts.push(`${json.ok_count} reversal${json.ok_count === 1 ? '' : 's'}`);
        if (json.refund && !refundErr) parts.push(`venue refund $${(json.refund.amount_cents/100).toFixed(2)}`);
        const summary = parts.length ? parts.join(' + ') : 'No-op';
        window.apToast(`${prefix}Refund / Reverse — ${summary}`, 'success');
        if (!json.dry_run) { apReload(); window.apShowDetail(txnId); }
      }
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Reverse failed', e.message || 'Network error');
    }
  }

  // ── Tier 3: Re-route Payout ──────────────────────────────────────────────
  // Changes the destination artist on a still-scheduled artist_payout child.
  // Use when the original artist's Connect account closed before the
  // scheduler fired the transfer.
  window.apOpenReroute = function(txnId) {
    const data = window._apLastDetail;
    const t = data && data.transaction;
    if (!t || t.id !== txnId) return;
    const formHtml = `
      <div style="margin-bottom:12px;padding:10px 12px;background:rgba(6,182,212,0.05);border:1px solid rgba(6,182,212,0.18);border-radius:6px;font-size:0.78rem;color:var(--text);line-height:1.5;">
        Change the destination artist on this still-scheduled payout. The new artist must have completed Stripe Connect onboarding — we'll reject otherwise. Current destination: <strong>${esc(t.artist_name || 'artist #' + t.artist_id)}</strong>.
      </div>
      <label style="display:block;margin-bottom:10px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">New artist id</div>
        <div style="display:flex;gap:6px;align-items:stretch;">
          <input id="apRerouteArtistId" type="number" min="1" placeholder="e.g. 42"
            style="flex:1;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
          <button type="button" onclick="apOpenFindArtist('apRerouteArtistId')"
            style="padding:7px 14px;background:rgba(139,92,246,0.15);border:1px solid rgba(139,92,246,0.45);border-radius:5px;color:#c4b5fd;font-size:0.75rem;font-weight:600;cursor:pointer;white-space:nowrap;">
            🔍 Find Artist ID
          </button>
        </div>
        <div id="apRerouteArtistPreview" style="font-size:0.7rem;color:var(--text-gray);margin-top:4px;"></div>
      </label>
      <label style="display:block;margin-bottom:10px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Reason (required, min 5 chars)</div>
        <textarea id="apRerouteReason" rows="2" maxlength="1000" placeholder="e.g. Original artist's Connect account was suspended by Stripe — band asked us to route to their backup account."
          style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.82rem;box-sizing:border-box;resize:vertical;"></textarea>
      </label>
      <label style="display:flex;align-items:flex-start;gap:10px;font-size:0.85rem;color:#fbbf24;margin-top:10px;padding:10px 12px;background:rgba(245,158,11,0.12);border:2px solid rgba(245,158,11,0.5);border-radius:6px;cursor:pointer;">
        <input id="apRerouteDryRun" type="checkbox" style="width:18px;height:18px;margin-top:2px;flex-shrink:0;">
        <span><strong>Dry run (safe test mode)</strong><br>
          <span style="font-size:0.72rem;color:var(--text-gray);">Validates the destination artist's Connect status without writing the change.</span>
        </span>
      </label>`;
    window.showStyledModal(
      '↪ Re-route Payout — Transaction #' + txnId,
      formHtml,
      [
        { text: 'Cancel',   style: 'ghost',   onClick: () => {} },
        { text: 'Re-route', style: 'primary', onClick: () => apSubmitReroute(txnId) },
      ],
      { tone: 'info', size: 'md' }
    );
  };
  async function apSubmitReroute(txnId) {
    const new_artist_id = parseInt(document.getElementById('apRerouteArtistId')?.value || '0', 10);
    const reason        = (document.getElementById('apRerouteReason')?.value || '').trim();
    const dry_run       = !!document.getElementById('apRerouteDryRun')?.checked;
    if (!new_artist_id) {
      window.showErrorModal && window.showErrorModal('Re-route', 'Enter the new artist id.');
      return;
    }
    if (reason.length < 5) {
      window.showErrorModal && window.showErrorModal('Re-route', 'Reason is required (min 5 chars).');
      return;
    }
    try {
      const res = await fetch('/api/admin/payments/' + txnId + '/reroute-payout', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_artist_id, reason, dry_run }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Re-route failed', json.detail || ('HTTP ' + res.status));
        return;
      }
      const prefix = json.dry_run ? '[DRY RUN] ' : '';
      const msg = json.dry_run
        ? `Validation passed. ${json.to_artist_name} (#${json.to_artist_id}) is a valid destination.`
        : `Payout re-routed from artist #${json.from_artist_id} → #${json.to_artist_id} (${json.to_artist_name}).`;
      window.showSuccessModal && window.showSuccessModal(prefix + 'Re-route',
        msg,
        () => { if (!json.dry_run) { apReload(); window.apShowDetail(txnId); } }
      );
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Re-route failed', e.message || 'Network error');
    }
  }

  // ── Find Artist ID (used by Re-route modal) ───────────────────────────────
  // Stacked modal that opens over the Re-route prompt. Loads the full
  // artist list once (cached on window for the page session), then filters
  // client-side by letter or substring. Modeled on the Payment Settings tab's
  // "Venue Free Trials" search but rendered inline in a gf-modal.
  window._apArtistCache = window._apArtistCache || null;

  window.apOpenFindArtist = function(targetInputId) {
    const ensureList = window._apArtistCache
      ? Promise.resolve(window._apArtistCache)
      : fetch('/api/admin/artists', { credentials: 'include' })
          .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
          .then(list => {
            window._apArtistCache = list.sort((a, b) =>
              String(a.name || '').localeCompare(String(b.name || ''))
            );
            return window._apArtistCache;
          });

    const formHtml = `
      <p style="font-size:0.75rem;color:var(--text);margin:0 0 10px;">
        Click an artist to autopopulate the id. Search by name or filter by first letter.
      </p>
      <input id="apFindArtistSearch" type="text" placeholder="🔍 Search artist by name, email, or id…"
        oninput="apFindArtistRender()"
        style="width:100%;padding:7px 10px;background:#151b28;border:1px solid #333;border-radius:5px;color:var(--text-white);font-size:0.8rem;box-sizing:border-box;margin-bottom:8px;">
      <div id="apFindArtistLetters" style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:8px;"></div>
      <div id="apFindArtistResults" style="max-height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:5px;">
        <div style="color:var(--text-gray);padding:14px;font-size:0.78rem;text-align:center;">Loading…</div>
      </div>
      <div id="apFindArtistInfo" style="font-size:0.65rem;color:var(--text-gray);margin-top:6px;text-align:center;"></div>`;

    window.showStyledModal(
      'Find Artist',
      formHtml,
      [ { text: 'Close', style: 'ghost', onClick: () => {} } ],
      { tone: 'info', size: 'md' }
    );

    window._apFindArtistTargetId = targetInputId;
    window._apFindArtistLetter = '';

    ensureList.then(() => {
      apFindArtistBuildLetters();
      apFindArtistRender();
    }).catch(e => {
      const wrap = document.getElementById('apFindArtistResults');
      if (wrap) wrap.innerHTML = `<div style="color:#ef4444;padding:10px;font-size:0.78rem;">Failed to load artists: ${esc(e.message)}</div>`;
    });
  };

  function apFindArtistBuildLetters() {
    const wrap = document.getElementById('apFindArtistLetters');
    if (!wrap) return;
    const present = new Set();
    (window._apArtistCache || []).forEach(a => {
      const c = (a.name || '').trim().charAt(0).toUpperCase();
      if (c >= 'A' && c <= 'Z') present.add(c);
      else if (c) present.add('#');
    });
    const letters = Array.from(present).sort();
    const btn = (label, value, isActive) => `
      <button onclick="apFindArtistSetLetter('${value}')"
        style="padding:3px 9px;font-size:0.7rem;border-radius:4px;cursor:pointer;
               ${isActive
                 ? 'background:rgba(245,158,11,0.2);border:1px solid rgba(245,158,11,0.6);color:#f59e0b;font-weight:700;'
                 : 'background:rgba(255,255,255,0.04);border:1px solid var(--border);color:var(--text-gray);'}">${esc(label)}</button>`;
    wrap.innerHTML = btn('All', '', window._apFindArtistLetter === '')
                   + letters.map(l => btn(l, l, window._apFindArtistLetter === l)).join('');
  }

  window.apFindArtistSetLetter = function(letter) {
    window._apFindArtistLetter = letter;
    const search = document.getElementById('apFindArtistSearch');
    if (search) search.value = '';
    apFindArtistBuildLetters();
    apFindArtistRender();
  };

  window.apFindArtistRender = function() {
    const wrap = document.getElementById('apFindArtistResults');
    const info = document.getElementById('apFindArtistInfo');
    if (!wrap) return;
    const q = (document.getElementById('apFindArtistSearch')?.value || '').trim().toLowerCase();
    const letter = window._apFindArtistLetter || '';
    if (q && letter) { window._apFindArtistLetter = ''; apFindArtistBuildLetters(); }

    let list = window._apArtistCache || [];
    if (q) {
      list = list.filter(a =>
        (a.name || '').toLowerCase().includes(q) ||
        (a.owner_email || '').toLowerCase().includes(q) ||
        String(a.id) === q
      );
    } else if (letter) {
      list = list.filter(a => {
        const c = (a.name || '').trim().charAt(0).toUpperCase();
        if (letter === '#') return !(c >= 'A' && c <= 'Z') && !!c;
        return c === letter;
      });
    }

    if (info) info.textContent = `${list.length} artist${list.length === 1 ? '' : 's'}` + (list.length > 200 ? ' (showing first 200)' : '');
    list = list.slice(0, 200);

    if (!list.length) {
      wrap.innerHTML = '<div style="color:var(--text-gray);padding:10px;font-size:0.78rem;text-align:center;">No artists match.</div>';
      return;
    }

    wrap.innerHTML = list.map(a => {
      const sub = [a.artist_type, a.city, a.state].filter(Boolean).join(' · ');
      return `
        <div onclick="apFindArtistPick(${a.id})"
          style="display:flex;align-items:center;gap:10px;padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.05);cursor:pointer;"
          onmouseover="this.style.background='rgba(6,182,212,0.06)'"
          onmouseout="this.style.background=''">
          <span style="display:inline-block;min-width:56px;padding:2px 6px;font-family:monospace;font-size:0.72rem;font-weight:700;color:#c4b5fd;background:rgba(139,92,246,0.12);border:1px solid rgba(139,92,246,0.3);border-radius:4px;text-align:center;">#${a.id}</span>
          <span style="flex:1;min-width:0;">
            <span style="font-size:0.82rem;color:var(--text);font-weight:500;">${esc(a.name || '(unnamed)')}</span>
            ${sub ? `<span style="display:block;font-size:0.66rem;color:var(--text-gray);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(sub)}</span>` : ''}
            ${a.owner_email ? `<span style="display:block;font-size:0.62rem;color:var(--text-gray);">${esc(a.owner_email)}</span>` : ''}
          </span>
        </div>`;
    }).join('');
  };

  window.apFindArtistPick = function(artistId) {
    const targetId = window._apFindArtistTargetId;
    const input = targetId && document.getElementById(targetId);
    if (input) {
      input.value = String(artistId);
      const a = (window._apArtistCache || []).find(x => x.id === artistId);
      const preview = document.getElementById('apRerouteArtistPreview');
      if (preview && a) {
        preview.innerHTML = `Selected: <strong style="color:var(--text);">${esc(a.name || '(unnamed)')}</strong> · #${a.id}`;
      }
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    // Close just the Find Artist modal; the Re-route modal beneath stays open.
    apCloseTopModal();
  };

  // Close the top-most gf-modal overlay so stacked modals work.
  // Prefer the close-X button so any teardown logic fires; otherwise remove.
  function apCloseTopModal() {
    const overlays = document.querySelectorAll('.gfm-modal-overlay');
    if (!overlays.length) return;
    const top = overlays[overlays.length - 1];
    const x = top.querySelector('.gfm-modal-close');
    if (x) x.click(); else top.remove();
  }

  // ── Tier 4: Bulk actions + Reconciliation ────────────────────────────────
  // Bulk state lives in this Set so it survives table re-renders within the
  // same page session. Clearing happens on tab switch / reload.
  const apSelected = new Set();
  window._apSelected = apSelected;

  window.apToggleRow = function(txnId, checked) {
    if (checked) apSelected.add(txnId); else apSelected.delete(txnId);
    apUpdateBulkBar();
  };
  window.apToggleAllOnPage = function(checked) {
    document.querySelectorAll('.ap-row-cb').forEach(cb => {
      cb.checked = checked;
      const id = parseInt(cb.dataset.txnId, 10);
      if (checked) apSelected.add(id); else apSelected.delete(id);
    });
    apUpdateBulkBar();
  };
  window.apClearSelection = function() {
    apSelected.clear();
    document.querySelectorAll('.ap-row-cb').forEach(cb => cb.checked = false);
    apUpdateBulkBar();
  };

  function apUpdateBulkBar() {
    const bar = document.getElementById('apBulkBar');
    if (!bar) return;
    if (apSelected.size === 0) { bar.style.display = 'none'; return; }
    // 'flex' (not '') because the bar relies on align-items + gap from
    // its inline style, which only apply when display:flex.
    bar.style.display = 'flex';
    const countEl = document.getElementById('apBulkCount');
    if (countEl) countEl.textContent = String(apSelected.size);
  }

  window.apOpenBulk = function() {
    if (apSelected.size === 0) return;
    const ids = Array.from(apSelected);
    const formHtml = `
      <p style="font-size:0.78rem;color:var(--text);margin:0 0 10px;">
        Apply a safe action to <strong>${ids.length}</strong> selected transaction${ids.length === 1 ? '' : 's'}. Refund / reverse-transfer are intentionally NOT bulk-available — those need per-row review.
      </p>
      <label style="display:block;margin-bottom:10px;">
        <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Action</div>
        <select id="apBulkAction" onchange="apBulkActionChanged()"
          style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
          <option value="refire">↻ Re-fire (reset failed → scheduled)</option>
          <option value="resend-email">✉ Resend Email</option>
          <option value="mark-resolved">✓ Mark Resolved</option>
        </select>
      </label>
      <div id="apBulkParams"></div>
      <label style="display:flex;align-items:flex-start;gap:10px;font-size:0.85rem;color:#fbbf24;margin-top:10px;padding:10px 12px;background:rgba(245,158,11,0.12);border:2px solid rgba(245,158,11,0.5);border-radius:6px;cursor:pointer;">
        <input id="apBulkDryRun" type="checkbox" checked style="width:18px;height:18px;margin-top:2px;flex-shrink:0;">
        <span><strong>Dry run (safe test mode)</strong><br>
          <span style="font-size:0.72rem;color:var(--text-gray);">Recommended for the first run — surfaces per-row errors without committing.</span>
        </span>
      </label>`;
    window.showStyledModal(
      'Bulk Action — ' + ids.length + ' transaction' + (ids.length === 1 ? '' : 's'),
      formHtml,
      [
        { text: 'Cancel', style: 'ghost',   onClick: () => {} },
        { text: 'Apply', style: 'primary', onClick: () => apSubmitBulk(ids) },
      ],
      { tone: 'info', size: 'md' }
    );
    setTimeout(apBulkActionChanged, 0);
  };

  window.apBulkActionChanged = function() {
    const action = document.getElementById('apBulkAction')?.value;
    const wrap = document.getElementById('apBulkParams');
    if (!wrap) return;
    if (action === 'mark-resolved') {
      wrap.innerHTML = `
        <label style="display:block;margin-bottom:8px;">
          <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">New status</div>
          <select id="apBulkNewStatus"
            style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
            ${['paid','transferred','payment_cancelled','suspended','dispute_won','dispute_lost']
              .map(s => `<option value="${s}">${s}</option>`).join('')}
          </select>
        </label>
        <label style="display:block;">
          <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Reason (required, min 5 chars)</div>
          <textarea id="apBulkReason" rows="2" maxlength="1000"
            style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.82rem;box-sizing:border-box;resize:vertical;"></textarea>
        </label>`;
    } else if (action === 'resend-email') {
      wrap.innerHTML = `
        <label style="display:block;">
          <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">Template</div>
          <select id="apBulkTemplate"
            style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
            <option value="venue_charged">Venue charged (to venue)</option>
            <option value="artist_payout_sent">Artist payout sent (to artist)</option>
          </select>
          <div style="font-size:0.65rem;color:var(--text-gray);margin-top:3px;">Rows that don't match this template will fail individually — that's fine, the batch continues.</div>
        </label>`;
    } else {
      wrap.innerHTML = '<div style="font-size:0.72rem;color:var(--text-gray);">No extra parameters. Re-fire works on rows in payment_failed / charge_retry / transfer_failed / pending_transfer.</div>';
    }
  };

  async function apSubmitBulk(ids) {
    const action  = document.getElementById('apBulkAction')?.value;
    const dry_run = !!document.getElementById('apBulkDryRun')?.checked;
    let params = {};
    if (action === 'mark-resolved') {
      const new_status = document.getElementById('apBulkNewStatus')?.value;
      const reason     = (document.getElementById('apBulkReason')?.value || '').trim();
      if (reason.length < 5) {
        window.showErrorModal && window.showErrorModal('Bulk', 'Reason required (min 5 chars).');
        return;
      }
      params = { new_status, reason };
    } else if (action === 'resend-email') {
      params = { template: document.getElementById('apBulkTemplate')?.value };
    }
    try {
      const res = await fetch('/api/admin/payments/bulk', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ txn_ids: ids, action, params, dry_run }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Bulk failed', json.detail || ('HTTP ' + res.status));
        return;
      }
      const prefix = json.dry_run ? '[DRY RUN] ' : '';
      const failRows = (json.results || []).filter(r => !r.ok);
      let failDetail = '';
      if (failRows.length) {
        failDetail = '<div style="margin-top:8px;font-size:0.7rem;color:var(--text-gray);max-height:200px;overflow:auto;">'
                   + failRows.slice(0, 20).map(r => `<div>• #${r.txn_id}: ${esc(r.error || 'unknown')}</div>`).join('')
                   + (failRows.length > 20 ? `<div>… and ${failRows.length - 20} more</div>` : '')
                   + '</div>';
      }
      const bodyHtml = `${prefix}${json.ok_count} succeeded, ${json.fail_count} failed (action: ${esc(action)}).${failDetail}`;
      window.showStyledModal('Bulk results', bodyHtml,
        [{ text: 'Close', style: 'primary', onClick: () => {
          if (!json.dry_run) { apClearSelection(); apReload(); }
        }}],
        { tone: failRows.length ? 'warning' : 'info', size: 'md' });
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Bulk failed', e.message || 'Network error');
    }
  }

  // ── Tier 4: Reconciliation report ────────────────────────────────────────
  // One-click "sync DB to Stripe" from a reconcile row. Hits the
  // single-row mark-resolved endpoint with the inferred target status
  // and a fixed reason so the audit trail is clear about where the
  // change came from.
  window.apReconSync = async function(txnId, newStatus, stripeState) {
    const reason = `Reconcile: Stripe state '${stripeState}' — syncing our DB to match.`;
    try {
      const res = await fetch('/api/admin/payments/' + txnId + '/mark-resolved', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_status: newStatus, reason }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.showErrorModal && window.showErrorModal('Sync failed',
          json.detail || ('HTTP ' + res.status));
        return;
      }
      window.apToast(`#${txnId} synced — ${json.from} → ${json.to}`, 'success');
      apReload();
      // Re-run the reconcile so the row drops off (or shows match).
      if (typeof window.apRunReconcile === 'function') window.apRunReconcile();
    } catch (e) {
      window.showErrorModal && window.showErrorModal('Sync failed', e.message || 'Network error');
    }
  };

  window.apOpenReconcile = function() {
    const today = new Date();
    const monthAgo = new Date(today); monthAgo.setDate(monthAgo.getDate() - 30);
    const fmt = d => d.toISOString().slice(0, 10);
    // The Run button is rendered INLINE in the form (not as a gf-modals
    // footer button) because gf-modals closes the modal when any footer
    // button fires — that would tear down #apReconResults before
    // apRunReconcile could write into it. The footer keeps only Close.
    const formHtml = `
      <p style="font-size:0.78rem;color:var(--text);margin:0 0 10px;">
        Compares our <code>transactions</code> table to Stripe (PaymentIntents + Transfers) for a gig-date window. Highlights status mismatches and amount divergences. Read-only — act on findings via the single-row actions on each row.
      </p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:8px;">
        <label>
          <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">From (gig date)</div>
          <input id="apReconFrom" type="date" value="${fmt(monthAgo)}"
            style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
        </label>
        <label>
          <div style="font-size:0.7rem;color:var(--text-gray);margin-bottom:3px;">To (gig date)</div>
          <input id="apReconTo" type="date" value="${fmt(today)}"
            style="width:100%;padding:7px 10px;background:#151b28;border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.85rem;box-sizing:border-box;">
        </label>
      </div>
      <label style="display:flex;align-items:center;gap:8px;font-size:0.75rem;color:var(--text-gray);">
        <input id="apReconOnlyMismatches" type="checkbox" checked style="width:auto;">
        Only show mismatches (uncheck to see every scanned row)
      </label>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:10px;">
        <div style="font-size:0.65rem;color:var(--text-gray);">
          Capped at 200 txns per run — narrow the date range for larger windows. Each row calls Stripe, so this takes a few seconds.
        </div>
        <button id="apReconRunBtn" onclick="apRunReconcile()"
          style="padding:7px 18px;background:linear-gradient(135deg,var(--purple),var(--cyan));color:#fff;border:none;border-radius:6px;font-size:0.8rem;font-weight:700;cursor:pointer;white-space:nowrap;">
          ▶ Run
        </button>
      </div>
      <div id="apReconResults" style="margin-top:12px;"></div>`;
    window.showStyledModal(
      'Stripe ⇄ DB Reconciliation',
      formHtml,
      [ { text: 'Close', style: 'ghost', onClick: () => {} } ],
      { tone: 'info', size: 'lg' }
    );
  };

  window.apRunReconcile = async function() {
    const fd = document.getElementById('apReconFrom')?.value;
    const td = document.getElementById('apReconTo')?.value;
    const om = !!document.getElementById('apReconOnlyMismatches')?.checked;
    const wrap = document.getElementById('apReconResults');
    const btn  = document.getElementById('apReconRunBtn');
    if (!fd || !td) { if (wrap) wrap.innerHTML = '<div style="color:#ef4444;padding:8px;font-size:0.78rem;">Pick both dates.</div>'; return; }
    if (btn) { btn.disabled = true; btn.style.opacity = '0.6'; btn.textContent = '⏳ Scanning…'; }
    if (wrap) wrap.innerHTML = '<div style="text-align:center;color:var(--text-gray);padding:16px;font-size:0.85rem;">Scanning… this may take a few seconds.</div>';
    try {
      const url = '/api/admin/payments/reports/reconcile?from_date=' + encodeURIComponent(fd)
                + '&to_date=' + encodeURIComponent(td)
                + '&only_mismatches=' + (om ? 'true' : 'false');
      const res = await fetch(url, { credentials: 'include' });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        wrap.innerHTML = `<div style="color:#ef4444;padding:8px;">${esc(json.detail || ('HTTP ' + res.status))}</div>`;
        return;
      }
      const m = json.mismatches || [];
      const header = `
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px;font-size:0.75rem;">
          <span style="padding:4px 10px;border-radius:4px;background:rgba(34,197,94,0.10);color:#22c55e;border:1px solid rgba(34,197,94,0.3);"><strong>${json.ok_count}</strong> match</span>
          <span style="padding:4px 10px;border-radius:4px;background:rgba(239,68,68,0.10);color:#ef4444;border:1px solid rgba(239,68,68,0.3);"><strong>${json.mismatch_count}</strong> mismatch</span>
          <span style="padding:4px 10px;border-radius:4px;background:rgba(148,163,184,0.10);color:var(--text-gray);border:1px solid var(--border);"><strong>${json.no_stripe_id}</strong> no Stripe id</span>
          <span style="padding:4px 10px;border-radius:4px;background:rgba(148,163,184,0.10);color:var(--text-gray);border:1px solid var(--border);">scanned ${json.scanned}</span>
          ${json.truncated ? '<span style="padding:4px 10px;border-radius:4px;background:rgba(245,158,11,0.10);color:#f59e0b;border:1px solid rgba(245,158,11,0.3);">⚠ truncated at ' + json.max_per_call + '</span>' : ''}
        </div>`;
      // Map Stripe's lifecycle state to our internal status when admin
      // wants to "sync with Stripe" in one click. Only set when we have an
      // unambiguous mapping; otherwise the row gets a — and admin uses
      // detail-modal Mark Resolved.
      function syncTarget(stripeState) {
        switch (stripeState) {
          case 'reversed':              return 'payment_cancelled';
          case 'canceled_or_refunded':  return 'payment_cancelled';
          case 'succeeded':             return null;  // ambiguous: paid OR transferred — admin picks
          default:                      return null;
        }
      }
      const rows = m.map(x => {
        const tgt = !x.match ? syncTarget(x.stripe_state) : null;
        const syncBtn = tgt
          ? `<button onclick="apReconSync(${x.txn_id}, '${tgt}', '${esc(x.stripe_state)}')"
               style="padding:3px 9px;background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.4);color:#22c55e;border-radius:4px;font-size:0.68rem;font-weight:600;cursor:pointer;white-space:nowrap;"
               title="Set status to '${esc(tgt)}' to match Stripe.">↺ Sync</button>`
          : '';
        return `<tr style="border-bottom:1px solid var(--border);${x.match ? '' : 'background:rgba(239,68,68,0.04);'}">
          <td style="padding:5px;"><a href="javascript:void(0)" onclick="window.apShowDetail(${x.txn_id})" style="color:var(--cyan);">#${x.txn_id}</a></td>
          <td style="padding:5px;">${esc(fmtDate(x.gig_date))}</td>
          <td style="padding:5px;">${esc(x.venue_name || '—')}</td>
          <td style="padding:5px;">${esc(x.artist_name || '—')}</td>
          <td style="padding:5px;">${esc(TYPE_LABEL[x.transaction_type] || x.transaction_type)}</td>
          <td style="padding:5px;">${statusPill(x.our_status)}</td>
          <td style="padding:5px;color:${x.match ? '#22c55e' : '#ef4444'};">${esc(x.stripe_state || '—')}</td>
          <td style="padding:5px;font-size:0.7rem;color:var(--text-gray);">${esc(x.summary || (x.match ? '✓' : ''))}</td>
          <td style="padding:5px;">${syncBtn}</td>
        </tr>`;
      }).join('');
      const table = `
        <table style="width:100%;border-collapse:collapse;font-size:0.75rem;">
          <thead><tr style="background:rgba(255,255,255,0.03);">
            <th style="padding:5px;text-align:left;color:var(--text-gray);">Txn</th>
            <th style="padding:5px;text-align:left;color:var(--text-gray);">Gig date</th>
            <th style="padding:5px;text-align:left;color:var(--text-gray);">Venue</th>
            <th style="padding:5px;text-align:left;color:var(--text-gray);">Artist</th>
            <th style="padding:5px;text-align:left;color:var(--text-gray);">Type</th>
            <th style="padding:5px;text-align:left;color:var(--text-gray);">Our status</th>
            <th style="padding:5px;text-align:left;color:var(--text-gray);">Stripe</th>
            <th style="padding:5px;text-align:left;color:var(--text-gray);">Notes</th>
            <th style="padding:5px;text-align:left;color:var(--text-gray);"></th>
          </tr></thead>
          <tbody>${rows || '<tr><td colspan="9" style="padding:12px;text-align:center;color:var(--text-gray);">No rows to show.</td></tr>'}</tbody>
        </table>`;
      wrap.innerHTML = header + table;
    } catch (e) {
      wrap.innerHTML = `<div style="color:#ef4444;padding:8px;">${esc(e.message || 'Network error')}</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.style.opacity = '1'; btn.textContent = '▶ Run'; }
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
