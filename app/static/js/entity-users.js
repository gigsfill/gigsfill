/**
 * Entity Users Management
 * Shared functionality for managing users with access to Artists/Venues
 */

class EntityUsersManager {
  constructor(entityType, entityId, entityName) {
    this.entityType = entityType; // 'artist' or 'venue'
    this.entityId = entityId;
    this.entityName = entityName;
    this.users = [];
  }
  
  /**
   * Load users with access to this entity
   */
  async loadUsers() {
    try {
      if (window._artistAccessDenied) return [];
      const response = await fetch(`/api/entity-users/${this.entityType}/${this.entityId}`, {
        credentials: 'include'
      });
      
      if (!response.ok) {
        console.error('Failed to load users:', response.status);
        return [];
      }
      
      this.users = await response.json();
      return this.users;
    } catch (error) {
      console.error('Error loading users:', error);
      return [];
    }
  }
  
  /**
   * Render users list in container
   */
  renderUsersList(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    if (this.users.length === 0) {
      container.innerHTML = '<p style="color: var(--text-muted); text-align: center;">No users found.</p>';
      return;
    }
    
    // Column headers
    const headerHtml = `
      <div class="entity-users-header" style="display: grid; grid-template-columns: 120px 120px 1fr 140px 80px; gap: 16px; padding: 8px 16px; background: rgba(139, 92, 246, 0.1); border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 6px; margin-bottom: 8px;">
        <span style="font-size: 0.75rem; font-weight: 600; color: var(--text-gray); text-transform: uppercase; letter-spacing: 0.05em;">First Name</span>
        <span style="font-size: 0.75rem; font-weight: 600; color: var(--text-gray); text-transform: uppercase; letter-spacing: 0.05em;">Last Name</span>
        <span style="font-size: 0.75rem; font-weight: 600; color: var(--text-gray); text-transform: uppercase; letter-spacing: 0.05em;">Email</span>
        <span style="font-size: 0.75rem; font-weight: 600; color: var(--text-gray); text-transform: uppercase; letter-spacing: 0.05em;">Phone</span>
        <span></span>
      </div>
    `;
    
    // User rows
    const rowsHtml = this.users.map(user => {
      const isPending = user.role === 'pending';
      const isDeclined = user.role === 'declined';
      const isInvitation = isPending || isDeclined;
      const rowOpacity = isInvitation ? 'opacity: 0.7;' : '';
      const clickHandler = isInvitation ? `onclick="entityUsersManager.showReinviteModal(${user.invitation_id}, '${(user.email || '').replace(/'/g, "\\'")}')" style="cursor: pointer; ${rowOpacity}"` : `style="${rowOpacity}"`;
      
      let statusHtml = '';
      if (user.role === 'owner') {
        statusHtml = `<span style="font-size: 0.7rem; color: var(--cyan); text-transform: uppercase; font-weight: 600;">Owner</span>`;
      } else if (isPending) {
        statusHtml = `<span style="font-size: 0.7rem; color: #f59e0b; text-transform: uppercase; font-weight: 600;">Pending</span>`;
      } else if (isDeclined) {
        statusHtml = `<span style="font-size: 0.7rem; color: #ef4444; text-transform: uppercase; font-weight: 600;">Declined</span>`;
      } else {
        statusHtml = `
          <button class="btn" style="background: #dc3545; padding: 6px 12px; font-size: 0.75rem; text-transform: uppercase;" 
                  onclick="event.stopPropagation(); entityUsersManager.confirmRemoveUser(${user.user_id}, '${user.first_name} ${user.last_name}')">
            REMOVE
          </button>
        `;
      }
      
      return `
        <div class="entity-item" ${clickHandler}>
          <div style="display: grid; grid-template-columns: 120px 120px 1fr 140px 80px; gap: 16px; align-items: center; padding: 12px 16px;">
            <span style="color: ${isInvitation ? 'var(--text-gray)' : 'var(--text)'}; font-size: 0.875rem; font-style: ${isInvitation ? 'italic' : 'normal'};">${user.first_name || '-'}</span>
            <span style="color: ${isInvitation ? 'var(--text-gray)' : 'var(--text)'}; font-size: 0.875rem; font-style: ${isInvitation ? 'italic' : 'normal'};">${user.last_name || '-'}</span>
            <span style="color: var(--text-gray); font-size: 0.875rem; overflow: hidden; text-overflow: ellipsis;">${user.email || '-'}</span>
            <span style="color: var(--text-gray); font-size: 0.875rem;">${user.phone || '-'}</span>
            ${statusHtml}
          </div>
        </div>
      `;
    }).join('');
    
    container.innerHTML = headerHtml + rowsHtml;
  }
  
