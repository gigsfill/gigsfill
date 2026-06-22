/* admin-connect-health.js — Account Health subtab (Platform Settings).
 *
 * Reads the cached Stripe Connect account health snapshot built by
 * the daily audit (services/connect_health.audit_all_accounts).
 * Lets admin see at a glance how many artists need attention + drill
 * into each one + force-email the onboarding link without waiting
 * for the 7-day debounce.
 *
 * Endpoints:
 *   GET  /api/admin/connect-health
 *   POST /api/admin/connect-health/audit-now
 *   POST /api/admin/connect-health/{artist_id}/email-onboarding
 */
(function () {
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }
  function _fmtAgo(s) {
    if (!s) return 'never';
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

  async function loadConnectHealth() {
    const stats = document.getElementById('connectHealthStats');
    const list  = document.getElementById('connectHealthList');
    if (!stats || !list) return;
    stats.innerHTML = '<div style="padding:10px;color:var(--text-gray);font-size:0.78rem;">Loading…</div>';
    list.innerHTML = '';
    let data;
    try {
      const res = await fetch('/api/admin/connect-health', { credentials: 'include' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      data = await res.json();
    } catch (e) {
      stats.innerHTML = `<div style="padding:10px;color:#ef4444;font-size:0.78rem;">Failed to load: ${_esc(e.message)}</div>`;
      return;
    }

    const healthyPct = data.total > 0 ? Math.round(100 * data.healthy_count / data.total) : 0;
    stats.innerHTML = `
      <div style="padding:10px;background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.35);border-radius:6px;">
        <div style="font-size:0.62rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Healthy</div>
        <div style="font-size:1.1rem;color:#22c55e;font-weight:700;margin-top:2px;">${data.healthy_count} / ${data.total} (${healthyPct}%)</div>
      </div>
      <div style="padding:10px;background:${data.unhealthy_count > 0 ? 'rgba(239,68,68,0.08)' : 'rgba(255,255,255,0.02)'};border:1px solid ${data.unhealthy_count > 0 ? 'rgba(239,68,68,0.4)' : 'var(--border)'};border-radius:6px;">
        <div style="font-size:0.62rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Need attention</div>
        <div style="font-size:1.1rem;color:${data.unhealthy_count > 0 ? '#f87171' : 'var(--text-gray)'};font-weight:700;margin-top:2px;">${data.unhealthy_count}</div>
      </div>
      <div style="padding:10px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;">
        <div style="font-size:0.62rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Last audit</div>
        <div style="font-size:0.95rem;color:var(--text);font-weight:600;margin-top:2px;">${_fmtAgo(data.last_audit_at)}</div>
      </div>
    `;

    if (!data.unhealthy_count) {
      list.innerHTML = '<div style="padding:18px;text-align:center;color:var(--text-gray);font-size:0.82rem;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;">All artist Connect accounts healthy ✓</div>';
      return;
    }

    list.innerHTML = `<table class="data-table" style="width:100%;">
      <thead><tr>
        <th>Artist</th><th>Status</th><th>What Stripe needs</th>
        <th>Last polled</th><th>Last emailed</th><th></th>
      </tr></thead>
      <tbody>${data.unhealthy.map(rowHtml).join('')}</tbody>
    </table>`;
    list.querySelectorAll('.connEmailBtn').forEach(btn => {
      btn.addEventListener('click', () => _emailOnboarding(parseInt(btn.dataset.artist, 10), btn));
    });
  }

  function rowHtml(u) {
    // Build a one-line summary of what Stripe wants. currently_due +
    // past_due are arrays of requirement keys ("individual.id_number",
    // "external_account", etc.). Show the count + a sample.
    const due = (u.currently_due || []).concat(u.past_due || []);
    const cdSample = due.slice(0, 3).map(k => k.split('.').slice(-1)[0]).join(', ');
    const hasErrors = (u.errors || []).length > 0;
    const dueText = due.length
      ? `${due.length} required${cdSample ? ' (' + _esc(cdSample) + (due.length > 3 ? '…' : '') + ')' : ''}`
      : (u.disabled_reason ? _esc(u.disabled_reason) : '—');
    const statusBits = [];
    if (!u.payouts_enabled) statusBits.push('<span style="color:#ef4444;font-weight:700;">Payouts off</span>');
    if (!u.charges_enabled) statusBits.push('<span style="color:#f59e0b;">Charges off</span>');
    if (!u.details_submitted) statusBits.push('<span style="color:#f59e0b;">Setup incomplete</span>');
    if (hasErrors) statusBits.push('<span style="color:#ef4444;">Errors</span>');
    if (!statusBits.length) statusBits.push('<span style="color:#f59e0b;">Missing info</span>');
    return `
      <tr>
        <td>
          <div style="font-weight:600;color:var(--text);">${_esc(u.artist_name || ('Artist #' + u.artist_id))}</div>
          <div style="font-size:0.7rem;color:var(--text-gray);">${_esc(u.artist_email || '')}</div>
        </td>
        <td style="font-size:0.78rem;">${statusBits.join(' · ')}</td>
        <td style="font-size:0.78rem;color:var(--text-gray);">${dueText}</td>
        <td style="font-size:0.7rem;color:var(--text-gray);">${_fmtAgo(u.last_polled_at)}</td>
        <td style="font-size:0.7rem;color:var(--text-gray);">${_fmtAgo(u.artist_emailed_at)}</td>
        <td>
          <button class="connEmailBtn" data-artist="${parseInt(u.artist_id, 10)}"
            title="Force-send the Stripe onboarding email to this artist now. Bypasses the 7-day debounce. Use when an artist says they didn't get the previous email."
            style="padding:4px 10px;background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.4);border-radius:4px;color:var(--cyan);cursor:pointer;font-size:0.7rem;font-weight:600;white-space:nowrap;">Email link</button>
        </td>
      </tr>`;
  }

  async function _emailOnboarding(artistId, btn) {
    if (!artistId) return;
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Sending…';
    try {
      const res = await fetch(`/api/admin/connect-health/${artistId}/email-onboarding`, {
        method: 'POST', credentials: 'include'
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        btn.textContent = '✓ Sent';
        btn.style.color = '#22c55e';
        setTimeout(loadConnectHealth, 1200);
      } else {
        btn.textContent = '✗ Failed';
        btn.style.color = '#ef4444';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; btn.style.color = ''; }, 2500);
      }
    } catch (e) {
      btn.textContent = '✗ Error';
      btn.style.color = '#ef4444';
      setTimeout(() => { btn.disabled = false; btn.textContent = orig; btn.style.color = ''; }, 2500);
    }
  }

  // Wire the "Audit now" button after DOM ready
  function _wireAuditBtn() {
    const btn = document.getElementById('connectHealthAuditBtn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const orig = btn.textContent;
      btn.disabled = true; btn.textContent = 'Auditing…';
      try {
        const res = await fetch('/api/admin/connect-health/audit-now', {
          method: 'POST', credentials: 'include'
        });
        if (res.ok) {
          btn.textContent = '✓ Done';
          await loadConnectHealth();
          setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 1500);
        } else {
          btn.textContent = '✗ Failed';
          setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
        }
      } catch (e) {
        btn.textContent = '✗ Error';
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2500);
      }
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wireAuditBtn);
  } else {
    _wireAuditBtn();
  }

  window.loadConnectHealth = loadConnectHealth;
})();
