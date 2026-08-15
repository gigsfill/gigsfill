/* hold-mgmt.js — Venue's hold-management panel inside the gig modal.
 *
 * Renders a self-contained card at the top of the gig modal (#modalBody)
 * showing live hold state + actions. Lazy-loaded via
 * GET /api/gigs/{gig_id}/hold-status; safe to call on any gig (no-op
 * if not held).
 *
 * Two states the venue cares about:
 *   - active: someone is being offered the gig right now. Show who,
 *             how much time they have, who's queued behind them, who
 *             declined. Actions: Skip current artist, Release hold,
 *             Reorder waitlist.
 *   - exhausted: everyone said no / timed out. Show open slot summary.
 *                Actions: Open to all, Cancel empty slots.
 *
 * Looks like a regular content card (same vocabulary as the other
 * modal sections) with an amber accent so it stands out as the
 * 'this needs your attention' panel.
 */
(function () {
  const PANEL_ID = 'holdMgmtPanel';

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  function _fmtTime(t) {
    if (!t) return '';
    const parts = String(t).split(':');
    const h = parseInt(parts[0], 10);
    const m = parts[1] || '00';
    const ampm = h >= 12 ? 'PM' : 'AM';
    return ((h % 12) || 12) + ':' + m + ' ' + ampm;
  }

  async function renderPanel(gigId) {
    // Remove any prior render
    const prior = document.getElementById(PANEL_ID);
    if (prior) prior.remove();

    let data;
    try {
      const res = await fetch(`/api/gigs/${gigId}/hold-status`, { credentials: 'include' });
      if (!res.ok) return;
      data = await res.json();
    } catch (e) {
      return;  // silent — the rest of the modal is usable
    }
    if (!data || !data.is_held) return;

    // Prefer .modal-section (venue-create-gigs.html's edit form
    // container — the modal there has no #modalBody element, only
    // <section class="modal-section">). Fallback to #modalBody on
    // pages that have it.
    const body = document.querySelector('.modal-section') || document.getElementById('modalBody');
    if (!body) return;

    const isActive = data.hold_status === 'active';
    const isExhausted = data.hold_status === 'exhausted';

    const panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.style.cssText = 'background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.35);border-radius:8px;padding:14px 16px;margin:0 0 18px 0;';

    let html = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <span style="font-size:1.05rem;">🔒</span>
        <span style="font-size:0.85rem;font-weight:700;color:#fcd34d;text-transform:uppercase;letter-spacing:0.06em;">
          ${isExhausted ? 'Hold Exhausted — Action Needed' : 'Hold Active'}
        </span>
      </div>`;

    if (isExhausted) {
      // ── Exhausted state: big resolution buttons ──
      html += `<p style="font-size:0.84rem;color:var(--text-gray);margin:0 0 12px 0;">
        Every artist on your hold list either declined or didn't respond in time.
        ${data.open_slot_count} slot${data.open_slot_count === 1 ? '' : 's'} still need${data.open_slot_count === 1 ? 's' : ''} someone — your call:
      </p>`;
      if (data.open_slots && data.open_slots.length) {
        html += '<div style="font-size:0.78rem;color:var(--text);margin-bottom:10px;">Empty: ' +
          data.open_slots.map(s => `Slot ${s.slot_number} (${_fmtTime(s.start_time)}–${_fmtTime(s.end_time)})`).join(', ') +
          '</div>';
      }
      html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;">
        <button type="button" id="holdResolveOpenBtn"
          style="padding:7px 16px;background:rgba(34,197,94,0.16);border:1px solid rgba(34,197,94,0.5);border-radius:5px;color:#86efac;cursor:pointer;font-size:0.82rem;font-weight:600;">
          Open empty slots to all
        </button>
        <button type="button" id="holdResolveCancelBtn"
          style="padding:7px 16px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.45);border-radius:5px;color:#f87171;cursor:pointer;font-size:0.82rem;font-weight:600;">
          Cancel empty slots
        </button>
      </div>`;
    } else {
      // ── Active state: per-bucket rotation status (Jun 2026 Option A) ──
      // One card per distinct open-slot artist_type. Buckets advance
      // independently, so the venue sees the live offer for each type
      // — AND a per-type notice when a bucket is dead (no candidates of
      // that type queued, or all of them declined). Lets the venue act
      // on the dead bucket without waiting for the rest of the rotation.
      const _typeIcon = t => ({'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'❓', 'Open Mic MC':'🎙️', 'Karaoke MC':'🎶'}[t] || '🎵');
      const _fmtRemaining = (h, reminder) => {
        if (h == null) return '—';
        const totalMins = Math.round(h * 60);
        let s;
        if (totalMins <= 0) s = 'expiring now';
        else if (totalMins < 60) s = `${totalMins} ${totalMins === 1 ? 'minute' : 'minutes'} remaining`;
        else if (h < 24) { const hh = Math.round(h); s = `${hh} ${hh === 1 ? 'hour' : 'hours'} remaining`; }
        else { const dd = Math.round(h/24); s = `${dd} ${dd === 1 ? 'day' : 'days'} remaining`; }
        return reminder ? `${s}, reminder sent` : s;
      };
      const buckets = Array.isArray(data.buckets) ? data.buckets : [];
      if (buckets.length > 0) {
        html += '<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px;">';
        buckets.forEach(b => {
          const icon = _typeIcon(b.artist_type);
          const slotCnt = b.slot_count || 0;
          const slotLbl = `${slotCnt} ${slotCnt === 1 ? 'slot' : 'slots'}`;
          const headerHtml = `<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
            <span style="font-size:0.95rem;">${icon}</span>
            <span style="font-weight:700;color:var(--text);font-size:0.84rem;">${_esc(b.artist_type)}</span>
            <span style="color:var(--text-gray);font-size:0.75rem;">· ${slotLbl}</span>
          </div>`;
          let bodyHtml = '';
          let cardBg = 'rgba(0,0,0,0.25)';
          let cardBorder = '1px solid rgba(255,255,255,0.05)';
          if (b.state === 'active' && b.in_flight) {
            const inf = b.in_flight;
            bodyHtml = `<div style="font-size:0.82rem;color:var(--text);">
              <span style="font-size:0.65rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin-right:6px;">Offered to:</span>
              <span style="font-weight:700;">${_esc(inf.artist_name || 'Artist #'+inf.artist_id)}</span>
              <span style="color:var(--text-gray);margin-left:6px;">(${_fmtRemaining(inf.hours_remaining, inf.reminder_sent)})</span>
            </div>`;
          } else if (b.state === 'active') {
            // No in-flight but candidates remain. Shouldn't usually
            // happen — send_next_hold_offer fires immediately — but
            // surface it as a transient between-offers state.
            bodyHtml = `<div style="font-size:0.82rem;color:var(--text-gray);">Advancing to next ${_esc(b.artist_type)} artist…</div>`;
          } else if (b.state === 'no_candidates') {
            cardBg = 'rgba(239,68,68,0.08)';
            cardBorder = '1px solid rgba(239,68,68,0.35)';
            bodyHtml = `<div style="font-size:0.8rem;color:#fca5a5;margin-bottom:8px;line-height:1.45;">
              <strong>No ${_esc(b.artist_type)} artists on your hold list</strong> — this ${slotLbl === '1 slot' ? 'slot' : 'group of slots'} won't fill through the hold rotation.
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <button type="button" class="hold-bucket-add" data-type="${_esc(b.artist_type)}"
                style="padding:5px 12px;background:rgba(124,107,255,0.15);border:1px solid rgba(124,107,255,0.45);border-radius:4px;color:#c4b5fd;cursor:pointer;font-size:0.74rem;font-weight:600;">+ Add ${_esc(b.artist_type)} artist</button>
              <button type="button" class="hold-bucket-cancel" data-type="${_esc(b.artist_type)}"
                style="padding:5px 12px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.45);border-radius:4px;color:#f87171;cursor:pointer;font-size:0.74rem;font-weight:600;">Cancel ${_esc(b.artist_type)} slot${slotCnt === 1 ? '' : 's'}</button>
            </div>`;
          } else if (b.state === 'exhausted') {
            cardBg = 'rgba(239,68,68,0.08)';
            cardBorder = '1px solid rgba(239,68,68,0.35)';
            bodyHtml = `<div style="font-size:0.8rem;color:#fca5a5;margin-bottom:8px;line-height:1.45;">
              <strong>Every ${_esc(b.artist_type)} artist on your list declined or didn't respond in time.</strong>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <button type="button" class="hold-bucket-add" data-type="${_esc(b.artist_type)}"
                style="padding:5px 12px;background:rgba(124,107,255,0.15);border:1px solid rgba(124,107,255,0.45);border-radius:4px;color:#c4b5fd;cursor:pointer;font-size:0.74rem;font-weight:600;">+ Add another ${_esc(b.artist_type)}</button>
              <button type="button" class="hold-bucket-cancel" data-type="${_esc(b.artist_type)}"
                style="padding:5px 12px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.45);border-radius:4px;color:#f87171;cursor:pointer;font-size:0.74rem;font-weight:600;">Cancel ${_esc(b.artist_type)} slot${slotCnt === 1 ? '' : 's'}</button>
            </div>`;
          }
          html += `<div style="background:${cardBg};border:${cardBorder};border-radius:6px;padding:9px 12px;">
            ${headerHtml}${bodyHtml}
          </div>`;
        });
        html += '</div>';
      }
      // Waitlist — original artists (added_post_creation=0) are LOCKED:
      // no drag, no × — they keep their position from gig creation.
      // Artists added later (added_post_creation=1) are draggable to
      // reorder amongst themselves + removable via ×. They always sit
      // BELOW the locked tier (higher position numbers) so the offer
      // cycle hits originals first.
      if (data.waitlist && data.waitlist.length > 0) {
        html += `<div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin:10px 0 6px 0;">Waitlist <span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--text-gray);margin-left:8px;font-size:0.68rem;">(original artists locked; added artists below them can be reordered)</span></div>`;
        html += '<div id="holdMgmtWaitlist" style="display:flex;flex-direction:column;gap:3px;margin-bottom:8px;">';
        data.waitlist.forEach((w, i) => {
          const state = w.state;
          const reason = w.state_reason || {};
          const isAdded = !!w.added_post_creation;
          let badge = '';
          let badgeColor = '';
          // Human-readable badge per state. The state_reason carries the
          // why (freq conflict / booked elsewhere in series / etc) so the
          // venue isn't left guessing why an artist couldn't accept.
          const _fmtDate = (d) => {
            try {
              const [y, m, day] = String(d || '').split('-').map(Number);
              return new Date(y, m-1, day).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            } catch (_) { return d || ''; }
          };
          const _fmtHrs = (h) => {
            if (h == null) return '';
            const totalMins = Math.round(h * 60);
            if (totalMins <= 0) return 'expiring now';
            if (totalMins < 60) return `${totalMins}m left`;
            if (h < 24) { const hh = Math.round(h); return `${hh}h left`; }
            const dd = Math.round(h / 24); return `${dd}d left`;
          };
          if (state === 'current_offer') {
            const tl = w.hours_remaining != null ? ` · ${_fmtHrs(w.hours_remaining)}` : '';
            badge = `● Offered${tl}`;
            badgeColor = '#fcd34d';
          } else if (state === 'accepted') {
            badge = '✓ Booked';
            badgeColor = '#86efac';
          } else if (state === 'booked_elsewhere') {
            // They accepted the series, just not THIS date. Not a real decline.
            badge = `↻ Booked ${_fmtDate(reason.other_date)} (series)`;
            badgeColor = '#86efac';
          } else if (state === 'expired') {
            badge = '⏱ Time Expired';
            badgeColor = '#f87171';
          } else if (state === 'declined') {
            badge = '✗ Declined';
            badgeColor = '#f87171';
          } else if (state === 'queued' && reason.kind === 'freq_blocked') {
            // Queued but freq conflict means they can't accept even when offered.
            badge = `⚠ Freq conflict · played ${reason.days_apart}d ${reason.direction === 'after' ? 'after' : 'before'}`;
            badgeColor = '#fbbf24';
          } else {
            badge = 'Queued';
            badgeColor = 'var(--text-gray)';
          }
          // Draggable + removable ONLY when this row was added post-creation
          // AND it hasn't yet been offered (state='queued'). Anything else
          // (offered / accepted / declined) is immutable.
          const reorderable = isAdded && state === 'queued';
          const dragHandle = reorderable
            ? '<span class="hold-mgmt-handle" style="color:var(--text-muted);cursor:grab;font-size:0.85rem;user-select:none;">⋮⋮</span>'
            : '<span style="display:inline-block;width:14px;"></span>';
          const removeBtn = reorderable
            ? `<button type="button" class="hold-mgmt-remove" data-aid="${parseInt(w.artist_id, 10)}"
                title="Remove this artist from the waitlist (they haven't been emailed yet)"
                style="margin-left:6px;background:transparent;border:none;color:var(--text-gray);cursor:pointer;font-size:0.9rem;line-height:1;padding:0 4px;">×</button>`
            : '';
          const dragAttrs = reorderable ? `draggable="true"` : '';
          const cursor = reorderable ? 'grab' : 'default';
          const bgTint = isAdded ? 'rgba(34,197,94,0.06)' : 'rgba(255,255,255,0.02)';
          html += `<div class="hold-mgmt-row${reorderable ? ' is-reorderable' : ''}"
              data-aid="${parseInt(w.artist_id, 10)}" ${dragAttrs}
              style="display:flex;align-items:center;gap:8px;padding:5px 8px;background:${bgTint};border-radius:4px;font-size:0.78rem;cursor:${cursor};">
            ${dragHandle}
            <span style="color:var(--text-muted);min-width:18px;">${i + 1}.</span>
            <span style="flex:1;color:var(--text);">${_esc(w.artist_name || 'Artist #'+w.artist_id)}${isAdded ? ' <span style="color:#86efac;font-size:0.66rem;font-weight:600;">(added)</span>' : ''}</span>
            <span style="color:${badgeColor};font-size:0.7rem;font-weight:600;">${badge}</span>
            ${removeBtn}
          </div>`;
        });
        html += '</div>';
        // Add-artist affordance
        html += `<div style="margin:6px 0 12px 0;">
          <button type="button" id="holdAddArtistBtn"
            style="padding:5px 12px;background:rgba(34,197,94,0.10);border:1px solid rgba(34,197,94,0.35);border-radius:5px;color:#86efac;cursor:pointer;font-size:0.74rem;font-weight:600;">+ Add Artist(s) to Waitlist</button>
          <div id="holdAddArtistPicker" style="display:none;margin-top:6px;border:1px solid var(--border);border-radius:5px;padding:6px;max-height:200px;overflow:auto;background:rgba(0,0,0,0.2);"></div>
        </div>`;
      }
      // Action buttons
      html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
        ${data.current_offer ? `
        <button type="button" id="holdSkipCurrentBtn"
          title="Mark the current offer as declined and immediately move on to the next artist. Use when the artist tells you offline they can't make it."
          style="padding:6px 12px;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.4);border-radius:5px;color:#fcd34d;cursor:pointer;font-size:0.78rem;font-weight:600;">
          Skip Current Artist
        </button>` : ''}
        <button type="button" id="holdReleaseBtn"
          title="End the hold immediately and open the gig to all artists. The current offer (if any) is cancelled."
          style="padding:6px 12px;background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.4);border-radius:5px;color:#93c5fd;cursor:pointer;font-size:0.78rem;font-weight:600;">
          Release Hold (Open to All)
        </button>
      </div>`;
    }

    panel.innerHTML = html;
    // Insert the panel as a SIBLING before .modal-section instead of
    // inside it. The booked-gig view (_showBookedGigModal) hides
    // modal-section entirely, which used to hide the hold-mgmt panel
    // too once any slot was booked — so a venue with a held gig
    // whose Slot 1 was booked-and-countersigned saw no queue state
    // at all in the gig modal (Jun 2026 user report). Falls back to
    // insertBefore(body.firstChild) for pages where the parent
    // doesn't exist (e.g. #modalBody-based pages).
    const parent = body.parentNode;
    if (parent && body.parentNode) {
      parent.insertBefore(panel, body);
    } else {
      body.insertBefore(panel, body.firstChild);
    }

    // Wire up the buttons
    _wireButtons(gigId, panel);
  }

  function _wireButtons(gigId, panel) {
    const skip = panel.querySelector('#holdSkipCurrentBtn');
    if (skip) skip.addEventListener('click', () => _action(gigId, 'skip', skip));
    const release = panel.querySelector('#holdReleaseBtn');
    if (release) release.addEventListener('click', async () => {
      // BUG FIX (Jul 2026 audit): window.showConfirm(title) returns the overlay
      // DOM node synchronously — NOT a Promise. Awaiting it resolved to a
      // truthy DOM element and _action fired immediately, releasing the hold
      // before the user could click Cancel. Wrap it the same way openAll and
      // cancelEmpty below do (see comment on line 309).
      const ok = window.showConfirm
        ? await new Promise(res => window.showConfirm(
            'Release Hold?',
            'This ends the current hold rotation and re-opens the gig to any preferred artist immediately. The artist currently holding an offer will lose their exclusive window.',
            () => res(true),
            () => res(false),
            { tone: 'warning', confirmStyle: 'danger',
              confirmLabel: 'Release Hold', cancelLabel: 'Keep Hold Active' }
          ))
        : confirm('Release this hold and open the gig to all artists?');
      if (!ok) return;
      _action(gigId, 'release', release);
    });
    // Both actions get explicit confirmation modals (Jun 2026) so the
    // venue clearly understands which behavior they're triggering.
    // "Open to all" leaves the open slots BOOKABLE for any artist
    // (no slot mutation); "Cancel empty slots" marks each open slot
    // status='cancelled' (the slot rows stay, just non-bookable).
    // Per a user report where Open-to-All was thought to delete a
    // slot — neither action deletes from the DB.
    const openAll = panel.querySelector('#holdResolveOpenBtn');
    if (openAll) openAll.addEventListener('click', async () => {
      const ok = window.showConfirm
        ? await new Promise(res => window.showConfirm(
            'Open empty slots to all artists?',
            'Your hold list is done. Clicking confirm will keep every empty slot OPEN for any artist to book — nothing is cancelled or deleted. Booked slots stay booked.',
            () => res(true),
            () => res(false),
            { confirmLabel: 'Open to All', cancelLabel: 'Not Yet', confirmStyle: 'success' }
          ))
        : confirm('Open empty slots to all artists? Empty slots stay open; nothing is deleted.');
      if (!ok) return;
      _action(gigId, 'open_all', openAll);
    });
    const cancelEmpty = panel.querySelector('#holdResolveCancelBtn');
    if (cancelEmpty) cancelEmpty.addEventListener('click', async () => {
      const ok = window.showConfirm
        ? await new Promise(res => window.showConfirm(
            'Cancel the empty slots?',
            'This will mark every empty (unbooked) slot as cancelled, so no artist can book them. Booked slots stay booked. The slot rows themselves stay on the gig — they just become non-bookable.',
            () => res(true),
            () => res(false),
            { tone: 'warning', confirmStyle: 'danger',
              confirmLabel: 'Cancel Empty Slots', cancelLabel: 'Keep Open' }
          ))
        : confirm('Cancel the empty slots? Booked slots stay.');
      if (!ok) return;
      _action(gigId, 'cancel_empty', cancelEmpty);
    });

    // Per-bucket actions (Jun 2026 — parallel-per-type rotation).
    // "+ Add <Type> artist" opens the existing add-artist picker; the
    // picker auto-filters by slot type so only matching artists show.
    panel.querySelectorAll('.hold-bucket-add').forEach(btn => {
      btn.addEventListener('click', () => {
        const picker = panel.querySelector('#holdAddArtistPicker');
        const trigger = panel.querySelector('#holdAddArtistBtn');
        if (picker && trigger) {
          if (picker.style.display !== 'block') trigger.click();
          // Scroll the picker into view so it's not hidden below the fold.
          setTimeout(() => picker.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 80);
        }
      });
    });
    // "Cancel <Type> slot(s)" cancels every open slot of that type via
    // /hold/cancel-slots-by-type. Confirmation modal first.
    panel.querySelectorAll('.hold-bucket-cancel').forEach(btn => {
      btn.addEventListener('click', async () => {
        const artistType = btn.dataset.type;
        const ok = window.showConfirm
          ? await new Promise(res => window.showConfirm(
              `Cancel the empty ${artistType} slot(s)?`,
              `This marks every open ${artistType} slot on this gig as cancelled. Booked slots of any type are untouched. The other artist-type rotations keep running.`,
              () => res(true),
              () => res(false),
              { tone: 'warning', confirmStyle: 'danger',
                confirmLabel: `Cancel ${artistType} Slot(s)`, cancelLabel: 'Keep Open' }
            ))
          : confirm(`Cancel the empty ${artistType} slot(s)?`);
        if (!ok) return;
        btn.disabled = true;
        const orig = btn.textContent;
        btn.textContent = '…';
        try {
          const res = await fetch(`/api/gigs/${gigId}/hold/cancel-slots-by-type`, {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_type: artistType }),
          });
          if (!res.ok) throw new Error('Request failed');
          const data = await res.json();
          if (window.showSuccessModal && data.message) {
            window.showSuccessModal('Slots cancelled', data.message);
          }
          await renderPanel(gigId);
          if (typeof window.invalidateGigs === 'function') window.invalidateGigs();
          if (typeof window.renderCalendar === 'function') window.renderCalendar();
        } catch (e) {
          btn.disabled = false;
          btn.textContent = orig;
          if (window.showErrorModal) window.showErrorModal('Could not cancel slots', e.message || 'Please try again.');
        }
      });
    });

    _wireWaitlistEditing(gigId, panel);
    _wireAddedDragReorder(gigId, panel);
  }

  // Drag-to-reorder among the added (post-creation) rows. Originals
  // are not draggable (no .is-reorderable class) so dropping onto
  // them is rejected.
  function _wireAddedDragReorder(gigId, panel) {
    const list = panel.querySelector('#holdMgmtWaitlist');
    if (!list) return;
    let dragEl = null;
    list.querySelectorAll('.hold-mgmt-row.is-reorderable').forEach(row => {
      row.addEventListener('dragstart', (e) => {
        dragEl = row;
        row.style.opacity = '0.4';
        try { e.dataTransfer.setData('text/plain', row.dataset.aid); } catch(_) {}
        e.dataTransfer.effectAllowed = 'move';
      });
      row.addEventListener('dragend', () => {
        row.style.opacity = '';
        list.querySelectorAll('.hold-mgmt-row').forEach(r => { r.style.borderTop = ''; });
        dragEl = null;
      });
      row.addEventListener('dragover', (e) => {
        if (!dragEl || dragEl === row || !row.classList.contains('is-reorderable')) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        list.querySelectorAll('.hold-mgmt-row').forEach(r => { r.style.borderTop = ''; });
        row.style.borderTop = '2px solid #22c55e';
      });
      row.addEventListener('drop', async (e) => {
        e.preventDefault();
        if (!dragEl || dragEl === row || !row.classList.contains('is-reorderable')) return;
        const allAdded = Array.from(list.querySelectorAll('.hold-mgmt-row.is-reorderable'));
        const fromIdx = allAdded.indexOf(dragEl);
        let toIdx = allAdded.indexOf(row);
        if (fromIdx < 0 || toIdx < 0) return;
        allAdded.splice(fromIdx, 1);
        if (fromIdx < toIdx) toIdx -= 1;
        allAdded.splice(toIdx, 0, dragEl);
        const newOrder = allAdded.map(r => parseInt(r.dataset.aid, 10));
        try {
          await fetch(`/api/gigs/${gigId}/hold/reorder`, {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queued_ids: newOrder })
          });
          await renderPanel(gigId);
        } catch (_) {}
      });
    });
  }

  // Add/remove venue-added artists. Original locked rows never touched.
  function _wireWaitlistEditing(gigId, panel) {
    // Remove handlers on the × buttons (only on added rows)
    panel.querySelectorAll('.hold-mgmt-remove').forEach(btn => {
      btn.addEventListener('click', async () => {
        const aid = parseInt(btn.dataset.aid, 10);
        const ok = window.showConfirm
          ? await new Promise(res => window.showConfirm(
              'Remove from waitlist?',
              'This artist hasn\'t been emailed yet — they won\'t know they were ever on the list.',
              () => res(true), () => res(false),
              { confirmLabel: 'Remove', confirmStyle: 'danger' }
            ))
          : confirm('Remove this artist from the waitlist?');
        if (!ok) return;
        try {
          // Get current added artists (in order), drop the targeted one, POST.
          // Audit Y4: check res.ok before .json() so the click doesn't
          // silently no-op when the GET 4xx/5xx's. Surface the FastAPI
          // {detail} message to the user via the styled error modal.
          const statusRes = await fetch(`/api/gigs/${gigId}/hold-status`, { credentials: 'include' });
          if (!statusRes.ok) {
            let _detail = `Could not load the hold list (HTTP ${statusRes.status}).`;
            try { const _j = await statusRes.json(); if (_j && _j.detail) _detail = _j.detail; } catch (_) {}
            throw new Error(_detail);
          }
          const data = await statusRes.json();
          const remaining = (data.waitlist || [])
            .filter(w => w.added_post_creation && w.artist_id !== aid)
            .map(w => w.artist_id);
          const reorderRes = await fetch(`/api/gigs/${gigId}/hold/reorder`, {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queued_ids: remaining })
          });
          if (!reorderRes.ok) {
            let _detail = `Could not update the hold list (HTTP ${reorderRes.status}).`;
            try { const _j = await reorderRes.json(); if (_j && _j.detail) _detail = _j.detail; } catch (_) {}
            throw new Error(_detail);
          }
          await renderPanel(gigId);
        } catch (e) {
          const msg = (e && e.message) || 'Could not remove the artist from the waitlist.';
          if (typeof window.showErrorModal === 'function') {
            window.showErrorModal('Remove Failed', msg);
          }
        }
      });
    });

    // + Add artist: reveal a picker of approved preferred artists who
    // aren't already on the waitlist. Click one → append to queued tail.
    const addBtn = panel.querySelector('#holdAddArtistBtn');
    const addPicker = panel.querySelector('#holdAddArtistPicker');
    if (addBtn && addPicker) {
      addBtn.addEventListener('click', async () => {
        if (addPicker.style.display === 'block') {
          addPicker.style.display = 'none';
          return;
        }
        addPicker.style.display = 'block';
        addPicker.innerHTML = '<div style="padding:8px;color:var(--text-gray);font-size:0.74rem;">Loading…</div>';
        try {
          const venueId = await _resolveVenueId(gigId);
          // Fetch hold-status first so we know the gig date — needed to
          // ask the preferred-artists endpoint for per-artist freq status
          // (Jun 2026 — Hold-Gig add-artist UX).
          const gigRes = await fetch(`/api/gigs/${gigId}/hold-status`, { credentials: 'include' });
          const gigData = gigRes.ok ? await gigRes.json() : {};
          const _qs = gigData.date ? `?for_gig_date=${encodeURIComponent(gigData.date)}` : '';
          const artistsRes = await fetch(`/api/venues/${venueId}/preferred-artists${_qs}`, { credentials: 'include' });
          if (!artistsRes.ok) throw new Error();
          const data = await artistsRes.json();
          const approved = (Array.isArray(data) ? data : (data.preferred_artists || []))
            .filter(a => (a.status || 'approved') === 'approved');
          // Already-on-waitlist ids
          const onList = new Set(
            Array.from(panel.querySelectorAll('.hold-mgmt-row[data-aid]'))
              .map(r => parseInt(r.dataset.aid, 10))
          );
          // Type-match candidates against any open slot (mirrors the
          // create-flow picker — adding an artist who doesn't match
          // any slot would just auto-decline).
          const openSlots = gigData.open_slots || [];
          const _csvSet = s => s ? new Set(String(s).split(/[,;]/).map(x => x.trim().toLowerCase()).filter(Boolean)) : new Set();
          function _matches(artist, slot) {
            if (!slot.artist_type || !artist.artist_type) return false;
            if (artist.artist_type !== slot.artist_type) return false;
            const sf = _csvSet(slot.band_formats);
            if (sf.size > 0 && ![..._csvSet(artist.band_formats)].some(x => sf.has(x))) return false;
            const ss = _csvSet(slot.styles);
            if (ss.size > 0 && ![..._csvSet(artist.styles)].some(x => ss.has(x))) return false;
            return true;
          }
          const candidates = approved.filter(a => {
            if (onList.has(parseInt(a.artist_id || a.id, 10))) return false;
            if (!openSlots.length) return true;  // no open slots = no filter
            return openSlots.some(s => _matches(a, s));
          });
          if (!candidates.length) {
            addPicker.innerHTML = '<div style="padding:8px;color:var(--text-gray);font-size:0.74rem;font-style:italic;">No more preferred artists match the open slots\' type.</div>';
            return;
          }
          // Render the picker with the same vocabulary as the create
          // modal: type icon, name, (Override Pay), click toggles.
          // Per user note: only artists matching a slot are listed +
          // pay override shown.
          const ICON = { 'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'❓', 'Open Mic MC':'🎙️', 'Karaoke MC':'🎶' };
          function _payFor(a) {
            const dollars = Number(a.pay_dollars_override) || 0;
            const cents = Number(a.pay_cents_override) || 0;
            if (dollars > 0 || cents > 0) {
              const v = dollars + cents / 100;
              return v % 1 === 0 ? `$${Math.round(v)}` : `$${v.toFixed(2)}`;
            }
            return '$--';
          }
          // Count under-freq candidates so we can surface the summary
          // note explaining what happens when they're added (Jun 2026).
          //
          // 2026-08-07: branch on series membership. Single-gig holds
          // auto-stamp `frequency_exempt=1` so the copy accurately
          // reads "waive for this gig only". Series-mode holds
          // (recurring_group_id present) intentionally do NOT waive —
          // the artist's bundled-offer modal greys out dates that
          // conflict with the venue's freq rule, so the rule stays in
          // force across the series. Show a matching series message
          // instead so the venue isn't misled.
          const _underFreqCount = candidates.filter(a => a.freq_status && a.freq_status.under_limit).length;
          const _isSeriesMode = !!(gigData && gigData.recurring_group_id);
          let _summaryNote = '';
          if (_underFreqCount > 0) {
            const _who = `<strong>${_underFreqCount} artist${_underFreqCount!==1?'s':''}</strong> ${_underFreqCount===1?'is':'are'}`;
            const _body = _isSeriesMode
              ? `${_who} currently under this venue's frequency policy. They can still be invited — their bundled-offer modal will grey out dates that conflict with the rule.`
              : `${_who} under this venue's frequency policy. Adding them will waive the rule for this gig only.`;
            _summaryNote = `<div style="font-size:0.64rem;color:#fbbf24;margin:2px 4px 6px;background:rgba(251,191,36,0.10);border:1px solid rgba(251,191,36,0.25);border-radius:4px;padding:5px 7px;line-height:1.35;">${_body}</div>`;
          }
          function _freqChip(a) {
            const fs = a.freq_status;
            if (!fs || !fs.under_limit) return '';
            return `<span title="Last gig here ${fs.abs_days_between} day${fs.abs_days_between!==1?'s':''} ${fs.days_between>0?'before':'after'} this gig (venue requires ${fs.freq_days})" style="flex:0 0 auto;font-size:0.62rem;font-weight:600;color:#fbbf24;background:rgba(251,191,36,0.12);border:1px solid rgba(251,191,36,0.30);border-radius:3px;padding:1px 5px;">freq ${fs.abs_days_between}d</span>`;
          }
          addPicker.innerHTML = '<div style="font-size:0.66rem;color:var(--text-gray);margin:2px 4px 6px;font-style:italic;">Click an artist to add them to the waitlist (matched to your open slots).</div>' +
            _summaryNote +
            candidates.map(a => {
              const aid = parseInt(a.artist_id || a.id, 10);
              const nm = _esc(a.name || ('Artist ' + aid));
              const icon = ICON[a.artist_type] || '🎵';
              const pay = _payFor(a);
              const chip = _freqChip(a);
              return `<button type="button" class="hold-mgmt-add" data-aid="${aid}"
                title="${_esc(a.artist_type || 'Artist')}"
                style="display:flex;align-items:center;gap:8px;width:100%;text-align:left;padding:5px 8px;margin-bottom:3px;background:transparent;border:1px solid rgba(255,255,255,0.06);color:var(--text);cursor:pointer;font-size:0.78rem;border-radius:4px;">
                <span style="flex:0 0 auto;">${icon}</span>
                <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${nm}</span>
                ${chip}
                <span style="flex:0 0 auto;opacity:0.7;font-weight:500;color:var(--text-gray);">(${pay})</span>
              </button>`;
            }).join('');
          addPicker.querySelectorAll('.hold-mgmt-add').forEach(b => {
            b.addEventListener('mouseenter', () => { b.style.background = 'rgba(34,197,94,0.10)'; b.style.borderColor = 'rgba(34,197,94,0.35)'; });
            b.addEventListener('mouseleave', () => { b.style.background = 'transparent'; b.style.borderColor = 'rgba(255,255,255,0.06)'; });
            b.addEventListener('click', async () => {
              const newAid = parseInt(b.dataset.aid, 10);
              try {
                const status = await (await fetch(`/api/gigs/${gigId}/hold-status`, { credentials: 'include' })).json();
                // Send the full added-list including the new artist
                // appended to the end. Backend diffs + inserts.
                const addedOrder = (status.waitlist || [])
                  .filter(w => w.added_post_creation)
                  .map(w => w.artist_id);
                addedOrder.push(newAid);
                await fetch(`/api/gigs/${gigId}/hold/reorder`, {
                  method: 'POST', credentials: 'include',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ queued_ids: addedOrder })
                });
                await renderPanel(gigId);
              } catch (_) {}
            });
          });
        } catch (e) {
          addPicker.innerHTML = '<div style="padding:8px;color:#ef4444;font-size:0.74rem;">Could not load preferred artists.</div>';
        }
      });
    }
  }

  // Look up the gig's venue_id so we can fetch its preferred artists.
  // Cached on the panel — the hold-status response carries venue_id.
  async function _resolveVenueId(gigId) {
    try {
      const r = await fetch(`/api/gigs/${gigId}/hold-status`, { credentials: 'include' });
      const d = await r.json();
      return d.venue_id;
    } catch (_) { return null; }
  }

  async function _action(gigId, kind, btn) {
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '…';
    let url, body = null;
    if (kind === 'release') {
      url = `/api/gigs/${gigId}/hold/release`;
    } else if (kind === 'skip') {
      url = `/api/gigs/${gigId}/hold/skip-current`;
    } else if (kind === 'open_all' || kind === 'cancel_empty') {
      url = `/api/gigs/${gigId}/hold/resolve-exhausted`;
      body = JSON.stringify({ action: kind });
    } else {
      return;
    }
    try {
      const res = await fetch(url, {
        method: 'POST', credentials: 'include',
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body,
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        // Re-render the panel with new state (or remove it if hold cleared)
        await renderPanel(gigId);
        // Also refresh the calendar AND the Pending Offers banner so
        // the gig's visual state updates immediately on both surfaces
        // (without waiting for the 60s banner poll).
        try { if (typeof window.invalidateGigs === 'function') window.invalidateGigs(); } catch (_) {}
        if (typeof window.refreshGigs === 'function') {
          try { await window.refreshGigs(); } catch (_) {}
        } else if (typeof window.loadGigs === 'function') {
          try { window.loadGigs(); } catch (_) {}
        }
        if (typeof window.refreshVenueHoldOffersBanner === 'function') {
          try { window.refreshVenueHoldOffersBanner(); } catch (_) {}
        }
        // Surface a success message describing what actually happened
        // — confirms to the venue that nothing was deleted unexpectedly.
        if (typeof window.showSuccessModal === 'function') {
          const msg = (kind === 'open_all')
            ? 'Empty slots are now open for any artist to book. The slot rows are still there — they just left the hold cycle.'
            : (kind === 'cancel_empty')
                ? `Cancelled ${data.cancelled_count || 'the'} empty slot${(data.cancelled_count === 1) ? '' : 's'}. The rows stay on the gig in a cancelled state; booked slots are unaffected.`
                : (data.message || 'Done.');
          try { window.showSuccessModal('Done', msg); } catch (_) {}
        }
      } else {
        btn.textContent = '✗ ' + (data.message || data.error || 'Failed');
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
      }
    } catch (e) {
      btn.textContent = '✗ Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
    }
  }

  window._renderHoldMgmtPanel = renderPanel;
})();
