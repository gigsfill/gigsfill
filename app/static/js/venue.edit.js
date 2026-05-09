// -----------------------------
// HELPERS
// -----------------------------

/**
 * Resize an image File to fit within maxW×maxH while preserving aspect ratio,
 * then return it as a Blob (image/jpeg, quality 0.92).
 * Used before uploading profile/logo images so they fit the display area.
 */
function resizeImageForProfile(file, maxW, maxH) {
  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      let { width: w, height: h } = img;
      // Scale down to fit maxW × maxH (never scale up)
      const scale = Math.min(1, maxW / w, maxH / h);
      w = Math.round(w * scale);
      h = Math.round(h * scale);
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      canvas.getContext('2d').drawImage(img, 0, 0, w, h);
      canvas.toBlob(blob => resolve(blob), 'image/jpeg', 0.92);
    };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(file); }; // fallback: use original
    img.src = url;
  });
}

let autosaveHideTimer = null;
let lastActiveField = null;

let headerSaveTimers = new Map();

function showHeaderSaving() {
  // disabled — causes layout artifacts
}

function qs(id) {
  return document.getElementById(id);
}

function getVenueId() {
  return new URLSearchParams(window.location.search).get("venue_id");
}

document.addEventListener("DOMContentLoaded", () => {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get("venue_id");

  if (!venueId) return;

  const link = document.getElementById("createGigsLink");
  if (!link) return;

  link.href = `/app/venue-create-gigs.html?venue_id=${venueId}`;
});

// Function to handle autosave for form fields
function bindAutosave(el, field, venueId, transform = v => v, headerEl = null, headerLabel = "") {
  if (!el) return;

  let debounceTimer = null;

  // Function to handle the save logic
  const save = async () => {
    try {
      // City validation - block save if city is invalid, auto-fill state if valid
      if (field === 'city') {
        const val = el.value.trim();
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
              if (typeof showCityError === 'function') showCityError(el, false);
              if (vd.state && stateEl && stateEl.value !== vd.state) {
                stateEl.value = vd.state;
                stateEl.dispatchEvent(new Event('change', { bubbles: true }));
              }
            } else {
              if (typeof showCityError === 'function') showCityError(el, true);
              return; // Don't save invalid city
            }
          } catch(e) {
            return;
          }
        }
      }
      
      // Strip commas from numeric fields before saving
      let valueToSave = el.value;
      if (field === 'default_pay_dollars' || field === 'venue_size') {
        valueToSave = valueToSave.replace(/,/g, '');
      }
      
      // Normalize URL fields - ensure https://www. prefix
      const urlFields = ['website_url','facebook_url','instagram_url','twitter_url','yelp_url','google_maps_url'];
      if (urlFields.includes(field) && valueToSave.trim()) {
        let url = valueToSave.trim();
        if (!/^https?:\/\//i.test(url)) {
          if (!/^www\./i.test(url)) {
            url = 'www.' + url;
          }
          url = 'https://' + url;
        } else if (/^https?:\/\/(?!www\.)/i.test(url) && !url.includes('://www.')) {
          url = url.replace(/^(https?:\/\/)/, '$1www.');
        }
        valueToSave = url;
        el.value = url;
      }
      
      const response = await fetch(`/api/venues/${venueId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ [field]: transform(valueToSave) })
      });
      
      if (!response.ok) {
        throw new Error(`Failed to save: ${response.statusText}`);
      }
    } catch (error) {
      console.error("Error saving field:", field, error);
    }
  };

  // Handle Enter keypress for input fields (blur on Enter)
  el.addEventListener("keydown", e => {
    if (
      e.key === "Enter" &&
      el.tagName !== "TEXTAREA" &&
      el.id !== "videoUrl"
    ) {
    
      e.preventDefault();
      el.blur();
    }
  });
  

  // Handle input event for autosave (debounced for better performance)
  el.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(save, 500);
  });

  // Trigger save on blur or when content is changed
  el.addEventListener("blur", () => {
    clearTimeout(debounceTimer);
    save();
  });
  
  el.addEventListener("change", () => {
    clearTimeout(debounceTimer);
    save();
  });
  
}

// -----------------------------
// TOGGLES
// -----------------------------
function applyToggles() {
  const venueId = getVenueId();

  const hasStage = qs("has_stage")?.value === "true";
  const hasSound = qs("has_sound_equipment")?.value === "true";
  const hasSoundEngineer = qs("has_sound_engineer")?.value === "true";
  const hasLighting = qs("has_lighting")?.value === "true";

  const arrivalTimeType = qs("arrival_time_type")?.value;
  const isArrivalLimited = arrivalTimeType === "no_earlier_than";

  // STAGE - show/hide stage details section
  qs("stage-details")?.classList.toggle("hidden", !hasStage);

  // SOUND EQUIPMENT - show/hide textarea
  qs("sound_equipment_description")?.classList.toggle("hidden", !hasSound);

  // SOUND ENGINEER GROUP - show/hide entire section when sound equipment is Yes
  qs("sound-engineer-group")?.classList.toggle("hidden", !hasSound);

  // SOUND ENGINEER DETAILS - show/hide textarea when sound engineer is Yes
  qs("sound_engineer_details")?.classList.toggle("hidden", !hasSoundEngineer);

  // LIGHTING - show/hide textarea
  qs("lighting_description")?.classList.toggle("hidden", !hasLighting);

  // ARRIVAL - show/hide time dropdowns AND wrapper div
  document.getElementById("arrivalInline")?.classList.toggle("hidden", !isArrivalLimited);
  qs("arrival_no_earlier_than_hour")?.classList.toggle("hidden", !isArrivalLimited);
  qs("arrival_no_earlier_than_period")?.classList.toggle("hidden", !isArrivalLimited);
  qs("load_in_out_details")?.classList.toggle("hidden", !isArrivalLimited);
}

// -----------------------------
// CLEAR FIELDS WHEN NO IS SELECTED
// -----------------------------
async function clearStageFields(venueId) {
  const setupDesc = qs("setup_location_description");
  const stageWidth = qs("stage_width_ft");
  const stageDepth = qs("stage_depth_ft");
  if (setupDesc) setupDesc.value = "";
  if (stageWidth) stageWidth.value = "";
  if (stageDepth) stageDepth.value = "";
  
  await fetch(`/api/venues/${venueId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      has_stage: false,
      setup_location_description: null,
      stage_width_ft: null,
      stage_depth_ft: null
    })
  });
}

