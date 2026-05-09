// Auto-extracted from reset_password.html inline scripts
// Generated for CSP compliance (Phase 5)

async function resetPassword(e) {
  e.preventDefault();
  
  const newPassword = document.getElementById('newPassword').value;
  const confirmPassword = document.getElementById('confirmPassword').value;
  
  if (newPassword !== confirmPassword) {
    showError('Passwords do not match');
    return;
  }
  
  if (newPassword.length < 6) {
    showError('Password must be at least 6 characters');
    return;
  }
  
  // Get token from URL
  const urlParams = new URLSearchParams(window.location.search);
  const token = urlParams.get('token');
  
  if (!token) {
    showError('Invalid reset link');
    return;
  }
  
  try {
    const res = await fetch('/api/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: token,
        new_password: newPassword
      })
    });

    if (res.ok) {
      showSuccess('Password reset successfully! Redirecting to login...');
      setTimeout(() => {
        window.location.href = '/app/index.html';
      }, 2000);
    } else {
      const error = await res.json();
      showError(error.detail || 'Reset link expired or invalid');
    }
  } catch (error) {
    console.error('Reset error:', error);
    showError('An error occurred');
  }
}
  


// Wire form submit via addEventListener (CSP-safe, replaces inline onsubmit)
document.addEventListener('DOMContentLoaded', function() {
  const form = document.getElementById('resetForm');
  if (form) form.addEventListener('submit', resetPassword);
});
