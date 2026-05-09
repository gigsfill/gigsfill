/* ================================================================
   GigsFill Admin — Database Browser
   admin-db.js
   ================================================================ */

   (function() {
    'use strict';
    
    /* ── State ── */
    let _tables      = [];
    let _activeTable = null;
    let _schema      = [];       // [{name, type, pk, notnull}]
    let _rows        = [];
    let _page        = 1;
    let _pageSize    = 50;
    let _totalPages  = 1;
    let _totalRows   = 0;
    let _sortCol     = '';
    let _sortDir     = 'asc';
    let _search      = '';
    let _editRowId   = null;     // rowid of row currently being edited inline
    
    /* ── Public init ── */
    window.initDbTab = async function() {
      await loadTableList();
    };
    
    /* ── Load table list ── */
    async function loadTableList() {
      const sidebar = document.getElementById('dbTableList');
      if (!sidebar) return;
      try {
        const r = await fetch('/api/admin/db/tables', { credentials: 'include' });
        if (!r.ok) throw new Error(await r.text());
        _tables = await r.json();
    
        sidebar.innerHTML = _tables.map(t => `
          <div class="db-table-item" id="dbtab-${t.name}" onclick="selectDbTable('${t.name}')">
            <span class="dbt-name">${t.name}</span>
            <span class="dbt-count">${t.rows.toLocaleString()}</span>
          </div>
        `).join('');
    
        // Auto-select first table only on initial load
        if (_tables.length && !_activeTable) selectDbTable(_tables[0].name);
      } catch(e) {
        sidebar.innerHTML = `<div style="color:#f87171;padding:12px;font-size:0.8rem;">Error: ${e.message}</div>`;
      }
    }
    
    /* ── Select table ── */
    window.selectDbTable = async function(name) {
      _activeTable = name;
      _page = 1;
      _sortCol = '';
      _sortDir = 'asc';
      _search  = '';
      _editRowId = null;
    
      // Highlight sidebar item
      document.querySelectorAll('.db-table-item').forEach(el => el.classList.remove('active'));
      const el = document.getElementById('dbtab-' + name);
      if (el) el.classList.add('active');
    
      // Clear search box
      const sb = document.getElementById('dbSearch');
      if (sb) sb.value = '';
    
      // Load schema
      try {
        const r = await fetch(`/api/admin/db/tables/${encodeURIComponent(name)}/schema`, { credentials: 'include' });
        if (!r.ok) throw new Error(await r.text());
        _schema = await r.json();
      } catch(e) {
        _schema = [];
      }
    
      await fetchTableRows();
    };
    
    /* ── Fetch rows ── */
    async function fetchTableRows() {
      if (!_activeTable) return;
      const container = document.getElementById('dbTableContainer');
      if (container) container.innerHTML = '<div style="color:var(--text-gray);padding:20px;text-align:center;">Loading…</div>';
    
      try {
        const params = new URLSearchParams({
          page: _page,
          page_size: _pageSize,
          sort_col: _sortCol,
          sort_dir: _sortDir,
          search: _search
        });
        const r = await fetch(`/api/admin/db/tables/${encodeURIComponent(_activeTable)}/rows?${params}`, { credentials: 'include' });
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        _rows       = data.rows || [];
        _totalPages = data.total_pages || 1;
        _totalRows  = data.total || 0;
        _page       = data.page || 1;
        renderTable(data.columns || []);
        renderDbPagination();
        updateDbTableTitle();
      } catch(e) {
        if (container) container.innerHTML = `<div style="color:#f87171;padding:20px;">Error: ${e.message}</div>`;
      }
    }
    
    /* ── Render table ── */
    function renderTable(columns) {
      const container = document.getElementById('dbTableContainer');
      if (!container) return;
    
      if (!_rows.length) {
        container.innerHTML = '<div style="color:var(--text-gray);text-align:center;padding:40px;font-size:0.85rem;">No rows found.</div>';
        return;
      }
    
      // Audit fix (May 2026): mirror the backend `_PROTECTED_TABLES` list so
      // the UI doesn't show Edit/Delete buttons that the API will 403 on.
      // Keep in sync with `routes/admin.py:_PROTECTED_TABLES`.
      const PROTECTED = [
        'users', 'platform_settings',
        'gigs', 'gig_slots', 'transactions', 'gig_contracts', 'flyers',
        'payment_cancellations', 'venue_payment_overrides', 'entity_payment_settings',
        'affiliate_referrals', 'affiliate_earnings', 'affiliate_payouts',
      ];
      const isProtected = PROTECTED.includes(_activeTable);
    
      // Build header
      let thead = '<thead><tr>';
      thead += '<th style="width:32px;">#</th>';
      columns.forEach(col => {
        const isSorted = _sortCol === col;
        const arrow = isSorted ? (_sortDir === 'asc' ? ' ▲' : ' ▼') : '';
        thead += `<th onclick="dbSortBy('${col}')" class="db-th-sortable" title="Sort by ${col}">${col}${arrow}</th>`;
      });
      thead += '<th style="width:90px;">Actions</th>';
      thead += '</tr></thead>';
    
      // Build rows
      let tbody = '<tbody>';
      _rows.forEach((row, rowIdx) => {
        // We use the first column as rowid proxy, or try 'id' column
        const idColIdx = columns.indexOf('id');
        const rowid    = idColIdx >= 0 ? row[idColIdx] : (rowIdx + 1 + (_page - 1) * _pageSize);
    
        const isEditing = _editRowId === rowid && !isProtected;
    
        tbody += `<tr id="dbrow-${rowid}" class="${isEditing ? 'db-row-editing' : ''}">`;
        tbody += `<td style="color:var(--text-gray);font-size:0.7rem;">${(_page - 1) * _pageSize + rowIdx + 1}</td>`;
    
        columns.forEach((col, ci) => {
          const val = row[ci];
          const display = val === null ? '<em style="color:#475569;">null</em>'
                        : String(val).length > 120 ? String(val).substring(0, 120) + '…'
                        : String(val);
    
          if (isEditing) {
            const isNull = val === null;
            const inputVal = isNull ? '' : String(val);
            tbody += `<td><input class="db-cell-input" data-col="${col}" value="${escAttr(inputVal)}" placeholder="${isNull ? 'null' : ''}"></td>`;
          } else {
            tbody += `<td title="${escAttr(String(val ?? ''))}">${display}</td>`;
          }
        });
    
        // Action buttons
        if (isEditing) {
          tbody += `<td>
            <button class="db-btn-save" onclick="dbSaveEdit(${rowid}, '${escAttr(JSON.stringify(columns))}')">💾 Save</button>
            <button class="db-btn-cancel" onclick="dbCancelEdit()">✕</button>
          </td>`;
        } else {
          tbody += `<td>`;
          if (!isProtected) {
            tbody += `<button class="db-btn-edit" onclick="dbStartEdit(${rowid})">✏️</button>`;
            tbody += `<button class="db-btn-delete" onclick="dbDeleteRow(${rowid})">🗑</button>`;
          }
          tbody += `</td>`;
        }
    
        tbody += '</tr>';
      });
      tbody += '</tbody>';
    
      // Add-row button row at bottom
      let addRow = '';
      if (!isProtected) {
        addRow = `
          <div style="margin-top:12px;">
            <button onclick="dbShowAddRow()" class="db-btn-add">＋ Add Row</button>
            <div id="dbAddRowForm" style="display:none;margin-top:12px;"></div>
          </div>`;
      }
    
      container.innerHTML = `
        <div style="overflow-x:auto;">
          <table class="db-data-table">
            ${thead}
            ${tbody}
          </table>
        </div>
        ${addRow}
      `;
    }
    
    /* ── Sort ── */
    window.dbSortBy = function(col) {
      if (_sortCol === col) {
        _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        _sortCol = col;
        _sortDir = 'asc';
      }
      _page = 1;
      fetchTableRows();
    };
    
    /* ── Search ── */
    window.dbDoSearch = function() {
      _search = (document.getElementById('dbSearch')?.value || '').trim();
      _page   = 1;
      fetchTableRows();
    };
    
    window.dbSearchKeydown = function(e) {
      if (e.key === 'Enter') dbDoSearch();
    };
    
    /* ── Pagination ── */
    function renderDbPagination() {
      const info = document.getElementById('dbPageInfo');
      const prev = document.getElementById('dbPrevBtn');
      const next = document.getElementById('dbNextBtn');
      const sz   = document.getElementById('dbPageSize');
      if (info) info.textContent = `Page ${_page} of ${_totalPages}  (${_totalRows.toLocaleString()} rows)`;
      if (prev) prev.disabled = _page <= 1;
      if (next) next.disabled = _page >= _totalPages;
      if (sz)   sz.value = _pageSize;
    }
    
    window.dbChangePage = function(dir) {
      _page = Math.max(1, Math.min(_totalPages, _page + dir));
      _editRowId = null;
      fetchTableRows();
    };
    
    window.dbChangePageSize = function(el) {
      _pageSize = parseInt(el.value) || 50;
      _page = 1;
      fetchTableRows();
    };
    
    function updateDbTableTitle() {
      const title = document.getElementById('dbActiveTableTitle');
      if (title) title.textContent = _activeTable || '';
      const sub = document.getElementById('dbActiveTableSub');
      if (sub) sub.textContent = `${_totalRows.toLocaleString()} rows`;
    }
    
    /* ── Inline Edit ── */
    window.dbStartEdit = function(rowid) {
      _editRowId = rowid;
      renderTable(_schema.map(c => c.name));
    };
    
    window.dbCancelEdit = function() {
      _editRowId = null;
      fetchTableRows();
    };
    
    window.dbSaveEdit = async function(rowid, colsJson) {
      const columns = JSON.parse(colsJson);
      const row = document.getElementById('dbrow-' + rowid);
      if (!row) return;
    
      const inputs = row.querySelectorAll('.db-cell-input');
      const updates = {};
      inputs.forEach(inp => {
        const col = inp.dataset.col;
        updates[col] = inp.value === '' ? null : inp.value;
      });
    
      try {
        const r = await fetch(`/api/admin/db/tables/${encodeURIComponent(_activeTable)}/rows/${rowid}`, {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updates)
        });
        if (!r.ok) throw new Error(await r.text());
        _editRowId = null;
        showDbToast('Row updated ✓');
        fetchTableRows();
      } catch(e) {
        window._adminToast('Save failed: ' + e.message, 'rgba(239,68,68,0.8)');
      }
    };
    
    /* ── Delete ── */
    window.dbDeleteRow = async function(rowid) {
      window._adminConfirm({
        title: '🗑 Delete Row',
        titleColor: '#ef4444',
        body: 'Delete this row from <strong style="color:var(--text-white);">' + _activeTable + '</strong>?<br><br>This cannot be undone.',
        cancelLabel: 'Cancel',
        confirmLabel: 'Delete Row',
        confirmColor: 'rgba(239,68,68,0.25)',
        onConfirm: async function() {
          try {
            const r = await fetch('/api/admin/db/tables/' + encodeURIComponent(_activeTable) + '/rows/' + rowid, {
              method: 'DELETE',
              credentials: 'include'
            });
            if (!r.ok) throw new Error(await r.text());
            showDbToast('Row deleted');
            fetchTableRows();
            loadTableList();
          } catch(e) {
            window._adminToast('Delete failed: ' + e.message, 'rgba(239,68,68,0.8)');
          }
        }
      });
    };
    
    /* ── Add Row ── */
    window.dbShowAddRow = function() {
      const form = document.getElementById('dbAddRowForm');
      if (!form) return;
    
      const editableCols = _schema.filter(c => !c.pk);
      if (!editableCols.length) {
        form.innerHTML = '<p style="color:var(--text-gray);font-size:0.8rem;">No editable columns.</p>';
        form.style.display = '';
        return;
      }
    
      const fields = editableCols.map(c => `
        <div style="display:flex;flex-direction:column;gap:3px;">
          <label style="font-size:0.68rem;color:var(--text-gray);text-transform:uppercase;">${c.name} <span style="color:#475569;">${c.type}</span></label>
          <input class="db-add-input" data-col="${c.name}" placeholder="${c.notnull ? 'required' : 'optional'}"
                 style="padding:6px 8px;background:#0d1220;border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.82rem;">
        </div>
      `).join('');
    
      form.innerHTML = `
        <div style="padding:14px;background:rgba(6,182,212,0.06);border:1px solid rgba(6,182,212,0.2);border-radius:8px;">
          <div style="font-size:0.8rem;color:var(--cyan);font-weight:600;margin-bottom:12px;">New Row</div>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:12px;">
            ${fields}
          </div>
          <div style="display:flex;gap:8px;">
            <button onclick="dbSubmitAddRow()" class="db-btn-save" style="padding:6px 16px;">＋ Insert</button>
            <button onclick="document.getElementById('dbAddRowForm').style.display='none'" class="db-btn-cancel" style="padding:6px 12px;">Cancel</button>
          </div>
        </div>
      `;
      form.style.display = '';
    };
    
    window.dbSubmitAddRow = async function() {
      const form = document.getElementById('dbAddRowForm');
      const inputs = form.querySelectorAll('.db-add-input');
      const data = {};
      inputs.forEach(inp => {
        data[inp.dataset.col] = inp.value === '' ? null : inp.value;
      });
    
      try {
        const r = await fetch(`/api/admin/db/tables/${encodeURIComponent(_activeTable)}/rows`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data)
        });
        if (!r.ok) throw new Error(await r.text());
        form.style.display = 'none';
        showDbToast('Row inserted ✓');
        fetchTableRows();
        loadTableList();
      } catch(e) {
        window._adminToast('Insert failed: ' + e.message, 'rgba(239,68,68,0.8)');
      }
    };
    
    /* ── Export CSV ── */
    window.dbExportCsv = function() {
      if (!_activeTable) return;
      window.open(`/api/admin/db/export/${encodeURIComponent(_activeTable)}`, '_blank');
    };
    
    /* ── Refresh ── */
    window.dbRefresh = async function() {
      const currentTable = _activeTable;
      await loadTableList();
      // Re-select the previously active table instead of defaulting to first
      if (currentTable && _tables.find(t => t.name === currentTable)) {
        selectDbTable(currentTable);
      }
    };
    
    /* ── Toast ── */
    function showDbToast(msg) {
      let t = document.getElementById('dbToast');
      if (!t) {
        t = document.createElement('div');
        t.id = 'dbToast';
        t.style.cssText = 'position:fixed;bottom:28px;right:28px;background:rgba(6,182,212,0.9);color:#000;font-weight:700;padding:10px 20px;border-radius:8px;font-size:0.85rem;z-index:9999;transition:opacity 0.4s;pointer-events:none;';
        document.body.appendChild(t);
      }
      t.textContent = msg;
      t.style.opacity = '1';
      clearTimeout(t._timer);
      t._timer = setTimeout(() => t.style.opacity = '0', 2200);
    }
    
    /* ── Helpers ── */
    function escAttr(s) {
      return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    
    })();
    