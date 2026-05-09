setTimezone('America/Los_Angeles');

// Auto-extracted from admin.html inline scripts
// Generated for CSP compliance (Phase 5)

// Cache is NOT cleared on every load — that would destroy user preferences.
// Only clear specific stale keys if needed.

let currentDataType = null;
let currentPage = 1;
let totalPages = 1;
let allData = [];
let filteredData = [];
const ITEMS_PER_PAGE = 10;

// Support ticket state
let allSupportTickets = [];
let filteredSupportTickets = [];
let supportCurrentPage = 1;
let supportSortColumn = 'created_at';
let supportSortDirection = 'desc';
const SUPPORT_PER_PAGE = 20;

function logout() {
  fetch('/api/logout', { method: 'POST' })
    .then(() => window.location.href = '/app/index.html')
    .catch(() => window.location.href = '/app/index.html');
}

// switchTab is defined in admin.html DOMContentLoaded block (after all scripts load)
// to ensure all tab-specific functions (logs, database, affiliates, etc.) are available.
// Do NOT redefine it here.

// Analytics drilldown state
let analyticsDetailData = [];
let analyticsDetailPage_ = 1;
let analyticsDetailTotalPages = 1;
let activeAnalyticsCard = null;
let activeEventBubble = null;
const ANALYTICS_PER_PAGE = 15;

