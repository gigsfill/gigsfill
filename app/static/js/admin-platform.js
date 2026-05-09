// ── Global admin modal/toast helpers ─────────────────────────────────────────

window._adminToast = function(msg, color) {
  const t = document.createElement('div');
  t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1a2235;border:1px solid ' + (color || 'var(--border)') + ';border-radius:8px;padding:10px 22px;color:' + (color || 'var(--cyan)') + ';font-size:0.82rem;font-weight:600;z-index:99999;box-shadow:0 4px 20px rgba(0,0,0,0.4);pointer-events:none;';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function() { t.remove(); }, 2800);
};

window._adminConfirm = function(opts) {
  var title = opts.title || '';
  var titleColor = opts.titleColor || 'var(--text-white)';
  var body = opts.body || '';
  var cancelLabel = opts.cancelLabel || 'Cancel';
  var confirmLabel = opts.confirmLabel !== undefined ? opts.confirmLabel : 'Confirm';
  var confirmColor = opts.confirmColor || 'rgba(6,182,212,0.25)';
  var onConfirm = opts.onConfirm;
  var onCancel = opts.onCancel;

  var id = '_adminConfirmModal';
  var existing = document.getElementById(id);
  if (existing) existing.remove();

  var ccBorder = confirmColor.replace(/,[\d.]+\)$/, ',0.5)');
  var ccText = confirmColor.indexOf('239,68') > -1 ? '#f87171'
             : confirmColor.indexOf('245,158') > -1 ? '#fbbf24'
             : confirmColor.indexOf('16,185') > -1 ? '#34d399'
             : 'var(--cyan)';

  var confirmBtnHtml = confirmLabel
    ? '<button id="' + id + '_confirm" style="padding:8px 22px;background:' + confirmColor + ';border:1px solid ' + ccBorder + ';border-radius:6px;color:' + ccText + ';font-size:0.8rem;font-weight:700;cursor:pointer;">' + confirmLabel + '</button>'
    : '';

  var backdrop = document.createElement('div');
  backdrop.id = id;
  backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:10000;display:flex;align-items:center;justify-content:center;padding:20px;box-sizing:border-box;';
  backdrop.innerHTML =
    '<div style="background:#1a2235;border:1px solid var(--border);border-radius:10px;padding:28px 32px;max-width:420px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5);">' +
      '<div style="font-size:1rem;font-weight:700;color:' + titleColor + ';margin-bottom:12px;">' + title + '</div>' +
      '<div style="font-size:0.83rem;color:var(--text-gray);line-height:1.6;margin-bottom:22px;">' + body + '</div>' +
      '<div style="display:flex;gap:10px;justify-content:center;">' +
        '<button id="' + id + '_cancel" style="padding:8px 20px;background:transparent;border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:var(--text-gray);font-size:0.8rem;cursor:pointer;">' + cancelLabel + '</button>' +
        confirmBtnHtml +
      '</div>' +
    '</div>';

  document.body.appendChild(backdrop);
  backdrop.addEventListener('click', function(e) { if (e.target === backdrop) { backdrop.remove(); if (onCancel) onCancel(); } });
  document.getElementById(id + '_cancel').onclick = function() { backdrop.remove(); if (onCancel) onCancel(); };
  var confirmBtn = document.getElementById(id + '_confirm');
  if (confirmBtn) confirmBtn.onclick = function() { backdrop.remove(); if (onConfirm) onConfirm(); };
  return backdrop;
};

/**
 * admin-platform.js
 * Platform Settings tab — Email Settings, Payment Settings, Venue Overrides,
 * Data Section (users/artists/venues/gigs bubbles), Accounting tab trigger.
 */

