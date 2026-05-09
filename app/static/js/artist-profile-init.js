// Auto-extracted from artist-profile.html inline scripts
// Generated for CSP compliance (Phase 5)

const TAB_NAMES = ['info','calendar','videos','pictures','audio','social'];
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.profile-tabs button').forEach(b => b.classList.remove('active'));
  const panel = document.getElementById('tab-' + name);
  if (panel) panel.classList.add('active');
  const idx = TAB_NAMES.indexOf(name);
  const btns = document.querySelectorAll('.profile-tabs button');
  if (btns[idx]) btns[idx].classList.add('active');
}

let artist = null, artistGigs = [], calYear, calMonth;
const params = new URLSearchParams(window.location.search);
let artistId = params.get("artist_id");

(async function loadArtist() {
  if (!artistId) {
    const meRes = await fetch("/api/me", { credentials: "include" });
    if (!meRes.ok) return;
    const me = await meRes.json();
    if (!me.artist_id) return;
    artistId = me.artist_id;
  }
  const res = await fetch(`/api/artists/${artistId}`, { credentials: "include" });
  if (!res.ok) return;
  artist = await res.json();

  document.getElementById("artistName").textContent = artist.name || "";
  document.getElementById("artistLocation").textContent = [artist.city, artist.state].filter(Boolean).join(", ");
  document.title = (artist.name || "Artist") + " – GigsFill";
  // Dynamic SEO updates
  const seoDesc = `${artist.name || 'Artist'}${artist.city ? ' from ' + artist.city + ', ' + artist.state : ''} – ${artist.artist_type || 'musician'} on GigsFill. View profile, upcoming gigs, and booking info.`;
  document.querySelector('meta[name="description"]')?.setAttribute('content', seoDesc);
  document.querySelector('meta[property="og:title"]')?.setAttribute('content', document.title);
  document.querySelector('meta[property="og:description"]')?.setAttribute('content', seoDesc);
  document.querySelector('meta[name="twitter:title"]')?.setAttribute('content', document.title);
  document.querySelector('meta[name="twitter:description"]')?.setAttribute('content', seoDesc);

  let typeText = artist.artist_type || "";
  const artistTypeEl = document.getElementById("artistType");
  if (artist.artist_type === "Live Band" && (artist.band_formats || artist.styles)) {
    const rowStyle = 'display: flex; gap: 8px; align-items: baseline; font-size: 0.95em;';
    const labelStyle = 'color: rgba(255,255,255,0.6); white-space: nowrap; min-width: 90px; text-align: right;';
    const valueStyle = 'color: #fff; font-weight: 500;';
    let rows = `<div style="${rowStyle}"><span style="${labelStyle}">Artist Type:</span> <span style="${valueStyle}">${esc(artist.artist_type)}</span></div>`;
    if (artist.band_formats) {
      rows += `<div style="${rowStyle}"><span style="${labelStyle}">Lineup(s):</span> <span style="${valueStyle}">${esc(artist.band_formats.replace(/,/g, ', '))}</span></div>`;
    }
    if (artist.styles) {
      rows += `<div style="${rowStyle}"><span style="${labelStyle}">Styles:</span> <span style="${valueStyle}">${esc(artist.styles.replace(/,/g, ', '))}</span></div>`;
    }
    artistTypeEl.innerHTML = `<div style="display: flex; flex-direction: column; gap: 4px; text-align: left;">${rows}</div>`;
  } else {
    artistTypeEl.textContent = typeText;
  }
  document.getElementById("artistBio").textContent = (artist.bio || "").trim();
  document.getElementById("artistBooking").textContent = artist.booking_contact ? `Booking: ${artist.booking_contact}` : "";

  const ensureProto = u => { if (!u) return ''; const t = u.trim(); return (t.startsWith('http://') || t.startsWith('https://')) ? t : 'https://' + t; };
  const links = [
    { url: artist.spotify_url, label: "Spotify", icon: "🎵" },
    { url: artist.instagram_url, label: "Instagram", icon: "📷" },
    { url: artist.facebook_url, label: "Facebook", icon: "👥" },
    { url: artist.youtube_url, label: "YouTube", icon: "▶️" },
    { url: artist.twitter_url, label: "Twitter/X", icon: "🐦" },
    { url: artist.tiktok_url, label: "TikTok", icon: "🎬" },
    { url: artist.website_url, label: "Website", icon: "🌐" }
  ].filter(l => l.url && l.url.trim());
  if (links.length > 0) {
    document.getElementById("socialMedia").innerHTML = links.map(l => `<a href="${escAttr(ensureProto(l.url))}" target="_blank" rel="noopener noreferrer" class="social-link"><span>${esc(l.icon)}</span><span>${esc(l.label)}</span></a>`).join('');
  } else { document.getElementById("socialEmpty").style.display = "block"; }

  document.getElementById("artistLogo").src = "/app/static/img/profile-placeholder.svg";
  loadMedia();
  loadGigs();
})();

