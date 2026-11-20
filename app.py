"""RecCheck demo UI: a trust layer for creator recommendations.

    streamlit run app.py

Rule: the UI draws and never decides. All scoring lives in trust_layer/. This file calls
score_post, renders the events it emits, and reads the saved runs in results/.
"""
import ast
import base64
import hmac
import html
import itertools
import json
import time
from pathlib import Path

import streamlit as st

import traces
from phase2_mock import phase2_screen
from trust_layer import llm, tools
from trust_layer.orchestrator import score_post

ROOT = Path(__file__).parent
# Cards in the order the orchestrator runs them, so the audience reads left to right as it happens.
AGENTS = [("freshness", "Freshness"), ("disclosure", "Disclosure"), ("claim", "Claim"), ("brand_safety", "Brand safety")]
# The demo story in order: free pass, pass after a second lookup, clear fail, human review, hard gate, the wrong pass.
PINNED = ["p001", "p002", "p004", "p005", "p003", "p027"]


def badge(text, kind, extra=""):
    """A colored pill styled in ui_style.css. It always carries its text, so nothing depends on color alone."""
    return f'<span class="badge {kind} {extra}">{text}</span>'


VERDICT = {v: badge(v.upper(), v) for v in ("pass", "warn", "fail")}
ROUTE_COLOR = {"pass": "pass", "review": "warn", "hold": "fail"}  # the score number takes the route's color
ROUTE = {"pass": badge("PASS", "pass", "route"), "review": badge("HUMAN REVIEW", "warn", "route"),
         "hold": badge("HOLD", "fail", "route")}
STATE = {"waiting": badge("waiting", "muted"), "running": badge("running…", "running"), "done": badge("done", "done"),
         "skipped": badge("skipped", "muted"), "not needed": badge("not needed", "muted")}
# Container keys become CSS classes (st-key-card-3), the stable hooks the stylesheet targets.
# A counter keeps every key unique within a run.
_keys = itertools.count()


def key(prefix: str) -> str:
    return f"{prefix}-{next(_keys)}"

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


def safe(text) -> str:
    """For text drawn next to HTML badges: escape HTML too, so a caption or LLM reason cannot inject markup.
    The $ becomes an HTML entity, so Streamlit never reads it as math."""
    return html.escape(str(text)).replace("$", "&#36;")


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
        for col, (agent, title) in zip(st.columns(4), AGENTS):
            box = col.container(border=True, key=key("card"))
            box.markdown(f"**{title}**")
            self.cards[agent] = {"slot": box.empty(), "state": "waiting", "steps": [], "raw": [], "result": None}
            self.draw(agent)

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
            # One wrapped line, not a list, so the agent cards below stay above the fold.
            self.decisions.append(f"<i>{kind}</i>: {safe(detail)}")
            self.strip.markdown('<p class="orch-line"><b>Orchestrator</b> · ' + " → ".join(self.decisions) + "</p>",
                                unsafe_allow_html=True)
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
        card["steps"].append(f"- *{kind}*: {safe(plain(detail) if kind == 'tool' else detail)}")
        if kind == "tool":
            card["raw"].append(detail)
        self.draw(agent)

    def draw(self, key):
        c = self.cards[key]
        with c["slot"].container():
            text = STATE[c["state"]] + ("\n\n" + "\n".join(c["steps"]) if c["steps"] else "")
            r = c["result"]
            if r and r["verdict"] != "skipped":
                text += f"\n\n{VERDICT[r['verdict']]} · confidence {r['confidence']:.2f}\n\n{safe(r['reason'])}"
            elif r:
                text += f"\n\n{safe(r['reason'])}"
            st.markdown(text, unsafe_allow_html=True)  # badges are HTML; all other text went through safe()
            if c["raw"]:
                with st.expander("Raw tool output"):
                    st.code("\n".join(c["raw"]), language=None)

    def complete(self, key, r):
        c = self.cards[key]
        # A check that looked something up and then found nothing to check was not needed, not skipped.
        c["state"] = ("not needed" if c["steps"] else "skipped") if r["verdict"] == "skipped" else "done"
        c["result"] = r
        self.draw(key)

    def finish(self, outcome, label, source):
        for key, r in outcome["verdicts"].items():  # fills in any card the events did not complete
            if not self.cards[key]["result"]:
                self.complete(key, r)
        result_panel(self.result, source, outcome, label)


