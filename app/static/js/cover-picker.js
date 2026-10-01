/**
 * Public profile background image picker.
 * =======================================
 * 2026-10-01. One module for both artist-edit and venue-edit.
 *
 * Replaces a pair of "Use as Cover" buttons scattered across the media
 * cards and the logo. That wording never said what it did — cover of what?
 * — and the implementation had drifted into two separate IIFEs on the
 * artist page, one holding `markHero` and the other holding the helpers it
 * called, which threw "el is not defined" the moment the logo button was
 * wired up.
 *
 * Now there is one section, one button, and a dialog that shows every
 * candidate image as a tile (logo first, then uploaded photos). Picking one
 * reveals the framing controls — drag to position, zoom, band height — all
 * previewing at the exact proportions the profile will render.
 *
 * Host pages provide:
 *   window.gfCoverPicker.init({
 *     entity: "artist" | "venue",
 *     id: 12,
 *     putUrl: "/artists/12",            // update endpoint (NOT /api for artists)
 *     mediaUrl: "/api/artists/12/media"
 *   })
 */
(function () {
  "use strict";

  var cfg = null;
  var media = [];          // candidate images, logo first
  var state = { mediaId: null, x: 50, y: 50, zoom: 1, ratio: 64 / 21,
              logoOverlay: false, logoOpacity: 1, logoScale: 0.46,
              logoX: 50, logoY: 50 };
  var saveTimer = null;
  var DEFAULT_RATIO = 64 / 21;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ── persistence ─────────────────────────────────────────────────────
  async function put(body) {
    try {
      var r = await fetch(cfg.putUrl, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body)
      });
      return r.ok;
    } catch (e) { return false; }
  }

  function setStatus(kind) {
    // Two places: the dialog footer while it's open, and the section
    // summary behind it for when it isn't.
    var el = $("gfCoverStatus");
    if (el) {
      el.className = "gf-save" + (kind ? " " + kind : "");
      el.textContent = kind === "saving" ? "Saving\u2026"
                     : kind === "saved"  ? "\u2713 Saved"
                     : kind === "error"  ? "\u2717 Could not save \u2014 check your connection"
                     : "Changes save automatically";
      if (kind === "saved") {
        clearTimeout(el._t);
        el._t = setTimeout(function () { setStatus(null); }, 2200);
      }
    }
    var m = $("gfCoverSaved");
    if (m && kind === "saved") {
      m.style.display = "inline";
      clearTimeout(m._t);
      m._t = setTimeout(function () { m.style.display = "none"; }, 1800);
    }
  }
  function flashSaved() { setStatus("saved"); }

  // Debounced: drag and slider input fire continuously.
  function saveFraming() {
    clearTimeout(saveTimer);
    setStatus("saving");
    saveTimer = setTimeout(async function () {
      var ok = await put({
        hero_focal_x: Math.round(state.x * 10) / 10,
        hero_focal_y: Math.round(state.y * 10) / 10,
        hero_zoom: Math.round(state.zoom * 100) / 100,
        hero_ratio: Math.round(state.ratio * 1000) / 1000,
        hero_logo_overlay: state.logoOverlay ? 1 : 0,
        hero_logo_opacity: Math.round(state.logoOpacity * 100) / 100,
        hero_logo_scale: Math.round(state.logoScale * 100) / 100,
        hero_logo_x: Math.round(state.logoX * 10) / 10,
        hero_logo_y: Math.round(state.logoY * 10) / 10
      });
      setStatus(ok ? "saved" : "error");
    }, 450);
  }

  // ── rendering ───────────────────────────────────────────────────────
  function logoImage() {
    return media.filter(function (m) { return m.media_type === "profile" && m.file_path; })[0] || null;
  }

  // The overlay only makes sense when a logo exists AND it isn't already
  // the background — laying the logo over itself is pointless.
  function overlayAvailable() {
    var logo = logoImage();
    return !!(logo && state.mediaId && Number(logo.id) !== Number(state.mediaId));
  }

  function currentImage() {
    if (!state.mediaId) return null;
    return media.filter(function (m) { return Number(m.id) === Number(state.mediaId); })[0] || null;
  }

  // The section summary: what's set, or that nothing is.
  function renderSummary() {
    var box = $("gfCoverSummary");
    if (!box) return;
    var img = currentImage();
    if (!img) {
      box.innerHTML =
        '<div class="gf-cover-none">No background image set — your profile header ' +
        'shows just your name and details, with no image band.</div>';
      return;
    }
    box.innerHTML =
      '<div class="gf-cover-current">' +
        '<div class="gf-cover-thumb" style="background-image:url(\'' + esc(img.file_path) + '\');' +
          "background-position:" + state.x + "% " + state.y + "%;\"></div>" +
        // Just which image is set. The zoom and band-height readout here
        // restated settings the dialog already shows, in a place where
        // nothing can be done about them.
        '<div class="gf-cover-meta">' +
          "<strong>" + esc(img.media_type === "profile" ? "Logo" : (img.title || "Photo")) + "</strong>" +
        "</div>" +
        '<button type="button" class="gf-cover-clear" id="gfCoverClear">Remove</button>' +
      "</div>";
  }

  function renderTiles() {
    var wrap = $("gfCoverTiles");
    if (!wrap) return;
    if (!media.length) {
      wrap.innerHTML = '<p class="gf-cover-none">Upload a logo or a photo first — ' +
        'they\'ll appear here to choose from.</p>';
      return;
    }
    wrap.innerHTML = media.map(function (m) {
      var on = Number(m.id) === Number(state.mediaId);
      var label = m.media_type === "profile" ? "Logo" : (m.title || "Photo");
      return '<button type="button" class="gf-tile' + (on ? " on" : "") + '" data-media="' + m.id + '">' +
        '<span class="gf-tile-img" style="background-image:url(\'' + esc(m.file_path) + '\')"></span>' +
        '<span class="gf-tile-label">' + esc(label) + (on ? " ✓" : "") + "</span>" +
      "</button>";
    }).join("");
  }

  // Preview must mirror the profile exactly: outer box clips, inner layer
  // carries the image plus the transform, both driven by the same numbers.
  function applyPreview() {
    var outer = $("gfCoverPreview"), inner = $("gfCoverPreviewImg");
    if (!outer || !inner) return;
    var img = currentImage();
    var pane = $("gfCoverFraming");
    if (!img) { if (pane) pane.style.display = "none"; return; }
    if (pane) pane.style.display = "block";

    outer.style.aspectRatio = String(state.ratio);
    inner.style.backgroundImage = 'url("' + img.file_path + '")';
    inner.style.backgroundPosition = state.x + "% " + state.y + "%";
    inner.style.transformOrigin = state.x + "% " + state.y + "%";
    inner.style.transform = (state.zoom > 1) ? "scale(" + state.zoom + ")" : "none";

    // Overlay preview, mirroring the profile exactly.
    var row = $("gfCoverLogoRow"), ovImg = $("gfCoverPreviewLogo"), chk = $("gfCoverLogoOn");
    var avail = overlayAvailable();
    // Three sibling rows in the shared grid, so each is shown individually
    // — a wrapper would have broken the column alignment with Zoom/Height.
    if (row) row.style.display = avail ? "grid" : "none";
    var sizeRow = $("gfCoverLogoSizeRow"), fadeRow = $("gfCoverLogoFadeRow");
    var showSub = avail && state.logoOverlay;
    if (sizeRow) sizeRow.style.display = showSub ? "grid" : "none";
    if (fadeRow) fadeRow.style.display = showSub ? "grid" : "none";
    if (chk) chk.checked = !!state.logoOverlay;
    if (ovImg) {
      if (avail && state.logoOverlay) {
        ovImg.src = logoImage().file_path;
        ovImg.style.opacity = state.logoOpacity;
        ovImg.style.maxWidth = Math.round(state.logoScale * 100) + "%";
        // Height tracks width so a tall badge can't overflow the band.
        ovImg.style.maxHeight = Math.round(state.logoScale * 135) + "%";
        ovImg.style.left = state.logoX + "%";
        ovImg.style.top = state.logoY + "%";
        ovImg.style.display = "block";
      } else {
        ovImg.style.display = "none";
      }
    }
    var op = $("gfCoverLogoOpacity"), opv = $("gfCoverLogoOpacityVal");
    if (op) op.value = Math.round(state.logoOpacity * 100);
    if (opv) opv.textContent = Math.round(state.logoOpacity * 100) + "%";
    var ls = $("gfCoverLogoSize"), lsv = $("gfCoverLogoSizeVal");
    if (ls) ls.value = Math.round(state.logoScale * 100);
    if (lsv) lsv.textContent = Math.round(state.logoScale * 100) + "%";


    var z = $("gfCoverZoom"), zv = $("gfCoverZoomVal"), h = $("gfCoverHeight");
    if (z) z.value = Math.round(state.zoom * 100);
    if (zv) zv.textContent = Math.round(state.zoom * 100) + "%";
    if (h) h.value = Math.round(state.ratio * 100);
  }

  function refresh() { renderTiles(); applyPreview(); renderSummary(); }

  // ── dialog ──────────────────────────────────────────────────────────
  function open() { $("gfCoverModal").classList.add("open"); refresh(); }
  function close() { $("gfCoverModal").classList.remove("open"); }

  async function selectMedia(id) {
    setStatus("saving");
    state.mediaId = Number(id);
    // A fresh pick starts from a clean frame; inheriting the previous
    // image's crop lands the new one somewhere arbitrary.
    state.x = 50; state.y = 50; state.zoom = 1;
    var ok = await put({
      hero_media_id: state.mediaId,
      hero_focal_x: 50, hero_focal_y: 50, hero_zoom: 1,
      hero_ratio: Math.round(state.ratio * 1000) / 1000
    });
    setStatus(ok ? "saved" : "error");
    refresh();
  }

  // Remove clears the whole background setup, not just the image. It
  // previously left the overlay toggle, logo size, fade, zoom and band
  // height behind, so choosing a new picture silently inherited the last
  // one's settings — the logo would already be switched on at the old size.
  async function clearMedia() {
    setStatus("saving");
    state.mediaId = null;
    state.x = 50; state.y = 50; state.zoom = 1; state.ratio = DEFAULT_RATIO;
    state.logoOverlay = false; state.logoOpacity = 1; state.logoScale = 0.46;
    state.logoX = 50; state.logoY = 50;
    var ok = await put({
      hero_media_id: 0,
      hero_focal_x: 50, hero_focal_y: 50, hero_zoom: 1,
      hero_ratio: Math.round(DEFAULT_RATIO * 1000) / 1000,
      hero_logo_overlay: 0, hero_logo_opacity: 1, hero_logo_scale: 0.46,
      hero_logo_x: 50, hero_logo_y: 50
    });
    setStatus(ok ? "saved" : "error");
    refresh();
  }

  // ── drag ────────────────────────────────────────────────────────────
  var dragging = false, dragWhat = null, sx = 0, sy = 0, sfx = 50, sfy = 50;

  function onDown(e) {
    var box = $("gfCoverPreview");
    if (!box || !currentImage()) return;
    if (!(e.target === box || box.contains(e.target))) return;

    // Grab the logo and the logo moves; grab anywhere else and the
    // background pans. No mode to remember — you drag the thing you want.
    var logoEl = $("gfCoverPreviewLogo");
    dragWhat = (logoEl && logoEl.style.display !== "none" && e.target === logoEl)
      ? "logo" : "bg";

    dragging = true;
    box.classList.add("dragging");
    var pt = e.touches ? e.touches[0] : e;
    sx = pt.clientX; sy = pt.clientY;
    sfx = (dragWhat === "logo") ? state.logoX : state.x;
    sfy = (dragWhat === "logo") ? state.logoY : state.y;
    e.preventDefault();
  }
  function onMove(e) {
    if (!dragging) return;
    var box = $("gfCoverPreview"), rect = box.getBoundingClientRect();
    var pt = e.touches ? e.touches[0] : e;
    var dx = ((pt.clientX - sx) / rect.width) * 100;
    var dy = ((pt.clientY - sy) / rect.height) * 100;

    if (dragWhat === "logo") {
      // Direct: you're moving the object itself, so it follows the cursor.
      state.logoX = Math.max(0, Math.min(100, sfx + dx));
      state.logoY = Math.max(0, Math.min(100, sfy + dy));
    } else {
      // Inverse: panning behind a window, so dragging left reveals what is
      // to the image's right — like a photo under glass.
      state.x = Math.max(0, Math.min(100, sfx - dx));
      state.y = Math.max(0, Math.min(100, sfy - dy));
    }
    applyPreview();
    e.preventDefault();
  }
  function onUp() {
    if (!dragging) return;
    dragging = false;
    var box = $("gfCoverPreview");
    if (box) box.classList.remove("dragging");
    saveFraming();
    renderSummary();
  }

  function setZoom(z) {
    state.zoom = Math.max(1, Math.min(4, z));
    applyPreview();
    saveFraming();
    renderSummary();
  }

  // ── wiring ──────────────────────────────────────────────────────────
  function bind() {
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t) return;

      if (t.closest && t.closest("#gfCoverOpen")) { e.preventDefault(); open(); return; }
      if (t.closest && (t.closest("#gfCoverClose") || t.closest("#gfCoverClose2"))) {
        e.preventDefault(); close(); return;
      }
      if (t.id === "gfCoverModal") { close(); return; }      // backdrop
      if (t.closest && t.closest("#gfCoverClear")) { e.preventDefault(); clearMedia(); return; }

      var tile = t.closest && t.closest(".gf-tile");
      if (tile) { e.preventDefault(); selectMedia(tile.dataset.media); return; }

      if (t.id === "gfCoverLogoOn") {
        state.logoOverlay = !!t.checked;
        applyPreview(); saveFraming(); renderSummary();
        return;
      }
      if (t.closest && t.closest("#gfCoverZoomIn"))  { setZoom(state.zoom + 0.1); return; }
      if (t.closest && t.closest("#gfCoverZoomOut")) { setZoom(state.zoom - 0.1); return; }
      if (t.closest && t.closest("#gfCoverReset")) {
        // Everything the dialog controls, including the overlay sliders —
        // "Reset framing" that left the logo at 80% and half-faded would be
        // a confusing half-measure.
        state.x = 50; state.y = 50; state.zoom = 1; state.ratio = DEFAULT_RATIO;
        state.logoOpacity = 1; state.logoScale = 0.46;
        state.logoX = 50; state.logoY = 50;
        applyPreview(); saveFraming(); renderSummary();
        return;
      }
    });

    document.addEventListener("input", function (e) {
      if (!e.target) return;
      if (e.target.id === "gfCoverZoom") setZoom(Number(e.target.value) / 100);
      if (e.target.id === "gfCoverLogoSize") {
        state.logoScale = Math.max(0.15, Math.min(1, Number(e.target.value) / 100));
        applyPreview(); saveFraming();
        return;
      }
      if (e.target.id === "gfCoverLogoOpacity") {
        state.logoOpacity = Math.max(0.1, Math.min(1, Number(e.target.value) / 100));
        applyPreview(); saveFraming();
        return;
      }
      if (e.target.id === "gfCoverHeight") {
        state.ratio = Math.max(1.6, Math.min(5, Number(e.target.value) / 100));
        applyPreview(); saveFraming(); renderSummary();
      }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });

    document.addEventListener("mousedown", onDown);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.addEventListener("touchstart", onDown, { passive: false });
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onUp);
    document.addEventListener("wheel", function (e) {
      var box = $("gfCoverPreview");
      if (!box || !currentImage()) return;
      if (!(e.target === box || box.contains(e.target))) return;
      e.preventDefault();
      // Wheel always zooms the background, including over the logo — the
      // logo has its own Size slider.
      setZoom(state.zoom + (e.deltaY < 0 ? 0.08 : -0.08));
    }, { passive: false });
  }

  // ── public API ──────────────────────────────────────────────────────
  window.gfCoverPicker = {
    init: async function (options) {
      cfg = options;
      bind();
      await this.reload();
    },

    // Called again after an upload or delete so the tiles stay current.
    reload: async function () {
      if (!cfg) return;
      try {
        var r = await fetch(cfg.mediaUrl, { credentials: "include" });
        var all = r.ok ? await r.json() : [];
        // Logo first — it's the most likely choice and shouldn't be hunted
        // for among the photos.
        var logo = all.filter(function (m) { return m.media_type === "profile" && m.file_path; });
        var pics = all.filter(function (m) { return m.media_type === "picture" && m.file_path; });
        media = logo.concat(pics);
      } catch (e) { media = []; }
      refresh();
    },

    // Host pages hand over the saved values once the entity has loaded.
    setState: function (s) {
      state.mediaId = s.hero_media_id || null;
      state.x = (s.hero_focal_x == null) ? 50 : Number(s.hero_focal_x);
      state.y = (s.hero_focal_y == null) ? 50 : Number(s.hero_focal_y);
      state.zoom = (s.hero_zoom == null) ? 1 : Math.max(1, Math.min(4, Number(s.hero_zoom)));
      state.ratio = (s.hero_ratio == null) ? DEFAULT_RATIO
                                           : Math.max(1.6, Math.min(5, Number(s.hero_ratio)));
      state.logoOverlay = !!s.hero_logo_overlay;
      state.logoOpacity = (s.hero_logo_opacity == null)
        ? 1 : Math.max(0.1, Math.min(1, Number(s.hero_logo_opacity)));
      state.logoScale = (s.hero_logo_scale == null)
        ? 0.46 : Math.max(0.15, Math.min(1, Number(s.hero_logo_scale)));
      state.logoX = (s.hero_logo_x == null) ? 50 : Math.max(0, Math.min(100, Number(s.hero_logo_x)));
      state.logoY = (s.hero_logo_y == null) ? 50 : Math.max(0, Math.min(100, Number(s.hero_logo_y)));
      refresh();
    }
  };
})();
