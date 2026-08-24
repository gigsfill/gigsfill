/* venue-gig-details-modal.js — Shared "Venue Gig Details" modal.
 *
 * Purpose: give logged-in artists a one-click view of a venue's rig /
 * pay / tabs / load-in specs from anywhere a venue is referenced —
 * the shared gig-modal (artist book-gigs + venue create-gigs), the
 * public-gigs / venue-profile / artist-profile inline gig modals, and
 * the venue-profile Gig Details tab itself.
 *
 * All content comes from GET /api/venues/{id}/public. The endpoint sets
 * viewer_is_artist=true only when the caller owns or is an entity_user
 * of at least one artist row, so venue-only visitors and anonymous
 * traffic don't see the extra info. Callers still gate the LINK on
 * mountLinks() below so venue users don't even see a click affordance
 * that would 401 for them.
 *
 * Public API (window.venueGigDetails):
 *   showModal(venueId, venueName?) - opens the modal for a venue.
 *   mountLinks(root?)              - scans root (defaults to document)
 *                                    for .vgd-link-slot placeholders
 *                                    and fills them with the "(Venue
 *                                    Gig Details)" link when the
 *                                    viewer is an artist. Callers
 *                                    invoke this right after each
 *                                    render pass; the artist check is
 *                                    cached so repeated calls are cheap.
 *   isArtist()                     - Promise<bool>, cached.
 *   buildRowsHTML(venuePayload)    - Row-only render used by both this
 *                                    modal and venue-profile.html's
 *                                    Gig Details tab so they can't
 *                                    drift apart.
 *
 * The link placeholder markup callers should emit inline after the
 * venue name:
 *   <span class="vgd-link-slot" data-vgd-vid="${vid}"
 *         data-vgd-name="${esc(vname)}"></span>
 * mountLinks() replaces the slot content in place.
 */
