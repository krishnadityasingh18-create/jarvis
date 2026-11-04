// Home: three live cards (Claude Code, Music, Focus), Claude's progress in detail, and quick
// actions on whatever is on the clipboard. Data arrives on the island's socket (see island.js).
import { desk, listen, send, mmss, focusLeft } from "/ui/island.js";

const $ = q => document.querySelector(q);
const bridge = window.island;
const PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>';
const STOP = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';

// ---------- Claude Code
const STEPS = ["read", "edit", "test", "done"];
const SUB = { start: "Starting", read: "Reading the code", edit: "Making changes", test: "Running tests", done: "Done", failed: "Stopped" };
const SHORT = { ...SUB, read: "Reading", edit: "Editing", test: "Testing" };   // the card is narrow
const RING = { start: .08, read: .3, edit: .6, test: .85, done: 1, failed: 1 };
function claude() {
  const c = desk.claude, live = c && c.step !== "done" && c.step !== "failed";
  $("#hc-claude").classList.toggle("on", !!live);
  $("#hc-claude .arc").style.strokeDashoffset = 88 * (1 - (c ? RING[c.step] ?? 0 : 0));
  $("#cc-time").textContent = c ? mmss(Math.max(0, Math.round(Date.now() / 1000 - c.started))) : "—";
  const n = c ? STEPS.indexOf(c.step) : -1;
  $("#cc-sub").textContent = !c ? "No task running" : SHORT[c.step] + (n >= 0 && n < 3 ? ` · ${n + 1} of 4` : "");
  $("#cc-live").hidden = !c;
  if (!c) return;
  $("#cl-proj").textContent = c.project;
  $("#cl-step").textContent = SUB[c.step];
  if (c.step === "edit" || c.step === "read") {   // the file being worked on, and the change
    $("#cl-file").hidden = !c.file;
    $("#cl-fn").textContent = c.file + (c.step === "edit" ? " · being edited" : " · reading");
    $("#cl-old").textContent = c.old ? "− " + c.old + "\n" : "";
    $("#cl-new").textContent = c.new ? "+ " + c.new : "";
  }
  const at = c.step === "failed" ? -1 : c.step === "start" ? 0 : n;
  document.querySelectorAll("#cl-steps i").forEach((i, k) => {
    i.classList.toggle("done", k < at || c.step === "done");
    i.classList.toggle("now", k === at && c.step !== "done");
  });
  $("#cc-live").classList.toggle("failed", c.step === "failed");
}
listen("claude_live", claude);
$("#hc-claude").addEventListener("click", () => desk.claude && $("#cc-live").scrollIntoView({ behavior: "smooth", block: "nearest" }));

// ---------- Music (whatever Windows says is playing: Spotify, a browser tab, …)
function music() {
  const m = desk.media, has = !!m.title;
  $("#hc-music").classList.toggle("on", has);
  $("#mu-cover").style.backgroundImage = has && m.thumb ? `url("${m.thumb}")` : "";
  $("#mu-t").textContent = has ? m.title : "Nothing playing";
  $("#mu-a").textContent = has ? m.artist || m.app || "" : "Play something";
  $("#mu-pp").innerHTML = m.playing ? PAUSE : PLAY;
  document.querySelectorAll("#hc-music .cb").forEach(b => { b.disabled = !has; });
}
listen("media", music);
document.querySelectorAll("#hc-music .cb").forEach(b => b.addEventListener("click", () => send({ type: "media", action: b.dataset.m })));

// ---------- Focus (25 minutes; the backend keeps the time, so every window agrees)
function focus() {
  const left = focusLeft(), on = left > 0, total = (desk.focus.minutes || 25) * 60;
  $("#hc-focus").classList.toggle("on", on);
  $("#hc-focus").style.setProperty("--p", on ? 1 - left / total : 0);
  $("#fo-time").textContent = mmss(on ? left : 25 * 60);
  $("#fo-sub").textContent = on ? `${Math.ceil(left / 60)} min left` : "Ready to start";
  $("#fo-go").innerHTML = on ? STOP : PLAY;
  $("#fo-go").setAttribute("aria-label", on ? "Stop focus" : "Start focus");
}
listen("focus", focus);
$("#fo-go").addEventListener("click", () => send({ type: "focus", minutes: focusLeft() > 0 ? 0 : 25 }));
setInterval(() => { focus(); if (desk.claude && desk.claude.step !== "done" && desk.claude.step !== "failed") claude(); }, 1000);

// ---------- quick actions on the clipboard: the request goes in as a typed turn, shown in Chat
async function clipboard() {
  try { return bridge?.clipboard ? await bridge.clipboard() : await navigator.clipboard.readText(); }
  catch { return ""; }
}
document.querySelectorAll("#qa button").forEach(b => b.addEventListener("click", async () => {
  const text = (await clipboard()).trim();
  if (!text) return emptyClip(b);
  if (send({ type: "text_input", text: `${b.dataset.q}:\n\n${text.slice(0, 6000)}` }))
    document.querySelector('#seg button[data-v="chat"]').click();
}));
function emptyClip(b) {
  const was = b.textContent;
  b.textContent = "Clipboard is empty"; b.disabled = true;
  setTimeout(() => { b.textContent = was; b.disabled = false; }, 1600);
}

claude(); music(); focus();
