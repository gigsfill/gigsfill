// CUSTOM MODAL SYSTEM FOR GIGSFILL

// Jul 2026 audit fix (F-M6): showModal used to interpolate title + message
// into innerHTML raw. Callers like `showError(err.detail)` inject
// backend exception strings that may contain user input — real XSS
// surface. Now: showModal() escapes both by default; showModalHTML()
// is the explicit opt-in when a caller has already sanitized markup.
function _modalEscape(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showModal(title, message, buttons = [{ text: 'OK', onClick: null }]) {
  return _renderModal(_modalEscape(title), _modalEscape(message).replace(/\n/g, '<br>'), buttons);
}

function showModalHTML(title, messageHtml, buttons = [{ text: 'OK', onClick: null }]) {
  return _renderModal(_modalEscape(title), messageHtml, buttons);
}

function _renderModal(titleHtml, bodyHtml, buttons) {
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
    <div class="modal-title">${titleHtml}</div>
    <button class="modal-close" onclick="closeModal()">×</button>
  `;

  // Body
  const body = document.createElement('div');
  body.className = 'modal-body';
  body.innerHTML = bodyHtml;
  
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
