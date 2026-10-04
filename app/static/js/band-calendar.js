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
      /* One small square per member, carrying their initials in their own
         colour, so a day's line-up reads at a glance. */
      ".gfbc-dot { width:16px; height:16px; border-radius:4px; font-size:0.52rem; font-weight:700;",
      "  display:flex; align-items:center; justify-content:center; color:#0b0f17; letter-spacing:-0.02em; }",
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
      ".gfbc-free { color:#34d399; font-size:0.78rem; }",
      ".gfbc-act { margin-top:10px; display:flex; flex-wrap:wrap; gap:8px; }",
      ".gfbc-act button { border-radius:6px; cursor:pointer; font-size:0.76rem; padding:6px 13px;",
      "  border:1px solid var(--border); background:rgba(255,255,255,0.06); color:var(--text); }",
      ".gfbc-act button:hover { border-color:var(--cyan); color:var(--cyan); }",
      ".gfbc-act button.gfbc-danger:hover { border-color:var(--gfbc-block); color:var(--gfbc-block); }",
      ".gfbc-status { font-size:0.74rem; color:var(--text-gray); min-height:16px; margin-top:8px; }",
      ".gfbc-status.ok { color:#34d399; } .gfbc-status.err { color:#ef4444; }",
      ".gfbc-listbtn { background:rgba(255,255,255,0.06); border:1px solid var(--border);",
      "  color:var(--text); border-radius:6px; cursor:pointer; font-size:0.78rem; padding:4px 10px; }",
      ".gfbc-listbtn:hover { border-color:var(--cyan); color:var(--cyan); }",
      ".gfbc-modal { position:fixed; inset:0; z-index:9100; display:none; padding:20px;",
      "  background:rgba(3,5,10,0.88); align-items:center; justify-content:center; }",
      ".gfbc-modal.open { display:flex; }",
      ".gfbc-sheet { background:#151b28; border:1px solid var(--border); border-radius:12px;",
      "  width:min(560px,100%); max-height:85vh; display:flex; flex-direction:column;",
      "  box-shadow:0 30px 80px rgba(0,0,0,0.6); }",
      ".gfbc-sheet-head { display:flex; align-items:center; justify-content:space-between;",
      "  padding:13px 17px; border-bottom:1px solid rgba(148,163,184,0.32); }",
      ".gfbc-sheet-head h4 { margin:0; font-size:0.92rem; color:var(--text); font-weight:700; }",
      ".gfbc-sheet-head button { background:none; border:none; color:var(--text-gray);",
      "  font-size:1.4rem; line-height:1; cursor:pointer; padding:0 4px; }",
      ".gfbc-sheet-head button:hover { color:var(--text); }",
      ".gfbc-sheet-body { padding:6px 17px 16px; overflow-y:auto; }",
      /* One row per run of consecutive days. Dates left, who is out right, so
         the eye runs down a single column of dates. */
      /* A grid, not a flex row: with min-width the longest range ("Sat, Jan 30,
         2027 - Mon, Feb 1, 2027") pushed its own names column right and every
         line started somewhere different. Fixed tracks keep the three columns
         in line down the sheet. */
      ".gfbc-li { display:grid; grid-template-columns:252px 1fr auto; gap:14px;",
      "  align-items:baseline; padding:8px 0; font-size:0.84rem;",
      "  border-bottom:1px solid rgba(148,163,184,0.14); }",
      ".gfbc-li:last-child { border-bottom:none; }",
      ".gfbc-li-d { color:var(--text); font-weight:600; white-space:nowrap;",
      "  font-variant-numeric:tabular-nums; }",
      ".gfbc-li-n { color:var(--text-gray); font-size:0.79rem; }",
      ".gfbc-li-c { color:var(--text-muted); font-size:0.72rem; white-space:nowrap;",
      "  text-align:right; font-variant-numeric:tabular-nums; }",
      ".gfbc-empty { color:var(--text-gray); font-size:0.84rem; padding:14px 0; }",
      "@media (max-width:520px) { .gfbc-li { grid-template-columns:1fr auto; }",
      "  .gfbc-li-d { grid-column:1 / -1; } }",
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
          // One look for "cannot play". A day off and a gig with another band
          // are the same fact to this band, and the second is not ours to
          // broadcast.
          return '<span class="gfbc-dot" style="background:hsl(' + hueFor(m.user_id) +
                 ',70%,62%)" title="' + esc(m.name) + '">' + esc(initials(m.name)) + "</span>";
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
        '<span><i style="background:hsl(200,70%,62%)"></i>A member is off — booking still allowed</span>'
      : '<span><i style="background:rgba(245,158,11,0.6)"></i>You are off</span>';

    this.root.innerHTML =
      '<div class="gfbc">' +
        '<div class="gfbc-head">' +
          '<span class="gfbc-title">' + MONTHS[this.m] + " " + this.y + "</span>" +
          '<span class="gfbc-nav">' +
            '<button type="button" class="gfbc-listbtn" data-list="1">All dates</button>' +
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

    var listBtn = this.root.querySelector("[data-list]");
    if (listBtn) listBtn.addEventListener("click", function () { self.openList(); });

    this.root.querySelectorAll("[data-nav]").forEach(function (b) {
      b.addEventListener("click", function () {
        var n = Number(b.dataset.nav);
        if (n === 0) { var t = new Date(); self.y = t.getFullYear(); self.m = t.getMonth(); }
        else { self.m += n; if (self.m < 0) { self.m = 11; self.y--; } else if (self.m > 11) { self.m = 0; self.y++; } }
        self.selected = null;
        self.load();
      });
    });

    // Click toggles, hover inspects. One gesture cannot do both: the panel
    // used to appear only after a click, so the only way to read who was away
    // was to mark yourself away and then click again to undo it — and the help
    // text cheerfully told people to do exactly that.
    //
    // Marking days is the frequent, bulk action, so it keeps the click.
    // Reading a day is occasional, so it moves to hover and keyboard focus,
    // neither of which changes anything.
    this.root.querySelectorAll(".gfbc-cell[data-day]").forEach(function (c) {
      c.addEventListener("click", function () { self.onDay(c.dataset.day); });
      c.addEventListener("mouseenter", function () { self.peek(c.dataset.day); });
      c.addEventListener("focus", function () { self.peek(c.dataset.day); });
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
      var gig = list.filter(function (m) { return m.is_self && m.locked; })[0];
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

  // Read-only. Deliberately does NOT set this.selected: a hovered day must not
  // survive a re-render as though the user had chosen it.
  Cal.prototype.peek = function (day) {
    if (this.busy) return;          // mid-save, the panel is about to redraw
    this.showDetail(day);
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
        return "<span>" + esc(m.name) + (m.is_self ? " (you)" : "") + "</span>";
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

  var mounted = {};   // element id -> Cal, so two callers cannot double-mount

  // ── "All dates" snapshot ──────────────────────────────────────────────
  // A month grid answers "is the 14th free". It is poor at "when are we out
  // over the next year", which is the question when someone is planning. This
  // lists every marked day in order, collapsing consecutive days into one row.

  function addDays(isoStr, n) {
    var p = isoStr.split("-");
    var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    d.setDate(d.getDate() + n);
    return iso(d);
  }

  function prettyDay(isoStr) {
    return new Date(isoStr + "T12:00:00").toLocaleDateString(undefined, {
      weekday: "short", month: "short", day: "numeric", year: "numeric"
    });
  }

  // Two consecutive days only belong on one line when the SAME people are out
  // on both. Merging on date alone would print "Oct 10 - Oct 12" over three
  // days that each had a different member away, which is just wrong.
  function mergeRuns(days, keyOf) {
    var dates = Object.keys(days).filter(function (d) { return (days[d] || []).length; }).sort();
    var runs = [];
    dates.forEach(function (d) {
      var k = keyOf(days[d]);
      var last = runs[runs.length - 1];
      if (last && last.key === k && addDays(last.end, 1) === d) {
        last.end = d;
        last.count += 1;
        return;
      }
      runs.push({ start: d, end: d, key: k, count: 1, entries: days[d] });
    });
    return runs;
  }

  Cal.prototype.openList = async function () {
    var self = this;
    // On <body>, not inside root: render() rewrites root.innerHTML on every
    // toggle, which would tear the sheet out from under whoever is reading it.
    if (!this._sheetId) {
      this._sheetId = "gfbcSheet_" + (this.root.id || Math.random().toString(36).slice(2));
    }
    var host = document.getElementById(this._sheetId);
    if (!host) {
      host = document.createElement("div");
      host.className = "gfbc-modal";
      host.id = this._sheetId;
      document.body.appendChild(host);
      host.addEventListener("click", function (e) {
        if (e.target === host || (e.target.closest && e.target.closest("[data-close]"))) {
          host.classList.remove("open");
        }
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") host.classList.remove("open");
      });
    }
    host.innerHTML = '<div class="gfbc-sheet"><div class="gfbc-sheet-head">' +
      "<h4>All dates</h4><button type=\"button\" data-close=\"1\" aria-label=\"Close\">\u00D7</button>" +
      '</div><div class="gfbc-sheet-body"><p class="gfbc-empty">Loading\u2026</p></div></div>';
    host.classList.add("open");

    // The grid holds three months; the snapshot asks for its own range rather
    // than listing whatever happens to be loaded.
    //
    // Starts at the 1st of the current month, not today: a day marked earlier
    // this month is still on the grid, and a list that silently omitted it
    // would not be "all dates". Runs 13 months, so the same month next year is
    // included rather than falling just outside the window.
    //
    // Month arithmetic via Date, not a day count — `new Date(y, m + 13, 0)` is
    // the last day of the 13th month and handles month lengths, year rollover
    // and leap years without any of it being spelled out here.
    var _now = new Date();
    var from = iso(new Date(_now.getFullYear(), _now.getMonth(), 1));
    var to = iso(new Date(_now.getFullYear(), _now.getMonth() + 13, 0));
    var days = {};
    try {
      var url = this.o.mode === "band"
        ? "/api/artists/" + this.o.artistId + "/calendar?start=" + from + "&end=" + to
        : "/api/me/days-off?start=" + from + "&end=" + to;
      var res = await fetch(url, { credentials: "include" });
      if (res.ok) days = (await res.json()).days || {};
    } catch (e) { /* falls through to the empty state */ }

    var isBand = this.o.mode === "band";
    var runs = mergeRuns(days, function (list) {
      return isBand
        ? list.map(function (m) { return m.user_id; }).sort().join(",")
        : "me";
    });

    var body = host.querySelector(".gfbc-sheet-body");
    if (!runs.length) {
      body.innerHTML = '<p class="gfbc-empty">Nothing marked in the next 13 months.</p>';
      return;
    }
    body.innerHTML = runs.map(function (r) {
      var when = r.start === r.end
        ? prettyDay(r.start)
        : prettyDay(r.start) + " \u2013 " + prettyDay(r.end);
      var who = isBand
        ? r.entries.map(function (m) {
            return esc(m.name) + (m.is_self ? " (you)" : "");
          }).join(", ")
        : "";
      // All three cells always, even when empty: a skipped cell would slide the
      // next row's content into the wrong column.
      return '<div class="gfbc-li">' +
               '<span class="gfbc-li-d">' + esc(when) + "</span>" +
               '<span class="gfbc-li-n">' + who + "</span>" +
               '<span class="gfbc-li-c">' + (r.count > 1 ? r.count + " days" : "") + "</span>" +
             "</div>";
    }).join("");
  };

  window.gfBandCalendar = {
    mount: function (opts) {
      if (!opts || !opts.el) return null;
      var key = typeof opts.el === "string" ? opts.el : (opts.el.id || "");
      if (key && mounted[key]) return mounted[key];
      var c = new Cal(opts);
      if (!c.root) return null;
      if (key) mounted[key] = c;
      c.load();
      return c;
    }
  };

  // Self-mount from markup. The calendar used to appear only if a separate
  // page-init file called mount(), and when that file was rewritten without a
  // new cache-buster, browsers kept the old copy: it still revealed the panel
  // but never mounted anything, so the tab rendered empty with no error. This
  // removes the dependency — the element that needs a calendar says so itself.
  function autoMount() {
    document.querySelectorAll("[data-gfbc-mode]").forEach(function (el) {
      var mode = el.getAttribute("data-gfbc-mode");
      var opts = { el: el.id || el, mode: mode };
      if (mode === "band") {
        var aid = el.getAttribute("data-artist-id") ||
                  new URLSearchParams(window.location.search).get("artist_id");
        if (!aid) return;                       // nothing to show a calendar for
        opts.artistId = parseInt(aid, 10);
        var section = document.getElementById("availabilitySection");
        if (section && section.style.display === "none") section.style.display = "";
      }
      window.gfBandCalendar.mount(opts);
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoMount);
  } else {
    autoMount();
  }
})();
