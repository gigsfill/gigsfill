// user-affiliate.js — User profile Affiliates tab

let _affSummary = null;

// ── Entry Point ───────────────────────────────────────────────────────────────

async function loadAffiliatesPage() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('stripe') === 'complete' || params.get('stripe') === 'refresh') {
    window.history.replaceState({}, '', window.location.pathname + '?tab=affiliates');
  }
  await Promise.all([
    loadAffSummary(),
    loadAffMyEmails(),
    loadAffMyReferrals(),
    loadAffW9(),
    loadAffProgramSettings(),
    loadAffArtistStripeAccounts(),
    loadAffChecklist(),
  ]);
}

// ── Setup checklist (Stripe + W-9 mandatory for affiliates) ──────────────────

async function loadAffChecklist() {
  const uid = window._currentUserId;
  if (!uid) return;
  try {
    const r = await fetch(`/api/onboarding/affiliate/${uid}`, { credentials: 'include' });
    if (!r.ok) return;
    const d = await r.json();
    const banner = document.getElementById('affChecklistBanner');
    if (!banner) return;

    // No tasks (no referrals yet) → hide banner entirely.
    if (!d.tasks || !d.tasks.length || d.all_complete) {
      banner.style.display = 'none';
      return;
    }

    const stripeTask = d.tasks.find(t => t.key === 'stripe_setup');
    const w9Task     = d.tasks.find(t => t.key === 'w9_filed');
    const done = d.tasks.filter(t => t.completed).length;
    const total = d.tasks.length;

    const stripeBtn = document.getElementById('affChecklistStripeBtn');
    const w9Btn     = document.getElementById('affChecklistW9Btn');
    if (stripeBtn) {
      if (stripeTask?.completed) {
        stripeBtn.textContent = '✓ Stripe set up';
        stripeBtn.disabled = true;
        stripeBtn.style.opacity = '0.6';
        stripeBtn.style.cursor = 'default';
      }
    }
    if (w9Btn) {
      if (w9Task?.completed) {
        w9Btn.textContent = '✓ W-9 on file';
        w9Btn.disabled = true;
        w9Btn.style.opacity = '0.6';
        w9Btn.style.cursor = 'default';
      }
    }

    const msgEl = document.getElementById('affChecklistMessage');
    if (msgEl) {
      const pending = d.tasks.filter(t => !t.completed).map(t => t.title);
      msgEl.textContent =
        'A venue has been linked to your affiliate account. Before we can pay you, please complete: '
        + pending.join(' and ') + '.';
    }
    const progEl = document.getElementById('affChecklistProgress');
    if (progEl) progEl.textContent = `${done} of ${total} steps complete`;
    banner.style.display = 'block';
  } catch (e) {}
}

// ── Program Settings (dynamic payout schedule footer) ────────────────────────

async function loadAffProgramSettings() {
  try {
    const r = await fetch('/api/affiliate/program-settings');
    if (!r.ok) return;
    const s = await r.json();

    // If program is disabled, hide the affiliate tab content
    if (s.enabled === false) {
      const tabContent = document.getElementById('affiliatesTabContent') ||
                         document.querySelector('.affiliates-tab-content');
      if (tabContent) {
        tabContent.innerHTML = '<div style="text-align:center;padding:48px 20px;color:var(--text-gray);font-size:0.88rem;">The affiliate program is currently disabled. Check back soon!</div>';
      }
      return;
    }

    const rate     = s.rate_percent;
    const reduced  = s.reduced_rate_percent;
    const days     = s.reduced_after_days;
    const minDol   = (s.min_payout_cents / 100).toFixed(0);
    const years    = days >= 365 ? Math.round(days/365) + ' year' : days + ' days';
    const el = document.getElementById('affPayoutScheduleInfo');
    if (el) el.innerHTML =
      `<strong style="color:var(--text);">Payout Schedule:</strong> Apr 1 · Jul 1 · Oct 1 · Dec 31<br>` +
      `<strong style="color:var(--text);">Earnings on:</strong> ${rate}% of each gig fee for venues you referred` +
      (reduced !== rate ? ` (reduces to ${reduced}% after ${years})` : '') + `<br>` +
      `<strong style="color:var(--text);">Minimum payout:</strong> $${minDol} — smaller balances roll over to next quarter`;
  } catch(e) {}
}

// ── Summary / Stats ───────────────────────────────────────────────────────────

async function loadAffSummary() {
  try {
    const r = await fetch('/api/affiliate/my-summary', { credentials: 'include' });
    if (!r.ok) return;
    _affSummary = await r.json();

    const codeEl = document.getElementById('affMyCode');
    if (codeEl) codeEl.textContent = _affSummary.affiliate_code || '—';

    const urlEl = document.getElementById('affShareUrl');
    if (urlEl && _affSummary.affiliate_code)
      urlEl.textContent = `gigsfill.com/app/signup-new.html?aff=${_affSummary.affiliate_code}&role=venue`;

    const statsEl = document.getElementById('affMyStats');
    if (statsEl) {
      statsEl.innerHTML = [
        ['Linked Venues',  _affSummary.active_venues || 0,                                      '#06b6d4'],
        ['Total Earned',   '$' + ((_affSummary.total_earned_cents||0)/100).toFixed(2),           '#10b981'],
        ['Unpaid Balance', '$' + ((_affSummary.unpaid_cents||0)/100).toFixed(2),                 '#f59e0b'],
        ['YTD '+new Date().getFullYear(), '$'+((_affSummary.ytd_cents||0)/100).toFixed(2),       '#8b5cf6'],
      ].map(([label,val,color]) => `
        <div style="background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:8px;padding:12px 16px;flex:1;min-width:110px;">
          <div style="font-size:1.1rem;font-weight:700;color:${color};">${val}</div>
          <div style="font-size:0.7rem;color:var(--text-gray);margin-top:2px;">${label}</div>
        </div>`).join('');
      Object.assign(statsEl.style, {display:'flex',gap:'8px',flexWrap:'wrap',marginBottom:'16px'});
    }

    _updateAffStripeStatus(_affSummary.has_stripe, _affSummary.stripe_artist_name, _affSummary.stripe_account_id);
    _renderPayoutHistory(_affSummary.payouts || []);
  } catch(e) {}
}

