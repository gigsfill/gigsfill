// Service Worker Registration (shared across all pages)
// Aug 27 2026: also force-activate any waiting SW. Previously,
// updating sw.js just installed a new SW into "waiting" state — it
// wouldn't take over until every page controlled by the OLD SW was
// closed. A hard refresh does NOT count (the tab is still open with
// the old SW). Users hit by a bug in the old SW's fetch handler
// (e.g. respondWith(undefined) on cache-miss surfacing as
// TypeError: NetworkError on the page) had no way to recover without
// visiting DevTools. This block fixes that:
//   • send SKIP_WAITING to any already-waiting SW → it activates
//   • same for any that ARRIVES during this page's life (updatefound)
//   • when the controller swaps, reload the page ONCE so the fresh
//     SW controls this tab too (guarded so the reload can't loop)
if ("serviceWorker" in navigator) {
    let _swReloaded = false;
    navigator.serviceWorker.addEventListener('controllerchange', function() {
        if (_swReloaded) return;
        _swReloaded = true;
        window.location.reload();
    });
    navigator.serviceWorker.register("/sw.js", { scope: "/" })
        .then(function(reg) {
            // Nudge the browser to check for a new sw.js right now
            // (background check runs periodically otherwise).
            reg.update();

            function _promote(sw) {
                if (sw && sw.state === 'installed' && navigator.serviceWorker.controller) {
                    sw.postMessage({ type: 'SKIP_WAITING' });
                }
            }
            // An SW already waiting from a prior tab load
            _promote(reg.waiting);
            // A brand-new SW that appears while this tab is open
            reg.addEventListener('updatefound', function() {
                const nw = reg.installing;
                if (!nw) return;
                nw.addEventListener('statechange', function() {
                    _promote(nw);
                });
            });
        })
        .catch(function(e) {
            console.log("SW registration failed:", e);
        });
}
