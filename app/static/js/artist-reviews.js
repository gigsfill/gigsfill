/**
 * GigsFill — Artist Reviews UI
 * =============================
 * Renders star ratings, review cards, and the submit-review form.
 * Used on:
 *   - artist-profile.html  (public view: rating summary + review list)
 *   - venue-create-gigs.html  (post-gig prompt to rate the artist)
 */

// ── STAR RENDERING HELPERS ─────────────────────────────────────────────────

/**
 * Render filled/half/empty stars as HTML.
 * @param {number|null} rating  e.g. 4.3
 * @param {number} size         icon size in px (default 16)
 */
function renderStars(rating, size = 16) {
  if (!rating) return `<span style="color:#9ca3af;font-size:${size * 0.75}px;">No reviews yet</span>`;
  const stars = [];
  for (let i = 1; i <= 5; i++) {
    if (rating >= i) {
      stars.push(`<span style="color:#f59e0b;font-size:${size}px;">★</span>`);
    } else if (rating >= i - 0.5) {
      stars.push(`<span style="color:#f59e0b;font-size:${size}px;">⯨</span>`);
    } else {
      stars.push(`<span style="color:#d1d5db;font-size:${size}px;">★</span>`);
    }
  }
  return stars.join('');
}

/**
 * Render clickable star input for the review form.
 * @param {string} containerId  ID of element to render into
 * @param {function} onChange   called with numeric rating 1-5
 */
function renderStarInput(containerId, onChange) {
  const container = document.getElementById(containerId);
  if (!container) return;

  let selected = 0;

  function render(hover = 0) {
    container.innerHTML = '';
    for (let i = 1; i <= 5; i++) {
      const star = document.createElement('span');
      star.textContent = '★';
      star.style.cssText = `
        font-size:32px; cursor:pointer; padding:0 2px; transition:color .1s;
        color:${i <= (hover || selected) ? '#f59e0b' : '#d1d5db'};
      `;
      star.addEventListener('mouseenter', () => render(i));
      star.addEventListener('mouseleave', () => render(0));
      star.addEventListener('click', () => {
        selected = i;
        render(0);
        if (onChange) onChange(i);
      });
      container.appendChild(star);
    }
  }

  render(0);
  return {
    getValue: () => selected,
    setValue: (v) => { selected = v; render(0); }
  };
}


// ── RATING SUMMARY WIDGET ──────────────────────────────────────────────────

/**
 * Render the full rating summary (avg + bar breakdown) for an artist profile.
 * @param {string} containerId
 * @param {number} artistId
 */
