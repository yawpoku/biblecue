'use strict'

const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')

// ---------------------------------------------------------------------------
// Single-instance lock
// ---------------------------------------------------------------------------
const gotTheLock = app.requestSingleInstanceLock()
if (!gotTheLock) {
  app.quit()
  process.exit(0)
}

// ---------------------------------------------------------------------------
// Window-state persistence (simple JSON — no extra dependency needed)
// ---------------------------------------------------------------------------
const STATE_FILE = path.join(app.getPath('userData'), 'window-state.json')

function loadWindowState () {
  try {
    const raw = fs.readFileSync(STATE_FILE, 'utf8')
    return JSON.parse(raw)
  } catch {
    return { width: 1200, height: 780, x: undefined, y: undefined }
  }
}

function saveWindowState (win) {
  try {
    if (win.isMaximized() || win.isMinimized()) return
    const bounds = win.getBounds()
    fs.writeFileSync(STATE_FILE, JSON.stringify(bounds), 'utf8')
  } catch (err) {
    console.error('[state] Failed to save window state:', err.message)
  }
}

// ---------------------------------------------------------------------------
// Python process management
// ---------------------------------------------------------------------------
let pythonProcess = null
let mainWindow = null

function getPythonScriptPath () {
  if (app.isPackaged) {
    // Bundled standalone binary — Windows uses backend.exe, macOS/Linux use backend
    const binaryName = process.platform === 'win32' ? 'backend.exe' : 'backend'
    const binPath = path.join(process.resourcesPath, 'python', binaryName)
    if (fs.existsSync(binPath)) return binPath
    return path.join(process.resourcesPath, 'python', 'backend.py')
  }
  // Development: biblecue.py lives one level up from biblecue-desktop/
  const devPath = path.join(__dirname, '..', 'biblecue.py')
  if (fs.existsSync(devPath)) return devPath
  return path.join(__dirname, 'backend.py')
}

// macOS quarantines every file in a downloaded .app. Approving the app itself
// (right-click → Open / "Open Anyway") does not clear the flag on the bundled
// backend binary, which macOS then kills on spawn. Since BibleCue isn't
// notarized, strip the quarantine attribute from our own backend before
// launching it. No-op on other platforms and in dev.
function clearBackendQuarantine (scriptPath) {
  if (process.platform !== 'darwin' || !app.isPackaged) return
  try {
    const { execFileSync } = require('child_process')
    const pythonDir = path.dirname(scriptPath) // …/Contents/Resources/python
    execFileSync('/usr/bin/xattr', ['-dr', 'com.apple.quarantine', pythonDir], {
      timeout: 5000,
      stdio: 'ignore'
    })
    console.log('[python] cleared com.apple.quarantine on', pythonDir)
  } catch (err) {
    // xattr exits non-zero when the attribute isn't present — harmless.
    console.log('[python] quarantine clear skipped:', err.message)
  }
}