function _updateAffStripeStatus(hasStripe, artistName, accountId) {
  const block = document.getElementById('affStripeBlock');
  if (!block) return;
  const connectedLabel = hasStripe
    ? (artistName
        ? `✅ Stripe Connected — <strong>${esc(artistName)}</strong>`
        : `✅ Stripe Connected <span style="font-size:0.68rem;color:var(--text-gray);">(${accountId ? accountId.slice(-6) : ''})</span>`)
    : null;
  if (hasStripe) {
    block.innerHTML = `<div id="affStripeStatus" style="font-size:0.75rem;color:#10b981;padding:12px 16px;background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.3);border-radius:8px;">
      <div style="margin-bottom:8px;">${connectedLabel}</div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <div style="display:flex;flex-direction:column;gap:4px;flex:1;min-width:160px;">
          <label style="font-size:0.68rem;color:var(--text-gray);">Switch to a different artist account</label>
          <select id="affExistingStripeSelect"
            style="padding:5px 8px;background:rgba(255,255,255,0.05);border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.75rem;cursor:pointer;">
            <option value="">Choose existing account…</option>
          </select>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;align-items:center;">
          <label style="font-size:0.68rem;color:var(--text-gray);">Or connect new</label>
          <button onclick="startAffStripeOnboard()"
            style="padding:5px 14px;background:rgba(245,158,11,0.2);border:1px solid rgba(245,158,11,0.5);border-radius:5px;color:#fbbf24;font-size:0.72rem;cursor:pointer;white-space:nowrap;">
            Connect Stripe →
          </button>
        </div>
      </div>
    </div>`;
    loadAffArtistStripeAccounts();
  } else {
    if (!document.getElementById('affExistingStripeSelect')) {
      block.innerHTML = `<div id="affStripeStatus" style="font-size:0.75rem;color:#f59e0b;padding:12px 16px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.3);border-radius:8px;">
        <div style="margin-bottom:8px;">⚠️ Please set up Stripe to receive your Affiliate payouts…</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <div style="display:flex;flex-direction:column;gap:4px;flex:1;min-width:160px;">
            <label style="font-size:0.68rem;color:var(--text-gray);">Use existing artist account</label>
            <select id="affExistingStripeSelect"
              style="padding:5px 8px;background:rgba(255,255,255,0.05);border:1px solid var(--border);border-radius:5px;color:var(--text);font-size:0.75rem;cursor:pointer;">
              <option value="">Choose existing account…</option>
            </select>
          </div>
          <div style="display:flex;flex-direction:column;gap:4px;align-items:center;">
            <label style="font-size:0.68rem;color:var(--text-gray);">Or connect new</label>
            <button onclick="startAffStripeOnboard()"
              style="padding:5px 14px;background:rgba(245,158,11,0.2);border:1px solid rgba(245,158,11,0.5);border-radius:5px;color:#fbbf24;font-size:0.72rem;cursor:pointer;white-space:nowrap;">
              Connect Stripe →
            </button>
          </div>
        </div>
      </div>`;
      loadAffArtistStripeAccounts();
    }
  }
}

function _renderPayoutHistory(payouts) {
  const el = document.getElementById('affPayoutHistory');
  if (!el) return;
  if (!payouts.length) {
    el.innerHTML = '<div style="text-align:center;padding:12px;color:var(--text-gray);font-size:0.8rem;">No payouts yet</div>';
    return;
  }
  const statusLabel = s => ({
    paid:            '<span style="color:#10b981;font-weight:700;">Paid</span>',
    below_threshold: '<span style="color:#f59e0b;">Below min — rolled over</span>',
    no_stripe:       '<span style="color:#8b5cf6;">No Stripe connected</span>',
    transfer_failed: '<span style="color:#ef4444;">Transfer failed</span>',
    processing:      '<span style="color:#3b82f6;">Processing</span>',
    pending:         '<span style="color:#6b7280;">Pending</span>',
  }[s] || `<span style="color:var(--text-gray);">${esc(s)}</span>`);

  el.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:0.78rem;">
    <thead><tr style="border-bottom:1px solid var(--border);">
      <th style="padding:7px 8px;text-align:left;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Quarter</th>
      <th style="padding:7px 8px;text-align:right;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Amount</th>
      <th style="padding:7px 8px;text-align:center;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Status</th>
      <th style="padding:7px 8px;text-align:right;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Date</th>
    </tr></thead>
    <tbody>${payouts.map(p => `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
        <td style="padding:7px 8px;color:var(--text);">${esc(p.quarter)}</td>
        <td style="padding:7px 8px;text-align:right;color:#10b981;font-weight:600;">$${((p.total_cents||0)/100).toFixed(2)}</td>
        <td style="padding:7px 8px;text-align:center;">${statusLabel(p.status)}</td>
        <td style="padding:7px 8px;text-align:right;color:var(--text-gray);font-size:0.72rem;">${p.paid_at ? new Date(p.paid_at).toLocaleDateString() : '—'}</td>
      </tr>`).join('')}
    </tbody></table>`;
}

// ── Copy Link ─────────────────────────────────────────────────────────────────

function copyAffCode() {
  const code = _affSummary?.affiliate_code;
  if (!code) return;
  // Audit fix (May 2026 part 9d): direct venue-signup link, not homepage.
  // Affiliate program is about getting VENUES signed up — drop recipients
  // straight onto the venue signup form, cookie set, ready to fill out.
  const url = `https://gigsfill.com/app/signup-new.html?aff=${code}&role=venue`;

  // Build modal if not already present
  let modal = document.getElementById('affCopyLinkModal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'affCopyLinkModal';
    modal.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:9000;align-items:center;justify-content:center;';
    modal.addEventListener('click', function(e) { if (e.target === modal) modal.style.display = 'none'; });
    modal.innerHTML = `
      <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:28px 28px 24px;max-width:480px;width:90%;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
          <div style="font-size:1rem;font-weight:700;color:var(--text);">Your Affiliate Link</div>
          <button onclick="document.getElementById('affCopyLinkModal').style.display='none'"
            style="background:transparent;border:none;color:var(--text-gray);font-size:1.2rem;cursor:pointer;padding:0 4px;">✕</button>
        </div>
        <p style="font-size:0.82rem;color:var(--text-gray);margin:0 0 16px;line-height:1.6;">
          Share this link with venue owners. When they sign up using it, they'll be permanently linked to your affiliate account and you'll earn a commission on every gig they book.
        </p>
        <div style="display:flex;gap:8px;align-items:center;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:8px;padding:10px 14px;">
          <span id="affCopyLinkUrl" style="flex:1;font-size:0.82rem;color:var(--cyan);font-family:monospace;word-break:break-all;"></span>
          <button id="affCopyLinkBtn" onclick="window._doCopyAffLink()"
            style="flex-shrink:0;padding:6px 14px;background:rgba(139,92,246,0.2);border:1px solid rgba(139,92,246,0.4);border-radius:6px;color:#c4b5fd;font-size:0.78rem;cursor:pointer;font-weight:600;white-space:nowrap;">
            Copy
          </button>
        </div>
        <div id="affCopyLinkResult" style="font-size:0.75rem;color:#10b981;margin-top:8px;opacity:0;transition:opacity .3s;">✓ Copied to clipboard!</div>
      </div>`;
    document.body.appendChild(modal);
  }

  document.getElementById('affCopyLinkUrl').textContent = url;
  modal.style.display = 'flex';
}

