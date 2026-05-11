/**
 * Artist Stripe Connect Payment Management
 * Handles: Connect Express onboarding, payout status, earnings history
 */

// Styled modal (reuse if venue loaded first, otherwise define)
if (typeof showPaymentModal !== 'function') {
  window.showPaymentModal = function(title, message, type) {
    type = type || 'info';
    var colors = {
      info: { border: 'rgba(99,91,255,0.4)', title: '#635bff' },
      success: { border: 'rgba(16,185,129,0.4)', title: '#10b981' },
      error: { border: 'rgba(239,68,68,0.4)', title: '#ef4444' },
      warning: { border: 'rgba(245,158,11,0.4)', title: '#f59e0b' }
    };
    var c = colors[type] || colors.info;
    var existing = document.getElementById('paymentModal');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.id = 'paymentModal';
    modal.innerHTML =
      '<div style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;" onclick="if(event.target===this)this.parentElement.remove()">' +
        '<div style="background:#1a1f2e;border:1px solid ' + c.border + ';border-radius:12px;padding:28px;max-width:420px;width:90%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.5);">' +
          '<h3 style="color:' + c.title + ';font-size:1rem;font-weight:700;margin:0 0 12px 0;">' + title + '</h3>' +
          '<p style="color:#9ca3af;font-size:0.85rem;line-height:1.6;margin:0 0 20px 0;">' + message + '</p>' +
          '<button onclick="this.closest(\'#paymentModal\').remove()" style="padding:10px 32px;background:' + c.title + ';color:white;border:none;border-radius:6px;font-size:0.85rem;font-weight:600;cursor:pointer;">OK</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
  };
}

function getArtistId() {
  var params = new URLSearchParams(window.location.search);
  return params.get("artist_id") || window.currentArtistId;
}

