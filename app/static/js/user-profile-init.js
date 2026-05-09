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
  
  // Fetch owned entities
  try {
    const resp = await fetch('/api/me/delete-preview', { credentials: 'include' });
    if (resp.ok) {
      const data = await resp.json();
      const artists = data.artists || [];
      const venues = data.venues || [];
      
      if (artists.length > 0 || venues.length > 0) {
        entSection.style.display = 'block';
        let html = '';
        
        artists.forEach(a => {
          const gigsNote = a.booked_gigs > 0 ? ` <span style="color:#f59e0b;font-size:0.75rem;">(${a.booked_gigs} booked gig${a.booked_gigs > 1 ? 's' : ''})</span>` : '';
          html += `<label style="display:flex;align-items:center;gap:10px;padding:8px 10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;cursor:pointer;">
            <input type="checkbox" checked class="delete-entity-cb" data-type="artist" data-id="${a.id}" data-gigs="${a.booked_gigs}" onchange="updateDeleteWarning()" style="width:16px;height:16px;accent-color:#ef4444;">
            <span style="font-size:0.85rem;color:var(--text);">🎤 <strong>${escapeHtmlDel(a.name)}</strong>${gigsNote}</span>
          </label>`;
        });
        
        venues.forEach(v => {
          const gigsNote = v.booked_gigs > 0 ? ` <span style="color:#f59e0b;font-size:0.75rem;">(${v.booked_gigs} booked gig${v.booked_gigs > 1 ? 's' : ''})</span>` : '';
          html += `<label style="display:flex;align-items:center;gap:10px;padding:8px 10px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:6px;cursor:pointer;">
            <input type="checkbox" checked class="delete-entity-cb" data-type="venue" data-id="${v.id}" data-gigs="${v.booked_gigs}" onchange="updateDeleteWarning()" style="width:16px;height:16px;accent-color:#ef4444;">
            <span style="font-size:0.85rem;color:var(--text);">📍 <strong>${escapeHtmlDel(v.name)}</strong>${gigsNote}</span>
          </label>`;
        });
        
        entList.innerHTML = html;
        updateDeleteWarning();
      }
    }
  } catch (e) {
    // Non-critical, modal still works
  }
  
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

// Enable button only when "DELETE" is typed
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('deleteConfirmInput');
  if (input) {
    input.addEventListener('input', () => {
      const btn = document.getElementById('confirmDeleteBtn');
      const match = input.value.trim() === 'DELETE';
      btn.disabled = !match;
      btn.style.opacity = match ? '1' : '0.4';
      btn.style.cursor = match ? 'pointer' : 'not-allowed';
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
  
  try {
    const resp = await fetch('/api/me/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ delete_entities: entities })
    });
    
    if (!resp.ok) throw new Error('Failed');
    
    // Clear session and redirect
    window.location.href = '/?deleted=1';
  } catch (e) {
    btn.textContent = 'Delete Account';
    btn.disabled = false;
    btn.style.opacity = '1';
    alert('Failed to delete account. Please try again.');
  }
}

function escapeHtmlDel(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}
  

