// The island: JARVIS as a floating pill at the top of the screen, speaking the /ws protocol.
// Motion: Apple-style springs (stiffness=(2π/d)², damping=(1−bounce)·4π/d, mass 1), all
// interruptible — a new target continues from the current position and velocity. Only #shell
// changes size; the orb, bubble and notification move by transform.
import * as core from "/ui/core.js";

const $ = q => document.querySelector(q);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
// reduced motion: a stiff, critically damped spring (~150 ms, no overshoot) instead
const P = (d, bounce) => reduced ? { k: 900, c: 60 } : { k: (2 * Math.PI / d) ** 2, c: (1 - bounce) * 4 * Math.PI / d };
export const EXPAND = P(.5, .15), COLLAPSE = P(.42, 0), MICRO = P(.3, .1), DROP = P(.55, .3);
class S {
  constructor(x) { this.x = this.t = x; this.v = 0; this.p = MICRO; }
  to(t, p = this.p) { this.t = t; this.p = p; }
  step(dt) { const a = -this.p.k * (this.x - this.t) - this.p.c * this.v; this.v += a * dt; this.x += this.v * dt; }
  get done() { return Math.abs(this.x - this.t) < .05 && Math.abs(this.v) < .05; }
}
export const sp = { w: new S(120), h: new S(38), r: new S(19), ox: new S(4), oy: new S(4), os: new S(28),
  bx: new S(0), ty: new S(0), ny: new S(10), nw: new S(120), nh: new S(38), seg: new S(0), lvl: new S(0) };
sp.lvl.p = P(.16, .15);

const shell = $("#shell"), orbEl = $("#orb"), mid = $("#mid"), bars = [...document.querySelectorAll("#eq i")];
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const later = (ms, f) => setTimeout(f, ms);
const bridge = window.island;   // the Electron window (absent in a plain browser)

// ---------- state shared with the panel
export const ui = { mode: "idle", state: "off", lastReply: "", hooks: { mode: [], state: [] } };
let capW = 0, holdUntil = 0, capTok = 0, hovering = false;

// ---------- geometry
let extraH = { confirm: 122 };
export function geom(m, preset) {
  const fit = Math.max(120, Math.min(460, 98 + capW + (ui.state === "listening" ? 34 : 0)));   // width follows the content
  const g = { idle: [fit, 38, 19], peek: [Math.max(320, fit), 64, 26], caption: [fit, 38, 19],
              confirm: [400, extraH.confirm, 30], open: [440, 600, 32] }[m];
  sp.w.to(g[0], preset); sp.h.to(g[1], preset); sp.r.to(g[2], preset);
  const big = m === "open", card = m === "confirm";
  sp.ox.to(big ? 18 : card ? 14 : 4, preset); sp.oy.to(big ? 18 : card ? 14 : 4, preset); sp.os.to(big ? 44 : card ? 32 : 28, preset);
  return g[0];
}
export function setMode(m) {
  const opening = m === "open" || m === "confirm", was = ui.mode, wasBig = was === "open" || was === "confirm";
  ui.mode = m;
  document.body.classList.toggle("isopen", m === "open");
  shell.classList.toggle("open", m === "open");
  $("#collapsed").style.opacity = opening ? 0 : 1;
  $("#peek").style.opacity = m === "peek" ? 1 : 0;
  $("#confirm").classList.toggle("on", m === "confirm");
  geom(m, opening ? EXPAND : wasBig ? COLLAPSE : MICRO);
  if (m === "open" || m === "confirm") interactive(true);
  else if (wasBig) interactive(hovering);
  ui.hooks.mode.forEach(f => f(m, was));
  if (!opening) { nextNote(); if (wasBig) ticker(true); }
  bubble(); tuck();
}
function interactive(on) { bridge?.interactive?.(on); }

