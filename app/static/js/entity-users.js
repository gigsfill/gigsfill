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
    
    // User rows.
    // Audit fix (May 2026 part 8): every user-controlled field interpolated
    // into HTML or into inline-onclick JS-string args is now escaped/coerced.
    // Inline onclick args use jsAttr (JSON.stringify-based) which produces
    // its own outer quotes — emitting `${jsAttr(x)}` not `'${jsAttr(x)}'`.
    const _jsa = window.jsAttr || JSON.stringify;
    const _e = (s) => (typeof esc === 'function' ? esc(s) : String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'));
    const rowsHtml = this.users.map(user => {
      const isPending = user.role === 'pending';
      const isDeclined = user.role === 'declined';
      const isInvitation = isPending || isDeclined;
      const rowOpacity = isInvitation ? 'opacity: 0.7;' : '';
      const _invId = parseInt(user.invitation_id, 10) || 0;
      const _uid = parseInt(user.user_id, 10) || 0;
      const _fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim();
      const clickHandler = isInvitation
        ? `onclick="entityUsersManager.showReinviteModal(${_invId}, ${_jsa(user.email || '')})" style="cursor: pointer; ${rowOpacity}"`
        : `style="${rowOpacity}"`;

      // Jul 22 2026: swap the big red "REMOVE" button for a subtle
      // trash icon on the far right of every non-owner row. Matches
      // the trash icons used on Contact Messages, Support Tickets,
      // Messages Inbox, Admin Users, etc. Owner row shows the "Owner"
      // badge — no trash there (can't delete the main user of the
      // entity, only re-transfer ownership through a separate flow).
      // Pending/declined invitations get a status label + trash so
      // admin can revoke them too.
      let statusHtml = '';
      const _trashBtn = (label, actionJs, hoverTitle) => `
        <button onclick="event.stopPropagation();${actionJs}"
                title="${hoverTitle}"
                style="background:transparent;border:0;color:#94a3b8;font-size:1rem;cursor:pointer;padding:4px 8px;border-radius:4px;line-height:1;"
                onmouseover="this.style.color='#fca5a5';this.style.background='rgba(239,68,68,0.1)';"
                onmouseout="this.style.color='#94a3b8';this.style.background='transparent';">🗑</button>
      `;
      if (user.role === 'owner') {
        // Jul 22 2026: only the CURRENT owner viewing this page can
        // initiate a transfer, so only show the "Transfer" link when
        // the row's user_id matches the viewing user's id. The
        // dropdown target is restricted to existing team members
        // (backend enforces this; UI just lists what's already loaded).
        const _isMe = window._currentUserInfo && window._currentUserInfo.id === _uid;
        const transferLink = _isMe
          ? `<a href="javascript:void(0)" onclick="event.stopPropagation();entityUsersManager.openTransferOwnerModal()" style="font-size:0.7rem;color:#a78bfa;text-decoration:none;border-bottom:1px dashed rgba(167,139,250,0.4);margin-left:8px;" title="Hand off ownership of this ${_e(this.entityType)} to a team member">Transfer →</a>`
          : '';
        statusHtml = `<span style="font-size: 0.7rem; color: var(--cyan); text-transform: uppercase; font-weight: 600;">Owner</span>${transferLink}`;
      } else if (isPending) {
        statusHtml = `<div style="display:flex;align-items:center;gap:8px;justify-content:flex-end;">
          <span style="font-size: 0.7rem; color: #f59e0b; text-transform: uppercase; font-weight: 600;">Pending</span>
          ${_trashBtn('trash', `entityUsersManager.confirmRevokeInvitation(${_invId}, ${_jsa(user.email || '')})`, 'Revoke this pending invitation')}
        </div>`;
      } else if (isDeclined) {
        statusHtml = `<div style="display:flex;align-items:center;gap:8px;justify-content:flex-end;">
          <span style="font-size: 0.7rem; color: #ef4444; text-transform: uppercase; font-weight: 600;">Declined</span>
          ${_trashBtn('trash', `entityUsersManager.confirmRevokeInvitation(${_invId}, ${_jsa(user.email || '')})`, 'Remove this declined invitation from the list')}
        </div>`;
      } else {
        statusHtml = _trashBtn('trash',
          `entityUsersManager.confirmRemoveUser(${_uid}, ${_jsa(_fullName)})`,
          `Remove ${_e(_fullName || user.email || 'this user')}'s access to this ${this.entityType}`);
      }

      return `
        <div class="entity-item" ${clickHandler}>
          <div style="display: grid; grid-template-columns: 120px 120px 1fr 140px 80px; gap: 16px; align-items: center; padding: 12px 16px;">
            <span style="color: ${isInvitation ? 'var(--text-gray)' : 'var(--text)'}; font-size: 0.875rem; font-style: ${isInvitation ? 'italic' : 'normal'};">${_e(user.first_name || '-')}</span>
            <span style="color: ${isInvitation ? 'var(--text-gray)' : 'var(--text)'}; font-size: 0.875rem; font-style: ${isInvitation ? 'italic' : 'normal'};">${_e(user.last_name || '-')}</span>
            <span style="color: var(--text-gray); font-size: 0.875rem; overflow: hidden; text-overflow: ellipsis;">${_e(user.email || '-')}</span>
            <span style="color: var(--text-gray); font-size: 0.875rem;">${_e(user.phone || '-')}</span>
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
   * Show confirmation modal before removing user.
   *
   * Phase 2 migration: was inline-styled HTML. Now uses showStyledModal —
   * auto-toned 'error' (red) because the title contains "Remove".
   */
  confirmRemoveUser(userId, userName) {
    const safeName = String(userName || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
    window.showStyledModal(
      '⚠️ Remove User Access',
      `<p>Are you sure you want to remove <strong style="color:var(--cyan);">${safeName}</strong>'s access?</p>`,
      [
        { text: 'Cancel', style: 'ghost' },
        { text: 'Remove', style: 'danger', onClick: () => this.removeUser(userId) },
      ]
    );
  }

  /**
   * Legacy close helpers — kept for backwards compatibility with any
   * inline onclick attributes elsewhere. Now just closes any open modal.
   */
  closeRemoveModal() {
    if (typeof window.closeAllModals === 'function') window.closeAllModals();
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

  // Jul 22 2026: revoke a pending or declined invitation from the Users
  // tab (trash icon on non-accepted rows). Fires a two-step confirm
  // matching the accepted-user removal flow.
  confirmRevokeInvitation(invitationId, email) {
    if (!invitationId) return;
    const safeEmail = String(email || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
    window.showStyledModal(
      '⚠️ Revoke Invitation',
      `<p>Remove the pending invitation for <strong style="color:var(--cyan);">${safeEmail}</strong>?</p>` +
      `<p style="margin-top:8px;font-size:0.82rem;color:var(--text-gray);">Any link they were emailed will stop working. You can invite them again later.</p>`,
      [
        { text: 'Cancel', style: 'ghost' },
        { text: 'Revoke', style: 'danger', onClick: () => this.revokeInvitation(invitationId) },
      ]
    );
  }

  // Jul 22 2026: current-owner-only "Transfer ownership" flow.
  // Shows a dropdown of existing team members (excluding pending/
  // declined invitations and excluding the current owner themselves).
  // On confirm, fires POST /api/entity-users/{type}/{id}/transfer-owner.
  openTransferOwnerModal() {
    const teamMembers = (this.users || []).filter(u =>
      u.role !== 'pending' && u.role !== 'declined' && u.role !== 'owner' && u.user_id
    );
    if (teamMembers.length === 0) {
      window.showStyledModal(
        '⚠️ No team members yet',
        `<p style="margin:0;">You need at least one team member on this ${this.entityType} before you can transfer ownership.</p>` +
        `<p style="margin:12px 0 0;font-size:0.82rem;color:var(--text-gray);">Invite someone using the button above, wait for them to accept, then come back here.</p>`,
        [{ text: 'OK', style: 'primary' }],
        { tone: 'warning' }
      );
      return;
    }
    const _esc = s => String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    const options = teamMembers.map(u => {
      const label = ((u.first_name || '') + ' ' + (u.last_name || '')).trim() || u.email || ('user #' + u.user_id);
      return `<option value="${u.user_id}">${_esc(label)} — ${_esc(u.email || '')}</option>`;
    }).join('');
    const bodyHtml =
      `<p style="margin:0 0 12px;">Hand off ownership of this ${_esc(this.entityType)} to another team member. You'll keep <strong style="color:var(--cyan);">admin</strong> access afterward — you won't lose your ability to manage.</p>` +
      `<label style="display:block;font-size:0.8rem;color:var(--text-gray);margin-bottom:6px;">New owner</label>` +
      `<select id="_transferOwnerSelect" style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid rgba(255,255,255,0.2);background:rgba(255,255,255,0.05);color:var(--text);font-size:0.95rem;">` +
        options +
      `</select>` +
      `<p style="margin:12px 0 0;font-size:0.78rem;color:var(--text-gray);">The new owner gets full control immediately. This can't be undone by them alone — they'd have to transfer it back to you.</p>`;

    window.showStyledModal(
      '⚡ Transfer Ownership',
      bodyHtml,
      [
        { text: 'Cancel', style: 'ghost' },
        {
          text: 'Transfer', style: 'primary',
          onClick: () => {
            const sel = document.getElementById('_transferOwnerSelect');
            const newOwnerId = sel ? parseInt(sel.value, 10) : 0;
            if (!newOwnerId) return;
            this.transferOwner(newOwnerId);
          }
        },
      ],
      { tone: 'warning' }
    );
  }

  async transferOwner(newOwnerUserId) {
    try {
      const res = await fetch(`/api/entity-users/${this.entityType}/${this.entityId}/transfer-owner`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ new_owner_user_id: newOwnerUserId })
      });
      let body = null;
      try { body = await res.json(); } catch (_) {}
      if (!res.ok) {
        this.showResultModal('error', (body && body.detail) || 'Transfer failed');
        return;
      }
      this.showResultModal('success', (body && body.message) || 'Ownership transferred', () => {
        window.location.reload();
      });
    } catch (e) {
      this.showResultModal('error', 'Failed to transfer ownership');
    }
  }

  async revokeInvitation(invitationId) {
    this.closeRemoveModal();
    try {
      const response = await fetch(`/api/entity-users/${this.entityType}/${this.entityId}/invitations/${invitationId}`,
        { method: 'DELETE', credentials: 'include' });
      if (response.ok) {
        await this.loadUsers();
        this.renderUsersList('entityUsersList');
        this.updateBadge('usersBadge');
      } else {
        let detail = 'Failed to revoke invitation';
        try { const j = await response.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
        this.showResultModal('error', detail);
      }
    } catch (error) {
      console.error('Error revoking invitation:', error);
      this.showResultModal('error', 'Failed to revoke invitation');
    }
  }

  /**
   * Show branded result modal.
   *
   * Phase 2 migration: delegates to gf-modals helpers for the right tone:
   * success → green stripe, error → red, other → neutral. onClose fires
   * after the modal is dismissed (any path: OK button, X, esc, backdrop).
   */
  showResultModal(type, message, onClose = null) {
    if (type === 'success') {
      window.showSuccessModal('Success!', message, onClose);
    } else if (type === 'error') {
      window.showErrorModal('Error', message, onClose);
    } else {
      window.showAlert(message, 'Notice', { onClose });
    }
  }

  /** Legacy close helper — kept for backwards compatibility. */
  closeResultModal(hasCallback) {
    if (typeof window.closeAllModals === 'function') window.closeAllModals();
  }
  
  /**
   * Show re-invite confirmation modal.
   *
   * Phase 2 migration: was inline-styled HTML. Now uses showStyledModal.
   * The #reinviteStatus div is preserved inside the body so sendReinvite()
   * can still update it in place during the async request.
   */
  showReinviteModal(invitationId, email) {
    const safeEmail = String(email || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
    window.showStyledModal(
      '📧 Re-send Invitation?',
      `<p>Send another invitation to <strong style="color:var(--cyan);">${safeEmail}</strong>?</p>` +
      `<div id="reinviteStatus" style="margin-top:1rem;font-size:0.85rem;text-align:center;min-height:20px;"></div>`,
      [
        { text: 'Cancel', style: 'ghost' },
        { text: 'Send Invite', style: 'primary',
          onClick: () => { this.sendReinvite(invitationId); return false; /* let sendReinvite update status + close */ } },
      ]
    );
    // The legacy sendReinvite implementation expects #reinviteSendBtn — give
    // the gfm-modal footer's last button that id so the existing handler can
    // disable it without rewrites.
    setTimeout(() => {
      const overlay = document.querySelector('.gfm-modal-overlay');
      if (overlay) {
        const btns = overlay.querySelectorAll('.gfm-modal-footer .btn');
        if (btns.length) btns[btns.length - 1].id = 'reinviteSendBtn';
      }
    }, 0);
  }

  /** Legacy close helper — kept for backwards compatibility. */
  closeReinviteModal() {
    if (typeof window.closeAllModals === 'function') window.closeAllModals();
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
  // ─── Multi-invite modal (2026-07-25) ─────────────────────────────────
  // Replaced the single-email + name/phone form with a bulk pattern:
  // paste any number of emails, one shared personal message, one row
  // in the Users list per invitee. Server-side loop lives in
  // POST /api/entity-users/{type}/{id}/invite-multiple. Old single-invite
  // endpoint is untouched — other code paths (re-invite, etc.) still use it.
  openInviteModal() {
    const modal    = document.getElementById('inviteUserModal');
    const titleEl  = document.getElementById('inviteModalTitle');
    const emailsEl = document.getElementById('inviteEmails');
    const msgEl    = document.getElementById('inviteMessage');
    const statusEl = document.getElementById('inviteStatus');
    const sendBtn  = document.getElementById('sendInviteBtn');

    if (titleEl) {
      titleEl.innerHTML = `Invite Users to <span style="color: var(--cyan);">${esc(this.entityName)}</span>`;
    }
    if (emailsEl) emailsEl.value = '';
    if (msgEl)    msgEl.value    = '';
    if (statusEl) { statusEl.innerHTML = ''; statusEl.className = 'invite-status'; }
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.textContent = 'Send Invitations';
      sendBtn.onclick = () => this.sendInvitation();
    }
    this._updateInviteEmailCount();

    if (modal) {
      modal.classList.remove('hidden');
      setTimeout(() => {
        if (emailsEl) {
          emailsEl.focus();
          // Live counter — bound once per modal open; oninput assignment
          // (not addEventListener) makes re-open reset cleanly.
          emailsEl.oninput = () => this._updateInviteEmailCount();
        }
      }, 100);
    }
  }

  closeInviteModal() {
    const modal = document.getElementById('inviteUserModal');
    if (modal) modal.classList.add('hidden');
  }

  _parseInviteEmails() {
    const raw = (document.getElementById('inviteEmails')?.value || '').trim();
    if (!raw) return { valid: [], invalid: [] };
    const tokens = raw.split(/[,;\s\n]+/).map(t => t.trim()).filter(Boolean);
    const seen = {}, valid = [], invalid = [];
    tokens.forEach(t => {
      const lc = t.toLowerCase();
      if (seen[lc]) return;
      seen[lc] = true;
      if (t.includes('@') && t.split('@')[1] && t.split('@')[1].includes('.')) valid.push(t);
      else invalid.push(t);
    });
    return { valid, invalid };
  }

  _updateInviteEmailCount() {
    const el = document.getElementById('inviteEmailCount');
    if (!el) return;
    const { valid, invalid } = this._parseInviteEmails();
    const n = valid.length;
    let txt = n === 0 ? '0 emails entered' : (n + ' email' + (n === 1 ? '' : 's') + ' entered');
    if (invalid.length) txt += ' · ' + invalid.length + ' invalid';
    el.textContent = txt;
    el.style.color = invalid.length ? '#f59e0b' : (n > 25 ? '#f59e0b' : 'var(--text-gray)');
  }

  async sendInvitation() {
    const statusEl = document.getElementById('inviteStatus');
    const sendBtn  = document.getElementById('sendInviteBtn');
    const { valid: emails, invalid } = this._parseInviteEmails();
    const message = (document.getElementById('inviteMessage')?.value || '').trim();

    if (emails.length === 0) {
      if (statusEl) {
        statusEl.textContent = invalid.length
          ? 'No valid email addresses. Check the entries and try again.'
          : 'Please enter at least one email address.';
        statusEl.className = 'invite-status error';
      }
      return;
    }

    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.textContent = 'Sending ' + emails.length + ' invitation' + (emails.length === 1 ? '' : 's') + '…';
    }
    if (statusEl) { statusEl.textContent = ''; statusEl.className = 'invite-status sending'; }

    try {
      const response = await fetch(
        `/api/entity-users/${this.entityType}/${this.entityId}/invite-multiple`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ emails: emails.join(','), personal_message: message })
        }
      );

      let result;
      const ct = response.headers.get('content-type') || '';
      if (ct.includes('application/json')) result = await response.json();
      else result = { detail: (await response.text()) || 'Server error' };

      if (!response.ok) {
        if (statusEl) {
          statusEl.textContent = result.detail || 'Failed to send invitations';
          statusEl.className = 'invite-status error';
        }
        if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = 'Send Invitations'; }
        return;
      }

      // Build human-readable summary from the server's counters.
      const parts = [];
      if (result.sent_count > 0)
        parts.push('✓ ' + result.sent_count + ' invitation' + (result.sent_count === 1 ? '' : 's') + ' sent');
      if (result.already_member_count > 0)
        parts.push(result.already_member_count + ' already a member');
      if (result.already_pending_count > 0)
        parts.push(result.already_pending_count + ' already had a pending invite');
      if (result.email_failed_count > 0)
        parts.push(result.email_failed_count + ' invited but email delivery failed');
      if (result.invalid_count > 0)
        parts.push(result.invalid_count + ' invalid email' + (result.invalid_count === 1 ? '' : 's'));
      if (result.other_errors_count > 0)
        parts.push(result.other_errors_count + ' other error' + (result.other_errors_count === 1 ? '' : 's'));

      if (statusEl) {
        statusEl.innerHTML = parts.join('<br>');
        statusEl.className = result.sent_count > 0 ? 'invite-status success' : 'invite-status error';
      }

      await this.loadUsers();
      this.renderUsersList('entityUsersList');
      this.updateBadge('usersBadge');

      if (sendBtn) {
        sendBtn.textContent = 'Done';
        sendBtn.disabled = false;
        sendBtn.onclick = () => this.closeInviteModal();
      }
      // 2026-07-26: auto-close after 5s on any successful send so the
      // user doesn't have to click Done. Guarded on `sent_count > 0` so
      // a purely-skipped batch (everyone already a member) stays open
      // long enough for the user to read the breakdown. Guarded on the
      // modal still being open so a manual close inside the 5s window
      // doesn't re-close a modal the user has since re-opened.
      if (result.sent_count > 0) {
        const modal = document.getElementById('inviteUserModal');
        setTimeout(() => {
          if (modal && !modal.classList.contains('hidden')) this.closeInviteModal();
        }, 5000);
      }
    } catch (error) {
      console.error('Error sending invitations:', error);
      if (statusEl) {
        statusEl.textContent = 'Failed to send invitations';
        statusEl.className = 'invite-status error';
      }
      if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = 'Send Invitations'; }
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
