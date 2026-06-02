"""
HowzYourDay - web companion backend.

A thin FastAPI app that lets a signed-in user talk to the SAME Cartesia agent
from the browser (no phone, no international call). It does four things:

  1. Magic-link email sign-in -> a 15-day session cookie (the web user's identity).
  2. /ws             -> WebSocket proxy: browser <-> Cartesia agent stream.
                        Mints a short-lived Cartesia access token (secret key
                        never reaches the browser) and injects the user's
                        identity as metadata.from = "web:<hash>", which the
                        agent reads in get_agent -> same Redis/Mem0 memory.
  3. /api/get-call   -> outbound: Cartesia calls the user's phone number.
  4. /               -> the landing page (dial the number, or start on the web).
     /talk           -> the single-page voice UI (sign in, then talk).

Identity parity: phone callers are keyed by E.164; web users by web:<hash(email)>.
Both flow through the agent's existing memory layer unchanged.
"""

import asyncio
import hashlib
import json
import os
import smtplib
import ssl
from contextlib import suppress
from email.message import EmailMessage

import httpx
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from itsdangerous import SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name) or default


# --- Cartesia ---
CARTESIA_API_KEY = _env("CARTESIA_API_KEY")
AGENT_ID = _env("AGENT_ID")
FROM_NUMBER_ID = _env("FROM_NUMBER_ID")          # for outbound "get a call"
CARTESIA_API_VERSION = _env("CARTESIA_API_VERSION", "2026-03-01")
CARTESIA_WS_VERSION = _env("CARTESIA_WS_VERSION", "2025-04-16")
CARTESIA_BASE = "https://api.cartesia.ai"

# --- Auth / session ---
SESSION_SECRET = _env("SESSION_SECRET", "dev-only-change-me")
BASE_URL = _env("BASE_URL", "http://localhost:8787")
SESSION_DAYS = int(_env("SESSION_DAYS", "15"))

# --- Magic-link email (Fastmail SMTP) ---
SMTP_HOST = _env("SMTP_HOST", "smtp.fastmail.com")
SMTP_PORT = int(_env("SMTP_PORT", "465"))
SMTP_USER = _env("SMTP_USER")                   # Fastmail address (SMTP login)
SMTP_PASS = _env("SMTP_PASS")                   # Fastmail app-specific password
MAGIC_FROM = _env("MAGIC_FROM") or SMTP_USER    # sender address
MAGIC_MAX_AGE = int(_env("MAGIC_MAX_AGE", "1800"))   # link valid 30 minutes
EMAIL_CONFIGURED = bool(SMTP_USER and SMTP_PASS)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = FastAPI(title="HowzYourDay Web")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=SESSION_DAYS * 24 * 3600,
    same_site="lax",
    https_only=BASE_URL.startswith("https"),
)

_signer = URLSafeTimedSerializer(SESSION_SECRET, salt="howzyourday-magic-link")


def web_user_id(email: str) -> str:
    """Stable, short, non-reversible id from the email - the web counterpart to E.164."""
    return "web:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def _magic_email_html(link: str) -> str:
    return f"""\
<!doctype html><html><body style="margin:0;background:#15100e;">
  <div style="max-width:440px;margin:0 auto;padding:44px 28px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;">
    <div style="height:44px;width:44px;border-radius:50%;background:linear-gradient(135deg,#f3c07a,#e58e76);margin-bottom:24px;"></div>
    <h1 style="font-family:Georgia,'Times New Roman',serif;color:#f3ebe1;font-size:30px;font-weight:400;margin:0 0 10px;">HowzYour<span style="color:#e89a52;">Day</span></h1>
    <p style="color:#b6a597;font-size:15px;line-height:1.6;margin:0 0 28px;">Tap below to come in and talk through your day. This link works once and expires in 30 minutes.</p>
    <a href="{link}" style="display:inline-block;background:linear-gradient(180deg,#f3c07a,#e89a52);color:#231712;text-decoration:none;font-weight:600;font-size:15px;padding:14px 30px;border-radius:999px;">Start talking</a>
    <p style="color:#7c6c5f;font-size:12px;line-height:1.6;margin:30px 0 0;">If you didn't ask for this, you can safely ignore it.</p>
  </div>
</body></html>"""


def _send_magic_link(to_email: str, link: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Your HowzYourDay sign-in link"
    msg["From"] = MAGIC_FROM
    msg["To"] = to_email
    msg.set_content(f"Tap to come in and talk through your day:\n\n{link}\n\nThis link works once and expires in 30 minutes.")
    msg.add_alternative(_magic_email_html(link), subtype="html")
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=15) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


# =============================================================================
# Auth
# =============================================================================

@app.post("/auth/request")
async def auth_request(request: Request):
    """Email the caller a one-tap, single-use sign-in link."""
    if not EMAIL_CONFIGURED:
        raise HTTPException(503, "Email sign-in isn't set up yet.")
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(400, "Please enter a valid email.")
    link = f"{BASE_URL}/auth/verify?token={_signer.dumps(email)}"
    try:
        await asyncio.to_thread(_send_magic_link, email, link)
    except Exception as e:
        raise HTTPException(502, f"Couldn't send the email right now: {e}")
    return {"ok": True}


