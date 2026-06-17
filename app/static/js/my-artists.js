// v73: My Artists Component for Venue Create Gigs Page

// Audit fix (May 2026 part 7): HTML/attr escape helpers. Artist names + cities
// are user-controlled and were interpolated raw into innerHTML + inline onclick
// args (XSS sinks). _ma_esc neutralizes HTML; _ma_attr also escapes apostrophes
// + backslashes for inline event handler string args.
function _ma_esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function _ma_attr(s) {
  // Safe for JS-string-literal-in-HTML-attr (escape \, ', ", &lt;, &gt;).
  return String(s == null ? '' : s)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, '\\&#39;')
    .replace(/"/g, '\\&quot;')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

class MyArtists {
  constructor(venueId) {
    this.venueId = venueId;
    this.artists = [];
    this.activeFilters = new Set(['preferred']); // v73: Default to Preferred
    this.expandedArtists = new Set();
    this.showPastGigs = false; // Track if showing past gigs
    this.collapsedGigs = new Set(); // Track collapsed gig sections
    this.gigPages = {}; // Track current page per artist: { artistId: pageNumber }
    this.GIGS_PER_PAGE = 10;
    this.init();
  }

  async init() {
    await this.loadArtists();
    
    // v73: Auto-expand preferred artists with gigs
    const expandPromises = [];
    this.artists.forEach(artist => {
      if (artist.preferred_status === 'approved') {
        const gigsCount = artist.gigs_count || 0;
        if (gigsCount > 0) {
          this.expandedArtists.add(artist.artist_id);
          expandPromises.push(this.expandArtist(artist.artist_id));
        }
      }
    });
    await Promise.all(expandPromises);
    
    this.render();
  }

  async loadArtists() {
    try {
      // Load preferred artists with their gigs
      const response = await fetch(`/api/venues/${this.venueId}/preferred-artists-with-gigs`, {
        credentials: 'include'
      });
      
      if (response.ok) {
        this.artists = await response.json();
        // v93: Auto-render after loading
        this.render();
      }
    } catch (error) {
      console.error("❌ v73: Error loading artists:", error);
    }
  }

  calculateStats() {
    const stats = {
      gigsBooked: 0,
      pastGigs: 0,
      preferred: 0,
      pending: 0,
      denied: 0,
      revoked: 0,
      nonPreferred: 0,
      banned: 0
    };
    
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    
    this.artists.forEach(artist => {
      if (artist.gigs_count > 0) stats.gigsBooked++;
      
      // Count past gigs
      if (artist.gigs) {
        artist.gigs.forEach(gig => {
          const [year, month, day] = gig.date.split('-');
          const gigDate = new Date(parseInt(year), parseInt(month) - 1, parseInt(day));
          gigDate.setHours(0, 0, 0, 0);
          if (gigDate < today) {
            stats.pastGigs++;
          }
        });
      }
      
      if (artist.preferred_status === 'approved') stats.preferred++;
      else if (artist.preferred_status === 'pending') stats.pending++;
      else if (artist.preferred_status === 'denied') stats.denied++;
      // Part 10j: 'revoked' is its own bubble now (only counts those WITH gigs —
      // a revoked artist with bookings whose history still needs a home).
      else if (artist.preferred_status === 'revoked') { if ((artist.gigs_count || 0) > 0) stats.revoked++; }
      else if (artist.preferred_status === 'banned') stats.banned++;
      // Part 10k: non-preferred artists with gig history (no preferred row, not banned).
      else if (artist.preferred_status === 'non_preferred') { if ((artist.gigs_count || 0) > 0) stats.nonPreferred++; }
    });
    
    return stats;
  }

  async toggleFilter(filter) {
    // Exclusive toggle - clicking activates the filter (or turns off if already active)
    if (this.activeFilters.has(filter)) {
      // Clicking active filter turns it off, shows all
      this.activeFilters.clear();
    } else {
      // Clicking inactive filter makes it the only active filter
      this.activeFilters.clear();
      this.activeFilters.add(filter);
    }
    
    // Always clear showPastGigs when changing filters
    this.showPastGigs = false;
    
    // Auto-expand artists with gigs based on filter
    this.expandedArtists.clear();
    const expandPromises = [];
    
    if (filter === 'preferred') {
      this.artists.forEach(artist => {
        if (artist.preferred_status === 'approved') {
          const gigsCount = artist.gigs_count || 0;
          if (gigsCount > 0) {
            this.expandedArtists.add(artist.artist_id);
            expandPromises.push(this.expandArtist(artist.artist_id));
          }
        }
      });
    } else if (filter === 'pastGigs') {
      // Show past gigs view
      this.showPastGigs = true;
      
      // Expand all artists with gigs
      this.artists.forEach(artist => {
        const gigsCount = artist.gigs_count || 0;
        if (gigsCount > 0) {
          this.expandedArtists.add(artist.artist_id);
          expandPromises.push(this.expandArtist(artist.artist_id));
        }
      });
    }
    
    await Promise.all(expandPromises);
    this.render();
  }

  async toggleArtist(artistId) {
    if (this.expandedArtists.has(artistId)) {
      this.expandedArtists.delete(artistId);
    } else {
      this.expandedArtists.add(artistId);
      // Load gigs if not already loaded
      await this.expandArtist(artistId);
    }
    this.render();
  }

  async expandArtist(artistId) {
    const artist = this.artists.find(a => a.artist_id === artistId);
    if (!artist || artist.gigs) return;
    
    try {
      const response = await fetch(`/api/artists/${artistId}/gigs-at-venue/${this.venueId}`, {
        credentials: 'include'
      });
      
      if (response.ok) {
        artist.gigs = await response.json();
      }
    } catch (error) {
      console.error(`❌ v73: Error loading gigs for artist ${artistId}:`, error);
    }
  }

  filterArtists() {
    if (this.activeFilters.size === 0) return [];
    
    return this.artists.filter(artist => {
      if (this.activeFilters.has('gigs') && artist.gigs_count > 0) return true;
      if (this.activeFilters.has('preferred') && artist.preferred_status === 'approved') return true;
      if (this.activeFilters.has('pending') && artist.preferred_status === 'pending') return true;
      if (this.activeFilters.has('denied') && artist.preferred_status === 'denied') return true;
      // Part 10j: revoked is its own filter; only show revoked artists who have gigs.
      if (this.activeFilters.has('revoked') && artist.preferred_status === 'revoked' && (artist.gigs_count || 0) > 0) return true;
      // Part 10k: non-preferred (guest) artists with gigs.
      if (this.activeFilters.has('nonPreferred') && artist.preferred_status === 'non_preferred' && (artist.gigs_count || 0) > 0) return true;
      if (this.activeFilters.has('banned') && artist.preferred_status === 'banned') return true;
      // Show all artists with gigs for pastGigs filter
      if (this.activeFilters.has('pastGigs') && artist.gigs_count > 0) return true;
      return false;
    });
  }

  async approveArtist(artistId) {
    // Add loading state to button
    const btn = event?.target;
    const origText = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Approving...'; }
    try {
      await fetch(`/api/preferred-artists/${artistId}/approve`, {
        method: 'PUT',
        credentials: 'include'
      });
      await this.loadArtists();
      this.render();
      this.updateBadge();
      
      // v73: Reload Activity Center on venue page
      if (window.activityCenterVenue) {
        await window.activityCenterVenue.loadNotifications();
      }
    } catch (error) {
      console.error('Error approving artist:', error);
      if (btn) { btn.disabled = false; btn.textContent = origText; }
    }
  }

  async denyArtist(artistId) {
    const btn = event?.target;
    const origText = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Declining...'; }
    try {
      await fetch(`/api/preferred-artists/${artistId}/deny`, {
        method: 'PUT',
        credentials: 'include'
      });
      await this.loadArtists();
      this.render();
      this.updateBadge();
      
      // v73: Reload Activity Center on venue page
      if (window.activityCenterVenue) {
        await window.activityCenterVenue.loadNotifications();
      }
    } catch (error) {
      console.error('Error denying artist:', error);
      if (btn) { btn.disabled = false; btn.textContent = origText; }
    }
  }
  
  updateBadge() {
    const badge = document.getElementById('artistsBadge');
    if (badge && this.artists) {
      const approvedCount = this.artists.filter(a => a.preferred_status === 'approved').length;
      badge.textContent = `(${approvedCount})`;
    }
  }
  
  // Phase 2 migration (May 2026): three confirm-then-act modals (revoke
  // preferred / ban / unban) used to be inline-styled DOM builders. Replaced
  // with showStyledModal calls so they pick up the unified modal look. Each
  // wires its primary button via an async onClick that returns false to
  // keep the modal open while the network request is in flight, then
  // closes via closeAllModals on success.
  // Note: the bare alert() fallback on network errors was kept in spirit
  // by routing failures through showErrorModal — visible feedback rather
  // than the legacy native alert.
  async makePreferred(artistId, artistName) {
    try {
      const r = await fetch(`/api/venues/${this.venueId}/artists/${artistId}/make-preferred`, {
        method: 'POST', credentials: 'include'
      });
      if (r.ok) {
        await this.loadArtists();
        this.activeFilters.clear();
        this.activeFilters.add('preferred');
        this.render();
      } else {
        const d = await r.json().catch(() => ({}));
        window.showErrorModal && window.showErrorModal('Could not make preferred', d.detail || 'Please try again.');
      }
    } catch (e) {
      console.error('makePreferred failed', e);
    }
  }

  async revokePreferred(artistId, preferredId, artistName) {
    const safeName = (artistName || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
    window.showStyledModal(
      'Revoke Preferred Status',
      `<div class="gf-notice gf-notice--error" style="margin-bottom:14px;"><strong>Are you sure?</strong> Revoke the preferred status for <strong>${safeName}</strong>?</div>` +
      `<p>This artist will no longer be able to book future gigs at your venue. Existing booked gigs will remain unchanged.</p>`,
      [
        { text: 'Cancel', style: 'ghost' },
        {
          text: 'Confirm Revoke', style: 'danger',
          onClick: async () => {
            try {
              const response = await fetch(`/api/preferred-artists/${preferredId}/revoke`, { method: 'PUT', credentials: 'include' });
              if (response.ok) {
                window.closeAllModals();
                await this.loadArtists();
                this.render();
                if (window.activityCenterVenue) await window.activityCenterVenue.loadNotifications();
              } else {
                window.showErrorModal('Revoke Failed', 'Could not revoke preferred status. Please try again.');
              }
            } catch (error) {
              console.error('Error revoking preferred status:', error);
              window.showErrorModal('Revoke Failed', 'Network error — please try again.');
            }
            return false; // keep modal open until handler decides
          }
        },
      ],
      { tone: 'error' }
    );
  }

  async banArtist(artistId, artistName) {
    const safeName = (artistName || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
    window.showStyledModal(
      '🚫 Ban Artist',
      `<div class="gf-notice gf-notice--error" style="margin-bottom:14px;">Ban <strong>${safeName}</strong> from your venue?</div>` +
      `<p>They will be permanently blocked from booking any gig at your venue — even during blast windows. This cannot be undone without manually removing the ban.</p>` +
      `<label style="font-size:0.85rem;color:var(--text-gray);display:block;margin:14px 0 6px;">Reason <span style="color:#6b7280;">(optional)</span></label>` +
      `<input id="_banReasonInput" type="text" placeholder="e.g. No-show, misconduct...">`,
      [
        { text: 'Cancel', style: 'ghost' },
        {
          text: 'Confirm Ban', style: 'danger',
          onClick: async () => {
            const reason = (document.getElementById('_banReasonInput')?.value || '').trim();
            try {
              const r = await fetch(`/api/venues/${this.venueId}/ban-artist/${artistId}`, {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({reason})
              });
              if (r.ok) {
                window.closeAllModals();
                await this.loadArtists();
                this.render();
                if (typeof window.refreshSearchArtistsBanned === 'function') window.refreshSearchArtistsBanned();
              } else {
                window.showErrorModal('Ban Failed', 'Could not ban artist. Please try again.');
              }
            } catch (e) {
              window.showErrorModal('Ban Failed', 'Network error — please try again.');
            }
            return false;
          }
        },
      ],
      { tone: 'error' }
    );
  }

  async unbanArtist(artistId, artistName) {
    const safeName = (artistName || '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
    window.showStyledModal(
      'Remove Ban',
      `<div class="gf-notice gf-notice--success" style="margin-bottom:14px;">Remove ban for <strong>${safeName}</strong>?</div>` +
      `<p>They will be able to request preferred artist status again and may appear in future blast emails.</p>`,
      [
        { text: 'Cancel', style: 'ghost' },
        {
          text: 'Remove Ban', style: 'primary',
          onClick: async () => {
            try {
              const r = await fetch(`/api/venues/${this.venueId}/ban-artist/${artistId}`, { method: 'DELETE', credentials: 'include' });
              if (r.ok) {
                window.closeAllModals();
                await this.loadArtists();
                this.render();
                if (typeof window.refreshSearchArtistsBanned === 'function') window.refreshSearchArtistsBanned();
              } else {
                window.showErrorModal('Unban Failed', 'Could not remove ban. Please try again.');
              }
            } catch (e) {
              window.showErrorModal('Unban Failed', 'Network error — please try again.');
            }
            return false;
          }
        },
      ],
      { tone: 'success' }
    );
  }

  async togglePastGigs() {
    await this.toggleFilter('pastGigs');
  }

  render() {
    const container = document.getElementById('myArtists');
    if (!container) return;

    const stats = this.calculateStats();
    const filteredArtists = this.filterArtists();
    const isActive = (filter) => this.activeFilters.has(filter);

    container.innerHTML = `
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 0.75rem; flex-wrap: wrap;">
        <h2 style="margin: 0; font-size: 1rem; white-space: nowrap;">My Artists</h2>
        
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
          <div class="stat-bubble" onclick="myArtists.toggleFilter('preferred')" style="background: ${isActive('preferred') ? 'rgba(34, 197, 94, 0.3)' : 'rgba(34, 197, 94, 0.1)'}; border: 2px solid ${isActive('preferred') ? '#22c55e' : 'rgba(34, 197, 94, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('preferred') ? '0 0 12px rgba(34, 197, 94, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #22c55e;">${stats.preferred}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred Artists</span>
          </div>

          ${stats.revoked > 0 ? `<div class="stat-bubble" onclick="myArtists.toggleFilter('revoked')" style="background: ${isActive('revoked') ? 'rgba(245, 158, 11, 0.3)' : 'rgba(245, 158, 11, 0.1)'}; border: 2px solid ${isActive('revoked') ? '#f59e0b' : 'rgba(245, 158, 11, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('revoked') ? '0 0 12px rgba(245, 158, 11, 0.5)' : 'none'};" title="Artists whose preferred status was revoked but who have past gigs with you">
            <span style="font-size: 0.9rem; font-weight: 600; color: #f59e0b;">${stats.revoked}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred Status Revoked</span>
          </div>` : ''}

          <div class="stat-bubble" onclick="myArtists.toggleFilter('denied')" style="background: ${isActive('denied') ? 'rgba(239, 68, 68, 0.3)' : 'rgba(239, 68, 68, 0.1)'}; border: 2px solid ${isActive('denied') ? '#ef4444' : 'rgba(239, 68, 68, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('denied') ? '0 0 12px rgba(239, 68, 68, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #ef4444;">${stats.denied}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred Status Denied</span>
          </div>

          ${stats.nonPreferred > 0 ? `<div class="stat-bubble" onclick="myArtists.toggleFilter('nonPreferred')" style="background: ${isActive('nonPreferred') ? 'rgba(56, 189, 248, 0.3)' : 'rgba(56, 189, 248, 0.1)'}; border: 2px solid ${isActive('nonPreferred') ? '#38bdf8' : 'rgba(56, 189, 248, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('nonPreferred') ? '0 0 12px rgba(56, 189, 248, 0.5)' : 'none'};" title="Artists who played a gig without being preferred (e.g. last-minute / open-window bookings)">
            <span style="font-size: 0.9rem; font-weight: 600; color: #38bdf8;">${stats.nonPreferred}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Non-Preferred Artists</span>
          </div>` : ''}

          ${stats.banned > 0 ? `<div class="stat-bubble" onclick="myArtists.toggleFilter('banned')" style="background: ${isActive('banned') ? 'rgba(127,29,29,0.4)' : 'rgba(127,29,29,0.15)'}; border: 2px solid ${isActive('banned') ? '#ef4444' : 'rgba(239,68,68,0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('banned') ? '0 0 12px rgba(239,68,68,0.4)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #fca5a5;">${stats.banned}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">🚫 Banned Artists</span>
          </div>` : ''}
        </div>
      </div>

      <div style="display: flex; flex-direction: column; gap: 6px;">
        ${this.activeFilters.size === 0 ? '<p style="text-align: left; color: var(--text-muted); padding: 1rem;">No artist filters selected</p>' : filteredArtists.length === 0 ? '<p style="text-align: left; color: var(--text-muted); padding: 1rem;">No artists match your filters</p>' : ''}
        ${this.activeFilters.size > 0 ? filteredArtists.map(a => this.renderArtist(a)).join('') : ''}
      </div>
    `;

    // After render: check each Rate Artist button and update label if already reviewed
    if (typeof _checkAndMarkArtistReviewed === 'function') {
      container.querySelectorAll('._rateArtistBtn').forEach(btn => {
        const aid = btn.dataset.artistId || btn.getAttribute('data-artist-id');
        if (aid) _checkAndMarkArtistReviewed(btn, parseInt(aid));
      });
    }
  }

  renderArtist(artist) {
    const artistId = artist.artist_id;
    const artistName = artist.artist_name;
    const status = artist.preferred_status;
    
    // Filter gigs based on showPastGigs
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    
    let filteredGigs = [];
    if (artist.gigs) {
      filteredGigs = artist.gigs.filter(gig => {
        const [year, month, day] = gig.date.split('-');
        const gigDate = new Date(parseInt(year), parseInt(month) - 1, parseInt(day));
        gigDate.setHours(0, 0, 0, 0);
        
        if (this.showPastGigs) {
          return gigDate < today; // Show only past gigs
        } else {
          return gigDate >= today; // Show only today and future gigs
        }
      });
    }
    
    const gigsCount = filteredGigs.length;
    const hasGigs = gigsCount > 0;
    
    // If in pastGigs mode and no past gigs, don't render this artist
    if (this.showPastGigs && gigsCount === 0) {
      return '';
    }
    
    // v73: Helper function to format time
    const formatTime = (time24) => {
      if (!time24) return '';
      const parts = time24.split(':');
      let hours = parseInt(parts[0]);
      const minutes = parts[1];
      const ampm = hours >= 12 ? 'PM' : 'AM';
      hours = hours % 12 || 12;
      return `${hours}:${minutes} ${ampm}`;
    };
    
    let statusBadge = '';
    if (status === 'approved') {
      statusBadge = '<span style="background: rgba(34, 197, 94, 0.2); border: 1px solid rgba(34, 197, 94, 0.5); color: #22c55e; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Preferred</span>';
    } else if (status === 'pending') {
      statusBadge = '<span style="background: rgba(249, 115, 22, 0.2); border: 1px solid rgba(249, 115, 22, 0.5); color: #f97316; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Pending</span>';
    } else if (status === 'denied') {
      statusBadge = '<span style="background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.5); color: #ef4444; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Denied</span>';
    } else if (status === 'revoked') {
      statusBadge = '<span style="background: rgba(245, 158, 11, 0.2); border: 1px solid rgba(245, 158, 11, 0.5); color: #f59e0b; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Preferred Status Revoked</span>';
    } else if (status === 'non_preferred') {
      statusBadge = '<span style="background: rgba(56, 189, 248, 0.2); border: 1px solid rgba(56, 189, 248, 0.5); color: #38bdf8; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Non-Preferred</span>';
    }
    
    // Pay + frequency override INPUT values. When no override has been set
    // (a freshly-approved preferred artist) we default to 0 so the venue
    // explicitly sees "no override" rather than the inherited venue
    // default (which would mislead them into thinking they'd set one).
    // The backend treats override = 0 / NULL identically (max() with the
    // gig pay wins), so 0 here is purely a clearer UI default.
    const payDollars = (artist.pay_dollars_override != null) ? artist.pay_dollars_override : 0;
    const payCents = String((artist.pay_cents_override != null) ? artist.pay_cents_override : 0).padStart(2, '0');
    const freqDays = (artist.frequency_days_override != null) ? artist.frequency_days_override : 0;
    const showOverrideChip = (status === 'approved');
    
    // Audit fix (May 2026 part 7): escape every user-controlled field.
    const _aid_safe = parseInt(artistId, 10) || 0;
    const _aname_h = _ma_esc(artistName);
    const _aname_a = _ma_attr(artistName);
    const _city_h  = _ma_esc(artist.artist_city || '');
    const _state_h = _ma_esc(artist.artist_state || '');
    return `
      <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 8px;">
        <div style="display: grid; grid-template-columns: minmax(160px, 1.5fr) auto auto; align-items: center; gap: 10px;">
          <div style="display: flex; flex-direction: column; gap: 2px; min-width: 0;">
            <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
              <a href="/app/artist-profile.html?artist_id=${_aid_safe}" target="_blank" style="font-weight: 600; font-size: 0.9rem; color: #7c6bff; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${_aname_h}</a>
              <span style="font-size: 0.75rem; color: var(--text-muted); white-space: nowrap;">${_city_h}, ${_state_h}</span>
            </div>
            <span onclick="myArtists.openPastGigsModal(${_aid_safe}, '${_aname_a}')" style="font-size: 0.68rem; color: #3b82f6; white-space: nowrap; cursor: pointer; width: fit-content;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'" title="View past gigs with this artist">📅 Past Gigs ›</span>
          </div>
          ${showOverrideChip ? `<div onclick="event.stopPropagation()" style="display: flex; align-items: center; gap: 12px; background: rgba(99,91,255,0.08); border: 1px solid rgba(99,91,255,0.2); border-radius: 6px; padding: 5px 12px; white-space: nowrap;">
            <span style="font-size: 0.7rem; color: rgba(124,107,255,0.7); font-weight: 600; letter-spacing: 0.02em;">Override Settings:</span>
            <div style="display: flex; align-items: center; gap: 4px;">
              <span style="font-size: 0.75rem; color: var(--text-muted);">Pay:</span>
              <span style="color: var(--text-muted); font-size: 0.8rem;">$</span>
              <input type="text" value="${payDollars}" data-pref-id="${artist.preferred_id}" data-field="pay_dollars"
                onblur="myArtists.saveOverride(${artist.preferred_id}, 'pay_dollars_override', this.value.replace(/,/g,''))"
                onkeypress="if(event.key==='Enter'){this.blur()}"
                style="width: 52px; padding: 3px 6px; background: rgba(21,27,40,0.8); border: 1px solid rgba(99,91,255,0.3); border-radius: 4px; color: white; font-size: 0.8rem; text-align: right;">
              <span style="color: var(--text-muted); font-size: 0.8rem;">.</span>
              <input type="text" value="${payCents}" maxlength="2" data-pref-id="${artist.preferred_id}" data-field="pay_cents"
                onblur="myArtists.saveOverride(${artist.preferred_id}, 'pay_cents_override', this.value)"
                onkeypress="if(event.key==='Enter'){this.blur()}"
                style="width: 28px; padding: 3px 4px; background: rgba(21,27,40,0.8); border: 1px solid rgba(99,91,255,0.3); border-radius: 4px; color: white; font-size: 0.8rem; text-align: center;">
            </div>
            <div style="width: 1px; height: 16px; background: rgba(99,91,255,0.25);"></div>
            <div style="display: flex; align-items: center; gap: 4px;">
              <span style="font-size: 0.75rem; color: var(--text-muted);">Frequency:</span>
              <span style="font-size: 0.75rem; color: var(--text-muted);">1 per</span>
              <input type="number" value="${freqDays}" min="0" max="365" data-pref-id="${artist.preferred_id}" data-field="freq"
                onblur="myArtists.saveOverride(${artist.preferred_id}, 'frequency_days_override', this.value)"
                onkeypress="if(event.key==='Enter'){this.blur()}"
                style="width: 44px; padding: 3px 4px; background: rgba(21,27,40,0.8); border: 1px solid rgba(99,91,255,0.3); border-radius: 4px; color: white; font-size: 0.8rem; text-align: center;">
              <span style="font-size: 0.75rem; color: var(--text-muted);">days</span>
            </div>
          </div>` : '<div></div>'}
          <div onclick="event.stopPropagation()" style="display: flex; align-items: center; gap: 8px; justify-content: flex-end;">
            ${status === 'pending' ? `
              <button onclick="event.stopPropagation(); myArtists.approveArtist(${artist.preferred_id})" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #22c55e; border: 1px solid #22c55e; line-height: 1.4; font-weight: 500;" title="Approve as Preferred Artist — they can book your gigs directly">Approve</button>
              <button onclick="event.stopPropagation(); myArtists.denyArtist(${artist.preferred_id})" class="btn ghost" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500;" title="Deny this preferred status request">Deny</button>
              <button onclick="event.stopPropagation(); myArtists.banArtist(${_aid_safe}, '${_aname_a}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; line-height: 1.4; font-weight: 500;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
            ` : (status === 'denied' || status === 'revoked') ? `
              <button onclick="event.stopPropagation(); openReviewModal({ artistId: ${_aid_safe}, artistName: '${_aname_a}' })" class="btn ghost _rateArtistBtn" data-artist-id="${_aid_safe}" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500; color:#f59e0b; border-color:rgba(245,158,11,0.3);" title="Leave a review for this artist">Rate Artist</button>
              <button onclick="event.stopPropagation(); myArtists.approveArtist(${artist.preferred_id})" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #22c55e; border: 1px solid #22c55e; line-height: 1.4; font-weight: 500;" title="Approve as Preferred Artist — they can book your gigs directly">Approve</button>
              <button onclick="event.stopPropagation(); myArtists.banArtist(${_aid_safe}, '${_aname_a}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; line-height: 1.4; font-weight: 500;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
            ` : status === 'approved' ? `
              <button onclick="event.stopPropagation(); openReviewModal({ artistId: ${_aid_safe}, artistName: '${_aname_a}' })" class="btn ghost _rateArtistBtn" data-artist-id="${_aid_safe}" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500; color:#f59e0b; border-color:rgba(245,158,11,0.3);" title="Leave a review for this artist">Rate Artist</button>
              <button onclick="event.stopPropagation(); myArtists.revokePreferred(${_aid_safe}, ${artist.preferred_id}, '${_aname_a}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #ef4444; border: 1px solid #ef4444; color: white; line-height: 1.4; font-weight: 500;" title="Revoke preferred status — artist can no longer book gigs normally">Revoke</button>
              <button onclick="event.stopPropagation(); myArtists.banArtist(${_aid_safe}, '${_aname_a}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; line-height: 1.4; font-weight: 500;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
            ` : status === 'non_preferred' ? `
              <button onclick="event.stopPropagation(); openReviewModal({ artistId: ${_aid_safe}, artistName: '${_aname_a}' })" class="btn ghost _rateArtistBtn" data-artist-id="${_aid_safe}" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500; color:#f59e0b; border-color:rgba(245,158,11,0.3);" title="Leave a review for this artist">Rate Artist</button>
              <button onclick="event.stopPropagation(); myArtists.makePreferred(${_aid_safe}, '${_aname_a}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #22c55e; border: 1px solid #22c55e; line-height: 1.4; font-weight: 500;" title="Make this artist a Preferred Artist — they can book your gigs directly">Make Preferred</button>
              <button onclick="event.stopPropagation(); myArtists.banArtist(${_aid_safe}, '${_aname_a}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; line-height: 1.4; font-weight: 500;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
            ` : status === 'banned' ? `
              <span style="font-size:0.7rem;color:#ef4444;font-weight:600;padding:3px 8px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:4px;" title="This artist is permanently banned from your venue">🚫 Banned</span>
              <button onclick="event.stopPropagation(); myArtists.unbanArtist(${_aid_safe}, '${_aname_a}')" class="btn ghost" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500;" title="Remove ban — artist can request preferred status again">Remove Ban</button>
            ` : ''}
          </div>
        </div>
        
        ${artist.waitlist_gig_id ? (() => {
          const [wy, wm, wd] = (artist.waitlist_gig_date || '').split('-');
          const wDateStr = wy ? new Date(parseInt(wy), parseInt(wm)-1, parseInt(wd)).toLocaleDateString() : '';
          const wStart = artist.waitlist_gig_start ? (typeof formatTime === 'function' ? formatTime(artist.waitlist_gig_start) : artist.waitlist_gig_start) : '';
          const wEnd = artist.waitlist_gig_end ? (typeof formatTime === 'function' ? formatTime(artist.waitlist_gig_end) : artist.waitlist_gig_end) : '';
          const wTimeStr = wStart ? (wEnd ? `${wStart} – ${wEnd}` : wStart) : '';
          return `
            <div style="margin-top:6px; margin-left:20px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.08);">
              <div onclick="event.stopPropagation(); if(typeof window.openWaitlistModal==='function') window.openWaitlistModal(${artist.waitlist_gig_id},${this.venueId},'${wDateStr}${wTimeStr ? ' · ' + wTimeStr : ''}');" style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding:5px 8px; background:rgba(139,92,246,0.08); border:1px solid rgba(139,92,246,0.2); border-radius:5px; cursor:pointer;" title="Click to view full waitlist">
                <div style="font-size:0.82rem; color:#e2e8f0; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                  ${wDateStr ? `<strong style="white-space:nowrap;">${wDateStr}</strong><span style="color:rgba(255,255,255,0.3);">|</span>` : ''}
                  ${wTimeStr ? `<span style="white-space:nowrap; color:#e2e8f0;">${wTimeStr}</span><span style="color:rgba(255,255,255,0.3);">|</span>` : ''}
                  <span style="color:#a78bfa; font-weight:600; white-space:nowrap;">⏳ Waitlisted — ${artist.waitlist_position} of ${artist.waitlist_total}</span>
                </div>
                <span style="color:#a78bfa; font-size:0.72rem; white-space:nowrap; opacity:0.7;">View all ›</span>
              </div>
            </div>
          `;
        })() : ''}

        ${hasGigs && filteredGigs.length > 0 ? `
          <div onclick="event.stopPropagation()" style="margin-top: 8px; margin-left: 20px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.08);">
            <div onclick="myArtists.toggleGigsCollapse(${artistId})" style="display: flex; align-items: center; gap: 6px; cursor: pointer; margin-bottom: 6px; user-select: none;" title="${this.collapsedGigs.has(artistId) ? 'Show' : 'Hide'} gigs">
              <span style="color: var(--text-muted); font-size: 0.7rem; transition: transform 0.2s; display: inline-block; transform: rotate(${this.collapsedGigs.has(artistId) ? '0' : '90'}deg);">▶</span>
              <span style="font-size: 0.75rem; color: var(--text-muted);">${gigsCount} gig${gigsCount !== 1 ? 's' : ''}</span>
            </div>
            ${!this.collapsedGigs.has(artistId) ? (() => {
              const page = this.gigPages[artistId] || 1;
              const totalPages = Math.ceil(filteredGigs.length / this.GIGS_PER_PAGE);
              const start = (page - 1) * this.GIGS_PER_PAGE;
              const pageGigs = filteredGigs.slice(start, start + this.GIGS_PER_PAGE);
              return `
                ${pageGigs.map(gig => {
                  const [year, month, day] = gig.date.split('-');
                  const dateStr = new Date(parseInt(year), parseInt(month) - 1, parseInt(day)).toLocaleDateString();
                  return `
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding:5px 8px; background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25); border-radius:5px; margin-bottom:4px;">
                      <div style="font-size:0.82rem; color:#e2e8f0; display:flex; align-items:center; gap:10px; flex-wrap:wrap; cursor:pointer;" onclick="myArtists.showGigDetails(${gig.id})" onmouseover="this.parentElement.style.background='rgba(239,68,68,0.14)'" onmouseout="this.parentElement.style.background='rgba(239,68,68,0.08)'">
                        <strong style="white-space:nowrap;">${dateStr}</strong><span style="color:rgba(255,255,255,0.3);">|</span>
                        <span style="white-space:nowrap;">${formatTime(gig.start_time)} – ${formatTime(gig.end_time)}</span><span style="color:rgba(255,255,255,0.3);">|</span>
                        <span style="color:#f87171; font-weight:600; white-space:nowrap;">${({'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠'}[gig.artist_type] || '🎵')} Booked • ${(() => {
                          // Door-deal aware: prefer the per-artist slot's pay_summary.
                          const _mySlot = (gig.slots || []).find(s => parseInt(s.artist_id) === parseInt(artist.artist_id));
                          if (_mySlot && window.formatPaySummary && (window.hasDoorDeal && window.hasDoorDeal(_mySlot) || _mySlot.pay_summary)) {
                            return window.formatPaySummary(_mySlot);
                          }
                          const _eff = (gig.effective_pay != null ? gig.effective_pay : gig.pay);
                          return _eff != null ? `$${Number(_eff).toFixed(2)}` : 'N/A';
                        })()}</span>
                      </div>
                      <span style="display:flex;gap:4px;flex-shrink:0;align-items:center;">
                        <span onclick="event.stopPropagation(); typeof openMessageModal === 'function' && openMessageModal(${parseInt(gig.id,10)||0}, '${_ma_attr(artist.artist_name || 'Artist')}')" style="font-size:0.7rem; color:#06b6d4; cursor:pointer; white-space:nowrap; padding:2px 7px; border-radius:4px; border:1px solid rgba(6,182,212,0.25); transition:background 0.15s;" onmouseover="this.style.background='rgba(6,182,212,0.12)'" onmouseout="this.style.background='none'" title="Message Artist">Message Artist</span>
                        <span onclick="event.stopPropagation(); window.flyerEditor && window.flyerEditor.open(window.venueId || '${this.venueId}', ${gig.id})" style="font-size:0.7rem; color:#c4b5fd; cursor:pointer; white-space:nowrap; padding:2px 6px; border-radius:4px; transition:background 0.15s;" onmouseover="this.style.background='rgba(139,92,246,0.2)'" onmouseout="this.style.background='none'" title="Create/Edit Flyer">🎨 Flyer</span>
                      </span>
                    </div>
                  `;
                }).join('')}
                ${totalPages > 1 ? `
                  <div style="display: flex; justify-content: flex-end; align-items: center; gap: 4px; margin-top: 6px; padding-top: 4px;">
                    <button onclick="event.stopPropagation(); myArtists.setGigPage(${artistId}, ${page - 1})" ${page <= 1 ? 'disabled' : ''} style="padding: 2px 8px; font-size: 0.7rem; background: ${page <= 1 ? 'transparent' : 'rgba(99,91,255,0.15)'}; border: 1px solid ${page <= 1 ? 'rgba(255,255,255,0.1)' : 'rgba(99,91,255,0.3)'}; border-radius: 4px; color: ${page <= 1 ? 'var(--text-muted)' : '#a78bfa'}; cursor: ${page <= 1 ? 'default' : 'pointer'};">‹</button>
                    <span style="font-size: 0.7rem; color: var(--text-muted); min-width: 60px; text-align: center;">${page} / ${totalPages}</span>
                    <button onclick="event.stopPropagation(); myArtists.setGigPage(${artistId}, ${page + 1})" ${page >= totalPages ? 'disabled' : ''} style="padding: 2px 8px; font-size: 0.7rem; background: ${page >= totalPages ? 'transparent' : 'rgba(99,91,255,0.15)'}; border: 1px solid ${page >= totalPages ? 'rgba(255,255,255,0.1)' : 'rgba(99,91,255,0.3)'}; border-radius: 4px; color: ${page >= totalPages ? 'var(--text-muted)' : '#a78bfa'}; cursor: ${page >= totalPages ? 'default' : 'pointer'};">›</button>
                  </div>
                ` : ''}
              `;
            })() : ''}
          </div>
        ` : ''}
      </div>
    `;
  }

  async saveOverride(preferredId, field, value) {
    try {
      const data = {};
      data[field] = value === '' ? null : parseInt(value, 10);
      await fetch(`/api/preferred-artists/${preferredId}/override`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(data)
      });
      // Update local cache so re-render shows correct values
      const artist = this.artists.find(a => a.preferred_id === preferredId);
      if (artist) {
        if (field === 'pay_dollars_override') artist.pay_dollars_override = data[field];
        else if (field === 'pay_cents_override') artist.pay_cents_override = data[field];
        else if (field === 'frequency_days_override') artist.frequency_days_override = data[field];
      }
    } catch (e) {
      console.error('Failed to save override:', e);
    }
  }

  toggleGigsCollapse(artistId) {
    if (this.collapsedGigs.has(artistId)) {
      this.collapsedGigs.delete(artistId);
    } else {
      this.collapsedGigs.add(artistId);
    }
    this.render();
  }

  setGigPage(artistId, page) {
    this.gigPages[artistId] = page;
    this.render();
  }

  // ── Past Gigs modal ─────────────────────────────────────────────────────
  async openPastGigsModal(artistId, artistName) {
    const PER_PAGE = 10;
    this._pg = { artistId, artistName, gigs: [], sortCol: 'date', sortDir: -1, page: 1 };
    // Build overlay
    let overlay = document.getElementById('pastGigsOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'pastGigsOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;padding:20px;';
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = `
      <div style="background:#1a1f2e;border:1px solid #2a3040;border-radius:14px;width:100%;max-width:820px;max-height:88vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.5);">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:18px 24px;border-bottom:1px solid #2a3040;">
          <div style="font-size:1.05rem;font-weight:700;color:#e2e8f0;">Past Gigs — ${_ma_esc(artistName)}</div>
          <button onclick="document.getElementById('pastGigsOverlay').remove()" style="background:transparent;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;line-height:1;">✕</button>
        </div>
        <div id="pastGigsBody" style="overflow:auto;padding:16px 24px 24px;">
          <div style="text-align:center;color:#94a3b8;padding:30px;font-size:0.85rem;">Loading…</div>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    try {
      const r = await fetch(`/api/venues/${this.venueId}/artists/${artistId}/past-gigs`, { credentials: 'include' });
      const d = r.ok ? await r.json() : { gigs: [] };
      this._pg.gigs = d.gigs || [];
    } catch (e) {
      this._pg.gigs = [];
    }
    this._renderPastGigs();
  }

  _sortPastGigs() {
    const { sortCol, sortDir } = this._pg;
    const val = (g) => {
      if (sortCol === 'date') return (g.date || '') + (g.start_time || '');
      if (sortCol === 'time') return g.start_time || '';
      if (sortCol === 'pay') return Number(g.pay || 0);
      if (sortCol === 'status') return (g.status || '').toLowerCase();
      return '';
    };
    this._pg.gigs.sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va < vb) return -1 * this._pg.sortDir;
      if (va > vb) return 1 * this._pg.sortDir;
      return 0;
    });
  }

  setPastGigsSort(col) {
    if (this._pg.sortCol === col) this._pg.sortDir *= -1;
    else { this._pg.sortCol = col; this._pg.sortDir = (col === 'date' ? -1 : 1); }
    this._pg.page = 1;
    this._renderPastGigs();
  }

  setPastGigsPage(p) {
    const totalPages = Math.max(1, Math.ceil(this._pg.gigs.length / 10));
    this._pg.page = Math.min(Math.max(1, p), totalPages);
    this._renderPastGigs();
  }

  async savePastGigNote(gigId, el) {
    const notes = el.value;
    // reflect in local state
    const g = this._pg.gigs.find(x => x.gig_id === gigId);
    if (g) g.notes = notes;
    try {
      await fetch(`/api/venues/${this.venueId}/gigs/${gigId}/artist-note/${this._pg.artistId}`, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes })
      });
      el.style.borderColor = 'rgba(34,197,94,0.5)';
      setTimeout(() => { el.style.borderColor = 'rgba(255,255,255,0.12)'; }, 1200);
    } catch (e) { console.error('save note failed', e); }
  }

  _renderPastGigs() {
    const body = document.getElementById('pastGigsBody');
    if (!body || !this._pg) return;
    const PER_PAGE = 10;
    const fmtTime = (t) => {
      if (!t) return '';
      const p = String(t).split(':'); let h = parseInt(p[0]); const m = p[1] || '00';
      const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12; return `${h}:${m} ${ap}`;
    };
    const fmtDate = (d) => {
      if (!d) return '';
      const [y, mo, da] = String(d).split('-');
      return new Date(parseInt(y), parseInt(mo) - 1, parseInt(da)).toLocaleDateString();
    };
    const statusPill = (s) => {
      const map = {
        'Booked':   ['#22c55e', 'rgba(34,197,94,0.15)'],
        'Cancelled':['#ef4444', 'rgba(239,68,68,0.15)'],
        'Contract Pending': ['#f59e0b', 'rgba(245,158,11,0.15)'],
      };
      const [c, bg] = map[s] || ['#94a3b8', 'rgba(148,163,184,0.15)'];
      return `<span style="color:${c};background:${bg};padding:2px 8px;border-radius:4px;font-size:0.72rem;font-weight:600;white-space:nowrap;">${_ma_esc(s)}</span>`;
    };

    if (!this._pg.gigs.length) {
      body.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:30px;font-size:0.85rem;">No past gigs with this artist yet.</div>';
      return;
    }

    this._sortPastGigs();
    const total = this._pg.gigs.length;
    const totalPages = Math.ceil(total / PER_PAGE);
    const page = Math.min(this._pg.page, totalPages);
    const start = (page - 1) * PER_PAGE;
    const pageGigs = this._pg.gigs.slice(start, start + PER_PAGE);

    const arrow = (col) => this._pg.sortCol === col ? (this._pg.sortDir === 1 ? ' ▲' : ' ▼') : '';
    const th = (col, label, align) => `<th onclick="myArtists.setPastGigsSort('${col}')" style="text-align:${align||'left'};padding:8px 10px;font-size:0.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.03em;cursor:pointer;user-select:none;white-space:nowrap;border-bottom:1px solid #2a3040;">${label}${arrow(col)}</th>`;

    const aName = _ma_attr(this._pg.artistName);
    const rows = pageGigs.map(g => `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
        <td style="padding:8px 10px;font-size:0.82rem;color:#e2e8f0;white-space:nowrap;vertical-align:top;font-weight:600;">${fmtDate(g.date)}</td>
        <td style="padding:8px 10px;font-size:0.8rem;color:#cbd5e1;white-space:nowrap;vertical-align:top;">${fmtTime(g.start_time)} – ${fmtTime(g.end_time)}</td>
        <td style="padding:8px 10px;font-size:0.8rem;color:#22c55e;font-weight:600;white-space:nowrap;text-align:right;vertical-align:top;">${g.pay_summary || (window.hasDoorDeal && window.hasDoorDeal(g) && window.formatPaySummary ? window.formatPaySummary(g) : '$'+(Number(g.pay)||0).toFixed(2))}</td>
        <td style="padding:8px 10px;vertical-align:top;">${statusPill(g.status)}</td>
        <td style="padding:8px 10px;vertical-align:top;min-width:160px;">
          <textarea oninput="this.style.height='auto';this.style.height=(this.scrollHeight)+'px';" onblur="myArtists.savePastGigNote(${g.gig_id}, this)" placeholder="Add a note…" style="width:100%;min-height:30px;resize:none;overflow:hidden;background:rgba(21,27,40,0.8);border:1px solid rgba(255,255,255,0.12);border-radius:5px;color:#e2e8f0;font-size:0.78rem;padding:5px 7px;font-family:inherit;line-height:1.4;">${_ma_esc(g.notes || '')}</textarea>
        </td>
        <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">
          <div style="display:flex;flex-direction:column;gap:4px;">
            <span onclick="typeof openMessageModal==='function' && openMessageModal(${parseInt(g.gig_id,10)||0}, '${aName}')" style="font-size:0.7rem;color:#06b6d4;cursor:pointer;padding:3px 8px;border:1px solid rgba(6,182,212,0.3);border-radius:4px;text-align:center;" title="Message Artist">Message</span>
            <span onclick="window.flyerEditor && window.flyerEditor.open(window.venueId || '${this.venueId}', ${parseInt(g.gig_id,10)||0})" style="font-size:0.7rem;color:#c4b5fd;cursor:pointer;padding:3px 8px;border:1px solid rgba(139,92,246,0.3);border-radius:4px;text-align:center;" title="Create/Edit Flyer">🎨 Flyer</span>
          </div>
        </td>
      </tr>`).join('');

    body.innerHTML = `
      <table style="width:100%;border-collapse:collapse;">
        <thead><tr>
          ${th('date', 'Date')}
          ${th('time', 'Time')}
          ${th('pay', 'Pay', 'right')}
          ${th('status', 'Status')}
          <th style="text-align:left;padding:8px 10px;font-size:0.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.03em;border-bottom:1px solid #2a3040;">Notes</th>
          <th style="text-align:left;padding:8px 10px;font-size:0.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.03em;border-bottom:1px solid #2a3040;">Actions</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${totalPages > 1 ? `
        <div style="display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:14px;">
          <button onclick="myArtists.setPastGigsPage(${page - 1})" ${page <= 1 ? 'disabled' : ''} style="padding:4px 12px;font-size:0.78rem;background:${page <= 1 ? 'transparent' : 'rgba(99,91,255,0.15)'};border:1px solid ${page <= 1 ? 'rgba(255,255,255,0.1)' : 'rgba(99,91,255,0.3)'};border-radius:5px;color:${page <= 1 ? '#64748b' : '#a78bfa'};cursor:${page <= 1 ? 'default' : 'pointer'};">‹ Prev</button>
          <span style="font-size:0.78rem;color:#94a3b8;">Page ${page} of ${totalPages} · ${total} gigs</span>
          <button onclick="myArtists.setPastGigsPage(${page + 1})" ${page >= totalPages ? 'disabled' : ''} style="padding:4px 12px;font-size:0.78rem;background:${page >= totalPages ? 'transparent' : 'rgba(99,91,255,0.15)'};border:1px solid ${page >= totalPages ? 'rgba(255,255,255,0.1)' : 'rgba(99,91,255,0.3)'};border-radius:5px;color:${page >= totalPages ? '#64748b' : '#a78bfa'};cursor:${page >= totalPages ? 'default' : 'pointer'};">Next ›</button>
        </div>` : `<div style="margin-top:12px;font-size:0.75rem;color:#64748b;text-align:right;">${total} gig${total !== 1 ? 's' : ''}</div>`}
    `;
    // Auto-size existing textareas to fit content
    body.querySelectorAll('textarea').forEach(t => { t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; });
  }

  showGigDetails(gigId) {
    // Find gig in existing data
    let gig = null;
    let artistName = null;
    const searchId = parseInt(gigId, 10);
    
    for (const artist of this.artists) {
      if (artist.gigs) {
        gig = artist.gigs.find(g => parseInt(g.id, 10) === searchId);
        if (gig) {
          // Add artist info to gig
          artistName = artist.artist_name;
          gig.artist_id = artist.artist_id;
          gig.artist_name = artistName;
          break;
        }
      }
    }
    
    if (!gig) {
      console.warn('Gig not found:', gigId);
      return;
    }
    
    // Use the existing openGigModal from venue.create-gigs.js
    if (window.openGigModal) {
      window.openGigModal(gig);
    } else {
      console.error('openGigModal not available');
    }
  }
}

// Global instance
let myArtists;

// Initialize on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const venueId = params.get('venue_id');
    if (venueId && document.getElementById('myArtists')) {
      myArtists = new MyArtists(venueId);
      window.myArtists = myArtists; // v93: Expose on window for other scripts
    }
  });
} else {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get('venue_id');
  if (venueId && document.getElementById('myArtists')) {
    myArtists = new MyArtists(venueId);
    window.myArtists = myArtists; // v93: Expose on window for other scripts
  }
}
