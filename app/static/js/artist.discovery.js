import { apiGet, apiPost } from "./api.js";

/**
 * Find Artists — the venue-side mirror of venue.discovery.js.
 *
 * 2026-09-29. Until now discovery was one-directional: artists had a
 * dedicated "Find Venues" page with a Request Preferred button, while
 * venues had no browse surface at all — artist search existed only
 * inside the gig-creation flow, with no way to act on what you found.
 * A venue could not invite an artist it had never worked with, because
 * only artists already carrying a `preferred_artists` row appeared
 * anywhere in the venue UI.
 *
 * Invitations here write `status='invited'`, which grants nothing until
 * the artist accepts. See routes/preferred_artists.py.
 */

// HTML-escape everything interpolated into innerHTML. Artist names are
// user input; without this an artist named `<img src=x onerror=...>`
// would execute on every venue's discovery view. Same reasoning as the
// Jul 2026 audit fix in venue.discovery.js.
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

document.addEventListener("DOMContentLoaded", async () => {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get("venue_id");

  if (!venueId) {
    window.location.href = "/app/user-profile.html";
    return;
  }

  let allArtists = [];
  let related = [];          // this venue's existing preferred_artists rows
  let currentFilter = "all"; // artist_type, or "all"
  let searchTerm = "";
  let cityFilter = "";
  let stateFilter = "";

  async function loadArtists() {
    try {
      allArtists = await apiGet("/api/artists/search");
      // Every preferred_artists row for this venue, any status — the
      // endpoint deliberately has no status filter, which is what lets
      // us show approved / invited / pending / denied distinctly.
      related = await apiGet(`/api/venues/${venueId}/preferred-artists-with-gigs`);
      buildStateOptions();
      buildTypeFilters();
      updateStats();
      renderArtists();
    } catch (e) {
      console.error("Failed to load artists:", e);
      const c = document.getElementById("artistsList");
      if (c) c.innerHTML = `<div class="no-results"><h3>Couldn't load artists</h3><p>Please refresh and try again.</p></div>`;
    }
  }

  function statusFor(artistId) {
    const row = related.find(r => Number(r.artist_id) === Number(artistId));
    return row ? row.preferred_status : null;
  }

  function updateStats() {
    document.getElementById("totalArtists").textContent = allArtists.length;
    document.getElementById("preferredArtists").textContent =
      related.filter(r => r.preferred_status === "approved").length;
    document.getElementById("invitedArtists").textContent =
      related.filter(r => r.preferred_status === "invited").length;
  }

  // All 50 states from the shared us-states.js list, not just the ones
  // that happen to have artists today — otherwise a venue searching a
  // state with no artists yet sees no option and can't tell whether it
  // filtered to zero or the filter simply doesn't exist.
  function buildStateOptions() {
    const sel = document.getElementById("stateSelect");
    if (!sel) return;
    if (typeof getStatesHTML === "function") {
      sel.innerHTML = '<option value="">All States</option>' + getStatesHTML();
    }
  }

  function buildTypeFilters() {
    const wrap = document.getElementById("typeFilters");
    if (!wrap) return;
    const types = [...new Set(allArtists.map(a => (a.artist_type || "").trim()).filter(Boolean))].sort();
    wrap.innerHTML =
      `<div class="filter-chip ${currentFilter === "all" ? "active" : ""}" data-filter="all">All Artists</div>` +
      types.map(t =>
        `<div class="filter-chip ${currentFilter === t ? "active" : ""}" data-filter="${esc(t)}">${esc(t)}</div>`
      ).join("");
    wrap.querySelectorAll(".filter-chip").forEach(chip => {
      chip.addEventListener("click", () => {
        currentFilter = chip.dataset.filter;
        buildTypeFilters();
        renderArtists();
      });
    });
  }

  function filterArtists() {
    let out = allArtists;
    if (searchTerm) {
      const q = searchTerm.toLowerCase();
      out = out.filter(a =>
        (a.name || "").toLowerCase().includes(q) ||
        (a.styles || "").toLowerCase().includes(q) ||
        (a.band_formats || "").toLowerCase().includes(q)
      );
    }
    if (cityFilter) {
      const q = cityFilter.toLowerCase();
      out = out.filter(a => (a.city || "").toLowerCase().includes(q));
    }
    if (stateFilter) {
      out = out.filter(a => (a.state || "") === stateFilter);
    }
    if (currentFilter !== "all") {
      out = out.filter(a => (a.artist_type || "") === currentFilter);
    }
    return out;
  }

  function renderArtists() {
    const container = document.getElementById("artistsList");
    if (!container) return;
    const filtered = filterArtists();

    if (filtered.length === 0) {
      container.innerHTML = `
        <div class="no-results">
          <h3>No artists found</h3>
          <p>Try adjusting your filters or search terms</p>
        </div>
      `;
      return;
    }

    container.innerHTML = filtered.map(artist => {
      const status = statusFor(artist.id);
      const location = [artist.city, artist.state].filter(Boolean).join(", ") || "Location not set";

      let statusBadge = "";
      let actionButton = "";

      if (status === "approved") {
        statusBadge = `<span class="status-badge status-approved">✓ Preferred</span>`;
        actionButton = `<button class="btn ghost small" disabled>Already Preferred</button>`;
      } else if (status === "invited") {
        statusBadge = `<span class="status-badge status-invited">✉ Invited</span>`;
        actionButton = `<button class="btn ghost small" disabled>Awaiting Response</button>`;
      } else if (status === "pending") {
        // Artist asked first — send the venue to the existing approve flow
        // rather than inviting someone who already applied.
        statusBadge = `<span class="status-badge status-pending">⏳ They Requested</span>`;
        actionButton = `<button class="btn primary small" onclick="window.location.href='/app/venue-create-gigs.html?venue_id=${encodeURIComponent(venueId)}&tab=artists'">Review Request</button>`;
      } else {
        statusBadge = `<span class="status-badge status-none">${status === "denied" || status === "revoked" ? esc(status) : "Not Invited"}</span>`;
        actionButton = `<button class="btn primary small" onclick="invitePreferred(${Number(artist.id)}, '${esc(artist.name).replace(/'/g, "\\'")}')">Invite as Preferred</button>`;
      }

      const bits = [];
      if (artist.artist_type)  bits.push(`<span class="detail-item"><span class="detail-icon">🎤</span> ${esc(artist.artist_type)}</span>`);
      if (artist.band_formats) bits.push(`<span class="detail-item"><span class="detail-icon">👥</span> ${esc(artist.band_formats)}</span>`);
      if (artist.styles)       bits.push(`<span class="detail-item"><span class="detail-icon">🎵</span> ${esc(artist.styles)}</span>`);

      return `
        <div class="artist-card">
          <div class="artist-header">
            <div>
              <div class="artist-name">
                <a href="/app/artist-profile.html?artist_id=${Number(artist.id)}" target="_blank" style="color: inherit; text-decoration: none;">
                  ${esc(artist.name)}
                </a>
              </div>
              <div class="artist-location">📍 ${esc(location)}</div>
            </div>
            <div style="display: flex; gap: 12px; align-items: center;">
              ${statusBadge}
              ${actionButton}
            </div>
          </div>
          ${bits.length ? `<div class="artist-details">${bits.join("")}</div>` : ""}
        </div>
      `;
    }).join("");
  }

  // Invite → status='invited'. The artist gets an in-app notification and
  // an email, and becomes preferred only if they accept.
  window.invitePreferred = (artistId, artistName) => {
    const safeName = esc(artistName || "this artist");
    window.showStyledModal(
      "Invite as Preferred Artist",
      `<p style="margin:0 0 12px 0;">Invite <strong>${safeName}</strong> to become a Preferred Artist at your venue.</p>` +
      `<p style="margin:0 0 14px 0;color:var(--text-gray);font-size:0.85rem;">They'll get an email and can review your venue before accepting. They become preferred only once they accept.</p>` +
      `<label style="display:block;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-gray);margin-bottom:6px;">Add a note (optional)</label>` +
      `<textarea id="prefInviteMsg" rows="3" maxlength="500" placeholder="Caught your set last month — we'd love to have you on our regular list."
         style="width:100%;box-sizing:border-box;padding:9px 11px;border-radius:6px;border:1px solid var(--border);background:var(--bg-dark,#0f1419);color:var(--text);font-size:0.85rem;resize:vertical;"></textarea>`,
      [
        { text: "Cancel", style: "ghost" },
        { text: "Send Invitation", style: "primary", onClick: async () => {
            const el = document.getElementById("prefInviteMsg");
            const message = el ? el.value.trim() : "";
            try {
              await apiPost(`/api/venues/${venueId}/artists/${artistId}/make-preferred`, { message });
              await loadArtists();
              window.showSuccessModal && window.showSuccessModal(
                "Invitation Sent",
                `${artistName} has been invited and will get an email. They'll show as Preferred once they accept.`
              );
            } catch (e) {
              console.error("invitePreferred failed", e);
              window.showErrorModal && window.showErrorModal(
                "Could not send invitation", (e && e.message) || "Please try again.");
              return false;  // keep the modal open
            }
          }
        }
      ]
    );
  };

  // Wire search inputs
  const searchEl = document.getElementById("searchInput");
  if (searchEl) searchEl.addEventListener("input", (e) => { searchTerm = e.target.value; renderArtists(); });
  const cityEl = document.getElementById("cityInput");
  if (cityEl) cityEl.addEventListener("input", (e) => { cityFilter = e.target.value; renderArtists(); });
  const stateEl = document.getElementById("stateSelect");
  if (stateEl) stateEl.addEventListener("change", (e) => { stateFilter = e.target.value; renderArtists(); });

  await loadArtists();
});
