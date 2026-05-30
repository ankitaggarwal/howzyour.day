"""
HowzYourDay — memory.

Two backends, one identity (the E.164 phone as user_id):

  - PROFILE  : a rich structured card per caller, stored as ONE JSON value in
               Upstash Redis (fetched by key — no scan, no search). Fronted by a
               per-worker in-process cache. This is the latency-critical path.
  - SEMANTIC : Mem0 Platform (hosted). We send raw conversation turns; Mem0's
               own LLM extracts/embeds/stores them. Searched on demand mid-call
               ("remember when...") and written post-call.

Public API (async, used by main.py):
  load_context(phone)            -> dict for the system prompt
  save_call(phone, messages, extracted)
  search_relevant(phone, query)  -> str
  delete_user(phone)             -> wipes everything for that user
  normalize_identity(phone)         -> "+digits"
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger

import config
import prompts

_redis = None
_mem0 = None

# In-process profile cache (per worker; zero network hop). Upstash is fast but
# still one HTTPS round-trip; a repeat read inside the same hot worker is 0ms.
# Upstash is the shared source of truth; this dict is the zero-hop fast lane.
# Short TTL bounds cross-worker staleness; save_profile/_delete_profile keep it consistent.
_PROFILE_CACHE_TTL = 120  # seconds
_profile_cache: dict = {}  # user_id -> (expiry_monotonic, profile_json)


def _cache_get(user_id: str) -> Optional[dict]:
    entry = _profile_cache.get(user_id)
    if not entry:
        return None
    expiry, payload = entry
    if time.monotonic() >= expiry:
        _profile_cache.pop(user_id, None)
        return None
    return json.loads(payload)  # fresh copy, no aliasing of the cached object


def _cache_set(user_id: str, profile: dict) -> None:
    _profile_cache[user_id] = (time.monotonic() + _PROFILE_CACHE_TTL, json.dumps(profile))


def _cache_del(user_id: str) -> None:
    _profile_cache.pop(user_id, None)


# =============================================================================
# Identity
# =============================================================================

def normalize_phone(phone: str) -> str:
    """E.164: a single leading '+' followed by digits only."""
    if not phone:
        return "+unknown"
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "+" + digits if digits else "+unknown"


def normalize_identity(raw: str) -> str:
    """Resolve a caller identity to a storage id.

    Phone callers are E.164-normalized; web users arrive already prefixed
    ("web:<hash>", set by the web backend from the signed-in email) and pass
    through untouched. Idempotent, so re-normalizing a stored id is safe.
    """
    if not raw:
        return "+unknown"
    if raw.startswith("web:"):
        return raw
    return normalize_phone(raw)


def _key(phone: str) -> str:
    return config.REDIS_KEY_PREFIX + normalize_identity(phone)


# =============================================================================
# Clients
# =============================================================================

def _redis_client():
    global _redis
    if _redis is None:
        from upstash_redis import Redis
        _redis = Redis(url=config.UPSTASH_REDIS_REST_URL, token=config.UPSTASH_REDIS_REST_TOKEN)
    return _redis


def _mem0_client():
    """Hosted Mem0 Platform client (extraction + embeddings + store are server-side)."""
    global _mem0
    if _mem0 is None:
        from mem0 import MemoryClient
        _mem0 = MemoryClient(api_key=config.MEM0_API_KEY)
        logger.info("mem0 Platform client ready")
    return _mem0


# =============================================================================
# Profile (fast structured record — Upstash Redis)
# =============================================================================

def empty_profile(phone: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "user_id": normalize_identity(phone),
        "name": None, "location": None, "occupation": None,
        "key_facts": [], "open_threads": [],
        "entities": {},                # name -> {kind, relation, note, mentions, last_seen}
        "last_calls": [], "mood_history": [],
        "inferred": {"calls_by_weekday": {}, "calls_by_hour_utc": {},
                     "avg_days_between_calls": None, "sensitive": False},
        "total_calls": 0, "last_call_date": None, "last_call_ts": None,
        "first_call": now, "updated_at": now,
    }


def get_profile(phone: str) -> Optional[dict]:
    user_id = normalize_identity(phone)
    local = _cache_get(user_id)
    if local is not None:
        return local
    raw = _redis_client().get(_key(phone))
    if not raw:
        return None
    profile = json.loads(raw) if isinstance(raw, str) else raw
    _cache_set(user_id, profile)
    return profile


def save_profile(phone: str, profile: dict) -> None:
    user_id = normalize_identity(phone)
    profile["user_id"] = user_id
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    _redis_client().set(_key(phone), json.dumps(profile), ex=config.REDIS_TTL_SECONDS)
    _cache_set(user_id, profile)


def _delete_profile(phone: str) -> None:
    _redis_client().delete(_key(phone))
    _cache_del(normalize_identity(phone))


def _merge_entities(profile: dict, entities: list, call_date: str) -> None:
    store = profile.setdefault("entities", {})
    for e in entities or []:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        rec = store.get(name.lower(), {"name": name, "mentions": 0})
        rec["mentions"] = rec.get("mentions", 0) + 1
        rec["last_seen"] = call_date
        for f in ("kind", "relation", "note"):
            if e.get(f):
                rec[f] = e[f]
        store[name.lower()] = rec


def _update_inferred(profile: dict, now: datetime) -> None:
    inf = profile.setdefault("inferred", {})
    wd = inf.setdefault("calls_by_weekday", {})
    hr = inf.setdefault("calls_by_hour_utc", {})
    wd[str(now.weekday())] = wd.get(str(now.weekday()), 0) + 1
    hr[str(now.hour)] = hr.get(str(now.hour), 0) + 1
    prev = profile.get("last_call_ts")
    if prev:
        try:
            gap = (now - datetime.fromisoformat(prev)).total_seconds() / 86400
            n = profile.get("total_calls", 0)
            avg = inf.get("avg_days_between_calls")
            inf["avg_days_between_calls"] = round(gap if avg is None else (avg * (n - 1) + gap) / n, 2)
        except Exception:
            pass


def apply_call(phone: str, extracted: dict, call_date: str) -> dict:
    profile = get_profile(phone) or empty_profile(phone)
    now = datetime.now(timezone.utc)

    for field in ("name", "location", "occupation"):
        if extracted.get(field):
            profile[field] = extracted[field]
    if extracted.get("key_facts"):
        merged = list(dict.fromkeys(extracted["key_facts"] + profile.get("key_facts", [])))
        profile["key_facts"] = merged[: config.MAX_KEY_FACTS]
    if extracted.get("open_threads"):
        profile["open_threads"] = extracted["open_threads"]

    _merge_entities(profile, extracted.get("entities", []), call_date)

    call_record = {"date": call_date, "summary": extracted.get("summary", "")}
    if extracted.get("mood"):
        call_record["mood"] = extracted["mood"]
        profile.setdefault("mood_history", []).append({"date": call_date, "mood": extracted["mood"]})
        profile["mood_history"] = profile["mood_history"][-30:]
    if extracted.get("topics"):
        call_record["topics"] = extracted["topics"]
    profile["last_calls"] = [call_record] + profile.get("last_calls", [])[: config.MAX_LAST_CALLS - 1]

    profile["total_calls"] = profile.get("total_calls", 0) + 1
    _update_inferred(profile, now)
    if extracted.get("sensitive"):
        profile.setdefault("inferred", {})["sensitive"] = True
    profile["last_call_date"] = call_date
    profile["last_call_ts"] = now.isoformat()

    save_profile(phone, profile)
    return profile


def format_profile_for_prompt(profile: dict) -> str:
    if not profile:
        return ""
    lines = ["## What you know about this person:"]
    if profile.get("name"):
        lines.append(f"- Name: {profile['name']}")
    if profile.get("location"):
        lines.append(f"- Location: {profile['location']}")
    if profile.get("occupation"):
        lines.append(f"- Occupation: {profile['occupation']}")
    if profile.get("total_calls", 0) > 1:
        lines.append(f"- This is call number {profile['total_calls']} with them")
    if profile.get("key_facts"):
        lines.append("\n### Key facts about them:")
        lines += [f"- {f}" for f in profile["key_facts"][:7]]
    entities = profile.get("entities") or {}
    if entities:
        top = sorted(entities.values(), key=lambda e: e.get("mentions", 0), reverse=True)[:6]
        lines.append("\n### People and things in their life:")
        for e in top:
            desc = " - ".join(p for p in (e.get("relation"), e.get("note")) if p)
            lines.append(f"- {e['name']}" + (f" ({desc})" if desc else ""))
    if profile.get("open_threads"):
        lines.append("\n### Things to follow up on:")
        lines += [f"- {t}" for t in profile["open_threads"][:5]]
    if profile.get("last_calls"):
        last = profile["last_calls"][0]
        lines.append(f"\n### Last conversation ({last.get('date', 'recently')}):")
        lines.append(f"- {last.get('summary', 'No summary')}")
        if last.get("mood"):
            lines.append(f"- They seemed: {last['mood']}")
    return "\n".join(lines)


# =============================================================================
# Semantic memory (Mem0 Platform — hosted)
# =============================================================================

def add_call_memory(user_id: str, messages: List[dict], metadata: Optional[dict] = None, infer: bool = True):
    """Send raw turns to Mem0; its own LLM extracts and embeds them (infer=True)."""
    if not messages:
        return
    try:
        _mem0_client().add(messages, user_id=user_id, metadata=metadata or {}, infer=infer)
        logger.info(f"mem0: stored call for {user_id} (infer={infer})")
    except Exception as e:
        logger.error(f"mem0 add failed: {e}")


def search_memories(user_id: str, query: str, limit: int = 5) -> List[dict]:
    try:
        res = _mem0_client().search(query, version="v2", filters={"user_id": user_id})
        results = (res.get("results", res) if isinstance(res, dict) else res) or []
        return results[:limit]
    except Exception as e:
        logger.error(f"mem0 search failed: {e}")
        return []


def _delete_memories(user_id: str) -> None:
    try:
        _mem0_client().delete_all(user_id=user_id)
    except Exception as e:
        logger.error(f"mem0 delete_all failed: {e}")


def format_memories_for_prompt(results: List[dict]) -> str:
    if not results:
        return ""
    lines = ["Relevant things from past conversations:"]
    for m in results[:5]:
        text = m.get("memory") or m.get("text") or m.get("content")
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


# =============================================================================
# Public async API (orchestration; sync stores run in threads)
# =============================================================================

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def load_context(phone: str) -> dict:
    prof = await asyncio.to_thread(get_profile, phone)
    is_first_time = not (prof and prof.get("name"))
    is_same_day = bool(prof and prof.get("last_call_date") == _today())

    parts = []
    if is_first_time:
        parts.append(prompts.FIRST_TIME_CONTEXT)
    elif is_same_day:
        parts.append(prompts.SAME_DAY_CONTEXT)
    if prof:
        block = format_profile_for_prompt(prof)
        if block:
            parts.append(block)

    return {
        "user_id": normalize_identity(phone),
        "profile": prof or empty_profile(phone),
        "is_first_time": is_first_time,
        "is_same_day": is_same_day,
        "context_text": "\n\n".join(parts),
    }


async def save_call(phone: str, messages: list, extracted: dict) -> None:
    if not messages:
        return
    user_id = normalize_identity(phone)
    date = _today()
    await asyncio.to_thread(apply_call, phone, extracted, date)
    metadata = {"date": date, "summary": extracted.get("summary"),
                "mood": extracted.get("mood"), "topics": extracted.get("topics", [])}
    await asyncio.to_thread(add_call_memory, user_id, messages, metadata, True)
    logger.info(f"Saved call for {user_id}")


async def search_relevant(phone: str, query: str) -> str:
    user_id = normalize_identity(phone)
    results = await asyncio.to_thread(search_memories, user_id, query, 5)
    return format_memories_for_prompt(results)


async def delete_user(phone: str) -> None:
    user_id = normalize_identity(phone)
    await asyncio.to_thread(_delete_profile, phone)
    await asyncio.to_thread(_delete_memories, user_id)
