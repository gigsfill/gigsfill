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

    // Phase 2 migration: was a 35-line inline-styled venue-suspended
    // modal. Now uses showStyledModal — auto-toned error via the
    // "Suspended" title keyword. Non-dismissible so the user must take
    // action (go to Payments tab) rather than just dismiss the warning.
    // When they click the action button: close the modal, switch to the
    // Payments tab, then start polling for a fresh card so we can show
    // the reactivation toast when one's added.
    const bodyHtml =
      `<p>Your venue is suspended because: <strong style="color:#f87171;">${reasonText}</strong></p>` +
      `<div class="gf-bubble" style="margin-top:14px;text-align:left;">` +
        `<p style="margin:0 0 6px;color:var(--text);"><strong>While suspended:</strong></p>` +
        `<ul style="margin:0;padding-left:18px;line-height:1.9;">` +
          `<li>Your venue profile is <strong style="color:#f87171;">hidden</strong> from artists</li>` +
          `<li>Your gigs are <strong style="color:#f87171;">not visible</strong> in search results</li>` +
          `<li>You <strong style="color:#f87171;">cannot create</strong> new gigs</li>` +
        `</ul>` +
      `</div>` +
      bookedMsg +
      `<p style="margin-top:14px;">Add a valid payment card to reactivate your venue immediately.</p>`;

    window.showStyledModal(
      '🚫 Venue Suspended',
      bodyHtml,
      [
        { text: '💳 Go to Payments Tab', style: 'danger',
          onClick: () => {
            // Switch to the Payments tab on the underlying page
            const paymentsBtn = document.querySelector('[data-tab="payments"]') ||
                                document.querySelector('button[onclick*="payments"]');
            if (paymentsBtn) paymentsBtn.click();

            // Poll for a fresh card every 3s; show reactivation toast when
            // one appears. Auto-stops after 5 min so we don't leak intervals.
            const recheckInterval = setInterval(async () => {
              try {
                const res = await fetch(`/api/stripe/venue/${venueId}/payment-status`, { credentials: 'include' });
                if (!res.ok) return;
                const newData = await res.json();
                if (newData.payment_status === 'active' && newData.has_card) {
                  clearInterval(recheckInterval);
                  suspensionActive = false;
                  window._venuePaymentStatus = newData;
                  showReactivationSuccess();
                }
              } catch (e) {}
            }, 3000);
            setTimeout(() => clearInterval(recheckInterval), 300000);
            // modal will close automatically (no `return false`)
          }
        },
      ],
      { dismissible: false }
    );
  }

  function dismissSuspensionModal() {
    if (typeof window.closeAllModals === 'function') window.closeAllModals();
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

        // Phase 2 migration: was inline-styled confirm with raw onclick
        // attributes. Now uses showStyledModal — auto-toned error via the
        // "Remove" and "Suspend" keywords in the title.
        window.showStyledModal(
          '🚫 Remove Card & Suspend Venue?',
          warningMsg,
          [
            { text: 'Keep Card', style: 'ghost' },
            { text: 'Remove Card', style: 'danger',
              onClick: async () => {
                const res = await fetch(`/api/stripe/venue/${venueId}/payment-method`, {
                  method: 'DELETE', credentials: 'include'
                });
                if (res.ok) {
                  if (typeof loadVenueCard === 'function') loadVenueCard();
                  setTimeout(checkVenuePaymentStatus, 500);
                }
              }
            },
          ]
        );
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
