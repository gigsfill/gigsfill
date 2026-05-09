// Auto-extracted from artist-book-gigs.html inline scripts
// Generated for CSP compliance (Phase 5)

// === Block 1 of 5 ===
// Enhanced modal management for day-modal class cleanup
document.addEventListener('DOMContentLoaded', () => {
  const overlay = document.getElementById('modalOverlay');
  const modal = overlay ? overlay.querySelector('.modal') : null;
  
  if (!modal || !overlay) return;
  
  // Override closeModal to clean up classes
  const originalCloseModal = window.closeModal;
  window.closeModal = function() {
    modal.classList.remove('day-modal');
    if (originalCloseModal) {
      originalCloseModal();
    } else {
      overlay.classList.add('hidden');
    }
  };
});

// === Block 2 of 5 ===
(function () {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  if (!artistId) {
    window.location.href = "/app/user-profile.html";
    return;
  }

  const btn = document.getElementById("artistProfileBtn");
  if (btn) {
    btn.href = `/app/artist-profile.html?artist_id=${artistId}`;
  }
  
  const editBtn = document.getElementById("artistEditBtn");
  if (editBtn) {
    editBtn.href = `/app/artist-edit.html?artist_id=${artistId}`;
  }

  // Wire iCal export link
  const icalBtn = document.getElementById("artistIcalBtn");
  if (icalBtn) {
    icalBtn.href = `/api/artists/${artistId}/calendar.ics`;
  }
  
  // Fetch and display artist name in header
  fetch(`/api/artists/${artistId}`, { credentials: 'include' })
    .then(res => res.ok ? res.json() : null)
    .then(artist => {
      if (artist && artist.name) {
        const logo = document.querySelector('.logo');
        if (logo) {
          logo.innerHTML = `<img src="/app/static/img/gigsfill-logo.png" alt="GigsFill" style="height:44px;width:auto;flex-shrink:0;"><span style="font: 600 0.875rem 'Inter', sans-serif; color: var(--cyan); margin-left: 24px; background: none; -webkit-background-clip: unset; -webkit-text-fill-color: var(--cyan); letter-spacing: normal;">[${esc(artist.name)}]</span>`;
        }
      }
    })
    .catch(() => {});
})();

// === Block 3 of 5 ===
function goToPaymentSettings() {
  // Close all modals first
  document.getElementById('paymentRequiredModal').classList.add('hidden');
  const modalOverlay = document.getElementById('modalOverlay');
  if (modalOverlay) {
    modalOverlay.classList.add('hidden');
  }
  
  // Switch to payments tab directly by manipulating classes
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  
  const paymentsTab = document.querySelector('.tab[onclick*="payments"]');
  if (paymentsTab) {
    paymentsTab.classList.add('active');
  }
  const paymentsContent = document.getElementById('payments-tab');
  if (paymentsContent) {
    paymentsContent.classList.add('active');
  }
  
  // Scroll to top of page
  window.scrollTo(0, 0);
}

function checkArtistPaymentMethod() {
  const complete = document.getElementById('artistConnectComplete');
  if (!complete || complete.style.display === 'none') {
    alert('Please connect your payout account in the Payments tab before booking gigs.');
    return false;
  }
  return true;
}
window.checkArtistPaymentMethod = checkArtistPaymentMethod;

// === Block 4 of 5 ===
(function () {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  if (!artistId) {
    window.location.href = "/app/user-profile.html";
    return;
  }

  const btn = document.getElementById("artistProfileBtn");
  if (btn) {
    btn.href = `/app/artist-profile.html?artist_id=${artistId}`;
  }
  
  const editBtn = document.getElementById("artistEditBtn");
  if (editBtn) {
    editBtn.href = `/app/artist-edit.html?artist_id=${artistId}`;
  }
})();

// v95: Initialize Activity Center (ARTIST)
document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");

  // Auto-switch to Activity tab on first load only if unread notifications exist
  // (NOT on every poll cycle — that would yank the user away from whatever tab they're on)
  window._activityAutoSwitchDone = false;
  window.onActivityCenterLoaded = function(unreadCount, entityType) {
    if (window._activityAutoSwitchDone) return;  // only switch once on initial load
    if (unreadCount > 0) {
      window._activityAutoSwitchDone = true;
      const activityTabBtn = document.querySelector('.tab[onclick*="activity"]');
      if (activityTabBtn) {
        switchTab('activity', activityTabBtn);
      }
    }
  };

  if (artistId && document.getElementById('activityCenter')) {
    
    window.activityCenterArtist = new ActivityCenter(
      'activityCenter',
      artistId,
      'artist'
    );
    // Start real-time polling — 30s interval, pauses when tab is hidden
    window.activityCenterArtist.startPolling(30000);
    
    // CUSTOMIZATION: Handle "All" button to deselect other filters
    setTimeout(() => {
      const activityEl = document.getElementById('activityCenter');
      if (activityEl) {
        // Add click handler to All button
        activityEl.addEventListener('click', (e) => {
          const target = e.target;
          if (target.classList.contains('filter-btn') && target.textContent.trim() === 'All') {
            // Deselect all other filter buttons
            const otherFilters = activityEl.querySelectorAll('.filter-btn:not([data-filter="all"])');
            otherFilters.forEach(btn => btn.classList.remove('active'));
          }
        });
      }
    }, 500);

  } else {
    console.warn('[v95 INIT] ActivityCenter not initialized');
  }
});

