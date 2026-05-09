/**
 * User Dropdown Component
 * Displays user name with dropdown menu for Profile and Sign Out
 */

// Inject dropdown styles
(function injectDropdownStyles() {
  if (document.getElementById('user-dropdown-styles')) return;
  
  const styles = document.createElement('style');
  styles.id = 'user-dropdown-styles';
  styles.textContent = `
    .user-dropdown {
      position: relative;
      display: inline-block;
    }
    
    .user-dropdown-trigger {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      background: transparent;
      border: none;
      color: var(--text-gray, #9ca3af);
      font: 500 0.875rem/1.5 'Inter', -apple-system, sans-serif;
      cursor: pointer;
      transition: color 0.2s ease;
    }
    
    .user-dropdown-trigger:hover {
      color: var(--text, #e5e5e5);
    }
    
    .user-dropdown-trigger .user-name {
      max-width: 150px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    
    .user-dropdown-trigger .dropdown-arrow {
      font-size: 0.6rem;
      transition: transform 0.2s ease;
    }
    
    .user-dropdown.open .dropdown-arrow {
      transform: rotate(180deg);
    }
    
    .user-dropdown-menu {
      position: absolute;
      top: calc(100% + 4px);
      right: 0;
      min-width: 140px;
      background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%);
      border: 1px solid rgba(124, 107, 255, 0.3);
      border-radius: 8px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
      opacity: 0;
      visibility: hidden;
      transform: translateY(-8px);
      transition: all 0.2s ease;
      z-index: 9999;
      overflow: hidden;
    }
    
    .user-dropdown.open .user-dropdown-menu {
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
    }
    
    .user-dropdown-menu a {
      display: block;
      padding: 10px 16px;
      color: var(--text-gray, #9ca3af);
      text-decoration: none;
      font: 500 0.875rem/1.5 'Inter', -apple-system, sans-serif;
      transition: all 0.15s ease;
    }
    
    .user-dropdown-menu a:hover {
      color: var(--text, #e5e5e5);
      background: rgba(255, 255, 255, 0.05);
    }
    
    .user-dropdown-menu .divider {
      height: 1px;
      background: rgba(255, 255, 255, 0.1);
      margin: 4px 0;
    }
  `;
  document.head.appendChild(styles);
})();

/**
 * Initialize user dropdown
 * Call this after DOM is ready
 */
async function initUserDropdown() {
  try {
    // Fetch current user info
    const response = await fetch('/api/me', { credentials: 'include' });
    if (!response.ok) return;
    
    const user = await response.json();
    const userName = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.email || 'User';
    
    // Fetch user's venues to determine if they can invite artists
    let userVenues = [];
    try {
      const vr = await fetch('/api/my/venues', { credentials: 'include' });
      if (vr.ok) userVenues = await vr.json();
    } catch(e) {}
    
    // Find the header-actions container
    const headerActions = document.querySelector('.header-actions');
    if (!headerActions) return;
    
    // Remove any existing dropdown first (in case we're re-initializing)
    const existingDropdown = headerActions.querySelector('.user-dropdown');
    if (existingDropdown) {
      existingDropdown.remove();
    }
    
    // Remove existing User Profile link and Logout/Sign Out button
    const toRemove = [];
    headerActions.querySelectorAll('a, button').forEach(el => {
      const text = el.textContent.trim().toLowerCase();
      if (text === 'user profile' || text === 'logout' || text === 'sign out') {
        toRemove.push(el);
      }
    });
    toRemove.forEach(el => el.remove());
    
    // Build invite/recommend link based on whether user has venues
    // Build dropdown links — Invite Artists only on venue pages
    const isArtistPage = window.location.pathname.includes('artist-book-gigs');
    const inviteLink = (userVenues.length > 0 && !isArtistPage)
      ? '<a href="#" onclick="openInviteArtistsModal(event)">Invite Artists</a>\n'
      : '';
    
    // Create dropdown HTML
    const dropdown = document.createElement('div');
    dropdown.className = 'user-dropdown';
    dropdown.innerHTML = `
      <button class="user-dropdown-trigger" onclick="toggleUserDropdown(event)">
        <span class="user-name">${escapeHtml(userName)}</span>
        <span class="dropdown-arrow">▼</span>
      </button>
      <div class="user-dropdown-menu">
        <a href="/app/user-profile.html">Profile</a>
        <div class="divider"></div>
        <a href="#" onclick="openHelpModal(event)">Help</a>
        <a href="#" onclick="openRecommendModal(event)">Recommend GigsFill</a>
        ${inviteLink}
        <div class="divider"></div>
        <a href="#" onclick="userDropdownSignOut(event)">Sign Out</a>
      </div>
    `;
    
    // Store user info globally for modals
    window._currentUserInfo = { id: user.id, name: userName, email: user.email || '', venues: userVenues };
    
    // Append to header actions
    headerActions.appendChild(dropdown);
    
    // Close dropdown when clicking outside (only add once)
    if (!window._userDropdownClickHandlerAdded) {
      document.addEventListener('click', (e) => {
        const dropdown = document.querySelector('.user-dropdown');
        if (dropdown && !dropdown.contains(e.target)) {
          dropdown.classList.remove('open');
        }
      });
      window._userDropdownClickHandlerAdded = true;
    }
    
  } catch (error) {
    console.error('Error initializing user dropdown:', error);
  }
}

