// -----------------------------
// HELPERS
// -----------------------------

/**
 * Resize an image File to fit within maxW×maxH while preserving aspect ratio.
 * Returns a Blob (image/jpeg, quality 0.92). Never upscales.
 */
function resizeImageForProfile(file, maxW, maxH) {
  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      let { width: w, height: h } = img;
      const scale = Math.min(1, maxW / w, maxH / h);
      w = Math.round(w * scale);
      h = Math.round(h * scale);
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      canvas.getContext('2d').drawImage(img, 0, 0, w, h);
      canvas.toBlob(blob => resolve(blob), 'image/jpeg', 0.92);
    };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
    img.src = url;
  });
}

/** Wire up drag-and-drop reorder on the Social Media #socialGrid. Each child
 *  `.social-row` carries `data-brand="..."`; on dragend we collect the brands
 *  in current DOM order, comma-join, and PUT `social_order` to the artist
 *  endpoint. Public profile reads the field and renders tiles in that order. */
function setupSocialReorder(artistId) {
  const grid = document.getElementById('socialGrid');
  if (!grid || grid.dataset.dndWired) return;
  grid.dataset.dndWired = '1';
  let dragged = null;
  let didMove = false;

  grid.addEventListener('dragstart', e => {
    if (!(e.target instanceof HTMLElement)) return;
    const handle = e.target.closest('.drag-handle');
    if (!handle) return;
    const row = handle.closest('.social-row');
    if (!row) return;
    dragged = row;
    row.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });

  grid.addEventListener('dragover', e => {
    e.preventDefault();
    if (!dragged) return;
    if (!(e.target instanceof HTMLElement)) return;
    const row = e.target.closest('.social-row');
    if (!row || row === dragged) return;
    const rect = row.getBoundingClientRect();
    const after = e.clientY > rect.top + rect.height / 2;
    grid.insertBefore(dragged, after ? row.nextSibling : row);
    didMove = true;
  });

  grid.addEventListener('dragend', async () => {
    if (!dragged) return;
    dragged.classList.remove('dragging');
    if (didMove) {
      const order = [...grid.querySelectorAll('.social-row')]
        .map(r => r.dataset.brand).filter(Boolean).join(',');
      try {
        // The artist update endpoint is /artists/{id} (no /api prefix) —
        // bindAutosave() above this in this same file uses that path; the
        // /api/artists/{id} route is GET-only and silently 405'd this PUT.
        const r = await fetch(`/artists/${artistId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ social_order: order }),
        });
        if (!r.ok) console.warn('social_order save returned', r.status);
      } catch (e) { console.warn('social_order save failed', e); }
    }
    dragged = null;
    didMove = false;
  });
}


/** HTML-escape any string that will be interpolated into innerHTML / template
 *  literals. Without this a user typing `</textarea>` in the caption field
 *  would break the surrounding HTML. */
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function qs(id) {
  return document.getElementById(id);
}

function getArtistId() {
  const params = new URLSearchParams(window.location.search);
  return params.get("artist_id");
}

function bindAutosave(input, field, artistId) {
  if (!input) return;

  // Enter = blur (for text inputs)
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      input.blur();
    }
  });

  const save = async () => {
    // City validation - block save if city is invalid, auto-fill state if valid
    if (field === 'city') {
      const val = input.value.trim();
      if (val) {
        const stateEl = document.getElementById('state');
        const stateVal = stateEl ? stateEl.value : '';
        try {
          let vr = await fetch('/api/validate-city?city=' + encodeURIComponent(val) + (stateVal ? '&state=' + encodeURIComponent(stateVal) : '') + '&_t=' + Date.now());
          let vd = await vr.json();
          if (!vd.valid && stateVal) {
            vr = await fetch('/api/validate-city?city=' + encodeURIComponent(val) + '&_t=' + Date.now());
            vd = await vr.json();
          }
          if (vd.valid) {
            if (typeof showCityError === 'function') showCityError(input, false);
            if (vd.state && stateEl && stateEl.value !== vd.state) {
              stateEl.value = vd.state;
              stateEl.dispatchEvent(new Event('change', { bubbles: true }));
            }
          } else {
            if (typeof showCityError === 'function') showCityError(input, true);
            return; // Don't save invalid city
          }
        } catch(e) {
          return;
        }
      }
    }

    // Normalize URL fields — add https:// scheme if missing. ONLY prepend
    // `www.` when the host is a bare apex domain (e.g. `spotify.com` →
    // `www.spotify.com`). If the host already has a subdomain
    // (`open.spotify.com`, `music.apple.com`, `youtu.be`), leave it alone —
    // adding `www.` in front breaks those (Spotify's `open` subdomain
    // doesn't resolve as `www.open.spotify.com`). Jul 25 2026 bug fix.
    const urlFields = ['website_url','facebook_url','instagram_url','twitter_url','youtube_url','spotify_url','tiktok_url'];
    if (urlFields.includes(field) && input.value.trim()) {
      let url = input.value.trim();
      if (!/^https?:\/\//i.test(url)) {
        url = 'https://' + url;
      }
      try {
        const u = new URL(url);
        // Count dots in hostname: 1 dot = bare apex (`spotify.com`); 2+ = subdomain.
        const dotCount = (u.hostname.match(/\./g) || []).length;
        if (dotCount === 1 && !u.hostname.startsWith('www.')) {
          u.hostname = 'www.' + u.hostname;
          url = u.toString();
        }
      } catch (_) { /* malformed URL — leave user's typed value alone */ }
      input.value = url;
    }

    // Audit fix (May 2026 part 3): surface autosave failures instead of
    // silently dropping them. Previously a 4xx/5xx (expired cookie, bad
    // validation, server down) returned a discarded Promise and the user
    // assumed the field was saved.
    try {
      const _res = await fetch(`/artists/${artistId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ [field]: input.value.trim() })
      });
      if (!_res.ok) {
        let _detail = "";
        try { const _j = await _res.json(); _detail = _j && _j.detail ? _j.detail : ""; } catch (_) {}
        const _msg = _detail || `Couldn't save (HTTP ${_res.status}). Try again or refresh the page.`;
        if (window.showErrorModal) window.showErrorModal("Save failed", _msg);
        else console.warn("[artist.edit autosave]", field, _msg);
      } else if (field === 'name' && typeof window.reloadVanityUrl === 'function') {
        // Jul 2026: refresh the vanity URL section after a name change
        // so the URL updates in place (backend may have auto-migrated
        // the slug — see maybe_update_slug_on_rename in vanity.py).
        window.reloadVanityUrl('artist', parseInt(artistId, 10));
      }
    } catch (_e) {
      const _msg = "Couldn't reach the server. Check your connection and try again.";
      if (window.showErrorModal) window.showErrorModal("Save failed", _msg);
      else console.warn("[artist.edit autosave]", field, _e);
    }
  };

  input.addEventListener("blur", save);
  input.addEventListener("change", save); // ← REQUIRED for select
}

