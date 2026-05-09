/**
 * Venue Stripe Payment Management
 * Handles: Save card via Stripe Elements, view/update/remove card, billing history
 */

let venueStripe = null;
let venueCardElement = null;

// Styled modal instead of alert()
function showPaymentModal(title, message, type) {
  type = type || 'info';
  const colors = {
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
}

// Styled confirm modal
function showPaymentConfirm(title, message, onConfirm) {
  var existing = document.getElementById('paymentModal');
  if (existing) existing.remove();
  var modal = document.createElement('div');
  modal.id = 'paymentModal';
  modal.innerHTML =
    '<div style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;">' +
      '<div style="background:#1a1f2e;border:1px solid rgba(239,68,68,0.4);border-radius:12px;padding:28px;max-width:420px;width:90%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.5);">' +
        '<h3 style="color:#ef4444;font-size:1rem;font-weight:700;margin:0 0 12px 0;">' + title + '</h3>' +
        '<p style="color:#9ca3af;font-size:0.85rem;line-height:1.6;margin:0 0 20px 0;">' + message + '</p>' +
        '<div style="display:flex;gap:10px;justify-content:center;">' +
          '<button onclick="this.closest(\'#paymentModal\').remove()" style="padding:10px 24px;background:transparent;color:#9ca3af;border:1px solid #333;border-radius:6px;font-size:0.85rem;cursor:pointer;">Cancel</button>' +
          '<button id="paymentConfirmBtn" style="padding:10px 24px;background:#ef4444;color:white;border:none;border-radius:6px;font-size:0.85rem;font-weight:600;cursor:pointer;">Confirm</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  document.body.appendChild(modal);
  document.getElementById('paymentConfirmBtn').onclick = function() {
    modal.remove();
    onConfirm();
  };
}

// Initialize Stripe Elements
async function initVenueStripeCard() {
  var cardSection = document.getElementById('venueAddCardSection');
  try {
    var configRes = await fetch('/api/stripe/config', { credentials: 'include' });
    if (!configRes.ok) {
      cardSection.innerHTML = '<div style="text-align:center;padding:24px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:8px;"><p style="color:#ef4444;font-size:0.85rem;margin:0;">Unable to connect to payment system. Please try again later.</p></div>';
      return;
    }
    var config = await configRes.json();
    if (!config.publishable_key) {
      cardSection.innerHTML = '<div style="text-align:center;padding:24px;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:8px;"><p style="color:#f59e0b;font-size:0.85rem;margin:0;">Stripe is not configured yet. Contact platform admin to add API keys in Admin → Payments.</p></div>';
      return;
    }
    // Wait for Stripe.js if still loading (async script)
    if (typeof Stripe === 'undefined') {
      for (let i = 0; i < 50; i++) {
        await new Promise(r => setTimeout(r, 100));
        if (typeof Stripe !== 'undefined') break;
      }
      if (typeof Stripe === 'undefined') {
        cardSection.innerHTML = '<div style="text-align:center;padding:24px;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:8px;"><p style="color:#f59e0b;font-size:0.85rem;margin:0;">Stripe is still loading. Please try again in a moment.</p></div>';
        return;
      }
    }
    venueStripe = Stripe(config.publishable_key);
    var elements = venueStripe.elements();
    venueCardElement = elements.create('card', {
      style: {
        base: { color: '#ffffff', fontFamily: '"Inter", -apple-system, sans-serif', fontSize: '16px', '::placeholder': { color: '#6b7280' } },
        invalid: { color: '#ef4444' }
      }
    });
    var cardMount = document.getElementById('venueCardElement');
    if (cardMount) venueCardElement.mount('#venueCardElement');
  } catch (e) {
    console.error('Stripe init error:', e);
    cardSection.innerHTML = '<div style="text-align:center;padding:24px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:8px;"><p style="color:#ef4444;font-size:0.85rem;margin:0;">Failed to load Stripe. Check your internet connection and refresh.</p></div>';
  }
}

// Load saved card
async function loadVenueCard() {
  var params = new URLSearchParams(window.location.search);
  var venueId = params.get("venue_id");
  if (!venueId) return;
  
  // Always fetch fee percentage (independent of card status)
  try {
    var cfgRes = await fetch('/api/stripe/config', { credentials: 'include' });
    if (cfgRes.ok) {
      var cfg = await cfgRes.json();
      var feeEl = document.getElementById('venueFeePercent');
      if (feeEl && cfg.platform_fee_percent != null) {
        feeEl.textContent = Number(cfg.platform_fee_percent) % 1 === 0 ? cfg.platform_fee_percent : Number(cfg.platform_fee_percent).toFixed(1);
      }
    }
  } catch(e) {}
  
  try {
    var res = await fetch('/api/stripe/venue/' + venueId + '/payment-method', { credentials: 'include' });
    if (!res.ok) { initVenueStripeCard(); return; }
    var data = await res.json();
    if (data.has_card) {
      document.getElementById('venueCurrentCard').style.display = 'block';
      document.getElementById('venueAddCardSection').style.display = 'none';
      document.getElementById('venueCardBrand').textContent = (data.brand || 'Card').toUpperCase();
      document.getElementById('venueCardLast4').textContent = data.last4;
      document.getElementById('venueCardExp').textContent = data.exp_month + '/' + data.exp_year;
    } else {
      document.getElementById('venueCurrentCard').style.display = 'none';
      document.getElementById('venueAddCardSection').style.display = 'block';
      initVenueStripeCard();
    }
  } catch (e) { console.error('Load card error:', e); initVenueStripeCard(); }
}

// Save card via SetupIntent
async function venueSaveCard() {
  var params = new URLSearchParams(window.location.search);
  var venueId = params.get("venue_id");
  if (!venueId || !venueStripe || !venueCardElement) {
    showPaymentModal('Setup Required', 'Stripe is still loading. Please wait a moment and try again.', 'warning');
    return;
  }
  var btn = document.getElementById('venueAddCardBtn');
  var errorEl = document.getElementById('venueCardError');
  var successEl = document.getElementById('venueCardSuccess');
  btn.disabled = true; btn.textContent = 'Saving...';
  errorEl.style.display = 'none'; successEl.style.display = 'none';
  try {
    var setupRes = await fetch('/api/stripe/venue/' + venueId + '/setup-intent', { method: 'POST', credentials: 'include' });
    if (!setupRes.ok) {
      var err = await setupRes.json().catch(function() { return {}; });
      throw new Error(err.detail || 'Failed to initialize card setup. Check that Stripe is configured in Admin → Payments.');
    }
    var setup = await setupRes.json();
    var result = await venueStripe.confirmCardSetup(setup.client_secret, { payment_method: { card: venueCardElement } });
    if (result.error) { errorEl.textContent = result.error.message; errorEl.style.display = 'block'; return; }
    var saveRes = await fetch('/api/stripe/venue/' + venueId + '/save-payment-method', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ payment_method_id: result.setupIntent.payment_method })
    });
    if (!saveRes.ok) throw new Error('Failed to save card on file');
    showPaymentModal('Card Saved', 'Your card has been securely saved. It will be charged the day after each gig is performed.', 'success');
    setTimeout(function() { loadVenueCard(); }, 500);
  } catch (e) {
    showPaymentModal('Card Setup Failed', e.message || 'Failed to save card. Please try again.', 'error');
  } finally { btn.disabled = false; btn.textContent = '💳 Save Card'; }
}

