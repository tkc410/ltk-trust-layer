"""With and without: the same shopper question answered from all retrieved posts, then from trusted posts only.

    python3 compare.py

- Retrieval is a keyword match over the 30 main posts. It stands in for LTK's real search.
- Routes are read from results/eval.json, not rescored: posts are scored at publish time
  and the AI reads the stored result.
- The two calls use the same prompt. The only difference is which posts go in.
Results go to results/compare.json for the UI.
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from trust_layer import llm

ROOT = Path(__file__).parent

# Neutral on purpose: nothing here nudges the unfiltered answer to look bad.
SYSTEM = (
    "You are a shopping assistant. Answer the shopper's question using only the creator posts "
    "provided. Do not add facts that are not in the posts. If no post fits the question, say so.\n"
    "End with one line: \"Posts used:\" followed by the post IDs you relied on, or \"none\"."
)


def load(name):
    return json.loads((ROOT / name).read_text())


def stored_routes():
    """Route and reason per post from the saved eval run. All runs agreed; take the most common."""
    saved = load("results/eval.json")
    routes = {}
    for r in saved["sets"]["main"]["posts"]:
        route = Counter(x["route"] for x in r["runs"]).most_common(1)[0][0]
        verdicts = r["runs"][0]["verdicts"]
        # eval.json keeps verdicts, not reason text, so the reason lists the checks that warned or failed.
        flagged = [f"{agent} {v}" for agent, v in verdicts.items() if v in ("warn", "fail")]
        routes[r["post_id"]] = {"route": route, "reason": ", ".join(flagged) or "no check flagged"}
    return routes, saved["generated_at"]


def retrieve(question, posts, products):
    """A post matches when any keyword appears as a whole word in its caption or product name."""
    def text(p):
        return p["caption"] + " " + products[p["product_id"]]["name"]
    return [p for p in posts
            if any(re.search(rf"\b{re.escape(k)}\b", text(p), re.IGNORECASE) for k in question["keywords"])]


def answer(question, posts, products):
    if posts:
        lines = [f"- {p['post_id']} {p['creator']} (product: {products[p['product_id']]['name']}): "
                 f"\"{p['caption']}\"" for p in posts]
    else:
        lines = ["(no posts)"]
    prompt = f"Shopper question: {question['question']}\n\nCreator posts:\n" + "\n".join(lines)
    text, usage = llm.ask_text(llm.STRONG_MODEL, SYSTEM, prompt)
    used = re.search(r"Posts used:\s*(.*)", text, re.IGNORECASE)
    posts_used = re.findall(r"\bp\d{3}\b", used.group(1)) if used else []
    return {"posts_given": [p["post_id"] for p in posts], "answer": text, "posts_used": posts_used, "usage": usage}


def main():
    if not llm.live():
        raise SystemExit("compare.py needs LIVE mode: run `ant auth login` or set ANTHROPIC_API_KEY.")
    posts, products = load("data/posts.json"), load("data/products.json")
    routes, routes_from = stored_routes()
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": llm.STRONG_MODEL,
        "routes_from_eval_run": routes_from,
        "questions": [],
    }
    for q in load("data/questions.json"):
        retrieved = retrieve(q, posts, products)
        # Only pass goes in. A post waiting for review has not been cleared yet.
        trusted = [p for p in retrieved if routes[p["post_id"]]["route"] == "pass"]
        excluded = [{"post_id": p["post_id"], **routes[p["post_id"]]} for p in retrieved if p not in trusted]
        result = {
            **q,
            "retrieved": [p["post_id"] for p in retrieved],
            "excluded": excluded,
            "unfiltered": answer(q, retrieved, products),
            "filtered": answer(q, trusted, products),
        }
        output["questions"].append(result)

        print(f"\n=== {q['question_id']} {q['question']}")
        print(f"retrieved {result['retrieved']}")
        for e in excluded:
            print(f"  excluded {e['post_id']}: {e['route']} ({e['reason']})")
        for side in ("unfiltered", "filtered"):
            print(f"\n--- {side.upper()} (posts given: {result[side]['posts_given'] or 'none'})")
            print(result[side]["answer"])

    out = ROOT / "results" / "compare.json"
    out.write_text(json.dumps(output, indent=1))
    print(f"\nSaved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
