// Auto-extracted from index.html inline scripts
// Generated for CSP compliance (Phase 5)

// ── Part 10p: Artist-invitation token banner on login ───────────────────────
// When the page is loaded as /app/index.html?invite=<token>, fetch the
// invitation details and show a banner above the login form. Pre-fills the
// email field so the user just enters their password. After login the
// pending-invite popup (loaded from user-dropdown.js on the next page) takes
// over and shows the "Request Preferred Status at:" prompt.
(function _loginInviteBanner() {
  try {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('invite');
    if (!token) return;
    fetch('/api/artist-invitations/by-token/' + encodeURIComponent(token), { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(inv => {
        if (!inv) return;
        const emailEl = document.getElementById('email');
        if (emailEl && inv.invited_email) emailEl.value = inv.invited_email;
        const inviter = inv.inviter_name || 'A venue';
        const venueNames = (inv.venues || []).map(v => v.venue_name).filter(Boolean);
        const venuesPhrase = venueNames.length === 0 ? 'their venue'
          : venueNames.length === 1 ? venueNames[0]
          : venueNames.length === 2 ? (venueNames[0] + ' and ' + venueNames[1])
          : (venueNames.slice(0, -1).join(', ') + ', and ' + venueNames[venueNames.length - 1]);
        const escHtml = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const banner = document.createElement('div');
        banner.id = '_inviteLoginBanner';
        banner.style.cssText = 'background:rgba(6,182,212,0.10);border:1px solid rgba(6,182,212,0.35);border-radius:6px;padding:12px 14px;margin:0 auto 14px;max-width:420px;font-size:13px;color:#a5f3fc;line-height:1.5;text-align:center;';
        banner.innerHTML = '🎶 <strong>' + escHtml(inviter) + '</strong> from <strong>' + escHtml(venuesPhrase) + '</strong> invited you. Log in to accept.';
        function _insertBanner() {
          // Insert just above the email input
          if (emailEl && emailEl.parentNode && emailEl.parentNode.parentNode) {
            emailEl.parentNode.parentNode.insertBefore(banner, emailEl.parentNode);
          }
        }
        if (document.readyState === 'loading') {
          document.addEventListener('DOMContentLoaded', _insertBanner);
        } else {
          _insertBanner();
        }
      })
      .catch(() => {});
  } catch (e) { /* non-fatal */ }
})();

async function login() {
  const emailInput = document.getElementById('email');
  const passwordInput = document.getElementById('password');

  if (!emailInput.value || !passwordInput.value) {
    showError('Please enter both email and password');
    return;
  }

  // Audit fix (May 2026 part 5): disable the Sign In button + show "Signing
  // in…" so rapid double-clicks don't fire two POSTs (the second can race
  // with the first, hit brute-force lockout, or land on the wrong destination
  // after the is_admin recheck).
  const _btn = document.querySelector('button[onclick="login()"]')
            || document.querySelector('button[type="submit"]')
            || document.querySelector('#loginBtn');
  const _origLabel = _btn ? _btn.textContent : null;
  if (_btn) { _btn.disabled = true; _btn.textContent = 'Signing in…'; }
  function _restoreLoginBtn() {
    if (_btn) { _btn.disabled = false; _btn.textContent = _origLabel || 'Sign In'; }
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
      _restoreLoginBtn();
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
    _restoreLoginBtn();
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
  // Send visitors to the clean vanity URL when possible:
  //   "Thousand Oaks" → gigsfill.com/thousandoaks
  // The resolver serves public-gigs.html with the city pre-filtered and
  // gracefully falls back if the slug doesn't match anything.
  const slug = city.toLowerCase().replace(/[^a-z0-9]+/g, '');
  if (slug && slug.length >= 2 && slug.length <= 60) {
    window.location.href = `/${slug}`;
  } else {
    window.location.href = `/app/public-gigs.html?city=${encodeURIComponent(city)}`;
  }
}

// Init shared city autocomplete + validation
initCityAutocomplete({ inputId: 'searchCity' });

// Pre-fill the city field based on the visitor's IP (best-effort,
// silently no-ops on failure, DNT/GPC opt-out, or if the user already
// typed something). Also surfaces a small "We guessed your city" note so
// the autofill isn't a silent surprise — privacy good citizenship and
// also reduces "wait, why is Phoenix in the box?" confusion.
(function autoSuggestCity() {
  const input = document.getElementById('searchCity');
  if (!input || input.value.trim()) return;
  fetch('/api/geo/suggest-city', { credentials: 'omit' })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (!data || !data.ok || !data.city) return;
      if (input.value.trim()) return;  // user typed during the round-trip
      input.value = data.city;

      // Add a small hint under the field. Clicking "(change)" clears it.
      const wrap = input.parentElement;
      if (!wrap || wrap.querySelector('.gf-city-guess-note')) return;
      const note = document.createElement('div');
      note.className = 'gf-city-guess-note';
      note.style.cssText =
        'font-size: 0.72rem; color: var(--text-gray);' +
        ' margin-top: 4px; line-height: 1.4;';
      note.innerHTML =
        'We guessed your city from your IP address - ' +
        '<a href="#" class="gf-city-guess-change"' +
        ' style="color: var(--cyan); text-decoration: none;' +
        ' border-bottom: 1px dashed rgba(6,182,212,0.55);">change</a>' +
        ' · <a href="/app/legal.html#gf-privacy-section-marker"' +
        ' style="color: var(--text-gray); text-decoration: underline;">privacy</a>';
      wrap.appendChild(note);
      // "change" link clears the field and removes the note.
      note.querySelector('.gf-city-guess-change').addEventListener('click', (e) => {
        e.preventDefault();
        input.value = '';
        input.focus();
        note.remove();
      });
      // First real keystroke also removes the note (they're taking over).
      input.addEventListener('input', function once() {
        note.remove();
        input.removeEventListener('input', once);
      });
    })
    .catch(() => { /* ignore */ });
})();

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