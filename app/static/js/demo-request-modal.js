/* Demo Request modal — homepage CTA.
 *
 * Renders a modal with a form to submit up to 3 preferred time slots.
 * Sends to POST /api/demo-request, then shows a branded success
 * message. Self-contained; injects its own <style> block on first
 * open so the modal styling doesn't collide with anything else.
 */
(function () {
  'use strict';

  // Reschedule mode is entered by opening the homepage with
  // ?reschedule=<signed-token>. When set, the modal opens automatically,
  // prefills fields from GET /api/demo-request/reschedule/<token>,
  // posts to the same endpoint (verb POST), and shows a slightly
  // different header. Everything else — the slot picker, phone
  // formatting, city→state autofill — is shared with the new-request
  // path so we don't fork the picker logic.
  var _rescheduleToken = null;
  var _reschedulePrefill = null;
  var _priorSlotHuman = null;

  // Native browser rendering ('title=' works everywhere; no CSS deps).
  var _stylesInjected = false;
  function _injectStyles() {
    if (_stylesInjected) return;
    _stylesInjected = true;
    var css = [
      '.dr-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.68);z-index:99998;display:flex;align-items:center;justify-content:center;padding:20px;opacity:0;transition:opacity 0.18s ease;}',
      '.dr-overlay.dr-open{opacity:1;}',
      // `color-scheme: dark` on the modal root tells Chromium to draw
      // native form controls (select popover, scrollbars, date picker
      // popover, autofill highlight) with dark chrome. Without this
      // the <option> list rendered white regardless of the CSS we set
      // on <option> — that\'s an OS-drawn surface.
      '.dr-modal{color-scheme:dark;background:#151b28;border:1px solid rgba(255,255,255,0.08);border-radius:14px;max-width:540px;width:100%;max-height:92vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.6);transform:translateY(10px);transition:transform 0.18s ease;}',
      '.dr-overlay.dr-open .dr-modal{transform:translateY(0);}',
      '.dr-header{padding:24px 28px 20px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);border-radius:14px 14px 0 0;position:relative;}',
      '.dr-header h2{margin:0;color:#fff;font-size:22px;font-weight:700;}',
      '.dr-header p{margin:6px 0 0;color:rgba(255,255,255,0.85);font-size:13px;line-height:1.55;}',
      '.dr-close{position:absolute;top:16px;right:16px;background:transparent;border:0;color:#fff;font-size:24px;line-height:1;cursor:pointer;opacity:0.85;padding:4px 8px;border-radius:4px;}',
      '.dr-close:hover{opacity:1;background:rgba(255,255,255,0.12);}',
      '.dr-body{padding:24px 28px 8px;color:#e5e7eb;}',
      '.dr-body .dr-field{margin-bottom:16px;}',
      '.dr-body label{display:block;font-size:12px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;}',
      '.dr-body label.dr-req::after{content:" *";color:#f87171;}',
      '.dr-body input[type=text],.dr-body input[type=email],.dr-body input[type=tel],.dr-body textarea{width:100%;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:6px;color:#e5e7eb;font-size:14px;font-family:inherit;box-sizing:border-box;transition:border-color 0.15s;}',
      '.dr-body input:focus,.dr-body textarea:focus{outline:none;border-color:#06b6d4;background:rgba(255,255,255,0.05);}',
      // Match the notifications-all `.dark-dropdown` pattern (proven to
      // render dark across Chrome/Firefox/Safari desktop + mobile) —
      // `!important` overrides any framework/UA rule that would otherwise
      // repaint the popover white on click.
      ".dr-body select{width:100% !important;padding:10px 12px 10px 12px !important;padding-right:32px !important;background-color:#1a1f2e !important;border:1px solid rgba(255,255,255,0.2) !important;border-radius:6px !important;color:#e5e7eb !important;font-size:14px !important;font-family:inherit !important;box-sizing:border-box !important;cursor:pointer !important;-webkit-appearance:none !important;-moz-appearance:none !important;appearance:none !important;color-scheme:dark !important;background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%2394a3b8' d='M1 1l5 5 5-5'/%3E%3C/svg%3E\") !important;background-repeat:no-repeat !important;background-position:right 12px center !important;transition:border-color 0.15s !important;}",
      ".dr-body select:hover{background-color:#212938 !important;border-color:rgba(255,255,255,0.28) !important;}",
      ".dr-body select:focus{outline:none !important;border-color:#06b6d4 !important;background-color:#1a1f2e !important;}",
      ".dr-body select option{background-color:#1a1f2e !important;color:#e5e7eb !important;padding:8px !important;}",
      // Date input: styled `<input type=text readonly>` that opens a
      // custom GfDatePicker popup (defined below). Recurring gigs
      // section proved this is the only reliable cross-browser way to
      // get a fully dark date picker (venue.create-gigs.js:722 comment).
      ".dr-body .dr-date-input{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);border-radius:6px;color:#e5e7eb;font-size:13px;font-family:inherit;box-sizing:border-box;cursor:pointer;padding:8px 10px;text-align:left;transition:border-color 0.15s;caret-color:transparent;}",
      ".dr-body .dr-date-input:focus{outline:none;border-color:#06b6d4;background:rgba(255,255,255,0.06);}",
      // GfDatePicker popup styles — ported verbatim from
      // venue-create-gigs.html:1635-1751.
      ".gf-date-popup{position:absolute;background:#1a1a2e;border:1px solid rgba(124,107,255,0.45);border-radius:8px;padding:10px;box-shadow:0 12px 32px rgba(0,0,0,0.6);z-index:100000;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;user-select:none;min-width:248px;}",
      ".gf-date-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;padding:0 2px;}",
      ".gf-date-title{background:linear-gradient(135deg,#a855f7,#06b6d4);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;font-weight:700;font-size:0.88rem;letter-spacing:0.02em;}",
      ".gf-date-nav{background:rgba(124,107,255,0.10);border:1px solid rgba(124,107,255,0.25);color:#c4b5fd;cursor:pointer;width:26px;height:26px;border-radius:5px;font-size:0.85rem;line-height:1;display:inline-flex;align-items:center;justify-content:center;transition:background 0.12s,border-color 0.12s;font-family:inherit;}",
      ".gf-date-nav:hover{background:rgba(124,107,255,0.22);border-color:rgba(124,107,255,0.55);}",
      ".gf-date-grid{display:grid;grid-template-columns:repeat(7,32px);gap:2px;}",
      ".gf-date-dow{text-align:center;font-size:0.62rem;color:rgba(255,255,255,0.4);padding:4px 0;font-weight:700;letter-spacing:0.05em;}",
      ".gf-date-cell{width:32px;height:28px;background:transparent;border:1px solid transparent;color:#d1d5db;font-size:0.78rem;font-weight:500;cursor:pointer;border-radius:4px;padding:0;line-height:1;transition:background 0.10s,border-color 0.10s;font-family:inherit;}",
      ".gf-date-cell:hover{background:rgba(124,107,255,0.18);border-color:rgba(124,107,255,0.40);}",
      ".gf-date-cell.today{border-color:rgba(34,197,94,0.55);color:#86efac;font-weight:700;}",
      ".gf-date-cell.selected{background:linear-gradient(135deg,#a855f7,#7c3aed);color:#fff;border-color:rgba(168,85,247,0.8);font-weight:700;}",
      ".gf-date-cell.selected:hover{background:linear-gradient(135deg,#c084fc,#a855f7);}",
      ".gf-date-cell.empty{cursor:default;pointer-events:none;}",
      ".gf-date-cell.disabled{opacity:0.28;cursor:not-allowed;pointer-events:none;}",
      ".gf-date-footer{display:flex;justify-content:space-between;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.08);}",
      ".gf-date-footer button{background:transparent;border:0;color:#a78bfa;font-size:0.72rem;font-weight:600;cursor:pointer;padding:2px 6px;border-radius:4px;font-family:inherit;}",
      ".gf-date-footer button:hover{background:rgba(124,107,255,0.15);color:#c4b5fd;}",
      '.dr-body textarea{min-height:70px;resize:vertical;}',
      '.dr-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:12px;}',
      '.dr-slots-hdr{margin:8px 0 12px;font-size:13px;color:#94a3b8;line-height:1.5;}',
      '.dr-slots-hdr strong{color:#e5e7eb;font-weight:600;}',
      '.dr-slot{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);border-radius:8px;padding:12px 14px;margin-bottom:10px;}',
      '.dr-slot-label{font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;font-weight:600;}',
      '.dr-slot-row{display:grid;grid-template-columns:130px auto;gap:14px;align-items:center;}',
      // The custom date input is a styled text field — narrower than a
      // native date input because we don\'t need to reserve space for
      // the OS-drawn calendar icon.
      '.dr-slot .dr-date-input{width:130px;max-width:130px;min-width:0;}',
      '.dr-time-group{display:flex;gap:5px;align-items:center;}',
      // Same chrome as .dr-body select but with a tighter caret zone
      // because the boxes are narrow. Rendering matches the rest of
      // the site's selects (site-wide select style in gigsfill.css).
      // Time selects — same aggressive !important pattern as
      // .dr-body select so the popover renders dark across browsers.
      ".dr-time-select{color-scheme:dark !important;padding:7px 22px 7px 8px !important;font-size:13px !important;background-color:#1a1f2e !important;border:1px solid rgba(255,255,255,0.2) !important;border-radius:6px !important;color:#e5e7eb !important;font-family:inherit !important;box-sizing:border-box !important;cursor:pointer !important;-webkit-appearance:none !important;-moz-appearance:none !important;appearance:none !important;background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 12 8'%3E%3Cpath fill='%2394a3b8' d='M1 1l5 5 5-5'/%3E%3C/svg%3E\") !important;background-repeat:no-repeat !important;background-position:right 7px center !important;}",
      '.dr-time-select:hover{background-color:#212938 !important;border-color:rgba(255,255,255,0.28) !important;}',
      '.dr-time-select:focus{outline:none !important;border-color:#06b6d4 !important;background-color:#1a1f2e !important;}',
      '.dr-time-select option{background-color:#1a1f2e !important;color:#e5e7eb !important;padding:6px !important;}',
      '.dr-time-select.dr-hour{width:60px;}',
      '.dr-time-select.dr-min{width:60px;}',
      '.dr-time-select.dr-ampm{width:60px;margin-left:2px;}',
      // Disabled AM/PM — keep readable, hide caret, obviously non-interactive.
      '.dr-time-select.dr-ampm:disabled{background-image:none !important;padding-right:8px !important;background-color:#1a1f2e !important;color:#e5e7eb !important;border-color:rgba(255,255,255,0.12) !important;opacity:1 !important;cursor:default !important;text-align:center !important;text-align-last:center !important;}',
      '.dr-time-colon{color:#94a3b8;font-weight:700;font-size:16px;flex-shrink:0;}',
      '@media (max-width:480px){.dr-slot-row{grid-template-columns:1fr;gap:8px;}.dr-slot input[type=date]{width:100%;max-width:none;}}',
      '.dr-remove-slot{background:transparent;border:0;color:#94a3b8;font-size:11px;cursor:pointer;padding:4px 8px;border-radius:4px;margin-top:6px;font-family:inherit;}',
      '.dr-remove-slot:hover{color:#f87171;}',
      '.dr-add-slot{width:100%;background:transparent;border:1px dashed rgba(139,92,246,0.4);color:#c4b5fd;padding:10px;font-size:13px;font-weight:600;cursor:pointer;border-radius:6px;margin-bottom:16px;font-family:inherit;transition:all 0.15s;}',
      '.dr-add-slot:hover{border-color:#06b6d4;color:#67e8f9;}',
      '.dr-add-slot:disabled{opacity:0.4;cursor:not-allowed;}',
      '.dr-footer{padding:16px 28px 24px;border-top:1px solid rgba(255,255,255,0.06);display:flex;justify-content:flex-end;gap:10px;}',
      '.dr-btn{padding:11px 22px;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;border:0;font-family:inherit;transition:all 0.15s;}',
      '.dr-btn-ghost{background:transparent;color:#94a3b8;border:1px solid rgba(255,255,255,0.1);}',
      '.dr-btn-ghost:hover{color:#e5e7eb;background:rgba(255,255,255,0.04);}',
      '.dr-btn-primary{background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;}',
      '.dr-btn-primary:hover{box-shadow:0 4px 16px rgba(139,92,246,0.4);transform:translateY(-1px);}',
      '.dr-btn-primary:disabled{opacity:0.5;cursor:not-allowed;transform:none;box-shadow:none;}',
      '.dr-error{color:#f87171;font-size:13px;padding:10px 14px;background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.2);border-radius:6px;margin:0 0 12px;}',
      '.dr-success{padding:32px 28px;text-align:center;color:#e5e7eb;}',
      '.dr-success-icon{font-size:56px;margin-bottom:12px;line-height:1;}',
      '.dr-success h3{margin:0 0 8px;font-size:20px;font-weight:700;background:linear-gradient(135deg,#8b5cf6,#06b6d4);background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent;}',
      '.dr-success p{margin:0 0 20px;color:#94a3b8;font-size:14px;line-height:1.6;}',
      '.dr-hp{position:absolute !important;left:-9999px !important;opacity:0 !important;pointer-events:none !important;height:0;width:0;}',
      '@media (max-width:480px){.dr-grid-2{grid-template-columns:1fr;}.dr-header{padding:20px 20px 18px;}.dr-body{padding:20px 20px 4px;}.dr-footer{padding:14px 20px 20px;}}',
    ].join('\n');
    var s = document.createElement('style');
    s.textContent = css;
    document.head.appendChild(s);
  }

  // ─── date helpers ─────────────────────────────────────────────────
  function _todayIso() {
    var d = new Date();
    return d.toISOString().slice(0, 10);
  }
  function _maxIso() {
    var d = new Date();
    d.setDate(d.getDate() + 45);
    return d.toISOString().slice(0, 10);
  }
  function _defaultDateFor(index) {
    // 1st: 3 days out; 2nd: 5 days; 3rd: 7 days. Just seeds — user can change.
    var offsets = [3, 5, 7];
    var d = new Date();
    d.setDate(d.getDate() + (offsets[index] || 3));
    return d.toISOString().slice(0, 10);
  }
  function _isoToDisp(iso) {
    if (!iso) return '';
    var m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return String(iso);
    return m[2] + '/' + m[3] + '/' + m[1];
  }
  function _dispToIso(disp) {
    if (!disp) return '';
    var m = String(disp).trim().match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (!m) return '';
    var mo = ('0' + m[1]).slice(-2);
    var da = ('0' + m[2]).slice(-2);
    return m[3] + '-' + mo + '-' + da;
  }

  // ─── GfDatePicker (ported from venue.create-gigs.js:730) ─────────
  // Custom dark-themed calendar popup — the native <input type="date">
  // popover can't be reliably themed dark across browsers, so we
  // build our own. Popup styles are in the injected <style> block
  // (.gf-date-popup and children).
  function GfDatePicker(input, opts) {
    this.input = input;
    this.opts = opts || {};
    this.popup = null;
    this.viewDate = new Date();
    this.outsideHandler = null;
    this.repositionHandler = null;
    var self = this;
    input.addEventListener('click', function () { if (!input.disabled) self.open(); });
    input.addEventListener('keydown', function (e) {
      if (input.disabled) return;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); self.open(); }
      if (e.key === 'Escape') self.close();
    });
  }
  GfDatePicker.prototype._parseValue = function () {
    var v = (this.input.value || '').trim();
    var m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (m) {
      var d = new Date(parseInt(m[3], 10), parseInt(m[1], 10) - 1, parseInt(m[2], 10));
      d.setHours(0, 0, 0, 0);
      return d;
    }
    m = v.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) {
      var d2 = new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10));
      d2.setHours(0, 0, 0, 0);
      return d2;
    }
    return null;
  };
  GfDatePicker.prototype._toDisp = function (d) {
    var mo = String(d.getMonth() + 1).padStart(2, '0');
    var da = String(d.getDate()).padStart(2, '0');
    var y = d.getFullYear();
    return mo + '/' + da + '/' + y;
  };
  GfDatePicker.prototype._toIso = function (d) {
    var mo = String(d.getMonth() + 1).padStart(2, '0');
    var da = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + mo + '-' + da;
  };
  GfDatePicker.prototype.open = function () {
    if (this.popup) return;
    var sel = this._parseValue();
    if (sel) this.viewDate = new Date(sel);
    else this.viewDate = new Date();
    this.viewDate.setDate(1);
    this.popup = document.createElement('div');
    this.popup.className = 'gf-date-popup';
    document.body.appendChild(this.popup);
    this._render();
    this._position();
    var self = this;
    setTimeout(function () {
      self.outsideHandler = function (e) {
        if (!self.popup) return;
        if (!self.popup.contains(e.target) && e.target !== self.input) self.close();
      };
      document.addEventListener('mousedown', self.outsideHandler);
    }, 0);
    this.repositionHandler = function () { self._position(); };
    window.addEventListener('resize', this.repositionHandler);
    window.addEventListener('scroll', this.repositionHandler, true);
  };
  GfDatePicker.prototype.close = function () {
    if (!this.popup) return;
    this.popup.remove();
    this.popup = null;
    if (this.outsideHandler) {
      document.removeEventListener('mousedown', this.outsideHandler);
      this.outsideHandler = null;
    }
    if (this.repositionHandler) {
      window.removeEventListener('resize', this.repositionHandler);
      window.removeEventListener('scroll', this.repositionHandler, true);
      this.repositionHandler = null;
    }
  };
  GfDatePicker.prototype._position = function () {
    if (!this.popup) return;
    var rect = this.input.getBoundingClientRect();
    var popupH = this.popup.offsetHeight || 260;
    var popupW = this.popup.offsetWidth  || 260;
    var vpH    = window.innerHeight;
    var vpW    = window.innerWidth;
    var margin = 8;

    // Vertical: prefer below, flip above when not enough room.
    var spaceBelow = vpH - rect.bottom;
    var spaceAbove = rect.top;
    var top;
    if (spaceBelow < popupH + margin && spaceAbove > popupH + margin) {
      top = rect.top + window.scrollY - popupH - 4;
    } else if (spaceBelow < popupH + margin) {
      // Neither above nor below has clean room — clamp to viewport top
      // so at least the header is visible and the calendar scrolls.
      top = window.scrollY + margin;
    } else {
      top = rect.bottom + window.scrollY + 4;
    }

    // Horizontal: prefer left-aligned to the input, clamp to viewport.
    var left = rect.left + window.scrollX;
    if (left + popupW + margin > window.scrollX + vpW) {
      left = window.scrollX + vpW - popupW - margin;
    }
    if (left < window.scrollX + margin) left = window.scrollX + margin;

    this.popup.style.left = left + 'px';
    this.popup.style.top  = top + 'px';
  };
  GfDatePicker.prototype._render = function () {
    var self = this;
    var selDate = this._parseValue();
    var year = this.viewDate.getFullYear();
    var month = this.viewDate.getMonth();
    var monthNames = ['January','February','March','April','May','June',
                      'July','August','September','October','November','December'];
    var firstDay = new Date(year, month, 1);
    var startDow = firstDay.getDay();
    var daysInMonth = new Date(year, month + 1, 0).getDate();
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var minDate = this.opts.minIso ? (function(){var m=self.opts.minIso.match(/^(\d{4})-(\d{2})-(\d{2})$/);var d=new Date(+m[1],+m[2]-1,+m[3]);d.setHours(0,0,0,0);return d;})() : null;
    var maxDate = this.opts.maxIso ? (function(){var m=self.opts.maxIso.match(/^(\d{4})-(\d{2})-(\d{2})$/);var d=new Date(+m[1],+m[2]-1,+m[3]);d.setHours(0,0,0,0);return d;})() : null;

    var cells = [];
    for (var i = 0; i < startDow; i++) cells.push('<span class="gf-date-cell empty"></span>');
    for (var d = 1; d <= daysInMonth; d++) {
      var cellDate = new Date(year, month, d);
      cellDate.setHours(0, 0, 0, 0);
      var isToday = cellDate.getTime() === today.getTime();
      var isSel = selDate && cellDate.getTime() === selDate.getTime();
      var isDisabled = (minDate && cellDate < minDate) || (maxDate && cellDate > maxDate);
      var cls = ['gf-date-cell'];
      if (isToday) cls.push('today');
      if (isSel) cls.push('selected');
      if (isDisabled) cls.push('disabled');
      cells.push('<button type="button" class="' + cls.join(' ') + '" data-day="' + d + '"' + (isDisabled ? ' disabled' : '') + '>' + d + '</button>');
    }
    this.popup.innerHTML =
      '<div class="gf-date-header">' +
        '<button type="button" class="gf-date-nav" data-dir="-1" aria-label="Previous month">&#9664;</button>' +
        '<span class="gf-date-title">' + monthNames[month] + ' ' + year + '</span>' +
        '<button type="button" class="gf-date-nav" data-dir="1" aria-label="Next month">&#9654;</button>' +
      '</div>' +
      '<div class="gf-date-grid">' +
        '<span class="gf-date-dow">S</span><span class="gf-date-dow">M</span>' +
        '<span class="gf-date-dow">T</span><span class="gf-date-dow">W</span>' +
        '<span class="gf-date-dow">T</span><span class="gf-date-dow">F</span>' +
        '<span class="gf-date-dow">S</span>' +
        cells.join('') +
      '</div>';

    this.popup.querySelectorAll('.gf-date-nav').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var dir = parseInt(btn.dataset.dir, 10);
        self.viewDate = new Date(self.viewDate.getFullYear(), self.viewDate.getMonth() + dir, 1);
        self._render();
      });
    });
    this.popup.querySelectorAll('.gf-date-cell[data-day]:not(.disabled)').forEach(function (cell) {
      cell.addEventListener('click', function (e) {
        e.stopPropagation();
        var dd = parseInt(cell.dataset.day, 10);
        var picked = new Date(year, month, dd);
        self.input.value = self._toDisp(picked);
        self.input.setAttribute('data-iso', self._toIso(picked));
        self.input.dispatchEvent(new Event('change', { bubbles: true }));
        self.close();
      });
    });
  };

  // Expose the picker + style-injector on window so the admin decline
  // modal (admin-demos.js) can reuse the exact same date + time picker
  // components without duplicating the class + CSS. `drInjectStyles`
  // is idempotent — safe to call from any consumer before creating
  // .dr-date-input / .dr-time-select elements.
  window.GfDatePicker = GfDatePicker;
  window.drInjectStyles = _injectStyles;
  // Small helpers so the admin decline modal can render identical
  // 12h ↔ 24h time selects without re-implementing.
  window.drIsoToDisp = _isoToDisp;
  window.drDispToIso = _dispToIso;
  window.drTodayIso  = _todayIso;
  window.drMaxIso    = _maxIso;

  // ─── modal state ─────────────────────────────────────────────────
  var overlay = null;
  var slotsState = [];  // [{date, bucket}]
  var errorEl = null;
  var submittingCount = 0;

  function _renderSlot(index) {
    var s = slotsState[index] || {};
    var wrap = document.createElement('div');
    wrap.className = 'dr-slot';
    wrap.setAttribute('data-slot-idx', String(index));

    var lbl = document.createElement('div');
    lbl.className = 'dr-slot-label';
    lbl.textContent = 'Preferred #' + (index + 1);
    wrap.appendChild(lbl);

    var row = document.createElement('div');
    row.className = 'dr-slot-row';

    // Custom dark date picker (see GfDatePicker below). The input is a
    // read-only text field so no native browser popover appears. We
    // display mm/dd/yyyy for the user but store the ISO yyyy-mm-dd in
    // slotsState[index].date so the backend gets what it validates.
    var initialIso = s.date || _defaultDateFor(index);
    var dt = document.createElement('input');
    dt.type = 'text';
    dt.readOnly = true;
    dt.className = 'dr-date-input';
    dt.setAttribute('inputmode', 'none');
    dt.value = _isoToDisp(initialIso);
    dt.setAttribute('data-iso', initialIso);
    dt.addEventListener('change', function () {
      // GfDatePicker writes mm/dd/yyyy; convert back to iso for state.
      var iso = _dispToIso(dt.value) || dt.getAttribute('data-iso') || '';
      dt.setAttribute('data-iso', iso);
      slotsState[index] = slotsState[index] || {};
      slotsState[index].date = iso;
    });
    row.appendChild(dt);
    // Attach the custom picker with today/max bounds.
    new GfDatePicker(dt, { minIso: _todayIso(), maxIso: _maxIso() });

    // Time picker: 12-hour clock. Hour dropdown (9 → 12 → 5 in the
    // typical business-day order), minute dropdown (00/15/30/45), and
    // AM/PM which auto-selects to match the hour. The internal state
    // stores the 24h `time` (HH:MM) since the backend validates
    // hours in 24h.
    var timeGroup = document.createElement('div');
    timeGroup.className = 'dr-time-group';

    // Hour options — 12h labels 9,10,11,12,1,2,3,4,5. Value == label so
    // the DOM state is trivial to read; conversion happens in the
    // sync function below.
    var HOUR_ORDER = ['9', '10', '11', '12', '1', '2', '3', '4', '5'];
    var hourSel = document.createElement('select');
    hourSel.className = 'dr-time-select dr-hour';
    HOUR_ORDER.forEach(function (h) {
      var opt = document.createElement('option');
      opt.value = h;
      opt.textContent = h;
      hourSel.appendChild(opt);
    });
    // Seed default: 10 AM. If we have a prior 24h value in state, map back.
    var initialHour12 = s.hour12 || '10';
    var initialAmpm   = s.ampm   || 'AM';
    if (s.hour && !s.hour12) {
      // Prior state stored 24h — reverse-map
      var _h24 = parseInt(s.hour, 10);
      if (_h24 === 0)      { initialHour12 = '12'; initialAmpm = 'AM'; }
      else if (_h24 < 12)  { initialHour12 = String(_h24); initialAmpm = 'AM'; }
      else if (_h24 === 12){ initialHour12 = '12'; initialAmpm = 'PM'; }
      else                 { initialHour12 = String(_h24 - 12); initialAmpm = 'PM'; }
    }
    hourSel.value = initialHour12;

    var colon = document.createElement('span');
    colon.className = 'dr-time-colon';
    colon.textContent = ':';

    var minSel = document.createElement('select');
    minSel.className = 'dr-time-select dr-min';
    ['00', '15', '30', '45'].forEach(function (m) {
      var mopt = document.createElement('option');
      mopt.value = m;
      mopt.textContent = m;
      minSel.appendChild(mopt);
    });
    var initialMinute = s.minute || '00';
    minSel.value = initialMinute;

    var ampmSel = document.createElement('select');
    ampmSel.className = 'dr-time-select dr-ampm';
    ['AM', 'PM'].forEach(function (ap) {
      var aopt = document.createElement('option');
      aopt.value = ap;
      aopt.textContent = ap;
      ampmSel.appendChild(aopt);
    });
    ampmSel.value = initialAmpm;
    // Not editable — AM/PM is fully determined by the hour in our
    // 9 AM – 5 PM window. Keeps a native <select> for a11y + consistent
    // rendering but user can't change it directly.
    ampmSel.disabled = true;
    ampmSel.setAttribute('aria-readonly', 'true');
    ampmSel.setAttribute('tabindex', '-1');

    // Auto-select AM/PM based on hour. Business-day rule for our
    // 9 AM – 5 PM window: hour 9/10/11 → AM; 12/1/2/3/4/5 → PM.
    // Fires whenever the hour changes. User can still manually override
    // the AM/PM after (uncommon; validated on the server if they do).
    function _autoSetAmpm() {
      var h = hourSel.value;
      ampmSel.value = (h === '9' || h === '10' || h === '11') ? 'AM' : 'PM';
    }
    if (!s.hour) _autoSetAmpm();  // only auto-adjust on fresh render

    function _to24Hour(h12, ampm) {
      var h = parseInt(h12, 10);
      if (ampm === 'AM') return h === 12 ? 0 : h;
      // PM
      return h === 12 ? 12 : h + 12;
    }

    function _updateSlotTime() {
      var h24 = _to24Hour(hourSel.value, ampmSel.value);
      slotsState[index] = slotsState[index] || {};
      // dt.value is display "mm/dd/yyyy"; the ISO form lives on data-iso.
      slotsState[index].date   = dt.getAttribute('data-iso') || _dispToIso(dt.value) || '';
      slotsState[index].hour12 = hourSel.value;
      slotsState[index].ampm   = ampmSel.value;
      slotsState[index].hour   = (h24 < 10 ? '0' + h24 : String(h24));
      slotsState[index].minute = minSel.value;
      slotsState[index].time   = slotsState[index].hour + ':' + minSel.value;
    }

    hourSel.addEventListener('change', function () {
      _autoSetAmpm();
      _updateSlotTime();
    });
    minSel.addEventListener('change',  _updateSlotTime);
    // ampmSel is disabled — no listener; it's a read-only display that
    // reflects hourSel.value via _autoSetAmpm().

    timeGroup.appendChild(hourSel);
    timeGroup.appendChild(colon);
    timeGroup.appendChild(minSel);
    timeGroup.appendChild(ampmSel);
    row.appendChild(timeGroup);

    // Seed slotsState with initial values so an unmodified slot still submits.
    // dt.value is display format (mm/dd/yyyy); state carries the ISO
    // yyyy-mm-dd (stored on the input as data-iso attr).
    var _initH24 = _to24Hour(initialHour12, ampmSel.value);
    slotsState[index] = {
      date:   dt.getAttribute('data-iso') || _dispToIso(dt.value) || initialIso,
      hour12: initialHour12,
      ampm:   ampmSel.value,
      hour:   (_initH24 < 10 ? '0' + _initH24 : String(_initH24)),
      minute: initialMinute,
      time:   (_initH24 < 10 ? '0' + _initH24 : String(_initH24)) + ':' + initialMinute,
    };

    wrap.appendChild(row);

    if (index > 0) {
      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'dr-remove-slot';
      rm.textContent = '× Remove this slot';
      rm.addEventListener('click', function () {
        slotsState.splice(index, 1);
        _rerenderSlots();
      });
      wrap.appendChild(rm);
    }

    return wrap;
  }

  function _rerenderSlots() {
    var container = overlay.querySelector('#drSlotsContainer');
    container.innerHTML = '';
    slotsState.forEach(function (_, i) {
      container.appendChild(_renderSlot(i));
    });
    var addBtn = overlay.querySelector('#drAddSlotBtn');
    addBtn.disabled = slotsState.length >= 3;
    addBtn.textContent = slotsState.length >= 3
      ? '✓ 3 slots selected — max reached'
      : '+ Add another preferred time (' + slotsState.length + '/3)';
  }

  function _showError(msg) {
    if (errorEl) errorEl.remove();
    errorEl = document.createElement('div');
    errorEl.className = 'dr-error';
    errorEl.textContent = msg;
    var body = overlay.querySelector('.dr-body');
    body.insertBefore(errorEl, body.firstChild);
    body.scrollTop = 0;
  }

  function _hideError() {
    if (errorEl) { errorEl.remove(); errorEl = null; }
  }

  function _showSuccess(message) {
    var modal = overlay.querySelector('.dr-modal');
    modal.innerHTML = '';

    var hdr = document.createElement('div');
    hdr.className = 'dr-header';
    hdr.style.padding = '20px 28px';
    hdr.innerHTML = '<button class="dr-close" onclick="window.closeDemoRequestModal()">×</button>';
    modal.appendChild(hdr);

    var wrap = document.createElement('div');
    wrap.className = 'dr-success';
    wrap.innerHTML =
      '<div class="dr-success-icon">🎉</div>' +
      '<h3>Request received</h3>' +
      '<p>' + (message || 'Thanks — a GigsFill team member will get back to you shortly.') + '</p>' +
      '<button class="dr-btn dr-btn-primary" onclick="window.closeDemoRequestModal()">Close</button>';
    modal.appendChild(wrap);
  }

  async function _submit() {
    _hideError();
    var first = (overlay.querySelector('#drFirstName').value || '').trim();
    var last  = (overlay.querySelector('#drLastName').value  || '').trim();
    var name  = (first + ' ' + last).trim();
    var email = (overlay.querySelector('#drEmail').value || '').trim();
    var phone = (overlay.querySelector('#drPhone').value || '').trim();
    var entType = overlay.querySelector('#drEntityType').value || '';
    var entName = (overlay.querySelector('#drEntityName').value || '').trim();
    var city  = (overlay.querySelector('#drCity').value  || '').trim();
    var state = (overlay.querySelector('#drState').value || '').trim().toUpperCase();
    var notes = (overlay.querySelector('#drNotes').value || '').trim();
    var hp    = (overlay.querySelector('#drHp').value    || '').trim();

    if (!first) return _showError('Please enter your first name.');
    if (!last)  return _showError('Please enter your last name.');
    if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email))
      return _showError('Please enter a valid email address.');
    if (!entType)
      return _showError('Please tell us if you\'re a venue, artist, or other.');
    if (!slotsState.length)
      return _showError('Please pick at least one preferred time slot.');

    var payload = {
      name: name,
      first_name: first,
      last_name: last,
      email: email, phone: phone,
      entity_type: entType, entity_name: entName,
      city: city, state: state, notes: notes,
      _hp: hp,
      preferred_slots: slotsState.filter(function (s) { return s && s.date && s.time; }),
    };

    var submitBtn = overlay.querySelector('#drSubmitBtn');
    var origLabel = submitBtn.textContent;
    submittingCount++;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Sending…';

    // Reschedule mode swaps endpoint + button label but the payload
    // is a subset (the server ignores email/entity_type on reschedule
    // to keep identity stable — it only accepts new slots/notes/phone).
    var url = _rescheduleToken
      ? ('/api/demo-request/reschedule/' + encodeURIComponent(_rescheduleToken))
      : '/api/demo-request';

    try {
      var res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        credentials: 'omit',
      });
      var body = null;
      try { body = await res.json(); } catch (_) {}
      if (!res.ok) {
        var msg = (body && body.detail) || 'Something went wrong. Please try again.';
        _showError(String(msg));
        submitBtn.disabled = false;
        submitBtn.textContent = origLabel;
        return;
      }
      _showSuccess((body && body.message) || null);
    } catch (e) {
      _showError('Network error — please try again.');
      submitBtn.disabled = false;
      submitBtn.textContent = origLabel;
    } finally {
      submittingCount--;
    }
  }

  window.showDemoRequestModal = function (opts) {
    opts = opts || {};
    _injectStyles();
    if (overlay) return;

    var isReschedule = !!(opts.rescheduleToken || _rescheduleToken);
    if (opts.rescheduleToken) _rescheduleToken = opts.rescheduleToken;

    var headerTitle = isReschedule
      ? 'Pick new times for your demo'
      : 'Request a live demo';
    var headerBody = isReschedule
      ? ('Sorry the first slot didn\'t stick — pick a few new times below and we\'ll confirm one shortly.'
         + (_priorSlotHuman
              ? (' <span style="opacity:0.85;">(Old slot: <strong>' + _priorSlotHuman + '</strong> — released.)</span>')
              : ''))
      : 'Give us a few times that work in the coming weeks and we\'ll get back to you shortly.';

    slotsState = [];  // will be seeded by _renderSlot

    overlay = document.createElement('div');
    overlay.className = 'dr-overlay';
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) window.closeDemoRequestModal();
    });

    overlay.innerHTML =
      '<div class="dr-modal" role="dialog" aria-labelledby="drTitle">' +
        '<div class="dr-header">' +
          '<button class="dr-close" onclick="window.closeDemoRequestModal()">×</button>' +
          '<h2 id="drTitle">' + headerTitle + '</h2>' +
          '<p>' + headerBody + '</p>' +
        '</div>' +
        '<div class="dr-body">' +
          '<div class="dr-grid-2">' +
            '<div class="dr-field">' +
              '<label class="dr-req">First name</label>' +
              '<input type="text" id="drFirstName" placeholder="First" autocomplete="given-name" maxlength="60">' +
            '</div>' +
            '<div class="dr-field">' +
              '<label class="dr-req">Last name</label>' +
              '<input type="text" id="drLastName" placeholder="Last" autocomplete="family-name" maxlength="60">' +
            '</div>' +
          '</div>' +
          '<div class="dr-grid-2">' +
            '<div class="dr-field">' +
              '<label class="dr-req">Email</label>' +
              '<input type="email" id="drEmail" placeholder="you@email.com" autocomplete="email" maxlength="200">' +
            '</div>' +
            '<div class="dr-field">' +
              '<label>Phone</label>' +
              '<input type="tel" id="drPhone" placeholder="(555) 123-4567" autocomplete="tel" inputmode="tel" maxlength="14">' +
            '</div>' +
          '</div>' +
          '<div class="dr-grid-2">' +
            '<div class="dr-field">' +
              '<label class="dr-req">I\'m a…</label>' +
              '<select id="drEntityType">' +
                '<option value="">Choose one</option>' +
                '<option value="venue">Venue</option>' +
                '<option value="artist">Artist</option>' +
                '<option value="other">Other</option>' +
              '</select>' +
            '</div>' +
            '<div class="dr-field">' +
              '<label>Venue / Artist name</label>' +
              '<input type="text" id="drEntityName" placeholder="14 Cannons" maxlength="200">' +
            '</div>' +
          '</div>' +
          '<div class="dr-grid-2">' +
            '<div class="dr-field">' +
              '<label>City</label>' +
              '<input type="text" id="drCity" placeholder="Nashville" maxlength="100">' +
            '</div>' +
            '<div class="dr-field">' +
              '<label>State</label>' +
              '<input type="text" id="drState" placeholder="TN" maxlength="2" style="text-transform:uppercase;">' +
            '</div>' +
          '</div>' +
          '<div class="dr-field">' +
            '<label>What are you hoping to see?</label>' +
            '<textarea id="drNotes" placeholder="e.g. I book weekly live music at my brewery and want to see how the booking flow works" maxlength="2000"></textarea>' +
          '</div>' +
          '<label class="dr-req" style="display:block;margin:8px 0 4px;">Choose a start time for a 30-60 minute demo presentation</label>' +
          '<div style="font-size:11px;color:#94a3b8;margin:0 0 12px;text-transform:none;letter-spacing:0;font-weight:400;">Pick 1-3 options. Times shown in Pacific.</div>' +
          '<div id="drSlotsContainer"></div>' +
          '<button type="button" class="dr-add-slot" id="drAddSlotBtn">+ Add another preferred time (1/3)</button>' +
          // Honeypot — bots fill visible-looking fields; humans don\'t see this
          '<input type="text" id="drHp" class="dr-hp" tabindex="-1" autocomplete="off">' +
        '</div>' +
        '<div class="dr-footer">' +
          '<button type="button" class="dr-btn dr-btn-ghost" onclick="window.closeDemoRequestModal()">Cancel</button>' +
          '<button type="button" class="dr-btn dr-btn-primary" id="drSubmitBtn">' +
            (isReschedule ? 'Send new times' : 'Request Demo') +
          '</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);
    setTimeout(function () { overlay.classList.add('dr-open'); }, 10);

    // Seed with one slot
    slotsState = [{}];
    _rerenderSlots();

    // Prefill from the reschedule-context response. Email + entity
    // are locked so identity stays stable across rounds.
    if (isReschedule && _reschedulePrefill) {
      try {
        var pf = _reschedulePrefill;
        var q = function (id) { return overlay.querySelector(id); };
        if (pf.first_name) q('#drFirstName').value = pf.first_name;
        if (pf.last_name)  q('#drLastName').value  = pf.last_name;
        if (!pf.first_name && !pf.last_name && pf.name) {
          var parts = String(pf.name).trim().split(/\s+/);
          q('#drFirstName').value = parts[0] || '';
          q('#drLastName').value  = parts.slice(1).join(' ') || '';
        }
        if (pf.email) {
          q('#drEmail').value = pf.email;
          q('#drEmail').readOnly = true;
          q('#drEmail').style.opacity = '0.7';
        }
        if (pf.phone)       q('#drPhone').value      = pf.phone;
        if (pf.entity_type) {
          q('#drEntityType').value = pf.entity_type;
          q('#drEntityType').disabled = true;
          q('#drEntityType').style.opacity = '0.7';
        }
        if (pf.entity_name) q('#drEntityName').value = pf.entity_name;
        if (pf.city)        q('#drCity').value       = pf.city;
        if (pf.state)       q('#drState').value      = pf.state;
        if (pf.notes)       q('#drNotes').value      = pf.notes;
      } catch (_) {}
    }

    overlay.querySelector('#drAddSlotBtn').addEventListener('click', function () {
      if (slotsState.length >= 3) return;
      slotsState.push({});
      _rerenderSlots();
    });
    overlay.querySelector('#drSubmitBtn').addEventListener('click', _submit);
    // Focus the first empty field. Reschedule mode → jump to first slot's date.
    var focusTarget = isReschedule
      ? overlay.querySelector('.dr-date-input')
      : overlay.querySelector('#drFirstName');
    if (focusTarget) try { focusTarget.focus(); } catch (_) {}

    // City→State autofill via the shared helper. When user types a
    // known city, dropdown appears; picking or blurring on an exact
    // match populates the State field automatically.
    if (window.initCityAutocomplete) {
      try {
        window.initCityAutocomplete({ inputId: 'drCity', stateId: 'drState' });
      } catch (_) {}
    }

    // Phone auto-format: cap at 10 digits, render as (XXX) XXX-XXXX.
    // Runs on every keystroke + on paste. Cursor stays parked at the
    // end for a clean typing experience — reformatting doesn\'t bounce
    // the caret around.
    var phoneEl = overlay.querySelector('#drPhone');
    if (phoneEl) {
      phoneEl.addEventListener('input', function () {
        var digits = phoneEl.value.replace(/\D/g, '').slice(0, 10);
        var out = '';
        if (digits.length === 0) {
          out = '';
        } else if (digits.length <= 3) {
          out = '(' + digits;
        } else if (digits.length <= 6) {
          out = '(' + digits.slice(0, 3) + ') ' + digits.slice(3);
        } else {
          out = '(' + digits.slice(0, 3) + ') ' + digits.slice(3, 6) + '-' + digits.slice(6);
        }
        if (phoneEl.value !== out) {
          phoneEl.value = out;
          // Park cursor at end after each format pass
          try { phoneEl.setSelectionRange(out.length, out.length); } catch (_) {}
        }
      });
    }
  };

  window.closeDemoRequestModal = function () {
    if (!overlay) return;
    overlay.classList.remove('dr-open');
    setTimeout(function () {
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
      overlay = null;
      errorEl = null;
      slotsState = [];
      // Drop reschedule context so a subsequent manual open of the
      // modal on the same page-load doesn't accidentally reuse it.
      _rescheduleToken = null;
      _reschedulePrefill = null;
      _priorSlotHuman = null;
    }, 180);
  };

  // Auto-open in reschedule mode when the URL carries a ?reschedule=<tok>.
  // The token was minted by the backend, embedded in the prospect's
  // confirmation email, and points at their existing demo_requests row.
  // We validate + fetch prefill BEFORE opening the modal so a bad or
  // expired token surfaces a branded error instead of a blank form.
  document.addEventListener('DOMContentLoaded', function () {
    try {
      // Only fire on the homepage (has the Request-a-Live-Demo button).
      // demo-request-modal.js is now also loaded on admin.html so
      // GfDatePicker can be reused by the decline modal — the auto-open
      // path must not run there.
      if (!document.getElementById('requestDemoBtn')) return;
      var params = new URLSearchParams(window.location.search);
      var tok = params.get('reschedule');
      if (!tok) return;
      // Strip the token from the URL so a page reload doesn't retrigger,
      // and history entries don't leak the signed token around.
      try {
        var url = new URL(window.location.href);
        url.searchParams.delete('reschedule');
        window.history.replaceState({}, '', url.toString());
      } catch (_) {}
      fetch('/api/demo-request/reschedule/' + encodeURIComponent(tok), {
        method: 'GET', credentials: 'omit',
      }).then(function (res) {
        return res.json().then(function (body) { return { ok: res.ok, body: body }; });
      }).then(function (r) {
        if (!r.ok) {
          var detail = (r.body && r.body.detail) || 'This reschedule link is no longer valid.';
          if (window.showModal) {
            window.showModal('Reschedule link expired', String(detail),
              [{ text: 'OK', primary: true }]);
          } else {
            alert(detail);
          }
          return;
        }
        _reschedulePrefill = (r.body && r.body.prefill) || null;
        _priorSlotHuman = (r.body && r.body.prior_slot_human) || null;
        window.showDemoRequestModal({ rescheduleToken: tok });
      }).catch(function () {
        if (window.showModal) {
          window.showModal('Reschedule link', 'Could not load your request. Please try again in a moment.',
            [{ text: 'OK', primary: true }]);
        }
      });
    } catch (_) {}
  });
})();
