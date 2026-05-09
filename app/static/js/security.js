/**
 * GigsFill Security Utilities
 * ============================
 * Global XSS prevention helpers. Include this BEFORE any other JS files.
 * 
 * Usage in template literals:
 *   innerHTML = `<div>${esc(userName)}</div>`;
 *   innerHTML = `<a href="${escAttr(url)}">${esc(linkText)}</a>`;
 */

// Short alias for HTML escaping — use in innerHTML template literals
function esc(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Note: escapeHtml() is defined in user-dropdown.js
// Use esc() directly in all other files

// Escape for use inside HTML attributes (also handles backticks)
function escAttr(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/`/g, '&#96;');
}
