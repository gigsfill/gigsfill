/**
 * GigsFill Event Delegation Utility
 * ==================================
 * Replaces inline onclick/onchange handlers with centralized event delegation.
 * 
 * USAGE IN TEMPLATES:
 *   Before: onclick="showGigDetail(${gig.id})"
 *   After:  data-action="showGigDetail" data-id="${gig.id}"
 * 
 * REGISTER HANDLERS:
 *   GigsFill.on('showGigDetail', (el) => {
 *       const id = el.dataset.id;
 *       showGigDetail(parseInt(id));
 *   });
 * 
 * MODAL CLOSE (common pattern):
 *   Before: onclick="closeModal()"
 *   After:  data-action="closeModal"
 */

window.GigsFill = window.GigsFill || {};

(function() {
    'use strict';
    
    const handlers = {};
    
    /**
     * Register a click handler for a data-action value.
     * @param {string} action - The data-action attribute value
     * @param {function} fn - Handler receiving (element, event)
     */
    GigsFill.on = function(action, fn) {
        handlers[action] = fn;
    };
    
    /**
     * Register multiple handlers at once.
     * @param {Object} map - { actionName: handlerFn, ... }
     */
    GigsFill.onAll = function(map) {
        Object.assign(handlers, map);
    };
    
    // Global click delegation
    document.addEventListener('click', function(e) {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        
        const action = target.dataset.action;
        if (handlers[action]) {
            e.preventDefault();
            handlers[action](target, e);
        }
    });
    
    // Global change delegation for inputs/selects
    document.addEventListener('change', function(e) {
        const target = e.target.closest('[data-change]');
        if (!target) return;
        
        const action = target.dataset.change;
        if (handlers[action]) {
            handlers[action](target, e);
        }
    });
    
    // Common built-in actions (covers ~30 onclick patterns across the app)
    
    // Close modal: data-action="closeModal"
    GigsFill.on('closeModal', function(el) {
        if (typeof closeModal === 'function') closeModal();
    });
    
    // Close by removing closest positioned parent: data-action="dismissOverlay"
    GigsFill.on('dismissOverlay', function(el) {
        const overlay = el.closest('[style*="position"]');
        if (overlay) overlay.remove();
    });
    
    // Navigate: data-action="navigate" data-href="/app/..."
    GigsFill.on('navigate', function(el) {
        const href = el.dataset.href;
        if (href) window.location.href = href;
    });
    
    // Stop propagation only: data-action="stopProp"  
    GigsFill.on('stopProp', function(el, e) {
        e.stopPropagation();
    });
    
    // Hide modal overlay: data-action="hideOverlay" data-target="modalOverlay"
    GigsFill.on('hideOverlay', function(el) {
        const targetId = el.dataset.target;
        if (targetId) {
            const overlay = document.getElementById(targetId);
            if (overlay) overlay.classList.add('hidden');
        }
    });

})();
