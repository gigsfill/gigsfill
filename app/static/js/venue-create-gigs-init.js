// Auto-extracted from venue-create-gigs.html inline scripts
// Generated for CSP compliance (Phase 5)

// === Block 1 of 4 ===
(function () {
  const params = new URLSearchParams(window.location.search);
  let venueId = params.get("venue_id");

  if (!venueId) {
    console.error("❌ venue_id missing on venue-create-gigs.html");
    return;
  }

  // 🔒 Expose globally for other scripts
  window.venueId = venueId;

  // 🔧 Heal URL if needed
  const url = new URL(window.location.href);
  if (!url.searchParams.get("venue_id")) {
    url.searchParams.set("venue_id", venueId);
    window.history.replaceState({}, "", url);
  }

  // Header links
  document.getElementById("venueEditBtn").href =
    `/app/venue-edit.html?venue_id=${venueId}`;

  if (typeof window.applyVanityToLinks === "function") {
    window.applyVanityToLinks("venue", venueId, ["#venueProfileBtn"]);
  }

  // Wire iCal export link
  const venueIcalBtn = document.getElementById("venueIcalBtn");
  if (venueIcalBtn) {
    venueIcalBtn.href = `/api/venues/${venueId}/calendar.ics`;
  }
  
  // Fetch venue name + free trial status for header
  Promise.all([
    fetch(`/api/venues/${venueId}`, { credentials: 'include' }).then(r => r.ok ? r.json() : null).catch(() => null),
    fetch(`/api/payment-info?venue_id=${venueId}`, { credentials: 'include' }).then(r => r.ok ? r.json() : null).catch(() => null),
  ]).then(([venue, payInfo]) => {
    if (venue && (venue.venue_name || venue.name)) {
      const logo = document.querySelector('.logo');
      if (logo) {
        const venueName = venue.venue_name || venue.name;
        const freeTrial = payInfo && payInfo.free_trial;
        const trialBadge = freeTrial
          ? ` <span style="font-size:0.72rem;font-weight:600;color:#f59e0b;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);border-radius:4px;padding:2px 7px;vertical-align:middle;white-space:nowrap;">🎟 Free Trial</span>`
          : '';
        logo.innerHTML = `<img src="/app/static/img/gigsfill-logo.png" alt="GigsFill" style="height:44px;width:auto;flex-shrink:0;"><span style="font: 600 0.875rem 'Inter', sans-serif; color: var(--cyan); margin-left: 24px; background: none; -webkit-background-clip: unset; -webkit-text-fill-color: var(--cyan); letter-spacing: normal;">[${esc(venueName)}]</span>${trialBadge}`;
      }
    }
  });
})();