  /**
   * Update user count badge
   */
  updateBadge(badgeId) {
    const badge = document.getElementById(badgeId);
    if (badge) {
      const activeCount = this.users.filter(u => u.role !== 'pending' && u.role !== 'declined').length;
      badge.textContent = `(${activeCount})`;
    }
  }
  
  /**
   * Show confirmation modal before removing user
   */
  confirmRemoveUser(userId, userName) {
    // Create modal HTML - branded to match site
    const modalHtml = `
      <div class="modal-overlay" id="removeUserModal" style="position: fixed; inset: 0; background: rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center; z-index: 10000;">
        <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid #7c6bff; border-radius: 12px; padding: 2rem; max-width: 400px; text-align: center; box-shadow: 0 8px 32px rgba(124,107,255,0.4);">
          <div style="font-size: 2.5rem; margin-bottom: 1rem; color: #ef4444;">⚠️</div>
          <h2 style="color: #ffffff; margin: 0 0 0.75rem 0; font-size: 1.25rem;">Remove User Access</h2>
          <p style="color: #a1a1aa; margin: 0 0 1.5rem 0; font-size: 0.95rem; line-height: 1.5;">
            Are you sure you want to remove <strong style="color: var(--cyan);">${userName}</strong>'s access?
          </p>
          <div style="display: flex; gap: 12px; justify-content: center;">
            <button class="btn ghost" onclick="entityUsersManager.closeRemoveModal()">Cancel</button>
            <button class="btn" style="background: #ef4444; border-color: #ef4444;" onclick="entityUsersManager.removeUser(${userId})">Remove</button>
          </div>
        </div>
      </div>
    `;
    
    // Remove any existing modal
    const existingModal = document.getElementById('removeUserModal');
    if (existingModal) existingModal.remove();
    
    // Add modal to page
    document.body.insertAdjacentHTML('beforeend', modalHtml);
  }
  
  /**
   * Close remove user modal
   */
  closeRemoveModal() {
    const modal = document.getElementById('removeUserModal');
    if (modal) modal.remove();
  }
  
  /**
   * Remove a user's access
   */
  async removeUser(userId) {
    // Close modal if open
    this.closeRemoveModal();
    
    try {
      const response = await fetch(`/api/entity-users/${this.entityType}/${this.entityId}/remove/${userId}`, {
        method: 'DELETE',
        credentials: 'include'
      });
      
      if (response.ok) {
        const result = await response.json();
        
        // If user removed themselves, show message and redirect
        if (result.removed_self) {
          this.showResultModal('success', result.message, () => {
            window.location.href = '/app/user-profile.html';
          });
          return;
        }
        
        await this.loadUsers();
        this.renderUsersList('entityUsersList');
        this.updateBadge('usersBadge');
      } else {
        const error = await response.json();
        this.showResultModal('error', error.detail || 'Failed to remove user');
      }
    } catch (error) {
      console.error('Error removing user:', error);
      this.showResultModal('error', 'Failed to remove user');
    }
  }
  
