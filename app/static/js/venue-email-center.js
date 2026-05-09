/**
 * Venue Email Center
 * Allows venues to send emails to preferred artists
 */

let currentVenue = null;
let preferredArtists = [];
let selectedArtists = new Set();
let emailHistory = [];

// Pagination and sorting state
let currentPage = 1;
const itemsPerPage = 10;
let sortColumn = 'sent_at';
let sortDirection = 'desc'; // 'asc', 'desc', or null

// Tab switching
function switchEmailTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.ec-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.ec-tab-content').forEach(c => c.classList.remove('active'));
    
    // Activate clicked tab
    const btn = document.querySelector('.ec-tab[onclick*="' + tabName + '"]');
    if (btn) btn.classList.add('active');
    
    const content = document.getElementById(tabName + '-tab');
    if (content) content.classList.add('active');
    
    // Load invitation data when switching to that tab
    if (tabName === 'invite-artists' && currentVenue) {
        loadInvitedArtists(currentVenue.id);
    }
}

function getVenueIdFromURL() {
    const params = new URLSearchParams(window.location.search);

    // URL takes priority
    const urlVenueId = params.get("venue_id");
    if (urlVenueId) return urlVenueId;

    // Fallback to loaded venue
    if (currentVenue && currentVenue.id) {
        return currentVenue.id;
    }

    return null;
}

function updateHeaderLinks() {
    const headerActions = document.getElementById('headerActions');
    if (!headerActions) return;

    const venueId = getVenueIdFromURL();

    // Note: User dropdown is added after via initUserDropdown()
    headerActions.innerHTML = `
        <a class="btn ghost" href="/app/venue-create-gigs.html${venueId ? `?venue_id=${venueId}` : ''}">
            Create Gigs
        </a>
        <a class="btn ghost" href="/app/venue-profile.html${venueId ? `?venue_id=${venueId}` : ''}" target="_blank">
            Venue Profile
        </a>
        <a class="btn ghost" href="/app/venue-edit.html${venueId ? `?venue_id=${venueId}` : ''}">
            Edit Venue Profile
        </a>
    `;
    
    // Re-initialize user dropdown after setting header links
    if (typeof initUserDropdown === 'function') {
        initUserDropdown();
    }
    
    // Load and display venue name in header
    if (venueId) {
        fetch(`/api/venues/${venueId}`, { credentials: 'include' })
            .then(res => res.ok ? res.json() : null)
            .then(venue => {
                if (venue && (venue.venue_name || venue.name)) {
                    const logo = document.querySelector('.logo');
                    if (logo) {
                        const venueName = venue.venue_name || venue.name;
                        logo.innerHTML = `<img src="/app/static/img/gigsfill-logo.png" alt="GigsFill" style="height:44px;width:auto;flex-shrink:0;"><span style="font: 600 0.875rem 'Inter', sans-serif; color: var(--cyan); margin-left: 24px; background: none; -webkit-background-clip: unset; -webkit-text-fill-color: var(--cyan); letter-spacing: normal;">[${esc(venueName)}]</span>`;
                    }
                }
            })
            .catch(() => {});
    }
}

// Initialize page
// Check if running embedded inside venue-create-gigs (tab mode)
const _isEmbeddedEmailCenter = !!document.getElementById('emailcenter-tab');

if (!_isEmbeddedEmailCenter) {
    document.addEventListener('DOMContentLoaded', async () => {
        updateHeaderLinks();
        await loadVenues();
        setupFormListeners();
    });
}

// Called from venue-create-gigs.html when Email Center tab is opened
let _emailCenterInitialized = false;
function initEmailCenterForVenue(venueId) {
    currentVenue = { id: parseInt(venueId) };
    
    // Hide venue selector since we already know the venue
    const selectorContainer = document.getElementById('venueSelectorContainer');
    if (selectorContainer) selectorContainer.style.display = 'none';
    
    if (!_emailCenterInitialized) {
        setupFormListeners();
        _emailCenterInitialized = true;
    }
    
    // Fetch venue name so "Message from [Venue Name]" and subject line are correct
    fetch(`/api/venues/${venueId}`, { credentials: 'include' })
        .then(res => res.ok ? res.json() : null)
        .then(venue => {
            if (venue) {
                currentVenue.venue_name = venue.venue_name || venue.name || '';
                currentVenue.name = currentVenue.venue_name;
            }
        })
        .catch(() => {});
    
    loadPreferredArtists(venueId);
    loadEmailHistory(venueId);
    loadInvitedArtists(venueId);
}

