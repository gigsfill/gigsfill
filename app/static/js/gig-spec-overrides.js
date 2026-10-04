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
  var GROUPS = [
    ["Stage", [
      ["has_stage", "bool", "Stage"],
      ["stage_width_ft", "num", "Stage width (ft)"],
      ["stage_depth_ft", "num", "Stage depth (ft)"],
      ["setup_location_description", "text", "Where they set up"]
    ]],
    ["Sound & light", [
      ["has_sound_equipment", "bool", "Sound equipment provided"],
      ["sound_equipment_description", "text", "What's provided"],
      ["has_sound_engineer", "bool", "Sound engineer"],
      ["sound_engineer_details", "text", "Engineer details"],
      ["has_lighting", "bool", "Lighting"],
      ["lighting_description", "text", "Lighting details"]
    ]],
    ["Getting in", [
      ["load_in_out_details", "text", "Load in / out"],
      ["arrival_time_type", "text", "Arrival"],
      ["arrival_no_earlier_than_hour", "text", "No earlier than (hour)"],
      ["arrival_no_earlier_than_period", "text", "AM / PM"]
    ]],
    ["Hospitality", [
      ["bar_tab_details", "text", "Bar tab"],
      ["food_tab_details", "text", "Food"]
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

  function render() {
    var host = document.getElementById("gigSpecFields");
    if (!host) return;
    host.innerHTML = GROUPS.map(function (grp) {
      var rows = grp[1].map(function (f) {
        var key = f[0], kind = f[1], label = f[2];
        var id = "ovr_" + key;
        var ctrl;
        if (kind === "bool") {
          ctrl = '<label class="gf-spec-yn"><input type="checkbox" id="' + id +
                 '" data-k="' + key + '" data-t="bool"> Yes</label>';
        } else {
          ctrl = '<input type="' + (kind === "num" ? "number" : "text") + '" id="' + id +
                 '" data-k="' + key + '" data-t="' + kind + '"' +
                 (kind === "num" ? ' step="0.5" min="0"' : "") + ">";
        }
        return '<div class="gf-spec-row">' +
                 '<label for="' + id + '">' + esc(label) + "</label>" +
                 '<div class="gf-spec-ctrl">' + ctrl + "</div>" +
               "</div>";
      }).join("");
      return '<div class="gf-spec-group"><h4>' + esc(grp[0]) + "</h4>" + rows + "</div>";
    }).join("");
    injectStyles();
    prefill();
  }

  // Copy the venue's current answers in. Called on mount and whenever the box
  // is ticked from empty, so the venue starts from what they already have
  // rather than a blank form they would have to retype.
  function prefill() {
    els().forEach(function (el) {
      var v = venueRow[el.dataset.k];
      if (el.dataset.t === "bool") el.checked = truthy(v);
      else el.value = (v === null || v === undefined) ? "" : String(v);
    });
  }

  function injectStyles() {
    if (document.getElementById("gfSpecStyles")) return;
    var css = document.createElement("style");
    css.id = "gfSpecStyles";
    css.textContent = [
      ".gf-spec-group { margin-bottom:14px; }",
      ".gf-spec-group:last-child { margin-bottom:0; }",
      ".gf-spec-group h4 { margin:0 0 7px; font-size:0.74rem; font-weight:700;",
      "  text-transform:uppercase; letter-spacing:0.06em; color:var(--cyan); }",
      ".gf-spec-row { display:grid; grid-template-columns:168px 1fr; gap:10px;",
      "  align-items:center; margin-bottom:6px; }",
      ".gf-spec-row label { font-size:0.78rem; color:var(--text-gray); }",
      ".gf-spec-ctrl input, .gf-spec-ctrl select { width:100%; padding:5px 8px;",
      "  font-size:0.78rem; background:#151b28; border:1px solid var(--border);",
      "  border-radius:6px; color:var(--text); }",
      /* An overridden field should be obvious at a glance when scanning back
         over the panel. */
      ".gf-spec-ctrl input.on, .gf-spec-ctrl select.on { border-color:var(--cyan);",
      "  background:rgba(6,182,212,0.08); }",
      "@media (max-width:560px) { .gf-spec-row { grid-template-columns:1fr; gap:3px; } }"
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
        els().forEach(function (el) {
          var v = gig["ovr_" + el.dataset.k];
          if (el.dataset.t === "bool") el.checked = truthy(v);
          else el.value = (v === null || v === undefined) ? "" : String(v);
        });
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
      els().forEach(function (el) {
        out["ovr_" + el.dataset.k] = el.dataset.t === "bool"
          ? (el.checked ? 1 : 0)
          : String(el.value).trim();
      });
      return out;
    },

    fields: function () { return FIELDS.map(function (f) { return f[0]; }); }
  };

  var _hasOwnCopy = false;
})();
