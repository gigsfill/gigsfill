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

    // Normalize URL fields - ensure https://www. prefix
    const urlFields = ['website_url','facebook_url','instagram_url','twitter_url','youtube_url','spotify_url','tiktok_url'];
    if (urlFields.includes(field) && input.value.trim()) {
      let url = input.value.trim();
      if (!/^https?:\/\//i.test(url)) {
        if (!/^www\./i.test(url)) {
          url = 'www.' + url;
        }
        url = 'https://' + url;
      } else if (/^https?:\/\/(?!www\.)/i.test(url) && !url.includes('://www.')) {
        url = url.replace(/^(https?:\/\/)/, '$1www.');
      }
      input.value = url;
    }

    await fetch(`/artists/${artistId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        [field]: input.value.trim()
      })
    });
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
  const artistProfileBtn = document.getElementById("artistProfileBtn");
  if (artistProfileBtn) {
    artistProfileBtn.href = `/app/artist-profile.html?artist_id=${artistId}`;
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
    await fetch(`/artists/${artistId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload)
    });
  }
  

  function initMediaDragAndDrop(containerId) {
    const container = qs(containerId);
    if (!container) return;
  
    let dragged = null;
    let didMove = false;

  
    container.addEventListener("dragstart", e => {
      if (!(e.target instanceof HTMLElement)) return;
    
      let card = null;
    
      // Audio: drag only from handle
      if (container.id === "audio") {
        const handle = e.target.closest(".drag-handle");
        if (!handle) return;
        card = handle.closest(".audio-row");
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
        // Support both .media-card and .audio-row
        const selector = container.id === "audio" ? ".audio-row" : ".media-card";
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
    
      // Support both .media-card and .audio-row
      const card = e.target.closest(".media-card") || e.target.closest(".audio-row");
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
    `;

    // Populate styles + lineup checkboxes
    const formatsContainer = formatsBlock.querySelector('.band-formats') || formatsBlock;
    formatsContainer.innerHTML = `
      <div>
        <label style="font-weight: 600; font-size: 0.85rem; color: var(--text-gray, #94a3b8); display: block; margin-bottom: 8px;">Styles (select at least one)</label>
        <div style="display: flex; gap: 16px; flex-wrap: wrap;" id="stylesChecks">
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="artist_style" value="Country" /><span>Country</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="artist_style" value="Hip-Hop" /><span>Hip-Hop</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="artist_style" value="Indie" /><span>Indie</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="artist_style" value="Jazz" /><span>Jazz</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="artist_style" value="Latin" /><span>Latin</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="artist_style" value="Pop" /><span>Pop</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="artist_style" value="Reggae" /><span>Reggae</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="artist_style" value="Rock" /><span>Rock</span></label>
        </div>
      </div>
      <div>
        <label style="font-weight: 600; font-size: 0.85rem; color: var(--text-gray, #94a3b8); display: block; margin-bottom: 8px;">Lineup (select at least one)</label>
        <div style="display: flex; gap: 16px; flex-wrap: wrap;" id="lineupChecks">
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="band_format" value="Solo" /><span>Solo</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="band_format" value="Duo" /><span>Duo</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="band_format" value="Trio" /><span>Trio</span></label>
          <label style="display: flex; align-items: center; gap: 6px;"><input type="checkbox" name="band_format" value="Full Band" /><span>Full Band</span></label>
        </div>
      </div>
    `;

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
  
    if (artist.artist_type === "Live Band") {
      formatsBlock.classList.remove("hidden");
  
      if (artist.band_formats) {
        artist.band_formats.split(",").forEach(v => {
          const cb = [...formatChecks].find(c => c.value === v.trim());
          if (cb) cb.checked = true;
        });
      }
      if (artist.styles) {
        artist.styles.split(",").forEach(v => {
          const cb = [...styleChecks].find(c => c.value === v.trim());
          if (cb) cb.checked = true;
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

  // v73: FIXED - Load users who have access to this artist
  async function initBookingContact(artistId, artist) {
    const select = qs("booking_contact");
  
    // Get current user info
    const userRes = await fetch('/api/me', { credentials: 'include' });
    if (!userRes.ok) return;
    
    const currentUser = await userRes.json();
  
    // Get all users with access to this artist (for now, just the owner)
    const users = [currentUser];
  
    // Build dropdown with user details
    let optionsHTML = '<option value="">Select Booking Contact</option>';
    
    users.forEach(user => {
      const displayName = `${user.first_name} ${user.last_name}`.trim() || 'Unnamed User';
      const email = user.email || '';
      const phone = user.phone || '';
      
      // Create user ID value
      const value = user.id;
      
      // Format: "John Carta - john@johncarta.com - 805-231-0046"
      let label = displayName;
      if (email) label += ` - ${email}`;
      if (phone) label += ` - ${phone}`;
      
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
  
      const res = await fetch(`/artists/${artistId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ booking_contact: value })
      });
  
      if (!res.ok) {
        console.error("Failed to save booking contact");
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
  
    // AUDIO
    qs("addAudioBtn").onclick = () => qs("audioInput").click();
    qs("audioInput").onchange = async e => {
      const file = e.target.files[0];
      if (!file) return;
  
      const fd = new FormData();
      fd.append("file", file);
  
      await fetch(`/api/artists/${artistId}/media/audio`, {
        method: "POST",
        credentials: "include",
        body: fd
      });
  
      loadMedia(artistId);
    };
  
    // VIDEO
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
        console.error("Failed to add video");
        return;
      }
    
      qs("videoUrl").value = "";
      loadMedia(artistId);
    };
    
  }

  function getVideoThumbnail(url) {
    // YouTube
    const ytMatch = url.match(/(?:youtube\.com.*v=|youtu\.be\/)([^&]+)/);
    if (ytMatch) {
      return `https://img.youtube.com/vi/${ytMatch[1]}/hqdefault.jpg`;
    }
  
    // Vimeo (fallback icon for now)
    return "/app/static/img/video-placeholder.svg";
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
  
        picturesEl.insertAdjacentHTML("beforeend", `
          <div class="media-card" data-id="${m.id}">
            <img src="${m.file_path}">
            <div class="media-overlay center-overlay">
              <span class="drag-handle" draggable="true">☰</span>
              <input
                value="${m.title || ""}"
                placeholder="Title"
                data-id="${m.id}"
                class="media-title"
              />
              <button class="delete-btn" data-id="${m.id}">Delete</button>
            </div>
          </div>
        `);
      }
  
      // -----------------------------
      // AUDIO
      // -----------------------------
      if (m.media_type === "audio") {
        hasAudio = true;
  
        audioEl.insertAdjacentHTML("beforeend", `
          <div class="audio-row" data-id="${m.id}">
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
        `);
      }
  
      // -----------------------------
      // VIDEOS
      // -----------------------------
      if (m.media_type === "video") {
        hasVideos = true;
        const thumb = getVideoThumbnail(m.video_url);
  
        videosEl.insertAdjacentHTML("beforeend", `
          <div class="media-card" data-id="${m.id}">
            <img src="${thumb}" alt="Video thumbnail">
            <div class="media-overlay center-overlay">
              <span class="drag-handle" draggable="true">☰</span>
              <input
                class="media-title"
                value="${m.title || ""}"
                placeholder="Title"
                data-id="${m.id}"
              />
              <button class="delete-btn" data-id="${m.id}">Delete</button>
            </div>
          </div>
        `);
      }
    });
  }
  
  

  document.addEventListener("keydown", e => {
    if (!(e.target instanceof HTMLElement)) return;
    if (!e.target.classList.contains("media-title")) return;
  
    if (e.key === "Enter") {
      e.preventDefault();
      e.target.blur();
    }
  });
  
  
  
  document.addEventListener("blur", async e => {
    if (!(e.target instanceof HTMLElement)) return;
    if (!e.target.classList.contains("media-title")) return;
  
    const id = e.target.dataset.id;
    const title = e.target.value.trim();
  
    await fetch(`/api/media/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ title })
    });
  }, true);
  
  
  document.addEventListener("click", async e => {
    if (!(e.target instanceof HTMLElement)) return;
    if (!e.target.classList.contains("delete-btn")) return;
  
    const id = e.target.dataset.id;
    if (!confirm("Delete this media?")) return;
  
    await fetch(`/api/media/${id}`, {
      method: "DELETE",
      credentials: "include"
    });
  
    // Support both .media-card and .audio-row
    const card = e.target.closest(".media-card") || e.target.closest(".audio-row");
    if (card) card.remove();
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