(function () {
  const OVERLAY_ID = 'venueGigDetailsOverlay';
  const CACHE = { artistPromise: null, venue: {} };

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  function isArtist() {
    if (CACHE.artistPromise) return CACHE.artistPromise;
    CACHE.artistPromise = fetch('/api/me', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(me => Array.isArray(me && me.artists) && me.artists.length > 0)
      .catch(() => false);
    return CACHE.artistPromise;
  }

  // Row builder shared between this modal and venue-profile.html's Gig
  // Details tab. Consumes the /api/venues/{id}/public payload shape
  // (same field names as backend/routes/venues.py:get_venue_public).
  function buildRowsHTML(v, opts) {
    v = v || {};
    opts = opts || {};
    const yn = b => (String(b).toLowerCase() === 'true' || b === 1 || b === true) ? 'Yes' : 'No';
    const has = x => x != null && String(x).trim() !== '';
    const isTrue = x => String(x).toLowerCase() === 'true' || x === 1 || x === true;
    const fmtPay = () => {
      const d = Number(v.default_pay_dollars) || 0;
      const c = Number(v.default_pay_cents) || 0;
      if (d === 0 && c === 0) return null;
      const tot = d + c / 100;
      return tot % 1 === 0 ? `$${Math.round(tot)}` : `$${tot.toFixed(2)}`;
    };
    const fmtArrival = () => {
      if (v.arrival_time_type === 'no_earlier_than') {
        const h = v.arrival_no_earlier_than_hour, p = v.arrival_no_earlier_than_period;
        return (h && p) ? `No earlier than ${h}:00 ${p}` : 'No earlier than (unspecified)';
      }
      if (v.arrival_time_type === 'flexible') return 'Flexible';
      return null;
    };
    const line = (label, valueHtml) => `
      <div style="display:flex;gap:16px;align-items:baseline;padding:8px 0;border-bottom:1px solid rgba(148,163,184,0.10);">
        <div style="flex:0 0 220px;font-size:0.72rem;font-weight:700;color:var(--cyan);text-transform:uppercase;letter-spacing:0.06em;">${_esc(label)}</div>
        <div style="flex:1;min-width:0;font-size:0.88rem;color:var(--text);line-height:1.4;white-space:pre-wrap;word-break:break-word;">${valueHtml}</div>
      </div>`;
    const rows = [];

    if (has(v.venue_size)) rows.push(line('Capacity', `${_esc(v.venue_size)} guests`));
    // 2026-08-24: skip the Default Pay row when this modal is opened
    // from a specific-gig context (opts.hideDefaultPay=true). The parent
    // gig modal already shows THIS gig's actual pay right above; the
    // venue's default is meta-info that confused artists comparing the
    // two numbers ("Gig says $10 but Venue Gig Details says $200?").
    // Standalone venue-profile Gig Details tab keeps the row since
    // there's no gig context there.
    if (!opts.hideDefaultPay) {
      const payTxt = fmtPay();
      if (payTxt) rows.push(line('Default Pay', `${payTxt} <span style="color:var(--text-muted);font-size:0.78rem;">(per gig — actual pay varies)</span>`));
    }
    if (Number(v.artist_frequency_days) > 0) {
      rows.push(line('Artist Frequency', `1 performance every ${Number(v.artist_frequency_days)} days`));
    }

    rows.push(line('Has Stage?', _esc(yn(v.has_stage))));
    if (isTrue(v.has_stage)) {
      if (has(v.stage_width_ft) || has(v.stage_depth_ft)) {
        const w = has(v.stage_width_ft) ? `${v.stage_width_ft} ft wide` : null;
        const d = has(v.stage_depth_ft) ? `${v.stage_depth_ft} ft deep` : null;
        rows.push(line('Stage Dimensions', _esc([w, d].filter(Boolean).join(' × ') || '—')));
      }
      if (has(v.setup_location_description)) rows.push(line('Stage Setup Notes', _esc(v.setup_location_description)));
    }

    rows.push(line('Sound Equipment?', _esc(yn(v.has_sound_equipment))));
    if (has(v.sound_equipment_description)) rows.push(line('Sound Equipment Details', _esc(v.sound_equipment_description)));

    // Sound Engineer only when Sound Equipment=Yes (matches the
    // conditional reveal on venue-edit.html so we don't confuse artists
    // with a "No" to a question the venue was never asked).
    if (isTrue(v.has_sound_equipment)) {
      rows.push(line('Sound Engineer?', _esc(yn(v.has_sound_engineer))));
      if (has(v.sound_engineer_details)) rows.push(line('Sound Engineer Details', _esc(v.sound_engineer_details)));
    }

    rows.push(line('Lighting?', _esc(yn(v.has_lighting))));
    if (has(v.lighting_description)) rows.push(line('Lighting Details', _esc(v.lighting_description)));

    if (has(v.bar_tab_details)) rows.push(line('Bar Tab', _esc(v.bar_tab_details)));
    if (has(v.food_tab_details)) rows.push(line('Food Tab', _esc(v.food_tab_details)));

    const arr = fmtArrival();
    if (arr) rows.push(line('Arrival Time', _esc(arr)));
    if (has(v.load_in_out_details)) rows.push(line('Load In / Out Details', _esc(v.load_in_out_details)));

    if (isTrue(v.pro_certified)) {
      rows.push(line('PRO Certified', '<span style="color:#22c55e;font-weight:600;">✓ Yes — venue holds public performance license(s)</span>'));
    }

    return rows.length
      ? rows.join('')
      : `<div style="color:var(--text-muted);font-style:italic;padding:12px 0;">This venue hasn't filled in gig details yet.</div>`;
  }

  function _closeModal() {
    const o = document.getElementById(OVERLAY_ID);
    if (o) o.remove();
  }

  async function showModal(venueId, venueName) {
    const vid = parseInt(venueId, 10);
    if (!vid) return;
    _closeModal();

    const overlay = document.createElement('div');
    overlay.id = OVERLAY_ID;
    // z-index above other modals (gf-modals uses 10000, gig modal ~2000)
    // so this pops over anything already open.
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.65);z-index:11000;display:flex;align-items:center;justify-content:center;padding:20px;';
    overlay.addEventListener('click', e => { if (e.target === overlay) _closeModal(); });

    const shell = document.createElement('div');
    shell.setAttribute('role', 'dialog');
    shell.setAttribute('aria-modal', 'true');
    shell.style.cssText = 'background:var(--card,#151b28);border:1px solid var(--border,#333);border-radius:12px;max-width:640px;width:100%;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,0.5);';
    shell.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border,#333);">
        <div>
          <div style="font-size:0.7rem;font-weight:700;color:var(--cyan);text-transform:uppercase;letter-spacing:0.06em;">Venue Gig Details</div>
          <div id="vgdTitle" style="font-size:1.05rem;font-weight:600;color:var(--text,#e5e7eb);margin-top:2px;">${_esc(venueName || 'Loading…')}</div>
        </div>
        <button type="button" aria-label="Close" style="background:none;border:none;color:var(--text-muted,#94a3b8);font-size:1.6rem;line-height:1;cursor:pointer;padding:0 4px;">&times;</button>
      </div>
      <div id="vgdBody" style="padding:16px 20px;overflow-y:auto;">
        <div style="color:var(--text-muted,#94a3b8);font-style:italic;padding:14px 0;">Loading venue details…</div>
      </div>`;
    shell.querySelector('button[aria-label="Close"]').addEventListener('click', _closeModal);
    overlay.appendChild(shell);
    document.body.appendChild(overlay);

    // Escape closes; scoped so we remove the listener on close.
    const onKey = e => { if (e.key === 'Escape') { _closeModal(); document.removeEventListener('keydown', onKey); } };
    document.addEventListener('keydown', onKey);

    // Fetch + render. Cached per venue for the session so re-opening
    // the same modal is instant.
    try {
      let v = CACHE.venue[vid];
      if (!v) {
        const res = await fetch(`/api/venues/${vid}/public`, { credentials: 'include' });
        if (!res.ok) throw new Error('venue lookup failed');
        v = await res.json();
        CACHE.venue[vid] = v;
      }
      if (v.venue_name) shell.querySelector('#vgdTitle').textContent = v.venue_name;
      if (!v.viewer_is_artist) {
        shell.querySelector('#vgdBody').innerHTML = `<div style="color:var(--text-muted);font-style:italic;padding:14px 0;">Sign in with an artist account to view gig details for this venue.</div>`;
        return;
      }
      // Opened from a gig modal (via mountLinks) — skip the "Default
      // Pay" row since the parent gig modal already shows THIS gig's
      // actual pay right above. venue-profile.html calls buildRowsHTML
      // directly without opts and still gets the row.
      shell.querySelector('#vgdBody').innerHTML = buildRowsHTML(v, { hideDefaultPay: true });
    } catch (e) {
      shell.querySelector('#vgdBody').innerHTML = `<div style="color:#ef4444;padding:14px 0;">Could not load venue details.</div>`;
    }
  }

  // Fill any `.vgd-link-slot` placeholders in the given root with the
  // "(Venue Gig Details)" link — but only when the viewer is an artist.
  // Idempotent: skips slots we've already processed.
  async function mountLinks(root) {
    const scope = root || document;
    const slots = Array.from(scope.querySelectorAll('.vgd-link-slot:not([data-vgd-mounted])'));
    if (!slots.length) return;
    const artist = await isArtist();
    slots.forEach(slot => {
      slot.setAttribute('data-vgd-mounted', '1');
      if (!artist) return;  // leave empty — no link for venue-only viewers
      const vid = parseInt(slot.dataset.vgdVid, 10);
      const vname = slot.dataset.vgdName || '';
      if (!vid) return;
      const a = document.createElement('a');
      a.href = 'javascript:void(0)';
      a.textContent = '(Venue Gig Details)';
      a.style.cssText = 'margin-left:8px;font-size:0.78rem;color:var(--cyan);text-decoration:none;font-style:italic;cursor:pointer;';
      a.addEventListener('mouseenter', () => a.style.textDecoration = 'underline');
      a.addEventListener('mouseleave', () => a.style.textDecoration = 'none');
      a.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); showModal(vid, vname); });
      slot.appendChild(a);
    });
  }

  window.venueGigDetails = { showModal, mountLinks, isArtist, buildRowsHTML };
})();
