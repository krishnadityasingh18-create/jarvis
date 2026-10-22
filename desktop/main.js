const { app, BrowserWindow, Tray, Menu, screen, ipcMain, nativeImage } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

const URL = process.env.JARVIS_ISLAND_URL || 'http://127.0.0.1:8765/island';
const W = 560, H = 660;
let win, tray;

const place = () => {
  if (!win) return;
  const wa = screen.getPrimaryDisplay().workArea;
  win.setBounds({ x: Math.round(wa.x + (wa.width - W) / 2), y: wa.y, width: W, height: H });
};
const top = () => win && win.setAlwaysOnTop(true, 'screen-saver');

// full view = the Edge HUD app window
const fullView = () =>
  spawn('msedge.exe', ['--app=http://127.0.0.1:8765'], { detached: true, stdio: 'ignore' }).unref();

if (!app.requestSingleInstanceLock()) app.quit();

app.whenReady().then(() => {
  win = new BrowserWindow({
    width: W, height: H, x: 0, y: 0,
    transparent: true, frame: false, resizable: false, movable: false,
    hasShadow: false, skipTaskbar: true, show: false,
    focusable: false, type: 'toolbar', alwaysOnTop: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), backgroundThrottling: false },
  });
  top();
  place();
  win.setIgnoreMouseEvents(true, { forward: true });
  win.loadURL(URL);
  win.showInactive(); // transparent, so no flash before the page paints
  win.on('blur', top);
  setInterval(top, 5000);

  for (const e of ['display-metrics-changed', 'display-added', 'display-removed']) screen.on(e, place);

  ipcMain.on('island:interactive', (_, on) => win.setIgnoreMouseEvents(!on, { forward: true }));
  ipcMain.on('island:focusable', (_, on) => { win.setFocusable(on); if (on) win.focus(); });

  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'tray.png')));
  tray.setToolTip('JARVIS');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Full view', click: fullView },
    { label: 'Quit island', click: () => app.exit(0) },
  ]));
});
