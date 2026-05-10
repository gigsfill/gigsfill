/*
 * ============================================================================
 * GigsFill Modal Helpers — single source of truth (PREVIEW)
 * ============================================================================
 * Replaces every per-page helper:
 *   - modals.js:showModal/closeModal/showSuccess/showError/showConfirm (legacy)
 *   - artist.book-gigs.js:showStyledModal (purple-bordered)
 *   - artist.book-gigs.js:showSuccessModal
 *   - venue.create-gigs.js:showAlert
 *   - inline `document.createElement` modal builders scattered around
 *
 * All helpers render gfm-modal-* classes from app/static/css/modals.css.
 * Once approved, this file is loaded on every page and the duplicates above
 * become thin window.* assignments at the top (already done in this file).
 *
 * ─── Public API ─────────────────────────────────────────────────────────────
 *   showStyledModal(title, content, buttons, opts)  — generic dialog
 *   showAlert(message, title)                       — single-button OK
 *   showConfirm(title, message, onConfirm, onCancel)— Cancel/Confirm pair
 *   showSuccessModal(title, message, onClose)       — green-stripe success
 *   showErrorModal(title, message, onClose)         — red-stripe error
 *   closeAllModals()                                — clear stack
 *
 * `buttons` array entries: { text, style?: 'primary'|'ghost'|'danger', onClick? }
 *   - onClick may return false to prevent the modal from closing (useful for
 *     async confirmation that needs to leave the modal open until done)
 *
 * `opts` object (all optional): {
 *     size:        'sm' | 'md' | 'lg' | 'xl' | 'full',  // default 'md'
 *     tone:        'success' | 'error' | 'warning',
 *     dismissible: boolean,                              // default true; false hides X + esc + backdrop click
 *     onClose:     fn,                                   // fired after any close path
 *   }
 *
 * Security:
 *   - title is set via textContent (XSS-safe; will render `<script>` as text)
 *   - content is set via innerHTML (callers are responsible for escaping
 *     user-supplied strings; use the global esc() helper from security.js)
 *   - showAlert / showConfirm / showSuccess / showError auto-escape their
 *     message argument since they're convenience wrappers for plain strings
 * ============================================================================
 */

