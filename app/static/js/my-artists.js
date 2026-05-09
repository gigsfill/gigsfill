// v73: My Artists Component for Venue Create Gigs Page

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
      // v73: Include 'revoked' in denied count
      else if (artist.preferred_status === 'denied' || artist.preferred_status === 'revoked') stats.denied++;
      else if (artist.preferred_status === 'banned') stats.banned++;
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
      // v73: Include 'revoked' in denied filter
      if (this.activeFilters.has('denied') && (artist.preferred_status === 'denied' || artist.preferred_status === 'revoked')) return true;
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
  
  // v73: Revoke preferred status with confirmation
  async revokePreferred(artistId, preferredId, artistName) {
    // Show confirmation modal
    const modal = document.createElement('div');
    modal.id = 'revokeModal';
    modal.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;';
    
    modal.innerHTML = `
      <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid rgba(239, 68, 68, 0.5); border-radius: 12px; padding: 2rem; max-width: 500px; box-shadow: 0 8px 32px rgba(0,0,0,0.5);">
        <h2 style="color: #ffffff; margin-bottom: 1rem; font-size: 1.5rem;">Revoke Preferred Status</h2>
        <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem;">
          <p style="color: #ef4444; margin: 0; font-weight: 500;">Are you sure you want to revoke the preferred status for ${artistName}?</p>
        </div>
        <p style="color: #a1a1aa; margin-bottom: 1.5rem; font-size: 0.95rem;">
          This artist will no longer be able to book future gigs at your venue. Existing booked gigs will remain unchanged.
        </p>
        <div style="display: flex; gap: 12px; justify-content: flex-end;">
          <button id="cancelRevoke" class="btn ghost" style="padding: 8px 16px;">Cancel</button>
          <button id="confirmRevoke" class="btn" style="padding: 8px 16px; background: #ef4444; border: 1px solid #ef4444;">Confirm Revoke Preferred Status</button>
        </div>
      </div>
    `;
    
    document.body.appendChild(modal);
    
    // Handle cancel
    document.getElementById('cancelRevoke').onclick = () => {
      document.body.removeChild(modal);
    };
    
    // Handle confirm
    document.getElementById('confirmRevoke').onclick = async () => {
      try {
        const response = await fetch(`/api/preferred-artists/${preferredId}/revoke`, {
          method: 'PUT',
          credentials: 'include'
        });
        
        if (response.ok) {
          document.body.removeChild(modal);
          await this.loadArtists();
          this.render();
          
          // v73: Reload Activity Center on venue page
          if (window.activityCenterVenue) {
            await window.activityCenterVenue.loadNotifications();
          }
        } else {
          alert('Failed to revoke preferred status');
        }
      } catch (error) {
        console.error('Error revoking preferred status:', error);
        alert('Failed to revoke preferred status');
      }
    };
    
    // Close on background click
    modal.onclick = (e) => {
      if (e.target === modal) {
        document.body.removeChild(modal);
      }
    };
  }

  async banArtist(artistId, artistName) {
    const modal = document.createElement('div');
    modal.id = 'banArtistModal';
    modal.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;';
    modal.innerHTML = `
      <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid rgba(239, 68, 68, 0.5); border-radius: 12px; padding: 2rem; max-width: 500px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.5);">
        <h2 style="color: #ffffff; margin-bottom: 1rem; font-size: 1.5rem;">🚫 Ban Artist</h2>
        <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem;">
          <p style="color: #ef4444; margin: 0; font-weight: 500;">Ban <strong>${artistName}</strong> from your venue?</p>
        </div>
        <p style="color: #a1a1aa; margin-bottom: 1rem; font-size: 0.95rem;">
          They will be permanently blocked from booking any gig at your venue — even during blast windows. This cannot be undone without manually removing the ban.
        </p>
        <div style="margin-bottom: 1.5rem;">
          <label style="font-size: 0.85rem; color: #a1a1aa; display: block; margin-bottom: 6px;">Reason <span style="color: #6b7280;">(optional)</span></label>
          <input id="banReasonInput" type="text" placeholder="e.g. No-show, misconduct..."
            style="width: 100%; padding: 8px 12px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; color: #ffffff; font-size: 0.9rem; box-sizing: border-box; outline: none;">
        </div>
        <div style="display: flex; gap: 12px; justify-content: flex-end;">
          <button id="cancelBan" class="btn ghost" style="padding: 8px 16px;">Cancel</button>
          <button id="confirmBan" class="btn" style="padding: 8px 16px; background: #ef4444; border: 1px solid #ef4444;">Confirm Ban</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    document.getElementById('cancelBan').onclick = () => modal.remove();
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.getElementById('confirmBan').onclick = async () => {
      const reason = document.getElementById('banReasonInput').value.trim();
      try {
        const r = await fetch(`/api/venues/${this.venueId}/ban-artist/${artistId}`, {
          method: 'POST', credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({reason})
        });
        if (r.ok) {
          modal.remove();
          await this.loadArtists();
          this.render();
          if (typeof window.refreshSearchArtistsBanned === 'function') window.refreshSearchArtistsBanned();
        } else { alert('Failed to ban artist'); }
      } catch(e) { alert('Error banning artist'); }
    };
  }

  async unbanArtist(artistId, artistName) {
    const modal = document.createElement('div');
    modal.id = 'unbanArtistModal';
    modal.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;';
    modal.innerHTML = `
      <div style="background: linear-gradient(135deg, #1a1f2e 0%, #0f1419 100%); border: 2px solid rgba(239, 68, 68, 0.5); border-radius: 12px; padding: 2rem; max-width: 500px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.5);">
        <h2 style="color: #ffffff; margin-bottom: 1rem; font-size: 1.5rem;">Remove Ban</h2>
        <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem;">
          <p style="color: #ef4444; margin: 0; font-weight: 500;">Remove ban for <strong>${artistName}</strong>?</p>
        </div>
        <p style="color: #a1a1aa; margin-bottom: 1.5rem; font-size: 0.95rem;">
          They will be able to request preferred artist status again and may appear in future blast emails.
        </p>
        <div style="display: flex; gap: 12px; justify-content: flex-end;">
          <button id="cancelUnban" class="btn ghost" style="padding: 8px 16px;">Cancel</button>
          <button id="confirmUnban" class="btn" style="padding: 8px 16px; background: #22c55e; border: 1px solid #22c55e;">Remove Ban</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    document.getElementById('cancelUnban').onclick = () => modal.remove();
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.getElementById('confirmUnban').onclick = async () => {
      try {
        const r = await fetch(`/api/venues/${this.venueId}/ban-artist/${artistId}`, {
          method: 'DELETE', credentials: 'include'
        });
        if (r.ok) {
          modal.remove();
          await this.loadArtists();
          this.render();
          if (typeof window.refreshSearchArtistsBanned === 'function') window.refreshSearchArtistsBanned();
        } else { alert('Failed to remove ban'); }
      } catch(e) { alert('Error removing ban'); }
    };
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
        
        <div style="display: flex; gap: 8px;">
          <div class="stat-bubble" onclick="myArtists.toggleFilter('preferred')" style="background: ${isActive('preferred') ? 'rgba(34, 197, 94, 0.3)' : 'rgba(34, 197, 94, 0.1)'}; border: 2px solid ${isActive('preferred') ? '#22c55e' : 'rgba(34, 197, 94, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('preferred') ? '0 0 12px rgba(34, 197, 94, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #22c55e;">${stats.preferred}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred</span>
          </div>
          
          <div class="stat-bubble" onclick="myArtists.toggleFilter('denied')" style="background: ${isActive('denied') ? 'rgba(239, 68, 68, 0.3)' : 'rgba(239, 68, 68, 0.1)'}; border: 2px solid ${isActive('denied') ? '#ef4444' : 'rgba(239, 68, 68, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('denied') ? '0 0 12px rgba(239, 68, 68, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #ef4444;">${stats.denied}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Denied</span>
          </div>

          ${stats.banned > 0 ? `<div class="stat-bubble" onclick="myArtists.toggleFilter('banned')" style="background: ${isActive('banned') ? 'rgba(127,29,29,0.4)' : 'rgba(127,29,29,0.15)'}; border: 2px solid ${isActive('banned') ? '#ef4444' : 'rgba(239,68,68,0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('banned') ? '0 0 12px rgba(239,68,68,0.4)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #fca5a5;">${stats.banned}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">🚫 Banned</span>
          </div>` : ''}

          <div class="stat-bubble" onclick="myArtists.toggleFilter('pastGigs')" style="background: ${isActive('pastGigs') ? 'rgba(59, 130, 246, 0.3)' : 'rgba(59, 130, 246, 0.1)'}; border: 2px solid ${isActive('pastGigs') ? '#3b82f6' : 'rgba(59, 130, 246, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('pastGigs') ? '0 0 12px rgba(59, 130, 246, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #3b82f6;">${stats.pastGigs || 0}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Past Gigs</span>
          </div>
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
    }
    
    // Pay and frequency: use override if set, otherwise venue default
    const payDollars = (artist.pay_dollars_override != null) ? artist.pay_dollars_override : (artist.venue_default_pay_dollars || 0);
    const payCents = String((artist.pay_cents_override != null) ? artist.pay_cents_override : (artist.venue_default_pay_cents || 0)).padStart(2, '0');
    const freqDays = (artist.frequency_days_override != null) ? artist.frequency_days_override : (artist.venue_default_freq_days || 0);
    
    return `
      <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 8px;">
        <div style="display: grid; grid-template-columns: minmax(160px, 1.5fr) auto auto; align-items: center; gap: 10px;">
          <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
            <a href="/app/artist-profile.html?artist_id=${artistId}" target="_blank" onclick="event.stopPropagation()" style="font-weight: 600; font-size: 0.9rem; color: #7c6bff; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${artistName}</a>
            <span style="font-size: 0.75rem; color: var(--text-muted); white-space: nowrap;">${artist.artist_city || ''}, ${artist.artist_state || ''}</span>
          </div>
          <div style="display: flex; align-items: center; gap: 12px; background: rgba(99,91,255,0.08); border: 1px solid rgba(99,91,255,0.2); border-radius: 6px; padding: 5px 12px; white-space: nowrap;">
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
          </div>
          <div style="display: flex; align-items: center; gap: 8px; justify-content: flex-end;">
            ${status === 'pending' ? `
              <button onclick="event.stopPropagation(); myArtists.approveArtist(${artist.preferred_id})" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #22c55e; border: 1px solid #22c55e; line-height: 1.4; font-weight: 500;" title="Approve as Preferred Artist — they can book your gigs directly">Approve</button>
              <button onclick="event.stopPropagation(); myArtists.denyArtist(${artist.preferred_id})" class="btn ghost" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500;" title="Deny this preferred status request">Deny</button>
              <button onclick="event.stopPropagation(); myArtists.banArtist(${artistId}, '${artistName}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; line-height: 1.4; font-weight: 500;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
            ` : (status === 'denied' || status === 'revoked') ? `
              <button onclick="event.stopPropagation(); openReviewModal({ artistId: ${artistId}, artistName: '${artistName.replace(/'/g,'')}' })" class="btn ghost _rateArtistBtn" data-artist-id="${artistId}" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500; color:#f59e0b; border-color:rgba(245,158,11,0.3);" title="Leave a review for this artist">Rate Artist</button>
              <button onclick="event.stopPropagation(); myArtists.approveArtist(${artist.preferred_id})" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #22c55e; border: 1px solid #22c55e; line-height: 1.4; font-weight: 500;" title="Approve as Preferred Artist — they can book your gigs directly">Approve</button>
              <button onclick="event.stopPropagation(); myArtists.banArtist(${artistId}, '${artistName}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; line-height: 1.4; font-weight: 500;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
            ` : status === 'approved' ? `
              <button onclick="event.stopPropagation(); openReviewModal({ artistId: ${artistId}, artistName: '${artistName.replace(/'/g,'')}' })" class="btn ghost _rateArtistBtn" data-artist-id="${artistId}" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500; color:#f59e0b; border-color:rgba(245,158,11,0.3);" title="Leave a review for this artist">Rate Artist</button>
              <button onclick="event.stopPropagation(); myArtists.revokePreferred(${artistId}, ${artist.preferred_id}, '${artistName}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #ef4444; border: 1px solid #ef4444; color: white; line-height: 1.4; font-weight: 500;" title="Revoke preferred status — artist can no longer book gigs normally">Revoke</button>
              <button onclick="event.stopPropagation(); myArtists.banArtist(${artistId}, '${artistName}')" class="btn" style="padding: 3px 10px; font-size: 0.7rem; background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; line-height: 1.4; font-weight: 500;" title="Ban artist from ever booking a gig at this venue">🚫 Ban</button>
            ` : status === 'banned' ? `
              <span style="font-size:0.7rem;color:#ef4444;font-weight:600;padding:3px 8px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:4px;" title="This artist is permanently banned from your venue">🚫 Banned</span>
              <button onclick="event.stopPropagation(); myArtists.unbanArtist(${artistId}, '${artistName}')" class="btn ghost" style="padding: 3px 10px; font-size: 0.7rem; line-height: 1.4; font-weight: 500;" title="Remove ban — artist can request preferred status again">Remove Ban</button>
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
          <div style="margin-top: 8px; margin-left: 20px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.08);">
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
                        <span style="color:#f87171; font-weight:600; white-space:nowrap;">${({'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠'}[gig.artist_type] || '🎵')} Booked • $${(gig.effective_pay != null ? gig.effective_pay : gig.pay) || 'N/A'}</span>
                      </div>
                      <span style="display:flex;gap:4px;flex-shrink:0;align-items:center;">
                        <span onclick="event.stopPropagation(); typeof openMessageModal === 'function' && openMessageModal(${gig.id}, '${artist.artist_name ? artist.artist_name.replace(/'/g,'') : 'Artist'}')" style="font-size:0.7rem; color:#06b6d4; cursor:pointer; white-space:nowrap; padding:2px 7px; border-radius:4px; border:1px solid rgba(6,182,212,0.25); transition:background 0.15s;" onmouseover="this.style.background='rgba(6,182,212,0.12)'" onmouseout="this.style.background='none'" title="Message Artist">Message Artist</span>
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
