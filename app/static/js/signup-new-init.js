// Auto-extracted from signup-new.html inline scripts
// Generated for CSP compliance (Phase 5)

// === Block 1 of 2 ===
let currentStep = 1;
let selectedRole = window.selectedRole || '';

console.log('[SIGNUP v3] Script loaded');

function selectRole(role) {
  console.log('[SIGNUP] selectRole called with:', role);
  selectedRole = role;
  window.selectedRole = role; // sync with inline handler
  document.querySelectorAll('.role-card').forEach(c => c.classList.remove('selected'));
  const card = document.querySelector(`.role-card[data-role="${role}"]`);
  if (card) card.classList.add('selected');
  else console.warn('[SIGNUP] No card found for role:', role);
}
// Make globally accessible
window.selectRole = selectRole;

// Attach click listeners to role cards (CSP-safe, no inline onclick)
function attachRoleListeners() {
  const cards = document.querySelectorAll('.role-card[data-role]');
  console.log('[SIGNUP] Found role cards with data-role:', cards.length);
  if (cards.length === 0) {
    // Fallback: try without data-role attribute
    const allCards = document.querySelectorAll('.role-card');
    console.log('[SIGNUP] Fallback - all .role-card elements:', allCards.length);
    allCards.forEach((card, i) => {
      const role = i === 0 ? 'artist' : 'venue';
      card.addEventListener('click', () => selectRole(role));
      console.log('[SIGNUP] Attached fallback click listener for:', role);
    });
  } else {
    cards.forEach(card => {
      card.addEventListener('click', () => selectRole(card.dataset.role));
      console.log('[SIGNUP] Attached click listener for:', card.dataset.role);
    });
  }
}

// Try immediately (script is at bottom of page)
attachRoleListeners();

// Also try on DOMContentLoaded in case DOM isn't ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', attachRoleListeners);
  console.log('[SIGNUP] Also waiting for DOMContentLoaded');
}

function toggleBandFormats() {
  const artistType = document.getElementById('artistType').value;
  const isLiveBand = artistType === 'Live Band';
  document.getElementById('bandFormatsField').style.display = isLiveBand ? 'block' : 'none';
  const lineupField = document.getElementById('lineupField');
  if (lineupField) lineupField.style.display = isLiveBand ? 'block' : 'none';
}

