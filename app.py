"""Trust Layer demo UI.

    streamlit run app.py

Rule: the UI draws and never decides. All scoring lives in trust_layer/. This file calls
score_post, renders the events it emits, and reads the saved runs in results/.
"""
import ast
import json
import time
from pathlib import Path

import streamlit as st

from trust_layer import llm, tools
from trust_layer.orchestrator import score_post

ROOT = Path(__file__).parent
# Cards in the order the orchestrator runs them, so the audience reads left to right as it happens.
AGENTS = [("freshness", "Freshness"), ("disclosure", "Disclosure"), ("claim", "Claim"), ("brand_safety", "Brand safety")]
# The demo story in order: free pass, pass after a second lookup, clear fail, human review, hard gate, the wrong pass.
PINNED = ["p001", "p002", "p004", "p005", "p003", "p027"]
# Every verdict and route has a text label as well as a color, so nothing depends on color alone.
VERDICT = {"pass": ":green[**PASS**]", "warn": ":orange[**WARN**]", "fail": ":red[**FAIL**]"}
ROUTE = {"pass": ":green[**PASS**]", "review": ":orange[**HUMAN REVIEW**]", "hold": ":red[**HOLD**]"}
STATE = {"waiting": ":gray[waiting]", "running": ":blue[**running…**]", "done": "**done**",
         "skipped": ":gray[**skipped**]", "not needed": ":gray[**not needed**]"}

# Found in code review, not by a post, so it is listed first and on its own.
DESIGN_LIMIT = ("The claim agent runs only when a cheap keyword rule spots a checkable claim: a $ price, a %, "
                "dupe, cashmere, silk, leather, cures, treats or guaranteed. A false claim outside that list, "
                "such as \"pure wool\", skips the claim check entirely. No post in either set tests this yet.")
LIMITS = {
    "p019": "A banned phrase in a sponsored post goes to review, not hold. One failed check gets averaged up "
            "by three passing ones. Whether a brand-guideline breach should hold a post is an open policy question.",
    "p027": "\"The same yarn the luxury labels use\" is read as enthusiasm because it names no brand. "
            "The line between enthusiasm and a comparison claim is fuzzy.",
    "h09": "An out-of-stock sponsored post goes to review, not hold. Same averaging as p019.",
    "h10": "A guaranteed-result claim gets a claim warn, not a fail. The claim prompt fails only health or medical claims.",
}
HISTORY = ("First live run on the 30-post set, before the step 6 fixes: **25/30**. Measured on the code at commit "
           "36d6172 with the new 30-post data, and recorded in the message of commit ac61e54. "
           "It is a historical figure and cannot be recomputed from the current code.")


def md(text) -> str:
    """Streamlit reads $...$ as math, so escape dollar signs in anything we display."""
    return str(text).replace("$", r"\$")


def money(x) -> str:
    return "none" if x is None else f"${x:.0f}"


def load(path: str):
    return json.loads((ROOT / path).read_text())


def plain(detail: str) -> str:
    """A tool result in plain words for the audience. The raw output stays in the expander."""
    name, _, raw = detail.partition(" -> ")
    if name == "product details":
        return f"Full details: {raw}"
    try:
        d = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return detail
    if name == "product status":
        if d["status"] == "delisted":
            return f"No longer sold (was {money(d['price_at_post'])})"
        stock = "In stock" if d["status"] == "in_stock" else "Out of stock"
        return f"{stock}, {money(d['price_at_post'])} then, {money(d['current_price'])} now"
    if name == "partnership lookup":
        return f"Paid partnership with {d['brand']}" if d["paid"] else "Not a paid post"
    if name == "product record":
        return f"{d['name']} by {d['brand']}, {d['material']}, {money(d['current_price'])} now"
    if name.startswith("guidelines for"):
        return f"{name.removeprefix('guidelines for ')} bans: {', '.join(d['banned_phrases'])}"
    return detail


