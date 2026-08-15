// v015 FIX - CLEAR YOUR BROWSER CACHE!
// v73: My Venues Redesign - Preferred & Denied Only

// Audit fix (May 2026 part 7): HTML/attr escape helpers. Same pattern as
// my-artists.js — venue names and locations were interpolated raw into
// innerHTML and inline onclick string args, which is an XSS sink.
function _mv_esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function _mv_attr(s) {
  return String(s == null ? '' : s)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, '\\&#39;')
    .replace(/"/g, '\\&quot;')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

class MyVenuesRedesign {
  constructor() {
    this.venues = [];
    this.activeFilters = new Set(['preferred']);
    this.expandedVenues = new Set();
    this.showPastGigs = false;
    this.collapsedGigs = new Set();
    this.gigPages = {};
    this.GIGS_PER_PAGE = 10;
    // Don't auto-init in constructor — caller will call loadVenues()+render()
  }

  async init() {
    if (window._artistAccessDenied) return;
    await this.loadVenues();
    this.render();
  }


  async loadVenues() {
    try {
      const params = new URLSearchParams(window.location.search);
      const artistId = params.get('artist_id');
      
      // Load venues with gigs
      const venuesResponse = await fetch(`/api/artists/${artistId}/venues`, {
        credentials: 'include'
      });
      
      if (venuesResponse.ok) {
        this.venues = await venuesResponse.json();

        // Clear gigs_loaded flag so data is fresh after cancel/reload
        this.venues.forEach(v => { v.gigs_loaded = false; });
        
        // Auto-expand venues with gigs to load their gig data
        const expandPromises = [];
        this.venues.forEach(v => {
          const gigsCount = v.gigs_count || 0;
          if (gigsCount > 0) {
            this.expandedVenues.add(v.venue_id || v.id);
            expandPromises.push(this.expandVenue(v.venue_id || v.id));
          }
        });
        await Promise.all(expandPromises);
      }
    } catch (error) {
      console.error("❌ Error loading venues:", error);
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

    this.venues.forEach(v => {
      // Count future gigs
      const gigsCount = v.gigs_count || (v.gigs ? v.gigs.length : 0);
      if (gigsCount > 0) {
        stats.gigsBooked += gigsCount;
      }
      
      // Count past gigs
      if (v.gigs) {
        v.gigs.forEach(gig => {
          const [year, month, day] = gig.date.split('-');
          const gigDate = new Date(parseInt(year), parseInt(month) - 1, parseInt(day));
          gigDate.setHours(0, 0, 0, 0);
          if (gigDate < today) {
            stats.pastGigs++;
          }
        });
      }
      
      const status = v.preferred_status || v.status;
      if (['approved', 'preferred', 'active'].includes(status)) {
        stats.preferred++;
      }
      
      if (status === 'pending') stats.pending++;
      if (status === 'denied') stats.denied++;
      // Part 10j: 'revoked' is its own bubble — only count those WITH gigs
      // (a venue we're no longer preferred at but have history with).
      if (status === 'revoked' && ((v.gigs_count || (v.gigs ? v.gigs.length : 0)) > 0)) stats.revoked++;
      // Part 10k: 'normal' = gigs at venue but no preferred relationship → Non-Preferred.
      if (status === 'normal' && ((v.gigs_count || (v.gigs ? v.gigs.length : 0)) > 0)) stats.nonPreferred++;
      if (status === 'banned') stats.banned++;
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
    
    this.expandedVenues.clear();
  
    // Auto-expand venues with gigs based on filter
    const expandPromises = [];
    this.venues.forEach(v => {
      const venueId = v.venue_id || v.id;
      const gigsCount = v.gigs_count || 0;
  
      const status = v.preferred_status || v.status;
  
      if (filter === 'preferred' && ['approved', 'preferred', 'active'].includes(status)) {
        if (gigsCount > 0) {
          this.expandedVenues.add(venueId);
          expandPromises.push(this.expandVenue(venueId));
        }
      } else if (filter === 'denied' && (status === 'denied' || status === 'revoked')) {
        // No auto-expand for denied
      } else if (filter === 'pastGigs') {
        // Show past gigs view
        this.showPastGigs = true;
        
        // Expand all venues with gigs
        if (gigsCount > 0) {
          this.expandedVenues.add(venueId);
          expandPromises.push(this.expandVenue(venueId));
        }
      }
    });
  
    await Promise.all(expandPromises);
    this.render();
  }
  

  async toggleVenue(venueId) {
    if (this.expandedVenues.has(venueId)) {
      this.expandedVenues.delete(venueId);
      this.render();
    } else {
      await this.expandVenue(venueId);
      this.render();
    }
  }

  async expandVenue(venueId) {
    // v73: Fetch gigs if not already loaded
    const venue = this.venues.find(v => (v.venue_id || v.id) === venueId);
    if (!venue) return;
    
    if (!venue.gigs_loaded) {
      try {
        const params = new URLSearchParams(window.location.search);
        const artistId = params.get('artist_id');
        
        const response = await fetch(`/api/artists/${artistId}/venues/${venueId}/gigs`, {
          credentials: 'include'
        });
        
        if (response.ok) {
          venue.gigs = await response.json();
          venue.gigs_loaded = true;
        }
      } catch (error) {
        console.error('Error loading gigs:', error);
        venue.gigs = [];
      }
    }
    
    this.expandedVenues.add(venueId);
  }

  filterVenues() {
    if (this.activeFilters.size === 0) return this.sortVenues(this.venues);

    const filtered = this.venues.filter(v => {
      const status = v.preferred_status || v.status;
      const gigsCount = v.gigs_count || (v.gigs ? v.gigs.length : 0);
    
      if (this.activeFilters.has('gigs') && gigsCount > 0) return true;
    
      // Always show venues with active waitlist entries
      if (v.waitlist_gig_id) return true;

      if (
        this.activeFilters.has('preferred') &&
        ['approved', 'preferred', 'active'].includes(status)
      ) return true;
    
      if (this.activeFilters.has('pending') && status === 'pending') return true;

      if (this.activeFilters.has('denied') && status === 'denied') return true;

      // Part 10j: revoked is its own filter; only show revoked venues with gigs.
      if (this.activeFilters.has('revoked') && status === 'revoked' && gigsCount > 0) return true;

      // Part 10k: non-preferred (played without preferred status) + banned.
      if (this.activeFilters.has('nonPreferred') && status === 'normal' && gigsCount > 0) return true;
      if (this.activeFilters.has('banned') && status === 'banned') return true;

      // Show all venues with gigs for pastGigs filter
      if (this.activeFilters.has('pastGigs') && gigsCount > 0) return true;

      return false;
    });
    
    return this.sortVenues(filtered);
  }

  sortVenues(venues) {
    // v73: Sort by:
    // 1. Venues with gigs (by closest gig date)
    // 2. Preferred venues (alphabetical)
    // 3. Pending venues (alphabetical)
    // 4. Denied venues (alphabetical)
    
    return venues.sort((a, b) => {
      const aStatus = a.preferred_status || a.status;
      const bStatus = b.preferred_status || b.status;
      const aGigsCount = a.gigs_count || 0;
      const bGigsCount = b.gigs_count || 0;
      const aName = (a.venue_name || a.name || '').toLowerCase();
      const bName = (b.venue_name || b.name || '').toLowerCase();
      
      // Both have gigs - sort by closest gig date
      if (aGigsCount > 0 && bGigsCount > 0) {
        if (a.next_gig_date && b.next_gig_date) {
          return new Date(a.next_gig_date) - new Date(b.next_gig_date);
        }
        return 0;
      }
      
      // Only A has gigs
      if (aGigsCount > 0) return -1;
      // Only B has gigs
      if (bGigsCount > 0) return 1;
      
      // No gigs - sort by status then alphabetically
      const statusOrder = { approved: 1, pending: 2, denied: 3, normal: 4 };
      const aOrder = statusOrder[aStatus] || 5;
      const bOrder = statusOrder[bStatus] || 5;
      
      if (aOrder !== bOrder) return aOrder - bOrder;
      
      // Same status - alphabetical
      return aName.localeCompare(bName);
    });
  }

  async togglePastGigs() {
    await this.toggleFilter('pastGigs');
  }

  render() {
    const container = document.getElementById('myVenuesRedesigned');
    if (!container) return;

    const stats = this.calculateStats();
    const filteredVenues = this.filterVenues();

    const isActive = (filter) => this.activeFilters.has(filter);

    container.innerHTML = `
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 0.75rem; flex-wrap: wrap;">
        <h2 style="margin: 0; font-size: 1rem; white-space: nowrap;">My Venues</h2>
        
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
          <div class="stat-bubble" onclick="myVenuesRedesign.toggleFilter('preferred')" style="background: ${isActive('preferred') ? 'rgba(34, 197, 94, 0.3)' : 'rgba(34, 197, 94, 0.1)'}; border: 2px solid ${isActive('preferred') ? '#22c55e' : 'rgba(34, 197, 94, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('preferred') ? '0 0 12px rgba(34, 197, 94, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #22c55e;">${stats.preferred}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred Venues</span>
          </div>

          ${stats.revoked > 0 ? `<div class="stat-bubble" onclick="myVenuesRedesign.toggleFilter('revoked')" style="background: ${isActive('revoked') ? 'rgba(245, 158, 11, 0.3)' : 'rgba(245, 158, 11, 0.1)'}; border: 2px solid ${isActive('revoked') ? '#f59e0b' : 'rgba(245, 158, 11, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('revoked') ? '0 0 12px rgba(245, 158, 11, 0.5)' : 'none'};" title="Venues where your preferred status was revoked but you have past gigs">
            <span style="font-size: 0.9rem; font-weight: 600; color: #f59e0b;">${stats.revoked}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred Status Revoked</span>
          </div>` : ''}

          <div class="stat-bubble" onclick="myVenuesRedesign.toggleFilter('denied')" style="background: ${isActive('denied') ? 'rgba(239, 68, 68, 0.3)' : 'rgba(239, 68, 68, 0.1)'}; border: 2px solid ${isActive('denied') ? '#ef4444' : 'rgba(239, 68, 68, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('denied') ? '0 0 12px rgba(239, 68, 68, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #ef4444;">${stats.denied}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred Status Denied</span>
          </div>

          ${stats.nonPreferred > 0 ? `<div class="stat-bubble" onclick="myVenuesRedesign.toggleFilter('nonPreferred')" style="background: ${isActive('nonPreferred') ? 'rgba(56, 189, 248, 0.3)' : 'rgba(56, 189, 248, 0.1)'}; border: 2px solid ${isActive('nonPreferred') ? '#38bdf8' : 'rgba(56, 189, 248, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('nonPreferred') ? '0 0 12px rgba(56, 189, 248, 0.5)' : 'none'};" title="Venues where you played a gig without being a preferred artist">
            <span style="font-size: 0.9rem; font-weight: 600; color: #38bdf8;">${stats.nonPreferred}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Non-Preferred Venues</span>
          </div>` : ''}

          ${stats.banned > 0 ? `<div class="stat-bubble" onclick="myVenuesRedesign.toggleFilter('banned')" style="background: ${isActive('banned') ? 'rgba(127,29,29,0.4)' : 'rgba(127,29,29,0.15)'}; border: 2px solid ${isActive('banned') ? '#ef4444' : 'rgba(239,68,68,0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('banned') ? '0 0 12px rgba(239,68,68,0.4)' : 'none'};" title="Venues that have banned you from booking">
            <span style="font-size: 0.9rem; font-weight: 600; color: #fca5a5;">${stats.banned}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">🚫 Banned Venues</span>
          </div>` : ''}
        </div>
      </div>

      <div style="display: flex; flex-direction: column; gap: 6px;">
        ${filteredVenues.length === 0
          ? '<p style="text-align: left; color: var(--text-muted); padding: 1rem;">No venues match your filters</p>'
          : filteredVenues.map(v => this.renderVenue(v)).join('')}
      </div>

    `;
  }

  renderVenue(venue) {
    const venueId = venue.venue_id || venue.id;
    const venueName = venue.venue_name || venue.name;
    const status = venue.preferred_status || venue.status;
    const isExpanded = this.expandedVenues.has(venueId);
    
    // Filter gigs based on showPastGigs
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    
    let filteredGigs = [];
    if (venue.gigs) {
      filteredGigs = venue.gigs.filter(gig => {
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
    
    // If in pastGigs mode and no past gigs, don't render this venue
    if (this.showPastGigs && gigsCount === 0) {
      return '';
    }
    
    // Status badge styling
    let statusBadge = '';
    if (status === 'approved') {
      statusBadge = '<span style="background: rgba(34, 197, 94, 0.2); border: 1px solid rgba(34, 197, 94, 0.5); color: #22c55e; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Preferred</span>';
    } else if (status === 'pending') {
      statusBadge = '<span style="background: rgba(249, 115, 22, 0.2); border: 1px solid rgba(249, 115, 22, 0.5); color: #f97316; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Pending</span>';
    } else if (status === 'denied') {
      statusBadge = '<span style="background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.5); color: #ef4444; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Denied</span>';
    } else if (status === 'revoked') {
      statusBadge = '<span style="background: rgba(245, 158, 11, 0.2); border: 1px solid rgba(245, 158, 11, 0.5); color: #f59e0b; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Preferred Status Revoked</span>';
    } else if (status === 'banned') {
      statusBadge = '<span style="background: rgba(127,29,29,0.3); border: 1px solid rgba(239,68,68,0.5); color: #fca5a5; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">🚫 Banned</span>';
    } else if (status === 'normal') {
      statusBadge = '<span style="background: rgba(56, 189, 248, 0.2); border: 1px solid rgba(56, 189, 248, 0.5); color: #38bdf8; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">Non-Preferred</span>';
    }
    
    // Waitlist badge (shown instead of or in addition to status badge)
    if (venue.waitlist_gig_id) {
      const pos = venue.waitlist_position || '?';
      const total = venue.waitlist_total || '?';
      statusBadge = `<span style="background: rgba(139,92,246,0.2); border: 1px solid rgba(139,92,246,0.5); color: #a78bfa; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">⏳ Waitlisted (${pos} of ${total})</span>`;
    }

    // 2026-08-06: only surface an override chip when the venue has
    // ACTUALLY set one. Zero counts as "not set" — a saved $0.00 pay
    // override is functionally identical to no override at all (the
    // artist can't be booked for $0), and a saved 0-day frequency is
    // meaningless. Treating them as "no override" matches the venue
    // user's mental model on the My Artists tab where the empty state
    // is 0/0/0. Gated on approved-preferred status — the columns
    // don't apply otherwise.
    const payOverrideD = venue.pay_dollars_override || 0;
    const payOverrideC = venue.pay_cents_override   || 0;
    const freqOverrideD = venue.frequency_days_override || 0;
    const hasPayOverride  = (status === 'approved') && (payOverrideD > 0 || payOverrideC > 0);
    const hasFreqOverride = (status === 'approved') && (freqOverrideD > 0);
    const payOverrideCStr = String(payOverrideC).padStart(2, '0');

    // Audit fix (May 2026 part 7): escape every user-controlled field.
    const _vid_safe = parseInt(venueId, 10) || 0;
    const _vname_h = _mv_esc(venueName || 'Unknown Venue');
    const _vcity_h = _mv_esc(venue.city || '');
    const _vstate_h = _mv_esc(venue.state || '');
    return `
      <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 6px 8px;">
        <div style="display: grid; grid-template-columns: minmax(100px, 1fr) auto auto; align-items: center; gap: 10px;">
          <div style="display: flex; flex-direction: column; gap: 2px; min-width: 0;">
            <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
              <a href="/app/venue-profile.html?venue_id=${_vid_safe}" target="_blank" style="font-weight: 600; font-size: 0.9rem; color: #7c6bff; text-decoration: none; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${_vname_h}</a>
              <span style="font-size: 0.75rem; color: var(--text-muted); white-space: nowrap;">${_vcity_h}, ${_vstate_h}</span>
              ${hasGigs ? `<span style="font-size: 0.75rem; color: var(--text-muted); white-space: nowrap;">${gigsCount} gig${gigsCount !== 1 ? 's' : ''}</span>` : ''}
              ${venue.avg_rating ? `<span title="${venue.avg_rating}/5 from ${venue.review_count} review${venue.review_count !== 1 ? 's' : ''}" style="font-size:0.75rem; color:#f59e0b; white-space:nowrap; cursor:default;">★ ${parseFloat(venue.avg_rating).toFixed(1)}<span style="color:var(--text-muted); margin-left:2px;">(${venue.review_count})</span></span>` : ''}
            </div>
            <span onclick="myVenuesRedesign.openPastGigsModal(${_vid_safe}, '${_mv_attr(venueName || 'Venue')}')" style="font-size: 0.68rem; color: #3b82f6; white-space: nowrap; cursor: pointer; width: fit-content;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'" title="View your past gigs at this venue">📅 Past Gigs ›</span>
          </div>
          ${(hasPayOverride || hasFreqOverride) ? `<div onclick="event.stopPropagation()" style="display: flex; align-items: center; gap: 12px; background: rgba(99,91,255,0.08); border: 1px solid rgba(99,91,255,0.2); border-radius: 6px; padding: 5px 12px; white-space: nowrap;">
            ${hasPayOverride ? `<div title="Pay Override — a custom pay rate this venue set specifically for you. Replaces the venue's default pay for gigs you book here." style="display: flex; align-items: center; gap: 4px; cursor: help;">
              <span style="font-size: 0.75rem; color: var(--text-muted);">Pay:</span>
              <span style="font-size: 0.8rem; color: #e2e8f0; font-weight: 500;">$${payOverrideD}.${payOverrideCStr}</span>
            </div>` : ''}
            ${(hasPayOverride && hasFreqOverride) ? `<div style="width: 1px; height: 16px; background: rgba(99,91,255,0.25);"></div>` : ''}
            ${hasFreqOverride ? `<div title="Frequency Override — how often this venue lets you book. Replaces the venue's default frequency limit (e.g. '1 per 28 days' means you can book once every 28 days at this venue)." style="display: flex; align-items: center; gap: 4px; cursor: help;">
              <span style="font-size: 0.75rem; color: var(--text-muted);">Frequency:</span>
              <span style="font-size: 0.8rem; color: #e2e8f0; font-weight: 500;">1 per ${freqOverrideD} days</span>
            </div>` : ''}
          </div>` : '<div></div>'}
          <div onclick="event.stopPropagation()" style="display: flex; align-items: center; gap: 8px; justify-content: flex-end;">
            ${_buildRateVenueBtn(venue, venueId, venueName)}
            ${statusBadge}
          </div>
        </div>
        
        ${venue.waitlist_gig_id ? (() => {
          const params = new URLSearchParams(window.location.search);
          const artistId = params.get('artist_id');
          const [wy, wm, wd] = (venue.waitlist_gig_date || '').split('-');
          const wDateStr = wy ? new Date(parseInt(wy), parseInt(wm)-1, parseInt(wd)).toLocaleDateString() : '';
          const wStart = venue.waitlist_gig_start ? (typeof formatTime12Hour === 'function' ? formatTime12Hour(venue.waitlist_gig_start) : venue.waitlist_gig_start) : '';
          const wEnd = venue.waitlist_gig_end ? (typeof formatTime12Hour === 'function' ? formatTime12Hour(venue.waitlist_gig_end) : venue.waitlist_gig_end) : '';
          const wTimeStr = wStart ? (wEnd ? `${wStart} – ${wEnd}` : wStart) : '';
          return `
            <div style="margin-top:6px; margin-left:20px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.08);">
              <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding:5px 8px; background:rgba(139,92,246,0.08); border:1px solid rgba(139,92,246,0.2); border-radius:5px;">
                <div style="font-size:0.82rem; color:#e2e8f0; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                  ${wDateStr ? `<strong style="white-space:nowrap;">${wDateStr}</strong><span style="color:rgba(255,255,255,0.3);">|</span>` : ''}
                  ${wTimeStr ? `<span style="white-space:nowrap; color:#e2e8f0;">${wTimeStr}</span><span style="color:rgba(255,255,255,0.3);">|</span>` : ''}
                  <span style="color:#a78bfa; font-weight:600; white-space:nowrap;">⏳ Waitlisted — Position ${venue.waitlist_position} of ${venue.waitlist_total}</span>
                </div>
                <button onclick="event.stopPropagation(); leaveWaitlist(${venue.waitlist_gig_id}, ${artistId})"
                  style="padding:3px 10px; font-size:0.75rem; background:rgba(239,68,68,0.15); border:1px solid rgba(239,68,68,0.4); color:#f87171; border-radius:4px; cursor:pointer; white-space:nowrap;">
                  Leave Waitlist
                </button>
              </div>
            </div>
          `;
        })() : ''}
        ${hasGigs && filteredGigs.length > 0 ? `
          <div onclick="event.stopPropagation()" style="margin-top: 6px; margin-left: 20px; padding-top: 6px; border-top: 1px solid rgba(255,255,255,0.08);">
            ${(() => {
              const page = this.gigPages[venueId] || 1;
              const totalPages = Math.ceil(filteredGigs.length / this.GIGS_PER_PAGE);
              const start = (page - 1) * this.GIGS_PER_PAGE;
              const pageGigs = filteredGigs.slice(start, start + this.GIGS_PER_PAGE);
              return `
                ${pageGigs.map(gig => {
                  const [year, month, day] = gig.date.split('-');
                  const dateStr = new Date(parseInt(year), parseInt(month) - 1, parseInt(day)).toLocaleDateString();
                  const gigStart = formatTime12Hour(gig.start_time);
                  const gigEnd = formatTime12Hour(gig.end_time);
                  // Door-deal aware. The /artists/:aid/venues/:vid/gigs
                  // endpoint now stamps deal_type / guarantee_cents /
                  // door_pct (and pay_summary) onto each row directly,
                  // so call formatPaySummary on the gig itself —
                  // it picks up door terms when present and falls back
                  // to flat-dollar otherwise. The previous version
                  // looked at gig.slots[] which this endpoint doesn't
                  // populate, so door deals always rendered as the
                  // bare guarantee dollar amount.
                  let payDisp;
                  if (window.formatPaySummary && (gig.pay_summary || window.hasDoorDeal?.(gig))) {
                    payDisp = window.formatPaySummary(gig);
                  } else if (gig.effective_pay != null) {
                    payDisp = '$' + parseFloat(gig.effective_pay).toFixed(2);
                  } else if (gig.pay != null) {
                    payDisp = '$' + parseFloat(gig.pay).toFixed(2);
                  } else {
                    payDisp = null;
                  }
                  const gigIcon = ({'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠', 'Open Mic MC':'🎙️', 'Karaoke MC':'🎶'}[gig.artist_type] || '🎵');
                  return `
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding:5px 8px; background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25); border-radius:5px; margin-bottom:4px;">
                      <div onclick="myVenuesRedesign.showGigDetails(${gig.id})" style="font-size:0.82rem; color:#e2e8f0; display:flex; align-items:center; gap:10px; flex-wrap:wrap; cursor:pointer;" onmouseover="this.parentElement.style.background='rgba(239,68,68,0.14)'" onmouseout="this.parentElement.style.background='rgba(239,68,68,0.08)'">
                        <strong style="white-space:nowrap;">${dateStr}</strong>
                        <span style="color:rgba(255,255,255,0.3);">|</span>
                        <span style="white-space:nowrap;">${gigStart} – ${gigEnd}</span>
                        <span style="color:rgba(255,255,255,0.3);">|</span>
                        <span style="color:#f87171; font-weight:600; white-space:nowrap;">${gigIcon} Booked${payDisp ? ' • ' + payDisp : ''}</span>
                      </div>
                      <span style="display:flex;gap:4px;flex-shrink:0;align-items:center;">
                        <span onclick="event.stopPropagation(); typeof openMessageModal === 'function' && openMessageModal(${parseInt(gig.id,10)||0}, '${_mv_attr(venue.venue_name || 'Venue')}')" style="font-size:0.7rem; color:#06b6d4; cursor:pointer; white-space:nowrap; padding:2px 7px; border-radius:4px; border:1px solid rgba(6,182,212,0.25); transition:background 0.15s;" onmouseover="this.style.background='rgba(6,182,212,0.12)'" onmouseout="this.style.background='none'" title="Message Venue">Message Venue</span>
                        <span class="gig-flyer-btn" data-gig-id="${gig.id}" style="font-size:0.7rem; color:#c4b5fd; cursor:pointer; white-space:nowrap; padding:2px 6px; border-radius:4px; transition:background 0.15s;" onmouseover="this.style.background='rgba(139,92,246,0.2)'" onmouseout="this.style.background='none'" title="View Event Flyer">🎨 Flyer</span>
                      </span>
                    </div>
                  `;
                }).join('')}
                ${totalPages > 1 ? `
                  <div style="display: flex; justify-content: flex-end; align-items: center; gap: 4px; margin-top: 6px; padding-top: 4px;">
                    <button onclick="event.stopPropagation(); myVenuesRedesign.setGigPage(${venueId}, ${page - 1})" ${page <= 1 ? 'disabled' : ''} style="padding: 2px 8px; font-size: 0.7rem; background: ${page <= 1 ? 'transparent' : 'rgba(99,91,255,0.15)'}; border: 1px solid ${page <= 1 ? 'rgba(255,255,255,0.1)' : 'rgba(99,91,255,0.3)'}; border-radius: 4px; color: ${page <= 1 ? 'var(--text-muted)' : '#a78bfa'}; cursor: ${page <= 1 ? 'default' : 'pointer'};">‹</button>
                    <span style="font-size: 0.7rem; color: var(--text-muted); min-width: 60px; text-align: center;">${page} / ${totalPages}</span>
                    <button onclick="event.stopPropagation(); myVenuesRedesign.setGigPage(${venueId}, ${page + 1})" ${page >= totalPages ? 'disabled' : ''} style="padding: 2px 8px; font-size: 0.7rem; background: ${page >= totalPages ? 'transparent' : 'rgba(99,91,255,0.15)'}; border: 1px solid ${page >= totalPages ? 'rgba(255,255,255,0.1)' : 'rgba(99,91,255,0.3)'}; border-radius: 4px; color: ${page >= totalPages ? 'var(--text-muted)' : '#a78bfa'}; cursor: ${page >= totalPages ? 'default' : 'pointer'};">›</button>
                  </div>
                ` : ''}
              `;
            })()}
          </div>
        ` : ''}
      </div>
    `;
  }

  toggleGigsCollapse(venueId) {
    if (this.collapsedGigs.has(venueId)) {
      this.collapsedGigs.delete(venueId);
    } else {
      this.collapsedGigs.add(venueId);
    }
    this.render();
  }

  setGigPage(venueId, page) {
    this.gigPages[venueId] = page;
    this.render();
  }

  // ── Past Gigs modal (artist side) — mirror of venue My Artists modal ─────
  _pgArtistId() {
    return new URLSearchParams(window.location.search).get('artist_id');
  }

  async openPastGigsModal(venueId, venueName) {
    const aid = this._pgArtistId();
    this._pg = { venueId, venueName, artistId: aid, gigs: [], sortCol: 'date', sortDir: -1, page: 1 };
    let overlay = document.getElementById('pastGigsOverlayV');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'pastGigsOverlayV';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;padding:20px;';
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = `
      <div style="background:#1a1f2e;border:1px solid #2a3040;border-radius:14px;width:100%;max-width:820px;max-height:88vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.5);">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:18px 24px;border-bottom:1px solid #2a3040;">
          <div style="font-size:1.05rem;font-weight:700;color:#e2e8f0;">Past Gigs — ${_mv_esc(venueName)}</div>
          <button onclick="document.getElementById('pastGigsOverlayV').remove()" style="background:transparent;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;line-height:1;">✕</button>
        </div>
        <div id="pastGigsBodyV" style="overflow:auto;padding:16px 24px 24px;">
          <div style="text-align:center;color:#94a3b8;padding:30px;font-size:0.85rem;">Loading…</div>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    try {
      const r = await fetch(`/api/artists/${aid}/venues/${venueId}/past-gigs`, { credentials: 'include' });
      const d = r.ok ? await r.json() : { gigs: [] };
      this._pg.gigs = d.gigs || [];
    } catch (e) { this._pg.gigs = []; }
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
    const g = this._pg.gigs.find(x => x.gig_id === gigId);
    if (g) g.notes = notes;
    try {
      await fetch(`/api/artists/${this._pg.artistId}/gigs/${gigId}/venue-note/${this._pg.venueId}`, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes })
      });
      el.style.borderColor = 'rgba(34,197,94,0.5)';
      setTimeout(() => { el.style.borderColor = 'rgba(255,255,255,0.12)'; }, 1200);
    } catch (e) { console.error('save note failed', e); }
  }

  _renderPastGigs() {
    const body = document.getElementById('pastGigsBodyV');
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
      return `<span style="color:${c};background:${bg};padding:2px 8px;border-radius:4px;font-size:0.72rem;font-weight:600;white-space:nowrap;">${_mv_esc(s)}</span>`;
    };

    if (!this._pg.gigs.length) {
      body.innerHTML = '<div style="text-align:center;color:#94a3b8;padding:30px;font-size:0.85rem;">No past gigs at this venue yet.</div>';
      return;
    }

    this._sortPastGigs();
    const total = this._pg.gigs.length;
    const totalPages = Math.ceil(total / PER_PAGE);
    const page = Math.min(this._pg.page, totalPages);
    const start = (page - 1) * PER_PAGE;
    const pageGigs = this._pg.gigs.slice(start, start + PER_PAGE);

    const arrow = (col) => this._pg.sortCol === col ? (this._pg.sortDir === 1 ? ' ▲' : ' ▼') : '';
    const th = (col, label, align) => `<th onclick="myVenuesRedesign.setPastGigsSort('${col}')" style="text-align:${align||'left'};padding:8px 10px;font-size:0.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.03em;cursor:pointer;user-select:none;white-space:nowrap;border-bottom:1px solid #2a3040;">${label}${arrow(col)}</th>`;

    const vName = _mv_attr(this._pg.venueName);
    const rows = pageGigs.map(g => `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
        <td style="padding:8px 10px;font-size:0.82rem;color:#e2e8f0;white-space:nowrap;vertical-align:top;font-weight:600;">${fmtDate(g.date)}</td>
        <td style="padding:8px 10px;font-size:0.8rem;color:#cbd5e1;white-space:nowrap;vertical-align:top;">${fmtTime(g.start_time)} – ${fmtTime(g.end_time)}</td>
        <td style="padding:8px 10px;font-size:0.8rem;color:#22c55e;font-weight:600;white-space:nowrap;text-align:right;vertical-align:top;">${g.pay_summary || (window.hasDoorDeal && window.hasDoorDeal(g) && window.formatPaySummary ? window.formatPaySummary(g) : '$'+(Number(g.pay)||0).toFixed(2))}</td>
        <td style="padding:8px 10px;vertical-align:top;">${statusPill(g.status)}</td>
        <td style="padding:8px 10px;vertical-align:top;min-width:160px;">
          <textarea oninput="this.style.height='auto';this.style.height=(this.scrollHeight)+'px';" onblur="myVenuesRedesign.savePastGigNote(${g.gig_id}, this)" placeholder="Add a note…" style="width:100%;min-height:30px;resize:none;overflow:hidden;background:rgba(21,27,40,0.8);border:1px solid rgba(255,255,255,0.12);border-radius:5px;color:#e2e8f0;font-size:0.78rem;padding:5px 7px;font-family:inherit;line-height:1.4;">${_mv_esc(g.notes || '')}</textarea>
        </td>
        <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">
          <div style="display:flex;flex-direction:column;gap:4px;">
            <span onclick="typeof openMessageModal==='function' && openMessageModal(${parseInt(g.gig_id,10)||0}, '${vName}')" style="font-size:0.7rem;color:#06b6d4;cursor:pointer;padding:3px 8px;border:1px solid rgba(6,182,212,0.3);border-radius:4px;text-align:center;" title="Message Venue">Message</span>
            <span class="gig-flyer-btn" data-gig-id="${parseInt(g.gig_id,10)||0}" style="font-size:0.7rem;color:#c4b5fd;cursor:pointer;padding:3px 8px;border:1px solid rgba(139,92,246,0.3);border-radius:4px;text-align:center;" title="View Event Flyer">🎨 Flyer</span>
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
          <button onclick="myVenuesRedesign.setPastGigsPage(${page - 1})" ${page <= 1 ? 'disabled' : ''} style="padding:4px 12px;font-size:0.78rem;background:${page <= 1 ? 'transparent' : 'rgba(99,91,255,0.15)'};border:1px solid ${page <= 1 ? 'rgba(255,255,255,0.1)' : 'rgba(99,91,255,0.3)'};border-radius:5px;color:${page <= 1 ? '#64748b' : '#a78bfa'};cursor:${page <= 1 ? 'default' : 'pointer'};">‹ Prev</button>
          <span style="font-size:0.78rem;color:#94a3b8;">Page ${page} of ${totalPages} · ${total} gigs</span>
          <button onclick="myVenuesRedesign.setPastGigsPage(${page + 1})" ${page >= totalPages ? 'disabled' : ''} style="padding:4px 12px;font-size:0.78rem;background:${page >= totalPages ? 'transparent' : 'rgba(99,91,255,0.15)'};border:1px solid ${page >= totalPages ? 'rgba(255,255,255,0.1)' : 'rgba(99,91,255,0.3)'};border-radius:5px;color:${page >= totalPages ? '#64748b' : '#a78bfa'};cursor:${page >= totalPages ? 'default' : 'pointer'};">Next ›</button>
        </div>` : `<div style="margin-top:12px;font-size:0.75rem;color:#64748b;text-align:right;">${total} gig${total !== 1 ? 's' : ''}</div>`}
    `;
    body.querySelectorAll('textarea').forEach(t => { t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; });
  }

  showGigDetails(gigId) {
    // Find gig in existing data
    let gig = null;
    const searchId = parseInt(gigId, 10);

    for (const venue of this.venues) {
      if (venue.gigs) {
        gig = venue.gigs.find(g => parseInt(g.id, 10) === searchId);
        if (gig) {
          // Add venue info to gig
          gig.venue_name = venue.venue_name;
          gig.venue_id = venue.venue_id || venue.id;
          gig.address_line_1 = gig.address_line_1 || venue.address_line_1;
          gig.address_line_2 = gig.address_line_2 || venue.address_line_2;
          gig.city = gig.city || venue.city;
          gig.state = gig.state || venue.state;
          break;
        }
      }
    }
    
    if (!gig) {
      console.warn('Gig not found:', gigId);
      return;
    }
    
    // Use the existing openGigModal from artist-book-gigs.js
    if (window.openGigModal) {
      window.openGigModal(gig);
    } else {
      console.error('openGigModal not available');
    }
  }
}

// Global instance
let myVenuesRedesign;

// Expose class on window so switchTab can instantiate it on demand
window._MyVenuesRedesignClass = MyVenuesRedesign;

// Initialize when DOM is ready — works whether container is visible or not
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('myVenuesRedesigned')) {
      myVenuesRedesign = new MyVenuesRedesign();
      window.myVenuesRedesign = myVenuesRedesign;
      // Only auto-load if tab is visible at page load
      if (document.getElementById('artists-tab')?.classList.contains('active')) {
        myVenuesRedesign.init();
      }
    }
  });
} else {
  if (document.getElementById('myVenuesRedesigned')) {
    myVenuesRedesign = new MyVenuesRedesign();
    window.myVenuesRedesign = myVenuesRedesign;
    if (document.getElementById('artists-tab')?.classList.contains('active')) {
      myVenuesRedesign.init();
    }
  }
}
// Delegated event listener for flyer buttons — CSP-safe replacement for inline onclick.
// CAPTURE phase (Jul 2026 fix): the outer gig-list container has
// `onclick="event.stopPropagation()"` which swallowed the bubble before
// document could see it. Capturing runs on the way DOWN, before any
// stopPropagation in the bubble chain.
document.addEventListener('click', function(e) {
  const btn = e.target.closest('.gig-flyer-btn');
  if (!btn) return;
  e.stopPropagation();
  const gigId = btn.dataset.gigId;
  if (!gigId) return;
  fetch('/api/gigs/' + gigId + '/flyer/public', { credentials: 'include' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data && (data.thumbnail_data || data.canvas_data || data.use_builtin) && typeof _showFlyerOverlay === 'function') {
        _showFlyerOverlay(data, 'flyerFullModal');
      } else {
        alert('No flyer available for this gig.');
      }
    })
    .catch(function() { alert('Could not load flyer.'); });
}, true);  // true = capture phase

// Build "Rate Venue" / "Edit Review" button for My Venues tab
function _buildRateVenueBtn(venue, venueId, venueName) {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get('artist_id');
  const hasReview = venue.my_review && venue.my_review.rating;
  const rating = hasReview ? venue.my_review.rating : 0;
  const reviewText = hasReview ? (venue.my_review.review_text || '') : '';
  const safeName = (venueName || 'Venue').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  const label = hasReview ? '✏️ Edit Review' : '⭐ Rate Venue';
  const style = hasReview
    ? 'padding:3px 10px;font-size:0.72rem;border-radius:4px;cursor:pointer;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);color:#f59e0b;white-space:nowrap;'
    : 'padding:3px 10px;font-size:0.72rem;border-radius:4px;cursor:pointer;background:rgba(6,182,212,0.1);border:1px solid rgba(6,182,212,0.3);color:#06b6d4;white-space:nowrap;';
  // Audit fix (May 2026 part 8): jsAttr (JSON.stringify-based) produces a
  // valid JS string literal that is also HTML-attribute-safe — no `"` or `'`
  // breakouts possible. Previous escape only handled `\` and `'`, missed `"`.
  const _jsa = window.jsAttr || JSON.stringify;
  const _vid = parseInt(venueId, 10) || 0;
  const _aid = parseInt(artistId, 10) || 0;
  const _rating = parseInt(rating, 10) || 0;
  return '<button onclick="event.stopPropagation(); openVenueRateModal(' + _vid + ', ' + _jsa(venueName) + ', ' + _aid + ', ' + _rating + ', ' + _jsa(reviewText) + ', this)" style="' + style + '">' + label + '</button>';
}


// Rate Venue modal for My Venues tab
// Phase 3 migration: was an inline self-built modal. Now uses showStyledModal.
// Mirror of review-modal.js but for the artist→venue direction; API path is
// /api/artists/{aid}/venues/{vid}/review.
window.openVenueRateModal = function(venueId, venueName, artistId, existingRating, existingText, triggerBtn) {
  let selected = existingRating || 0;
  const isEdit = selected > 0;
  const starLabels = ['','Poor','Fair','Good','Very Good','Excellent'];

  function starHtml(n) {
    let html = '';
    for (let i = 1; i <= 5; i++) {
      html += '<span class="_mvStar" data-val="' + i + '" style="font-size:2rem;cursor:pointer;transition:transform 0.1s,color 0.1s;user-select:none;color:' + (i<=n?'#f59e0b':'#444') + ';transform:' + (i<=n?'scale(1.1)':'scale(1)') + '">' + (i<=n?'★':'☆') + '</span>';
    }
    return html;
  }

  const esc = s => String(s||'').replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));

  const body =
    '<p style="color:#d1d5db;margin:0 0 18px 0;font-size:0.85rem;text-align:center;">' + esc(venueName) + '</p>' +
    '<div id="_mvStarRow" style="display:flex;gap:8px;justify-content:center;margin-bottom:6px;">' + starHtml(selected) + '</div>' +
    '<div id="_mvStarLabel" style="text-align:center;font-size:0.78rem;color:#9ca3af;height:16px;margin-bottom:14px;">' + (selected > 0 ? starLabels[selected] : '') + '</div>' +
    '<div id="_mvStarErr" style="color:#ef4444;font-size:0.78rem;text-align:center;margin-bottom:8px;display:none;">Please select a rating.</div>' +
    '<textarea id="_mvReviewText" rows="3" maxlength="1000" placeholder="Share your experience (optional)…" style="width:100%;box-sizing:border-box;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-radius:7px;color:#e5e5e5;padding:9px 11px;font-size:0.85rem;resize:vertical;outline:none;margin-bottom:6px;">' + esc(existingText) + '</textarea>' +
    '<div id="_mvReviewMsg" style="font-size:0.78rem;text-align:center;min-height:16px;"></div>';

  window.showStyledModal(
    isEdit ? 'Edit Your Review' : 'Rate This Venue',
    body,
    [
      {
        text: 'Delete Review', style: 'danger',
        onClick: async () => {
          const overlay = document.querySelector('.gfm-modal-overlay');
          const msg = overlay && overlay.querySelector('#_mvReviewMsg');
          const btns = overlay && overlay.querySelectorAll('.gfm-modal-footer .btn');
          const delBtn = btns && btns[0];
          if (delBtn) { delBtn.disabled = true; delBtn.textContent = 'Deleting…'; }
          try {
            const res = await fetch('/api/artists/' + artistId + '/venues/' + venueId + '/review', {
              method: 'DELETE', credentials: 'include'
            });
            if (!res.ok) throw new Error('Delete failed');
            if (msg) { msg.style.color = '#10b981'; msg.textContent = '✓ Review deleted.'; }
            if (triggerBtn) {
              triggerBtn.textContent = '⭐ Rate Venue';
              triggerBtn.style.background = 'rgba(6,182,212,0.1)';
              triggerBtn.style.border = '1px solid rgba(6,182,212,0.3)';
              triggerBtn.style.color = '#06b6d4';
              // Audit fix (May 2026 part 8): jsAttr handles \, ', ", control chars.
              const _jsa = window.jsAttr || JSON.stringify;
              const _vid = parseInt(venueId, 10) || 0;
              const _aid = parseInt(artistId, 10) || 0;
              triggerBtn.setAttribute('onclick',
                "event.stopPropagation(); openVenueRateModal(" + _vid + ", " + _jsa(venueName || '') + ", " + _aid + ", 0, '', this)");
            }
            setTimeout(() => { if (window.closeAllModals) window.closeAllModals(); }, 1200);
          } catch (e) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = 'Delete failed. Please try again.'; }
            if (delBtn) { delBtn.disabled = false; delBtn.textContent = 'Delete Review'; }
            return false;
          }
          return false;
        }
      },
      { text: 'Cancel', style: 'ghost' },
      {
        text: isEdit ? 'Update Review' : 'Submit Review', style: 'primary',
        onClick: async () => {
          const overlay = document.querySelector('.gfm-modal-overlay');
          if (!selected) {
            const err = overlay && overlay.querySelector('#_mvStarErr');
            if (err) err.style.display = '';
            return false;
          }
          const txt = overlay && overlay.querySelector('#_mvReviewText');
          const reviewText = (txt && txt.value || '').trim();
          const msg = overlay && overlay.querySelector('#_mvReviewMsg');
          const btns = overlay && overlay.querySelectorAll('.gfm-modal-footer .btn');
          const submitBtn = btns && btns[btns.length - 1];
          if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Saving…'; }
          try {
            const res = await fetch('/api/artists/' + artistId + '/venues/' + venueId + '/review', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              credentials: 'include',
              body: JSON.stringify({ rating: selected, review_text: reviewText })
            });
            if (!res.ok) throw new Error(await res.text());
            if (msg) { msg.style.color = '#10b981'; msg.textContent = '✓ Review saved!'; }
            if (triggerBtn) {
              triggerBtn.textContent = '✏️ Edit Review';
              triggerBtn.style.background = 'rgba(245,158,11,0.12)';
              triggerBtn.style.border = '1px solid rgba(245,158,11,0.35)';
              triggerBtn.style.color = '#f59e0b';
              // Audit fix (May 2026 part 8): jsAttr handles \, ', ", control chars.
              const _jsa = window.jsAttr || JSON.stringify;
              const _vid = parseInt(venueId, 10) || 0;
              const _aid = parseInt(artistId, 10) || 0;
              const _sel = parseInt(selected, 10) || 0;
              triggerBtn.setAttribute('onclick',
                'event.stopPropagation(); openVenueRateModal(' + _vid + ', ' + _jsa(venueName || '') + ', ' + _aid + ', ' + _sel + ', ' + _jsa(reviewText) + ', this)');
            }
            setTimeout(() => { if (window.closeAllModals) window.closeAllModals(); }, 1200);
          } catch (e) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = 'Failed to save. Please try again.'; }
            if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = isEdit ? 'Update Review' : 'Submit Review'; }
            return false;
          }
          return false;
        }
      }
    ],
    { size: 'md' }
  );

  // Post-mount wiring: star hover/click, hide Delete button when not editing
  setTimeout(() => {
    const overlay = document.querySelector('.gfm-modal-overlay');
    if (!overlay) return;
    const delBtn = overlay.querySelectorAll('.gfm-modal-footer .btn')[0];
    if (delBtn && !isEdit) delBtn.style.display = 'none';

    function renderStars(n) {
      overlay.querySelectorAll('._mvStar').forEach(s => {
        const v = parseInt(s.dataset.val);
        s.textContent = v <= n ? '★' : '☆';
        s.style.color = v <= n ? '#f59e0b' : '#444';
        s.style.transform = v <= n ? 'scale(1.1)' : 'scale(1)';
      });
      const lbl = overlay.querySelector('#_mvStarLabel');
      if (lbl) lbl.textContent = n > 0 ? starLabels[n] : '';
    }

    overlay.querySelectorAll('._mvStar').forEach(s => {
      s.addEventListener('mouseover', () => renderStars(parseInt(s.dataset.val)));
      s.addEventListener('mouseout',  () => renderStars(selected));
      s.addEventListener('click', () => {
        selected = parseInt(s.dataset.val);
        renderStars(selected);
        const err = overlay.querySelector('#_mvStarErr');
        if (err) err.style.display = 'none';
      });
    });
  }, 50);
};