function venueUpdateCard() {
  document.getElementById('venueCurrentCard').style.display = 'none';
  document.getElementById('venueAddCardSection').style.display = 'block';
  document.getElementById('venueCardSuccess').style.display = 'none';
  document.getElementById('venueCardError').style.display = 'none';
  document.getElementById('venueCardElement').innerHTML = '';
  initVenueStripeCard();
}

function venueRemoveCard() {
  showPaymentConfirm('Remove Card?', "You won't be able to book gigs until you add a new card.", function() {
    var params = new URLSearchParams(window.location.search);
    var venueId = params.get("venue_id");
    if (!venueId) return;
    fetch('/api/stripe/venue/' + venueId + '/payment-method', { method: 'DELETE', credentials: 'include' }).then(function() { loadVenueCard(); });
  });
}

function checkVenuePaymentMethod() {
  var cardDisplay = document.getElementById('venueCurrentCard');
  if (cardDisplay && cardDisplay.style.display !== 'none') {
    return true;
  }
  showPaymentModal('Payment Method Required', 
    'Please add a payment card in the <strong>Payments</strong> tab before creating gigs. Your card will be charged the day after each gig is performed.', 
    'warning');
  return false;
}
window.checkVenuePaymentMethod = checkVenuePaymentMethod;

async function loadVenueBillingHistory() {
  var params = new URLSearchParams(window.location.search);
  var venueId = params.get("venue_id");
  if (!venueId) return;
  try {
    var res = await fetch('/api/stripe/venue/' + venueId + '/transactions', { credentials: 'include' });
    if (!res.ok) return;
    var txns = await res.json();
    var container = document.getElementById('venueBillingHistory');
    if (!txns || txns.length === 0) { container.innerHTML = '<p style="color:var(--text-muted);font-size:0.875rem;">No billing history yet</p>'; return; }

    // Store data for sorting/pagination/export
    var nowMs = Date.now();
    // Note: pre-gig cancellations DELETE the transaction entirely (in
    // cleanup_gig_records) so they never reach this list. Rows here with
    // status='payment_cancelled' are post-gig payment cancellations.
    window._venueBillingData = txns.map(function(t) {
      // FIX (May 2026): backend computes effective_status for venue_charge parent rows.
      // Parent status='charged' even after children are paid out — effective_status
      // promotes to 'paid' when all children have been paid.
      var rawStatus = t.effective_status || t.status;
      // Venue-perspective status:
      //   - Upcoming  : booking confirmed, gig hasn't started yet
      //   - Processing: gig started, artist payout pending (venue's been charged)
      //   - Paid ✓    : artist payout completed
      var statusMap = { paid:'Paid ✓', charged:'Processing', test:'Test', scheduled:'Scheduled', pending:'Upcoming', charge_retry:'Retrying', payment_failed:'Failed', pending_transfer:'Processing', transfer_failed:'Processing', payment_cancelled:'Cancelled' };
      var colorMap = { paid:'#10b981', charged:'#f59e0b', test:'#60a5fa', scheduled:'#8b5cf6', pending:'#8b5cf6', charge_retry:'#f97316', payment_failed:'#ef4444', pending_transfer:'#f59e0b', transfer_failed:'#f59e0b', payment_cancelled:'#f97316' };
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
      var isCx = t.status === 'payment_cancelled';
      var gigFeeCents = t.amount_cents || 0;
      // For cancelled: platform fee = commission_cents (set at booking time).
      // If commission_cents is 0 (legacy row), fall back to venue_charge_cents - amount_cents,
      // or estimate from amount at 50% default fee split.
      var _rawCommission = t.commission_cents || 0;
      var _rawVcFee = (t.venue_charge_cents || 0) - (t.amount_cents || 0);
      var platformFeeCents = isCx
        ? (_rawCommission || _rawVcFee || Math.round((gigFeeCents || 0) * 0.50))
        : _rawVcFee;
      // For cancelled: total = platform fee owed. Venue doesn't pay gig fee to artist.
      var totalPaidCents = isCx
        ? platformFeeCents
        : (t.venue_charge_cents || 0);
      // FIX (May 2026): override status label for non-terminal txns whose gig
      // hasn't started yet. Without this, an "8pm tonight" gig that was charged
      // earlier in the day shows "Paid ✓" — misleading because the gig hasn't
      // even happened yet. Show "Upcoming" until the gig start time has passed.
      // Lifecycle:
      //   Future (dtSort > nowMs): "Upcoming" purple, regardless of status
      //   Past + non-terminal status: "Processing" orange (gig started, awaiting payout)
      //   Terminal: use status map (Paid ✓ green, Cancelled orange, etc.)
      var TERMINAL = { paid:1, payment_cancelled:1, payment_failed:1, transfer_failed:1 };
      var dtSort = t.gig_date ? new Date(t.gig_date + 'T' + (t.gig_time || t.start_time || '00:00') + ':00').getTime() : 0;
      var displayStatus = statusMap[rawStatus] || 'Pending';
      var displayColor  = colorMap[rawStatus] || '#f59e0b';
      if (!TERMINAL[rawStatus]) {
        if (dtSort > nowMs) {
          displayStatus = 'Upcoming';
          displayColor  = '#8b5cf6';  // purple
        } else {
          // Gig has started/finished but no terminal status yet → Processing
          displayStatus = 'Processing';
          displayColor  = '#f59e0b';  // orange
        }
      }

      return {
        artist_name: t.artist_name || 'Artist',
        artist_id: t.resolved_artist_id || t.artist_id || '',
        gig_date: t.gig_date || '',
        gig_date_sort: dtSort,
        gig_time: displayTime,
        gig_fee: gigFeeCents / 100,
        platform_fee: platformFeeCents / 100,
        total_paid: totalPaidCents / 100,
        amount: totalPaidCents / 100,
        status: displayStatus,
        statusColor: displayColor,
        rawStatus: rawStatus,
        txn_id: t.id,
        cancel_reason: t.cancel_reason || ''
      };
    });
    window._venueBillSort = { col: 'gig_date_sort', dir: -1 };  // all = descending (most recent first)
    window._venueBillFilter = 'all';
    window._venueBillPage = 1;
    // Set dropdown to "pending"
    var sel = document.getElementById('venueBillShowFilter');
    if (sel) sel.value = 'all';
    renderVenueBillingTable();
  } catch (e) { console.error('Load history error:', e); }
}

