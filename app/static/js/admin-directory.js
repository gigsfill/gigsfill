// admin-directory.js — Directory tab (Users / Artists / Venues).
//
// Fetches the rich payloads from /api/admin/directory/{type}, keeps them
// cached client-side, and renders sortable/filterable/paginated tables per
// sub-tab. Row click → detail modal that shows every field and (for artists
// + venues) a link to the public profile page. Users get a delete button on
// each row that fires the admin cascade endpoint added in the same batch.
//
// Kept in its own file (not folded into admin-init.js or admin-platform.js)
// because both of those are already ~1000+ lines each and this feature
// has enough per-type surface area to warrant separation.
(function () {
  'use strict';

  // Per-type state. Cache the last server response so sub-tab switches
  // and pagination don't re-hit the endpoint.
  const state = {
    users:   { data: [], filtered: [], page: 1, perPage: 25, search: '', sort: { col: 'created_at', dir: -1 } },
    artists: { data: [], filtered: [], page: 1, perPage: 25, search: '', sort: { col: 'created_at', dir: -1 } },
    venues:  { data: [], filtered: [], page: 1, perPage: 25, search: '', sort: { col: 'created_at', dir: -1 } },
  };
  let activeType = 'users';

  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const jsAttr = s => String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  const dollars = c => '$' + ((c || 0) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtDate = d => {
    if (!d) return '—';
    // Server returns ISO with or without Z; normalize.
    try {
      const dt = new Date(String(d).includes('T') ? d : d + 'Z');
      if (isNaN(dt.getTime())) return String(d).slice(0, 10);
      return dt.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
    } catch (_) { return String(d).slice(0, 10); }
  };
  const fmtDateTime = d => {
    if (!d) return '—';
    try {
      const dt = new Date(String(d).includes('T') ? d : d + 'Z');
      if (isNaN(dt.getTime())) return String(d);
      return dt.toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch (_) { return String(d); }
  };
  const yesNo = v => v ? '<span style="color:#22c55e;">✓</span>' : '<span style="color:#ef4444;">✕</span>';
  const stars = r => {
    if (r == null) return '<span style="color:var(--text-muted);">—</span>';
    const rounded = Math.round(r);
    return '<span style="color:#f59e0b;">' + '★'.repeat(rounded) + '<span style="color:var(--text-muted);">' + '☆'.repeat(5 - rounded) + '</span></span> <span style="color:var(--text-muted);font-size:0.7rem;">' + r.toFixed(1) + '</span>';
  };

  // Column definitions per type. Each entry: [key, label, renderer?, sortAccessor?]
  const COLS = {
    users: [
      ['last_name',   'Name',       r => esc(((r.first_name || '') + ' ' + (r.last_name || '')).trim() || '—') + (r.is_admin ? ' <span style="color:#a78bfa;font-size:0.68rem;font-weight:700;">ADMIN</span>' : ''), r => (r.last_name || '') + ' ' + (r.first_name || '')],
      ['email',       'Email',      r => `<a href="mailto:${esc(r.email)}" onclick="event.stopPropagation();" style="color:var(--cyan);text-decoration:none;">${esc(r.email)}</a>`],
      ['phone',       'Phone',      r => esc(r.phone || '—')],
      ['artist_count','Artists',    r => `<span style="color:${r.artist_count ? '#a78bfa' : 'var(--text-muted)'};font-weight:600;">${r.artist_count || 0}</span>`, r => r.artist_count || 0],
      ['venue_count', 'Venues',     r => `<span style="color:${r.venue_count ? 'var(--cyan)' : 'var(--text-muted)'};font-weight:600;">${r.venue_count || 0}</span>`, r => r.venue_count || 0],
      ['gigs_booked_as_artist', 'Gigs (A)', r => r.gigs_booked_as_artist || 0, r => r.gigs_booked_as_artist || 0],
      ['gigs_posted_as_venue',  'Gigs (V)', r => r.gigs_posted_as_venue  || 0, r => r.gigs_posted_as_venue  || 0],
      ['lifetime_venue_spend_cents', 'Lifetime Spend',    r => `<span style="color:#10b981;">${dollars(r.lifetime_venue_spend_cents)}</span>`,   r => r.lifetime_venue_spend_cents || 0],
      ['lifetime_artist_earnings_cents', 'Lifetime Earn', r => `<span style="color:#06b6d4;">${dollars(r.lifetime_artist_earnings_cents)}</span>`, r => r.lifetime_artist_earnings_cents || 0],
      ['last_login',  'Last Login', r => fmtDateTime(r.last_login), r => r.last_login || ''],
      ['created_at',  'Joined',     r => fmtDate(r.created_at),     r => r.created_at || ''],
    ],
    artists: [
      ['name',         'Artist Name', r => esc(r.name)],
      ['artist_type',  'Type',        r => esc(r.artist_type || '—')],
      ['band_formats', 'Lineup',      r => esc(r.band_formats || '—')],
      ['styles',       'Styles',      r => esc(r.styles || '—')],
      ['city',         'City',        r => esc(r.city || '—')],
      ['state',        'State',       r => esc(r.state || '—')],
      ['avg_rating',   'Rating',      r => stars(r.avg_rating), r => r.avg_rating || 0],
      ['review_count', '# Reviews',   r => r.review_count || 0, r => r.review_count || 0],
      ['gigs_booked',  'Gigs Booked', r => r.gigs_booked || 0,  r => r.gigs_booked || 0],
      ['lifetime_payouts_cents', 'Lifetime Payouts', r => `<span style="color:#06b6d4;">${dollars(r.lifetime_payouts_cents)}</span>`, r => r.lifetime_payouts_cents || 0],
      ['stripe_connect_ok', 'Stripe', r => yesNo(r.stripe_connect_ok), r => r.stripe_connect_ok ? 1 : 0],
      ['owner_email',  'Owner',       r => esc(r.owner_email || '—')],
      ['created_at',   'Joined',      r => fmtDate(r.created_at), r => r.created_at || ''],
    ],
    venues: [
      ['venue_name',   'Venue Name',  r => esc(r.venue_name)],
      ['city',         'City',        r => esc(r.city || '—')],
      ['state',        'State',       r => esc(r.state || '—')],
      ['venue_size',   'Size',        r => esc(r.venue_size || '—')],
      ['default_pay',  'Default Pay', r => `$${r.default_pay_dollars || 0}.${String(r.default_pay_cents || 0).padStart(2, '0')}`, r => (r.default_pay_dollars || 0) * 100 + (r.default_pay_cents || 0)],
      ['avg_rating',   'Rating',      r => stars(r.avg_rating), r => r.avg_rating || 0],
      ['review_count', '# Reviews',   r => r.review_count || 0, r => r.review_count || 0],
      ['gigs_posted',  'Gigs Posted', r => r.gigs_posted || 0,  r => r.gigs_posted || 0],
      ['gigs_booked',  'Gigs Booked', r => r.gigs_booked || 0,  r => r.gigs_booked || 0],
      ['lifetime_spend_cents', 'Lifetime Spend', r => `<span style="color:#10b981;">${dollars(r.lifetime_spend_cents)}</span>`, r => r.lifetime_spend_cents || 0],
      ['payment_method_ok', 'Payment', r => yesNo(r.payment_method_ok), r => r.payment_method_ok ? 1 : 0],
      ['zero_pay_booking_count', 'Zero-Pay', r => r.zero_pay_booking_count > 0 ? `<span style="color:#ef4444;">${r.zero_pay_booking_count}</span>` : '—', r => r.zero_pay_booking_count || 0],
      ['owner_email',  'Owner',       r => esc(r.owner_email || '—')],
      ['created_at',   'Joined',      r => fmtDate(r.created_at), r => r.created_at || ''],
    ],
  };

  // ── Fetch + cache ────────────────────────────────────────────────────────
  window._dirLoad = async function (type, forceReload) {
    const s = state[type];
    if (!s) return;
    if (s.data.length > 0 && !forceReload) { _applyAndRender(type); return; }
    const container = $('dir' + capitalize(type) + 'Table');
    if (container) container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:24px;">Loading…</p>';
    try {
      const res = await fetch('/api/admin/directory/' + type, { credentials: 'include' });
      if (!res.ok) {
        if (container) container.innerHTML = `<p style="color:#ef4444;text-align:center;padding:24px;">Failed to load (HTTP ${res.status}).</p>`;
        return;
      }
      s.data = await res.json();
      s.page = 1;
      _applyAndRender(type);
      // Update sub-tab counts.
      const badge = $('dir' + capitalize(type) + 'Count');
      if (badge) badge.textContent = '(' + s.data.length + ')';
    } catch (e) {
      if (container) container.innerHTML = `<p style="color:#ef4444;text-align:center;padding:24px;">${esc(e.message)}</p>`;
    }
  };

  // Called from the search input.
  window._dirFilter = function (type, val) {
    const s = state[type];
    if (!s) return;
    s.search = (val || '').toLowerCase();
    s.page = 1;
    _applyAndRender(type);
  };

  function _applyAndRender(type) {
    const s = state[type];
    const q = s.search;
    s.filtered = q
      ? s.data.filter(row => Object.values(row).some(v => v != null && String(v).toLowerCase().includes(q)))
      : [...s.data];
    if (s.sort.col) {
      const accessor = (COLS[type].find(c => c[0] === s.sort.col) || [])[3]
        || (r => r[s.sort.col]);
      s.filtered.sort((a, b) => {
        const av = accessor(a) ?? '';
        const bv = accessor(b) ?? '';
        if (typeof av === 'number' && typeof bv === 'number') return s.sort.dir * (av - bv);
        return s.sort.dir * String(av).localeCompare(String(bv));
      });
    }
    _renderTable(type);
  }

  window._dirSort = function (type, col) {
    const s = state[type];
    if (!s) return;
    if (s.sort.col === col) s.sort.dir *= -1;
    else { s.sort.col = col; s.sort.dir = 1; }
    _applyAndRender(type);
  };

  window._dirPage = function (type, delta) {
    const s = state[type];
    if (!s) return;
    const totalPages = Math.max(1, Math.ceil(s.filtered.length / s.perPage));
    s.page = Math.max(1, Math.min(s.page + delta, totalPages));
    _renderTable(type);
  };

  function _renderTable(type) {
    const s = state[type];
    const cols = COLS[type];
    const container = $('dir' + capitalize(type) + 'Table');
    const pager = $('dir' + capitalize(type) + 'Pagination');
    if (!container) return;
    if (!s.filtered.length) {
      container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:24px;">No ' + type + ' match this search.</p>';
      if (pager) pager.innerHTML = '';
      return;
    }
    const totalPages = Math.max(1, Math.ceil(s.filtered.length / s.perPage));
    if (s.page > totalPages) s.page = totalPages;
    const start = (s.page - 1) * s.perPage;
    const slice = s.filtered.slice(start, start + s.perPage);
    const hdr = 'padding:7px 8px;text-align:left;font-size:0.65rem;font-weight:700;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;';
    const arrow = key => s.sort.col === key ? (s.sort.dir === 1 ? ' ▲' : ' ▼') : '';
    let head = cols.map(([key, label]) =>
      `<th style="${hdr}" onclick="window._dirSort('${type}','${key}')">${label}${arrow(key)}</th>`
    ).join('');
    // Trailing "actions" column: trash for users only.
    if (type === 'users') head += `<th style="${hdr}text-align:right;"></th>`;
    const td = 'padding:6px 8px;font-size:0.72rem;color:var(--text);border-bottom:1px solid rgba(255,255,255,0.04);white-space:nowrap;';
    const body = slice.map(row => {
      const cells = cols.map(([key, , render]) => {
        const html = render ? render(row) : esc(String(row[key] ?? '—'));
        return `<td style="${td}">${html}</td>`;
      }).join('');
      let actions = '';
      if (type === 'users') {
        const uid = parseInt(row.id, 10) || 0;
        const email = jsAttr(row.email || '');
        actions = `<td style="padding:6px 4px;text-align:right;border-bottom:1px solid rgba(255,255,255,0.04);">
          <button onclick="event.stopPropagation();window.adminDeleteUser(${uid}, '${email}')"
                  title="Force-delete this user (cascades to their artists / venues)"
                  style="background:transparent;border:0;color:#94a3b8;font-size:0.85rem;cursor:pointer;padding:2px 6px;border-radius:4px;line-height:1;"
                  onmouseover="this.style.color='#fca5a5';this.style.background='rgba(239,68,68,0.1)';"
                  onmouseout="this.style.color='#94a3b8';this.style.background='transparent';">🗑</button>
        </td>`;
      }
      return `<tr onclick="window._dirShowDetail('${type}', ${parseInt(row.id, 10) || 0})" style="cursor:pointer;" onmouseover="this.style.background='rgba(255,255,255,0.03)';" onmouseout="this.style.background='';">${cells}${actions}</tr>`;
    }).join('');
    container.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:0.72rem;">
      <thead><tr style="background:rgba(255,255,255,0.02);">${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>`;
    if (pager) {
      const from = start + 1;
      const to = Math.min(start + s.perPage, s.filtered.length);
      const btn = (label, delta, disabled) =>
        `<button onclick="${disabled ? '' : `window._dirPage('${type}',${delta})`}" ${disabled ? 'disabled' : ''}
                 style="padding:4px 10px;background:rgba(255,255,255,0.05);border:1px solid var(--border);border-radius:5px;color:var(--text-white);font-size:0.7rem;cursor:${disabled ? 'not-allowed' : 'pointer'};opacity:${disabled ? '0.4' : '1'};">${label}</button>`;
      pager.innerHTML = `<span>${from}–${to} of ${s.filtered.length.toLocaleString()}</span>
        ${btn('‹ Prev', -1, s.page <= 1)}
        <span>Page ${s.page} of ${totalPages}</span>
        ${btn('Next ›', +1, s.page >= totalPages)}`;
    }
  }

  // ── Detail modal ─────────────────────────────────────────────────────────
  window._dirShowDetail = function (type, id) {
    const row = state[type].data.find(r => r.id === id);
    if (!row) return;
    let profileLink = '';
    let title = '';
    if (type === 'users') {
      title = ((row.first_name || '') + ' ' + (row.last_name || '')).trim() || row.email || 'User';
    } else if (type === 'artists') {
      title = row.name || 'Artist';
      // Note: handler looks up the entity name from state cache — passing
      // it inline would require JSON-encoded quote escaping that doesn't
      // survive the onclick="..." attribute cleanly and breaks the DOM.
      // event.stopPropagation on button click — without it the click
      // bubbles to the detail-modal backdrop and closes the modal
      // before the transfer flow starts.
      profileLink = `<a href="/app/artist-profile.html?artist_id=${row.id}" target="_blank" style="display:inline-block;padding:6px 14px;background:rgba(139,92,246,0.2);border:1px solid rgba(139,92,246,0.45);border-radius:6px;color:#c4b5fd;font-size:0.78rem;font-weight:600;text-decoration:none;">Open Profile ↗</a>
        <button onclick="event.stopPropagation();window._dirAdminTransferOwner('artist', ${row.id})" style="padding:6px 14px;background:rgba(245,158,11,0.15);border:1px solid rgba(245,158,11,0.45);border-radius:6px;color:#fbbf24;font-size:0.78rem;font-weight:600;cursor:pointer;">⚡ Transfer Owner</button>`;
    } else if (type === 'venues') {
      title = row.venue_name || 'Venue';
      profileLink = `<a href="/app/venue-profile.html?venue_id=${row.id}" target="_blank" style="display:inline-block;padding:6px 14px;background:rgba(6,182,212,0.2);border:1px solid rgba(6,182,212,0.45);border-radius:6px;color:var(--cyan);font-size:0.78rem;font-weight:600;text-decoration:none;">Open Profile ↗</a>
        <button onclick="event.stopPropagation();window._dirAdminTransferOwner('venue', ${row.id})" style="padding:6px 14px;background:rgba(245,158,11,0.15);border:1px solid rgba(245,158,11,0.45);border-radius:6px;color:#fbbf24;font-size:0.78rem;font-weight:600;cursor:pointer;">⚡ Transfer Owner</button>`;
    }
    // Build field list — one row per key, formatted per column definitions.
    const cols = COLS[type];
    const rowsHtml = cols.map(([key, label, render]) => {
      const val = render ? render(row) : esc(String(row[key] ?? '—'));
      return `<div style="display:grid;grid-template-columns:180px 1fr;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
        <div style="font-size:0.72rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.04em;font-weight:600;">${label}</div>
        <div style="font-size:0.85rem;color:var(--text);">${val}</div>
      </div>`;
    }).join('');
    // Extra useful keys not shown in the row (mostly IDs).
    const extras = [
      ['ID',        row.id],
      type === 'users'   ? ['Admin?', yesNo(row.is_admin)] : null,
      type === 'artists' ? ['Owner ID', row.owner_user_id || '—'] : null,
      type === 'venues'  ? ['Owner ID', row.owner_user_id || '—'] : null,
      type === 'venues'  ? ['Payment Status', esc(row.payment_status || '—')] : null,
    ].filter(Boolean);
    const extrasHtml = extras.map(([label, val]) =>
      `<div style="display:grid;grid-template-columns:180px 1fr;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
        <div style="font-size:0.72rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.04em;font-weight:600;">${label}</div>
        <div style="font-size:0.85rem;color:var(--text);">${val}</div>
      </div>`
    ).join('');
    const modal = document.createElement('div');
    modal.id = 'dirDetailModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:10009;display:flex;align-items:center;justify-content:center;padding:16px;';
    modal.innerHTML = `
      <div style="background:var(--bg,#0f1117);border:1px solid var(--border);border-radius:14px;max-width:720px;width:100%;max-height:88vh;display:flex;flex-direction:column;box-shadow:0 24px 64px rgba(0,0,0,0.6);overflow:hidden;">
        <div style="display:flex;justify-content:space-between;align-items:center;padding:18px 22px;border-bottom:1px solid var(--border);gap:12px;flex-wrap:wrap;">
          <div>
            <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin-bottom:2px;">${type.charAt(0).toUpperCase() + type.slice(1, -1)} detail</div>
            <div style="font-weight:700;font-size:1.05rem;color:var(--text);">${esc(title)}</div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;">
            ${profileLink}
            <button onclick="document.getElementById('dirDetailModal').remove()" style="background:none;border:none;color:var(--text-gray);font-size:1.4rem;cursor:pointer;line-height:1;">✕</button>
          </div>
        </div>
        <div style="overflow-y:auto;flex:1;padding:8px 22px 22px;">
          ${rowsHtml}
          ${extrasHtml}
        </div>
      </div>
    `;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    // Escape to close.
    const escHandler = e => {
      if (e.key === 'Escape') { modal.remove(); document.removeEventListener('keydown', escHandler); }
    };
    document.addEventListener('keydown', escHandler);
  };

  // ── Admin transfer-owner (bypasses team-member requirement) ─────────────
  // Any user in the system can be the target — the Directory tab's Users
  // sub-tab already holds the full user list (cached). We build a dropdown
  // from that cache; if it hasn't loaded yet, fetch it inline.
  window._dirAdminTransferOwner = async function (entityType, entityId) {
    // Close the detail modal first so we don't stack two modals — the
    // detail modal's backdrop-click handler would fire when the user
    // clicks inside the transfer modal.
    const _dm = document.getElementById('dirDetailModal');
    if (_dm) _dm.remove();
    // Look up the entity name from the cached state (avoids inline
    // JSON-encoded name in the onclick attribute, which breaks the
    // double-quoted onclick when the name contains quotes).
    const typeKey = entityType === 'venue' ? 'venues' : 'artists';
    const entityRow = (state[typeKey] && state[typeKey].data || []).find(r => r.id === entityId) || {};
    const entityName = entityRow.venue_name || entityRow.name || (entityType + ' #' + entityId);
    // Ensure users list is loaded so the picker has options.
    if (!state.users.data.length) await window._dirLoad('users', false);
    const users = state.users.data.filter(u => u.id).sort((a, b) =>
      String(a.email || '').localeCompare(String(b.email || ''))
    );
    if (!users.length) {
      window.showStyledModal('No users', '<p style="margin:0;">No users in the directory to transfer to.</p>',
        [{ text: 'OK', style: 'primary' }], { size: 'sm', tone: 'error' });
      return;
    }
    const options = users.map(u => {
      const name = ((u.first_name || '') + ' ' + (u.last_name || '')).trim();
      const label = (name ? name + ' — ' : '') + (u.email || 'user #' + u.id);
      return `<option value="${u.id}">${esc(label)}</option>`;
    }).join('');
    const body =
      `<p style="margin:0 0 10px;">Force-transfer <strong style="color:var(--cyan);">${esc(entityName || (entityType + ' #' + entityId))}</strong> to a different owner. The current owner keeps <strong>admin</strong> team access; the new owner gets full control.</p>` +
      `<label style="display:block;font-size:0.8rem;color:var(--text-gray);margin-bottom:6px;">New owner</label>` +
      `<select id="_dirTransferSelect" style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid rgba(255,255,255,0.2);background:rgba(255,255,255,0.05);color:var(--text);font-size:0.95rem;">${options}</select>` +
      `<p style="margin:12px 0 0;font-size:0.78rem;color:var(--text-gray);font-style:italic;">Admin override. Logged to admin_audit_log.</p>`;

    window.showStyledModal(
      '⚡ Transfer Ownership (Admin)',
      body,
      [
        { text: 'Cancel', style: 'ghost' },
        {
          text: 'Transfer', style: 'primary',
          onClick: async () => {
            const sel = document.getElementById('_dirTransferSelect');
            const newOwnerId = sel ? parseInt(sel.value, 10) : 0;
            if (!newOwnerId) return;
            try {
              const res = await fetch(`/api/admin/${entityType}/${entityId}/transfer-owner`, {
                method: 'POST', credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_owner_user_id: newOwnerId })
              });
              let b = null; try { b = await res.json(); } catch (_) {}
              if (!res.ok) {
                window.showStyledModal('Transfer failed',
                  `<p style="margin:0;">${esc((b && b.detail) || ('HTTP ' + res.status))}</p>`,
                  [{ text: 'OK', style: 'primary' }], { size: 'sm', tone: 'error' });
                return;
              }
              // Full reload so Directory tab + counters everywhere refresh.
              window.location.reload();
            } catch (_e) {
              window.showStyledModal('Network error',
                '<p style="margin:0;">Could not reach the server.</p>',
                [{ text: 'OK', style: 'primary' }], { size: 'sm', tone: 'error' });
            }
          }
        },
      ],
      { tone: 'warning' }
    );
  };

  // ── Sub-tab switching ────────────────────────────────────────────────────
  window.switchDirectorySubTab = function (name) {
    activeType = name;
    const container = document.getElementById('directory-tab');
    if (!container) return;
    container.querySelectorAll('.ps-subtab').forEach(b => b.classList.remove('active'));
    container.querySelectorAll('.ps-subtab-content').forEach(c => c.classList.remove('active'));
    const btn = container.querySelector('.ps-subtab[onclick*="' + name + '"]');
    if (btn) btn.classList.add('active');
    const panel = document.getElementById('directory-' + name);
    if (panel) panel.classList.add('active');
    window._dirLoad(name, false);
  };

  // Called by switchTab('directory') in admin.html. Load ALL three
  // types in parallel so sub-tab count badges ("Artists (127)") populate
  // immediately — otherwise the two inactive tabs would sit at "(–)"
  // until clicked. Cached responses keep sub-tab switches instant.
  window._dirActivate = function () {
    Promise.all([
      window._dirLoad('users',   false),
      window._dirLoad('artists', false),
      window._dirLoad('venues',  false),
    ]);
  };

  function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
})();
