// The core: an Apple Intelligence-style glow ring. Four layers of the same flowing
// multicolour gradient, from a crisp line to a wide soft bloom, each turning at its own
// speed and breathing on its own phase. States change motion and brightness only.
// anime.js owns time: one createTimer is the frame loop, every value the renderer reads
// (spin, brightness, energy) is tweened by animate().
import { animate, createTimer, utils } from "/ui/anime.esm.min.js";

// Apple Intelligence-style hues, vivid enough to stay distinct as the layers blend,
// closed into a loop for the conic gradient
const PALETTE = ["#4F8BFF", "#B26BFF", "#FF5EA8", "#FF8A4C", "#FFC46B", "#FF5EA8", "#B26BFF", "#3FC8FF", "#4F8BFF"];
export const ACCENT = "#A374F5";   // one colour for the rest of the UI (status dot, ambient glow)
// layer: line width, blur (both as fractions of the radius), opacity, turn rate vs. the base spin
const LAYERS = [[.03, 0, 1, 1], [.07, .035, .9, -.7], [.14, .08, .7, 1.35], [.24, .13, .55, -.9]];
// per state: base spin (rad/s), brightness, resting energy
const MOTION = { idle: [.25, .8, 0], listening: [.6, 1, .35], thinking: [2.2, 1, .2], speaking: [.5, 1, 0],
                 confirming: [.6, 1, .35], off: [.08, .35, 0] };
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const darkMode = matchMedia("(prefers-color-scheme: dark)");

// everything the renderer reads; only anime.js (and the clock) writes it
const m = { spin: .08, bright: .35, energy: 0, scale: .85, rot: 0, fade: 0 };
let speech = null, state = "off", layers = [], built = 0;

function fit(c) {
  const r = c.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return [w, h];
}

// each layer drawn once per size into its own canvas; frames only rotate and fade them
function build(R) {
  layers = LAYERS.map(([lw, blur, alpha, turn]) => {
    const pad = R * (lw + blur * 3), S = Math.ceil((R + pad) * 2), c = document.createElement("canvas");
    c.width = c.height = S;
    const x = c.getContext("2d"), g = x.createConicGradient(0, S / 2, S / 2);
    PALETTE.forEach((col, i) => g.addColorStop(i / (PALETTE.length - 1), col));
    x.filter = blur ? `blur(${(R * blur).toFixed(1)}px)` : "none";
    x.strokeStyle = g; x.lineWidth = R * lw;
    x.beginPath(); x.arc(S / 2, S / 2, R, 0, Math.PI * 2); x.stroke();
    return { c, S, alpha, turn, phase: Math.random() * 6.28 };
  });
  built = R;
}

function draw(cx, t) {
  const [w, h] = fit(cx.canvas), R = Math.round(Math.min(w, h) * .31);   // room for the bloom inside the canvas
  if (R !== built) build(R);
  cx.clearRect(0, 0, w, h);
  // a faint wash inside the ring, so it reads as an orb, not a hoop
  const g = cx.createRadialGradient(w / 2, h / 2, R * .1, w / 2, h / 2, R);
  g.addColorStop(0, "rgba(163,116,245,0)"); g.addColorStop(1, `rgba(163,116,245,${.08 * m.bright})`);
  cx.fillStyle = g; cx.beginPath(); cx.arc(w / 2, h / 2, R, 0, Math.PI * 2); cx.fill();
  layers.forEach((L, i) => {
    // the crisp lines keep their true colours; only the soft blooms add light (in the dark)
    cx.globalCompositeOperation = i > 1 && darkMode.matches ? "lighter" : "source-over";
    const breathe = reduced ? 1 : .85 + .15 * Math.sin(t * .8 + L.phase);   // no slow loop if motion is reduced
    const swell = 1 + m.energy * .025 * (i / 3);          // outer blooms swell with speech
    cx.globalAlpha = Math.min(1, L.alpha * m.bright * m.fade * breathe * (1 + m.energy * .6 * (i / 3)));
    cx.save(); cx.translate(w / 2, h / 2); cx.rotate(m.rot * L.turn + L.phase); cx.scale(swell * m.scale, swell * m.scale);
    cx.drawImage(L.c, -L.S / 2, -L.S / 2); cx.restore();
  });
  cx.globalAlpha = 1; cx.globalCompositeOperation = "source-over";
}

/** Start the loop. visible(): whether the orb is on screen; color(css): the UI accent. */
export function start(canvas, { visible, color }) {
  color(ACCENT);
  const cx = canvas.getContext("2d");
  createTimer({ onUpdate: self => {
    m.rot += m.spin * Math.min(.05, self.deltaTime / 1000);   // integrated: speed changes never jump
    if (visible()) draw(cx, self.currentTime / 1000);
  } });
  animate(m, { scale: 1, fade: 1, duration: reduced ? 0 : 1200, ease: "outQuart" });   // boot
  darkMode.addEventListener("change", () => { built = 0; });
}

export function setState(s) {
  state = s;
  const [spin, bright, energy] = MOTION[s] || MOTION.idle;
  speech?.pause(); speech = null;
  animate(m, { spin: reduced ? spin * .2 : spin, bright, duration: 1000, ease: "inOutSine" });
  if (s === "speaking" && !reduced) {
    // speech: the glow swells and settles in irregular breaths, re-rolled every beat
    speech = animate(m, { energy: () => utils.random(.35, 1, 2), duration: () => utils.random(160, 280),
                          ease: "inOutSine", loop: true, onLoop: a => a.refresh() });
  } else animate(m, { energy: reduced ? 0 : energy, duration: 600, ease: "inOutSine" });
}