async function clearSoundEquipmentFields(venueId) {
  const soundDesc = qs("sound_equipment_description");
  const seToggle = qs("has_sound_engineer");
  const seDesc = qs("sound_engineer_details");
  
  if (soundDesc) soundDesc.value = "";
  if (seToggle) seToggle.value = "false";
  if (seDesc) seDesc.value = "";
  
  await fetch(`/api/venues/${venueId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      has_sound_equipment: false,
      sound_equipment_description: null,
      has_sound_engineer: false,
      sound_engineer_details: null
    })
  });
}

async function clearSoundEngineerFields(venueId) {
  const seDesc = qs("sound_engineer_details");
  if (seDesc) seDesc.value = "";
  
  await fetch(`/api/venues/${venueId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      has_sound_engineer: false,
      sound_engineer_details: null
    })
  });
}

async function clearLightingFields(venueId) {
  const lightDesc = qs("lighting_description");
  if (lightDesc) lightDesc.value = "";
  
  await fetch(`/api/venues/${venueId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      has_lighting: false,
      lighting_description: null
    })
  });
}

async function clearArrivalFields(venueId) {
  const hourEl = qs("arrival_no_earlier_than_hour");
  const periodEl = qs("arrival_no_earlier_than_period");
  const detailsEl = qs("load_in_out_details");
  
  if (hourEl) hourEl.value = "";
  if (periodEl) periodEl.value = "PM";
  if (detailsEl) detailsEl.value = "";
  
  await fetch(`/api/venues/${venueId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      arrival_time_type: "flexible",
      arrival_no_earlier_than_hour: null,
      arrival_no_earlier_than_period: null,
      load_in_out_details: null
    })
  });
}

