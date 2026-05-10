import { apiGet, apiPost } from "./api.js";
// REMOVED: notifications.js - now using activity-center.js

// Global closeModal function
window.closeModal = function() {
  const overlay = document.getElementById('modalOverlay');
  if (overlay) {
    overlay.classList.add("hidden");
    overlay.style.display = ""; // reset inline styles
  }
};

document.addEventListener("DOMContentLoaded", async () => {
  const calendarEl = document.getElementById("calendar");
  const monthLabel = document.getElementById("monthLabel");
  const prevBtn = document.getElementById("prevMonth");
  const nextBtn = document.getElementById("nextMonth");
  
  // Second calendar for search tab
  const calendarEl2 = document.getElementById("calendar2");
  const monthLabel2 = document.getElementById("monthLabel2");
  const prevBtn2 = document.getElementById("prevMonth2");
  const nextBtn2 = document.getElementById("nextMonth2");

  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  
  if (!artistId) {
    alert("No artist selected");
    return;
  }

  // Set access-denied flag immediately (synchronously) so all other scripts
  // that run via DOMContentLoaded/setTimeout see it before their fetches fire.
  // Will be cleared below once access is confirmed.
  window._artistAccessDenied = true;

  if (!calendarEl) {
    console.error("❌ calendar element not found");
    return;
  }

  // Upfront access check: if user doesn't own this artist (or have entity_users access),
  // show modal and stop before any other loads. This runs before loadCities/loadGigs/etc.
  const accessRes = await fetch(`/api/artists/${artistId}/access-check`, { credentials: "include" });
  if (!accessRes.ok) {
    const overlay = document.getElementById("modalOverlay");
    const modalTitle = document.getElementById("modalTitle");
    const modalBody = document.getElementById("modalBody");
    const modalActions = document.getElementById("modalActions");
    if (overlay && modalTitle && modalBody && modalActions) {
      modalTitle.textContent = "No Access to This Artist";
      modalBody.innerHTML = `
        <p style="margin-bottom: 16px; line-height: 1.6;">You’re logged in, but your account doesn’t have permission to manage this artist profile.</p>
        <p style="margin-bottom: 16px; line-height: 1.6; color: var(--text-muted); font-size: 0.9rem;">If you think this is a mistake, ask the artist owner to invite you from their <strong>Users</strong> tab.</p>
      `;
      if (modalActions) { modalActions.innerHTML = ''; modalActions.style.display = 'none'; }
      modalBody.innerHTML += '<div style="display:flex;justify-content:flex-end;margin-top:16px;"><button class="_gig-btn _gig-btn-ghost" onclick="window.location.href=\'/app/index.html\'">Close</button></div>';
      overlay.classList.remove("hidden");
    } else {
      document.body.innerHTML = `
        <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#020617;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
          <div style="max-width:460px;padding:32px 28px;border-radius:16px;background:rgba(15,23,42,0.95);border:1px solid rgba(148,163,184,0.35);text-align:center;">
            <div style="font-size:0.85rem;font-weight:600;letter-spacing:0.18em;color:#38bdf8;margin-bottom:8px;">GIGSFILL</div>
            <h1 style="margin:0 0 12px;font-size:1.2rem;font-weight:600;color:#e5e7eb;">No Access to This Artist</h1>
            <p style="margin:0 0 20px;font-size:0.9rem;line-height:1.6;color:#9ca3af;">You’re logged in, but your account doesn’t have permission to manage this artist profile.</p>
            <div style="display:flex;gap:10px;justify-content:center;">
            <button onclick="window.location.href='/app/login.html';" style="display:inline-block;padding:10px 28px;border-radius:8px;background:#635bff;color:white;font-weight:600;cursor:pointer;border:none;font-size:0.95rem;">Close</button>
            </div>
          </div>
        </div>
      `;
    }
    window._artistAccessDenied = true;
    return;
  }

  // Access confirmed — clear the flag so other scripts can proceed normally
  window._artistAccessDenied = false;

  let currentDate = new Date();
  let gigs = [];
  let allGigs = []; // Store unfiltered gigs
  let preferredVenues = [];
  let myGigs = [];
  let myWaitlistGigIds = new Set(); // gig IDs where this artist is on the waitlist
  let myWaitlistOfferGigIds = new Set(); // gig IDs where this artist has an active offer
  let myWaitlistOfferExpiry = {}; // gig_id -> offer_expires_at ISO string
  let venueFrequencies = {};
  let venuePayOverrides = {}; // venue_id -> override pay amount
  let venueBlastSettings = {}; // venue_id -> blast/blink settings from public endpoint
  let artistDefaultCity = null; // v73: Store artist's city for Clear Filters

  // Search/Filter state
  let filters = {
    venue: '',
    city: '',
    cityCoords: null, // {lat, lon}
    minPay: 0,
    mileRadius: 20
  };

  let allCities = []; // US cities for autocomplete
  let artistData = null; // v94: Store artist's type and formats for gig matching
    window._artistData = artistData;

  // Centralized handler when the logged-in user doesn't have access
  // to the artist profile (e.g. URL artist_id was edited).
  function showNoArtistAccessAndStop() {
    const overlay = document.getElementById("modalOverlay");
    const modalTitle = document.getElementById("modalTitle");
    const modalBody = document.getElementById("modalBody");
    const modalActions = document.getElementById("modalActions");

    if (overlay && modalTitle && modalBody && modalActions) {
      modalTitle.textContent = "No Access to This Artist";
      modalBody.innerHTML = `
        <p style="margin-bottom: 16px; line-height: 1.6;">
          You’re logged in, but your account doesn’t have permission to manage this artist profile.
        </p>
        <p style="margin-bottom: 16px; line-height: 1.6; color: var(--text-muted); font-size: 0.9rem;">
          If you think this is a mistake, ask the artist owner to invite you from their <strong>Users</strong> tab.
        </p>
      `;
      if (modalActions) { modalActions.innerHTML = ''; modalActions.style.display = 'none'; }
      modalBody.innerHTML += '<div style="display:flex;justify-content:flex-end;margin-top:16px;"><button class="_gig-btn _gig-btn-ghost" onclick="window.location.href=\'/app/index.html\'">Close</button></div>';
      overlay.classList.remove("hidden");
    } else {
      // Fallback: full-page message if modal elements aren't present
      document.body.innerHTML = `
        <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#020617;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
          <div style="max-width:460px;padding:32px 28px;border-radius:16px;background:rgba(15,23,42,0.95);box-shadow:0 20px 40px rgba(15,23,42,0.8);border:1px solid rgba(148,163,184,0.35);text-align:center;">
            <div style="font-size:0.85rem;font-weight:600;letter-spacing:0.18em;color:#38bdf8;margin-bottom:8px;">GIGSFILL</div>
            <h1 style="margin:0 0 12px;font-size:1.2rem;font-weight:600;color:#e5e7eb;">No Access to This Artist</h1>
            <p style="margin:0 0 20px;font-size:0.9rem;line-height:1.6;color:#9ca3af;">
              You’re logged in, but your account doesn’t have permission to manage this artist profile.
            </p>
            <div style="display:flex;gap:10px;justify-content:center;">
            <button onclick="window.location.href='/app/login.html';" style="display:inline-block;padding:10px 28px;border-radius:8px;background:#635bff;color:white;font-weight:600;cursor:pointer;border:none;font-size:0.95rem;">Close</button>
            </div>
          </div>
        </div>
      `;
    }
  }

  /* ---------------- UTIL ---------------- */

  function formatTime12Hour(timeStr) {
    if (!timeStr) return "";
    const [h, m] = timeStr.split(":").map(Number);
    const period = h >= 12 ? "PM" : "AM";
    const hour = ((h + 11) % 12) + 1;
    return `${hour}:${m.toString().padStart(2, "0")} ${period}`;
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

  function showSuccessModal(title, message) {
    const overlay = document.getElementById("modalOverlay");
    const modalTitle = document.getElementById("modalTitle");
    const modalBody = document.getElementById("modalBody");
    const modalActions = document.getElementById("modalActions");

    modalTitle.textContent = title;
    modalBody.innerHTML = `
      <div style="text-align: center; padding: 20px;">
        <div style="font-size: 3rem; margin-bottom: 16px;">✓</div>
        <p style="font-size: 1.1rem; color: #10b981; font-weight: 600; margin-bottom: 24px;">${message}</p>
        <button class="btn primary" onclick="document.getElementById('modalOverlay').classList.add('hidden')" style="min-width: 120px;">OK</button>
      </div>
    `;
    if (modalActions) modalActions.style.display = 'none';

    overlay.classList.remove("hidden");
  }

  function daysBetween(date1Str, date2Str) {
    const d1 = new Date(date1Str);
    const d2 = new Date(date2Str);
    const diffTime = Math.abs(d2 - d1);
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    return diffDays;
  }

  function isGigBlockedByFrequency(gig) {
    
    // CRITICAL FIX: Only check frequency for venues where this artist has BOOKED gigs
    const venueGigs = myGigs.filter(g => g.venue_id === gig.venue_id);
    
    // If artist has never booked at this venue, frequency doesn't apply
    if (venueGigs.length === 0) {
      return false;
    }

    const freqLimit = venueFrequencies[gig.venue_id];
    // 0 days = no restriction (venue override allows any booking)
    if (freqLimit === 0 || freqLimit === undefined || freqLimit === null) {
      return false;
    }

    for (const bookedGig of venueGigs) {
      // Calculate days between - DON'T use abs() yet, we need the sign!
      const gigDate = (() => { const _m = gig.date.match(/(\d{4})-(\d{2})-(\d{2})/); return _m ? new Date(parseInt(_m[1]), parseInt(_m[2])-1, parseInt(_m[3])) : new Date(gig.date); })();
      const bookedDate = (() => { const _m = bookedGig.date.match(/(\d{4})-(\d{2})-(\d{2})/); return _m ? new Date(parseInt(_m[1]), parseInt(_m[2])-1, parseInt(_m[3])) : new Date(bookedGig.date); })();
      const daysDiff = Math.floor((gigDate - bookedDate) / (1000 * 60 * 60 * 24));
      const absDays = Math.abs(daysDiff);
      
      if (absDays <= freqLimit) {
        return {
          blocked: true,
          lastGigDate: bookedGig.date,
          daysBetween: daysDiff, // Can be negative (before) or positive (after)
          absDaysBetween: absDays,
          daysRequired: freqLimit,
          isBeforeBookedGig: daysDiff < 0 // Negative means gig is BEFORE booked gig
        };
      }
    }

    return false;
  }

  function isGigStartedToday(gig) {
    if (!gig.date || !gig.start_time) return false;
    const now = new Date();
    const [y, m, d] = gig.date.split('-').map(Number);
    const gigDay = new Date(y, m - 1, d);
    gigDay.setHours(0, 0, 0, 0);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (gigDay.getTime() !== today.getTime()) return false;
    const [h, min] = gig.start_time.split(':').map(Number);
    const startTime = new Date(y, m - 1, d, h, min, 0);
    return now >= startTime;
  }

  function formatDateForDisplay(dateStr) {
    if (!dateStr) return '';
    const [year, month, day] = dateStr.split('-').map(Number);
    const d = new Date(year, month - 1, day);
    return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  function haversineMiles(lat1, lon1, lat2, lon2) {
    const R = 3958.8;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2)**2 + Math.cos(lat1 * Math.PI/180) * Math.cos(lat2 * Math.PI/180) * Math.sin(dLon/2)**2;
    return R * 2 * Math.asin(Math.sqrt(a));
  }

  function isBlastOpenForArtist(gig) {
    // is_blast_open means radius_blast_token is set — an actual blast has fired.
    // frequency_exempt alone just means frequency limits are waived for booking;
    // it does NOT mean the gig should glow amber on the calendar.
    if (!gig.is_blast_open) return false;
    // is_blast_open with no artist location — show amber (conservative: let them try)
    if (!artistData || !artistData.latitude || !artistData.longitude) return true;
    if (!gig.venue_lat || !gig.venue_lon) return true;
    const dist = haversineMiles(artistData.latitude, artistData.longitude, gig.venue_lat, gig.venue_lon);
    return dist <= (gig.blast_radius_miles || 20);
  }

  function isGigToday(gig) {
    if (!gig.date) return false;
    const [y, mo, d] = gig.date.split('-').map(Number);
    const today = new Date();
    return y === today.getFullYear() && mo === today.getMonth() + 1 && d === today.getDate();
  }

  function isGigEndPassed(gig) {
    if (!gig.date) return false;
    const [y, mo, d] = gig.date.split('-').map(Number);
    const endTimeStr = gig.end_time || gig.start_time || '23:59';
    const [h, min] = endTimeStr.split(':').map(Number);
    let endDate = new Date(y, mo - 1, d, h, min, 0);
    if (gig.start_time && gig.end_time && gig.end_time <= gig.start_time) {
      endDate = new Date(y, mo - 1, d + 1, h, min, 0);
    }
    return new Date() > endDate;
  }

  // ── Artist blink logic ────────────────────────────────────────────────────
  // Returns {blink: true, color: '#f59e0b'} if this gig bubble should blink
  // for this artist, or {blink: false} if not.
  //
  // Rules:
  //  1. Cancelled/radius blast: only blink when the blast has already fired
  //     (g.is_blast_open set). No date-proximity blinking for these.
  //  2. Open gig windows (1w, 36h): blink if gig date is within the venue's
  //     configured window AND blink_enabled is on AND:
  //       a. artist is a preferred artist at this venue, OR
  //       b. venue has blast_all_enabled for that key and artist is within radius.
  function shouldBlinkForArtist(gig) {
    if (!gig || gig.status !== 'open' || isGigEndPassed(gig)) return {blink: false};

    const bs = venueBlastSettings[gig.venue_id];
    const isPreferred = preferredVenues.some(
      pv => pv.venue_id === gig.venue_id && pv.status === 'approved'
    );

    // ── Case 1: actual radius blast fired (radius_blast_token set on gig) ──
    if (gig.is_blast_open) {
      const firedKey = gig.last_notification_key || 'radius_blast';
      const s = bs && bs[firedKey];
      const color = (s && s.blink_color) || '#f59e0b';
      const blinkOn = s ? s.blink_enabled : true;
      if (blinkOn) return {blink: true, color};
      return {blink: false};
    }

    if (!bs) return {blink: false}; // no settings loaded yet

    // ── Case 2: open-gig notification windows (1w and 36h only) ──────────
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const gigDate = new Date(gig.date + 'T00:00:00');
    const daysUntil = Math.round((gigDate - now) / 86400000);
    if (daysUntil < 0) return {blink: false};

    function toDays(val, unit) {
      if (unit === 'hours') return val / 24;
      if (unit === 'days')  return val;
      return val * 7;
    }

    function artistInRadius(radiusMiles) {
      if (!artistData || !artistData.latitude || !artistData.longitude) return true; // no location = show it
      if (!gig.venue_lat || !gig.venue_lon) return true;
      return haversineMiles(artistData.latitude, artistData.longitude, gig.venue_lat, gig.venue_lon) <= radiusMiles;
    }

    // Only 1w and 36h notify open gigs; check in urgency order
    for (const key of ['open_gig_36h', 'open_gig_1w']) {
      const s = bs[key];
      if (!s || !s.enabled || !s.blink_enabled) continue;
      const windowDays = toDays(s.time_value || 1, s.time_unit || 'weeks');
      if (daysUntil > windowDays) continue;

      // Preferred artist: always blink
      if (isPreferred) return {blink: true, color: s.blink_color || '#f59e0b'};

      // Non-preferred: blink only if blast_all is enabled and within radius
      if (s.blast_all_enabled && artistInRadius(s.blast_all_radius || 20)) {
        return {blink: true, color: s.blink_color || '#f59e0b'};
      }
    }

    return {blink: false};
  }

  function getGigClass(gig) {
    // Pending venue approval (same-day booking) — show cyan pulse for the requesting artist,
    // show as booked (with waitlist option) for all other artists
    const aid = parseInt(artistId);

    // Check if THIS artist has a pending_venue_approval slot — works for both
    // single-slot gigs (gig.status = pending_venue_approval, gig.artist_id = aid)
    // AND multi-slot gigs (parent gig.status stays 'open', but slot status is pending)
    const myPendingEntry = myGigs.find(mg => mg.id === gig.id && (
      mg.status === "pending_venue_approval" ||
      (mg.slots || []).some(s => s.artist_id === aid && s.status === 'pending_venue_approval')
    ));
    if (myPendingEntry) return "pending-venue-approval";

    if (gig.status === "pending_venue_approval") {
      const isMyPending = gig.artist_id && parseInt(gig.artist_id) === aid;
      if (isMyPending) return "pending-venue-approval";
      // Another artist has a pending booking — treat as booked so others see waitlist
      return "booked-other";
    }

    // For open multi-slot gigs: detect pending contract via contract_status or contract_hold
    if (gig.status === "open") {
      const _pendingContractStates = ['artist_signed', 'pending', 'awaiting_venue_upload'];
      const _hasPendingContract = gig.contract_hold_artist_id ||
        (gig.contract_status && _pendingContractStates.includes(gig.contract_status));
      if (_hasPendingContract) {
        const myGigEntryPC = myGigs.find(mg => mg.id === gig.id);
        const myPendingSlotPC = myGigEntryPC && (myGigEntryPC.slots || []).some(s =>
          s.artist_id === aid && (s.status === 'pending_contract' || s.status === 'awaiting_venue_contract')
        );
        const isContractHolder = gig.contract_artist_id && gig.contract_artist_id === aid;
        return (myPendingSlotPC || isContractHolder) ? "pending-contract-mine" : "pending-contract";
      }
    }

    // Pending contract — verify THIS specific artist holds it (not a sibling on same account)
    if (gig.status === "pending_contract" || gig.status === "awaiting_venue_contract") {
      const isArtist = gig.artist_id && parseInt(gig.artist_id) === aid;
      const isHolder = (gig.contract_hold_artist_id && parseInt(gig.contract_hold_artist_id) === aid);
      const myGigEntry2 = myGigs.find(mg => mg.id === gig.id);
      const mySlotPending = myGigEntry2 && (myGigEntry2.slots || []).some(s =>
        s.artist_id === aid && (s.status === 'pending_contract' || s.status === 'booked')
      );
      const isMyPending = isArtist || isHolder || mySlotPending;
      return isMyPending ? "pending-contract-mine" : "pending-contract";
    }

    // Check if THIS specific artist has a booked slot (not a sibling artist on same account)
    const myGigEntry = myGigs.find(mg => mg.id === gig.id);
    const isMyGig = myGigEntry && (
      (myGigEntry.artist_id && parseInt(myGigEntry.artist_id) === aid) ||
      (myGigEntry.slots || []).some(s => s.artist_id === aid && (s.status === 'booked' || s.status === 'pending_contract'))
    );

    // If THIS artist is on the waitlist — blinking blue "waitlisted"
    if (window._myWaitlistGigIds && window._myWaitlistGigIds.has(gig.id) && !isMyGig) {
      return "waitlisted-mine";
    }

    // For open gigs with active waitlist — check BEFORE isMyGig so a cancelled artist
    // sees the waitlist bubble, not their old booked-mine bubble
    if (gig.status === "open" && gig.has_active_waitlist && !isMyGig) {
      return "waitlist-pending";
    }

    if (isMyGig) {
      // If the gig has started (or ended), show black "started" bubble — no more actions
      if (isGigStartedToday(gig) || isGigEndPassed(gig)) return "started";
      // Check if artist's slot is pending_contract — show blinking pending, not solid booked
      const myGigE = myGigs.find(mg => mg.id === gig.id);
      const myPendingSlot = myGigE && (myGigE.slots || []).some(s =>
        s.artist_id === aid && (s.status === 'pending_contract' || s.status === 'awaiting_venue_contract')
      );
      if (myPendingSlot) return "pending-contract-mine";
      return "booked-mine";
    }

    if (gig.status === "booked") {
      // Only show red "booked" bubble when ALL slots are booked
      const totalSlots = gig.total_slots_count || 0;
      const bookedSlots = gig.booked_slots_count || 0;
      if (totalSlots > 0 && bookedSlots < totalSlots) {
        return "open";
      }
      // If gig started, show black regardless
      if (isGigStartedToday(gig) || isGigEndPassed(gig)) return "started";
      return "booked-other";
    }

    if (gig.status === "open" && isGigStartedToday(gig)) {
      if (!isGigEndPassed(gig)) return "open";
      return "started";
    }

    const venueStatus = preferredVenues.find(v => v.venue_id === gig.venue_id);

    if (gig.status === "open" && !isGigEndPassed(gig)) {
      if (gig.has_active_waitlist) {
        return "waitlist-pending";
      }
      // blast-open removed — all open gigs show as green regardless of blast status
    }

    if (venueStatus && (venueStatus.status === "revoked" || venueStatus.status === "denied")) {
      return "blocked";
    }

    if (gig.status === "open") {
      if (venueStatus && venueStatus.status === "approved") {
        // Check freq exemption — multiple sources:
        // 1. gig.frequency_exempt: set when a blast email fired for this gig
        // 2. gig.is_blast_open: radius blast token present
        // 3. blastWindowExempt: gig is within venue's configured notification window
        //    (open_gig_36h or open_gig_1w). When a preferred artist would be notified
        //    by the blast, frequency limits are waived — they should be able to book.
        const gigFreqExempt = !!(gig.frequency_exempt || gig.is_blast_open);

        let blastWindowExempt = false;
        if (!gigFreqExempt) {
          const bs = venueBlastSettings[gig.venue_id];
          if (bs) {
            const _now = new Date(); _now.setHours(0, 0, 0, 0);
            const _gd  = new Date(gig.date + 'T00:00:00');
            const _days = Math.round((_gd - _now) / 86400000);
            if (_days >= 0) {
              for (const _k of ['open_gig_36h', 'open_gig_1w']) {
                const _s = bs[_k];
                if (!_s || !_s.enabled) continue;
                const _w = _s.time_unit === 'hours' ? _s.time_value / 24
                         : _s.time_unit === 'days'  ? _s.time_value
                         : _s.time_value * 7;
                if (_days <= _w) { blastWindowExempt = true; break; }
              }
            }
          }
        }

        const freqExempt = gigFreqExempt || blastWindowExempt;
        const freqCheck  = freqExempt ? null : isGigBlockedByFrequency(gig);
        if (freqCheck && freqCheck.blocked === true) {
          return "blocked";
        }
      }
      return "open";
    }

    return "blocked";
  }

  // Returns a descriptive tooltip for each calendar bubble
  function getGigHoverTitle(g, gigClass) {
    const aid = parseInt(artistId);
    switch (gigClass) {
      case 'booked-mine':
        return '💙 Your Gig is Booked!';
      case 'booked-other': {
        // Check if a slot (not just parent gig) has pending_venue_approval status
        const hasPendingSlot = (g.slots || []).some(s => s.status === 'pending_venue_approval');
        if (g.status === 'pending_venue_approval' || hasPendingSlot) {
          const artistName = g.artist_name || (g.slots||[]).find(s=>s.status==='pending_venue_approval')?.artist_name || 'an artist';
          return `⏳ ${artistName} has a booking request pending venue approval — join the waitlist!`;
        }
        const artistName = g.artist_name || 'another artist';
        if (myWaitlistGigIds.has(g.id)) {
          return `🔴 This gig is booked by ${artistName} — but you are on the waitlist!`;
        }
        return `🔴 This gig is booked by ${artistName}`;
      }
      case 'open':
        return '🟢 This gig is Open — book it now!';
      case 'started':
        return '⏱️ This gig has already started';
      case 'pending-venue-approval':
        return '⏳ Awaiting venue approval for your booking request';
      case 'pending-contract-mine': {
        const cs = g.contract_status || '';
        return (cs === 'artist_signed' || cs === 'fully_signed') ? '📋 Waiting on Venue to countersign...' : '📋 Upload your signed contract!';
      }
      case 'pending-contract':
        return '📋 Contract in progress with another artist';
      case 'waitlist-pending':
        if (myWaitlistOfferGigIds.has(g.id)) {
          const _exp = myWaitlistOfferExpiry[g.id];
          if (_exp) {
            const _expDate = new Date(_exp);
            const _now = new Date();
            const _hoursLeft = (_expDate - _now) / 3600000;
            const _timeStr = _expDate.toLocaleTimeString([], {hour: 'numeric', minute:'2-digit'});
            const _window = _hoursLeft <= 3 ? `until ${_timeStr}` : `${Math.round(_hoursLeft)} hours`;
            return `🎯 Gig just opened and you're next on the waitlist! You have ${_window} to book — check your email!`;
          }
          return '🎯 Gig just opened and you\'re next on the waitlist! Check your email to book!';
        }
        if (myWaitlistGigIds.has(g.id)) {
          return '⏳ You are on the waitlist! Another artist is being contacted first — you will be notified if they decline.';
        }
        return '🔴 Gig just opened — waitlisted artists are being contacted first. Check back soon!';
      case 'blast-open':
        return ''; // handled separately with detailed blast info
      case 'blocked': {
        // Determine WHY it's blocked
        const venueStatus = preferredVenues.find(v => v.venue_id === g.venue_id);
        if (venueStatus && venueStatus.status === 'banned') {
          return `🚫 You are not permitted to book at this venue`;
        }
        if (venueStatus && (venueStatus.status === 'revoked' || venueStatus.status === 'denied')) {
          return `⛔ ${venueStatus.status === 'revoked' ? 'Your preferred status was revoked' : 'Your preferred status request was denied'} at this venue`;
        }
        if (artistData && g.artist_type && artistData.artist_type !== g.artist_type) {
          return `⛔ You are not the right type of Artist for this gig — requires ${g.artist_type}`;
        }
        if (g.artist_type === 'Live Band' && g.band_formats && artistData && artistData.band_formats) {
          const reqFmts = g.band_formats.split(',').map(f => f.trim());
          const myFmts = artistData.band_formats.split(',').map(f => f.trim());
          if (!reqFmts.some(rf => myFmts.includes(rf))) {
            return `⛔ Your lineup doesn't match — this gig requires: ${reqFmts.join(', ')}`;
          }
        }
        if (venueStatus && venueStatus.status === 'approved') {
          // Check if frequency is waived — same logic as getGigClass
          const _gigFE = !!(g.frequency_exempt || g.is_blast_open);
          let _blastWE = false;
          if (!_gigFE) {
            const _bs = venueBlastSettings[g.venue_id];
            if (_bs) {
              const _now2 = new Date(); _now2.setHours(0,0,0,0);
              const _gd2  = new Date(g.date + 'T00:00:00');
              const _days2 = Math.round((_gd2 - _now2) / 86400000);
              if (_days2 >= 0) {
                for (const _k2 of ['open_gig_36h', 'open_gig_1w']) {
                  const _s2 = _bs[_k2];
                  if (!_s2 || !_s2.enabled) continue;
                  const _w2 = _s2.time_unit === 'hours' ? _s2.time_value / 24
                            : _s2.time_unit === 'days'  ? _s2.time_value
                            : _s2.time_value * 7;
                  if (_days2 <= _w2) { _blastWE = true; break; }
                }
              }
            }
          }
          if (_gigFE || _blastWE) {
            return `🟢 Frequency limits waived — book this gig now!`;
          }
          const freqCheck = isGigBlockedByFrequency(g);
          if (freqCheck && freqCheck.blocked === true) {
            const freqDays = freqCheck.daysRequired;
            const lastDate = freqCheck.lastGigDate ? new Date(freqCheck.lastGigDate + 'T00:00:00').toLocaleDateString('en-US', {month:'short', day:'numeric'}) : 'recently';
            return `⛔ Frequency limit: this venue allows 1 gig every ${freqDays} day${freqDays !== 1 ? 's' : ''}. You have a gig booked on ${lastDate}`;
          }
        }
        return '⛔ This gig is not available to you';
      }
      default:
        return '';
    }
  }

  function renderMyGigs() {
    const el = document.getElementById("myGigs");
    if (!el) return;
  
    if (!myGigs.length) {
      el.innerHTML = "<em>No gigs booked yet.</em>";
      return;
    }
  
    el.innerHTML = myGigs.map(g => {
      const time =
        g.start_time && g.end_time
          ? `${formatTime12Hour(g.start_time)} – ${formatTime12Hour(g.end_time)}`
          : g.start_time
          ? formatTime12Hour(g.start_time)
          : "";
  
      // Build address parts, filtering out null/empty values
      const addressParts = [
        g.address_line_1,
        g.address_line_2,
        g.city && g.state ? `${g.city}, ${g.state}` : (g.city || g.state),
        g.postal_code && g.postal_code !== 'null' ? g.postal_code : null
      ].filter(Boolean).join(", ");
  
      return `
        <div class="my-gig" style="padding: 12px; margin-bottom: 8px; background: rgba(255,255,255,0.03); border-radius: 8px;">
          <strong>${g.date}</strong>
          · ${time}
          · <a href="/app/venue-profile.html?venue_id=${g.venue_id}" target="_blank">
              <strong>${g.venue_name}</strong>
            </a>
          ${addressParts ? `· ${addressParts}` : ''}
        </div>
      `;
    }).join("");
  }
  
  /* ---------------- LOAD DATA ---------------- */

  async function loadGigs() {
    allGigs = await apiGet("/gigs");
    gigs = [...allGigs]; // Copy for filtering
    // Fetch blast/blink settings for any new venues not yet cached
    await loadVenueBlastSettings();
  }

  async function loadMyGigs() {
    myGigs = await apiGet(`/api/my/gigs?artist_id=${artistId}`);
    renderMyGigs();
    // Also refresh waitlist membership for hover titles
    try {
      const wlRes = await fetch(`/api/artists/${artistId}/waitlist`, { credentials: 'include' });
      if (wlRes.ok) {
        const wlItems = await wlRes.json();
        myWaitlistGigIds = new Set((wlItems || []).map(w => w.gig_id));
        myWaitlistOfferGigIds = new Set((wlItems || []).filter(w => w.has_offer || w.offer_sent).map(w => w.gig_id));
        myWaitlistOfferExpiry = {};
        (wlItems || []).filter(w => (w.has_offer || w.offer_sent) && w.offer_expires_at).forEach(w => {
          myWaitlistOfferExpiry[w.gig_id] = w.offer_expires_at;
        });
      }
    } catch (_e) {}
  }  

  async function loadVenueFrequencies() {
    const venueIds = [...new Set(gigs.map(g => g.venue_id))];
    
    for (const venueId of venueIds) {
      try {
        const res = await fetch(`/api/venues/${venueId}/frequency`, {
          credentials: "include"
        });
        if (res.ok) {
          const data = await res.json();
          venueFrequencies[venueId] = data.artist_frequency_days;
        }
      } catch (e) {
        console.error(`Failed to load frequency for venue ${venueId}:`, e);
      }
    }
  }

  async function loadPreferredVenues() {
    try {
      preferredVenues = await apiGet(`/api/artist/preferred-venues?artist_id=${artistId}`);
      // Build pay override lookup
      venuePayOverrides = {};
      preferredVenues.forEach(pv => {
        if (pv.status === 'approved' && pv.pay_dollars_override != null) {
          venuePayOverrides[pv.venue_id] = parseFloat(pv.pay_dollars_override) + parseFloat(pv.pay_cents_override || 0) / 100;
        }
      });
      // Use venue's frequency override for this artist when set (0 = no restriction)
      preferredVenues.forEach(pv => {
        if (pv.venue_id != null && pv.frequency_days_override !== undefined && pv.frequency_days_override !== null) {
          venueFrequencies[pv.venue_id] = Number(pv.frequency_days_override);
        }
      });
      // renderPreferredSection(); // DISABLED - Preferred status now shown in Activity Center
    } catch (e) {
      console.error('❌ v97: Failed to load preferred venues:', e);
      // If this call is forbidden, treat it as no access to this artist
      showNoArtistAccessAndStop();
      throw e;
    }
  }

  // Get effective pay: MAX of published gig pay vs venue's override for this artist
  function getEffectivePay(gig) {
    const gigPay = parseFloat(gig.pay) || 0;
    const overridePay = venuePayOverrides[gig.venue_id] || 0;
    return Math.max(gigPay, overridePay);
  }

  // Fetch blast/blink settings for all unique venues in gig list
  async function loadVenueBlastSettings() {
    const venueIds = [...new Set(gigs.map(g => g.venue_id).filter(Boolean))];
    await Promise.all(venueIds.map(async vid => {
      if (venueBlastSettings[vid]) return; // already cached
      try {
        const res = await fetch(`/api/venues/${vid}/blast-settings/public`, { credentials: 'include' });
        if (res.ok) venueBlastSettings[vid] = await res.json();
      } catch (_e) {}
    }));
  }

  // v94: Load artist data for type matching
  async function loadArtistData() {
    try {
      artistData = await apiGet(`/api/artists/${artistId}`);
      // Set timezone from artist's state
      if (artistData && artistData.state && typeof setTimezoneFromState === 'function') {
        setTimezoneFromState(artistData.state);
      }
    } catch (e) {
      console.error('v94: Failed to load artist data:', e);
      artistData = null;
    }
  }

  async function getPreferredStatus(venueId) {
    const res = await fetch(`/venues/${venueId}/preferred-status?artist_id=${artistId}`, {
      credentials: "include"
    });
    if (!res.ok) return { status: null };
    return res.json();
  }

  /* ---------------- PREFERRED SECTION ---------------- */

  function renderPreferredSection() {
    if (!preferredEl) return;

    const pending = preferredVenues.filter(v => v.status === "pending");
    const approved = preferredVenues.filter(v => v.status === "approved");
    const denied = preferredVenues.filter(v => v.status === "denied");

    preferredEl.innerHTML = `
      <h3>Preferred Artist Status</h3>

      <div class="preferred-status-section">
        <div class="status-row" onclick="togglePreferredList('approved')" style="cursor: pointer; padding: 12px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; margin-bottom: 8px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="color: #10b981; font-weight: 600;">✓ Approved (${approved.length})</span>
            <span id="approved-arrow" style="color: #10b981;">▼</span>
          </div>
        </div>
        <div id="approved-list" class="venue-list" style="display: none; margin-bottom: 16px; padding-left: 12px;">
          ${approved.length ? approved.map(v => `
            <div style="padding: 6px 0;">
              <a href="/app/venue-profile.html?venue_id=${v.venue_id}" target="_blank" style="color: var(--accent-cyan);">
                ${v.venue_name}
              </a>
            </div>
          `).join("") : "<em style='color: var(--text-muted);'>None</em>"}
        </div>

        <div class="status-row" onclick="togglePreferredList('pending')" style="cursor: pointer; padding: 12px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 8px; margin-bottom: 8px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="color: #f59e0b; font-weight: 600;">⏳ Pending (${pending.length})</span>
            <span id="pending-arrow" style="color: #f59e0b;">▼</span>
          </div>
        </div>
        <div id="pending-list" class="venue-list" style="display: none; margin-bottom: 16px; padding-left: 12px;">
          ${pending.length ? pending.map(v => `
            <div style="padding: 6px 0;">
              <a href="/app/venue-profile.html?venue_id=${v.venue_id}" target="_blank" style="color: var(--accent-cyan);">
                ${v.venue_name}
              </a>
            </div>
          `).join("") : "<em style='color: var(--text-muted);'>None</em>"}
        </div>

        <div class="status-row" onclick="togglePreferredList('denied')" style="cursor: pointer; padding: 12px; background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; margin-bottom: 8px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="color: #ef4444; font-weight: 600;">✗ Denied (${denied.length})</span>
            <span id="denied-arrow" style="color: #ef4444;">▼</span>
          </div>
        </div>
        <div id="denied-list" class="venue-list" style="display: none; margin-bottom: 16px; padding-left: 12px;">
          ${denied.length ? denied.map(v => `
            <div style="padding: 6px 0;">
              <a href="/app/venue-profile.html?venue_id=${v.venue_id}" target="_blank" style="color: var(--accent-cyan);">
                ${v.venue_name}
              </a>
            </div>
          `).join("") : "<em style='color: var(--text-muted);'>None</em>"}
        </div>
      </div>
    `;
  }

  // Toggle function for collapsible lists
  window.togglePreferredList = function(status) {
    const list = document.getElementById(`${status}-list`);
    const arrow = document.getElementById(`${status}-arrow`);
    
    if (list.style.display === "none") {
      list.style.display = "block";
      arrow.textContent = "▲";
    } else {
      list.style.display = "none";
      arrow.textContent = "▼";
    }
  };

  /* ---------------- RENDER CALENDAR ---------------- */

  function renderCalendar() {
    calendarEl.innerHTML = "";

    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();

    monthLabel.textContent = currentDate.toLocaleString("default", {
      month: "long",
      year: "numeric"
    });
    
    // Get today's date for comparison
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    getMonthDays(year, month).forEach(day => {
      const iso = `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
      const cell = document.createElement("div");
      cell.className = "day";
      
      // Check if day is in current month
      const isCurrentMonth = day.getMonth() === month;
      if (!isCurrentMonth) {
        cell.classList.add("other-month");
      }
      
      // Check if this date is in the past
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

      // Get and sort gigs for this day (earliest to latest)
      const dayGigs = gigs.filter(g => g.date === iso).sort((a, b) => {
        return a.start_time.localeCompare(b.start_time);
      });

      if (dayGigs.length > 0) {
        // Create scrollable container for gigs
        const gigsContainer = document.createElement("div");
        gigsContainer.className = "gigs-container";
        
        dayGigs.forEach(g => {
          const div = document.createElement("div");
          const gigClass = getGigClass(g);
          div.className = `gig ${gigClass}`;
          const icons = { 'Live Band': '🎸', 'DJ': '🎧', 'Comedian': '🎤', 'Trivia Host': '🧠' };
          const icon = icons[g.artist_type] || '🎵';
          // For booked gigs, show the artist's actual slot time
          let bubbleTime = g.start_time;
          if (gigClass === 'booked-mine') {
            const myGigEntry = myGigs.find(mg => mg.id === g.id);
            if (myGigEntry && myGigEntry.start_time) bubbleTime = myGigEntry.start_time;
          }
          div.textContent = `${icon} ${formatTime12Hour(bubbleTime)} · ${g.venue_name}`;
          // Set hover title for all bubble types (blast-open handled separately below)
          if (gigClass !== 'blast-open') {
            const ht = getGigHoverTitle(g, gigClass);
            if (ht) div.title = ht;
          }
          if (gigClass === 'pending-contract-mine') {
            const cs = g.contract_status;
            // For multi-slot: check myGigs slot status too
            const _myGigE = myGigs.find(mg => mg.id === g.id);
            const _mySlot = _myGigE && (_myGigE.slots || []).find(s =>
              s.artist_id === parseInt(artistId) &&
              (s.status === 'pending_contract' || s.status === 'awaiting_venue_contract')
            );
            const _waitingOnVenue = cs === 'artist_signed' || cs === 'fully_signed' ||
              g.status === 'awaiting_venue_contract' ||
              (_mySlot && _mySlot.status === 'pending_contract');
            div.title = _waitingOnVenue ? 'Waiting on Venue...' : 'Upload Contract!';
          }
          // Waitlist active — red blinking bubble
          if (gigClass === 'waitlisted-mine') {
            div.style.cssText = 'background: linear-gradient(135deg, #3b82f6, #2563eb) !important; animation: gig-blast-pulse 1.2s ease-in-out infinite !important; color: #fff !important; cursor: pointer;';
            div.title = "You're waitlisted for this gig!";
          }
          if (gigClass === 'waitlist-pending') {
            div.style.cssText = 'background: linear-gradient(135deg, #ef4444, #dc2626) !important; animation: gig-blast-pulse 1.2s ease-in-out infinite !important; color: #fff !important; cursor: pointer;';
            div.title = getGigHoverTitle(g, gigClass);
            div.onmouseenter = function() { this.style.animationPlayState = 'paused'; };
            div.onmouseleave = function() { this.style.animationPlayState = 'running'; };
          }
          // Apply custom blink color when venue set a non-default color
          if (gigClass === 'blast-open') {
            const blinkInfo = shouldBlinkForArtist(g);
            const color = blinkInfo.color || '#f59e0b';
            if (color !== '#f59e0b') {
              const colorEnd = color === '#10b981' ? '#059669' : color;
              div.style.setProperty('--blink-bg', color);
              div.style.setProperty('--blink-bg-end', colorEnd);
            }
            // Build a meaningful hover title
            const bs = venueBlastSettings[g.venue_id] || {};
            let hoverTitle;
            if (g.is_blast_open) {
              const radius = g.blast_radius_miles || (bs.radius_blast && bs.radius_blast.blast_all_radius) || 20;
              hoverTitle = `⚡ Blasted to all artists within ${radius} miles — book now!`;
            } else {
              const isPreferredHere = preferredVenues.some(pv => pv.venue_id === g.venue_id && pv.status === 'approved');
              if (isPreferredHere) {
                hoverTitle = `📢 You were notified as a Preferred Artist — frequency limits waived, book now!`;
              } else {
                let blastRadius = null;
                for (const key of ['open_gig_36h', 'open_gig_1w']) {
                  const s = bs[key]; if (s && s.blast_all_enabled) { blastRadius = s.blast_all_radius || 20; break; }
                }
                hoverTitle = blastRadius
                  ? `📢 Blasted to all artists within ${blastRadius} miles — book now!`
                  : `📢 Open gig — notification sent to Preferred Artists`;
              }
            }
            div.title = hoverTitle;
            div.onmouseenter = function() { this.style.animationPlayState = 'paused'; };
            div.onmouseleave = function() { this.style.animationPlayState = 'running'; };
          }
          div.onclick = e => {
            e.stopPropagation();
            openGigModal(g);
          };
          gigsContainer.appendChild(div);
        });
        
        cell.appendChild(gigsContainer);
        
        // Add click handler to day cell (but not on gigs or scrollbar)
        cell.onclick = (e) => {
          if (e.target === cell || e.target === dayNumber) {
            openDayGigsModal(day, dayGigs);
          }
        };
        
      }

      calendarEl.appendChild(cell);
    });
    
    // Clone calendar to calendar2 if it exists (for search tab)
    if (calendarEl2) {
      calendarEl2.innerHTML = calendarEl.innerHTML;
      monthLabel2.textContent = monthLabel.textContent;
    }
  }

  /* ---------------- DAY GIGS MODAL ---------------- */
  
  function openDayGigsModal(day, dayGigs) {
    const overlay = document.getElementById("modalOverlay");
    const modal = overlay.querySelector(".modal");

    modal.classList.add("day-modal");

    const title = document.getElementById("modalTitle");
    const body = document.getElementById("modalBody");
    
    // Format date as "Monday, January 21, 2026"
    const dateStr = day.toLocaleDateString('en-US', { 
      weekday: 'long', 
      year: 'numeric', 
      month: 'long', 
      day: 'numeric' 
    });
    
    title.textContent = dateStr;
    
    let content = `
      <div class="day-modal-content" style="
        display: grid;
        grid-auto-rows: auto;
        row-gap: 10px;
        width: 100%;
        min-width: 820px;
        font-size: 0.7rem;
      ">
    `;

    
    dayGigs.forEach(g => {
      const gigClass = getGigClass(g);

      let gigBg = '';
      let gigBorder = '';
      let gigTextColor = '#ffffff';

      if (gigClass === 'open') {
        gigBg = 'linear-gradient(135deg,#10b981,#059669)';
        gigBorder = '#059669';
        gigTextColor = '#000000';
      } else if (gigClass === 'booked-mine') {
        gigBg = 'linear-gradient(135deg,#00d9ff,#00a8cc)';
        gigBorder = '#00a8cc';
        gigTextColor = '#000000';
      } else if (gigClass === 'booked-other') {
        gigBg = 'linear-gradient(135deg,#ef4444,#b91c1c)';
        gigBorder = '#b91c1c';
        gigTextColor = '#ffffff';
      } else if (gigClass === 'pending-contract-mine') {
        gigBg = 'linear-gradient(135deg,#3b82f6,#2563eb)';
        gigBorder = '#2563eb';
        gigTextColor = '#000000';
      } else if (gigClass === 'pending-contract') {
        gigBg = 'linear-gradient(135deg,#ef4444,#b91c1c)';
        gigBorder = '#b91c1c';
        gigTextColor = '#ffffff';
      } else if (gigClass === 'pending-venue-approval') {
        gigBg = 'linear-gradient(135deg,#00d9ff,#00a8cc)';
        gigBorder = '#00a8cc';
        gigTextColor = '#000000';
      } else if (gigClass === 'blocked') {
        gigBg = 'linear-gradient(135deg,#4b5563,#374151)';
        gigBorder = '#374151';
        gigTextColor = '#ffffff';
      } else if (gigClass === 'started') {
        gigBg = 'linear-gradient(135deg,#1f2937,#111827)';
        gigBorder = '#111827';
        gigTextColor = '#ffffff';
      } else if (gigClass === 'blast-open' || gigClass === 'waitlist-pending') {
        gigBg = 'linear-gradient(135deg,#f59e0b,#d97706)';
        gigBorder = '#d97706';
        gigTextColor = '#000000';
      }

      
      // Format lineup with spaces
      let bandFormatsDisplay = '';
      if (g.artist_type === 'Live Band' && g.band_formats) {
        const formatsWithSpaces = g.band_formats.split(',').map(f => f.trim()).join(', ');
        bandFormatsDisplay = `<span style="margin-left: 8px; color: var(--text-muted);">• ${formatsWithSpaces}</span>`;
      }
      if (g.artist_type === 'Live Band' && g.styles) {
        const stylesWithSpaces = g.styles.split(',').map(s => s.trim()).join(', ');
        bandFormatsDisplay += `<span style="margin-left: 8px; color: var(--text-muted);">• ${stylesWithSpaces}</span>`;
      }
      
      // For booked gigs, use the artist's actual slot time from myGigs
      let displayStartTime = g.start_time;
      let displayEndTime = g.end_time;
      if (gigClass === 'booked-mine') {
        const myGigEntry = myGigs.find(mg => mg.id === g.id);
        if (myGigEntry && myGigEntry.start_time) {
          displayStartTime = myGigEntry.start_time;
          displayEndTime = myGigEntry.end_time;
        }
      }

      // Hover title from same logic as calendar bubbles
      const rowTooltip = gigClass === 'blast-open' ? '' : getGigHoverTitle(g, gigClass);

      // Pay: only show for own booked gig or open/bookable gig.
      // For multi-slot gigs, gig.pay is just slot 1's pay — misleading. So:
      //   - if artist is booked into a specific slot, show that slot's pay
      //     (preferring effective override the venue may have set for them)
      //   - otherwise, show a $min – $max range across slots if they differ;
      //     a single value if all slots have the same pay
      const canSeeDayPay = gigClass === 'booked-mine' || gigClass === 'open' || gigClass === 'blast-open' || gigClass === 'pending-venue-approval';
      let payDisplay = '—';
      if (canSeeDayPay) {
        const slots = g.slots || [];
        const isMulti = slots.length > 1;
        const effectivePay = getEffectivePay(g);
        if (isMulti) {
          if (gigClass === 'booked-mine') {
            const mySlot = slots.find(s => parseInt(s.artist_id) === parseInt(artistId));
            const myPay = (mySlot && parseFloat(mySlot.pay)) || effectivePay;
            payDisplay = myPay > 0 ? `$${myPay.toFixed(2)}` : '—';
          } else {
            const pays = slots.map(s => parseFloat(s.pay) || 0).filter(p => p > 0);
            if (pays.length) {
              const min = Math.min(...pays), max = Math.max(...pays);
              payDisplay = (min === max) ? `$${min.toFixed(2)}` : `$${min.toFixed(2)} – $${max.toFixed(2)}`;
            }
          }
        } else if (effectivePay > 0) {
          payDisplay = effectivePay > (parseFloat(g.pay) || 0)
            ? `<span style="color:#a855f7;">$${effectivePay.toFixed(2)}</span>`
            : `$${effectivePay.toFixed(2)}`;
        }
      }

      // Artist column: show who is booked, or OPEN, for any gig state.
      // Multi-slot needs to surface every booked artist (not just the first)
      // and indicate when some slots are still open. Mirrors venue day-list.
      let artistDisplay = '';
      const isOpenState = gigClass === 'open' || gigClass === 'blast-open' || gigClass === 'waitlist-pending' || gigClass === 'waitlisted-mine';

      const _slots = g.slots || [];
      const _isMulti = _slots.length > 1;
      const _bookedSlots = _slots.filter(s => s.artist_id && (s.status === 'booked' || s.status === 'pending_contract'));
      const _openCount = _slots.filter(s => s.status === 'open').length;
      // Escape aName before innerHTML injection — protects against malicious
      // registered names. esc() comes from security.js loaded on this page.
      const _renderArtistLink = (aId, aName) => aId
        ? `<a href="/app/artist-profile.html?artist_id=${aId}" target="_blank" onclick="event.stopPropagation()" style="color:${gigTextColor || '#fff'};text-decoration:underline;font-weight:600;">${esc(aName)}</a>`
        : `<span style="font-weight:600;">${esc(aName)}</span>`;

      if (_isMulti && _bookedSlots.length > 0) {
        const _names = _bookedSlots.map(s => _renderArtistLink(s.artist_id, s.artist_name || 'Booked')).join(', ');
        const _openBadge = _openCount > 0
          ? `<span style="opacity:0.7;font-weight:500;margin-left:6px;">· ${_openCount} open</span>`
          : '';
        artistDisplay = _names + _openBadge;
      } else if (_isMulti && _bookedSlots.length === 0 && _openCount > 0) {
        artistDisplay = `<span style="opacity:0.75;">OPEN · ${_openCount} slots</span>`;
      } else if (isOpenState) {
        artistDisplay = `<span style="opacity:0.75;">OPEN</span>`;
      } else {
        const resolvedArtistId = g.artist_id || (_bookedSlots[0] && _bookedSlots[0].artist_id) || null;
        const resolvedArtistName = g.artist_name || (_bookedSlots[0] && _bookedSlots[0].artist_name) || null;
        if (resolvedArtistName) {
          artistDisplay = _renderArtistLink(resolvedArtistId, resolvedArtistName);
        } else if (gigClass === 'booked-other' || gigClass === 'pending-contract') {
          artistDisplay = `<span style="opacity:0.75;">Booked</span>`;
        }
      }

      content += `
        <div
          onclick="openGigFromDayModal(${g.id})"
          class="gig ${gigClass} gig-row"
          ${rowTooltip ? `title="${rowTooltip.replace(/"/g, '&quot;')}"` : ''}
          style="
            display: grid;
            grid-template-columns: 130px minmax(130px,1fr) minmax(120px,1fr) minmax(120px,1fr) 80px minmax(150px,1.5fr);
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
          <div>${({'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠'}[g.artist_type] || '🎵') + ' '}${formatTime12Hour(displayStartTime)}${displayEndTime ? ' \u2013 ' + formatTime12Hour(displayEndTime) : ''}</div>

          <div style="overflow:hidden;text-overflow:ellipsis;">${esc(g.venue_name)}</div>

          <div style="overflow:hidden;text-overflow:ellipsis;">${esc(g.city || '')}${g.state ? ', ' + esc(g.state) : ''}</div>

          <div style="overflow:hidden;text-overflow:ellipsis;">${artistDisplay}</div>

          <div>${payDisplay}</div>

          <div style="overflow:hidden;text-overflow:ellipsis;">
            ${esc(g.artist_type || 'Any')}${g.artist_type === 'Live Band' && g.band_formats ? ' • ' + esc(g.band_formats.split(',').map(f => f.trim()).join(', ')) : ''}${g.artist_type === 'Live Band' && g.styles ? ' • ' + esc(g.styles.split(',').map(s => s.trim()).join(', ')) : ''}
          </div>
        </div>
      `;
    });

    content += '</div>';

    body.innerHTML = content;

    // Put Close in #modalActions — same as all other modals, gives border-top + lower-right positioning
    const _dayMA = document.getElementById("modalActions");
    if (_dayMA) {
      _dayMA.innerHTML = `<button onclick="closeModal()" class="btn ghost">Close</button>`;
      _dayMA.style.display = '';
    }

    overlay.classList.remove("hidden");
  }
  
  
  // Helper function to open individual gig from day modal
  window.openGigFromDayModal = function(gigId) {
    const gig = gigs.find(g => g.id === gigId);
    if (gig) {
      // Close day modal first
      const overlay = document.getElementById("modalOverlay");
      const modal = overlay.querySelector(".modal");
      
      // Remove day-modal class to reset width
      modal.classList.remove("day-modal");
      
      // Close the overlay briefly, then open regular gig modal
      overlay.classList.add("hidden");
      
      // Open regular gig modal after a tiny delay to ensure clean transition
      setTimeout(() => {
        openGigModal(gig);
      }, 50);
    }
  };

  // Open gig modal for contract upload (e.g. from Activity Center "Upload Signed PDF" link)
  window.openGigModalForContractUpload = async function(gigId) {
    // Always reload to get fresh status (gig may have transitioned since page load)
    if (typeof loadGigs === 'function') await loadGigs();
    let gig = gigs.find(g => g.id === parseInt(gigId, 10));
    if (gig) openGigModal(gig);
  };

  /* ---------------- MODAL ---------------- */


  function formatBookingError(rawMsg) {
    if (!rawMsg) return 'An unexpected error occurred.';
    if (rawMsg.startsWith('You have a blackout on this date')) {
      const reason = rawMsg.replace('You have a blackout on this date:', '').trim();
      return `<strong>YOU HAVE BLOCK-OUT DAYS SET!</strong><br/><br/>You cannot book this gig because you have marked yourself unavailable${reason && reason !== 'marked as unavailable' ? ': <em>' + reason + '</em>' : ' on this date'}.<br/><br/>Remove your block-out days in your availability settings to book this gig.`;
    }
    if (rawMsg.startsWith('W9_REQUIRED')) {
      return `<strong>W-9 Required</strong><br/>${rawMsg.replace('W9_REQUIRED: ', '')}`;
    }
    return rawMsg;
  }

  async function openGigModal(gig) {
    const overlay = document.getElementById("modalOverlay");
    const modal   = overlay.querySelector(".modal");
    const body    = document.getElementById("modalBody");
    const titleEl = document.getElementById("modalTitle");
    const modalActions = document.getElementById("modalActions");
    if (modalActions) { modalActions.innerHTML = ''; modalActions.style.display = 'none'; }
    modal.classList.remove("day-modal");

    // Show loading state immediately
    overlay.classList.remove("hidden");
    body.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-muted);">Loading...</div>';

    // ── Fetch unified modal data from backend ──────────────────────────
    let data;
    try {
      data = await window.fetchModalData(gig.id, 'artist', artistId);
    } catch (e) {
      body.innerHTML = `<p style="color:#ef4444;padding:16px;">Failed to load gig details: ${e.message}</p>`;
      return;
    }

    // Update in-memory gigs array with fresh data
    const idx = gigs.findIndex(g => g.id === gig.id);
    if (idx >= 0) {
      gigs[idx] = { ...gigs[idx],
        status: data.status, has_active_waitlist: data.has_active_waitlist,
        is_blast_open: data.is_blast_open, slots: data.slots,
        contract_hold_artist_id: data.contract_hold_artist_id,
      };
      gig = gigs[idx];
    }

    // ── Title ──────────────────────────────────────────────────────────
    const titleText = data.is_past ? 'Past Gig Details'
                    : data.is_in_progress ? 'Gig In Progress'
                    : 'Gig Details';

    // ── Render via shared module ───────────────────────────────────────
    const result = await window.renderGigModal(data, {
      onClose: () => overlay.classList.add('hidden'),
      onBook:  (slotId, slotNum) => { /* handled by .book-slot-btn delegation below */ },
      onCancelSlot: (slotId, slotNum) => { /* handled by #cancelSlotBtn below */ },
      onCancelGig:  () => { /* handled by #cancelGig below */ },
      onMessage:    (gigId, venueName, aid) => openMessageModal(gigId, venueName, aid),
      onJoinWaitlist:  (gigId, aid) => joinWaitlist(gigId, aid),
      onLeaveWaitlist: (gigId, aid) => leaveWaitlist(gigId, aid),
      onRequestPreferred: (venueId, btnId) => { /* handled by #requestPreferred below */ },
      onRate: null,  // handled via _rate-venue-btn delegation
      onCountersign: (contractId) => window._doCountersign && window._doCountersign(contractId),
    });

    window.mountGigModal(result, body, titleText);
    window._gmCurrentGigId = data.id;

    // Separator after title
    setTimeout(() => {
      if (!titleEl.nextElementSibling?.classList.contains('modal-separator')) {
        const sep = document.createElement('div');
        sep.className = 'modal-separator';
        titleEl.after(sep);
      }
    }, 10);

    // ── Async venue rating ─────────────────────────────────────────────
    if (data.venue_id) {
      fetch(`/api/venues/${data.venue_id}/reviews/summary`, { credentials: 'include' })
        .then(r => r.ok ? r.json() : null)
        .then(s => {
          if (!s || !s.review_count) return;
          const el = body.querySelector(`#_venueRatingInline_${data.venue_id}`);
          if (el) el.textContent = `★ ${parseFloat(s.avg_rating).toFixed(1)} (${s.review_count})`;
        }).catch(() => {});
    }

    // ── Async flyer button ─────────────────────────────────────────────
    fetch(`/api/gigs/${data.id}/flyer/public`, { credentials: 'include' })
      .then(r => r.json()).then(flyerData => {
        if (!flyerData || (!flyerData.thumbnail_data && !flyerData.canvas_data && !flyerData.use_builtin)) return;
        window._abgFlyerData = window._abgFlyerData || {};
        window._abgFlyerData[data.id] = flyerData;
        window._abgShowFlyer = (gigId) => {
          if (typeof _showFlyerOverlay === 'function') _showFlyerOverlay((window._abgFlyerData||{})[gigId], 'flyerFullModal');
        };
        const actionsDiv = body.querySelector('#gigModalActionsRow > div') || body.querySelector('#gigModalActionsRow');
        const flyerBtn = document.createElement('button');
        flyerBtn.className = '_gig-btn _gig-btn-purple';
        flyerBtn.textContent = 'View Event Flyer';
        flyerBtn.onclick = () => window._abgShowFlyer(data.id);
        if (actionsDiv) {
          const cancelBtn = Array.from(actionsDiv.querySelectorAll('button')).find(b => b.id === 'cancelSlotBtn' || b.id === 'cancelGig');
          const closeBtn  = Array.from(actionsDiv.querySelectorAll('button')).find(b => b.textContent.trim() === 'Close');
          if (cancelBtn) actionsDiv.insertBefore(flyerBtn, cancelBtn);
          else if (closeBtn) actionsDiv.insertBefore(flyerBtn, closeBtn);
          else actionsDiv.appendChild(flyerBtn);
        }
      }).catch(() => {});

    // ── Wire event handlers after DOM is set ──────────────────────────

    // Request Preferred Status
    const requestBtn = document.getElementById("requestPreferred");
    if (requestBtn) {
      requestBtn.onclick = async () => {
        requestBtn.disabled = true;
        requestBtn.textContent = 'Sending Request...';
        try {
          await apiPost(`/api/venues/${data.venue_id}/preferred/request?artist_id=${artistId}`, {});
          overlay.classList.add("hidden");
          await loadPreferredVenues();
          if (window.activityCenter) await window.activityCenter.loadNotifications();
          if (window.myVenuesRedesign) { await myVenuesRedesign.loadVenues(); myVenuesRedesign.render(); }
          showSuccessModal("Request Sent!", "Preferred status request sent to venue!");
          renderCalendar();
        } catch (e) {
          requestBtn.disabled = false;
          requestBtn.textContent = 'Ask Venue for Preferred Status';
          alert("Request failed: " + e.message);
        }
      };
    }

    // Book slot buttons (delegated — rendered per slot row)
    body.querySelectorAll('.book-slot-btn').forEach(btn => {
      btn.onclick = async () => {
        if (typeof window.checkArtistPaymentMethod === 'function' && !window.checkArtistPaymentMethod()) return;
        const slotId  = btn.dataset.slotId;
        const slotNum = btn.dataset.slotNum;

        // Contract check
        let contractInfo = null;
        try {
          const cRes = await fetch(`/api/venues/${data.venue_id}/contracts/active`, { credentials: 'include' });
          if (cRes.ok) {
            const cd = await cRes.json();
            if (cd.has_contract) contractInfo = { venueId: data.venue_id, contractType: cd.contract_type, required: cd.require_for_booking };
          }
        } catch (_) {}

        if (contractInfo && contractInfo.required) {
          btn.disabled = true; btn.textContent = 'Loading...';
          try {
            const prevRes = await fetch(`/api/gigs/${data.id}/contract-preview?artist_id=${artistId}`, { credentials: 'include' });
            if (!prevRes.ok) throw new Error('Failed to load contract');
            const preview = await prevRes.json();
            window._pendingSlotBooking = { gigId: data.id, slotId, slotNum, artistId };
            if (preview.contract_type === 'custom_builder' || preview.contract_type === 'auto_generated') {
              overlay.classList.add("hidden"); showContractSigningModal(gig, preview, artistId); return;
            } else if (preview.contract_type === 'pdf_upload' && preview.per_gig_pdf) {
              overlay.classList.add("hidden"); showPerGigPdfModal(gig, artistId); return;
            } else if (preview.contract_type === 'pdf_upload') {
              overlay.classList.add("hidden"); showPdfContractModal(gig, preview, artistId); return;
            }
          } catch (e) {
            btn.disabled = false; btn.textContent = 'Book';
            showStyledModal('Error', `<p style="color:#ef4444;">Failed to load contract: ${e.message}</p>`, [{text:'OK',style:'ghost'}]);
            return;
          }
        }

        // Standard slot book
        btn.disabled = true; btn.textContent = 'Booking...';
        try {
          const res = await apiPost(`/api/gigs/${data.id}/slots/${slotId}/book?artist_id=${artistId}${window._blastToken ? "&blast_token=" + window._blastToken : ""}`, {});
          overlay.classList.add("hidden");
          await loadGigs(); await loadMyGigs();
          if (window.activityCenter) await window.activityCenter.loadNotifications();
          if (window.myVenuesRedesign) { await myVenuesRedesign.loadVenues(); myVenuesRedesign.render(); }
          if (res && res.pending_approval) {
            showSuccessModal("Request Sent!", `Your same-day booking request for Slot ${slotNum} has been sent to the venue.`);
          } else {
            showSuccessModal("Slot Booked!", `You booked Slot ${slotNum}!`);
          }
          renderCalendar();
          if (typeof window.loadArtistEarningsHistory === 'function') window.loadArtistEarningsHistory();
        } catch (e) {
          btn.disabled = false; btn.textContent = 'Book';
          const msg = e.message || 'Booking failed';
          const formatted = typeof formatBookingError === 'function' ? formatBookingError(msg) : msg;
          showStyledModal('Booking Unavailable',
            `<div style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:16px;"><p style="color:#ef4444;margin:0;text-align:center;">${formatted}</p></div>`,
            [{text:'OK',style:'ghost'}]);
        }
      };
    });

    // Cancel My Slot
    const cancelSlotBtn = document.getElementById('cancelSlotBtn');
    if (cancelSlotBtn) {
      cancelSlotBtn.onclick = async () => {
        const slotId  = cancelSlotBtn.dataset.slotId;
        const slotNum = cancelSlotBtn.dataset.slotNum;
        const slotCancelOverlay = document.getElementById('slotCancelModalOverlay');
        document.getElementById('slotCancelReason').value = '';
        slotCancelOverlay.classList.remove('hidden');
        document.getElementById('confirmSlotCancelBtn').onclick = async () => {
          const reason = (document.getElementById('slotCancelReason').value || '').trim() || 'No reason provided';
          slotCancelOverlay.classList.add('hidden');
          cancelSlotBtn.disabled = true; cancelSlotBtn.textContent = 'Cancelling...';
          try {
            const res = await fetch(`/api/gigs/${data.id}/slots/${slotId}/cancel`, {
              method: 'DELETE', headers: {'Content-Type':'application/json'}, credentials: 'include',
              body: JSON.stringify({ cancelled_by: 'artist', cancellation_reason: reason })
            });
            if (!res.ok) {
              // FIX (May 2026 audit #7): surface FastAPI's {detail} body so the
              // artist sees the real reason (auth, server error, etc.) instead
              // of a silent button-reset. Mirrors the venue path.
              let detail = `HTTP ${res.status}`;
              try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
              throw new Error(detail);
            }
            overlay.classList.add("hidden");
            await loadGigs(); await loadMyGigs();
            if (window.myVenuesRedesign) { await myVenuesRedesign.loadVenues(); myVenuesRedesign.render(); }
            if (window.activityCenter) await window.activityCenter.loadNotifications();
            showSuccessModal("Slot Cancelled", `Slot ${slotNum} has been released.`);
            renderCalendar();
          } catch (e) {
            cancelSlotBtn.disabled = false; cancelSlotBtn.textContent = 'Cancel My Slot';
            showStyledModal('Cancellation Failed',
              `<p style="color:#ef4444;">${(e && e.message) || 'Could not cancel the slot. Please try again.'}</p>`,
              [{text:'OK',style:'ghost'}]);
          }
        };
      };
    }

    // Cancel Gig (whole gig — single-slot or pending contract)
    const cancelGigBtn = document.getElementById("cancelGig");
    if (cancelGigBtn) {
      cancelGigBtn.onclick = () => {
        overlay.classList.add("hidden");
        const cancelModal  = document.getElementById("cancelModalOverlay");
        const cancelReason = document.getElementById("cancelReason");
        const confirmBtn   = document.getElementById("confirmCancelBtn");
        cancelReason.value = "";
        cancelModal.classList.remove("hidden");
        confirmBtn.onclick = async () => {
          const reason = cancelReason.value.trim() || "No reason provided";
          confirmBtn.disabled = true; confirmBtn.textContent = 'Cancelling...';
          try {
            const resp = await fetch(`/api/gigs/${data.id}/cancel`, {
              method: "DELETE", headers: {"Content-Type":"application/json"}, credentials: "include",
              body: JSON.stringify({ cancelled_by: "artist", cancellation_reason: reason, artist_id: parseInt(artistId) })
            });
            if (!resp.ok) {
              // Audit fix (May 2026): surface FastAPI's {detail} body so the
              // artist sees the real reason. Slot-cancel sibling above was
              // upgraded earlier — this whole-gig path was missed.
              let detail = `HTTP ${resp.status}`;
              try { const j = await resp.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
              throw new Error(detail);
            }
            cancelModal.classList.add("hidden");
            await loadGigs(); await loadMyGigs();
            await new Promise(r => setTimeout(r, 200));
            await loadGigs(); await loadMyGigs();
            if (typeof window.loadArtistEarningsHistory === 'function') await window.loadArtistEarningsHistory();
            if (typeof loadArtistContracts === 'function') await loadArtistContracts();
            if (window.myVenuesRedesign) { await myVenuesRedesign.loadVenues(); myVenuesRedesign.render(); }
            if (window.activityCenter) await window.activityCenter.loadNotifications();
            showSuccessModal("Gig Cancelled", "The gig has been cancelled.");
            renderCalendar();
          } catch (e) {
            console.error(e);
            showStyledModal('Cancellation Failed',
              `<p style="color:#ef4444;">${(e && e.message) || 'Could not cancel the gig. Please try again.'}</p>`,
              [{text:'OK',style:'ghost'}]);
          } finally {
            // Audit fix (May 2026): always re-enable the button so users
            // aren't stuck staring at "Cancelling…" if anything in the
            // success path hangs.
            confirmBtn.disabled = false; confirmBtn.textContent = 'Confirm Cancellation';
          }
        };
      };
    }

    // Waitlist leave buttons (._wl-decline-btn)
    body.querySelectorAll('._wl-decline-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = 'Leaving…';
        try {
          const res = await fetch(`/api/gigs/${btn.dataset.gigId}/waitlist?artist_id=${btn.dataset.artistId}`,
            { method: 'DELETE', credentials: 'include' });
          if (!res.ok) {
            // Audit fix (May 2026): surface FastAPI's {detail} so the artist
            // sees why leaving the waitlist failed (e.g. authz issue) instead
            // of a silent button reset.
            let detail = `HTTP ${res.status}`;
            try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
            showStyledModal('Could Not Leave Waitlist',
              `<p style="color:#ef4444;">${detail}</p>`,
              [{text:'OK',style:'ghost'}]);
            return;
          }
          overlay.classList.add('hidden');
          await loadGigs(); renderCalendar();
          if (typeof window.loadArtistWaitlists === 'function') window.loadArtistWaitlists();
        } catch (e) {
          showStyledModal('Could Not Leave Waitlist',
            `<p style="color:#ef4444;">${(e && e.message) || 'Network error.'}</p>`,
            [{text:'OK',style:'ghost'}]);
        } finally {
          btn.disabled = false; btn.textContent = 'Leave Waitlist';
        }
      });
    });

    // Rate venue buttons
    body.querySelectorAll('._rate-venue-btn').forEach(rateVenueBtn => {
      const _rGigId    = rateVenueBtn.dataset.gigId;
      const _rVenueId  = rateVenueBtn.dataset.venueId;
      const _rArtistId = rateVenueBtn.dataset.artistId;
      const _rVenueName = rateVenueBtn.dataset.venueName || 'Venue';
      (async () => {
        try {
          const chk = await fetch(`/api/artists/${_rArtistId}/venues/${_rVenueId}/review`, { credentials: 'include' });
          if (chk.ok) {
            const d = await chk.json();
            if (d.reviewed) {
              rateVenueBtn.textContent = '✏️ Edit Review';
              rateVenueBtn.dataset.existingRating = d.rating;
              rateVenueBtn.dataset.existingText = d.review_text || '';
            }
          }
        } catch (_) {}
      })();
      rateVenueBtn.addEventListener('click', () => {
        if (typeof openVenueRateModal === 'function') {
          openVenueRateModal(parseInt(_rVenueId), _rVenueName, parseInt(_rArtistId),
            parseInt(rateVenueBtn.dataset.existingRating)||0,
            rateVenueBtn.dataset.existingText||'', rateVenueBtn);
        }
      });
    });
  }

  /* ---------------- INIT ---------------- */

  prevBtn.onclick = () => {
    currentDate.setDate(1); // Fix: Set to 1st to prevent month skipping
    currentDate.setMonth(currentDate.getMonth() - 1);
    renderCalendar();
  };

  nextBtn.onclick = () => {
    currentDate.setDate(1); // Fix: Set to 1st to prevent month skipping
    currentDate.setMonth(currentDate.getMonth() + 1);
    renderCalendar();
  };
  
  // Second calendar buttons (for search tab)
  if (prevBtn2) {
    prevBtn2.onclick = () => {
      currentDate.setDate(1); // Fix: Set to 1st to prevent month skipping
      currentDate.setMonth(currentDate.getMonth() - 1);
      renderCalendar();
    };
  }
  
  if (nextBtn2) {
    nextBtn2.onclick = () => {
      currentDate.setDate(1); // Fix: Set to 1st to prevent month skipping
      currentDate.setMonth(currentDate.getMonth() + 1);
      renderCalendar();
    };
  }

  // Search/Filter functionality - v73: Only apply on Enter
  document.getElementById('applyFilters').onclick = applyFilters;
  document.getElementById('clearFilters').onclick = () => {
    document.getElementById('searchVenue').value = '';
    document.getElementById('searchCity').value = '';
    document.getElementById('minPay').value = '0';
    document.getElementById('mileRadius').value = ''; // Clear mile radius
    
    // Reset amenity toggle buttons
    document.querySelectorAll('.amenity-toggle').forEach(btn => {
      btn.setAttribute('data-active', 'false');
      btn.textContent = 'Any';
      btn.style.background = 'rgba(255,255,255,0.05)';
      btn.style.borderColor = 'rgba(255,255,255,0.2)';
      btn.style.color = 'var(--text-muted)';
    });
    
    // Reset artist type filters
    document.querySelectorAll('.artist-type-filter-toggle').forEach(btn => {
      btn.setAttribute('data-active', 'false');
      btn.style.background = 'rgba(255,255,255,0.05)';
      btn.style.borderColor = 'rgba(255,255,255,0.2)';
      btn.style.color = 'var(--text-muted)';
    });
    
    // Hide and reset band format filters
    const bandFormatContainer = document.getElementById('bandFormatBubblesArtist');
    if (bandFormatContainer) {
      bandFormatContainer.style.display = 'none';
    }
    
    document.querySelectorAll('.band-format-filter-toggle').forEach(btn => {
      btn.setAttribute('data-active', 'false');
      btn.style.background = 'rgba(255,255,255,0.05)';
      btn.style.borderColor = 'rgba(255,255,255,0.2)';
      btn.style.color = 'var(--text-muted)';
    });
    
    filters.venue = '';
    filters.city = '';
    filters.cityCoords = null;
    filters.minPay = 0;
    filters.mileRadius = 20;
    gigs = [...allGigs];
    
    // Auto-apply filters after clearing
    applyFilters();
  };

  // v93: Auto-apply filters on text input changes (with debounce)
  const searchVenueInput = document.getElementById('searchVenue');
  const searchCityInput = document.getElementById('searchCity');
  const minPayInput = document.getElementById('minPay');
  const mileRadiusInput = document.getElementById('mileRadius');
  
  searchVenueInput.addEventListener('input', () => {
    clearTimeout(searchVenueInput.filterTimeout);
    searchVenueInput.filterTimeout = setTimeout(() => {
      applyFilters();
    }, 300);
  });
  
  searchCityInput.addEventListener('input', () => {
    clearTimeout(searchCityInput.filterTimeout);
    searchCityInput.filterTimeout = setTimeout(() => {
      applyFilters();
    }, 300);
  });
  
  minPayInput.addEventListener('input', () => {
    clearTimeout(minPayInput.filterTimeout);
    minPayInput.filterTimeout = setTimeout(() => {
      applyFilters();
    }, 300);
  });
  
  mileRadiusInput.addEventListener('input', () => {
    applyFilters();
  });
  
  // v73: Enter key applies filters
  const filterInputs = ['searchVenue', 'searchCity', 'minPay', 'mileRadius'];
  filterInputs.forEach(id => {
    const input = document.getElementById(id);
    if (input) {
      input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          applyFilters();
        }
      });
    }
  });
  
  // v73: Clear min pay on focus (using existing minPayInput const)
  minPayInput.addEventListener('focus', () => {
    if (minPayInput.value === '0') {
      minPayInput.value = '';
    }
  });
  minPayInput.addEventListener('blur', () => {
    if (minPayInput.value === '') {
      minPayInput.value = '0';
    }
  });

  // Venue autocomplete
  const venueInput = document.getElementById('searchVenue');
  const autocompleteDiv = document.getElementById('venueAutocomplete');
  let allVenues = []; // Store unique venue names

  // Extract unique venues from gigs
  function updateVenueList() {
    const venueSet = new Set();
    allGigs.forEach(gig => {
      if (gig.venue_name) {
        venueSet.add(gig.venue_name);
      }
    });
    allVenues = Array.from(venueSet).sort();
  }

  venueInput.addEventListener('input', (e) => {
    const value = e.target.value.toLowerCase().trim();
    
    if (!value) {
      autocompleteDiv.style.display = 'none';
      return;
    }

    const matches = allVenues.filter(venue => 
      venue.toLowerCase().includes(value)
    );

    if (matches.length === 0) {
      autocompleteDiv.style.display = 'none';
      return;
    }

    autocompleteDiv.innerHTML = matches.map(venue => `
      <div style="
        padding: 10px 12px;
        cursor: pointer;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        transition: background 0.2s;
      "
      onmouseover="this.style.background='rgba(255,255,255,0.08)'"
      onmouseout="this.style.background='transparent'"
      onclick="selectVenue('${venue.replace(/'/g, "\\'")}')">
        ${venue}
      </div>
    `).join('');

    autocompleteDiv.style.display = 'block';
  });

  // v94: Add Enter key support for venue search
  venueInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      autocompleteDiv.style.display = 'none';
      applyFilters();
    }
  });

  // Close autocomplete when clicking outside
  document.addEventListener('click', (e) => {
    if (!venueInput.contains(e.target) && !autocompleteDiv.contains(e.target)) {
      autocompleteDiv.style.display = 'none';
    }
  });

  // Global function to select venue from autocomplete
  window.selectVenue = function(venueName) {
    venueInput.value = venueName;
    autocompleteDiv.style.display = 'none';
    // v94: Auto-apply filters when venue is selected
    applyFilters();
  };

  // City autocomplete with arrow key navigation
  const cityInput = document.getElementById('searchCity');
  const cityAutocompleteDiv = document.getElementById('cityAutocomplete');
  let cityAutocompleteIndex = -1;
  let cityAutocompleteMatches = [];

  // Load cities on page load
  async function loadCities() {
    try {
      allCities = await apiGet('/api/cities/all');
    } catch (error) {
      console.error('Failed to load cities:', error);
      allCities = [];
    }
  }

  cityInput.addEventListener('input', (e) => {
    const value = e.target.value.toLowerCase().trim();
    
    if (!value || value.length < 2) {
      cityAutocompleteDiv.style.display = 'none';
      cityAutocompleteIndex = -1;
      cityAutocompleteMatches = [];
      return;
    }

    const matches = allCities.filter(city => 
      city.city.toLowerCase().includes(value) ||
      city.state.toLowerCase() === value
    ).slice(0, 10); // Limit to 10 results

    if (matches.length === 0) {
      cityAutocompleteDiv.style.display = 'none';
      cityAutocompleteIndex = -1;
      cityAutocompleteMatches = [];
      return;
    }

    cityAutocompleteMatches = matches;
    cityAutocompleteIndex = -1; // Reset selection when new matches appear
    
    renderCityAutocomplete();
    cityAutocompleteDiv.style.display = 'block';
  });
  
  // Helper function to render city autocomplete with highlight
  function renderCityAutocomplete() {
    cityAutocompleteDiv.innerHTML = cityAutocompleteMatches.map((city, index) => {
      const isHighlighted = index === cityAutocompleteIndex;
      return `
        <div 
          data-city-index="${index}"
          style="
            padding: 10px 12px;
            cursor: pointer;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            transition: background 0.2s;
            background: ${isHighlighted ? 'rgba(255,255,255,0.08)' : 'transparent'};
          "
          onmouseover="this.style.background='rgba(255,255,255,0.08)'"
          onmouseout="this.style.background='${isHighlighted ? 'rgba(255,255,255,0.08)' : 'transparent'}'"
          onclick="selectCity('${city.city}', '${city.state}', ${city.lat}, ${city.lon})">
          <strong>${city.city}</strong>, ${city.state}
        </div>
      `;
    }).join('');
  }
  
  // Add keyboard navigation for city autocomplete
  cityInput.addEventListener('keydown', (e) => {
    if (cityAutocompleteDiv.style.display === 'none' || cityAutocompleteMatches.length === 0) {
      return;
    }
    
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      cityAutocompleteIndex = (cityAutocompleteIndex + 1) % cityAutocompleteMatches.length;
      renderCityAutocomplete();
      
      // Scroll into view if needed
      const highlighted = cityAutocompleteDiv.querySelector(`[data-city-index="${cityAutocompleteIndex}"]`);
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
      const highlighted = cityAutocompleteDiv.querySelector(`[data-city-index="${cityAutocompleteIndex}"]`);
      if (highlighted) {
        highlighted.scrollIntoView({ block: 'nearest' });
      }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      
      if (cityAutocompleteIndex >= 0 && cityAutocompleteIndex < cityAutocompleteMatches.length) {
        // Select the highlighted city
        const selectedCity = cityAutocompleteMatches[cityAutocompleteIndex];
        selectCity(selectedCity.city, selectedCity.state, selectedCity.lat, selectedCity.lon);
      } else {
        // No selection, just close and apply filters
        cityAutocompleteDiv.style.display = 'none';
        applyFilters();
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cityAutocompleteDiv.style.display = 'none';
      cityAutocompleteIndex = -1;
    }
  });

  // Close city autocomplete when clicking outside
  document.addEventListener('click', (e) => {
    if (!cityInput.contains(e.target) && !cityAutocompleteDiv.contains(e.target)) {
      cityAutocompleteDiv.style.display = 'none';
    }
  });

  // Global function to select city from autocomplete
  window.selectCity = function(cityName, state, lat, lon) {
    cityInput.value = cityName;
    filters.city = cityName.toLowerCase();
    filters.cityCoords = { lat, lon };
    cityAutocompleteDiv.style.display = 'none';
    // v94: Auto-apply filters when city is selected
    applyFilters();
  };

  function applyFilters() {
    const cityInput = document.getElementById('searchCity').value.trim();
    
    filters = {
      venue: document.getElementById('searchVenue').value.toLowerCase(),
      city: cityInput.toLowerCase(),
      minPay: parseFloat(document.getElementById('minPay').value) || 0,
      mileRadius: parseFloat(document.getElementById('mileRadius').value) || 20,
      hasStage: document.getElementById('hasStage').getAttribute('data-active') === 'true',
      hasSoundEquipment: document.getElementById('hasSoundEquipment').getAttribute('data-active') === 'true',
      hasLighting: document.getElementById('hasLighting').getAttribute('data-active') === 'true'
    };

    // If city was typed but we don't have coords, look it up
    if (filters.city && !filters.cityCoords) {
      const cityMatch = allCities.find(c => 
        c.city.toLowerCase() === filters.city
      );
      if (cityMatch) {
        filters.cityCoords = { lat: cityMatch.lat, lon: cityMatch.lon };
      }
    }

    let filtered = [...allGigs];

    // v94: If venue name is entered, ONLY filter by venue - ignore all other criteria
    if (filters.venue) {
      filtered = filtered.filter(g => g.venue_name && g.venue_name.toLowerCase().includes(filters.venue));
      
      // Update display and return early
      gigs = filtered;
      renderCalendar();
      
      const resultsEl = document.getElementById('searchResults');
      resultsEl.textContent = `Found ${filtered.length} gig${filtered.length !== 1 ? 's' : ''} at ${filters.venue}`;
      return;
    }

    // If no venue name, apply other filters independently

    // Filter by minimum pay (uses effective pay including venue overrides)
    if (filters.minPay > 0) {
      filtered = filtered.filter(g => {
        const effectivePay = getEffectivePay(g);
        return effectivePay >= filters.minPay;
      });
    }
    
    // Filter by amenities
    if (filters.hasStage) {
      filtered = filtered.filter(g => {
        const hasStage = g.has_stage === 1 || g.has_stage === true;
        return hasStage;
      });
    }
    if (filters.hasSoundEquipment) {
      filtered = filtered.filter(g => g.has_sound_equipment === 1 || g.has_sound_equipment === true);
    }
    if (filters.hasLighting) {
      filtered = filtered.filter(g => g.has_lighting === 1 || g.has_lighting === true);
    }
    
    // Filter by artist type
    const activeArtistTypes = Array.from(document.querySelectorAll('.artist-type-filter-toggle[data-active="true"]')).map(btn => btn.getAttribute('data-type'));
    if (activeArtistTypes.length > 0) {
      filtered = filtered.filter(g => {
        // Multi-slot gigs: always show — each slot has its own artist_type
        if (g.slots && g.slots.length > 0) return true;
        // Gig must accept one of the active types
        if (!g.artist_type || g.artist_type === 'Any') {
          return true; // Gig accepts any type
        }
        return activeArtistTypes.includes(g.artist_type);
      });
    }
    
    // Filter by band format (only if Live Band is active)
    const isLiveBandActive = document.querySelector('.artist-type-filter-toggle[data-type="Live Band"]')?.getAttribute('data-active') === 'true';
    if (isLiveBandActive) {
      const activeBandFormats = Array.from(document.querySelectorAll('.band-format-filter-toggle[data-active="true"]')).map(btn => btn.getAttribute('data-format'));
      if (activeBandFormats.length > 0) {
        filtered = filtered.filter(g => {
          // Multi-slot gigs: always show — each slot has its own band_formats
          if (g.slots && g.slots.length > 0) return true;
          // Only filter Live Band gigs
          if (g.artist_type !== 'Live Band') {
            return true; // Not a Live Band gig, don't filter
          }
          if (!g.band_formats) {
            return true; // Gig accepts any format
          }
          // Check if gig accepts at least one of the active formats
          const gigFormats = g.band_formats.split(',').map(f => f.trim());
          return gigFormats.some(format => activeBandFormats.includes(format));
        });
      }
    }

    // Filter by city and radius
    if (filters.city && filters.cityCoords) {
      // We have coordinates - do true radius filtering
      filtered = filtered.filter(g => {
        if (!g.venue_lat || !g.venue_lon) {
          // No coords: can't determine distance — include it so it's not hidden
          return true;
        }
        
        // v94: Log coordinates for debugging
        
        // Calculate distance
        const distance = calculateDistance(
          filters.cityCoords.lat,
          filters.cityCoords.lon,
          g.venue_lat,
          g.venue_lon
        );
        
        const inRange = distance <= filters.mileRadius;
        return inRange;
      });
    } else if (filters.city) {
      // No coordinates, just match by city name
      filtered = filtered.filter(g => {
        if (!g.city) return false;
        const gigCity = g.city.toLowerCase().trim();
        return gigCity.includes(filters.city) || filters.city.includes(gigCity);
      });
    }

    gigs = filtered;
    renderCalendar();
    
    const resultsEl = document.getElementById('searchResults');
    
    if (filters.city) {
      // Capitalize city name for display (filters.city is lowercase for matching)
      const cityDisplay = filters.city.split(' ').map(word => 
        word.charAt(0).toUpperCase() + word.slice(1)
      ).join(' ');
      resultsEl.textContent = `Found ${filtered.length} gig${filtered.length !== 1 ? 's' : ''} within ${filters.mileRadius} miles of ${cityDisplay}`;
    } else {
      resultsEl.textContent = `Found ${filtered.length} gig${filtered.length !== 1 ? 's' : ''} matching your filters`;
    }
  }

  // Haversine distance calculation (miles)
  function calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 3956; // Earth radius in miles
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
  }

  // REMOVED: renderNotifications - now handled by activity-center.js

  // Load cities for autocomplete
  await loadCities();

  // v73: Load artist info and APPLY city filter by default
  try {
    const artistInfo = await apiGet(`/api/artists/${artistId}`);
    if (artistInfo && artistInfo.city) {
      // Set default city in the input field (visible to user)
      const cityMatch = allCities.find(c => 
        c.city.toLowerCase() === artistInfo.city.toLowerCase()
      );
      
      if (cityMatch) {
        // v73: Store for Clear Filters
        artistDefaultCity = {
          name: cityMatch.city,
          coords: { lat: cityMatch.lat, lon: cityMatch.lon }
        };
        
        document.getElementById('searchCity').value = cityMatch.city;
        // v73: SET filters to activate by default
        filters.city = cityMatch.city.toLowerCase();
        filters.cityCoords = { lat: cityMatch.lat, lon: cityMatch.lon };
      }
    }
  } catch (error) {
  }

  await loadGigs();
  await loadMyGigs();
  await loadVenueFrequencies();
  await loadPreferredVenues();
  await loadArtistData(); // v94: Load artist data for type matching
  await loadVenueBlastSettings(); // fetch blink/blast prefs for all venues in gig list
  
  // If the authenticated user doesn't have access to this artist profile
  // (e.g. URL was edited to another artist_id), show a blocking message and stop.
  if (!artistData) {
    showNoArtistAccessAndStop();
    return;
  }
  
  // Update venue autocomplete list
  updateVenueList();
  
  // v73: Apply filters immediately on page load
  applyFilters();

  // ── Deep link: declined_gig param — artist just clicked "Not Available" in email ──
  const declinedGigId = params.get('declined_gig');
  if (declinedGigId) {
    // Close any stale modal, clear URL param, reload gigs fresh
    const _ov = document.getElementById('modalOverlay');
    if (_ov) _ov.classList.add('hidden');
    // Strip URL params so refresh doesn't re-trigger
    window.history.replaceState({}, '', window.location.pathname + (params.get('artist_id') ? '?artist_id=' + params.get('artist_id') : ''));
    // Reload gigs to get fresh has_active_waitlist state
    await loadGigs();
    await loadArtistWaitlists();
    renderCalendar();
  }

  // ── Deep link: auto-open gig modal if open_gig param is in URL ─────────
  const openGigId = params.get('open_gig');
  const blastToken = params.get('blast_token');
  if (openGigId) {
    if (blastToken) window._blastToken = blastToken;
    let targetGig = gigs.find(g => g.id === parseInt(openGigId, 10));
    if (targetGig) {
      const gigDate = new Date(targetGig.date + 'T12:00:00');
      currentDate = new Date(gigDate.getFullYear(), gigDate.getMonth(), 1);
      renderCalendar();
      setTimeout(() => openGigModal(targetGig), 150);
    } else {
      // Gig not in local array — fetch it directly (may be a past/booked/different-month gig)
      console.warn(`[deep-link] Gig ${openGigId} not found locally, fetching directly`);
      apiGet(`/api/gigs/${openGigId}/detail`).then(gig => {
        if (gig && gig.id) {
          const gigDate = new Date(gig.date + 'T12:00:00');
          currentDate = new Date(gigDate.getFullYear(), gigDate.getMonth(), 1);
          renderCalendar();
          setTimeout(() => openGigModal(gig), 150);
        } else {
          console.warn(`[deep-link] Gig ${openGigId} not found via direct fetch either`);
        }
      }).catch(e => console.warn('[deep-link] Direct fetch failed:', e));
    }
  }

  // =====================================================
  // LOAD MY VENUES
  // =====================================================
  
  let allVenuesData = [];
  let filteredVenuesData = [];
  
  document.querySelectorAll('.amenity-toggle').forEach(btn => {
    btn.addEventListener('click', function() {
      const isActive = this.getAttribute('data-active') === 'true';
      
      if (isActive) {
        // Turn off - show "Any" again
        this.setAttribute('data-active', 'false');
        this.textContent = 'Any';
        this.style.background = 'rgba(255,255,255,0.05)';
        this.style.borderColor = 'rgba(255,255,255,0.2)';
        this.style.color = 'var(--text-muted)';
      } else {
        // Turn on - show "✓ Yes"
        this.setAttribute('data-active', 'true');
        this.textContent = '✓ Yes';
        this.style.background = 'rgba(16, 185, 129, 0.2)';
        this.style.borderColor = '#10b981';
        this.style.color = '#10b981';
      }
      
      // v73: Auto-apply filters when amenity is toggled
      applyFilters();
    });
  });
  
  // Artist Type Filter Handling (for Search Gigs)
  document.querySelectorAll('.artist-type-filter-toggle').forEach(btn => {
    btn.addEventListener('click', function() {
      const isActive = this.getAttribute('data-active') === 'true';
      const type = this.getAttribute('data-type');
      
      if (isActive) {
        // Turn off
        this.setAttribute('data-active', 'false');
        this.style.background = 'rgba(255,255,255,0.05)';
        this.style.borderColor = 'rgba(255,255,255,0.2)';
        this.style.color = 'var(--text-muted)';
      } else {
        // Turn on
        this.setAttribute('data-active', 'true');
        this.style.background = 'rgba(34, 197, 94, 0.2)';
        this.style.borderColor = 'rgba(34, 197, 94, 0.5)';
        this.style.color = '#22c55e';
      }
      
      // Show/hide band format bubbles
      const bandFormatContainer = document.getElementById('bandFormatBubblesArtist');
      const liveBandBtn = document.querySelector('.artist-type-filter-toggle[data-type="Live Band"]');
      const isLiveBandActive = liveBandBtn && liveBandBtn.getAttribute('data-active') === 'true';
      
      if (bandFormatContainer) {
        bandFormatContainer.style.display = isLiveBandActive ? 'block' : 'none';
      }
      
      applyFilters();
    });
  });
  
  // Band Format Filter Handling (for Search Gigs)
  document.querySelectorAll('.band-format-filter-toggle').forEach(btn => {
    btn.addEventListener('click', function() {
      const isActive = this.getAttribute('data-active') === 'true';
      
      if (isActive) {
        // Turn off
        this.setAttribute('data-active', 'false');
        this.style.background = 'rgba(255,255,255,0.05)';
        this.style.borderColor = 'rgba(255,255,255,0.2)';
        this.style.color = 'var(--text-muted)';
      } else {
        // Turn on
        this.setAttribute('data-active', 'true');
        this.style.background = 'rgba(34, 197, 94, 0.2)';
        this.style.borderColor = 'rgba(34, 197, 94, 0.5)';
        this.style.color = '#22c55e';
      }
      
      applyFilters();
    });
  });
  
  // Set default artist type filters based on artist's profile
  if (artistData) {
    // Activate the artist's type
    if (artistData.artist_type) {
      const typeBtn = document.querySelector(`.artist-type-filter-toggle[data-type="${artistData.artist_type}"]`);
      if (typeBtn) {
        typeBtn.setAttribute('data-active', 'true');
        typeBtn.style.background = 'rgba(34, 197, 94, 0.2)';
        typeBtn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
        typeBtn.style.color = '#22c55e';
      }
      
      // Show band format bubbles if Live Band
      if (artistData.artist_type === 'Live Band') {
        const bandFormatContainer = document.getElementById('bandFormatBubblesArtist');
        if (bandFormatContainer) {
          bandFormatContainer.style.display = 'block';
        }
        
        // Set artist's lineup
        if (artistData.band_formats) {
          const formats = artistData.band_formats.split(',').map(f => f.trim());
          
          // First, turn off all formats
          document.querySelectorAll('.band-format-filter-toggle').forEach(btn => {
            btn.setAttribute('data-active', 'false');
            btn.style.background = 'rgba(255,255,255,0.05)';
            btn.style.borderColor = 'rgba(255,255,255,0.2)';
            btn.style.color = 'var(--text-muted)';
          });
          
          // Then activate only the artist's formats
          formats.forEach(format => {
            const formatBtn = document.querySelector(`.band-format-filter-toggle[data-format="${format}"]`);
            if (formatBtn) {
              formatBtn.setAttribute('data-active', 'true');
              formatBtn.style.background = 'rgba(34, 197, 94, 0.2)';
              formatBtn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
              formatBtn.style.color = '#22c55e';
            }
          });
        }
      }
    }
  }
  
  // Auto-apply filters on page load to show filtered calendar
  applyFilters();
  
  // Load venues on page load
  // v73: Removed - using my-venues-redesign.js
  
  // v73: Expose openGigModal on window for My Venues
  window.openGigModal = openGigModal;
  // Expose applyFilters globally for preferred venue search
  window.applyFilters = applyFilters;
  // Expose functions needed by contract signing modals (defined outside DOMContentLoaded)
  window.showSuccessModal = showSuccessModal;
  // NOTE (Phase 2 May 2026): no longer publishing showStyledModal to window.
  // The canonical version is gf-modals.js's window.showStyledModal; this
  // module's local showStyledModal is now a thin adapter that delegates
  // there with legacy-button-shape mapping + auto-tone for error titles.
  // Reassigning window.showStyledModal here would point window at the
  // adapter, causing infinite recursion when the adapter calls window's
  // version. External callers (gig-modal.js etc.) get the canonical helper
  // directly from window — they don't need the adapter's auto-tone logic.
  window.loadGigs = loadGigs;
  window.loadMyGigs = loadMyGigs;
  window.renderCalendar = renderCalendar;
  // Expose gigs array so waitlist functions (defined on window) can access it
  Object.defineProperty(window, '_abgGigs', { get: () => gigs, configurable: true });

  // Auto-refresh gig data every 60s so waitlist changes, cancellations, and new bookings
  // are reflected without requiring a manual page refresh
  setInterval(async () => {
    try {
      await loadGigs();
      await loadMyGigs();
      await loadVenueBlastSettings();
      renderCalendar();
    } catch (_e) {}
  }, 60000);
});

