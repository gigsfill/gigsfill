/* ================================================================
   GigsFill Admin — Logs Viewer
   admin-logs.js
   ================================================================ */

(function() {
'use strict';

/* ── State ── */
let _logsData     = [];
let _logsOffset   = 0;
const LOGS_LIMIT  = 200;
let _logsLevel    = 'ALL';
let _logsSearch   = '';
let _logsAutoRefresh = null;
let _logsTotal    = 0;

/* ── Init (called when tab opens) ── */
window.initLogsTab = function() {
  if (document.getElementById('logsInitialized')) return;
  document.getElementById('logsInitialized').value = '1';
  fetchLogs(true);
};

/* ── Fetch ── */
window.fetchLogs = async function(reset) {
  if (reset) _logsOffset = 0;
  const level  = document.getElementById('logsLevelFilter')?.value || 'ALL';
  const search = (document.getElementById('logsSearch')?.value || '').trim();
  _logsLevel  = level;
  _logsSearch = search;

  const btn = document.getElementById('logsRefreshBtn');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ Loading…'; }

  try {
    const params = new URLSearchParams({
      level, search, limit: LOGS_LIMIT, offset: _logsOffset
    });
    const r = await fetch('/api/admin/logs?' + params, { credentials: 'include' });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    _logsData  = data.lines || [];
    _logsTotal = data.total || 0;
    renderLogs();
    updateLogsPagination();
  } catch(e) {
    const out = document.getElementById('logsOutput');
    if (out) out.innerHTML = `<div class="log-line log-error">Error fetching logs: ${e.message}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⟳ Refresh'; }
  }
};

/* ── Render log lines ── */
function renderLogs() {
  const out = document.getElementById('logsOutput');
  if (!out) return;

  if (!_logsData.length) {
    out.innerHTML = '<div style="color:var(--text-gray);text-align:center;padding:40px;font-size:0.85rem;">No log lines found matching current filters.</div>';
    return;
  }

  const html = _logsData.map(line => {
    const cls   = logLineClass(line);
    const esc   = line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const hilit = _logsSearch ? highlight(esc, _logsSearch) : esc;
    return `<div class="log-line ${cls}">${hilit}</div>`;
  }).join('');

  out.innerHTML = html;
}

function logLineClass(line) {
  const u = line.toUpperCase();
  if (u.includes('CRITICAL') || u.includes('⛔'))  return 'log-critical';
  if (u.includes('ERROR'))                          return 'log-error';
  if (u.includes('WARNING') || u.includes('WARN') || u.includes('⚠')) return 'log-warn';
  if (u.includes('DEBUG'))                          return 'log-debug';
  return 'log-info';
}

function highlight(text, term) {
  const re = new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'), 'gi');
  return text.replace(re, m => `<mark style="background:rgba(250,204,21,0.35);color:inherit;border-radius:2px;">${m}</mark>`);
}

/* ── Pagination ── */
function updateLogsPagination() {
  const info = document.getElementById('logsPageInfo');
  const prev = document.getElementById('logsPrevBtn');
  const next = document.getElementById('logsNextBtn');
  if (!info) return;
  const showing = _logsData.length;
  const from = _logsTotal ? _logsOffset + 1 : 0;
  const to   = _logsOffset + showing;
  info.textContent = `Showing ${from}–${to} of ${_logsTotal}`;
  if (prev) prev.disabled = _logsOffset === 0;
  if (next) next.disabled = (_logsOffset + showing) >= _logsTotal;
}

window.logsChangePage = function(dir) {
  _logsOffset = Math.max(0, _logsOffset + dir * LOGS_LIMIT);
  fetchLogs(false);
};

/* ── Auto-refresh ── */
window.toggleLogsAutoRefresh = function() {
  const btn = document.getElementById('logsAutoRefreshBtn');
  if (_logsAutoRefresh) {
    clearInterval(_logsAutoRefresh);
    _logsAutoRefresh = null;
    if (btn) { btn.textContent = '▶ Auto-Refresh'; btn.style.background = 'rgba(255,255,255,0.05)'; }
  } else {
    _logsAutoRefresh = setInterval(() => fetchLogs(true), 5000);
    if (btn) { btn.textContent = '⏸ Stop Auto-Refresh'; btn.style.background = 'rgba(6,182,212,0.2)'; }
    fetchLogs(true);
  }
};

/* ── Clear buffer ── */
window.clearLogBuffer = async function() {
  window._adminConfirm({
    title: '🗑 Clear Log Buffer',
    titleColor: '#f59e0b',
    body: 'Clear the in-memory log buffer?<br><br><span style="color:var(--text-muted);font-size:0.78rem;">Disk log files are not affected.</span>',
    cancelLabel: 'Cancel',
    confirmLabel: 'Clear Buffer',
    confirmColor: 'rgba(245,158,11,0.25)',
    onConfirm: async function() {
      try {
        await fetch('/api/admin/logs/clear', { method: 'DELETE', credentials: 'include' });
        fetchLogs(true);
      } catch(e) { window._adminToast('Error clearing logs: ' + e.message, 'rgba(239,68,68,0.8)'); }
    }
  });
};

/* ── Copy visible logs ── */
window.copyLogs = function() {
  const text = _logsData.join('\n');
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('logsCopyBtn');
    if (btn) { btn.textContent = '✓ Copied!'; setTimeout(() => btn.textContent = '📋 Copy', 1500); }
  });
};

/* ── Stop auto-refresh when leaving tab ── */
window.stopLogsAutoRefresh = function() {
  if (_logsAutoRefresh) {
    clearInterval(_logsAutoRefresh);
    _logsAutoRefresh = null;
    const btn = document.getElementById('logsAutoRefreshBtn');
    if (btn) { btn.textContent = '▶ Auto-Refresh'; btn.style.background = 'rgba(255,255,255,0.05)'; }
  }
};

})();