// Load user's venues
async function loadVenues() {
    try {
        const response = await fetch('/api/my/venues', { credentials: 'include' });
        if (!response.ok) throw new Error('Failed to load venues');
        
        const venues = await response.json();
        
        if (venues.length === 0) {
            showNoVenuesMessage();
            return;
        }
        
        // Check if venue_id is in URL
        const urlVenueId = getVenueIdFromURL();
        
        if (urlVenueId) {
            // Find the venue matching URL
            currentVenue = venues.find(v => v.id == urlVenueId);
            if (!currentVenue && venues.length > 0) {
                currentVenue = venues[0];
            }
        } else if (venues.length === 1) {
            currentVenue = venues[0];
        }
        
        if (currentVenue) {
            // Update URL if needed
            const url = new URL(window.location.href);
            if (!url.searchParams.get('venue_id')) {
                url.searchParams.set('venue_id', currentVenue.id);
                window.history.replaceState({}, '', url);
            }
            
            updateHeaderLinks();
            await loadPreferredArtists(currentVenue.id);
            await loadEmailHistory(currentVenue.id);
            loadInvitedArtists(currentVenue.id);
        } else if (venues.length > 1) {
            // Multiple venues - show selector
            showVenueSelector(venues);
        }
    } catch (error) {
        console.error('Error loading venues:', error);
        showError('Failed to load venues. Please refresh the page.');
    }
}

// Show venue selector for users with multiple venues
function showVenueSelector(venues) {
    const container = document.getElementById('venueSelectorContainer');
    const select = document.getElementById('venueSelect');
    
    select.innerHTML = '<option value="">Select a venue...</option>' +
        venues.map(v => `<option value="${v.id}">${v.name}</option>`).join('');
    
    select.addEventListener('change', async (e) => {
        const venueId = parseInt(e.target.value);
        if (venueId) {
            currentVenue = venues.find(v => v.id === venueId);
            
            // Update URL
            const url = new URL(window.location.href);
            url.searchParams.set('venue_id', venueId);
            window.history.replaceState({}, '', url);
            
            updateHeaderLinks();
            await loadPreferredArtists(venueId);
            await loadEmailHistory(venueId);
            loadInvitedArtists(venueId);
        }
    });
    
    container.style.display = 'block';
}

// Load preferred artists for selected venue
async function loadPreferredArtists(venueId) {
    try {
        const recipientsGrid = document.getElementById('recipientsGrid');
        recipientsGrid.innerHTML = '<div class="loading">Loading preferred artists...</div>';
        
        const response = await fetch(`/api/venues/${venueId}/preferred-artists`, { 
            credentials: 'include' 
        });
        
        if (!response.ok) throw new Error('Failed to load preferred artists');
        
        const artists = await response.json();
        preferredArtists = artists.filter(a => a.status === 'approved');
        
        if (preferredArtists.length === 0) {
            showNoArtistsMessage();
            return;
        }
        
        displayArtists();
        document.getElementById('recipientsSection').style.display = 'block';
        document.getElementById('emailFormContainer').style.display = 'block';
    } catch (error) {
        console.error('Error loading preferred artists:', error);
        showError('Failed to load preferred artists.');
    }
}

// Display preferred artists as selectable cards
function displayArtists() {
    const recipientsGrid = document.getElementById('recipientsGrid');
    
    recipientsGrid.innerHTML = preferredArtists.map(artist => {
        const artistEmail = artist.artist_email || artist.email || 'No email';
        const artistName = artist.artist_name || artist.name || 'Unknown';
        const isSelected = selectedArtists.has(artist.artist_id);
        
        return `
            <div class="recipient-card ${isSelected ? 'selected' : ''}" 
                 onclick="toggleArtist(${artist.artist_id})"
                 data-artist-id="${artist.artist_id}">
                <input type="checkbox" 
                       class="recipient-checkbox" 
                       ${isSelected ? 'checked' : ''}
                       onclick="event.stopPropagation(); toggleArtist(${artist.artist_id})">
                <span class="artist-name">${artistName}</span>
                <span class="artist-email">${artistEmail}</span>
            </div>
        `;
    }).join('');
    
    updateSelectedCount();
    updateSendButton();
    updateToggleButton();
}

