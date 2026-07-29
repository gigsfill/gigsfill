/* venue-hold-offers-banner.js — Pending Hold Offers banner on
 * venue-create-gigs.
 *
 * Mirror of hold-offers-banner.js but from the venue perspective.
 * Polls /api/me/venue-hold-offers on load + every 60s. Renders one
 * bubble per held gig the venue user can manage. Each bubble shows:
 *   - 🔒 gig date + title
 *   - For active: "Offered to <artist-name> · X hours left"
 *                  + queued/declined counts
 *   - For exhausted: red flashing border + "Open to All" / "Cancel Gig" buttons
 *
 * Filtered to the current venue_id in the URL so a multi-venue user
 * only sees the venue they're currently viewing (mirrors the artist
 * banner's artist_id filter).
 */
(function () {
  const POLL_MS = 60_000;
  const BANNER_ID = 'venueHoldOffersBanner';

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  function _fmtTimeLeft(h) {
    if (h == null) return '';
    const totalMins = Math.round(h * 60);
    if (totalMins <= 0) return 'expiring now';
    if (totalMins < 60) return `${totalMins} ${totalMins === 1 ? 'minute' : 'minutes'} left`;
    const hrs = Math.round(h);
    if (hrs < 24) return `${hrs} ${hrs === 1 ? 'hour' : 'hours'} left`;
    const days = Math.round(h / 24);
    return `${days} ${days === 1 ? 'day' : 'days'} left`;
  }

  function _fmtDate(d) {
    if (!d) return '';
    try {
      const dt = new Date(d + 'T00:00:00');
      if (isNaN(dt.getTime())) return d;
      return dt.toLocaleDateString(undefined, {
        weekday: 'short', month: 'short', day: 'numeric'
      });
    } catch (_) { return d; }
  }

  function _currentVenueId() {
    try {
      const params = new URLSearchParams(window.location.search);
      const v = parseInt(params.get('venue_id'), 10);
      return Number.isFinite(v) && v > 0 ? v : null;
    } catch (_) { return null; }
  }

  // Install the keyframe once. Exhausted bubbles get a pulsing red border
  // so the venue can't miss that a gig needs them to pick Open-to-All or
  // Cancel.
  function _installStyles() {
    if (document.getElementById('vhob-styles')) return;
    const st = document.createElement('style');
    st.id = 'vhob-styles';
    // !important on every animated property to beat the inline style
    // we set on the bubble (inline styles otherwise win over rules,
    // but with !important the animation wins). Cycle alternates a deep
    // red panel with a softer pink-tinted panel so it reads as a flash
    // even on dark backgrounds. box-shadow also pulses outward.
    st.textContent = `
      @keyframes vhob-flash {
        0%, 100% {
          background-color: rgba(239,68,68,0.18) !important;
          border-color: #ef4444 !important;
          box-shadow: 0 0 0 0 rgba(239,68,68,0.65), 0 0 14px 0 rgba(239,68,68,0.35) !important;
        }
        50% {
          background-color: rgba(239,68,68,0.05) !important;
          border-color: #fca5a5 !important;
          box-shadow: 0 0 0 8px rgba(239,68,68,0.0), 0 0 22px 4px rgba(239,68,68,0.55) !important;
        }
      }
      .vhob-flash { animation: vhob-flash 1.2s ease-in-out infinite !important; }
    `;
    document.head.appendChild(st);
  }

  async function load() {
    const banner = document.getElementById(BANNER_ID);
    if (!banner) return;
    _installStyles();
    let data;
    try {
      const res = await fetch('/api/me/venue-hold-offers', { credentials: 'include' });
      if (!res.ok) return;
      data = await res.json();
    } catch (_) { return; }
    const vid = _currentVenueId();
    let offers = (data && data.offers) || [];
    if (vid != null) {
      offers = offers.filter(o => parseInt(o.venue_id, 10) === vid);
    }
    if (!offers.length) {
      banner.innerHTML = '';
      banner.style.marginBottom = '';
      return;
    }
    banner.style.marginBottom = '20px';
    const needsActionCount = offers.filter(o => o.needs_action).length;

    // Group by recurring_group_id so a 26-week series collapses to a
    // single summary row with group actions. Standalone (non-recurring)
    // gigs stay as individual rows (rgid=null bucket). Persistent
    // expand/collapse state on the banner element so a 60s poll doesn't
    // snap groups closed.
    if (!banner._vhobExpanded) banner._vhobExpanded = {};
    const groups = {};
    const standalone = [];
    offers.forEach(o => {
      if (o.recurring_group_id) {
        if (!groups[o.recurring_group_id]) groups[o.recurring_group_id] = [];
        groups[o.recurring_group_id].push(o);
      } else {
        standalone.push(o);
      }
    });
    // Sort each group by date
    Object.values(groups).forEach(arr => arr.sort((a,b) => (a.gig_date||'').localeCompare(b.gig_date||'')));
    standalone.sort((a, b) => {
      const aE = a.hold_status === 'exhausted' ? 0 : 1;
      const bE = b.hold_status === 'exhausted' ? 0 : 1;
      if (aE !== bE) return aE - bE;
      return (a.gig_date || '').localeCompare(b.gig_date || '');
    });

    const headerLabel = needsActionCount
      ? `${offers.length} pending hold${offers.length === 1 ? '' : 's'} · ${needsActionCount} need${needsActionCount === 1 ? 's' : ''} your call`
      : `${offers.length} pending hold${offers.length === 1 ? '' : 's'}`;

    // Render groups first (exhausted ones bubble to top), then standalones.
    const groupKeys = Object.keys(groups).sort((a, b) => {
      const ga = groups[a], gb = groups[b];
      const aExh = ga.some(o => o.hold_status === 'exhausted') ? 0 : 1;
      const bExh = gb.some(o => o.hold_status === 'exhausted') ? 0 : 1;
      if (aExh !== bExh) return aExh - bExh;
      return (ga[0].gig_date || '').localeCompare(gb[0].gig_date || '');
    });

    const groupsHtml = groupKeys.map(rgid => _seriesGroupHtml(rgid, groups[rgid], banner._vhobExpanded[rgid])).join('');
    const standaloneHtml = standalone.map(_rowHtml).join('');

    banner.innerHTML = `
      <div style="background:rgba(124,107,255,0.08);border:1.5px solid rgba(124,107,255,0.5);border-radius:10px;padding:10px 14px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
          <span style="font-size:1rem;">🔒</span>
          <span style="font-size:0.78rem;font-weight:700;color:#a78bfa;text-transform:uppercase;letter-spacing:0.06em;">
            ${_esc(headerLabel)}
          </span>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;">
          ${groupsHtml}${standaloneHtml}
        </div>
      </div>
    `;
    banner.querySelectorAll('.vhob-open').forEach(btn => {
      btn.addEventListener('click', () => _openToAll(parseInt(btn.dataset.gig, 10), btn));
    });
    banner.querySelectorAll('.vhob-cancel').forEach(btn => {
      btn.addEventListener('click', () => _cancelGig(parseInt(btn.dataset.gig, 10),
        btn.dataset.date, btn.dataset.title, btn));
    });
    banner.querySelectorAll('.vhob-toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const rgid = btn.dataset.rgid;
        banner._vhobExpanded[rgid] = !banner._vhobExpanded[rgid];
        load();
      });
    });
    banner.querySelectorAll('.vhob-group-open').forEach(btn => {
      btn.addEventListener('click', () => _seriesOpenAll(btn.dataset.rgid, btn));
    });
    banner.querySelectorAll('.vhob-group-cancel').forEach(btn => {
      btn.addEventListener('click', () => _seriesCancelAll(btn.dataset.rgid, btn));
    });
  }

  function _seriesGroupHtml(rgid, offers, isExpanded) {
    // Aggregate stats across the series
    const dates = offers.map(o => o.gig_date).sort();
    const startDate = dates[0];
    const endDate = dates[dates.length - 1];
    const totalGigs = offers.length;
    const totalBookedSlots = offers.reduce((s, o) => s + (o.booked_slot_count || 0), 0);
    const totalOpenSlots = offers.reduce((s, o) => s + (o.open_slot_count || 0), 0);
    const totalSlots = offers.reduce((s, o) => s + (o.total_slot_count || 0), 0);
    const exhaustedCount = offers.filter(o => o.hold_status === 'exhausted').length;
    const activeCount = offers.filter(o => o.hold_status === 'active').length;
    const groupExhausted = exhaustedCount > 0;

    // Cycle border/background based on whether ANY gig is exhausted
    const flashStyle = groupExhausted
      ? 'background-color: rgba(239,68,68,0.10); border:1.5px solid #ef4444; animation: vhob-flash 1.4s ease-in-out infinite;'
      : 'background-color: rgba(124,107,255,0.05); border:1px solid rgba(124,107,255,0.35);';

    // Summary on the SAME line as the title (parenthesised), so the
    // venue gets the overview at a glance without a second line.
    const summaryParen = groupExhausted
      ? `<span style="color:#fca5a5;font-weight:600;font-size:0.82rem;margin-left:10px;">(${exhaustedCount} of ${totalGigs} date${totalGigs === 1 ? '' : 's'} need a decision · ${totalOpenSlots} slot${totalOpenSlots === 1 ? '' : 's'} still open)</span>`
      : `<span style="color:var(--text-gray);font-size:0.82rem;margin-left:10px;">(${activeCount} of ${totalGigs} date${totalGigs === 1 ? '' : 's'} in rotation · ${totalBookedSlots}/${totalSlots} slot${totalSlots === 1 ? '' : 's'} booked)</span>`;

    // Group action buttons (only meaningful for exhausted gigs).
    // Indented to vertically align with the title text — the title
    // sits inside the right-hand flex column (after toggle + icon),
    // so the buttons live in the SAME flex column to inherit the
    // same left-edge.
    const groupActions = exhaustedCount > 0 ? `
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">
        <button type="button" class="vhob-group-open" data-rgid="${_esc(rgid)}"
          title="Open the ${exhaustedCount} exhausted date${exhaustedCount === 1 ? '' : 's'} in this series to all artists — any matching artist can book ${exhaustedCount === 1 ? 'it' : 'them'}. Booked slots stay booked. Nothing is deleted."
          style="padding:5px 12px;background:rgba(34,197,94,0.16);border:1px solid rgba(34,197,94,0.5);border-radius:5px;color:#86efac;cursor:pointer;font-size:0.74rem;font-weight:600;">
          Open To All · Series (${exhaustedCount})
        </button>
        <button type="button" class="vhob-group-cancel" data-rgid="${_esc(rgid)}"
          title="For each of the ${exhaustedCount} exhausted date${exhaustedCount === 1 ? '' : 's'}: DELETE every empty slot. If a gig has no booked slots remaining (single-slot gig, or multi-slot gig with nothing booked), the whole gig is DELETED — wiped from your calendar. If a multi-slot gig has at least one booked slot, the booked slot stays and only the empty slots are removed. Booked slots are never touched."
          style="padding:5px 12px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.45);border-radius:5px;color:#f87171;cursor:pointer;font-size:0.74rem;font-weight:600;">
          Delete Empty Slots · Series
        </button>
      </div>` : '';

    const childRows = isExpanded
      ? `<div style="display:flex;flex-direction:column;gap:6px;margin-top:10px;padding-top:10px;border-top:1px dashed rgba(124,107,255,0.25);">
           ${offers.map(_rowHtml).join('')}
         </div>`
      : '';

    return `
      <div style="${flashStyle};border-radius:8px;padding:10px 14px;">
        <div style="display:flex;align-items:flex-start;gap:10px;">
          <button type="button" class="vhob-toggle" data-rgid="${_esc(rgid)}"
            title="${isExpanded ? 'Collapse this series' : 'Expand to see every date'}"
            style="background:transparent;border:none;color:#c4b5fd;cursor:pointer;font-size:1rem;padding:0;line-height:1.4;flex:0 0 auto;">
            ${isExpanded ? '▼' : '▶'}
          </button>
          <span style="font-size:1rem;flex:0 0 auto;line-height:1.4;">🔁</span>
          <!-- Title row + buttons share this column so buttons inherit
               the title's left edge (Jul 2026 layout fix). -->
          <div style="display:flex;flex-direction:column;flex:1;min-width:200px;">
            <div style="display:flex;align-items:baseline;flex-wrap:wrap;">
              <span style="font-weight:700;color:var(--text);font-size:0.88rem;">
                Recurring series · ${_fmtDate(startDate)} → ${_fmtDate(endDate)}
              </span>
              ${summaryParen}
            </div>
            ${groupActions}
          </div>
        </div>
        ${childRows}
      </div>
    `;
  }

  function _rowHtml(o) {
    const title = o.gig_title ? ` <span style="color:var(--text-gray);font-weight:400;">"${_esc(o.gig_title)}"</span>` : '';
    const date = _fmtDate(o.gig_date);
    const isExhausted = o.hold_status === 'exhausted';

    // Build the per-gig status line
    let statusLine = '';
    if (isExhausted) {
      // Truthful breakdown by actual artist state (Jul 2026 — replaces
      // the generic "All N declined or expired" which lumped freq
      // conflicts and series-accept-elsewhere into the wrong bucket).
      // Each segment is a hover-tooltip with the artist names.
      const parts = [];
      const _names = (arr) => arr.map(a => a.artist_name || `Artist ${a.artist_id}`).join('\n');
      if ((o.declined_artists || []).length) {
        parts.push(
          `<span title="${_esc(_names(o.declined_artists))}" style="color:#fca5a5;font-weight:600;cursor:help;border-bottom:1px dotted #fca5a5;">${o.declined_artists.length} declined</span>`
        );
      }
      if ((o.expired_artists || []).length) {
        parts.push(
          `<span title="${_esc(_names(o.expired_artists))}" style="color:#fbbf24;font-weight:600;cursor:help;border-bottom:1px dotted #fbbf24;">${o.expired_artists.length} timed out</span>`
        );
      }
      if ((o.freq_blocked_artists || []).length) {
        const detail = o.freq_blocked_artists.map(a =>
          `${a.artist_name || 'Artist '+a.artist_id} (played ${a.days_apart}d ${a.direction})`
        ).join('\n');
        parts.push(
          `<span title="${_esc(detail)}" style="color:#fcd34d;font-weight:600;cursor:help;border-bottom:1px dotted #fcd34d;">${o.freq_blocked_artists.length} freq-blocked</span>`
        );
      }
      if ((o.booked_elsewhere_artists || []).length) {
        const detail = o.booked_elsewhere_artists.map(a =>
          `${a.artist_name || 'Artist '+a.artist_id} → booked ${a.other_date}`
        ).join('\n');
        parts.push(
          `<span title="${_esc(detail)}" style="color:#86efac;font-weight:600;cursor:help;border-bottom:1px dotted #86efac;">${o.booked_elsewhere_artists.length} booked other date${o.booked_elsewhere_artists.length === 1 ? '' : 's'}</span>`
        );
      }
      const summary = parts.length
        ? parts.join(' <span style="color:var(--text-muted);">·</span> ')
        : '<span style="color:#fca5a5;font-weight:600;">No artists left</span>';
      statusLine = `<span style="color:#fca5a5;font-weight:600;font-size:0.82rem;">Exhausted — </span><span style="font-size:0.82rem;">${summary}</span><span style="color:var(--text-muted);font-size:0.82rem;"> · pick a next step</span>`;
    } else if (o.current_offer) {
      const co = o.current_offer;
      const hrs = co.hours_remaining;
      const urgent = hrs != null && hrs < 6;
      const color = urgent ? '#ef4444' : '#fcd34d';
      // "declined" was misleading — it lumped together explicit declines,
      // expired offers, AND artists who couldn't accept due to frequency
      // conflicts or who booked another date in the series. Removed from
      // the banner. The hold-management panel shows per-artist detail.
      const queuedExtra = o.queued_count
        ? ` <span style="color:var(--text-muted);font-weight:500;">· ${o.queued_count} queued</span>`
        : '';
      statusLine = `
        <span style="color:var(--text);font-size:0.82rem;">
          Offered to: <span style="color:#a78bfa;font-weight:700;">${_esc(co.artist_name)}</span>
          <span style="color:${color};font-weight:600;">(${_fmtTimeLeft(hrs)})</span>
          ${queuedExtra}
        </span>`;
    } else {
      // Active hold but no current offer — typically a transitory state
      // (between offers). Just show queued count.
      const queuedTxt = o.queued_count
        ? `${o.queued_count} queued`
        : 'Advancing to next artist…';
      statusLine = `<span style="color:var(--text-muted);font-size:0.82rem;">${queuedTxt}</span>`;
    }

    // Action buttons row — only shown when exhausted.
    const actions = isExhausted ? `
      <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">
        <button type="button" class="vhob-open" data-gig="${parseInt(o.gig_id, 10)}"
          data-tooltip="Open the remaining slots to all artists."
          style="padding:6px 14px;background:#10b981;border:none;border-radius:5px;color:#fff;cursor:pointer;font-size:0.78rem;font-weight:600;">Open to All</button>
        <button type="button" class="vhob-cancel" data-gig="${parseInt(o.gig_id, 10)}"
          data-date="${_esc(o.gig_date)}" data-title="${_esc(o.gig_title || '')}"
          data-tooltip="Either cancel this entire gig, or just the remaining unbooked slots."
          style="padding:6px 14px;background:transparent;border:1px solid #dc2626;border-radius:5px;color:#f87171;cursor:pointer;font-size:0.78rem;font-weight:600;">Cancel Gig/Slots</button>
      </div>` : '';

    const bubbleCls = isExhausted ? 'vhob-flash' : '';
    const bubbleStyle = isExhausted
      ? 'background:rgba(239,68,68,0.10);border:1.5px solid #ef4444;border-radius:6px;padding:10px 14px;'
      : 'background:rgba(0,0,0,0.2);border-radius:6px;padding:10px 14px;border:1px solid rgba(124,107,255,0.25);';

    // Slot counts chip — "2/3 booked" or "1 of 2 open" — sits inline
    // next to the date so the venue sees gig progress at a glance.
    const booked = o.booked_slot_count || 0;
    const total = o.total_slot_count || 0;
    const open = o.open_slot_count || 0;
    let slotChip = '';
    if (total > 0) {
      const isFull = open === 0 && booked === total;
      const color = isFull ? '#22c55e' : (open === total ? '#94a3b8' : '#fcd34d');
      slotChip = `<span style="font-size:0.74rem;font-weight:600;color:${color};background:rgba(148,163,184,0.10);border:1px solid rgba(148,163,184,0.25);border-radius:999px;padding:1px 8px;white-space:nowrap;" title="${booked} of ${total} slot${total === 1 ? '' : 's'} booked, ${open} open">${booked}/${total} booked · ${open} open</span>`;
    }

    return `<div class="${bubbleCls}" style="${bubbleStyle}">
      <div style="display:flex;align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap;">
        <span style="font-size:0.88rem;color:var(--text);display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <span style="color:#fcd34d;font-weight:600;">${date}</span>${title}
          ${slotChip}
        </span>
        <span style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          ${statusLine}
        </span>
      </div>
      ${actions}
    </div>`;
  }

  async function _openToAll(gigId, btn) {
    if (!gigId) return;
    const ok = window.showConfirm
      ? await new Promise(res => window.showConfirm(
          'Open remaining slots to all artists?',
          'The hold list ends here and the empty slots become visible to every artist in your area (subject to your blast settings).',
          () => res(true),
          () => res(false),
          { confirmLabel: 'Open to All', cancelLabel: 'Cancel', confirmStyle: 'success' }
        ))
      : confirm('Open remaining slots to all artists?');
    if (!ok) return;
    btn.disabled = true; const orig = btn.textContent; btn.textContent = '…';
    try {
      const res = await fetch(`/api/gigs/${gigId}/hold/resolve-exhausted`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'open_all' }),
      });
      if (res.ok) {
        // Close the venue's Edit-Gig modal if it's open showing the
        // cancelled (or now-open-to-all) gig. The modal id is #gigModal
        // and it uses the .hidden class to toggle visibility (see
        // venue.create-gigs.js).
        try {
          const modal = document.getElementById('gigModal');
          if (modal) modal.classList.add('hidden');
        } catch (_) {}
        // Invalidate the gig cache so the calendar re-fetches from the
        // server rather than serving the stale list (which still has
        // the cancelled gig in it).
        try { if (typeof window.invalidateGigs === 'function') window.invalidateGigs(); } catch (_) {}
        await load();
        if (typeof window.refreshGigs === 'function') {
          try { await window.refreshGigs(); } catch (_) {}
        }
        // Success feedback (Jun 2026 audit fix): the prior behavior
        // was silent — banner repainted without telling the venue
        // what happened. Without a toast the venue might not realize
        // the action took effect or might re-click thinking it didn't
        // work. Mirrors the hold-mgmt panel's success copy so the two
        // surfaces feel consistent.
        if (typeof window.showSuccessModal === 'function') {
          try {
            window.showSuccessModal(
              'Opened to all artists',
              'Empty slots are now open for any artist to book. The slot rows are still there — they just left the hold cycle.'
            );
          } catch (_) {}
        }
      } else {
        btn.textContent = '✗ Failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
      }
    } catch (_) {
      btn.textContent = '✗ Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
    }
  }

  async function _cancelGig(gigId, date, title, btn) {
    // Jun 2026: button no longer auto-cancels the gig. Per venue
    // direction, open the standard Edit Gig modal so the venue can
    // choose: cancel individual unbooked slots OR cancel the whole
    // gig — same controls they already use for editing. The button
    // label is now "Cancel Gig/Slots".
    if (!gigId) return;
    if (typeof window.openGigById === 'function') {
      try {
        await window.openGigById(gigId);
      } catch (e) {
        console.error('openGigById failed from banner:', e);
      }
      return;
    }
    // Legacy fallback (shouldn't fire — venue.create-gigs.js exposes
    // openGigById; this is here only if the page didn't load it).
    const label = title ? `"${title}" on ${_fmtDate(date)}` : `the gig on ${_fmtDate(date)}`;
    const ok = window.showConfirm
      ? await new Promise(res => window.showConfirm(
          'Cancel this gig?',
          `Couldn't open the Edit Gig modal — fall back to cancelling ${label} entirely? Empty slots and all. This cannot be undone.`,
          () => res(true),
          () => res(false),
          { tone: 'warning', confirmStyle: 'danger',
            confirmLabel: 'Cancel Gig', cancelLabel: 'Keep Gig' }
        ))
      : confirm(`Cancel ${label}? This cannot be undone.`);
    if (!ok) return;
    btn.disabled = true; const orig = btn.textContent; btn.textContent = '…';
    try {
      const res = await fetch(`/api/gigs/${gigId}/with-slots`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (res.ok) {
        try {
          const modal = document.getElementById('gigModal');
          if (modal) modal.classList.add('hidden');
        } catch (_) {}
        try { if (typeof window.invalidateGigs === 'function') window.invalidateGigs(); } catch (_) {}
        await load();
        if (typeof window.refreshGigs === 'function') {
          try { await window.refreshGigs(); } catch (_) {}
        }
      } else {
        btn.textContent = '✗ Failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
      }
    } catch (_) {
      btn.textContent = '✗ Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
    }
  }

  async function _seriesOpenAll(rgid, btn) {
    if (!rgid) return;
    const ok = window.showConfirm
      ? await new Promise(res => window.showConfirm(
          'Open exhausted dates to all artists?',
          'This opens every EXHAUSTED date in this recurring series to all artists — any matching artist can book them. Dates whose hold rotation is still active are untouched. Booked slots stay booked. Nothing is deleted.',
          () => res(true),
          () => res(false),
          { confirmLabel: 'Open Series to All', cancelLabel: 'Not Yet', confirmStyle: 'success' }
        ))
      : confirm('Open exhausted dates in this series to all artists?');
    if (!ok) return;
    btn.disabled = true; const orig = btn.textContent; btn.textContent = '…';
    try {
      const res = await fetch(`/api/series/${encodeURIComponent(rgid)}/hold/resolve-exhausted`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'open_all' }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        if (window.showSuccessModal) window.showSuccessModal('Done', data.message || 'Series opened to all.');
        if (typeof window.invalidateGigs === 'function') window.invalidateGigs();
        if (typeof window.renderCalendar === 'function') window.renderCalendar();
        load();
      } else {
        btn.textContent = '✗ Failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
        if (window.showErrorModal) window.showErrorModal('Failed', data.message || 'Try again.');
      }
    } catch (e) {
      btn.textContent = '✗ Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
    }
  }

  async function _seriesCancelAll(rgid, btn) {
    if (!rgid) return;
    const ok = window.showConfirm
      ? await new Promise(res => window.showConfirm(
          'Delete empty slots across this series?',
          'For each exhausted date in this recurring series, this DELETES every empty slot. If a gig has no booked slots, the whole gig is removed from your calendar. If a multi-slot gig has at least one booked slot, only the empty slots are deleted and the booked ones stay. Booked slots are never touched. This cannot be undone.',
          () => res(true),
          () => res(false),
          { tone: 'warning', confirmStyle: 'danger',
            confirmLabel: 'Delete Empty Slots', cancelLabel: 'Keep' }
        ))
      : confirm('Delete empty slots across this series?');
    if (!ok) return;
    btn.disabled = true; const orig = btn.textContent; btn.textContent = '…';
    try {
      const res = await fetch(`/api/series/${encodeURIComponent(rgid)}/hold/resolve-exhausted`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'cancel_empty' }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        if (window.showSuccessModal) window.showSuccessModal('Done', data.message || 'Cancelled.');
        if (typeof window.invalidateGigs === 'function') window.invalidateGigs();
        if (typeof window.renderCalendar === 'function') window.renderCalendar();
        load();
      } else {
        btn.textContent = '✗ Failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
        if (window.showErrorModal) window.showErrorModal('Failed', data.message || 'Try again.');
      }
    } catch (e) {
      btn.textContent = '✗ Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    load();
    setInterval(load, POLL_MS);
    // Refresh on tab focus so a venue who toggled away to act elsewhere
    // sees the latest state when they come back.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') load();
    });
    window.addEventListener('focus', load);
  });

  // Expose for other scripts to nudge an immediate refresh after a
  // hold-related action.
  window.refreshVenueHoldOffersBanner = load;
})();