  /**
   * Show branded result modal
   */
  showResultModal(type, message, onClose = null) {
    // Remove any existing result modal
    const existing = document.getElementById('entityResultModal');
    if (existing) existing.remove();
    
    const isSuccess = type === 'success';
    const isError = type === 'error';
    const icon = isSuccess ? '✓' : isError ? '✕' : 'ℹ';
    const iconColor = isSuccess ? '#22c55e' : isError ? '#ef4444' : '#5b8cff';
    const title = isSuccess ? 'Success!' : isError ? 'Error' : 'Notice';
    
    const modalHtml = `
      <div id="entityResultModal" style="position: fixed; inset: 0; background: rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center; z-index: 10000;">
        <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid #7c6bff; border-radius: 12px; padding: 2rem; max-width: 400px; text-align: center; box-shadow: 0 8px 32px rgba(124,107,255,0.4);">
          <div style="font-size: 3rem; margin-bottom: 1rem; color: ${iconColor};">${icon}</div>
          <h2 style="color: #ffffff; margin: 0 0 0.75rem 0; font-size: 1.25rem;">${title}</h2>
          <p style="color: #a1a1aa; margin: 0 0 1.5rem 0; font-size: 0.95rem; line-height: 1.5;">${message}</p>
          <button class="btn primary" style="min-width: 120px;" onclick="entityUsersManager.closeResultModal(${onClose ? 'true' : 'false'})">OK</button>
        </div>
      </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Store callback for later
    this._resultModalCallback = onClose;
  }
  
  /**
   * Close result modal
   */
  closeResultModal(hasCallback) {
    const modal = document.getElementById('entityResultModal');
    if (modal) modal.remove();
    
    if (hasCallback && this._resultModalCallback) {
      this._resultModalCallback();
    }
    this._resultModalCallback = null;
  }
  
  /**
   * Show re-invite confirmation modal
   */
  showReinviteModal(invitationId, email) {
    const existing = document.getElementById('reinviteModal');
    if (existing) existing.remove();
    
    const modalHtml = `
      <div id="reinviteModal" style="position: fixed; inset: 0; background: rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center; z-index: 10000;">
        <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid #7c6bff; border-radius: 12px; padding: 2rem; max-width: 420px; text-align: center; box-shadow: 0 8px 32px rgba(124,107,255,0.4);">
          <div style="font-size: 2.5rem; margin-bottom: 1rem;">📧</div>
          <h2 style="color: #ffffff; margin: 0 0 0.75rem 0; font-size: 1.25rem;">Re-send Invitation?</h2>
          <p style="color: #a1a1aa; margin: 0 0 1.5rem 0; font-size: 0.95rem; line-height: 1.5;">
            Send another invitation to <strong style="color: var(--cyan);">${email}</strong>?
          </p>
          <div id="reinviteStatus" style="margin-bottom: 1rem; font-size: 0.85rem;"></div>
          <div style="display: flex; gap: 12px; justify-content: center;">
            <button class="btn ghost" onclick="entityUsersManager.closeReinviteModal()">Cancel</button>
            <button id="reinviteSendBtn" class="btn primary" onclick="entityUsersManager.sendReinvite(${invitationId})">Send Invite</button>
          </div>
        </div>
      </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Click outside to close
    document.getElementById('reinviteModal').addEventListener('click', function(e) {
      if (e.target === this) entityUsersManager.closeReinviteModal();
    });
  }
  
  /**
   * Close re-invite modal
   */
  closeReinviteModal() {
    const modal = document.getElementById('reinviteModal');
    if (modal) modal.remove();
  }
  