// Toggle artist selection
function toggleArtist(artistId) {
    if (selectedArtists.has(artistId)) {
        selectedArtists.delete(artistId);
    } else {
        selectedArtists.add(artistId);
    }
    
    // Update UI
    const card = document.querySelector(`[data-artist-id="${artistId}"]`);
    const checkbox = card.querySelector('.recipient-checkbox');
    
    if (selectedArtists.has(artistId)) {
        card.classList.add('selected');
        checkbox.checked = true;
    } else {
        card.classList.remove('selected');
        checkbox.checked = false;
    }
    
    updateSelectedCount();
    updateSendButton();
}

// Toggle all artists selection
function toggleAllArtists() {
    if (selectedArtists.size === preferredArtists.length) {
        // All selected, so deselect all
        selectedArtists.clear();
    } else {
        // Not all selected, so select all
        preferredArtists.forEach(artist => {
            selectedArtists.add(artist.artist_id);
        });
    }
    displayArtists();
}

// Update the toggle button text
function updateToggleButton() {
    const btn = document.getElementById('toggleSelectBtn');
    if (!btn) return;
    
    if (selectedArtists.size === preferredArtists.length && preferredArtists.length > 0) {
        btn.textContent = 'Deselect All';
    } else {
        btn.textContent = 'Select All';
    }
}

// Legacy functions for compatibility
function selectAllArtists() {
    preferredArtists.forEach(artist => {
        selectedArtists.add(artist.artist_id);
    });
    displayArtists();
}

function deselectAllArtists() {
    selectedArtists.clear();
    displayArtists();
}

// Update selected count display
function updateSelectedCount() {
    document.getElementById('selectedCount').textContent = 
        `${selectedArtists.size} selected`;
}

// Update send button state
function updateSendButton() {
    const sendBtn = document.getElementById('sendEmailBtn');
    const subject = document.getElementById('emailSubject').value.trim();
    const body = document.getElementById('emailBody').value.trim();
    
    sendBtn.disabled = !(selectedArtists.size > 0 && subject && body);
}

// Setup form field listeners
function setupFormListeners() {
    const subjectField = document.getElementById('emailSubject');
    const bodyField = document.getElementById('emailBody');
    
    subjectField.addEventListener('input', (e) => {
        document.getElementById('subjectCount').textContent = e.target.value.length;
        updateSendButton();
    });
    
    bodyField.addEventListener('input', (e) => {
        const count = e.target.value.length;
        document.getElementById('bodyCount').textContent = count;
        
        // Limit to 5000 characters
        if (count > 5000) {
            e.target.value = e.target.value.substring(0, 5000);
            document.getElementById('bodyCount').textContent = '5000';
        }
        
        updateSendButton();
    });
}

// Send email to selected artists
async function sendEmail() {
    const subject = document.getElementById('emailSubject').value.trim();
    const body = document.getElementById('emailBody').value.trim();
    
    if (!currentVenue || selectedArtists.size === 0 || !subject || !body) {
        showError('Please fill in all fields and select at least one artist.');
        return;
    }
    
    const sendBtn = document.getElementById('sendEmailBtn');
    sendBtn.disabled = true;
    sendBtn.textContent = 'Sending...';
    
    try {
        const response = await fetch('/api/venues/send-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                venue_id: currentVenue.id,
                venue_name: currentVenue.venue_name || currentVenue.name,
                artist_ids: Array.from(selectedArtists),
                subject: subject,
                body: body
            })
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to send email');
        }
        
        const result = await response.json();
        
        // Show prominent "Email Sent!" confirmation (success div + span)
        const successMessage = document.getElementById('successMessage');
        const errorMessage = document.getElementById('errorMessage');
        const emailSentMsg = document.getElementById('emailSentMsg');
        if (successMessage) {
            successMessage.textContent = 'Email Sent!';
            successMessage.style.display = 'block';
            if (errorMessage) errorMessage.style.display = 'none';
        }
        if (emailSentMsg) {
            emailSentMsg.textContent = 'Email Sent!';
            emailSentMsg.classList.add('show');
        }
        setTimeout(() => {
            if (emailSentMsg) emailSentMsg.classList.remove('show');
            if (successMessage) successMessage.style.display = 'none';
        }, 4000);
        
        clearEmailForm();
        await loadEmailHistory(currentVenue.id);
        
    } catch (error) {
        console.error('Error sending email:', error);
        showError(error.message || 'Failed to send email. Please try again.');
    } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send Email';
        updateSendButton();
    }
}

