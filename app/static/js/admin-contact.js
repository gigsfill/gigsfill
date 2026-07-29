/* Admin panel — Contact Messages tab.
 *
 * Renders the "Send us a note" inbox from the homepage. Each row has
 * three actions: Mark replied (transitions status), Archive, Delete.
 * A subject-less "Reply" button opens the user's mail client with the
 * sender's address pre-filled — admin does the actual reply from
 * their own inbox.
 *
 * Auto-refreshes the tab badge (unread `new` count) on load + every
 * time the tab is opened.
 */
(function () {
  'use strict';

  var _lastFilter = 'all';
  var _rowCache = {};

  // Pagination + search — mirrors the Support Tickets pattern in
  // admin-init.js. `_allRows` is the untouched server response;
  // `_filteredRows` is what search + status filter have narrowed it
  // to; `_renderPage` slices from `_filteredRows`.
  var _allRows = [];
  var _filteredRows = [];
  var _currentPage = 1;
  var CONTACT_PER_PAGE = 20;
  var _searchQuery = '';

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _relDate(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso + (iso.indexOf('Z') === -1 && iso.indexOf('+') === -1 ? 'Z' : ''));
      var diffMin = Math.floor((Date.now() - d.getTime()) / 60000);
      if (diffMin < 1) return 'just now';
      if (diffMin < 60) return diffMin + ' min ago';
      var diffHr = Math.floor(diffMin / 60);
      if (diffHr < 24) return diffHr + ' hr' + (diffHr === 1 ? '' : 's') + ' ago';
      var diffDay = Math.floor(diffHr / 24);
      if (diffDay < 7) return diffDay + ' day' + (diffDay === 1 ? '' : 's') + ' ago';
      return d.toLocaleDateString();
    } catch (_) { return iso; }
  }

  function _statusPill(status, id) {
    var map = {
      new:      { label: 'New',      color: '#f59e0b', bg: 'rgba(245,158,11,0.14)' },
      replied:  { label: 'Replied',  color: '#22d3ee', bg: 'rgba(34,211,238,0.14)' },
      archived: { label: 'Archived', color: '#94a3b8', bg: 'rgba(148,163,184,0.12)' },
    };
    var s = map[status] || { label: status || '?', color: '#94a3b8', bg: 'rgba(148,163,184,0.12)' };
    return '<span style="display:inline-block;padding:1px 6px;border-radius:8px;font-size:0.62rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:' + s.bg + ';color:' + s.color + ';">' + s.label + '</span>';
  }

  // Renders a `<tr>` matching the Support Tickets table style so both
  // sections on the same tab feel like one system. Row click →
  // reply modal (same interaction as the message-body click before);
  // delete lives as a subtle icon at the right end so it doesn't
  // compete visually with the click-to-open primary action.
  function _renderRow(r) {
    _rowCache[r.id] = r;
    var isNew = r.status === 'new';
    var replyCount = (r.replies && r.replies.length) || 0;
    var statusColor = isNew ? '#f59e0b' : r.status === 'replied' ? '#22d3ee' : '#94a3b8';

    // Compact 1-line message preview — full text lives in the modal.
    var msgPreview = (r.message || '').replace(/\s+/g, ' ').slice(0, 90);
    if ((r.message || '').length > 90) msgPreview += '…';
    var replyBadge = replyCount > 0
      ? ' <span style="background:rgba(6,182,212,0.2);color:var(--cyan);padding:0 5px;border-radius:8px;font-size:0.6rem;font-weight:600;">' + replyCount + '</span>'
      : '';

    return (
      '<tr style="cursor:pointer;' + (isNew ? 'background:rgba(245,158,11,0.04);' : '') + '" onclick="window._contactOpenReply(' + r.id + ')">' +
        '<td style="padding:6px 8px;font-size:0.75rem;white-space:nowrap;font-weight:600;color:var(--cyan);">#' + r.id + '</td>' +
        '<td style="padding:6px 8px;font-size:0.75rem;white-space:nowrap;">' + _esc(_relDate(r.created_at)) + '</td>' +
        '<td style="padding:6px 8px;white-space:nowrap;font-size:0.8rem;">' + _esc(r.name || '--') + '</td>' +
        '<td style="padding:6px 8px;white-space:nowrap;font-size:0.75rem;"><a href="mailto:' + _esc(r.email) + '" onclick="event.stopPropagation();" style="color:#06b6d4;text-decoration:none;">' + _esc(r.email) + '</a></td>' +
        '<td style="padding:6px 8px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.75rem;color:var(--text);">' + _esc(msgPreview) + replyBadge + '</td>' +
        '<td style="padding:6px 8px;"><span style="color:' + statusColor + ';font-weight:600;font-size:0.7rem;text-transform:uppercase;">' + _esc(r.status || 'new') + '</span></td>' +
        '<td style="padding:6px 4px;text-align:right;">' +
          '<button onclick="event.stopPropagation();window._contactDelete(' + r.id + ')" ' +
            'title="Delete permanently" ' +
            'style="background:transparent;border:0;color:#94a3b8;font-size:0.85rem;cursor:pointer;padding:2px 6px;border-radius:4px;line-height:1;" ' +
            'onmouseover="this.style.color=\'#fca5a5\';this.style.background=\'rgba(239,68,68,0.1)\';" ' +
            'onmouseout="this.style.color=\'#94a3b8\';this.style.background=\'transparent\';">🗑</button>' +
        '</td>' +
      '</tr>'
    );
  }

  window.loadContactMessages = async function (filter) {
    if (typeof filter === 'string') _lastFilter = filter;
    var container = document.getElementById('contactMessagesList');
    if (!container) return;
    try {
      var res = await fetch('/api/admin/contact-messages?status=' + encodeURIComponent(_lastFilter),
                              { credentials: 'include' });
      if (!res.ok) {
        container.innerHTML = '<p style="color:#f87171;font-size:0.85rem;">Could not load messages (HTTP ' + res.status + ').</p>';
        return;
      }
      var body = await res.json();
      var rows = (body && body.messages) || [];

      // Three badge surfaces reflect the same "N unreplied" count:
      //   • Inline "N new" pill next to the section header (readable
      //     when the sub-tab is active).
      //   • Contact-Messages sub-tab button badge (visible when any
      //     Messages tab is showing).
      //   • Top-level Messages tab badge, which sums all three
      //     sub-tabs' counts (updated via _updateMessagesTotalBadge).
      var newCount = rows.filter(function (r) { return r.status === 'new'; }).length;
      var inline = document.getElementById('contactNewInlineBadge');
      if (inline) {
        if (newCount > 0) { inline.style.display = 'inline-block'; inline.textContent = newCount + ' new'; }
        else              { inline.style.display = 'none'; }
      }
      var subBadge = document.getElementById('contactMessagesBadge');
      if (subBadge) {
        if (newCount > 0) { subBadge.style.display = 'inline-block'; subBadge.textContent = String(newCount); }
        else              { subBadge.style.display = 'none'; }
      }
      if (typeof window._updateMessagesTotalBadge === 'function') window._updateMessagesTotalBadge();

      // Stash the full result set + apply any active search + reset
      // to page 1. Page navigation then slices from _filteredRows.
      _allRows = rows;
      _applySearch();
      _currentPage = 1;
      _renderPage();
    } catch (e) {
      container.innerHTML = '<p style="color:#f87171;font-size:0.85rem;">Network error loading messages.</p>';
    }
  };

  var _pendingReplyOpen = null;  // { id: number, consumed: bool }
  function _handlePendingDeepLinkReply() {
    if (!_pendingReplyOpen || _pendingReplyOpen.consumed) return;
    var id = _pendingReplyOpen.id;
    if (!_rowCache[id]) return;  // row is filtered out; wait for a switch to 'all'
    _pendingReplyOpen.consumed = true;
    // Small tick so the DOM settles before we stack another modal.
    setTimeout(function () { window._contactOpenReply(id); }, 60);
  }

  // Apply the current search term against `_allRows` to produce
  // `_filteredRows`. Empty query = no filter (show everything the
  // status filter returned from the server). Match is case-insensitive
  // across the same fields the Support Tickets search covers: sender
  // name, email, message body, and any prior reply text.
  function _applySearch() {
    var q = (_searchQuery || '').toLowerCase();
    if (!q) { _filteredRows = _allRows.slice(); return; }
    _filteredRows = _allRows.filter(function (r) {
      if ((r.name || '').toLowerCase().indexOf(q) !== -1) return true;
      if ((r.email || '').toLowerCase().indexOf(q) !== -1) return true;
      if ((r.message || '').toLowerCase().indexOf(q) !== -1) return true;
      // Reply thread text too — so admin can find a message by
      // recalling what they said in an earlier reply.
      var replies = r.replies || [];
      for (var i = 0; i < replies.length; i++) {
        if (((replies[i] || {}).message || '').toLowerCase().indexOf(q) !== -1) return true;
      }
      return false;
    });
  }

  window.filterContactMessages = function (query) {
    _searchQuery = query || '';
    _applySearch();
    _currentPage = 1;  // any keystroke resets to page 1
    _renderPage();
  };

  // Table + rows + pagination, all in one shot. Called from
  // loadContactMessages() on initial paint, from contactPage()
  // when admin clicks Prev/Next, and from filterContactMessages()
  // on search keystroke — all three re-slice `_filteredRows` so
  // navigation stays instant with no API round-trip.
  function _renderPage() {
    var container = document.getElementById('contactMessagesList');
    if (!container) return;
    if (!_filteredRows.length) {
      var emptyMsg;
      if (_searchQuery) {
        emptyMsg = 'No messages match "' + _esc(_searchQuery) + '".';
      } else if (_lastFilter === 'new') {
        emptyMsg = 'No new messages. 🎉';
      } else {
        emptyMsg = 'No messages in this view.';
      }
      container.innerHTML = '<p style="color:var(--text-gray);font-size:0.85rem;text-align:center;padding:40px 0;">' + emptyMsg + '</p>';
      _updatePagination();
      return;
    }
    var totalPages = Math.max(1, Math.ceil(_filteredRows.length / CONTACT_PER_PAGE));
    if (_currentPage > totalPages) _currentPage = totalPages;
    if (_currentPage < 1) _currentPage = 1;
    var start = (_currentPage - 1) * CONTACT_PER_PAGE;
    var pageRows = _filteredRows.slice(start, start + CONTACT_PER_PAGE);

    // Same class="data-table" markup as Support Tickets so both
    // sections style identically without extra CSS.
    container.innerHTML =
      '<table class="data-table" style="font-size:0.8rem;width:100%;">' +
        '<thead><tr>' +
          '<th style="padding:6px 8px;width:60px;white-space:nowrap;">#</th>' +
          '<th style="padding:6px 8px;width:110px;white-space:nowrap;">Date</th>' +
          '<th style="padding:6px 8px;width:170px;white-space:nowrap;">From</th>' +
          '<th style="padding:6px 8px;white-space:nowrap;">Email</th>' +
          '<th style="padding:6px 8px;">Message</th>' +
          '<th style="padding:6px 8px;width:80px;white-space:nowrap;">Status</th>' +
          '<th style="padding:6px 4px;width:32px;"></th>' +
        '</tr></thead>' +
        '<tbody>' + pageRows.map(_renderRow).join('') + '</tbody>' +
      '</table>';
    _updatePagination();
    _handlePendingDeepLinkReply();
  }

  function _updatePagination() {
    // Count runs against the FILTERED set so the "(N messages)" tail
    // reflects what admin actually sees. Full row count would be
    // misleading when a search is active.
    var total = _filteredRows ? _filteredRows.length : 0;
    var totalPages = Math.max(1, Math.ceil(total / CONTACT_PER_PAGE));
    var info = document.getElementById('contactPageInfo');
    if (info) {
      info.textContent = _currentPage + ' / ' + totalPages + ' (' + total +
        ' message' + (total === 1 ? '' : 's') + ')';
    }
    var prev = document.getElementById('contactPrevBtn');
    var next = document.getElementById('contactNextBtn');
    if (prev) prev.disabled = _currentPage <= 1;
    if (next) next.disabled = _currentPage >= totalPages;
  }

  window.contactPage = function (dir) {
    var totalPages = Math.max(1, Math.ceil(_filteredRows.length / CONTACT_PER_PAGE));
    _currentPage += dir;
    if (_currentPage < 1) _currentPage = 1;
    if (_currentPage > totalPages) _currentPage = totalPages;
    _renderPage();
  };

  // Format an ISO datetime as "Jul 19, 3:24pm" for the thread timeline.
  function _fmtWhen(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso + (iso.indexOf('Z') === -1 && iso.indexOf('+') === -1 ? 'Z' : ''));
      var opts = { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };
      return d.toLocaleString([], opts);
    } catch (_) { return iso; }
  }

  // Open the branded reply modal for a specific message id.
  // Renders the original message + any prior replies as a thread at
  // the top, then a textarea + Send button. POSTs to /reply which
  // sends the email, appends to replies_json, and transitions the
  // row status to 'replied' — the list reload picks all that up.
  window._contactOpenReply = function (msgId) {
    var row = _rowCache[msgId];
    if (!row) {
      _err('Row not loaded', 'Refresh the list and try again.');
      return;
    }
    if (!window.showStyledModal) {
      alert('Modal system not loaded');
      return;
    }

    // Build the thread — newest-first so the message being replied to
    // sits right next to the textarea. Order:
    //   [newest reply] → ... → [oldest reply] → [original note at bottom]
    // Actor detection: explicit `actor` field first (new), fall back
    // to actor_user_id presence (legacy admin replies).
    var threadHtml = '';
    (row.replies || []).slice().reverse().forEach(function (rep) {
      var actor = (rep.actor || (rep.actor_user_id != null ? 'admin' : 'prospect')).toLowerCase();
      var isAdmin = actor === 'admin';
      var bg = isAdmin ? 'rgba(139,92,246,0.06)' : 'rgba(6,182,212,0.06)';
      var border = isAdmin ? '#a78bfa' : '#06b6d4';
      var labelColor = isAdmin ? '#c4b5fd' : '#7dd3fc';
      var label = isAdmin ? 'You' : _esc(row.name || 'Prospect');
      var indent = isAdmin ? 'margin-left:20px;' : '';
      threadHtml +=
        '<div style="padding:12px 14px;background:' + bg + ';border-left:3px solid ' + border + ';border-radius:0 4px 4px 0;margin-bottom:8px;' + indent + '">' +
          '<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.06em;color:' + labelColor + ';font-weight:700;margin-bottom:6px;">' +
            label + ' · ' + _esc(_fmtWhen(rep.ts)) +
          '</div>' +
          '<div style="font-size:0.85rem;color:var(--text,#e5e7eb);white-space:pre-wrap;line-height:1.5;">' + _esc(rep.message || '') + '</div>' +
        '</div>';
    });
    // Original note last (oldest message, bottom of the thread)
    threadHtml +=
      '<div style="padding:12px 14px;background:rgba(6,182,212,0.06);border-left:3px solid #06b6d4;border-radius:0 4px 4px 0;margin-bottom:8px;">' +
        '<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.06em;color:#7dd3fc;font-weight:700;margin-bottom:6px;">' +
          _esc(row.name || '?') + ' · ' + _esc(_fmtWhen(row.created_at)) + ' · Original' +
        '</div>' +
        '<div style="font-size:0.85rem;color:var(--text,#e5e7eb);white-space:pre-wrap;line-height:1.5;">' + _esc(row.message || '') + '</div>' +
      '</div>';

    var content =
      '<div style="margin-bottom:14px;">' +
        '<div style="font-size:0.72rem;color:var(--text-gray,#94a3b8);margin-bottom:4px;">Replying to</div>' +
        '<div style="font-size:0.85rem;color:var(--text,#e5e7eb);">' +
          _esc(row.name || '?') +
          ' &lt;<a href="mailto:' + _esc(row.email) + '" style="color:#06b6d4;text-decoration:none;">' + _esc(row.email) + '</a>&gt;' +
        '</div>' +
      '</div>' +
      // Thread — capped max-height so a long history scrolls internally
      '<div style="max-height:280px;overflow-y:auto;padding-right:4px;margin-bottom:14px;padding-top:2px;">' +
        threadHtml +
      '</div>' +
      '<label style="display:block;font-size:0.72rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Your reply</label>' +
      '<textarea id="cReplyText-' + msgId + '" placeholder="Hi ' + _esc((row.name || '').split(/\s+/)[0] || 'there') + ',\n\n" ' +
        'style="width:100%;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:var(--text,#e5e7eb);font-size:0.9rem;font-family:inherit;box-sizing:border-box;min-height:130px;resize:vertical;"></textarea>' +
      '<p style="margin:10px 0 0;font-size:0.72rem;color:var(--text-gray,#94a3b8);line-height:1.5;">' +
        'Sends from the platform SMTP. If they reply, it lands in your admin inbox — you can log that reply back here manually via the row\'s notes.' +
      '</p>';

    window.showStyledModal('Reply to ' + _esc(row.name || 'this note'), content, [
      { text: 'Cancel', style: 'ghost' },
      { text: '✉ Send reply', style: 'primary',
        onClick: function () {
          var ta = document.getElementById('cReplyText-' + msgId);
          var msg = ta ? (ta.value || '').trim() : '';
          if (!msg || msg.length < 3) {
            _err('Empty reply', 'Please write a reply before sending.');
            return false;  // keep modal open
          }
          _sendReply(msgId, msg);
        } },
    ], { size: 'md' });

    // Autofocus the textarea after the modal DOM mounts.
    setTimeout(function () {
      var ta = document.getElementById('cReplyText-' + msgId);
      if (ta) { try { ta.focus(); } catch (_) {} }
    }, 40);
  };

  async function _sendReply(msgId, message) {
    try {
      var res = await fetch('/api/admin/contact-messages/' + msgId + '/reply', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message }),
      });
      var body = null;
      try { body = await res.json(); } catch (_) {}
      if (!res.ok) {
        _err('Could not send', (body && body.detail) || ('HTTP ' + res.status));
        return;
      }
      if (body && body.email_sent === false) {
        // Row was updated but SMTP failed — surface it so admin knows.
        _err('Reply logged, email failed', 'The reply is saved but the outbound email did not send. Check SMTP settings.');
      }
      window.loadContactMessages();
    } catch (e) {
      _err('Network error', 'Could not reach the server.');
    }
  }

  window._contactSetStatus = async function (id, newStatus, silent) {
    try {
      var res = await fetch('/api/admin/contact-messages/' + id, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      var body = null;
      try { body = await res.json(); } catch (_) {}
      if (!res.ok) {
        if (!silent) _err('Could not update', (body && body.detail) || ('HTTP ' + res.status));
        return;
      }
      window.loadContactMessages();
    } catch (e) {
      if (!silent) _err('Network error', 'Could not reach the server.');
    }
  };

  window._contactDelete = function (id) {
    var row = _rowCache[id] || {};
    var name = row.name || 'this message';
    var doDelete = async function () {
      try {
        var res = await fetch('/api/admin/contact-messages/' + id,
                                { method: 'DELETE', credentials: 'include' });
        var body = null;
        try { body = await res.json(); } catch (_) {}
        if (!res.ok) {
          _err('Could not delete', (body && body.detail) || ('HTTP ' + res.status));
          return;
        }
        window.loadContactMessages();
      } catch (e) {
        _err('Network error', 'Could not reach the server.');
      }
    };
    if (window.showStyledModal) {
      window.showStyledModal('Delete message?',
        '<p style="margin:0 0 12px;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
          'Permanently delete this message from <strong style="color:var(--text,#e5e7eb);">' + _esc(name) + '</strong>?' +
        '</p>' +
        '<p style="margin:0;color:var(--text-gray,#94a3b8);font-size:0.82rem;line-height:1.5;">' +
          '<strong style="color:#fca5a5;">This cannot be undone.</strong> For a soft-hide, use <em>Archive</em> instead.' +
        '</p>',
        [
          { text: 'Keep it', style: 'ghost' },
          { text: '🗑 Delete', style: 'danger', onClick: function () { doDelete(); } },
        ], { size: 'sm', tone: 'error' });
    } else {
      if (confirm('Delete message from "' + name + '"?')) doDelete();
    }
  };

  function _err(title, msg) {
    if (window.showErrorModal) window.showErrorModal(title, msg);
    else alert(title + '\n\n' + msg);
  }

  document.addEventListener('DOMContentLoaded', function () {
    // Parse ?reply=<id> from the notification-email deep-link BEFORE
    // priming the badge — this way when the list finishes loading,
    // the pending-open handler catches the target row and pops the
    // modal automatically. Also strip the param from the URL so a
    // reload doesn't re-trigger.
    try {
      var params = new URLSearchParams(window.location.search);
      var rid = params.get('reply');
      if (rid && /^\d+$/.test(rid)) {
        _pendingReplyOpen = { id: parseInt(rid, 10), consumed: false };
        // Jul 2026 restructure: Contact Messages now lives under the
        // Messages top-level tab → Contact Messages sub-tab. Force
        // both switches so the target row actually mounts before the
        // pending-reply modal tries to open on it.
        setTimeout(function () {
          if (window.switchTab) window.switchTab('messages');
          if (window.switchMessagesSubTab) window.switchMessagesSubTab('contact');
        }, 100);
        try {
          var url = new URL(window.location.href);
          url.searchParams.delete('reply');
          window.history.replaceState({}, '', url.toString());
        } catch (_) {}
      }
    } catch (_) {}

    // Prime the badge on page open regardless of active tab.
    setTimeout(function () { if (window.loadContactMessages) window.loadContactMessages(); }, 900);

    // Contact Messages lives inside the Messages tab now. `_messagesActivate`
    // (in admin.html) fires all three loaders on tab open, so we don't
    // need to hook switchTab here — kept just in case something else
    // opens the Messages tab without going through _messagesActivate.
    var origSwitch = window.switchTab;
    if (origSwitch && !window._contactTabHooked) {
      window._contactTabHooked = true;
      window.switchTab = function (tab) {
        var r = origSwitch.apply(this, arguments);
        // Messages tab loader lives in admin.html — no reload here to
        // avoid double-fetching. This hook is a no-op now but kept
        // intact in case a future change wants per-tab side effects.
        return r;
      };
    }
  });
})();
