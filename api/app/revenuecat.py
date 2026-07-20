"""RevenueCat webhook authentication and product-credit policy."""

from __future__ import annotations

import hmac
from collections.abc import Iterable
from typing import Any

from .config import Settings

GRANT_EVENT_TYPES = {"INITIAL_PURCHASE", "RENEWAL", "NON_RENEWING_PURCHASE"}
REVOKE_EVENT_TYPES = {"REFUND"}


def verify_webhook_authorization(provided: str | None, expected: str) -> bool:
    """Compare the exact configured header without leaking timing information."""
    return bool(provided and expected) and hmac.compare_digest(provided, expected)


def parse_credit_mapping(raw: str) -> dict[str, float]:
    mapping: dict[str, float] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"Invalid RevenueCat credit mapping: {item!r}")
        minutes = float(value)
        if minutes <= 0:
            raise ValueError("RevenueCat credit minutes must be positive")
        mapping[key.strip()] = minutes
    return mapping


def credit_minutes_for_event(event: dict[str, Any], settings: Settings) -> float:
    if str(event.get("type") or "") not in GRANT_EVENT_TYPES:
        return 0

    products = parse_credit_mapping(settings.revenuecat_product_credit_minutes)
    product_id = str(event.get("product_id") or "")
    if product_id in products:
        return products[product_id]

    entitlements = parse_credit_mapping(settings.revenuecat_entitlement_credit_minutes)
    entitlement_ids: Iterable[Any] = event.get("entitlement_ids") or []
    matches = [entitlements[str(item)] for item in entitlement_ids if str(item) in entitlements]
    return max(matches, default=0)
