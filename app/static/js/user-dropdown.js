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
    
    // Build dropdown links — Invite Artists only on venue pages.
    // (Jun 2026) "My Invites" standalone page removed: the invitation
    // status tracking now lives inside the Email Center → Invite Artists
    // sub-tab in venue-create-gigs.html, where the venue user is already
    // managing invites in context. Keeping a separate top-level dropdown
    // entry just to open a list page was duplicate surface area.
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
        <a href="#" onclick="openFeedbackModal(event)">Feedback</a>
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
// Exposed so pages that mutate the user's profile (e.g. user-profile.js
// after a successful Save Changes) can trigger the header re-render
// without a full page reload. Jul 22 2026.
window.initUserDropdown = initUserDropdown;
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

    <!-- FEEDBACK MODAL -->
    <div class="gf-modal-overlay" id="feedbackModal" onclick="if(event.target===this)closeFeedbackModal()">
      <div class="gf-modal">
        <div class="gf-modal-header">
          <h2>Share Feedback</h2>
          <button class="gf-modal-close" onclick="closeFeedbackModal()">&times;</button>
        </div>
        <div class="gf-modal-body">
          <p style="color:#9ca3af;font-size:0.85rem;margin:0 0 20px;line-height:1.5;">GigsFill is new and growing — your suggestions shape what gets built next. Let us know what's working, what's confusing, or what you wish the site did.</p>

          <label for="feedbackSubject">Subject</label>
          <input type="text" id="feedbackSubject" placeholder="Brief headline for your feedback" maxlength="200">

          <label for="feedbackDescription">Description</label>
          <textarea id="feedbackDescription" rows="6" placeholder="Tell us what's on your mind. Suggestions, frustrations, what's working well — all useful." maxlength="5000"></textarea>

          <div class="gf-modal-actions">
            <button class="gf-btn gf-btn-ghost" onclick="closeFeedbackModal()">Cancel</button>
            <button class="gf-btn gf-btn-primary" id="feedbackSubmitBtn" onclick="submitFeedback()">Send Feedback</button>
          </div>
          <div class="gf-modal-status" id="feedbackStatus"></div>
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
          <p style="color:#9ca3af;font-size:0.85rem;margin:0 0 10px;line-height:1.5;">Know an artist or venue that would love GigsFill? Send them an invite!</p>
          <div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.30);border-radius:8px;padding:10px 14px;margin:0 0 20px;color:#a7f3d0;font-size:0.78rem;line-height:1.5;">
            <strong style="color:#10b981;">💰 Affiliate Perk:</strong> If a recommended <strong>venue</strong> signs up using your link, they're automatically attached to your affiliate account and you'll earn a commission on <strong>every gig they book</strong>. Track your referrals and earnings on the Affiliate tab of your profile.
            <div style="color:#6b7280;margin-top:4px;font-size:0.72rem;">(Artist signups don't trigger affiliate credit — see <a href="/app/legal.html#affiliate" style="color:#06b6d4;" target="_blank">Terms</a>.)</div>
          </div>

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

    <!-- INVITE ARTISTS MODAL (part 10p: multi-venue) -->
    <div class="gf-modal-overlay" id="inviteArtistsModal" onclick="if(event.target===this)closeInviteArtistsModal()">
      <div class="gf-modal" style="max-width:600px;">
        <div class="gf-modal-header">
          <h2>Invite Artists to GigsFill</h2>
          <button class="gf-modal-close" onclick="closeInviteArtistsModal()">&times;</button>
        </div>
        <div class="gf-modal-body">
          <p style="color:#9ca3af;font-size:0.85rem;margin:0 0 16px;line-height:1.5;">
            Invite artists to join GigsFill. They'll get one email mentioning every venue you select below. After signup (or login, if they already have an account) they'll be asked whether to request preferred-artist status at the venues you picked &mdash; one click instead of three.
          </p>

          <label style="margin-bottom:8px;">Inviting from <span id="invVenuesCount" style="color:#9ca3af;font-weight:400;text-transform:none;letter-spacing:0;font-size:0.78rem;"></span></label>
          <div id="invVenuesList" style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:6px;padding:10px 12px;max-height:160px;overflow-y:auto;margin-bottom:4px;">
            <div style="color:#6b7280;font-size:0.8rem;">Loading your venues&hellip;</div>
          </div>
          <div style="display:flex;gap:8px;margin-bottom:14px;">
            <button type="button" onclick="_invToggleAllVenues(true)" style="background:transparent;border:1px solid rgba(124,107,255,0.4);color:#a78bfa;font-size:0.7rem;padding:3px 10px;border-radius:4px;cursor:pointer;">Select all</button>
            <button type="button" onclick="_invToggleAllVenues(false)" style="background:transparent;border:1px solid rgba(255,255,255,0.15);color:#9ca3af;font-size:0.7rem;padding:3px 10px;border-radius:4px;cursor:pointer;">Select none</button>
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
    const payload = {
      category, subject, description,
      user_id: userInfo.id,
      user_email: userInfo.email,
      user_name: userInfo.name
    };
    // Use apiPostSafe so users see the real backend `detail` message
    // (rate limit, missing field, etc.) instead of a generic "Failed".
    if (typeof window.apiPostSafe === 'function') {
      await window.apiPostSafe('/api/support/ticket', payload);
    } else {
      const response = await fetch('/api/support/ticket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        let detail = `Failed to submit (${response.status})`;
        try { const j = await response.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
        throw new Error(detail);
      }
    }

    status.textContent = '✓ Ticket submitted! We\'ll get back to you soon.';
    status.style.color = '#22c55e';
    btn.textContent = 'OK';
    btn.disabled = false;
    btn.onclick = () => closeHelpModal();
    // Auto-close after 3s so the user isn't forced to click OK — they
    // still can if they want to dismiss sooner.
    setTimeout(function () {
      var m = document.getElementById('helpModal');
      if (m && m.classList.contains('open')) closeHelpModal();
    }, 3000);
  } catch (error) {
    console.error('Error submitting help ticket:', error);
    status.textContent = (error && error.message) || 'Failed to submit. Please try again.';
    status.style.color = '#ef4444';
    btn.disabled = false;
    btn.textContent = 'Submit';
  }
}

