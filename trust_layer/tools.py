"""Mock tools the agents can call. In production these would be LTK services."""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
_products = json.loads((DATA / "products.json").read_text())
_brands = json.loads((DATA / "brands.json").read_text())


def get_product_status(product_id: str) -> dict:
    """Stock status and price, then and now."""
    p = _products[product_id]
    return {k: p[k] for k in ("status", "price_at_post", "current_price")}


def get_product_record(product_id: str) -> dict:
    """The short catalog record: name, brand, price, material."""
    p = _products[product_id]
    return {k: p[k] for k in ("name", "brand", "retailer", "current_price", "material")}


def get_product_details(product_id: str) -> str:
    """The longer retailer description. Used as the claim agent's second lookup."""
    return _products[product_id]["details"]


def get_partnership(post: dict) -> dict:
    """Whether the post is paid, and by which brand."""
    return {"paid": post["paid_partnership"], "brand": post["sponsor_brand"]}


def get_brand_guidelines(brand: str):
    """The brand's content rules, or None if the brand has not supplied any."""
    return _brands.get(brand)
