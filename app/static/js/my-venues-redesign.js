// v015 FIX - CLEAR YOUR BROWSER CACHE!
// v73: My Venues Redesign - Preferred & Denied Only

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
      denied: 0
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
      // v73: Include 'revoked' in denied count
      if (status === 'denied' || status === 'revoked') stats.denied++;
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
    
      // v73: Include 'revoked' in denied filter
      if (
        this.activeFilters.has('denied') &&
        (status === 'denied' || status === 'revoked')
      ) return true;
    
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
        
        <div style="display: flex; gap: 8px;">
          <div class="stat-bubble" onclick="myVenuesRedesign.toggleFilter('preferred')" style="background: ${isActive('preferred') ? 'rgba(34, 197, 94, 0.3)' : 'rgba(34, 197, 94, 0.1)'}; border: 2px solid ${isActive('preferred') ? '#22c55e' : 'rgba(34, 197, 94, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('preferred') ? '0 0 12px rgba(34, 197, 94, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #22c55e;">${stats.preferred}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Preferred</span>
          </div>
          
          <div class="stat-bubble" onclick="myVenuesRedesign.toggleFilter('denied')" style="background: ${isActive('denied') ? 'rgba(239, 68, 68, 0.3)' : 'rgba(239, 68, 68, 0.1)'}; border: 2px solid ${isActive('denied') ? '#ef4444' : 'rgba(239, 68, 68, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('denied') ? '0 0 12px rgba(239, 68, 68, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #ef4444;">${stats.denied}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Denied</span>
          </div>

          <div class="stat-bubble" onclick="myVenuesRedesign.toggleFilter('pastGigs')" style="background: ${isActive('pastGigs') ? 'rgba(59, 130, 246, 0.3)' : 'rgba(59, 130, 246, 0.1)'}; border: 2px solid ${isActive('pastGigs') ? '#3b82f6' : 'rgba(59, 130, 246, 0.3)'}; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; box-shadow: ${isActive('pastGigs') ? '0 0 12px rgba(59, 130, 246, 0.5)' : 'none'};">
            <span style="font-size: 0.9rem; font-weight: 600; color: #3b82f6;">${stats.pastGigs || 0}</span>
            <span style="font-size: 0.75rem; color: var(--text-muted); margin-left: 4px;">Past Gigs</span>
          </div>
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
    }
    
    // Waitlist badge (shown instead of or in addition to status badge)
    if (venue.waitlist_gig_id) {
      const pos = venue.waitlist_position || '?';
      const total = venue.waitlist_total || '?';
      statusBadge = `<span style="background: rgba(139,92,246,0.2); border: 1px solid rgba(139,92,246,0.5); color: #a78bfa; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;">⏳ Waitlisted (${pos} of ${total})</span>`;
    }

    // Pay and frequency: use override if set, otherwise venue default
    const payD = (venue.pay_dollars_override != null) ? venue.pay_dollars_override : (venue.venue_default_pay_dollars || 0);
    const payC = String((venue.pay_cents_override != null) ? venue.pay_cents_override : (venue.venue_default_pay_cents || 0)).padStart(2, '0');
    const freqD = (venue.frequency_days_override != null) ? venue.frequency_days_override : (venue.venue_default_freq_days || 0);

    return `
      <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 6px 8px;">
        <div style="display: grid; grid-template-columns: minmax(100px, 1fr) auto auto; align-items: center; gap: 10px;">
          <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
            <a href="/app/venue-profile.html?venue_id=${venueId}" target="_blank" onclick="event.stopPropagation()" style="font-weight: 600; font-size: 0.9rem; color: #7c6bff; text-decoration: none; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${venueName || 'Unknown Venue'}</a>
            <span style="font-size: 0.75rem; color: var(--text-muted); white-space: nowrap;">${venue.city || ''}, ${venue.state || ''}</span>
            ${hasGigs ? `<span style="font-size: 0.75rem; color: var(--text-muted); white-space: nowrap;">${gigsCount} gig${gigsCount !== 1 ? 's' : ''}</span>` : ''}
            ${venue.avg_rating ? `<span title="${venue.avg_rating}/5 from ${venue.review_count} review${venue.review_count !== 1 ? 's' : ''}" style="font-size:0.75rem; color:#f59e0b; white-space:nowrap; cursor:default;">★ ${parseFloat(venue.avg_rating).toFixed(1)}<span style="color:var(--text-muted); margin-left:2px;">(${venue.review_count})</span></span>` : ''}
          </div>
          <div style="display: flex; align-items: center; gap: 12px; background: rgba(99,91,255,0.08); border: 1px solid rgba(99,91,255,0.2); border-radius: 6px; padding: 5px 12px; white-space: nowrap;">
            <div style="display: flex; align-items: center; gap: 4px;">
              <span style="font-size: 0.75rem; color: var(--text-muted);">Pay:</span>
              <span style="font-size: 0.8rem; color: #e2e8f0; font-weight: 500;">$${payD}.${payC}</span>
            </div>
            <div style="width: 1px; height: 16px; background: rgba(99,91,255,0.25);"></div>
            <div style="display: flex; align-items: center; gap: 4px;">
              <span style="font-size: 0.75rem; color: var(--text-muted);">Frequency:</span>
              <span style="font-size: 0.8rem; color: #e2e8f0; font-weight: 500;">1 per ${freqD} days</span>
            </div>
          </div>
          <div style="display: flex; align-items: center; gap: 8px; justify-content: flex-end;">
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
          <div style="margin-top: 6px; margin-left: 20px; padding-top: 6px; border-top: 1px solid rgba(255,255,255,0.08);">
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
                  const payAmt = gig.effective_pay != null ? parseFloat(gig.effective_pay).toFixed(2) : (gig.pay != null ? parseFloat(gig.pay).toFixed(2) : null);
                  const gigIcon = ({'Live Band':'🎸','DJ':'🎧','Comedian':'🎤','Trivia Host':'🧠'}[gig.artist_type] || '🎵');
                  return `
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding:5px 8px; background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25); border-radius:5px; margin-bottom:4px;">
                      <div onclick="myVenuesRedesign.showGigDetails(${gig.id})" style="font-size:0.82rem; color:#e2e8f0; display:flex; align-items:center; gap:10px; flex-wrap:wrap; cursor:pointer;" onmouseover="this.parentElement.style.background='rgba(239,68,68,0.14)'" onmouseout="this.parentElement.style.background='rgba(239,68,68,0.08)'">
                        <strong style="white-space:nowrap;">${dateStr}</strong>
                        <span style="color:rgba(255,255,255,0.3);">|</span>
                        <span style="white-space:nowrap;">${gigStart} – ${gigEnd}</span>
                        <span style="color:rgba(255,255,255,0.3);">|</span>
                        <span style="color:#f87171; font-weight:600; white-space:nowrap;">${gigIcon} Booked${payAmt ? ' • $' + payAmt : ''}</span>
                      </div>
                      <span style="display:flex;gap:4px;flex-shrink:0;align-items:center;">
                        <span onclick="event.stopPropagation(); typeof openMessageModal === 'function' && openMessageModal(${gig.id}, '${venue.venue_name ? venue.venue_name.replace(/'/g,'') : 'Venue'}')" style="font-size:0.7rem; color:#06b6d4; cursor:pointer; white-space:nowrap; padding:2px 7px; border-radius:4px; border:1px solid rgba(6,182,212,0.25); transition:background 0.15s;" onmouseover="this.style.background='rgba(6,182,212,0.12)'" onmouseout="this.style.background='none'" title="Message Venue">Message Venue</span>
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
// Delegated event listener for flyer buttons — CSP-safe replacement for inline onclick
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
});

