# Trust Layer for LTK AI (demo)

Make sure every creator post that feeds an AI answer has earned its place there.

The Trust Layer scores a creator post on four signals (claim, freshness, disclosure, brand safety)
before it is allowed to ground an AI answer. Strong posts pass, weak ones are held back, and the
uncertain ones go to a human reviewer. All data is synthetic, and the post photos are AI-generated
stand-ins.

## Built and designed

| | Status | Where |
|---|---|---|
| Phase 1: Trust Layer | **Built**, working code with real Claude API calls | `trust_layer/`, `app.py` |
| Phase 2: Out-of-Stock Recovery | **Designed**, shown as one mock screen. No logic. | `phase2_mock.py` |

## How it works

1. **The orchestrator runs cheap checks first.** Freshness and the disclosure tag check are rules, so
   they're free. The disclosure LLM step runs only when a paid post has no clear tag. The claim agent
   runs only when the caption has a checkable claim, and brand safety only on sponsored posts that
   have guidelines. A plain post costs zero LLM calls.
2. **Four agents.** Each returns a verdict (pass, warn or fail), a confidence, a one-line reason and the
   evidence it used.

   | Agent | Tools (mock lookups) | LLM |
   |---|---|---|
   | Freshness | Product status | None, rules only |
   | Disclosure | Partnership lookup | Small model, only if there's no clear tag |
   | Claim | Product record, plus a second lookup if the record is unclear | Strong model |
   | Brand safety | Brand guidelines | Small model |

3. **Hard gates first.** A paid post with no disclosure is held. A delisted product is held.
4. **Trust score, 0 to 100.** The weights are claim 40, freshness 25, brand safety 20 and disclosure 15.
   A skipped check's weight is spread over the others.
5. **Routes.**
   - 80 and above passes, 50 to 79 goes to human review, and below 50 is held.
   - Any confidence under 0.6 goes to review.
   - A pass is capped to review by any fail, a claim warn, or a disclosure warn on a paid post.

The weights and thresholds are starting assumptions, not tuned values. In production they would come from
labeled data and reviewer feedback.

Models: `claude-haiku-5-5` (small) and `claude-sonnet-5-5` (strong), set in `trust_layer/llm.py`.

## Results (saved live run, 2026-11-20, 3 runs per post)

From `results/eval.json`:

| Set | Correct route | Wrong passes | Stable across runs |
|---|---|---|---|
| Main, 30 posts | 28/30 every run | 1 (p027) | Yes, no route or verdict changed |
| Holdout, 10 posts | 8/10 every run | 0 | Yes |

- **Cost per post:** 0 to 3 LLM calls, up to 1,821 tokens and 9.2 seconds. 10 of the 30 main posts never
  call an LLM.
- **How the numbers were made:** the main set was 25/30 on the first live run, before fixes informed by
  that set. The holdout set was written after those fixes and was never used for tuning, so quote 8/10.
- **What they show:** labels are reviewer judgment, not rule output. 40 synthetic posts show the method;
  they don't prove accuracy.

From `results/compare.json` (the same shopper questions answered without and with the filter):

- **Without the filter**, the answer repeated a false cashmere claim, two medical claims and a wrong price.
- **With the filter**, those posts were left out.
- **The control question** gave the same picks both ways.

## Known limits (kept on purpose, not tuned away)

- **Claim keyword gate.** The claim agent runs only when a keyword rule spots a checkable claim. A false
  claim outside the list ("pure wool") skips the check.
- **One failed check gets averaged up.** A banned phrase (p019) or an out-of-stock sponsored post (h09)
  lands in review, not hold. This is an open policy question: should one fail hold a post?
- **Enthusiasm versus comparison is fuzzy.** "Same yarn the luxury labels use" (p027) is read as enthusiasm
  because it names no brand, so the post passes.
- **Guaranteed-result claims get a warn, not a fail (h10).** Only health or medical claims fail.

## Phase 2 design: Out-of-Stock Recovery (not built)

