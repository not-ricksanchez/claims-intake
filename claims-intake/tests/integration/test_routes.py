"""HTTP integration tests for POST /notifications.

Authority: docs/api-contract.md sections 2 through 6. These tests pin the HTTP
seam (`app`, `get_policy_client`, `get_repository`) and the status/code mapping.
They are written against the contract, not against a stub `routes.py`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claims.api.routes import app, get_policy_client, get_repository  # type: ignore[attr-defined]
from claims.policy_client import LookupFailureReason, StubPolicyClient
from claims.repository import NotificationRepository

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CLAIM_REFERENCE = re.compile(r"^CLM-\d{4}-\d{6}$")
NOTIFICATIONS_URL = "/notifications"

RULE_CODES = frozenset(
    {
        "POLICY_NOT_FOUND",
        "LOSS_BEFORE_INCEPTION",
        "LOSS_AFTER_EXPIRY",
        "AMOUNT_EXCEEDS_LIMIT",
        "TYPE_NOT_COVERED",
        "DUPLICATE_NOTIFICATION",
        "POLICY_CANCELLED",
    }
)

DEPENDENCY_FAILURES: tuple[tuple[LookupFailureReason, int, str], ...] = (
    ("timeout", 504, "POLICY_MASTER_TIMEOUT"),
    ("unreachable", 503, "POLICY_MASTER_UNAVAILABLE"),
    ("unparsable", 502, "POLICY_MASTER_INVALID_RESPONSE"),
)


def _index_payloads(filename: str) -> dict[str, dict[str, Any]]:
    raw: Any = json.loads((DATA_DIR / filename).read_text())
    assert isinstance(raw, list)
    indexed: dict[str, dict[str, Any]] = {}
    for entry in raw:
        assert isinstance(entry, dict)
        entry_id = entry["id"]
        payload = entry["payload"]
        assert isinstance(entry_id, str)
        assert isinstance(payload, dict)
        indexed[entry_id] = payload
    return indexed


def _index_policies() -> dict[str, dict[str, Any]]:
    raw: Any = json.loads((DATA_DIR / "policies.json").read_text())
    assert isinstance(raw, list)
    indexed: dict[str, dict[str, Any]] = {}
    for record in raw:
        assert isinstance(record, dict)
        policy_number = record["policy_number"]
        assert isinstance(policy_number, str)
        indexed[policy_number] = record
    return indexed


VALID = _index_payloads("fnol_valid.json")
INVALID = _index_payloads("fnol_invalid.json")
POLICIES = _index_policies()


@contextmanager
def override_dependencies(
    *,
    policy_client: StubPolicyClient | None = None,
    repository: NotificationRepository | None = None,
) -> Iterator[TestClient]:
    """Bind FastAPI overrides for one test and always clear them afterward."""

    bound_client = policy_client if policy_client is not None else StubPolicyClient()
    bound_repository = repository if repository is not None else NotificationRepository()
    app.dependency_overrides[get_policy_client] = lambda: bound_client
    app.dependency_overrides[get_repository] = lambda: bound_repository
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """A TestClient with a fresh in-memory repository and a working policy stub."""

    with override_dependencies() as test_client:
        yield test_client


def _json_object(response: Any) -> dict[str, Any]:
    body = response.json()
    assert isinstance(body, dict)
    return body


def _assert_error_envelope(body: dict[str, Any]) -> dict[str, Any]:
    assert "code" in body
    assert "message" in body
    assert "detail" in body
    assert isinstance(body["code"], str)
    assert isinstance(body["message"], str)
    assert isinstance(body["detail"], dict)
    return body["detail"]


def _as_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _assert_detail(
    detail: dict[str, Any],
    *,
    keys: tuple[str, ...],
    payload: dict[str, Any],
    expected_rule: str,
) -> None:
    for key in keys:
        assert key in detail
    if "rule" in keys:
        assert detail["rule"] == expected_rule
    if "policy_number" in keys:
        assert detail["policy_number"] == payload["policy_number"]
    if "loss_date" in keys:
        assert detail["loss_date"] == payload["loss_date"]
    if "claim_type" in keys:
        assert detail["claim_type"] == payload["claim_type"]
    if "estimated_amount" in keys:
        assert _as_decimal(detail["estimated_amount"]) == _as_decimal(payload["estimated_amount"])

    policy = POLICIES.get(str(payload["policy_number"]))
    if policy is None:
        return
    if "effective_date" in keys:
        assert detail["effective_date"] == policy["effective_date"]
    if "expiry_date" in keys:
        assert detail["expiry_date"] == policy["expiry_date"]
    if "cancellation_date" in keys:
        assert detail["cancellation_date"] == policy["cancellation_date"]
    if "limit" in keys:
        assert _as_decimal(detail["limit"]) == _as_decimal(policy["limit"])
    if "permitted_claim_types" in keys:
        assert list(detail["permitted_claim_types"]) == list(policy["permitted_claim_types"])


class TestSuccessfulNotification:
    """Contract section 3: a well-formed admissible notification is recorded."""

    def test_valid_notification_is_recorded(self, client: TestClient) -> None:
        response = client.post(NOTIFICATIONS_URL, json=VALID["VALID-01"])
        body = _json_object(response)

        assert response.status_code == 201
        assert body["status"] == "recorded"
        assert isinstance(body["claim_reference"], str)
        assert CLAIM_REFERENCE.match(body["claim_reference"])


class TestValidationRuleRefusals:
    """Contract section 4.2: each invalid fixture maps to one rule, code, and status."""

    @pytest.mark.parametrize(
        ("entry_id", "expected_status", "expected_code", "expected_rule", "detail_keys", "seed_id"),
        [
            pytest.param(
                "INVALID-01",
                422,
                "POLICY_NOT_FOUND",
                "V-1",
                ("rule", "policy_number"),
                None,
                id="V-1-POLICY_NOT_FOUND",
            ),
            pytest.param(
                "INVALID-02",
                422,
                "LOSS_BEFORE_INCEPTION",
                "V-2",
                ("rule", "policy_number", "loss_date", "effective_date"),
                None,
                id="V-2-LOSS_BEFORE_INCEPTION",
            ),
            pytest.param(
                "INVALID-03",
                422,
                "LOSS_AFTER_EXPIRY",
                "V-3",
                ("rule", "policy_number", "loss_date", "expiry_date"),
                None,
                id="V-3-LOSS_AFTER_EXPIRY",
            ),
            pytest.param(
                "INVALID-04",
                422,
                "AMOUNT_EXCEEDS_LIMIT",
                "V-4",
                ("rule", "policy_number", "estimated_amount", "limit"),
                None,
                id="V-4-AMOUNT_EXCEEDS_LIMIT",
            ),
            pytest.param(
                "INVALID-05",
                422,
                "TYPE_NOT_COVERED",
                "V-5",
                ("rule", "policy_number", "claim_type", "permitted_claim_types"),
                None,
                id="V-5-TYPE_NOT_COVERED",
            ),
            pytest.param(
                "INVALID-06",
                409,
                "DUPLICATE_NOTIFICATION",
                "V-6",
                (),
                "VALID-01",
                id="V-6-DUPLICATE_NOTIFICATION",
            ),
            pytest.param(
                "INVALID-07",
                422,
                "POLICY_CANCELLED",
                "V-7",
                ("rule", "policy_number", "loss_date", "cancellation_date"),
                None,
                id="V-7-POLICY_CANCELLED",
            ),
        ],
    )
    def test_invalid_fnol_entry_returns_contract_code(
        self,
        client: TestClient,
        entry_id: str,
        expected_status: int,
        expected_code: str,
        expected_rule: str,
        detail_keys: tuple[str, ...],
        seed_id: str | None,
    ) -> None:
        if seed_id is not None:
            seed_response = client.post(NOTIFICATIONS_URL, json=VALID[seed_id])
            assert seed_response.status_code == 201

        payload = INVALID[entry_id]
        response = client.post(NOTIFICATIONS_URL, json=payload)
        body = _json_object(response)
        detail = _assert_error_envelope(body)

        assert response.status_code == expected_status
        assert body["code"] == expected_code
        if detail_keys:
            _assert_detail(
                detail,
                keys=detail_keys,
                payload=payload,
                expected_rule=expected_rule,
            )


class TestUninterpretableRequests:
    """Contract sections 2.4 and 6: a body that cannot be interpreted is 400."""

    def test_missing_required_field_is_schema_failure_not_a_rule(
        self, client: TestClient
    ) -> None:
        payload = dict(VALID["VALID-01"])
        del payload["estimated_amount"]

        response = client.post(NOTIFICATIONS_URL, json=payload)
        body = _json_object(response)
        _assert_error_envelope(body)

        assert response.status_code == 400
        assert body["code"] == "SCHEMA_VALIDATION_FAILED"
        assert body["code"] not in RULE_CODES

    def test_unrecognized_field_is_rejected_not_silently_accepted(
        self, client: TestClient
    ) -> None:
        payload = dict(VALID["VALID-01"])
        payload["unexpected_field"] = "should-not-be-accepted"

        response = client.post(NOTIFICATIONS_URL, json=payload)
        body = _json_object(response)

        assert response.status_code != 201
        assert response.status_code == 400
        _assert_error_envelope(body)
        assert body["code"] == "SCHEMA_VALIDATION_FAILED"
        assert "claim_reference" not in body

    def test_malformed_json_is_schema_failure(self, client: TestClient) -> None:
        response = client.post(
            NOTIFICATIONS_URL,
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        body = _json_object(response)
        _assert_error_envelope(body)

        assert response.status_code == 400
        assert body["code"] == "SCHEMA_VALIDATION_FAILED"


class TestUnsupportedMediaType:
    """Contract section 6: Content-Type other than application/json is 415."""

    def test_wrong_content_type_is_unsupported_media_type(self, client: TestClient) -> None:
        response = client.post(
            NOTIFICATIONS_URL,
            content=json.dumps(VALID["VALID-01"]).encode(),
            headers={"Content-Type": "text/plain"},
        )
        body = _json_object(response)
        _assert_error_envelope(body)

        assert response.status_code == 415
        assert body["code"] == "UNSUPPORTED_MEDIA_TYPE"


class TestPolicyMasterDependencyFailures:
    """Contract sections 1 and 6: a policy master that did not answer is a 5xx."""

    def test_policy_master_timeout(self) -> None:
        with override_dependencies(
            policy_client=StubPolicyClient(fail_with="timeout")
        ) as client:
            response = client.post(NOTIFICATIONS_URL, json=VALID["VALID-01"])
        body = _json_object(response)
        _assert_error_envelope(body)

        assert response.status_code == 504
        assert (response.status_code < 500) is False
        assert body["code"] == "POLICY_MASTER_TIMEOUT"

    def test_policy_master_unavailable(self) -> None:
        with override_dependencies(
            policy_client=StubPolicyClient(fail_with="unreachable")
        ) as client:
            response = client.post(NOTIFICATIONS_URL, json=VALID["VALID-01"])
        body = _json_object(response)
        _assert_error_envelope(body)

        assert response.status_code == 503
        assert (response.status_code < 500) is False
        assert body["code"] == "POLICY_MASTER_UNAVAILABLE"

    def test_policy_master_invalid_response(self) -> None:
        with override_dependencies(
            policy_client=StubPolicyClient(fail_with="unparsable")
        ) as client:
            response = client.post(NOTIFICATIONS_URL, json=VALID["VALID-01"])
        body = _json_object(response)
        _assert_error_envelope(body)

        assert response.status_code == 502
        assert (response.status_code < 500) is False
        assert body["code"] == "POLICY_MASTER_INVALID_RESPONSE"

    def test_policy_not_found_is_distinguishable_from_dependency_failures(
        self, client: TestClient
    ) -> None:
        not_found = client.post(NOTIFICATIONS_URL, json=INVALID["INVALID-01"])
        not_found_body = _json_object(not_found)
        not_found_detail = _assert_error_envelope(not_found_body)

        assert not_found.status_code == 422
        assert not_found_body["code"] == "POLICY_NOT_FOUND"
        assert "rule" in not_found_detail
        assert "policy_number" in not_found_detail

        for reason, expected_status, expected_code in DEPENDENCY_FAILURES:
            with override_dependencies(
                policy_client=StubPolicyClient(fail_with=reason)
            ) as failing_client:
                failed = failing_client.post(NOTIFICATIONS_URL, json=VALID["VALID-01"])
            failed_body = _json_object(failed)
            failed_detail = _assert_error_envelope(failed_body)

            assert failed.status_code == expected_status
            assert failed_body["code"] == expected_code
            assert not_found.status_code != failed.status_code
            assert not_found_body["code"] != failed_body["code"]
            assert set(not_found_detail.keys()) != set(failed_detail.keys())


class TestDuplicateNotification:
    """Contract section 4.2 V-6: identity is policy_number, loss_date, and claim_type."""

    def test_second_post_of_same_payload_is_duplicate(self) -> None:
        repository = NotificationRepository()
        payload = VALID["VALID-01"]

        with override_dependencies(repository=repository) as client:
            first = client.post(NOTIFICATIONS_URL, json=payload)
            second = client.post(NOTIFICATIONS_URL, json=payload)

        first_body = _json_object(first)
        second_body = _json_object(second)
        _assert_error_envelope(second_body)

        assert first.status_code == 201
        assert first_body["status"] == "recorded"
        assert second.status_code == 409
        assert second_body["code"] == "DUPLICATE_NOTIFICATION"
