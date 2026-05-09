// Auto-extracted from index.html inline scripts
// Generated for CSP compliance (Phase 5)

async function login() {
  const emailInput = document.getElementById('email');
  const passwordInput = document.getElementById('password');

  if (!emailInput.value || !passwordInput.value) {
    showError('Please enter both email and password');
    return;
  }

  try {
    const res = await fetch("/api/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: emailInput.value,
        password: passwordInput.value
      })
    });

    if (!res.ok) {
      showError('Invalid login credentials');
      return;
    }

    // Redirect back to original page if we were sent here from a protected link.
    // Audit fix (May 2026): validate the redirect target. Previously any
    // ?redirect=https://evil.com would be honored as window.location.href —
    // open redirect → phishing vector. Only allow same-origin app paths
    // starting with `/app/`.
    const params = new URLSearchParams(window.location.search);
    const rawRedirect = params.get('redirect');
    function _safeRedirect(raw) {
      if (!raw) return null;
      let decoded;
      try { decoded = decodeURIComponent(raw); } catch (_) { return null; }
      // Reject anything that could route off-origin: schemes, protocol-relative,
      // backslash tricks, etc. Only accept app paths.
      if (!decoded.startsWith('/app/')) return null;
      if (decoded.includes('//') || decoded.includes('\\')) return null;
      return decoded;
    }
    let destination = _safeRedirect(rawRedirect) || '/app/user-profile.html';

    // If the redirect target is an admin page, verify the user is actually an admin
    // before following it — prevents non-admin users from being dumped on admin.html
    if (destination.includes('admin.html')) {
      try {
        const meRes = await fetch('/api/me', { credentials: 'include' });
        const me = meRes.ok ? await meRes.json() : {};
        // Audit fix (May 2026): `if (!me.is_admin)` was a latent bug — the
        // API used to return the literal TEXT string `'false'` for non-admins,
        // and `!'false'` is `false` (truthy string), so the gate failed open.
        // Migration normalized to bool, but keep this defensive across all
        // historical forms (true / 'true' / 1 / '1' all admit; everything
        // else — including 'false', 0, null, undefined — denies).
        const isAdmin = me.is_admin === true || me.is_admin === 'true'
                     || me.is_admin === 1 || me.is_admin === '1';
        if (!isAdmin) {
          destination = '/app/user-profile.html';
        }
      } catch(e) {
        destination = '/app/user-profile.html';
      }
    }

    window.location.href = destination;
  } catch (error) {
    console.error('Login error:', error);
    showError('An error occurred during login');
  }
}

function showForgotPasswordModal() {
  const modalHTML = `
    <div class="field">
      <label>Email Address</label>
      <input type="email" id="resetEmail" placeholder="you@email.com">
    </div>
  `;
  
  showModal('Reset Password', modalHTML, [
    { text: 'Cancel', onClick: null },
    { text: 'Send Reset Link', primary: true, onClick: sendPasswordReset }
  ]);
  
  setTimeout(() => {
    const resetEmailInput = document.getElementById('resetEmail');
    if (resetEmailInput) {
      resetEmailInput.focus();
      resetEmailInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
          sendPasswordReset();
        }
      });
    }
  }, 100);
}

async function sendPasswordReset() {
  const email = document.getElementById('resetEmail')?.value;
  
  if (!email) {
    // Show error inside the modal, not behind it
    const errEl = document.getElementById('resetEmailError');
    if (errEl) { errEl.textContent = 'Please enter your email address'; errEl.style.display = 'block'; }
    else showError('Please enter your email address');
    return;
  }

  // Disable button while sending
  const sendBtn = document.querySelector('#modalOverlay .btn.primary');
  if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = 'Sending...'; }

  try {
    await fetch('/api/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    });

    // Close modal first, then show success below it
    closeModal();
    showSuccess('If an account exists with that email, a password reset link has been sent.');
  } catch (error) {
    console.error('Reset error:', error);
    if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = 'Send Reset Link'; }
    showError('An error occurred. Please try again.');
  }
}

async function findMusic() {
  const city = document.getElementById('searchCity').value.trim();
  if (!city) {
    showError('Please enter a city name');
    return;
  }
  // If already blocked by overlay, don't proceed
  if (typeof isCityBlocked === 'function' && isCityBlocked()) return;
  // Validate city before navigating
  try {
    const r = await fetch('/api/validate-city?city=' + encodeURIComponent(city) + '&_t=' + Date.now());
    const d = await r.json();
    if (!d.valid) {
      showCityError(document.getElementById('searchCity'), true);
      return;
    }
  } catch(e) {}
  window.location.href = `/app/public-gigs.html?city=${encodeURIComponent(city)}`;
}

// Init shared city autocomplete + validation
initCityAutocomplete({ inputId: 'searchCity' });

// Enter on city input triggers search (only if autocomplete dropdown not open)
document.getElementById('searchCity').addEventListener('keypress', function(e) {
  if (e.key === 'Enter') {
    // The shared module handles Enter when dropdown is open (picks item)
    // Only findMusic if no dropdown interaction happened
    setTimeout(function() { findMusic(); }, 100);
  }
});

document.getElementById('email').addEventListener('keypress', function (e) {
  if (e.key === 'Enter') login();
});

document.getElementById('password').addEventListener('keypress', function (e) {
  if (e.key === 'Enter') login();
});