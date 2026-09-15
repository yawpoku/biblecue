// =============================================================================
// BibleCue — Renderer Process
// Connects to Python backend on ws://127.0.0.1:8767
// =============================================================================

'use strict';

document.addEventListener('DOMContentLoaded', () => {

  // macOS keeps its native traffic-light window controls even in a frameless
  // window. Flag it on <body> so style.css can inset the titlebar content
  // past them and hide the custom min/max/close buttons (redundant on Mac).
  if (window.electronAPI && window.electronAPI.platform === 'darwin') {
    document.body.classList.add('is-mac');
  }

  // ===========================================================================
  // SECTION 1: DOM References
  // ===========================================================================

  const dom = {
    // Status bar
    statusDot:        document.getElementById('status-dot'),
    statusText:       document.getElementById('status-text'),
    dgStatusMain:     document.getElementById('dg-status-main'),

    // Listen button
    btnListen:        document.getElementById('btn-listen'),
    pulseDot:         document.getElementById('pulse-dot'),
    listenLabel:      document.getElementById('listen-label'),

    // Verse display
    verseText:        document.getElementById('verse-text'),
    verseRef:         document.getElementById('verse-ref'),

    // Logs
    transcriptLog:    document.getElementById('transcript-log'),
    detectionLog:     document.getElementById('detection-log'),

    // Settings fields
    selTranslation:   document.getElementById('sel-translation'),
    inpCooldown:      document.getElementById('inp-cooldown'),
    selDevice:        document.getElementById('sel-device'),
    chkAutostart:     document.getElementById('chk-autostart'),
    saveIndicator:    document.getElementById('save-indicator'),

    // Outputs card
    outToggle:          document.getElementById('outputs-toggle'),
    outBody:            document.getElementById('outputs-body'),
    outArrow:           document.getElementById('outputs-arrow'),
    outPpEnabled:       document.getElementById('out-pp-enabled'),
    outPpIp:            document.getElementById('out-pp-ip'),
    outPpPort:          document.getElementById('out-pp-port'),
    outPpUuid:          document.getElementById('out-pp-uuid'),
    outEwEnabled:       document.getElementById('out-ew-enabled'),
    outEwPath:          document.getElementById('out-ew-path'),
    outClipEnabled:     document.getElementById('out-clip-enabled'),
    outWebhookEnabled:  document.getElementById('out-webhook-enabled'),
    outWebhookUrl:      document.getElementById('out-webhook-url'),
    outFileEnabled:     document.getElementById('out-file-enabled'),
    outFilePath:        document.getElementById('out-file-path'),
    outObsEnabled:      document.getElementById('out-obs-enabled'),
    outObsIp:           document.getElementById('out-obs-ip'),
    outObsPort:         document.getElementById('out-obs-port'),
    outObsPassword:     document.getElementById('out-obs-password'),
    outObsSource:       document.getElementById('out-obs-source'),
    outTcpEnabled:      document.getElementById('out-tcp-enabled'),
    outTcpIp:           document.getElementById('out-tcp-ip'),
    outTcpPort:         document.getElementById('out-tcp-port'),
    outNdiEnabled:      document.getElementById('out-ndi-enabled'),
    outNdiName:         document.getElementById('out-ndi-name'),

    // Display card
    displayToggle:    document.getElementById('display-toggle'),
    displayBody:      document.getElementById('display-body'),
    displayArrow:     document.getElementById('display-arrow'),
    bgTypeRadios:     document.querySelectorAll('input[name="bg-type"]'),
    bgColorRow:       document.getElementById('bg-color-row'),
    bgImageRow:       document.getElementById('bg-image-row'),
    inpBgColor:       document.getElementById('inp-bg-color'),
    btnBgImagePick:   document.getElementById('btn-bg-image-pick'),
    btnBgImageClear:  document.getElementById('btn-bg-image-clear'),
    inpBgImageFile:   document.getElementById('inp-bg-image-file'),
    bgImagePreview:   document.getElementById('bg-image-preview'),
    selFontFamily:    document.getElementById('sel-font-family'),
    selFontWeight:    document.getElementById('sel-font-weight'),
    inpVerseSize:     document.getElementById('inp-verse-size'),
    inpRefSize:       document.getElementById('inp-ref-size'),
    inpTextColor:     document.getElementById('inp-text-color'),
    inpAccentColor:   document.getElementById('inp-accent-color'),
    alignBtns:        document.querySelectorAll('.align-btn'),
    stepperBtns:      document.querySelectorAll('.stepper-btn'),

    // Advanced settings
    advToggle:        document.getElementById('adv-toggle'),
    advBody:          document.getElementById('adv-body'),
    advArrow:         document.getElementById('adv-arrow'),

    // Manual verse
    btnManualSend:    document.getElementById('btn-manual-send'),
    inpManual:        document.getElementById('inp-manual'),

    // Titlebar
    btnMinimize:      document.getElementById('btn-minimize'),
    btnMaximize:      document.getElementById('btn-maximize'),
    btnClose:         document.getElementById('btn-close'),
    btnTheme:         document.getElementById('btn-theme'),

    // Help / setup guide
    btnHelp:          document.getElementById('btn-help'),
    btnHelpClose:     document.getElementById('btn-help-close'),
    helpOverlay:      document.getElementById('help-overlay'),

    // Log panel buttons
    clearLogBtns:     document.querySelectorAll('.btn-clear-log'),
    copyLogBtns:      document.querySelectorAll('.btn-copy-log'),
  };

  // ===========================================================================
  // SECTION 2: State
  // ===========================================================================

  let ws = null;
  let wsRetryDelay = 1000;
  const WS_MAX_DELAY = 10000;
  let WS_URL = 'ws://127.0.0.1:8765'; // default, overridden on boot

  let isListening = false;
  let autoStartPending = false;
  let saveIndicatorTimer = null;
  let advBodyOpen = false;

  // For interim transcript line replacement
  let lastEntryWasInterim = false;

  // Display card — the background image (data: URL) and alignment aren't
  // plain form fields, so track them here and read/write on populate/collect.
  let displayState = {
    bg_image: '',
    align_h: 'center',
    align_v: 'middle',
  };

  // ===========================================================================
  // SECTION 3: Utility Helpers
  // ===========================================================================

  /**
   * Debounce: delays fn execution until ms ms have passed since last call.
   * @param {Function} fn
   * @param {number} ms
   * @returns {Function}
   */
  function debounce(fn, ms) {
    let timer = null;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  /**
   * Send a JSON message over the WebSocket if it is open.
   * @param {object} msg
   */
  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    } else {
      console.warn('[WCI] WS not open — dropped message:', msg);
    }
  }

  // ===========================================================================
  // SECTION 4: Log Management
  // ===========================================================================

  const MAX_LOG_ENTRIES = 500;

  /**
   * Append a text entry to a log panel.
   * @param {string} panelId  - ID of the log container element
   * @param {string} text     - Text to display
   */
  function appendLog(panelId, text) {
    const panel = document.getElementById(panelId);
    if (!panel) return;

    // Determine CSS class based on panel and content
    let cssClass = '';
    if (panelId === 'transcript-log') {
      cssClass = text.startsWith('>> ') ? 'log-interim' : 'log-final';
    } else if (panelId === 'detection-log') {
      cssClass = 'log-detect';
    }

    // For transcript panel: if last entry was interim, replace it
    if (panelId === 'transcript-log' && lastEntryWasInterim) {
      const lastChild = panel.lastElementChild;
      if (lastChild && lastChild.classList.contains('log-interim')) {
        panel.removeChild(lastChild);
      }
    }

    // Create entry element
    const entry = document.createElement('div');
    entry.className = `log-entry ${cssClass} slide-in`;
    entry.textContent = text;
    panel.appendChild(entry);

    // Update interim tracking for transcript
    if (panelId === 'transcript-log') {
      lastEntryWasInterim = text.startsWith('>> ');
    }

    // Enforce max entries
    const entries = panel.querySelectorAll('.log-entry');
    if (entries.length > MAX_LOG_ENTRIES) {
      panel.removeChild(entries[0]);
    }

    // Update count badge
    const countId = panelId === 'transcript-log' ? 'tx-count' : 'dt-count';
    const countEl = document.getElementById(countId);
    if (countEl) {
      const currentCount = parseInt(countEl.textContent) || 0;
      countEl.textContent = currentCount + 1;
    }

    // Auto-scroll to bottom
    panel.scrollTop = panel.scrollHeight;

    // Remove animation class after it plays to allow re-triggering
    entry.addEventListener('animationend', () => {
      entry.classList.remove('slide-in');
    }, { once: true });
  }

  // ===========================================================================
  // SECTION 5: Verse Display
  // ===========================================================================

  /**
   * Update the on-screen verse with a fade-in animation.
   * @param {string} verseText
   * @param {string} reference
   * @param {string} translation
   */
  function displayVerse(verseText, reference, translation) {
    if (dom.verseText) dom.verseText.textContent = verseText || '';
    if (dom.verseRef)  dom.verseRef.textContent  = reference
      ? `${reference}${translation ? ' (' + translation + ')' : ''}`
      : '';

    // Trigger animation
    const targets = [dom.verseText, dom.verseRef].filter(Boolean);
    targets.forEach(el => {
      el.classList.remove('verse-updated');
      // Force reflow so removing+adding the class re-triggers the animation
      void el.offsetWidth;
      el.classList.add('verse-updated');
      setTimeout(() => el.classList.remove('verse-updated'), 500);
    });

    if (verseText && verseText !== '—') {
      appendLog('detection-log', `✓ Displayed: ${reference}`);
    }
  }

  // ===========================================================================
  // SECTION 6: Status Bar Updates
  // ===========================================================================

  /**
   * Update the main status bar dot and label.
   * @param {string} text
   * @param {string} color  - 'green' | 'red' | 'yellow' | 'gray' | etc.
   */
  function setStatus(text, color) {
    if (dom.statusText) dom.statusText.textContent = text;
    if (dom.statusDot) {
      dom.statusDot.className = 'status-dot'; // reset
      if (color) dom.statusDot.classList.add(`dot-${color}`);
    }
  }

  // ===========================================================================
  // SECTION 7: Listen Button State
  // ===========================================================================

  /**
   * Reflect the current listening state in the UI.
   * Source of truth is always the Python backend via `listening_state`.
   * @param {boolean} active
   */
  function setListeningState(active) {
    isListening = active;

    if (dom.btnListen) {
      dom.btnListen.classList.remove('btn-listen-start', 'btn-listen-stop');
      if (active) {
        dom.btnListen.textContent = '■  Stop Listening';
        dom.btnListen.classList.add('btn-listen-stop');
      } else {
        dom.btnListen.textContent = '▶  Start Listening';
        dom.btnListen.classList.add('btn-listen-start');
      }
    }

    if (dom.pulseDot) {
      dom.pulseDot.classList.toggle('active', active);
    }

    if (dom.listenLabel) {
      dom.listenLabel.textContent = active ? 'Live' : 'Not listening';
      dom.listenLabel.style.color = active ? 'var(--color-green, #4caf50)' : 'var(--color-muted, #888)';
    }

    if (!active) {
      const urlRow = document.getElementById('listener-url-row');
      if (urlRow) urlRow.style.display = 'none';
    }

    updateWaveform(active);
  }

  /**
   * Animate waveform bars when listening is active.
   * @param {boolean} active
   */
  function updateWaveform(active) {
    const bars = document.getElementById('waveform-bars');
    if (!bars) return;
    if (active) {
      bars.classList.add('waveform-active');
    } else {
      bars.classList.remove('waveform-active');
    }
  }

  // ===========================================================================
  // SECTION 8: Settings — Populate Form
  // ===========================================================================

  /**
   * Populate all settings form fields from a settings data object.
   * @param {object} data
   */
  function populateSettings(data) {
    if (!data) return;

    const setVal = (el, val) => { if (el && val !== undefined && val !== null) el.value = val; };
    const setChk = (el, val) => { if (el && val !== undefined) el.checked = !!val; };

    setVal(dom.selTranslation,  data.translation);
    setVal(dom.inpCooldown,     data.cooldown_secs);
    setChk(dom.chkAutostart,    data.autostart);

    // Auto-start on load if configured
    if (data.autostart && !autoStartPending) {
      autoStartPending = true;
      setTimeout(() => {
        if (!isListening) {
          send({ type: 'start_listening' });
        }
        autoStartPending = false;
      }, 1500);
    }

    if (data.outputs) populateOutputs(data.outputs);
    if (data.display) populateDisplay(data.display);
  }

  /**
   * Populate output plugin fields from an outputs array.
   * @param {Array} outputs
   */
  function populateOutputs(outputs) {
    if (!Array.isArray(outputs)) return;
    const find = (type) => outputs.find(o => o.type === type) || {};
    const pp      = find('propresenter');
    const ew      = find('easyworship');
    const clip    = find('clipboard');
    const webhook = find('http_webhook');
    const file    = find('text_file');
    const obs     = find('obs_websocket');
    const tcp     = find('tcp_raw');
    const ndi     = find('ndi');
    if (dom.outPpEnabled)      dom.outPpEnabled.checked      = !!pp.enabled;
    if (dom.outPpIp)           dom.outPpIp.value             = pp.ip    || '';
    if (dom.outPpPort)         dom.outPpPort.value           = pp.port  || '1025';
    if (dom.outPpUuid)         dom.outPpUuid.value           = pp.uuid  || '';
    if (dom.outEwEnabled)      dom.outEwEnabled.checked      = !!ew.enabled;
    if (dom.outEwPath)         dom.outEwPath.value           = ew.path || '';
    if (dom.outClipEnabled)    dom.outClipEnabled.checked    = !!clip.enabled;
    if (dom.outWebhookEnabled) dom.outWebhookEnabled.checked = !!webhook.enabled;
    if (dom.outWebhookUrl)     dom.outWebhookUrl.value       = webhook.url || '';
    if (dom.outFileEnabled)    dom.outFileEnabled.checked    = !!file.enabled;
    if (dom.outFilePath)       dom.outFilePath.value         = file.path || '';
    if (dom.outObsEnabled)     dom.outObsEnabled.checked     = !!obs.enabled;
    if (dom.outObsIp)          dom.outObsIp.value            = obs.ip       || '127.0.0.1';
    if (dom.outObsPort)        dom.outObsPort.value          = obs.port     || '4455';
    if (dom.outObsPassword)    dom.outObsPassword.value      = obs.password || '';
    if (dom.outObsSource)      dom.outObsSource.value        = obs.source   || 'BibleVerse';
    if (dom.outTcpEnabled)     dom.outTcpEnabled.checked     = !!tcp.enabled;
    if (dom.outTcpIp)          dom.outTcpIp.value            = tcp.ip   || '';
    if (dom.outTcpPort)        dom.outTcpPort.value          = tcp.port || '';
    if (dom.outNdiEnabled)     dom.outNdiEnabled.checked     = !!ndi.enabled;
    if (dom.outNdiName)        dom.outNdiName.value          = ndi.stream_name || 'BibleCue';
  }

  /**
   * Populate the Display card from a display settings object.
   * @param {object} display
   */
  function populateDisplay(display) {
    if (!display) return;
    const setVal = (el, val) => { if (el && val !== undefined && val !== null) el.value = val; };

    const bgType = display.bg_type || 'color';
    const bgTypeRadio = document.querySelector(`input[name="bg-type"][value="${bgType}"]`);
    if (bgTypeRadio) bgTypeRadio.checked = true;
    if (dom.bgColorRow) dom.bgColorRow.style.display = bgType === 'color' ? '' : 'none';
    if (dom.bgImageRow) dom.bgImageRow.style.display = bgType === 'image' ? '' : 'none';

    setVal(dom.inpBgColor, display.bg_color || '#060B18');
    displayState.bg_image = display.bg_image || '';
    renderBgImagePreview();

    setVal(dom.selFontFamily, display.font_family || 'Playfair Display');
    setVal(dom.selFontWeight, display.font_weight || '500');
    setVal(dom.inpVerseSize, display.verse_size ?? 100);
    setVal(dom.inpRefSize,   display.ref_size ?? 100);
    updateStepperBounds(dom.inpVerseSize);
    updateStepperBounds(dom.inpRefSize);
    setVal(dom.inpTextColor,   display.text_color   || '#F8FAFC');
    setVal(dom.inpAccentColor, display.accent_color || '#F59E0B');

    displayState.align_h = display.align_h || 'center';
    displayState.align_v = display.align_v || 'middle';
    setAlignButtons();
  }

  /** Reflects displayState.align_h/align_v onto the alignment button group. */
  function setAlignButtons() {
    dom.alignBtns.forEach(btn => {
      const active = btn.dataset.value === displayState[btn.dataset.group === 'h' ? 'align_h' : 'align_v'];
      btn.classList.toggle('align-btn-active', active);
    });
  }

  /** Disables a stepper's -/+ buttons once the input hits that bound. */
  function updateStepperBounds(input) {
    if (!input) return;
    const min = input.min !== '' ? Number(input.min) : -Infinity;
    const max = input.max !== '' ? Number(input.max) : Infinity;
    const val = Number(input.value);
    document.querySelectorAll(`.stepper-btn[data-step-target="${input.id}"]`).forEach(btn => {
      const dir = Number(btn.dataset.stepDir);
      btn.disabled = dir < 0 ? val <= min : val >= max;
    });
  }

  /** Shows/hides the small background-image thumbnail + filename. */
  function renderBgImagePreview() {
    if (!dom.bgImagePreview) return;
    if (displayState.bg_image) {
      dom.bgImagePreview.hidden = false;
      dom.bgImagePreview.style.backgroundImage = `url("${displayState.bg_image}")`;
    } else {
      dom.bgImagePreview.hidden = true;
      dom.bgImagePreview.style.backgroundImage = '';
    }
  }

  // ===========================================================================
  // SECTION 9: Settings — Collect & Send
  // ===========================================================================

  /**
   * Collect all output plugin settings from the form.
   * @returns {Array}
   */
  function collectOutputs() {
    return [
      { type: 'propresenter', enabled: dom.outPpEnabled ? dom.outPpEnabled.checked : false,
        ip: dom.outPpIp ? dom.outPpIp.value.trim() : '',
        port: dom.outPpPort ? dom.outPpPort.value.trim() : '1025',
        uuid: dom.outPpUuid ? dom.outPpUuid.value.trim() : '' },
      { type: 'easyworship', enabled: dom.outEwEnabled ? dom.outEwEnabled.checked : false,
        path: dom.outEwPath ? dom.outEwPath.value.trim() : '' },
      { type: 'clipboard', enabled: dom.outClipEnabled ? dom.outClipEnabled.checked : false },
      { type: 'http_webhook', enabled: dom.outWebhookEnabled ? dom.outWebhookEnabled.checked : false,
        url: dom.outWebhookUrl ? dom.outWebhookUrl.value.trim() : '' },
      { type: 'text_file', enabled: dom.outFileEnabled ? dom.outFileEnabled.checked : false,
        path: dom.outFilePath ? dom.outFilePath.value.trim() : '' },
      { type: 'obs_websocket', enabled: dom.outObsEnabled ? dom.outObsEnabled.checked : false,
        ip: dom.outObsIp ? dom.outObsIp.value.trim() : '127.0.0.1',
        port: dom.outObsPort ? dom.outObsPort.value.trim() : '4455',
        password: dom.outObsPassword ? dom.outObsPassword.value : '',
        source: dom.outObsSource ? dom.outObsSource.value.trim() : 'BibleVerse' },
      { type: 'tcp_raw', enabled: dom.outTcpEnabled ? dom.outTcpEnabled.checked : false,
        ip: dom.outTcpIp ? dom.outTcpIp.value.trim() : '',
        port: dom.outTcpPort ? dom.outTcpPort.value.trim() : '' },
      { type: 'ndi', enabled: dom.outNdiEnabled ? dom.outNdiEnabled.checked : false,
        stream_name: dom.outNdiName ? dom.outNdiName.value.trim() || 'BibleCue' : 'BibleCue' },
    ];
  }

  /**
   * Collect the Display card into a display settings object.
   * @returns {object}
   */
  function collectDisplay() {
    const bgTypeEl = document.querySelector('input[name="bg-type"]:checked');
    return {
      bg_type:      bgTypeEl ? bgTypeEl.value : 'color',
      bg_color:     dom.inpBgColor ? dom.inpBgColor.value : '#060B18',
      bg_image:     displayState.bg_image,
      text_color:   dom.inpTextColor ? dom.inpTextColor.value : '#F8FAFC',
      accent_color: dom.inpAccentColor ? dom.inpAccentColor.value : '#F59E0B',
      font_family:  dom.selFontFamily ? dom.selFontFamily.value : 'Playfair Display',
      font_weight:  dom.selFontWeight ? dom.selFontWeight.value : '500',
      verse_size:   dom.inpVerseSize ? Number(dom.inpVerseSize.value) || 100 : 100,
      ref_size:     dom.inpRefSize ? Number(dom.inpRefSize.value) || 100 : 100,
      align_h:      displayState.align_h,
      align_v:      displayState.align_v,
    };
  }

  /**
   * Collect all current settings from the form.
   * @returns {object}
   */
  function collectSettings() {
    return {
      translation:   dom.selTranslation ? dom.selTranslation.value          : 'KJV',
      cooldown_secs: dom.inpCooldown    ? Number(dom.inpCooldown.value)     : 8,
      audio_device:  dom.selDevice      ? Number(dom.selDevice.value)       : -1,
      autostart:     dom.chkAutostart   ? dom.chkAutostart.checked          : false,
      mode:          'google',  // the only mode now — see index.html for why there's no picker
      outputs:       collectOutputs(),
      display:       collectDisplay(),
    };
  }

  /**
   * Send current settings to the Python backend and show save indicator.
   */
  function saveSettings() {
    send({ type: 'set_settings', data: collectSettings() });
    showSaveIndicator();
  }

  /**
   * Show a toast notification.
   * @param {string} message
   * @param {string} type     - 'success' | 'error' | 'info' | 'warning'
   * @param {number} duration - ms before auto-dismiss
   */
  function showToast(message, type = 'success', duration = 2500) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    const icons = { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' };
    toast.innerHTML = `<span class="toast-icon">${icons[type] || '●'}</span><span class="toast-msg">${message}</span>`;

    container.appendChild(toast);

    // Trigger enter animation
    requestAnimationFrame(() => toast.classList.add('toast-visible'));

    setTimeout(() => {
      toast.classList.remove('toast-visible');
      toast.classList.add('toast-hiding');
      setTimeout(() => {
        if (container.contains(toast)) container.removeChild(toast);
      }, 300);
    }, duration);
  }

  /**
   * Show the "✓ Saved" indicator briefly.
   */
  function showSaveIndicator() {
    showToast('Settings saved', 'success', 2000);
    // Also keep the original indicator for backward compat
    if (!dom.saveIndicator) return;
    clearTimeout(saveIndicatorTimer);
    dom.saveIndicator.classList.add('visible');
    saveIndicatorTimer = setTimeout(() => {
      dom.saveIndicator.classList.remove('visible');
    }, 2500);
  }

  // Debounced version for input changes
  const debouncedSave = debounce(saveSettings, 800);

  // ===========================================================================
  // SECTION 10: Devices Dropdown
  // ===========================================================================

  /**
   * Populate the audio device selector from a device list.
   * @param {Array<{id: number, name: string}>} list
   */
  function populateDevices(list) {
    if (!dom.selDevice || !Array.isArray(list)) return;
    const currentVal = dom.selDevice.value;
    dom.selDevice.innerHTML = '';
    list.forEach(device => {
      const opt = document.createElement('option');
      opt.value = device.id;
      opt.textContent = device.name;
      dom.selDevice.appendChild(opt);
    });
    // Restore previous selection if it still exists
    dom.selDevice.value = currentVal;
  }

  // ===========================================================================
  // SECTION 11: WebSocket — Message Router
  // ===========================================================================

  /**
   * Handle a parsed incoming WebSocket message object.
   * @param {object} msg
   */
  function handleMessage(msg) {
    switch (msg.type) {

      case 'transcript': {
        // Interim transcripts start with ">> " and are replaced by the next entry
        const prefix = msg.interim ? '>> ' : '';
        appendLog('transcript-log', `${prefix}${msg.text || ''}`);
        break;
      }

      case 'log': {
        if (msg.panel === 'transcript') {
          appendLog('transcript-log', msg.text || '');
        } else if (msg.panel === 'detection') {
          appendLog('detection-log', msg.text || '');
        }
        break;
      }

      case 'verse_display': {
        displayVerse(msg.verse_text, msg.reference, msg.translation);
        break;
      }

      case 'status': {
        setStatus(msg.text || '', msg.color || 'gray');
        break;
      }

      case 'dg_status': {
        if (dom.dgStatusMain) dom.dgStatusMain.textContent = msg.text || '';
        break;
      }

      case 'settings_response': {
        populateSettings(msg.data);
        break;
      }

      case 'devices_response': {
        populateDevices(msg.list);
        break;
      }

      case 'listening_state': {
        setListeningState(!!msg.active);
        break;
      }

      case 'python-crashed': {
        const backendDot  = document.getElementById('backend-dot');
        const backendText = document.getElementById('backend-text');
        if (backendDot)  backendDot.classList.remove('active');
        if (backendText) backendText.textContent = 'Backend Offline';
        appendLog('detection-log', '[ERROR] Python backend crashed');
        showToast('Backend process crashed', 'error', 4000);
        break;
      }

      case 'listener_url': {
        // Show the clickable URL link in the sidebar — does NOT open the browser
        const url = msg.url;
        const urlRow  = document.getElementById('listener-url-row');
        const urlLink = document.getElementById('listener-url-link');
        if (urlRow && urlLink && url) {
          urlLink.textContent = url;
          urlLink.onclick = (e) => {
            e.preventDefault();
            if (window.electronAPI && window.electronAPI.openExternalUrl) {
              window.electronAPI.openExternalUrl(url);
            } else {
              window.open(url, '_blank');
            }
          };
          urlRow.style.display = 'flex';
        }
        break;
      }

      case 'open_browser_url': {
        // Listening has started — open the browser listener page
        const url = msg.url;
        if (url && window.electronAPI && window.electronAPI.openExternalUrl) {
          window.electronAPI.openExternalUrl(url);
        }
        const urlRow  = document.getElementById('listener-url-row');
        const urlLink = document.getElementById('listener-url-link');
        if (urlRow && urlLink && url) {
          urlLink.textContent = url;
          urlLink.onclick = (e) => {
            e.preventDefault();
            if (window.electronAPI && window.electronAPI.openExternalUrl) {
              window.electronAPI.openExternalUrl(url);
            } else {
              window.open(url, '_blank');
            }
          };
          urlRow.style.display = 'flex';
        }
        break;
      }

      default:
        console.debug('[WCI] Unhandled message type:', msg.type, msg);
    }
  }

  // ===========================================================================
  // SECTION 12: WebSocket — Connection Manager
  // ===========================================================================

  /**
   * Open a WebSocket connection to the Python backend.
   * Retries with exponential backoff on disconnect.
   */
  function connectWebSocket() {
    if (ws) {
      // Clean up any existing socket
      ws.onopen = ws.onclose = ws.onerror = ws.onmessage = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
      ws = null;
    }

    setStatus('Reconnecting...', 'red');
    appendLog('detection-log', `[WS] Connecting to ${WS_URL}...`);

    try {
      ws = new WebSocket(WS_URL);
    } catch (err) {
      console.error('[WCI] WebSocket construction failed:', err);
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      console.log('[WCI] WebSocket connected');
      wsRetryDelay = 1000; // reset backoff
      setStatus('Connected to backend', 'green');
      appendLog('detection-log', '[WS] Connected to backend');

      const backendDot  = document.getElementById('backend-dot');
      const backendText = document.getElementById('backend-text');
      if (backendDot)  backendDot.classList.add('active');
      if (backendText) backendText.textContent = 'Backend Online';

      // Request initial state
      send({ type: 'get_settings' });
      send({ type: 'get_devices' });
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      } catch (err) {
        console.error('[WCI] Failed to parse message:', event.data, err);
      }
    };

    ws.onclose = (event) => {
      console.warn('[WCI] WebSocket closed:', event.code, event.reason);
      setStatus('Reconnecting...', 'red');
      setListeningState(false);

      const backendDot  = document.getElementById('backend-dot');
      const backendText = document.getElementById('backend-text');
      if (backendDot)  backendDot.classList.remove('active');
      if (backendText) backendText.textContent = 'Backend Offline';

      scheduleReconnect();
    };

    ws.onerror = (err) => {
      console.error('[WCI] WebSocket error:', err);
      // onclose will fire after onerror, so reconnect is handled there
    };
  }

  /**
   * Schedule a reconnection attempt with exponential backoff.
   */
  function scheduleReconnect() {
    appendLog('detection-log', `[WS] Reconnecting in ${wsRetryDelay / 1000}s...`);
    setTimeout(() => {
      connectWebSocket();
    }, wsRetryDelay);
    wsRetryDelay = Math.min(wsRetryDelay * 2, WS_MAX_DELAY);
  }

  // ===========================================================================
  // SECTION 13: Listen Button
  // ===========================================================================

  if (dom.btnListen) {
    dom.btnListen.addEventListener('click', () => {
      if (isListening) {
        send({ type: 'stop_listening' });
      } else {
        send({ type: 'start_listening' });
      }
    });
  }

  // ===========================================================================
  // SECTION 14: Settings Form — Change Listeners
  // ===========================================================================

  const settingsInputs = [
    dom.selTranslation,
    dom.inpCooldown,
    dom.selDevice,
    dom.chkAutostart,
    dom.outPpIp, dom.outPpPort, dom.outPpUuid,
    dom.outEwPath,
    dom.outWebhookUrl, dom.outFilePath,
    dom.outObsIp, dom.outObsPort, dom.outObsPassword, dom.outObsSource,
    dom.outTcpIp, dom.outTcpPort,
    dom.outNdiName,
    dom.inpBgColor, dom.selFontFamily, dom.selFontWeight,
    dom.inpVerseSize, dom.inpRefSize, dom.inpTextColor, dom.inpAccentColor,
  ].filter(Boolean);

  settingsInputs.forEach(el => {
    const eventType = (el.type === 'checkbox' || el.tagName === 'SELECT' || el.type === 'color') ? 'change' : 'input';
    el.addEventListener(eventType, debouncedSave);
    if (el === dom.inpVerseSize || el === dom.inpRefSize) {
      el.addEventListener('input', () => updateStepperBounds(el));
    }
  });

  // Output enabled checkboxes — immediate save
  [dom.outPpEnabled, dom.outEwEnabled, dom.outClipEnabled, dom.outWebhookEnabled,
   dom.outFileEnabled, dom.outObsEnabled, dom.outTcpEnabled, dom.outNdiEnabled]
    .filter(Boolean)
    .forEach(el => el.addEventListener('change', saveSettings));

  // Background type (color/image) — immediate save, toggles which row shows
  dom.bgTypeRadios.forEach(radio => {
    radio.addEventListener('change', () => {
      const isImage = radio.value === 'image' && radio.checked;
      if (radio.checked) {
        if (dom.bgColorRow) dom.bgColorRow.style.display = radio.value === 'color' ? '' : 'none';
        if (dom.bgImageRow) dom.bgImageRow.style.display = radio.value === 'image' ? '' : 'none';
      }
      if (radio.checked) saveSettings();
    });
  });

  // Alignment buttons — immediate save
  dom.alignBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const group = btn.dataset.group === 'h' ? 'align_h' : 'align_v';
      displayState[group] = btn.dataset.value;
      setAlignButtons();
      saveSettings();
    });
  });

  // Size steppers (verse/reference size) — number-input spinners are hidden
  // app-wide for a consistent look, so +/- buttons are the only way to
  // adjust these without typing. Reuses the existing debounced-save
  // listener already attached to the input by dispatching a real 'input'
  // event rather than duplicating the save call here.
  dom.stepperBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const input = document.getElementById(btn.dataset.stepTarget);
      if (!input) return;
      const step = Number(input.step) || 1;
      const min  = input.min !== '' ? Number(input.min) : -Infinity;
      const max  = input.max !== '' ? Number(input.max) : Infinity;
      const dir  = Number(btn.dataset.stepDir);
      const next = Math.min(max, Math.max(min, (Number(input.value) || 0) + dir * step));
      input.value = next;
      updateStepperBounds(input);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });

  // Background image picker — reads the chosen file as a data URL (no main-
  // process/IPC needed, contextIsolation-safe) and stores it directly in
  // settings, same as any other display field.
  if (dom.btnBgImagePick && dom.inpBgImageFile) {
    dom.btnBgImagePick.addEventListener('click', () => dom.inpBgImageFile.click());
    dom.inpBgImageFile.addEventListener('change', () => {
      const file = dom.inpBgImageFile.files && dom.inpBgImageFile.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        displayState.bg_image = reader.result;
        renderBgImagePreview();
        saveSettings();
      };
      reader.onerror = () => showToast('Could not read that image', 'error');
      reader.readAsDataURL(file);
      dom.inpBgImageFile.value = '';
    });
  }
  if (dom.btnBgImageClear) {
    dom.btnBgImageClear.addEventListener('click', () => {
      displayState.bg_image = '';
      renderBgImagePreview();
      saveSettings();
    });
  }

  // ===========================================================================
  // SECTION 15: Advanced Settings Collapsible
  // ===========================================================================

  // Collapsible bodies animate via an inline max-height measured from the
  // element's real scrollHeight, not a fixed CSS cap. A fixed cap has to be
  // either too small (clips real content — the Outputs card has 6 rows,
  // each with its own expandable fields) or way oversized "to be safe",
  // and an oversized max-height transitioning inside a scrollable ancestor
  // (#sidebar) causes the browser's scroll-anchoring to jump the scroll
  // position unpredictably — e.g. the Transcription Mode card at the top
  // scrolling out of reach. Measuring the exact height sidesteps both.
  function openCollapsible (body, arrow) {
    body.classList.add('open');
    if (arrow) arrow.classList.add('open');
    body.style.maxHeight = body.scrollHeight + 'px';
  }
  function closeCollapsible (body, arrow) {
    // Pin the current rendered height first so there's a real starting
    // point for the transition, then collapse on the next frame.
    body.style.maxHeight = body.scrollHeight + 'px';
    requestAnimationFrame(() => {
      body.classList.remove('open');
      if (arrow) arrow.classList.remove('open');
      body.style.maxHeight = '0px';
    });
  }
  // Re-measure an already-open body after its own content changes height
  // (e.g. expanding a nested output's config fields) so newly revealed
  // content isn't clipped by the old max-height value.
  function refreshCollapsible (body) {
    if (body && body.classList.contains('open')) {
      body.style.maxHeight = body.scrollHeight + 'px';
    }
  }

  if (dom.advToggle && dom.advBody) {
    dom.advToggle.addEventListener('click', () => {
      advBodyOpen = !advBodyOpen;
      if (advBodyOpen) openCollapsible(dom.advBody, dom.advArrow);
      else closeCollapsible(dom.advBody, dom.advArrow);
    });
  }

  let outBodyOpen = false;
  if (dom.outToggle && dom.outBody) {
    dom.outToggle.addEventListener('click', () => {
      outBodyOpen = !outBodyOpen;
      if (outBodyOpen) openCollapsible(dom.outBody, dom.outArrow);
      else closeCollapsible(dom.outBody, dom.outArrow);
    });
  }

  let displayBodyOpen = false;
  if (dom.displayToggle && dom.displayBody) {
    dom.displayToggle.addEventListener('click', () => {
      displayBodyOpen = !displayBodyOpen;
      if (displayBodyOpen) openCollapsible(dom.displayBody, dom.displayArrow);
      else closeCollapsible(dom.displayBody, dom.displayArrow);
    });
  }

  // Output plugin expand buttons
  document.querySelectorAll('.output-expand-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      const cfg = document.getElementById(targetId);
      if (cfg) {
        cfg.classList.toggle('open');
        btn.classList.toggle('open');
        refreshCollapsible(dom.outBody);
      }
    });
  });


  // ===========================================================================
  // SECTION 17: Manual Verse
  // ===========================================================================

  /**
   * Send the current value of the manual verse input.
   */
  function sendManualVerse() {
    if (!dom.inpManual) return;
    const text = dom.inpManual.value.trim();
    if (!text) return;
    send({ type: 'manual_verse', text });
    dom.inpManual.value = '';
  }

  if (dom.btnManualSend) {
    dom.btnManualSend.addEventListener('click', sendManualVerse);
  }

  // Fullscreen live scripture button
  const btnFullscreen = document.getElementById('btn-fullscreen');
  if (btnFullscreen) {
    btnFullscreen.addEventListener('click', () => {
      window.electronAPI?.openFullscreen();
    });
  }

  // Copy verse button
  const btnCopyVerse = document.getElementById('btn-copy-verse');
  if (btnCopyVerse) {
    btnCopyVerse.addEventListener('click', () => {
      const verseT = dom.verseText ? dom.verseText.textContent : '';
      const verseR = dom.verseRef  ? dom.verseRef.textContent  : '';
      if (verseT && verseT !== '—') {
        const text = `${verseT} — ${verseR}`;
        navigator.clipboard.writeText(text).then(() => {
          showToast('Verse copied to clipboard', 'success');
        }).catch(() => {
          showToast('Could not copy verse', 'error');
        });
      }
    });
  }

  if (dom.inpManual) {
    dom.inpManual.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        sendManualVerse();
      }
    });
  }

  // ===========================================================================
  // SECTION 18: Clear Log Buttons
  // ===========================================================================

  dom.clearLogBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      if (targetId) {
        const panel = document.getElementById(targetId);
        if (panel) panel.innerHTML = '';
        // Reset interim tracking if clearing transcript
        if (targetId === 'transcript-log') lastEntryWasInterim = false;
        // Reset count badge
        const countId = targetId === 'transcript-log' ? 'tx-count' : 'dt-count';
        const countEl = document.getElementById(countId);
        if (countEl) countEl.textContent = '0';
      }
    });
  });

  // Copy-all buttons for the Live Transcript / Scripture Detection panels
  dom.copyLogBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      const panel = targetId ? document.getElementById(targetId) : null;
      if (!panel) return;
      const lines = Array.from(panel.querySelectorAll('.log-entry')).map(el => el.textContent);
      if (!lines.length) {
        showToast('Nothing to copy yet', 'info');
        return;
      }
      navigator.clipboard.writeText(lines.join('\n')).then(() => {
        showToast(`Copied ${lines.length} line${lines.length === 1 ? '' : 's'}`, 'success');
      }).catch(() => {
        showToast('Could not copy', 'error');
      });
    });
  });

  // ===========================================================================
  // SECTION 19: Custom Titlebar Buttons
  // ===========================================================================

  if (dom.btnMinimize) {
    dom.btnMinimize.addEventListener('click', () => window.electronAPI?.minimize());
  }
  if (dom.btnMaximize) {
    dom.btnMaximize.addEventListener('click', () => window.electronAPI?.maximize());
  }
  if (dom.btnClose) {
    dom.btnClose.addEventListener('click', () => window.electronAPI?.close());
  }

  // ── Application menu bar (File / Edit / View / Help) ────────────────────
  const SETUP_GUIDE_URL = 'https://claude.ai/code/artifact/049238da-f9c3-4f7b-89c2-7f2a8e00ad05';
  const MENU_ACTIONS = {
    'restart-backend': () => { window.electronAPI?.restartPython(); showToast('Restarting backend…', 'info'); },
    'quit':            () => window.electronAPI?.close(),
    'cut':              () => document.execCommand('cut'),
    'copy':             () => document.execCommand('copy'),
    'paste':            () => document.execCommand('paste'),
    'reload':           () => window.electronAPI?.reloadWindow(),
    'toggle-devtools':  () => window.electronAPI?.toggleDevTools(),
    'setup-guide':      () => window.electronAPI?.openExternalUrl(SETUP_GUIDE_URL),
    'report-issue':     () => window.electronAPI?.openExternalUrl('https://github.com/yawpoku/biblecue/issues'),
    'github':           () => window.electronAPI?.openExternalUrl('https://github.com/yawpoku/biblecue'),
  };
  document.querySelectorAll('#menubar .menubar-dropdown button').forEach(btn => {
    btn.addEventListener('click', () => {
      const action = MENU_ACTIONS[btn.dataset.action];
      if (action) action();
      closeAllMenus();
    });
  });
  // Click-to-open (in addition to the CSS :hover), so the menu also works
  // for keyboard/touch users, and so clicking one item then moving to an
  // adjacent one switches menus like a real menu bar.
  const menubarItems = document.querySelectorAll('.menubar-item');
  function closeAllMenus() {
    menubarItems.forEach(m => m.classList.remove('menu-open'));
  }
  menubarItems.forEach(item => {
    item.addEventListener('click', (e) => {
      if (e.target.closest('.menubar-dropdown')) return; // let button clicks through
      const isOpen = item.classList.contains('menu-open');
      closeAllMenus();
      if (!isOpen) item.classList.add('menu-open');
    });
    item.addEventListener('mouseenter', () => {
      if ([...menubarItems].some(m => m.classList.contains('menu-open'))) {
        closeAllMenus();
        item.classList.add('menu-open');
      }
    });
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#menubar')) closeAllMenus();
  });

  // ── Help / setup guide modal ─────────────────────────────────────────────
  function openHelp () {
    if (dom.helpOverlay) dom.helpOverlay.hidden = false;
  }
  function closeHelp () {
    if (dom.helpOverlay) dom.helpOverlay.hidden = true;
  }
  if (dom.btnHelp) {
    dom.btnHelp.addEventListener('click', openHelp);
  }
  if (dom.btnHelpClose) {
    dom.btnHelpClose.addEventListener('click', closeHelp);
  }
  if (dom.helpOverlay) {
    // Click on the dimmed backdrop (not the modal itself) closes it
    dom.helpOverlay.addEventListener('click', (e) => {
      if (e.target === dom.helpOverlay) closeHelp();
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && dom.helpOverlay && !dom.helpOverlay.hidden) closeHelp();
  });

  // Copy-to-clipboard buttons on code snippets in the help guide
  document.querySelectorAll('.help-copy-btn').forEach(btn => {
    const textEl = btn.previousElementSibling; // the [data-copy-text] span
    if (!textEl) return;
    btn.addEventListener('click', () => {
      const text = textEl.dataset.copyText || textEl.textContent;
      navigator.clipboard.writeText(text).then(() => {
        btn.classList.add('copied');
        btn.textContent = '✓';
        setTimeout(() => {
          btn.classList.remove('copied');
          btn.textContent = '⎘';
        }, 1400);
      }).catch(() => showToast('Could not copy — select the text manually', 'error'));
    });
  });

  // ── Theme toggle (dark ↔ light) ──────────────────────────────────────────
  function applyTheme (theme) {
    document.body.dataset.theme = theme;
    if (dom.btnTheme) {
      dom.btnTheme.textContent = theme === 'light' ? '🌙' : '☀';
      dom.btnTheme.title = theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode';
    }
  }

  // Restore saved preference on boot
  const savedTheme = localStorage.getItem('biblecue-theme') || 'dark';
  applyTheme(savedTheme);

  if (dom.btnTheme) {
    dom.btnTheme.addEventListener('click', () => {
      const next = document.body.dataset.theme === 'light' ? 'dark' : 'light';
      applyTheme(next);
      localStorage.setItem('biblecue-theme', next);
    });
  }

  // ===========================================================================
  // SECTION 20: Keyboard Shortcuts
  // ===========================================================================

  document.addEventListener('keydown', (e) => {
    // Ctrl+L — toggle listening
    if (e.ctrlKey && e.key === 'l') {
      e.preventDefault();
      if (isListening) {
        send({ type: 'stop_listening' });
      } else {
        send({ type: 'start_listening' });
      }
    }

    // Ctrl+B — open browser
    if (e.ctrlKey && e.key === 'b') {
      e.preventDefault();
      send({ type: 'open_browser' });
    }

    // Ctrl+T — toggle light/dark theme
    if (e.ctrlKey && e.key === 't') {
      e.preventDefault();
      if (dom.btnTheme) dom.btnTheme.click();
    }
  });

  // ===========================================================================
  // SECTION 21: Boot — Initial Connection
  // ===========================================================================

  // Set initial UI state
  setListeningState(false);
  setStatus('Connecting...', 'gray');

  // Resolve ws port from settings before connecting
  if (window.electronAPI && window.electronAPI.getWsPort) {
    window.electronAPI.getWsPort().then(port => {
      WS_URL = `ws://127.0.0.1:${port}`;
      connectWebSocket();
    }).catch(() => connectWebSocket());
  } else {
    connectWebSocket();
  }

}); // end DOMContentLoaded
