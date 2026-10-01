/**
 * Shared social brand icons + colours.
 * ===================================
 * 2026-10-01. These SVGs and brand colours lived inline inside
 * artist-profile.html, which meant venue-profile.html and the redesign
 * prototype each needed their own copy. Extracted so there's one set.
 *
 *   window.gfSocialIcon(brand)   -> inline SVG string
 *   window.gfBrandColor(brand)   -> brand hex, for accenting
 *   window.gfSafeHref(url)       -> url if http(s), else "" 
 *
 * The href guard matters: these URLs are artist-supplied, so a saved value
 * of `javascript:alert(1)` must not survive into an anchor.
 */
(function () {
  "use strict";

  var COLORS = {
    spotify:    '#1DB954',
    instagram:  '#E1306C',
    facebook:   '#1877F2',
    youtube:    '#FF0000',
    twitter:    '#e5e7eb',   /* X is mono — light grey reads on dark */
    tiktok:     '#69C9D0',
    website:    '#06b6d4',
    yelp:       '#D32323',
    google_maps:'#EA4335'
  };


  var ICONS = {
        spotify:   '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.52 17.34c-.24.36-.66.48-1.02.24-2.82-1.74-6.36-2.1-10.56-1.14-.42.12-.78-.18-.9-.54-.12-.42.18-.78.54-.9 4.56-1.02 8.52-.6 11.64 1.32.42.18.48.66.3 1.02zm1.44-3.3c-.3.42-.84.6-1.26.3-3.24-1.98-8.16-2.58-11.94-1.38-.48.12-1.02-.12-1.14-.6-.12-.48.12-1.02.6-1.14C9.6 9.9 15 10.56 18.72 12.84c.36.18.54.78.24 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.3c-.6.18-1.2-.18-1.38-.72-.18-.6.18-1.2.72-1.38 4.26-1.26 11.28-1.02 15.72 1.62.54.3.72 1.02.42 1.56-.3.42-1.02.6-1.56.3z"/></svg>',
        instagram: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.16c3.2 0 3.58.01 4.85.07 3.25.15 4.77 1.69 4.92 4.92.06 1.27.07 1.65.07 4.85 0 3.21-.01 3.58-.07 4.85-.15 3.23-1.66 4.77-4.92 4.92-1.27.06-1.64.07-4.85.07-3.2 0-3.58-.01-4.85-.07-3.26-.15-4.77-1.7-4.92-4.92-.06-1.27-.07-1.64-.07-4.85 0-3.2.01-3.58.07-4.85.15-3.23 1.66-4.77 4.92-4.92 1.27-.06 1.65-.07 4.85-.07zM12 0C8.74 0 8.33.01 7.05.07 2.7.27.27 2.69.07 7.05.01 8.33 0 8.74 0 12c0 3.26.01 3.67.07 4.95.2 4.36 2.62 6.78 6.98 6.98C8.33 23.99 8.74 24 12 24c3.26 0 3.67-.01 4.95-.07 4.35-.2 6.78-2.62 6.98-6.98.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95C23.73 2.69 21.31.27 16.95.07 15.67.01 15.26 0 12 0zm0 5.84a6.16 6.16 0 1 0 0 12.32 6.16 6.16 0 0 0 0-12.32zM12 16a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm6.41-11.85a1.44 1.44 0 1 0 0 2.88 1.44 1.44 0 0 0 0-2.88z"/></svg>',
        facebook:  '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M24 12.07C24 5.45 18.63.07 12 .07S0 5.45 0 12.07c0 5.99 4.39 10.96 10.13 11.86V15.5H7.08v-3.47h3.05V9.43c0-3.01 1.79-4.67 4.53-4.67 1.31 0 2.69.23 2.69.23v2.95H15.83c-1.49 0-1.96.93-1.96 1.88v2.25h3.33l-.53 3.47h-2.8v8.43C19.61 23.03 24 18.06 24 12.07z"/></svg>',
        youtube:   '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M23.5 6.19a3.02 3.02 0 0 0-2.12-2.14C19.5 3.55 12 3.55 12 3.55s-7.5 0-9.38.5A3.02 3.02 0 0 0 .5 6.2C0 8.07 0 12 0 12s0 3.93.5 5.81a3.02 3.02 0 0 0 2.12 2.14c1.87.5 9.38.5 9.38.5s7.5 0 9.38-.5a3.02 3.02 0 0 0 2.12-2.14C24 15.93 24 12 24 12s0-3.93-.5-5.81zM9.55 15.57V8.43L15.82 12l-6.27 3.57z"/></svg>',
        twitter:   '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M18.24 2.25h3.31l-7.23 8.26 8.5 11.24h-6.65l-5.21-6.82L4.99 21.75H1.68l7.73-8.83L1.25 2.25h6.83l4.71 6.23zM17.08 19.77h1.83L7.08 4.13H5.12L17.08 19.77z"/></svg>',
        tiktok:    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64 2.93 2.93 0 0 1 .88.13V9.4a6.84 6.84 0 0 0-1-.05A6.33 6.33 0 0 0 5.07 20.1a6.34 6.34 0 0 0 10.86-4.43V8.31a8.16 8.16 0 0 0 4.77 1.52V6.42a4.85 4.85 0 0 1-1.11.27z"/></svg>',
        website:   '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zM11 19.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>',
        yelp:      '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M14.05 14.42l1.43 4.94c.1.38-.14.78-.5.83-1.66.21-3.24-.16-4.7-.96-.21-.11-.27-.39-.13-.59l3.27-4.45c.2-.28.55-.16.63.23zm-2.06-2.97L7.81 6.05c-.22-.27-.62-.14-.78.18A11.15 11.15 0 0 0 6.4 9.96c-.02.36.26.69.62.71l5.16-.04c.34-.01.5-.41.31-.69zm5.96-.34l-4.7 1.43c-.36.11-.36.62 0 .73l4.7 1.43c.4.13.78-.17.78-.59v-2.41c0-.42-.38-.72-.78-.59zm-3.59-1.6l3.27 4.45c.14.2.42.14.55-.06.84-1.36 1.32-2.86 1.42-4.45.03-.36-.24-.65-.6-.7l-4.31-.63c-.34-.05-.53.4-.33.66zm-3.06 4.71l-3.4-3.85c-.27-.31-.78-.16-.83.25C7 12.16 7 13.71 7.38 15.18c.08.34.46.46.73.23l3.39-3.85c.18-.2.18-.51 0-.71l-.2-.43z"/></svg>',
        google_maps:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z"/></svg>'
  };

  window.gfSocialIcon = function (brand) { return ICONS[brand] || ICONS.website; };
  window.gfBrandColor = function (brand) { return COLORS[brand] || '#9ca3af'; };
  window.gfSafeHref = function (url) {
    var u = String(url || '').trim();
    if (!u) return '';
    // If it already declares a scheme, that scheme must be http(s). Naively
    // prepending https:// to anything unrecognised turns `javascript:alert(1)`
    // into `https://javascript:alert(1)` — a safe scheme wrapping a payload,
    // which is not the same as rejecting it.
    if (/^[a-z][a-z0-9+.\-]*:/i.test(u)) {
      return /^https?:\/\//i.test(u) ? u : '';
    }
    return 'https://' + u;   // bare domain, e.g. "fridayspast.com"
  };
})();