// Set arrival time defaults when "No Earlier Than" is selected
function setArrivalDefaults() {
  const hourEl = qs("arrival_no_earlier_than_hour");
  const periodEl = qs("arrival_no_earlier_than_period");
  
  if (hourEl && !hourEl.value) hourEl.value = "12";
  if (periodEl && !periodEl.value) periodEl.value = "PM";
}

// -----------------------------
// LOAD VENUE
// -----------------------------
async function loadVenue() {
  const venueId = getVenueId();
  if (!venueId) return;

  const res = await fetch(`/api/venues/${venueId}`, { credentials: "include" });
  if (!res.ok) return;

  const venueData = await res.json();

  const fields = [
    { id: "venue_name", field: "venue_name" },
    { id: "description", field: "description" },
    { id: "address_line_1", field: "address_line_1" },
    { id: "address_line_2", field: "address_line_2" },
    { id: "city", field: "city" },
    { id: "state", field: "state" },
    { id: "postal_code", field: "postal_code" },
    { id: "venue_size", field: "venue_size" },
    { id: "stage_width_ft", field: "stage_width_ft" },
    { id: "stage_depth_ft", field: "stage_depth_ft" },
    { id: "setup_location_description", field: "setup_location_description" },
    { id: "sound_equipment_description", field: "sound_equipment_description" },
    { id: "sound_engineer_details", field: "sound_engineer_details" },
    { id: "lighting_description", field: "lighting_description" },
    { id: "arrival_no_earlier_than_hour", field: "arrival_no_earlier_than_hour" },
    { id: "arrival_no_earlier_than_period", field: "arrival_no_earlier_than_period" },
    { id: "load_in_out_details", field: "load_in_out_details" },
    { id: "default_pay_dollars", field: "default_pay_dollars" },
    { id: "default_pay_cents", field: "default_pay_cents" },
    { id: "bar_tab_details", field: "bar_tab_details" },
    { id: "food_tab_details", field: "food_tab_details" },
    { id: "website_url", field: "website_url" },
    { id: "facebook_url", field: "facebook_url" },
    { id: "instagram_url", field: "instagram_url" },
    { id: "twitter_url", field: "twitter_url" },
    { id: "yelp_url", field: "yelp_url" },
    { id: "google_maps_url", field: "google_maps_url" },
  ];

  fields.forEach(({ id, field }) => {
    const el = document.getElementById(id);
    if (!el) return;
  
    let value = venueData[field];
  
    // FORCE cents to 2 digits on load
    if (field === "default_pay_cents") {
      value = value == null ? "00" : String(value).padStart(2, "0");
    }
    
    // Format dollars with commas on load
    if (field === "default_pay_dollars" && value != null) {
      value = parseInt(value, 10).toLocaleString('en-US');
    }
    
    // Format venue_size with commas on load
    if (field === "venue_size" && value != null && value !== "") {
      value = parseInt(value, 10).toLocaleString('en-US');
    }
  
    el.value = value ?? "";
  });
  

  // Set the toggle values
  document.getElementById("has_stage").value = venueData.has_stage ? "true" : "false";
  document.getElementById("has_sound_equipment").value = venueData.has_sound_equipment ? "true" : "false";
  
  const hasSoundEngineerEl = document.getElementById("has_sound_engineer");
  if (hasSoundEngineerEl) {
    hasSoundEngineerEl.value = venueData.has_sound_engineer ? "true" : "false";
  }

  document.getElementById("has_lighting").value = venueData.has_lighting ? "true" : "false";
  document.getElementById("arrival_time_type").value = venueData.arrival_time_type || "flexible";

  // Apply toggle visibility
  applyToggles();

  // FLYER SETTINGS
  const autoFlyersEl = document.getElementById('auto_flyers');
  const flyerTplRow = document.getElementById('flyerTemplateRow');
  const flyerTplSel = document.getElementById('default_flyer_template_id');
  if (autoFlyersEl) {
    autoFlyersEl.checked = !!venueData.auto_flyers;
    if (flyerTplRow) flyerTplRow.style.display = venueData.auto_flyers ? '' : 'none';
  }
  // Load templates into dropdown
  if (flyerTplSel) {
    try {
      const tr = await fetch(`/api/venues/${venueId}/flyer-templates`, { credentials: 'include' });
      if (tr.ok) {
        const tpls = await tr.json();
        tpls.forEach(t => {
          const opt = document.createElement('option');
          opt.value = t.id; opt.textContent = t.name;
          flyerTplSel.appendChild(opt);
        });
      }
    } catch(e) {}
    if (venueData.default_flyer_template_id) flyerTplSel.value = String(venueData.default_flyer_template_id);
  }

  // ARTIST FREQUENCY (LOAD)
  const freqMode = document.getElementById("artistFrequencyMode");
  const freqWrap = document.getElementById("artistFrequencyLimit");
  const freqDays = document.getElementById("artistFrequencyDays");
  const freqDaysLabel = document.getElementById("artistFrequencyDaysLabel");

  if (freqMode && freqDays) {
    const days = venueData.artist_frequency_days;
    const mode = venueData.artist_frequency_mode;

    // Check mode field first, then fall back to days-based logic
    if (mode === "limit") {
      freqMode.value = "limit";
      freqDays.value = (days && days > 0) ? days : ""; // Show placeholder if 0 or null
      freqWrap?.classList.remove("hidden");
      freqDays.classList.remove("hidden");
      freqDaysLabel?.classList.remove("hidden");
    } else if (days !== null && days !== undefined && days > 0) {
      freqMode.value = "limit";
      freqDays.value = days;
      freqWrap?.classList.remove("hidden");
      freqDays.classList.remove("hidden");
      freqDaysLabel?.classList.remove("hidden");
    } else {
      freqMode.value = "none";
      freqDays.value = "";
      freqWrap?.classList.add("hidden");
      freqDays.classList.add("hidden");
      freqDaysLabel?.classList.add("hidden");
    }
  }
}

