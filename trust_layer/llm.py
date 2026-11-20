"""Thin wrapper around the Claude API.

Two model tiers, set by environment variables so they can be changed without
touching code. With no ANTHROPIC_API_KEY and no `ant auth login` credentials, live() is False and the agents
fall back to simple offline heuristics, so the pipeline can still be run and tested.
"""
import json
import os
import time
from pathlib import Path

SMALL_MODEL = os.getenv("TRUST_SMALL_MODEL", "claude-haiku-5-5")
STRONG_MODEL = os.getenv("TRUST_STRONG_MODEL", "claude-sonnet-5-5")

# Why: a hung connection must not stall a live demo for minutes. Each call gives up after
# 15 seconds, and retries are off so 15 seconds is the worst case. The slowest call seen is about 7 seconds.
TIMEOUT_SECONDS = 15


def client():
    import anthropic  # imported here so offline mode needs no install

    return anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=0)


JSON_RULE = (
    'Reply with one JSON object only, no other text: '
    '{"verdict": "pass" | "warn" | "fail", "confidence": 0.0-1.0, "reason": "one short sentence"}'
)


def live() -> bool:
    """Live if an API key is set, or if `ant auth login` has stored credentials (the SDK reads those)."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return True
    creds = Path.home() / ".config" / "anthropic" / "credentials"
    return creds.is_dir() and any(creds.glob("*.json"))


def ask_text(model: str, system: str, user: str) -> tuple[str, dict]:
    """Send one prompt, return (plain text answer, usage numbers). Used for shopper answers."""
    start = time.time()
    msg = client().messages.create(
        model=model,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    usage = {
        "model": model,
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "seconds": round(time.time() - start, 2),
    }
    return text, usage


def ask_json(model: str, system: str, user: str) -> tuple[dict, dict]:
    """Send one prompt, return (parsed JSON answer, usage numbers)."""
    start = time.time()
    msg = client().messages.create(
        model=model,
        max_tokens=2000,  # headroom for any thinking the model does before its answer
        system=system + "\n" + JSON_RULE,
        messages=[{"role": "user", "content": user}],
    )
    # Some models return a thinking block first, so read only the text blocks.
    text = "".join(b.text for b in msg.content if b.type == "text")
    answer = json.loads(text[text.index("{"): text.rindex("}") + 1])
    usage = {
        "model": model,
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "seconds": round(time.time() - start, 2),
    }
    return answer, usage
