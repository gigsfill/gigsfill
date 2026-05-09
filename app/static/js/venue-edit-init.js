// Auto-extracted from venue-edit.html inline scripts
// Generated for CSP compliance (Phase 5)

// === Block 1 of 4 ===
(function () {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get("venue_id");
  
  if (!venueId) {
    console.error("❌ venue_id missing on venue-edit.html");
    return;
  }
  
  window.venueId = venueId;
  
  const url = new URL(window.location.href);
  if (!url.searchParams.get("venue_id")) {
    url.searchParams.set("venue_id", venueId);
    window.history.replaceState({}, "", url);
  }
})();
  

// === Block 2 of 4 ===
// City autocomplete handled by shared city-autocomplete.js module
document.addEventListener('DOMContentLoaded', function(){
  initCityAutocomplete({ inputId: 'city', stateId: 'state' });
});


// === Block 3 of 4 ===
function logout() {
  fetch('/api/logout', { method: 'POST' })
    .then(() => window.location.href = '/app/index.html')
    .catch(() => window.location.href = '/app/index.html');
}

// Comma formatting for dollar input
function formatWithCommas(input) {
  let value = input.value.replace(/[^\d]/g, '');
  if (value) {
    value = parseInt(value, 10).toLocaleString('en-US');
  }
  input.value = value;
}

document.addEventListener('DOMContentLoaded', function() {
  const dollarsInput = document.getElementById('default_pay_dollars');
  if (dollarsInput) {
    dollarsInput.addEventListener('input', function() {
      formatWithCommas(this);
    });
  }
  
  const capacityInput = document.getElementById('venue_size');
  if (capacityInput) {
    capacityInput.addEventListener('input', function() {
      formatWithCommas(this);
    });
  }
});
  

// === Block 4 of 4 ===
(function () {
  const venueId = window.venueId;
  if (!venueId) return;

  const create = document.getElementById("createGigsBtn");
  const profile = document.getElementById("venueProfileBtn");
  const email = document.getElementById("emailCenterBtn");

  if (create) create.href = `/app/venue-create-gigs.html?venue_id=${venueId}`;
  if (profile) profile.href = `/app/venue-profile.html?venue_id=${venueId}`;
  if (email) email.href = `/app/venue-email-center.html?venue_id=${venueId}`;
})();

// === Additional Block (Phase 5 pass 2) ===
// PRO License Management - Auto-save
let _proUploadTarget = '';

function showProStatus(msg, color) {
  const el = document.getElementById('proSaveStatus');
  if (!el) return;
  el.textContent = msg;
  el.style.color = color || '#22c55e';
  el.style.opacity = '1';
  if (msg === '✓ Saved') setTimeout(() => { el.style.opacity = '0'; }, 2000);
}

async function autoSaveProCert() {
  const venueId = window.venueId;
  if (!venueId) return;
  showProStatus('Saving...', 'var(--text-gray)');
  try {
    const certified = document.getElementById('pro_certified').checked ? 1 : 0;
    const res = await fetch(`/api/venues/${venueId}`, {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pro_certified: certified, pro_certified_at: certified ? new Date().toISOString() : null })
    });
    if (!res.ok) throw new Error(res.status);
    showProStatus('✓ Saved', '#22c55e');
  } catch (e) {
    console.error('PRO cert save:', e);
    showProStatus('✗ Save failed', '#ef4444');
  }
}

async function autoSaveProLicenses() {
  const venueId = window.venueId;
  if (!venueId) return;
  showProStatus('Saving...', 'var(--text-gray)');
  try {
    const licenses = ['ASCAP', 'BMI', 'SESAC', 'GMR'].map(pro => ({
      pro_name: pro,
      license_number: document.getElementById(`pro_${pro.toLowerCase()}_number`)?.value || '',
      expiration_date: document.getElementById(`pro_${pro.toLowerCase()}_expiration`)?.value || ''
    }));
    const res = await fetch(`/api/venues/${venueId}/pro-licenses`, {
      method: 'PUT', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ licenses })
    });
    if (!res.ok) throw new Error(res.status);
    showProStatus('✓ Saved', '#22c55e');
  } catch (e) {
    console.error('PRO licenses save:', e);
    showProStatus('✗ Save failed', '#ef4444');
  }
}