def result_panel(slot, source, outcome=None, label=None, status="waiting"):
    """Same five lines before, during and after a run, so the page does not jump when the score appears."""
    with slot.container(border=True, key=key("result")):
        st.caption(f"Source: {source}")
        if outcome is None:
            shown = badge("running…", "running") if status == "running…" else f'<span class="score-of">{status}</span>'
            st.markdown(f'<div class="score-line"><span class="score-label">Trust score</span>{shown}</div>',
                        unsafe_allow_html=True)
            st.markdown("Decided by: shown when the run ends")
            st.markdown("Reviewer label: shown when the run ends")
            st.caption("Cost: shown when the run ends")
            return
        agrees = "matches the reviewer label" if outcome["route"] == label else "differs from the reviewer label"
        calls = outcome["llm_calls"]
        st.markdown(f'<div class="score-line"><span class="score-label">Trust score</span>'
                    f'<span class="score {ROUTE_COLOR[outcome["route"]]}">{outcome["score"]}</span>'
                    f'<span class="score-of">/100</span>'
                    f'{ROUTE[outcome["route"]]}</div>', unsafe_allow_html=True)
        st.markdown(f"Decided by: {md(outcome['route_reason'])}")
        st.markdown(f"Reviewer label: **{label}**, {agrees}.")
        st.caption(f"Cost: {calls} LLM call{'' if calls == 1 else 's'} · {outcome['tokens']:,} tokens · "
                   f"{outcome['seconds']:.1f} seconds")


def live_run(post, on_event) -> dict:
    """Event source 1: a live run through the engine. Event source 2 is traces.replay, same outcome shape."""
    if not llm.live():
        # Without credentials the engine would quietly use offline rules. That is not a live run.
        raise RuntimeError("no API credentials on this server")
    start = time.time()
    return traces.outcome_of(score_post(post, on_event=on_event), time.time() - start)


def failure_in_words(e: Exception) -> str:
    """Why a live run failed, in plain words for the audience. Matched by name so offline mode needs no SDK."""
    name = type(e).__name__
    if name == "APITimeoutError":
        return f"the Claude API did not answer within {llm.TIMEOUT_SECONDS} seconds"
    if name == "APIConnectionError":
        return "the Claude API could not be reached (no network?)"
    if name.endswith("Error") and hasattr(e, "status_code"):
        return f"the Claude API returned an error (status {e.status_code})"
    if "no API credentials" in str(e):
        return "this server has no API credentials"
    return f"an unexpected error ({name})"


def recorded_label(trace) -> str:
    when = trace["recorded_at"][:16].replace("T", " ")
    return f"Replay of a run recorded on {when} UTC"


def live_mode() -> bool:
    """Replay is the default. Live spends API credits, so it needs the passcode from the secrets store."""
    try:
        expected = st.secrets.get("LIVE_PASSCODE")
    except Exception:  # no secrets file on this machine
        expected = None
    if not expected:
        # No selector at all, so the sidebar can never show Live while the page is in replay.
        st.sidebar.caption("Mode: Replay. Live mode is not set up on this server.")
        return False
    if st.sidebar.radio("Mode", ["Replay", "Live"], horizontal=True) != "Live":
        return False
    code = st.sidebar.text_input("Live passcode", type="password")
    if not code:
        return False
    if hmac.compare_digest(code.encode(), str(expected).encode()):
        return True
    st.sidebar.error("Wrong passcode. Live mode stays locked.")
    return False


def post_card(post):
    status = tools.get_product_status(post["product_id"])
    record = tools.get_product_record(post["product_id"])
    sponsor = f"paid partnership with {post['sponsor_brand']}" if post["paid_partnership"] else "not sponsored"
    tint = f"g{sum(map(ord, post['product_id'])) % 5}"
    icon = load("data/product_icons.json").get(post["product_id"], "🛍️")
    avatar = f'<span class="avatar {tint}">{post["creator"].lstrip("@")[:2].upper()}</span>'
    photo = photo_uri(post["post_id"])
    with st.container(border=True, key=key("post")):
        # A narrow photo column: the card must stay short so the agent cards sit above the fold.
        left, right = st.columns([1, 3], vertical_alignment="center")
        if photo:  # an LTK-style tall photo with the creator's avatar on it, compact on this screen
            left.markdown(f'<div class="post-photo compact"><img src="{photo}" alt="Lifestyle photo of the '
                          f'{safe(record["name"])}">{avatar}</div>', unsafe_allow_html=True)
        else:  # no photo for this post: the illustrated tile
            left.markdown(f'<div class="product-tile {tint}"><span>{icon}</span></div>', unsafe_allow_html=True)
        right.markdown(f'{"" if photo else avatar} **{safe(post["creator"])}** · {sponsor}'
                       f"\n\n> {safe(post['caption'])}\n\n"
                       f'<span class="product-chip">{icon} '
                       + safe(f"**{record['name']}** ({record['brand']}) · {money(status['price_at_post'])} then, "
                              f"{money(status['current_price'])} now · {status['status'].replace('_', ' ')}")
                       + "</span>", unsafe_allow_html=True)


