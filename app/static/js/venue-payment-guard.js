/**
 * Venue Payment Guard
 * Checks venue payment status on page load.
 * If suspended, shows a blocking modal that only allows access to Payments tab.
 */

(function() {
  const params = new URLSearchParams(window.location.search);
  const venueId = params.get("venue_id");
  if (!venueId) return;

  let suspensionActive = false;

  async function checkVenuePaymentStatus() {
    try {
      const res = await fetch(`/api/stripe/venue/${venueId}/payment-status`, { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();

      if (data.payment_status === 'suspended' || !data.has_card) {
        // Check if onboarding is still incomplete — if so, let the checklist handle it
        try {
          const obRes = await fetch(`/api/onboarding/venue/${venueId}`, { credentials: 'include' });
          if (obRes.ok) {
            const obData = await obRes.json();
            if (!obData.all_complete) {
              // Onboarding checklist will guide them — skip suspension modal
              window._venuePaymentStatus = data;
              return;
            }
          }
        } catch (e) {}

        showSuspensionModal(data);
        suspensionActive = true;
      } else {
        dismissSuspensionModal();
        suspensionActive = false;
      }

      // Store status globally for other scripts to check
      window._venuePaymentStatus = data;
    } catch (e) {
      console.error('[PaymentGuard] Status check error:', e);
    }
  }

  function showSuspensionModal(data) {
    // Remove any existing modal
    const existing = document.getElementById('venueSuspensionModal');
    if (existing) existing.remove();

    const hasBookedGigs = data.booked_gigs_affected > 0;
    const bookedMsg = hasBookedGigs
      ? `<div style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:14px;margin:16px 0;">
           <p style="color:#ef4444;margin:0;font-size:0.85rem;line-height:1.6;">
             <strong>⚠️ ${data.booked_gigs_affected} booked gig${data.booked_gigs_affected > 1 ? 's' : ''} affected.</strong>
             Artists with upcoming booked gigs have been notified that payment may not be processed.
           </p>
         </div>`
      : '';

    const reasonText = data.suspension_reason || 'No payment card on file';

    const modal = document.createElement('div');
    modal.id = 'venueSuspensionModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:100000;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = `
      <div style="background:linear-gradient(135deg,#1a1f2e 0%,#0f1419 100%);border:2px solid rgba(239,68,68,0.5);border-radius:16px;padding:32px;max-width:520px;width:95%;box-shadow:0 20px 60px rgba(0,0,0,0.5);text-align:center;">
        <div style="width:64px;height:64px;margin:0 auto 20px;background:rgba(239,68,68,0.15);border-radius:50%;display:flex;align-items:center;justify-content:center;">
          <span style="font-size:32px;">🚫</span>
        </div>
        <h2 style="color:#ef4444;font-size:1.3rem;font-weight:700;margin:0 0 8px;">Venue Suspended</h2>
        <p style="color:#9ca3af;font-size:0.9rem;margin:0 0 16px;line-height:1.6;">
          Your venue is suspended because: <strong style="color:#f87171;">${reasonText}</strong>
        </p>
        <div style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:16px;margin:0 0 16px;text-align:left;">
          <p style="color:#d1d5db;font-size:0.85rem;margin:0 0 8px;line-height:1.6;">While suspended:</p>
          <ul style="color:#9ca3af;font-size:0.85rem;margin:0;padding-left:20px;line-height:2;">
            <li>Your venue profile is <strong style="color:#f87171;">hidden</strong> from artists</li>
            <li>Your gigs are <strong style="color:#f87171;">not visible</strong> in search results</li>
            <li>You <strong style="color:#f87171;">cannot create</strong> new gigs</li>
          </ul>
        </div>
        ${bookedMsg}
        <p style="color:#9ca3af;font-size:0.85rem;margin:0 0 20px;line-height:1.6;">
          Add a valid payment card to reactivate your venue immediately.
        </p>
        <button id="suspensionGoToPayments" style="
          padding:14px 32px;
          background:linear-gradient(135deg,#635bff 0%,#7c6bff 100%);
          color:white;border:none;border-radius:8px;font-size:0.95rem;font-weight:600;
          cursor:pointer;width:100%;
          box-shadow:0 4px 12px rgba(99,91,255,0.4);
        ">
          💳 Go to Payments Tab
        </button>
      </div>
    `;
    document.body.appendChild(modal);

    document.getElementById('suspensionGoToPayments').addEventListener('click', () => {
      // Click the Payments tab button
      const paymentsBtn = document.querySelector('[data-tab="payments"]') ||
                          document.querySelector('button[onclick*="payments"]');
      if (paymentsBtn) {
        paymentsBtn.click();
      }
      // Hide modal temporarily so they can interact with Payments tab
      modal.style.display = 'none';

      // Re-check status after they might add a card (poll every 3 seconds)
      const recheckInterval = setInterval(async () => {
        try {
          const res = await fetch(`/api/stripe/venue/${venueId}/payment-status`, { credentials: 'include' });
          if (!res.ok) return;
          const newData = await res.json();
          if (newData.payment_status === 'active' && newData.has_card) {
            clearInterval(recheckInterval);
            dismissSuspensionModal();
            suspensionActive = false;
            window._venuePaymentStatus = newData;
            // Show success
            showReactivationSuccess();
          }
        } catch (e) {}
      }, 3000);

      // Stop polling after 5 minutes
      setTimeout(() => clearInterval(recheckInterval), 300000);
    });
  }

  function dismissSuspensionModal() {
    const modal = document.getElementById('venueSuspensionModal');
    if (modal) modal.remove();
  }

  function showReactivationSuccess() {
    const toast = document.createElement('div');
    toast.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#10b981;color:white;padding:16px 32px;border-radius:10px;z-index:100001;font-weight:600;font-size:0.95rem;box-shadow:0 8px 24px rgba(16,185,129,0.4);';
    toast.textContent = '✅ Venue Reactivated! Your profile and gigs are now visible.';
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
  }

  // Also update card removal to warn about suspension
  window._originalVenueRemoveCard = window.venueRemoveCard;
  window.venueRemoveCard = function() {
    // Check how many booked gigs
    fetch(`/api/stripe/venue/${venueId}/payment-status`, { credentials: 'include' })
      .then(r => r.json())
      .then(data => {
        const bookedCount = data.booked_gigs_affected || 0;
        let warningMsg = "Removing your card will <strong style='color:#ef4444;'>suspend your venue</strong>. Your profile and gigs will be hidden from artists.";
        if (bookedCount > 0) {
          warningMsg += `<br><br><strong style="color:#ef4444;">⚠️ ${bookedCount} booked gig${bookedCount > 1 ? 's' : ''} will be affected.</strong> Artists will be notified of the payment issue.`;
        }
        warningMsg += "<br><br>Are you sure you want to remove your card?";

        // Show styled confirm
        const existingModal = document.getElementById('paymentModal');
        if (existingModal) existingModal.remove();

        const modal = document.createElement('div');
        modal.id = 'paymentModal';
        modal.innerHTML = `
          <div style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;">
            <div style="background:#1a1f2e;border:1px solid rgba(239,68,68,0.4);border-radius:12px;padding:28px;max-width:480px;width:90%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.5);">
              <h3 style="color:#ef4444;font-size:1rem;font-weight:700;margin:0 0 12px 0;">🚫 Remove Card & Suspend Venue?</h3>
              <p style="color:#9ca3af;font-size:0.85rem;line-height:1.6;margin:0 0 20px 0;">${warningMsg}</p>
              <div style="display:flex;gap:10px;justify-content:center;">
                <button onclick="this.closest('#paymentModal').remove()" style="padding:10px 24px;background:transparent;color:#9ca3af;border:1px solid #333;border-radius:6px;font-size:0.85rem;cursor:pointer;">Keep Card</button>
                <button id="confirmRemoveCardBtn" style="padding:10px 24px;background:#ef4444;color:white;border:none;border-radius:6px;font-size:0.85rem;font-weight:600;cursor:pointer;">Remove Card</button>
              </div>
            </div>
          </div>`;
        document.body.appendChild(modal);

        document.getElementById('confirmRemoveCardBtn').onclick = async function() {
          modal.remove();
          // Proceed with original removal
          const res = await fetch(`/api/stripe/venue/${venueId}/payment-method`, {
            method: 'DELETE', credentials: 'include'
          });
          if (res.ok) {
            // Reload card display
            if (typeof loadVenueCard === 'function') loadVenueCard();
            // Check status again (will show suspension modal)
            setTimeout(checkVenuePaymentStatus, 500);
          }
        };
      })
      .catch(() => {
        // Fallback to original
        if (window._originalVenueRemoveCard) window._originalVenueRemoveCard();
      });
  };

  // Run on page load (with small delay to let DOM settle)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(checkVenuePaymentStatus, 500));
  } else {
    setTimeout(checkVenuePaymentStatus, 500);
  }

  // Export for other scripts
  window.checkVenuePaymentStatus = checkVenuePaymentStatus;
  window.isVenueSuspended = () => suspensionActive;
})();
