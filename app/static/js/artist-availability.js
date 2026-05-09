/**
 * GigsFill — Artist Availability / Blackout Dates UI
 * ====================================================
 * Renders the blackout date management panel for the artist edit page.
 * Also provides venue-side helpers to show greyed-out unavailable dates.
 *
 * Usage on artist-edit.html:
 *   await renderAvailabilityPanel('availabilityContainer', artistId);
 */

// ── AVAILABILITY PANEL (artist edit page) ─────────────────────────────────

async function renderAvailabilityPanel(containerId, artistId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;">Loading availability...</div>`;

  try {
    const res = await fetch(`/api/artists/${artistId}/availability`, { credentials: 'include' });
    const data = await res.json();
    const allBlackouts = data.blackouts || [];

    // Auto-silently delete expired blocks (end date in the past)
    const today = new Date().toISOString().slice(0, 10);
    const expired = allBlackouts.filter(b => b.blackout_end < today);
    for (const b of expired) {
      try { await fetch(`/api/artists/${artistId}/availability/${b.id}`, { method: 'DELETE', credentials: 'include' }); } catch(e) {}
    }
    const activeBlackouts = expired.length > 0 ? allBlackouts.filter(b => b.blackout_end >= today) : allBlackouts;
    _renderAvailabilityUI(container, artistId, activeBlackouts);
  } catch (e) {
    container.innerHTML = `<div style="color:#ef4444;font-size:0.8rem;">Could not load availability settings</div>`;
  }
}

function _renderAvailabilityUI(container, artistId, blackouts) {
  const today = new Date().toISOString().slice(0, 10);

  container.innerHTML = `
    <div style="margin-bottom:16px;">
      <div style="font-size:0.75rem;font-weight:700;color:var(--cyan);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">
        Block Dates
      </div>
      <p style="font-size:0.75rem;color:var(--text-gray);margin:0 0 12px;">
        Add date ranges when you're unavailable to perform. Dates with your existing bookings cannot be blocked.
      </p>

      <!-- Add form -->
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;margin-bottom:16px;">
        <div class="field" style="flex:1;min-width:130px;">
          <label style="font-size:0.7rem;">From</label>
          <input type="date" id="blackoutStart_${artistId}" min="${today}"
            style="padding:7px 10px;font-size:0.8rem;width:100%;box-sizing:border-box;">
        </div>
        <div class="field" style="flex:1;min-width:130px;">
          <label style="font-size:0.7rem;">To</label>
          <input type="date" id="blackoutEnd_${artistId}" min="${today}"
            style="padding:7px 10px;font-size:0.8rem;width:100%;box-sizing:border-box;">
        </div>
        <div class="field" style="flex:2;min-width:160px;">
          <label style="font-size:0.7rem;">Reason (optional)</label>
          <input type="text" id="blackoutReason_${artistId}" placeholder="e.g. Tour, Vacation"
            maxlength="200" style="padding:7px 10px;font-size:0.8rem;width:100%;box-sizing:border-box;">
        </div>
        <button onclick="addBlackout(${artistId})"
          style="background:var(--cyan);color:#fff;border:none;border-radius:6px;
                 padding:8px 16px;font-size:0.8rem;font-weight:600;cursor:pointer;white-space:nowrap;">
          + Block Dates
        </button>
      </div>
      <div id="blackoutMsg_${artistId}" style="font-size:0.75rem;margin-bottom:8px;"></div>

      <!-- List -->
      <div id="blackoutList_${artistId}">
        ${_renderBlackoutList(blackouts, artistId)}
      </div>
    </div>
  `;
}

