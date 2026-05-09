// CUSTOM MODAL SYSTEM FOR GIGSFILL

function showModal(title, message, buttons = [{ text: 'OK', onClick: null }]) {
  // Remove any existing modals
  const existing = document.querySelector('.modal-overlay');
  if (existing) existing.remove();
  
  // Create modal
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) closeModal(); };
  
  const modal = document.createElement('div');
  modal.className = 'modal-content';
  
  // Header
  const header = document.createElement('div');
  header.className = 'modal-header';
  header.innerHTML = `
    <div class="modal-title">${title}</div>
    <button class="modal-close" onclick="closeModal()">×</button>
  `;
  
  // Body
  const body = document.createElement('div');
  body.className = 'modal-body';
  body.innerHTML = message;
  
  // Footer
  const footer = document.createElement('div');
  footer.className = 'modal-footer';
  buttons.forEach(btn => {
    const button = document.createElement('button');
    button.className = btn.primary ? 'btn primary' : 'btn ghost';
    button.textContent = btn.text;
    button.onclick = () => {
      if (btn.onClick) btn.onClick();
      closeModal();
    };
    footer.appendChild(button);
  });
  
  modal.appendChild(header);
  modal.appendChild(body);
  modal.appendChild(footer);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
}

function closeModal() {
  const modal = document.querySelector('.modal-overlay');
  if (modal) {
    modal.style.opacity = '0';
    setTimeout(() => modal.remove(), 200);
  }
}

// Convenience functions
function showSuccess(message) {
  showModal('Success', message, [{ text: 'OK', primary: true }]);
}

function showError(message) {
  showModal('Error', message, [{ text: 'OK', primary: true }]);
}

function showConfirm(title, message, onConfirm) {
  showModal(title, message, [
    { text: 'Cancel', onClick: null },
    { text: 'Confirm', primary: true, onClick: onConfirm }
  ]);
}
