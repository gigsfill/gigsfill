// v75: User Profile Page - Matching Admin Styling

// Format phone number
function formatPhoneNumber(value) {
  // Remove all non-digit characters
  const digits = value.replace(/\D/g, '');
  
  // Format as (XXX) XXX-XXXX
  if (digits.length <= 3) {
    return digits;
  } else if (digits.length <= 6) {
    return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
  } else {
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6, 10)}`;
  }
}

// v86: Modal format functions matching signup-new.html
function formatModalPayDollars(input) {
  let value = input.value.replace(/[^0-9]/g, '');
  if (value) {
    value = parseInt(value).toLocaleString();
  }
  input.value = value;
}

// City autocomplete - calls API for city suggestions
async function searchCities(query, datalistId) {
  if (query.length < 2) return;
  
  try {
    const response = await fetch(`/api/cities/search?q=${encodeURIComponent(query)}&limit=10`);
    if (!response.ok) return;
    
    const cities = await response.json();
    const datalist = document.getElementById(datalistId);
    if (datalist) {
      datalist.innerHTML = cities.map(c => `<option value="${escAttr(c.city)}">${esc(c.city)}, ${esc(c.state)}</option>`).join('');
    }
  } catch (error) {
    console.error('Error searching cities:', error);
  }
}

function formatModalPayCents(input) {
  let value = input.value.replace(/[^0-9]/g, '');
  if (value.length === 1) {
    value = '0' + value;
  } else if (value.length > 2) {
    value = value.substring(0, 2);
  }
  input.value = value;
}

function formatModalCapacity(input) {
  let value = input.value.replace(/[^0-9]/g, '');
  if (value) {
    value = parseInt(value).toLocaleString();
  }
  input.value = value;
}

// Tab switching (same as admin)
function switchTab(tab) {
  // Update tab buttons
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  
  // Update tab content — handle both class and display-style tabs
  document.querySelectorAll('.tab-content').forEach(c => {
    c.classList.remove('active');
    if (c.id === 'affiliates-tab') c.style.display = 'none';
  });
  const panel = document.getElementById(tab + '-tab');
  if (panel) {
    panel.classList.add('active');
    if (tab === 'affiliates') panel.style.display = '';
  }
  
  // Load content based on tab
  if (tab === 'my-artists') loadArtists();
  if (tab === 'my-venues') loadVenues();
  if (tab === 'email') loadEmailPreferences();
  if (tab === 'affiliates') loadAffiliatesPage();
}

// Show save indicator (inline)
function showSaveIndicator() {
  const indicator = document.getElementById('inlineSaveIndicator');
  if (indicator) {
    indicator.style.opacity = '1';
    setTimeout(() => {
      indicator.style.opacity = '0';
    }, 2000);
  }
}

// Load user settings
async function loadUserSettings() {
  try {
    const response = await fetch('/api/me', { credentials: 'include' });
    
    if (!response.ok) {
      console.error('Failed to load user settings:', response.status);
      // If not authenticated, redirect to login
      if (response.status === 401) {
        window.location.href = '/app/index.html';
      }
      return;
    }
    
    const user = await response.json();
    window._currentUserId = user.id;  // expose for affiliate W9 tab
    // Check if user needs W9 prompt (has affiliates but no W9 filed)
    if (typeof checkAffW9Prompt === 'function') {
      setTimeout(checkAffW9Prompt, 800);  // slight delay so page renders first
    }

    // v79: Show admin button if user is admin.
    // Audit fix (May 2026): handle every form `is_admin` has had — true,
    // 'true', 1, '1'. Post-migration values are 0/1; legacy was 'true'/'false'.
    if (user.is_admin === true || user.is_admin === 'true'
        || user.is_admin === 1 || user.is_admin === '1') {
      const adminBtn = document.getElementById('adminButton');
      if (adminBtn) adminBtn.style.display = 'inline-block';
    }
    
    const firstName = document.getElementById('firstName');
    const lastName = document.getElementById('lastName');
    const userEmail = document.getElementById('userEmail');
    const phone = document.getElementById('phone');
    
    if (firstName) firstName.value = user.first_name || '';
    if (lastName) lastName.value = user.last_name || '';
    if (userEmail) userEmail.value = user.email || '';
    if (phone) {
      phone.value = formatPhoneNumber(user.phone || '');
      
      // Add input event listener for phone formatting
      phone.addEventListener('input', (e) => {
        const cursorPos = e.target.selectionStart;
        const oldLength = e.target.value.length;
        e.target.value = formatPhoneNumber(e.target.value);
        const newLength = e.target.value.length;
        
        // Adjust cursor position
        const diff = newLength - oldLength;
        e.target.setSelectionRange(cursorPos + diff, cursorPos + diff);
      });
    }
  } catch (error) {
    console.error('Error loading user settings:', error);
  }
}

// Save user settings
async function saveUserSettings(e) {
  e.preventDefault();
  
  try {
    const response = await fetch('/api/me', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        first_name: document.getElementById('firstName').value,
        last_name: document.getElementById('lastName').value,
        email: document.getElementById('userEmail').value,
        phone: document.getElementById('phone').value
      })
    });
    
    if (response.ok) {
      showSaveIndicator();
    } else {
      alert('Failed to save settings');
    }
  } catch (error) {
    console.error('Error saving settings:', error);
    alert('Failed to save settings');
  }
}

// Load artists count
async function loadArtistsCount() {
  try {
    const response = await fetch('/api/my/artists', { credentials: 'include' });
    if (!response.ok) return;
    const artists = await response.json();
    const countEl = document.getElementById('artistCount');
    if (countEl) countEl.textContent = `(${artists.length})`;
  } catch (error) {
    console.error('Error loading artists count:', error);
  }
}

// Load venues count
async function loadVenuesCount() {
  try {
    const response = await fetch('/api/my/venues', { credentials: 'include' });
    if (!response.ok) return;
    const venues = await response.json();
    const countEl = document.getElementById('venueCount');
    if (countEl) countEl.textContent = `(${venues.length})`;
  } catch (error) {
    console.error('Error loading venues count:', error);
  }
}

// Load artists
async function loadArtists() {
  try {
    const response = await fetch('/api/my/artists?nocache=' + new Date().getTime(), { credentials: 'include' });
    if (!response.ok) return;
    const artists = await response.json();
    
    const container = document.getElementById('artistsList');
    if (!container) return;
    
    if (artists.length === 0) {
      container.innerHTML = '<p style="color: var(--text-muted);">You have no artists yet.</p>';
      return;
    }
    
    container.innerHTML = artists.map(function(artist, index) {
      return '<div class="entity-item draggable" draggable="true" data-id="' + artist.id + '" data-type="artist" data-index="' + index + '">' +
        '<span class="drag-handle" title="Drag to reorder">☰</span>' +
        '<div class="entity-item-content" onclick="window.location.href=\'/app/artist-book-gigs.html?artist_id=' + artist.id + '\'">' +
          '<span style="color: var(--accent-cyan); font-weight: 500;">' + artist.name + '</span>' +
          '<button class="btn" style="background: #dc3545;" onclick="event.stopPropagation(); deleteArtist(' + artist.id + ')">Delete</button>' +
        '</div>' +
      '</div>';
    }).join('');
    
    initDragAndDrop(container, 'artist');
  } catch (error) {
    console.error('Error loading artists:', error);
  }
}

// Load venues
async function loadVenues() {
  try {
    const response = await fetch('/api/my/venues?nocache=' + new Date().getTime(), { credentials: 'include' });
    if (!response.ok) return;
    const venues = await response.json();
    
    const container = document.getElementById('venuesList');
    if (!container) return;
    
    if (venues.length === 0) {
      container.innerHTML = '<p style="color: var(--text-muted);">You have no venues yet.</p>';
      return;
    }
    
    container.innerHTML = venues.map(function(venue, index) {
      return '<div class="entity-item draggable" draggable="true" data-id="' + venue.id + '" data-type="venue" data-index="' + index + '">' +
        '<span class="drag-handle" title="Drag to reorder">☰</span>' +
        '<div class="entity-item-content" onclick="window.location.href=\'/app/venue-create-gigs.html?venue_id=' + venue.id + '\'">' +
          '<span style="color: var(--accent-cyan); font-weight: 500;">' + venue.name + '</span>' +
          '<button class="btn" style="background: #dc3545;" onclick="event.stopPropagation(); deleteVenue(' + venue.id + ')">Delete</button>' +
        '</div>' +
      '</div>';
    }).join('');
    
    initDragAndDrop(container, 'venue');
  } catch (error) {
    console.error('Error loading venues:', error);
  }
}

// Delete artist
// v82: Delete confirmation modal state
let deleteConfirmCallback = null;

// v82: Show delete confirmation modal
function showDeleteConfirmModal(message, callback) {
  document.getElementById('deleteConfirmMessage').textContent = message;
  deleteConfirmCallback = callback;
  document.getElementById('deleteConfirmModal').style.display = 'flex';
}

// v82: Close delete confirmation modal
function closeDeleteConfirmModal() {
  document.getElementById('deleteConfirmModal').style.display = 'none';
  deleteConfirmCallback = null;
}

// v82: Confirm delete action
function confirmDelete() {
  if (deleteConfirmCallback) {
    deleteConfirmCallback();
  }
  closeDeleteConfirmModal();
}

// Delete artist
async function deleteArtist(artistId) {
  showDeleteConfirmModal(
    'Are you sure you want to delete this artist? This cannot be undone.',
    async () => {
      try {
        const response = await fetch(`/api/artists/${artistId}`, {
          method: 'DELETE',
          credentials: 'include'
        });
        
        if (response.ok) {
          showSaveIndicator();
          loadArtists();
          loadArtistsCount();
        } else {
          alert('Failed to delete artist');
        }
      } catch (error) {
        console.error('Error deleting artist:', error);
        alert('Failed to delete artist');
      }
    }
  );
}

// Delete venue
async function deleteVenue(venueId) {
  showDeleteConfirmModal(
    'Are you sure you want to delete this venue? This cannot be undone.',
    async () => {
      try {
        const response = await fetch(`/api/venues/${venueId}`, {
          method: 'DELETE',
          credentials: 'include'
        });
        
        if (response.ok) {
          showSaveIndicator();
          loadVenues();
          loadVenuesCount();
        } else {
          alert('Failed to delete venue');
        }
      } catch (error) {
        console.error('Error deleting venue:', error);
        alert('Failed to delete venue');
      }
    }
  );
}

// Load notification preferences (Email + SMS)
let _carrierLoaded = false;
async function loadEmailPreferences() {
  try {
    const artistsResponse = await fetch('/api/my/artists', { credentials: 'include' });
    const venuesResponse = await fetch('/api/my/venues', { credentials: 'include' });
    
    const hasArtists = artistsResponse.ok && (await artistsResponse.json()).length > 0;
    const hasVenues = venuesResponse.ok && (await venuesResponse.json()).length > 0;
    
    // Load email prefs, SMS prefs, carriers, and user data in parallel
    const [emailRes, smsRes, carrierRes, userRes] = await Promise.all([
      fetch('/api/user-email-preferences', { credentials: 'include' }),
      fetch('/api/user-sms-preferences', { credentials: 'include' }),
      fetch('/api/sms-carriers', { credentials: 'include' }),
      fetch('/api/me', { credentials: 'include' })
    ]);
    
    const emailPrefs = emailRes.ok ? await emailRes.json() : [];
    const smsPrefs = smsRes.ok ? await smsRes.json() : [];
    const carriers = carrierRes.ok ? await carrierRes.json() : [];
    const userData = userRes.ok ? await userRes.json() : {};
    
    const artistContainer = document.getElementById('artistEmailPreferences');
    const venueContainer = document.getElementById('venueEmailPreferences');
    if (!artistContainer || !venueContainer) return;
    
    // Only populate carrier dropdown ONCE to avoid resetting user selection
    const carrierSelect = document.getElementById('smsCarrierSelect');
    if (carrierSelect && !_carrierLoaded) {
      _carrierLoaded = true;
      carriers.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = c.name;
        if (userData.sms_carrier === c.id) opt.selected = true;
        carrierSelect.appendChild(opt);
      });
    }
    
    // Show phone number and status
    const phoneDisplay = document.getElementById('smsPhoneNumber');
    const statusBadge = document.getElementById('smsStatusBadge');
    if (phoneDisplay) {
      phoneDisplay.textContent = userData.phone ? formatPhoneNumber(userData.phone) : 'Not set — add phone in User Settings';
      phoneDisplay.style.color = userData.phone ? 'var(--text)' : 'var(--text-gray)';
    }
    
    // Determine SMS readiness from current dropdown value (not just server data)
    const currentCarrier = carrierSelect ? carrierSelect.value : '';
    const smsReady = !!(userData.phone && currentCarrier);
    
    if (statusBadge) {
      if (smsReady) {
        statusBadge.innerHTML = '<span style="color: #22c55e; font-size: 0.8rem; font-weight: 600;">✓ Ready</span>';
      } else if (userData.phone && !currentCarrier) {
        statusBadge.innerHTML = '<span style="color: #f59e0b; font-size: 0.8rem; font-weight: 600;">Select carrier</span>';
      } else {
        statusBadge.innerHTML = '<span style="color: var(--text-gray); font-size: 0.8rem;">Add phone first</span>';
      }
    }
    
    const artistLabels = {
      'artist_gig_booked':                { title: 'Gig Booked',                  desc: 'When you book a gig' },
      'artist_gig_cancelled':             { title: 'Gig Cancelled',               desc: 'When a gig you booked is cancelled' },
      'artist_gig_edited':                { title: 'Gig Updated',                 desc: 'When a venue edits a gig you have booked' },
      'artist_booking_pending_approval':  { title: 'Booking Pending Approval',    desc: 'When your same-day booking is awaiting venue approval' },
      'artist_booking_approved':          { title: 'Booking Approved',            desc: 'When a venue approves your same-day booking request' },
      'artist_booking_denied':            { title: 'Booking Denied',              desc: 'When a venue denies your same-day booking request' },
      'artist_payment_sent':              { title: 'Payment Received',            desc: 'When your gig payout is sent' },
      'artist_preferred_request':         { title: 'Preferred Request Sent',      desc: 'When you send a preferred status request' },
      'artist_preferred_approved':        { title: 'Preferred Approved',          desc: 'When a Venue approves your preferred request' },
      'artist_preferred_denied':          { title: 'Preferred Denied',            desc: 'When a Venue denies your preferred request' },
      'artist_preferred_revoked':         { title: 'Preferred Revoked',           desc: 'When a Venue revokes your preferred status' },
      'waitlist_offer':                   { title: 'Waitlist Offer',              desc: 'When you reach the top of a waitlist and a gig opens up' },
      'artist_venue_payment_issue':       { title: 'Payment Issue Alert',         desc: 'When there is an issue with your Stripe account affecting payouts' }
    };

    // Blast notification types — separate group, different defaults
    const blastLabels = {
      'venue_open_gig_36h':  { title: '36-Hour Gig Blast',   desc: 'Last-minute open gigs at your preferred venues 36 hours before start' },
      'venue_open_gig_1w':   { title: '1-Week Gig Blast',    desc: 'Open gigs at your preferred venues sent 1 week before the date' },
      'venue_open_gig_2w':   { title: '2-Week Gig Blast',    desc: 'Open gigs at your preferred venues sent 2 weeks before the date' },
      'venue_open_gig_4w':   { title: '4-Week Gig Blast',    desc: 'Open gigs at your preferred venues sent 4 weeks before the date' },
      'cancelled_gig_preferred_blast': { title: 'Cancellation Blast (Preferred)',  desc: 'When a booked gig is cancelled and re-opened for preferred artists' },
      'cancelled_gig_radius_blast':    { title: 'Cancellation Blast (All Artists)', desc: 'When a booked gig is cancelled and blasted to all nearby artists' },
    };

    // Defaults: 1w and 36h are ON, others are OFF
    const blastDefaults = {
      'venue_open_gig_1w':   true,
      'venue_open_gig_36h':  true,
      'venue_open_gig_2w':   false,
      'venue_open_gig_4w':   false,
      'cancelled_gig_preferred_blast': true,
      'cancelled_gig_radius_blast':    true,
    };
    
    const venueLabels = {
      'venue_gig_booked':              { title: 'Gig Booked',               desc: 'When an Artist books a gig at your Venue' },
      'venue_gig_cancelled':           { title: 'Gig Cancelled',            desc: 'When an Artist cancels a gig at your Venue' },
      'venue_booking_approval_request':{ title: 'Same-Day Booking Request', desc: 'When an Artist requests same-day booking approval' },
      'venue_contract_sign_needed':    { title: 'Contract Signed',          desc: 'When an Artist signs a contract and needs your countersignature' },
      'venue_payment_charged':         { title: 'Payment Charged',          desc: 'When your card is charged for a gig booking' },
      'transfer_failed_venue':         { title: 'Payment Failed',           desc: 'When a charge to your card fails' },
      'venue_preferred_request':       { title: 'Preferred Request',        desc: 'When an Artist requests preferred status' },
      'venue_preferred_approved':      { title: 'Preferred Approved',       desc: 'When you approve a preferred request' },
      'venue_preferred_denied':        { title: 'Preferred Denied',         desc: 'When you deny a preferred request' },
      'venue_preferred_revoked':       { title: 'Preferred Revoked',        desc: "When you revoke an Artist's preferred status" }
    };
    
    function buildRow(type, label, emailEnabled, smsEnabled, smsReady) {
      return '<div style="display: flex; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border);">' +
        '<div style="flex: 1; min-width: 0;">' +
          '<div style="font-size: 0.88rem; font-weight: 600; color: var(--text);">' + label.title + (label.desc ? '<span style="font-weight: 400; font-style: italic; color: var(--text-gray); font-size: 0.8rem; margin-left: 6px;">(' + label.desc + ')</span>' : '') + '</div>' +
        '</div>' +
        '<div style="display: flex; align-items: center; gap: 18px; flex-shrink: 0; margin-left: 12px;">' +
          '<div style="text-align: center; min-width: 48px;">' +
            '<label class="toggle-switch" style="margin: 0;">' +
              '<input type="checkbox" ' + (emailEnabled ? 'checked' : '') + ' onchange="toggleEmailPreference(\'' + type + '\', this.checked)">' +
              '<span class="toggle-slider"></span>' +
            '</label>' +
            '<div style="font-size: 0.65rem; color: var(--text-gray); margin-top: 2px; display:none;">Email</div>' +
          '</div>' +
          '<div data-sms-toggle style="display:none; text-align: center; min-width: 48px;' + (!smsReady ? ' opacity: 0.35; pointer-events: none;' : '') + '">' +
            '<label class="toggle-switch" style="margin: 0;">' +
              '<input type="checkbox" ' + (smsEnabled ? 'checked' : '') + ' onchange="toggleSmsPreference(\'' + type + '\', this.checked)"' + (!smsReady ? ' disabled' : '') + '>' +
              '<span class="toggle-slider"></span>' +
            '</label>' +
            '<div style="font-size: 0.65rem; color: var(--text-gray); margin-top: 2px;">Text</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    }
    
    // Populate artist notifications
    if (hasArtists) {
      let html = '';
      Object.keys(artistLabels).forEach(function(type) {
        var ep = emailPrefs.find(function(p) { return p.notification_type === type; });
        var sp = smsPrefs.find(function(p) { return p.notification_type === type; });
        var emailOn = ep ? ep.enabled : true;
        var smsOn = sp ? sp.enabled : false;
        html += buildRow(type, artistLabels[type], emailOn, smsOn, smsReady);
      });
      // Blast notifications section
      if (Object.keys(blastLabels).length > 0) {
        html += '<div style="border-bottom: 1px solid var(--border); padding-bottom: 8px; margin-bottom: 8px;">' +
          '<h3 style="color: #f59e0b; font-size: 0.9rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; margin: 0;">⚡ Venue Blast Emails</h3>' +
          '<p style="font-size: 0.76rem; color: var(--text-gray); margin: 4px 0 0;">Control which automated blast emails you receive from venues about open gigs.</p>' +
          '</div>';
        Object.keys(blastLabels).forEach(function(type) {
          var ep = emailPrefs.find(function(p) { return p.notification_type === type; });
          var sp = smsPrefs.find(function(p) { return p.notification_type === type; });
          var emailOn = ep ? !!ep.enabled : blastDefaults[type];
          var smsOn = sp ? sp.enabled : false;
          html += buildRow(type, blastLabels[type], emailOn, smsOn, smsReady);
        });
      }
      artistContainer.innerHTML = html;
    }
    
    // Populate venue notifications
    if (hasVenues) {
      let html = '<div style="border-bottom: 1px solid var(--border); padding-bottom: 8px; margin-bottom: 8px; margin-top: 28px;">' +
        '<h3 style="color: var(--cyan); font-size: 0.9rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; margin: 0;">Venue Notifications</h3>' +
        '</div>';
      Object.keys(venueLabels).forEach(function(type) {
        var ep = emailPrefs.find(function(p) { return p.notification_type === type; });
        var sp = smsPrefs.find(function(p) { return p.notification_type === type; });
        var emailOn = ep ? ep.enabled : true;
        var smsOn = sp ? sp.enabled : false;
        html += buildRow(type, venueLabels[type], emailOn, smsOn, smsReady);
      });
      venueContainer.innerHTML = html;
    }
    
    if (!hasArtists && !hasVenues) {
      artistContainer.innerHTML = '<p style="color: var(--text-gray); text-align: center;">Create an artist or venue to manage notification preferences.</p>';
    }
  } catch (error) {
    console.error('Error loading notification preferences:', error);
  }
}

// Toggle email preference
async function toggleEmailPreference(notificationType, enabled) {
  try {
    const response = await fetch('/api/user-email-preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        notification_type: notificationType,
        enabled: enabled
      })
    });
    if (response.ok) showSaveIndicator();
  } catch (error) {
    console.error('Error updating email preference:', error);
  }
}

// Toggle SMS preference
async function toggleSmsPreference(notificationType, enabled) {
  try {
    const response = await fetch('/api/user-sms-preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        notification_type: notificationType,
        enabled: enabled
      })
    });
    if (response.ok) showSaveIndicator();
  } catch (error) {
    console.error('Error updating SMS preference:', error);
  }
}

// Update SMS carrier - saves through PUT /api/me
async function updateSmsCarrier(carrier) {
  try {
    // Read current user data so we don't blank other fields
    const getRes = await fetch('/api/me', { credentials: 'include' });
    if (!getRes.ok) return;
    const cur = await getRes.json();
    
    // Save all fields including new carrier
    const response = await fetch('/api/me', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        first_name: cur.first_name || '',
        last_name: cur.last_name || '',
        email: cur.email || '',
        phone: cur.phone || '',
        sms_carrier: carrier || null
      })
    });
    
    if (response.ok) {
      showSaveIndicator();
      const smsReady = !!(cur.phone && carrier);
      document.querySelectorAll('[data-sms-toggle]').forEach(function(wrapper) {
        wrapper.style.opacity = smsReady ? '1' : '0.35';
        wrapper.style.pointerEvents = smsReady ? 'auto' : 'none';
        var cb = wrapper.querySelector('input[type="checkbox"]');
        if (cb) cb.disabled = !smsReady;
      });
      var statusBadge = document.getElementById('smsStatusBadge');
      if (statusBadge) {
        if (smsReady) {
          statusBadge.innerHTML = '<span style="color: #22c55e; font-size: 0.8rem; font-weight: 600;">✓ Ready</span>';
        } else if (cur.phone) {
          statusBadge.innerHTML = '<span style="color: #f59e0b; font-size: 0.8rem; font-weight: 600;">Select carrier</span>';
        } else {
          statusBadge.innerHTML = '<span style="color: var(--text-gray); font-size: 0.8rem;">Add phone first</span>';
        }
      }
    }
  } catch (error) {
    console.error('Error updating SMS carrier:', error);
  }
}

// Logout
function logout() {
  document.cookie = "session_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
  document.cookie = "user_id=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
  window.location.href = "/app/index.html";
}

// v79: US States for modals
const US_STATES = [
  {code: 'AL', name: 'Alabama'}, {code: 'AK', name: 'Alaska'}, {code: 'AZ', name: 'Arizona'},
  {code: 'AR', name: 'Arkansas'}, {code: 'CA', name: 'California'}, {code: 'CO', name: 'Colorado'},
  {code: 'CT', name: 'Connecticut'}, {code: 'DE', name: 'Delaware'}, {code: 'FL', name: 'Florida'},
  {code: 'GA', name: 'Georgia'}, {code: 'HI', name: 'Hawaii'}, {code: 'ID', name: 'Idaho'},
  {code: 'IL', name: 'Illinois'}, {code: 'IN', name: 'Indiana'}, {code: 'IA', name: 'Iowa'},
  {code: 'KS', name: 'Kansas'}, {code: 'KY', name: 'Kentucky'}, {code: 'LA', name: 'Louisiana'},
  {code: 'ME', name: 'Maine'}, {code: 'MD', name: 'Maryland'}, {code: 'MA', name: 'Massachusetts'},
  {code: 'MI', name: 'Michigan'}, {code: 'MN', name: 'Minnesota'}, {code: 'MS', name: 'Mississippi'},
  {code: 'MO', name: 'Missouri'}, {code: 'MT', name: 'Montana'}, {code: 'NE', name: 'Nebraska'},
  {code: 'NV', name: 'Nevada'}, {code: 'NH', name: 'New Hampshire'}, {code: 'NJ', name: 'New Jersey'},
  {code: 'NM', name: 'New Mexico'}, {code: 'NY', name: 'New York'}, {code: 'NC', name: 'North Carolina'},
  {code: 'ND', name: 'North Dakota'}, {code: 'OH', name: 'Ohio'}, {code: 'OK', name: 'Oklahoma'},
  {code: 'OR', name: 'Oregon'}, {code: 'PA', name: 'Pennsylvania'}, {code: 'RI', name: 'Rhode Island'},
  {code: 'SC', name: 'South Carolina'}, {code: 'SD', name: 'South Dakota'}, {code: 'TN', name: 'Tennessee'},
  {code: 'TX', name: 'Texas'}, {code: 'UT', name: 'Utah'}, {code: 'VT', name: 'Vermont'},
  {code: 'VA', name: 'Virginia'}, {code: 'WA', name: 'Washington'}, {code: 'WV', name: 'West Virginia'},
  {code: 'WI', name: 'Wisconsin'}, {code: 'WY', name: 'Wyoming'}
];

// v80: Populate state dropdowns
function populateModalStates() {
  const artistState = document.getElementById('modal_artistState');
  const venueState = document.getElementById('modal_venueState');
  
  const stateOptions = US_STATES.map(s => `<option value="${s.code}">${s.name}</option>`).join('');
  
  if (artistState && artistState.options.length === 1) artistState.innerHTML += stateOptions;
  if (venueState && venueState.options.length === 1) venueState.innerHTML += stateOptions;
}

// v81: Open Add Artist Modal
async function openAddArtistModal() {
  populateModalStates();
  
  // v81: Populate booking contact with actual user info
  try {
    const userResponse = await fetch('/api/me', { credentials: 'include' });
    const user = await userResponse.json();
    
    const bookingSelect = document.getElementById('modal_artistBookingContact');
    if (bookingSelect && user.email) {
      const firstName = user.first_name || '';
      const lastName = user.last_name || '';
      const email = user.email || '';
      const phone = user.phone || '';
      
      const displayName = `${firstName} ${lastName}`.trim();
      let label = displayName;
      if (email) label += ` - ${email}`;
      if (phone) label += ` - ${phone}`;
      
      bookingSelect.innerHTML = `<option value="self" selected>${esc(label)}</option>`;
    }
  } catch (error) {
    console.error('Error loading user info:', error);
  }
  
  document.getElementById('addArtistModal').style.display = 'flex';
}

// v80: Close Add Artist Modal
function closeAddArtistModal() {
  document.getElementById('addArtistModal').style.display = 'none';
  // Reset form
  document.getElementById('modal_artistName').value = '';
  document.getElementById('modal_artistType').value = '';
  document.getElementById('modal_artistCity').value = '';
  document.getElementById('modal_artistState').value = '';
  document.getElementById('modal_artistBio').value = '';
  document.getElementById('modal_bandFormatsField').style.display = 'none';
  const mlf = document.getElementById('modal_lineupField');
  if (mlf) mlf.style.display = 'none';
  document.querySelectorAll('input[name="modal_band_format"]').forEach(cb => cb.checked = false);
  document.querySelectorAll('input[name="modal_artist_style"]').forEach(cb => cb.checked = false);
  document.getElementById('artistModalError').classList.remove('show');
  document.getElementById('artistModalError').textContent = '';
}

// v80: Toggle band formats and styles for artist modal
function toggleModalBandFormats() {
  const artistType = document.getElementById('modal_artistType').value;
  const isLiveBand = artistType === 'Live Band';
  document.getElementById('modal_bandFormatsField').style.display = isLiveBand ? 'block' : 'none';
  const lineupField = document.getElementById('modal_lineupField');
  if (lineupField) lineupField.style.display = isLiveBand ? 'block' : 'none';
}

// v80: Show artist modal error
function showArtistModalError(message) {
  const errorDiv = document.getElementById('artistModalError');
  errorDiv.textContent = message;
  errorDiv.classList.add('show');
  setTimeout(() => {
    errorDiv.classList.remove('show');
  }, 5000);
}

// v82: Submit Artist Modal - EXACT copy from signup completeSignup()
async function submitArtistModal() {
  const name = document.getElementById('modal_artistName').value;
  const artistType = document.getElementById('modal_artistType').value;
  const city = document.getElementById('modal_artistCity').value;
  let state = document.getElementById('modal_artistState').value;
  const bio = document.getElementById('modal_artistBio').value;
  
  // Validation
  if (!name || !artistType || !city || !state) {
    showArtistModalError('Please fill in all required fields');
    return;
  }
  
  // City validation - must be in system (direct API call) + auto-fill state
  if (city) {
    try {
      let cvr = await fetch('/api/validate-city?city=' + encodeURIComponent(city.trim()) + (state ? '&state=' + encodeURIComponent(state) : '') + '&_t=' + Date.now());
      let cvd = await cvr.json();
      if (!cvd.valid && state) {
        cvr = await fetch('/api/validate-city?city=' + encodeURIComponent(city.trim()) + '&_t=' + Date.now());
        cvd = await cvr.json();
      }
      if (!cvd.valid) {
        showCityError(document.getElementById('modal_artistCity'), true);
        return;
      }
      if (cvd.state) {
        state = cvd.state;
        document.getElementById('modal_artistState').value = cvd.state;
      }
    } catch(e) {
      showArtistModalError('Could not validate city. Please try again.');
      return;
    }
  }
  
  // Get styles and lineup if Live Band
  let bandFormats = '';
  let styles = '';
  if (artistType === 'Live Band') {
    const checkedStyles = Array.from(document.querySelectorAll('input[name="modal_artist_style"]:checked'));
    if (checkedStyles.length === 0) {
      showArtistModalError('Please select at least one style');
      return;
    }
    styles = checkedStyles.map(cb => cb.value).join(',');
    
    const checkedFormats = Array.from(document.querySelectorAll('input[name="modal_band_format"]:checked'));
    if (checkedFormats.length === 0) {
      showArtistModalError('Please select at least one lineup option');
      return;
    }
    bandFormats = checkedFormats.map(cb => cb.value).join(',');
  }
  
  // v82: Get full booking contact string (not just 'self')
  const bookingContactSelect = document.getElementById('modal_artistBookingContact');
  const bookingContactText = bookingContactSelect.options[bookingContactSelect.selectedIndex].text;
  
  // Build payload EXACTLY like signup
  const formData = {
    name: name,
    artist_type: artistType,
    city: city,
    state: state,
    bio: bio,
    band_formats: bandFormats,
    styles: styles,
    booking_contact: bookingContactText // v82: Full string, not 'self'
  };
  
  // Check for duplicate artist name in same city+state
  try {
    const dupRes = await fetch('/api/check-duplicate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'artist', name: formData.name, city: formData.city, state: formData.state })
    });
    const dupData = await dupRes.json();
    if (dupData.duplicate) {
      await showProfileDuplicateModal(dupData);
      return;
    }
  } catch(e) { /* on error let through */ }

  try {
    // Show loading state on button
    const submitBtn = event?.target || document.querySelector('#addArtistModal .btn.primary');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Creating...'; }
    
    const response = await fetch('/api/artists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(formData)
    });
    
    if (response.ok) {
      closeAddArtistModal();
      showSaveIndicator();
      loadArtists();
      loadArtistsCount();
    } else {
      const error = await response.text();
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Create Artist'; }
      showArtistModalError('Failed to create artist: ' + error);
    }
  } catch (error) {
    console.error('Error creating artist:', error);
    const submitBtn = document.querySelector('#addArtistModal .btn.primary');
    if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Create Artist'; }
    showArtistModalError('Failed to create artist');
  }
}

// v80: Venue Modal - Step Management
let currentModalVenueStep = 1;

// v85: Open Add Venue Modal - FORCE step 1 with inline styles
function openAddVenueModal() {
  populateModalStates();
  currentModalVenueStep = 1;
  document.getElementById('addVenueModal').style.display = 'flex';
  
  // v85: FORCE step indicators with INLINE STYLES to override everything
  const step1Indicator = document.getElementById('modal_venueStep1Indicator');
  const step2Indicator = document.getElementById('modal_venueStep2Indicator');
  
  // Reset classes
  step1Indicator.className = 'modal-step';
  step2Indicator.className = 'modal-step';
  
  // Add active class to step 1
  step1Indicator.classList.add('active');
  
  // FORCE inline styles as backup
  step1Indicator.style.background = '#22d3ee';  // Cyan
  step1Indicator.style.color = 'white';
  step2Indicator.style.background = 'rgba(255, 255, 255, 0.1)';  // Gray
  step2Indicator.style.color = 'rgba(255, 255, 255, 0.6)';
  
  
  showModalVenueStep(1);
}

// v80: Close Add Venue Modal  
function closeAddVenueModal() {
  document.getElementById('addVenueModal').style.display = 'none';
  currentModalVenueStep = 1;
  // Reset form - v86: Updated for signup-new.html field IDs
  document.getElementById('modal_venueName').value = '';
  document.getElementById('modal_venueAddress').value = '';
  document.getElementById('modal_venueCity').value = '';
  document.getElementById('modal_venueState').value = '';
  document.getElementById('modal_venueZip').value = '';
  document.getElementById('modal_venueDescription').value = '';
  document.getElementById('modal_venueDefaultPayDollars').value = '';
  document.getElementById('modal_venueDefaultPayCents').value = '00';
  document.getElementById('modal_venueFrequency').value = '';
  document.getElementById('modal_venueCapacity').value = '';
  document.getElementById('modal_venueHasStage').value = '0';
  document.getElementById('modal_venueHasSoundEquipment').value = '0';
  document.getElementById('modal_venueHasSoundEngineer').value = '0';
  document.getElementById('modal_venueHasLighting').value = '0';
  document.getElementById('modal_venueArrivalType').value = 'flexible';
  document.getElementById('modal_venueStageWidth').value = '';
  document.getElementById('modal_venueStageDepth').value = '';
  document.getElementById('modal_venueSetupLocation').value = '';
  document.getElementById('modal_venueSoundEquipmentDesc').value = '';
  document.getElementById('modal_venueSoundEngineerDetails').value = '';
  document.getElementById('modal_venueLightingDesc').value = '';
  document.getElementById('modal_venueArrivalHour').value = '12';
  document.getElementById('modal_venueArrivalPeriod').value = 'PM';
  document.getElementById('modal_venueLoadInOut').value = '';
  // Reset toggle sections
  document.getElementById('modal_stageDetails').style.display = 'none';
  document.getElementById('modal_soundDetails').style.display = 'none';
  document.getElementById('modal_engineerDetails').style.display = 'none';
  document.getElementById('modal_lightingDetails').style.display = 'none';
  document.getElementById('modal_arrivalDetails').style.display = 'none';
  document.getElementById('venueModalError').classList.remove('show');
  document.getElementById('venueModalError').textContent = '';
  showModalVenueStep(1);
}

// v85: Show venue step - FORCE with inline styles
function showModalVenueStep(step) {
  
  // Hide all steps
  document.getElementById('modal_venueStep1').classList.remove('active');
  document.getElementById('modal_venueStep2').classList.remove('active');
  
  // Show current step
  document.getElementById('modal_venueStep' + step).classList.add('active');
  
  // Update indicators
  const step1Indicator = document.getElementById('modal_venueStep1Indicator');
  const step2Indicator = document.getElementById('modal_venueStep2Indicator');
  
  
  // Reset classes
  step1Indicator.className = 'modal-step';
  step2Indicator.className = 'modal-step';
  
  // Set classes and INLINE STYLES based on step
  if (step === 1) {
    step1Indicator.classList.add('active');
    // FORCE inline styles - CYAN for step 1, GRAY for step 2
    step1Indicator.style.background = '#22d3ee';
    step1Indicator.style.color = 'white';
    step2Indicator.style.background = 'rgba(255, 255, 255, 0.1)';
    step2Indicator.style.color = 'rgba(255, 255, 255, 0.6)';
  } else if (step === 2) {
    step2Indicator.classList.add('active');
    // v85: BOTH BLANK/GRAY on step 2 as requested
    step1Indicator.style.background = 'rgba(255, 255, 255, 0.1)';
    step1Indicator.style.color = 'rgba(255, 255, 255, 0.6)';
    step2Indicator.style.background = '#22d3ee';
    step2Indicator.style.color = 'white';
  }
  
  
  currentModalVenueStep = step;
}

// v80: Next venue step
function nextModalVenueStep() {
  if (currentModalVenueStep === 1) {
    // Validate step 1
    const name = document.getElementById('modal_venueName').value;
    const address = document.getElementById('modal_venueAddress').value;
    const city = document.getElementById('modal_venueCity').value;
    const state = document.getElementById('modal_venueState').value;
    const zip = document.getElementById('modal_venueZip').value;
    
    if (!name || !address || !city || !state || !zip) {
      showVenueModalError('Please fill in all required fields');
      return;
    }
    
    showModalVenueStep(2);
  }
}

// v80: Previous venue step
function prevModalVenueStep() {
  if (currentModalVenueStep === 2) {
    showModalVenueStep(1);
  }
}

// v80: Show venue modal error
function showVenueModalError(message) {
  const errorDiv = document.getElementById('venueModalError');
  errorDiv.textContent = message;
  errorDiv.classList.add('show');
  setTimeout(() => {
    errorDiv.classList.remove('show');
  }, 5000);
}

// v86: Toggle functions for venue modal - Updated div IDs
function toggleModalStageDetails() {
  const hasStage = document.getElementById('modal_venueHasStage').value === '1';
  document.getElementById('modal_stageDetails').style.display = hasStage ? 'block' : 'none';
}

function toggleModalSoundDetails() {
  const hasSound = document.getElementById('modal_venueHasSoundEquipment').value === '1';
  document.getElementById('modal_soundDetails').style.display = hasSound ? 'block' : 'none';
}

function toggleModalEngineerDetails() {
  const hasEngineer = document.getElementById('modal_venueHasSoundEngineer').value === '1';
  document.getElementById('modal_engineerDetails').style.display = hasEngineer ? 'block' : 'none';
}

function toggleModalLightingDetails() {
  const hasLighting = document.getElementById('modal_venueHasLighting').value === '1';
  document.getElementById('modal_lightingDetails').style.display = hasLighting ? 'block' : 'none';
}

function toggleModalArrivalDetails() {
  const arrivalType = document.getElementById('modal_venueArrivalType').value;
  document.getElementById('modal_arrivalDetails').style.display = arrivalType === 'no_earlier_than' ? 'inline-flex' : 'none';
}

// v86: Submit Venue Modal - Updated for signup-new.html fields
async function submitVenueModal() {
  // v86: Get dollars and cents separately
  const dollarsStr = document.getElementById('modal_venueDefaultPayDollars').value.replace(/,/g, '');
  const centsStr = document.getElementById('modal_venueDefaultPayCents').value || '00';
  const frequency = document.getElementById('modal_venueFrequency').value;
  const capacityStr = document.getElementById('modal_venueCapacity').value.replace(/,/g, '');
  
  if (!dollarsStr || !capacityStr) {
    showVenueModalError('Please fill in all required fields');
    return;
  }
  
  // Compute pay as dollars (backend expects dollars, not cents)
  const dollars = parseInt(dollarsStr) || 0;
  const cents = parseInt(centsStr) || 0;
  const defaultPayDollars = dollars + (cents / 100);
  
  // v86: Define has_stage first, then use it
  const hasStage = parseInt(document.getElementById('modal_venueHasStage').value) || 0;
  const hasSound = parseInt(document.getElementById('modal_venueHasSoundEquipment').value) || 0;
  
  // Build payload EXACTLY like signup completeSignup()
  const payload = {
    venue_name: document.getElementById('modal_venueName').value,
    address_line_1: document.getElementById('modal_venueAddress').value,
    city: document.getElementById('modal_venueCity').value,
    state: document.getElementById('modal_venueState').value,
    zip_code: document.getElementById('modal_venueZip').value,
    description: document.getElementById('modal_venueDescription').value,
    default_pay: defaultPayDollars, // Send as dollars (e.g., 200.50)
    performance_frequency_days: parseInt(frequency) || 0,
    capacity: parseInt(capacityStr) || 0,
    
    // Amenity fields - EXACT copy from signup
    has_stage: hasStage,
    stage_width: hasStage ? (parseInt(document.getElementById('modal_venueStageWidth').value) || 0) : 0,
    stage_depth: hasStage ? (parseInt(document.getElementById('modal_venueStageDepth').value) || 0) : 0,
    setup_location: document.getElementById('modal_venueSetupLocation').value || null,
    has_sound_equipment: hasSound,
    sound_equipment_desc: hasSound ? (document.getElementById('modal_venueSoundEquipmentDesc').value || null) : null,
    has_sound_engineer: hasSound ? (parseInt(document.getElementById('modal_venueHasSoundEngineer').value) || 0) : 0,
    sound_engineer_details: hasSound ? (document.getElementById('modal_venueSoundEngineerDetails').value || null) : null,
    has_lighting: parseInt(document.getElementById('modal_venueHasLighting').value) || 0,
    lighting_desc: document.getElementById('modal_venueLightingDesc').value || null,
    bar_tab_details: document.getElementById('modal_venueBarTabDetails').value || null,
    food_tab_details: document.getElementById('modal_venueFoodTabDetails').value || null,
    load_in_out: document.getElementById('modal_venueLoadInOut').value || null,
    
    // PRO certification
    pro_certified: document.getElementById('modal_venueProCertified')?.checked ? 1 : 0,
    
    // Arrival time fields - EXACT copy from signup
    arrival_type: document.getElementById('modal_venueArrivalType').value || 'flexible',
    arrival_hour: document.getElementById('modal_venueArrivalHour').value || null,
    arrival_period: document.getElementById('modal_venueArrivalPeriod').value || null
  };
  
  // PRO certification validation
  if (!document.getElementById('modal_venueProCertified')?.checked) {
    showVenueModalError('You must certify that your venue maintains active public performance licenses.');
    return;
  }
  
  // City validation - must be in system (direct API call) + auto-fill state
  if (payload.city) {
    try {
      let cvr = await fetch('/api/validate-city?city=' + encodeURIComponent(payload.city.trim()) + (payload.state ? '&state=' + encodeURIComponent(payload.state) : '') + '&_t=' + Date.now());
      let cvd = await cvr.json();
      if (!cvd.valid && payload.state) {
        cvr = await fetch('/api/validate-city?city=' + encodeURIComponent(payload.city.trim()) + '&_t=' + Date.now());
        cvd = await cvr.json();
      }
      if (!cvd.valid) {
        showCityError(document.getElementById('modal_venueCity'), true);
        return;
      }
      if (cvd.state) {
        payload.state = cvd.state;
        document.getElementById('modal_venueState').value = cvd.state;
      }
    } catch(e) {
      showVenueModalError('Could not validate city. Please try again.');
      return;
    }
  }
  
  // Check for duplicate venue name in same city+state
  try {
    const dupRes = await fetch('/api/check-duplicate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'venue', name: payload.venue_name, city: payload.city, state: payload.state })
    });
    const dupData = await dupRes.json();
    if (dupData.duplicate) {
      await showProfileDuplicateModal(dupData);
      return;
    }
  } catch(e) { /* on error let through */ }

  try {
    // Show loading state on button
    const submitBtn = document.querySelector('#addVenueModal .btn.primary[onclick*="submitVenueModal"]');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Creating...'; }
    
    const response = await fetch('/api/venues', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload)
    });
    
    if (response.ok) {
      closeAddVenueModal();
      showSaveIndicator();
      loadVenues();
      loadVenuesCount();
    } else {
      const error = await response.text();
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Create Venue'; }
      showVenueModalError('Failed to create venue: ' + error);
    }
  } catch (error) {
    console.error('Error creating venue:', error);
    const submitBtn = document.querySelector('#addVenueModal .btn.primary[onclick*="submitVenueModal"]');
    if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Create Venue'; }
    showVenueModalError('Failed to create venue');
  }
}

// Load on page load
document.addEventListener('DOMContentLoaded', () => {
  loadUserSettings();
  loadArtistsCount();
  loadVenuesCount();
  
  // User settings form
  const form = document.getElementById('userSettingsForm');
  if (form) {
    form.addEventListener('submit', saveUserSettings);
    
    // Add Enter key handler to blur inputs
    const inputs = form.querySelectorAll('input');
    inputs.forEach(input => {
      input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          input.blur(); // Remove cursor
          form.requestSubmit(); // Submit the form
        }
      });
    });
  }
  
  // v79: Close modals on outside click
  window.addEventListener('click', (e) => {
    const artistModal = document.getElementById('addArtistModal');
    const venueModal = document.getElementById('addVenueModal');
    const deleteModal = document.getElementById('deleteConfirmModal');
    if (e.target === artistModal) closeAddArtistModal();
    if (e.target === venueModal) closeAddVenueModal();
    if (e.target === deleteModal) closeDeleteConfirmModal();
  });
});

// v96: Drag and Drop for entity ordering
var draggedItem = null;

function initDragAndDrop(container, entityType) {
  var items = container.querySelectorAll('.entity-item.draggable');
  
  items.forEach(function(item) {
    item.addEventListener('dragstart', function(e) {
      draggedItem = item;
      item.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', item.dataset.id);
    });
    
    item.addEventListener('dragend', function() {
      item.classList.remove('dragging');
      draggedItem = null;
      
      container.querySelectorAll('.entity-item').forEach(function(el) {
        el.classList.remove('drag-over');
      });
      
      saveEntityOrder(container, entityType);
    });
    
    item.addEventListener('dragover', function(e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (item !== draggedItem) {
        item.classList.add('drag-over');
      }
    });
    
    item.addEventListener('dragleave', function() {
      item.classList.remove('drag-over');
    });
    
    item.addEventListener('drop', function(e) {
      e.preventDefault();
      item.classList.remove('drag-over');
      
      if (draggedItem && item !== draggedItem) {
        var rect = item.getBoundingClientRect();
        var midY = rect.top + rect.height / 2;
        
        if (e.clientY < midY) {
          container.insertBefore(draggedItem, item);
        } else {
          container.insertBefore(draggedItem, item.nextSibling);
        }
      }
    });
  });
}

function saveEntityOrder(container, entityType) {
  var items = container.querySelectorAll('.entity-item.draggable');
  var order = [];
  
  items.forEach(function(item, index) {
    order.push({
      id: parseInt(item.dataset.id),
      display_order: index
    });
  });
  
  fetch('/api/my/' + entityType + 's/order', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ order: order })
  })
  .then(function(response) {
    if (response.ok) {
    }
  })
  .catch(function(error) {
    console.error('Error saving ' + entityType + ' order:', error);
  });
}
// ── Duplicate artist/venue modal for user-profile page ───────────────────────
function showProfileDuplicateModal(dupData) {
  return new Promise(function(resolve) {
    const existing = document.getElementById('profileDupModal');
    if (existing) existing.remove();
    const typeLabel = dupData.type === 'artist' ? 'Artist' : 'Venue';
    const backdrop = document.createElement('div');
    backdrop.id = 'profileDupModal';
    backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:10000;display:flex;align-items:center;justify-content:center;padding:20px;box-sizing:border-box;';
    backdrop.innerHTML =
      '<div style="background:#1a2235;border:1px solid var(--border);border-radius:10px;padding:28px 32px;max-width:440px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5);">' +
        '<div style="font-size:1rem;font-weight:700;color:#06b6d4;margin-bottom:14px;">' + typeLabel + ' Already Exists</div>' +
        '<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);border-radius:8px;padding:14px 16px;margin-bottom:16px;">' +
          '<div style="font-size:0.95rem;font-weight:600;color:var(--text-white);">' + (dupData.name || '') + '</div>' +
          '<div style="font-size:0.8rem;color:var(--text-gray);margin-top:3px;">' + (dupData.city || '') + ', ' + (dupData.state || '') + '</div>' +
        '</div>' +
        '<p style="font-size:0.83rem;color:var(--text-gray);line-height:1.6;margin:0 0 20px;">A ' + typeLabel.toLowerCase() + ' with this name already exists in this city. Would you like to request access to that profile instead?</p>' +
        '<div style="display:flex;gap:10px;justify-content:center;">' +
          '<button id="profDupBack" style="padding:8px 20px;background:transparent;border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:var(--text-gray);font-size:0.8rem;cursor:pointer;">No, Go Back</button>' +
          '<button id="profDupRequest" style="padding:8px 22px;background:rgba(6,182,212,0.2);border:1px solid rgba(6,182,212,0.5);border-radius:6px;color:var(--cyan);font-size:0.8rem;font-weight:700;cursor:pointer;">Request Access</button>' +
        '</div>' +
        '<div id="profDupStatus" style="text-align:center;font-size:0.78rem;margin-top:12px;min-height:18px;"></div>' +
      '</div>';
    document.body.appendChild(backdrop);

    document.getElementById('profDupBack').onclick = function() { backdrop.remove(); resolve(false); };
    backdrop.addEventListener('click', function(e) { if (e.target === backdrop) { backdrop.remove(); resolve(false); } });

    document.getElementById('profDupRequest').onclick = async function() {
      const btn = document.getElementById('profDupRequest');
      const status = document.getElementById('profDupStatus');
      btn.disabled = true; btn.textContent = 'Sending…';
      // Get current user info from window._currentUserInfo if available
      const uInfo = window._currentUserInfo || {};
      try {
        const r = await fetch('/api/request-access', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: dupData.type,
            entity_id: dupData.entity_id,
            requester_name: uInfo.name || '',
            requester_email: uInfo.email || ''
          })
        });
        if (r.ok) {
          status.style.color = '#10b981';
          status.textContent = '✓ Request sent! The profile owner will invite you via email.';
          btn.textContent = 'Done';
          btn.onclick = function() { backdrop.remove(); resolve(true); };
          btn.disabled = false;
        } else { throw new Error('failed'); }
      } catch(e) {
        status.style.color = '#ef4444';
        status.textContent = 'Failed to send request. Please try again.';
        btn.disabled = false; btn.textContent = 'Request Access';
      }
    };
  });
}


