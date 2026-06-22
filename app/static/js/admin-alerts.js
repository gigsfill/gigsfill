/* admin-alerts.js — System alerts banner on admin.html.
 *
 * Polls GET /api/admin/system-alerts every 60s. Renders unresolved
 * + un-acknowledged alerts as a red-bordered card stack at the top
 * of admin.html. Each card has:
 *   - severity-colored left border (critical=red, warning=amber)
 *   - one-line message + expandable details
 *   - first_seen / last_seen / count
 *   - Dismiss button → POST /acknowledge → hides the card until
 *     the alert fires again (next fire re-surfaces it)
 *
 * Auto-clears when alerts resolve server-side — no admin action
 * required for the common case of stripe-webhook-secret-rotated-
 * and-then-fixed.
 */
(function () {
  const POLL_MS = 60_000;

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  function _fmtAgo(s) {
    if (!s) return '';
    try {
      const d = new Date(String(s).replace(' ', 'T') + 'Z');
      if (isNaN(d.getTime())) return _esc(s);
      const diff = Date.now() - d.getTime();
      const mins = Math.floor(diff / 60_000);
      if (mins < 1) return 'just now';
      if (mins < 60) return mins + 'm ago';
      const hrs = Math.floor(mins / 60);
      if (hrs < 24) return hrs + 'h ago';
      return Math.floor(hrs / 24) + 'd ago';
    } catch (_) { return _esc(s); }
  }

  function _severityColor(sev) {
    if (sev === 'critical') return { border: '#ef4444', bg: 'rgba(239,68,68,0.08)', text: '#fca5a5' };
    if (sev === 'warning')  return { border: '#f59e0b', bg: 'rgba(245,158,11,0.08)', text: '#fbbf24' };
    return { border: '#6b7280', bg: 'rgba(107,114,128,0.08)', text: '#9ca3af' };
  }

  async function loadAlerts() {
    const banner = document.getElementById('systemAlertsBanner');
    if (!banner) return;
    let data;
    try {
      const res = await fetch('/api/admin/system-alerts', { credentials: 'include' });
      if (res.status === 401 || res.status === 403) {
        // Not an admin (or session expired) — silent, don't pollute the UI
        banner.innerHTML = '';
        return;
      }
      if (!res.ok) throw new Error('HTTP ' + res.status);
      data = await res.json();
    } catch (e) {
      // Network blip — don't blow away an existing banner, just skip this tick
      console.warn('[admin-alerts] poll failed:', e.message);
      return;
    }
    // Filter: show only un-acknowledged active alerts in the banner.
    // Acknowledged ones stay in the DB (so admin can see history in a
    // dedicated view later) but the banner stays clean.
    const visible = (data.active || []).filter(a => !a.acknowledged_at);
    if (!visible.length) {
      banner.innerHTML = '';
      return;
    }
    banner.innerHTML = visible.map(a => {
      const c = _severityColor(a.severity);
      const expandId = 'alert-detail-' + a.id;
      const sevLabel = (a.severity || 'info').toUpperCase();
      return `
        <div style="margin-bottom:10px;border-left:4px solid ${c.border};background:${c.bg};border-radius:6px;padding:14px 16px;display:flex;gap:12px;align-items:flex-start;">
          <div style="flex-shrink:0;font-size:1.2rem;line-height:1.2;">⚠️</div>
          <div style="flex:1;min-width:0;">
            <div style="display:flex;gap:10px;align-items:center;margin-bottom:4px;flex-wrap:wrap;">
              <span style="font-size:0.65rem;font-weight:800;color:${c.text};text-transform:uppercase;letter-spacing:0.06em;">${_esc(sevLabel)}</span>
              <span style="font-size:0.7rem;color:var(--text-gray);font-family:monospace;">${_esc(a.alert_type)}</span>
              <span style="font-size:0.7rem;color:var(--text-gray);">·  fired ${a.count}× ·  first ${_fmtAgo(a.first_seen_at)} ·  latest ${_fmtAgo(a.last_seen_at)}</span>
            </div>
            <div style="font-size:0.92rem;color:var(--text);font-weight:600;margin-bottom:4px;">${_esc(a.message)}</div>
            ${a.details ? `
              <details id="${expandId}" style="margin-top:4px;">
                <summary style="cursor:pointer;font-size:0.78rem;color:${c.text};font-weight:600;">Show fix steps</summary>
                <div style="margin-top:8px;padding:10px;background:rgba(0,0,0,0.2);border-radius:5px;font-size:0.82rem;color:var(--text-gray);line-height:1.5;">${_esc(a.details)}</div>
              </details>` : ''}
          </div>
          <button data-alert-id="${a.id}" class="alertDismissBtn"
            title="Hide this alert from the banner. If the underlying condition fires again, the alert will re-appear."
            style="flex-shrink:0;padding:5px 12px;background:transparent;border:1px solid ${c.border};border-radius:5px;color:${c.text};cursor:pointer;font-size:0.75rem;font-weight:600;align-self:center;">Dismiss</button>
        </div>`;
    }).join('');
    banner.querySelectorAll('.alertDismissBtn').forEach(btn => {
      btn.addEventListener('click', () => _acknowledge(parseInt(btn.dataset.alertId, 10), btn));
    });
  }

  async function _acknowledge(alertId, btn) {
    if (!alertId) return;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const res = await fetch(`/api/admin/system-alerts/${alertId}/acknowledge`, {
        method: 'POST', credentials: 'include'
      });
      if (res.ok) {
        // Reload the whole banner so the row disappears cleanly + any
        // siblings re-flow.
        await loadAlerts();
      } else {
        btn.textContent = '✗ Failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = 'Dismiss'; }, 2000);
      }
    } catch (e) {
      btn.textContent = '✗ Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = 'Dismiss'; }, 2000);
    }
  }

  // Fire on page load (after a tick so DOMContentLoaded settles) +
  // poll every minute. Cheap query, indexed table — no perf concern.
  function _start() {
    loadAlerts();
    setInterval(loadAlerts, POLL_MS);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _start);
  } else {
    _start();
  }

  window.loadSystemAlerts = loadAlerts;
})();
