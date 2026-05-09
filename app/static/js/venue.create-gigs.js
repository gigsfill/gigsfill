document.addEventListener("DOMContentLoaded", async () => {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get("venue_id");
  if (!venueId) {
    document.body.innerHTML = '<div style="padding: 40px; text-align: center;">Missing venue_id parameter</div>';
    return;
  }

  const calendarEl = document.getElementById("calendar");
  const monthLabel = document.getElementById("monthLabel");
  const prevBtn = document.getElementById("prevMonth");
  const nextBtn = document.getElementById("nextMonth");
  
  // Second calendar for search tab
  const calendarEl2 = document.getElementById("calendar2");
  const monthLabel2 = document.getElementById("monthLabel2");
  const prevBtn2 = document.getElementById("prevMonth2");
  const nextBtn2 = document.getElementById("nextMonth2");

  const modal = document.getElementById("gigModal");
  const saveBtn = document.getElementById("saveGig");
  const cancelBtn = document.getElementById("cancelGig");
  let _recurringSnapshot = null; // snapshot of recurring UI state on modal open
  const deleteBtn = document.getElementById("deleteGig");

  const gigDateInput = document.getElementById("gigDate");
  const titleInput = document.getElementById("gigTitle");
  const startInput = document.getElementById("gigStart") || { value: "" };
  const endInput = document.getElementById("gigEnd") || { value: "" };
  const payDollarsInput = document.getElementById("default_pay_dollars") || { value: "" };
  const payCentsInput = document.getElementById("default_pay_cents") || { value: "" };

  const notesInput = document.getElementById("gigNotes");
  
  // v93: New inputs for artist type and recurring
  const artistTypeInput = document.getElementById("gigArtistType");
  const bandFormatOptions = document.getElementById("bandFormatOptions");
  const styleOptions = document.getElementById("styleOptions");
  const recurringCheckbox = document.getElementById("recurringGig");
  const recurringOptions = document.getElementById("recurringOptions");
  
  // Format number with commas
  function formatWithCommas(value) {
    // Remove all non-digits
    const digits = value.replace(/\D/g, '');
    // Add commas
    return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }
  
  // Format date for display (YYYY-MM-DD to readable format)
  function formatDateForDisplay(dateStr) {
    if (!dateStr) return '';
    const [year, month, day] = dateStr.split('-');
    const date = new Date(parseInt(year), parseInt(month) - 1, parseInt(day));
    const options = { weekday: 'long', year: 'numeric', month: 'short', day: 'numeric' };
    return date.toLocaleDateString('en-US', options);
  }

  // === RECURRING END TYPE CONTROL ===
  const endTypeCheckboxes = document.querySelectorAll('input[name="endType"]');
  const endAfterInput = document.getElementById('endAfter');
  const endByInput = document.getElementById('endBy');

  function _captureRecurringSnapshot() {
    _recurringSnapshot = {
      recurWeeks: document.getElementById('recurWeeks')?.value || '1',
      checked: recurringCheckbox ? recurringCheckbox.checked : false,
      optionsDisplay: recurringOptions ? recurringOptions.style.display : 'none',
      daySun: document.getElementById('daySun')?.checked || false,
      dayMon: document.getElementById('dayMon')?.checked || false,
      dayTue: document.getElementById('dayTue')?.checked || false,
      dayWed: document.getElementById('dayWed')?.checked || false,
      dayThu: document.getElementById('dayThu')?.checked || false,
      dayFri: document.getElementById('dayFri')?.checked || false,
      daySat: document.getElementById('daySat')?.checked || false,
      endType: document.querySelector('input[name="endType"]:checked')?.value || 'after',
      endAfter: endAfterInput ? endAfterInput.value : '',
      endBy: endByInput ? endByInput.value : '',
      pendingDetach: selectedGig ? !!selectedGig._pendingDetach : false,
    };
  }

  function _restoreRecurringSnapshot() {
    if (!_recurringSnapshot) return;
    const s = _recurringSnapshot;
    const rw = document.getElementById('recurWeeks');
    if (rw) rw.value = s.recurWeeks;
    if (recurringCheckbox) { recurringCheckbox.checked = s.checked; }
    if (recurringOptions) recurringOptions.style.display = s.optionsDisplay;
    ['daySun','dayMon','dayTue','dayWed','dayThu','dayFri','daySat'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.checked = s[id];
    });
    document.querySelectorAll('input[name="endType"]').forEach(r => {
      r.checked = r.value === s.endType;
    });
    if (endAfterInput) {
      endAfterInput.value = s.endAfter;
      endAfterInput.disabled = s.endType !== 'after';
    }
    if (endByInput) {
      endByInput.value = s.endBy;
      endByInput.disabled = s.endType !== 'by';
    }
    if (selectedGig) selectedGig._pendingDetach = s.pendingDetach;
    _recurringSnapshot = null;
  }

  function setEndType(selected) {
    endTypeCheckboxes.forEach(cb => {
      cb.checked = cb === selected;
    });

    endAfterInput.disabled = selected.value !== 'after';
    endByInput.disabled = selected.value !== 'by';
  }

  endTypeCheckboxes.forEach(cb => {
    cb.addEventListener('change', () => {
      if (!cb.checked) {
        cb.checked = true; // prevent unchecking all
        return;
      }
      setEndType(cb);
    });
  });

  // Force default on modal open
  function resetEndType() {
    const defaultCb =
      document.querySelector('input[name="endType"][value="after"]') ||
      endTypeCheckboxes[0];
    setEndType(defaultCb);
  }

  // v93 FIX: Guard artist type handler - NOW DISABLED (per-slot artist type)
  // Global artistTypeInput kept for backward compat but hidden
  // Slot builder is always visible

  // Initialize slot builder with Slot 1 on page load if empty
  if (document.getElementById('slotList') && document.getElementById('slotList').children.length === 0) {
    // Will be populated when modal opens
  }

  // Recurring checkbox: toggle options, and confirm before detaching an existing gig from series
  if (recurringCheckbox && recurringOptions) {
    recurringCheckbox.addEventListener('change', () => {
      if (recurringCheckbox.checked) {
        recurringOptions.style.display = 'block';
        // Reset end type inputs to clean state when showing for the first time
        resetEndType();
        endAfterInput.value = '';
        endByInput.value = '';
        // Auto-check the gig's day of week if no days are selected yet
        const anyDayChecked = ['daySun','dayMon','dayTue','dayWed','dayThu','dayFri','daySat']
          .some(id => document.getElementById(id)?.checked);
        if (!anyDayChecked && selectedGig && selectedGig.date) {
          const _d = new Date(selectedGig.date + 'T00:00:00');
          const _dayMap = ['daySun','dayMon','dayTue','dayWed','dayThu','dayFri','daySat'];
          const _el = document.getElementById(_dayMap[_d.getDay()]);
          if (_el) _el.checked = true;
        }
      } else {
        // If this is an existing recurring gig, confirm before detaching
        if (selectedGig && selectedGig.id && selectedGig.recurring_group_id) {
          // Re-check immediately while we ask
          recurringCheckbox.checked = true;

          // Show site-style confirm modal
          const overlay = document.createElement('div');
          overlay.id = '_detachConfirmOverlay';
          overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;z-index:10002;';
          overlay.innerHTML = `
            <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:2px solid rgba(239,68,68,0.5);border-radius:12px;padding:2rem;max-width:420px;width:90%;text-align:center;box-shadow:0 8px 32px rgba(239,68,68,0.2);">
              <div style="font-size:2.5rem;margin-bottom:1rem;">🔁</div>
              <p style="color:#f0f0f0;margin:0 0 0.5rem;font-size:1.05rem;font-weight:700;">Remove from Recurring Series?</p>
              <p style="color:#a0a0b0;margin:0 0 1.75rem;font-size:0.88rem;line-height:1.5;">This gig will be removed from the recurring series and become a standalone gig. The other gigs in the series won't be affected.</p>
              <div style="display:flex;gap:12px;justify-content:center;">
                <button id="_detachCancel" class="btn primary" style="min-width:110px;">Keep in Series</button>
                <button id="_detachConfirm" class="btn" style="min-width:110px;background:rgba(239,68,68,0.2);border-color:rgba(239,68,68,0.5);color:#ef4444;">Remove</button>
              </div>
            </div>
          `;
          document.body.appendChild(overlay);

          document.getElementById('_detachCancel').onclick = () => {
            overlay.remove();
            // Leave checkbox checked, options visible — no change
          };

          document.getElementById('_detachConfirm').onclick = () => {
            overlay.remove();
            // Uncheck and hide options, flag for detach on save
            recurringCheckbox.checked = false;
            recurringOptions.style.display = 'none';
            if (selectedGig) selectedGig._pendingDetach = true;
            // Remove the series banner from the modal immediately
            const info = document.getElementById('gigArtistInfo');
            if (info) { info.innerHTML = ''; info.style.display = 'none'; }
          };
        } else {
          // New gig or non-recurring — just hide options
          recurringOptions.style.display = 'none';
        }
      }
    });
  }

  // ===================================
  // SLOT BUILDER (All Artist Types)
  // ===================================
  let slotCounter = 0;
  
  function getDefaultSlotTimes() {
    // Comedian: 20-minute slots, Others: 2-hour slots (7pm-9pm)
    const type = artistTypeInput ? artistTypeInput.value : '';
    if (type === 'Comedian') {
      return { startTime: '19:00', endTime: '19:20', durationMinutes: 20 };
    } else {
      return { startTime: '19:00', endTime: '21:00', durationMinutes: 120 };
    }
  }
  
  function getDefaultSlotPay() {
    if (venueData) {
      const dollars = venueData.default_pay_dollars || 0;
      const cents = venueData.default_pay_cents || 0;
      return { dollars: formatWithCommas(dollars.toString()), cents: cents.toString().padStart(2, '0') };
    }
    return { dollars: '0', cents: '00' };
  }
  
  function addSlotRow(startTime = '', endTime = '', payDollars = '', payCents = '', slotArtistType = '', slotBandFormats = '', slotStyles = '') {
    slotCounter++;
    const slotList = document.getElementById('slotList');
    const slotNum = slotList.children.length + 1;
    const defaults = getDefaultSlotTimes();
    const defaultPay = getDefaultSlotPay();
    
    // Auto-chain from previous slot
    let useStart = startTime || defaults.startTime;
    let useEnd = endTime || defaults.endTime;
    const prevRow = slotList.lastElementChild;
    if (prevRow && !startTime) {
      const prevEnd = prevRow.querySelector('.slot-end').value;
      if (prevEnd) {
        useStart = prevEnd;
        const [h, m] = prevEnd.split(':').map(Number);
        const newEnd = new Date(2000, 0, 1, h, m + defaults.durationMinutes);
        useEnd = newEnd.getHours().toString().padStart(2, '0') + ':' + newEnd.getMinutes().toString().padStart(2, '0');
      }
    }
    
    const useDollars = payDollars || defaultPay.dollars;
    const useCents = payCents || defaultPay.cents;

    const allStyles = ['Country','Hip-Hop','Indie','Jazz','Latin','Pop','Reggae','Rock'];
    const allLineup = ['Solo','Duo','Trio','Full Band'];
    const selStyles = slotStyles ? slotStyles.split(',').map(s => s.trim()) : [];
    const selFormats = slotBandFormats ? slotBandFormats.split(',').map(f => f.trim()) : [];
    const isLiveBand = slotArtistType === 'Live Band';
    
    const row = document.createElement('div');
    row.className = 'slot-row';
    row.dataset.slotIndex = slotCounter;
    row.style.cssText = 'padding:8px 10px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:6px; margin-bottom:2px;';
    
    row.innerHTML = `
      <div style="display:flex; align-items:center; gap:6px; flex-wrap:nowrap;">
        <span style="font-size:0.75rem; font-weight:600; color:#a78bfa; min-width:40px; white-space:nowrap;">Slot ${slotNum}</span>
        <input type="time" class="slot-start" value="${useStart}" style="flex:0 0 auto; width:105px; min-width:0; font-size:0.8rem; padding:4px 6px;">
        <span style="color:var(--text-muted); font-size:0.7rem;">to</span>
        <input type="time" class="slot-end" value="${useEnd}" style="flex:0 0 auto; width:105px; min-width:0; font-size:0.8rem; padding:4px 6px;">
        <span style="color:var(--text-muted); font-size:0.75rem; margin-left:4px;">$</span>
        <input type="text" class="slot-pay-dollars" value="${useDollars}" maxlength="6" style="width:7ch; text-align:right; font-size:0.8rem; padding:4px 6px;" placeholder="0">
        <span style="color:var(--text-muted);">.</span>
        <input type="text" class="slot-pay-cents" value="${useCents}" maxlength="2" style="width:4ch; text-align:center; font-size:0.8rem; padding:4px 6px;" placeholder="00">
        <button type="button" class="remove-slot-btn" style="
          background:rgba(239,68,68,0.15); border:1px solid rgba(239,68,68,0.3); color:#ef4444;
          border-radius:4px; padding:2px 8px; font-size:0.8rem; cursor:pointer; line-height:1.4; flex-shrink:0; margin-left:auto;
        " title="Remove slot">×</button>
      </div>
      <div style="display:flex; align-items:center; gap:8px; margin-top:6px;">
        <label style="font-size:0.7rem; color:#a78bfa; min-width:80px; font-weight:600;">Artist Type:</label>
        <select class="slot-artist-type" style="font-size:0.78rem; padding:3px 6px; flex:1; max-width:180px;">
          <option value="">Select Type</option>
          <option value="Live Band" ${slotArtistType === 'Live Band' ? 'selected' : ''}>Live Band</option>
          <option value="DJ" ${slotArtistType === 'DJ' ? 'selected' : ''}>DJ</option>
          <option value="Comedian" ${slotArtistType === 'Comedian' ? 'selected' : ''}>Comedian</option>
          <option value="Trivia Host" ${slotArtistType === 'Trivia Host' ? 'selected' : ''}>Trivia Host</option>
        </select>
      </div>
      <div class="slot-styles-row" style="display:${isLiveBand ? 'flex' : 'none'}; align-items:flex-start; gap:8px; margin-top:4px; flex-wrap:wrap;">
        <label style="font-size:0.7rem; color:#a78bfa; min-width:80px; font-weight:600; padding-top:2px;">Styles:</label>
        <div style="display:flex; gap:8px 12px; flex-wrap:wrap; flex:1;">
          ${allStyles.map(s => `<label style="display:flex; align-items:center; gap:4px; font-size:0.75rem; cursor:pointer;"><input type="checkbox" class="slot-style-cb" value="${s}" ${selStyles.includes(s) || (!slotStyles && isLiveBand) ? 'checked' : ''}><span>${s}</span></label>`).join('')}
        </div>
      </div>
      <div class="slot-lineup-row" style="display:${isLiveBand ? 'flex' : 'none'}; align-items:flex-start; gap:8px; margin-top:4px; flex-wrap:wrap;">
        <label style="font-size:0.7rem; color:#a78bfa; min-width:80px; font-weight:600; padding-top:2px;">Lineup:</label>
        <div style="display:flex; gap:8px 12px; flex-wrap:wrap; flex:1;">
          ${allLineup.map(f => `<label style="display:flex; align-items:center; gap:4px; font-size:0.75rem; cursor:pointer;"><input type="checkbox" class="slot-lineup-cb" value="${f}" ${selFormats.includes(f) || (!slotBandFormats && isLiveBand) ? 'checked' : ''}><span>${f}</span></label>`).join('')}
        </div>
      </div>
    `;
    
    // Artist type change handler per slot
    const typeSelect = row.querySelector('.slot-artist-type');
    const stylesRow = row.querySelector('.slot-styles-row');
    const lineupRow = row.querySelector('.slot-lineup-row');
    typeSelect.addEventListener('change', () => {
      const isLB = typeSelect.value === 'Live Band';
      stylesRow.style.display = isLB ? 'flex' : 'none';
      lineupRow.style.display = isLB ? 'flex' : 'none';
      // Sync hidden global artistTypeInput from first slot (backward compat)
      const firstSlot = slotList.querySelector('.slot-row .slot-artist-type');
      if (firstSlot && artistTypeInput) artistTypeInput.value = firstSlot.value;
    });

    // ── Smart time check ─────────────────────────────────────────────────
    // Fires when end time changes. If end < start and both are in a range
    // where it looks like the user meant AM (overnight), either auto-correct
    // or ask via a small popup.
    function _fmt12(hhmm) {
      if (!hhmm) return '';
      const [h, m] = hhmm.split(':').map(Number);
      const ampm = h >= 12 ? 'PM' : 'AM';
      const hr = h % 12 || 12;
      return `${hr}:${String(m).padStart(2,'0')} ${ampm}`;
    }

    function _addHours(hhmm, hrs) {
      const [h, m] = hhmm.split(':').map(Number);
      const total = ((h + hrs) % 24 + 24) % 24;
      return `${String(total).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
    }

    function _checkSlotTimes() {
      const startInput = row.querySelector('.slot-start');
      const endInput   = row.querySelector('.slot-end');
      const s = startInput.value;
      const e = endInput.value;
      if (!s || !e || e === s) return;

      const [sh, sm] = s.split(':').map(Number);
      const [eh, em] = e.split(':').map(Number);
      const startMins = sh * 60 + sm;
      const endMins   = eh * 60 + em;

      // End is after start — no issue
      if (endMins > startMins) return;

      // End equals start — validateSlots handles this
      if (endMins === startMins) return;

      // End is before start — suggest flipping AM/PM (±12h)
      let suggested, hint;
      if (eh < 12) {
        // e.g. end=11:00 (AM), start=19:00 → suggest 23:00 (11pm)
        suggested = _addHours(e, 12);
        hint = 'Looks like you meant PM — end time is before start.';
      } else {
        // e.g. end=13:00 (1pm), start=19:00 → suggest 01:00 (1am overnight)
        suggested = _addHours(e, -12);
        hint = 'Looks like an overnight gig — end time is before start.';
      }

      const msg = document.createElement('div');
      msg.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);display:flex;align-items:center;justify-content:center;z-index:10005;';
      msg.innerHTML = `
        <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:2px solid rgba(99,91,255,0.5);border-radius:12px;padding:1.5rem 2rem;max-width:360px;width:90%;text-align:center;box-shadow:0 8px 32px rgba(99,91,255,0.25);">
          <div style="font-size:2rem;margin-bottom:0.75rem;">🕐</div>
          <p style="color:#f0f0f0;margin:0 0 0.4rem;font-size:1rem;font-weight:700;">Did you mean:</p>
          <p style="color:#a78bfa;margin:0 0 1.25rem;font-size:1.1rem;font-weight:600;">${_fmt12(s)} to ${_fmt12(suggested)}?</p>
          <p style="color:#888;font-size:0.8rem;margin:0 0 1.25rem;">${hint}</p>
          <div style="display:flex;gap:10px;justify-content:center;">
            <button id="_timeNo"  class="btn ghost" style="min-width:80px;">No, keep it</button>
            <button id="_timeYes" class="btn primary" style="min-width:80px;">Yes, fix it</button>
          </div>
        </div>
      `;
      document.body.appendChild(msg);
      msg.querySelector('#_timeYes').onclick = () => { endInput.value = suggested; msg.remove(); };
      msg.querySelector('#_timeNo').onclick  = () => { msg.remove(); };
    }

    row.querySelector('.slot-end').addEventListener('blur', _checkSlotTimes);
    // ─────────────────────────────────────────────────────────────────────
    
    // Remove slot handler
    row.querySelector('.remove-slot-btn').addEventListener('click', () => {
      const slotRows = slotList.querySelectorAll('.slot-row');
      if (slotRows.length <= 1) {
        // Last slot — if editing an existing gig, trigger delete instead
        if (selectedGig && selectedGig.id) {
          // Simulate clicking Delete Gig button to enter confirm-delete flow
          deleteBtn.dataset.confirmDelete = 'false';
          deleteBtn.dataset.multiSlotDelete = 'false';
          deleteBtn.textContent = 'Confirm Delete?';
          deleteBtn.dataset.confirmDelete = 'true';
          // Auto-reset after 5s if not clicked
          setTimeout(() => {
            if (deleteBtn.dataset.confirmDelete === 'true') {
              deleteBtn.dataset.confirmDelete = 'false';
              deleteBtn.textContent = 'Delete Gig';
            }
          }, 5000);
          showAlert('This is the only slot. To remove it, use "Confirm Delete?" to delete the whole gig.');
        } else {
          // Creating new gig — just prevent removal, keep at least 1 slot
          showAlert('A gig must have at least one slot.');
        }
        return;
      }
      row.remove();
      renumberSlots();
      clearSlotError();
    });
    
    // Dollar formatting on pay input
    row.querySelector('.slot-pay-dollars').addEventListener('input', (e) => {
      e.target.value = formatWithCommas(e.target.value);
    });
    
    // Validate on time change
    row.querySelector('.slot-start').addEventListener('change', validateSlots);
    row.querySelector('.slot-end').addEventListener('change', validateSlots);
    
    slotList.appendChild(row);
    clearSlotError();
  }
  
  function renumberSlots() {
    const rows = document.querySelectorAll('#slotList .slot-row');
    rows.forEach((row, i) => {
      row.querySelector('span').textContent = `Slot ${i + 1}`;
    });
  }
  
  // ── Overlap check helper ──────────────────────────────────────────────────
  // Returns a human-readable conflict message, or null if no overlap.
  // s1<e2 AND e1>s2 → overlap; touching endpoints (4pm-7pm / 7pm-9pm) = OK.
  function _timesOverlap(s1, e1, s2, e2) {
    return s1 < e2 && e1 > s2;
  }

  async function checkNewGigOverlap(date, slots, excludeGigId) {
    // Make sure cache is fresh
    if (gigsCacheDirty) await refreshGigs();
    const sameDayGigs = venueGigsCache.filter(g =>
      g.date === date &&
      g.id !== excludeGigId &&
      g.status !== 'cancelled'
    );
    if (sameDayGigs.length === 0) return null;

    for (const slot of slots) {
      const ns = slot.start_time, ne = slot.end_time;
      if (!ns || !ne) continue;
      for (const existing of sameDayGigs) {
        // Check against each slot of the existing gig if multi-slot, else gig-level times
        const existingWindows = [];
        if (existing.slots && existing.slots.length) {
          existing.slots.forEach(es => {
            if (es.start_time && es.end_time) existingWindows.push([es.start_time, es.end_time]);
          });
        }
        if (!existingWindows.length && existing.start_time && existing.end_time) {
          existingWindows.push([existing.start_time, existing.end_time]);
        }
        for (const [es, ee] of existingWindows) {
          if (_timesOverlap(ns, ne, es, ee)) {
            const fmt = t => { const [h, m] = t.split(':'); const hr = +h; return `${hr > 12 ? hr-12 : hr||12}:${m} ${hr >= 12 ? 'PM' : 'AM'}`; };
            return `Slot time ${fmt(ns)}–${fmt(ne)} overlaps with an existing gig at this venue (${fmt(es)}–${fmt(ee)}). Please choose a different time.`;
          }
        }
      }
    }
    return null;
  }
  // ─────────────────────────────────────────────────────────────────────────

  function getSlotData() {
    const rows = document.querySelectorAll('#slotList .slot-row');
    const slots = [];
    rows.forEach((row, i) => {
      const start = row.querySelector('.slot-start').value;
      const end = row.querySelector('.slot-end').value;
      const dollars = (row.querySelector('.slot-pay-dollars')?.value || '0').replace(/,/g, '');
      const cents = row.querySelector('.slot-pay-cents')?.value || '0';
      const pay = parseFloat(`${dollars}.${cents}`);
      const artistType = row.querySelector('.slot-artist-type')?.value || null;
      let bandFormats = null;
      let styles = null;
      if (artistType === 'Live Band') {
        const checkedStyles = row.querySelectorAll('.slot-style-cb:checked');
        styles = Array.from(checkedStyles).map(cb => cb.value).join(',') || null;
        const checkedLineup = row.querySelectorAll('.slot-lineup-cb:checked');
        bandFormats = Array.from(checkedLineup).map(cb => cb.value).join(',') || null;
      }
      slots.push({ slot_number: i + 1, start_time: start, end_time: end, pay: pay,
                    artist_type: artistType, band_formats: bandFormats, styles: styles });
    });
    return slots;
  }
  
  function validateSlots() {
    const slots = getSlotData();
    
    for (let i = 0; i < slots.length; i++) {
      const s = slots[i];
      // Allow end_time <= start_time for overnight slots (e.g. 11pm–1am; end is next day)
      if (s.end_time === s.start_time) {
        showSlotError(`Slot ${i + 1}: End time cannot equal start time.`);
        return false;
      }
      for (let j = 0; j < slots.length; j++) {
        if (i === j) continue;
        const o = slots[j];
        // Overlap: same-day only (overnight slots not compared as overlapping)
        const sOvernight = s.end_time <= s.start_time;
        const oOvernight = o.end_time <= o.start_time;
        if (!sOvernight && !oOvernight && s.start_time < o.end_time && s.end_time > o.start_time) {
          showSlotError(`Slot ${i + 1} overlaps with Slot ${j + 1}. Times cannot overlap.`);
          return false;
        }
      }
    }
    clearSlotError();
    return true;
  }
  
  function showSlotError(msg) {
    const el = document.getElementById('slotError');
    if (el) { el.textContent = msg; el.style.display = 'block'; }
  }
  
  function clearSlotError() {
    const el = document.getElementById('slotError');
    if (el) { el.textContent = ''; el.style.display = 'none'; }
  }
  
  function resetSlotBuilder() {
    const slotList = document.getElementById('slotList');
    if (slotList) slotList.innerHTML = '';
    slotCounter = 0;
    clearSlotError();
  }
  
  // Add Slot button handler
  const addSlotBtn = document.getElementById('addSlotBtn');
  if (addSlotBtn) {
    addSlotBtn.addEventListener('click', () => addSlotRow());
  }


  let currentDate = new Date();
  let selectedGig = null;
  let selectedDate = null;
  
  // Gig cache - avoids fetching from API on every calendar render
  let venueGigsCache = [];
  let gigsCacheDirty = true;
  
  async function refreshGigs() {
    venueGigsCache = await api(`/venues/${venueId}/gigs`);
    gigsCacheDirty = false;
  }
  
  function invalidateGigs() {
    gigsCacheDirty = true;
  }
  let venueData = null; // Store venue default values
  window.venueBlinkSettings = {}; // Blink settings per notification_key

  // Fetch venue data for defaults (enforces access control on backend)
  try {
    const res = await fetch(`/api/venues/${venueId}`, {
      credentials: "include"
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch venue: ${res.status} ${res.statusText}`);
    }
    venueData = await res.json();
  } catch (e) {
    console.error("❌ v104: Failed to load venue data:", e);
  }

  // Fetch blink settings for calendar rendering
  try {
    const blinkRes = await fetch(`/api/venues/${venueId}/email-notifications`, { credentials: 'include' });
    if (blinkRes.ok) {
      const blinkData = await blinkRes.json();
      window.venueBlinkSettings = blinkData;
    } 
  } catch (e) {}

  // If the authenticated user doesn't have access to this venue,
  // short‑circuit the page and show a friendly message.
  if (!venueData) {
    document.body.innerHTML = `
      <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#020617;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
        <div style="max-width:460px;padding:32px 28px;border-radius:16px;background:rgba(15,23,42,0.95);box-shadow:0 20px 40px rgba(15,23,42,0.8);border:1px solid rgba(148,163,184,0.35);text-align:center;">
          <div style="font-size:0.85rem;font-weight:600;letter-spacing:0.18em;color:#38bdf8;margin-bottom:8px;">GIGSFILL</div>
          <h1 style="margin:0 0 12px;font-size:1.2rem;font-weight:600;color:#e5e7eb;">No Access to This Venue</h1>
          <p style="margin:0 0 20px;font-size:0.9rem;line-height:1.6;color:#9ca3af;">
            You’re logged in, but your account doesn’t have permission to manage this venue.
            If you think this is a mistake, ask the venue owner to invite you from the <strong>Users</strong> tab.
          </p>
          <button onclick="window.location.href='/app/user-profile.html';" style="display:inline-flex;align-items:center;justify-content:center;padding:8px 18px;border-radius:999px;border:1px solid rgba(56,189,248,0.6);background:linear-gradient(135deg,#0ea5e9,#22c55e);color:white;font-size:0.85rem;font-weight:600;cursor:pointer;">
            Go to My Dashboard
          </button>
        </div>
      </div>
    `;
    return;
  }

  // ===================================
  // STYLED ALERT FUNCTION
  // ===================================
  function showAlert(message, title = "Alert") {
    const alertModal = document.getElementById("alertModal");
    const alertModalTitle = document.getElementById("alertModalTitle");
    const alertModalMessage = document.getElementById("alertModalMessage");
    const alertModalOk = document.getElementById("alertModalOk");
    
    alertModalTitle.textContent = title;
    alertModalMessage.textContent = message;
    alertModal.classList.remove("hidden");
    
    alertModalOk.onclick = () => {
      alertModal.classList.add("hidden");
    };
  }

  async function api(url, options = {}) {
    const res = await fetch(url, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...options
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function formatTime12Hour(t) {
    if (!t || typeof t !== "string" || !t.includes(":")) {
      return "";
    }
  
    const parts = t.split(":");
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
  
    if (isNaN(h) || isNaN(m)) {
      return "";
    }
  
    const ampm = h >= 12 ? "PM" : "AM";
    const hour = ((h + 11) % 12) + 1;
  
    return `${hour}:${m.toString().padStart(2, "0")} ${ampm}`;
  }

  function isGigStartedToday(gig) {
    if (!gig.date || !gig.start_time) return false;
    const now = new Date();
    const [y, mo, d] = gig.date.split('-').map(Number);
    const gigDay = new Date(y, mo - 1, d);
    gigDay.setHours(0, 0, 0, 0);
    const todayMid = new Date();
    todayMid.setHours(0, 0, 0, 0);
    if (gigDay.getTime() !== todayMid.getTime()) return false;
    const [h, min] = gig.start_time.split(':').map(Number);
    const startTime = new Date(y, mo - 1, d, h, min, 0);
    return now >= startTime;
  }

  function isSlotStartedToday(gig, slot) {
    if (!gig.date || !slot.start_time) return false;
    const now = new Date();
    const [y, mo, d] = gig.date.split('-').map(Number);
    const gigDay = new Date(y, mo - 1, d);
    gigDay.setHours(0, 0, 0, 0);
    const todayMid = new Date();
    todayMid.setHours(0, 0, 0, 0);
    if (gigDay.getTime() !== todayMid.getTime()) return false;
    const [h, min] = slot.start_time.split(':').map(Number);
    const startTime = new Date(y, mo - 1, d, h, min, 0);
    return now >= startTime;
  }

  function isGigEndPassed(gig) {
    // Returns true if the gig's end time has already passed (gig is over).
    if (!gig.date) return false;
    const [y, mo, d] = gig.date.split('-').map(Number);
    const endTimeStr = gig.end_time || gig.start_time || '23:59';
    const [h, min] = endTimeStr.split(':').map(Number);
    let endDate = new Date(y, mo - 1, d, h, min, 0);
    // Handle overnight slots: if end_time < start_time, end is next day
    if (gig.start_time && gig.end_time && gig.end_time <= gig.start_time) {
      endDate = new Date(y, mo - 1, d + 1, h, min, 0);
    }
    return new Date() > endDate;
  }


  function getMonthDays(year, month) {
    const days = [];
    
    // Get first day of month
    const firstDay = new Date(year, month, 1);
    const firstDayOfWeek = firstDay.getDay(); // 0 = Sunday
    
    // Add days from previous month to fill the week
    for (let i = firstDayOfWeek - 1; i >= 0; i--) {
      const d = new Date(year, month, 0 - i); // 0 = last day of previous month
      days.push(d);
    }
    
    // Add all days of current month
    const d = new Date(year, month, 1);
    while (d.getMonth() === month) {
      days.push(new Date(d));
      d.setDate(d.getDate() + 1);
    }
    
    // Add days from next month to complete the last week
    const lastDay = days[days.length - 1];
    const lastDayOfWeek = lastDay.getDay();
    if (lastDayOfWeek < 6) { // If not Saturday
      for (let i = 1; i <= 6 - lastDayOfWeek; i++) {
        const d = new Date(year, month + 1, i);
        days.push(d);
      }
    }
    
    return days;
  }

  // ── Blink style helper ─────────────────────────────────────────────────────
  // Returns inline CSS string for a blinking gig bubble, or null if no blink.
  // Uses per-venue blink settings keyed by last_notification_key.
  // Only notification keys with blink_enabled=true produce a blink effect.
  function getBlinkStyle(g) {
    // Returns a hex color if this gig should blink, null if not.
    // Rules:
    //   - cancelled_blast / radius_blast: ONLY blink when the blast has already fired
    //     (g.is_blast_open is set). Never blink these by date proximity alone —
    //     they are post-cancellation events, not advance notices.
    //   - open_gig_* keys: blink based on how close the gig date is to today,
    //     within the window the venue configured, if blink_enabled is on.
    const bs = window.venueBlinkSettings || {};
    const _defaultColors = {
      'open_gig_4w': '#10b981', 'open_gig_2w': '#10b981',
      'open_gig_1w': '#f59e0b', 'open_gig_36h': '#f59e0b',
      'cancelled_blast': '#f59e0b', 'radius_blast': '#f59e0b',
    };

    // Blast fired — check the specific blast notification key first,
    // then fall through to proximity-based open_gig_* check below
    if (g.is_blast_open) {
      const blastKey = g.last_notification_key;
      if (blastKey) {
        const s = bs[blastKey];
        if (s && s.blink_enabled) {
          return s.blink_color || _defaultColors[blastKey] || '#f59e0b';
        }
        // Blast key found but blink not enabled — no blink for blast reason,
        // but fall through to check proximity-based open_gig_* blink below
      }
      // No blast key or blink not enabled for blast — fall through to proximity check
    }

    // Open gig notifications — blink by date-window proximity only
    if (!g.date) return null;
    const gigDate = new Date(g.date + 'T00:00:00');
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const daysUntil = Math.round((gigDate - now) / 86400000);
    if (daysUntil < 0) return null; // past gig

    function toDays(val, unit) {
      if (unit === 'hours') return val / 24;
      if (unit === 'days')  return val;
      return val * 7; // weeks
    }

    // Only open_gig keys — cancelled/radius blast never appear here
    const urgencyOrder = ['open_gig_36h', 'open_gig_1w', 'open_gig_2w', 'open_gig_4w'];
    for (const key of urgencyOrder) {
      const s = bs[key];
      // blink_enabled is independent of email enabled — venue can blink without sending emails
      if (!s || !s.blink_enabled) continue;
      const windowDays = toDays(s.time_value || 1, s.time_unit || 'weeks');
      if (daysUntil <= windowDays) {
        return s.blink_color || _defaultColors[key] || '#f59e0b';
      }
    }

    return null; // not within any blink-enabled window
  }

  // Returns a descriptive hover title explaining WHY this gig bubble is blinking
  function getBlinkTitle(g, blinkColor) {
    const bs = window.venueBlinkSettings || {};
    const _dc = {'open_gig_4w':'#10b981','open_gig_2w':'#10b981','open_gig_1w':'#f59e0b','open_gig_36h':'#f59e0b'};
    const isAmber = blinkColor && blinkColor !== '#10b981';

    // Actual radius blast fired
    if (g.is_blast_open) {
      const key = g.last_notification_key || 'radius_blast';
      if (key === 'radius_blast') {
        const miles = (bs.radius_blast && bs.radius_blast.time_value)
          ? `${bs.radius_blast.time_value} ${bs.radius_blast.time_unit} before`
          : 'near start time';
        const radius = (bs.radius_blast && bs.radius_blast.blast_all_radius) || 20;
        return `⚡ Radius blast sent — all artists within ${radius} miles were notified (${miles})`;
      }
      if (key === 'cancelled_blast') {
        const radius = (bs.cancelled_blast && bs.cancelled_blast.blast_all_radius) || 20;
        const blastAll = bs.cancelled_blast && bs.cancelled_blast.blast_all_enabled;
        return blastAll
          ? `⚡ Cancellation blast sent — all Preferred Artists + artists within ${radius} miles notified`
          : '⚡ Cancellation blast sent — all Preferred Artists notified';
      }
    }

    // Open gig notification window — determine which window we're in
    if (!g.date) return '⏰ Notification window active';
    const gigDate = new Date(g.date + 'T00:00:00');
    const now = new Date(); now.setHours(0,0,0,0);
    const daysUntil = Math.round((gigDate - now) / 86400000);

    function toDays(val, unit) {
      if (unit === 'hours') return val / 24;
      if (unit === 'days') return val;
      return val * 7;
    }

    // Check which window this gig falls in (most urgent first)
    for (const key of ['open_gig_36h', 'open_gig_1w', 'open_gig_2w', 'open_gig_4w']) {
      const s = bs[key];
      if (!s || !s.enabled) continue;
      const windowDays = toDays(s.time_value || 1, s.time_unit || 'weeks');
      if (daysUntil <= windowDays) {
        const when = s.time_unit === 'hours'
          ? `${s.time_value}hr`
          : s.time_unit === 'days'
            ? `${s.time_value} day${s.time_value > 1 ? 's' : ''}`
            : `${s.time_value} week${s.time_value > 1 ? 's' : ''}`;
        if (isAmber) {
          const blastAll = s.blast_all_enabled;
          const radius = s.blast_all_radius || 20;
          return blastAll
            ? `⚡ ${when}-out email sent — Preferred Artists + all artists within ${radius} miles notified`
            : `📢 ${when}-out email sent — all Preferred Artists notified`;
        } else {
          // Green blink — within window but no email sent yet (or blink_enabled=true on a green key)
          return `🟢 Gig is open and coming up. Check Email Center to blast this gig to artists.`;
        }
      }
    }

    return '⏰ Notification window active';
  }

async function renderCalendar() {
    calendarEl.innerHTML = "";

    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();

    monthLabel.textContent =
      currentDate.toLocaleString("default", { month: "long", year: "numeric" });

    // Re-fetch blink settings so Email Center changes take effect immediately
    try {
      const _br = await fetch(`/api/venues/${venueId}/email-notifications`, { credentials: 'include' });
      if (_br.ok) window.venueBlinkSettings = await _br.json();
    } catch(e) {}

    if (gigsCacheDirty) await refreshGigs();
    const gigs = venueGigsCache || [];
    const days = getMonthDays(year, month);
    
    // v93: Get today's date for comparison
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    for (const day of days) {
      const iso = `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
      const cell = document.createElement("div");
      cell.className = "day";
      
      // Check if day is in current month
      const isCurrentMonth = day.getMonth() === month;
      if (!isCurrentMonth) {
        cell.classList.add("other-month");
      }
      
      // v93: Check if this date is in the past
      const dayDate = new Date(day);
      dayDate.setHours(0, 0, 0, 0);
      const isPast = dayDate < today;
      
      if (isPast) {
        cell.classList.add("past-date");
      }
      
      if (dayDate.getTime() === today.getTime()) {
        cell.classList.add("today");
      }
      
      const dayNumber = document.createElement("div");
      dayNumber.className = "day-number";
      dayNumber.textContent = day.getDate();
      cell.appendChild(dayNumber);

      // Get and SORT gigs for this day by start_time (earliest to latest)
      const dayGigs = gigs.filter(g => g.date === iso).sort((a, b) => {
        return a.start_time.localeCompare(b.start_time);
      });

      if (dayGigs.length > 0) {
        // Create scrollable container for gigs
        const gigsContainer = document.createElement("div");
        gigsContainer.className = "gigs-container";
        
        dayGigs.forEach(g => {
          const icons = { 'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤', 'Trivia Host': '🧠' };
          const icon = icons[g.artist_type] || '🎵';
          
          if (g.slots && g.slots.length > 0) {
            // Multi-slot: show each slot sorted by start_time, earliest first
            const sortedDaySlots = [...g.slots].sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
            sortedDaySlots.forEach(slot => {
              const slotIcon = icons[slot.artist_type || g.artist_type] || '🎵';
              const slotDiv = document.createElement("div");
              const slotBooked = slot.status === 'booked';
              const slotPendingApproval = slot.status === 'pending_venue_approval';
              const parentPending = slot.status === 'pending_contract';
              const slotStarted = !parentPending && !slotPendingApproval && isSlotStartedToday(g, slot);
              const _slotHasWaitlist = g.status !== 'cancelled' && !slotBooked && !slotStarted && !parentPending && !slotPendingApproval && !!g.has_active_waitlist && !isPast && !isGigEndPassed(g);
              const slotCls = g.status === 'cancelled' ? 'cancelled' : slotPendingApproval ? 'pending-venue-approval' : parentPending ? 'pending-contract-venue' : (slotStarted ? 'started' : slotBooked ? 'booked' : (_slotHasWaitlist ? 'waitlist-pending' : (!isPast && window.getBlinkStyle && window.getBlinkStyle(g) ? 'blast-open' : 'open')));
              slotDiv.className = `gig ${slotCls}`;
              // Set base hover titles for non-blast states
              if (slotCls === 'booked') {
                slotDiv.title = slot.artist_name ? `🎤 Booked: ${slot.artist_name}` : '🎤 This slot is booked';
              } else if (slotCls === 'open') {
                slotDiv.title = '🟢 This slot is Open — artists can book it';
              } else if (slotCls === 'started') {
                slotDiv.title = '⏱️ This slot has already started';
              } else if (slotCls === 'pending-venue-approval') {
                slotDiv.title = `🔴 ${slot.artist_name || 'An artist'} wants to book — click to Approve or Deny!`;
              }
              if (parentPending) {
                const cs = g.contract_status || '';
                slotDiv.title = (cs === 'artist_signed') ? 'Countersign Contract!' : 'Waiting on Artist to Upload Contract...';
                slotDiv.style.cssText = 'background: linear-gradient(135deg, #ef4444, #dc2626); animation: gig-force-pulse 1.8s ease-in-out infinite; color: white; cursor: pointer; padding: 2px 6px; border-radius: 6px; margin-bottom: 2px; font-size: 0.82rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;';
                slotDiv.onmouseenter = function() { this.style.animation='none'; this.style.opacity='1'; };
                slotDiv.onmouseleave = function() { this.style.animation='gig-force-pulse 1.8s ease-in-out infinite'; };
              } else if (slotCls === 'cancelled') {
                slotDiv.title = '🚫 This gig was cancelled — click to view details';
              } else if (_slotHasWaitlist) {
                slotDiv.title = '🔴 Waitlist active — contacting artists sequentially. Click to view.';
                slotDiv.style.cssText = 'background: linear-gradient(135deg, #ef4444, #dc2626) !important; animation: gig-blast-pulse-venue 1.2s ease-in-out infinite !important; color: #fff !important; cursor: pointer; padding: 2px 6px; border-radius: 6px; margin-bottom: 2px; font-size: 0.82rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;';
                slotDiv.onmouseenter = function() { this.style.animationPlayState='paused'; };
                slotDiv.onmouseleave = function() { this.style.animationPlayState='running'; };
              } else if (!slotBooked && !slotStarted && !isGigEndPassed(g) && !isPast) {
                const _blinkColor = (window.getBlinkStyle || function(){return null;})(g);
                if (_blinkColor) {
                  slotDiv.title = getBlinkTitle(g, _blinkColor);
                  slotDiv.style.setProperty('--blink-bg', _blinkColor);
                  slotDiv.style.setProperty('--blink-bg-end', _blinkColor === '#10b981' ? '#059669' : _blinkColor === '#f59e0b' ? '#d97706' : _blinkColor);
                  slotDiv.onmouseenter = function() { this.style.animationPlayState='paused'; };
                  slotDiv.onmouseleave = function() { this.style.animationPlayState='running'; };
                }
              }
              const slotTime = formatTime12Hour(slot.start_time);
              const slotArtist = slot.artist_name || 'OPEN';
              slotDiv.textContent = `${slotIcon} ${slotTime} · ${slotArtist}`;
              slotDiv.onclick = (e) => {
                e.stopPropagation();
                if (slotPendingApproval) {
                  // Pass a synthetic gig object so openGigModal shows the approval UI
                  openGigModal({ ...g, status: 'pending_venue_approval', artist_id: slot.artist_id, artist_name: slot.artist_name, _pendingSlot: slot });
                  return;
                }
                openGigModal(g);
              };
              gigsContainer.appendChild(slotDiv);
            });
          } else {
            // Single gig: icon + start time + artist or OPEN
            const div = document.createElement("div");
            const isStarted = (g.status === "open" || g.status === "booked") && isGigStartedToday(g);
            const isPendingContract = g.status === "pending_contract" || g.status === "awaiting_venue_contract"
              || (g.status === "open" && (g.slots || []).some(s => s.status === 'pending_contract' || s.status === 'awaiting_venue_contract'));
            const isPendingApproval = g.status === "pending_venue_approval";
            const _hasWaitlist = !isStarted && !isPendingContract && !isPendingApproval && g.status === 'open' && !!g.has_active_waitlist && !isPast && !isGigEndPassed(g);
            const gigCls = g.status === 'cancelled' ? 'cancelled' : isStarted ? 'started' : isPendingApproval ? 'pending-venue-approval' : isPendingContract ? 'pending-contract-venue' : (g.status === "booked") ? "booked" : (_hasWaitlist ? 'waitlist-pending' : (!isPast && window.getBlinkStyle && window.getBlinkStyle(g) ? "blast-open" : "open"));
            div.className = `gig ${gigCls}`;
            div.setAttribute("data-gig-status", g.status || "");
            div.setAttribute("data-contract-status", g.contract_status || "");
            const start = formatTime12Hour(g.start_time);
            const artist = g.artist_name || 'OPEN';
            div.textContent = `${icon} ${start} · ${artist}`;
            // Set base hover titles for non-blast states
            if (gigCls === 'booked') {
              div.title = g.artist_name ? `🎤 Booked: ${g.artist_name}` : '🎤 This gig is booked';
            } else if (gigCls === 'open') {
              div.title = '🟢 This gig is Open — artists can book it';
            } else if (gigCls === 'started') {
              div.title = '⏱️ This gig has already started';
            } else if (gigCls === 'pending-venue-approval') {
              div.title = `🔴 ${g.artist_name || 'An artist'} wants to book — click to Approve or Deny!`;
            }
            if (isPendingContract) {
              const cs = g.contract_status;
              div.title = (cs === 'artist_signed') ? 'Countersign Contract!' : 'Waiting on Artist to Upload Contract...';
              // INLINE style - bypass any CSS caching
              div.style.cssText = 'background: linear-gradient(135deg, #ef4444, #dc2626) !important; animation: gig-pulse-venue 1.8s ease-in-out infinite; cursor: pointer; color: white; padding: 2px 6px; border-radius: 6px; margin-bottom: 2px; font-size: 0.82rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;';
              div.onmouseenter = function() { this.style.animation = 'none'; this.style.opacity = '1'; };
              div.onmouseleave = function() { this.style.animation = 'gig-pulse-venue 1.8s ease-in-out infinite'; };
            } else if (_hasWaitlist) {
              div.title = '🔴 Waitlist active — contacting artists sequentially. Click to view.';
              div.style.cssText = 'background: linear-gradient(135deg, #ef4444, #dc2626) !important; animation: gig-blast-pulse-venue 1.2s ease-in-out infinite !important; color: #fff !important; cursor: pointer; padding: 2px 6px; border-radius: 6px; margin-bottom: 2px; font-size: 0.82rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;';
              div.onmouseenter = function() { this.style.animationPlayState = 'paused'; };
              div.onmouseleave = function() { this.style.animationPlayState = 'running'; };
            } else if (!isGigEndPassed(g) && !isPast && g.status === 'open') {
              const _blinkColor2 = (window.getBlinkStyle || function(){return null;})(g);
              if (_blinkColor2) {
                div.title = getBlinkTitle(g, _blinkColor2);
                div.style.setProperty('--blink-bg', _blinkColor2);
                div.style.setProperty('--blink-bg-end', _blinkColor2 === '#10b981' ? '#059669' : _blinkColor2 === '#f59e0b' ? '#d97706' : _blinkColor2);
                div.onmouseenter = function() { this.style.animationPlayState='paused'; };
                div.onmouseleave = function() { this.style.animationPlayState='running'; };
              }
            }
            div.onclick = (e) => {
              e.stopPropagation();
              openGigModal(g);
            };
            gigsContainer.appendChild(div);
          }
        });
        
        cell.appendChild(gigsContainer);
        
        // Add click handler to day cell - open Create Gig modal when clicking on empty space
        cell.onclick = (e) => {
          if (e.target === cell || e.target === dayNumber) {
            if (!isCurrentMonth || isPast) return;
            openGigModal({ date: iso, status: "open" });
          }
        };
      } else {
        // No gigs - allow creating new gig on future dates in current month
        cell.onclick = () => {
          if (!isCurrentMonth) {
            return;
          }
          if (isPast) {
            return;
          }
          openGigModal({ date: iso, status: "open" });
        };
      }

      calendarEl.appendChild(cell);
    }
    
    // Clone calendar to calendar2 if it exists (for search tab)
    if (calendarEl2) {
      calendarEl2.innerHTML = calendarEl.innerHTML;
      monthLabel2.textContent = monthLabel.textContent;
    }
  }

  // Expose for activity center links
  window.showVenueGigModal = async function(gigId) {
    invalidateGigs();
    await renderCalendar();
    const allG = getCachedGigs ? getCachedGigs() : [];
    const gig = allG.find(g => g.id === parseInt(gigId, 10));
    if (gig) openGigModal(gig);
  };

  // ── Day-square modal: list all gigs for a date ────────────────────────────
  async function openVenueDayGigsModal(iso, dayGigs) {
    if (!dayGigs || dayGigs.length === 0) {
      openGigModal({ date: iso, status: 'open' });
      return;
    }

    const overlay = document.getElementById('modalOverlay');
    const modal = overlay.querySelector('.modal');
    const title = document.getElementById('modalTitle');
    const body = document.getElementById('modalBody');
    const modalActions = document.getElementById('modalActions');

    modal.classList.add('day-modal');

    const [y, mo, d] = iso.split('-').map(Number);
    title.textContent = new Date(y, mo-1, d).toLocaleDateString('en-US', {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
    });

    const icons = {'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠'};

    const rows = dayGigs.map(g => {
      const isPendingContract = g.status === 'pending_contract' || g.status === 'awaiting_venue_contract'
        || (g.status === 'open' && (g.slots || []).some(s => s.status === 'pending_contract' || s.status === 'awaiting_venue_contract'));
      const isPendingApproval = g.status === 'pending_venue_approval';
      const isBooked = g.status === 'booked';
      const isBlast = !isBooked && !isPendingContract && !isPendingApproval && window.getBlinkStyle && window.getBlinkStyle(g);
      const hasWaitlist = !!g.has_active_waitlist && !isBooked && !isPendingContract && !isPendingApproval;

      let gigBg, gigBorder, gigTextColor;
      if (isBooked) {
        gigBg = 'linear-gradient(135deg,#ef4444,#b91c1c)'; gigBorder = '#b91c1c'; gigTextColor = '#ffffff';
      } else if (isPendingContract || isPendingApproval) {
        gigBg = 'linear-gradient(135deg,#3b82f6,#2563eb)'; gigBorder = '#2563eb'; gigTextColor = '#000000';
      } else if (hasWaitlist || isBlast) {
        gigBg = 'linear-gradient(135deg,#f59e0b,#d97706)'; gigBorder = '#d97706'; gigTextColor = '#000000';
      } else {
        gigBg = 'linear-gradient(135deg,#10b981,#059669)'; gigBorder = '#059669'; gigTextColor = '#000000';
      }

      const icon = icons[g.artist_type] || '🎵';
      const fmt = t => { if (!t) return ''; const [h,m] = t.split(':').map(Number); return ((h%12)||12)+':'+String(m).padStart(2,'0')+(h>=12?'PM':'AM'); };
      const time = fmt(g.start_time) + (g.end_time ? ' \u2013 ' + fmt(g.end_time) : '');

      // Artist: from gig or booked slot
      let artistDisplay = '';
      if (isBooked || isPendingContract || isPendingApproval) {
        const bookedSlot = (g.slots || []).find(s => s.artist_id && (s.status === 'booked' || s.status === 'pending_contract' || s.status === 'pending_venue_approval'));
        const aId = (bookedSlot && bookedSlot.artist_id) || g.artist_id;
        const aName = (bookedSlot && bookedSlot.artist_name) || g.artist_name;
        if (aName && aId) {
          artistDisplay = `<a href="/app/artist-profile.html?artist_id=${aId}" target="_blank" onclick="event.stopPropagation()" style="color:${gigTextColor};text-decoration:underline;font-weight:600;" onmouseover="this.style.opacity='0.8'" onmouseout="this.style.opacity='1'">${aName}</a>`;
        } else if (aName) {
          artistDisplay = `<span style="font-weight:600;">${aName}</span>`;
        } else {
          artistDisplay = `<span style="opacity:0.75;">Booked</span>`;
        }
      } else if (hasWaitlist) {
        artistDisplay = `<span style="opacity:0.85;">Waitlist Active</span>`;
      } else {
        artistDisplay = `<span style="opacity:0.75;">OPEN</span>`;
      }

      let artistTypeDisplay = g.artist_type || 'Any';
      if (g.artist_type === 'Live Band' && g.band_formats)
        artistTypeDisplay += ' \u2022 ' + g.band_formats.split(',').map(f => f.trim()).join(', ');

      return `<div onclick="window._venueOpenGigFromDay(${g.id})" style="display:grid;grid-template-columns:130px minmax(130px,1fr) minmax(120px,1fr) minmax(120px,1fr) minmax(150px,1.5fr);column-gap:12px;align-items:center;padding:6px 10px;margin:0;border:1px solid ${gigBorder};border-radius:6px;background:${gigBg};color:${gigTextColor};font-weight:700;white-space:nowrap;cursor:pointer;line-height:1.3;">
        <div>${icon} ${time}</div>
        <div style="overflow:hidden;text-overflow:ellipsis;">${g.venue_name || ''}</div>
        <div style="overflow:hidden;text-overflow:ellipsis;">${g.city || ''}${g.state ? ', ' + g.state : ''}</div>
        <div style="overflow:hidden;text-overflow:ellipsis;">${artistDisplay}</div>
        <div style="overflow:hidden;text-overflow:ellipsis;">${artistTypeDisplay}</div>
      </div>`;
    }).join('');

    body.innerHTML = `<div style="display:grid;grid-auto-rows:auto;row-gap:10px;width:100%;min-width:820px;font-size:0.7rem;">${rows}</div>`;

    if (modalActions) {
      modalActions.innerHTML = `
        <button onclick="openGigModal({date:'${iso}',status:'open'}); document.getElementById('modalOverlay').classList.add('hidden'); document.querySelector('#modalOverlay .modal').classList.remove('day-modal');" class="btn primary">+ Create New Gig</button>
        <button onclick="document.getElementById('modalOverlay').classList.add('hidden'); document.querySelector('#modalOverlay .modal').classList.remove('day-modal');" class="btn ghost">Close</button>
      `;
      modalActions.style.display = '';
    }

    window._venueOpenGigFromDay = function(gigId) {
      const allG = getCachedGigs ? getCachedGigs() : [];
      const gig = allG.find(g => g.id === gigId);
      if (gig) {
        document.getElementById('modalOverlay').classList.add('hidden');
        modal.classList.remove('day-modal');
        setTimeout(() => openGigModal(gig), 50);
      }
    };

    overlay.classList.remove('hidden');
  }
  function _injectVenueActionButtons(gig) {
    // Remove previously injected buttons
    ['_msgArtistBtn','_rateArtistBtn'].forEach(id => { const el = document.getElementById(id); if (el) el.remove(); });
    if (!gig || !gig.id) return;

    const modalActions = document.querySelector('#gigModal .modal-actions');
    if (!modalActions) return;

    const _aname = (gig.artist_name || 'Artist').replace(/['"]/g, '');
    const _aid   = gig.artist_id || null;

    const msgBtn = document.createElement('button');
    msgBtn.id = '_msgArtistBtn';
    msgBtn.className = 'btn ghost';
    msgBtn.style.cssText = 'color:#06b6d4;border-color:rgba(6,182,212,0.4);';
    msgBtn.textContent = 'Message Artist';
    msgBtn.onclick = () => { if (typeof openMessageModal === 'function') openMessageModal(gig.id, _aname, _aid); };

    const rateBtn = document.createElement('button');
    rateBtn.id = '_rateArtistBtn';
    rateBtn.className = 'btn ghost _rateArtistBtn';
    rateBtn.style.cssText = 'color:#f59e0b;border-color:rgba(245,158,11,0.4);';
    rateBtn.textContent = 'Rate Artist';
    rateBtn.dataset.artistId = _aid;
    rateBtn.onclick = () => { if (typeof openReviewModal === 'function') openReviewModal({ artistId: _aid, artistName: gig.artist_name, gigId: gig.id, gigDate: gig.date, gigTitle: gig.title }); };
    _checkAndMarkArtistReviewed(rateBtn, _aid);

    // Insert at front of modal-actions (before Flyer button)
    modalActions.insertBefore(rateBtn, modalActions.firstChild);
    modalActions.insertBefore(msgBtn, modalActions.firstChild);
  }

  async function showSameDayApprovalModal(gig, directSlot) {
    let approvalArtistId = gig.artist_id || (directSlot && directSlot.artist_id);
    let artistName = gig.artist_name || (directSlot && directSlot.artist_name) || 'An artist';
    let slotDesc = '';
    if (directSlot) {
      const t = directSlot.start_time ? ' · ' + formatTime12Hour(directSlot.start_time) : '';
      slotDesc = ' — Slot ' + directSlot.slot_number + t;
    } else if (!approvalArtistId) {
      try {
        const slotsRes = await fetch('/api/gigs/' + gig.id + '/slots', { credentials: 'include' });
        if (slotsRes.ok) {
          const slots = await slotsRes.json();
          const ps = slots.find(s => s.status === 'pending_venue_approval');
          if (ps) { approvalArtistId = ps.artist_id; artistName = ps.artist_name || artistName; slotDesc = ' — Slot ' + ps.slot_number; }
        }
      } catch(e) {}
    }

    const gigDate = gig.date ? new Date(gig.date + 'T00:00:00').toLocaleDateString('en-US', { weekday:'short', month:'short', day:'numeric' }) : '';
    const gigTime = gig.start_time ? formatTime12Hour(gig.start_time) : '';
    const gigTitle = gig.title || gig.artist_type || 'Gig';

    const overlay = document.createElement('div');
    overlay.id = 'approvalOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px;';
    overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid rgba(217,119,6,0.4);border-radius:12px;padding:28px;max-width:440px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5);">'
      + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:20px;">'
      + '<span style="font-size:1.4rem;">⏳</span>'
      + '<h3 style="margin:0;color:#fbbf24;font-size:1.1rem;font-weight:700;">Same-Day Booking Request</h3>'
      + '</div>'
      + '<div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:14px;margin-bottom:16px;">'
      + '<div style="color:#e2e8f0;font-weight:600;margin-bottom:4px;">' + gigTitle + '</div>'
      + '<div style="color:#94a3b8;font-size:0.85rem;">' + gigDate + (gigTime ? ' · ' + gigTime : '') + slotDesc + '</div>'
      + '</div>'
      + '<p style="color:#d1d5db;margin:0 0 20px 0;line-height:1.6;">'
      + '<strong style="color:#fbbf24;">' + artistName + '</strong> is requesting same-day approval to perform at this gig.'
      + '<br/><span style="font-size:0.85rem;color:#9ca3af;">Approve to confirm their booking, or deny to keep the slot open.</span>'
      + '</p>'
      + '<div style="display:flex;gap:10px;">'
      + '<button id="_approveBtn" style="flex:1;padding:12px;background:linear-gradient(135deg,#059669,#047857);color:white;border:none;border-radius:8px;font-weight:700;cursor:pointer;font-size:0.95rem;">✓ Approve</button>'
      + '<button id="_denyBtn" style="flex:1;padding:12px;background:linear-gradient(135deg,#dc2626,#b91c1c);color:white;border:none;border-radius:8px;font-weight:700;cursor:pointer;font-size:0.95rem;">✗ Deny</button>'
      + '</div>'
      + '<button id="_approvalCloseBtn" style="width:100%;margin-top:10px;padding:8px;background:transparent;color:#6b7280;border:1px solid rgba(255,255,255,0.1);border-radius:8px;cursor:pointer;font-size:0.85rem;">Cancel</button>'
      + '</div>';
    document.body.appendChild(overlay);

    const removeOverlay = () => { const el = document.getElementById('approvalOverlay'); if (el) el.remove(); };
    overlay.querySelector('#_approvalCloseBtn').onclick = removeOverlay;
    overlay.addEventListener('click', e => { if (e.target === overlay) removeOverlay(); });

    overlay.querySelector('#_approveBtn').onclick = async () => {
      try {
        const res = await fetch('/api/gigs/' + gig.id + '/approve-booking?artist_id=' + approvalArtistId, { method: 'POST', credentials: 'include' });
        removeOverlay();
        if (res.ok) { showAlert('Booking approved! The artist has been notified.', '✓ Booking Approved'); invalidateGigs(); await renderCalendar(); }
        else { showAlert('Error approving booking. Please try again.', 'Error'); }
      } catch(e) { removeOverlay(); showAlert('Error approving booking.', 'Error'); }
    };

    overlay.querySelector('#_denyBtn').onclick = async () => {
      try {
        const res = await fetch('/api/gigs/' + gig.id + '/deny-booking?artist_id=' + approvalArtistId, { method: 'POST', credentials: 'include' });
        removeOverlay();
        if (res.ok) { showAlert('Booking request denied. The slot is now open again.', 'Booking Denied'); invalidateGigs(); await renderCalendar(); }
        else { showAlert('Error denying booking. Please try again.', 'Error'); }
      } catch(e) { removeOverlay(); showAlert('Error denying booking.', 'Error'); }
    };
  }

    async function openGigModal(gig) {
    selectedGig = gig;
    selectedDate = gig.date;
    // Clean up any previously injected action buttons
    ['_msgArtistBtn','_rateArtistBtn','_venueGigBtnRow','_approveBtn','_denyBtn','editGigBtn'].forEach(id => { const el = document.getElementById(id); if (el) el.remove(); });
    // Restore any hidden permanent modal action buttons
    ['saveGig','cancelGig','deleteGig','flyerGigBtn'].forEach(id => { const el = document.getElementById(id); if (el) el.style.removeProperty('display'); });
  
    // Check if gig is in the past: use gig END moment (UTC) so timezone doesn't hide past-gig UI.
    // DB stores date/time as UTC; comparing calendar date to "today" local can be wrong across TZ.
    const now = new Date();
    const today = new Date(now);
    today.setHours(0, 0, 0, 0);
    let gigDate = null;
    let isPastGig = false;
    
    if (gig.date) {
      const [year, month, day] = gig.date.split('-');
      gigDate = new Date(parseInt(year), parseInt(month) - 1, parseInt(day));
      gigDate.setHours(0, 0, 0, 0);
      // Past only when the gig's calendar date is strictly before today (so "later today" is not past)
      isPastGig = gigDate.getTime() < today.getTime();
    }
  
    // Reset day modal state if it was previously open
    const modal = document.getElementById("gigModal");
    // Don't need to remove day-modal class since we're not using it
    
    const modalBody = document.getElementById('modalBody');
    if (modalBody) {
      modalBody.style.display = 'none';
    }
    
    const modalSection = modal.querySelector('.modal-section');
    if (modalSection) {
      modalSection.style.display = 'block';
    }
    
    const modalTitle = document.getElementById("modalTitle");
    const gigInputFields = document.querySelectorAll(".gig-input-field");
    const gigArtistInfo = document.getElementById("gigArtistInfo");
    const cancelGigBtn = document.getElementById("cancelGig");
  
    const recurringBlock = document.getElementById("recurringBlock");
    if (recurringBlock) {
      recurringBlock.style.display = "block";
    }
    // Also restore the recurring checkbox row (may have been hidden by openBookedGigEdit)
    const recurringRow = document.querySelector('.modal-row:has(#recurringGig)');
    if (recurringRow) recurringRow.style.display = '';
    // Reset recurring state for new gig creation
    if (!gig.id) {
      if (recurringCheckbox) { recurringCheckbox.checked = false; recurringCheckbox.disabled = false; }
      const recurringOptions = document.getElementById('recurringOptions');
      if (recurringOptions) recurringOptions.style.display = 'none';
    }
    // Always clear pending detach flag
    if (selectedGig) selectedGig._pendingDetach = false;
  
    if (gigArtistInfo) {
      gigArtistInfo.innerHTML = "";
      gigArtistInfo.style.display = "none";
    }

    // Reset cancel state (with safety check)
    if (cancelGigBtn) {
      cancelGigBtn.dataset.cancelMode = "false";
      cancelGigBtn.textContent = "Close";
    }
    
    // DEFENSIVE: Reset ALL button states at the start
    // This ensures clean state regardless of previous modal usage
    const cancelPayBtn = document.getElementById('cancelGigPaymentBtn');
    if (cancelPayBtn) cancelPayBtn.style.display = 'none';
    // Flyer button: show for any saved gig, hide for new gig creation
    const flyerGigBtn = document.getElementById('flyerGigBtn');
    if (flyerGigBtn) {
      flyerGigBtn.style.display = gig.id ? 'inline-block' : 'none';
      window._currentGigIdForFlyer = gig.id || null;
    }
    const msgArtistGigBtn = document.getElementById('msgArtistGigBtn');
    if (msgArtistGigBtn) {
      msgArtistGigBtn.style.display = 'none';
      msgArtistGigBtn.onclick = null;
    }
    if (deleteBtn) {
      deleteBtn.classList.add("hidden");
      deleteBtn.textContent = "Delete Gig";
      deleteBtn.disabled = false;
      deleteBtn.dataset.confirmDelete = 'false';
      deleteBtn.dataset.multiSlotDelete = 'false';
      deleteBtn.dataset.multiSlotBookedCount = '0';
    }
    if (saveBtn) {
      saveBtn.style.display = "block";
      saveBtn.disabled = false;
    }
    
    // Clear the artist info section
    if (gigArtistInfo) {
      gigArtistInfo.innerHTML = "";
      gigArtistInfo.style.display = "none";
    }
    
    if (gig.id && gig.status === "cancelled") {
      // Cancelled gig — show read-only view
      modalTitle.textContent = "🚫 Cancelled Gig";
      gigInputFields.forEach(field => field.style.display = "flex");
      if (gigDateInput) gigDateInput.textContent = formatDateForDisplay(gig.date);
      if (saveBtn) saveBtn.style.display = "none";
      if (cancelGigBtn) { cancelGigBtn.textContent = "Close"; cancelGigBtn.dataset.cancelMode = "false"; }
      if (deleteBtn) deleteBtn.classList.remove("hidden");
      const recurBlock = document.getElementById("recurringBlock");
      if (recurBlock) recurBlock.style.display = "none";
      // Show cancelled notice banner
      const existingBanner = document.getElementById('venue-blast-banner');
      if (existingBanner) existingBanner.remove();
      const cancelledBanner = document.createElement('div');
      cancelledBanner.id = 'venue-blast-banner';
      cancelledBanner.style.cssText = 'margin:0 0 16px 0;padding:12px 16px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.4);border-radius:8px;';
      cancelledBanner.innerHTML = '<p style="margin:0;font-size:0.88rem;font-weight:700;color:#f87171;">🚫 This gig has been cancelled</p>';
      modalSection.insertAdjacentElement('beforebegin', cancelledBanner);
      document.getElementById('modalOverlay').classList.remove('hidden');
      return;
    }

    if (gig.id && gig.status === "booked") {
      
      // Hide recurring block if it exists
      const recurringBlock = document.getElementById("recurringBlock");
      if (recurringBlock) {
        recurringBlock.style.display = "none";
      }

      // Show booked gig modal (always uses slot-based view)
      await _showBookedGigModal(gig, isPastGig, modalTitle, gigArtistInfo, deleteBtn, saveBtn, cancelGigBtn);
      modal.classList.remove("hidden");
      return;

    } else if (gig.id && gig.status === "pending_venue_approval") {
      // Render exactly like a booked gig — same info display — but swap action buttons for Approve/Deny

      // Resolve artist info from slot if needed
      const directSlot = gig._pendingSlot || null;
      let approvalArtistId = gig.artist_id || (directSlot && directSlot.artist_id);
      if (!approvalArtistId ) {
        try {
          const slotsRes = await fetch(`/api/gigs/${gig.id}/slots`, { credentials: 'include' });
          if (slotsRes.ok) {
            const slots = await slotsRes.json();
            const ps = slots.find(s => s.status === 'pending_venue_approval');
            if (ps) { approvalArtistId = ps.artist_id; gig = { ...gig, artist_id: ps.artist_id, artist_name: ps.artist_name }; }
          }
        } catch(e) {}
      }
      const slotDesc = directSlot ? ` — Slot ${directSlot.slot_number}` : '';

      // --- Reuse booked gig display ---
      const recurringBlock = document.getElementById("recurringBlock");
      if (recurringBlock) recurringBlock.style.display = "none";
      modalTitle.textContent = "⏳ Pending Approval" + slotDesc;
      let separator = document.querySelector('.modal-separator');
      if (!separator) { separator = document.createElement('div'); separator.className = 'modal-separator'; modalTitle.after(separator); }
      const modalSection = document.querySelector('.modal-section');
      if (modalSection) modalSection.style.display = "none";
      gigInputFields.forEach(field => field.style.display = "none");
      gigArtistInfo.style.display = "block";

      let payDisplay = gig.pay ? '$' + Number(gig.pay).toFixed(2) : 'Not specified';
      try {
        const payRes = await fetch('/api/gigs/' + gig.id + '/effective-pay' + (approvalArtistId ? '?artist_id=' + approvalArtistId : ''), { credentials: 'include' });
        if (payRes.ok) { const pd = await payRes.json(); if (pd.pay != null) payDisplay = '$' + Number(pd.pay).toFixed(2); }
      } catch(e) {}

      let infoHTML = `
        <div style="margin-bottom: 20px; padding: 12px; background: rgba(217,119,6,0.15); border-radius: 8px; border: 1px solid rgba(217,119,6,0.4);">
          <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 4px;">Requesting to book:</div>
          <a href="/app/artist-profile.html?artist_id=${approvalArtistId}" target="_blank"
             style="color: #fbbf24; font-weight: 600; font-size: 1.1rem; text-decoration: none;">
            ${gig.artist_name || 'Unknown Artist'}
          </a>
        </div>
        <div style="display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; font-size: 0.95rem; line-height: 1.6; margin-bottom: 16px;">
          <div style="font-weight: 600; color: var(--text-primary);">Date:</div>
          <div style="color: var(--text-primary);">${formatDateForDisplay(gig.date)}</div>
          <div style="font-weight: 600; color: var(--text-primary);">Time:</div>
          <div style="color: var(--text-primary);">${formatTime12Hour(gig.start_time)} – ${formatTime12Hour(gig.end_time)}</div>
          <div style="font-weight: 600; color: var(--text-primary);">Pay:</div>
          <div style="color: var(--text-primary);">${payDisplay}</div>`;
      if (gig.title) infoHTML += `<div style="font-weight:600;color:var(--text-primary);">Gig Title:</div><div style="color:var(--text-primary);">${esc(gig.title)}</div>`;
      if (gig.artist_type) infoHTML += `<div style="font-weight:600;color:var(--text-primary);">Artist Type:</div><div style="color:var(--text-primary);">${gig.artist_type}</div>`;
      infoHTML += `</div>`;
      infoHTML += `
        <div style="padding: 14px; background: rgba(217,119,6,0.08); border: 1px solid rgba(217,119,6,0.3); border-radius: 8px;">
          <p style="color:#d1d5db; margin:0 0 4px 0; font-size:0.9rem; line-height:1.5;">
            <strong style="color:#fbbf24;">${gig.artist_name || 'This artist'}</strong> is requesting same-day approval${slotDesc}.
          </p>
          <p style="color:#9ca3af; margin:0; font-size:0.82rem;">Approve to confirm their booking, or deny to keep the slot open.</p>
        </div>`;

      // Waitlist section — show any artists waiting even during pending approval
      try {
        const vparams = new URLSearchParams(window.location.search);
        const vid = vparams.get('venue_id');
        if (vid) {
          const wRes = await fetch(`/api/venues/${vid}/gigs/${gig.id}/waitlist`, { credentials: 'include' });
          if (wRes.ok) {
            const wList = await wRes.json();
            if (wList && wList.length > 0) {
              infoHTML += `<div style="margin-top:16px;padding:12px 16px;background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.25);border-radius:8px;">
                <div style="font-size:0.8rem;font-weight:700;color:#a78bfa;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px;">⏳ Waitlisted Artists (${wList.length})</div>` +
                wList.map((w, i) => {
                  const stars = w.avg_rating ? '★'.repeat(Math.round(w.avg_rating)) + ' ' + parseFloat(w.avg_rating).toFixed(1) : '';
                  return `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.06);font-size:0.82rem;">
                    <div><span style="color:var(--text-gray);margin-right:8px;">#${i+1}</span><a href="/app/artist-profile.html?artist_id=${w.artist_id}" target="_blank" style="color:var(--cyan);text-decoration:none;">${esc(w.artist_name || 'Artist')}</a>${w.artist_type ? `<span style="color:var(--text-gray);margin-left:6px;font-size:0.75rem;">${esc(w.artist_type)}</span>` : ''}</div>
                    <span style="color:#f59e0b;font-size:0.72rem;">${stars}</span>
                  </div>`;
                }).join('') +
                '</div>';
            }
          }
        }
      } catch(e) {}

      gigArtistInfo.innerHTML = infoHTML;

      // --- Swap action buttons: hide standard ones, inject Approve/Deny as extras ---
      const modalActions = document.querySelector('#gigModal .modal-actions');
      if (modalActions) {
        // Hide permanent buttons (keep in DOM so next modal open can restore them)
        ['saveGig', 'deleteGig', 'flyerGigBtn'].forEach(id => {
          const el = document.getElementById(id); if (el) el.style.display = 'none';
        });
        const cancelGigBtn2 = document.getElementById('cancelGig');
        if (cancelGigBtn2) cancelGigBtn2.textContent = 'Close';

        const denyBtn = document.createElement('button');
        denyBtn.id = '_denyBtn';
        denyBtn.className = 'btn';
        denyBtn.style.cssText = 'background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.4);';
        denyBtn.textContent = '✗ Deny';
        denyBtn.onclick = async () => {
          denyBtn.disabled = true; denyBtn.textContent = 'Denying...';
          try {
            const res = await fetch(`/api/gigs/${gig.id}/deny-booking?artist_id=${approvalArtistId}`, { method: 'POST', credentials: 'include' });
            modal.classList.add('hidden');
            if (res.ok) { showAlert('Booking request denied. The slot is now open again.', 'Booking Denied'); invalidateGigs(); await renderCalendar(); }
            else { showAlert('Error denying booking. Please try again.', 'Error'); }
          } catch(e) { modal.classList.add('hidden'); showAlert('Error denying booking.', 'Error'); }
        };

        const approveBtn = document.createElement('button');
        approveBtn.id = '_approveBtn';
        approveBtn.className = 'btn primary';
        approveBtn.textContent = '✓ Approve Booking';
        approveBtn.onclick = async () => {
          approveBtn.disabled = true; approveBtn.textContent = 'Approving...';
          try {
            const res = await fetch(`/api/gigs/${gig.id}/approve-booking?artist_id=${approvalArtistId}`, { method: 'POST', credentials: 'include' });
            modal.classList.add('hidden');
            if (res.ok) { showAlert('Booking approved! The artist has been notified.', '✓ Booking Approved'); invalidateGigs(); await renderCalendar(); }
            else { const body = await res.json().catch(() => ({})); showAlert('Error: ' + (body.detail || res.status), 'Approve Failed'); }
          } catch(e) { modal.classList.add('hidden'); showAlert('Error approving booking.', 'Error'); }
        };

        modalActions.appendChild(denyBtn);
        modalActions.appendChild(approveBtn);
      }
      modal.classList.remove("hidden");
      return;

    } else if (gig.id && (
        gig.status === "pending_contract" || gig.status === "awaiting_venue_contract" ||
        (gig.status === "open" && (gig.slots || []).some(s =>
          s.status === 'pending_contract' || s.status === 'awaiting_venue_contract'))
      )) {
      // PENDING CONTRACT — unified modal renderer
      const recurringBlock = document.getElementById("recurringBlock");
      if (recurringBlock) recurringBlock.style.display = "none";
      const modalSection = document.querySelector('.modal-section');
      if (modalSection) modalSection.style.display = "none";
      gigInputFields.forEach(f => f.style.display = "none");
      if (saveBtn) saveBtn.style.display = "none";
      if (deleteBtn) { deleteBtn.classList.add("hidden"); deleteBtn.style.display = "none"; }
      if (cancelGigBtn) cancelGigBtn.textContent = "Close";

      let separator = document.querySelector('.modal-separator');
      if (!separator) {
        separator = document.createElement('div');
        separator.className = 'modal-separator';
        modalTitle.after(separator);
      }

      gigArtistInfo.style.display = "block";
      gigArtistInfo.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:0.9rem;">Loading...</div>';

      try {
        const modalData = await fetchModalData(gig.id, 'venue', venueId);
        const result = await renderGigModal(modalData, {
          onClose: () => modal.classList.add('hidden'),
          onCountersign: (cid) => window._doCountersign && window._doCountersign(cid),
          onUploadVenueContractPdf: (gigId, vid) => { /* handled via _uploadVenueGigPdf */ },
        });
        modalTitle.textContent = "Gig Pending — Contract";
        gigArtistInfo.innerHTML = result.html;
        // Append actions row
        const actionsRow = document.createElement('div');
        actionsRow.id = 'gigModalActionsRow';
        actionsRow.innerHTML = result.actionsHtml;
        gigArtistInfo.appendChild(actionsRow);
      } catch(e) {
        console.error('Venue pending contract modal error:', e);
        gigArtistInfo.innerHTML = `<p style="color:#ef4444;padding:12px;">Failed to load contract details: ${e.message}</p>`;
      }

    } else if (gig.id && gig.status === "open") {
      // OPEN GIG - EDIT MODE (or read-only if past)
      // For multi-slot: if any slot is booked, show booked view not edit form
      const _hasBookedSlot = (gig.slots || []).some(s =>
        s.status === 'booked' || s.status === 'pending_contract'
      );
      if (_hasBookedSlot) {
        await _showBookedGigModal(gig, isPastGig, modalTitle, gigArtistInfo, deleteBtn, saveBtn, cancelGigBtn);
        modal.classList.remove("hidden");
        return;
      }

      _captureRecurringSnapshot(); // snapshot before any form fields are set

      if (isPastGig) {
        // Past gig - always use slot-based view
        await _showBookedGigModal(gig, isPastGig, modalTitle, gigArtistInfo, deleteBtn, saveBtn, cancelGigBtn);
        modal.classList.remove("hidden");
        return;

      } else {
        // Current or future gig - normal edit mode
        
        // Show warning if gig has already started today
        if (isGigStartedToday(gig)) {
          gigArtistInfo.style.display = "block";
          gigArtistInfo.innerHTML = `
            <div style="margin-bottom: 16px; padding: 14px; background: rgba(75, 85, 99, 0.2); border: 1px solid rgba(75, 85, 99, 0.4); border-radius: 8px;">
              <p style="color: #9ca3af; margin: 0; line-height: 1.5; font-size: 0.9rem;">
                <strong>⏰ This gig has already started.</strong> It can no longer be booked by artists.
              </p>
            </div>
          ` + (gigArtistInfo.innerHTML || '');
        }
        
        modalTitle.textContent = "Edit Gig";
        
        // Show the modal-section
        const modalSection = document.querySelector('.modal-section');
        if (modalSection) {
          modalSection.style.display = "block";
        }

        const _bs = window.venueBlinkSettings || {};

        // Waitlist banner — show when waitlist is active (takes priority over blast banner)
        const existingWlBanner = document.getElementById('venue-waitlist-banner');
        if (existingWlBanner) existingWlBanner.remove();
        if (gig.has_active_waitlist) {
          // Fetch waitlist to show artist name
          let wlBannerHtml = '';
          try {
            const wlRes = await fetch(`/api/venues/${gig.venue_id}/gigs/${gig.id}/waitlist`, { credentials: 'include' });
            if (wlRes.ok) {
              const wlList = await wlRes.json();
              if (wlList && wlList.length > 0) {
                const topArtist = wlList[0];
                const offerActive = topArtist.offer_sent && topArtist.offer_expires_at && new Date(topArtist.offer_expires_at) > new Date();
                const remaining = wlList.length;
                wlBannerHtml = `
                  <p style="margin:0;font-size:0.9rem;font-weight:700;color:#a78bfa;">⏳ WAITLIST IN PROGRESS (${remaining} artist${remaining !== 1 ? 's' : ''})</p>
                  <p style="margin:6px 0 0;font-size:0.82rem;color:#c4b5fd;line-height:1.5;">
                    ${offerActive
                      ? `<strong><a href="/app/artist-profile.html?artist_id=${topArtist.artist_id}" target="_blank" style="color:#c4b5fd;">${esc(topArtist.artist_name)}</a></strong> was offered this gig and has ${(() => { try { const _exp = topArtist.offer_expires_at ? new Date(topArtist.offer_expires_at) : null; if (_exp) { const _mins = Math.round((_exp - new Date()) / 60000); if (_mins <= 0) return 'time running out'; if (_mins < 60) return `${_mins} minutes`; const _hrs = Math.round(_mins / 60); return `${_hrs} hour${_hrs !== 1 ? 's' : ''}`; } return (_gs => (_gs - new Date()) / 3600000 <= 36 ? '2 hours' : '24 hours')(new Date(gig.date + 'T' + (gig.start_time || '00:00'))); } catch(_) { return '24 hours'; } })()} to respond.`
                      : `<strong><a href="/app/artist-profile.html?artist_id=${topArtist.artist_id}" target="_blank" style="color:#c4b5fd;">${esc(topArtist.artist_name)}</a></strong> is 1st on the waitlist and will be contacted.`}
                    ${remaining > 1 ? ` ${remaining - 1} more artist${remaining - 1 !== 1 ? 's' : ''} waiting after them.` : ''}
                    ${(() => {
                      const _cb = (_bs || {}).cancelled_blast;
                      const _blastAll = _cb && _cb.blast_all_enabled;
                      const _radius = (_cb && _cb.blast_all_radius) || 20;
                      return _blastAll
                        ? `If no waitlisted artist books, a blast will go to your preferred artists and all artists within a <strong>${_radius} mile</strong> radius.`
                        : `If no waitlisted artist books, a blast will go to your preferred artists.`;
                    })()}
                  </p>
                `;
              }
            }
          } catch(_we) {}
          if (!wlBannerHtml) {
            const _cb2 = (_bs || {}).cancelled_blast;
            const _blastAll2 = _cb2 && _cb2.blast_all_enabled;
            const _radius2 = (_cb2 && _cb2.blast_all_radius) || 20;
            const _blastTxt = _blastAll2
              ? `a blast will go to your preferred artists and all artists within a <strong>${_radius2} mile</strong> radius`
              : `a blast will go to your preferred artists`;
            wlBannerHtml = `<p style="margin:0;font-size:0.9rem;font-weight:700;color:#a78bfa;">⏳ WAITLIST IN PROGRESS</p>
              <p style="margin:6px 0 0;font-size:0.82rem;color:#c4b5fd;line-height:1.5;">Waitlisted artists are being contacted. If no one books, ${_blastTxt} per your Email Center settings.</p>`;
          }
          const wlBanner = document.createElement('div');
          wlBanner.id = 'venue-waitlist-banner';
          wlBanner.style.cssText = 'margin:0 0 16px 0;padding:14px 16px;background:rgba(139,92,246,0.15);border:1px solid rgba(139,92,246,0.5);border-radius:8px;';
          wlBanner.innerHTML = wlBannerHtml;
          modalSection.insertAdjacentElement('beforebegin', wlBanner);
        }

        // Blast/notification banner — content driven by last_notification_key + Email Center settings
        const existingBlastBanner = document.getElementById('venue-blast-banner');
        if (existingBlastBanner) existingBlastBanner.remove();

        const _nk = gig.last_notification_key;
        // Calculate HOURS until gig start — match how the scheduler fires (by start time, not midnight)
        const _gigStartTime = gig.start_time || '19:00';
        const [_sh, _sm] = _gigStartTime.split(':').map(Number);
        const _gigStart = new Date(gig.date + 'T00:00:00');
        _gigStart.setHours(_sh, _sm, 0, 0);
        const _hoursUntil = (_gigStart - new Date()) / 3600000; // hours from now until gig start

        // Determine what banner to show
        let _bannerTitle = null, _bannerBody = null, _bannerColor = '#a78bfa', _bannerBg = 'rgba(99,91,255,0.1)', _bannerBorder = 'rgba(99,91,255,0.3)';

        // Helper: find the next scheduled blast for this gig based on hoursUntil
        // firedKey: the notification_key that already fired — exclude it and anything more urgent
        function _findNextBlast(hoursUntil, firedKey) {
          function _toH(val, unit) { return unit==='hours' ? val : unit==='days' ? val*24 : val*7*24; }
          function _lbl(val, unit) { return unit==='hours' ? `${val}h` : unit==='days' ? `${val} day${val!==1?'s':''}` : `${val} week${val!==1?'s':''}`; }
          const _ord = ['open_gig_36h','open_gig_1w','open_gig_2w','open_gig_4w'];
          // Find the threshold of the already-fired blast — exclude it and anything smaller
          const _firedSettings = firedKey && _bs[firedKey];
          const _firedH = _firedSettings ? _toH(_firedSettings.time_value||1, _firedSettings.time_unit||'weeks') : 0;
          const _th = _ord.filter(k => _bs[k] && _bs[k].enabled)
            .map(k => ({key:k, wh:_toH(_bs[k].time_value||1,_bs[k].time_unit||'weeks'), label:_lbl(_bs[k].time_value||1,_bs[k].time_unit||'weeks')}))
            .filter(t => t.wh > _firedH) // only blasts with LARGER threshold than what already fired
            .sort((a,b) => a.wh-b.wh);
          let next = null;
          for (let i=_th.length-1;i>=0;i--) { if (_th[i].wh < hoursUntil) { next=_th[i]; break; } }
          return next; // null = no more blasts scheduled
        }

        // Returns radius blurb if blast_all is enabled for this key
        function _radiusBlurb(key) {
          const _s = _bs[key];
          if (!_s || !_s.blast_all_enabled) return '';
          const _r = _s.blast_all_radius || 20;
          return ` and all artists within <strong>${_r} miles</strong>`;
        }

        const _sentLabels = {
          'open_gig_4w':     { icon: '📅', title: '4-Week Notice Sent', amber: false },
          'open_gig_2w':     { icon: '📅', title: '2-Week Notice Sent', amber: false },
          'open_gig_1w':     { icon: '⏰', title: '1-Week Notice Sent', amber: true  },
          'open_gig_36h':    { icon: '⚡', title: '36-Hour Blast Sent', amber: true  },
          'radius_blast':    { icon: '⚡', title: 'Radius Blast Sent',  amber: true  },
          'cancelled_blast': { icon: '🔄', title: 'Cancellation Blast Sent', amber: true },
          'new_gig_blast':   { icon: '⚡', title: 'New Gig Blast Sent', amber: true  },
        };

        if (_nk && _sentLabels[_nk]) {
          const _sl = _sentLabels[_nk];
          _bannerColor = _sl.amber ? '#f59e0b' : '#10b981';
          _bannerBg    = _sl.amber ? 'rgba(245,158,11,0.1)' : 'rgba(16,185,129,0.1)';
          _bannerBorder= _sl.amber ? 'rgba(245,158,11,0.4)' : 'rgba(16,185,129,0.4)';
          _bannerTitle = `${_sl.icon} ${_sl.title}`;
          // Find next upcoming blast
          const _nextB = _findNextBlast(_hoursUntil, _nk);
          const _sentRadius = (_nk === 'open_gig_36h' || _nk === 'open_gig_1w') ? _radiusBlurb(_nk) : '';
          const _freqNote = _sentRadius ? ' Frequency limitations and Preferred Status are not in effect.' : '';
          const _whoNotified = `Preferred artists${_sentRadius} were notified.${_freqNote}`;
          if (_nextB) {
            _bannerBody = `${_whoNotified} Next scheduled blast: <strong>${_nextB.label} notice</strong> — will fire automatically if this gig is still open.`;
          } else {
            _bannerBody = `${_whoNotified} No further automated blast emails are scheduled for this gig.`;
          }
        } else if (gig.is_blast_open) {
          // Blast token set but notification_key not recognized
          const _nextB2 = _findNextBlast(_hoursUntil, _nk);
          _bannerTitle = '⚡ Blast Sent';
          _bannerBody = _nextB2
            ? `Artists were notified. Next scheduled blast: <strong>${_nextB2.label} notice</strong>.`
            : 'Artists were notified. No further automated blasts scheduled.';
          _bannerColor = '#f59e0b'; _bannerBg = 'rgba(245,158,11,0.1)'; _bannerBorder = 'rgba(245,158,11,0.4)';
        } else {
          // No notification fired yet — find the NEXT blast that will fire.
          // Compare in HOURS against actual gig start time (how the scheduler works).
          function _toHours(val, unit) {
            if (unit === 'hours') return val;
            if (unit === 'days')  return val * 24;
            return val * 7 * 24;
          }
          function _fmtLabel(val, unit) {
            if (unit === 'hours') return `${val} hour${val !== 1 ? 's' : ''}`;
            if (unit === 'days')  return `${val} day${val !== 1 ? 's' : ''}`;
            return `${val} week${val !== 1 ? 's' : ''}`;
          }
          const _keys = ['open_gig_36h', 'open_gig_1w', 'open_gig_2w', 'open_gig_4w'];
          const _keyMeta = {
            'open_gig_36h': { emoji: '⚡', col: '#f59e0b', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.4)' },
            'open_gig_1w':  { emoji: '⏰', col: '#f59e0b', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.4)' },
            'open_gig_2w':  { emoji: '📅', col: '#10b981', bg: 'rgba(16,185,129,0.1)', border: 'rgba(16,185,129,0.4)' },
            'open_gig_4w':  { emoji: '📅', col: '#10b981', bg: 'rgba(16,185,129,0.1)', border: 'rgba(16,185,129,0.4)' },
          };
          // Build sorted threshold list (ascending hours)
          const _thresholds = _keys
            .filter(k => _bs[k] && _bs[k].enabled)
            .map(k => ({ key: k, wh: _toHours(_bs[k].time_value || 1, _bs[k].time_unit || 'weeks') }))
            .sort((a, b) => a.wh - b.wh);

          // Find largest threshold LESS THAN hoursUntil = next blast to fire as time counts down.
          // A blast can only fire if hoursUntil is STILL ABOVE that threshold.
          // If hoursUntil is already below ALL thresholds, every blast window has passed
          // without firing — show a warning instead of a "coming soon" notice.
          let _nextKey = null;
          for (let _ti = _thresholds.length - 1; _ti >= 0; _ti--) {
            if (_thresholds[_ti].wh < _hoursUntil) { _nextKey = _thresholds[_ti].key; break; }
          }

          // Determine the smallest threshold (most urgent blast)
          const _smallestThreshold = _thresholds.length > 0 ? _thresholds[0] : null;
          const _insideAllWindows = _smallestThreshold && _hoursUntil <= _smallestThreshold.wh;

          if (_insideAllWindows && !_nextKey && _hoursUntil >= 0) {
            // We are past the point where any scheduled blast would fire.
            // The scheduler already missed this window — warn the venue.
            const _missedLabel = _fmtLabel(_smallestThreshold ? (_bs[_smallestThreshold.key].time_value || 1) : 36, _smallestThreshold ? (_bs[_smallestThreshold.key].time_unit || 'hours') : 'hours');
            _bannerTitle = '⚠️ Blast Window Passed';
            _bannerBody = `The automated <strong>${_missedLabel}</strong> blast window has already passed without firing. Use <strong>Email Center</strong> to manually blast this gig to artists now.`;
            _bannerColor = '#ef4444'; _bannerBg = 'rgba(239,68,68,0.08)'; _bannerBorder = 'rgba(239,68,68,0.3)';
          } else if (_nextKey && _hoursUntil >= 0) {
            const _s = _bs[_nextKey];
            const _m = _keyMeta[_nextKey];
            const _label = _fmtLabel(_s.time_value || 1, _s.time_unit || 'weeks');
            _bannerTitle = `${_m.emoji} Next Blast: ${_label} notice`;
            const _upcomingRadius = (_nextKey === 'open_gig_36h' || _nextKey === 'open_gig_1w') ? _radiusBlurb(_nextKey) : '';
            const _upcomingWho = `preferred artists${_upcomingRadius}`;
            const _upcomingFreq = _upcomingRadius ? ' Frequency limitations and Preferred Status will not be in effect.' : '';
            _bannerBody = `This gig will trigger your <strong>${_label}</strong> Email Center blast — ${_upcomingWho} will be emailed automatically when the time comes.${_upcomingFreq}`;
            _bannerColor = _m.col; _bannerBg = _m.bg; _bannerBorder = _m.border;
          }
        }

        if (_bannerTitle && !gig.has_active_waitlist) {
          const blastBanner = document.createElement('div');
          blastBanner.id = 'venue-blast-banner';
          blastBanner.style.cssText = `margin: 0 0 16px 0; padding: 12px 16px; background: ${_bannerBg}; border: 1px solid ${_bannerBorder}; border-radius: 8px;`;
          blastBanner.innerHTML = `
            <p style="margin: 0; font-size: 0.88rem; font-weight: 700; color: ${_bannerColor};">${_bannerTitle}</p>
            <p style="margin: 5px 0 0; font-size: 0.8rem; color: ${_bannerColor}; opacity: 0.85; line-height: 1.5;">${_bannerBody}</p>
          `;
          modalSection.insertAdjacentElement('beforebegin', blastBanner);
        }
        
        // Show all input fields
        gigInputFields.forEach(field => field.style.display = "flex");
        
        // Set date field
        if (gigDateInput) {
          gigDateInput.textContent = formatDateForDisplay(gig.date);
        }
        
        // v97: Show recurring options when editing a recurring gig
        const recurringBlock = document.getElementById("recurringBlock");
        if (gig.recurring_group_id) {
          // Show recurring checkbox row but keep it checked and disabled
          const recurringRow = document.querySelector('.modal-row:has(#recurringGig)');
          if (recurringRow) {
            recurringRow.style.display = "flex";
          }
          recurringCheckbox.checked = true;
          recurringCheckbox.disabled = false; // Allow unchecking to detach from series
          recurringOptions.style.display = "block";
          
          // Pre-fill recurring options from gig data
          document.getElementById('recurWeeks').value = gig.recurring_interval_weeks || 1;
          
          // Set selected days — fall back to gig's actual day of week if none stored
          const selectedDaysRaw = gig.recurring_days_of_week || '';
          const selectedDays = selectedDaysRaw ? selectedDaysRaw.split(',') : [];
          document.getElementById('daySun').checked = selectedDays.includes('0');
          document.getElementById('dayMon').checked = selectedDays.includes('1');
          document.getElementById('dayTue').checked = selectedDays.includes('2');
          document.getElementById('dayWed').checked = selectedDays.includes('3');
          document.getElementById('dayThu').checked = selectedDays.includes('4');
          document.getElementById('dayFri').checked = selectedDays.includes('5');
          document.getElementById('daySat').checked = selectedDays.includes('6');
          // If no days stored, auto-check the day this gig falls on
          if (!selectedDaysRaw.trim()) {
            const _gigDate = new Date(gig.date + 'T00:00:00');
            const _dayMap = ['daySun','dayMon','dayTue','dayWed','dayThu','dayFri','daySat'];
            const _dayEl = document.getElementById(_dayMap[_gigDate.getDay()]);
            if (_dayEl) _dayEl.checked = true;
          }
          
          // Set end type
          const endType = (gig.recurring_end_type && gig.recurring_end_type !== 'never') ? gig.recurring_end_type : 'after';
          document.querySelectorAll('input[name="endType"]').forEach(radio => {
            radio.checked = (radio.value === endType);
          });
          
          endAfterInput.disabled = endType !== 'after';
          endByInput.disabled = endType !== 'by';
          
          if (endType === 'after' && gig.recurring_end_after) {
            endAfterInput.value = gig.recurring_end_after;
          }
          if (endType === 'by' && gig.recurring_end_by_date) {
            endByInput.value = gig.recurring_end_by_date;
          }
          
          // Add series indicator
          gigArtistInfo.style.display = "block";
          gigArtistInfo.innerHTML = `
            <div style="padding: 12px; background: rgba(124, 107, 255, 0.15); border: 1px solid rgba(124, 107, 255, 0.3); border-radius: 8px; margin-bottom: 16px;">
              <div style="font-size: 0.875rem; color: #a78bfa; font-weight: 500; text-align: center;">
                🔁 This gig is part of a recurring series<br>
                <span style="font-size: 0.75rem; opacity: 0.8;">Changes to recurring settings will add/remove gigs (booked gigs won't be deleted)</span>
              </div>
            </div>
          `;
        } else {
          // Non-recurring gig — hide the recurring options panel but SHOW the checkbox
          // so the user can opt to make it recurring
          const recurringBlock2 = document.getElementById("recurringBlock");
          if (recurringBlock2) recurringBlock2.style.display = "none";
          const recurringRow2 = document.querySelector('.modal-row:has(#recurringGig)');
          if (recurringRow2) recurringRow2.style.display = "flex";
          if (recurringCheckbox) { recurringCheckbox.checked = false; recurringCheckbox.disabled = false; }
          if (recurringOptions) recurringOptions.style.display = "none";
          // Clear stale end type values from a previous modal open
          resetEndType();
          endAfterInput.value = '';
          endByInput.value = '';
          // Clear any leftover series banner from previous modal open
          gigArtistInfo.innerHTML = "";
          gigArtistInfo.style.display = "none";
        }
        
        titleInput.value = gig.title || "";
        notesInput.value = gig.notes || "";
        
        artistTypeInput.value = gig.artist_type || "";

        // Per-slot artist type: styles/lineup hidden at modal level
        
        // Populate slot builder for multi-slot gigs (all new gigs)
        resetSlotBuilder();
        const slotBuilder = document.getElementById('slotBuilder');
        if (slotBuilder) {
          slotBuilder.style.display = 'block';
          // Load slots with per-slot artist type data
          try {
            const slots = gig.slots || await api(`/api/gigs/${gig.id}/slots`);
            slots.forEach(s => { if (s.id == null && s.slot_id != null) s.id = s.slot_id; });
            if (slots && slots.length > 0) {
              for (const slot of slots) {
                const payVal = parseFloat(slot.pay) || 0;
                const payD = formatWithCommas(Math.floor(payVal).toString());
                const payC = Math.round((payVal % 1) * 100).toString().padStart(2, '0');
                addSlotRow(slot.start_time, slot.end_time, payD, payC,
                           slot.artist_type || '',
                           slot.band_formats || '',
                           slot.styles || '');
              }
            }
          } catch(e) {
            console.error('Failed to load slots for edit:', e);
            addSlotRow();
          }
        } else if (slotBuilder) {
          // Even non-multi-slot gigs get the slot builder now
          slotBuilder.style.display = 'block';
          addSlotRow('', '', '', '', gig.artist_type || '', gig.band_formats || '', gig.styles || '');
        }

        // Styles/lineup now handled per-slot (populated in addSlotRow above)
        
        // In-progress check: if the gig has started but not ended, hide Save Changes
        // and Delete Gig — editing or deleting a gig that's currently happening is
        // wrong (artist mid-set, mid-set-list emails would be confusing). Same logic
        // applies to both booked and open gigs.
        const _editInProgress = isGigStartedToday(gig) && !isGigEndPassed(gig);
        if (_editInProgress) {
          deleteBtn.classList.add("hidden");
          deleteBtn.style.display = "none";
          saveBtn.style.display = "none";
          modalTitle.textContent = "Gig In Progress";
          if (gigArtistInfo) {
            gigArtistInfo.style.display = "block";
            gigArtistInfo.innerHTML = '<div style="margin-top:12px;padding:12px 14px;background:rgba(75,85,99,0.2);border:1px solid rgba(75,85,99,0.4);border-radius:8px;"><p style="color:#9ca3af;margin:0;font-size:0.9rem;"><strong>⏰ This gig is in progress.</strong> Edit and delete actions are unavailable while the gig is running.</p></div>';
          }
        } else {
          deleteBtn.classList.remove("hidden");
          deleteBtn.style.display = ""; // Clear inline style
          deleteBtn.textContent = "Delete Gig";
          saveBtn.style.display = "block";
          saveBtn.textContent = "Save Changes";
          saveBtn.disabled = false;
        }
        if (cancelGigBtn) {
          cancelGigBtn.textContent = "Close";
        }
      }

    } else {
      // NEW GIG - CREATE MODE
      modalTitle.textContent = "Create Gig";

      // Always remove blast banner when opening in create mode
      const existingBlastBanner = document.getElementById('venue-blast-banner');
      if (existingBlastBanner) existingBlastBanner.remove();
      
      // Show the modal-section
      const modalSection = document.querySelector('.modal-section');
      if (modalSection) {
        modalSection.style.display = "block";
      }
      
      // Show all input fields
      gigInputFields.forEach(field => field.style.display = "flex");
      
      // Set date field
      if (gigDateInput) {
        gigDateInput.textContent = formatDateForDisplay(gig.date);
      }
      
      // Ensure recurring checkbox row is visible for new gigs
      const recurringRow = document.querySelector('.modal-row:has(#recurringGig)');
      if (recurringRow) {
        recurringRow.style.display = "flex";
      }
      
      titleInput.value = "";
      notesInput.value = "";
      
      // Artist type now per-slot; keep hidden global for compat
      artistTypeInput.value = "";
      
      // Reset slot builder and show with default Slot 1
      resetSlotBuilder();
      const slotBuilder = document.getElementById('slotBuilder');
      if (slotBuilder) {
        slotBuilder.style.display = 'block';
        addSlotRow(); // Add default Slot 1
      }

      
      // v93: Reset recurring options
      recurringCheckbox.checked = false;
      recurringOptions.style.display = "none";
      document.getElementById('recurWeeks').value = '1';
      
      // Uncheck all days
      document.getElementById('dayMon').checked = false;
      document.getElementById('dayTue').checked = false;
      document.getElementById('dayWed').checked = false;
      document.getElementById('dayThu').checked = false;
      document.getElementById('dayFri').checked = false;
      document.getElementById('daySat').checked = false;
      document.getElementById('daySun').checked = false;

      // Auto-check the day of week this gig falls on
      if (gigDate) {
        const dayMap = ['daySun','dayMon','dayTue','dayWed','dayThu','dayFri','daySat'];
        const dayEl = document.getElementById(dayMap[gigDate.getDay()]);
        if (dayEl) dayEl.checked = true;
      }

      resetEndType();
      endAfterInput.value = '';
      endByInput.value = '';


      // HIDE delete button for CREATE mode
      deleteBtn.classList.add("hidden");
      deleteBtn.style.display = "none"; // Force hide with inline style too
      
      saveBtn.style.display = "block";
      saveBtn.textContent = "Create Gig";
      saveBtn.disabled = false;
      if (cancelGigBtn) {
        cancelGigBtn.textContent = "Close";
      }
    }
  
    modal.classList.remove("hidden");
  }

  // ===================================
  // MULTI-SLOT GIG VIEW (VENUE SIDE)
  // ===================================
  // Edit Gig from Booked Gig Details modal
  // Reuses existing slot builder UI + title input + updateSingleGig
  async function openBookedGigEdit(gig) {
    const gigArtistInfo = document.getElementById('gigArtistInfo');
    const saveBtn = document.getElementById('saveGig');
    const editGigBtn = document.getElementById('editGigBtn');
    const deleteBtn = document.getElementById('deleteGig');
    const modalSection = document.querySelector('.modal-section');
    const modalTitle = document.getElementById('modalTitle');

    // Show the modal section (slot builder + title live in there)
    if (modalSection) modalSection.style.display = 'block';

    // Show title, slot builder, notes; hide recurring block
    const recurringBlock = document.getElementById('recurringBlock');
    if (recurringBlock) recurringBlock.style.display = 'none';
    const recurringOptions = document.getElementById('recurringOptions');
    if (recurringOptions) recurringOptions.style.display = 'none';
    const recurringRow = document.querySelector('.modal-row:has(#recurringGig)');
    if (recurringRow) recurringRow.style.display = 'none';

    // Show title row
    const titleRow = document.getElementById('gigTitle')?.closest('.modal-row');
    if (titleRow) titleRow.style.display = '';
    if (document.getElementById('gigTitle')) document.getElementById('gigTitle').value = gig.title || '';

    // Show slot builder
    const slotBuilder = document.getElementById('slotBuilder');
    if (slotBuilder) slotBuilder.style.display = 'block';

    // Show notes
    const notesRow = document.getElementById('gigNotes')?.closest('.modal-row');
    if (notesRow) notesRow.style.display = '';
    if (document.getElementById('gigNotes')) document.getElementById('gigNotes').value = gig.notes || '';

    // Clear and pre-populate slot builder from booked slots
    const slotList = document.getElementById('slotList');
    if (slotList) slotList.innerHTML = '';
    slotCounter = 0;

    let slots = [];
    try {
      slots = gig.slots || await api(`/api/gigs/${gig.id}/slots`);
      // Cached gig.slots from /venues/{vid}/gigs aliases gs.id as slot_id, while
      // /api/gigs/{id}/slots returns gs.id directly. Normalize so downstream code
      // can read slot.id consistently regardless of source.
      slots.forEach(s => { if (s.id == null && s.slot_id != null) s.id = s.slot_id; });
    } catch(e) { console.error('Failed to load slots for edit:', e); }

    // Helper: for booked slots, lock artist type/styles/lineup — only time+pay editable
    function addBookedSlotRow(slot) {
      const pay = parseFloat(slot.pay) || 0;
      const payD = Math.floor(pay).toString();
      const payC = Math.round((pay - Math.floor(pay)) * 100).toString().padStart(2, '0');
      addSlotRow(slot.start_time, slot.end_time, payD, payC,
        slot.artist_type || gig.artist_type || '',
        slot.band_formats || gig.band_formats || '',
        slot.styles || gig.styles || '');
      // Lock: hide artist type / styles / lineup rows on the last-added slot row
      const slotList = document.getElementById('slotList');
      const lastRow = slotList ? slotList.lastElementChild : null;
      if (lastRow) {
        // Hide artist type select row
        const typeRow = lastRow.querySelector('.slot-artist-type')?.closest('div');
        if (typeRow) typeRow.style.display = 'none';
        // Hide styles row
        const stylesRow = lastRow.querySelector('.slot-styles-row');
        if (stylesRow) stylesRow.style.display = 'none';
        // Hide lineup row
        const lineupRow = lastRow.querySelector('.slot-lineup-row');
        if (lineupRow) lineupRow.style.display = 'none';
        // Mark as booked so validateSlotArtistTypes skips it
        lastRow.dataset.isBooked = 'true';
        // Show artist row below slot header line (matches Gig Details layout)
        if (slot.artist_name) {
          const _aname = slot.artist_name.replace(/['"]/g, '');
          const artistRow = document.createElement('div');
          artistRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:6px;flex-wrap:wrap;width:100%;';

          const artistLabel = document.createElement('span');
          artistLabel.style.cssText = 'font-size:0.78rem;color:var(--text-muted);white-space:nowrap;';
          artistLabel.textContent = 'Artist:';
          artistRow.appendChild(artistLabel);

          const artistLink = document.createElement('a');
          artistLink.href = `/app/artist-profile.html?artist_id=${slot.artist_id}`;
          artistLink.target = '_blank';
          artistLink.style.cssText = 'color:#ef4444;font-weight:600;font-size:0.82rem;text-decoration:none;flex:1;';
          artistLink.textContent = slot.artist_name;
          artistRow.appendChild(artistLink);

          if (gig && gig.id) {
            const msgBtn = document.createElement('button');
            msgBtn.style.cssText = 'background:transparent;border:1px solid rgba(6,182,212,0.4);color:#06b6d4;border-radius:4px;padding:3px 10px;font-size:0.75rem;cursor:pointer;white-space:nowrap;';
            msgBtn.textContent = 'Message Artist';
            msgBtn.onclick = () => { if (typeof openMessageModal === 'function') openMessageModal(gig.id, _aname, slot.artist_id); };
            artistRow.appendChild(msgBtn);

            const rateBtn = document.createElement('button');
            const _gigTitle = (gig.title||'').replace(/'/g, "\'");
            rateBtn.className = '_rateArtistBtn';
            rateBtn.style.cssText = 'background:transparent;border:1px solid rgba(245,158,11,0.4);color:#f59e0b;border-radius:4px;padding:3px 10px;font-size:0.75rem;cursor:pointer;white-space:nowrap;';
            rateBtn.textContent = 'Rate Artist';
            rateBtn.dataset.artistId = slot.artist_id;
            rateBtn.onclick = () => { if (typeof openReviewModal === 'function') openReviewModal({ artistId: slot.artist_id, artistName: slot.artist_name, gigId: gig.id, gigDate: gig.date, gigTitle: gig.title }); };
            _checkAndMarkArtistReviewed(rateBtn, slot.artist_id);
            artistRow.appendChild(rateBtn);
          }

          lastRow.appendChild(artistRow);
        }
      }
    }

    if (slots.length > 0) {
      slots.forEach(slot => {
        if (slot.status === 'booked') {
          addBookedSlotRow(slot);
        } else {
          // Open slot: full editable
          const pay = parseFloat(slot.pay) || 0;
          const payD = Math.floor(pay).toString();
          const payC = Math.round((pay - Math.floor(pay)) * 100).toString().padStart(2, '0');
          addSlotRow(slot.start_time, slot.end_time, payD, payC,
            slot.artist_type || gig.artist_type || '',
            slot.band_formats || gig.band_formats || '',
            slot.styles || gig.styles || '');
        }
      });
    } else {
      const pay = parseFloat(gig.pay) || 0;
      const payD = Math.floor(pay).toString();
      const payC = Math.round((pay - Math.floor(pay)) * 100).toString().padStart(2, '0');
      addSlotRow(gig.start_time, gig.end_time, payD, payC, gig.artist_type || '', gig.band_formats || '', gig.styles || '');
    }

    // Mark selectedGig as booked-edit mode ONLY if there are actually booked slots.
    // For open recurring gigs, keep _isBookedEdit false so the series modal fires
    // and PUT /gigs/{id} runs — saving all fields (artist_type, styles, lineup, pay, etc.)
    const hasBookedSlots = slots.some(s => s.status === 'booked');
    selectedGig = Object.assign({}, gig, { _isBookedEdit: hasBookedSlots });

    // Defense-in-depth: even though the Edit Gig button is hidden for in-progress
    // booked gigs, hide Save Changes here too in case of a race or alternate entry path.
    const _bookedInProgress = isGigStartedToday(gig) && !isGigEndPassed(gig);
    if (gigArtistInfo) gigArtistInfo.style.display = 'none';
    if (saveBtn) {
      if (_bookedInProgress) {
        saveBtn.style.display = 'none';
      } else {
        saveBtn.style.display = 'block';
        saveBtn.textContent = 'Save Changes';
      }
    }
    if (editGigBtn) editGigBtn.style.display = 'none';
    if (deleteBtn) deleteBtn.style.display = 'none';
    modalTitle.textContent = _bookedInProgress ? 'Gig In Progress' : 'Edit Gig';
    if (_bookedInProgress && gigArtistInfo) {
      gigArtistInfo.style.display = 'block';
      gigArtistInfo.innerHTML = '<div style="margin-top:12px;padding:12px 14px;background:rgba(75,85,99,0.2);border:1px solid rgba(75,85,99,0.4);border-radius:8px;"><p style="color:#9ca3af;margin:0;font-size:0.9rem;"><strong>⏰ This gig is in progress.</strong> Edit actions are unavailable while the gig is running.</p></div>';
    }
  }

  // ─── WAITLIST VIEWER FOR VENUE ────────────────────────────────────────────────
async function _renderWaitlistBtn(gigId, slotId, venueId, slotNum) {
  try {
    const res = await fetch(`/api/venues/${venueId}/gigs/${gigId}/waitlist`, { credentials: 'include' });
    if (!res.ok) return '';
    const list = await res.json();
    if (!list || list.length === 0) return '';
    return `<div style="padding-left:62px;margin-top:5px;">
      <button onclick="openWaitlistModal(${gigId},${venueId},'Slot ${slotNum}')"
        style="background:transparent;border:1px solid rgba(168,85,247,0.4);color:#a855f7;border-radius:4px;padding:3px 10px;font-size:0.75rem;cursor:pointer;white-space:nowrap;">
        👥 View Waitlist (${list.length})
      </button>
    </div>`;
  } catch(e) { return ''; }
}

window.openWaitlistModal = async function(gigId, venueId, slotLabel) {
  // Remove any existing waitlist modal
  document.getElementById('_wl-modal-overlay')?.remove();

  const overlay = document.createElement('div');
  overlay.id = '_wl-modal-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.65);z-index:10000;display:flex;align-items:center;justify-content:center;';

  const modal = document.createElement('div');
  modal.style.cssText = 'background:var(--bg-card,#1e1e2e);border:1px solid rgba(255,255,255,0.12);border-radius:12px;padding:28px 32px;width:680px;max-width:95vw;max-height:80vh;overflow-y:auto;position:relative;color:var(--text-primary,#e2e8f0);';

  modal.innerHTML = `
    <button onclick="document.getElementById('_wl-modal-overlay').remove()"
      style="position:absolute;top:14px;right:16px;background:none;border:none;color:var(--text-muted,#94a3b8);font-size:1.3rem;cursor:pointer;line-height:1;">×</button>
    <h3 style="margin:0 0 4px;font-size:1.1rem;font-weight:700;">👥 Waitlist — ${slotLabel || 'This Gig'}</h3>
    <p style="margin:0 0 18px;font-size:0.82rem;color:var(--text-muted,#94a3b8);">Artists will be offered the slot in order if a booking cancels.</p>
    <div id="_wl-list-body" style="font-size:0.88rem;">Loading...</div>
  `;

  overlay.appendChild(modal);
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

  try {
    const res = await fetch(`/api/venues/${venueId}/gigs/${gigId}/waitlist`, { credentials: 'include' });
    const list = res.ok ? await res.json() : [];
    const body = document.getElementById('_wl-list-body');
    if (!list || list.length === 0) {
      body.innerHTML = '<em style="color:var(--text-muted,#94a3b8);">No artists on the waitlist.</em>';
      return;
    }
    const now = Date.now();
    let rows = list.map((entry, idx) => {
      const pos = entry.position || (idx + 1);
      const offerActive = entry.offer_sent && entry.offer_expires_at && new Date(entry.offer_expires_at).getTime() > now;
      const offerExpired = entry.offer_sent && entry.offer_expires_at && new Date(entry.offer_expires_at).getTime() <= now;
      const declined = entry.offer_declined;
      let badge = '';
      if (offerActive) badge = `<span style="background:rgba(34,197,94,0.15);color:#22c55e;border:1px solid #22c55e55;border-radius:4px;padding:1px 7px;font-size:0.72rem;margin-left:8px;">⏰ Offer Sent</span>`;
      else if (declined) badge = `<span style="background:rgba(239,68,68,0.1);color:#ef4444;border:1px solid #ef444455;border-radius:4px;padding:1px 7px;font-size:0.72rem;margin-left:8px;">Declined</span>`;
      else if (offerExpired) badge = `<span style="background:rgba(245,158,11,0.1);color:#f59e0b;border:1px solid #f59e0b55;border-radius:4px;padding:1px 7px;font-size:0.72rem;margin-left:8px;">Expired</span>`;
      return `<div style="display:flex;align-items:center;gap:12px;padding:9px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
        <span style="font-weight:700;color:var(--accent-cyan,#06b6d4);min-width:22px;text-align:right;">${pos}</span>
        <a href="/app/artist-profile.html?artist_id=${entry.artist_id}" target="_blank"
           style="color:var(--text-primary,#e2e8f0);text-decoration:none;flex:1;font-weight:600;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'">${entry.artist_name}</a>
        ${badge}
      </div>`;
    }).join('');
    body.innerHTML = `
      <div style="display:flex;gap:12px;padding-bottom:8px;margin-bottom:4px;font-size:0.75rem;font-weight:700;color:var(--text-muted,#94a3b8);border-bottom:1px solid rgba(255,255,255,0.1);">
        <span style="min-width:22px;text-align:right;">#</span>
        <span style="flex:1;">Artist</span>
        <span style="min-width:80px;"></span>
      </div>
      ${rows}
    `;
  } catch(e) {
    document.getElementById('_wl-list-body').innerHTML = '<em style="color:#ef4444;">Error loading waitlist.</em>';
  }
};

async function _showBookedGigModal(gig, isPastGig, modalTitle, gigArtistInfo, deleteBtn, saveBtn, cancelGigBtn) {
    const icons = { 'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤', 'Trivia Host': '🧠' };
    const icon = icons[gig.artist_type] || '🎵';
    const eventLabel = gig.title || `${gig.artist_type || 'Multi-Slot'} Night`;
    modalTitle.textContent = isPastGig ? `Past Event Details` : `Gig Details`;
    
    // Hide the form section
    const modalSection = document.querySelector('.modal-section');
    if (modalSection) modalSection.style.display = "none";
    const gigInputFields = document.querySelectorAll(".gig-input-field");
    gigInputFields.forEach(field => field.style.display = "none");
    
    // Use cached slots from gig object (or fetch if missing)
    let slots = [];
    try {
      slots = gig.slots || await api(`/api/gigs/${gig.id}/slots`);
      // Cached gig.slots from /venues/{vid}/gigs aliases gs.id as slot_id, while
      // /api/gigs/{id}/slots returns gs.id directly. Normalize so downstream code
      // can read slot.id consistently regardless of source.
      slots.forEach(s => { if (s.id == null && s.slot_id != null) s.id = s.slot_id; });
    } catch(e) {
      console.error('Failed to load slots:', e);
    }
    
    const bookedCount = slots.filter(s => s.status === 'booked').length;
    const totalSlots = slots.length;
    
    // Use effective pay (venue override for artist) when we have an artist; otherwise gig base pay
    let payDisplay = gig.pay != null && gig.pay !== '' ? '$' + parseFloat(gig.pay).toFixed(2) : '';
    const artistIdForPay = gig.artist_id || (slots.find(s => s.status === 'booked') || {}).artist_id;
    if (artistIdForPay) {
      try {
        const payRes = await fetch('/api/gigs/' + gig.id + '/effective-pay?artist_id=' + artistIdForPay, { credentials: 'include' });
        if (payRes.ok) {
          const payData = await payRes.json();
          if (payData.pay != null) payDisplay = '$' + Number(payData.pay).toFixed(2);
        }
      } catch (e) { console.error('Effective pay (multi-slot):', e); }
    }
    
    gigArtistInfo.style.display = "block";
    
    let html = `
      <div style="display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; font-size: 0.95rem; line-height: 1.6; margin-bottom: 16px;">
        <div style="font-weight: 600;">Date:</div>
        <div>${formatDateForDisplay(gig.date)}</div>
        <div style="font-weight: 600;">Event:</div>
        <div>${eventLabel}</div>
        ${payDisplay ? `<div style="font-weight: 600;">Pay:</div><div>${payDisplay}</div>` : ''}
      </div>
    `;
    
    const typeIcons = { 'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤', 'Trivia Host': '🧠' };
    // Always sort by start_time ascending before rendering
    slots.sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));

    // Pre-fetch waitlist HTML for each booked slot (async, before building HTML string)
    const _slotWaitlistHtml = {};
    for (const slot of slots) {
      if (slot.status === 'booked') {
        _slotWaitlistHtml[slot.id] = await _renderWaitlistBtn(gig.id, slot.id, gig.venue_id, slot.slot_number);
      }
    }

    for (const slot of slots) {
      const isBooked = slot.status === 'booked';
      const color = isBooked ? '#ef4444' : '#22c55e';
      const bg = isBooked ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)';
      const slotType = slot.artist_type || gig.artist_type || '';
      const slotIcon = typeIcons[slotType] || '🎵';
      let typeInfo = slotType ? `${slotIcon} ${slotType}` : '';
      if (slotType === 'Live Band') {
        const fmts = slot.band_formats || gig.band_formats || '';
        const stls = slot.styles || gig.styles || '';
        if (fmts) typeInfo += ` · ${fmts}`;
        if (stls) typeInfo += ` · ${stls}`;
      }

      if (isBooked) {
        const _aname = (slot.artist_name || 'Artist').replace(/['"]/g, '');
        const cancelBtn = !isPastGig
          ? `<button onclick="cancelSlotBooking(${gig.id}, ${slot.id}, ${slot.slot_number}, ${slot.artist_id || 'null'})"
               style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:3px 9px;font-size:0.75rem;cursor:pointer;white-space:nowrap;"
               title="Cancel this slot booking">✕</button>`
          : '';
        const msgBtn = `<button onclick="if(typeof openMessageModal==='function') openMessageModal(${gig.id},'${_aname}',${slot.artist_id})"
          style="background:transparent;border:1px solid rgba(6,182,212,0.4);color:#06b6d4;border-radius:4px;padding:3px 10px;font-size:0.75rem;cursor:pointer;white-space:nowrap;">Message Artist</button>`;
        const rateBtn = `<button class="_rateArtistBtn" data-artist-id="${slot.artist_id}" onclick="if(typeof openReviewModal==='function') openReviewModal({artistId:${slot.artist_id},artistName:'${_aname}',gigId:${gig.id},gigDate:'${gig.date}',gigTitle:'${(gig.title||'').replace(/'/g,"\'")}'})"
          style="background:transparent;border:1px solid rgba(245,158,11,0.4);color:#f59e0b;border-radius:4px;padding:3px 10px;font-size:0.75rem;cursor:pointer;white-space:nowrap;">Rate Artist</button>`;

        html += `
          <div style="padding:9px 14px;margin-bottom:6px;background:${bg};border:1px solid ${color}33;border-radius:6px;font-size:0.85rem;">
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
              <span style="font-weight:700;min-width:56px;">Slot ${slot.slot_number}</span>
              <span style="min-width:140px;">${formatTime12Hour(slot.start_time)} – ${formatTime12Hour(slot.end_time)}</span>
              ${typeInfo ? `<span style="color:var(--text-muted);font-size:0.78rem;flex:1;">${typeInfo}</span>` : '<span style="flex:1;"></span>'}
              ${cancelBtn}
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-top:6px;padding-left:62px;">
              <span style="color:var(--text-muted);font-size:0.78rem;margin-right:4px;">Artist:</span>
              <a href="/app/artist-profile.html?artist_id=${slot.artist_id}" target="_blank"
                 style="color:${color};font-weight:600;font-size:0.82rem;text-decoration:none;flex:1;">${slot.artist_name}</a>
              ${msgBtn}
              ${rateBtn}
            </div>
            ${_slotWaitlistHtml[slot.id] || ''}
          </div>
        `;
      } else {
        html += `
          <div style="display:flex;align-items:center;padding:9px 14px;margin-bottom:6px;background:${bg};border:1px solid ${color}33;border-radius:6px;font-size:0.85rem;gap:6px;flex-wrap:wrap;">
            <span style="font-weight:700;min-width:56px;">Slot ${slot.slot_number}</span>
            <span style="min-width:140px;">${formatTime12Hour(slot.start_time)} – ${formatTime12Hour(slot.end_time)}</span>
            ${typeInfo ? `<span style="color:var(--text-muted);font-size:0.78rem;flex:1;">${typeInfo}</span>` : '<span style="flex:1;"></span>'}
            <span style="color:#22c55e;font-weight:600;font-size:0.85rem;">Open</span>
          </div>
        `;
      }
    }
    
    // Notes for Artist - always shown
    html += `
      <div style="margin-top: 12px; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 12px;">
        <label style="display:block; font-weight:600; font-size:0.85rem; color:var(--text-primary); margin-bottom:6px;">Notes for Artist</label>
        <textarea id="bookedGigNotes" rows="3"
          style="width:100%; padding:10px 12px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.15); border-radius:6px; color:var(--text-primary); font-family:inherit; font-size:0.9rem; resize:vertical; box-sizing:border-box;"
          placeholder="Add notes for the artist (e.g. contact info, load-in details, set times...)"
        >${gig.notes || ''}</textarea>
        <div style="display:flex; align-items:center; gap:12px; margin-top:8px; margin-bottom:20px;">
          <button onclick="updateBookedGigNotes(${gig.id})" class="btn primary" style="padding:6px 16px; font-size:0.8rem;">Update Notes</button>
          <span id="bookedNotesStatus" style="font-size:0.75rem; color:var(--accent-cyan,#06b6d4); opacity:0; transition:opacity 0.3s;">✓ Notes saved</span>
        </div>
      </div>
    `;
    
    gigArtistInfo.innerHTML = html;

    // Check each Rate Artist button and update label if already reviewed
    if (typeof _checkAndMarkArtistReviewed === 'function') {
      gigArtistInfo.querySelectorAll('._rateArtistBtn').forEach(btn => {
        const aid = btn.dataset.artistId || btn.getAttribute('data-artist-id');
        if (aid) _checkAndMarkArtistReviewed(btn, parseInt(aid));
      });
    }

    // Button state: use isGigEndPassed() which handles overnight gigs correctly
    saveBtn.style.display = "none";
    let multiGigHasEnded = isPastGig || isGigEndPassed(gig);
    if (multiGigHasEnded) {
      deleteBtn.classList.add("hidden");
      deleteBtn.style.display = "none";
      // Gig ended: show Cancel Payment only until 5pm next calendar day
      if (bookedCount > 0) {
        try {
          const now = new Date();
          const gigDayStart = gig.date ? new Date(gig.date + 'T00:00:00') : null;
          const cutoff = gigDayStart ? new Date(gigDayStart) : null;
          if (cutoff) { cutoff.setDate(cutoff.getDate() + 1); cutoff.setHours(17, 0, 0, 0); }
          const inCancelWindow = multiGigHasEnded && cutoff && (now.getTime() < cutoff.getTime());
          const txnRes = await fetch('/api/stripe/gig/' + gig.id + '/transaction-status', { credentials: 'include' });
          const txnData = txnRes.ok ? await txnRes.json() : {};
          const alreadyCancelled = txnData.has_transaction && txnData.status === 'payment_cancelled';
          if (inCancelWindow && !alreadyCancelled) {
            const container = (cancelGigBtn && cancelGigBtn.parentElement) ? cancelGigBtn.parentElement : document.querySelector('#gigModal .modal-actions');
            if (container) {
              let cancelPayBtn = document.getElementById('cancelGigPaymentBtn');
              if (!cancelPayBtn) {
                cancelPayBtn = document.createElement('button');
                cancelPayBtn.id = 'cancelGigPaymentBtn';
                cancelPayBtn.className = 'btn';
                cancelPayBtn.style.cssText = 'background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.3);padding:8px 16px;font-size:0.85rem;border-radius:6px;cursor:pointer;';
                container.insertBefore(cancelPayBtn, container.querySelector('#cancelGig') || container.lastElementChild);
              }
              cancelPayBtn.textContent = 'Cancel Gig Payment?';
              cancelPayBtn.style.display = '';
              cancelPayBtn.onclick = () => window._showCancelPaymentModal(gig.id);
            }
          } else {
            const cancelPayBtn = document.getElementById('cancelGigPaymentBtn');
            if (cancelPayBtn) cancelPayBtn.style.display = 'none';
          }
        } catch (e) { console.error('Txn status check (multi-slot):', e); }
      }
    } else {
      const _multiHasStarted = isGigStartedToday(gig);
      if (_multiHasStarted) {
        deleteBtn.classList.add("hidden");
        deleteBtn.style.display = "none";
        modalTitle.textContent = "Gig In Progress";
        const _inProg2 = document.getElementById('_gigInProgressNotice');
        if (!_inProg2) {
          const _n2 = document.createElement('div');
          _n2.id = '_gigInProgressNotice';
          _n2.style.cssText = 'margin-top:12px;padding:12px 14px;background:rgba(75,85,99,0.2);border:1px solid rgba(75,85,99,0.4);border-radius:8px;';
          _n2.innerHTML = '<p style="color:#9ca3af;margin:0;font-size:0.9rem;"><strong>⏰ This gig is in progress.</strong> Cancel and edit actions are unavailable while the gig is running.</p>';
          gigArtistInfo.appendChild(_n2);
        }
      } else {
        deleteBtn.classList.remove("hidden");
        deleteBtn.style.display = "inline-block";
        deleteBtn.style.visibility = "visible";
        deleteBtn.textContent = bookedCount > 0 ? "Cancel Gig" : "Delete Event";
      }
      deleteBtn.disabled = false;
      deleteBtn.dataset.cancelMode = "false";
      deleteBtn.dataset.multiSlotDelete = 'true';
      deleteBtn.dataset.multiSlotBookedCount = bookedCount;
      var modalActions = document.querySelector('#gigModal .modal-actions');
      if (modalActions) { modalActions.style.display = 'flex'; modalActions.style.visibility = 'visible'; }
      const cancelPayBtn = document.getElementById('cancelGigPaymentBtn');
      if (cancelPayBtn) cancelPayBtn.style.display = 'none';
      // Add Edit Gig button (green, left of Cancel/Delete) — but NOT when gig is in progress
      // FIX (May 2026): edits to in-progress gigs make no sense (gig is currently happening,
      // emails would say "we changed your gig" mid-performance). Hide the button entirely.
      let editGigBtn = document.getElementById('editGigBtn');
      if (_multiHasStarted) {
        if (editGigBtn) editGigBtn.style.display = 'none';
      } else {
        if (!editGigBtn) {
          editGigBtn = document.createElement('button');
          editGigBtn.id = 'editGigBtn';
          editGigBtn.className = 'btn';
          editGigBtn.style.cssText = 'background:rgba(34,197,94,0.15);color:#22c55e;border:1px solid rgba(34,197,94,0.4);';
          editGigBtn.textContent = 'Edit Gig';
          if (modalActions) modalActions.insertBefore(editGigBtn, deleteBtn);
        }
        editGigBtn.style.display = '';
        editGigBtn.onclick = () => openBookedGigEdit(gig);
      }
    }
    if (cancelGigBtn) cancelGigBtn.textContent = "Close";
    // Note: Message/Rate buttons are per-slot for multi-slot gigs (rendered inline above)
  }
  
  // Global: Cancel/delete a single slot from venue view
  window.cancelSlotBooking = async function(gigId, slotId, slotNum, hintArtistId = null) {
    // Get slot info and total slot count for this gig
    let slots = [];
    try {
      const slotsRes = await fetch(`/api/gigs/${gigId}/slots`, { credentials: 'include' });
      if (slotsRes.ok) slots = await slotsRes.json();
    } catch(e) {}

    // A slot has an artist on it whenever artist_id is set — regardless of whether
    // the slot's status is 'booked', 'pending_contract', 'awaiting_venue_contract',
    // etc. The previous `status === 'booked'` check sent any in-transit slot down
    // the open-slot path and showed the wrong modal.
    const slot = slots.find(s => s.id === slotId);
    const isBooked = slot
      ? (slot.artist_id != null)
      : (hintArtistId != null); // fallback when slots fetch failed
    const totalSlots = slots.length || 1;
    const artistName = (slot && slot.artist_name) || 'the artist';

    // ── Shared helper: styled confirmation overlay ────────────────────────
    // Returns a Promise that resolves to { reason, keepOpen } on confirm, or null on cancel.
    // keepOpenOption: whether to show the Keep Open / Delete radio choice.
    // keepOpenOption: gig-level cancel — choose between keep-open vs delete-whole-gig.
    // slotMode:       slot-level cancel — choose between keep-slot-open vs remove-slot
    //                 from the gig (canRemove gates the remove option when only 1 slot
    //                 remains, since removing the last slot would leave an empty gig).
    function _showCancelOverlay({ title, body, reasonPlaceholder, keepOpenOption = false, slotMode = false, canRemove = true }) {
      return new Promise(resolve => {
        const ov = document.createElement('div');
        ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:10000;display:flex;align-items:center;justify-content:center;';
        const showRadios = keepOpenOption || slotMode;
        const slotOptionsHtml = `
            <div style="margin-bottom:16px;">
              <div style="font-weight:600;color:#fff;font-size:0.82rem;margin-bottom:8px;">After cancelling:</div>
              <label style="display:flex;align-items:center;gap:8px;margin-bottom:7px;cursor:pointer;font-size:0.88rem;color:#d1d5db;">
                <input type="radio" name="_slotCancelMode" value="keep_open" checked style="accent-color:#22c55e;width:15px;height:15px;">
                Keep slot open <span style="color:#22c55e;font-weight:600;">(re-list as available to book)</span>
              </label>
              ${canRemove ? `
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.88rem;color:#d1d5db;">
                <input type="radio" name="_slotCancelMode" value="remove_slot" style="accent-color:#ef4444;width:15px;height:15px;">
                Remove this slot <span style="color:#ef4444;font-weight:600;">(slot deleted from gig)</span>
              </label>` : ''}
            </div>`;
        const gigOptionsHtml = `
            <div style="margin-bottom:16px;">
              <div style="font-weight:600;color:#fff;font-size:0.82rem;margin-bottom:8px;">After cancelling:</div>
              <label style="display:flex;align-items:center;gap:8px;margin-bottom:7px;cursor:pointer;font-size:0.88rem;color:#d1d5db;">
                <input type="radio" name="_slotCancelMode" value="keep_open" checked style="accent-color:#22c55e;width:15px;height:15px;">
                Keep gig open <span style="color:#22c55e;font-weight:600;">(re-list slot as available)</span>
              </label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.88rem;color:#d1d5db;">
                <input type="radio" name="_slotCancelMode" value="delete_gig" style="accent-color:#ef4444;width:15px;height:15px;">
                Delete entire gig <span style="color:#ef4444;font-weight:600;">(remove from calendar)</span>
              </label>
            </div>`;
        ov.innerHTML = `
          <div style="background:#1a1a2e;border:1px solid rgba(255,255,255,0.12);border-radius:12px;padding:24px;max-width:440px;width:92%;box-shadow:0 16px 48px rgba(0,0,0,0.5);">
            <h3 style="margin:0 0 12px;color:#ef4444;font-size:1.05rem;font-weight:700;">${title}</h3>
            <p style="color:#e2e8f0;margin:0 0 14px;line-height:1.6;font-size:0.9rem;">${body}</p>
            <label style="display:flex;flex-direction:column;gap:6px;margin-bottom:${showRadios ? '14px' : '18px'};">
              <span style="font-weight:600;color:#fff;font-size:0.82rem;">Reason for cancelling:</span>
              <textarea id="_slotCancelReason" rows="3" placeholder="${reasonPlaceholder}"
                style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:#fff;padding:8px 10px;font-size:0.85rem;font-family:inherit;resize:vertical;box-sizing:border-box;"></textarea>
            </label>
            ${slotMode ? slotOptionsHtml : (keepOpenOption ? gigOptionsHtml : '')}
            <div style="display:flex;gap:8px;justify-content:flex-end;">
              <button id="_slotCancelNo" class="btn ghost" style="padding:8px 16px;font-size:0.85rem;">Never Mind</button>
              <button id="_slotCancelYes" style="padding:8px 16px;font-size:0.85rem;background:#dc3545;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;">Confirm Cancel</button>
            </div>
          </div>
        `;
        document.body.appendChild(ov);
        ov.querySelector('#_slotCancelNo').onclick = () => { ov.remove(); resolve(null); };
        ov.querySelector('#_slotCancelYes').onclick = () => {
          const reason = (ov.querySelector('#_slotCancelReason').value || '').trim();
          const choice = ov.querySelector('input[name="_slotCancelMode"]:checked')?.value;
          let keepOpen = true;       // gig-level: keep gig open (re-list slot)
          let removeSlot = false;    // slot-level: delete the slot row from the gig
          if (slotMode) {
            removeSlot = choice === 'remove_slot';
            keepOpen = !removeSlot;  // removing the slot is implicitly "not keep open"
          } else if (keepOpenOption) {
            keepOpen = choice !== 'delete_gig';
          }
          ov.remove();
          resolve({ reason, keepOpen, removeSlot });
        };
        ov.addEventListener('click', e => { if (e.target === ov) { ov.remove(); resolve(null); } });
      });
    }

    // ── Shared helper: styled delete confirmation (no reason, no keep_open) ──
    // Used for removing unbooked open slots.
    function _showDeleteOpenSlotOverlay(slotN) {
      return new Promise(resolve => {
        const ov = document.createElement('div');
        ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:10000;display:flex;align-items:center;justify-content:center;';
        ov.innerHTML = `
          <div style="background:#1a1a2e;border:1px solid rgba(255,255,255,0.12);border-radius:12px;padding:24px;max-width:400px;width:92%;box-shadow:0 16px 48px rgba(0,0,0,0.5);text-align:center;">
            <div style="font-size:2rem;margin-bottom:12px;">🗑️</div>
            <h3 style="margin:0 0 10px;color:#f0f0f0;font-size:1rem;font-weight:700;">Remove Slot ${slotN}?</h3>
            <p style="color:#94a3b8;margin:0 0 20px;font-size:0.88rem;line-height:1.5;">
              This open slot has no artist booked. It will be removed from the event.
            </p>
            <div style="display:flex;gap:10px;justify-content:center;">
              <button id="_openSlotNo" class="btn ghost" style="padding:8px 20px;font-size:0.85rem;">Never Mind</button>
              <button id="_openSlotYes" style="padding:8px 20px;font-size:0.85rem;background:#dc3545;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;">Remove Slot</button>
            </div>
          </div>
        `;
        document.body.appendChild(ov);
        ov.querySelector('#_openSlotNo').onclick  = () => { ov.remove(); resolve(false); };
        ov.querySelector('#_openSlotYes').onclick = () => { ov.remove(); resolve(true); };
        ov.addEventListener('click', e => { if (e.target === ov) { ov.remove(); resolve(false); } });
      });
    }

    // ── CASE 1: Single-slot gig ───────────────────────────────────────────
    // The ✕ on a single-slot gig is equivalent to "Cancel Gig".
    // Show a proper cancel modal (with keep_open / delete choice if booked,
    // or a simple delete confirmation if open).
    if (totalSlots <= 1) {
      if (isBooked) {
        const result = await _showCancelOverlay({
          title: '⚠️ Cancel This Gig?',
          body: `<strong>${artistName}</strong> has booked this gig. They will be notified. We recommend reaching out so they understand why.`,
          reasonPlaceholder: "Explain why you're cancelling...",
          keepOpenOption: true,
        });
        if (!result) return;
        try {
          const resp = await fetch(`/api/gigs/${gigId}/cancel`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ cancelled_by: 'venue', cancellation_reason: result.reason, keep_open: result.keepOpen })
          });
          if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
          document.getElementById('gigModal').classList.add('hidden');
          invalidateGigs(); await renderCalendar();
          if (window.activityCenterVenue) await window.activityCenterVenue.loadNotifications();
          showAlert(result.keepOpen ? 'Booking cancelled — gig is open again.' : 'Gig cancelled and removed from calendar.', 'Gig Cancelled');
        } catch(e) {
          showAlert('Failed to cancel gig: ' + e.message, 'Error');
        }
      } else {
        // Open single-slot gig — confirm deletion
        const confirmed = await _showDeleteOpenSlotOverlay(slotNum);
        if (!confirmed) return;
        try {
          await fetch(`/api/gigs/${gigId}/cancel`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ cancelled_by: 'venue', keep_open: false })
          });
          document.getElementById('gigModal').classList.add('hidden');
          invalidateGigs(); await renderCalendar();
          showAlert('Gig removed from calendar.', 'Gig Deleted');
        } catch(e) {
          showAlert('Failed to delete gig: ' + e.message, 'Error');
        }
      }
      return;
    }

    // ── CASE 2: Multi-slot gig — booked slot ─────────────────────────────
    if (isBooked) {
      const result = await _showCancelOverlay({
        title: `⚠️ Cancel Slot ${slotNum} Booking?`,
        body: `<strong>${artistName}</strong> has booked this slot. They will be notified of the cancellation. We recommend communicating so they understand why.`,
        reasonPlaceholder: "Explain why you're cancelling this slot...",
        slotMode: true,
        canRemove: totalSlots > 1, // never let the LAST slot be removed (would leave an empty gig)
      });
      if (!result) return;
      try {
        const resp = await fetch(`/api/gigs/${gigId}/slots/${slotId}/cancel`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            cancelled_by: 'venue',
            cancellation_reason: result.reason,
            remove_slot: result.removeSlot,
          })
        });
        if (!resp.ok) {
          let detail = `HTTP ${resp.status}`;
          try { const j = await resp.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
          throw new Error(detail);
        }
        document.getElementById('gigModal').classList.add('hidden');
        invalidateGigs(); await renderCalendar();
        if (window.activityCenterVenue) await window.activityCenterVenue.loadNotifications();
        showAlert(
          result.removeSlot
            ? `Slot ${slotNum} removed — ${artistName} has been notified.`
            : `Slot ${slotNum} cancelled — it is now open and available to book again.`,
          result.removeSlot ? 'Slot Removed' : 'Slot Cancelled'
        );
      } catch(e) {
        console.error('Slot cancel error:', e);
        showAlert('Failed to cancel slot: ' + e.message, 'Error');
      }
      return;
    }

    // ── CASE 3: Multi-slot gig — open (unbooked) slot ────────────────────
    const confirmed = await _showDeleteOpenSlotOverlay(slotNum);
    if (!confirmed) return;
    try {
      const resp = await fetch(`/api/gigs/${gigId}/slots/${slotId}/cancel`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ cancelled_by: 'venue', remove_slot: true })
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const j = await resp.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
        throw new Error(detail);
      }
      document.getElementById('gigModal').classList.add('hidden');
      invalidateGigs(); await renderCalendar();
      showAlert(`Slot ${slotNum} removed from the event.`, 'Slot Removed');
    } catch(e) {
      console.error('Slot delete error:', e);
      showAlert('Failed to remove slot: ' + e.message, 'Error');
    }
  };

  saveBtn.onclick = async () => {
    // Check if payment method is selected
    // Block if venue is suspended
    if (typeof window.isVenueSuspended === 'function' && window.isVenueSuspended()) {
      if (typeof window.checkVenuePaymentStatus === 'function') window.checkVenuePaymentStatus();
      return;
    }
    if (typeof window.checkVenuePaymentMethod === 'function' && !window.checkVenuePaymentMethod()) {
      return; // Payment method required modal will be shown
    }
    
    const origText = saveBtn.textContent;
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';
    
    try {
      if (selectedGig.id) {
        // If venue confirmed detach from recurring series, do that first then save standalone
        if (selectedGig._pendingDetach) {
          await api(`/api/gigs/${selectedGig.id}/detach-series`, { method: 'POST' });
          selectedGig = Object.assign({}, selectedGig, { recurring_group_id: null, is_recurring: 0, _pendingDetach: false });
          // Patch the cache so re-opening the modal sees correct data immediately
          if (venueGigsCache) {
            const idx = venueGigsCache.findIndex(g => g.id === selectedGig.id);
            if (idx !== -1) venueGigsCache[idx] = Object.assign({}, venueGigsCache[idx], { recurring_group_id: null, is_recurring: 0 });
          }
          await updateSingleGig(selectedGig.id);
          return;
        }

        // If editing a booked gig directly, always save just this gig (skip recurring modal)
        if (!selectedGig._isBookedEdit && selectedGig.recurring_group_id) {
          // Show series edit modal
          saveBtn.disabled = false;
          saveBtn.textContent = origText;
          showSeriesModal('edit');
          return;
        }

        // If user checked "Recurring Gig?" on a previously standalone gig, convert to series
        if (!selectedGig.recurring_group_id && recurringCheckbox.checked) {
          // First update the existing gig to be part of the new series,
          // then createRecurringGigs will skip its date and only add the others
          selectedGig._convertingToRecurring = true;
          await createRecurringGigs();
          selectedGig._convertingToRecurring = false;
          return;
        }
        
        // Update existing gig (non-recurring, or booked-gig direct edit)
        await updateSingleGig(selectedGig.id);
        return;
      }

      // Create new gig(s)
      if (recurringCheckbox.checked) {
        await createRecurringGigs();
      } else {
        await createSingleGig();
      }
    } catch (e) {
      console.error('Save gig error:', e);
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = origText;
    }
  };

  function showGigSuccess(message) {
    modal.classList.add("hidden");
    
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;z-index:10001;';
    overlay.innerHTML = `
      <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:2px solid #22c55e;border-radius:12px;padding:2rem;max-width:400px;text-align:center;box-shadow:0 8px 32px rgba(34,197,94,0.3);">
        <div style="font-size:3rem;margin-bottom:1rem;color:#22c55e;">✓</div>
        <p style="color:#e5e5e5;margin:0 0 1.5rem;font-size:1rem;line-height:1.5;">${message}</p>
        <button class="btn primary" style="min-width:120px;">OK</button>
      </div>
    `;
    document.body.appendChild(overlay);
    
    const closeIt = () => { overlay.remove(); };
    overlay.querySelector('button').onclick = closeIt;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeIt(); });
  }

  // Validate per-slot artist types, styles, lineup
  function validateSlotArtistTypes() {
    const rows = document.querySelectorAll('#slotList .slot-row');
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      // Skip booked slots — their artist type is locked and cannot be changed
      if (row.dataset.isBooked === 'true') continue;
      const type = row.querySelector('.slot-artist-type')?.value;
      if (!type) {
        const sel = row.querySelector('.slot-artist-type');
        if (sel) { sel.style.border = '2px solid #ef4444'; sel.style.boxShadow = '0 0 8px rgba(239,68,68,0.5)'; sel.addEventListener('change', function rb() { sel.style.border = ''; sel.style.boxShadow = ''; sel.removeEventListener('change', rb); }); }
        showAlert(`Slot ${i+1}: Please select an Artist Type.`);
        return false;
      }
      if (type === 'Live Band') {
        const checkedStyles = row.querySelectorAll('.slot-style-cb:checked');
        if (checkedStyles.length === 0) {
          showAlert(`Slot ${i+1}: Please select at least one Style.`);
          return false;
        }
        const checkedLineup = row.querySelectorAll('.slot-lineup-cb:checked');
        if (checkedLineup.length === 0) {
          showAlert(`Slot ${i+1}: Please select at least one Lineup option.`);
          return false;
        }
      }
    }
    return true;
  }

  async function createSingleGig() {
    // MULTI-SLOT: All artist types use slot system
    const slots = getSlotData();
    if (slots.length === 0) {
      showAlert("Please add at least one time slot.");
      return;
    }
    if (!validateSlots()) {
      return;
    }
    if (!validateSlotArtistTypes()) {
      return;
    }
    
    // Derive gig-level artist_type from first slot (backward compat)
    const artistType = slots[0].artist_type || null;
    const bandFormats = slots[0].band_formats || null;
    const gigStyles = slots[0].styles || null;

    // ── Frontend overlap check ─────────────────────────────────────────────
    const overlapMsg = await checkNewGigOverlap(selectedDate, slots, null);
    if (overlapMsg) {
      showSlotError(overlapMsg);
      return;
    }
    // ──────────────────────────────────────────────────────────────────────

    await api(`/venues/${venueId}/gigs`, {
      method: "POST",
      body: JSON.stringify({
        date: selectedDate,
        title: titleInput.value,
        pay: slots[0].pay,
        notes: notesInput.value,
        artist_type: artistType,
        band_formats: bandFormats,
        styles: gigStyles,
        is_recurring: 0,
        slots: slots
      })
    });

    // After creation, check if gig is within 7 calendar days — offer blast
    const gigRes = await api(`/venues/${venueId}/gigs`);
    const newGig = (gigRes || []).find(g => g.date === selectedDate && g.status === 'open' && !g.artist_id);

    // Compare calendar dates (not timestamps) to avoid time-of-day edge cases
    const _todayLocal = new Date();
    const _todayStr = _todayLocal.getFullYear() + '-'
      + String(_todayLocal.getMonth() + 1).padStart(2, '0') + '-'
      + String(_todayLocal.getDate()).padStart(2, '0');
    const _msPerDay = 86400000;
    const daysUntil = newGig
      ? Math.round((new Date(newGig.date + 'T00:00:00') - new Date(_todayStr + 'T00:00:00')) / _msPerDay)
      : 99;

    invalidateGigs(); renderCalendar();

    modal.classList.add('hidden');
    // Show blast prompt immediately — it IS the success confirmation
    // Don't show showGigSuccess first (it would cover the blast modal)
    if (newGig && daysUntil >= 0) {
      _showNewGigBlastPrompt(
        [{id: newGig.id, date: newGig.date, daysUntil, slotCount: slots.length}],
        window.venueBlinkSettings || {},
        venueId
      );
    } else {
      showGigSuccess(`Gig created with ${slots.length} slot${slots.length > 1 ? 's' : ''}!`);
    }
  }

  async function createRecurringGigs() {
    // Validate slots first
    const slots = getSlotData();
    if (slots.length === 0) {
      showAlert("Please add at least one time slot.");
      return;
    }
    if (!validateSlots()) {
      return;
    }
    if (!validateSlotArtistTypes()) {
      return;
    }
    
    // Derive gig-level artist_type from first slot
    const artistType = slots[0].artist_type || null;
    const bandFormats = slots[0].band_formats || null;
    const gigStyles = slots[0].styles || null;

    // Generate recurring dates
    const dates = generateRecurringDates();
    if (dates.length === 0) {
      showAlert("Please select at least one day of the week for recurring gigs.");
      return;
    }

    // Generate unique recurring group ID
    const recurringGroupId = `recur_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    // Get recurring pattern info
    const everyWeeks = parseInt(document.getElementById('recurWeeks').value) || 1;
    const selectedDays = [];
    if (document.getElementById('daySun').checked) selectedDays.push('0');
    if (document.getElementById('dayMon').checked) selectedDays.push('1');
    if (document.getElementById('dayTue').checked) selectedDays.push('2');
    if (document.getElementById('dayWed').checked) selectedDays.push('3');
    if (document.getElementById('dayThu').checked) selectedDays.push('4');
    if (document.getElementById('dayFri').checked) selectedDays.push('5');
    if (document.getElementById('daySat').checked) selectedDays.push('6');
    
    const endType = document.querySelector('input[name="endType"]:checked')?.value || 'after';
    const endAfter = endType === 'after' ? (parseInt(endAfterInput.value) || null) : null;
    const endBy = endType === 'by' ? (endByInput.value || null) : null;

    // Get slot data for start/end/pay (already validated above)
    const startTime = slots[0].start_time;
    const endTime = slots[slots.length - 1].end_time;
    const pay = slots[0].pay;

    // Create all gigs in the series with proper database fields
    const gigData = {
      start_time: startTime,
      end_time: endTime,
      title: titleInput.value,
      pay: pay,
      notes: notesInput.value,
      artist_type: artistType,
      band_formats: bandFormats,
        styles: gigStyles,
      slots: slots,
      is_recurring: 1,
      recurring_group_id: recurringGroupId,
      recurring_interval_weeks: everyWeeks,
      recurring_days_of_week: selectedDays.join(','),
      recurring_end_type: endType,
      recurring_end_after: endAfter,
      recurring_end_by_date: endBy
    };

    const _createdGigEntries = [];
    const _failedDates = [];
    const _todayStrR = (() => { const n = new Date(); return n.getFullYear()+'-'+String(n.getMonth()+1).padStart(2,'0')+'-'+String(n.getDate()).padStart(2,'0'); })();
    const _msDay = 86400000;

    // Audit fix (May 2026): the backend has no batched recurring endpoint,
    // so each occurrence is its own commit. If one fails midway, prior
    // commits are already live — surface partial-failure to the user
    // instead of silently exiting.
    for (const date of dates) {
      // ── Overlap check per date ──────────────────────────────────────────
      // Skip overlap check for the existing gig's own date when converting
      const skipId = (selectedGig && selectedGig._convertingToRecurring && date === selectedGig.date) ? selectedGig.id : null;
      const overlapMsg = await checkNewGigOverlap(date, slots, skipId);
      if (overlapMsg) {
        _failedDates.push({ date, reason: overlapMsg });
        continue;
      }
      // ──────────────────────────────────────────────────────────────────

      try {
        let createdId = null;
        // When converting an existing gig to recurring, update it instead of creating duplicate
        if (selectedGig && selectedGig._convertingToRecurring && date === selectedGig.date) {
          await api(`/gigs/${selectedGig.id}`, {
            method: "PUT",
            body: JSON.stringify({ ...gigData, date })
          });
          createdId = selectedGig.id;
        } else {
          const res = await api(`/venues/${venueId}/gigs`, {
            method: "POST",
            body: JSON.stringify({ ...gigData, date })
          });
          createdId = res && (res.gig_id || res.id);
        }
        if (createdId) {
          const daysUntil = Math.round((new Date(date + 'T00:00:00') - new Date(_todayStrR + 'T00:00:00')) / _msDay);
          if (daysUntil >= 0) {
            _createdGigEntries.push({ id: createdId, date, daysUntil, slotCount: slots.length });
          }
        }
      } catch (err) {
        _failedDates.push({ date, reason: (err && err.message) || 'Unknown error' });
      }
    }

    // If any dates failed, tell the user — earlier ones were already created.
    if (_failedDates.length > 0) {
      const list = _failedDates.slice(0, 6).map(f => `<li><strong>${f.date}</strong>: ${f.reason}</li>`).join('');
      const more = _failedDates.length > 6 ? `<p style="margin:8px 0 0;">…and ${_failedDates.length - 6} more.</p>` : '';
      showStyledModal(
        `${_createdGigEntries.length} created, ${_failedDates.length} skipped`,
        `<p>Some occurrences in the series couldn't be created. The successful ones are already on your calendar.</p>
         <ul style="text-align:left;font-size:0.85rem;color:#fbbf24;line-height:1.6;">${list}</ul>${more}`,
        [{text:'OK',style:'ghost'}]
      );
    }

    modal.classList.add('hidden');
    invalidateGigs(); await renderCalendar(); // Make sure calendar refreshes
    // Show blast prompt immediately — skip success modal so it isn't hidden behind blast
    if (_createdGigEntries.length > 0) {
      _showNewGigBlastPrompt(_createdGigEntries, window.venueBlinkSettings || {}, venueId);
    } else {
      showGigSuccess("Recurring gigs created!");
    }
  }

  async function updateSingleGig(gigId, { silent = false } = {}) {
    // Get slots from the slot builder (per-slot artist types)
    const slots = getSlotData();
    if (slots.length > 0 && !validateSlots()) {
      return;
    }
    // Only hard-block on NEW gig creation, not edits
    // For edits: show a soft warning if artist_type is empty (blast matching may not work)
    if (slots.length > 0 && !selectedGig.id && !validateSlotArtistTypes()) {
      return;
    }
    if (slots.length > 0 && selectedGig.id) {
      const missingType = slots.some(s => !s.artist_type);
      if (missingType) {
        showAlert("⚠️ Warning: One or more slots have no Artist Type set. Automated blast emails use artist type for matching — without it, this gig won't appear in blasts. Saving anyway.");
      }
    }
    
    // Derive gig-level from first slot
    const artistType = slots.length > 0 ? (slots[0].artist_type || null) : null;
    const bandFormats = slots.length > 0 ? (slots[0].band_formats || null) : null;
    const gigStyles = slots.length > 0 ? (slots[0].styles || null) : null;
    
    // Derive parent start/end from slots
    const startTime = slots.length > 0 ? slots[0].start_time : '';
    const endTime = slots.length > 0 ? slots[slots.length - 1].end_time : '';
    const pay = slots.length > 0 ? slots[0].pay : 0;

    // ── Frontend overlap check (only for the primary gig being edited, not batch) ─
    if (!silent && slots.length > 0 && selectedGig && selectedGig.date) {
      const overlapMsg = await checkNewGigOverlap(selectedGig.date, slots, gigId);
      if (overlapMsg) {
        showSlotError(overlapMsg);
        return;
      }
    }
    
    // Use dedicated booked-edit endpoint for booked gigs (preserves status, updates times/pay/title/notes)
    if (selectedGig && selectedGig._isBookedEdit) {
      await api(`/api/gigs/${gigId}/booked-edit`, {
        method: "PUT",
        body: JSON.stringify({
          title: titleInput.value,
          notes: notesInput.value,
          slots: slots
        })
      });
    } else {
      await api(`/gigs/${gigId}`, {
        method: "PUT",
        body: JSON.stringify({
          title: titleInput.value,
          start_time: startTime,
          end_time: endTime,
          pay: pay,
          notes: notesInput.value,
          artist_type: artistType,
          band_formats: bandFormats,
          styles: gigStyles,
          slots: slots
        })
      });
    }
    // Clear stale slots cache so next modal open re-fetches from DB
    if (selectedGig) delete selectedGig.slots;
    _recurringSnapshot = null; // discard snapshot — changes were saved
    if (!silent) {
      showGigSuccess("Gig updated!");
      invalidateGigs(); await renderCalendar();
    }
  }

  async function updateSeriesGigs(recurringGroupId, fromDate) {
    // Get slots from the slot builder (per-slot artist types)
    const slots = getSlotData();
    
    // Derive gig-level from first slot
    const artistType = slots.length > 0 ? (slots[0].artist_type || null) : null;
    const bandFormats = slots.length > 0 ? (slots[0].band_formats || null) : null;
    const gigStyles = slots.length > 0 ? (slots[0].styles || null) : null;

    // Read recurring settings from the UI form fields (user may have changed them)
    // Fall back to selectedGig cache only if the form field is empty
    const _recurWeeksEl = document.getElementById('recurWeeks');
    const _endAfterEl   = document.getElementById('endAfter');
    const _endByEl      = document.getElementById('endBy');
    const _endTypeRadio = document.querySelector('input[name="endType"]:checked');

    const everyWeeks = parseInt(_recurWeeksEl?.value) || selectedGig.recurring_interval_weeks || 1;

    // For days: if the recurring options panel is visible, read from checkboxes
    // Otherwise fall back to selectedGig cache
    const _recurOpts = document.getElementById('recurringOptions');
    let existingDays;
    if (_recurOpts && _recurOpts.style.display !== 'none') {
      const _days = [];
      if (document.getElementById('daySun')?.checked) _days.push('0');
      if (document.getElementById('dayMon')?.checked) _days.push('1');
      if (document.getElementById('dayTue')?.checked) _days.push('2');
      if (document.getElementById('dayWed')?.checked) _days.push('3');
      if (document.getElementById('dayThu')?.checked) _days.push('4');
      if (document.getElementById('dayFri')?.checked) _days.push('5');
      if (document.getElementById('daySat')?.checked) _days.push('6');
      existingDays = _days.join(',') || selectedGig.recurring_days_of_week || '';
    } else {
      existingDays = selectedGig.recurring_days_of_week || '';
    }

    const endType = _endTypeRadio?.value || 
                    ((selectedGig.recurring_end_type && selectedGig.recurring_end_type !== 'never') 
                      ? selectedGig.recurring_end_type : 'after');
    const endAfter = endType === 'after'
      ? (parseInt(_endAfterEl?.value) || selectedGig.recurring_end_after || null)
      : null;
    const endBy = endType === 'by'
      ? (_endByEl?.value || selectedGig.recurring_end_by_date || null)
      : null;

    // Get slot data for start/end/pay
    const startTime = slots.length > 0 ? slots[0].start_time : '';
    const endTime = slots.length > 0 ? slots[slots.length - 1].end_time : '';
    const pay = slots.length > 0 ? slots[0].pay : 0;

    // ── Overlap check: verify no OTHER gig on any affected series date conflicts ──
    if (slots.length > 0) {
      if (gigsCacheDirty) await refreshGigs();
      // Get all dates in this series from fromDate forward
      const seriesDates = venueGigsCache
        .filter(g => g.recurring_group_id === recurringGroupId && g.date >= fromDate)
        .map(g => g.date);
      for (const date of seriesDates) {
        const overlapMsg = await checkNewGigOverlap(date, slots,
          // exclude ALL gigs in this series on that date
          venueGigsCache.filter(g => g.recurring_group_id === recurringGroupId && g.date === date).map(g => g.id)[0]
        );
        if (overlapMsg) {
          // checkNewGigOverlap excludes only one ID — for series we need to exclude all in series
          // Re-check manually excluding all series gig IDs
          const seriesIds = new Set(venueGigsCache
            .filter(g => g.recurring_group_id === recurringGroupId)
            .map(g => g.id));
          const sameDayOtherGigs = venueGigsCache.filter(g =>
            g.date === date && !seriesIds.has(g.id) && g.status !== 'cancelled'
          );
          let conflict = false;
          for (const slot of slots) {
            const ns = slot.start_time, ne = slot.end_time;
            if (!ns || !ne) continue;
            for (const eg of sameDayOtherGigs) {
              const ws = eg.start_time, we = eg.end_time;
              if (ws && we && _timesOverlap(ns, ne, ws, we)) {
                const fmt = t => { const [h, m] = t.split(':'); const hr = +h; return `${hr > 12 ? hr-12 : hr||12}:${m} ${hr >= 12 ? 'PM' : 'AM'}`; };
                showSlotError(`${date}: slot ${fmt(ns)}–${fmt(ne)} overlaps an existing gig (${fmt(ws)}–${fmt(we)}). Please choose a different time.`);
                conflict = true;
                break;
              }
            }
            if (conflict) break;
          }
          if (conflict) return;
        }
      }
    }
    // ─────────────────────────────────────────────────────────────────────

    // Call update-series endpoint — passes slots so backend updates per-slot data too
    const _seriesResult = await api(`/venues/${venueId}/gigs/recurring/${recurringGroupId}/update-series`, {
      method: "PUT",
      body: JSON.stringify({
        from_date: fromDate,
        title: titleInput.value,
        start_time: startTime,
        end_time: endTime,
        pay: pay,
        notes: notesInput.value,
        artist_type: artistType,
        band_formats: bandFormats,
        styles: gigStyles,
        slots: slots,
        // Preserve existing recurring settings
        recurring_interval_weeks: everyWeeks,
        recurring_days_of_week: existingDays,
        recurring_end_type: endType,
        recurring_end_after: endAfter,
        recurring_end_by_date: endBy,
        ...(updateSeriesGigs._extraParams || {})
      })
    });
    delete updateSeriesGigs._extraParams;

    // Handle overlap conflicts — show modal so venue can decide
    if (_seriesResult && _seriesResult.conflicts && _seriesResult.conflicts.length > 0) {
      const fmtDate = d => { const [y,m,day] = d.split('-'); return new Date(parseInt(y),parseInt(m)-1,parseInt(day)).toLocaleDateString('en-US',{month:'numeric',day:'numeric',year:'2-digit'}); };
      const conflictList = _seriesResult.conflicts.map(c =>
        `<tr>
          <td style="padding:7px 10px;font-size:0.85rem;color:#f0f0f0;white-space:nowrap;">${fmtDate(c.date)}</td>
          <td style="padding:7px 10px;font-size:0.82rem;color:#fcd34d;">${c.existing_title} (${c.existing_times})</td>
        </tr>`
      ).join('');

      await new Promise(resolve => {
        const ov = document.createElement('div');
        ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;z-index:10004;padding:16px;box-sizing:border-box;';
        ov.innerHTML = `
          <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:2px solid rgba(245,158,11,0.4);border-radius:12px;padding:1.75rem;max-width:520px;width:100%;box-shadow:0 16px 48px rgba(0,0,0,0.6);">
            <h3 style="color:#f0f0f0;margin:0 0 8px;font-size:1rem;font-weight:700;">⚠️ Time Conflicts Found</h3>
            <p style="color:#a0a0b0;font-size:0.83rem;margin:0 0 14px;line-height:1.5;">The following dates already have a gig at overlapping times:</p>
            <div style="background:rgba(0,0,0,0.3);border-radius:8px;overflow:hidden;margin-bottom:16px;max-height:200px;overflow-y:auto;">
              <table style="width:100%;border-collapse:collapse;">
                <thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1);">
                  <th style="padding:6px 10px;text-align:left;font-size:0.72rem;color:#888;text-transform:uppercase;">Date</th>
                  <th style="padding:6px 10px;text-align:left;font-size:0.72rem;color:#888;text-transform:uppercase;">Existing Gig</th>
                </tr></thead>
                <tbody>${conflictList}</tbody>
              </table>
            </div>
            <p style="color:#a0a0b0;font-size:0.83rem;margin:0 0 18px;line-height:1.5;">What would you like to do?</p>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
              <button id="_ovSkip" class="btn ghost" style="flex:1;min-width:120px;">Skip those dates</button>
              <button id="_ovAllow" class="btn" style="flex:1;min-width:120px;background:rgba(245,158,11,0.15);border-color:rgba(245,158,11,0.4);color:#f59e0b;">Allow Overlap</button>
              <button id="_ovCancel" class="btn" style="flex:1;min-width:80px;background:rgba(239,68,68,0.15);border-color:rgba(239,68,68,0.4);color:#ef4444;">Cancel</button>
            </div>
          </div>
        `;
        document.body.appendChild(ov);

        ov.querySelector('#_ovSkip').onclick = () => {
          ov.remove();
          // Re-run with skip_dates set — conflicting dates will be skipped
          updateSeriesGigs._extraParams = { skip_dates: _seriesResult.conflicts.map(c => c.date) };
          resolve('skip');
        };
        ov.querySelector('#_ovAllow').onclick = () => {
          ov.remove();
          // Re-run with force_overlap — insert even if conflicts exist
          updateSeriesGigs._extraParams = { force_overlap: true };
          resolve('allow');
        };
        ov.querySelector('#_ovCancel').onclick = () => {
          ov.remove();
          resolve('cancel');
        };
      }).then(async choice => {
        if (choice === 'cancel') return;
        // Re-call with the extra params set above
        await updateSeriesGigs(recurringGroupId, fromDate);
        return; // Early return — the recursive call will show success
      });
      return; // Don't show success here — handled in recursive call or cancelled
    }

    showGigSuccess("Recurring gigs updated!");
    invalidateGigs(); await renderCalendar();
  }

  function generateRecurringDates() {
    // Get inputs
    const everyWeeks = parseInt(document.getElementById('recurWeeks').value) || 1;
    
    // Get selected days (0=Sunday, 1=Monday, ..., 6=Saturday)
    const selectedDays = [];
    if (document.getElementById('daySun').checked) selectedDays.push(0);
    if (document.getElementById('dayMon').checked) selectedDays.push(1);
    if (document.getElementById('dayTue').checked) selectedDays.push(2);
    if (document.getElementById('dayWed').checked) selectedDays.push(3);
    if (document.getElementById('dayThu').checked) selectedDays.push(4);
    if (document.getElementById('dayFri').checked) selectedDays.push(5);
    if (document.getElementById('daySat').checked) selectedDays.push(6);

    if (selectedDays.length === 0) return [];
    selectedDays.sort((a, b) => a - b);

    // Get end condition
    const endType = document.querySelector('input[name="endType"]:checked')?.value;
    let maxWeeks = null;  // v97: Changed from maxOccurrences - now counts WEEKS not total gigs
    let endByDate = null;

    if (endType === 'by') {
      endByDate = endByInput.value; // Keep as string "YYYY-MM-DD"
    } else if (endType === 'after') {
      // v97: "occurrences" now means WEEKS - so 10 occurrences with Fri+Sat = 10 Fridays + 10 Saturdays
      maxWeeks = parseInt(endAfterInput.value) || 52;
    } else {
      maxWeeks = 52; // Default to 52 weeks (1 year)
    }
    // Audit fix (May 2026): hard cap recurring series at 104 weeks (2 years)
    // — without this, a fat-fingered "1000 occurrences" or paste-typo would
    // create hundreds of gigs in one click. Backend has no batched recurring
    // endpoint, so each occurrence is an independent commit.
    if (maxWeeks != null && maxWeeks > 104) maxWeeks = 104;

    const dates = [];
    
    // Parse clicked date
    const [year, month, day] = selectedDate.split('-').map(Number);
    let currentDate = new Date(year, month - 1, day); // month is 0-indexed in Date constructor
    const clickedDayOfWeek = currentDate.getDay();
    

    // If the clicked day is one of the selected days, start from it
    // Otherwise start from the first selected day in the next week
    let weekOffset = 0;
    let weeksGenerated = 0; // v97: Track weeks, not total gigs
    
    // Generate dates
    let safetyCounter = 0;
    while (safetyCounter < 500) {
      safetyCounter++;
      
      // v97: Check if we've generated enough weeks BEFORE processing this week
      if (maxWeeks && weeksGenerated >= maxWeeks) {
        return dates;
      }
      
      let addedGigThisWeek = false;
      
      for (const targetDayOfWeek of selectedDays) {
        // Calculate the date for this target day in the current week iteration
        const daysFromClickedDay = targetDayOfWeek - clickedDayOfWeek;
        const weeksToAdd = weekOffset * everyWeeks;
        const totalDaysToAdd = (weeksToAdd * 7) + daysFromClickedDay;
        
        const gigDate = new Date(year, month - 1, day + totalDaysToAdd);
        
        // Only include if on or after clicked date
        if (gigDate < currentDate) continue;
        
        // Format as YYYY-MM-DD
        const gigYear = gigDate.getFullYear();
        const gigMonth = String(gigDate.getMonth() + 1).padStart(2, '0');
        const gigDay = String(gigDate.getDate()).padStart(2, '0');
        const dateStr = `${gigYear}-${gigMonth}-${gigDay}`;
        
        // Check end by date condition
        if (endByDate && dateStr > endByDate) {
          return dates;
        }
        
        dates.push(dateStr);
        addedGigThisWeek = true;
      }
      
      // v97: Only count a week if we added at least one gig
      if (addedGigThisWeek) {
        weeksGenerated++;
      }
      
      weekOffset++;
    }
    
    return dates;
  }
  

  deleteBtn.onclick = async () => {
    if (!selectedGig.id) return;

    const cancelGigBtn = document.getElementById("cancelGig");
    const gigArtistInfo = document.getElementById("gigArtistInfo");
    
    // Check if this gig is part of a recurring series (only for open gigs)
    if (selectedGig.recurring_group_id && selectedGig.status !== "booked") {
      showSeriesModal('delete');
      return;
    }
    
    // MULTI-SLOT GIG handling
    if (true) {
      const bookedCount = parseInt(deleteBtn.dataset.multiSlotBookedCount || '0');
      
      if (bookedCount > 0) {
        // Has booked slots - show cancel process
        if (cancelGigBtn && cancelGigBtn.dataset.cancelMode !== "true") {
          cancelGigBtn.dataset.cancelMode = "true";
          
          // Get booked artist names from slot data
          let bookedArtists = [];
          try {
            const slotsRes = await fetch(`/api/gigs/${selectedGig.id}/slots`, { credentials: 'include' });
            if (slotsRes.ok) {
              const slots = await slotsRes.json();
              bookedArtists = slots.filter(s => s.status === 'booked').map(s => s.artist_name || 'Unknown Artist');
            }
          } catch(e) {}
          
          const artistList = bookedArtists.length > 0 ? bookedArtists.join(', ') : 'Booked artists';
          
          const cancellationHTML = `
            <div id="cancellationSection" style="margin-top: 24px; padding: 16px; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px;">
              <p style="color: #ef4444; margin: 0 0 12px 0; line-height: 1.6;">
                <strong>⚠️ This event has ${bookedCount} booked slot${bookedCount > 1 ? 's' : ''}!</strong><br/>
                <strong>${artistList}</strong> will be notified. We recommend communicating with the artist${bookedCount > 1 ? 's' : ''} so they understand why this event is being cancelled.
              </p>
              <label style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px;">
                <span style="font-weight: 500; color: #ffffff;">Reason For Cancelling:</span>
                <textarea id="cancelReason" rows="3" placeholder="Explain why you're cancelling this event..." style="width: 100%;"></textarea>
              </label>
              <div style="margin-bottom: 8px; font-weight: 500; color: #ffffff;">After cancelling:</div>
              <table style="border-collapse:collapse; margin-bottom:8px;"><tr><td style="padding:0; vertical-align:middle; padding-right:8px;"><input type="radio" name="cancelModeMulti" value="keep_open" checked style="width:16px;height:16px;cursor:pointer;margin:0;display:block;"></td><td style="padding:0; vertical-align:middle; color:#d1d5db; font-size:14px;">Keep Event Open? <span style="color:#22c55e;font-weight:600;">(re-list the slot as available)</span></td></tr></table>
              <table style="border-collapse:collapse;"><tr><td style="padding:0; vertical-align:middle; padding-right:8px;"><input type="radio" name="cancelModeMulti" value="delete_gig" style="width:16px;height:16px;cursor:pointer;margin:0;display:block;"></td><td style="padding:0; vertical-align:middle; color:#d1d5db; font-size:14px;">Delete Event Entirely? <span style="color:#ef4444;font-weight:600;">(remove from calendar)</span></td></tr></table>
            </div>
          `;
          
          gigArtistInfo.insertAdjacentHTML('beforeend', cancellationHTML);
          deleteBtn.textContent = "Confirm Cancel Event";
          deleteBtn.style.background = "#dc3545";
          if (cancelGigBtn) cancelGigBtn.textContent = "Close";
          return;
        }
        
        // Second click - actually cancel with reason
        const cancelReason = document.getElementById("cancelReason")?.value || "";
        const keepOpen = (document.querySelector('input[name="cancelModeMulti"]:checked')?.value ?? 'keep_open') === 'keep_open';
        deleteBtn.disabled = true;
        deleteBtn.textContent = 'Cancelling...';
        try {
          const resp = await fetch(`/api/gigs/${selectedGig.id}/with-slots`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ cancellation_reason: cancelReason, keep_open: keepOpen })
          });
          if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
          
          if (window.activityCenterVenue) await window.activityCenterVenue.loadNotifications();
          if (window.myArtists) { await myArtists.loadArtists(); myArtists.render(); }
          
          invalidateGigs(); await renderCalendar();
          showGigSuccess("Event cancelled");
        } catch (e) {
          deleteBtn.disabled = false;
          deleteBtn.textContent = 'Confirm Cancel Event';
          showAlert("Failed to cancel event: " + e.message);
        }
      } else {
        // No booked slots - simple delete confirmation
        if (deleteBtn.dataset.confirmDelete === 'true') {
          deleteBtn.disabled = true;
          deleteBtn.textContent = 'Deleting...';
          try {
            await api(`/api/gigs/${selectedGig.id}/with-slots`, { method: 'DELETE' });
            deleteBtn.dataset.confirmDelete = 'false';
            deleteBtn.dataset.multiSlotDelete = 'false';
            invalidateGigs(); await renderCalendar();
            showGigSuccess("Event deleted");
          } catch (e) {
            deleteBtn.disabled = false;
            deleteBtn.textContent = 'Delete Event';
            deleteBtn.dataset.confirmDelete = 'false';
            showAlert("Failed to delete: " + e.message);
          }
          return;
        }
        deleteBtn.dataset.confirmDelete = 'true';
        deleteBtn.textContent = 'Confirm Delete?';
        setTimeout(() => {
          if (deleteBtn.dataset.confirmDelete === 'true') {
            deleteBtn.dataset.confirmDelete = 'false';
            deleteBtn.textContent = 'Delete Event';
          }
        }, 5000);
      }
      return;
    }
    
    // SINGLE-ARTIST BOOKED GIG - cancel process
    if (selectedGig.status === "booked") {
      if (cancelGigBtn && cancelGigBtn.dataset.cancelMode !== "true") {
        cancelGigBtn.dataset.cancelMode = "true";
        
        const cancellationHTML = `
          <div id="cancellationSection" style="margin-top: 24px; padding: 16px; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px;">
            <p style="color: #ef4444; margin: 0 0 12px 0; line-height: 1.6;">
              <strong>This gig is booked!</strong> ${selectedGig.artist_name || 'The artist'} will be notified but we recommend communicating with the Artist so it is understood why this gig is being cancelled.
            </p>
            <label style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px;">
              <span style="font-weight: 500; color: #ffffff;">Reason For Cancelling:</span>
              <textarea id="cancelReason" rows="3" placeholder="Explain why you're cancelling this gig..." style="width: 100%;"></textarea>
            </label>
            <div style="margin-bottom: 8px; font-weight: 500; color: #ffffff;">After cancelling:</div>
            <table style="border-collapse:collapse; margin-bottom:8px;"><tr><td style="padding:0; vertical-align:middle; padding-right:8px;"><input type="radio" name="cancelModeSingle" value="keep_open" checked style="width:16px;height:16px;cursor:pointer;margin:0;display:block;"></td><td style="padding:0; vertical-align:middle; color:#d1d5db; font-size:14px;">Keep Gig Open? <span style="color:#22c55e;font-weight:600;">(re-list as available to book)</span></td></tr></table>
            <table style="border-collapse:collapse;"><tr><td style="padding:0; vertical-align:middle; padding-right:8px;"><input type="radio" name="cancelModeSingle" value="delete_gig" style="width:16px;height:16px;cursor:pointer;margin:0;display:block;"></td><td style="padding:0; vertical-align:middle; color:#d1d5db; font-size:14px;">Delete Gig Entirely? <span style="color:#ef4444;font-weight:600;">(remove from calendar)</span></td></tr></table>
          </div>
        `;
        
        gigArtistInfo.insertAdjacentHTML('beforeend', cancellationHTML);
        deleteBtn.textContent = "Confirm Cancel Gig";
        deleteBtn.style.background = "#dc3545";
        if (cancelGigBtn) cancelGigBtn.textContent = "Close";
        return;
      }
      
      const cancelReason = document.getElementById("cancelReason")?.value || "";
      const keepOpen = (document.querySelector('input[name="cancelModeSingle"]:checked')?.value ?? 'keep_open') === 'keep_open';
      deleteBtn.disabled = true;
      deleteBtn.textContent = 'Cancelling...';
      try {
        const resp = await fetch(`/api/gigs/${selectedGig.id}/cancel`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ cancelled_by: "venue", cancellation_reason: cancelReason, keep_open: keepOpen })
        });
        if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
        
        if (window.activityCenterVenue) await window.activityCenterVenue.loadNotifications();
        // Switch to Payments tab so container is visible, then load fresh data (fixes tab not updating)
        var paymentsTabBtn = document.querySelector('.tab[onclick*="payments"]');
        if (typeof switchTab === 'function' && paymentsTabBtn) switchTab('payments', paymentsTabBtn);
        if (typeof loadVenueBillingHistory === 'function') await loadVenueBillingHistory();
        if (window.venueContracts && window.venueContracts.loadExecuted) await window.venueContracts.loadExecuted();
        if (window.myArtists) {
          await myArtists.loadArtists(); myArtists.render();
          const badge = document.getElementById('artistsBadge');
          if (badge && window.myArtists.artists) {
            const approvedCount = window.myArtists.artists.filter(a => a.preferred_status === 'approved').length;
            badge.textContent = `(${approvedCount})`;
          }
        }
        showGigSuccess("Gig cancelled");
        invalidateGigs(); renderCalendar();
      } catch (e) {
        deleteBtn.disabled = false;
        deleteBtn.textContent = 'Confirm Cancel Gig';
        showAlert("Failed to cancel gig: " + e.message);
      }
    } else {
      // Regular open gig - delete
      deleteBtn.disabled = true;
      deleteBtn.textContent = 'Deleting...';
      try {
        await api(`/gigs/${selectedGig.id}`, { method: "DELETE" });
        showGigSuccess("Gig deleted");
        invalidateGigs(); renderCalendar();
      } catch (e) {
        deleteBtn.disabled = false;
        deleteBtn.textContent = 'Delete Gig';
        showAlert("Failed to delete gig: " + e.message);
      }
    }
  };

  cancelBtn.onclick = () => {
    // Remove any cancellation section if it exists
    const cancellationSection = document.getElementById("cancellationSection");
    if (cancellationSection) {
      cancellationSection.remove();
    }
    
    // Reset cancel mode
    cancelBtn.dataset.cancelMode = "false";
    
    // v97: Reset recurring checkbox disabled state
    recurringCheckbox.disabled = false;

    // Restore any recurring UI changes the user made but didn't save
    _restoreRecurringSnapshot();
    
    modal.classList.add("hidden");
  };

  // ===================================
  // SERIES MODAL FUNCTIONS
  // ===================================
  const seriesModal = document.getElementById("seriesModal");
  const seriesModalTitle = document.getElementById("seriesModalTitle");
  const seriesEditThis = document.getElementById("seriesEditThis");
  const seriesEditAll = document.getElementById("seriesEditAll");
  const seriesCancelBtn = document.getElementById("seriesCancelBtn");

  let seriesAction = null; // 'edit' or 'delete'

  function showSeriesModal(action) {
    seriesAction = action;
    
    if (action === 'edit') {
      seriesModalTitle.textContent = "Edit Recurring Gig";
      seriesEditThis.textContent = "This Gig Only";
      seriesEditAll.textContent = "All Gigs in Series";
    } else {
      seriesModalTitle.textContent = "Delete Recurring Gig";
      seriesEditThis.textContent = "This Gig Only";
      seriesEditAll.textContent = "All Gigs in Series";
    }
    
    seriesModal.classList.remove("hidden");
  }

  seriesEditThis.onclick = async () => {
    seriesModal.classList.add("hidden");
    
    if (seriesAction === 'edit') {
      // Show confirmation that this gig will be detached from the series
      const detachOverlay = document.createElement('div');
      detachOverlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;z-index:10003;padding:16px;box-sizing:border-box;';
      detachOverlay.innerHTML = `
        <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:2px solid rgba(245,158,11,0.4);border-radius:12px;padding:2rem;max-width:420px;width:100%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,0.6);">
          <div style="font-size:2.5rem;margin-bottom:1rem;">📋</div>
          <p style="color:#f0f0f0;margin:0 0 0.5rem;font-size:1.05rem;font-weight:700;">Save as Standalone Gig?</p>
          <p style="color:#a0a0b0;margin:0 0 1.75rem;font-size:0.88rem;line-height:1.6;">
            This gig will be <strong style="color:#f59e0b;">removed from the recurring series</strong> and saved as a standalone gig with your changes. The other gigs in the series won't be affected.
          </p>
          <div style="display:flex;gap:12px;justify-content:center;">
            <button id="_detachSeriesCancel" class="btn ghost" style="min-width:100px;">Cancel</button>
            <button id="_detachSeriesOk" class="btn primary" style="min-width:140px;">OK, Save Changes</button>
          </div>
        </div>
      `;
      document.body.appendChild(detachOverlay);

      detachOverlay.querySelector('#_detachSeriesCancel').onclick = () => {
        detachOverlay.remove();
        // Re-show series modal so user can choose again
        seriesModal.classList.remove("hidden");
      };

      detachOverlay.querySelector('#_detachSeriesOk').onclick = async () => {
        detachOverlay.remove();
        // Detach from series then save
        try {
          await api(`/api/gigs/${selectedGig.id}/detach-series`, { method: 'POST' });
          selectedGig = Object.assign({}, selectedGig, { recurring_group_id: null, is_recurring: 0 });
          if (venueGigsCache) {
            const idx = venueGigsCache.findIndex(g => g.id === selectedGig.id);
            if (idx !== -1) venueGigsCache[idx] = Object.assign({}, venueGigsCache[idx], { recurring_group_id: null, is_recurring: 0 });
          }
          await updateSingleGig(selectedGig.id);
        } catch (e) {
          showAlert("Failed to save: " + e.message);
        }
      };

    } else if (seriesAction === 'delete') {
      // Delete only this gig
      try {
        await api(`/gigs/${selectedGig.id}`, { method: "DELETE" });
        showGigSuccess("Gig deleted");
        invalidateGigs(); renderCalendar();
      } catch (e) {
        showAlert("Failed to delete gig: " + e.message);
      }
    }
  };

  seriesEditAll.onclick = async () => {
    seriesModal.classList.add("hidden");
    
    if (seriesAction === 'edit') {
      // Update all FUTURE gigs in series (including this one)
      await updateSeriesGigs(selectedGig.recurring_group_id,
        // Use EARLIEST gig in the series as from_date so all gigs get updated
        // (not just from the clicked gig forward)
        (() => {
          const seriesGigs = (venueGigsCache || [])
            .filter(g => g.recurring_group_id === selectedGig.recurring_group_id)
            .map(g => g.date)
            .sort();
          return seriesGigs.length ? seriesGigs[0] : selectedGig.date;
        })()
      );
    } else if (seriesAction === 'delete') {
      // Delete ALL gigs in series (past and future)
      try {
        const delResult = await api(`/venues/${venueId}/gigs/recurring/${selectedGig.recurring_group_id}?from_date=1900-01-01`, { 
          method: "DELETE" 
        });
        const skipped = delResult && delResult.skipped_dates && delResult.skipped_dates.length;
        if (skipped) {
          const fmt = d => { const [y,m,day] = d.split('-'); return new Date(y,m-1,day).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}); };
          showGigSuccess(`Series deleted — ${delResult.deleted} gig(s) removed.<br><br><strong style="color:#f59e0b;">⚠️ ${skipped} gig(s) kept (active bookings):</strong><br>${delResult.skipped_dates.map(fmt).join(', ')}`);
        } else {
          showGigSuccess(`Series deleted — ${delResult ? delResult.deleted : ''} gig(s) removed.`);
        }
        invalidateGigs(); await renderCalendar();
      } catch (e) {
        showAlert("Failed to delete series: " + e.message);
      }
    }
  };

  seriesCancelBtn.onclick = () => {
    seriesModal.classList.add("hidden");
  };

  // SELECT GIGS MODAL
  const selectGigsModal = document.getElementById("selectGigsModal");
  const selectGigsModalTitle = document.getElementById("selectGigsModalTitle");
  const gigSelectionList = document.getElementById("gigSelectionList");
  const selectAllGigs = document.getElementById("selectAllGigs");
  const confirmSelectedGigs = document.getElementById("confirmSelectedGigs");
  const closeSelectGigs = document.getElementById("closeSelectGigs");
  const seriesSelectGigs = document.getElementById("seriesSelectGigs");
  
  let selectedGigsToUpdate = [];

  seriesSelectGigs.onclick = async () => {
    seriesModal.classList.add("hidden");
    
    // Update modal title and button text based on action
    if (seriesAction === 'edit') {
      selectGigsModalTitle.textContent = "Select Gigs to Save";
      confirmSelectedGigs.textContent = "Save Gig(s)";
    } else {
      selectGigsModalTitle.textContent = "Select Gigs to Delete";
      confirmSelectedGigs.textContent = "Delete Gig(s)";
      confirmSelectedGigs.className = "btn danger";
    }
    
    // Fetch all gigs in the series
    try {
      // Fetch all venue gigs and filter by recurring_group_id
      if (gigsCacheDirty) await refreshGigs();
      const allGigs = venueGigsCache;
      const seriesGigs = allGigs.filter(g => g.recurring_group_id === selectedGig.recurring_group_id);
      
      // Filter to only show future gigs (including today)
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const futureGigs = seriesGigs.filter(g => {
        const gigDate = (() => { const _m = g.date.match(/(\d{4})-(\d{2})-(\d{2})/); return _m ? new Date(parseInt(_m[1]), parseInt(_m[2])-1, parseInt(_m[3])) : new Date(g.date); })();
        gigDate.setHours(0, 0, 0, 0);
        return gigDate >= today;
      });
      
      // Sort by date
      futureGigs.sort((a, b) => new Date(a.date) - new Date(b.date));
      
      // Populate the list
      gigSelectionList.innerHTML = '';
      futureGigs.forEach(gig => {
        const gigDiv = document.createElement('div');
        gigDiv.style.cssText = 'display: flex; align-items: center; gap: 8px; padding: 8px; margin: 4px 0; background: rgba(255,255,255,0.05); border-radius: 4px; cursor: pointer;';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = gig.id;
        checkbox.id = `gig-${gig.id}`;
        checkbox.className = 'gig-checkbox';
        checkbox.style.cssText = 'width: 16px; height: 16px;';
        
        const label = document.createElement('label');
        label.htmlFor = `gig-${gig.id}`;
        label.style.cssText = 'flex: 1; cursor: pointer; color: var(--text);';
        label.textContent = formatDateForDisplay(gig.date);
        
        gigDiv.appendChild(checkbox);
        gigDiv.appendChild(label);
        gigSelectionList.appendChild(gigDiv);
        
        // Make the whole div clickable
        gigDiv.onclick = (e) => {
          if (e.target !== checkbox) {
            checkbox.checked = !checkbox.checked;
            updateSelectAllState();
          }
        };
        
        checkbox.onchange = updateSelectAllState;
      });
      
      // Reset select all
      selectAllGigs.checked = false;
      
      selectGigsModal.classList.remove("hidden");
    } catch (e) {
      showAlert("Failed to load series gigs: " + e.message);
    }
  };
  
  function updateSelectAllState() {
    const checkboxes = document.querySelectorAll('.gig-checkbox');
    const allChecked = Array.from(checkboxes).every(cb => cb.checked);
    selectAllGigs.checked = allChecked;
  }
  
  selectAllGigs.onchange = () => {
    const checkboxes = document.querySelectorAll('.gig-checkbox');
    checkboxes.forEach(cb => {
      cb.checked = selectAllGigs.checked;
    });
  };
  
  confirmSelectedGigs.onclick = async () => {
    const checkboxes = document.querySelectorAll('.gig-checkbox:checked');
    const selectedGigIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
    
    if (selectedGigIds.length === 0) {
      showAlert("Please select at least one gig.");
      return;
    }
    
    selectGigsModal.classList.add("hidden");
    
    if (seriesAction === 'edit') {
      // Save changes to selected gigs — silent mode so we only show success once
      const _savedRecurId = selectedGig.recurring_group_id;
      selectedGig.recurring_group_id = null;
      try {
        for (const gigId of selectedGigIds) {
          await updateSingleGig(gigId, { silent: true });
        }
        showGigSuccess(`${selectedGigIds.length} gig(s) updated!`);
        invalidateGigs(); await renderCalendar();
      } finally {
        selectedGig.recurring_group_id = _savedRecurId;
      }
    } else if (seriesAction === 'delete') {
      // Delete selected gigs
      try {
        for (const gigId of selectedGigIds) {
          await api(`/gigs/${gigId}`, { method: "DELETE" });
        }
        selectGigsModal.classList.add("hidden");
        showGigSuccess("Selected gigs deleted");
        invalidateGigs(); renderCalendar();
      } catch (e) {
        showAlert("Failed to delete selected gigs: " + e.message);
      }
    }
  };
  
  closeSelectGigs.onclick = () => {
    selectGigsModal.classList.add("hidden");
    seriesModal.classList.remove("hidden");
  };

  prevBtn.onclick = () => {
    currentDate.setMonth(currentDate.getMonth() - 1);
    renderCalendar();
  };

  nextBtn.onclick = () => {
    currentDate.setMonth(currentDate.getMonth() + 1);
    renderCalendar();
  };
  
  // Second calendar buttons (for search tab)
  if (prevBtn2) {
    prevBtn2.onclick = () => {
      currentDate.setMonth(currentDate.getMonth() - 1);
      renderCalendar();
    };
  }
  
  if (nextBtn2) {
    nextBtn2.onclick = () => {
      currentDate.setMonth(currentDate.getMonth() + 1);
      renderCalendar();
    };
  }

  // === PREFERRED ARTIST APPROVAL SYSTEM ===
  
  async function loadPreferredRequests() {
    // DISABLED - Preferred requests now shown in Activity Center
    return;
  }
  function showApprovalModal(artistId, artistData) {
    // Create approval modal
    const modalHTML = `
      <div id="approvalModal" class="modal-overlay">
        <div class="modal">
          <h3>Approve ${artistData.artist_name}</h3>
          <p style="color: var(--text-secondary); margin-bottom: 1.5rem;">
            Would you like to customize the default values for this artist?
          </p>
          
          <label style="display: block; margin-bottom: 1rem;">
            <span style="display: block; margin-bottom: 0.5rem; font-weight: 500;">Pay Override</span>
            <div style="display: flex; align-items: center; gap: 0.5rem;">
              <span style="font-size: 1.2rem;">$</span>
              <input 
                type="number" 
                id="approvalPayDollars" 
                value="${venueData?.default_pay_dollars || 0}"
                style="width: 100px;"
              />
              <span>.</span>
              <input 
                type="number" 
                id="approvalPayCents" 
                value="${(venueData?.default_pay_cents || 0).toString().padStart(2, '0')}"
                maxlength="2"
                style="width: 60px;"
              />
            </div>
            <small style="color: var(--text-muted); display: block; margin-top: 0.25rem;">
              Leave as-is to use venue default (${venueData?.default_pay_dollars || 0}.${(venueData?.default_pay_cents || 0).toString().padStart(2, '0')})
            </small>
          </label>
          
          <label style="display: block; margin-bottom: 1.5rem;">
            <span style="display: block; margin-bottom: 0.5rem; font-weight: 500;">Frequency Override</span>
            <div style="display: flex; align-items: center; gap: 0.5rem;">
              <span>1 time every</span>
              <input 
                type="number" 
                id="approvalFrequencyDays" 
                value="${venueData?.artist_frequency_days || 28}"
                min="1"
                style="width: 80px;"
              />
              <span>days</span>
            </div>
            <small style="color: var(--text-muted); display: block; margin-top: 0.25rem;">
              Leave as-is to use venue default (${venueData?.artist_frequency_days || 28} days)
            </small>
          </label>
          
          <div class="modal-actions">
            <button id="approvalCancel" class="btn ghost">Cancel</button>
            <button id="approvalConfirm" class="btn primary">Approve Artist</button>
          </div>
        </div>
      </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHTML);
    
    const approvalModal = document.getElementById('approvalModal');
    const confirmBtn = document.getElementById('approvalConfirm');
    const cancelBtn = document.getElementById('approvalCancel');
    
    cancelBtn.onclick = () => approvalModal.remove();
    
    confirmBtn.onclick = async () => {
      const payDollars = parseInt(document.getElementById('approvalPayDollars').value) || 0;
      const payCents = parseInt(document.getElementById('approvalPayCents').value) || 0;
      const frequencyDays = parseInt(document.getElementById('approvalFrequencyDays').value) || 28;
      
      await api(
        `/venues/${venueId}/preferred-requests/${artistId}`,
        {
          method: "POST",
          body: JSON.stringify({
            action: "approved",
            pay_dollars_override: payDollars,
            pay_cents_override: payCents,
            frequency_days_override: frequencyDays
          })
        }
      );
      
      approvalModal.remove();
      loadPreferredRequests();
    };
  }

  renderCalendar();
  loadPreferredRequests();
  
  // v73: Expose openGigModal on window for My Artists
  window.openGigModal = openGigModal;
  window.invalidateGigs = invalidateGigs;
  window.renderCalendar = renderCalendar;
  window.getBlinkStyle = getBlinkStyle;
  
  // v90: Initialize artist search functionality
  initializeArtistSearch(venueId, venueData);
});

// v90: Artist Search Functionality
function initializeArtistSearch(venueId, venueData) {
  
  let allArtists = [];
  let bannedArtistIds = new Set();
  let filteredArtists = [];
  let cities = [];
  let preferredArtistIds = new Set(); // v93: Track which artists are already preferred
  
  const searchArtistInput = document.getElementById('searchArtist');
  const searchCityInput = document.getElementById('searchArtistCity');
  const mileRadiusInput = document.getElementById('artistMileRadius');
  const artistResultsList = document.getElementById('artistResultsList');
  const artistSearchResults = document.getElementById('artistSearchResults');
  
  const artistAutocomplete = document.getElementById('artistAutocomplete');
  const cityAutocomplete = document.getElementById('artistCityAutocomplete');
  
  const bandFormatBubbles = document.getElementById('bandFormatBubbles');
  
  // v93: Load preferred artists
  async function loadPreferredArtists() {
    try {
      const response = await fetch(`/api/venues/${venueId}/preferred-artists`, { credentials: 'include' });
      if (response.ok) {
        const preferred = await response.json();
        preferredArtistIds = new Set(preferred.filter(p => p.status === 'approved').map(p => p.artist_id));
        bannedArtistIds = new Set(preferred.filter(p => p.is_banned || p.status === 'banned').map(p => p.artist_id));
      } else if (response.status === 404) {
        // Endpoint not implemented yet - silently skip
      } else {
        console.error('❌ v93: Failed to load preferred artists:', response.status);
      }
    } catch (error) {
      console.error('❌ v93: Error loading preferred artists:', error);
    }
  }
  
  // v90: Load US cities for autocomplete
  async function loadCities() {
    try {
      const response = await fetch('/api/cities/all', { credentials: 'include' });
      cities = await response.json();
    } catch (error) {
      console.error('❌ v90: Error loading cities:', error);
      cities = []; // Set to empty array on error
    }
  }
  
  // v90: Load all artists
  async function loadArtists() {
    try {
      const response = await fetch('/api/artists/search', { credentials: 'include' });
      
      if (!response.ok) {
        const errorText = await response.text();
        console.error('❌ v90: Artists API error:', response.status, errorText);
        allArtists = [];
        displayArtists();
        return;
      }
      
      allArtists = await response.json();
      
      // v90: Don't apply filters initially, just wait for user to click Apply
      // applyFilters();
    } catch (error) {
      console.error('❌ v90: Error loading artists:', error);
      allArtists = [];
      displayArtists();
    }
  }
  
  // v90: Set default city to venue's city
  if (venueData && venueData.city) {
    searchCityInput.value = venueData.city.toLowerCase();
  }
  
  // v93: Helper to check if any filters are active
  function hasActiveFilters() {
    const activeTypes = Array.from(artistTypeButtons).filter(btn => btn.dataset.active === 'true');
    return activeTypes.length > 0;
  }
  
  // v93: Auto-apply filters on input change
  searchArtistInput.addEventListener('input', () => {
    clearTimeout(searchArtistInput.filterTimeout);
    searchArtistInput.filterTimeout = setTimeout(() => {
      if (hasActiveFilters()) {
        applyFilters();
      }
    }, 300);
  });
  
  searchCityInput.addEventListener('input', () => {
    clearTimeout(searchCityInput.filterTimeout);
    searchCityInput.filterTimeout = setTimeout(() => {
      if (hasActiveFilters()) {
        applyFilters();
      }
    }, 300);
  });
  
  mileRadiusInput.addEventListener('input', () => {
    // Auto-apply filters as user types (matching artist page behavior)
    applyFilters();
  });
  
  // Add Enter key handler for Mile Radius - always apply filters on Enter
  mileRadiusInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      applyFilters();
    }
  });
  
  // v93: Artist type toggle buttons with AUTO-APPLY
  const artistTypeButtons = document.querySelectorAll('.artist-type-toggle');
  artistTypeButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const isActive = btn.dataset.active === 'true';
      btn.dataset.active = (!isActive).toString();
      
      if (btn.dataset.active === 'true') {
        btn.style.background = 'rgba(34, 197, 94, 0.2)';
        btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
        btn.style.color = '#22c55e';
      } else {
        btn.style.background = 'rgba(255,255,255,0.05)';
        btn.style.borderColor = 'rgba(255,255,255,0.2)';
        btn.style.color = 'var(--text-muted)';
      }
      
      // Show/hide band format bubbles
      const liveBandButton = Array.from(artistTypeButtons).find(b => b.dataset.type === 'Live Band');
      if (liveBandButton && liveBandButton.dataset.active === 'true') {
        bandFormatBubbles.style.display = 'block';
      } else {
        bandFormatBubbles.style.display = 'none';
      }
      
      // v93: AUTO-APPLY filters
      applyFilters();
    });
  });
  
  // v93: Band format toggle buttons with AUTO-APPLY
  const bandFormatButtons = document.querySelectorAll('.band-format-toggle');
  bandFormatButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const isActive = btn.dataset.active === 'true';
      btn.dataset.active = (!isActive).toString();
      
      if (btn.dataset.active === 'true') {
        btn.style.background = 'rgba(34, 197, 94, 0.2)';
        btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
        btn.style.color = '#22c55e';
      } else {
        btn.style.background = 'rgba(255,255,255,0.05)';
        btn.style.borderColor = 'rgba(255,255,255,0.2)';
        btn.style.color = 'var(--text-muted)';
      }
      
      // v93: AUTO-APPLY filters
      if (hasActiveFilters()) {
        applyFilters();
      }
    });
  });
  
  // v90: Artist name autocomplete
  searchArtistInput.addEventListener('input', () => {
    const query = searchArtistInput.value.toLowerCase().trim();
    
    if (query.length === 0) {
      artistAutocomplete.style.display = 'none';
      return;
    }
    
    const matches = allArtists
      .filter(a => a.name.toLowerCase().includes(query))
      .slice(0, 10);
    
    if (matches.length === 0) {
      artistAutocomplete.style.display = 'none';
      return;
    }
    
    artistAutocomplete.innerHTML = matches.map(artist => `
      <div style="padding: 8px 12px; cursor: pointer; transition: background 0.2s;" 
           onmouseover="this.style.background='rgba(255,255,255,0.1)'" 
           onmouseout="this.style.background='transparent'"
           onclick="document.getElementById('searchArtist').value = '${artist.name.replace(/'/g, "\\'")}'; document.getElementById('artistAutocomplete').style.display = 'none'; applyFilters();">
        ${artist.name}
      </div>
    `).join('');
    
    artistAutocomplete.style.display = 'block';
  });
  
  // v94: Add Enter key support for artist search
  searchArtistInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      artistAutocomplete.style.display = 'none';
      applyFilters();
    }
  });
  
  // v90: City autocomplete with arrow key navigation
  let cityAutocompleteIndex = -1;
  let cityAutocompleteMatches = [];
  
  searchCityInput.addEventListener('input', () => {
    const query = searchCityInput.value.toLowerCase().trim();
    
    if (query.length === 0) {
      cityAutocomplete.style.display = 'none';
      cityAutocompleteIndex = -1;
      cityAutocompleteMatches = [];
      return;
    }
    
    const matches = cities
      .filter(c => c.city.toLowerCase().includes(query))
      .slice(0, 10);
    
    if (matches.length === 0) {
      cityAutocomplete.style.display = 'none';
      cityAutocompleteIndex = -1;
      cityAutocompleteMatches = [];
      return;
    }
    
    cityAutocompleteMatches = matches;
    cityAutocompleteIndex = -1; // Reset selection when new matches appear
    
    renderCityAutocomplete();
    cityAutocomplete.style.display = 'block';
  });
  
  // Helper function to render city autocomplete with highlight
  function renderCityAutocomplete() {
    cityAutocomplete.innerHTML = cityAutocompleteMatches.map((city, index) => {
      const isHighlighted = index === cityAutocompleteIndex;
      return `
        <div 
          data-city-index="${index}"
          style="
            padding: 8px 12px; 
            cursor: pointer; 
            transition: background 0.2s;
            background: ${isHighlighted ? 'rgba(255,255,255,0.1)' : 'transparent'};
          " 
          onmouseover="this.style.background='rgba(255,255,255,0.1)'" 
          onmouseout="this.style.background='${isHighlighted ? 'rgba(255,255,255,0.1)' : 'transparent'}'"
          onclick="selectCityFromAutocomplete('${city.city.toLowerCase()}')">
          ${city.city}, ${city.state || city.state_id || city.state_code || ''}
        </div>
      `;
    }).join('');
  }
  
  // Helper function to select a city
  window.selectCityFromAutocomplete = function(cityName) {
    searchCityInput.value = cityName;
    cityAutocomplete.style.display = 'none';
    cityAutocompleteIndex = -1;
    cityAutocompleteMatches = [];
    applyFilters();
  };
  
  // Add keyboard navigation for city autocomplete
  searchCityInput.addEventListener('keydown', (e) => {
    if (cityAutocomplete.style.display === 'none' || cityAutocompleteMatches.length === 0) {
      return;
    }
    
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      cityAutocompleteIndex = (cityAutocompleteIndex + 1) % cityAutocompleteMatches.length;
      renderCityAutocomplete();
      
      // Scroll into view if needed
      const highlighted = cityAutocomplete.querySelector(`[data-city-index="${cityAutocompleteIndex}"]`);
      if (highlighted) {
        highlighted.scrollIntoView({ block: 'nearest' });
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      cityAutocompleteIndex = cityAutocompleteIndex <= 0 
        ? cityAutocompleteMatches.length - 1 
        : cityAutocompleteIndex - 1;
      renderCityAutocomplete();
      
      // Scroll into view if needed
      const highlighted = cityAutocomplete.querySelector(`[data-city-index="${cityAutocompleteIndex}"]`);
      if (highlighted) {
        highlighted.scrollIntoView({ block: 'nearest' });
      }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      
      if (cityAutocompleteIndex >= 0 && cityAutocompleteIndex < cityAutocompleteMatches.length) {
        // Select the highlighted city
        const selectedCity = cityAutocompleteMatches[cityAutocompleteIndex];
        selectCityFromAutocomplete(selectedCity.city.toLowerCase());
      } else {
        // No selection, just close and apply filters
        cityAutocomplete.style.display = 'none';
        applyFilters();
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cityAutocomplete.style.display = 'none';
      cityAutocompleteIndex = -1;
    }
  });
  
  // v90: Close autocomplete on click outside
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#searchArtist') && !e.target.closest('#artistAutocomplete')) {
      artistAutocomplete.style.display = 'none';
    }
    if (!e.target.closest('#searchArtistCity') && !e.target.closest('#artistCityAutocomplete')) {
      cityAutocomplete.style.display = 'none';
    }
  });
  
  // v90: Calculate distance between two lat/lon points (Haversine formula)
  function calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 3959; // Earth's radius in miles
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
  }
  
  // v90: Apply filters
  function applyFilters() {
    
    const artistNameFilter = searchArtistInput.value.toLowerCase().trim();
    const cityFilter = searchCityInput.value.toLowerCase().trim();
    const mileRadius = parseInt(mileRadiusInput.value) || 20;
    
    // Get active artist types
    const activeTypes = Array.from(artistTypeButtons)
      .filter(btn => btn.dataset.active === 'true')
      .map(btn => btn.dataset.type);
    
    
    // v94: REMOVED RESTRICTION - No required fields
    // If user types artist name, that takes priority
    
    // Get active lineup options (only if Live Band is active)
    const liveBandActive = activeTypes.includes('Live Band');
    const activeBandFormats = liveBandActive ? Array.from(bandFormatButtons)
      .filter(btn => btn.dataset.active === 'true')
      .map(btn => btn.dataset.format) : [];
    
    
    // Find city coordinates
    let cityCoords = null;
    if (cityFilter && cities && Array.isArray(cities)) {
      const cityData = cities.find(c => c.city.toLowerCase() === cityFilter);
      if (cityData) {
        cityCoords = { lat: cityData.lat, lon: cityData.lon };
      }
    }
    
    // Filter artists
    filteredArtists = allArtists.filter(artist => {
      // v94: If artist name is entered, ONLY filter by name - ignore all other criteria
      if (artistNameFilter) {
        return artist.name.toLowerCase().includes(artistNameFilter);
      }
      
      // If no artist name, apply other filters independently
      
      // Artist type filter - ONLY apply if types are selected
      if (activeTypes.length > 0 && !activeTypes.includes(artist.artist_type)) {
        return false;
      }
      
      // v93: Band format filter (only for Live Bands when Live Band type is selected)
      if (artist.artist_type === 'Live Band' && liveBandActive) {
        if (activeBandFormats.length === 0) {
          // No lineup options selected = don't show any Live Bands
          return false;
        }
        // Some lineup options selected = check if artist matches
        const artistFormats = (artist.band_formats || '').split(',').map(f => f.trim());
        const hasMatchingFormat = artistFormats.some(f => activeBandFormats.includes(f));
        if (!hasMatchingFormat) {
          return false;
        }
      }
      
      // Distance filter - ONLY apply if city is entered
      if (cityCoords && artist.latitude && artist.longitude) {
        const distance = calculateDistance(cityCoords.lat, cityCoords.lon, artist.latitude, artist.longitude);
        if (distance > mileRadius) {
          return false;
        }
      }
      
      return true;
    });
    
    displayArtists();
  }
  
  // v94: Expose applyFilters globally for autocomplete onclick handlers
  window.applyFilters = applyFilters;
  
  // v90: Display artist results
  function displayArtists() {
    // artistSearchResults.textContent = `Found ${filteredArtists.length} artist${filteredArtists.length !== 1 ? 's' : ''}`;
    artistSearchResults.textContent = ''; // Don't show "Found X artists"
    
    if (filteredArtists.length === 0) {
      artistResultsList.innerHTML = '<p style="color: var(--text-muted); padding: 12px;">No artists found matching your criteria.</p>';
      return;
    }
    
    artistResultsList.innerHTML = filteredArtists.map(artist => {
      const formats = artist.artist_type === 'Live Band' && artist.band_formats
        ? ' (' + artist.band_formats.split(',').join(', ') + ')'
        : '';
      const artStyles = artist.artist_type === 'Live Band' && artist.styles
        ? ' · ' + artist.styles.split(',').join(', ')
        : '';
      
      // v93: Check if already preferred
      const isPreferred = preferredArtistIds.has(artist.id);
      
      return `
        <div style="
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 10px 12px;
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 6px;
          margin-bottom: 8px;
          transition: background 0.2s;
        " onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='rgba(255,255,255,0.03)'">
          <div style="flex: 1;">
            <a href="/app/artist-profile.html?artist_id=${artist.id}" target="_blank" style="font-weight: 600; color: #7c6bff; text-decoration: none; font-size: 0.95rem;">${artist.name}</a>
            <span style="color: var(--text-muted); margin-left: 12px; font-size: 0.85rem;">${artist.city || 'N/A'}, ${artist.state || 'N/A'}</span>
            <span style="color: var(--text-muted); margin-left: 12px; font-size: 0.85rem;">${artist.artist_type}${formats}${artStyles}</span>
          </div>
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
          ${bannedArtistIds.has(artist.id) ? `
            <span style="padding:5px 10px;font-size:0.8rem;background:rgba(127,29,29,0.3);border:1px solid rgba(239,68,68,0.4);color:#fca5a5;border-radius:6px;white-space:nowrap;">🚫 Banned</span>
            <button onclick="unbanArtistFromSearch(${artist.id}, '${artist.name.replace(/'/g, "\\'")}', ${venueId})" class="btn ghost" style="padding:5px 10px;font-size:0.8rem;white-space:nowrap;" title="Remove ban — artist can request preferred status again">Remove Ban</button>
          ` : isPreferred ? `
            <span style="padding:5px 10px;font-size:0.8rem;background:rgba(34,197,94,0.2);border:1px solid #22c55e;color:#22c55e;border-radius:6px;white-space:nowrap;">✓ Preferred</span>
            <button onclick="banArtistFromSearch(${artist.id}, '${artist.name.replace(/'/g, "\\'")}', ${venueId})" class="btn" style="padding:5px 10px;font-size:0.8rem;background:#7f1d1d;border:1px solid #ef4444;color:#fca5a5;white-space:nowrap;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
          ` : `
            <button onclick="approvePreferredArtist(${artist.id}, '${artist.name.replace(/'/g, "\\'")}', ${venueId})" class="btn" style="padding:5px 10px;font-size:0.8rem;background:#22c55e;border:1px solid #22c55e;white-space:nowrap;" title="Add as Preferred Artist — they can book your gigs directly">Approve Preferred</button>
            <button onclick="banArtistFromSearch(${artist.id}, '${artist.name.replace(/'/g, "\\'")}', ${venueId})" class="btn" style="padding:5px 10px;font-size:0.8rem;background:#7f1d1d;border:1px solid #ef4444;color:#fca5a5;white-space:nowrap;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
          `}
          </div>
        </div>
      `;
    }).join('');
  }
  
  // v90: Event listeners
  document.getElementById('applyArtistFilters').addEventListener('click', applyFilters);
  document.getElementById('clearArtistFilters').addEventListener('click', () => {
    searchArtistInput.value = '';
    searchCityInput.value = '';
    mileRadiusInput.value = '';
    
    // Reset all artist types to INACTIVE (OFF)
    artistTypeButtons.forEach(btn => {
      btn.dataset.active = 'false';
      btn.style.background = 'rgba(255,255,255,0.05)';
      btn.style.borderColor = 'rgba(255,255,255,0.2)';
      btn.style.color = 'var(--text-muted)';
    });
    
    // Reset all lineup options to active (but hide container)
    bandFormatButtons.forEach(btn => {
      btn.dataset.active = 'true';
      btn.style.background = 'rgba(34, 197, 94, 0.2)';
      btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
      btn.style.color = '#22c55e';
    });
    
    bandFormatBubbles.style.display = 'none';
    
    // Show all artists (no filters)
    filteredArtists = [...allArtists];
    displayArtists();
  });
  
  // v93: Expose functions on window for approvePreferredArtist to call
  window.artistSearch = {
    loadPreferredArtists,
    displayArtists
  };

  window.banArtistFromSearch = async function(artistId, artistName, venueId) {
    const modal = document.createElement('div');
    modal.id = 'banSearchModal';
    modal.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;';
    modal.innerHTML = `
      <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid rgba(239, 68, 68, 0.5); border-radius: 12px; padding: 2rem; max-width: 500px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.5);">
        <h2 style="color: #ffffff; margin-bottom: 1rem; font-size: 1.5rem;">🚫 Ban Artist</h2>
        <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem;">
          <p style="color: #ef4444; margin: 0; font-weight: 500;">Ban <strong>${artistName}</strong> from your venue?</p>
        </div>
        <p style="color: #a1a1aa; margin-bottom: 1rem; font-size: 0.95rem;">
          They will be permanently blocked from booking any gig at your venue — even during blast windows. This cannot be undone without manually removing the ban.
        </p>
        <div style="margin-bottom: 1.5rem;">
          <label style="font-size: 0.85rem; color: #a1a1aa; display: block; margin-bottom: 6px;">Reason <span style="color: #6b7280;">(optional)</span></label>
          <input id="banSearchReasonInput" type="text" placeholder="e.g. No-show, misconduct..."
            style="width: 100%; padding: 8px 12px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; color: #ffffff; font-size: 0.9rem; box-sizing: border-box; outline: none;">
        </div>
        <div style="display: flex; gap: 12px; justify-content: flex-end;">
          <button id="cancelBanSearch" class="btn ghost" style="padding: 8px 16px;">Cancel</button>
          <button id="confirmBanSearch" class="btn" style="padding: 8px 16px; background: #ef4444; border: 1px solid #ef4444;">Confirm Ban</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    document.getElementById('cancelBanSearch').onclick = () => modal.remove();
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.getElementById('confirmBanSearch').onclick = async () => {
      const reason = document.getElementById('banSearchReasonInput').value.trim();
      modal.remove();
      try {
        const r = await fetch(`/api/venues/${venueId}/ban-artist/${artistId}`, {
          method: 'POST', credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({reason})
        });
        if (r.ok) {
          bannedArtistIds.add(artistId);
          preferredArtistIds.delete(artistId);
          displayArtists();
        } else { alert('Failed to ban artist'); }
      } catch(e) { alert('Error banning artist'); }
    };
  };

  window.unbanArtistFromSearch = async function(artistId, artistName, venueId) {
    const modal = document.createElement('div');
    modal.id = 'unbanSearchModal';
    modal.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;';
    modal.innerHTML = `
      <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid rgba(239, 68, 68, 0.5); border-radius: 12px; padding: 2rem; max-width: 500px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.5);">
        <h2 style="color: #ffffff; margin-bottom: 1rem; font-size: 1.5rem;">Remove Ban</h2>
        <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem;">
          <p style="color: #ef4444; margin: 0; font-weight: 500;">Remove ban for <strong>${artistName}</strong>?</p>
        </div>
        <p style="color: #a1a1aa; margin-bottom: 1.5rem; font-size: 0.95rem;">
          They will be able to request preferred artist status again and may appear in future blast emails.
        </p>
        <div style="display: flex; gap: 12px; justify-content: flex-end;">
          <button id="cancelUnbanSearch" class="btn ghost" style="padding: 8px 16px;">Cancel</button>
          <button id="confirmUnbanSearch" class="btn" style="padding: 8px 16px; background: #22c55e; border: 1px solid #22c55e;">Remove Ban</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    document.getElementById('cancelUnbanSearch').onclick = () => modal.remove();
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.getElementById('confirmUnbanSearch').onclick = async () => {
      modal.remove();
      try {
        const r = await fetch(`/api/venues/${venueId}/ban-artist/${artistId}`, {
          method: 'DELETE', credentials: 'include'
        });
        if (r.ok) { bannedArtistIds.delete(artistId); displayArtists(); }
        else { alert('Failed to remove ban'); }
      } catch(e) { alert('Error removing ban'); }
    };
  };
  
  // v90: Initialize
  loadCities();
  loadArtists();
  // Expose refresh so My Artists can sync banned state after ban/unban
  window.refreshSearchArtistsBanned = async function() {
    await loadPreferredArtists();
    if (typeof displayArtists === 'function') displayArtists();
  };

  loadPreferredArtists(); // v93: Load preferred artists on init
  
  // Update My Artists badge after my-artists.js loads
  // v96: Only count approved artists
  const updateArtistsBadge = () => {
    const badge = document.getElementById('artistsBadge');
    if (badge && window.myArtists && window.myArtists.artists) {
      const approvedCount = window.myArtists.artists.filter(a => a.preferred_status === 'approved').length;
      badge.textContent = `(${approvedCount})`;
    }
  };
  
  // Check periodically for my-artists.js to finish loading
  const badgeInterval = setInterval(() => {
    if (window.myArtists && window.myArtists.artists) {
      updateArtistsBadge();
      clearInterval(badgeInterval);
    }
  }, 100);
  
  // Also update badge when tab is switched
  const originalSwitchTab = window.switchTab;
  window.switchTab = function(tabName, button) {
    if (originalSwitchTab) originalSwitchTab(tabName, button);
    if (tabName === 'artists') {
      setTimeout(updateArtistsBadge, 100);
    }
  };

  // Auto-refresh calendar every 60 seconds so bookings by artists appear without manual refresh
  setInterval(async () => {
    const modal = document.getElementById('gigModal');
    if (modal && !modal.classList.contains('hidden')) return; // Don't refresh while modal is open
    invalidateGigs();
    await renderCalendar();
  }, 60000);
}

// v93: Approve Preferred Artist (no popups, silent execution)
async function approvePreferredArtist(artistId, artistName, venueId) {
  
  try {
    const response = await fetch(`/api/venues/${venueId}/preferred-artists/${artistId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include'
    });
    
    if (response.ok) {
      
      // v93: Reload preferred artists list and update search display
      if (window.artistSearch) {
        await window.artistSearch.loadPreferredArtists();
        window.artistSearch.displayArtists();
      } else {
        console.error('❌ v93: window.artistSearch not available!');
      }
      
      // Reload activity center if available
      if (window.activityCenterVenue && typeof window.activityCenterVenue.loadNotifications === 'function') {
        await window.activityCenterVenue.loadNotifications();
      }
      
      // v93: Reload my artists if available
      if (window.myArtists && typeof window.myArtists.loadArtists === 'function') {
        await window.myArtists.loadArtists();
        // Note: render() is called automatically by loadArtists()
        
        // Update badge count - v96: Only count approved
        const badge = document.getElementById('artistsBadge');
        if (badge && window.myArtists.artists) {
          const approvedCount = window.myArtists.artists.filter(a => a.preferred_status === 'approved').length;
          badge.textContent = `(${approvedCount})`;
        }
      } else {
        console.error('❌ v93: window.myArtists.loadArtists not available!');
      }
    } else {
      const error = await response.text();
      console.error(`❌ v93: Failed to approve: ${error}`);
    }
  } catch (error) {
    console.error('❌ v93: Error approving artist:', error);
  }
}

