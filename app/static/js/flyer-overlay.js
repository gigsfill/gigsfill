// Shared flyer overlay functions — used by multiple pages
function _buildBuiltinFlyerJSON(gi) {
  var cW = 415, cH = 520;
  var s = cH / 1350;
  var W = cW * 0.94, L = cW * 0.03;
  var FONT = 'Trebuchet MS', GREY = 'rgba(200,200,210,0.9)';
  function ft(t) { if (!t) return ''; var pp = t.split(':').map(Number); return ((pp[0]%12)||12)+':'+String(pp[1]).padStart(2,'0')+(pp[0]>=12?'PM':'AM'); }
  var dStr = gi.date ? new Date(gi.date+'T00:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'}) : '';
  var tStr = gi.start_time ? ft(gi.start_time)+(gi.end_time?' - '+ft(gi.end_time):'') : '';
  var loc  = [gi.address_line_1, gi.city&&gi.state?gi.city+', '+gi.state:(gi.city||gi.state)].filter(Boolean).join('\n');
  var R = Math.round;
  var objs = [
    // Dark overlay
    {type:'rect', left:0, top:0, width:cW, height:cH, fill:'rgba(0,0,0,0.45)', selectable:false, evented:false},
    // Venue name
    {type:'textbox', left:L, top:R(cH*0.03), width:W, text:(gi.venue_name||'VENUE NAME').toUpperCase(),
      fontSize:R(120*s), fontFamily:FONT, fontWeight:'bold', fill:'#ffffff', textAlign:'center',
      stroke:'#000000', strokeWidth:R(2*s), _tplVar:'venue_name'}
  ];
  if (loc) objs.push({type:'textbox', left:L, top:R(cH*0.225), width:W, text:loc,
    fontSize:R(45*s), fontFamily:FONT, fontWeight:'bold', fill:GREY, textAlign:'center', lineHeight:1.15, _tplVar:'location'});
  objs.push({type:'textbox', left:L, top:R(cH*0.355), width:W, text:'\u2605  L I V E   M U S I C  \u2605',
    fontSize:R(72*s), fontFamily:FONT, fontWeight:'bold', fill:GREY, textAlign:'center', charSpacing:100});
  if (dStr) objs.push({type:'textbox', left:L, top:R(cH*0.44), width:W, text:dStr,
    fontSize:R(80*s), fontFamily:FONT, fontWeight:'bold', fill:'#ffffff', textAlign:'center',
    stroke:'#000000', strokeWidth:R(3*s), _tplVar:'date'});
  if (tStr) objs.push({type:'textbox', left:L, top:R(cH*0.53), width:W, text:tStr,
    fontSize:R(65*s), fontFamily:FONT, fontWeight:'bold', fill:GREY, textAlign:'center', _tplVar:'time'});
  // Artist area — tagged artist_logo so the canvas renderer swaps in the real photo
  objs.push({type:'textbox', left:L, top:R(cH*0.50), width:W,
    text: gi.artist_picture_url ? '' : (gi.artist_name||'').toUpperCase(),
    fontSize:R(100*s), fontFamily:FONT, fontWeight:'bold', fill:'#ffffff', textAlign:'center',
    stroke:'#000000', strokeWidth:R(3*s), _tplVar:'artist_logo'});
  return JSON.stringify({version:'5.3.1', objects:objs, background:'#0a0a14', width:cW, height:cH});
}

