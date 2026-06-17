/**
 * Flyer Editor — Fabric.js canvas-based gig flyer creator
 * Usage: window.flyerEditor.open(venueId, gigId)
 */
(function () {
  'use strict';

  let canvas = null;
  let venueId = null;
  let gigId = null;
  let currentFlyer = null;
  let gigInfo = null;
  let _activeTab = 'create';
  
  // Undo/Redo
  const undoStack = [];
  const redoStack = [];
  let _undoPaused = false;
  let _dirty = false;
  const MAX_UNDO = 40;

  const DEFAULT_BG_URL = '/app/static/img/flyer_default_bg.jpg';

  const SIZE_PRESETS = {
    instagram_post:   { w: 1080, h: 1350, label: 'Instagram Post (4:5)' },
    instagram_square: { w: 1080, h: 1080, label: 'Instagram Square (1:1)' },
    instagram_story:  { w: 1080, h: 1920, label: 'Instagram Story (9:16)' },
    facebook_event:   { w: 1920, h: 1080, label: 'Facebook Event (16:9)' },
  };
  let currentPreset = 'instagram_post';
  const CANVAS_DISPLAY_HEIGHT = 520;

  /* =========================================================
     STYLED MODAL (replaces browser prompt/confirm)
     ========================================================= */
  function feModal(opts) {
    // opts: { title, message, input, placeholder, value, confirmText, cancelText, danger }
    // Returns Promise: resolve(value) for prompt, resolve(true/false) for confirm
    return new Promise(resolve => {
      let existing = document.getElementById('feModalOverlay');
      if (existing) existing.remove();
      const ov = document.createElement('div');
      ov.id = 'feModalOverlay';
      ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:30000;display:flex;align-items:center;justify-content:center;';
      const confirmColor = opts.danger ? '#ef4444' : '#8b5cf6';
      // Audit fix (May 2026 part 7): escape title + message (textContent
      // via the existing `esc` helper) so a venue-controlled template/flyer
      // name containing `<img onerror=...>` doesn't execute when the delete
      // confirm modal opens.
      ov.innerHTML = `
        <div style="background:var(--card,#1a1f2e);border:1px solid var(--border,#2d3548);border-radius:12px;padding:24px;width:90%;max-width:400px;">
          <h3 style="margin:0 0 8px;font-size:1rem;color:#e2e8f0;">${esc(opts.title || 'Confirm')}</h3>
          ${opts.message ? `<p style="margin:0 0 16px;font-size:0.85rem;color:var(--text-gray,#94a3b8);white-space:pre-line;">${esc(opts.message)}</p>` : ''}
          ${opts.input ? `<input id="feModalInput" type="text" value="${esc(opts.value || '')}" placeholder="${esc(opts.placeholder || '')}"
            style="width:100%;padding:8px 12px;font-size:0.85rem;background:#151b28;border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#e2e8f0;margin-bottom:16px;box-sizing:border-box;" autofocus>` : ''}
          <div style="display:flex;justify-content:flex-end;gap:8px;">
            <button id="feModalCancel" style="padding:7px 16px;font-size:0.8rem;border-radius:6px;border:1px solid var(--border,#2d3548);background:transparent;color:var(--text-gray,#94a3b8);cursor:pointer;">${opts.cancelText || 'Cancel'}</button>
            <button id="feModalConfirm" style="padding:7px 16px;font-size:0.8rem;border-radius:6px;border:1px solid ${confirmColor};background:${confirmColor}22;color:${confirmColor};cursor:pointer;font-weight:600;">${opts.confirmText || 'OK'}</button>
          </div>
        </div>`;
      document.body.appendChild(ov);
      const inp = document.getElementById('feModalInput');
      if (inp) { inp.focus(); inp.select(); inp.addEventListener('keydown', e => { if (e.key === 'Enter') done(true); if (e.key === 'Escape') done(false); }); }
      function done(ok) {
        ov.remove();
        if (opts.input) resolve(ok ? (inp?.value || '') : null);
        else resolve(ok);
      }
      document.getElementById('feModalCancel').onclick = () => done(false);
      document.getElementById('feModalConfirm').onclick = () => done(true);
      ov.addEventListener('click', e => { if (e.target === ov) done(false); });
    });
  }

  /* =========================================================
     MODAL HTML
     ========================================================= */
  function buildModal() {
    if (document.getElementById('flyerEditorOverlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'flyerEditorOverlay';
    overlay.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:20000;align-items:center;justify-content:center;';
    overlay.innerHTML = `
<div id="flyerEditorModal" style="background:var(--card,#1a1f2e);border:1px solid var(--border,#2d3548);border-radius:14px;width:96vw;max-width:1200px;height:92vh;max-height:850px;display:flex;flex-direction:column;overflow:hidden;">
  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 20px;border-bottom:1px solid var(--border,#2d3548);flex-shrink:0;">
    <div style="display:flex;gap:4px;">
      <button id="flyerTabCreate" onclick="FE.switchTab('create')" class="fe-tab active">Create Flyer</button>
      <button id="flyerTabPrevious" onclick="FE.switchTab('previous')" class="fe-tab">Previous Flyers</button>
    </div>
    <div style="display:flex;align-items:center;gap:10px;">
      <span id="flyerGigLabel" style="font-size:0.72rem;color:var(--text-gray,#94a3b8);"></span>
      <button onclick="FE.close()" style="background:none;border:none;color:var(--text-gray,#94a3b8);font-size:1.4rem;cursor:pointer;padding:4px 8px;line-height:1;">&times;</button>
    </div>
  </div>

  <!-- CREATE TAB -->
  <div id="flyerCreateTab" style="flex:1;display:flex;overflow:hidden;flex-direction:column;">
    <!-- File Title Bar -->
    <div id="flyerTitleBar" style="padding:6px 16px;border-bottom:1px solid rgba(255,255,255,0.06);background:rgba(0,0,0,0.15);display:flex;align-items:center;gap:8px;flex-shrink:0;">
      <span id="flyerFileTitle" style="font-size:0.88rem;font-weight:600;color:#e2e8f0;">Default Template</span>
      <span id="flyerDirtyFlag" style="font-size:0.72rem;color:#f59e0b;display:none;">(Not Saved)</span>
      <div style="flex:1;"></div>
      <span style="font-size:0.72rem;color:#94a3b8;font-style:italic;">Template content auto-fills per gig (artist, date, time, logos)</span>
    </div>
    <div style="flex:1;display:flex;overflow:hidden;">
    <!-- Canvas -->
    <div style="flex:1;display:flex;align-items:center;justify-content:center;padding:12px;background:rgba(0,0,0,0.25);overflow:auto;min-width:0;position:relative;">
      <div id="flyerUndoBar" style="position:absolute;top:8px;left:12px;display:flex;gap:4px;z-index:5;">
        <button id="flyerUndoBtn" onclick="FE.undo()" title="Undo (Ctrl+Z)" style="width:30px;height:28px;border:1px solid rgba(255,255,255,0.15);border-radius:5px;background:rgba(30,35,50,0.85);color:#94a3b8;cursor:pointer;font-size:0.85rem;display:flex;align-items:center;justify-content:center;" disabled>↩</button>
        <button id="flyerRedoBtn" onclick="FE.redo()" title="Redo (Ctrl+Y)" style="width:30px;height:28px;border:1px solid rgba(255,255,255,0.15);border-radius:5px;background:rgba(30,35,50,0.85);color:#94a3b8;cursor:pointer;font-size:0.85rem;display:flex;align-items:center;justify-content:center;" disabled>↪</button>
      </div>
      <div style="position:absolute;top:8px;right:12px;z-index:5;">
        <div style="position:relative;display:inline-block;">
          <button id="flyerGigVarsBtn" onclick="FE.toggleGigVars()" style="padding:4px 10px;border:1px solid rgba(139,92,246,0.4);border-radius:5px;background:rgba(30,35,50,0.9);color:#c4b5fd;cursor:pointer;font-size:0.72rem;font-weight:500;">+ Gig Variables ▾</button>
          <div id="flyerGigVarsMenu" style="display:none;position:absolute;top:100%;right:0;margin-top:4px;background:var(--card,#1a1f2e);border:1px solid var(--border,#2d3548);border-radius:8px;padding:4px;min-width:160px;z-index:10;">
            <div onclick="FE.addGigVar('venue_logo')" class="fe-export-opt">🏠 Venue Logo</div>
            <div onclick="FE.addGigVar('artist_logo')" class="fe-export-opt">🎤 Artist Logo</div>
            <div style="height:1px;background:var(--border,#2d3548);margin:3px 0;"></div>
            <div onclick="FE.addGigVar('venue_name')" class="fe-export-opt">Venue Name</div>
            <div onclick="FE.addGigVar('artist_name')" class="fe-export-opt">Artist Name</div>
            <div onclick="FE.addGigVar('location')" class="fe-export-opt">Location</div>
            <div onclick="FE.addGigVar('date')" class="fe-export-opt">Date</div>
            <div onclick="FE.addGigVar('time')" class="fe-export-opt">Time</div>
          </div>
        </div>
      </div>
      <div id="flyerCanvasWrap" style="position:relative;box-shadow:0 4px 24px rgba(0,0,0,0.5);"><canvas id="flyerCanvas"></canvas></div>
    </div>
    <!-- Sidebar -->
    <div id="flyerSidebar" style="width:340px;flex-shrink:0;border-left:1px solid var(--border,#2d3548);display:flex;flex-direction:column;overflow-y:auto;overflow-x:hidden;">
      
      <!-- TEMPLATE-RELATED CLUSTER: visually grouped (subtle tint) so the
           default-template / saved-templates / previous-flyer choices read
           as one "where do I start from?" panel. Inside, the Default
           Template sits alone above a solid divider, then the two "load
           an existing flyer" methods sit together separated by — OR —. -->
      <div class="fe-template-group">

        <!-- DEFAULT TEMPLATE section: Auto-Create checkbox + site-default picker -->
        <div id="flyerSettingsSection" class="fe-section fe-template-default-section">
          <label style="display:flex;align-items:center;gap:8px;margin-bottom:8px;cursor:pointer;"
                 title="Unchecked means gigs will not have a flyer unless you specifically create one.">
            <input type="checkbox" id="flyerAutoCreate" style="accent-color:#8b5cf6;" onchange="FE.toggleAutoFlyers(this.checked)">
            <span style="font-size:0.72rem;color:var(--text-gray,#94a3b8);">Show a flyer on all booked gigs?</span>
          </label>
          <div id="flyerSettingsTemplateRow" style="display:none;">
            <div class="fe-label">Default Template</div>
            <div style="font-size:0.68rem;color:var(--text-gray,#64748b);margin-bottom:4px;">Which template to use for all gigs?</div>
            <select id="flyerSiteDefault" onchange="FE.setSiteDefault(this.value)" style="width:100%;" class="fe-select">
              <option value="">&#11088; Default Template</option>
            </select>
            <div style="font-size:0.62rem;color:var(--text-gray,#64748b);margin-top:4px;font-style:italic;">(Individually saved gig flyers always override this)</div>
          </div>
        </div>

        <!-- LOAD subgroup: two ways to load an existing flyer, joined by — OR — -->
        <div class="fe-template-load-block">

          <!-- LOAD STORED TEMPLATES -->
          <div class="fe-section fe-template-load-section">
            <div class="fe-label">Load Stored Templates</div>
            <select id="flyerTemplateSelect" onchange="FE.onTemplateSelect(this.value)" style="width:100%;" class="fe-select">
              <option value="">Load Template</option>
            </select>
            <!-- Visual thumbnail grid populated by renderTemplateThumbs()
                 after loadTemplateDropdown(). Lets the user browse stored
                 templates visually instead of guessing from names. -->
            <div id="flyerTemplateThumbs" style="margin-top:8px;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;"></div>
          </div>

          <div class="fe-or-separator">— — OR — —</div>

          <!-- LOAD TEMPLATE FROM PREVIOUS FLYER (venue mode only — hidden in admin mode) -->
          <div id="flyerPrevSection" class="fe-section fe-template-load-section fe-section-template-end">
            <div class="fe-label">Load Template From Previous Flyer</div>
            <select id="flyerRecentDropdown" onchange="FE.loadPrevious(this.value); this.value='';" class="fe-select" style="width:100%;margin-bottom:6px;">
              <option value="">— Recent Flyers —</option>
            </select>
            <!-- Mirrors the template thumbnail grid above — shows the 3
                 most recently saved previous flyers visually. -->
            <div id="flyerPrevThumbs" style="margin-bottom:6px;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;"></div>
            <input type="text" id="flyerPrevSearch" placeholder="Search by name, artist, date..." oninput="FE.searchPrevious()" class="fe-input" style="width:100%;margin-bottom:6px;">
            <div id="flyerPrevResults" style="max-height:120px;overflow-y:auto;border:1px solid rgba(255,255,255,0.06);border-radius:6px;background:rgba(0,0,0,0.15);"></div>
            <div style="font-size:0.62rem;color:var(--text-gray,#64748b);margin-top:4px;font-style:italic;">(Flyers stored for 1 year)</div>
          </div>

        </div>

      </div>


      <!-- CANVAS SIZE -->
      <div class="fe-section">
        <div class="fe-label">Canvas Size</div>
        <select id="flyerSizePreset" onchange="FE.changeSize(this.value)" style="width:100%;" class="fe-select">
          ${Object.entries(SIZE_PRESETS).map(([k,v])=>`<option value="${k}">${v.label}</option>`).join('')}
        </select>
      </div>

      <!-- BACKGROUND -->
      <div class="fe-section">
        <div class="fe-label">Background</div>
        <div style="display:flex;gap:4px;">
          <label class="fe-btn fe-btn-uniform" style="cursor:pointer;position:relative;overflow:hidden;">
            🎨 BG Color
            <input type="color" id="flyerBgColor" value="#1a1a2e" onchange="FE.setBgColor(this.value)" style="position:absolute;opacity:0;width:0;height:0;">
          </label>
          <button onclick="document.getElementById('flyerBgImgUpload').click()" class="fe-btn fe-btn-uniform">🖼 BG Image</button>
          <button onclick="FE.clearBgImage()" class="fe-btn fe-btn-uniform">✕ Clear BG</button>
        </div>
        <input type="file" id="flyerBgImgUpload" accept="image/*" style="display:none;" onchange="FE.handleBgUpload(this)">
      </div>

      <!-- BORDER -->
      <div class="fe-section">
        <div class="fe-label">Border</div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
          <input type="checkbox" id="flyerBorderEnabled" style="accent-color:#8b5cf6;" onchange="FE.toggleBorder(this.checked)">
          <span style="font-size:0.72rem;color:var(--text-gray,#94a3b8);">Add border around flyer</span>
        </div>
        <div id="flyerBorderControls" style="display:none;">
          <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
            <label style="font-size:0.72rem;color:var(--text-gray,#94a3b8);white-space:nowrap;">Color:</label>
            <label style="cursor:pointer;position:relative;overflow:hidden;flex:1;">
              <div id="flyerBorderColorSwatch" style="width:100%;height:26px;border-radius:5px;border:1px solid rgba(255,255,255,0.15);background:#ffffff;cursor:pointer;"></div>
              <input type="color" id="flyerBorderColor" value="#ffffff" oninput="FE.updateBorder()" style="position:absolute;opacity:0;width:0;height:0;">
            </label>
            <label style="font-size:0.72rem;color:var(--text-gray,#94a3b8);white-space:nowrap;">Thickness:</label>
            <input type="range" id="flyerBorderThickness" min="2" max="60" value="12" oninput="FE.updateBorder()" style="flex:1;accent-color:#8b5cf6;">
            <span id="flyerBorderThickLabel" style="font-size:0.72rem;color:#c4b5fd;min-width:26px;text-align:right;">12</span>
          </div>
        </div>
      </div>

      <!-- ADD ELEMENTS -->
      <div class="fe-section">
        <div class="fe-label">Add Elements</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">
          <button onclick="FE.addText()" class="fe-btn">T  Text</button>
          <button onclick="FE.addHeading()" class="fe-btn">H  Heading</button>
          <button onclick="document.getElementById('flyerImgUpload').click()" class="fe-btn">📷 Image</button>
          <button onclick="FE.addRect()" class="fe-btn">▬ Rectangle</button>
          <button onclick="FE.addCircle()" class="fe-btn">● Circle</button>
          <button onclick="FE.addLine()" class="fe-btn">— Line</button>
        </div>
        <input type="file" id="flyerImgUpload" accept="image/*" style="display:none;" onchange="FE.handleImageUpload(this)">
      </div>

      <!-- PROPERTIES -->
      <div id="flyerProps" class="fe-section" style="display:none;">
        <div class="fe-label">Properties</div>
        <div id="flyerTextProps" style="display:none;">
          <div style="display:flex;gap:4px;margin-bottom:5px;">
            <select id="flyerFontFamily" onchange="FE.setProp('fontFamily',this.value)" class="fe-select" style="flex:1;min-width:0;">
              <option value="Arial">Arial</option><option value="Arial Black">Arial Black</option>
              <option value="Georgia">Georgia</option><option value="Impact">Impact</option>
              <option value="Courier New">Courier New</option><option value="Trebuchet MS">Trebuchet MS</option>
              <option value="Verdana">Verdana</option><option value="Times New Roman">Times New Roman</option>
            </select>
            <input type="number" id="flyerFontSize" value="24" min="8" max="200" onchange="FE.setFontSize(this.value)" class="fe-input" style="width:48px;text-align:center;">
          </div>
          <div style="display:flex;gap:3px;margin-bottom:5px;">
            <button onclick="FE.toggleBold()" class="fe-btn fe-sq" id="flyerBoldBtn" style="font-weight:700;">B</button>
            <button onclick="FE.toggleItalic()" class="fe-btn fe-sq" id="flyerItalicBtn" style="font-style:italic;">I</button>
            <button onclick="FE.toggleUnderline()" class="fe-btn fe-sq" id="flyerUnderlineBtn" style="text-decoration:underline;">U</button>
            <div style="width:1px;background:rgba(255,255,255,0.08);margin:0 2px;"></div>
            <button onclick="FE.setAlign('left')" class="fe-btn fe-sq">⫷</button>
            <button onclick="FE.setAlign('center')" class="fe-btn fe-sq">☰</button>
            <button onclick="FE.setAlign('right')" class="fe-btn fe-sq">⫸</button>
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            <label class="fe-mini">Fill</label><input type="color" id="flyerTextColor" value="#ffffff" onchange="FE.setProp('fill',this.value)" class="fe-color">
            <label class="fe-mini">Stroke</label><input type="color" id="flyerTextStroke" value="#000000" onchange="FE.setTextStroke(this.value)" class="fe-color">
            <input type="number" id="flyerStrokeW" value="0" min="0" max="10" onchange="FE.setStrokeWidth(this.value)" class="fe-input" style="width:34px;text-align:center;" title="Stroke width">
          </div>
        </div>
        <div id="flyerShapeProps" style="display:none;">
          <div style="display:flex;gap:6px;align-items:center;margin-bottom:5px;">
            <label class="fe-mini">Fill</label><input type="color" id="flyerShapeFill" value="#8b5cf6" onchange="FE.setProp('fill',this.value)" class="fe-color">
            <label class="fe-mini">Border</label><input type="color" id="flyerShapeStroke" value="#ffffff" onchange="FE.setProp('stroke',this.value)" class="fe-color">
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            <label class="fe-mini">Opacity</label><input type="range" id="flyerOpacity" min="0" max="100" value="100" oninput="FE.setOpacity(this.value)" style="flex:1;">
            <span id="flyerOpacityVal" class="fe-mini" style="width:26px;text-align:right;">100</span>
          </div>
        </div>
        <div style="display:flex;gap:3px;margin-top:6px;">
          <button onclick="FE.copySelected()" class="fe-btn fe-sq" title="Copy (Ctrl+C)">📋</button>
          <button onclick="FE.cutSelected()" class="fe-btn fe-sq" title="Cut (Ctrl+X)">✂</button>
          <button onclick="FE.pasteClipboard()" class="fe-btn fe-sq" title="Paste (Ctrl+V)">📎</button>
          <span style="width:1px;background:rgba(255,255,255,0.1);margin:0 2px;"></span>
          <button onclick="FE.layer('bringForward')" class="fe-btn fe-sq" title="Forward">↑</button>
          <button onclick="FE.layer('sendBackwards')" class="fe-btn fe-sq" title="Backward">↓</button>
          <button onclick="FE.layer('bringToFront')" class="fe-btn fe-sq" title="Front">⤒</button>
          <button onclick="FE.layer('sendToBack')" class="fe-btn fe-sq" title="Back">⤓</button>
          <button onclick="FE.deleteSelected()" class="fe-btn fe-sq" style="color:#ef4444;margin-left:auto;" title="Delete">🗑</button>
        </div>
        <!-- Nudge / center row — moves the selected object by 10px in
             the chosen direction, or snaps it to the canvas center. -->
        <div style="display:flex;gap:3px;margin-top:6px;align-items:center;">
          <span class="fe-mini" style="margin-right:4px;">Move:</span>
          <button onclick="FE.nudgeSelected(-10, 0)" class="fe-btn fe-sq" title="Move left">←</button>
          <button onclick="FE.nudgeSelected(10, 0)"  class="fe-btn fe-sq" title="Move right">→</button>
          <button onclick="FE.nudgeSelected(0, -10)" class="fe-btn fe-sq" title="Move up">↑</button>
          <button onclick="FE.nudgeSelected(0, 10)"  class="fe-btn fe-sq" title="Move down">↓</button>
          <button onclick="FE.centerOnCanvas()" class="fe-btn fe-sq" title="Center on canvas">⊕</button>
        </div>
      </div>

      <!-- LAYERS -->
      <div class="fe-section">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
          <div class="fe-label" style="margin:0;">Layers</div>
          <button onclick="FE.refreshLayers()" style="font-size:0.65rem;padding:2px 6px;border:1px solid rgba(255,255,255,0.15);border-radius:4px;background:transparent;color:#94a3b8;cursor:pointer;">↻ Refresh</button>
        </div>
        <div id="flyerLayersList" style="max-height:200px;overflow-y:auto;border:1px solid rgba(255,255,255,0.06);border-radius:6px;background:rgba(0,0,0,0.15);"></div>
      </div>
    </div>
    </div>
  </div>

  <!-- PREVIOUS FLYERS TAB -->
  <div id="flyerPreviousTab" style="flex:1;display:none;flex-direction:column;overflow:hidden;">
    <div style="padding:10px 20px;border-bottom:1px solid var(--border,#2d3548);display:flex;gap:10px;align-items:center;">
      <input type="text" id="flyerPrevTabSearch" placeholder="Search flyers..." oninput="FE.filterPreviousTab()" class="fe-input" style="flex:1;max-width:300px;">
      <span id="flyerCount" style="font-size:0.72rem;color:var(--text-gray,#94a3b8);"></span>
    </div>
    <div id="flyerPreviousList" style="flex:1;overflow-y:auto;padding:10px 20px;"></div>
  </div>

  <!-- Footer -->
  <div id="flyerFooter" style="padding:8px 20px;border-top:1px solid var(--border,#2d3548);display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap;">
    <div style="position:relative;display:inline-block;">
      <button onclick="FE.toggleSaveMenu()" class="fe-action" style="--ac:139,92,246;">💾 Save ▾</button>
      <div id="flyerSaveMenu" style="display:none;position:absolute;bottom:100%;left:0;background:var(--card,#1a1f2e);border:1px solid var(--border,#2d3548);border-radius:8px;padding:4px;margin-bottom:4px;min-width:280px;z-index:10;white-space:nowrap;">
        <div id="flyerSaveGigItem" onclick="FE.save()" class="fe-export-opt">💾 Save Gig Flyer (this gig only)</div>
        <div style="height:1px;background:var(--border,#2d3548);margin:4px 0;"></div>
      <div id="flyerSaveDefaultItem" onclick="FE.saveAsDefaultTemplate()" class="fe-export-opt">⭐ Save as Default Template</div>
        <div id="flyerSaveAdminDefaultItem" onclick="FE.saveAsAdminDefault()" class="fe-export-opt" style="display:none;">⭐ Save as Site-Wide Default Template</div>
        <div style="height:1px;background:var(--border,#2d3548);margin:4px 0;"></div>
        <div id="flyerSaveNewItem" onclick="FE.saveAsNewTemplate()" class="fe-export-opt">📌 Save as New Template</div>
        <div id="flyerSaveNewAdminItem" onclick="FE.saveAsNewAdminTemplate()" class="fe-export-opt" style="display:none;">📌 Save as New Admin Template</div>
      </div>
    </div>
    <div style="position:relative;display:inline-block;">
      <button onclick="FE.toggleExport()" class="fe-action" style="--ac:34,197,94;">⬇ Export ▾</button>
      <div id="flyerExportMenu" style="display:none;position:absolute;bottom:100%;left:0;background:var(--card,#1a1f2e);border:1px solid var(--border,#2d3548);border-radius:8px;padding:4px;margin-bottom:4px;min-width:180px;z-index:10;">
        <div onclick="FE.exportAs('png')" class="fe-export-opt">PNG (Web / Social)</div>
        <div onclick="FE.exportAs('jpg')" class="fe-export-opt">JPG (Smaller File)</div>
        <div onclick="FE.exportAs('png-print')" class="fe-export-opt">PNG (Print Quality, 2×)</div>
        <div onclick="FE.exportAs('pdf')" class="fe-export-opt">PDF (Print Ready)</div>
      </div>
    </div>
    <button id="flyerDeleteTplBtn" onclick="FE.deleteCurrentTemplate()" class="fe-action" style="--ac:239,68,68;display:none;">🗑 Delete Template</button>
    <button id="flyerDeleteFlyerBtn" onclick="FE.deleteCurrentFlyer()" class="fe-action" style="--ac:239,68,68;display:none;">🗑 Delete Flyer</button>
    <span id="flyerGigOnlyBadge" style="display:none;font-size:0.68rem;font-weight:700;color:#f59e0b;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);border-radius:6px;padding:3px 8px;white-space:nowrap;">📌 Gig Flyer Active (Overrides Default Templates)</span>
    <div style="flex:1;"></div>
    <input type="file" id="flyerUploadOwn" accept="image/*" onchange="FE.uploadOwnFlyer(this)" style="display:none;">
    <button onclick="document.getElementById('flyerUploadOwn').click()" class="fe-action" style="--ac:59,130,246;" title="Upload your own flyer image">📤 Upload Flyer</button>
  </div>
</div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => {
      ['flyerExportMenu','flyerSaveMenu','flyerGigVarsMenu'].forEach(id => {
        const m = document.getElementById(id);
        if (m && !e.target.closest('#'+id) && !e.target.closest('[onclick*="toggle"]')) m.style.display = 'none';
      });
    });

    // Drag-and-drop image upload onto the canvas. The drop zone is the
    // canvas wrap's PARENT (so the user can drop on the surrounding
    // padding too, not only the canvas pixels). Visual cue: cyan glow
    // on dragover. Files that aren't images are silently ignored.
    const dropZone = document.querySelector('#flyerCanvasWrap')?.parentElement;
    if (dropZone) {
      const originalShadow = dropZone.style.boxShadow;
      const setHover = (on) => {
        dropZone.style.boxShadow = on
          ? 'inset 0 0 0 3px rgba(6,182,212,0.65)'
          : originalShadow || '';
      };
      ['dragenter', 'dragover'].forEach((ev) => {
        dropZone.addEventListener(ev, (e) => {
          if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return;
          e.preventDefault(); e.stopPropagation();
          setHover(true);
        });
      });
      ['dragleave', 'dragend'].forEach((ev) => {
        dropZone.addEventListener(ev, (e) => {
          // Only un-highlight when leaving the dropzone entirely, not on child transitions
          if (ev === 'dragleave' && dropZone.contains(e.relatedTarget)) return;
          setHover(false);
        });
      });
      dropZone.addEventListener('drop', (e) => {
        if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
        e.preventDefault(); e.stopPropagation();
        setHover(false);
        const files = Array.from(e.dataTransfer.files).filter(f => f.type && f.type.startsWith('image/'));
        files.forEach((f) => _addImageFile(f));
      });
    }
    if (!document.getElementById('flyerEditorCSS')) {
      const s = document.createElement('style'); s.id = 'flyerEditorCSS';
      s.textContent = `
        .fe-tab{padding:6px 14px;font-size:0.78rem;font-weight:600;border:1px solid var(--border,#2d3548);border-radius:6px;cursor:pointer;background:transparent;color:var(--text-gray,#94a3b8);transition:all .15s;}
        .fe-tab.active{background:rgba(139,92,246,0.2);color:#a78bfa;border-color:rgba(139,92,246,0.4);}
        .fe-section{padding:10px 14px;border-bottom:1px solid var(--border,#2d3548);}
        .fe-label{font-size:0.68rem;font-weight:700;color:var(--cyan,#06b6d4);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;}
        /* Visual cluster for the "where do I start from?" sections. The
           Default Template section sits alone above a solid divider, then
           the two "load existing flyer" methods sit together separated by
           a centered "— — OR — —" instead of a line. */
        .fe-template-group{
          background:linear-gradient(180deg, rgba(139,92,246,0.05) 0%, rgba(139,92,246,0.02) 100%);
          border-left:2px solid rgba(139,92,246,0.35);
          border-bottom:3px solid rgba(139,92,246,0.45);
          box-shadow:0 4px 14px rgba(0,0,0,0.18) inset;
        }
        /* Clear the default 2px white-translucent border-bottom on
           sections so we can control the dividers explicitly. */
        .fe-template-group .fe-section{border-bottom:none !important;margin-bottom:0;}
        /* Solid line under the Default Template section. */
        .fe-template-group .fe-template-default-section{
          border-bottom:1px solid rgba(139,92,246,0.45) !important;
        }
        /* The "load existing flyer" subgroup: stored templates + previous
           flyer feel like one block. No divider between them — just the OR. */
        .fe-template-load-block{padding:0;}
        .fe-template-load-section{padding-top:6px;padding-bottom:6px;}
        .fe-or-separator{
          text-align:left;
          padding-left:14px;
          font-size:0.68rem;
          font-weight:700;
          letter-spacing:.18em;
          color:var(--text-gray,#94a3b8);
          padding-top:4px;
          padding-bottom:6px;
          user-select:none;
        }
        .fe-btn{padding:5px 8px;font-size:0.72rem;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:5px;color:#cbd5e1;cursor:pointer;transition:all .12s;white-space:nowrap;text-align:center;}
        .fe-btn:hover{background:rgba(139,92,246,0.2);border-color:rgba(139,92,246,0.4);color:#e2d9f3;}
        .fe-btn.active{background:rgba(139,92,246,0.3);border-color:rgba(139,92,246,0.5);color:#c4b5fd;}
        .fe-btn-uniform{flex:1;min-width:0;justify-content:center;display:inline-flex;align-items:center;}
        .fe-sq{width:28px;min-width:28px;padding:4px 0;text-align:center;}
        .fe-select{font-size:0.75rem;padding:5px 8px;background:#151b28;border:1px solid rgba(255,255,255,0.12);border-radius:5px;color:#e2e8f0;appearance:auto;}
        .fe-select option:disabled{font-size:0.5rem;line-height:0.6;padding:1px 8px;color:#555;}
        #flyerUndoBtn:hover:not(:disabled),#flyerRedoBtn:hover:not(:disabled){background:rgba(99,91,255,0.3);color:#e2e8f0;}
        .fe-input{font-size:0.75rem;padding:5px 8px;background:#151b28;border:1px solid rgba(255,255,255,0.12);border-radius:5px;color:#e2e8f0;}
        .fe-color{width:28px;height:24px;border:none;cursor:pointer;border-radius:4px;padding:0;}
        .fe-mini{font-size:0.68rem;color:var(--text-gray,#94a3b8);}
        .fe-layer-row:hover{background:rgba(139,92,246,0.1);}
        .fe-layer-active{background:rgba(139,92,246,0.2)!important;}
        .fe-action{padding:5px 14px;font-size:0.75rem;font-weight:600;border:1px solid rgba(var(--ac),0.4);border-radius:6px;cursor:pointer;background:rgba(var(--ac),0.15);color:rgb(var(--ac));transition:all .12s;}
        .fe-action:hover{filter:brightness(1.25);}
        .fe-export-opt{padding:6px 12px;font-size:0.78rem;color:#e2e8f0;cursor:pointer;border-radius:4px;transition:background .1s;}
        .fe-export-opt:hover{background:rgba(255,255,255,0.05);}
        .fe-section{border-bottom:2px solid rgba(255,255,255,0.18);margin-bottom:2px;}
        .fe-section:last-child{border-bottom:none;}
        #flyerProps{background:rgba(139,92,246,0.06);border-left:2px solid rgba(139,92,246,0.5);margin-left:-2px;}
        #flyerProps .fe-label{color:#c4b5fd;}
        #flyerSidebar::-webkit-scrollbar{width:5px;}
        #flyerSidebar::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.08);border-radius:3px;}
        .fe-prev-item{padding:5px 8px;font-size:0.72rem;color:#cbd5e1;cursor:pointer;border-bottom:1px solid rgba(255,255,255,0.04);transition:background .1s;display:flex;justify-content:space-between;align-items:center;}
        .fe-prev-item:hover{background:rgba(139,92,246,0.1);}
      `;
      document.head.appendChild(s);
    }
  }

  /* =========================================================
     OPEN / CLOSE
     ========================================================= */
  async function open(vId, gId) {
    venueId = vId; gigId = gId; currentFlyer = null; gigInfo = null;
    buildModal();
    document.getElementById('flyerEditorOverlay').style.display = 'flex';
    if (typeof fabric === 'undefined')
      await loadScript('https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.3.1/fabric.min.js');

    // ── ADMIN MODE (venueId === 0) ──────────────────────────────────────────
    if (venueId === 0) {
      // Hide gig-specific and venue-specific UI
      const gigLabel = document.getElementById('flyerGigLabel');
      if (gigLabel) gigLabel.style.display = 'none';
      const settingsSection = document.getElementById('flyerSettingsSection');
      if (settingsSection) settingsSection.style.display = 'none';
      const prevSection = document.getElementById('flyerPrevSection');
      if (prevSection) prevSection.style.display = 'none';
      initCanvas();
      // Switch save menu to admin options
      const saveGig = document.getElementById('flyerSaveGigItem');
      const saveDef = document.getElementById('flyerSaveDefaultItem');
      const saveAdmDef = document.getElementById('flyerSaveAdminDefaultItem');
      const saveNew = document.getElementById('flyerSaveNewItem');
      const saveNewAdm = document.getElementById('flyerSaveNewAdminItem');
      if (saveGig) saveGig.style.display = 'none';
      if (saveDef) saveDef.style.display = 'none';
      if (saveAdmDef) saveAdmDef.style.display = '';
      if (saveNew) saveNew.style.display = 'none';
      if (saveNewAdm) saveNewAdm.style.display = '';
      // Load the site-wide Default Template if it exists
      try {
        const r = await fetch('/api/admin/flyers/default-template', {credentials:'include'});
        if (r.ok) {
          const tpl = await r.json();
          if (tpl.canvas_data && tpl.canvas_data !== '{}') {
            currentFlyer = tpl;
            loadCanvasData(tpl.canvas_data, false);
            setFileTitle('Default Template'); setStatus('"Default Template" loaded', '#67e8f9');
            setTimeout(() => markClean(), 150);
          } else { loadDefaultTemplate(); setFileTitle('Default Template'); markClean(); }
        } else { loadDefaultTemplate(); setFileTitle('Default Template'); markClean(); }
      } catch(e) { loadDefaultTemplate(); setFileTitle('Default Template'); markClean(); }
      loadAdminTemplateDropdown();
      switchTab('create');
      document.getElementById('flyerDeleteFlyerBtn').style.display = 'none';
      document.getElementById('flyerDeleteTplBtn').style.display = 'none';
      return;
    }
    // ── END ADMIN MODE ──────────────────────────────────────────────────────

    try {
      let r = await fetch(`/api/venues/${venueId}/flyers/gig-info/${gigId}`, {credentials:'include'});
      if (!r.ok) r = await fetch(`/api/gig-info-for-flyer/${gigId}`, {credentials:'include'});
      if (r.ok) gigInfo = await r.json();
    } catch(e) { 
      try { 
        const r2 = await fetch(`/api/gig-info-for-flyer/${gigId}`, {credentials:'include'});
        if (r2.ok) gigInfo = await r2.json();
      } catch(e2) { console.error('gig-info:', e2); }
    }

    // For multi-slot gigs: artist info lives on slots[], not the root.
    // Hoist the first booked slot's artist up to root so all downstream code works.
    if (gigInfo && !gigInfo.artist_picture_url && gigInfo.slots && gigInfo.slots.length > 0) {
      const firstBookedSlot = gigInfo.slots.find(s => s.artist_id) || gigInfo.slots[0];
      if (firstBookedSlot && firstBookedSlot.artist_id) {
        gigInfo.artist_id        = gigInfo.artist_id        || firstBookedSlot.artist_id;
        gigInfo.artist_name      = gigInfo.artist_name      || firstBookedSlot.artist_name;
        gigInfo.artist_picture_url = gigInfo.artist_picture_url || firstBookedSlot.artist_picture_url;
      }
    }
    console.log('[FlyerEditor] gigInfo:', JSON.stringify(gigInfo, null, 2));
    const lbl = document.getElementById('flyerGigLabel');
    if (lbl && gigInfo) {
      const d = gigInfo.date ? new Date(gigInfo.date+'T00:00:00').toLocaleDateString() : '';
      lbl.textContent = `${gigInfo.artist_name||'Open Gig'} · ${d}`;
    }
    // Update save button to show actual venue name e.g. "Save as 14 Cannons_Default Template"
    const vName = gigInfo?.venue_name || 'Venue';
    const saveDefItem = document.getElementById('flyerSaveDefaultItem');
    if (saveDefItem) saveDefItem.textContent = `⭐ Save as ${vName}_Default Template`;

    initCanvas();

    let loaded = false;
    let loadedTemplateId = null; // tracks which template is on canvas — passed to loadTemplateDropdown

    // Check existing flyer for this gig
    // A gig flyer record may be auto-created (canvas_data='{}') or user-saved (has real canvas_data).
    // Only treat it as "loaded" when the user has actually saved custom content.
    // Auto-created placeholders fall through to template loading so title stays as template name.
    try {
      const r = await fetch(`/api/gigs/${gigId}/flyer`, {credentials:'include'});
      if (r.ok) {
        const data = await r.json();
        if (data.exists) {
          let full = await fetch(`/api/venues/${venueId}/flyers/${data.id}`, {credentials:'include'});
          if (!full.ok) full = await fetch(`/api/flyers/${data.id}/detail`, {credentials:'include'});
          if (full.ok) {
            const flyerData = await full.json();
            const cd = flyerData.canvas_data;
            // Only count as "loaded" if user has saved real canvas content
            const hasUserContent = cd && cd !== '{}' && cd !== '{"objects":[]}' && cd !== '{"objects":[],"background":"#0a0a14"}';
            if (hasUserContent) {
              currentFlyer = flyerData;
              loadCanvasData(cd, true);
              loaded = true;
              setStatus(`"${currentFlyer.name || 'Untitled'}" loaded`, '#67e8f9');
              setFileTitle(currentFlyer.name || 'Untitled');
              setTimeout(() => markClean(), 150);  // after Fabric settles
              // Show badge — this is a gig-specific flyer overriding the template
              const badge = document.getElementById('flyerGigOnlyBadge');
              if (badge) badge.style.display = '';
            } else {
              // Auto-created placeholder — remember the record id for saving later
              // but don't treat it as a "loaded" flyer (fall through to template)
              currentFlyer = { id: flyerData.id, name: flyerData.name };
            }
          }
        }
      }
    } catch(e) {}

    if (!loaded) {
      // No saved gig flyer — load template based on venue settings:
      // If auto_flyers is ON: load the template chosen under "Which template to use for all gigs"
      //   (default_flyer_template_id if set, else VenueName_Default Template by name)
      // If auto_flyers is OFF: load site-wide Default Template as a starting point

      // Fetch venue settings once — need both auto_flyers and default_flyer_template_id
      let autoFlyers = false;
      let chosenTemplateId = null;
      try {
        const settR = await fetch(`/api/venues/${venueId}`, {credentials:'include'});
        if (settR.ok) {
          const vData = await settR.json();
          autoFlyers = !!(vData.auto_flyers);
          chosenTemplateId = vData.default_flyer_template_id || null;
          console.log('[FlyerEditor] venue settings: auto_flyers='+autoFlyers+' chosenTemplateId='+chosenTemplateId);
        } else {
          console.error('[FlyerEditor] venue settings fetch failed: HTTP '+settR.status);
        }
      } catch(e) { console.error('[FlyerEditor] venue settings load error:', e); }
      console.log('[FlyerEditor] template decision: autoFlyers='+autoFlyers+' chosenTemplateId='+chosenTemplateId);

      // Hide gig-only badge — no gig flyer found, showing template
      const _badge = document.getElementById('flyerGigOnlyBadge');
      if (_badge) _badge.style.display = 'none';

      if (autoFlyers && chosenTemplateId) {
        // auto_flyers ON + explicit template chosen — load that specific template
        try {
          let tplR = await fetch(`/api/venues/${venueId}/flyers/${chosenTemplateId}`, {credentials:'include'});
          if (!tplR.ok) tplR = await fetch(`/api/flyers/${chosenTemplateId}/detail`, {credentials:'include'});
          if (tplR.ok) {
            const tpl = await tplR.json();
            if (tpl.canvas_data && tpl.canvas_data !== '{}') {
              loadCanvasData(tpl.canvas_data, true);
              setStatus(`"${tpl.name || 'Default Template'}" loaded`, '#67e8f9');
              setFileTitle(tpl.name || 'Default Template');
              setTimeout(() => markClean(), 150);
              loadedTemplateId = String(chosenTemplateId);
            }
          }
        } catch(e) { console.error('chosen template load:', e); }
      }

      // NOTE: if auto_flyers=ON but chosenTemplateId=NULL, user explicitly chose "Default Template"
      // — fall straight through to site-wide Default Template, do NOT use VenueName_Default Template

      if (!loadedTemplateId) {
        // Load site-wide Default Template:
        // - auto_flyers OFF: use it as a blank starting point
        // - auto_flyers ON + chosenTemplateId NULL: user explicitly picked "Default Template"
        // - auto_flyers ON + chosenTemplateId set but load failed: fallback
        try {
          const siteR = await fetch('/api/flyers/site-default-template');
          if (siteR.ok) {
            const siteTpl = await siteR.json();
            if (siteTpl.canvas_data && siteTpl.canvas_data !== '{}') {
              loadCanvasData(siteTpl.canvas_data, true);
              setStatus('"Default Template" loaded', '#67e8f9');
              setFileTitle('Default Template');
              setTimeout(() => markClean(), 150);
              loadedTemplateId = '__default__';
            }
          }
        } catch(e) {}
      }

      if (!loadedTemplateId) {
        // Last resort: built-in JS template
        loadDefaultTemplate();
        setStatus('"Default Template" loaded', '#67e8f9');
        setFileTitle('Default Template');
        setTimeout(() => markClean(), 150);
        loadedTemplateId = '__default__';
      }

    }

    // Show/hide Delete Flyer button
    const delFlyerBtn = document.getElementById('flyerDeleteFlyerBtn');
    if (delFlyerBtn) delFlyerBtn.style.display = currentFlyer?.id ? '' : 'none';

    // Pass loadedTemplateId so dropdown shows the correct selection immediately after options load
    await loadTemplateDropdown(loadedTemplateId);
    loadFlyerSettings();
    loadRecentDropdown();
    switchTab('create');
    document.getElementById('flyerDeleteTplBtn').style.display = 'none';
  }

  async function close(force) {
    if (!force && _dirty) {
      // Show unsaved changes modal
      const choice = await new Promise(resolve => {
        const ov = document.createElement('div');
        ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:30000;display:flex;align-items:center;justify-content:center;';
        ov.innerHTML = `
          <div style="background:var(--card,#1a1f2e);border:1px solid var(--border,#2d3548);border-radius:12px;padding:24px;max-width:380px;width:90%;text-align:center;">
            <div style="font-size:1.1rem;font-weight:600;color:#e2e8f0;margin-bottom:12px;">Save Changes to Flyer?</div>
            <div style="font-size:0.82rem;color:#94a3b8;margin-bottom:20px;">You have unsaved changes that will be lost.</div>
            <div style="display:flex;flex-direction:column;gap:8px;">
              <button id="_cSaveGig" style="padding:8px;border-radius:6px;border:1px solid rgba(139,92,246,0.5);background:rgba(139,92,246,0.2);color:#c4b5fd;cursor:pointer;font-size:0.82rem;font-weight:500;">💾 Save Gig Flyer (this gig only)</button>
              <button id="_cSaveVenue" style="padding:8px;border-radius:6px;border:1px solid rgba(139,92,246,0.3);background:transparent;color:#a78bfa;cursor:pointer;font-size:0.82rem;">⭐ Save as ${gigInfo?.venue_name ? gigInfo.venue_name+'_Default Template' : 'Default Template'}</button>
              <button id="_cSaveTpl" style="padding:8px;border-radius:6px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#94a3b8;cursor:pointer;font-size:0.82rem;">📌 Save as New Template</button>
              <div style="height:1px;background:var(--border,#2d3548);margin:4px 0;"></div>
              <button id="_cDiscard" style="padding:8px;border-radius:6px;border:1px solid rgba(239,68,68,0.3);background:transparent;color:#f87171;cursor:pointer;font-size:0.82rem;">Discard Changes</button>
              <button id="_cCancel" style="padding:8px;border-radius:6px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#64748b;cursor:pointer;font-size:0.82rem;">Cancel</button>
            </div>
          </div>`;
        document.body.appendChild(ov);
        ov.querySelector('#_cSaveGig').onclick = () => { document.body.removeChild(ov); resolve('save'); };
        ov.querySelector('#_cSaveVenue').onclick = () => { document.body.removeChild(ov); resolve('venue'); };
        ov.querySelector('#_cSaveTpl').onclick = () => { document.body.removeChild(ov); resolve('template'); };
        ov.querySelector('#_cDiscard').onclick = () => { document.body.removeChild(ov); resolve('discard'); };
        ov.querySelector('#_cCancel').onclick = () => { document.body.removeChild(ov); resolve('cancel'); };
      });
      if (choice === 'cancel') return;
      if (choice === 'save') await save();
      else if (choice === 'venue') await saveAsDefaultTemplate();
      else if (choice === 'template') await saveAsNewTemplate();
      // 'discard' falls through to close
    }
    _dirty = false;
    document.getElementById('flyerEditorOverlay').style.display = 'none';
    if (canvas) { canvas.dispose(); canvas = null; }
  }

  function loadScript(src) {
    return new Promise((res, rej) => {
      if (document.querySelector(`script[src="${src}"]`)) return res();
      const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = rej;
      document.head.appendChild(s);
    });
  }

  /* =========================================================
     CANVAS
     ========================================================= */

  // Fabric.js with crossOrigin:'anonymous' on same-origin paths fails silently
  // because static servers don't return CORS headers for local uploads.
  // Only use crossOrigin for truly external URLs.
  function fabricLoadImage(url, callback) {
    if (!url) { callback(null); return; }
    const opts = url.startsWith('/') ? {} : { crossOrigin:'anonymous' };
    fabric.Image.fromURL(url, callback, opts);
  }

  function initCanvas() {
    const preset = SIZE_PRESETS[currentPreset];
    // Fit the canvas inside a fixed max-bounding-box so it never bursts
    // out of the modal column and never gets so big that 4:5 looks huge.
    // 520 max height keeps portrait/square at the same size they were
    // before; 600 max width keeps Facebook Event (16:9) on-screen.
    const MAX_H = 520;
    const MAX_W = 600;
    const scale = Math.min(MAX_H / preset.h, MAX_W / preset.w);
    const dw = Math.round(preset.w * scale);
    const dh = Math.round(preset.h * scale);
    if (canvas) canvas.dispose();
    canvas = new fabric.Canvas('flyerCanvas', {
      width: dw, height: dh,
      backgroundColor: '#0a0a14', preserveObjectStacking: true,
      uniScaleTransform: false,
      uniformScaling: false,
    });
    canvas._realWidth = preset.w;
    canvas._realHeight = preset.h;
    canvas._scale = scale;
    // Free-scale all newly added objects (no aspect ratio lock on corner handles)
    canvas.on('object:added', e => {
      if (e && e.target) e.target.set({ lockUniScaling: false });
      refreshLayers();
    });
    canvas.on('selection:created', () => { updateProps(); refreshLayers(); });
    canvas.on('selection:updated', () => { updateProps(); refreshLayers(); });
    canvas.on('selection:cleared', () => { hideProps(); refreshLayers(); });
    canvas.on('object:modified', () => { updateProps(); refreshLayers(); keepBorderOnTop(); });
    canvas.on('object:moving', (e) => { keepBorderOnTop(); applySnapGuides(e); });
    canvas.on('object:modified', () => { clearSnapGuides(); });
    canvas.on('mouse:up', () => { clearSnapGuides(); });
    canvas.on('selection:cleared', () => { clearSnapGuides(); });
    canvas.on('object:removed', refreshLayers);
    // Auto-shrink text as the user types. Fires every keystroke inside
    // an editing Textbox — keeps long content from busting out of its box.
    canvas.on('text:changed', (e) => {
      if (e && e.target) { try { autoFitText(e.target); } catch (_) {} }
    });
    initUndoRedo();
  }

  /* Auto-shrink a Textbox's font size when the text would wrap onto more
     lines than the original placeholder expected. Long artist names that
     used to bust out of their box now shrink to fit. The first time we
     run this on an object we cache its original fontSize as the upper
     bound so subsequent shorter text re-grows back up. */
  function autoFitText(obj) {
    if (!obj || !canvas) return;
    if (!obj.type || !obj.type.toLowerCase().includes('text')) return;
    if (typeof obj.initDimensions !== 'function') return;
    if (obj._origFontSize == null) obj._origFontSize = obj.fontSize;
    if (obj._origLineCount == null) {
      // Capture the natural line count at the original font size — that's
      // the budget we won't exceed when fitting longer text in.
      obj._origLineCount = Math.max(1, (obj._textLines || [obj.text || '']).length);
    }
    const expected = Math.max(obj._origLineCount, (obj.text || '').split('\n').length);
    // Try to grow back up to the original size first.
    obj.set('fontSize', obj._origFontSize);
    obj.initDimensions();
    let fs = obj._origFontSize;
    const MIN = 8;
    let attempts = 0;
    while (obj._textLines && obj._textLines.length > expected && fs > MIN && attempts < 40) {
      fs *= 0.92;
      obj.set('fontSize', fs);
      obj.initDimensions();
      attempts++;
    }
  }

  /* =========================================================
     SNAP GUIDES (Figma/Canva-style alignment lines while dragging)
     ========================================================= */
  const SNAP_THRESHOLD = 6;   // px tolerance for "this edge matches"
  let _activeGuides = [];

  // Compute 6 reference lines for an object (or the canvas itself):
  // left / right / centerX (vertical guides) and top / bottom / centerY.
  function _objectGuides(obj) {
    const b = obj.getBoundingRect(true);
    return {
      v: [b.left, b.left + b.width / 2, b.left + b.width],   // vertical lines (x values)
      h: [b.top,  b.top  + b.height / 2, b.top  + b.height], // horizontal lines (y values)
    };
  }

  function applySnapGuides(e) {
    if (!canvas || !e || !e.target) return;
    const moving = e.target;
    if (moving._isBg || moving._isBgColor || moving._isBorder || moving._isDarkOverlay) {
      clearSnapGuides(); return;
    }
    // Collect candidate guide lines from canvas edges + every OTHER object
    const targets = { v: [0, canvas.width / 2, canvas.width],
                      h: [0, canvas.height / 2, canvas.height] };
    canvas.getObjects().forEach((o) => {
      if (o === moving) return;
      if (o._isBorder || o._isDarkOverlay) return;
      const g = _objectGuides(o);
      g.v.forEach((x) => targets.v.push(x));
      g.h.forEach((y) => targets.h.push(y));
    });

    const m = _objectGuides(moving);
    let snapX = null, snapY = null;
    let snapXLine = null, snapYLine = null;

    // For each of the 3 vertical lines (left, centerX, right) on the
    // moving object, find the closest target line within threshold.
    m.v.forEach((x, idx) => {
      targets.v.forEach((tx) => {
        const d = Math.abs(tx - x);
        if (d <= SNAP_THRESHOLD && (snapX === null || d < Math.abs(snapX))) {
          snapX = tx - x;       // delta to add to moving object's left
          snapXLine = tx;       // where to draw the guide
        }
      });
    });
    m.h.forEach((y, idx) => {
      targets.h.forEach((ty) => {
        const d = Math.abs(ty - y);
        if (d <= SNAP_THRESHOLD && (snapY === null || d < Math.abs(snapY))) {
          snapY = ty - y;
          snapYLine = ty;
        }
      });
    });

    // Apply the snap by nudging the moving object's left/top by the delta.
    if (snapX !== null) moving.set({ left: (moving.left || 0) + snapX });
    if (snapY !== null) moving.set({ top:  (moving.top  || 0) + snapY });
    if (snapX !== null || snapY !== null) moving.setCoords();

    // Draw the visible guide lines on the upper canvas (above objects).
    drawSnapGuides(snapXLine, snapYLine);
  }

  function drawSnapGuides(xLine, yLine) {
    if (!canvas) return;
    clearSnapGuides();
    const ctx = canvas.contextTop;
    if (!ctx) return;
    ctx.save();
    ctx.strokeStyle = '#06b6d4';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    if (xLine != null) {
      ctx.beginPath(); ctx.moveTo(xLine + 0.5, 0); ctx.lineTo(xLine + 0.5, canvas.height); ctx.stroke();
      _activeGuides.push({ kind: 'v', pos: xLine });
    }
    if (yLine != null) {
      ctx.beginPath(); ctx.moveTo(0, yLine + 0.5); ctx.lineTo(canvas.width, yLine + 0.5); ctx.stroke();
      _activeGuides.push({ kind: 'h', pos: yLine });
    }
    ctx.restore();
  }

  function clearSnapGuides() {
    if (!canvas) return;
    _activeGuides = [];
    // Clearing the upper-canvas context is the standard Fabric pattern.
    canvas.clearContext(canvas.contextTop);
  }

  function attachZoneRender(rect) {
    if (rect._zoneRenderAttached) return;
    rect._zoneRenderAttached = true;
    const orig = rect._render.bind(rect);
    rect._render = function(ctx) {
      orig(ctx);
      const w = this.width, h = this.height;
      const fs = Math.max(16, Math.min(36, h * 0.22));
      ctx.save();
      ctx.font = `bold ${fs}px Impact, Arial Black, sans-serif`;
      ctx.fillStyle = 'rgba(196,181,253,0.9)';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(this._zoneLabel || 'ARTIST LOGO', 0, -fs * 0.5);
      ctx.font = `${Math.max(10, fs * 0.48)}px Arial, sans-serif`;
      ctx.fillStyle = 'rgba(148,163,184,0.75)';
      ctx.fillText('resize to set logo area', 0, fs * 0.7);
      ctx.restore();
    };
  }

  function loadCanvasData(data, hydrateVars, doneCb) {
    if (!canvas || !data) { if (doneCb) doneCb(); return; }
    _undoPaused = true;
    try {
      const json = typeof data === 'string' ? JSON.parse(data) : data;
      // Build a src→_tplVar map AND an index→_tplVar map for fallback
      // This handles both image objects (matched by src) and text objects (matched by index)
      const tplVarBySrc = {};
      const tplVarByIdx = {};
      (json.objects || []).forEach((o, i) => {
        if (o._tplVar) {
          tplVarByIdx[i] = o._tplVar;
          if (o.src) tplVarBySrc[o.src] = o._tplVar;
        }
      });
      // Reviver runs on each object as Fabric creates it — restore _tplVar immediately
      const reviver = (jsonObj, fabricObj) => {
        const tv = (jsonObj.src && tplVarBySrc[jsonObj.src]) || jsonObj._tplVar;
        if (tv) fabricObj._tplVar = tv;
        if (jsonObj._isBg) fabricObj._isBg = true;
        if (jsonObj._isBgColor) fabricObj._isBgColor = true;
        if (jsonObj._isBorder) fabricObj._isBorder = true;
        if (jsonObj._layerLocked) { fabricObj._layerLocked = true; fabricObj.set({selectable:false,evented:false}); }
        if (jsonObj._isLogoPlaceholderRect) fabricObj._isLogoPlaceholderRect = true;
        if (jsonObj._isDarkOverlay) fabricObj._isDarkOverlay = true;
        if (jsonObj._isZoneRect) {
          fabricObj._isZoneRect = true;
          fabricObj._zoneLabel = jsonObj._zoneLabel || 'ARTIST LOGO';
          attachZoneRender(fabricObj);
        }
      };
      canvas.loadFromJSON(json, () => {
        // Second pass: catch any objects reviver may have missed (belt-and-suspenders)
        const objs = canvas.getObjects();
        objs.forEach((obj, i) => {
          if (!obj._tplVar && tplVarByIdx[i]) obj._tplVar = tplVarByIdx[i];
        });
        if (hydrateVars && gigInfo) hydrateTemplateVars();
        syncBorderUI();
        canvas.renderAll();
        _undoPaused = false;
        if (doneCb) doneCb();
      }, reviver);
    } catch(e) { console.error('loadCanvas:', e); _undoPaused = false; if (doneCb) doneCb(); }
  }

  function hydrateTemplateVars() {
    if (!canvas || !gigInfo) return;
    console.log('[FlyerEditor] hydrateTemplateVars: artist_picture_url=', gigInfo.artist_picture_url, 'artist_name=', gigInfo.artist_name);
    const objs = canvas.getObjects().map(o => ({type:o.type, _tplVar:o._tplVar, text:o.text?.slice?.(0,20)}));
    console.log('[FlyerEditor] canvas objects:', JSON.stringify(objs));
    const s = canvas._scale || 0.385;
    const varMap = {
      venue_name: (gigInfo.venue_name || 'VENUE NAME').toUpperCase(),
      location: buildLocationText() || '123 Main St\nCity, State 00000',
      date: gigInfo.date ? new Date(gigInfo.date+'T00:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'}) : 'Date TBA',
      time: buildTimeText() || 'Time TBA',
      artist_name: gigInfo.artist_name ? gigInfo.artist_name.toUpperCase() : ''
    };
    // First pass: remove only legacy unlabelled text placeholders
    // Do NOT remove zone rects here — they are handled in the second pass (replaced with real images)
    {
      const toRemove = [];
      canvas.getObjects().forEach(obj => {
        // Remove old-style dashed rect placeholders (not zone rects, not borders, not overlays)
        if (obj.type === 'rect' && !obj._isBorder && !obj._isDarkOverlay && !obj._isZoneRect &&
            obj.strokeDashArray && obj.strokeDashArray.length &&
            obj._tplVar === 'artist_logo_placeholder') {
          toRemove.push(obj);
        }
        // Remove legacy unlabelled text (old FEATURING / YOUR ARTIST text)
        if (obj.type && obj.type.includes('text') && !obj._tplVar) {
          const t = (obj.text || '').trim().toUpperCase();
          if (t === 'FEATURING' || t === 'YOUR ARTIST') toRemove.push(obj);
        }
      });
      toRemove.forEach(o => canvas.remove(o));
    }
    // Second pass: hydrate template variables
    // Pre-check: does canvas already have an explicit artist_logo object?
    // If yes, skip the artist_name→image fallback to avoid overwriting saved geometry.
    // hasArtistLogo: true only when there's a real positioned image (not a zone rect placeholder)
    const hasArtistLogo = canvas.getObjects().some(o => o._tplVar === 'artist_logo' && o.type === 'image' && !o._isZoneRect);
    canvas.getObjects().forEach(obj => {
      if (!obj._tplVar) return;
      const v = obj._tplVar;
      if (v === 'venue_logo' || v === 'artist_logo') {
        // Multi-slot: each artist_logo image carries _tplArtistId pointing at the
        // specific artist whose logo it is. Hydrate against THAT artist's URL —
        // not gigInfo.artist_picture_url, which is just slot 1's artist and would
        // overwrite every artist_logo image with the same picture.
        let url;
        if (v === 'venue_logo') {
          url = gigInfo.venue_picture_url;
        } else {
          if (obj._tplArtistId != null && Array.isArray(gigInfo.slots)) {
            const slot = gigInfo.slots.find(s => Number(s.artist_id) === Number(obj._tplArtistId));
            if (slot) {
              url = slot.artist_picture_url;
            } else {
              // Tagged artist is no longer booked on this gig — leave the saved
              // image as-is. Cancel-cleanup is the path that should remove it.
              return;
            }
          } else {
            url = gigInfo.artist_picture_url;
          }
        }
        if (url) {
          const cw = canvas.width, ch = canvas.height;
          const isZoneRect = obj.type === 'rect' && obj._isZoneRect;
          const savedGeom = obj.type === 'image' ? {
            left:    obj.left,
            top:     obj.top,
            scaleX:  obj.scaleX,
            scaleY:  obj.scaleY,
            angle:   obj.angle  || 0,
            originX: obj.originX || 'left',
            originY: obj.originY || 'top',
            flipX:   obj.flipX  || false,
            flipY:   obj.flipY  || false,
            shadow:  obj.shadow,
            opacity: obj.opacity != null ? obj.opacity : 1,
          } : null;
          const artistTagId = (v === 'artist_logo') ? obj._tplArtistId : undefined;
          fabricLoadImage(url, function(img) {
            if (!img || !canvas) return;
            if (savedGeom) {
              // Saved image — apply exact saved position/size, swap only the src
              img.set({ ...savedGeom, _tplVar: v });
            } else if (isZoneRect) {
              // Zone rect — fit image inside the rect's exact bounds
              const zoneW = obj.width  * (obj.scaleX || 1);
              const zoneH = obj.height * (obj.scaleY || 1);
              const sc = Math.min(zoneW / img.width, zoneH / img.height, 1);
              img.set({
                left: obj.left + zoneW / 2, top: obj.top + zoneH / 2,
                scaleX: sc, scaleY: sc,
                originX: 'center', originY: 'center',
                shadow: new fabric.Shadow({color:'rgba(0,0,0,0.8)',blur:20}),
                _tplVar: v
              });
            } else {
              // Placeholder text — fit image into that zone
              const oldTop    = obj.top;
              const oldWidth  = cw * 0.94;
              const oldHeight = ch - obj.top - ch * 0.03;
              const sc = Math.min(oldWidth / img.width, oldHeight / img.height, 1);
              img.set({
                left: cw / 2, top: oldTop + oldHeight / 2,
                scaleX: sc, scaleY: sc,
                originX: 'center', originY: 'center',
                shadow: obj.shadow,
                _tplVar: v
              });
            }
            // Preserve the artist tag so subsequent saves keep the multi-slot binding.
            if (artistTagId != null) img._tplArtistId = artistTagId;
            canvas.remove(obj); canvas.add(img); canvas.renderAll();
          });
        } else if (v === 'venue_logo') {
          // No venue picture — leave the zone rect as-is (shows "VENUE LOGO" placeholder label)
          // Same behavior as artist_logo with no picture
        } else if (v === 'artist_logo') {
          // No artist picture available.
          // If this is a zone rect placeholder, leave it exactly as-is — it's already the visible purple placeholder.
          // If it's a saved image (e.g. prev gig photo) with no replacement, also leave it.
          // Only replace plain text placeholders with artist name text when we have a name.
          if (!obj._isZoneRect && obj.type && obj.type.includes('text') && gigInfo.artist_name) {
            const s = canvas._scale || 0.385;
            const oldWidth = (obj.width || 200) * (obj.scaleX || 1);
            const oldHeight = (obj.height || 200) * (obj.scaleY || 1);
            const txt = new fabric.Textbox(gigInfo.artist_name.toUpperCase(), {
              left: obj.left, top: obj.top + oldHeight * 0.1,
              width: oldWidth, originX: obj.originX || 'left',
              fontSize: Math.max(60, Math.min(160, oldHeight * 0.4)) * s,
              fontFamily: 'Impact, Arial Black, sans-serif', fontWeight: 'bold',
              fill: '#ffffff', opacity: 1, textAlign: 'center', lineHeight: 0.85,
              _tplVar: 'artist_logo', stroke: '#000000', strokeWidth: 3 * s,
              shadow: new fabric.Shadow({color:'rgba(0,0,0,0.9)',blur:25})
            });
            canvas.remove(obj); canvas.add(txt); canvas.renderAll();
          }
          // Zone rect: leave it — it shows the purple placeholder border correctly
        }
      } else if (v === 'artist_name' && obj.type && obj.type.includes('text')) {
        // If artist has a profile picture AND there's no dedicated artist_logo, replace text with image
        if (gigInfo.artist_picture_url && !hasArtistLogo) {
          const cw = canvas.width, ch = canvas.height;
          const artistZoneTop = obj.top;
          const artistZoneH = ch - obj.top - (ch * 0.03);
          fabricLoadImage(gigInfo.artist_picture_url, function(img) {
            if (!img || !canvas) return;
            const W = cw * 0.94;
            const sc = Math.min((W * 0.94) / img.width, artistZoneH / img.height, 1);
            img.set({ left:cw/2, top:artistZoneTop + artistZoneH/2,
              originX:'center', originY:'center', scaleX:sc, scaleY:sc,
              _tplVar:'artist_logo',
              shadow: new fabric.Shadow({color:'rgba(0,0,0,0.9)',blur:35}) });
            canvas.remove(obj); canvas.add(img); canvas.renderAll();
          });
        } else if (varMap.artist_name) {
          // No picture but has name — ensure full visibility (white, full opacity)
          obj.set({ text: varMap.artist_name, fill: '#ffffff', opacity: 1, stroke: '#000000', strokeWidth: (canvas._scale||0.385) * 3 });
        }
      } else if (varMap[v] !== undefined && obj.type && obj.type.includes('text')) {
        // Only update if we have a real non-empty value — never blank out a placeholder
        if (varMap[v] !== '') obj.set('text', varMap[v]);
      }
    });

    // ── Fallback pass: hydrate by text content for objects missing _tplVar ──
    // Handles templates saved before _tplVar was reliably serialized, or any loss of the tag.
    const dateVal  = varMap.date;
    const timeVal  = varMap.time;
    const venueVal = varMap.venue_name;
    const locVal   = varMap.location;
    const timeRegex = /^\d{1,2}:\d{2}\s*(AM|PM)/i;
    const dateRegex = /^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4}$/i;
    canvas.getObjects().forEach(obj => {
      if (!obj.type || !obj.type.includes('text')) return;
      const t = (obj.text || '').trim();
      if (!t) return;
      const tUpper = t.toUpperCase();
      // For tagged date objects whose text is still the placeholder 'Date', force-hydrate with real date
      // This handles templates where _tplVar='date' was tagged but varMap.date was empty on first pass
      if (obj._tplVar === 'date' && (t === 'Date' || t === 'DATE' || dateRegex.test(t)) && dateVal) {
        obj.set('text', dateVal);
        return;
      }
      if (obj._tplVar) return; // skip all other already-tagged objects
      // Date: placeholder is exactly 'Date' or a month-day-year string
      if ((t === 'Date' || t === 'DATE' || dateRegex.test(t)) && dateVal) {
        obj._tplVar = 'date';
        obj.set('text', dateVal);
      // Time: placeholder is exactly 'Time' or a time pattern
      } else if ((t === 'Time' || t === 'TIME' || timeRegex.test(t)) && timeVal) {
        obj._tplVar = 'time';
        obj.set('text', timeVal);
      // Venue name: placeholder is 'VENUE NAME' or matches venue name pattern
      } else if ((tUpper === 'VENUE NAME' || tUpper === venueVal) && venueVal) {
        obj._tplVar = 'venue_name';
        obj.set('text', venueVal);
      // Location: placeholder starts with '123 Main' or is multiple address lines
      } else if ((t === 'LOCATION' || t.includes('123 Main') || (t.includes('\n') && t.length < 120)) && locVal) {
        obj._tplVar = 'location';
        obj.set('text', locVal);
      }
    });
    // After hydrating template variables with real gig data, shrink any
    // text objects that would overflow (long artist name, etc.).
    canvas.getObjects().forEach((o) => {
      if (o && o.type && o.type.toLowerCase().includes('text')) {
        try { autoFitText(o); } catch (_) {}
      }
    });
    canvas.renderAll();
  }

  function buildLocationText() {
    if (!gigInfo) return '';
    const lines = [];
    if (gigInfo.address_line_1) lines.push(gigInfo.address_line_1);
    if (gigInfo.address_line_2) lines.push(gigInfo.address_line_2);
    const cs = [gigInfo.city, gigInfo.state].filter(Boolean).join(', ');
    if (cs) lines.push(cs);
    return lines.join('\n');
  }

  function buildTimeText() {
    if (!gigInfo) return '';
    if (gigInfo.slots && gigInfo.slots.length > 0) {
      return gigInfo.slots.map(sl => `${fmt12(sl.start_time)} - ${fmt12(sl.end_time)}`).join('  |  ');
    }
    if (gigInfo.start_time) {
      let t = fmt12(gigInfo.start_time);
      if (gigInfo.end_time) t += '  -  ' + fmt12(gigInfo.end_time);
      return t;
    }
    return '';
  }

  function getCanvasJSON() { return canvas ? JSON.stringify(canvas.toJSON(['_tplVar', '_tplArtistId', '_isBg', '_isBgColor', '_isBorder', '_isDarkOverlay', '_isLogoPlaceholderRect', '_isZoneRect', '_zoneLabel', '_layerLocked'])) : '{}'; }
  function getThumbnail() { return canvas ? canvas.toDataURL({format:'jpeg',quality:0.92,multiplier:1.5}) : ''; }

  /* =========================================================
     NAMING
     ========================================================= */
  function buildFlyerName() {
    const v = (gigInfo?.venue_name || '').replace(/[^a-zA-Z0-9 ]/g,'').replace(/\s+/g,'_');
    const d = gigInfo?.date ? new Date(gigInfo.date+'T00:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}).replace(/[\s,]+/g,'_') : '';
    const st = gigInfo?.start_time ? fmt12(gigInfo.start_time).replace(/\s+/g,'') : '';
    const et = gigInfo?.end_time ? fmt12(gigInfo.end_time).replace(/\s+/g,'') : '';
    const timeRange = (st && et) ? `${st}-${et}` : st || '';
    const a = (gigInfo?.artist_name || '').replace(/[^a-zA-Z0-9 ]/g,'').replace(/\s+/g,'_');
    const parts = [v, d, timeRange, a].filter(Boolean);
    return parts.join('_') || 'Flyer';
  }

  function updateNameDisplay() {
    // Name now shown via setStatus only
  }

  /* =========================================================
     DEFAULT TEMPLATE — PROFESSIONAL CONCERT FLYER
     Canvas: ~416x520 display, exports at 1080x1350
     Scale s ≈ 0.385
     Layout: Venue → Location → LIVE MUSIC → Date → Time → Artist
     ========================================================= */
  function fmt12(t) {
    if (!t) return '';
    const [h,m] = t.split(':'); const hr = parseInt(h);
    return `${hr > 12 ? hr - 12 : hr || 12}:${m} ${hr >= 12 ? 'PM' : 'AM'}`;
  }

  function loadDefaultTemplate() {
    if (!canvas) return;
    _undoPaused = true;
    canvas.clear();
    const cw = canvas.width, ch = canvas.height;
    canvas.backgroundColor = '#0a0a14';
    let elementsAdded = false;
    function ensureElements() {
      if (elementsAdded) return;
      elementsAdded = true;
      addDefaultElements();
      _undoPaused = false;
    }
    fabric.Image.fromURL(DEFAULT_BG_URL, function(img) {
      if (img && img.width > 0 && canvas) {
        const sc = Math.max(cw / img.width, ch / img.height);
        img.set({ scaleX: sc, scaleY: sc, originX:'left', originY:'top', opacity: 0.40 });
        img._isBg = true;  // flag for fit-on-resize logic
        canvas.add(img);
        canvas.sendToBack(img);
      }
      ensureElements();
    });
    setTimeout(() => { ensureElements(); }, 2000);
  }

  function addDefaultElements() {
    const s = canvas._scale, cw = canvas.width, ch = canvas.height;
    const W = cw * 0.94, L = cw * 0.03;
    const FONT = 'Trebuchet MS';
    const GREY = 'rgba(200,200,210,0.9)';

    // Dark overlay for readability
    canvas.add(new fabric.Rect({ left:0,top:0,width:cw,height:ch,
      fill:'rgba(0,0,0,0.35)', selectable:false, evented:false, _isDarkOverlay:true }));

    // ═══════════════════════════════════════
    //  1. VENUE LOGO — zone rect placeholder (top 3%-19%)
    //  hydrateTemplateVars() swaps it for the real venue logo when a gig is opened.
    //  Never inject real data here — the template must stay generic.
    // ═══════════════════════════════════════
    const venueZoneTop = ch * 0.03;
    const venueZoneH   = ch * 0.16;
    const venueZoneW   = W * 0.75;
    const venueZoneLeft = cw / 2 - venueZoneW / 2;
    const venueZone = new fabric.Rect({
      left: venueZoneLeft, top: venueZoneTop,
      width: venueZoneW, height: venueZoneH,
      fill: 'rgba(139,92,246,0.07)',
      stroke: 'rgba(139,92,246,0.7)', strokeWidth: Math.max(2, 2 * s),
      strokeDashArray: [8 * s, 5 * s],
      rx: 6 * s, ry: 6 * s,
      selectable: true, evented: true,
      _tplVar: 'venue_logo', _isZoneRect: true, _zoneLabel: 'VENUE LOGO'
    });
    attachZoneRender(venueZone);
    canvas.add(venueZone);

    // ═══════════════════════════════════════
    //  2. LOCATION — 22-32% (multi-line)
    // ═══════════════════════════════════════
    const locText = buildLocationText() || '123 Main St\nCity, State 00000';
    canvas.add(new fabric.Textbox(locText, {
      left:L, top:ch*0.225, width:W,
      fontSize:45*s, fontFamily:FONT, fontWeight:'bold', fill:GREY,
      textAlign:'center', lineHeight:1.15,
      stroke:'#000000', strokeWidth:0, _tplVar:'location'
    }));

    // ═══════════════════════════════════════
    //  3. ★ LIVE MUSIC ★ — 36-46%
    // ═══════════════════════════════════════
    canvas.add(new fabric.Rect({ left:L, top:ch*0.36, width:W, height:2*s,
      fill:'rgba(139,92,246,0.5)', selectable:true, evented:true,
      shadow: new fabric.Shadow({color:'rgba(139,92,246,0.8)',blur:12}) }));

    canvas.add(new fabric.Textbox('★  L I V E   M U S I C  ★', {
      left:L, top:ch*0.38, width:W,
      fontSize:90*s, fontFamily:FONT, fontWeight:'bold', fill:GREY,
      textAlign:'center', letterSpacing:120,
      stroke:'#000000', strokeWidth:4*s, _tplVar:'live_music',
      shadow: new fabric.Shadow({color:'rgba(192,132,252,0.6)',blur:20})
    }));

    canvas.add(new fabric.Rect({ left:L, top:ch*0.46, width:W, height:2*s,
      fill:'rgba(139,92,246,0.5)', selectable:true, evented:true,
      shadow: new fabric.Shadow({color:'rgba(139,92,246,0.8)',blur:12}) }));

    // ═══════════════════════════════════════
    //  4. DATE — 48-56%
    // ═══════════════════════════════════════
    const dateStr = gigInfo?.date
      ? new Date(gigInfo.date+'T00:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'})
      : 'DATE';

    canvas.add(new fabric.Textbox(dateStr, {
      left:L, top:ch*0.485, width:W,
      fontSize:85*s, fontFamily:FONT, fontWeight:'bold', fill:'#ffffff',
      textAlign:'center',
      stroke:'#000000', strokeWidth:4*s, _tplVar:'date',
      shadow: new fabric.Shadow({color:'rgba(0,0,0,0.8)',blur:10})
    }));

    // ═══════════════════════════════════════
    //  5. TIME — 57-65%
    // ═══════════════════════════════════════
    const timeText = buildTimeText() || 'TIME';
    canvas.add(new fabric.Textbox(timeText, {
      left:L, top:ch*0.57, width:W,
      fontSize:85*s, fontFamily:FONT, fontWeight:'bold', fill:'#ffffff', textAlign:'center',
      stroke:'#000000', strokeWidth:4*s, _tplVar:'time',
      shadow: new fabric.Shadow({color:'rgba(0,0,0,0.7)',blur:8})
    }));

    // ═══════════════════════════════════════
    //  6. ARTIST LOGO — always a purple zone rect placeholder.
    //  hydrateTemplateVars() swaps it with the real artist image when a specific gig is opened.
    //  This ensures the template always shows the resizable placeholder, not injected data.
    // ═══════════════════════════════════════
    const artistZoneTop = ch * 0.67;
    const artistZoneH = ch * 0.30;
    const placeholderW = W * 0.80;
    const placeholderH = artistZoneH * 0.88;
    const placeholderL = cw / 2 - placeholderW / 2;
    const placeholderT = artistZoneTop + artistZoneH * 0.06;
    const zone = new fabric.Rect({
      left: placeholderL, top: placeholderT,
      width: placeholderW, height: placeholderH,
      fill: 'rgba(139,92,246,0.07)',
      stroke: 'rgba(139,92,246,0.7)', strokeWidth: Math.max(2, 2 * s),
      strokeDashArray: [8 * s, 5 * s],
      rx: 6 * s, ry: 6 * s,
      selectable: true, evented: true,
      _tplVar: 'artist_logo', _isZoneRect: true, _zoneLabel: 'ARTIST LOGO'
    });
    attachZoneRender(zone);
    canvas.add(zone);

    canvas.renderAll();
  }

  /* =========================================================
     TEMPLATES
     ========================================================= */
  async function loadTemplateDropdown(activeTemplateId) {
    const sel = document.getElementById('flyerTemplateSelect');
    const siteSel = document.getElementById('flyerSiteDefault');
    if (!sel) return;
    while (sel.options.length > 1) sel.remove(1);
    if (siteSel) while (siteSel.options.length > 1) siteSel.remove(1);

    try {
      // Fetch templates and venue settings in parallel
      const [r, r2] = await Promise.all([
        fetch(`/api/venues/${venueId}/flyer-templates`, {credentials:'include'}),
        fetch(`/api/venues/${venueId}`, {credentials:'include'})
      ]);
      if (!r.ok) return;
      const templates = await r.json();
      let savedTemplateId = null;
      if (r2.ok) {
        const v = await r2.json();
        savedTemplateId = v.default_flyer_template_id ? String(v.default_flyer_template_id) : null;
      }

      const vName = gigInfo?.venue_name || '';
      const venueDefaultName = vName ? `${vName}_Default Template` : '';
      const venueDefault = venueDefaultName ? templates.find(t => t.name === venueDefaultName) : null;
      const others = templates.filter(t => t.name !== venueDefaultName);

      // TEMPLATES dropdown: ⭐ Default Template first, then venue templates
      const defOpt = document.createElement('option');
      defOpt.value = '__default__'; defOpt.textContent = '⭐ Default Template';
      sel.appendChild(defOpt);
      const blankOpt = document.createElement('option');
      blankOpt.value = '__blank__'; blankOpt.textContent = '⬜ Blank Canvas';
      sel.appendChild(blankOpt);
      if (venueDefault || others.length > 0) {
        const sep = document.createElement('option'); sep.disabled = true;
        sep.textContent = '────────────────────────'; sel.appendChild(sep);
      }
      if (venueDefault) {
        const opt = document.createElement('option');
        opt.value = String(venueDefault.id); opt.textContent = venueDefault.name;
        sel.appendChild(opt);
      }
      if (others.length > 0) {
        if (venueDefault) {
          const sep2 = document.createElement('option'); sep2.disabled = true;
          sep2.textContent = '────────────────────────'; sel.appendChild(sep2);
        }
        others.forEach(t => {
          const opt = document.createElement('option');
          opt.value = String(t.id); opt.textContent = t.name;
          sel.appendChild(opt);
        });
      }

      // "Which template for all gigs" dropdown
      if (siteSel) {
        const allTpls = [];
        if (venueDefault) allTpls.push(venueDefault);
        allTpls.push(...others);
        allTpls.forEach(t => {
          const opt = document.createElement('option');
          opt.value = String(t.id); opt.textContent = t.name;
          siteSel.appendChild(opt);
        });
        // Restore saved "Which template" selection
        if (savedTemplateId) siteSel.value = savedTemplateId;
      }

      // Set TEMPLATES dropdown to show what is currently loaded on canvas
      // activeTemplateId passed from open() takes priority; otherwise use savedTemplateId
      const toSelect = activeTemplateId || savedTemplateId;
      if (toSelect && toSelect !== '__default__') {
        sel.value = String(toSelect);
      } else if (toSelect === '__default__' || (!toSelect && !savedTemplateId)) {
        sel.value = '__default__';
      }
      // If activeTemplateId is null (gig has its own saved flyer), leave sel on "Load Template"

      // Render the visual thumbnail grid — show the 3 most-recent
      // templates so the strip stays compact. The dropdown above lists
      // everything for users who want the full picker.
      const tplsAll = [];
      if (venueDefault) tplsAll.push(venueDefault);
      tplsAll.push(...others);
      // Newest first (by updated_at when present, else by id).
      tplsAll.sort((a, b) => {
        const ka = a.updated_at || a.created_at || a.id || 0;
        const kb = b.updated_at || b.created_at || b.id || 0;
        return String(kb).localeCompare(String(ka));
      });
      renderTemplateThumbs(tplsAll.slice(0, 3), toSelect);
    } catch(e) { console.error('[FlyerEditor] loadTemplateDropdown error:', e); }
  }

  /* Render a small thumbnail grid for stored templates. Click a thumbnail
     to load that template (same as selecting it in the dropdown). The
     currently-loaded template is highlighted with a cyan border. */
  function renderTemplateThumbs(templates, activeId) {
    const wrap = document.getElementById('flyerTemplateThumbs');
    if (!wrap) return;
    if (!templates || !templates.length) { wrap.innerHTML = ''; return; }
    const activeStr = activeId != null ? String(activeId) : '';
    wrap.innerHTML = templates.map((t) => {
      const id = String(t.id);
      const isActive = id === activeStr;
      const nameEsc = (t.name || 'Untitled').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      const thumb = (t.thumbnail_data && /^data:image\//.test(t.thumbnail_data))
        ? `<img src="${t.thumbnail_data}" alt="" style="display:block;width:100%;height:60px;object-fit:cover;background:#0a0a14;">`
        : `<div style="height:60px;background:linear-gradient(135deg,#1a1f2e,#0a0a14);display:flex;align-items:center;justify-content:center;color:#475569;font-size:1.4rem;">🎵</div>`;
      const border = isActive ? '2px solid var(--cyan,#06b6d4)' : '1px solid rgba(255,255,255,0.08)';
      return `
        <div class="fe-tpl-thumb" data-template-id="${id}" title="${nameEsc}"
             style="cursor:pointer;border:${border};border-radius:6px;overflow:hidden;background:#0a0a14;">
          ${thumb}
          <div style="font-size:0.62rem;color:#cbd5e1;padding:3px 5px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${nameEsc}</div>
        </div>`;
    }).join('');
    // Wire clicks
    wrap.querySelectorAll('.fe-tpl-thumb').forEach((el) => {
      el.addEventListener('click', () => {
        const id = el.getAttribute('data-template-id');
        if (id) onTemplateSelect(id);
      });
      el.addEventListener('mouseover', () => {
        if (!el.style.borderColor.includes('182, 212'))  // not active
          el.style.borderColor = 'rgba(139,92,246,0.55)';
      });
      el.addEventListener('mouseout', () => {
        if (!el.classList.contains('active'))
          el.style.borderColor = '';
      });
    });
  }

  async function loadAdminTemplateDropdown() {
    const sel = document.getElementById('flyerTemplateSelect');
    if (!sel) return;
    while (sel.options.length > 1) sel.remove(1);
    try {
      const r = await fetch('/api/admin/flyers/templates', {credentials:'include'});
      if (!r.ok) return;
      const templates = await r.json();
      const defTpl = templates.find(t => t.name.toLowerCase() === 'default template');
      const others = templates.filter(t => t.name.toLowerCase() !== 'default template');
      const blankOptA = document.createElement('option');
      blankOptA.value = '__blank__'; blankOptA.textContent = '⬜ Blank Canvas';
      sel.appendChild(blankOptA);
      if (defTpl) {
        const opt = document.createElement('option');
        opt.value = defTpl.id; opt.textContent = '⭐ ' + defTpl.name;
        sel.appendChild(opt);
      }
      if (others.length) {
        const sep = document.createElement('option'); sep.disabled = true; sep.textContent = '────────────────────────';
        sel.appendChild(sep);
        others.forEach(t => {
          const opt = document.createElement('option'); opt.value = t.id; opt.textContent = t.name;
          sel.appendChild(opt);
        });
      }
    } catch(e) {}
  }

  async function onAdminTemplateSelect(val) {
    if (!val) return;
    if (val === '__blank__') {
      if (canvas) { canvas.clear(); canvas.backgroundColor = '#1a1a2e'; canvas.renderAll(); }
      setStatus('Blank canvas', '#94a3b8');
      setFileTitle('Untitled'); markDirty();
      currentFlyer = null;
      return;
    }
    const del = document.getElementById('flyerDeleteTplBtn');
    try {
      const r = await fetch(`/api/admin/flyers/templates/${val}`, {credentials:'include'});
      if (r.ok) {
        const tpl = await r.json();
        currentFlyer = tpl;
        applyTemplatePreset(tpl);
        loadCanvasData(tpl.canvas_data, false, () => { refreshLayers(); syncBorderUI(); });
        const isDefault = tpl.name.toLowerCase() === 'default template';
        if (del) { del.style.display = isDefault ? 'none' : ''; del.dataset.templateId = val; del.dataset.templateName = tpl.name; }
        setStatus(`"${tpl.name}" loaded`, '#67e8f9');
        setFileTitle(tpl.name); markClean();
        // Keep dropdown showing selected template — do NOT reset to empty
        document.getElementById('flyerTemplateSelect').value = String(val);
      }
    } catch(e) {}
  }

  async function saveAsAdminDefault() {
    closeSaveMenu();
    const ok = await feModal({
      title: 'Save as Site-Wide Default Template',
      message: 'This will overwrite the "Default Template" that all venues see when no custom template is set.\n\nAre you sure?',
      confirmText: 'Save Site Default'
    });
    if (!ok) return;
    try {
      setStatus('Saving site default...', 'var(--text-gray)');
      const r = await fetch('/api/admin/flyers/default-template', {
        method:'PUT', credentials:'include', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          canvas_data: getCanvasJSON(), thumbnail_data: getThumbnail(),
          size_preset: currentPreset,
          width: SIZE_PRESETS[currentPreset].w, height: SIZE_PRESETS[currentPreset].h
        })
      });
      if (!r.ok) throw new Error(await r.text());
      const saved = await r.json();
      currentFlyer = { id: saved.id, name: 'Default Template' };
      setStatus('✓ Site-wide "Default Template" saved', '#22c55e');
      setFileTitle('Default Template'); markClean();
      loadAdminTemplateDropdown();
    } catch(e) { setStatus('✗ Save failed: '+e.message, '#ef4444'); }
  }

  async function saveAsNewAdminTemplate() {
    closeSaveMenu();
    const name = await feModal({ title:'Save as New Admin Template', input:true, placeholder:'Template name', value:'My Admin Template', confirmText:'Save Template' });
    if (!name) return;
    try {
      const r = await fetch('/api/admin/flyers/templates', {
        method:'POST', credentials:'include', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          name, canvas_data: getCanvasJSON(), thumbnail_data: getThumbnail(),
          size_preset: currentPreset,
          width: SIZE_PRESETS[currentPreset].w, height: SIZE_PRESETS[currentPreset].h
        })
      });
      if (!r.ok) throw new Error(await r.text());
      const saved = await r.json();
      currentFlyer = { id: saved.id, name };
      setStatus(`✓ "${name}" saved`, '#22c55e');
      setFileTitle(name); markClean();
      loadAdminTemplateDropdown();
    } catch(e) { setStatus('✗ Save failed: '+e.message, '#ef4444'); }
  }

  async function deleteAdminTemplate(id, name) {
    const ok = await feModal({ title:'Delete Template', message:`Delete "${name}"? This cannot be undone.`, confirmText:'Delete', danger:true });
    if (!ok) return;
    try {
      const r = await fetch(`/api/admin/flyers/templates/${id}`, { method:'DELETE', credentials:'include' });
      if (!r.ok) throw new Error(await r.text());
      setStatus(`"${name}" deleted`, '#94a3b8');
      currentFlyer = null; loadDefaultTemplate(); setFileTitle('Default Template'); markClean();
      loadAdminTemplateDropdown();
    } catch(e) { setStatus('✗ Delete failed: '+e.message, '#ef4444'); }
  }

  async function setSiteDefault(templateId) {
    if (!venueId) return;
    const sendId = (templateId && templateId !== '__default__') ? templateId : null;
    try {
      await fetch(`/api/venues/${venueId}/settings/default-template`, {
        method:'PUT', credentials:'include', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ template_id: sendId, auto_flyers: true })
      });
    } catch(e) {}
    // Sync TEMPLATES dropdown AND load the template onto the canvas
    const tplSel = document.getElementById('flyerTemplateSelect');
    const loadVal = sendId ? String(sendId) : '__default__';
    if (tplSel) tplSel.value = loadVal;
    await onTemplateSelect(loadVal);
  }

  async function toggleAutoFlyers(checked) {
    if (!venueId) return;
    const row = document.getElementById('flyerSettingsTemplateRow');
    if (row) row.style.display = checked ? '' : 'none';
    try {
      await fetch(`/api/venues/${venueId}`, {
        method:'PUT', credentials:'include', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ auto_flyers: checked ? 1 : 0 })
      });
    } catch(e) {}
  }

  async function loadFlyerSettings() {
    if (!venueId) return;
    // Make sure both venue-only sections are visible
    const s = document.getElementById('flyerSettingsSection');
    if (s) s.style.display = '';
    const p = document.getElementById('flyerPrevSection');
    if (p) p.style.display = '';

    try {
      const r = await fetch(`/api/venues/${venueId}`, {credentials:'include'});
      if (r.ok) {
        const v = await r.json();
        const cb = document.getElementById('flyerAutoCreate');
        const row = document.getElementById('flyerSettingsTemplateRow');
        const autoOn = !!(v.auto_flyers || v.default_flyer_template_id);
        if (cb) cb.checked = autoOn;
        if (row) row.style.display = autoOn ? '' : 'none';
        // Note: flyerSiteDefault value is restored by loadTemplateDropdown() to avoid race conditions
      }
    } catch(e) {}
  }

  async function onTemplateSelect(val) {
    if (!val) return;
    if (val === '__blank__') {
      if (canvas) { canvas.clear(); canvas.backgroundColor = '#1a1a2e'; canvas.renderAll(); }
      setStatus('Blank canvas', '#94a3b8');
      setFileTitle('Untitled'); markDirty();
      currentFlyer = null;
      const del = document.getElementById('flyerDeleteTplBtn');
      const delFlyer = document.getElementById('flyerDeleteFlyerBtn');
      if (del) del.style.display = 'none';
      if (delFlyer) delFlyer.style.display = 'none';
      return;
    }
    if (venueId === 0) { await onAdminTemplateSelect(val); return; }
    const del = document.getElementById('flyerDeleteTplBtn');
    const delFlyer = document.getElementById('flyerDeleteFlyerBtn');
    const tplSel = document.getElementById('flyerTemplateSelect');
    const siteSel = document.getElementById('flyerSiteDefault');
    if (val === '__default__') {
      currentFlyer = null;
      if (del) del.style.display = 'none';
      if (delFlyer) delFlyer.style.display = 'none';
      // Keep TEMPLATES dropdown showing __default__ — do NOT touch flyerSiteDefault
      if (tplSel) tplSel.value = '__default__';
      let _loaded = false;
      try {
        const siteR = await fetch('/api/flyers/site-default-template');
        if (siteR.ok) {
          const siteTpl = await siteR.json();
          if (siteTpl.canvas_data && siteTpl.canvas_data !== '{}') {
            applyTemplatePreset(siteTpl);
            loadCanvasData(siteTpl.canvas_data, false, () => { refreshLayers(); syncBorderUI(); });
            setStatus('"Default Template" loaded', '#67e8f9');
            setFileTitle('Default Template'); markClean();
            _loaded = true;
          }
        }
      } catch(e) {}
      if (!_loaded) {
        loadDefaultTemplate();
        setStatus('"Default Template" loaded', '#67e8f9');
        setFileTitle('Default Template'); markClean();
      }
    } else {
      try {
        const r = await fetch(`/api/venues/${venueId}/flyers/${val}`, {credentials:'include'});
        if (r.ok) {
          const tpl = await r.json();
          if (!tpl.canvas_data || tpl.canvas_data === '{}') {
            setStatus('Template has no content yet', '#f59e0b');
            return;
          }
          // Sync sidebar (canvas size preset) to whatever the template
          // was authored at, then load the design.
          applyTemplatePreset(tpl);
          loadCanvasData(tpl.canvas_data, false, () => { refreshLayers(); syncBorderUI(); });
          if (del) { del.style.display = ''; del.dataset.templateId = val; del.dataset.templateName = tpl.name || 'Untitled'; }
          if (delFlyer) delFlyer.style.display = 'none';
          currentFlyer = null;
          setStatus(`"${tpl.name || 'Untitled'}" loaded`, '#67e8f9');
          setFileTitle(tpl.name || 'Untitled'); markClean();
          if (tplSel) tplSel.value = String(val);
        } else {
          console.error('[FlyerEditor] template fetch failed: HTTP '+r.status+' for flyer id='+val);
          setStatus('Failed to load template (HTTP '+r.status+')', '#ef4444');
        }
      } catch(e) { console.error('[FlyerEditor] template fetch error:', e); }
    }
    // Do NOT reset flyerTemplateSelect — keep the selected value visible
  }

  async function saveAsDefaultTemplate() {
    closeSaveMenu();
    // If gigInfo is null (gig-info fetch failed), fetch venue name directly
    let vName = gigInfo?.venue_name || '';
    if (!vName && venueId) {
      try {
        const vr = await fetch(`/api/venues/${venueId}`, {credentials:'include'});
        if (vr.ok) { const vd = await vr.json(); vName = vd.venue_name || ''; }
      } catch(e) {}
    }
    if (!vName) { setStatus('✗ Cannot save: venue name unavailable', '#ef4444'); return; }
    const tplName = `${vName}_Default Template`;
    try {
      setStatus('Saving as default template...', 'var(--text-gray)');
      const r = await fetch(`/api/venues/${venueId}/flyers/default-template`, {
        method:'PUT', credentials:'include', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          name: tplName,
          canvas_data: getCanvasJSON(), thumbnail_data: getThumbnail(),
          size_preset: currentPreset,
          width: SIZE_PRESETS[currentPreset].w, height: SIZE_PRESETS[currentPreset].h
        })
      });
      if (!r.ok) throw new Error(await r.text());
      const saved = await r.json();
      // Point the venue's default_flyer_template_id at this template
      if (saved.id) {
        await fetch(`/api/venues/${venueId}/settings/default-template`, {
          method:'PUT', credentials:'include', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ template_id: saved.id })
        });
      }
      setStatus(`✓ "${tplName}" saved as default template`, '#22c55e');
      setFileTitle(tplName); markClean();
      loadTemplateDropdown();
    } catch(e) { setStatus('✗ Save failed: '+e.message, '#ef4444'); }
  }

  async function saveAsNewTemplate() {
    closeSaveMenu();
    const name = await feModal({ title:'Save as New Template', input:true, placeholder:'Template name', value:'My Template', confirmText:'Save Template' });
    if (!name) return;
    try {
      const r = await fetch(`/api/venues/${venueId}/flyers`, {
        method:'POST', credentials:'include', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          name, canvas_data:getCanvasJSON(), thumbnail_data:getThumbnail(),
          is_template:true, size_preset:currentPreset,
          width:SIZE_PRESETS[currentPreset].w, height:SIZE_PRESETS[currentPreset].h
        })
      });
      if (!r.ok) {
        // Audit fix (May 2026 part 5): parse JSON {detail} body instead of
        // returning a raw HTML 500 page. Previously a wall of HTML landed in
        // the status bar; admins couldn't see the actual error.
        let _detail = `HTTP ${r.status}`;
        try { const _j = await r.json(); if (_j && _j.detail) _detail = _j.detail; } catch (_) {
          try { _detail = (await r.text()).slice(0, 200); } catch (_) {}
        }
        throw new Error(_detail);
      }
      setStatus('✓ Template saved', '#22c55e');
      setFileTitle(name); markClean();
      loadTemplateDropdown();
    } catch(e) { setStatus('✗ Save failed: '+e.message, '#ef4444'); }
  }

  async function deleteCurrentTemplate() {
    const btn = document.getElementById('flyerDeleteTplBtn');
    const tid = btn?.dataset?.templateId;
    const tname = btn?.dataset?.templateName || 'this template';
    if (!tid) return;
    if (venueId === 0) { await deleteAdminTemplate(tid, tname); return; }
    const ok = await feModal({ title:'Delete Template', message:`Are you sure you want to permanently delete the template:\n\n"${tname}"?`, confirmText:'Delete', danger:true });
    if (!ok) return;
    try {
      // Audit fix (May 2026 part 5): check res.ok and surface {detail}.
      // Previously a 403 silently "succeeded" — admin saw the template
      // greyed out but it was still in the DB.
      const _dr = await fetch(`/api/venues/${venueId}/flyers/${tid}`, {method:'DELETE',credentials:'include'});
      if (!_dr.ok) {
        let _detail = `HTTP ${_dr.status}`;
        try { const _j = await _dr.json(); if (_j && _j.detail) _detail = _j.detail; } catch (_) {}
        setStatus(`✗ Delete failed: ${_detail}`, '#ef4444');
        return;
      }
      btn.style.display = 'none';
      setStatus(`"${tname}" deleted`, '#f97316');
      loadTemplateDropdown();
    } catch(e) {
      setStatus(`✗ Delete failed: ${(e && e.message) || 'network error'}`, '#ef4444');
    }
  }

  async function deleteCurrentFlyer() {
    if (!currentFlyer?.id) return;
    const fname = currentFlyer.name || 'this flyer';
    const ok = await feModal({ title:'Delete Flyer', message:`Are you sure you want to permanently delete the flyer:\n\n"${fname}"?`, confirmText:'Delete', danger:true });
    if (!ok) return;
    try {
      // Audit fix (May 2026 part 5): same as deleteCurrentTemplate — surface
      // failures instead of treating any response as success.
      const _dr2 = await fetch(`/api/venues/${venueId}/flyers/${currentFlyer.id}`, {method:'DELETE',credentials:'include'});
      if (!_dr2.ok) {
        let _detail = `HTTP ${_dr2.status}`;
        try { const _j = await _dr2.json(); if (_j && _j.detail) _detail = _j.detail; } catch (_) {}
        setStatus(`✗ Delete failed: ${_detail}`, '#ef4444');
        return;
      }
      currentFlyer = null;
      const delBtn = document.getElementById('flyerDeleteFlyerBtn');
      if (delBtn) delBtn.style.display = 'none';
      loadDefaultTemplate();
      setStatus(`"${fname}" deleted`, '#f97316');
      loadRecentDropdown();
    } catch(e) {}
  }

  /* =========================================================
     LOAD RECENT (top 20)
     ========================================================= */
  async function loadRecentDropdown() {
    const sel = document.getElementById('flyerRecentDropdown');
    if (!sel) return;
    while (sel.options.length > 1) sel.remove(1);
    try {
      const r = await fetch(`/api/venues/${venueId}/flyers`, {credentials:'include'});
      if (!r.ok) return;
      const flyers = await r.json();
      // Deduplicate by name — keep most recent (first in list)
      const seen = new Set();
      const deduped = [];
      flyers.slice(0, 30).forEach(f => {
        if (seen.has(f.name)) return;
        seen.add(f.name);
        deduped.push(f);
        const opt = document.createElement('option');
        opt.value = f.id;
        opt.textContent = f.name;
        sel.appendChild(opt);
      });
      // Mirror the same visual thumbnail strip: top 3 most-recent flyers.
      renderPrevFlyerThumbs(deduped.slice(0, 3));
    } catch(e) {}
  }

  /* Thumbnail strip for "Load Template From Previous Flyer" — same idea
     as renderTemplateThumbs but uses the venue's recent saved-flyer
     records. Click a thumbnail = load that flyer (same as loadPrevious). */
  function renderPrevFlyerThumbs(flyers) {
    const wrap = document.getElementById('flyerPrevThumbs');
    if (!wrap) return;
    if (!flyers || !flyers.length) { wrap.innerHTML = ''; return; }
    wrap.innerHTML = flyers.map((f) => {
      const id = String(f.id);
      const nameEsc = (f.name || 'Untitled').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      const thumb = (f.thumbnail_data && /^data:image\//.test(f.thumbnail_data))
        ? `<img src="${f.thumbnail_data}" alt="" style="display:block;width:100%;height:60px;object-fit:cover;background:#0a0a14;">`
        : `<div style="height:60px;background:linear-gradient(135deg,#1a1f2e,#0a0a14);display:flex;align-items:center;justify-content:center;color:#475569;font-size:1.4rem;">🎵</div>`;
      return `
        <div class="fe-prev-thumb" data-flyer-id="${id}" title="${nameEsc}"
             style="cursor:pointer;border:1px solid rgba(255,255,255,0.08);border-radius:6px;overflow:hidden;background:#0a0a14;">
          ${thumb}
          <div style="font-size:0.62rem;color:#cbd5e1;padding:3px 5px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${nameEsc}</div>
        </div>`;
    }).join('');
    wrap.querySelectorAll('.fe-prev-thumb').forEach((el) => {
      el.addEventListener('click', () => {
        const id = el.getAttribute('data-flyer-id');
        if (id) loadPrevious(id);
      });
      el.addEventListener('mouseover', () => { el.style.borderColor = 'rgba(139,92,246,0.55)'; });
      el.addEventListener('mouseout',  () => { el.style.borderColor = ''; });
    });
  }

  /* =========================================================
     SEARCH PREVIOUS
     ========================================================= */
  let _searchTimeout = null;
  function searchPrevious() {
    clearTimeout(_searchTimeout);
    _searchTimeout = setTimeout(async () => {
      const q = document.getElementById('flyerPrevSearch')?.value || '';
      const container = document.getElementById('flyerPrevResults');
      if (!container) return;
      if (q.length < 1) { container.innerHTML = '<div style="padding:8px;font-size:0.7rem;color:var(--text-gray,#64748b);text-align:center;">Type to search...</div>'; return; }
      try {
        const r = await fetch(`/api/venues/${venueId}/flyers/search?q=${encodeURIComponent(q)}`, {credentials:'include'});
        if (!r.ok) return;
        const flyers = await r.json();
        if (!flyers.length) { container.innerHTML = '<div style="padding:8px;font-size:0.7rem;color:var(--text-gray,#64748b);text-align:center;">No results</div>'; return; }
        container.innerHTML = flyers.map(f => {
          const d = f.gig_date ? new Date(f.gig_date+'T00:00:00').toLocaleDateString() : '';
          return `<div class="fe-prev-item" onclick="FE.loadPrevious(${f.id})"><span>${esc(f.name)}</span><span style="font-size:0.65rem;color:var(--text-gray,#64748b);">${d}</span></div>`;
        }).join('');
      } catch(e) {}
    }, 300);
  }

  async function loadPrevious(flyerId) {
    if (!flyerId) return;
    try {
      const r = await fetch(`/api/venues/${venueId}/flyers/${flyerId}`, {credentials:'include'});
      if (!r.ok) return;
      const flyer = await r.json();
      // Sync sidebar (canvas size preset) to whatever the previous flyer
      // was authored at, then load the design.
      applyTemplatePreset(flyer);
      loadCanvasData(flyer.canvas_data, false, () => { refreshLayers(); syncBorderUI(); });
      setStatus(`"${flyer.name || 'Untitled'}" loaded — use Save to keep changes`, '#67e8f9');
      setFileTitle(flyer.name || 'Untitled'); markClean();
      updateNameDisplay(flyer.name);
      // Reset TEMPLATES dropdown to "Load Template" — this is a previous flyer, not a template
      const tplSel = document.getElementById('flyerTemplateSelect');
      if (tplSel) tplSel.value = '';
      // Hide Delete Template button — user loaded a flyer, not a template
      const delTpl = document.getElementById('flyerDeleteTplBtn');
      if (delTpl) delTpl.style.display = 'none';
      // Clear the "previous flyers" search UI so the result list doesn't
      // sit there stranded after the user has loaded their pick.
      const searchInput = document.getElementById('flyerPrevSearch');
      if (searchInput) searchInput.value = '';
      const resultsBox = document.getElementById('flyerPrevResults');
      if (resultsBox) resultsBox.innerHTML = '';
      const recentSel = document.getElementById('flyerRecentDropdown');
      if (recentSel) recentSel.value = '';
    } catch(e) {}
  }

  /* =========================================================
     ADD ELEMENTS
     ========================================================= */
  function addText() { if(!canvas)return; const s=canvas._scale;
    const t=new fabric.Textbox('Your text here',{left:canvas.width/2,top:canvas.height/2,originX:'center',originY:'center',width:canvas.width*0.6,fontSize:24*s,fontFamily:'Arial',fill:'#ffffff',textAlign:'center',editable:true});
    canvas.add(t);canvas.setActiveObject(t);canvas.renderAll(); }
  function addHeading() { if(!canvas)return; const s=canvas._scale;
    const t=new fabric.Textbox('HEADING',{left:canvas.width/2,top:canvas.height/2,originX:'center',originY:'center',width:canvas.width*0.8,fontSize:48*s,fontFamily:'Impact',fontWeight:'bold',fill:'#ffffff',textAlign:'center',editable:true});
    canvas.add(t);canvas.setActiveObject(t);canvas.renderAll(); }
  function addRect() { if(!canvas)return; const s=canvas._scale;
    canvas.add(new fabric.Rect({left:canvas.width/2,top:canvas.height/2,originX:'center',originY:'center',width:200*s,height:100*s,fill:'rgba(139,92,246,0.4)',stroke:'#8b5cf6',strokeWidth:2*s,rx:8*s,ry:8*s}));
    canvas.setActiveObject(canvas.getObjects().pop());canvas.renderAll(); }
  function addCircle() { if(!canvas)return; const s=canvas._scale;
    canvas.add(new fabric.Circle({left:canvas.width/2,top:canvas.height/2,originX:'center',originY:'center',radius:60*s,fill:'rgba(6,182,212,0.4)',stroke:'#06b6d4',strokeWidth:2*s}));
    canvas.setActiveObject(canvas.getObjects().pop());canvas.renderAll(); }
  function addLine() { if(!canvas)return; const s=canvas._scale;
    canvas.add(new fabric.Line([canvas.width*0.2,canvas.height/2,canvas.width*0.8,canvas.height/2],{stroke:'#ffffff',strokeWidth:2*s}));
    canvas.setActiveObject(canvas.getObjects().pop());canvas.renderAll(); }
  function handleImageUpload(input) {
    if(!input.files?.[0]||!canvas) return;
    _addImageFile(input.files[0]);
    input.value='';
  }

  // Add a File-typed image to the canvas — used by both the file picker
  // and the drag-and-drop handler on the canvas wrap.
  function _addImageFile(file) {
    if (!canvas || !file || !file.type || !file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      fabric.Image.fromURL(e.target.result, (img) => {
        if (!img || !canvas) return;
        const sc = Math.min(canvas.width * 0.6 / img.width,
                            canvas.height * 0.4 / img.height, 1);
        img.set({
          left: canvas.width / 2, top: canvas.height / 2,
          originX: 'center', originY: 'center',
          scaleX: sc, scaleY: sc,
        });
        canvas.add(img); canvas.setActiveObject(img); canvas.renderAll();
      });
    };
    reader.readAsDataURL(file);
  }

  /* =========================================================
     BACKGROUND
     ========================================================= */
  function setBgColor(c){
    if(!canvas)return;
    // Remove old bg color rect(s)
    canvas.getObjects().filter(o=>o._isBgColor).forEach(o=>canvas.remove(o));
    // Add a new rect that covers the whole canvas, tagged so it shows in Layers
    const r=new fabric.Rect({
      left:0,top:0,width:canvas.width,height:canvas.height,
      fill:c,selectable:true,evented:true,
      _isBgColor:true,_isBg:true
    });
    canvas.add(r);canvas.sendToBack(r);canvas.renderAll();
    saveState();
  }
  function handleBgUpload(input){if(!input.files?.[0]||!canvas)return;const reader=new FileReader();reader.onload=e=>{
    fabric.Image.fromURL(e.target.result,img=>{const sc=Math.max(canvas.width/img.width,canvas.height/img.height);
      img.set({scaleX:sc,scaleY:sc,originX:'left',originY:'top',_isBg:true});
      canvas.getObjects().filter(o=>o._isBg).forEach(o=>canvas.remove(o));
      canvas.add(img);canvas.sendToBack(img);canvas.renderAll();});};
    reader.readAsDataURL(input.files[0]);input.value='';}
  function clearBgImage(){if(!canvas)return;
    canvas.getObjects().filter(o=>o._isBg).forEach(o=>canvas.remove(o));
    canvas.setBackgroundImage(null,canvas.renderAll.bind(canvas));canvas.renderAll();}

  function toggleBorder(enabled) {
    const controls = document.getElementById('flyerBorderControls');
    if (controls) controls.style.display = enabled ? 'block' : 'none';
    if (!enabled) {
      // Remove border rect
      if (canvas) { canvas.getObjects().filter(o=>o._isBorder).forEach(o=>canvas.remove(o)); canvas.renderAll(); }
    } else {
      updateBorder();
    }
  }

  function updateBorder() {
    if (!canvas) return;
    const colorInput = document.getElementById('flyerBorderColor');
    const thickInput = document.getElementById('flyerBorderThickness');
    const swatch = document.getElementById('flyerBorderColorSwatch');
    const thickLabel = document.getElementById('flyerBorderThickLabel');
    const color = colorInput ? colorInput.value : '#ffffff';
    const thickness = thickInput ? parseInt(thickInput.value) : 12;
    if (swatch) swatch.style.background = color;
    if (thickLabel) thickLabel.textContent = thickness;
    // Remove old border rects
    canvas.getObjects().filter(o => o._isBorder).forEach(o => canvas.remove(o));
    const W = canvas.width, H = canvas.height;
    const t = thickness;
    // 4 solid fill rects — one per edge — guaranteed equal thickness all around
    const edges = [
      { left:0,    top:0,     width:W,   height:t   }, // top
      { left:0,    top:H-t,   width:W,   height:t   }, // bottom
      { left:0,    top:0,     width:t,   height:H   }, // left
      { left:W-t,  top:0,     width:t,   height:H   }, // right
    ];
    edges.forEach(e => {
      const r = new fabric.Rect({
        ...e, fill: color, stroke: null, strokeWidth: 0,
        rx: 0, ry: 0,
        selectable: false, evented: false, _isBorder: true
      });
      canvas.add(r);
      canvas.bringToFront(r);
    });
    canvas.renderAll();
  }

  function keepBorderOnTop() {
    if (!canvas) return;
    canvas.getObjects().filter(o => o._isBorder).forEach(o => canvas.bringToFront(o));
  }

  // Sync border UI state when loading canvas data
  function syncBorderUI() {
    if (!canvas) return;
    const borderObjs = canvas.getObjects().filter(o => o._isBorder);
    const checkbox = document.getElementById('flyerBorderEnabled');
    const controls = document.getElementById('flyerBorderControls');
    const colorInput = document.getElementById('flyerBorderColor');
    const swatch = document.getElementById('flyerBorderColorSwatch');
    const thickInput = document.getElementById('flyerBorderThickness');
    const thickLabel = document.getElementById('flyerBorderThickLabel');
    if (borderObjs.length > 0) {
      const b = borderObjs[0];
      if (checkbox) checkbox.checked = true;
      if (controls) controls.style.display = 'block';
      const col = b.fill || '#ffffff';
      if (colorInput) colorInput.value = col;
      if (swatch) swatch.style.background = col;
      // Thickness = height of top edge rect (first one)
      const t = b.height || 12;
      if (thickInput) thickInput.value = t;
      if (thickLabel) thickLabel.textContent = t;
      // Ensure all border rects are on top
      borderObjs.forEach(o => canvas.bringToFront(o));
    } else {
      if (checkbox) checkbox.checked = false;
      if (controls) controls.style.display = 'none';
    }
  }

  /* =========================================================
     PROPERTIES
     ========================================================= */
  function updateProps(){const obj=canvas?.getActiveObject();if(!obj)return hideProps();
    document.getElementById('flyerProps').style.display='';
    const isText=['textbox','i-text','text'].includes(obj.type);
    document.getElementById('flyerTextProps').style.display=isText?'':'none';
    document.getElementById('flyerShapeProps').style.display=isText?'none':'';
    // Update label to show what is selected
    const lbl=document.querySelector('#flyerProps .fe-label');
    if(lbl){
      const name=isText?'Text Properties':(obj.type==='image'?'Image Properties':(obj.type==='rect'?'Shape Properties':'Object Properties'));
      lbl.textContent=name;
    }
    if(isText){document.getElementById('flyerFontFamily').value=obj.fontFamily||'Arial';
      document.getElementById('flyerFontSize').value=Math.round((obj.fontSize||24)/(canvas._scale||1));
      document.getElementById('flyerTextColor').value=hex(obj.fill);
      document.getElementById('flyerTextStroke').value=hex(obj.stroke||'#000000');
      document.getElementById('flyerStrokeW').value=Math.round((obj.strokeWidth||0)/(canvas._scale||1));
      document.getElementById('flyerBoldBtn').classList.toggle('active',obj.fontWeight==='bold');
      document.getElementById('flyerItalicBtn').classList.toggle('active',obj.fontStyle==='italic');
      document.getElementById('flyerUnderlineBtn').classList.toggle('active',!!obj.underline);
    } else {document.getElementById('flyerShapeFill').value=hex(obj.fill);
      document.getElementById('flyerShapeStroke').value=hex(obj.stroke||'#ffffff');
      const op=Math.round((obj.opacity||1)*100);
      document.getElementById('flyerOpacity').value=op;document.getElementById('flyerOpacityVal').textContent=op;}}
  function hideProps(){const e=document.getElementById('flyerProps');if(e)e.style.display='none';}
  function hex(c){if(!c||typeof c!=='string')return'#000000';if(c.startsWith('#'))return c.length>7?c.slice(0,7):c;
    const m=c.match(/(\d+)/g);if(m&&m.length>=3)return'#'+m.slice(0,3).map(n=>parseInt(n).toString(16).padStart(2,'0')).join('');return'#000000';}
  function setProp(p,v){const o=canvas?.getActiveObject();if(o){o.set(p,v);canvas.renderAll();}}
  function setFontSize(v){const o=canvas?.getActiveObject();if(o){o.set('fontSize',parseInt(v)*(canvas._scale||1));canvas.renderAll();}}
  function setTextStroke(c){setProp('stroke',c);}
  function setStrokeWidth(v){const o=canvas?.getActiveObject();if(o){o.set('strokeWidth',parseInt(v)*(canvas._scale||1));canvas.renderAll();}}
  function setAlign(a){setProp('textAlign',a);}
  function toggleBold(){const o=canvas?.getActiveObject();if(o){o.set('fontWeight',o.fontWeight==='bold'?'normal':'bold');canvas.renderAll();updateProps();}}
  function toggleItalic(){const o=canvas?.getActiveObject();if(o){o.set('fontStyle',o.fontStyle==='italic'?'normal':'italic');canvas.renderAll();updateProps();}}
  function toggleUnderline(){const o=canvas?.getActiveObject();if(o){o.set('underline',!o.underline);canvas.renderAll();updateProps();}}
  function setOpacity(v){const o=canvas?.getActiveObject();if(o){o.set('opacity',parseInt(v)/100);canvas.renderAll();}document.getElementById('flyerOpacityVal').textContent=v;}
  function layer(method){const o=canvas?.getActiveObject();if(o){canvas[method](o);canvas.renderAll();}}
  function deleteSelected(){const o=canvas?.getActiveObject();if(o){canvas.remove(o);canvas.discardActiveObject();canvas.renderAll();hideProps();}}

  let _clipboard = null;
  function copySelected() {
    const obj = canvas?.getActiveObject(); if (!obj) return;
    obj.clone(function(cloned) { _clipboard = cloned; });
  }
  function cutSelected() {
    const obj = canvas?.getActiveObject(); if (!obj) return;
    obj.clone(function(cloned) { _clipboard = cloned; });
    canvas.remove(obj); canvas.discardActiveObject(); canvas.renderAll();
  }
  function pasteClipboard() {
    if (!_clipboard || !canvas) return;
    _clipboard.clone(function(cloned) {
      cloned.set({ left: cloned.left + 20, top: cloned.top + 20 });
      if (cloned.type === 'activeSelection') {
        cloned.canvas = canvas;
        cloned.forEachObject(function(o) { canvas.add(o); });
        cloned.setCoords();
      } else {
        canvas.add(cloned);
      }
      _clipboard.top += 20; _clipboard.left += 20;
      canvas.setActiveObject(cloned); canvas.renderAll();
    }, ['_tplVar']);
  }
  function centerOnCanvas() {
    const obj = canvas?.getActiveObject(); if (!obj) return;
    obj.set({ left: canvas.width / 2, top: canvas.height / 2, originX: 'center', originY: 'center' });
    obj.setCoords(); canvas.renderAll();
  }

  /* Move the currently-selected object by (dx, dy) pixels. Used by the
     ← → ↑ ↓ buttons in the properties panel for fine-grained positioning. */
  function nudgeSelected(dx, dy) {
    const obj = canvas?.getActiveObject(); if (!obj) return;
    obj.set({ left: (obj.left || 0) + dx, top: (obj.top || 0) + dy });
    obj.setCoords();
    canvas.renderAll();
  }

  /* =========================================================
     SIZE
     ========================================================= */
  /* When loading a saved template/flyer, switch the canvas to the size
     preset that template was authored at (and sync the sidebar dropdown
     so the UI matches). Returns true if a size change happened. */
  function applyTemplatePreset(tpl) {
    if (!tpl) return false;
    let preset = tpl.size_preset;
    // Fallback: infer from width/height if size_preset is missing.
    if (!preset && tpl.width && tpl.height) {
      for (const [k, v] of Object.entries(SIZE_PRESETS)) {
        if (v.w === tpl.width && v.h === tpl.height) { preset = k; break; }
      }
    }
    if (!preset || !SIZE_PRESETS[preset] || preset === currentPreset) return false;
    currentPreset = preset;
    const sel = document.getElementById('flyerSizePreset');
    if (sel) sel.value = preset;
    // Re-init the canvas at the new size BEFORE the template's JSON
    // gets loaded — otherwise the JSON would be drawn at the wrong
    // dimensions and we'd be back to the "stretched / off-canvas" bug.
    initCanvas();
    return true;
  }

  function changeSize(preset) {
    // Dispose canvas, init at new size, reload JSON (objects come back
    // at their old coordinates). After the load callback fires, do two
    // targeted fixups — re-fit the border to the new canvas dimensions
    // and proportionally reposition any non-background images so a
    // centered logo stays centered, a top-right image stays top-right,
    // etc. Both are wrapped in try/catch so a bug here can never blank
    // the canvas — the design was already restored by loadCanvasData.
    const oldW = canvas ? canvas.width  : 0;
    const oldH = canvas ? canvas.height : 0;
    currentPreset = preset;
    const json = getCanvasJSON();
    initCanvas();
    if (json && json !== '{}' && json !== '{"objects":[]}') {
      loadCanvasData(json, true, () => {
        try { refitBorderToCanvas(); } catch (e) { console.error('refit border:', e); }
        try { repositionImagesProportionally(oldW, oldH); }
        catch (e) { console.error('reposition images:', e); }
        canvas && canvas.renderAll();
      });
    } else {
      loadDefaultTemplate();
    }
  }

  /* Re-draw the border (4 edge rects) at the new canvas dimensions.
     Pulls the existing color + thickness from the rects we just loaded,
     then calls updateBorder() to wipe + redraw flush to the new edges. */
  function refitBorderToCanvas() {
    if (!canvas) return;
    const borders = canvas.getObjects().filter((o) => o._isBorder);
    if (!borders.length) return;
    const first = borders[0];
    const color = first.fill || '#ffffff';
    const thickness = Math.min(first.width || 12, first.height || 12);
    const colorInput = document.getElementById('flyerBorderColor');
    const thickInput = document.getElementById('flyerBorderThickness');
    if (colorInput) colorInput.value = color;
    if (thickInput) thickInput.value = thickness;
    updateBorder();
  }

  /* Maintain the relative position of each non-background image when
     the canvas size changes. A logo at 50% from the left, 10% from the
     top on the old canvas → same 50% / 10% on the new canvas. Skips
     full-bleed images (those re-fill via the background logic). */
  function repositionImagesProportionally(oldW, oldH) {
    if (!canvas || !oldW || !oldH) return;
    const newW = canvas.width, newH = canvas.height;
    if (newW === oldW && newH === oldH) return;
    canvas.getObjects().forEach((o) => {
      if (o.type !== 'image') return;
      if (o._isBg || o._isBgColor) return;
      const b = o.getBoundingRect(true);
      const cx = b.left + b.width / 2;
      const cy = b.top  + b.height / 2;
      const newCx = (cx / oldW) * newW;
      const newCy = (cy / oldH) * newH;
      o.setPositionByOrigin({ x: newCx, y: newCy }, 'center', 'center');
      o.setCoords();
    });
  }

  /* Re-fit existing objects into the new canvas size. Two-step algorithm:
       1. Backgrounds, borders, and dark overlays stretch to fill the new
          canvas edge-to-edge (they were doing that on the old canvas).
       2. Everything else is scaled uniformly by the smaller of
          (newW/oldW, newH/oldH) — so the design fits the more
          constraining axis — then translated so its bounding box is
          centered in the new canvas.
     Result: a flyer designed for 4:5 still reads correctly at 16:9 with
     content centered, no clipping. */
  function fitObjectsToCanvas(oldW, oldH) {
    if (!canvas || !oldW || !oldH) return;
    const newW = canvas.width;
    const newH = canvas.height;
    if (newW === oldW && newH === oldH) return;
    const objs = canvas.getObjects();
    if (!objs.length) return;

    const isFullBleed = (o) =>
      o._isBg || o._isBgColor || o._isBorder || o._isDarkOverlay;

    // 1) Stretch full-bleed objects to the new canvas. For images use
    // aspect-preserving "cover" fit (same as loadDefaultTemplate) so the
    // background photo doesn't get distorted.
    objs.forEach((o) => {
      if (!isFullBleed(o)) return;
      if (o.type === 'image' && o.width && o.height) {
        const sc = Math.max(newW / o.width, newH / o.height);
        o.set({
          left: 0, top: 0, originX: 'left', originY: 'top',
          scaleX: sc, scaleY: sc,
        });
      } else {
        o.set({
          left: 0, top: 0, originX: 'left', originY: 'top',
          width: newW, height: newH, scaleX: 1, scaleY: 1,
        });
      }
      o.setCoords();
    });

    // 2) Scale and center content (everything that isn't full-bleed).
    const content = objs.filter((o) => !isFullBleed(o));
    if (!content.length) { canvas.renderAll(); return; }

    const fit = Math.min(newW / oldW, newH / oldH);
    if (!isFinite(fit) || fit <= 0) { canvas.renderAll(); return; }

    // Scale coords + dimensions for each content object.
    content.forEach((o) => {
      o.left = (o.left || 0) * fit;
      o.top  = (o.top  || 0) * fit;
      o.scaleX = (o.scaleX || 1) * fit;
      o.scaleY = (o.scaleY || 1) * fit;
      if (o.fontSize)    o.fontSize    = o.fontSize    * fit;
      if (o.strokeWidth) o.strokeWidth = o.strokeWidth * fit;
      o.setCoords();
    });

    // Compute the scaled bounding box and translate to center it.
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    content.forEach((o) => {
      const b = o.getBoundingRect(true);
      if (b.left < minX) minX = b.left;
      if (b.top  < minY) minY = b.top;
      if (b.left + b.width  > maxX) maxX = b.left + b.width;
      if (b.top  + b.height > maxY) maxY = b.top  + b.height;
    });
    const bboxCx = (minX + maxX) / 2;
    const bboxCy = (minY + maxY) / 2;
    const dx = newW / 2 - bboxCx;
    const dy = newH / 2 - bboxCy;
    if (isFinite(dx) && isFinite(dy)) {
      content.forEach((o) => {
        o.left = (o.left || 0) + dx;
        o.top  = (o.top  || 0) + dy;
        o.setCoords();
      });
    }

    canvas.renderAll();
  }

  /* =========================================================
     SAVE
     ========================================================= */
  function toggleSaveMenu(){const m=document.getElementById('flyerSaveMenu');if(m)m.style.display=m.style.display==='block'?'none':'block';}
  function closeSaveMenu(){const m=document.getElementById('flyerSaveMenu');if(m)m.style.display='none';}

  async function save() {
    closeSaveMenu();
    if(!canvas||!venueId)return;
    const name=currentFlyer?.name||buildFlyerName();
    const payload={gig_id:gigId,artist_id:gigInfo?.artist_id||null,name,
      canvas_data:getCanvasJSON(),thumbnail_data:getThumbnail(),
      size_preset:currentPreset,width:SIZE_PRESETS[currentPreset].w,height:SIZE_PRESETS[currentPreset].h};
    try {
      setStatus('Saving...','var(--text-gray)');
      let r;
      if(currentFlyer?.id){
        r=await fetch(`/api/venues/${venueId}/flyers/${currentFlyer.id}`,{method:'PUT',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      } else {
        // Check for duplicate name
        try {
          const dc=await fetch(`/api/venues/${venueId}/flyers/search?q=${encodeURIComponent(name)}`,{credentials:'include'});
          if(dc.ok){const dupes=await dc.json();const exact=dupes.find(f=>f.name===name&&!f.is_template);
            if(exact){const ow=await feModal({title:'Flyer Already Exists',message:'A flyer named "'+name+'" already exists. Overwrite it?',confirmText:'Overwrite'});
              if(ow){r=await fetch(`/api/venues/${venueId}/flyers/${exact.id}`,{method:'PUT',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                if(!r.ok)throw new Error(await r.text());currentFlyer={id:exact.id,name};
                setStatus('\u2713 "'+name+'" overwritten','#22c55e');setFileTitle(name);markClean();document.getElementById('flyerDeleteFlyerBtn').style.display='';loadRecentDropdown();return;}else{return;}}}
        }catch(e){}
        r=await fetch(`/api/venues/${venueId}/flyers`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      }
      if(!r.ok){const err=await r.text();throw new Error(err);}
      const data=await r.json();
      if(!currentFlyer)currentFlyer={id:data.id,name};
      else currentFlyer.name=name;
      setStatus(`✓ "${name}" saved`,'#22c55e');
      setFileTitle(name); markClean();
      const delBtn=document.getElementById('flyerDeleteFlyerBtn');
      if(delBtn)delBtn.style.display='';
      loadRecentDropdown();
    } catch(e){console.error('Save:',e);setStatus('✗ Save failed: '+e.message,'#ef4444');}
  }

  async function saveAsNew() {
    closeSaveMenu();
    let name=await feModal({title:'Save Gig Flyer As',input:true,placeholder:'Flyer name',value:buildFlyerName(),confirmText:'Save'});
    if(!name)return;
    // Check for duplicate
    try{const dc=await fetch(`/api/venues/${venueId}/flyers/search?q=${encodeURIComponent(name)}`,{credentials:'include'});
      if(dc.ok){const dupes=await dc.json();const exact=dupes.find(f=>f.name===name&&!f.is_template);
        if(exact){const ow=await feModal({title:'Flyer Already Exists',message:'A flyer named "'+name+'" already exists. Overwrite it, or choose a different name?',confirmText:'Overwrite',cancelText:'Rename'});
          if(ow){const payload={gig_id:gigId,artist_id:gigInfo?.artist_id||null,name,canvas_data:getCanvasJSON(),thumbnail_data:getThumbnail(),
            size_preset:currentPreset,width:SIZE_PRESETS[currentPreset].w,height:SIZE_PRESETS[currentPreset].h};
            const r=await fetch(`/api/venues/${venueId}/flyers/${exact.id}`,{method:'PUT',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
            if(!r.ok)throw new Error(await r.text());currentFlyer={id:exact.id,name};
            setStatus('\u2713 "'+name+'" overwritten','#22c55e');document.getElementById('flyerDeleteFlyerBtn').style.display='';loadRecentDropdown();return;}
          else{return saveAsNew();}}}}catch(e){}
    const payload={gig_id:gigId,artist_id:gigInfo?.artist_id||null,name,
      canvas_data:getCanvasJSON(),thumbnail_data:getThumbnail(),
      size_preset:currentPreset,width:SIZE_PRESETS[currentPreset].w,height:SIZE_PRESETS[currentPreset].h};
    try {
      const r=await fetch(`/api/venues/${venueId}/flyers`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(!r.ok)throw new Error(await r.text());
      const data=await r.json();
      currentFlyer={id:data.id,name};
      setStatus(`✓ "${name}" saved`,'#22c55e');
      const delBtn=document.getElementById('flyerDeleteFlyerBtn');
      if(delBtn)delBtn.style.display='';
      loadRecentDropdown();
    } catch(e){setStatus('✗ Save failed: '+e.message,'#ef4444');}
  }

  function setStatus(msg,color){
    // Toast: a floating, clearly-visible popup near the top of the editor.
    // Success ('✓ ...') auto-dismisses after 2.5s; errors ('✗ ...') stay 5s.
    feShowToast(msg, color);
    // Title-bar flag: also flash in the small title-bar status text for context.
    const flag=document.getElementById('flyerDirtyFlag');
    if(flag){
      flag.textContent=msg; flag.style.color=color||'#22c55e'; flag.style.display='';
      const isErr = msg.startsWith('✗');
      setTimeout(()=>{ if(_dirty){flag.textContent='(Not Saved)';flag.style.color='#f59e0b';}else{flag.style.display='none';}}, isErr ? 5000 : 2500);
    }
  }

  function feShowToast(msg, color){
    let toast = document.getElementById('flyerToast');
    if(!toast){
      toast = document.createElement('div');
      toast.id = 'flyerToast';
      toast.style.cssText = 'position:absolute;top:60px;left:50%;transform:translateX(-50%);'
        + 'padding:10px 18px;border-radius:8px;font-size:0.9rem;font-weight:600;'
        + 'box-shadow:0 6px 20px rgba(0,0,0,0.4);z-index:100000;'
        + 'opacity:0;transition:opacity 0.18s;pointer-events:none;'
        + 'border:1px solid rgba(255,255,255,0.15);';
      // Mount inside the editor overlay so it sits above the canvas.
      const ov = document.getElementById('flyerEditorOverlay') || document.body;
      ov.appendChild(toast);
    }
    const isErr = (msg || '').startsWith('✗');
    toast.textContent = msg;
    toast.style.background = isErr ? 'rgba(239,68,68,0.95)' : 'rgba(34,197,94,0.95)';
    toast.style.color = '#ffffff';
    toast.style.opacity = '1';
    clearTimeout(feShowToast._t);
    feShowToast._t = setTimeout(() => { toast.style.opacity = '0'; }, isErr ? 5000 : 2500);
  }

  /* =========================================================
     GIG VARIABLES — re-add tagged elements to canvas
     ========================================================= */
  function toggleGigVars() {
    const m = document.getElementById('flyerGigVarsMenu');
    if (m) m.style.display = m.style.display === 'block' ? 'none' : 'block';
  }

  // FIX (May 2026): multi-slot artist logo picker + add helper.
  // Tags each added image with `_tplArtistId` so the cancel-cleanup code
  // can remove only the cancelled artist's logo, leaving others intact.
  function _showArtistLogoPicker(slots) {
    const existing = document.getElementById('_artistLogoPickerOverlay');
    if (existing) existing.remove();
    const ov = document.createElement('div');
    ov.id = '_artistLogoPickerOverlay';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:99999;display:flex;align-items:center;justify-content:center;';
    const card = document.createElement('div');
    card.style.cssText = 'background:#1a1d2e;border:1px solid rgba(139,92,246,0.5);border-radius:12px;padding:20px 24px;max-width:480px;width:90%;max-height:70vh;overflow-y:auto;';
    card.innerHTML = `
      <div style="font-size:1.05rem;font-weight:600;color:#fff;margin-bottom:6px;">Add Artist Logo</div>
      <div style="font-size:0.85rem;color:#9ca3af;margin-bottom:14px;">Pick which artist's logo to add to the flyer:</div>
      <div id="_artistLogoPickerList" style="display:flex;flex-direction:column;gap:8px;"></div>
      <div style="margin-top:14px;text-align:right;">
        <button class="btn ghost" type="button" id="_artistLogoPickerCancel">Cancel</button>
      </div>
    `;
    ov.appendChild(card);
    document.body.appendChild(ov);
    const list = card.querySelector('#_artistLogoPickerList');
    slots.forEach(slot => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.style.cssText = 'display:flex;align-items:center;gap:12px;padding:8px 12px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;cursor:pointer;text-align:left;transition:all 0.15s;';
      btn.onmouseover = () => { btn.style.background = 'rgba(139,92,246,0.15)'; btn.style.borderColor = 'rgba(139,92,246,0.5)'; };
      btn.onmouseout  = () => { btn.style.background = 'rgba(255,255,255,0.04)'; btn.style.borderColor = 'rgba(255,255,255,0.1)'; };
      btn.innerHTML = `
        <img src="${slot.artist_picture_url}" alt="" style="width:40px;height:40px;border-radius:6px;object-fit:cover;background:#000;">
        <div style="flex:1;">
          <div style="font-weight:600;">${(slot.artist_name || 'Artist').replace(/</g,'&lt;')}</div>
          <div style="font-size:0.75rem;color:#9ca3af;">Slot ${slot.slot_number}</div>
        </div>
      `;
      btn.onclick = () => {
        ov.remove();
        _addArtistLogoForSlot(slot);
      };
      list.appendChild(btn);
    });
    card.querySelector('#_artistLogoPickerCancel').onclick = () => ov.remove();
    ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  }

  function _addArtistLogoForSlot(slot) {
    if (!canvas || !slot || !slot.artist_picture_url) return;
    const s = canvas._scale || 0.385, cw = canvas.width, ch = canvas.height;
    const W = cw * 0.94;
    fabricLoadImage(slot.artist_picture_url, function(img) {
      if (!img || !canvas) return;
      const targetW = W * 0.60;  // smaller than default since multi-slot may stack several
      const targetH = ch * 0.35;
      const sc = Math.min(targetW / img.width, targetH / img.height, 1);
      img.set({
        left: cw / 2, top: ch * 0.55,
        originX: 'center', originY: 'center',
        scaleX: sc, scaleY: sc,
        _tplVar: 'artist_logo',
        _tplArtistId: slot.artist_id,  // KEY: enables cancel-cleanup to find this logo
        shadow: new fabric.Shadow({color: 'rgba(0,0,0,0.8)', blur: 20})
      });
      canvas.add(img);
      canvas.setActiveObject(img);
      canvas.renderAll();
      setStatus(`Added ${slot.artist_name || 'artist'}'s logo — drag to position`, '#67e8f9');
    });
  }

  function addGigVar(varName) {
    document.getElementById('flyerGigVarsMenu').style.display = 'none';
    if (!canvas) return;
    const s = canvas._scale || 0.385, cw = canvas.width, ch = canvas.height;
    const W = cw * 0.94, L = cw * 0.03;
    const FONT = 'Trebuchet MS', GREY = 'rgba(200,200,210,0.9)';

    // FIX (May 2026): for multi-slot gigs, "Artist Logo" should let the venue pick
    // WHICH artist's logo to add. Without this they only got slot 1's artist
    // and had no way to add the other artists. Picker shows all booked artists
    // with logos; clicking one adds that artist's logo with the artist_id tagged
    // so the cancel-cleanup code can find and remove it later if that artist is
    // cancelled from the gig.
    if (varName === 'artist_logo' && gigInfo && gigInfo.is_multi_slot && Array.isArray(gigInfo.slots)) {
      const eligibleSlots = gigInfo.slots.filter(s => s.artist_id && s.artist_picture_url);
      if (eligibleSlots.length > 1) {
        _showArtistLogoPicker(eligibleSlots);
        return;
      } else if (eligibleSlots.length === 1) {
        _addArtistLogoForSlot(eligibleSlots[0]);
        return;
      }
      // 0 eligible slots — fall through to placeholder zone behavior
    }

    if (varName === 'venue_logo' || varName === 'artist_logo') {
      // Remove any existing object tagged with this var only
      const toRemove = canvas.getObjects().filter(obj => {
        if (obj._tplVar === varName) return true;
        // Only remove placeholder/zone rects that belong to this specific varName
        if ((obj._isLogoPlaceholderRect || obj._isZoneRect) && obj._tplVar === varName) return true;
        return false;
      });
      toRemove.forEach(o => canvas.remove(o));

      const url = varName === 'venue_logo' ? gigInfo?.venue_picture_url : gigInfo?.artist_picture_url;
      if (url) {
        fabricLoadImage(url, function(img) {
          if (!img || !canvas) return;
          // Default size: 80% wide, maintaining aspect ratio, centered in lower half
          const targetW = W * 0.80;
          const targetH = ch * 0.45;
          const sc = Math.min(targetW / img.width, targetH / img.height, 1);
          img.set({ left:cw/2, top:ch*0.55, originX:'center', originY:'center', scaleX:sc, scaleY:sc,
            _tplVar:varName, shadow: new fabric.Shadow({color:'rgba(0,0,0,0.8)',blur:20}) });
          canvas.add(img); canvas.setActiveObject(img); canvas.renderAll();
        });
      } else {
        // No image yet — add a resizable zone rect showing where the logo will appear
        const isArtist = varName === 'artist_logo';
        const label = isArtist ? 'ARTIST LOGO' : 'VENUE LOGO';
        const placeholderH = ch * 0.30;
        const placeholderW = W * 0.75;
        const zoneLeft = cw/2 - placeholderW/2;
        const zoneTop  = ch * 0.38;
        const zone = new fabric.Rect({
          left: zoneLeft, top: zoneTop,
          width: placeholderW, height: placeholderH,
          fill: 'rgba(139,92,246,0.07)',
          stroke: 'rgba(139,92,246,0.7)', strokeWidth: Math.max(2, 2 * s),
          strokeDashArray: [8 * s, 5 * s],
          rx: 6 * s, ry: 6 * s,
          selectable: true, evented: true,
          _tplVar: varName,
          _isZoneRect: true,
          _zoneLabel: label
        });
        // Draw label text centered inside the rect on every render
        attachZoneRender(zone);
        canvas.add(zone);
        canvas.setActiveObject(zone);
        canvas.renderAll();
        setStatus(`${isArtist ? 'Artist' : 'Venue'} logo zone added — resize this box to set where the logo will appear`, '#67e8f9');
      }
      return;
    }

    const defs = {
      venue_name:  { text:(gigInfo?.venue_name||'VENUE NAME').toUpperCase(), fontSize:120, fill:'#ffffff', sw:2 },
      location:    { text:buildLocationText()||'123 Main St\nCity, State 00000', fontSize:45, fill:GREY, sw:0 },
      live_music:  { text:'★  L I V E   M U S I C  ★', fontSize:90, fill:GREY, sw:4, ls:120 },
      date:        { text:gigInfo?.date ? new Date(gigInfo.date+'T00:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'}) : 'DATE', fontSize:85, fill:'#ffffff', sw:4 },
      time:        { text:buildTimeText()||'TIME', fontSize:85, fill:'#ffffff', sw:4 },
      artist_name: { text:gigInfo?.artist_name ? gigInfo.artist_name.toUpperCase() : 'ARTIST NAME', fontSize:200, fill:'#ffffff', sw:4 }
    };
    const d = defs[varName]; if (!d) return;
    const tb = new fabric.Textbox(d.text, {
      left:L, top:ch*0.4, width:W,
      fontSize:d.fontSize*s, fontFamily:FONT, fontWeight:'bold', fill:d.fill, textAlign:'center',
      stroke:'#000000', strokeWidth:d.sw*s, letterSpacing:d.ls||0, _tplVar:varName,
      shadow: new fabric.Shadow({color:'rgba(0,0,0,0.7)',blur:10})
    });
    canvas.add(tb); canvas.setActiveObject(tb); canvas.renderAll();
  }

  /* =========================================================
     UNDO / REDO
     ========================================================= */
  function saveState() {
    if (_undoPaused || !canvas) return;
    const json = JSON.stringify(canvas.toJSON(['_tplVar', '_isBg', '_isBgColor', '_isBorder', '_isDarkOverlay', '_isLogoPlaceholderRect', '_isZoneRect', '_zoneLabel', '_layerLocked']));
    // Don't push duplicate states
    if (undoStack.length > 0 && undoStack[undoStack.length - 1] === json) return;
    undoStack.push(json);
    if (undoStack.length > MAX_UNDO) undoStack.shift();
    redoStack.length = 0;
    markDirty();
    updateUndoRedoBtns();
  }

  function markDirty() {
    _dirty = true;
    const flag = document.getElementById('flyerDirtyFlag');
    if (flag) flag.style.display = '';
  }
  function markClean() {
    _dirty = false;
    const flag = document.getElementById('flyerDirtyFlag');
    if (flag) flag.style.display = 'none';
  }
  function setFileTitle(name) {
    const el = document.getElementById('flyerFileTitle');
    if (el) el.textContent = name;
  }
  function _loadJsonWithTplVars(jsonStr, callback) {
    const json = typeof jsonStr === 'string' ? JSON.parse(jsonStr) : jsonStr;
    const tplVars = (json.objects || []).map(o => o._tplVar || null);
    canvas.loadFromJSON(json, function() {
      const objs = canvas.getObjects();
      for (let i = 0; i < objs.length && i < tplVars.length; i++) {
        if (tplVars[i]) objs[i]._tplVar = tplVars[i];
      }
      if (callback) callback();
    });
  }
  function undo() {
    if (!canvas || undoStack.length === 0) return;
    redoStack.push(JSON.stringify(canvas.toJSON(['_tplVar', '_isBg', '_isBgColor', '_isBorder', '_isDarkOverlay', '_isLogoPlaceholderRect', '_isZoneRect', '_zoneLabel', '_layerLocked'])));
    const prev = undoStack.pop();
    _undoPaused = true;
    _loadJsonWithTplVars(prev, function() { canvas.renderAll(); _undoPaused = false; updateUndoRedoBtns(); });
  }
  function redo() {
    if (!canvas || redoStack.length === 0) return;
    undoStack.push(JSON.stringify(canvas.toJSON(['_tplVar', '_isBg', '_isBgColor', '_isBorder', '_isDarkOverlay', '_isLogoPlaceholderRect', '_isZoneRect', '_zoneLabel', '_layerLocked'])));
    const next = redoStack.pop();
    _undoPaused = true;
    _loadJsonWithTplVars(next, function() { canvas.renderAll(); _undoPaused = false; updateUndoRedoBtns(); });
  }
  function updateUndoRedoBtns() {
    const ub = document.getElementById('flyerUndoBtn');
    const rb = document.getElementById('flyerRedoBtn');
    if (ub) { ub.disabled = undoStack.length === 0; ub.style.opacity = undoStack.length ? '1' : '0.4'; }
    if (rb) { rb.disabled = redoStack.length === 0; rb.style.opacity = redoStack.length ? '1' : '0.4'; }
  }
  function initUndoRedo() {
    if (!canvas) return;
    undoStack.length = 0; redoStack.length = 0;
    canvas.on('object:modified', saveState);
    canvas.on('object:added', saveState);
    canvas.on('object:removed', saveState);
    updateUndoRedoBtns();
  }

  /* =========================================================
     UPLOAD OWN FLYER (full image replacement)
     ========================================================= */
  function uploadOwnFlyer(input) {
    if (!input.files || !input.files[0] || !canvas) return;
    const file = input.files[0];
    const reader = new FileReader();
    reader.onload = function(ev) {
      saveState(); // Save current state before replacing
      canvas.clear();
      canvas.backgroundColor = '#0a0a14';
      fabric.Image.fromURL(ev.target.result, function(img) {
        if (!img || !canvas) return;
        const cw = canvas.width, ch = canvas.height;
        // Scale to fill canvas
        const sc = Math.max(cw / img.width, ch / img.height);
        img.set({ scaleX:sc, scaleY:sc, originX:'center', originY:'center',
          left:cw/2, top:ch/2 });
        canvas.add(img);
        canvas.renderAll();
        const displayName = file.name.replace(/\.[^.]+$/, '');
        setFileTitle(displayName);
        currentFlyer = null;
        setStatus(`✓ Flyer image loaded: ${file.name}`, '#22c55e');
      });
    };
    reader.readAsDataURL(file);
    input.value = '';
  }

  /* =========================================================
     EXPORT
     ========================================================= */
  function toggleExport(){const m=document.getElementById('flyerExportMenu');if(m)m.style.display=m.style.display==='block'?'none':'block';}
  function exportAs(format){
    document.getElementById('flyerExportMenu').style.display='none';
    if(!canvas)return;
    // Base multiplier brings the small display canvas back up to the
    // preset's real dimensions (1080×1350 etc. — fine for social).
    const baseMult = canvas._realWidth / canvas.width;
    // For print quality, target ~2× real dimensions but cap the longest
    // output edge at 3000 px. The original 3× multiplier produced 5000+
    // px outputs that ran out of canvas memory on some browsers.
    const longestDisplay = Math.max(canvas.width, canvas.height);
    const maxLongSidePx = 3000;
    const capMult = maxLongSidePx / longestDisplay;
    const printMult = Math.min(baseMult * 2, capMult);
    const name = currentFlyer?.name || buildFlyerName();
    canvas.discardActiveObject(); canvas.renderAll();
    const safeExport = (opts, ext, label) => {
      try {
        const url = canvas.toDataURL(opts);
        dl(url, name + ext);
      } catch (err) {
        console.error('[FlyerEditor] export ' + label + ' failed:', err);
        setStatus('Export failed — try a smaller canvas size or PNG (Web)', '#ef4444');
      }
    };
    if (format === 'png')        safeExport({format:'png',  multiplier:baseMult},                      '.png',       'PNG');
    else if (format === 'jpg')   safeExport({format:'jpeg', quality:0.92, multiplier:baseMult},        '.jpg',       'JPG');
    else if (format === 'png-print') safeExport({format:'png', multiplier:printMult},                  '_print.png', 'PNG print');
    else if (format === 'pdf')   exportPDF(name, baseMult);
  }
  function dl(u,f){const a=document.createElement('a');a.href=u;a.download=f;document.body.appendChild(a);a.click();document.body.removeChild(a);}
  async function exportPDF(name,mult){if(typeof window.jspdf==='undefined')await loadScript('https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js');
    const{jsPDF}=window.jspdf;const rw=canvas._realWidth,rh=canvas._realHeight;
    const pdf=new jsPDF({orientation:rw>rh?'landscape':'portrait',unit:'px',format:[rw,rh]});
    pdf.addImage(canvas.toDataURL({format:'png',multiplier:mult}),'PNG',0,0,rw,rh);pdf.save(name+'.pdf');}

  /* =========================================================
     PREVIOUS FLYERS TAB
     ========================================================= */
  let _allFlyers=[],_sortCol='updated_at',_sortDir='desc';
  async function loadAllFlyers(){try{const r=await fetch(`/api/venues/${venueId}/flyers`,{credentials:'include'});if(r.ok)_allFlyers=await r.json();}catch(e){}}
  function renderPreviousTab(){const container=document.getElementById('flyerPreviousList'),countEl=document.getElementById('flyerCount');if(!container)return;
    const q=(document.getElementById('flyerPrevTabSearch')?.value||'').toLowerCase();
    let list=_allFlyers;if(q)list=list.filter(f=>(f.name||'').toLowerCase().includes(q)||(f.artist_name||'').toLowerCase().includes(q)||(f.gig_date||'').includes(q));
    list.sort((a,b)=>{let av=a[_sortCol]||'',bv=b[_sortCol]||'';if(typeof av==='string')av=av.toLowerCase();if(typeof bv==='string')bv=bv.toLowerCase();
      return _sortDir==='asc'?(av<bv?-1:av>bv?1:0):(av>bv?-1:av<bv?1:0);});
    if(countEl)countEl.textContent=`${list.length} flyer${list.length!==1?'s':''}`;
    if(!list.length){container.innerHTML='<div style="text-align:center;padding:40px;color:var(--text-gray);font-size:0.85rem;">No flyers found</div>';return;}
    const hdr=(col,label)=>{const arrow=_sortCol===col?(_sortDir==='asc'?' ▲':' ▼'):'';
      return `<th onclick="FE.sortBy('${col}')" style="text-align:left;padding:6px 8px;font-size:0.7rem;font-weight:700;color:var(--cyan,#06b6d4);text-transform:uppercase;cursor:pointer;white-space:nowrap;border-bottom:1px solid var(--border);">${label}${arrow}</th>`;};
    container.innerHTML=`<table style="width:100%;border-collapse:collapse;"><thead><tr><th style="width:44px;padding:6px;border-bottom:1px solid var(--border);"></th>
      ${hdr('name','Name')}${hdr('artist_name','Artist')}${hdr('gig_date','Date')}${hdr('updated_at','Modified')}
      <th style="padding:6px 8px;font-size:0.7rem;font-weight:700;color:var(--cyan);text-transform:uppercase;border-bottom:1px solid var(--border);">Actions</th>
    </tr></thead><tbody>${list.map(f=>{
      const thumb=f.thumbnail_data?`<img src="${f.thumbnail_data}" style="width:36px;height:46px;object-fit:cover;border-radius:3px;border:1px solid var(--border);">`:'<div style="width:36px;height:46px;background:rgba(255,255,255,0.03);border-radius:3px;border:1px solid var(--border);"></div>';
      const gd=f.gig_date?new Date(f.gig_date+'T00:00:00').toLocaleDateString():'—';const md=f.updated_at?new Date(f.updated_at).toLocaleDateString():'';
      return `<tr style="border-bottom:1px solid rgba(255,255,255,0.03);" onmouseover="this.style.background='rgba(255,255,255,0.02)'" onmouseout="this.style.background='none'">
        <td style="padding:5px;">${thumb}</td>
        <td style="padding:5px 8px;font-size:0.78rem;color:#e2e8f0;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(f.name)}</td>
        <td style="padding:5px 8px;font-size:0.78rem;color:var(--text-gray);">${esc(f.artist_name||'—')}</td>
        <td style="padding:5px 8px;font-size:0.78rem;color:var(--text-gray);">${gd}</td>
        <td style="padding:5px 8px;font-size:0.78rem;color:var(--text-gray);">${md}</td>
        <td style="padding:5px 8px;"><div style="display:flex;gap:3px;">
          <button onclick="FE.openExisting(${f.id})" class="fe-btn" style="font-size:0.66rem;">Edit</button>
          <button onclick="FE.deleteFlyerRow(${f.id})" class="fe-btn" style="font-size:0.66rem;color:#ef4444;">Del</button>
        </div></td></tr>`;}).join('')}</tbody></table>`;}
  function sortBy(col){if(_sortCol===col)_sortDir=_sortDir==='asc'?'desc':'asc';else{_sortCol=col;_sortDir='asc';}renderPreviousTab();}
  function filterPreviousTab(){renderPreviousTab();}
  async function openExisting(flyerId){try{const r=await fetch(`/api/venues/${venueId}/flyers/${flyerId}`,{credentials:'include'});if(!r.ok)return;
    currentFlyer=await r.json();loadCanvasData(currentFlyer.canvas_data);setFileTitle(currentFlyer.name||'Untitled');markClean();updateNameDisplay();switchTab('create');}catch(e){}}
  async function deleteFlyerRow(flyerId){
    const ok=await feModal({title:'Delete Flyer',message:'Delete this flyer permanently?',confirmText:'Delete',danger:true});
    if(!ok)return;
    try{await fetch(`/api/venues/${venueId}/flyers/${flyerId}`,{method:'DELETE',credentials:'include'});
      if(currentFlyer?.id===flyerId)currentFlyer=null;await loadAllFlyers();renderPreviousTab();}catch(e){}}

  /* =========================================================
     TAB SWITCHING
     ========================================================= */
  function switchTab(tab){_activeTab=tab;
    const ct=document.getElementById('flyerCreateTab'),pt=document.getElementById('flyerPreviousTab');
    const cb=document.getElementById('flyerTabCreate'),pb=document.getElementById('flyerTabPrevious');
    const ft=document.getElementById('flyerFooter');
    if(tab==='create'){ct.style.display='flex';pt.style.display='none';ft.style.display='flex';cb.classList.add('active');pb.classList.remove('active');}
    else{ct.style.display='none';pt.style.display='flex';ft.style.display='none';pb.classList.add('active');cb.classList.remove('active');loadAllFlyers().then(renderPreviousTab);}}

  function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML;}

  /* =========================================================
     LAYERS PANEL
     ========================================================= */
  function getLayerLabel(obj) {
    if (obj._isBorder)               return '🔲 Border';
    if (obj._isBgColor)               return '🎨 Background Color';
    if (obj._isBg)                    return '🖼 Background Image';
    if (obj._isDarkOverlay)           return '🌑 Dark Overlay';
    if (obj._isLogoPlaceholderRect)   return '📦 Logo Placeholder Box';
    if (obj._isZoneRect)              return '🎯 Artist Logo Zone (drag to resize)';
    const v = obj._tplVar;
    if (v === 'artist_logo')            return '🎤 Artist Logo';
    if (v === 'venue_logo')             return '🏠 Venue Logo';
    if (v === 'artist_logo_placeholder') return '📦 Artist Logo (placeholder)';
    if (v === 'venue_name')             return '📛 Venue Name';
    if (v === 'location')               return '📍 Location';
    if (v === 'date')                   return '📅 Date';
    if (v === 'time')                   return '⏰ Time';
    if (v === 'live_music')             return '🎸 Live Music';
    if (v === 'artist_name')            return '🎤 Artist Name';
    if (obj.type === 'image')           return '🖼 Image';
    if (obj.type === 'textbox' || obj.type === 'text') {
      const preview = (obj.text || '').replace(/\n/g, ' ').slice(0, 24);
      return '✏️ ' + (preview || 'Text');
    }
    if (obj.type === 'rect')   return '▬ Rectangle';
    if (obj.type === 'circle') return '● Circle';
    if (obj.type === 'line')   return '— Line';
    return '◈ ' + (obj.type || 'Object');
  }

  // Track last clicked layer index for shift-select range
  let _lastLayerClickIdx = null;

  function refreshLayers() {
    const list = document.getElementById('flyerLayersList');
    if (!list || !canvas) return;
    const allObjs = canvas.getObjects();
    // Include bg images in layers but exclude border (handled separately) and dark overlay
    const objs = allObjs.filter(o => !o._isBorder && !o._isDarkOverlay);
    if (objs.length === 0) { list.innerHTML = '<div style="padding:8px;font-size:0.72rem;color:#64748b;text-align:center;">No layers</div>'; return; }
    const reversed = [...objs].reverse(); // topmost first in panel
    const activeObj = canvas.getActiveObject();
    // Collect all currently selected objects (handle ActiveSelection)
    const selectedObjs = new Set();
    if (activeObj) {
      if (activeObj.type === 'activeSelection') {
        activeObj.getObjects().forEach(o => selectedObjs.add(o));
      } else {
        selectedObjs.add(activeObj);
      }
    }
    list.innerHTML = reversed.map((obj, panelIdx) => {
      const i = allObjs.indexOf(obj);
      const label = getLayerLabel(obj);
      const vis = obj.visible !== false;
      const isActive = selectedObjs.has(obj);
      const isBg = !!obj._isBg;
      const isLocked = !!obj._layerLocked;
      const btnStyle = 'background:none;border:none;cursor:pointer;font-size:0.8rem;padding:0 3px;line-height:1;';
      const lockBtn = `<button onclick="event.stopPropagation();FE.layerToggleLock(${i})" title="${isLocked ? 'Unlock layer' : 'Lock layer'}"
        style="${btnStyle}color:${isLocked ? '#f59e0b' : '#4b5563'};">🔒</button>`;
      const upBtn = isBg || isLocked ? '' :
        `<button onclick="event.stopPropagation();FE.layerMoveUp(${i})" title="Move up" style="${btnStyle}color:#94a3b8;">↑</button>`;
      const downBtn = isBg || isLocked ? '' :
        `<button onclick="event.stopPropagation();FE.layerMoveDown(${i})" title="Move down" style="${btnStyle}color:#94a3b8;">↓</button>`;
      const delBtn = `<button onclick="event.stopPropagation();FE.layerDelete(${i})" title="Delete" style="${btnStyle}color:#ef4444;">🗑</button>`;
      return `<div class="fe-layer-row${isActive ? ' fe-layer-active' : ''}" data-idx="${i}" data-pidx="${panelIdx}"
        style="display:flex;align-items:center;gap:2px;padding:4px 6px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,0.04);font-size:0.72rem;color:${vis ? '#e2e8f0' : '#4b5563'};"
        onclick="FE.layerSelect(${i}, ${panelIdx}, event)">
        <button onclick="event.stopPropagation();FE.layerToggleVis(${i})" title="${vis ? 'Hide' : 'Show'}"
          style="${btnStyle}color:${vis ? '#94a3b8' : '#374151'};">${vis ? '👁' : '🚫'}</button>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${label}</span>
        ${lockBtn}${upBtn}${downBtn}${delBtn}
      </div>`;
    }).join('');
  }

  function layerSelect(i, panelIdx, event) {
    if (!canvas) return;
    const allObjs = canvas.getObjects();
    const userObjs = allObjs.filter(o => !o._isBorder && !o._isBg && !o._isDarkOverlay);
    const reversed = [...userObjs].reverse(); // same order as panel

    const obj = allObjs[i];
    if (!obj || obj._isBorder || obj._isDarkOverlay) return;
    // Bg objects are selectable for delete but not move
    if (obj._isBg) { canvas.setActiveObject(obj); canvas.requestRenderAll(); refreshLayers(); return; }

    if (event && event.shiftKey && _lastLayerClickIdx !== null) {
      // Shift-select: pick range in panel order
      const lo = Math.min(_lastLayerClickIdx, panelIdx);
      const hi = Math.max(_lastLayerClickIdx, panelIdx);
      const rangeObjs = reversed.slice(lo, hi + 1).filter(o => !o._isBorder && !o._isBg && !o._isDarkOverlay);
      if (rangeObjs.length === 1) {
        canvas.setActiveObject(rangeObjs[0]);
      } else if (rangeObjs.length > 1) {
        const sel = new fabric.ActiveSelection(rangeObjs, { canvas });
        canvas.setActiveObject(sel);
      }
    } else {
      canvas.setActiveObject(obj);
      _lastLayerClickIdx = panelIdx;
    }

    canvas.requestRenderAll();
    refreshLayers();
    updateProps();
  }

  function layerToggleVis(i) {
    if (!canvas) return;
    const obj = canvas.getObjects()[i];
    if (!obj) return;
    obj.visible = obj.visible === false ? true : false;
    canvas.renderAll();
    refreshLayers();
    saveState();
  }

  function layerMoveUp(i) {
    if (!canvas) return;
    const obj = canvas.getObjects()[i];
    if (!obj || obj._layerLocked) return;
    canvas.bringForward(obj);
    // Keep border on top
    canvas.getObjects().filter(o => o._isBorder).forEach(o => canvas.bringToFront(o));
    canvas.renderAll();
    refreshLayers();
    saveState();
  }

  function layerMoveDown(i) {
    if (!canvas) return;
    const obj = canvas.getObjects()[i];
    if (!obj || obj._layerLocked) return;
    canvas.sendBackwards(obj);
    canvas.renderAll();
    refreshLayers();
    saveState();
  }

  function layerDelete(i) {
    if (!canvas) return;
    const obj = canvas.getObjects()[i];
    if (!obj) return;
    canvas.remove(obj);
    canvas.renderAll();
    refreshLayers();
    saveState();
  }

  function layerToggleLock(i) {
    if (!canvas) return;
    const obj = canvas.getObjects()[i];
    if (!obj) return;
    obj._layerLocked = !obj._layerLocked;
    // Also toggle Fabric's selection/movement lock to match
    obj.set({ selectable: !obj._layerLocked, evented: !obj._layerLocked });
    canvas.discardActiveObject();
    canvas.renderAll();
    refreshLayers();
    saveState();
  }



  /* =========================================================
     PUBLIC API
     ========================================================= */
  window.FE = window.flyerEditor = {
    open, close, switchTab,
    addText, addHeading, addRect, addCircle, addLine, handleImageUpload,
    setBgColor, handleBgUpload, clearBgImage, toggleBorder, updateBorder,
    setProp, setFontSize, setTextStroke, setStrokeWidth, setAlign,
    toggleBold, toggleItalic, toggleUnderline, setOpacity,
    layer, deleteSelected, copySelected, cutSelected, pasteClipboard, centerOnCanvas, nudgeSelected, changeSize,
    onTemplateSelect, saveAsDefaultTemplate, saveAsNewTemplate, deleteCurrentTemplate, deleteCurrentFlyer, loadDefaultTemplate, setSiteDefault, toggleAutoFlyers,
    saveAsAdminDefault, saveAsNewAdminTemplate, deleteAdminTemplate,
    searchPrevious, loadPrevious,
    toggleSaveMenu, save,
    toggleExport, exportAs,
    sortBy, filterPreviousTab, openExisting, deleteFlyerRow,
    undo, redo, uploadOwnFlyer, toggleGigVars, addGigVar,
    refreshLayers, layerSelect, layerToggleVis, layerMoveUp, layerMoveDown, layerDelete
  };

  document.addEventListener('keydown', e => {
    if(!canvas)return;const ov=document.getElementById('flyerEditorOverlay');
    if(!ov||ov.style.display==='none')return;
    if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)||e.target.isContentEditable)return;
    if(canvas.getActiveObject()?.isEditing)return;
    if(e.key==='Delete'||e.key==='Backspace'){deleteSelected();e.preventDefault();}
    if((e.ctrlKey||e.metaKey)&&e.key==='z'&&!e.shiftKey){e.preventDefault();undo();}
    if((e.ctrlKey||e.metaKey)&&(e.key==='y'||(e.key==='z'&&e.shiftKey))){e.preventDefault();redo();}
    if((e.ctrlKey||e.metaKey)&&e.key==='c'){e.preventDefault();copySelected();}
    if((e.ctrlKey||e.metaKey)&&e.key==='x'){e.preventDefault();cutSelected();}
    if((e.ctrlKey||e.metaKey)&&e.key==='v'){e.preventDefault();pasteClipboard();}
    // Arrow key nudge: 1px normal, 10px with Shift
    const arrowKeys = ['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'];
    if(arrowKeys.includes(e.key)){
      const activeObj = canvas.getActiveObject();
      if(!activeObj) return;
      e.preventDefault();
      const step = e.shiftKey ? 10 : 1;
      const dx = e.key==='ArrowLeft' ? -step : e.key==='ArrowRight' ? step : 0;
      const dy = e.key==='ArrowUp'   ? -step : e.key==='ArrowDown'  ? step : 0;
      if(activeObj.type === 'activeSelection') {
        // Move all objects in the selection
        activeObj.getObjects().forEach(o => {
          o.set({ left: o.left + dx, top: o.top + dy });
          o.setCoords();
        });
        activeObj.setCoords();
      } else {
        activeObj.set({ left: activeObj.left + dx, top: activeObj.top + dy });
        activeObj.setCoords();
      }
      canvas.requestRenderAll();
      saveState();
    }
  });
})();