// v95: Auto-refresh artist notifications when returning to tab
document.addEventListener('visibilitychange', async () => {
  if (!document.hidden && window.activityCenterArtist?.loadNotifications) {
    await window.activityCenterArtist.loadNotifications();
  }
});

// Update My Venues badge count - v96: Only count approved venues
// v97: Added logging and more reliable timing
window.updateVenuesBadge = function() {
  if (window._artistAccessDenied) return;
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  if (!artistId) {
    return;
  }
  
  
  fetch(`/api/artist/preferred-venues?artist_id=${artistId}`, { credentials: 'include' })
    .then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then(venues => {
      // v96: Only count approved venues, not pending
      const approvedVenues = venues.filter(v => v.status === 'approved');
      const badge = document.getElementById('artistsBadge');
      if (badge) {
        badge.textContent = `(${approvedVenues.length})`;
      }
    })
    .catch(err => console.error('❌ v97: Failed to update venues badge:', err));
};

// Call on load - v97: Call immediately and also after delay
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    if (window.updateVenuesBadge) {
      window.updateVenuesBadge();
    }
  }, 500);
});


// === Block 5 of 5 ===
function switchTab(tabName, button) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  button.classList.add('active');
  document.getElementById(tabName + '-tab').classList.add('active');
  
  // If switching to Calendar tab, always collapse Search Gigs
  if (tabName === 'calendar') {
    const section = document.getElementById('searchGigsSection');
    const icon = document.getElementById('searchToggleIcon');
    if (section && icon) {
      section.style.display = 'none';
      icon.textContent = '▶';
    }
  }
  
  // v97: If switching to My Venues tab (artists), refresh the list and badge
  if (tabName === 'artists') {
    if (window.updateVenuesBadge) {
      window.updateVenuesBadge();
    }
    if (window.myVenuesRedesign && window.myVenuesRedesign.loadVenues) {
      window.myVenuesRedesign.loadVenues().then(() => {
        window.myVenuesRedesign.render();
      });
    }
  }
  
  // If switching to Users tab, load users
  if (tabName === 'users' && entityUsersManager) {
    entityUsersManager.loadUsers().then(() => {
      entityUsersManager.renderUsersList('entityUsersList');
      entityUsersManager.updateBadge('usersBadge');
    });
  }
  
  // If switching to Analytics tab, load analytics
  if (tabName === 'analytics') {
    loadArtistAnalytics();
  }

  // If switching to Payments tab, load payment notice
  if (tabName === 'payments') {
    loadPaymentNotice();
    if (typeof window.loadArtistEarningsHistory === 'function') window.loadArtistEarningsHistory();
  }
  
  // If switching to Taxes tab, load W9 data
  if (tabName === 'taxes') {
    loadW9();
    loadArtistContracts();
  }
}

// Legal/Taxes sub-tab switching (artist side)
function switchArtistLegalSubtab(name, btn) {
  document.querySelectorAll('.artist-legal-subtab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.artist-legal-subtab-content').forEach(c => { c.classList.remove('active'); c.style.display = 'none'; });
  btn.classList.add('active');
  const el = document.getElementById(name + '-subtab');
  if (el) { el.classList.add('active'); el.style.display = ''; }
  if (name === 'artistContracts') loadArtistContracts();
}

// Load artist's gig contracts
let _artistContracts = [];
let _artistContractsPage = 1;
const _artistContractsPerPage = 20;
let _artistContractsFilter = 'all'; // 'all' = show everything, 'pending' = upcoming, 'completed' = past
let _artistContractsSort = { col: 'gig_date', dir: 1 };
function setArtistContractsFilter(value) {
  _artistContractsFilter = value || 'all';
  _artistContractsPage = 1;
  renderArtistContracts();
}
function artistContractsSortBy(col) {
  if (_artistContractsSort.col === col) _artistContractsSort.dir *= -1;
  else { _artistContractsSort.col = col; _artistContractsSort.dir = 1; }
  _artistContractsPage = 1;
  renderArtistContracts();
}