// Phase 2 migration (May 2026): the old dom-builder version of this helper
// shipped its own inline cssText (purple-bordered gradient card with hard-
// coded fonts/colors) — see git blame for the v73 original. Now delegates
// to the canonical window.showStyledModal in gf-modals.js so this page's
// popups inherit the unified look. Two adaptations:
//   1. Legacy button shape was {text, style, action}; new is
//      {text, style, onClick}. Map action→onClick and default style.
//   2. Older callers don't pass opts. Auto-tone modals whose title contains
//      a negative-action keyword (Error / Failed / Cancellation / Could Not
//      / Unavailable / etc.) so errors visually telegraph as red. Caller-
//      supplied opts.tone always wins.
function showStyledModal(title, content, buttons, opts) {
  const adapted = (buttons || []).map(b => ({
    text:    b.text,
    style:   b.style || 'primary',
    onClick: b.action || b.onClick,
  }));
  let mergedOpts = opts || {};
  if (!mergedOpts.tone && /(error|fail|cancel|unavailable|invalid|denied|could ?not|cannot)/i.test(String(title || ''))) {
    mergedOpts = Object.assign({}, mergedOpts, { tone: 'error' });
  }
  return window.showStyledModal(title, content, adapted, mergedOpts);
}

// ============================================
// CONTRACT SIGNING MODALS — shown during booking flow
// ============================================

