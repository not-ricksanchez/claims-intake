# Payload Triage

Every payload in `data/fnol_edge.json` classified against `docs/api-contract.md` as you have completed it. The classification records what the contract says the service does, which is not always what the payload obviously violates.

Fill one row per payload. Where a payload is accepted, leave the rule, code, and status columns as `-`.

## Classification

| Payload | Outcome | Rule | Code | Status |
| --- | --- | --- | --- | --- |
| EDGE-01 | accepted |  |  | 201 |
| EDGE-02 | accepted |  |  | 201 |
| EDGE-03 | accepted |  |  | 201 |
| EDGE-04 | rejected | V-7 | POLICY_CANCELLED  | 422 |
| EDGE-05 | rejected | V-2 | LOSS_BEFORE_INCEPTION | 422 |
| EDGE-06 | rejected | V-4 | AMOUNT_EXCEEDS_LIMIT | 422  |
| EDGE-07 | rejected | V-1 | POLICY_NOT_FOUND | 422 |
| EDGE-08 | rejected | 2.4  | SCHEMA_VALIDATION_FAILED | 400 |
| EDGE-09 | rejected | V-5 | TYPE_NOT_COVERED | 422 |
| EDGE-10 | rejected | V-7 | POLICY_CANCELLED | 422 |
| EDGE-11 | rejected | 2.4 | SCHEMA_VALIDATION_FAILED | 400 |
| EDGE-12 | rejected | 2.4 | SCHEMA_VALIDATION_FAILED | 400 |

## Decision log

Three payloads cannot be classified against the contract as it shipped, because the contract left a decision unmade. For each one, record the ambiguity, the decision, its authority, and the alternative you rejected.

A decision recorded here and nowhere else has not been made. Amend `docs/api-contract.md` so that a reader of the contract alone could not arrive at the other reading.

### Decision 1

**Payload.** 

EDGE-07

**The ambiguity.** What the contract failed to determine, and the two readings that were both available.

Section 2.2 describes `policy_number` as "identifier as
held in the policy master" and V-1 requires that it "exists in the policy master", but neither says how a submitted value is compared to the stored one. In this payload, the policy_number arrived as all lower case, wheras the policy master has this policy mumber stores as all uppercase which created a mismatch despite both the policy number being the same with only difference in ther cases(upper and lower). Apart from this abmiguity, this payload would have passed all other checks.

**Decision.** What the service does.

Since the comparison is exact, in terms of matching charaters and not disreagrding the case of the characters, this payload stands rejected by V-1 with "POLICY_NOT_FOUND'.

**Authority.** The work item, acceptance criterion, or product rule that supports it.

Section 1 and Section 2.2.

**Rejected alternative.** The other reading, and why it is wrong rather than merely less preferred.

Case folding is wrong here because if would increase the overhead on the backend and the services, it would also call upon other overhead measures such as removing hyphens, whitespaces andd many other transformations.

**Contract amended.** Section and what changed.

Section 4.2, note beneath the rule table: V-1 compares
`policy_number` to the policy master byte for byte, with no case folding so a value differing only in case is not found.

### Decision 2

**Payload.**

EDGE-11

**The ambiguity.**

Section 2.3 fixes the vocabulary at five values and says
the permitted subset is "a property of the policy record and is evaluated by rule V-5". 'flood' is not absent from a product's subset, it is absent from the vocabulary itself, and the contract did not say which check owns that case. 

**Decision.**

A 'claim_type' outside the five values in section 2.3 is an interpretation failure. EDGE-11 is rejected before any rule runs, with
`SCHEMA_VALIDATION_FAILED` and status 400.

**Authority.**

Section 2.4 defines a 400 as a request that cannot be interpreted, including "a field carried a value of the wrong type", and
assigns the fault to the caller's code.

**Rejected alternative.**

Simply rejecting every input from the user in the 'claim_type' field which is not present in the V-5 claim types list and invoking V-5 there would make the handler's part difficult.

**Contract amended.**

Section 4.1, interpretation stage: a claim type outside
2.3 never reaches V-5. Section 4.2: V-5 assumes the value is already in the vocabulary.

### Decision 3

**Payload.**

EDGE-12

**The ambiguity.**

SEction 2.2 says two decimal places without saying whether that is a rule for the caller or a description of the money type. Refuse it, or round it to 3500.00 and record a claim against `MOT-4476`, which has room under its limit and covers collision. Nothing in the contract chooses.


**Decision.**
Exactly two decimal places, or the request fails interpretation. `SCHEMA_VALIDATION_FAILED`, 400, naming `estimated_amount`.

**Authority.**
Section 2.4 makes an uninterpretable value a 400.

**Rejected alternative.**

Rounding here would create an overhead with the service, the rounding may be floor division based or general rounding which may have an additional case which might make the users input exceed its limit by a dollor and invoke V-4.

**Contract amended.**

Section 4.1, interpretation stage: `estimated_amount`
must exceed zero, and is never rounded.


**Reconciliation note (Day 2)**

Compared every `NotificationRequest` refusal against contract section 6.

Model refusals checked: missing required fields, undefined fields, wrong types
(including unparsable `loss_date`), `claim_type` outside the section 2.3
vocabulary, empty `policy_number`, `estimated_amount` not greater than zero,
and `estimated_amount` with other than two decimal places.

All of these are interpretation failures under section 2.4 and map to
`SCHEMA_VALIDATION_FAILED` / 400. That code is already present in section 6.

Nothing was missing. No contract change required.