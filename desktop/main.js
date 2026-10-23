const { app, BrowserWindow, Tray, Menu, screen, ipcMain, nativeImage } = require('electron');
const path = require('path');
const { over } = require('./hit');

const URL = process.env.JARVIS_ISLAND_URL || 'http://127.0.0.1:8765/island';
const W = 560, H = 660;
let win, tray, rect = null, pageWants = false, passing = false;

const place = () => {
  if (!win) return;
  const wa = screen.getPrimaryDisplay().workArea;
  win.setBounds({ x: Math.round(wa.x + (wa.width - W) / 2), y: wa.y, width: W, height: H });
};
const top = () => win && win.setAlwaysOnTop(true, 'screen-saver');

// full view: the page sends {"type":"full_view"} so Python's open_full_view de-dupes
const fullView = () => win && win.webContents.send('island:full-view');

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else app.whenReady().then(() => {
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

  const apply = (want) => { if (want !== passing) { passing = want; win.setIgnoreMouseEvents(!want, { forward: true }); } };
  ipcMain.on('island:interactive', (_, on) => { pageWants = on; apply(on || cursorOver()); });
  // an OS drag sends the window no mouse events, so the page can't ask: while the cursor is over
  // the island (its rect comes from the page) take clicks and drops, otherwise pass them through
  const cursorOver = () => over(screen.getCursorScreenPoint(), win.getBounds(), rect);
  ipcMain.on('island:rect', (_, r) => { rect = r && { x: +r.x, y: +r.y, w: +r.w, h: +r.h }; });
  setInterval(() => apply(pageWants || cursorOver()), 50);
  ipcMain.on('island:focusable', (_, on) => { win.setFocusable(on); if (on) win.focus(); });

  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'tray.png')));
  tray.setToolTip('JARVIS');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Full view', click: fullView },
    { label: 'Quit island', click: () => app.exit(0) },
  ]));
});
