# HowzYourDay — Web Companion

Talk to the HowzYourDay agent **from the browser** — no phone, no
international call. Enter your email, click the one-tap link we send you, press
space, and speak. It's the same agent and the same memory (Redis + Mem0) as the
phone line; web users are simply keyed by `web:<hash(email)>` instead of a phone
number.

```
web/
  backend/   FastAPI: magic-link email sign-in + session, a WebSocket proxy to
             Cartesia, and outbound "call me"
  frontend/  single-page voice UI (Tailwind CDN + vanilla JS + Web Audio),
             no build step
```

## How it works

```
browser mic ──pcm_44100──▶ /ws (FastAPI proxy) ──▶ wss://api.cartesia.ai/agents/stream/<agent>
   ▲  speaker ◀──media_output──┘  (proxy mints a short-lived access token,        │
                                   injects metadata.from = web:<hash>)            │
                                            agent's get_agent reads metadata ─────┘
                                            → loads this user's Redis/Mem0 memory
```

The Cartesia secret key never reaches the browser — the browser is
authenticated to our proxy by a signed session cookie, and the proxy mints a
short-lived access token for each call. The agent reads `metadata.from`, so a
web user resolves to the same memory record the phone line would use.

## Setup

1. **Env** — `cp .env.example backend/.env` and fill in `CARTESIA_API_KEY`,
   `AGENT_ID`, `FROM_NUMBER_ID`, a random `SESSION_SECRET`, and your SMTP
   details for the sign-in email (`SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`).

2. **Run**
   ```bash
   cd web/backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python app.py            # serves http://localhost:8787
   ```

3. Open `http://localhost:8787`, sign in by email, press **space**, and talk.

## Notes

- `.env` is gitignored — never commit secrets.
- Deploy to any host that supports WebSockets (a `Dockerfile` and `fly.toml`
  are included); set `BASE_URL` to the public URL.
