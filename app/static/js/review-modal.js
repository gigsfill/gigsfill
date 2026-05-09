/**
 * GigsFill — Artist Review Modal (venue-create-gigs side)
 * Matches openVenueRateModal styling exactly for site consistency.
 * openReviewModal({ artistId, artistName, gigId, gigDate, gigTitle })
 * openReviewModal({ artistId, artistName })  // general review
 */
window.openReviewModal = function({ artistId, artistName, gigId = null, gigDate = null, gigTitle = null, existingRating = 0, existingText = '' } = {}) {
  var existing = document.getElementById('reviewModalOverlay');
  if (existing) existing.remove();

  var selected = existingRating || 0;
  var isEdit = selected > 0;
  var starLabels = ['','Poor','Fair','Good','Very Good','Excellent'];

  function starHtml(n) {
    var html = '';
    for (var i = 1; i <= 5; i++) {
      html += '<span class="_rvStar" data-val="' + i + '" style="font-size:2rem;cursor:pointer;transition:transform 0.1s,color 0.1s;user-select:none;color:' + (i<=n?'#f59e0b':'#444') + ';transform:' + (i<=n?'scale(1.1)':'scale(1)') + '">' + (i<=n?'★':'☆') + '</span>';
    }
    return html;
  }

  var ov = document.createElement('div');
  ov.id = 'reviewModalOverlay';
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:20000;display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box;';
  ov.innerHTML =
    '<div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:1px solid rgba(6,182,212,0.3);border-radius:12px;padding:26px 30px;max-width:420px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5);">' +
      '<h3 id="rvModalTitle" style="color:#06b6d4;margin:0 0 4px;font-size:0.95rem;text-transform:uppercase;letter-spacing:0.05em;">' + (isEdit ? 'Edit Your Review' : 'Rate This Artist') + '</h3>' +
      '<p style="color:#d1d5db;margin:0 0 18px;font-size:0.85rem;">' + (artistName || '') + '</p>' +
      '<div id="_rvStarRow" style="display:flex;gap:8px;justify-content:center;margin-bottom:6px;">' + starHtml(selected) + '</div>' +
      '<div id="_rvStarLabel" style="text-align:center;font-size:0.78rem;color:#9ca3af;height:16px;margin-bottom:14px;">' + (selected > 0 ? starLabels[selected] : '') + '</div>' +
      '<div id="_rvStarErr" style="color:#ef4444;font-size:0.78rem;text-align:center;margin-bottom:8px;display:none;">Please select a rating.</div>' +
      '<textarea id="_rvReviewText" rows="3" maxlength="1000" placeholder="Share your experience (optional)\u2026" style="width:100%;box-sizing:border-box;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-radius:7px;color:#e5e5e5;padding:9px 11px;font-size:0.85rem;resize:vertical;outline:none;margin-bottom:16px;">' + (existingText || '') + '</textarea>' +
      '<div id="_rvReviewMsg" style="font-size:0.78rem;text-align:center;min-height:16px;margin-bottom:10px;"></div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end;">' +
        '<button id="_rvDeleteBtn" style="padding:7px 18px;background:transparent;color:#ef4444;border:1px solid rgba(239,68,68,0.4);border-radius:6px;font-size:0.82rem;cursor:pointer;' + (isEdit ? '' : 'display:none;') + '">Delete Review</button>' +
        '<button id="_rvCancelBtn" style="padding:7px 18px;background:transparent;color:#9ca3af;border:1px solid rgba(255,255,255,0.15);border-radius:6px;font-size:0.82rem;cursor:pointer;">Cancel</button>' +
        '<button id="_rvSubmitBtn" style="padding:7px 20px;background:#06b6d4;color:#fff;border:none;border-radius:6px;font-size:0.82rem;font-weight:600;cursor:pointer;">' + (isEdit ? 'Update Review' : 'Submit Review') + '</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(ov);

  // Star interaction
  function renderStars(n) {
    ov.querySelectorAll('._rvStar').forEach(function(s) {
      var v = parseInt(s.dataset.val);
      s.textContent = v <= n ? '★' : '☆';
      s.style.color = v <= n ? '#f59e0b' : '#444';
      s.style.transform = v <= n ? 'scale(1.1)' : 'scale(1)';
    });
    var lbl = document.getElementById('_rvStarLabel');
    if (lbl) lbl.textContent = n > 0 ? starLabels[n] : '';
  }

  ov.querySelectorAll('._rvStar').forEach(function(s) {
    s.addEventListener('mouseover', function() { renderStars(parseInt(s.dataset.val)); });
    s.addEventListener('mouseout',  function() { renderStars(selected); });
    s.addEventListener('click',     function() {
      selected = parseInt(s.dataset.val);
      renderStars(selected);
      document.getElementById('_rvStarErr').style.display = 'none';
    });
  });

  ov.querySelector('#_rvCancelBtn').addEventListener('click', function() { ov.remove(); });
  ov.addEventListener('click', function(e) { if (e.target === ov) ov.remove(); });

  // Check existing review from API on open (authoritative)
  var venueId = window.venueId || new URLSearchParams(window.location.search).get('venue_id');
  if (venueId && artistId) {
    fetch('/api/venues/' + venueId + '/artists/' + artistId + '/review', { credentials: 'include' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) {
        if (d && d.reviewed) {
          selected = d.rating;
          renderStars(selected);
          var txt = document.getElementById('_rvReviewText');
          if (txt && !txt.value) txt.value = d.review_text || '';
          var title = document.getElementById('rvModalTitle');
          if (title) title.textContent = 'Edit Your Review';
          var delBtn = document.getElementById('_rvDeleteBtn');
          if (delBtn) delBtn.style.display = '';
        }
      }).catch(function() {});
  }

  // Delete
  ov.querySelector('#_rvDeleteBtn').addEventListener('click', async function() {
    if (!venueId) return;
    var msg = document.getElementById('_rvReviewMsg');
    var btn = document.getElementById('_rvDeleteBtn');
    btn.disabled = true; btn.textContent = 'Deleting\u2026';
    try {
      var res = await fetch('/api/venues/' + venueId + '/artists/' + artistId + '/review', {
        method: 'DELETE', credentials: 'include'
      });
      if (!res.ok) throw new Error('failed');
      if (msg) { msg.style.color = '#10b981'; msg.textContent = '\u2713 Review deleted.'; }
      document.querySelectorAll('#_rateArtistBtn, ._rateArtistBtn').forEach(function(b) {
        var bAid = b.dataset.artistId || b.getAttribute('data-artist-id');
        if (!bAid || bAid == artistId) {
          b.textContent = 'Rate Artist';
          b.style.borderColor = 'rgba(245,158,11,0.4)';
          b.style.color = '#f59e0b';
        }
      });
      setTimeout(function() { ov.remove(); }, 1200);
    } catch(e) {
      if (msg) { msg.style.color = '#ef4444'; msg.textContent = 'Delete failed. Please try again.'; }
      btn.disabled = false; btn.textContent = 'Delete Review';
    }
  });

  // Submit
  ov.querySelector('#_rvSubmitBtn').addEventListener('click', async function() {
    if (!selected) { document.getElementById('_rvStarErr').style.display = ''; return; }
    var reviewText = (document.getElementById('_rvReviewText').value || '').trim();
    var msg = document.getElementById('_rvReviewMsg');
    var btn = document.getElementById('_rvSubmitBtn');
    btn.disabled = true; btn.textContent = 'Saving\u2026';

    if (!venueId) { msg.style.color='#ef4444'; msg.textContent='No venue context.'; btn.disabled=false; btn.textContent='Submit Review'; return; }

    try {
      var res = await fetch('/api/venues/' + venueId + '/artists/' + artistId + '/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ rating: selected, review_text: reviewText, artist_id: artistId })
      });
      if (!res.ok) throw new Error((await res.json().catch(function(){return {};})).detail || 'Failed');

      msg.style.color = '#10b981'; msg.textContent = '\u2713 Review saved!';

      // Update Rate Artist buttons in DOM
      document.querySelectorAll('#_rateArtistBtn, ._rateArtistBtn').forEach(function(b) {
        var bAid = b.dataset.artistId || b.getAttribute('data-artist-id');
        if (!bAid || bAid == artistId) {
          b.textContent = '\u270f\ufe0f Edit Review';
          b.style.borderColor = 'rgba(245,158,11,0.5)';
          b.style.color = '#f59e0b';
        }
      });
      setTimeout(function() { ov.remove(); }, 1400);
    } catch(e) {
      msg.style.color = '#ef4444'; msg.textContent = e.message || 'Submission failed.';
      btn.disabled = false; btn.textContent = isEdit ? 'Update Review' : 'Submit Review';
    }
  });
};

window._rvEsc = function(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
};