// -----------------------------
// LOAD ARTIST
// -----------------------------
async function loadArtist() {
  const artistId = getArtistId();
  if (!artistId) return;

  // -----------------------------
  // FIX HEADER NAV LINKS
  // -----------------------------
  if (typeof window.applyVanityToLinks === "function") {
    window.applyVanityToLinks("artist", artistId, ["#artistProfileBtn"]);
  }

  const bookGigsBtn = document.getElementById("bookGigsBtn");
  if (bookGigsBtn) {
    bookGigsBtn.href = `/app/artist-book-gigs.html?artist_id=${artistId}`;
  }

  const res = await fetch(`/artists/${artistId}`, {
    credentials: "include"
  });

  if (!res.ok) {
    console.error("Failed to load artist");
    return;
  }

  const artist = await res.json();

  // POPULATE
  qs("name").value = artist.name || "";
  bindAutosave(qs("name"), "name", artistId);
  
  qs("city").value = artist.city || "";
  qs("state").value = artist.state || "";
  qs("bio").value = artist.bio || "";

  // Social Media
  if (qs("spotify_url")) qs("spotify_url").value = artist.spotify_url || "";
  if (qs("instagram_url")) qs("instagram_url").value = artist.instagram_url || "";
  if (qs("facebook_url")) qs("facebook_url").value = artist.facebook_url || "";
  if (qs("youtube_url")) qs("youtube_url").value = artist.youtube_url || "";
  if (qs("twitter_url")) qs("twitter_url").value = artist.twitter_url || "";
  if (qs("tiktok_url")) qs("tiktok_url").value = artist.tiktok_url || "";
  if (qs("website_url")) qs("website_url").value = artist.website_url || "";

  // AUTOSAVE
  bindAutosave(qs("city"), "city", artistId);
  bindAutosave(qs("state"), "state", artistId);

  // Social Media Autosave
  if (qs("spotify_url")) bindAutosave(qs("spotify_url"), "spotify_url", artistId);
  if (qs("instagram_url")) bindAutosave(qs("instagram_url"), "instagram_url", artistId);
  if (qs("facebook_url")) bindAutosave(qs("facebook_url"), "facebook_url", artistId);
  if (qs("youtube_url")) bindAutosave(qs("youtube_url"), "youtube_url", artistId);
  if (qs("twitter_url")) bindAutosave(qs("twitter_url"), "twitter_url", artistId);
  if (qs("tiktok_url")) bindAutosave(qs("tiktok_url"), "tiktok_url", artistId);
  if (qs("website_url")) bindAutosave(qs("website_url"), "website_url", artistId);

  // Reorder the social rows in DOM to match the saved `social_order` (a
  // comma-separated list of brand keys). Rows missing from the saved order
  // stay in their original position relative to each other at the end. The
  // user can then drag-reorder, and the new order saves back via `setupSocialReorder`.
  if (artist.social_order) {
    const grid = qs("socialGrid");
    if (grid) {
      const want = artist.social_order.split(',').map(s => s.trim()).filter(Boolean);
      const rows = Array.from(grid.querySelectorAll('.social-row'));
      const byBrand = Object.fromEntries(rows.map(r => [r.dataset.brand, r]));
      // Append rows in the saved order first; any not in want come after.
      want.forEach(b => { if (byBrand[b]) grid.appendChild(byBrand[b]); });
      rows.forEach(r => { if (!want.includes(r.dataset.brand)) grid.appendChild(r); });
    }
  }
  setupSocialReorder(artistId);

  // ARTIST TYPE AUTOSAVE
  const artistTypeEl = qs("artist_type");
  const formatsBlock = qs("bandFormatsBlock");
  const formatChecks = formatsBlock.querySelectorAll("input[type=checkbox]");
  const body = document.body;
  
  function getSelectedFormats() {
    return [...formatChecks].filter(c => c.checked).map(c => c.value);
  }
  
  function lockPage(lock) {
    body.classList.toggle("page-locked", lock);
  }
  
  let blinkInterval = null;
  
  function startBlink() {
    if (blinkInterval) return;
    blinkInterval = setInterval(() => {
      formatsBlock.classList.add("blink");
      setTimeout(() => formatsBlock.classList.remove("blink"), 400);
    }, 800);
  }
  
  function stopBlink() {
    clearInterval(blinkInterval);
    blinkInterval = null;
    formatsBlock.classList.remove("blink");
  }
  
  async function saveArtistType(artistId, payload) {
    // Audit fix (May 2026 part 3): surface autosave failures instead of
    // silently dropping them.
    try {
      const _res = await fetch(`/artists/${artistId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload)
      });
      if (!_res.ok) {
        let _detail = "";
        try { const _j = await _res.json(); _detail = _j && _j.detail ? _j.detail : ""; } catch (_) {}
        const _msg = _detail || `Couldn't save artist type (HTTP ${_res.status}).`;
        if (window.showErrorModal) window.showErrorModal("Save failed", _msg);
      }
    } catch (_e) {
      if (window.showErrorModal) window.showErrorModal("Save failed", "Couldn't reach the server.");
    }
  }
  

  function initMediaDragAndDrop(containerId) {
    const container = qs(containerId);
    if (!container) return;
  
    let dragged = null;
    let didMove = false;

  
    container.addEventListener("dragstart", e => {
      if (!(e.target instanceof HTMLElement)) return;

      let card = null;

      // Audio: drag only from handle. The handle is inside .audio-row, but
      // .audio-row is wrapped in .audio-entry (which contains the caption
      // textarea above the row). Reorder must move the entry — the row alone
      // would orphan the caption from its audio.
      if (container.id === "audio") {
        const handle = e.target.closest(".drag-handle");
        if (!handle) return;
        card = handle.closest(".audio-entry");
      }
      // Pictures & Videos: drag whole card
      else {
        card = e.target.closest(".media-card");
      }

      if (!card) return;

      dragged = card;
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });



    container.addEventListener("dragend", async () => {
      if (!dragged) return;

      dragged.classList.remove("dragging");

      if (didMove) {
        const selector = container.id === "audio" ? ".audio-entry" : ".media-card";
        const ids = [...container.querySelectorAll(selector)]
          .map((el, i) => ({
            id: el.dataset.id,
            display_order: i
          }));
    
        for (const item of ids) {
          await fetch(`/api/media/${item.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({ display_order: item.display_order })
          });
        }
      }
    
      dragged = null;
      didMove = false;
    });
    
  
    container.addEventListener("dragover", e => {
      e.preventDefault();
    
      if (!dragged) return;
      if (!(e.target instanceof HTMLElement)) return;
    
      // Support .media-card (pics/videos) and .audio-entry (audio with caption above row)
      const card = e.target.closest(".media-card") || e.target.closest(".audio-entry");
      if (!card || card === dragged) return;
    
      const rect = card.getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;
    
      container.insertBefore(
        dragged,
        after ? card.nextSibling : card
      );
    
      didMove = true;
    });

    // -----------------------------
    // BIO SAVE (MANUAL)
    // -----------------------------
    const bioEl = qs("bio");
    const saveBioBtn = qs("saveBioBtn");
    const bioStatus = qs("bioStatus");

    if (saveBioBtn && bioEl) {
      saveBioBtn.addEventListener("click", async () => {
        bioStatus.textContent = "Bio saving...";
        bioStatus.style.color = "#aaa";

        try {
          const res = await fetch(`/artists/${artistId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({
              bio: bioEl.value
            })
          });

          if (!res.ok) throw new Error("Save failed");

          bioStatus.textContent = "Bio saved";
          bioStatus.style.color = "#6fe36f";

          setTimeout(() => {
            bioStatus.textContent = "";
          }, 2000);
        } catch (err) {
          bioStatus.textContent = "Error saving bio";
          bioStatus.style.color = "#ff6b6b";
        }
      });
    }

    
    
  }
  
  

  function initArtistType(artistId, artist) {

    // ✅ FIX: Populate dropdown options
    artistTypeEl.innerHTML = `
      <option value="">Select Artist Type</option>
      <option value="Live Band">Live Band</option>
      <option value="DJ">DJ</option>
      <option value="Comedian">Comedian</option>
      <option value="Trivia Host">Trivia Host</option>
      <option value="Open Mic MC">Open Mic MC</option>
      <option value="Karaoke MC">Karaoke MC</option>
    `;

    // Populate styles + lineup pills (Jul 1 2026: matches the Search
    // Gigs page toggle style — inactive pills are muted grey, active
    // pills go green). Hidden checkboxes underneath preserve the
    // existing form contract; the label click flips the checkbox
    // natively, and a change listener syncs the pill's visual state
    // via a data-active flag.
    const formatsContainer = formatsBlock.querySelector('.band-formats') || formatsBlock;
    const _STYLE_OPTIONS = ['Country','Hip-Hop','Indie','Jazz','Latin','Pop','Reggae','Rock'];
    const _LINEUP_OPTIONS = ['Solo','Duo','Trio','Full Band'];
    const _pillCss = 'padding:4px 8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.2); color:var(--text-muted); border-radius:6px; cursor:pointer; transition:all 0.2s; font-size:0.75rem; font-weight:500; user-select:none; display:inline-block;';
    const _sectionLabelCss = 'font-weight:600; font-size:0.85rem; color:var(--text-gray,#94a3b8); display:block; margin-bottom:8px;';
    const _pillsRowCss = 'display:flex; gap:8px; flex-wrap:wrap;';
    function _pillHtml(name, val) {
      return `<label class="artist-edit-pill" data-active="false" style="${_pillCss}">`
           + `<input type="checkbox" name="${name}" value="${val}" style="position:absolute; opacity:0; width:0; height:0; pointer-events:none;">`
           + `<span>${val}</span></label>`;
    }
    formatsContainer.innerHTML = `
      <div style="margin-bottom:14px;">
        <label style="${_sectionLabelCss}">Styles (select at least one)</label>
        <div style="${_pillsRowCss}" id="stylesChecks">
          ${_STYLE_OPTIONS.map(v => _pillHtml('artist_style', v)).join('')}
        </div>
      </div>
      <div>
        <label style="${_sectionLabelCss}">Lineup (select at least one)</label>
        <div style="${_pillsRowCss}" id="lineupChecks">
          ${_LINEUP_OPTIONS.map(v => _pillHtml('band_format', v)).join('')}
        </div>
      </div>
    `;
    // Sync pill visual state from its underlying checkbox. Active =
    // green, inactive = muted. Runs on load (after `checked` is set
    // from artist data) and on every subsequent click.
    function _syncPillFromCb(cb) {
      const pill = cb.closest('.artist-edit-pill');
      if (!pill) return;
      if (cb.checked) {
        pill.setAttribute('data-active', 'true');
        pill.style.background = 'rgba(34, 197, 94, 0.2)';
        pill.style.borderColor = 'rgba(34, 197, 94, 0.5)';
        pill.style.color = '#22c55e';
      } else {
        pill.setAttribute('data-active', 'false');
        pill.style.background = 'rgba(255,255,255,0.05)';
        pill.style.borderColor = 'rgba(255,255,255,0.2)';
        pill.style.color = 'var(--text-muted)';
      }
    }
    // Wire change listener so click-on-label (native checkbox toggle)
    // triggers the sync. The existing save-on-change handler further
    // below already fires on `change` — this listener runs first and
    // just updates styling.
    formatsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => _syncPillFromCb(cb));
    });

    // Re-query checkboxes after populating
    const formatChecks = formatsBlock.querySelectorAll('input[name="band_format"]');
    const styleChecks = formatsBlock.querySelectorAll('input[name="artist_style"]');
    const allChecks = formatsBlock.querySelectorAll('input[type=checkbox]');

    function getSelectedFormats() {
      return [...formatChecks].filter(c => c.checked).map(c => c.value);
    }
    function getSelectedStyles() {
      return [...styleChecks].filter(c => c.checked).map(c => c.value);
    }
    function isValid() {
      return getSelectedFormats().length > 0 && getSelectedStyles().length > 0;
    }

    // Set current value
    artistTypeEl.value = artist.artist_type || "";

    // Jul 1 2026: MC-type equipment gate. Show the "I bring my own
    // equipment" checkbox only for Open Mic MC / Karaoke MC types.
    const mcEquipBlock = document.getElementById("mcEquipmentBlock");
    const mcEquipCb = document.getElementById("has_own_equipment");
    function _syncMcEquipBlock(type) {
      const isMC = type === "Open Mic MC" || type === "Karaoke MC";
      if (mcEquipBlock) mcEquipBlock.style.display = isMC ? "block" : "none";
    }
    _syncMcEquipBlock(artist.artist_type);
    if (mcEquipCb) {
      mcEquipCb.checked = artist.has_own_equipment === true
                       || artist.has_own_equipment === 1
                       || artist.has_own_equipment === "1"
                       || artist.has_own_equipment === "true";
      mcEquipCb.addEventListener("change", async () => {
        // Save immediately (matches the type/formats save pattern).
        try {
          await window.apiPutSafe
            ? window.apiPutSafe(`/artists/${artistId}`, { has_own_equipment: mcEquipCb.checked })
            : fetch(`/artists/${artistId}`, {
                method: "PUT", credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ has_own_equipment: mcEquipCb.checked })
              });
        } catch (e) { console.error("save has_own_equipment:", e); }
      });
    }

    if (artist.artist_type === "Live Band") {
      formatsBlock.classList.remove("hidden");
  
      if (artist.band_formats) {
        artist.band_formats.split(",").forEach(v => {
          const cb = [...formatChecks].find(c => c.value === v.trim());
          if (cb) { cb.checked = true; _syncPillFromCb(cb); }
        });
      }
      if (artist.styles) {
        artist.styles.split(",").forEach(v => {
          const cb = [...styleChecks].find(c => c.value === v.trim());
          if (cb) { cb.checked = true; _syncPillFromCb(cb); }
        });
      }
      if (isValid()) {
        stopBlink();
        lockPage(false);
      } else {
        lockPage(true);
        startBlink();
      }
    }
  
    artistTypeEl.addEventListener("change", async () => {

      const type = artistTypeEl.value;

      // Jul 1 2026: sync MC-equipment block visibility on type change.
      _syncMcEquipBlock(type);

      if (type === "Live Band") {
        formatsBlock.classList.remove("hidden");
        if (!isValid()) {
          lockPage(true);
          startBlink();
        }
        return;
      }

      formatsBlock.classList.add("hidden");
      allChecks.forEach(c => (c.checked = false));
      stopBlink();
      lockPage(false);

      await saveArtistType(artistId, {
        artist_type: type,
        band_formats: null,
        styles: null
      });
    });
  
    allChecks.forEach(cb => {
      cb.addEventListener("change", async () => {
        if (!isValid()) {
          lockPage(true);
          startBlink();
          return;
        }
  
        stopBlink();
        lockPage(false);
  
        await saveArtistType(artistId, {
          artist_type: "Live Band",
          band_formats: getSelectedFormats().join(","),
          styles: getSelectedStyles().join(",")
        });
      });
    });

    // Helper function for getting selected formats
    function getSelectedFormats() {
      return [...formatChecks].filter(c => c.checked).map(c => c.value);
    }
  }

  // Audit fix (May 2026 part 2): previously this dropdown only listed the
  // logged-in user. Multi-user artist accounts (entity_users) couldn't
  // delegate the booking contact to a bandmate or agent. Now the dropdown
  // loads /api/entity-users/artist/{id} which returns the owner + every
  // entity_user; ``currentUser`` is only used as a fallback when that
  // endpoint is unavailable (legacy auth-less / network failure).
  async function initBookingContact(artistId, artist) {
    const select = qs("booking_contact");

    const userRes = await fetch('/api/me', { credentials: 'include' });
    if (!userRes.ok) return;
    const currentUser = await userRes.json();

    // Pull the full member list. If the call fails for any reason, fall
    // back to just the current user so the dropdown is never empty.
    let users = [currentUser];
    try {
      const r = await fetch(`/api/entity-users/artist/${artistId}`, { credentials: 'include' });
      if (r.ok) {
        const body = await r.json();
        if (body && Array.isArray(body.users) && body.users.length) {
          users = body.users.map(u => ({
            id: u.user_id, first_name: u.first_name, last_name: u.last_name,
            email: u.email, phone: u.phone, role: u.role,
          }));
        }
      }
    } catch (_) { /* fall back to currentUser-only */ }

    // Build dropdown with user details
    let optionsHTML = '<option value="">Select Booking Contact</option>';
    users.forEach(user => {
      const displayName = `${user.first_name || ''} ${user.last_name || ''}`.trim() || 'Unnamed User';
      const email = user.email || '';
      const phone = user.phone || '';
      const value = user.id;
      let label = displayName;
      if (email) label += ` - ${email}`;
      if (phone) label += ` - ${phone}`;
      if (user.role && user.role !== 'owner') label += ` (${user.role})`;
      optionsHTML += `<option value="${value}">${label}</option>`;
    });
    select.innerHTML = optionsHTML;

    // v73: Set current value - handle both user_id and formatted string
    if (artist.booking_contact) {
      // If booking_contact is a number, it's a user_id
      if (!isNaN(artist.booking_contact)) {
        select.value = artist.booking_contact;
      } else {
        // If it's a string (formatted), default to current user
        select.value = currentUser.id;
      }
    } else {
      // No booking contact set, default to current user
      select.value = currentUser.id;
    }
  
    select.addEventListener("change", async () => {
      const value = select.value || null;
      const _prev = select.dataset.lastValue || "";
      select.disabled = true;
      try {
        // Audit fix (May 2026 part 5): surface backend errors so the artist
        // sees the real reason a booking-contact change failed (authz, missing
        // entity user, etc) instead of a silent revert. Restore previous
        // selection on failure so the dropdown stays in sync with the DB.
        if (typeof window.apiPutSafe === 'function') {
          await window.apiPutSafe(`/artists/${artistId}`, { booking_contact: value });
        } else {
          const res = await fetch(`/artists/${artistId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({ booking_contact: value })
          });
          if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
            throw new Error(detail);
          }
        }
        select.dataset.lastValue = value || "";
      } catch (e) {
        console.error("Failed to save booking contact:", e);
        select.value = _prev;
        if (typeof showStyledModal === 'function') {
          showStyledModal('Could Not Update Booking Contact',
            `<p style="color:#ef4444;">${(e && e.message) || 'Please try again.'}</p>`,
            [{text:'OK',style:'ghost'}]);
        } else {
          alert((e && e.message) || 'Could not update booking contact.');
        }
      } finally {
        select.disabled = false;
      }
    });
  }
  

  function initMedia(artistId) {
    // PROFILE PIC
    qs("profilePic").onclick = () => qs("profilePicInput").click();
  
    qs("profilePicInput").onchange = async e => {
      const file = e.target.files[0];
      if (!file) return;
  
      const resized = await resizeImageForProfile(file, 1400, 280); // 2× retina
      const fd = new FormData();
      fd.append("file", resized, file.name.replace(/\.[^.]+$/, '.jpg'));
  
      const res = await fetch(
        `/api/artists/${artistId}/media/profile`,
        {
          method: "POST",
          credentials: "include",
          body: fd
        }
      );
  
      if (res.ok) loadMedia(artistId);
    };
  
    // PICTURES
    qs("addPicBtn").onclick = () => qs("picInput").click();
    qs("picInput").onchange = async e => {
      const file = e.target.files[0];
      if (!file) return;
  
      const resized = await resizeImageForProfile(file, 1200, 900);
      const fd = new FormData();
      fd.append("file", resized, file.name.replace(/\.[^.]+$/, '.jpg'));
  
      await fetch(`/api/artists/${artistId}/media/picture`, {
        method: "POST",
        credentials: "include",
        body: fd
      });
  
      loadMedia(artistId);
    };
  
    // AUDIO (MP3 file upload — capped at 3 per artist; the cap is enforced
    // server-side too in routes/media.py)
    qs("addAudioBtn").onclick = () => {
      const count = document.querySelectorAll('#audio .audio-entry[data-kind="audio"]').length;
      if (count >= 3) {
        const msg = "You've reached the 3 MP3 file limit. Delete an existing "
                  + "MP3 or add a link to external audio instead.";
        if (window.showErrorModal) window.showErrorModal("MP3 limit reached", msg);
        else alert(msg);
        return;
      }
      qs("audioInput").click();
    };
    qs("audioInput").onchange = async e => {
      const file = e.target.files[0];
      if (!file) return;

      // MP3-only — mirrors backend ALLOWED_EXTENSIONS['audio'] in routes/media.py.
      // Check the extension first (cheap) so the user gets a clear message
      // before we try uploading a file we'd reject anyway. Some macOS / iOS
      // pickers ignore the `accept` attribute, so we can't rely on it alone.
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      if (ext !== 'mp3') {
        const msg = `That file is a .${ext} — only MP3 files can be uploaded. ` +
                    `Either convert to MP3 (most audio editors export MP3 in one click) ` +
                    `or paste a link to the track on SoundCloud / Bandcamp / your own site ` +
                    `using the "+ Add Audio Link" field below.`;
        if (window.showErrorModal) window.showErrorModal("MP3 only", msg);
        else alert(msg);
        e.target.value = "";
        return;
      }

      // 5 MB cap — mirrors backend MAX_FILE_SIZES['audio']. Client-side so
      // the user gets immediate feedback before a multi-MB upload fails with
      // a 400. Backend still enforces this in case the check is bypassed.
      const MAX_AUDIO_BYTES = 5 * 1024 * 1024;
      if (file.size > MAX_AUDIO_BYTES) {
        const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
        const msg = `That file is ${sizeMb} MB — the limit is 5 MB. ` +
                    `Try re-encoding at a lower bitrate (128 kbps is plenty for a demo clip), ` +
                    `trimming the track, or linking to it on SoundCloud/Bandcamp instead.`;
        if (window.showErrorModal) window.showErrorModal("MP3 too large", msg);
        else alert(msg);
        e.target.value = "";  // reset so picking the same file fires onchange again
        return;
      }

      // Auto-populate Title from the uploaded filename (drop extension,
      // turn _ / - into spaces, trim). User can edit it after upload.
      const titleFromFilename = file.name
        .replace(/\.[^.]+$/, "")
        .replace(/[_-]+/g, " ")
        .trim();

      const fd = new FormData();
      fd.append("file", file);
      if (titleFromFilename) fd.append("title", titleFromFilename);

      const res = await fetch(`/api/artists/${artistId}/media/audio`, {
        method: "POST",
        credentials: "include",
        body: fd
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const msg = detail.detail || "Upload failed.";
        if (window.showErrorModal) window.showErrorModal("Upload failed", msg);
        else alert(msg);
      }
      e.target.value = "";  // reset so re-uploading the same file fires onchange
      loadMedia(artistId);
    };

    // AUDIO LINK (URL — SoundCloud / Bandcamp / etc. — unlimited)
    qs("addAudioLinkBtn").onclick = async () => {
      const input = qs("audioLinkUrl");
      const url = input.value.trim();
      if (!url) return;
      const fd = new FormData();
      fd.append("video_url", url);
      const res = await fetch(
        `/api/artists/${artistId}/media/audio_link`,
        { method: "POST", credentials: "include", body: fd }
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const msg = detail.detail || "Failed to add audio link.";
        if (window.showErrorModal) window.showErrorModal("Couldn't add link", msg);
        else alert(msg);
        return;
      }
      input.value = "";
      loadMedia(artistId);
    };

    // VIDEO LINK
    qs("addVideoBtn").onclick = async () => {
      const url = qs("videoUrl").value.trim();
      if (!url) return;

      const fd = new FormData();
      fd.append("video_url", url);

      const res = await fetch(
        `/api/artists/${artistId}/media/video`,
        {
          method: "POST",
          credentials: "include",
          body: fd
        }
      );

      if (!res.ok) {
        // Audit fix (May 2026 part 6): surface backend {detail} so the user
        // sees the rejection reason (malformed URL, disallowed host) instead
        // of nothing happening when they click.
        let _detail = `HTTP ${res.status}`;
        try { const _j = await res.json(); if (_j && _j.detail) _detail = _j.detail; } catch(_) {}
        console.error("Failed to add video:", _detail);
        if (typeof window.showErrorModal === 'function') {
          window.showErrorModal('Could not add video', _detail);
        }
        return;
      }

      qs("videoUrl").value = "";
      loadMedia(artistId);
    };

  }

  // Pick the right embed for an audio link based on its URL:
  //   • SoundCloud track/playlist URLs → the public widget iframe
  //   • Direct audio file (.mp3 / .wav / .ogg / .m4a / .aac / .flac) → <audio>
  //   • Anything else → a clickable 🔗 link (link still opens in a new tab)
  function renderAudioLinkPlayer(rawUrl) {
    const url = String(rawUrl || "");
    if (!url) return "";
    const safeUrl = url.replace(/"/g, "&quot;");

    if (/soundcloud\.com\//i.test(url)) {
      const src = "https://w.soundcloud.com/player/?url=" + encodeURIComponent(url)
                + "&color=%238b5cf6&inverse=true&auto_play=false&hide_related=true"
                + "&show_comments=false&show_user=true&show_reposts=false&show_teaser=false";
      return `<iframe class="audio-link-iframe" width="100%" height="100" scrolling="no" frameborder="no" allow="autoplay" src="${src.replace(/"/g,'&quot;')}" style="flex:1;min-width:0;border-radius:6px;"></iframe>`;
    }
    if (/\.(mp3|wav|ogg|m4a|aac|flac)(\?|#|$)/i.test(url)) {
      return `<audio controls src="${safeUrl}" style="flex:1;min-width:0;"></audio>`;
    }
    const displayUrl = url.replace(/^https?:\/\//i, "").replace(/\/+$/, "");
    const safeDisplay = displayUrl.replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `<a href="${safeUrl}" target="_blank" rel="noopener"
               style="flex:1;min-width:0;color:var(--cyan);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.85rem;"
               title="${safeUrl}">🔗 ${safeDisplay}</a>`;
  }

  // Sync return: best-effort immediate thumbnail (YouTube pattern is
  // deterministic from the URL so no API call needed). For Vimeo /
  // TikTok / Instagram / Facebook / bare video URLs, return the branded
  // placeholder first, then _asyncFetchThumb below fires an oEmbed
  // lookup (Vimeo + TikTok have public CORS-friendly oEmbed endpoints)
  // and swaps the placeholder for the real thumbnail when it arrives.
  // Cached in _thumbCache so we don't re-hit the API on every render.
  const _thumbCache = new Map();
  function getVideoThumbnail(url) {
    if (!url) return "/app/static/img/video-placeholder.svg";
    // YouTube — direct thumbnail URL
    const ytMatch = url.match(/(?:youtube\.com.*v=|youtu\.be\/|youtube\.com\/shorts\/)([^&?/]+)/);
    if (ytMatch) return `https://img.youtube.com/vi/${ytMatch[1]}/hqdefault.jpg`;
    // Cached from a prior oEmbed lookup?
    if (_thumbCache.has(url)) return _thumbCache.get(url);
    return "/app/static/img/video-placeholder.svg";
  }

  // Fire oEmbed lookups AFTER media loads and swap the placeholder for
  // the real thumbnail once resolved. Runs once per new URL; results
  // cached in _thumbCache. Called from loadMedia after DOM is written.
  async function _refreshMissingThumbnails() {
    const imgs = document.querySelectorAll('#videos .media-card img[src*="video-placeholder"]');
    const seen = new Set();
    for (const img of imgs) {
      const card = img.closest('.media-card');
      if (!card) continue;
      const editBtn = card.querySelector('.edit-url-btn');
      const url = editBtn ? editBtn.dataset.currentUrl : '';
      if (!url || seen.has(url)) continue;
      seen.add(url);
      _asyncFetchThumb(url).then(thumbUrl => {
        if (!thumbUrl) return;
        _thumbCache.set(url, thumbUrl);
        // Update every img currently pointing at this url's card.
        document.querySelectorAll('#videos .media-card').forEach(c => {
          const b = c.querySelector('.edit-url-btn');
          if (b && b.dataset.currentUrl === url) {
            const i = c.querySelector('img');
            if (i) i.src = thumbUrl;
          }
        });
      }).catch(() => { /* silent — placeholder stays */ });
    }
  }

  async function _asyncFetchThumb(url) {
    try {
      // Vimeo — public oEmbed with CORS. Any /video/{id} or /{id} URL.
      if (/vimeo\.com\/(?:video\/)?\d+/.test(url)) {
        const r = await fetch('https://vimeo.com/api/oembed.json?url=' + encodeURIComponent(url));
        if (r.ok) { const j = await r.json(); return j.thumbnail_url || null; }
      }
      // TikTok — public oEmbed with CORS.
      if (/tiktok\.com\/@[^/]+\/video\/\d+/.test(url)) {
        const r = await fetch('https://www.tiktok.com/oembed?url=' + encodeURIComponent(url));
        if (r.ok) { const j = await r.json(); return j.thumbnail_url || null; }
      }
      // Instagram + Facebook locked their oEmbed behind Graph API tokens
      // (Nov 2020). No public thumbnail — placeholder stays.
    } catch (_) { /* silent */ }
    return null;
  }
  

  
  async function loadMedia(artistId) {

    const res = await fetch(`/api/artists/${artistId}/media`, {
      credentials: "include"
    });

    if (!res.ok) return;
  
    const items = await res.json();
  
    const picturesEl = qs("pictures");
    const audioEl = qs("audio");
    const videosEl = qs("videos");
    const profilePicEl = qs("profilePic");
  
    // -----------------------------
    // HARD RESET (your original intent)
    // -----------------------------
    picturesEl.innerHTML = "";
    audioEl.innerHTML = "";
    videosEl.innerHTML = "";
  
    // 🔥 IMPORTANT: reset profile pic FIRST
    profilePicEl.src = "/app/static/img/profile-placeholder.svg";
  
    let hasPictures = false;
    let hasAudio = false;
    let hasVideos = false;
  
    items.forEach(m => {
      // -----------------------------
      // PROFILE PIC (CRITICAL FIX)
      // -----------------------------
      if (m.media_type === "profile" && m.file_path) {
        profilePicEl.src = m.file_path;
      }
  
      // -----------------------------
      // PICTURES
      // -----------------------------
      if (m.media_type === "picture") {
        hasPictures = true;
        const caption = m.caption || "";
        picturesEl.insertAdjacentHTML("beforeend", `
          <div class="media-card" data-id="${m.id}" data-kind="picture">
            <img src="${m.file_path}">
            <div class="media-overlay center-overlay">
              <span class="drag-handle" draggable="true">☰</span>
              <input
                value="${m.title || ""}"
                placeholder="Title"
                data-id="${m.id}"
                class="media-title"
              />
              <textarea
                class="picture-caption"
                placeholder="Add a caption (e.g. backstage, Aug 14)…"
                maxlength="500"
                rows="2"
                data-id="${m.id}"
              >${escapeHtml(caption)}</textarea>
              <button class="delete-btn" data-id="${m.id}">Delete</button>
            </div>
          </div>
        `);
      }
  
      // -----------------------------
      // AUDIO (MP3 file)
      // -----------------------------
      //
      // Layout per entry (full-width card):
      //   ┌─ .audio-entry ────────────────────────────────────────────┐
      //   │ <textarea> caption / notes (full width)                    │
      //   │ <row> ☰  title  <audio>  Delete                            │
      //   └────────────────────────────────────────────────────────────┘
      //
      // Caption is independent of title — title is 1-line searchable label,
      // caption is multi-line context. Stored in artist_media.caption; saves
      // on blur via the same /api/media/{id} PUT handler the title uses.
      if (m.media_type === "audio") {
        hasAudio = true;
        const caption = m.caption || "";
        audioEl.insertAdjacentHTML("beforeend", `
          <div class="audio-entry" data-id="${m.id}" data-kind="audio">
            <textarea
              class="audio-caption"
              placeholder="Add a caption or notes about this track…"
              maxlength="500"
              data-id="${m.id}"
              rows="1"
            >${escapeHtml(caption)}</textarea>
            <div class="audio-row">
              <span class="drag-handle" draggable="true">☰</span>
              <input
                class="media-title"
                value="${m.title || ""}"
                placeholder="Title"
                maxlength="65"
                data-id="${m.id}"
              />
              <audio controls src="${m.file_path}"></audio>
              <button class="delete-btn" data-id="${m.id}">Delete</button>
            </div>
          </div>
        `);
      }

      // -----------------------------
      // AUDIO LINK (external URL — SoundCloud / Bandcamp / direct file)
      // -----------------------------
      if (m.media_type === "audio_link") {
        hasAudio = true;
        const url = m.video_url || "";
        const playerHtml = renderAudioLinkPlayer(url);
        const caption = m.caption || "";
        // ✏️ Edit URL button — subtle cyan icon that opens a modal to
        // fix the source URL without deleting + re-adding the entry.
        // Added 2026-07-25.
        const editUrlBtn = `<button class="edit-url-btn" data-id="${m.id}" data-current-url="${escapeHtml(url)}" title="Edit the audio URL" style="background:transparent;border:0;color:#94a3b8;font-size:0.95rem;cursor:pointer;padding:2px 6px;border-radius:4px;line-height:1;" onmouseover="this.style.color='#7dd3fc';this.style.background='rgba(6,182,212,0.1)';" onmouseout="this.style.color='#94a3b8';this.style.background='transparent';">✏️</button>`;
        audioEl.insertAdjacentHTML("beforeend", `
          <div class="audio-entry" data-id="${m.id}" data-kind="audio_link">
            <textarea
              class="audio-caption"
              placeholder="Add a caption or notes about this track…"
              maxlength="500"
              data-id="${m.id}"
              rows="1"
            >${escapeHtml(caption)}</textarea>
            <div class="audio-row">
              <span class="drag-handle" draggable="true">☰</span>
              <input
                class="media-title"
                value="${m.title || ""}"
                placeholder="Title"
                maxlength="65"
                data-id="${m.id}"
              />
              ${playerHtml}
              ${editUrlBtn}
              <button class="delete-btn" data-id="${m.id}">Delete</button>
            </div>
          </div>
        `);
      }

      // -----------------------------
      // VIDEOS
      // -----------------------------
      if (m.media_type === "video") {
        hasVideos = true;
        const thumb = getVideoThumbnail(m.video_url);
        const caption = m.caption || "";
        const videoUrl = m.video_url || "";
        // ✏️ Edit URL button — same pattern as audio_link. Sits next to
        // Delete so the two "manage" actions are grouped. Added 2026-07-25.
        const editUrlBtn = `<button class="edit-url-btn" data-id="${m.id}" data-current-url="${escapeHtml(videoUrl)}" title="Edit the video URL" style="background:transparent;border:1px solid rgba(148,163,184,0.3);color:#94a3b8;font-size:0.75rem;cursor:pointer;padding:3px 10px;border-radius:4px;line-height:1;margin-right:6px;" onmouseover="this.style.color='#7dd3fc';this.style.borderColor='rgba(6,182,212,0.5)';" onmouseout="this.style.color='#94a3b8';this.style.borderColor='rgba(148,163,184,0.3)';">✏️ Edit URL</button>`;
        // Caption sits inside the overlay below the title input — matches the
        // audio-entry pattern (title + caption together as the "metadata" of
        // the media). Public profile renders it below the title-label and
        // skips the row entirely when empty, so no blank line for plain videos.
        videosEl.insertAdjacentHTML("beforeend", `
          <div class="media-card" data-id="${m.id}" data-kind="video">
            <img src="${thumb}" alt="Video thumbnail">
            <div class="media-overlay center-overlay">
              <span class="drag-handle" draggable="true">☰</span>
              <input
                class="media-title"
                value="${m.title || ""}"
                placeholder="Title"
                data-id="${m.id}"
              />
              <textarea
                class="video-caption"
                placeholder="Add a caption (e.g. Live at the Roxy · Aug 14)…"
                maxlength="500"
                rows="2"
                data-id="${m.id}"
              >${escapeHtml(caption)}</textarea>
              <div style="display:flex;gap:6px;align-items:center;justify-content:center;flex-wrap:wrap;">
                ${editUrlBtn}
                <button class="delete-btn" data-id="${m.id}">Delete</button>
              </div>
            </div>
          </div>
        `);
      }
    });

    // Surface MP3 count on the upload button (e.g. "+ Add MP3 File (2/3)")
    const audioCount = items.filter(m => m.media_type === "audio").length;
    const countEl = document.getElementById("addAudioBtnCount");
    if (countEl) countEl.textContent = audioCount ? `(${audioCount}/3)` : "";

    // Kick off async oEmbed lookups for videos whose thumbnails aren't
    // deterministic (Vimeo, TikTok). Swaps the placeholder for the real
    // thumbnail in place when the API responds. YouTube already resolved
    // synchronously above; Instagram/Facebook keep the placeholder.
    _refreshMissingThumbnails();
  }
  
  

  // Helper: does this element get the title/caption save treatment?
  // Captions for audio / video / picture all share Enter-commits-and-saves
  // behavior + the same `{ caption: value }` PUT payload.
  function _isMediaTitle(el)   { return el && el.classList && el.classList.contains("media-title"); }
  function _isMediaCaption(el) {
    return el && el.classList &&
           (el.classList.contains("audio-caption") ||
            el.classList.contains("video-caption") ||
            el.classList.contains("picture-caption"));
  }

  document.addEventListener("keydown", e => {
    if (!(e.target instanceof HTMLElement)) return;
    // Title inputs + caption textareas: Enter commits (blurs → blur handler
    // fires the PUT). Captions are intentionally short — "Live at the Roxy
    // 8/14/26" style — not paragraphs, so we treat the textarea like a
    // single-line field. preventDefault stops the newline being inserted.
    if ((_isMediaTitle(e.target) || _isMediaCaption(e.target)) && e.key === "Enter") {
      e.preventDefault();
      e.target.blur();
    }
  });



  document.addEventListener("blur", async e => {
    if (!(e.target instanceof HTMLElement)) return;
    const isTitle = _isMediaTitle(e.target);
    const isCaption = _isMediaCaption(e.target);
    if (!isTitle && !isCaption) return;

    const id = e.target.dataset.id;
    const value = e.target.value.trim();
    const payload = isTitle ? { title: value } : { caption: value };

    await fetch(`/api/media/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload)
    });
  }, true);
  
  
  // Edit-URL click handler for video + audio_link cards. Opens a small
  // modal with the current URL prefilled; Save (or Enter) PUTs the
  // new value and refreshes the media list so the card updates
  // (new thumbnail for videos, new player for audio_links).
  // Added 2026-07-25.
  document.addEventListener("click", async e => {
    if (!(e.target instanceof HTMLElement)) return;
    // Match the button itself OR its child (emoji span, etc.)
    const btn = e.target.closest ? e.target.closest(".edit-url-btn") : null;
    if (!btn) return;
    const id = btn.dataset.id;
    const currentUrl = btn.dataset.currentUrl || "";
    const card = btn.closest(".media-card") || btn.closest(".audio-entry");
    const kind = card && card.dataset.kind;
    const label = kind === "video" ? "Video URL" : "Audio URL";
    const artistId = new URLSearchParams(window.location.search).get("artist_id");

    _openUrlEditModal(label, currentUrl, async (newUrl) => {
      const trimmed = (newUrl || "").trim();
      if (!trimmed || trimmed === currentUrl) return;
      try {
        const res = await fetch(`/api/media/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ video_url: trimmed })
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          if (window.showErrorModal) window.showErrorModal("Save failed", d.detail || `HTTP ${res.status}`);
          return;
        }
        if (artistId && typeof loadMedia === "function") loadMedia(artistId);
      } catch (_) {
        if (window.showErrorModal) window.showErrorModal("Network error", "Could not reach the server.");
      }
    });
  });

  // Minimal edit-URL modal — uniform look with the rest of the site's
  // modals (dark card, cyan gradient Save button). Enter submits, Escape
  // cancels. Overlay click-outside dismisses via the universal
  // safety-net in gf-modals.js.
  function _openUrlEditModal(labelText, initialValue, onSave) {
    const existing = document.getElementById("_urlEditOverlay");
    if (existing) existing.remove();
    const overlay = document.createElement("div");
    overlay.id = "_urlEditOverlay";
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:10010;display:flex;align-items:center;justify-content:center;padding:16px;";
    overlay.innerHTML = `
      <div style="background:var(--card,#151b28);border:1px solid var(--border);border-radius:12px;padding:24px 28px;max-width:520px;width:100%;box-shadow:0 24px 64px rgba(0,0,0,0.6);">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;gap:12px;">
          <h3 style="margin:0;font-size:1.05rem;font-weight:700;color:var(--text);">Edit ${escapeHtml(labelText)}</h3>
          <button id="_urlEditCloseX" style="background:transparent;border:1px solid rgba(239,68,68,0.35);color:#ef4444;font-size:1.5rem;line-height:1;cursor:pointer;padding:0;width:32px;height:32px;border-radius:6px;">&times;</button>
        </div>
        <label style="display:block;font-size:0.75rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:6px;">${escapeHtml(labelText)}</label>
        <input id="_urlEditInput" type="text" value="${escapeHtml(initialValue || "")}" placeholder="https://…"
               style="width:100%;padding:10px 12px;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:0.9rem;box-sizing:border-box;">
        <p style="margin:8px 0 0;font-size:0.75rem;color:var(--text-muted);font-style:italic;">Press Enter to save.</p>
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px;">
          <button id="_urlEditCancel" style="padding:9px 18px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-gray);font-size:0.82rem;font-weight:600;cursor:pointer;">Cancel</button>
          <button id="_urlEditSave" style="padding:9px 20px;border-radius:6px;border:none;background:linear-gradient(135deg,var(--purple,#8b5cf6),var(--cyan,#06b6d4));color:#fff;font-size:0.82rem;font-weight:600;cursor:pointer;">Save</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const input = document.getElementById("_urlEditInput");
    const close = () => { const el = document.getElementById("_urlEditOverlay"); if (el) el.remove(); };
    const submit = () => { const v = input ? input.value : ""; close(); onSave(v); };
    document.getElementById("_urlEditCloseX").onclick = close;
    document.getElementById("_urlEditCancel").onclick = close;
    document.getElementById("_urlEditSave").onclick   = submit;
    overlay.onclick = e => { if (e.target === overlay) close(); };
    input.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
    setTimeout(() => { input.focus(); input.select(); }, 30);
  }

  document.addEventListener("click", async e => {
    if (!(e.target instanceof HTMLElement)) return;
    if (!e.target.classList.contains("delete-btn")) return;

    const btn = e.target;
    const id = btn.dataset.id;
    // For audio, walk up to .audio-entry so the caption (which sits ABOVE the
    // .audio-row) gets removed along with the row. For pictures/videos the
    // entire card IS the unit.
    const card = btn.closest(".media-card") || btn.closest(".audio-entry");

    // Pick a label by media kind so the modal is specific.
    const kind = card && card.dataset.kind;
    let label = "Delete this media?";
    if (kind === "audio")       label = "Delete this MP3 file?";
    else if (kind === "audio_link") label = "Delete this audio link?";

    const doDelete = async () => {
      const res = await fetch(`/api/media/${id}`, {
        method: "DELETE",
        credentials: "include"
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const msg = detail.detail || "Delete failed.";
        if (window.showErrorModal) window.showErrorModal("Delete failed", msg);
        else alert(msg);
        return;
      }
      if (card) card.remove();
      // Refresh audio button's count badge if we just removed an MP3
      if (kind === "audio") {
        const remaining = document.querySelectorAll('#audio .audio-entry[data-kind="audio"]').length;
        const countEl = document.getElementById("addAudioBtnCount");
        if (countEl) countEl.textContent = remaining ? `(${remaining}/3)` : "";
      }
    };

    if (window.showConfirm) {
      window.showConfirm(
        label,
        "This action can't be undone.",
        // Wrap in sync fire-and-forget so gf-modals closes the confirm
        // on first click. Passing the async `doDelete` directly would
        // return a Promise → modal stays open even after the DELETE
        // succeeds and the card is removed from the DOM behind it.
        // Same pattern as the Support Tickets / Contact Messages
        // delete flows. Fixed 2026-07-25.
        function () { doDelete(); },
        null,
        { tone: 'warning', confirmLabel: 'Delete', cancelLabel: 'Cancel', confirmStyle: 'danger' }
      );
    } else if (confirm(label)) {
      doDelete();
    }
  });
  

  document.addEventListener("play", e => {
    if (!(e.target instanceof HTMLAudioElement)) return;
  
    document.querySelectorAll("audio").forEach(audio => {
      if (audio !== e.target) {
        audio.pause();
      }
    });
  }, true);
  

  
  initArtistType(artistId, artist);
  initBookingContact(artistId, artist);
  initMedia(artistId);
  loadMedia(artistId);
  initMediaDragAndDrop("pictures");
  initMediaDragAndDrop("audio");
  initMediaDragAndDrop("videos");

}

document.addEventListener("DOMContentLoaded", () => {
  loadArtist();
});
