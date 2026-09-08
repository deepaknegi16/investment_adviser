"""Shared safety vocabulary: the disclaimer, and advice-language detection.

Before this module the disclaimer existed as four hardcoded strings across
analyst.py, screener.py, gold_watch.py and gold_alerts.py — two spelled
"personalized" and two "personalised". A disclaimer that drifts is a disclaimer
nobody is maintaining, so there is now exactly one.

`scan_advice()` is the enforcement half. Telling a model "this is not financial
advice" in a system prompt is a request, not a guarantee; this checks whether it
listened. It is deliberately generic (no gold in it) so the Analyst and Screener
can adopt it without a rewrite.
"""
from __future__ import annotations

import re
from typing import List

DISCLAIMER = "Informational research, not personalised financial advice."

# Prompt-side phrasing, so every agent's system prompt says the same thing.
PROMPT_RULE = (
    "This is informational research, not personalised financial advice. Describe "
    "what the evidence shows and what it implies; never instruct the reader to "
    "buy, sell or hold, and never guarantee an outcome."
)

# Each pattern is (kind, regex). Kept narrow on purpose: the goal is to catch a
# model that has started giving instructions, not to police the word "buy".
# "The analyst consensus is BUY" and "buying pressure from central banks" are
# legitimate research language and must not trip this.
_ADVICE_PATTERNS = [
    ("directive", r"\byou\s+(?:should|must|need to|ought to)\s+(?:buy|sell|hold|exit|invest|allocate|sell off)\b"),
    ("directive", r"\b(?:i|we)\s+(?:recommend|advise|suggest)\s+(?:that\s+)?you\b"),
    ("directive", r"\b(?:buy|sell|exit)\s+(?:now|immediately|today|right away)\b"),
    ("directive", r"\byour\s+(?:portfolio|money|savings|capital)\s+should\b"),
    ("guarantee", r"\b(?:guaranteed|guarantee[sd]?)\s+(?:to|returns?|profit|gains?)\b"),
    ("guarantee", r"\bwill\s+(?:definitely|certainly|surely)\s+(?:rise|fall|reach|hit|go)\b"),
    ("guarantee", r"\b(?:risk[- ]free|can'?t lose|no downside|sure thing)\b"),
    ("guarantee", r"\bis\s+guaranteed\b"),
]

_COMPILED = [(kind, re.compile(rx, re.I)) for kind, rx in _ADVICE_PATTERNS]


def scan_advice(text: str) -> List[dict]:
    """Return [{kind, phrase}] for advice-like language found in `text`."""
    if not text:
        return []
    found = []
    for kind, rx in _COMPILED:
        for m in rx.finditer(text):
            found.append({"kind": kind, "phrase": m.group(0).strip()})
    return found


def with_disclaimer(text: str) -> str:
    """Append the disclaimer unless it is already there. Never rewrites `text`."""
    if not text:
        return DISCLAIMER
    if DISCLAIMER.lower() in text.lower():
        return text
    return f"{text.rstrip()} {DISCLAIMER}"