async function saveProLicenses() { await autoSaveProCert(); await autoSaveProLicenses(); }

async function loadProLicenses() {
  const venueId = window.venueId;
  if (!venueId) return;
  
  try {
    const res = await fetch(`/api/venues/${venueId}`, { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      const cb = document.getElementById('pro_certified');
      if (cb) cb.checked = !!data.pro_certified;
    }
  } catch (e) {}
  
  try {
    const res = await fetch(`/api/venues/${venueId}/pro-licenses`, { credentials: 'include' });
    if (!res.ok) return;
    const data = await res.json();
    (data.licenses || []).forEach(lic => {
      const key = lic.pro_name.toLowerCase();
      const numEl = document.getElementById(`pro_${key}_number`);
      const expEl = document.getElementById(`pro_${key}_expiration`);
      const fileEl = document.getElementById(`pro_${key}_file`);
      if (numEl && lic.license_number) numEl.value = lic.license_number;
      if (expEl && lic.expiration_date) expEl.value = lic.expiration_date;
      if (fileEl && lic.license_file_path) {
        fileEl.innerHTML = `<a href="${escAttr(lic.license_file_path)}" target="_blank" style="color: var(--cyan);">📄 View uploaded license</a>`;
      }
    });
  } catch (e) {}
}

function uploadProLicense(proName) {
  _proUploadTarget = proName;
  document.getElementById('proLicenseFileInput').click();
}

function bindProAutosave() {
  const cb = document.getElementById('pro_certified');
  if (cb) cb.addEventListener('change', autoSaveProCert);
  
  ['ascap', 'bmi', 'sesac', 'gmr'].forEach(pro => {
    const numEl = document.getElementById(`pro_${pro}_number`);
    const expEl = document.getElementById(`pro_${pro}_expiration`);
    [numEl, expEl].forEach(el => {
      if (!el) return;
      let timer = null;
      el.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(autoSaveProLicenses, 500); });
      el.addEventListener('blur', () => { clearTimeout(timer); autoSaveProLicenses(); });
      el.addEventListener('change', () => { clearTimeout(timer); autoSaveProLicenses(); });
    });
  });
}

document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('proLicenseFileInput')?.addEventListener('change', async function() {
    if (!this.files.length || !_proUploadTarget) return;
    const venueId = window.venueId;
    if (!venueId) return;
    const formData = new FormData();
    formData.append('file', this.files[0]);
    const key = _proUploadTarget.toLowerCase();
    const fileEl = document.getElementById(`pro_${key}_file`);
    if (fileEl) fileEl.innerHTML = '<span style="color: var(--cyan);">Uploading...</span>';
    try {
      const res = await fetch(`/api/venues/${venueId}/pro-licenses/${_proUploadTarget}/upload`, {
        method: 'POST', credentials: 'include', body: formData
      });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      if (fileEl) fileEl.innerHTML = `<a href="${escAttr(data.file_path)}" target="_blank" style="color: var(--cyan);">📄 View uploaded license</a>`;
    } catch (e) {
      if (fileEl) fileEl.innerHTML = '<span style="color: #ef4444;">Upload failed</span>';
    }
    this.value = '';
  });
  
  const wait = setInterval(() => {
    if (window.venueId) { clearInterval(wait); loadProLicenses(); bindProAutosave(); }
  }, 200);
  setTimeout(() => clearInterval(wait), 5000);
});

// === Auto-Flyers Setting — auto-save on toggle ===
document.addEventListener('DOMContentLoaded', function() {
  const cb = document.getElementById('auto_flyers');
  if (!cb) return;
  cb.addEventListener('change', async function() {
    const venueId = window.venueId;
    if (!venueId) return;
    try {
      const res = await fetch(`/api/venues/${venueId}`, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_flyers: cb.checked ? 1 : 0 })
      });
      if (!res.ok) throw new Error(res.status);
    } catch (e) {
      console.error('auto_flyers save:', e);
    }
  });
});
