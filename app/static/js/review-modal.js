/**
 * GigsFill — Artist Review Modal (venue-create-gigs side)
 * Phase 3 migration: was a self-built inline modal. Now uses showStyledModal
 * for chrome + tone consistency. Star interactions, textarea, async lookup,
 * Submit/Delete handlers are wired up after mount.
 *
 * openReviewModal({ artistId, artistName, gigId, gigDate, gigTitle, existingRating, existingText })
 */
window.openReviewModal = function({ artistId, artistName, gigId = null, gigDate = null, gigTitle = null, existingRating = 0, existingText = '' } = {}) {
  let selected = existingRating || 0;
  let isEdit = selected > 0;
  const starLabels = ['','Poor','Fair','Good','Very Good','Excellent'];
  const venueId = window.venueId || new URLSearchParams(window.location.search).get('venue_id');

  function starHtml(n) {
    let html = '';
    for (let i = 1; i <= 5; i++) {
      html += '<span class="_rvStar" data-val="' + i + '" style="font-size:2rem;cursor:pointer;transition:transform 0.1s,color 0.1s;user-select:none;color:' + (i<=n?'#f59e0b':'#444') + ';transform:' + (i<=n?'scale(1.1)':'scale(1)') + '">' + (i<=n?'★':'☆') + '</span>';
    }
    return html;
  }

  const body =
    `<p style="color:#d1d5db;margin:0 0 18px 0;font-size:0.85rem;text-align:center;">${(artistName || '').replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]))}</p>` +
    `<div id="_rvStarRow" style="display:flex;gap:8px;justify-content:center;margin-bottom:6px;">${starHtml(selected)}</div>` +
    `<div id="_rvStarLabel" style="text-align:center;font-size:0.78rem;color:#9ca3af;height:16px;margin-bottom:14px;">${selected > 0 ? starLabels[selected] : ''}</div>` +
    '<div id="_rvStarErr" style="color:#ef4444;font-size:0.78rem;text-align:center;margin-bottom:8px;display:none;">Please select a rating.</div>' +
    `<textarea id="_rvReviewText" rows="3" maxlength="1000" placeholder="Share your experience (optional)…" style="width:100%;box-sizing:border-box;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-radius:7px;color:#e5e5e5;padding:9px 11px;font-size:0.85rem;resize:vertical;outline:none;margin-bottom:6px;">${(existingText || '').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</textarea>` +
    '<div id="_rvReviewMsg" style="font-size:0.78rem;text-align:center;min-height:16px;"></div>';

  window.showStyledModal(
    isEdit ? 'Edit Your Review' : 'Rate This Artist',
    body,
    [
      {
        text: 'Delete Review', style: 'danger',
        onClick: async () => {
          if (!venueId) return false;
          const overlay = document.querySelector('.gfm-modal-overlay');
          const msg = overlay && overlay.querySelector('#_rvReviewMsg');
          const btns = overlay && overlay.querySelectorAll('.gfm-modal-footer .btn');
          const delBtn = btns && btns[0];
          if (delBtn) { delBtn.disabled = true; delBtn.textContent = 'Deleting…'; }
          try {
            const res = await fetch(`/api/venues/${venueId}/artists/${artistId}/review`, {
              method: 'DELETE', credentials: 'include'
            });
            if (!res.ok) throw new Error('failed');
            if (msg) { msg.style.color = '#10b981'; msg.textContent = '✓ Review deleted.'; }
            document.querySelectorAll('#_rateArtistBtn, ._rateArtistBtn').forEach(b => {
              const bAid = b.dataset.artistId || b.getAttribute('data-artist-id');
              if (!bAid || bAid == artistId) {
                b.textContent = 'Rate Artist';
                b.style.borderColor = 'rgba(245,158,11,0.4)';
                b.style.color = '#f59e0b';
              }
            });
            setTimeout(() => { if (window.closeAllModals) window.closeAllModals(); }, 1200);
          } catch (e) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = 'Delete failed. Please try again.'; }
            if (delBtn) { delBtn.disabled = false; delBtn.textContent = 'Delete Review'; }
            return false; // keep modal open
          }
          return false; // we close it ourselves via setTimeout
        }
      },
      { text: 'Cancel', style: 'ghost' },
      {
        text: isEdit ? 'Update Review' : 'Submit Review', style: 'primary',
        onClick: async () => {
          const overlay = document.querySelector('.gfm-modal-overlay');
          if (!selected) {
            const err = overlay && overlay.querySelector('#_rvStarErr');
            if (err) err.style.display = '';
            return false;
          }
          const txt = overlay && overlay.querySelector('#_rvReviewText');
          const reviewText = (txt && txt.value || '').trim();
          const msg = overlay && overlay.querySelector('#_rvReviewMsg');
          const btns = overlay && overlay.querySelectorAll('.gfm-modal-footer .btn');
          const submitBtn = btns && btns[btns.length - 1];
          if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Saving…'; }
          if (!venueId) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = 'No venue context.'; }
            if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Submit Review'; }
            return false;
          }
          try {
            const res = await fetch(`/api/venues/${venueId}/artists/${artistId}/review`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              credentials: 'include',
              body: JSON.stringify({ rating: selected, review_text: reviewText, artist_id: artistId })
            });
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Failed');
            if (msg) { msg.style.color = '#10b981'; msg.textContent = '✓ Review saved!'; }
            // Update Rate Artist buttons in DOM
            document.querySelectorAll('#_rateArtistBtn, ._rateArtistBtn').forEach(b => {
              const bAid = b.dataset.artistId || b.getAttribute('data-artist-id');
              if (!bAid || bAid == artistId) {
                b.textContent = '✏️ Edit Review';
                b.style.borderColor = 'rgba(245,158,11,0.5)';
                b.style.color = '#f59e0b';
              }
            });
            setTimeout(() => { if (window.closeAllModals) window.closeAllModals(); }, 1400);
          } catch (e) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = e.message || 'Submission failed.'; }
            if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = isEdit ? 'Update Review' : 'Submit Review'; }
            return false;
          }
          return false; // closed via setTimeout
        }
      }
    ],
    { size: 'md' }
  );

  // ── Post-mount wiring ──
  setTimeout(() => {
    const overlay = document.querySelector('.gfm-modal-overlay');
    if (!overlay) return;

    // Hide Delete button initially if not editing — async check below may unhide it.
    const delBtn = overlay.querySelectorAll('.gfm-modal-footer .btn')[0];
    if (delBtn && !isEdit) delBtn.style.display = 'none';

    function renderStars(n) {
      overlay.querySelectorAll('._rvStar').forEach(s => {
        const v = parseInt(s.dataset.val);
        s.textContent = v <= n ? '★' : '☆';
        s.style.color = v <= n ? '#f59e0b' : '#444';
        s.style.transform = v <= n ? 'scale(1.1)' : 'scale(1)';
      });
      const lbl = overlay.querySelector('#_rvStarLabel');
      if (lbl) lbl.textContent = n > 0 ? starLabels[n] : '';
    }

    overlay.querySelectorAll('._rvStar').forEach(s => {
      s.addEventListener('mouseover', () => renderStars(parseInt(s.dataset.val)));
      s.addEventListener('mouseout',  () => renderStars(selected));
      s.addEventListener('click', () => {
        selected = parseInt(s.dataset.val);
        renderStars(selected);
        const err = overlay.querySelector('#_rvStarErr');
        if (err) err.style.display = 'none';
      });
    });

    // Check existing review from API on open (authoritative — may flip isEdit)
    if (venueId && artistId) {
      fetch(`/api/venues/${venueId}/artists/${artistId}/review`, { credentials: 'include' })
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (d && d.reviewed) {
            selected = d.rating;
            renderStars(selected);
            const txt = overlay.querySelector('#_rvReviewText');
            if (txt && !txt.value) txt.value = d.review_text || '';
            const title = overlay.querySelector('.gfm-modal-title');
            if (title) title.textContent = 'Edit Your Review';
            if (delBtn) delBtn.style.display = '';
            const submitBtn = overlay.querySelectorAll('.gfm-modal-footer .btn');
            if (submitBtn && submitBtn[submitBtn.length-1]) {
              submitBtn[submitBtn.length-1].textContent = 'Update Review';
            }
          }
        }).catch(() => {});
    }
  }, 50);
};

window._rvEsc = function(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
};
