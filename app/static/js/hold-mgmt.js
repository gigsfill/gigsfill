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

    const body = document.getElementById('modalBody');
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
      // ── Active state: show current offer + waitlist ──
      if (data.current_offer) {
        const co = data.current_offer;
        const hours = co.hours_remaining != null ? `${co.hours_remaining}h` : '—';
        html += `<div style="background:rgba(0,0,0,0.25);border-radius:6px;padding:10px 14px;margin-bottom:12px;">
          <div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin-bottom:4px;">Currently offered to</div>
          <div style="font-size:0.95rem;color:var(--text);font-weight:600;">${_esc(co.artist_name || 'Artist #'+co.artist_id)}</div>
          <div style="font-size:0.74rem;color:var(--text-gray);margin-top:2px;">
            ${hours} remaining ${co.reminder_sent ? '· reminder sent' : ''}
          </div>
        </div>`;
      }
      // Slot summary
      if (data.booked_slot_count > 0 || data.open_slot_count > 0) {
        html += `<div style="font-size:0.78rem;color:var(--text-gray);margin-bottom:10px;">
          ${data.booked_slot_count} booked · ${data.open_slot_count} still open
        </div>`;
      }
      // Waitlist — queued rows are draggable to reorder; current_offer
      // (in flight) + accepted + declined are locked at their positions.
      if (data.waitlist && data.waitlist.length > 1) {
        const queuedCount = data.waitlist.filter(w => w.state === 'queued').length;
        const dragHint = queuedCount > 1
          ? '<span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--text-gray);margin-left:8px;font-size:0.68rem;">(drag queued rows to reorder)</span>'
          : '';
        html += `<div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin:10px 0 6px 0;">Waitlist${dragHint}</div>`;
        html += '<div id="holdMgmtWaitlist" style="display:flex;flex-direction:column;gap:3px;margin-bottom:12px;">';
        data.waitlist.forEach((w, i) => {
          const state = w.state;
          let badge = '';
          let badgeColor = '';
          if (state === 'current_offer') {
            badge = '● Offered';
            badgeColor = '#fcd34d';
          } else if (state === 'accepted') {
            badge = '✓ Booked';
            badgeColor = '#86efac';
          } else if (state === 'declined') {
            badge = '✗ Declined / expired';
            badgeColor = '#f87171';
          } else {
            badge = 'Queued';
            badgeColor = 'var(--text-gray)';
          }
          // Only the queued rows are draggable.
          const isDraggable = state === 'queued';
          const dragAttrs = isDraggable
            ? `draggable="true" data-aid="${parseInt(w.artist_id, 10)}"`
            : '';
          const cursor = isDraggable ? 'grab' : 'default';
          const handle = isDraggable
            ? '<span class="hold-mgmt-handle" style="color:var(--text-muted);cursor:grab;font-size:0.85rem;user-select:none;">⋮⋮</span>'
            : '<span style="display:inline-block;width:14px;"></span>';
          html += `<div class="hold-mgmt-row ${isDraggable ? 'is-draggable' : ''}"
              ${dragAttrs}
              style="display:flex;align-items:center;gap:8px;padding:5px 8px;background:rgba(255,255,255,0.02);border-radius:4px;font-size:0.78rem;cursor:${cursor};">
            ${handle}
            <span style="color:var(--text-muted);min-width:18px;">${i + 1}.</span>
            <span style="flex:1;color:var(--text);">${_esc(w.artist_name || 'Artist #'+w.artist_id)}</span>
            <span style="color:${badgeColor};font-size:0.7rem;font-weight:600;">${badge}</span>
          </div>`;
        });
        html += '</div>';
      }
      // Action buttons
      html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
        ${data.current_offer ? `
        <button type="button" id="holdSkipCurrentBtn"
          title="Mark the current offer as declined and immediately move on to the next artist. Use when the artist tells you offline they can't make it."
          style="padding:6px 12px;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.4);border-radius:5px;color:#fcd34d;cursor:pointer;font-size:0.78rem;font-weight:600;">
          Skip current artist
        </button>` : ''}
        <button type="button" id="holdReleaseBtn"
          title="End the hold immediately and open the gig to all artists. The current offer (if any) is cancelled."
          style="padding:6px 12px;background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.4);border-radius:5px;color:#93c5fd;cursor:pointer;font-size:0.78rem;font-weight:600;">
          Release hold (open to all)
        </button>
      </div>`;
    }

    panel.innerHTML = html;
    body.insertBefore(panel, body.firstChild);

    // Wire up the buttons
    _wireButtons(gigId, panel);
  }

  function _wireButtons(gigId, panel) {
    const skip = panel.querySelector('#holdSkipCurrentBtn');
    if (skip) skip.addEventListener('click', () => _action(gigId, 'skip', skip));
    const release = panel.querySelector('#holdReleaseBtn');
    if (release) release.addEventListener('click', async () => {
      const ok = window.showConfirm
        ? await window.showConfirm('Release this hold and open the gig to all artists?')
        : confirm('Release this hold and open the gig to all artists?');
      if (!ok) return;
      _action(gigId, 'release', release);
    });
    const openAll = panel.querySelector('#holdResolveOpenBtn');
    if (openAll) openAll.addEventListener('click', () => _action(gigId, 'open_all', openAll));
    const cancelEmpty = panel.querySelector('#holdResolveCancelBtn');
    if (cancelEmpty) cancelEmpty.addEventListener('click', async () => {
      const ok = window.showConfirm
        ? await window.showConfirm('Cancel the empty slots? (Booked slots stay.)')
        : confirm('Cancel the empty slots? (Booked slots stay.)');
      if (!ok) return;
      _action(gigId, 'cancel_empty', cancelEmpty);
    });
    _wireDragReorder(gigId, panel);
  }

  // Drag-and-drop reorder on the queued portion of the waitlist.
  // POSTs the new order to /hold/reorder; current_offer and declined
  // rows are immutable (backend rejects attempts to touch them).
  function _wireDragReorder(gigId, panel) {
    const list = panel.querySelector('#holdMgmtWaitlist');
    if (!list) return;
    let dragEl = null;
    list.querySelectorAll('.hold-mgmt-row.is-draggable').forEach(row => {
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
        if (!dragEl || dragEl === row) return;
        // Only allow drop onto OTHER queued rows
        if (!row.classList.contains('is-draggable')) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        list.querySelectorAll('.hold-mgmt-row').forEach(r => { r.style.borderTop = ''; });
        row.style.borderTop = '2px solid #f59e0b';
      });
      row.addEventListener('drop', async (e) => {
        e.preventDefault();
        if (!dragEl || dragEl === row || !row.classList.contains('is-draggable')) return;
        // Compute the new order of queued rows
        const allQueued = Array.from(list.querySelectorAll('.hold-mgmt-row.is-draggable'));
        const fromIdx = allQueued.indexOf(dragEl);
        let toIdx = allQueued.indexOf(row);
        if (fromIdx < 0 || toIdx < 0) return;
        // Move element in array
        allQueued.splice(fromIdx, 1);
        if (fromIdx < toIdx) toIdx -= 1;
        allQueued.splice(toIdx, 0, dragEl);
        const newOrder = allQueued.map(r => parseInt(r.dataset.aid, 10));
        // POST to backend
        try {
          const res = await fetch(`/api/gigs/${gigId}/hold/reorder`, {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queued_ids: newOrder })
          });
          if (res.ok) {
            await renderPanel(gigId);
          }
        } catch (_) {}
      });
    });
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
        // Also refresh the calendar so the gig's visual state updates
        if (typeof window.loadGigs === 'function') {
          try { window.loadGigs(); } catch (_) {}
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
