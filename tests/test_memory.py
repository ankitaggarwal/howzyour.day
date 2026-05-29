"""
Live verification of the memory layer against real Upstash Redis + Mem0 Platform.

Exercises the full path: profile round-trip in Redis, semantic store/search in
Mem0 (infer=False for a fast, deterministic check), and per-user isolation.

Run:  PYTHONPATH=. python3 tests/test_memory.py
"""

import asyncio
import sys

import memory

A = "+1 (999) 555-0000"   # messy on purpose -> tests normalization
B = "+19995550001"
_results = []


def check(name, cond):
    _results.append((name, cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


async def main():
    print("\n=== identity ===")
    check("normalize strips formatting", memory.normalize_phone(A) == "+19995550000")

    # Clean slate.
    memory._delete_memories(memory.normalize_phone(A))
    memory._delete_memories(memory.normalize_phone(B))
    memory._delete_profile(A)
    memory._delete_profile(B)

    print("\n=== profile round-trip (Upstash Redis) ===")
    check("missing profile -> None", memory.get_profile(A) is None)
    p = memory.apply_call(A, {
        "name": "Alex", "location": "Lisbon", "occupation": "teacher",
        "key_facts": ["building a voice agent"], "open_threads": ["ship the revamp"],
        "entities": [{"name": "Biscuit", "kind": "pet", "relation": "dog"},
                     {"name": "Sam", "kind": "person", "relation": "sister"}],
        "summary": "Talked about the project", "mood": "focused", "topics": ["work"],
        "sensitive": False,
    }, "2026-05-27")
    check("apply_call sets name", p["name"] == "Alex")
    memory._cache_del(memory.normalize_phone(A))  # force a real Redis read, not the cache
    got = memory.get_profile(A)
    check("profile persists in Redis", got is not None and got["name"] == "Alex")
    check("total_calls incremented", got["total_calls"] == 1)
    check("entities tracked", "biscuit" in got["entities"] and got["entities"]["biscuit"]["mentions"] == 1)
    check("mood_history appended", got["mood_history"] and got["mood_history"][-1]["mood"] == "focused")
    check("inferred activity recorded", sum(got["inferred"]["calls_by_weekday"].values()) == 1)
    block = memory.format_profile_for_prompt(got)
    check("prompt block mentions name + thread + entity",
          "Alex" in block and "ship the revamp" in block and "Biscuit" in block)

    print("\n=== semantic memory (Mem0 Platform, infer=False) ===")
    uidA, uidB = memory.normalize_phone(A), memory.normalize_phone(B)
    memory.add_call_memory(uidA, [
        {"role": "user", "content": "I adopted a golden retriever named Biscuit"},
        {"role": "assistant", "content": "Aw, Biscuit. Great name."},
    ], {"date": "2026-05-27"}, infer=False)
    memory.add_call_memory(uidB, [
        {"role": "user", "content": "I just moved to Berlin for a new job"},
    ], {"date": "2026-05-27"}, infer=False)
    await asyncio.sleep(3.0)  # hosted indexing is async

    resA = memory.search_memories(uidA, "do I have a pet?", limit=5)
    textA = " ".join((m.get("memory") or m.get("text") or "") for m in resA).lower()
    check("user A recalls their own dog", "biscuit" in textA or "retriever" in textA)
    check("user A does NOT see user B's Berlin move", "berlin" not in textA)

    print("\n=== orchestrator ===")
    ctx = await memory.load_context(A)
    check("returning caller detected", ctx["is_first_time"] is False)
    ctx_new = await memory.load_context("+10000000000")
    check("unknown caller -> first_time", ctx_new["is_first_time"] is True)

    print("\n=== cleanup ===")
    await memory.delete_user(A)
    await memory.delete_user(B)
    memory._delete_profile("+10000000000")
    check("profile deleted", memory.get_profile(A) is None)

    n_pass = sum(1 for _, c in _results if c)
    print(f"\n{'='*50}\n{n_pass}/{len(_results)} checks passed\n{'='*50}")
    sys.exit(0 if n_pass == len(_results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
