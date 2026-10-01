/**
 * Artist profile — redesign prototype (2026-10-01).
 *
 * Same endpoints as artist-profile.html; no backend changes. The difference
 * is structural: one scroll with anchored sections instead of tab panels, so
 * nothing a venue needs is reachable only by clicking.
 *
 * Sections render in decision order — see them, read them, check they're
 * free, see what they play, contact them. Empty sections remove themselves
 * from the nav rather than offering a link to nothing.
 */
(function () {
  "use strict";

  var esc = window.esc || function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };

  var params = new URLSearchParams(window.location.search);
  var artistId = params.get("artist_id");

  // Nav is built from what actually rendered, so a band with no setlist
  // doesn't get a "What They Play" link that scrolls to an empty block.
  var SECTIONS = [
    { id: "__cal",       label: "Calendar", action: "v2OpenCal()" },
    { id: "sec-watch",   label: "Watch" },
    { id: "sec-photos",  label: "Photos" },
    { id: "sec-about",   label: "About" },
    { id: "sec-dates",   label: "Dates" },
    { id: "sec-setlist", label: "Setlist" },
    { id: "sec-contact", label: "Contact" }
  ];
  var present = {};

  function $(id) { return document.getElementById(id); }

  async function getJSON(url) {
    try {
      var r = await fetch(url, { credentials: "include" });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  function show(id, on) { var el = $(id); if (el) el.style.display = on ? "" : "none"; }

  // ── hero ────────────────────────────────────────────────────────────
  function renderHero(a, media) {
    document.title = (a.name || "Artist") + " – GigsFill";
    window.__v2ArtistName = a.name || "Artist";
    $("v2Name").textContent = a.name || "Artist";

    // The artist's own photo becomes the page, not a thumbnail on ours.
    // Prefer a real picture; fall back to the profile image; failing both,
    // the CSS gradient stands in rather than a flat block.
    var pics = media.filter(function (m) { return m.media_type === "picture" && m.file_path; });
    var profile = media.filter(function (m) { return m.media_type === "profile" && m.file_path; })[0];
    var heroSrc = (pics[0] && pics[0].file_path) || (profile && profile.file_path) || "";
    if (heroSrc) {
      $("v2HeroBg").style.backgroundImage = 'url("' + heroSrc + '")';
    } else {
      $("v2Hero").classList.add("no-photo");
    }
    if (profile && profile.file_path) $("v2Logo").src = profile.file_path;
    else if (pics[0]) $("v2Logo").src = pics[0].file_path;
    $("v2Logo").alt = (a.name || "Artist") + " logo";

    var loc = [a.city, a.state].filter(Boolean).join(", ");
    var bits = [];
    if (loc) bits.push(esc(loc));
    if (a.artist_type) bits.push(esc(a.artist_type));
    $("v2Sub").innerHTML = bits.join('<span class="dot">•</span>');

    // Styles and lineups as chips. These are what a venue filters on, so
    // they belong beside the name rather than inside a tab.
    // Two rows, styles above lineups. They answer different questions —
    // "what do they sound like" then "how many of them turn up" — and
    // running them together as one wrapped row blurs the distinction.
    function chipRow(csv, cls) {
      var items = (csv || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
      if (!items.length) return "";
      return '<div class="v2-chip-row">' + items.map(function (x) {
        return '<span class="v2-chip ' + cls + '">' + esc(x) + "</span>";
      }).join("") + "</div>";
    }
    $("v2Chips").innerHTML = chipRow(a.styles, "accent") + chipRow(a.band_formats, "");

    // Rating only when it exists. "0 reviews" reads as a negative signal
    // for a new artist who simply hasn't been reviewed yet.
    var cta = "";
    var rating = Number(a.avg_rating || 0);
    var count = Number(a.review_count || 0);
    if (rating > 0 && count > 0) {
      var full = Math.round(rating);
      cta += '<span class="v2-rating"><span class="v2-stars">' +
             "★".repeat(full) + "☆".repeat(Math.max(0, 5 - full)) +
             "</span><strong>" + rating.toFixed(1) + "</strong>" +
             '<span style="color:var(--v2-dim);">(' + count + ")</span></span>";
    }
    $("v2Cta").innerHTML = cta;   // action is appended by renderAction()
  }

  // Relationship badge sits with the name, where a venue reads it as a fact
  // about this artist rather than as another button to weigh up.
  function setNameBadge(text, tone) {
    var el = $("v2NameBadge");
    if (!el) return;
    el.className = "v2-name-badge" + (tone ? " " + tone : "");
    el.textContent = text;
    el.style.display = "inline-flex";
  }

  // ── the action ──────────────────────────────────────────────────────
  // What this says depends entirely on who's reading it. A generic "Book
  // This Artist" is wrong for everyone: a venue that already works with
  // them doesn't want to be sold, and a logged-out visitor can't act on it.
  //
  //   venue, already approved  → state the relationship, no action
  //   venue, invitation sent   → say so; nothing more to do
  //   venue, artist applied    → send them to the approval screen
  //   venue, no relationship   → invite
  //   anyone else              → point at the contact details
  async function renderAction(a) {
    var slot = document.createElement("span");
    $("v2Cta").appendChild(slot);

    function fallback() {
      // Anchors down to the contact block. Marked so it is NOT mirrored
      // there — beside the actual phone number and email it would read
      // "Contact for Booking" directly under "Contact: ...".
      slot.outerHTML = '<a class="v2-btn primary" data-noMirror="1" href="#sec-contact">Contact for Booking</a>';
    }

    var me = await getJSON("/api/me");
    var venues = (me && me.venues) || [];
    if (!venues.length) { fallback(); return; }

    var venueId = me.venue_id || venues[0].id;
    var rel = await getJSON("/api/venues/" + venueId + "/preferred-artists-with-gigs");
    if (!rel) { fallback(); return; }

    var row = rel.filter(function (r) {
      return Number(r.artist_id) === Number(artistId);
    })[0];
    var status = row ? row.preferred_status : null;

    if (status === "approved") {
      // Already a fact beside the name — repeating it as a CTA-sized block
      // would be the page telling them something they just read.
      setNameBadge("★ Your Preferred Artist", "");
      slot.outerHTML = "";
    } else if (status === "invited") {
      setNameBadge("Invitation Sent", "wait");
      slot.outerHTML = "";
    } else if (status === "pending") {
      // They asked first — approving is the right move, not inviting.
      slot.outerHTML = '<a class="v2-btn primary" href="/app/venue-create-gigs.html?venue_id=' +
        encodeURIComponent(venueId) + '&tab=artists">Review Their Request</a>';
    } else {
      slot.outerHTML = '<button class="v2-btn primary" id="v2InviteBtn" ' +
        'onclick="v2Invite(' + Number(venueId) + ')">Invite as Preferred Artist</button>';
    }
  }

  window.v2Invite = async function (venueId) {
    var btn = $("v2InviteBtn");
    if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
    try {
      var r = await fetch("/api/venues/" + venueId + "/artists/" + artistId + "/make-preferred", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ message: "" })
      });
      if (!r.ok) throw new Error((await r.json().catch(function () { return {}; })).detail || "Failed");
      // Invitations grant nothing until the artist accepts, so the label
      // says "sent", not "preferred".
      if (btn) btn.outerHTML = '<span class="v2-status wait">Invitation Sent</span>';
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = "Invite as Preferred Artist"; }
      alert("Could not send the invitation: " + (e.message || "please try again"));
    }
  };

  // Which platform a video lives on, and whether it serves a public still.
  function platformOf(url) {
    var u = String(url || "");
    if (/instagram\.com/i.test(u))               return { name: "Instagram", noThumb: true };
    if (/tiktok\.com/i.test(u))                  return { name: "TikTok",    noThumb: true };
    if (/facebook\.com|fb\.watch/i.test(u))      return { name: "Facebook",  noThumb: true };
    if (/youtube\.com|youtu\.be/i.test(u))       return { name: "YouTube",   noThumb: false };
    if (/vimeo\.com/i.test(u))                   return { name: "Vimeo",     noThumb: false };
    return { name: "Video", noThumb: true };
  }

  // ── videos ──────────────────────────────────────────────────────────
  function renderVideos(media) {
    var vids = media.filter(function (m) { return m.media_type === "video" && m.video_url; });
    $("v2VideoCount").textContent = vids.length ? vids.length + " videos" : "";
    if (!vids.length) { show("v2VideosEmpty", true); return; }
    present["sec-watch"] = true;

    $("v2Videos").innerHTML = vids.map(function (v) {
      var plat = platformOf(v.video_url);
      // Instagram, TikTok and Facebook expose no public thumbnail. The shared
      // helper fills that gap with a full-bleed branded gradient, which is
      // fine once and shouting when four sit in a row — identical loud tiles
      // that say nothing about the clip. Here they get a quiet plate with a
      // small badge instead, so the titles carry the information.
      var thumbInner = plat.noThumb
        ? '<div class="v2-thumb-blank"><span class="v2-badge">' + esc(plat.name) + "</span></div>"
        : '<img data-video-url="' + esc(v.video_url) + '" alt="">';
      return '<a class="v2-card" href="' + esc(v.video_url) + '" target="_blank" rel="noopener">' +
        '<div class="v2-thumb">' + thumbInner +
        '<div class="v2-play"><span></span></div></div>' +
        '<div class="v2-card-body">' +
          '<div class="v2-card-title">' + esc(v.title || "Untitled") + "</div>" +
          (v.caption ? '<div class="v2-card-sub">' + esc(v.caption) + "</div>" : "") +
        "</div></a>";
    }).join("");

    // Real stills only where a platform actually serves one.
    if (window.attachVideoThumb) {
      $("v2Videos").querySelectorAll("img[data-video-url]").forEach(function (img) {
        window.attachVideoThumb(img, img.getAttribute("data-video-url"));
      });
    }
  }

  // ── photos ──────────────────────────────────────────────────────────
  function renderPhotos(media) {
    var pics = media.filter(function (m) { return m.media_type === "picture" && m.file_path; });
    $("v2PhotoCount").textContent = pics.length ? pics.length + " photos" : "";
    if (!pics.length) { show("v2PhotosEmpty", true); return; }
    present["sec-photos"] = true;
    if (pics.length <= 3) $("v2Photos").classList.add("few");

    $("v2Photos").innerHTML = pics.map(function (p) {
      return '<figure class="v2-photo" onclick="v2OpenLb(\'' + esc(p.file_path) + '\')">' +
        '<img src="' + esc(p.file_path) + '" alt="' + esc(p.title || "") + '" loading="lazy">' +
        (p.title ? "<figcaption>" + esc(p.title) + "</figcaption>" : "") +
        "</figure>";
    }).join("");
  }

  // ── about ───────────────────────────────────────────────────────────
  function renderAbout(a) {
    present["sec-about"] = true;
    $("v2Bio").textContent = a.bio || "No bio yet.";

    var facts = [];
    if (a.artist_type) facts.push(["Type", a.artist_type]);
    if (a.band_formats) facts.push(["Lineups", a.band_formats.split(",").join(", ")]);
    if (a.styles) facts.push(["Styles", a.styles.split(",").join(", ")]);
    if (a.city || a.state) facts.push(["Based in", [a.city, a.state].filter(Boolean).join(", ")]);
    // Only state equipment when it's a yes — "No" invents an objection the
    // venue may not have had.
    if (a.has_own_equipment) facts.push(["Own PA", "Yes"]);
    $("v2Facts").innerHTML = facts.map(function (f) {
      return '<div class="v2-fact"><dt>' + esc(f[0]) + "</dt><dd>" + esc(f[1]) + "</dd></div>";
    }).join("");
  }

  // ── dates ───────────────────────────────────────────────────────────
  var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

  function renderDates(gigs) {
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var upcoming = (gigs || []).filter(function (g) {
      if (!g.date) return false;
      var d = new Date(String(g.date) + "T00:00:00");
      return d >= today;
    }).sort(function (x, y) { return String(x.date).localeCompare(String(y.date)); });

    $("v2DateCount").textContent = upcoming.length ? upcoming.length + " booked" : "";
    if (!upcoming.length) { show("v2DatesEmpty", true); return; }
    present["sec-dates"] = true;

    $("v2Dates").innerHTML = upcoming.slice(0, 8).map(function (g) {
      var d = new Date(String(g.date) + "T00:00:00");
      var where = [g.venue_name, [g.city, g.state].filter(Boolean).join(", ")]
                  .filter(Boolean).join(" — ");
      var time = "";
      if (g.start_time && window.formatTime12Hour) {
        time = window.formatTime12Hour(g.start_time);
        if (g.end_time) time += " – " + window.formatTime12Hour(g.end_time);
      } else if (g.start_time) {
        time = g.start_time;
      }
      // Which lineup is booked for this date — a venue comparing dates
      // cares whether it's a solo set or the full band, and the gig row
      // already carries it.
      var fmt = (g.band_formats || g.artist_band_formats || "")
        .split(",").map(function (x) { return x.trim(); }).filter(Boolean).join(" / ");
      // Format lives in the pill on the right, so keep it out of the meta
      // line — it was printing twice on the same row.
      var meta = time;
      return '<div class="v2-date">' +
        '<div class="v2-date-when"><div class="v2-date-mon">' + MON[d.getMonth()] + "</div>" +
        '<div class="v2-date-day">' + d.getDate() + "</div></div>" +
        '<div class="v2-date-main"><div class="v2-date-venue">' + esc(where || "Private booking") + "</div>" +
        '<div class="v2-date-meta">' + esc(meta) + "</div></div>" +
        (fmt ? '<span class="v2-date-fmt">' + esc(fmt) + "</span>" : "") +
        "</div>";
    }).join("");
  }


  // ── availability calendar ───────────────────────────────────────────
  // Opens from the nav. Keeps the month grid, because "are they free on
  // the 17th" is a spatial question, without letting an empty month own
  // the page the way the old layout did.
  var calGigs = [];
  var calCursor = new Date();
  var CAL_MONTHS = ["January","February","March","April","May","June",
                    "July","August","September","October","November","December"];

  function iso(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
           "-" + String(d.getDate()).padStart(2, "0");
  }

  function renderCal() {
    var y = calCursor.getFullYear(), m = calCursor.getMonth();
    $("v2CalMonth").textContent = CAL_MONTHS[m] + " " + y;

    var first = new Date(y, m, 1).getDay();
    var days = new Date(y, m + 1, 0).getDate();
    var prevLast = new Date(y, m, 0).getDate();
    var todayStr = iso(new Date());
    var html = "";

    // Leading days carry real numbers rather than blanks, so the grid reads
    // as a continuous month rather than starting with holes.
    for (var i = first - 1; i >= 0; i--) {
      html += '<div class="v2-cal-cell other"><div class="v2-cal-num">' +
              (prevLast - i) + "</div></div>";
    }
    for (var d = 1; d <= days; d++) {
      var ds = y + "-" + String(m + 1).padStart(2, "0") + "-" + String(d).padStart(2, "0");
      var onDay = calGigs.filter(function (g) { return String(g.date) === ds; });
      var cls = "v2-cal-cell" + (onDay.length ? " booked" : "") + (ds === todayStr ? " today" : "");
      var click = onDay.length ? ' onclick="v2OpenDay(\'' + ds + '\')"' : "";
      html += '<div class="' + cls + '"' + click + '><div class="v2-cal-num">' + d + "</div>";
      onDay.forEach(function (g) {
        var t = g.start_time && window.formatTime12Hour ? window.formatTime12Hour(g.start_time) : "";
        html += '<div class="v2-cal-gig" title="' + esc((g.venue_name || "") + " " + t) + '">' +
                esc(t || "Booked") + "</div>";
      });
      html += "</div>";
    }
    // Trailing days so the final week isn't a ragged row.
    var cells = first + days;
    var trail = (7 - (cells % 7)) % 7;
    for (var k = 1; k <= trail; k++) {
      html += '<div class="v2-cal-cell other"><div class="v2-cal-num">' + k + "</div></div>";
    }
    $("v2CalGrid").innerHTML = html;
  }


  // Clicking a booked day swaps the modal body for that day's detail rather
  // than stacking a second modal on top of the first — one layer, with a way
  // back. Slots are fetched per gig so multi-slot nights list every artist.
  window.v2OpenDay = async function (ds) {
    var dayGigs = calGigs.filter(function (g) { return String(g.date) === ds; });
    if (!dayGigs.length) return;

    await Promise.all(dayGigs.map(async function (g) {
      if (g._slots) return;
      var r = await getJSON("/api/gigs/" + g.id + "/slots/public");
      g._slots = r || [];
    }));

    var parts = ds.split("-").map(Number);
    var pretty = new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString("en-US", {
      weekday: "long", month: "long", day: "numeric", year: "numeric"
    });

    var rows = dayGigs.map(function (g) {
      var t = g.start_time && window.formatTime12Hour ? window.formatTime12Hour(g.start_time) : "";
      if (g.end_time && window.formatTime12Hour) t += " – " + window.formatTime12Hour(g.end_time);
      var where = [g.city, g.state].filter(Boolean).join(", ");
      var venue = g.venue_id
        ? '<a href="/app/venue-profile.html?venue_id=' + Number(g.venue_id) +
          '" target="_blank" rel="noopener">' + esc(g.venue_name || "") + "</a>"
        : esc(g.venue_name || "");
      var who = (g._slots || []).filter(function (sl) {
        return sl.status === "booked" && sl.artist_name;
      }).map(function (sl) { return esc(sl.artist_name); });
      var fmt = (g.band_formats || "").split(",").map(function (x) { return x.trim(); })
                .filter(Boolean).join(" / ");

      return '<div class="v2-day-gig">' +
        '<div class="v2-day-time">' + esc(t || "Time TBC") + "</div>" +
        '<div class="v2-day-main">' +
          '<div class="v2-day-venue">' + venue + "</div>" +
          '<div class="v2-day-meta">' + esc(where) +
            (fmt ? '<span class="v2-day-sep">·</span>' + esc(fmt) : "") +
            (who.length ? '<span class="v2-day-sep">·</span>' + who.join(", ") : "") +
          "</div>" +
        "</div></div>";
    }).join("");

    // Header carries the date now. Month navigation is meaningless once
    // you've picked a specific night, and "Availability" is the wrong
    // title for a single booking.
    // Day view keeps the same "Show All Booked Dates" control as the grid,
    // so the full list is reachable from here too. Month stepping goes —
    // it means nothing once a specific night is on screen. Closing and
    // reopening Calendar returns to the grid.
    $("v2ModalTitle").textContent = pretty;
    $("v2ModalTitle").style.display = "block";
    $("v2ModalHead").classList.add("day");
    $("v2CalNavGroup").style.display = "none";

    $("v2CalDayBody").innerHTML = rows;
    $("v2CalMain").style.display = "none";
    $("v2CalDay").style.display = "block";
  };

  window.v2BackToCal = function () {
    $("v2CalDay").style.display = "none";
    $("v2CalMain").style.display = "block";
    $("v2ModalTitle").style.display = "none";
    $("v2ModalHead").classList.remove("day");
    $("v2CalNavGroup").style.display = "inline-flex";
  };


  // "Show All Booked Dates" — hands off to the shared public gigs list
  // modal (the same one the live profile uses), so the sortable/exportable
  // list isn't reimplemented here. Multi-slot gigs expand to one row per
  // slot this artist actually holds, otherwise a four-slot night would
  // look like four of their bookings.
  window.v2ShowAllBooked = function () {
    if (typeof window.openPublicGigsListModal !== "function") return;

    // Hand off rather than stack. Leaving the calendar open underneath
    // means the list's X reveals a modal the user has finished with and
    // has to dismiss again — two closes for one exit. Closing here makes
    // the list's own X the single way out, whether it was opened from the
    // month grid or from a day's detail.
    window.v2CloseCal();

    var rows = [];
    (calGigs || []).forEach(function (g) {
      var slots = Array.isArray(g.slots) ? g.slots : [];
      var multi = slots.length > 1;
      if (slots.length) {
        slots.forEach(function (sl) {
          if (sl.status !== "booked") return;
          if (String(sl.artist_id) !== String(artistId)) return;
          rows.push({
            date: g.date, venue_id: g.venue_id, venue_name: g.venue_name || "",
            address_line_1: g.address_line_1 || "", address_line_2: g.address_line_2 || "",
            city: g.city || "", state: g.state || "",
            start_time: sl.start_time || g.start_time,
            end_time: sl.end_time || g.end_time,
            title: g.title || "", is_multi_slot: multi, slot_number: sl.slot_number
          });
        });
      } else if (g.status === "booked") {
        rows.push({
          date: g.date, venue_id: g.venue_id, venue_name: g.venue_name || "",
          address_line_1: g.address_line_1 || "", address_line_2: g.address_line_2 || "",
          city: g.city || "", state: g.state || "",
          start_time: g.start_time, end_time: g.end_time,
          title: g.title || "", is_multi_slot: false, slot_number: null
        });
      }
    });
    var name = (window.__v2ArtistName || "artist");
    var slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    window.openPublicGigsListModal({
      title: "\uD83D\uDCC5 All Booked Gigs — " + name,
      rows: rows,
      exportBasename: slug + "_booked_gigs",
      columns: ["date", "time", "venue", "address"]
    });
  };

  window.v2OpenCal = function () {
    window.v2BackToCal();
    renderCal();
    $("v2CalModal").classList.add("open");
  };
  window.v2CloseCal = function () { $("v2CalModal").classList.remove("open"); };
  window.v2CalStep = function (n) {
    calCursor = new Date(calCursor.getFullYear(), calCursor.getMonth() + n, 1);
    renderCal();
  };

  // ── setlist ─────────────────────────────────────────────────────────
  var SETLIST_PREVIEW = 60;   // enough to judge range without a wall of text

  function renderSetlist(data) {
    var songs = (data && data.songs) || [];
    $("v2SongCount").textContent = songs.length ? songs.length + " songs" : "";
    if (!songs.length) { show("v2SongsEmpty", true); return; }
    present["sec-setlist"] = true;

    function line(s) {
      return '<div class="v2-song">' +
        '<span class="v2-song-t">' + esc(s.song_title || "") + "</span>" +
        (s.original_artist ? ' <span class="v2-song-a">(' + esc(s.original_artist) + ")</span>" : "") +
        "</div>";
    }

    // Long lists collapse. Bands with 200 songs shouldn't push every other
    // section off the page, but the count is visible so nothing looks hidden.
    if (songs.length > SETLIST_PREVIEW) {
      $("v2Songs").innerHTML = songs.slice(0, SETLIST_PREVIEW).map(line).join("");
      var more = document.createElement("div");
      more.className = "v2-song-more";
      more.innerHTML = '<button type="button" class="v2-btn ghost" id="v2SongMore">' +
                       "Show all " + songs.length + " songs</button>";
      $("v2Songs").parentNode.insertBefore(more, $("v2Songs").nextSibling);
      document.getElementById("v2SongMore").addEventListener("click", function () {
        $("v2Songs").innerHTML = songs.map(line).join("");
        more.remove();
      });
    } else {
      $("v2Songs").innerHTML = songs.map(line).join("");
    }
  }

  // ── social + contact ────────────────────────────────────────────────
  var SOCIAL = [
    ["website",   "website_url",   "Website"],
    ["instagram", "instagram_url", "Instagram"],
    ["spotify",   "spotify_url",   "Spotify"],
    ["youtube",   "youtube_url",   "YouTube"],
    ["facebook",  "facebook_url",  "Facebook"],
    ["twitter",   "twitter_url",   "X"],
    ["tiktok",    "tiktok_url",    "TikTok"]
  ];

  function renderContact(a) {
    present["sec-contact"] = true;

    // Honour the artist's own ordering when they've set one.
    var order = (a.social_order || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    var list = SOCIAL.slice().sort(function (x, y) {
      var ix = order.indexOf(x[0]); var iy = order.indexOf(y[0]);
      return (ix < 0 ? 99 : ix) - (iy < 0 ? 99 : iy);
    });

    var icon = window.gfSocialIcon || function () { return ""; };
    var color = window.gfBrandColor || function () { return "#9ca3af"; };
    var safe = window.gfSafeHref || function (u) { return u; };

    var html = "";
    list.forEach(function (s) {
      var url = safe(a[s[1]]);
      if (!url) return;
      if (s[0] === "website" && !a.website_public) return;
      // Website tile carries the artist's name — "this is who you'll
      // reach" reads better than the generic word.
      var label = (s[0] === "website") ? (a.name || "Website") : s[2];
      html += '<a class="v2-social-tile" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" ' +
              'style="--brand:' + color(s[0]) + '" title="' + esc(label) + '">' +
              '<span class="v2-social-ico">' + icon(s[0]) + "</span>" +
              "<span>" + esc(label) + "</span></a>";
    });
    $("v2Social").innerHTML = html || '<span class="v2-empty">No links yet.</span>';

    // "Book <artist>" restated what the page is for. The useful content is
    // the contact itself, so lead with it.
    var booking = (a.booking_contact || "").trim();
    $("v2Contact").innerHTML =
      '<div class="v2-contact-line">' +
        '<span class="v2-contact-label">Contact:</span>' +
        '<span class="v2-contact-value">' +
          (booking ? linkifyContact(booking) : "Not listed") +
        "</span>" +
      "</div>" +
      '<span id="v2ContactAction"></span>';
  }

  // Make the email and phone inside a freeform contact string tappable.
  // It's one field the artist types however they like, so this matches
  // rather than parses — anything unrecognised is left as escaped text.
  function linkifyContact(raw) {
    var out = esc(raw);
    out = out.replace(/([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})/gi, function (m) {
      return '<a href="mailto:' + m + '">' + m + "</a>";
    });
    // US-ish numbers: (805) 231-0046, 805-231-0046, 805.231.0046
    out = out.replace(/(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})/g, function (m) {
      var digits = m.replace(/\D/g, "");
      if (digits.length !== 10) return m;
      return '<a href="tel:+1' + digits + '">' + m + "</a>";
    });
    return out;
  }

  // ── nav ─────────────────────────────────────────────────────────────
  function renderNav() {
    var html = "";
    SECTIONS.forEach(function (s) {
      // Calendar is an action, not an anchor — it opens the modal.
      if (s.action) {
        html += '<a role="button" tabindex="0" class="act" onclick="' + s.action + '">' +
                esc(s.label) + "</a>";
        return;
      }
      if (!present[s.id]) {
        var el = $(s.id);
        if (el) el.style.display = "none";   // hide the empty section too
        return;
      }
      html += '<a href="#' + s.id + '">' + esc(s.label) + "</a>";
    });
    $("v2NavInner").innerHTML = html;

    // Highlight whichever section is in view. Cheap scroll handler rather
    // than IntersectionObserver so behaviour is obvious when debugging.
    var links = Array.prototype.slice.call($("v2NavInner").querySelectorAll("a"))
      .filter(function (l) {
        var h = l.getAttribute("href") || "";
        return h.charAt(0) === "#" && h.length > 1;
      });
    function sync() {
      var best = null, bestTop = -Infinity;
      links.forEach(function (l) {
        var sec = document.querySelector(l.getAttribute("href"));
        if (!sec) return;
        var top = sec.getBoundingClientRect().top - 80;
        if (top <= 0 && top > bestTop) { bestTop = top; best = l; }
      });
      links.forEach(function (l) { l.classList.toggle("on", l === best); });
    }
    window.addEventListener("scroll", sync, { passive: true });
    sync();
  }

  // ── lightbox ────────────────────────────────────────────────────────
  window.v2OpenLb = function (src) {
    $("v2LbImg").src = src;
    $("v2Lb").classList.add("open");
  };
  window.v2CloseLb = function () { $("v2Lb").classList.remove("open"); };
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") window.v2CloseLb();
  });
  document.addEventListener("click", function (e) {
    if (e.target && e.target.id === "v2Lb") window.v2CloseLb();
  });

  // ── boot ────────────────────────────────────────────────────────────
  (async function init() {
    if (!artistId) {
      document.querySelector("main").innerHTML =
        '<div class="v2-wrap" style="padding:60px 0;"><p class="v2-empty">No artist specified.</p></div>';
      return;
    }

    var results = await Promise.all([
      getJSON("/api/artists/" + artistId),
      getJSON("/api/artists/" + artistId + "/media"),
      getJSON("/api/artists/" + artistId + "/gigs/public"),
      getJSON("/api/artists/" + artistId + "/setlist")
    ]);
    var artist = results[0], media = results[1] || [], gigs = results[2] || [], setlist = results[3];

    if (!artist) {
      document.querySelector("main").innerHTML =
        '<div class="v2-wrap" style="padding:60px 0;"><p class="v2-empty">Artist not found.</p></div>';
      return;
    }

    renderHero(artist, media);
    renderVideos(media);
    renderPhotos(media);
    renderAbout(artist);
    renderDates(gigs);
    renderSetlist(setlist);
    renderContact(artist);
    renderNav();

    calGigs = gigs;
    await renderAction(artist);
    // Mirror whatever the hero resolved to, so the page doesn't offer two
    // different answers to "what can I do about this artist".
    var mirror = $("v2ContactAction");
    var heroAction = $("v2Cta").lastElementChild;
    if (mirror && heroAction && !heroAction.hasAttribute("data-noMirror")) {
      mirror.innerHTML = heroAction.outerHTML;
    }
  })();
})();
