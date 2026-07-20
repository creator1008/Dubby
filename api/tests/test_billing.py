from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

from app.auth import AuthenticatedUser
from app.billing import StripeBilling
from app.config import Settings
from app.revenuecat import (
    credit_minutes_for_event,
    parse_credit_mapping,
    verify_webhook_authorization,
)
from app.routers.billing import create_checkout, revenuecat_webhook, stripe_webhook
from app.schemas import CheckoutCreate


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _signed_header(payload: bytes, secret: str) -> str:
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_webhook_raw_body_signature_verification() -> None:
    secret = "whsec_test_only"
    payload = json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": "checkout.session.completed",
            "data": {"object": {}},
        }
    ).encode()
    billing = StripeBilling(
        Settings(_env_file=None, stripe_webhook_secret=secret)
    )

    event = billing.verify_webhook(payload, _signed_header(payload, secret))
    assert event["id"] == "evt_1"
    with pytest.raises(Exception):
        billing.verify_webhook(payload + b" ", _signed_header(payload, secret))


class FakeBillingRepository:
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        self.customer: str | None = None
        self.events: set[str] = set()
        self.normalized: list[dict] = []

    async def get_stripe_customer(self, _owner_id: UUID) -> str | None:
        return self.customer

    async def save_stripe_customer(
        self, _owner_id: UUID, customer_id: str
    ) -> str:
        self.customer = customer_id
        return customer_id

    async def get_user_by_stripe_customer(
        self, _customer_id: str
    ) -> UUID | None:
        return self.user_id

    async def process_stripe_event(self, event: dict) -> bool:
        self.normalized.append(event)
        if event["event_id"] in self.events:
            return False
        self.events.add(event["event_id"])
        return True

    async def process_revenuecat_event(self, event: dict) -> bool:
        self.normalized.append(event)
        if event["event_id"] in self.events:
            return False
        self.events.add(event["event_id"])
        return True


@pytest.mark.anyio
async def test_checkout_reuses_customer_mapping(monkeypatch) -> None:
    user_id = uuid4()
    repo = FakeBillingRepository(user_id)
    user = AuthenticatedUser(id=user_id, email="user@example.test", role="authenticated")
    created: list[str] = []

    async def fake_customer(self, *_args) -> str:
        created.append("cus_test")
        return "cus_test"

    async def fake_checkout(self, **_kwargs) -> str:
        return "https://checkout.stripe.test/session"

    monkeypatch.setattr(StripeBilling, "create_customer", fake_customer)
    monkeypatch.setattr(StripeBilling, "create_checkout", fake_checkout)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")

    from app.config import get_settings

    get_settings.cache_clear()
    try:
        first = await create_checkout(
            CheckoutCreate(kind="credits"), user=user, repo=repo
        )
        second = await create_checkout(
            CheckoutCreate(kind="subscription"), user=user, repo=repo
        )
    finally:
        get_settings.cache_clear()
    assert first.url == second.url
    assert created == ["cus_test"]


@pytest.mark.anyio
async def test_paid_checkout_is_idempotently_normalized(monkeypatch) -> None:
    user_id = uuid4()
    repo = FakeBillingRepository(user_id)
    event = {
        "id": "evt_checkout",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_1",
                "mode": "payment",
                "payment_status": "paid",
                "payment_intent": "pi_1",
                "customer": "cus_1",
                "metadata": {
                    "dubby_user_id": str(user_id),
                    "credit_kind": "credits",
                },
            }
        },
    }
    monkeypatch.setattr(StripeBilling, "verify_webhook", lambda *_args: event)

    body = json.dumps(event).encode()
    consumed = False

    async def receive() -> dict:
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/"}, receive)
    first = await stripe_webhook(request, repo, stripe_signature="test")
    consumed = False
    request = Request({"type": "http", "method": "POST", "path": "/"}, receive)
    second = await stripe_webhook(request, repo, stripe_signature="test")

    assert first == {"received": True, "processed": True}
    assert second == {"received": True, "processed": False}
    assert repo.normalized[0]["credit_reference"] == "pi_1"
    assert repo.normalized[0]["credit_minutes"] == 30


def test_revenuecat_auth_and_product_mapping() -> None:
    settings = Settings(
        _env_file=None,
        revenuecat_product_credit_minutes="starter_monthly=60, credits_30=30",
        revenuecat_entitlement_credit_minutes="starter=45",
    )
    assert verify_webhook_authorization("Bearer opaque-test", "Bearer opaque-test")
    assert not verify_webhook_authorization("Bearer wrong", "Bearer opaque-test")
    assert parse_credit_mapping("credits_30=30") == {"credits_30": 30}
    assert credit_minutes_for_event(
        {"type": "INITIAL_PURCHASE", "product_id": "starter_monthly"},
        settings,
    ) == 60
    assert credit_minutes_for_event(
        {"type": "RENEWAL", "entitlement_ids": ["starter"]},
        settings,
    ) == 45
    assert credit_minutes_for_event(
        {"type": "CANCELLATION", "product_id": "starter_monthly"},
        settings,
    ) == 0


@pytest.mark.anyio
async def test_revenuecat_webhook_auth_and_idempotency(monkeypatch) -> None:
    user_id = uuid4()
    repo = FakeBillingRepository(user_id)
    event = {
        "event": {
            "id": "rc_evt_1",
            "type": "INITIAL_PURCHASE",
            "app_user_id": str(user_id),
            "product_id": "credits_30",
            "entitlement_ids": [],
            "transaction_id": "store_tx_1",
            "store": "APP_STORE",
        }
    }
    body = json.dumps(event).encode()

    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH_HEADER", "Bearer rc-test")
    monkeypatch.setenv("REVENUECAT_PRODUCT_CREDIT_MINUTES", "credits_30=30")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        async def request() -> Request:
            sent = False

            async def receive() -> dict:
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            return Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/",
                    "headers": [(b"content-type", b"application/json")],
                },
                receive,
            )

        first = await revenuecat_webhook(
            await request(), repo, authorization="Bearer rc-test"
        )
        second = await revenuecat_webhook(
            await request(), repo, authorization="Bearer rc-test"
        )
        with pytest.raises(Exception) as rejected:
            await revenuecat_webhook(
                await request(), repo, authorization="Bearer wrong"
            )
    finally:
        get_settings.cache_clear()

    assert first == {"received": True, "processed": True}
    assert second == {"received": True, "processed": False}
    assert rejected.value.status_code == 401
    assert repo.normalized[0]["credit_minutes"] == 30
    assert repo.normalized[0]["transaction_id"] == "store_tx_1"
