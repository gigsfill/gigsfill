/**
 * "These gigs won't pick up your new settings"
 * ============================================
 * 2026-10-08. A gig whose venue ticked "this gig differs from my usual setup"
 * keeps its own complete copy of the room spec, and later edits to the venue
 * profile deliberately do not reach it — what the gig advertised stays what it
 * advertised.
 *
 * That is the right behaviour and a silent trap. A venue that buys a PA and
 * updates its profile has no way to know that next month's residency still
 * says "bring your own", until an artist turns up expecting the old setup.
 * So after a venue saves its room settings, this tells them which upcoming
 * gigs are pinned and offers to re-base them.
 *
 * It hooks fetch rather than each save handler. venue.edit.js already PUTs to
 * /api/venues/{id} from six places and more get added; hooking one of them and
 * missing the rest would make the warning appear only sometimes, which is
 * worse than not having it.
 */
(function () {
  "use strict";

  var SPEC_KEYS = [
    "has_stage", "stage_width_ft", "stage_depth_ft", "setup_location_description",
    "has_sound_equipment", "sound_equipment_description",
    "has_sound_engineer", "sound_engineer_details",
    "has_lighting", "lighting_description", "load_in_out_details",
    "arrival_time_type", "arrival_no_earlier_than_hour",
    "arrival_no_earlier_than_period", "bar_tab_details", "food_tab_details"
  ];

  // Human labels for the "what changed" list, so a venue reads "Sound
  // equipment" rather than a column name.
  var LABELS = {
    has_stage: "Stage", stage_width_ft: "Stage width", stage_depth_ft: "Stage depth",
    setup_location_description: "Setup location",
    has_sound_equipment: "Sound equipment", sound_equipment_description: "Sound details",
    has_sound_engineer: "Sound engineer", sound_engineer_details: "Engineer details",
    has_lighting: "Lighting", lighting_description: "Lighting details",
    load_in_out_details: "Load in / out", arrival_time_type: "Arrival",
    arrival_no_earlier_than_hour: "Arrival hour",
    arrival_no_earlier_than_period: "Arrival AM/PM",
    bar_tab_details: "Bar tab", food_tab_details: "Food"
  };

  var venueId = null;
  var checking = false;
  var dismissedThisVisit = false;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function prettyDate(iso) {
    try {
      return new Date(iso + "T12:00:00").toLocaleDateString(undefined, {
        weekday: "short", month: "short", day: "numeric", year: "numeric"
      });
    } catch (e) { return iso; }
  }

  // Only react to a save that actually touched the room spec. A venue editing
  // its description or socials should not be asked about gig setups.
  function touchesSpec(body) {
    if (!body) return false;
    var obj;
    try { obj = typeof body === "string" ? JSON.parse(body) : body; }
    catch (e) { return false; }
    if (!obj || typeof obj !== "object") return false;
    return SPEC_KEYS.some(function (k) { return k in obj; });
  }

  async function check() {
    if (checking || dismissedThisVisit || !venueId) return;
    checking = true;
    try {
      var r = await fetch("/api/venues/" + venueId + "/gigs-with-own-setup",
                          { credentials: "include" });
      if (!r.ok) return;
      var d = await r.json();
      var stale = (d && d.stale) || [];
      if (stale.length) show(stale);
    } catch (e) {
      /* a failed check must never block the save the venue just made */
    } finally {
      checking = false;
    }
  }

  function show(gigs) {
    var host = document.getElementById("gfDriftModal");
    if (!host) {
      host = document.createElement("div");
      host.id = "gfDriftModal";
      host.style.cssText = "position:fixed;inset:0;z-index:9200;display:flex;" +
        "align-items:center;justify-content:center;padding:20px;" +
        "background:rgba(3,5,10,0.88);";
      document.body.appendChild(host);
    }
    var rows = gigs.map(function (g) {
      var what = (g.differs || []).map(function (f) { return LABELS[f] || f; });
      var shown = what.slice(0, 4).join(", ") + (what.length > 4 ? " +" + (what.length - 4) : "");
      return '<label style="display:flex;gap:10px;align-items:flex-start;padding:9px 0;' +
             'border-bottom:1px solid rgba(148,163,184,0.14);">' +
               '<input type="checkbox" class="gf-drift-pick" value="' + g.gig_id +
               '" checked style="margin-top:3px;accent-color:var(--cyan);">' +
               "<span>" +
                 '<span style="display:block;font-size:0.84rem;color:var(--text);font-weight:600;">' +
                   esc(prettyDate(g.date)) + (g.title ? " — " + esc(g.title) : "") + "</span>" +
                 '<span style="display:block;font-size:0.75rem;color:var(--text-gray);margin-top:2px;">' +
                   "Differs on: " + esc(shown) + "</span>" +
               "</span>" +
             "</label>";
    }).join("");

    host.innerHTML =
      '<div style="background:#151b28;border:1px solid var(--border);border-radius:12px;' +
      'width:min(560px,100%);max-height:85vh;display:flex;flex-direction:column;' +
      'box-shadow:0 30px 80px rgba(0,0,0,0.6);">' +
        '<div style="padding:14px 18px;border-bottom:1px solid rgba(148,163,184,0.32);">' +
          '<h3 style="margin:0;font-size:0.95rem;color:var(--text);">' +
            gigs.length + " upcoming gig" + (gigs.length === 1 ? "" : "s") +
            " won’t use your new settings</h3></div>" +
        '<div style="padding:14px 18px;overflow-y:auto;">' +
          '<p style="margin:0 0 12px;font-size:0.82rem;color:var(--text-gray);line-height:1.55;">' +
            "These gigs have their own setup, so they kept what they were advertising " +
            "when you set them up. That is usually what you want &mdash; artists have " +
            "already seen those details.<br><br>" +
            "Tick any you’d like to re-copy your current venue settings onto. " +
            '<strong style="color:var(--text);">This replaces that gig’s whole setup</strong>, ' +
            "including anything you changed on it." +
          "</p>" + rows +
        "</div>" +
        '<div style="padding:12px 18px;border-top:1px solid rgba(148,163,184,0.32);' +
        'display:flex;gap:10px;justify-content:flex-end;">' +
          '<button type="button" id="gfDriftSkip" style="background:rgba(255,255,255,0.06);' +
          'border:1px solid var(--border);color:var(--text);border-radius:6px;' +
          'padding:8px 14px;font-size:0.8rem;cursor:pointer;">Leave them as they are</button>' +
          '<button type="button" id="gfDriftApply" style="background:var(--cyan);border:none;' +
          'color:#06121a;border-radius:6px;padding:8px 16px;font-size:0.8rem;font-weight:700;' +
          'cursor:pointer;">Update ticked gigs</button>' +
        "</div>" +
      "</div>";

    function close() { dismissedThisVisit = true; host.remove(); }
    host.querySelector("#gfDriftSkip").addEventListener("click", close);
    host.querySelector("#gfDriftApply").addEventListener("click", async function () {
      var ids = Array.prototype.slice
        .call(host.querySelectorAll(".gf-drift-pick:checked"))
        .map(function (c) { return Number(c.value); });
      if (!ids.length) { close(); return; }
      var btn = this;
      btn.disabled = true;
      btn.textContent = "Updating…";
      try {
        await fetch("/api/venues/" + venueId + "/gigs-with-own-setup/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ gig_ids: ids })
        });
      } catch (e) { /* nothing to recover; the venue can re-save */ }
      close();
    });
  }

  function install() {
    venueId = new URLSearchParams(window.location.search).get("venue_id");
    if (!venueId) return;
    var native = window.fetch;
    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
      var p = native.apply(this, arguments);
      if (method === "PUT" && /\/api\/venues\/\d+(\?|$)/.test(url) &&
          touchesSpec(init && init.body)) {
        p.then(function (res) {
          // Only after the save actually landed — warning about gigs that
          // will not follow a change that did not happen is noise.
          if (res && res.ok) setTimeout(check, 400);
        }).catch(function () {});
      }
      return p;
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
})();
