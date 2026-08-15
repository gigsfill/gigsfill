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
// `srcBtn` optional — pass the tab button directly when calling
// programmatically (deep-link from email digest, etc.). Inline
// onclick callers still work via window.event. Without the fallback
// to a queried button, programmatic .click() in browsers that don't
// populate window.event for synthetic events would crash here.
function switchTab(tab, srcBtn) {
  // Update tab buttons
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const _activeBtn = srcBtn
    || (typeof event !== 'undefined' && event && event.target)
    || document.querySelector(`.tab[onclick*="switchTab('${tab}')"]`);
  if (_activeBtn) _activeBtn.classList.add('active');
  
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
  if (tab === 'email') { loadEmailPreferences(); loadCalendarFeedUrl(); }
  if (tab === 'affiliates') loadAffiliatesPage();
}

// ── Calendar Sync — fetches the iCal feed URL (mints token on first call) ──
async function loadCalendarFeedUrl() {
  const input = document.getElementById('calendarFeedUrl');
  if (!input || input.dataset.loaded === '1') return;
  try {
    const data = (typeof window.apiGetSafe === 'function')
      ? await window.apiGetSafe('/api/me/calendar-feed-url')
      : await (await fetch('/api/me/calendar-feed-url', { credentials: 'include' })).json();
    if (data && data.url) {
      input.value = data.url;
      input.dataset.loaded = '1';
    }
  } catch (e) {
    input.value = 'Could not load calendar URL — try again later';
  }
}

async function copyCalendarFeed() {
  const input = document.getElementById('calendarFeedUrl');
  const btn = document.getElementById('calendarFeedCopyBtn');
  if (!input || !btn) return;
  try {
    await navigator.clipboard.writeText(input.value);
    const orig = btn.textContent;
    btn.textContent = '✓ Copied';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  } catch (_) {
    // Fallback: select the text for manual copy
    input.select();
  }
}

