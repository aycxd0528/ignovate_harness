const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('ignovateDesktop', {
  writeClipboardText: text => ipcRenderer.invoke('write-clipboard-text', text),
  chooseProject: () => ipcRenderer.invoke('choose-project'),
  openFile: (project, path) => ipcRenderer.invoke('open-project-file', project, path),
});
