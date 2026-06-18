/* admin-flags.js — Off-Platform-Pay Flagged Venues admin tab.
 *
 * Lists venues that have booked artists at $0 pay (flat-$0 or pure-
 * door no-guarantee). Each row shows the venue, running count of $0
 * bookings, last-flagged timestamp, and an editable fee_pct_override
 * input that admin can use to raise this venue's platform fee on
 * FUTURE real bookings to recoup the lost cut.
 *
 * Backend:
 *   GET  /api/admin/zero-pay-flagged-venues   — list (auth: admin)
 *   PUT  /api/admin/venues/{vid}/fee-pct-override  — set/clear
 *
 * UX:
 *   - Inline number input; blur to save (with Esc to revert).
 *   - Green flash on save success, red border on validation error.
 *   - Empty value = clear override (revert to platform default).
 */
(function () {
  // Dismiss the help banner on tab load if user already X'd it.
  function _restoreBannerState() {
    if (sessionStorage.getItem('flagsBannerDismissed') === '1') {
      const b = document.getElementById('flagsHelpBanner');
      if (b) b.style.display = 'none';
    }
  }

  function _fmtPct(v) {
    if (v == null) return '—';
    const n = Number(v);
    if (!isFinite(n)) return '—';
    // Trim trailing zeros for compact display: 25 → "25%", 12.5 → "12.5%"
    return (Math.round(n * 100) / 100).toString() + '%';
  }

  function _fmtTime(s) {
    if (!s) return '—';
    try {
      const d = new Date(s);
      if (isNaN(d.getTime())) return s;
      return d.toLocaleString();
    } catch (_) {
      return s;
    }
  }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  async function loadFlaggedVenues() {
    _restoreBannerState();
    const container = document.getElementById('flagsTable');
    const empty = document.getElementById('flagsEmpty');
    if (!container) return;
    container.innerHTML = '<div style="padding:20px;color:var(--text-gray);font-size:0.8rem;">Loading…</div>';
    empty && (empty.style.display = 'none');

    let rows = [];
    try {
      const res = await fetch('/api/admin/zero-pay-flagged-venues', { credentials: 'include' });
      if (res.status === 401 || res.status === 403) {
        container.innerHTML = '<div style="padding:20px;color:#f87171;font-size:0.8rem;">Not authorized.</div>';
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      rows = await res.json();
    } catch (e) {
      container.innerHTML = `<div style="padding:20px;color:#f87171;font-size:0.8rem;">Failed to load: ${_esc(e && e.message || 'unknown')}</div>`;
      return;
    }

    if (!Array.isArray(rows) || rows.length === 0) {
      container.innerHTML = '';
      empty && (empty.style.display = 'block');
      return;
    }

    // Render table. Each row's fee_pct_override cell is an inline
    // editable number input — change + blur (or Enter) saves; Esc
    // reverts. The save handler PUTs to the admin endpoint and
    // refreshes only that row's UI on success.
    const head = `
      <thead>
        <tr>
          <th style="text-align:left;">Venue</th>
          <th style="text-align:left;">Location</th>
          <th style="text-align:right;">$0 Bookings</th>
          <th style="text-align:left;">Last Flagged</th>
          <th style="text-align:left;">Fee % Override</th>
          <th></th>
        </tr>
      </thead>`;

    const body = rows.map(r => {
      const cur = r.fee_pct_override == null ? '' : String(r.fee_pct_override);
      const venueId = parseInt(r.venue_id, 10) || 0;
      const loc = [r.city, r.state].filter(Boolean).join(', ');
      const profileHref = `/app/venue-profile.html?venue_id=${venueId}`;
      return `
        <tr data-venue-id="${venueId}">
          <td>
            <a href="${profileHref}" target="_blank" style="color:var(--cyan);text-decoration:none;">
              ${_esc(r.venue_name || `Venue #${venueId}`)}
            </a>
          </td>
          <td style="color:var(--text-gray);font-size:0.78rem;">${_esc(loc || '—')}</td>
          <td style="text-align:right;font-weight:700;color:#fbbf24;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">
            ${parseInt(r.zero_pay_booking_count) || 0}
          </td>
          <td style="color:var(--text-gray);font-size:0.78rem;">${_esc(_fmtTime(r.last_zero_pay_at))}</td>
          <td>
            <div style="display:flex;align-items:center;gap:6px;">
              <input type="number" class="flagsFeeInput" data-venue-id="${venueId}"
                value="${_esc(cur)}" min="0" max="100" step="0.5"
                placeholder="—"
                title="Per-venue platform fee % override. Leave blank to use the global default. Range 0–100. Press Enter or blur to save; Esc to cancel."
                style="width:72px;padding:4px 8px;background:#151b28;border:1px solid #333;border-radius:5px;color:var(--text-white);font-size:0.78rem;text-align:right;">
              <span style="color:var(--text-gray);font-size:0.78rem;">%</span>
              <span class="flagsRowStatus" style="font-size:0.7rem;color:var(--text-gray);min-width:64px;"></span>
            </div>
          </td>
          <td>
            <button class="flagsClearBtn" data-venue-id="${venueId}"
              title="Clear this venue's override and revert to the global platform fee %"
              style="padding:4px 10px;background:transparent;border:1px solid rgba(255,255,255,0.18);border-radius:5px;color:var(--text-gray);cursor:pointer;font-size:0.7rem;">
              Clear
            </button>
          </td>
        </tr>`;
    }).join('');

    container.innerHTML = `<table class="data-table" style="width:100%;">${head}<tbody>${body}</tbody></table>`;
    _wireInputs();
  }

  function _wireInputs() {
    document.querySelectorAll('.flagsFeeInput').forEach(inp => {
      // Track the value we saw on focus so Esc can revert.
      inp.addEventListener('focus', () => { inp.dataset._lastSaved = inp.value; });
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          inp.blur(); // triggers save via blur handler
        } else if (e.key === 'Escape') {
          inp.value = inp.dataset._lastSaved || '';
          inp.blur();
        }
      });
      inp.addEventListener('blur', () => _saveFeeOverride(inp));
    });
    document.querySelectorAll('.flagsClearBtn').forEach(btn => {
      btn.addEventListener('click', () => {
        const vid = parseInt(btn.getAttribute('data-venue-id'), 10);
        const inp = document.querySelector(`.flagsFeeInput[data-venue-id="${vid}"]`);
        if (inp) {
          inp.value = '';
          _saveFeeOverride(inp);
        }
      });
    });
  }

  async function _saveFeeOverride(inp) {
    const venueId = parseInt(inp.getAttribute('data-venue-id'), 10);
    const row = inp.closest('tr');
    const statusEl = row ? row.querySelector('.flagsRowStatus') : null;
    const raw = inp.value.trim();
    let payload;
    if (raw === '') {
      payload = { fee_pct_override: null };
    } else {
      const v = Number(raw);
      if (!isFinite(v) || v < 0 || v > 100) {
        inp.style.borderColor = '#ef4444';
        if (statusEl) { statusEl.textContent = 'Invalid'; statusEl.style.color = '#f87171'; }
        return;
      }
      payload = { fee_pct_override: v };
    }
    inp.style.borderColor = '#333';
    if (statusEl) { statusEl.textContent = 'Saving…'; statusEl.style.color = 'var(--text-gray)'; }
    try {
      const res = await fetch(`/api/admin/venues/${venueId}/fee-pct-override`, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const j = await res.json(); if (j && j.detail) msg = j.detail; } catch (_) {}
        throw new Error(msg);
      }
      inp.dataset._lastSaved = inp.value;
      if (statusEl) {
        statusEl.textContent = '✓ Saved';
        statusEl.style.color = '#22c55e';
        setTimeout(() => { if (statusEl.textContent === '✓ Saved') statusEl.textContent = ''; }, 1500);
      }
    } catch (e) {
      inp.style.borderColor = '#ef4444';
      if (statusEl) {
        statusEl.textContent = 'Failed';
        statusEl.style.color = '#f87171';
      }
      console.error('[flags] save override failed:', e);
    }
  }

  // Expose so switchTab() can call it on tab activation.
  window.loadFlaggedVenues = loadFlaggedVenues;
})();
