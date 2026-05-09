/**
 * venue.contracts.js
 * Manages contract templates on the venue-create-gigs page (Legal/Taxes tab).
 * Supports 3 types: pdf_upload, custom_builder, auto_generated
 */

(function () {
  let venueId = null;
  let venueName = '';
  let currentContract = null;
  let allContracts = [];
  let _autoLoading = false;
  let _skipAutoOnRender = false;
  let _bound = false;

  // ============================================
  // CONTRACT NAMING CONVENTION
  // Prepends venue name: "VenueName_(ContractName)"
  // ============================================
  function formatContractName(rawName) {
    if (!rawName) rawName = 'Standard Contract';
    if (!venueName) return rawName;
    const prefix = venueName + '_';
    // Don't double-prepend if already starts with venue name
    if (rawName.startsWith(prefix) || rawName.startsWith(venueName + ' ') || rawName.startsWith(venueName + '_')) {
      return rawName;
    }
    return `${venueName}_${rawName}`;
  }

  // ============================================
  // INIT
  // ============================================
  async function init() {
    venueId = window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (!venueId) return;
    // Fetch venue name for contract naming convention
    try {
      const res = await fetch(`/api/venues/${venueId}`, { credentials: 'include' });
      if (res.ok) {
        const v = await res.json();
        venueName = v.venue_name || v.name || '';
      }
    } catch (e) {}
    loadContracts();
    loadVenueExecutedContracts();
    if (!_bound) bindEvents();
    installTabGuard();
  }

  function bindEvents() {
    _bound = true;

    const typeSelect = document.getElementById('contractType');
    if (typeSelect) typeSelect.addEventListener('change', onTypeChange);

    const reqToggle = document.getElementById('contractRequireForBooking');
    if (reqToggle) reqToggle.addEventListener('change', async () => {
      stopEnforcement();
      if (reqToggle.checked) {
        toggleContractOptions(true);
        const typeSelect = document.getElementById('contractType');
        if (!typeSelect || !typeSelect.value) {
          // No contract selected — enforce
          startEnforcement();
        } else if (currentContract) {
          saveContract();
        }
      } else {
        // Unchecked — delete existing contract and hide options
        if (currentContract) {
          try {
            await fetch(`/api/venues/${venueId}/contracts/${currentContract.id}`, {
              method: 'DELETE', credentials: 'include'
            });
          } catch (e) { console.error('Delete on uncheck:', e); }
          currentContract = null;
        }
        const typeSelect = document.getElementById('contractType');
        if (typeSelect) typeSelect.value = '';
        toggleContractOptions(false);
        showTypeSection('');
        showContractMsg('', '');
        const statusEl = document.getElementById('contractStatus');
        if (statusEl) {
          statusEl.textContent = 'No contract set up';
          statusEl.style.color = 'var(--text-gray)';
        }
      }
    });

    const saveBtn = document.getElementById('contractSaveBtn');
    if (saveBtn) saveBtn.addEventListener('click', () => saveContract());

    const pdfInput = document.getElementById('contractPdfInput');
    if (pdfInput) pdfInput.addEventListener('change', uploadPdf);

    const perGigCb = document.getElementById('contractPerGigPdfCheck');
    if (perGigCb) perGigCb.addEventListener('change', () => {
      const desc = document.getElementById('perGigPdfDesc');
      if (desc) desc.style.display = perGigCb.checked ? '' : 'none';
      if (currentContract) saveContract();
    });

    document.querySelectorAll('.contract-placeholder-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const targetId = btn.dataset.target || 'contractBuilderEditor';
        insertPlaceholder(btn.dataset.placeholder, targetId);
      });
    });
  }

  // ============================================
  // LOAD
  // ============================================
  // ============================================
  // CHECKBOX ENFORCEMENT — require contract selection
  // ============================================
  let _enforcing = false;

  function showContractWarning() {
    clearContractWarning();
    const reqRow = document.getElementById('contractRequireRow');
    if (!reqRow) return;
    const warn = document.createElement('div');
    warn.id = 'contractRequireWarning';
    warn.style.cssText = 'color:#ef4444; font-size:0.85rem; font-weight:600; margin-bottom:8px; padding:8px 12px; background:rgba(239,68,68,0.1); border:1px solid rgba(239,68,68,0.3); border-radius:8px;';
    warn.textContent = '\u26a0 You must choose a contract option below to continue.';
    reqRow.parentNode.insertBefore(warn, reqRow);
  }

  function clearContractWarning() {
    const warn = document.getElementById('contractRequireWarning');
    if (warn) warn.remove();
    const typeSelect = document.getElementById('contractType');
    if (typeSelect) typeSelect.style.border = '';
  }

  function isContractEnforcementNeeded() {
    const reqToggle = document.getElementById('contractRequireForBooking');
    const typeSelect = document.getElementById('contractType');
    return reqToggle && reqToggle.checked && (!typeSelect || !typeSelect.value);
  }

  function startEnforcement() {
    if (_enforcing) return;
    _enforcing = true;
    showContractWarning();
    const typeSelect = document.getElementById('contractType');
    if (typeSelect) {
      typeSelect.style.border = '2px solid #ef4444';
      typeSelect.focus();
    }
  }

  function stopEnforcement() {
    _enforcing = false;
    clearContractWarning();
  }

  function toggleContractOptions(show) {
    const container = document.getElementById('contractOptionsContainer');
    if (container) container.style.display = show ? '' : 'none';
  }

  // ============================================
  // TAB SWITCH INTERCEPTION
  // ============================================
  function installTabGuard() {
    if (window._contractTabGuardInstalled) return;
    window._contractTabGuardInstalled = true;

    // Block main tab switching
    const realSwitchTab = window.switchTab;
    window.switchTab = function(tabName, button) {
      if (isContractEnforcementNeeded()) {
        scrollToContractWarning();
        return; // block the switch
      }
      if (realSwitchTab) realSwitchTab(tabName, button);
    };

    // Block legal sub-tab switching (Executed Contracts, Tax Settings)
    const realSwitchLegalSubtab = window.switchLegalSubtab;
    window.switchLegalSubtab = function(name, btn) {
      if (isContractEnforcementNeeded()) {
        scrollToContractWarning();
        return; // block the switch
      }
      if (realSwitchLegalSubtab) realSwitchLegalSubtab(name, btn);
    };

    // Also intercept link clicks that navigate away
    document.addEventListener('click', function(e) {
      if (!isContractEnforcementNeeded()) return;
      const link = e.target.closest('a[href]');
      if (link && link.href && !link.href.startsWith('#') && !link.href.startsWith('javascript')) {
        e.preventDefault();
        e.stopPropagation();
        scrollToContractWarning();
      }
    }, true);
  }

  function scrollToContractWarning() {
    showContractWarning();
    const typeSelect = document.getElementById('contractType');
    if (typeSelect) {
      typeSelect.style.border = '2px solid #ef4444';
      typeSelect.focus();
      typeSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  // ============================================
  // LOAD / RENDER
  // ============================================
  async function loadContracts() {
    try {
      const res = await fetch(`/api/venues/${venueId}/contracts`, { credentials: 'include' });
      if (!res.ok) return;
      allContracts = await res.json();
      currentContract = allContracts.find(c => c.is_active) || null;
      renderContractState();
    } catch (e) {
      console.error('Load contracts:', e);
    }
  }

  // ============================================
  // RENDER
  // ============================================
  function renderContractState() {
    const typeSelect = document.getElementById('contractType');
    const reqToggle = document.getElementById('contractRequireForBooking');
    const builderSection = document.getElementById('contractBuilderSection');
    const autoSection = document.getElementById('contractAutoSection');
    const statusEl = document.getElementById('contractStatus');
    const pdfUploadRow = document.getElementById('contractPdfUploadRow');
    const pdfFileRow = document.getElementById('contractPdfFileRow');

    // Hide all type sections
    if (builderSection) builderSection.style.display = 'none';
    if (autoSection) autoSection.style.display = 'none';
    if (pdfUploadRow) pdfUploadRow.style.display = 'none';
    if (pdfFileRow) pdfFileRow.style.display = 'none';

    if (currentContract) {
      if (typeSelect) typeSelect.value = currentContract.contract_type;
      if (reqToggle) reqToggle.checked = !!currentContract.require_for_booking;
      // Show options container if contract exists (checkbox is checked)
      toggleContractOptions(true);
      
      // Per-gig PDF checkbox
      const perGigCb = document.getElementById('contractPerGigPdfCheck');
      const perGigDesc = document.getElementById('perGigPdfDesc');
      if (perGigCb) {
        perGigCb.checked = !!currentContract.per_gig_pdf;
        if (perGigDesc) perGigDesc.style.display = perGigCb.checked ? '' : 'none';
      }

      showTypeSection(currentContract.contract_type);

      if (currentContract.contract_type === 'pdf_upload') {
        renderPdfState();
      } else if (currentContract.contract_type === 'custom_builder') {
        const editor = document.getElementById('contractBuilderEditor');
        if (editor) editor.value = currentContract.contract_body || '';
        renderBuilderFieldsList();
      } else if (currentContract.contract_type === 'auto_generated') {
        const editor = document.getElementById('contractAutoEditor');
        if (editor) {
          if (currentContract.contract_body && currentContract.contract_body.trim().length > 100) {
            editor.value = currentContract.contract_body;
          } else if (!_skipAutoOnRender) {
            loadAutoText();
          }
        }
      }

      if (statusEl) {
        statusEl.textContent = '\u2713 Contract active';
        statusEl.style.color = '#22c55e';
      }
    } else {
      if (typeSelect) typeSelect.value = '';
      if (reqToggle) reqToggle.checked = false;
      // Hide options container when no contract
      toggleContractOptions(false);
      if (statusEl) {
        statusEl.textContent = 'No contract set up';
        statusEl.style.color = 'var(--text-gray)';
      }
    }
  }

  function showTypeSection(type) {
    const builderSection = document.getElementById('contractBuilderSection');
    const autoSection = document.getElementById('contractAutoSection');
    const saveBtn = document.getElementById('contractSaveBtn');
    const viewBtn = document.getElementById('contractViewBtn');
    const pdfUploadRow = document.getElementById('contractPdfUploadRow');
    const pdfFileRow = document.getElementById('contractPdfFileRow');
    const perGigRow = document.getElementById('contractPerGigPdf');

    if (builderSection) builderSection.style.display = 'none';
    if (autoSection) autoSection.style.display = 'none';
    if (pdfUploadRow) pdfUploadRow.style.display = 'none';
    if (pdfFileRow) pdfFileRow.style.display = 'none';
    if (perGigRow) perGigRow.style.display = 'none';

    // Save + View buttons: show for builder and auto, hide for pdf and none
    const showBtns = (type === 'custom_builder' || type === 'auto_generated');
    if (saveBtn) saveBtn.style.display = showBtns ? '' : 'none';
    if (viewBtn) viewBtn.style.display = showBtns ? '' : 'none';

    if (type === 'pdf_upload') {
      // Show per-gig PDF option
      if (perGigRow) perGigRow.style.display = '';
      // If PDF already uploaded, show file row (with Replace button); hide upload button
      // If no PDF yet, show upload button
      const hasPdf = currentContract && currentContract.pdf_file_path;
      if (hasPdf) {
        if (pdfFileRow) pdfFileRow.style.display = '';
        if (pdfUploadRow) pdfUploadRow.style.display = 'none';
      } else {
        if (pdfUploadRow) pdfUploadRow.style.display = '';
      }
    }
    if (type === 'custom_builder' && builderSection) builderSection.style.display = '';
    if (type === 'auto_generated' && autoSection) autoSection.style.display = '';
  }

  function renderPdfState() {
    const pdfStatus = document.getElementById('contractPdfStatus');
    const pdfFileRow = document.getElementById('contractPdfFileRow');
    const pdfUploadRow = document.getElementById('contractPdfUploadRow');
    if (!pdfStatus) return;

    if (currentContract && currentContract.pdf_file_path) {
      if (pdfFileRow) pdfFileRow.style.display = '';
      if (pdfUploadRow) pdfUploadRow.style.display = 'none';
      const fullPath = currentContract.pdf_file_path;
      const displayName = currentContract.name || fullPath.split('/').pop() || 'contract.pdf';
      const uploadDate = currentContract.updated_at
        ? new Date(currentContract.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
        : '';

      pdfStatus.innerHTML = `
        <div style="display:flex; align-items:center; gap:16px; padding:10px 14px; background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:8px; flex-wrap:wrap;">
          <a href="${fullPath}" target="_blank" style="color:var(--cyan); font-size:0.85rem; text-decoration:none; border-bottom:1px solid rgba(6,182,212,0.3); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:280px;" title="${displayName}">\u{1F4C4} ${displayName}</a>
          <span style="font-size:0.75rem; color:var(--text-gray); white-space:nowrap;">${uploadDate}</span>
          <div style="display:flex; gap:8px; margin-left:auto;">
            <button class="btn ghost" onclick="window.venueContracts.openRename()" style="padding:4px 12px; font-size:0.75rem;">Rename</button>
            <button class="btn ghost" onclick="document.getElementById('contractPdfInput').click()" style="padding:4px 12px; font-size:0.75rem;">Replace</button>
            <button class="btn ghost" onclick="window.venueContracts.removePdf()" style="padding:4px 12px; font-size:0.75rem; color:#ef4444; border-color:rgba(239,68,68,0.3);">Remove</button>
          </div>
        </div>`;
    } else {
      if (pdfFileRow) pdfFileRow.style.display = 'none';
      if (pdfUploadRow) pdfUploadRow.style.display = '';
      pdfStatus.innerHTML = '';
    }
  }

  function renderBuilderFieldsList() {
    const el = document.getElementById('contractBuilderFieldsList');
    if (!el) return;
    let fields = [];
    try {
      fields = currentContract && currentContract.custom_fields ? JSON.parse(currentContract.custom_fields) : [];
    } catch (e) { fields = []; }
    if (fields.length === 0) {
      el.innerHTML = '<p style="font-size:0.8rem; color:var(--text-gray);">Use the placeholder buttons above to insert fillable fields into your contract text.</p>';
    } else {
      el.innerHTML = `<p style="font-size:0.8rem; color:var(--text-gray);">Custom fields: ${fields.map(f => `<code style="background:#1e293b; padding:2px 6px; border-radius:4px; font-size:0.75rem;">{{${esc(f)}}}</code>`).join(', ')}</p>`;
    }
  }

  // ============================================
  // EVENTS
  // ============================================
  async function onTypeChange() {
    const typeSelect = document.getElementById('contractType');
    const type = typeSelect ? typeSelect.value : '';

    // Clear enforcement warning when a type is chosen
    if (type) {
      stopEnforcement();
    }

    if (!type) {
      if (currentContract) await deleteContract(true);
      showTypeSection('');
      // If checkbox is still checked, re-enforce
      const reqToggle = document.getElementById('contractRequireForBooking');
      if (reqToggle && reqToggle.checked) startEnforcement();
      return;
    }

    // Create or update the contract record
    if (currentContract && currentContract.contract_type !== type) {
      currentContract.contract_type = type;
      if (type === 'auto_generated') {
        currentContract.contract_body = '';
      }
      await saveContract(true);
    } else if (!currentContract) {
      await saveContract(true);
    }

    // Refresh from server — skip auto-fetch since we handle it explicitly below
    _skipAutoOnRender = true;
    await loadContracts();
    _skipAutoOnRender = false;

    showTypeSection(type);

    // For auto-generated, ALWAYS fetch fresh text from server
    if (type === 'auto_generated') {
      _autoLoading = false; // reset flag in case renderContractState set it
      await loadAutoText();
    }

    // For pdf_upload with no existing PDF, immediately open file browser
    if (type === 'pdf_upload') {
      const hasPdf = currentContract && currentContract.pdf_file_path;
      if (!hasPdf) {
        const pdfInput = document.getElementById('contractPdfInput');
        if (pdfInput) {
          pdfInput.value = '';
          let fileSelected = false;

          // Listen for file selection FIRST
          function onFileChosen() {
            fileSelected = true;
            pdfInput.removeEventListener('change', onFileChosen);
          }
          pdfInput.addEventListener('change', onFileChosen);

          // Small delay to let DOM settle, then trigger file dialog
          setTimeout(() => {
            pdfInput.click();
            // Detect cancel: when window regains focus and no file was selected
            function onFocusBack() {
              window.removeEventListener('focus', onFocusBack);
              setTimeout(() => {
                if (!fileSelected && (!pdfInput.files || !pdfInput.files.length)) {
                  // User cancelled — revert to "None"
                  pdfInput.removeEventListener('change', onFileChosen);
                  const typeSelect = document.getElementById('contractType');
                  if (typeSelect) typeSelect.value = '';
                  if (currentContract) deleteContract(true);
                  showTypeSection('');
                }
              }, 500);
            }
            window.addEventListener('focus', onFocusBack);
          }, 200);
        }
      }
    }
  }

  function insertPlaceholder(placeholder, targetId) {
    const editor = document.getElementById(targetId);
    if (!editor) return;
    const tag = `{{${placeholder}}}`;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const text = editor.value;
    editor.value = text.substring(0, start) + tag + text.substring(end);
    editor.selectionStart = editor.selectionEnd = start + tag.length;
    editor.focus();
  }

  // ============================================
  // SAVE
  // ============================================
  async function saveContract(skipReload) {
    const typeSelect = document.getElementById('contractType');
    const type = typeSelect ? typeSelect.value : '';
    if (!type) return;

    const reqToggle = document.getElementById('contractRequireForBooking');
    const perGigCb = document.getElementById('contractPerGigPdfCheck');

    const payload = {
      contract_type: type,
      name: (currentContract && currentContract.name) ? currentContract.name : formatContractName('Standard Contract'),
      require_for_booking: reqToggle ? reqToggle.checked : false,
      per_gig_pdf: (perGigCb && type === 'pdf_upload') ? perGigCb.checked : false,
    };

    if (type === 'custom_builder') {
      const editor = document.getElementById('contractBuilderEditor');
      payload.contract_body = editor ? editor.value : '';
      const matches = payload.contract_body.match(/\{\{(\w+)\}\}/g) || [];
      const standardFields = ['artist_name', 'artist_email', 'artist_phone', 'artist_city', 'artist_state',
                              'artist_contact_name', 'venue_name', 'venue_address', 'venue_city', 'venue_state',
                              'gig_date', 'gig_start_time', 'gig_end_time', 'gig_pay', 'gig_title'];
      payload.custom_fields = [...new Set(matches.map(m => m.replace(/[{}]/g, '')).filter(f => !standardFields.includes(f)))];
    } else if (type === 'auto_generated') {
      const editor = document.getElementById('contractAutoEditor');
      payload.contract_body = editor ? editor.value : '';
    }

    showContractMsg('Saving...', 'var(--text-gray)');

    try {
      let res;
      if (currentContract && currentContract.id) {
        res = await fetch(`/api/venues/${venueId}/contracts/${currentContract.id}`, {
          method: 'PUT', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      } else {
        res = await fetch(`/api/venues/${venueId}/contracts`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Save failed');
      }

      const data = await res.json();
      showContractMsg('\u2713 Saved', '#22c55e');

      if (!currentContract || !currentContract.id) {
        currentContract = { id: data.contract_id || data.id, contract_type: type, ...payload };
      } else {
        // Update local state
        Object.assign(currentContract, payload);
      }

      // Refresh from server unless caller says skip
      if (!skipReload) {
        try {
          const res2 = await fetch(`/api/venues/${venueId}/contracts`, { credentials: 'include' });
          if (res2.ok) {
            allContracts = await res2.json();
            currentContract = allContracts.find(c => c.is_active) || currentContract;
          }
        } catch (e) {}
      }
    } catch (e) {
      console.error('Save contract:', e);
      showContractMsg('\u2717 ' + e.message, '#ef4444');
    }
  }

  // ============================================
  // DELETE
  // ============================================
  async function deleteContract(silent) {
    if (!currentContract) return;
    if (!silent && !confirm('Remove this contract template? Artists will no longer see a contract when booking.')) return;

    try {
      const res = await fetch(`/api/venues/${venueId}/contracts/${currentContract.id}`, {
        method: 'DELETE', credentials: 'include'
      });
      if (!res.ok) throw new Error('Delete failed');
      currentContract = null;
      _autoLoading = false;
      if (!silent) showContractMsg('Contract removed', '#22c55e');
      await loadContracts();
    } catch (e) {
      console.error('Delete contract:', e);
      if (!silent) showContractMsg('\u2717 Delete failed', '#ef4444');
    }
  }

  // ============================================
  // REMOVE CONTRACT — styled modal
  // ============================================
  function removePdf() {
    if (!currentContract) return;
    const overlay = document.getElementById('contractRemoveOverlay');
    if (overlay) overlay.style.display = 'flex';
  }

  function closeRemove() {
    const overlay = document.getElementById('contractRemoveOverlay');
    if (overlay) overlay.style.display = 'none';
  }

  async function confirmRemove() {
    closeRemove();
    if (!currentContract) return;

    showContractMsg('Removing...', 'var(--text-gray)');
    try {
      const res = await fetch(`/api/venues/${venueId}/contracts/${currentContract.id}`, {
        method: 'DELETE', credentials: 'include'
      });
      if (!res.ok) throw new Error('Remove failed');
      currentContract = null;
      // Reset UI to "None" state
      const typeSelect = document.getElementById('contractType');
      if (typeSelect) typeSelect.value = '';
      const reqToggle = document.getElementById('contractRequireForBooking');
      if (reqToggle) reqToggle.checked = false;
      toggleContractOptions(false);
      showTypeSection('');
      showContractMsg('✓ Contract removed', '#22c55e');
      await loadContracts();
    } catch (e) {
      console.error('Remove PDF:', e);
      showContractMsg('✗ Remove failed', '#ef4444');
    }
  }

  // ============================================
  // PDF UPLOAD
  // ============================================
  async function uploadPdf() {
    const input = document.getElementById('contractPdfInput');
    if (!input || !input.files.length) return;

    const file = input.files[0];
    const rawFileName = file.name.replace(/\.pdf$/i, '');
    const contractName = formatContractName(rawFileName);

    if (!currentContract || !currentContract.id) {
      const reqToggle = document.getElementById('contractRequireForBooking');
      const payload = {
        contract_type: 'pdf_upload',
        name: contractName,
        require_for_booking: reqToggle ? reqToggle.checked : false,
      };
      showContractMsg('Creating contract...', 'var(--text-gray)');
      try {
        const createRes = await fetch(`/api/venues/${venueId}/contracts`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!createRes.ok) throw new Error('Create failed');
        const data = await createRes.json();
        currentContract = { id: data.contract_id || data.id, contract_type: 'pdf_upload', name: contractName };
      } catch (e) {
        showContractMsg('\u2717 ' + e.message, '#ef4444');
        input.value = '';
        return;
      }
    } else {
      try {
        await fetch(`/api/venues/${venueId}/contracts/${currentContract.id}`, {
          method: 'PUT', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: contractName })
        });
      } catch (e) {}
    }

    const formData = new FormData();
    formData.append('file', file);

    showContractMsg('Uploading PDF...', 'var(--text-gray)');

    try {
      const res = await fetch(`/api/venues/${venueId}/contracts/${currentContract.id}/upload-pdf`, {
        method: 'POST', credentials: 'include', body: formData
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Upload failed');
      }
      showContractMsg('\u2713 PDF uploaded', '#22c55e');
      input.value = '';
      await loadContracts();
    } catch (e) {
      console.error('Upload PDF:', e);
      showContractMsg('\u2717 ' + e.message, '#ef4444');
      input.value = '';
    }
  }

  // ============================================
  // AUTO-GENERATED — Fetch full contract text from server
  // ============================================
  async function loadAutoText() {
    if (_autoLoading) return;
    _autoLoading = true;

    const editor = document.getElementById('contractAutoEditor');
    if (!editor) { _autoLoading = false; return; }

    editor.value = 'Generating contract from your venue profile...';
    editor.disabled = true;

    try {
      const res = await fetch(`/api/venues/${venueId}/contracts/auto-preview`, { credentials: 'include' });
      if (!res.ok) throw new Error('Failed to generate contract');
      const data = await res.json();
      const text = data.contract_text || '';
      editor.value = text;
      editor.disabled = false;

      // Auto-save the generated text so it persists
      if (currentContract && currentContract.id && text) {
        try {
          await fetch(`/api/venues/${venueId}/contracts/${currentContract.id}`, {
            method: 'PUT', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ contract_body: text })
          });
          currentContract.contract_body = text;
          showContractMsg('\u2713 Contract generated & saved', '#22c55e');
        } catch (e) {}
      }
    } catch (e) {
      editor.value = 'Error generating contract: ' + e.message + '\n\nPlease ensure your venue profile is set up, then try selecting Auto-Generated again.';
      editor.disabled = false;
    }
    _autoLoading = false;
  }

  // ============================================
  // RESET TO DEFAULT — use styled modal
  // ============================================
  function resetToDefault() {
    const overlay = document.getElementById('contractRestoreOverlay');
    if (overlay) overlay.style.display = 'flex';
  }

  function closeRestore() {
    const overlay = document.getElementById('contractRestoreOverlay');
    if (overlay) overlay.style.display = 'none';
  }

  async function confirmRestore() {
    closeRestore();
    _autoLoading = false;
    await loadAutoText();
  }

  // ============================================
  // VIEW CONTRACT MODAL
  // ============================================
  function viewContract() {
    const overlay = document.getElementById('contractViewOverlay');
    const body = document.getElementById('contractViewBody');
    if (!overlay || !body) { console.error('View modal elements not found'); return; }

    let text = '';
    // Use dropdown type as fallback if currentContract not set
    const typeSelect = document.getElementById('contractType');
    const type = (currentContract && currentContract.contract_type) || (typeSelect ? typeSelect.value : '');

    if (type === 'auto_generated') {
      const editor = document.getElementById('contractAutoEditor');
      text = editor ? editor.value : '';
    } else if (type === 'custom_builder') {
      const editor = document.getElementById('contractBuilderEditor');
      text = editor ? editor.value : '';
    }

    if (!text.trim()) {
      text = '(No contract text to preview. Write or generate your contract first.)';
    }

    // Render with placeholders highlighted
    const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const highlighted = escaped.replace(/\{\{(\w+)\}\}/g, '<span style="background:rgba(139,92,246,0.25); color:#a78bfa; padding:1px 4px; border-radius:3px;">{{$1}}</span>');
    body.innerHTML = highlighted;
    overlay.style.display = 'flex';
  }

  function closeView() {
    const overlay = document.getElementById('contractViewOverlay');
    if (overlay) overlay.style.display = 'none';
  }

  // ============================================
  // EXECUTED CONTRACTS LIST (venue side)
  // ============================================
  let _venueExecContracts = [];
  let _venueExecPage = 1;
  const _execPerPage = 20;
  let _venueExecFilter = 'all'; // 'all' = show everything, 'pending' = upcoming, 'completed' = past
  let _venueExecSort = { col: 'gig_date', dir: 1 }; // 1 = asc (soonest first), -1 = desc
  function setExecFilter(value) {
    _venueExecFilter = value || 'all';
    _venueExecPage = 1;
    renderVenueExecutedContracts();
  }
  async function loadVenueExecutedContracts() {
    const listEl = document.getElementById('venueExecutedContractsList');
    if (!listEl) return;

    const vid = venueId || window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (!vid) { listEl.innerHTML = '<p style="color:var(--text-muted); font-size:0.85rem;">No venue ID.</p>'; return; }

    try {
      const res = await fetch(`/api/venues/${vid}/gig-contracts`, { credentials: 'include' });
      if (!res.ok) throw new Error('Failed');
      _venueExecContracts = await res.json();
      _venueExecPage = 1;
      renderVenueExecutedContracts();
    } catch (e) {
      listEl.innerHTML = '<p style="color:var(--text-muted); font-size:0.85rem;">Unable to load executed contracts.</p>';
    }
  }

  function execSortBy(col) {
    if (_venueExecSort.col === col) _venueExecSort.dir *= -1;
    else { _venueExecSort.col = col; _venueExecSort.dir = col === 'gig_date' ? 1 : 1; }
    _venueExecPage = 1;
    renderVenueExecutedContracts();
  }

  function renderVenueExecutedContracts() {
    const listEl = document.getElementById('venueExecutedContractsList');
    const pagEl = document.getElementById('venueExecutedContractsPagination');
    if (!listEl) return;
    const filterEl = document.getElementById('venueExecContractsFilter');
    if (filterEl) filterEl.value = _venueExecFilter;

    const today = new Date().toISOString().slice(0, 10);
    let filtered = _venueExecContracts.filter(c => {
      const d = (c.gig_date || '').slice(0, 10);
      if (_venueExecFilter === 'pending') return d >= today;
      if (_venueExecFilter === 'completed') return d < today;
      return true;
    });
    const sortCol = _venueExecSort.col;
    const sortDir = _venueExecSort.dir;
    filtered.sort((a, b) => {
      let va = a[sortCol]; let vb = b[sortCol];
      if (sortCol === 'gig_date') { va = va || ''; vb = vb || ''; return sortDir * (va.localeCompare(vb)); }
      if (sortCol === 'display_name') {
        const an = (a.venue_name || '') + (a.artist_name || '') + (a.gig_date || '');
        const bn = (b.venue_name || '') + (b.artist_name || '') + (b.gig_date || '');
        return sortDir * (an.localeCompare(bn));
      }
      if (sortCol === 'artist_name') { va = (a.artist_name || ''); vb = (b.artist_name || ''); return sortDir * (va.localeCompare(vb)); }
      if (sortCol === 'status') { va = (a.status || ''); vb = (b.status || ''); return sortDir * (va.localeCompare(vb)); }
      return 0;
    });

    if (filtered.length === 0) {
      listEl.innerHTML = '<p style="color:var(--text-muted); font-size:0.85rem;">No contracts found.</p>';
      if (pagEl) pagEl.style.display = 'none';
      return;
    }

    const totalPages = Math.ceil(filtered.length / _execPerPage);
    const start = (_venueExecPage - 1) * _execPerPage;
    const pageItems = filtered.slice(start, start + _execPerPage);

    const arrow = (col) => _venueExecSort.col === col ? (_venueExecSort.dir === 1 ? ' ↑' : ' ↓') : '';
    const hdrStyle = 'padding:10px 12px; font-weight:600; font-size:0.8rem; color:var(--text-gray); cursor:pointer; user-select:none; border-bottom:1px solid var(--border);';
    let tableHtml = `
      <table style="width:100%; border-collapse:collapse;">
        <thead>
          <tr>
            <th style="${hdrStyle} text-align:left;" onclick="window.venueContracts.execSortBy('display_name')">Contract Name${arrow('display_name')}</th>
            <th style="${hdrStyle} text-align:left;" onclick="window.venueContracts.execSortBy('artist_name')">Artist Name${arrow('artist_name')}</th>
            <th style="${hdrStyle} text-align:left;" onclick="window.venueContracts.execSortBy('gig_date')">Date${arrow('gig_date')}</th>
            <th style="${hdrStyle} text-align:left;" onclick="window.venueContracts.execSortBy('status')">Status${arrow('status')}</th>
            <th style="${hdrStyle} text-align:right;">Download</th>
          </tr>
        </thead>
        <tbody>`;

    pageItems.forEach(c => {
      const date = c.gig_date || '';
      let fmtDate = '';
      if (date) {
        const parts = date.split('-');
        if (parts.length === 3) {
          const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
          fmtDate = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        } else {
          fmtDate = new Date(date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        }
      }
      const artistName = c.artist_name || 'Unknown Artist';
      const gigTitle = c.gig_title || 'Gig';
      const venueName = c.venue_name || 'Venue';
      let displayName = '';
      if (date && venueName && artistName) {
        const parts = date.split('-');
        const dateStr = parts.length === 3 ? parts[0] + '_' + parts[1] + '_' + parts[2] : '';
        const venueSafe = venueName.replace(/\s+/g, '').replace(/[^a-zA-Z0-9]/g, '').substring(0, 60);
        const artistSafe = artistName.replace(/\s+/g, '').replace(/[^a-zA-Z0-9]/g, '').substring(0, 60);
        if (dateStr && venueSafe && artistSafe) displayName = dateStr + '_' + venueSafe + '_' + artistSafe;
      }
      if (!displayName) displayName = c.template_name || (artistName + ' — ' + gigTitle);
      if (c.status === 'cancelled' && !displayName.includes('_CANCELLED')) displayName += '_CANCELLED';

      let statusBadge = '';
      if (c.status === 'fully_signed') {
        statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(34,197,94,0.15); color:#22c55e; white-space:nowrap;">Fully Signed</span>';
      } else if (c.status === 'artist_signed') {
        statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(234,179,8,0.15); color:#eab308; white-space:nowrap;">Awaiting Countersign</span>';
      } else if (c.status === 'venue_signed') {
        statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(234,179,8,0.15); color:#eab308; white-space:nowrap;">Awaiting Artist</span>';
      } else if (c.status === 'cancelled') {
        statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(239,68,68,0.15); color:#ef4444; white-space:nowrap;">Cancelled</span>';
      } else if (c.status === 'expired') {
        statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(107,114,128,0.15); color:#9ca3af; white-space:nowrap;">Expired</span>';
      } else {
        statusBadge = '<span style="font-size:0.7rem; padding:2px 8px; border-radius:4px; background:rgba(239,68,68,0.15); color:#ef4444; white-space:nowrap;">Pending</span>';
      }

      let downloadBtn = '';
      if (c.signed_pdf_path) {
        downloadBtn = `<a href="${c.signed_pdf_path}" download style="color:var(--cyan); font-size:0.75rem;">Download</a>`;
      } else if (c.pdf_file_path) {
        downloadBtn = `<a href="${c.pdf_file_path}" download style="color:var(--cyan); font-size:0.75rem;">Download</a>`;
      } else {
        downloadBtn = `<a href="#" onclick="window.venueContracts.downloadDigitalContract(${c.id});return false;" style="color:var(--cyan); font-size:0.75rem; cursor:pointer;">Download</a>`;
      }

      tableHtml += `
        <tr style="border-bottom:1px solid var(--border);">
          <td style="padding:10px 12px; font-size:0.85rem; color:var(--text-primary); overflow:hidden; text-overflow:ellipsis; max-width:280px;" title="${displayName}">${displayName}</td>
          <td style="padding:10px 12px; font-size:0.85rem; color:var(--text-gray);">${c.artist_id ? `<a href="/app/artist-profile.html?artist_id=${c.artist_id}" target="_blank" style="color:var(--accent-cyan,#06b6d4); text-decoration:none;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'">${artistName}</a>` : artistName}</td>
          <td style="padding:10px 12px; font-size:0.85rem; color:var(--text-gray); white-space:nowrap;">${fmtDate}</td>
          <td style="padding:10px 12px;">${statusBadge}</td>
          <td style="padding:10px 12px; text-align:right;">${downloadBtn}</td>
        </tr>`;
    });

    tableHtml += '</tbody></table>';
    listEl.innerHTML = tableHtml;

    if (pagEl) {
      if (totalPages <= 1) {
        pagEl.style.display = 'none';
      } else {
        pagEl.style.display = 'flex';
        let pagHtml = '';
        pagHtml += `<button class="btn ghost" onclick="window.venueContracts.execGoPage(${_venueExecPage - 1})" style="padding:4px 10px; font-size:0.75rem;" ${_venueExecPage <= 1 ? 'disabled' : ''}>← Prev</button>`;
        pagHtml += `<span style="font-size:0.8rem; color:var(--text-gray);">Page ${_venueExecPage} of ${totalPages}</span>`;
        pagHtml += `<button class="btn ghost" onclick="window.venueContracts.execGoPage(${_venueExecPage + 1})" style="padding:4px 10px; font-size:0.75rem;" ${_venueExecPage >= totalPages ? 'disabled' : ''}>Next →</button>`;
        pagEl.innerHTML = pagHtml;
      }
    }
  }

  function execGoPage(page) {
    const totalPages = Math.ceil(_venueExecContracts.length / _execPerPage);
    if (page < 1 || page > totalPages) return;
    _venueExecPage = page;
    renderVenueExecutedContracts();
  }

  // ============================================
  // RENAME MODAL
  // ============================================
  function openRename() {
    const overlay = document.getElementById('contractRenameOverlay');
    const input = document.getElementById('contractRenameInput');
    if (!overlay || !input) return;
    input.value = (currentContract && currentContract.name) || '';
    overlay.style.display = 'flex';
    setTimeout(() => input.focus(), 50);
  }

  function closeRename() {
    const overlay = document.getElementById('contractRenameOverlay');
    if (overlay) overlay.style.display = 'none';
  }

  async function saveRename() {
    if (!currentContract) return;
    const input = document.getElementById('contractRenameInput');
    const rawName = input ? input.value.trim() : '';
    if (!rawName) return;
    const newName = formatContractName(rawName);

    showContractMsg('Renaming...', 'var(--text-gray)');
    try {
      const res = await fetch(`/api/venues/${venueId}/contracts/${currentContract.id}`, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName })
      });
      if (!res.ok) throw new Error('Rename failed');
      showContractMsg('\u2713 Renamed', '#22c55e');
      closeRename();
      await loadContracts();
    } catch (e) {
      showContractMsg('\u2717 ' + e.message, '#ef4444');
    }
  }

  // ============================================
  // HELPERS
  // ============================================
  function showContractMsg(msg, color) {
    const el = document.getElementById('contractSaveStatus');
    if (!el) return;
    el.textContent = msg;
    el.style.color = color || '#22c55e';
    el.style.opacity = '1';
    if (msg.startsWith('\u2713')) {
      setTimeout(() => { el.style.opacity = '0'; }, 2500);
    }
  }

  // ============================================
  // INIT ON DOM READY
  // ============================================
  const wait = setInterval(() => {
    if (window.venueId || new URLSearchParams(window.location.search).get('venue_id')) {
      clearInterval(wait);
      init();
    }
  }, 200);
  setTimeout(() => clearInterval(wait), 8000);

  async function downloadDigitalContract(contractId) {
    try {
      const res = await fetch(`/api/gig-contracts/${contractId}/download-pdf`, { credentials: 'include' });
      if (!res.ok) {
        let msg = 'Download failed';
        try { const j = await res.json(); msg = j.detail || msg; } catch(e) { msg = `Server error (${res.status})`; }
        alert(msg);
        return;
      }
      const blob = await res.blob();
      const cd = res.headers.get('content-disposition') || '';
      const fnMatch = cd.match(/filename="?([^"]+)"?/);
      const filename = fnMatch ? fnMatch[1] : `Contract_${contractId}.pdf`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert('Failed to download contract: ' + e.message);
    }
  }

  window.venueContracts = {
    save: saveContract,
    delete: deleteContract,
    removePdf: removePdf,
    closeRemove: closeRemove,
    confirmRemove: confirmRemove,
    openRename: openRename,
    closeRename: closeRename,
    saveRename: saveRename,
    insertPlaceholder: insertPlaceholder,
    resetToDefault: resetToDefault,
    closeRestore: closeRestore,
    confirmRestore: confirmRestore,
    viewContract: viewContract,
    closeView: closeView,
    execGoPage: execGoPage,
    setExecFilter: setExecFilter,
    execSortBy: execSortBy,
    loadExecuted: loadVenueExecutedContracts,
    downloadDigitalContract: downloadDigitalContract,
    _reinit: async function () {
      venueId = window.venueId || new URLSearchParams(window.location.search).get('venue_id');
      if (venueId) {
        if (!venueName) {
          try {
            const res = await fetch(`/api/venues/${venueId}`, { credentials: 'include' });
            if (res.ok) { const v = await res.json(); venueName = v.venue_name || v.name || ''; }
          } catch (e) {}
        }
        loadContracts();
        loadVenueExecutedContracts();
        if (!_bound) bindEvents();
      }
    }
  };
})();