async function renderRatingSummary(containerId, artistId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;">Loading ratings...</div>`;

  try {
    const res = await fetch(`/api/artists/${artistId}/reviews/summary`, { credentials: 'include' });
    const data = await res.json();

    if (!data.review_count) {
      container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;">No reviews yet</div>`;
      return;
    }

    const avg = data.avg_rating || 0;
    const total = data.review_count;
    const breakdown = [5, 4, 3, 2, 1].map(n => ({
      stars: n,
      count: data[`${['','one','two','three','four','five'][n]}_star`] || 0
    }));

    container.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:24px;flex-wrap:wrap;">
        <!-- Left: big average -->
        <div style="text-align:center;min-width:80px;">
          <div style="font-size:2.5rem;font-weight:800;color:var(--text);line-height:1;">${avg.toFixed(1)}</div>
          <div style="margin:4px 0;">${renderStars(avg, 18)}</div>
          <div style="font-size:0.7rem;color:var(--text-gray);">${total} review${total !== 1 ? 's' : ''}</div>
        </div>

        <!-- Right: breakdown bars -->
        <div style="flex:1;min-width:160px;">
          ${breakdown.map(({ stars, count }) => {
            const pct = total > 0 ? Math.round((count / total) * 100) : 0;
            return `
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span style="font-size:0.72rem;color:var(--text-gray);width:32px;text-align:right;">${stars}★</span>
                <div style="flex:1;height:8px;background:var(--border);border-radius:4px;overflow:hidden;">
                  <div style="width:${pct}%;height:100%;background:#f59e0b;border-radius:4px;transition:width .3s;"></div>
                </div>
                <span style="font-size:0.72rem;color:var(--text-gray);width:24px;">${count}</span>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;">Could not load ratings</div>`;
  }
}


// ── REVIEW LIST ────────────────────────────────────────────────────────────

/**
 * Render paginated list of reviews for an artist.
 * @param {string} containerId
 * @param {number} artistId
 * @param {number} page
 */
async function renderReviews(containerId, artistId, page = 1) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;padding:16px 0;">Loading reviews...</div>`;

  try {
    const res = await fetch(`/api/artists/${artistId}/reviews?page=${page}&limit=5`, { credentials: 'include' });
    const data = await res.json();

    if (!data.reviews || data.reviews.length === 0) {
      container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;padding:16px 0;">No reviews yet — be the first venue to rate this artist!</div>`;
      return;
    }

    // get current venue id fresh
    let myVenueId = null;
    try {
      const meRes = await fetch('/api/me', { credentials: 'include' });
      if (meRes.ok) { const me = await meRes.json(); myVenueId = me.venue_id || null; }
    } catch(e) {}

    const reviewsHtml = data.reviews.map(r => {
      const isOwn = myVenueId && r.venue_id === myVenueId;
      const actionBtns = isOwn ? `
        <div style="display:flex;gap:6px;margin-top:8px;">
          <button data-rv="${r.venue_id}" data-ai="${artistId}" data-rt="${r.rating}" data-tx="${(r.review_text||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;')}" data-ci="${containerId}" data-ri="${artistId}" onclick="editReview(this.dataset.rv,this.dataset.ai,this.dataset.rt,this.dataset.tx,this.dataset.ci,this.dataset.ri)"
            style="font-size:0.7rem;padding:3px 10px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer;">Edit</button>
          <button onclick="deleteReview(${r.id},'${containerId}',${artistId})"
            style="font-size:0.7rem;padding:3px 10px;border-radius:4px;border:1px solid #ef4444;background:transparent;color:#ef4444;cursor:pointer;">Delete</button>
        </div>` : '';
      return `
      <div style="border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:10px;background:var(--card);">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
          <div>
            <div style="margin-bottom:2px;">${renderStars(r.rating, 15)}</div>
            <div style="font-size:0.72rem;color:var(--text-gray);">
              ${esc(r.venue_name)}${r.city ? ` · ${esc(r.city)}${r.state ? ', ' + esc(r.state) : ''}` : ''}
            </div>
          </div>
          <div style="font-size:0.7rem;color:var(--text-gray);">${r.gig_date ? 'Gig played on: ' + esc(r.gig_date.slice(0, 10)) : ''}</div>
        </div>
        ${r.review_text ? `<p style="margin:0;font-size:0.8rem;color:var(--text);line-height:1.5;">${esc(r.review_text)}</p>` : ''}
        ${actionBtns}
      </div>`;
    }).join('');

    const paginationHtml = data.pages > 1 ? `
      <div style="display:flex;gap:8px;justify-content:center;margin-top:12px;">
        ${page > 1 ? `<button onclick="renderReviews('${containerId}', ${artistId}, ${page - 1})" class="btn btn-sm">← Prev</button>` : ''}
        <span style="font-size:0.8rem;color:var(--text-gray);align-self:center;">Page ${page} of ${data.pages}</span>
        ${page < data.pages ? `<button onclick="renderReviews('${containerId}', ${artistId}, ${page + 1})" class="btn btn-sm">Next →</button>` : ''}
      </div>
    ` : '';

    container.innerHTML = reviewsHtml + paginationHtml;
  } catch (e) {
    container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;padding:16px 0;">Could not load reviews</div>`;
  }
}


// ── SUBMIT REVIEW FORM ─────────────────────────────────────────────────────

/**
 * Render and handle the post-gig review form.
 * @param {string} containerId
 * @param {number} venueId
 * @param {number} gigId
 * @param {string} artistName
 * @param {function} onSuccess  called after successful submit
 */
async function renderReviewForm(containerId, venueId, gigId, artistName, onSuccess) {
  const container = document.getElementById(containerId);
  if (!container) return;

  // Check if already reviewed
  try {
    const check = await fetch(`/api/venues/${venueId}/gigs/${gigId}/review`, { credentials: 'include' });
    const existing = await check.json();
    if (existing.review) {
      container.innerHTML = `
        <div style="background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:8px;padding:12px;text-align:center;">
          <div style="margin-bottom:4px;">${renderStars(existing.review.rating, 18)}</div>
          <div style="font-size:0.8rem;color:var(--text-gray);">Review submitted ✓</div>
          ${existing.review.review_text ? `<p style="margin:8px 0 0;font-size:0.8rem;color:var(--text);">"${esc(existing.review.review_text)}"</p>` : ''}
        </div>
      `;
      return;
    }
  } catch (e) { /* first time */ }

  let selectedRating = 0;

  container.innerHTML = `
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;">
      <div style="font-size:0.85rem;font-weight:600;color:var(--text);margin-bottom:4px;">⭐ Rate ${esc(artistName)}</div>
      <div style="font-size:0.75rem;color:var(--text-gray);margin-bottom:12px;">How was the performance?</div>
      <div id="reviewStarInput_${gigId}" style="margin-bottom:12px;"></div>
      <textarea id="reviewText_${gigId}" placeholder="Optional: Share details about the performance..."
        style="width:100%;box-sizing:border-box;background:var(--bg);border:1px solid var(--border);border-radius:6px;
               padding:8px 10px;color:var(--text);font-size:0.8rem;resize:vertical;min-height:70px;"></textarea>
      <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
        <button id="reviewSubmitBtn_${gigId}" onclick="submitReview(${venueId}, ${gigId})"
          style="background:var(--cyan);color:#fff;border:none;border-radius:6px;padding:8px 16px;
                 font-size:0.8rem;font-weight:600;cursor:pointer;" disabled>
          Submit Review
        </button>
        <span id="reviewMsg_${gigId}" style="font-size:0.75rem;color:var(--text-gray);"></span>
      </div>
    </div>
  `;

  const starInput = renderStarInput(`reviewStarInput_${gigId}`, (rating) => {
    selectedRating = rating;
    const btn = document.getElementById(`reviewSubmitBtn_${gigId}`);
    if (btn) btn.disabled = false;
  });

  // Expose submit function globally for onclick
  window.submitReview = async function(venueId, gigId) {
    const btn = document.getElementById(`reviewSubmitBtn_${gigId}`);
    const msg = document.getElementById(`reviewMsg_${gigId}`);
    const reviewText = document.getElementById(`reviewText_${gigId}`)?.value?.trim() || '';

    if (!selectedRating) {
      if (msg) msg.textContent = 'Please select a star rating first';
      return;
    }

    if (btn) { btn.disabled = true; btn.textContent = 'Submitting...'; }

    try {
      const res = await fetch(`/api/venues/${venueId}/gigs/${gigId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ rating: selectedRating, review_text: reviewText })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');

      container.innerHTML = `
        <div style="background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:8px;padding:12px;text-align:center;">
          <div style="margin-bottom:4px;">${renderStars(selectedRating, 18)}</div>
          <div style="font-size:0.8rem;color:#22c55e;font-weight:600;">Review submitted! Thank you.</div>
        </div>
      `;
      if (onSuccess) onSuccess(selectedRating);
    } catch (e) {
      if (btn) { btn.disabled = false; btn.textContent = 'Submit Review'; }
      if (msg) msg.textContent = e.message || 'Failed to submit';
    }
  };
}


// ── INLINE STAR BADGE (for artist cards in search/booking) ────────────────

/**
 * Returns a compact inline HTML badge like "★ 4.3 (12)"
 * for use inside artist listing cards.
 */
function starBadge(avg_rating, review_count) {
  if (!review_count || !avg_rating) {
    return `<span style="font-size:0.7rem;color:#9ca3af;">No reviews</span>`;
  }
  return `
    <span style="font-size:0.72rem;color:#f59e0b;font-weight:600;">★ ${Number(avg_rating).toFixed(1)}</span>
    <span style="font-size:0.7rem;color:#9ca3af;"> (${review_count})</span>
  `;
}


// ── VENUE EDIT / DELETE OWN REVIEW ────────────────────────────────────────────

function _ensureReviewModals() {
  if (document.getElementById('reviewEditModal')) return;
  var div = document.createElement('div');
  div.innerHTML = '<div id="reviewEditModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);backdrop-filter:blur(4px);align-items:center;justify-content:center;z-index:10000"><div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:28px 32px;max-width:440px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.5)"><h3 style="margin:0 0 18px;font-size:1rem;font-weight:700;color:var(--text);padding-bottom:12px;border-bottom:1px solid var(--border)">Edit Review</h3><div style="margin-bottom:14px"><div style="font-size:0.75rem;color:var(--text-gray);font-weight:600;text-transform:uppercase;letter-spacing:0.03em;margin-bottom:8px">Rating</div><div id="reviewEditStars" style="display:flex;gap:6px"></div></div><div style="margin-bottom:18px"><div style="font-size:0.75rem;color:var(--text-gray);font-weight:600;text-transform:uppercase;letter-spacing:0.03em;margin-bottom:8px">Review</div><textarea id="reviewEditText" rows="4" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px;color:var(--text);font-size:0.85rem;resize:vertical;box-sizing:border-box"></textarea></div><div style="display:flex;gap:10px;justify-content:flex-end"><button id="reviewEditCancel" style="padding:9px 18px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-gray);font-size:0.82rem;font-weight:600;cursor:pointer">Cancel</button><button id="reviewEditSave" style="padding:9px 18px;border-radius:6px;border:none;background:linear-gradient(135deg,var(--purple),var(--cyan));color:#fff;font-size:0.82rem;font-weight:600;cursor:pointer">Save Changes</button></div></div></div>'
    + '<div id="reviewDeleteModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);backdrop-filter:blur(4px);align-items:center;justify-content:center;z-index:10000"><div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:28px 32px;max-width:400px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.5)"><h3 style="margin:0 0 12px;font-size:1rem;font-weight:700;color:var(--text);padding-bottom:12px;border-bottom:1px solid var(--border)">Delete Review</h3><p style="margin:0 0 22px;font-size:0.85rem;color:var(--text-gray);line-height:1.5">Are you sure you want to delete this review? This cannot be undone.</p><div style="display:flex;gap:10px;justify-content:flex-end"><button id="reviewDeleteCancel" style="padding:9px 18px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-gray);font-size:0.82rem;font-weight:600;cursor:pointer">Cancel</button><button id="reviewDeleteConfirm" style="padding:9px 18px;border-radius:6px;border:none;background:#ef4444;color:#fff;font-size:0.82rem;font-weight:600;cursor:pointer">Delete</button></div></div></div>';
  document.body.appendChild(div);
  document.getElementById('reviewEditCancel').onclick = _closeReviewEditModal;
  document.getElementById('reviewEditSave').onclick = _submitReviewEdit;
  document.getElementById('reviewDeleteCancel').onclick = _closeReviewDeleteModal;
  document.getElementById('reviewDeleteConfirm').onclick = _submitReviewDelete;
}

var _reviewEditState = {};
var _reviewDeleteState = {};

function editReview(venueId, artistId, currentRating, currentText, containerId, renderArtistId) {
  _ensureReviewModals();
  _reviewEditState = { venueId: venueId, artistId: artistId, containerId: containerId, renderArtistId: renderArtistId, rating: currentRating };
  var starsEl = document.getElementById('reviewEditStars');
  starsEl.innerHTML = '';
  for (var i = 1; i <= 5; i++) {
    (function(val) {
      var s = document.createElement('span');
      s.textContent = '\u2605';
      s.style.cssText = 'font-size:1.6rem;cursor:pointer;transition:color 0.1s;color:' + (val <= currentRating ? '#f59e0b' : 'var(--border)');
      s.onmouseenter = function() { starsEl.querySelectorAll('span').forEach(function(x){ x.style.color = x.dataset.val <= val ? '#f59e0b' : 'var(--border)'; }); };
      s.onmouseleave = function() { starsEl.querySelectorAll('span').forEach(function(x){ x.style.color = x.dataset.val <= _reviewEditState.rating ? '#f59e0b' : 'var(--border)'; }); };
      s.onclick = function() { _reviewEditState.rating = val; starsEl.querySelectorAll('span').forEach(function(x){ x.style.color = x.dataset.val <= val ? '#f59e0b' : 'var(--border)'; }); };
      s.dataset.val = val;
      starsEl.appendChild(s);
    })(i);
  }
  document.getElementById('reviewEditText').value = currentText || '';
  document.getElementById('reviewEditModal').style.display = 'flex';
}

function _closeReviewEditModal() {
  document.getElementById('reviewEditModal').style.display = 'none';
}

async function _submitReviewEdit() {
  var state = _reviewEditState;
  var reviewText = document.getElementById('reviewEditText').value.trim();
  var res = await fetch('/api/reviews/' + state.reviewId, {
    method: 'PUT', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating: state.rating, review_text: reviewText })
  });
  var data = await res.json();
  _closeReviewEditModal();
  if (data.ok) { renderReviews(state.containerId, state.renderArtistId); }
  else { alert(data.detail || 'Could not update review'); }
}

function deleteReview(reviewId, containerId, renderArtistId) {
  _ensureReviewModals();
  _reviewDeleteState = { reviewId: reviewId, containerId: containerId, renderArtistId: renderArtistId };
  document.getElementById('reviewDeleteModal').style.display = 'flex';
}

function _closeReviewDeleteModal() {
  document.getElementById('reviewDeleteModal').style.display = 'none';
}

async function _submitReviewDelete() {
  var state = _reviewDeleteState;
  var res = await fetch('/api/venues/' + state.venueId + '/artists/' + state.artistId + '/review', {
    method: 'DELETE', credentials: 'include'
  });
  var data = await res.json();
  _closeReviewDeleteModal();
  if (data.ok) { renderReviews(state.containerId, state.renderArtistId); }
  else { alert(data.detail || 'Could not delete review'); }
}


// ── VENUE RATING SUMMARY (mirrors renderRatingSummary for venues) ─────────────

async function renderVenueRatingSummary(containerId, venueId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;">Loading ratings...</div>`;

  try {
    const res = await fetch(`/api/venues/${venueId}/reviews/summary`, { credentials: 'include' });
    const data = await res.json();

    const badge = document.getElementById('reviewsBadge');
    if (badge) badge.textContent = data.review_count > 0 ? `(${data.review_count})` : '';

    if (!data.review_count) {
      container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;">No reviews yet</div>`;
      return;
    }

    const avg = data.avg_rating || 0;
    const total = data.review_count;
    const breakdown = [5, 4, 3, 2, 1].map(n => ({
      stars: n,
      count: data[['','one','two','three','four','five'][n] + '_star'] || 0
    }));

    container.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:24px;flex-wrap:wrap;">
        <div style="text-align:center;min-width:80px;">
          <div style="font-size:2.5rem;font-weight:800;color:var(--text);line-height:1;">${Number(avg).toFixed(1)}</div>
          <div style="margin:4px 0;">${renderStars(avg, 18)}</div>
          <div style="font-size:0.7rem;color:var(--text-gray);">${total} review${total !== 1 ? 's' : ''}</div>
        </div>
        <div style="flex:1;min-width:160px;">
          ${breakdown.map(({ stars, count }) => {
            const pct = total > 0 ? Math.round((count / total) * 100) : 0;
            return `
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span style="font-size:0.72rem;color:var(--text-gray);width:32px;text-align:right;">${stars}★</span>
                <div style="flex:1;height:8px;background:var(--border);border-radius:4px;overflow:hidden;">
                  <div style="width:${pct}%;height:100%;background:#f59e0b;border-radius:4px;transition:width .3s;"></div>
                </div>
                <span style="font-size:0.72rem;color:var(--text-gray);width:24px;">${count}</span>
              </div>`;
          }).join('')}
        </div>
      </div>`;
  } catch (e) {
    container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;">Could not load ratings</div>`;
  }
}


// ── VENUE REVIEW LIST (mirrors renderReviews for venues) ──────────────────────

async function renderVenueReviews(containerId, venueId, page = 1) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;padding:16px 0;">Loading reviews...</div>`;

  try {
    const res = await fetch(`/api/venues/${venueId}/reviews?page=${page}&limit=5`, { credentials: 'include' });
    const data = await res.json();

    if (!data.reviews || data.reviews.length === 0) {
      container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;padding:16px 0;">No reviews yet — artists who have performed here can leave a review!</div>`;
      return;
    }

    const reviewsHtml = data.reviews.map(r => {
      return `
      <div style="border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:10px;background:var(--card);">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
          <div>
            <div style="margin-bottom:2px;">${renderStars(r.rating, 15)}</div>
            <div style="font-size:0.72rem;color:var(--text-gray);">
              <a href="/app/artist-profile.html?artist_id=${r.artist_id}" target="_blank"
                 style="color:var(--cyan);text-decoration:none;font-weight:600;">${esc(r.artist_name || 'Artist')}</a>
            </div>
          </div>
          <div style="font-size:0.7rem;color:var(--text-gray);">${r.created_at ? new Date(r.created_at).toLocaleDateString('en-US',{month:'long',year:'numeric'}) : ''}</div>
        </div>
        ${r.review_text ? `<p style="margin:0;font-size:0.8rem;color:var(--text);line-height:1.5;">${esc(r.review_text)}</p>` : ''}
      </div>`;
    }).join('');

    const paginationHtml = data.pages > 1 ? `
      <div style="display:flex;gap:8px;justify-content:center;margin-top:12px;">
        ${page > 1 ? `<button onclick="renderVenueReviews('${containerId}', ${venueId}, ${page - 1})" class="btn btn-sm">← Prev</button>` : ''}
        <span style="font-size:0.8rem;color:var(--text-gray);align-self:center;">Page ${page} of ${data.pages}</span>
        ${page < data.pages ? `<button onclick="renderVenueReviews('${containerId}', ${venueId}, ${page + 1})" class="btn btn-sm">Next →</button>` : ''}
      </div>` : '';

    container.innerHTML = reviewsHtml + paginationHtml;
  } catch (e) {
    container.innerHTML = `<div style="color:var(--text-gray);font-size:0.8rem;padding:16px 0;">Could not load reviews</div>`;
  }
}
