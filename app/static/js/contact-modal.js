/* "Send us a note" contact modal — homepage secondary CTA.
 *
 * Opened by the small text link under the Request-Demo button. Small
 * form (name / email / message) plus a honeypot; posts to /api/contact
 * which is rate-limited server-side to 3/hour per IP and silent-
 * succeeds on honeypot fill. Success shows a branded thank-you screen
 * inside the same modal shell.
 *
 * Reuses the styling patterns from demo-request-modal.js so both feel
 * like one system without pulling in the full slot-picker weight.
 */
(function () {
  'use strict';

  var _stylesInjected = false;
  function _injectStyles() {
    if (_stylesInjected) return;
    _stylesInjected = true;
    var css = [
      '.cn-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.68);z-index:99998;display:flex;align-items:center;justify-content:center;padding:20px;opacity:0;transition:opacity 0.18s ease;}',
      '.cn-overlay.cn-open{opacity:1;}',
      '.cn-modal{color-scheme:dark;background:#151b28;border:1px solid rgba(255,255,255,0.08);border-radius:14px;max-width:460px;width:100%;max-height:92vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.6);transform:translateY(10px);transition:transform 0.18s ease;}',
      '.cn-overlay.cn-open .cn-modal{transform:translateY(0);}',
      '.cn-header{padding:22px 26px 18px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);border-radius:14px 14px 0 0;position:relative;}',
      '.cn-header h2{margin:0;color:#fff;font-size:20px;font-weight:700;}',
      '.cn-header p{margin:6px 0 0;color:rgba(255,255,255,0.85);font-size:13px;line-height:1.5;}',
      '.cn-close{position:absolute;top:14px;right:14px;background:transparent;border:0;color:#fff;font-size:22px;line-height:1;cursor:pointer;opacity:0.85;padding:4px 8px;border-radius:4px;}',
      '.cn-close:hover{opacity:1;background:rgba(255,255,255,0.12);}',
      '.cn-body{padding:22px 26px 8px;color:#e5e7eb;}',
      '.cn-field{margin-bottom:14px;}',
      '.cn-body label{display:block;font-size:12px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;}',
      '.cn-body label.cn-req::after{content:" *";color:#f87171;}',
      '.cn-body input[type=text],.cn-body input[type=email],.cn-body textarea{width:100%;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:6px;color:#e5e7eb;font-size:14px;font-family:inherit;box-sizing:border-box;transition:border-color 0.15s;}',
      '.cn-body input:focus,.cn-body textarea:focus{outline:none;border-color:#06b6d4;background:rgba(255,255,255,0.05);}',
      '.cn-body textarea{min-height:110px;resize:vertical;}',
      '.cn-footer{padding:14px 26px 22px;border-top:1px solid rgba(255,255,255,0.06);display:flex;justify-content:flex-end;gap:10px;}',
      '.cn-btn{padding:10px 20px;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;border:0;font-family:inherit;transition:all 0.15s;}',
      '.cn-btn-ghost{background:transparent;color:#94a3b8;border:1px solid rgba(255,255,255,0.1);}',
      '.cn-btn-ghost:hover{color:#e5e7eb;background:rgba(255,255,255,0.04);}',
      '.cn-btn-primary{background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;}',
      '.cn-btn-primary:hover{box-shadow:0 4px 16px rgba(139,92,246,0.4);transform:translateY(-1px);}',
      '.cn-btn-primary:disabled{opacity:0.5;cursor:not-allowed;transform:none;box-shadow:none;}',
      '.cn-error{color:#f87171;font-size:13px;padding:10px 14px;background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.2);border-radius:6px;margin:0 0 12px;}',
      '.cn-success{padding:32px 26px;text-align:center;color:#e5e7eb;}',
      '.cn-success-icon{font-size:52px;margin-bottom:10px;line-height:1;}',
      '.cn-success h3{margin:0 0 8px;font-size:20px;font-weight:700;background:linear-gradient(135deg,#8b5cf6,#06b6d4);background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent;}',
      '.cn-success p{margin:0 0 20px;color:#94a3b8;font-size:14px;line-height:1.6;}',
      '.cn-hp{position:absolute !important;left:-9999px !important;opacity:0 !important;pointer-events:none !important;height:0;width:0;}',
      '@media (max-width:480px){.cn-header{padding:18px 18px 16px;}.cn-body{padding:18px 18px 4px;}.cn-footer{padding:12px 18px 18px;}}',
    ].join('\n');
    var s = document.createElement('style');
    s.textContent = css;
    document.head.appendChild(s);
  }

  var overlay = null;
  var errorEl = null;

  function _showError(msg) {
    if (!overlay) return;
    if (!errorEl) {
      errorEl = document.createElement('div');
      errorEl.className = 'cn-error';
      var body = overlay.querySelector('.cn-body');
      if (body) body.insertBefore(errorEl, body.firstChild);
    }
    errorEl.textContent = msg;
    errorEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  function _hideError() {
    if (errorEl && errorEl.parentNode) errorEl.parentNode.removeChild(errorEl);
    errorEl = null;
  }

  function _showSuccess() {
    if (!overlay) return;
    var modal = overlay.querySelector('.cn-modal');
    if (!modal) return;
    modal.innerHTML = '';
    var wrap = document.createElement('div');
    wrap.className = 'cn-success';
    wrap.innerHTML =
      '<div class="cn-success-icon">📬</div>' +
      '<h3>Note received</h3>' +
      "<p>Thanks — we'll get back to you shortly.</p>" +
      '<button class="cn-btn cn-btn-primary" onclick="window.closeContactModal()">Close</button>';
    modal.appendChild(wrap);
  }

  async function _submit() {
    _hideError();
    var name    = (overlay.querySelector('#cnName').value    || '').trim();
    var email   = (overlay.querySelector('#cnEmail').value   || '').trim();
    var message = (overlay.querySelector('#cnMessage').value || '').trim();
    var hp      = (overlay.querySelector('#cnHp').value      || '').trim();

    if (!name) return _showError('Please enter your name.');
    if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email))
      return _showError('Please enter a valid email address.');
    if (!message || message.length < 5)
      return _showError('Please write a short message.');

    var submitBtn = overlay.querySelector('#cnSubmitBtn');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Sending…';

    try {
      var res = await fetch('/api/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, email: email, message: message, _hp: hp }),
        credentials: 'omit',
      });
      var body = null;
      try { body = await res.json(); } catch (_) {}
      if (!res.ok) {
        var msg = (body && body.detail) || 'Something went wrong. Please try again.';
        _showError(String(msg));
        submitBtn.disabled = false;
        submitBtn.textContent = 'Send';
        return;
      }
      _showSuccess();
    } catch (e) {
      _showError('Network error — please try again.');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Send';
    }
  }

  window.showContactModal = function () {
    _injectStyles();
    if (overlay) return;

    overlay = document.createElement('div');
    overlay.className = 'cn-overlay';
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) window.closeContactModal();
    });

    overlay.innerHTML =
      '<div class="cn-modal" role="dialog" aria-labelledby="cnTitle">' +
        '<div class="cn-header">' +
          '<button class="cn-close" onclick="window.closeContactModal()">×</button>' +
          '<h2 id="cnTitle">Send us a note</h2>' +
          "<p>Have a question or want to learn more? We'll get back to you shortly.</p>" +
        '</div>' +
        '<div class="cn-body">' +
          '<div class="cn-field">' +
            '<label class="cn-req" for="cnName">Your name</label>' +
            '<input type="text" id="cnName" autocomplete="name" maxlength="120">' +
          '</div>' +
          '<div class="cn-field">' +
            '<label class="cn-req" for="cnEmail">Email</label>' +
            '<input type="email" id="cnEmail" placeholder="you@email.com" autocomplete="email" maxlength="200">' +
          '</div>' +
          '<div class="cn-field">' +
            '<label class="cn-req" for="cnMessage">Message</label>' +
            '<textarea id="cnMessage" placeholder="Tell us what\'s on your mind…" maxlength="4000"></textarea>' +
          '</div>' +
          // Honeypot — hidden from humans, catches bots.
          '<input type="text" id="cnHp" class="cn-hp" tabindex="-1" autocomplete="off">' +
        '</div>' +
        '<div class="cn-footer">' +
          '<button type="button" class="cn-btn cn-btn-ghost" onclick="window.closeContactModal()">Cancel</button>' +
          '<button type="button" class="cn-btn cn-btn-primary" id="cnSubmitBtn">Send</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);
    setTimeout(function () { overlay.classList.add('cn-open'); }, 10);

    overlay.querySelector('#cnSubmitBtn').addEventListener('click', _submit);
    overlay.querySelector('#cnName').focus();
  };

  window.closeContactModal = function () {
    if (!overlay) return;
    overlay.classList.remove('cn-open');
    setTimeout(function () {
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
      overlay = null;
      errorEl = null;
    }, 180);
  };
})();