// ---------- frame loop: springs -> geometry/transforms (writes only while something moves)
const box = (e, x, y, w, h, r) => Object.assign(e.style, { left: x + "px", top: y + "px", width: w + "px", height: h + "px", borderRadius: r + "px" });
let last = performance.now(), wasMoving = true, noteOn = false;
const springs = Object.values(sp);
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(.05, (now - last) / 1000); last = now;
  const moving = springs.some(s => !s.done);
  if (moving) { const n = Math.ceil(dt / (1 / 240)); for (let i = 0; i < n; i++) for (const s of springs) s.step(dt / n); }
  else for (const s of springs) { s.x = s.t; s.v = 0; }
  if (ui.state === "listening") meter(now / 1000);
  if (moving || wasMoving) draw();
  wasMoving = moving;
}
let lastRect = "";
function reportRect(x, y, w, h) {   // the shell's on-screen rect, so Electron can let OS drags reach it
  const k = [x, y, w, h].join();
  if (k !== lastRect) { lastRect = k; bridge?.rect?.({ x, y, w, h }); }
}
function draw() {
  const w = Math.round(sp.w.x), h = Math.round(sp.h.x), r = Math.max(0, sp.r.x);
  const L = Math.round((innerWidth - w) / 2), T = Math.round(10 + sp.ty.x);
  box(shell, L, T, w, h, r); box($("#gPill"), L, T, w, h, r);
  reportRect(L, T, w, h);
  box($("#glow"), L - 3, T - 3, w + 6, h + 6, r + 3);
  box($("#rim"), L - 1.5, T - 1.5, w + 3, h + 3, r + 1.5);
  box($("#comet"), L - 2.5, T - 2.5, w + 5, h + 5, r + 2.5);
  box($("#cometGlow"), L - 4, T - 4, w + 8, h + 8, r + 4);
  // the orb canvas has bloom room around the ring: 0.225 of its size on each side
  const os = sp.os.x, k = os / 64 * 1.45;
  orbEl.style.transform = `translate(${sp.ox.x - os * .225}px,${sp.oy.x - os * .225}px) scale(${k})`;
  // split bubble: hides under the island's right edge, then pulls out
  const bx = L + w - 19 + sp.bx.x;
  box($("#gBub"), bx, T, 38, 38, 19); $("#gBub").style.opacity = sp.bx.x > 1 && ui.mode !== "open" ? 1 : 0;
  $("#bub").style.transform = `translate(${bx}px,${T}px)`; $("#bub").style.opacity = sp.bx.x > 40 ? 1 : 0;
  // notification drop
  const nw = Math.round(sp.nw.x), nh = Math.round(sp.nh.x);
  box($("#gNote"), Math.round((innerWidth - nw) / 2), Math.round(sp.ny.x), nw, nh, Math.min(nh / 2, 24));
  $("#note").style.transform = `translate(${Math.round((innerWidth - 330) / 2)}px,${Math.round(sp.ny.x)}px)`;
  // the gooey layer costs a filter pass per frame: only while something splits off
  $("#goo").style.display = sp.bx.x > .5 || noteOn || sp.ny.x > 10.5 ? "block" : "none";
  ui.hooks.draw?.();
}
addEventListener("resize", () => { wasMoving = true; });

// ---------- the listening meter (7 bars; the orb itself never pulses)
function meter(t) {
  bars.forEach((b, i) => b.style.transform = `scaleY(${.18 + .82 * Math.min(1, (.35 + sp.lvl.x) * Math.abs(Math.sin(t * 7 + i * 1.25)))})`);
  document.querySelectorAll(".mic.hot").forEach(m => m.style.setProperty("--l", 1 + sp.lvl.x * .35));
  if (sp.lvl.done) { sp.lvl.x = sp.lvl.t; sp.lvl.v = 0; } else for (let i = 0; i < 4; i++) sp.lvl.step(1 / 240);
}
// lvl only steps here (not in the geometry loop), so a level never wakes the layout writes
springs.splice(springs.indexOf(sp.lvl), 1);

// ---------- the middle of the island: content in one span, measured so the island fits it
function fill(html) {
  mid.classList.remove("sc");
  mid.innerHTML = `<span>${html}</span>`;
  return mid.firstChild;
}
function fitMid(inner, extra) {
  capW = inner.offsetWidth + extra;
  if (ui.mode === "idle" || ui.mode === "caption" || ui.mode === "peek") {
    const w = geom(ui.mode, MICRO), room = w - 44 - (ui.state === "listening" ? 78 : 44);
    const over = Math.max(0, inner.offsetWidth - room);   // keep the newest words in view
    inner.style.transform = over ? `translateX(${-over - 8}px)` : "";
    mid.classList.toggle("sc", over > 0);
  }
}
export function midLabel(html, extra = 6) { capTok++; fitMid(fill(html), extra); }
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// captions: words blur in one by one and the island's width follows them
function caption(text, ms) {
  const words = String(text).split(/\s+/).filter(Boolean), tok = ++capTok, inner = fill("");
  let i = 0;
  return new Promise(res => {
    const tick = () => {
      if (tok !== capTok) return res(false);
      if (i >= words.length) return res(true);
      inner.append(el("span", "w", words[i++]));
      fitMid(inner, 6);
      if (ui.mode !== "open" && ui.mode !== "confirm" && ui.mode !== "caption") setMode("caption");
      later(reduced ? 0 : ms, tick);
    };
    tick();
  });
}