// -----------------------------
// BIND AUTOSAVE FOR EACH FIELD
// -----------------------------
async function bindAllFields(venueId) {
  const normalFields = [
    "venue_name",
    "description",
    "address_line_1",
    "address_line_2",
    "city",
    "state",
    "postal_code",
    "venue_size",
    "stage_width_ft",
    "stage_depth_ft",
    "setup_location_description",
    "sound_equipment_description",
    "sound_engineer_details",
    "lighting_description",
    "arrival_no_earlier_than_hour",
    "arrival_no_earlier_than_period",
    "load_in_out_details",
    "default_pay_dollars",
    "default_pay_cents",
    "bar_tab_details",
    "food_tab_details",
    "website_url",
    "facebook_url",
    "instagram_url",
    "twitter_url",
    "yelp_url",
    "google_maps_url"
  ];

  normalFields.forEach(id =>
    bindAutosave(qs(id), id, venueId)
  );

  // BOOLEAN FIELDS
  bindAutosave(qs("artistFrequencyMode"), "artist_frequency_mode", venueId);
  // artistFrequencyDays has custom validation - handled separately below
  bindAutosave(qs("default_pay_cents"), "default_pay_cents", venueId, v => String(v || "0").replace(/\D/g, "").padStart(2, "0"));
  
  const cents = qs("default_pay_cents");
  if (cents) {
    cents.addEventListener("blur", () => {
      cents.value = String(cents.value || "0")
        .replace(/\D/g, "")
        .padStart(2, "0");
    });
  }

  // ARTIST FREQUENCY MODE HANDLING WITH VALIDATION
  const freqMode = document.getElementById("artistFrequencyMode");
  const freqWrap = document.getElementById("artistFrequencyLimit");
  const freqDays = document.getElementById("artistFrequencyDays");

  // Helper to show simple alert modal
  function showFrequencyAlert(message) {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-content" style="max-width: 400px; text-align: center;">
        <p style="margin-bottom: 20px; line-height: 1.6;">${message}</p>
        <button class="btn primary" style="min-width: 100px;">OK</button>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector("button").addEventListener("click", () => {
      overlay.remove();
      if (freqMode.value === "limit") {
        freqDays.focus();
      }
    });
  }

  if (freqMode && freqDays) {
    // Validate and save frequency days
    async function saveFrequencyDays() {
      const venueId = getVenueId();
      if (!venueId) return false;

      const val = freqDays.value.trim();
      if (!val) return false; // Empty - will be caught by blur handler

      const num = parseInt(val, 10);
      if (isNaN(num) || num < 1 || num > 365) {
        showFrequencyAlert("Please enter a number between 1-365.");
        freqDays.value = "";
        return false;
      }

      await fetch(`/api/venues/${venueId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ artist_frequency_days: num })
      });
      return true;
    }

    // Save on input change (with validation)
    freqDays.addEventListener("change", saveFrequencyDays);

    // Enter key saves and blurs
    freqDays.addEventListener("keypress", async (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const success = await saveFrequencyDays();
        if (success) {
          freqDays.blur();
        }
      }
    });

    // Blur handler - check if they left without valid input
    freqDays.addEventListener("blur", () => {
      if (freqMode.value !== "limit") return;
      
      const val = freqDays.value.trim();
      if (!val) {
        setTimeout(() => {
          showFrequencyAlert("You chose to limit how often an Artist can perform at your venue. Please enter how many days between performances (1-365).");
        }, 100);
        return;
      }

      const num = parseInt(val, 10);
      if (isNaN(num) || num < 1 || num > 365) {
        showFrequencyAlert("Please enter a number between 1-365.");
        freqDays.value = "";
      }
    });

    // Mode change handler
    freqMode.addEventListener("change", async () => {
      const venueId = getVenueId();
      if (!venueId) return;
      
      const freqDaysLabel = document.getElementById("artistFrequencyDaysLabel");

      if (freqMode.value === "none") {
        freqDays.value = "";
        freqWrap?.classList.add("hidden");
        freqDays.classList.add("hidden");
        freqDaysLabel?.classList.add("hidden");
        
        await fetch(`/api/venues/${venueId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            artist_frequency_days: 0,
            artist_frequency_mode: "none"
          })
        });
      } else {
        // Switching to Limit - clear field to show placeholder
        freqDays.value = "";
        freqWrap?.classList.remove("hidden");
        freqDays.classList.remove("hidden");
        freqDaysLabel?.classList.remove("hidden");
        
        await fetch(`/api/venues/${venueId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            artist_frequency_mode: "limit"
          })
        });

        setTimeout(() => freqDays.focus(), 0);
      }
    });
  }
}

// -----------------------------
// MEDIA HELPERS
// -----------------------------
function getVideoThumbnail(url) {
  const yt = url.match(/(?:youtube\.com.*v=|youtu\.be\/)([^&]+)/);
  return yt
    ? `https://img.youtube.com/vi/${yt[1]}/hqdefault.jpg`
    : "/app/static/img/video-placeholder.svg";
}

async function saveVideo() {
  const venueId = getVenueId();
  if (!venueId) return;

  const input = qs("videoUrl");
  if (!input) return;

  const url = input.value.trim();
  if (!url) return;

  const fd = new FormData();
  fd.append("video_url", url);

  const res = await fetch(`/api/venues/${venueId}/media/video`, {
    method: "POST",
    credentials: "include",
    body: fd
  });

  if (!res.ok) {
    console.error("Failed to save video");
    return;
  }

  input.value = "";
  loadVenueMedia(venueId);
}

// -----------------------------
// MEDIA INIT
// -----------------------------
function initVenueMedia(venueId) {
  qs("profilePic").onclick = () => qs("profilePicInput").click();

  qs("profilePicInput").onchange = async e => {
    const raw = e.target.files[0];
    if (!raw) return;
    const resized = await resizeImageForProfile(raw, 1400, 280); // 2× for retina
    const fd = new FormData();
    fd.append("file", resized, raw.name.replace(/\.[^.]+$/, '.jpg'));

    await fetch(`/api/venues/${venueId}/media/profile`, {
      method: "POST",
      credentials: "include",
      body: fd
    });

    loadVenueMedia(venueId);
  };

  qs("addPicBtn").onclick = () => qs("picInput").click();
  qs("picInput").onchange = async e => {
    const raw = e.target.files[0];
    if (!raw) return;
    const resized = await resizeImageForProfile(raw, 1200, 900);
    const fd = new FormData();
    fd.append("file", resized, raw.name.replace(/\.[^.]+$/, '.jpg'));

    await fetch(`/api/venues/${venueId}/media/picture`, {
      method: "POST",
      credentials: "include",
      body: fd
    });

    loadVenueMedia(venueId);
  };

  qs("videoUrl").addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveVideo();
    }
  });  

  qs("addVideoBtn").onclick = saveVideo;
}

