"""
Today Journal — voice agent entry point (Cartesia Line SDK 0.2.10).

Flow:
  - get_agent(): on each call, resolve the caller's phone, load their memory,
    and build a TodayAgent with a personalized system prompt + greeting.
  - TodayAgent: a single conversational LlmAgent. A background `recall_memory`
    tool does on-demand semantic search ("remember when..."). On CallEnded we
    extract structured info and persist it.

Run:    OPENROUTER_API_KEY=... uv run python main.py
Deploy: pushed to the connected repo, auto-deployed by Cartesia.
"""

import asyncio
import json
import os
from typing import Annotated, AsyncIterable, Optional

from loguru import logger

from line.agent import AgentClass, TurnEnv
from line.events import AgentSendText, CallEnded, InputEvent, OutputEvent, UserTextSent
from line.llm_agent import LlmAgent, LlmConfig, ToolEnv, end_call, loopback_tool
from line.voice_agent_app import AgentEnv, CallRequest, VoiceAgentApp

import config
import memory
import prompts

# All models go through OpenRouter (LiteLLM reads OPENROUTER_API_KEY from env too).
_AGENT_API_KEY = config.OPENROUTER_API_KEY
# Resolve the best available model once at worker boot (prefers Gemini 2.5 Flash,
# falls back gracefully). Bounded + safe — never blocks startup.
MODEL = config.resolve_model()


# =============================================================================
# Helpers
# =============================================================================

def _caller_phone(call_request: CallRequest) -> Optional[str]:
    for attr in ("from_number", "from_", "caller", "phone", "caller_id"):
        val = getattr(call_request, attr, None)
        if val:
            return val
    return None


def _caller_identity(call_request: CallRequest) -> tuple[Optional[str], Optional[str]]:
    """Resolve who is calling, for telephony OR web.

    Phone calls surface the number on the CallRequest. Web calls come through our
    backend's WebSocket proxy, which sets metadata.from = "web:<hash>" (+ a name).
    Returns (identity, display_name); identity is normalized by the memory layer.
    """
    raw = _caller_phone(call_request)
    meta = getattr(call_request, "metadata", None) or {}
    raw = raw or meta.get("from") or meta.get("user_id")
    return raw, meta.get("name")


def _history_to_messages(history) -> list[dict]:
    """Convert SDK conversation events into [{role, content}] for storage/extraction."""
    messages = []
    for ev in history or []:
        if isinstance(ev, UserTextSent):
            text = getattr(ev, "content", None)
            if text:
                messages.append({"role": "user", "content": text})
        elif isinstance(ev, AgentSendText):
            text = getattr(ev, "text", None)
            if text:
                messages.append({"role": "assistant", "content": text})
    return messages


def _greeting(ctx: dict) -> str:
    profile = ctx.get("profile", {})
    if ctx.get("is_same_day"):
        name = profile.get("name")
        return f"Hey {name}, back again. What's up?" if name else "Hey, back again. What's on your mind?"
    if not ctx.get("is_first_time") and profile.get("name"):
        name = profile["name"]
        threads = profile.get("open_threads") or []
        if threads:
            return f"Hey {name}! Last time you mentioned {threads[0]}. How's that going?"
        return f"Hey {name}! Good to hear from you. What's going on?"
    return prompts.get_instant_greeting()


async def _extract(messages: list[dict]) -> dict:
    """One-shot structured extraction of the call (LiteLLM, same model as the agent)."""
    import litellm
    try:
        resp = await litellm.acompletion(
            model=MODEL,
            api_key=_AGENT_API_KEY,
            messages=[{"role": "user", "content": prompts.get_extraction_prompt(messages)}],
            temperature=0.3,
            max_tokens=600,
        )
        text = (resp.choices[0].message.content or "").strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text)
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return {"summary": "Call completed"}


# =============================================================================
# Agent
# =============================================================================

class TodayAgent(AgentClass):
    def __init__(self, user_id: Optional[str], system_prompt: str, introduction: str):
        self.user_id = user_id
        self._last_event: Optional[InputEvent] = None
        self._chatter = LlmAgent(
            model=MODEL,
            api_key=_AGENT_API_KEY,
            tools=[self.recall_memory, end_call],
            max_tool_iterations=3,   # this is a chatty companion, not an agent that chains tools
            config=LlmConfig(system_prompt=system_prompt, introduction=introduction,
                             max_tokens=120,  # spoken replies stay to a sentence or two
                             **config.reasoning_off_kwargs()),
        )

    async def process(self, env: TurnEnv, event: InputEvent) -> AsyncIterable[OutputEvent]:
        self._last_event = event
        if isinstance(event, CallEnded):
            await self._save(event)
            return
        async for output in self._chatter.process(env, event):
            yield output

    @loopback_tool
    async def recall_memory(
        self,
        ctx: ToolEnv,
        query: Annotated[str, "The specific thing the caller asked you to recall from a PAST conversation"],
    ) -> str:
        """Look up something from THIS caller's earlier conversations, then answer once.

        Call this ONLY when the caller explicitly asks about the past, e.g. "remember
        when...", "what did I tell you about...", "what did we talk about last time".
        Do NOT call it for greetings, names, small talk, or general questions.
        """
        if not self.user_id:
            return "I don't have anything saved from before."
        found = await memory.search_relevant(self.user_id, query)
        return found or "We haven't talked about that before."

    async def _save(self, event: InputEvent):
        if not self.user_id:
            return
        messages = _history_to_messages(getattr(event, "history", []))
        if not messages:
            return
        extracted = await _extract(messages)
        await memory.save_call(self.user_id, messages, extracted)

    async def _cleanup(self):
        await self._chatter.cleanup()


# =============================================================================
# App
# =============================================================================

async def get_agent(env: AgentEnv, call_request: CallRequest):
    identity, _name = _caller_identity(call_request)
    user_id = memory.normalize_identity(identity) if identity else None

    system_prompt = prompts.SYSTEM_PROMPT + "\n\n" + prompts.get_time_context_for_prompt()
    introduction = prompts.get_instant_greeting()

    if identity:
        # Bound the call-start fetch: a slow store must never delay first audio.
        # On timeout/failure we fall through to the instant, no-DB greeting below.
        try:
            ctx = await asyncio.wait_for(memory.load_context(identity), timeout=0.4)
            if ctx["context_text"]:
                system_prompt += "\n\n" + ctx["context_text"]
            introduction = _greeting(ctx)
        except asyncio.TimeoutError:
            logger.warning(f"Memory load slow for {user_id}; using instant greeting")
        except Exception as e:
            logger.warning(f"Memory load failed for {user_id}: {e}")

    logger.info(f"=== CALL from {user_id or 'unknown'} ===")
    return TodayAgent(user_id, system_prompt, introduction)


app = VoiceAgentApp(get_agent=get_agent)

if __name__ == "__main__":
    logger.info("Starting Today Journal")
    app.run()
