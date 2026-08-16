"""Shared agent loop on the OpenAI Responses API.

Drives the agentic cycle: model → function calls → results → model, with the
built-in web_search tool handled server-side by OpenAI. The final answer is
constrained to a strict JSON schema via structured outputs.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

import openai
from openai import OpenAI

MAX_TURNS = 12  # model round-trips per agent run (function-call cycles)

_client: Optional[OpenAI] = None


class AgentUnavailable(Exception):
    """Raised when the AI layer can't run (no key, refusal, bad output)."""


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise AgentUnavailable(
                "No OpenAI credentials found. Set OPENAI_API_KEY in backend/.env "
                "to enable AI analysis."
            )
        _client = OpenAI()
    return _client


def _extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise AgentUnavailable("Agent returned non-JSON output.")


def run_agent(
    system: str,
    prompt: str,
    tools: List[Dict[str, Any]],
    tool_impls: Dict[str, Callable[..., str]],
    schema: Dict[str, Any],
    schema_name: str,
    model: str,
    reasoning_effort: str = "medium",
    max_output_tokens: int = 16000,
) -> Dict[str, Any]:
    """Run an agentic loop and return the parsed structured result."""
    client = _get_client()
    input_items: List[Any] = [{"role": "user", "content": prompt}]

    try:
        for _ in range(MAX_TURNS):
            resp = client.responses.create(
                model=model,
                instructions=system,
                input=input_items,
                tools=tools,
                reasoning={"effort": reasoning_effort},
                max_output_tokens=max_output_tokens,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
            input_items += resp.output
            calls = [item for item in resp.output if item.type == "function_call"]
            if not calls:
                if resp.status == "incomplete":
                    raise AgentUnavailable(
                        "Agent response was truncated — try refreshing."
                    )
                text = resp.output_text
                if not text:
                    refusals = [
                        c.refusal
                        for item in resp.output
                        if item.type == "message"
                        for c in item.content
                        if c.type == "refusal"
                    ]
                    if refusals:
                        raise AgentUnavailable(f"The model declined: {refusals[0]}")
                    raise AgentUnavailable("Agent returned no text output.")
                return _extract_json(text)
            for call in calls:
                impl = tool_impls.get(call.name)
                if impl is None:
                    output = json.dumps({"error": f"unknown tool {call.name}"})
                else:
                    try:
                        output = impl(**json.loads(call.arguments))
                    except Exception as e:  # tool errors go back to the model
                        output = json.dumps({"error": str(e)})
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": output,
                    }
                )
        raise AgentUnavailable("Agent did not finish within the turn limit.")
    except openai.AuthenticationError as e:
        raise AgentUnavailable(f"OpenAI authentication failed: {e}") from e
    except openai.RateLimitError as e:
        if getattr(e, "code", None) == "insufficient_quota":
            raise AgentUnavailable(
                "The OpenAI account has no remaining credits (insufficient_quota). "
                "Add billing/credits at platform.openai.com, then retry."
            ) from e
        raise AgentUnavailable("OpenAI API rate limited — try again shortly.") from e
    except openai.APIStatusError as e:
        raise AgentUnavailable(f"OpenAI API error ({e.status_code}): {e.message}") from e
    except openai.APIConnectionError:
        raise AgentUnavailable("Could not reach the OpenAI API (network error).")
