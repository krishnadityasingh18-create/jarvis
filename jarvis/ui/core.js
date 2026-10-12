// The core: a perfectly round dot-lattice shell (the user's reference orb) that only
// shimmers while speaking; and the audio meter. anime.js owns time: one createTimer is the frame loop,
// and every value the renderer reads (spin, pulse, colour, shimmer, bars) is
// tweened by animate().
import { animate, createTimer, utils } from "/ui/anime.esm.min.js";

export const COLORS = { idle: "#CFE3FF", listening: "#D8E06A", thinking: "#FF6B5E", speaking: "#E07BFF",
                        confirming: "#7CF5C4", off: "#7C93A6" };
// per state: spin (rad/s), pulse depth, audio level
const MOTION = { idle: [.10, 0, .06], listening: [.22, .5, .5], thinking: [.95, 0, .2],
                 speaking: [.12, 0, .85], confirming: [.2, .5, .5], off: [.03, 0, 0] };
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));

// an even dot lattice over the unit sphere (Fibonacci spiral): reads as the fine grid
const N = 24000, P = new Float32Array(N * 3), GA = Math.PI * (3 - Math.sqrt(5));
for (let i = 0; i < N; i++) {
  const y = 1 - 2 * (i + .5) / N, r = Math.sqrt(1 - y * y), a = i * GA;
  P[i * 3] = Math.cos(a) * r; P[i * 3 + 1] = y; P[i * 3 + 2] = Math.sin(a) * r;
}

// everything the renderer reads; only anime.js (and the clock) writes it
const m = { spin: .1, amp: 0, lvl: 0, beat: 0, scale: .6, rot: 0, r: 207, g: 227, b: 255,
            rip: 0, shimT: 0 };   // speaking: shimmer strength, and its clock
const bars = Array.from({ length: 40 }, () => ({ v: 0 }));
// speaking: many small wavelets running over the surface; they change brightness only,
// never the radius. Object-space, so they ride the sphere's spin.
const shimmer = (x, y, z, t) => Math.max(0, Math.sin(9 * x + 7 * y - 3.2 * t) * Math.sin(8 * z - 6 * x + 2.7 * t)
  + .6 * Math.sin(13 * y + 11 * z - 4.1 * t));
let beatAnim = null, onColor = () => {}, muted = false, state = "off";

function fit(c) {
  const r = c.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return [w, h, dpr];
}

