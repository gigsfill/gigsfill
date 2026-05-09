(async function authGuard() {
  // Pages that verified users can still access even without verifying email.
  // user-profile.html is allowed so they can update their email address.
  const VERIFY_EXEMPT = [
    '/app/verify-email.html',
    '/app/user-profile.html',
  ];

  const currentPath = window.location.pathname;
  const isExempt = VERIFY_EXEMPT.some(p => currentPath.endsWith(p.replace('/app/', '')));

  try {
    const res = await fetch("/api/me", {
      credentials: "include"
    });

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
    document.documentElement.style.visibility = "visible";
  } catch (err) {
    console.warn("Auth failed — redirecting", err);

    // Clear session cookies
    document.cookie = "session_token=; Max-Age=0; path=/";
    document.cookie = "user_id=; Max-Age=0; path=/";

    // Preserve current URL so login can redirect back
    const returnUrl = encodeURIComponent(window.location.href);
    window.location.href = `/app/index.html?redirect=${returnUrl}`;
  }
})();
  

async function logout() {
  await fetch("/api/logout", { method: "POST" });
  document.cookie = "session_token=; Max-Age=0; path=/";
  document.cookie = "user_id=; Max-Age=0; path=/";
  window.location.href = "/app/index.html";
}
