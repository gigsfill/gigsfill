/**
 * GigsFill — In-App Gig Messaging
 * ================================
 * Renders message threads per gig.
 * Handles sending, polling for new messages, and unread badge in nav.
 *
 * Usage:
 *   await openMessageThread(gigId, containerId);
 *   startUnreadBadgePolling();   // call once on page load
 */

let _messageThreadGigId = null;
let _messageThreadContainer = null;
let _messagePoller = null;
let _lastMessageId = 0;

// ── OPEN MESSAGE THREAD ────────────────────────────────────────────────────

async function openMessageThread(gigId, containerId, artistId, venueId) {
  _messageThreadGigId = gigId;
  _messageThreadContainer = containerId;
  _messageThreadArtistId = artistId || null;
  _messageThreadVenueId = venueId || null;
  _lastMessageId = 0;

  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = `
    <div style="height:400px;display:flex;flex-direction:column;border:1px solid var(--border);border-radius:10px;overflow:hidden;">
      <div id="msgHeader_${gigId}" style="background:var(--card);padding:10px 14px;border-bottom:1px solid var(--border);font-size:0.8rem;font-weight:600;color:var(--text);">
        Loading...
      </div>
      <div id="msgList_${gigId}" style="flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px;background:var(--bg);">
        <div style="color:var(--text-gray);font-size:0.8rem;text-align:center;">Loading messages...</div>
      </div>
      <div style="padding:10px;background:var(--card);border-top:1px solid var(--border);display:flex;gap:8px;">
        <textarea id="msgInput_${gigId}" placeholder="Type a message..."
          style="flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 10px;
                 color:var(--text);font-size:0.8rem;resize:none;height:38px;font-family:inherit;"
          onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendGigMessage(${gigId});}"></textarea>
        <button onclick="sendGigMessage(${gigId})"
          style="background:var(--cyan);color:#fff;border:none;border-radius:8px;padding:0 16px;
                 font-weight:600;font-size:0.8rem;cursor:pointer;white-space:nowrap;">
          Send
        </button>
      </div>
    </div>
  `;

  await _loadMessages(gigId);
  await _markThreadRead(gigId);
  _startMessagePolling(gigId);
}


// ── LOAD / REFRESH MESSAGES ────────────────────────────────────────────────

async function _loadMessages(gigId) {
  try {
    const _aid = window._messageThreadArtistId;
    const res = await fetch(`/api/gigs/${gigId}/messages${_aid ? '?artist_id=' + _aid : ''}`, { credentials: 'include' });
    if (!res.ok) {
      if (res.status === 401) {
        const body = document.getElementById(`msgBody_${gigId}`);
        if (body) body.innerHTML = '<div style="text-align:center;padding:32px;"><div style="color:#f59e0b;font-size:0.9rem;font-weight:600;margin-bottom:10px;">Session expired</div><div style="color:#888;font-size:0.82rem;margin-bottom:16px;">Please log in again.</div><button onclick="window.location.href=\'/app/login.html\'" class="btn primary" style="font-size:0.82rem;">Go to Login</button></div>';
        return;
      }
      throw new Error('Failed to load');
    }
    const data = await res.json();

    // Update header
    const header = document.getElementById(`msgHeader_${gigId}`);
    if (header && data.gig) {
      header.innerHTML = `
        <span>💬 Messages</span>
        <span style="font-weight:400;color:var(--text-gray);margin-left:8px;">
          ${esc(data.gig.venue_name || '')} &amp; ${esc(data.gig.artist_name || 'Artist')}
          · ${esc((data.gig.gig_date || data.gig.date || '').slice(0, 10))}
        </span>
      `;
    }

    // Render messages
    const list = document.getElementById(`msgList_${gigId}`);
    if (!list) return;

    const msgs = data.messages || [];
    if (msgs.length === 0) {
      list.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;text-align:center;padding:20px 0;">No messages yet. Start the conversation!</div>`;
      return;
    }

    list.innerHTML = msgs.map(m => _renderMessage(m)).join('');

    // Track last message ID for polling
    if (msgs.length > 0) {
      _lastMessageId = msgs[msgs.length - 1].id;
    }

    // Scroll to bottom
    list.scrollTop = list.scrollHeight;

  } catch (e) {
    const list = document.getElementById(`msgList_${gigId}`);
    if (list) list.innerHTML = `<div style="color:#ef4444;font-size:0.8rem;text-align:center;">Could not load messages</div>`;
  }
}