// Clear email form
function clearEmailForm() {
    document.getElementById('emailSubject').value = '';
    document.getElementById('emailBody').value = '';
    document.getElementById('subjectCount').textContent = '0';
    document.getElementById('bodyCount').textContent = '0';
    deselectAllArtists();
}

// Load email history
async function loadEmailHistory(venueId) {
    if (!venueId) {
        venueId = getVenueIdFromURL();
    }
    
    if (!venueId) {
        document.getElementById('emailHistory').innerHTML = 
            '<p class="empty-state">Select a venue to see email history.</p>';
        return;
    }
    
    // Reset pagination
    currentPage = 1;
    
    try {
        const response = await fetch(`/api/venue-emails/history?venue_id=${venueId}&_=${Date.now()}`, { 
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (!response.ok) {
            emailHistory = [];
            displayEmailHistory();
            return;
        }
        
        emailHistory = await response.json();
        displayEmailHistory();
    } catch (error) {
        emailHistory = [];
        displayEmailHistory();
    }
}

// FIX (May 2026): per-row delete handler using two-click confirm.
// First click on "Delete" → button text becomes "Confirm?" with red filled bg.
// Second click within 3 seconds → actually deletes.
// No click within 3 seconds → button reverts to "Delete".
// Avoids the ugly browser confirm() dialog while preventing accidental deletion.
const _emailDeletePending = {};  // email_id -> { timer }

function requestDeleteEmailHistory(emailId) {
    const btn = document.getElementById('del-email-' + emailId);
    if (!btn) return;

    // Already in pending-confirm state? Then this is the actual delete click.
    if (_emailDeletePending[emailId]) {
        clearTimeout(_emailDeletePending[emailId].timer);
        delete _emailDeletePending[emailId];
        _commitDeleteEmailHistory(emailId, btn);
        return;
    }

    // First click: switch to "Confirm?" and start 3s revert timer
    btn.textContent = 'Confirm?';
    btn.style.background = 'rgba(239,68,68,0.85)';
    btn.style.color = '#ffffff';
    btn.style.borderColor = '#ef4444';
    // Override hover styles to keep filled-red appearance
    btn.onmouseover = function () { this.style.background = '#ef4444'; };
    btn.onmouseout  = function () { this.style.background = 'rgba(239,68,68,0.85)'; };

    _emailDeletePending[emailId] = {
        timer: setTimeout(function () {
            // User didn't confirm — revert button
            const b = document.getElementById('del-email-' + emailId);
            if (b) {
                b.textContent = 'Delete';
                b.style.background = 'transparent';
                b.style.color = '#ef4444';
                b.style.borderColor = 'rgba(239,68,68,0.5)';
                b.onmouseover = function () { this.style.background = 'rgba(239,68,68,0.1)'; };
                b.onmouseout  = function () { this.style.background = 'transparent'; };
            }
            delete _emailDeletePending[emailId];
        }, 3000)
    };
}

async function _commitDeleteEmailHistory(emailId, btn) {
    btn.disabled = true;
    btn.style.opacity = '0.6';
    btn.textContent = 'Deleting…';
    try {
        const resp = await fetch('/api/venue-emails/history/' + emailId, {
            method: 'DELETE',
            credentials: 'include',
        });
        if (!resp.ok) {
            const errBody = await resp.text();
            throw new Error(errBody || ('HTTP ' + resp.status));
        }
        // Remove locally so the UI updates without re-fetch
        emailHistory = emailHistory.filter(e => e.id !== emailId);
        displayEmailHistory();
    } catch (e) {
        // Restore button on error
        btn.disabled = false;
        btn.style.opacity = '';
        btn.textContent = 'Delete';
        btn.style.background = 'transparent';
        btn.style.color = '#ef4444';
        btn.style.borderColor = 'rgba(239,68,68,0.5)';
        showSuccess('Could not delete: ' + (e.message || e));
    }
}

// Display email history with sorting and pagination
function displayEmailHistory() {
    const historyContainer = document.getElementById('emailHistory');
    
    if (!emailHistory || emailHistory.length === 0) {
        historyContainer.innerHTML = 
            '<p class="empty-state">No emails sent yet.</p>';
        updatePaginationControls(0);
        return;
    }
    
    // Sort the data
    let sortedHistory = [...emailHistory];
    if (sortColumn && sortDirection) {
        sortedHistory.sort((a, b) => {
            let valA, valB;
            
            if (sortColumn === 'sent_at') {
                valA = (typeof parseUTC === 'function' ? parseUTC(a.sent_at) : new Date(a.sent_at)).getTime();
                valB = (typeof parseUTC === 'function' ? parseUTC(b.sent_at) : new Date(b.sent_at)).getTime();
            } else if (sortColumn === 'artist') {
                valA = getArtistDisplay(a).toLowerCase();
                valB = getArtistDisplay(b).toLowerCase();
            } else if (sortColumn === 'subject') {
                valA = (a.subject || '').toLowerCase();
                valB = (b.subject || '').toLowerCase();
            }
            
            if (sortDirection === 'asc') {
                return valA > valB ? 1 : valA < valB ? -1 : 0;
            } else {
                return valA < valB ? 1 : valA > valB ? -1 : 0;
            }
        });
    }
    
    // Paginate
    const totalPages = Math.ceil(sortedHistory.length / itemsPerPage);
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;
    
    const startIdx = (currentPage - 1) * itemsPerPage;
    const pageData = sortedHistory.slice(startIdx, startIdx + itemsPerPage);
    
    // Find original indices for click handler
    const originalIndices = pageData.map(item => emailHistory.indexOf(item));
    
    historyContainer.innerHTML = pageData.map((email, idx) => {
        const originalIdx = originalIndices[idx];
        const artistDisplay = getArtistDisplay(email);
        const subjectDisplay = truncateText(email.subject || '', 50);
        const sentDisplay = formatDateShort(email.sent_at);

        // FIX (May 2026): per-row Delete button using site's standard btn styling.
        // Two-click confirm pattern (Delete → Confirm? → actually deletes) avoids
        // a confirm() popup. Button reverts to "Delete" after 3 seconds if user
        // doesn't confirm. event.stopPropagation() prevents row's modal-open click.
        return `
            <div class="history-item" onclick="openEmailDetail(${originalIdx})">
                <div class="history-sent">${sentDisplay}</div>
                <div class="history-artist">${artistDisplay}</div>
                <div class="history-subject">${subjectDisplay}</div>
                <button type="button" class="btn"
                        id="del-email-${email.id}"
                        onclick="event.stopPropagation(); requestDeleteEmailHistory(${email.id});"
                        style="padding: 4px 10px; font-size: 0.75rem; line-height: 1; height: 26px; align-self: center; justify-self: end; border: 1px solid rgba(239,68,68,0.5); color: #ef4444; background: transparent; border-radius: 4px; transition: all 0.15s;"
                        onmouseover="this.style.background='rgba(239,68,68,0.1)';"
                        onmouseout="this.style.background='transparent';">
                    Delete
                </button>
            </div>
        `;
    }).join('');
    
    updatePaginationControls(sortedHistory.length);
    updateSortArrows();
}

// Get artist display name
function getArtistDisplay(email) {
    if (email.recipients_json) {
        try {
            const recipients = JSON.parse(email.recipients_json);
            if (recipients.length === 1) {
                return recipients[0].name;
            } else if (recipients.length > 1) {
                return 'Multiple Artists';
            }
        } catch (e) {}
    }
    return email.recipient_count === 1 ? '1 Artist' : `${email.recipient_count} Artists`;
}

// Truncate text
function truncateText(text, maxLen) {
    if (text.length <= maxLen) return text;
    return text.substring(0, maxLen) + '...';
}

// Format date short (for table)
function formatDateShort(sentAt) {
    if (typeof formatUTC === 'function') return formatUTC(sentAt, 'short');
    return new Date(sentAt).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit'
    });
}

