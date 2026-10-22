const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('island', {
  interactive: (on) => ipcRenderer.send('island:interactive', !!on),
  onFullView: (cb) => ipcRenderer.on('island:full-view', () => cb()),
  focusable: (on) => ipcRenderer.send('island:focusable', !!on),
});
