# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 the vectr-cli contributors
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version. See the LICENSE file for details.
"""
vectr_client.py
===============
Secure Python client for the VECTR GraphQL API.

VECTR is a purple-team tracking platform by Security Risk Advisors. This module
wraps its single GraphQL endpoint with a small, typed, dependency-light client.

--------------------------------------------------------------------------------
API DOCUMENTATION REFERENCES
--------------------------------------------------------------------------------
  Overview & queries .......... https://docs.vectr.io/graphql/
  API key / authentication .... https://docs.vectr.io/API-Key/
  Test case mutations ......... https://docs.vectr.io/graphql/testcases/
  Campaign mutations .......... https://docs.vectr.io/graphql/campaigns/
  Assessment mutations ........ https://docs.vectr.io/graphql/assessments/
  Full schema (Query) ......... https://docs.vectr.io/graphql/schema/query.doc.html
  Full schema (Mutation) ...... https://docs.vectr.io/graphql/schema/mutation.doc.html

--------------------------------------------------------------------------------
ENDPOINT  (https://docs.vectr.io/graphql/#the-graphql-endpoint)
--------------------------------------------------------------------------------
  The GraphQL API has a single endpoint:
      https://<vectr_hostname>/sra-purpletools-rest/graphql
  All operations (queries and mutations) POST a JSON body to this URL.

--------------------------------------------------------------------------------
AUTHENTICATION  (https://docs.vectr.io/API-Key/#key-usage)
--------------------------------------------------------------------------------
  API key v1.0 uses an Authorization header in the form:
      Authorization: VEC1 <key_id>:<key_secret>
  The key inherits the permissions of the user that created it.

--------------------------------------------------------------------------------
CONFIGURATION (environment variables — no secrets in source)
--------------------------------------------------------------------------------
  VECTR_BASE_URL        e.g. https://vectr.example.com         (SaaS / port 443)
                        or   https://vectr.internal:8081       (self-hosted)
  VECTR_API_KEY_ID      the key ID   (text before the ':')
  VECTR_API_KEY_SECRET  the key secret (text after the ':')
  VECTR_VERIFY_SSL      "false" to disable TLS verification (self-signed dev certs)

--------------------------------------------------------------------------------
SECURITY NOTES
--------------------------------------------------------------------------------
  * Credentials come from the environment (or explicit constructor args), never
    hard-coded.
  * TLS verification is ON unless VECTR_VERIFY_SSL=false (or verify_ssl=False).
  * Every caller value is sent as a GraphQL *variable* (structured JSON), never
    string-interpolated into the query text — this prevents query injection.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter, Retry

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================

class VECTRClientError(Exception):
    """Base exception for all VECTR client errors (config, network, transport)."""


class VECTRAuthError(VECTRClientError):
    """Raised when authentication fails (HTTP 401/403) or credentials are missing."""


class VECTRRequestError(VECTRClientError):
    """Raised when the server returns an unexpected (non-2xx) HTTP status code."""


class VECTRGraphQLError(VECTRClientError):
    """Raised when a 200 response body contains a non-empty GraphQL 'errors' array."""


# =============================================================================
# Helpers
# =============================================================================

def _env_verify_ssl(default: bool = True) -> bool:
    """Interpret the VECTR_VERIFY_SSL env var as a boolean (default True)."""
    raw = os.environ.get("VECTR_VERIFY_SSL")
    if raw is None:
        return default
    return raw.strip().lower() not in ("false", "0", "no", "off")


# =============================================================================
# Client
# =============================================================================

class VECTRClient:
    """
    Thin, secure wrapper around the VECTR GraphQL endpoint.

    Typical use:
        from vectr_client import VECTRClient
        with VECTRClient() as client:           # reads env vars
            for db in client.list_databases():
                print(db["name"])

    Constructor parameters (all optional; fall back to environment variables):
        base_url        Base URL of the VECTR instance.
        api_key_id      API key ID.
        api_key_secret  API key secret.
        verify_ssl      TLS verification toggle (None -> read VECTR_VERIFY_SSL).
        timeout         Per-request timeout in seconds (default 30).
        max_retries     Retries on transient 5xx / connection errors (default 3).
    """

    # Single fixed path for every operation.
    # Ref: https://docs.vectr.io/graphql/#the-graphql-endpoint
    _GRAPHQL_PATH = "/sra-purpletools-rest/graphql"

    def __init__(
        self,
        base_url: str | None = None,
        api_key_id: str | None = None,
        api_key_secret: str | None = None,
        verify_ssl: bool | None = None,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._base_url = (base_url or os.environ.get("VECTR_BASE_URL", "")).rstrip("/")
        self._api_key_id = api_key_id or os.environ.get("VECTR_API_KEY_ID", "")
        self._api_key_secret = api_key_secret or os.environ.get("VECTR_API_KEY_SECRET", "")

        if verify_ssl is None:
            verify_ssl = _env_verify_ssl(default=True)

        if not self._base_url:
            raise VECTRClientError(
                "VECTR base URL is required. Pass base_url= or set VECTR_BASE_URL."
            )
        if not self._api_key_id or not self._api_key_secret:
            raise VECTRAuthError(
                "VECTR API key credentials are required. Pass api_key_id/api_key_secret "
                "or set VECTR_API_KEY_ID / VECTR_API_KEY_SECRET. "
                "See https://docs.vectr.io/API-Key/"
            )

        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._endpoint = f"{self._base_url}{self._GRAPHQL_PATH}"

        if not verify_ssl:
            # Silence the InsecureRequestWarning when the user has explicitly
            # opted out of verification (e.g. self-signed dev cert).
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:  # pragma: no cover
                pass

        # Reusable session with retry/backoff on transient server errors.
        self._session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST", "GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

        # The auth header (set once) follows the VEC1 scheme from the docs.
        # Ref: https://docs.vectr.io/API-Key/#key-version-10
        self._session.headers.update(
            {
                "Authorization": f"VEC1 {self._api_key_id}:{self._api_key_secret}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # -------------------------------------------------------------------------
    # Low-level transport
    # -------------------------------------------------------------------------

    def _execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        """
        POST a GraphQL document and return its 'data' object.

        GraphQL operations are sent as a JSON body containing 'query' and
        (optionally) 'variables' / 'operationName'.
        Ref: https://docs.vectr.io/graphql/#communicating-with-graphql

        Raises VECTRAuthError, VECTRRequestError, or VECTRGraphQLError.
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        if operation_name:
            payload["operationName"] = operation_name

        logger.debug("POST %s op=%s", self._endpoint, operation_name or "<anonymous>")

        try:
            response = self._session.post(
                self._endpoint,
                json=payload,
                verify=self._verify_ssl,
                timeout=self._timeout,
            )
        except requests.exceptions.SSLError as exc:
            raise VECTRClientError(
                "TLS/SSL error — the server certificate could not be verified. "
                "For a self-signed dev instance set VECTR_VERIFY_SSL=false."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise VECTRClientError(f"Connection error: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise VECTRClientError(f"Request timed out after {self._timeout}s.") from exc

        if response.status_code in (401, 403):
            raise VECTRAuthError(
                f"Authentication failed (HTTP {response.status_code}). "
                "Check your API key credentials."
            )
        if not response.ok:
            raise VECTRRequestError(
                f"Unexpected HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise VECTRClientError(
                f"Response was not valid JSON: {response.text[:300]}"
            ) from exc

        # GraphQL returns HTTP 200 even for query-level errors; check the body.
        if "errors" in body and body["errors"]:
            messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
            raise VECTRGraphQLError(f"GraphQL error(s): {messages}")

        return body.get("data", {})

    def raw_query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute any arbitrary GraphQL query or mutation.

        Useful for schema introspection or operations not yet wrapped by a typed
        helper. The full schema is documented at:
          https://docs.vectr.io/graphql/schema/query.doc.html
          https://docs.vectr.io/graphql/schema/mutation.doc.html
        """
        return self._execute(query, variables, operation_name)

    # -------------------------------------------------------------------------
    # Generic pagination helper
    # -------------------------------------------------------------------------

    def _paginate(
        self,
        query: str,
        root_field: str,
        variables: dict[str, Any],
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Walk a Relay-style connection and return every node.

        VECTR list queries follow the cursor pattern: they accept `first` and
        `after`, and return `nodes { ... }` plus
        `pageInfo { endCursor hasNextPage }`.
        Ref: https://docs.vectr.io/graphql/#query-with-pagination

        `query` must select that shape under `root_field`.
        """
        all_nodes: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            vars_ = dict(variables)
            vars_["first"] = page_size
            if cursor:
                vars_["after"] = cursor
            result = self._execute(query, vars_)
            conn = result.get(root_field, {}) or {}
            all_nodes.extend(conn.get("nodes", []) or [])
            page_info = conn.get("pageInfo", {}) or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
        return all_nodes

    # =========================================================================
    # DATABASES (environments)
    # Schema: Query.databases -> [Database]
    # Ref: https://docs.vectr.io/graphql/schema/query.doc.html
    # =========================================================================

    def list_databases(self) -> list[dict[str, Any]]:
        """Return all databases (environments) visible to the API key."""
        query = "query { databases { id name } }"
        return self._execute(query).get("databases") or []

    # =========================================================================
    # ASSESSMENTS
    # Mutations doc: https://docs.vectr.io/graphql/assessments/
    # Schema: Query.assessment(s), Mutation.assessment.{create,update,delete}
    # =========================================================================

    def get_assessment(self, db: str, assessment_id: str) -> dict[str, Any]:
        """Fetch a single assessment by ID. Schema: Query.assessment."""
        query = """
        query GetAssessment($db: String, $id: String!) {
            assessment(db: $db, id: $id) {
                id name description createTime updateTime
            }
        }
        """
        return self._execute(query, {"db": db, "id": assessment_id})

    def list_assessments(self, db: str, page_size: int = 50) -> list[dict[str, Any]]:
        """List all assessments in a database. Schema: Query.assessments."""
        query = """
        query ListAssessments($db: String!, $first: Int, $after: String) {
            assessments(db: $db, first: $first, after: $after) {
                nodes { id name description createTime updateTime }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "assessments", {"db": db}, page_size)

    def list_library_assessments(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List library (template) assessments. Schema: Query.libraryAssessments."""
        query = """
        query LibAssessments($first: Int, $after: String) {
            libraryAssessments(first: $first, after: $after) {
                nodes { id name description createTime }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "libraryAssessments", {}, page_size)

    def create_assessment(
        self, db: str, assessment_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Create one or more assessments in `db`.

        Each item conforms to CreateAssessmentDataInput (name, description,
        organizationIds, optional killChainId).
        Ref: https://docs.vectr.io/graphql/assessments/#create-a-local-assessment
        """
        query = """
        mutation CreateAssessment($input: CreateAssessmentInput!) {
            assessment {
                create(input: $input) {
                    assessments { id name description createTime }
                }
            }
        }
        """
        return self._execute(
            query, {"input": {"db": db, "assessmentData": assessment_data}},
            operation_name="CreateAssessment",
        )

    def update_assessment(self, assessment_data: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Update one or more assessments.

        UpdateAssessmentInput has a single field, `assessmentData`, a list of
        UpdateAssessmentDataInput. There is NO `db` field/argument and NO
        separate assessmentId — each data item carries its own required `id`,
        plus optional name, description, organizationIds, tagIds,
        attackLifeCycleId. This payload matches the schema at every level
        (verified by live introspection of UpdateAssessmentInput and
        UpdateAssessmentDataInput).

        KNOWN SERVER-SIDE LIMITATION: on at least some VECTR builds the
        `assessment.update` resolver rejects every schema-valid update with
        'Exception while fetching data (/assessment/update) : Invalid argument
        value(s)', even though create/delete and testCase.update all work. The
        CLI catches this and shows a clear message rather than a raw error.
        Refs:
          https://docs.vectr.io/graphql/schema/updateassessmentinput.doc.html
          https://docs.vectr.io/graphql/schema/updateassessmentdatainput.doc.html
          https://docs.vectr.io/graphql/schema/assessmentmutations.doc.html
        """
        query = """
        mutation UpdateAssessment($input: UpdateAssessmentInput!) {
            assessment {
                update(input: $input) {
                    assessments { id name description updateTime }
                }
            }
        }
        """
        return self._execute(
            query,
            {"input": {"assessmentData": assessment_data}},
            operation_name="UpdateAssessment",
        )

    def delete_assessment(self, db: str, assessment_ids: list[str]) -> dict[str, Any]:
        """
        Delete one or more assessments.

        Schema: Mutation.assessment.delete -> DeleteAssessmentPayload,
        whose only field is `deletedIds: [String!]`.
        Ref: https://docs.vectr.io/graphql/schema/deleteassessmentpayload.doc.html
        """
        query = """
        mutation DeleteAssessment($input: DeleteAssessmentInput!) {
            assessment {
                delete(input: $input) { deletedIds }
            }
        }
        """
        return self._execute(
            query, {"input": {"db": db, "ids": assessment_ids}},
            operation_name="DeleteAssessment",
        )

    # =========================================================================
    # CAMPAIGNS
    # Mutations doc: https://docs.vectr.io/graphql/campaigns/
    # =========================================================================

    def get_campaigns_by_ids(self, db: str, ids: list[str]) -> dict[str, Any]:
        """
        Fetch campaigns (with their test cases) by ID.
        Schema: Query.campaignsByIds.
        Ref: https://docs.vectr.io/graphql/#query-with-variables
        """
        query = """
        query CampaignsByIds($db: String!, $ids: [String]!) {
            campaignsByIds(db: $db, ids: $ids) {
                nodes {
                    id name description
                    testCases { id name mitreId outcomes createTime updateTime }
                }
            }
        }
        """
        return self._execute(query, {"db": db, "ids": ids}, operation_name="CampaignsByIds")

    def list_campaigns(self, db: str, page_size: int = 50) -> list[dict[str, Any]]:
        """List all campaigns in a database. Schema: Query.campaigns."""
        query = """
        query ListCampaigns($db: String!, $first: Int, $after: String) {
            campaigns(db: $db, first: $first, after: $after) {
                nodes { id name description createTime updateTime }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "campaigns", {"db": db}, page_size)

    def list_library_campaigns(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List library (template) campaigns. Schema: Query.libraryCampaigns."""
        query = """
        query LibCampaigns($first: Int, $after: String) {
            libraryCampaigns(first: $first, after: $after) {
                nodes { id name description createTime }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "libraryCampaigns", {}, page_size)

    def create_campaign(
        self, db: str, assessment_id: str, campaign_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Create one or more campaigns inside an assessment.

        Each item conforms to CreateCampaignDataInput (name, description,
        organizationIds).
        Ref: https://docs.vectr.io/graphql/campaigns/#create-a-local-campaign
        """
        query = """
        mutation CreateCampaign($input: CreateCampaignInput!) {
            campaign {
                create(input: $input) {
                    campaigns { id name description createTime }
                }
            }
        }
        """
        return self._execute(
            query,
            {"input": {"db": db, "assessmentId": assessment_id, "campaignData": campaign_data}},
            operation_name="CreateCampaign",
        )

    def create_campaign_template(
        self, campaign_data: list[dict[str, Any]], overwrite: bool = False
    ) -> dict[str, Any]:
        """
        Create campaign template(s) in the library.

        Each item conforms to CreateCampaignTemplateDataInput (name, description,
        organizationIds, optional testCaseTemplateIds).
        Ref: https://docs.vectr.io/graphql/campaigns/#create-campaign-template
        """
        query = """
        mutation CreateCampaignTemplate($input: CreateCampaignTemplateInput!) {
            campaign {
                createTemplate(input: $input) {
                    campaigns { id name description createTime }
                }
            }
        }
        """
        return self._execute(
            query, {"input": {"overwrite": overwrite, "campaignTemplateData": campaign_data}},
            operation_name="CreateCampaignTemplate",
        )

    def delete_campaign(self, db: str, campaign_ids: list[str]) -> dict[str, Any]:
        """
        Delete one or more campaigns.

        Schema: Mutation.campaign.delete -> DeleteCampaignPayload, whose only
        field is `deletedIds: [String!]`.
        Ref: https://docs.vectr.io/graphql/schema/deletecampaignpayload.doc.html
        """
        query = """
        mutation DeleteCampaign($input: DeleteCampaignInput!) {
            campaign {
                delete(input: $input) { deletedIds }
            }
        }
        """
        return self._execute(
            query, {"input": {"db": db, "ids": campaign_ids}},
            operation_name="DeleteCampaign",
        )

    # =========================================================================
    # TEST CASES
    # Mutations doc: https://docs.vectr.io/graphql/testcases/
    # =========================================================================

    def get_testcase(self, db: str, testcase_id: str) -> dict[str, Any]:
        """Fetch a single test case by ID. Schema: Query.testcase."""
        query = """
        query GetTestCase($db: String!, $id: String!) {
            testcase(db: $db, id: $id) {
                id name description
                mitreId techniqueName
                phase { id name }
                outcome { id name }
                createTime updateTime
            }
        }
        """
        return self._execute(query, {"db": db, "id": testcase_id})

    def list_testcases(
        self, db: str, first: int = 50, after: str | None = None
    ) -> dict[str, Any]:
        """
        Return a single page of test cases (raw connection).
        Schema: Query.testcases.
        Ref: https://docs.vectr.io/graphql/#query-with-pagination
        """
        query = """
        query ListTestCases($db: String!, $first: Int, $after: String) {
            testcases(db: $db, first: $first, after: $after) {
                nodes {
                    id name description
                    mitreId techniqueName
                    phase { id name }
                    outcome { id name }
                    createTime updateTime
                }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        variables: dict[str, Any] = {"db": db, "first": first}
        if after:
            variables["after"] = after
        return self._execute(query, variables)

    def get_all_testcases(self, db: str, page_size: int = 50) -> list[dict[str, Any]]:
        """Collect every test case in a database, paginating automatically."""
        query = """
        query AllTestCases($db: String!, $first: Int, $after: String) {
            testcases(db: $db, first: $first, after: $after) {
                nodes {
                    id name description
                    mitreId techniqueName
                    phase { id name }
                    outcome { id name }
                    createTime updateTime
                }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "testcases", {"db": db}, page_size)

    def list_library_testcases(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List library (template) test cases. Schema: Query.libraryTestcases."""
        query = """
        query LibTestCases($first: Int, $after: String) {
            libraryTestcases(first: $first, after: $after) {
                nodes { id name description mitreId techniqueName phase { id name } createTime }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "libraryTestcases", {}, page_size)

    def create_testcase_template(
        self, template_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Create test case template(s) in the library.

        Each item conforms to CreateTestCaseTemplateDataInput. Required fields:
        name, description, phase, technique, organization.
        Ref: https://docs.vectr.io/graphql/testcases/#create-test-case-template
        """
        query = """
        mutation CreateTestCaseTemplate($input: CreateTestCaseTemplateInput!) {
            testCase {
                createTemplate(input: $input) {
                    testCases { id name }
                }
            }
        }
        """
        return self._execute(
            query, {"input": {"testCaseTemplateData": template_data}},
            operation_name="CreateTestCaseTemplate",
        )

    def clone_testcase_template(
        self, library_testcase_id: str, clone_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Clone an existing test case template.

        clone_data items conform to UpdateTestCaseTemplateDataInput; a missing
        field (e.g. description) is inherited from the source template.
        Ref: https://docs.vectr.io/graphql/testcases/#clone-an-existing-test-case-template
        """
        query = """
        mutation CloneTestCaseTemplate($input: CloneTestCaseTemplateInput!) {
            testCase {
                cloneTemplate(input: $input) {
                    testCases { id name description }
                }
            }
        }
        """
        return self._execute(
            query,
            {"input": {"libraryTestCaseId": library_testcase_id,
                       "testCaseTemplateData": clone_data}},
            operation_name="CloneTestCaseTemplate",
        )

    def create_testcase_with_template(
        self, db: str, campaign_id: str, create_inputs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Create local test case(s) and map each to a template by name
        (the recommended approach for purple-team exercises).

        Inputs conform to CreateTestCaseAndTemplateMatchByNameInput; each entry
        has `testCaseData` and an optional `templateName` (defaults to the test
        case name). A matching template is created if none exists.
        Ref: https://docs.vectr.io/graphql/testcases/#create-test-case-and-match-template-by-name
        """
        query = """
        mutation CreateTCWithTemplate($input: CreateTestCaseAndTemplateMatchByNameInput!) {
            testCase {
                createWithTemplateMatchByName(input: $input) {
                    testCases { id name description createTime }
                }
            }
        }
        """
        return self._execute(
            query,
            {"input": {"db": db, "campaignId": campaign_id,
                       "createTestCaseInputs": create_inputs}},
            operation_name="CreateTCWithTemplate",
        )

    def create_testcase_without_template(
        self, db: str, campaign_id: str, testcase_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Create local test case(s) with NO template mapping.

        The VECTR docs advise against this unless you know what you're doing,
        because un-templated test cases are weaker for reporting.
        Ref: https://docs.vectr.io/graphql/testcases/#create-test-case-without-template
        """
        query = """
        mutation CreateTCWithoutTemplate($input: CreateTestCaseWithoutTemplateInput!) {
            testCase {
                createWithoutTemplate(input: $input) {
                    testCases { id name description createTime }
                }
            }
        }
        """
        return self._execute(
            query,
            {"input": {"db": db, "campaignId": campaign_id, "testCaseData": testcase_data}},
            operation_name="CreateTCWithoutTemplate",
        )

    def update_testcase(self, db: str, testcase_updates: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Update one or more test cases in an environment.

        Verified against a live instance: UpdateTestCaseInput has `db`
        (required, on the wrapper) plus `testCaseUpdates`, a list of
        UpdateTestCaseDataInput. Each item is keyed by `testCaseId` (required)
        and may set operatorGuidance, description, outcome, currentStatus,
        addTagsByName / removeTagsByName, attack times, etc.
        Refs:
          https://docs.vectr.io/graphql/schema/updatetestcaseinput.doc.html
          https://docs.vectr.io/graphql/schema/updatetestcasedatainput.doc.html
        Note: the list field is `testCaseUpdates` (not `testCaseData`), and the
        per-item id field is `testCaseId` (not `id`) — both confirmed by live
        schema introspection.
        """
        query = """
        mutation UpdateTestCase($input: UpdateTestCaseInput!) {
            testCase {
                update(input: $input) {
                    testCases { id name description }
                }
            }
        }
        """
        return self._execute(
            query,
            {"input": {"db": db, "testCaseUpdates": testcase_updates}},
            operation_name="UpdateTestCase",
        )

    def delete_testcase(self, db: str, testcase_ids: list[str]) -> dict[str, Any]:
        """
        Delete one or more test cases.

        Schema: Mutation.testCase.delete -> DeleteTestCasePayload, which (like
        the assessment/campaign delete payloads) returns `deletedIds: [String!]`.
        Ref: https://docs.vectr.io/graphql/schema/deletetestcasepayload.doc.html
        """
        query = """
        mutation DeleteTestCase($input: DeleteTestCaseInput!) {
            testCase {
                delete(input: $input) { deletedIds }
            }
        }
        """
        return self._execute(
            query, {"input": {"db": db, "ids": testcase_ids}},
            operation_name="DeleteTestCase",
        )

    # =========================================================================
    # REFERENCE DATA (read-only): organizations, phases, kill chains,
    # outcomes, tag types, tags.
    # Schema: https://docs.vectr.io/graphql/schema/query.doc.html
    # =========================================================================

    def list_organizations(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List organizations. Schema: Query.organizations."""
        query = """
        query Orgs($first: Int, $after: String) {
            organizations(first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "organizations", {}, page_size)

    def list_phases(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List kill-chain phases. Schema: Query.phases."""
        query = """
        query Phases($first: Int, $after: String) {
            phases(first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "phases", {}, page_size)

    def list_killchains(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List kill chains. Schema: Query.killchains."""
        query = """
        query KillChains($first: Int, $after: String) {
            killchains(first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "killchains", {}, page_size)

    def list_outcomes(self) -> list[dict[str, Any]]:
        """List all outcomes (flat). Schema: Query.outcomes -> [Outcome]."""
        query = "query { outcomes { id name } }"
        return self._execute(query).get("outcomes") or []

    def list_tag_types(self) -> list[dict[str, Any]]:
        """List tag types. Schema: Query.tagTypes -> [TagType]."""
        query = "query { tagTypes { id name } }"
        return self._execute(query).get("tagTypes") or []

    def list_tags(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List tags. Schema: Query.tags."""
        query = """
        query Tags($first: Int, $after: String) {
            tags(first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "tags", {}, page_size)

    # =========================================================================
    # ENVIRONMENT TOOLS (read-only): red/blue tools, defense layers, vendors,
    # defense tool products.
    # =========================================================================

    def list_redtools(self, db: str, page_size: int = 50) -> list[dict[str, Any]]:
        """List red tools in a database. Schema: Query.redtools."""
        query = """
        query RedTools($db: String!, $first: Int, $after: String) {
            redtools(db: $db, first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "redtools", {"db": db}, page_size)

    def list_bluetools(self, db: str, page_size: int = 50) -> list[dict[str, Any]]:
        """List blue/defense tools in a database. Schema: Query.bluetools."""
        query = """
        query BlueTools($db: String!, $first: Int, $after: String) {
            bluetools(db: $db, first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "bluetools", {"db": db}, page_size)

    def list_defense_layers(self, db: str, page_size: int = 50) -> list[dict[str, Any]]:
        """List defensive layers in a database. Schema: Query.defensivelayers."""
        query = """
        query DefLayers($db: String!, $first: Int, $after: String) {
            defensivelayers(db: $db, first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "defensivelayers", {"db": db}, page_size)

    def list_vendors(self, db: str, page_size: int = 50) -> list[dict[str, Any]]:
        """List vendors in a database. Schema: Query.vendors."""
        query = """
        query Vendors($db: String!, $first: Int, $after: String) {
            vendors(db: $db, first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "vendors", {"db": db}, page_size)

    def list_defense_tool_products(self, page_size: int = 50) -> list[dict[str, Any]]:
        """List defense tool products (global). Schema: Query.defenseToolProducts."""
        query = """
        query DTP($first: Int, $after: String) {
            defenseToolProducts(first: $first, after: $after) {
                nodes { id name }
                pageInfo { endCursor hasNextPage }
            }
        }
        """
        return self._paginate(query, "defenseToolProducts", {}, page_size)

    # =========================================================================
    # APP / SERVER CONFIG (read-only). Schema: Query.vectrAppConfig.
    # =========================================================================

    def get_app_config(self) -> dict[str, Any]:
        """
        Return VECTR application config.

        The fields of VectrAppConfig vary by build (e.g. `version` does not
        exist on every instance). Rather than guess, this introspects the
        type's leaf fields first, then queries exactly those.
        """
        introspect = """
        query {
            __type(name: "VectrAppConfig") {
                fields { name type { kind ofType { kind } } }
            }
        }
        """
        meta = self._execute(introspect)
        fields = (meta.get("__type") or {}).get("fields") or []

        leaf_kinds = {"SCALAR", "ENUM"}
        selectable: list[str] = []
        for f in fields:
            t = f.get("type") or {}
            kind = t.get("kind")
            of = (t.get("ofType") or {}).get("kind")
            if kind in leaf_kinds or (kind == "NON_NULL" and of in leaf_kinds):
                selectable.append(f["name"])

        if not selectable:
            return self._execute("query { vectrAppConfig { __typename } }")

        selection = " ".join(selectable)
        return self._execute(f"query {{ vectrAppConfig {{ {selection} }} }}")

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "VECTRClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
