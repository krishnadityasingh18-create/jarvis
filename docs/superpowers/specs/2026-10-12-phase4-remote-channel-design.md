# JARVIS Phase 4: The Away Channel (Vapi calls, n8n, WhatsApp) — Design

Status: self-approved under the user's standing permission (2026-10-12). Decisions in §8.

## 1. Goal

When the user is away from the desk, the same JARVIS — same memory, tools, plan and
reminders — answers on the phone (Vapi + Twilio) and over WhatsApp (their n8n workflows).
JARVIS can call the user (e.g. for a reminder they asked to be phoned about) and, only when
asked and confirmed, place a call or send a WhatsApp message to someone else.

## 2. Shape

```
Vapi phone call ──► tunnel ──► POST /v1/chat/completions  (OpenAI-compatible, streaming)
n8n (WhatsApp in) ─► tunnel ──► POST /api/message           (JSON in, JSON out)
                                     │  bearer token (Credential Manager "remote_token")
                                     ▼
                              brain.run_turn(channel="phone"/"whatsapp")
JARVIS ──► Vapi API: POST /call          (call_me / place_call)
JARVIS ──► n8n webhook: send WhatsApp     (send_whatsapp)
```

- **Vapi as a "Custom LLM":** Vapi handles speech; it sends the conversation to JARVIS's
  OpenAI-compatible endpoint and speaks what comes back. JARVIS takes the last user message
  as the turn text (its own history is the memory) and streams the spoken part back. The
  on-screen part is dropped for the phone.
- **n8n:** a webhook POSTs `{"text": "...", "from": "+91…"}` to `/api/message`; JARVIS
  replies `{"reply": "..."}` (spoken part + short detail). n8n sends it back on WhatsApp.
- **Auth:** every remote request needs `Authorization: Bearer <remote_token>` (random 32
  bytes, made by `python -m jarvis remote token`, stored in Credential Manager). Wrong or
  missing → 401. Rate limit 30 requests/minute. The desktop UI stays localhost-only.
- **Tunnel:** `python -m jarvis remote tunnel` runs a Cloudflare quick tunnel (cloudflared,
  free, no account) and prints the public URL to paste into Vapi/n8n. (ngrok's fixed free
  domain is the alternative once the user signs up.)

## 3. Safety on remote channels

- Remote turns can't show a confirmation card. Anything that needs confirmation is
  **declined** with "I'll need you at the desk to approve that, sir", unless it is in
  `[remote] allow = [...]` (empty by default).
- Remote turns are tainted from the start (the caller's words arrive over the internet,
  and the token could leak), so even normally-unconfirmed side effects are declined.
  Read-only tools (briefing, search, reading notes, web) work.
- `ask_claude` is never available remotely.

## 4. Outbound

| Tool | Does | Confirm |
|---|---|---|
| `call_me(reason)` | Vapi outbound call to the user's own number (`[remote] my_number`) | no (to the user) |
| `place_call(number, purpose)` | call someone else; the confirmation reads the number and purpose back | **yes** |
| `send_whatsapp(to, text)` | via the user's n8n webhook (`[remote] whatsapp_webhook`) | **yes** |

Reminders gain an optional "call me" form (`remind(..., call=true)`): at the time, JARVIS
speaks it at the desk and also calls the user's phone.

Secrets: `vapi_key` (private API key), `vapi_phone_id`, `n8n_token` — in Credential Manager
via `python -m jarvis keys set`. Numbers/URLs in `config.toml`.

## 5. Errors

Missing Vapi/n8n settings → the tool says exactly which `keys set`/config line is needed.
Vapi/n8n HTTP errors are reported, never retried automatically. Twilio trial: calls only
reach verified numbers — reported plainly.

## 6. Testing

Unit tests: bearer auth (missing/wrong/right), rate limit, OpenAI-compatible streaming shape
(SSE chunks, `[DONE]`), `/api/message` round trip with a fake turn runner, remote turns
decline confirm-required and side-effect tools, `ask_claude` hidden remotely, call_me /
place_call / send_whatsapp request shapes against a fake HTTP client, reminder with call.
Live: the endpoints answered from a local client with the token; a quick tunnel started.

## 7. Cost

JARVIS side ₹0 (cloudflared quick tunnels are free). Calls use Vapi/Twilio minutes — the
only paid part of the whole project; counted against the ₹300 monthly cap via a per-call
estimate (`[prices] vapi_inr_per_min`).

## 8. Decisions made under standing permission

1. Vapi uses JARVIS as a Custom LLM (one brain, one memory) instead of a separate assistant.
2. Remote channels are read-only by default; anything with a side effect needs the desk.
3. Cloudflare quick tunnel (no account) first; ngrok static domain later if wanted.
4. WhatsApp goes through the user's existing n8n workflow (a webhook), not a new service.
5. No outbound call or message without the user's explicit request + confirmation, except
   `call_me` to the user's own number.

## 9. Changes after review (2026-10-12)

- **Caller check:** the token only proves Vapi/n8n; requests must also come from the user's
  number (`my_number`, `allowed_numbers`). Unknown WhatsApp senders: 403, no turn. Unknown or
  missing Vapi caller: "I can't verify who's calling", no turn.
- **Remote is read-only:** every non-read-only tool is refused remotely (including plan,
  tasks, timetable), except names listed in `[remote] allow`. Private file tools are refused
  remotely while `content_private` is on.
- **Phone replies are fully spoken** (no screen part, no two-sentence cap).
- **Deferred (Rulings):** `remind(..., call=true)` and counting Vapi minutes against the
  budget — they need the user's real Vapi setup to test; tracked for after setup.