// Build "Rate Venue" / "Edit Review" button for My Venues tab
function _buildRateVenueBtn(venue, venueId, venueName) {
  const params = new URLSearchParams(window.location.search);
  const artistId = params.get('artist_id');
  const hasReview = venue.my_review && venue.my_review.rating;
  const rating = hasReview ? venue.my_review.rating : 0;
  const reviewText = hasReview ? (venue.my_review.review_text || '') : '';
  const safeName = (venueName || 'Venue').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  const safeText = reviewText.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  const label = hasReview ? '✏️ Edit Review' : '⭐ Rate Venue';
  const style = hasReview
    ? 'padding:3px 10px;font-size:0.72rem;border-radius:4px;cursor:pointer;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);color:#f59e0b;white-space:nowrap;'
    : 'padding:3px 10px;font-size:0.72rem;border-radius:4px;cursor:pointer;background:rgba(6,182,212,0.1);border:1px solid rgba(6,182,212,0.3);color:#06b6d4;white-space:nowrap;';
  return '<button onclick="event.stopPropagation(); openVenueRateModal(' + venueId + ', \'' + safeName + '\', ' + artistId + ', ' + rating + ', \'' + safeText + '\', this)" style="' + style + '">' + label + '</button>';
}


// Rate Venue modal for My Venues tab
window.openVenueRateModal = function(venueId, venueName, artistId, existingRating, existingText, triggerBtn) {
  var existing = document.getElementById('_myVenuesRateModal');
  if (existing) existing.remove();

  var selected = existingRating || 0;
  var isEdit = selected > 0;
  var starLabels = ['','Poor','Fair','Good','Very Good','Excellent'];

  function starHtml(n) {
    var html = '';
    for (var i = 1; i <= 5; i++) {
      html += '<span class="_mvStar" data-val="' + i + '" style="font-size:2rem;cursor:pointer;transition:transform 0.1s,color 0.1s;user-select:none;color:' + (i<=n?'#f59e0b':'#444') + ';transform:' + (i<=n?'scale(1.1)':'scale(1)') + '">' + (i<=n?'★':'☆') + '</span>';
    }
    return html;
  }

  var ov = document.createElement('div');
  ov.id = '_myVenuesRateModal';
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:20000;display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box;';
  ov.innerHTML =
    '<div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:1px solid rgba(6,182,212,0.3);border-radius:12px;padding:26px 30px;max-width:420px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5);">' +
      '<h3 style="color:#06b6d4;margin:0 0 4px;font-size:0.95rem;text-transform:uppercase;letter-spacing:0.05em;">' + (isEdit ? 'Edit Your Review' : 'Rate This Venue') + '</h3>' +
      '<p style="color:#d1d5db;margin:0 0 18px;font-size:0.85rem;">' + venueName + '</p>' +
      '<div id="_mvStarRow" style="display:flex;gap:8px;justify-content:center;margin-bottom:6px;">' + starHtml(selected) + '</div>' +
      '<div id="_mvStarLabel" style="text-align:center;font-size:0.78rem;color:#9ca3af;height:16px;margin-bottom:14px;">' + (selected > 0 ? starLabels[selected] : '') + '</div>' +
      '<div id="_mvStarErr" style="color:#ef4444;font-size:0.78rem;text-align:center;margin-bottom:8px;display:none;">Please select a rating.</div>' +
      '<textarea id="_mvReviewText" rows="3" maxlength="1000" placeholder="Share your experience (optional)\u2026" style="width:100%;box-sizing:border-box;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-radius:7px;color:#e5e5e5;padding:9px 11px;font-size:0.85rem;resize:vertical;outline:none;margin-bottom:16px;">' + (existingText || '') + '</textarea>' +
      '<div id="_mvReviewMsg" style="font-size:0.78rem;text-align:center;min-height:16px;margin-bottom:10px;"></div>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end;">' +
        '<button id="_mvDeleteBtn" style="padding:7px 18px;background:transparent;color:#ef4444;border:1px solid rgba(239,68,68,0.4);border-radius:6px;font-size:0.82rem;cursor:pointer;' + (isEdit ? '' : 'display:none;') + '">Delete Review</button>' +
        '<button id="_mvCancelBtn" style="padding:7px 18px;background:transparent;color:#9ca3af;border:1px solid rgba(255,255,255,0.15);border-radius:6px;font-size:0.82rem;cursor:pointer;">Cancel</button>' +
        '<button id="_mvSubmitBtn" style="padding:7px 20px;background:#06b6d4;color:#fff;border:none;border-radius:6px;font-size:0.82rem;font-weight:600;cursor:pointer;">' + (isEdit ? 'Update Review' : 'Submit Review') + '</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(ov);

  function renderStars(n) {
    ov.querySelectorAll('._mvStar').forEach(function(s) {
      var v = parseInt(s.dataset.val);
      s.textContent = v <= n ? '★' : '☆';
      s.style.color = v <= n ? '#f59e0b' : '#444';
      s.style.transform = v <= n ? 'scale(1.1)' : 'scale(1)';
    });
    var lbl = document.getElementById('_mvStarLabel');
    if (lbl) lbl.textContent = n > 0 ? starLabels[n] : '';
  }

  ov.querySelectorAll('._mvStar').forEach(function(s) {
    s.addEventListener('mouseover', function() { renderStars(parseInt(s.dataset.val)); });
    s.addEventListener('mouseout',  function() { renderStars(selected); });
    s.addEventListener('click',     function() {
      selected = parseInt(s.dataset.val);
      renderStars(selected);
      document.getElementById('_mvStarErr').style.display = 'none';
    });
  });

  ov.querySelector('#_mvCancelBtn').addEventListener('click', function() { ov.remove(); });
  ov.addEventListener('click', function(e) { if (e.target === ov) ov.remove(); });

  var delBtnEl = ov.querySelector('#_mvDeleteBtn');
  if (delBtnEl) {
    delBtnEl.addEventListener('click', async function() {
      var msg = document.getElementById('_mvReviewMsg');
      delBtnEl.disabled = true; delBtnEl.textContent = 'Deleting…';
      try {
        var res = await fetch('/api/artists/' + artistId + '/venues/' + venueId + '/review', {
          method: 'DELETE', credentials: 'include'
        });
        if (!res.ok) throw new Error('Delete failed');
        if (msg) { msg.style.color = '#10b981'; msg.textContent = '✓ Review deleted.'; }
        // Reset button in venue row
        if (triggerBtn) {
          triggerBtn.textContent = '⭐ Rate Venue';
          triggerBtn.style.background = 'rgba(6,182,212,0.1)';
          triggerBtn.style.border = '1px solid rgba(6,182,212,0.3)';
          triggerBtn.style.color = '#06b6d4';
          var safeName3 = (venueName || '').replace(/'/g, "\\'");
          triggerBtn.setAttribute('onclick',
            "event.stopPropagation(); openVenueRateModal(" + venueId + ", '" + safeName3 + "', " + artistId + ", 0, '', this)");
        }
        setTimeout(function() { ov.remove(); }, 1200);
      } catch(e) {
        if (msg) { msg.style.color = '#ef4444'; msg.textContent = 'Delete failed. Please try again.'; }
        delBtnEl.disabled = false; delBtnEl.textContent = 'Delete Review';
      }
    });
  }

  ov.querySelector('#_mvSubmitBtn').addEventListener('click', async function() {
    if (!selected) { document.getElementById('_mvStarErr').style.display = ''; return; }
    var reviewText = (document.getElementById('_mvReviewText').value || '').trim();
    var msg = document.getElementById('_mvReviewMsg');
    var btn = document.getElementById('_mvSubmitBtn');
    btn.disabled = true; btn.textContent = 'Saving\u2026';

    try {
      var res = await fetch('/api/artists/' + artistId + '/venues/' + venueId + '/review', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'include',
        body: JSON.stringify({rating: selected, review_text: reviewText})
      });
      if (!res.ok) throw new Error(await res.text());

      msg.style.color = '#10b981';
      msg.textContent = '\u2713 Review saved!';

      if (triggerBtn) {
        triggerBtn.textContent = '\u270f\ufe0f Edit Review';
        triggerBtn.style.background = 'rgba(245,158,11,0.12)';
        triggerBtn.style.border = '1px solid rgba(245,158,11,0.35)';
        triggerBtn.style.color = '#f59e0b';
        var safeText = reviewText.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        var safeName2 = (venueName || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        triggerBtn.setAttribute('onclick',
          'event.stopPropagation(); openVenueRateModal(' + venueId + ', \'' + safeName2 + '\', ' + artistId + ', ' + selected + ', \'' + safeText + '\', this)');
      }
      setTimeout(function() { ov.remove(); }, 1200);
    } catch(e) {
      msg.style.color = '#ef4444';
      msg.textContent = 'Failed to save. Please try again.';
      btn.disabled = false; btn.textContent = isEdit ? 'Update Review' : 'Submit Review';
    }
  });
};

