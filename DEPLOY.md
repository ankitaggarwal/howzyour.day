# Deploying HowzYourDay (DigitalOcean)

There are two independent pieces:

1. **The voice agent** — deployed to **Cartesia**. You connect this GitHub repo
   to Cartesia and it auto-deploys `main.py` on push. Its secrets
   (`OPENROUTER_API_KEY`, Upstash, Mem0) are set in the Cartesia dashboard. The
   droplet below is **not** involved in the phone agent.

2. **The web companion** (`web/`) — the browser version of the same agent. That
   is what we deploy on a DigitalOcean droplet here, behind `howzyour.day`.

The web companion runs in two small containers on one droplet:

| Container | What it does |
|-----------|--------------|
| `web`   | The FastAPI app — email sign-in, the voice UI, and the `/ws` proxy to Cartesia |
| `caddy` | Reverse proxy that gives the site free automatic HTTPS |

Everything stateful (Cartesia, OpenRouter, Redis, Mem0, SMTP) lives elsewhere
and is configured through `web/backend/.env`. Nothing runs a database here.

---

## One-time server setup

SSH into the droplet (`ssh root@YOUR_SERVER_IP`), then:

```bash
# 1. Install Docker (official convenience script)
curl -fsSL https://get.docker.com | sh

# 2. Add 2 GB of swap (small droplets have ~1 GB RAM; this prevents crashes)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 3. Open the firewall for web traffic + SSH (if ufw is enabled)
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable

# 4. Get the code (public repo)
git clone https://github.com/ankitaggarwal/howzyour.day.git
cd howzyour.day
```

## Add the secrets (never committed to git)

Create `web/backend/.env` on the server with your real values:

```bash
nano web/backend/.env
```

```
# Cartesia (same account as the deployed agent)
CARTESIA_API_KEY=sk_car_...
AGENT_ID=agent_...
FROM_NUMBER_ID=ap_...            # only needed for the outbound "call me" button

# Public URL + session
BASE_URL=https://howzyour.day
SESSION_SECRET=<a long random string, e.g. `openssl rand -hex 32`>

# Magic-link sign-in email (SMTP)
SMTP_HOST=smtp.fastmail.com
SMTP_USER=you@yourdomain.com
SMTP_PASS=your_app_specific_password
```

(See `web/.env.example` for the full list, including optional knobs.)

## Point the domain at the server

In your DNS provider, add an **A record**: `howzyour.day -> YOUR_SERVER_IP`.
Caddy fetches the HTTPS certificate automatically once DNS has propagated.
The domain is already set in the `Caddyfile`.

## Start everything

```bash
docker compose up -d --build
```

Check it's healthy:

```bash
docker compose ps         # both should be "running"
docker compose logs -f    # follow logs (Ctrl-C to stop following)
```

Visit **https://howzyour.day** — sign in by email, press space, and talk.

---

## Deploying updates later

Push your changes to GitHub, then on the server:

```bash
cd ~/howzyour.day
./deploy.sh        # git pull + rebuild + restart
```

## Handy commands

```bash
docker compose ps              # what's running
docker compose logs -f web     # app logs
docker compose restart web     # restart just the app
docker compose down            # stop everything
docker compose up -d           # start everything (no rebuild)
```
