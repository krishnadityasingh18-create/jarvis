// The core: a perfectly round dot-lattice shell (the user's reference orb) that only
// shimmers while speaking. anime.js owns time: one createTimer is the frame loop, and
// every value the renderer reads (spin, pulse, colour, shimmer) is tweened by animate().
import { animate, createTimer } from "/ui/anime.esm.min.js";

// state colours for each appearance; dark is the user's white-blue idle
const PALETTE = {
  dark:  { idle: "#CFE3FF", listening: "#D8E06A", thinking: "#FF6B5E", speaking: "#E07BFF", confirming: "#7CF5C4", off: "#7C93A6" },
  light: { idle: "#3D6FD9", listening: "#B07D00", thinking: "#E5484D", speaking: "#9B4FD6", confirming: "#2E9E6A", off: "#8E8E93" },
};
// per state: spin (rad/s), pulse depth
const MOTION = { idle: [.10, 0], listening: [.22, .5], thinking: [.95, 0], speaking: [.12, 0], confirming: [.2, .5], off: [.03, 0] };
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const darkMode = matchMedia("(prefers-color-scheme: dark)");
const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const colorOf = s => { const p = PALETTE[darkMode.matches ? "dark" : "light"]; return p[s] || p.idle; };

// an even dot lattice over the unit sphere (Fibonacci spiral): reads as the fine grid
const N = 24000, P = new Float32Array(N * 3), GA = Math.PI * (3 - Math.sqrt(5));
for (let i = 0; i < N; i++) {
  const y = 1 - 2 * (i + .5) / N, r = Math.sqrt(1 - y * y), a = i * GA;
  P[i * 3] = Math.cos(a) * r; P[i * 3 + 1] = y; P[i * 3 + 2] = Math.sin(a) * r;
}

// everything the renderer reads; only anime.js (and the clock) writes it
const [r0, g0, b0] = hex(colorOf("off"));
const m = { spin: .1, amp: 0, beat: 0, scale: .6, rot: 0, r: r0, g: g0, b: b0,
            rip: 0, shimT: 0 };   // speaking: shimmer strength, and its clock
// speaking: many small wavelets running over the surface; they change brightness only,
// never the radius. Object-space, so they ride the sphere's spin.
const shimmer = (x, y, z, t) => Math.max(0, Math.sin(9 * x + 7 * y - 3.2 * t) * Math.sin(8 * z - 6 * x + 2.7 * t)
  + .6 * Math.sin(13 * y + 11 * z - 4.1 * t));
let beatAnim = null, onColor = () => {}, state = "off";

function fit(c) {
  const r = c.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return [w, h, dpr];
}

