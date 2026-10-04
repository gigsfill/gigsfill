/**
 * Band calendar — member days off
 * ===============================
 * 2026-10-04. Replaces the date-range + reason forms that fed
 * `user_availability` and `artist_availability`.
 *
 * Members click days they're gone. No reason is collected: in practice it was
 * blank or "Out of Town", and nobody making a booking decision ever saw it.
 *
 * Two mounts, same grid:
 *   mode "band" — on the artist's Availability tab. Clicking toggles YOUR day
 *                 for that band, and the grid shows every member's days so the
 *                 band can see at a glance when everyone is free.
 *   mode "me"   — on your own profile. Clicking marks you off across EVERY
 *                 band you play in, because "I'm away that weekend" is a fact
 *                 about you, not about one band.
 *
 * Member days are SOFT and always have been. They never make the artist
 * unavailable — a band that plays as a four-piece and sometimes as a duo must
 * still be bookable when the drummer is out, so booking only warns and names
 * who is away. Only an explicit band-wide mark (admin, deliberate, for a
 * hiatus or a tour) hard-blocks, because that one also pulls the artist out of
 * preferred-artist blasts and the open-gig digest.
 *
 * Usage:
 *   window.gfBandCalendar.mount({ el: 'bandCalendar', mode: 'band', artistId: 7 })
 *   window.gfBandCalendar.mount({ el: 'myCalendar',   mode: 'me' })
 */
