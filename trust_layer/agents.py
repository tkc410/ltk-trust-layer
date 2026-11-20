"""The four check agents. Each returns a CheckResult in the same shape."""
import json
import re
from dataclasses import dataclass, field

from . import llm, tools

DISCLOSURE_TAGS = ("#ad", "#sponsored", "#paidpartnership", "#ltkpartner")
SOFT_DISCLOSURE = ("partnered with", "gifted", "thank you", "thanks to", "collab")
CLAIM_PATTERN = re.compile(
    r"\$\d+|\d+\s?%|\bdupe\b|\bcashmere\b|\bsilk\b|\bleather\b|\bcures?\b|\btreats?\b|\bguaranteed\b",
    re.IGNORECASE,
)
PRICE_MOVE_LIMIT = 0.20  # a price change above 20% is worth a warning


@dataclass
class CheckResult:
    agent: str
    verdict: str            # pass | warn | fail | skipped
    confidence: float       # 0 to 1
    reason: str
    evidence: list = field(default_factory=list)
    llm_usage: list = field(default_factory=list)  # one entry per LLM call
    flags: list = field(default_factory=list)      # e.g. "delisted", "phase2"


def has_checkable_claim(caption: str) -> bool:
    """Cheap rule the orchestrator uses to decide if the claim agent is needed."""
    return bool(CLAIM_PATTERN.search(caption))


# ---------------------------------------------------------------- disclosure
def disclosure_agent(post: dict, emit) -> CheckResult:
    partnership = tools.get_partnership(post)
    emit("disclosure", "tool", f"partnership lookup -> {partnership}")
    if not partnership["paid"]:
        return CheckResult("disclosure", "skipped", 1.0, "Not a paid post, no disclosure needed.", [partnership])

    caption = post["caption"].lower()
    tag = next((t for t in DISCLOSURE_TAGS if t in caption), None)
    if tag:
        emit("disclosure", "rule", f"found clear tag {tag}, no LLM needed")
        return CheckResult("disclosure", "pass", 1.0, f"Paid post carries {tag}.", [partnership, tag])

    # No clear tag. Only now is the LLM worth calling: is there disclosure in plain words?
    emit("disclosure", "decide", "paid post with no clear tag, checking wording")
    if llm.live():
        answer, usage = llm.ask_json(
            llm.SMALL_MODEL,
            "You check whether a paid creator post clearly discloses the brand relationship to a shopper.",
            f"Caption: {post['caption']}\nSponsor: {partnership['brand']}\n"
            "pass = clearly disclosed in words, warn = hinted but unclear, fail = not disclosed.",
        )
        return CheckResult("disclosure", answer["verdict"], answer["confidence"], answer["reason"], [partnership], [usage])

    if any(s in caption for s in SOFT_DISCLOSURE):
        return CheckResult("disclosure", "warn", 0.5, "Wording hints at a partnership but has no clear tag. (offline rule)", [partnership])
    return CheckResult("disclosure", "fail", 0.9, "Paid post with no disclosure. (offline rule)", [partnership])


# ----------------------------------------------------------------- freshness
def freshness_agent(post: dict, emit) -> CheckResult:
    status = tools.get_product_status(post["product_id"])
    emit("freshness", "tool", f"product status -> {status}")

    if status["status"] == "delisted":
        return CheckResult("freshness", "fail", 1.0, "Product is no longer sold.", [status], flags=["delisted", "phase2"])
    if status["status"] == "out_of_stock":
        return CheckResult("freshness", "fail", 1.0, "Product is out of stock.", [status], flags=["phase2"])

    move = abs(status["current_price"] - status["price_at_post"]) / status["price_at_post"]
    if move > PRICE_MOVE_LIMIT:
        return CheckResult("freshness", "warn", 1.0, f"Price moved {move:.0%} since the post.", [status])
    return CheckResult("freshness", "pass", 1.0, "In stock, price unchanged.", [status])