- **Trigger:** freshness finds a linked product out of stock or delisted.
- **Matching, in tier order:**
  1. The same product at another partnered retailer. Swapped automatically, and the creator is notified.
  2. The same line in another variant, such as another color. Needs creator approval.
  3. A similar product within about 20% of the price. Needs creator approval.

  Shoppers see tier 2 and 3 substitutes labeled "similar".
- **Sponsored posts** are never swapped without the brand's approval.
- **Guardrails:**
  - The substitute must pass the trust checks itself. A polyester tank can't replace one the caption
    calls cotton.
  - The creator's ID carries over to the new link.
- **Estimated recovered commission:** clicks on the dead link over 30 days × conversion rate × average
  order value × the new retailer's commission rate. In the mock, every input is labeled illustrative.
  None of them are LTK numbers.
- **How to measure it:** the share of dead links recovered, the creator approval rate, and click-through
  on substitutes versus original links.

## Run it

    pip install -r requirements.txt
    streamlit run app.py         # the demo UI, opens in replay mode

Screens:
- **Agent trace:** the main screen.
- **With and without:** the comparison above.
- **Eval panel:** the results and known limits.
- **Phase 2 mock:** the design screen.

### Replay mode (the default)

The agent trace plays a run recorded earlier, from `results/traces/` (all 40 posts), through the same
drawing code as a live run. It makes no API calls and needs no key. The page always says "Replay of a run
recorded on [date]", so a recording never looks like a live run.

### Live mode (needs a passcode)

Live mode makes real Claude API calls, so it is locked behind a passcode.

1. Credentials: `ant auth login`, or `export ANTHROPIC_API_KEY=...`.
2. Passcode: create `.streamlit/secrets.toml` with one line, `LIVE_PASSCODE = "pick-your-own"`.
   That file is gitignored. On a hosting service, put `LIVE_PASSCODE` (and the API key) in the service's
   secrets store, never in the repo.
3. In the sidebar, choose Live and enter the passcode.

Without a configured passcode, the Mode selector doesn't appear and the app stays in replay.

If a live call fails, the page falls back to that post's recording and says why in plain words. Each API
call gives up after 15 seconds, with retries off.

There is no limit on passcode attempts, so pick a long passcode and set a low spending limit on the API key.

### Command line

    python3 run_demo.py           # all main posts, with the trace printed
    python3 run_demo.py p004      # one post
    python3 run_demo.py holdout   # the holdout set
    python3 eval.py               # both sets, 3 runs each, saved to results/eval.json
    python3 compare.py            # with and without, saved to results/compare.json
    python3 traces.py             # re-record every post's trace (live, about 43 LLM calls)
    python3 -m unittest -v        # unit tests for the routing rules

Without credentials, `run_demo.py` and `eval.py` run in OFFLINE mode, where simple rules stand in for the
LLM. `compare.py` and `traces.py` refuse to run offline.

The models can be changed with the TRUST_SMALL_MODEL and TRUST_STRONG_MODEL environment variables.

## Files, in reading order

| File | What it holds |
|---|---|
| data/*.json | Posts, holdout posts, products, brand guidelines, shopper questions, the Phase 2 mock scenario |
| trust_layer/tools.py | Mock lookups the agents call |
| trust_layer/llm.py | The Claude API wrapper, the two model tiers and the timeout |
| trust_layer/agents.py | The four agents: disclosure, freshness, claim, brand safety |
| trust_layer/orchestrator.py | Which agents run, hard gates, trust score, route |
| run_demo.py | Runs posts and prints the trace |
| eval.py | Accuracy, stability and cost over both labeled sets |
| compare.py | The same shopper questions answered with and without the trust layer |
| traces.py | Records traces and replays them |
| app.py | The Streamlit UI. It draws and never decides. |
| ui_style.css | The UI's look: light theme, solid colors, motion off for reduced-motion users |
| phase2_mock.py | The Phase 2 mock screen, drawn from data/phase2_mock.json. No logic. |
| tests/test_route.py | Unit tests for the hard gates, caps and score bands |
| assets/posts/ | AI-generated stand-in photos, one per post (prompts in IMAGE_PROMPTS.md) |
