// Part 10p Phase 2 — Global "My Invites" page logic
// Loads /api/me/invitations, groups by token (so one multi-venue invite renders
// as one card with per-venue pills), supports filtering by status and resending
// (24h cooldown enforced server-side).

(function () {
  const STATUS_LABELS = {
    pending: 'Pending',
    bounced: 'Bounced',
    signed_up: 'Signed Up',
    preferred_requested: 'Preferred Requested',
    preferred_approved: 'Preferred Approved',
    preferred_denied: 'Preferred Denied',
    declined: 'Declined',
    expired: 'Expired',
  };

  let allRows = [];
  let activeFilter = 'all';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function relTime(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso.endsWith && iso.endsWith('Z') ? iso : iso + 'Z');
      const s = Math.floor((Date.now() - d.getTime()) / 1000);
      if (s < 60) return 'just now';
      if (s < 3600) return Math.floor(s / 60) + 'm ago';
      if (s < 86400) return Math.floor(s / 3600) + 'h ago';
      if (s < 2592000) return Math.floor(s / 86400) + 'd ago';
      return d.toLocaleDateString();
    } catch (_) { return ''; }
  }

  function groupByToken(rows) {
    // One token = one card. Rows without a token (legacy single-venue
    // invitations from before part 10p) get one card per row keyed by id.
    const map = new Map();
    for (const r of rows) {
      const key = r.token || ('legacy-' + r.id);
      if (!map.has(key)) {
        map.set(key, {
          token: r.token || null,
          invited_email: r.invited_email,
          inviter_name: r.inviter_name,
          message: r.message,
          sent_at: r.sent_at,
          last_resent_at: r.last_resent_at,
          resent_count: r.resent_count || 0,
          venues: [],
        });
      }
      const grp = map.get(key);
      grp.venues.push({
        venue_id: r.venue_id,
        venue_name: r.venue_name,
        status: r.status,
        bounce_reason: r.bounce_reason,
        signed_up_at: r.signed_up_at,
        declined_at: r.declined_at,
        preferred_requested_at: r.preferred_requested_at,
      });
      // For the card's "most-recent" timestamps, take the max.
      if (r.sent_at && r.sent_at > grp.sent_at) grp.sent_at = r.sent_at;
    }
    return Array.from(map.values()).sort((a, b) => (b.sent_at || '').localeCompare(a.sent_at || ''));
  }

  function matchesFilter(grp, f) {
    if (f === 'all') return true;
    return grp.venues.some(v => v.status === f);
  }

  function showInlineErr(btn, message) {
    // Insert an inline error box right under the button row so the cooldown
    // message (e.g. "Please wait about 23h 47m before resending…") is visible
    // instead of being buried in a tooltip. Cleared on next render.
    const card = btn.closest('.card');
    if (!card) { alert(message); return; }
    let box = card.querySelector('._mi-inline-err');
    if (!box) {
      box = document.createElement('div');
      box.className = '_mi-inline-err';
      box.style.cssText = 'background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);color:#fca5a5;padding:6px 10px;border-radius:4px;font-size:0.72rem;margin-top:6px;';
      card.appendChild(box);
    }
    box.textContent = '⚠ ' + message;
  }

  async function resend(token, btn) {
    btn.disabled = true;
    btn.textContent = 'Resending…';
    try {
      const r = await fetch('/api/artist-invitations/' + encodeURIComponent(token) + '/resend', {
        method: 'POST', credentials: 'include',
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const msg = body.detail || ('HTTP ' + r.status);
        // 429 = cooldown; surface the server's explanation inline (it includes
        // the remaining wait, e.g. "Please wait about 21h 4m before resending…").
        if (r.status === 429) {
          showInlineErr(btn, msg);
          btn.disabled = false;
          btn.textContent = 'Resend';
          return;
        }
        throw new Error(msg);
      }
      const d = await r.json();
      btn.textContent = d.bounced ? 'Bounced' : 'Sent';
      setTimeout(load, 1000);
    } catch (e) {
      showInlineErr(btn, e.message || 'Resend failed');
      btn.disabled = false;
      btn.textContent = 'Resend';
    }
  }

  function render() {
    const groups = groupByToken(allRows).filter(g => matchesFilter(g, activeFilter));
    const root = document.getElementById('list');
    if (groups.length === 0) {
      root.className = 'empty';
      root.innerHTML = activeFilter === 'all'
        ? '<div>You haven\'t invited any artists yet. Open the User dropdown → <strong>Invite Artists</strong> to get started.</div>'
        : '<div>No invitations match this filter.</div>';
      return;
    }
    root.className = '';
    root.innerHTML = groups.map(grp => {
      const venues = grp.venues.map(v => {
        const label = STATUS_LABELS[v.status] || v.status;
        return '<span class="pill pill-' + esc(v.status) + '" title="' + esc(v.venue_name) + ' · ' + esc(label) + '">'
          + esc(v.venue_name) + ' · ' + esc(label) + '</span>';
      }).join('');
      const bounce = grp.venues.find(v => v.bounce_reason);
      const bounceHtml = bounce
        ? '<div class="bounce-msg">⚠ Email bounced: ' + esc(bounce.bounce_reason) + ' — fix the address and resend, or remove the invite.</div>'
        : '';
      const canResend = !!grp.token && grp.venues.some(v => !['signed_up','preferred_requested','preferred_approved','declined','expired'].includes(v.status));
      const resendBtn = canResend
        ? '<button class="btn" onclick="window._mi_resend(\'' + esc(grp.token) + '\', this)">Resend</button>'
        : '';
      const resendCount = grp.resent_count > 0 ? ' <span style="color:var(--text-muted);font-size:0.7rem;">(resent ' + grp.resent_count + 'x)</span>' : '';
      return '<div class="card">' +
        '<div class="card-header">' +
          '<div><div class="email">' + esc(grp.invited_email) + '</div>' +
            (grp.message ? '<div style="color:var(--text-muted);font-size:0.75rem;margin-top:3px;font-style:italic;">"' + esc(grp.message) + '"</div>' : '') +
          '</div>' +
          '<div class="when">' + esc(relTime(grp.sent_at)) + resendCount + '</div>' +
        '</div>' +
        '<div class="venue-pills">' + venues + '</div>' +
        bounceHtml +
        (resendBtn ? '<div class="actions">' + resendBtn + '</div>' : '') +
      '</div>';
    }).join('');
  }

  window._mi_resend = resend;

  async function load() {
    const root = document.getElementById('list');
    root.className = 'empty';
    root.textContent = 'Loading…';
    try {
      const r = await fetch('/api/me/invitations', { credentials: 'include' });
      if (!r.ok) throw new Error('Could not load invitations (HTTP ' + r.status + ')');
      allRows = await r.json();
      render();
    } catch (e) {
      root.className = 'empty';
      root.textContent = e.message || 'Could not load invitations.';
    }
  }

  function wireFilters() {
    document.querySelectorAll('#filters .chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('#filters .chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        activeFilter = chip.getAttribute('data-f');
        render();
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { wireFilters(); load(); });
  } else {
    wireFilters(); load();
  }
})();
