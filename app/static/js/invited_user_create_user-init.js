// Auto-extracted from invited_user_create_user.html inline scripts
// Generated for CSP compliance (Phase 5)

// Get token from URL
const params = new URLSearchParams(window.location.search);
const token = params.get('token');

let invitationData = null;

// Load invitation details
async function loadInvitation() {
  if (!token) {
    showInvalidState();
    return;
  }
  
  try {
    const response = await fetch(`/api/invitations/${token}`);
    
    if (!response.ok) {
      showInvalidState();
      return;
    }
    
    invitationData = await response.json();
    
    if (invitationData.status !== 'pending') {
      showInvalidState();
      return;
    }
    
    // Show form
    document.getElementById('loadingState').style.display = 'none';
    document.getElementById('mainForm').style.display = 'block';
    
    // Populate data
    document.getElementById('entityNameDisplay').textContent = invitationData.entity_name;
    
    // Check if user already exists
    if (invitationData.user_exists) {
      // Show existing user card, hide new user form
      document.getElementById('newUserForm').style.display = 'none';
      document.getElementById('existingUserCard').style.display = 'block';
      document.getElementById('entityNameExisting').textContent = invitationData.entity_name;
      document.getElementById('welcomeText').innerHTML = `
        Good news! You already have a GigsFill account.<br><br>
        <span class="entity-name">${invitationData.invited_email}</span>
      `;
    } else {
      // Show new user form
      document.getElementById('email').value = invitationData.invited_email;
    }
    
  } catch (error) {
    console.error('Error loading invitation:', error);
    showInvalidState();
  }
}

// Accept invitation for existing user
async function acceptExistingUser() {
  hideError();
  
  try {
    const response = await fetch(`/api/invitations/${token}/accept-existing`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    
    const result = await response.json();
    
    if (!response.ok) {
      showError(result.detail || 'Failed to accept invitation');
      return;
    }
    
    // Show success modal
    const modal = document.createElement('div');
    modal.className = 'success-modal';
    modal.innerHTML = `
      <div class="success-modal-content">
        <div class="icon">✓</div>
        <h2>Access Granted!</h2>
        <p>You now have access to ${result.entity_name}.<br>Redirecting to login...</p>
      </div>
    `;
    document.body.appendChild(modal);
    
    // Redirect to login after 2 seconds
    setTimeout(() => {
      window.location.href = '/app/index.html';
    }, 2000);
    
  } catch (error) {
    console.error('Error accepting invitation:', error);
    showError('An error occurred. Please try again.');
  }
}

function showInvalidState() {
  document.getElementById('loadingState').style.display = 'none';
  document.getElementById('invalidState').style.display = 'block';
}

function showError(message) {
  const errorDiv = document.getElementById('errorMessage');
  errorDiv.textContent = message;
  errorDiv.style.display = 'block';
  window.scrollTo(0, 0);
}

function hideError() {
  document.getElementById('errorMessage').style.display = 'none';
}

// Phone formatting
function formatPhoneNumber(input) {
  let value = input.value.replace(/\D/g, "");
  if (value.length > 10) value = value.slice(0, 10);
  if (value.length >= 6) {
    input.value = `(${value.slice(0,3)}) ${value.slice(3,6)}-${value.slice(6)}`;
  } else if (value.length >= 3) {
    input.value = `(${value.slice(0,3)}) ${value.slice(3)}`;
  } else if (value.length > 0 && value.length < 3) {
    input.value = value;
  }
}

// Create account
async function createAccount() {
  hideError();
  
  const firstName = document.getElementById('firstName').value.trim();
  const lastName = document.getElementById('lastName').value.trim();
  const email = document.getElementById('email').value.trim();
  const phone = document.getElementById('phone').value.trim();
  const password = document.getElementById('password').value;
  
  // Validation
  if (!firstName || !lastName || !email || !password) {
    showError('Please fill in all required fields');
    return;
  }
  
  if (password.length < 6) {
    showError('Password must be at least 6 characters');
    return;
  }
  
  try {
    const response = await fetch(`/api/invitations/${token}/accept`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        first_name: firstName,
        last_name: lastName,
        email: email,
        phone: phone,
        password: password
      })
    });
    
    const result = await response.json();
    
    if (!response.ok) {
      showError(result.detail || 'Failed to create account');
      return;
    }
    
    // Show success modal
    const modal = document.createElement('div');
    modal.className = 'success-modal';
    modal.innerHTML = `
      <div class="success-modal-content">
        <div class="icon">✓</div>
        <h2>Account Created!</h2>
        <p>You now have access to ${result.entity_name}.<br>Redirecting to login...</p>
      </div>
    `;
    document.body.appendChild(modal);
    
    // Redirect to login after 2 seconds
    setTimeout(() => {
      window.location.href = '/app/index.html';
    }, 2000);
    
  } catch (error) {
    console.error('Error creating account:', error);
    showError('An error occurred. Please try again.');
  }
}

// Load on page load
document.addEventListener('DOMContentLoaded', loadInvitation);

