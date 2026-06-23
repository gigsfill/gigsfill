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
          const isAdded = !!w.added_post_creation;
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
            style="padding:5px 12px;background:rgba(34,197,94,0.10);border:1px solid rgba(34,197,94,0.35);border-radius:5px;color:#86efac;cursor:pointer;font-size:0.74rem;font-weight:600;">+ Add artist to waitlist</button>
          <div id="holdAddArtistPicker" style="display:none;margin-top:6px;border:1px solid var(--border);border-radius:5px;padding:6px;max-height:140px;overflow:auto;background:rgba(0,0,0,0.2);"></div>
        </div>`;
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
          const data = await (await fetch(`/api/gigs/${gigId}/hold-status`, { credentials: 'include' })).json();
          const remaining = (data.waitlist || [])
            .filter(w => w.added_post_creation && w.artist_id !== aid)
            .map(w => w.artist_id);
          await fetch(`/api/gigs/${gigId}/hold/reorder`, {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queued_ids: remaining })
          });
          await renderPanel(gigId);
        } catch (_) {}
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
          const res = await fetch(`/api/venues/${venueId}/preferred-artists`, { credentials: 'include' });
          if (!res.ok) throw new Error();
          const data = await res.json();
          const approved = (Array.isArray(data) ? data : (data.preferred_artists || []))
            .filter(a => (a.status || 'approved') === 'approved');
          // Already-on-waitlist ids
          const onList = new Set(
            Array.from(panel.querySelectorAll('.hold-mgmt-row[data-aid]'))
              .map(r => parseInt(r.dataset.aid, 10))
          );
          const candidates = approved.filter(a => !onList.has(parseInt(a.artist_id || a.id, 10)));
          if (!candidates.length) {
            addPicker.innerHTML = '<div style="padding:8px;color:var(--text-gray);font-size:0.74rem;font-style:italic;">All your preferred artists are already on the list.</div>';
            return;
          }
          addPicker.innerHTML = candidates.map(a => {
            const aid = parseInt(a.artist_id || a.id, 10);
            const nm = _esc(a.name || ('Artist ' + aid));
            return `<button type="button" class="hold-mgmt-add" data-aid="${aid}"
              style="display:block;width:100%;text-align:left;padding:5px 8px;background:transparent;border:none;color:var(--text);cursor:pointer;font-size:0.78rem;border-radius:3px;">
              + ${nm}
            </button>`;
          }).join('');
          addPicker.querySelectorAll('.hold-mgmt-add').forEach(b => {
            b.addEventListener('mouseenter', () => b.style.background = 'rgba(34,197,94,0.10)');
            b.addEventListener('mouseleave', () => b.style.background = 'transparent');
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
