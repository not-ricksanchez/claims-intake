# Claims Intake Service: API Contract

Version 0.4. Owned by the claims intake team. Consumed by the claims portal team.

This document is the authority on what the service accepts, what it returns, and under what conditions it refuses. Where the code and this document disagree, the document is correct and the code is a defect.

Sections 1 through 3 are fixed. Do not edit them.

## 1. Purpose and scope

The claims intake service accepts a first notice of loss from the claims portal, validates it against the policy master and a table of business rules, and either records a notification and issues a claim reference or refuses the submission with a specific reason.

**In scope.** Accepting a notification, validating it, and recording it. Issuing a claim reference. Reporting the reason a notification was refused.

**Out of scope.** Adjusting, reserving, payment, and any decision about coverage beyond the rules in section 4. The service decides whether a notification is well formed and admissible. It does not decide whether the claim will be paid.

**The policy master is a dependency, not part of this service.** The service reads policy records from it and does not write to it. A policy that cannot be read is a condition this contract specifies, and it is specified separately from a policy that does not exist, because the two require different action from the caller.

**Compatibility.** Adding a field to a response is a compatible change and callers must ignore fields they do not recognize. Adding a new error code is a compatible change and callers must fall through to default handling for a code they do not recognize. Changing the meaning of an existing code, removing a field, or changing a status code for an existing condition is not compatible and does not happen without a version increment agreed with the portal team.

## 2. Request

### 2.1 Endpoint

```
POST /notifications
Content-Type: application/json
```

### 2.2 Body

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `policy_number` | string | yes | Identifier as held in the policy master. Not empty. |
| `loss_date` | string | yes | Calendar date, `YYYY-MM-DD`. |
| `claim_type` | string | yes | One of the values in 2.3. Not empty. |
| `estimated_amount` | decimal | yes | United States dollars, two decimal places. Greater than zero. |
| `description` | string | no | Free text. Absent and `null` are equivalent. |

The service rejects a body carrying a field not listed above. A misspelled field name is a defect in the caller's code, and accepting the payload with the field ignored would record a notification built from data the caller did not send.

### 2.3 Claim type vocabulary

`collision`, `theft`, `glass`, `liability`, `weather`.

Which of these are admissible on a given notification depends on the product the policy is written on. The vocabulary is fixed by this contract. The permitted subset is a property of the policy record and is evaluated by rule `V-5`.

### 2.4 Well formed against acceptable

A request that cannot be interpreted is refused with status `400`. This means the body was not valid JSON, a required field was absent, a field carried a value of the wrong type, or a field was present that this contract does not define. The caller's code is wrong.

A request that was interpreted and whose content is not admissible is refused with status `422`. The caller's data is wrong, and a person needs to see the reason.

This split is stated here once and holds without exception everywhere else in this document.

## 3. Success response

A notification that passes every rule in section 4 is recorded and the service responds:

```
201 Created
Content-Type: application/json

{
  "claim_reference": "CLM-2026-000317",
  "status": "recorded"
}
```

**`claim_reference`** matches the pattern `CLM-YYYY-NNNNNN`, where `YYYY` is the calendar year in which the notification was recorded and `NNNNNN` is a zero padded sequence. A claim reference is unique across all recorded notifications and is never reissued. It is the value the claims handler quotes and the value every downstream system keys on.

**`status`** is `recorded` on every success response this contract defines. It exists because the portal displays it and because a future state that is not `recorded` is foreseeable. Callers must not treat it as constant.

A refused notification is never recorded and no claim reference is issued. There is no partial outcome: either a notification exists with a reference, or nothing was written.

## 4. Validation

### 4.1 Evaluation order

Rules are evaluated based on the position as stated below. Evaluation stops at the first failure and that rule's code is returned. V-1 short circuits: if it fails, no rule that reads a policy field is evaluated.

| Position | Rule |
| --- | --- |
| 1 | V-1 | 
| 2 | V-7 | 
| 3 | V-6 | 
| 4 | V-2 | 
| 5 | V-3 | 
| 6 | V-4 | 
| 7 | V-5 | 

### 4.2 Rule table