function _renderBlackoutList(blackouts, artistId) {
  if (!blackouts || blackouts.length === 0) {
    return `<div style="color:var(--text-gray);font-size:0.8rem;text-align:center;padding:16px 0;">No blocked dates — you're available for all gigs.</div>`;
  }

  return blackouts.map(b => `
    <div id="blackoutRow_${b.id}" style="background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-bottom:8px;">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div style="font-size:0.82rem;font-weight:600;color:var(--text);">
            🚫 ${esc(b.blackout_start)} → ${esc(b.blackout_end)}
          </div>
          ${b.reason ? `<div style="font-size:0.72rem;color:var(--text-gray);margin-top:2px;">${esc(b.reason)}</div>` : ''}
        </div>
        <div style="display:flex;gap:6px;">
          <button onclick="editBlackout(${artistId}, ${b.id}, '${b.blackout_start}', '${b.blackout_end}', '${esc(b.reason||'').replace(/'/g,"\\'")}')"
            style="background:rgba(99,91,255,0.1);border:1px solid rgba(99,91,255,0.3);color:#635bff;
                   border-radius:6px;padding:4px 10px;font-size:0.75rem;cursor:pointer;">
            Edit
          </button>
          <button onclick="deleteBlackout(${artistId}, ${b.id})"
            style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:#ef4444;
                   border-radius:6px;padding:4px 10px;font-size:0.75rem;cursor:pointer;">
            Remove
          </button>
        </div>
      </div>
      <div id="blackoutEdit_${b.id}" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--border);">
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;">
          <div>
            <label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:3px;">Start</label>
            <input type="date" id="editStart_${b.id}"
              style="background:var(--input-bg,#1a1f2e);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:5px 8px;font-size:0.8rem;">
          </div>
          <div>
            <label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:3px;">End</label>
            <input type="date" id="editEnd_${b.id}"
              style="background:var(--input-bg,#1a1f2e);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:5px 8px;font-size:0.8rem;">
          </div>
          <div style="flex:1;min-width:140px;">
            <label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:3px;">Reason (optional)</label>
            <input type="text" id="editReason_${b.id}" maxlength="200"
              style="width:100%;box-sizing:border-box;background:var(--input-bg,#1a1f2e);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:5px 8px;font-size:0.8rem;">
          </div>
          <button onclick="saveBlackoutEdit(${artistId}, ${b.id})"
            style="background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.3);color:#10b981;border-radius:6px;padding:5px 12px;font-size:0.75rem;cursor:pointer;">
            Save
          </button>
          <button onclick="document.getElementById('blackoutEdit_${b.id}').style.display='none'"
            style="background:none;border:1px solid var(--border);color:var(--text-gray);border-radius:6px;padding:5px 10px;font-size:0.75rem;cursor:pointer;">
            Cancel
          </button>
        </div>
        <div id="editMsg_${b.id}" style="font-size:0.72rem;margin-top:5px;"></div>
      </div>
    </div>
  `).join('');
}


// ── ADD BLACKOUT ──────────────────────────────────────────────────────────

window.addBlackout = async function(artistId, force = false) {
  const start = document.getElementById(`blackoutStart_${artistId}`)?.value;
  const end = document.getElementById(`blackoutEnd_${artistId}`)?.value;
  const reason = document.getElementById(`blackoutReason_${artistId}`)?.value?.trim() || '';
  const msgEl = document.getElementById(`blackoutMsg_${artistId}`);

  if (!start || !end) {
    if (msgEl) { msgEl.textContent = 'Please select both start and end dates'; msgEl.style.color = '#ef4444'; }
    return;
  }
  if (end < start) {
    if (msgEl) { msgEl.textContent = 'End date must be after start date'; msgEl.style.color = '#ef4444'; }
    return;
  }

  try {
    const body = { blackout_start: start, blackout_end: end, reason };
    if (force) body.force = true;
    const res = await fetch(`/api/artists/${artistId}/availability`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(body)
    });
    const data = await res.json();

    // Special case: 409 with waitlist conflict — backend returns a structured
    // payload describing waitlisted gigs in the requested range. Show the user
    // a confirmation modal asking whether to keep waitlist (cancel blackout)
    // or remove from waitlist (proceed with blackout).
    if (res.status === 409 && data?.detail?.error === 'waitlist_conflict') {
      _showWaitlistConflictModal(artistId, data.detail.conflicts || []);
      return;
    }

    if (!res.ok) throw new Error((typeof data.detail === 'string' ? data.detail : null) || 'Failed to save');

    let successMsg = '✓ Dates blocked';
    if (data.removed_from_waitlists && data.removed_from_waitlists.length > 0) {
      successMsg = `✓ Dates blocked. Removed from ${data.removed_from_waitlists.length} waitlist(s).`;
    }
    if (msgEl) { msgEl.textContent = successMsg; msgEl.style.color = '#22c55e'; }
    setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 3000);

    // Clear inputs
    const s = document.getElementById(`blackoutStart_${artistId}`);
    const e = document.getElementById(`blackoutEnd_${artistId}`);
    const r = document.getElementById(`blackoutReason_${artistId}`);
    if (s) s.value = ''; if (e) e.value = ''; if (r) r.value = '';

    // Refresh list
    await _refreshBlackoutList(artistId);
  } catch (err) {
    if (msgEl) { msgEl.textContent = err.message || 'Failed to block dates'; msgEl.style.color = '#ef4444'; }
  }
};