async function loadAnalytics() {
  try {
    document.getElementById('analyticsLastUpdated').textContent = 'Loading…';
    const r = await fetch('/api/analytics/stats/admin-dashboard', { credentials: 'include' });
    if (!r.ok) { document.getElementById('analyticsLastUpdated').textContent = 'Error loading data'; return; }
    const d = await r.json();

    const fmt$ = v => '$' + (v || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const fmt  = v => (v || 0).toLocaleString();

    // Platform Overview
    set('an-total-users',     fmt(d.total_users));
    set('an-total-artists',   fmt(d.total_artists));
    set('an-total-venues',    fmt(d.total_venues));
    set('an-total-gigs',      fmt(d.total_gigs));
    set('an-booked-gigs',     fmt(d.booked_gigs));
    set('an-open-gigs',       fmt(d.open_gigs));
    set('an-upcoming-gigs',   fmt(d.upcoming_gigs));
    set('an-past-gigs',       fmt(d.past_gigs));
    set('an-cancelled-gigs',  fmt(d.cancelled_gigs));

    // Growth
    set('an-new-users-7d',    fmt(d.new_users_7d));
    set('an-new-artists-7d',  fmt(d.new_artists_7d));
    set('an-new-venues-7d',   fmt(d.new_venues_7d));
    set('an-new-gigs-7d',     fmt(d.new_gigs_7d));
    set('an-bookings-7d',     fmt(d.bookings_7d));
    set('an-new-users-30d',   fmt(d.new_users_30d));
    set('an-new-gigs-30d',    fmt(d.new_gigs_30d));
    set('an-bookings-30d',    fmt(d.bookings_30d));

    // Revenue
    set('an-total-revenue',    fmt$(d.total_revenue));
    set('an-total-commission', fmt$(d.total_commission));
    set('an-total-payouts',    fmt$(d.total_payouts));
    set('an-revenue-30d',      fmt$(d.revenue_30d));
    set('an-revenue-7d',       fmt$(d.revenue_7d));
    set('an-total-transactions', fmt(d.total_transactions));
    set('an-pending-payments', fmt$(d.pending_payments));
    set('an-failed-payments',  fmt(d.failed_payments));

    // Engagement
    set('an-total-contracts',   fmt(d.total_contracts));
    set('an-signed-contracts',  fmt(d.signed_contracts));
    set('an-pending-contracts', fmt(d.pending_contracts));
    set('an-preferred-pairs',   fmt(d.preferred_pairs));
    set('an-active-waitlist',   fmt(d.active_waitlist));
    set('an-open-tickets',      fmt(d.open_tickets));
    set('an-tickets-7d',        fmt(d.tickets_7d));
    set('an-pending-preferred', fmt(d.pending_preferred));

    // Traffic
    set('an-unique-visitors', fmt(d.unique_visitors));
    set('an-activity-24h',    fmt(d.activity_24h));
    set('an-activity-7d',     fmt(d.activity_7d));
    set('an-activity-30d',    fmt(d.activity_30d));
    set('an-gig-clicks',      fmt(d.gig_clicks_total));
    set('an-gig-clicks-7d',   fmt(d.gig_clicks_7d));

    // Recent Bookings list
    const rb = document.getElementById('an-recent-bookings');
    if (d.recent_bookings && d.recent_bookings.length) {
      rb.innerHTML = d.recent_bookings.map(b => `
        <div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
          <span style="color:var(--text-white);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:55%;">${b.artist_name}</span>
          <span style="color:var(--text-gray);white-space:nowrap;font-size:0.68rem;">${b.venue_name ? b.venue_name.substring(0,18) : ''} · ${b.date ? b.date.substring(5) : ''}</span>
        </div>`).join('');
    } else { rb.innerHTML = '<p style="color:var(--text-gray);font-size:0.72rem;">No bookings yet</p>'; }

    // Top Artists
    const ta = document.getElementById('an-top-artists');
    if (d.top_artists_booked && d.top_artists_booked.length) {
      ta.innerHTML = d.top_artists_booked.map((a,i) => `
        <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
          <span style="color:var(--text-white);">${i+1}. ${a.name}</span>
          <span style="color:var(--cyan);font-weight:700;">${a.bookings}</span>
        </div>`).join('');
    } else { ta.innerHTML = '<p style="color:var(--text-gray);">No data yet</p>'; }

    // Top Venues
    const tv = document.getElementById('an-top-venues');
    if (d.top_venues_booked && d.top_venues_booked.length) {
      tv.innerHTML = d.top_venues_booked.map((v,i) => `
        <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
          <span style="color:var(--text-white);">${i+1}. ${v.venue_name}</span>
          <span style="color:var(--cyan);font-weight:700;">${v.bookings}</span>
        </div>`).join('');
    } else { tv.innerHTML = '<p style="color:var(--text-gray);">No data yet</p>'; }

    // Recent Signups
    const rs = document.getElementById('an-recent-signups');
    if (d.recent_signups && d.recent_signups.length) {
      const roleColor = r => r==='artist' ? '#06b6d4' : r==='venue' ? '#f59e0b' : '#6b7280';
      const roleIcon  = r => r==='artist' ? '🎤' : r==='venue' ? '🏛️' : '👤';
      rs.innerHTML = d.recent_signups.map(u => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
          <span style="color:var(--text-white);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%;">${roleIcon(u.role)} ${u.email}</span>
          <span style="color:${roleColor(u.role)};font-size:0.65rem;font-weight:600;text-transform:uppercase;">${u.role}</span>
        </div>`).join('');
    } else { rs.innerHTML = '<p style="color:var(--text-gray);">No signups yet</p>'; }

    // Top Cities
    const tc = document.getElementById('an-top-cities');
    if (d.top_cities && d.top_cities.length) {
      tc.innerHTML = d.top_cities.map((c,i) => `
        <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
          <span style="color:var(--text-white);">${i+1}. ${c.city}${c.state ? ', '+c.state : ''}</span>
          <span style="color:var(--cyan);font-weight:700;">${c.searches}</span>
        </div>`).join('');
    } else { tc.innerHTML = '<p style="color:var(--text-gray);">No search data yet</p>'; }

    // Events by type
    const et = document.getElementById('an-events-by-type');
    if (d.events_by_type && d.events_by_type.length) {
      et.innerHTML = d.events_by_type.map(e => `
        <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
          <span style="color:var(--text-gray);text-transform:capitalize;">${(e.event_type||'').replace(/_/g,' ')}</span>
          <span style="color:var(--cyan);font-weight:700;">${e.count}</span>
        </div>`).join('');
    } else { et.innerHTML = '<p style="color:var(--text-gray);">No events yet</p>'; }

    // Revenue table — all 12 months always shown, amount above, N/A for future
    const chartEl  = document.getElementById('an-revenue-chart');
    const labelsEl = document.getElementById('an-revenue-labels');

    // Set year in title
    const yearEl = document.getElementById('an-revenue-year');
    if (yearEl) yearEl.textContent = String(new Date().getFullYear());

    // Build a lookup of what the API returned keyed by "MM" (zero-padded month)
    const revByMonth = {};
    (d.revenue_by_month || []).forEach(m => {
      if (m.month) revByMonth[m.month] = m.total || 0;  // key is "YYYY-MM"
    });

    const now = new Date();
    const curYear = now.getFullYear();
    const curMonth = now.getMonth() + 1; // 1-based

    // Build all 12 months: Jan of current year through Dec of current year
    const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

    // Generate the 12 calendar months for the current year
    const months = MONTH_NAMES.map((name, i) => {
      const mo = i + 1; // 1-based
      const key = `${curYear}-${String(mo).padStart(2,'0')}`;
      const isFuture = mo > curMonth;
      const total = revByMonth[key];
      return { name, mo, key, isFuture, total: total !== undefined ? total : null };
    });

    // Render as proper CSS bar chart with proportional bars
    const maxRevenue = Math.max(...months.filter(m => !m.isFuture && m.total).map(m => m.total), 1);

    chartEl.style.alignItems = 'flex-end';
    chartEl.style.height = '120px';
    chartEl.style.gap = '4px';

    const bars = months.map(m => {
      const isCurrentMonth = m.mo === curMonth;
      const pct = (!m.isFuture && m.total) ? Math.max(4, Math.round((m.total / maxRevenue) * 100)) : 0;

      let label, barColor, barOpacity;
      if (m.isFuture) {
        label = '';
        barColor = 'rgba(255,255,255,0.06)';
        barOpacity = 1;
      } else if (!m.total || m.total === 0) {
        label = '$0';
        barColor = 'rgba(255,255,255,0.08)';
        barOpacity = 1;
      } else {
        label = '$' + (m.total >= 1000
          ? (m.total/1000).toFixed(1) + 'k'
          : m.total.toLocaleString('en-US', {maximumFractionDigits: 0}));
        barColor = isCurrentMonth ? '#f59e0b' : '#10b981';
        barOpacity = isCurrentMonth ? 1 : 0.75;
      }

      return `<div style="flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:3px;cursor:default;"
        title="${m.name} ${curYear}: ${m.isFuture ? 'Future' : (m.total ? '$'+m.total.toLocaleString() : '$0')}">
        <div style="font-size:0.58rem;color:${isCurrentMonth?'#f59e0b':'var(--text-gray)'};font-weight:${isCurrentMonth?'700':'400'};white-space:nowrap;overflow:hidden;max-width:100%;text-align:center;">${label}</div>
        <div style="width:100%;height:${pct}%;background:${barColor};opacity:${barOpacity};border-radius:3px 3px 0 0;min-height:${m.isFuture?'2':'0'}px;transition:opacity .2s;"
          onmouseover="this.style.opacity='1'"
          onmouseout="this.style.opacity='${barOpacity}'"></div>
        <div style="font-size:0.62rem;color:${isCurrentMonth?'#f59e0b':'var(--text-gray)'};font-weight:${isCurrentMonth?'700':'400'};white-space:nowrap;">${m.name}</div>
      </div>`;
    });

    chartEl.innerHTML = bars.join('');
    labelsEl.innerHTML = '';

    document.getElementById('analyticsLastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch(e) {
    console.error('Analytics error:', e);
    document.getElementById('analyticsLastUpdated').textContent = 'Error: ' + e.message;
  }
}

function set(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function showAnalyticsDetail(type) {
  // Wire analytics cards to the data drilldown section (same as platform stats cards)
  if (typeof window.toggleDataSection === 'function') {
    window.toggleDataSection(type);
    // Scroll to the data section
    const ds = document.getElementById('dataSection');
    if (ds && ds.style.display !== 'none') {
      ds.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }
}


async function loadSupportTickets() {
  const container = document.getElementById('supportTableContainer');
  if (container) container.innerHTML = '<p style="color: var(--text-gray); text-align: center; font-size: 0.85rem; padding: 40px 0;">Loading tickets…</p>';
  try {
    const response = await fetch('/api/admin/support-tickets', { credentials: 'include' });
    if (!response.ok) {
      const err = await response.text();
      console.error('Support tickets API error:', response.status, err);
      if (container) container.innerHTML = '<p style="color: #ef4444; text-align: center; font-size: 0.85rem; padding: 40px 0;">Error ' + response.status + ' loading tickets. Check console.</p>';
      return;
    }
    allSupportTickets = await response.json();
    filteredSupportTickets = [...allSupportTickets];
    supportCurrentPage = 1;
    // Apply any active status filter
    const statusSel = document.getElementById('supportStatusFilter');
    if (statusSel && statusSel.value && statusSel.value !== 'all') {
      supportStatusFilter = statusSel.value;
      filteredSupportTickets = allSupportTickets.filter(t => (t.status || 'open') === supportStatusFilter);
    }
    renderSupportTickets();
  } catch (error) {
    console.error('Error loading support tickets:', error);
    if (container) container.innerHTML = '<p style="color: #ef4444; text-align: center; font-size: 0.85rem; padding: 40px 0;">Failed to load tickets: ' + error.message + '</p>';
  }
}

let supportStatusFilter = 'all';

function filterSupportTickets(query) {
  query = (query || '').toLowerCase();
  const base = supportStatusFilter === 'all' ? allSupportTickets
    : allSupportTickets.filter(t => (t.status || 'open') === supportStatusFilter);
  if (!query) {
    filteredSupportTickets = base;
  } else {
    filteredSupportTickets = base.filter(t => 
      (t.subject || '').toLowerCase().includes(query) ||
      (t.user_name || '').toLowerCase().includes(query) ||
      (t.user_email || '').toLowerCase().includes(query) ||
      (t.category || '').toLowerCase().includes(query) ||
      (t.description || '').toLowerCase().includes(query)
    );
  }
  supportCurrentPage = 1;
  renderSupportTickets();
}

function filterSupportTicketsByStatus(status) {
  supportStatusFilter = status || 'all';
  const query = (document.getElementById('supportSearchInput')?.value || '').toLowerCase();
  filterSupportTickets(query);
}

function sortSupportTickets(column) {
  if (supportSortColumn === column) {
    supportSortDirection = supportSortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    supportSortColumn = column;
    supportSortDirection = column === 'created_at' ? 'desc' : 'asc';
  }
  
  filteredSupportTickets.sort((a, b) => {
    let valA = a[column] || '';
    let valB = b[column] || '';
    if (column === 'created_at') {
      valA = valA ? new Date(valA).getTime() : 0;
      valB = valB ? new Date(valB).getTime() : 0;
    } else {
      valA = String(valA).toLowerCase();
      valB = String(valB).toLowerCase();
    }
    if (valA < valB) return supportSortDirection === 'asc' ? -1 : 1;
    if (valA > valB) return supportSortDirection === 'asc' ? 1 : -1;
    return 0;
  });
  
  supportCurrentPage = 1;
  renderSupportTickets();
}

function renderSupportTickets() {
  const container = document.getElementById('supportTableContainer');
  
  if (!filteredSupportTickets || filteredSupportTickets.length === 0) {
    container.innerHTML = '<p style="color: var(--text-gray); text-align: center; font-size: 0.85rem;">No support tickets found</p>';
    updateSupportPagination();
    return;
  }
  
  const totalPages = Math.ceil(filteredSupportTickets.length / SUPPORT_PER_PAGE);
  const start = (supportCurrentPage - 1) * SUPPORT_PER_PAGE;
  const pageData = filteredSupportTickets.slice(start, start + SUPPORT_PER_PAGE);
  
  const arrow = (col) => {
    if (supportSortColumn !== col) return '';
    return supportSortDirection === 'asc' ? ' ▲' : ' ▼';
  };
  
  container.innerHTML = `
    <table class="data-table" style="font-size: 0.8rem; width: 100%;">
      <thead>
        <tr>
          <th style="padding: 6px 8px; cursor: pointer; user-select: none; width: 90px; white-space: nowrap;" onclick="sortSupportTickets('id')">Ticket #${arrow('id')}</th>
          <th style="padding: 6px 8px; cursor: pointer; user-select: none; width: 160px; white-space: nowrap;" onclick="sortSupportTickets('created_at')">Date${arrow('created_at')}</th>
          <th style="padding: 6px 8px; cursor: pointer; user-select: none; width: 170px; white-space: nowrap;" onclick="sortSupportTickets('user_name')">From${arrow('user_name')}</th>
          <th style="padding: 6px 8px; cursor: pointer; user-select: none; width: 130px; white-space: nowrap;" onclick="sortSupportTickets('category')">Category${arrow('category')}</th>
          <th style="padding: 6px 8px; cursor: pointer; user-select: none;" onclick="sortSupportTickets('subject')">Subject${arrow('subject')}</th>
          <th style="padding: 6px 8px; cursor: pointer; user-select: none; width: 80px; white-space: nowrap;" onclick="sortSupportTickets('status')">Status${arrow('status')}</th>
        </tr>
      </thead>
      <tbody>
        ${pageData.map(t => {
          const date = t.created_at ? formatUTC(t.created_at, 'short') : '--';
          const statusColor = t.status === 'open' ? '#f59e0b' : t.status === 'closed' ? '#10b981' : '#6b7280';
          return `
            <tr style="cursor: pointer;" onclick="showTicketDetail(${t.id})">
              <td style="padding: 6px 8px; font-size: 0.75rem; white-space: nowrap; font-weight: 600; color: var(--cyan);">#${(t.id + 99999)}</td>
              <td style="padding: 6px 8px; font-size: 0.75rem; white-space: nowrap;">${date}</td>
              <td style="padding: 6px 8px; white-space: nowrap;">${t.user_name || t.user_email || '--'}</td>
              <td style="padding: 6px 8px; white-space: nowrap;"><span style="background: rgba(6,182,212,0.15); color: var(--cyan); padding: 1px 6px; border-radius: 3px; font-size: 0.65rem; text-transform: uppercase;">${t.category || '--'}</span></td>
              <td style="padding: 6px 8px; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${t.subject || '--'}${t.reply_count ? ` <span style="background: rgba(6,182,212,0.2); color: var(--cyan); padding: 0 5px; border-radius: 8px; font-size: 0.6rem; font-weight: 600;">${t.reply_count}</span>` : ''}</td>
              <td style="padding: 6px 8px;"><span style="color: ${statusColor}; font-weight: 600; font-size: 0.7rem; text-transform: uppercase;">${t.status || 'open'}</span></td>
            </tr>
          `;
        }).join('')}
      </tbody>
    </table>
  `;
  
  updateSupportPagination();
}

function updateSupportPagination() {
  const total = filteredSupportTickets ? filteredSupportTickets.length : 0;
  const totalPages = Math.max(1, Math.ceil(total / SUPPORT_PER_PAGE));
  document.getElementById('supportPageInfo').textContent = supportCurrentPage + ' / ' + totalPages + ' (' + total + ' tickets)';
  document.getElementById('supportPrevBtn').disabled = supportCurrentPage <= 1;
  document.getElementById('supportNextBtn').disabled = supportCurrentPage >= totalPages;
}

function supportPage(dir) {
  const totalPages = Math.max(1, Math.ceil(filteredSupportTickets.length / SUPPORT_PER_PAGE));
  supportCurrentPage += dir;
  if (supportCurrentPage < 1) supportCurrentPage = 1;
  if (supportCurrentPage > totalPages) supportCurrentPage = totalPages;
  renderSupportTickets();
}

function showTicketDetail(ticketId) {
  const ticket = allSupportTickets.find(t => t.id === ticketId);
  if (!ticket) return;
  
  const date = ticket.created_at ? formatUTC(ticket.created_at, 'short') : '--';
  const statusColor = ticket.status === 'open' ? '#f59e0b' : ticket.status === 'closed' ? '#10b981' : '#6b7280';
  
  const modal = document.createElement('div');
  modal.id = 'ticketModal';
  modal.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;';
  modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
  
  modal.innerHTML = `
    <div style="background: var(--card); border: 1px solid var(--border); border-radius: 12px; max-width: 800px; width: 95%; max-height: 90vh; display: flex; flex-direction: column;">
      <!-- Header -->
      <div style="padding: 20px 28px 16px; border-bottom: 1px solid var(--border); flex-shrink: 0;">
        <div style="display: flex; justify-content: space-between; align-items: start;">
          <div>
            <h3 style="margin: 0 0 6px; color: var(--text-white); font-size: 1rem;">Ticket #${ticket.id} — ${ticket.subject || 'No subject'}</h3>
            <div style="display: flex; gap: 16px; font-size: 0.75rem; color: var(--text-gray);">
              <span>${ticket.user_name || ''} (${ticket.user_email || ''})</span>
              <span>${ticket.category || ''}</span>
              <span>${date}</span>
              <span style="color: ${statusColor}; font-weight: 600; text-transform: uppercase;">${ticket.status || 'open'}</span>
            </div>
          </div>
          <div style="display: flex; gap: 6px; align-items: center; flex-shrink: 0;">
            ${ticket.status !== 'closed' ? `<button onclick="updateTicketStatus(${ticket.id}, 'closed')" style="padding: 5px 12px; background: #10b981; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 0.7rem;">Close</button>` : `<button onclick="updateTicketStatus(${ticket.id}, 'open')" style="padding: 5px 12px; background: #f59e0b; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 0.7rem;">Reopen</button>`}
            <button onclick="this.closest('#ticketModal').remove()" style="background: none; border: none; color: var(--text-gray); font-size: 1.2rem; cursor: pointer; padding: 0 4px;">✕</button>
          </div>
        </div>
      </div>
      
      <!-- Conversation thread (scrollable) -->
      <div id="ticketThread" style="flex: 1; overflow-y: auto; padding: 20px 28px;">
        <p style="color: var(--text-gray); font-size: 0.8rem; text-align: center;">Loading conversation...</p>
      </div>
      
      <!-- Reply compose (pinned to bottom) -->
      <div style="padding: 16px 28px 20px; border-top: 1px solid var(--border); flex-shrink: 0;">
        <textarea id="ticketReplyBody" rows="3" placeholder="Type your reply..." 
          style="width: 100%; padding: 10px 14px; background: #151b28; border: 1px solid #333; border-radius: 8px; color: var(--text-white); font-size: 0.8rem; resize: vertical; box-sizing: border-box; min-height: 70px; font-family: inherit; line-height: 1.5;"></textarea>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px;">
          <span style="font-size: 0.65rem; color: var(--text-gray);">Reply will be emailed to ${ticket.user_email || 'user'} via platform email settings</span>
          <button id="ticketSendBtn" onclick="sendTicketReply(${ticket.id})" 
            style="padding: 8px 20px; background: var(--cyan); color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.75rem; font-weight: 600;">
            Send Reply
          </button>
        </div>
      </div>
    </div>
  `;
  
  document.body.appendChild(modal);
  loadTicketThread(ticket);
}

async function loadTicketThread(ticket) {
  const container = document.getElementById('ticketThread');
  if (!container) return;
  
  let replies = [];
  try {
    const resp = await fetch('/api/admin/support-tickets/' + ticket.id + '/replies', { credentials: 'include' });
    if (resp.ok) replies = await resp.json();
  } catch (e) {
    console.error('Error loading replies:', e);
  }
  
  // Build thread: original ticket first, then replies
  let html = '';
  
  // Original ticket message
  const origDate = ticket.created_at ? formatUTC(ticket.created_at, 'short') : '';
  html += `
    <div style="margin-bottom: 16px;">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
        <span style="display: inline-block; width: 28px; height: 28px; border-radius: 50%; background: #7c3aed; color: white; text-align: center; line-height: 28px; font-size: 0.7rem; font-weight: 600; flex-shrink: 0;">${(ticket.user_name || 'U').charAt(0).toUpperCase()}</span>
        <span style="font-size: 0.8rem; font-weight: 600; color: var(--text-white);">${ticket.user_name || ticket.user_email || 'User'}</span>
        <span style="font-size: 0.65rem; color: var(--text-gray);">${origDate}</span>
        <span style="background: rgba(124,58,237,0.15); color: #a78bfa; padding: 1px 6px; border-radius: 3px; font-size: 0.6rem; text-transform: uppercase;">Original</span>
      </div>
      <div style="margin-left: 36px; background: rgba(124,58,237,0.06); border: 1px solid rgba(124,58,237,0.15); border-radius: 8px; padding: 14px 16px;">
        <div style="font-size: 0.8rem; color: var(--text-white); line-height: 1.6; white-space: pre-wrap;">${ticket.description || ''}</div>
      </div>
    </div>`;
  
  // Replies
  for (const r of replies) {
    const isAdmin = r.sender_type === 'admin';
    const avatar = isAdmin ? 'S' : (ticket.user_name || 'U').charAt(0).toUpperCase();
    const avatarBg = isAdmin ? '#06b6d4' : '#7c3aed';
    const bubbleBg = isAdmin ? 'rgba(6,182,212,0.06)' : 'rgba(124,58,237,0.06)';
    const bubbleBorder = isAdmin ? 'rgba(6,182,212,0.15)' : 'rgba(124,58,237,0.15)';
    const label = isAdmin ? 'Support' : 'User';
    const labelColor = isAdmin ? 'var(--cyan)' : '#a78bfa';
    const labelBg = isAdmin ? 'rgba(6,182,212,0.15)' : 'rgba(124,58,237,0.15)';
    const name = r.sender_name || (isAdmin ? 'GigsFill Support' : ticket.user_name || 'User');
    const rDate = r.created_at ? formatUTC(r.created_at, 'short') : '';
    
    html += `
      <div style="margin-bottom: 16px;">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
          <span style="display: inline-block; width: 28px; height: 28px; border-radius: 50%; background: ${avatarBg}; color: white; text-align: center; line-height: 28px; font-size: 0.7rem; font-weight: 600; flex-shrink: 0;">${avatar}</span>
          <span style="font-size: 0.8rem; font-weight: 600; color: var(--text-white);">${name}</span>
          <span style="font-size: 0.65rem; color: var(--text-gray);">${rDate}</span>
          <span style="background: ${labelBg}; color: ${labelColor}; padding: 1px 6px; border-radius: 3px; font-size: 0.6rem; text-transform: uppercase;">${label}</span>
        </div>
        <div style="margin-left: 36px; background: ${bubbleBg}; border: 1px solid ${bubbleBorder}; border-radius: 8px; padding: 14px 16px;">
          <div style="font-size: 0.8rem; color: var(--text-white); line-height: 1.6; white-space: pre-wrap;">${r.body || ''}</div>
        </div>
      </div>`;
  }
  
  if (replies.length === 0) {
    html += `<div style="text-align: center; padding: 12px 0; color: var(--text-gray); font-size: 0.75rem;">No replies yet — type below to respond.</div>`;
  }
  
  container.innerHTML = html;
  
  // Scroll to bottom of thread
  container.scrollTop = container.scrollHeight;
}

async function sendTicketReply(ticketId) {
  const textarea = document.getElementById('ticketReplyBody');
  const btn = document.getElementById('ticketSendBtn');
  const body = (textarea ? textarea.value : '').trim();
  
  if (!body) {
    textarea.style.borderColor = '#ef4444';
    setTimeout(() => { textarea.style.borderColor = '#333'; }, 2000);
    return;
  }
  
  btn.disabled = true;
  btn.textContent = 'Sending...';
  
  try {
    const resp = await fetch('/api/admin/support-tickets/' + ticketId + '/reply', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body })
    });
    
    const result = await resp.json();
    
    if (resp.ok) {
      textarea.value = '';
      // Reload the thread
      const ticket = allSupportTickets.find(t => t.id === ticketId);
      if (ticket) {
        ticket.reply_count = (ticket.reply_count || 0) + 1;
        loadTicketThread(ticket);
      }
      renderSupportTickets();  // Update reply count in table
      
      if (result.email_sent) {
        btn.textContent = '✓ Sent & Emailed';
      } else {
        btn.textContent = '✓ Saved (email failed)';
        btn.style.background = '#f59e0b';
      }
    } else {
      btn.textContent = 'Failed — try again';
      btn.style.background = '#ef4444';
    }
  } catch (e) {
    console.error('Error sending reply:', e);
    btn.textContent = 'Error — try again';
    btn.style.background = '#ef4444';
  }
  
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = 'Send Reply';
    btn.style.background = '';
  }, 3000);
}

async function updateTicketStatus(ticketId, status) {
  try {
    await fetch('/api/admin/support-tickets/' + ticketId, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status })
    });
    // Update local data
    const ticket = allSupportTickets.find(t => t.id === ticketId);
    if (ticket) ticket.status = status;
    renderSupportTickets();
    // Close and reopen the modal to refresh header
    const modal = document.getElementById('ticketModal');
    if (modal) modal.remove();
    showTicketDetail(ticketId);
  } catch (error) {
    console.error('Error updating ticket:', error);
  }
}

// ============================================
// ACCOUNTING TAB
// ============================================
let _acctData = [];
let _acctSort = { col: 'gig_date', dir: -1 };
let _acctPage = 1;
const ACCT_PER_PAGE = 20;

function _fmt12(ts) {
  if (!ts) return '';
  const p = ts.split(':');
  let h = parseInt(p[0]); const m = p[1] || '00';
  const ap = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return h + ':' + m + ' ' + ap;
}

function _cents(v) { return '$' + (v / 100).toFixed(2); }

async function loadAccounting() {
  const container = document.getElementById('accountingTable');
  try {
    const res = await fetch('/api/admin/accounting', { credentials: 'include' });
    if (!res.ok) { container.innerHTML = '<p style="color:#ef4444;">Failed to load accounting data</p>'; return; }
    _acctData = await res.json();
    _acctPage = 1;
    renderAccountingSummary();
    renderAccountingTable();
  } catch (e) { container.innerHTML = '<p style="color:#ef4444;">Error: ' + e.message + '</p>'; }
}

function renderAccountingSummary() {
  const el = document.getElementById('accountingSummary');

  // Status buckets (what counts as "completed" for accounting purposes):
  //   - 'paid'              → transfer fully settled
  //   - 'charged'           → venue charged, transfer pending (or already transferred but not yet bank-settled)
  //   - 'pending_transfer'  → venue charged, transfer hasn't fired yet
  //   - 'transfer_failed'   → venue charged, transfer crashed; awaiting retry
  //   - 'payment_cancelled' → venue cancelled before payout; platform fee may have been charged
  // (Cancelled gigs are still "completed" for accounting because money was moved.)
  const COMPLETED_STATUSES = ['paid','charged','pending_transfer','transfer_failed','payment_cancelled'];
  // "Successful" txns where the artist actually got paid (or will, once retry settles).
  // Cancelled gigs aren't successful — they were called off. transfer_failed is in progress.
  const SUCCESS_STATUSES = ['paid','charged','pending_transfer'];

  let txnCount = 0, successCount = 0;
  let totalGigValue = 0;        // Sum of gig pay across non-cancelled completed txns
  let totalFees = 0;            // Platform revenue earned (mutually exclusive: commission OR cancel fee, not both)
  let totalStripeFees = 0;      // Stripe processing on actual charges
  let totalProfit = 0;          // GF Profit (already calculated row-by-row by backend)

  _acctData.forEach(t => {
    if (!COMPLETED_STATUSES.includes(t.status)) return;
    txnCount++;
    if (SUCCESS_STATUSES.includes(t.status)) successCount++;

    if (t.status === 'payment_cancelled') {
      // Cancelled — only the cancel fee counts as platform revenue. The original
      // commission_cents is moot because no charge was scheduled / nothing was paid out.
      // Gig value also doesn't contribute (the gig didn't happen).
      totalFees += (t.platform_fee_on_cancel_cents || 0);
    } else {
      // Successful or in-flight charge — full commission counts as platform revenue.
      totalFees += (t.commission_cents || 0);
      totalGigValue += (t.gig_fee_cents || 0);
    }

    totalStripeFees += (t.stripe_fee_cents || 0);
    totalProfit += (t.gigsfill_profit_cents || 0);
  });

  // Average gig value over the txns that actually contributed to it (non-cancelled completed)
  const gigValueCount = _acctData.filter(t =>
    COMPLETED_STATUSES.includes(t.status) && t.status !== 'payment_cancelled'
  ).length;
  const avgGigValue = gigValueCount > 0 ? totalGigValue / gigValueCount : 0;

  const card = (label, val, color, sub) => `<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:12px 16px;min-width:120px;">
    <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">${label}</div>
    <div style="font-size:1.1rem;font-weight:700;color:${color};">${val}</div>
    ${sub ? `<div style="font-size:0.65rem;color:var(--text-muted);margin-top:2px;">${sub}</div>` : ''}
  </div>`;
  el.innerHTML = card('Completed Txns', txnCount, 'var(--text-white)', successCount + ' successful')
    + card('Total Gig Value', _cents(totalGigValue), '#06b6d4', 'avg ' + _cents(avgGigValue))
    + card('Total Fees', _cents(totalFees), '#8b5cf6', 'platform revenue')
    + card('Stripe Costs', _cents(totalStripeFees), '#f59e0b', 'processing')
    + card('Net Profit', _cents(totalProfit), '#10b981', 'after Stripe');
}

function renderAccountingTable() {
  const container = document.getElementById('accountingTable');
  let data = _acctData.slice();
  const sort = _acctSort;

  data.sort((a, b) => {
    let av = a[sort.col], bv = b[sort.col];
    if (sort.col === 'gig_date') { av = av || ''; bv = bv || ''; }
    if (typeof av === 'string') { av = av.toLowerCase(); bv = (bv||'').toLowerCase(); }
    if (av < bv) return -1 * sort.dir;
    if (av > bv) return 1 * sort.dir;
    return 0;
  });

  const totalPages = Math.max(1, Math.ceil(data.length / ACCT_PER_PAGE));
  if (_acctPage > totalPages) _acctPage = totalPages;
  const start = (_acctPage - 1) * ACCT_PER_PAGE;
  const pageData = data.slice(start, start + ACCT_PER_PAGE);

  if (data.length === 0) { container.innerHTML = '<p style="color:var(--text-muted);">No transactions found.</p>'; return; }

  const arrow = (col) => {
    if (sort.col !== col) return ' <span style="opacity:0.3;font-size:0.6rem;">⇅</span>';
    return sort.dir === 1 ? ' <span style="font-size:0.6rem;">▲</span>' : ' <span style="font-size:0.6rem;">▼</span>';
  };

  const hs = 'cursor:pointer;user-select:none;padding:6px 8px;font-size:0.68rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.03em;border-bottom:1px solid rgba(255,255,255,0.1);white-space:nowrap;';
  const statusColors = { paid:'#10b981', test:'#60a5fa', scheduled:'#8b5cf6', charged:'#f59e0b', charge_retry:'#f97316', payment_failed:'#ef4444', transfer_failed:'#ef4444', payment_cancelled:'#f97316', pending_transfer:'#f59e0b' };
  const statusLabels = { paid:'Paid', test:'Test', scheduled:'Scheduled', charged:'Charged', charge_retry:'Retry', payment_failed:'Failed', transfer_failed:'Xfer Fail', payment_cancelled:'Cancelled', pending_transfer:'Pending Xfer' };

  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.78rem;">';
  html += '<thead><tr>';
  html += `<th style="${hs}text-align:left;" onclick="acctSortBy('gig_date')">Date${arrow('gig_date')}</th>`;
  html += `<th style="${hs}text-align:left;">Time</th>`;
  html += `<th style="${hs}text-align:left;" onclick="acctSortBy('venue_name')">Venue${arrow('venue_name')}</th>`;
  html += `<th style="${hs}text-align:left;" onclick="acctSortBy('artist_name')">Artist${arrow('artist_name')}</th>`;
  html += `<th style="${hs}text-align:left;" onclick="acctSortBy('status')">Status${arrow('status')}</th>`;
  html += `<th style="${hs}text-align:right;" onclick="acctSortBy('gig_fee_cents')">Gig Paid${arrow('gig_fee_cents')}</th>`;
  html += `<th style="${hs}text-align:right;">Venue Fee</th>`;
  html += `<th style="${hs}text-align:right;">Venue Charged</th>`;
  html += `<th style="${hs}text-align:right;">Artist Fee</th>`;
  html += `<th style="${hs}text-align:right;">Artist Payout</th>`;
  html += `<th style="${hs}text-align:right;">Stripe Fee</th>`;
  html += `<th style="${hs}text-align:right;" onclick="acctSortBy('gigsfill_profit_cents')">GF Profit${arrow('gigsfill_profit_cents')}</th>`;
  html += '</tr></thead><tbody>';

  pageData.forEach(t => {
    const timeStr = t.start_time && t.end_time ? _fmt12(t.start_time) + ' - ' + _fmt12(t.end_time) : (t.start_time ? _fmt12(t.start_time) : '');
    const sColor = statusColors[t.status] || '#f59e0b';
    const sLabel = statusLabels[t.status] || t.status;
    const isCancelled = t.status === 'payment_cancelled';
    const venueCharged = isCancelled ? t.platform_fee_on_cancel_cents : t.venue_charge_cents;
    const profitColor = t.gigsfill_profit_cents > 0 ? '#10b981' : (t.gigsfill_profit_cents < 0 ? '#ef4444' : 'var(--text-muted)');

    html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">';
    html += `<td style="padding:6px 8px;color:var(--text-gray);white-space:nowrap;">${t.gig_date || ''}</td>`;
    html += `<td style="padding:6px 8px;color:var(--text-muted);white-space:nowrap;font-size:0.73rem;">${timeStr}</td>`;
    html += `<td style="padding:6px 8px;color:var(--text-white);overflow:hidden;text-overflow:ellipsis;max-width:120px;white-space:nowrap;" title="${t.venue_name}">${t.venue_name}</td>`;
    html += `<td style="padding:6px 8px;color:var(--text-white);overflow:hidden;text-overflow:ellipsis;max-width:120px;white-space:nowrap;" title="${t.artist_name}">${t.artist_name}</td>`;
    html += `<td style="padding:6px 8px;"><span style="color:${sColor};font-weight:600;font-size:0.73rem;">${sLabel}</span></td>`;
    html += `<td style="padding:6px 8px;text-align:right;color:var(--text-white);">${_cents(t.gig_fee_cents)}</td>`;
    html += `<td style="padding:6px 8px;text-align:right;color:#f59e0b;">${_cents(t.venue_fee_cents)}</td>`;
    html += `<td style="padding:6px 8px;text-align:right;color:#10b981;font-weight:600;">${_cents(venueCharged)}</td>`;
    html += `<td style="padding:6px 8px;text-align:right;color:#f59e0b;">${_cents(t.artist_fee_cents)}</td>`;
    html += `<td style="padding:6px 8px;text-align:right;color:#ef4444;">${isCancelled ? '—' : _cents(t.artist_payout_cents)}</td>`;
    html += `<td style="padding:6px 8px;text-align:right;color:#ef4444;">${_cents(t.stripe_fee_cents)}</td>`;
    html += `<td style="padding:6px 8px;text-align:right;color:${profitColor};font-weight:700;">${_cents(t.gigsfill_profit_cents)}</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;

  // Pagination
  const pagEl = document.getElementById('accountingPagination');
  const bs = 'background:rgba(255,255,255,0.05);border:1px solid var(--glass-border,rgba(255,255,255,0.1));color:var(--text);padding:4px 10px;border-radius:4px;font-size:0.75rem;cursor:pointer;';
  const ds = bs + 'opacity:0.3;cursor:default;';
  pagEl.innerHTML = `<span style="font-size:0.75rem;color:var(--text-muted);">Page ${_acctPage} of ${totalPages} (${data.length} records)</span>
    <button onclick="acctGoPage(${_acctPage - 1})" style="${_acctPage <= 1 ? ds : bs}" ${_acctPage <= 1 ? 'disabled' : ''}>◀ Prev</button>
    <button onclick="acctGoPage(${_acctPage + 1})" style="${_acctPage >= totalPages ? ds : bs}" ${_acctPage >= totalPages ? 'disabled' : ''}>Next ▶</button>`;
}

function acctSortBy(col) {
  if (_acctSort.col === col) { _acctSort.dir *= -1; } else { _acctSort.col = col; _acctSort.dir = col === 'gig_date' ? -1 : 1; }
  _acctPage = 1;
  renderAccountingTable();
}
function acctGoPage(p) { _acctPage = p; renderAccountingTable(); }

// Export modal
function showAccountingExportModal() {
  document.getElementById('accountingExportModal').style.display = 'flex';
}
function closeAccountingExportModal() {
  document.getElementById('accountingExportModal').style.display = 'none';
}
function toggleAcctCustomRange() {
  document.getElementById('acctCustomRangeDiv').style.display =
    document.getElementById('acctExportRange').value === 'custom' ? 'block' : 'none';
}

function _getAcctExportData() {
  const range = document.getElementById('acctExportRange').value;
  let data = _acctData.slice();
  const now = new Date();
  if (range === 'ytd') {
    const yearStart = now.getFullYear() + '-01-01';
    data = data.filter(t => t.gig_date >= yearStart);
  } else if (range === 'last_month') {
    const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    const from = d.toISOString().slice(0, 10);
    const to = new Date(now.getFullYear(), now.getMonth(), 0).toISOString().slice(0, 10);
    data = data.filter(t => t.gig_date >= from && t.gig_date <= to);
  } else if (range === 'last_quarter') {
    const qStart = new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3 - 3, 1);
    const qEnd = new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 0);
    data = data.filter(t => t.gig_date >= qStart.toISOString().slice(0, 10) && t.gig_date <= qEnd.toISOString().slice(0, 10));
  } else if (range === 'custom') {
    const from = document.getElementById('acctExportFrom').value;
    const to = document.getElementById('acctExportTo').value;
    if (from) data = data.filter(t => t.gig_date >= from);
    if (to) data = data.filter(t => t.gig_date <= to);
  }
  return data;
}

function executeAccountingExport() {
  const fmt = document.querySelector('input[name="acctExportFmt"]:checked').value;
  const data = _getAcctExportData();
  if (!data.length) { window._adminToast('No data in selected range', 'rgba(245,158,11,0.8)'); return; }

  const headers = ['Date','Time','Venue','Artist','Status','Gig Paid','Venue Fee','Venue Charged','Artist Fee','Artist Payout','Stripe Fee','GF Profit'];

  function row(t) {
    const time = t.start_time && t.end_time ? _fmt12(t.start_time) + '-' + _fmt12(t.end_time) : '';
    const isCx = t.status === 'payment_cancelled';
    return [
      t.gig_date, time, t.venue_name, t.artist_name, t.status,
      (t.gig_fee_cents/100).toFixed(2), (t.venue_fee_cents/100).toFixed(2),
      ((isCx ? t.platform_fee_on_cancel_cents : t.venue_charge_cents)/100).toFixed(2),
      (t.artist_fee_cents/100).toFixed(2),
      isCx ? '0.00' : (t.artist_payout_cents/100).toFixed(2),
      (t.stripe_fee_cents/100).toFixed(2), (t.gigsfill_profit_cents/100).toFixed(2)
    ];
  }

  if (fmt === 'excel') {
    let csv = headers.join(',') + '\n';
    data.forEach(t => {
      const r = row(t);
      csv += r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',') + '\n';
    });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'gigsfill_accounting.csv'; a.click();
  } else {
    const w = window.open('', '_blank');
    w.document.write('<html><head><title>GigsFill Accounting</title><style>body{font-family:Arial,sans-serif;padding:20px;font-size:12px;}table{width:100%;border-collapse:collapse;margin-top:12px;}th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;}th{background:#f4f4f4;font-weight:bold;font-size:11px;}tr:nth-child(even){background:#fafafa;}.r{text-align:right;}</style></head><body>');
    w.document.write('<h2>GigsFill Accounting Report</h2><p>Exported: ' + new Date().toLocaleDateString() + ' | Records: ' + data.length + '</p>');
    w.document.write('<table><tr>' + headers.map(h => '<th>' + h + '</th>').join('') + '</tr>');
    data.forEach(t => {
      const r = row(t);
      w.document.write('<tr>' + r.map((v, i) => '<td' + (i >= 5 ? ' class="r"' : '') + '>' + (i >= 5 ? '$' : '') + v + '</td>').join('') + '</tr>');
    });
    // Totals row — order: Gig Fee, Venue Fee, Venue Charged, Artist Fee, Artist Payout, Stripe Fee, GF Profit
    let totals = [0,0,0,0,0,0,0];
    data.forEach(t => {
      const isCx = t.status === 'payment_cancelled';
      totals[0] += t.gig_fee_cents;
      totals[1] += t.venue_fee_cents;
      totals[2] += isCx ? t.platform_fee_on_cancel_cents : t.venue_charge_cents;
      totals[3] += t.artist_fee_cents;
      totals[4] += isCx ? 0 : t.artist_payout_cents;
      totals[5] += t.stripe_fee_cents;
      totals[6] += t.gigsfill_profit_cents;
    });
    w.document.write('<tr style="font-weight:bold;background:#e8e8e8;"><td colspan="5" style="text-align:right;">TOTALS:</td>');
    totals.forEach(v => w.document.write('<td class="r">$' + (v/100).toFixed(2) + '</td>'));
    w.document.write('</tr></table></body></html>');
    w.document.close();
    w.print();
  }
  closeAccountingExportModal();
}