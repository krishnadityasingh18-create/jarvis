// Jarvis's window: talks to the server over /ws. Apple-style: one focused stage, glass
// layers, and critically damped springs (interruptible: every animation starts from the
// element's current on-screen value).
import { animate, spring, stagger, svg, utils } from "/ui/anime.esm.min.js";
import * as core from "/ui/core.js";

const $ = id => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const SPRING = spring({ bounce: 0, duration: 380 });   // damping 1.0, response ~0.4 s
// move with a spring; with reduced motion, only cross-fade
function move(el, props, extra = {}) {
  if (!reduced) return animate(el, { ...props, ease: SPRING, ...extra });
  const { opacity, ...rest } = props;
  utils.set(el, Object.fromEntries(Object.entries(rest).map(([k, v]) => [k, Array.isArray(v) ? v[1] : v])));
  return animate(el, { opacity: opacity ?? 1, duration: 150, ...extra });
}
const STATUS = { idle: "Ready", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking",
                 confirming: "Waiting for your answer", off: "Offline, reconnecting…" };
const ICON = { done: "✓", failed: "✕", declined: "–", undone: "↶" };
// tools/<module>.py → neural node
const NODE = { notes: "memory", memory: "memory", web: "research", claude: "research", delegate: "research",
               github: "research", files: "study", apps: "system", windows: "system", edit: "system",
               devices: "hardware", day: "planner", briefing: "planner", google_tools: "comms", outbound: "comms" };

let ws, state = "off", tab = "conv", sheetOpen = false, screenText = "", current = null, confirmId = null;
let mic = { muted: false, available: false }, attached = [], lit = new Set(), litTimer = null;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

// ---------- status
function status(text) { $("status").textContent = text; }
function setState(s) {
  if (s === state) return;
  state = s; core.setState(s);
  const note = !mic.available ? " · mic off" : mic.muted ? " · muted" : "";
  status(STATUS[s] + (s === "idle" || s === "off" ? note : ""));
  clearTimeout(litTimer);
  if (s === "idle") litTimer = setTimeout(() => { lit.clear(); drawNet(); }, 2500);
  drawNet();
}

// ---------- conversation
function renderDetail() {
  $("detail").innerHTML = screenText ? DOMPurify.sanitize(marked.parse(screenText)) : "";
  if (!screenText) $("detail").append(el("p", "empty", "Longer answers, tables and links appear here."));
  $("more").hidden = !screenText;
}
function bubble(cls, text) {
  const p = el("p", "msg " + cls, text);
  $("transcript").append(p); p.scrollIntoView({ block: "end" });
  move(p, { opacity: [0, 1], y: [6, 0] });
  return p;
}
function toast(text) {
  const t = el("div", "toast glass strong"), body = el("div"), x = el("button", "icon-btn", "×");
  body.append(el("b", null, /^reminder/i.test(text) ? "Reminder" : "Jarvis"), el("span", null, text.replace(/^reminder[^:]*:\s*/i, "")));
  x.type = "button"; x.setAttribute("aria-label", "Dismiss");
  // in from the right, out to the right: the same path both ways
  const close = () => move(t, { x: 40, opacity: 0 }, { onComplete: () => t.remove() });
  x.onclick = close; t.append(body, x); $("toasts").append(t);
  move(t, { x: [40, 0], opacity: [0, 1] }); setTimeout(close, 15000);
}

