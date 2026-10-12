// The particle core (the sphere from the reference video) and the audio meter.
// anime.js owns time: one createTimer is the frame loop, and every value the
// renderer reads (spin, pulse, colour, bar heights) is tweened by animate().
import { animate, createTimer, utils } from "/ui/anime.esm.min.js";

export const COLORS = { idle: "#5CE1FF", listening: "#D8E06A", thinking: "#FF6B5E", speaking: "#E07BFF",
                        confirming: "#7CF5C4", off: "#7C93A6" };
// per state: spin (rad/s), pulse depth, audio level, thinking jitter
const MOTION = { idle: [.10, 0, .06, 0], listening: [.22, .5, .5, 0], thinking: [.65, .25, .2, 1],
                 speaking: [.16, 1, .85, 0], confirming: [.2, .5, .5, 0], off: [.03, 0, 0, 0] };
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));

const N = 1100, P = [], L = [];
for (let i = 0; i < N; i++) {
  let x, y, z, d;
  do { x = utils.random(-1, 1, 4); y = utils.random(-1, 1, 4); z = utils.random(-1, 1, 4); d = x * x + y * y + z * z; }
  while (d > 1 || d < 1e-4);
  const r = Math.pow(Math.random(), 0.5) / Math.sqrt(d);
  P.push({ x: x * r, y: y * r, z: z * r, s: Math.random() < .1 ? 2.1 : utils.random(.5, 1.6, 2), ph: utils.random(0, 6.28, 2) });
}
for (let i = 0; i < N; i++) {   // ponytail: O(n²) once at load, ~0.6 M distance checks
  let k = 0;
  for (let j = i + 1; j < N && k < 2; j++) {
    const a = P[i], b = P[j], dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    if (dx * dx + dy * dy + dz * dz < .02) { L.push(i, j); k++; }
  }
}

// everything the renderer reads; only anime.js writes it
const m = { spin: .1, amp: 0, lvl: 0, jit: 0, beat: 0, scale: .6, rot: 0, r: 92, g: 225, b: 255 };
const bars = Array.from({ length: 40 }, () => ({ v: 0 }));
let beatAnim = null, onColor = () => {}, muted = false, state = "off";

function fit(c) {
  const r = c.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return [w, h, dpr];
}

function drawSphere(cx, t) {
  const [w, h, dpr] = fit(cx.canvas), R = Math.min(w, h) * .42 * m.scale;
  const pulse = 1 + m.amp * .045 * m.beat, rgb = `${m.r | 0},${m.g | 0},${m.b | 0}`;
  cx.clearRect(0, 0, w, h); cx.globalCompositeOperation = "lighter";
  const g = cx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, R * 1.1);
  g.addColorStop(0, `rgba(${rgb},.3)`); g.addColorStop(1, "rgba(0,0,0,0)");
  cx.fillStyle = g; cx.fillRect(0, 0, w, h);
  const cr = Math.cos(m.rot), sr = Math.sin(m.rot), ct = Math.cos(.35), st = Math.sin(.35), Q = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const p = P[i], j = 1 + m.jit * .05 * Math.sin(t * 6 + p.ph);
    const x = (p.x * cr + p.z * sr) * j, z0 = (-p.x * sr + p.z * cr) * j, y = p.y * j;
    const y2 = y * ct - z0 * st, z = y * st + z0 * ct, f = 2.8 / (2.8 + z);
    Q[i * 3] = w / 2 + x * f * R * pulse; Q[i * 3 + 1] = h / 2 + y2 * f * R * pulse; Q[i * 3 + 2] = z;
  }
  cx.strokeStyle = cx.fillStyle = `rgb(${rgb})`; cx.globalAlpha = .13; cx.lineWidth = dpr; cx.beginPath();
  for (let i = 0; i < L.length; i += 2) { cx.moveTo(Q[L[i] * 3], Q[L[i] * 3 + 1]); cx.lineTo(Q[L[i + 1] * 3], Q[L[i + 1] * 3 + 1]); }
  cx.stroke();
  for (let i = 0; i < N; i++) {
    cx.globalAlpha = Math.max(.15, .9 - (Q[i * 3 + 2] + 1) * .35) * (.75 + .25 * Math.sin(t * 2 + P[i].ph));
    cx.beginPath(); cx.arc(Q[i * 3], Q[i * 3 + 1], P[i].s * dpr, 0, 6.283); cx.fill();
  }
  cx.globalAlpha = 1; cx.globalCompositeOperation = "source-over";
}

function drawBars(ax) {
  const [w, h] = fit(ax.canvas), bw = w / bars.length;
  ax.clearRect(0, 0, w, h); ax.fillStyle = `rgb(${m.r | 0},${m.g | 0},${m.b | 0})`; ax.globalAlpha = .85;
  bars.forEach((b, i) => { const bh = Math.max(2, b.v * m.lvl * h); ax.fillRect(i * bw + 1, (h - bh) / 2, bw - 2, bh); });
}

/** Start the loop. visible(): whether the sphere is on screen; colour(rgb): the accent changed. */
export function start(sphere, audio, { visible, color }) {
  onColor = color;
  const cx = sphere.getContext("2d"), ax = audio.getContext("2d");
  createTimer({ onUpdate: self => {
    const t = self.currentTime / 1000;
    m.rot += m.spin * Math.min(.05, self.deltaTime / 1000);
    if (visible()) drawSphere(cx, t);
    drawBars(ax);
  } });
  // each bar wanders to a new random height, re-rolled every loop
  animate(bars, { v: () => utils.random(.25, 1, 2), duration: () => utils.random(110, 220), ease: "inOutSine",
                  loop: true, onLoop: a => a.refresh() });
  animate(m, { scale: 1, duration: reduced ? 0 : 1600, ease: "outElastic(1, .6)" });   // boot
}

export function setState(s) {
  state = s;
  const [spin, amp, level, jit] = MOTION[s] || MOTION.idle, lvl = muted && s === "idle" ? 0 : level, [r, g, b] = hex(COLORS[s] || COLORS.idle);
  animate(m, { spin: reduced ? spin * .2 : spin, amp: reduced ? 0 : amp, lvl, jit: reduced ? 0 : jit, r, g, b,
               duration: 900, ease: "outQuad", onUpdate: () => onColor(`rgb(${m.r | 0}, ${m.g | 0}, ${m.b | 0})`) });
  beatAnim?.pause();
  beatAnim = amp && !reduced
    ? animate(m, { beat: [-1, 1], duration: s === "speaking" ? 180 : 700, ease: "inOutSine", loop: true, alternate: true })
    : animate(m, { beat: 0, duration: 300 });
}

export function setMuted(on) { muted = on; if (state === "idle") animate(m, { lvl: on ? 0 : MOTION.idle[2], duration: 400 }); }
