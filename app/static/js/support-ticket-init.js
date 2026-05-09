// support-ticket-init.js — User-facing ticket conversation page
// Loaded via token link from support reply emails (no login required)

(function() {
  const params = new URLSearchParams(window.location.search);
  const ticketId = params.get('id');
  const token = params.get('token');
  const container = document.getElementById('ticketApp');

  if (!ticketId || !token) {
    container.innerHTML = '<div class="error">Invalid link. Please use the link from your support email.</div>';
    return;
  }

  let ticketData = null;

  loadTicket();

  async function loadTicket() {
    try {
      const resp = await fetch(`/api/support/ticket/${ticketId}?token=${encodeURIComponent(token)}`);
      if (resp.status === 403) {
        container.innerHTML = '<div class="error">This link is invalid or has expired. Please check your email for the correct link.</div>';
        return;
      }
      if (resp.status === 404) {
        container.innerHTML = '<div class="error">Ticket not found. It may have been removed, or the server may need to be restarted with the latest update.</div>';
        return;
      }
      if (!resp.ok) {
        const errText = await resp.text().catch(() => '');
        container.innerHTML = `<div class="error">Unable to load ticket (${resp.status}). ${errText ? errText.substring(0, 100) : 'Please try again.'}</div>`;
        return;
      }
      ticketData = await resp.json();
      render();
    } catch (e) {
      container.innerHTML = '<div class="error">Unable to load ticket. Please try again later.</div>';
    }
  }

  function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
      const d = new Date(dateStr + (dateStr.includes('Z') || dateStr.includes('+') ? '' : 'Z'));
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) + 
             ' ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    } catch { return dateStr; }
  }

  function render() {
    const t = ticketData.ticket;
    const replies = ticketData.replies || [];
    const isClosed = t.status === 'closed';

    let html = '';

    // Header
    html += `
      <div class="ticket-header">
        <h2>Ticket #${t.id} — ${t.subject || 'Support Request'}</h2>
        <div class="ticket-meta">
          <span>${t.category || ''}</span>
          <span>${formatDate(t.created_at)}</span>
          <span class="status ${t.status === 'closed' ? 'status-closed' : 'status-open'}">${t.status || 'open'}</span>
        </div>
      </div>`;

    // Thread
    html += '<div class="thread">';

    // Original message
    const userName = t.user_name || 'You';
    const userInitial = userName.charAt(0).toUpperCase();
    html += `
      <div class="msg msg-user">
        <div class="msg-header">
          <div class="msg-avatar">${userInitial}</div>
          <span class="msg-name">${userName}</span>
          <span class="msg-date">${formatDate(t.created_at)}</span>
          <span class="msg-badge">Original</span>
        </div>
        <div class="msg-body">${escapeHtml(t.description || '')}</div>
      </div>`;

    // Replies
    for (const r of replies) {
      const isAdmin = r.sender_type === 'admin';
      const cls = isAdmin ? 'msg-admin' : 'msg-user';
      const avatar = isAdmin ? 'S' : userInitial;
      const name = isAdmin ? (r.sender_name || 'GigsFill Support') : userName;
      const badge = isAdmin ? 'Support' : 'You';

      html += `
        <div class="msg ${cls}">
          <div class="msg-header">
            <div class="msg-avatar">${avatar}</div>
            <span class="msg-name">${escapeHtml(name)}</span>
            <span class="msg-date">${formatDate(r.created_at)}</span>
            <span class="msg-badge">${badge}</span>
          </div>
          <div class="msg-body">${escapeHtml(r.body || '')}</div>
        </div>`;
    }

    html += '</div>';

    // Reply box or closed banner
    if (isClosed) {
      html += '<div class="closed-banner">This ticket has been closed. If you need further help, please submit a new ticket.</div>';
    } else {
      html += `
        <div class="reply-box">
          <textarea id="replyBody" placeholder="Type your reply..."></textarea>
          <div class="reply-actions">
            <span class="hint">Your reply will be sent to GigsFill Support</span>
            <button class="btn-send" id="sendBtn" onclick="window._sendReply()">Send Reply</button>
          </div>
          <div id="replyStatus" style="margin-top: 8px; font-size: 0.75rem; display: none;"></div>
        </div>`;
    }

    container.innerHTML = html;

    // Scroll to bottom if there are replies
    if (replies.length > 0) {
      const thread = container.querySelector('.thread');
      if (thread) thread.scrollTop = thread.scrollHeight;
    }
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  window._sendReply = async function() {
    const textarea = document.getElementById('replyBody');
    const btn = document.getElementById('sendBtn');
    const status = document.getElementById('replyStatus');
    const body = (textarea ? textarea.value : '').trim();

    if (!body) {
      textarea.style.borderColor = '#ef4444';
      setTimeout(() => { textarea.style.borderColor = '#333'; }, 2000);
      return;
    }

    btn.disabled = true;
    btn.textContent = 'Sending...';
    status.style.display = 'none';

    try {
      const resp = await fetch(`/api/support/ticket/${ticketId}/reply?token=${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body })
      });

      const result = await resp.json();

      if (resp.ok) {
        textarea.value = '';
        status.style.display = 'block';
        status.style.color = '#10b981';
        status.textContent = result.email_sent 
          ? '✓ Reply sent! Support has been notified.' 
          : '✓ Reply saved. (Email notification could not be sent)';
        
        // Reload to show the new message in thread
        const reloadResp = await fetch(`/api/support/ticket/${ticketId}?token=${encodeURIComponent(token)}`);
        if (reloadResp.ok) {
          ticketData = await reloadResp.json();
          render();
          // Scroll to bottom
          const thread = container.querySelector('.thread');
          if (thread) thread.scrollTop = thread.scrollHeight;
        }
      } else {
        status.style.display = 'block';
        status.style.color = '#ef4444';
        status.textContent = result.detail || 'Failed to send reply. Please try again.';
      }
    } catch (e) {
      status.style.display = 'block';
      status.style.color = '#ef4444';
      status.textContent = 'Network error. Please try again.';
    }

    btn.disabled = false;
    btn.textContent = 'Send Reply';
  };
})();