async function nextStep() {
  // Sync from window in case inline handler set it
  if (!selectedRole && window.selectedRole) selectedRole = window.selectedRole;
  if (currentStep === 1 && !selectedRole) {
    showError('Please select an account type');
    return;
  }
  
  if (currentStep === 2) {
    // Validate step 2
    const firstName = document.getElementById('firstName').value;
    const lastName = document.getElementById('lastName').value;
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;
    const phone = document.getElementById('phone').value;
    
    if (!firstName || !lastName || !email || !password || !phone) {
      showError('Please fill in all required fields');
      return;
    }
    
    if (password.length < 6) {
      showError('Password must be at least 6 characters');
      return;
    }
    
    if (!email.includes('@')) {
      showError('Please enter a valid email address');
      return;
    }
  }
  
  if (currentStep === 3 && selectedRole === 'venue') {
    // Validate venue step 3 before going to step 4
    const venueName = document.getElementById('venueName');
    const address = document.getElementById('venueAddress');
    const city = document.getElementById('venueCity');
    const state = document.getElementById('venueState');
    const zip = document.getElementById('venueZip');
    
    // Clear previous highlights
    [venueName, address, city, state, zip].forEach(field => {
      field.style.border = '';
      field.style.boxShadow = '';
    });
    
    // Check each field
    let firstError = null;
    if (!venueName.value) {
      venueName.style.border = '2px solid #ef4444';
      venueName.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, 0.2)';
      firstError = firstError || 'Venue Name is required';
    }
    if (!address.value) {
      address.style.border = '2px solid #ef4444';
      address.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, 0.2)';
      firstError = firstError || 'Address is required';
    }
    if (!city.value) {
      city.style.border = '2px solid #ef4444';
      city.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, 0.2)';
      firstError = firstError || 'City is required';
    }
    if (!state.value) {
      state.style.border = '2px solid #ef4444';
      state.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, 0.2)';
      firstError = firstError || 'State is required';
    }
    if (!zip.value) {
      zip.style.border = '2px solid #ef4444';
      zip.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, 0.2)';
      firstError = firstError || 'Zip Code is required';
    }
    
    if (firstError) {
      showError(firstError);
      return;
    }
    
    // CITY VALIDATION — must be in our system before proceeding
    if (city.value && state.value) {
      try {
        const r = await fetch('/api/validate-city?city=' + encodeURIComponent(city.value.trim()) + '&state=' + encodeURIComponent(state.value) + '&_t=' + Date.now());
        const d = await r.json();
        if (!d.valid) {
          const r2 = await fetch('/api/validate-city?city=' + encodeURIComponent(city.value.trim()) + '&_t=' + Date.now());
          const d2 = await r2.json();
          if (d2.valid && d2.state) {
            state.value = d2.state;
            state.dispatchEvent(new Event('change', { bubbles: true }));
          } else {
            showCityError(city, true);
            return;
          }
        }
      } catch(e) {
        showCityError(city, true);
        return;
      }
    } else if (city.value && !state.value) {
      try {
        const r = await fetch('/api/validate-city?city=' + encodeURIComponent(city.value.trim()) + '&_t=' + Date.now());
        const d = await r.json();
        if (d.valid && d.state) {
          state.value = d.state;
          state.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
          showCityError(city, true);
          return;
        }
      } catch(e) {
        showCityError(city, true);
        return;
      }
    }
  }
  
  hideError();
  
  // Hide current step - handle special step3 and step4 IDs
  if (currentStep === 3) {
    if (selectedRole === 'artist') {
      document.getElementById('step3-artist')?.classList.remove('active');
    } else {
      document.getElementById('step3-venue')?.classList.remove('active');
    }
  } else if (currentStep === 4) {
    document.getElementById('step4-venue')?.classList.remove('active');
  } else {
    document.getElementById(`step${currentStep}`)?.classList.remove('active');
  }
  
  const currentIndicator = document.getElementById(`step${currentStep}-indicator`);
  if (currentIndicator) {
    currentIndicator.classList.add('completed');
    currentIndicator.classList.remove('active');
  }
  
  // Show next step
  currentStep++;
  
  if (currentStep === 3) {
    // Show appropriate step 3 based on role
    if (selectedRole === 'artist') {
      document.getElementById('step3-artist').classList.add('active');
      // Populate booking contact from step 2 info
      const bc = document.getElementById('artistBookingContact');
      if (bc) {
        const fn = document.getElementById('firstName').value || '';
        const ln = document.getElementById('lastName').value || '';
        const em = document.getElementById('email').value || '';
        const ph = document.getElementById('phone').value || '';
        const contactStr = `${fn} ${ln} - ${em} - ${ph}`.trim();
        bc.innerHTML = `<option value="${contactStr}" selected>${contactStr}</option>`;
      }
    } else {
      document.getElementById('step3-venue').classList.add('active');
    }
  } else if (currentStep === 4 && selectedRole === 'venue') {
    // Show step 4 for venues only
    document.getElementById('step4-venue').classList.add('active');
  } else {
    document.getElementById(`step${currentStep}`)?.classList.add('active');
  }
  
  const nextIndicator = document.getElementById(`step${currentStep}-indicator`);
  if (nextIndicator) {
    nextIndicator.classList.add('active');
  }
}

function prevStep() {
  hideError();
  
  // Hide current step
  if (currentStep === 3) {
    document.getElementById('step3-artist')?.classList.remove('active');
    document.getElementById('step3-venue')?.classList.remove('active');
  } else if (currentStep === 4) {
    document.getElementById('step4-venue')?.classList.remove('active');
  } else {
    document.getElementById(`step${currentStep}`)?.classList.remove('active');
  }
  
  const currentIndicator = document.getElementById(`step${currentStep}-indicator`);
  if (currentIndicator) {
    currentIndicator.classList.remove('active');
  }
  
  // Show previous step
  currentStep--;
  
  if (currentStep === 3 && selectedRole === 'venue') {
    document.getElementById('step3-venue').classList.add('active');
  } else if (currentStep === 3 && selectedRole === 'artist') {
    document.getElementById('step3-artist').classList.add('active');
  } else {
    document.getElementById(`step${currentStep}`)?.classList.add('active');
  }
  
  const prevIndicator = document.getElementById(`step${currentStep}-indicator`);
  if (prevIndicator) {
    prevIndicator.classList.add('active');
    prevIndicator.classList.remove('completed');
  }
}

