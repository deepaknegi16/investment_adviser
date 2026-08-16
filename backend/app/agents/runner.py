"""Shared agent machinery on the free Gemini stack, with Groq fallback.

Gemini's API does not allow Google-Search grounding, custom function tools,
and strict JSON output in a single request, so agent runs are a pipeline of
three primitives:

  1. tool_research()        — function-calling loop over our market-data tools
  2. web_research()         — Google Search grounding pass (news, with sources)
  3. structured_synthesis() — final JSON answer constrained to a schema

simple_response() serves the chat. Synthesis and chat fall back to Groq
(OpenAI-compatible API, no web search) when Gemini is rate-limited or down.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# Stable Gemini model to drop to when the primary is overloaded ("high demand").
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash")
MAX_TOOL_TURNS = 8

_gemini_client: Optional[genai.Client] = None
_groq_client = None


class AgentUnavailable(Exception):
    """Raised when the AI layer can't run (no key, quota, bad output)."""


def _gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise AgentUnavailable(
                "No Gemini credentials found. Create a free API key at "
                "aistudio.google.com/apikey and set GEMINI_API_KEY in backend/.env."
            )
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _groq():
    """Groq client via the OpenAI-compatible endpoint; None if no key."""
    global _groq_client
    if _groq_client is None and os.environ.get("GROQ_API_KEY"):
        from openai import OpenAI

        _groq_client = OpenAI(
            api_key=os.environ["GROQ_API_KEY"], base_url=GROQ_BASE_URL
        )
    return _groq_client


def _map_gemini_error(e: Exception) -> AgentUnavailable:
    code = getattr(e, "code", None)
    if code == 429:
        return AgentUnavailable(
            "Gemini free-tier limit reached for now — try again in a minute "
            "(daily quotas reset at midnight Pacific)."
        )
    if code in (401, 403):
        return AgentUnavailable(
            "Gemini API key was rejected — check GEMINI_API_KEY in backend/.env."
        )
    message = getattr(e, "message", None) or str(e)
    return AgentUnavailable(f"Gemini API error: {message}")


def _is_fallback_worthy(e: Exception) -> bool:
    """Rate limits and server errors are worth retrying on Groq; auth is not."""
    return getattr(e, "code", None) not in (401, 403)


def _is_transient(e: Exception) -> bool:
    msg = (getattr(e, "message", None) or str(e)).lower()
    return getattr(e, "code", None) in (500, 503) or "high demand" in msg or "overloaded" in msg


def _generate(model: str, contents, config) -> Any:
    """generate_content with overload/rate-limit resilience.

    Free-tier quotas are per model, so on a 429 or overload we hop to the
    stable fallback model's separate quota bucket, then wait out the
    per-minute window and try both again before giving up.
    """
    import time

    client = _gemini()
    attempts = [(model, 0)]
    if GEMINI_FALLBACK_MODEL != model:
        attempts.append((GEMINI_FALLBACK_MODEL, 2))
    attempts.append((model, 40))
    if GEMINI_FALLBACK_MODEL != model:
        attempts.append((GEMINI_FALLBACK_MODEL, 10))
    last: Optional[Exception] = None
    for attempt_model, delay in attempts:
        if delay:
            time.sleep(delay)
        try:
            return client.models.generate_content(
                model=attempt_model, contents=contents, config=config
            )
        except genai_errors.APIError as e:
            last = e
            if not (_is_transient(e) or getattr(e, "code", None) == 429):
                raise
    raise last  # exhausted everywhere — surface the original APIError


def _extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise AgentUnavailable("Agent returned non-JSON output.")


# ---------------------------------------------------------------- primitives


