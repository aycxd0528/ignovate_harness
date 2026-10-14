const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('ignovateDesktop', {
  chooseProject: () => ipcRenderer.invoke('choose-project'),
  openFile: (project, path) => ipcRenderer.invoke('open-project-file', project, path),
});