function _doCopyAffLink() {
  const code = _affSummary?.affiliate_code;
  if (!code) return;
  const url = `https://gigsfill.com/app/signup-new.html?aff=${code}&role=venue`;
  const btn = document.getElementById('affCopyLinkBtn');
  const result = document.getElementById('affCopyLinkResult');
  navigator.clipboard.writeText(url).then(() => {
    if (btn) { btn.textContent = '✓ Copied!'; btn.style.color = '#10b981'; }
    if (result) { result.style.opacity = '1'; }
    setTimeout(() => {
      if (btn) { btn.textContent = 'Copy'; btn.style.color = ''; }
      if (result) { result.style.opacity = '0'; }
    }, 2500);
  }).catch(() => {
    // Fallback: select the text
    const span = document.getElementById('affCopyLinkUrl');
    if (span) {
      const range = document.createRange();
      range.selectNode(span);
      window.getSelection().removeAllRanges();
      window.getSelection().addRange(range);
    }
  });
}
window._doCopyAffLink = _doCopyAffLink;

// ── Recommend Form ────────────────────────────────────────────────────────────
// (Jun 2026) The inline form + toggleRecommendForm / sendAffRecommend
// were deleted in favor of the same modal the dropdown's "Recommend
// GigsFill" entry opens (openRecommendModal in user-dropdown.js). Single
// source of truth for the affiliate-recommend flow. After a successful
// send the modal's submit handler dispatches `affRecommendSent` so the
// "Sent Recommendations" table on this page refreshes — see the
// listener wired below.
window.addEventListener('affRecommendSent', () => {
  if (typeof loadAffMyEmails === 'function') loadAffMyEmails();
});

// ── Sent Emails List ──────────────────────────────────────────────────────────

async function loadAffMyEmails() {
  const el = document.getElementById('affEmailsList');
  if (!el) return;
  try {
    const r = await fetch('/api/affiliate/my-emails', { credentials: 'include' });
    if (!r.ok) { el.innerHTML = ''; return; }
    const emails = await r.json();

    if (!emails.length) {
      el.innerHTML = '<div style="font-size:0.78rem;color:var(--text-gray);text-align:center;padding:8px 0;">No recommendations sent yet. Send one above!</div>';
      return;
    }

    const badge = s => ({
      converted:         '<span style="font-size:0.65rem;padding:2px 7px;border-radius:10px;background:rgba(16,185,129,0.15);color:#10b981;font-weight:700;">✓ Converted</span>',
      claimed_by_other:  '<span style="font-size:0.65rem;padding:2px 7px;border-radius:10px;background:rgba(245,158,11,0.12);color:#f59e0b;">Claimed by other</span>',
      signed_up_no_link: '<span style="font-size:0.65rem;padding:2px 7px;border-radius:10px;background:rgba(59,130,246,0.12);color:#60a5fa;">Signed up</span>',
      sent:              '<span style="font-size:0.65rem;padding:2px 7px;border-radius:10px;background:rgba(107,114,128,0.15);color:#9ca3af;">Sent</span>',
    }[s] || '');

    el.innerHTML = `
      <div style="font-size:0.72rem;font-weight:700;color:var(--text-gray);margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em;">Sent Recommendations</div>
      <table style="width:100%;border-collapse:collapse;">
        <thead><tr style="border-bottom:1px solid var(--border);">
          <th style="padding:5px 8px;text-align:left;font-size:0.68rem;color:var(--text-gray);font-weight:600;">Recipient</th>
          <th style="padding:5px 8px;text-align:left;font-size:0.68rem;color:var(--text-gray);font-weight:600;">Email</th>
          <th style="padding:5px 8px;text-align:center;font-size:0.68rem;color:var(--text-gray);font-weight:600;">Sent</th>
          <th style="padding:5px 8px;text-align:center;font-size:0.68rem;color:var(--text-gray);font-weight:600;">Status</th>
          <th style="padding:5px 8px;"></th>
        </tr></thead>
        <tbody>${emails.map(e => `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
            <td style="padding:6px 8px;color:var(--text);font-size:0.78rem;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
              ${e.recipient_name ? esc(e.recipient_name) : '<span style="color:var(--text-gray);">—</span>'}
            </td>
            <td style="padding:6px 8px;color:var(--text-gray);font-size:0.75rem;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
              ${esc(e.recipient_email)}
            </td>
            <td style="padding:6px 8px;text-align:center;color:var(--text-gray);font-size:0.72rem;white-space:nowrap;">
              ${new Date(e.sent_at).toLocaleDateString()}
            </td>
            <td style="padding:6px 8px;text-align:center;">${badge(e.status)}</td>
            <td style="padding:6px 8px;text-align:right;">
              <button onclick="window.resendAffRecommend(${e.id}, this)"
                style="padding:3px 10px;background:rgba(6,182,212,0.12);border:1px solid rgba(6,182,212,0.3);border-radius:4px;color:var(--cyan);font-size:0.68rem;cursor:pointer;white-space:nowrap;">
                Resend
              </button>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  } catch(e) { el.innerHTML = ''; }
}

