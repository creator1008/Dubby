"""Credit balance and ledger history (read-only for end users).

Credits are minutes of dubbing. The balance is the sum of ledger deltas;
grants and spends are written server-side only (signup trigger, job charge).
"""

from __future__ import annotations

from ..auth import CurrentUser
from ..deps import Repo
from ..schemas import CreditEntryOut, CreditsOut

from fastapi import APIRouter

router = APIRouter(prefix="/v1/credits", tags=["credits"])


@router.get("", response_model=CreditsOut)
async def get_credits(user: CurrentUser, repo: Repo) -> CreditsOut:
    balance = await repo.get_credit_balance(user.id)
    entries = await repo.list_credit_entries(user.id, limit=50)
    return CreditsOut(
        balance_minutes=balance,
        entries=[CreditEntryOut.model_validate(e) for e in entries],
    )
