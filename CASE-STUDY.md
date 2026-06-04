# HowzYourDay — case study

A voice companion you call at the end of the day. Talk it through, hang up — and next
time, it remembers you. No app, no sign-up: your phone number is your identity.

**Live:** [howzyour.day](https://howzyour.day) · **Deck:** [the 12-slide story](https://ankitaggarwal.github.io/howzyour.day/) · **Code:** [GitHub](https://github.com/ankitaggarwal/howzyour.day) (MIT)

## The problem

Most journaling apps die on one friction: they ask you to **type**. At 11pm, tired,
nobody opens an app and writes paragraphs — so the habit never sticks. But people
*will* tell a friend about their day, out loud, unprompted. So the interface is a
phone call.

## The tension

A voice you'd actually call back has to be two things that pull against each other:

- **Instant** — it answers in the first half-second, like a friend who picks up.
- **Intimate** — it remembers you between calls.

Fast usually means stateless; memory usually means slow. Every design choice serves
one of those, or the tension between them.

## How it resolves

- **Instant comes from the hot path.** A small profile card lives in **Upstash Redis**,
  fetched *by key* (no scan) with a per-worker cache. The greeting lands before any
  heavy lookup runs.
- **Intimate comes from the deep path, used sparingly.** Semantic memory lives in
  **Mem0**; it's touched only when you ask about the past ("remember when…").
- **Hide the seam.** When recall does fire, a short filler line plays while the lookup
  runs — the pause disappears into conversation. The win wasn't being fast everywhere;
  it was covering the one moment it isn't.
- **Remember less, on purpose.** On hang-up it keeps identity, people, open threads,
  and mood, and drops the small talk. Curation is the feature.
- **One identity, two front doors.** Keyed by your phone number (or `web:hash(email)`
  on the web). Same agent, same memory — the front door is just an identity scheme.

## Stack

Python · **Cartesia Line** (voice) · **OpenRouter** (LLM) · **Upstash Redis** (fast
profile) · **Mem0** (semantic memory) · FastAPI (web companion) · Docker.

## The lesson

For a companion, **latency is the product** — and the craft is hiding the moment it
isn't fast. The model was the easy part; the *feeling of being remembered* was the work.
