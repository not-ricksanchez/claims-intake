"""Repository tests: recording, references, and duplicate detection."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

import pytest

from claims.models import ClaimRecord, ClaimType, NotificationRequest
from claims.repository import NotificationRepository

CLAIM_REFERENCE = re.compile(r"^CLM-\d{4}-\d{6}$")


class NotificationFactory(Protocol):
    def __call__(self, **overrides: Any) -> NotificationRequest: ...


@pytest.fixture
def repository() -> NotificationRepository:
    return NotificationRepository()


@pytest.fixture
def make_notification() -> NotificationFactory:
    def factory(**overrides: Any) -> NotificationRequest:
        fields: dict[str, Any] = {
            "policy_number": "MOT-4471",
            "loss_date": date(2026, 4, 2),
            "claim_type": "collision",
            "estimated_amount": Decimal("4200.00"),
            "description": "Rear ended at a junction.",
        }
        fields.update(overrides)
        return NotificationRequest(**fields)

    return factory


def test_record_issues_unique_references(
    repository: NotificationRepository,
    make_notification: NotificationFactory,
) -> None:
    first = repository.record(make_notification())
    second = repository.record(make_notification(loss_date=date(2026, 4, 3)))

    assert isinstance(first, ClaimRecord)
    assert isinstance(second, ClaimRecord)
    assert CLAIM_REFERENCE.match(first.claim_reference)
    assert CLAIM_REFERENCE.match(second.claim_reference)
    assert first.claim_reference != second.claim_reference


def test_find_matching_requires_all_three_fields(
    repository: NotificationRepository,
    make_notification: NotificationFactory,
) -> None:
    recorded = repository.record(make_notification())

    match = repository.find_matching("MOT-4471", date(2026, 4, 2), "collision")

    assert match is not None
    assert match.claim_reference == recorded.claim_reference


@pytest.mark.parametrize(
    ("policy_number", "loss_date", "claim_type"),
    [
        pytest.param("MOT-4472", date(2026, 4, 2), "collision", id="policy_differs"),
        pytest.param("MOT-4471", date(2026, 4, 3), "collision", id="date_differs"),
        pytest.param("MOT-4471", date(2026, 4, 2), "theft", id="type_differs"),
    ],
)
def test_two_of_three_is_not_a_duplicate(
    repository: NotificationRepository,
    make_notification: NotificationFactory,
    policy_number: str,
    loss_date: date,
    claim_type: ClaimType,
) -> None:
    repository.record(make_notification())

    assert repository.find_matching(policy_number, loss_date, claim_type) is None


def test_refused_notification_is_not_a_duplicate(
    repository: NotificationRepository,
    make_notification: NotificationFactory,
) -> None:
    # WI-0151 AC-3: a refused submission never reaches record(), so a later
    # identical submission is the first written record, not a duplicate.
    refused = make_notification()

    assert (
        repository.find_matching(
            refused.policy_number,
            refused.loss_date,
            refused.claim_type,
        )
        is None
    )

    recorded = repository.record(make_notification())

    assert recorded.claim_reference.endswith("-000001")
    assert (
        repository.find_matching(
            refused.policy_number,
            refused.loss_date,
            refused.claim_type,
        )
        is recorded
    )
