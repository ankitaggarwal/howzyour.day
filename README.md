# HowzYourDay

A voice journaling companion on **Cartesia Line**. Call in, talk through your day, hang up — it remembers you next time. The caller's phone number is their identity; memory uses **Upstash Redis** (fast profile card) + **Mem0 Platform** (semantic recall), with the LLM served via **OpenRouter**.

## Files (flat and simple)

```
main.py       # the voice agent (Line SDK 0.2.10: AgentClass + LlmAgent)
memory.py     # all memory: profile store + semantic (mem0) + orchestrator
prompts.py    # system prompt, greetings, extraction prompt
config.py     # settings (env)
tests/test_memory.py   # live verification of the memory layer
AGENTS.md     # Cartesia Line SDK reference (how the SDK is used here)
```

## How it works

- **On a call** (`get_agent`): resolve the caller's phone → load their memory → build a `TodayAgent` (one `LlmAgent`) with a personalized system prompt and greeting.
- **During the call**: a background `recall_memory` tool does on-demand semantic search when the caller asks about the past ("remember when…"), masked by a filler line.
- **On hang-up** (`CallEnded`): extract structured info from the transcript and persist it.

## Memory — two backends, one identity

Everything is keyed by the caller's E.164 phone — no cross-user read path.

- **Profile** (fast, latency-critical): one JSON card per caller in **Upstash Redis**, fetched **by key** (no scan), with a per-worker in-process cache. Holds identity, key facts, entities (people/places/projects/pets), open threads, mood trend, and inferred activity signals.
- **Semantic** (deep, on-demand): **Mem0 Platform** (hosted). We send raw turns; Mem0 enriches and embeds them server-side. Used mid-call ("remember when…") and written post-call.

**Saved:** identity, distinctive facts, entities, open threads, mood. **Skipped:** small talk, filler, one-off logistics, anything about other people. (Rules in `prompts.py`.)

## Setup

```bash
cp .env.example .env   # fill in keys
uv sync
```

Required env: `OPENROUTER_API_KEY` (model auto-resolves to `openrouter/google/gemini-2.5-flash`; pin a different one with `MODEL`), `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` (the per-database REST token), and `MEM0_API_KEY`.

## Run & test

```bash
PYTHONPATH=. python3 tests/test_memory.py    # verify memory against live Upstash + Mem0
OPENROUTER_API_KEY=... uv run python main.py # run the agent locally
```

Deploys automatically when pushed to the connected Cartesia repo.

## Web companion

A browser version of the same agent (sign in by email, talk from the page —
no phone call) lives in [`web/`](web/). It reuses the same agent and memory;
web users are keyed by `web:<hash(email)>` instead of a phone number.

Deploy it on a single DigitalOcean droplet behind automatic HTTPS — see
[DEPLOY.md](DEPLOY.md).

## License

MIT — see [LICENSE](LICENSE).
