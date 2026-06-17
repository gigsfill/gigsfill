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

/**
 * Build a SAFE inline-JS-string literal for use inside an HTML attribute.
 *
 * Audit fix (May 2026 part 8): the recurring XSS sink across the codebase
 * is the pattern `onclick="someFn('${esc(name)}')"`. esc() encodes `'` to
 * `&#39;`, but the HTML attribute parser DECODES `&#39;` back to `'` BEFORE
 * the JS string is parsed — so the apostrophe still breaks the inline JS
 * literal and any injection inside `name` runs.
 *
 * jsAttr() emits a complete, quoted, escape-safe JS string literal usable
 * inside a double-quoted HTML attribute. Two-stage escaping:
 *   1. JSON.stringify() produces a valid JS string literal (handles `\`,
 *      `"`, control chars, unicode, `</script>` via the standard rules,
 *      surrogate pairs).
 *   2. The result is then HTML-attribute-escaped so the embedded `"` chars
 *      don't end the host attribute.
 *
 * Use AS the entire JS string token (no surrounding quotes):
 *     `onclick="someFn(${jsAttr(name)})"`
 * NOT `onclick="someFn('${jsAttr(name)}')"` — jsAttr already includes the
 * outer quotes.
 */
function jsAttr(str) {
  if (str === null || str === undefined) return '""';
  // 1) JSON.stringify → valid JS string literal
  let json = JSON.stringify(String(str));
  // 2) Also block `</` so a value like `</script>` can't break out of an
  // inline-script context (defense-in-depth for non-attribute uses).
  json = json.replace(/</g, '\\u003c').replace(/>/g, '\\u003e');
  // 3) HTML-attribute escape — the JSON literal contains `"` which would
  // close the surrounding double-quoted HTML attribute.
  return json
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;');
}

// Expose globally (security.js is loaded before everything else)
if (typeof window !== 'undefined') {
  window.esc = esc;
  window.escAttr = escAttr;
  window.jsAttr = jsAttr;
}
