const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('island', {
  interactive: (on) => ipcRenderer.send('island:interactive', !!on),
  focusable: (on) => ipcRenderer.send('island:focusable', !!on),
});