// ---------- activity
function actRow(a) {
  let li = $("act" + a.id); const fresh = !li;
  if (fresh) { li = el("li"); li.id = "act" + a.id; $("actlist").prepend(li); }
  li.replaceChildren();
  li.append(el("span", a.status, ICON[a.status] || "·"));
  li.lastChild.title = a.status + ": " + (a.result || "");
  const t = el("span", "tool", a.tool.replace(/_/g, " ") + " ");
  t.append(el("em", null, Object.values(a.args || {}).join(" ").replace(/[*_`#>]+/g, "") + (a.agent ? " · via " + a.agent : "")));
  t.title = t.textContent; li.append(t, el("span", "t", (a.ts || "").slice(11, 16)));
  if (a.status === "done" && a.undo) {
    const b = el("button", "btn", "Undo"); b.type = "button"; b.onclick = () => send({ type: "undo", id: a.id }); li.append(b);
  } else li.append(el("span"));
  if (fresh && sheetOpen && tab === "activity") move(li, { opacity: [0, 1], y: [-6, 0] });
}

// ---------- confirm: a modal question, so dim to focus
let returnFocus = null;
function openConfirm(text) {
  $("cq").textContent = text;
  if ($("confirm").hidden) {   // a dialog still closing is caught mid-flight instead
    utils.set($("scrim"), { opacity: 0 }); utils.set($("confirm"), { opacity: 0, scale: .94 });
    returnFocus = document.activeElement;
  }
  $("scrim").hidden = $("confirm").hidden = false;
  move($("scrim"), { opacity: 1 });
  move($("confirm"), { opacity: 1, scale: 1 });
  $("confirm").querySelector(".no").focus();   // the safe answer is the default
}
function closeConfirm() {
  confirmId = null;
  if ($("confirm").contains(document.activeElement)) returnFocus?.focus?.();
  move($("scrim"), { opacity: 0 }, { onComplete: () => { if (!confirmId) $("scrim").hidden = true; } });
  move($("confirm"), { opacity: 0, scale: .94 }, { onComplete: () => { if (!confirmId) $("confirm").hidden = true; } });
}
function answer(ok) { if (confirmId) send({ type: "confirm_reply", id: confirmId, ok }); }

// ---------- side panel (non-blocking: no scrim, the stage stays live)
function selectTab(name) {
  tab = name;
  document.querySelectorAll(".seg button").forEach(b => {
    const on = b.dataset.tab === name; b.setAttribute("aria-selected", on); b.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll(".panel").forEach(p => { p.hidden = p.dataset.panel !== name; });
  const panel = document.querySelector(`.panel[data-panel="${name}"]`);
  if (sheetOpen) move(panel, { opacity: [0, 1] });
  if (name === "conv") $("transcript").lastElementChild?.scrollIntoView({ block: "end" });
  if (name === "chats") move("#chatlist li", { opacity: [0, 1], y: [6, 0] }, { delay: stagger(30) });
  if (name === "neural") { move(".nd", { scale: [.6, 1], opacity: [0, 1] }, { delay: stagger(30, { from: "center" }) }); drawNet(true); }
}
// make room: the stage glides left to centre in what the panel leaves (when there's space),
// and notifications move off the panel so glass never stacks on glass
function makeRoom() {
  const w = $("sheet").offsetWidth || Math.min(420, innerWidth - 28);
  // room beside the panel (a half-screen snap has it): the stage and input bar shrink into it;
  // otherwise the panel floats over the stage but stops above the input bar
  const side = sheetOpen && innerWidth - w - 28 >= 420;
  document.body.classList.toggle("panel-over", sheetOpen && !side);
  move(".stage, .dock", { right: `${side ? w + 14 : 0}px` });
  move("#toasts", { x: side ? -(w + 30) : 0 });
}
addEventListener("resize", () => { if (sheetOpen) makeRoom(); });
function sheet(open, name) {
  if (name) selectTab(name);
  if (open === sheetOpen) return;
  sheetOpen = open; $("menu").setAttribute("aria-expanded", open);
  const s = $("sheet");
  // make room: the stage glides left to centre in what the panel leaves (when there's space)
  makeRoom();
  if (open) {
    if (s.hidden) { utils.set(s, { x: 40, opacity: 0, scale: .98 }); s.hidden = false; }
    move(s, { x: 0, opacity: 1, scale: 1 });
    selectTab(tab);
  } else {
    move(s, { x: 40, opacity: 0, scale: .98 }, { onComplete: () => { if (!sheetOpen) s.hidden = true; } });
  }
}

// ---------- neural view
const drawables = new Map();
function drawNet(redraw = false) {
  const on = new Set(lit);
  if (state !== "idle" && state !== "off") on.add("core");
  if (["listening", "speaking", "confirming"].includes(state)) on.add("voice");
  document.querySelectorAll(".nd").forEach(n => n.classList.toggle("on", on.has(n.dataset.k)));
  document.querySelectorAll(".ln").forEach(l => {
    const was = l.classList.contains("on"), is = on.has("core") && on.has(l.dataset.k);
    l.classList.toggle("on", is);
    if (is && (!was || redraw) && sheetOpen && tab === "neural" && !reduced) {
      if (!drawables.has(l)) drawables.set(l, svg.createDrawable(l)[0]);
      animate(drawables.get(l), { draw: ["0 0", "0 1"], ease: SPRING });
    }
  });
}

// ---------- history
function renderChats(m) {
  $("chatlist").replaceChildren(...m.chats.map(c => {
    const li = el("li"), b = el("button", "row" + (c.id === m.current ? " cur" : ""));
    b.type = "button";
    const when = (c.ts || "").slice(0, 16).replace("T", " ");
    b.append(el("span", "ti", c.title), el("span", "me", `${c.count} message${c.count === 1 ? "" : "s"} · ${when}` +
             (c.id === m.current ? " · open now" : "")));
    b.onclick = () => { send({ type: "open_chat", id: c.id }); selectTab("conv"); };
    li.append(b); return li;
  }));
  if (!m.chats.length) $("chatlist").append(el("li", "empty", "Your chats will be listed here."));
}

// ---------- attachments
function renderFiles() {
  $("files").replaceChildren(...attached.map((f, i) => {
    const li = el("li", "glass strong"), s = el("span", null, f.name), x = el("button", "icon-btn", "×");
    s.title = f.path; x.type = "button"; x.setAttribute("aria-label", "Remove " + f.name);
    x.onclick = () => { attached.splice(i, 1); renderFiles(); };
    li.append(s, x); return li;
  }));
}
$("upload").onchange = e => {
  for (const f of e.target.files) {
    if (f.size > 10 * 1024 * 1024) { toast(`Couldn't attach ${f.name}: it's over 10 MB.`); continue; }
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

// ---------- the + menu and the model menu (as in Claude's desktop app): each grows out of
// its button; arrows move, Escape or a click elsewhere closes, focus starts on the useful item
function popover(btn, mn) {
  const p = { open: false, root: btn.parentElement };
  const items = () => [...mn.querySelectorAll(".mi")];
  p.set = open => {
    if (open === p.open) return;
    p.open = open; btn.setAttribute("aria-expanded", open);
    if (open) {
      pops.forEach(o => o !== p && o.set(false));   // one menu at a time
      if (mn.hidden) { utils.set(mn, { opacity: 0, scale: .92, y: 6 }); mn.hidden = false; }
      move(mn, { opacity: 1, scale: 1, y: 0 });
      (mn.querySelector('[aria-checked="true"][role=menuitemradio]') || items()[0]).focus();
    } else {
      move(mn, { opacity: 0, scale: .92, y: 6 }, { onComplete: () => { if (!p.open) mn.hidden = true; } });
    }
  };
  btn.onclick = () => p.set(!p.open);
  mn.addEventListener("keydown", e => {
    const list = items(), i = list.indexOf(document.activeElement);
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (step) { e.preventDefault(); list[(i + step + list.length) % list.length].focus(); }
    else if (e.key === "Home" || e.key === "End") { e.preventDefault(); list[e.key === "Home" ? 0 : list.length - 1].focus(); }
    else if (e.key === "Escape") { p.set(false); btn.focus(); }
    else if (e.key === "Tab") p.set(false);
  });
  return p;
}
const plus = popover($("plus"), $("plusmenu")), models = popover($("modelbtn"), $("modelmenu")), pops = [plus, models];
addEventListener("pointerdown", e => pops.forEach(p => { if (p.open && !p.root.contains(e.target)) p.set(false); }));
const menu = open => plus.set(open);
document.querySelectorAll("#modelmenu .mi").forEach(b => b.onclick = () => { models.set(false); setRoute(b.dataset.r, true); });
$("m-files").onclick = () => { menu(false); $("upload").click(); };
$("m-shot").onclick = () => { menu(false); status("Taking a screenshot…"); send({ type: "screenshot" }); };
// the switch stays in the menu; it just flips the model like the model menu does
$("m-think").onclick = () => setRoute($("m-think").getAttribute("aria-checked") === "true" ? "fast" : "reasoning", true);

// ---------- events from the server
const on = {
  history(m) {
    $("transcript").replaceChildren();
    for (const h of m.messages) {
      const p = el("p", "msg " + h.role, h.content || (h.screen ? "(on screen)" : "(interrupted)"));
      if (h.interrupted) p.classList.add("cut");
      $("transcript").append(p);
    }
    if (!m.messages.length) $("transcript").append(el("p", "empty", "Nothing said yet in this chat."));
    const last = m.messages.findLast(h => h.role === "assistant"), asked = m.messages.findLast(h => h.role === "user");
    screenText = last ? last.screen : ""; renderDetail();
    $("caption").textContent = last ? last.content : "";
    $("asked").textContent = asked ? asked.content.split("\n")[0] : "";
    setRoute(m.route);
  },
  state(m) { setState(m.state); },
  user_text(m) {
    $("transcript").querySelector(".empty")?.remove();
    bubble("user", m.text); current = null; screenText = ""; renderDetail();
    lit.clear(); drawNet();
    $("netcap").textContent = "“" + m.text.split("\n")[0] + "”";
    $("asked").textContent = m.text.split("\n")[0]; $("caption").textContent = "";
  },
  spoken_sentence(m) {
    if (!current) current = bubble("assistant", m.text);
    else current.textContent += " " + m.text;
    $("caption").textContent = current.textContent;
  },
  screen_delta(m) { screenText += m.text; renderDetail(); },
  turn_end() { current = null; setState("idle"); },
  route(m) { status(`Thinking with ${m.route}…`); },
  banner(m) { toast(m.text); },
  actions(m) { $("actlist").replaceChildren(); [...m.actions].reverse().forEach(actRow); },
  action(m) {
    actRow(m);
    const credits = m.tool === "call_me" && m.status === "done" && (m.result || "").split("Credits — ")[1];
    if (credits) credits.split(" · ").forEach(c => bubble("note", c));   // Jarvis called: what's left
  },
  agent(m) { status(`Asking ${m.agent}…`); lit.add("research"); drawNet(); },
  tool(m) { status(m.name.replace(/_/g, " ") + "…"); lit.add(NODE[m.module] || "system"); drawNet(); },
  confirm(m) { confirmId = m.id; openConfirm(m.text); },
  confirm_done(m) { if (m.id === confirmId) closeConfirm(); },
  chats: renderChats,
  mic(m) {
    mic = m;
    const b = $("mic"), label = m.muted ? "Unmute microphone" : "Mute microphone";
    b.classList.toggle("on", m.muted); b.setAttribute("aria-pressed", m.muted);
    b.setAttribute("aria-label", label); b.title = m.available ? label : "Microphone off (voice is loading or unavailable)";
    b.querySelector(".on").hidden = m.muted; b.querySelector(".off").hidden = !m.muted;
    if (state === "idle") { state = ""; setState("idle"); }   // refresh the status note
  },
  uploaded(m) { if (state === "idle") status(STATUS.idle); attached.push(m); renderFiles(); move("#files li:last-child", { opacity: [0, 1], scale: [.9, 1] }); $("text").focus(); },
};

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => setState("idle");
  ws.onmessage = e => { const m = JSON.parse(e.data); on[m.type]?.(m); };
  ws.onclose = () => { setState("off"); setTimeout(connect, 2000); };
}
const send = m => ws?.readyState === 1 && ws.send(JSON.stringify(m));

// ---------- controls
const ROUTE_NAME = { fast: "Fast", reasoning: "Reasoning", local: "Local" }, ROUTE_SHORT = { reasoning: "Think" };
let route = "fast";
function setRoute(r, tell = false) {
  route = r;
  $("modelname").textContent = (innerWidth < 520 && ROUTE_SHORT[r]) || ROUTE_NAME[r] || r; $("modelbtn").setAttribute("aria-label", "Model: " + (ROUTE_NAME[r] || r));
  document.querySelectorAll("#modelmenu .mi").forEach(b => b.setAttribute("aria-checked", b.dataset.r === r));
  $("m-think").setAttribute("aria-checked", r === "reasoning");
  if (tell) send({ type: "set_route", route: r });
}
$("newchat").onclick = () => send({ type: "new_chat" });
$("core").onclick = () => send({ type: "cancel" });
$("mic").onclick = () => send({ type: "mute", on: !mic.muted });
$("menu").onclick = () => sheet(!sheetOpen);
$("close").onclick = () => { sheet(false); $("menu").focus(); };
$("more").onclick = () => sheet(true, "detail");
const tabs = [...document.querySelectorAll(".seg button")];
tabs.forEach((b, i) => {
  b.onclick = () => selectTab(b.dataset.tab);
  b.tabIndex = b.getAttribute("aria-selected") === "true" ? 0 : -1;
  b.addEventListener("keydown", e => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!step) return;
    const next = tabs[(i + step + tabs.length) % tabs.length];
    selectTab(next.dataset.tab); next.focus();
  });
});
$("text").addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.value.trim()) { say(e.target.value); e.target.value = ""; }
});
$("sheet").addEventListener("keydown", e => { if (e.key === "Escape") { sheet(false); $("menu").focus(); } });
$("confirm").querySelector(".yes").onclick = () => answer(true);
$("confirm").querySelector(".no").onclick = () => answer(false);
$("confirm").addEventListener("keydown", e => {
  if (e.key === "Escape") answer(false);
  if (e.key === "Tab") {   // focus stays in the question until it's answered
    const [a, b] = $("confirm").querySelectorAll("button");
    if (document.activeElement === (e.shiftKey ? a : b)) { e.preventDefault(); (e.shiftKey ? b : a).focus(); }
  }
});

// ---------- any window size
// the stage ends where the input bar (and any attachment chips above it) begins
new ResizeObserver(([e]) => document.documentElement.style.setProperty("--dock", e.target.offsetHeight + "px"))
  .observe(document.querySelector(".dock"));
// a narrow window gets the short hint, so it never shows cut off
const hint = () => { $("text").placeholder = innerWidth < 520 ? "Ask Jarvis…" : "Ask Jarvis, or say “Hey Jarvis”"; };
hint(); addEventListener("resize", () => { hint(); setRoute(route); });

// ---------- boot
core.start($("sphere"), {
  visible: () => true,
  color: rgb => document.documentElement.style.setProperty("--acc", rgb),
});
move(".bar, .input", { opacity: [0, 1], y: [8, 0] }, { delay: stagger(80) });
connect();
