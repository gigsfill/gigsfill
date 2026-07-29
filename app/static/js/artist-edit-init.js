// Auto-extracted from artist-edit.html inline scripts
// Generated for CSP compliance (Phase 5)

// === Block 1 of 3 ===
(function () {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  
  if (!artistId) {
    console.error("❌ artist_id missing on artist-edit.html");
    window.location.href = "/app/user-profile.html";
    return;
  }
  
  window.artistId = artistId;
})();
  

// === Block 2 of 3 ===
// City autocomplete handled by shared city-autocomplete.js module
document.addEventListener('DOMContentLoaded', function(){
  initCityAutocomplete({ inputId: 'city', stateId: 'state' });
});


// === Block 3 of 3 ===
(function () {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  if (!artistId) return;

  const bookGigsBtn = document.getElementById("bookGigsBtn");
  if (bookGigsBtn) {
    bookGigsBtn.href = `/app/artist-book-gigs.html?artist_id=${artistId}`;
  }
  
  if (typeof window.applyVanityToLinks === "function") {
    window.applyVanityToLinks("artist", artistId, ["#artistProfileBtn"]);
  }
})();
  


// === Availability Panel ===
(function() {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get('artist_id');
  if (!artistId) return;

  const section = document.getElementById('availabilitySection');
  if (section) section.style.display = '';

  if (typeof renderAvailabilityPanel === 'function') {
    renderAvailabilityPanel('availabilityContainer', parseInt(artistId));
  }
})();


// ── MESSAGES HEADER BUTTON (2026-07-26) ─────────────────────────────────────
// Mirrors the wiring in artist-book-gigs-init.js so the Messages button
// persists across the Book Gigs → Edit Artist Profile navigation instead
// of vanishing. Requires messages.js to be loaded above (it exposes
// window.openInboxModal + startUnreadBadgePolling).
window.showRecentMessages = function () {
  const _p = new URLSearchParams(window.location.search);
  const _aid = _p.get('artist_id');
  if (typeof window.openInboxModal === 'function') {
    window.openInboxModal({ side: 'artist', artistId: _aid ? parseInt(_aid) : null });
  }
};

(function initArtistMessages() {
  const _p = new URLSearchParams(window.location.search);
  const _aid = _p.get('artist_id');
  if (!_aid) return;  // no artist context → button stays hidden
  const btn = document.getElementById('headerMsgBtn');
  if (btn) btn.style.display = '';
  if (typeof startUnreadBadgePolling === 'function') {
    startUnreadBadgePolling(30000, { artist_id: parseInt(_aid) });
  }
})();
