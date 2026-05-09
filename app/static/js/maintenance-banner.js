/**
 * GigsFill Maintenance Banner
 * ============================
 * Checks /api/maintenance-status on every page load.
 * If maintenance mode is ON, shows a full-screen overlay blocking the page.
 * Admin pages (/app/admin.html) are never blocked.
 * Re-checks every 60 seconds — overlay auto-dismisses when maintenance ends.
 */
(function () {
  'use strict';

  // Never block the admin panel
  if (window.location.pathname.includes('/admin')) return;

  var _checkInterval = null;
  var _overlayEl = null;

  function _createOverlay(message) {
    if (_overlayEl) return; // already showing

    var el = document.createElement('div');
    el.id = 'gf-maintenance-overlay';
    el.setAttribute('role', 'alert');
    el.setAttribute('aria-live', 'assertive');
    el.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:2147483647',
      'display:flex', 'flex-direction:column',
      'align-items:center', 'justify-content:center',
      'padding:24px',
      'background:linear-gradient(135deg,#0a0e1a 0%,#0f172a 100%)',
      'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    ].join(';');

    el.innerHTML = [
      '<div style="max-width:480px;width:100%;text-align:center;">',
        // Logo
        '<img src="/app/static/img/gigsfill-logo.png" alt="GigsFill"',
        '  style="height:52px;width:auto;margin-bottom:32px;opacity:0.9;">',
        // Icon
        '<div style="',
          'width:72px;height:72px;margin:0 auto 24px;',
          'background:rgba(251,191,36,0.15);',
          'border:2px solid rgba(251,191,36,0.4);',
          'border-radius:50%;display:flex;align-items:center;justify-content:center;',
          'font-size:32px;">',
          '🔧',
        '</div>',
        // Heading
        '<h1 style="',
          'color:#f1f5f9;font-size:1.6rem;font-weight:800;',
          'margin:0 0 12px;letter-spacing:-0.02em;">',
          'Under Maintenance',
        '</h1>',
        // Message
        '<p id="gf-maintenance-msg" style="',
          'color:#94a3b8;font-size:1rem;line-height:1.7;',
          'margin:0 0 32px;max-width:380px;margin-left:auto;margin-right:auto;">',
          _escHtml(message),
        '</p>',
        // Animated dots
        '<div style="display:flex;gap:8px;justify-content:center;margin-bottom:32px;">',
          '<span class="gf-dot" style="',
            'width:10px;height:10px;background:#f59e0b;border-radius:50%;',
            'animation:gfPulse 1.4s ease-in-out infinite;"></span>',
          '<span class="gf-dot" style="',
            'width:10px;height:10px;background:#f59e0b;border-radius:50%;',
            'animation:gfPulse 1.4s ease-in-out 0.2s infinite;"></span>',
          '<span class="gf-dot" style="',
            'width:10px;height:10px;background:#f59e0b;border-radius:50%;',
            'animation:gfPulse 1.4s ease-in-out 0.4s infinite;"></span>',
        '</div>',
        // Footer note
        '<p style="color:#475569;font-size:0.78rem;margin:0;">',
          'This page will automatically refresh when we\'re back online.',
        '</p>',
      '</div>',
      // Keyframe style
      '<style>',
        '@keyframes gfPulse{',
          '0%,100%{opacity:0.3;transform:scale(0.85)}',
          '50%{opacity:1;transform:scale(1.15)}',
        '}',
      '</style>'
    ].join('');

    document.body.appendChild(el);
    _overlayEl = el;
  }

  function _removeOverlay() {
    if (_overlayEl) {
      _overlayEl.remove();
      _overlayEl = null;
    }
  }

  function _escHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _check() {
    fetch('/api/maintenance-status', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : { maintenance: false }; })
      .then(function (data) {
        if (data.maintenance) {
          _createOverlay(data.message || 'GigsFill is currently undergoing maintenance. We\'ll be back shortly!');
        } else {
          if (_overlayEl) {
            // Was in maintenance, now back — reload so page initialises properly
            window.location.reload();
          }
          _removeOverlay();
        }
      })
      .catch(function () {
        // Network error — don't show banner (could be user's connection)
      });
  }

  // Run on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _check);
  } else {
    _check();
  }

  // Re-check every 60 seconds so the banner auto-dismisses when maintenance ends
  _checkInterval = setInterval(_check, 60000);

})();
