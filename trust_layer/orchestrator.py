"""The orchestrator: decides which agents run, then applies gates, score and route."""
from . import agents, tools

WEIGHTS = {"claim": 40, "freshness": 25, "brand_safety": 20, "disclosure": 15}
POINTS = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
PASS_AT = 80          # score at or above this passes
REVIEW_AT = 50        # score at or above this goes to human review, below is held
MIN_CONFIDENCE = 0.6  # any agent less sure than this sends the post to a human


def score_post(post: dict, on_event=None) -> dict:
    """Run the trust checks on one post. on_event receives each step as it happens."""
    trace = []

    def emit(agent, kind, detail):
        event = {"agent": agent, "kind": kind, "detail": detail}
        trace.append(event)
        if on_event:
            on_event(event)

    results = {}

    def record(r):
        # Why: send each verdict as soon as its agent returns, so a live view completes cards one by one.
        results[r.agent] = r
        emit(r.agent, "verdict", f"{r.verdict} ({r.confidence:.2f}) {r.reason}")

    # Rule 1: freshness and the disclosure tag check always run. Both are free.
    emit("orchestrator", "decide", "run freshness and disclosure (no LLM cost)")
    record(agents.freshness_agent(post, emit))
    # Rule 2 lives inside the disclosure agent: it calls the LLM only when a paid post has no clear tag.
    record(agents.disclosure_agent(post, emit))

    # Rule 3: the claim agent runs only when the caption has a checkable claim.
    if agents.has_checkable_claim(post["caption"]):
        emit("orchestrator", "decide", "caption has a checkable claim, run claim agent")
        record(agents.claim_agent(post, emit))
    else:
        emit("orchestrator", "decide", "no checkable claim, skip claim agent")
        record(agents.CheckResult("claim", "skipped", 1.0, "No checkable claim in caption."))

    # Rule 4: brand safety runs only on sponsored posts whose brand has guidelines.
    brand = post["sponsor_brand"]
    if post["paid_partnership"] and brand and tools.get_brand_guidelines(brand):
        emit("orchestrator", "decide", "sponsored post with guidelines, run brand safety agent")
        record(agents.brand_safety_agent(post, emit))
    else:
        emit("orchestrator", "decide", "not sponsored or no guidelines, skip brand safety agent")
        record(agents.CheckResult("brand_safety", "skipped", 1.0, "Not sponsored, or brand has no guidelines."))

    score = _trust_score(results)
    route, why = _route(post, results, score)
    emit("orchestrator", "route", f"score {score} -> {route}: {why}")

    usage = [u for r in results.values() for u in r.llm_usage]
    return {
        "post_id": post["post_id"],
        "score": score,
        "route": route,
        "route_reason": why,
        "phase2_candidate": any("phase2" in r.flags for r in results.values()),
        "results": results,
        "llm_calls": len(usage),
        "llm_usage": usage,
        "trace": trace,
    }


def _trust_score(results: dict) -> int:
    """Weighted score over the checks that ran. Skipped checks drop out, so their weight spreads to the rest."""
    ran = {name: r for name, r in results.items() if r.verdict != "skipped"}
    total = sum(WEIGHTS[name] for name in ran)
    earned = sum(WEIGHTS[name] * POINTS[r.verdict] for name, r in ran.items())
    return round(100 * earned / total)


def _route(post: dict, results: dict, score: int) -> tuple[str, str]:
    # Hard gates first. These are never averaged away.
    if post["paid_partnership"] and results["disclosure"].verdict == "fail":
        return "hold", "hard gate: paid post with no disclosure"
    if "delisted" in results["freshness"].flags:
        return "hold", "hard gate: product delisted"

    unsure = [r.agent for r in results.values() if r.confidence < MIN_CONFIDENCE]
    if unsure and score >= REVIEW_AT:
        return "review", f"low confidence from {', '.join(unsure)}"

    if score >= PASS_AT:
        # Why: with these weights a lone fail or claim warn can still reach 80, so pass or
        # review would hang on where the threshold sits. These two rules make it explicit.
        failed = [r.agent for r in results.values() if r.verdict == "fail"]
        if failed:
            return "review", f"{', '.join(failed)} failed, and any fail blocks a pass"
        if results["claim"].verdict == "warn":
            return "review", "claim warned, and a claim warn needs review"
        # Why: an unclear disclosure ("gifted") on a paid post needs a human, whatever the score.
        if post["paid_partnership"] and results["disclosure"].verdict == "warn":
            return "review", "paid post with unclear disclosure needs review"
        return "pass", "score at or above pass threshold"
    if score >= REVIEW_AT:
        return "review", "score in the review band"
    return "hold", "score below review threshold"
