/**
 * Per-gig room spec overrides.
 * ============================
 * 2026-10-07. Every gig used to inherit the venue's standing spec through a
 * live JOIN. A venue whose default is "no PA" had no way to say it was
 * bringing one for a particular night — and because artists filter gig search
 * on these fields, that gig was quietly filtered OUT of results for everyone
 * who requires sound equipment. The venue lost applicants on the gig it cared
 * most about, with nothing on screen to explain it.
 *
 * Each control is tri-state, not a plain input:
 *   "Use my venue default"  → sends null, the gig inherits
 *   Yes / No, or typed text → sends the value, this gig only
 *
 * That third state is the whole point. A plain checkbox cannot distinguish
 * "no PA for this gig" from "I haven't said", and a plain text box cannot
 * distinguish "no bar tab tonight" from "unchanged" — which are exactly the
 * overrides a venue is most likely to want.
 *
 * window.gfGigSpec.mount({ venue })  — venue row, for the default labels
 * window.gfGigSpec.load(gig)         — fill from a gig being edited
 * window.gfGigSpec.payload()         — { ovr_*: value|null } for the save
 * window.gfGigSpec.reset()           — back to inheriting everything
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
          // A select, not a checkbox: a checkbox has two states and this
          // needs three. "Use my venue default" has to be distinguishable
          // from an explicit No.
          ctrl = '<select id="' + id + '" data-k="' + key + '" data-t="bool">' +
                   '<option value="">Use my venue default (' + esc(defaultLabel(key, kind)) + ")</option>" +
                   '<option value="1">Yes &mdash; for this gig</option>' +
                   '<option value="0">No &mdash; for this gig</option>' +
                 "</select>";
        } else {
          ctrl = '<input type="' + (kind === "num" ? "number" : "text") + '" id="' + id +
                 '" data-k="' + key + '" data-t="' + kind + '" placeholder="' +
                 esc(defaultLabel(key, kind)) + '"' + (kind === "num" ? ' step="0.5" min="0"' : "") + ">";
        }
        return '<div class="gf-spec-row">' +
                 '<label for="' + id + '">' + esc(label) + "</label>" +
                 '<div class="gf-spec-ctrl">' + ctrl + "</div>" +
               "</div>";
      }).join("");
      return '<div class="gf-spec-group"><h4>' + esc(grp[0]) + "</h4>" + rows + "</div>";
    }).join("");
    injectStyles();
    host.addEventListener("input", updateCount);
    host.addEventListener("change", updateCount);
    updateCount();
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

  function updateCount() {
    var n = 0;
    els().forEach(function (el) {
      var set = String(el.value).trim() !== "";
      el.classList.toggle("on", set);
      if (set) n++;
    });
    var badge = document.getElementById("gigSpecCount");
    if (badge) badge.textContent = n ? "— " + n + " changed" : "";
    // Open the panel on load when a gig already overrides something, or the
    // venue would have to guess that there is anything behind the button.
    if (n && !_userToggled) show(true);
  }

  var _userToggled = false;
  function show(on) {
    var p = document.getElementById("gigSpecPanel");
    if (p) p.style.display = on ? "block" : "none";
  }

  window.gfGigSpec = {
    mount: function (opts) {
      venueRow = (opts && opts.venue) || {};
      render();
      var t = document.getElementById("gigSpecToggle");
      if (t && !t._wired) {
        t._wired = true;
        t.addEventListener("click", function () {
          _userToggled = true;
          var p = document.getElementById("gigSpecPanel");
          show(!p || p.style.display === "none");
        });
      }
      var r = document.getElementById("gigSpecReset");
      if (r && !r._wired) {
        r._wired = true;
        r.addEventListener("click", function () { window.gfGigSpec.reset(); });
      }
    },

    load: function (gig) {
      _userToggled = false;
      els().forEach(function (el) {
        var v = gig ? gig["ovr_" + el.dataset.k] : null;
        el.value = (v === null || v === undefined) ? "" : String(v);
      });
      updateCount();
    },

    reset: function () {
      els().forEach(function (el) { el.value = ""; });
      updateCount();
    },

    // Always sends every key, with null for the ones left on the default.
    // Sending only the filled ones would make clearing an override
    // impossible — the server would never hear that it should go back to
    // inheriting.
    payload: function () {
      var out = {};
      els().forEach(function (el) {
        var raw = String(el.value).trim();
        out["ovr_" + el.dataset.k] = raw === "" ? null
          : (el.dataset.t === "bool" ? Number(raw)
             : el.dataset.t === "num" ? Number(raw) : raw);
      });
      return out;
    },

    fields: function () { return FIELDS.map(function (f) { return f[0]; }); }
  };
})();