async function completeSignup() {
  console.log('[SIGNUP] completeSignup called, role:', selectedRole);
  try {
    const payload = {
      first_name: document.getElementById('firstName').value,
      last_name: document.getElementById('lastName').value,
      email: document.getElementById('email').value,
      password: document.getElementById('password').value,
      phone: document.getElementById('phone').value,
      role: selectedRole
    };

    // Pass affiliate code from URL param or cookie
    const affParam = new URLSearchParams(window.location.search).get('aff');
    const affCookie = document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('aff_code='));
    const affCode = affParam || (affCookie ? decodeURIComponent(affCookie.split('=')[1]) : null);
    if (affCode) payload.affiliate_code = affCode.trim().toUpperCase();
    
    // --- CITY VALIDATION FUNCTION (inline, no external deps) ---
    // Returns { valid: bool, state: string|null }
    async function checkCity(city, state) {
      try {
        const r = await fetch('/api/validate-city?city=' + encodeURIComponent(city.trim()) + (state ? '&state=' + encodeURIComponent(state) : '') + '&_t=' + Date.now());
        return await r.json();
      } catch(e) { return { valid: false }; }
    }
    
    if (selectedRole === 'artist') {
      payload.artist_name = document.getElementById('artistName').value;
      payload.artist_type = document.getElementById('artistType').value;
      payload.city = document.getElementById('artistCity').value;
      payload.state = document.getElementById('artistState').value;
      payload.bio = document.getElementById('artistBio').value;
      
      if (!payload.artist_name || !payload.artist_type || !payload.city || !payload.state) {
        showError('Please fill in all required fields');
        return;
      }
      
      // CITY VALIDATION - HARD BLOCK + AUTO-FILL STATE
      let artistCityCheck = await checkCity(payload.city, payload.state);
      if (!artistCityCheck.valid) {
        artistCityCheck = await checkCity(payload.city, '');
        if (!artistCityCheck.valid) {
          showCityError(document.getElementById('artistCity'), true);
          return;
        }
      }
      if (artistCityCheck.state) {
        payload.state = artistCityCheck.state;
        document.getElementById('artistState').value = artistCityCheck.state;
      }
      
      // Check for duplicate artist name+city+state
      if (typeof checkDuplicateEntity === 'function') {
        const isDuplicate = await checkDuplicateEntity();
        if (isDuplicate) return;
      }
      
      // Styles and Lineup if Live Band
      if (payload.artist_type === 'Live Band') {
        const checkedStyles = Array.from(document.querySelectorAll('input[name="artist_style"]:checked'));
        if (checkedStyles.length === 0) {
          showError('Please select at least one style');
          return;
        }
        payload.styles = checkedStyles.map(cb => cb.value).join(',');
        
        const checkedFormats = Array.from(document.querySelectorAll('input[name="band_format"]:checked'));
        if (checkedFormats.length === 0) {
          showError('Please select at least one lineup option');
          return;
        }
        payload.band_formats = checkedFormats.map(cb => cb.value).join(',');
      }
    } else {
      payload.venue_name = document.getElementById('venueName').value;
      payload.address = document.getElementById('venueAddress').value;
      payload.city = document.getElementById('venueCity').value;
      payload.state = document.getElementById('venueState').value;
      payload.zip = document.getElementById('venueZip').value;
      payload.description = document.getElementById('venueDescription').value;
      const payDollars = document.getElementById('venueDefaultPayDollars').value.replace(/,/g, '');
      const payCents = document.getElementById('venueDefaultPayCents').value || '00';
      payload.default_pay_dollars = parseInt(payDollars) || 0;
      payload.default_pay_cents = parseInt(payCents) || 0;
      payload.default_pay = payload.default_pay_dollars + (payload.default_pay_cents / 100);
      payload.performance_frequency = document.getElementById('venueFrequency').value;
      const capacityValue = document.getElementById('venueCapacity').value.replace(/,/g, '');
      payload.capacity = parseInt(capacityValue) || 0;
      
      // v73: Amenity fields - default stage dimensions to 0 if no stage
      payload.has_stage = parseInt(document.getElementById('venueHasStage')?.value) || 0;
      payload.stage_width_ft = payload.has_stage ? (document.getElementById('venueStageWidth')?.value || 0) : 0;
      payload.stage_depth_ft = payload.has_stage ? (document.getElementById('venueStageDepth')?.value || 0) : 0;
      payload.setup_location_description = document.getElementById('venueSetupLocation')?.value || null;
      payload.has_sound_equipment = parseInt(document.getElementById('venueHasSoundEquipment')?.value) || 0;
      payload.sound_equipment_description = document.getElementById('venueSoundEquipmentDesc')?.value || null;
      payload.has_sound_engineer = parseInt(document.getElementById('venueHasSoundEngineer')?.value) || 0;
      payload.sound_engineer_details = document.getElementById('venueSoundEngineerDetails')?.value || null;
      payload.has_lighting = parseInt(document.getElementById('venueHasLighting')?.value) || 0;
      payload.lighting_description = document.getElementById('venueLightingDesc')?.value || null;
      payload.bar_tab_details = document.getElementById('venueBarTabDetails')?.value || null;
      payload.food_tab_details = document.getElementById('venueFoodTabDetails')?.value || null;
      payload.load_in_out_details = document.getElementById('venueLoadInOut')?.value || null;
      
      // PRO certification - required
      const proCert = document.getElementById('venueProCertified');
      if (!proCert || !proCert.checked) {
        showError('You must certify that your venue maintains active public performance licenses to continue.');
        return;
      }
      payload.pro_certified = 1;
      
      // v73: Arrival time fields
      payload.arrival_time_type = document.getElementById('venueArrivalType')?.value || 'flexible';
      payload.arrival_no_earlier_than_hour = document.getElementById('venueArrivalHour')?.value || null;
      payload.arrival_no_earlier_than_period = document.getElementById('venueArrivalPeriod')?.value || null;
      
      if (!payload.venue_name || !payload.address || !payload.city || !payload.state || !payload.zip || payload.default_pay_dollars === undefined || !payload.performance_frequency || !payload.capacity) {
        showError('Please fill in all required fields');
        return;
      }
      
      // CITY VALIDATION - HARD BLOCK + AUTO-FILL STATE
      let venueCityCheck = await checkCity(payload.city, payload.state);
      if (!venueCityCheck.valid) {
        venueCityCheck = await checkCity(payload.city, '');
        if (!venueCityCheck.valid) {
          showCityError(document.getElementById('venueCity'), true);
          return;
        }
      }
      if (venueCityCheck.state) {
        payload.state = venueCityCheck.state;
        document.getElementById('venueState').value = venueCityCheck.state;
      }
      
      // Check for duplicate venue name+city+state
      if (typeof checkDuplicateEntity === 'function') {
        const isDuplicate = await checkDuplicateEntity();
        if (isDuplicate) return;
      }
    }
    
    console.log('[SIGNUP] City validation passed, proceeding with signup');
    
    // Show loading state on Create Account button
    const signupBtn = event?.target || document.querySelector('button[onclick*="completeSignup"]');
    if (signupBtn) { signupBtn.disabled = true; signupBtn.textContent = 'Creating Account...'; }
    
    const res = await fetch("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    
    if (!res.ok) {
      const error = await res.text();
      throw new Error(error);
    }
    
    // Success - fetch /api/me to get entity IDs and redirect to the right dashboard
    let dashboardUrl = '/app/index.html'; // fallback
    try {
      const meRes = await fetch('/api/me', { credentials: 'include' });
      if (meRes.ok) {
        const me = await meRes.json();
        if (selectedRole === 'artist' && me.artists && me.artists.length > 0) {
          dashboardUrl = `/app/artist-book-gigs.html?artist_id=${me.artists[0].id}`;
        } else if (selectedRole === 'venue' && me.venues && me.venues.length > 0) {
          dashboardUrl = `/app/venue-create-gigs.html?venue_id=${me.venues[0].id}`;
        } else if (me.artists && me.artists.length > 0) {
          dashboardUrl = `/app/artist-book-gigs.html?artist_id=${me.artists[0].id}`;
        } else if (me.venues && me.venues.length > 0) {
          dashboardUrl = `/app/venue-create-gigs.html?venue_id=${me.venues[0].id}`;
        } else {
          dashboardUrl = '/app/user-profile.html';
        }
      }
    } catch (e) {
      dashboardUrl = '/app/user-profile.html';
    }

    // Phase 2 migration: was an inline-styled non-dismissible "redirecting"
    // toast. Now uses showStyledModal — auto-toned green via the body's
    // success keywords, non-dismissible so the user can't accidentally
    // close before the redirect fires.
    window.showStyledModal(
      'Account Created!',
      '<div style="text-align:center;font-size:3rem;color:#22c55e;margin-bottom:8px;">✓</div>' +
      '<p style="text-align:center;">Taking you to your dashboard...</p>',
      [], // no buttons — auto-redirects
      { size: 'sm', dismissible: false, tone: 'success' }
    );

    setTimeout(() => {
      window.location.href = dashboardUrl;
    }, 2000);
    
  } catch (error) {
    console.error('Signup error:', error);
    const signupBtn = document.querySelector('button[onclick*="completeSignup"]');
    if (signupBtn) { signupBtn.disabled = false; signupBtn.textContent = 'Create Account 🎉'; }
    showError('Signup failed: ' + error.message);
  }
}