// Toggle sort
function toggleSort(column) {
    if (sortColumn === column) {
        // Cycle: desc -> asc -> none -> desc
        if (sortDirection === 'desc') {
            sortDirection = 'asc';
        } else if (sortDirection === 'asc') {
            sortDirection = null;
            sortColumn = null;
        } else {
            sortDirection = 'desc';
            sortColumn = column;
        }
    } else {
        sortColumn = column;
        sortDirection = 'desc';
    }
    
    currentPage = 1;
    displayEmailHistory();
}

// Update sort arrows
function updateSortArrows() {
    // Clear all arrows
    document.querySelectorAll('.sort-arrow').forEach(el => el.textContent = '');
    
    // Set active arrow
    if (sortColumn && sortDirection) {
        const arrow = document.getElementById(`sort-${sortColumn}`);
        if (arrow) {
            arrow.textContent = sortDirection === 'desc' ? '▼' : '▲';
        }
    }
}

// Pagination
function updatePaginationControls(totalItems) {
    const totalPages = Math.ceil(totalItems / itemsPerPage) || 1;
    
    document.getElementById('pageInfo').textContent = `${currentPage} / ${totalPages}`;
    document.getElementById('prevPageBtn').disabled = currentPage <= 1;
    document.getElementById('nextPageBtn').disabled = currentPage >= totalPages;
}