  /**
   * Send re-invite
   */
  async sendReinvite(invitationId) {
    const statusEl = document.getElementById('reinviteStatus');
    const sendBtn = document.getElementById('reinviteSendBtn');
    
    if (sendBtn) sendBtn.disabled = true;
    if (statusEl) {
      statusEl.textContent = 'Sending...';
      statusEl.style.color = '#5b8cff';
    }
    
    try {
      const response = await fetch(`/api/entity-invitations/${invitationId}/reinvite`, {
        method: 'POST',
        credentials: 'include'
      });
      
      if (response.ok) {
        if (statusEl) {
          statusEl.textContent = 'Invitation sent!';
          statusEl.style.color = '#22c55e';
        }
        
        // Reload users list to update status
        await this.loadUsers();
        this.renderUsersList('entityUsersList');
        this.updateBadge('usersBadge');
        
        if (sendBtn) {
          sendBtn.textContent = 'OK';
          sendBtn.disabled = false;
          sendBtn.onclick = () => this.closeReinviteModal();
        }
      } else {
        const err = await response.json();
        if (statusEl) {
          statusEl.textContent = err.detail || 'Failed to send';
          statusEl.style.color = '#ef4444';
        }
        if (sendBtn) sendBtn.disabled = false;
      }
    } catch (error) {
      console.error('Error re-inviting:', error);
      if (statusEl) {
        statusEl.textContent = 'Failed to send invitation';
        statusEl.style.color = '#ef4444';
      }
      if (sendBtn) sendBtn.disabled = false;
    }
  }
  
  /**
   * Open invite modal
   */
  openInviteModal() {
    const modal = document.getElementById('inviteUserModal');
    const titleEl = document.getElementById('inviteModalTitle');
    const emailInput = document.getElementById('inviteEmail');
    const statusEl = document.getElementById('inviteStatus');
    
    if (titleEl) {
      titleEl.innerHTML = `Invite a User to have access to <span style="color: var(--cyan);">${esc(this.entityName)}</span>`;
    }
    if (emailInput) {
      emailInput.value = '';
    }
    // Clear extra fields
    ['inviteFirstName', 'inviteLastName', 'invitePhone'].forEach(id => {
      const el = document.getElementById(id);
      if (el) { el.value = ''; el.readOnly = false; el.style.opacity = ''; }
    });
    const lookupStatus = document.getElementById('inviteEmailLookupStatus');
    if (lookupStatus) lookupStatus.textContent = '';
    if (statusEl) {
      statusEl.textContent = '';
      statusEl.className = 'invite-status';
    }
    
    if (modal) {
      modal.classList.remove('hidden');
      setTimeout(() => {
        if (emailInput) emailInput.focus();
        this.setupEnterKeyHandler();
        this._setupEmailLookup();
      }, 100);
    }
  }
  
  /**
   * Close invite modal
   */
  closeInviteModal() {
    const modal = document.getElementById('inviteUserModal');
    if (modal) {
      modal.classList.add('hidden');
    }
  }
  
  /**
   * Wire up email blur to auto-lookup existing user and pre-fill name/phone
   */
  _setupEmailLookup() {
    const emailInput = document.getElementById('inviteEmail');
    if (!emailInput || emailInput._lookupBound) return;
    emailInput._lookupBound = true;

    const doLookup = async () => {
      const email = emailInput.value.trim();
      const lookupStatus = document.getElementById('inviteEmailLookupStatus');
      const firstEl = document.getElementById('inviteFirstName');
      const lastEl  = document.getElementById('inviteLastName');
      const phoneEl = document.getElementById('invitePhone');

      // Reset
      [firstEl, lastEl, phoneEl].forEach(el => {
        if (el) { el.readOnly = false; el.style.opacity = ''; }
      });
      if (lookupStatus) lookupStatus.textContent = '';

      if (!email || !email.includes('@') || !email.includes('.')) return;

      try {
        const res = await fetch(`/api/users/lookup-by-email?email=${encodeURIComponent(email)}`, { credentials: 'include' });
        if (!res.ok) return;
        const data = await res.json();
        if (data.found) {
          if (firstEl) { firstEl.value = data.first_name; firstEl.readOnly = true; firstEl.style.opacity = '0.7'; }
          if (lastEl)  { lastEl.value  = data.last_name;  lastEl.readOnly  = true; lastEl.style.opacity  = '0.7'; }
          if (phoneEl) { phoneEl.value = data.phone;      phoneEl.readOnly = true; phoneEl.style.opacity = '0.7'; }
          if (lookupStatus) {
            lookupStatus.textContent = '✓ Existing GigsFill user — info auto-filled';
            lookupStatus.style.color = '#10b981';
          }
        }
      } catch(e) { /* silent */ }
    };

    emailInput.addEventListener('blur', doLookup);
    // Also trigger on Enter/Tab from email field
    emailInput.addEventListener('keydown', (e) => {
      if (e.key === 'Tab') doLookup();
    });
  }

