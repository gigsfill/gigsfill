/**
 * Per-gig room spec overrides.
 * ============================
 * 2026-10-07, reworked 2026-10-08. Every gig used to inherit the venue's
 * standing spec through a live JOIN. A venue whose default is "no PA" had no
 * way to say it was bringing one for a particular night — and because artists
 * filter gig search on these fields, that gig was quietly filtered OUT of
 * results for everyone who requires sound equipment.
 *
 * One checkbox, then the same form the venue edit page shows, prefilled with
 * the venue's current answers. Tick it and edit whatever differs; everything
 * else stays as the venue has it, and the gig keeps its own complete copy.
 *
 * The earlier design made each control tri-state ("use my venue default" /
 * Yes / No) so only genuinely changed fields were pinned. It was replaced
 * because it asked venues to learn a control they see nowhere else, to solve
 * a problem they do not have. The cost is that an overridden gig is a
 * snapshot: later edits to the venue profile do not reach it. For a gig whose
 * details artists have already seen, that is arguably the safer behaviour.
 *
 * window.gfGigSpec.mount({ venue })  — venue row, used to prefill
 * window.gfGigSpec.load(gig)         — fill from a gig being edited
 * window.gfGigSpec.payload()         — { ovr_enabled, ovr_* } for the save
 */
