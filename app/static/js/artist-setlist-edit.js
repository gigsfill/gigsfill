/**
 * Setlist editor — artist-edit.html
 * ==================================
 * Renders inside #setlistEditRoot. Reads ?artist_id= from the URL (same
 * pattern as every other artist-edit control). Ships:
 *   • add-one row (title + original artist + Add)
 *   • "Paste bulk list" textarea → POST /bulk (parses each line by common
 *     separators server-side; handles numbered lists, quotes, en/em dashes)
 *   • list of songs with inline Edit / Delete + drag handle
 *   • drag-and-drop reorder using native HTML5 drag events (no library)
 *   • "Clear all" button behind a themed confirm
 *
 * Backend contract lives in backend/routes/setlist.py.
 */
(function () {
  'use strict';

  const _params = new URLSearchParams(window.location.search);
  const _artistId = parseInt(_params.get('artist_id') || '0', 10);
  if (!_artistId) return;

  let _songs = [];  // authoritative order from the server (or after local reorder)
  let _editingId = null;

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  async function _fetchSetlist() {
    const res = await fetch(`/api/artists/${_artistId}/setlist`, { credentials: 'include' });
    if (!res.ok) throw new Error('Failed to load setlist');
    const data = await res.json();
    _songs = (data.songs || []).slice()
      .sort((a, b) => (a.display_order || 0) - (b.display_order || 0));
  }

  function _render() {
    const root = document.getElementById('setlistEditRoot');
    if (!root) return;
    const jsA = window.jsAttr || JSON.stringify;

    root.innerHTML = `
      <!-- Bulk paste — pasting a copied list is the fastest way to seed a
           big setlist. Backend /bulk splits on tab, hyphen, em/en-dash,
           " by ", " | ", and "," so most formats work as-is. -->
      <div style="background:rgba(6,182,212,0.05);border:1px solid rgba(6,182,212,0.2);border-radius:8px;padding:12px 14px;margin-bottom:16px;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
          <strong style="font-size:0.85rem;color:var(--cyan);">Bulk paste</strong>
          <button id="setlistBulkToggle"
            style="background:none;border:none;color:var(--text-gray);font-size:0.75rem;cursor:pointer;text-decoration:underline;">Show / hide</button>
        </div>
        <div id="setlistBulkBody" style="display:none;">
          <p style="color:var(--text-gray);font-size:0.75rem;margin:6px 0 8px;line-height:1.5;">
            Paste one song per line. Accepted formats:
            <code style="color:var(--cyan);">Wonderwall - Oasis</code>,
            <code style="color:var(--cyan);">Wonderwall — Oasis</code>,
            <code style="color:var(--cyan);">Wonderwall by Oasis</code>,
            <code style="color:var(--cyan);">Wonderwall, Oasis</code>,
            tab-separated (from Excel), or just <code style="color:var(--cyan);">Wonderwall</code> (title only).
            Numbered lists (<code style="color:var(--cyan);">1. Wonderwall - Oasis</code>) work too.
          </p>
          <textarea id="setlistBulkText" rows="6" placeholder="Wonderwall - Oasis&#10;Sweet Caroline - Neil Diamond&#10;Purple Rain - Prince"
            style="width:100%;box-sizing:border-box;background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:6px;padding:8px 10px;font-size:0.82rem;font-family:monospace;resize:vertical;"></textarea>
          <div style="display:flex;align-items:center;gap:10px;margin-top:8px;">
            <button id="setlistBulkGo"
              style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.35);color:#10b981;border-radius:6px;padding:6px 14px;font-size:0.82rem;cursor:pointer;font-weight:600;">
              Parse & Add
            </button>
            <span id="setlistBulkStatus" style="font-size:0.78rem;color:var(--text-gray);"></span>
          </div>
        </div>
      </div>

      <!-- Add one -->
      <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-bottom:14px;">
        <div style="flex:2;min-width:180px;">
          <label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:3px;">Song title</label>
          <input type="text" id="setlistAddTitle" maxlength="200"
            style="width:100%;box-sizing:border-box;background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:6px;padding:6px 10px;font-size:0.85rem;">
        </div>
        <div style="flex:2;min-width:180px;">
          <label style="font-size:0.72rem;color:var(--text-gray);display:block;margin-bottom:3px;">Original artist (optional)</label>
          <input type="text" id="setlistAddArtist" maxlength="200"
            style="width:100%;box-sizing:border-box;background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:6px;padding:6px 10px;font-size:0.85rem;">
        </div>
        <button id="setlistAddBtn"
          style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.35);color:#10b981;border-radius:6px;padding:6px 16px;font-size:0.85rem;cursor:pointer;font-weight:600;">
          Add Song
        </button>
        <span id="setlistAddStatus" style="font-size:0.78rem;color:var(--text-gray);"></span>
      </div>

      <!-- List header -->
      <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 8px;border-bottom:1px solid var(--border);font-size:0.72rem;color:var(--text-gray);font-weight:600;">
        <span>${_songs.length} song${_songs.length === 1 ? '' : 's'} — drag ⋮⋮ to reorder</span>
        ${_songs.length > 0 ? `
          <button id="setlistClearAll"
            style="background:none;border:1px solid rgba(239,68,68,0.3);color:#ef4444;border-radius:4px;padding:3px 10px;font-size:0.72rem;cursor:pointer;">Clear all</button>
        ` : ''}
      </div>

      <!-- Rows -->
      <div id="setlistRows" style="max-height:600px;overflow-y:auto;">
        ${_songs.length === 0
          ? '<div style="text-align:center;padding:32px 12px;color:var(--text-gray);font-size:0.85rem;">No songs yet. Add one above or paste a bulk list.</div>'
          : _songs.map(_renderRow).join('')}
      </div>
    `;

    _bindHandlers();
  }

  function _renderRow(song) {
    const t = _esc(song.song_title);
    const a = _esc(song.original_artist || '');
    if (_editingId === song.id) {
      return `
        <div class="setlist-row" data-id="${song.id}" style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:rgba(6,182,212,0.05);border-bottom:1px solid rgba(255,255,255,0.05);">
          <input type="text" id="setlistEditTitle_${song.id}" value="${_esc(song.song_title)}" maxlength="200"
            style="flex:2;min-width:0;background:#151b28;border:1px solid #333;color:var(--text-white);border-radius:4px;padding:4px 8px;font-size:0.82rem;">
          <input type="text" id="setlistEditArtist_${song.id}" value="${_esc(song.original_artist || '')}" maxlength="200"
            placeholder="original artist"
            style="flex:2;min-width:0;background:#151b28;border:1px solid #333;color:var(--text-gray);border-radius:4px;padding:4px 8px;font-size:0.82rem;">
          <button data-setlist-save="${song.id}"
            style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.35);color:#10b981;border-radius:4px;padding:3px 10px;font-size:0.75rem;cursor:pointer;">Save</button>
          <button data-setlist-cancel="${song.id}"
            style="background:none;border:1px solid var(--border);color:var(--text-gray);border-radius:4px;padding:3px 10px;font-size:0.75rem;cursor:pointer;">Cancel</button>
        </div>
      `;
    }
    return `
      <div class="setlist-row" draggable="true" data-id="${song.id}"
        style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.05);cursor:move;">
        <span style="color:var(--text-muted);font-size:0.82rem;user-select:none;">⋮⋮</span>
        <div style="flex:1;min-width:0;font-weight:500;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${t}</div>
        <div style="flex:1;min-width:0;font-size:0.78rem;color:var(--text-gray);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${a}</div>
        <button data-setlist-edit="${song.id}"
          style="background:rgba(6,182,212,0.1);border:1px solid rgba(6,182,212,0.3);color:var(--cyan);border-radius:4px;padding:3px 10px;font-size:0.72rem;cursor:pointer;">Edit</button>
        <button data-setlist-delete="${song.id}"
          style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);color:#ef4444;border-radius:4px;padding:3px 10px;font-size:0.72rem;cursor:pointer;">Delete</button>
      </div>
    `;
  }

  function _bindHandlers() {
    const $ = id => document.getElementById(id);

    const bulkToggle = $('setlistBulkToggle');
    if (bulkToggle) {
      bulkToggle.addEventListener('click', () => {
        const body = $('setlistBulkBody');
        if (body) body.style.display = body.style.display === 'none' ? 'block' : 'none';
      });
    }

    const bulkGo = $('setlistBulkGo');
    if (bulkGo) {
      bulkGo.addEventListener('click', async () => {
        const ta = $('setlistBulkText');
        const status = $('setlistBulkStatus');
        const text = (ta?.value || '').trim();
        if (!text) { status.textContent = 'Paste something first.'; status.style.color = '#ef4444'; return; }
        status.textContent = 'Parsing…'; status.style.color = 'var(--text-gray)';
        bulkGo.disabled = true;
        try {
          const res = await fetch(`/api/artists/${_artistId}/setlist/bulk`, {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || 'Bulk add failed');
          status.textContent = `✓ Added ${data.added} song${data.added === 1 ? '' : 's'} (total ${data.total})${data.truncated_at_cap ? ' — hit the 1000-song cap' : ''}.`;
          status.style.color = '#10b981';
          if (ta) ta.value = '';
          await _fetchSetlist();
          _render();
        } catch (e) {
          status.textContent = '✗ ' + e.message; status.style.color = '#ef4444';
        } finally {
          bulkGo.disabled = false;
        }
      });
    }

    const addBtn = $('setlistAddBtn');
    if (addBtn) {
      addBtn.addEventListener('click', async () => {
        const t = ($('setlistAddTitle')?.value || '').trim();
        const a = ($('setlistAddArtist')?.value || '').trim();
        const status = $('setlistAddStatus');
        if (!t) { status.textContent = 'Enter a song title.'; status.style.color = '#ef4444'; return; }
        status.textContent = 'Adding…'; status.style.color = 'var(--text-gray)';
        try {
          const res = await fetch(`/api/artists/${_artistId}/setlist`, {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ song_title: t, original_artist: a }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || 'Add failed');
          $('setlistAddTitle').value = '';
          $('setlistAddArtist').value = '';
          status.textContent = '✓ Added.'; status.style.color = '#10b981';
          await _fetchSetlist();
          _render();
          // Refocus the title box for rapid-fire manual entry
          const again = document.getElementById('setlistAddTitle');
          if (again) again.focus();
        } catch (e) {
          status.textContent = '✗ ' + e.message; status.style.color = '#ef4444';
        }
      });
    }

    // Delegated handlers for per-row buttons and drag reorder.
    const rows = $('setlistRows');
    if (rows) {
      rows.addEventListener('click', async (ev) => {
        const editBtn = ev.target.closest('[data-setlist-edit]');
        const delBtn  = ev.target.closest('[data-setlist-delete]');
        const saveBtn = ev.target.closest('[data-setlist-save]');
        const cancelBtn = ev.target.closest('[data-setlist-cancel]');
        if (editBtn) {
          _editingId = parseInt(editBtn.dataset.setlistEdit, 10);
          _render();
        } else if (cancelBtn) {
          _editingId = null;
          _render();
        } else if (saveBtn) {
          const id = parseInt(saveBtn.dataset.setlistSave, 10);
          const t = ($(`setlistEditTitle_${id}`)?.value || '').trim();
          const a = ($(`setlistEditArtist_${id}`)?.value || '').trim();
          if (!t) return;
          const res = await fetch(`/api/artists/${_artistId}/setlist/${id}`, {
            method: 'PUT', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ song_title: t, original_artist: a }),
          });
          if (res.ok) {
            _editingId = null;
            await _fetchSetlist();
            _render();
          }
        } else if (delBtn) {
          const id = parseInt(delBtn.dataset.setlistDelete, 10);
          const song = _songs.find(s => s.id === id);
          const label = song ? `"${song.song_title}"` : 'this song';
          const ok = await _confirm(`Delete ${label} from your setlist?`);
          if (!ok) return;
          const res = await fetch(`/api/artists/${_artistId}/setlist/${id}`, {
            method: 'DELETE', credentials: 'include',
          });
          if (res.ok) {
            await _fetchSetlist();
            _render();
          }
        }
      });

      // ── Drag-and-drop reorder ─────────────────────────────────────────
      let dragId = null;
      rows.addEventListener('dragstart', (ev) => {
        const row = ev.target.closest('.setlist-row[draggable="true"]');
        if (!row) return;
        dragId = parseInt(row.dataset.id, 10);
        row.style.opacity = '0.4';
        ev.dataTransfer.effectAllowed = 'move';
      });
      rows.addEventListener('dragend', (ev) => {
        const row = ev.target.closest('.setlist-row');
        if (row) row.style.opacity = '';
      });
      rows.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
      });
      rows.addEventListener('drop', async (ev) => {
        ev.preventDefault();
        if (dragId == null) return;
        const target = ev.target.closest('.setlist-row');
        if (!target) return;
        const targetId = parseInt(target.dataset.id, 10);
        if (dragId === targetId) return;
        // Reorder _songs locally, then persist the new order.
        const fromIdx = _songs.findIndex(s => s.id === dragId);
        const toIdx   = _songs.findIndex(s => s.id === targetId);
        if (fromIdx < 0 || toIdx < 0) return;
        const moved = _songs.splice(fromIdx, 1)[0];
        _songs.splice(toIdx, 0, moved);
        _render();
        // Fire-and-forget; we already show the new order.
        try {
          await fetch(`/api/artists/${_artistId}/setlist/reorder`, {
            method: 'PUT', credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: _songs.map(s => s.id) }),
          });
        } catch (_) { /* silent — next reload will re-fetch server truth */ }
        dragId = null;
      });
    }

    const clearBtn = $('setlistClearAll');
    if (clearBtn) {
      clearBtn.addEventListener('click', async () => {
        const ok = await _confirm(`Clear ALL ${_songs.length} songs from your setlist? This can't be undone.`);
        if (!ok) return;
        const res = await fetch(`/api/artists/${_artistId}/setlist`, {
          method: 'DELETE', credentials: 'include',
        });
        if (res.ok) {
          await _fetchSetlist();
          _render();
        }
      });
    }
  }

  // Themed confirm; falls back to window.confirm if gf-modals isn't loaded.
  function _confirm(msg) {
    if (typeof window.showConfirm === 'function') {
      return new Promise(resolve => {
        window.showConfirm('Setlist', msg, {
          confirmText: 'Delete',
          onConfirm: () => resolve(true),
          onCancel:  () => resolve(false),
        });
      });
    }
    return Promise.resolve(window.confirm(msg));
  }

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      await _fetchSetlist();
      _render();
    } catch (e) {
      const root = document.getElementById('setlistEditRoot');
      if (root) root.innerHTML = '<div style="color:#ef4444;font-size:0.85rem;">Couldn\'t load setlist.</div>';
    }
  });
})();