// The dots are splatted into a light buffer (adding up where they overlap), then tone-mapped.
// Dark: dim → state colour, bright → white, added onto the black. Light: dim → state colour,
// dense → a deeper shade of it, painted over the pale background. Overlap lights the rim.
// ponytail: CPU pixel buffer, ~3 ms a frame at 24k dots; WebGL points if it ever needs more
let acc = null, img = null, off = null;
function drawSphere(cx) {
  const [w, h, dpr] = fit(cx.canvas), R = Math.min(w, h) * .44 * m.scale * (1 + m.amp * .04 * m.beat);
  const ox = w / 2, oy = h / 2, col = [m.r, m.g, m.b], dark = darkMode.matches;
  cx.clearRect(0, 0, w, h);
  // the soft tinted glow inside the shell
  const g = cx.createRadialGradient(ox, oy, 0, ox, oy, R * 1.05), c = col.map(v => v | 0);
  g.addColorStop(0, `rgba(${c},${dark ? .34 : .16})`); g.addColorStop(.75, `rgba(${c},${dark ? .14 : .07})`);
  g.addColorStop(1, `rgba(${c},0)`);
  cx.fillStyle = g; cx.fillRect(0, 0, w, h);

  // the orb's box: everything is drawn inside it
  const half = Math.ceil(R * 1.1), bx = Math.max(0, (ox - half) | 0), by = Math.max(0, (oy - half) | 0);
  const bw = Math.min(w - bx, half * 2), bh = Math.min(h - by, half * 2);
  if (bw <= 0 || bh <= 0) return;
  if (!img || img.width !== bw || img.height !== bh) {
    off = document.createElement("canvas"); off.width = bw; off.height = bh;
    img = off.getContext("2d").createImageData(bw, bh); acc = new Float32Array(bw * bh);
  } else acc.fill(0);

  const cr = Math.cos(m.rot), sr = Math.sin(m.rot), ct = Math.cos(.35), st = Math.sin(.35);
  const cx0 = ox - bx, cy0 = oy - by, sz = Math.max(1, Math.round(1.25 * dpr));
  for (let i = 0; i < N; i++) {
    const x0 = P[i * 3], y0 = P[i * 3 + 1], z0 = P[i * 3 + 2];
    const x = x0 * cr + z0 * sr, z1 = -x0 * sr + z0 * cr, y = y0 * ct - z1 * st, z = y0 * st + z1 * ct;
    const b = m.rip && m.rip * shimmer(x0, y0, z0, m.shimT);
    const a = (z > 0 ? .3 : .14) + b * 1.1;   // front half brighter
    const px = (cx0 + x * R) | 0, py = (cy0 + y * R) | 0;
    for (let v = 0; v < sz; v++) {
      if (py + v < 0 || py + v >= bh) continue;
      const row = (py + v) * bw;
      for (let u = 0; u < sz; u++) if (px + u >= 0 && px + u < bw) acc[row + px + u] += a;
    }
  }
  const d = img.data, lo = dark ? col.map(v => v + (255 - v) * .35) : col, hi = dark ? [255, 255, 255] : col.map(v => v * .55);
  for (let i = 0, n = bw * bh; i < n; i++) {
    const l = acc[i];
    if (l === 0) { d[i * 4 + 3] = 0; continue; }
    const v = 1 - Math.exp(-l * 2.4), wv = v * v;
    d[i * 4] = lo[0] + (hi[0] - lo[0]) * wv; d[i * 4 + 1] = lo[1] + (hi[1] - lo[1]) * wv;
    d[i * 4 + 2] = lo[2] + (hi[2] - lo[2]) * wv; d[i * 4 + 3] = v * 255;
  }
  off.getContext("2d").putImageData(img, 0, 0);
  cx.globalCompositeOperation = dark ? "lighter" : "source-over";
  cx.drawImage(off, bx, by); cx.globalCompositeOperation = "source-over";
}

/** Start the loop. visible(): whether the orb is on screen; color(rgb): the accent changed. */
export function start(sphere, { visible, color }) {
  onColor = color;
  const cx = sphere.getContext("2d");
  createTimer({ onUpdate: self => {
    const dt = Math.min(.05, self.deltaTime / 1000);
    m.rot += m.spin * dt; m.shimT += dt;   // integrated, so speed changes never jump
    if (visible()) drawSphere(cx);
  } });
  animate(m, { scale: 1, duration: reduced ? 0 : 1400, ease: "outQuart" });   // boot
  darkMode.addEventListener("change", () => setState(state));   // Windows theme flipped
}

export function setState(s) {
  state = s;
  const [spin, amp] = MOTION[s] || MOTION.idle, [r, g, b] = hex(colorOf(s));
  animate(m, { spin: reduced ? spin * .2 : spin, amp: reduced ? 0 : amp, r, g, b,
               duration: 1400, ease: "inOutSine", onUpdate: () => onColor(`rgb(${m.r | 0}, ${m.g | 0}, ${m.b | 0})`) });
  beatAnim?.pause();
  beatAnim = amp && !reduced
    ? animate(m, { beat: [-1, 1], duration: 700, ease: "inOutSine", loop: true, alternate: true })
    : animate(m, { beat: 0, duration: 300 });
  animate(m, { rip: s === "speaking" && !reduced ? 1 : 0, duration: 900, ease: "inOutSine" });
}
