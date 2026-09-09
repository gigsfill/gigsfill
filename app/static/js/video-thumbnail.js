/**
 * Shared video-thumbnail helper.
 * ==============================
 * Renders "nice" thumbnails for the media grid on artist-profile.html
 * and venue-profile.html:
 *
 *   • YouTube  — pulls maxresdefault.jpg (1280×720) with a graceful
 *                fallback to hqdefault.jpg (480×360) when a video
 *                doesn't have a max-res thumb (some older uploads,
 *                Shorts under a certain view count, etc.).
 *   • Vimeo    — hits Vimeo's public oEmbed endpoint client-side to
 *                fetch the real thumbnail URL, cached in-memory so
 *                subsequent renders of the same video are instant.
 *   • Instagram / TikTok / Facebook — no easy public thumbnail path,
 *                so we render a nicely-branded SVG fallback with the
 *                platform's colors + name, plus a play-button overlay.
 *                Card still reads as "this is a video, this is where
 *                it lives" without a placeholder icon.
 *   • Anything else — generic dark gradient fallback.
 *
 * Play-button overlay: a semi-transparent black circle with a white
 * triangle centered on the thumbnail, so every video card reads as
 * "click to play" at a glance. CSS is self-injected once on load.
 *
 * Public API:
 *   window.videoThumb(url)         → synchronous best-effort URL
 *   window.attachVideoThumb(imgEl, url)
 *     Preferred entry point: sets img.src to the best synchronous
 *     value, wires the YouTube maxres → hqdefault fallback, kicks off
 *     the Vimeo async lookup, AND adds the play-button overlay to
 *     imgEl.parentElement. One call per <img>.
 */
