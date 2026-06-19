(async function authGuard() {
  // Pages that verified users can still access even without verifying email.
  // user-profile.html is allowed so they can update their email address.
  const VERIFY_EXEMPT = [
    '/app/verify-email.html',
    '/app/user-profile.html',
  ];

  const currentPath = window.location.pathname;
  const isExempt = VERIFY_EXEMPT.some(p => currentPath.endsWith(p.replace('/app/', '')));

  // UX fix (Jun 2026): show a small "checking session…" overlay if /api/me
  // hasn't resolved within 600ms. Without this, slow networks gave users
  // up to 12s of blank-white page (the pre-paint script sets
  // visibility:hidden on <html>, which would normally hide our overlay
  // too — we use visibility:visible !important to break out of that).
  let _guard_overlay = null;
  const _guard_overlay_timer = setTimeout(() => {
    try {
      if (!document.body) return;
      const o = document.createElement('div');
      o.id = '__gf_auth_loading__';
      o.setAttribute('style', [
        'position:fixed', 'inset:0',
        'background:#0a0a14',
        'display:flex', 'flex-direction:column', 'align-items:center', 'justify-content:center',
        'gap:14px',
        'z-index:2147483647',
        'visibility:visible !important',
        'opacity:1',
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif",
        'color:rgba(255,255,255,0.65)',
        'font-size:0.85rem', 'letter-spacing:0.04em',
      ].join(';'));
      o.innerHTML = `
        <div style="width:36px;height:36px;border-radius:50%;
                    border:3px solid rgba(124,107,255,0.18);
                    border-top-color:#a855f7;
                    animation:__gf_auth_spin__ .9s linear infinite;"></div>
        <div>Checking session&hellip;</div>
        <style>@keyframes __gf_auth_spin__ { to { transform: rotate(360deg); } }</style>
      `;
      document.body.appendChild(o);
      _guard_overlay = o;
    } catch (_) {}
  }, 600);
  function _hideOverlay() {
    clearTimeout(_guard_overlay_timer);
    if (_guard_overlay) { try { _guard_overlay.remove(); } catch (_) {} _guard_overlay = null; }
  }

  // Audit fix (May 2026 part 7): timeout fallback. The protected-page inline
  // pre-paint hide sets visibility:hidden synchronously; this script flips it
  // back on success / navigates away on failure. If the fetch hangs (offline,
  // slow network, broken backend), the page stays invisible forever with no
  // spinner or error. After 12s, treat that as a network failure and route
  // the user to the login page so they're not staring at a white screen.
  const _guard_timeout = setTimeout(() => {
    _hideOverlay();
    try {
      document.documentElement.style.visibility = 'visible';
    } catch (_) {}
    // Pass pathname+search+hash (NOT full URL with origin) so the
    // login page's _safeRedirect — which requires the decoded value
    // to start with /app/ — accepts it. Previously we encoded
    // window.location.href which decodes to "https://gigsfill.com/…"
    // and got rejected, so post-login the user landed on the default
    // profile page no matter what URL they clicked.
    const _ret = encodeURIComponent(window.location.pathname + window.location.search + window.location.hash);
    window.location.href = `/app/index.html?redirect=${_ret}`;
  }, 12000);

  try {
    const _ctrl = ('AbortController' in window) ? new AbortController() : null;
    if (_ctrl) setTimeout(() => { try { _ctrl.abort(); } catch (_) {} }, 10000);
    const res = await fetch("/api/me", {
      credentials: "include",
      signal: _ctrl ? _ctrl.signal : undefined
    });
    clearTimeout(_guard_timeout);

    if (!res.ok) {
      throw new Error("Not authenticated");
    }

    const user = await res.json();

    if (!user || !user.id) {
      throw new Error("Invalid user");
    }

    // ── Email verification gate ──────────────────────────────────────────
    // If the user has not verified their email and this page is not exempt,
    // block access and redirect to the verify-email wall page.
    // Admins are always exempt so they can never be locked out.
    // Audit fix (May 2026): handle every form `is_admin` has had — true,
    // 'true', 1, '1'. The May 8 column migration normalized stored values to
    // 0/1 but raw API responses can still return either form depending on
    // SQLite affinity. Tighter than the previous two-form check.
    const isAdmin = user.is_admin === true || user.is_admin === 'true'
                 || user.is_admin === 1 || user.is_admin === '1';
    if (!user.email_verified && !isExempt && !isAdmin) {
      window.location.href = '/app/verify-email.html';
      return;
    }

    // ✅ AUTH + VERIFICATION OK — SHOW PAGE
    _hideOverlay();
    document.documentElement.style.visibility = "visible";
  } catch (err) {
    clearTimeout(_guard_timeout);
    _hideOverlay();
    console.warn("Auth failed — redirecting", err);

    // Clear session cookies
    document.cookie = "session_token=; Max-Age=0; path=/";
    document.cookie = "user_id=; Max-Age=0; path=/";

    // Preserve current URL so login can redirect back. pathname+search+hash
    // only (same reason as the timeout path above): _safeRedirect on the
    // login page requires the decoded value to start with /app/.
    const returnUrl = encodeURIComponent(window.location.pathname + window.location.search + window.location.hash);
    window.location.href = `/app/index.html?redirect=${returnUrl}`;
  }
})();
  

async function logout() {
  await fetch("/api/logout", { method: "POST" });
  document.cookie = "session_token=; Max-Age=0; path=/";
  document.cookie = "user_id=; Max-Age=0; path=/";
  window.location.href = "/app/index.html";
}
