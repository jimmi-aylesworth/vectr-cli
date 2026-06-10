# VECTR GraphQL API — Python CLI

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)

A menu-driven Python application for the
[VECTR](https://vectr.io) purple-team platform's GraphQL API.

Every selection uses a numbered-menu style picker to keep things clean.
**Every create / update / delete is gated behind a secondary
confirmation prompt** shown *before* any change is sent to the server.

---

## Contents

| File                | Purpose                                                        |
|---------------------|----------------------------------------------------------------|
| `vectr.py`          | Interactive CLI application — **run this**                      |
| `vectr_client.py`   | API client library (transport + typed methods)                 |
| `requirements.txt`  | Python dependencies                                            |
| `.env.example`      | Template for the environment variables                        |
| `README.md`         | This file                                                     |

---

## API documentation references

This tool is built directly against the official VECTR docs:

- Overview & queries — https://docs.vectr.io/graphql/
- API key / authentication — https://docs.vectr.io/API-Key/
- Test case mutations — https://docs.vectr.io/graphql/testcases/
- Campaign mutations — https://docs.vectr.io/graphql/campaigns/
- Assessment mutations — https://docs.vectr.io/graphql/assessments/
- Schema (Query) — https://docs.vectr.io/graphql/schema/query.doc.html
- Schema (Mutation) — https://docs.vectr.io/graphql/schema/mutation.doc.html

Individual methods in `vectr_client.py` cite the specific schema type or doc
section they implement.

---

## Setup

> **WSL note:** create your virtual environment on the Linux filesystem
> (e.g. `~/vectr-cli`), **not** under `/mnt/c` or `/mnt/d`. Running a venv
> interpreter from a Windows-mounted drive fails under WSL.

```bash
python3 -m venv vectr-cli
cd vectr-cli
source ./bin/activate
pip install -r requirements.txt
```

## Configuration

Credentials and connection settings come from environment variables — nothing is
hard-coded. Copy the template and fill it in:

```bash
cp .env.example .env
# edit .env, then load it:
set -a && source .env && set +a
```

Or export directly:

```bash
export VECTR_BASE_URL="https://your-vectr-host"   # add :8081 for self-hosted
export VECTR_API_KEY_ID="your_key_id"
export VECTR_API_KEY_SECRET="your_key_secret"
export VECTR_VERIFY_SSL="true"                    # ONLY for self-signed dev certs
```

Create an API key in VECTR via **Profile → API Key tab → Create API Key**
(https://docs.vectr.io/API-Key/#create-an-api-key). The header format is
`VEC1 <key_id>:<key_secret>`.

## Run

```bash
python3 vectr.py
```

On launch the app queries the live `databases` list, lets you pick an
environment, then presents the main menu.

---

## Features

After picking a database, the main menu exposes:

- **Assessments** — list / view / create / update / delete
- **Campaigns** — list / view (with test cases) / create / delete
- **Test Cases** — list / view / create (template-match by name) /
  create (no template) / update / delete
- **Reference Data** (read-only) — organizations, phases, kill chains,
  outcomes, tag types, tags
- **Environment Tools** (read-only) — red tools, blue tools, defense layers,
  vendors, defense tool products
- **Library / Templates** — list library test cases / campaigns / assessments;
  create test case template; clone template; create campaign template
- **App / Server Info** — read application config
- **Switch Database** — re-pick the active environment
- **Run Raw GraphQL** — free-form query or mutation (mutations are
  confirmation-gated)

---

## Confirmation model

Read operations run immediately. Any operation that writes to VECTR
(`create`, `update`, `delete`, or a raw mutation) first prints a
**CONFIRM ACTION** block summarizing the exact target and payload, and only
proceeds when you type `y`. Anything else cancels with no changes made.

```
----------------------------------------------------------------
  CONFIRM ACTION
----------------------------------------------------------------
  DELETE this assessment. This is destructive and cannot be undone.
    db: Developer_TST
    id: 57
    name: Pythonic CMD Line
----------------------------------------------------------------
  Proceed? Type 'y' to confirm, anything else to cancel:
```

---

## Using the client library directly

```python
from vectr_client import VECTRClient

with VECTRClient() as client:                 # reads env vars
    for db in client.list_databases():
        print(db["id"], db["name"])

    cases = client.get_all_testcases(db="MY_ENV")
    print(f"{len(cases)} test cases")
```

Errors are surfaced as typed exceptions:

```python
from vectr_client import (
    VECTRAuthError,      # 401/403 or missing credentials
    VECTRGraphQLError,   # GraphQL-level errors in the response body
    VECTRRequestError,   # unexpected HTTP status
    VECTRClientError,    # base class / config / network / TLS
)
```

---

## Security notes

- Credentials and TLS settings come from environment variables; nothing is
  hard-coded.
- TLS verification is **on** unless you explicitly set `VECTR_VERIFY_SSL=false`.
- All user input is passed as GraphQL **variables** (structured JSON), never
  interpolated into the query string — preventing query injection.
- API keys inherit the permissions of the user that created them; use a
  least-privilege account for automation, and store the secret in a password
  manager or secrets store.

---

## Troubleshooting

| Symptom                                             | Fix                                                                 |
|-----------------------------------------------------|---------------------------------------------------------------------|
| `ensurepip ... non-zero exit status 1` (WSL)        | Create the venv under `~`, not a `/mnt/<drive>`                     |
| Script runs as shell / `import: command not found`  | Run with `python3 vectr.py`, or ensure the `#!/usr/bin/env python3` shebang is present |
| `Connection ... timed out` on `:8081`               | SaaS instances use port 443 — drop `:8081` from `VECTR_BASE_URL`    |
| `CERTIFICATE_VERIFY_FAILED ... self-signed`         | Set `VECTR_VERIFY_SSL=false` for the dev instance                   |
| `Field 'ids' ... is undefined` on delete            | Fixed in this build — delete payloads now select `deletedIds`       |
| `Invalid argument value(s)` on assessment update    | Server-side limitation on some VECTR builds: the `assessment.update` resolver rejects every schema-valid update. The CLI now reports this clearly. Create/delete and **test case update** work normally. |

---

## Contributing

Issues and pull requests are welcome. If you discover an API quirk, the fastest
way to pin it down is the **Run Raw GraphQL** menu option combined with `__type`
introspection (see `docs/` for a worked example).

## License

This project is licensed under the **GNU General Public License v3.0 or later**
(GPL-3.0-or-later). See the [LICENSE](LICENSE) file for the full text.

## Disclaimer

This is an independent, community tool and is not affiliated with or endorsed by
Security Risk Advisors. "VECTR" is a product of Security Risk Advisors. Use
against systems you are authorized to access.
