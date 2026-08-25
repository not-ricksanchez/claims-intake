"""Model boundary tests: what is accepted and what is refused."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from claims.models import NotificationRequest, RecordedNotification

DATA = Path(__file__).resolve().parents[2] / "data"


def _payloads(filename: str) -> dict[str, dict[str, Any]]:
    entries: list[dict[str, Any]] = json.loads((DATA / filename).read_text())
    return {entry["id"]: entry["payload"] for entry in entries}


INVALID = _payloads("fnol_invalid.json")
EDGE = _payloads("fnol_edge.json")

# Only these fail interpretation; everything else survives to the rules.
MODEL_FAILURES = {"EDGE-08", "EDGE-11", "EDGE-12"}


def _valid(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "policy_number": "MOT-4471",
        "loss_date": "2026-04-02",
        "claim_type": "collision",
        "estimated_amount": "4200.00",
        "description": "Rear ended at a junction.",
    }
    payload.update(overrides)
    return payload


def test_accepts_well_formed_payload() -> None:
    notification = NotificationRequest(**_valid())

    assert notification.loss_date == date(2026, 4, 2)
    assert notification.estimated_amount == Decimal("4200.00")
    assert isinstance(notification.loss_date, date)
    assert isinstance(notification.estimated_amount, Decimal)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {k: v for k, v in _valid().items() if k != "policy_number"},
            id="missing_policy_number",
        ),
        pytest.param(
            {k: v for k, v in _valid().items() if k != "loss_date"},
            id="missing_loss_date",
        ),
        pytest.param(
            {k: v for k, v in _valid().items() if k != "claim_type"},
            id="missing_claim_type",
        ),
        pytest.param(
            {k: v for k, v in _valid().items() if k != "estimated_amount"},
            id="missing_estimated_amount",
        ),
        pytest.param(_valid(policy_number=""), id="empty_policy_number"),
        pytest.param(_valid(loss_date="not-a-date"), id="loss_date_unparsable"),
        pytest.param(_valid(claim_type="flood"), id="claim_type_outside_vocabulary"),
        pytest.param(_valid(estimated_amount="0.00"), id="estimated_amount_zero"),
        pytest.param(_valid(estimated_amount="3499.999"), id="estimated_amount_three_dp"),
        pytest.param(_valid(extra_field="x"), id="undefined_field"),
    ],
)
def test_refuses_invalid_payload(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NotificationRequest(**payload)


@pytest.mark.parametrize(
    "claim_reference",
    [
        pytest.param("CLM-2026-317", id="wrong_format"),
        pytest.param("", id="empty"),
    ],
)
def test_refuses_bad_claim_reference(claim_reference: str) -> None:
    with pytest.raises(ValidationError):
        RecordedNotification(
            claim_reference=claim_reference,
            policy_number="MOT-4471",
            loss_date=date(2026, 4, 2),
            claim_type="collision",
            estimated_amount=Decimal("4200.00"),
        )


@pytest.mark.parametrize(
    "payload_id",
    sorted(EDGE) + sorted(INVALID),
    ids=sorted(EDGE) + sorted(INVALID),
)
def test_payload_classification(payload_id: str) -> None:
    payload = EDGE[payload_id] if payload_id in EDGE else INVALID[payload_id]

    if payload_id in MODEL_FAILURES:
        with pytest.raises(ValidationError):
            NotificationRequest(**payload)
    else:
        NotificationRequest(**payload)
