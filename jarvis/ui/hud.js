// The JARVIS HUD: talks to the server over /ws and animates everything with anime.js.
import { animate, createTimeline, stagger, svg, scrambleText, utils } from "/ui/anime.esm.min.js";
import * as core from "/ui/core.js";

const $ = id => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const D = ms => reduced ? 0 : ms;   // every duration goes through this
const BIG = { idle: "Standing by", listening: "Listening", thinking: "Processing", speaking: "Speaking",
              confirming: "Yes or no?", off: "Offline" };
const ICON = { done: "✓", failed: "✗", declined: "–", undone: "↶" };
// tools/<module>.py → neural node
const NODE = { notes: "memory", memory: "memory", web: "research", claude: "research", delegate: "research",
               github: "research", files: "study", apps: "system", windows: "system", edit: "system",
               devices: "hardware", day: "planner", briefing: "planner", google_tools: "comms", outbound: "comms" };

let ws, state = "off", view = "home", screenText = "", current = null, confirmId = null, confirmTimer = null;
let muted = false, attached = [], lit = new Set(), litTimer = null;
const shown = { spent: 0, total: 0, pct: 0 };   // numbers count up to their new values

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const enter = (targets, extra = {}) => animate(targets, { opacity: [0, 1], y: [10, 0], duration: D(450), ease: "outExpo", ...extra });

// ---------- state + readout
function setState(s) {
  if (s === state) return;
  state = s; document.body.dataset.state = s;
  core.setState(s);
  $("st").textContent = s; $("aio").textContent = muted ? "muted" : s;
  animate($("big"), { innerHTML: scrambleText({ text: BIG[s] || s, chars: "uppercase", cursor: "_" }), duration: D(500) });
  clearTimeout(litTimer);
  if (s === "idle") litTimer = setTimeout(() => { lit.clear(); drawNet(); }, 2500);
  drawNet();
}
function renderDetail() { $("detail").innerHTML = DOMPurify.sanitize(marked.parse(screenText)); }
function line(cls, text) {
  const p = el("p", "msg " + cls, text);
  $("transcript").append(p); $("transcript").scrollTop = 1e9; enter(p, { x: [cls === "user" ? -8 : 8, 0], y: 0 }); return p;
}
function banner(text) {
  const c = el("div", "card"), body = el("div"), x = el("button", null, "×");
  body.append(el("b", null, /^reminder/i.test(text) ? "Reminder" : "Notice"), el("span", null, text.replace(/^reminder[^:]*:\s*/i, "")));
  x.type = "button"; x.setAttribute("aria-label", "Dismiss");
  const close = () => animate(c, { opacity: 0, y: -10, duration: D(300), ease: "inQuad", onComplete: () => c.remove() });
  x.onclick = close; c.append(body, x); $("banners").append(c);
  enter(c, { y: [-14, 0], scale: [.96, 1] }); setTimeout(close, 15000);
}
function countTo(key, value, render) {
  animate(shown, { [key]: value, duration: D(900), ease: "outExpo", onUpdate: () => render(shown[key]) });
}

// ---------- actions log
function actRow(a) {
  let li = $("act" + a.id); const fresh = !li;
  if (fresh) { li = el("li"); li.id = "act" + a.id; $("actlist").prepend(li); }
  li.replaceChildren();
  li.append(el("span", a.status, ICON[a.status] || "·"));
  li.lastChild.title = a.status + ": " + (a.result || "");
  const t = el("span", "tool", a.tool + " ");
  t.append(el("em", null, Object.values(a.args || {}).join(" ") + (a.agent ? " · via " + a.agent : "")));
  t.title = t.textContent; li.append(t, el("span", "t", (a.ts || "").slice(11, 16)));
  if (a.status === "done" && a.undo) {
    const b = el("button", "mini", "Undo"); b.type = "button"; b.onclick = () => send({ type: "undo", id: a.id }); li.append(b);
  } else li.append(el("span"));
  if (fresh) enter(li, { x: [-10, 0], y: 0 });
}