(function () {
  'use strict';

  // ── URL parsers ─────────────────────────────────────────────────
  function _ytId(url) {
    // Matches ?v=<id>, /shorts/<id>, /embed/<id>, and youtu.be/<id>.
    // Video ids are exactly 11 chars of [A-Za-z0-9_-].
    const m = String(url || '').match(
      /(?:youtube\.com\/(?:watch\?[^ ]*v=|shorts\/|embed\/|v\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/
    );
    return m ? m[1] : null;
  }
  function _vimeoId(url) {
    const m = String(url || '').match(/vimeo\.com\/(?:video\/|channels\/[^/]+\/|groups\/[^/]+\/videos\/)?(\d+)/);
    return m ? m[1] : null;
  }
  function _isInstagram(url) { return /instagram\.com/i.test(String(url || '')); }
  function _isTikTok(url)    { return /tiktok\.com/i.test(String(url || '')); }
  function _isFacebook(url)  { return /(?:facebook\.com|fb\.watch)/i.test(String(url || '')); }

  // ── Vimeo async lookup with in-memory cache ────────────────────
  const _vimeoCache = new Map();  // vimeoId → real thumbnail URL
  const _vimeoInflight = new Map();  // vimeoId → Promise<url|null>
  async function _fetchVimeoThumb(id, videoUrl) {
    if (_vimeoCache.has(id)) return _vimeoCache.get(id);
    if (_vimeoInflight.has(id)) return _vimeoInflight.get(id);
    const p = (async () => {
      try {
        const r = await fetch(
          `https://vimeo.com/api/oembed.json?url=${encodeURIComponent(videoUrl)}`,
          { credentials: 'omit' }
        );
        if (!r.ok) return null;
        const j = await r.json();
        // Vimeo's oEmbed gives a plain thumbnail and (sometimes) one
        // with a "Play" chevron baked in. Prefer the plain one — we
        // overlay our own play button, so a second chevron would look
        // like a bug.
        const thumb = j.thumbnail_url;
        if (thumb) _vimeoCache.set(id, thumb);
        return thumb;
      } catch (_) {
        return null;
      } finally {
        _vimeoInflight.delete(id);
      }
    })();
    _vimeoInflight.set(id, p);
    return p;
  }

  // ── Branded fallback SVG ───────────────────────────────────────
  // Per-platform gradient + big label. Rendered inline as a data:
  // URI so no round-trip to fetch the asset. 480×270 = 16:9.
  const _BRANDS = {
    instagram: {
      stops: ['#F58529', '#DD2A7B', '#8134AF', '#515BD4'],
      label: 'INSTAGRAM',
    },
    tiktok: {
      // TikTok's teal + magenta on a near-black background.
      stops: ['#010101', '#25F4EE', '#FE2C55', '#010101'],
      label: 'TIKTOK',
    },
    facebook: {
      stops: ['#1877f2', '#4267B2'],
      label: 'FACEBOOK',
    },
    vimeo: {
      stops: ['#1AB7EA', '#00A5DC'],
      label: 'VIMEO',
    },
    other: {
      stops: ['#334155', '#0f172a'],
      label: 'VIDEO',
    },
  };
  function _brandedFallback(platform) {
    const b = _BRANDS[platform] || _BRANDS.other;
    const stops = b.stops.map((c, i) =>
      `<stop offset="${((i / (b.stops.length - 1)) * 100).toFixed(0)}%" stop-color="${c}"/>`
    ).join('');
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 270" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">${stops}</linearGradient></defs><rect width="480" height="270" fill="url(#g)"/><text x="240" y="152" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" font-size="38" font-weight="800" fill="white" opacity="0.9" letter-spacing="4">${b.label}</text></svg>`;
    // Use encodeURIComponent-based data URI (safer for unicode than btoa
    // in some browsers) then base64 for consistent sizing.
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
  }
  function _platform(url) {
    if (_ytId(url)) return 'youtube';
    if (_vimeoId(url)) return 'vimeo';
    if (_isInstagram(url)) return 'instagram';
    if (_isTikTok(url)) return 'tiktok';
    if (_isFacebook(url)) return 'facebook';
    return 'other';
  }

  // ── Public: synchronous best-effort ─────────────────────────────
  window.videoThumb = function (url) {
    const yt = _ytId(url);
    if (yt) return `https://img.youtube.com/vi/${yt}/maxresdefault.jpg`;
    // For Vimeo we don't have a real synchronous thumb — return the
    // Vimeo-branded placeholder; `attachVideoThumb` handles the
    // async swap. Non-video-platform URLs get their brand fallback.
    return _brandedFallback(_platform(url));
  };

  // ── Public: smart attach ────────────────────────────────────────
  // Sets img.src, wires YouTube max→hq fallback + Vimeo async swap,
  // and adds the play overlay to the parent element.
  window.attachVideoThumb = function (imgEl, url) {
    if (!imgEl) return;
    const yt = _ytId(url);
    const vimeo = _vimeoId(url);
    if (yt) {
      imgEl.src = `https://img.youtube.com/vi/${yt}/maxresdefault.jpg`;
      // Older / less-viewed YouTube videos don't have a maxres image
      // and 404. Swap to hqdefault (which every video has) on error.
      // Also detect the well-known "no thumb" placeholder — YouTube
      // sometimes serves a 120×90 grey image instead of a 404, which
      // otherwise renders as a tiny centered dot.
      let triedFallback = false;
      const swap = () => {
        if (triedFallback) return;
        triedFallback = true;
        imgEl.src = `https://img.youtube.com/vi/${yt}/hqdefault.jpg`;
      };
      imgEl.addEventListener('error', swap);
      imgEl.addEventListener('load', function onLoad() {
        // maxresdefault of a placeholder video is 120px. Real
        // thumbnails come back at 1280x720. Anything under 400px
        // wide is the "no maxres" fallback grey image.
        if (imgEl.naturalWidth && imgEl.naturalWidth < 400) swap();
      });
    } else if (vimeo) {
      imgEl.src = _brandedFallback('vimeo');
      _fetchVimeoThumb(vimeo, url).then(t => { if (t) imgEl.src = t; });
    } else {
      imgEl.src = window.videoThumb(url);
    }
    _addPlayOverlay(imgEl);
  };

  function _addPlayOverlay(imgEl) {
    const parent = imgEl && imgEl.parentElement;
    if (!parent) return;
    if (parent.querySelector('.gf-video-play-overlay')) return;
    // The play overlay is absolutely positioned inside the card. The
    // card needs a positioning context — most .media-card styles set
    // position:relative already, but we ensure it defensively.
    const cs = parent.style.position || getComputedStyle(parent).position;
    if (cs === 'static' || !cs) parent.style.position = 'relative';
    const overlay = document.createElement('div');
    overlay.className = 'gf-video-play-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" width="26" height="26"><polygon points="8,5 20,12 8,19"/></svg>';
    parent.appendChild(overlay);
  }

  // ── Self-injected stylesheet ────────────────────────────────────
  if (!document.getElementById('gf-video-thumb-css')) {
    const s = document.createElement('style');
    s.id = 'gf-video-thumb-css';
    s.textContent = [
      '.gf-video-play-overlay{',
      '  position:absolute;top:50%;left:50%;',
      '  transform:translate(-50%,-50%);',
      '  width:52px;height:52px;border-radius:50%;',
      '  background:rgba(0,0,0,0.55);color:#fff;',
      '  display:flex;align-items:center;justify-content:center;',
      '  pointer-events:none;',
      '  transition:transform 0.18s ease, background 0.18s ease;',
      '  box-shadow:0 6px 14px rgba(0,0,0,0.35);',
      '}',
      '.gf-video-play-overlay svg{margin-left:3px;} ',  /* nudge triangle */
      '.media-card:hover .gf-video-play-overlay{',
      '  transform:translate(-50%,-50%) scale(1.08);',
      '  background:rgba(0,0,0,0.75);',
      '}',
    ].join('');
    document.head.appendChild(s);
  }
})();