@app.get("/auth/verify")
def auth_verify(request: Request, token: str = ""):
    """Consume a magic link and start the 15-day session."""
    try:
        email = _signer.loads(token, max_age=MAGIC_MAX_AGE)
    except SignatureExpired:
        return RedirectResponse("/talk?error=expired")
    except Exception:
        return RedirectResponse("/talk?error=link")
    request.session["user"] = {"email": email, "name": email.split("@")[0]}
    return RedirectResponse("/talk")


@app.get("/api/me")
def me(request: Request):
    user = request.session.get("user")
    return {"authenticated": bool(user), "user": user, "email_configured": EMAIL_CONFIGURED}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


# =============================================================================
# Cartesia helpers
# =============================================================================

async def mint_access_token() -> str:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"{CARTESIA_BASE}/access-token",
            headers={"Authorization": f"Bearer {CARTESIA_API_KEY}",
                     "Cartesia-Version": CARTESIA_API_VERSION},
            json={"grants": {"agent": True}, "expires_in": 3600},
        )
        r.raise_for_status()
        return r.json()["token"]


# =============================================================================
# WebSocket proxy: browser <-> Cartesia agent
# =============================================================================

@app.websocket("/ws")
async def ws_proxy(client: WebSocket):
    user = client.session.get("user") if "session" in client.scope else None
    if not user or not user.get("email"):
        await client.close(code=4401)  # unauthenticated
        return
    await client.accept()

    uid = web_user_id(user["email"])
    name = user.get("name")
    try:
        token = await mint_access_token()
    except Exception as e:
        await client.send_text(json.dumps({"event": "error", "message": f"token: {e}"}))
        await client.close(code=1011)
        return

    uri = f"{CARTESIA_BASE.replace('https', 'wss')}/agents/stream/{AGENT_ID}"
    headers = {"Authorization": f"Bearer {token}", "Cartesia-Version": CARTESIA_WS_VERSION}

    try:
        try:
            upstream = await websockets.connect(uri, additional_headers=headers, max_size=None)
        except TypeError:  # older websockets used extra_headers
            upstream = await websockets.connect(uri, extra_headers=headers, max_size=None)
    except Exception as e:
        await client.send_text(json.dumps({"event": "error", "message": f"connect: {e}"}))
        await client.close(code=1011)
        return

    async def client_to_upstream():
        try:
            while True:
                raw = await client.receive_text()
                try:
                    data = json.loads(raw)
                except Exception:
                    await upstream.send(raw)
                    continue
                # Inject the signed-in user's identity on the opening start event.
                if data.get("event") == "start":
                    data.setdefault("config", {}).setdefault("input_format", "pcm_44100")
                    data["metadata"] = {"from": uid, "name": name}
                    raw = json.dumps(data)
                await upstream.send(raw)
        except WebSocketDisconnect:
            pass
        finally:
            with suppress(Exception):
                await upstream.close()

    async def upstream_to_client():
        try:
            async for msg in upstream:
                await client.send_text(msg if isinstance(msg, str) else msg.decode())
        except Exception:
            pass
        finally:
            with suppress(Exception):
                await client.close()

    await asyncio.gather(client_to_upstream(), upstream_to_client())


# =============================================================================
# Outbound: "get a call" (Cartesia calls the user's phone number)
# =============================================================================

@app.post("/api/get-call")
async def get_call(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "sign in first")
    body = await request.json()
    to_number = (body.get("to_number") or "").strip()
    if not to_number.startswith("+"):
        raise HTTPException(400, "to_number must be E.164, e.g. +15551234567")
    if not FROM_NUMBER_ID:
        raise HTTPException(500, "FROM_NUMBER_ID not configured")

    payload = {
        "from_number_id": FROM_NUMBER_ID,
        "agent_id": AGENT_ID,
        "outbound_calls": [
            {"to_number": to_number,
             "metadata": {"from": web_user_id(user["email"]), "name": user.get("name")}}
        ],
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{CARTESIA_BASE}/agents/calls",
            headers={"X-API-Key": CARTESIA_API_KEY, "Cartesia-Version": CARTESIA_API_VERSION},
            json=payload,
        )
    # Cartesia returns plain-text on validation errors, so never assume JSON.
    try:
        data = r.json()
    except Exception:
        data = {"detail": (r.text or "").strip() or f"Cartesia HTTP {r.status_code}"}
    return JSONResponse(data, status_code=r.status_code)


# =============================================================================
# Static frontend
# =============================================================================

@app.get("/")
def home():
    """The landing page: dial the number, or start on the web."""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/talk")
def talk():
    """The voice app: email sign-in, then talk from the browser."""
    return FileResponse(os.path.join(FRONTEND_DIR, "talk.html"))


if __name__ == "__main__":
    import uvicorn

    port = int(_env("PORT", "8787"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