(function () {
  "use strict";

  // Grouped the way a venue thinks about the room, not the way the columns
  // happen to be ordered.
  // Control kinds mirror the venue edit page one-for-one, so a venue is
  // filling in the form they already know:
  //   yesno  — a Yes/No <select> (venue edit uses selects, not checkboxes)
  //   num    — a short numeric box, 80px like "Width / Depth" there
  //   line   — a single-line textarea, the .single-line treatment there
  //   arrive — the Flexible / No Earlier Than select
  //   hour   — the 1..12 select, 85px
  //   ampm   — the AM/PM select, 85px
  var GROUPS = [
    ["Stage", [
      ["has_stage", "yesno", "Stage?"],
      ["stage_width_ft", "num", "Width (ft)"],
      ["stage_depth_ft", "num", "Depth (ft)"],
      ["setup_location_description", "line", "Setup location"]
    ]],
    ["Sound & light", [
      ["has_sound_equipment", "yesno", "Sound equipment?"],
      ["sound_equipment_description", "line", "What's provided"],
      ["has_sound_engineer", "yesno", "Sound engineer?"],
      ["sound_engineer_details", "line", "Engineer details"],
      ["has_lighting", "yesno", "Lighting?"],
      ["lighting_description", "line", "Lighting details"]
    ]],
    ["Getting in", [
      ["load_in_out_details", "line", "Load in / out"],
      ["arrival_time_type", "arrive", "Arrival"],
      ["arrival_no_earlier_than_hour", "hour", "No earlier than"],
      ["arrival_no_earlier_than_period", "ampm", "AM / PM"]
    ]],
    ["Hospitality", [
      ["bar_tab_details", "line", "Bar tab"],
      ["food_tab_details", "line", "Food"]
    ]]
  ];

  var FIELDS = GROUPS.reduce(function (a, g) { return a.concat(g[1]); }, []);
  var venueRow = {};

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function truthy(v) {
    return v === 1 || v === true || v === "1" || String(v).toLowerCase() === "true";
  }

  // What the venue's standing answer is, shown beside each control so the
  // venue can see what they are departing from.
  function defaultLabel(key, kind) {
    var v = venueRow[key];
    if (kind === "bool") return truthy(v) ? "Yes" : "No";
    if (v === null || v === undefined || String(v).trim() === "") return "not set";
    return String(v).length > 34 ? String(v).slice(0, 34) + "…" : String(v);
  }

  function control(key, kind) {
    var id = "ovr_" + key;
    var a = 'id="' + id + '" data-k="' + key + '" data-t="' + kind + '"';
    if (kind === "yesno") {
      return "<select " + a + '><option value="false">No</option>' +
             '<option value="true">Yes</option></select>';
    }
    if (kind === "arrive") {
      return "<select " + a + '><option value="flexible">Flexible</option>' +
             '<option value="no_earlier_than">No Earlier Than:</option></select>';
    }
    if (kind === "hour") {
      var o = '<option value="">--</option>';
      for (var h = 1; h <= 12; h++) o += "<option>" + h + "</option>";
      return "<select " + a + ' class="gf-narrow">' + o + "</select>";
    }
    if (kind === "ampm") {
      return "<select " + a + ' class="gf-narrow"><option value="PM">PM</option>' +
             '<option value="AM">AM</option></select>';
    }
    if (kind === "num") {
      return '<input type="text" inputmode="numeric" placeholder="feet" ' + a +
             ' class="gf-narrow">';
    }
    return "<textarea rows=\"1\" " + a + "></textarea>";
  }

  function render() {
    var host = document.getElementById("gigSpecFields");
    if (!host) return;
    host.innerHTML = GROUPS.map(function (grp) {
      var rows = grp[1].map(function (f) {
        return '<div class="gf-spec-row">' +
                 '<label for="ovr_' + f[0] + '">' + esc(f[2]) + "</label>" +
                 '<div class="gf-spec-ctrl">' + control(f[0], f[1]) + "</div>" +
               "</div>";
      }).join("");
      return '<div class="gf-spec-group"><h4>' + esc(grp[0]) + "</h4>" + rows + "</div>";
    }).join("");
    injectStyles();
    host.addEventListener("input", function (e) { autoGrow(e.target); });
    prefill();
  }

  // Copy the venue's current answers in. Called on mount and whenever the box
  // is ticked from empty, so the venue starts from what they already have
  // rather than a blank form they would have to retype.
  function prefill() {
    els().forEach(function (el) {
      setVal(el, venueRow[el.dataset.k]);
    });
  }

  // One place that knows how each control carries a value, so prefill, load
  // and payload cannot drift on what "yes" looks like.
  function setVal(el, v) {
    var t = el.dataset.t;
    if (t === "yesno") { el.value = truthy(v) ? "true" : "false"; return; }
    if (t === "ampm") {
      // Venue edit defaults to PM; an empty select here would save "".
      el.value = (String(v).toUpperCase() === "AM") ? "AM" : "PM";
      return;
    }
    if (t === "arrive") {
      // A venue that never set this has "" stored, which matches no option and
      // renders an empty select. Flexible is the venue-edit default too.
      el.value = (v === "no_earlier_than") ? "no_earlier_than" : "flexible";
      return;
    }
    el.value = (v === null || v === undefined) ? "" : String(v);
    autoGrow(el);
  }

  // One-line textareas clipped anything longer than the box, with no scrollbar
  // — a venue could not read back what their own bar tab said. Grow to fit,
  // capped so one long paragraph cannot push Save off the screen.
  function autoGrow(el) {
    if (el.tagName !== "TEXTAREA") return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 76) + "px";
  }

  function getVal(el) {
    var t = el.dataset.t;
    if (t === "yesno") return el.value === "true" ? 1 : 0;
    if (t === "num") {
      var n = parseFloat(String(el.value).replace(/[^0-9.]/g, ""));
      return isNaN(n) ? null : n;
    }
    return String(el.value).trim();
  }

  function injectStyles() {
    if (document.getElementById("gfSpecStyles")) return;
    var css = document.createElement("style");
    css.id = "gfSpecStyles";
    css.textContent = [
      /* Two columns on a wide modal — sixteen single-file rows made the panel
         taller than the screen and pushed Save out of reach. */
      /* auto-fit, not a fixed 2, and keyed off the container rather than the
         viewport: the modal is narrower than the window, so a viewport media
         query gave two columns of 164px and squeezed the text fields to 38px. */
      "#gigSpecFields { display:grid;",
      "  grid-template-columns:repeat(auto-fit, minmax(290px, 1fr));",
      "  gap:4px 26px; align-items:start; }",
      ".gf-spec-group { grid-column:span 1; margin:0 0 10px; }",
      ".gf-spec-group h4 { margin:0 0 6px; font-size:0.7rem; font-weight:700;",
      "  text-transform:uppercase; letter-spacing:0.07em; color:var(--cyan);",
      "  padding-bottom:4px; border-bottom:1px solid rgba(148,163,184,0.22); }",
      ".gf-spec-row { display:grid; grid-template-columns:116px minmax(0,1fr);",
      "  gap:10px; align-items:center; margin-bottom:5px; min-height:28px; }",
      ".gf-spec-row label { font-size:0.76rem; color:var(--text-gray); line-height:1.3; }",
      /* Controls size to their content rather than filling the row. A Yes/No
         select stretched edge to edge reads as a text field and made the panel
         hard to scan. */
      ".gf-spec-ctrl { min-width:0; }",
      ".gf-spec-ctrl select, .gf-spec-ctrl input, .gf-spec-ctrl textarea {",
      "  padding:4px 8px; font-size:0.78rem; background:#151b28;",
      "  border:1px solid var(--border); border-radius:6px; color:var(--text);",
      "  font-family:inherit; }",
      ".gf-spec-ctrl select { width:auto; min-width:84px; }",
      ".gf-spec-ctrl .gf-narrow { width:84px; text-align:center; }",
      ".gf-spec-ctrl input.gf-narrow { text-align:left; }",
      /* Single-line textareas, matching .single-line on the venue edit page —
         they grow if someone pastes a paragraph but start one line tall. */
      ".gf-spec-ctrl textarea { width:100%; resize:vertical; min-height:28px;",
      "  max-height:76px; overflow-y:auto; line-height:1.35; }",
      ".gf-spec-ctrl select:focus, .gf-spec-ctrl input:focus,",
      ".gf-spec-ctrl textarea:focus { outline:none; border-color:var(--cyan); }",
      "@media (max-width:620px) { .gf-spec-row {",
      "  grid-template-columns:100px minmax(0,1fr); } }"
    ].join("\n");
    document.head.appendChild(css);
  }

  function els() {
    return Array.prototype.slice.call(
      document.querySelectorAll("#gigSpecFields [data-k]"));
  }

  function enabled() {
    var c = document.getElementById("gigSpecOn");
    return !!(c && c.checked);
  }

  function syncPanel() {
    var on = enabled();
    var p = document.getElementById("gigSpecPanel");
    if (p) p.style.display = on ? "block" : "none";
    // scrollHeight is 0 while the panel is display:none, so sizing done during
    // prefill was a no-op and every field stayed one line tall, clipping its
    // own content. Re-measure once it is actually on screen.
    if (on) els().forEach(autoGrow);
    var badge = document.getElementById("gigSpecCount");
    if (badge) badge.textContent = on ? "" : "";
  }

  window.gfGigSpec = {
    mount: function (opts) {
      venueRow = (opts && opts.venue) || {};
      render();
      var chk = document.getElementById("gigSpecOn");
      if (chk && !chk._wired) {
        chk._wired = true;
        chk.addEventListener("change", function () {
          // Ticking from empty copies the venue's current answers in. Ticking
          // back on a gig that already has its own copy must NOT re-copy, or
          // a venue toggling the box twice would silently lose their edits.
          if (chk.checked && !_hasOwnCopy) prefill();
          syncPanel();
        });
      }
      var rst = document.getElementById("gigSpecReset");
      if (rst && !rst._wired) {
        rst._wired = true;
        rst.addEventListener("click", function () { window.gfGigSpec.reset(); });
      }
      syncPanel();
    },

    load: function (gig) {
      var on = String((gig && gig.ovr_enabled) || 0) === "1";
      _hasOwnCopy = on;
      var chk = document.getElementById("gigSpecOn");
      if (chk) chk.checked = on;
      if (on) {
        els().forEach(function (el) { setVal(el, gig["ovr_" + el.dataset.k]); });
      } else {
        prefill();
      }
      syncPanel();
    },

    // "Re-copy my venue settings" — discard this gig's edits and start again
    // from the venue's current answers, without unticking the box.
    reset: function () {
      _hasOwnCopy = false;
      prefill();
      syncPanel();
    },

    // Always sends the flag. Unticking has to reach the server as an explicit
    // 0 so the stored copy is cleared — otherwise a gig would keep showing a
    // spec the venue had already turned off.
    payload: function () {
      var out = { ovr_enabled: enabled() ? 1 : 0 };
      if (!out.ovr_enabled) return out;
      els().forEach(function (el) { out["ovr_" + el.dataset.k] = getVal(el); });
      return out;
    },

    fields: function () { return FIELDS.map(function (f) { return f[0]; }); }
  };

  var _hasOwnCopy = false;
})();