function _showFlyerOverlay(data, modalId) {
  var fm = document.getElementById(modalId);
  if (!fm) {
    fm = document.createElement('div');
    fm.id = modalId;
    fm.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:99999;align-items:center;justify-content:center;cursor:pointer;padding:20px;box-sizing:border-box;';
    fm.onclick = function(e) { if (e.target === fm) fm.style.display = 'none'; };
    document.body.appendChild(fm);
  }
  fm.style.display = 'flex';
  var mid = modalId;
  var closeBtn = '<button onclick="document.getElementById(\''+mid+'\').style.display=\'none\'" style="padding:9px 22px;background:rgba(255,255,255,0.08);color:#e2e8f0;border:1px solid rgba(255,255,255,0.2);border-radius:8px;font-size:0.85rem;font-weight:500;cursor:pointer;">&#10005; Close</button>';

  // Build descriptive filename from gig_info
  var gi0 = (data && data.gig_info) || {};
  // Hoist artist name from slots if not on root (multi-slot gigs)
  if (!gi0.artist_name && gi0.slots && gi0.slots.length) {
    var s0 = gi0.slots.find(function(s){return s.artist_name;}) || gi0.slots[0];
    if (s0) gi0.artist_name = s0.artist_name;
  }
  var _gigFileName = (function() {
    var v = (gi0.venue_name||'').replace(/[^a-zA-Z0-9]/g,'_').replace(/_+/g,'_').replace(/^_|_$/g,'');
    var d = gi0.date ? (function(){ var dt=new Date(gi0.date+'T00:00:00'); return dt.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}).replace(/[\s,]+/g,'_'); })() : '';
    function _ft(t){if(!t)return'';var p=t.split(':').map(Number);return((p[0]%12)||12)+'_'+String(p[1]).padStart(2,'0')+(p[0]>=12?'PM':'AM');}
    var t = gi0.start_time ? _ft(gi0.start_time)+(gi0.end_time?'-'+_ft(gi0.end_time):'') : '';
    var a = (gi0.artist_name||'').replace(/[^a-zA-Z0-9]/g,'_').replace(/_+/g,'_').replace(/^_|_$/g,'');
    return [v,d,t,a].filter(Boolean).join('_') || (data&&data.name) || 'event-flyer';
  })();


  if (data && data.thumbnail_data) {
    fm.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:12px;" onclick="event.stopPropagation()"><img src="' + data.thumbnail_data + '" style="max-height:520px;max-width:88vw;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,0.6);object-fit:contain;"><div style="display:flex;gap:12px;"><a href="' + data.thumbnail_data + '" download="' + _gigFileName + '.jpg" style="padding:9px 22px;background:rgba(139,92,246,0.85);color:#fff;border:1px solid rgba(139,92,246,0.6);border-radius:8px;font-size:0.85rem;font-weight:600;text-decoration:none;cursor:pointer;">&#11015; Download Flyer</a>' + closeBtn + '</div></div>';
    return;
  }

  if (data && data.canvas_data) {
    // Container for spinner — Fabric will place its wrapper div inside _pfMount
    fm.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:12px;" onclick="event.stopPropagation()"><div id="_pfMount" style="position:relative;border-radius:12px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,0.6);"><canvas id="_pfCanvas"></canvas><div id="_pfSpinner" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(10,10,20,0.9);"><span style="color:#c4b5fd;font-size:1rem;">Loading flyer...</span></div></div><div id="_pfBtnRow" style="display:flex;gap:12px;"><span id="_pfDlPlaceholder"></span>' + closeBtn + '</div></div>';

    function doRender() {
      try {
        var parsed = typeof data.canvas_data === 'string' ? JSON.parse(data.canvas_data) : data.canvas_data;

        // canvas JSON width/height ARE the display dimensions (e.g. 415x520 for instagram_post)
        var canvasW = parsed.width  || 415;
        var canvasH = parsed.height || 520;

        // Display at fixed 520px height (matching editor CANVAS_DISPLAY_HEIGHT),
        // preserving aspect ratio — same size regardless of thumbnail vs canvas path
        var aspect = canvasW / canvasH;
        var displayH = Math.min(520, Math.floor(window.innerHeight * 0.80));
        var displayW = Math.round(displayH * aspect);
        // If too wide, constrain to viewport width
        var maxW = Math.floor(window.innerWidth * 0.88);
        if (displayW > maxW) { displayW = maxW; displayH = Math.round(displayW / aspect); }

        var mount = document.getElementById('_pfMount');
        if (!mount) return;
        mount.style.width  = displayW + 'px';
        mount.style.height = displayH + 'px';

        // Create Fabric canvas at exact display size — no transforms needed
        var fc = new fabric.StaticCanvas('_pfCanvas', {
          width: displayW, height: displayH, renderOnAddRemove: false
        });

        // Gig info
        var gi = data.gig_info || {};
        if (!gi.artist_picture_url && gi.slots && gi.slots.length) {
          var sl = gi.slots.find(function(s) { return s.artist_id; }) || gi.slots[0];
          if (sl) { gi.artist_id = gi.artist_id || sl.artist_id; gi.artist_name = gi.artist_name || sl.artist_name; gi.artist_picture_url = gi.artist_picture_url || sl.artist_picture_url; }
        }

        function ft(t) { if (!t) return ''; var p = t.split(':').map(Number); return ((p[0]%12)||12)+':'+String(p[1]).padStart(2,'0')+(p[0]>=12?'PM':'AM'); }
        var dateStr = gi.date ? new Date(gi.date+'T00:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'}) : '';
        var timeStr = gi.start_time ? ft(gi.start_time)+(gi.end_time?' - '+ft(gi.end_time):'') : '';
        var loc = [gi.address_line_1, gi.city&&gi.state?gi.city+', '+gi.state:(gi.city||gi.state)].filter(Boolean).join('\n');

        // Use src->_tplVar map + reviver for reliable custom prop restoration
        var tplVarByIdx = {};
        var tplVarBySrc = {};
        (parsed.objects || []).forEach(function(o, i) {
          if (o._tplVar) { tplVarByIdx[i] = o._tplVar; if (o.src) tplVarBySrc[o.src] = o._tplVar; }
        });
        var reviver = function(jsonObj, fabricObj) {
          var tv = (jsonObj.src && tplVarBySrc[jsonObj.src]) || jsonObj._tplVar;
          if (tv) fabricObj._tplVar = tv;
          if (jsonObj._isZoneRect) fabricObj._isZoneRect = true;
        };

        fc.loadFromJSON(parsed, function() {
          var objs = fc.getObjects();
          objs.forEach(function(obj, i) { if (!obj._tplVar && tplVarByIdx[i]) obj._tplVar = tplVarByIdx[i]; });

          // Update text vars
          objs.forEach(function(obj) {
            var v = obj._tplVar;
            if (!v || obj.text === undefined) return;
            if      (v==='date'        && dateStr)       obj.set('text', dateStr);
            else if (v==='time'        && timeStr)       obj.set('text', timeStr);
            else if (v==='venue_name'  && gi.venue_name) obj.set('text', gi.venue_name.toUpperCase());
            else if (v==='location'    && loc)           obj.set('text', loc);
            else if (v==='artist_name' && gi.artist_name) obj.set('text', gi.artist_name.toUpperCase());
          });

          function finish() {
            fc.renderAll();
            var sp = document.getElementById('_pfSpinner'); if (sp) sp.style.display='none';
            var dlPh = document.getElementById('_pfDlPlaceholder');
            if (dlPh) {
              var dlUrl = fc.toDataURL({format:'jpeg', quality:0.92, multiplier: Math.round(Math.min(1400/canvasW, 2))});
              dlPh.outerHTML = '<a href="' + dlUrl + '" download="' + _gigFileName + '.jpg" style="padding:9px 22px;background:rgba(139,92,246,0.85);color:#fff;border:1px solid rgba(139,92,246,0.6);border-radius:8px;font-size:0.85rem;font-weight:600;text-decoration:none;cursor:pointer;">&#11015; Download Flyer</a>';
            }
          }

          // Count pending async image loads — call finish() only when all are done
          var pending = 0;
          function maybeFinish() { if (--pending <= 0) finish(); }

          // ── Venue Logo ──
          var vLogoImage = objs.find(function(o) { return o._tplVar==='venue_logo' && o.type==='image'; });
          var vLogoZone  = objs.find(function(o) { return o._tplVar==='venue_logo' && o._isZoneRect; });
          if (gi.venue_picture_url && (vLogoImage || vLogoZone)) {
            pending++;
            fabric.Image.fromURL(gi.venue_picture_url, function(img) {
              if (img) {
                var ref = vLogoImage || vLogoZone;
                if (vLogoImage) {
                  img.set({ left:vLogoImage.left, top:vLogoImage.top,
                    scaleX:vLogoImage.scaleX||1, scaleY:vLogoImage.scaleY||1, angle:vLogoImage.angle||0,
                    originX:vLogoImage.originX||'left', originY:vLogoImage.originY||'top',
                    shadow:vLogoImage.shadow, opacity:vLogoImage.opacity!=null?vLogoImage.opacity:1, _tplVar:'venue_logo' });
                } else {
                  var vzW = vLogoZone.width*(vLogoZone.scaleX||1), vzH = vLogoZone.height*(vLogoZone.scaleY||1);
                  var vsc = Math.min(vzW/img.width, vzH/img.height, 1);
                  img.set({ left:vLogoZone.left+vzW/2, top:vLogoZone.top+vzH/2,
                    scaleX:vsc, scaleY:vsc, originX:'center', originY:'center',
                    shadow:new fabric.Shadow({color:'rgba(0,0,0,0.8)',blur:20}), _tplVar:'venue_logo' });
                }
                fc.remove(ref); fc.add(img);
              }
              // Keep border on top
              fc.getObjects().filter(function(o){return o._isBorder;}).forEach(function(o){fc.bringToFront(o);});
              maybeFinish();
            });
          }

          // ── Artist Logo ──
          if (!gi.artist_picture_url) { if (!pending) finish(); return; }

          var logoImage = objs.find(function(o) { return o._tplVar==='artist_logo' && o.type==='image'; });
          var logoZone  = objs.find(function(o) { return o._tplVar==='artist_logo' && o._isZoneRect; });
          var logoText  = objs.find(function(o) { return o._tplVar==='artist_logo' && o.text!==undefined; });

          if (logoImage || logoZone || logoText) {
            pending++;
            fabric.Image.fromURL(gi.artist_picture_url, function(img) {
              if (img) {
                if (logoImage) {
                  img.set({ left:logoImage.left, top:logoImage.top,
                    scaleX:logoImage.scaleX||1, scaleY:logoImage.scaleY||1, angle:logoImage.angle||0,
                    originX:logoImage.originX||'left', originY:logoImage.originY||'top',
                    shadow:logoImage.shadow, opacity:logoImage.opacity!=null?logoImage.opacity:1, _tplVar:'artist_logo' });
                  fc.remove(logoImage); fc.add(img);
                  fc.getObjects().filter(function(o){return o._isBorder;}).forEach(function(o){fc.bringToFront(o);});
                } else if (logoZone) {
                  var zoneW = logoZone.width*(logoZone.scaleX||1), zoneH = logoZone.height*(logoZone.scaleY||1);
                  var sc = Math.min(zoneW/img.width, zoneH/img.height, 1);
                  img.set({ left:logoZone.left+zoneW/2, top:logoZone.top+zoneH/2,
                    scaleX:sc, scaleY:sc, originX:'center', originY:'center',
                    shadow:new fabric.Shadow({color:'rgba(0,0,0,0.8)',blur:20}), _tplVar:'artist_logo' });
                  fc.remove(logoZone); fc.add(img);
                  fc.getObjects().filter(function(o){return o._isBorder;}).forEach(function(o){fc.bringToFront(o);});
                } else {
                  var tw = canvasW*0.94, th = canvasH-logoText.top-canvasH*0.03;
                  var s2 = Math.min(tw/img.width, th/img.height, 1);
                  img.set({ left:canvasW/2, top:logoText.top+th/2, scaleX:s2, scaleY:s2,
                    originX:'center', originY:'center', shadow:logoText.shadow, _tplVar:'artist_logo' });
                  fc.remove(logoText); fc.add(img);
                  fc.getObjects().filter(function(o){return o._isBorder;}).forEach(function(o){fc.bringToFront(o);});
                }
                objs.forEach(function(o) { if (o._tplVar==='artist_name') o.visible = false; });
              }
              maybeFinish();
            });
          }

          if (!pending) finish();
        }, reviver);
      } catch(e) {
        console.error('Flyer render error:', e);
        var sp = document.getElementById('_pfSpinner'); if (sp) sp.style.display='none';
            var dlPh = document.getElementById('_pfDlPlaceholder');
            if (dlPh) {
              var dlUrl = fc.toDataURL({format:'jpeg', quality:0.92, multiplier: Math.round((window.devicePixelRatio||1) * Math.min(1400/canvasW, 2))});
              dlPh.outerHTML = '<a href="' + dlUrl + '" download="' + _gigFileName + '.jpg" style="padding:9px 22px;background:rgba(139,92,246,0.85);color:#fff;border:1px solid rgba(139,92,246,0.6);border-radius:8px;font-size:0.85rem;font-weight:600;text-decoration:none;cursor:pointer;">&#11015; Download Flyer</a>';
            }
      }
    }

    if (typeof fabric !== 'undefined') { doRender(); }
    else { var s=document.createElement('script'); s.src='https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.3.1/fabric.min.js'; s.onload=doRender; document.head.appendChild(s); }
    return;
  }

  // use_builtin: build the default template layout client-side from gig_info, then render via canvas path
  if (data && data.use_builtin && data.gig_info) {
    var gi = data.gig_info || {};
    // Multi-slot hoist
    if (!gi.artist_picture_url && gi.slots && gi.slots.length) {
      var sl = gi.slots.find(function(s){return s.artist_id;}) || gi.slots[0];
      if (sl) { gi.artist_name = gi.artist_name || sl.artist_name; gi.artist_picture_url = gi.artist_picture_url || sl.artist_picture_url; }
    }
    _showFlyerOverlay({ canvas_data: _buildBuiltinFlyerJSON(gi), gig_info: gi }, modalId);
    return;
  }

  fm.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;gap:16px;text-align:center;" onclick="event.stopPropagation()"><div style="font-size:3rem;">&#127912;</div><div style="color:#e2e8f0;font-size:1.1rem;font-weight:600;">Flyer Coming Soon</div><div style="color:#94a3b8;font-size:0.85rem;">The venue is preparing an event flyer for this gig.</div>' + closeBtn + '</div>';
}
