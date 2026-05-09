// Auto-extracted from venue-profile.html inline scripts
// Generated for CSP compliance (Phase 5)

const TAB_NAMES = ['info','calendar','videos','pictures','social','reviews'];
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.profile-tabs button').forEach(b => b.classList.remove('active'));
  const panel = document.getElementById('tab-' + name);
  if (panel) panel.classList.add('active');
  // Use ID-based lookup so hidden buttons don't break index-based selection
  const btn = document.getElementById('tabBtn' + name.charAt(0).toUpperCase() + name.slice(1));
  if (btn) btn.classList.add('active');
  // Lazy-load reviews on first open
  if (name === 'reviews' && !window._venueReviewsLoaded) {
    window._venueReviewsLoaded = true;
    if (typeof renderVenueRatingSummary === 'function') {
      renderVenueRatingSummary('venueRatingSummaryContainer', venueId);
      renderVenueReviews('venueReviewsListContainer', venueId);
    }
  }
}

let venueData = null, venueGigs = [], calYear, calMonth;
let venueId = new URLSearchParams(window.location.search).get("venue_id");

(async function loadVenue() {
  if (!venueId) { document.querySelector('.profile-container').innerHTML = '<p style="text-align:center;color:var(--text-gray);padding:40px;">Missing venue_id parameter.</p>'; return; }

  const res = await fetch(`/api/venues/${venueId}/public`, { credentials: "include" });
  if (!res.ok) { document.querySelector('.profile-container').innerHTML = '<p style="text-align:center;color:var(--text-gray);padding:40px;">Venue not found.</p>'; return; }
  venueData = await res.json();

  // Hero
  document.getElementById("venueName").textContent = venueData.venue_name || "";
  const addr = [venueData.address_line_1, venueData.address_line_2, venueData.city, venueData.state, venueData.postal_code].filter(Boolean);
  document.getElementById("venueAddress").textContent = addr.join(", ");
  document.title = (venueData.venue_name || "Venue") + " – GigsFill";
  // Dynamic SEO updates
  const seoDesc = `${venueData.venue_name || 'Venue'}${venueData.city ? ' in ' + venueData.city + ', ' + venueData.state : ''} – live music venue on GigsFill. View upcoming events, amenities, and booking info.`;
  document.querySelector('meta[name="description"]')?.setAttribute('content', seoDesc);
  document.querySelector('meta[property="og:title"]')?.setAttribute('content', document.title);
  document.querySelector('meta[property="og:description"]')?.setAttribute('content', seoDesc);
  document.querySelector('meta[name="twitter:title"]')?.setAttribute('content', document.title);
  document.querySelector('meta[name="twitter:description"]')?.setAttribute('content', seoDesc);

  // Bio
  document.getElementById("venueBio").textContent = (venueData.description || "").trim();
  // Show about text in header
  const _aboutEl = document.getElementById("venueAboutHeader");
  if (_aboutEl && venueData.description) _aboutEl.textContent = venueData.description.trim();

  // Check if logged-in user is an artist → show Venue Info tab
  try {
    const _meRes = await fetch('/api/me', { credentials: 'include' });
    if (_meRes.ok) {
      const _me = await _meRes.json();
      // /api/me returns artists array — if non-empty, user is an artist
      if (_me.artists && _me.artists.length > 0) {
        const _infoBtn = document.getElementById('tabBtnInfo');
        if (_infoBtn) _infoBtn.style.display = '';
      }
    }
  } catch(_e) {}

  // Details
  const dh = [];
  dh.push(`<div class="detail-item"><div class="label">Capacity</div><div class="value">${venueData.venue_size || "No Info Provided"}</div></div>`);
  let freq = "No Limit";
  if (venueData.artist_frequency_days && venueData.artist_frequency_days > 0) freq = `1 performance every ${venueData.artist_frequency_days} days`;
  dh.push(`<div class="detail-item"><div class="label">Artist Frequency</div><div class="value">${freq}</div></div>`);
  let pay = "No Info Provided";
  if (venueData.default_pay_dollars !== null && venueData.default_pay_dollars !== undefined) {
    pay = `$${venueData.default_pay_dollars || 0}.${String(venueData.default_pay_cents || 0).padStart(2, "0")}`;
  }
  dh.push(`<div class="detail-item"><div class="label">Default Pay</div><div class="value">${pay}</div></div>`);
  let arrival = "Flexible";
  if (venueData.arrival_time_type === "no_earlier_than" && venueData.arrival_no_earlier_than_hour) {
    arrival = `No earlier than ${venueData.arrival_no_earlier_than_hour} ${venueData.arrival_no_earlier_than_period || ""}`;
  }
  const loadIn = venueData.load_in_out_details || "No Info Provided";
  dh.push(`<div class="detail-item"><div class="label">Arrival</div><div class="value">${arrival}<div class="detail">${loadIn}</div></div></div>`);
  document.getElementById("venueDetails").innerHTML = dh.join("");

  // Perks
  const ph = [];
  ph.push(`<div class="detail-item"><div class="label">Bar Tab</div><div class="value">${venueData.bar_tab_details || "No Info Provided"}</div></div>`);
  ph.push(`<div class="detail-item"><div class="label">Food Tab</div><div class="value">${venueData.food_tab_details || "No Info Provided"}</div></div>`);
  let stage = "No";
  if (venueData.has_stage) { stage = "Yes"; const dims = []; if (venueData.stage_width_ft) dims.push(`${venueData.stage_width_ft}' wide`); if (venueData.stage_depth_ft) dims.push(`${venueData.stage_depth_ft}' deep`); if (dims.length) stage += ` (${dims.join(" × ")})`; }
  ph.push(`<div class="detail-item"><div class="label">Stage</div><div class="value">${stage}${venueData.has_stage && venueData.setup_location_description ? `<div class="detail">${venueData.setup_location_description}</div>` : ""}</div></div>`);
  ph.push(`<div class="detail-item"><div class="label">Sound Equipment</div><div class="value">${venueData.has_sound_equipment ? "Yes" : "No"}${venueData.has_sound_equipment && venueData.sound_equipment_description ? `<div class="detail">${venueData.sound_equipment_description}</div>` : ""}</div></div>`);
  ph.push(`<div class="detail-item"><div class="label">Lighting</div><div class="value">${venueData.has_lighting ? "Yes" : "No"}${venueData.has_lighting && venueData.lighting_description ? `<div class="detail">${venueData.lighting_description}</div>` : ""}</div></div>`);
  ph.push(`<div class="detail-item"><div class="label">Sound Engineer</div><div class="value">${venueData.has_sound_engineer ? "Yes" : "No"}${venueData.has_sound_engineer && venueData.sound_engineer_details ? `<div class="detail">${venueData.sound_engineer_details}</div>` : ""}</div></div>`);
  document.getElementById("venuePerks").innerHTML = ph.join("");

  // PRO Certification
  if (venueData.pro_certified) {
    const vn = venueData.venue_name || 'This venue';
    document.getElementById("proCertText").textContent = `${vn} represents and warrants that this venue maintains active public performance licenses from all applicable Performing Rights Organizations ("PROs"), including but not limited to ASCAP, BMI, SESAC, GMR, and any other organization required for the lawful public performance of musical works at this venue. ${vn} understands that maintaining such licenses is solely the responsibility of the venue.`;
    document.getElementById("proCertSection").style.display = "";
  }

  // Social
  const ensureProto = u => { if (!u) return ''; const t = u.trim(); return (t.startsWith('http://') || t.startsWith('https://')) ? t : 'https://' + t; };
  const links = [
    { url: venueData.website_url, label: "Website", icon: "🌐" },
    { url: venueData.facebook_url, label: "Facebook", icon: "👥" },
    { url: venueData.instagram_url, label: "Instagram", icon: "📷" },
    { url: venueData.twitter_url, label: "Twitter/X", icon: "🐦" },
    { url: venueData.yelp_url, label: "Yelp", icon: "⭐" },
    { url: venueData.google_maps_url, label: "Google Maps", icon: "📍" }
  ].filter(l => l.url && l.url.trim());
  if (links.length > 0) {
    document.getElementById("socialMedia").innerHTML = links.map(l => `<a href="${escAttr(ensureProto(l.url))}" target="_blank" rel="noopener noreferrer" class="social-link"><span>${esc(l.icon)}</span><span>${esc(l.label)}</span></a>`).join('');
  } else { document.getElementById("socialEmpty").style.display = "block"; }

  document.getElementById("venueLogo").src = "/app/static/img/profile-placeholder.svg";
  loadMedia();
  loadGigs();
})();