/**
 * Digital Contract Signing Modal (builder / auto_generated)
 */
function showContractSigningModal(gig, preview, artistId) {
  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.85); z-index:10002; display:flex; align-items:flex-start; justify-content:center; padding-top:30px; overflow-y:auto;';

  // Render HTML contract body safely
  const bodyHtml = preview.rendered_body || '';

  modal.innerHTML = `
    <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%); border:2px solid rgba(124,107,255,0.4); border-radius:12px; padding:24px; max-width:750px; width:95%; max-height:90vh; display:flex; flex-direction:column; box-shadow:0 8px 32px rgba(124,107,255,0.3);">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:16px;">
        <h2 style="margin:0; font-size:1.1rem; color:#fff;">📋 Contract — Review & Sign to Book</h2>
        <button class="btn ghost" onclick="this.closest('[style*=position]').remove()" style="padding:4px 12px; font-size:0.8rem;">✕ Close</button>
      </div>
      <p style="font-size:0.8rem; color:var(--text-gray); margin:0 0 12px;">Read the contract below carefully. Type your full legal name to sign and confirm your booking.</p>
      <div style="flex:1; overflow-y:auto; background:#0a0e14; border:1px solid var(--border); border-radius:8px; padding:20px; margin-bottom:16px; font-size:0.85rem; line-height:1.7; color:#e5e5e5; min-height:200px; max-height:50vh;">${bodyHtml}</div>
      <div style="border-top:1px solid var(--border); padding-top:16px;">
        <label style="font-size:0.85rem; color:var(--cyan); display:block; margin-bottom:8px;">Sign by typing your full legal name:</label>
        <input type="text" id="contractSignatureName" placeholder="Your Full Legal Name" style="width:100%; padding:10px 14px; font-size:1rem; font-style:italic; margin-bottom:12px; background:#0a0e14; border:1px solid var(--border); border-radius:8px; color:#fff;">
        <div style="display:flex; gap:12px; justify-content:flex-end;">
          <button class="btn ghost" onclick="this.closest('[style*=position]').remove()">Cancel</button>
          <button class="btn primary" id="contractSignAndBookBtn">Sign & Book This Gig</button>
        </div>
        <div id="contractSignStatus" style="font-size:0.8rem; margin-top:8px; text-align:right;"></div>
      </div>
    </div>`;

  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });

  const signBtn = modal.querySelector('#contractSignAndBookBtn');
  signBtn.onclick = async () => {
    const sigName = modal.querySelector('#contractSignatureName').value.trim();
    const statusEl = modal.querySelector('#contractSignStatus');
    if (!sigName) { statusEl.textContent = 'Please type your full legal name to sign.'; statusEl.style.color = '#ef4444'; return; }
    if (sigName.length < 2) { statusEl.textContent = 'Please enter your full name.'; statusEl.style.color = '#ef4444'; return; }

    signBtn.disabled = true;
    signBtn.textContent = 'Signing & Booking...';
    statusEl.textContent = '';

    try {
      const bookingPayload = { artist_id: artistId, signature_name: sigName };
      // If this was triggered from a slot booking, include slot_id
      if (window._pendingSlotBooking) {
        bookingPayload.slot_id = window._pendingSlotBooking.slotId;
      }
      const res = await fetch(`/api/gigs/${gig.id}/book-with-contract${window._blastToken ? "?blast_token=" + window._blastToken : ""}`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bookingPayload)
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Booking failed');
      }
      modal.remove();
      window._pendingContractInfo = null;
      const wasSlotBooking = !!window._pendingSlotBooking;
      window._pendingSlotBooking = null;
      if (typeof window.showSuccessModal === 'function') {
        window.showSuccessModal("Gig Booked & Contract Signed!", wasSlotBooking ? "Your slot booking is confirmed." : "Your booking is confirmed. The venue will be notified.");
        // Refresh calendar behind modal
        if (typeof window.loadGigs === 'function') await window.loadGigs();
        if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
        if (typeof window.renderCalendar === 'function') window.renderCalendar();
      }
      if (typeof window.loadGigs === 'function') await window.loadGigs();
      if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
      if (window.activityCenter) await window.activityCenter.loadNotifications();
      if (typeof window.renderCalendar === 'function') window.renderCalendar();
      if (typeof window.loadArtistEarningsHistory === 'function') window.loadArtistEarningsHistory();
    } catch (e) {
      signBtn.disabled = false;
      signBtn.textContent = 'Sign & Book This Gig';
      statusEl.textContent = e.message;
      statusEl.style.color = '#ef4444';
    }
  };
}