// ---------- confirm
function closeConfirm() {
  clearInterval(confirmTimer); confirmId = null;
  animate($("confirm"), { opacity: 0, scale: .96, duration: D(200), onComplete: () => $("confirm").classList.remove("open") });
}
function answer(ok) { if (confirmId) send({ type: "confirm_reply", id: confirmId, ok }); }

// ---------- views
function show(v) {
  if (v === view) return;
  view = v;
  for (const id of ["home", "chats", "brain"]) $(id).hidden = id !== v;
  document.querySelectorAll(".nav[data-view]").forEach(b => {
    const on = b.dataset.view === v && v !== "home";
    b.classList.toggle("on", on); if (b.hasAttribute("aria-pressed")) b.setAttribute("aria-pressed", on);
  });
  animate($(v), { opacity: [0, 1], scale: [.97, 1], duration: D(500), ease: "outExpo" });
  if (v === "chats") enter("#chatlist li", { delay: stagger(45) });
  if (v === "brain") { animate(".nd", { scale: [0, 1], duration: D(600), delay: stagger(50, { from: "center" }), ease: "outBack" }); drawNet(true); }
}
const drawables = new Map();
function drawNet(redraw = false) {
  const on = new Set(lit);
  if (state !== "idle" && state !== "off") on.add("core");
  if (["listening", "speaking", "confirming"].includes(state)) on.add("voice");
  document.querySelectorAll(".nd").forEach(n => {
    const was = n.classList.contains("on"), is = on.has(n.dataset.k);
    n.classList.toggle("on", is);
    if (is && !was && view === "brain") animate(n, { scale: [1, 1.18, 1], duration: D(600), ease: "outElastic(1, .5)" });
  });
  document.querySelectorAll(".ln").forEach(l => {
    const was = l.classList.contains("on"), is = on.has("core") && on.has(l.dataset.k);
    l.classList.toggle("on", is);
    if (is && (!was || redraw)) {
      if (!drawables.has(l)) drawables.set(l, svg.createDrawable(l)[0]);
      animate(drawables.get(l), { draw: ["0 0", "0 1"], duration: D(500), ease: "outQuad" });
    }
  });
}
function renderChats(m) {
  $("chatlist").replaceChildren(...m.chats.map(c => {
    const li = el("li"), b = el("button", "row" + (c.id === m.current ? " cur" : ""));
    b.type = "button";
    b.append(el("span", "ti", c.title), el("span", "me", `${c.count} message${c.count === 1 ? "" : "s"} · ` +
             (c.ts || "").slice(0, 16).replace("T", " ") + (c.id === m.current ? " · current" : "")));
    b.onclick = () => { send({ type: "open_chat", id: c.id }); show("home"); };
    li.append(b); return li;
  }));
  if (!m.chats.length) $("chatlist").append(el("li", "hint", "Chats you have show up here. New chat files the current one."));
  $("chatno").textContent = "chat " + m.current;
}

// ---------- attachments
function renderFiles() {
  $("files").replaceChildren(...attached.map((f, i) => {
    const li = el("li"), s = el("span", null, f.name), x = el("button", "x", "×");
    s.title = f.path; x.type = "button"; x.setAttribute("aria-label", "Detach " + f.name);
    x.onclick = () => animate(li, { opacity: 0, scale: .8, duration: D(200), onComplete: () => { attached.splice(i, 1); renderFiles(); } });
    li.append(s, x); return li;
  }));
}
$("upload").onchange = e => {
  for (const f of e.target.files) {
    if (f.size > 10 * 1024 * 1024) { banner(`Couldn't attach ${f.name}: over 10 MB`); continue; }
    const r = new FileReader();
    r.onload = () => send({ type: "upload", name: f.name, data: r.result.split(",")[1] || "" });
    r.readAsDataURL(f);
  }
  e.target.value = "";
};
function say(text) {
  if (attached.length) text += "\n\nAttached files:\n" + attached.map(f => "- " + f.path).join("\n");
  attached = []; renderFiles(); send({ type: "text_input", text });
}

