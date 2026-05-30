"""
HowzYourDay — settings.

Secrets come from the environment (no hardcoded keys). For local runs put them
in a .env (gitignored); for deploy set them in the Cartesia dashboard's env.

Backends:
  - LLM      : OpenRouter via LiteLLM (model = openrouter/<slug>), key OPENROUTER_API_KEY.
  - Hot store: Upstash Redis (REST) — the fast per-caller profile card.
  - Semantic : Mem0 Platform (hosted) — conversational long-term memory.
"""

import os

from dotenv import load_dotenv
from loguru import logger

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name) or default


# --- LLM via OpenRouter ------------------------------------------------------
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_API_BASE = _get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")

# Default is verified-available on OpenRouter (May 2026). resolve_model() may
# upgrade/downgrade this at startup based on what the account can actually reach.
DEFAULT_MODEL = "openrouter/google/gemini-2.5-flash"
MODEL = _get("MODEL", DEFAULT_MODEL)

# Preference order (best first). Bare OpenRouter slugs; the "openrouter/" prefix
# is added by resolve_model(). We pick a fast Flash model for low latency, and
# fall back to "flash-latest" so we degrade gracefully if a pinned one is retired.
MODEL_PREFERENCES = [
    "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-preview-09-2025",
    "google/gemini-flash-latest",
]

# --- Upstash Redis (REST) ----------------------------------------------------
UPSTASH_REDIS_REST_URL = _get("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = _get("UPSTASH_REDIS_REST_TOKEN")
# Hot card is "fast", not "forgetful": long TTL so weekly callers keep context,
# refreshed on every write. Doubles as a GDPR-friendly auto-expiry. 90 days.
REDIS_TTL_SECONDS = int(_get("REDIS_TTL_SECONDS", str(90 * 24 * 3600)))
REDIS_KEY_PREFIX = _get("REDIS_KEY_PREFIX", "profile:")

# --- Mem0 Platform (hosted) --------------------------------------------------
MEM0_API_KEY = _get("MEM0_API_KEY")

# --- Profile sizing ----------------------------------------------------------
MAX_KEY_FACTS = 15
MAX_LAST_CALLS = 3

# Expose the OpenRouter key to LiteLLM (which reads provider keys from the env).
if OPENROUTER_API_KEY:
    os.environ.setdefault("OPENROUTER_API_KEY", OPENROUTER_API_KEY)


def reasoning_off_kwargs() -> dict:
    """LlmConfig kwargs that disable the model's 'thinking' to cut time-to-first-token.

    Gemini 2.5 Flash is a hybrid-reasoning model; Line defaults reasoning_effort to
    "low" (a 1024-token thinking budget every turn), which adds a silent pause before
    the first spoken word — bad for a voice loop. We don't need reasoning for casual
    conversation, so we turn it off.

    OpenRouter doesn't accept litellm's `reasoning_effort`, so we pass OpenRouter's
    native reasoning control as a body param. Direct providers (e.g. gemini/...) use
    the first-class `reasoning_effort="none"`.
    """
    if MODEL.startswith("openrouter/"):
        return {"extra": {"extra_body": {"reasoning": {"max_tokens": 0}}}}
    return {"reasoning_effort": "none"}


def resolve_model(timeout: float = 2.0) -> str:
    """Pick the best available model from OpenRouter's live catalog.

    Queries the public models list and returns the first MODEL_PREFERENCES entry
    that exists, prefixed for LiteLLM. Any failure falls back to MODEL so a slow
    or down catalog never blocks startup.
    """
    try:
        import httpx

        r = httpx.get(f"{OPENROUTER_API_BASE}/models", timeout=timeout)
        r.raise_for_status()
        available = {m["id"] for m in r.json().get("data", [])}
        for slug in MODEL_PREFERENCES:
            if slug in available:
                chosen = f"openrouter/{slug}"
                logger.info(f"OpenRouter model resolved: {chosen}")
                return chosen
        logger.warning("No preferred model available on OpenRouter; using default")
    except Exception as e:
        logger.warning(f"OpenRouter model listing failed ({e}); using default {MODEL}")
    return MODEL