// -----------------------------
// LOAD MEDIA
// -----------------------------
async function loadVenueMedia(venueId) {
  const res = await fetch(`/api/venues/${venueId}/media`, { credentials: "include" });
  if (!res.ok) return;

  const items = await res.json();

  qs("pictures").innerHTML = "";
  qs("videos").innerHTML = "";

  items.forEach(m => {
    if (m.media_type === "profile") {
      qs("profilePic").src = m.file_path;
    }

    if (m.media_type === "picture") {
      qs("pictures").innerHTML += `
        <div class="media-card" data-id="${m.id}" draggable="true">
          <img src="${m.file_path}">
          <div class="media-overlay center-overlay">
            <span class="drag-handle" draggable="true">☰</span>
            <input
              class="media-title"
              draggable="false"
              value="${m.title || ""}"
              data-id="${m.id}"
            />
            <button class="delete-btn" data-id="${m.id}">Delete</button>
          </div>
        </div>
      `;
    }

    if (m.media_type === "video") {
      qs("videos").innerHTML += `
        <div class="media-card" data-id="${m.id}" draggable="true">
          <img src="${getVideoThumbnail(m.video_url)}">
          <div class="media-overlay center-overlay">
            <span class="drag-handle" draggable="true">☰</span>
            <input
              class="media-title"
              draggable="false"
              value="${m.title || ""}"
              data-id="${m.id}"
            />
            <button class="delete-btn" data-id="${m.id}">Delete</button>
          </div>
        </div>
      `;
    }
  });

  initMediaDragAndDrop("pictures");
  initMediaDragAndDrop("videos");
}