function spawnPython () {
  const scriptPath = getPythonScriptPath()

  if (!fs.existsSync(scriptPath)) {
    console.warn('[python] backend not found at:', scriptPath)
    if (mainWindow) {
      mainWindow.webContents.send('python-log', `[WARN] backend not found at: ${scriptPath}`)
    }
    return
  }

  // If it's a bundled standalone binary, spawn it directly — no Python needed
  if (scriptPath.endsWith('.exe') || (!scriptPath.endsWith('.py') && !scriptPath.endsWith('.pyc'))) {
    clearBackendQuarantine(scriptPath)
    console.log(`[python] Spawning bundled binary: ${scriptPath}`)
    const proc = spawn(scriptPath, ['--headless'], {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: process.platform === 'win32'
    })
    proc.on('error', (err) => {
      console.error('[python] spawn error:', err.message)
      if (mainWindow) {
        mainWindow.webContents.send('python-log', `[ERROR] spawn error: ${err.message}`)
        mainWindow.webContents.send('python-crashed', { code: null, signal: null, message: err.message })
      }
    })
    proc.on('spawn', () => {
      console.log(`[python] Bundled backend started with PID ${proc.pid}`)
      pythonProcess = proc
      attachPythonListeners(proc)
    })
    return
  }

  // Development mode: find Python and run the .py script
  function trySpawn (executables, index = 0) {
    if (index >= executables.length) {
      const msg = 'Python not found. Please install Python 3 and ensure it is on your PATH.'
      console.error('[python]', msg)
      if (mainWindow) {
        mainWindow.webContents.send('python-log', `[ERROR] ${msg}`)
        mainWindow.webContents.send('python-crashed', { code: null, signal: null, message: msg })
      }
      dialog.showErrorBox('Python Not Found', msg)
      return
    }

    const exe = executables[index]
    console.log(`[python] Trying executable: ${exe}`)

    const proc = spawn(exe, [scriptPath, '--headless'], {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })

    proc.on('error', (err) => {
      if (err.code === 'ENOENT') {
        console.warn(`[python] ${exe} not found, trying next...`)
        trySpawn(executables, index + 1)
      } else {
        console.error('[python] spawn error:', err.message)
        if (mainWindow) {
          mainWindow.webContents.send('python-log', `[ERROR] spawn error: ${err.message}`)
          mainWindow.webContents.send('python-crashed', { code: null, signal: null, message: err.message })
        }
      }
    })

    proc.on('spawn', () => {
      console.log(`[python] Process started with PID ${proc.pid} using '${exe}'`)
      pythonProcess = proc
      attachPythonListeners(proc)
    })
  }

  function findPythonW () {
    if (process.platform !== 'win32') return null
    const { execSync } = require('child_process')
    try {
      const out = execSync('where pythonw', { encoding: 'utf8', windowsHide: true, stdio: ['ignore','pipe','pipe'] }).trim()
      const first = out.split('\n')[0].trim()
      if (first && fs.existsSync(first)) return first
    } catch {}
    try {
      const out = execSync('where python', { encoding: 'utf8', windowsHide: true, stdio: ['ignore','pipe','pipe'] }).trim()
      const pyPath = out.split('\n')[0].trim()
      const pyWPath = pyPath.replace(/python\.exe$/i, 'pythonw.exe')
      if (pyWPath !== pyPath && fs.existsSync(pyWPath)) return pyWPath
    } catch {}
    return null
  }

  const pythonwPath = findPythonW()
  const candidates = pythonwPath
    ? [pythonwPath, 'python', 'python3']
    : process.platform === 'win32'
      ? ['pythonw', 'python', 'python3']
      : ['python3', 'python']
  trySpawn(candidates)
}

function attachPythonListeners (proc) {
  // Pipe stdout line by line to renderer
  let stdoutBuf = ''
  proc.stdout.on('data', (chunk) => {
    stdoutBuf += chunk.toString()
    let newlineIdx
    while ((newlineIdx = stdoutBuf.indexOf('\n')) !== -1) {
      const line = stdoutBuf.slice(0, newlineIdx).trimEnd()
      stdoutBuf = stdoutBuf.slice(newlineIdx + 1)
      console.log('[python stdout]', line)
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('python-log', line)
      }
    }
  })

  // Pipe stderr line by line to renderer
  let stderrBuf = ''
  proc.stderr.on('data', (chunk) => {
    stderrBuf += chunk.toString()
    let newlineIdx
    while ((newlineIdx = stderrBuf.indexOf('\n')) !== -1) {
      const line = stderrBuf.slice(0, newlineIdx).trimEnd()
      stderrBuf = stderrBuf.slice(newlineIdx + 1)
      console.error('[python stderr]', line)
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('python-log', `[STDERR] ${line}`)
      }
    }
  })

  proc.on('close', (code, signal) => {
    console.log(`[python] Process exited — code: ${code}, signal: ${signal}`)
    if (pythonProcess === proc) {
      pythonProcess = null
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('python-log', `[INFO] Python process exited (code=${code}, signal=${signal})`)
      if (code !== 0 && code !== null) {
        mainWindow.webContents.send('python-crashed', { code, signal, message: `Exited with code ${code}` })
      }
    }
  })
}

function killPython () {
  if (pythonProcess) {
    console.log('[python] Killing process PID', pythonProcess.pid)
    try {
      pythonProcess.kill('SIGTERM')
    } catch (err) {
      console.warn('[python] Kill failed:', err.message)
    }
    pythonProcess = null
  }
}