function showError(message) {
  const errorDiv = document.getElementById('errorMessage');
  errorDiv.textContent = message;
  errorDiv.style.display = 'block';
  window.scrollTo(0, 0);
}

function hideError() {
  document.getElementById('errorMessage').style.display = 'none';
}
// Phone formatting
function formatPhoneNumber(input) {
  let value = input.value.replace(/\D/g, "");
  if (value.length > 10) value = value.slice(0, 10);
  if (value.length >= 6) {
    input.value = `(${value.slice(0,3)}) ${value.slice(3,6)}-${value.slice(6)}`;
  } else if (value.length >= 3) {
    input.value = `(${value.slice(0,3)}) ${value.slice(3)}`;
  } else if (value.length > 0 && value.length < 3) {
    input.value = value;
  }
}


// === Block 2 of 2 ===
// Init city autocomplete using shared module (with built-in validation)
document.addEventListener('DOMContentLoaded', function(){
  if (typeof initCityAutocomplete === 'function') {
    initCityAutocomplete({inputId:'artistCity', stateId:'artistState'});
    initCityAutocomplete({inputId:'venueCity', stateId:'venueState'});
  }
});

// Phase 2 migration: duplicate entity modal — was an inline-styled
// confirm/promise dialog. Now delegates to showStyledModal. Resolves the
// returned Promise based on which button was clicked. After a successful
// request-access POST, the dialog body is updated in place to show a
// success message and the primary button switches to "OK".
function showDuplicateModal(dupData) {
  return new Promise((resolve) => {
    const typeLabel = dupData.type === 'artist' ? 'Artist' : 'Venue';
    const safeName  = String(dupData.name || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
    const safeCity  = String(dupData.city || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
    const safeState = String(dupData.state || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);

    const bodyHtml =
      `<div class="gf-notice gf-notice--error" style="margin-bottom:14px;">` +
        `<div style="font-size:1.05rem;font-weight:700;color:var(--text);">${safeName}</div>` +
        `<div style="font-size:0.85rem;color:var(--text-gray);margin-top:4px;">${safeCity}, ${safeState}</div>` +
      `</div>` +
      `<p>This ${typeLabel.toLowerCase()} already exists in our system. Would you like to request permission to access their profile?</p>` +
      `<div id="_dupStatus" style="text-align:center;font-size:0.8rem;margin-top:14px;min-height:20px;"></div>`;

    window.showStyledModal(
      `${typeLabel} Already Exists`,
      bodyHtml,
      [
        { text: 'No, Go Back', style: 'ghost', onClick: () => resolve(false) },
        {
          text: 'Yes, Request Access', style: 'primary',
          onClick: async () => {
            const overlay = document.querySelector('.gfm-modal-overlay');
            if (!overlay) return;
            const status = overlay.querySelector('#_dupStatus');
            const footerBtns = overlay.querySelectorAll('.gfm-modal-footer .btn');
            const yesBtn = footerBtns[footerBtns.length - 1];
            yesBtn.disabled = true;
            yesBtn.textContent = 'Sending...';

            try {
              const res = await fetch('/api/request-access', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  type: dupData.type,
                  entity_id: dupData.entity_id,
                  requester_name: document.getElementById('firstName').value + ' ' + document.getElementById('lastName').value,
                  requester_email: document.getElementById('email').value
                })
              });
              if (!res.ok) throw new Error('Failed');
              if (status) {
                status.style.color = '#22c55e';
                status.textContent = '✓ Request sent! The profile owner will invite you via email.';
              }
              yesBtn.textContent = 'OK';
              yesBtn.disabled = false;
              yesBtn.onclick = () => { window.closeAllModals(); resolve(true); };
            } catch (e) {
              if (status) {
                status.style.color = '#ef4444';
                status.textContent = 'Failed to send request. Please try again.';
              }
              yesBtn.disabled = false;
              yesBtn.textContent = 'Yes, Request Access';
            }
            return false; // keep modal open during async work / for status display
          }
        },
      ],
      { onClose: () => resolve(false) }
    );
  });
}

