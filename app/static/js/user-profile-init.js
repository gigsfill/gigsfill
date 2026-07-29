// Auto-extracted from user-profile.html inline scripts
// Generated for CSP compliance (Phase 5)

// === Block 1 of 2 ===
function logout() {
  fetch('/api/logout', { method: 'POST' })
    .then(() => window.location.href = '/app/index.html')
    .catch(() => window.location.href = '/app/index.html');
}

// Format zip code - only 5 digits
function formatZipCode(input) {
  input.value = input.value.replace(/[^0-9]/g, '').substring(0, 5);
}

// City autocomplete handled by shared city-autocomplete.js module
document.addEventListener('DOMContentLoaded', function() {
  initCityAutocomplete({ inputId: 'modal_artistCity', stateId: 'modal_artistState' });
  initCityAutocomplete({ inputId: 'modal_venueCity', stateId: 'modal_venueState' });
});

// Format modal frequency - 0-365 only
function formatModalFrequency(input) {
  let value = parseInt(input.value) || 0;
  if (value < 0) value = 0;
  if (value > 365) value = 365;
  input.value = value;
}

document.getElementById('passwordForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  
  const currentPassword = document.getElementById('currentPassword').value;
  const newPassword = document.getElementById('newPassword').value;
  const confirmPassword = document.getElementById('confirmPassword').value;
  
  if (!currentPassword) {
    showError('Please enter your current password');
    return;
  }
  
  if (newPassword !== confirmPassword) {
    showError('New passwords do not match');
    return;
  }
  
  if (newPassword.length < 6) {
    showError('New password must be at least 6 characters');
    return;
  }
  
  try {
    const response = await fetch('/api/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword
      })
    });
    
    if (response.ok) {
      showSuccess('Password changed successfully!');
      document.getElementById('passwordForm').reset();
    } else {
      const err = await response.json().catch(() => ({}));
      showError(err.detail || 'Failed to change password');
    }
  } catch (error) {
    console.error('Error changing password:', error);
    showError('An error occurred while changing password');
  }
});

  

// === Block 2 of 2 ===
let _deleteEntities = [];

