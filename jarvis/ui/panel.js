// The panel: the island expanded to 440×600. Chat (WhatsApp-style bubbles), Artifacts, Vault,
// glance cards and an input bar. Shares island.js's springs, socket and confirm card.
import { ui, sp, on, send, setMode, askCard, micButton, notify, ICON, MICRO } from "/ui/island.js";

const $ = q => document.querySelector(q);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const svg = html => { const t = document.createElement("template"); t.innerHTML = html; return t.content.firstChild; };
const wrap = (type, f) => { const prev = on[type]; on[type] = m => { prev?.(m); f(m); }; };
const bridge = window.island;
const md = text => DOMPurify.sanitize(marked.parse(text || ""));
const hhmm = ts => { const d = ts ? new Date(ts) : new Date(); return isNaN(d) ? "" : d.getHours() + ":" + String(d.getMinutes()).padStart(2, "0"); };
function when(ts) {
  const d = new Date(ts); if (!ts || isNaN(d)) return "";
  const days = Math.floor((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 864e5);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : days < 7 ? d.toLocaleDateString(undefined, { weekday: "long" })
    : days < 14 ? "last week" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
const MIC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/></svg>';

// ---------- open / collapse: the shell springs first, the content follows ~80 ms later
const shell = $("#shell"), panel = $("#panel");
ui.hooks.click = () => { if (ui.mode !== "open" && ui.mode !== "confirm") setMode("open"); };
ui.hooks.mode.push(m => {
  panel.classList.remove("in");
  if (m === "open") setTimeout(() => ui.mode === "open" && panel.classList.add("in"), 80);
  else if (document.activeElement && panel.contains(document.activeElement)) document.activeElement.blur();
});
$("#close").addEventListener("click", e => { e.stopPropagation(); setMode("idle"); });
$("#full").addEventListener("click", e => { e.stopPropagation(); send({ type: "full_view" }); });
// a click outside the panel collapses it (the window stays interactive while it's open)
addEventListener("pointerdown", e => { if (ui.mode === "open" && !shell.contains(e.target)) setMode("idle"); });

// ---------- status line
const STATUS = { idle: "Idle", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking", off: "Offline" };
let route = "fast";
const stat = () => { $("#stat").textContent = ui.state === "off" ? "Offline" : `${STATUS[ui.state]} · ${route} route`; };
ui.hooks.state.push(stat);
wrap("route", m => { route = m.route; stat(); });

// ---------- tabs: a segmented control with a sliding (spring) indicator
const tabs = [...document.querySelectorAll("#seg button")];
function tab(btn) {
  sp.seg.to(btn.offsetLeft - 3, MICRO);
  tabs.forEach(b => { b.classList.toggle("on", b === btn); b.setAttribute("aria-selected", b === btn); });
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("on", v.id === "v-" + btn.dataset.v));
}
tabs.forEach(b => b.addEventListener("click", () => tab(b)));
ui.hooks.draw = () => { $("#seg .pill").style.transform = `translateX(${sp.seg.x}px)`; };

// ---------- in-panel notices (the drop card waits while the panel is open)
let toastT;
function toast(text) {
  const t = $("#ptoast"); t.textContent = text; t.classList.add("on");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 3500);
}
for (const type of ["banner", "notify"]) {
  const prev = on[type];
  on[type] = m => ui.mode === "open" ? toast(m.title && m.icon !== "info" ? `${m.title}: ${m.text}` : m.text) : prev(m);
}

// ---------- glance cards
const GLABEL = { sun: "Weather", cal: "Next class", check: "Due soon", mail: "Mail", bell: "Reminder" };
wrap("glance", m => {
  $("#glance").replaceChildren(...(m.items || []).map(g => {
    const c = el("div", "g"), l = el("div", "l"), v = el("div", "v", g.value + " ");
    l.append(svg(ICON[g.icon] || ICON.check), GLABEL[g.icon] || "");
    v.append(el("small", null, g.detail || "")); c.append(l, v); return c;
  }));
});

// ---------- chat
const thread = $("#thread"), view = $("#v-chat");
let current = null, heardByVoice = false, chatsMsg = null;
const toBottom = () => { view.scrollTop = view.scrollHeight; };   // live turns always follow
function waCard(args, result) {
  const sent = /\bsent\b/i.test(result || "") && !/not sent|n't|error|fail/i.test(result || "");
  const c = el("div", "wa" + (sent ? "" : " failed"));
  c.append(el("div", "who", `To ${args.to || "?"}${sent ? "" : " · not sent"}`), args.text || "");
  return c;
}
function bubble(role, text, { ts, voice, old, cut } = {}) {
  const b = el("div", "b " + (role === "user" ? "me" : "jv") + (old ? " old" : "") + (cut ? " cut" : ""));
  if (voice) { const v = el("div", "voice"); v.append(svg(MIC), "voice"); b.append(v); }
  b.append(el("span", "tx", text));
  if (role !== "user") b.append(el("div", "md"));
  b.append(el("span", "m", hhmm(ts) + (role === "user" ? " ✓✓" : "")));
  return b;
}
wrap("history", m => {
  current = null;
  const out = [];
  if (m.messages.length) {
    const day = el("div", "day"); out.push(day);
    day.append(el("span", null, when(m.messages[0].ts).replace(/^./, c => c.toUpperCase()) || "Today"));
  }
  for (const h of m.messages) {
    const b = bubble(h.role, h.content || (h.screen ? "" : "(interrupted)"),
                     { ts: h.ts, voice: h.role === "user" && /voice/.test(h.route || ""), old: true, cut: !!h.interrupted });
    if (h.screen) b.querySelector(".md").innerHTML = md(h.screen);
    let acts = []; try { acts = h.actions ? JSON.parse(h.actions) : []; } catch { /* old rows */ }
    for (const a of acts) if (a.name === "send_whatsapp") {
      let args = {}; try { args = JSON.parse(a.arguments); } catch { /* keep {} */ }
      b.insertBefore(waCard(args, a.result), b.querySelector(".m"));
    }
    out.push(b);
  }
  if (!m.messages.length) out.push(el("div", "empty", "Nothing said yet in this chat."));
  thread.replaceChildren(...out);
  if (m.route) { route = m.route; stat(); }
  requestAnimationFrame(toBottom);
  if (chatsMsg) on.chats(chatsMsg);
});
ui.hooks.state.push(s => { if (s === "listening") heardByVoice = true; else if (s === "idle" || s === "off") heardByVoice = false; });
wrap("user_text", m => {
  thread.querySelector(".empty")?.remove();
  thread.append(bubble("user", m.text, { voice: heardByVoice })); heardByVoice = false; current = null;
  toBottom();
});
function reply() {
  if (!current) { current = bubble("assistant", ""); current.raw = ""; thread.append(current); }
  return current;
}
wrap("spoken_sentence", m => {
  const b = reply(), tx = b.querySelector(".tx");
  tx.textContent = (tx.textContent ? tx.textContent + " " : "") + m.text;
  toBottom();
});
wrap("screen_delta", m => {
  const b = reply();
  b.raw += m.text; b.querySelector(".md").innerHTML = md(b.raw);
  toBottom();
});
wrap("action", m => {
  if (m.tool !== "send_whatsapp") return;
  const b = reply(); b.insertBefore(waCard(m.args || {}, m.status === "done" ? m.result : ""), b.querySelector(".m")); toBottom();
});
wrap("turn_end", () => { current = null; });
wrap("chats", m => {
  chatsMsg = m;
  $("#chips").replaceChildren(...m.chats.map(c => {
    const b = el("button", "chip" + (c.id === m.current ? " on" : ""), c.title);
    b.type = "button"; b.title = `${c.title} · ${c.count} message${c.count === 1 ? "" : "s"}`;
    b.onclick = () => send({ type: "open_chat", id: c.id });
    return b;
  }));
});

// ---------- artifacts: tiles with real previews; click opens in the default app
const KIND = { document: "Document", research: "Research", commit: "Claude Code", file: "File" };
wrap("artifacts", m => {
  const items = m.items || [];
  $("#grid").replaceChildren(...items.map(a => {
    const t = el("button", "tile" + (a.exists === false ? " gone" : "")), prev = el("div", "prev"), cap = el("div", "cap");
    t.type = "button"; t.title = a.path || a.title;
    if (a.kind === "commit") {
      const code = el("code");
      for (const line of String(a.preview || "").split("\n").slice(0, 7))
        code.append(el("span", line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : null, line + "\n"));
      prev.append(code);
    } else prev.textContent = String(a.preview || "").slice(0, 400);
    cap.append(el("div", "n", a.title), el("div", "k", a.exists === false ? "moved or deleted" : `${KIND[a.kind] || "File"} · ${when(a.ts)}`));
    t.append(prev, cap);
    t.onclick = () => send({ type: "open_item", kind: "artifact", id: a.id });   // the server checks and answers
    return t;
  }));
  if (!items.length) $("#grid").replaceChildren(el("div", "empty", "Documents, research and code JARVIS makes for you appear here."));
  $("#grid").style.display = items.length ? "" : "block";
});

// ---------- vault
const VICON = {
  note: ["rgba(255,196,107,.15)", "#FFC46B", '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h6"/></svg>'],
  link: ["rgba(63,200,255,.15)", "#3FC8FF", '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>'],
  file: ["rgba(255,94,168,.15)", "#FF5EA8", '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>'],
};
const X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>';
let vault = [];
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u || ""; } };
function renderVault() {
  const q = $("#vq").value.trim().toLowerCase();
  const items = vault.filter(v => !q || `${v.title} ${v.text}`.toLowerCase().includes(q));
  $("#vlist").replaceChildren(...items.map(v => {
    const [bg, fg, icon] = VICON[v.kind] || VICON.note, row = el("div", "item"), ic = el("div", "ic"), body = el("div"), x = el("button", "x");
    ic.style.background = bg; ic.style.color = fg; ic.append(svg(icon));
    const kind = v.kind === "link" ? `Link · ${host(v.url)}` : `${v.kind === "file" ? "File" : "Note"} · kept ${when(v.ts)}`;
    body.append(el("div", "n", v.title || v.text), el("div", "k", kind));
    x.type = "button"; x.title = "Remove (undo by saying “undo”)"; x.setAttribute("aria-label", "Remove " + (v.title || "item")); x.append(svg(X));
    x.onclick = e => { e.stopPropagation(); send({ type: "remove_vault", id: v.id }); };
    row.tabIndex = 0;
    // a note has no file or link: it opens in place
    row.onclick = () => {
      if (v.kind !== "note") return send({ type: "open_item", kind: "vault", id: v.id });
      const f = body.querySelector(".full");
      if (f) f.remove(); else body.append(el("div", "full", v.text));
    };
    row.onkeydown = e => { if (e.key === "Enter") row.onclick(); };
    row.append(ic, body, x); return row;
  }));
  if (!items.length) $("#vlist").append(el("div", "empty", q ? "Nothing in the vault matches." : "Your vault is empty."));
}
$("#vq").addEventListener("input", renderVault);
wrap("vault", m => { vault = m.items || []; renderVault(); });