async function resendAffRecommend(emailId, btn) {
  const orig = btn.textContent;
  btn.textContent = '…'; btn.disabled = true;
  try {
    const r = await fetch(`/api/affiliate/resend-recommend/${emailId}`, { method:'POST', credentials:'include' });
    let d = {};
    try { d = await r.json(); } catch (_) {}
    if (r.ok && d.ok) {
      btn.textContent = '✓ Sent!'; btn.style.color = '#10b981'; btn.style.fontWeight = '700';
      setTimeout(async () => {
        btn.textContent = orig; btn.style.color = ''; btn.style.fontWeight = '';
        btn.disabled = false;
        await loadAffMyEmails();
      }, 2500);
    } else {
      // Surface the backend's reason (cooldown, cap reached, kill switch, etc.)
      // rather than the generic "Error" that hid what was happening.
      const reason = (d && d.detail) ? String(d.detail) : (r.status === 429 ? 'Slow down — try later' : 'Error');
      btn.textContent = reason.length > 40 ? reason.slice(0, 38) + '…' : reason;
      btn.title = reason;
      btn.style.color = '#ef4444';
      setTimeout(() => {
        btn.textContent = orig; btn.style.color = ''; btn.title = ''; btn.disabled = false;
      }, 4500);
    }
  } catch(e) { btn.textContent = orig; btn.disabled = false; }
}

// ── Referrals List (expandable rows) ──────────────────────────────────────────

let _affReferrals = [];

async function loadAffMyReferrals() {
  const el = document.getElementById('affReferralsList');
  if (!el) return;
  try {
    const r = await fetch('/api/affiliate/my-referrals', { credentials: 'include' });
    if (!r.ok) { el.innerHTML = '<div style="color:var(--text-gray);font-size:0.8rem;text-align:center;padding:12px;">No referrals yet</div>'; return; }
    _affReferrals = await r.json();
    _renderAffReferrals(el);
  } catch(e) {
    el.innerHTML = '<div style="color:#ef4444;font-size:0.78rem;text-align:center;padding:12px;">Error loading referrals</div>';
  }
}