async function openDeleteAccountModal() {
  const modal = document.getElementById('deleteAccountModal');
  const input = document.getElementById('deleteConfirmInput');
  const btn = document.getElementById('confirmDeleteBtn');
  const entSection = document.getElementById('deleteEntitiesSection');
  const entList = document.getElementById('deleteEntitiesList');
  const gigsWarn = document.getElementById('bookedGigsWarning');
  
  input.value = '';
  btn.disabled = true;
  btn.style.opacity = '0.4';
  btn.style.cursor = 'not-allowed';
  entSection.style.display = 'none';
  gigsWarn.style.display = 'none';
  _deleteEntities = [];
  
  // Fetch owned entities. BUG FIX (Jul 2026 audit): if this fetch fails,
  // the modal can no longer show the correct live-txn blockers or
  // solo/team split — so we abort with a branded error instead of
  // silently opening a half-populated form the user could submit.
  let previewOk = false;
  try {
    const resp = await fetch('/api/me/delete-preview', { credentials: 'include' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    previewOk = true;
    {
      const data = await resp.json();
      const artists = data.artists || [];
      const venues = data.venues || [];
      
      const delegated = data.delegated_memberships || [];
      if (artists.length > 0 || venues.length > 0 || delegated.length > 0) {
        entSection.style.display = 'block';
        let html = '';

        // Row builder. Solo-owned entities (other_users_count === 0) render
        // as a locked, checked row with a clear "will be deleted — you're
        // the only user" caption. Team-shared entities are default UNCHECKED.
        // Live-txn blockers are surfaced per row AND checked across the
        // whole list — if any owned entity has one, the Delete Account
        // button becomes non-clickable and a top banner explains why.
        const rowHtml = (kind, ent) => {
          const icon = kind === 'artist' ? '🎤' : '📍';
          const kindWord = kind === 'artist' ? 'Artist' : 'Venue';
          const others = parseInt(ent.other_users_count || 0);
          const isSolo = others === 0;
          const live = ent.live_txns || [];
          const hasLive = live.length > 0;
          const gigsNote = ent.booked_gigs > 0
            ? ` <span style="color:#f59e0b;font-size:0.75rem;">(${ent.booked_gigs} upcoming booked gig${ent.booked_gigs > 1 ? 's' : ''} — will be cancelled)</span>`
            : '';
          // Team-member note. Solo entities MUST be deleted (nobody else could
          // access them otherwise). Multi-user entities are an owner's
          // CHOICE — either delete (all team members lose access) or leave
          // alive to hand off to the remaining team. Copy makes both cases
          // fully explicit so the owner can decide without guessing.
          const teamNote = isSolo
            ? `<span style="color:#f87171;font-size:0.72rem;">You are the only user of this ${kindWord}, so it must be deleted.</span>`
            : `<span style="color:#94a3b8;font-size:0.72rem;">This ${kindWord} has <strong>${others}</strong> other team member${others > 1 ? 's' : ''}. Check to delete it (they'll lose access), or leave unchecked to hand it off to them.</span>`;
          const liveNote = hasLive
            ? `<div style="margin-top:6px;font-size:0.72rem;color:#fca5a5;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:4px;padding:6px 8px;">🚫 Blocked by ${live.length} in-flight payment${live.length > 1 ? 's' : ''}. These clear on their own once each gig's payout completes — try again after they've all settled.</div>`
            : '';
          const checked = isSolo ? 'checked disabled' : '';
          const cursor = isSolo ? 'default' : 'pointer';
          return `<label style="display:flex;align-items:flex-start;gap:10px;padding:8px 10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;cursor:${cursor};">
            <input type="checkbox" ${checked} class="delete-entity-cb"
                   data-type="${kind}" data-id="${ent.id}" data-gigs="${ent.booked_gigs || 0}"
                   data-solo="${isSolo ? '1' : '0'}"
                   data-live="${hasLive ? '1' : '0'}"
                   onchange="updateDeleteWarning()" style="width:16px;height:16px;accent-color:#ef4444;margin-top:2px;">
            <span style="font-size:0.85rem;color:var(--text);line-height:1.5;flex:1;">
              ${icon} <strong>${escapeHtmlDel(ent.name)}</strong>${gigsNote}<br>${teamNote}
              ${liveNote}
            </span>
          </label>`;
        };

        artists.forEach(a => { html += rowHtml('artist', a); });
        venues.forEach(v  => { html += rowHtml('venue',  v); });

        // Aggregate live-txn banner — if any owned entity has blockers,
        // the whole Delete Account flow is disabled since the backend
        // will 409 anyway. Ties the state to the button-enable rule in
        // the DOMContentLoaded handler below.
        const anyLive = artists.some(a => (a.live_txns || []).length > 0)
                     || venues.some(v  => (v.live_txns  || []).length > 0);
        if (anyLive) {
          html = `<div id="deleteLiveTxnBanner" style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.4);border-radius:8px;padding:12px 14px;margin-bottom:14px;">
            <p style="color:#fca5a5;font-size:0.85rem;font-weight:700;margin:0 0 4px;">🚫 Account deletion is blocked</p>
            <p style="color:#fca5a5;font-size:0.78rem;margin:0;">One of your profiles still has payments in flight (money charged but not yet transferred, transfers still processing, or an open dispute). These clear on their own once each gig's payout completes — try again once all of them have settled. Historical/settled payments don't block deletion.</p>
          </div>` + html;
        }

        // Delegated memberships — entities the user is on the team of but
        // does NOT own. These stay alive after deletion; we just surface
        // them so the user isn't surprised to lose access, and so they can
        // ask the owner to transfer / re-invite them if that's not intended.
        if (delegated.length > 0) {
          html += '<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border);">';
          html += '<p style="color:var(--text-gray);font-size:0.78rem;margin:0 0 8px;font-weight:600;">You\'ll also be removed from these teams (the profiles stay alive under their owners):</p>';
          delegated.forEach(m => {
            const icon = m.entity_type === 'artist' ? '🎤' : '📍';
            const role = m.role ? ` <span style="color:#94a3b8;font-size:0.72rem;">(${escapeHtmlDel(m.role)})</span>` : '';
            html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:0.82rem;color:var(--text-gray);">
              ${icon} <strong>${escapeHtmlDel(m.name)}</strong>${role}
            </div>`;
          });
          html += '</div>';
        }

        entList.innerHTML = html;
        updateDeleteWarning();
      }
    }
  } catch (e) {
    // Preview endpoint failed — refuse to open the modal with stale/empty
    // state. Show branded error so the user retries once the API recovers.
    if (typeof window.showErrorModal === 'function') {
      window.showErrorModal(
        'Delete Account Unavailable',
        'We couldn\'t load your profile info to prepare the delete flow safely. Please try again in a moment.'
      );
    } else {
      alert('Delete Account is temporarily unavailable. Please try again.');
    }
    return;
  }
  if (!previewOk) return;

  modal.style.display = 'flex';
  input.focus();
}

function updateDeleteWarning() {
  const gigsWarn = document.getElementById('bookedGigsWarning');
  const cbs = document.querySelectorAll('.delete-entity-cb:checked');
  let totalGigs = 0;
  cbs.forEach(cb => totalGigs += parseInt(cb.dataset.gigs || 0));
  gigsWarn.style.display = totalGigs > 0 ? 'block' : 'none';
}

// Enable button only when "DELETE" is typed AND no live-txn blockers exist.
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('deleteConfirmInput');
  if (input) {
    input.addEventListener('input', () => {
      const btn = document.getElementById('confirmDeleteBtn');
      const typed = input.value.trim() === 'DELETE';
      // Live-txn banner presence = hard block from the backend, don't
      // even let the user click. Backend guard would 409 anyway.
      const blocked = !!document.getElementById('deleteLiveTxnBanner');
      const match = typed && !blocked;
      btn.disabled = !match;
      btn.style.opacity = match ? '1' : '0.4';
      btn.style.cursor = match ? 'pointer' : 'not-allowed';
      btn.title = blocked
        ? 'Resolve mid-flight payments first (see banner above).'
        : (typed ? '' : 'Type DELETE to confirm.');
    });
  }
});

async function executeDeleteAccount() {
  const btn = document.getElementById('confirmDeleteBtn');
  btn.disabled = true;
  btn.textContent = 'Deleting...';
  
  // Gather checked entities
  const entities = [];
  document.querySelectorAll('.delete-entity-cb:checked').forEach(cb => {
    entities.push({ type: cb.dataset.type, id: parseInt(cb.dataset.id) });
  });
  
  // BUG FIX (Jul 2026 audit): surface the backend detail via branded modal
  // instead of a raw alert("Failed"). Live-txn / not-your-entity errors now
  // reach the user with the real reason.
  try {
    const resp = await fetch('/api/me/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ delete_entities: entities })
    });

    if (resp.ok) {
      window.location.href = '/?deleted=1';
      return;
    }

    let detail = '';
    try { const j = await resp.json(); detail = (j && j.detail) || ''; } catch (_) {}

    // BUG FIX (Jul 2026 audit): after a failed submit the button used to
    // re-enable (opacity=1) even though the DELETE typed-input still had
    // the old value. That let the user click Delete again with a single
    // click. Now: clear the input AND re-disable the button so the user
    // must re-type DELETE to confirm — matches the input handler's
    // enable-once-typed semantics.
    const _confirmInput = document.getElementById('deleteConfirmInput');
    if (_confirmInput) _confirmInput.value = '';
    btn.textContent = 'Delete Account';
    btn.disabled = true;
    btn.style.opacity = '0.4';
    btn.style.cursor = 'not-allowed';

    const _title = 'Delete Account Failed';
    if (typeof detail === 'string' && detail.startsWith('LIVE_TRANSACTION_EXISTS')) {
      window.showErrorModal('Deletion Blocked', detail.replace(/^LIVE_TRANSACTION_EXISTS:\s*/, ''));
    } else if (typeof detail === 'string' && detail.startsWith('NOT_YOUR_')) {
      window.showErrorModal('Not Allowed', 'One of the selected profiles isn\'t owned by you.');
    } else if (typeof detail === 'string' && detail.startsWith('ALREADY_DELETED')) {
      window.showAlert('One of the selected profiles was already deleted.', 'Already Deleted');
    } else if (typeof window.showErrorModal === 'function') {
      window.showErrorModal(_title, detail || ('HTTP ' + resp.status));
    } else {
      alert(detail || 'Failed to delete account. Please try again.');
    }
  } catch (e) {
    // Same re-lock behavior on network-error path.
    const _confirmInput = document.getElementById('deleteConfirmInput');
    if (_confirmInput) _confirmInput.value = '';
    btn.textContent = 'Delete Account';
    btn.disabled = true;
    btn.style.opacity = '0.4';
    btn.style.cursor = 'not-allowed';
    btn.style.opacity = '1';
    if (typeof window.showErrorModal === 'function') {
      window.showErrorModal('Delete Account Failed', (e && e.message) || 'Network error. Please try again.');
    } else {
      alert('Failed to delete account. Please try again.');
    }
  }
}

function escapeHtmlDel(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}
  

