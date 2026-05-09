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
  
  const profileBtn = document.getElementById("artistProfileBtn");
  if (profileBtn) {
    profileBtn.href = `/app/artist-profile.html?artist_id=${artistId}`;
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
