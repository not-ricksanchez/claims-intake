"""Rule evaluation tests.

Authority: docs/api-contract.md section 4 and docs/requirements-brief.md.
Each parametrized case is named for the comparison or acceptance criterion
it protects. A case that expects None is a pass; a case that expects a
RuleFailure is a refusal. HTTP status codes belong to the HTTP layer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from claims.models import ClaimRecord, ClaimType, NotificationRequest, Policy, RuleFailure
from claims.policy_client import PolicyLookupFailed, StubPolicyClient
from claims.repository import NotificationRepository
from claims.service import (
    evaluate_amount_within_limit,
    evaluate_claim_type_covered,
    evaluate_loss_after_inception,
    evaluate_loss_before_expiry,
    evaluate_not_cancelled,
    evaluate_not_duplicate,
    evaluate_notification,
    evaluate_policy_exists,
    submit_notification,
)

STANDARD_TYPES: tuple[ClaimType, ...] = (
    "collision",
    "theft",
    "glass",
    "liability",
    "weather",
)
NAMED_PERILS_TYPES: tuple[ClaimType, ...] = ("theft", "glass", "weather", "liability")


def _notification(**overrides: Any) -> NotificationRequest:
    fields: dict[str, Any] = {
        "policy_number": "MOT-4471",
        "loss_date": date(2026, 4, 2),
        "claim_type": "collision",
        "estimated_amount": Decimal("4200.00"),
        "description": "Rear ended at a junction.",
    }
    fields.update(overrides)
    return NotificationRequest(**fields)


def _policy(**overrides: Any) -> Policy:
    fields: dict[str, Any] = {
        "policy_number": "MOT-4471",
        "product": "personal_auto_standard",
        "effective_date": date(2026, 3, 1),
        "expiry_date": date(2027, 2, 28),
        "cancellation_date": None,
        "limit": Decimal("50000.00"),
        "permitted_claim_types": STANDARD_TYPES,
    }
    fields.update(overrides)
    return Policy(**fields)


V1_NOT_FOUND = RuleFailure(rule="V-1", code="POLICY_NOT_FOUND")
V2_BEFORE_INCEPTION = RuleFailure(rule="V-2", code="LOSS_BEFORE_INCEPTION")
V3_AFTER_EXPIRY = RuleFailure(rule="V-3", code="LOSS_AFTER_EXPIRY")
V4_EXCEEDS_LIMIT = RuleFailure(rule="V-4", code="AMOUNT_EXCEEDS_LIMIT")
V5_TYPE_NOT_COVERED = RuleFailure(rule="V-5", code="TYPE_NOT_COVERED")
V6_DUPLICATE = RuleFailure(rule="V-6", code="DUPLICATE_NOTIFICATION")
V7_CANCELLED = RuleFailure(rule="V-7", code="POLICY_CANCELLED")


@pytest.mark.parametrize(
    ("policy_number", "expected"),
    [
        pytest.param("MOT-4471", None, id="policy_present"),
        pytest.param("MOT-9999", V1_NOT_FOUND, id="WI-0142-AC-4-policy_absent"),
    ],
)
def test_v1_policy_exists(
    policy_client: StubPolicyClient,
    policy_number: str,
    expected: RuleFailure | None,
) -> None:
    notification = _notification(policy_number=policy_number)

    assert evaluate_policy_exists(notification, policy_client) == expected


@pytest.mark.parametrize(
    ("loss_date", "expected"),
    [
        pytest.param(date(2026, 2, 28), V2_BEFORE_INCEPTION, id="WI-0142-AC-1-before_inception"),
        pytest.param(date(2026, 3, 1), None, id="WI-0142-AC-3-on_inception"),
        pytest.param(date(2026, 3, 2), None, id="after_inception"),
    ],
)
def test_v2_loss_after_inception(loss_date: date, expected: RuleFailure | None) -> None:
    notification = _notification(loss_date=loss_date)
    policy = _policy(effective_date=date(2026, 3, 1))

    #assert evaluate_loss_after_inception(notification, policy) == expected
    assert evaluate_loss_after_inception(notification, policy) == forcefull_fail


@pytest.mark.parametrize(
    ("loss_date", "expected"),
    [
        pytest.param(date(2027, 2, 27), None, id="before_expiry"),
        pytest.param(date(2027, 2, 28), None, id="on_expiry"),
        pytest.param(date(2027, 3, 1), V3_AFTER_EXPIRY, id="after_expiry"),
    ],
)
def test_v3_loss_before_expiry(loss_date: date, expected: RuleFailure | None) -> None:
    notification = _notification(loss_date=loss_date)
    policy = _policy(expiry_date=date(2027, 2, 28))

    assert evaluate_loss_before_expiry(notification, policy) == expected


@pytest.mark.parametrize(
    ("estimated_amount", "expected"),
    [
        pytest.param(Decimal("49999.99"), None, id="below_limit"),
        pytest.param(Decimal("50000.00"), None, id="on_limit"),
        pytest.param(Decimal("50000.01"), V4_EXCEEDS_LIMIT, id="above_limit"),
    ],
)
def test_v4_amount_within_limit(
    estimated_amount: Decimal, expected: RuleFailure | None
) -> None:
    notification = _notification(estimated_amount=estimated_amount)
    policy = _policy(limit=Decimal("50000.00"))

    assert evaluate_amount_within_limit(notification, policy) == expected


@pytest.mark.parametrize(
    ("claim_type", "permitted", "expected"),
    [
        pytest.param("collision", STANDARD_TYPES, None, id="standard_permits_collision"),
        pytest.param("weather", STANDARD_TYPES, None, id="standard_permits_weather"),
        pytest.param("theft", NAMED_PERILS_TYPES, None, id="named_perils_permits_theft"),
        pytest.param(
            "collision",
            NAMED_PERILS_TYPES,
            V5_TYPE_NOT_COVERED,
            id="named_perils_excludes_collision",
        ),
        pytest.param("liability", ("liability",), None, id="liability_only_permits_liability"),
        pytest.param(
            "collision",
            ("liability",),
            V5_TYPE_NOT_COVERED,
            id="liability_only_excludes_collision",
        ),
        pytest.param(
            "glass",
            ("liability",),
            V5_TYPE_NOT_COVERED,
            id="liability_only_excludes_glass",
        ),
    ],
)
def test_v5_claim_type_covered(
    claim_type: ClaimType,
    permitted: tuple[ClaimType, ...],
    expected: RuleFailure | None,
) -> None:
    notification = _notification(claim_type=claim_type)
    policy = _policy(permitted_claim_types=permitted)

    assert evaluate_claim_type_covered(notification, policy) == expected


@pytest.mark.parametrize(
    ("seed", "overrides", "expected"),
    [
        pytest.param(None, {}, None, id="WI-0151-AC-3-nothing_recorded"),
        pytest.param(
            {},
            {},
            V6_DUPLICATE,
            id="WI-0151-AC-1-same_policy_date_and_type",
        ),
        pytest.param(
            {},
            {"policy_number": "MOT-4472"},
            None,
            id="policy_number_differs",
        ),
        pytest.param(
            {},
            {"loss_date": date(2026, 4, 3)},
            None,
            id="loss_date_differs",
        ),
        pytest.param(
            {},
            {"claim_type": "theft"},
            None,
            id="claim_type_differs",
        ),
        pytest.param(
            {},
            {"estimated_amount": Decimal("99.00"), "description": "different text"},
            V6_DUPLICATE,
            id="amount_and_description_are_not_identity",
        ),
    ],
)
def test_v6_not_duplicate(
    seed: dict[str, Any] | None,
    overrides: dict[str, Any],
    expected: RuleFailure | None,
) -> None:
    repository = NotificationRepository()
    if seed is not None:
        repository.record(_notification(**seed))

    assert evaluate_not_duplicate(_notification(**overrides), repository) == expected


@pytest.mark.parametrize(
    ("cancellation_date", "loss_date", "expected"),
    [
        pytest.param(None, date(2026, 4, 2), None, id="WI-0158-AC-3-cancellation_absent"),
        pytest.param(date(2026, 2, 1), date(2026, 1, 31), None, id="before_cancellation"),
        pytest.param(
            date(2026, 2, 1),
            date(2026, 2, 1),
            V7_CANCELLED,
            id="WI-0158-AC-2-on_cancellation",
        ),
        pytest.param(
            date(2026, 2, 1),
            date(2026, 2, 2),
            V7_CANCELLED,
            id="WI-0158-AC-1-after_cancellation",
        ),
        pytest.param(
            date(2025, 10, 1),
            date(2026, 1, 8),
            V7_CANCELLED,
            id="WI-0158-AC-4-cancelled_and_after_original_term",
        ),
    ],
)
def test_v7_not_cancelled(
    cancellation_date: date | None,
    loss_date: date,
    expected: RuleFailure | None,
) -> None:
    notification = _notification(loss_date=loss_date)
    policy = _policy(
        effective_date=date(2025, 1, 1),
        expiry_date=date(2025, 12, 31),
        cancellation_date=cancellation_date,
    )

    assert evaluate_not_cancelled(notification, policy) == expected


@pytest.mark.parametrize(
    ("policy", "notification", "expected"),
    [
        pytest.param(_policy(), _notification(), None, id="every_policy_rule_passes"),
        pytest.param(
            _policy(
                effective_date=date(2025, 1, 1),
                expiry_date=date(2025, 12, 31),
                cancellation_date=date(2025, 10, 1),
            ),
            _notification(loss_date=date(2026, 1, 8)),
            V7_CANCELLED,
            id="WI-0158-AC-4-cancelled_before_expiry_rule",
        ),
        pytest.param(
            _policy(effective_date=date(2026, 4, 15), limit=Decimal("50000.00")),
            _notification(loss_date=date(2026, 3, 2), estimated_amount=Decimal("72000.00")),
            V2_BEFORE_INCEPTION,
            id="WI-0142-inception_before_amount",
        ),
        pytest.param(
            _policy(expiry_date=date(2026, 2, 28), permitted_claim_types=("liability",)),
            _notification(loss_date=date(2026, 8, 20), claim_type="collision"),
            V3_AFTER_EXPIRY,
            id="expiry_before_claim_type",
        ),
        pytest.param(
            _policy(limit=Decimal("10000.00"), permitted_claim_types=("liability",)),
            _notification(estimated_amount=Decimal("26000.00"), claim_type="collision"),
            V4_EXCEEDS_LIMIT,
            id="amount_before_claim_type",
        ),
    ],
)
def test_evaluate_notification_first_policy_rule(
    policy: Policy,
    notification: NotificationRequest,
    expected: RuleFailure | None,
) -> None:
    assert evaluate_notification(notification, policy) == expected


@pytest.mark.parametrize(
    "reason",
    [
        pytest.param("timeout", id="timeout"),
        pytest.param("unreachable", id="unreachable"),
        pytest.param("unparsable", id="unparsable"),
    ],
)
def test_submit_notification_propagates_lookup_failure(
    reason: str,
) -> None:
    client = StubPolicyClient(fail_with=reason)  # type: ignore[arg-type]
    repository = NotificationRepository()
    notification = _notification()

    with pytest.raises(PolicyLookupFailed) as caught:
        submit_notification(notification, client, repository)

    assert caught.value.reason == reason
    assert caught.value.policy_number == notification.policy_number
    assert repository.find_matching(
        notification.policy_number,
        notification.loss_date,
        notification.claim_type,
    ) is None


def test_submit_notification_policy_not_found_is_v1(
    policy_client: StubPolicyClient,
) -> None:
    repository = NotificationRepository()
    notification = _notification(policy_number="MOT-9999", loss_date=date(2020, 1, 1))

    assert submit_notification(notification, policy_client, repository) == V1_NOT_FOUND
    assert (
        repository.find_matching(
            notification.policy_number,
            notification.loss_date,
            notification.claim_type,
        )
        is None
    )


def test_submit_notification_cancelled_beats_duplicate(
    policy_client: StubPolicyClient,
) -> None:
    repository = NotificationRepository()
    notification = _notification(
        policy_number="MOT-4496",
        loss_date=date(2026, 3, 1),
        claim_type="collision",
        estimated_amount=Decimal("1000.00"),
    )
    repository.record(notification)

    assert submit_notification(notification, policy_client, repository) == V7_CANCELLED


def test_submit_notification_duplicate_beats_inception(
    policy_client: StubPolicyClient,
) -> None:
    repository = NotificationRepository()
    notification = _notification(
        policy_number="MOT-4493",
        loss_date=date(2026, 3, 2),
        claim_type="collision",
        estimated_amount=Decimal("1000.00"),
    )
    repository.record(notification)

    assert submit_notification(notification, policy_client, repository) == V6_DUPLICATE


def test_submit_notification_records_when_every_rule_passes(
    policy_client: StubPolicyClient,
) -> None:
    repository = NotificationRepository()
    notification = _notification()

    recorded = submit_notification(notification, policy_client, repository)

    assert isinstance(recorded, ClaimRecord)
    assert recorded.policy_number == notification.policy_number
    assert recorded.loss_date == notification.loss_date
    assert recorded.claim_type == notification.claim_type
    assert (
        repository.find_matching(
            notification.policy_number,
            notification.loss_date,
            notification.claim_type,
        )
        is recorded
    )


def test_submit_notification_does_not_record_a_refusal(
    policy_client: StubPolicyClient,
) -> None:
    repository = NotificationRepository()
    notification = _notification(
        policy_number="MOT-4502",
        loss_date=date(2026, 3, 19),
        estimated_amount=Decimal("26000.00"),
    )

    assert submit_notification(notification, policy_client, repository) == V4_EXCEEDS_LIMIT
    assert (
        repository.find_matching(
            notification.policy_number,
            notification.loss_date,
            notification.claim_type,
        )
        is None
    )


def test_wi_0151_ac3_refused_submission_is_not_a_duplicate(
    policy_client: StubPolicyClient,
) -> None:
    repository = NotificationRepository()
    refused = _notification(
        policy_number="MOT-4502",
        loss_date=date(2026, 3, 19),
        claim_type="collision",
        estimated_amount=Decimal("26000.00"),
    )
    retry = _notification(
        policy_number="MOT-4502",
        loss_date=date(2026, 3, 19),
        claim_type="collision",
        estimated_amount=Decimal("1000.00"),
    )

    assert submit_notification(refused, policy_client, repository) == V4_EXCEEDS_LIMIT
    recorded = submit_notification(retry, policy_client, repository)

    assert isinstance(recorded, ClaimRecord)
    assert recorded.estimated_amount == Decimal("1000.00")
