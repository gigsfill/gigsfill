// ─────────────────────────────────────────────────────────────────────
// gf-tooltip.js — site-wide custom tooltip.
//
// Drop this script onto any page and:
//   • Elements with data-tooltip="…" get a styled dark/purple tooltip
//     matching the site aesthetic (modal card bg, modal font, soft shadow).
//   • Elements with the legacy title="…" attribute are auto-promoted —
//     the title is stashed, the attribute is stripped while the tooltip
//     is shown (so the browser doesn't draw its own white tooltip), and
//     restored on mouseout. No markup changes required across the
//     codebase to get consistent styling.
//
// CSS is injected once via <style> on first load — single-file include.
// ─────────────────────────────────────────────────────────────────────
(function initGfTooltips() {
  if (window.__gfTooltipsInit) return;
  window.__gfTooltipsInit = true;

  // ── inject CSS once ──────────────────────────────────────────────────
  if (!document.getElementById('gf-tooltip-styles')) {
    const style = document.createElement('style');
    style.id = 'gf-tooltip-styles';
    style.textContent = `
      .gf-tooltip {
        position: absolute;
        background: #1a1a2e;
        border: 1px solid rgba(124, 107, 255, 0.45);
        border-radius: 6px;
        padding: 7px 11px;
        font-family: var(--font-primary, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif);
        font-size: 0.74rem;
        font-weight: 400;
        color: var(--text, #e5e7eb);
        line-height: 1.45;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
        z-index: 100001;
        max-width: 640px;
        white-space: pre;
        pointer-events: none;
        opacity: 0;
        transform: translateY(-3px);
        transition: opacity 0.14s ease, transform 0.14s ease;
      }
      .gf-tooltip.gf-tooltip-wrap { white-space: pre-line; max-width: 320px; }
      .gf-tooltip.gf-tooltip-visible { opacity: 1; transform: translateY(0); }
      .gf-tooltip::before {
        content: '';
        position: absolute;
        top: -5px;
        left: 50%;
        transform: translateX(-50%) rotate(45deg);
        width: 8px;
        height: 8px;
        background: #1a1a2e;
        border-left: 1px solid rgba(124, 107, 255, 0.45);
        border-top: 1px solid rgba(124, 107, 255, 0.45);
      }
      /* 2026-09-16: cursor-anchored arrow — when show() computes an
         offset it sets --gf-arrow-left, and this class kicks in. */
      .gf-tooltip.gf-tooltip-arrow-custom::before {
        left: var(--gf-arrow-left, 50%);
      }
      .gf-tooltip.gf-tooltip-above::before {
        top: auto;
        bottom: -5px;
        border-left: 0;
        border-top: 0;
        border-right: 1px solid rgba(124, 107, 255, 0.45);
        border-bottom: 1px solid rgba(124, 107, 255, 0.45);
      }
    `;
    document.head.appendChild(style);
  }

  let activeTip = null;
  let activeTarget = null;

  function hide() {
    if (activeTip) { activeTip.remove(); activeTip = null; }
    // Restore any title we stashed while showing the custom tooltip.
    if (activeTarget && activeTarget.dataset.gfOrigTitle != null) {
      activeTarget.setAttribute('title', activeTarget.dataset.gfOrigTitle);
      delete activeTarget.dataset.gfOrigTitle;
    }
    activeTarget = null;
  }

  // 2026-09-16: position tooltips near the cursor for wide triggers.
  // Previously the tooltip always centered under the trigger's bounding
  // box — fine for tiny icons but terrible for full-width paragraphs
  // and long email-center rows, where the tooltip could appear hundreds
  // of pixels away from wherever the cursor actually was. Now we take
  // the pointer's pageX from the hover event and center the tooltip on
  // it, clamped inside the trigger's horizontal bounds so a narrow
  // trigger still looks centered.
  function show(target, cursorX, cursorY) {
    hide();
    // Prefer data-tooltip; fall back to title (auto-migration).
    let text = target.getAttribute('data-tooltip');
    if (!text && target.hasAttribute('title')) {
      text = target.getAttribute('title');
      // Stash and strip so the browser doesn't render its own native tooltip
      // alongside ours. Restored on hide().
      target.dataset.gfOrigTitle = text;
      target.removeAttribute('title');
    }
    if (!text) return;

    const tip = document.createElement('div');
    tip.className = 'gf-tooltip';
    // Auto-wrap mode for very long single-line text
    if (text.length > 80 && !text.includes('\n')) tip.classList.add('gf-tooltip-wrap');
    tip.textContent = text;
    document.body.appendChild(tip);

    const rect = target.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;

    // Vertical: below the trigger by default, flip above when no room.
    // Keep it anchored to the trigger's edge (not the cursor Y) so the
    // tooltip doesn't jitter as the mouse moves within the trigger.
    let top = rect.bottom + window.scrollY + 8;
    if (rect.bottom + tipRect.height + 16 > vh) {
      top = rect.top + window.scrollY - tipRect.height - 8;
      tip.classList.add('gf-tooltip-above');
    }

    // Horizontal: center on the cursor when we have one (mouse / touch),
    // fall back to the trigger's midpoint (keyboard focus). Clamp inside
    // the trigger so a narrow icon still gets a centered tooltip, and
    // clamp inside the viewport so we don't overflow the edge.
    const _elCenter = rect.left + window.scrollX + (rect.width / 2);
    const _anchorX = (typeof cursorX === 'number')
      ? cursorX + window.scrollX
      : _elCenter;
    const _tw = tipRect.width;
    let left = _anchorX - _tw / 2;
    const _elLeft  = rect.left + window.scrollX;
    const _elRight = rect.right + window.scrollX;
    // Only clamp inside the trigger when the trigger is wider than the
    // tooltip (so a tiny icon doesn't force the tip to overhang it).
    if (rect.width > _tw + 8) {
      left = Math.max(_elLeft, Math.min(left, _elRight - _tw));
    }
    // Viewport clamp — always applies.
    left = Math.max(8, Math.min(left, vw + window.scrollX - _tw - 8));

    tip.style.top = top + 'px';
    tip.style.left = left + 'px';

    // Reposition the CSS arrow to point at the cursor (or element center
    // for keyboard focus). Falls back to the default 50% if we can't
    // compute a sensible offset.
    const _arrowX = _anchorX - left;
    if (_arrowX >= 6 && _arrowX <= _tw - 6) {
      // Inline style overrides the 50% default in the injected CSS.
      const _arrows = tip.querySelectorAll(':scope::before');
      // ::before pseudo-elements can't be reached via querySelectorAll,
      // so set a custom property the stylesheet reads.
      tip.style.setProperty('--gf-arrow-left', _arrowX + 'px');
      tip.classList.add('gf-tooltip-arrow-custom');
    }

    requestAnimationFrame(() => tip.classList.add('gf-tooltip-visible'));

    activeTip = tip;
    activeTarget = target;
  }

  // Delegated mouseover / mouseout so it covers elements added dynamically.
  document.addEventListener('mouseover', (e) => {
    const t = e.target.closest('[data-tooltip], [title]');
    if (!t) return;
    if (t === activeTarget) return;
    show(t, e.clientX, e.clientY);
  });
  document.addEventListener('mouseout', (e) => {
    if (!activeTarget) return;
    // Only hide when leaving the trigger entirely.
    if (!activeTarget.contains(e.relatedTarget)) hide();
  });

  // A11y + mobile: also surface tooltips when the trigger is keyboard-
  // focused (so screen-reader / keyboard users get the contextual help
  // their mouse-only counterparts see) and on first touch (so phone
  // users can tap to read, since hover doesn't exist on touch). A
  // second tap or any outside tap dismisses.
  document.addEventListener('focusin', (e) => {
    const t = e.target.closest('[data-tooltip], [title]');
    if (!t) return;
    if (t === activeTarget) return;
    // Keyboard focus — no cursor position; show() falls back to element center.
    show(t);
  });
  document.addEventListener('focusout', (e) => {
    if (!activeTarget) return;
    if (!activeTarget.contains(e.relatedTarget)) hide();
  });
  document.addEventListener('touchstart', (e) => {
    const t = e.target.closest('[data-tooltip], [title]');
    if (!t) {
      if (activeTarget) hide();
      return;
    }
    if (t === activeTarget) { hide(); return; }
    const touch = e.touches && e.touches[0];
    const cx = touch ? touch.clientX : undefined;
    const cy = touch ? touch.clientY : undefined;
    show(t, cx, cy);
  }, { passive: true });

  // Hide on scroll / resize / Escape so tooltips never linger stale.
  window.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
})();