/**
 * Standard PDF Contract Modal - Books gig with 24hr hold
 */
function showPdfContractModal(gig, preview, artistId) {
  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.85); z-index:10002; display:flex; align-items:center; justify-content:center;';

  const pdfUrl = preview.pdf_url || '';
  const contractName = preview.name || 'Contract';

  modal.innerHTML = `
    <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%); border:2px solid rgba(124,107,255,0.4); border-radius:12px; padding:24px; max-width:500px; width:95%; box-shadow:0 8px 32px rgba(124,107,255,0.3);">
      <h2 style="margin:0 0 16px; font-size:1.1rem; color:#fff;">📋 Contract Required — PDF Signature</h2>
      <div style="background:rgba(234,179,8,0.1); border:1px solid rgba(234,179,8,0.3); border-radius:8px; padding:14px; margin-bottom:16px;">
        <p style="color:#eab308; margin:0; font-size:0.85rem; line-height:1.6;">
          <strong>You have 24 hours</strong> to download, sign, and upload this contract to confirm your booking.
        </p>
      </div>
      <div style="margin-bottom:16px;">
        <p style="font-size:0.85rem; color:var(--text-gray); margin:0 0 4px;">Contract: <strong style="color:#fff;">${contractName}</strong></p>
      </div>
      <div id="pdfUploadArea" style="display:none; margin-bottom:16px;">
        <p style="font-size:0.8rem; color:var(--text-gray); margin:0 0 8px;">Upload your signed contract (PDF only):</p>
        <label class="btn primary" style="padding:8px 24px; font-size:0.85rem; cursor:pointer; display:inline-block; border-radius:8px;">
          ⬆ Upload Signed Contract PDF
          <input type="file" accept=".pdf" id="pdfSignedUploadInput" style="display:none;">
        </label>
        <div id="pdfUploadStatus" style="font-size:0.8rem; margin-top:6px;"></div>
      </div>
      <div style="display:flex; gap:12px; justify-content:flex-end; border-top:1px solid var(--border); padding-top:16px;">
        <button class="btn ghost" onclick="this.closest('[style*=position]').remove()">Cancel</button>
        <button class="btn primary" id="pdfHoldAndBookBtn">Hold Gig & Download Contract</button>
      </div>
      <div id="pdfBookStatus" style="font-size:0.8rem; margin-top:8px; text-align:right;"></div>
    </div>`;

  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });

  const holdBtn = modal.querySelector('#pdfHoldAndBookBtn');
  holdBtn.onclick = async () => {
    holdBtn.disabled = true;
    holdBtn.textContent = 'Booking...';
    const statusEl = modal.querySelector('#pdfBookStatus');

    try {
      const pdfPayload = { artist_id: artistId };
      if (window._pendingSlotBooking) pdfPayload.slot_id = window._pendingSlotBooking.slotId;
      const res = await fetch(`/api/gigs/${gig.id}/book-with-contract${window._blastToken ? "?blast_token=" + window._blastToken : ""}`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pdfPayload)
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Booking failed');
      }
      const data = await res.json();
      window._pendingContractInfo = null;

      // Replace modal content with clean download/upload layout (matches gig details modal)
      const innerBox = modal.querySelector('div > div');
      if (innerBox) {
        innerBox.innerHTML = `
          <h2 style="margin:0 0 16px; font-size:1.1rem; color:#fff;">📋 Contract Signature Required</h2>
          <div style="padding: 14px 18px; background: rgba(234, 179, 8, 0.1); border: 1px solid rgba(234, 179, 8, 0.3); border-radius: 8px;">
            <p style="color: #eab308; margin: 0 0 6px 0; font-size: 0.9rem; font-weight: 600;">
              ✓ Gig Held Successfully
            </p>
            <p style="color: var(--text-muted); margin: 0 0 4px 0; font-size: 0.85rem; line-height: 1.5;">
              Download, print, sign, and upload the contract PDF to confirm your booking.
            </p>
            <p style="color: var(--text-muted); margin: 0 0 14px 0; font-size: 0.8rem; font-style: italic; opacity: 0.8;">
              (This gig is held for 24 hours so you can sign and upload completed contract.)
            </p>
            <div style="display:flex; flex-direction:column; gap:10px; align-items:center;">
              <a href="${pdfUrl || ''}" download class="btn" style="padding:8px 24px; font-size:0.85rem; background:rgba(139,92,246,0.2); border:1px solid rgba(139,92,246,0.4); color:#c4b5fd; border-radius:8px; text-decoration:none; display:inline-block;">⬇ Download Contract PDF</a>
              <label class="btn primary" style="padding:8px 24px; font-size:0.85rem; cursor:pointer; border-radius:8px;">
                ⬆ Upload Signed Contract PDF
                <input type="file" accept=".pdf" id="pdfSignedUploadInput2" style="display:none;">
              </label>
              <span style="font-size:0.75rem; color:var(--text-muted); opacity:0.7;">PDF files only</span>
            </div>
            <div id="pdfUploadStatus2" style="font-size:0.8rem; margin-top:8px; text-align:center;"></div>
          </div>
          <div style="display:flex; justify-content:flex-end; border-top:1px solid var(--border); padding-top:16px; margin-top:16px;">
            <button class="btn ghost" onclick="this.closest('[style*=position]').remove()">Close</button>
          </div>
        `;
        // Wire up new upload input
        const newUpload = document.getElementById('pdfSignedUploadInput2');
        if (newUpload) {
          newUpload.onchange = async () => {
            if (!newUpload.files.length) return;
            const st = document.getElementById('pdfUploadStatus2');
            st.innerHTML = '<span style="color:var(--text-gray);">Uploading...</span>';
            const fd = new FormData();
            fd.append('file', newUpload.files[0]);
            try {
              const r = await fetch('/api/gig-contracts/' + data.contract_id + '/upload-signed', {
                method: 'POST', credentials: 'include', body: fd
              });
              if (!r.ok) { const e = await r.json().catch(()=>({})); throw new Error(e.detail || 'Upload failed'); }
              st.innerHTML = '<span style="color:#22c55e;">✓ Contract uploaded! Booking confirmed.</span>';
              setTimeout(() => modal.remove(), 1500);
              if (typeof window.loadGigs === 'function') await window.loadGigs();
              if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
              if (typeof window.renderCalendar === 'function') window.renderCalendar();
              if (window.activityCenter) await window.activityCenter.loadNotifications();
            } catch (e) {
              st.innerHTML = '<span style="color:#ef4444;">✗ ' + e.message + '</span>';
            }
          };
        }
      }

      // Refresh calendar behind modal so gig shows correct status
      if (typeof window.loadGigs === 'function') await window.loadGigs();
      if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
      if (typeof window.renderCalendar === 'function') window.renderCalendar();
      if (window.activityCenter) await window.activityCenter.loadNotifications();

      if (pdfUrl) { const a = document.createElement('a'); a.href = pdfUrl; a.download = ''; document.body.appendChild(a); a.click(); a.remove(); }

      const uploadInput = modal.querySelector('#pdfSignedUploadInput');
      uploadInput.onchange = async () => {
        if (!uploadInput.files.length) return;
        const uploadStatus = modal.querySelector('#pdfUploadStatus');
        uploadStatus.innerHTML = '<span style="color:var(--text-gray);">Uploading...</span>';
        const formData = new FormData();
        formData.append('file', uploadInput.files[0]);
        try {
          const upRes = await fetch(`/api/gig-contracts/${data.contract_id}/upload-signed`, {
            method: 'POST', credentials: 'include', body: formData
          });
          if (!upRes.ok) {
            const err = await upRes.json().catch(() => ({}));
            const msg = Array.isArray(err.detail) ? (err.detail[0] && err.detail[0].msg) || 'Upload failed' : (err.detail || 'Upload failed');
            throw new Error(msg);
          }
          uploadStatus.innerHTML = '<span style="color:#22c55e;">✓ Contract uploaded! Booking confirmed.</span>';
          setTimeout(() => modal.remove(), 1500);
          if (typeof window.loadGigs === 'function') await window.loadGigs();
          if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
          if (typeof window.renderCalendar === 'function') window.renderCalendar();
          if (window.activityCenter) await window.activityCenter.loadNotifications();
        } catch (e) {
          uploadStatus.innerHTML = `<span style="color:#ef4444;">✗ ${esc(e.message)}</span>`;
        }
      };
    } catch (e) {
      holdBtn.disabled = false;
      holdBtn.textContent = 'Hold Gig & Download Contract';
      statusEl.innerHTML = `<span style="color:#ef4444;">${esc(e.message)}</span>`;
    }
  };
}

