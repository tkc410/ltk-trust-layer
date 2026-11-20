"""Phase 2 mock screen: Out-of-Stock Recovery. A design, not working code.

Everything shown comes from data/phase2_mock.json (the scenario) and data/products.json
(the linked products). No LLM calls, no matching logic, and nothing in trust_layer/ is used.
The buttons are disabled on purpose: the screen shows what the creator would see, nothing more.
"""
import base64
import html
import json
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
WHO_DECIDES = {1: "Swapped automatically, creator notified",
               2: "Needs creator approval. Shoppers see it labeled \"similar\".",
               3: "Needs creator approval. Shoppers see it labeled \"similar\"."}


def safe(text) -> str:
    """Escape HTML, and turn $ into an entity so Streamlit never reads it as math."""
    return html.escape(str(text)).replace("$", "&#36;")


def pill(text, kind) -> str:
    """Same badge styles as the trace screen (ui_style.css)."""
    return f'<span class="badge {kind}">{safe(text)}</span>'


def phase2_screen():
    mock = json.loads((ROOT / "data" / "phase2_mock.json").read_text())
    products = json.loads((ROOT / "data" / "products.json").read_text())
    post, cand, rej, c = mock["post"], mock["candidate"], mock["rejected"], mock["commission"]
    dead = products[post["dead"]]

    st.warning("**Phase 2 design, not built.** A mock of the next phase. Nothing on this screen runs, "
               "and every number is illustrative.")

    left, right = st.columns([2, 3])
    with left, st.container(border=True, key="post-phase2"):
        photo = ROOT / "assets" / "posts" / f"{post['photo']}.jpg"
        if photo.exists():
            uri = base64.b64encode(photo.read_bytes()).decode()
            st.markdown(f'<div class="post-photo"><img src="data:image/jpeg;base64,{uri}" '
                        f'alt="Lifestyle photo of the outfit"></div>', unsafe_allow_html=True)
        st.markdown(f"**{safe(post['creator'])}** · not sponsored\n\n> {safe(post['caption'])}",
                    unsafe_allow_html=True)
        st.markdown("**Linked products**")
        for pid in post["linked"]:
            p = products[pid]
            flag = pill("OUT OF STOCK", "fail") if pid == post["dead"] else pill("in stock", "pass")
            st.markdown(f'<span class="product-chip">{safe(p["name"])} · {safe(f"${p["price_at_post"]:.0f}")}'
                        f"</span> {flag}", unsafe_allow_html=True)

    with right:
        with st.container(border=True, key="card-phase2"):
            st.markdown(f"#### Substitute finder {pill('done', 'done')}", unsafe_allow_html=True)
            st.markdown(f"Freshness flagged **{safe(dead['name'])}** as out of stock. Searching in tier order:")
            # Tiers are tried cheapest and safest first, the same cheap-first idea as the orchestrator.
            st.markdown("\n".join(f"- Tier {s['tier']}: {safe(s['looked_for'])} → {safe(s['found'])}"
                                  for s in mock["search"]), unsafe_allow_html=True)

            diff = cand["price"] - dead["price_at_post"]
            st.markdown(f"**Candidate:** {safe(cand['name'])} ({safe(cand['brand'])}, {safe(cand['retailer'])}) "
                        f"{pill(f'TIER {cand['tier']}', 'warn')}", unsafe_allow_html=True)
            st.markdown(f"{safe(cand['tier_name'])}. Price {safe(f'${cand['price']:.0f}')}, "
                        f"{'same as' if diff == 0 else safe(f'${diff:+.0f} vs')} the original.",
                        unsafe_allow_html=True)
            st.markdown("**Why it matched**\n" + "\n".join(f"- {safe(w)}" for w in cand["why"]),
                        unsafe_allow_html=True)
            # Guardrail: a substitute must pass the trust checks itself. This is the link back to Phase 1.
            st.markdown(f"{pill('REJECTED', 'fail')} {safe(rej['name'])} ({safe(rej['material'])}, "
                        f"tier {rej['tier']}): {safe(rej['why'])}", unsafe_allow_html=True)
            st.markdown(f"**Who decides:** {safe(WHO_DECIDES[cand['tier']])} The creator's ID stays on the "
                        f"new link, so the commission still goes to them.", unsafe_allow_html=True)
            a, d = st.columns(2)
            a.button("Approve", disabled=True, use_container_width=True, help="Mock only, does nothing")
            d.button("Decline", disabled=True, use_container_width=True, help="Mock only, does nothing")

        with st.container(border=True, key="result-phase2"):
            recovered = (c["clicks_30_days"] * c["conversion_rate"] * c["average_order_value"]
                         * c["commission_rate"])
            st.markdown("#### Estimated recovered commission, per 30 days")
            st.markdown(f'<div class="score-line"><span class="score pass">{safe(f"${recovered:,.0f}")}</span>'
                        f'<span class="score-of">illustrative</span></div>', unsafe_allow_html=True)
            st.markdown(
                f"Clicks on the dead link, last 30 days: **{c['clicks_30_days']:,}** *(illustrative)*  \n"
                f"× conversion rate: **{c['conversion_rate']:.0%}** *(illustrative)*  \n"
                f"× average order value: **{safe(f'${c['average_order_value']:.0f}')}** *(illustrative)*  \n"
                f"× new retailer's commission rate: **{c['commission_rate']:.0%}** *(illustrative)*",
                unsafe_allow_html=True)
            st.caption("Placeholders, not LTK numbers. Measured in production by: share of dead links "
                       "recovered, creator approval rate, and click-through on substitutes versus original links.")