// idle: a quiet ticker of what's next, so the island is never empty
export const ICON = {
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2"/></svg>',
  cal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h11"/></svg>',
  mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 7 9 6 9-6"/></svg>',
  bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>',
  whatsapp: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2z"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
};
export let glance = [];
let ti = 0;
const soon = () => glance.find(g => g.icon === "bell" && g.at && (left(g) > 0 && left(g) <= 600));
const left = g => Math.round((Date.parse(g.at) - Date.now()) / 1000);
const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
function ticker(now) {
  if (ui.state !== "idle" || !(ui.mode === "idle" || ui.mode === "peek") || performance.now() < holdUntil) return;
  if (!now && !reduced) ti++;
  const due = soon();   // a reminder in the next 10 minutes stays put, counting down
  const g = due || glance[ti % glance.length];
  if (!g) return midLabel("");   // nothing to show yet: just the orb and the mic
  const d = due ? "in " + mmss(left(due)) : g.detail;
  midLabel(`<span class="tk">${ICON[g.icon] || ICON.check}${esc(g.value)} <span class="d">${esc(d || "")}</span></span>`, 26);
}
setInterval(ticker, 4500);

// ---------- state: colour, motion and words
const LOOK = { listening: "hue-rotate(-40deg) saturate(1.35) brightness(1.15)", thinking: "hue-rotate(70deg) saturate(1.5)" };
const bornAt = performance.now();
function offlineLabel() {
  midLabel(`<span class="lbl">${performance.now() - bornAt < 20000 ? "Starting…" : "Offline · click to retry"}</span>`);
}
export function setState(s) {
  if (s === "confirming") s = "listening";   // listening for yes/no looks like listening
  if (s === ui.state) return;
  ui.state = s;
  document.body.className = document.body.className.replace(/\s*st-\w+/g, "") + " st-" + s;
  core.setState(s); core.setFilter(LOOK[s] || "");
  if (s !== "listening") sp.lvl.to(0);
  if (s === "listening") midLabel('<span class="lbl">Listening</span>');
  else if (s === "thinking") { if (performance.now() >= holdUntil) thinking(); }
  else if (s === "speaking") midLabel('<span class="lbl">Speaking</span>');
  else if (s === "off") offlineLabel();
  else { holdUntil = 0; if (ui.mode === "caption") setMode("idle"); ticker(true); }
  ui.hooks.state.forEach(f => f(s));
  bubble(); tuck();
}
const thinking = () => midLabel('<span class="shim">Thinking</span>');
later(20500, () => ui.state === "off" && offlineLabel());

