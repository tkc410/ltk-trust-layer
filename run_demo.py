"""Run the trust layer over the sample posts and print each trace.

    python run_demo.py           # all posts
    python run_demo.py p004      # one post
"""
import json
import sys
from pathlib import Path

from trust_layer import llm
from trust_layer.orchestrator import score_post

posts = json.loads((Path(__file__).parent / "data" / "posts.json").read_text())
if len(sys.argv) > 1:
    posts = [p for p in posts if p["post_id"] == sys.argv[1]]

print("Mode:", "LIVE (Claude API)" if llm.live() else "OFFLINE (rule stand-ins, no API key set)")
correct = 0
for post in posts:
    print(f"\n=== {post['post_id']} {post['creator']}\n    \"{post['caption']}\"")
    decision = score_post(post, on_event=lambda e: print(f"  [{e['agent']:<12}] {e['kind']:<7} {e['detail']}"))
    match = decision["route"] == post["label"]
    correct += match
    print(f"  => score {decision['score']}, route {decision['route'].upper()}, "
          f"LLM calls {decision['llm_calls']}, phase 2 candidate: {decision['phase2_candidate']}, "
          f"expected {post['label']} {'OK' if match else 'MISMATCH'}")

print(f"\n{correct}/{len(posts)} routes match the labels")