// === Block 2 of 4 ===
function goToPaymentSettings() {
  // Close all modals first
  document.getElementById('paymentRequiredModal').classList.add('hidden');
  const gigModal = document.getElementById('gigModal');
  if (gigModal) {
    gigModal.classList.add('hidden');
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

function checkVenuePaymentMethod() {
  // Check if venue has a saved Stripe card
  const cardDisplay = document.getElementById('venueCurrentCard');
  if (!cardDisplay || cardDisplay.style.display === 'none') {
    alert('Please add a payment card in the Payments tab before booking gigs.');
    return false;
  }
  return true;
}

// Make it globally available for the external JS
window.checkVenuePaymentMethod = checkVenuePaymentMethod;

// === Block 3 of 4 ===
// ================================
// RECURRING GIG "ENDS" RADIO LOGIC
// ================================
document.addEventListener('DOMContentLoaded', () => {
  const endAfter = document.getElementById('endAfter');
  const endBy = document.getElementById('endBy');

  if (!endAfter || !endBy) return;

  document.querySelectorAll('input[name="endType"]').forEach(radio => {
    radio.addEventListener('change', () => {
      const type = radio.value;

      endAfter.disabled = type !== 'after';
      endBy.disabled = type !== 'by';
    });
  });
});

// === Block 4 of 4 ===
(function () {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get("venue_id");

  if (!venueId) {
    console.error("❌ venue-create-gigs: missing venue_id");
    return;
  }

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

  console.log("✅ Initializing Venue Activity Center for venue_id:", venueId);

  const _venueAC = new ActivityCenter(
    "venueActivityCenter",
    venueId,
    "venue"
  );
  // Start real-time polling — 30s interval, pauses when tab is hidden
  _venueAC.startPolling(30000);
})();

// === Additional Block (Phase 5 pass 2) ===
function switchTab(tabName, button) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  button.classList.add('active');
  document.getElementById(tabName + '-tab').classList.add('active');
  
  // If switching to Calendar tab, always collapse Search Artists
  if (tabName === 'calendar') {
    const section = document.getElementById('searchArtistsSection');
    const icon = document.getElementById('searchToggleIcon');
    if (section && icon) {
      section.style.display = 'none';
      icon.textContent = '▶';
    }
  }
  
  // v97: If switching to My Artists tab, refresh the list and badge
  if (tabName === 'artists') {
    // Refresh the My Artists list if available
    if (window.myArtists && window.myArtists.loadArtists) {
      window.myArtists.loadArtists();
    }
    // Update badge
    setTimeout(() => {
      const badge = document.getElementById('artistsBadge');
      if (badge && window.myArtists && window.myArtists.artists) {
        const approvedCount = window.myArtists.artists.filter(a => a.preferred_status === 'approved').length;
        badge.textContent = `(${approvedCount})`;
      }
    }, 300);
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
    loadVenueAnalytics();
  }

  // If switching to Payments tab, load payment notice
  if (tabName === 'payments') {
    loadPaymentNotice();
  }

  // If switching to Taxes tab, load tax settings and 1099s
  if (tabName === 'taxes') {
    loadVenueTaxSettings();
    populateTaxYearDropdown();
    // Initialize contracts when Legal/Taxes tab opens
    if (window.venueContracts && window.venueContracts._reinit) {
      window.venueContracts._reinit();
    }
  }

  // If switching to Email Center tab, initialize it
  if (tabName === 'emailcenter') {
    const vid = window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (vid) {
      loadVenueEmailNotifications(vid);
      // Only initialize send-email sub-tab if user clicks into it
      if (!window._emailCenterTabLoaded) {
        window._emailCenterTabLoaded = true;
      }
    }
  }
}

// Email Center Sub-tab switching
// Legal/Taxes Sub-tab switching
function switchLegalSubtab(name, btn) {
  document.querySelectorAll('.legal-subtab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.legal-subtab-content').forEach(c => { c.classList.remove('active'); c.style.display = 'none'; });
  btn.classList.add('active');
  const el = document.getElementById(name + '-subtab');
  if (el) { el.classList.add('active'); el.style.display = ''; }
  if (name === 'executedContracts' && window.venueContracts) window.venueContracts.loadExecuted();
}

function switchEmailSubTab(subTab, button) {
  document.querySelectorAll('.ec-subtab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.ec-subtab-content').forEach(c => c.classList.remove('active'));
  button.classList.add('active');
  document.getElementById('ec-' + subTab).classList.add('active');
  
  // Initialize Send Email sub-tab when first opened
  if (subTab === 'sendemail') {
    const vid = window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (vid && typeof initEmailCenterForVenue === 'function') {
      initEmailCenterForVenue(vid);
    }
  }
  
  // Load invited artists when switching to that tab
  if (subTab === 'inviteartists') {
    const vid = window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (vid) loadInvitedArtists(vid);
  }
}

// Email Notification Settings
let _emailNotifSaveTimeout;

// Explicit per-key ID map — no suffix guessing, maps exactly to HTML element IDs
const _NOTIF_ID_MAP = {
  gig_confirmation: {
    toggle: 'notif_gig_confirmation',
    val:    'notif_gig_confirmation_val',
    unit:   'notif_gig_confirmation_unit',
  },
  open_gig_4w: {
    toggle:                    'notif_open_gig_4w',
    val:                       'notif_open_gig_4w_val',
    unit:                      'notif_open_gig_4w_unit',
    blast_all:                 'notif_open_gig_4w_blast_all',
    blast_radius:              'notif_open_gig_4w_blast_radius',
    waive_frequency:           'notif_open_gig_4w_waive_freq',
    cancel_notify_preferred:   'notif_open_gig_4w_cancel_pref',
    cancel_notify_all_enabled: 'notif_open_gig_4w_cancel_all',
    cancel_notify_all_radius:  'notif_open_gig_4w_cancel_all_radius',
    blink:                     'notif_open_gig_4w_blink',
    blink_color:               'notif_open_gig_4w_blink_color',
  },
  open_gig_2w: {
    toggle:                    'notif_open_gig_2w',
    val:                       'notif_open_gig_2w_val',
    unit:                      'notif_open_gig_2w_unit',
    blast_all:                 'notif_open_gig_2w_blast_all',
    blast_radius:              'notif_open_gig_2w_blast_radius',
    waive_frequency:           'notif_open_gig_2w_waive_freq',
    cancel_notify_preferred:   'notif_open_gig_2w_cancel_pref',
    cancel_notify_all_enabled: 'notif_open_gig_2w_cancel_all',
    cancel_notify_all_radius:  'notif_open_gig_2w_cancel_all_radius',
    blink:                     'notif_open_gig_2w_blink',
    blink_color:               'notif_open_gig_2w_blink_color',
  },
  open_gig_1w: {
    toggle:                    'notif_open_gig_1w',
    val:                       'notif_open_gig_1w_val',
    unit:                      'notif_open_gig_1w_unit',
    blast_all:                 'notif_open_gig_1w_blast_all',
    blast_radius:              'notif_open_gig_1w_blast_radius',
    waive_frequency:           'notif_open_gig_1w_waive_freq',
    cancel_notify_preferred:   'notif_open_gig_1w_cancel_pref',
    cancel_notify_all_enabled: 'notif_open_gig_1w_cancel_all',
    cancel_notify_all_radius:  'notif_open_gig_1w_cancel_all_radius',
    blink:                     'notif_open_gig_1w_blink',
    blink_color:               'notif_open_gig_1w_blink_color',
  },
  open_gig_36h: {
    toggle:                    'notif_open_gig_36h',
    val:                       'notif_open_gig_36h_val',
    unit:                      'notif_open_gig_36h_unit',
    blast_all:                 'notif_open_gig_36h_blast_all',
    blast_radius:              'notif_open_gig_36h_blast_radius',
    waive_frequency:           'notif_open_gig_36h_waive_freq',
    cancel_notify_preferred:   'notif_open_gig_36h_cancel_pref',
    cancel_notify_all_enabled: 'notif_open_gig_36h_cancel_all',
    cancel_notify_all_radius:  'notif_open_gig_36h_cancel_all_radius',
    blink:                     'notif_open_gig_36h_blink',
    blink_color:               'notif_open_gig_36h_blink_color',
  },
  cancelled_blast: {
    toggle:        'notif_cancelled_blast',
    val:           'notif_cancelled_blast_val',
    unit:          'notif_cancelled_blast_unit',
    blast_all:     'notif_cancelled_blast_all',
    blast_radius:  'notif_cancelled_blast_radius',
    blink:         'notif_cancelled_blast_blink',
    blink_color:   'notif_cancelled_blast_blink_color',
  },
  radius_blast: {
    toggle:      'notif_radius_blast',
    val:         'notif_radius_blast_val',
    unit:        'notif_radius_blast_unit',
    miles:       'notif_radius_blast_miles',
    blast_all:   'notif_radius_blast_all',
    blast_radius:'notif_radius_blast_miles',
    blink:       'notif_radius_blast_blink',
    blink_color: 'notif_radius_blast_blink_color',
  },
};

function _el(id) { return id ? document.getElementById(id) : null; }

async function loadVenueEmailNotifications(venueId) {
  // Block saves during load so onchange handlers don't overwrite DB values
  window._emailNotifLoaded = false;
  try {
    const response = await fetch(`/api/venues/${venueId}/email-notifications`, { credentials: 'include' });
    if (!response.ok) { console.error('Failed to load email notifications:', response.status); return; }
    const settings = await response.json();

    Object.keys(_NOTIF_ID_MAP).forEach(key => {
      const s = settings[key];
      if (!s) return;
      const ids = _NOTIF_ID_MAP[key];

      const toggle = _el(ids.toggle);
      if (toggle) toggle.checked = !!s.enabled;

      const val = _el(ids.val);
      if (val) val.value = s.time_value != null ? s.time_value : val.value;

      const unit = _el(ids.unit);
      if (unit) unit.value = s.time_unit || unit.value;

      const miles = _el(ids.miles);
      if (miles && s.radius_miles != null) miles.value = s.radius_miles;

      const blastAll = _el(ids.blast_all);
      if (blastAll) blastAll.checked = !!s.blast_all_enabled;

      const blastRadius = _el(ids.blast_radius);
      if (blastRadius && s.blast_all_radius != null) blastRadius.value = s.blast_all_radius;

      const blinkToggle = _el(ids.blink);
      if (blinkToggle) blinkToggle.checked = !!s.blink_enabled;

      const blinkColor = _el(ids.blink_color);
      if (blinkColor && s.blink_color) blinkColor.value = s.blink_color;

      const waiveFreq = _el(ids.waive_frequency);
      if (waiveFreq) waiveFreq.checked = s.waive_frequency !== false;  // default true

      const cnp = _el(ids.cancel_notify_preferred);
      if (cnp) cnp.checked = !!s.cancel_notify_preferred;
      const cnae = _el(ids.cancel_notify_all_enabled);
      if (cnae) cnae.checked = !!s.cancel_notify_all_enabled;
      const cnar = _el(ids.cancel_notify_all_radius);
      if (cnar && s.cancel_notify_all_radius != null) cnar.value = s.cancel_notify_all_radius;
    });
  } catch (e) {
    console.error('Error loading email notification settings:', e);
  }
  window._emailNotifLoaded = true; // allow saves now
}

async function saveVenueEmailNotifications() {
  // Don't save while initial load is populating fields
  if (!window._emailNotifLoaded) return;

  clearTimeout(_emailNotifSaveTimeout);
  _emailNotifSaveTimeout = setTimeout(async () => {
    const vid = window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (!vid) return;

    const data = {};
    Object.keys(_NOTIF_ID_MAP).forEach(key => {
      const ids = _NOTIF_ID_MAP[key];

      const toggle      = _el(ids.toggle);
      const valEl       = _el(ids.val);
      const unitEl      = _el(ids.unit);
      const milesEl     = _el(ids.miles);
      const blastAllEl  = _el(ids.blast_all);
      const blastRadEl  = _el(ids.blast_radius);
      const blinkEl     = _el(ids.blink);
      const blinkColEl  = _el(ids.blink_color);
      const waiveFreqEl = _el(ids.waive_frequency);
      const cnpEl       = _el(ids.cancel_notify_preferred);
      const cnaeEl      = _el(ids.cancel_notify_all_enabled);
      const cnarEl      = _el(ids.cancel_notify_all_radius);

      data[key] = {
        enabled:          toggle     ? toggle.checked                     : true,
        time_value:       valEl      ? (parseInt(valEl.value)    || 1)    : 1,
        time_unit:        unitEl     ? unitEl.value                       : 'weeks',
        radius_miles:     milesEl    ? (parseInt(milesEl.value)  || 20)   : null,
        blast_all_enabled: blastAllEl ? blastAllEl.checked                : false,
        blast_all_radius: blastRadEl  ? (parseInt(blastRadEl.value) || 20): 20,
        blink_enabled:    blinkEl    ? blinkEl.checked                    : false,
        blink_color:      blinkColEl ? blinkColEl.value                   : null,
        waive_frequency:  waiveFreqEl ? waiveFreqEl.checked               : true,
        cancel_notify_preferred:   cnpEl  ? cnpEl.checked                 : false,
        cancel_notify_all_enabled: cnaeEl ? cnaeEl.checked                : false,
        cancel_notify_all_radius:  cnarEl ? (parseInt(cnarEl.value) || 20): 20,
      };
    });

    try {
      const response = await fetch(`/api/venues/${vid}/email-notifications`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(data)
      });

      if (response.ok) {
        // Show save indicator
        const indicator = document.getElementById('emailNotifSaveIndicator');
        if (indicator) {
          indicator.style.opacity = '1';
          setTimeout(() => { indicator.style.opacity = '0'; }, 2000);
        }
        // Refresh blink settings then immediately re-render calendar
        try {
          const blinkRes = await fetch(`/api/venues/${vid}/email-notifications`, { credentials: 'include' });
          if (blinkRes.ok) window.venueBlinkSettings = await blinkRes.json();
        } catch (_e) {}
        if (typeof window.renderCalendar === 'function') {
          window.invalidateGigs && window.invalidateGigs();
          await window.renderCalendar();
        }
      } else {
        console.error('Save email notifications failed:', response.status, await response.text());
      }
    } catch (e) {
      console.error('Error saving email notification settings:', e);
    }
  }, 400);
}

