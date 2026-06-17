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

  function show(target) {
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

    // Default: below the trigger
    let top = rect.bottom + window.scrollY + 8;
    if (rect.bottom + tipRect.height + 16 > vh) {
      // Flip above when no room below
      top = rect.top + window.scrollY - tipRect.height - 8;
      tip.classList.add('gf-tooltip-above');
    }
    let left = rect.left + window.scrollX + (rect.width / 2) - (tipRect.width / 2);
    left = Math.max(8, Math.min(left, vw - tipRect.width - 8));

    tip.style.top = top + 'px';
    tip.style.left = left + 'px';
    requestAnimationFrame(() => tip.classList.add('gf-tooltip-visible'));

    activeTip = tip;
    activeTarget = target;
  }

  // Delegated mouseover / mouseout so it covers elements added dynamically.
  document.addEventListener('mouseover', (e) => {
    const t = e.target.closest('[data-tooltip], [title]');
    if (!t) return;
    if (t === activeTarget) return;
    show(t);
  });
  document.addEventListener('mouseout', (e) => {
    if (!activeTarget) return;
    // Only hide when leaving the trigger entirely.
    if (!activeTarget.contains(e.relatedTarget)) hide();
  });

  // Hide on scroll / resize / Escape so tooltips never linger stale.
  window.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
})();