// Start Stripe Connect onboarding - redirects to Stripe's hosted page
async function artistStartConnect() {
  var artistId = getArtistId();
  if (!artistId) return;
  var btn = document.getElementById('artistConnectBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Connecting to Stripe...'; }
  try {
    var res = await fetch('/api/stripe/artist/' + artistId + '/create-connect-account', { method: 'POST', credentials: 'include' });
    if (!res.ok) {
      var err = await res.json().catch(function() { return {}; });
      var msg = err.detail || 'Failed to start Stripe onboarding.';
      if (msg.indexOf('not configured') > -1 || msg.indexOf('Stripe') > -1) {
        msg = 'Stripe is not configured yet. The platform admin needs to add Stripe API keys in Admin → Payments before onboarding can begin.';
      }
      showPaymentModal('Setup Error', msg, 'error');
      return;
    }
    var data = await res.json();
    // Redirect to Stripe's hosted onboarding page
    window.location.href = data.url;
  } catch (e) {
    console.error('Connect error:', e);
    showPaymentModal('Connection Error', 'Unable to reach the payment server. Please check your internet connection and try again.', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔗 Connect with Stripe'; }
  }
}

// Check Connect status and show appropriate UI
async function loadArtistConnectStatus() {
  var artistId = getArtistId();
  if (!artistId) return;
  var notSetup = document.getElementById('artistConnectNotSetup');
  var pending = document.getElementById('artistConnectPending');
  var complete = document.getElementById('artistConnectComplete');
  if (!notSetup) return;
  try {
    var res = await fetch('/api/stripe/artist/' + artistId + '/connect-status', { credentials: 'include' });
    if (!res.ok) return;
    var data = await res.json();
    window._artistConnectReady = !!(data.connected && data.onboarding_complete);
    if (!data.connected) {
      notSetup.style.display = 'block'; pending.style.display = 'none'; complete.style.display = 'none';
    } else if (!data.onboarding_complete) {
      notSetup.style.display = 'none'; pending.style.display = 'block'; complete.style.display = 'none';
    } else {
      notSetup.style.display = 'none'; pending.style.display = 'none'; complete.style.display = 'block';
      var bankName = document.getElementById('artistBankName');
      var bankLast4 = document.getElementById('artistBankLast4');
      if (bankName && data.bank_name) bankName.textContent = data.bank_name;
      if (bankLast4 && data.bank_last4) bankLast4.textContent = data.bank_last4;
    }
  } catch (e) { console.error('Connect status error:', e); }
}

// Open Stripe Express dashboard
async function artistOpenDashboard() {
  var artistId = getArtistId();
  if (!artistId) return;
  try {
    var res = await fetch('/api/stripe/artist/' + artistId + '/dashboard-link', { method: 'POST', credentials: 'include' });
    if (!res.ok) {
      showPaymentModal('Error', 'Unable to open Stripe dashboard. Please try again.', 'error');
      return;
    }
    var data = await res.json();
    window.open(data.url, '_blank');
  } catch (e) { console.error('Dashboard link error:', e); }
}

function checkArtistPaymentMethod() {
  var payoutReady = window._artistConnectReady || false;
  var w9Ready = window._artistW9Ready || false;
  
  // Also check DOM as fallback for payout
  if (!payoutReady) {
    var complete = document.getElementById('artistConnectComplete');
    payoutReady = complete && complete.style.display !== 'none';
  }
  
  if (payoutReady && w9Ready) {
    return true;
  }
  
  // Build message with links to the right pages
  var params = new URLSearchParams(window.location.search);
  var artistIdParam = params.get('artist_id') || '';
  var missing = [];
  if (!payoutReady) missing.push('<li style="margin-bottom:8px;"><strong>Payout Account</strong> — Set up your Stripe Connect account to receive payments.<br><a href="javascript:void(0)" onclick="var m=document.getElementById(\'paymentModal\'); if(m)m.remove(); var t=document.querySelector(\'.tab[onclick*=payments]\'); if(t)t.click();" style="color:#a78bfa;">Go to Payments Tab →</a></li>');
  if (!w9Ready) missing.push('<li style="margin-bottom:8px;"><strong>W9 Tax Information</strong> — Complete your W9 form for tax compliance.<br><a href="javascript:void(0)" onclick="var m=document.getElementById(\'paymentModal\'); if(m)m.remove(); var t=document.querySelector(\'.tab[onclick*=taxes]\'); if(t)t.click();" style="color:#a78bfa;">Go to Tax Information Tab →</a></li>');
  
  showPaymentModal('Setup Required Before Booking', 
    '<p style="margin-bottom:12px;">Please complete the following before booking gigs:</p><ul style="text-align:left;padding-left:20px;margin:0;">' + missing.join('') + '</ul>', 
    'warning');
  return false;
}
window.checkArtistPaymentMethod = checkArtistPaymentMethod;

async function loadArtistEarningsHistory() {
  var artistId = getArtistId();
  if (!artistId) return;
  try {
    var res = await fetch('/api/stripe/artist/' + artistId + '/transactions', { credentials: 'include' });
    if (!res.ok) return;
    var txns = await res.json();
    var container = document.getElementById('artistEarningsHistory');
    if (!txns || txns.length === 0) { container.innerHTML = '<p style="color:var(--text-muted);font-size:0.875rem;">No earnings history yet</p>'; return; }

    // Store data for sorting/pagination/export
    var nowMs = Date.now();
    // Note: pre-gig cancellations DELETE the transaction entirely (in
    // cleanup_gig_records) so they never reach this list. Rows here with
    // status='payment_cancelled' are post-gig payment cancellations — venue
    // refused to pay after the gig. Artist should see these as "Cancelled red".
    window._artistEarnData = txns.map(function(t) {
      var statusMap = { paid:'Paid', transferred:'Processing', test:'Test', scheduled:'Upcoming', pending:'Upcoming', charged:'Processing', pending_transfer:'Processing', charge_retry:'Processing', payment_failed:'Issue', transfer_failed:'Issue', payment_cancelled:'Cancelled' };
      var colorMap = { paid:'#10b981', transferred:'#f59e0b', test:'#60a5fa', scheduled:'#8b5cf6', pending:'#8b5cf6', charged:'#f59e0b', pending_transfer:'#f59e0b', charge_retry:'#f97316', payment_failed:'#ef4444', transfer_failed:'#ef4444', payment_cancelled:'#f97316' };
      var statusTip = { transferred:'Transfer sent to your Stripe account — funds held while the venue payment settles, then released to your bank (typically 5–7 business days after the gig)', paid:'Deposited in your bank account', scheduled:'Scheduled for processing after the gig', pending:'Pending processing', charged:'Venue charged — transfer in progress (bank deposit typically 5–7 business days after the gig)', pending_transfer:'Payment processing — payout typically arrives within 5–7 business days of the gig', charge_retry:'Retrying charge', payment_failed:'Payment issue — contact support', transfer_failed:'Transfer issue — contact support', upcoming:'Gig is upcoming — payout will process after the gig completes' };
      window._artistEarnStatusTip = statusTip;
      // Format time from "HH:MM" 24h or similar to 12h display
      var rawTime = t.gig_time || t.start_time || '';
      var displayTime = rawTime;
      if (rawTime && rawTime.indexOf(':') > -1) {
        var parts = rawTime.split(':');
        var h = parseInt(parts[0], 10);
        var m = parts[1] || '00';
        var ampm = h >= 12 ? 'PM' : 'AM';
        h = h % 12 || 12;
        displayTime = h + ':' + m + ' ' + ampm;
      }
      var artistPayout = (t.artist_payout_cents || 0) / 100;
      var gigFee = (t.amount_cents || 0) / 100;
      var rawTimeSort = t.gig_time || t.start_time || '00:00';
      var timeParts = rawTimeSort.split(':');
      var timeSortVal = parseInt(timeParts[0] || 0) * 60 + parseInt(timeParts[1] || 0);
      var dateTimeSort = (t.gig_date ? new Date(t.gig_date + 'T00:00:00').getTime() : 0) + timeSortVal * 60000;

      // Lifecycle:
      //   Future (dateTimeSort > nowMs): "Upcoming" purple
      //   Past + non-terminal status: "Processing" orange (gig started, payout pending)
      //   Terminal: use status map (Paid green, Cancelled red)
      var TERMINAL = { paid:1, payment_cancelled:1, payment_failed:1, transfer_failed:1 };
      var displayStatus = statusMap[t.status] || 'Pending';
      var displayColor  = colorMap[t.status] || '#f59e0b';
      if (!TERMINAL[t.status]) {
        if (dateTimeSort > nowMs) {
          displayStatus = 'Upcoming';
          displayColor  = '#8b5cf6';  // purple
        } else {
          displayStatus = 'Processing';
          displayColor  = '#f59e0b';  // orange
        }
      }

      return {
        venue_name: t.venue_name || 'Venue',
        venue_id: t.resolved_venue_id || '',
        gig_date: t.gig_date || '',
        gig_date_sort: dateTimeSort,
        gig_time: displayTime,
        gig_fee: gigFee,
        platform_fee: gigFee - artistPayout,
        total_paid: artistPayout,
        amount: artistPayout,
        status: displayStatus,
        statusColor: displayColor,
        rawStatus: t.status || 'pending',
        cancel_reason: t.cancel_reason || ''
      };
    });
    window._artistEarnSort = { col: 'gig_date_sort', dir: -1 };  // descending: most recent first
    window._artistEarnFilter = 'all';
    window._artistEarnPage = 1;
    renderArtistEarningsTable();
  } catch (e) { console.error('Load earnings error:', e); }
}

function renderArtistEarningsTable() {
  var container = document.getElementById('artistEarningsHistory');
  var data = (window._artistEarnData || []).slice();
  var sort = window._artistEarnSort || { col: 'gig_date_sort', dir: 1 };
  var page = window._artistEarnPage || 1;
  var perPage = 10;
  var now = Date.now();

  // Apply pending/completed/all filter
  var filterMode = window._artistEarnFilter || 'all';
  data = data.filter(function(t) {
    if (filterMode === 'all') return true;
    var isPast = t.gig_date_sort < now;
    return filterMode === 'completed' ? isPast : !isPast;
  });

  // Sort
  data.sort(function(a, b) {
    var av = a[sort.col], bv = b[sort.col];
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av < bv) return -1 * sort.dir;
    if (av > bv) return 1 * sort.dir;
    return 0;
  });

  var totalPages = Math.max(1, Math.ceil(data.length / perPage));
  if (page > totalPages) page = totalPages;
  window._artistEarnPage = page;
  var start = (page - 1) * perPage;
  var pageData = data.slice(start, start + perPage);

  var arrow = function(col) {
    if (sort.col !== col) return ' <span style="opacity:0.3;font-size:0.65rem;">⇅</span>';
    return sort.dir === 1 ? ' <span style="font-size:0.65rem;">▲</span>' : ' <span style="font-size:0.65rem;">▼</span>';
  };

  var hdrStyle = 'cursor:pointer;user-select:none;padding:8px 10px;font-size:0.75rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid rgba(255,255,255,0.08);white-space:nowrap;';
  var html = '<table style="width:100%;border-collapse:collapse;table-layout:fixed;">';
  html += '<colgroup><col style="width:14%"><col style="width:10%"><col style="width:22%"><col style="width:14%"><col style="width:14%"><col style="width:14%"><col style="width:12%"></colgroup>';
  html += '<thead><tr>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="artistEarnSortBy(\'gig_date_sort\')">Date' + arrow('gig_date_sort') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;">Time</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="artistEarnSortBy(\'venue_name\')">Venue' + arrow('venue_name') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="artistEarnSortBy(\'gig_fee\')">Gig Pay' + arrow('gig_fee') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="artistEarnSortBy(\'platform_fee\')">Platform Fee' + arrow('platform_fee') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="artistEarnSortBy(\'total_paid\')">Total Paid' + arrow('total_paid') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="artistEarnSortBy(\'status\')">Status' + arrow('status') + '</th>';
  html += '</tr></thead><tbody>';

  pageData.forEach(function(t) {
    var link = t.venue_id
      ? '<a href="/app/venue-profile.html?venue_id=' + t.venue_id + '" style="color:var(--text-white);text-decoration:none;border-bottom:1px dashed rgba(255,255,255,0.3);" onmouseover="this.style.color=\'#a78bfa\'" onmouseout="this.style.color=\'var(--text-white)\'">' + t.venue_name + '</a>'
      : t.venue_name;
    var isCancelled = t.rawStatus === 'payment_cancelled';
    // Gig fee: strikethrough when cancelled (venue didn't pay artist)
    var gigFeeCell = isCancelled
      ? '<span style="color:#9ca3af;text-decoration:line-through;">$' + t.gig_fee.toFixed(2) + '</span>'
      : '$' + t.gig_fee.toFixed(2);
    // Total paid: just $0.00 for cancelled — artist gets nothing, no parenthetical needed
    var totalPaidCell = isCancelled
      ? '<span style="color:#ef4444;font-weight:700;">$0.00</span>'
      : '<span style="color:#10b981;font-weight:700;">$' + t.total_paid.toFixed(2) + '</span>';
    html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-gray);">' + t.gig_date + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-gray);">' + (t.gig_time || '') + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-white);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + link + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-white);">' + gigFeeCell + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-muted);">$' + t.platform_fee.toFixed(2) + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;">' + totalPaidCell + '</td>';
    if (isCancelled) {
      var baseMsg = "Venue has cancelled the payment for this gig. If you disagree, reach out directly to the venue to settle dispute.";
      var reasonTip = ' title="' + baseMsg + (t.cancel_reason ? ' Reason for cancellation: ' + t.cancel_reason.replace(/"/g, '&quot;') : '') + '"';
      html += '<td style="padding:10px;font-size:0.75rem;font-weight:600;color:#f97316;cursor:help;"' + reasonTip + '>Cancelled ⓘ</td>';
    } else {
      var tip = (window._artistEarnStatusTip && window._artistEarnStatusTip[t.rawStatus]) || '';
      var tipAttr = tip ? ' title="' + tip + '" style="padding:10px;font-size:0.75rem;font-weight:600;color:' + t.statusColor + ';cursor:help;"' : ' style="padding:10px;font-size:0.75rem;font-weight:600;color:' + t.statusColor + ';"';
      var statusIcon = t.rawStatus === 'transferred' ? ' ⓘ' : (t.rawStatus === 'paid' ? ' ✓' : '');
      html += '<td' + tipAttr + '>' + t.status + statusIcon + '</td>';
    }
    html += '</tr>';
  });
  html += '</tbody></table>';

  // Pagination
  if (totalPages > 1) {
    var btnStyle = 'background:rgba(255,255,255,0.05);border:1px solid var(--glass-border,rgba(255,255,255,0.1));color:var(--text);padding:4px 10px;border-radius:4px;font-size:0.75rem;cursor:pointer;';
    var disStyle = btnStyle + 'opacity:0.3;cursor:default;';
    html += '<div style="display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:10px;font-size:0.75rem;color:var(--text-muted);">';
    html += '<span>Page ' + page + ' of ' + totalPages + '</span>';
    html += '<button onclick="artistEarnGoPage(' + (page - 1) + ')" style="' + (page <= 1 ? disStyle : btnStyle) + '" ' + (page <= 1 ? 'disabled' : '') + '>◀ Prev</button>';
    html += '<button onclick="artistEarnGoPage(' + (page + 1) + ')" style="' + (page >= totalPages ? disStyle : btnStyle) + '" ' + (page >= totalPages ? 'disabled' : '') + '>Next ▶</button>';
    html += '</div>';
  }
  container.innerHTML = html;
}

function artistEarnSortBy(col) {
  var s = window._artistEarnSort;
  if (s.col === col) { s.dir *= -1; } else { s.col = col; s.dir = col === 'gig_date_sort' ? -1 : 1; }
  window._artistEarnPage = 1;
  renderArtistEarningsTable();
}
function artistEarnGoPage(p) { window._artistEarnPage = p; renderArtistEarningsTable(); }

function exportArtistEarnings(format) {
  var data = window._artistEarnData;
  if (!data || data.length === 0) return;
  if (format === 'excel') {
    var csv = 'Date,Time,Venue,Gig Pay,Platform Fee,Total Paid,Status\n';
    data.forEach(function(t) {
      var totalPaid = t.rawStatus === 'payment_cancelled' ? 0 : t.total_paid;
      csv += t.gig_date + ',' + (t.gig_time || '') + ',"' + t.venue_name.replace(/"/g,'""') + '",' + t.gig_fee.toFixed(2) + ',' + t.platform_fee.toFixed(2) + ',' + totalPaid.toFixed(2) + ',' + t.status + '\n';
    });
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'earnings_history.csv'; a.click();
  } else if (format === 'pdf') {
    var w = window.open('', '_blank');
    w.document.write('<html><head><title>Earnings History</title><style>@page{size:landscape;margin:10mm 12mm;}body{font-family:Arial,sans-serif;padding:10px 15px;}table{width:100%;border-collapse:collapse;margin-top:12px;}th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:12px;white-space:nowrap;}th{background:#f4f4f4;font-weight:bold;}tr:nth-child(even){background:#fafafa;}.right{text-align:right;}.cancelled{color:#ef4444;}</style></head><body>');
    w.document.write('<h2 style="margin-bottom:4px;">Earnings History</h2><p style="margin-top:0;font-size:12px;color:#666;">Exported: ' + new Date().toLocaleDateString() + '</p>');
    w.document.write('<table><tr><th>Date</th><th>Time</th><th>Venue</th><th class="right">Gig Pay</th><th class="right">Platform Fee</th><th class="right">Total Paid</th><th>Status</th></tr>');
    data.forEach(function(t) {
      var isCancelled = t.rawStatus === 'payment_cancelled';
      var totalPaidStr = isCancelled ? '<span class="cancelled">$0.00</span>' : '$' + t.total_paid.toFixed(2);
      var statusStr = isCancelled ? '<span class="cancelled">Cancelled</span>' : t.status;
      w.document.write('<tr><td>' + t.gig_date + '</td><td>' + (t.gig_time || '') + '</td><td>' + t.venue_name + '</td><td class="right">$' + t.gig_fee.toFixed(2) + '</td><td class="right">$' + t.platform_fee.toFixed(2) + '</td><td class="right">' + totalPaidStr + '</td><td>' + statusStr + '</td></tr>');
    });
    w.document.write('</table></body></html>');
    w.document.close();
    w.print();
  }
  var dd = document.getElementById('artistEarnExportDD');
  if (dd) dd.style.display = 'none';
}

async function loadArtistW9Status() {
  var artistId = getArtistId();
  if (!artistId) return;
  try {
    var res = await fetch('/api/artists/' + artistId + '/w9', { credentials: 'include' });
    if (!res.ok) { window._artistW9Ready = false; return; }
    var data = await res.json();
    // W9 exists if no 'status: not_filed' and not needing recertification
    window._artistW9Ready = !(data.status === 'not_filed') && !data.needs_recertification;
  } catch (e) { window._artistW9Ready = false; }
}

async function loadArtistPaymentSettings() {
  if (window._artistAccessDenied) return;
  // Fetch artist fee percentage
  try {
    var cfgRes = await fetch('/api/stripe/config', { credentials: 'include' });
    if (cfgRes.ok) {
      var cfg = await cfgRes.json();
      var feeEl = document.getElementById('artistFeePercent');
      if (feeEl && cfg.artist_fee_percent != null) {
        feeEl.textContent = Number(cfg.artist_fee_percent) % 1 === 0 ? cfg.artist_fee_percent : Number(cfg.artist_fee_percent).toFixed(1);
      }
    }
  } catch(e) {}
  await loadArtistConnectStatus();
  await loadArtistW9Status();
  loadArtistEarningsHistory();
  loadArtistPayoutSummary();
  if (typeof window.loadArtistWaitlists === 'function') window.loadArtistWaitlists();
}

// Handle return from Stripe onboarding
function checkStripeReturn() {
  var params = new URLSearchParams(window.location.search);
  if (params.get('stripe_return') === '1' || params.get('stripe_refresh') === '1') {
    var url = new URL(window.location);
    url.searchParams.delete('stripe_return');
    url.searchParams.delete('stripe_refresh');
    url.searchParams.set('tab', 'payments');
    window.history.replaceState({}, '', url);
    if (typeof switchTab === 'function') {
      setTimeout(function() { switchTab('payments'); }, 100);
    }
  }
}

window.artistStartConnect = artistStartConnect;
window.artistOpenDashboard = artistOpenDashboard;
window.loadArtistPaymentSettings = loadArtistPaymentSettings;
window.loadArtistEarningsHistory = loadArtistEarningsHistory;

window.setArtistEarnShow = function(mode) {
  window._artistEarnFilter = mode;
  window._artistEarnPage = 1;
  // Flip sort direction: pending = ascending (soonest first), completed = descending (most recent first)
  window._artistEarnSort = { col: 'gig_date_sort', dir: (mode === 'completed' || mode === 'all') ? -1 : 1 };
  renderArtistEarningsTable();
};

// Set dropdown to "pending" on page load
document.addEventListener('DOMContentLoaded', function() {
  var sel = document.getElementById('artistEarnShowFilter');
  if (sel) sel.value = 'all';
});
window.checkStripeReturn = checkStripeReturn;
window.artistEarnSortBy = artistEarnSortBy;
window.artistEarnGoPage = artistEarnGoPage;
window.exportArtistEarnings = exportArtistEarnings;

document.addEventListener('DOMContentLoaded', checkStripeReturn);

// Close export dropdown on outside click
document.addEventListener('click', function(e) {
  var dd = document.getElementById('artistEarnExportDD');
  if (dd && dd.style.display === 'block' && !e.target.closest('[onclick*="artistEarnExportDD"]') && !dd.contains(e.target)) {
    dd.style.display = 'none';
  }
});

async function loadArtistPayoutSummary() {
  var artistId = getArtistId();
  if (!artistId) return;
  try {
    var res = await fetch('/api/stripe/artist/' + artistId + '/earnings-summary', { credentials: 'include' });
    if (!res.ok) return;
    var d = await res.json();

    var fmt = function(n) { return '$' + (n || 0).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 }); };

    // KPI bubbles
    var el = document.getElementById('payKpiThisMonth');
    if (el) el.textContent = fmt(d.earned_this_month);
    el = document.getElementById('payKpiThisYear');
    if (el) el.textContent = fmt(d.earned_this_year);
    el = document.getElementById('payKpiAllTime');
    if (el) el.textContent = fmt(d.total_earned);
    el = document.getElementById('payKpiPending');
    if (el) el.textContent = fmt(d.pending_payout);
    el = document.getElementById('payKpiGigs');
    if (el) el.textContent = (d.gigs_completed || 0);

    // Monthly bar chart
    var byMonthEl = document.getElementById('payByMonth');
    if (byMonthEl) {
      if (d.by_month && d.by_month.length > 0) {
        var maxEarned = Math.max.apply(null, d.by_month.map(function(m) { return m.earned; }));
        byMonthEl.innerHTML = d.by_month.map(function(m) {
          var pct = maxEarned ? Math.round((m.earned / maxEarned) * 100) : 0;
          return '<div style="margin-bottom:8px;">' +
            '<div style="display:flex;justify-content:space-between;font-size:0.72rem;color:var(--text-gray);margin-bottom:3px;">' +
              '<span>' + (m.month || '') + '</span>' +
              '<span style="color:var(--text);font-weight:600;">' + fmt(m.earned) + ' &middot; ' + m.gigs + ' gig' + (m.gigs !== 1 ? 's' : '') + '</span>' +
            '</div>' +
            '<div style="background:var(--border);border-radius:4px;height:8px;">' +
              '<div style="background:linear-gradient(90deg,#8b5cf6,#10b981);border-radius:4px;height:8px;width:' + pct + '%;transition:width 0.5s;"></div>' +
            '</div>' +
          '</div>';
        }).join('');
      } else {
        byMonthEl.innerHTML = '<p style="color:var(--text-gray);font-size:0.8rem;">No completed payouts yet</p>';
      }
    }

    // Per-venue breakdown
    var byVenueEl = document.getElementById('payByVenue');
    if (byVenueEl) {
      if (d.per_venue && d.per_venue.length > 0) {
        byVenueEl.innerHTML = d.per_venue.map(function(v, i) {
          var link = v.venue_id
            ? '<a href="/app/venue-profile.html?venue_id=' + v.venue_id + '" target="_blank" style="color:var(--cyan);text-decoration:none;">' + esc(v.venue_name) + '</a>'
            : esc(v.venue_name);
          return '<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--border);font-size:0.82rem;">' +
            '<div style="display:flex;align-items:center;gap:8px;">' +
              '<span style="color:var(--text-gray);font-size:0.7rem;width:16px;">' + (i + 1) + '</span>' +
              link +
            '</div>' +
            '<div style="text-align:right;">' +
              '<span style="font-weight:700;color:#10b981;">' + fmt(v.total_earned) + '</span>' +
              '<span style="color:var(--text-gray);font-size:0.7rem;margin-left:6px;">' + v.gig_count + ' gig' + (v.gig_count !== 1 ? 's' : '') + '</span>' +
            '</div>' +
          '</div>';
        }).join('');
      } else {
        byVenueEl.innerHTML = '<p style="color:var(--text-gray);font-size:0.8rem;">No completed payouts yet</p>';
      }
    }

  } catch(e) { console.error('Payout summary error:', e); }
}

window.loadArtistPayoutSummary = loadArtistPayoutSummary;
