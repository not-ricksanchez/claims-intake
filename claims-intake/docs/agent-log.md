# Agent decision log

## Accepted: keep V-6 out of POLICY_RULES

The agent put the duplicate check outside the policy-rule table and had
`submit_notification` run it between V-7 and V-2. I kept that.

Contract 4.1 is V-1, V-7, V-6, V-2, … — V-6 sits in the middle of the policy
rules. If it were just tacked on the end of `POLICY_RULES`, a duplicate whose
loss is also before inception would come back as `LOSS_BEFORE_INCEPTION`.
That's the wrong code. WI-0151 AC-1 says it's a duplicate. `repository.record`
doesn't run rules, so that row can exist.

## Corrected: workflow has to live at the repo root

The agent wrote `.github/workflows/checks.yaml` at the git root. I moved it
under `claims-intake/.github/` so it sat next to the package. Actions stayed
empty. GitHub never saw the file, so ruff/mypy/pytest couldn't fail the PR.

I moved it back to rrot for github to recognize it as a CI pipeline file.


## Failing required check blocked the merge:

Changed an assertion in test_validation.py to make the pipeline fail.
Once the change was pushed, the CI pieplined failed successfully.
The changes were reverted in a later commit, and now the piepline passes all the checks.