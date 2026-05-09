// Auto-extracted from contract-sign.html inline scripts
// Generated for CSP compliance (Phase 5)

let contractData = null;
let gigId = null;
let contractId = null;

async function init() {
  const params = new URLSearchParams(window.location.search);
  gigId = params.get('gig_id');
  contractId = params.get('contract_id');
  
  if (!gigId && !contractId) {
    document.getElementById('loading').innerHTML = '<p style="color:#ef4444;">Missing contract reference.</p>';
    return;
  }
  
  try {
    let url = contractId ? `/api/gig-contracts/${contractId}` : `/api/gigs/${gigId}/contract`;
    const res = await fetch(url, { credentials: 'include' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load contract');
    }
    contractData = await res.json();
    contractId = contractData.id;
    renderContract();
  } catch (e) {
    document.getElementById('loading').innerHTML = `<p style="color:#ef4444;">${esc(e.message)}</p>`;
  }
}

function renderContract() {
  document.getElementById('loading').style.display = 'none';
  document.getElementById('contractContent').style.display = '';
  
  const title = contractData.template_name || contractData.contract_type || 'Contract';
  document.getElementById('contractTitle').textContent = title;
  
  const meta = [];
  if (contractData.venue_name) meta.push(contractData.venue_name);
  if (contractData.gig_date) {
    const dp = contractData.gig_date.split('-');
    if (dp.length === 3) {
      meta.push(new Date(parseInt(dp[0]), parseInt(dp[1]) - 1, parseInt(dp[2])).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }));
    } else {
      meta.push((() => { const _m = String(contractData.gig_date).match(/(\d{4})-(\d{2})-(\d{2})/); return _m ? new Date(parseInt(_m[1]), parseInt(_m[2])-1, parseInt(_m[3])) : new Date(); })().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }));
    }
  }
  document.getElementById('contractMeta').textContent = meta.join(' · ');
  
  // Check if already signed
  if (contractData.status === 'executed' || contractData.status === 'countersigned' || contractData.status === 'fully_signed') {
    document.getElementById('alreadySignedSection').style.display = '';
    document.getElementById('alreadySignedMsg').textContent = 'Contract fully executed!';
    if (contractData.contract_type !== 'pdf_upload') {
      const dl = document.getElementById('downloadSignedLink');
      dl.href = `/api/gig-contracts/${contractId}/download-pdf`;
      dl.style.display = 'inline';
    }
    return;
  }
  
  if (contractData.status === 'artist_signed') {
    if (contractData.is_venue_user) {
      // Venue user — show contract body + countersign form
      const body = contractData.rendered_body || contractData.contract_body || '';
      const artistSigName = contractData.artist_signature_name || 'Artist';
      const artistSigDate = contractData.artist_signature_date ? new Date(contractData.artist_signature_date).toLocaleDateString() : '';
      
      document.getElementById('alreadySignedSection').style.display = '';
      document.getElementById('alreadySignedSection').innerHTML = `
        <div style="margin-bottom:16px;">
          <div style="background:rgba(139,92,246,0.08); border:1px solid rgba(139,92,246,0.3); border-radius:8px; padding:14px; margin-bottom:16px;">
            <p style="color:#a78bfa; margin:0; font-size:0.85rem; line-height:1.5;">
              📋 <strong>Artist has signed</strong> — Please review the contract below and countersign to confirm the booking.
            </p>
          </div>
          ${body ? `<div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:20px; max-height:350px; overflow-y:auto; margin-bottom:16px; font-size:0.85rem; line-height:1.7; color:var(--text);">${body}</div>` : ''}
          <div style="background:rgba(34,197,94,0.08); border:1px solid rgba(34,197,94,0.2); border-radius:8px; padding:12px; margin-bottom:16px;">
            <p style="margin:0; font-size:0.8rem; color:#22c55e;">
              ✓ Artist signed by: <strong>${artistSigName}</strong>${artistSigDate ? ' on ' + artistSigDate : ''}
            </p>
          </div>
          <div style="border-top:1px solid rgba(255,255,255,0.1); padding-top:16px;">
            <label style="display:block; font-size:0.85rem; color:var(--text-muted); margin-bottom:6px; font-weight:600;">Your Full Legal Name (Venue Countersignature)</label>
            <input type="text" id="countersignName" placeholder="Type your full legal name" style="width:100%; padding:10px 14px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.15); border-radius:6px; color:#fff; font-size:0.9rem; box-sizing:border-box;">
            <div style="margin-top:12px; display:flex; align-items:center; gap:12px;">
              <button onclick="submitCountersign()" id="countersignBtn" class="btn primary" style="padding:10px 24px;">Countersign & Confirm Booking</button>
              <span id="countersignStatus" style="font-size:0.85rem;"></span>
            </div>
          </div>
        </div>
      `;
    } else {
      // Artist user — waiting for venue
      document.getElementById('alreadySignedSection').style.display = '';
      document.getElementById('alreadySignedMsg').textContent = 'Contract signed — awaiting venue countersignature.';
      if (contractData.contract_type !== 'pdf_upload') {
        const dl = document.getElementById('downloadSignedLink');
        dl.href = `/api/gig-contracts/${contractId}/download-pdf`;
        dl.style.display = 'inline';
      }
    }
    return;
  }
  
  if (contractData.contract_type === 'pdf_upload') {
    document.getElementById('pdfContractSection').style.display = '';
    if (contractData.pdf_file_path) {
      document.getElementById('pdfDownloadLink').href = contractData.pdf_file_path;
    }
    setupDragDrop();
  } else {
    document.getElementById('digitalContractSection').style.display = '';
    const body = contractData.rendered_body || contractData.contract_body || '';
    // Sanitize contract HTML: strip <script>, <iframe>, on* handlers, javascript: hrefs
    // while preserving legitimate rich text formatting (bold, lists, links, etc.)
    const bodyEl = document.getElementById('contractBodyHtml');
    const parser = new DOMParser();
    const doc = parser.parseFromString(body, 'text/html');
    // Remove dangerous elements
    ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button'].forEach(tag => {
      doc.querySelectorAll(tag).forEach(el => el.remove());
    });
    // Remove on* event handlers and javascript: hrefs from all elements
    doc.querySelectorAll('*').forEach(el => {
      Array.from(el.attributes).forEach(attr => {
        if (attr.name.startsWith('on') || (attr.name === 'href' && attr.value.trim().toLowerCase().startsWith('javascript:'))) {
          el.removeAttribute(attr.name);
        }
      });
    });
    bodyEl.innerHTML = doc.body.innerHTML ? doc.body.innerHTML.replace(/<script[\s\S]*?<\/script>/gi,'').replace(/\son\w+\s*=/gi,' data-removed=') : '';
  }
}

async function signContract() {
  const name = document.getElementById('signatureName').value.trim();
  const agreed = document.getElementById('signatureAgree').checked;
  
  if (!name) { alert('Please type your full name.'); return; }
  if (!agreed) { alert('Please agree to the terms.'); return; }
  
  const btn = document.getElementById('signBtn');
  btn.disabled = true;
  btn.textContent = 'Signing...';
  
  try {
    const res = await fetch(`/api/gig-contracts/${contractId}/sign`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signature_name: name })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to sign');
    }
    
    document.getElementById('signStatus').textContent = '✓ Signed successfully!';
    document.getElementById('signStatus').style.color = '#22c55e';
    setTimeout(() => { window.location.href = '/app/artist-book-gigs.html'; }, 1500);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Sign Contract';
    document.getElementById('signStatus').textContent = '✗ ' + e.message;
    document.getElementById('signStatus').style.color = '#ef4444';
  }
}

async function uploadSignedPdf() {
  const input = document.getElementById('signedPdfInput');
  if (!input || !input.files.length) return;
  
  const file = input.files[0];
  const formData = new FormData();
  formData.append('file', file);
  
  const statusEl = document.getElementById('pdfUploadStatus');
  statusEl.textContent = 'Uploading...';
  statusEl.style.color = 'var(--text-gray)';
  
  try {
    const res = await fetch(`/api/gig-contracts/${contractId}/upload-signed`, {
      method: 'POST',
      credentials: 'include',
      body: formData
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Upload failed');
    }
    
    statusEl.textContent = '✓ Signed contract uploaded!';
    statusEl.style.color = '#22c55e';
    setTimeout(() => { window.location.href = '/app/artist-book-gigs.html'; }, 1500);
  } catch (e) {
    statusEl.textContent = '✗ ' + e.message;
    statusEl.style.color = '#ef4444';
    input.value = '';
  }
}

function setupDragDrop() {
  const zone = document.getElementById('pdfDropZone');
  if (!zone) return;
  
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const files = e.dataTransfer.files;
    if (files.length > 0 && files[0].type === 'application/pdf') {
      document.getElementById('signedPdfInput').files = files;
      uploadSignedPdf();
    }
  });
}

async function downloadContract() {
  if (!contractData) return;
  if (contractData.contract_type === 'pdf_upload') {
    if (contractData.pdf_file_path) window.open(contractData.pdf_file_path, '_blank');
  } else {
    window.open(`/api/gig-contracts/${contractId}/download-pdf`, '_blank');
  }
}

async function submitCountersign() {
  const name = document.getElementById('countersignName').value.trim();
  if (!name) { alert('Please type your full legal name.'); return; }
  
  const btn = document.getElementById('countersignBtn');
  const status = document.getElementById('countersignStatus');
  btn.disabled = true;
  btn.textContent = 'Countersigning...';
  status.textContent = '';
  
  try {
    const res = await fetch(`/api/gig-contracts/${contractId}/countersign`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signature_name: name })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Countersign failed');
    }
    status.style.color = '#22c55e';
    status.textContent = '✓ Contract countersigned! Booking confirmed.';
    btn.style.display = 'none';
    setTimeout(() => { window.location.href = '/app/venue-create-gigs.html?venue_id=' + (contractData.venue_id || ''); }, 2000);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Countersign & Confirm Booking';
    status.style.color = '#ef4444';
    status.textContent = '✗ ' + e.message;
  }
}

init();

