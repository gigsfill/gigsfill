/* Admin panel — Demo Requests tab.
 *
 * Lists pending/scheduled/declined demo requests. Each pending row
 * shows the 3 preferred slots as one-click accept buttons + a decline
 * button. Accept fires POST /api/admin/demo-requests/{id}/accept;
 * decline opens a small textarea + POST decline. Auto-refreshes badge
 * count on the tab.
 */
(function () {
  'use strict';

  // Pre-launch default = 'all' so recently-scheduled + declined stay
  // visible after they've been actioned. Users can filter down to
  // "Pending only" once the queue gets busy.
  var _lastFilter = 'all';

  // Cache of last-rendered rows so accept/decline/delete handlers can
  // pull the slot human text without re-fetching. Keyed by request id.
  var _rowCache = {};

  // Platform-wide default meeting URL — supplied by the list endpoint
  // alongside `requests`. Used both to color the per-row "Meeting URL"
  // button (green = something's set, amber = nothing) and to pre-fill
  // the modal's "effective URL" preview so admin knows what the
  // prospect email will actually contain.
  var _platformMeetingUrl = '';

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _relDate(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso + (iso.indexOf('Z') === -1 && iso.indexOf('+') === -1 ? 'Z' : ''));
      var diffMs = Date.now() - d.getTime();
      var diffMin = Math.floor(diffMs / 60000);
      if (diffMin < 1) return 'just now';
      if (diffMin < 60) return diffMin + ' min ago';
      var diffHr = Math.floor(diffMin / 60);
      if (diffHr < 24) return diffHr + ' hour' + (diffHr === 1 ? '' : 's') + ' ago';
      var diffDay = Math.floor(diffHr / 24);
      if (diffDay < 7) return diffDay + ' day' + (diffDay === 1 ? '' : 's') + ' ago';
      return d.toLocaleDateString();
    } catch (_) { return iso; }
  }

  // Relative-to-scheduled-time hint for the row's status corner.
  // `scheduled_at` is a naive Pacific timestamp like "2026-07-20T14:30:00".
  // We interpret it as viewer-local (fine when the admin is Pacific;
  // off by a few hours in ET but the "in 2 days" phrasing survives).
  //
  // Returns "in 2 days", "tomorrow", "in 3 hours", "just now", "2 days ago",
  // "next week", etc. Falls back to a plain locale date if we can't parse.
  function _relScheduled(scheduledAt) {
    if (!scheduledAt) return '';
    try {
      var d = new Date(scheduledAt);
      if (isNaN(d.getTime())) return '';
      var now = Date.now();
      var diffMs = d.getTime() - now;
      var abs = Math.abs(diffMs);
      var future = diffMs > 0;
      var mins = Math.floor(abs / 60000);
      if (mins < 1)  return future ? 'starting now' : 'just now';
      if (mins < 60) return future ? ('in ' + mins + ' min') : (mins + ' min ago');
      var hrs = Math.floor(mins / 60);
      if (hrs < 24) return future ? ('in ' + hrs + ' hr' + (hrs === 1 ? '' : 's'))
                                   : (hrs + ' hr' + (hrs === 1 ? '' : 's') + ' ago');
      var days = Math.floor(hrs / 24);
      if (days === 1) return future ? 'tomorrow' : 'yesterday';
      if (days < 7)   return future ? ('in ' + days + ' days') : (days + ' days ago');
      var weeks = Math.floor(days / 7);
      if (weeks < 5)  return future ? ('in ' + weeks + ' wk' + (weeks === 1 ? '' : 's'))
                                     : (weeks + ' wk' + (weeks === 1 ? '' : 's') + ' ago');
      return d.toLocaleDateString();
    } catch (_) { return ''; }
  }

  function _statusPill(status, reqId) {
    var map = {
      pending:   { label: 'Pending',   color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
      scheduled: { label: 'Scheduled', color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
      declined:  { label: 'Declined',  color: '#94a3b8', bg: 'rgba(148,163,184,0.12)' },
      cancelled: { label: 'Cancelled', color: '#f87171', bg: 'rgba(248,113,113,0.12)' },
      completed: { label: 'Completed', color: '#22d3ee', bg: 'rgba(34,211,238,0.14)' },
      no_show:   { label: 'No Show',   color: '#f97316', bg: 'rgba(249,115,22,0.14)' },
    };
    var s = map[status] || { label: status || 'Unknown', color: '#94a3b8', bg: 'rgba(148,163,184,0.12)' };
    // Completed + no_show pills are clickable so admin can toggle the
    // outcome after the demo happened. Everything else renders as a
    // static badge.
    if (reqId && (status === 'completed' || status === 'no_show')) {
      return '<button onclick="window._demosSetOutcome(' + reqId + ', \'' + status + '\')" ' +
        'title="Click to change outcome" ' +
        'style="display:inline-block;padding:1px 6px;border-radius:8px;font-size:0.62rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:' + s.bg + ';color:' + s.color + ';border:1px solid ' + s.color + '55;cursor:pointer;font-family:inherit;">' +
        s.label + ' ▾</button>';
    }
    return '<span style="display:inline-block;padding:1px 6px;border-radius:8px;font-size:0.62rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:' + s.bg + ';color:' + s.color + ';">' + s.label + '</span>';
  }

  function _slotChip(slotHuman, i, status, scheduledIdx, reqId, isPending, isPast) {
    if (isPending) {
      if (isPast) {
        // Past slots on a pending row — render as a disabled black chip.
        // Not clickable; server also blocks accept on past slots as a
        // defense-in-depth guard. 2026-07-25.
        return (
          '<div title="This time has passed" ' +
          'style="display:block;text-align:left;width:100%;padding:6px 10px;background:#0b0d12;border:1px solid rgba(255,255,255,0.06);border-radius:5px;color:#4b5563;font-size:0.75rem;margin-bottom:4px;cursor:not-allowed;">' +
          '<span style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.05em;color:#4b5563;margin-right:6px;">#' + (i + 1) + '</span>' +
          '<span style="text-decoration:line-through;">' + _esc(slotHuman) + '</span>' +
          '<span style="float:right;color:#6b7280;font-weight:600;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.05em;">Past</span>' +
          '</div>'
        );
      }
      return (
        '<button onclick="window._demosAccept(' + reqId + ', ' + i + ')" ' +
        'style="display:block;text-align:left;width:100%;padding:6px 10px;background:rgba(139,92,246,0.06);border:1px solid rgba(139,92,246,0.3);border-radius:5px;color:#e5e7eb;font-size:0.75rem;cursor:pointer;margin-bottom:4px;transition:all 0.15s;font-family:inherit;" ' +
        'onmouseover="this.style.background=\'linear-gradient(135deg,rgba(139,92,246,0.15),rgba(6,182,212,0.15))\';this.style.borderColor=\'#06b6d4\';" ' +
        'onmouseout="this.style.background=\'rgba(139,92,246,0.06)\';this.style.borderColor=\'rgba(139,92,246,0.3)\';">' +
        '<span style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.05em;color:#94a3b8;margin-right:6px;">#' + (i + 1) + '</span>' +
        '<span style="font-weight:500;">' + _esc(slotHuman) + '</span>' +
        '<span style="float:right;color:#06b6d4;font-weight:600;font-size:0.68rem;">✓ Accept</span>' +
        '</button>'
      );
    } else {
      var isScheduled = (i === scheduledIdx && status === 'scheduled');
      var opac = isScheduled ? 1 : 0.5;
      var bg = isScheduled ? 'linear-gradient(135deg,rgba(16,185,129,0.15),rgba(6,182,212,0.15))' : 'rgba(255,255,255,0.02)';
      var bd = isScheduled ? 'rgba(16,185,129,0.4)' : 'rgba(255,255,255,0.05)';
      return (
        '<div style="padding:5px 10px;background:' + bg + ';border:1px solid ' + bd + ';border-radius:5px;color:#e5e7eb;font-size:0.75rem;margin-bottom:4px;opacity:' + opac + ';">' +
        '<span style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.05em;color:#94a3b8;margin-right:6px;">#' + (i + 1) + '</span>' +
        '<span>' + _esc(slotHuman) + '</span>' +
        (isScheduled ? '<span style="float:right;color:#10b981;font-weight:600;font-size:0.68rem;">✓ Scheduled</span>' : '') +
        '</div>'
      );
    }
  }

  function _renderCard(r) {
    var isPending = r.status === 'pending';
    _rowCache[r.id] = r;
    var pastArr = r.preferred_slots_past || [];
    var slotsHtml = (r.preferred_slots_human || []).map(function (h, i) {
      return _slotChip(h, i, r.status, r.scheduled_slot_index, r.id, isPending, !!pastArr[i]);
    }).join('');

    var contactBits = [
      '<a href="mailto:' + _esc(r.email) + '" style="color:#06b6d4;text-decoration:none;">' + _esc(r.email) + '</a>',
      r.phone ? _esc(r.phone) : null,
      r.city || r.state ? _esc([r.city, r.state].filter(function (x) { return x; }).join(', ')) : null,
    ].filter(Boolean).join(' · ');

    var entityLine = '';
    if (r.entity_type || r.entity_name) {
      var t = r.entity_type ? r.entity_type.charAt(0).toUpperCase() + r.entity_type.slice(1) : '';
      entityLine = '<div style="font-size:0.7rem;color:var(--text-gray);margin-top:2px;">' +
        (t ? '<strong>' + _esc(t) + '</strong>' : '') +
        (t && r.entity_name ? ' · ' : '') +
        (r.entity_name ? _esc(r.entity_name) : '') +
        '</div>';
    }

    var notesHtml = '';
    if (r.notes) {
      notesHtml = '<div style="margin-top:8px;padding:7px 10px;background:rgba(6,182,212,0.05);border-left:2px solid #06b6d4;border-radius:0 3px 3px 0;font-size:0.72rem;color:var(--text);line-height:1.5;white-space:pre-wrap;">' + _esc(r.notes) + '</div>';
    }

    // Every row can be deleted (spam / duplicates / test rows), so the
    // Delete button lives in a row-level footer that renders regardless
    // of status. Pending rows also get a "Decline instead" button.
    // Scheduled rows get "Change time" + "Cancel demo" so admin can
    // change a slot they picked by accident, or cancel a real
    // scheduled demo (which emails the prospect). Non-pending rows
    // that already have admin_notes show them.
    var nameEsc = _esc(r.name || '?').replace(/\'/g, "\\'");
    var deleteBtn =
      '<button onclick="window._demosDelete(' + r.id + ', \'' + nameEsc + '\')" ' +
      'style="padding:4px 10px;background:transparent;border:1px solid rgba(239,68,68,0.35);border-radius:5px;color:#fca5a5;font-size:0.68rem;cursor:pointer;font-family:inherit;margin-left:6px;" ' +
      'title="Permanently delete this request">🗑 Delete</button>';
    var declineBtn = isPending
      ? '<button onclick="window._demosDeclineOpen(' + r.id + ')" style="padding:4px 10px;background:transparent;border:1px solid rgba(239,68,68,0.35);border-radius:5px;color:#fca5a5;font-size:0.68rem;cursor:pointer;font-family:inherit;">Decline instead</button>'
      : '';
    var isScheduled = r.status === 'scheduled';
    // Meeting URL status pill for scheduled rows. Green if a URL is
    // set (row override OR platform default), amber if nothing —
    // amber is a soft nudge that the confirmation email doesn't
    // include a join link yet.
    var effectiveUrl = (r.meeting_url && r.meeting_url.trim()) || (_platformMeetingUrl || '');
    var mtgLabel = r.meeting_url
      ? '🎥 URL (custom)'
      : (effectiveUrl ? '🎥 URL (default)' : '🎥 Set URL');
    var mtgColor = effectiveUrl ? 'rgba(16,185,129,0.35)' : 'rgba(245,158,11,0.4)';
    var mtgFg = effectiveUrl ? '#86efac' : '#fcd34d';
    var meetingBtn = isScheduled
      ? '<button onclick="window._demosSetMeetingUrl(' + r.id + ')" ' +
        'style="padding:4px 10px;background:transparent;border:1px solid ' + mtgColor + ';border-radius:5px;color:' + mtgFg + ';font-size:0.68rem;cursor:pointer;font-family:inherit;margin-right:6px;" ' +
        'title="Set a custom Teams / Zoom / Meet URL for this specific demo (overrides platform default)">' + mtgLabel + '</button>'
      : '';
    var changeBtn = isScheduled
      ? '<button onclick="window._demosChangeTime(' + r.id + ')" ' +
        'style="padding:4px 10px;background:transparent;border:1px solid rgba(139,92,246,0.4);border-radius:5px;color:#c4b5fd;font-size:0.68rem;cursor:pointer;font-family:inherit;margin-right:6px;" ' +
        'title="Revert to pending so you can pick a different slot">↻ Change time</button>'
      : '';
    var cancelBtn = isScheduled
      ? '<button onclick="window._demosCancel(' + r.id + ')" ' +
        'style="padding:4px 10px;background:transparent;border:1px solid rgba(239,68,68,0.35);border-radius:5px;color:#fca5a5;font-size:0.68rem;cursor:pointer;font-family:inherit;margin-right:6px;" ' +
        'title="Cancel this demo and email the prospect">✗ Cancel demo</button>'
      : '';
    var declineNote = (!isPending && r.admin_notes)
      ? '<div style="margin-top:8px;padding:7px 10px;background:rgba(148,163,184,0.06);border-radius:3px;font-size:0.68rem;color:var(--text-gray);"><em>Note:</em> ' + _esc(r.admin_notes) + '</div>'
      : '';
    // Internal outcome notes render on completed / no_show rows with
    // a subtle cyan accent so they're visually distinct from the
    // decline note (which reflects a message that was sent to the
    // prospect). Truncated to fit; hover the row to see full text.
    var outcomeNote = ((r.status === 'completed' || r.status === 'no_show') && r.outcome_notes)
      ? '<div style="margin-top:8px;padding:7px 10px;background:rgba(6,182,212,0.06);border-left:2px solid rgba(6,182,212,0.5);border-radius:0 3px 3px 0;font-size:0.7rem;color:var(--text);line-height:1.5;white-space:pre-wrap;" title="' + _esc(r.outcome_notes) + '"><em style="color:#7dd3fc;">📝 Notes:</em> ' + _esc(r.outcome_notes) + '</div>'
      : '';
    var actionsHtml =
      declineNote +
      outcomeNote +
      '<div style="margin-top:10px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.06);text-align:right;">' +
        meetingBtn + changeBtn + cancelBtn + declineBtn + deleteBtn +
      '</div>';

    return (
      '<div id="row-' + r.id + '" style="background:var(--card,#151b28);border:1px solid var(--border,rgba(255,255,255,0.08));border-radius:8px;padding:10px 12px;scroll-margin-top:80px;">' +
        '<div style="display:flex;align-items:start;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:6px;">' +
          '<div style="flex:1;min-width:180px;">' +
            '<div style="font-size:0.82rem;font-weight:700;color:var(--text);">' + _esc(r.name || '?') + '</div>' +
            '<div style="font-size:0.7rem;color:var(--text-gray);margin-top:2px;">' + contactBits + '</div>' +
            entityLine +
          '</div>' +
          '<div style="text-align:right;font-size:0.62rem;color:var(--text-gray);">' +
            _statusPill(r.status, r.id) +
            // For scheduled / completed / no_show rows the interesting
            // date is the demo time, not when the request was created.
            // Everything else keeps the "requested X ago" reading.
            '<div style="margin-top:3px;">' +
              (((r.status === 'scheduled' || r.status === 'completed' || r.status === 'no_show')
                  && r.scheduled_at)
                ? _esc(_relScheduled(r.scheduled_at))
                : _esc(_relDate(r.created_at))) +
            '</div>' +
          '</div>' +
        '</div>' +
        notesHtml +
        '<div style="margin-top:10px;">' + slotsHtml + '</div>' +
        actionsHtml +
        '<div id="decline-box-' + r.id + '"></div>' +
      '</div>'
    );
  }

  window.loadDemoRequests = async function (filter) {
    if (typeof filter === 'string') _lastFilter = filter;
    var container = document.getElementById('demoRequestsList');
    if (!container) return;
    try {
      var res = await fetch('/api/admin/demo-requests?status=' + encodeURIComponent(_lastFilter), { credentials: 'include' });
      if (!res.ok) {
        container.innerHTML = '<p style="color:#f87171;font-size:0.85rem;">Could not load demo requests (HTTP ' + res.status + ').</p>';
        return;
      }
      var body = await res.json();
      var rows = (body && body.requests) || [];
      _platformMeetingUrl = (body && body.platform_meeting_url) || '';

      // Update Demo Requests sub-tab badge (`demosPendingBadge`) —
      // now lives on the sub-tab button under Messages, but the id
      // stayed the same so this code didn't need changing. Also
      // trigger a total-badge recalc so the top-level Messages badge
      // reflects the new count.
      var pendingCount = rows.filter(function (r) { return r.status === 'pending'; }).length;
      var badge = document.getElementById('demosPendingBadge');
      if (badge) {
        if (pendingCount > 0) {
          badge.style.display = 'inline-block';
          badge.textContent = String(pendingCount);
        } else {
          badge.style.display = 'none';
        }
      }
      if (typeof window._updateMessagesTotalBadge === 'function') window._updateMessagesTotalBadge();

      if (!rows.length) {
        var empty = _lastFilter === 'pending'
          ? '<p style="color:var(--text-gray);font-size:0.9rem;text-align:center;padding:60px 20px;">No pending demo requests. 🎉</p>'
          : '<p style="color:var(--text-gray);font-size:0.9rem;text-align:center;padding:60px 20px;">No demo requests in this view.</p>';
        container.innerHTML = empty;
        return;
      }

      container.innerHTML = rows.map(_renderCard).join('');

      // Reminder-email deep-link support: URLs like
      //   /app/admin.html?tab=demos#row-42
      // scroll the row into view and briefly flash a cyan glow so admin
      // can find it quickly. Runs after the render so the target row
      // is actually in the DOM. Falls through silently if the row is
      // filtered out of the current view.
      _handleRowAnchor();
    } catch (e) {
      container.innerHTML = '<p style="color:#f87171;font-size:0.85rem;">Network error loading demo requests.</p>';
    }
  };

  function _handleRowAnchor() {
    try {
      var hash = window.location.hash || '';
      var m = hash.match(/^#row-(\d+)$/);
      if (!m) return;
      var el = document.getElementById('row-' + m[1]);
      if (!el) return;
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      // Brief cyan glow so admin's eye lands on it. Reset after 2s.
      var prev = el.style.boxShadow;
      el.style.transition = 'box-shadow 0.3s ease';
      el.style.boxShadow = '0 0 0 3px rgba(6,182,212,0.5), 0 4px 20px rgba(6,182,212,0.3)';
      setTimeout(function () { el.style.boxShadow = prev; }, 2000);
    } catch (_) {}
  }

  window._demosAccept = function (reqId, slotIndex) {
    var row = _rowCache[reqId] || {};
    var slotHuman = (row.preferred_slots_human || [])[slotIndex] || ('Slot #' + (slotIndex + 1));
    var name = row.name || 'this prospect';
    var doAccept = async function () {
      try {
        var res = await fetch('/api/admin/demo-requests/' + reqId + '/accept', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ slot_index: slotIndex }),
        });
        var body = null;
        try { body = await res.json(); } catch (_) {}
        if (!res.ok) {
          _err('Could not accept', (body && body.detail) || ('Accept failed (HTTP ' + res.status + ').'));
          return;
        }
        window.loadDemoRequests();
      } catch (e) {
        _err('Network error', 'Could not reach the server. Check your connection and try again.');
      }
    };
    if (window.showStyledModal) {
      var content =
        '<p style="margin:0 0 14px 0;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
          'Confirm this time slot for <strong style="color:var(--text,#e5e7eb);">' + _esc(name) + '</strong>?' +
        '</p>' +
        '<div style="padding:14px 16px;background:linear-gradient(135deg,rgba(139,92,246,0.1),rgba(6,182,212,0.1));border:1px solid rgba(6,182,212,0.3);border-radius:8px;margin-bottom:14px;">' +
          '<div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;margin-bottom:6px;">Scheduled time</div>' +
          '<div style="font-size:1rem;font-weight:600;color:var(--text,#e5e7eb);">📅 ' + _esc(slotHuman) + '</div>' +
        '</div>' +
        '<p style="margin:0;color:var(--text-gray,#94a3b8);font-size:0.82rem;line-height:1.5;">' +
          'They\'ll receive a branded confirmation email with a calendar invite attached. The other two preferred slots will be released.' +
        '</p>';
      window.showStyledModal('Accept demo request', content, [
        { text: 'Cancel', style: 'ghost' },
        // Wrap the async handler in a void thunk so gf-modals doesn't
        // see the returned Promise and keep the modal open (async
        // handlers stay-open by design — see gf-modals.js:164-177).
        // Errors surface via _err() which opens its own modal, so
        // closing immediately is safe.
        { text: 'Accept & send invite', style: 'primary',
          onClick: function () { doAccept(); } },
      ], { size: 'sm' });
    } else {
      if (confirm('Accept ' + slotHuman + ' for ' + name + '?')) doAccept();
    }
  };

  // Decline modal — reuses the homepage demo-request modal's picker
  // components (GfDatePicker + .dr-date-input + .dr-time-select) so
  // admin sees the same dark calendar popup + 12h hour/min/AM-PM
  // selects the prospect would see when submitting. Filling any of
  // the 1-3 slot rows switches the prospect email from "reply with
  // new times" fallback to "here's what would work for us — click
  // one" flow, cutting one round of back-and-forth. Server enforces
  // 9 AM – 5 PM PT, 15-min increments; the picker matches those.
  //
  // demo-request-modal.js exposes window.GfDatePicker + drInjectStyles;
  // both are loaded on admin.html for exactly this purpose.

  var _AD_HOUR_ORDER = ['9', '10', '11', '12', '1', '2', '3', '4', '5'];
  var _AD_MIN_OPTIONS = ['00', '15', '30', '45'];

  // Local per-modal slot state — keyed by reqId so a click on one
  // decline modal doesn't leak state to another. Reset on modal close
  // via window.closeAllModals (implicit — state is only read on submit).
  var _adDeclSlots = {};

  function _adRenderSlot(reqId, index) {
    var s = (_adDeclSlots[reqId] || [])[index] || {};

    var wrap = document.createElement('div');
    wrap.className = 'dr-slot';
    wrap.setAttribute('data-slot-idx', String(index));

    var lbl = document.createElement('div');
    lbl.className = 'dr-slot-label';
    lbl.textContent = 'Option #' + (index + 1);
    wrap.appendChild(lbl);

    var row = document.createElement('div');
    row.className = 'dr-slot-row';

    // Custom dark date picker — same one the homepage modal uses.
    var initialIso = s.date || '';
    var dt = document.createElement('input');
    dt.type = 'text';
    dt.readOnly = true;
    dt.className = 'dr-date-input';
    dt.setAttribute('inputmode', 'none');
    dt.placeholder = 'mm/dd/yyyy';
    if (initialIso) {
      dt.value = (window.drIsoToDisp || function(x){return x;})(initialIso);
      dt.setAttribute('data-iso', initialIso);
    }
    dt.addEventListener('change', function () {
      var iso = (window.drDispToIso || function(x){return x;})(dt.value) || dt.getAttribute('data-iso') || '';
      dt.setAttribute('data-iso', iso);
      _adDeclSlots[reqId][index] = _adDeclSlots[reqId][index] || {};
      _adDeclSlots[reqId][index].date = iso;
    });
    row.appendChild(dt);
    if (window.GfDatePicker) {
      new window.GfDatePicker(dt, {
        minIso: window.drTodayIso ? window.drTodayIso() : undefined,
        maxIso: window.drMaxIso   ? window.drMaxIso()   : undefined,
      });
    }

    // Time picker — 12h clock. Values are label-value (9,10,11,12,1,…,5).
    // AM/PM is auto-derived from the hour and locked so admin can't
    // accidentally select 3 AM for a business demo.
    var timeGroup = document.createElement('div');
    timeGroup.className = 'dr-time-group';

    var hourSel = document.createElement('select');
    hourSel.className = 'dr-time-select dr-hour';
    _AD_HOUR_ORDER.forEach(function (h) {
      var o = document.createElement('option');
      o.value = h; o.textContent = h; hourSel.appendChild(o);
    });
    var initialHour12 = s.hour12 || '10';
    var initialAmpm   = s.ampm   || 'AM';
    hourSel.value = initialHour12;

    var colon = document.createElement('span');
    colon.className = 'dr-time-colon';
    colon.textContent = ':';

    var minSel = document.createElement('select');
    minSel.className = 'dr-time-select dr-min';
    _AD_MIN_OPTIONS.forEach(function (m) {
      var mo = document.createElement('option');
      mo.value = m; mo.textContent = m; minSel.appendChild(mo);
    });
    minSel.value = s.minute || '00';

    var ampmSel = document.createElement('select');
    ampmSel.className = 'dr-time-select dr-ampm';
    ['AM', 'PM'].forEach(function (ap) {
      var ao = document.createElement('option');
      ao.value = ap; ao.textContent = ap; ampmSel.appendChild(ao);
    });
    ampmSel.value = initialAmpm;
    ampmSel.disabled = true;
    ampmSel.setAttribute('aria-readonly', 'true');
    ampmSel.setAttribute('tabindex', '-1');

    function _autoSetAmpm() {
      var h = hourSel.value;
      ampmSel.value = (h === '9' || h === '10' || h === '11') ? 'AM' : 'PM';
    }
    if (!s.hour12) _autoSetAmpm();

    function _to24Hour(h12, ampm) {
      var h = parseInt(h12, 10);
      if (ampm === 'AM') return h === 12 ? 0 : h;
      return h === 12 ? 12 : h + 12;
    }
    function _syncTime() {
      _autoSetAmpm();
      _adDeclSlots[reqId][index] = _adDeclSlots[reqId][index] || {};
      var st = _adDeclSlots[reqId][index];
      st.hour12 = hourSel.value;
      st.minute = minSel.value;
      st.ampm   = ampmSel.value;
      var h24 = _to24Hour(hourSel.value, ampmSel.value);
      st.hour = String(h24);
      st.time = (('0' + h24).slice(-2)) + ':' + minSel.value;
    }
    hourSel.addEventListener('change', _syncTime);
    minSel.addEventListener('change', _syncTime);
    _syncTime();  // seed initial state

    timeGroup.appendChild(hourSel);
    timeGroup.appendChild(colon);
    timeGroup.appendChild(minSel);
    timeGroup.appendChild(ampmSel);
    row.appendChild(timeGroup);
    wrap.appendChild(row);

    // Remove-slot button — only shown when >1 slot exists, so we
    // always keep at least one row available.
    if ((_adDeclSlots[reqId] || []).length > 1) {
      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'dr-remove-slot';
      rm.textContent = '× Remove';
      rm.addEventListener('click', function () {
        _adDeclSlots[reqId].splice(index, 1);
        _adRerenderSlots(reqId);
      });
      wrap.appendChild(rm);
    }

    return wrap;
  }

  function _adRerenderSlots(reqId) {
    var container = document.getElementById('ad-decl-slots-' + reqId);
    var addBtn = document.getElementById('ad-decl-add-' + reqId);
    if (!container) return;
    container.innerHTML = '';
    (_adDeclSlots[reqId] || []).forEach(function (_s, i) {
      container.appendChild(_adRenderSlot(reqId, i));
    });
    if (addBtn) {
      var n = (_adDeclSlots[reqId] || []).length;
      addBtn.disabled = n >= 3;
      addBtn.textContent = '+ Add another preferred time (' + n + '/3)';
    }
  }

  window._demosDeclineOpen = function (reqId) {
    var row = _rowCache[reqId] || {};
    var name = row.name || 'this prospect';
    // Reset slot state on every open + seed with one empty slot so the
    // picker is immediately visible (discoverable). Admin who doesn't
    // want to counter-propose just skips it — submit ignores rows with
    // blank date, so no error is fired on "empty first row".
    _adDeclSlots[reqId] = [{}];

    // Their requested times as strike-through context so admin
    // knows what didn't work when writing the message.
    var theirSlotsHtml = '';
    if (row.preferred_slots_human && row.preferred_slots_human.length) {
      theirSlotsHtml =
        '<div style="padding:10px 12px;background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.25);border-radius:6px;margin-bottom:14px;">' +
          '<div style="font-size:0.65rem;text-transform:uppercase;letter-spacing:0.08em;color:#fca5a5;margin-bottom:6px;font-weight:700;">Their requested times (declined)</div>' +
          row.preferred_slots_human.map(function (h) {
            return '<div style="font-size:0.78rem;color:var(--text-gray,#94a3b8);text-decoration:line-through;opacity:0.8;">' + _esc(h) + '</div>';
          }).join('') +
        '</div>';
    }

    if (!window.showStyledModal) {
      var msg = prompt('Decline demo for ' + name + '. Optional message to prospect:', '');
      if (msg === null) return;
      _sendDecline(reqId, msg, []);
      return;
    }

    // Make sure the shared demo-modal CSS is injected before we create
    // any .dr-slot / .dr-date-input / .dr-time-select elements. This is
    // idempotent — safe to call every open.
    if (typeof window.drInjectStyles === 'function') {
      try { window.drInjectStyles(); } catch (_) {}
    }

    var content =
      '<p style="margin:0 0 12px 0;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
        'Decline the demo for <strong style="color:var(--text,#e5e7eb);">' + _esc(name) + '</strong>. Optionally propose 1-3 times that would work — the prospect will get them as one-click accept buttons in the email.' +
      '</p>' +
      theirSlotsHtml +
      '<label style="display:block;font-size:0.72rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Message to prospect (optional)</label>' +
      '<textarea id="ad-decl-msg-' + reqId + '" placeholder="e.g. Sorry, none of those worked on our end — could any of the times below work instead?" ' +
        'style="width:100%;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:var(--text,#e5e7eb);font-size:0.85rem;font-family:inherit;box-sizing:border-box;min-height:70px;resize:vertical;"></textarea>' +
      // `class="dr-body"` is required so the `.dr-body .dr-date-input`
      // scoped rules from demo-request-modal.js apply here too. We
      // override the .dr-body's own padding/color to blend into the
      // gf-modals content area.
      '<div class="dr-body" style="padding:0;margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06);">' +
        '<label style="display:block;font-size:0.72rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Suggest alternate times (optional)</label>' +
        '<div style="font-size:0.72rem;color:var(--text-gray,#94a3b8);margin-bottom:10px;line-height:1.5;">' +
          'Pick 1-3 options — the prospect gets one-click accept buttons for each. All times Pacific.' +
        '</div>' +
        '<div id="ad-decl-slots-' + reqId + '"></div>' +
        '<button type="button" id="ad-decl-add-' + reqId + '" class="dr-add-slot">+ Add another preferred time (1/3)</button>' +
      '</div>';

    window.showStyledModal('Decline demo request?', content, [
      { text: 'Cancel', style: 'ghost' },
      { text: '✗ Send decline', style: 'danger',
        onClick: function () {
          var msgEl = document.getElementById('ad-decl-msg-' + reqId);
          var msg = msgEl ? (msgEl.value || '').trim() : '';
          var suggested = [];
          var slots = _adDeclSlots[reqId] || [];
          for (var i = 0; i < slots.length; i++) {
            var st = slots[i] || {};
            // Blank date = admin didn't fill in this row → skip it
            // rather than erroring, since the whole slot picker is
            // optional. Time always has a value (defaults to 10:00 AM).
            if (!st.date) continue;
            if (!st.time) {
              _err('Missing time', 'Option #' + (i + 1) + ' needs a time.');
              return false;  // keep modal open
            }
            suggested.push({ date: st.date, time: st.time });
          }
          _sendDecline(reqId, msg, suggested);
        } },
    ], { size: 'md', tone: 'error' });

    // Wire the picker slots + add button AFTER the modal DOM mounts.
    setTimeout(function () {
      _adRerenderSlots(reqId);  // renders 0 slots initially
      var addBtn = document.getElementById('ad-decl-add-' + reqId);
      if (addBtn) {
        addBtn.addEventListener('click', function () {
          if ((_adDeclSlots[reqId] || []).length >= 3) return;
          _adDeclSlots[reqId].push({});
          _adRerenderSlots(reqId);
        });
      }
    }, 0);
  };

  async function _sendDecline(reqId, message, suggestedSlots) {
    try {
      var res = await fetch('/api/admin/demo-requests/' + reqId + '/decline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          message: message,
          admin_suggested_slots: suggestedSlots,
        }),
      });
      var body = null;
      try { body = await res.json(); } catch (_) {}
      if (!res.ok) {
        _err('Could not decline', (body && body.detail) || ('Decline failed (HTTP ' + res.status + ').'));
        return;
      }
      window.loadDemoRequests();
    } catch (e) {
      _err('Network error', 'Could not reach the server. Check your connection and try again.');
    }
  }

  // Retained for legacy callers of the old inline decline panel; no-op
  // now that decline uses a proper modal.
  window._demosDeclineCancel = function () {};
  window._demosDeclineSubmit = function () {};

  window._demosDelete = function (reqId, name) {
    var row = _rowCache[reqId] || {};
    var email = row.email || '';
    var status = row.status || '';
    var doDelete = async function () {
      try {
        var res = await fetch('/api/admin/demo-requests/' + reqId, {
          method: 'DELETE',
          credentials: 'include',
        });
        var body = null;
        try { body = await res.json(); } catch (_) {}
        if (!res.ok) {
          _err('Could not delete', (body && body.detail) || ('Delete failed (HTTP ' + res.status + ').'));
          return;
        }
        window.loadDemoRequests();
      } catch (e) {
        _err('Network error', 'Could not reach the server. Check your connection and try again.');
      }
    };
    if (window.showStyledModal) {
      var statusPill = status ? _statusPill(status) : '';
      var content =
        '<p style="margin:0 0 14px 0;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
          'Permanently delete this demo request?' +
        '</p>' +
        '<div style="padding:14px 16px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.35);border-radius:8px;margin-bottom:14px;">' +
          '<div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.08em;color:#fca5a5;margin-bottom:6px;">Prospect</div>' +
          '<div style="font-size:0.95rem;font-weight:600;color:var(--text,#e5e7eb);">' + _esc(name || '?') + '</div>' +
          (email ? '<div style="font-size:0.78rem;color:var(--text-gray,#94a3b8);margin-top:3px;">' + _esc(email) + '</div>' : '') +
          (statusPill ? '<div style="margin-top:8px;">' + statusPill + '</div>' : '') +
        '</div>' +
        '<p style="margin:0;color:var(--text-gray,#94a3b8);font-size:0.82rem;line-height:1.5;">' +
          '<strong style="color:#fca5a5;">This cannot be undone.</strong> The prospect will <strong>not</strong> be emailed. Use this for spam, test rows, or duplicates — for a real cancel with notification, use <em>Decline</em> instead.' +
        '</p>';
      window.showStyledModal('Delete demo request?', content, [
        { text: 'Keep it', style: 'ghost' },
        // Void wrapper so the returned Promise from doDelete doesn't
        // trip gf-modals' stay-open-for-async guard (see admin-demos
        // Accept handler above for details).
        { text: '🗑 Delete permanently', style: 'danger',
          onClick: function () { doDelete(); } },
      ], { size: 'sm', tone: 'error' });
    } else {
      if (confirm('Permanently delete this demo request from "' + name + '"? This cannot be undone. The prospect will NOT be emailed.')) doDelete();
    }
  };

  function _err(title, msg) {
    if (window.showErrorModal) {
      window.showErrorModal(title, msg);
    } else {
      alert(title + '\n\n' + msg);
    }
  }

  // Change time (scheduled → pending). Fires POST /revert-to-pending,
  // which clears scheduled_slot/scheduled_at/reminder_sent_at but
  // preserves the 3 preferred slots + slots_version. Prospect is NOT
  // emailed — a "your time changed to X" confirmation only fires when
  // admin subsequently accepts a new slot.
  window._demosChangeTime = function (reqId) {
    var row = _rowCache[reqId] || {};
    var name = row.name || 'this prospect';
    var slotHuman = (row.preferred_slots_human || [])[row.scheduled_slot_index] || 'the scheduled time';
    var doRevert = async function () {
      try {
        var res = await fetch('/api/admin/demo-requests/' + reqId + '/revert-to-pending', {
          method: 'POST', credentials: 'include',
        });
        var body = null;
        try { body = await res.json(); } catch (_) {}
        if (!res.ok) {
          _err('Could not change time', (body && body.detail) || ('Revert failed (HTTP ' + res.status + ').'));
          return;
        }
        window.loadDemoRequests();
      } catch (e) {
        _err('Network error', 'Could not reach the server. Check your connection and try again.');
      }
    };
    if (window.showStyledModal) {
      var content =
        '<p style="margin:0 0 14px 0;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
          'Change the confirmed time for <strong style="color:var(--text,#e5e7eb);">' + _esc(name) + '</strong>?' +
        '</p>' +
        '<div style="padding:14px 16px;background:linear-gradient(135deg,rgba(139,92,246,0.1),rgba(6,182,212,0.1));border:1px solid rgba(139,92,246,0.3);border-radius:8px;margin-bottom:14px;">' +
          '<div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;margin-bottom:6px;">Currently scheduled</div>' +
          '<div style="font-size:0.95rem;font-weight:600;color:var(--text,#e5e7eb);text-decoration:line-through;opacity:0.8;">' + _esc(slotHuman) + '</div>' +
        '</div>' +
        '<p style="margin:0;color:var(--text-gray,#94a3b8);font-size:0.82rem;line-height:1.5;">' +
          'This releases the time and moves the request back to <strong>Pending</strong>, so you can pick a different one of their 3 preferred slots. <strong style="color:#c4b5fd;">The prospect is not emailed</strong> until you accept a new time.' +
        '</p>';
      window.showStyledModal('Change scheduled time?', content, [
        { text: 'Keep the current time', style: 'ghost' },
        { text: '↻ Revert to pending', style: 'primary',
          onClick: function () { doRevert(); } },
      ], { size: 'sm' });
    } else {
      if (confirm('Revert this scheduled demo back to pending so you can pick a different slot?')) doRevert();
    }
  };

  // Admin-side cancel — real cancel, emails prospect. Optional message
  // textarea for a short reason ("Family emergency, need to reschedule").
  // Row moves to `cancelled`; prospect can request a fresh demo from
  // the homepage. Distinct from Delete (which is hard-delete + no email).
  window._demosCancel = function (reqId) {
    var row = _rowCache[reqId] || {};
    var name = row.name || 'this prospect';
    var slotHuman = (row.preferred_slots_human || [])[row.scheduled_slot_index] || 'the scheduled time';
    var doCancel = async function (message) {
      try {
        var res = await fetch('/api/admin/demo-requests/' + reqId + '/cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ message: message || '' }),
        });
        var body = null;
        try { body = await res.json(); } catch (_) {}
        if (!res.ok) {
          _err('Could not cancel', (body && body.detail) || ('Cancel failed (HTTP ' + res.status + ').'));
          return;
        }
        window.loadDemoRequests();
      } catch (e) {
        _err('Network error', 'Could not reach the server. Check your connection and try again.');
      }
    };
    if (window.showStyledModal) {
      var content =
        '<p style="margin:0 0 14px 0;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
          'Cancel this demo for <strong style="color:var(--text,#e5e7eb);">' + _esc(name) + '</strong>?' +
        '</p>' +
        '<div style="padding:14px 16px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.35);border-radius:8px;margin-bottom:14px;">' +
          '<div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.08em;color:#fca5a5;margin-bottom:6px;">Was scheduled for</div>' +
          '<div style="font-size:0.95rem;font-weight:600;color:var(--text,#e5e7eb);text-decoration:line-through;opacity:0.8;">' + _esc(slotHuman) + '</div>' +
        '</div>' +
        '<label style="display:block;font-size:0.72rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Short message to the prospect (optional)</label>' +
        '<textarea id="ad-cancel-msg-' + reqId + '" placeholder="e.g. Something came up on our end — reply with 2-3 times that work in the next week and we\'ll rebook." ' +
          'style="width:100%;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:var(--text,#e5e7eb);font-size:0.85rem;font-family:inherit;box-sizing:border-box;min-height:70px;resize:vertical;"></textarea>' +
        '<p style="margin:10px 0 0;color:var(--text-gray,#94a3b8);font-size:0.78rem;line-height:1.5;">' +
          'The prospect will get a branded cancel email with your message (blank = generic wording). Row moves to <strong style="color:#fca5a5;">Cancelled</strong> and stays in the queue for reference.' +
        '</p>';
      window.showStyledModal('Cancel demo?', content, [
        { text: 'Keep it scheduled', style: 'ghost' },
        { text: '✗ Cancel & notify prospect', style: 'danger',
          onClick: function () {
            var ta = document.getElementById('ad-cancel-msg-' + reqId);
            var msg = ta ? (ta.value || '').trim() : '';
            doCancel(msg);
          } },
      ], { size: 'sm', tone: 'error' });
    } else {
      if (confirm('Cancel this demo? The prospect will be emailed.')) doCancel('');
    }
  };

  // Per-row Teams / Zoom / Meet URL override. Empty string clears the
  // override and falls back to the platform-default URL from settings.
  // Both are re-embedded into the next reminder + any re-sent
  // confirmation (accepting/rejecting after the URL is set).
  window._demosSetMeetingUrl = function (reqId) {
    var row = _rowCache[reqId] || {};
    var name = row.name || 'this prospect';
    var current = (row.meeting_url || '').trim();
    var platformFallback = _platformMeetingUrl || '';
    var effective = current || platformFallback;

    var doSave = async function (url) {
      try {
        var res = await fetch('/api/admin/demo-requests/' + reqId + '/meeting-url', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ meeting_url: url || '' }),
        });
        var body = null;
        try { body = await res.json(); } catch (_) {}
        if (!res.ok) {
          _err('Could not save', (body && body.detail) || ('Save failed (HTTP ' + res.status + ').'));
          return;
        }
        window.loadDemoRequests();
      } catch (e) {
        _err('Network error', 'Could not reach the server. Check your connection and try again.');
      }
    };

    if (!window.showStyledModal) {
      var v = prompt('Meeting URL for ' + name + ' (blank to use platform default):', current);
      if (v === null) return;
      doSave(v.trim());
      return;
    }

    var fallbackNote = platformFallback
      ? ('<div style="margin-top:6px;font-size:0.72rem;color:var(--text-gray,#94a3b8);">' +
          'Leave blank to use the platform default: <span style="color:#7dd3fc;word-break:break-all;">' +
          _esc(platformFallback) + '</span></div>')
      : ('<div style="margin-top:6px;font-size:0.72rem;color:#fcd34d;">' +
          'No platform default set. The confirmation email will read "we\'ll send the link before the demo" until you paste one here or in Settings → Demo Pipeline.</div>');

    var content =
      '<p style="margin:0 0 14px 0;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
        'Set the join URL for <strong style="color:var(--text,#e5e7eb);">' + _esc(name) + '</strong>. Prospects click it to join in-browser — no account required.' +
      '</p>' +
      '<label style="display:block;font-size:0.72rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Meeting URL (Teams / Zoom / Meet)</label>' +
      '<input type="url" id="ad-mtg-url-' + reqId + '" placeholder="https://teams.microsoft.com/l/meetup-join/…" ' +
        'value="' + _esc(current) + '" ' +
        'style="width:100%;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:var(--text,#e5e7eb);font-size:0.85rem;font-family:inherit;box-sizing:border-box;">' +
      fallbackNote +
      '<div style="margin-top:14px;padding:10px 12px;background:linear-gradient(135deg,rgba(139,92,246,0.06),rgba(6,182,212,0.06));border:1px solid rgba(6,182,212,0.25);border-radius:6px;font-size:0.72rem;color:var(--text-gray,#94a3b8);">' +
        'Current effective URL in the next email: ' +
        (effective
           ? '<a href="' + _esc(effective) + '" target="_blank" rel="noopener" style="color:#7dd3fc;word-break:break-all;">' + _esc(effective) + '</a>'
           : '<span style="color:#fcd34d;">(none)</span>') +
      '</div>';

    var buttons = [
      { text: 'Cancel', style: 'ghost' },
      { text: 'Save', style: 'primary',
        onClick: function () {
          var el = document.getElementById('ad-mtg-url-' + reqId);
          var v = el ? (el.value || '').trim() : '';
          if (v && !/^https?:\/\//i.test(v)) {
            _err('Bad URL', 'Meeting URL must start with http:// or https://.');
            return false;  // keep modal open
          }
          doSave(v);
        } },
    ];
    if (current) {
      buttons.splice(1, 0, {
        text: 'Clear override',
        style: 'ghost',
        onClick: function () { doSave(''); },
      });
    }
    window.showStyledModal('Meeting URL', content, buttons, { size: 'sm' });
  };

  // Change outcome on a past-scheduled demo (Completed ↔ No Show).
  // Reached by clicking the status pill on any row where the demo
  // time has passed. The scheduler auto-transitions `scheduled` →
  // `completed` 60 min after the scheduled_at time; admin corrects
  // via this modal if the prospect didn't show up.
  window._demosSetOutcome = function (reqId, currentStatus) {
    var row = _rowCache[reqId] || {};
    var name = row.name || 'this prospect';
    var slotHuman = (row.preferred_slots_human || [])[row.scheduled_slot_index || 0]
                      || 'the scheduled time';
    var existingNotes = row.outcome_notes || '';

    var doSet = async function (outcome, notes) {
      try {
        var payload = { outcome: outcome };
        // Only send `outcome_notes` when the modal actually collected
        // a value — omitting the key entirely lets the endpoint leave
        // stored notes untouched. An empty string, on the other hand,
        // is an explicit clear.
        if (typeof notes === 'string') payload.outcome_notes = notes;
        var res = await fetch('/api/admin/demo-requests/' + reqId + '/outcome', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify(payload),
        });
        var body = null;
        try { body = await res.json(); } catch (_) {}
        if (!res.ok) {
          _err('Could not update', (body && body.detail) || ('Update failed (HTTP ' + res.status + ').'));
          return;
        }
        window.loadDemoRequests();
      } catch (e) {
        _err('Network error', 'Could not reach the server. Check your connection and try again.');
      }
    };

    if (!window.showStyledModal) {
      var pick = confirm('Set outcome for ' + name + ' to No Show? (Cancel = Completed)');
      var n = prompt('Notes about how the demo went (optional):', existingNotes);
      doSet(pick ? 'no_show' : 'completed', n == null ? undefined : n);
      return;
    }

    // Whole chip is the button — click either to pick + auto-close.
    // Current selection is highlighted so admin sees at-a-glance what
    // they'd be changing from.
    var completedActive = currentStatus === 'completed';
    var noShowActive    = currentStatus === 'no_show';
    var chip = function (active, color, outcomeVal, label, sub) {
      return (
        '<button data-outcome="' + outcomeVal + '" ' +
          'style="padding:14px 12px;background:' + (active ? color + '18' : 'rgba(255,255,255,0.02)') + ';' +
          'border:1px solid ' + (active ? color + '80' : 'rgba(255,255,255,0.10)') + ';' +
          'border-radius:8px;text-align:center;cursor:pointer;font-family:inherit;transition:all 0.12s;" ' +
          'onmouseover="this.style.background=\'' + color + '20\';this.style.borderColor=\'' + color + 'aa\';" ' +
          'onmouseout="this.style.background=\'' + (active ? color + '18' : 'rgba(255,255,255,0.02)') + '\';this.style.borderColor=\'' + (active ? color + '80' : 'rgba(255,255,255,0.10)') + '\';">' +
          '<div style="font-size:0.95rem;font-weight:700;color:' + (active ? color : 'var(--text,#e5e7eb)') + ';">' + label + '</div>' +
          '<div style="font-size:0.7rem;color:var(--text-gray,#94a3b8);margin-top:4px;">' + sub + '</div>' +
        '</button>'
      );
    };
    var content =
      '<p style="margin:0 0 12px 0;color:var(--text-gray,#94a3b8);font-size:0.9rem;line-height:1.5;">' +
        'Update the outcome of <strong style="color:var(--text,#e5e7eb);">' + _esc(name) + '</strong>\'s demo:' +
      '</p>' +
      '<div style="padding:10px 12px;background:linear-gradient(135deg,rgba(139,92,246,0.06),rgba(6,182,212,0.06));border:1px solid rgba(6,182,212,0.25);border-radius:6px;margin-bottom:14px;">' +
        '<div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;margin-bottom:3px;">Was scheduled for</div>' +
        '<div style="font-size:0.9rem;font-weight:600;color:var(--text,#e5e7eb);">' + _esc(slotHuman) + '</div>' +
      '</div>' +
      '<label style="display:block;font-size:0.72rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Notes (optional, internal only)</label>' +
      '<textarea id="outcomeNotes-' + reqId + '" placeholder="How the demo went, follow-up needed, next steps, etc." ' +
        'style="width:100%;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:var(--text,#e5e7eb);font-size:0.85rem;font-family:inherit;box-sizing:border-box;min-height:70px;resize:vertical;margin-bottom:14px;">' +
        _esc(existingNotes) +
      '</textarea>' +
      '<div id="outcomeChoices-' + reqId + '" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">' +
        chip(completedActive, '#22d3ee', 'completed', '✓ Completed', 'Demo happened') +
        chip(noShowActive,    '#f97316', 'no_show',   '✗ No Show',   'Prospect didn\'t join') +
      '</div>' +
      '<p style="margin:14px 0 0;font-size:0.72rem;color:var(--text-gray,#94a3b8);text-align:center;">' +
        'Notes save when you pick an outcome. Nothing is emailed to the prospect.' +
      '</p>';

    window.showStyledModal('Demo outcome', content, [], { size: 'sm' });
    // Wire the chips AFTER the modal DOM mounts. gf-modals renders
    // synchronously so the container is available immediately.
    setTimeout(function () {
      var wrap = document.getElementById('outcomeChoices-' + reqId);
      if (!wrap) return;
      wrap.querySelectorAll('button[data-outcome]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var v = btn.getAttribute('data-outcome');
          var ta = document.getElementById('outcomeNotes-' + reqId);
          var n = ta ? (ta.value || '').trim() : '';
          if (window.closeAllModals) window.closeAllModals();
          doSet(v, n);
        });
      });
      // Autofocus the notes area so admin can start typing immediately.
      var ta = document.getElementById('outcomeNotes-' + reqId);
      if (ta) { try { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); } catch (_) {} }
    }, 0);
  };

  // Hook into tab switch — also autoload on page open if URL has ?tab=demos
  document.addEventListener('DOMContentLoaded', function () {
    // Kickoff badge check even before the tab is opened
    setTimeout(function () {
      if (window.loadDemoRequests) window.loadDemoRequests();
    }, 800);

    // Direct-link support: ?tab=demos or ?tab=messages → open Messages
    // top-level tab with Demo Requests sub-tab active. Legacy `?tab=demos`
    // URLs (from before the Jul 2026 restructure) still work.
    try {
      var params = new URLSearchParams(window.location.search);
      var tabParam = params.get('tab');
      if ((tabParam === 'demos' || tabParam === 'messages') && window.switchTab) {
        setTimeout(function () {
          window.switchTab('messages');
          if (window.switchMessagesSubTab) window.switchMessagesSubTab('demos');
        }, 100);
      }
    } catch (_) {}

    // When switchTab fires for 'messages', reload the demo list so
    // its badge is fresh (the top-level Messages badge sums all three).
    var origSwitch = window.switchTab;
    if (origSwitch && !window._demosTabHooked) {
      window._demosTabHooked = true;
      window.switchTab = function (tab) {
        var r = origSwitch.apply(this, arguments);
        if (tab === 'messages') {
          if (window._updateDemoMeetingUrlWarn) {
            try { window._updateDemoMeetingUrlWarn(); } catch (_) {}
          }
        }
        return r;
      };
    }
  });
})();