// WhatsApp not linked: a row in the Vault footer that shows the QR
wrap("whatsapp", m => {
  const box = $("#walink");
  box.hidden = m.state === "linked";
  if (m.state === "linked") return box.replaceChildren();
  if (m.state === "qr" && m.qr?.startsWith("data:image/")) {
    const img = el("img"); img.src = m.qr; img.alt = "WhatsApp link QR code";
    box.replaceChildren(el("div", null, "Open WhatsApp on your phone → Linked devices → Link a device, and scan this."), img);
  } else {
    const b = el("button", "btn go wa", "Link WhatsApp"); b.type = "button";
    b.onclick = () => send({ type: "whatsapp_link" });
    box.replaceChildren(el("div", null, "WhatsApp isn't linked, sir. Link it to send from your own number."), b);
  }
});

// ---------- input bar
const text = $("#text");
text.addEventListener("focus", () => bridge?.focusable?.(true));
text.addEventListener("blur", () => bridge?.focusable?.(false));
text.addEventListener("keydown", e => {
  if (e.key !== "Enter" || !text.value.trim()) return;
  if (send({ type: "text_input", text: text.value })) text.value = "";
  else toast("Not connected to JARVIS yet. Your message is still in the box.");
});
micButton($("#pmic"));

// ---------- drag a file or text onto the island → "Keep in vault?"
const MAX_DROP = 10 * 1024 * 1024;   // the socket carries it as base64
const hasData = e => [...(e.dataTransfer?.types || [])].some(t => t === "Files" || t.startsWith("text/"));
addEventListener("dragover", e => { if (!hasData(e)) return; e.preventDefault(); shell.classList.add("drag"); bridge?.interactive?.(true); });
addEventListener("dragleave", e => { if (!e.relatedTarget) shell.classList.remove("drag"); });
addEventListener("drop", e => {
  if (!hasData(e)) return;
  e.preventDefault(); shell.classList.remove("drag");
  const file = e.dataTransfer.files[0], txt = !file && (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain"));
  if (file) {
    if (file.size > MAX_DROP) return notify({ icon: "info", title: "Too big to drop", text: `${file.name} is over 10 MB. Say “keep” with its path instead.` });
    askCard({ kind: "Vault", msg: ["Keep ", el("b", null, file.name), " in your vault?"], ok: "Keep", onAnswer: ok => {
      if (!ok) return;
      const r = new FileReader();
      r.onload = () => send({ type: "keep", name: file.name, data: String(r.result).split(",")[1] || "" });
      r.readAsDataURL(file);
    } });
  } else if (txt && txt.trim()) {
    const short = txt.trim().length > 90 ? txt.trim().slice(0, 90) + "…" : txt.trim();
    askCard({ kind: "Vault", msg: ["Keep ", el("i", null, `“${short}”`), " in your vault?"], ok: "Keep",
              onAnswer: ok => ok && send({ type: "keep", text: txt.trim() }) });
  }
});

// ---------- boot: the indicator starts under the selected tab
requestAnimationFrame(() => { const b = $("#seg button.on"); sp.seg.x = sp.seg.t = b.offsetLeft - 3; });
stat();