// Modal shown when artist tries to blackout dates that overlap waitlisted gigs.
// Two options:
//   1. Keep waitlist (cancel blackout)
//   2. Remove from waitlist + add blackout (re-submit with force=true)
function _showWaitlistConflictModal(artistId, conflicts) {
  // Remove any existing modal first
  const existing = document.getElementById('waitlistConflictModal');
  if (existing) existing.remove();

  const conflictHtml = conflicts.map(c => {
    const dateStr = new Date(c.date + 'T12:00:00').toLocaleDateString(undefined, {
      weekday: 'long', year: 'numeric', month: 'short', day: 'numeric'
    });
    const venuePart = c.venue_name ? ` at <strong>${_escapeHtml(c.venue_name)}</strong>` : '';
    const titlePart = c.title ? ` — ${_escapeHtml(c.title)}` : '';
    return `<li style="margin:6px 0;">${dateStr}${venuePart}${titlePart}</li>`;
  }).join('');

  const overlay = document.createElement('div');
  overlay.id = 'waitlistConflictModal';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;padding:20px;';
  overlay.innerHTML = `
    <div role="dialog" aria-modal="true" style="background:#fff;max-width:480px;width:100%;border-radius:8px;padding:24px;box-shadow:0 10px 40px rgba(0,0,0,0.3);">
      <h3 style="margin:0 0 12px;font-size:1.1rem;color:#1a1a2e;">Waitlist conflict</h3>
      <p style="margin:0 0 12px;font-size:0.9rem;color:#4b5563;line-height:1.5;">
        You are on the waitlist for the following gig(s) in this date range:
      </p>
      <ul style="margin:0 0 16px 20px;padding:0;font-size:0.9rem;color:#1a1a2e;">
        ${conflictHtml}
      </ul>
      <p style="margin:0 0 20px;font-size:0.9rem;color:#4b5563;line-height:1.5;">
        Choose how to handle this:
      </p>
      <div style="display:flex;flex-direction:column;gap:8px;">
        <button id="wlKeepBtn" style="padding:10px 16px;border-radius:6px;border:1px solid #d1d5db;background:#fff;color:#1a1a2e;font-size:0.9rem;font-weight:600;cursor:pointer;">
          Keep waitlist position (cancel blackout)
        </button>
        <button id="wlRemoveBtn" style="padding:10px 16px;border-radius:6px;border:0;background:#1a1a2e;color:#fff;font-size:0.9rem;font-weight:600;cursor:pointer;">
          Remove from waitlist and add blackout
        </button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  document.getElementById('wlKeepBtn').onclick = () => {
    overlay.remove();
    const msgEl = document.getElementById(`blackoutMsg_${artistId}`);
    if (msgEl) {
      msgEl.textContent = 'Blackout cancelled — your waitlist position is unchanged.';
      msgEl.style.color = '#6b7280';
      setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 4000);
    }
  };
  document.getElementById('wlRemoveBtn').onclick = () => {
    overlay.remove();
    // Re-submit with force=true
    window.addBlackout(artistId, true);
  };
  // Close on overlay click (counts as Keep)
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) document.getElementById('wlKeepBtn').click();
  });
}

function _escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]
  );
}


// ── DELETE BLACKOUT ────────────────────────────────────────────────────────

window.editBlackout = function(artistId, blackoutId, start, end, reason) {
  const panel = document.getElementById(`blackoutEdit_${blackoutId}`);
  if (!panel) return;
  panel.style.display = 'block';
  const sEl = document.getElementById(`editStart_${blackoutId}`);
  const eEl = document.getElementById(`editEnd_${blackoutId}`);
  const rEl = document.getElementById(`editReason_${blackoutId}`);
  if (sEl) sEl.value = start;
  if (eEl) eEl.value = end;
  if (rEl) rEl.value = reason;
};

window.saveBlackoutEdit = async function(artistId, blackoutId) {
  const start = document.getElementById(`editStart_${blackoutId}`)?.value;
  const end = document.getElementById(`editEnd_${blackoutId}`)?.value;
  const reason = document.getElementById(`editReason_${blackoutId}`)?.value?.trim() || '';
  const msgEl = document.getElementById(`editMsg_${blackoutId}`);

  if (!start || !end) {
    if (msgEl) { msgEl.textContent = 'Please select both dates'; msgEl.style.color = '#ef4444'; }
    return;
  }
  if (end < start) {
    if (msgEl) { msgEl.textContent = 'End date must be on or after start date'; msgEl.style.color = '#ef4444'; }
    return;
  }

  try {
    const res = await fetch(`/api/artists/${artistId}/availability/${blackoutId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ blackout_start: start, blackout_end: end, reason })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (msgEl) { msgEl.textContent = err.detail || 'Save failed'; msgEl.style.color = '#ef4444'; }
      return;
    }
    // Re-render the panel
    await renderAvailabilityPanel('availabilityContainer', artistId);
  } catch(e) {
    if (msgEl) { msgEl.textContent = 'Error saving'; msgEl.style.color = '#ef4444'; }
  }
};


