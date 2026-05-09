/**
 * GigsFill — Global Fetch Helpers (api-globals.js)
 * =================================================
 * Drop-in replacements for raw `fetch()` that surface real backend error
 * messages instead of generic ones. Available as `window.apiGetSafe`,
 * `window.apiPostSafe`, `window.apiPutSafe`, `window.apiDeleteSafe`.
 *
 * Use in non-ESM (IIFE-style) JS files where ES module imports aren't
 * available. ESM files should use `import { apiGet, apiPost, ... } from './api.js'`
 * which has the same behavior.
 *
 * Why these exist:
 *   FastAPI HTTPException returns JSON like {"detail": "..."}. Code that does
 *   `if (!res.ok) throw new Error('Failed to load')` discards that detail and
 *   shows the user a generic message. These helpers read the response body and
 *   throw an Error containing the backend's actual reason. So a 403 with
 *   "You have a blackout on this date: Vacation" reaches the user instead of
 *   "Failed to load".
 *
 * Usage:
 *   try {
 *     const data = await window.apiPostSafe('/api/foo', { ... });
 *     // data is parsed JSON
 *   } catch (e) {
 *     alert(e.message);  // shows the backend's actual error message
 *   }
 *
 * Loaded via <script src="/app/static/js/api-globals.js"></script> early
 * in the page so it's available to all subsequent scripts.
 */
(function () {
  'use strict';

  async function _readErrorMessage(res) {
    try {
      const ct = res.headers.get('content-type') || '';
      if (ct.includes('application/json')) {
        const body = await res.json();
        // FastAPI HTTPException: { "detail": "string" } or { "detail": { ... } }
        if (typeof body.detail === 'string') return body.detail;
        if (body.detail && typeof body.detail === 'object') {
          // Structured detail (e.g. waitlist_conflict)
          return body.detail.message || body.detail.error || JSON.stringify(body.detail);
        }
        if (typeof body.message === 'string') return body.message;
        if (typeof body.error === 'string') return body.error;
      } else {
        const text = await res.text();
        if (text) return text;
      }
    } catch (_e) {
      // Body wasn't readable — fall through
    }
    return res.statusText || ('HTTP ' + res.status);
  }

  async function apiGetSafe(url) {
    const res = await fetch(url, { credentials: 'include', cache: 'no-store' });
    if (!res.ok) {
      const msg = await _readErrorMessage(res);
      throw new Error(msg);
    }
    return res.json();
  }

  async function apiPostSafe(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (!res.ok) {
      const msg = await _readErrorMessage(res);
      throw new Error(msg);
    }
    return res.json();
  }

  async function apiPutSafe(url, body) {
    const res = await fetch(url, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (!res.ok) {
      const msg = await _readErrorMessage(res);
      throw new Error(msg);
    }
    return res.json();
  }

  async function apiDeleteSafe(url) {
    const res = await fetch(url, {
      method: 'DELETE',
      credentials: 'include'
    });
    if (!res.ok) {
      const msg = await _readErrorMessage(res);
      throw new Error(msg);
    }
    return res.json();
  }

  // Expose globally
  window.apiGetSafe    = apiGetSafe;
  window.apiPostSafe   = apiPostSafe;
  window.apiPutSafe    = apiPutSafe;
  window.apiDeleteSafe = apiDeleteSafe;
})();
