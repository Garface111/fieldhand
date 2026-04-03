"""
Live material price lookup.

Strategy:
  1. Try Home Depot product search API (unofficial, scrape-based)
  2. Fall back to a curated price table for common electrical/plumbing/HVAC items
  3. Always return confidence level so the agent can caveat hallucinated prices

The real commercial path for production:
  - Home Depot Pro API (requires B2B account)
  - Platt Electric API (electrical wholesale)
  - Waxman/WESCO API
  - Grainger catalog API

For now: scrape Home Depot search + fallback table.
"""
import re
import json
import httpx
from typing import Optional


# ── Fallback price table ──────────────────────────────────────────────
# Common items with typical contractor pricing (mid-2024 US averages)
FALLBACK_PRICES = {
    # Electrical
    "romex 12/2": {"price": 1.15, "unit": "per ft", "vendor": "estimate"},
    "romex 14/2": {"price": 0.75, "unit": "per ft", "vendor": "estimate"},
    "romex 10/2": {"price": 1.65, "unit": "per ft", "vendor": "estimate"},
    "romex 12/3": {"price": 1.50, "unit": "per ft", "vendor": "estimate"},
    "single pole breaker 15a": {"price": 8.50, "unit": "each", "vendor": "estimate"},
    "single pole breaker 20a": {"price": 9.25, "unit": "each", "vendor": "estimate"},
    "double pole breaker 20a": {"price": 14.50, "unit": "each", "vendor": "estimate"},
    "double pole breaker 30a": {"price": 16.00, "unit": "each", "vendor": "estimate"},
    "double pole breaker 50a": {"price": 22.00, "unit": "each", "vendor": "estimate"},
    "200 amp panel": {"price": 185.00, "unit": "each", "vendor": "estimate"},
    "100 amp panel": {"price": 95.00, "unit": "each", "vendor": "estimate"},
    "gfci outlet": {"price": 16.50, "unit": "each", "vendor": "estimate"},
    "afci breaker": {"price": 42.00, "unit": "each", "vendor": "estimate"},
    "outlet": {"price": 2.50, "unit": "each", "vendor": "estimate"},
    "switch": {"price": 2.00, "unit": "each", "vendor": "estimate"},
    "wire nut": {"price": 0.15, "unit": "each", "vendor": "estimate"},
    "conduit 1/2 emt": {"price": 0.65, "unit": "per ft", "vendor": "estimate"},
    "conduit 3/4 emt": {"price": 0.95, "unit": "per ft", "vendor": "estimate"},
    "surge protector whole home": {"price": 195.00, "unit": "each", "vendor": "estimate"},
    # Plumbing
    "pvc pipe 1/2": {"price": 0.55, "unit": "per ft", "vendor": "estimate"},
    "pvc pipe 3/4": {"price": 0.70, "unit": "per ft", "vendor": "estimate"},
    "copper pipe 1/2": {"price": 1.80, "unit": "per ft", "vendor": "estimate"},
    "copper pipe 3/4": {"price": 2.40, "unit": "per ft", "vendor": "estimate"},
    "pex pipe 1/2": {"price": 0.65, "unit": "per ft", "vendor": "estimate"},
    "pex pipe 3/4": {"price": 0.90, "unit": "per ft", "vendor": "estimate"},
    "water heater 40 gal": {"price": 520.00, "unit": "each", "vendor": "estimate"},
    "water heater 50 gal": {"price": 620.00, "unit": "each", "vendor": "estimate"},
    "expansion tank": {"price": 68.00, "unit": "each", "vendor": "estimate"},
    "ball valve 1/2": {"price": 8.50, "unit": "each", "vendor": "estimate"},
    "ball valve 3/4": {"price": 11.00, "unit": "each", "vendor": "estimate"},
    "toilet": {"price": 145.00, "unit": "each", "vendor": "estimate"},
    "faucet": {"price": 89.00, "unit": "each", "vendor": "estimate"},
    "p-trap": {"price": 7.50, "unit": "each", "vendor": "estimate"},
    "wax ring": {"price": 6.00, "unit": "each", "vendor": "estimate"},
    # HVAC
    "furnace filter 16x25x1": {"price": 8.00, "unit": "each", "vendor": "estimate"},
    "thermostat": {"price": 95.00, "unit": "each", "vendor": "estimate"},
    "smart thermostat": {"price": 165.00, "unit": "each", "vendor": "estimate"},
    "hvac duct 6 inch": {"price": 3.50, "unit": "per ft", "vendor": "estimate"},
    "refrigerant r410a": {"price": 28.00, "unit": "per lb", "vendor": "estimate"},
}