// ---------- confirm card (also used for "Keep in vault?")
let ask = null, beforeAsk = "idle";
export function askCard({ kind, msg, ok, wa = false, onAnswer }) {
  const card = $("#confirm"), go = card.querySelector(".go");
  $("#ck").textContent = kind; $("#cmsg").replaceChildren(...msg);
  go.textContent = ok; go.classList.toggle("wa", wa);
  extraH.confirm = Math.max(122, card.offsetHeight);   // laid out at its final width, so this is real
  if (ui.mode !== "confirm") beforeAsk = ui.mode === "open" ? "open" : "idle";
  ask = onAnswer;
  setMode("confirm");
}
function endAsk(ok) {
  const f = ask; ask = null;
  if (ui.mode === "confirm") setMode(beforeAsk);
  f?.(ok);
}
$("#confirm").addEventListener("click", e => {
  const b = e.target.closest("[data-act]"); e.stopPropagation();
  if (b) endAsk(b.dataset.act === "ok");
});
let confirmId = null;
export const confirming = () => confirmId !== null;   // a server question is up: drops must not replace it
function confirmQuestion(m) {
  confirmId = m.id;
  const text = String(m.text ?? ""), hit = /^send whatsapp to (.+?): ['"“](.*)['"”](?:,\s*sir\?)?$/is.exec(text.trim()), wa = !!hit;
  const msg = hit ? ["To ", el("b", null, hit[1]), ": ", el("i", null, `“${hit[2]}”`)] : [text];
  askCard({ kind: wa ? "WhatsApp · from your number" : "JARVIS asks", msg, ok: wa ? "Send" : "Allow", wa,
            onAnswer: ok => send({ type: "confirm_reply", id: m.id, ok }) });
}

// ---------- live activities: a bubble splits off the island with a progress ring
const ACTIVITY = { ask_claude: "claude", send_whatsapp: "wa" };
let activity = null;
function bubble() {
  const due = soon(), b = $("#bub");
  const kind = activity ? ACTIVITY[activity] : due ? "bell" : null;
  b.className = kind ? kind + (activity ? " spin" : "") : b.className;
  if (due && !activity) $("#arc").style.strokeDashoffset = 88 * (1 - left(due) / 600);
  else $("#arc").style.strokeDashoffset = "";
  const out = !!kind && ui.mode !== "open" && ui.mode !== "confirm";
  if ((sp.bx.t > 0) !== out) sp.bx.to(out ? 50 : 0, out ? DROP : COLLAPSE);
}
setInterval(() => { bubble(); if (soon() && ui.state === "idle" && ui.mode === "idle") ticker(true); }, 1000);

// ---------- notifications: a card drops out of the island, stays ~3.5 s, is pulled back in
const notes = [];
export function notify(n) {
  notes.push(n);
  if (n.icon === "reminder") wake(4500);
  nextNote();
}
function nextNote() {
  if (noteOn || !notes.length || ui.mode === "open" || ui.mode === "confirm") return;
  const n = notes.shift(), card = $("#note");
  noteOn = true; tuck();
  card.className = n.icon || "";
  card.querySelector(".ic").innerHTML = ICON[n.icon === "reminder" ? "bell" : n.icon] || ICON.info;
  card.querySelector(".n").textContent = n.title; card.querySelector(".k").textContent = n.text;
  card.title = n.text;
  sp.nw.to(330, DROP); sp.nh.to(60, DROP); sp.ny.to(60, DROP);
  later(420, () => card.style.opacity = 1);
  later(3600, () => card.style.opacity = 0);
  later(3750, () => { sp.nw.to(120, COLLAPSE); sp.nh.to(38, COLLAPSE); sp.ny.to(10, COLLAPSE); });
  later(4500, () => { noteOn = false; tuck(); nextNote(); });
}
function banner(text) {
  const rem = /^reminder/i.test(text);
  notify({ icon: rem ? "reminder" : "info", title: rem ? "Reminder" : "JARVIS", text: rem ? text.replace(/^reminder[^:]*:\s*/i, "") : text });
}

// ---------- fullscreen apps: the island tucks up out of sight until something needs you
let fullscreen = false, awakeUntil = 0;
function wake(ms) { awakeUntil = Date.now() + ms; tuck(); later(ms + 50, tuck); }
function tuck() {
  const busy = (ui.state !== "idle" && ui.state !== "off") || ui.mode !== "idle" || noteOn || Date.now() < awakeUntil;
  const hide = fullscreen && !busy;
  if ((sp.ty.t < 0) !== hide) sp.ty.to(hide ? -80 : 0, hide ? COLLAPSE : DROP);
}

// ---------- socket: reconnects with backoff; the server's on-connect messages restore everything
let ws = null, backoff = 500, retry = null;
export const send = m => ws?.readyState === 1 && (ws.send(JSON.stringify(m)), true);
export const on = {
  state(m) { setState(m.state); },
  history(m) {
    const a = m.messages.findLast(h => h.role === "assistant" && h.content);
    if (a) ui.lastReply = a.content;
  },
  user_text(m) {
    // your words blur in, then "Thinking" returns while the answer is worked out
    const n = String(m.text).split(/\s+/).length, ms = 70;
    holdUntil = performance.now() + n * ms + 900;
    caption(m.text, ms);
    later(n * ms + 950, () => { if (ui.state === "thinking" && performance.now() >= holdUntil) thinking(); });
  },
  spoken_sentence(m) {
    holdUntil = 0;
    caption(m.text, 170);
    ui.lastReply = ui.speaking ? ui.lastReply + " " + m.text : m.text; ui.speaking = true;
  },
  turn_end() { ui.speaking = false; activity = null; bubble(); },
  confirm: confirmQuestion,
  confirm_done(m) {
    if (m.id !== confirmId) return;
    confirmId = null;
    if (ask) { ask = null; if (ui.mode === "confirm") setMode(beforeAsk); }
  },
  banner(m) { banner(m.text); },
  notify(m) { notify(m); },
  mic(m) {
    document.querySelectorAll(".mic").forEach(b => {
      b.classList.toggle("off", !m.available || m.muted);
      b.title = !m.available ? "Microphone off (voice is loading or unavailable)" : m.muted ? "Microphone muted" : "Hold to talk";
    });
  },
  mic_level(m) { if (ui.state === "listening") sp.lvl.to(Math.max(0, Math.min(1, +m.level || 0))); },   // late levels are ignored
  glance(m) { glance = m.items || []; ticker(true); bubble(); },
  fullscreen(m) { fullscreen = !!m.on; tuck(); },
  tool_start(m) { if (ACTIVITY[m.name]) { activity = m.name; bubble(); } },
  action(m) {
    if (m.tool === activity) { activity = null; bubble(); }
    if (m.tool === "send_whatsapp" && m.status === "done" && ui.mode !== "open") {   // only once the helper said so
      holdUntil = performance.now() + 1600;
      midLabel('<span class="ok">✓ Sent</span>');
      if (ui.mode === "idle") setMode("caption");
      later(1650, () => { if (ui.mode === "caption" && ui.state === "idle") setMode("idle"); ticker(true); });
    }
  },
};
function connect() {
  clearTimeout(retry);
  const s = ws = new WebSocket(`ws://${location.host}/ws`);
  s.onopen = () => { backoff = 500; setState("idle"); };
  s.onmessage = e => {
    const m = JSON.parse(e.data);
    try { on[m.type]?.(m); } catch (err) { console.warn("island: couldn't show", m.type, err); }
  };
  s.onclose = () => {
    if (s !== ws) return;
    setState("off"); confirmId = null; if (ui.mode === "confirm") endAsk(false);
    retry = later(backoff, connect); backoff = Math.min(8000, backoff * 2);
  };
}

// ---------- pointer: hover peeks, press squishes, the mic is push-to-talk
let peekT;
shell.addEventListener("pointerenter", () => {
  hovering = true; interactive(true);
  if (ui.mode === "idle" && ui.lastReply) peekT = later(120, () => {
    if (ui.mode !== "idle" || !hovering) return;
    $("#peek").replaceChildren("Last · ", el("b", null, ui.lastReply.split("\n")[0]));
    setMode("peek");
  });
});
shell.addEventListener("pointerleave", () => {
  hovering = false; clearTimeout(peekT);
  if (ui.mode === "peek") setMode("idle");
  if (ui.mode !== "open" && ui.mode !== "confirm") interactive(false);
});
shell.addEventListener("pointerdown", () => { if (ui.mode === "idle" || ui.mode === "peek" || ui.mode === "caption") { sp.w.v -= 60; sp.h.v -= 25; } });
shell.addEventListener("click", () => {
  if (ui.state === "off") { backoff = 500; connect(); return; }
  ui.hooks.click?.();
});
export function micButton(b) {
  const up = e => {
    if (!b.classList.contains("hot")) return;
    b.classList.remove("hot"); b.style.removeProperty("--l");
    send({ type: "ptt", down: false });
  };
  b.addEventListener("pointerdown", e => {
    e.stopPropagation(); b.setPointerCapture?.(e.pointerId);
    b.classList.add("hot"); send({ type: "ptt", down: true });
  });
  b.addEventListener("pointerup", up); b.addEventListener("pointercancel", up); b.addEventListener("lostpointercapture", up);
  b.addEventListener("click", e => e.stopPropagation());
}
micButton($("#cmic"));
addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (ui.mode === "confirm") endAsk(false);
  else if (ui.mode === "open") setMode("idle");
});

// ---------- boot
core.start($("#orb canvas"), { visible: () => !document.hidden && sp.ty.x > -60, color: () => {} });
core.setState("off");
offlineLabel();
setMode("idle");
bridge?.onFullView?.(() => send({ type: "full_view" }));   // the tray's "Full view" (always via the server)
requestAnimationFrame(t => { last = t; frame(t); });
connect();