(function () {
  'use strict';

  // ── HTML-escape helper (mirrors security.js esc; defined locally so this
  //    file works even if security.js loaded after) ──────────────────────────
  function _esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ── Track modal stack for closeAllModals + Esc handling ────────────────
  const _stack = [];

  // ── Auto-tone heuristic ────────────────────────────────────────────────
  // If a caller doesn't explicitly pass opts.tone, infer it from the title.
  // Negative-action keywords → 'error' (red), positive → 'success' (green),
  // "are you sure" verbiage → 'warning' (amber). Caller-supplied tone always
  // wins. Set opts.tone = '' (empty string) explicitly to force neutral
  // purple if you want to override the inference. This runs centrally so
  // every page in the app gets consistent tone semantics without each
  // call site having to pass {tone:'error'} manually.
  const _ERROR_RX   = /(error|fail|cancel(led|lation)?|unavailable|invalid|denied|could ?not|cannot|expired|rejected|declined|not found|not authorized|forbidden|no access|missing|conflict|incorrect|wrong|exhausted|remove|delete|ban\b|block(ed)?|abort|leave|kick|stop|🚫|✕|❌|⛔|⚠️)/i;
  const _SUCCESS_RX = /(success|saved|booked|confirmed|sent|completed|published|reset successfully|signed|transferred|paid|approved|welcome|done|ok!|✓|🎉|🎊)/i;
  const _WARNING_RX = /^(are you sure|warning|caution|heads up|review|verify|double-check|please confirm)/i;
  function _inferTone(title) {
    const t = String(title || '');
    if (_WARNING_RX.test(t)) return 'warning';
    if (_ERROR_RX.test(t))   return 'error';
    if (_SUCCESS_RX.test(t)) return 'success';
    return null;
  }

  // ── Core builder ───────────────────────────────────────────────────────
  function _buildModal(opts) {
    // Auto-tone: only fires when caller didn't specify tone at all
    // (undefined). To force neutral, caller can pass {tone: ''} or
    // {tone: 'info'}.
    let tone = opts.tone;
    if (tone === undefined) tone = _inferTone(opts.title);

    const overlay = document.createElement('div');
    overlay.className = 'gfm-modal-overlay';

    const modal = document.createElement('div');
    let cls = 'gfm-modal';
    if (opts.size) cls += ' gfm-modal--' + opts.size;
    if (tone) cls += ' gfm-modal--' + tone;
    modal.className = cls;

    // Header (rendered if title given, OR if dismissible to show the X)
    if (opts.title || opts.dismissible !== false) {
      const header = document.createElement('div');
      header.className = 'gfm-modal-header';

      const title = document.createElement('h3');
      title.className = 'gfm-modal-title';
      title.textContent = opts.title || '';   // textContent is XSS-safe
      header.appendChild(title);

      if (opts.dismissible !== false) {
        const close = document.createElement('button');
        close.className = 'gfm-modal-close';
        close.setAttribute('aria-label', 'Close');
        close.type = 'button';
        close.textContent = '✕';
        close.onclick = () => _closeOne(overlay, opts);
        header.appendChild(close);
      }
      modal.appendChild(header);
    }

    // Body
    const body = document.createElement('div');
    body.className = 'gfm-modal-body' + (opts.bodyFlush ? ' gfm-modal-body--flush' : '');
    if (opts.content instanceof HTMLElement) {
      body.appendChild(opts.content);
    } else if (typeof opts.content === 'string') {
      body.innerHTML = opts.content;
    }
    modal.appendChild(body);

    // Footer
    if (Array.isArray(opts.buttons) && opts.buttons.length) {
      const footer = document.createElement('div');
      footer.className = 'gfm-modal-footer';
      opts.buttons.forEach((b) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn ' + (b.style === 'ghost' ? 'ghost'
                                : b.style === 'danger' ? 'danger'
                                : 'primary');
        btn.textContent = b.text || 'OK';
        btn.onclick = () => {
          let stayOpen = false;
          try {
            if (typeof b.onClick === 'function') {
              const r = b.onClick();
              if (r === false) stayOpen = true;
            }
          } catch (e) { console.error('[gfm-modal] button onClick error:', e); }
          if (!stayOpen) _closeOne(overlay, opts);
        };
        footer.appendChild(btn);
      });
      modal.appendChild(footer);
    }

    // Backdrop click (when dismissible)
    if (opts.dismissible !== false) {
      overlay.onclick = (e) => { if (e.target === overlay) _closeOne(overlay, opts); };
    }

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Esc-to-close (only on the topmost modal)
    if (opts.dismissible !== false) {
      const onKey = (e) => {
        if (e.key === 'Escape' && _stack[_stack.length - 1] === overlay) {
          _closeOne(overlay, opts);
        }
      };
      overlay._gfKeyHandler = onKey;
      document.addEventListener('keydown', onKey);
    }

    _stack.push(overlay);
    return overlay;
  }

  function _closeOne(overlay, opts) {
    if (!overlay || !overlay.parentNode) return;
    overlay.classList.add('gfm-modal-closing');
    if (overlay._gfKeyHandler) document.removeEventListener('keydown', overlay._gfKeyHandler);
    setTimeout(() => {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      const i = _stack.indexOf(overlay); if (i >= 0) _stack.splice(i, 1);
      try { if (opts && typeof opts.onClose === 'function') opts.onClose(); } catch (e) {}
    }, 160);
  }

  // ── Public helpers ─────────────────────────────────────────────────────

  // showStyledModal(title, content, buttons, opts)
  //   buttons: [{ text, style: 'primary'|'ghost'|'danger', onClick }]
  //   opts:    { size, tone, dismissible, onClose, bodyFlush }
  window.showStyledModal = function (title, content, buttons, opts) {
    const o = Object.assign({ title, content, buttons }, opts || {});
    return _buildModal(o);
  };

  // showAlert(message, title?, opts?)
  //   opts: { tone: 'success'|'error'|'warning', size, dismissible, onClose }
  // Pass tone:'error' for cancellations/failures, 'success' for confirmations,
  // 'warning' for "are you sure" prompts. Default = neutral purple.
  window.showAlert = function (message, title, opts) {
    return _buildModal(Object.assign({
      title: title || 'Notice',
      content: '<p>' + _esc(message) + '</p>',
      buttons: [{ text: 'OK', style: 'primary' }],
      size: 'sm',
    }, opts || {}));
  };

  // showConfirm(title, message, onConfirm, onCancel?, opts?)
  //   opts: { tone, size, confirmLabel, cancelLabel, confirmStyle, dismissible }
  // Use tone:'warning' for destructive confirmations (cancel payment, delete
  // gig); tone:'error' for already-failed states needing user ack.
  window.showConfirm = function (title, message, onConfirm, onCancel, opts) {
    opts = opts || {};
    return _buildModal(Object.assign({
      title: title,
      content: '<p>' + _esc(message) + '</p>',
      buttons: [
        { text: opts.cancelLabel  || 'Cancel',  style: 'ghost',   onClick: onCancel },
        { text: opts.confirmLabel || 'Confirm', style: opts.confirmStyle || 'primary', onClick: onConfirm },
      ],
      size: 'sm',
    }, opts));
  };

  // showSuccessModal(title, message, onClose?)
  window.showSuccessModal = function (title, message, onClose) {
    return _buildModal({
      title: title || 'Success',
      content: '<p>' + _esc(message) + '</p>',
      buttons: [{ text: 'OK', style: 'primary', onClick: onClose }],
      size: 'sm',
      tone: 'success',
    });
  };

  // showErrorModal(title, message, onClose?)
  // message may be a plain string (escaped) or HTMLElement (used as-is).
  window.showErrorModal = function (title, message, onClose) {
    let content;
    if (message instanceof HTMLElement) content = message;
    else content = '<p>' + _esc(message) + '</p>';
    return _buildModal({
      title: title || 'Error',
      content: content,
      buttons: [{ text: 'OK', style: 'primary', onClick: onClose }],
      size: 'sm',
      tone: 'error',
    });
  };

  // closeAllModals() — drain the stack
  window.closeAllModals = function () {
    [..._stack].forEach((o) => _closeOne(o));
  };

  // NOTE: this file deliberately does NOT alias window.showModal /
  // window.closeModal / window.showSuccess / window.showError to the new
  // helpers. Those globals are owned by the legacy modals.js (still loaded
  // on reset_password.html / support-ticket.html / review.html). Aliasing
  // would silently switch those pages to the new look, which we want to do
  // only deliberately during Phase 2 migration.

})();
