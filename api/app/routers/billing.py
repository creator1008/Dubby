"""Stripe Checkout and signed webhook endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import stripe
from fastapi import APIRouter, Header, Request

from ..auth import CurrentUser
from ..billing import StripeBilling
from ..config import get_settings
from ..deps import Repo
from ..errors import BadRequestError, UnauthorizedError
from ..revenuecat import credit_minutes_for_event, verify_webhook_authorization
from ..schemas import CheckoutCreate, CheckoutOut

router = APIRouter(prefix="/v1/billing", tags=["billing"])


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if value else {}


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (TypeError, ValueError, OSError):
        return None


def _timestamp_ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC) if value else None
    except (TypeError, ValueError, OSError):
        return None


def _revenuecat_user_id(event: dict[str, Any]) -> UUID | None:
    candidates = [event.get("app_user_id"), *(event.get("aliases") or [])]
    return next((user_id for value in candidates if (user_id := _uuid(value))), None)


@router.post("/checkout", response_model=CheckoutOut)
async def create_checkout(
    body: CheckoutCreate, user: CurrentUser, repo: Repo
) -> CheckoutOut:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise BadRequestError("Billing is not configured")
    customer_id = await repo.get_stripe_customer(user.id)
    billing = StripeBilling(settings)
    if customer_id is None:
        created = await billing.create_customer(user.id, user.email)
        customer_id = await repo.save_stripe_customer(user.id, created)
    url = await billing.create_checkout(
        user_id=user.id, customer_id=customer_id, kind=body.kind
    )
    return CheckoutOut(url=url)


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    repo: Repo,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, bool]:
    # Signature verification must receive the exact bytes Stripe sent.
    payload = await request.body()
    if not stripe_signature:
        raise BadRequestError("Missing Stripe-Signature")
    try:
        event = StripeBilling(get_settings()).verify_webhook(
            payload, stripe_signature
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise BadRequestError("Invalid Stripe signature") from exc

    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    obj = _as_dict(_as_dict(event.get("data")).get("object"))
    if not event_id or not event_type:
        raise BadRequestError("Invalid Stripe event")

    metadata = _as_dict(obj.get("metadata"))
    customer_id = str(obj.get("customer") or "") or None
    user_id = _uuid(metadata.get("dubby_user_id") or obj.get("client_reference_id"))
    if user_id is None and customer_id:
        user_id = await repo.get_user_by_stripe_customer(customer_id)

    normalized: dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type,
        "payload": event,
        "user_id": user_id,
        "customer_id": customer_id,
    }
    settings = get_settings()

    if event_type == "checkout.session.completed":
        mode = obj.get("mode")
        if (
            mode == "payment"
            and obj.get("payment_status") == "paid"
            and metadata.get("credit_kind") == "credits"
        ):
            normalized["credit_minutes"] = settings.stripe_credit_pack_minutes
            normalized["credit_reference"] = str(
                obj.get("payment_intent") or obj["id"]
            )
        if mode == "subscription":
            normalized["subscription_id"] = str(obj.get("subscription") or "") or None
            normalized["subscription_status"] = "active"

    elif event_type == "invoice.paid":
        parent = _as_dict(obj.get("parent"))
        subscription_details = _as_dict(parent.get("subscription_details"))
        invoice_metadata = _as_dict(subscription_details.get("metadata"))
        user_id = _uuid(invoice_metadata.get("dubby_user_id")) or user_id
        normalized["user_id"] = user_id
        subscription_id = str(
            subscription_details.get("subscription") or obj.get("subscription") or ""
        ) or None
        normalized["subscription_id"] = subscription_id
        lines = _as_dict(obj.get("lines")).get("data") or []
        invoice_price_ids: set[str] = set()
        for line in lines:
            line_data = _as_dict(line)
            legacy_price = _as_dict(line_data.get("price")).get("id")
            price_details = _as_dict(_as_dict(line_data.get("pricing")).get("price_details"))
            price_id = legacy_price or price_details.get("price")
            if price_id:
                invoice_price_ids.add(str(price_id))
        if subscription_id and settings.stripe_subscription_price_id in invoice_price_ids:
            normalized["subscription_status"] = "active"
            normalized["credit_minutes"] = settings.stripe_subscription_minutes
            normalized["credit_reference"] = str(obj["id"])

    elif event_type.startswith("customer.subscription."):
        normalized.update(
            subscription_id=str(obj.get("id") or "") or None,
            subscription_status=str(obj.get("status") or "unknown"),
            period_end=_timestamp(obj.get("current_period_end")),
            cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
        )
        items = _as_dict(obj.get("items")).get("data") or []
        if items:
            normalized["price_id"] = _as_dict(_as_dict(items[0]).get("price")).get("id")

    if (
        (normalized.get("credit_minutes") or normalized.get("subscription_id"))
        and normalized.get("user_id") is None
    ):
        raise BadRequestError("Stripe customer is not mapped to a Dubby user")

    processed = await repo.process_stripe_event(normalized)
    return {"received": True, "processed": processed}


@router.post("/revenuecat/webhook")
async def revenuecat_webhook(
    request: Request,
    repo: Repo,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, bool]:
    settings = get_settings()
    if not verify_webhook_authorization(
        authorization, settings.revenuecat_webhook_auth_header
    ):
        raise UnauthorizedError("Invalid RevenueCat webhook authorization")

    payload = await request.json()
    event = _as_dict(_as_dict(payload).get("event"))
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    app_user_id = str(event.get("app_user_id") or "")
    if not event_id or not event_type or not app_user_id:
        raise BadRequestError("Invalid RevenueCat event")

    entitlement_ids = event.get("entitlement_ids") or []
    if not isinstance(entitlement_ids, list):
        raise BadRequestError("Invalid RevenueCat entitlement_ids")

    user_id = _revenuecat_user_id(event)
    credit_minutes = credit_minutes_for_event(event, settings)
    if credit_minutes and user_id is None:
        raise BadRequestError("RevenueCat App User ID is not a Dubby user UUID")

    status_by_type = {
        "INITIAL_PURCHASE": "active",
        "RENEWAL": "active",
        "NON_RENEWING_PURCHASE": "active",
        "UNCANCELLATION": "active",
        "PRODUCT_CHANGE": "active",
        "CANCELLATION": "cancelled",
        "BILLING_ISSUE": "billing_issue",
        "EXPIRATION": "expired",
        "REFUND": "revoked",
    }
    normalized: dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
        "user_id": user_id,
        "app_user_id": app_user_id,
        "product_id": str(event.get("product_id") or "") or None,
        "entitlement_ids": [str(item) for item in entitlement_ids],
        "transaction_id": str(event.get("transaction_id") or "") or None,
        "original_transaction_id": (
            str(event.get("original_transaction_id") or "") or None
        ),
        "status": status_by_type.get(event_type, "unknown"),
        "expires_at": _timestamp_ms(event.get("expiration_at_ms")),
        "store": str(event.get("store") or "") or None,
        "credit_minutes": credit_minutes,
    }
    processed = await repo.process_revenuecat_event(normalized)
    return {"received": True, "processed": processed}