// Prevent search inputs from staying highlighted after Enter key
document.addEventListener('DOMContentLoaded', () => {
  const searchArtistInput = document.getElementById('searchArtist');
  const searchCityInput = document.getElementById('searchArtistCity');
  const mileRadiusInput = document.getElementById('artistMileRadius');
  
  if (searchArtistInput) {
    searchArtistInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        searchArtistInput.blur();
      }
    });
  }
  
  if (searchCityInput) {
    searchCityInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        searchCityInput.blur();
      }
    });
  }
  
  if (mileRadiusInput) {
    mileRadiusInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        mileRadiusInput.blur();
      }
    });
  }
});

// Update notes on a booked gig
async function updateBookedGigNotes(gigId) {
  const textarea = document.getElementById('bookedGigNotes');
  const status = document.getElementById('bookedNotesStatus');
  if (!textarea) return;
  
  const notes = textarea.value.trim();
  
  try {
    const response = await fetch(`/api/gigs/${gigId}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ notes })
    });
    
    if (!response.ok) {
      const errBody = await response.text();
      console.error('Notes update failed:', response.status, errBody);
      throw new Error(`Failed: ${response.status} ${errBody}`);
    }
    
    // Update local cache so reopening modal shows new notes immediately
    if (typeof venueGigsCache !== 'undefined' && venueGigsCache) {
      const cached = venueGigsCache.find(g => g.id === gigId);
      if (cached) cached.notes = notes;
    }

    // Show saved indicator
    if (status) {
      status.style.opacity = '1';
      setTimeout(() => { status.style.opacity = '0'; }, 2500);
    }
  } catch (error) {
    console.error('Error updating gig notes:', error);
    if (status) {
      status.textContent = '✗ Failed to save';
      status.style.color = '#ef4444';
      status.style.opacity = '1';
      setTimeout(() => {
        status.style.opacity = '0';
      }, 3000);
      // Reset for next attempt after fade
      setTimeout(() => {
        status.textContent = '✓ Notes saved';
        status.style.color = 'var(--accent-cyan, #06b6d4)';
      }, 3500);
    }
  }
}

// ============================================
// CANCEL GIG PAYMENT
// ============================================

window._showCancelPaymentModal = function(gigId) {
  // Fetch fee percentage for disclaimer
  fetch('/api/stripe/config', { credentials: 'include' })
    .then(r => r.json())
    .then(cfg => {
      const feePct = cfg.platform_fee_percent != null ? (Number(cfg.platform_fee_percent) % 1 === 0 ? cfg.platform_fee_percent : Number(cfg.platform_fee_percent).toFixed(1)) : '5';
      
      const overlay = document.createElement('div');
      overlay.id = 'cancelPaymentOverlay';
      overlay.style.cssText = 'position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,0.75);display:flex;align-items:center;justify-content:center;';
      overlay.innerHTML = `
        <div style="background:var(--card-bg,#1a1a2e);border:1px solid rgba(239,68,68,0.3);border-radius:12px;max-width:500px;width:92%;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,0.5);">
          <h3 style="margin:0 0 4px 0;color:#ef4444;font-size:1.1rem;">⚠️ Cancel Gig Payment?</h3>
          <p style="color:var(--text-muted,#999);font-size:0.85rem;margin:0 0 18px 0;line-height:1.5;">
            Are you sure you want to cancel this gig's payment to the artist?
          </p>
          
          <label style="display:block;font-size:0.85rem;color:var(--text,#fff);font-weight:600;margin-bottom:6px;">Reason for not paying artist:</label>
          <textarea id="cancelPaymentReason" rows="3" placeholder="Please explain why you are cancelling this payment..." 
            style="width:100%;padding:10px 14px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:#fff;font-size:0.85rem;resize:vertical;box-sizing:border-box;font-family:inherit;"></textarea>
          
          <p style="color:rgba(239,68,68,0.7);font-size:0.75rem;margin:12px 0 6px 0;line-height:1.5;">
            ⚠️ Cancelling this gig's payment still requires the Venue to pay the GigsFill platform fee of ${feePct}%.
          </p>
          <p style="color:var(--text-muted,#777);font-size:0.72rem;margin:0 0 18px 0;line-height:1.4;">
            <em>Disclaimer: Payment disputes are between the Venue and Artist. GigsFill is not involved in resolving payment disputes 
            and is not responsible for the outcome. The artist may contact you directly regarding this matter.</em>
          </p>
          
          <div style="display:flex;gap:10px;justify-content:flex-end;">
            <button id="cancelPayOverlayClose" class="btn ghost" style="padding:8px 18px;font-size:0.85rem;">Never Mind</button>
            <button id="cancelPayConfirmBtn" class="btn" style="background:#ef4444;color:#fff;padding:8px 18px;font-size:0.85rem;border:none;border-radius:6px;cursor:pointer;">Confirm Cancel Payment</button>
          </div>
          <div id="cancelPayStatus" style="margin-top:10px;text-align:center;font-size:0.85rem;"></div>
        </div>
      `;
      document.body.appendChild(overlay);
      
      function closeCancelPaymentAndGigModal() {
        overlay.remove();
        const gigModal = document.getElementById('gigModal');
        if (gigModal) gigModal.classList.add('hidden');
      }
      overlay.querySelector('#cancelPayOverlayClose').onclick = closeCancelPaymentAndGigModal;
      overlay.addEventListener('click', (e) => { if (e.target === overlay) closeCancelPaymentAndGigModal(); });
      
      overlay.querySelector('#cancelPayConfirmBtn').onclick = async () => {
        const reason = document.getElementById('cancelPaymentReason').value.trim();
        if (!reason) { alert('Please provide a reason for cancelling this payment.'); return; }
        
        const btn = overlay.querySelector('#cancelPayConfirmBtn');
        const status = overlay.querySelector('#cancelPayStatus');
        btn.disabled = true;
        btn.textContent = 'Processing...';
        
        try {
          const res = await fetch('/api/stripe/cancel-gig-payment', {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gig_id: gigId, reason: reason })
          });
          
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            const msg = Array.isArray(err.detail) ? err.detail.join(' ') : (err.detail || 'Cancel failed');
            throw new Error(typeof msg === 'string' ? msg : 'Cancel failed');
          }
          
          const result = await res.json();
          status.style.color = '#22c55e';
          const fee = result.platform_fee_charged != null ? result.platform_fee_charged : 0;
          status.textContent = result.message || (fee > 0 ? '✓ Payment cancelled. Platform fee of $' + (fee / 100).toFixed(2) + ' charged.' : '✓ Payment cancelled.');
          btn.style.display = 'none';
          overlay.querySelector('#cancelPayOverlayClose').textContent = 'Close';
          overlay.querySelector('#cancelPayOverlayClose').onclick = () => {
            overlay.remove();
            const gigModal = document.getElementById('gigModal');
            if (gigModal) gigModal.classList.add('hidden');
          };
          
          // Hide the cancel payment button in the gig modal
          const cpb = document.getElementById('cancelGigPaymentBtn');
          if (cpb) cpb.style.display = 'none';
          
          // Refresh activity center
          if (window.activityCenterVenue) window.activityCenterVenue.loadNotifications();
          // Refresh billing
          if (typeof loadVenueBillingHistory === 'function') loadVenueBillingHistory();
          
        } catch(e) {
          btn.disabled = false;
          btn.textContent = 'Confirm Cancel Payment';
          status.style.color = '#ef4444';
          status.textContent = '✗ ' + e.message;
        }
      };
    })
    .catch(() => {
      alert('Unable to load fee configuration.');
    });
};

// ============================================
// COUNTERSIGN FUNCTIONS
// ============================================

window._doCountersign = async function(contractId) {
  // Support both legacy id and new per-contract id from unified modal
  const nameEl = document.getElementById('modalCountersignName_' + contractId)
               || document.getElementById('modalCountersignName');
  const btn    = document.getElementById('modalCountersignBtn');
  const status = document.getElementById('modalCountersignStatus');
  
  const name = (nameEl && nameEl.value || '').trim();
  if (!name) { alert('Please type your full legal name.'); return; }
  
  btn.disabled = true;
  btn.textContent = 'Countersigning...';
  if (status) status.textContent = '';
  
  try {
    const res = await fetch('/api/gig-contracts/' + contractId + '/countersign', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signature_name: name })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Countersign failed');
    }
    if (status) { status.style.color = '#22c55e'; status.textContent = '✓ Contract countersigned! Booking confirmed.'; }
    btn.style.display = 'none';
    // Refresh calendar, activity center, and executed contracts after short delay
    setTimeout(() => {
      const modal = document.getElementById('gigModal');
      if (modal) modal.classList.add('hidden');
      if (typeof window.invalidateGigs === 'function' && typeof window.renderCalendar === 'function') {
        window.invalidateGigs();
        window.renderCalendar();
      }
      // Refresh activity center
      if (window.activityCenterVenue) window.activityCenterVenue.loadNotifications();
      // Refresh executed contracts list
      if (window.venueContracts && window.venueContracts.loadExecuted) window.venueContracts.loadExecuted();
      // Refresh Payments tab so new transaction appears
      if (typeof loadVenueBillingHistory === 'function') loadVenueBillingHistory();
    }, 1500);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Countersign & Confirm Booking';
    if (status) { status.style.color = '#ef4444'; status.textContent = '✗ ' + e.message; }
  }
};

// Called from Activity Center countersign link
window.showCountersignModal = async function(gigId) {
  try {
    // Fetch gig data from cached gigs or API
    const gigsRes = await fetch('/gigs', { credentials: 'include' });
    if (!gigsRes.ok) throw new Error('Failed to load gigs');
    const allGigs = await gigsRes.json();
    const gig = allGigs.find(g => g.id === gigId);
    if (!gig) throw new Error('Gig not found');
    
    // Use the existing modal system (openGigModal handles pending_contract)
    if (typeof window.openGigModal === 'function') {
      window.openGigModal(gig);
      // Show the modal if hidden
      const modal = document.getElementById('gigModal');
      if (modal) modal.classList.remove('hidden');
    } else {
      // Fallback: build a standalone overlay
      const cRes = await fetch('/api/gigs/' + gigId + '/contract', { credentials: 'include' });
      if (!cRes.ok) throw new Error('No contract found');
      const cData = await cRes.json();
      if (!cData.has_contract || !cData.id) throw new Error('No contract found');
      
      const fullRes = await fetch('/api/gig-contracts/' + cData.id, { credentials: 'include' });
      if (!fullRes.ok) throw new Error('Failed to load contract');
      const fc = await fullRes.json();
      
      const body = fc.rendered_body || fc.contract_body || '';
      const artistSig = fc.artist_signature_name || fc.artist_name || 'Artist';
      const artistSigDate = fc.artist_signature_date ? new Date(fc.artist_signature_date).toLocaleDateString() : '';
      const contractLinkHtml = (fc.signed_pdf_path) ? ` <a href="${fc.signed_pdf_path}" target="_blank" rel="noopener" style="color:var(--cyan);font-size:0.85rem;margin-left:6px;">View contract</a>` : '';
      
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;';
      overlay.innerHTML = `
        <div style="background:var(--card-bg,#1a1a2e);border:1px solid rgba(255,255,255,0.1);border-radius:12px;max-width:600px;width:90%;max-height:85vh;overflow-y:auto;padding:24px;">
          <h3 style="margin:0 0 16px 0;color:var(--text,#fff);font-size:1.1rem;">📋 Countersign Contract</h3>
          ${body ? '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:16px;max-height:250px;overflow-y:auto;margin-bottom:12px;font-size:0.82rem;line-height:1.7;color:var(--text,#fff);">' + body + '</div>' : ''}
          <div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.2);border-radius:8px;padding:10px;margin-bottom:14px;">
            <p style="margin:0;font-size:0.8rem;color:#22c55e;">✓ Contract signed by: <strong>${artistSig}</strong>${artistSigDate ? ' on ' + artistSigDate : ''}${contractLinkHtml}</p>
          </div>
          <div style="border-top:1px solid rgba(255,255,255,0.1);padding-top:14px;">
            <label style="display:block;font-size:0.85rem;color:var(--text-muted,#999);margin-bottom:6px;font-weight:600;">Your Full Legal Name (Venue Countersignature)</label>
            <input type="text" id="modalCountersignName" placeholder="Type your full legal name" style="width:100%;padding:10px 14px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:#fff;font-size:0.9rem;box-sizing:border-box;">
            <div style="margin-top:12px;display:flex;align-items:center;gap:12px;">
              <button onclick="window._doCountersign(${cData.id})" id="modalCountersignBtn" class="btn primary" style="padding:10px 24px;">Countersign & Confirm Booking</button>
              <button onclick="this.closest('div[style*=fixed]').remove()" class="btn ghost" style="padding:10px 16px;">Cancel</button>
              <span id="modalCountersignStatus" style="font-size:0.85rem;"></span>
            </div>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    }
  } catch(e) {
    alert('Could not load contract: ' + e.message);
  }
};

