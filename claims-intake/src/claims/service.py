"""Rule evaluation and notification submission.

This module owns the decision. It does not know it was reached over HTTP, which
is why it can be tested by calling a function with a typed object and asserting on
the result with no server running. It does not know where notifications are
stored either. It knows the rules.

Each rule takes the notification and whatever it needs, decides, and returns a
`RuleFailure` that names the rule and the contract code, or `None` if the rule
passed. Nothing prints, nothing raises for an ordinary refusal, and nothing
reaches for a status code, because a status code is a fact about HTTP and this
module does not know about HTTP.
"""

from __future__ import annotations

from collections.abc import Callable

from claims.models import ClaimRecord, NotificationRequest, Policy, RuleFailure
from claims.policy_client import PolicyClient, PolicyNotFound, PolicyRecord
from claims.repository import NotificationRepository

PolicyRule = Callable[[NotificationRequest, Policy], RuleFailure | None]


def evaluate_policy_exists(
    notification: NotificationRequest,
    policy_client: PolicyClient,
) -> RuleFailure | None:
    """V-1. The policy must exist in the policy master.

    This rule is different from the others in one way that matters: it is the only
    one that reaches outside the service, so it is the only one that can fail for
    a reason that is not the caller's fault. `PolicyNotFound` is caught here and
    turned into an ordinary refusal, because a policy that does not exist is a
    fact about the caller's data. `PolicyLookupFailed` is deliberately not caught,
    because the caller did nothing wrong and the HTTP layer has to be able to tell
    the two apart. Contract section 6 fixes what each becomes.

    V-1 short circuits. Every other rule compares against a field on a policy, and
    if there is no policy there is nothing to compare against. Reporting
    LOSS_BEFORE_INCEPTION for a policy number that does not exist is not merely
    unhelpful, it is a false statement about the client's data (WI-0142, AC-4).
    """
    try:
        policy_client.get_policy(notification.policy_number)
    except PolicyNotFound:
        return RuleFailure(rule="V-1", code="POLICY_NOT_FOUND")
    return None


def evaluate_not_cancelled(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-7. Cover must not have ended by cancellation."""
    if policy.cancellation_date is None:
        return None
    if notification.loss_date < policy.cancellation_date:
        return None
    return RuleFailure(rule="V-7", code="POLICY_CANCELLED")


def evaluate_not_duplicate(
    notification: NotificationRequest,
    repository: NotificationRepository,
) -> RuleFailure | None:
    """V-6. The same loss event must not already be recorded.

    This is not a member of POLICY_RULES. Those functions are pure: they read a
    notification and a policy and they return. V-6 has to ask the repository
    whether a record exists, and putting that lookup in the rule table would mix
    deciding with doing. submit_notification calls this between V-7 and V-2 so
    the order in contract section 4.1 is preserved without the pure rules knowing
    that a store exists.
    """
    matching = repository.find_matching(
        notification.policy_number,
        notification.loss_date,
        notification.claim_type,
    )
    if matching is not None:
        return RuleFailure(rule="V-6", code="DUPLICATE_NOTIFICATION")
    return None


def evaluate_loss_after_inception(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-2. The loss must not precede policy inception.

    The boundary is stated in contract section 4.2 and in WI-0142 AC-3. A loss on
    the inception date is covered.
    """
    if notification.loss_date >= policy.effective_date:
        return None
    return RuleFailure(rule="V-2", code="LOSS_BEFORE_INCEPTION")


def evaluate_loss_before_expiry(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-3. The loss must not fall after the policy expiry date."""
    if notification.loss_date <= policy.expiry_date:
        return None
    return RuleFailure(rule="V-3", code="LOSS_AFTER_EXPIRY")


def evaluate_amount_within_limit(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-4. The estimated amount must not exceed the policy limit.

    An amount equal to the limit is within cover, per contract section 4.2.
    """
    if notification.estimated_amount <= policy.limit:
        return None
    return RuleFailure(rule="V-4", code="AMOUNT_EXCEEDS_LIMIT")


def evaluate_claim_type_covered(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """V-5. The claim type must be permitted on the policy's product."""
    if notification.claim_type in policy.permitted_claim_types:
        return None
    return RuleFailure(rule="V-5", code="TYPE_NOT_COVERED")


def _policy_from_record(record: PolicyRecord) -> Policy:
    return Policy.model_validate(
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


# Contract section 4.1 among the rules that are functions of a notification
# and a policy: V-7, V-2, V-3, V-4, V-5. V-1 is the lookup that produced the
# policy. V-6 is a repository query, so it is not in this table;
# submit_notification runs it between V-7 and V-2.
POLICY_RULES: tuple[PolicyRule, ...] = (
    evaluate_not_cancelled,
    evaluate_loss_after_inception,
    evaluate_loss_before_expiry,
    evaluate_amount_within_limit,
    evaluate_claim_type_covered,
)


def evaluate_notification(
    notification: NotificationRequest,
    policy: Policy,
) -> RuleFailure | None:
    """Evaluate the policy-pure rules and return the first refusal, or None.

    A notification can violate several rules at once and the caller sees one
    reason, so the order this function evaluates in is a caller-visible behavior.
    It is fixed by contract section 4.1 and by nothing else. If you find yourself
    choosing an order here, the contract is incomplete and the fix belongs there.

    This function has no side effects and touches nothing outside the two
    arguments. V-1 already happened: the caller holds a policy. V-6 is not
    evaluated here because a duplicate check is a question to the repository,
    and this function does not have one.
    """
    for rule in POLICY_RULES:
        failure = rule(notification, policy)
        if failure is not None:
            return failure
    return None


def submit_notification(
    notification: NotificationRequest,
    policy_client: PolicyClient,
    repository: NotificationRepository,
) -> ClaimRecord | RuleFailure:
    """Validate, and record only if every rule passed.

    Nothing is written before the decision is made. A notification is either
    recorded with a claim reference or it does not exist, and there is no state in
    between for a later reader to interpret.

    Order is contract section 4.1: V-1, V-7, V-6, V-2, V-3, V-4, V-5. V-6 sits
    between two policy-pure rules, which is why it is called here rather than
    being appended to POLICY_RULES. `PolicyLookupFailed` is not caught; the
    HTTP layer maps `reason` to the matching 5xx status.
    """
    try:
        record = policy_client.get_policy(notification.policy_number)
    except PolicyNotFound:
        return RuleFailure(rule="V-1", code="POLICY_NOT_FOUND")

    policy = _policy_from_record(record)

    cancelled = evaluate_not_cancelled(notification, policy)
    if cancelled is not None:
        return cancelled

    duplicate = evaluate_not_duplicate(notification, repository)
    if duplicate is not None:
        return duplicate

    for rule in POLICY_RULES:
        if rule is evaluate_not_cancelled:
            continue
        failure = rule(notification, policy)
        if failure is not None:
            return failure

    return repository.record(notification)
