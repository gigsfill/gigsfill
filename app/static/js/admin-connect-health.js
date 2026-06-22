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

    // Stash for the drill-down modal lookup
    window._connectHealthRows = {};
    data.unhealthy.forEach(u => { window._connectHealthRows[u.artist_id] = u; });

    list.innerHTML = `<table class="data-table" style="width:100%;">
      <thead><tr>
        <th>Artist</th><th>Status</th><th>What Stripe needs</th>
        <th>Unhealthy since</th><th>Last emailed</th><th></th>
      </tr></thead>
      <tbody>${data.unhealthy.map(rowHtml).join('')}</tbody>
    </table>`;
    list.querySelectorAll('.connEmailBtn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        _emailOnboarding(parseInt(btn.dataset.artist, 10), btn);
      });
    });
    list.querySelectorAll('.connRowClickable').forEach(row => {
      row.addEventListener('click', () => _openDetailModal(parseInt(row.dataset.artist, 10)));
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
    const suspendedBadge = u.auto_suspended_at
      ? '<span style="display:inline-block;margin-left:6px;padding:1px 6px;background:rgba(239,68,68,0.15);border:1px solid #ef4444;border-radius:3px;color:#f87171;font-size:0.6rem;font-weight:700;">SUSPENDED</span>'
      : '';
    return `
      <tr class="connRowClickable" data-artist="${parseInt(u.artist_id, 10)}"
        style="cursor:pointer;" title="Click row for full Stripe requirement details.">
        <td>
          <div style="font-weight:600;color:var(--text);">${_esc(u.artist_name || ('Artist #' + u.artist_id))}${suspendedBadge}</div>
          <div style="font-size:0.7rem;color:var(--text-gray);">${_esc(u.artist_email || '')}</div>
        </td>
        <td style="font-size:0.78rem;">${statusBits.join(' · ')}</td>
        <td style="font-size:0.78rem;color:var(--text-gray);">${dueText}</td>
        <td style="font-size:0.7rem;color:var(--text-gray);">${_fmtAgo(u.unhealthy_since)}</td>
        <td style="font-size:0.7rem;color:var(--text-gray);">${_fmtAgo(u.artist_emailed_at)}</td>
        <td>
          <button class="connEmailBtn" data-artist="${parseInt(u.artist_id, 10)}"
            title="Force-send the Stripe onboarding email to this artist now. Bypasses the 7-day debounce. Use when an artist says they didn't get the previous email."
            style="padding:4px 10px;background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.4);border-radius:4px;color:var(--cyan);cursor:pointer;font-size:0.7rem;font-weight:600;white-space:nowrap;">Email link</button>
        </td>
      </tr>`;
  }

  // Drill-down modal — shows full Stripe requirements + errors + direct
  // Stripe Dashboard link for an admin doing deep investigation.
  function _openDetailModal(artistId) {
    const u = (window._connectHealthRows || {})[artistId];
    if (!u) return;
    const due = (u.currently_due || []).concat(u.past_due || []);
    const dueList = due.length
      ? `<ul style="margin:4px 0 12px;padding-left:18px;">${due.map(k => `<li style="color:var(--text);font-family:monospace;font-size:0.78rem;">${_esc(k)}</li>`).join('')}</ul>`
      : '<p style="color:var(--text-gray);font-size:0.78rem;margin:4px 0 12px;">None — disabled for other reason.</p>';
    const errsList = (u.errors || []).length
      ? `<ul style="margin:4px 0 12px;padding-left:18px;">${u.errors.map(e =>
          `<li style="color:#f87171;font-size:0.78rem;"><b>${_esc(e.code || '?')}</b>: ${_esc(e.reason || '—')} <span style="color:var(--text-gray);">(${_esc(e.requirement || '')})</span></li>`
        ).join('')}</ul>`
      : '<p style="color:var(--text-gray);font-size:0.78rem;margin:4px 0 12px;">No validation errors reported.</p>';
    const acctId = u.stripe_connect_account_id || '';
    const dashUrl = acctId
      ? `https://dashboard.stripe.com/connect/accounts/${encodeURIComponent(acctId)}`
      : 'https://dashboard.stripe.com/connect/accounts';
    // Find/create the modal container
    let m = document.getElementById('connectHealthModal');
    if (!m) {
      m = document.createElement('div');
      m.id = 'connectHealthModal';
      m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;padding:20px;';
      m.addEventListener('click', e => { if (e.target === m) m.remove(); });
      document.body.appendChild(m);
    }
    m.innerHTML = `
      <div style="max-width:560px;width:100%;max-height:85vh;overflow:auto;background:#0f1419;border:1px solid var(--border);border-radius:10px;padding:22px 24px;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:14px;">
          <div>
            <div style="font-size:1.05rem;font-weight:700;color:var(--text);">${_esc(u.artist_name || ('Artist #' + u.artist_id))}</div>
            <div style="font-size:0.72rem;color:var(--text-gray);margin-top:2px;">${_esc(u.artist_email || '—')}</div>
            <div style="font-size:0.7rem;color:var(--text-gray);font-family:monospace;margin-top:2px;">${_esc(acctId)}</div>
          </div>
          <button id="connectModalClose" style="background:transparent;border:none;color:var(--text-gray);font-size:1.4rem;cursor:pointer;line-height:1;">×</button>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px;">
          <div style="padding:8px 10px;background:rgba(255,255,255,0.03);border-radius:5px;">
            <div style="font-size:0.6rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Payouts</div>
            <div style="font-size:0.85rem;color:${u.payouts_enabled ? '#22c55e' : '#ef4444'};font-weight:600;">${u.payouts_enabled ? 'Enabled ✓' : 'Disabled ✗'}</div>
          </div>
          <div style="padding:8px 10px;background:rgba(255,255,255,0.03);border-radius:5px;">
            <div style="font-size:0.6rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Charges</div>
            <div style="font-size:0.85rem;color:${u.charges_enabled ? '#22c55e' : '#ef4444'};font-weight:600;">${u.charges_enabled ? 'Enabled ✓' : 'Disabled ✗'}</div>
          </div>
          <div style="padding:8px 10px;background:rgba(255,255,255,0.03);border-radius:5px;">
            <div style="font-size:0.6rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Onboarding</div>
            <div style="font-size:0.85rem;color:${u.details_submitted ? '#22c55e' : '#f59e0b'};font-weight:600;">${u.details_submitted ? 'Submitted' : 'Incomplete'}</div>
          </div>
          <div style="padding:8px 10px;background:rgba(255,255,255,0.03);border-radius:5px;">
            <div style="font-size:0.6rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Unhealthy since</div>
            <div style="font-size:0.85rem;color:var(--text);font-weight:600;">${_fmtAgo(u.unhealthy_since)}</div>
          </div>
        </div>

        ${u.disabled_reason ? `<div style="padding:8px 10px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:5px;margin-bottom:14px;font-size:0.78rem;color:#f87171;"><b>Disabled reason:</b> ${_esc(u.disabled_reason)}</div>` : ''}

        ${u.auto_suspended_at ? `<div style="padding:8px 10px;background:rgba(239,68,68,0.10);border:1px solid #ef4444;border-radius:5px;margin-bottom:14px;font-size:0.78rem;color:#f87171;"><b>🔒 Auto-suspended:</b> account has been unhealthy for 30+ days. stripe_connect_onboarding_complete cleared. Will auto-reinstate when Stripe reports healthy.</div>` : ''}

        <h4 style="margin:14px 0 4px;font-size:0.7rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Stripe requirements outstanding</h4>
        ${dueList}

        <h4 style="margin:14px 0 4px;font-size:0.7rem;color:var(--text-gray);text-transform:uppercase;letter-spacing:0.05em;">Validation errors</h4>
        ${errsList}

        <div style="display:flex;gap:8px;margin-top:18px;flex-wrap:wrap;">
          <a href="${dashUrl}" target="_blank" rel="noopener noreferrer"
            style="padding:6px 14px;background:rgba(124,107,255,0.12);border:1px solid rgba(124,107,255,0.4);border-radius:5px;color:#a78bfa;text-decoration:none;font-size:0.78rem;font-weight:600;">Open in Stripe Dashboard ↗</a>
          <button id="connectModalEmailBtn"
            style="padding:6px 14px;background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.4);border-radius:5px;color:var(--cyan);cursor:pointer;font-size:0.78rem;font-weight:600;">Email onboarding link</button>
        </div>

        <div style="font-size:0.66rem;color:var(--text-gray);margin-top:14px;">
          Last polled ${_fmtAgo(u.last_polled_at)} · Last admin alert ${_fmtAgo(u.admin_alerted_at)}
        </div>
      </div>
    `;
    m.querySelector('#connectModalClose').addEventListener('click', () => m.remove());
    m.querySelector('#connectModalEmailBtn').addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      const orig = btn.textContent;
      btn.disabled = true; btn.textContent = 'Sending…';
      await _emailOnboarding(artistId, btn);
      setTimeout(() => { if (m.parentNode) m.remove(); }, 1500);
    });
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