// ========== CALENDAR ==========
async function loadGigs() {
  try { const r = await fetch(`/venues/${venueId}/gigs`); if (r.ok) venueGigs = await r.json(); } catch(e) {}
  const now = new Date(); calYear = now.getFullYear(); calMonth = now.getMonth();
  renderCalendar();
}
function changeMonth(d) { calMonth += d; if (calMonth > 11) { calMonth = 0; calYear++; } if (calMonth < 0) { calMonth = 11; calYear--; } renderCalendar(); }
function renderCalendar() {
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('calMonth').textContent = `${months[calMonth]} ${calYear}`;
  const firstDay = new Date(calYear, calMonth, 1).getDay();
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  const _now = new Date();
  const todayStr = `${_now.getFullYear()}-${String(_now.getMonth()+1).padStart(2,'0')}-${String(_now.getDate()).padStart(2,'0')}`;
  const icons = { 'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤', 'Trivia Host': '🧠' };
  let html = '';
  for (let i = 0; i < firstDay; i++) html += '<div class="cal-day empty"></div>';
  for (let d = 1; d <= daysInMonth; d++) {
    const ds = `${calYear}-${String(calMonth+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const isToday = ds === todayStr;
    const isPast = ds < todayStr;
    const dayGigs = venueGigs.filter(g => g.date === ds);
    html += `<div class="cal-day${isToday ? ' today' : ''}${isPast ? ' past' : ''}"><div class="day-num">${d}</div>`;
    if (dayGigs.length > 0) {
      html += '<div class="cal-gigs-container">';
      let gigIdx = 0;
      dayGigs.forEach((g) => {
        if (g.slots && g.slots.length > 0) {
          // Expand multi-slot gigs into individual slot bubbles
          g.slots.slice().sort((a,b) => (a.start_time||'').localeCompare(b.start_time||'')).forEach((slot) => {
            const isBooked = slot.status === 'booked';
            const cls = isBooked ? 'booked' : 'open';
            const t = fmtTime(slot.start_time);
            const icon = (icons[g.artist_type] || '🎵') + ' ';
            const label = isBooked ? `${icon}${t} · ${slot.artist_name || 'Booked'}` : `${icon}${t} · Open`;
            html += `<div class="cal-gig ${cls}" onclick="showGigDetail('${ds}',${gigIdx})" title="${esc(label)}">${esc(label)}</div>`;
          });
        } else {
          // Single gig or non-multi-slot
          const cls = g.status === 'booked' ? 'booked' : 'open';
          const t = fmtTime(g.start_time);
          const icon = (icons[g.artist_type] || '🎵') + ' ';
          const label = g.status === 'booked' && g.artist_name ? `${icon}${t} · ${g.artist_name}` : `${icon}${t} · ${g.title || 'Open'}`;
          html += `<div class="cal-gig ${cls}" onclick="showGigDetail('${ds}',${gigIdx})" title="${esc(label)}">${esc(label)}</div>`;
        }
        gigIdx++;
      });
      html += '</div>';
    }
    html += '</div>';
  }
  document.getElementById('calGrid').innerHTML = html;
}
function fmtTime(t) { if (!t) return ''; const [h,m] = t.split(':').map(Number); return ((h%12)||12)+':'+String(m).padStart(2,'0')+(h>=12?'PM':'AM'); }
function fmtDate(d) { if (!d) return ''; const [y,mo,dy] = d.split('-'); const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']; return `${months[parseInt(mo)-1]} ${parseInt(dy)}, ${y}`; }

async function showGigDetail(date, idx) {
  const dayGigs = venueGigs.filter(g => g.date === date);
  const g = dayGigs[idx]; if (!g) return;
  const timeStr = fmtTime(g.start_time) + (g.end_time ? ' – ' + fmtTime(g.end_time) : '');
  const loc = [venueData?.city, venueData?.state].filter(Boolean).join(', ');
  const artType = g.artist_actual_type || g.artist_type || '';
  let formats = g.artist_band_formats || g.band_formats || '';
  if (formats) formats = formats.replace(/,/g, ', ');

  const isMultiSlot = g.total_slots_count > 0;
  const addr1 = venueData?.address_line_1 || '';
  const addr2 = venueData?.address_line_2 || '';
  const city = venueData?.city || '';
  const state = venueData?.state || '';
  let locHtml = '';
  if (addr1) locHtml += esc(addr1) + '<br>';
  if (addr2) locHtml += esc(addr2) + '<br>';
  if (city || state) locHtml += esc([city, state].filter(Boolean).join(', '));

  let rows = '';
  rows += modalRow('Date', fmtDate(g.date));
  if (!isMultiSlot) rows += modalRow('Time', timeStr || 'TBD');
  rows += modalRow('Venue', g.venue_name || venueData?.venue_name || '');
  if (locHtml) rows += modalRowRaw('Location', locHtml);
  if (!isMultiSlot && g.status === 'booked' && g.artist_name) {
    const artistLink = g.artist_id ? `<a href="/app/artist-profile.html?artist_id=${g.artist_id}" target="_blank">${esc(g.artist_name)}</a>` : esc(g.artist_name);
    rows += modalRowRaw('Artist', artistLink);
  }
  if (!isMultiSlot && g.status === 'open') rows += modalRow('Status', 'Open – Looking for artist');
  if (artType) rows += modalRow('Artist Type', artType);
  if (artType === 'Live Band' && formats) rows += modalRow('Lineup', formats);
  let gStyles = g.styles || '';
  if (artType === 'Live Band' && gStyles) rows += modalRow('Styles', gStyles.replace(/,/g, ', '));
  
  // For multi-slot gigs, fetch and display slot details
  if (isMultiSlot) {
    try {
      const slotsRes = await fetch(`/api/gigs/${g.id}/slots`);
      if (slotsRes.ok) {
        const slots = (await slotsRes.json()).sort((a,b) => (a.start_time||'').localeCompare(b.start_time||''));
        slots.forEach(s => {
          const isBooked = s.status === 'booked';
          const color = isBooked ? '#ef4444' : '#22c55e';
          const bg = isBooked ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)';
          const slotLabel = isBooked 
            ? `<a href="/app/artist-profile.html?artist_id=${s.artist_id}" target="_blank" style="color:${color}; text-decoration:none; font-weight:600;">${esc(s.artist_name || 'Booked')}</a>`
            : '<span style="color:#22c55e; font-weight:600;">Open</span>';
          rows += `<div style="display:flex; align-items:center; padding:9px 14px; margin-bottom:6px; background:${bg}; border:1px solid ${color}33; border-radius:6px; font-size:0.85rem; color:var(--text);">
            <span style="font-weight:700; min-width:56px;">Slot ${s.slot_number}</span>
            <span style="min-width:160px;">${fmtTime(s.start_time)} – ${fmtTime(s.end_time)}</span>
            <span style="margin-left:auto; text-align:right;">${slotLabel}</span>
          </div>`;
        });
      }
    } catch(e) { console.error('Error loading slots:', e); }
  }
  
  document.getElementById('gigModalBody').innerHTML = rows;
  document.getElementById('gigModal').classList.remove('hidden');
  // Show flyer button if gig is booked and venue has a flyer
  const gigIsBooked = g.status === 'booked' || g.booked_slots_count > 0;
  if (gigIsBooked) {
    fetch(`/api/gigs/${g.id}/flyer/public`).then(r=>r.json()).then(data=>{
      if (!data.exists) return;
      const modalBody = document.getElementById('gigModalBody');
      if (!modalBody) return;
      const fd = document.createElement('div');
      fd.style.cssText = 'margin-top:16px;text-align:center;';
      fd.innerHTML = `<button onclick="showProfileFlyer(${g.id})" style="padding:8px 20px;border:1px solid rgba(139,92,246,0.4);border-radius:8px;background:rgba(139,92,246,0.15);color:#c4b5fd;cursor:pointer;font-size:0.85rem;font-weight:600;">🎨 View Event Flyer</button>`;
      modalBody.appendChild(fd);
      window._profileFlyerData = window._profileFlyerData || {};
      window._profileFlyerData[g.id] = data;
    }).catch(()=>{});
  }
}
function modalRow(label, value) { return `<div class="gig-modal-row"><span class="gig-modal-label">${label}</span><span class="gig-modal-value">${esc(value)}</span></div>`; }
function modalRowRaw(label, html) { return `<div class="gig-modal-row"><span class="gig-modal-label">${label}</span><span class="gig-modal-value">${html}</span></div>`; }
function closeGigModal() { document.getElementById('gigModal').classList.add('hidden'); }
document.getElementById('gigModal').addEventListener('click', function(e) { if (e.target === this) closeGigModal(); });

// ========== MEDIA ==========
function ytThumb(url) { const m = url.match(/(?:youtube\.com.*v=|youtu\.be\/)([^&]+)/); return m ? `https://img.youtube.com/vi/${m[1]}/hqdefault.jpg` : "/app/static/img/video-placeholder.svg"; }
async function loadMedia() {
  const res = await fetch(`/api/venues/${venueId}/media`, { credentials: "include" }); if (!res.ok) return;
  const items = await res.json();
  const pics = document.getElementById("pictures"), videos = document.getElementById("videos");
  pics.innerHTML = ""; videos.innerHTML = "";
  items.forEach(m => {
    if (m.media_type === "profile" || m.media_type === "logo") document.getElementById("venueLogo").src = m.file_path;
    if (m.media_type === "picture") { const d = document.createElement("div"); d.className = "media-card"; d.innerHTML = `<img src="${escAttr(m.file_path)}"><div class="media-title-label">${esc(m.title||"")}</div>`; d.onclick = () => openModal(m.file_path, "image"); pics.appendChild(d); }
    if (m.media_type === "video") { const d = document.createElement("div"); d.className = "media-card"; d.innerHTML = `<img src="${escAttr(ytThumb(m.video_url))}"><div class="media-title-label">${esc(m.title||"")}</div>`; d.onclick = () => openModal(m.video_url, "video"); videos.appendChild(d); }
  });
  if (!pics.children.length) document.getElementById('picturesEmpty').style.display = 'block';
  if (!videos.children.length) document.getElementById('videosEmpty').style.display = 'block';
}
function openModal(src, type) {
  const modal = document.getElementById("mediaModal"), content = document.getElementById("mediaModalContent");
  if (type === "image") content.innerHTML = `<img src="${escAttr(src)}" style="max-width:90vw;max-height:90vh;border-radius:12px;">`;
  else { const yt = src.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&]+)/); content.innerHTML = yt ? `<iframe width="800" height="450" src="https://www.youtube.com/embed/${escAttr(yt[1])}" frameborder="0" allow="accelerometer;autoplay;clipboard-write;encrypted-media;gyroscope;picture-in-picture" allowfullscreen style="border-radius:12px;"></iframe>` : `<video controls src="${escAttr(src)}" style="max-width:90vw;max-height:90vh;border-radius:12px;"></video>`; }
  modal.classList.remove("hidden"); modal.style.display = "flex";
  modal.onclick = e => { if (e.target === modal) { modal.classList.add("hidden"); modal.style.display = "none"; content.innerHTML = ""; } };
}
// esc() provided by security.js
  