def _normalize(text: str) -> str:
    return text.lower().strip()


def _fuzzy_match(query: str, table: dict) -> Optional[dict]:
    """Find best matching item in fallback table."""
    q = _normalize(query)
    # Direct match
    if q in table:
        return {**table[q], "item": q, "confidence": "fallback_table"}
    # Partial match — look for key words
    best = None
    best_score = 0
    q_words = set(q.split())
    for k, v in table.items():
        k_words = set(k.split())
        overlap = len(q_words & k_words)
        if overlap > best_score:
            best_score = overlap
            best = {**v, "item": k, "confidence": "fallback_estimate"}
    if best and best_score >= 1:
        return best
    return None


async def search_home_depot(query: str, zip_code: str = "10001") -> Optional[dict]:
    """
    Search Home Depot for a product and return price.
    Uses their internal search API (no auth required for basic lookups).
    """
    try:
        url = "https://www.homedepot.com/federation-gateway/graphql"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "X-Experience-Name": "b2c",
        }
        payload = {
            "operationName": "searchModel",
            "variables": {
                "storefilter": "ALL",
                "channel": "DESKTOP",
                "keyword": query,
                "navParam": "",
                "pageSize": 3,
                "startIndex": 0,
                "zipCode": zip_code,
            },
            "query": """query searchModel($keyword: String!, $pageSize: Int, $startIndex: Int, $zipCode: String) {
                searchModel(keyword: $keyword, pageSize: $pageSize, startIndex: $startIndex, zipCode: $zipCode) {
                    products {
                        itemId
                        identifiers { productLabel modelNumber brandName }
                        pricing { value original }
                        availabilityType { type }
                    }
                }
            }"""
        }
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                products = (data.get("data", {})
                            .get("searchModel", {})
                            .get("products") or [])
                if products:
                    p = products[0]
                    label = p.get("identifiers", {}).get("productLabel", query)
                    price = (p.get("pricing") or {}).get("value")
                    if price:
                        return {
                            "item": label,
                            "price": float(price),
                            "unit": "each",
                            "vendor": "Home Depot",
                            "confidence": "live",
                        }
    except Exception:
        pass
    return None


async def lookup_price(item: str, quantity: float = 1, zip_code: str = "10001") -> dict:
    """
    Main entry point. Returns:
      {item, price, unit, total, vendor, confidence, note}
    confidence: "live" | "fallback_table" | "fallback_estimate" | "not_found"
    """
    # Try live HD search first
    result = await search_home_depot(item, zip_code)

    # Fall back to table
    if not result:
        result = _fuzzy_match(item, FALLBACK_PRICES)

    if not result:
        return {
            "item": item,
            "price": None,
            "unit": "each",
            "total": None,
            "vendor": "unknown",
            "confidence": "not_found",
            "note": f"No price found for '{item}'. Recommend calling supplier.",
        }

    total = round(result["price"] * quantity, 2)
    note = ""
    if result["confidence"] == "fallback_estimate":
        note = f"Price is an estimate based on '{result['item']}' — verify with supplier."
    elif result["confidence"] == "fallback_table":
        note = "Price from contractor reference table. May vary by region/supplier."
    elif result["confidence"] == "live":
        note = f"Live price from {result['vendor']}."

    return {
        "item": item,
        "price": result["price"],
        "unit": result.get("unit", "each"),
        "total": total,
        "quantity": quantity,
        "vendor": result.get("vendor", "estimate"),
        "confidence": result["confidence"],
        "note": note,
    }


async def lookup_multiple(items: list[dict]) -> list[dict]:
    """
    Lookup prices for a list of items.
    items: [{"description": str, "qty": float, "zip": str}]
    """
    import asyncio
    tasks = [
        lookup_price(
            item["description"],
            quantity=item.get("qty", 1),
            zip_code=item.get("zip", "10001"),
        )
        for item in items
    ]
    return await asyncio.gather(*tasks)
