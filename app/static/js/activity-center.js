// v88: Activity Center - Clickable Bubbles + Filtering by Artist/Venue

class ActivityCenter {
  constructor(containerId, entityId = null, entityType = null) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;

    // 🔥 v89 FIX: bind instance immediately for inline onclick handlers
    if (containerId === 'activityCenter') {
      window.activityCenter = this;
    }
    if (containerId === 'venueActivityCenter') {
      window.activityCenterVenue = this;
    }
    
    this.notifications = [];
    this.currentPage = 1;
    this.perPage = 10;
    // 🔥 DEFAULT FILTER: Unread for artist + venue pages
    this.activeFilters = new Set(['unread']);

    this.isVenue = containerId === 'venueActivityCenter';
    this.entityId = entityId;
    this.entityType = entityType;

    this.render();
    this.loadNotifications();

  }

  toggleFilter(filter) {
    if (this.activeFilters.has(filter)) {
      this.activeFilters.delete(filter);
    } else {
      this.activeFilters.add(filter); // v73: Allow multiple filters
    }
    this.render(); // Re-render to update bubble styles
    this.currentPage = 1; // Reset to first page on filter change
    this.displayFiltered();
  }

  calculateStats() {
    return {
      all: this.notifications.length,
      unread: this.notifications.filter(n => !n.is_read).length,
      booked: this.notifications.filter(n => n.notification_type === 'gig_booked').length,
      cancelled: this.notifications.filter(n => n.notification_type === 'gig_cancelled').length,
      preferred: this.notifications.filter(n => n.notification_type === 'preferred_approved').length,
      requests: this.notifications.filter(n => n.notification_type === 'preferred_request').length,
      denied: this.notifications.filter(n => n.notification_type === 'preferred_denied').length
    };
  }

  render() {
    const stats = this.calculateStats();
    const isActive = (filter) => this.activeFilters.has(filter);
    
    // v73: Different bubbles for venue vs artist
    const bubbles = this.isVenue ? `
      <div class="stat-bubble" onclick="window.activityCenterVenue.toggleFilter('all')" style="background: ${isActive('all') ? 'rgba(124, 107, 255, 0.3)' : 'rgba(124, 107, 255, 0.1)'}; border: 2px solid ${isActive('all') ? '#7c6bff' : 'rgba(124, 107, 255, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('all') ? '0 0 12px rgba(124, 107, 255, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #7c6bff;">${stats.all}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">All</span>
      </div>
      
      <div class="stat-bubble" onclick="window.activityCenterVenue.toggleFilter('unread')" style="background: ${isActive('unread') ? 'rgba(91, 140, 255, 0.3)' : 'rgba(91, 140, 255, 0.1)'}; border: 2px solid ${isActive('unread') ? '#5b8cff' : 'rgba(91, 140, 255, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('unread') ? '0 0 12px rgba(91, 140, 255, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #5b8cff;">${stats.unread}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Unread</span>
      </div>
      
      <div class="stat-bubble" onclick="window.activityCenterVenue.toggleFilter('booked')" style="background: ${isActive('booked') ? 'rgba(34, 197, 94, 0.3)' : 'rgba(34, 197, 94, 0.1)'}; border: 2px solid ${isActive('booked') ? '#22c55e' : 'rgba(34, 197, 94, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('booked') ? '0 0 12px rgba(34, 197, 94, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #22c55e;">${stats.booked}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Gigs Booked</span>
      </div>
      
      <div class="stat-bubble" onclick="window.activityCenterVenue.toggleFilter('cancelled')" style="background: ${isActive('cancelled') ? 'rgba(239, 68, 68, 0.3)' : 'rgba(239, 68, 68, 0.1)'}; border: 2px solid ${isActive('cancelled') ? '#ef4444' : 'rgba(239, 68, 68, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('cancelled') ? '0 0 12px rgba(239, 68, 68, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #ef4444;">${stats.cancelled}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Gigs Cancelled</span>
      </div>
      
      <div class="stat-bubble" onclick="window.activityCenterVenue.toggleFilter('requests')" style="background: ${isActive('requests') ? 'rgba(249, 115, 22, 0.3)' : 'rgba(249, 115, 22, 0.1)'}; border: 2px solid ${isActive('requests') ? '#f97316' : 'rgba(249, 115, 22, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('requests') ? '0 0 12px rgba(249, 115, 22, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #f97316;">${stats.requests}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred Requests</span>
      </div>
    ` : `
      <div class="stat-bubble" onclick="window.activityCenter.toggleFilter('all')" style="background: ${isActive('all') ? 'rgba(124, 107, 255, 0.3)' : 'rgba(124, 107, 255, 0.1)'}; border: 2px solid ${isActive('all') ? '#7c6bff' : 'rgba(124, 107, 255, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('all') ? '0 0 12px rgba(124, 107, 255, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #7c6bff;">${stats.all}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">All</span>
      </div>
      
      <div class="stat-bubble" onclick="window.activityCenter.toggleFilter('unread')" style="background: ${isActive('unread') ? 'rgba(91, 140, 255, 0.3)' : 'rgba(91, 140, 255, 0.1)'}; border: 2px solid ${isActive('unread') ? '#5b8cff' : 'rgba(91, 140, 255, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('unread') ? '0 0 12px rgba(91, 140, 255, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #5b8cff;">${stats.unread}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Unread</span>
      </div>
      
      <div class="stat-bubble" onclick="window.activityCenter.toggleFilter('booked')" style="background: ${isActive('booked') ? 'rgba(34, 197, 94, 0.3)' : 'rgba(34, 197, 94, 0.1)'}; border: 2px solid ${isActive('booked') ? '#22c55e' : 'rgba(34, 197, 94, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('booked') ? '0 0 12px rgba(34, 197, 94, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #22c55e;">${stats.booked}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Gigs Booked</span>
      </div>
      
      <div class="stat-bubble" onclick="window.activityCenter.toggleFilter('cancelled')" style="background: ${isActive('cancelled') ? 'rgba(239, 68, 68, 0.3)' : 'rgba(239, 68, 68, 0.1)'}; border: 2px solid ${isActive('cancelled') ? '#ef4444' : 'rgba(239, 68, 68, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('cancelled') ? '0 0 12px rgba(239, 68, 68, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #ef4444;">${stats.cancelled}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Gigs Cancelled</span>
      </div>
      
      <div class="stat-bubble" onclick="window.activityCenter.toggleFilter('requests')" style="background: ${isActive('requests') ? 'rgba(249, 115, 22, 0.3)' : 'rgba(249, 115, 22, 0.1)'}; border: 2px solid ${isActive('requests') ? '#f97316' : 'rgba(249, 115, 22, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('requests') ? '0 0 12px rgba(249, 115, 22, 0.5)' : 'none'};">
        <span style="font-size: 0.9rem; font-weight: 600; color: #f97316;">${stats.requests}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred Requests</span>
      </div>
    `;
    
    this.container.innerHTML = `
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 0.75rem; flex-wrap: wrap;">
        <h2 style="margin: 0; font-size: 1rem;">Activity Center</h2>
        
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
          ${bubbles}
        </div>
      </div>
      
      <div id="notificationsList${this.isVenue ? 'Venue' : ''}" style="display: flex; flex-direction: column; gap: 4px;"></div>
    `;
  }

  displayFiltered() {
    const listEl = document.getElementById('notificationsList' + (this.isVenue ? 'Venue' : ''));
    if (!listEl) return;
    const instanceName = this.isVenue ? 'activityCenterVenue' : 'activityCenter';
    
    let filtered = this.notifications;
    
    // v73: If no filters selected, show nothing
    if (this.activeFilters.size === 0) {
      listEl.innerHTML = '<p style="color: var(--text-muted); text-align: left; padding: 1rem; font-size: 0.9rem;">No notification filters selected</p>';
      return;
    }
    
    // If 'all' is selected, show all
    if (this.activeFilters.has('all')) {
      filtered = this.notifications;
    } else {
      // Apply multiple filters (OR logic)
      filtered = this.notifications.filter(n => {
        if (this.activeFilters.has('unread') && !n.is_read) return true;
        if (this.activeFilters.has('booked') && (n.notification_type === 'gig_booked' || n.notification_type === 'gig_edited')) return true;
        if (this.activeFilters.has('cancelled') && n.notification_type === 'gig_cancelled') return true;
        if (this.activeFilters.has('preferred') && n.notification_type === 'preferred_approved') return true;
        if (this.activeFilters.has('requests') && n.notification_type === 'preferred_request') return true;
        if (this.activeFilters.has('denied') && n.notification_type === 'preferred_denied') return true;
        return false;
      });
    }
    
    if (filtered.length === 0) {
      listEl.innerHTML = '<p style="color: var(--text-muted); text-align: left; padding: 1rem; font-size: 0.9rem;">No notifications in this category</p>';
      return;
    }

    // Pagination
    const totalPages = Math.ceil(filtered.length / this.perPage);
    if (this.currentPage > totalPages) this.currentPage = totalPages;
    if (this.currentPage < 1) this.currentPage = 1;
    const startIdx = (this.currentPage - 1) * this.perPage;
    const pageItems = filtered.slice(startIdx, startIdx + this.perPage);
    
    const rows = pageItems.map(n => {
      const dateStr = n.created_at;
      let date = '';
      if (dateStr) {
        // created_at is stored as UTC from Python's datetime.utcnow()
        // Ensure we parse it as UTC by appending 'Z' if no timezone info
        const utcStr = dateStr.includes('Z') || dateStr.includes('+') ? dateStr : dateStr.replace(' ', 'T') + 'Z';
        const d = new Date(utcStr);
        if (!isNaN(d.getTime())) {
          date = d.toLocaleDateString();
        }
      }
      let messageWithLinks = this.formatNotificationMessage(n);
      
      // Action buttons (approve/deny, accept/decline)
      let actionButtons = '';
      if (this.isVenue && n.notification_type === 'preferred_request' && n.artist_id && n.venue_id) {
        actionButtons = `
          <div style="display: flex; gap: 4px;">
            <button onclick="event.stopPropagation(); window.${instanceName}.approvePreferred(${n.artist_id}, ${n.venue_id})" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #22c55e; border: 1px solid #22c55e; line-height: 1.4; font-weight: 500;">Approve</button>
            <button onclick="event.stopPropagation(); window.${instanceName}.denyPreferred(${n.artist_id}, ${n.venue_id})" class="btn ghost" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500;">Deny</button>
          </div>
        `;
      }
      if (n.notification_type === 'entity_invite' && n.action_token) {
        actionButtons = `
          <div style="display: flex; gap: 4px;">
            <button onclick="event.stopPropagation(); window.${instanceName}.acceptEntityInvite('${n.action_token}', ${n.id})" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #22c55e; border: 1px solid #22c55e; line-height: 1.4; font-weight: 500;">Accept</button>
            <button onclick="event.stopPropagation(); window.${instanceName}.declineEntityInvite('${n.action_token}', ${n.id})" class="btn ghost" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500;">Decline</button>
          </div>
        `;
      }
      if (n.notification_type === 'entity_invite_accepted') {
        actionButtons = `<span style="font-size: 0.7rem; color: #22c55e; font-weight: 600; text-transform: uppercase;">✓ Accepted</span>`;
      }
      if (n.notification_type === 'entity_invite_declined') {
        actionButtons = `<span style="font-size: 0.7rem; color: #ef4444; font-weight: 600; text-transform: uppercase;">✕ Declined</span>`;
      }
      if (n.notification_type === 'waitlist_exhausted_venue') {
        actionButtons = `<span style="font-size: 0.7rem; color: #f59e0b; font-weight: 600;">⚠️ Artists Notified</span>`;
      }
      
      // Split message into main + detail for two-line display
      const msgParts = (typeof messageWithLinks === 'object') ? messageWithLinks : this.splitMessage(messageWithLinks);
      const titleClean = (n.title || '').replace(/!$/, '');
      const rowBg = n.is_read ? 'rgba(255,255,255,0.02)' : 'rgba(91, 140, 255, 0.08)';
      const borderColor = n.is_read ? 'transparent' : '#5b8cff';
      const titleColor = n.is_read ? 'var(--text)' : '#5b8cff';
      const mainText = msgParts.main || msgParts;
      const detailLine = msgParts.detail ? '<div style="padding-left: 252px; color: var(--text-muted); font-size: 0.82rem; margin-top: 1px; font-style: italic;">' + msgParts.detail + '</div>' : '';
      
      return '<div onclick="window.' + instanceName + '.markAsRead(' + n.id + ')" style="padding: 6px 8px; background: ' + rowBg + '; border-left: 3px solid ' + borderColor + '; border-radius: 4px; cursor: pointer;" onmouseover="this.style.background=\'rgba(255,255,255,0.05)\'" onmouseout="this.style.background=\'' + rowBg + '\'">' +
        '<div style="display: flex; align-items: flex-start; gap: 8px;">' +
          '<div style="flex: 1; min-width: 0;">' +
            '<div style="display: flex; align-items: flex-start; gap: 8px;">' +
              '<div style="font-size: 0.85rem; flex: 1; min-width: 0;">' +
                '<div style="display: flex; align-items: baseline;">' +
                  '<strong style="color: ' + titleColor + '; font-size: 0.85rem; white-space: nowrap; min-width: 240px; margin-right: 12px; flex-shrink: 0;">' + titleClean + '</strong>' +
                  '<span style="color: var(--text-muted); font-size: 0.85rem;">' + mainText + '</span>' +
                '</div>' +
                detailLine +
              '</div>' +
              actionButtons +
            '</div>' +
          '</div>' +
          '<span style="color: var(--text-muted); font-size: 0.75rem; white-space: nowrap; margin-top: 2px;">' + date + '</span>' +
          '<button onclick="event.stopPropagation(); window.' + instanceName + '.deleteNotification(' + n.id + ')" title="Delete" style="background: none; border: none; cursor: pointer; color: var(--text-muted); font-size: 0.85rem; padding: 2px 6px; border-radius: 4px; transition: all 0.15s; flex-shrink: 0;" onmouseover="this.style.color=\'#ef4444\';this.style.background=\'rgba(239,68,68,0.1)\'" onmouseout="this.style.color=\'var(--text-muted)\';this.style.background=\'none\'">&#10005;</button>' +
        '</div>' +
      '</div>';
    }).join('');

    // Pagination controls
    let paginationHtml = '';
    if (totalPages > 1) {
      paginationHtml = `
        <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 8px; font-size: 0.75rem; color: var(--text-muted);">
          <span>Page ${this.currentPage} of ${totalPages}</span>
          <button onclick="event.stopPropagation(); window.${instanceName}.goPage(${this.currentPage - 1})" ${this.currentPage <= 1 ? 'disabled' : ''} style="background: rgba(255,255,255,0.05); border: 1px solid var(--glass-border, rgba(255,255,255,0.1)); color: ${this.currentPage <= 1 ? 'rgba(255,255,255,0.2)' : 'var(--text)'}; padding: 4px 10px; border-radius: 4px; cursor: ${this.currentPage <= 1 ? 'default' : 'pointer'}; font-size: 0.75rem;">◀ Prev</button>
          <button onclick="event.stopPropagation(); window.${instanceName}.goPage(${this.currentPage + 1})" ${this.currentPage >= totalPages ? 'disabled' : ''} style="background: rgba(255,255,255,0.05); border: 1px solid var(--glass-border, rgba(255,255,255,0.1)); color: ${this.currentPage >= totalPages ? 'rgba(255,255,255,0.2)' : 'var(--text)'}; padding: 4px 10px; border-radius: 4px; cursor: ${this.currentPage >= totalPages ? 'default' : 'pointer'}; font-size: 0.75rem;">Next ▶</button>
        </div>
      `;
    }

    listEl.innerHTML = rows + paginationHtml;
  }

  goPage(page) {
    this.currentPage = page;
    this.displayFiltered();
  }

  async deleteNotification(notificationId) {
    try {
      await fetch(`/api/notifications/${notificationId}`, {
        method: 'DELETE',
        credentials: 'include'
      });
      // Remove from local array
      this.notifications = this.notifications.filter(n => n.id !== notificationId);
      this.render();
      this.displayFiltered();
      
      // Update badge with new unread count
      const stats = this.calculateStats();
      const activityBadge = document.getElementById('activityBadge');
      if (activityBadge) {
        activityBadge.textContent = `(${stats.unread})`;
      }
    } catch (error) {
      console.error('Error deleting notification:', error);
    }
  }

  // Convert 24-hour time string (HH:MM) to 12-hour format (H:MM AM/PM)
  formatTimeTo12Hour(timeStr) {
    if (!timeStr) return '';
    
    // Handle HH:MM or HH:MM:SS format
    const parts = timeStr.split(':');
    if (parts.length < 2) return timeStr;
    
    let hours = parseInt(parts[0], 10);
    const minutes = parts[1];
    
    if (isNaN(hours)) return timeStr;
    
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12;
    if (hours === 0) hours = 12;
    
    return `${hours}:${minutes} ${ampm}`;
  }

  formatNotificationMessage(n) {
    // Create hyperlinks for names
    const artistLink = n.artist_name && n.artist_id ? 
      `<a href="/app/artist-profile.html?artist_id=${n.artist_id}" target="_blank" onclick="event.stopPropagation()" style="color: #7c6bff; text-decoration: none; font-weight: 600;">${n.artist_name}</a>` : 
      n.artist_name || '';
    
    const venueLink = n.venue_name && n.venue_id ? 
      `<a href="/app/venue-profile.html?venue_id=${n.venue_id}" target="_blank" onclick="event.stopPropagation()" style="color: #7c6bff; text-decoration: none; font-weight: 600;">${n.venue_name}</a>` : 
      n.venue_name || '';

    // Format date and time if available
    let gigDate = '';
    let gigTime = '';
    if (n.gig_date) {
      const _ds = String(n.gig_date).match(/(\d{4})-(\d{2})-(\d{2})/);
      if (_ds) {
        const dateObj = new Date(parseInt(_ds[1]), parseInt(_ds[2]) - 1, parseInt(_ds[3]));
        gigDate = dateObj.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
      }
    }
    if (n.gig_start_time) {
      gigTime = this.formatTimeTo12Hour(n.gig_start_time);
    }

    // Helper: apply name links to a raw message string
    const linkify = (msg) => {
      if (n.artist_name && n.artist_id) msg = msg.replace(new RegExp(n.artist_name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), artistLink);
      if (n.venue_name && n.venue_id) msg = msg.replace(new RegExp(n.venue_name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), venueLink);
      return msg;
    };

    // Custom formatting based on notification type
    switch (n.notification_type) {
      case 'preferred_approved':
        return `${venueLink} has approved ${artistLink} as a preferred artist`;
      
      case 'preferred_denied':
        return `${venueLink} has denied ${artistLink} preferred artist request.`;
      
      case 'gig_cancelled': {
        // Slot cancellation — split into two lines
        if (n.message && n.message.includes('Slot')) {
          let msg = linkify(n.message);
          // Split at ". Slot" or ". Your Slot"
          const slotMatch = msg.match(/^(.*?\.\s*)((?:Your\s+)?Slot\s+.*)$/i);
          if (slotMatch) {
            return { main: slotMatch[1].trim(), detail: slotMatch[2].trim() };
          }
          return { main: msg, detail: '' };
        }
        // Non-slot cancellation
        let mainMsg = '';
        if (gigDate && gigTime) {
          mainMsg = `${artistLink} Cancelled gig at ${venueLink} on ${gigDate} at ${gigTime}.`;
        } else if (gigDate) {
          mainMsg = `${artistLink} Cancelled gig at ${venueLink} on ${gigDate}.`;
        } else {
          mainMsg = `${artistLink} Cancelled gig at ${venueLink}.`;
        }
        return { main: mainMsg, detail: '' };
      }
      
      case 'gig_booked': {
        // Slot booking — split into two lines
        if (n.message && n.message.includes('Slot')) {
          let msg = linkify(n.message);
          // Split at ". Slot" or ". Booked Slot"
          const slotMatch = msg.match(/^(.*?\.\s*)((?:Booked\s+)?Slot\s+.*)$/i);
          if (slotMatch) {
            return { main: slotMatch[1].trim(), detail: slotMatch[2].trim() };
          }
          return { main: msg, detail: '' };
        }
        // Non-slot booking
        let mainMsg = '';
        if (gigDate && gigTime) {
          mainMsg = `${artistLink} booked a gig at ${venueLink} on ${gigDate} at ${gigTime}.`;
        } else if (gigDate) {
          mainMsg = `${artistLink} booked a gig at ${venueLink} on ${gigDate}.`;
        } else {
          mainMsg = `${artistLink} booked a gig at ${venueLink}.`;
        }
        return { main: mainMsg, detail: '' };
      }
      
      case 'gig_edited': {
        // Slot edit — split into two lines matching gig_booked format
        if (n.message && n.message.includes('Slot')) {
          let msg = linkify(n.message);
          const slotMatch = msg.match(/^(.*?\.\s*)((?:Updated\s+)?Slot\s+.*)$/i);
          if (slotMatch) {
            return { main: slotMatch[1].trim(), detail: slotMatch[2].trim() };
          }
          return { main: msg, detail: '' };
        }
        // Non-slot edit
        let mainMsg = '';
        if (gigDate && gigTime) {
          mainMsg = `${venueLink} updated your gig on ${gigDate} at ${gigTime}.`;
        } else if (gigDate) {
          mainMsg = `${venueLink} updated your gig on ${gigDate}.`;
        } else {
          mainMsg = `${venueLink} updated your gig.`;
        }
        return { main: mainMsg, detail: '' };
      }

      case 'preferred_request':
        return `${artistLink} requested preferred status at ${venueLink}.`;
      
      case 'contract_pending': {
        let msg = linkify(n.message || '');
        if (!this.isVenue && n.gig_id) {
          msg += '<div style="margin-top:8px; display:flex; flex-wrap:wrap; gap:10px;">' +
            '<a href="javascript:void(0)" onclick="event.stopPropagation(); window.openGigModalForContractUpload && window.openGigModalForContractUpload(' + n.gig_id + ')" style="color: #c4b5fd; text-decoration: none; font-weight: 600; font-size:0.82rem; padding:4px 12px; background:rgba(139,92,246,0.15); border:1px solid rgba(139,92,246,0.3); border-radius:6px; display:inline-block;">⬇ Download Contract PDF</a>' +
            '<a href="javascript:void(0)" onclick="event.stopPropagation(); window.openGigModalForContractUpload && window.openGigModalForContractUpload(' + n.gig_id + ')" style="color: #c4b5fd; text-decoration: none; font-weight: 600; font-size:0.82rem; padding:4px 12px; background:rgba(139,92,246,0.25); border:1px solid rgba(139,92,246,0.4); border-radius:6px; display:inline-block;">⬆ Upload Signed Contract PDF</a>' +
            '</div>';
        }
        return msg;
      }
            case 'contract_artist_signed': {
        // Artist: you uploaded, waiting on venue
        let msg = linkify(n.message || '');
        return msg;
      }
            case 'contract_countersign_needed': {
        let msg = `${artistLink} has signed the contract for ${n.gig_title || gigDate || 'a gig'}.`;
        if (n.gig_id) {
          msg += ` <a href="javascript:void(0)" onclick="event.stopPropagation(); window.showCountersignModal && window.showCountersignModal(${n.gig_id})" style="color: #a78bfa; text-decoration: none; font-weight: 600; border-bottom: 1px dashed rgba(167,139,250,0.5);">Countersign Contract →</a>`;
        }
        return msg;
      }
      
      case 'contract_ready': {
        // Per-gig PDF: venue uploaded contract, artist needs to download/sign/upload
        let msg = linkify(n.message || '');
        if (!this.isVenue && n.gig_id) {
          msg += ` <a href="javascript:void(0)" onclick="event.stopPropagation(); window.openGigModalForContractUpload && window.openGigModalForContractUpload(${n.gig_id})" style="color: #06b6d4; text-decoration: none; font-weight: 600; border-bottom: 1px dashed rgba(6,182,212,0.5);">Download & Sign Contract →</a>`;
        }
        return msg;
      }
      
      case 'contract_awaiting_venue': {
        return linkify(n.message || '');
      }
      
      case 'contract_upload_needed': {
        let msg = linkify(n.message || '');
        if (this.isVenue && n.gig_id) {
          msg += ` <a href="javascript:void(0)" onclick="event.stopPropagation(); window.showVenueGigModal && window.showVenueGigModal(${n.gig_id})" style="color: #a78bfa; text-decoration: none; font-weight: 600; border-bottom: 1px dashed rgba(167,139,250,0.5);">Upload Contract →</a>`;
        }
        return msg;
      }
      
      case 'contract_countersigned': {
        let msg = `${artistLink} has signed the contract for ${n.gig_title || gigDate || 'a gig'}.`;
        msg += ` <span style="color: #22c55e; font-weight: 600;">Countersign Completed ✓</span>`;
        return msg;
      }
      
      case 'payment_cancelled': {
        let pmsg = n.message || '';
        if (n.artist_name && n.artist_id) pmsg = pmsg.replace(new RegExp(n.artist_name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), artistLink);
        if (n.venue_name && n.venue_id) pmsg = pmsg.replace(new RegExp(n.venue_name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), venueLink);
        return pmsg;
      }
      
      case 'payment_reinstated': {
        let rmsg = n.message || '';
        if (n.artist_name && n.artist_id) rmsg = rmsg.replace(new RegExp(n.artist_name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), artistLink);
        if (n.venue_name && n.venue_id) rmsg = rmsg.replace(new RegExp(n.venue_name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), venueLink);
        return rmsg;
      }
      
      default:
        // Fall back to the original message with name replacements
        let messageWithLinks = n.message || '';
        
        // Replace artist name with hyperlink if available
        if (n.artist_name && n.artist_id) {
          messageWithLinks = messageWithLinks.replace(new RegExp(n.artist_name, 'g'), artistLink);
        }
        
        // Replace venue name with hyperlink if available
        if (n.venue_name && n.venue_id) {
          messageWithLinks = messageWithLinks.replace(new RegExp(n.venue_name, 'g'), venueLink);
        }
        
        // Replace "You" and "Your" with actual names
        if (this.isVenue && n.venue_name) {
          messageWithLinks = messageWithLinks.replace(/\bYou\b/g, n.venue_name);
          messageWithLinks = messageWithLinks.replace(/\bYour\b/g, venueLink + "'s");
          messageWithLinks = messageWithLinks.replace(new RegExp(`\\b${n.venue_name}\\b`, 'g'), venueLink);
        } else if (!this.isVenue && n.artist_name) {
          // For notifications where the viewer IS the subject (e.g. waitlist_offer sent to the artist),
          // keep "You/Your" as-is. Only replace for cross-entity notifications.
          const _firstPersonTypes = ['waitlist_offer', 'waitlist_gig_available', 'gig_cancelled'];
          if (!_firstPersonTypes.includes(n.notification_type)) {
            messageWithLinks = messageWithLinks.replace(/\bYou\b/g, n.artist_name);
            messageWithLinks = messageWithLinks.replace(/\bYour\b/g, artistLink + "'s");
          }
          messageWithLinks = messageWithLinks.replace(new RegExp(`\\b${n.artist_name}\\b`, 'g'), artistLink);
        }
        
        return messageWithLinks;
    }
  }

  // Split a message string at ". Slot" boundary for two-line display
  splitMessage(msg) {
    if (typeof msg === 'object') return msg;
    const str = String(msg || '');
    const slotMatch = str.match(/^(.*?\.\s*)((?:Booked\s+)?Slot\s+.*)$/i);
    if (slotMatch) {
      return { main: slotMatch[1].trim(), detail: slotMatch[2].trim() };
    }
    return { main: str, detail: '' };
  }

  async markAsRead(notificationId) {
    try {
      await fetch(`/api/notifications/${notificationId}/read`, {
        method: 'POST',
        credentials: 'include'
      });
      
      // Update local state
      const notification = this.notifications.find(n => n.id === notificationId);
      if (notification) {
        notification.is_read = true;
      }
      
      this.render();
      this.displayFiltered();
      
      // Update badge with new unread count
      const stats = this.calculateStats();
      const activityBadge = document.getElementById('activityBadge');
      if (activityBadge) {
        activityBadge.textContent = `(${stats.unread})`;
      }
    } catch (error) {
      console.error('Error marking notification as read:', error);
    }
  }
  
  async approvePreferred(artistId, venueId) {
    try {
      // Find the preferred_artists record
      const response = await fetch(`/api/venues/${venueId}/preferred-artists-with-gigs`, {
        credentials: 'include'
      });
      if (response.ok) {
        const artists = await response.json();
        const artist = artists.find(a => a.artist_id === artistId);
        if (artist && artist.preferred_id) {
          const approveResponse = await fetch(`/api/preferred-artists/${artist.preferred_id}/approve`, {
            method: 'PUT',
            credentials: 'include'
          });
          
          // v73: Delete the preferred_request notification
          const requestNotif = this.notifications.find(n => 
            n.notification_type === 'preferred_request' && 
            n.artist_id === artistId && 
            n.venue_id === venueId
          );
          if (requestNotif) {
            await fetch(`/api/notifications/${requestNotif.id}`, {
              method: 'DELETE',
              credentials: 'include'
            });
          }
          
          // v73: FORCE reload notifications and My Artists
          await this.loadNotifications();
          
          if (window.myArtists) {
            await myArtists.loadArtists();
            myArtists.render();
          }
        }
      }
    } catch (error) {
      console.error('❌ v73: Error approving preferred artist:', error);
    }
  }
  
  async denyPreferred(artistId, venueId) {
    try {
      // Find the preferred_artists record
      const response = await fetch(`/api/venues/${venueId}/preferred-artists-with-gigs`, {
        credentials: 'include'
      });
      if (response.ok) {
        const artists = await response.json();
        const artist = artists.find(a => a.artist_id === artistId);
        if (artist && artist.preferred_id) {
          const denyResponse = await fetch(`/api/preferred-artists/${artist.preferred_id}/deny`, {
            method: 'PUT',
            credentials: 'include'
          });
          
          // v73: Delete the preferred_request notification
          const requestNotif = this.notifications.find(n => 
            n.notification_type === 'preferred_request' && 
            n.artist_id === artistId && 
            n.venue_id === venueId
          );
          if (requestNotif) {
            await fetch(`/api/notifications/${requestNotif.id}`, {
              method: 'DELETE',
              credentials: 'include'
            });
          }
          
          // v73: FORCE reload notifications and My Artists
          await this.loadNotifications();
          
          if (window.myArtists) {
            await myArtists.loadArtists();
            myArtists.render();
          }
        }
      }
    } catch (error) {
      console.error('❌ v73: Error denying preferred artist:', error);
    }
  }
  
  async acceptEntityInvite(token, notificationId) {
    try {
      const response = await fetch(`/api/invitations/${token}/accept-existing`, {
        method: 'POST',
        credentials: 'include'
      });
      
      if (response.ok) {
        const result = await response.json();
        
        // Delete the notification
        await fetch(`/api/notifications/${notificationId}`, {
          method: 'DELETE',
          credentials: 'include'
        });
        
        // Show branded success modal
        this.showResultModal('success', `You now have access to ${result.entity_name}!`, () => {
          window.location.href = '/app/user-profile.html';
        });
        
        // Reload notifications
        await this.loadNotifications();
      } else {
        const error = await response.json();
        this.showResultModal('error', error.detail || 'Failed to accept invitation');
      }
    } catch (error) {
      console.error('Error accepting entity invite:', error);
      this.showResultModal('error', 'Failed to accept invitation');
    }
  }
  
  async declineEntityInvite(token, notificationId) {
    try {
      const response = await fetch(`/api/invitations/${token}/decline`, {
        method: 'POST',
        credentials: 'include'
      });
      
      if (response.ok) {
        // Delete the notification
        await fetch(`/api/notifications/${notificationId}`, {
          method: 'DELETE',
          credentials: 'include'
        });
        
        // Show branded modal
        this.showResultModal('info', 'Invitation declined');
        
        // Reload notifications
        await this.loadNotifications();
      } else {
        const error = await response.json();
        this.showResultModal('error', error.detail || 'Failed to decline invitation');
      }
    } catch (error) {
      console.error('Error declining entity invite:', error);
      this.showResultModal('error', 'Failed to decline invitation');
    }
  }
  
  showResultModal(type, message, onClose = null) {
    // Remove any existing result modal
    const existing = document.getElementById('resultModal');
    if (existing) existing.remove();
    
    const isSuccess = type === 'success';
    const isError = type === 'error';
    const icon = isSuccess ? '✓' : isError ? '✕' : 'ℹ';
    const iconColor = isSuccess ? '#22c55e' : isError ? '#ef4444' : '#5b8cff';
    const title = isSuccess ? 'Success!' : isError ? 'Error' : 'Notice';
    
    const overlay = document.createElement('div');
    overlay.id = 'resultModal';
    overlay.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center; z-index: 10000;';
    
    overlay.innerHTML = `
      <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid #7c6bff; border-radius: 12px; padding: 2rem; max-width: 400px; text-align: center; box-shadow: 0 8px 32px rgba(124,107,255,0.4);">
        <div style="font-size: 3rem; margin-bottom: 1rem; color: ${iconColor};">${icon}</div>
        <h2 style="color: #ffffff; margin: 0 0 0.75rem 0; font-size: 1.25rem;">${title}</h2>
        <p style="color: #a1a1aa; margin: 0 0 1.5rem 0; font-size: 0.95rem; line-height: 1.5;">${message}</p>
        <button class="btn primary" style="min-width: 120px;">OK</button>
      </div>
    `;
    
    document.body.appendChild(overlay);
    
    const closeModal = () => {
      overlay.remove();
      if (onClose) onClose();
    };
    
    // Close button handler
    overlay.querySelector('button').onclick = closeModal;
    
    // Click outside to close
    overlay.onclick = (e) => {
      if (e.target === overlay) closeModal();
    };
  }
  
  async openNotificationModal(notificationId) {
    const notification = this.notifications.find(
      n => String(n.id) === String(notificationId)
    );
  
    if (!notification) {
      console.error(
        '❌ ActivityCenter: Notification not found for modal:',
        notificationId,
        this.notifications
      );
      return;
    }
  
    
    // Build modal content
    let content = `
      <div style="margin-bottom: 1rem;">
        <h3 style="color: #5b8cff; margin: 0 0 0.5rem 0;">${notification.title}</h3>
        <p style="margin: 0 0 1rem 0;">${notification.message}</p>
      </div>
    `;
    
    // Add gig details if available
    if (notification.gig_id) {
      content += `
        <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 1rem; margin-bottom: 1rem;">
          <h4 style="margin: 0 0 0.5rem 0;">Gig Details:</h4>
          ${notification.gig_title ? `<p><strong>Title:</strong> ${notification.gig_title}</p>` : ''}
          ${notification.gig_date ? `<p><strong>Date:</strong> ${notification.gig_date}</p>` : ''}
          ${notification.venue_name ? `<p><strong>Venue:</strong> <a href="/app/venue-profile.html?venue_id=${notification.venue_id}" target="_blank" style="color: #7c6bff;">${notification.venue_name}</a></p>` : ''}
          ${notification.artist_name ? `<p><strong>Artist:</strong> <a href="/app/artist-profile.html?artist_id=${notification.artist_id}" target="_blank" style="color: #7c6bff;">${notification.artist_name}</a></p>` : ''}
          ${notification.cancellation_reason ? `<p><strong>Cancellation Reason:</strong> ${notification.cancellation_reason}</p>` : ''}
        </div>
      `;
    }
    
    // Show modal
    this.showModal('Notification Details', content, async () => {
      // Mark as read when modal closes
      await this.markAsRead(notificationId);
    });
  }
  
  showModal(title, content, onClose) {
    // Create modal overlay
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.8); z-index: 10000; display: flex; align-items: center; justify-content: center;';
    
    overlay.innerHTML = `
      <div style="background: var(--bg); border: 1px solid var(--glass-border); border-radius: 12px; padding: 2rem; max-width: 600px; max-height: 80vh; overflow-y: auto;">
        <h2 style="margin: 0 0 1rem 0;">${title}</h2>
        ${content}
        <button class="btn primary" style="margin-top: 1rem;">Close</button>
      </div>
    `;
    
    document.body.appendChild(overlay);
    
    // Close button handler
    const closeBtn = overlay.querySelector('button');
    closeBtn.onclick = () => {
      document.body.removeChild(overlay);
      if (onClose) onClose();
    };
    
    // Click outside to close
    overlay.onclick = (e) => {
      if (e.target === overlay) {
        document.body.removeChild(overlay);
        if (onClose) onClose();
      }
    };
  }

  async loadNotifications() {
    
    try {
      const response = await fetch('/api/notifications', {credentials: 'include'});
      
      if (response.ok) {
        let notifications = await response.json();
        
        // v88: Filter by specific artist_id or venue_id if provided
        if (this.entityId && this.entityType) {
          
          const beforeCount = notifications.length;
          
          if (this.entityType === 'artist') {
            notifications = notifications.filter(n => {
              const currentArtistId = parseInt(this.entityId);
          
              const matchesArtistId =
                n.artist_id && parseInt(n.artist_id) === currentArtistId;
          
              // Venue-only notifications (never show to artist)
              const isVenueSideNotification =
                n.notification_type === 'preferred_request' &&
                n.title === 'New Preferred Artist Request';
          
              // User-level notifications (always show)
              const isUserLevelNotification =
                n.notification_type === 'entity_invite' ||
                n.notification_type === 'entity_invite_accepted' ||
                n.notification_type === 'entity_invite_declined';

          
              const shouldShow =
                (matchesArtistId || isUserLevelNotification) &&
                !isVenueSideNotification;
          
              return shouldShow;
            });

          } else if (this.entityType === 'venue') {
            notifications = notifications.filter(n => {
              // v88: STRICT filtering - notification must have venue_id matching current venue
              const notifVenueId = parseInt(n.venue_id);
              const currentVenueId = parseInt(this.entityId);
              const matchesVenueId = notifVenueId === currentVenueId;
              
              // v88: ALSO exclude artist-side notifications even if they have our venue_id
              // Check the TITLE to determine if it's artist-side vs venue-side
              // Artist-side: "Preferred Status Requested", "Preferred Status Approved", etc.
              // Venue-side: "New Preferred Artist Request", "Preferred Artist Approved", etc.
              const isArtistSideNotification =
                n.notification_type === 'preferred_approved' ||
                n.notification_type === 'preferred_denied' ||
                n.notification_type === 'preferred_revoked';

              // User-level notifications (always show)
              const isUserLevelNotification =
                n.notification_type === 'entity_invite' ||
                n.notification_type === 'entity_invite_accepted' ||
                n.notification_type === 'entity_invite_declined';
              
              const shouldShow = (matchesVenueId || isUserLevelNotification) && !isArtistSideNotification;
              
              if (!shouldShow) {
              } else {
              }
              
              return shouldShow;
            });
          }
          
        } else {
        }
        
        this.notifications = notifications;
        this.render();
        this.displayFiltered();
        
        // v97: Update the activity badge with unread count
        const stats = this.calculateStats();
        const activityBadge = document.getElementById('activityBadge');
        if (activityBadge) {
          activityBadge.textContent = `(${stats.unread})`;
        }
        
        // v97: Fire callback for auto-switching to Activity tab if unread > 0
        if (typeof window.onActivityCenterLoaded === 'function') {
          window.onActivityCenterLoaded(stats.unread, this.entityType);
        }
      }
    } catch (error) {
      console.error('❌ v88 DEBUG: Error loading notifications:', error);
    }
  }
  
  // v97: Get unread count for external use
  getUnreadCount() {
    return this.notifications.filter(n => !n.is_read).length;
  }

  // ── Real-time polling ───────────────────────────────────────────────────────
  // Polls /api/notifications every POLL_INTERVAL ms.
  // Pauses automatically when the tab is hidden and resumes when visible.
  // Shows a subtle "pulse" on the badge when new unread notifications arrive.

  startPolling(intervalMs = 30000) {
    if (this._pollTimer) return; // already polling
    this._pollInterval = intervalMs;
    this._lastUnreadCount = this.getUnreadCount();

    const tick = async () => {
      if (document.visibilityState === 'hidden') return; // skip hidden tab
      const prevUnread = this._lastUnreadCount;
      await this.loadNotifications();
      const newUnread = this.getUnreadCount();
      if (newUnread > prevUnread) {
        this._pulseActivityBadge();
      }
      this._lastUnreadCount = newUnread;
    };

    this._pollTimer = setInterval(tick, this._pollInterval);

    // Pause/resume on tab visibility change
    this._visibilityHandler = () => {
      if (document.visibilityState === 'visible') {
        // Run an immediate refresh when the user returns to the tab
        tick();
      }
    };
    document.addEventListener('visibilitychange', this._visibilityHandler);

    // Clean up on page unload
    window.addEventListener('beforeunload', () => this.stopPolling(), { once: true });
  }

  stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
    if (this._visibilityHandler) {
      document.removeEventListener('visibilitychange', this._visibilityHandler);
      this._visibilityHandler = null;
    }
  }

  _pulseActivityBadge() {
    // Brief cyan-glow pulse on the activity badge to signal new notifications
    const badge = document.getElementById('activityBadge');
    if (!badge) return;
    badge.style.transition = 'color 0.2s, text-shadow 0.2s';
    badge.style.color = '#06b6d4';
    badge.style.textShadow = '0 0 8px rgba(6,182,212,0.8)';
    setTimeout(() => {
      badge.style.color = '';
      badge.style.textShadow = '';
    }, 2000);
  }
}

// v88 FIX: explicitly bind globals for inline onclick handlers
window.activityCenter = window.activityCenter || null;
window.activityCenterVenue = window.activityCenterVenue || null;
