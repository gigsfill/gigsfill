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

  // Audit fix (May 2026 part 6/7): wrap fetch with AbortController + timeout.
  // GET uses 45s; state-changing methods (POST/PUT/DELETE) use 90s. Slow
  // backend operations (Stripe round-trips, PDF render, email blasts) can
  // exceed 45s and would otherwise leave the server running while the client
  // retries — risking duplicate bookings/charges. 90s is long enough for the
  // slowest legit operation without making genuinely hung calls feel forever.
  // 204 handling: calling .json() on an empty body throws — _parseBodyOr204
  // returns null instead.
  const _TIMEOUT_GET_MS  = 45000;
  const _TIMEOUT_WRITE_MS = 90000;
  async function _fetchWithTimeout(url, opts, isWrite) {
    const ctrl = new AbortController();
    const ms = isWrite ? _TIMEOUT_WRITE_MS : _TIMEOUT_GET_MS;
    const timer = setTimeout(() => ctrl.abort(), ms);
    try {
      return await fetch(url, Object.assign({ signal: ctrl.signal }, opts || {}));
    } catch (e) {
      if (e && e.name === 'AbortError') {
        const _secs = Math.round(ms / 1000);
        throw new Error(`Request timed out after ${_secs}s. The operation may still have completed on the server — refresh before retrying.`);
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }
  async function _parseBodyOr204(res) {
    if (res.status === 204) return null;
    const _len = res.headers.get('content-length');
    if (_len === '0') return null;
    try {
      return await res.json();
    } catch (_) {
      // Empty / non-JSON 2xx body — treat as success-with-no-payload.
      return null;
    }
  }

  async function apiGetSafe(url) {
    const res = await _fetchWithTimeout(url, { credentials: 'include', cache: 'no-store' }, false);
    if (!res.ok) {
      const msg = await _readErrorMessage(res);
      throw new Error(msg);
    }
    return _parseBodyOr204(res);
  }

  async function apiPostSafe(url, body) {
    const res = await _fetchWithTimeout(url, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }, true);
    if (!res.ok) {
      const msg = await _readErrorMessage(res);
      throw new Error(msg);
    }
    return _parseBodyOr204(res);
  }

  async function apiPutSafe(url, body) {
    const res = await _fetchWithTimeout(url, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }, true);
    if (!res.ok) {
      const msg = await _readErrorMessage(res);
      throw new Error(msg);
    }
    return _parseBodyOr204(res);
  }

  async function apiDeleteSafe(url, body) {
    // Audit fix (May 2026 part 4): accept an optional JSON body. The
    // cancel endpoints (gigs / slots / with-slots) all take payloads
    // (cancelled_by, cancellation_reason, keep_open) — callers that
    // hand-rolled raw fetch were doing it because this helper didn't
    // support that, which left them out of the {detail: "..."} error
    // surface. Now they can migrate cleanly.
    const opts = { method: 'DELETE', credentials: 'include' };
    if (body !== undefined && body !== null) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const res = await _fetchWithTimeout(url, opts, true);
    if (!res.ok) {
      const msg = await _readErrorMessage(res);
      throw new Error(msg);
    }
    return _parseBodyOr204(res);
  }

  // Expose globally
  window.apiGetSafe    = apiGetSafe;
  window.apiPostSafe   = apiPostSafe;
  window.apiPutSafe    = apiPutSafe;
  window.apiDeleteSafe = apiDeleteSafe;

  // ── window.formatPaySummary(slotOrGig) ────────────────────────────────
  // Single source of truth for rendering a slot/gig's pay amount with
  // door-split awareness. Mirrors backend/services/email_dispatch.py's
  // format_pay_summary_with_sign. Returns "$60.00" for flat pay or
  // "$50.00 guarantee + 20% of door" for door-split slots.
  //
  // Inputs handled (any of these may be present):
  //   - pay_summary    → backend pre-computed string (preferred — return as-is)
  //   - deal_type      → 'flat' | 'door'
  //   - guarantee_cents (int) / door_pct (int) → for door deals
  //   - pay (number, dollars) → flat amount or door floor
  //
  // Falls back gracefully: never throws, returns "$0.00" if nothing usable.
  function formatPaySummary(slot) {
    if (!slot || typeof slot !== 'object') return '$0.00';
    const dealType = String(slot.deal_type || 'flat').toLowerCase();
    if (dealType === 'door') {
      const gua = Math.max(0, Number(slot.guarantee_cents) || 0);
      const pct = Math.max(0, Math.min(100, parseInt(slot.door_pct, 10) || 0));
      if (gua > 0 || pct > 0) {
        const guaDollars = (gua / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        // SETTLED door deal: show the actual dollar share that came
        // from the door, not the booking-time percentage. Once
        // settled_at is set + receipts are recorded, the artist
        // wants to see "$10.00 guarantee + $120.00 from door"
        // rather than the abstract "+50% of door" deal terms.
        // Falls back to the unsettled format if settle fields aren't
        // available (e.g. on an unsettled future gig). Note: this
        // takes precedence over backend-precomputed pay_summary
        // because that string was computed at booking time and
        // doesn't know about subsequent settlement.
        if (slot.settled_at && slot.door_receipts_cents != null) {
          const shareCents = Math.floor((Number(slot.door_receipts_cents) || 0) * pct / 100);
          const shareDollars = (shareCents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          return `$${guaDollars} guarantee + $${shareDollars} from door`;
        }
        // Honor backend pre-computed string only when unsettled —
        // it matches the deal-terms form.
        if (typeof slot.pay_summary === 'string' && slot.pay_summary) return slot.pay_summary;
        return `$${guaDollars} guarantee + ${pct}% of door`;
      }
    }
    if (typeof slot.pay_summary === 'string' && slot.pay_summary) return slot.pay_summary;
    const pay = Number(slot.pay) || 0;
    return '$' + pay.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  window.formatPaySummary = formatPaySummary;

  // ── window.hasDoorDeal(slotOrGig) ─────────────────────────────────────
  // True if the slot uses door split AND has at least one usable term set.
  // Use for showing a 🎯 badge or door-specific UI.
  window.hasDoorDeal = function (slot) {
    if (!slot || typeof slot !== 'object') return false;
    if (String(slot.deal_type || 'flat').toLowerCase() !== 'door') return false;
    const gua = Number(slot.guarantee_cents) || 0;
    const pct = parseInt(slot.door_pct, 10) || 0;
    return gua > 0 || pct > 0;
  };

  // ── window.buildPaySlotTooltip(slots, counterparty) ──────────────────
  // Shared hover-popover builder for the Payments tables (venue +
  // artist). Renders a styled tooltip listing one row per slot:
  //
  //   Slot N · Artist Name   $XX.XX    breakdown
  //
  // Where `breakdown` is "flat" (muted) for flat-pay slots, or
  // "$10.00 guarantee + 50% of door ($10.00)" for door deals (with
  // the door share computed from receipts × pct). Pre-settle door
  // slots show "not yet settled" in amber.
  //
  // Returns HTML that should be placed INSIDE a relatively-positioned
  // host span carrying class .gf-pay-hover-host. The :hover rule
  // injected below flips display:none → block.
  // Per-slot payment status pill renderer — shared between the
  // tooltip + future inline UI. Reads (payout_status,
  // bank_settlement_status, payout_expected_at) from a slot_details
  // entry. Returns null when no payout data available (slot booked
  // but not yet charged → pay isn't owed yet).
  //
  // States mirror what the artist's earnings UI shows so the venue
  // can speak the artist's language:
  //
  //   paid                             → "Paid ✓"             green
  //   transferred + bss='pending'      → "At Stripe (hold)"   amber
  //   transferred + bss='available'    → "Payout queued Jun 24" blue
  //   transferred + bss=null/unknown   → "Transferring"        amber
  //   pending_transfer / charged       → "Processing"          amber
  //   scheduled                        → "Awaiting payout"     purple
  //   transfer_failed / payment_failed → "✗ Failed"            red
  function _payoutStatusForSlot(sd) {
    const ps = sd && sd.payout_status;
    if (!ps) return null;
    const bss = sd.bank_settlement_status;
    if (ps === 'paid') {
      return { label: 'Paid ✓', color: '#10b981' };
    }
    if (ps === 'transferred') {
      if (bss === 'pending') {
        return { label: 'At Stripe (hold)', color: '#f59e0b' };
      }
      if (bss === 'available') {
        let dateStr = '';
        if (sd.payout_expected_at) {
          try {
            const d = new Date(sd.payout_expected_at + 'T00:00:00');
            dateStr = ' ' + d.toLocaleDateString(undefined, {month:'short', day:'numeric'});
          } catch (_) {}
        }
        return { label: 'Payout queued' + dateStr, color: '#3b82f6' };
      }
      return { label: 'Transferring', color: '#f59e0b' };
    }
    if (ps === 'pending_transfer' || ps === 'charged') {
      return { label: 'Processing', color: '#f59e0b' };
    }
    if (ps === 'scheduled' || ps === 'test') {
      return { label: 'Awaiting payout', color: '#8b5cf6' };
    }
    if (ps === 'transfer_failed' || ps === 'payment_failed') {
      return { label: '✗ Failed', color: '#ef4444' };
    }
    if (ps === 'charge_retry') {
      return { label: 'Retrying', color: '#f97316' };
    }
    return null;
  }
  window._payoutStatusForSlot = _payoutStatusForSlot;

  window.buildPaySlotTooltip = function (slots, counterparty) {
    if (!Array.isArray(slots) || !slots.length) return '';
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
    const money = (c) => '$' + ((Number(c) || 0) / 100).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
    const dollars = (d) => '$' + (Number(d) || 0).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
    // Only render the 4th "Status" column if at least one slot has
    // a payout_status (i.e. has been booked + had a transaction
    // created). For pre-booking gigs the column is dead weight.
    const showStatusCol = slots.some(sd => _payoutStatusForSlot(sd) !== null);
    const rows = slots.map((sd) => {
      const slotN = sd.slot_number != null ? `Slot ${parseInt(sd.slot_number, 10)}` : '';
      const who = sd.artist_name || counterparty || '';
      const label = slotN + (slotN && who ? ' · ' : '') + who;
      const isDoor = String(sd.deal_type || '').toLowerCase() === 'door';
      const payStr = sd.pay != null ? dollars(sd.pay)
                  : (sd.settled_pay_cents != null ? money(sd.settled_pay_cents) : '');
      let detail = '';
      if (isDoor) {
        const gua = money(sd.guarantee_cents || 0);
        const pct = parseInt(sd.door_pct, 10) || 0;
        if (sd.settled_at && sd.door_receipts_cents != null) {
          // Door share = receipts × pct / 100 (mirrors backend math).
          const shareCents = Math.floor((Number(sd.door_receipts_cents) || 0) * pct / 100);
          detail = `${gua} guarantee + ${pct}% of door (${money(shareCents)})`;
        } else {
          detail = `${gua} guarantee + ${pct}% of door · <span style="color:#fbbf24;">not yet settled</span>`;
        }
      } else {
        detail = '<span style="color:var(--text-muted, #94a3b8);">flat</span>';
      }
      let statusCell = '';
      if (showStatusCol) {
        const st = _payoutStatusForSlot(sd);
        statusCell = st
          ? `<div style="color:${st.color};font-weight:600;padding:3px 0 3px 18px;text-align:right;white-space:nowrap;">${esc(st.label)}</div>`
          : `<div style="color:var(--text-muted, #94a3b8);padding:3px 0 3px 18px;text-align:right;">—</div>`;
      }
      return ''
        + '<div style="display:contents;">'
        +   `<div style="color:#a78bfa;font-weight:600;padding:3px 0;white-space:nowrap;">${esc(label)}</div>`
        +   `<div style="color:var(--text, #e4e7eb);font-weight:700;padding:3px 0 3px 18px;text-align:right;white-space:nowrap;">${payStr}</div>`
        +   `<div style="color:var(--text-muted, #94a3b8);padding:3px 0 3px 18px;white-space:nowrap;">${detail}</div>`
        +   statusCell
        + '</div>';
    }).join('');
    const gridTemplate = showStatusCol
      ? 'max-content max-content 1fr max-content'
      : 'max-content max-content 1fr';
    const headerRow = showStatusCol
      ? '<div style="display:contents;">'
      + '<div style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted, #94a3b8);padding:0 0 4px 0;">Slot</div>'
      + '<div style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted, #94a3b8);padding:0 0 4px 18px;text-align:right;">Pay</div>'
      + '<div style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted, #94a3b8);padding:0 0 4px 18px;">Deal</div>'
      + '<div style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted, #94a3b8);padding:0 0 4px 18px;text-align:right;">Payout</div>'
      + '</div>'
      : '';
    return ''
      + '<div class="gf-pay-hover-popover" style="'
      +   'position:absolute;bottom:100%;left:50%;transform:translateX(-50%);'
      +   'margin-bottom:8px;display:none;z-index:9000;pointer-events:none;'
      +   'background:#151b28;border:1px solid rgba(6,182,212,0.35);border-radius:8px;'
      +   'box-shadow:0 10px 24px rgba(0,0,0,0.45);padding:10px 14px;'
      +   'font-size:0.74rem;line-height:1.35;min-width:280px;max-width:640px;'
      +   '">'
      +   '<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--cyan, #06b6d4);font-weight:700;margin-bottom:6px;">Pay Breakdown</div>'
      +   `<div style="display:grid;grid-template-columns:${gridTemplate};column-gap:14px;row-gap:2px;align-items:baseline;">${headerRow}${rows}</div>`
      + '</div>';
  };

  // CSS that powers the hover trigger. Injected once on module load.
  (function _injectPayHoverCss() {
    if (document.getElementById('gfPayHoverCss')) return;
    const s = document.createElement('style');
    s.id = 'gfPayHoverCss';
    s.textContent =
      '.gf-pay-hover-host:hover .gf-pay-hover-popover { display: block !important; }';
    document.head.appendChild(s);
  })();
})();
