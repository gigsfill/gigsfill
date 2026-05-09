import { apiGet, apiPost, apiDelete } from "./api.js";

document.addEventListener("DOMContentLoaded", async () => {
  let allNotifications = [];
  let filteredNotifications = [];

  // Load all notifications
  async function loadNotifications() {
    try {
      allNotifications = await apiGet("/api/notifications");
      filteredNotifications = [...allNotifications];
      renderStats();
      renderNotifications();
    } catch (error) {
      console.error("Failed to load notifications:", error);
      document.getElementById("notificationsList").innerHTML = 
        '<p style="color: #ef4444; text-align: center;">Failed to load notifications</p>';
    }
  }

  // Render summary stats
  function renderStats() {
    const stats = {
      total: allNotifications.length,
      unread: allNotifications.filter(n => !n.is_read).length,
      read: allNotifications.filter(n => n.is_read).length,
      today: allNotifications.filter(n => {
        const notifDate = new Date(n.created_at);
        const today = new Date();
        return notifDate.toDateString() === today.toDateString();
      }).length
    };

    const container = document.getElementById("summaryStats");
    container.innerHTML = `
      <div style="background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 8px; padding: 12px; text-align: center;">
        <p style="color: #3b82f6; font-size: 1rem; margin: 0; font-weight: 600;">Total: ${stats.total}</p>
      </div>
      
      <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 12px; text-align: center;">
        <p style="color: #ef4444; font-size: 1rem; margin: 0; font-weight: 600;">Unread: ${stats.unread}</p>
      </div>
      
      <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 12px; text-align: center;">
        <p style="color: #10b981; font-size: 1rem; margin: 0; font-weight: 600;">Read: ${stats.read}</p>
      </div>
      
      <div style="background: rgba(234, 179, 8, 0.1); border: 1px solid rgba(234, 179, 8, 0.3); border-radius: 8px; padding: 12px; text-align: center;">
        <p style="color: #eab308; font-size: 1rem; margin: 0; font-weight: 600;">Today: ${stats.today}</p>
      </div>
    `;
  }

  // Render notifications list
  function renderNotifications() {
    const container = document.getElementById("notificationsList");
    
    if (!filteredNotifications.length) {
      container.innerHTML = `
        <div style="text-align: center; padding: 48px;">
          <p style="font-size: 3rem; margin-bottom: 16px;">🔔</p>
          <p style="color: var(--text-muted); font-size: 1.1rem;">No notifications found</p>
        </div>
      `;
      return;
    }

    let html = '<div style="display: grid; gap: 12px;">';
    
    filteredNotifications.forEach(notif => {
      const isUnread = !notif.is_read;
      const bgColor = isUnread ? 'rgba(59, 130, 246, 0.1)' : 'rgba(255, 255, 255, 0.02)';
      const borderColor = isUnread ? 'rgba(59, 130, 246, 0.3)' : 'rgba(255, 255, 255, 0.08)';
      
      // Determine icon based on notification type
      let icon = '📬';
      if (notif.notification_type === 'gig_cancelled') icon = '❌';
      if (notif.notification_type === 'preferred_approved') icon = '✅';
      if (notif.notification_type === 'preferred_denied') icon = '❌';
      if (notif.notification_type === 'preferred_request') icon = '📋';
      if (notif.notification_type === 'gig_booked') icon = '🎉';

      // Format timestamp
      const date = new Date(notif.created_at);
      const timeAgo = getTimeAgo(date);
      const fullDate = date.toLocaleString();

      // Build message with hyperlinks and cancellation reason
      let enhancedMessage = notif.message;
      
      // Add hyperlink to venue name if present
      if (notif.venue_id && notif.venue_name) {
        const venueLink = `<a href="/app/venue-profile.html?venue_id=${notif.venue_id}" target="_blank" style="color: var(--accent-cyan); text-decoration: none;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'">${notif.venue_name}</a>`;
        enhancedMessage = enhancedMessage.replace(notif.venue_name, venueLink);
      }
      
      // Add hyperlink to artist name if present
      if (notif.artist_id && notif.artist_name) {
        const artistLink = `<a href="/app/artist-profile.html?artist_id=${notif.artist_id}" target="_blank" style="color: var(--accent-cyan); text-decoration: none;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'">${notif.artist_name}</a>`;
        enhancedMessage = enhancedMessage.replace(notif.artist_name, artistLink);
      }
      
      // Add cancellation reason if this is a cancellation notification
      let cancellationReasonHtml = '';
      if (notif.notification_type === 'gig_cancelled' && notif.cancellation_reason) {
        cancellationReasonHtml = `
          <div style="margin-top: 12px; padding: 12px; background: rgba(239, 68, 68, 0.1); border-left: 3px solid #ef4444; border-radius: 4px;">
            <strong style="color: #ef4444; font-size: 0.85rem;">Cancellation Reason:</strong>
            <p style="color: var(--text-muted); margin: 4px 0 0 0; font-size: 0.85rem; line-height: 1.5;">${notif.cancellation_reason}</p>
          </div>
        `;
      }

      html += `
        <div 
          style="
            background: ${bgColor};
            border: 1px solid ${borderColor};
            ${isUnread ? 'border-left: 4px solid #3b82f6;' : ''}
            border-radius: 8px;
            padding: 16px;
            cursor: pointer;
            transition: all 0.2s;
          "
          onmouseover="this.style.background='rgba(255,255,255,0.06)'"
          onmouseout="this.style.background='${bgColor}'"
          onclick="markAsRead(${notif.id})"
        >
          <div style="display: flex; justify-content: space-between; align-items: start; gap: 12px;">
            <div style="flex: 1;">
              <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
                <span style="font-size: 1.5rem;">${icon}</span>
                <div style="flex: 1;">
                  <strong style="color: ${isUnread ? '#3b82f6' : '#ffffff'}; font-size: 1.1rem; display: block;">${notif.title}</strong>
                  <span style="font-size: 0.75rem; color: var(--text-muted);">${fullDate}</span>
                </div>
                ${isUnread ? '<span style="background: #3b82f6; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.7rem;">NEW</span>' : ''}
              </div>
              
              <p style="color: var(--text-muted); margin: 8px 0; line-height: 1.6;">${enhancedMessage}</p>
              
              ${cancellationReasonHtml}
              
              <div style="display: flex; gap: 12px; margin-top: 12px; font-size: 0.8rem; color: var(--text-muted);">
                <span>⏰ ${timeAgo}</span>
                ${notif.venue_name ? `<span>🏢 ${notif.venue_name}</span>` : ''}
                ${notif.artist_name ? `<span>🎤 ${notif.artist_name}</span>` : ''}
              </div>
            </div>
            
            <button 
              class="btn danger small"
              onclick="event.stopPropagation(); deleteNotification(${notif.id})"
              style="font-size: 0.75rem;"
            >Delete</button>
          </div>
        </div>
      `;
    });
    
    html += '</div>';
    container.innerHTML = html;
  }

  // Mark notification as read
  window.markAsRead = async function(notifId) {
    try {
      await apiPost(`/api/notifications/${notifId}/read`, {});
      await loadNotifications();
    } catch (error) {
      console.error("Failed to mark as read:", error);
    }
  };

  // Delete notification
  window.deleteNotification = async function(notifId) {
    if (!confirm("Delete this notification?")) return;
    
    try {
      await apiDelete(`/api/notifications/${notifId}`);
      await loadNotifications();
    } catch (error) {
      console.error("Failed to delete notification:", error);
      alert("Failed to delete notification");
    }
  };

  // Mark all as read
  document.getElementById("markAllRead").onclick = async () => {
    try {
      await apiPost("/api/notifications/mark-all-read", {});
      await loadNotifications();
    } catch (error) {
      console.error("Failed to mark all as read:", error);
      alert("Failed to mark all as read");
    }
  };

  // Filter by type
  document.getElementById("typeFilter").addEventListener("change", (e) => {
    const type = e.target.value;
    applyFilters();
  });

  // Filter by read status
  document.getElementById("readFilter").addEventListener("change", (e) => {
    applyFilters();
  });

  // Apply filters
  function applyFilters() {
    const typeFilter = document.getElementById("typeFilter").value;
    const readFilter = document.getElementById("readFilter").value;
    
    filteredNotifications = allNotifications.filter(notif => {
      // Type filter
      if (typeFilter !== 'all' && notif.notification_type !== typeFilter) {
        return false;
      }
      
      // Read status filter
      if (readFilter === 'unread' && notif.is_read) {
        return false;
      }
      if (readFilter === 'read' && !notif.is_read) {
        return false;
      }
      
      return true;
    });
    
    renderNotifications();
  }

  // Helper function to format time ago
  function getTimeAgo(date) {
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);
    
    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    if (seconds < 2592000) return `${Math.floor(seconds / 604800)}w ago`;
    return date.toLocaleDateString();
  }

  // Initial load
  await loadNotifications();
});
