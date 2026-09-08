"""Model boundary tests: what is accepted and what is refused."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from pydantic import ValidationError

from claims.models import (
    ClaimRecord,
    ErrorCode,
    NotificationRequest,
    Policy,
    RuleFailure,
    RuleId,
)
from claims.policy_client import StubPolicyClient

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


def _policy_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "policy_number": "MOT-4471",
        "product": "personal_auto_standard",
        "effective_date": date(2026, 3, 1),
        "expiry_date": date(2027, 2, 28),
        "cancellation_date": None,
        "limit": Decimal("50000.00"),
        "permitted_claim_types": (
            "collision",
            "theft",
            "glass",
            "liability",
            "weather",
        ),
    }
    fields.update(overrides)
    return fields


def _claim_record_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "claim_reference": "CLM-2026-000317",
        "policy_number": "MOT-4471",
        "loss_date": date(2026, 4, 2),
        "claim_type": "collision",
        "estimated_amount": Decimal("4200.00"),
    }
    fields.update(overrides)
    return fields


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


@pytest.mark.parametrize(
    "policy_number",
    [
        pytest.param("MOT-4471", id="five_permitted_types"),
        pytest.param("MOT-4481", id="four_permitted_types"),
        pytest.param("MOT-4486", id="one_permitted_type"),
        pytest.param("MOT-4496", id="cancelled_policy"),
    ],
)
def test_policy_accepts_master_record(
    policy_client: StubPolicyClient,
    policy_number: str,
) -> None:
    record = policy_client.get_policy(policy_number)

    policy = Policy.model_validate(
        {
            "policy_number": record.policy_number,
            "product": record.product,
            "effective_date": record.effective_date,
            "expiry_date": record.expiry_date,
            "cancellation_date": record.cancellation_date,
            "limit": record.limit,
            "permitted_claim_types": record.permitted_claim_types,
        }
    )

    assert policy.product == record.product
    assert policy.cancellation_date == record.cancellation_date
    assert policy.permitted_claim_types == record.permitted_claim_types
    assert isinstance(policy.effective_date, date)
    assert isinstance(policy.limit, Decimal)


def test_policy_product_and_cancellation_date_are_required() -> None:
    assert Policy.model_fields["product"].is_required()
    assert Policy.model_fields["cancellation_date"].is_required()
    assert Policy.model_fields["cancellation_date"].annotation == date | None


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {k: v for k, v in _policy_fields().items() if k != "product"},
            id="missing_product",
        ),
        pytest.param(
            {k: v for k, v in _policy_fields().items() if k != "cancellation_date"},
            id="missing_cancellation_date",
        ),
        pytest.param(
            _policy_fields(permitted_claim_types=("flood",)),
            id="permitted_type_outside_vocabulary",
        ),
    ],
)
def test_policy_refuses_invalid_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Policy(**fields)


def test_policy_accepts_explicit_null_cancellation_date() -> None:
    policy = Policy(**_policy_fields(cancellation_date=None))

    assert policy.cancellation_date is None


def test_claim_record_accepts_contract_reference() -> None:
    recorded = ClaimRecord(**_claim_record_fields())

    assert recorded.claim_reference == "CLM-2026-000317"
    assert isinstance(recorded.loss_date, date)
    assert isinstance(recorded.estimated_amount, Decimal)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            _claim_record_fields(claim_reference="CLM-2026-317"),
            id="wrong_format",
        ),
        pytest.param(_claim_record_fields(claim_reference=""), id="empty_reference"),
        pytest.param(
            _claim_record_fields(claim_type="flood"),
            id="claim_type_outside_vocabulary",
        ),
    ],
)
def test_claim_record_refuses_invalid_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ClaimRecord(**fields)


@pytest.mark.parametrize(
    ("rule", "code"),
    [
        pytest.param("V-1", "POLICY_NOT_FOUND", id="v1"),
        pytest.param("V-2", "LOSS_BEFORE_INCEPTION", id="v2"),
        pytest.param("V-7", "POLICY_CANCELLED", id="v7"),
        pytest.param("V-3", "LOSS_AFTER_EXPIRY", id="v3"),
        pytest.param("V-4", "AMOUNT_EXCEEDS_LIMIT", id="v4"),
        pytest.param("V-5", "TYPE_NOT_COVERED", id="v5"),
        pytest.param("V-6", "DUPLICATE_NOTIFICATION", id="v6"),
    ],
)
def test_rule_failure_accepts_contract_pair(rule: RuleId, code: ErrorCode) -> None:
    failure = RuleFailure(rule=rule, code=code)

    assert failure.rule == rule
    assert failure.code == code


def test_rule_failure_is_immutable() -> None:
    failure = RuleFailure(rule="V-1", code="POLICY_NOT_FOUND")

    with pytest.raises(ValidationError):
        failure.rule = "V-2"


def test_rule_and_code_are_distinct_types() -> None:
    hints = get_type_hints(RuleFailure)

    assert hints["rule"] is RuleId
    assert hints["code"] is ErrorCode
    assert RuleId is not ErrorCode


@pytest.mark.parametrize(
    ("rule", "code"),
    [
        pytest.param("POLICY_NOT_FOUND", "V-1", id="swapped"),
        pytest.param("V-1", "V-1", id="rule_used_as_code"),
        pytest.param("POLICY_NOT_FOUND", "POLICY_NOT_FOUND", id="code_used_as_rule"),
    ],
)
def test_rule_failure_rejects_interchanged_strings(rule: str, code: str) -> None:
    with pytest.raises(ValidationError):
        RuleFailure.model_validate({"rule": rule, "code": code})