# ------------------------------------------------------------ screen 1: live agent trace
class TraceView:
    """Draws one run. Feed it events one by one (live now, a recorded trace in step 11), then the outcome."""

    def __init__(self, result_slot):
        self.result = result_slot
        self.strip, self.decisions = st.empty(), []
        self.cards = {}
        for col, (key, title) in zip(st.columns(4), AGENTS):
            box = col.container(border=True)
            box.markdown(f"**{title}**")
            self.cards[key] = {"slot": box.empty(), "state": "waiting", "steps": [], "raw": [], "result": None}
            self.draw(key)

    def start(self, key):
        # Agents run one after another, so when one starts, any running one has finished.
        for k, c in self.cards.items():
            if c["state"] == "running" and k != key:
                c["state"] = "done"
                self.draw(k)
        if self.cards[key]["state"] == "waiting":
            self.cards[key]["state"] = "running"
            self.draw(key)

    def on_event(self, event):
        agent, kind, detail = event["agent"], event["kind"], event["detail"]
        if agent == "orchestrator":
            self.decisions.append(f"- *{kind}*: {md(detail)}")
            self.strip.markdown("**Orchestrator**\n" + "\n".join(self.decisions))
            # The orchestrator announces which agents it runs or skips before they act.
            for key, name in (("claim", "claim agent"), ("brand_safety", "brand safety agent")):
                if f"skip {name}" in detail:
                    self.cards[key]["state"] = "skipped"
                    self.draw(key)
                elif f"run {name}" in detail:
                    self.start(key)
            return
        if kind == "verdict":
            # The engine writes a verdict as "pass (0.97) reason", sent as soon as the agent returns.
            verdict, confidence, reason = detail.split(" ", 2)
            self.complete(agent, {"verdict": verdict, "confidence": float(confidence.strip("()")), "reason": reason})
            return
        self.start(agent)
        card = self.cards[agent]
        card["steps"].append(f"- *{kind}*: {md(plain(detail) if kind == 'tool' else detail)}")
        if kind == "tool":
            card["raw"].append(detail)
        self.draw(agent)

    def draw(self, key):
        c = self.cards[key]
        with c["slot"].container():
            text = STATE[c["state"]] + ("\n\n" + "\n".join(c["steps"]) if c["steps"] else "")
            r = c["result"]
            if r and r["verdict"] != "skipped":
                text += f"\n\n{VERDICT[r['verdict']]} · confidence {r['confidence']:.2f}\n\n{md(r['reason'])}"
            elif r:
                text += f"\n\n{md(r['reason'])}"
            st.markdown(text)
            if c["raw"]:
                with st.expander("Raw tool output"):
                    st.code("\n".join(c["raw"]), language=None)

    def complete(self, key, r):
        c = self.cards[key]
        # A check that looked something up and then found nothing to check was not needed, not skipped.
        c["state"] = ("not needed" if c["steps"] else "skipped") if r["verdict"] == "skipped" else "done"
        c["result"] = r
        self.draw(key)

    def finish(self, outcome, label):
        for key, r in outcome["verdicts"].items():  # fills in any card the events did not complete
            if not self.cards[key]["result"]:
                self.complete(key, r)
        result_panel(self.result, outcome, label)


def result_panel(slot, outcome=None, label=None, status="waiting"):
    """Same four lines before, during and after a run, so the page does not jump when the score appears."""
    with slot.container(border=True):
        if outcome is None:
            st.markdown(f"### Trust score: {status}")
            st.markdown("Decided by: shown when the run ends")
            st.markdown("Reviewer label: shown when the run ends")
            st.caption("Cost: shown when the run ends")
            return
        agrees = "matches the reviewer label" if outcome["route"] == label else "differs from the reviewer label"
        calls = outcome["llm_calls"]
        st.markdown(f"### Trust score {outcome['score']}/100 → {ROUTE[outcome['route']]}")
        st.markdown(f"Decided by: {md(outcome['route_reason'])}")
        st.markdown(f"Reviewer label: **{label}**, {agrees}.")
        st.caption(f"Cost: {calls} LLM call{'' if calls == 1 else 's'} · {outcome['tokens']:,} tokens · "
                   f"{outcome['seconds']:.1f} seconds")


def live_run(post, on_event) -> dict:
    """Event source for a live run. Step 11 adds a replay source that returns the same outcome shape."""
    start = time.time()
    d = score_post(post, on_event=on_event)
    return {
        "score": d["score"], "route": d["route"], "route_reason": d["route_reason"],
        "llm_calls": d["llm_calls"],
        "tokens": sum(u["input_tokens"] + u["output_tokens"] for u in d["llm_usage"]),
        "seconds": time.time() - start,
        "verdicts": {k: {"verdict": r.verdict, "confidence": r.confidence, "reason": r.reason}
                     for k, r in d["results"].items()},
    }


def post_card(post):
    status = tools.get_product_status(post["product_id"])
    record = tools.get_product_record(post["product_id"])
    sponsor = f"paid partnership with {post['sponsor_brand']}" if post["paid_partnership"] else "not sponsored"
    with st.container(border=True):
        st.markdown(f"**{post['creator']}** · {sponsor}\n\n> {md(post['caption'])}\n\n"
                    + md(f"**{record['name']}** ({record['brand']}) · {money(status['price_at_post'])} then, "
                         f"{money(status['current_price'])} now · {status['status'].replace('_', ' ')}"))


def trace_screen(posts):
    ids = PINNED + [i for i in posts if i not in PINNED]
    pid = st.sidebar.selectbox("Creator post", ids,
                               format_func=lambda i: f"{'📌 ' if i in PINNED else ''}{i} {posts[i]['creator']}")
    run = st.sidebar.button("Run trust checks", type="primary")
    st.sidebar.caption("LIVE: real Claude API calls" if llm.live() else "OFFLINE: simple rules stand in for the LLM")
    # Post and result side by side at the top, so the score is on screen without scrolling.
    left, right = st.columns([3, 2])
    with left:
        post_card(posts[pid])
    result_slot = right.empty()
    if not run:
        result_panel(result_slot, status="press Run trust checks in the sidebar")
        return
    result_panel(result_slot, status="running…")
    view = TraceView(result_slot)
    view.finish(live_run(posts[pid], view.on_event), posts[pid]["label"])


