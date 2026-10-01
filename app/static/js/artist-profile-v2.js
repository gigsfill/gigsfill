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
    var chips = "";
    (a.styles || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean)
      .forEach(function (s) { chips += '<span class="v2-chip accent">' + esc(s) + "</span>"; });
    (a.band_formats || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean)
      .forEach(function (s) { chips += '<span class="v2-chip">' + esc(s) + "</span>"; });
    $("v2Chips").innerHTML = chips;

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
    cta += '<a class="v2-btn primary" href="#sec-contact">Book This Artist</a>';
    $("v2Cta").innerHTML = cta;
  }

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
      return '<div class="v2-date">' +
        '<div class="v2-date-when"><div class="v2-date-mon">' + MON[d.getMonth()] + "</div>" +
        '<div class="v2-date-day">' + d.getDate() + "</div></div>" +
        '<div class="v2-date-main"><div class="v2-date-venue">' + esc(where || "Private booking") + "</div>" +
        '<div class="v2-date-meta">' + esc(time) + "</div></div></div>";
    }).join("");
  }

  // ── setlist ─────────────────────────────────────────────────────────
  function renderSetlist(data) {
    var songs = (data && data.songs) || [];
    $("v2SongCount").textContent = songs.length ? songs.length + " songs" : "";
    if (!songs.length) { show("v2SongsEmpty", true); return; }
    present["sec-setlist"] = true;

    $("v2Songs").innerHTML = songs.map(function (s, i) {
      return '<div class="v2-song"><span class="v2-song-n">' + (i + 1) + "</span>" +
        '<span><span class="v2-song-t">' + esc(s.song_title || "") + "</span>" +
        (s.original_artist ? '<br><span class="v2-song-a">' + esc(s.original_artist) + "</span>" : "") +
        "</span></div>";
    }).join("");
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

    var html = "";
    list.forEach(function (s) {
      var url = a[s[1]];
      if (!url) return;
      // website_public gates the website link only — socials are public.
      if (s[0] === "website" && !a.website_public) return;
      html += '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(s[2]) + "</a>";
    });
    $("v2Social").innerHTML = html || '<span class="v2-empty">No links yet.</span>';

    var booking = a.booking_contact || "";
    $("v2Contact").innerHTML =
      "<h3>Book " + esc(a.name || "this artist") + "</h3>" +
      "<p>" + (booking ? esc(booking) : "Contact details not listed.") + "</p>" +
      '<a class="v2-btn primary" href="/app/venue-create-gigs.html">Invite as Preferred Artist</a>';
  }

  // ── nav ─────────────────────────────────────────────────────────────
  function renderNav() {
    var html = "";
    SECTIONS.forEach(function (s) {
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
    var links = Array.prototype.slice.call($("v2NavInner").querySelectorAll("a"));
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
  })();
})();