/**
 * Per-Gig PDF Contract Modal - 48hr hold, venue uploads
 */
function showPerGigPdfModal(gig, artistId) {
  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.85); z-index:10002; display:flex; align-items:center; justify-content:center;';

  modal.innerHTML = `
    <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%); border:2px solid rgba(124,107,255,0.4); border-radius:12px; padding:24px; max-width:480px; width:95%; box-shadow:0 8px 32px rgba(124,107,255,0.3);">
      <h2 style="margin:0 0 16px; font-size:1.1rem; color:#fff;">📋 Contract Required — Venue-Specific</h2>
      <div style="background:rgba(6,182,212,0.08); border:1px solid rgba(6,182,212,0.2); border-radius:8px; padding:14px; margin-bottom:16px;">
        <p style="color:#67e8f9; margin:0; font-size:0.85rem; line-height:1.6;">
          This venue prepares a <strong>unique contract for each gig</strong>. The venue will have <strong>48 hours</strong> to upload your contract. You'll be notified when it's ready.
        </p>
      </div>
      <p style="font-size:0.8rem; color:var(--text-gray); line-height:1.5; margin:0 0 16px;">
        After the venue uploads the contract, you'll have <strong>24 hours</strong> to download, sign, and upload it.
      </p>
      <div style="display:flex; gap:12px; justify-content:flex-end; border-top:1px solid var(--border); padding-top:16px;">
        <button class="btn ghost" onclick="this.closest('[style*=position]').remove()">Cancel</button>
        <button class="btn primary" id="perGigRequestBtn">Request Booking</button>
      </div>
      <div id="perGigBookStatus" style="font-size:0.8rem; margin-top:8px; text-align:right;"></div>
    </div>`;

  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });

  const reqBtn = modal.querySelector('#perGigRequestBtn');
  reqBtn.onclick = async () => {
    reqBtn.disabled = true;
    reqBtn.textContent = 'Requesting...';
    const statusEl = modal.querySelector('#perGigBookStatus');

    try {
      const pgPayload = { artist_id: artistId };
      if (window._pendingSlotBooking) pgPayload.slot_id = window._pendingSlotBooking.slotId;
      const res = await fetch(`/api/gigs/${gig.id}/book-with-contract${window._blastToken ? "?blast_token=" + window._blastToken : ""}`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pgPayload)
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Request failed');
      }
      window._pendingContractInfo = null;
      modal.remove();
      if (typeof window.showSuccessModal === 'function') window.showSuccessModal("Booking Requested!", "The venue has been notified to upload your contract.");
      // Refresh calendar so gig shows correct pending status
      if (typeof window.loadGigs === 'function') await window.loadGigs();
      if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
      if (typeof window.loadGigs === 'function') await window.loadGigs();
      if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
      if (typeof window.renderCalendar === 'function') window.renderCalendar();
    } catch (e) {
      reqBtn.disabled = false;
      reqBtn.textContent = 'Request Booking';
      statusEl.innerHTML = `<span style="color:#ef4444;">${esc(e.message)}</span>`;
    }
  };
}

