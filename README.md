# Trust Layer for LTK AI (demo)

Scores a creator post on four signals before it is allowed to ground an AI answer.
All data is synthetic.

## Run it

    pip install -r requirements.txt
    streamlit run app.py         # the demo UI, opens in replay mode

The UI has three screens: the agent trace, the with-and-without comparison, and the eval panel.

### Replay mode (the default)

The agent trace plays a run recorded earlier, from `results/traces/`, through the same drawing
code as a live run. It makes no API calls and needs no key. The page always says
"Replay of a run recorded on [date]", so a recording never looks like a live run.

### Live mode (needs a passcode)

Live mode makes real Claude API calls, so it is locked behind a passcode.

1. Credentials: `ant auth login`, or `export ANTHROPIC_API_KEY=...`.
2. Passcode: create `.streamlit/secrets.toml` with one line, `LIVE_PASSCODE = "pick-your-own"`.
   That file is gitignored. On a hosting service, put `LIVE_PASSCODE` (and the API key) in the
   service's secrets store, never in the repo.
3. In the sidebar, choose Live and enter the passcode.

Without a configured passcode the Mode selector does not appear and the app stays in replay.
If a live call fails or times out, the page falls back to that post's recording and says so.

### Command line

    python3 run_demo.py           # all main posts, with the trace printed
    python3 run_demo.py p004      # one post
    python3 run_demo.py holdout   # the holdout set
    python3 eval.py               # both sets, 3 runs each, saved to results/eval.json
    python3 compare.py            # with and without, saved to results/compare.json
    python3 traces.py             # re-record every post's trace (live, about 43 LLM calls)
    python3 -m unittest -v        # unit tests for the routing rules

Without credentials, `run_demo.py` and `eval.py` run in OFFLINE mode, where simple rules stand in
for the LLM. `compare.py` and `traces.py` refuse to run offline.
Models are set with TRUST_SMALL_MODEL and TRUST_STRONG_MODEL (see trust_layer/llm.py).

## Files, in reading order

| File | What it holds |
|---|---|
| data/*.json | Posts, holdout posts, products, brand guidelines, shopper questions |
| trust_layer/tools.py | Mock lookups the agents call |
| trust_layer/llm.py | The Claude API wrapper and the two model tiers |
| trust_layer/agents.py | The four agents: disclosure, freshness, claim, brand safety |
| trust_layer/orchestrator.py | Which agents run, hard gates, trust score, route |
| run_demo.py | Runs posts and prints the trace |
| eval.py | Accuracy, stability and cost over both labeled sets |
| compare.py | The same shopper questions answered with and without the trust layer |
| traces.py | Records traces and replays them |
| app.py | The Streamlit UI. It draws and never decides. |
| tests/test_route.py | Unit tests for the hard gates, caps and score bands |

## Status

Built: agents, orchestrator, scoring, 30 labeled posts and 10 holdout posts, eval script,
with-and-without comparison, UI, recorded traces with replay and a passcode for live mode.
Not built yet: Phase 2 mock.

Labels are what a careful reviewer would decide, not what the rules predict. The holdout set was
written after the scoring fixes and was not used for tuning.
