import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { createServer } from "../helper.mjs";

const TOKEN = "t0k3n";

function fakeSock(send) {
  const ev = new EventEmitter();
  const sent = [];
  return { ev, sent, sendMessage: async (jid, content) => { sent.push([jid, content]); return send(jid, content); } };
}

async function start(sock) {
  const api = createServer({ sock, token: TOKEN });
  const port = await api.listen(0);
  const get = (path, token = TOKEN) =>
    fetch(`http://127.0.0.1:${port}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} }).then((r) => r.json().then((j) => [r.status, j]));
  const post = (path, body) =>
    fetch(`http://127.0.0.1:${port}${path}`, { method: "POST", headers: { Authorization: `Bearer ${TOKEN}` }, body: JSON.stringify(body) }).then((r) => r.json());
  return { api, get, post, port };
}

test("401 without or with a wrong token", async () => {
  const { api, get } = await start(fakeSock(async () => ({})));
  assert.equal((await get("/status", null))[0], 401);
  assert.equal((await get("/status", "nope"))[0], 401);
  assert.equal((await get("/status"))[0], 200);
  api.close();
});

test("status follows the connection events, qr as a data URL", async () => {
  const sock = fakeSock(async () => ({}));
  const { api, get } = await start(sock);
  assert.deepEqual((await get("/status"))[1], { state: "unlinked", qr: null });
  sock.ev.emit("connection.update", { qr: "2@abc" });
  await api.idle();
  const [, s] = await get("/status");
  assert.equal(s.state, "qr");
  assert.match(s.qr, /^data:image\/png;base64,/);
  sock.ev.emit("connection.update", { connection: "open" });
  assert.deepEqual((await get("/status"))[1], { state: "linked", qr: null });
  api.close();
});

test("contacts come from contacts.upsert and pushNames", async () => {
  const sock = fakeSock(async () => ({}));
  const { api, get } = await start(sock);
  sock.ev.emit("contacts.upsert", [{ id: "911@s.whatsapp.net", name: "Rahul Sharma" }, { id: "123@g.us", name: "Rahul group" }]);
  sock.ev.emit("contacts.update", [{ id: "912@s.whatsapp.net", notify: "Rahul K" }]);
  const [, r] = await get("/contacts?q=rahul");
  assert.deepEqual(r.matches.map((m) => [m.name, m.number]).sort(), [["Rahul K", "+912"], ["Rahul Sharma", "+911"]]);
  api.close();
});

test("/send answers ok only after the server ack", async () => {
  let ack;
  const sock = fakeSock(() => new Promise((res) => { ack = () => res({ key: { id: "ABC" } }); }));
  const { api, post } = await start(sock);
  sock.ev.emit("connection.update", { connection: "open" });
  let done = false;
  const p = post("/send", { to: "+919876543210", text: "hi" }).then((r) => { done = true; return r; });
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(sock.sent.length, 1);
  assert.equal(sock.sent[0][0], "919876543210@s.whatsapp.net");
  assert.equal(done, false);
  ack();
  assert.deepEqual(await p, { ok: true, id: "ABC" });
  api.close();
});

test("/send reports a failed send, and refuses when unlinked or bad number", async () => {
  const sock = fakeSock(async () => { throw new Error("Connection Closed"); });
  const { api, post } = await start(sock);
  assert.equal((await post("/send", { to: "+919876543210", text: "hi" })).ok, false);   // unlinked
  assert.equal(sock.sent.length, 0);
  sock.ev.emit("connection.update", { connection: "open" });
  const r = await post("/send", { to: "+919876543210", text: "hi" });
  assert.deepEqual(r, { ok: false, error: "Connection Closed" });
  assert.equal((await post("/send", { to: "rahul", text: "hi" })).ok, false);
  assert.equal((await post("/send", { to: "+919876543210", text: "" })).ok, false);
  api.close();
});

test("replies: only from chats sent to, none from strangers", async () => {
  const sock = fakeSock(async () => ({ key: { id: "X" } }));
  const { api, get, post } = await start(sock);
  sock.ev.emit("connection.update", { connection: "open" });
  await post("/send", { to: "+919000000001", text: "hi" });
  const msg = (jid, text, fromMe = false) => ({ key: { remoteJid: jid, fromMe }, pushName: "N", message: { conversation: text } });
  sock.ev.emit("messages.upsert", { type: "notify", messages: [msg("919000000001@s.whatsapp.net", "hello back"), msg("999@s.whatsapp.net", "spam"), msg("919000000001@s.whatsapp.net", "mine", true)] });
  const [, r] = await get("/replies?since=2000-01-01T00:00:00Z");
  assert.equal(r.messages.length, 1);
  assert.equal(r.messages[0].from, "+919000000001");
  assert.equal(r.messages[0].text, "hello back");
  const [, none] = await get("/replies?since=2999-01-01T00:00:00Z");
  assert.deepEqual(none.messages, []);
  api.close();
});
