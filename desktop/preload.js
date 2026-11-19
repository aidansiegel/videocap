// The station list's only bridge to the app: the list of stations, and three actions. Station pages get nothing.
'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('videocap', {
  onStations: (cb) => ipcRenderer.on('stations', (e, list) => cb(list)),
  open: (url) => ipcRenderer.invoke('open', url),
  add: (addr) => ipcRenderer.invoke('add', addr),
  remove: (url) => ipcRenderer.invoke('remove', url),
});