(function () {

  // ── Data Section (ps-stat bubbles) ─────────────────────────────────────────
  let _dsType    = '';
  let _dsData    = [];
  let _dsFiltered = [];
  let _dsPage    = 1;
  const DS_PER_PAGE = 20;

  window.toggleDataSection = async function (type) {
    const section = document.getElementById('dataSection');
    const title   = document.getElementById('dataTitle');
    const search  = document.getElementById('searchInput');

    if (_dsType === type && section && section.style.display !== 'none') {
      section.style.display = 'none';
      _dsType = '';
      return;
    }

    _dsType = type;
    if (section) section.style.display = '';
    if (search)  search.value = '';

    const labels = { users: 'Users', artists: 'Artists', venues: 'Venues', gigs: 'Gigs', booked_gigs: 'Booked Gigs' };
    if (title) title.textContent = labels[type] || type;

    const endpoint = `/api/admin/${type === 'booked_gigs' ? 'gigs' : type}`;
    const container = document.getElementById('dataTableContainer');
    if (container) container.innerHTML = '<div style="color:var(--text-gray);padding:20px;text-align:center;">Loading…</div>';

    try {
      const r = await fetch(endpoint, { credentials: 'include' });
      if (!r.ok) throw new Error(await r.text());
      let rawData = await r.json();
      // Use local date (not UTC) so today's gigs always appear
      const _now = new Date();
      const today = _now.getFullYear() + '-'
        + String(_now.getMonth() + 1).padStart(2, '0') + '-'
        + String(_now.getDate()).padStart(2, '0');
      if (type === 'booked_gigs') {
        _dsData = rawData.filter(g => g.status === 'booked' && g.date >= today);
      } else if (type === 'gigs') {
        _dsData = rawData.filter(g => ['open','booked','pending_contract'].includes(g.status) && g.date >= today);
      } else {
        _dsData = rawData;
      }
      // Init default sort for this type
      const defCols = DS_COLS[type];
      if (defCols) {
        const defCol = defCols.find(d => d[2]);  // find defaultSort=true
        if (defCol) {
          _dsSort = { col: defCol[0], dir: defCol[3] || 'asc' };
        } else {
          _dsSort = { col: null, dir: 'asc' };
        }
      } else {
        _dsSort = { col: null, dir: 'asc' };
      }
      _dsFiltered = [..._dsData];
      _dsPage = 1;
      _applyFilterSort();
      renderDsTable();
    } catch (e) {
      if (container) container.innerHTML = `<div style="color:#ef4444;padding:20px;">Error: ${e.message}</div>`;
    }
  };

  // Sort state per type
  let _dsSort = { col: null, dir: 'asc' };

  // Column definitions per type: [key, label, defaultSort, defaultDir]
  const DS_COLS = {
    users: [
      ['last_name',    'Last Name',   true,  'asc'],
      ['first_name',   'First Name',  false, 'asc'],
      ['email',        'Email',       false, 'asc'],
      ['artist_count', 'Artists',     false, 'asc'],
      ['venue_count',  'Venues',      false, 'asc'],
      ['created_at',   'Joined',      false, 'desc'],
    ],
    artists: [
      ['name',         'Artist Name', true,  'asc'],
      ['artist_type',  'Type',        false, 'asc'],
      ['city',         'City',        false, 'asc'],
      ['state',        'State',       false, 'asc'],
      ['owner_email',  'Email',       false, 'asc'],
      ['created_at',   'Joined',      false, 'desc'],
    ],
    venues: [
      ['venue_name',   'Venue Name',  true,  'asc'],
      ['city',         'City',        false, 'asc'],
      ['state',        'State',       false, 'asc'],
      ['owner_email',  'Email',       false, 'asc'],
      ['created_at',   'Joined',      false, 'desc'],
    ],
    gigs: [
      ['date',         'Date',        true,  'asc'],
      ['start_time',   'Time',        false, 'asc'],
      ['venue_name',   'Venue',       false, 'asc'],
      ['artist_name',  'Artist',      false, 'asc'],
      ['status',       'Status',      false, 'asc'],
      ['pay_dollars',  'Pay',         false, 'desc'],
    ],
    booked_gigs: [
      ['date',         'Date',        true,  'asc'],
      ['start_time',   'Time',        false, 'asc'],
      ['venue_name',   'Venue',       false, 'asc'],
      ['artist_name',  'Artist',      false, 'asc'],
      ['pay_dollars',  'Pay',         false, 'desc'],
    ],
  };

  window.dsSortBy = function(col) {
    if (_dsSort.col === col) {
      _dsSort.dir = _dsSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
      _dsSort.col = col;
      // default dir: desc for dates/pay, asc for names
      const isDesc = ['date','created_at','pay_dollars','last_login'].includes(col);
      _dsSort.dir = isDesc ? 'asc' : 'asc';  // user can toggle after
    }
    _dsPage = 1;
    _applyFilterSort();
    renderDsTable();
  };

  function _applyFilterSort() {
    const q = (document.getElementById('searchInput')?.value || '').toLowerCase();
    let data = q
      ? _dsData.filter(row => Object.values(row).some(v => v && String(v).toLowerCase().includes(q)))
      : [..._dsData];

    if (_dsSort.col) {
      const col = _dsSort.col;
      const dir = _dsSort.dir === 'asc' ? 1 : -1;
      data.sort((a, b) => {
        let va = a[col] ?? '';
        let vb = b[col] ?? '';
        if (col === 'date' || col === 'created_at' || col === 'last_login') {
          va = va ? new Date(va).getTime() : 0;
          vb = vb ? new Date(vb).getTime() : 0;
          return (va - vb) * dir;
        }
        if (col === 'pay_dollars') {
          return (Number(va) - Number(vb)) * dir;
        }
        return String(va).toLowerCase().localeCompare(String(vb).toLowerCase()) * dir;
      });
    }
    _dsFiltered = data;
  }

  function renderDsTable() {
    const container = document.getElementById('dataTableContainer');
    const pageInfo  = document.getElementById('pageInfo');
    const prevBtn   = document.getElementById('prevBtn');
    const nextBtn   = document.getElementById('nextBtn');
    if (!container) return;

    const totalPages = Math.max(1, Math.ceil(_dsFiltered.length / DS_PER_PAGE));
    if (_dsPage > totalPages) _dsPage = totalPages;
    const slice = _dsFiltered.slice((_dsPage - 1) * DS_PER_PAGE, _dsPage * DS_PER_PAGE);

    if (pageInfo) pageInfo.textContent = `Page ${_dsPage} of ${totalPages} (${_dsFiltered.length})`;
    if (prevBtn)  prevBtn.disabled = _dsPage <= 1;
    if (nextBtn)  nextBtn.disabled = _dsPage >= totalPages;

    if (!slice.length) {
      container.innerHTML = '<div style="color:var(--text-gray);padding:20px;text-align:center;">No results</div>';
      return;
    }

    const cols = DS_COLS[_dsType];
    if (!cols) {
      // Fallback: generic display
      const keys = Object.keys(slice[0]);
      const th = keys.map(k => `<th style="padding:6px 10px;text-align:left;font-size:0.7rem;color:var(--text-gray);border-bottom:1px solid var(--border);white-space:nowrap;">${k}</th>`).join('');
      const rows = slice.map(row => '<tr>' + keys.map(k => {
        const v = row[k]; const d = v == null ? '' : String(v);
        return `<td style="padding:5px 10px;font-size:0.75rem;border-bottom:1px solid rgba(255,255,255,0.04);white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis;">${esc(d)}</td>`;
      }).join('') + '</tr>').join('');
      container.innerHTML = `<table style="width:100%;border-collapse:collapse;"><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table>`;
      return;
    }

    const thStyle = 'padding:6px 10px;text-align:left;font-size:0.7rem;color:var(--text-gray);border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;';
    const th = cols.map(([key, label]) => {
      const arrow = _dsSort.col === key ? (_dsSort.dir === 'asc' ? ' ▲' : ' ▼') : '';
      return `<th style="${thStyle}" onclick="dsSortBy('${key}')">${label}${arrow}</th>`;
    }).join('');

    const _fmt12h = (t) => {
      if (!t) return '';
      const parts = String(t).substring(0,5).split(':');
      let h = parseInt(parts[0]), m = parts[1] || '00';
      const ampm = h >= 12 ? 'PM' : 'AM';
      h = h % 12 || 12;
      return h + ':' + m + ' ' + ampm;
    };
    const tdStyle = 'padding:5px 10px;font-size:0.75rem;border-bottom:1px solid rgba(255,255,255,0.04);white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis;';
    const rows = slice.map(row => '<tr>' + cols.map(([key]) => {
      let v = row[key] ?? '';
      if ((key === 'date' || key === 'created_at') && v) v = String(v).substring(0, 10);
      if (key === 'start_time') v = _fmt12h(v);
      if (key === 'pay_dollars' && v !== '') v = '$' + v;
      if (key === 'is_admin') v = v ? '✓' : '';
      return `<td style="${tdStyle}">${esc(String(v))}</td>`;
    }).join('') + '</tr>').join('');

    container.innerHTML = `<table style="width:100%;border-collapse:collapse;"><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  // Search box wired via oninput in HTML
  window.filterDsSearch = function (val) {
    _dsPage = 1;
    _applyFilterSort();
    renderDsTable();
  };

  window.changePage = function (dir) {
    _dsPage += dir;
    renderDsTable();
  };

  // Wire up search input (in case oninput="filterDsSearch(this.value)" isn't set in HTML)
  document.addEventListener('DOMContentLoaded', function () {
    const si = document.getElementById('searchInput');
    if (si && !si.getAttribute('oninput')) {
      si.addEventListener('input', function () { window.filterDsSearch(this.value); });
    }
  });

  // ── Load stat bubble counts ────────────────────────────────────────────────
  async function loadDashboardStats() {
    try {
      const [users, artists, venues, gigs] = await Promise.all([
        fetch('/api/admin/users',   { credentials: 'include' }).then(r => r.ok ? r.json() : []),
        fetch('/api/admin/artists', { credentials: 'include' }).then(r => r.ok ? r.json() : []),
        fetch('/api/admin/venues',  { credentials: 'include' }).then(r => r.ok ? r.json() : []),
        fetch('/api/admin/gigs',    { credentials: 'include' }).then(r => r.ok ? r.json() : []),
      ]);
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
      set('totalUsers',   users.length);
      set('totalArtists', artists.length);
      set('totalVenues',  venues.length);
      set('totalGigs',    gigs.length);
      set('bookedGigs',   gigs.filter(g => g.status === 'booked').length);
    } catch (e) { console.error('loadDashboardStats:', e); }
  }

  // ── Server Health ──────────────────────────────────────────────────────────
  window.loadServerHealth = async function() {
    try {
      const r = await fetch('/api/admin/system-health', { credentials: 'include' });
      if (!r.ok) return;
      const d = await r.json();

      const panel   = document.getElementById('serverHealthPanel');
      const alerts  = document.getElementById('serverAlerts');
      const warns   = document.getElementById('serverWarnings');
      if (!panel) return;
      panel.style.display = '';

      // ── Alerts ──────────────────────────────────────────────────────────
      if (d.alerts && d.alerts.length) {
        alerts.style.display = '';
        alerts.innerHTML = d.alerts.map(a =>
          `<div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.4);border-radius:7px;padding:10px 14px;font-size:0.82rem;color:#fca5a5;margin-bottom:6px;">
            ${a}
            ${d.upgrade_path ? `<div style="margin-top:6px;font-size:0.75rem;color:#f87171;">💡 ${d.upgrade_path}</div>` : ''}
          </div>`
        ).join('');
      } else {
        alerts.style.display = 'none';
        alerts.innerHTML = '';
      }

      // ── Warnings ────────────────────────────────────────────────────────
      if (d.warnings && d.warnings.length) {
        warns.style.display = '';
        warns.innerHTML = d.warnings.map(w =>
          `<div style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.35);border-radius:7px;padding:10px 14px;font-size:0.82rem;color:#fcd34d;margin-bottom:6px;">${w}</div>`
        ).join('');
      } else {
        warns.style.display = 'none';
        warns.innerHTML = '';
      }

      // ── Metric bars ─────────────────────────────────────────────────────
      function _bar(pct, label) {
        const col = pct >= 90 ? '#ef4444' : pct >= 70 ? '#f59e0b' : '#22c55e';
        return `<span style="display:inline-flex;align-items:center;gap:6px;">
          <span style="color:var(--text-muted);font-size:0.7rem;">${label}</span>
          <span style="display:inline-block;width:60px;height:6px;background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden;">
            <span style="display:block;width:${pct}%;height:100%;background:${col};border-radius:3px;transition:width .3s;"></span>
          </span>
          <span style="color:${col};font-weight:600;">${pct}%</span>
        </span>`;
      }

      const mem  = document.getElementById('shMemBar');
      const cpu  = document.getElementById('shCpuBar');
      const disk = document.getElementById('shDiskBar');
      const db   = document.getElementById('shDbBar');
      const red  = document.getElementById('shRedisBar');
      const dbt  = document.getElementById('shDbType');

      if (mem && d.memory_pct !== null)
        mem.innerHTML = _bar(d.memory_pct, `RAM ${d.memory_used_mb}/${d.memory_total_mb}MB`);
      if (cpu && d.cpu_pct !== null)
        cpu.innerHTML = _bar(d.cpu_pct, 'CPU');
      if (disk && d.disk_pct !== null)
        disk.innerHTML = _bar(d.disk_pct, 'Disk');
      if (db) {
        if (d.db_size_mb !== null)
          db.innerHTML = `<span style="color:var(--text-muted);font-size:0.7rem;">DB</span> <span style="color:var(--text);font-size:0.78rem;">${d.db_size_mb}MB</span>`;
        else
          db.innerHTML = '';
      }
      if (red)
        red.innerHTML = d.redis
          ? `<span style="color:#22c55e;font-size:0.78rem;">● Redis</span>`
          : `<span style="color:#f59e0b;font-size:0.78rem;">● Redis offline</span>`;
      if (dbt)
        dbt.innerHTML = d.db_type === 'postgresql'
          ? `<span style="color:#22c55e;font-size:0.78rem;">● PostgreSQL</span>`
          : `<span style="color:#f59e0b;font-size:0.78rem;">● SQLite</span>`;

      const wkEl = document.getElementById('shWorkers');
      if (wkEl && d.workers)
        wkEl.innerHTML = `<span style="color:var(--text-muted);font-size:0.7rem;">Workers</span> <span style="color:var(--text);font-size:0.78rem;">${d.workers}</span>`;

      // ── Upgrade nudge in metrics bar ─────────────────────────────────────
      const existingNudge = document.getElementById('_shUpgradeNudge');
      if (existingNudge) existingNudge.remove();
      if (d.upgrade_recommended && d.upgrade_path) {
        const nudge = document.createElement('div');
        nudge.id = '_shUpgradeNudge';
        nudge.style.cssText = 'width:100%;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.08);font-size:0.75rem;color:#fcd34d;';
        nudge.innerHTML = `💡 <strong>Upgrade recommended:</strong> ${d.upgrade_path}`;
        document.getElementById('serverMetrics').appendChild(nudge);
      }

    } catch(e) { console.error('loadServerHealth:', e); }
  };

  // ── Email Settings ──────────────────────────────────────────────────────────
  async function loadEmailSettings() {
    try {
      const r = await fetch('/api/admin/settings', { credentials: 'include' });
      if (!r.ok) return;
      const d = await r.json();
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
      set('platformEmailFromName', d.platform_email_from_name);
      set('platformEmail',         d.platform_email);
      set('platformEmailPassword', d.platform_email_password);
      set('platformSmtpServer',    d.platform_smtp_server);
      set('platformSmtpPort',      d.platform_smtp_port);
      set('supportEmailFromName',  d.support_email_from_name);
      set('supportEmail',          d.support_email);
      set('supportEmailPassword',  d.support_email_password);
      set('supportSmtpServer',     d.support_smtp_server);
      set('supportSmtpPort',       d.support_smtp_port);
      set('adminAlertEmail',       d.admin_alert_email);
    } catch (e) { console.error('loadEmailSettings:', e); }
  }

  window.autoSaveSettings = async function () {
    const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const payload = {
      platform_email_from_name: get('platformEmailFromName'),
      platform_email:           get('platformEmail'),
      platform_email_password:  get('platformEmailPassword'),
      platform_smtp_server:     get('platformSmtpServer'),
      platform_smtp_port:       get('platformSmtpPort'),
      support_email_from_name:  get('supportEmailFromName'),
      support_email:            get('supportEmail'),
      support_email_password:   get('supportEmailPassword'),
      support_smtp_server:      get('supportSmtpServer'),
      support_smtp_port:        get('supportSmtpPort'),
      admin_alert_email:        get('adminAlertEmail'),
    };
    try {
      await fetch('/api/admin/settings', {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      flashSaved('autoSaveIndicator');
    } catch (e) { console.error('autoSaveSettings:', e); }
  };

  // ── Payment Settings ───────────────────────────────────────────────────────
  window.loadPaymentSettings = async function () {
    try {
      const r = await fetch('/api/admin/payment-settings', { credentials: 'include' });
      if (!r.ok) return;
      const d = await r.json();

      const enabled = d.payments_enabled === 'true' || d.payments_enabled === true || d.payments_enabled === '1';
      setToggle(enabled);

      // Mirror status in the Payment subtab banner
      const mirror = document.getElementById('paymentStatusMirror');
      if (mirror) {
        mirror.textContent = enabled ? '✅ Live Payments ON' : '⏸ Live Payments OFF';
        mirror.style.color = enabled ? '#10b981' : '#ef4444';
      }

      const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
      set('stripePublishableKey', d.admin_stripe_publishable_key);
      set('stripeSecretKey',      d.admin_stripe_secret_key);
      set('stripeWebhookSecret',  d.admin_stripe_webhook_secret);
      set('platformFeePercent',   d.platform_fee_percent);
      set('platformMinFee',       d.platform_min_fee);
      set('paymentDelayDays',     d.payment_processing_delay_days);

      const split = document.getElementById('platformFeeSplit');
      if (split && d.platform_fee_split) split.value = d.platform_fee_split;

      const hour = document.getElementById('paymentProcessingHour');
      if (hour && d.payment_processing_hour) hour.value = d.payment_processing_hour;
    } catch (e) { console.error('loadPaymentSettings:', e); }
  };

  window.autoSavePaymentSettings = async function () {
    const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const enabled = document.getElementById('paymentsEnabled');
    const payload = {
      payments_enabled: enabled ? (enabled.checked ? 'true' : 'false') : 'false',
    };
    // Only include fee fields if they have a non-empty value — prevents wiping DB values
    // when user hasn't opened the Payment subtab yet and fields are blank
    const feePercent = get('platformFeePercent');
    const feeSplit   = get('platformFeeSplit');
    const minFee     = get('platformMinFee');
    const delayDays  = get('paymentDelayDays');
    const payHour    = get('paymentProcessingHour');
    if (feePercent !== '')  payload.platform_fee_percent           = feePercent;
    if (feeSplit)           payload.platform_fee_split             = feeSplit;
    if (minFee !== '')      payload.platform_min_fee               = minFee;
    if (delayDays !== '')   payload.payment_processing_delay_days  = delayDays;
    if (payHour)            payload.payment_processing_hour        = payHour;
    // Only include Stripe keys if they are non-empty and not masked (avoid overwriting with blanks)
    const pub = get('stripePublishableKey');
    const sec = get('stripeSecretKey');
    const whk = get('stripeWebhookSecret');
    if (pub && !pub.startsWith('•')) payload.admin_stripe_publishable_key = pub;
    if (sec && !sec.startsWith('•')) payload.admin_stripe_secret_key      = sec;
    if (whk && !whk.startsWith('•')) payload.admin_stripe_webhook_secret  = whk;
    try {
      await fetch('/api/admin/payment-settings', {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      flashSaved('paymentSaveIndicator');
    } catch (e) { console.error('autoSavePaymentSettings:', e); }
  };

  window.togglePaymentsEnabled = async function () {
    const cb = document.getElementById('paymentsEnabled');
    if (!cb) return;
    const enabled = cb.checked;
    setToggle(enabled);
    // Save ONLY the payments_enabled flag — do NOT send empty stripe keys
    // (the full payment fields may not be loaded yet if user hasn't clicked Payment subtab)
    try {
      const r = await fetch('/api/admin/payment-settings', {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payments_enabled: enabled ? 'true' : 'false' })
      });
      if (r.ok) {
        window.flashSaved('siteStatusSaved');
        window._adminToast(
          enabled ? '⚡ Live payments are now ON' : '⏸ Live payments are now OFF',
          enabled ? 'rgba(16,185,129,0.8)' : 'rgba(245,158,11,0.8)'
        );
        const mirror = document.getElementById('paymentStatusMirror');
        if (mirror) { mirror.textContent = enabled ? '✅ Live Payments ON' : '⏸ Live Payments OFF'; mirror.style.color = enabled ? '#10b981' : '#ef4444'; }
      } else {
        const err = await r.json().catch(() => ({}));
        window._adminToast('Save failed: ' + (err.detail || r.status), 'rgba(239,68,68,0.8)');
        // Revert toggle visually
        setToggle(!enabled);
      }
    } catch(e) { window._adminToast('Save failed: ' + e.message, 'rgba(239,68,68,0.8)'); }
  };

  window.setToggle = function setToggle(enabled) {
    const cb     = document.getElementById('paymentsEnabled');
    const track  = document.getElementById('paymentsToggleTrack');
    const thumb  = document.getElementById('paymentsToggleThumb');
    const label  = document.getElementById('paymentsStatusLabel');
    if (cb)    cb.checked = enabled;
    if (track) track.style.background = enabled ? '#10b981' : '#333';
    if (thumb) thumb.style.left = enabled ? '19px' : '3px';  // 36px track - 14px thumb - 3px pad
    if (label) { label.textContent = enabled ? 'ON' : 'OFF'; label.style.color = enabled ? '#10b981' : '#ef4444'; }
  }

  // ── Venue Payment Overrides ────────────────────────────────────────────────
  window.loadVenuePaymentOverrides = async function () {
    await Promise.all([loadLetterBar(), searchVenueOverrides('')]);
  };

  async function loadLetterBar() {
    const bar = document.getElementById('letterBar');
    if (!bar) return;
    try {
      const r = await fetch('/api/admin/venue-payment-overrides/letters', { credentials: 'include' });
      if (!r.ok) return;
      const letters = await r.json();
      // Add "All" button first
      let html = `<button onclick="searchVenueOverrides('')" style="padding:2px 7px;font-size:0.7rem;background:rgba(6,182,212,0.15);border:1px solid rgba(6,182,212,0.4);border-radius:4px;color:var(--cyan);cursor:pointer;font-weight:600;">All</button> `;
      html += letters.map(l => {
        const glow = l.active
          ? 'background:rgba(245,158,11,0.2);border-color:rgba(245,158,11,0.6);color:#f59e0b;font-weight:700;box-shadow:0 0 6px rgba(245,158,11,0.4);'
          : 'background:rgba(255,255,255,0.04);border:1px solid var(--border);color:var(--text-gray);';
        return `<button data-letter="${l.letter}" onclick="setActiveLetter(this,'${l.letter}')" style="padding:2px 7px;font-size:0.7rem;${glow}border-radius:4px;cursor:pointer;">${l.letter}</button>`;
      }).join('');
      bar.innerHTML = html;
    } catch (e) { console.error('loadLetterBar:', e); }
  }

  window.searchVenueOverrides = async function (query, letter) {
    const list = document.getElementById('venueOverrideList');
    const info = document.getElementById('venueResultsInfo');
    if (!list) return;
    list.innerHTML = '<div style="color:var(--text-gray);padding:10px;font-size:0.75rem;">Loading…</div>';
    try {
      const url = letter
        ? `/api/admin/venues/search?letter=${encodeURIComponent(letter)}`
        : query
          ? `/api/admin/venues/search?q=${encodeURIComponent(query)}`
          : '/api/admin/venue-payment-overrides';
      const r = await fetch(url, { credentials: 'include' });
      if (!r.ok) throw new Error(await r.text());
      const venues = await r.json();
      if (info) {
        info.style.display = venues.length ? '' : 'none';
        info.textContent = `${venues.length} venue${venues.length !== 1 ? 's' : ''}`;
      }
      if (!venues.length) {
        list.innerHTML = '<div style="color:var(--text-gray);padding:10px;font-size:0.75rem;">No venues found</div>';
        return;
      }
      list.innerHTML = venues.map(v => {
        const suspended = v.payments_suspended;
        const sub = [v.city, v.state, v.owner_email].filter(Boolean).join(' · ');
        return `
          <div style="display:flex;align-items:center;gap:10px;padding:7px 10px;border-bottom:1px solid rgba(255,255,255,0.05);">
            <span style="flex:1;min-width:0;">
              <span style="font-size:0.78rem;color:var(--text-white);font-weight:500;">${v.venue_name || ''}</span>
              ${sub ? `<span style="display:block;font-size:0.65rem;color:var(--text-gray);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${sub}</span>` : ''}
              ${suspended && v.notes ? `<span style="display:block;font-size:0.63rem;color:#f59e0b;font-style:italic;margin-top:1px;">${v.notes}</span>` : ''}
            </span>
            <span style="font-size:0.7rem;white-space:nowrap;color:${suspended ? '#f59e0b' : 'var(--text-gray)'};">${suspended ? '🎟 Free Trial' : 'Standard'}</span>
            <button onclick="toggleVenueOverride(${v.id}, ${!suspended}, this.dataset.vname)" data-vname="${(v.venue_name||'')}"

              style="white-space:nowrap;padding:3px 10px;font-size:0.7rem;border-radius:5px;cursor:pointer;
                     background:${suspended ? 'rgba(239,68,68,0.15)' : 'rgba(245,158,11,0.15)'};
                     border:1px solid ${suspended ? 'rgba(239,68,68,0.4)' : 'rgba(245,158,11,0.4)'};
                     color:${suspended ? '#ef4444' : '#f59e0b'};">
              ${suspended ? 'Remove Free Trial' : 'Set Free Trial'}
            </button>
          </div>`;
      }).join('');
    } catch (e) {
      list.innerHTML = `<div style="color:#ef4444;padding:10px;font-size:0.75rem;">Error: ${e.message}</div>`;
    }
  };

  async function _refreshVenueList() {
    await loadLetterBar();
    const search = document.getElementById('venueOverrideSearch');
    const q = search ? search.value.trim() : '';
    const activeBtn = document.querySelector('#letterBar button.ltr-active');
    const letter = activeBtn ? activeBtn.dataset.letter : '';
    await window.searchVenueOverrides(q, letter);
  }

  window.toggleVenueOverride = async function (venueId, suspend, venueName) {
    const name = venueName || 'This venue';
    if (suspend) {
      window._adminConfirm({
        title: '🎟 Set Free Trial',
        titleColor: '#f59e0b',
        body: '<strong style="color:var(--text-white);">' + name + '</strong> will have automated payments suspended.',
        cancelLabel: 'Cancel',
        confirmLabel: '✓ Enable Free Trial',
        confirmColor: 'rgba(245,158,11,0.25)',
        onConfirm: async function() {
          const inp = document.getElementById('_ftNote');
          const notes = inp ? inp.value.trim() : '';
          try {
            const r = await fetch('/api/admin/venue-payment-overrides', {
              method: 'POST', credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ venue_id: venueId, payments_suspended: true, notes: notes })
            });
            if (!r.ok) throw new Error(await r.text());
            await _refreshVenueList();
          } catch (e) { window._adminToast('Error: ' + e.message, 'rgba(239,68,68,0.8)'); }
        }
      });
      // Inject notes input below the body text
      setTimeout(function() {
        const bd = document.querySelector('#_adminConfirmModal div[style*="line-height:1.6"]');
        if (bd && !document.getElementById('_ftNote')) {
          const d = document.createElement('div');
          d.style.cssText = 'margin-top:12px;';
          d.innerHTML = '<label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:4px;">Notes (optional)</label>' +
            '<input id="_ftNote" type="text" placeholder="e.g. Free trial through June 2026" ' +
            'style="width:100%;padding:8px 10px;background:#0f1624;border:1px solid var(--border);border-radius:6px;color:var(--text-white);font-size:0.78rem;box-sizing:border-box;">';
          bd.parentNode.insertBefore(d, bd.nextSibling);
          document.getElementById('_ftNote').focus();
        }
      }, 40);
    } else {
      window._adminConfirm({
        title: '🔓 Remove Free Trial',
        titleColor: '#06b6d4',
        body: 'Remove free trial from <strong style="color:var(--text-white);">' + name + '</strong>?<br><br>Automated payments will resume.',
        cancelLabel: 'Keep Free Trial',
        confirmLabel: '✓ Remove Free Trial',
        confirmColor: 'rgba(239,68,68,0.25)',
        onConfirm: async function() {
          try {
            const r = await fetch('/api/admin/venue-payment-overrides', {
              method: 'POST', credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ venue_id: venueId, payments_suspended: false })
            });
            if (!r.ok) throw new Error(await r.text());
            await _refreshVenueList();
          } catch (e) { window._adminToast('Error: ' + e.message, 'rgba(239,68,68,0.8)'); }
        }
      });
    }
  };

  // Wire venue search input
  document.addEventListener('DOMContentLoaded', function () {
    const vs = document.getElementById('venueOverrideSearch');
    if (vs && !vs.getAttribute('oninput')) {
      vs.addEventListener('input', function () { window.searchVenueOverrides(this.value.trim()); });
    }
  });


  window.setActiveLetter = function(btn, letter) {
    // Mark active letter button
    document.querySelectorAll('#letterBar button').forEach(b => b.classList.remove('ltr-active'));
    if (btn) btn.classList.add('ltr-active');
    // Clear search input when clicking a letter
    if (letter) {
      const si = document.getElementById('venueOverrideSearch');
      if (si) si.value = '';
    }
    window.searchVenueOverrides('', letter);
  };

  // ── Helpers ────────────────────────────────────────────────────────────────
  window.flashSaved = function flashSaved(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.opacity = '1';
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.style.opacity = '0'; }, 2000);
  }

  // ── Init on platform tab open ──────────────────────────────────────────────
  // Hook into switchTab
  const _origSwitchTab = window.switchTab;
  if (typeof _origSwitchTab === 'function') {
    window.switchTab = function (tab) {
      _origSwitchTab(tab);
      if (tab === 'platform') {
        loadDashboardStats();
        loadServerHealth();
        loadEmailSettings();
        loadSiteStatusToggles();
        // Payment settings load when user clicks Payment subtab (via switchPsTab)
      }
    };
  }

  // ── Site Status Toggles ───────────────────────────────────────────────────────
  // Quick-access toggles in the status strip at the top of Platform Settings.

  async function loadSiteStatusToggles() {
    try {
      // Load payment settings — populate both the toggle AND the form fields
      const pay = await fetch('/api/admin/payment-settings', { credentials: 'include' })
        .then(r => r.ok ? r.json() : {});
      const paymentsOn = pay.payments_enabled === 'true' || pay.payments_enabled === true || pay.payments_enabled === '1';
      setToggle(paymentsOn);  // setToggle lives in the IIFE — updates #paymentsEnabled + track + thumb + label

      // Pre-populate payment form fields so they're ready when user clicks Payment subtab
      // (avoids the "blank fields wipe DB values" bug)
      const setField = (id, v) => { const el = document.getElementById(id); if (el && v) el.value = v; };
      setField('stripePublishableKey', pay.admin_stripe_publishable_key);
      setField('stripeSecretKey',      pay.admin_stripe_secret_key);
      setField('stripeWebhookSecret',  pay.admin_stripe_webhook_secret);
      setField('platformFeePercent',   pay.platform_fee_percent);
      setField('platformMinFee',       pay.platform_min_fee);
      setField('paymentDelayDays',     pay.payment_processing_delay_days);
      const split = document.getElementById('platformFeeSplit');
      if (split && pay.platform_fee_split) split.value = pay.platform_fee_split;
      const hour = document.getElementById('paymentProcessingHour');
      if (hour && pay.payment_processing_hour) hour.value = pay.payment_processing_hour;

      // Load affiliate enabled state
      const aff = await fetch('/api/admin/affiliate/settings', { credentials: 'include' })
        .then(r => r.ok ? r.json() : {});
      const affEnabled = aff.affiliate_enabled === 'true' || aff.affiliate_enabled === '1';
      _setQuickToggle('affQuick', affEnabled, 'ON', 'OFF', '#10b981', '#ef4444');

      // Load signups & maintenance from platform settings
      const ps = await fetch('/api/admin/settings', { credentials: 'include' })
        .then(r => r.ok ? r.json() : {});
      const signupsOpen = ps.signups_enabled !== false && ps.signups_enabled !== 'false' && ps.signups_enabled !== '0';
      _setQuickToggle('signups', signupsOpen, 'OPEN', 'CLOSED', '#10b981', '#ef4444', true);
      const maintOn = ps.maintenance_mode === true || ps.maintenance_mode === 'true' || ps.maintenance_mode === '1';
      _setQuickToggle('maint', maintOn, 'ON', 'OFF', '#f59e0b', '#6b7280');
      const maintMsgEl = document.getElementById('maintMessage');
      if (maintMsgEl) maintMsgEl.value = ps.maintenance_message || '';
    } catch(e) { console.error('loadSiteStatusToggles:', e); }
  }

  window._setQuickToggle = function(prefix, enabled, onLabel, offLabel, onColor, offColor, invertTrack) {
    const label = document.getElementById(prefix + 'QuickLabel') || document.getElementById(prefix + 'Label');
    const track = document.getElementById(prefix + 'ToggleTrack') || document.getElementById(prefix + 'Track');
    const thumb = document.getElementById(prefix + 'ToggleThumb') || document.getElementById(prefix + 'Thumb');
    const cb    = document.getElementById(prefix + 'QuickEnabled') || document.getElementById(prefix + 'Enabled');
    if (label) { label.textContent = enabled ? onLabel : offLabel; label.style.color = enabled ? onColor : offColor; }
    if (track) track.style.background = enabled ? onColor : (invertTrack ? offColor : '#333');
    if (thumb) thumb.style.left = enabled ? 'calc(100% - 17px)' : '3px';
    if (cb)    cb.checked = enabled;
  }


  // Also load on DOMContentLoaded since platform is the default active tab
  document.addEventListener('DOMContentLoaded', function () {
    loadDashboardStats();
    loadServerHealth();
    loadEmailSettings();
    loadSiteStatusToggles();
  });

})();




window.toggleAffQuick = async function() {
  const enabled = document.getElementById('affQuickEnabled')?.checked;
  _setQuickToggle('affQuick', enabled, 'ON', 'OFF', '#10b981', '#ef4444');
  try {
    const affR = await fetch('/api/admin/affiliate/settings', {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ affiliate_enabled: enabled ? 'true' : 'false' })
    });
    if (!affR.ok) { window._adminToast('Save failed: ' + affR.status, 'rgba(239,68,68,0.8)'); return; }
    // Mirror to the affiliate settings tab toggle if it exists
    const affCb = document.getElementById('affEnabled');
    const affLabel = document.getElementById('affEnabledLabel');
    const affTrack = document.getElementById('affEnabledTrack');
    const affThumb = document.getElementById('affEnabledThumb');
    if (affCb) affCb.checked = enabled;
    if (affLabel) { affLabel.textContent = enabled ? 'ON' : 'OFF'; affLabel.style.color = enabled ? '#10b981' : '#ef4444'; }
    if (affTrack) affTrack.style.background = enabled ? '#10b981' : '#4b5563';
    if (affThumb) affThumb.style.left = enabled ? '19px' : '3px';
    window.flashSaved('siteStatusSaved');
  } catch(e) { window._adminToast('Save failed: ' + e.message, 'rgba(239,68,68,0.8)'); }
};

window.toggleSignupsEnabled = async function() {
  const enabled = document.getElementById('signupsEnabled')?.checked;
  _setQuickToggle('signups', enabled, 'OPEN', 'CLOSED', '#10b981', '#ef4444', true);
  try {
    const sigR = await fetch('/api/admin/settings', {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signups_enabled: enabled ? 'true' : 'false' })
    });
    if (!sigR.ok) { window._adminToast('Save failed: ' + sigR.status, 'rgba(239,68,68,0.8)'); return; }
    window.flashSaved('siteStatusSaved');
    if (!enabled) {
      window._adminToast('⚠ Signups are now CLOSED — new users cannot register', 'rgba(239,68,68,0.8)');
    }
  } catch(e) { window._adminToast('Save failed: ' + e.message, 'rgba(239,68,68,0.8)'); }
};

window.toggleMaintenance = async function() {
  const enabled = document.getElementById('maintEnabled')?.checked;
  _setQuickToggle('maint', enabled, 'ON', 'OFF', '#f59e0b', '#6b7280');
  try {
    const _maintMsg = document.getElementById('maintMessage')?.value || '';
    const maintR = await fetch('/api/admin/settings', {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ maintenance_mode: enabled ? 'true' : 'false', maintenance_message: _maintMsg })
    });
    if (!maintR.ok) { window._adminToast('Save failed: ' + maintR.status, 'rgba(239,68,68,0.8)'); return; }
    window.flashSaved('siteStatusSaved');
    if (enabled) {
      window._adminToast('🚧 Maintenance mode ON — users will see maintenance page', 'rgba(245,158,11,0.9)');
    }
  } catch(e) { window._adminToast('Save failed: ' + e.message, 'rgba(239,68,68,0.8)'); }
};

window.saveMaintMessage = async function() {
  const msg = document.getElementById('maintMessage')?.value || '';
  try {
    const r = await fetch('/api/admin/settings', {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ maintenance_message: msg })
    });
    if (!r.ok) { window._adminToast('Save failed: ' + r.status, 'rgba(239,68,68,0.8)'); return; }
    window.flashSaved('siteStatusSaved');
  } catch(e) { window._adminToast('Save failed: ' + e.message, 'rgba(239,68,68,0.8)'); }
};

// Expose flashSaved for the status strip
window._flashSiteStatusSaved = function() { flashSaved('siteStatusSaved'); };

// ── SMTP Test ─────────────────────────────────────────────────────────────────

window.testSmtp = async function () {
  const toEmail  = (document.getElementById('smtpTestEmail')?.value || '').trim();
  const btn      = document.getElementById('smtpTestBtn');
  const resultEl = document.getElementById('smtpTestResult');

  if (!toEmail || !toEmail.includes('@')) {
    if (resultEl) { resultEl.style.color = '#ef4444'; resultEl.textContent = 'Enter a valid email address first.'; }
    return;
  }

  const origText = btn ? btn.textContent : 'Send Test';
  if (btn) { btn.disabled = true; btn.textContent = 'Sending…'; }
  if (resultEl) { resultEl.style.color = 'var(--text-gray)'; resultEl.textContent = 'Sending test email…'; }

  try {
    const r = await fetch('/api/admin/test-smtp', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to: toEmail }),
    });
    const d = await r.json();
    if (resultEl) {
      if (d.ok) {
        resultEl.style.color = '#10b981';
        resultEl.textContent = '✓ ' + (d.message || 'Test email sent!');
      } else {
        resultEl.style.color = '#ef4444';
        resultEl.textContent = '✗ ' + (d.error || d.detail || 'Send failed — check SMTP settings.');
      }
    }
  } catch (e) {
    if (resultEl) { resultEl.style.color = '#ef4444'; resultEl.textContent = '✗ Request failed: ' + e.message; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origText; }
  }
};
