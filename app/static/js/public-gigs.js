// PUBLIC GIGS PAGE - COPIED FROM artist_book-gigs.js
// Simplified for public view-only access

/* ---------------- ANALYTICS TRACKING ---------------- */

// Generate or retrieve session ID for tracking
function getSessionId() {
  let sessionId = sessionStorage.getItem('gf_session_id');
  if (!sessionId) {
    sessionId = 'sess_' + Math.random().toString(36).substring(2, 15) + Date.now().toString(36);
    sessionStorage.setItem('gf_session_id', sessionId);
  }
  return sessionId;
}

// Track analytics event (fire and forget)
function trackEvent(eventType, data = {}) {
  try {
    fetch('/api/analytics/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_type: eventType,
        session_id: getSessionId(),
        ...data
      })
    }).catch(() => {}); // Silently ignore errors
  } catch (e) {
    // Never let tracking break the app
  }
}

/* ---------------- END ANALYTICS ---------------- */

// Global closeModal function - EXACT COPY
window.closeModal = function() {
  const overlay = document.getElementById('modalOverlay');
  if (overlay) {
    overlay.classList.add("hidden");
    overlay.style.display = "";
  }
};

document.addEventListener("DOMContentLoaded", async () => {
  const calendarEl = document.getElementById("calendar");
  const monthLabel = document.getElementById("monthLabel");
  const prevBtn = document.getElementById("prevMonth");
  const nextBtn = document.getElementById("nextMonth");

  if (!calendarEl) {
    console.error("❌ calendar element not found");
    return;
  }

  let currentDate = new Date();
  let gigs = [];
  let allGigs = [];
  let allCities = [];
  let allVenues = []; // For venue autocomplete
  let allArtists = []; // For artist autocomplete

  // Get city from URL params
  const params = new URLSearchParams(window.location.search);
  const cityParam = params.get("city");

  /* ---------------- UTIL - EXACT COPY ---------------- */

  function formatTime12Hour(timeStr) {
    if (!timeStr) return "";
    const [h, m] = timeStr.split(":").map(Number);
    const period = h >= 12 ? "PM" : "AM";
    const hour = ((h + 11) % 12) + 1;
    return `${hour}:${m.toString().padStart(2, "0")} ${period}`;
  }

  // EXACT COPY from artist_book-gigs.js
  function getMonthDays(year, month) {
    const days = [];
    
    const firstDay = new Date(year, month, 1);
    const firstDayOfWeek = firstDay.getDay();
    
    for (let i = firstDayOfWeek - 1; i >= 0; i--) {
      const d = new Date(year, month, 0 - i);
      days.push(d);
    }
    
    const d = new Date(year, month, 1);
    while (d.getMonth() === month) {
      days.push(new Date(d));
      d.setDate(d.getDate() + 1);
    }
    
    const lastDay = days[days.length - 1];
    const lastDayOfWeek = lastDay.getDay();
    if (lastDayOfWeek < 6) {
      for (let i = 1; i <= 6 - lastDayOfWeek; i++) {
        const d = new Date(year, month + 1, i);
        days.push(d);
      }
    }
    
    return days;
  }

  // Haversine distance calculation - COPIED from artist_book-gigs.js
  function haversineDistance(lat1, lon1, lat2, lon2) {
    const R = 3959; // Earth's radius in miles
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
  }

  function getGigClass(gig) {
    if (gig.status === "booked") {
      return "booked";
    }
    // Multi-slot gig with at least one booked slot shows as booked (red)
    if (gig.booked_slots_count > 0) {
      return "booked";
    }
    return "open";
  }

  /* ---------------- RENDER CALENDAR - EXACT COPY ---------------- */

  function renderCalendar() {
    calendarEl.innerHTML = "";

    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();

    monthLabel.textContent = currentDate.toLocaleString("default", {
      month: "long",
      year: "numeric"
    });
    
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    getMonthDays(year, month).forEach(day => {
      const iso = `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
      const cell = document.createElement("div");
      cell.className = "day";
      
      const isCurrentMonth = day.getMonth() === month;
      if (!isCurrentMonth) {
        cell.classList.add("other-month");
      }
      
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

      const dayGigs = gigs.filter(g => g.date === iso).sort((a, b) => {
        return (a.start_time || '').localeCompare(b.start_time || '');
      });

      if (dayGigs.length > 0) {
        const gigsContainer = document.createElement("div");
        gigsContainer.className = "gigs-container";
        
        dayGigs.forEach(g => {
          const div = document.createElement("div");
          div.className = `gig ${getGigClass(g)}`;
          const icons = { 'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤', 'Trivia Host': '🧠' };
          const icon = (icons[g.artist_type] || '🎵') + ' ';
          div.textContent = `${icon}${formatTime12Hour(g.start_time)} · ${g.venue_name}`;
          div.onclick = e => {
            e.stopPropagation();
            openGigModal(g);
          };
          gigsContainer.appendChild(div);
        });
        
        cell.appendChild(gigsContainer);
        
        cell.onclick = (e) => {
          if (e.target === cell || e.target === dayNumber) {
            openDayGigsModal(day, dayGigs);
          }
        };
      }

      calendarEl.appendChild(cell);
    });
  }

  /* ---------------- DAY GIGS MODAL ---------------- */
  
  async function openDayGigsModal(day, dayGigs) {
    const overlay = document.getElementById("modalOverlay");
    const modal = overlay.querySelector(".modal");

    modal.classList.add("day-modal");

    const title = document.getElementById("modalTitle");
    const body = document.getElementById("modalBody");
    
    const dateStr = day.toLocaleDateString('en-US', { 
      weekday: 'long', 
      year: 'numeric', 
      month: 'long', 
      day: 'numeric' 
    });
    
    title.textContent = dateStr;
    
    // Add separator
    setTimeout(() => {
      if (!title.nextElementSibling || !title.nextElementSibling.classList.contains('modal-separator')) {
        const sep = document.createElement('div');
        sep.className = 'modal-separator';
        title.after(sep);
      }
    }, 10);
    
    // Column headers + grid layout
    let content = `
      <div class="day-modal-content" style="
        display: grid;
        grid-auto-rows: auto;
        row-gap: 10px;
        width: 100%;
        min-width: 820px;
      ">
    `;

    // Pre-fetch slot data for multi-slot gigs so we can show artist names
    await Promise.all(dayGigs.filter(g => g.status === 'booked' || (g.booked_slots_count > 0)).map(async g => {
      try {
        const r = await fetch(`/api/gigs/${g.id}/slots`);
        if (r.ok) g._slots = (await r.json()).sort((a,b) => (a.start_time||'').localeCompare(b.start_time||''));
      } catch(e) {}
    }));

    dayGigs.forEach(g => {
      const gigClass = getGigClass(g);

      let gigBg, gigBorder, gigTextColor;
      if (gigClass === 'open') {
        gigBg = 'linear-gradient(135deg,#10b981,#059669)';
        gigBorder = '#059669';
        gigTextColor = '#000000';
      } else {
        // booked (red)
        gigBg = 'linear-gradient(135deg,#ef4444,#b91c1c)';
        gigBorder = '#b91c1c';
        gigTextColor = '#ffffff';
      }

      const location = `${g.venue_city || g.city || ''}${(g.venue_city || g.city) && (g.venue_state || g.state) ? ', ' : ''}${g.venue_state || g.state || ''}`;

      let artistTypeDisplay = esc(g.artist_type || 'Any');
      if (g.artist_type === 'Live Band' && g.band_formats)
        artistTypeDisplay += ` \u2022 ${g.band_formats.split(',').map(f => esc(f.trim())).join(', ')}`;

      // Artist cell — from slots if available, else gig directly
      let artistDisplay = '';
      if (gigClass === 'open') {
        artistDisplay = `<span style="opacity:0.75;">OPEN</span>`;
      } else if (g._slots) {
        const booked = g._slots.filter(s => s.status === 'booked' && s.artist_name);
        artistDisplay = booked.length === 0
          ? '<span style="opacity:0.75;">Booked</span>'
          : booked.map(s => `<a href="/app/artist-profile.html?artist_id=${s.artist_id}" target="_blank" onclick="event.stopPropagation()" style="color:${gigTextColor};text-decoration:underline;font-weight:600;" onmouseover="this.style.opacity='0.8'" onmouseout="this.style.opacity='1'">${esc(s.artist_name)}</a>`).join('<br>');
      } else if (g.artist_name) {
        artistDisplay = `<a href="/app/artist-profile.html?artist_id=${g.artist_id}" target="_blank" onclick="event.stopPropagation()" style="color:${gigTextColor};text-decoration:underline;font-weight:600;">${esc(g.artist_name)}</a>`;
      } else {
        artistDisplay = `<span style="opacity:0.75;">Booked</span>`;
      }

      const icons = {'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠'};
      const icon = icons[g.artist_type] || '🎵';

      content += `
        <div
          onclick="openGigFromDayModal(${g.id})"
          class="gig ${gigClass} gig-row"
          style="
            display: grid;
            grid-template-columns: 130px minmax(130px,1fr) minmax(120px,1fr) minmax(120px,1fr) minmax(150px,1.5fr);
            column-gap: 12px;
            align-items: center;
            padding: 6px 10px;
            margin: 0;
            line-height: 1.3;
            white-space: nowrap;
            font-weight: 700;
            background: ${gigBg};
            border: 1px solid ${gigBorder};
            border-radius: 6px;
            color: ${gigTextColor};
            cursor: pointer;
          "
        >
          <div>${icon} ${formatTime12Hour(g.start_time)}${g.end_time ? ' \u2013 ' + formatTime12Hour(g.end_time) : ''}</div>
          <div style="overflow:hidden;text-overflow:ellipsis;">${esc(g.venue_name || '')}</div>
          <div style="overflow:hidden;text-overflow:ellipsis;">${esc(location)}</div>
          <div style="overflow:hidden;text-overflow:ellipsis;">${artistDisplay}</div>
          <div style="overflow:hidden;text-overflow:ellipsis;">${esc(artistTypeDisplay)}</div>
        </div>
      `;
    });

    content += '</div>';
    body.innerHTML = content;

    // Close button in #modalActions — lower right, with border-top separator
    const _ma = document.getElementById("modalActions");
    if (_ma) {
      _ma.innerHTML = `<button onclick="closeModal()" class="btn ghost">Close</button>`;
      _ma.style.display = '';
    }

    overlay.classList.remove("hidden");
  }

  window.openGigFromDayModal = function(gigId) {
    const gig = gigs.find(g => g.id === gigId);
    if (gig) {
      const overlay = document.getElementById("modalOverlay");
      const modal = overlay.querySelector(".modal");
      modal.classList.remove("day-modal");
      overlay.classList.add("hidden");
      
      setTimeout(() => {
        openGigModal(gig);
      }, 50);
    }
  };

  /* ---------------- GIG MODAL ---------------- */

  async function openGigModal(gig) {
    // Track gig click
    trackEvent('gig_click', {
      gig_id: gig.id,
      venue_id: gig.venue_id,
      artist_id: gig.artist_id || null,
      city: gig.venue_city || gig.city || null,
      state: gig.venue_state || gig.state || null,
      event_data: {
        gig_status: gig.status,
        artist_type: gig.artist_type,
        venue_name: gig.venue_name,
        gig_date: gig.date
      }
    });
    
    const overlay = document.getElementById("modalOverlay");
    const modal = overlay.querySelector(".modal");
    const title = document.getElementById("modalTitle");
    const body = document.getElementById("modalBody");
    
    modal.classList.remove("day-modal");
    title.textContent = "Gig Details";
    
    setTimeout(() => {
      if (!title.nextElementSibling || !title.nextElementSibling.classList.contains('modal-separator')) {
        const sep = document.createElement('div');
        sep.className = 'modal-separator';
        title.after(sep);
      }
    }, 10);

    const cityState = `${gig.venue_city || gig.city || ''}${(gig.venue_city || gig.city) && (gig.venue_state || gig.state) ? ', ' : ''}${gig.venue_state || gig.state || ''}`;
    const addr1 = gig.address_line_1 || '';
    const addr2 = gig.address_line_2 || '';
    let locationHtml = '';
    if (addr1) locationHtml += `${esc(addr1)}<br>`;
    if (addr2) locationHtml += `${esc(addr2)}<br>`;
    if (cityState) locationHtml += esc(cityState);

    // Use fixed label width for alignment
    const labelStyle = 'font-weight: 600; color: var(--text-muted); min-width: 100px; display: inline-block; vertical-align: top;';
    
    const isMultiSlot = gig.total_slots_count > 0;
    
    let content = `
      <div style="font-size: 0.95rem; line-height: 2;">
        <div><span style="${labelStyle}">Date:</span> ${esc(gig.date)}</div>
        ${!isMultiSlot ? `<div><span style="${labelStyle}">Time:</span> ${formatTime12Hour(gig.start_time)} – ${formatTime12Hour(gig.end_time)}</div>` : ''}
        <div><span style="${labelStyle}">Venue:</span> <a href="/app/venue-profile.html?venue_id=${gig.venue_id}" target="_blank" rel="noopener" style="color: var(--cyan); text-decoration: none;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'">${esc(gig.venue_name)}</a></div>
        <div><span style="${labelStyle}">Location:</span> <span style="display:inline-block; line-height:1.5;">${locationHtml || 'N/A'}</span></div>
      </div>
    `;

    // If BOOKED (single-artist gig), show artist info
    if (!isMultiSlot && gig.status === "booked" && gig.artist_id && gig.artist_name) {
      content += `
        <div style="
          margin-top: 16px;
          padding: 16px;
          background: rgba(239, 68, 68, 0.15);
          border: 1px solid rgba(239, 68, 68, 0.3);
          border-radius: 8px;
        ">
          <div style="font-size: 0.875rem; color: #ffffff; margin-bottom: 4px; font-weight: 600;">Booked by:</div>
          <a href="/app/artist-profile.html?artist_id=${gig.artist_id}" target="_blank" rel="noopener" style="color: #ffffff; font-weight: 700; font-size: 1.1rem; text-decoration: none;">
            ${esc(gig.artist_name)}
          </a>
        </div>
      `;
    } 
    // If OPEN, show "Looking For" info
    else if (!isMultiSlot && gig.status === "open") {
      let lookingFor = esc(gig.artist_type || 'Any Artist');
      if (gig.artist_type === 'Live Band' && gig.band_formats) {
        lookingFor += ` (${gig.band_formats.split(',').map(f => esc(f.trim())).join(', ')})`;
      }
      
      content += `
        <div style="
          margin-top: 16px;
          padding: 16px;
          background: rgba(16, 185, 129, 0.15);
          border: 1px solid rgba(16, 185, 129, 0.3);
          border-radius: 8px;
        ">
          <div style="font-size: 1.1rem; color: #ffffff; margin-bottom: 8px; font-weight: 700;">No Artist Booked For This Gig...</div>
          <div style="color: #ffffff; font-weight: 600; font-size: 0.9rem;">
            Looking For: ${lookingFor}
          </div>
        </div>
      `;
    }

    // Artist type info (after status box)
    if (gig.artist_type) {
      content += `
        <div style="font-size: 0.95rem; line-height: 2; margin-top: 16px;">
          <div><span style="${labelStyle}">Artist Type:</span> ${esc(gig.artist_type)}</div>
      `;
      
      if (gig.artist_type === 'Live Band' && gig.band_formats) {
        content += `
          <div><span style="${labelStyle}">Band Formats:</span> ${gig.band_formats.split(',').map(f => esc(f.trim())).join(', ')}</div>
        `;
      }
      
      content += `</div>`;
    }

    // For multi-slot gigs, fetch and display slots (after Artist Type / Band Formats)
    if (isMultiSlot) {
      try {
        const slotsRes = await fetch(`/api/gigs/${gig.id}/slots`);
        if (slotsRes.ok) {
          const slots = (await slotsRes.json()).sort((a,b) => (a.start_time||'').localeCompare(b.start_time||''));
          content += '<div style="margin-top:14px;">';
          slots.forEach(s => {
            const isBooked = s.status === 'booked';
            const color = isBooked ? '#ef4444' : '#22c55e';
            const bg = isBooked ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)';
            const slotLabel = isBooked 
              ? `<a href="/app/artist-profile.html?artist_id=${s.artist_id}" target="_blank" style="color:${color}; text-decoration:none; font-weight:600;">${esc(s.artist_name || 'Booked')}</a>`
              : '<span style="color:#22c55e; font-weight:600;">Open</span>';
            content += `<div style="display:flex; align-items:center; padding:9px 14px; margin-bottom:6px; background:${bg}; border:1px solid ${color}33; border-radius:6px; font-size:0.9rem;">
              <span style="font-weight:700; min-width:56px;">Slot ${s.slot_number}</span>
              <span style="min-width:175px;">${formatTime12Hour(s.start_time)} – ${formatTime12Hour(s.end_time)}</span>
              <span style="margin-left:auto; text-align:right;">${slotLabel}</span>
            </div>`;
          });
          content += '</div>';
        }
      } catch(e) { console.error('Error loading slots:', e); }
    }

    body.innerHTML = content;

    // Only show flyer button for booked gigs (open gigs don't have flyers yet)
    const gigIsBooked = gig.status === 'booked' || gig.booked_slots_count > 0;
    if (gigIsBooked) {
      fetch(`/api/gigs/${gig.id}/flyer/public`).then(r=>r.json()).then(data=>{
        if (!data.exists) return;
        const fd = document.createElement('div');
        fd.style.cssText = 'margin-top:16px;text-align:center;';
        fd.innerHTML = `<button onclick="showPublicFlyer(${gig.id})" style="padding:8px 20px;border:1px solid rgba(139,92,246,0.4);border-radius:8px;background:rgba(139,92,246,0.15);color:#c4b5fd;cursor:pointer;font-size:0.85rem;font-weight:600;">🎨 View Event Flyer</button>`;
        body.appendChild(fd);
        // Cache flyer data for modal
        window._publicFlyerData = window._publicFlyerData || {};
        window._publicFlyerData[gig.id] = data;
      }).catch(()=>{});
    }
    
    document.getElementById("modalActions").innerHTML = `
      <button onclick="closeModal()" class="btn ghost">Close</button>
    `;
    
    overlay.classList.remove("hidden");
  }

  /* ---------------- LOAD DATA ---------------- */

  async function loadGigs() {
    try {
      const response = await fetch('/api/gigs/public');
      if (response.ok) {
        allGigs = await response.json();
        gigs = [...allGigs];
        updateVenueList();
        updateArtistList();
      }
    } catch (error) {
      console.error('Error loading gigs:', error);
    }
  }

  async function loadCities() {
    try {
      const response = await fetch('/api/cities/all');
      if (response.ok) {
        allCities = await response.json();
      }
    } catch (error) {
      console.error('Error loading cities:', error);
    }
  }

  // Extract unique venues from gigs - COPIED from artist_book-gigs.js
  function updateVenueList() {
    const venueSet = new Set();
    allGigs.forEach(gig => {
      if (gig.venue_name) {
        venueSet.add(gig.venue_name);
      }
    });
    allVenues = Array.from(venueSet).sort();
  }

  // Extract unique artists from booked gigs
  function updateArtistList() {
    const artistSet = new Set();
    allGigs.forEach(gig => {
      if (gig.artist_name) {
        artistSet.add(gig.artist_name);
      }
    });
    allArtists = Array.from(artistSet).sort();
  }

  /* ---------------- FILTERS - COPIED FROM ARTIST_BOOK-GIGS.JS ---------------- */

  function applyFilters() {
    // Don't apply if city is blocked by invalid city overlay
    if (typeof isCityBlocked === 'function' && isCityBlocked()) return;
    
    const venueFilter = document.getElementById('searchVenue').value.toLowerCase().trim();
    const cityFilter = document.getElementById('searchCity').value.toLowerCase().trim();
    const artistFilter = document.getElementById('searchArtist').value.toLowerCase().trim();
    const mileRadius = parseInt(document.getElementById('mileRadius').value) || 0;

    // Get city coordinates for radius search
    let cityCoords = null;
    if (cityFilter && mileRadius > 0) {
      const matchedCity = allCities.find(c => 
        c.city.toLowerCase() === cityFilter.toLowerCase()
      );
      if (matchedCity) {
        cityCoords = { lat: matchedCity.lat, lon: matchedCity.lon };
      }
    }

    // Get active artist types
    const activeArtistTypes = [];
    document.querySelectorAll('.artist-type-filter-toggle[data-active="true"]').forEach(btn => {
      activeArtistTypes.push(btn.dataset.type);
    });

    // Get active band formats
    const activeBandFormats = [];
    document.querySelectorAll('.band-format-filter-toggle[data-active="true"]').forEach(btn => {
      activeBandFormats.push(btn.dataset.format);
    });

    gigs = allGigs.filter(gig => {
      // Venue filter
      if (venueFilter && gig.venue_name && !gig.venue_name.toLowerCase().includes(venueFilter)) {
        return false;
      }

      // Artist filter (search by booked artist name)
      if (artistFilter && gig.artist_name && !gig.artist_name.toLowerCase().includes(artistFilter)) {
        return false;
      }
      // If filtering by artist but gig has no artist, exclude it
      if (artistFilter && !gig.artist_name) {
        return false;
      }

      // City/radius filter
      if (cityFilter) {
        if (cityCoords && mileRadius > 0 && gig.venue_lat && gig.venue_lon) {
          // Use radius search
          const distance = haversineDistance(cityCoords.lat, cityCoords.lon, gig.venue_lat, gig.venue_lon);
          if (distance > mileRadius) {
            return false;
          }
        } else {
          // Fallback to text match
          const gigCity = (gig.venue_city || gig.city || '').toLowerCase();
          if (!gigCity.includes(cityFilter)) {
            return false;
          }
        }
      }

      // Artist type filter
      if (activeArtistTypes.length > 0) {
        if (!gig.artist_type || !activeArtistTypes.includes(gig.artist_type)) {
          return false;
        }
      }

      // Band format filter
      if (activeBandFormats.length > 0 && gig.artist_type === 'Live Band') {
        if (!gig.band_formats) return false;
        const gigFormats = gig.band_formats.split(',').map(f => f.trim());
        const hasMatch = activeBandFormats.some(f => gigFormats.includes(f));
        if (!hasMatch) return false;
      }

      return true;
    });

    document.getElementById('searchResults').textContent = `Found ${gigs.length} gig${gigs.length !== 1 ? 's' : ''} matching your criteria`;
    renderCalendar();
    
    // Track filter usage (only if any filter is active)
    if (venueFilter || cityFilter || artistFilter || activeArtistTypes.length > 0) {
      const matchedCity = allCities.find(c => c.city.toLowerCase() === cityFilter.toLowerCase());
      trackEvent('filter_apply', {
        city: cityFilter || null,
        state: matchedCity ? matchedCity.state : null,
        event_data: {
          venue_search: venueFilter || null,
          artist_search: artistFilter || null,
          artist_types: activeArtistTypes,
          band_formats: activeBandFormats,
          mile_radius: mileRadius,
          results_count: gigs.length
        }
      });
    }
  }

  function clearFilters() {
    document.getElementById('searchVenue').value = '';
    document.getElementById('searchCity').value = '';
    document.getElementById('searchArtist').value = '';
    document.getElementById('mileRadius').value = '20';

    document.querySelectorAll('.artist-type-filter-toggle').forEach(btn => {
      btn.dataset.active = 'false';
      btn.style.background = 'rgba(255,255,255,0.05)';
      btn.style.border = '1px solid rgba(255,255,255,0.2)';
      btn.style.color = 'var(--text-muted)';
    });

    document.querySelectorAll('.band-format-filter-toggle').forEach(btn => {
      btn.dataset.active = 'false';
      btn.style.background = 'rgba(255,255,255,0.05)';
      btn.style.border = '1px solid rgba(255,255,255,0.2)';
      btn.style.color = 'var(--text-muted)';
    });

    const bandFormatDiv = document.getElementById('bandFormatBubblesArtist');
    if (bandFormatDiv) bandFormatDiv.style.display = 'none';

    gigs = [...allGigs];
    document.getElementById('searchResults').textContent = '';
    renderCalendar();
  }

  /* ---------------- EVENT LISTENERS ---------------- */

  // Calendar navigation
  prevBtn.addEventListener("click", () => {
    currentDate.setMonth(currentDate.getMonth() - 1);
    renderCalendar();
  });

  nextBtn.addEventListener("click", () => {
    currentDate.setMonth(currentDate.getMonth() + 1);
    renderCalendar();
  });

  // Search toggle
  document.getElementById('toggleSearchGigs').addEventListener('click', function(e) {
    e.preventDefault();
    const section = document.getElementById('searchGigsSection');
    const icon = document.getElementById('searchToggleIcon');
    
    if (section.style.display === 'none') {
      section.style.display = 'block';
      icon.textContent = '▼';
    } else {
      section.style.display = 'none';
      icon.textContent = '▶';
    }
  });

  // Filter buttons
  document.getElementById('applyFilters').addEventListener('click', applyFilters);
  document.getElementById('clearFilters').addEventListener('click', clearFilters);

  // ENTER KEY support for all filter inputs - COPIED from artist_book-gigs.js
  const filterInputs = ['searchVenue', 'searchCity', 'searchArtist', 'mileRadius'];
  filterInputs.forEach(id => {
    const input = document.getElementById(id);
    if (input) {
      input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          // Hide any autocomplete dropdowns
          document.getElementById('venueAutocomplete').style.display = 'none';
          var _cad = document.getElementById('cityAutocomplete'); if (_cad) _cad.style.display = 'none';
          document.getElementById('artistAutocomplete').style.display = 'none';
          // Blur the input so user knows it worked
          input.blur();
          applyFilters();
        }
      });
    }
  });

  // Artist type toggles - AUTO-APPLY on click
  document.querySelectorAll('.artist-type-filter-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const isActive = btn.dataset.active === 'true';
      btn.dataset.active = !isActive;
      
      if (!isActive) {
        btn.style.background = 'rgba(34, 197, 94, 0.2)';
        btn.style.border = '1px solid rgba(34, 197, 94, 0.5)';
        btn.style.color = '#22c55e';
      } else {
        btn.style.background = 'rgba(255,255,255,0.05)';
        btn.style.border = '1px solid rgba(255,255,255,0.2)';
        btn.style.color = 'var(--text-muted)';
      }

      const liveBandBtn = document.querySelector('.artist-type-filter-toggle[data-type="Live Band"]');
      const bandFormatDiv = document.getElementById('bandFormatBubblesArtist');
      if (liveBandBtn && bandFormatDiv) {
        bandFormatDiv.style.display = liveBandBtn.dataset.active === 'true' ? 'block' : 'none';
      }
      
      // Auto-apply filters
      applyFilters();
    });
  });

  // Band format toggles - AUTO-APPLY on click
  document.querySelectorAll('.band-format-filter-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const isActive = btn.dataset.active === 'true';
      btn.dataset.active = !isActive;
      
      if (!isActive) {
        btn.style.background = 'rgba(34, 197, 94, 0.2)';
        btn.style.border = '1px solid rgba(34, 197, 94, 0.5)';
        btn.style.color = '#22c55e';
      } else {
        btn.style.background = 'rgba(255,255,255,0.05)';
        btn.style.border = '1px solid rgba(255,255,255,0.2)';
        btn.style.color = 'var(--text-muted)';
      }
      
      // Auto-apply filters
      applyFilters();
    });
  });

  // VENUE AUTOCOMPLETE - COPIED from artist_book-gigs.js
  const venueInput = document.getElementById('searchVenue');
  const venueAutocompleteDiv = document.getElementById('venueAutocomplete');

  venueInput.addEventListener('input', (e) => {
    const value = e.target.value.toLowerCase().trim();
    
    if (!value) {
      venueAutocompleteDiv.style.display = 'none';
      return;
    }

    const matches = allVenues.filter(venue => 
      venue.toLowerCase().includes(value)
    );

    if (matches.length === 0) {
      venueAutocompleteDiv.style.display = 'none';
      return;
    }

    venueAutocompleteDiv.innerHTML = matches.map(venue => `
      <div style="
        padding: 10px 12px;
        cursor: pointer;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        transition: background 0.2s;
      "
      onmouseover="this.style.background='rgba(255,255,255,0.08)'"
      onmouseout="this.style.background='transparent'"
      onclick="selectVenue('${venue.replace(/'/g, "\\'")}')">
        ${esc(venue)}
      </div>
    `).join('');

    venueAutocompleteDiv.style.display = 'block';
  });

  // Global function to select venue from autocomplete
  window.selectVenue = function(venueName) {
    venueInput.value = venueName;
    venueAutocompleteDiv.style.display = 'none';
    applyFilters();
  };

  // Close venue autocomplete when clicking outside
  document.addEventListener('click', (e) => {
    if (!venueInput.contains(e.target) && !venueAutocompleteDiv.contains(e.target)) {
      venueAutocompleteDiv.style.display = 'none';
    }
  });

  // ARTIST AUTOCOMPLETE
  const artistInput = document.getElementById('searchArtist');
  const artistAutocompleteDiv = document.getElementById('artistAutocomplete');

  artistInput.addEventListener('input', (e) => {
    const value = e.target.value.toLowerCase().trim();
    
    if (!value) {
      artistAutocompleteDiv.style.display = 'none';
      return;
    }

    const matches = allArtists.filter(artist => 
      artist.toLowerCase().includes(value)
    );

    if (matches.length === 0) {
      artistAutocompleteDiv.style.display = 'none';
      return;
    }

    artistAutocompleteDiv.innerHTML = matches.map(artist => `
      <div style="
        padding: 10px 12px;
        cursor: pointer;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        transition: background 0.2s;
      "
      onmouseover="this.style.background='rgba(255,255,255,0.08)'"
      onmouseout="this.style.background='transparent'"
      onclick="selectArtist('${artist.replace(/'/g, "\\'")}')">
        ${esc(artist)}
      </div>
    `).join('');

    artistAutocompleteDiv.style.display = 'block';
  });

  // Global function to select artist from autocomplete
  window.selectArtist = function(artistName) {
    artistInput.value = artistName;
    artistAutocompleteDiv.style.display = 'none';
    applyFilters();
  };

  // Close artist autocomplete when clicking outside
  document.addEventListener('click', (e) => {
    if (!artistInput.contains(e.target) && !artistAutocompleteDiv.contains(e.target)) {
      artistAutocompleteDiv.style.display = 'none';
    }
  });

  // CITY AUTOCOMPLETE - COPIED from artist_book-gigs.js
  // City autocomplete — use shared module with validation + blocking overlay
  const cityInput = document.getElementById('searchCity');
  if (typeof initCityAutocomplete === 'function') {
    initCityAutocomplete({
      inputId: 'searchCity',
      onSelect: function(city, state, cityObj) {
        // Auto-apply filters after selecting a city from dropdown
        setTimeout(function() { applyFilters(); }, 50);
      }
    });
  }

  // Close city autocomplete on outside click (shared module handles this too)

  /* ---------------- INIT ---------------- */

  // Track page view
  trackEvent('page_view', { 
    event_data: { 
      page: 'public_gigs',
      has_city_param: !!cityParam
    } 
  });

  await loadCities();
  await loadGigs();
  
  if (cityParam) {
    cityInput.value = cityParam;
    
    // Track city search from URL parameter (came from index page)
    const matchedCity = allCities.find(c => c.city.toLowerCase() === cityParam.toLowerCase());
    trackEvent('city_search', {
      city: cityParam,
      state: matchedCity ? matchedCity.state : null,
      event_data: { source: 'url_param' }
    });
    
    applyFilters();
  }
  
  renderCalendar();
});

// Builds a Fabric canvas_data JSON string representing the built-in default flyer template,
// pre-hydrated with gig info so the existing canvas renderer can display it.
function _buildBuiltinFlyerJSON(gi) {
  var cW = 415, cH = 520;
  var s = cH / 1350;
  var W = cW * 0.94, L = cW * 0.03;
  var FONT = 'Trebuchet MS', GREY = 'rgba(200,200,210,0.9)';
  function ft(t) { if (!t) return ''; var pp = t.split(':').map(Number); return ((pp[0]%12)||12)+':'+String(pp[1]).padStart(2,'0')+(pp[0]>=12?'PM':'AM'); }
  var dStr = gi.date ? new Date(gi.date+'T00:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'}) : '';
  var tStr = gi.start_time ? ft(gi.start_time)+(gi.end_time?' - '+ft(gi.end_time):'') : '';
  var loc  = [gi.address_line_1, gi.city&&gi.state?gi.city+', '+gi.state:(gi.city||gi.state)].filter(Boolean).join('\n');
  var R = Math.round;
  var objs = [
    // Dark overlay
    {type:'rect', left:0, top:0, width:cW, height:cH, fill:'rgba(0,0,0,0.45)', selectable:false, evented:false},
    // Venue name
    {type:'textbox', left:L, top:R(cH*0.03), width:W, text:(gi.venue_name||'VENUE NAME').toUpperCase(),
      fontSize:R(120*s), fontFamily:FONT, fontWeight:'bold', fill:'#ffffff', textAlign:'center',
      stroke:'#000000', strokeWidth:R(2*s), _tplVar:'venue_name'}
  ];
  if (loc) objs.push({type:'textbox', left:L, top:R(cH*0.225), width:W, text:loc,
    fontSize:R(45*s), fontFamily:FONT, fontWeight:'bold', fill:GREY, textAlign:'center', lineHeight:1.15, _tplVar:'location'});
  objs.push({type:'textbox', left:L, top:R(cH*0.355), width:W, text:'\u2605  L I V E   M U S I C  \u2605',
    fontSize:R(72*s), fontFamily:FONT, fontWeight:'bold', fill:GREY, textAlign:'center', charSpacing:100});
  if (dStr) objs.push({type:'textbox', left:L, top:R(cH*0.44), width:W, text:dStr,
    fontSize:R(80*s), fontFamily:FONT, fontWeight:'bold', fill:'#ffffff', textAlign:'center',
    stroke:'#000000', strokeWidth:R(3*s), _tplVar:'date'});
  if (tStr) objs.push({type:'textbox', left:L, top:R(cH*0.53), width:W, text:tStr,
    fontSize:R(65*s), fontFamily:FONT, fontWeight:'bold', fill:GREY, textAlign:'center', _tplVar:'time'});
  // Artist area — tagged artist_logo so the canvas renderer swaps in the real photo
  objs.push({type:'textbox', left:L, top:R(cH*0.50), width:W,
    text: gi.artist_picture_url ? '' : (gi.artist_name||'').toUpperCase(),
    fontSize:R(100*s), fontFamily:FONT, fontWeight:'bold', fill:'#ffffff', textAlign:'center',
    stroke:'#000000', strokeWidth:R(3*s), _tplVar:'artist_logo'});
  return JSON.stringify({version:'5.3.1', objects:objs, background:'#0a0a14', width:cW, height:cH});
}

function showPublicFlyer(gigId) {
  _showFlyerOverlay((window._publicFlyerData||{})[gigId], 'flyerFullModal');
}

function _showFlyerOverlay(data, modalId) {
  var fm = document.getElementById(modalId);
  if (!fm) {
    fm = document.createElement('div');
    fm.id = modalId;
    fm.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:99999;align-items:center;justify-content:center;cursor:pointer;padding:20px;box-sizing:border-box;';
    fm.onclick = function(e) { if (e.target === fm) fm.style.display = 'none'; };
    document.body.appendChild(fm);
  }
  fm.style.display = 'flex';
  var mid = modalId;
  var closeBtn = '<button onclick="document.getElementById(\''+mid+'\').style.display=\'none\'" style="padding:9px 22px;background:rgba(255,255,255,0.08);color:#e2e8f0;border:1px solid rgba(255,255,255,0.2);border-radius:8px;font-size:0.85rem;font-weight:500;cursor:pointer;">&#10005; Close</button>';

  // Build descriptive filename from gig_info
  var gi0 = (data && data.gig_info) || {};
  // Hoist artist name from slots if not on root (multi-slot gigs)
  if (!gi0.artist_name && gi0.slots && gi0.slots.length) {
    var s0 = gi0.slots.find(function(s){return s.artist_name;}) || gi0.slots[0];
    if (s0) gi0.artist_name = s0.artist_name;
  }
  var _gigFileName = (function() {
    var v = (gi0.venue_name||'').replace(/[^a-zA-Z0-9]/g,'_').replace(/_+/g,'_').replace(/^_|_$/g,'');
    var d = gi0.date ? (function(){ var dt=new Date(gi0.date+'T00:00:00'); return dt.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}).replace(/[\s,]+/g,'_'); })() : '';
    function _ft(t){if(!t)return'';var p=t.split(':').map(Number);return((p[0]%12)||12)+'_'+String(p[1]).padStart(2,'0')+(p[0]>=12?'PM':'AM');}
    var t = gi0.start_time ? _ft(gi0.start_time)+(gi0.end_time?'-'+_ft(gi0.end_time):'') : '';
    var a = (gi0.artist_name||'').replace(/[^a-zA-Z0-9]/g,'_').replace(/_+/g,'_').replace(/^_|_$/g,'');
    return [v,d,t,a].filter(Boolean).join('_') || (data&&data.name) || 'event-flyer';
  })();


  if (data && data.thumbnail_data) {
    fm.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:12px;" onclick="event.stopPropagation()"><img src="' + data.thumbnail_data + '" style="max-height:520px;max-width:88vw;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,0.6);object-fit:contain;"><div style="display:flex;gap:12px;"><a href="' + data.thumbnail_data + '" download="' + _gigFileName + '.jpg" style="padding:9px 22px;background:rgba(139,92,246,0.85);color:#fff;border:1px solid rgba(139,92,246,0.6);border-radius:8px;font-size:0.85rem;font-weight:600;text-decoration:none;cursor:pointer;">&#11015; Download Flyer</a>' + closeBtn + '</div></div>';
    return;
  }

  if (data && data.canvas_data) {
    // Container for spinner — Fabric will place its wrapper div inside _pfMount
    fm.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:12px;" onclick="event.stopPropagation()"><div id="_pfMount" style="position:relative;border-radius:12px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,0.6);"><canvas id="_pfCanvas"></canvas><div id="_pfSpinner" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(10,10,20,0.9);"><span style="color:#c4b5fd;font-size:1rem;">Loading flyer...</span></div></div><div id="_pfBtnRow" style="display:flex;gap:12px;"><span id="_pfDlPlaceholder"></span>' + closeBtn + '</div></div>';

    function doRender() {
      try {
        var parsed = typeof data.canvas_data === 'string' ? JSON.parse(data.canvas_data) : data.canvas_data;

        // canvas JSON width/height ARE the display dimensions (e.g. 415x520 for instagram_post)
        var canvasW = parsed.width  || 415;
        var canvasH = parsed.height || 520;

        // Display at fixed 520px height (matching editor CANVAS_DISPLAY_HEIGHT),
        // preserving aspect ratio — same size regardless of thumbnail vs canvas path
        var aspect = canvasW / canvasH;
        var displayH = Math.min(520, Math.floor(window.innerHeight * 0.80));
        var displayW = Math.round(displayH * aspect);
        // If too wide, constrain to viewport width
        var maxW = Math.floor(window.innerWidth * 0.88);
        if (displayW > maxW) { displayW = maxW; displayH = Math.round(displayW / aspect); }

        var mount = document.getElementById('_pfMount');
        if (!mount) return;
        mount.style.width  = displayW + 'px';
        mount.style.height = displayH + 'px';

        // Create Fabric canvas at exact display size — no transforms needed
        var fc = new fabric.StaticCanvas('_pfCanvas', {
          width: displayW, height: displayH, renderOnAddRemove: false
        });

        // Gig info
        var gi = data.gig_info || {};
        if (!gi.artist_picture_url && gi.slots && gi.slots.length) {
          var sl = gi.slots.find(function(s) { return s.artist_id; }) || gi.slots[0];
          if (sl) { gi.artist_id = gi.artist_id || sl.artist_id; gi.artist_name = gi.artist_name || sl.artist_name; gi.artist_picture_url = gi.artist_picture_url || sl.artist_picture_url; }
        }

        function ft(t) { if (!t) return ''; var p = t.split(':').map(Number); return ((p[0]%12)||12)+':'+String(p[1]).padStart(2,'0')+(p[0]>=12?'PM':'AM'); }
        var dateStr = gi.date ? new Date(gi.date+'T00:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'}) : '';
        var timeStr = gi.start_time ? ft(gi.start_time)+(gi.end_time?' - '+ft(gi.end_time):'') : '';
        var loc = [gi.address_line_1, gi.city&&gi.state?gi.city+', '+gi.state:(gi.city||gi.state)].filter(Boolean).join('\n');

        // Use src->_tplVar map + reviver for reliable custom prop restoration
        var tplVarByIdx = {};
        var tplVarBySrc = {};
        (parsed.objects || []).forEach(function(o, i) {
          if (o._tplVar) { tplVarByIdx[i] = o._tplVar; if (o.src) tplVarBySrc[o.src] = o._tplVar; }
        });
        var reviver = function(jsonObj, fabricObj) {
          var tv = (jsonObj.src && tplVarBySrc[jsonObj.src]) || jsonObj._tplVar;
          if (tv) fabricObj._tplVar = tv;
          if (jsonObj._isZoneRect) fabricObj._isZoneRect = true;
        };

        fc.loadFromJSON(parsed, function() {
          var objs = fc.getObjects();
          objs.forEach(function(obj, i) { if (!obj._tplVar && tplVarByIdx[i]) obj._tplVar = tplVarByIdx[i]; });

          // Update text vars
          objs.forEach(function(obj) {
            var v = obj._tplVar;
            if (!v || obj.text === undefined) return;
            if      (v==='date'        && dateStr)       obj.set('text', dateStr);
            else if (v==='time'        && timeStr)       obj.set('text', timeStr);
            else if (v==='venue_name'  && gi.venue_name) obj.set('text', gi.venue_name.toUpperCase());
            else if (v==='location'    && loc)           obj.set('text', loc);
            else if (v==='artist_name' && gi.artist_name) obj.set('text', gi.artist_name.toUpperCase());
          });

          function finish() {
            fc.renderAll();
            var sp = document.getElementById('_pfSpinner'); if (sp) sp.style.display='none';
            var dlPh = document.getElementById('_pfDlPlaceholder');
            if (dlPh) {
              var dlUrl = fc.toDataURL({format:'jpeg', quality:0.92, multiplier: Math.round(Math.min(1400/canvasW, 2))});
              dlPh.outerHTML = '<a href="' + dlUrl + '" download="' + _gigFileName + '.jpg" style="padding:9px 22px;background:rgba(139,92,246,0.85);color:#fff;border:1px solid rgba(139,92,246,0.6);border-radius:8px;font-size:0.85rem;font-weight:600;text-decoration:none;cursor:pointer;">&#11015; Download Flyer</a>';
            }
          }

          // Count pending async image loads — call finish() only when all are done
          var pending = 0;
          function maybeFinish() { if (--pending <= 0) finish(); }

          // ── Venue Logo ──
          var vLogoImage = objs.find(function(o) { return o._tplVar==='venue_logo' && o.type==='image'; });
          var vLogoZone  = objs.find(function(o) { return o._tplVar==='venue_logo' && o._isZoneRect; });
          if (gi.venue_picture_url && (vLogoImage || vLogoZone)) {
            pending++;
            fabric.Image.fromURL(gi.venue_picture_url, function(img) {
              if (img) {
                var ref = vLogoImage || vLogoZone;
                if (vLogoImage) {
                  img.set({ left:vLogoImage.left, top:vLogoImage.top,
                    scaleX:vLogoImage.scaleX||1, scaleY:vLogoImage.scaleY||1, angle:vLogoImage.angle||0,
                    originX:vLogoImage.originX||'left', originY:vLogoImage.originY||'top',
                    shadow:vLogoImage.shadow, opacity:vLogoImage.opacity!=null?vLogoImage.opacity:1, _tplVar:'venue_logo' });
                } else {
                  var vzW = vLogoZone.width*(vLogoZone.scaleX||1), vzH = vLogoZone.height*(vLogoZone.scaleY||1);
                  var vsc = Math.min(vzW/img.width, vzH/img.height, 1);
                  img.set({ left:vLogoZone.left+vzW/2, top:vLogoZone.top+vzH/2,
                    scaleX:vsc, scaleY:vsc, originX:'center', originY:'center',
                    shadow:new fabric.Shadow({color:'rgba(0,0,0,0.8)',blur:20}), _tplVar:'venue_logo' });
                }
                fc.remove(ref); fc.add(img);
              }
              // Keep border on top
              fc.getObjects().filter(function(o){return o._isBorder;}).forEach(function(o){fc.bringToFront(o);});
              maybeFinish();
            });
          }

          // ── Artist Logo ──
          if (!gi.artist_picture_url) { if (!pending) finish(); return; }

          var logoImage = objs.find(function(o) { return o._tplVar==='artist_logo' && o.type==='image'; });
          var logoZone  = objs.find(function(o) { return o._tplVar==='artist_logo' && o._isZoneRect; });
          var logoText  = objs.find(function(o) { return o._tplVar==='artist_logo' && o.text!==undefined; });

          if (logoImage || logoZone || logoText) {
            pending++;
            fabric.Image.fromURL(gi.artist_picture_url, function(img) {
              if (img) {
                if (logoImage) {
                  img.set({ left:logoImage.left, top:logoImage.top,
                    scaleX:logoImage.scaleX||1, scaleY:logoImage.scaleY||1, angle:logoImage.angle||0,
                    originX:logoImage.originX||'left', originY:logoImage.originY||'top',
                    shadow:logoImage.shadow, opacity:logoImage.opacity!=null?logoImage.opacity:1, _tplVar:'artist_logo' });
                  fc.remove(logoImage); fc.add(img);
                  fc.getObjects().filter(function(o){return o._isBorder;}).forEach(function(o){fc.bringToFront(o);});
                } else if (logoZone) {
                  var zoneW = logoZone.width*(logoZone.scaleX||1), zoneH = logoZone.height*(logoZone.scaleY||1);
                  var sc = Math.min(zoneW/img.width, zoneH/img.height, 1);
                  img.set({ left:logoZone.left+zoneW/2, top:logoZone.top+zoneH/2,
                    scaleX:sc, scaleY:sc, originX:'center', originY:'center',
                    shadow:new fabric.Shadow({color:'rgba(0,0,0,0.8)',blur:20}), _tplVar:'artist_logo' });
                  fc.remove(logoZone); fc.add(img);
                  fc.getObjects().filter(function(o){return o._isBorder;}).forEach(function(o){fc.bringToFront(o);});
                } else {
                  var tw = canvasW*0.94, th = canvasH-logoText.top-canvasH*0.03;
                  var s2 = Math.min(tw/img.width, th/img.height, 1);
                  img.set({ left:canvasW/2, top:logoText.top+th/2, scaleX:s2, scaleY:s2,
                    originX:'center', originY:'center', shadow:logoText.shadow, _tplVar:'artist_logo' });
                  fc.remove(logoText); fc.add(img);
                  fc.getObjects().filter(function(o){return o._isBorder;}).forEach(function(o){fc.bringToFront(o);});
                }
                objs.forEach(function(o) { if (o._tplVar==='artist_name') o.visible = false; });
              }
              maybeFinish();
            });
          }

          if (!pending) finish();
        }, reviver);
      } catch(e) {
        console.error('Flyer render error:', e);
        var sp = document.getElementById('_pfSpinner'); if (sp) sp.style.display='none';
            var dlPh = document.getElementById('_pfDlPlaceholder');
            if (dlPh) {
              var dlUrl = fc.toDataURL({format:'jpeg', quality:0.92, multiplier: Math.round((window.devicePixelRatio||1) * Math.min(1400/canvasW, 2))});
              dlPh.outerHTML = '<a href="' + dlUrl + '" download="' + _gigFileName + '.jpg" style="padding:9px 22px;background:rgba(139,92,246,0.85);color:#fff;border:1px solid rgba(139,92,246,0.6);border-radius:8px;font-size:0.85rem;font-weight:600;text-decoration:none;cursor:pointer;">&#11015; Download Flyer</a>';
            }
      }
    }

    if (typeof fabric !== 'undefined') { doRender(); }
    else { var s=document.createElement('script'); s.src='https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.3.1/fabric.min.js'; s.onload=doRender; document.head.appendChild(s); }
    return;
  }

  // use_builtin: build the default template layout client-side from gig_info, then render via canvas path
  if (data && data.use_builtin && data.gig_info) {
    var gi = data.gig_info || {};
    // Multi-slot hoist
    if (!gi.artist_picture_url && gi.slots && gi.slots.length) {
      var sl = gi.slots.find(function(s){return s.artist_id;}) || gi.slots[0];
      if (sl) { gi.artist_name = gi.artist_name || sl.artist_name; gi.artist_picture_url = gi.artist_picture_url || sl.artist_picture_url; }
    }
    _showFlyerOverlay({ canvas_data: _buildBuiltinFlyerJSON(gi), gig_info: gi }, modalId);
    return;
  }

  fm.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:16px;text-align:center;" onclick="event.stopPropagation()"><div style="font-size:3rem;">&#127912;</div><div style="color:#e2e8f0;font-size:1.1rem;font-weight:600;">Flyer Coming Soon</div><div style="color:#94a3b8;font-size:0.85rem;">The venue is preparing an event flyer for this gig.</div>' + closeBtn + '</div>';
}