// ---------------------------------------------------------------------------
// Create main window
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Right-click context menu (Electron shows none by default)
// ---------------------------------------------------------------------------
// Covers the log panels, the verse display, and text inputs (manual verse,
// Deepgram key, output fields) — anywhere a user might select or type text.
function attachContextMenu (webContents) {
  webContents.on('context-menu', (_event, params) => {
    const items = []

    if (params.isEditable) {
      items.push(
        { label: 'Cut', role: 'cut', enabled: params.editFlags.canCut },
        { label: 'Copy', role: 'copy', enabled: params.editFlags.canCopy },
        { label: 'Paste', role: 'paste', enabled: params.editFlags.canPaste },
        { type: 'separator' },
        { label: 'Select All', role: 'selectAll', enabled: params.editFlags.canSelectAll }
      )
    } else if (params.selectionText) {
      items.push({ label: 'Copy', role: 'copy' })
    }

    if (!items.length) return
    Menu.buildFromTemplate(items).popup({ window: BrowserWindow.fromWebContents(webContents) })
  })
}

// ---------------------------------------------------------------------------
// Application menu — File / Edit / View / Help. The window itself is
// frame:false with a custom-drawn titlebar, and on Windows/Linux a
// frameless window shows no menu bar unless explicitly told to: build a
// real one (so every command is reachable from it, not just the custom
// titlebar buttons) and force it visible. macOS always shows the app
// menu bar at the screen top regardless of window frame, so no visibility
// call is needed there.
// ---------------------------------------------------------------------------
function buildAppMenu () {
  const isMac = process.platform === 'darwin'
  const SETUP_GUIDE_URL = 'https://claude.ai/code/artifact/049238da-f9c3-4f7b-89c2-7f2a8e00ad05'

  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' }
      ]
    }] : []),
    {
      label: 'File',
      submenu: [
        {
          label: 'Restart Backend',
          click: () => { killPython(); setTimeout(() => spawnPython(), 500) }
        },
        { type: 'separator' },
        isMac ? { role: 'close' } : { role: 'quit', label: 'Exit' }
      ]
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' }
      ]
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    {
      label: 'Window',
      submenu: [
        { role: 'minimize' },
        ...(isMac ? [{ role: 'zoom' }, { type: 'separator' }, { role: 'front' }] : [{ role: 'close' }])
      ]
    },
    {
      role: 'help',
      submenu: [
        { label: 'Setup Guide', click: () => shell.openExternal(SETUP_GUIDE_URL) },
        { label: 'Report an Issue', click: () => shell.openExternal('https://github.com/yawpoku/biblecue/issues') },
        { label: 'View on GitHub', click: () => shell.openExternal('https://github.com/yawpoku/biblecue') },
        { type: 'separator' },
        { label: `BibleCue v${app.getVersion()}`, enabled: false }
      ]
    }
  ]

  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function createWindow () {
  const state = loadWindowState()

  mainWindow = new BrowserWindow({
    width: Math.max(state.width || 1200, 1100),
    height: Math.max(state.height || 780, 700),
    x: state.x,
    y: state.y,
    minWidth: 1100,
    minHeight: 700,
    frame: false,
    titleBarStyle: 'hidden',
    // macOS keeps its native red/yellow/green controls even with frame:false;
    // position them to sit centered in the 32px custom titlebar instead of
    // Electron's default (which overlaps the logo). The renderer insets the
    // titlebar content and hides the custom min/max/close on darwin — see
    // body.is-mac in style.css.
    ...(process.platform === 'darwin' ? { trafficLightPosition: { x: 14, y: 10 } } : {}),
    backgroundColor: '#060B18',
    icon: path.join(__dirname, 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
      preload: path.join(__dirname, 'preload.js')
    }
  })

  // Load the renderer
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'))
  attachContextMenu(mainWindow.webContents)

  // buildAppMenu() still matters on macOS, where the app menu bar always
  // lives at the screen top regardless of window frame (giving Cmd+Q,
  // Cmd+C, etc.). On Windows/Linux a frame:false window has no native
  // chrome to host a menu bar in at all — setMenuBarVisibility(true) does
  // nothing there — so the visible File/Edit/View/Help row on Windows is
  // the custom HTML #menubar in index.html/app.js instead.
  buildAppMenu()

  // Show window gracefully once content is ready
  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    if (process.env.NODE_ENV === 'development') {
      mainWindow.webContents.openDevTools()
    }
  })

  // Attempt to set window transparency for glass effect on Windows 11
  try {
    mainWindow.setBackgroundColor('#060B18')
  } catch (e) {
    // ignore if not supported
  }

  // Persist window state on resize/move
  mainWindow.on('resize', () => saveWindowState(mainWindow))
  mainWindow.on('move', () => saveWindowState(mainWindow))

  // Kill python cleanly when the window is about to close
  mainWindow.on('close', () => {
    saveWindowState(mainWindow)
    killPython()
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// ---------------------------------------------------------------------------
// Second-instance handling
// ---------------------------------------------------------------------------
app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  }
})

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------
app.whenReady().then(() => {
  createWindow()
  spawnPython()
})

