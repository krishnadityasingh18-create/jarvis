// JARVIS WhatsApp helper: a token-protected localhost API over a Baileys socket (unofficial
// WhatsApp Web protocol; sends from the user's own linked number).
// Run: node helper.mjs --data <dir>   -> writes <dir>/wa/port, keeps the session in <dir>/wa_auth
import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import QRCode from "qrcode";
import { match } from "./contacts.mjs";

const DAY = 24 * 3600 * 1000;
const E164 = /^\+\d{8,15}$/;

export function createServer({ sock, token }) {
  let state = "unlinked", qr = null, pending = Promise.resolve();
  const contacts = new Map();           // jid -> {name, jid, number}
  const sentTo = new Map();             // jid -> ms of the last send
  let replies = [];                     // memory only
  let server, cur = null;           // cur: the live socket (replaced on reconnect)

  const person = (jid) => "+" + jid.split("@")[0].split(":")[0];
  const upsert = (c) => {
    if (!c?.id?.endsWith("@s.whatsapp.net")) return;
    const name = c.name || c.notify || c.verifiedName || contacts.get(c.id)?.name;
    if (name) contacts.set(c.id, { name, jid: c.id, number: person(c.id) });
  };

  function attach(s) {
    cur = s;
    s.ev.on("contacts.upsert", (l) => l.forEach(upsert));
    s.ev.on("contacts.update", (l) => l.forEach(upsert));
    s.ev.on("connection.update", (u) => {
      if (u.qr) pending = QRCode.toDataURL(u.qr).then((url) => { state = "qr"; qr = url; });
      if (u.connection === "open") { state = "linked"; qr = null; }
      if (u.connection === "close") { state = "unlinked"; qr = null; }
    });
    s.ev.on("messages.upsert", ({ messages }) => {
      for (const m of messages ?? []) {
        const jid = m.key?.remoteJid;
        const text = m.message?.conversation || m.message?.extendedTextMessage?.text;
        if (m.key?.fromMe || !jid || !text || Date.now() - (sentTo.get(jid) ?? 0) > DAY) continue;
        if (m.pushName) upsert({ id: jid, notify: m.pushName });
        replies.push({ from: person(jid), name: contacts.get(jid)?.name ?? m.pushName ?? "", text, ts: new Date().toISOString() });
      }
    });
  }
  if (sock) attach(sock);

  async function send(body) {
    if (state !== "linked") return { ok: false, error: "WhatsApp isn't linked" };
    if (!E164.test(body.to ?? "")) return { ok: false, error: "that isn't a number with its country code" };
    if (!body.text || typeof body.text !== "string") return { ok: false, error: "no message text" };
    const jid = body.to.slice(1) + "@s.whatsapp.net";
    try {
      const r = await cur.sendMessage(jid, { text: body.text });   // resolves on the server ack
      sentTo.set(jid, Date.now());
      return { ok: true, id: r?.key?.id ?? "" };
    } catch (e) {
      return { ok: false, error: String(e?.message ?? e) };
    }
  }

  async function handle(req, res) {
    const auth = Buffer.from(req.headers.authorization ?? "");
    const want = Buffer.from(`Bearer ${token}`);
    const out = (code, obj) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(obj)); };
    if (auth.length !== want.length || !crypto.timingSafeEqual(auth, want)) return out(401, { error: "unauthorized" });
    const url = new URL(req.url, "http://x");
    if (req.method === "GET" && url.pathname === "/status") { await pending; return out(200, { state, qr }); }
    if (req.method === "GET" && url.pathname === "/contacts")
      return out(200, { matches: match([...contacts.values()], url.searchParams.get("q")) });
    if (req.method === "GET" && url.pathname === "/replies") {
      const since = Date.parse(url.searchParams.get("since") ?? "") || 0;
      replies = replies.filter((r) => Date.now() - Date.parse(r.ts) < DAY);
      return out(200, { messages: replies.filter((r) => Date.parse(r.ts) > since) });
    }
    if (req.method === "POST" && url.pathname === "/send") {
      let raw = "";
      for await (const c of req) { raw += c; if (raw.length > 20000) return out(413, { ok: false, error: "too big" }); }
      let body; try { body = JSON.parse(raw); } catch { return out(400, { ok: false, error: "bad JSON" }); }
      return out(200, await send(body));
    }
    out(404, { error: "not found" });
  }

  server = http.createServer((req, res) => handle(req, res).catch((e) => { res.writeHead(500); res.end(String(e)); }));
  return {
    server, attach,
    idle: () => pending,
    listen: (port = 0) => new Promise((ok) => server.listen(port, "127.0.0.1", () => ok(server.address().port))),
    close: () => { server.close(); server.closeAllConnections(); },
  };
}

async function main() {
  const i = process.argv.indexOf("--data");
  if (i < 0 || !process.argv[i + 1]) throw new Error("usage: node helper.mjs --data <dir>");
  const dir = path.resolve(process.argv[i + 1]);
  const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } =
    await import("@whiskeysockets/baileys");
  const { state: auth, saveCreds } = await useMultiFileAuthState(path.join(dir, "wa_auth"));
  const { version } = await fetchLatestBaileysVersion().catch(() => ({}));
  const token = crypto.randomBytes(32).toString("hex");
  const api = createServer({ sock: null, token });

  const connect = () => {
    const sock = makeWASocket({ auth, version, printQRInTerminal: false, syncFullHistory: false });
    api.attach(sock);
    sock.ev.on("creds.update", saveCreds);
    sock.ev.on("connection.update", ({ connection, lastDisconnect }) => {
      if (connection !== "close") return;
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        fs.rmSync(path.join(dir, "wa_auth"), { recursive: true, force: true });   // a stale session would never relink
      }
      setTimeout(connect, 3000);   // reconnects; after a logout this shows a fresh QR
    });
  };
  connect();
  const port = await api.listen(0);
  fs.mkdirSync(path.join(dir, "wa"), { recursive: true });
  fs.writeFileSync(path.join(dir, "wa", "port"), JSON.stringify({ port, token }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((e) => { console.error(e); process.exit(1); });
}