async function rotateCalendarFeed() {
  // BUG FIX (Jul 2026 audit): switch native confirm() + alert() to the
  // branded gf-modals dialogs so the destructive "rotate URL" flow matches
  // the rest of the site's chrome (styled modal, tone/confirmStyle for
  // destructive intent).
  const doRotate = async () => {
    const input = document.getElementById('calendarFeedUrl');
    try {
      if (typeof window.apiPostSafe === 'function') {
        await window.apiPostSafe('/api/me/calendar-feed-url/rotate', {});
      } else {
        await fetch('/api/me/calendar-feed-url/rotate', { method: 'POST', credentials: 'include' });
      }
      input.dataset.loaded = '0';
      await loadCalendarFeedUrl();
    } catch (e) {
      if (typeof window.showErrorModal === 'function') {
        window.showErrorModal('Rotate Failed', (e && e.message) || 'Could not rotate calendar URL.');
      } else {
        alert((e && e.message) || 'Could not rotate token');
      }
    }
  };
  if (typeof window.showConfirm === 'function') {
    window.showConfirm(
      'Rotate calendar URL?',
      'Anyone using the current URL will stop receiving updates and will need the new one.',
      doRotate,
      null,
      { tone: 'warning', confirmStyle: 'danger', confirmLabel: 'Rotate URL' }
    );
  } else if (confirm('Generate a fresh URL? Anyone using the current URL will stop receiving updates and will need the new one.')) {
    doRotate();
  }
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
    if (userEmail) {
      userEmail.value = user.email || '';
      // Cache the loaded email so saveUserSettings can detect a change and
      // prompt for the current password before hitting PUT /api/me — the
      // backend requires it on email change (May 2026 stolen-session hardening).
      userEmail.dataset.loadedEmail = user.email || '';
    }
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

// Save user settings.
//
// Email changes require re-entering the current password (May 2026 hardening
// against stolen-session takeover — see routes/me.py:update_current_user).
// If the email field differs from the loaded value, we prompt for the password
// via the branded gf-modals dialog and thread it into the PUT body. All error
// paths surface the backend's actual `detail` string in a branded modal
// instead of the old browser `alert('Failed to save settings')`.
async function saveUserSettings(e) {
  e.preventDefault();
  const emailEl = document.getElementById('userEmail');
  const loadedEmail = ((emailEl && emailEl.dataset.loadedEmail) || '').trim().toLowerCase();
  const currentEmail = ((emailEl && emailEl.value) || '').trim().toLowerCase();
  const emailChanged = !!currentEmail && currentEmail !== loadedEmail;

  const doSave = async (currentPassword) => {
    const body = {
      first_name: document.getElementById('firstName').value,
      last_name:  document.getElementById('lastName').value,
      email:      document.getElementById('userEmail').value,
      phone:      document.getElementById('phone').value,
    };
    if (currentPassword) body.current_password = currentPassword;

    let response;
    try {
      response = await fetch('/api/me', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      });
    } catch (error) {
      window.showErrorModal('Save Failed', 'Network error — please check your connection and try again.');
      return;
    }

    if (response.ok) {
      showSaveIndicator();
      // Jul 22 2026: refresh the upper-right user dropdown so a
      // first/last-name change appears immediately without a full
      // page reload. initUserDropdown re-fetches /api/me and rebuilds
      // the pill in-place.
      if (typeof window.initUserDropdown === 'function') {
        try { await window.initUserDropdown(); } catch (_e) {}
      }
      if (emailChanged) {
        emailEl.dataset.loadedEmail = currentEmail;
        window.showSuccessModal(
          'Email Update Sent',
          'Check your new inbox — we just sent a verification link there. Your old address also received a heads-up.'
        );
      }
      return;
    }

    // Surface the backend's actual reason. FastAPI returns {"detail": "..."}.
    let detail = '';
    try { const j = await response.json(); detail = (j && j.detail) || ''; } catch (_) {}

    if (typeof detail === 'string' && detail.startsWith('INVALID_PASSWORD')) {
      window.showErrorModal(
        'Wrong Password',
        'That password did not match. Try again — or use the "Forgot password?" link on the login page if you need to reset it.'
      );
    } else if (typeof detail === 'string' && detail.startsWith('EMAIL_UNAVAILABLE')) {
      window.showErrorModal(
        'Email Unavailable',
        'That email address cannot be used. If it belongs to you, sign in from the other account first.'
      );
    } else if (typeof detail === 'string' && detail.startsWith('INVALID_PHONE')) {
      window.showErrorModal('Phone Invalid', 'Phone must be a 10-digit US number.');
    } else {
      window.showErrorModal('Save Failed', detail || `Could not save settings (HTTP ${response.status}).`);
    }
  };

  if (!emailChanged) {
    await doSave(null);
    return;
  }

  // Email is changing — show a branded prompt for the current password.
  const pwFieldId = '_userProfilePwField';
  const bodyHtml =
    '<p style="margin:0 0 12px;">Changing your email requires confirming your current password.</p>' +
    '<input id="' + pwFieldId + '" type="password" autocomplete="current-password" ' +
      'placeholder="Current password" ' +
      'style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid rgba(255,255,255,0.2);' +
      'background:rgba(255,255,255,0.05);color:var(--text);font-size:0.95rem;box-sizing:border-box;">' +
    '<p style="margin:12px 0 0;font-size:0.8rem;color:var(--text-muted);">' +
      'We\'ll email a verification link to your new address, and notify your old address as a security heads-up.' +
    '</p>';

  // Capture the overlay returned by showStyledModal so the button handler
  // reads its OWN password input reliably (document.getElementById would
  // work here in practice since ids are unique, but scoping to the overlay
  // is more defensive if a stale modal is still fading out). Also fires
  // the save on Enter within the password field.
  const _pwOverlay = window.showStyledModal(
    '🔒 Confirm your password',
    bodyHtml,
    [
      { text: 'Cancel', style: 'ghost' },
      {
        text: 'Confirm & Save', style: 'primary',
        onClick: () => {
          // Handler intentionally NOT async — the async wrapper in gf-modals
          // returns a Promise and marks the modal to stay open, but if we
          // await inside and the modal-close/refetch flow ever throws, the
          // rejection is silent. Instead: synchronously read the password,
          // close the modal, and fire the fetch in a separate microtask.
          const pwEl = (_pwOverlay && _pwOverlay.querySelector('#' + pwFieldId))
                    || document.getElementById(pwFieldId);
          const pw = (pwEl && pwEl.value) ? pwEl.value : '';
          if (!pw) {
            if (pwEl) pwEl.focus();
            return false; // keep modal open (gf-modals: false → stayOpen)
          }
          window.closeAllModals();
          // Fire and forget — doSave surfaces its own success/error modal.
          Promise.resolve().then(() => doSave(pw)).catch((err) => {
            console.error('[user-profile] doSave error:', err);
            window.showErrorModal('Save Failed', (err && err.message) || 'Unexpected error — please try again.');
          });
          // Return nothing — modal is already closed above.
        }
      }
    ],
    { size: 'sm' }
  );
  // Autofocus + Enter-to-submit on the password field.
  setTimeout(() => {
    const pwEl = (_pwOverlay && _pwOverlay.querySelector('#' + pwFieldId))
              || document.getElementById(pwFieldId);
    if (!pwEl) return;
    pwEl.focus();
    pwEl.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        const confirmBtn = _pwOverlay && _pwOverlay.querySelector('.gfm-modal-footer .btn.primary');
        if (confirmBtn) confirmBtn.click();
      }
    });
  }, 50);
}