// ---------- events from the server
const on = {
  history(m) {
    $("transcript").replaceChildren();
    for (const h of m.messages) {
      const p = el("p", "msg " + h.role, h.content || (h.screen ? "(on screen)" : "(interrupted)"));
      if (h.interrupted) p.classList.add("cut");
      $("transcript").append(p);
    }
    enter("#transcript .msg", { delay: stagger(25) }); $("transcript").scrollTop = 1e9;
    const last = m.messages.findLast(h => h.role === "assistant");
    screenText = last ? last.screen : ""; renderDetail();
    $("caption").textContent = last ? last.content : "";
    setRoute(m.route);
  },
  usage(m) {
    const pct = m.cap_inr ? Math.min(100, Math.round(m.spent_inr / m.cap_inr * 100)) : 0;
    const req = Object.entries(m.requests_today), total = req.reduce((a, [, n]) => a + n, 0);
    countTo("spent", m.spent_inr, v => { $("spentv").textContent = "₹" + v.toFixed(2).replace(/\.00$/, ""); $("statspent").textContent = $("spentv").textContent; });
    countTo("pct", pct, v => $("pct").textContent = Math.round(v) + "%");
    countTo("total", total, v => $("stattotal").textContent = Math.round(v));
    $("capv").textContent = $("statcap").textContent = "₹" + m.cap_inr;
    for (const id of ["spendbar", "bigbar"]) {
      $(id).classList.toggle("warn", pct >= 80);
      animate($(id).firstElementChild, { width: pct + "%", duration: D(900), ease: "outExpo" });
    }
    $("reqs").replaceChildren(...req.map(([p, n]) => {
      const d = el("div", "req"); d.append(el("span", null, p.replace(/_/g, " ")), el("b", null, n)); return d;
    }));
  },
  state(m) { setState(m.state); },
  user_text(m) {
    line("user", m.text); current = null; screenText = ""; renderDetail();
    lit.clear(); drawNet();
    $("netcap").replaceChildren(el("span", null, "Command "), "“" + m.text.split("\n")[0] + "”");
    $("caption").textContent = "";
  },
  spoken_sentence(m) {
    if (!current) current = line("assistant", m.text);
    else current.textContent += " " + m.text;
    $("caption").textContent = current.textContent;
    animate($("caption"), { opacity: [.3, 1], duration: D(350) });
  },
  screen_delta(m) { screenText += m.text; renderDetail(); },
  turn_end() { current = null; setState("idle"); },
  route(m) { $("st").textContent = `thinking (${m.route})`; },
  banner(m) { banner(m.text); },
  actions(m) { $("actlist").replaceChildren(); [...m.actions].reverse().forEach(actRow); },
  action(m) { actRow(m); },
  agent(m) { $("st").textContent = `consulting ${m.agent}`; lit.add("research"); drawNet(); },
  tool(m) { $("st").textContent = "running " + m.name; lit.add(NODE[m.module] || "system"); drawNet(); },
  confirm(m) {
    confirmId = m.id; let left = m.seconds;
    $("cq").textContent = m.text;
    const tick = () => { $("cleft").textContent = left > 0 ? `${left}s` : ""; left--; };
    clearInterval(confirmTimer); tick(); confirmTimer = setInterval(tick, 1000);
    show("home"); $("confirm").classList.add("open");
    animate($("confirm"), { opacity: [0, 1], scale: [.9, 1], duration: D(450), ease: "outBack" });
    $("confirm").querySelector(".no").focus();   // safe default
  },
  confirm_done(m) { if (m.id === confirmId) closeConfirm(); },
  chats: renderChats,
  mic(m) {
    muted = m.muted; core.setMuted(muted);
    $("micst").textContent = !m.available ? "off" : muted ? "muted" : "live";
    const b = $("mic"), label = muted ? "Unmute microphone" : "Mute microphone";
    b.classList.toggle("muted", muted); b.setAttribute("aria-pressed", muted);
    b.setAttribute("aria-label", label); b.title = m.available ? label : "No microphone (voice off or still loading)";
    b.querySelector(".on").hidden = muted; b.querySelector(".off").hidden = !muted;
    $("aio").textContent = muted ? "muted" : state;
    animate(b, { scale: [.85, 1], duration: D(400), ease: "outBack" });
  },
  uploaded(m) { attached.push(m); renderFiles(); enter("#files li:last-child", { scale: [.8, 1], y: 0 }); openTyper(true); },
};

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { $("link").textContent = "online"; setState("idle"); };
  ws.onmessage = e => { const m = JSON.parse(e.data); on[m.type]?.(m); };
  ws.onclose = () => { $("link").textContent = "offline"; setState("off"); setTimeout(connect, 2000); };
}
const send = m => ws?.readyState === 1 && ws.send(JSON.stringify(m));