/**
 * Toggle dropdown open/closed
 */
function toggleUserDropdown(event) {
  event.stopPropagation();
  const dropdown = event.target.closest('.user-dropdown');
  if (dropdown) {
    dropdown.classList.toggle('open');
  }
}

/**
 * Handle sign out
 */
async function userDropdownSignOut(event) {
  event.preventDefault();
  try {
    await fetch('/api/logout', { method: 'POST', credentials: 'include' });
  } catch (e) {
    // Ignore errors
  }
  window.location.href = '/';
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => { initUserDropdown(); injectGlobalModals(); });
} else {
  initUserDropdown();
  injectGlobalModals();
}

/**
 * Inject Help and Recommend modal HTML + styles into page
 */
function injectGlobalModals() {
  if (document.getElementById('gf-global-modals')) return;
  
  // Styles
  const modalStyles = document.createElement('style');
  modalStyles.textContent = `
    .gf-modal-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.6);
      z-index: 10000;
      align-items: center;
      justify-content: center;
    }
    .gf-modal-overlay.open {
      display: flex;
    }
    .gf-modal {
      background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%);
      border: 1px solid rgba(6,182,212,0.3);
      border-radius: 12px;
      width: 90%;
      max-width: 480px;
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 20px 60px rgba(0,0,0,0.5);
    }
    .gf-modal-header {
      padding: 20px 24px 16px;
      border-bottom: 1px solid rgba(255,255,255,0.1);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .gf-modal-header h2 {
      margin: 0;
      font-size: 1rem;
      font-weight: 700;
      color: #06b6d4;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .gf-modal-close {
      background: none;
      border: none;
      color: #9ca3af;
      font-size: 1.2rem;
      cursor: pointer;
      padding: 4px 8px;
    }
    .gf-modal-close:hover { color: #e5e5e5; }
    .gf-modal-body {
      padding: 20px 24px 24px;
    }
    .gf-modal-body label {
      display: block;
      font-size: 0.75rem;
      font-weight: 600;
      color: #9ca3af;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 6px;
    }
    .gf-modal-body input,
    .gf-modal-body select,
    .gf-modal-body textarea {
      width: 100%;
      padding: 10px 12px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.15);
      border-radius: 6px;
      color: #e5e5e5;
      font: 0.9rem/1.5 'Inter', -apple-system, sans-serif;
      margin-bottom: 16px;
      box-sizing: border-box;
    }
    .gf-modal-body textarea {
      resize: vertical;
      min-height: 80px;
    }
    .gf-modal-body input:focus,
    .gf-modal-body select:focus,
    .gf-modal-body textarea:focus {
      outline: none;
      border-color: rgba(6,182,212,0.5);
    }
    .gf-modal-body select option {
      background: #151b28;
      color: #e5e5e5;
    }
    .gf-modal-actions {
      display: flex;
      gap: 12px;
      justify-content: flex-end;
      margin-top: 8px;
    }
    .gf-btn {
      padding: 8px 20px;
      border-radius: 6px;
      font: 600 0.85rem 'Inter', sans-serif;
      cursor: pointer;
      border: none;
      transition: all 0.2s;
    }
    .gf-btn-primary {
      background: #06b6d4;
      color: #fff;
    }
    .gf-btn-primary:hover { background: #0891b2; }
    .gf-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
    .gf-btn-ghost {
      background: transparent;
      color: #9ca3af;
      border: 1px solid rgba(255,255,255,0.15);
    }
    .gf-btn-ghost:hover { color: #e5e5e5; border-color: rgba(255,255,255,0.3); }
    .gf-modal-status {
      text-align: center;
      font-size: 0.8rem;
      margin-top: 12px;
      min-height: 20px;
    }
  `;
  document.head.appendChild(modalStyles);
  
  // Modal HTML
  const container = document.createElement('div');
  container.id = 'gf-global-modals';
  container.innerHTML = `
    <!-- HELP / SUPPORT MODAL -->
    <div class="gf-modal-overlay" id="helpModal" onclick="if(event.target===this)closeHelpModal()">
      <div class="gf-modal">
        <div class="gf-modal-header">
          <h2>Help & Support</h2>
          <button class="gf-modal-close" onclick="closeHelpModal()">&times;</button>
        </div>
        <div class="gf-modal-body">
          <p style="color:#9ca3af;font-size:0.85rem;margin:0 0 20px;line-height:1.5;">Have an issue or need help? Fill out this form and our team will get back to you.</p>
          
          <label for="helpCategory">Category</label>
          <select id="helpCategory">
            <option value="">Select a category...</option>
            <option value="Payment Issue">Payment Issue</option>
            <option value="Booking Problem">Booking Problem</option>
            <option value="Technical Issue">Technical Issue</option>
            <option value="Account Issue">Account Issue</option>
            <option value="Feedback">Feedback / Suggestion</option>
            <option value="Other">Other</option>
          </select>
          
          <label for="helpSubject">Subject</label>
          <input type="text" id="helpSubject" placeholder="Brief summary of your issue" maxlength="200">
          
          <label for="helpDescription">Description</label>
          <textarea id="helpDescription" rows="5" placeholder="Please describe your issue in detail..." maxlength="5000"></textarea>
          
          <div class="gf-modal-actions">
            <button class="gf-btn gf-btn-ghost" onclick="closeHelpModal()">Cancel</button>
            <button class="gf-btn gf-btn-primary" id="helpSubmitBtn" onclick="submitHelpTicket()">Submit</button>
          </div>
          <div class="gf-modal-status" id="helpStatus"></div>
        </div>
      </div>
    </div>

    <!-- RECOMMEND GIGSFILL MODAL -->
    <div class="gf-modal-overlay" id="recommendModal" onclick="if(event.target===this)closeRecommendModal()">
      <div class="gf-modal">
        <div class="gf-modal-header">
          <h2>Recommend GigsFill</h2>
          <button class="gf-modal-close" onclick="closeRecommendModal()">&times;</button>
        </div>
        <div class="gf-modal-body">
          <p style="color:#9ca3af;font-size:0.85rem;margin:0 0 20px;line-height:1.5;">Know an artist or venue that would love GigsFill? Send them an invite!</p>
          
          <label for="recName">Their Name <span style="font-weight:400;text-transform:none;color:#6b7280;">(optional)</span></label>
          <input type="text" id="recName" placeholder="e.g. John Smith" maxlength="100">
          
          <label for="recEmail">Their Email</label>
          <input type="email" id="recEmail" placeholder="friend@email.com">
          
          <label for="recMessage">Personal Message <span style="font-weight:400;text-transform:none;color:#6b7280;">(optional)</span></label>
          <textarea id="recMessage" rows="3" placeholder="Hey, you should check this out..." maxlength="1000"></textarea>
          
          <div class="gf-modal-actions">
            <button class="gf-btn gf-btn-ghost" onclick="closeRecommendModal()">Cancel</button>
            <button class="gf-btn gf-btn-primary" id="recSubmitBtn" onclick="submitRecommendation()">Send Recommendation</button>
          </div>
          <div class="gf-modal-status" id="recStatus"></div>
        </div>
      </div>
    </div>

    <!-- INVITE ARTISTS MODAL -->
    <div class="gf-modal-overlay" id="inviteArtistsModal" onclick="if(event.target===this)closeInviteArtistsModal()">
      <div class="gf-modal" style="max-width:540px;">
        <div class="gf-modal-header">
          <h2>Invite Artists to GigsFill</h2>
          <button class="gf-modal-close" onclick="closeInviteArtistsModal()">&times;</button>
        </div>
        <div class="gf-modal-body">
          <p style="color:#9ca3af;font-size:0.85rem;margin:0 0 16px;line-height:1.5;">
            Invite artists you work with to join GigsFill. They'll receive an email letting them know your venue is using GigsFill for booking.
          </p>

          <div id="invVenueSelectWrap" style="display:none;margin-bottom:16px;">
            <label for="invVenueSelect">Sending from Venue</label>
            <select id="invVenueSelect" style="width:100%;"></select>
          </div>

          <label>Send as</label>
          <div style="display:flex;gap:12px;margin-bottom:16px;">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:0.85rem;color:#d1d5db;font-weight:400;text-transform:none;letter-spacing:0;">
              <input type="radio" name="invSendAs" value="venue" checked style="accent-color:#06b6d4;"> <span id="invVenueNameLabel">Venue name</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:0.85rem;color:#d1d5db;font-weight:400;text-transform:none;letter-spacing:0;">
              <input type="radio" name="invSendAs" value="personal" style="accent-color:#06b6d4;"> <span id="invPersonalNameLabel">Your name</span>
            </label>
          </div>

          <label for="invEmails">Email Addresses</label>
          <textarea id="invEmails" rows="4" placeholder="Enter email addresses separated by commas, spaces, or one per line&#10;&#10;artist1@email.com, artist2@email.com&#10;artist3@email.com" style="font-size:0.85rem;"></textarea>
          <div style="color:#6b7280;font-size:0.75rem;margin-top:4px;" id="invEmailCount">0 emails entered</div>

          <label for="invMessage" style="margin-top:12px;">Personal Message <span style="font-weight:400;text-transform:none;color:#6b7280;">(optional)</span></label>
          <textarea id="invMessage" rows="3" placeholder="Enter a personal message to your artists here..." maxlength="1000" style="font-size:0.85rem;"></textarea>

          <div class="gf-modal-actions">
            <button class="gf-btn gf-btn-ghost" onclick="closeInviteArtistsModal()">Cancel</button>
            <button class="gf-btn gf-btn-primary" id="invSubmitBtn" onclick="submitArtistInvitations()">Send Invitations</button>
          </div>
          <div class="gf-modal-status" id="invStatus"></div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(container);
}

// ===== HELP MODAL =====
function openHelpModal(event) {
  if (event) event.preventDefault();
  document.querySelector('.user-dropdown')?.classList.remove('open');
  document.getElementById('helpModal').classList.add('open');
  document.getElementById('helpCategory').value = '';
  document.getElementById('helpSubject').value = '';
  document.getElementById('helpDescription').value = '';
  document.getElementById('helpStatus').textContent = '';
  document.getElementById('helpSubmitBtn').disabled = false;
  document.getElementById('helpSubmitBtn').textContent = 'Submit';
}

function closeHelpModal() {
  document.getElementById('helpModal').classList.remove('open');
}

async function submitHelpTicket() {
  const category = document.getElementById('helpCategory').value;
  const subject = document.getElementById('helpSubject').value.trim();
  const description = document.getElementById('helpDescription').value.trim();
  const status = document.getElementById('helpStatus');
  const btn = document.getElementById('helpSubmitBtn');
  
  if (!category || !subject || !description) {
    status.textContent = 'Please fill in all fields.';
    status.style.color = '#ef4444';
    return;
  }
  
  btn.disabled = true;
  btn.textContent = 'Submitting...';
  status.textContent = '';
  
  const userInfo = window._currentUserInfo || {};
  
  try {
    const response = await fetch('/api/support/ticket', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        category,
        subject,
        description,
        user_id: userInfo.id,
        user_email: userInfo.email,
        user_name: userInfo.name
      })
    });
    
    if (!response.ok) throw new Error('Failed to submit');
    
    status.textContent = '✓ Ticket submitted! We\'ll get back to you soon.';
    status.style.color = '#22c55e';
    btn.textContent = 'OK';
    btn.disabled = false;
    btn.onclick = () => closeHelpModal();
  } catch (error) {
    console.error('Error submitting help ticket:', error);
    status.textContent = 'Failed to submit. Please try again.';
    status.style.color = '#ef4444';
    btn.disabled = false;
    btn.textContent = 'Submit';
  }
}

// ===== RECOMMEND MODAL =====
function openRecommendModal(event) {
  if (event) event.preventDefault();
  document.querySelector('.user-dropdown')?.classList.remove('open');
  document.getElementById('recommendModal').classList.add('open');
  document.getElementById('recName').value = '';
  document.getElementById('recEmail').value = '';
  document.getElementById('recMessage').value = '';
  document.getElementById('recStatus').textContent = '';
  document.getElementById('recSubmitBtn').disabled = false;
  document.getElementById('recSubmitBtn').textContent = 'Send Recommendation';
}

function closeRecommendModal() {
  document.getElementById('recommendModal').classList.remove('open');
}

async function submitRecommendation() {
  const recipientName = document.getElementById('recName').value.trim();
  const recipientEmail = document.getElementById('recEmail').value.trim();
  const message = document.getElementById('recMessage').value.trim();
  const status = document.getElementById('recStatus');
  const btn = document.getElementById('recSubmitBtn');

  if (!recipientEmail || !recipientEmail.includes('@')) {
    status.textContent = 'Please enter a valid email address.';
    status.style.color = '#ef4444';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Sending...';
  status.textContent = '';

  // FIX (May 2026): point at the affiliate-aware endpoint so the user gets
  // affiliate credit if their friend signs up. The legacy /api/recommend
  // (in backend/main.py) doesn't include the user's affiliate code in the
  // signup link, so referrals from this header button were going uncredited.
  // Field names: 'message' (legacy) → 'personal_note' (affiliate API).
  // user_id / user_name are not needed — the affiliate endpoint uses session auth.
  try {
    const response = await fetch('/api/affiliate/recommend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        recipient_email: recipientEmail,
        recipient_name: recipientName,
        personal_note: message
      })
    });

    let data = null;
    try { data = await response.json(); } catch (_) { /* non-JSON response */ }

    if (!response.ok) {
      // HTTP error (e.g. 400 "No affiliate code assigned") — show server detail if any
      const detail = (data && data.detail) || 'Failed to send. Please try again.';
      status.textContent = detail;
      status.style.color = '#ef4444';
      btn.disabled = false;
      btn.textContent = 'Send Recommendation';
      return;
    }

    if (data && data.already_claimed) {
      status.textContent = 'That email was already recommended by someone else.';
      status.style.color = '#f59e0b';
      btn.disabled = false;
      btn.textContent = 'Send Recommendation';
      return;
    }

    if (data && data.ok === false) {
      status.textContent = data.detail || 'Failed to send. Please try again.';
      status.style.color = '#ef4444';
      btn.disabled = false;
      btn.textContent = 'Send Recommendation';
      return;
    }

    status.textContent = '✓ Recommendation sent!';
    status.style.color = '#22c55e';
    btn.textContent = 'OK';
    btn.disabled = false;
    btn.onclick = () => closeRecommendModal();
  } catch (error) {
    console.error('Error sending recommendation:', error);
    status.textContent = 'Failed to send. Please try again.';
    status.style.color = '#ef4444';
    btn.disabled = false;
    btn.textContent = 'Send Recommendation';
  }
}


// ===== INVITE ARTISTS MODAL =====

function _parseInviteEmails() {
  const raw = (document.getElementById('invEmails')?.value || '').trim();
  if (!raw) return [];
  const list = raw.split(/[,;\s\n]+/).filter(e => {
    e = e.trim();
    return e && e.includes('@') && e.split('@')[1].includes('.');
  });
  // De-duplicate (case-insensitive)
  const seen = {};
  return list.filter(e => {
    const lc = e.toLowerCase();
    if (seen[lc]) return false;
    seen[lc] = true;
    return true;
  });
}

function _updateInviteEmailCount() {
  const emails = _parseInviteEmails();
  const el = document.getElementById('invEmailCount');
  if (el) {
    const n = emails.length;
    el.textContent = n === 0 ? '0 emails entered' : n === 1 ? '1 email entered' : n + ' emails entered';
    el.style.color = n > 50 ? '#ef4444' : '#6b7280';
  }
}

function openInviteArtistsModal(event) {
  if (event) event.preventDefault();
  document.querySelector('.user-dropdown')?.classList.remove('open');
  
  const info = window._currentUserInfo || {};
  const venues = info.venues || [];
  
  // Populate venue selector
  const wrap = document.getElementById('invVenueSelectWrap');
  const sel = document.getElementById('invVenueSelect');
  if (venues.length > 1 && sel) {
    sel.innerHTML = venues.map(v => '<option value="' + v.id + '">' + escapeHtml(v.venue_name || v.name || 'Venue') + '</option>').join('');
    wrap.style.display = '';
    sel.onchange = function() {
      const opt = sel.options[sel.selectedIndex];
      document.getElementById('invVenueNameLabel').textContent = opt.textContent;
    };
  } else if (sel && venues.length === 1) {
    sel.innerHTML = '<option value="' + venues[0].id + '">' + escapeHtml(venues[0].venue_name || venues[0].name || 'Venue') + '</option>';
    wrap.style.display = 'none';
  }
  
  // Set labels
  if (venues.length > 0) {
    document.getElementById('invVenueNameLabel').textContent = venues[0].venue_name || venues[0].name || 'Venue name';
  }
  document.getElementById('invPersonalNameLabel').textContent = info.name || 'Your name';
  
  // Reset fields
  document.getElementById('invEmails').value = '';
  document.getElementById('invMessage').value = '';
  document.getElementById('invStatus').textContent = '';
  document.getElementById('invSubmitBtn').disabled = false;
  document.getElementById('invSubmitBtn').textContent = 'Send Invitations';
  _updateInviteEmailCount();
  
  // Live email count
  const emailsEl = document.getElementById('invEmails');
  emailsEl.oninput = _updateInviteEmailCount;
  
  document.getElementById('inviteArtistsModal').classList.add('open');
}

function closeInviteArtistsModal() {
  document.getElementById('inviteArtistsModal').classList.remove('open');
}

async function submitArtistInvitations() {
  const emails = _parseInviteEmails();
  const message = (document.getElementById('invMessage')?.value || '').trim();
  const status = document.getElementById('invStatus');
  const btn = document.getElementById('invSubmitBtn');
  const sel = document.getElementById('invVenueSelect');
  const sendAs = document.querySelector('input[name="invSendAs"]:checked')?.value || 'venue';
  
  if (emails.length === 0) {
    status.textContent = 'Please enter at least one valid email address.';
    status.style.color = '#ef4444';
    return;
  }
  if (emails.length > 50) {
    status.textContent = 'Maximum 50 emails at a time. Please send in batches.';
    status.style.color = '#ef4444';
    return;
  }
  
  const venueId = sel ? sel.value : '';
  if (!venueId) {
    status.textContent = 'No venue selected.';
    status.style.color = '#ef4444';
    return;
  }
  
  const info = window._currentUserInfo || {};
  
  btn.disabled = true;
  btn.textContent = 'Sending ' + emails.length + ' invitation' + (emails.length > 1 ? 's' : '') + '...';
  status.textContent = '';
  
  try {
    const response = await fetch('/api/venues/' + venueId + '/invite-artists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        emails: emails.join(','),
        message: message,
        user_id: info.id,
        inviter_name: info.name,
        send_as: sendAs
      })
    });
    
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to send');
    }
    
    const result = await response.json();
    
    let msg = '✓ ' + result.message;
    status.textContent = msg;
    status.style.color = '#22c55e';
    btn.textContent = 'Sent!';
    
    // Refresh invited artists tracker if on venue email center page
    if (typeof loadInvitedArtists === 'function') {
      loadInvitedArtists(venueId);
    }
    
    btn.textContent = 'OK';
    btn.disabled = false;
    btn.onclick = () => closeInviteArtistsModal();
  } catch (error) {
    console.error('Error inviting artists:', error);
    status.textContent = error.message || 'Failed to send invitations. Please try again.';
    status.style.color = '#ef4444';
    btn.disabled = false;
    btn.textContent = 'Send Invitations';
  }
}
