"""Recorded traces: the demo's safety net.

    python3 traces.py      # run every post once in LIVE mode and save results/traces/<post_id>.json

A trace holds the full event list (with timings), the outcome, the date and the models used.
The UI replays a trace through the same drawing code as a live run.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from trust_layer import llm
from trust_layer.orchestrator import score_post

ROOT = Path(__file__).parent
TRACE_DIR = ROOT / "results" / "traces"
STEP_PAUSE = 0.25    # fixed display pause between replayed events, so each step can be read
REPLAY_SPEED = 0.5   # recorded gaps (which include LLM calls) play at half their real length


def outcome_of(decision: dict, seconds: float) -> dict:
    """The plain, saveable result of one run. Live runs and replays both return this shape."""
    return {
        "score": decision["score"], "route": decision["route"], "route_reason": decision["route_reason"],
        "llm_calls": decision["llm_calls"],
        "tokens": sum(u["input_tokens"] + u["output_tokens"] for u in decision["llm_usage"]),
        "seconds": seconds,
        "verdicts": {k: {"verdict": r.verdict, "confidence": r.confidence, "reason": r.reason}
                     for k, r in decision["results"].items()},
    }


def record(post: dict) -> dict:
    start, events = time.time(), []
    decision = score_post(post, on_event=lambda e: events.append({**e, "t": round(time.time() - start, 3)}))
    return {
        "post_id": post["post_id"],
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": {"small": llm.SMALL_MODEL, "strong": llm.STRONG_MODEL},
        "events": events,
        "outcome": outcome_of(decision, round(time.time() - start, 2)),
        "llm_usage": decision["llm_usage"],
    }


def load(post_id: str):
    path = TRACE_DIR / f"{post_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def replay(trace: dict, on_event, sleep=time.sleep) -> dict:
    """Send the recorded events again, paced from their recorded timings. No API calls."""
    last = 0.0
    for event in trace["events"]:
        sleep(STEP_PAUSE + (event["t"] - last) * REPLAY_SPEED)
        last = event["t"]
        on_event({k: event[k] for k in ("agent", "kind", "detail")})
    return trace["outcome"]


def main():
    if not llm.live():
        raise SystemExit("Recording needs LIVE mode, so a rule-based run is never saved as a recording.")
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    for file in ("posts.json", "holdout_posts.json"):
        for post in json.loads((ROOT / "data" / file).read_text()):
            trace = record(post)
            (TRACE_DIR / f"{post['post_id']}.json").write_text(json.dumps(trace, indent=1))
            o = trace["outcome"]
            print(f"{post['post_id']}: {o['route']:<6} score {o['score']:>3}, {o['llm_calls']} LLM calls, {o['seconds']}s")
    print(f"Saved traces to {TRACE_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
