"""Stripe boundary. All SDK calls are isolated here for easy mocking."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import stripe

from .config import Settings


class StripeBilling:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        event = stripe.Webhook.construct_event(
            payload, signature, self.settings.stripe_webhook_secret
        )
        return event.to_dict(for_json=True)

    async def create_customer(self, user_id: UUID, email: str | None) -> str:
        customer = await asyncio.to_thread(
            stripe.Customer.create,
            email=email,
            metadata={"dubby_user_id": str(user_id)},
            api_key=self.settings.stripe_secret_key,
        )
        return str(customer["id"])

    async def create_checkout(
        self,
        *,
        user_id: UUID,
        customer_id: str,
        kind: str,
    ) -> str:
        subscription = kind == "subscription"
        price_id = (
            self.settings.stripe_subscription_price_id
            if subscription
            else self.settings.stripe_credit_pack_price_id
        )
        metadata = {
            "dubby_user_id": str(user_id),
            "credit_kind": kind,
            "credit_minutes": str(
                self.settings.stripe_subscription_minutes
                if subscription
                else self.settings.stripe_credit_pack_minutes
            ),
        }
        kwargs: dict[str, Any] = {
            "customer": customer_id,
            "mode": "subscription" if subscription else "payment",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": self.settings.checkout_success_url,
            "cancel_url": self.settings.checkout_cancel_url,
            "client_reference_id": str(user_id),
            "metadata": metadata,
            "api_key": self.settings.stripe_secret_key,
        }
        if subscription:
            kwargs["subscription_data"] = {"metadata": metadata}
        else:
            kwargs["payment_intent_data"] = {"metadata": metadata}
        session = await asyncio.to_thread(stripe.checkout.Session.create, **kwargs)
        return str(session["url"])