// ===== FEEDBACK MODAL =====
// Same backend as Help (POST /api/support/ticket) but with category
// hardcoded to "Feedback" so the existing admin Support tab + email
// routing + ticket lifecycle just work — no backend or DB changes.
function openFeedbackModal(event) {
  if (event) event.preventDefault();
  document.querySelector('.user-dropdown')?.classList.remove('open');
  document.getElementById('feedbackModal').classList.add('open');
  document.getElementById('feedbackSubject').value = '';
  document.getElementById('feedbackDescription').value = '';
  document.getElementById('feedbackStatus').textContent = '';
  document.getElementById('feedbackSubmitBtn').disabled = false;
  document.getElementById('feedbackSubmitBtn').textContent = 'Send Feedback';
}

function closeFeedbackModal() {
  document.getElementById('feedbackModal').classList.remove('open');
}

async function submitFeedback() {
  const subject = document.getElementById('feedbackSubject').value.trim();
  const description = document.getElementById('feedbackDescription').value.trim();
  const status = document.getElementById('feedbackStatus');
  const btn = document.getElementById('feedbackSubmitBtn');

  if (!subject || !description) {
    status.textContent = 'Please fill in both fields.';
    status.style.color = '#ef4444';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Sending...';
  status.textContent = '';

  const userInfo = window._currentUserInfo || {};

  try {
    const payload = {
      category: 'Feedback', subject, description,
      user_id: userInfo.id,
      user_email: userInfo.email,
      user_name: userInfo.name
    };
    if (typeof window.apiPostSafe === 'function') {
      await window.apiPostSafe('/api/support/ticket', payload);
    } else {
      const response = await fetch('/api/support/ticket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        let detail = `Failed to send (${response.status})`;
        try { const j = await response.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
        throw new Error(detail);
      }
    }

    status.textContent = '✓ Thanks! Your feedback is in front of us.';
    status.style.color = '#22c55e';
    btn.textContent = 'OK';
    btn.disabled = false;
    btn.onclick = () => closeFeedbackModal();
    // Auto-close after 3s (parity with Help modal).
    setTimeout(function () {
      var m = document.getElementById('feedbackModal');
      if (m && m.classList.contains('open')) closeFeedbackModal();
    }, 3000);
  } catch (error) {
    console.error('Error submitting feedback:', error);
    status.textContent = (error && error.message) || 'Failed to send. Please try again.';
    status.style.color = '#ef4444';
    btn.disabled = false;
    btn.textContent = 'Send Feedback';
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
    // Tell the Affiliate dashboard's "Sent Recommendations" table to
    // refresh if the user is currently viewing it. user-affiliate.js
    // listens for this event and re-fetches GET /api/affiliate/my-emails.
    try { window.dispatchEvent(new CustomEvent('affRecommendSent')); } catch (_) {}
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
    // Amber (not red) over 50 — part 10p removed the hard limit. The color
    // just hints "that's a lot, double-check" without implying a cap.
    el.style.color = n > 50 ? '#f59e0b' : '#6b7280';
  }
}

function _invToggleAllVenues(checked) {
  document.querySelectorAll('#invVenuesList input[type="checkbox"]').forEach(cb => { cb.checked = checked; });
  _updateInviteVenueCount();
}

function _updateInviteVenueCount() {
  const el = document.getElementById('invVenuesCount');
  if (!el) return;
  const total = document.querySelectorAll('#invVenuesList input[type="checkbox"]').length;
  const sel = document.querySelectorAll('#invVenuesList input[type="checkbox"]:checked').length;
  el.textContent = '(' + sel + ' of ' + total + ' selected)';
}

function openInviteArtistsModal(event) {
  if (event) event.preventDefault();
  document.querySelector('.user-dropdown')?.classList.remove('open');

  const list = document.getElementById('invVenuesList');
  list.innerHTML = '<div style="color:#6b7280;font-size:0.8rem;">Loading your venues&hellip;</div>';

  // Reset rest of the form
  document.getElementById('invEmails').value = '';
  document.getElementById('invMessage').value = '';
  document.getElementById('invStatus').textContent = '';
  document.getElementById('invSubmitBtn').disabled = false;
  document.getElementById('invSubmitBtn').textContent = 'Send Invitations';
  document.getElementById('invSubmitBtn').onclick = submitArtistInvitations;
  _updateInviteEmailCount();
  document.getElementById('invEmails').oninput = _updateInviteEmailCount;

  document.getElementById('inviteArtistsModal').classList.add('open');

  // Fetch venues the current user controls — owner OR member via entity_users.
  fetch('/api/my/venues', { credentials: 'include' })
    .then(r => r.ok ? r.json() : Promise.reject(new Error('Could not load your venues')))
    .then(venues => {
      if (!Array.isArray(venues) || venues.length === 0) {
        list.innerHTML = '<div style="color:#f59e0b;font-size:0.8rem;">You don\'t control any venues yet, so there\'s no venue context to invite from. Create or join a venue first.</div>';
        document.getElementById('invSubmitBtn').disabled = true;
        return;
      }
      list.innerHTML = venues.map(v => {
        const vid = parseInt(v.id, 10) || 0;
        const name = escapeHtml(v.venue_name || v.name || 'Venue');
        const loc = (v.city || v.state) ? ' <span style="color:#6b7280;font-size:0.75rem;">' + escapeHtml([v.city, v.state].filter(Boolean).join(', ')) + '</span>' : '';
        return '<label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:4px 0;font-size:0.85rem;color:#e5e7eb;font-weight:400;text-transform:none;letter-spacing:0;">' +
          '<input type="checkbox" class="inv-venue-cb" value="' + vid + '" checked onchange="_updateInviteVenueCount()" style="accent-color:#06b6d4;width:14px;height:14px;cursor:pointer;">' +
          '<span><strong style="color:#fff;">' + name + '</strong>' + loc + '</span>' +
          '</label>';
      }).join('');
      _updateInviteVenueCount();
    })
    .catch(err => {
      list.innerHTML = '<div style="color:#ef4444;font-size:0.8rem;">' + escapeHtml(err.message || 'Could not load your venues.') + '</div>';
    });
}

function closeInviteArtistsModal() {
  document.getElementById('inviteArtistsModal').classList.remove('open');
}

async function submitArtistInvitations() {
  const emails = _parseInviteEmails();
  const message = (document.getElementById('invMessage')?.value || '').trim();
  const status = document.getElementById('invStatus');
  const btn = document.getElementById('invSubmitBtn');
  const venueIds = Array.from(document.querySelectorAll('#invVenuesList input.inv-venue-cb:checked'))
    .map(cb => parseInt(cb.value, 10)).filter(n => n > 0);

  if (emails.length === 0) {
    status.textContent = 'Please enter at least one valid email address.';
    status.style.color = '#ef4444';
    return;
  }
  if (venueIds.length === 0) {
    status.textContent = 'Select at least one venue to invite from.';
    status.style.color = '#ef4444';
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Sending ' + emails.length + ' invitation' + (emails.length > 1 ? 's' : '') + '...';
  status.textContent = '';

  try {
    const response = await fetch('/api/me/invite-artists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        emails: emails.join(','),
        venue_ids: venueIds,
        message: message,
      })
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || ('Failed to send (HTTP ' + response.status + ')'));
    }

    const result = await response.json();

    // Build a friendly summary
    const parts = [];
    if (result.sent_count > 0) parts.push('✓ ' + result.sent_count + ' invitation' + (result.sent_count === 1 ? '' : 's') + ' sent across ' + result.venues + ' venue' + (result.venues === 1 ? '' : 's'));
    if (result.bounced_count > 0) parts.push(result.bounced_count + ' bounced');
    if (result.skipped_already_pending_count > 0) parts.push(result.skipped_already_pending_count + ' skipped (already invited within 24h)');
    if (result.invalid_count > 0) parts.push(result.invalid_count + ' invalid email' + (result.invalid_count === 1 ? '' : 's'));
    status.innerHTML = parts.join('<br>');
    status.style.color = result.sent_count > 0 ? '#22c55e' : '#f59e0b';

    btn.textContent = 'Done';
    btn.disabled = false;
    btn.onclick = () => closeInviteArtistsModal();

    // Refresh any per-venue tracker if visible
    if (typeof loadInvitedArtists === 'function') {
      venueIds.forEach(vid => { try { loadInvitedArtists(vid); } catch(_) {} });
    }
  } catch (error) {
    console.error('Error inviting artists:', error);
    status.textContent = error.message || 'Failed to send invitations. Please try again.';
    status.style.color = '#ef4444';
    btn.disabled = false;
    btn.textContent = 'Send Invitations';
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// Part 10p: Pending-artist-invite popup
// ─────────────────────────────────────────────────────────────────────────────
// On every authenticated page load, ask the backend whether the current user
// has an unconsumed artist-invitation token. If yes, show a modal listing the
// inviter's venues with pre-checked checkboxes for venues where the user isn't
// already preferred. Skipped or accepted → token row stamped `preferred_requested_at`
// so the popup never reappears for that token.
(function _pendingArtistInvitePopup() {
  // Don't fire on signup / login / static pages — those have their own banners.
  const skip = ['/app/index.html', '/app/signup-new.html', '/app/verify-email.html', '/app/legal.html'];
  if (skip.some(p => window.location.pathname.endsWith(p))) return;
  // Don't fire twice per page load (e.g. if the user-dropdown script gets included twice)
  if (window._pendingInviteHookFired) return;
  window._pendingInviteHookFired = true;

  function _showPopup(inv) {
    // Build the modal HTML
    const inviter = String(inv.inviter_name || 'A venue').replace(/</g,'&lt;');
    const venues = inv.venues || [];
    const eligible = venues.filter(v => !v.already_preferred && !['pending','denied','revoked','banned'].includes(v.preferred_status || ''));
    // If every venue is already preferred → friendly "nothing to do" + dismiss
    if (eligible.length === 0) {
      const msg = venues.length === 0
        ? 'Nothing to request.'
        : 'You\'re already a preferred artist (or have a pending request) at every venue ' + inviter + ' invited you for — no action needed.';
      _renderTokenPopup({
        title: '🎶 You\'re all set!',
        bodyHtml: '<p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:#d1d5db;">' + msg + '</p>',
        primaryLabel: 'Got it',
        showSecondary: false,
        onPrimary: () => _dismissToken(inv.token).then(_closePopup),
      });
      return;
    }
    const rows = venues.map(v => {
      const vid = parseInt(v.venue_id, 10) || 0;
      const nameSafe = String(v.venue_name || 'Venue').replace(/</g,'&lt;');
      if (v.already_preferred) {
        return '<label style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:4px;background:rgba(34,197,94,0.06);font-size:0.85rem;color:#86efac;cursor:default;">' +
          '<span style="width:14px;text-align:center;">✓</span>' +
          '<span><strong>' + nameSafe + '</strong> <span style="color:#6b7280;font-size:0.75rem;">already preferred</span></span>' +
          '</label>';
      }
      if (['pending','denied','revoked'].includes(v.preferred_status || '')) {
        const stat = v.preferred_status === 'pending' ? 'request pending' : (v.preferred_status === 'denied' ? 'previously denied' : 'previously revoked');
        return '<label style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:4px;background:rgba(245,158,11,0.06);font-size:0.85rem;color:#fbbf24;cursor:default;">' +
          '<span style="width:14px;text-align:center;">!</span>' +
          '<span><strong>' + nameSafe + '</strong> <span style="color:#9ca3af;font-size:0.75rem;">' + stat + ' — skip</span></span>' +
          '</label>';
      }
      return '<label style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:4px;background:rgba(6,182,212,0.06);font-size:0.85rem;color:#e5e7eb;cursor:pointer;">' +
        '<input type="checkbox" class="_inv-pop-cb" value="' + vid + '" checked style="accent-color:#06b6d4;width:14px;height:14px;cursor:pointer;">' +
        '<span><strong>' + nameSafe + '</strong></span>' +
        '</label>';
    }).join('');
    _renderTokenPopup({
      title: '🎶 Request Preferred Status',
      bodyHtml:
        '<p style="margin:0 0 12px;font-size:14px;line-height:1.6;color:#d1d5db;">' +
        '<strong>' + inviter + '</strong> invited you to GigsFill. Want to request preferred-artist status at the venues below in one click?</p>' +
        '<div style="display:flex;flex-direction:column;gap:4px;max-height:240px;overflow-y:auto;margin-bottom:16px;">' + rows + '</div>' +
        '<p style="margin:0;font-size:11px;color:#6b7280;line-height:1.5;">Preferred artists can book gigs at a venue directly without venue approval. You can always request later from your profile.</p>',
      primaryLabel: 'Request Preferred Status',
      secondaryLabel: 'Maybe later',
      showSecondary: true,
      onPrimary: () => {
        const vids = Array.from(document.querySelectorAll('._inv-pop-cb:checked')).map(cb => parseInt(cb.value, 10)).filter(n => n > 0);
        if (vids.length === 0) { _dismissToken(inv.token).then(_closePopup); return; }
        const btn = document.getElementById('_invPopPrimary');
        if (btn) { btn.disabled = true; btn.textContent = 'Requesting…'; }
        fetch('/api/artist-invitations/' + encodeURIComponent(inv.token) + '/accept-preferred', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ venue_ids: vids })
        })
          .then(r => r.ok ? r.json() : r.json().then(b => Promise.reject(new Error(b.detail || 'Request failed'))))
          .then(res => {
            const parts = [];
            if ((res.requested || []).length > 0) parts.push('✓ Requested at ' + res.requested.length + ' venue' + (res.requested.length === 1 ? '' : 's'));
            if ((res.skipped_already_preferred || []).length > 0) parts.push((res.skipped_already_preferred.length) + ' already preferred');
            const msg = parts.join(' · ') || 'Done';
            _renderTokenPopup({
              title: '🎉 Done!',
              bodyHtml: '<p style="margin:0;font-size:14px;color:#86efac;">' + msg + '</p><p style="margin:12px 0 0;font-size:13px;color:#9ca3af;line-height:1.5;">Each venue will get a notification and email about your request.</p>',
              primaryLabel: 'OK',
              showSecondary: false,
              onPrimary: _closePopup,
            });
          })
          .catch(err => {
            if (btn) { btn.disabled = false; btn.textContent = 'Request Preferred Status'; }
            const errEl = document.getElementById('_invPopErr');
            if (errEl) { errEl.textContent = err.message || 'Request failed'; errEl.style.display = 'block'; }
          });
      },
      onSecondary: () => _dismissToken(inv.token).then(_closePopup),
    });
  }

  function _renderTokenPopup({ title, bodyHtml, primaryLabel, secondaryLabel, showSecondary, onPrimary, onSecondary }) {
    _closePopup();
    const overlay = document.createElement('div');
    overlay.id = '_inviteTokenOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:100000;display:flex;align-items:center;justify-content:center;padding:20px;';
    const card = document.createElement('div');
    card.style.cssText = 'background:#1a1a2e;border:1px solid rgba(124,107,255,0.3);border-radius:10px;padding:24px 28px;max-width:480px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5);';
    card.innerHTML =
      '<h2 style="margin:0 0 16px;font-size:1.1rem;font-weight:700;color:#fff;">' + title + '</h2>' +
      '<div>' + bodyHtml + '</div>' +
      '<div id="_invPopErr" style="display:none;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.4);color:#fca5a5;font-size:0.8rem;padding:8px 12px;border-radius:4px;margin-top:12px;"></div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:20px;">' +
        (showSecondary ? '<button id="_invPopSecondary" style="background:transparent;border:1px solid rgba(255,255,255,0.2);color:#d1d5db;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:0.85rem;">' + (secondaryLabel || 'Cancel') + '</button>' : '') +
        '<button id="_invPopPrimary" style="background:#06b6d4;border:none;color:#fff;padding:8px 18px;border-radius:6px;cursor:pointer;font-size:0.85rem;font-weight:600;">' + primaryLabel + '</button>' +
      '</div>';
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    document.getElementById('_invPopPrimary').onclick = onPrimary;
    if (showSecondary) document.getElementById('_invPopSecondary').onclick = onSecondary;
  }
  function _closePopup() {
    const el = document.getElementById('_inviteTokenOverlay');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  function _dismissToken(token) {
    return fetch('/api/artist-invitations/' + encodeURIComponent(token) + '/dismiss', {
      method: 'POST', credentials: 'include'
    }).catch(() => {});
  }

  // Kick off the check. Wait for the user-dropdown's auth load so we know
  // who the user is. If /api/me returns 401 (not logged in) this no-ops.
  function _checkPending() {
    // Don't fire for venue users — invitations are artist-only and the popup
    // doesn't apply. Use /api/me/pending-artist-invite which returns
    // {pending: false} for users without a matching invite anyway, but we
    // can short-circuit on the role too.
    fetch('/api/me/pending-artist-invite', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d || !d.pending || !d.token) return;
        return fetch('/api/artist-invitations/by-token/' + encodeURIComponent(d.token), { credentials: 'include' })
          .then(r => r.ok ? r.json() : null)
          .then(inv => { if (inv) _showPopup(inv); });
      })
      .catch(() => {});
  }
  // Defer a tick so the rest of the dropdown init can finish first.
  if (document.readyState === 'complete') {
    setTimeout(_checkPending, 300);
  } else {
    window.addEventListener('load', () => setTimeout(_checkPending, 300));
  }
})();
