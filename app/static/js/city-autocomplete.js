/**
 * City Autocomplete — shared module for GigsFill
 * Dropdown appended to document.body with fixed positioning.
 * Includes page-blocking overlay when city is invalid.
 */
(function () {
  "use strict";

  let _cities = null;
  let _loading = null;
  let _blockOverlay = null;
  let _blockedInput = null;
  let _cityInvalid = false;

  function loadCities() {
    if (_cities) return Promise.resolve(_cities);
    if (_loading) return _loading;
    _loading = fetch("/api/cities/all")
      .then(function(r) { return r.ok ? r.json() : []; })
      .then(function(d) { _cities = d; return d; })
      .catch(function() { _cities = []; return []; });
    return _loading;
  }

  loadCities();

  function search(query, limit) {
    limit = limit || 8;
    if (!_cities || query.length < 1) return [];
    var q = query.toLowerCase().trim();
    var starts = [], contains = [];
    for (var i = 0; i < _cities.length; i++) {
      var n = _cities[i].city.toLowerCase();
      if (n.startsWith(q)) starts.push(_cities[i]);
      else if (n.includes(q)) contains.push(_cities[i]);
      if (starts.length + contains.length >= limit * 3) break;
    }
    return starts.concat(contains).slice(0, limit);
  }

  window.validateCityName = async function(cityName, stateCode) {
    var cities = await loadCities();
    if (!cities || !cityName) return null;
    var cn = cityName.trim().toLowerCase();
    var sc = stateCode ? stateCode.trim().toUpperCase() : null;
    for (var i = 0; i < cities.length; i++) {
      if (cities[i].city.toLowerCase() === cn) {
        if (!sc || cities[i].state === sc) return cities[i];
      }
    }
    return null;
  };

  // ─── PAGE BLOCKING OVERLAY ───
  function showBlockOverlay(input) {
    if (_blockOverlay) return;
    _blockedInput = input;
    _cityInvalid = true;

    _blockOverlay = document.createElement('div');
    _blockOverlay.id = 'cityBlockOverlay';
    _blockOverlay.style.cssText =
      'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9998;';

    var modalBox = document.createElement('div');
    modalBox.className = 'cityBlockModal';
    modalBox.style.cssText =
      'position:fixed;background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);' +
      'border:2px solid #ef4444;border-radius:12px;padding:20px 28px;max-width:400px;width:90%;' +
      'box-shadow:0 20px 60px rgba(0,0,0,0.6);text-align:center;z-index:9999;';
    modalBox.innerHTML =
      '<div style="font-size:1.6rem;margin-bottom:8px;">⚠️</div>' +
      '<h3 style="margin:0 0 6px;color:#ef4444;font-size:1rem;">Invalid City</h3>' +
      '<p style="margin:0;color:#9ca3af;font-size:0.82rem;line-height:1.4;">' +
      'This city is either misspelled or too small for our system.<br>' +
      'Please fix the city field to continue.</p>';

    _blockOverlay.addEventListener('mousedown', function(e) {
      e.preventDefault();
      e.stopPropagation();
      if (input) input.focus();
    });

    document.body.appendChild(_blockOverlay);
    document.body.appendChild(modalBox);

    // Position modal below the city field
    function positionModal() {
      var rect = input.getBoundingClientRect();
      var top = rect.bottom + 8;
      var left = rect.left + (rect.width / 2) - 200;
      if (left < 10) left = 10;
      if (top + 180 > window.innerHeight) top = rect.top - 180;
      modalBox.style.top = top + 'px';
      modalBox.style.left = left + 'px';
    }
    positionModal();
    _blockOverlay._positionModal = positionModal;
    _blockOverlay._modalBox = modalBox;
    window.addEventListener('resize', positionModal);
    window.addEventListener('scroll', positionModal, true);

    // Raise city field + its parent above overlay
    var parent = input.closest('.field') || input.parentElement;
    if (parent) { parent.style.position = 'relative'; parent.style.zIndex = '9999'; }
    input.style.position = 'relative';
    input.style.zIndex = '9999';
    input.style.border = '2px solid #ef4444';
    input.style.background = '#1a1f2e';
    input.style.boxShadow = '0 0 12px rgba(239,68,68,0.4)';

    // Also raise state select above overlay
    var stateEl = input._cityStateEl;
    if (stateEl) {
      var sp = stateEl.closest('.field') || stateEl.parentElement;
      if (sp) { sp.style.position = 'relative'; sp.style.zIndex = '9999'; }
      stateEl.style.position = 'relative';
      stateEl.style.zIndex = '9999';
      stateEl.style.border = '2px solid #ef4444';
      stateEl.style.background = '#1a1f2e';
    }

    // Block navigation
    window.addEventListener('beforeunload', blockBeforeUnload);
    document.addEventListener('click', blockClicks, true);

    setTimeout(function() { input.focus(); }, 80);
  }

  function removeBlockOverlay() {
    _cityInvalid = false;
    if (_blockOverlay) {
      if (_blockOverlay._modalBox) _blockOverlay._modalBox.remove();
      if (_blockOverlay._positionModal) {
        window.removeEventListener('resize', _blockOverlay._positionModal);
        window.removeEventListener('scroll', _blockOverlay._positionModal, true);
      }
      _blockOverlay.remove();
      _blockOverlay = null;
    }
    if (_blockedInput) {
      _blockedInput.style.position = '';
      _blockedInput.style.zIndex = '';
      _blockedInput.style.border = '';
      _blockedInput.style.background = '';
      _blockedInput.style.boxShadow = '';
      var parent = _blockedInput.closest('.field') || _blockedInput.parentElement;
      if (parent) { parent.style.position = ''; parent.style.zIndex = ''; }
      var stateEl = _blockedInput._cityStateEl;
      if (stateEl) {
        stateEl.style.position = '';
        stateEl.style.zIndex = '';
        stateEl.style.border = '';
        stateEl.style.background = '';
        var sp = stateEl.closest('.field') || stateEl.parentElement;
        if (sp) { sp.style.position = ''; sp.style.zIndex = ''; }
      }
      _blockedInput = null;
    }
    window.removeEventListener('beforeunload', blockBeforeUnload);
    document.removeEventListener('click', blockClicks, true);
  }

  function blockBeforeUnload(e) {
    if (_cityInvalid) { e.preventDefault(); e.returnValue = ''; return ''; }
  }

  function blockClicks(e) {
    if (!_cityInvalid) return;
    var t = e.target;
    // Allow city input
    if (_blockedInput && (_blockedInput === t || _blockedInput.contains(t))) return;
    // Allow state select
    if (_blockedInput && _blockedInput._cityStateEl) {
      var se = _blockedInput._cityStateEl;
      if (se === t || se.contains(t)) return;
    }
    // Allow autocomplete dropdown items
    if (t.closest && t.closest('[data-i]')) return;
    // Block
    e.preventDefault();
    e.stopPropagation();
    if (_blockedInput) _blockedInput.focus();
  }

  window.isCityBlocked = function() { return _cityInvalid; };

  // ─── SHOW / CLEAR ERROR ───
  window.showCityError = function(input, show) {
    // Remove any old inline error text (cleanup from previous versions)
    var errEl = input.parentElement.querySelector('.city-validation-error');
    if (errEl) errEl.remove();
    if (show) {
      input.style.border = '2px solid #ef4444';
      input.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, 0.2)';
      showBlockOverlay(input);
    } else {
      input.style.border = '';
      input.style.boxShadow = '';
      removeBlockOverlay();
    }
  };

  // ─── VALIDATION ON BLUR ───
  window.attachCityValidation = function(inputId, stateId) {
    var input = document.getElementById(inputId);
    if (!input) return;
    if (stateId) input._cityStateEl = document.getElementById(stateId);

    input.addEventListener('blur', async function() {
      // Longer delay so autocomplete pick() fires first across all browsers
      var self = this;
      await new Promise(function(r) { setTimeout(r, 200); });

      // If pointer is inside dropdown or pick is in progress, skip validation
      if (self._ddPointerIn && self._ddPointerIn()) return;
      // If a pick just happened (within 300ms), skip validation
      if (self._lastPickTime && (Date.now() - self._lastPickTime) < 300) return;

      var val = self.value.trim();
      if (!val) { window.showCityError(self, false); return; }

      var stateEl = stateId ? document.getElementById(stateId) : null;
      var stateVal = stateEl ? stateEl.value : '';
      try {
        var r1 = await fetch('/api/validate-city?city=' + encodeURIComponent(val) + (stateVal ? '&state=' + encodeURIComponent(stateVal) : '') + '&_t=' + Date.now());
        var d = await r1.json();
        if (!d.valid && stateVal) {
          var r2 = await fetch('/api/validate-city?city=' + encodeURIComponent(val) + '&_t=' + Date.now());
          d = await r2.json();
        }
        if (d.valid) {
          window.showCityError(self, false);
          if (d.state && stateEl && stateEl.value !== d.state) {
            stateEl.value = d.state;
            stateEl.dispatchEvent(new Event('change', { bubbles: true }));
          }
        } else {
          window.showCityError(self, true);
        }
      } catch(e) {}
    });

    // Live re-validate while blocked so overlay lifts as soon as city becomes valid
    input.addEventListener('input', function() {
      var self = this;
      if (!_cityInvalid || _blockedInput !== self) return;
      clearTimeout(self._revalTimer);
      self._revalTimer = setTimeout(async function() {
        var val = self.value.trim();
        if (!val) return;
        var stateEl = stateId ? document.getElementById(stateId) : null;
        var stateVal = stateEl ? stateEl.value : '';
        try {
          var r1 = await fetch('/api/validate-city?city=' + encodeURIComponent(val) + (stateVal ? '&state=' + encodeURIComponent(stateVal) : '') + '&_t=' + Date.now());
          var d = await r1.json();
          if (!d.valid && stateVal) {
            var r2 = await fetch('/api/validate-city?city=' + encodeURIComponent(val) + '&_t=' + Date.now());
            d = await r2.json();
          }
          if (d.valid) {
            window.showCityError(self, false);
            if (d.state && stateEl && stateEl.value !== d.state) {
              stateEl.value = d.state;
              stateEl.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }
        } catch(e) {}
      }, 500);
    });
  };

  // ─── AUTOCOMPLETE DROPDOWN ───
  window.initCityAutocomplete = function (opts) {
    var input = document.getElementById(opts.inputId);
    if (!input) return;
    if (opts.stateId) input._cityStateEl = document.getElementById(opts.stateId);

    // Defeat Chrome/Edge native autofill which ignores autocomplete="off"
    // for fields with ids like "city", "address", "name", etc.
    // Random string ensures no browser can match it to an autofill category
    input.setAttribute("autocomplete", "gf-no-autofill-" + Math.random().toString(36).slice(2, 6));
    input.setAttribute("data-lpignore", "true");       // LastPass
    input.setAttribute("data-1p-ignore", "true");      // 1Password
    input.setAttribute("data-form-type", "other");     // Dashlane
    // Chrome also checks the name attribute
    if (!input.getAttribute("name") || input.getAttribute("name") === "city") {
      input.setAttribute("name", "gf-locality-" + Math.random().toString(36).slice(2, 6));
    }

    var dd = document.createElement("div");
    dd.style.cssText =
      "position:fixed;background:rgba(15,20,32,0.98);" +
      "border:1px solid rgba(255,255,255,0.12);" +
      "border-radius:8px;max-height:220px;overflow-y:auto;z-index:99999;" +
      "display:none;box-shadow:0 8px 24px rgba(0,0,0,0.4);";
    document.body.appendChild(dd);

    var matches = [], idx = -1, picking = false, pointerInDD = false;
    // Expose pointer state on input for blur handler
    input._ddPointerIn = function() { return pointerInDD || picking; };

    function positionDD() {
      var r = input.getBoundingClientRect();
      dd.style.top = (r.bottom + 2) + "px";
      dd.style.left = r.left + "px";
      dd.style.width = Math.max(r.width, 220) + "px";
    }

    function render() {
      dd.innerHTML = "";
      matches.forEach(function(c, i) {
        var div = document.createElement("div");
        div.setAttribute("data-i", i);
        div.style.cssText = 'padding:9px 12px;cursor:pointer;font-size:0.88rem;' +
          'border-bottom:1px solid rgba(255,255,255,0.06);' +
          'background:' + (i === idx ? 'rgba(6,182,212,0.18)' : 'transparent') + ';' +
          'color:' + (i === idx ? '#06b6d4' : '#e2e8f0') + ';transition:background 0.1s;' +
          'user-select:none;-webkit-user-select:none;';
        div.innerHTML = c.city + ', <span style="opacity:0.55;pointer-events:none;">' + c.state + '</span>';
        // Direct handlers on each item — most reliable cross-browser
        div.onclick = function(e) { e.stopPropagation(); pick(i); };
        div.onmouseover = function() {
          // Update styles in-place without re-rendering DOM
          var prev = dd.querySelector('[data-i="' + idx + '"]');
          if (prev) { prev.style.background = 'transparent'; prev.style.color = '#e2e8f0'; }
          idx = i;
          div.style.background = 'rgba(6,182,212,0.18)';
          div.style.color = '#06b6d4';
        };
        dd.appendChild(div);
      });
    }

    function pick(i) {
      var c = matches[i];
      if (!c) return;
      picking = true;
      input._lastPickTime = Date.now();
      input.value = c.city;
      window.showCityError(input, false);  // clears error + removes overlay
      if (opts.stateId) {
        var sel = document.getElementById(opts.stateId);
        if (sel) {
          sel.value = c.state;
          sel.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      dd.style.display = "none";
      matches = []; idx = -1;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      if (typeof opts.onSelect === "function") opts.onSelect(c.city, c.state, c);
      // Keep focus on input briefly to prevent immediate blur validation
      try { input.focus(); } catch(e) {}
      setTimeout(function() { picking = false; }, 200);
    }

    input.addEventListener("input", function () {
      if (picking) return;
      // Skip if just picked (Chrome can fire input after programmatic value change)
      if (this._lastPickTime && (Date.now() - this._lastPickTime) < 300) return;
      var v = this.value.trim();
      if (v.length < 2) { dd.style.display = "none"; matches = []; idx = -1; return; }
      matches = search(v);
      idx = -1;
      if (!matches.length) { dd.style.display = "none"; return; }
      positionDD();
      render();
      dd.style.display = "block";
    });

    // Use capture phase to intercept before Chrome autofill can steal events
    input.addEventListener("keydown", function (e) {
      if (dd.style.display === "none" || !matches.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault(); e.stopPropagation();
        idx = (idx + 1) % matches.length; render();
        var el = dd.querySelector('[data-i="' + idx + '"]');
        if (el) el.scrollIntoView({ block: "nearest" });
      } else if (e.key === "ArrowUp") {
        e.preventDefault(); e.stopPropagation();
        idx = idx <= 0 ? matches.length - 1 : idx - 1; render();
        var el2 = dd.querySelector('[data-i="' + idx + '"]');
        if (el2) el2.scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter") {
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
        if (idx >= 0) {
          pick(idx);
        } else if (matches.length > 0) {
          // Auto-pick first match on Enter even if none highlighted
          pick(0);
        }
      } else if (e.key === "Escape") {
        dd.style.display = "none";
      } else if (e.key === "Tab" && idx >= 0) {
        pick(idx); dd.style.display = "none";
      }
    }, true);

    // Track pointer inside dropdown to prevent blur race condition
    dd.addEventListener("pointerenter", function() { pointerInDD = true; });
    dd.addEventListener("pointerleave", function() { pointerInDD = false; });
    dd.addEventListener("mouseenter", function() { pointerInDD = true; });
    dd.addEventListener("mouseleave", function() { pointerInDD = false; });

    // Prevent blur when clicking anywhere in dropdown container
    // IMPORTANT: Only mousedown.preventDefault() prevents blur.
    // Do NOT preventDefault on pointerdown — Chromium suppresses click events after that.
    dd.addEventListener("mousedown", function(e) {
      e.preventDefault();
      pointerInDD = true;
      picking = true;
    });
    dd.addEventListener("pointerdown", function() {
      // Just set flags — do NOT preventDefault here (breaks click in Chrome/Edge)
      pointerInDD = true;
      picking = true;
    });
    // Touch support for mobile
    dd.addEventListener("touchstart", function() {
      pointerInDD = true;
      picking = true;
    }, { passive: true });
    dd.addEventListener("touchend", function(e) {
      var el = e.target.closest("[data-i]");
      if (el) { e.preventDefault(); pick(parseInt(el.dataset.i)); }
    });
    document.addEventListener("click", function (e) {
      if (!input.contains(e.target) && !dd.contains(e.target)) dd.style.display = "none";
    });
    window.addEventListener("scroll", function() { if (dd.style.display !== "none") positionDD(); }, true);
    window.addEventListener("resize", function() { if (dd.style.display !== "none") positionDD(); });

    if (opts.validate !== false) {
      window.attachCityValidation(opts.inputId, opts.stateId);
    }
  };
})();