function prevPage() {
    if (currentPage > 1) {
        currentPage--;
        displayEmailHistory();
    }
}

function nextPage() {
    const totalPages = Math.ceil(emailHistory.length / itemsPerPage);
    if (currentPage < totalPages) {
        currentPage++;
        displayEmailHistory();
    }
}

// Format date for display
function formatDate(sentAt) {
    if (typeof formatUTC === 'function') return formatUTC(sentAt, 'short');
    const date = new Date(sentAt);
    const datePart = date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
    });

    const timePart = date.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit'
    });

    return `${datePart} · ${timePart}`;
}

// Open email detail modal
function openEmailDetail(index) {
    const email = emailHistory[index];

    // FIX (May 2026): "To:" was just "N artist(s)" — uninformative. Now click
    // to expand and see each recipient's name + email. Older history rows that
    // pre-date this fix don't have recipients_json — for those, show count only.
    const count = email.recipient_count || 0;
    let recipients = [];
    if (email.recipients_json) {
        try {
            const parsed = JSON.parse(email.recipients_json);
            if (Array.isArray(parsed)) recipients = parsed;
        } catch (e) {
            // ignore parse errors, treat as no recipients data
        }
    }

    let recipientsHtml;
    if (recipients.length > 0) {
        // Build chevron summary + collapsed list. JS toggles on click.
        const summaryId = `_emailRecipSummary_${index}`;
        const listId    = `_emailRecipList_${index}`;
        const items = recipients.map(r => {
            const nm = esc(r.name || 'Artist');
            const em = esc(r.email || '');
            return `<div style="padding: 4px 0; font-size: 0.85rem; color: var(--text-white);">
                ${nm} <span style="color: var(--text-muted);">&lt;${em}&gt;</span>
            </div>`;
        }).join('');
        recipientsHtml = `
            <button id="${summaryId}" type="button"
                    onclick="(function(){
                        const list = document.getElementById('${listId}');
                        const sum  = document.getElementById('${summaryId}');
                        const isHidden = list.style.display === 'none';
                        list.style.display = isHidden ? '' : 'none';
                        sum.querySelector('.chev').textContent = isHidden ? '▾' : '▸';
                    })();"
                    style="background: none; border: none; padding: 0; color: var(--cyan); cursor: pointer; font-size: 0.85rem; text-align: left; display: inline-flex; align-items: center; gap: 6px;">
                <span class="chev" style="font-size: 0.7rem;">▸</span>
                <span>${count} artist${count === 1 ? '' : 's'}</span>
            </button>
            <div id="${listId}" style="display: none; margin-top: 8px; padding-left: 16px; border-left: 2px solid rgba(255,255,255,0.08);">
                ${items}
            </div>
        `;
    } else {
        // Older row without recipients_json — show count, not clickable
        recipientsHtml = `<span style="color: var(--text-white);">${count} artist${count === 1 ? '' : 's'}</span>`;
    }

    document.getElementById('emailDetailBody').innerHTML = `
        <div style="display: grid; grid-template-columns: 70px 1fr; gap: 6px 12px; margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,0.08);">
            <div style="font-size: 0.8rem; color: var(--text-muted); font-weight: 600;">To:</div>
            <div style="font-size: 0.85rem;">${recipientsHtml}</div>
            <div style="font-size: 0.8rem; color: var(--text-muted); font-weight: 600;">Sent:</div>
            <div style="font-size: 0.85rem; color: var(--text-white);">${formatDate(email.sent_at)}</div>
            <div style="font-size: 0.8rem; color: var(--text-muted); font-weight: 600;">Subject:</div>
            <div style="font-size: 0.85rem; color: var(--text-white);">${esc(email.subject)}</div>
        </div>
        <div class="modal-body">${email.body}</div>
    `;

    document.getElementById('emailDetailModal').classList.remove('hidden');
}