function showProfileFlyer(gigId) {
  const data = (window._profileFlyerData || {})[gigId];
  let fm = document.getElementById('profileFlyerModal');
  if (!fm) {
    fm = document.createElement('div');
    fm.id = 'profileFlyerModal';
    fm.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:99999;align-items:center;justify-content:center;cursor:pointer;padding:20px;';
    fm.onclick = (e) => { if (e.target === fm) fm.style.display = 'none'; };
    document.body.appendChild(fm);
  }
  if (data && data.thumbnail_data) {
    fm.innerHTML = `<div style="position:relative;display:flex;flex-direction:column;align-items:center;gap:12px;" onclick="event.stopPropagation()">
      <img src="${data.thumbnail_data}" style="max-height:80vh;max-width:88vw;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,0.6);object-fit:contain;">
      <div style="display:flex;gap:12px;">
        <a href="${data.thumbnail_data}" download="${_gigFileName}.jpg" style="padding:9px 22px;background:rgba(139,92,246,0.85);color:#fff;border:1px solid rgba(139,92,246,0.6);border-radius:8px;font-size:0.85rem;font-weight:600;text-decoration:none;cursor:pointer;">⬇ Download Flyer</a>
        <button onclick="document.getElementById('profileFlyerModal').style.display='none'" style="padding:8px 24px;background:rgba(255,255,255,0.15);color:#fff;border:1px solid rgba(255,255,255,0.3);border-radius:8px;font-size:0.85rem;cursor:pointer;">✕ Close</button>
      </div>
    </div>`;
  } else {
    fm.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:16px;text-align:center;" onclick="event.stopPropagation()">
      <div style="font-size:3rem;">🎨</div>
      <div style="color:#e2e8f0;font-size:1.1rem;font-weight:600;">Flyer Coming Soon</div>
      <div style="color:#94a3b8;font-size:0.85rem;">The venue is preparing an event flyer for this gig.</div>
      <button onclick="document.getElementById('profileFlyerModal').style.display='none'" style="padding:8px 24px;background:rgba(255,255,255,0.15);color:#fff;border:1px solid rgba(255,255,255,0.3);border-radius:8px;font-size:0.85rem;cursor:pointer;margin-top:8px;">Close</button>
    </div>`;
  }
  fm.style.display = 'flex';
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
  var closeBtn = '<button onclick="document.getElementById(\'' + mid + '\').style.display=\'none\'" style="padding:9px 22px;background:rgba(255,255,255,0.08);color:#e2e8f0;border:1px solid rgba(255,255,255,0.2);border-radius:8px;font-size:0.85rem;font-weight:500;cursor:pointer;">&#10005; Close</button>';

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
    fm.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:12px;" onclick="event.stopPropagation()"><div id="_pfMount" style="position:relative;border-radius:12px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,0.6);"><canvas id="_pfCanvas"></canvas><div id="_pfSpinner" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(10,10,20,0.9);"><span style="color:#c4b5fd;font-size:1rem;">Loading flyer...</span></div></div><div id="_pfBtnRow" style="display:flex;gap:12px;"><span id="_pfDlPlaceholder"></span>' + closeBtn + '</div></div>';
    function doRender() {
      try {
        var parsed = typeof data.canvas_data === 'string' ? JSON.parse(data.canvas_data) : data.canvas_data;
        var canvasW = parsed.width  || 415;
        var canvasH = parsed.height || 520;
                        var aspect = canvasW / canvasH;
        var displayH = Math.min(520, Math.floor(window.innerHeight * 0.80));
        var displayW = Math.round(displayH * aspect);
        var maxW = Math.floor(window.innerWidth * 0.88);
        if (displayW > maxW) { displayW = maxW; displayH = Math.round(displayW / aspect); }
        var mount = document.getElementById('_pfMount');
        if (!mount) return;
        mount.style.width  = displayW + 'px';
        mount.style.height = displayH + 'px';
        var fc = new fabric.StaticCanvas('_pfCanvas', {width: displayW, height: displayH, renderOnAddRemove: false});

        var gi = data.gig_info || {};
        if (!gi.artist_picture_url && gi.slots && gi.slots.length) {
          var sl = gi.slots.find(function(s) { return s.artist_id; }) || gi.slots[0];
          if (sl) { gi.artist_id = gi.artist_id || sl.artist_id; gi.artist_name = gi.artist_name || sl.artist_name; gi.artist_picture_url = gi.artist_picture_url || sl.artist_picture_url; }
        }
        function ft(t) { if (!t) return ''; var p = t.split(':').map(Number); return ((p[0]%12)||12)+':'+String(p[1]).padStart(2,'0')+(p[0]>=12?'PM':'AM'); }
        var dateStr = gi.date ? new Date(gi.date+'T00:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'}) : '';
        var timeStr = gi.start_time ? ft(gi.start_time)+(gi.end_time?' - '+ft(gi.end_time):'') : '';
        var loc = [gi.address_line_1, gi.city&&gi.state?gi.city+', '+gi.state:(gi.city||gi.state)].filter(Boolean).join('\n');
        var tplVars = (parsed.objects || []).map(function(o) { return o._tplVar || null; });
        fc.loadFromJSON(parsed, function() {
          var objs = fc.getObjects();
          for (var i = 0; i < objs.length && i < tplVars.length; i++) { if (tplVars[i]) objs[i]._tplVar = tplVars[i]; }
          objs.forEach(function(obj) {
            var v = obj._tplVar;
            if (!v || obj.text === undefined) return;
            if      (v==='date'       && dateStr)       obj.set('text', dateStr);
            else if (v==='time'       && timeStr)       obj.set('text', timeStr);
            else if (v==='venue_name' && gi.venue_name) obj.set('text', gi.venue_name.toUpperCase());
            else if (v==='location'   && loc)           obj.set('text', loc);
            else if ((v==='artist_name'||v==='artist_logo') && gi.artist_name) obj.set('text', gi.artist_name.toUpperCase());
          });
          var ref = objs.find(function(o) { return o._tplVar==='artist_logo'; });
          function finish() {
            fc.renderAll();
            var sp = document.getElementById('_pfSpinner'); if (sp) sp.style.display='none';
            var dlPh = document.getElementById('_pfDlPlaceholder');
            if (dlPh) {
              var dlUrl = fc.toDataURL({format:'jpeg', quality:0.92, multiplier: Math.round(Math.min(1400/canvasW, 2))});
              dlPh.outerHTML = '<a href=\"' + dlUrl + '\" download=\"' + _gigFileName + '.jpg\" style=\"padding:9px 22px;background:rgba(139,92,246,0.85);color:#fff;border:1px solid rgba(139,92,246,0.6);border-radius:8px;font-size:0.85rem;font-weight:600;text-decoration:none;cursor:pointer;\">&#11015; Download Flyer</a>';
            }
          }
          if (gi.artist_picture_url && ref) {
            fabric.Image.fromURL(gi.artist_picture_url, function(img) {
              if (img) {
                var tw=(ref.width||canvasW*0.75)*(ref.scaleX||1), th=(ref.height||canvasH*0.25)*(ref.scaleY||1);
                var s2=Math.min(tw/img.width,th/img.height,1);
                img.set({left:ref.left,top:ref.top,scaleX:s2,scaleY:s2,originX:ref.originX||'left',originY:ref.originY||'top',shadow:ref.shadow});
                fc.remove(ref); fc.add(img);
              }
              finish();
            });
          } else { finish(); }
        });
      } catch(e) { console.error('Flyer render error:',e); var sp=document.getElementById('_pfSpinner'); if(sp)sp.style.display='none'; }
    }
    if (typeof fabric !== 'undefined') { doRender(); }
    else { var s=document.createElement('script'); s.src='https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.3.1/fabric.min.js'; s.onload=doRender; document.head.appendChild(s); }
    return;
  }

  fm.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:16px;text-align:center;" onclick="event.stopPropagation()"><div style="font-size:3rem;">&#127912;</div><div style="color:#e2e8f0;font-size:1.1rem;font-weight:600;">Flyer Coming Soon</div><div style="color:#94a3b8;font-size:0.85rem;">The venue is preparing an event flyer for this gig.</div>' + closeBtn + '</div>';
}

// ── Venue Reviews ────────────────────────────────────────────────────────────

async function loadVenueReviews(page) {
  if (!venueId) return;
  const list = document.getElementById('venueReviewsList');
  const pagination = document.getElementById('venueReviewsPagination');
  const summaryContainer = document.getElementById('venueRatingSummaryContainer');
  if (!list) return;

  try {
    const [summaryRes, reviewsRes] = await Promise.all([
      fetch(`/api/venues/${venueId}/reviews/summary`, { credentials: 'include' }),
      fetch(`/api/venues/${venueId}/reviews?page=${page}&limit=5`, { credentials: 'include' })
    ]);

    // ── Rating summary (matches artist-profile style) ──
    if (summaryRes.ok && summaryContainer) {
      const s = await summaryRes.json();
      const badge = document.getElementById('reviewsBadge');
      if (s.review_count > 0) {
        if (badge) badge.textContent = `(${s.review_count})`;
        const avg = parseFloat(s.avg_rating) || 0;
        const total = s.review_count;
        const breakdown = [5, 4, 3, 2, 1].map(n => ({
          stars: n,
          count: s[['', 'one', 'two', 'three', 'four', 'five'][n] + '_star'] || 0
        }));
        // Build filled/empty stars like artist-reviews.js renderStars()
        function starHtml(rating, size) {
          let out = '';
          for (let i = 1; i <= 5; i++) {
            const col = rating >= i ? '#f59e0b' : (rating >= i - 0.5 ? '#f59e0b' : '#d1d5db');
            out += `<span style="color:${col};font-size:${size}px;">★</span>`;
          }
          return out;
        }
        summaryContainer.innerHTML = `
          <div style="display:flex;align-items:flex-start;gap:24px;flex-wrap:wrap;">
            <div style="text-align:center;min-width:80px;">
              <div style="font-size:2.5rem;font-weight:800;color:var(--text);line-height:1;">${avg.toFixed(1)}</div>
              <div style="margin:4px 0;">${starHtml(avg, 18)}</div>
              <div style="font-size:0.7rem;color:var(--text-gray);">${total} review${total !== 1 ? 's' : ''}</div>
            </div>
            <div style="flex:1;min-width:160px;">
              ${breakdown.map(({ stars, count }) => {
                const pct = total > 0 ? Math.round((count / total) * 100) : 0;
                return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                  <span style="font-size:0.72rem;color:var(--text-gray);width:32px;text-align:right;">${stars}★</span>
                  <div style="flex:1;height:8px;background:var(--border);border-radius:4px;overflow:hidden;">
                    <div style="width:${pct}%;height:100%;background:#f59e0b;border-radius:4px;transition:width .3s;"></div>
                  </div>
                  <span style="font-size:0.72rem;color:var(--text-gray);width:24px;">${count}</span>
                </div>`;
              }).join('')}
            </div>
          </div>`;
      } else {
        if (badge) badge.textContent = '';
        if (summaryContainer) summaryContainer.innerHTML = '';
      }
    }

    // ── Review list ──
    if (!reviewsRes.ok) throw new Error('Failed to load reviews');
    const data = await reviewsRes.json();

    if (!data.reviews || data.reviews.length === 0) {
      list.innerHTML = '<div style="color:var(--text-gray);font-size:0.8rem;padding:16px 0;">No reviews yet. Artists who have performed here can leave a review.</div>';
      if (pagination) pagination.innerHTML = '';
      return;
    }

    list.innerHTML = data.reviews.map(r => {
      // Use same card style as artist-reviews.js
      let starsHtml = '';
      for (let i = 1; i <= 5; i++) {
        starsHtml += `<span style="color:${i <= r.rating ? '#f59e0b' : '#d1d5db'};font-size:15px;">★</span>`;
      }
      const date = r.created_at
        ? new Date(r.created_at).toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
        : '';
      return `
        <div style="border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:10px;background:var(--card);">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
            <div>
              <div style="margin-bottom:2px;">${starsHtml}</div>
              <div style="font-size:0.72rem;color:var(--text-gray);">
                <a href="/app/artist-profile.html?artist_id=${r.artist_id}" target="_blank"
                   style="color:var(--cyan);text-decoration:none;font-weight:600;">${esc(r.artist_name || 'Artist')}</a>
              </div>
            </div>
            <div style="font-size:0.7rem;color:var(--text-gray);">${date}</div>
          </div>
          ${r.review_text ? `<p style="margin:0;font-size:0.8rem;color:var(--text);line-height:1.5;">${esc(r.review_text)}</p>` : ''}
        </div>`;
    }).join('');

    // Pagination
    if (pagination) {
      if (data.pages <= 1) {
        pagination.innerHTML = '';
      } else {
        const btns = [];
        for (let p = 1; p <= data.pages; p++) {
          const active = p === data.page;
          btns.push(`<button onclick="loadVenueReviews(${p})" style="padding:4px 12px;border-radius:6px;font-size:0.8rem;cursor:pointer;background:${active ? 'rgba(6,182,212,0.2)' : 'transparent'};border:1px solid ${active ? 'rgba(6,182,212,0.5)' : 'rgba(255,255,255,0.15)'};color:${active ? '#06b6d4' : 'var(--text-muted)'};">${p}</button>`);
        }
        pagination.innerHTML = btns.join('');
      }
    }
  } catch (e) {
    if (list) list.innerHTML = '<div style="color:var(--text-gray);font-size:0.9rem;">Could not load reviews.</div>';
    console.error('loadVenueReviews:', e);
  }
}

// Make pagination buttons work globally
window.loadVenueReviews = loadVenueReviews;

// Also load review summary in background on page load to populate the badge
window.addEventListener('load', () => {
  if (!venueId) return;
  fetch(`/api/venues/${venueId}/reviews/summary`, { credentials: 'include' })
    .then(r => r.ok ? r.json() : null)
    .then(s => {
      if (s && s.review_count > 0) {
        const badge = document.getElementById('reviewsBadge');
        if (badge) badge.textContent = `(${s.review_count})`;
      }
    }).catch(() => {});
});
