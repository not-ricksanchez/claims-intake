# Claims Intake Service

A service that accepts a first notice of loss, validates it against the policy
master and the rule table in `docs/api-contract.md`, and either records a
notification and issues a claim reference or refuses the submission with a
specific reason.

## Where things are

| Path | What it holds |
| --- | --- |
| `docs/api-contract.md` | What the service accepts, returns, and refuses. The authority. |
| `docs/requirements-brief.md` | The open work items and their acceptance criteria. |
| `docs/payload-triage.md` | Your Day 1 classification of the edge payloads. |
| `data/` | Synthetic policies and notification payloads. |
| `src/claims/` | The service. |
| `tests/` | Unit tests mirror `src/claims/`. Integration tests exercise HTTP. |

## Working in this repository

You are inside a Linux container. Confirm it before you start:

```
uname -sm     # Linux aarch64
pwd           # /workspaces/claims-intake
```

Dependencies are installed when the container is created. There is no install
step in any assignment this week. If a tool you need is missing, that is a defect
in the image specification and should be reported rather than worked around.

```
uv run pytest
uv run ruff check .
uv run mypy src tests
```

Those three commands match the CI workflow at `/.github/workflows/checks.yaml`
and `[tool.mypy]` in `pyproject.toml`. Run them from this directory
(`claims-intake/`).

## Running the service

The HTTP surface is a FastAPI app at `claims.api.routes:app`. From this
directory, start it with:

```
uv run uvicorn claims.api.routes:app --host 0.0.0.0 --port 8000
```

The process listens on port 8000. The endpoint is `POST /notifications` with
`Content-Type: application/json`. Request fields, success shape, and error
envelope are specified in `docs/api-contract.md` (sections 2, 3, 5, and 6).

The curl bodies below are the `payload` objects from `data/fnol_valid.json`
entry `VALID-01` and `data/fnol_invalid.json` entry `INVALID-03`. They are
not invented examples.

### Accepted notification (201)

```
curl -i -X POST http://127.0.0.1:8000/notifications \
  -H "Content-Type: application/json" \
  -d '{
    "policy_number": "MOT-4471",
    "loss_date": "2026-04-02",
    "claim_type": "collision",
    "estimated_amount": "4200.00",
    "description": "Rear ended at a junction."
  }'
```

A notification that passes every rule is recorded. The response is
`201 Created`:

```
{
  "claim_reference": "CLM-2026-000001",
  "status": "recorded"
}
```

`claim_reference` matches `CLM-YYYY-NNNNNN`. `YYYY` is the calendar year in
which the notification was recorded; `NNNNNN` is a zero-padded sequence the
service issues and never reuses. The value above is what a freshly started
process returns for its first recorded notification in 2026. Later successes
increment the sequence. `status` is `recorded` on every success this contract
defines.

### Rejected notification (422)

`INVALID-03` is well formed JSON with the required fields, but it is not
admissible: policy `MOT-4489` expires on `2026-02-28` and the loss date is
`2026-03-20`. Evaluation stops at rule V-3.

```
curl -i -X POST http://127.0.0.1:8000/notifications \
  -H "Content-Type: application/json" \
  -d '{
    "policy_number": "MOT-4489",
    "loss_date": "2026-03-20",
    "claim_type": "theft",
    "estimated_amount": "8000.00",
    "description": "Loss falls after the policy expiry date."
  }'
```

The response is `422 Unprocessable Entity` with the error envelope from
contract sections 5 and 6:

```
{
  "code": "LOSS_AFTER_EXPIRY",
  "message": "The loss date falls after the policy expiry date.",
  "detail": {
    "rule": "V-3",
    "policy_number": "MOT-4489",
    "loss_date": "2026-03-20",
    "expiry_date": "2026-02-28"
  }
}
```

Every non-2xx response uses `{ "code", "message", "detail" }`. Callers branch
on `code` (`LOSS_AFTER_EXPIRY` here, status 422 per section 6). `message` is
not a stable contract field. `detail` is always an object; its keys vary by
code. The keys shown are the ones section 5 lists for `LOSS_AFTER_EXPIRY`.

## Running in a container

The `Dockerfile` in this directory packages the same uvicorn process. Build
from here (next to `pyproject.toml`):

```
docker build -t claims-intake .
```

`python:3.12-slim` is a multi-arch image. A plain `docker build` produces an
image for the architecture of the machine that runs the build. That is the
right command for local use. This repository's local environment is arm64:
the check in "Working in this repository" (`uname -sm`) prints
`Linux aarch64`, so a default build here is `linux/arm64` and matches this
devcontainer.

Pass `--platform` only when you are building an image for a *target*
architecture that is not the build host. For example, producing a `linux/amd64`
image from this arm64 Mac or devcontainer, to run on an amd64 CI runner or
cloud host:

```
docker build --platform linux/amd64 -t claims-intake .
```

Do not add that flag for a container you intend to run here. It would force
amd64 emulation on an arm64 machine instead of a native image.

Run the image, publishing port 8000 to the host:

```
docker run --rm -p 8000:8000 claims-intake
```

The curl examples in "Running the service" then hit `http://127.0.0.1:8000`
the same way they do against a local uvicorn process.

## Data

Everything in `data/` is synthetic and was authored for this program. It contains
no real client data and no named clients.