function _renderAffReferrals(el) {
  if (!_affReferrals.length) {
    el.innerHTML = `<div style="text-align:center;padding:20px;color:var(--text-gray);font-size:0.8rem;">
      No venues linked yet.<br><span style="font-size:0.72rem;">When a venue signs up using your link or recommendation, they'll appear here.</span></div>`;
    return;
  }
  const hasEarnings = _affReferrals.some(rv => rv.gig_count > 0);
  let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
    <div style="font-size:0.72rem;font-weight:700;color:var(--text-gray);text-transform:uppercase;letter-spacing:.04em;">Referred Venues & Earnings</div>
    ${hasEarnings ? `<div style="display:flex;gap:6px;">
      <button onclick="exportAffEarnings('pdf')" style="padding:4px 12px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);border-radius:5px;color:#f87171;font-size:0.68rem;font-weight:600;cursor:pointer;">PDF</button>
      <button onclick="exportAffEarnings('excel')" style="padding:4px 12px;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);border-radius:5px;color:#34d399;font-size:0.68rem;font-weight:600;cursor:pointer;">Excel</button>
    </div>` : ''}
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:0.78rem;">
    <thead><tr style="border-bottom:1px solid var(--border);">
      <th style="padding:7px 8px;text-align:left;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Venue</th>
      <th style="padding:7px 4px;text-align:center;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Gigs</th>
      <th style="padding:7px 8px;text-align:right;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Total Earned</th>
      <th style="padding:7px 8px;text-align:right;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Unpaid</th>
      <th style="padding:7px 8px;text-align:right;color:var(--text-gray);font-size:0.7rem;font-weight:600;">Rate</th>
    </tr></thead>
    <tbody id="affReferralsBody">`;

  _affReferrals.forEach((rv, idx) => {
    const linkedDays = Math.floor((Date.now() - new Date(rv.linked_at)) / 86400000);
    // Audit fix (May 2026 part 9c): prefer server-computed `current_rate_percent`
    // (live admin setting) over the row snapshot. Falls back to the snapshot if
    // the server is older or current_rate_percent didn't come through.
    const curRate = (rv.current_rate_percent !== undefined && rv.current_rate_percent !== null)
      ? rv.current_rate_percent
      : (linkedDays >= (rv.reduced_after_days||365) ? rv.reduced_rate_percent : rv.initial_rate_percent);
    const hasGigs = (rv.gig_count||0) > 0;
    html += `<tr id="affVenueRow_${rv.venue_id}"
        onclick="${hasGigs ? `toggleAffVenueExpand(${rv.venue_id}, this)` : ''}"
        style="border-bottom:1px solid rgba(255,255,255,0.04);${hasGigs ? 'cursor:pointer;' : ''}transition:background .15s;"
        onmouseover="if(${hasGigs}) this.style.background='rgba(255,255,255,0.03)'"
        onmouseout="this.style.background=''">
      <td style="padding:7px 8px;color:var(--text);">
        ${hasGigs ? `<span style="font-size:0.68rem;color:var(--text-muted);margin-right:4px;" id="affVenueChevron_${rv.venue_id}">▶</span>` : ''}
        ${esc(rv.venue_name)}
        <div style="font-size:0.68rem;color:var(--text-gray);">${esc(rv.city||'')}${rv.state?', '+esc(rv.state):''}</div>
      </td>
      <td style="padding:7px 4px;text-align:center;">${rv.gig_count||0}</td>
      <td style="padding:7px 8px;text-align:right;color:#10b981;font-weight:600;">$${((rv.total_earned_cents||0)/100).toFixed(2)}</td>
      <td style="padding:7px 8px;text-align:right;color:${(rv.unpaid_cents||0)>0?'#f59e0b':'var(--text-gray)'};">$${((rv.unpaid_cents||0)/100).toFixed(2)}</td>
      <td style="padding:7px 8px;text-align:right;color:var(--text-gray);">${curRate}%
        ${linkedDays < (rv.reduced_after_days||365) ? `<div style="font-size:0.65rem;">${(rv.reduced_after_days||365)-linkedDays}d until ${rv.reduced_rate_percent}%</div>` : ''}
      </td>
    </tr>
    <tr id="affVenueExpand_${rv.venue_id}" style="display:none;">
      <td colspan="5" style="padding:0;background:rgba(6,182,212,0.03);border-bottom:1px solid var(--border);">
        <div id="affVenueExpandContent_${rv.venue_id}" style="padding:12px 16px;"></div>
      </td>
    </tr>`;
  });

  html += '</tbody></table>';
  el.innerHTML = html;
}

const _affVenuePages = {};
const _affVenueOpen = {};

async function toggleAffVenueExpand(venueId, rowEl) {
  const expandRow = document.getElementById(`affVenueExpand_${venueId}`);
  const chevron = document.getElementById(`affVenueChevron_${venueId}`);
  if (!expandRow) return;

  if (_affVenueOpen[venueId]) {
    expandRow.style.display = 'none';
    if (chevron) chevron.textContent = '▶';
    _affVenueOpen[venueId] = false;
  } else {
    expandRow.style.display = '';
    if (chevron) chevron.textContent = '▼';
    _affVenueOpen[venueId] = true;
    await loadAffVenueEarnings(venueId, _affVenuePages[venueId] || 1);
  }
}

async function loadAffVenueEarnings(venueId, page) {
  _affVenuePages[venueId] = page;
  const container = document.getElementById(`affVenueExpandContent_${venueId}`);
  if (!container) return;
  container.innerHTML = '<div style="color:var(--text-gray);font-size:0.78rem;padding:8px 0;">Loading…</div>';
  try {
    const r = await fetch(`/api/affiliate/my-venue-earnings/${venueId}?page=${page}&limit=10`, { credentials: 'include' });
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    const { earnings, total, pages } = data;

    if (!earnings.length) {
      container.innerHTML = '<div style="color:var(--text-gray);font-size:0.78rem;padding:8px 0;">No gig earnings yet for this venue.</div>';
      return;
    }

    // Quarter label helper: "2026-Q1" → "2026-Q1 (4-1-2026)"
    const quarterPayDate = q => {
      const m = q && q.match(/^(\d{4})-Q([1-4])$/);
      if (!m) return q || '';
      const payMap = {'1':'4-1','2':'7-1','3':'10-1','4':'12-31'};
      return `${q} (${payMap[m[2]]}-${m[1]})`;
    };

    // Fixed equal-width columns - all 7 columns at ~14% each
    let html = `<table style="width:100%;border-collapse:collapse;font-size:0.74rem;margin-bottom:8px;table-layout:fixed;">
      <colgroup>
        <col style="width:13%">
        <col style="width:22%">
        <col style="width:12%">
        <col style="width:8%">
        <col style="width:11%">
        <col style="width:22%">
        <col style="width:12%">
      </colgroup>
      <thead><tr style="border-bottom:1px solid rgba(255,255,255,0.08);">
        <th style="padding:6px 8px;text-align:left;color:var(--text-gray);font-size:0.68rem;font-weight:600;">Date</th>
        <th style="padding:6px 8px;text-align:left;color:var(--text-gray);font-size:0.68rem;font-weight:600;">Gig / Artist</th>
        <th style="padding:6px 8px;text-align:right;color:var(--text-gray);font-size:0.68rem;font-weight:600;">Gig Fee</th>
        <th style="padding:6px 8px;text-align:center;color:var(--text-gray);font-size:0.68rem;font-weight:600;">Rate</th>
        <th style="padding:6px 8px;text-align:right;color:var(--text-gray);font-size:0.68rem;font-weight:600;">Earned</th>
        <th style="padding:6px 8px;text-align:left;color:var(--text-gray);font-size:0.68rem;font-weight:600;">Quarter (Pays)</th>
        <th style="padding:6px 8px;text-align:center;color:var(--text-gray);font-size:0.68rem;font-weight:600;">Status</th>
      </tr></thead>
      <tbody>`;

    earnings.forEach(e => {
      const dt = e.gig_date ? new Date(e.gig_date + 'T00:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}) : '—';
      const title = e.gig_title || (e.artist_name ? 'w/ ' + esc(e.artist_name) : 'Gig');
      const statusColor = e.payout_id ? '#10b981' : '#f59e0b';
      const statusLabel = e.payout_status === 'paid' ? 'Paid' : e.payout_id ? esc(e.payout_status||'Processing') : 'Unpaid';
      const _td = (val, align, color, extra) => `<td style="padding:6px 8px;text-align:${align};color:${color||'var(--text-gray)'};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.74rem;${extra||''}">${val}</td>`;
      html += `<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
        ${_td(dt, 'left', 'var(--text-gray)')}
        ${_td(`<span title="${esc(title)}">${esc(title)}</span>`, 'left', 'var(--text)')}
        ${_td('$'+((e.gig_fee_cents||0)/100).toFixed(2), 'right', 'var(--text)')}
        ${_td(e.rate_percent+'%', 'center', 'var(--text-gray)')}
        ${_td('$'+((e.earned_cents||0)/100).toFixed(2), 'right', '#10b981', 'font-weight:600;')}
        ${_td(quarterPayDate(e.quarter), 'left', 'var(--text-gray)', 'font-size:0.68rem;')}
        ${_td(statusLabel, 'center', statusColor, 'font-weight:600;')}
      </tr>`;
    });
    html += '</tbody></table>';

    // Pagination
    if (pages > 1) {
      html += `<div style="display:flex;justify-content:flex-end;align-items:center;gap:6px;font-size:0.72rem;color:var(--text-gray);">
        <span>Page ${page} of ${pages}</span>
        ${page > 1 ? `<button onclick="loadAffVenueEarnings(${venueId},${page-1})" style="padding:3px 10px;background:rgba(255,255,255,0.06);border:1px solid var(--border);border-radius:4px;color:var(--text-gray);font-size:0.7rem;cursor:pointer;">◀ Prev</button>` : ''}
        ${page < pages ? `<button onclick="loadAffVenueEarnings(${venueId},${page+1})" style="padding:3px 10px;background:rgba(255,255,255,0.06);border:1px solid var(--border);border-radius:4px;color:var(--text-gray);font-size:0.7rem;cursor:pointer;">Next ▶</button>` : ''}
      </div>`;
    }

    container.innerHTML = html;
  } catch(e) {
    container.innerHTML = '<div style="color:#ef4444;font-size:0.75rem;padding:8px 0;">Error loading earnings</div>';
  }
}

// ── Export Affiliate Earnings ────────────────────────────────────────────────

async function exportAffEarnings(fmt) {
  // Fetch ALL earnings across ALL venues
  let allRows = [];
  for (const rv of _affReferrals) {
    if (!rv.gig_count) continue;
    let page = 1;
    while (true) {
      const r = await fetch(`/api/affiliate/my-venue-earnings/${rv.venue_id}?page=${page}&limit=100`, { credentials: 'include' });
      if (!r.ok) break;
      const data = await r.json();
      data.earnings.forEach(e => allRows.push({ ...e, venue_name: rv.venue_name, city: rv.city, state: rv.state }));
      if (page >= data.pages) break;
      page++;
    }
  }

  if (!allRows.length) { alert('No earnings to export.'); return; }

  const headers = ['Venue','City','State','Date','Gig Title','Artist','Gig Fee','Rate %','Earned','Quarter','Status'];
  const rows = allRows.map(e => [
    e.venue_name||'', e.city||'', e.state||'',
    e.gig_date||'', e.gig_title||'', e.artist_name||'',
    ((e.gig_fee_cents||0)/100).toFixed(2),
    e.rate_percent||'',
    ((e.earned_cents||0)/100).toFixed(2),
    e.quarter||'',
    e.payout_status || (e.payout_id ? 'Processing' : 'Unpaid'),
  ]);

  if (fmt === 'excel') {
    const csvLines = [headers, ...rows].map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(','));
    let csv = csvLines.join('\r\n')
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = `affiliate-earnings-${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
  } else {
    // PDF via print-friendly HTML
    const totalEarned = allRows.reduce((s,e) => s + (e.earned_cents||0), 0);
    let tableRows = rows.map(r => `<tr>${r.map((v,i) => `<td style="border:1px solid #ddd;padding:4px 8px;font-size:11px;${i>=6&&i<=8?'text-align:right;':''}">${v}</td>`).join('')}</tr>`).join('');
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Affiliate Earnings</title>
    <style>body{font-family:Arial,sans-serif;margin:20px;}h2{color:#1a1a2e;}table{border-collapse:collapse;width:100%;}th{background:#1a1a2e;color:#fff;padding:6px 8px;font-size:11px;text-align:left;}
    .total{font-weight:700;margin-top:10px;text-align:right;}</style></head>
    <body><h2>Affiliate Earnings Report</h2><p style="font-size:12px;color:#555;">Exported ${new Date().toLocaleDateString()}</p>
    <table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${tableRows}</tbody></table>
    <div class="total">Total Earned: $${(totalEarned/100).toFixed(2)}</div>
    <script>window.onload=()=>{window.print();}<\/script></body></html>`;
    const w = window.open('','_blank'); w.document.write(html); w.document.close();
  }
}

// ── Stripe Connect ────────────────────────────────────────────────────────────

async function startAffStripeOnboard() {
  const btn = event.target; const orig = btn.textContent;
  btn.textContent = 'Loading…'; btn.disabled = true;
  try {
    const r = await fetch('/api/affiliate/stripe/onboard', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'} });
    const d = await r.json();
    if (d.url) window.location.href = d.url;
    else { btn.textContent = 'Error — try again'; btn.disabled = false; }
  } catch(e) { btn.textContent=orig; btn.disabled=false; }
}

// Fix: clicking Connect Stripe → browser back leaves the button stuck on
// "Loading…" because the browser may restore the page from BFCache exactly
// as it was at navigation time. Reset the button BEFORE leaving so the
// cached snapshot is already clean, AND re-render on return as defense in
// depth. `pagehide` fires reliably whether BFCache is used or not;
// `pageshow` is the BFCache restore signal; `visibilitychange` covers the
// case where the user comes back via tab focus rather than a navigation.
function _resetAffStripeButtonIfLoading() {
  const block = document.getElementById('affStripeBlock');
  if (!block) return;
  const btns = block.querySelectorAll('button');
  btns.forEach(function(b) {
    const t = (b.textContent || '').trim();
    if (t === 'Loading…' || t === 'Loading...' || b.disabled) {
      b.textContent = 'Connect Stripe →';
      b.disabled = false;
    }
  });
}

window.addEventListener('pagehide', _resetAffStripeButtonIfLoading);
window.addEventListener('pageshow', function(e) {
  // Reset on every pageshow — covers BFCache restore (e.persisted=true) AND
  // the case where the browser opted out of BFCache and did a full reload
  // but somehow preserved the in-memory button state.
  _resetAffStripeButtonIfLoading();
  if (e.persisted && _affSummary) {
    _updateAffStripeStatus(_affSummary.has_stripe, _affSummary.stripe_artist_name, _affSummary.stripe_account_id);
  }
});
document.addEventListener('visibilitychange', function() {
  if (document.visibilityState === 'visible') _resetAffStripeButtonIfLoading();
});

// ── URL param auto-open ───────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  if (params.get('tab') === 'affiliates') {
    const btn = document.querySelector('.tab[onclick*="affiliates"]');
    if (btn) btn.click();
  }
});

// ── W9 / Tax Info ─────────────────────────────────────────────────────────────

async function loadAffW9() {
  const uid = window._currentUserId;
  if (!uid) return;
  try {
    const r = await fetch(`/api/users/${uid}/w9`, { credentials: 'include' });
    if (!r.ok) return;
    const resp = await r.json();
    const d = resp.w9;  // API returns {w9: {...}, tax_year: N}
    if (!d || !d.tax_name) return;

    const statusEl = document.getElementById('affW9Status');
    if (statusEl) {
      const yr = d.tax_year || resp.tax_year || new Date().getFullYear();
      statusEl.textContent = `Filed ${yr}`;
      Object.assign(statusEl.style, {background:'rgba(16,185,129,0.1)',color:'#10b981',borderColor:'rgba(16,185,129,0.3)'});
    }
    const summaryEl = document.getElementById('affW9Summary');
    if (summaryEl) summaryEl.textContent = `${d.tax_name}${d.tin_last4?` · TIN ••••${d.tin_last4}`:''}${d.city?` · ${d.city}`:''}`;

    const filedEl = document.getElementById('affW9Filed');
    if (filedEl) filedEl.style.display = 'flex';
    const toggleBtn = document.getElementById('affW9ToggleBtn');
    if (toggleBtn) toggleBtn.style.display = 'none';

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
    set('w9TaxName', d.tax_name); set('w9BusinessName', d.business_name);
    set('w9Classification', d.tax_classification); set('w9TinType', d.tin_type);
    set('w9Address1', d.address_line_1); set('w9City', d.city);
    set('w9State', d.state); set('w9Zip', d.zip_code);

    // Audit fix (May 2026 part 9c): API returns {records:[...], threshold_cents:N}
    // not a flat array, AND the row field is `total_cents` not `total_earned_cents`.
    // Both bugs combined meant the 1099 history block never rendered.
    const histResp = await fetch(`/api/users/${uid}/affiliate-1099s`, { credentials:'include' })
      .then(r => r.ok ? r.json() : null).catch(() => null);
    const histRows = (histResp && Array.isArray(histResp.records)) ? histResp.records : [];
    if (histRows.length) {
      const histEl = document.getElementById('affTaxHistory');
      const listEl = document.getElementById('aff1099List');
      if (histEl) histEl.style.display = 'block';
      if (listEl) listEl.innerHTML = histRows.map(h => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:0.75rem;">
          <span style="color:var(--text);">${esc(h.tax_year)} 1099-NEC</span>
          <span style="color:#10b981;font-weight:600;">$${((h.total_cents||0)/100).toFixed(2)}</span>
          <span style="font-size:0.68rem;color:var(--text-gray);">${esc(h.status||'generated')}</span>
        </div>`).join('');
    }
  } catch(e) {}
}

