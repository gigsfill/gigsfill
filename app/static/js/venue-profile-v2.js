/**
 * Venue profile — redesign, 2026-10-01.
 *
 * The artist-side counterpart shipped first; this mirrors its structure
 * (one scroll, anchored nav, calendar in a modal, context-aware action)
 * with one addition that is the real point of the page.
 *
 * GigsFill already collects stage width and depth, PA, whether there's a
 * sound engineer, lighting, load-in notes, arrival window, typical pay and
 * bar/food tabs — and the old profile showed none of it. That data is
 * exactly what an artist decides on, so it leads here as a spec block.
 *
 * It stays behind the existing artist-only gate in
 * /api/venues/{id}/public. That gate is a deliberate audit fix (an
 * anonymous scraper could otherwise harvest every venue's ops catalogue),
 * so this renders what the server chose to send rather than asking for
 * more.
 */
(function () {
  "use strict";

  var esc = window.esc || function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };

  // Browsers restore the previous scroll position on reload, which on a
  // long single-scroll profile drops you into the middle of the page with
  // no context. Opt out and start at the top — unless the URL carries an
  // explicit #section, which the visitor chose and should still win.
  if ("scrollRestoration" in history) {
    try { history.scrollRestoration = "manual"; } catch (e) {}
  }
  if (!window.location.hash) {
    window.addEventListener("load", function () { window.scrollTo(0, 0); });
  }

  var params = new URLSearchParams(window.location.search);
  // window._VANITY is injected by the slug resolver for pretty URLs like
  // gigsfill.com/venuedemo, which carry no query string. It takes
  // precedence over ?venue_id=N.
  var venueId = (window._VANITY && window._VANITY.type === "venue"
                   ? window._VANITY.id : null)
                || params.get("venue_id");

  var SECTIONS = [
    { id: "__cal",       label: "Calendar", action: "v2OpenCal()" },
    { id: "sec-specs",   label: "The Room" },
    { id: "sec-photos",  label: "Photos" },
    { id: "sec-watch",   label: "Video" },
    { id: "sec-about",   label: "About" },
    { id: "sec-dates",   label: "Gigs" },
    { id: "sec-reviews", label: "Reviews" },
    { id: "sec-contact", label: "Contact" }
  ];
  var present = {};

  function $(id) { return document.getElementById(id); }
  function show(id, on) { var el = $(id); if (el) el.style.display = on ? "" : "none"; }

  async function getJSON(url) {
    try {
      var r = await fetch(url, { credentials: "include" });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  // ── hero ────────────────────────────────────────────────────────────
  function renderHero(v, media) {
    var name = v.venue_name || "Venue";
    document.title = name + " – GigsFill";
    window.__v2VenueName = name;
    $("v2Name").textContent = name;

    var pics = media.filter(function (m) { return m.media_type === "picture" && m.file_path; });
    var chosen = null;
    if (v.hero_media_id) {
      chosen = media.filter(function (m) {
        return Number(m.id) === Number(v.hero_media_id) && m.file_path;
      })[0] || null;
    }
    var heroSrc = (chosen && chosen.file_path) || (pics[0] && pics[0].file_path) || "";
    if (heroSrc) {
      $("v2HeroBg").style.backgroundImage = 'url("' + heroSrc + '")';
      var fx = (v.hero_focal_x == null) ? 50 : Number(v.hero_focal_x);
      var fy = (v.hero_focal_y == null) ? 50 : Number(v.hero_focal_y);
      var z = (v.hero_zoom == null) ? 1 : Math.max(1, Math.min(4, Number(v.hero_zoom)));
      $("v2HeroBg").style.backgroundPosition = fx + "% " + fy + "%";
      // Zoom via transform with the focal point as origin, so pushing in
      // keeps the chosen subject where the user put it instead of drifting
      // toward the centre. 1 = exactly "cover", so the default is a no-op.
      $("v2HeroBg").style.transformOrigin = fx + "% " + fy + "%";
      $("v2HeroBg").style.transform = (z > 1) ? "scale(" + z + ")" : "none";
    } else {
      $("v2Hero").classList.add("no-photo");
    }
    // Venues have no logo, so the hero plate carries their first photo —
    // better than an empty frame next to the name.
    if (pics[0]) $("v2Logo").src = pics[0].file_path;
    else $("v2Logo").style.display = "none";

    var where = [v.city, v.state].filter(Boolean).join(", ");
    var bits = [];
    if (where) bits.push(esc(where));
    if (v.venue_size) bits.push("Capacity " + esc(v.venue_size));
    $("v2Sub").innerHTML = bits.join('<span class="dot">•</span>');

    var chips = "";
    if (v.pro_certified) chips += '<span class="v2-chip accent">★ PRO Certified</span>';
    // Free-trial venues pay artists directly, which changes what the artist
    // should expect — worth saying up front, not at payment time.
    if (v.is_free_trial) chips += '<span class="v2-chip">Pays artists directly</span>';
    $("v2Chips").innerHTML = chips ? '<div class="v2-chip-row">' + chips + "</div>" : "";

    var cta = "";
    var rating = Number(v.avg_rating || 0), count = Number(v.review_count || 0);
    if (rating > 0 && count > 0) {
      var full = Math.round(rating);
      cta += '<span class="v2-rating"><span class="v2-stars">' +
             "★".repeat(full) + "☆".repeat(Math.max(0, 5 - full)) +
             "</span><strong>" + rating.toFixed(1) + "</strong>" +
             '<span style="color:var(--v2-dim);">(' + count + ")</span></span>";
    }
    $("v2Cta").innerHTML = cta;
  }

  // ── the spec block ──────────────────────────────────────────────────
  // Only what the server sent. Fields the artist-only gate stripped are
  // simply absent, so nothing has to be hidden client-side.
  function renderSpecs(v) {
    if (!v.viewer_is_artist) {
      show("v2SpecsLocked", true);
      present["sec-specs"] = true;      // keep the nav link; the copy explains
      return;
    }

    var rows = [];

    // Stage. Dimensions matter more than the yes/no — "has a stage" tells a
    // five-piece nothing about whether they fit on it.
    if (v.has_stage) {
      var dims = (v.stage_width_ft && v.stage_depth_ft)
        ? v.stage_width_ft + " ft × " + v.stage_depth_ft + " ft"
        : "Stage available";
      rows.push(["Stage", dims, v.setup_location_description]);
    } else {
      rows.push(["Stage", '<span class="no">No stage</span>',
                 v.setup_location_description]);
    }

    rows.push(["Sound",
      v.has_sound_equipment ? "PA provided" : '<span class="no">Bring your own PA</span>',
      v.sound_equipment_description]);

    if (v.has_sound_engineer) {
      rows.push(["Engineer", "Sound engineer on site", v.sound_engineer_details]);
    }

    rows.push(["Lighting",
      v.has_lighting ? "Stage lighting" : '<span class="no">No stage lighting</span>',
      v.lighting_description]);

    if (v.load_in_out_details) rows.push(["Load in / out", "", v.load_in_out_details]);

    var arrival = formatArrival(v);
    if (arrival) rows.push(["Arrival", arrival, null]);

    var pay = payLine(v);
    if (pay) rows.push(["Typical pay", pay, null]);

    if (v.bar_tab_details)  rows.push(["Bar tab", "", v.bar_tab_details]);
    if (v.food_tab_details) rows.push(["Food", "", v.food_tab_details]);

    // Label, then body text. Value and detail share one treatment — the
    // mix of white values and muted details made rows that happened to
    // have both look like a different kind of row from those that didn't.
    $("v2Specs").innerHTML = rows.map(function (r) {
      return '<div class="v2-spec">' +
        '<div class="v2-spec-label">' + esc(r[0]) + "</div>" +
        (r[1] ? '<div class="v2-spec-value">' + r[1] + "</div>" : "") +
        (r[2] ? '<div class="v2-spec-value">' + esc(r[2]) + "</div>" : "") +
        "</div>";
    }).join("");
    present["sec-specs"] = true;
  }

  function formatArrival(v) {
    var t = (v.arrival_time_type || "").trim();
    if (!t) return "";
    if (t.toLowerCase().indexOf("no earlier") === -1) return t;
    var h = v.arrival_no_earlier_than_hour, p = v.arrival_no_earlier_than_period;
    return h ? "No earlier than " + h + (p ? " " + p : "") : t;
  }

  function payLine(v) {
    var d = Number(v.default_pay_dollars || 0), c = Number(v.default_pay_cents || 0);
    if (!d && !c) return "";
    return "$" + d + (c ? "." + String(c).padStart(2, "0") : "") + " typical";
  }

  // ── media ───────────────────────────────────────────────────────────
  function platformOf(url) {
    var u = String(url || "");
    if (/instagram\.com/i.test(u))          return { name: "Instagram", noThumb: true };
    if (/tiktok\.com/i.test(u))             return { name: "TikTok",    noThumb: true };
    if (/facebook\.com|fb\.watch/i.test(u)) return { name: "Facebook",  noThumb: true };
    if (/youtube\.com|youtu\.be/i.test(u))  return { name: "YouTube",   noThumb: false };
    if (/vimeo\.com/i.test(u))              return { name: "Vimeo",     noThumb: false };
    return { name: "Video", noThumb: true };
  }

  function renderVideos(media) {
    var vids = media.filter(function (m) { return m.media_type === "video" && m.video_url; });
    $("v2VideoCount").textContent = vids.length ? vids.length + " videos" : "";
    if (!vids.length) { show("v2VideosEmpty", true); return; }
    present["sec-watch"] = true;

    $("v2Videos").innerHTML = vids.map(function (v) {
      var plat = platformOf(v.video_url);
      var inner = plat.noThumb
        ? '<div class="v2-thumb-blank"><span class="v2-badge">' + esc(plat.name) + "</span></div>"
        : '<img data-video-url="' + esc(v.video_url) + '" alt="">';
      return '<a class="v2-card" href="' + esc(v.video_url) + '" target="_blank" rel="noopener">' +
        '<div class="v2-thumb">' + inner + '<div class="v2-play"><span></span></div></div>' +
        '<div class="v2-card-body"><div class="v2-card-title">' + esc(v.title || "Untitled") + "</div>" +
        (v.caption ? '<div class="v2-card-sub">' + esc(v.caption) + "</div>" : "") +
        "</div></a>";
    }).join("");

    if (window.attachVideoThumb) {
      $("v2Videos").querySelectorAll("img[data-video-url]").forEach(function (img) {
        window.attachVideoThumb(img, img.getAttribute("data-video-url"));
      });
    }
  }

  function renderPhotos(media) {
    var pics = media.filter(function (m) { return m.media_type === "picture" && m.file_path; });
    $("v2PhotoCount").textContent = pics.length ? pics.length + " photos" : "";
    if (!pics.length) { show("v2PhotosEmpty", true); return; }
    present["sec-photos"] = true;
    if (pics.length <= 3) $("v2Photos").classList.add("few");
    $("v2Photos").innerHTML = pics.map(function (p) {
      return '<figure class="v2-photo" onclick="v2OpenLb(\'' + esc(p.file_path) + '\')">' +
        '<img src="' + esc(p.file_path) + '" alt="' + esc(p.title || "") + '" loading="lazy">' +
        (p.title ? "<figcaption>" + esc(p.title) + "</figcaption>" : "") + "</figure>";
    }).join("");
  }

  // ── about ───────────────────────────────────────────────────────────
  function renderAbout(v) {
    present["sec-about"] = true;
    $("v2Bio").textContent = v.description || "No description yet.";
    var street = [v.address_line_1, v.address_line_2].filter(Boolean).join(", ");
    var facts = [];
    if (street) facts.push(["Address", street]);
    if (v.city || v.state) facts.push(["City", [v.city, v.state].filter(Boolean).join(", ")]);
    if (v.postal_code) facts.push(["ZIP", v.postal_code]);
    if (v.venue_size) facts.push(["Capacity", v.venue_size]);
    if (v.pro_certified) facts.push(["Status", "PRO Certified"]);
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
      return new Date(String(g.date) + "T00:00:00") >= today;
    }).sort(function (a, b) { return String(a.date).localeCompare(String(b.date)); });

    $("v2DateCount").textContent = upcoming.length ? upcoming.length + " listed" : "";
    if (!upcoming.length) { show("v2DatesEmpty", true); return; }
    present["sec-dates"] = true;

    $("v2Dates").innerHTML = upcoming.slice(0, 8).map(function (g) {
      var d = new Date(String(g.date) + "T00:00:00");
      var t = "";
      if (g.start_time && window.formatTime12Hour) {
        t = window.formatTime12Hour(g.start_time);
        if (g.end_time) t += " – " + window.formatTime12Hour(g.end_time);
      }
      // An open slot is the actionable one for an artist reading this, so
      // say which it is rather than listing every date identically.
      var open = (g.status === "open") || (g.booked_slots_count < g.total_slots_count);
      var who = open ? "Open" : (g.artist_name || "Booked");
      var fmt = (g.band_formats || "").split(",").map(function (x) { return x.trim(); })
                 .filter(Boolean).join(" / ");
      return '<div class="v2-date">' +
        '<div class="v2-date-when"><div class="v2-date-mon">' + MON[d.getMonth()] + "</div>" +
        '<div class="v2-date-day">' + d.getDate() + "</div></div>" +
        '<div class="v2-date-main"><div class="v2-date-venue">' + esc(who) + "</div>" +
        '<div class="v2-date-meta">' + esc(t) + "</div></div>" +
        (fmt ? '<span class="v2-date-fmt">' + esc(fmt) + "</span>" : "") +
        "</div>";
    }).join("");
  }

  // ── reviews ─────────────────────────────────────────────────────────
  async function renderReviewsSection() {
    var summary = await getJSON("/api/venues/" + venueId + "/reviews/summary");
    if (!summary || !Number(summary.review_count)) return;
    present["sec-reviews"] = true;
    if (typeof window.renderVenueRatingSummary === "function") {
      window.renderVenueRatingSummary("v2RatingSummary", parseInt(venueId, 10));
    }
    if (typeof window.renderVenueReviews === "function") {
      window.renderVenueReviews("v2ReviewsList", parseInt(venueId, 10));
    }
  }

  // ── social + contact ────────────────────────────────────────────────
  var SOCIAL = [
    ["website",     "website_url",     "Website"],
    ["instagram",   "instagram_url",   "Instagram"],
    ["facebook",    "facebook_url",    "Facebook"],
    ["twitter",     "twitter_url",     "X"],
    ["yelp",        "yelp_url",        "Yelp"],
    ["google_maps", "google_maps_url", "Directions"]
  ];

  function renderContact(v) {
    present["sec-contact"] = true;
    var order = (v.social_order || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
    var list = SOCIAL.slice().sort(function (a, b) {
      var ia = order.indexOf(a[0]), ib = order.indexOf(b[0]);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });

    var icon = window.gfSocialIcon || function () { return ""; };
    var color = window.gfBrandColor || function () { return "#9ca3af"; };
    var safe = window.gfSafeHref || function (u) { return u; };

    var html = "";
    list.forEach(function (s) {
      var url = safe(v[s[1]]);
      if (!url) return;
      if (s[0] === "website" && !v.website_public) return;
      var label = (s[0] === "website") ? (v.venue_name || "Website") : s[2];
      html += '<a class="v2-social-tile" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" ' +
              'style="--brand:' + color(s[0]) + '" title="' + esc(label) + '">' +
              '<span class="v2-social-ico">' + icon(s[0]) + "</span><span>" + esc(label) + "</span></a>";
    });
    $("v2Social").innerHTML = html || '<span class="v2-empty">No links yet.</span>';

    var addr = [v.address_line_1, v.address_line_2, v.city, v.state, v.postal_code]
      .filter(Boolean).join(", ");
    $("v2Contact").innerHTML =
      '<div class="v2-contact-line">' +
        '<span class="v2-contact-label">Address:</span>' +
        '<span class="v2-contact-value">' + (addr ? esc(addr) : "Not listed") + "</span>" +
      "</div>" +
      '<span id="v2ContactAction"></span>';
  }

  // ── nav ─────────────────────────────────────────────────────────────
  function renderNav() {
    var html = "";
    SECTIONS.forEach(function (s) {
      if (s.action) {
        html += '<a role="button" tabindex="0" class="act" onclick="' + s.action + '">' +
                esc(s.label) + "</a>";
        return;
      }
      if (!present[s.id]) {
        var el = $(s.id);
        if (el) el.style.display = "none";
        return;
      }
      html += '<a href="#' + s.id + '">' + esc(s.label) + "</a>";
    });
    $("v2NavInner").innerHTML = html;

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

  // ── calendar ────────────────────────────────────────────────────────
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

    for (var i = first - 1; i >= 0; i--) {
      html += '<div class="v2-cal-cell other"><div class="v2-cal-num">' + (prevLast - i) + "</div></div>";
    }
    for (var d = 1; d <= days; d++) {
      var ds = y + "-" + String(m + 1).padStart(2, "0") + "-" + String(d).padStart(2, "0");
      var onDay = calGigs.filter(function (g) { return String(g.date) === ds; });
      var cls = "v2-cal-cell" + (onDay.length ? " booked" : "") + (ds === todayStr ? " today" : "");
      var click = onDay.length ? ' onclick="v2OpenDay(\'' + ds + '\')"' : "";
      html += '<div class="' + cls + '"' + click + '><div class="v2-cal-num">' + d + "</div>";
      onDay.forEach(function (g) {
        var t = g.start_time && window.formatTime12Hour ? window.formatTime12Hour(g.start_time) : "";
        // Open nights are the ones an artist can act on, so they read
        // differently from nights already filled.
        var isOpen = (g.status === "open");
        html += '<div class="v2-cal-gig' + (isOpen ? " ext" : "") + '" title="' +
                esc((isOpen ? "Open — " : "Booked — ") + (g.artist_name || "")) + '">' +
                esc(t || (isOpen ? "Open" : "Booked")) + "</div>";
      });
      html += "</div>";
    }
    var cells = first + days, trail = (7 - (cells % 7)) % 7;
    for (var k = 1; k <= trail; k++) {
      html += '<div class="v2-cal-cell other"><div class="v2-cal-num">' + k + "</div></div>";
    }
    $("v2CalGrid").innerHTML = html;
  }

  window.v2ShowAllBooked = function () {
    if (typeof window.openPublicGigsListModal !== "function") return;
    window.v2CloseCal();
    var rows = (calGigs || []).map(function (g) {
      return {
        date: g.date, venue_id: g.venue_id, venue_name: g.venue_name || "",
        artist_name: g.artist_name || (g.status === "open" ? "Open" : "Booked"),
        address_line_1: g.address_line_1 || "", address_line_2: g.address_line_2 || "",
        city: g.city || "", state: g.state || "",
        start_time: g.start_time, end_time: g.end_time,
        title: g.title || "", is_multi_slot: !!g.is_multi_slot, slot_number: null
      };
    });
    var name = window.__v2VenueName || "venue";
    var slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    window.openPublicGigsListModal({
      title: "📅 All Gigs — " + name,
      rows: rows,
      exportBasename: slug + "_gigs",
      columns: ["date", "time", "artist", "address"]
    });
  };

  window.v2OpenDay = async function (ds) {
    var dayGigs = calGigs.filter(function (g) { return String(g.date) === ds; });
    if (!dayGigs.length) return;

    await Promise.all(dayGigs.map(async function (g) {
      if (g._slots) return;
      g._slots = (await getJSON("/api/gigs/" + g.id + "/slots/public")) || [];
    }));

    var parts = ds.split("-").map(Number);
    var pretty = new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString("en-US", {
      weekday: "long", month: "long", day: "numeric", year: "numeric"
    });

    $("v2CalDayBody").innerHTML = dayGigs.map(function (g) {
      var t = g.start_time && window.formatTime12Hour ? window.formatTime12Hour(g.start_time) : "";
      if (g.end_time && window.formatTime12Hour) t += " – " + window.formatTime12Hour(g.end_time);
      var booked = (g._slots || []).filter(function (sl) {
        return sl.status === "booked" && sl.artist_name;
      }).map(function (sl) {
        return '<a href="/app/artist-profile.html?artist_id=' + Number(sl.artist_id) +
               '" target="_blank" rel="noopener">' + esc(sl.artist_name) + "</a>";
      });
      var fmt = (g.band_formats || "").split(",").map(function (x) { return x.trim(); })
                 .filter(Boolean).join(" / ");
      return '<div class="v2-day-gig">' +
        '<div class="v2-day-time">' + esc(t || "Time TBC") + "</div>" +
        '<div class="v2-day-main">' +
          '<div class="v2-day-venue">' +
            (booked.length ? booked.join(", ") : (g.status === "open" ? "Open slot" : "Booked")) +
          "</div>" +
          '<div class="v2-day-meta">' + esc(g.artist_type || "") +
            (fmt ? '<span class="v2-day-sep">·</span>' + esc(fmt) : "") +
          "</div>" +
        "</div></div>";
    }).join("");

    $("v2ModalTitle").textContent = pretty;
    $("v2ModalTitle").style.display = "block";
    $("v2ModalHead").classList.add("day");
    $("v2CalNavGroup").style.display = "none";
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

  // ── lightbox ────────────────────────────────────────────────────────
  window.v2OpenLb = function (src) {
    $("v2LbImg").src = src;
    $("v2Lb").classList.add("open");
  };
  window.v2CloseLb = function () { $("v2Lb").classList.remove("open"); };
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { window.v2CloseLb(); window.v2CloseCal(); }
  });
  document.addEventListener("click", function (e) {
    if (e.target && e.target.id === "v2Lb") window.v2CloseLb();
  });

  // ── the action ──────────────────────────────────────────────────────
  // Mirrors the artist page: say something useful to whoever is reading,
  // rather than one generic button that fits nobody.
  async function renderAction(v) {
    var slot = document.createElement("span");
    $("v2Cta").appendChild(slot);

    var me = await getJSON("/api/me");
    var artists = (me && me.artists) || [];
    if (!artists.length) {
      slot.outerHTML = '<a class="v2-btn primary" data-noMirror="1" href="#sec-contact">Find This Venue</a>';
      return;
    }
    // An artist reading this wants the open nights, not a contact card.
    var aid = artists[0].id;
    slot.outerHTML = '<a class="v2-btn primary" href="/app/artist-book-gigs.html?artist_id=' +
                     encodeURIComponent(aid) + '">See Open Gigs</a>';
  }

  // ── boot ────────────────────────────────────────────────────────────
  (async function init() {
    if (!venueId) {
      document.querySelector("main").innerHTML =
        '<div class="v2-wrap" style="padding:60px 0;"><p class="v2-empty">No venue specified.</p></div>';
      return;
    }

    var results = await Promise.all([
      getJSON("/api/venues/" + venueId + "/public"),
      getJSON("/api/venues/" + venueId + "/media"),
      getJSON("/api/gigs/public")
    ]);
    var venue = results[0], media = results[1] || [], allGigs = results[2] || [];

    if (!venue) {
      document.querySelector("main").innerHTML =
        '<div class="v2-wrap" style="padding:60px 0;"><p class="v2-empty">Venue not found.</p></div>';
      return;
    }

    // /api/gigs/public is site-wide, so narrow to this venue here.
    var gigs = allGigs.filter(function (g) { return Number(g.venue_id) === Number(venueId); });

    renderHero(venue, media);
    renderSpecs(venue);
    renderPhotos(media);
    renderVideos(media);
    renderAbout(venue);
    renderDates(gigs);
    renderContact(venue);

    calGigs = gigs;
    await renderReviewsSection();
    renderNav();
    await renderAction(venue);

    var mirror = $("v2ContactAction");
    var heroAction = $("v2Cta").lastElementChild;
    if (mirror && heroAction && !heroAction.hasAttribute("data-noMirror")) {
      mirror.innerHTML = heroAction.outerHTML;
    }
  })();
})();
