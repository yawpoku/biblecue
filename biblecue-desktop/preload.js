const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.invoke('minimize-window'),
  maximize: () => ipcRenderer.invoke('maximize-window'),
  close: () => ipcRenderer.invoke('close-window'),
  getPythonStatus: () => ipcRenderer.invoke('get-python-status'),
  restartPython: () => ipcRenderer.invoke('restart-python'),
  onPythonLog: (cb) => ipcRenderer.on('python-log', (_, line) => cb(line)),
  onPythonCrashed: (cb) => ipcRenderer.on('python-crashed', cb),
  platform: process.platform,
  openFullscreen: () => ipcRenderer.invoke('open-fullscreen'),
  openExternalUrl: (url) => ipcRenderer.invoke('open-external-url', url),
  getWsPort: () => ipcRenderer.invoke('get-ws-port'),
  reloadWindow: () => ipcRenderer.invoke('reload-window'),
  toggleDevTools: () => ipcRenderer.invoke('toggle-devtools'),
})