async function loadArtistContracts() {
  const listEl = document.getElementById('artistContractsList');
  if (!listEl) return;
  
  const aid = new URLSearchParams(window.location.search).get('artist_id');
  if (!aid) { listEl.innerHTML = '<p style="color:var(--text-muted); font-size:0.85rem;">No artist selected.</p>'; return; }
  
  try {
    const res = await fetch(`/api/artists/${aid}/gig-contracts`, { credentials: 'include' });
    if (!res.ok) throw new Error('Failed');
    _artistContracts = await res.json();
    _artistContractsPage = 1;
    renderArtistContracts();
  } catch (e) {
    listEl.innerHTML = '<p style="color:var(--text-muted); font-size:0.85rem;">Unable to load contracts.</p>';
  }
}

function renderArtistContracts() {
  const listEl = document.getElementById('artistContractsList');
  const pagEl = document.getElementById('artistContractsPagination');
  if (!listEl) return;
  
  // Use local date (not UTC) so tonight's gigs don't show as 'completed' before midnight
  const _todayLocal = new Date();
  const today = `${_todayLocal.getFullYear()}-${String(_todayLocal.getMonth()+1).padStart(2,'0')}-${String(_todayLocal.getDate()).padStart(2,'0')}`;
  let filtered = _artistContracts.filter(c => {
    const d = (c.gig_date || '').slice(0, 10);
    if (_artistContractsFilter === 'pending') return d >= today;
    if (_artistContractsFilter === 'completed') return d < today;
    return true;
  });
  const sortCol = _artistContractsSort.col;
  const sortDir = _artistContractsSort.dir;
  filtered.sort((a, b) => {
    let va = a[sortCol]; let vb = b[sortCol];
    if (sortCol === 'gig_date') { va = va || ''; vb = vb || ''; return sortDir * (va.localeCompare(vb)); }
    if (sortCol === 'display_name') {
      const an = (a.venue_name || '') + (a.artist_name || '') + (a.gig_date || '');
      const bn = (b.venue_name || '') + (b.artist_name || '') + (b.gig_date || '');
      return sortDir * (an.localeCompare(bn));
    }
    if (sortCol === 'venue_name') { va = (a.venue_name || ''); vb = (b.venue_name || ''); return sortDir * (va.localeCompare(vb)); }
    if (sortCol === 'status') { va = (a.status || ''); vb = (b.status || ''); return sortDir * (va.localeCompare(vb)); }
    return 0;
  });
  
  if (filtered.length === 0) {
    listEl.innerHTML = '<p style="color:var(--text-muted); font-size:0.85rem;">No contracts in this view. Change "Show" to see Pending or Completed.</p>';
    if (pagEl) pagEl.style.display = 'none';
    return;
  }
  
  const totalPages = Math.ceil(filtered.length / _artistContractsPerPage);
  const start = (_artistContractsPage - 1) * _artistContractsPerPage;
  const pageItems = filtered.slice(start, start + _artistContractsPerPage);
  
  const arrow = (col) => _artistContractsSort.col === col ? (_artistContractsSort.dir === 1 ? ' ↑' : ' ↓') : '';
  const hdrStyle = 'padding:10px 12px; font-weight:600; font-size:0.8rem; color:var(--text-gray); cursor:pointer; user-select:none; border-bottom:1px solid var(--border);';
  let tableHtml = `
    <table style="width:100%; border-collapse:collapse;">
      <thead>
        <tr>
          <th style="${hdrStyle} text-align:left;" onclick="artistContractsSortBy('display_name')">Contract Name${arrow('display_name')}</th>
          <th style="${hdrStyle} text-align:left;" onclick="artistContractsSortBy('venue_name')">Venue Name${arrow('venue_name')}</th>
          <th style="${hdrStyle} text-align:left;" onclick="artistContractsSortBy('gig_date')">Date${arrow('gig_date')}</th>
          <th style="${hdrStyle} text-align:left;" onclick="artistContractsSortBy('status')">Status${arrow('status')}</th>
          <th style="${hdrStyle} text-align:right;">Download</th>
        </tr>
      </thead>
      <tbody>`;
  
  pageItems.forEach(c => {
    const date = c.gig_date || '';
    let fmtDate = '';
    if (date) {
      const parts = date.split('-');
      if (parts.length === 3) {
        const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
        fmtDate = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      } else {
        fmtDate = new Date(date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      }
    }
    const venueName = c.venue_name || 'Unknown Venue';
    const artistName = c.artist_name || 'Artist';
    const gigTitle = c.gig_title || 'Gig';
    let displayName = '';
    if (date && venueName && artistName) {
      const parts = date.split('-');
      const dateStr = parts.length === 3 ? parts[0] + '_' + parts[1] + '_' + parts[2] : '';
      const venueSafe = venueName.replace(/\s+/g, '').replace(/[^a-zA-Z0-9]/g, '').substring(0, 60);
      const artistSafe = artistName.replace(/\s+/g, '').replace(/[^a-zA-Z0-9]/g, '').substring(0, 60);
      if (dateStr && venueSafe && artistSafe) displayName = dateStr + '_' + venueSafe + '_' + artistSafe;
    }
    if (!displayName) displayName = c.template_name || (venueName + ' — ' + gigTitle);
    
    let statusBadge = '';
    if (c.status === 'executed' || c.status === 'countersigned' || c.status === 'fully_signed') {
      statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(34,197,94,0.15); color:#22c55e; white-space:nowrap;">Fully Signed</span>';
    } else if (c.status === 'artist_signed') {
      statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(234,179,8,0.15); color:#eab308; white-space:nowrap;">Awaiting Venue</span>';
    } else if (c.status === 'pending') {
      statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(239,68,68,0.15); color:#ef4444; white-space:nowrap;">Pending</span>';
    } else if (c.status === 'expired') {
      statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(107,114,128,0.15); color:#9ca3af; white-space:nowrap;">Expired</span>';
    } else if (c.status === 'cancelled') {
      statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(239,68,68,0.15); color:#ef4444; white-space:nowrap;">Cancelled</span>';
    }
    
    let downloadBtn = '';
    if (c.signed_pdf_path) {
      downloadBtn = `<a href="${c.signed_pdf_path}" download style="color:var(--cyan); font-size:0.75rem;">Download</a>`;
    } else if (c.pdf_file_path) {
      downloadBtn = `<a href="${c.pdf_file_path}" download style="color:var(--cyan); font-size:0.75rem;">Download</a>`;
    } else {
      downloadBtn = `<a href="#" onclick="downloadArtistContract(${c.id});return false;" style="color:var(--cyan); font-size:0.75rem; cursor:pointer;">Download</a>`;
    }
    
    tableHtml += `
      <tr style="border-bottom:1px solid var(--border);">
        <td style="padding:10px 12px; font-size:0.85rem; color:var(--text-primary); overflow:hidden; text-overflow:ellipsis; max-width:280px;" title="${displayName}">${displayName}</td>
        <td style="padding:10px 12px; font-size:0.85rem; color:var(--text-gray);">${venueName}</td>
        <td style="padding:10px 12px; font-size:0.85rem; color:var(--text-gray); white-space:nowrap;">${fmtDate}</td>
        <td style="padding:10px 12px;">${statusBadge}</td>
        <td style="padding:10px 12px; text-align:right;">${downloadBtn}</td>
      </tr>`;
  });
  
  tableHtml += '</tbody></table>';
  listEl.innerHTML = tableHtml;
  
  if (pagEl) {
    if (totalPages <= 1) {
      pagEl.style.display = 'none';
    } else {
      pagEl.style.display = 'flex';
      let pagHtml = '';
      pagHtml += `<button class="btn ghost" onclick="artistContractsGoPage(${_artistContractsPage - 1})" style="padding:4px 10px; font-size:0.75rem;" ${_artistContractsPage <= 1 ? 'disabled' : ''}>← Prev</button>`;
      pagHtml += `<span style="font-size:0.8rem; color:var(--text-gray);">Page ${_artistContractsPage} of ${totalPages}</span>`;
      pagHtml += `<button class="btn ghost" onclick="artistContractsGoPage(${_artistContractsPage + 1})" style="padding:4px 10px; font-size:0.75rem;" ${_artistContractsPage >= totalPages ? 'disabled' : ''}>Next →</button>`;
      pagEl.innerHTML = pagHtml;
    }
  }
}

function artistContractsGoPage(page) {
  _artistContractsPage = page;
  renderArtistContracts();
}

function downloadArtistContract(contractId) {
  window.open(`/api/gig-contracts/${contractId}/download-pdf`, '_blank');
}

async function loadPaymentNotice() {
  const notice = document.getElementById('paymentNotice');
  if (!notice) return;
  
  try {
    const response = await fetch('/api/payment-info', { credentials: 'include' });
    if (!response.ok) return;
    const info = await response.json();
    
    const dayWord = info.delay_days === 1 ? 'day' : 'days';
    notice.style.background = 'rgba(6, 182, 212, 0.08)';
    notice.style.border = '1px solid rgba(6, 182, 212, 0.2)';
    notice.style.color = 'var(--cyan)';
    notice.innerHTML = `💳 Payments are automatically processed <strong>${info.delay_days} ${dayWord}</strong> after the gig at <strong>${info.processing_time_display}</strong>.`;
    notice.style.display = 'block';
  } catch (e) {
    console.error('Error loading payment notice:', e);
  }
}

// Analytics bubble switching (used by both artist and venue)
function switchAnalyticsBubble(prefix, section) {
  // Update bubble styles
  document.querySelectorAll(`[id^="${prefix}Bubble_"]`).forEach(bubble => {
    bubble.style.background = 'rgba(139,92,246,0.1)';
    bubble.style.border = '1px solid rgba(139,92,246,0.2)';
  });
  const activeBubble = document.getElementById(`${prefix}Bubble_${section}`);
  if (activeBubble) {
    activeBubble.style.background = 'rgba(139,92,246,0.2)';
    activeBubble.style.border = '2px solid rgba(139,92,246,0.5)';
  }
  
  // Show/hide content sections
  document.querySelectorAll(`[id^="${prefix}AnalyticsContent_"]`).forEach(content => {
    content.style.display = 'none';
  });
  const activeContent = document.getElementById(`${prefix}AnalyticsContent_${section}`);
  if (activeContent) {
    activeContent.style.display = 'block';
  }
}

async function loadArtistAnalytics() {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  if (!artistId) return;
  
  try {
    const response = await fetch(`/api/analytics/stats/artist/${artistId}`, { credentials: 'include' });
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    
    // Update stat cards
    document.getElementById('artistAnalyticsTotalClicks').textContent = data.total_clicks || 0;
    document.getElementById('artistAnalyticsUniqueVisitors').textContent = data.unique_visitors || 0;
    document.getElementById('artistAnalytics7d').textContent = data.clicks_last_7d || 0;
    document.getElementById('artistAnalytics30d').textContent = data.clicks_last_30d || 0;
    
    // Viewer cities
    const viewerCities = document.getElementById('artistViewerCities');
    if (data.viewer_cities && data.viewer_cities.length > 0) {
      viewerCities.innerHTML = `
        <table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">
          <thead>
            <tr style="border-bottom: 1px solid var(--border);">
              <th style="text-align: left; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">CITY</th>
              <th style="text-align: left; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">STATE</th>
              <th style="text-align: right; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">VIEWS</th>
            </tr>
          </thead>
          <tbody>
            ${data.viewer_cities.map(city => `
              <tr style="border-bottom: 1px solid var(--border);">
                <td style="padding: 8px;">${city.city || 'Unknown'}</td>
                <td style="padding: 8px;">${city.state || ''}</td>
                <td style="padding: 8px; text-align: right; font-weight: 600; color: var(--cyan);">${city.count}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }
    
    // Popular gigs
    const popularGigs = document.getElementById('artistPopularGigs');
    if (data.clicks_by_gig && data.clicks_by_gig.length > 0) {
      popularGigs.innerHTML = `
        <table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">
          <thead>
            <tr style="border-bottom: 1px solid var(--border);">
              <th style="text-align: left; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">DATE</th>
              <th style="text-align: left; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">VENUE</th>
              <th style="text-align: right; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">VIEWS</th>
              <th style="text-align: right; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">UNIQUE</th>
            </tr>
          </thead>
          <tbody>
            ${data.clicks_by_gig.map(gig => `
              <tr style="border-bottom: 1px solid var(--border);">
                <td style="padding: 8px;">${gig.gig_date || 'Unknown'}</td>
                <td style="padding: 8px;">${gig.venue_name || 'Unknown'}</td>
                <td style="padding: 8px; text-align: right; font-weight: 600; color: var(--cyan);">${gig.clicks}</td>
                <td style="padding: 8px; text-align: right; color: var(--text-gray);">${gig.unique_clicks}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }
    
    // Recent activity - split into 7-day and 30-day views
    if (data.recent_clicks && data.recent_clicks.length > 0) {
      const now = new Date();
      const sevenDaysAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
      const thirtyDaysAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
      
      const renderClicks = (clicks) => clicks.map(click => {
        const time = formatUTC(click.created_at, 'short');
        return `
          <div style="padding: 8px 0; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem;">
            <div>
              <span style="color: var(--text);">${click.gig_date || 'Unknown gig'}</span>
              <span style="color: var(--text-gray); margin-left: 8px;">@ ${click.venue_name || 'Unknown'}</span>
              <span style="color: var(--text-gray); margin-left: 8px;">${click.city || ''}${click.state ? ', ' + click.state : ''}</span>
            </div>
            <span style="color: var(--text-gray); font-size: 0.75rem;">${time}</span>
          </div>
        `;
      }).join('');
      
      const clicks7 = data.recent_clicks.filter(c => parseUTC(c.created_at) >= sevenDaysAgo);
      const clicks30 = data.recent_clicks.filter(c => parseUTC(c.created_at) >= thirtyDaysAgo);
      
      const activity7 = document.getElementById('artistRecentActivity7');
      activity7.innerHTML = clicks7.length > 0 ? renderClicks(clicks7) : '<p style="color: var(--text-muted); font-size: 0.85rem;">No activity in the last 7 days</p>';
      
      const activity30 = document.getElementById('artistRecentActivity30');
      activity30.innerHTML = clicks30.length > 0 ? renderClicks(clicks30) : '<p style="color: var(--text-muted); font-size: 0.85rem;">No activity in the last 30 days</p>';
    }
    
  } catch (error) {
    console.error('Error loading artist analytics:', error);
  }
}

// Initialize entity users manager (non-blocking)
// Wait longer to ensure artist data is loaded
setTimeout(() => {
  if (window._artistAccessDenied) return;
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  
  if (artistId) {
    // Try to get artist name
    fetch(`/api/artists/${artistId}`, { credentials: 'include' })
      .then(res => {
        if (!res.ok) throw new Error('Not found');
        return res.json();
      })
      .then(artist => {
        const artistName = artist.name || 'this artist';
        window.entityUsersManager = initEntityUsers('artist', artistId, artistName);
      })
      .catch(err => {
        console.warn('⚠️ Entity Users: Could not load artist name:', err);
        window.entityUsersManager = initEntityUsers('artist', artistId, 'this artist');
      });
  }
}, 500);

// Navigate to Calendar tab, open Search Gigs, and search by venue name
window.searchVenueFromPreferred = function(venueName, venueCity) {
  // 1. Switch to Calendar tab
  const calendarBtn = document.querySelector('.tab[onclick*="calendar"]');
  if (calendarBtn) {
    switchTab('calendar', calendarBtn);
  }

  // 2. Open Search Gigs section
  const section = document.getElementById('searchGigsSection');
  const icon = document.getElementById('searchToggleIcon');
  if (section) {
    section.style.display = 'block';
    if (icon) icon.textContent = '▼';
  }

  // 3. Fill venue name and city, keep radius as-is
  const venueInput = document.getElementById('searchVenue');
  if (venueInput) {
    venueInput.value = venueName;
  }
  const cityInput = document.getElementById('searchCity');
  if (cityInput) {
    cityInput.value = venueCity || '';
  }
  const minPayInput = document.getElementById('minPay');
  if (minPayInput) minPayInput.value = '0';

  // 4. Apply filters
  if (window.applyFilters) {
    window.applyFilters();
  }

  // 5. Scroll to top so user sees the calendar
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

// Toggle Search Gigs Section
document.getElementById('toggleSearchGigs').addEventListener('click', function(e) {
  e.preventDefault();
  const section = document.getElementById('searchGigsSection');
  const icon = document.getElementById('searchToggleIcon');
  
  if (section.style.display === 'none') {
    section.style.display = 'block';
    icon.textContent = '▼';
  } else {
    section.style.display = 'none';
    icon.textContent = '▶';
  }
});

// ==========================================
// ARTIST PAYMENT SETTINGS (Stripe Connect)
// ==========================================

// Load payment settings when page loads
document.addEventListener('DOMContentLoaded', function() {
  setTimeout(loadArtistPaymentSettings, 300);
});

// === Additional Block (Phase 5 pass 2) ===
// ==========================================
// W9 TAX FORM

function showW9Error(msg) {
  // Remove any existing W9 error
  const existing = document.getElementById('w9ErrorBanner');
  if (existing) existing.remove();
  const banner = document.createElement('div');
  banner.id = 'w9ErrorBanner';
  banner.style.cssText = 'background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.4);border-radius:8px;padding:12px 16px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;gap:12px;';
  banner.innerHTML = `<span style="color:#ef4444;font-size:0.88rem;">⚠️ ${msg}</span><button onclick="document.getElementById('w9ErrorBanner').remove()" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:1.1rem;line-height:1;padding:0;">×</button>`;
  // Insert above the form
  const form = document.getElementById('w9FormSection');
  if (form) form.insertBefore(banner, form.firstChild);
  else { const s = document.getElementById('w9Status'); if (s) s.after(banner); }
  banner.scrollIntoView({behavior:'smooth', block:'nearest'});
}
// ==========================================
let w9Data = null;
let w9IsEditing = false;

// Populate state dropdown from US_STATES
(function() {
  const sel = document.getElementById('w9State');
  if (sel && typeof US_STATES !== 'undefined') {
    sel.innerHTML = '<option value="">Select...</option>' + US_STATES.map(s => `<option value="${s.code}">${s.name}</option>`).join('');
  }
})();

// Toggle "other" classification field
document.getElementById('w9TaxClassification').addEventListener('change', function() {
  document.getElementById('w9OtherClassRow').style.display = this.value === 'other' ? 'block' : 'none';
});

// Update TIN label when type changes
document.getElementById('w9TinType').addEventListener('change', function() {
  const label = this.value === 'ein' ? 'EIN' : 'SSN';
  document.getElementById('w9TinLabel').textContent = label;
});

async function loadW9() {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  if (!artistId) return;
  
  try {
    const res = await fetch(`/api/artists/${artistId}/w9`, { credentials: 'include' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    w9Data = data;
    renderW9Status(data);
  } catch (err) {
    document.getElementById('w9Status').innerHTML = `
      <p style="color: var(--text-muted); font-size: 0.85rem;">Unable to load tax information.</p>
    `;
  }
}

function renderW9Status(data) {
  const statusEl = document.getElementById('w9Status');
  const formSection = document.getElementById('w9FormSection');
  
  if (data.status === 'not_filed') {
    // No W9 on file
    statusEl.innerHTML = `
      <div style="background: rgba(249,115,22,0.1); border: 1px solid rgba(249,115,22,0.3); border-radius: 8px; padding: 16px; display: flex; justify-content: space-between; align-items: center;">
        <div>
          <div style="font-weight: 600; color: #f97316; font-size: 0.95rem;">⚠️ No W-9 on File</div>
          <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">Complete your W-9 so venues can issue you a 1099 at year end.</div>
        </div>
        <button class="btn primary" onclick="showW9Form()" style="white-space: nowrap;">Fill Out W-9</button>
      </div>
    `;
    formSection.style.display = 'none';
    
    // Pre-fill from artist data
    if (data.prefill) {
      document.getElementById('w9TaxName').value = data.prefill.tax_name || '';
      document.getElementById('w9City').value = data.prefill.city || '';
      document.getElementById('w9State').value = data.prefill.state || '';
    }
  } else {
    // W9 is on file
    const needsRecert = data.needs_recertification;
    const classLabels = {
      'individual': 'Individual / Sole Proprietor',
      'single_llc': 'Single-Member LLC',
      'c_corp': 'C Corporation',
      's_corp': 'S Corporation',
      'partnership': 'Partnership',
      'trust_estate': 'Trust / Estate',
      'llc_c': 'LLC (C Corp)',
      'llc_s': 'LLC (S Corp)',
      'llc_p': 'LLC (Partnership)',
      'other': 'Other'
    };
    
    const certDate = data.certified_at ? new Date(data.certified_at).toLocaleDateString() : 'N/A';
    const tinDisplay = data.tin 
      ? (data.tin_type === 'ein' ? `EIN: ${data.tin}` : `SSN: ${data.tin}`)
      : (data.tin_type === 'ein' ? `EIN: ***-**${data.tin_last4}` : `SSN: ***-**-${data.tin_last4}`);
    
    statusEl.innerHTML = `
      <div style="background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3); border-radius: 8px; padding: 16px; margin-bottom: ${needsRecert ? '12px' : '0'};">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
          <div style="font-weight: 600; color: #22c55e; font-size: 0.95rem;">✓ W-9 on File (${data.tax_year})</div>
          <button class="btn ghost" onclick="editW9()" style="padding: 4px 12px; font-size: 0.8rem;">Edit</button>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 0.85rem;">
          <div><span style="color: var(--text-muted);">Name:</span> <span style="color: var(--text);">${data.tax_name || ''}</span></div>
          <div><span style="color: var(--text-muted);">Classification:</span> <span style="color: var(--text);">${classLabels[data.tax_classification] || data.tax_classification}</span></div>
          ${data.business_name ? `<div><span style="color: var(--text-muted);">Business:</span> <span style="color: var(--text);">${data.business_name}</span></div>` : ''}
          <div><span style="color: var(--text-muted);">${tinDisplay}</span></div>
          <div><span style="color: var(--text-muted);">Address:</span> <span style="color: var(--text);">${[data.address_line_1, data.city, data.state, data.zip_code].filter(Boolean).join(', ')}</span></div>
          <div><span style="color: var(--text-muted);">Certified:</span> <span style="color: var(--text);">${certDate}</span></div>
        </div>
      </div>
      ${needsRecert ? `
        <div style="background: rgba(249,115,22,0.1); border: 1px solid rgba(249,115,22,0.3); border-radius: 8px; padding: 12px; display: flex; justify-content: space-between; align-items: center;">
          <div>
            <div style="font-weight: 600; color: #f97316; font-size: 0.85rem;">🔄 Recertification Needed for ${new Date().getFullYear()}</div>
            <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 2px;">Your W-9 was last certified for ${data.tax_year}. Confirm your info is still correct.</div>
          </div>
          <button class="btn primary" onclick="recertifyW9()" style="white-space: nowrap; padding: 6px 14px; font-size: 0.85rem;" id="recertifyBtn">Recertify</button>
        </div>
      ` : ''}
    `;
    formSection.style.display = 'none';
  }
}

function showW9Form() {
  w9IsEditing = false;
  document.getElementById('w9FormSection').style.display = 'block';
  document.getElementById('w9CancelBtn').style.display = w9Data && w9Data.status !== 'not_filed' ? 'inline-block' : 'none';
  document.getElementById('w9Tin').value = '';
  document.getElementById('w9Certify').checked = false;
}

function editW9() {
  w9IsEditing = true;
  // Populate form with existing data
  if (w9Data) {
    document.getElementById('w9TaxName').value = w9Data.tax_name || '';
    document.getElementById('w9BusinessName').value = w9Data.business_name || '';
    document.getElementById('w9TaxClassification').value = w9Data.tax_classification || '';
    document.getElementById('w9OtherClassification').value = w9Data.other_classification || '';
    document.getElementById('w9OtherClassRow').style.display = w9Data.tax_classification === 'other' ? 'block' : 'none';
    document.getElementById('w9ExemptPayeeCode').value = w9Data.exempt_payee_code || '';
    document.getElementById('w9FatcaCode').value = w9Data.fatca_exemption_code || '';
    document.getElementById('w9Address1').value = w9Data.address_line_1 || '';
    document.getElementById('w9Address2').value = w9Data.address_line_2 || '';
    document.getElementById('w9City').value = w9Data.city || '';
    document.getElementById('w9State').value = w9Data.state || '';
    document.getElementById('w9Zip').value = w9Data.zip_code || '';
    document.getElementById('w9TinType').value = w9Data.tin_type || 'ssn';
    document.getElementById('w9TinLabel').textContent = w9Data.tin_type === 'ein' ? 'EIN' : 'SSN';
    document.getElementById('w9Tin').value = w9Data.tin || '';
    if (!w9Data.tin) {
      document.getElementById('w9Tin').placeholder = 'Re-enter 9 digits to update';
    }
  }
  document.getElementById('w9Certify').checked = false;
  document.getElementById('w9CancelBtn').style.display = 'inline-block';
  document.getElementById('w9FormSection').style.display = 'block';
}

function cancelW9Edit() {
  document.getElementById('w9FormSection').style.display = 'none';
  if (w9Data) renderW9Status(w9Data);
}

async function saveW9() {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  if (!artistId) return;
  
  const tin = document.getElementById('w9Tin').value.replace(/[^0-9]/g, '');
  const taxName = document.getElementById('w9TaxName').value.trim();
  const taxClass = document.getElementById('w9TaxClassification').value;
  const certified = document.getElementById('w9Certify').checked;
  
  // Validation
  if (!taxName) { showW9Error('Please enter your legal name.'); return; }
  if (!taxClass) { showW9Error('Please select a tax classification.'); return; }
  if (!tin) { showW9Error('Please enter your SSN or EIN.'); return; }
  if (tin.length !== 9) { showW9Error('SSN/EIN must be exactly 9 digits (numbers only).'); return; }
  if (!certified) { showW9Error('You must check the certification box to save.'); return; }
  
  const btn = document.getElementById('w9SaveBtn');
  btn.textContent = 'Saving...';
  btn.disabled = true;
  
  try {
    const res = await fetch(`/api/artists/${artistId}/w9`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tax_name: taxName,
        business_name: document.getElementById('w9BusinessName').value.trim(),
        tax_classification: taxClass,
        other_classification: document.getElementById('w9OtherClassification').value.trim(),
        exempt_payee_code: document.getElementById('w9ExemptPayeeCode').value.trim(),
        fatca_exemption_code: document.getElementById('w9FatcaCode').value.trim(),
        address_line_1: document.getElementById('w9Address1').value.trim(),
        address_line_2: document.getElementById('w9Address2').value.trim(),
        city: document.getElementById('w9City').value.trim(),
        state: document.getElementById('w9State').value.trim(),
        zip_code: document.getElementById('w9Zip').value.trim(),
        tin_type: document.getElementById('w9TinType').value,
        tin: tin,
        certified: certified
      })
    });
    
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to save');
    }
    
    // Reload to show updated status
    await loadW9();
    document.getElementById('w9FormSection').style.display = 'none';
    
  } catch (err) {
    showW9Error('Error saving W-9: ' + err.message);
  } finally {
    btn.textContent = 'Save W-9';
    btn.disabled = false;
  }
}