def tool_research(
    model: str,
    system: str,
    prompt: str,
    function_decls: List[Dict[str, Any]],
    tool_impls: Dict[str, Any],
    max_turns: int = MAX_TOOL_TURNS,
) -> str:
    """Function-calling loop: the model pulls data via our tools, returns notes."""
    client = _gemini()
    contents: List[Any] = [
        types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    ]
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=function_decls)],
    )
    try:
        for _ in range(max_turns):
            resp = _generate(model, contents, config)
            candidate = resp.candidates[0] if resp.candidates else None
            if candidate is None or candidate.content is None:
                raise AgentUnavailable("Gemini returned an empty response.")
            contents.append(candidate.content)
            calls = [
                p.function_call
                for p in (candidate.content.parts or [])
                if p.function_call
            ]
            if not calls:
                return resp.text or ""
            response_parts = []
            for call in calls:
                impl = tool_impls.get(call.name)
                try:
                    output = impl(**dict(call.args)) if impl else json.dumps(
                        {"error": f"unknown tool {call.name}"}
                    )
                except Exception as e:  # tool errors go back to the model
                    output = json.dumps({"error": str(e)})
                response_parts.append(
                    types.Part.from_function_response(
                        name=call.name, response={"result": output}
                    )
                )
            contents.append(types.Content(role="user", parts=response_parts))
        raise AgentUnavailable("Agent did not finish within the tool-turn limit.")
    except genai_errors.APIError as e:
        raise _map_gemini_error(e) from e


def web_research(model: str, prompt: str) -> Tuple[str, List[Dict[str, str]]]:
    """Google-Search-grounded pass. Returns (findings text, [{title, url}])."""
    client = _gemini()
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())]
    )
    try:
        resp = _generate(model, prompt, config)
    except genai_errors.APIError as e:
        raise _map_gemini_error(e) from e
    sources: List[Dict[str, str]] = []
    candidate = resp.candidates[0] if resp.candidates else None
    grounding = getattr(candidate, "grounding_metadata", None)
    for chunk in getattr(grounding, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        if web and web.uri:
            sources.append({"title": web.title or web.uri, "url": web.uri})
    return resp.text or "", sources


def structured_synthesis(
    model: str,
    system: str,
    prompt: str,
    schema: Dict[str, Any],
    groq_fallback: bool = True,
) -> Dict[str, Any]:
    """Final JSON answer constrained to `schema`; Groq fallback on Gemini outage."""
    client = _gemini()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_json_schema=schema,
    )
    try:
        resp = _generate(model, prompt, config)
        if not resp.text:
            raise AgentUnavailable("Gemini returned no text output.")
        return _extract_json(resp.text)
    except genai_errors.APIError as e:
        if groq_fallback and _is_fallback_worthy(e) and _groq() is not None:
            return _groq_json(system, prompt, schema)
        raise _map_gemini_error(e) from e


def simple_response(
    instructions: str,
    messages: List[Dict[str, str]],
    model: str,
) -> Tuple[str, str]:
    """Chat turn. Returns (reply, provider). Gemini first, Groq fallback."""
    try:
        client = _gemini()
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]
        resp = _generate(
            model, contents, types.GenerateContentConfig(system_instruction=instructions)
        )
        if resp.text:
            return resp.text, "gemini"
        raise AgentUnavailable("Gemini returned no text output.")
    except (genai_errors.APIError, AgentUnavailable) as e:
        if isinstance(e, genai_errors.APIError) and not _is_fallback_worthy(e):
            raise _map_gemini_error(e) from e
        groq = _groq()
        if groq is None:
            if isinstance(e, genai_errors.APIError):
                raise _map_gemini_error(e) from e
            raise
        return _groq_chat(instructions, messages), "groq"


# ------------------------------------------------------------- Groq fallback


def _groq_messages(instructions: str, messages: List[Dict[str, str]]):
    return [{"role": "system", "content": instructions}] + [
        {"role": m["role"], "content": m["content"]} for m in messages
    ]


def _groq_chat(instructions: str, messages: List[Dict[str, str]]) -> str:
    import openai as openai_mod

    try:
        resp = _groq().chat.completions.create(
            model=GROQ_MODEL, messages=_groq_messages(instructions, messages)
        )
        return resp.choices[0].message.content or ""
    except openai_mod.OpenAIError as e:
        raise AgentUnavailable(f"Groq fallback also failed: {e}") from e


def _groq_json(system: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    import openai as openai_mod

    instructions = (
        f"{system}\n\nRespond with ONLY a JSON object matching this schema exactly:\n"
        f"{json.dumps(schema)}"
    )
    try:
        resp = _groq().chat.completions.create(
            model=GROQ_MODEL,
            messages=_groq_messages(instructions, [{"role": "user", "content": prompt}]),
            response_format={"type": "json_object"},
        )
        return _extract_json(resp.choices[0].message.content or "")
    except openai_mod.OpenAIError as e:
        raise AgentUnavailable(f"Groq fallback also failed: {e}") from e