  /**
   * Send invitation
   */
  async sendInvitation() {
    const emailInput = document.getElementById('inviteEmail');
    const statusEl = document.getElementById('inviteStatus');
    const sendBtn = document.getElementById('sendInviteBtn');
    
    const email = emailInput?.value?.trim();
    const firstName = document.getElementById('inviteFirstName')?.value?.trim() || '';
    const lastName  = document.getElementById('inviteLastName')?.value?.trim()  || '';
    const phone     = document.getElementById('invitePhone')?.value?.trim()     || '';
    
    if (!email) {
      if (statusEl) {
        statusEl.textContent = 'Please enter an email address';
        statusEl.className = 'invite-status error';
      }
      return;
    }
    
    // Validate email format
    if (!email.includes('@') || !email.includes('.')) {
      if (statusEl) {
        statusEl.textContent = 'Please enter a valid email address';
        statusEl.className = 'invite-status error';
      }
      return;
    }
    
    // Update UI to show sending
    if (sendBtn) sendBtn.disabled = true;
    if (statusEl) {
      statusEl.textContent = 'Sending Email...';
      statusEl.className = 'invite-status sending';
    }
    
    try {
      const response = await fetch(`/api/entity-users/${this.entityType}/${this.entityId}/invite`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, first_name: firstName, last_name: lastName, phone })
      });
      
      let result;
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        result = await response.json();
      } else {
        const text = await response.text();
        result = { detail: text || 'Server error' };
      }
      
      if (response.ok) {
        if (statusEl) {
          statusEl.textContent = 'Email Sent!';
          statusEl.className = 'invite-status success';
        }
        
        // Reload users list to show new invitation
        await this.loadUsers();
        this.renderUsersList('entityUsersList');
        this.updateBadge('usersBadge');
        
        if (sendBtn) {
          sendBtn.textContent = 'OK';
          sendBtn.disabled = false;
          sendBtn.onclick = () => this.closeInviteModal();
        }
      } else {
        if (statusEl) {
          statusEl.textContent = result.detail || 'Failed to send invitation';
          statusEl.className = 'invite-status error';
        }
        if (sendBtn) sendBtn.disabled = false;
      }
    } catch (error) {
      console.error('Error sending invitation:', error);
      if (statusEl) {
        statusEl.textContent = 'Failed to send invitation';
        statusEl.className = 'invite-status error';
      }
      if (sendBtn) sendBtn.disabled = false;
    }
  }
  
  /**
   * Handle Enter key in email input
   */
  setupEnterKeyHandler() {
    const emailInput = document.getElementById('inviteEmail');
    if (emailInput) {
      emailInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          this.sendInvitation();
        }
      });
    }
  }
}

// Global instance - will be set by the page
let entityUsersManager = null;

/**
 * Initialize entity users management
 */
function initEntityUsers(entityType, entityId, entityName) {
  entityUsersManager = new EntityUsersManager(entityType, entityId, entityName);
  
  // Load user count for badge on init (but don't render list yet)
  entityUsersManager.loadUsers().then(() => {
    entityUsersManager.updateBadge('usersBadge');
  });
  
  return entityUsersManager;
}