// ---------- controls
function setRoute(r) {
  $("routest").textContent = r; $("directive").textContent = "Primary directive · route " + r;
  document.querySelectorAll("#route button").forEach(b => b.setAttribute("aria-pressed", b.dataset.r === r));
}
document.querySelectorAll("#route button").forEach(b => b.onclick = () => { setRoute(b.dataset.r); send({ type: "set_route", route: b.dataset.r }); });
document.querySelectorAll(".cmd[data-say]").forEach(b => b.onclick = () => {
  animate(b, { scale: [.94, 1], duration: D(300), ease: "outBack" }); say(b.dataset.say);
});
document.querySelectorAll(".nav[data-view]").forEach(b => b.onclick = () => show(b.dataset.view));
$("newchat").onclick = () => { send({ type: "new_chat" }); show("home"); };
$("stopbtn").onclick = $("core").onclick = () => send({ type: "cancel" });
$("mic").onclick = () => send({ type: "mute", on: !muted });
function openTyper(open) {
  $("typer").classList.toggle("open", open); $("typebtn").setAttribute("aria-expanded", open);
  $("typebtn").setAttribute("aria-label", open ? "Close typing" : "Type a command");
  $("text").tabIndex = open ? 0 : -1; if (open) $("text").focus();
}
$("typebtn").onclick = () => openTyper(!$("typer").classList.contains("open"));
$("text").addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.value.trim()) { say(e.target.value); e.target.value = ""; }
  else if (e.key === "Escape") openTyper(false);
});
$("confirm").querySelector(".yes").onclick = () => answer(true);
$("confirm").querySelector(".no").onclick = () => answer(false);

// ---------- clock
function clock() {
  const d = new Date(), p = n => String(n).padStart(2, "0");
  $("hm").textContent = `${p(d.getHours())}:${p(d.getMinutes())}`; $("ss").textContent = ":" + p(d.getSeconds());
  $("date").textContent = d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" }).replace(",", " ·");
}
clock(); setInterval(clock, 1000);

// ---------- boot
core.start($("sphere"), $("audio"), {
  visible: () => view === "home",
  color: rgb => document.documentElement.style.setProperty("--acc", rgb),
});
createTimeline({ defaults: { ease: "outExpo", duration: D(700) } })
  .add(".brand h1", { innerHTML: scrambleText({ text: "J.A.R.V.I.S.", chars: "uppercase" }), duration: D(900) }, 0)
  .add(".chips > span, .clock", { opacity: [0, 1], y: [-8, 0], delay: stagger(D(70)) }, D(150))
  .add(".panel", { opacity: [0, 1], y: [16, 0], delay: stagger(D(60)) }, D(250))
  .add(".readout, .dock", { opacity: [0, 1], y: [12, 0], delay: stagger(D(120)) }, D(450));
connect();