# ------------------------------------------------------------ screen 2: with and without
def compare_screen():
    data = load("results/compare.json")
    questions = {q["question_id"]: q for q in data["questions"]}
    qid = st.selectbox("Shopper question", list(questions), format_func=lambda i: questions[i]["question"])
    q = questions[qid]
    st.caption(f"Saved run {data['generated_at']} · model {data['model']} · routes from the eval run of "
               f"{data['routes_from_eval_run']} · retrieval is a keyword match standing in for LTK search")
    st.markdown(f"Retrieved posts: {', '.join(q['retrieved'])}")
    for col, side, title in zip(st.columns(2), ("unfiltered", "filtered"),
                                ("Without the trust layer", "With the trust layer")):
        with col.container(border=True):
            st.markdown(f"#### {title}")
            st.caption(f"Posts given to the model: {', '.join(q[side]['posts_given']) or 'none'}")
            if q[side]["posts_given"]:
                st.markdown(md(q[side]["answer"]))
            else:
                st.info("No trusted posts for this question.")
    st.markdown("#### Left out by the trust layer")
    if not q["excluded"]:
        st.markdown("Nothing was left out. Every retrieved post passed.")
    captions = {p["post_id"]: p["caption"] for p in load("data/posts.json")}
    for e in q["excluded"]:
        st.markdown(f"- **{e['post_id']}** {ROUTE[e['route']]}: {e['reason']}. \"{md(captions[e['post_id']])}\"")


# ------------------------------------------------------------ screen 3: eval panel
def eval_table(s) -> str:
    n, runs = s["posts"], s["per_run"]
    # Identical runs would repeat the same column, so show one and let the stability line say so.
    same = all(r == runs[0] for r in runs)
    shown, heads = (runs[:1], [f"All {len(runs)} runs"]) if same else (runs, [f"Run {i + 1}" for i in range(len(runs))])
    rows = [("Exact route", [f"{r['exact']}/{n}" for r in shown]),
            ("Pass vs not pass", [f"{r['pass_vs_not']}/{n}" for r in shown]),
            ("Wrong passes", [str(r["wrong_passes"]) for r in shown])]
    rows += [(f"Group: {g}", [f"{r['by_group'][g]['correct']}/{r['by_group'][g]['total']}" for r in shown])
             for g in ("clean", "hold", "borderline")]
    lines = ["| | " + " | ".join(heads) + " |", "|---|" + "---|" * len(heads)]
    return "\n".join(lines + [f"| {name} | " + " | ".join(cells) + " |" for name, cells in rows])


def eval_screen():
    data = load("results/eval.json")
    st.caption(f"Saved run {data['generated_at']} · {data['mode']} · {data['runs']} runs per post · "
               f"models {data['models']['small']} and {data['models']['strong']}")
    titles = {"main": ("Main set", "The step 6 fixes were informed by this set."),
              "holdout": ("Holdout", "Written after the fixes. Not used for tuning.")}
    misses = []
    for col, (name, (title, note)) in zip(st.columns(2), titles.items()):
        s = data["sets"][name]["summary"]
        misses += s["misses"]
        c = s["cost_per_post"]
        with col.container(border=True):
            st.markdown(f"#### {title} ({s['posts']} posts)")
            st.caption(note)
            st.markdown(eval_table(s))
            st.markdown(f"**Stability:** route changed on {len(s['route_changed'])} posts, "
                        f"a verdict changed on {len(s['verdict_changed'])}, across {s['runs']} runs.")
            st.markdown(f"**Cost per post (min to max):** {c['llm_calls']['min']}-{c['llm_calls']['max']} LLM calls, "
                        f"{c['tokens']['min']:,}-{c['tokens']['max']:,} tokens, "
                        f"{c['seconds']['min']:.1f}-{c['seconds']['max']:.1f} seconds. "
                        f"{s['zero_llm_posts']} of {s['posts']} posts never call an LLM.")
    st.markdown("#### Known limits")
    st.markdown(f"- **Claim keyword gate** (found in code review): {md(DESIGN_LIMIT)}")
    for m in misses:
        got = ", ".join(sorted(set(m["routes"])))
        st.markdown(f"- **{m['post_id']}** (label {m['label']}, got {got}): {md(LIMITS.get(m['post_id'], 'No note yet.'))}")
    st.info(HISTORY)


# ------------------------------------------------------------ page
st.set_page_config(page_title="Trust Layer for LTK AI", layout="wide")
st.markdown("<style>.block-container {padding-top: 2rem;}</style>", unsafe_allow_html=True)  # keep results above the fold
st.sidebar.title("Trust Layer")
screen = st.sidebar.radio("Screen", ["Live agent trace", "With and without", "Eval panel"])
st.sidebar.caption("All data is synthetic.")
st.subheader(screen)
if screen == "Live agent trace":
    all_posts = {p["post_id"]: p for f in ("data/posts.json", "data/holdout_posts.json") for p in load(f)}
    trace_screen(all_posts)
elif screen == "With and without":
    compare_screen()
else:
    eval_screen()