| ID  | Condition                                      | Code                    | Status |
| --- | ---------------------------------------------- | ----------------------- | ------ |
| V-1 | `policy_number` exists in the policy master    | `POLICY_NOT_FOUND`      | 422    |
| V-2 | `loss_date` >= policy `effective_date`         | `LOSS_BEFORE_INCEPTION` | 422    |
| V-3 | `loss_date` <= policy `expiry_date`            | `LOSS_AFTER_EXPIRY`     | 422    |
| V-4 | `estimated_amount` <= policy `limit`           | `AMOUNT_EXCEEDS_LIMIT`  | 422    |
| V-5 | `claim_type` permitted on the policy's product | `TYPE_NOT_COVERED`      | 422    |
| V-6 | no recorded notification exits where `policy_number` == recorded `policy_number` AND `loss_date` == recorded `loss_date` AND `claim_type` == recorded `claim_type` | `DUPLICATE_NOTIFICATION`      | 409    |
| V-7 | `loss_date` < policy `cancellation_date` OR policy `cancellation_date` is `null` | `POLICY_CANCELLED`      | 422    |

Boundaries are inclusive as written. A loss on the inception date is
covered (WI-0142, AC-3). An amount equal to the limit is within cover.
V-7 is the one exclusive boundary: cancellation takes effect at the start of the cancellation date, so `loss_date` == `cancellation_date` fails V-7 and is not covered (WI-0158, AC-2). Where `cancellation_date` is `null` the policy was not cancelled and V-7 passes without comparing dates (WI-0158, AC-3).

V-6 compares against recorded notifications only. A submission that was
refused was never recorded, so it cannot be the match that makes a later
submission a duplicate (WI-0151, AC-3). The comparison is on the three
fields named and no others: `estimated_amount` and `description` are not
part of the identity of a loss event.


## 5. Error envelope

Every non-2xx response returns this shape and no other.
 {"code": ..., "message": ..., "detail": { ... } }

Stable: code is always present, is a string and is present in section 6 along with appropriate status from section 6. Detail is always present and is an object.

Not stable: message is verbose and its language keeps changing. The keys inside the detail object are not stable across error codes.

What follows for a caller: Branch on code, render message.

Caller's reliance on inside detail: 

Rely: keys listed for code present with the type.

Not rely: key order, detail being non empty for an unrecognized code.

422 LOSS_AFTER_EXPIRY(a rule failure)
{ "code": "LOSS_AFTER_EXPIRY",
             "message": "The loss date falls after the policy expiry date.",
             "detail": { "rule": "V-3", "policy_number": "MOT-4489",
                         "loss_date": "2026-08-20", "expiry_date": "2026-07-28" } }

400 SCHEMA_VALIDATION_FAILED(uninterpretable)
{"code": "SCHEMA_VALIDATION_FAILED",
             "message": "The request could not be interpreted.",
             "detail": { "violations": [
               { "field": "estimated_amount", "problem": "required field absent" },
               ] }}

503 POLICY_MASTER_UNAVAILABLE(a policy master that did not answer)
{ "code": "POLICY_MASTER_UNAVAILABLE",
             "message": "The policy master could not be reached.",
             "detail": { "dependency": "policy_master", "retryable": true,
             }}
## 6. Status code mapping

| Code | Status | Condition |
| --- | --- | --- |
| `SCHEMA_VALIDATION_FAILED` | 400 | body not valid JSON, required field absent, wrong type, undefined field, or a value outside a vocabulary this contract fixes |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | Content-Type is not application/json |
| `POLICY_NOT_FOUND` | 422 | V-1|
| `LOSS_BEFORE_INCEPTION` | 422 | V-2 |
| `POLICY_CANCELLED` | 422 | V-7 |
| `LOSS_AFTER_EXPIRY` | 422 | V-3 |
| `AMOUNT_EXCEEDS_LIMIT` | 422 | V-4 |
| `TYPE_NOT_COVERED` | 422 | V-5 |
| `DUPLICATE_NOTIFICATION` | 409 | V-6 |
| `POLICY_MASTER_INVALID_RESPONSE` | 502 | Policy master answered with something the service cannot parse|
| `POLICY_MASTER_UNAVAILABLE` | 503 | Policy master unreachable |
| `POLICY_MASTER_TIMEOUT` | 504 | Policy master accepted the request and did not answer within the read timeout |
| `INTERNAL_ERROR` | 500 | Any fault inside this service not covered above |