function renderVenueBillingTable() {
  var container = document.getElementById('venueBillingHistory');
  var data = (window._venueBillingData || []).slice();
  var sort = window._venueBillSort || { col: 'gig_date_sort', dir: 1 };
  var page = window._venueBillPage || 1;
  var perPage = 10;

  // Apply pending/completed/all filter
  var now = Date.now();
  var filterMode = window._venueBillFilter || 'all';
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
  window._venueBillPage = page;
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
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="venueBillSortBy(\'gig_date_sort\')">Date' + arrow('gig_date_sort') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;">Time</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="venueBillSortBy(\'artist_name\')">Artist' + arrow('artist_name') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="venueBillSortBy(\'gig_fee\')">Gig Fee' + arrow('gig_fee') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="venueBillSortBy(\'platform_fee\')">Platform Fee' + arrow('platform_fee') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="venueBillSortBy(\'total_paid\')">Total Paid' + arrow('total_paid') + '</th>';
  html += '<th style="' + hdrStyle + 'text-align:left;" onclick="venueBillSortBy(\'status\')">Status' + arrow('status') + '</th>';
  html += '</tr></thead><tbody>';

  pageData.forEach(function(t) {
    // Multi-slot gigs render the artist column as a comma-separated list of names.
    // We can't link each name individually (only one artist_id is returned), so
    // multi-artist cells render as plain text. Single-artist cells stay clickable.
    var isMulti = (t.artist_name || '').indexOf(',') !== -1;
    var link = (t.artist_id && !isMulti)
      ? '<a href="/app/artist-profile.html?artist_id=' + t.artist_id + '" style="color:var(--text-white);text-decoration:none;border-bottom:1px dashed rgba(255,255,255,0.3);" onmouseover="this.style.color=\'#a78bfa\'" onmouseout="this.style.color=\'var(--text-white)\'">' + t.artist_name + '</a>'
      : t.artist_name;
    var isCancelled = t.rawStatus === 'payment_cancelled';
    var statusCell = '<span style="color:' + t.statusColor + ';">' + t.status + '</span>';
    if (isCancelled) {
      statusCell = '<span style="color:#f97316;">Cancelled</span> '
        + '<a href="javascript:void(0)" onclick="showReinstatePaymentModal(' + t.txn_id + ')" '
        + 'style="color:#a78bfa;font-size:0.7rem;text-decoration:none;border-bottom:1px dashed rgba(167,139,250,0.5);cursor:pointer;white-space:nowrap;">Pay Artist?</a>';
    }
    // Gig Fee and Total Paid: for cancelled, strike through gig fee, show platform fee still owed
    var gigFeeCell = isCancelled
      ? '<span style="color:#9ca3af;text-decoration:line-through;font-size:0.8rem;">$' + t.gig_fee.toFixed(2) + '</span>'
      : '$' + t.gig_fee.toFixed(2);
    var totalPaidCell = isCancelled
      ? '<span style="font-weight:700;color:#f97316;">$' + t.total_paid.toFixed(2) + '</span><div style="font-size:0.7rem;color:var(--text-muted);">platform fee</div>'
      : '<span style="font-weight:700;">$' + t.total_paid.toFixed(2) + '</span>';
    html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-gray);">' + t.gig_date + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-gray);">' + (t.gig_time || '') + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-white);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + link + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-white);">' + gigFeeCell + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-muted);">$' + t.platform_fee.toFixed(2) + '</td>';
    html += '<td style="padding:10px;font-size:0.85rem;color:var(--text-white);">' + totalPaidCell + '</td>';
    html += '<td style="padding:10px;font-size:0.75rem;font-weight:600;">' + statusCell + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table>';

  // Pagination
  if (totalPages > 1) {
    var btnStyle = 'background:rgba(255,255,255,0.05);border:1px solid var(--glass-border,rgba(255,255,255,0.1));color:var(--text);padding:4px 10px;border-radius:4px;font-size:0.75rem;cursor:pointer;';
    var disStyle = btnStyle + 'opacity:0.3;cursor:default;';
    html += '<div style="display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:10px;font-size:0.75rem;color:var(--text-muted);">';
    html += '<span>Page ' + page + ' of ' + totalPages + '</span>';
    html += '<button onclick="venueBillGoPage(' + (page - 1) + ')" style="' + (page <= 1 ? disStyle : btnStyle) + '" ' + (page <= 1 ? 'disabled' : '') + '>◀ Prev</button>';
    html += '<button onclick="venueBillGoPage(' + (page + 1) + ')" style="' + (page >= totalPages ? disStyle : btnStyle) + '" ' + (page >= totalPages ? 'disabled' : '') + '>Next ▶</button>';
    html += '</div>';
  }
  container.innerHTML = html;
}