@st.cache_data
def photo_uri(post_id: str):
    """The post's photo as a data URI, so it shows offline in replay. None when the post has no photo.
    Photos are AI-generated stand-ins (prompts in assets/posts/IMAGE_PROMPTS.md), not real creator photos."""
    path = ROOT / "assets" / "posts" / f"{post_id}.jpg"
    return f"data:image/jpeg;base64,{base64.b64encode(path.read_bytes()).decode()}" if path.exists() else None


def trace_screen(posts):
    ids = PINNED + [i for i in posts if i not in PINNED]
    pid = st.sidebar.selectbox("Creator post", ids,
                               format_func=lambda i: f"{'📌 ' if i in PINNED else ''}{i} {posts[i]['creator']}")
    live = live_mode()
    run = st.sidebar.button("Run trust checks", type="primary")
    post, trace = posts[pid], traces.load(pid)
    recorded = recorded_label(trace) if trace else None

    # The mode is always on screen, so a recording never looks like a live run.
    banner = st.empty()
    if live:
        banner.success("**Live**: real Claude API calls, happening now.")
    elif trace:
        banner.info(f"**{recorded}.** No API calls. Models: {trace['models']['small']}, {trace['models']['strong']}.")
    else:
        banner.warning("No recorded run for this post. Unlock live mode to run it.")
    source = "Live run" if live else (recorded or "none")

    # Post and result side by side at the top, so the score is on screen without scrolling.
    left, right = st.columns([3, 2])
    with left:
        post_card(post)
    result_slot = right.empty()
    if not run or not (live or trace):
        result_panel(result_slot, source, status="press Run trust checks in the sidebar")
        return
    result_panel(result_slot, source, status="running…")

    area = st.empty()
    if live:
        failed = None
        with area.container():
            view = TraceView(result_slot)
            try:
                outcome = live_run(post, view.on_event)
            except Exception as e:  # API error, timeout or no network: fall back to the recording
                failed = failure_in_words(e)
        if not failed:
            view.finish(outcome, post["label"], source)
            return
        area.empty()
        if not trace:
            banner.error(f"**Live run failed:** {failed}, and this post has no recording.")
            result_panel(result_slot, "none", status="live run failed")
            return
        banner.warning(f"**Live run failed:** {failed}. Showing the recording instead. **{recorded}.**")
    with area.container():
        view = TraceView(result_slot)
        view.finish(traces.replay(trace, view.on_event), post["label"], recorded)


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
        with col.container(border=True, key=key("nofilter" if side == "unfiltered" else "filtered")):
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
        st.markdown(f"- **{e['post_id']}** {ROUTE[e['route']]} {safe(e['reason'])}. \"{safe(captions[e['post_id']])}\"",
                    unsafe_allow_html=True)


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
        with col.container(border=True, key=key("evalbox")):
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
st.set_page_config(page_title="RecCheck", layout="wide")
st.markdown(f"<style>{(ROOT / 'ui_style.css').read_text()}</style>", unsafe_allow_html=True)  # the look, see ui_style.css
st.sidebar.title("RecCheck")
st.sidebar.caption("A trust layer for creator recommendations")
screen = st.sidebar.radio("Screen", ["Agent trace", "With and without", "Eval panel", "Phase 2 mock"])
st.sidebar.caption("All data is synthetic. Post photos are AI-generated stand-ins.")
st.subheader(screen)
if screen == "Agent trace":
    all_posts = {p["post_id"]: p for f in ("data/posts.json", "data/holdout_posts.json") for p in load(f)}
    trace_screen(all_posts)
elif screen == "With and without":
    compare_screen()
elif screen == "Eval panel":
    eval_screen()
else:
    phase2_screen()