// Show success message
function showSuccess(message) {
    const successMsg = document.getElementById('successMessage');
    successMsg.textContent = message;
    successMsg.style.display = 'block';
    
    setTimeout(() => {
        successMsg.style.display = 'none';
    }, 5000);
}

// Show error message
function showError(message) {
    const errorMsg = document.getElementById('errorMessage');
    errorMsg.textContent = message;
    errorMsg.style.display = 'block';
    
    setTimeout(() => {
        errorMsg.style.display = 'none';
    }, 5000);
}

// Show message when user has no venues
function showNoVenuesMessage() {
    const recipientsGrid = document.getElementById('recipientsGrid');
    recipientsGrid.innerHTML = `
        <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
                <polyline points="9 22 9 12 15 12 15 22"/>
            </svg>
            <h3>No Venues Found</h3>
            <p>You need to create a venue before you can send emails to artists.</p>
            <a href="/app/venue-create-gigs.html" class="btn primary" style="display: inline-block; margin-top: 1rem;">
                Create Venue
            </a>
        </div>
    `;
}

// Show message when venue has no preferred artists
function showNoArtistsMessage() {
    const recipientsGrid = document.getElementById('recipientsGrid');
    recipientsGrid.innerHTML = `
        <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                <circle cx="9" cy="7" r="4"/>
                <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
                <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
            </svg>
            <h3>No Preferred Artists</h3>
            <p>This venue doesn't have any approved preferred artists yet.</p>
        </div>
    `;
    
    document.getElementById('recipientsSection').style.display = 'block';
}

// ===== INVITED ARTISTS TRACKER =====

async function loadInvitedArtists(venueId) {
    const section = document.getElementById('invitedArtistsSection');
    const list = document.getElementById('invitedArtistsList');
    if (!section || !list) return;
    
    try {
        const response = await fetch('/api/venues/' + venueId + '/invitations', { credentials: 'include' });
        if (!response.ok) {
            list.innerHTML = '<div class="inv-empty">No artists invited yet. Click <strong>+ Invite Artists</strong> above to get started!</div>';
            return;
        }
        
        const data = await response.json();
        
        // Update stats
        const totalEl = document.getElementById('invTotalCount');
        const pendingEl = document.getElementById('invPendingCount');
        const signedUpEl = document.getElementById('invSignedUpCount');
        if (totalEl) totalEl.textContent = data.total || 0;
        if (pendingEl) pendingEl.textContent = data.pending || 0;
        if (signedUpEl) signedUpEl.textContent = data.signed_up || 0;
        
        if (!data.invitations || data.invitations.length === 0) {
            list.innerHTML = '<div class="inv-empty">No artists invited yet. Click <strong>+ Invite Artists</strong> above to get started!</div>';
            return;
        }
        
        // Render rows
        list.innerHTML = data.invitations.map(inv => {
            const date = inv.sent_at ? formatInvDate(inv.sent_at) : '—';
            const isSignedUp = inv.status === 'signed_up';
            const statusBadge = isSignedUp
                ? '<span class="inv-status-badge signed-up">✓ Signed Up</span>'
                : '<span class="inv-status-badge pending">Pending</span>';
            
            let actionHtml = '';
            if (isSignedUp) {
                const suDate = inv.signed_up_at ? formatInvDate(inv.signed_up_at) : '';
                actionHtml = suDate ? '<span style="font-size:0.72rem;color:#22c55e;">' + suDate + '</span>' : '—';
            } else {
                const resentNote = inv.resent_count > 0
                    ? ' <span style="font-size:0.65rem;color:#6b7280;">(×' + inv.resent_count + ')</span>'
                    : '';
                actionHtml = '<button class="inv-resend-btn" onclick="resendInvitation(' + inv.id + ', this)">Resend</button>' + resentNote
                    + ' <button class="inv-delete-btn" onclick="deleteInvitation(' + inv.id + ', this)" title="Delete this invitation" style="margin-left:6px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:3px 10px;font-size:0.75rem;cursor:pointer;font-weight:500;">Delete</button>';
            }
            
            return '<div class="inv-row">' +
                '<div class="inv-email" title="' + escapeAttr(inv.email) + '">' + escapeHtmlLocal(inv.email) + '</div>' +
                '<div>' + statusBadge + '</div>' +
                '<div class="inv-date">' + date + '</div>' +
                '<div>' + actionHtml + '</div>' +
                '</div>';
        }).join('');
        
    } catch (error) {
        console.error('Error loading invitations:', error);
        list.innerHTML = '<div class="inv-empty">No artists invited yet. Click <strong>+ Invite Artists</strong> above to get started!</div>';
    }
}

