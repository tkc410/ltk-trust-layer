# Trust Layer for LTK AI (demo)

Scores a creator post on four signals before it is allowed to ground an AI answer.
All data is synthetic.

## Run it

    python run_demo.py           # all sample posts
    python run_demo.py p004      # one post

Without an API key it runs in OFFLINE mode, where simple rules stand in for the LLM.
For LIVE mode:

    pip install -r requirements.txt
    ant auth login                  # or: export ANTHROPIC_API_KEY=...
    python run_demo.py

Models are set with TRUST_SMALL_MODEL and TRUST_STRONG_MODEL (see trust_layer/llm.py).

## Files, in reading order

| File | What it holds |
|---|---|
| data/*.json | Sample posts, products and brand guidelines |
| trust_layer/tools.py | Mock lookups the agents call |
| trust_layer/llm.py | The Claude API wrapper and the two model tiers |
| trust_layer/agents.py | The four agents: disclosure, freshness, claim, brand safety |
| trust_layer/orchestrator.py | Which agents run, hard gates, trust score, route |
| run_demo.py | Runs posts and prints the live trace |

## Status

Built: agents, orchestrator, scoring, 8 sample posts.
Not built yet: full 30-post labeled set, eval script, with/without comparison, UI, recorded traces, Phase 2 mock.