async function checkDuplicateEntity() {
  const type = selectedRole;
  let name, city, state;
  
  if (type === 'artist') {
    name = document.getElementById('artistName').value.trim();
    city = document.getElementById('artistCity').value.trim();
    state = document.getElementById('artistState').value;
  } else {
    name = document.getElementById('venueName').value.trim();
    city = document.getElementById('venueCity').value.trim();
    state = document.getElementById('venueState').value;
  }
  
  if (!name || !city || !state) return false; // Let required field validation handle
  
  try {
    const res = await fetch('/api/check-duplicate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, name, city, state })
    });
    const data = await res.json();
    
    if (data.duplicate) {
      const userChoseRequest = await showDuplicateModal(data);
      return true; // Block signup either way (they either requested access or went back)
    }
    return false; // No duplicate, continue
  } catch (e) {
    console.error('Duplicate check failed:', e);
    return false; // On error, let them through
  }
}


// State populator
// Populate states
        document.getElementById('artistState').innerHTML += US_STATES.map(s => `<option value="${s.code}">${s.name}</option>`).join('');

// State populator
// Populate states
        document.getElementById('venueState').innerHTML += US_STATES.map(s => `<option value="${s.code}">${s.name}</option>`).join('');

// === Block 2 of 2: Venue form utilities ===

