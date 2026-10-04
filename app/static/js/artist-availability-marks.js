/**
 * Member-unavailable marks on the artist's booking calendar.
 * ==========================================================
 * 2026-10-08. The band calendar knows who cannot play on which days, but the
 * calendar an artist actually books from knew nothing about it — so you could
 * be looking straight at an open Saturday with half the band away and have no
 * idea until after you applied.
 *
 * Draws a small square per absent member in the bottom-left of each day cell,
 * carrying their initials in the same per-person colour the band calendar
 * uses, so the two read as one thing.
 *
 * Injected rather than built into renderCalendar(): artist.book-gigs.js is
 * ~127KB and already exposes `.calendar-day[data-date]` as a decoration hook
 * for the external-gig bubbles. This follows that, including the
 * MutationObserver + rAF and the self-mutation guard — without the guard the
 * observer sees its own marks and loops.
 */
(function () {
  "use strict";

  var MARK = "gf-unavail-mark";
  var _days = {};            // iso -> [{user_id, name}]
  var _loadedKey = "";       // month window already fetched
  var _artistId = null;
  var _injecting = false;
  var _mo = null;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Same derivation as band-calendar.js, so a member is the same colour in
  // both places. Changing one without the other would be worse than no colour.
  function hueFor(id) { return (Number(id) * 47) % 360; }

  function initials(name) {
    var p = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!p.length) return "?";
    if (p.length === 1) return p[0].slice(0, 2).toUpperCase();
    return (p[0][0] + p[p.length - 1][0]).toUpperCase();
  }

  function styles() {
    if (document.getElementById("gfUnavailStyles")) return;
    var el = document.createElement("style");
    el.id = "gfUnavailStyles";
    el.textContent = [
      /* Bottom-left, clear of the gig bubbles which stack from the top. The
         cell is given position:relative only if it has none, so an existing
         layout is not disturbed. */
      "." + MARK + " { position:absolute; left:4px; bottom:3px; display:flex;",
      "  gap:2px; align-items:center; pointer-events:auto; z-index:2; }",
      /* 16px and 4px radius to match the squares on the band calendar — a
         member should be the same mark in both places. */
      "." + MARK + " b { width:16px; height:16px; border-radius:4px;",
      "  font-size:0.52rem; font-weight:700; line-height:16px; text-align:center;",
      "  color:#0b0f17; letter-spacing:-0.02em; display:block; }",
      "." + MARK + " i { font-style:normal; font-size:0.54rem; color:var(--text-gray); }",
      "@media (max-width:620px) { ." + MARK + " b { width:11px; height:11px;",
      "  line-height:11px; font-size:0.42rem; } }"
    ].join("\n");
    document.head.appendChild(el);
  }

  function monthWindow() {
    // Whatever the grid is showing, plus a month either side — the visible
    // grid spills into neighbouring months.
    var cal = document.getElementById("calendar") ||
              document.querySelector(".calendar, #calendarEl");
    var dates = cal ? Array.prototype.slice
      .call(cal.querySelectorAll(".calendar-day[data-date]"))
      .map(function (c) { return c.getAttribute("data-date"); })
      .filter(Boolean).sort() : [];
    if (!dates.length) return null;
    return { start: dates[0], end: dates[dates.length - 1] };
  }

  async function load() {
    var w = monthWindow();
    if (!w || !_artistId) return false;
    var key = w.start + ".." + w.end;
    if (key === _loadedKey) return true;      // same grid, nothing to refetch
    try {
      var r = await fetch("/api/artists/" + _artistId + "/calendar?start=" +
                          w.start + "&end=" + w.end, { credentials: "include" });
      if (!r.ok) return false;                // not a member of this artist
      var d = await r.json();
      _days = (d && d.days) || {};
      _loadedKey = key;
      return true;
    } catch (e) { return false; }
  }

  function inject() {
    if (_injecting) return;
    var cal = document.getElementById("calendar") ||
              document.querySelector(".calendar, #calendarEl");
    if (!cal) return;
    _injecting = true;
    try {
      styles();
      // Clear calendar-wide first. Removing only the dates in the fresh set
      // would strand a mark on a day whose last absence was just undone.
      cal.querySelectorAll("." + MARK).forEach(function (el) { el.remove(); });

      cal.querySelectorAll(".calendar-day[data-date]").forEach(function (cell) {
        var iso = cell.getAttribute("data-date");
        var off = iso && _days[iso];
        if (!off || !off.length) return;

        if (getComputedStyle(cell).position === "static") {
          cell.style.position = "relative";
        }
        var names = off.map(function (m) { return m.name; }).join(", ");
        var wrap = document.createElement("div");
        wrap.className = MARK;
        wrap.title = names + (off.length === 1 ? " is" : " are") + " unavailable";

        // Two initials then a count: three 14px squares already crowd a cell
        // that may also be carrying gig bubbles.
        off.slice(0, 2).forEach(function (m) {
          var b = document.createElement("b");
          b.style.background = "hsl(" + hueFor(m.user_id) + ",70%,62%)";
          b.textContent = initials(m.name);
          wrap.appendChild(b);
        });
        if (off.length > 2) {
          var more = document.createElement("i");
          more.textContent = "+" + (off.length - 2);
          wrap.appendChild(more);
        }
        cell.appendChild(wrap);
      });
    } finally {
      _injecting = false;
    }
  }

  async function refresh() {
    if (await load()) inject();
  }

  function start() {
    _artistId = new URLSearchParams(window.location.search).get("artist_id");
    if (!_artistId) return;
    var cal = document.getElementById("calendar") ||
              document.querySelector(".calendar, #calendarEl");
    if (!cal) { setTimeout(start, 400); return; }   // calendar not built yet

    refresh();
    // The grid is rebuilt wholesale on month change, so watch for it. rAF
    // coalesces the burst of mutations a re-render produces into one pass.
    _mo = new MutationObserver(function () {
      if (_injecting) return;                        // ignore our own marks
      cancelAnimationFrame(_mo._raf);
      _mo._raf = requestAnimationFrame(refresh);
    });
    _mo.observe(cal, { childList: true, subtree: true });
  }

  window.gfAvailabilityMarks = { refresh: refresh };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