// ─── New Gig Blast Prompt ─────────────────────────────────────────────────────
// Shows a per-gig table modal respecting Email Center preferences.
// gigEntries: [{id, date, daysUntil, slotCount}]
// blastSettings: from window.venueBlinkSettings
window._showNewGigBlastPrompt = async function(gigEntries, blastSettings, _venueId) {
  if (!gigEntries || gigEntries.length === 0) return;

  // Determine which windows apply from Email Center settings
  const s36h   = blastSettings && blastSettings['open_gig_36h'];
  const s1w    = blastSettings && blastSettings['open_gig_1w'];
  const s2w    = blastSettings && blastSettings['open_gig_2w'];
  const s4w    = blastSettings && blastSettings['open_gig_4w'];

  // blast_all_enabled on the 36h setting controls the radius blast option
  const radiusEnabled = s36h && s36h.blast_all_enabled;
  const radiusMiles   = (s36h && s36h.blast_all_radius) || 20;

  // Window thresholds in days
  // Manual blast — show ALL created gigs regardless of Email Center window settings
  // (Email Center windows are for automated scheduled blasts only)
  const windowForGig = (d) => {
    if (d <= 1)  return '36h';
    if (d <= 7)  return '1w';
    if (d <= 14) return '2w';
    if (d <= 28) return '4w';
    return 'future'; // beyond 4 weeks — still show so venue can blast upcoming gigs
  };

  // Show all created gigs
  const rows = gigEntries.slice();

  const fmt = d => {
    const [y,mo,day] = d.split('-');
    return new Date(parseInt(y), parseInt(mo)-1, parseInt(day))
      .toLocaleDateString('en-US', {month:'numeric', day:'numeric', year:'2-digit'});
  };

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);backdrop-filter:blur(4px);display:flex;align-items:center;justify-content:center;z-index:10002;padding:16px;box-sizing:border-box;';

  let tableRows = rows.map(g => {
    const win = windowForGig(g.daysUntil);
    const showRadius = radiusEnabled && (win === '36h' || win === '1w');
    const prefDefaultChecked = true; // always default preferred on
    const radDefaultChecked  = radiusEnabled && win === '36h'; // radius default checked only at 36h

    return `<tr data-gig-id="${g.id}" data-show-radius="${showRadius}">
      <td style="padding:10px 12px;font-size:0.88rem;color:#f0f0f0;white-space:nowrap;font-weight:600;">${fmt(g.date)}</td>
      <td style="padding:10px 12px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:0.82rem;color:#d1d5db;cursor:pointer;">
          <input type="checkbox" class="pref-cb" data-gig="${g.id}" ${prefDefaultChecked ? 'checked' : ''} style="width:15px;height:15px;accent-color:#635bff;">
          All Preferred Artists
        </label>
      </td>
      <td style="padding:10px 12px;">
        ${showRadius ? `<label style="display:flex;align-items:center;gap:6px;font-size:0.82rem;color:#d1d5db;cursor:pointer;">
          <input type="checkbox" class="radius-cb" data-gig="${g.id}" ${radDefaultChecked ? 'checked' : ''} style="width:15px;height:15px;accent-color:#f59e0b;">
          All Artists within ${radiusMiles} mi
        </label>` : `<span style="font-size:0.78rem;color:#555;">—</span>`}
      </td>
    </tr>`;
  }).join('');

  overlay.innerHTML = `
    <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:1px solid rgba(99,91,255,0.4);border-radius:14px;padding:1.75rem;max-width:600px;width:100%;box-shadow:0 16px 48px rgba(0,0,0,0.6);">
      <h2 style="color:#f0f0f0;font-size:1.1rem;font-weight:700;margin:0 0 6px 0;">⚡ Send Blast Emails?</h2>
      <p style="color:#888;font-size:0.82rem;margin:0 0 16px 0;">These gigs fall within your Email Center blast windows. Choose which to blast:</p>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;min-width:400px;">
          <thead>
            <tr style="border-bottom:1px solid rgba(255,255,255,0.1);">
              <th style="padding:8px 12px;text-align:left;font-size:0.75rem;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Date</th>
              <th style="padding:8px 12px;text-align:left;font-size:0.75rem;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Preferred Artists</th>
              <th style="padding:8px 12px;text-align:left;font-size:0.75rem;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Radius Blast</th>
            </tr>
          </thead>
          <tbody id="_blastTbody">
            ${tableRows}
          </tbody>
        </table>
      </div>
      <div style="margin-top:20px;display:flex;gap:12px;justify-content:flex-end;">
        <button id="_blastSkip" class="btn ghost">Skip</button>
        <button id="_blastSend" class="btn primary">Send Selected Blasts</button>
      </div>
      <p id="_blastStatus" style="margin:10px 0 0;font-size:0.82rem;color:#a78bfa;text-align:right;display:none;"></p>
    </div>
  `;
  document.body.appendChild(overlay);

  overlay.querySelector('#_blastSkip').onclick = () => {
    overlay.remove();
    showGigSuccess('Gig created successfully!');
  };

  overlay.querySelector('#_blastSend').onclick = async () => {
    const btn = overlay.querySelector('#_blastSend');
    const status = overlay.querySelector('#_blastStatus');
    btn.disabled = true;
    btn.textContent = 'Sending...';
    status.style.display = 'block';

    // Collect all gig selections and send ONE batch request (one email per artist)
    const gigSelections = [];
    for (const g of rows) {
      const prefCb   = overlay.querySelector(`.pref-cb[data-gig="${g.id}"]`);
      const radiusCb = overlay.querySelector(`.radius-cb[data-gig="${g.id}"]`);
      const doPreferred = prefCb && prefCb.checked;
      const doRadius    = radiusCb && radiusCb.checked;
      if (!doPreferred && !doRadius) continue;
      gigSelections.push({ id: g.id, blast_preferred: doPreferred, blast_all: doRadius, blast_radius: radiusMiles });
    }


    let totalSent = 0;
    if (gigSelections.length > 0) {
      status.textContent = 'Sending batch blast…';
      try {
        const res = await fetch(`/api/venues/${_venueId}/batch-blast`, {
          method: 'POST',
          credentials: 'include',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ gigs: gigSelections })
        });
        const data = await res.json();
        totalSent = data.sent || 0;
      } catch (e) {
      }
    } else {
    }

    overlay.remove();
    if (typeof window.invalidateGigs === 'function') window.invalidateGigs();
    if (typeof window.renderCalendar === 'function') window.renderCalendar();

    // Show result — doubles as the gig creation confirmation
    const done = document.createElement('div');
    done.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;z-index:10003;';
    done.innerHTML = `
      <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:2px solid #22c55e;border-radius:12px;padding:2rem;max-width:380px;text-align:center;">
        <div style="font-size:2.5rem;margin-bottom:1rem;">✓</div>
        <p style="color:#f0f0f0;margin:0 0 1.5rem;font-size:0.95rem;line-height:1.5;">
          ${gigSelections.length > 0
            ? 'Gig created! Emails sent — artists will be alerted to your new gig!'
            : 'Gig created successfully!'}
        </p>
        <button class="btn primary" style="min-width:100px;">Done</button>
      </div>
    `;
    document.body.appendChild(done);
    done.querySelector('button').onclick = () => done.remove();
    done.addEventListener('click', e => { if (e.target === done) done.remove(); });
  };
};