// Load artists count
async function loadArtistsCount() {
  try {
    const response = await fetch('/api/my/artists', { credentials: 'include' });
    if (!response.ok) return;
    const artists = await response.json();
    const countEl = document.getElementById('artistCount');
    if (countEl) countEl.textContent = `(${artists.length})`;

    // Jul 2026: hide the "My Availability" tab entirely for users
    // with no artist attached — availability windows only affect
    // artist-side booking, so the tab is meaningless for venue-only
    // users. Also hide the tab's content panel so a deep-link like
    // #availability doesn't render a naked empty section.
    const hasArtists = artists.length > 0;
    document.querySelectorAll('.tab').forEach(function (btn) {
      var oc = btn.getAttribute('onclick') || '';
      if (oc.indexOf("switchTab('availability')") !== -1) {
        btn.style.display = hasArtists ? '' : 'none';
      }
    });
    var availPanel = document.getElementById('availability-tab');
    if (availPanel && !hasArtists) availPanel.style.display = 'none';
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
    
    // Jul 2026: only the ORIGINAL OWNER sees a Delete button. Delegated
    // team members ('member' role) can't delete the artist — the API would
    // 403 anyway, so we don't render the button. If they want to leave the
    // team they can do it from the artist's Team tab (or the Delete Account
    // flow surfaces their delegated memberships).
    container.innerHTML = artists.map(function(artist, index) {
      const isOwner = artist.role === 'owner';
      const nameSafe = artist.name || '';
      const btnHtml = isOwner
        ? '<button class="btn" style="background: #dc3545;" onclick="event.stopPropagation(); deleteArtist(' + artist.id + ')">Delete</button>'
        : '<span style="font-size:0.72rem;color:var(--text-muted);padding:4px 10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:5px;" title="You have team access but only the original owner can delete this artist">Member access</span>';
      return '<div class="entity-item draggable" draggable="true" data-id="' + artist.id + '" data-type="artist" data-index="' + index + '">' +
        '<span class="drag-handle" title="Drag to reorder">☰</span>' +
        '<div class="entity-item-content" onclick="window.location.href=\'/app/artist-book-gigs.html?artist_id=' + artist.id + '\'">' +
          '<span style="color: var(--accent-cyan); font-weight: 500;">' + esc(nameSafe) + '</span>' +
          btnHtml +
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
    
    // Same owner-only rule as artists — see comment in loadArtists.
    container.innerHTML = venues.map(function(venue, index) {
      const isOwner = venue.role === 'owner';
      const nameSafe = venue.name || '';
      const btnHtml = isOwner
        ? '<button class="btn" style="background: #dc3545;" onclick="event.stopPropagation(); deleteVenue(' + venue.id + ')">Delete</button>'
        : '<span style="font-size:0.72rem;color:var(--text-muted);padding:4px 10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:5px;" title="You have team access but only the original owner can delete this venue">Member access</span>';
      return '<div class="entity-item draggable" draggable="true" data-id="' + venue.id + '" data-type="venue" data-index="' + index + '">' +
        '<span class="drag-handle" title="Drag to reorder">☰</span>' +
        '<div class="entity-item-content" onclick="window.location.href=\'/app/venue-create-gigs.html?venue_id=' + venue.id + '\'">' +
          '<span style="color: var(--accent-cyan); font-weight: 500;">' + esc(nameSafe) + '</span>' +
          btnHtml +
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

// Jul 2026 — proper delete flow for standalone artist/venue deletion.
// Fetches an informed preview from the backend, renders a branded modal
// that:
//   1. Grays out the button for delegated (non-owner) users with a clear
//      explanation that only the original owner can delete.
//   2. Lists upcoming booked gigs that will be cancelled (with dates and
//      counterparties), so the user knows what emails will fire.
//   3. Blocks the button entirely if the backend reports any live/
//      mid-flight transactions, with a support-contact hint.
//   4. Requires typing DELETE to confirm — no accidental clicks.
//   5. Reminds the user that PAST gigs, payments, and reviews stay intact
//      (tombstone model) so their delete decision is well-informed.
//   6. Surfaces backend `detail` on errors via a branded modal rather than
//      a raw browser alert.

function _entDelFmtDate(iso) {
  if (!iso) return '';
  try {
    const [y, m, d] = String(iso).slice(0, 10).split('-').map(n => parseInt(n, 10));
    return new Date(y, m - 1, d).toLocaleDateString('en-US',
      { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  } catch (_) { return iso; }
}

function _entDelEsc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

async function _openStandaloneEntityDeleteModal(kind, id) {
  // kind: 'artist' | 'venue'
  const label = kind === 'artist' ? 'Artist' : 'Venue';
  const emoji = kind === 'artist' ? '🎤' : '📍';

  // Fetch informed preview
  let preview;
  try {
    const resp = await fetch(`/api/${kind}s/${id}/delete-preview`, { credentials: 'include' });
    if (!resp.ok) {
      const j = await resp.json().catch(() => ({}));
      window.showErrorModal(`Delete ${label}`, j.detail || `Could not load ${kind} info (HTTP ${resp.status}).`);
      return;
    }
    preview = await resp.json();
  } catch (e) {
    window.showErrorModal(`Delete ${label}`, `Network error loading ${kind} info. Please try again.`);
    return;
  }

  const nameEsc = _entDelEsc(preview.name);

  // Already-deleted rare edge case: modal collapses to a single info message.
  if (preview.already_deleted) {
    window.showAlert(`${nameEsc} has already been deleted.`, `${emoji} Already Deleted`);
    return;
  }

  // NON-OWNER: greyed-out modal, no Delete button.
  if (!preview.is_owner) {
    const bodyHtml =
      `<div style="padding:4px 0 12px;">` +
      `<p style="margin:0 0 12px;font-size:0.95rem;">You have delegated access to <strong>${nameEsc}</strong> as a team member, but only the original owner can delete this ${kind}.</p>` +
      `<p style="margin:0 0 12px;font-size:0.85rem;color:var(--text-muted);">If you no longer want access, ask the owner to remove you from the team — or use the Team tab to leave.</p>` +
      `</div>`;
    window.showStyledModal(
      `${emoji} Cannot Delete ${label}`,
      bodyHtml,
      [{ text: 'Got it', style: 'primary' }],
      { size: 'sm', tone: 'warning' }
    );
    return;
  }

  // OWNER path: build informed modal body.
  const upcoming = preview.upcoming_gigs || [];
  const live = preview.live_txns || [];
  const others = parseInt(preview.other_users_count || 0);

  let body = '<div style="padding:4px 0 4px;">';
  body += `<p style="margin:0 0 12px;font-size:0.9rem;color:var(--text);">This will permanently delete <strong>${nameEsc}</strong> from active use.</p>`;

  // History-preservation reassurance
  body += `<div style="background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.25);border-radius:8px;padding:10px 12px;margin-bottom:14px;font-size:0.8rem;color:#93c5fd;line-height:1.55;">` +
    `<strong style="color:#bfdbfe;">History is preserved.</strong> Past gigs, payments, and reviews stay intact — they'll just render as "[Deleted] ${nameEsc}" going forward.` +
    `</div>`;

  // Live-txn BLOCKER (this hides the Delete button entirely).
  if (live.length > 0) {
    body += `<div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.35);border-radius:8px;padding:12px;margin-bottom:14px;">`;
    body += `<p style="color:#fca5a5;font-size:0.85rem;font-weight:700;margin:0 0 8px;">🚫 Deletion is blocked — ${live.length} in-flight payment${live.length > 1 ? 's' : ''}:</p>`;
    body += '<ul style="margin:0 0 8px;padding-left:18px;color:#fecaca;font-size:0.78rem;line-height:1.6;">';
    live.slice(0, 6).forEach(t => {
      const counterparty = kind === 'artist' ? (t.venue_name || 'a venue') : (t.artist_name || 'an artist');
      body += `<li>${_entDelFmtDate(t.date)} · ${_entDelEsc(counterparty)} · <em>${_entDelEsc(t.status)}</em></li>`;
    });
    if (live.length > 6) body += `<li>…and ${live.length - 6} more</li>`;
    body += '</ul>';
    body += `<p style="margin:0;color:#fca5a5;font-size:0.78rem;">These clear on their own once each gig's payout completes — try again once they've all settled.</p>`;
    body += '</div>';
  } else if (upcoming.length > 0) {
    // Upcoming-gig WARNING (informational, not blocking).
    body += `<div style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:8px;padding:12px;margin-bottom:14px;">`;
    body += `<p style="color:#f59e0b;font-size:0.85rem;font-weight:700;margin:0 0 8px;">⚠️ ${upcoming.length} upcoming booked gig${upcoming.length > 1 ? 's' : ''} will be cancelled:</p>`;
    body += '<ul style="margin:0;padding-left:18px;color:#fcd34d;font-size:0.78rem;line-height:1.6;">';
    upcoming.slice(0, 6).forEach(g => {
      const counterparty = kind === 'artist' ? (g.venue_name || 'a venue') : (g.artist_names || 'a booked artist');
      body += `<li>${_entDelFmtDate(g.date)} · ${_entDelEsc(counterparty)}</li>`;
    });
    if (upcoming.length > 6) body += `<li>…and ${upcoming.length - 6} more</li>`;
    body += '</ul>';
    body += `<p style="margin:8px 0 0;color:#fcd34d;font-size:0.78rem;">The other party will be notified by email.</p>`;
    body += '</div>';
  }

  // Team-member note.
  if (others > 0) {
    body += `<p style="margin:0 0 14px;font-size:0.82rem;color:var(--text-muted);">👥 ${others} other team member${others > 1 ? 's' : ''} will lose access.</p>`;
  }

  // Typed DELETE confirm — only if no blockers.
  const blocked = live.length > 0;
  if (!blocked) {
    body += `<label for="_entDelConfirmInput" style="display:block;font-size:0.82rem;color:var(--text-gray);margin-bottom:6px;">Type <strong style="color:#ef4444;">DELETE</strong> to confirm:</label>`;
    body += `<input id="_entDelConfirmInput" type="text" autocomplete="off" placeholder="DELETE" style="width:100%;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:0.9rem;box-sizing:border-box;">`;
  }
  body += '</div>';

  const overlay = window.showStyledModal(
    `⚠️ Delete ${label}`,
    body,
    blocked
      ? [{ text: 'Close', style: 'ghost' }]
      : [
          { text: 'Cancel', style: 'ghost' },
          {
            text: `Delete ${label}`, style: 'danger',
            onClick: () => {
              const input = (overlay && overlay.querySelector('#_entDelConfirmInput'))
                          || document.getElementById('_entDelConfirmInput');
              const typed = ((input && input.value) || '').trim();
              if (typed !== 'DELETE') {
                if (input) { input.focus(); input.style.borderColor = '#ef4444'; }
                return false;   // stayOpen
              }
              window.closeAllModals();
              // Pass the RAW name to the submit helper. It will only be used
              // for showSuccessModal/showAlert, which internally escape.
              // BUG FIX (Jul 2026 audit): was passing nameEsc → double-escape.
              Promise.resolve().then(() => _standaloneEntityDeleteSubmit(kind, id, preview.name)).catch(err => {
                console.error(`[user-profile] delete ${kind} error:`, err);
                window.showErrorModal(`Delete ${label} Failed`, (err && err.message) || 'Unexpected error.');
              });
            }
          }
        ],
    { size: 'md', tone: blocked ? 'warning' : 'error' }
  );

  // Only enable Delete button once DELETE is typed. Uses observer on the
  // typed input; matches the delete-account modal's behavior.
  if (!blocked) {
    setTimeout(() => {
      const input = (overlay && overlay.querySelector('#_entDelConfirmInput'))
                  || document.getElementById('_entDelConfirmInput');
      const btn = overlay && overlay.querySelector('.gfm-modal-footer .btn.danger');
      if (!input || !btn) return;
      btn.disabled = true;
      btn.style.opacity = '0.4';
      btn.style.cursor = 'not-allowed';
      input.addEventListener('input', () => {
        const ok = input.value.trim() === 'DELETE';
        btn.disabled = !ok;
        btn.style.opacity = ok ? '1' : '0.4';
        btn.style.cursor = ok ? 'pointer' : 'not-allowed';
        input.style.borderColor = ok ? '#22c55e' : 'var(--border)';
      });
      input.focus();
    }, 60);
  }
}

async function _standaloneEntityDeleteSubmit(kind, id, rawName) {
  // rawName: unescaped display name. showSuccessModal/showAlert/
  // showErrorModal internally escape their message parameter (see
  // gf-modals.js), so passing pre-escaped text produced &amp;amp;
  // etc. in the final DOM. Use rawName directly.
  const label = kind === 'artist' ? 'Artist' : 'Venue';
  try {
    const resp = await fetch(`/api/${kind}s/${id}`, { method: 'DELETE', credentials: 'include' });
    if (resp.ok) {
      showSaveIndicator();
      if (typeof loadArtists === 'function' && kind === 'artist') { loadArtists(); loadArtistsCount(); }
      if (typeof loadVenues  === 'function' && kind === 'venue')  { loadVenues();  loadVenuesCount();  }
      window.showSuccessModal(`${label} Deleted`, `${rawName} has been deleted. Past gigs, payments, and reviews stay in venue/artist histories under the "[Deleted]" name.`);
      return;
    }
    let detail = '';
    try { const j = await resp.json(); detail = (j && j.detail) || ''; } catch (_) {}
    if (detail.startsWith('NOT_YOUR_')) {
      window.showErrorModal('Not Allowed', 'Only the original owner can delete this profile.');
    } else if (detail.startsWith('LIVE_TRANSACTION_EXISTS')) {
      window.showErrorModal('Deletion Blocked', detail.replace(/^LIVE_TRANSACTION_EXISTS:\s*/, ''));
    } else if (detail.startsWith('ALREADY_DELETED')) {
      window.showAlert('This profile was already deleted.', 'Already Deleted');
    } else {
      window.showErrorModal(`Delete ${label} Failed`, detail || `HTTP ${resp.status}`);
    }
  } catch (e) {
    window.showErrorModal(`Delete ${label} Failed`, (e && e.message) || 'Network error.');
  }
}

async function deleteArtist(artistId) {
  return _openStandaloneEntityDeleteModal('artist', artistId);
}

async function deleteVenue(venueId) {
  return _openStandaloneEntityDeleteModal('venue', venueId);
}

// Load notification preferences (Email + SMS)
let _carrierLoaded = false;
async function loadEmailPreferences() {
  try {
    const artistsResponse = await fetch('/api/my/artists', { credentials: 'include' });
    const venuesResponse = await fetch('/api/my/venues', { credentials: 'include' });
    
    const hasArtists = artistsResponse.ok && (await artistsResponse.json()).length > 0;
    const hasVenues = venuesResponse.ok && (await venuesResponse.json()).length > 0;
    
    // Texting (SMS) is gated by an admin master switch. If it's off, the
    // SMS Setup section stays display:none (already set on the element)
    // and we skip the carrier / SMS-prefs fetches entirely. Cheap public
    // endpoint, no auth needed. Once admin flips Texting Enabled = ON
    // in Platform Settings → Text Settings, this resolves true and the
    // existing flow takes over unchanged.
    let textingEnabled = false;
    try {
      const tx = await fetch('/api/public/texting-status', { credentials: 'omit' });
      if (tx.ok) {
        const txData = await tx.json();
        textingEnabled = !!txData.enabled;
      }
    } catch (_) { /* default off */ }
    const smsSection = document.getElementById('smsSetupSection');
    if (smsSection) smsSection.style.display = textingEnabled ? '' : 'none';

    // Load email prefs, SMS prefs, carriers, and user data in parallel.
    // SMS prefs + carriers are only fetched when texting is enabled —
    // saves two round trips on every profile load while the feature is off.
    const [emailRes, smsRes, carrierRes, userRes] = await Promise.all([
      fetch('/api/user-email-preferences', { credentials: 'include' }),
      textingEnabled ? fetch('/api/user-sms-preferences', { credentials: 'include' }) : Promise.resolve({ ok: false }),
      textingEnabled ? fetch('/api/sms-carriers', { credentials: 'include' }) : Promise.resolve({ ok: false }),
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

    // Blast notification types — separate group, different defaults.
    // The first entry is the master "daily digest" toggle (Jun 2026):
    // when OFF, no consolidated digest email is sent regardless of the
    // per-window toggles below. Per-window toggles still gate what
    // gets ENQUEUED — the master toggle gates what gets SENT.
    const blastLabels = {
      'open_gig_daily_digest': { title: 'Daily Open-Gig Digest', desc: 'One consolidated email per day listing your upcoming booked gigs and open gigs you are eligible to book in the next 4 weeks. Turn off if you prefer not to receive a daily summary.' },
      'venue_open_gig_36h':  { title: '36-Hour Gig Notice',   desc: 'Include last-minute open gigs (within 36 hours) in your daily digest' },
      'venue_open_gig_1w':   { title: '1-Week Gig Notice',    desc: 'Include open gigs 1 week out in your daily digest' },
      'venue_open_gig_2w':   { title: '2-Week Gig Notice',    desc: 'Include open gigs 2 weeks out in your daily digest' },
      'venue_open_gig_4w':   { title: '4-Week Gig Notice',    desc: 'Include open gigs 4 weeks out in your daily digest' },
      'cancelled_gig_preferred_blast': { title: 'Cancellation Blast (Preferred)',  desc: 'When a booked gig is cancelled and re-opened for preferred artists. Sent immediately, not via digest.' },
      'cancelled_gig_radius_blast':    { title: 'Cancellation Blast (All Artists)', desc: 'When a booked gig is cancelled and blasted to all nearby artists. Sent immediately, not via digest.' },
    };

    // Defaults: digest master ON; 1w and 36h ON, 2w/4w OFF.
    const blastDefaults = {
      'open_gig_daily_digest': true,
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
      // 2026-08-10: 'venue_booking_approval_request' row removed. This
      // notification is fully venue-scoped now — controlled by the
      // "Require my approval for same-day bookings" toggle on each
      // venue's Email Notifications → Booking Policies section. When
      // the gate is ON, every venue team member gets the email; when
      // OFF, no gate fires so no email is sent. No per-user opt-out.
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
        '<div style="display: flex; align-items: center; gap: 18px; flex-shrink: 0; margin-right: 14px;">' +
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
        '<div style="flex: 1; min-width: 0;">' +
          '<div style="font-size: 0.88rem; font-weight: 600; color: var(--text);">' + label.title + (label.desc ? '<span style="font-weight: 400; font-style: italic; color: var(--text-gray); font-size: 0.8rem; margin-left: 6px;">(' + label.desc + ')</span>' : '') + '</div>' +
        '</div>' +
      '</div>';
    }
    
    // Populate artist notifications
    if (hasArtists) {
      // Jul 2026: mirror the Venue Notifications section header so
      // both sub-sections read as parallel. Same style + spacing as
      // the venue heading below (cyan, 0.9rem, uppercase).
      let html = '<div style="border-bottom: 1px solid var(--border); padding-bottom: 8px; margin-bottom: 8px;">' +
        '<h3 style="color: var(--cyan); font-size: 0.9rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; margin: 0;">Artist Notifications</h3>' +
        '</div>';
      Object.keys(artistLabels).forEach(function(type) {
        var ep = emailPrefs.find(function(p) { return p.notification_type === type; });
        var sp = smsPrefs.find(function(p) { return p.notification_type === type; });
        var emailOn = ep ? ep.enabled : true;
        var smsOn = sp ? sp.enabled : false;
        html += buildRow(type, artistLabels[type], emailOn, smsOn, smsReady);
      });
      // Blast notifications section. Restructured (Jun 19 2026):
      //   Daily Open-Gig Digest          ← master parent toggle
      //     ↳ 36-hour notice              ← children indented under
      //     ↳ 1-week notice               ← only shown when master is ON
      //     ↳ 2-week notice
      //     ↳ 4-week notice
      //   Cancellation Blast (Preferred)  ← separate (immediate-send, not via digest)
      //   Cancellation Blast (All Artists)
      //
      // The 4 window children are FILTERS for what enters the daily
      // digest. If master is off, no email is sent regardless of the
      // children, so we hide them entirely to avoid the impression
      // they do anything on their own.
      if (Object.keys(blastLabels).length > 0) {
        // Jul 2026: promoted to match the top-of-page "Notification
        // Preferences" heading (1rem, gradient title, wider letter-
        // spacing) so it reads as a real section break, not a
        // sub-label. Extra top margin gives it breathing room away
        // from the last artist row above.
        html += '<div style="border-bottom: 1px solid var(--border); padding-bottom: 8px; margin: 32px 0 8px 0;">' +
          '<h3 class="gradient-title" style="font-size: 1rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin: 0;">⚡ Venue Blast Emails</h3>' +
          '<p style="font-size: 0.76rem; color: var(--text-gray); margin: 4px 0 0;">Control which automated emails you receive from venues about open gigs.</p>' +
          '</div>';

        var DIGEST_KEY = 'open_gig_daily_digest';
        var DIGEST_CHILDREN = ['venue_open_gig_36h', 'venue_open_gig_1w',
                               'venue_open_gig_2w', 'venue_open_gig_4w'];
        var CANCEL_KEYS = ['cancelled_gig_preferred_blast', 'cancelled_gig_radius_blast'];

        // 1. Master digest toggle.
        (function() {
          var ep = emailPrefs.find(function(p) { return p.notification_type === DIGEST_KEY; });
          var sp = smsPrefs.find(function(p) { return p.notification_type === DIGEST_KEY; });
          var emailOn = ep ? !!ep.enabled : blastDefaults[DIGEST_KEY];
          var smsOn = sp ? sp.enabled : false;
          html += buildRow(DIGEST_KEY, blastLabels[DIGEST_KEY], emailOn, smsOn, smsReady);
        })();

        // 2. Indented children — wrapped in #digestChildrenWrap so the
        // master toggle's onchange can show/hide the whole block.
        var masterEp = emailPrefs.find(function(p) { return p.notification_type === DIGEST_KEY; });
        var masterOn = masterEp ? !!masterEp.enabled : blastDefaults[DIGEST_KEY];
        html += '<div id="digestChildrenWrap" style="padding-left:32px;border-left:2px solid rgba(245,158,11,0.25);margin-left:6px;margin-bottom:6px;' + (masterOn ? '' : 'display:none;') + '">' +
          '<p style="font-size:0.72rem;color:var(--text-gray);margin:6px 0 8px 0;font-style:italic;">Which notices feed your daily digest:</p>';
        DIGEST_CHILDREN.forEach(function(type) {
          var ep = emailPrefs.find(function(p) { return p.notification_type === type; });
          var sp = smsPrefs.find(function(p) { return p.notification_type === type; });
          var emailOn = ep ? !!ep.enabled : blastDefaults[type];
          var smsOn = sp ? sp.enabled : false;
          html += buildRow(type, blastLabels[type], emailOn, smsOn, smsReady);
        });
        html += '</div>';

        // 3. Cancellation blasts (independent — sent immediately, not via digest).
        CANCEL_KEYS.forEach(function(type) {
          if (!blastLabels[type]) return;
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
    // Digest history (artist-only feature — only renders when the
    // user has at least one artist profile, since the digest queue
    // is keyed on artists).
    if (hasArtists) {
      loadDigestHistory();
    } else {
      const sec = document.getElementById('digestHistorySection');
      if (sec) sec.style.display = 'none';
    }
  } catch (error) {
    console.error('Error loading notification preferences:', error);
  }
}

// Digest history — last 30 daily digests this user received, with a
// per-row Resend button that re-sends the historical batch identified
// by sent_at minute. Backend: /api/me/digest-history + /api/me/digest-resend.
async function loadDigestHistory() {
  const sec = document.getElementById('digestHistorySection');
  const list = document.getElementById('digestHistoryList');
  const pending = document.getElementById('digestHistoryPending');
  if (!sec || !list) return;
  sec.style.display = '';
  list.innerHTML = '<div style="color:var(--text-gray);font-size:0.82rem;padding:8px 0;">Loading…</div>';
  if (pending) pending.textContent = '';

  let data;
  try {
    const res = await fetch('/api/me/digest-history', { credentials: 'include' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    data = await res.json();
  } catch (e) {
    list.innerHTML = '<div style="color:#f87171;font-size:0.82rem;">Could not load digest history: ' + (e.message || 'unknown') + '</div>';
    return;
  }

  if (pending && data.pending_count > 0) {
    pending.innerHTML = '<span style="color:#fbbf24;">⏳ ' + data.pending_count + ' notice' +
      (data.pending_count === 1 ? '' : 's') + ' queued for your next digest (' + data.pending_venue_count +
      ' venue' + (data.pending_venue_count === 1 ? '' : 's') + ').</span>';
  }

  if (!data.recent_sends || data.recent_sends.length === 0) {
    list.innerHTML = '<div style="color:var(--text-gray);font-size:0.82rem;padding:8px 0;">No digests received yet. Your first one will go out at 9 AM your time once we have notices to send.</div>';
    return;
  }

  function _fmtSent(s) {
    if (!s) return '';
    try {
      const d = new Date(String(s).replace(' ', 'T') + 'Z');
      if (isNaN(d.getTime())) return s;
      return d.toLocaleString(undefined, {
        weekday: 'short', month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit'
      });
    } catch (_) { return s; }
  }
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  // 2026-08-07: whole row is now clickable → opens a preview modal
  // that renders the digest email inline (via /api/me/digest-preview).
  // The Resend button stays available on the row but stopPropagates so
  // its click doesn't open the modal.
  const rows = data.recent_sends.map(r => `
    <tr class="digestMyRow" data-minute="${_esc(r.sent_at_minute)}"
        style="border-bottom:1px solid rgba(255,255,255,0.05);cursor:pointer;transition:background 0.12s;"
        title="Click to preview this digest">
      <td style="padding:8px 12px 8px 0;font-size:0.82rem;color:var(--text);">${_esc(_fmtSent(r.sent_at_minute))}</td>
      <td style="padding:8px 12px;font-size:0.78rem;color:var(--text-gray);text-align:right;">${r.gig_count} gig${r.gig_count === 1 ? '' : 's'}</td>
      <td style="padding:8px 12px;font-size:0.78rem;color:var(--text-gray);text-align:right;">${r.venue_count} venue${r.venue_count === 1 ? '' : 's'}</td>
      <td style="padding:8px 0;text-align:right;">
        <button class="digestMyResendBtn" data-minute="${_esc(r.sent_at_minute)}"
          title="Re-send this digest to your inbox. Subject prefixed [RESENT]."
          style="padding:4px 12px;background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.4);border-radius:5px;color:var(--cyan);cursor:pointer;font-size:0.75rem;font-weight:600;">Resend</button>
      </td>
    </tr>
  `).join('');
  list.innerHTML = '<table style="width:100%;border-collapse:collapse;">' +
    '<thead><tr><th style="text-align:left;padding:4px 0;font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;">Sent</th>' +
    '<th style="text-align:right;padding:4px 0;font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;">Gigs</th>' +
    '<th style="text-align:right;padding:4px 0;font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;">Venues</th>' +
    '<th></th></tr></thead><tbody>' + rows + '</tbody></table>';
  list.querySelectorAll('.digestMyRow').forEach(tr => {
    tr.addEventListener('mouseenter', () => { tr.style.background = 'rgba(6,182,212,0.06)'; });
    tr.addEventListener('mouseleave', () => { tr.style.background = ''; });
    tr.addEventListener('click', () => _myDigestPreview(tr.dataset.minute));
  });
  list.querySelectorAll('.digestMyResendBtn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();  // don't open the preview modal
      _myDigestResend(btn.dataset.minute, btn);
    });
  });
}

// Preview modal — fetches the rendered digest HTML and drops it into a
// sandboxed iframe so the email's own inline styles can't leak into the
// page. Modal chrome matches the rest of the site (dark card + purple
// gradient header + Close button). Escape / backdrop / ✕ all close.
async function _myDigestPreview(minute) {
  if (!minute) return;
  const OV_ID = 'digestMyPreviewOverlay';
  let ov = document.getElementById(OV_ID);
  if (ov) ov.remove();
  ov = document.createElement('div');
  ov.id = OV_ID;
  ov.style.cssText = 'position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,0.75);display:flex;align-items:center;justify-content:center;padding:20px;';
  ov.innerHTML = `
    <div style="background:#1a1f2e;border:1px solid #2a3040;border-radius:14px;width:100%;max-width:820px;max-height:92vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.55);overflow:hidden;">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 22px;border-bottom:1px solid #2a3040;flex-wrap:wrap;gap:10px;">
        <div>
          <div id="digestMyPreviewTitle" style="font-size:1rem;font-weight:700;background:linear-gradient(135deg,#8b5cf6,#06b6d4);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;">📬 Digest preview</div>
          <div id="digestMyPreviewSub" style="font-size:0.75rem;color:#94a3b8;margin-top:2px;"></div>
        </div>
        <button id="digestMyPreviewClose" style="background:transparent;border:none;color:#94a3b8;font-size:1.4rem;cursor:pointer;line-height:1;padding:0 6px;" title="Close">✕</button>
      </div>
      <div id="digestMyPreviewBody" style="flex:1;overflow:auto;background:#f5f5f7;">
        <div style="padding:40px;text-align:center;color:#64748b;font-size:0.9rem;">Loading…</div>
      </div>
    </div>`;
  document.body.appendChild(ov);
  const close = () => { ov.remove(); document.removeEventListener('keydown', esc); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', esc);
  ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
  document.getElementById('digestMyPreviewClose').addEventListener('click', close);

  try {
    const res = await fetch('/api/me/digest-preview?sent_at_minute=' + encodeURIComponent(minute), {
      credentials: 'include',
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.detail || ('HTTP ' + res.status));
    }
    const titleEl = document.getElementById('digestMyPreviewTitle');
    const subEl   = document.getElementById('digestMyPreviewSub');
    if (titleEl && data.subject) titleEl.textContent = '📬 ' + data.subject;
    if (subEl)   subEl.textContent = 'Sent ' + minute + ' UTC · ' + data.row_count + ' gig' + (data.row_count === 1 ? '' : 's');
    const bodyEl = document.getElementById('digestMyPreviewBody');
    // Sandboxed iframe — the digest email uses inline table layouts +
    // its own colors; letting them cascade into the page would clash
    // with the dark theme. srcdoc keeps it self-contained and same-
    // origin-safe.
    bodyEl.innerHTML = '';
    const frame = document.createElement('iframe');
    frame.style.cssText = 'width:100%;height:100%;min-height:60vh;border:0;background:#fff;display:block;';
    frame.setAttribute('sandbox', 'allow-same-origin');
    frame.srcdoc = data.body_html || '<p style="padding:40px;text-align:center;color:#64748b;">Empty digest.</p>';
    bodyEl.appendChild(frame);
  } catch (e) {
    const bodyEl = document.getElementById('digestMyPreviewBody');
    if (bodyEl) {
      bodyEl.innerHTML = '<div style="padding:40px;text-align:center;color:#dc2626;font-size:0.9rem;">Could not load preview: ' +
        (e.message || 'unknown') + '</div>';
    }
  }
}

async function _myDigestResend(minute, btn) {
  if (!minute) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Sending…';
  try {
    const res = await fetch('/api/me/digest-resend', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sent_at_minute: minute })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.ok) {
      btn.textContent = '✓ Sent';
      btn.style.background = 'rgba(34,197,94,0.15)';
      btn.style.borderColor = 'rgba(34,197,94,0.45)';
      btn.style.color = '#22c55e';
      setTimeout(() => {
        btn.disabled = false; btn.textContent = orig;
        btn.style.background = 'rgba(6,182,212,0.12)';
        btn.style.borderColor = 'rgba(6,182,212,0.4)';
        btn.style.color = 'var(--cyan)';
      }, 2500);
    } else {
      btn.textContent = '✗ Failed';
      btn.style.color = '#ef4444';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; btn.style.color = 'var(--cyan)'; }, 3000);
    }
  } catch (e) {
    btn.textContent = '✗ Error';
    btn.style.color = '#ef4444';
    setTimeout(() => { btn.disabled = false; btn.textContent = orig; btn.style.color = 'var(--cyan)'; }, 3000);
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
    // Master digest toggle: show/hide the per-window children below it.
    // Children are filters for the digest — if the master is off, the
    // children don't do anything, so we hide them to avoid confusion.
    if (notificationType === 'open_gig_daily_digest') {
      var wrap = document.getElementById('digestChildrenWrap');
      if (wrap) wrap.style.display = enabled ? '' : 'none';
    }
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

// Logout — Jul 2026 audit fix (F-L11): the `session_token` cookie is
// HttpOnly (auth.py:142); JavaScript CAN'T touch it. The two lines that
// tried to clear it via `document.cookie =` were cargo-cult and did
// nothing. Real logout must POST /api/logout so the server clears the
// signed cookie AND invalidates the session. The `user_id` cookie was
// never set by the backend either — pure dead code.
function logout() {
  fetch('/api/logout', { method: 'POST', credentials: 'include' })
    .catch(() => {})
    .finally(() => {
      window.location.href = "/app/index.html";
    });
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

  // Deep-link support: open a specific tab when the URL hash OR a
  // ?tab=<id> query param matches a tab id. Hash is the older form
  // used by the booking flow's "Cancel Blackout Date" link
  // (/app/user-profile.html#availability); ?tab= is used by the
  // open-gig digest email's "your notification preferences" link
  // (/app/user-profile.html?tab=email). switchTab() relies on
  // event.target, so trigger an actual click on the tab button
  // rather than calling switchTab directly.
  const _qp = new URLSearchParams(window.location.search);
  const _tab = (window.location.hash || '').replace(/^#/, '') || _qp.get('tab') || '';
  if (_tab) {
    const btn = document.querySelector(`.tab[onclick*="switchTab('${_tab}')"]`);
    if (btn) setTimeout(() => switchTab(_tab, btn), 0);
  }
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


