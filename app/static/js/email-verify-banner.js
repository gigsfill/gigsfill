/**
 * Email Verification Banner
 * Shows a dismissible top banner if the logged-in user has not verified their email.
 * Only included on user-profile.html (the one page exempt from the hard auth gate).
 * Auto-dismisses when the user returns to the tab after verifying.
 */
(function() {
  'use strict';

  const STORAGE_KEY = 'gf_verify_dismissed';
  let _pollTimer = null;

  async function checkVerification() {
    // Don't re-show if user already dismissed this session
    const dismissed = sessionStorage.getItem(STORAGE_KEY);
    if (dismissed) return;

    try {
      const res = await fetch('/api/me', { credentials: 'include' });
      if (!res.ok) return;
      const me = await res.json();
      if (!me || !me.id) return;

      if (me.email_verified) {
        // Already verified — remove banner if it's there and stop polling
        removeBanner();
        stopPolling();
        return;
      }

      showBanner(me.email);
    } catch (_) {}
  }

  function removeBanner() {
    const banner = document.getElementById('_emailVerifyBanner');
    if (!banner) return;
    const h = banner.offsetHeight;
    banner.remove();
    document.body.style.paddingTop = Math.max(0, (parseInt(document.body.style.paddingTop) || 0) - h) + 'px';
  }

  function startPolling() {
    // Poll every 5s while tab is visible so banner disappears the moment
    // the user verifies in another tab/window without needing a full reload
    if (_pollTimer) return;
    _pollTimer = setInterval(() => {
      if (document.visibilityState !== 'hidden') checkVerification();
    }, 5000);
  }

  function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  function showBanner(email) {
    if (document.getElementById('_emailVerifyBanner')) return;

    const banner = document.createElement('div');
    banner.id = '_emailVerifyBanner';
    banner.style.cssText = [
      'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:99999',
      'background:linear-gradient(90deg,#92400e,#78350f)',
      'border-bottom:1px solid rgba(245,158,11,0.5)',
      'padding:10px 20px',
      'display:flex', 'align-items:center', 'justify-content:space-between',
      'gap:12px', 'flex-wrap:wrap',
      'box-shadow:0 2px 8px rgba(0,0,0,0.3)',
    ].join(';');

    banner.innerHTML = `
      <span style="color:#fde68a;font-size:0.85rem;line-height:1.4;">
        ✉️ <strong>Please verify your email</strong> — check your inbox at
        <strong>${escBanner(email)}</strong> for a verification link.
      </span>
      <div style="display:flex;gap:10px;flex-shrink:0;">
        <button id="_resendVerifyBtn" style="padding:5px 14px;background:rgba(245,158,11,0.25);border:1px solid rgba(245,158,11,0.5);border-radius:6px;color:#fde68a;font-size:0.8rem;cursor:pointer;white-space:nowrap;">
          Resend Email
        </button>
        <button id="_dismissVerifyBtn" style="padding:5px 10px;background:transparent;border:1px solid rgba(245,158,11,0.3);border-radius:6px;color:#d97706;font-size:0.8rem;cursor:pointer;">
          ✕
        </button>
      </div>`;

    document.body.prepend(banner);

    // Push page content down so banner doesn't overlap header
    document.body.style.paddingTop = (parseInt(document.body.style.paddingTop) || 0) + banner.offsetHeight + 'px';

    document.getElementById('_dismissVerifyBtn').addEventListener('click', () => {
      sessionStorage.setItem(STORAGE_KEY, '1');
      stopPolling();
      removeBanner();
    });

    document.getElementById('_resendVerifyBtn').addEventListener('click', async () => {
      const btn = document.getElementById('_resendVerifyBtn');
      if (!btn) return;
      btn.disabled = true;
      btn.textContent = 'Sending...';
      try {
        const r = await fetch('/api/resend-verification-email', {
          method: 'POST', credentials: 'include'
        });
        btn.textContent = r.ok ? 'Sent! Check your inbox' : 'Failed — try again';
        if (r.ok) btn.style.color = '#6ee7b7';
      } catch (_) {
        btn.textContent = 'Error — try again';
      }
      setTimeout(() => {
        if (document.getElementById('_resendVerifyBtn')) {
          btn.disabled = false;
          btn.textContent = 'Resend Email';
        }
      }, 10000);
    });

    // Start polling now that the banner is visible
    startPolling();

    // Also re-check immediately when the user returns to this tab
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') checkVerification();
    });
  }

  function escBanner(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Run after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkVerification);
  } else {
    checkVerification();
  }
})();
