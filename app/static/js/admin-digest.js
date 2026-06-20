/* admin-digest.js — Digest Queue admin subtab (Platform Settings → Digest Queue).
 *
 * Mirrors the Venue Flags subtab pattern. Reads GET /api/admin/digest-stats,
 * renders top-line counts + two tables (pending / recent sends), and
 * exposes a "Force send now" button per pending user so admin can test
 * a user's digest without waiting for their local 9 AM tick.
 */
(function () {
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }
  function _fmtAgo(s) {
    if (!s) return '—';
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

  async function loadDigestStats() {
    const top = document.getElementById('digestStatsTop');
    const pendingEl = document.getElementById('digestPendingTable');
    const recentEl = document.getElementById('digestRecentTable');
    if (!top || !pendingEl || !recentEl) return;
    top.innerHTML = '<div style="padding:8px;color:var(--text-gray);font-size:0.8rem;">Loading…</div>';
    pendingEl.innerHTML = '';
    recentEl.innerHTML = '';

    let data;
    try {
      const res = await fetch('/api/admin/digest-stats', { credentials: 'include' });
      if (res.status === 401 || res.status === 403) {
        top.innerHTML = '<div style="padding:8px;color:#f87171;font-size:0.8rem;">Not authorized.</div>';
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (e) {
      top.innerHTML = `<div style="padding:8px;color:#f87171;font-size:0.8rem;">Failed to load: ${_esc(e && e.message || 'unknown')}</div>`;
      return;
    }

    // Top-line stat bubbles. Color the master toggle state so the
    // admin can see at a glance whether the pipeline is even live.
    const flagColor = data.digest_enabled ? '#10b981' : '#ef4444';
    const flagText = data.digest_enabled ? 'Enabled' : 'Disabled (flag off)';
    top.innerHTML = `
      <div style="background:rgba(${data.digest_enabled ? '16,185,129' : '239,68,68'},0.10);border:1px solid ${flagColor};border-radius:6px;padding:8px 12px;">
        <div style="font-size:0.62rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Master flag</div>
        <div style="font-size:0.95rem;color:${flagColor};font-weight:700;margin-top:2px;">${flagText}</div>
      </div>
      <div style="background:rgba(124,107,255,0.08);border:1px solid rgba(124,107,255,0.3);border-radius:6px;padding:8px 12px;">
        <div style="font-size:0.62rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Send time</div>
        <div style="font-size:0.95rem;color:#a78bfa;font-weight:700;margin-top:2px;">${data.digest_hour}:00 local</div>
      </div>
      <div style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.3);border-radius:6px;padding:8px 12px;">
        <div style="font-size:0.62rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Pending</div>
        <div style="font-size:0.95rem;color:#fbbf24;font-weight:700;margin-top:2px;">${data.pending_count} item${data.pending_count === 1 ? '' : 's'} · ${data.pending_users} user${data.pending_users === 1 ? '' : 's'}</div>
      </div>
      <div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.3);border-radius:6px;padding:8px 12px;">
        <div style="font-size:0.62rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Sent (24h / 7d)</div>
        <div style="font-size:0.95rem;color:#22c55e;font-weight:700;margin-top:2px;">${data.sent_today} / ${data.sent_7d}</div>
      </div>
    `;

    // Pending table
    if (!data.pending_per_user.length) {
      pendingEl.innerHTML = '<div style="padding:14px;color:var(--text-gray);font-size:0.82rem;text-align:left;">No pending items. Either the hourly detector hasn\'t enqueued anything yet, or the morning sweep cleared the queue.</div>';
    } else {
      const rows = data.pending_per_user.map(r => `
        <tr>
          <td>${_esc(r.artist_name || '—')}</td>
          <td style="color:var(--text-gray);font-size:0.78rem;">${_esc(r.email || '—')}</td>
          <td style="text-align:right;font-weight:700;color:#fbbf24;">${r.gig_count}</td>
          <td style="text-align:right;color:var(--text-gray);">${r.venue_count}</td>
          <td style="color:var(--text-gray);font-size:0.78rem;">${_fmtAgo(r.oldest_queued)}</td>
          <td><button class="digestForceBtn" data-user="${parseInt(r.user_id, 10)}"
            title="Render and send this user's digest immediately. Bypasses the local-hour gate. The email subject is prefixed [ADMIN-FORCE]."
            style="padding:3px 10px;background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.4);border-radius:4px;color:var(--cyan);cursor:pointer;font-size:0.7rem;font-weight:600;">Force send</button></td>
        </tr>`).join('');
      pendingEl.innerHTML = `<table class="data-table" style="width:100%;">
        <thead><tr>
          <th>Artist</th><th>Email</th><th style="text-align:right;">Gigs</th>
          <th style="text-align:right;">Venues</th><th>Oldest queued</th><th></th>
        </tr></thead><tbody>${rows}</tbody></table>`;
      pendingEl.querySelectorAll('.digestForceBtn').forEach(btn => {
        btn.addEventListener('click', () => _forceSend(parseInt(btn.dataset.user, 10), btn));
      });
    }

    // Recent sends table
    if (!data.recent_sends.length) {
      recentEl.innerHTML = '<div style="padding:14px;color:var(--text-gray);font-size:0.82rem;text-align:left;">No digests sent yet.</div>';
    } else {
      const rows = data.recent_sends.map(r => `
        <tr>
          <td>${_esc(r.artist_name || '—')}</td>
          <td style="color:var(--text-gray);font-size:0.78rem;">${_esc(r.email || '—')}</td>
          <td style="text-align:right;color:var(--text-gray);">${r.gig_count}</td>
          <td style="text-align:right;color:var(--text-gray);">${r.venue_count}</td>
          <td style="color:var(--text-gray);font-size:0.78rem;">${_fmtAgo(r.sent_at)}</td>
        </tr>`).join('');
      recentEl.innerHTML = `<table class="data-table" style="width:100%;">
        <thead><tr>
          <th>Artist</th><th>Email</th><th style="text-align:right;">Gigs</th>
          <th style="text-align:right;">Venues</th><th>Sent</th>
        </tr></thead><tbody>${rows}</tbody></table>`;
    }
  }

  async function _forceSend(userId, btn) {
    if (!userId) return;
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Sending…';
    try {
      const res = await fetch(`/api/admin/digest-force-send/${userId}`, {
        method: 'POST', credentials: 'include'
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        btn.textContent = `✓ Sent (${data.sent})`;
        btn.style.background = 'rgba(34,197,94,0.15)';
        btn.style.borderColor = 'rgba(34,197,94,0.45)';
        btn.style.color = '#22c55e';
        setTimeout(loadDigestStats, 800);
      } else {
        btn.textContent = '✗ ' + (data.error || data.reason || `HTTP ${res.status}`);
        btn.style.background = 'rgba(239,68,68,0.15)';
        btn.style.borderColor = 'rgba(239,68,68,0.45)';
        btn.style.color = '#ef4444';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; btn.style.cssText = btn.style.cssText.replace(/background:[^;]+;|border-color:[^;]+;|color:[^;]+;/g, ''); }, 3000);
      }
    } catch (e) {
      btn.textContent = '✗ ' + e.message;
      btn.style.color = '#ef4444';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 3000);
    }
  }

  window.loadDigestStats = loadDigestStats;
})();