# --------------------------------------------------------------------- claim
def claim_agent(post: dict, emit) -> CheckResult:
    record = tools.get_product_record(post["product_id"])
    emit("claim", "tool", f"product record -> {record}")
    evidence, usage_log = [record], []

    if llm.live():
        system = (
            "You check whether the factual claims in a creator caption (price, material, "
            "comparison, benefit) are supported by the product data. Enthusiasm and opinion "
            "are not claims.\n"
            "fail = the data contradicts a claim, or the caption makes a health or medical benefit claim.\n"
            "warn = a claim cannot be checked against the data and a shopper would rely on it, "
            "such as a comparison to another brand's product.\n"
            "pass = every checkable claim matches the data.\n"
            # Why: without this, the model warns on details the short record simply omits.
            "If the full product description could settle a claim, return \"needs_more\": true "
            "instead of warning."
        )
        prompt = f"Caption: {post['caption']}\nProduct data: {json.dumps(record)}"
        answer, usage = llm.ask_json(llm.STRONG_MODEL, system, prompt)
        usage_log.append(usage)
        if answer.get("needs_more"):
            # The agent's own decision: ask for the longer description and try once more.
            details = tools.get_product_details(post["product_id"])
            emit("claim", "decide", "record unclear, requesting full product details")
            emit("claim", "tool", f"product details -> {details}")
            evidence.append(details)
            answer, usage = llm.ask_json(llm.STRONG_MODEL, system, prompt + f"\nFull details: {details}")
            usage_log.append(usage)
        return CheckResult("claim", answer["verdict"], answer["confidence"], answer["reason"], evidence, usage_log)

    return _claim_offline(post, record, evidence, emit)


def _claim_offline(post, record, evidence, emit) -> CheckResult:
    """Simple stand-in for the LLM so the pipeline runs without an API key."""
    caption = post["caption"].lower()
    price = re.search(r"\$(\d+)", caption)
    if price and record["current_price"] and record["current_price"] > float(price.group(1)) * 1.05:
        return CheckResult("claim", "fail", 0.9, f"Caption says ${price.group(1)}, product is now ${record['current_price']:.0f}. (offline rule)", evidence)
    for material in ("cashmere", "silk", "leather"):
        if material in caption and material not in record["material"].replace("faux leather", ""):
            details = tools.get_product_details(post["product_id"])
            emit("claim", "decide", "material claim, requesting full product details")
            emit("claim", "tool", f"product details -> {details}")
            evidence.append(details)
            return CheckResult("claim", "fail", 0.9, f"Caption says {material}, product is {record['material']}. (offline rule)", evidence)
    if re.search(r"\bcures?\b|\btreats?\b|\bguaranteed\b", caption):
        return CheckResult("claim", "fail", 0.8, "Benefit claim the product data does not support. (offline rule)", evidence)
    if "dupe" in caption:
        return CheckResult("claim", "warn", 0.5, "Comparison to another brand cannot be verified from product data. (offline rule)", evidence)
    return CheckResult("claim", "pass", 0.8, "Claims match the product data. (offline rule)", evidence)


# -------------------------------------------------------------- brand safety
def brand_safety_agent(post: dict, emit) -> CheckResult:
    brand = post["sponsor_brand"]
    guidelines = tools.get_brand_guidelines(brand)
    emit("brand_safety", "tool", f"guidelines for {brand} -> {guidelines}")

    if llm.live():
        answer, usage = llm.ask_json(
            llm.SMALL_MODEL,
            "You check whether a sponsored creator caption follows the sponsoring brand's content guidelines.",
            f"Caption: {post['caption']}\nGuidelines: {json.dumps(guidelines)}",
        )
        return CheckResult("brand_safety", answer["verdict"], answer["confidence"], answer["reason"], [guidelines], [usage])

    hit = next((p for p in guidelines["banned_phrases"] if p in post["caption"].lower()), None)
    if hit:
        return CheckResult("brand_safety", "fail", 0.9, f'Uses banned phrase "{hit}". (offline rule)', [guidelines])
    return CheckResult("brand_safety", "pass", 0.8, "No guideline violations found. (offline rule)", [guidelines])
