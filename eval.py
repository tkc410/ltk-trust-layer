"""Evaluate the trust layer on both labeled sets.

    python3 eval.py        # main set and holdout, 3 runs each
    python3 eval.py 1      # 1 run each, for a quick check

Rule: this script measures and reports. It changes no labels, prompts or thresholds.

Each post runs several times because live LLM verdicts can change between runs.
Results go to results/eval.json so the UI's eval panel can read them without re-running.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from trust_layer import llm
from trust_layer.orchestrator import score_post

ROOT = Path(__file__).parent
SETS = {"main": "posts.json", "holdout": "holdout_posts.json"}
GROUPS = ("clean", "hold", "borderline")
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def run_once(post: dict) -> dict:
    """Score one post and keep only what the eval needs."""
    start = time.time()
    decision = score_post(post)
    return {
        "route": decision["route"],
        "score": decision["score"],
        "verdicts": {name: r.verdict for name, r in decision["results"].items()},
        "llm_calls": decision["llm_calls"],
        "tokens": sum(u["input_tokens"] + u["output_tokens"] for u in decision["llm_usage"]),
        "seconds": round(time.time() - start, 2),
    }


def summarize(records: list) -> dict:
    per_run = []
    for i in range(RUNS):
        pairs = [(r["label"], r["group"], r["runs"][i]["route"]) for r in records]
        per_run.append({
            "exact": sum(route == label for label, _, route in pairs),
            # Pass versus not pass: the question that matters most for grounding an AI answer.
            "pass_vs_not": sum((route == "pass") == (label == "pass") for label, _, route in pairs),
            # A wrong pass is the costly error: a bad post reaches the AI answer.
            "wrong_passes": sum(route == "pass" and label != "pass" for label, _, route in pairs),
            "by_group": {
                g: {"correct": sum(route == label for label, grp, route in pairs if grp == g),
                    "total": sum(grp == g for _, grp, _ in pairs)}
                for g in GROUPS
            },
        })

    def changed(key):
        return [r["post_id"] for r in records
                if len({json.dumps(x[key], sort_keys=True) for x in r["runs"]}) > 1]

    all_runs = [x for r in records for x in r["runs"]]
    return {
        "posts": len(records),
        "runs": RUNS,
        "per_run": per_run,
        "route_changed": changed("route"),
        "verdict_changed": changed("verdicts"),
        "cost_per_post": {
            k: {"min": min(x[k] for x in all_runs), "max": max(x[k] for x in all_runs)}
            for k in ("llm_calls", "tokens", "seconds")
        },
        "zero_llm_posts": sum(all(x["llm_calls"] == 0 for x in r["runs"]) for r in records),
        "total_llm_calls": sum(x["llm_calls"] for x in all_runs),
        "total_tokens": sum(x["tokens"] for x in all_runs),
        "misses": [
            {"post_id": r["post_id"], "label": r["label"], "routes": [x["route"] for x in r["runs"]]}
            for r in records if any(x["route"] != r["label"] for x in r["runs"])
        ],
    }


def print_report(name: str, s: dict) -> None:
    n = s["posts"]
    runs = s["per_run"]
    row = lambda label, cells: print(f"  {label:<20}" + "".join(f"{c:>10}" for c in cells))
    print(f"\n== {name}: {n} posts x {s['runs']} runs")
    row("", [f"run {i + 1}" for i in range(len(runs))])
    row("exact route", [f"{r['exact']}/{n}" for r in runs])
    row("pass vs not pass", [f"{r['pass_vs_not']}/{n}" for r in runs])
    row("wrong passes", [r["wrong_passes"] for r in runs])
    for g in GROUPS:
        row(f"  {g}", [f"{r['by_group'][g]['correct']}/{r['by_group'][g]['total']}" for r in runs])
    print(f"  stability: route changed on {len(s['route_changed'])} {s['route_changed']}, "
          f"a verdict changed on {len(s['verdict_changed'])} {s['verdict_changed']}")
    c = s["cost_per_post"]
    print(f"  cost per post (min-max): LLM calls {c['llm_calls']['min']}-{c['llm_calls']['max']}, "
          f"tokens {c['tokens']['min']:,}-{c['tokens']['max']:,}, "
          f"seconds {c['seconds']['min']}-{c['seconds']['max']}")
    print(f"  {s['zero_llm_posts']}/{n} posts never call an LLM. "
          f"Totals: {s['total_llm_calls']} LLM calls, {s['total_tokens']:,} tokens")
    for m in s["misses"]:
        print(f"  miss {m['post_id']}: label {m['label']}, got {', '.join(m['routes'])}")


def main():
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if llm.live() else "offline",
        "models": {"small": llm.SMALL_MODEL, "strong": llm.STRONG_MODEL},
        "runs": RUNS,
        "sets": {},
    }
    print(f"Mode: {output['mode'].upper()}, {RUNS} run(s) per post")
    for name, file in SETS.items():
        posts = json.loads((ROOT / "data" / file).read_text())
        records = []
        for post in posts:
            print(f"  {name} {post['post_id']}", end="\r", flush=True)  # progress
            runs = [run_once(post) for _ in range(RUNS)]
            records.append({k: post[k] for k in ("post_id", "label", "group")} | {"runs": runs})
        print(" " * 30, end="\r")  # clear the progress line
        summary = summarize(records)
        output["sets"][name] = {"summary": summary, "posts": records}
        print_report(name, summary)

    out = ROOT / "results" / "eval.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(output, indent=1))
    print(f"\nSaved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