window.deleteBlackout = async function(artistId, blackoutId) {
  const msgEl = document.getElementById(`blackoutMsg_${artistId}`);
  try {
    const res = await fetch(`/api/artists/${artistId}/availability/${blackoutId}`, {
      method: 'DELETE',
      credentials: 'include'
    });
    if (!res.ok) throw new Error('Failed to remove');
    if (msgEl) { msgEl.textContent = '✓ Removed'; msgEl.style.color = '#22c55e'; }
    setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 2000);
    await _refreshBlackoutList(artistId);
  } catch (e) {
    if (msgEl) { msgEl.textContent = 'Failed to remove'; msgEl.style.color = '#ef4444'; }
  }
};


// ── REFRESH LIST ──────────────────────────────────────────────────────────

async function _refreshBlackoutList(artistId) {
  try {
    const res = await fetch(`/api/artists/${artistId}/availability`, { credentials: 'include' });
    const data = await res.json();
    const list = document.getElementById(`blackoutList_${artistId}`);
    if (list) list.innerHTML = _renderBlackoutList(data.blackouts || [], artistId);
  } catch (e) {}
}


// ── VENUE-SIDE: SHOW UNAVAILABILITY IN DATE PICKERS ───────────────────────

/**
 * Checks if a specific artist is available on a gig date.
 * Used by venue booking UI to grey out or warn about unavailable artists.
 * @param {number} artistId
 * @param {string} dateStr  YYYY-MM-DD
 * @returns {Promise<boolean>}
 */
async function checkArtistAvailable(artistId, dateStr) {
  try {
    const res = await fetch(`/api/artists/${artistId}/available?check_date=${dateStr}`, { credentials: 'include' });
    const data = await res.json();
    return data.available === true;
  } catch (e) {
    return true; // default to available on error
  }
}

/**
 * Batch-check availability for multiple artists on a single date.
 * @param {number[]} artistIds
 * @param {string} dateStr
 * @returns {Promise<{[artistId]: boolean}>}
 */
async function checkMultipleArtistsAvailable(artistIds, dateStr) {
  const results = {};
  await Promise.all(artistIds.map(async (id) => {
    results[id] = await checkArtistAvailable(id, dateStr);
  }));
  return results;
}

/**
 * Add an unavailability indicator to an artist card element.
 * @param {HTMLElement} el   The artist card DOM element
 * @param {boolean} available
 */
function markArtistAvailability(el, available) {
  if (!el) return;
  if (!available) {
    el.style.opacity = '0.5';
    const badge = document.createElement('div');
    badge.style.cssText = `
      position:absolute;top:6px;right:6px;
      background:rgba(239,68,68,0.9);color:#fff;
      font-size:0.65rem;font-weight:700;border-radius:4px;padding:2px 6px;
    `;
    badge.textContent = 'UNAVAILABLE';
    el.style.position = 'relative';
    el.appendChild(badge);
  } else {
    el.style.opacity = '';
    el.querySelectorAll('[data-avail-badge]').forEach(b => b.remove());
  }
}
