// Auto-extracted from invited_user_declined.html inline scripts
// Generated for CSP compliance (Phase 5)

// Get token from URL
const params = new URLSearchParams(window.location.search);
const token = params.get('token');

// Process decline
async function processDecline() {
  if (!token) {
    showInvalidState();
    return;
  }
  
  try {
    const response = await fetch(`/api/invitations/${token}/decline`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    
    if (!response.ok) {
      // Try to get invitation details anyway
      const detailsResponse = await fetch(`/api/invitations/${token}`);
      if (detailsResponse.ok) {
        const details = await detailsResponse.json();
        if (details.status === 'declined') {
          // Already declined, show the card anyway
          showDeclineCard(details);
          return;
        }
      }
      showInvalidState();
      return;
    }
    
    const result = await response.json();
    showDeclineCard(result);
    
  } catch (error) {
    console.error('Error processing decline:', error);
    showInvalidState();
  }
}

function showInvalidState() {
  document.getElementById('loadingState').style.display = 'none';
  document.getElementById('invalidState').style.display = 'block';
}

function showDeclineCard(data) {
  document.getElementById('loadingState').style.display = 'none';
  document.getElementById('declineCard').style.display = 'block';
  
  const inviterName = `${data.inviter_first_name || ''} ${data.inviter_last_name || ''}`.trim() || 'the inviter';
  
  document.getElementById('inviterName').textContent = inviterName;
  document.getElementById('inviterName2').textContent = inviterName;
  document.getElementById('entityName').textContent = data.entity_name || 'Unknown';
  
  // Set entity profile link
  const entityLink = document.getElementById('entityLink');
  if (data.entity_type && data.entity_id) {
    if (data.entity_type === 'artist') {
      entityLink.href = `/app/artist-profile.html?artist_id=${data.entity_id}`;
    } else {
      entityLink.href = `/app/venue-profile.html?venue_id=${data.entity_id}`;
    }
  }
}

// Process on page load
document.addEventListener('DOMContentLoaded', processDecline);