async function recertifyW9() {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  if (!artistId) return;
  
  const btn = document.getElementById('recertifyBtn');
  btn.textContent = 'Recertifying...';
  btn.disabled = true;
  
  try {
    const res = await fetch(`/api/artists/${artistId}/w9/recertify`, {
      method: 'POST',
      credentials: 'include'
    });
    
    if (!res.ok) throw new Error('Failed to recertify');
    
    await loadW9();
  } catch (err) {
    showW9Error('Error recertifying: ' + err.message);
  } finally {
    btn.textContent = 'Recertify';
    btn.disabled = false;
  }
}

// Load W9 when switching to Taxes tab
document.addEventListener('DOMContentLoaded', function() {
  // Lazy load - only fetch when tab is clicked
  const origSwitchTab = window.switchTab;
  if (origSwitchTab) {
    // Will be loaded on first tab click via the switchTab override below
  }
});

// ── MESSAGES HEADER BUTTON ───────────────────────────────────────────────────
window.showRecentMessages = function() {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get('artist_id');
  openInboxModal({ side: 'artist', artistId: artistId ? parseInt(artistId) : null });
};

// Show Messages button and start unread badge polling
(function initArtistMessages() {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get('artist_id');
  if (!artistId) return;
  const btn = document.getElementById('headerMsgBtn');
  if (btn) btn.style.display = '';
  if (typeof startUnreadBadgePolling === 'function') {
    const _p = new URLSearchParams(window.location.search);
    const _aid = _p.get('artist_id');
    startUnreadBadgePolling(30000, _aid ? { artist_id: parseInt(_aid) } : {});
  }
})();