function formatInvDate(dateStr) {
    try {
        const d = new Date(dateStr + (dateStr.includes('Z') || dateStr.includes('+') ? '' : 'Z'));
        const now = new Date();
        const diffMs = now - d;
        const diffDays = Math.floor(diffMs / 86400000);
        
        if (diffDays === 0) return 'Today';
        if (diffDays === 1) return 'Yesterday';
        if (diffDays < 7) return diffDays + 'd ago';
        
        return (d.getMonth() + 1) + '/' + d.getDate() + '/' + (d.getFullYear() % 100);
    } catch(e) { return dateStr; }
}

function escapeHtmlLocal(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

function escapeAttr(text) {
    return (text || '').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

async function resendInvitation(invitationId, btn) {
    const venueId = (currentVenue && currentVenue.id) || window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (!venueId) return;
    btn.disabled = true;
    btn.textContent = 'Sending...';
    
    try {
        const response = await fetch('/api/venues/' + venueId + '/resend-invitation/' + invitationId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include'
        });
        
        if (!response.ok) throw new Error('Failed');
        
        btn.textContent = 'Sent ✓';
        btn.style.color = '#22c55e';
        btn.style.borderColor = 'rgba(34,197,94,0.4)';
        
        // Reload the tracker after a moment
        setTimeout(() => loadInvitedArtists(venueId), 1500);
    } catch (error) {
        btn.textContent = 'Failed';
        btn.style.color = '#ef4444';
        setTimeout(() => {
            btn.textContent = 'Resend';
            btn.style.color = '';
            btn.style.borderColor = '';
            btn.disabled = false;
        }, 2000);
    }
}

async function deleteInvitation(invitationId, btn) {
    const venueId = (currentVenue && currentVenue.id) || window.venueId || new URLSearchParams(window.location.search).get('venue_id');
    if (!venueId) return;

    // Confirm before deleting
    if (btn.dataset.confirm !== 'true') {
        btn.dataset.confirm = 'true';
        btn.textContent = 'Confirm?';
        btn.style.background = 'rgba(239,68,68,0.25)';
        setTimeout(() => {
            if (btn.dataset.confirm === 'true') {
                btn.dataset.confirm = '';
                btn.textContent = 'Delete';
                btn.style.background = 'rgba(239,68,68,0.1)';
            }
        }, 4000);
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Deleting...';
    try {
        const response = await fetch('/api/venues/' + venueId + '/invitations/' + invitationId, {
            method: 'DELETE',
            credentials: 'include'
        });
        if (!response.ok) throw new Error('Failed');
        // Remove the row immediately
        const row = btn.closest('.inv-row');
        if (row) {
            row.style.transition = 'opacity 0.3s';
            row.style.opacity = '0';
            setTimeout(() => { loadInvitedArtists(venueId); }, 350);
        }
    } catch (error) {
        btn.textContent = 'Error';
        btn.style.color = '#ef4444';
        setTimeout(() => {
            btn.textContent = 'Delete';
            btn.style.color = '#ef4444';
            btn.disabled = false;
            btn.dataset.confirm = '';
        }, 2000);
    }
}

// Logout functionality
function logout() {
    fetch('/api/logout', { 
        method: 'POST', 
        credentials: 'include' 
    }).then(() => {
        window.location.href = '/app/index.html';
    }).catch(error => {
        console.error('Logout failed:', error);
        window.location.href = '/app/index.html';
    });
}