// ========== CALENDAR ==========
async function loadGigs() {
  try { const r = await fetch(`/api/artists/${artistId}/gigs/public`); if (r.ok) artistGigs = await r.json(); } catch(e) {}
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
    const dayGigs = artistGigs.filter(g => g.date === ds);
    html += `<div class="cal-day${isToday ? ' today' : ''}${isPast ? ' past' : ''}"><div class="day-num">${d}</div>`;
    if (dayGigs.length > 0) {
      html += '<div class="cal-gigs-container">';
      dayGigs.forEach((g, idx) => {
        const hasBookedSlots = g.booked_slots_count > 0;
        const cls = (g.status === 'booked' || hasBookedSlots) ? 'booked' : 'open';
        const t = fmtTime(g.start_time);
        const icon = (icons[g.artist_type] || '🎵') + ' ';
        const label = `${icon}${t} ${g.venue_name || g.title || 'Gig'}`;
        html += `<div class="cal-gig ${cls}" onclick="showGigDetail('${ds}',${idx})" title="${esc(label)}">${esc(label)}</div>`;
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
  const dayGigs = artistGigs.filter(g => g.date === date);
  const g = dayGigs[idx]; if (!g) return;
  const timeStr = fmtTime(g.start_time) + (g.end_time ? ' – ' + fmtTime(g.end_time) : '');
  const loc = [g.city, g.state].filter(Boolean).join(', ');
  const artType = g.artist_actual_type || artist?.artist_type || '';
  let formats = g.artist_band_formats || artist?.band_formats || '';
  if (formats) formats = formats.replace(/,/g, ', ');

  const isMultiSlot = g.total_slots_count > 0;
  const addr1 = g.address_line_1 || '';
  const addr2 = g.address_line_2 || '';
  let locHtml = '';
  if (addr1) locHtml += esc(addr1) + '<br>';
  if (addr2) locHtml += esc(addr2) + '<br>';
  if (g.city || g.state) locHtml += esc([g.city, g.state].filter(Boolean).join(', '));

  let rows = '';
  rows += modalRow('Date', fmtDate(g.date));
  if (!isMultiSlot) rows += modalRow('Time', timeStr || 'TBD');
  const venueLink = g.venue_id ? `<a href="/app/venue-profile.html?venue_id=${g.venue_id}" target="_blank">${esc(g.venue_name || '')}</a>` : esc(g.venue_name || '');
  rows += modalRowRaw('Venue', venueLink);
  if (locHtml) rows += modalRowRaw('Location', locHtml);
  if (!isMultiSlot) rows += modalRow('Artist', g.artist_name || artist?.name || '');
  if (artType) rows += modalRow('Artist Type', artType);
  if (artType === 'Live Band' && formats) rows += modalRow('Lineup', formats);
  let gStyles = g.styles || '';
  if (artType === 'Live Band' && gStyles) rows += modalRow('Styles', gStyles.replace(/,/g, ', '));
  
  // For multi-slot gigs, fetch and display slot details
  if (isMultiSlot) {
    try {
      const slotsRes = await fetch(`/api/gigs/${g.id}/slots`);
      if (slotsRes.ok) {
        const slots = await slotsRes.json();
        slots.forEach(s => {
          const isBooked = s.status === 'booked';
          const color = isBooked ? '#ef4444' : '#22c55e';
          const bg = isBooked ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)';
          const slotLabel = isBooked 
            ? (String(s.artist_id) === String(artistId)
                ? `<span style="color:${color}; font-weight:600;">${esc(s.artist_name || 'Booked')}</span>`
                : `<a href="/app/artist-profile.html?artist_id=${s.artist_id}" target="_blank" style="color:${color}; text-decoration:none; font-weight:600;">${esc(s.artist_name || 'Booked')}</a>`)
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
  const res = await fetch(`/api/artists/${artistId}/media`, { credentials: "include" }); if (!res.ok) return;
  const items = await res.json();
  const pics = document.getElementById("pictures"), audio = document.getElementById("audio"), videos = document.getElementById("videos");
  pics.innerHTML = ""; audio.innerHTML = ""; videos.innerHTML = "";
  items.forEach(m => {
    if (m.media_type === "logo" || m.media_type === "profile") document.getElementById("artistLogo").src = m.file_path;
    if (m.media_type === "picture") { const d = document.createElement("div"); d.className = "media-card"; d.innerHTML = `<img src="${escAttr(m.file_path)}"><div class="media-title-label">${esc(m.title||"")}</div>`; d.onclick = () => openModal(m.file_path, "image"); pics.appendChild(d); }
    if (m.media_type === "audio") { const d = document.createElement("div"); d.className = "audio-row"; d.innerHTML = `<strong>${esc((m.title||"").substring(0,50))}</strong><audio controls src="${escAttr(m.file_path)}"></audio>`; audio.appendChild(d); d.querySelector("audio").addEventListener("play", function(){ document.querySelectorAll("#audio audio").forEach(a => { if(a!==this) a.pause(); }); }); }
    if (m.media_type === "video") { const d = document.createElement("div"); d.className = "media-card"; d.innerHTML = `<img src="${escAttr(ytThumb(m.video_url))}"><div class="media-title-label">${esc(m.title||"")}</div>`; d.onclick = () => openModal(m.video_url, "video"); videos.appendChild(d); }
  });
  if (!pics.children.length) document.getElementById('picturesEmpty').style.display = 'block';
  if (!audio.children.length) document.getElementById('audioEmpty').style.display = 'block';
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
