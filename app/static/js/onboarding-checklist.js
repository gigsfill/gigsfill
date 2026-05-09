/**
 * Onboarding Checklist — Setup Walk-Through Modal
 * ================================================
 * Include on both venue-create-gigs.html and artist-book-gigs.html
 * Shows a to-do popup until all setup tasks are completed.
 */

(function() {
    'use strict';
  
    // ── Detect page context ──────────────────────────────────────────
    const params = new URLSearchParams(window.location.search);
    const venueId  = params.get('venue_id');
    const artistId = params.get('artist_id');
  
    if (!venueId && !artistId) return;
  
    const entityType = venueId ? 'venue' : 'artist';
    const entityId   = venueId || artistId;
  
    // ── Task navigation config ───────────────────────────────────────
    // Each task maps to a tab switch (same page) or visit_only (mark complete on click)
  
    const VENUE_NAV = {
      email_notifications: {
        action: 'tab',
        tab: 'emailcenter',
        subtab: { fn: 'switchEmailSubTab', name: 'notifications' }
      },
      payments: {
        action: 'tab',
        tab: 'payments'
      },
      contract_settings: {
        action: 'tab',
        tab: 'taxes',
        subtab: { fn: 'switchLegalSubtab', name: 'contractSettings' }
      },
      tax_settings: {
        action: 'tab',
        tab: 'taxes',
        subtab: { fn: 'switchLegalSubtab', name: 'taxSettings' }
      },
      edit_profile: {
        action: 'url',
        url: `/app/venue-edit.html?venue_id=${entityId}`
      }
    };
  
    const ARTIST_NAV = {
      payments: {
        action: 'tab',
        tab: 'payments'
      },
      tax_info: {
        action: 'tab',
        tab: 'taxes',
        subtab: { fn: 'switchArtistLegalSubtab', name: 'artistTaxInfo' }
      },
      edit_profile: {
        action: 'url',
        url: `/app/artist-edit.html?artist_id=${entityId}`
      }
    };
  
    const NAV_CONFIG = entityType === 'venue' ? VENUE_NAV : ARTIST_NAV;
  
    // ── State ────────────────────────────────────────────────────────
    let checklistData = null;
    let modalEl = null;
  
    // ── Fetch checklist status ───────────────────────────────────────
    async function fetchChecklist() {
      try {
        const res = await fetch(`/api/onboarding/${entityType}/${entityId}`, { credentials: 'include' });
        if (!res.ok) return null;
        return await res.json();
      } catch (e) {
        console.error('[Onboarding] Fetch error:', e);
        return null;
      }
    }
  
    // ── Mark visit-based task as complete ────────────────────────────
    async function markVisited(taskKey) {
      try {
        await fetch(`/api/onboarding/${entityType}/${entityId}/${taskKey}/visit`, {
          method: 'POST', credentials: 'include'
        });
      } catch (e) {
        console.error('[Onboarding] Visit mark error:', e);
      }
    }
  
    // ── Build & show modal ───────────────────────────────────────────
    function showModal(data) {
      if (modalEl) modalEl.remove();
  
      const completedCount = data.tasks.filter(t => t.completed).length;
      const totalCount = data.tasks.length;
      const entityLabel = entityType === 'venue' ? 'Venue' : 'Artist';
  
      const overlay = document.createElement('div');
      overlay.id = 'onboardingOverlay';
      overlay.style.cssText = `
        position: fixed; inset: 0; z-index: 99999;
        background: rgba(0,0,0,0.7); backdrop-filter: blur(4px);
        display: flex; align-items: center; justify-content: center;
        animation: obFadeIn 0.25s ease;
      `;
  
      const modal = document.createElement('div');
      modal.style.cssText = `
        background: #1a1f2e; border: 1px solid #2a3040; border-radius: 14px;
        width: 94%; max-width: 580px; max-height: 88vh; overflow: hidden;
        box-shadow: 0 20px 60px rgba(0,0,0,0.5); display: flex; flex-direction: column;
        animation: obSlideUp 0.3s ease;
      `;
  
      // ─ Header ─
      const header = document.createElement('div');
      header.style.cssText = `
        padding: 24px 28px 16px; border-bottom: 1px solid #2a3040;
        display: flex; align-items: flex-start; justify-content: space-between;
      `;
      header.innerHTML = `
        <div>
          <h2 style="margin:0 0 4px; font-size:20px; font-weight:700; color:#f1f5f9; letter-spacing:-0.01em;">
            ${entityLabel} Setup Checklist
          </h2>
          <p style="margin:0; font-size:13px; color:#64748b;">
            Complete the following items to begin using GigsFill...
          </p>
        </div>
        <button id="obClose" style="
          background: none; border: none; color: #64748b; font-size: 22px;
          cursor: pointer; padding: 0 0 0 12px; line-height: 1;
        " title="I'll do this later">&times;</button>
      `;
  
      // ─ Progress bar ─
      const progress = document.createElement('div');
      progress.style.cssText = 'padding: 0 28px 16px;';
      const pct = Math.round((completedCount / totalCount) * 100);
      progress.innerHTML = `
        <div style="display:flex; align-items:center; gap:10px; margin-top:12px;">
          <div style="flex:1; height:6px; background:#2a3040; border-radius:3px; overflow:hidden;">
            <div style="height:100%; width:${pct}%; background: linear-gradient(90deg, #22c55e, #10b981);
                 border-radius:3px; transition: width 0.5s ease;"></div>
          </div>
          <span style="font-size:12px; color:#64748b; white-space:nowrap;">${completedCount}/${totalCount} done</span>
        </div>
      `;
  
      // ─ Task list ─
      const list = document.createElement('div');
      list.style.cssText = 'padding: 8px 28px 20px; overflow-y: auto; flex: 1;';
  
      data.tasks.forEach((task, i) => {
        const row = document.createElement('div');
        row.className = 'ob-task-row';
        row.dataset.key = task.key;
        row.style.cssText = `
          display: flex; align-items: center; gap: 14px;
          padding: 14px 0; border-bottom: 1px solid rgba(42,48,64,0.6);
          cursor: ${task.completed ? 'default' : 'pointer'};
          transition: background 0.15s;
        `;
        if (!task.completed) {
          row.addEventListener('mouseenter', () => row.style.background = 'rgba(255,255,255,0.03)');
          row.addEventListener('mouseleave', () => row.style.background = 'none');
        }
  
        // Status badge
        const badge = document.createElement('div');
        if (task.completed) {
          badge.style.cssText = `
            min-width: 90px; padding: 5px 0; text-align: center; font-size: 11px;
            font-weight: 600; border-radius: 6px; letter-spacing: 0.02em;
            background: rgba(34,197,94,0.15); color: #22c55e; border: 1px solid rgba(34,197,94,0.3);
          `;
          badge.textContent = '✓ Completed';
        } else {
          badge.style.cssText = `
            min-width: 90px; padding: 5px 0; text-align: center; font-size: 11px;
            font-weight: 600; border-radius: 6px; letter-spacing: 0.02em;
            background: rgba(245,158,11,0.15); color: #f59e0b; border: 1px solid rgba(245,158,11,0.3);
          `;
          badge.textContent = 'Need To Do';
        }
  
        // Text content
        const content = document.createElement('div');
        content.style.cssText = 'flex: 1; min-width: 0;';
        content.innerHTML = `
          <div style="font-size:14px; font-weight:600; color:${task.completed ? '#64748b' : '#f1f5f9'};
               ${task.completed ? 'text-decoration: line-through; opacity: 0.6;' : ''}">
            ${i + 1}. ${task.title}
          </div>
          <div style="font-size:12.5px; color:#64748b; margin-top:3px; line-height:1.4;
               ${task.completed ? 'opacity: 0.5;' : ''}">
            ${task.description}
          </div>
        `;
  
        // Arrow for incomplete items
        const arrow = document.createElement('div');
        if (!task.completed) {
          arrow.style.cssText = 'color: #06b6d4; font-size: 16px; font-weight: 700; flex-shrink: 0;';
          arrow.textContent = '→';
        }
  
        row.appendChild(badge);
        row.appendChild(content);
        row.appendChild(arrow);
  
        // ─ Click handler ─
        if (!task.completed) {
          row.addEventListener('click', () => handleTaskClick(task));
        }
  
        list.appendChild(row);
      });
  
      // ─ CSS animations ─
      if (!document.getElementById('obStyles')) {
        const style = document.createElement('style');
        style.id = 'obStyles';
        style.textContent = `
          @keyframes obFadeIn { from { opacity: 0; } to { opacity: 1; } }
          @keyframes obSlideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        `;
        document.head.appendChild(style);
      }
  
      // ─ Assemble ─
      modal.appendChild(header);
      modal.appendChild(progress);
      modal.appendChild(list);
      overlay.appendChild(modal);
      document.body.appendChild(overlay);
      modalEl = overlay;
  
      // ─ Close handler (X button — temporarily hides, comes back on any click) ─
      document.getElementById('obClose').addEventListener('click', () => {
        closeModal();
        // Re-show on next click anywhere on the page
        function reshowOnClick() {
          document.removeEventListener('click', reshowOnClick);
          setTimeout(() => init(), 300);
        }
        setTimeout(() => {
          document.addEventListener('click', reshowOnClick, { once: true });
        }, 200);
      });
    }
  
  
    function closeModal() {
      if (modalEl) {
        modalEl.style.animation = 'obFadeIn 0.15s ease reverse';
        setTimeout(() => { if (modalEl) { modalEl.remove(); modalEl = null; } }, 150);
      }
    }
  
  
    // ── Handle task click ────────────────────────────────────────────
    async function handleTaskClick(task) {
      const nav = NAV_CONFIG[task.key];
      if (!nav) return;
  
      // Mark visit-based tasks as complete immediately
      if (!task.mandatory) {
        await markVisited(task.key);
      }
  
      closeModal();
  
      if (nav.action === 'tab') {
        // Switch to the correct tab on this page
        const tabBtn = document.querySelector(`.tab[onclick*="'${nav.tab}'"]`);
        if (tabBtn && typeof switchTab === 'function') {
          switchTab(nav.tab, tabBtn);
        }
  
        // Also switch subtab if specified
        if (nav.subtab) {
          setTimeout(() => {
            const subFn = window[nav.subtab.fn];
            if (typeof subFn === 'function') {
              const subBtn = document.querySelector(
                `.legal-subtab[onclick*="${nav.subtab.name}"], .ec-subtab[onclick*="${nav.subtab.name}"], .artist-legal-subtab[onclick*="${nav.subtab.name}"]`
              );
              if (subBtn) subFn(nav.subtab.name, subBtn);
            }
          }, 100);
        }
  
        // Re-show checklist when user switches back to calendar tab
        setupRecheck();
  
      } else if (nav.action === 'url') {
        // Navigate to another page — store return URL
        sessionStorage.setItem('onboarding_return', window.location.href);
        window.location.href = nav.url;
      }
    }
  
  
    // ── Re-check after switching tabs (for mandatory items) ──────────
    function setupRecheck() {
      const origSwitchTab = window.switchTab;
      if (typeof origSwitchTab === 'function') {
        window.switchTab = function(tabName, button) {
          origSwitchTab(tabName, button);
          // Restore original switchTab and re-show checklist
          window.switchTab = origSwitchTab;
          setTimeout(() => init(), 500);
        };
      }
    }
  
  
    // ── Return-to-checklist banner (shown on edit pages) ─────────────
    function showReturnBanner() {
      const returnUrl = sessionStorage.getItem('onboarding_return');
      if (!returnUrl) return;
      sessionStorage.removeItem('onboarding_return');
  
      const banner = document.createElement('div');
      banner.id = 'obReturnBanner';
      banner.style.cssText = `
        position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
        z-index: 99998; background: #1a1f2e; border: 1px solid #06b6d4;
        border-radius: 10px; padding: 12px 24px; display: flex; align-items: center; gap: 12px;
        box-shadow: 0 8px 30px rgba(0,0,0,0.4); animation: obSlideUp 0.3s ease;
        cursor: pointer;
      `;
      banner.innerHTML = `
        <span style="color:#06b6d4; font-size:18px;">←</span>
        <span style="color:#f1f5f9; font-size:14px; font-weight:500;">Return to Setup Checklist</span>
      `;
      banner.addEventListener('click', () => {
        window.location.href = returnUrl;
      });
      document.body.appendChild(banner);
  
      // Auto-dismiss after 15 seconds
      setTimeout(() => { if (banner.parentNode) banner.remove(); }, 15000);
    }
  
  
    // ── Init ─────────────────────────────────────────────────────────
    async function init() {
      // On edit pages, just show the return banner — don't show checklist
      const isEditPage = window.location.pathname.includes('-edit.html');
      if (isEditPage) {
        showReturnBanner();
        return;
      }
  
      const data = await fetchChecklist();
      if (!data || data.all_complete) return;
  
      checklistData = data;
  
      // Small delay to let page finish loading
      setTimeout(() => showModal(data), 600);
    }
  
    // Wait for DOM + page scripts to load
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => setTimeout(() => { if (!window._artistAccessDenied) init(); }, 300));
    } else {
      setTimeout(() => { if (!window._artistAccessDenied) init(); }, 300);
    }
  
  })();
  