function venueBillSortBy(col) {
  var s = window._venueBillSort;
  if (s.col === col) { s.dir *= -1; } else { s.col = col; s.dir = col === 'gig_date_sort' ? -1 : 1; }
  window._venueBillPage = 1;
  renderVenueBillingTable();
}
function venueBillGoPage(p) { window._venueBillPage = p; renderVenueBillingTable(); }

window.setVenueBillShow = function(mode) {
  window._venueBillFilter = mode;
  window._venueBillPage = 1;
  // Pending = ascending (soonest first), completed = descending (most recent first)
  window._venueBillSort = { col: 'gig_date_sort', dir: (mode === 'completed' || mode === 'all') ? -1 : 1 };
  renderVenueBillingTable();
};

function exportVenueBilling(format) {
  var data = window._venueBillingData;
  if (!data || data.length === 0) return;
  if (format === 'excel') {
    var csv = 'Date,Time,Artist,Gig Fee,Platform Fee,Total Paid,Status\n';
    data.forEach(function(t) {
      var isCx = t.rawStatus === 'payment_cancelled';
      csv += t.gig_date + ',' + (t.gig_time || '') + ',"' + t.artist_name.replace(/"/g,'""') + '",' + (isCx ? '0.00' : t.gig_fee.toFixed(2)) + ',' + t.platform_fee.toFixed(2) + ',' + (isCx ? t.platform_fee.toFixed(2) : t.total_paid.toFixed(2)) + ',' + t.status + '\n';
    });
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'billing_history.csv'; a.click();
  } else if (format === 'pdf') {
    var w = window.open('', '_blank');
    w.document.write('<html><head><title>Billing History</title><style>@page{size:landscape;margin:10mm 12mm;}body{font-family:Arial,sans-serif;padding:10px 15px;}table{width:100%;border-collapse:collapse;margin-top:12px;}th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:12px;white-space:nowrap;}th{background:#f4f4f4;font-weight:bold;}tr:nth-child(even){background:#fafafa;}.right{text-align:right;}.cancelled{color:#ef4444;}</style></head><body>');
    w.document.write('<h2 style="margin-bottom:4px;">Billing History</h2><p style="margin-top:0;font-size:12px;color:#666;">Exported: ' + new Date().toLocaleDateString() + '</p>');
    w.document.write('<table><tr><th>Date</th><th>Time</th><th>Artist</th><th class="right">Gig Fee</th><th class="right">Platform Fee</th><th class="right">Total Paid</th><th>Status</th></tr>');
    data.forEach(function(t) {
      var isCx = t.rawStatus === 'payment_cancelled';
      var gigFeeStr = isCx ? '<span class="cancelled">$0.00 ($' + t.gig_fee.toFixed(2) + ')</span>' : '$' + t.gig_fee.toFixed(2);
      var totalPaidStr = isCx ? '$' + t.platform_fee.toFixed(2) : '$' + t.total_paid.toFixed(2);
      var statusStr = isCx ? '<span class="cancelled">Cancelled</span>' : t.status;
      w.document.write('<tr><td>' + t.gig_date + '</td><td>' + (t.gig_time || '') + '</td><td>' + t.artist_name + '</td><td class="right">' + gigFeeStr + '</td><td class="right">$' + t.platform_fee.toFixed(2) + '</td><td class="right">' + totalPaidStr + '</td><td>' + statusStr + '</td></tr>');
    });
    w.document.write('</table></body></html>');
    w.document.close();
    w.print();
  }
  // Close dropdown
  var dd = document.getElementById('venueBillExportDD');
  if (dd) dd.style.display = 'none';
}

async function loadVenuePaymentSettings() {
  await loadVenueCard();
  loadVenueBillingHistory();
}

window.venueSaveCard = venueSaveCard;
window.venueUpdateCard = venueUpdateCard;
window.venueRemoveCard = venueRemoveCard;
window.showPaymentModal = showPaymentModal;
window.loadVenuePaymentSettings = loadVenuePaymentSettings;
window.venueBillSortBy = venueBillSortBy;
window.venueBillGoPage = venueBillGoPage;
window.setVenueBillShow = window.setVenueBillShow; // already defined above
window.exportVenueBilling = exportVenueBilling;

// Close export dropdown on outside click
document.addEventListener('click', function(e) {
  var dd = document.getElementById('venueBillExportDD');
  if (dd && dd.style.display === 'block' && !e.target.closest('[onclick*="venueBillExportDD"]') && !dd.contains(e.target)) {
    dd.style.display = 'none';
  }
});

// ============================================
// REINSTATE PAYMENT
// ============================================
function showReinstatePaymentModal(txnId) {
  var overlay = document.createElement('div');
  overlay.id = 'reinstatePayOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,0.75);display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = '<div style="background:var(--card-bg,#1a1a2e);border:1px solid rgba(139,92,246,0.3);border-radius:12px;max-width:460px;width:92%;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,0.5);">'
    + '<h3 style="margin:0 0 12px 0;color:#a78bfa;font-size:1.05rem;">💰 Reinstate Payment?</h3>'
    + '<p style="color:var(--text-muted,#999);font-size:0.85rem;line-height:1.5;margin:0 0 8px 0;">You cancelled this payment earlier. Are you sure you want to pay the artist their gig fee now?</p>'
    + '<p style="color:var(--text-muted,#777);font-size:0.75rem;margin:0 0 18px 0;line-height:1.4;"><em>Note: The GigsFill platform fee was already collected when you cancelled, so it will not be charged again.</em></p>'
    + '<div style="display:flex;gap:10px;justify-content:flex-end;">'
    + '<button id="reinstateClose" class="btn ghost" style="padding:8px 18px;font-size:0.85rem;">Cancel</button>'
    + '<button id="reinstateConfirm" class="btn primary" style="padding:8px 18px;font-size:0.85rem;">Yes, Pay Artist</button>'
    + '</div>'
    + '<div id="reinstateStatus" style="margin-top:10px;text-align:center;font-size:0.85rem;"></div>'
    + '</div>';
  document.body.appendChild(overlay);
  
  overlay.querySelector('#reinstateClose').onclick = function() { overlay.remove(); };
  overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
  
  overlay.querySelector('#reinstateConfirm').onclick = async function() {
    var btn = overlay.querySelector('#reinstateConfirm');
    var status = overlay.querySelector('#reinstateStatus');
    btn.disabled = true;
    btn.textContent = 'Processing...';
    
    try {
      var res = await fetch('/api/stripe/reinstate-gig-payment', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transaction_id: txnId })
      });
      
      if (!res.ok) {
        var err = await res.json().catch(function() { return {}; });
        throw new Error(err.detail || 'Reinstate failed');
      }
      
      var result = await res.json();
      status.style.color = '#22c55e';
      status.textContent = '✓ Payment processed! Artist payout of $' + (result.artist_payout / 100).toFixed(2) + ' sent.';
      btn.style.display = 'none';
      overlay.querySelector('#reinstateClose').textContent = 'Close';
      
      // Refresh billing table
      setTimeout(function() {
        loadVenueBillingHistory();
        if (window.activityCenterVenue) window.activityCenterVenue.loadNotifications();
      }, 1500);
      
    } catch(e) {
      btn.disabled = false;
      btn.textContent = 'Yes, Pay Artist';
      status.style.color = '#ef4444';
      status.textContent = '✗ ' + e.message;
    }
  };
}
window.showReinstatePaymentModal = showReinstatePaymentModal;