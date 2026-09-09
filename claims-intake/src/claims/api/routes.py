"""HTTP surface for the claims intake service.

This layer does three things and no more: it parses the request, it calls the
service, and it maps the outcome to a status code. It holds no rule logic. A rule
that appears here is a rule the service layer cannot be tested for.

Day 4 lab. Implement against `docs/api-contract.md` sections 5 and 6.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from claims.models import ClaimRecord, ErrorCode, NotificationRequest, RuleFailure
from claims.policy_client import PolicyClient, PolicyLookupFailed, StubPolicyClient
from claims.repository import NotificationRepository
from claims.service import submit_notification

app = FastAPI(title="Claims Intake Service")
_policy_client: PolicyClient = StubPolicyClient()
_repository = NotificationRepository()

_RULE_STATUS: dict[ErrorCode, int] = {
    "POLICY_NOT_FOUND": 422,
    "LOSS_BEFORE_INCEPTION": 422,
    "POLICY_CANCELLED": 422,
    "LOSS_AFTER_EXPIRY": 422,
    "AMOUNT_EXCEEDS_LIMIT": 422,
    "TYPE_NOT_COVERED": 422,
    "DUPLICATE_NOTIFICATION": 409,
}

_RULE_MESSAGE: dict[ErrorCode, str] = {
    "POLICY_NOT_FOUND": "No policy exists for the given policy number.",
    "LOSS_BEFORE_INCEPTION": "The loss date falls before the policy effective date.",
    "POLICY_CANCELLED": "The policy was cancelled on or before the loss date.",
    "LOSS_AFTER_EXPIRY": "The loss date falls after the policy expiry date.",
    "AMOUNT_EXCEEDS_LIMIT": "The estimated amount exceeds the policy limit.",
    "TYPE_NOT_COVERED": "The claim type is not covered by this policy.",
    "DUPLICATE_NOTIFICATION": "A notification for this loss has already been recorded.",
}


def get_policy_client() -> PolicyClient:
    return _policy_client


def get_repository() -> NotificationRepository:
    return _repository


def _error_response(code: str, message: str, detail: dict[str, Any], status_code: int) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, "detail": detail}, status_code=status_code)


def _is_json_content_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "application/json"


def _problem(error_type: str, message: str) -> str:
    if error_type == "missing":
        return "required field absent"
    if error_type == "extra_forbidden":
        return "field not defined"
    if error_type == "literal_error":
        return "value outside vocabulary"
    if error_type.endswith("_type") or "parsing" in error_type:
        return "wrong type"
    return message


def _violations_from(err: ValidationError) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for error in err.errors():
        field = ".".join(str(part) for part in error["loc"])
        violations.append({"field": field, "problem": _problem(error["type"], error["msg"])})
    return violations


def _policy_lookup_error(exc: PolicyLookupFailed) -> JSONResponse:
    detail: dict[str, Any] = {"dependency": "policy_master"}
    match exc.reason:
        case "unreachable":
            return _error_response(
                "POLICY_MASTER_UNAVAILABLE",
                "The policy master could not be reached.",
                {**detail, "retryable": True},
                503,
            )
        case "timeout":
            return _error_response(
                "POLICY_MASTER_TIMEOUT",
                "The policy master did not respond in time.",
                {**detail, "retryable": True},
                504,
            )
        case "unparsable":
            return _error_response(
                "POLICY_MASTER_INVALID_RESPONSE",
                "The policy master returned a response that could not be parsed.",
                {**detail, "retryable": False},
                502,
            )


def _rule_failure_detail(
    failure: RuleFailure,
    notification: NotificationRequest,
    policy_client: PolicyClient,
) -> dict[str, Any]:
    if failure.code == "POLICY_NOT_FOUND":
        return {"rule": failure.rule, "policy_number": notification.policy_number}

    # Re-fetch is read-only enrichment for the error envelope. V-1 already passed.
    policy = policy_client.get_policy(notification.policy_number)

    if failure.code == "LOSS_BEFORE_INCEPTION":
        return {
            "rule": failure.rule,
            "policy_number": notification.policy_number,
            "loss_date": notification.loss_date.isoformat(),
            "effective_date": policy.effective_date.isoformat(),
        }
    if failure.code == "LOSS_AFTER_EXPIRY":
        return {
            "rule": failure.rule,
            "policy_number": notification.policy_number,
            "loss_date": notification.loss_date.isoformat(),
            "expiry_date": policy.expiry_date.isoformat(),
        }
    if failure.code == "AMOUNT_EXCEEDS_LIMIT":
        return {
            "rule": failure.rule,
            "policy_number": notification.policy_number,
            "estimated_amount": str(notification.estimated_amount),
            "limit": str(policy.limit),
        }
    if failure.code == "TYPE_NOT_COVERED":
        return {
            "rule": failure.rule,
            "policy_number": notification.policy_number,
            "claim_type": notification.claim_type,
            "permitted_claim_types": list(policy.permitted_claim_types),
        }
    if failure.code == "DUPLICATE_NOTIFICATION":
        return {
            "rule": failure.rule,
            "policy_number": notification.policy_number,
            "loss_date": notification.loss_date.isoformat(),
            "claim_type": notification.claim_type,
        }
    cancellation = policy.cancellation_date
    return {
        "rule": failure.rule,
        "policy_number": notification.policy_number,
        "loss_date": notification.loss_date.isoformat(),
        "cancellation_date": cancellation.isoformat() if cancellation is not None else None,
    }


@app.post(
    "/notifications",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": NotificationRequest.model_json_schema(),
                }
            },
        }
    },
)


async def create_notification(
    request: Request,
    policy_client: Annotated[PolicyClient, Depends(get_policy_client)],
    repository: Annotated[NotificationRepository, Depends(get_repository)],
) -> JSONResponse:
    try:
        raw_content_type = request.headers.get("content-type")
        if not _is_json_content_type(raw_content_type):
            return _error_response(
                "UNSUPPORTED_MEDIA_TYPE",
                "Content-Type must be application/json.",
                {"content_type": raw_content_type},
                415,
            )

        try:
            data: object = json.loads(await request.body())
        except json.JSONDecodeError:
            return _error_response(
                "SCHEMA_VALIDATION_FAILED",
                "The request could not be interpreted.",
                {"violations": [{"field": "<body>", "problem": "body is not valid JSON"}]},
                400,
            )

        try:
            notification = NotificationRequest.model_validate(data)
        except ValidationError as err:
            return _error_response(
                "SCHEMA_VALIDATION_FAILED",
                "The request could not be interpreted.",
                {"violations": _violations_from(err)},
                400,
            )

        result = submit_notification(notification, policy_client, repository)
        if isinstance(result, ClaimRecord):
            return JSONResponse(
                {"claim_reference": result.claim_reference, "status": "recorded"},
                status_code=201,
            )

        return _error_response(
            result.code,
            _RULE_MESSAGE[result.code],
            _rule_failure_detail(result, notification, policy_client),
            _RULE_STATUS[result.code],
        )
    except PolicyLookupFailed as exc:
        return _policy_lookup_error(exc)
    except Exception:  # noqa: BLE001 - contract section 6 maps any other fault to 500
        return _error_response(
            "INTERNAL_ERROR",
            "An unexpected error occurred inside the service.",
            {},
            500,
        )
