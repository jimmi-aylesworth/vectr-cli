#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 the vectr-cli contributors
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version. See the LICENSE file for details.
"""
vectr.py
========
Full-featured interactive CLI for the VECTR GraphQL API, built on vectr_client.

Run:
    python3 vectr.py

--------------------------------------------------------------------------------
API DOCUMENTATION REFERENCES
--------------------------------------------------------------------------------
  Overview & queries .......... https://docs.vectr.io/graphql/
  API key / authentication .... https://docs.vectr.io/API-Key/
  Test case mutations ......... https://docs.vectr.io/graphql/testcases/
  Campaign mutations .......... https://docs.vectr.io/graphql/campaigns/
  Assessment mutations ........ https://docs.vectr.io/graphql/assessments/
  Schema (Query) .............. https://docs.vectr.io/graphql/schema/query.doc.html
  Schema (Mutation) ........... https://docs.vectr.io/graphql/schema/mutation.doc.html

--------------------------------------------------------------------------------
CONFIGURATION (environment variables — no secrets in source)
--------------------------------------------------------------------------------
  VECTR_BASE_URL        e.g. https://your-vectr-host  (add :8081 for self-hosted)
  VECTR_API_KEY_ID      API key ID
  VECTR_API_KEY_SECRET  API key secret
  VECTR_VERIFY_SSL      "false" only for self-signed dev certs

--------------------------------------------------------------------------------
DESIGN
--------------------------------------------------------------------------------
  * Every selection uses the same numbered-menu style as the initial database
    picker (see `menu()` / `pick_record()`).
  * EVERY operation that creates, updates, or deletes data shows a summary and a
    secondary 'y' confirmation BEFORE the change is sent (see `confirm()`).
    Read-only operations never prompt for confirmation.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable

from vectr_client import (
    VECTRClient,
    VECTRAuthError,
    VECTRGraphQLError,
    VECTRRequestError,
    VECTRClientError,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")

DIVIDER = "=" * 64
SUBDIV = "-" * 64


# ===========================================================================
# Reusable UI primitives  (all menus look like the database picker)
# ===========================================================================

def banner(title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def menu(title: str, options: list[str], allow_back: bool = True) -> int:
    """
    Display a numbered menu in the same style as the database picker.
    Returns the zero-based index of the chosen option, or -1 for 'back'.
    """
    banner(title)
    for idx, label in enumerate(options, start=1):
        print(f"  [{idx}]  {label}")
    if allow_back:
        print(f"  [0]  Back / Cancel")
    print()

    lo = 0 if allow_back else 1
    while True:
        raw = input(f"  Select [{lo}-{len(options)}]: ").strip()
        if raw.isdigit():
            choice = int(raw)
            if choice == 0 and allow_back:
                return -1
            if 1 <= choice <= len(options):
                return choice - 1
        print(f"  Please enter a number between {lo} and {len(options)}.")


def pick_record(title: str, records: list[dict], label_fn: Callable[[dict], str]) -> dict | None:
    """
    Render a list of records (dicts) as a numbered menu and return the chosen
    one, or None if the user goes back / the list is empty.
    """
    if not records:
        banner(title)
        print("  (no records found)")
        return None
    labels = [label_fn(r) for r in records]
    idx = menu(title, labels)
    if idx == -1:
        return None
    return records[idx]


def prompt(label: str, default: str | None = None, required: bool = False) -> str:
    """Prompt for a single line of text input."""
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"  {label}{suffix}: ").strip()
        if not val and default is not None:
            return default
        if val or not required:
            return val
        print("  This field is required.")


def prompt_list(label: str) -> list[str]:
    """Prompt for a comma-separated list; returns [] if blank."""
    raw = input(f"  {label} (comma-separated, blank = none): ").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def confirm(action: str, details: dict[str, Any] | None = None) -> bool:
    """
    Secondary confirmation gate. MUST be called before any create/update/delete.
    Shows exactly what will happen and requires an explicit 'y'.
    """
    print(f"\n{SUBDIV}")
    print("  CONFIRM ACTION")
    print(SUBDIV)
    print(f"  {action}")
    if details:
        print()
        for key, value in details.items():
            print(f"    {key}: {value}")
    print(SUBDIV)
    answer = input("  Proceed? Type 'y' to confirm, anything else to cancel: ").strip().lower()
    if answer == "y":
        return True
    print("  Cancelled — no changes were made.")
    return False


def pause() -> None:
    input("\n  Press Enter to continue...")


# ===========================================================================
# Application
# ===========================================================================

class VectrApp:
    def __init__(self, client: VECTRClient) -> None:
        self.client = client
        self.db: str | None = None          # currently selected environment name

    # ---- session bootstrap -------------------------------------------------

    def choose_database(self) -> bool:
        print("\nConnecting to VECTR and fetching databases...")
        dbs = self.client.list_databases()
        chosen = pick_record(
            "Select a Database (Environment)",
            dbs,
            lambda d: f"{d['name']}  (id: {d['id']})",
        )
        if not chosen:
            return False
        self.db = chosen["name"]
        print(f"\n  Active database: {self.db}")
        return True

    # ---- top-level loop ----------------------------------------------------

    def run(self) -> None:
        print("\nVECTR API — Full CLI")
        print("====================")
        if not self.choose_database():
            print("\n  No database selected. Exiting.")
            return

        top_level = [
            "Assessments",
            "Campaigns",
            "Test Cases",
            "Reference Data (orgs, phases, kill chains, outcomes, tags)",
            "Environment Tools (red/blue tools, defense layers, vendors)",
            "Library (templates)",
            "App / Server Info",
            "Switch Database",
            "Run Raw GraphQL",
        ]
        handlers = [
            self.menu_assessments,
            self.menu_campaigns,
            self.menu_testcases,
            self.menu_reference_data,
            self.menu_env_tools,
            self.menu_library,
            self.menu_app_info,
            self._switch_db,
            self.menu_raw,
        ]

        while True:
            idx = menu(f"Main Menu   (db: {self.db})", top_level, allow_back=True)
            if idx == -1:
                print("\n  Goodbye.")
                return
            try:
                handlers[idx]()
            except (VECTRGraphQLError, VECTRRequestError, VECTRClientError) as exc:
                print(f"\n  [ERROR] {exc}")
                pause()

    def _switch_db(self) -> None:
        self.choose_database()

    # ------------------------------------------------------------------
    # Shared pickers for reference data (used by create/update flows)
    # ------------------------------------------------------------------

    def _pick_optional(self, title: str, records: list[dict]) -> dict | None:
        """
        Show a numbered menu of reference records with an explicit
        '(leave unset)' option. Returns the chosen record, or None to leave
        the field unset. Returns None immediately if the list is empty.
        """
        if not records:
            return None
        labels = [f"{r.get('name')}  (id: {r.get('id')})" for r in records]
        labels.append("(leave unset)")
        idx = menu(title, labels, allow_back=False)
        if idx == len(records):       # the "(leave unset)" entry
            return None
        return records[idx]

    def _pick_organizations(self) -> list[str]:
        """
        Let the user select zero or more organizations from the live list.
        Returns a list of organization IDs (possibly empty).

        UX: option [0] always finishes selection. Once at least one org has
        been chosen, pressing Enter (blank input) also finishes. Each chosen
        org is removed from the subsequent list so it can't be picked twice.
        """
        orgs = self.client.list_organizations()
        if not orgs:
            return []
        chosen: list[dict] = []
        remaining = list(orgs)

        while remaining:
            title = "Add an Organization"
            if chosen:
                title += f"   (selected: {', '.join(o['name'] for o in chosen)})"
            banner(title)
            for i, o in enumerate(remaining, start=1):
                print(f"  [{i}]  {o.get('name')}  (id: {o.get('id')})")
            # [0] always finishes; Enter finishes once something is selected.
            done_hint = "Enter = done" if chosen else "0 = none / skip"
            print(f"  [0]  Done selecting  ({done_hint})")
            print()

            raw = input(f"  Add which? [0-{len(remaining)}]: ").strip()
            if raw == "" and chosen:        # blank Enter finishes after >=1 pick
                break
            if raw == "0":                  # explicit finish / skip
                break
            if raw.isdigit():
                c = int(raw)
                if 1 <= c <= len(remaining):
                    chosen.append(remaining.pop(c - 1))
                    continue
            print(f"  Please enter a number between 0 and {len(remaining)}"
                  + (" (or press Enter to finish)." if chosen else "."))

        return [o["id"] for o in chosen]

    # ==================================================================
    # ASSESSMENTS
    # ==================================================================

    def menu_assessments(self) -> None:
        options = [
            "List assessments",
            "View assessment details",
            "Create assessment",
            "Update assessment",
            "Delete assessment",
        ]
        while True:
            idx = menu(f"Assessments   (db: {self.db})", options)
            if idx == -1:
                return
            if idx == 0:
                self._list_assessments()
            elif idx == 1:
                self._view_assessment()
            elif idx == 2:
                self._create_assessment()
            elif idx == 3:
                self._update_assessment()
            elif idx == 4:
                self._delete_assessment()

    def _list_assessments(self) -> None:
        rows = self.client.list_assessments(self.db)
        banner(f"Assessments  (db: {self.db})")
        if not rows:
            print("  (none)")
        for a in rows:
            print(f"  [{a['id']:>4}]  {a['name']}")
        pause()

    def _view_assessment(self) -> None:
        rows = self.client.list_assessments(self.db)
        chosen = pick_record("Select an Assessment", rows, lambda a: f"[{a['id']}] {a['name']}")
        if not chosen:
            return
        full = self.client.get_assessment(self.db, chosen["id"]).get("assessment", {})
        banner(f"Assessment {chosen['id']}")
        for k, v in (full or {}).items():
            print(f"  {k}: {v}")
        pause()

    def _create_assessment(self) -> None:
        banner("Create Assessment")
        name = prompt("Name", required=True)
        description = prompt("Description")

        # Pick organizations and a kill chain from live menus rather than
        # typing raw IDs (typing invalid IDs causes "Invalid argument value(s)").
        org_ids = self._pick_organizations()
        killchains = self.client.list_killchains()
        kc = self._pick_optional("Select a Kill Chain", killchains)

        # CreateAssessmentDataInput requires only `name`. Include other fields
        # only when set, so empty values are never sent.
        # Ref: https://docs.vectr.io/graphql/schema/createassessmentdatainput.doc.html
        data: dict[str, Any] = {"name": name}
        if description:
            data["description"] = description
        if org_ids:
            data["organizationIds"] = org_ids
        if kc:
            data["killChainId"] = kc["id"]

        if not confirm(
            "Create a new assessment in this environment.",
            {"db": self.db, "name": name,
             "description": description or "(none)",
             "organizations": ", ".join(org_ids) if org_ids else "(none)",
             "killChain": kc["name"] if kc else "(default)"},
        ):
            return
        result = self.client.create_assessment(self.db, [data])
        created = result.get("assessment", {}).get("create", {}).get("assessments", [])
        print("\n  Created:")
        for a in created:
            print(f"    [{a['id']}] {a['name']}")
        pause()

    def _update_assessment(self) -> None:
        rows = self.client.list_assessments(self.db)
        chosen = pick_record("Select an Assessment to Update", rows, lambda a: f"[{a['id']}] {a['name']}")
        if not chosen:
            return
        banner(f"Update Assessment {chosen['id']}")
        new_name = prompt("New name", default=chosen["name"])
        new_desc = prompt("New description", default=chosen.get("description") or "")
        # The assessment id travels inside the data item (UpdateAssessmentDataInput).
        data = {"id": str(chosen["id"]), "name": new_name, "description": new_desc}

        if not confirm(
            f"Update assessment {chosen['id']}.",
            {"db": self.db, "id": chosen["id"],
             "name": f"{chosen['name']} -> {new_name}",
             "description": f"{chosen.get('description') or '(empty)'} -> {new_desc}"},
        ):
            return
        try:
            self.client.update_assessment([data])
            print("\n  Updated.")
        except VECTRGraphQLError as exc:
            # Some VECTR builds reject every schema-valid assessment update at
            # the resolver with "Invalid argument value(s)". Surface a clear
            # message instead of a raw error dump.
            if "Invalid argument value" in str(exc):
                print("\n  Your VECTR instance rejected the assessment update.")
                print("  This appears to be a server-side limitation of this build's")
                print("  assessment.update resolver — the payload matches the schema,")
                print("  but the server refuses it. Create and delete work normally.")
                print("  (Test case updates also work — see Test Cases > Update.)")
            else:
                print(f"\n  [ERROR] {exc}")
        pause()

    def _delete_assessment(self) -> None:
        rows = self.client.list_assessments(self.db)
        chosen = pick_record("Select an Assessment to DELETE", rows, lambda a: f"[{a['id']}] {a['name']}")
        if not chosen:
            return
        if not confirm(
            "DELETE this assessment. This is destructive and cannot be undone.",
            {"db": self.db, "id": chosen["id"], "name": chosen["name"]},
        ):
            return
        self.client.delete_assessment(self.db, [chosen["id"]])
        print("\n  Deleted.")
        pause()

    # ==================================================================
    # CAMPAIGNS
    # ==================================================================

    def menu_campaigns(self) -> None:
        options = [
            "List campaigns",
            "View campaign + test cases",
            "Create campaign (in an assessment)",
            "Delete campaign",
        ]
        while True:
            idx = menu(f"Campaigns   (db: {self.db})", options)
            if idx == -1:
                return
            if idx == 0:
                self._list_campaigns()
            elif idx == 1:
                self._view_campaign()
            elif idx == 2:
                self._create_campaign()
            elif idx == 3:
                self._delete_campaign()

    def _list_campaigns(self) -> None:
        rows = self.client.list_campaigns(self.db)
        banner(f"Campaigns  (db: {self.db})")
        if not rows:
            print("  (none)")
        for c in rows:
            print(f"  [{c['id']:>4}]  {c['name']}")
        pause()

    def _view_campaign(self) -> None:
        rows = self.client.list_campaigns(self.db)
        chosen = pick_record("Select a Campaign", rows, lambda c: f"[{c['id']}] {c['name']}")
        if not chosen:
            return
        result = self.client.get_campaigns_by_ids(self.db, [chosen["id"]])
        nodes = result.get("campaignsByIds", {}).get("nodes", [])
        banner(f"Campaign {chosen['id']}")
        for c in nodes:
            print(f"  {c['name']}  —  {c.get('description') or ''}")
            for tc in c.get("testCases", []) or []:
                print(f"    [{tc['id']}] {tc['name']}  ({tc.get('mitreId') or 'n/a'})")
        pause()

    def _create_campaign(self) -> None:
        assessments = self.client.list_assessments(self.db)
        parent = pick_record("Select Parent Assessment", assessments, lambda a: f"[{a['id']}] {a['name']}")
        if not parent:
            return
        banner("Create Campaign")
        name = prompt("Name", required=True)
        description = prompt("Description")
        org_ids = self._pick_organizations()
        data: dict[str, Any] = {"name": name}
        if description:
            data["description"] = description
        if org_ids:
            data["organizationIds"] = org_ids

        if not confirm(
            "Create a new campaign inside the selected assessment.",
            {"db": self.db, "assessmentId": parent["id"],
             "assessment": parent["name"], "name": name,
             "description": description or "(none)",
             "organizations": ", ".join(org_ids) if org_ids else "(none)"},
        ):
            return
        result = self.client.create_campaign(self.db, parent["id"], [data])
        created = result.get("campaign", {}).get("create", {}).get("campaigns", [])
        print("\n  Created:")
        for c in created:
            print(f"    [{c['id']}] {c['name']}")
        pause()

    def _delete_campaign(self) -> None:
        rows = self.client.list_campaigns(self.db)
        chosen = pick_record("Select a Campaign to DELETE", rows, lambda c: f"[{c['id']}] {c['name']}")
        if not chosen:
            return
        if not confirm(
            "DELETE this campaign and its test cases. Destructive, cannot be undone.",
            {"db": self.db, "id": chosen["id"], "name": chosen["name"]},
        ):
            return
        self.client.delete_campaign(self.db, [chosen["id"]])
        print("\n  Deleted.")
        pause()

    # ==================================================================
    # TEST CASES
    # ==================================================================

    def menu_testcases(self) -> None:
        options = [
            "List test cases",
            "View test case details",
            "Create test case (map to template by name) [recommended]",
            "Create test case (no template)",
            "Update test case",
            "Delete test case",
        ]
        while True:
            idx = menu(f"Test Cases   (db: {self.db})", options)
            if idx == -1:
                return
            if idx == 0:
                self._list_testcases()
            elif idx == 1:
                self._view_testcase()
            elif idx == 2:
                self._create_tc_with_template()
            elif idx == 3:
                self._create_tc_without_template()
            elif idx == 4:
                self._update_testcase()
            elif idx == 5:
                self._delete_testcase()

    def _list_testcases(self) -> None:
        rows = self.client.get_all_testcases(self.db)
        banner(f"Test Cases  (db: {self.db})  —  {len(rows)} total")
        for tc in rows[:200]:
            tech = tc.get("mitreId") or tc.get("techniqueName") or "n/a"
            print(f"  [{tc['id']:>6}]  {tc['name'][:48]:<48}  {tech}")
        if len(rows) > 200:
            print(f"  ... ({len(rows) - 200} more not shown)")
        pause()

    def _view_testcase(self) -> None:
        rows = self.client.get_all_testcases(self.db)
        chosen = pick_record("Select a Test Case", rows[:300], lambda t: f"[{t['id']}] {t['name']}")
        if not chosen:
            return
        full = self.client.get_testcase(self.db, chosen["id"]).get("testcase", {})
        banner(f"Test Case {chosen['id']}")
        for k, v in (full or {}).items():
            print(f"  {k}: {v}")
        pause()

    def _pick_campaign(self) -> dict | None:
        campaigns = self.client.list_campaigns(self.db)
        return pick_record("Select Target Campaign", campaigns, lambda c: f"[{c['id']}] {c['name']}")

    def _create_tc_with_template(self) -> None:
        campaign = self._pick_campaign()
        if not campaign:
            return
        banner("Create Test Case (template match by name)")
        name = prompt("Test case name", required=True)
        description = prompt("Description", required=True)
        phase = prompt("Phase (e.g. Execution)", required=True)
        technique = prompt("MITRE technique (e.g. T1110)", required=True)
        organization = prompt("Organization", required=True)
        template_name = prompt("Template name to match (blank = use test case name)")

        tc_data: dict[str, Any] = {
            "name": name, "description": description, "phase": phase,
            "technique": technique, "organization": organization,
        }
        create_input: dict[str, Any] = {"testCaseData": tc_data}
        if template_name:
            create_input["templateName"] = template_name

        if not confirm(
            "Create a test case in the campaign and map it to a template by name.",
            {"db": self.db, "campaignId": campaign["id"], "campaign": campaign["name"],
             "name": name, "phase": phase, "technique": technique,
             "template": template_name or "(same as name)"},
        ):
            return
        result = self.client.create_testcase_with_template(self.db, campaign["id"], [create_input])
        created = result.get("testCase", {}).get("createWithTemplateMatchByName", {}).get("testCases", [])
        print("\n  Created:")
        for tc in created:
            print(f"    [{tc['id']}] {tc['name']}")
        pause()

    def _create_tc_without_template(self) -> None:
        campaign = self._pick_campaign()
        if not campaign:
            return
        banner("Create Test Case (no template)")
        print("  Note: VECTR docs advise against this unless you know what you're doing.")
        name = prompt("Test case name", required=True)
        description = prompt("Description", required=True)
        phase = prompt("Phase", required=True)
        technique = prompt("MITRE technique", required=True)
        organization = prompt("Organization", required=True)
        data = {"name": name, "description": description, "phase": phase,
                "technique": technique, "organization": organization}

        if not confirm(
            "Create a test case with NO template mapping.",
            {"db": self.db, "campaignId": campaign["id"], "campaign": campaign["name"],
             "name": name, "phase": phase, "technique": technique},
        ):
            return
        result = self.client.create_testcase_without_template(self.db, campaign["id"], [data])
        created = result.get("testCase", {}).get("createWithoutTemplate", {}).get("testCases", [])
        print("\n  Created:")
        for tc in created:
            print(f"    [{tc['id']}] {tc['name']}")
        pause()

    def _update_testcase(self) -> None:
        rows = self.client.get_all_testcases(self.db)
        chosen = pick_record(
            "Select a Test Case to Update", rows[:300],
            lambda t: f"[{t['id']}] {t['name']}",
        )
        if not chosen:
            return

        banner(f"Update Test Case {chosen['id']}")
        print("  Leave a field blank to keep it unchanged.\n")
        new_desc = prompt("New description")
        new_guidance = prompt("New operator guidance")
        new_notes = prompt("New outcome notes")
        add_tags = prompt_list("Tags to ADD (by name)")
        remove_tags = prompt_list("Tags to REMOVE (by name)")

        # Build the per-item update keyed by testCaseId; include only the
        # fields the user actually filled in. (Shape verified against the live
        # schema: UpdateTestCaseInput.testCaseUpdates[].testCaseId.)
        item: dict[str, Any] = {"testCaseId": str(chosen["id"])}
        if new_desc:
            item["description"] = new_desc
        if new_guidance:
            item["operatorGuidance"] = new_guidance
        if new_notes:
            item["outcomeNotes"] = new_notes
        if add_tags:
            item["addTagsByName"] = add_tags
        if remove_tags:
            item["removeTagsByName"] = remove_tags

        if len(item) == 1:
            print("\n  Nothing to update — no fields were provided.")
            pause()
            return

        if not confirm(
            f"Update test case {chosen['id']}.",
            {"db": self.db, "testCaseId": chosen["id"], "name": chosen["name"],
             "description": new_desc or "(unchanged)",
             "operatorGuidance": new_guidance or "(unchanged)",
             "outcomeNotes": new_notes or "(unchanged)",
             "addTags": ", ".join(add_tags) if add_tags else "(none)",
             "removeTags": ", ".join(remove_tags) if remove_tags else "(none)"},
        ):
            return
        result = self.client.update_testcase(self.db, [item])
        updated = result.get("testCase", {}).get("update", {}).get("testCases", [])
        print("\n  Updated:")
        for tc in updated:
            print(f"    [{tc['id']}] {tc['name']}")
        pause()

    def _delete_testcase(self) -> None:
        rows = self.client.get_all_testcases(self.db)
        chosen = pick_record("Select a Test Case to DELETE", rows[:300], lambda t: f"[{t['id']}] {t['name']}")
        if not chosen:
            return
        if not confirm(
            "DELETE this test case. Destructive, cannot be undone.",
            {"db": self.db, "id": chosen["id"], "name": chosen["name"]},
        ):
            return
        self.client.delete_testcase(self.db, [chosen["id"]])
        print("\n  Deleted.")
        pause()

    # ==================================================================
    # REFERENCE DATA  (read-only)
    # ==================================================================

    def menu_reference_data(self) -> None:
        options = [
            "Organizations", "Phases", "Kill Chains",
            "Outcomes", "Tag Types", "Tags",
        ]
        fns = [
            lambda: self.client.list_organizations(),
            lambda: self.client.list_phases(),
            lambda: self.client.list_killchains(),
            lambda: self.client.list_outcomes(),
            lambda: self.client.list_tag_types(),
            lambda: self.client.list_tags(),
        ]
        while True:
            idx = menu("Reference Data (read-only)", options)
            if idx == -1:
                return
            rows = fns[idx]()
            banner(options[idx])
            if not rows:
                print("  (none)")
            for r in rows:
                print(f"  [{r.get('id')}]  {r.get('name')}")
            pause()

    # ==================================================================
    # ENVIRONMENT TOOLS  (read-only)
    # ==================================================================

    def menu_env_tools(self) -> None:
        options = [
            "Red Tools", "Blue Tools", "Defense Layers",
            "Vendors", "Defense Tool Products",
        ]
        while True:
            idx = menu(f"Environment Tools   (db: {self.db})", options)
            if idx == -1:
                return
            if idx == 0:
                rows = self.client.list_redtools(self.db)
            elif idx == 1:
                rows = self.client.list_bluetools(self.db)
            elif idx == 2:
                rows = self.client.list_defense_layers(self.db)
            elif idx == 3:
                rows = self.client.list_vendors(self.db)
            else:
                rows = self.client.list_defense_tool_products()
            banner(options[idx])
            if not rows:
                print("  (none)")
            for r in rows:
                print(f"  [{r.get('id')}]  {r.get('name')}")
            pause()

    # ==================================================================
    # LIBRARY (templates)
    # ==================================================================

    def menu_library(self) -> None:
        options = [
            "List library test cases",
            "List library campaigns",
            "List library assessments",
            "Create test case template",
            "Clone test case template",
            "Create campaign template",
        ]
        while True:
            idx = menu("Library / Templates", options)
            if idx == -1:
                return
            if idx == 0:
                self._read_list("Library Test Cases", self.client.list_library_testcases)
            elif idx == 1:
                self._read_list("Library Campaigns", self.client.list_library_campaigns)
            elif idx == 2:
                self._read_list("Library Assessments", self.client.list_library_assessments)
            elif idx == 3:
                self._create_tc_template()
            elif idx == 4:
                self._clone_tc_template()
            elif idx == 5:
                self._create_campaign_template()

    def _read_list(self, title: str, fn: Callable[[], list[dict]]) -> None:
        rows = fn()
        banner(title)
        if not rows:
            print("  (none)")
        for r in rows[:200]:
            print(f"  [{r.get('id')}]  {r.get('name')}")
        if len(rows) > 200:
            print(f"  ... ({len(rows) - 200} more)")
        pause()

    def _create_tc_template(self) -> None:
        banner("Create Test Case Template")
        name = prompt("Name", required=True)
        description = prompt("Description", required=True)
        phase = prompt("Phase", required=True)
        technique = prompt("MITRE technique", required=True)
        organization = prompt("Organization", required=True)
        defenses = prompt_list("Defenses")
        red_tools = prompt_list("Red tools")
        tags = prompt_list("Tags")

        data: dict[str, Any] = {
            "name": name, "description": description, "phase": phase,
            "technique": technique, "organization": organization,
        }
        if defenses:
            data["defenses"] = defenses
        if red_tools:
            data["redTools"] = [{"name": t} for t in red_tools]
        if tags:
            data["tags"] = tags

        if not confirm(
            "Create a new test case template in the library.",
            {"name": name, "phase": phase, "technique": technique,
             "organization": organization,
             "redTools": red_tools or "(none)", "tags": tags or "(none)"},
        ):
            return
        result = self.client.create_testcase_template([data])
        created = result.get("testCase", {}).get("createTemplate", {}).get("testCases", [])
        print("\n  Created:")
        for tc in created:
            print(f"    [{tc['id']}] {tc['name']}")
        pause()

    def _clone_tc_template(self) -> None:
        rows = self.client.list_library_testcases()
        source = pick_record("Select Template to Clone", rows[:300], lambda t: f"[{t['id']}] {t['name']}")
        if not source:
            return
        banner("Clone Test Case Template")
        new_name = prompt("New template name", required=True)
        new_desc = prompt("New description (blank = inherit)")
        clone_entry: dict[str, Any] = {"name": new_name}
        if new_desc:
            clone_entry["description"] = new_desc

        if not confirm(
            "Clone the selected library template into a new template.",
            {"sourceId": source["id"], "source": source["name"], "newName": new_name},
        ):
            return
        result = self.client.clone_testcase_template(source["id"], [clone_entry])
        created = result.get("testCase", {}).get("cloneTemplate", {}).get("testCases", [])
        print("\n  Created:")
        for tc in created:
            print(f"    [{tc['id']}] {tc['name']}")
        pause()

    def _create_campaign_template(self) -> None:
        banner("Create Campaign Template")
        name = prompt("Name", required=True)
        description = prompt("Description")
        org_ids = prompt_list("Organization IDs")
        tc_template_ids = prompt_list("Test case template IDs to include")
        data: dict[str, Any] = {"name": name, "description": description}
        if org_ids:
            data["organizationIds"] = org_ids
        if tc_template_ids:
            data["testCaseTemplateIds"] = tc_template_ids

        if not confirm(
            "Create a new campaign template in the library.",
            {"name": name, "description": description,
             "organizationIds": org_ids or "(none)",
             "testCaseTemplateIds": tc_template_ids or "(none)"},
        ):
            return
        result = self.client.create_campaign_template([data])
        created = result.get("campaign", {}).get("createTemplate", {}).get("campaigns", [])
        print("\n  Created:")
        for c in created:
            print(f"    [{c['id']}] {c['name']}")
        pause()

    # ==================================================================
    # APP INFO  (read-only)
    # ==================================================================

    def menu_app_info(self) -> None:
        banner("VECTR App / Server Info")
        try:
            cfg = self.client.get_app_config()
            print(f"  {cfg}")
        except VECTRClientError as exc:
            print(f"  Could not retrieve app config: {exc}")
        pause()

    # ==================================================================
    # RAW GRAPHQL  (read or write — write path is confirmed)
    # ==================================================================

    def menu_raw(self) -> None:
        banner("Run Raw GraphQL")
        print("  Enter a GraphQL query or mutation. End with a single '.' on its own line.")
        print()
        lines: list[str] = []
        while True:
            line = input("  > ")
            if line.strip() == ".":
                break
            lines.append(line)
        query = "\n".join(lines).strip()
        if not query:
            return

        is_mutation = query.lstrip().lower().startswith("mutation")
        if is_mutation:
            if not confirm(
                "Execute a RAW MUTATION. This may create, update, or delete data.",
                {"preview": query[:200] + ("..." if len(query) > 200 else "")},
            ):
                return
        result = self.client.raw_query(query)
        banner("Result")
        import json
        print(json.dumps(result, indent=2)[:4000])
        pause()


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    try:
        with VECTRClient() as client:
            VectrApp(client).run()
    except VECTRAuthError as exc:
        print(f"\n[AUTH ERROR] {exc}")
        print("  -> Check VECTR_API_KEY_ID and VECTR_API_KEY_SECRET.")
        sys.exit(1)
    except VECTRClientError as exc:
        print(f"\n[CLIENT ERROR] {exc}")
        print("  -> Check VECTR_BASE_URL is set and reachable.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