/**
 * Upload signed PDF from gig detail modal
 */
window._uploadSignedPdf = async function(input, contractId) {
  if (!input.files.length) return;
  const statusEl = document.getElementById('uploadSignedStatus');
  if (statusEl) statusEl.innerHTML = '<span style="color:var(--text-gray);">Uploading...</span>';
  const formData = new FormData();
  formData.append('file', input.files[0]);
  try {
    const res = await fetch(`/api/gig-contracts/${contractId}/upload-signed`, {
      method: 'POST', credentials: 'include', body: formData
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Upload failed');
    }
    if (statusEl) statusEl.innerHTML = '<span style="color:#22c55e;">✓ Contract uploaded! Booking confirmed.</span>';
    setTimeout(async () => {
      if (typeof window.loadGigs === 'function') await window.loadGigs();
      if (typeof window.loadMyGigs === 'function') await window.loadMyGigs();
      if (typeof window.renderCalendar === 'function') window.renderCalendar();
      if (window.activityCenter) await window.activityCenter.loadNotifications();
    }, 1000);
  } catch (e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:#ef4444;">✗ ${esc(e.message)}</span>`;
  }
};
// ─── WAITLIST ────────────────────────────────────────────────────────────────

function showWaitlistError(msg, gigId) {
  // Show styled error inside the modal if open, else fallback
  const body = document.getElementById('modalBody');
  if (body) {
    const existing = body.querySelector('#waitlistErrorBanner');
    if (existing) existing.remove();
    const banner = document.createElement('div');
    banner.id = 'waitlistErrorBanner';
    banner.style.cssText = 'background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.4);border-radius:8px;padding:10px 14px;margin-top:10px;display:flex;align-items:center;justify-content:space-between;gap:10px;';
    banner.innerHTML = `<span style="color:#ef4444;font-size:0.85rem;">⚠️ ${msg}</span><button onclick="this.parentElement.remove()" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:1.1rem;padding:0;">×</button>`;
    body.appendChild(banner);
  }
}

window.joinWaitlist = async function(gigId, artistId) {
  try {
    const res = await fetch(`/api/gigs/${gigId}/waitlist/join?artist_id=${artistId}`, {
      method: 'POST', credentials: 'include'
    });
    const data = await res.json();
    if (res.ok && (data.status === 'joined' || data.status === 'already_on_waitlist')) {
      // Re-open the gig modal to reflect updated state (modal fetches fresh waitlist status on open)
      if (typeof window.openGigFromDayModal === 'function') window.openGigFromDayModal(gigId);
      if (typeof window.loadArtistWaitlists === 'function') window.loadArtistWaitlists();
      if (window.myVenuesRedesign) { await window.myVenuesRedesign.loadVenues(); window.myVenuesRedesign.render(); }
    } else {
      if (typeof showErrorModal === 'function') showErrorModal(data.detail || 'Could not join waitlist');
      else showWaitlistError(data.detail || 'Could not join waitlist', gigId);
    }
  } catch(e) {
    if (typeof showErrorModal === 'function') showErrorModal('Error joining waitlist');
    else showWaitlistError('Error joining waitlist', gigId);
  }
};

window.leaveWaitlist = async function(gigId, artistId) {
  try {
    const res = await fetch(`/api/gigs/${gigId}/waitlist?artist_id=${artistId}`, {
      method: 'DELETE', credentials: 'include'
    });
    if (res.ok) {
      // Close modal and refresh
      const _ov = document.getElementById('modalOverlay');
      if (_ov) _ov.classList.add('hidden');
      // Immediately remove from local waitlist set so bubble updates at once
      if (window._myWaitlistGigIds) window._myWaitlistGigIds.delete(gigId);
      if (typeof renderCalendar === 'function') renderCalendar();
      if (typeof loadGigs === 'function') await loadGigs();
      if (typeof renderCalendar === 'function') renderCalendar();
      if (typeof window.loadArtistWaitlists === 'function') window.loadArtistWaitlists();
    } else {
      if (typeof showStyledModal === 'function') {
        showStyledModal('Error', '<p style="color:#ef4444;text-align:center;">Failed to leave waitlist — please try again.</p>', [{text:'OK',style:'ghost'}]);
      } else { alert('Failed to leave waitlist — please try again'); }
    }
  } catch(e) {
    if (typeof showStyledModal === 'function') {
      showStyledModal('Error', '<p style="color:#ef4444;text-align:center;">Something went wrong. Please try again.</p>', [{text:'OK',style:'ghost'}]);
    } else { alert('Error leaving waitlist'); }
  }
};

window.loadArtistWaitlists = async function() {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get('artist_id');
  const container = document.getElementById('artistWaitlistSection');
  if (!artistId || !container) return;

  try {
    const res = await fetch(`/api/artists/${artistId}/waitlist`, { credentials: 'include' });
    if (!res.ok) return;
    const items = await res.json();

    if (!items || items.length === 0) {
      window._myWaitlistGigIds = new Set();
      container.innerHTML = '<p style="color:var(--text-gray);font-size:0.85rem;">You\'re not on any waitlists.</p>';
      return;
    }

    // Store waitlisted gig IDs for bubble color logic
    window._myWaitlistGigIds = new Set(items.map(w => w.gig_id));

    container.innerHTML = items.map(w => {
      const pos = w.position || '?';
      const total = w.total_waiting || 1;
      const dateStr = w.date || '';
      const pay = w.pay ? `$${parseFloat(w.pay).toFixed(0)}` : '';
      const time = w.start_time ? formatTime12Hour(w.start_time) : '';
      return `
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;padding:10px 14px;background:rgba(139,92,246,0.08);border:1px solid rgba(139,92,246,0.2);border-radius:8px;margin-bottom:8px;">
          <div>
            <div style="font-size:0.85rem;font-weight:600;color:var(--text);">${esc(w.venue_name || 'Venue')}</div>
            <div style="font-size:0.75rem;color:var(--text-gray);margin-top:2px;">${dateStr}${time ? ' · ' + time : ''}${pay ? ' · ' + pay : ''}${w.artist_type ? ' · ' + w.artist_type : ''}</div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:0.75rem;color:#a78bfa;">${pos} of ${total}</span>
            <button onclick="leaveWaitlist(${w.gig_id}, ${artistId})" style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#ef4444;padding:4px 10px;border-radius:5px;font-size:0.72rem;cursor:pointer;">Leave</button>
          </div>
        </div>`;
    }).join('');
  } catch(e) { console.error('Waitlist load error:', e); }
};
