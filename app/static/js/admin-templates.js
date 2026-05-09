/**
 * admin-templates.js
 * Email Templates tab — load, edit, save via TinyMCE + /api/email-templates
 */

(function () {
  // ── State ──────────────────────────────────────────────────────────────────
  let _templates = {};   // keyed by template_type
  let _currentKey = '';

  // Variables available per template key
  const TEMPLATE_VARS = {
    artist_gig_booked:                 '{{artist_name}}, {{venue_name}}, {{gig_date}}, {{gig_time}}, {{gig_pay}}, {{city}}, {{state}}',
    artist_gig_cancelled:              '{{artist_name}}, {{venue_name}}, {{gig_date}}, {{gig_time}}, {{city}}, {{state}}',
    artist_preferred_request:          '{{artist_name}}, {{venue_name}}',
    artist_preferred_approved:         '{{artist_name}}, {{venue_name}}',
    artist_preferred_denied:           '{{artist_name}}, {{venue_name}}',
    artist_preferred_revoked:          '{{artist_name}}, {{venue_name}}',
    artist_payment_sent:               '{{artist_name}}, {{venue_name}}, {{gig_date}}, {{amount}}, {{platform_fee}}, {{net_pay}}',
    artist_venue_payment_issue:        '{{artist_name}}, {{venue_name}}, {{gig_date}}, {{amount}}',
    transfer_failed_artist:            '{{artist_name}}, {{venue_name}}, {{gig_date}}, {{amount}}',
    venue_gig_booked:                  '{{venue_name}}, {{artist_name}}, {{gig_date}}, {{gig_time}}, {{gig_pay}}, {{city}}, {{state}}',
    venue_gig_cancelled:               '{{venue_name}}, {{artist_name}}, {{gig_date}}, {{gig_time}}',
    venue_contract_sign_needed:        '{{venue_name}}, {{artist_name}}, {{gig_date}}, {{contract_link}}',
    venue_preferred_request:           '{{venue_name}}, {{artist_name}}',
    venue_preferred_approved:          '{{venue_name}}, {{artist_name}}',
    venue_preferred_denied:            '{{venue_name}}, {{artist_name}}',
    venue_preferred_revoked:           '{{venue_name}}, {{artist_name}}',
    venue_payment_charged:             '{{venue_name}}, {{artist_name}}, {{gig_date}}, {{amount}}',
    transfer_failed_venue:             '{{venue_name}}, {{artist_name}}, {{gig_date}}, {{amount}}',
    venue_gig_confirmation_reminder:   '{{venue_name}}, {{artist_name}}, {{gig_date}}, {{gig_time}}',
    venue_open_gig_4w:                 '{{venue_name}}, {{gig_date}}, {{gig_time}}, {{gig_pay}}',
    venue_open_gig_2w:                 '{{venue_name}}, {{gig_date}}, {{gig_time}}, {{gig_pay}}',
    venue_open_gig_1w:                 '{{venue_name}}, {{gig_date}}, {{gig_time}}, {{gig_pay}}',
    cancelled_gig_preferred_blast:     '{{artist_name}}, {{venue_name}}, {{gig_date}}, {{gig_time}}, {{gig_pay}}',
    cancelled_gig_radius_blast:        '{{artist_name}}, {{venue_name}}, {{gig_date}}, {{gig_time}}, {{gig_pay}}',
    support_ticket_received:           '{{user_name}}, {{ticket_id}}, {{subject}}',
    support_ticket_admin_notification: '{{user_name}}, {{ticket_id}}, {{subject}}, {{message}}',
    support_ticket_reply:              '{{user_name}}, {{ticket_id}}, {{reply_message}}',
    support_ticket:                    '{{user_name}}, {{email}}, {{subject}}, {{message}}',
    venue_message_to_artists:          '{{venue_name}}, {{artist_name}}, {{message}}',
    welcome:                           '{{user_name}}, {{login_url}}',
    entity_invitation:                 '{{inviter_name}}, {{entity_name}}, {{invite_link}}',
    recommend_gigsfill:                '{{sender_name}}, {{signup_url}}',
  };

  // ── Init TinyMCE ──────────────────────────────────────────────────────────
  function initEditor(content) {
    if (typeof tinymce === 'undefined') {
      console.warn('TinyMCE not loaded');
      return;
    }
    tinymce.remove('#templateBodyEditor');
    tinymce.init({
      selector: '#templateBodyEditor',
      height: 480,
      menubar: false,
      plugins: ['lists', 'link', 'code', 'emoticons'],
      toolbar: 'undo redo | bold italic underline | forecolor backcolor | bullist numlist | link | code | emoticons',
      skin: 'oxide-dark',
      content_css: 'dark',
      content_style: 'body { font-family: Arial, sans-serif; font-size: 14px; color: #e2e8f0; background: #0f1623; }',
      setup: function (editor) {
        editor.on('init', function () {
          editor.setContent(content || '');
        });
      }
    });
  }

  // ── Load all templates from API ───────────────────────────────────────────
  async function fetchAllTemplates() {
    try {
      const r = await fetch('/api/email-templates', { credentials: 'include' });
      if (!r.ok) return;
      const list = await r.json();
      _templates = {};
      list.forEach(t => { _templates[t.template_type] = t; });
    } catch (e) {
      console.error('fetchAllTemplates error:', e);
    }
  }

  // ── Called when dropdown changes ──────────────────────────────────────────
  window.loadSelectedTemplate = async function () {
    const sel = document.getElementById('templateSelector');
    if (!sel) return;
    const key = sel.value;
    _currentKey = key;

    const editorContainer = document.getElementById('editorContainer');
    const subjectRow      = document.getElementById('subjectRow');
    const variablesGuide  = document.getElementById('variablesGuide');
    const variablesList   = document.getElementById('variablesList');
    const emptyState      = document.getElementById('templateEmptyState');
    const subjectInput    = document.getElementById('templateSubject');
    const saveIndicator   = document.getElementById('saveIndicator');

    if (!key) {
      if (editorContainer) editorContainer.style.display = 'none';
      if (subjectRow)      subjectRow.style.display      = 'none';
      if (variablesGuide)  variablesGuide.style.display  = 'none';
      if (emptyState)      emptyState.style.display      = '';
      return;
    }

    // Show loading state
    if (emptyState)     emptyState.style.display      = 'none';
    if (subjectRow)     subjectRow.style.display      = 'flex';
    if (editorContainer) editorContainer.style.display = '';
    if (saveIndicator)  saveIndicator.style.opacity   = '0';

    // Variables guide
    if (variablesGuide && variablesList) {
      const vars = TEMPLATE_VARS[key];
      if (vars) {
        variablesList.textContent = vars;
        variablesGuide.style.display = '';
      } else {
        variablesGuide.style.display = 'none';
      }
    }

    // Fetch if not cached
    if (!_templates[key]) await fetchAllTemplates();

    const tpl = _templates[key] || { subject: '', body: '' };
    if (subjectInput) subjectInput.value = tpl.subject || '';
    initEditor(tpl.body || '');
  };

  // ── Save current template ─────────────────────────────────────────────────
  window.saveCurrentTemplate = async function () {
    if (!_currentKey) return;

    const subjectInput  = document.getElementById('templateSubject');
    const saveIndicator = document.getElementById('saveIndicator');

    let body = '';
    if (typeof tinymce !== 'undefined' && tinymce.get('templateBodyEditor')) {
      body = tinymce.get('templateBodyEditor').getContent();
    }

    const payload = {
      template_type: _currentKey,
      subject: subjectInput ? subjectInput.value.trim() : '',
      body: body
    };

    try {
      const r = await fetch('/api/email-templates', {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!r.ok) throw new Error(await r.text());
      // Update cache
      _templates[_currentKey] = payload;
      // Show saved indicator
      if (saveIndicator) {
        saveIndicator.style.opacity = '1';
        setTimeout(() => { saveIndicator.style.opacity = '0'; }, 2000);
      }
      // FIX (May 2026): the PUT now auto-exports to disk so edits survive restart.
      // If the disk write failed (rare — usually a permission issue), surface it
      // as a warning so admin knows to click "Export All" manually.
      try {
        const data = await r.json();
        if (data && data.exported === false) {
          window._adminToast(
            data.export_error || 'Saved to DB, but auto-export failed. Click "Export All" to retry.',
            'rgba(245,158,11,0.85)'
          );
        }
      } catch (_) { /* response wasn't JSON, ignore */ }
    } catch (e) {
      window._adminToast('Save failed: ' + e.message, 'rgba(239,68,68,0.8)');
    }
  };

  // ── Export all templates ──────────────────────────────────────────────────
  window.exportAllTemplates = async function () {
    try {
      const r = await fetch('/api/email-templates/export', { credentials: 'include' });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      window._adminToast(data.message || '✓ Templates exported successfully', 'rgba(16,185,129,0.8)');
    } catch (e) {
      window._adminToast('Export failed: ' + e.message, 'rgba(239,68,68,0.8)');
    }
  };

  // ── Pre-fetch when templates tab is opened ────────────────────────────────
  // Hook into switchTab so templates are ready before user picks one
  const _origSwitchTab = window.switchTab;
  if (typeof _origSwitchTab === 'function') {
    window.switchTab = function (tab) {
      _origSwitchTab(tab);
      if (tab === 'templates') fetchAllTemplates();
    };
  }

})();
