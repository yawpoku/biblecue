// =============================================================================
// BibleCue — Renderer Process
// Connects to Python backend on ws://127.0.0.1:8767
// =============================================================================

'use strict';

document.addEventListener('DOMContentLoaded', () => {

  // ===========================================================================
  // SECTION 1: DOM References
  // ===========================================================================

  const dom = {
    // Status bar
    statusDot:        document.getElementById('status-dot'),
    statusText:       document.getElementById('status-text'),
    dgStatus:         document.getElementById('dg-status'),
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
    inpDgKey:         document.getElementById('inp-dg-key'),
    selWhisperModel:  document.getElementById('sel-whisper-model'),
    selComputeDevice: document.getElementById('sel-compute-device'),
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

    // Advanced settings
    advToggle:        document.getElementById('adv-toggle'),
    advBody:          document.getElementById('adv-body'),
    advArrow:         document.getElementById('adv-arrow'),

    // API key visibility
    btnShowKey:       document.getElementById('btn-show-key'),

    // Manual verse
    btnManualSend:    document.getElementById('btn-manual-send'),
    inpManual:        document.getElementById('inp-manual'),

    // Titlebar
    btnMinimize:      document.getElementById('btn-minimize'),
    btnMaximize:      document.getElementById('btn-maximize'),
    btnClose:         document.getElementById('btn-close'),
    btnTheme:         document.getElementById('btn-theme'),

    // Clear log buttons
    clearLogBtns:     document.querySelectorAll('.btn-clear-log'),
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
    setVal(dom.inpDgKey,        data.deepgram_key);
    setVal(dom.selWhisperModel, data.whisper_model);
    setVal(dom.selComputeDevice, data.compute_device);
    setChk(dom.chkAutostart,    data.autostart);

    // Mode radio buttons
    if (data.mode) {
      const modeRadio = document.querySelector(`input[name="mode"][value="${data.mode}"]`);
      if (modeRadio) modeRadio.checked = true;
    }

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
  }

  /**
   * Populate output plugin fields from an outputs array.
   * @param {Array} outputs
   */
  function populateOutputs(outputs) {
    if (!Array.isArray(outputs)) return;
    const find = (type) => outputs.find(o => o.type === type) || {};
    const pp      = find('propresenter');
    const clip    = find('clipboard');
    const webhook = find('http_webhook');
    const file    = find('text_file');
    const obs     = find('obs_websocket');
    const tcp     = find('tcp_raw');
    if (dom.outPpEnabled)      dom.outPpEnabled.checked      = !!pp.enabled;
    if (dom.outPpIp)           dom.outPpIp.value             = pp.ip    || '';
    if (dom.outPpPort)         dom.outPpPort.value           = pp.port  || '1025';
    if (dom.outPpUuid)         dom.outPpUuid.value           = pp.uuid  || '';
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
    ];
  }

  /**
   * Collect all current settings from the form.
   * @returns {object}
   */
  function collectSettings() {
    const modeEl = document.querySelector('input[name="mode"]:checked');
    return {
      translation:   dom.selTranslation ? dom.selTranslation.value          : 'KJV',
      cooldown_secs: dom.inpCooldown    ? Number(dom.inpCooldown.value)     : 8,
      deepgram_key:  dom.inpDgKey       ? dom.inpDgKey.value.trim()         : '',
      whisper_model: dom.selWhisperModel? dom.selWhisperModel.value         : 'base',
      compute_device: dom.selComputeDevice ? dom.selComputeDevice.value : 'cpu',
      audio_device:  dom.selDevice      ? Number(dom.selDevice.value)       : -1,
      autostart:     dom.chkAutostart   ? dom.chkAutostart.checked          : false,
      mode:          modeEl             ? modeEl.value                      : 'google',
      outputs:       collectOutputs(),
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
        if (dom.dgStatus)     dom.dgStatus.textContent     = msg.text || '';
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
    dom.inpDgKey,
    dom.selWhisperModel,
    dom.selComputeDevice,
    dom.selDevice,
    dom.chkAutostart,
    dom.outPpIp, dom.outPpPort, dom.outPpUuid,
    dom.outWebhookUrl, dom.outFilePath,
    dom.outObsIp, dom.outObsPort, dom.outObsPassword, dom.outObsSource,
    dom.outTcpIp, dom.outTcpPort,
  ].filter(Boolean);

  settingsInputs.forEach(el => {
    const eventType = (el.type === 'checkbox') ? 'change' : 'input';
    el.addEventListener(eventType, debouncedSave);
  });

  // Mode radio buttons — immediate save, no debounce
  document.querySelectorAll('input[name="mode"]').forEach(radio => {
    radio.addEventListener('change', saveSettings);
  });

  // Output enabled checkboxes — immediate save
  [dom.outPpEnabled, dom.outClipEnabled, dom.outWebhookEnabled,
   dom.outFileEnabled, dom.outObsEnabled, dom.outTcpEnabled]
    .filter(Boolean)
    .forEach(el => el.addEventListener('change', saveSettings));

  // ===========================================================================
  // SECTION 15: Advanced Settings Collapsible
  // ===========================================================================

  if (dom.advToggle && dom.advBody) {
    dom.advToggle.addEventListener('click', () => {
      advBodyOpen = !advBodyOpen;
      dom.advBody.classList.toggle('open', advBodyOpen);
      if (dom.advArrow) dom.advArrow.classList.toggle('open', advBodyOpen);
    });
  }

  let outBodyOpen = false;
  if (dom.outToggle && dom.outBody) {
    dom.outToggle.addEventListener('click', () => {
      outBodyOpen = !outBodyOpen;
      dom.outBody.classList.toggle('open', outBodyOpen);
      if (dom.outArrow) dom.outArrow.classList.toggle('open', outBodyOpen);
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
      }
    });
  });

  // ===========================================================================
  // SECTION 16: Show/Hide API Key
  // ===========================================================================

  if (dom.btnShowKey && dom.inpDgKey) {
    dom.btnShowKey.addEventListener('click', () => {
      if (dom.inpDgKey.type === 'password') {
        dom.inpDgKey.type = 'text';
        dom.btnShowKey.textContent = '🙈';
      } else {
        dom.inpDgKey.type = 'password';
        dom.btnShowKey.textContent = '👁';
      }
    });
  }

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