function formatZipCode(el) {
  el.value = el.value.replace(/[^0-9]/g, '').substring(0, 5);
}

function formatPayDollars(el) {
  let raw = el.value.replace(/[^0-9]/g, '');
  if (raw.length > 6) raw = raw.substring(0, 6);
  el.value = raw.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function formatPayCents(el) {
  el.value = el.value.replace(/[^0-9]/g, '').substring(0, 2);
}

function formatFrequency(el) {
  let v = parseInt(el.value, 10);
  if (isNaN(v) || v < 0) el.value = '';
  else if (v > 365) el.value = '365';
}

function formatCapacity(el) {
  let raw = el.value.replace(/[^0-9]/g, '');
  if (raw.length > 6) raw = raw.substring(0, 6);
  el.value = raw.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function toggleStageDetails() {
  const show = document.getElementById('venueHasStage').value === '1';
  document.getElementById('stageDetails').style.display = show ? 'block' : 'none';
}

function toggleSoundDetails() {
  const show = document.getElementById('venueHasSoundEquipment').value === '1';
  document.getElementById('soundDetails').style.display = show ? 'block' : 'none';
  if (!show) {
    document.getElementById('venueHasSoundEngineer').value = '0';
    toggleEngineerDetails();
  }
}

function toggleEngineerDetails() {
  const show = document.getElementById('venueHasSoundEngineer').value === '1';
  document.getElementById('engineerDetails').style.display = show ? 'block' : 'none';
}

function toggleLightingDetails() {
  const show = document.getElementById('venueHasLighting').value === '1';
  document.getElementById('lightingDetails').style.display = show ? 'block' : 'none';
}

function toggleArrivalDetails() {
  const show = document.getElementById('venueArrivalType').value === 'no_earlier_than';
  document.getElementById('arrivalDetails').style.display = show ? 'inline-flex' : 'none';
}
