// Service Worker Registration (shared across all pages)
// Forces update check on every page load
if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" })
        .then(function(reg) {
            // Force check for updates immediately
            reg.update();
        })
        .catch(function(e) {
            console.log("SW registration failed:", e);
        });
}
