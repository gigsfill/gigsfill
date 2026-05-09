import { apiGet, apiPost } from "./api.js";

document.addEventListener("DOMContentLoaded", async () => {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get("artist_id");
  
  if (!artistId) {
    alert("No artist selected");
    window.location.href = "/app/user-profile.html";
    return;
  }

  let allVenues = [];
  let preferredVenues = [];
  let currentFilter = "all";
  let searchTerm = "";
  let cityFilter = "";
  let stateFilter = "";

  // Load data
  async function loadVenues() {
    try {
      allVenues = await apiGet("/api/venues/public");
      preferredVenues = await apiGet(`/api/artist/preferred-venues?artist_id=${artistId}`);
      updateStats();
      renderVenues();
    } catch (e) {
      console.error("Failed to load venues:", e);
    }
  }

  function updateStats() {
    const approved = preferredVenues.filter(v => v.status === "approved").length;
    const pending = preferredVenues.filter(v => v.status === "pending").length;
    
    document.getElementById("totalVenues").textContent = allVenues.length;
    document.getElementById("approvedVenues").textContent = approved;
    document.getElementById("pendingVenues").textContent = pending;
  }

  function getPreferredStatus(venueId) {
    const pref = preferredVenues.find(v => v.venue_id === venueId);
    return pref ? pref.status : null;
  }

  function filterVenues() {
    let filtered = allVenues;

    // Search filter
    if (searchTerm) {
      filtered = filtered.filter(v => 
        v.venue_name.toLowerCase().includes(searchTerm.toLowerCase())
      );
    }

    // City filter
    if (cityFilter) {
      filtered = filtered.filter(v => 
        v.city && v.city.toLowerCase().includes(cityFilter.toLowerCase())
      );
    }

    // State filter
    if (stateFilter) {
      filtered = filtered.filter(v => v.state === stateFilter);
    }

    // Equipment filters
    if (currentFilter === "has_stage") {
      filtered = filtered.filter(v => v.has_stage);
    } else if (currentFilter === "has_sound") {
      filtered = filtered.filter(v => v.has_sound_equipment);
    } else if (currentFilter === "has_lighting") {
      filtered = filtered.filter(v => v.has_lighting);
    }

    return filtered;
  }

  function renderVenues() {
    const container = document.getElementById("venuesList");
    const filtered = filterVenues();

    if (filtered.length === 0) {
      container.innerHTML = `
        <div class="no-results">
          <h3>No venues found</h3>
          <p>Try adjusting your filters or search terms</p>
        </div>
      `;
      return;
    }

    container.innerHTML = filtered.map(venue => {
      const status = getPreferredStatus(venue.id);
      const location = [venue.city, venue.state].filter(Boolean).join(", ") || "Location not set";
      
      let statusBadge = "";
      let actionButton = "";

      if (status === "approved") {
        statusBadge = `<span class="status-badge status-approved">✓ Approved</span>`;
        actionButton = `<button class="btn primary small" onclick="window.location.href='/app/artist-book-gigs.html?artist_id=${artistId}'">View Gigs</button>`;
      } else if (status === "pending") {
        statusBadge = `<span class="status-badge status-pending">⏳ Pending</span>`;
        actionButton = `<button class="btn ghost small" disabled>Request Sent</button>`;
      } else {
        statusBadge = `<span class="status-badge status-none">Not Requested</span>`;
        actionButton = `<button class="btn primary small" onclick="requestPreferred(${venue.id}, ${artistId})">Request Preferred</button>`;
      }

      const features = [];
      if (venue.has_stage) features.push('<span class="detail-item"><span class="detail-icon">🎭</span> Stage</span>');
      if (venue.has_sound_equipment) features.push('<span class="detail-item"><span class="detail-icon">🔊</span> Sound</span>');
      if (venue.has_lighting) features.push('<span class="detail-item"><span class="detail-icon">💡</span> Lighting</span>');
      if (venue.has_sound_engineer) features.push('<span class="detail-item"><span class="detail-icon">👨‍🎤</span> Engineer</span>');

      const pay = venue.default_pay_dollars 
        ? `$${venue.default_pay_dollars}.${(venue.default_pay_cents || 0).toString().padStart(2, '0')}` 
        : "Pay varies";

      return `
        <div class="venue-card">
          <div class="venue-header">
            <div>
              <div class="venue-name">
                <a href="/app/venue-profile.html?venue_id=${venue.id}" target="_blank" style="color: inherit; text-decoration: none;">
                  ${venue.venue_name}
                </a>
              </div>
              <div class="venue-location">📍 ${location}</div>
            </div>
            <div style="display: flex; gap: 12px; align-items: center;">
              ${statusBadge}
              ${actionButton}
            </div>
          </div>

          ${venue.description ? `<p style="color: var(--text-secondary); margin-top: 12px; line-height: 1.6;">${venue.description}</p>` : ''}

          ${features.length > 0 ? `
            <div class="venue-details">
              ${features.join('')}
              <span class="detail-item"><span class="detail-icon">💵</span> ${pay}</span>
              ${venue.venue_size ? `<span class="detail-item"><span class="detail-icon">📏</span> ${venue.venue_size}</span>` : ''}
            </div>
          ` : ''}
        </div>
      `;
    }).join("");
  }

  // Request preferred status
  window.requestPreferred = async (venueId, artistId) => {
    try {
      await apiPost(`/api/venues/${venueId}/preferred/request?artist_id=${artistId}`, {});
      await loadVenues(); // Reload to update status
      showNotification("Request sent successfully!");
    } catch (e) {
      alert("Failed to send request: " + e.message);
    }
  };

  function showNotification(message) {
    const notification = document.createElement("div");
    notification.style.cssText = `
      position: fixed;
      top: 80px;
      right: 20px;
      background: var(--accent-cyan);
      color: #000;
      padding: 16px 24px;
      border-radius: 8px;
      font-weight: 600;
      z-index: 10000;
      animation: slideIn 0.3s ease;
    `;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
      notification.remove();
    }, 3000);
  }

  // Event listeners
  document.getElementById("searchInput").addEventListener("input", (e) => {
    searchTerm = e.target.value;
    renderVenues();
  });

  document.getElementById("cityInput").addEventListener("input", (e) => {
    cityFilter = e.target.value;
    renderVenues();
  });

  document.getElementById("stateSelect").addEventListener("change", (e) => {
    stateFilter = e.target.value;
    renderVenues();
  });

  document.querySelectorAll(".filter-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      currentFilter = chip.dataset.filter;
      renderVenues();
    });
  });

  // Set default active filter
  document.querySelector('[data-filter="all"]').classList.add("active");

  // Load initial data
  await loadVenues();
});