function toggleAffW9Form() {
  const form = document.getElementById('affW9Form');
  if (form) form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

async function saveAffW9() {
  const uid = window._currentUserId;
  if (!uid) return;
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const certified = document.getElementById('w9Certified')?.checked;

  if (!get('w9TaxName')) {
    const r = document.getElementById('affW9SaveResult');
    if (r) { r.style.color='#ef4444'; r.textContent='Legal name is required.'; r.style.opacity='1'; }
    return;
  }
  if (!get('w9Tin')) {
    const r = document.getElementById('affW9SaveResult');
    if (r) { r.style.color='#ef4444'; r.textContent='TIN is required.'; r.style.opacity='1'; }
    return;
  }
  const payload = {
    tax_name: get('w9TaxName'),
    business_name: get('w9BusinessName'),
    tax_classification: get('w9Classification'),
    tin_type: get('w9TinType'),
    tin: get('w9Tin'),
    address_line_1: get('w9Address1'),
    city: get('w9City'),
    state: get('w9State').toUpperCase(),
    zip_code: get('w9Zip'),
    certified_at: certified ? new Date().toISOString() : null,
  };
  try {
    const r = await fetch(`/api/users/${uid}/w9`, {
      method:'PUT', credentials:'include',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    const d = await r.json();
    const resultEl = document.getElementById('affW9SaveResult');
    if (r.ok) {
      if (resultEl) { resultEl.style.color='#10b981'; resultEl.textContent='✓ Saved'; resultEl.style.opacity='1'; setTimeout(()=>resultEl.style.opacity='0',2500); }
      await loadAffW9();
    } else {
      if (resultEl) { resultEl.style.color='#ef4444'; resultEl.textContent=d.detail||'Save failed'; resultEl.style.opacity='1'; }
    }
  } catch(e) {
    const r = document.getElementById('affW9SaveResult');
    if (r) { r.style.color='#ef4444'; r.textContent='Request failed'; r.style.opacity='1'; }
  }
}

async function loadAffArtistStripeAccounts() {
  const select = document.getElementById('affExistingStripeSelect');
  if (!select) return;
  try {
    const r = await fetch('/api/affiliate/artist-stripe-accounts', { credentials: 'include' });
    if (!r.ok) return;
    const accounts = await r.json();
    // Remove old artist options
    Array.from(select.options).forEach(o => { if (o.value) o.remove(); });
    if (!accounts.length) {
      select.style.display = 'none';
      select.previousElementSibling && (select.previousElementSibling.style.display = 'none');
      return;
    }
    accounts.forEach(function(a) {
      const opt = document.createElement('option');
      opt.value = a.artist_id;
      opt.textContent = a.artist_name + ' (Stripe connected)';
      select.appendChild(opt);
    });
    select.style.display = '';
    select.onchange = async function() {
      const artistId = parseInt(select.value);
      if (!artistId) return;
      select.disabled = true;
      try {
        const res = await fetch('/api/affiliate/use-artist-stripe', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ artist_id: artistId })
        });
        const d = await res.json();
        if (d.ok) {
          await loadAffSummary();
        } else {
          alert('Could not link that Stripe account. Please try connecting a new one.');
          select.value = '';
        }
      } catch(e) {
        select.value = '';
      }
      select.disabled = false;
    };
  } catch(e) {}
}