app.on('window-all-closed', () => {
  killPython()
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})

// Ensure python is killed if the whole process exits unexpectedly
process.on('exit', () => killPython())
process.on('SIGINT', () => { killPython(); process.exit(0) })
process.on('SIGTERM', () => { killPython(); process.exit(0) })

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------

// Custom titlebar controls
ipcMain.handle('minimize-window', () => {
  if (mainWindow) mainWindow.minimize()
})

ipcMain.handle('maximize-window', () => {
  if (!mainWindow) return
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize()
  } else {
    mainWindow.maximize()
  }
})

ipcMain.handle('close-window', () => {
  if (mainWindow) mainWindow.close()
})

ipcMain.handle('reload-window', () => {
  if (mainWindow) mainWindow.reload()
})

ipcMain.handle('toggle-devtools', () => {
  if (mainWindow) mainWindow.webContents.toggleDevTools()
})

// Python status / control
ipcMain.handle('get-python-status', () => {
  return {
    running: pythonProcess !== null && pythonProcess.exitCode === null,
    pid: pythonProcess ? pythonProcess.pid : null
  }
})

ipcMain.handle('restart-python', () => {
  killPython()
  // Small delay so the old process can fully clean up before we respawn
  setTimeout(() => {
    spawnPython()
  }, 500)
  return { restarting: true }
})

// Fullscreen live scripture window
let fullscreenWindow = null
ipcMain.handle('open-fullscreen', () => {
  if (fullscreenWindow && !fullscreenWindow.isDestroyed()) {
    fullscreenWindow.focus()
    return
  }
  fullscreenWindow = new BrowserWindow({
    width: 1280,
    height: 720,
    backgroundColor: '#060B18',
    icon: path.join(__dirname, 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    title: 'BibleCue — Live Scripture',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    }
  })
  fullscreenWindow.loadFile(path.join(__dirname, 'renderer', 'fullscreen.html'))
  attachContextMenu(fullscreenWindow.webContents)
  fullscreenWindow.setMenuBarVisibility(false)
  fullscreenWindow.on('closed', () => { fullscreenWindow = null })
})

ipcMain.handle('open-external-url', (_, url) => {
  // http://127.0.0.1: for the local browser-listener link, https:// for the
  // Help menu's Setup Guide / GitHub links — both are hardcoded strings in
  // the renderer, never user input, so this isn't opening up arbitrary URLs.
  if (typeof url === 'string' && (url.startsWith('http://127.0.0.1:') || url.startsWith('https://'))) {
    shell.openExternal(url)
  }
})

ipcMain.handle('get-ws-port', () => {
  const candidates = [
    path.join(app.getPath('userData'), 'biblecue_settings.json'),
    path.join(app.getPath('userData'), 'bibleshow_settings.json'),
    path.join(app.getPath('userData'), 'probible_settings.json'),
    path.join(__dirname, '..', 'biblecue_settings.json'),
    path.join(__dirname, '..', 'bibleshow_settings.json'),
    path.join(__dirname, '..', 'probible_settings.json'),
  ]
  for (const p of candidates) {
    try {
      const raw = fs.readFileSync(p, 'utf8')
      const port = JSON.parse(raw).ws_port
      if (port) return port
    } catch {}
  }
  return 8765
})
