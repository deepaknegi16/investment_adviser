"""Shared agent loop: Claude tool runner + structured JSON output.

Uses the Anthropic SDK's beta tool runner to drive the tool-use loop.
Handles the two edge cases the runner doesn't: `pause_turn` from the
web-search server tool (restart with mirrored history) and `refusal`
stop reasons. The final message is validated JSON via output_config.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import anthropic

MODEL = os.environ.get("ADVISER_MODEL", "claude-opus-5")
MAX_PAUSE_RESTARTS = 3

_client: Optional[anthropic.Anthropic] = None


class AgentUnavailable(Exception):
    """Raised when the AI layer can't run (no key, refusal, bad output)."""


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise AgentUnavailable(
                "No Anthropic credentials found. Set ANTHROPIC_API_KEY in backend/.env "
                "to enable AI analysis."
            )
        _client = anthropic.Anthropic()
    return _client


def _extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Lenient fallback: strip code fences / grab the outermost object.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise AgentUnavailable("Agent returned non-JSON output.")


def run_agent(
    system: str,
    prompt: str,
    tools: List[Any],
    schema: Dict[str, Any],
    max_tokens: int = 16000,
) -> Dict[str, Any]:
    """Run an agentic loop and return the parsed structured result."""
    client = _get_client()
    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
    kwargs: Dict[str, Any] = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "tools": tools,
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    if MODEL.startswith(("claude-opus-5", "claude-fable-5")):
        # Server-side refusal fallbacks: if safety classifiers decline, the API
        # re-runs the request on Anthropic's recommended fallback model.
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"

    last = None
    restarts = 0
    try:
        while True:
            runner = client.beta.messages.tool_runner(messages=messages, **kwargs)
            for message in runner:
                last = message
                # Mirror history so a pause_turn restart can resume the turn.
                messages.append({"role": "assistant", "content": message.content})
                tool_response = runner.generate_tool_call_response()
                if tool_response is not None:
                    messages.append(tool_response)
            if last is None or last.stop_reason != "pause_turn":
                break
            restarts += 1
            if restarts > MAX_PAUSE_RESTARTS:
                raise AgentUnavailable("Agent did not finish (turn stayed paused).")
    except anthropic.AuthenticationError as e:
        raise AgentUnavailable(f"Anthropic authentication failed: {e.message}") from e
    except anthropic.RateLimitError as e:
        raise AgentUnavailable("Anthropic API rate limited — try again shortly.") from e
    except anthropic.APIStatusError as e:
        raise AgentUnavailable(f"Anthropic API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AgentUnavailable("Could not reach the Anthropic API (network error).") from e

    if last is None:
        raise AgentUnavailable("Agent produced no response.")
    if last.stop_reason == "refusal":
        raise AgentUnavailable("The model declined this request.")
    if last.stop_reason == "max_tokens":
        raise AgentUnavailable("Agent response was truncated — try refreshing.")

    text = next((b.text for b in last.content if b.type == "text"), None)
    if not text:
        raise AgentUnavailable("Agent returned no text output.")
    return _extract_json(text)