// The dots are splatted into a light buffer (adding up where they overlap), then tone-mapped:
// dim → the state colour, bright → white. Overlap is what lights the rim.
// ponytail: CPU pixel buffer, ~3 ms a frame at 24k dots; WebGL points if it ever needs more
let acc = null, img = null, off = null;
function drawSphere(cx) {
  const [w, h, dpr] = fit(cx.canvas), R = Math.min(w, h) * .40 * m.scale * (1 + m.amp * .04 * m.beat);
  const ox = w / 2, oy = h / 2, col = [m.r, m.g, m.b];
  cx.clearRect(0, 0, w, h);
  // the soft tinted glow inside the shell
  const g = cx.createRadialGradient(ox, oy, 0, ox, oy, R * 1.05);
  g.addColorStop(0, `rgba(${col.map(v => v | 0)},.34)`); g.addColorStop(.75, `rgba(${col.map(v => v | 0)},.14)`);
  g.addColorStop(1, "rgba(0,0,0,0)");
  cx.fillStyle = g; cx.fillRect(0, 0, w, h);

  // the orb's box: everything is drawn inside it
  const half = Math.ceil(R * 1.45), bx = Math.max(0, (ox - half) | 0), by = Math.max(0, (oy - half) | 0);
  const bw = Math.min(w - bx, half * 2), bh = Math.min(h - by, half * 2);
  if (bw <= 0 || bh <= 0) return;
  if (!img || img.width !== bw || img.height !== bh) {
    off = document.createElement("canvas"); off.width = bw; off.height = bh;
    img = off.getContext("2d").createImageData(bw, bh); acc = new Float32Array(bw * bh);
  } else acc.fill(0);

  const cr = Math.cos(m.rot), sr = Math.sin(m.rot), ct = Math.cos(.35), st = Math.sin(.35);
  const cx0 = ox - bx, cy0 = oy - by, dot = 1.25 * dpr;
  for (let i = 0; i < N; i++) {
    const x0 = P[i * 3], y0 = P[i * 3 + 1], z0 = P[i * 3 + 2];
    const x = x0 * cr + z0 * sr, z1 = -x0 * sr + z0 * cr, y = y0 * ct - z1 * st, z = y0 * st + z1 * ct;
    const k = R, b = m.rip && m.rip * shimmer(x0, y0, z0, m.shimT);
    const a = (z > 0 ? .3 : .14) + b * .5;   // front half brighter
    const sz = Math.max(1, Math.round(dot)), px = (cx0 + x * k) | 0, py = (cy0 + y * k) | 0;
    for (let v = 0; v < sz; v++) {
      const row = (py + v) * bw;
      if (py + v < 0 || py + v >= bh) continue;
      for (let u = 0; u < sz; u++) if (px + u >= 0 && px + u < bw) acc[row + px + u] += a;
    }
  }
  // tone map: light 0..∞ → 0..1; colour runs from the (white-washed) state colour to white
  const d = img.data, tint = col.map(v => v + (255 - v) * .35);
  for (let i = 0, n = bw * bh; i < n; i++) {
    const l = acc[i];
    if (l === 0) { d[i * 4 + 3] = 0; continue; }
    const v = 1 - Math.exp(-l * 2.4), wv = v * v;
    d[i * 4] = tint[0] + (255 - tint[0]) * wv; d[i * 4 + 1] = tint[1] + (255 - tint[1]) * wv;
    d[i * 4 + 2] = tint[2] + (255 - tint[2]) * wv; d[i * 4 + 3] = v * 255;
  }
  off.getContext("2d").putImageData(img, 0, 0);
  cx.globalCompositeOperation = "lighter"; cx.drawImage(off, bx, by); cx.globalCompositeOperation = "source-over";
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
    const dt = Math.min(.05, self.deltaTime / 1000);
    m.rot += m.spin * dt; m.shimT += dt;   // integrated, so speed changes never jump
    if (visible()) drawSphere(cx);
    drawBars(ax);
  } });
  // each bar wanders to a new random height, re-rolled every loop
  animate(bars, { v: () => utils.random(.25, 1, 2), duration: () => utils.random(110, 220), ease: "inOutSine",
                  loop: true, onLoop: a => a.refresh() });
  animate(m, { scale: 1, duration: reduced ? 0 : 1600, ease: "outElastic(1, .6)" });   // boot
}

export function setState(s) {
  state = s;
  const [spin, amp, level] = MOTION[s] || MOTION.idle, lvl = muted && s === "idle" ? 0 : level, [r, g, b] = hex(COLORS[s] || COLORS.idle);
  animate(m, { spin: reduced ? spin * .2 : spin, amp: reduced ? 0 : amp, lvl, r, g, b,
               duration: 1400, ease: "inOutSine", onUpdate: () => onColor(`rgb(${m.r | 0}, ${m.g | 0}, ${m.b | 0})`) });
  beatAnim?.pause();
  beatAnim = amp && !reduced
    ? animate(m, { beat: [-1, 1], duration: s === "speaking" ? 180 : 700, ease: "inOutSine", loop: true, alternate: true })
    : animate(m, { beat: 0, duration: 300 });
  const speaking = s === "speaking" && !reduced;
  animate(m, { rip: speaking ? 1 : 0, duration: 900, ease: "inOutSine" });
}

export function setMuted(on) { muted = on; if (state === "idle") animate(m, { lvl: on ? 0 : MOTION.idle[2], duration: 400 }); }