window.loadAffArtistStripeAccounts = loadAffArtistStripeAccounts;

// ── New-affiliate W9 popup ────────────────────────────────────────────────────

async function checkAffW9Prompt() {
  try {
    const r = await fetch('/api/affiliate/check-new-venues', { credentials: 'include' });
    if (!r.ok) return;
    const d = await r.json();
    // Two prerequisites for payout: W-9 + Stripe Connect. Show whichever
    // (or both) is still missing. If both are done, no popup.
    if (!d.needs_w9_prompt && !d.needs_stripe_prompt) return;

    // Build the missing-steps list dynamically so the message matches
    // what the user actually still has to do.
    const missing = [];
    if (d.needs_stripe_prompt) missing.push({ key: 'stripe', label: 'Stripe payout account' });
    if (d.needs_w9_prompt)     missing.push({ key: 'w9',     label: 'W-9 tax form' });
    const missingNames = missing.map(m => m.label).join(' and ');
    const buttonLabel  = missing.length === 1
      ? `Set up ${missing[0].label} →`
      : `Set up payouts →`;

    const modal = document.createElement('div');
    modal.id = 'affW9PromptModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:99999;display:flex;align-items:center;justify-content:center;padding:20px;';
    modal.innerHTML = `
      <div style="background:#1a1f2e;border:1px solid rgba(16,185,129,0.4);border-radius:14px;padding:32px 36px;max-width:480px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.6);text-align:center;">
        <div style="font-size:2.5rem;margin-bottom:12px;">🎉</div>
        <h2 style="margin:0 0 10px;font-size:1.2rem;font-weight:700;color:#10b981;">You're now affiliated with ${d.referral_count} venue${d.referral_count>1?'s':''}!</h2>
        <p style="margin:0 0 20px;font-size:0.88rem;line-height:1.6;color:var(--text-gray);">
          Congrats — you'll earn a commission on every gig booked at the venue${d.referral_count>1?'s':''} you referred.<br><br>
          <strong style="color:#f59e0b;">Before we can pay you:</strong> Please complete your ${missingNames}. We can't release affiliate payouts until ${missing.length === 1 ? 'this is' : 'these are'} on file.
        </p>
        <div style="display:flex;flex-direction:column;gap:10px;">
          <button id="affPayoutSetupGoBtn"
            data-missing="${missing.map(m=>m.key).join(',')}"
            style="padding:13px 24px;background:linear-gradient(135deg,#10b981,#059669);border:none;border-radius:8px;color:#fff;font-size:0.95rem;font-weight:700;cursor:pointer;"
            onclick="window._doAffPayoutSetup(this)">
            ${buttonLabel}
          </button>
          <button
            style="padding:8px 16px;background:transparent;border:1px solid rgba(255,255,255,0.15);border-radius:8px;color:var(--text-gray);font-size:0.78rem;cursor:pointer;"
            onclick="window._dismissAffSetupPrompt()" data-missing="${missing.map(m=>m.key).join(',')}">
            Remind me later
          </button>
        </div>
      </div>`;
    document.body.appendChild(modal);
  } catch(e) {}
}