// -----------------------------
// DRAG & DROP
// -----------------------------
function initMediaDragAndDrop(containerId) {
  const container = qs(containerId);
  if (!container) return;
  
  container.addEventListener("mousedown", e => {
    const title = e.target.closest(".media-title");
    if (!title) return;
  
    const card = title.closest(".media-card");
    if (!card) return;
  
    card.setAttribute("draggable", "false");
  });
  if (!container) return;

  let dragged = null;
  let didMove = false;

  container.addEventListener("dragstart", e => {
    if (e.target.classList.contains("media-title")) {
      e.preventDefault();
      return;
    }

    dragged = e.target.closest(".media-card");
    if (!dragged) return;

    dragged.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });

  container.addEventListener("dragover", e => {
    e.preventDefault();
    if (!dragged) return;

    const card = e.target.closest(".media-card");
    if (!card || card === dragged) return;

    const rect = card.getBoundingClientRect();
    const after = e.clientY > rect.top + rect.height / 2;
    container.insertBefore(dragged, after ? card.nextSibling : card);
    didMove = true;
  });

  container.addEventListener("dragend", async () => {
    if (!dragged) return;
    dragged.classList.remove("dragging");

    if (didMove) {
      const cards = [...container.querySelectorAll(".media-card")];
      for (let i = 0; i < cards.length; i++) {
        await fetch(`/api/venues/media/${cards[i].dataset.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ display_order: i })
        });
      }
    }

    dragged = null;
    didMove = false;
  });
}

// -----------------------------
// GLOBAL MEDIA EVENTS
// -----------------------------
document.addEventListener("dblclick", e => {
  const input = e.target.closest(".media-title");
  if (!input) return;

  input.focus();
  input.select();
});

document.addEventListener("dragstart", e => {
  if (e.target.classList.contains("media-title")) {
    e.preventDefault();
  }
});

document.addEventListener("keydown", e => {
  if (e.target.classList.contains("media-title") && e.key === "Enter") {
    e.preventDefault();
    e.target.blur();
  }
});

document.addEventListener("blur", async e => {
  if (e.target && e.target.closest) {
    const card = e.target.closest(".media-card");
    if (card) {
      card.setAttribute("draggable", "true");
    }
  }

  if (!e.target || !e.target.classList || !e.target.classList.contains("media-title")) return;

  await fetch(`/api/venues/media/${e.target.dataset.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ title: e.target.value.trim() })
  });
}, true);

document.addEventListener("click", async e => {
  if (!e.target.classList.contains("delete-btn")) return;
  if (!confirm("Delete this media?")) return;

  await fetch(`/api/venues/media/${e.target.dataset.id}`, {
    method: "DELETE",
    credentials: "include"
  });

  e.target.closest(".media-card").remove();
});

// -----------------------------
// INIT
// -----------------------------
async function init() {
  const venueId = getVenueId();
  if (!venueId) return;

  await loadVenue();
  bindAllFields(venueId);
  initVenueMedia(venueId);
  loadVenueMedia(venueId);
  
  // STAGE toggle - clear fields when No
  const stageToggle = qs("has_stage");
  if (stageToggle) {
    stageToggle.addEventListener("change", async () => {
      const hasStage = stageToggle.value === "true";
      if (!hasStage) {
        await clearStageFields(venueId);
      } else {
        await fetch(`/api/venues/${venueId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ has_stage: true })
        });
      }
      applyToggles();
    });
  }
  
  // SOUND EQUIPMENT toggle - clear fields when No
  const soundEquipToggle = qs("has_sound_equipment");
  if (soundEquipToggle) {
    soundEquipToggle.addEventListener("change", async () => {
      const hasSound = soundEquipToggle.value === "true";
      if (!hasSound) {
        await clearSoundEquipmentFields(venueId);
      } else {
        await fetch(`/api/venues/${venueId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ has_sound_equipment: true })
        });
      }
      applyToggles();
    });
  }
  
  // SOUND ENGINEER toggle - clear fields when No
  const soundEngineerToggle = qs("has_sound_engineer");
  if (soundEngineerToggle) {
    soundEngineerToggle.addEventListener("change", async () => {
      const hasEngineer = soundEngineerToggle.value === "true";
      if (!hasEngineer) {
        await clearSoundEngineerFields(venueId);
      } else {
        await fetch(`/api/venues/${venueId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ has_sound_engineer: true })
        });
      }
      applyToggles();
    });
  }
  
  // LIGHTING toggle - clear fields when No
  const lightingToggle = qs("has_lighting");
  if (lightingToggle) {
    lightingToggle.addEventListener("change", async () => {
      const hasLighting = lightingToggle.value === "true";
      if (!hasLighting) {
        await clearLightingFields(venueId);
      } else {
        await fetch(`/api/venues/${venueId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ has_lighting: true })
        });
      }
      applyToggles();
    });
  }
  
  // ARRIVAL TIME toggle - clear fields when Flexible, set defaults when No Earlier Than
  const arrivalToggle = qs("arrival_time_type");
  if (arrivalToggle) {
    arrivalToggle.addEventListener("change", async () => {
      const arrivalType = arrivalToggle.value;
      if (arrivalType === "flexible") {
        await clearArrivalFields(venueId);
      } else {
        // Set defaults to 12 PM
        setArrivalDefaults();
        await fetch(`/api/venues/${venueId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            arrival_time_type: "no_earlier_than",
            arrival_no_earlier_than_hour: "12",
            arrival_no_earlier_than_period: "PM"
          })
        });
      }
      applyToggles();
    });
  }

  // FLYER SETTINGS
  const autoFlyersToggle = document.getElementById('auto_flyers');
  const flyerTplRow = document.getElementById('flyerTemplateRow');
  if (autoFlyersToggle) {
    autoFlyersToggle.addEventListener('change', async () => {
      const val = autoFlyersToggle.checked;
      if (flyerTplRow) flyerTplRow.style.display = val ? '' : 'none';
      await fetch(`/api/venues/${venueId}`, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_flyers: val ? 1 : 0 })
      });
    });
  }
  const flyerTplSel = document.getElementById('default_flyer_template_id');
  if (flyerTplSel) {
    flyerTplSel.addEventListener('change', async () => {
      await fetch(`/api/venues/${venueId}/settings/default-template`, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_id: flyerTplSel.value || null })
      });
    });
  }
}

document.addEventListener("DOMContentLoaded", init);