async function loadPaymentNotice() {
  const notice = document.getElementById('paymentNotice');
  if (!notice) return;
  
  try {
    const vid = window.venueId || new URLSearchParams(window.location.search).get('venue_id') || 0;
    const response = await fetch(`/api/payment-info?venue_id=${vid}`, { credentials: 'include' });
    if (!response.ok) return;
    const info = await response.json();
    
    if (info.free_trial) {
      notice.style.background = 'rgba(245, 158, 11, 0.1)';
      notice.style.border = '1px solid rgba(245, 158, 11, 0.3)';
      notice.style.color = '#f59e0b';
      notice.innerHTML = '🎟️ <strong>Free Trial Active</strong> — After gig is completed, Venue will pay Artist directly until further notice.';
    } else {
      const dayWord = info.delay_days === 1 ? 'day' : 'days';
      notice.style.background = 'rgba(6, 182, 212, 0.08)';
      notice.style.border = '1px solid rgba(6, 182, 212, 0.2)';
      notice.style.color = 'var(--cyan)';
      notice.innerHTML = `💳 Payments are automatically processed <strong>${info.delay_days} ${dayWord}</strong> after the gig at <strong>${info.processing_time_display}</strong>.`;
    }
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

async function loadVenueAnalytics() {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get("venue_id");
  if (!venueId) return;
  
  try {
    const response = await fetch(`/api/analytics/stats/venue/${venueId}`, { credentials: 'include' });
    if (!response.ok) {
      console.log('No analytics data yet');
      return;
    }
    const data = await response.json();
    
    // Update stat cards
    document.getElementById('venueAnalyticsTotalClicks').textContent = data.total_clicks || 0;
    document.getElementById('venueAnalyticsUniqueVisitors').textContent = data.unique_visitors || 0;
    document.getElementById('venueAnalytics7d').textContent = data.clicks_last_7d || 0;
    document.getElementById('venueAnalytics30d').textContent = data.clicks_last_30d || 0;
    
    // Visitor cities
    const visitorCities = document.getElementById('venueVisitorCities');
    if (data.visitor_cities && data.visitor_cities.length > 0) {
      visitorCities.innerHTML = `
        <table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">
          <thead>
            <tr style="border-bottom: 1px solid var(--border);">
              <th style="text-align: left; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">CITY</th>
              <th style="text-align: left; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">STATE</th>
              <th style="text-align: right; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">VIEWS</th>
            </tr>
          </thead>
          <tbody>
            ${data.visitor_cities.map(city => `
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
    const popularGigs = document.getElementById('venuePopularGigs');
    if (data.clicks_by_gig && data.clicks_by_gig.length > 0) {
      popularGigs.innerHTML = `
        <table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">
          <thead>
            <tr style="border-bottom: 1px solid var(--border);">
              <th style="text-align: left; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">DATE</th>
              <th style="text-align: left; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">STATUS</th>
              <th style="text-align: right; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">CLICKS</th>
              <th style="text-align: right; padding: 6px 8px; color: var(--text-gray); font-size: 0.7rem;">UNIQUE</th>
            </tr>
          </thead>
          <tbody>
            ${data.clicks_by_gig.map(gig => `
              <tr style="border-bottom: 1px solid var(--border);">
                <td style="padding: 8px;">${gig.gig_date || 'Unknown'}</td>
                <td style="padding: 8px;">
                  <span style="background: ${gig.gig_status === 'booked' ? 'rgba(239,68,68,0.2)' : 'rgba(34,197,94,0.2)'}; color: ${gig.gig_status === 'booked' ? '#ef4444' : '#22c55e'}; padding: 2px 6px; border-radius: 4px; font-size: 0.7rem; text-transform: uppercase;">${gig.gig_status || 'open'}</span>
                </td>
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
              <span style="color: var(--text-gray); margin-left: 8px;">${click.city || ''}${click.state ? ', ' + click.state : ''}</span>
            </div>
            <span style="color: var(--text-gray); font-size: 0.75rem;">${time}</span>
          </div>
        `;
      }).join('');
      
      const clicks7 = data.recent_clicks.filter(c => parseUTC(c.created_at) >= sevenDaysAgo);
      const clicks30 = data.recent_clicks.filter(c => parseUTC(c.created_at) >= thirtyDaysAgo);
      
      const activity7 = document.getElementById('venueRecentActivity7');
      activity7.innerHTML = clicks7.length > 0 ? renderClicks(clicks7) : '<p style="color: var(--text-muted); font-size: 0.85rem;">No activity in the last 7 days</p>';
      
      const activity30 = document.getElementById('venueRecentActivity30');
      activity30.innerHTML = clicks30.length > 0 ? renderClicks(clicks30) : '<p style="color: var(--text-muted); font-size: 0.85rem;">No activity in the last 30 days</p>';
    }
    
  } catch (error) {
    console.error('Error loading venue analytics:', error);
  }
}

// Initialize entity users manager (non-blocking)
// Wait longer to ensure venue data is loaded
setTimeout(() => {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get("venue_id");
  
  if (venueId) {
    // Try to get venue name
    fetch(`/api/venues/${venueId}`, { credentials: 'include' })
      .then(res => {
        if (!res.ok) throw new Error('Not found');
        return res.json();
      })
      .then(venue => {
        // Venue API might return 'name' or 'venue_name'
        const venueName = venue.name || venue.venue_name || 'this venue';
        console.log('✅ Entity Users: Loaded venue data:', venue);
        console.log('✅ Entity Users: Using venue name:', venueName);
        window.entityUsersManager = initEntityUsers('venue', venueId, venueName);
      })
      .catch(err => {
        console.warn('⚠️ Entity Users: Could not load venue name:', err);
        window.entityUsersManager = initEntityUsers('venue', venueId, 'this venue');
      });
  }
}, 500);

// Toggle Search Artists Section
document.getElementById('toggleSearchArtists').addEventListener('click', function(e) {
  e.preventDefault();
  const section = document.getElementById('searchArtistsSection');
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
// VENUE PAYMENT SETTINGS (Stripe Connect)
// ==========================================

// Load payment settings when page loads
document.addEventListener('DOMContentLoaded', function() {
  setTimeout(loadVenuePaymentSettings, 300);
});

// === Additional Block (Phase 5 pass 2) ===
// ==========================================
// VENUE TAXES TAB
// ==========================================

function getVenueId() {
  return window.venueId || new URLSearchParams(window.location.search).get('venue_id');
}

async function loadVenueTaxSettings() {
  const vid = getVenueId();
  if (!vid) return;
  try {
    const res = await fetch(`/api/venues/${vid}/tax-settings`, { credentials: 'include' });
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('requireW9Toggle').checked = !!data.require_w9;
  } catch (e) {}
}

async function saveVenueTaxSettings() {
  const vid = getVenueId();
  if (!vid) return;
  const checked = document.getElementById('requireW9Toggle').checked;
  try {
    const res = await fetch(`/api/venues/${vid}/tax-settings`, {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ require_w9: checked })
    });
    if (res.ok) {
      const status = document.getElementById('taxSettingsSaveStatus');
      status.style.color = '#22c55e';
      status.textContent = '✓ Saved';
      status.style.opacity = '1';
      setTimeout(() => { status.style.opacity = '0'; }, 2000);
    }
  } catch (e) {}
}

function populateTaxYearDropdown() {
  const sel = document.getElementById('taxYear1099');
  if (!sel || sel.options.length > 1) return;
  const currentYear = new Date().getFullYear();
  const latestYear = currentYear - 1; // 1099s are for the prior year
  sel.innerHTML = '';
  // Start from most recent year down to 2025 (first year of platform)
  for (let y = latestYear; y >= 2025; y--) {
    sel.innerHTML += `<option value="${y}" ${y === latestYear ? 'selected' : ''}>${y}</option>`;
  }
}

// 1099-NEC handlers removed 2026-07-21 along with the Legal/Taxes UI
// section that called them. Stripe (TPSO) issues 1099-Ks directly to
// artists, so venues don't file 1099-NECs for GigsFill-processed
// payments. Backend endpoints in backend/routes/tax.py are still
// registered (auth-gated, no UI entry) for legacy record recovery.

// ── MESSAGE BADGE + RECENT MESSAGES ────────────────────────────────────────
(function() {
  // Show the messages button once page is ready
  const btn = document.getElementById('headerMsgBtn');
  if (btn) btn.style.display = '';

  // Start polling for unread badge — scoped to this venue
  if (typeof startUnreadBadgePolling === 'function') {
    const _p = new URLSearchParams(window.location.search);
    const _vid = _p.get('venue_id');
    startUnreadBadgePolling(30000, _vid ? { venue_id: parseInt(_vid) } : {});
  }
})();

window.showRecentMessages = function() {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get('venue_id');
  openInboxModal({ side: 'venue', venueId: venueId ? parseInt(venueId) : null });
};


// Check if venue already reviewed an artist — update button label if so
window._checkAndMarkArtistReviewed = async function(btn, artistId) {
  try {
    const venueId = window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (!venueId || !artistId) return;
    const res = await fetch('/api/venues/' + venueId + '/artists/' + artistId + '/review', { credentials: 'include' });
    if (!res.ok) return;
    const data = await res.json();
    if (data.reviewed) {
      btn.textContent = '\u270f\ufe0f Edit Review';
      btn.style.color = '#f59e0b';
      btn.style.borderColor = 'rgba(245,158,11,0.5)';
    }
  } catch(e) { /* silent */ }
};