(function () {
  "use strict";

  var MONTHS = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"];
  var DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Local-time ISO. new Date().toISOString() is UTC, which puts anyone west of
  // Greenwich on the wrong day for most of the evening — the exact hours a
  // musician is likely to be filling this in.
  function iso(d) {
    return d.getFullYear() + "-" +
           String(d.getMonth() + 1).padStart(2, "0") + "-" +
           String(d.getDate()).padStart(2, "0");
  }

  function initials(name) {
    var parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  // Stable per-person colour so the same member reads the same across months.
  function hueFor(id) { return (Number(id) * 47) % 360; }

  function injectStyles() {
    if (document.getElementById("gfBandCalStyles")) return;
    var css = document.createElement("style");
    css.id = "gfBandCalStyles";
    css.textContent = [
      ".gfbc { --gfbc-off: #f59e0b; --gfbc-block: #ef4444; }",
      ".gfbc-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; }",
      ".gfbc-title { font-size:0.95rem; font-weight:700; color:var(--text); }",
      ".gfbc-nav { display:flex; gap:6px; }",
      ".gfbc-nav button, .gfbc-today { background:rgba(255,255,255,0.06); border:1px solid var(--border);",
      "  color:var(--text); border-radius:6px; cursor:pointer; font-size:0.78rem; padding:4px 10px; }",
      ".gfbc-nav button:hover, .gfbc-today:hover { border-color:var(--cyan); color:var(--cyan); }",
      ".gfbc-dow { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; margin-bottom:4px; }",
      ".gfbc-dow span { text-align:center; font-size:0.68rem; color:var(--text-gray); font-weight:600;",
      "  text-transform:uppercase; letter-spacing:0.04em; }",
      ".gfbc-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }",
      // Square cells ballooned to ~140px tall in a full-width panel. A fixed
      // height keeps a month scannable in one screen, which is the whole point
      // of showing the band a grid.
      ".gfbc-cell { position:relative; height:62px; border:1px solid var(--border);",
      "  border-radius:7px; background:rgba(255,255,255,0.02); cursor:pointer; padding:3px 4px;",
      "  display:flex; flex-direction:column; align-items:flex-start; gap:2px; }",
      ".gfbc-cell:hover { border-color:var(--cyan); }",
      ".gfbc-cell[disabled] { cursor:default; opacity:0.35; }",
      ".gfbc-cell.gfbc-pad { visibility:hidden; }",
      ".gfbc-cell.gfbc-past { opacity:0.4; }",
      ".gfbc-num { font-size:0.74rem; color:var(--text-gray); font-variant-numeric:tabular-nums; line-height:1; }",
      ".gfbc-cell.gfbc-today-cell .gfbc-num { color:var(--cyan); font-weight:800; }",
      /* Your own day reads as a filled state, not a dot among others: it is the
         one you can change by clicking. */
      ".gfbc-cell.gfbc-mine { background:rgba(245,158,11,0.16); border-color:rgba(245,158,11,0.55); }",
      ".gfbc-dots { display:flex; flex-wrap:wrap; gap:2px; margin-top:auto; }",
      ".gfbc-dot { width:15px; height:15px; border-radius:50%; font-size:0.52rem; font-weight:700;",
      "  display:flex; align-items:center; justify-content:center; color:#0b0f17; letter-spacing:-0.02em; }",
      /* A gig with another band is a fact, not a choice: square it off and
         ring it so it never reads as something you clicked. */
      ".gfbc-dot.gfbc-gig { border-radius:4px; box-shadow:0 0 0 1.5px rgba(255,255,255,0.45) inset; }",
      ".gfbc-more { font-size:0.56rem; color:var(--text-gray); align-self:center; }",
      ".gfbc-legend { display:flex; flex-wrap:wrap; gap:12px; margin-top:12px; font-size:0.72rem; color:var(--text-gray); }",
      ".gfbc-legend i { display:inline-block; width:11px; height:11px; border-radius:3px; margin-right:5px;",
      "  vertical-align:-1px; }",
      ".gfbc-detail { margin-top:12px; padding:11px 13px; border:1px solid var(--border); border-radius:8px;",
      "  background:rgba(255,255,255,0.02); font-size:0.82rem; }",
      ".gfbc-detail h4 { margin:0 0 7px; font-size:0.82rem; color:var(--text); font-weight:700; }",
      ".gfbc-who { display:flex; flex-wrap:wrap; gap:6px; }",
      ".gfbc-who span { padding:2px 9px; border-radius:999px; font-size:0.72rem;",
      "  background:rgba(245,158,11,0.14); color:#fbbf24; border:1px solid rgba(245,158,11,0.3); }",
      ".gfbc-who span.gfbc-gigchip { background:rgba(52,211,153,0.13); color:#6ee7b7;",
      "  border-color:rgba(52,211,153,0.33); }",
      ".gfbc-free { color:#34d399; font-size:0.78rem; }",
      ".gfbc-act { margin-top:10px; display:flex; flex-wrap:wrap; gap:8px; }",
      ".gfbc-act button { border-radius:6px; cursor:pointer; font-size:0.76rem; padding:6px 13px;",
      "  border:1px solid var(--border); background:rgba(255,255,255,0.06); color:var(--text); }",
      ".gfbc-act button:hover { border-color:var(--cyan); color:var(--cyan); }",
      ".gfbc-act button.gfbc-danger:hover { border-color:var(--gfbc-block); color:var(--gfbc-block); }",
      ".gfbc-status { font-size:0.74rem; color:var(--text-gray); min-height:16px; margin-top:8px; }",
      ".gfbc-status.ok { color:#34d399; } .gfbc-status.err { color:#ef4444; }",
      "@media (max-width:520px) { .gfbc-cell { height:50px; } .gfbc-dot { width:13px; height:13px; } }"
    ].join("\n");
    document.head.appendChild(css);
  }

  function Cal(opts) {
    this.o = opts;
    this.root = typeof opts.el === "string" ? document.getElementById(opts.el) : opts.el;
    var now = new Date();
    this.y = now.getFullYear();
    this.m = now.getMonth();
    this.data = { days: {}, members: [] };
    this.selected = null;
    this.busy = false;
  }

  Cal.prototype.url = function (path) {
    return this.o.mode === "band"
      ? "/api/artists/" + this.o.artistId + path
      : "/api/me" + path;
  };

  Cal.prototype.range = function () {
    // One month either side, so moving back and forth does not refetch.
    var s = new Date(this.y, this.m - 1, 1);
    var e = new Date(this.y, this.m + 2, 0);
    return { start: iso(s), end: iso(e) };
  };

  Cal.prototype.load = async function () {
    var r = this.range();
    var url = this.o.mode === "band"
      ? "/api/artists/" + this.o.artistId + "/calendar?start=" + r.start + "&end=" + r.end
      : "/api/me/days-off?start=" + r.start + "&end=" + r.end;
    try {
      var res = await fetch(url, { credentials: "include" });
      if (!res.ok) throw new Error("load failed");
      var j = await res.json();
      this.data = {
        days: j.days || {},
        members: j.members || []
      };
    } catch (e) {
      this.data = { days: {}, members: [] };
      this.err = "Could not load the calendar.";
    }
    this.render();
  };

  Cal.prototype.status = function (msg, kind) {
    var el = this.root.querySelector(".gfbc-status");
    if (!el) return;
    el.className = "gfbc-status" + (kind ? " " + kind : "");
    el.textContent = msg || "";
    if (kind === "ok") {
      clearTimeout(el._t);
      el._t = setTimeout(function () { el.textContent = ""; el.className = "gfbc-status"; }, 1800);
    }
  };

  // Who is off on a day, in the shape the band view uses. The personal view
  // stores scope rows instead, so it answers from its own data.
  Cal.prototype.offOn = function (day) {
    return (this.data.days && this.data.days[day]) || [];
  };

  Cal.prototype.iAmOff = function (day) {
    var list = this.offOn(day);
    if (this.o.mode === "band") return list.some(function (m) { return m.is_self; });
    return list.length > 0;   // personal view only ever holds your own rows
  };

  Cal.prototype.render = function () {
    var self = this;
    injectStyles();
    var first = new Date(this.y, this.m, 1);
    var lead = first.getDay();
    var dim = new Date(this.y, this.m + 1, 0).getDate();
    var todayIso = iso(new Date());

    var cells = "";
    for (var i = 0; i < lead; i++) cells += '<div class="gfbc-cell gfbc-pad"></div>';

    for (var d = 1; d <= dim; d++) {
      var day = iso(new Date(this.y, this.m, d));
      var off = this.offOn(day);
      var cls = "gfbc-cell";
      if (day === todayIso) cls += " gfbc-today-cell";
      if (day < todayIso) cls += " gfbc-past";
      if (this.iAmOff(day)) cls += " gfbc-mine";

      var dots = "";
      if (this.o.mode === "band") {
        var shown = off.slice(0, 3);
        dots = shown.map(function (m) {
          var isGig = m.scope === "gig";
          var tip = m.name + (isGig ? " — booked with " + (m.with_artist || "another band") : "");
          return '<span class="gfbc-dot' + (isGig ? " gfbc-gig" : "") +
                 '" style="background:hsl(' + hueFor(m.user_id) +
                 ',70%,62%)" title="' + esc(tip) + '">' + esc(initials(m.name)) + "</span>";
        }).join("");
        if (off.length > shown.length) {
          dots += '<span class="gfbc-more">+' + (off.length - shown.length) + "</span>";
        }
      } else if (off.length) {
        // Personal view: show where the day applies, not who.
        var anyGlobal = off.some(function (x) { return x.scope === "all"; });
        dots = '<span class="gfbc-more">' + (anyGlobal ? "all bands" : "1 band") + "</span>";
      }

      cells += '<button type="button" class="' + cls + '" data-day="' + day + '">' +
                 '<span class="gfbc-num">' + d + "</span>" +
                 '<span class="gfbc-dots">' + dots + "</span>" +
               "</button>";
    }

    var legend = this.o.mode === "band"
      ? '<span><i style="background:rgba(245,158,11,0.6)"></i>You are off</span>' +
        '<span><i style="background:hsl(200,70%,62%)"></i>A member is off — booking still allowed</span>' +
        '<span><i style="background:hsl(140,70%,62%);border-radius:2px;box-shadow:0 0 0 1.5px rgba(255,255,255,0.45) inset"></i>Booked with another band — automatic</span>'
      : '<span><i style="background:rgba(245,158,11,0.6)"></i>You are off</span>';

    this.root.innerHTML =
      '<div class="gfbc">' +
        '<div class="gfbc-head">' +
          '<span class="gfbc-title">' + MONTHS[this.m] + " " + this.y + "</span>" +
          '<span class="gfbc-nav">' +
            '<button type="button" data-nav="-1" aria-label="Previous month">‹</button>' +
            '<button type="button" class="gfbc-today" data-nav="0">Today</button>' +
            '<button type="button" data-nav="1" aria-label="Next month">›</button>' +
          "</span>" +
        "</div>" +
        '<div class="gfbc-dow">' + DOW.map(function (x) { return "<span>" + x + "</span>"; }).join("") + "</div>" +
        '<div class="gfbc-grid">' + cells + "</div>" +
        '<div class="gfbc-legend">' + legend + "</div>" +
        '<div class="gfbc-status">' + (this.err ? esc(this.err) : "") + "</div>" +
        '<div class="gfbc-detail" style="display:none;"></div>' +
      "</div>";

    this.root.querySelectorAll("[data-nav]").forEach(function (b) {
      b.addEventListener("click", function () {
        var n = Number(b.dataset.nav);
        if (n === 0) { var t = new Date(); self.y = t.getFullYear(); self.m = t.getMonth(); }
        else { self.m += n; if (self.m < 0) { self.m = 11; self.y--; } else if (self.m > 11) { self.m = 0; self.y++; } }
        self.selected = null;
        self.load();
      });
    });

    this.root.querySelectorAll(".gfbc-cell[data-day]").forEach(function (c) {
      c.addEventListener("click", function () { self.onDay(c.dataset.day); });
    });

    if (this.selected) this.showDetail(this.selected);
  };

  // One click does both jobs the band asked for: it toggles you, and it shows
  // who else cannot play that day.
  Cal.prototype.onDay = async function (day) {
    if (this.busy) return;
    this.busy = true;
    this.status("Saving…");
    try {
      var res = await fetch(this.url("/days-off/toggle"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ day: day })
      });
      if (!res.ok) {
        var msg = "Could not save that day.";
        try { var b = await res.json(); if (b && b.detail) msg = b.detail; } catch (_) {}
        this.status(msg, "err");
        this.busy = false;
        return;
      }
      var j = await res.json();
      this.applyLocal(day, j.off);
      this.selected = day;
      this.status(j.off ? "Marked off" : "Marked available", "ok");
    } catch (e) {
      this.status("Could not save that day.", "err");
    }
    this.busy = false;
    this.render();
  };

  // Update in place rather than refetching: the grid should flip the instant
  // it is clicked, and someone marking a whole month off clicks ~12 times.
  Cal.prototype.applyLocal = function (day, isOff) {
    var list = this.data.days[day] || [];
    if (this.o.mode === "band") {
      // A gig with another band is derived from a booking, not from a click,
      // so un-clicking must not wipe it off the grid — only the server
      // cancelling that gig can. Without this guard, clearing your own day
      // would appear to clear a booking you are still committed to.
      var gig = list.filter(function (m) { return m.is_self && m.scope === "gig"; })[0];
      if (isOff) {
        if (!list.some(function (m) { return m.is_self; })) {
          list.push({ user_id: this.meId(), name: this.meName(), scope: "band", is_self: true });
        }
      } else {
        list = list.filter(function (m) { return !m.is_self; });
        if (gig) list.push(gig);
      }
    } else {
      list = isOff ? [{ artist_id: 0, artist_name: null, scope: "all" }] : [];
    }
    if (list.length) this.data.days[day] = list; else delete this.data.days[day];
  };

  Cal.prototype.meId = function () {
    var me = (this.data.members || []).filter(function (m) { return m.is_self; })[0];
    return me ? me.user_id : -1;
  };
  Cal.prototype.meName = function () {
    var me = (this.data.members || []).filter(function (m) { return m.is_self; })[0];
    return me ? me.name : "You";
  };

  Cal.prototype.showDetail = function (day) {
    var box = this.root.querySelector(".gfbc-detail");
    if (!box) return;
    var off = this.offOn(day);
    var pretty = new Date(day + "T12:00:00").toLocaleDateString(undefined, {
      weekday: "long", month: "long", day: "numeric", year: "numeric"
    });

    var who;
    if (this.o.mode !== "band") {
      who = off.length
        ? '<div class="gfbc-who"><span>You are off' +
          (off.some(function (x) { return x.scope === "all"; }) ? " — all bands" : "") + "</span></div>"
        : '<div class="gfbc-free">You are available.</div>';
    } else if (!off.length) {
      who = '<div class="gfbc-free">Everyone is available.</div>';
    } else {
      who = '<div class="gfbc-who">' + off.map(function (m) {
        var label = esc(m.name) + (m.is_self ? " (you)" : "");
        if (m.scope === "gig") {
          label += " — booked with " + esc(m.with_artist || "another band");
          if (m.venue) label += " at " + esc(m.venue);
        }
        return '<span' + (m.scope === "gig" ? ' class="gfbc-gigchip"' : "") + ">" + label + "</span>";
      }).join("") + "</div>" +
      '<p style="margin:8px 0 0;color:var(--text-gray);font-size:0.76rem;">' +
      "Bookings on this date still go through — you'll see a warning naming " +
      "whoever is away, and can book anyway if the line-up still works." + "</p>";
    }

    // 2026-10-05: the "block the whole band" control is gone with the rest of
    // artist-level availability. Nothing on this calendar sets band state any
    // more — you click your own days, everyone else's are a read-only view.
    var act = "";

    box.style.display = "block";
    box.innerHTML = "<h4>" + esc(pretty) + "</h4>" + who + act;

  };

  window.gfBandCalendar = {
    mount: function (opts) {
      if (!opts || !opts.el) return null;
      var c = new Cal(opts);
      if (!c.root) return null;
      c.load();
      return c;
    }
  };
})();
