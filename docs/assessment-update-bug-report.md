# Bug Report: `assessment.update` GraphQL mutation rejects all schema-valid input

## Summary

The GraphQL mutation `assessment.update` fails for **every** assessment with the
runtime error `Invalid argument value(s)`, even when the request exactly matches
the schema as reported by live introspection. The failure occurs at the resolver
(post-validation) and affects all assessments tested. Sibling write operations —
`assessment.create`, `assessment.delete`, and `testCase.update` — all succeed
against the same instance with the same API key, which isolates the problem to
the `assessment.update` resolver specifically.

## Severity / Impact

- **Type:** Functional defect (not a security vulnerability)
- **Impact:** Assessment metadata (name, description, organizations, tags, attack
  lifecycle) cannot be modified via the GraphQL API. Any automation or
  integration that edits existing assessments programmatically is blocked.
  Workarounds via the API are not available; only create/delete function.

## Environment

| Item | Value |
| --- | --- |
| Component | VECTR GraphQL API |
| Endpoint | `https://<vectr_host>/sra-purpletools-rest/graphql` |
| Auth | API Key v1.0 (`Authorization: VEC1 <key_id>:<key_secret>`) |
| Edition / Version | *(fill in — see "Version note" below)* |
| Environment (db) | e.g. `Developer_TST` |
| Client | Direct GraphQL `POST` (reproduced via cURL and a Python client) |

> **Version note:** `vectrAppConfig { version }` returns
> `Field 'version' in type 'VectrAppConfig' is undefined` on this build, so the
> version could not be read that way. Please substitute the build/version from
> your deployment (e.g. the Docker image tag or the version shown in the UI
> footer).

## Steps to Reproduce

All requests below use a valid API key with permission to read and modify the
target environment. Replace `<vectr_host>`, the `Authorization` value, the `db`
name, and the assessment `id` with values from your instance.

### 1. Confirm the assessment exists and is readable

```graphql
query {
  assessment(db: "Developer_TST", id: "58") {
    id
    name
  }
}
```

**Result:** succeeds, returning the assessment:

```json
{ "data": { "assessment": { "id": "58", "name": "Test_assessment" } } }
```

### 2. Attempt a minimal, schema-valid update

```graphql
mutation {
  assessment {
    update(input: { assessmentData: [{ id: "58", description: "desc only test" }] }) {
      assessments { id name }
    }
  }
}
```

**Result (the bug):**

```
Exception while fetching data (/assessment/update) : Invalid argument value(s)
```

The same error occurs for:
- updating `name` instead of `description`
- updating a different assessment (e.g. `id: "44"`)
- including `organizationIds` with a valid org ID
- including `attackLifeCycleId` set to the assessment's current, valid kill chain ID

### Equivalent cURL reproduction

```bash
curl -s -X POST "https://<vectr_host>/sra-purpletools-rest/graphql" \
  -H "Authorization: VEC1 <key_id>:<key_secret>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "mutation ($input: UpdateAssessmentInput!) { assessment { update(input: $input) { assessments { id name } } } }",
    "variables": { "input": { "assessmentData": [ { "id": "58", "description": "desc only test" } ] } }
  }'
```

## Expected Behavior

The assessment is updated and the mutation returns the updated assessment(s), e.g.:

```json
{ "data": { "assessment": { "update": { "assessments": [ { "id": "58", "name": "Test_assessment" } ] } } } }
```

## Actual Behavior

The mutation always returns:

```
Exception while fetching data (/assessment/update) : Invalid argument value(s)
```

## Evidence that the request matches the schema

The input was verified against the server's own introspection (not just the
public docs), confirming the payload is well-formed.

**`AssessmentMutations.update` takes a single `input: UpdateAssessmentInput!`:**

```graphql
query { __type(name: "AssessmentMutations") { fields { name args { name type { kind ofType { name } } } } } }
```
→ `update(input: UpdateAssessmentInput!)` — no other arguments.

**`UpdateAssessmentInput` has exactly one field, `assessmentData` (non-null list):**

```graphql
query { __type(name: "UpdateAssessmentInput") { inputFields { name type { kind ofType { name kind } } } } }
```
→ `assessmentData: [UpdateAssessmentDataInput!]!` — and **no** `db` field
(confirmed: sending `db` on the wrapper is rejected as
`contains a field not in 'UpdateAssessmentInput': 'db'`).

**`UpdateAssessmentDataInput` requires only `id: String!`; all other fields are optional:**

```graphql
query { __type(name: "UpdateAssessmentDataInput") { inputFields { name type { name kind ofType { name kind } } } } }
```
→ Fields: `id` (NON_NULL String), `name`, `description`, `organizationIds`,
`tagIds`, `attackLifeCycleId` — all optional except `id`.

The reproduction payload supplies a valid `id` (verified readable in step 1) and
one optional scalar. It violates no part of the introspected schema, yet the
resolver rejects it.

## Scope / Isolation

The following operations were tested against the **same instance, same API key,
same environment**, to isolate the defect:

| Operation | Result |
| --- | --- |
| `assessment(db, id)` (read) | Works |
| `assessment.create` | Works |
| `assessment.delete` | Works (returns `deletedIds`) |
| `assessment.update` | **Fails — `Invalid argument value(s)`** |
| `testCase.update` | Works |

Because reads, creates, and deletes all succeed — and because `testCase.update`
(another write resolver) succeeds — the issue is not authentication,
permissions, connectivity, or environment configuration. It is specific to the
`assessment.update` resolver.

### Contrast: `testCase.update` (works) vs `assessment.update` (fails)

A successful `testCase.update` against the same instance, for reference:

```graphql
mutation {
  testCase {
    update(input: {
      db: "Developer_TST",
      testCaseUpdates: [{ testCaseId: "5410", operatorGuidance: "write probe" }]
    }) {
      testCases { id name }
    }
  }
}
```
→ returns the updated test case successfully.

Note the structural difference that may be relevant to diagnosis: the working
`testCase.update` wrapper (`UpdateTestCaseInput`) carries a required `db` field
and a `testCaseUpdates` list, whereas `UpdateAssessmentInput` exposes **no** `db`
field at all. If the `assessment.update` resolver internally expects an
environment/`db` value that the input type does not expose, that would be
consistent with the observed "Invalid argument value(s)" failure.

## Additional observation (separate, minor)

`vectrAppConfig { version }` fails with
`Field 'version' in type 'VectrAppConfig' is undefined`. If `version` was removed
or renamed, the documentation and any clients selecting it will break. Flagging
in case it is unintended.

## Suggested Areas to Investigate

1. The `assessment.update` resolver's argument handling — specifically whether it
   requires an environment/`db` value that `UpdateAssessmentInput` does not
   expose (mirroring how `UpdateTestCaseInput` *does* expose `db`).
2. Whether `UpdateAssessmentInput`/`UpdateAssessmentDataInput` should include a
   `db` (or equivalent environment) field to match the resolver's expectations.
3. Replacing the generic `Invalid argument value(s)` message with a specific one
   that names the missing/invalid argument (as `testCase.update` does — it
   clearly reports `missing required fields '[db]'`).

## Reporter Notes

- Reproduced both via a Python GraphQL client and via direct cURL.
- Discovered while building an API integration; happy to provide further logs,
  exact request/response captures, or test against a patched build.
