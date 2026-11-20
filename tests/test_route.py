"""Unit tests for the routing rules. No LLM calls: each test builds the agent results by hand.

    python3 -m unittest -v
"""
import unittest

from trust_layer.agents import CheckResult
from trust_layer.orchestrator import _route, _trust_score


def post(paid=False):
    return {"paid_partnership": paid, "sponsor_brand": "Brand" if paid else None}


def results(claim="pass", freshness="pass", brand_safety="pass", disclosure="pass",
            confidence=1.0, flags=None):
    """All four checks, each passing unless told otherwise."""
    return {
        "claim": CheckResult("claim", claim, confidence, ""),
        "freshness": CheckResult("freshness", freshness, 1.0, "", flags=flags or []),
        "brand_safety": CheckResult("brand_safety", brand_safety, 1.0, ""),
        "disclosure": CheckResult("disclosure", disclosure, 1.0, ""),
    }


def route(p, r):
    return _route(p, r, _trust_score(r))[0]


class HardGates(unittest.TestCase):
    def test_paid_post_with_no_disclosure_is_held(self):
        # Scores 85 on its own, so only the gate can hold it.
        self.assertEqual(route(post(paid=True), results(disclosure="fail")), "hold")

    def test_delisted_product_is_held(self):
        r = results(freshness="fail", flags=["delisted", "phase2"])
        self.assertEqual(route(post(), r), "hold")


class CapsOnPass(unittest.TestCase):
    def test_any_fail_blocks_a_pass(self):
        r = results(brand_safety="fail")
        self.assertEqual(_trust_score(r), 80)  # would pass on score alone
        self.assertEqual(route(post(paid=True), r), "review")

    def test_claim_warn_blocks_a_pass(self):
        r = results(claim="warn")
        self.assertEqual(_trust_score(r), 80)
        self.assertEqual(route(post(), r), "review")

    def test_disclosure_warn_on_paid_post_blocks_a_pass(self):
        r = results(disclosure="warn")
        self.assertGreaterEqual(_trust_score(r), 80)
        self.assertEqual(route(post(paid=True), r), "review")


class ScoreBands(unittest.TestCase):
    def test_80_and_above_passes(self):
        self.assertEqual(route(post(), results()), "pass")

    def test_50_to_79_goes_to_review(self):
        r = results(freshness="warn", brand_safety="warn")  # 77.5, rounds to 78
        self.assertEqual(_trust_score(r), 78)
        self.assertEqual(route(post(), r), "review")

    def test_below_50_is_held(self):
        r = results(claim="fail", freshness="warn")  # 47.5, rounds to 48
        self.assertEqual(_trust_score(r), 48)
        self.assertEqual(route(post(), r), "hold")

    def test_low_confidence_goes_to_review_whatever_the_score(self):
        self.assertEqual(route(post(), results(confidence=0.5)), "review")


if __name__ == "__main__":
    unittest.main()