// ── RENDER A SINGLE MESSAGE ────────────────────────────────────────────────

function _renderMessage(m) {
  const isMine = m.is_mine === 1 || m.is_mine === true;
  const time = m.created_at ? new Date(m.created_at + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  const date = m.created_at ? new Date(m.created_at + 'Z').toLocaleDateString([], { month: 'short', day: 'numeric' }) : '';

  return `
    <div style="display:flex;flex-direction:column;align-items:${isMine ? 'flex-end' : 'flex-start'};">
      <div style="max-width:75%;">
        ${!isMine ? `<div style="font-size:0.68rem;color:var(--text-gray);margin-bottom:2px;padding-left:4px;">${esc(m.sender_name)}</div>` : ''}
        <div style="
          background:${isMine ? 'var(--cyan)' : 'var(--card)'};
          color:${isMine ? '#fff' : 'var(--text)'};
          border:${isMine ? 'none' : '1px solid var(--border)'};
          border-radius:${isMine ? '12px 12px 4px 12px' : '12px 12px 12px 4px'};
          padding:8px 12px;font-size:0.82rem;line-height:1.5;
          word-break:break-word;
        ">${esc(m.body).replace(/\n/g, '<br>')}</div>
        <div style="font-size:0.65rem;color:var(--text-gray);margin-top:2px;padding:0 4px;">${date} ${time}</div>
      </div>
    </div>
  `;
}


// ── SEND MESSAGE ──────────────────────────────────────────────────────────

window.sendGigMessage = async function(gigId) {
  const input = document.getElementById(`msgInput_${gigId}`);
  const body = input?.value?.trim();
  if (!body) return;

  input.value = '';
  input.disabled = true;

  try {
    const payload = { body };
    // Include artist_id so the backend knows which artist this venue message targets
    const _aid = window._messageThreadArtistId;
    if (_aid) payload.artist_id = _aid;
    // Include venue_id so the backend knows which venue is sending (multi-venue users)
    const _vid = window._messageThreadVenueId;
    if (_vid) payload.venue_id = _vid;
    const res = await fetch(`/api/gigs/${gigId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('Failed to send');
    await _loadMessages(gigId);
    await _markThreadRead(gigId);
  } catch (e) {
    if (input) input.value = body; // restore on failure
    console.error('Send failed:', e);
  } finally {
    if (input) input.disabled = false;
    if (input) input.focus();
  }
};


// ── MARK AS READ ──────────────────────────────────────────────────────────

async function _markThreadRead(gigId) {
  try {
    await fetch(`/api/gigs/${gigId}/messages/read`, {
      method: 'PUT',
      credentials: 'include'
    });
    updateUnreadBadge(); // refresh nav badge
  } catch (e) {}
}


// ── POLLING ───────────────────────────────────────────────────────────────

function _startMessagePolling(gigId, intervalMs = 15000) {
  if (_messagePoller) clearInterval(_messagePoller);
  _messagePoller = setInterval(async () => {
    if (_messageThreadGigId !== gigId) {
      clearInterval(_messagePoller);
      return;
    }
    await _loadMessages(gigId);
    await _markThreadRead(gigId);
  }, intervalMs);
}

function stopMessagePolling() {
  if (_messagePoller) {
    clearInterval(_messagePoller);
    _messagePoller = null;
  }
}


// ── NAV UNREAD BADGE ──────────────────────────────────────────────────────

let _unreadBadgePoller = null;

async function updateUnreadBadge(scope = {}) {
  try {
    const p = new URLSearchParams();
    if (scope.venue_id)  p.set('venue_id',  scope.venue_id);
    if (scope.artist_id) p.set('artist_id', scope.artist_id);
    const url = '/api/me/messages/unread-count' + (p.toString() ? '?' + p.toString() : '');
    const res = await fetch(url, { credentials: 'include' });
    if (!res.ok) return;
    const { unread } = await res.json();

    // Update all elements with class 'msg-unread-badge'
    document.querySelectorAll('.msg-unread-badge').forEach(el => {
      if (unread > 0) {
        el.textContent = unread > 99 ? '99+' : String(unread);
        el.style.display = '';
      } else {
        el.style.display = 'none';
      }
    });
  } catch (e) {}
}

/**
 * Start polling for unread count (for nav badge).
 * Call once on page load for authenticated users.
 * @param {number} intervalMs  Poll interval in ms (default 30s)
 * @param {object} scope       { venue_id } or { artist_id } to scope the count
 */
function startUnreadBadgePolling(intervalMs = 30000, scope = {}) {
  updateUnreadBadge(scope); // immediate
  if (_unreadBadgePoller) clearInterval(_unreadBadgePoller);
  _unreadBadgePoller = setInterval(() => updateUnreadBadge(scope), intervalMs);
}


// ── MESSAGE THREAD IN A MODAL ─────────────────────────────────────────────

/**
 * Open a message thread in a modal overlay.
 * @param {number} gigId
 * @param {string} title  Optional modal title
 */
function openMessageModal(gigId, title, artistId, venueId) {
  // If venueId not passed, try to read from URL (venue page)
  if (!venueId) {
    const _urlP = new URLSearchParams(window.location.search);
    const _urlVid = _urlP.get('venue_id');
    if (_urlVid) venueId = parseInt(_urlVid);
  }
  // Remove existing modal if any
  const existing = document.getElementById('gigMessageModal');
  if (existing) existing.remove();
  stopMessagePolling();

  const modal = document.createElement('div');
  modal.id = 'gigMessageModal';
  modal.style.cssText = `
    position:fixed;top:0;left:0;right:0;bottom:0;
    background:rgba(0,0,0,0.7);z-index:9999;
    display:flex;align-items:center;justify-content:center;
    padding:20px;box-sizing:border-box;
  `;

  modal.innerHTML = `
    <div style="background:var(--bg, #0f1117);border-radius:12px;width:100%;max-width:600px;
                max-height:90vh;overflow:hidden;display:flex;flex-direction:column;
                box-shadow:0 20px 60px rgba(0,0,0,0.5);">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;
                  border-bottom:1px solid var(--border);">
        <span style="font-size:0.9rem;font-weight:600;color:var(--text);">
          💬 ${title ? esc(title) : 'Message Thread'}
        </span>
        <button onclick="closeMessageModal(); if(window._msgInboxSide) openInboxModal({side:window._msgInboxSide, artistId:window._msgInboxArtistId||null, venueId:window._msgInboxVenueId||null});"
          style="background:none;border:none;color:var(--text-gray);font-size:1.2rem;cursor:pointer;" title="Back to Messages">← Back</button>
      </div>
      <div id="msgModalBody" style="flex:1;overflow:hidden;padding:0;">
      </div>
    </div>
  `;

  document.body.appendChild(modal);

  // Use a container div inside the modal
  const body = document.getElementById('msgModalBody');
  const threadDiv = document.createElement('div');
  threadDiv.id = `msgModalThread_${gigId}`;
  threadDiv.style.cssText = 'height:100%;';
  body.appendChild(threadDiv);

  // Render thread without outer border (since modal provides it)
  openMessageThread(gigId, `msgModalThread_${gigId}`, artistId, venueId);

  // Close on backdrop click
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeMessageModal();
  });
}

window.closeMessageModal = function() {
  stopMessagePolling();
  const modal = document.getElementById('gigMessageModal');
  if (modal) modal.remove();
};


// ── INBOX MODAL (shared: venue + artist) ─────────────────────────────────────
// Call: openInboxModal({ side: 'venue'|'artist', artistId: null })
window.openInboxModal = async function({ side = 'venue', artistId = null, venueId = null } = {}) {
  window._msgInboxSide = side;  // remember for Back button
  window._msgInboxArtistId = artistId;  // remember for Back button
  window._msgInboxVenueId = venueId;  // remember for Back button
  const existing = document.getElementById('msgInboxModal');
  if (existing) existing.remove();

  // Create modal skeleton with loading state
  const modal = document.createElement('div');
  modal.id = 'msgInboxModal';
  modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.75);z-index:9998;display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box;';
  modal.innerHTML = `
    <div id="msgInboxInner" style="background:var(--bg,#0f1117);border-radius:14px;width:100%;max-width:860px;max-height:88vh;display:flex;flex-direction:column;box-shadow:0 24px 64px rgba(0,0,0,0.6);overflow:hidden;">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:18px 22px;border-bottom:1px solid var(--border,#1e2433);">
        <span style="font-weight:700;font-size:1.05rem;color:var(--text,#f0f0f0);">Messages</span>
        <button onclick="document.getElementById('msgInboxModal').remove()" style="background:none;border:none;color:var(--text-gray,#888);font-size:1.4rem;cursor:pointer;line-height:1;">✕</button>
      </div>
      <div id="msgInboxBody" style="overflow-y:auto;flex:1;padding:16px 22px;">
        <div style="color:var(--text-muted,#666);text-align:center;padding:32px;">Loading messages…</div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });

  try {
    const _inboxParams = new URLSearchParams();
    if (artistId) _inboxParams.set('artist_id', artistId);
    if (venueId)  _inboxParams.set('venue_id', venueId);
    const msgUrl = '/api/me/messages' + (_inboxParams.toString() ? '?' + _inboxParams.toString() : '');
    const res = await fetch(msgUrl, { credentials: 'include' });
    const body = document.getElementById('msgInboxBody');
    if (!res.ok) {
      if (res.status === 401) {
        body.innerHTML = '<div style="text-align:center;padding:32px;"><div style="color:#f59e0b;font-size:0.95rem;font-weight:600;margin-bottom:12px;">Session expired</div><div style="color:#888;font-size:0.85rem;margin-bottom:18px;">Please log in again to view your messages.</div><button onclick="window.location.href=\'/app/login.html\'" class="btn primary" style="font-size:0.85rem;">Go to Login</button></div>';
      } else {
        body.innerHTML = '<div style="color:#ef4444;text-align:center;padding:32px;">Failed to load messages. Please try again.</div>';
      }
      return;
    }
    const msgs = await res.json();

    // left-aligned header + td styles
    const hdrStyle = 'padding:8px 10px;font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-gray,#888);border-bottom:1px solid var(--border,#1e2433);white-space:nowrap;cursor:pointer;user-select:none;text-align:left;';
    const hdrActive = hdrStyle + 'color:var(--cyan,#06b6d4);';
    const td  = 'padding:10px 10px;font-size:0.8rem;color:var(--text,#f0f0f0);border-bottom:1px solid rgba(255,255,255,0.05);vertical-align:middle;text-align:left;';

    const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

    // Profile URL helpers — artist page on venue side, venue page on artist side
    const artistProfileUrl = id => id ? `/app/artist-profile.html?artist_id=${id}` : null;
    const venueProfileUrl  = id => id ? `/app/venue-profile.html?venue_id=${id}`   : null;

    // Each row = latest message per gig thread (grouped inbox)
    // Columns: Msg Date | From | To (with profile link) | Gig Date | Latest Message

    if (!msgs.length) {
      body.innerHTML = `<table style="width:100%;border-collapse:collapse;">
        <thead><tr>
          <th style="${hdrStyle}">Msg Date</th>
          <th style="${hdrStyle}">From</th>
          <th style="${hdrStyle}">To</th>
          <th style="${hdrStyle}">Gig Date</th>
          <th style="${hdrStyle}">Latest Message</th>
        </tr></thead>
        <tbody><tr><td colspan="5" style="padding:32px;text-align:center;color:var(--text-muted,#666);font-size:0.85rem;">
          No messages yet. Open a booked gig and click "Message ${side === 'venue' ? 'Artist' : 'Venue'}" to start a conversation.
        </td></tr></tbody></table>`;
      return;
    }

    let sortCol = 'msg_date';
    let sortDir = -1;

    function _renderInbox(data, col, dir) {
      const sorted = [...data].sort((a, b) => {
        let av, bv;
        if      (col === 'msg_date')  { av = a.created_at || ''; bv = b.created_at || ''; }
        else if (col === 'gig_date')  { av = a.gig_date   || ''; bv = b.gig_date   || ''; }
        else if (col === 'to')        { av = side === 'venue' ? (a.artist_name||'') : (a.venue_name||''); bv = side === 'venue' ? (b.artist_name||'') : (b.venue_name||''); }
        else if (col === 'from')      { av = a.sender_name || ''; bv = b.sender_name || ''; }
        else                          { av = a.body || '';         bv = b.body || ''; }
        return dir * (av < bv ? -1 : av > bv ? 1 : 0);
      });

      const mkArrow = c => c !== col ? ' <span style="opacity:0.3;">↕</span>' : (dir === -1 ? ' ▼' : ' ▲');
      const th = (c, label) => `<th style="${c===col?hdrActive:hdrStyle}" onclick="window._msgInboxSort('${c}')">${label}${mkArrow(c)}</th>`;

      let h = `<table style="width:100%;border-collapse:collapse;" id="msgInboxTable">
        <thead><tr>
          ${th('msg_date','Msg Date')}
          ${th('from','From')}
          ${th('to','To')}
          ${th('gig_date','Gig Date')}
          ${th('msg','Latest Message')}
        </tr></thead><tbody>`;

      for (const m of sorted) {
        // "To" = the counterparty (who the current user is messaging)
        const toName = side === 'venue'
          ? (m.artist_name || 'Artist')
          : (m.venue_name  || 'Venue');
        const toUrl = side === 'venue'
          ? artistProfileUrl(m.artist_id)
          : venueProfileUrl(m.venue_id);

        const fromName = m.sender_name || (m.sender_type === 'venue' ? (m.venue_name||'Venue') : (m.artist_name||'Artist'));

        let msgDt = '—';
        if (m.created_at) {
          const d = new Date(m.created_at.includes('T') ? m.created_at : m.created_at + 'Z');
          msgDt = d.toLocaleDateString([], {month:'short',day:'numeric',year:'2-digit'}) + ' ' +
                  d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
        }

        const gigDate = m.gig_date ? m.gig_date.slice(0,10) : '—';
        const preview = (m.body||'').length > 90 ? m.body.slice(0,90)+'…' : (m.body||'');
        const unread  = (m.unread_count > 0)
          ? `<span style="display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 4px;background:#635bff;border-radius:9999px;font-size:0.65rem;font-weight:700;color:#fff;margin-left:6px;">${m.unread_count}</span>`
          : '';

        const toCell = toUrl
          ? `<a href="${toUrl}" target="_blank" style="color:var(--cyan,#06b6d4);text-decoration:none;" onclick="event.stopPropagation()">${esc(toName)}</a>`
          : esc(toName);

        const gigTitle = m.gig_title ? `<div style="font-size:0.7rem;color:var(--text-muted,#666);margin-top:2px;">${esc(m.gig_title)}</div>` : '';

        h += `<tr style="cursor:pointer;" onclick="document.getElementById('msgInboxModal').remove(); openMessageModal(${m.gig_id}, '${esc(toName)}', ${m.artist_id || null})" onmouseover="this.style.background='rgba(255,255,255,0.04)'" onmouseout="this.style.background=''">
          <td style="${td}white-space:nowrap;font-size:0.75rem;">${msgDt}</td>
          <td style="${td}white-space:nowrap;">${esc(fromName)}${unread}</td>
          <td style="${td}">${toCell}${gigTitle}</td>
          <td style="${td}white-space:nowrap;">${gigDate}</td>
          <td style="${td}max-width:260px;color:var(--text-muted,#aaa);font-style:italic;">${esc(preview)}</td>
        </tr>`;
      }
      h += '</tbody></table>';
      return h;
    }

    window._msgInboxSort = function(col) {
      if (sortCol === col) sortDir *= -1;
      else { sortCol = col; sortDir = col === 'msg_date' ? -1 : 1; }
      const b2 = document.getElementById('msgInboxBody');
      if (b2) b2.innerHTML = _renderInbox(msgs, sortCol, sortDir);
    };

    body.innerHTML = _renderInbox(msgs, sortCol, sortDir);
  } catch(e) {
    const body = document.getElementById('msgInboxBody');
    if (body) body.innerHTML = '<div style="color:#ef4444;text-align:center;padding:32px;">Error loading messages.</div>';
  }
};

function _fmt12(t) {
  if (!t) return '';
  const parts = String(t).split(':');
  let h = parseInt(parts[0], 10);
  const m = parts[1] || '00';
  const ampm = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${h}:${m} ${ampm}`;
}
