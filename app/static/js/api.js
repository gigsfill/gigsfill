/**
 * Simple fetch wrappers for ES module imports
 * Used by: artist.book-gigs.js, venue.discovery.js, notifications-all.js
 *
 * FIX (May 2026): on non-ok response, read the response body and surface the
 * backend's actual error message — instead of throwing a generic
 * "POST <url> failed: <status>" that hides what went wrong.
 *
 * FastAPI HTTPException returns JSON like { "detail": "..." } where detail can
 * be a string or a structured dict. We prefer the string detail; if detail is
 * a dict, we try to pull a `message` or `error` field; otherwise we fall back
 * to the raw status text. This way blackout 403s, validation 400s, etc. all
 * show the human-readable reason in the UI.
 */

async function _readErrorMessage(res) {
  try {
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      const body = await res.json();
      // FastAPI HTTPException: { "detail": "string" } or { "detail": { ... } }
      if (typeof body.detail === 'string') return body.detail;
      if (body.detail && typeof body.detail === 'object') {
        // Structured detail (e.g. waitlist_conflict response)
        return body.detail.message || body.detail.error || JSON.stringify(body.detail);
      }
      // Other API shapes
      if (typeof body.message === 'string') return body.message;
      if (typeof body.error === 'string') return body.error;
    } else {
      const text = await res.text();
      if (text) return text;
    }
  } catch (_e) {
    // Body wasn't readable as JSON or text — fall through to status text
  }
  return res.statusText || `HTTP ${res.status}`;
}

export async function apiGet(url) {
  const res = await fetch(url, { credentials: 'include', cache: 'no-store' });
  if (!res.ok) {
    const msg = await _readErrorMessage(res);
    throw new Error(msg);
  }
  return res.json();
}

export async function apiPost(url, body) {
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

export async function apiPut(url, body) {
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

export async function apiDelete(url) {
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