// Send user to the right setup affordance(s). If both are missing, jump
// to the Stripe block first since W-9 is filed inline near it on the
// same page (and the Affiliate setup checklist banner stays visible
// until both are done).
window._doAffPayoutSetup = function(btn) {
  const modal = document.getElementById('affPayoutSetupGoBtn')?.closest('#affW9PromptModal') || document.getElementById('affW9PromptModal');
  if (modal) modal.remove();
  const missing = (btn && btn.getAttribute('data-missing') || '').split(',').filter(Boolean);
  const affTab = document.querySelector('[onclick*="affiliates"]');
  if (affTab) affTab.click();
  setTimeout(() => {
    if (missing.includes('stripe')) {
      const block = document.getElementById('affStripeBlock');
      if (block) block.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (missing.includes('w9')) {
      const toggleBtn = document.getElementById('affW9ToggleBtn');
      if (toggleBtn) toggleBtn.click();
      const w9Form = document.getElementById('affW9Form');
      if (w9Form) { w9Form.style.display = 'block'; w9Form.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    }
  }, 400);
};

// Dismiss both prompts the user is currently being shown. Backend has
// separate flags per prompt so we POST to whichever endpoints apply.
window._dismissAffSetupPrompt = async function() {
  const btn = document.querySelector('#affW9PromptModal button[onclick*="_dismissAffSetupPrompt"]');
  const modal = document.getElementById('affW9PromptModal');
  if (modal) modal.remove();
  const missing = (btn && btn.getAttribute('data-missing') || '').split(',').filter(Boolean);
  try {
    if (missing.includes('w9')) {
      await fetch('/api/affiliate/dismiss-w9-prompt', { method: 'POST', credentials: 'include' });
    }
    if (missing.includes('stripe')) {
      await fetch('/api/affiliate/dismiss-stripe-prompt', { method: 'POST', credentials: 'include' });
    }
  } catch(e) {}
};

// Back-compat: existing button onclicks in old cached HTML still
// reference these names. Route them to the new combined handlers.
window._doAffW9Prompt        = function() { return window._doAffPayoutSetup({ getAttribute: () => 'w9' }); };
window._dismissAffW9Prompt   = function() {
  const modal = document.getElementById('affW9PromptModal');
  if (modal) modal.remove();
  return fetch('/api/affiliate/dismiss-w9-prompt', { method: 'POST', credentials: 'include' }).catch(() => {});
};

window.loadAffMyEmails         = loadAffMyEmails;
window.loadAffiliatesPage      = loadAffiliatesPage;
window.copyAffCode             = copyAffCode;
// toggleRecommendForm + sendAffRecommend removed (Jun 2026) — the inline
// form was replaced by openRecommendModal in user-dropdown.js. See the
// `affRecommendSent` event listener above for the refresh hook.
window.startAffStripeOnboard   = startAffStripeOnboard;
window.toggleAffW9Form         = toggleAffW9Form;
window.saveAffW9               = saveAffW9;
window.resendAffRecommend      = resendAffRecommend;
window.toggleAffVenueExpand    = toggleAffVenueExpand;
window.loadAffVenueEarnings    = loadAffVenueEarnings;
window.exportAffEarnings       = exportAffEarnings;
window.checkAffW9Prompt        = checkAffW9Prompt;
