"""In-memory CRM store — spec 007 tasks "How to use this file".

`FakeCrmStore` implements every public name of `app.services.crm.store`,
including a Python fake of the `crm_apply` database function, so route tests
never touch a database, as the rest of the suite does with its FakeDb. The
real functions are covered by `tests/db/`. Every slice extends this file with
the names it adds to the store; `test_crm_guard.py` fails if one is missing,
because an unfaked name would call the real Supabase client.

The fake keeps the database's rules that a route test can trip — workspace
isolation (CRM04), stale edits (CRM09), the last admin (CRM22), unique labels
(23505) — and raises them through `store.refusal_for`, so the HTTP answer is
the one production gives. A deal's CHECK constraints (030), which production
answers with a 500, raise `CheckViolation` here: a route that lets one
through has missed a rule it must check first.
"""

from __future__ import annotations

import functools
import inspect
import itertools
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.testclient import TestClient

from app.api import deps
from app.domain.crm import ai_fields as ai_field_rules
from app.domain.crm import enrichment as enrichment_rules
from app.domain.crm import pipeline
from app.main import app
from app.services import supabase as db
from app.services.crm import store

OWNER = {
    "id": "u-owner",
    "email": "owner@nunnarilabs.com",
    "display_name": "Owner",
    "role": "admin",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
    "is_owner": True,
    "timezone": "Asia/Kolkata",
    "number_format": "indian_short",
}
MEMBER = {
    "id": "u-member",
    "email": "deepika@nunnarilabs.com",
    "display_name": "Deepika",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
VIEWER = {
    "id": "u-viewer",
    "email": "devansh@nunnarilabs.com",
    "display_name": "Devansh",
    "role": "lead",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
SELLER = {
    "id": "u-seller",
    "email": "tamil@deepweaver.ai",
    "display_name": "Tamil",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "logs_time": False,
    "account_type": "crm_only",
    "timezone": "Australia/Sydney",
    "number_format": "full",
}
OUTSIDER = {
    "id": "u-outsider",
    "email": "vinita@nunnarilabs.com",
    "display_name": "Vinita",
    "role": "admin",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
PEOPLE = {p["id"]: p for p in (OWNER, MEMBER, VIEWER, SELLER, OUTSIDER)}

TABLES = (
    "crm_workspaces",
    "crm_memberships",
    "crm_workspace_settings",
    "crm_select_options",
    "crm_fx_rates",
    "crm_companies",
    "crm_company_domains",
    "crm_people",
    "crm_drive_links",
    "crm_opportunities",
    "crm_opportunity_contacts",
    "crm_stage_history",
    "tasks",
    "task_links",
    "crm_notes",
    "crm_note_links",
    "crm_note_mentions",
    "crm_saved_views",
    "crm_favourites",
    "crm_import_runs",
    # Logs (037): the job log, the bell and the digests' sent markers.
    "crm_job_runs",
    "crm_notifications",
    "crm_digest_sends",
    # Gmail sync (039): the mailboxes (audited) and the email log.
    "crm_mailboxes",
    "crm_email_messages",
    "crm_email_links",
    # Campaigns (041), both audited.
    "crm_campaigns",
    "crm_campaign_members",
    # Bookings targets and saved CSV mappings (043), both audited.
    "crm_bookings_targets",
    "crm_csv_mappings",
    # TVA tables the deal lifecycle writes (033), kept here so a failed
    # transition rolls them back with the rest.
    "projects",
    "project_phases",
    "project_milestones",
    "allocations",
    "time_entries",
    # What a conversion to CRM-only waits on (045): leave and comp-off
    # claims still pending a decision.
    "bookings",
    "compoff_credits",
    # TVA tables the CRM dashboard's capacity panel reads (`TVA_WORLD`).
    "holidays",
    "locations",
    "cost_periods",
    # Spec 009: the AI usage log (060), a log with no audit trigger.
    "crm_ai_usage",
    # Spec 009: the Review Queue (061), a log with explicit audit rows.
    "crm_review_items",
    "crm_review_item_deals",
    # Spec 009: email thread summaries and their links (062), logs.
    "crm_email_summaries",
    "crm_email_summary_links",
    # Spec 009: company enrichment (064), a log queued by a trigger.
    "crm_company_enrichment",
    # Spec 009: AI fields (065), audited, and their values, a log.
    "crm_ai_fields",
    "crm_ai_field_values",
    # Spec 010: discovery candidates (071), a log with no audit trigger.
    "crm_capture_candidates",
    # Spec 010: dry runs (072), a log with no audit trigger.
    "crm_capture_dry_runs",
    # Spec 010: the ignore list (073), audited configuration.
    "crm_capture_ignores",
)
#: The TVA reads the CRM makes (`services/supabase.py`, S5), faked over the
#: rows above. Only names the CRM alone calls, so a test that fakes the rest
#: of TVA itself is not overridden.
TVA_READS = (
    "get_project_by_source_opportunity",
    "list_crm_projects",
    "list_linkable_projects",
    "find_project_by_name",
    "list_project_phases",
    "list_project_milestones",
    "project_has_time_entries",
    "has_pending_decisions",
)
#: The rest of the TVA reads a whole-portal sweep reaches — the CRM
#: dashboard's capacity panel (FR-CRM-CAP-02) and the TVA audit — faked over
#: the same rows by `install_tva` (store name → fake method), which only a
#: test that wants the whole portal in memory calls (`tasks_fake.TaskWorld`).
TVA_WORLD = {
    "list_projects": "list_projects",
    "list_counting_allocations": "list_counting_allocations",
    "list_bookings": "list_bookings",
    "list_holidays": "list_holidays",
    "default_location_id": "default_location_id",
    "list_cost_periods": "list_cost_periods",
    "list_audit": "list_tva_audit",
}
#: The soft-deletable record tables (data-model §2).
RECORDS = (
    "crm_companies",
    "crm_people",
    "crm_drive_links",
    "crm_opportunities",
    "tasks",
    "crm_notes",
    "crm_campaigns",
    "crm_campaign_members",
)
#: The audit trigger's noun per table (026 `crm_audit_row`).
NOUNS = {
    "crm_companies": "company",
    "crm_people": "person",
    "crm_opportunities": "opportunity",
    "crm_workspace_settings": "setting",
    "crm_select_options": "option",
    "crm_mailboxes": "mailbox",
}
#: A polymorphic link's record type → its table (027 `crm_check_refs`).
TARGETS = {
    "company": "crm_companies",
    "person": "crm_people",
    "opportunity": "crm_opportunities",
    "task": "tasks",
    "note": "crm_notes",
    "campaign": "crm_campaigns",
}
OPEN_STAGES = ("discovery", "qualification", "proposal", "negotiation")
#: 061 `crm_review_items.type`.
REVIEW_TYPES = (
    "next_step_change",
    "task_from_commitment",
    "task_from_meeting",
    "deal_link",
    "enrichment_conflict",
    "rule_suggestion",
    # Spec 010 (071, 073).
    "deal_signal",
    "new_lead",
)


class CheckViolation(AssertionError):
    """A CHECK constraint of 030 that the route should have refused first."""


#: Column defaults the database would fill on insert.
_TRASH = {"deleted_at": None, "deleted_by": None, "deleted_group": None}
#: 038's flag columns.
_FLAGS = {"flag_level": "none", "flags": [], "flags_computed_at": None}
DEFAULTS = {
    "crm_select_options": {
        "system_key": None,
        "color": None,
        "is_default": False,
        "is_active": True,
        "is_fixed": False,
    },
    "crm_companies": {
        "entities": [],
        "lead_stage": "new",
        "channel_ids": [],
        "merged_into_id": None,
        "twenty_id": None,
        "last_inbound_at": None,
        "last_outbound_at": None,
        "last_contact_at": None,
        "position": 0,
        **_FLAGS,
        **_TRASH,
    },
    "crm_company_domains": {"is_primary": False},
    "crm_people": {
        "last_name": None,
        "company_id": None,
        "primary_email": None,
        "additional_emails": [],
        "phones": [],
        "owner_id": None,
        "do_not_contact": False,
        "last_inbound_at": None,
        "last_outbound_at": None,
        "last_contact_at": None,
        "position": 0,
        **_FLAGS,
        **_TRASH,
    },
    "crm_drive_links": dict(_TRASH),
    "crm_opportunities": {
        "point_of_contact_id": None,
        "entity": None,
        "stage": "discovery",
        "amount": None,
        "currency": None,
        "amount_in_inr": None,
        "fx_rate_used": None,
        "fx_month": None,
        "won_fx_rate": None,
        "won_fx_month": None,
        "expected_close_on": None,
        "next_step": None,
        "next_step_on": None,
        "channel_id": None,
        "deal_type_id": None,
        "engagement_model_id": None,
        "programme_id": None,
        "lost_reason_id": None,
        "project_category": None,
        "proposal_sent_on": None,
        "lost_notes": None,
        "lost_from_stage": None,
        "won_on": None,
        "lost_on": None,
        "twenty_id": None,
        "position": 0,
        "source_campaign_id": None,
        **_FLAGS,
        **_TRASH,
    },
    "crm_opportunity_contacts": {"role_id": None},
    "crm_campaigns": {
        "type_id": None,
        "status_id": None,
        "channel_id": None,
        "entity": None,
        "starts_on": None,
        "ends_on": None,
        "goal": None,
        "budget": None,
        "budget_currency": None,
        "budget_in_inr": None,
        "budget_fx_rate": None,
        "twenty_id": None,
        "position": 0,
        **_TRASH,
    },
    "crm_campaign_members": {
        "company_id": None,
        "person_id": None,
        "status": "targeted",
        "last_touch_on": None,
        "notes": None,
        "converted_opportunity_id": None,
        "twenty_id": None,
        "position": 0,
        **_TRASH,
    },
    "tasks": {
        "body": None,
        "status": "open",
        "due_on": None,
        "bucket": None,
        "category_id": None,
        "completed_at": None,
        "twenty_id": None,
        "position": 0,
        # 008 (047).
        "priority": "normal",
        "tags": [],
        "parent_id": None,
        "waiting_on_id": None,
        "waiting_on_text": None,
        "source": "manual",
        "source_ref": None,
        **_FLAGS,
        **_TRASH,
    },
    "task_links": {"project_id": None},
    "crm_notes": {
        "kind": "note",
        "occurred_on": None,
        "twenty_id": None,
        # 009 (063): a meeting note the job wrote.
        "source_ref": None,
        "ai_provenance": None,
        **_TRASH,
    },
    "crm_saved_views": {
        "icon": None,
        "kind": "table",
        "visibility": "private",
        "owner_id": None,
        "config": {},
        "system_key": None,
        "is_default": False,
        "hidden": False,
        "position": 0,
    },
    "crm_favourites": {"record_type": None, "position": 0},
    "crm_mailboxes": {
        "is_active": True,
        "history_id": None,
        "page_token": None,
        "backfilled_at": None,
        "last_synced_at": None,
        # Spec 010 (071): every mailbox starts as today's Labels only.
        "capture_source": "labels",
        "capture_start_date": "2026-04-01",
        "docs_checked_at": None,
        "reconcile_after": None,
        # Spec 010 R1 (074): the invitation rescan has not run.
        "invites_rescanned_at": None,
        "invite_rescan_token": None,
    },
    # 071: a candidate as marked, its decision empty (the reset of a later
    # message too).
    "crm_capture_candidates": {
        "status": "pending",
        "attempts": 0,
        "judged_message_id": None,
        "outcome": None,
        "confidence": None,
        "classifier_version": None,
        "judged_at": None,
        "dry_run": False,
        "finding": None,
    },
    # 009 (065): what an Admin's field keeps (D-3).
    "crm_ai_fields": {
        "options": [],
        "max_lines": None,
        "depends_on": None,
        "with_reason": False,
        "enabled": True,
        "position": 0,
    },
}


#: The seeds of migrations 034 and 038, read from the files themselves, so
#: the fake's registry and seeded views are the database's (data-model §3.15,
#: §7.5).
MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase/migrations"
MIGRATION_034 = MIGRATIONS / "034_crm_views.sql"
MIGRATION_038 = MIGRATIONS / "038_crm_flags.sql"
MIGRATION_041 = MIGRATIONS / "041_crm_campaigns.sql"
MIGRATION_047 = MIGRATIONS / "047_tasks_shared_table.sql"
MIGRATION_067 = MIGRATIONS / "067_crm_contact_recency.sql"
_BOOL = {"true": True, "false": False}


def _read_034() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = (
        MIGRATION_034.read_text()
        + MIGRATION_038.read_text()
        + MIGRATION_041.read_text()
        + MIGRATION_047.read_text()
        + MIGRATION_067.read_text()
    )
    field_rows = re.findall(
        r"\('(\w+)', '(\w+)', '(\w+)', '(\w+)', (true|false), (true|false), (true|false), "
        r"(true|false), (true|false), '(\w+)'\)",
        text,
    )
    fields = [
        {
            "object": r[0],
            "field": r[1],
            "column_sql": r[2],
            "type": r[3],
            "is_amount": _BOOL[r[4]],
            "filterable": _BOOL[r[5]],
            "sortable": _BOOL[r[6]],
            "groupable": _BOOL[r[7]],
            "aggregatable": _BOOL[r[8]],
            "workspace_scope": r[9],
        }
        for r in field_rows
    ]
    view_rows = re.findall(
        r"\('(\w+)', '(\w+)', '([^']+)', '([^']+)', '(\w+)', (true|false), (\d+),\s*'(\{.*?\})'\)",
        text,
        re.DOTALL,
    )
    views = [
        {
            "system_key": r[0],
            "object": r[1],
            "name": r[2],
            "icon": r[3],
            "kind": r[4],
            "is_default": _BOOL[r[5]],
            "position": int(r[6]),
            "config": json.loads(r[7]),
        }
        for r in view_rows
    ]
    return fields, views


def _task_status_filter(node: Any) -> Any:
    """047's saved-view rewrite (data-model §3.2): todo and in_progress become
    open; "is not Done" and "none of Done" become "none of Done, Cancelled"."""
    if not isinstance(node, dict):
        return node
    if isinstance(node.get("items"), list):
        return {**node, "items": [_task_status_filter(i) for i in node["items"]]}
    if node.get("field") != "status":
        return node
    out = dict(node)
    value = out.get("value")
    if value in ("todo", "in_progress"):
        out["value"] = "open"
    elif isinstance(value, list):
        out["value"] = list(
            dict.fromkeys("open" if v in ("todo", "in_progress") else v for v in value)
        )
    if out.get("operator") == "is_not" and out.get("value") == "done":
        out.update(operator="none_of", value=["done", "cancelled"])
    elif out.get("operator") == "none_of" and out.get("value") == ["done"]:
        out["value"] = ["done", "cancelled"]
    return out


OBJECT_FIELDS, SEEDED_VIEWS = _read_034()
for _view in SEEDED_VIEWS:
    if _view["object"] == "task" and "filters" in _view["config"]:
        _view["config"]["filters"] = _task_status_filter(_view["config"]["filters"])

_clock = itertools.count(1)
#: Pure: the fake keeps the real batching (S8).
_TEXT_BATCHES = store.text_batches


def _now() -> str:
    # Strictly increasing, so an `expect_updated_at` taken before a write never
    # equals the stamp after it.
    return datetime.fromtimestamp(1_790_000_000 + next(_clock), UTC).isoformat()


class FakeCrmStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in TABLES}
        self.audit: list[dict[str, Any]] = []
        self.applied: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        # The profiles the membership functions read and write: `client()`
        # shares its rows here, so a profile crm_invite creates is the one
        # the next request signs in as.
        self.profiles: dict[str, dict[str, Any]] = {}
        # TVA facts crm_convert_account re-checks (data-model §5.15).
        self.project_leads: set[str] = set()
        self.allocated: set[str] = set()
        # Personal and OAuth tokens crm_remove_membership revokes, by user.
        self.tokens: dict[str, int] = {}
        # Every crm_query call, to check what reached the database.
        self.queries: list[dict[str, Any]] = []
        # Every crm_set_flags call: (table, rows).
        self.flag_writes: list[tuple[str, int]] = []
        # Spec 009: every crm_set_contact_recency call: (table, rows).
        self.recency_writes: list[tuple[str, int]] = []

    # -- set-up helpers (not store names) -----------------------------------

    def add(self, table: str, **values: Any) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "updated_at": _now(), **values}
        self.rows[table][row["id"]] = row
        return row

    def add_workspace(self, key: str, name: str, *, tva_linked: bool = False) -> dict[str, Any]:
        ws = self.add(
            "crm_workspaces",
            key=key,
            name=name,
            tva_linked=tva_linked,
            has_entity=tva_linked,
            allowed_domains=["nunnarilabs.com"],
        )
        # 072: email capture's discovery is seeded off in every workspace.
        self.add(
            "crm_workspace_settings", workspace_id=ws["id"], key="capture", value={"mode": "off"}
        )
        # 074: course enquiries from Labs accepted (FR-CAP-ACAD-03, PD-41).
        self.add(
            "crm_workspace_settings",
            workspace_id=ws["id"],
            key="capture_routing",
            value={"accept_routed": True},
        )
        self.add(
            "crm_workspace_settings",
            workspace_id=ws["id"],
            key="stage_probabilities",
            value={
                "discovery": 10,
                "qualification": 25,
                "proposal": 50,
                "negotiation": 75,
                "closed_won": 100,
                "closed_lost": 0,
            },
        )
        lists = [
            ("opportunity.stage", True, [("discovery", "Discovery"), ("proposal", "Proposal")]),
            ("company.account_type", False, [(None, "Prospect"), ("client", "Client")]),
            ("channel", False, [(None, "Referral"), (None, "Inbound")]),
        ]
        for list_key, fixed, items in lists:
            for pos, (key_, label) in enumerate(items, start=1):
                self.add(
                    "crm_select_options",
                    workspace_id=ws["id"],
                    list_key=list_key,
                    system_key=key_,
                    label=label,
                    color=None,
                    position=pos,
                    is_default=fixed and pos == 1,
                    is_active=True,
                    is_fixed=fixed,
                )
        # 034's seeded views, read from the migration itself.
        for view in SEEDED_VIEWS:
            self.add(
                "crm_saved_views",
                workspace_id=ws["id"],
                visibility="shared",
                owner_id=None,
                hidden=False,
                created_at=_now(),
                created_by=None,
                updated_by=None,
                **json.loads(json.dumps(view)),
            )
        return ws

    def add_member(
        self, workspace: dict[str, Any], user_id: str, role: str, *, last_used_at: str | None = None
    ) -> dict[str, Any]:
        return self.add(
            "crm_memberships",
            workspace_id=workspace["id"],
            user_id=user_id,
            role=role,
            last_used_at=last_used_at,
        )

    def option(self, workspace: dict[str, Any], label: str) -> dict[str, Any]:
        return next(
            o
            for o in self.rows["crm_select_options"].values()
            if o["workspace_id"] == workspace["id"] and o["label"] == label
        )

    def add_deal_lists(self, workspace: dict[str, Any]) -> None:
        """The fixed and rule-bearing lists deals, tasks and notes read
        (026 seeds every one; `add_workspace` keeps only a few)."""
        lists = [
            (
                "company.lead_stage",
                True,
                [
                    ("new", "New"),
                    ("contacted", "Contacted"),
                    ("nurturing", "Nurturing"),
                    ("qualified", "Qualified"),
                    ("deal_open", "Deal open"),
                    ("customer", "Customer"),
                    ("partner", "Partner"),
                    ("disqualified", "Disqualified"),
                ],
            ),
            (
                "opportunity.stage",
                True,
                [
                    ("qualification", "Qualification"),
                    ("negotiation", "Negotiation"),
                    ("closed_won", "Closed Won"),
                    ("closed_lost", "Closed Lost"),
                ],
            ),
            (
                "task.status",
                True,
                [
                    ("open", "Open"),
                    ("waiting", "Waiting"),
                    ("done", "Done"),
                    ("cancelled", "Cancelled"),
                ],
            ),
            ("task.bucket", True, [("today", "Today"), ("this_week", "This week")]),
            ("opportunity.lost_reason", False, [("price", "Price"), ("other", "Other")]),
            ("opportunity.contact_role", False, [(None, "Champion"), (None, "Decision maker")]),
            ("task.category", False, [(None, "Sales"), ("delivery_handoff", "Delivery handoff")]),
            ("opportunity.deal_type", False, [(None, "New business")]),
        ]
        start = len(self.rows["crm_select_options"])
        for list_key, fixed, items in lists:
            for pos, (key_, label) in enumerate(items, start=start + 1):
                self.add(
                    "crm_select_options",
                    workspace_id=workspace["id"],
                    list_key=list_key,
                    system_key=key_,
                    label=label,
                    color=None,
                    position=pos,
                    is_default=False,
                    is_active=True,
                    is_fixed=fixed,
                )

    def add_campaign_lists(self, workspace: dict[str, Any]) -> None:
        """041's campaign lists: types, statuses (Planned the default) and the
        fixed member statuses."""
        lists = [
            ("campaign.type", False, None, [(None, "Webinar"), (None, "College outreach")]),
            (
                "campaign.status",
                False,
                "planned",
                [("planned", "Planned"), ("live", "Live"), ("completed", "Completed")],
            ),
            (
                "campaign_member.status",
                True,
                "targeted",
                [
                    ("targeted", "Targeted"),
                    ("invited", "Invited"),
                    ("registered", "Registered"),
                    ("attended", "Attended"),
                    ("engaged", "Engaged"),
                    ("meeting_booked", "Meeting booked"),
                    ("converted", "Converted"),
                    ("not_interested", "Not interested"),
                    ("unsubscribed", "Unsubscribed"),
                ],
            ),
        ]
        start = len(self.rows["crm_select_options"])
        for list_key, fixed, default, items in lists:
            for pos, (key_, label) in enumerate(items, start=start + 1):
                self.add(
                    "crm_select_options",
                    workspace_id=workspace["id"],
                    list_key=list_key,
                    system_key=key_,
                    label=label,
                    color=None,
                    position=pos,
                    is_default=key_ is not None and key_ == default,
                    is_active=True,
                    is_fixed=fixed,
                )

    def add_project(self, name: str, **values: Any) -> dict[str, Any]:
        """A TVA project, as `admin.create_project` would leave it."""
        defaults = {
            "client": None,
            "category": "client",
            "status": "tentative",
            "probability": None,
            "revenue": None,
            "lead_id": None,
            "is_archived": False,
            "crm_company_id": None,
            "source_opportunity_id": None,
            "crm_archived_at": None,
        }
        return self.add("projects", name=name, **{**defaults, **values})

    def add_allocation(self, project: dict[str, Any], user_id: str, **values: Any) -> dict:
        defaults = {
            "starts_on": "2026-11-02",
            "ends_on": "2026-12-31",
            "percent": "50",
            "released_at": None,
            "released_by": None,
            "release_reason": None,
            "released_by_opportunity_id": None,
        }
        return self.add(
            "allocations", project_id=project["id"], user_id=user_id, **{**defaults, **values}
        )

    def install(self, monkeypatch) -> FakeCrmStore:
        for name in public_store_names():
            monkeypatch.setattr(store, name, getattr(self, name))
        for name in TVA_READS:
            monkeypatch.setattr(db, name, getattr(self, name))
        # 008: the task store reads the same tables (tests/tasks_fake.py).
        from tasks_fake import FakeTaskStore

        self.tasks = FakeTaskStore(self).install(monkeypatch)
        return self

    def install_tva(self, monkeypatch) -> FakeCrmStore:
        """`TVA_WORLD` too, so no TVA read a route makes leaves memory."""
        for name, method in TVA_WORLD.items():
            monkeypatch.setattr(db, name, getattr(self, method))
        return self

    # -- TVA reads (services/supabase.py, S5) --------------------------------

    def has_pending_decisions(self, user_id: str) -> dict[str, bool]:
        def pending(table: str) -> bool:
            return any(
                r.get("user_id") == user_id and r.get("status") == "pending"
                for r in self.rows[table].values()
            )

        return {"leave": pending("bookings"), "compoff": pending("compoff_credits")}

    def get_project_by_source_opportunity(self, opportunity_id: str) -> dict[str, Any] | None:
        return next(
            (
                dict(p)
                for p in self.rows["projects"].values()
                if p.get("source_opportunity_id") == opportunity_id
            ),
            None,
        )

    def list_crm_projects(
        self, *, opportunity_ids: list[str] | None = None, company_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        rows = [
            dict(p)
            for p in self.rows["projects"].values()
            if (opportunity_ids and p.get("source_opportunity_id") in opportunity_ids)
            or (company_ids and p.get("crm_company_id") in company_ids)
        ]
        return sorted(rows, key=lambda p: p["name"])

    def list_linkable_projects(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = [
            {k: p.get(k) for k in ("id", "name", "status", "client")}
            for p in self.rows["projects"].values()
            if not p.get("source_opportunity_id")
            and not p["is_archived"]
            and (status is None or p["status"] == status)
        ]
        return sorted(rows, key=lambda p: p["name"])

    def find_project_by_name(self, name: str) -> dict[str, Any] | None:
        wanted = name.strip().lower()
        return next(
            (
                dict(p)
                for p in self.rows["projects"].values()
                if p["name"].strip().lower() == wanted
            ),
            None,
        )

    def list_project_phases(self, project_ids: list[str]) -> list[dict[str, Any]]:
        rows = [
            dict(p) for p in self.rows["project_phases"].values() if p["project_id"] in project_ids
        ]
        return sorted(rows, key=lambda p: p["starts_on"])

    def list_project_milestones(self, project_id: str) -> list[dict[str, Any]]:
        rows = [
            dict(m)
            for m in self.rows["project_milestones"].values()
            if m["project_id"] == project_id
        ]
        return sorted(rows, key=lambda m: m["due_on"])

    def project_has_time_entries(self, project_id: str) -> bool:
        return any(t["project_id"] == project_id for t in self.rows["time_entries"].values())

    # -- TVA reads (services/supabase.py, `TVA_WORLD`) -----------------------

    def list_projects(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        rows = [
            dict(p)
            for p in self.rows["projects"].values()
            if include_archived or not p["is_archived"]
        ]
        return sorted(rows, key=lambda p: p["name"])

    def list_counting_allocations(
        self, *, user_ids: list[str] | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """As the store: no released row, nothing on an archived project."""
        archived = {p["id"] for p in self.rows["projects"].values() if p["is_archived"]}
        rows = [
            dict(a)
            for a in self.rows["allocations"].values()
            if a.get("released_at") is None
            and a["project_id"] not in archived
            and (user_ids is None or a["user_id"] in user_ids)
            and (project_id is None or a["project_id"] == project_id)
        ]
        return sorted(rows, key=lambda a: a["starts_on"])

    def list_bookings(
        self,
        *,
        user_ids: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        statuses: list[str] | None = None,
        columns: str = "*",
    ) -> list[dict[str, Any]]:
        rows = [
            dict(b)
            for b in self.rows["bookings"].values()
            if (user_ids is None or b["user_id"] in user_ids)
            and (start is None or b["date"] >= start.isoformat())
            and (end is None or b["date"] <= end.isoformat())
            and (statuses is None or b["status"] in statuses)
        ]
        return sorted(rows, key=lambda b: b["date"])

    def list_holidays(self, start: date | None = None, end: date | None = None) -> list[dict]:
        rows = [
            dict(h)
            for h in self.rows["holidays"].values()
            if (start is None or h["date"] >= start.isoformat())
            and (end is None or h["date"] <= end.isoformat())
        ]
        return sorted(rows, key=lambda h: h["date"])

    def default_location_id(self) -> str | None:
        return next((r["id"] for r in self.rows["locations"].values() if r.get("is_default")), None)

    def list_cost_periods(self, user_id: str | None = None) -> list[dict[str, Any]]:
        rows = [
            dict(c)
            for c in self.rows["cost_periods"].values()
            if user_id is None or c["user_id"] == user_id
        ]
        return sorted(rows, key=lambda c: c["starts_on"])

    def list_tva_audit(self, limit: int = 200, *, tva_only: bool = True) -> list[dict[str, Any]]:
        """`supabase.list_audit`: with `tva_only`, no row with a workspace and
        no row of a task table (007 FR-CRM-AUD-04, 008 FR-TASK-VIS-05)."""
        rows = [
            dict(r)
            for r in self.audit
            if not tva_only
            or (r["workspace_id"] is None and r["target_table"] not in db.TASK_AUDIT_TABLES)
        ]
        return sorted(rows, key=lambda r: r["id"], reverse=True)[:limit]

    # -- store: workspaces and memberships ----------------------------------

    def memberships_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": m["id"],
                "role": m["role"],
                "last_used_at": m.get("last_used_at"),
                "workspace": {
                    k: self.rows["crm_workspaces"][m["workspace_id"]][k]
                    for k in ("id", "key", "name", "tva_linked", "has_entity", "allowed_domains")
                },
            }
            for m in self.rows["crm_memberships"].values()
            if m["user_id"] == user_id
        ]

    def touch_last_used(self, membership_id: str, at: datetime) -> None:
        self.rows["crm_memberships"][membership_id]["last_used_at"] = at.isoformat()

    # -- store: members, invitations, account type -------------------------

    _MEMBERSHIP = ("id", "workspace_id", "user_id", "role", "created_at")

    def _membership(self, row: dict[str, Any]) -> dict[str, Any]:
        return {k: row.get(k) for k in self._MEMBERSHIP}

    def list_members(self, workspace_id: str) -> list[dict[str, Any]]:
        return [
            self._membership(m)
            for m in self.rows["crm_memberships"].values()
            if m["workspace_id"] == workspace_id
        ]

    def get_membership(self, workspace_id: str, membership_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_memberships"].get(membership_id)
        return self._membership(row) if row and row["workspace_id"] == workspace_id else None

    def find_membership(self, membership_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_memberships"].get(membership_id)
        return self._membership(row) if row else None

    def list_workspaces(self) -> list[dict[str, Any]]:
        rows = [dict(w) for w in self.rows["crm_workspaces"].values()]
        return sorted(rows, key=lambda w: w["name"])

    def list_account_audit(
        self, *, workspace_id: str | None = None, before: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self.audit
            if r["action"].startswith("membership.") or r["action"] in store.ACCOUNT_ACTIONS
        ]
        if workspace_id:
            # As the store: the TVA `user.*` rows (no workspace) of the
            # people who are or were its members.
            people = {
                (r.get(k) or {}).get("user_id")
                for r in self.audit
                if r["workspace_id"] == workspace_id and r["target_table"] == "crm_memberships"
                for k in ("after", "before")
            } | {m["user_id"] for m in self.list_members(workspace_id)}
            rows = [
                r
                for r in rows
                if r["workspace_id"] == workspace_id
                or (
                    r["workspace_id"] is None
                    and r["action"].startswith("user.")
                    and r["target_id"] in people
                )
            ]
        if before is not None:
            rows = [r for r in rows if r["id"] < before]
        return sorted(rows, key=lambda r: r["id"], reverse=True)[:limit]

    def _is_tva_admin(self, user_id: str | None) -> bool:
        p = self.profiles.get(user_id or "")
        return bool(
            p
            and p.get("role") == "admin"
            and p.get("account_type", "staff") == "staff"
            and p.get("is_active", True)
        )

    def _tva_row(self, actor, action, target_id, before=None, after=None, *, ws=None) -> None:
        self.audit.append(
            {
                "id": len(self.audit) + 1,
                "at": _now(),
                "actor_id": actor["user_id"],
                "actor_label": actor.get("kind", "user"),
                "channel": actor.get("channel"),
                "workspace_id": ws,
                "action": action,
                "target_table": "profiles",
                "target_id": target_id,
                "before": before,
                "after": after,
            }
        )

    def invite(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """crm_invite (data-model §5.13)."""
        ws, who, uid = actor["workspace_id"], actor["user_id"], plan["user_id"]
        created = reactivated = False
        if plan.get("create_profile"):
            self.profiles[uid] = {
                "id": uid,
                "email": plan["email"],
                "display_name": plan["display_name"],
                "role": "user",
                "lead_id": None,
                "is_active": True,
                "logs_time": False,
                "account_type": "crm_only",
                "timezone": plan.get("timezone") or "Asia/Kolkata",
            }
            created = True
            self._tva_row(
                actor,
                "user.created",
                uid,
                after={"account_type": "crm_only", "email": plan["email"]},
            )
        else:
            person = self.profiles.get(uid)
            if person is None:
                raise store.refusal_for("CRM04", "No such person.")
            if not person.get("is_active", True):
                if (
                    not plan.get("reactivate")
                    or person.get("account_type") != "crm_only"
                    or (
                        person.get("deactivated_reason") != "crm_no_membership"
                        and not self._is_tva_admin(who)
                    )
                ):
                    raise store.refusal_for(
                        "CRM22", "This person's portal account is inactive; ask a TVA admin."
                    )
                person.update(is_active=True, deactivated_reason=None)
                reactivated = True
                self._tva_row(actor, "user.reactivated", uid, after={"is_active": True})
        if any(
            m["workspace_id"] == ws and m["user_id"] == uid
            for m in self.rows["crm_memberships"].values()
        ):
            raise store.refusal_for(
                "23505",
                'duplicate key value violates unique constraint "crm_memberships_workspace_user"',
            )
        m = self.add("crm_memberships", workspace_id=ws, user_id=uid, role=plan["role"])
        self._audit(actor, {"action": "membership.invited", "table": "crm_memberships"}, m, None, m)
        return {
            "membership_id": m["id"],
            "user_id": uid,
            "created_profile": created,
            "reactivated": reactivated,
        }

    def remove_membership(self, actor: dict[str, Any], membership_id: str) -> dict[str, Any]:
        """crm_remove_membership (data-model §5.3)."""
        ws, who = actor["workspace_id"], actor["user_id"]
        m = self.rows["crm_memberships"].get(membership_id)
        if m is None or m["workspace_id"] != ws:
            raise store.refusal_for("CRM04", "No such member in this workspace.")
        if m["role"] == "admin" and not any(
            o["workspace_id"] == ws and o["role"] == "admin" and o["id"] != m["id"]
            for o in self.rows["crm_memberships"].values()
        ):
            raise store.refusal_for("CRM22", "A workspace needs at least one admin.")
        person = self.profiles.get(m["user_id"], {})
        if (person.get("is_owner") or self._is_tva_admin(m["user_id"])) and not self._is_tva_admin(
            who
        ):
            raise store.refusal_for("CRM22", "Ask a TVA admin to change this membership.")
        # 037: the person's notifications for the workspace go with it.
        for n in [
            n
            for n in self.rows["crm_notifications"].values()
            if n["workspace_id"] == ws and n["user_id"] == m["user_id"]
        ]:
            del self.rows["crm_notifications"][n["id"]]
        del self.rows["crm_memberships"][m["id"]]
        self._audit(
            actor, {"action": "membership.removed", "table": "crm_memberships"}, m, dict(m), None
        )
        # 040: their mailboxes in the workspace are paused.
        for box in self.rows["crm_mailboxes"].values():
            if box["workspace_id"] == ws and box["owner_id"] == m["user_id"] and box["is_active"]:
                box["is_active"] = False
                self._audit(
                    actor,
                    {"op": "update", "table": "crm_mailboxes"},
                    box,
                    {"is_active": True},
                    {"is_active": False},
                )
        deactivated = False
        if (
            person.get("account_type") == "crm_only"
            and person.get("is_active", True)
            and not any(o["user_id"] == m["user_id"] for o in self.rows["crm_memberships"].values())
        ):
            person.update(is_active=False, deactivated_reason="crm_no_membership")
            self.tokens[m["user_id"]] = 0
            deactivated = True
            self._tva_row(
                actor, "user.deactivated", m["user_id"], {"is_active": True}, {"is_active": False}
            )
        return {"deactivated": deactivated}

    def convert_account(self, actor: dict[str, Any], user_id: str, to: str) -> dict[str, Any]:
        """crm_convert_account (data-model §5.15)."""
        person = self.profiles.get(user_id)
        if person is None:
            raise store.refusal_for("CRM04", "No such person.")
        current = person.get("account_type", "staff")
        if to == current:
            return {"id": user_id, "account_type": current, "changed": False}
        if to == "crm_only":
            if person.get("is_owner"):
                raise store.refusal_for("CRM22", "The owner's account cannot be CRM-only.")
            if any(p.get("lead_id") == user_id for p in self.profiles.values()):
                raise store.refusal_for(
                    "CRM22",
                    "This person is someone's lead; give their reports another lead first.",
                )
            if user_id in self.project_leads:
                raise store.refusal_for(
                    "CRM22", "This person leads a project; give it another lead first."
                )
            if user_id in self.allocated:
                raise store.refusal_for(
                    "CRM22",
                    "This person is allocated to a project; remove the allocation first.",
                )
            waiting = self.has_pending_decisions(user_id)
            if waiting["leave"]:
                raise store.refusal_for(
                    "CRM22",
                    "This person has leave waiting for a decision; decide or withdraw it first.",
                )
            if waiting["compoff"]:
                raise store.refusal_for(
                    "CRM22",
                    "This person has a comp-off claim waiting for a decision; decide it first.",
                )
            before = {k: person.get(k) for k in ("account_type", "role", "dashboard_access")}
            person.update(
                account_type="crm_only",
                role="user",
                lead_id=None,
                logs_time=False,
                dashboard_access=False,
            )
        else:
            before = {"account_type": current}
            person["account_type"] = "staff"
        self._tva_row(actor, "user.account_type", user_id, before, {"account_type": to})
        return {"id": user_id, "account_type": to, "role": person["role"], "changed": True}

    # -- store: settings, lists, FX -----------------------------------------

    def _of(self, table: str, workspace_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows[table].values() if r["workspace_id"] == workspace_id]

    def list_settings(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._of("crm_workspace_settings", workspace_id)

    def list_options(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._of("crm_select_options", workspace_id)
        return sorted(rows, key=lambda o: (o["list_key"], o["position"]))

    def get_option(self, workspace_id: str, option_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_select_options"].get(option_id)
        return dict(row) if row and row["workspace_id"] == workspace_id else None

    def list_fx_rates(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._of("crm_fx_rates", workspace_id)
        return sorted(rows, key=lambda r: (r["month"], r["currency"]), reverse=True)

    # -- store: audit -------------------------------------------------------

    def list_audit(self, workspace_id: str, **filters: Any) -> list[dict[str, Any]]:
        rows = [r for r in self.audit if r["workspace_id"] == workspace_id]
        for key, column in (
            ("target_table", "target_table"),
            ("target_id", "target_id"),
            ("actor_id", "actor_id"),
            ("action", "action"),
        ):
            if filters.get(key):
                rows = [r for r in rows if r[column] == filters[key]]
        if filters.get("before") is not None:
            rows = [r for r in rows if r["id"] < filters["before"]]
        rows = sorted(rows, key=lambda r: r["id"], reverse=True)
        return rows[: filters.get("limit", 50)]

    # -- store: records (S3) ------------------------------------------------

    def get_record(self, workspace_id: str, table: str, record_id: str) -> dict[str, Any] | None:
        row = self.rows[table].get(record_id)
        return dict(row) if row and row["workspace_id"] == workspace_id else None

    def list_records(
        self,
        workspace_id: str,
        table: str,
        *,
        q: str | None = None,
        filters: dict[str, Any] | None = None,
        any_of: dict[str, list[str]] | None = None,
        not_equal: dict[str, Any] | None = None,
        deleted: bool | None = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = self._of(table, workspace_id)
        for column, value in (filters or {}).items():
            rows = [r for r in rows if r.get(column) == value]
        for column, value in (not_equal or {}).items():
            rows = [r for r in rows if r.get(column) != value]
        for column, values in (any_of or {}).items():
            rows = [r for r in rows if r.get(column) in values]
        if deleted is True:
            rows = [r for r in rows if r.get("deleted_at")]
        elif deleted is False:
            rows = [r for r in rows if not r.get("deleted_at")]
        if q:
            columns = {
                "crm_companies": ("name",),
                "crm_people": ("first_name", "last_name", "primary_email"),
                "crm_drive_links": ("label",),
                "crm_opportunities": ("name",),
                "tasks": ("title",),
                "crm_notes": ("body",),
                "crm_campaigns": ("name",),
                "crm_campaign_members": ("notes",),
            }[table]
            rows = [r for r in rows if any(q.lower() in (r.get(c) or "").lower() for c in columns)]
        key = "deleted_at" if deleted is True else "updated_at"
        rows.sort(key=lambda r: (r.get(key) or "", r["id"]), reverse=True)
        return rows[offset : offset + limit]

    def list_domains(
        self,
        workspace_id: str,
        *,
        company_ids: list[str] | None = None,
        domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._of("crm_company_domains", workspace_id)
        if company_ids is not None:
            rows = [r for r in rows if r["company_id"] in company_ids]
        if domains is not None:
            rows = [r for r in rows if r["domain"] in domains]
        return sorted(rows, key=lambda r: (not r["is_primary"], r["domain"]))

    def domains_named(self, workspace_id: str, name: str) -> list[dict[str, Any]]:
        """The domains starting `name.` (store.domains_named's LIKE)."""
        rows = [
            r
            for r in self._of("crm_company_domains", workspace_id)
            if r["domain"].startswith(f"{name}.")
        ]
        return sorted(rows, key=lambda r: (not r["is_primary"], r["domain"]))

    def similar(self, workspace_id: str, kind: str, name: str, limit: int = 5) -> list[dict]:
        """crm_similar (029) with a Python pg_trgm similarity."""
        table = {"company": "crm_companies", "person": "crm_people"}[kind]
        found = []
        for r in self.list_records(workspace_id, table, limit=10_000):
            label = (
                r["name"]
                if kind == "company"
                else " ".join(p for p in (r["first_name"], r.get("last_name")) if p)
            )
            score = trigram_similarity(label, name)
            if score >= 0.4:
                found.append({"id": r["id"], "name": label, "similarity": score})
        return sorted(found, key=lambda f: (-f["similarity"], f["name"]))[:limit]

    def list_expired(self, workspace_id: str, table: str, cutoff: datetime) -> list[dict]:
        return [
            {"id": r["id"], "deleted_at": r["deleted_at"]}
            for r in self._of(table, workspace_id)
            if r.get("deleted_at") and _at(r["deleted_at"]) < cutoff
        ]

    def purge(self, actor: dict[str, Any], items: list[dict[str, str]]) -> dict[str, Any]:
        """crm_purge (029): only from the actor's workspace's Trash, with the
        Drive links that point at the record."""
        self.applied.append((actor, [{"op": "purge", **i} for i in items]))
        ws = actor["workspace_id"]
        kinds = {
            "crm_companies": "company",
            "crm_people": "person",
            "crm_opportunities": "opportunity",
            "tasks": "task",
            "crm_notes": "note",
            "crm_campaigns": "campaign",
            "crm_drive_links": None,
        }
        snapshot = {t: {k: dict(v) for k, v in rows.items()} for t, rows in self.rows.items()}
        audit_len = len(self.audit)
        try:
            for item in items:
                table, target = item["table"], item["id"]
                if table not in kinds:
                    raise store.refusal_for("CRM22", "That cannot be purged.")
                row = self.rows[table].get(target)
                if row is None or row["workspace_id"] != ws or not row.get("deleted_at"):
                    raise store.refusal_for("CRM04", "No such record in the Trash.")
                # 033: a deal with a project is never purged; a company's
                # projects lose the link, as TVA rows.
                if table == "crm_opportunities" and self.get_project_by_source_opportunity(target):
                    raise store.refusal_for(
                        "CRM22", "This deal has a TVA project; mark it Closed Lost instead."
                    )
                if table == "crm_companies":
                    for project in self.rows["projects"].values():
                        if project.get("crm_company_id") == target:
                            project["crm_company_id"] = None
                            self._tva_audit(
                                actor,
                                "project.updated",
                                "projects",
                                project["id"],
                                {"crm_company_id": target},
                                {"crm_company_id": None},
                            )
                if kinds[table]:
                    unlinked = set()
                    for links in ("crm_drive_links", "task_links", "crm_note_links"):
                        for link in list(self.rows[links].values()):
                            if link["record_type"] == kinds[table] and link["record_id"] == target:
                                if links == "task_links":
                                    unlinked.add(link["task_id"])
                                self._delete(actor, links, link)
                    # 048 (008 FR-TASK-DATA-14): a task left with no CRM link
                    # goes too, live or in the Trash.
                    for task_id in sorted(unlinked):
                        task = self.rows["tasks"].get(task_id)
                        if task and task["workspace_id"] == ws and not self._crm_links(task_id):
                            self._cascade(actor, "tasks", task_id)
                            self._delete(actor, "tasks", task)
                    # 039: the email links to it (a log: no audit row).
                    for link in list(self.rows["crm_email_links"].values()):
                        if (
                            link["workspace_id"] == ws
                            and link["record_type"] == kinds[table]
                            and link["record_id"] == target
                        ):
                            del self.rows["crm_email_links"][link["id"]]
                    # 062: the email summaries' links to it (a log).
                    for link in list(self.rows["crm_email_summary_links"].values()):
                        if (
                            link["workspace_id"] == ws
                            and link["record_type"] == kinds[table]
                            and link["record_id"] == target
                        ):
                            del self.rows["crm_email_summary_links"][link["id"]]
                    # 065: its AI field values (a log).
                    for value in list(self.rows["crm_ai_field_values"].values()):
                        if (
                            value["workspace_id"] == ws
                            and value["record_type"] == kinds[table]
                            and value["record_id"] == target
                        ):
                            del self.rows["crm_ai_field_values"][value["id"]]
                    # 061: the Review Queue items that target it.
                    for found in list(self.rows["crm_review_items"].values()):
                        if (
                            found["workspace_id"] == ws
                            and found["record_type"] == kinds[table]
                            and found["record_id"] == target
                        ):
                            del self.rows["crm_review_items"][found["id"]]
                    # 034: the favourites that pin it.
                    for fav in list(self.rows["crm_favourites"].values()):
                        if (
                            fav["workspace_id"] == ws
                            and fav["target_type"] == "record"
                            and fav["record_type"] == kinds[table]
                            and fav["target_id"] == target
                        ):
                            self._delete(actor, "crm_favourites", fav)
                self._cascade(actor, table, target)
                self._delete(actor, table, row)
                self._review_cascade()
        except Exception:
            self.rows, self.audit[audit_len:] = snapshot, []
            raise
        return {"purged": len(items)}

    def _crm_links(self, task_id: str) -> list[dict[str, Any]]:
        return [
            lk
            for lk in self.rows["task_links"].values()
            if lk["task_id"] == task_id and lk["record_type"] != "tva_project"
        ]

    def _cascade(self, actor, table: str, target: str) -> None:
        """The foreign-key actions a purge sets off (029, 030, 041), and a
        mailbox's delete (039: its messages and their links)."""
        members = self.rows["crm_campaign_members"]
        if table in ("crm_companies", "crm_people"):
            # 041: a membership naming the other one stays with it, unless
            # that one has its own already; the rest go (CASCADE).
            mine, other = (
                ("company_id", "person_id")
                if table == "crm_companies"
                else ("person_id", "company_id")
            )
            for m in list(members.values()):
                if m.get(mine) != target:
                    continue
                kept = m.get(other) and not any(
                    o["campaign_id"] == m["campaign_id"]
                    and o.get(mine) is None
                    and o.get(other) == m[other]
                    for o in members.values()
                )
                if kept:
                    self._update(actor, {"table": "crm_campaign_members"}, m, {mine: None})
                else:
                    self._delete(actor, "crm_campaign_members", m)
        if table == "crm_campaigns":
            for m in list(members.values()):
                if m["campaign_id"] == target:
                    self._delete(actor, "crm_campaign_members", m)
            for d in self.rows["crm_opportunities"].values():
                if d.get("source_campaign_id") == target:
                    self._update(
                        actor, {"table": "crm_opportunities"}, d, {"source_campaign_id": None}
                    )
        if table == "crm_opportunities":
            for m in members.values():
                if m.get("converted_opportunity_id") == target:
                    self._update(
                        actor,
                        {"table": "crm_campaign_members"},
                        m,
                        {"converted_opportunity_id": None},
                    )
        if table == "crm_mailboxes":
            for msg in list(self.rows["crm_email_messages"].values()):
                if msg["mailbox_id"] == target:
                    self._forget_message(msg["id"])
            # 062: a mailbox's summaries go with it (CASCADE).
            for summary in list(self.rows["crm_email_summaries"].values()):
                if summary["mailbox_id"] == target:
                    self._forget_summary(summary["id"])
        if table == "crm_companies":
            if any(d["company_id"] == target for d in self.rows["crm_opportunities"].values()):
                raise store.refusal_for("23503", "violates foreign key constraint")
            for d in list(self.rows["crm_company_domains"].values()):
                if d["company_id"] == target:
                    self._delete(actor, "crm_company_domains", d)
            # 064: the company's enrichment goes with it (CASCADE).
            for e in list(self.rows["crm_company_enrichment"].values()):
                if e["company_id"] == target:
                    del self.rows["crm_company_enrichment"][e["id"]]
            for p in self.rows["crm_people"].values():
                if p.get("company_id") == target:
                    self._update(actor, {"table": "crm_people"}, p, {"company_id": None})
        if table == "crm_people":
            for c in list(self.rows["crm_opportunity_contacts"].values()):
                if c["person_id"] == target:
                    self._delete(actor, "crm_opportunity_contacts", c)
            for d in self.rows["crm_opportunities"].values():
                if d.get("point_of_contact_id") == target:
                    self._update(
                        actor, {"table": "crm_opportunities"}, d, {"point_of_contact_id": None}
                    )
        if table == "crm_opportunities":
            for c in list(self.rows["crm_opportunity_contacts"].values()):
                if c["opportunity_id"] == target:
                    self._delete(actor, "crm_opportunity_contacts", c)
            for h in list(self.rows["crm_stage_history"].values()):
                if h["opportunity_id"] == target:
                    del self.rows["crm_stage_history"][h["id"]]
        if table == "tasks":
            for link in list(self.rows["task_links"].values()):
                if link["task_id"] == target:
                    self._delete(actor, "task_links", link)
            # 047: a subtask's parent is cleared (ON DELETE SET NULL).
            for child in self.rows["tasks"].values():
                if child.get("parent_id") == target:
                    self._update(actor, {"table": "tasks"}, child, {"parent_id": None})
        if table == "crm_notes":
            for links in ("crm_note_links", "crm_note_mentions"):
                for link in list(self.rows[links].values()):
                    if link["note_id"] == target:
                        self._delete(actor, links, link)

    # -- store: deals, tasks, notes (S4) -------------------------------------

    def list_links(
        self,
        workspace_id: str,
        table: str,
        *,
        owner_ids: list[str] | None = None,
        record_type: str | None = None,
        record_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        owner = store.LINK_TABLES[table]
        rows = self._of(table, workspace_id)
        if owner_ids is not None:
            rows = [r for r in rows if r[owner] in owner_ids]
        if record_type is not None:
            rows = [r for r in rows if r["record_type"] == record_type]
        if record_ids is not None:
            rows = [r for r in rows if r["record_id"] in record_ids]
        return sorted(rows, key=lambda r: r["created_at"])

    def list_mentions(self, workspace_id: str, note_ids: list[str]) -> list[dict[str, Any]]:
        return [r for r in self._of("crm_note_mentions", workspace_id) if r["note_id"] in note_ids]

    def list_contacts(
        self,
        workspace_id: str,
        *,
        opportunity_ids: list[str] | None = None,
        person_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._of("crm_opportunity_contacts", workspace_id)
        if opportunity_ids is not None:
            rows = [r for r in rows if r["opportunity_id"] in opportunity_ids]
        if person_ids is not None:
            rows = [r for r in rows if r["person_id"] in person_ids]
        return sorted(rows, key=lambda r: r["created_at"])

    def list_stage_history(self, workspace_id: str, opportunity_id: str) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self._of("crm_stage_history", workspace_id)
            if r["opportunity_id"] == opportunity_id
        ]
        return sorted(rows, key=lambda r: r["at"])

    def deal_transition(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """crm_deal_transition (031): all of it or none."""
        self.applied.append((actor, [{"op": "deal_transition", **plan}]))
        snapshot = {t: {k: dict(v) for k, v in rows.items()} for t, rows in self.rows.items()}
        audit_len = len(self.audit)
        try:
            return self._transition(actor, plan)
        except Exception:
            self.rows, self.audit[audit_len:] = snapshot, []
            raise

    def _tva_manager(self, user_id: str | None) -> bool:
        """033 `crm_tva_manager`: an active staff manager or admin."""
        p = self.profiles.get(user_id or "")
        return bool(
            p
            and p.get("is_active", True)
            and p.get("account_type", "staff") == "staff"
            and p.get("role") in ("manager", "admin")
        )

    def _tva_audit(self, actor, action, table, target_id, before, after) -> None:
        """033 `crm_tva_audit`: a TVA row (no workspace), the system acting
        for the person."""
        self.audit.append(
            {
                "id": len(self.audit) + 1,
                "at": _now(),
                "actor_id": actor["user_id"],
                "actor_label": "system",
                "channel": actor.get("channel"),
                "workspace_id": None,
                "action": action,
                "target_table": table,
                "target_id": target_id,
                "before": before,
                "after": after,
            }
        )

    def _deal_audit(self, actor, action: str, opp: dict[str, Any], after: dict) -> None:
        self.audit.append(
            {
                "id": len(self.audit) + 1,
                "at": _now(),
                "actor_id": actor["user_id"],
                "actor_label": actor["kind"],
                "channel": actor.get("channel"),
                "workspace_id": opp["workspace_id"],
                "action": action,
                "target_table": "crm_opportunities",
                "target_id": opp["id"],
                "before": None,
                "after": after,
            }
        )

    def _create_project(self, actor, opp, company, spec: dict[str, Any], status: str) -> dict:
        """033 `crm_tva_create_project`."""
        name = (spec.get("name") or "").strip()
        if not name:
            raise store.refusal_for("CRM22", "A project needs a name.")
        clash = self.find_project_by_name(name)
        if clash:
            raise store.refusal_for("CRM09", f"A project called “{clash['name']}” already exists.")
        made = self.add_project(
            name,
            client=company["name"],
            category=spec.get("category") or "client",
            status=status,
            probability=spec.get("probability"),
            lead_id=spec.get("lead_id"),
            crm_company_id=company["id"],
            source_opportunity_id=opp["id"],
            created_by=actor["user_id"],
        )
        self._tva_audit(
            actor,
            "project.created",
            "projects",
            made["id"],
            None,
            {"name": made["name"], "status": status, "source_opportunity_id": opp["id"]},
        )
        return made

    def _transition(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        ws, who = actor["workspace_id"], actor["user_id"]
        pa = plan.get("project") if isinstance(plan.get("project"), dict) else None
        act = (pa or {}).get("action")
        if pa is not None:
            if act not in (
                "create",
                "link",
                "sync_probability",
                "confirm",
                "archive_release",
                "unarchive_unrelease",
                "retentative",
            ):
                raise store.refusal_for("CRM22", "That is not a TVA project action.")
            if not self.rows["crm_workspaces"][ws].get("tva_linked"):
                raise store.refusal_for("CRM22", "This workspace is not connected to TVA projects.")
            adopt = act == "link" or (act == "confirm" and "project_id" in pa)
            if adopt and not self._tva_manager(who):
                raise store.refusal_for("CRM22", "Ask a TVA manager to link it.")
            if (
                act == "confirm"
                and ("revenue" in pa or "milestones" in pa)
                and not self._tva_manager(who)
            ):
                raise store.refusal_for(
                    "CRM22", "Only a TVA manager or admin can set revenue and milestones."
                )
        opp = self.rows["crm_opportunities"].get(plan["opportunity_id"])
        if opp is None or opp["workspace_id"] != ws or opp.get("deleted_at"):
            raise store.refusal_for("CRM04", "No such opportunity.")
        if plan.get("expect_stage") and opp["stage"] != plan["expect_stage"]:
            raise store.refusal_for(
                "CRM09", "This deal moved while you were editing it; reload and try again."
            )
        if plan.get("expect_updated_at") and _at(plan["expect_updated_at"]) != _at(
            opp["updated_at"]
        ):
            raise store.refusal_for(
                "CRM09", "This record changed while you were editing it; reload and try again."
            )
        company = self.rows["crm_companies"].get(opp["company_id"])
        if company is None or company["workspace_id"] != ws or company.get("deleted_at"):
            raise store.refusal_for("CRM04", "No such company.")
        wanted = plan.get("company")
        if wanted:
            if wanted.get("id") != company["id"]:
                raise store.refusal_for("CRM04", "No such company.")
            if wanted.get("expect_updated_at") and _at(wanted["expect_updated_at"]) != _at(
                company["updated_at"]
            ):
                raise store.refusal_for(
                    "CRM09", "The company changed meanwhile; reload and try again."
                )
        siblings = plan.get("expect_siblings")
        if siblings:
            others = [
                d
                for d in self.rows["crm_opportunities"].values()
                if d["workspace_id"] == ws
                and d["company_id"] == company["id"]
                and d["id"] != opp["id"]
                and not d.get("deleted_at")
            ]
            open_n = sum(1 for d in others if d["stage"] in pipeline.OPEN)
            won = any(d["stage"] == "closed_won" for d in others)
            if (open_n, won) != (siblings["open"], siblings["won"]):
                raise store.refusal_for(
                    "CRM09", "The company's deals changed meanwhile; reload and try again."
                )
        for task_id in plan.get("close_tasks") or []:
            task = self.rows["tasks"].get(task_id)
            on_deal = any(
                link["task_id"] == task_id
                and link["record_type"] == "opportunity"
                and link["record_id"] == opp["id"]
                for link in self.rows["task_links"].values()
            )
            if task is None or task["workspace_id"] != ws or task.get("deleted_at") or not on_deal:
                raise store.refusal_for("CRM04", "No such task on this deal.")
        if (plan.get("opportunity") or {}).get("stage") == "closed_lost":
            linked = {
                link["task_id"]
                for link in self.rows["task_links"].values()
                if link["record_type"] == "opportunity" and link["record_id"] == opp["id"]
            }
            if any(
                t["id"] in linked
                and t["workspace_id"] == ws
                and not t.get("deleted_at")
                and t["status"] in ("open", "waiting")
                and t["id"] not in (plan.get("close_tasks") or [])
                for t in self.rows["tasks"].values()
            ):
                raise store.refusal_for(
                    "CRM09", "The deal's tasks changed meanwhile; reload and try again."
                )
        for t in plan.get("create_tasks") or []:
            if not any(
                m["workspace_id"] == ws and m["user_id"] == t["assignee_id"]
                for m in self.rows["crm_memberships"].values()
            ):
                raise store.refusal_for(
                    "CRM04", "A task's assignee must be a member of this workspace."
                )
        # Re-check 4: the deal's own project; a plan's project id only as the
        # target of a link.
        project = self.get_project_by_source_opportunity(opp["id"]) if pa else None
        project = self.rows["projects"][project["id"]] if project else None
        target_project = None
        if act == "link" or (act == "confirm" and "project_id" in pa):
            if project:
                raise store.refusal_for("CRM09", "This deal already has a TVA project.")
            target_project = self.rows["projects"].get(pa.get("project_id"))
            if target_project is None:
                raise store.refusal_for("CRM04", "No such project.")
            if (
                target_project.get("source_opportunity_id")
                or target_project["is_archived"]
                or target_project["status"] != "tentative"
            ):
                raise store.refusal_for(
                    "CRM09",
                    f"“{target_project['name']}” cannot be linked: only an unlinked, "
                    "unarchived tentative project can.",
                )
        if act == "retentative" and project:
            if self.project_has_time_entries(project["id"]):
                raise store.refusal_for(
                    "CRM22",
                    f"{project['name']} has time logged against it, so it cannot be made "
                    "tentative.",
                )
            if any(m.get("invoiced_on") for m in self.list_project_milestones(project["id"])):
                raise store.refusal_for(
                    "CRM22",
                    f"{project['name']} has invoiced milestones, so it cannot be made tentative.",
                )

        values = dict(plan.get("opportunity") or {})
        if values.get("stage_entered_at") == "now":
            values["stage_entered_at"] = _now()
        op = {"table": "crm_opportunities"}
        if plan.get("history"):
            op["action"] = "opportunity.stage_changed"
        self._update(actor, op, opp, {**values, "updated_by": who})
        self._check_rows("crm_opportunities", opp)
        if plan.get("history"):
            self.add(
                "crm_stage_history",
                workspace_id=ws,
                opportunity_id=opp["id"],
                from_stage=plan["history"]["from"],
                to_stage=plan["history"]["to"],
                at=opp["stage_entered_at"],
                by=who,
                actor_kind=actor["kind"],
            )
        company_row = None
        if wanted and wanted.get("changes"):
            self._update(actor, {"table": "crm_companies"}, company, dict(wanted["changes"]))
            company_row = dict(company)
        closed = 0
        for task_id in plan.get("close_tasks") or []:
            task = self.rows["tasks"][task_id]
            # 048 (008 FR-TASK-CHG-04): Cancelled, not Done.
            if task["status"] in ("open", "waiting"):
                self._update(
                    actor,
                    {"table": "tasks"},
                    task,
                    {"status": "cancelled", "completed_at": None},
                )
                closed += 1
        note_id = None
        if plan.get("close_note"):
            refs: dict[str, str] = {}
            made = self._one(
                actor,
                {
                    "op": "insert",
                    "table": "crm_notes",
                    "values": {"body": plan["close_note"]["body"], "kind": "note"},
                },
                refs,
            )
            note_id = made["id"]
            self._one(
                actor,
                {
                    "op": "insert",
                    "table": "crm_note_links",
                    "values": {
                        "note_id": note_id,
                        "record_type": "opportunity",
                        "record_id": opp["id"],
                    },
                },
                refs,
            )
        created = []
        for t in plan.get("create_tasks") or []:
            refs = {}
            category = next(
                (
                    o["id"]
                    for o in self.rows["crm_select_options"].values()
                    if o["workspace_id"] == ws
                    and o["list_key"] == "task.category"
                    and t.get("category_key")
                    and o.get("system_key") == t["category_key"]
                ),
                None,
            )
            task = self._one(
                actor,
                {
                    "op": "insert",
                    "table": "tasks",
                    "values": {
                        "title": t["title"],
                        "body": t.get("body"),
                        "status": "open",
                        "priority": "normal",
                        "source": "rule",
                        "assignee_id": t["assignee_id"],
                        "due_on": t.get("due_on"),
                        "bucket": t.get("bucket"),
                        "category_id": category,
                    },
                },
                refs,
            )
            if not t.get("links"):
                raise store.refusal_for("CRM22", "A task must be linked to at least one record.")
            for link in t["links"]:
                self._one(
                    actor,
                    {
                        "op": "insert",
                        "table": "task_links",
                        "values": {"task_id": task["id"], **link},
                    },
                    refs,
                )
            created.append(task["id"])

        released = restored = 0
        if pa is not None:
            project, released, restored = self._tva_side(
                actor, pa, opp, company, project, target_project
            )
        return {
            "opportunity": dict(opp),
            "company": company_row,
            "project": dict(project) if project else None,
            "tasks_created": created,
            "tasks_closed": closed,
            "note_id": note_id,
            "allocations_released": released,
            "allocations_restored": restored,
        }

    def _tva_side(self, actor, pa, opp, company, project, target_project):
        """033's TVA branches, by `project.action`."""
        act, prob = pa["action"], pa.get("probability")
        released = restored = 0
        if act == "create" and project is None:
            project = self._create_project(actor, opp, company, pa, "tentative")
        elif act == "link":
            project = target_project
            project.update(
                source_opportunity_id=opp["id"],
                crm_company_id=company["id"],
                client=company["name"],
                probability=prob if prob is not None else project["probability"],
            )
            self._tva_audit(
                actor, "project.updated", "projects", project["id"], None, {"linked": opp["id"]}
            )
        elif act == "confirm":
            if target_project is not None:
                # FR-CRM-TVA-10 with -05: the handoff adopts the project.
                project = target_project
                project.update(
                    source_opportunity_id=opp["id"],
                    crm_company_id=company["id"],
                    client=company["name"],
                )
                self._tva_audit(
                    actor, "project.updated", "projects", project["id"], None, {"linked": opp["id"]}
                )
            if project is None:
                project = self._create_project(actor, opp, company, pa, "confirmed")
            else:
                name = (pa.get("name") or project["name"]).strip()
                clash = self.find_project_by_name(name)
                if clash and clash["id"] != project["id"]:
                    raise store.refusal_for("CRM09", f"A project called “{name}” already exists.")
                project.update(
                    name=name,
                    category=pa.get("category") or project["category"],
                    status="confirmed",
                    probability=prob if prob is not None else project["probability"],
                    lead_id=pa.get("lead_id"),
                )
                self._tva_audit(
                    actor,
                    "project.updated",
                    "projects",
                    project["id"],
                    None,
                    {"status": "confirmed"},
                )
            for kind, span in (pa.get("phases") or {}).items():
                if kind not in ("pre", "delivery", "support"):
                    raise store.refusal_for(
                        "CRM22", "The handoff sets only pre, delivery and support phases."
                    )
                found = next(
                    (
                        p
                        for p in self.rows["project_phases"].values()
                        if p["project_id"] == project["id"] and p["phase"] == kind
                    ),
                    None,
                )
                if found:
                    found.update(starts_on=span["starts_on"], ends_on=span["ends_on"])
                else:
                    found = self.add(
                        "project_phases",
                        project_id=project["id"],
                        phase=kind,
                        starts_on=span["starts_on"],
                        ends_on=span["ends_on"],
                        budget_hours=None,
                    )
                self._tva_audit(
                    actor, "project.phase_set", "project_phases", found["id"], None, span
                )
            if "revenue" in pa:
                project["revenue"] = pa["revenue"]
                self._tva_audit(
                    actor,
                    "project.updated",
                    "projects",
                    project["id"],
                    None,
                    {"revenue": pa["revenue"]},
                )
            if "milestones" in pa:
                # The full set, matched by id; invoiced ones never change.
                kept = {m["id"] for m in pa["milestones"] if m.get("id")}
                for old in self.list_project_milestones(project["id"]):
                    if old["id"] in kept:
                        continue
                    if old.get("invoiced_on"):
                        raise store.refusal_for(
                            "CRM22",
                            f"“{old['name']}” is invoiced, so the handoff cannot remove it.",
                        )
                    del self.rows["project_milestones"][old["id"]]
                    self._tva_audit(
                        actor, "milestone.removed", "project_milestones", old["id"], old, None
                    )
            for m in pa.get("milestones") or []:
                if m.get("id"):
                    old = self.rows["project_milestones"].get(m["id"])
                    if old is None or old["project_id"] != project["id"]:
                        raise store.refusal_for("CRM04", "No such milestone on this project.")
                    new = {k: m[k] for k in ("name", "due_on", "amount")}
                    if all(str(old[k]) == str(v) for k, v in new.items()):
                        continue
                    if old.get("invoiced_on"):
                        raise store.refusal_for(
                            "CRM22",
                            f"“{old['name']}” is invoiced, so the handoff cannot change it.",
                        )
                    old.update(new)
                    self._tva_audit(
                        actor, "milestone.updated", "project_milestones", old["id"], None, new
                    )
                    continue
                made = self.add(
                    "project_milestones", project_id=project["id"], invoiced_on=None, **m
                )
                self._tva_audit(actor, "milestone.added", "project_milestones", made["id"], None, m)
            self._deal_audit(
                actor,
                "opportunity.handoff",
                opp,
                {
                    "project_id": project["id"],
                    "project_name": project["name"],
                    "lead_id": project.get("lead_id"),
                    "revenue_written": "revenue" in pa,
                },
            )
        elif act == "sync_probability" and project and project["status"] == "tentative":
            if project["probability"] != prob:
                project["probability"] = prob
                self._tva_audit(
                    actor, "project.updated", "projects", project["id"], None, {"probability": prob}
                )
        elif act == "archive_release" and project:
            if not project["is_archived"]:
                project.update(
                    is_archived=True,
                    crm_archived_at=_now(),
                    probability=prob if prob is not None else project["probability"],
                )
                self._tva_audit(
                    actor, "project.updated", "projects", project["id"], None, {"is_archived": True}
                )
            for a in self.rows["allocations"].values():
                if a["project_id"] == project["id"] and a["released_at"] is None:
                    a.update(
                        released_at=_now(),
                        released_by=actor["user_id"],
                        release_reason=pa.get("reason") or "Deal lost",
                        released_by_opportunity_id=opp["id"],
                    )
                    released += 1
                    self._tva_audit(
                        actor,
                        "allocation.released",
                        "allocations",
                        a["id"],
                        None,
                        {"released": True},
                    )
        elif act == "unarchive_unrelease" and project:
            if project.get("crm_archived_at"):
                project.update(is_archived=False, crm_archived_at=None)
                self._tva_audit(
                    actor,
                    "project.updated",
                    "projects",
                    project["id"],
                    None,
                    {"is_archived": False},
                )
            if project["status"] == "tentative" and prob is not None:
                project["probability"] = prob
            for a in self.rows["allocations"].values():
                if a.get("released_by_opportunity_id") == opp["id"]:
                    a.update(
                        released_at=None,
                        released_by=None,
                        release_reason=None,
                        released_by_opportunity_id=None,
                    )
                    restored += 1
                    self._tva_audit(
                        actor,
                        "allocation.unreleased",
                        "allocations",
                        a["id"],
                        None,
                        {"released": False},
                    )
        elif act == "retentative" and project:
            project.update(
                status="tentative",
                probability=prob if prob is not None else project["probability"],
            )
            self._tva_audit(
                actor, "project.updated", "projects", project["id"], None, {"status": "tentative"}
            )
        return project, released, restored

    def link_project(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """crm_link_project (033): all of it or none."""
        self.applied.append((actor, [{"op": "link_project", **plan}]))
        snapshot = {t: {k: dict(v) for k, v in rows.items()} for t, rows in self.rows.items()}
        audit_len = len(self.audit)
        try:
            return self._link(actor, plan)
        except Exception:
            self.rows, self.audit[audit_len:] = snapshot, []
            raise

    def _link(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        ws, who, mode = actor["workspace_id"], actor["user_id"], plan.get("mode")
        if mode not in ("link", "create"):
            raise store.refusal_for("CRM22", "Link or create a project.")
        if not self.rows["crm_workspaces"][ws].get("tva_linked"):
            raise store.refusal_for("CRM22", "This workspace is not connected to TVA projects.")
        if not any(
            m["workspace_id"] == ws and m["user_id"] == who and m["role"] == "admin"
            for m in self.rows["crm_memberships"].values()
        ):
            raise store.refusal_for("CRM22", "Only a workspace admin can link a TVA project.")
        if mode == "link" and not self._tva_manager(who):
            raise store.refusal_for("CRM22", "Ask a TVA manager to link it.")
        opp = self.rows["crm_opportunities"].get(plan["opportunity_id"])
        if opp is None or opp["workspace_id"] != ws or opp.get("deleted_at"):
            raise store.refusal_for("CRM04", "No such opportunity.")
        if plan.get("expect_updated_at") and _at(plan["expect_updated_at"]) != _at(
            opp["updated_at"]
        ):
            raise store.refusal_for(
                "CRM09", "This record changed while you were editing it; reload and try again."
            )
        company = self.rows["crm_companies"][opp["company_id"]]
        if self.get_project_by_source_opportunity(opp["id"]):
            raise store.refusal_for("CRM09", "This deal already has a TVA project.")
        if opp["stage"] not in ("proposal", "negotiation", "closed_won"):
            raise store.refusal_for(
                "CRM22", "Only a deal at Proposal or later, or a won deal, gets a TVA project."
            )
        if opp["stage"] == "closed_won" and mode == "create":
            raise store.refusal_for(
                "CRM22", "A won deal is linked to its existing confirmed project."
            )
        if mode == "create":
            project = self._create_project(
                actor,
                opp,
                company,
                {
                    "name": plan.get("name") or opp["name"],
                    "category": plan.get("category") or opp.get("project_category") or "client",
                    "probability": opp["probability"],
                },
                "tentative",
            )
        else:
            project = self.rows["projects"].get(plan.get("project_id"))
            if project is None:
                raise store.refusal_for("CRM04", "No such project.")
            wanted = "confirmed" if opp["stage"] == "closed_won" else "tentative"
            if (
                project.get("source_opportunity_id")
                or project["is_archived"]
                or project["status"] != wanted
            ):
                raise store.refusal_for(
                    "CRM09",
                    f"“{project['name']}” cannot be linked: it must be unlinked, unarchived "
                    f"and {wanted}.",
                )
            project.update(
                source_opportunity_id=opp["id"],
                crm_company_id=company["id"],
                client=company["name"],
            )
            if wanted == "tentative":
                project["probability"] = opp["probability"]
            self._tva_audit(
                actor, "project.updated", "projects", project["id"], None, {"linked": opp["id"]}
            )
        self._deal_audit(
            actor,
            "opportunity.project_linked",
            opp,
            {"project_id": project["id"], "project_name": project["name"], "mode": mode},
        )
        return {"opportunity": dict(opp), "project": dict(project)}

    def list_timeline(
        self,
        workspace_id: str,
        table: str,
        record_id: str,
        *,
        record_type: str,
        related: dict[str, list[str]] | None = None,
        before: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        def about(r: dict[str, Any]) -> bool:
            sides = [r.get("after") or {}, r.get("before") or {}]
            # 008 FR-TASK-CHG-07: task rows under either name.
            named = {table, store.OLD_NAMES.get(table, table)}
            if r["target_table"] in named and r["target_id"] == record_id:
                return True
            links = ("crm_drive_links", "task_links", "crm_task_links", "crm_note_links")
            if r["target_table"] in links and any(
                x.get("record_id") == record_id and x.get("record_type") == record_type
                for x in sides
            ):
                return True
            if any(
                r["target_id"] in ids and r["target_table"] in (t, store.OLD_NAMES.get(t, t))
                for t, ids in (related or {}).items()
            ):
                return True
            if r["target_table"] == "crm_company_domains" and table == "crm_companies":
                return any(x.get("company_id") == record_id for x in sides)
            if r["target_table"] == "crm_opportunity_contacts" and table == "crm_opportunities":
                return any(x.get("opportunity_id") == record_id for x in sides)
            return False

        rows = [r for r in self.audit if r["workspace_id"] == workspace_id and about(r)]
        if before is not None:
            rows = [r for r in rows if r["id"] < before]
        return sorted(rows, key=lambda r: r["id"], reverse=True)[:limit]

    # -- store: views (S6a) ---------------------------------------------------

    def list_object_fields(self) -> list[dict[str, Any]]:
        return [dict(f) for f in sorted(OBJECT_FIELDS, key=lambda f: (f["object"], f["field"]))]

    def list_views(self, workspace_id: str, *, object: str | None = None) -> list[dict]:
        rows = self._of("crm_saved_views", workspace_id)
        if object is not None:
            rows = [r for r in rows if r["object"] == object]
        return sorted(rows, key=lambda r: (r["position"], r["name"]))

    def get_view(self, workspace_id: str, view_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_saved_views"].get(view_id)
        return dict(row) if row and row["workspace_id"] == workspace_id else None

    def list_favourites(
        self,
        workspace_id: str,
        *,
        user_id: str | None = None,
        target_type: str | None = None,
        target_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._of("crm_favourites", workspace_id)
        if user_id is not None:
            rows = [r for r in rows if r["user_id"] == user_id]
        if target_type is not None:
            rows = [r for r in rows if r["target_type"] == target_type]
        if target_ids is not None:
            rows = [r for r in rows if r["target_id"] in target_ids]
        return sorted(rows, key=lambda r: (r["position"], r["created_at"]))

    def query(
        self, workspace_id: str, kind: str, spec: dict[str, Any], *, redact: bool
    ) -> dict[str, Any]:
        """crm_query (034) in Python: the same checks, filters, keyset paging,
        groups and totals, over the registry the migration seeds."""
        self.queries.append(
            {"workspace_id": workspace_id, "kind": kind, "spec": spec, "redact": redact}
        )
        return FakeQuery(self, workspace_id, kind, json.loads(json.dumps(spec)), redact).run()

    # -- store: Today, search, merge (S7) -------------------------------------

    def due_next_steps(self, workspace_id: str, owner_id: str, by: str) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self._of("crm_opportunities", workspace_id)
            if r.get("owner_id") == owner_id
            and r["stage"] in OPEN_STAGES
            and not r.get("deleted_at")
            and r.get("next_step_on")
            and str(r["next_step_on"]) <= by
        ]
        return sorted(rows, key=lambda r: (str(r["next_step_on"]), r["name"]))

    def tasks_for_today(self, workspace_id: str, assignee_id: str, by: str) -> list[dict]:
        rows = [
            r
            for r in self._of("tasks", workspace_id)
            if r["assignee_id"] == assignee_id
            and r["status"] in ("open", "waiting")
            and not r.get("deleted_at")
            and (r.get("bucket") == "today" or (r.get("due_on") and str(r["due_on"]) <= by))
        ]
        return sorted(
            rows, key=lambda r: (r.get("due_on") is None, str(r.get("due_on")), r["title"])
        )

    def nurturing_without_next_touch(self, workspace_id: str, owner_id: str) -> list[dict]:
        rows = [
            r
            for r in self._of("crm_companies", workspace_id)
            if r.get("owner_id") == owner_id
            and r["lead_stage"] == "nurturing"
            and not r.get("next_touch_on")
            and not r.get("deleted_at")
        ]
        return sorted(rows, key=lambda r: r["name"])

    def search(self, workspace_id: str, q: str, limit: int, *, redact: bool) -> dict[str, Any]:
        """crm_search (035) in Python: a trigram match, a word inside a
        longer text, or a literal prefix; best first, live records only."""
        self.queries.append({"workspace_id": workspace_id, "search": q, "redact": redact})
        q = q.strip().lower()
        if len(q) < 2:
            raise store.refusal_for("CRM22", "Type at least two characters.")
        lim = max(1, min(limit, 8))

        def score(*texts: str | None) -> float | None:
            # As 035: the best match, averaged with the first text's whole
            # similarity, so an exact name outranks a longer one.
            best, hit, first = 0.0, False, None
            for text in texts:
                if not text:
                    continue
                text = text.lower()
                whole = trigram_similarity(text, q)
                first = whole if first is None else first
                words = max((trigram_similarity(w, q) for w in text.split()), default=0.0)
                if whole >= 0.3 or words >= 0.6 or text.startswith(q):
                    hit = True
                best = max(best, whole, words)
            return (best + (first or 0.0)) / 2 if hit else None

        def ranked(rows: list[tuple[float | None, dict]]) -> list[dict]:
            # Best first, then the most recently changed (stable sorts).
            kept = sorted((p for p in rows if p[0] is not None), key=lambda p: p[1]["id"])
            kept.sort(key=lambda p: str(p[1]["updated_at"]), reverse=True)
            kept.sort(key=lambda p: p[0], reverse=True)
            return [r for _, r in kept[:lim]]

        def live(table: str) -> list[dict]:
            return [r for r in self._of(table, workspace_id) if not r.get("deleted_at")]

        domains = self._of("crm_company_domains", workspace_id)
        companies = {r["id"]: r for r in self._of("crm_companies", workspace_id)}
        found: dict[str, list[dict]] = {}
        found["companies"] = ranked(
            [
                (
                    score(c["name"], *[d["domain"] for d in domains if d["company_id"] == c["id"]]),
                    {
                        "id": c["id"],
                        "name": c["name"],
                        "lead_stage": c["lead_stage"],
                        "owner_id": c.get("owner_id"),
                        "updated_at": c["updated_at"],
                        "domain": next(
                            (
                                d["domain"]
                                for d in domains
                                if d["company_id"] == c["id"] and d["is_primary"]
                            ),
                            None,
                        ),
                    },
                )
                for c in live("crm_companies")
                if not c.get("merged_into_id")
            ]
        )
        found["people"] = ranked(
            [
                (
                    score(
                        f"{p['first_name']} {p.get('last_name') or ''}".strip(),
                        p.get("primary_email"),
                        *(p.get("additional_emails") or []),
                    ),
                    {
                        "id": p["id"],
                        "first_name": p["first_name"],
                        "last_name": p.get("last_name"),
                        "primary_email": p.get("primary_email"),
                        "company_id": p.get("company_id"),
                        "company_name": (companies.get(p.get("company_id") or "") or {}).get(
                            "name"
                        ),
                        "updated_at": p["updated_at"],
                    },
                )
                for p in live("crm_people")
            ]
        )
        found["opportunities"] = ranked(
            [
                (
                    score(o["name"]),
                    {
                        "id": o["id"],
                        "name": o["name"],
                        "stage": o["stage"],
                        "company_id": o["company_id"],
                        "company_name": (companies.get(o["company_id"]) or {}).get("name"),
                        "owner_id": o.get("owner_id"),
                        "currency": o.get("currency"),
                        "updated_at": o["updated_at"],
                        **(
                            {}
                            if redact
                            else {
                                "amount": o.get("amount"),
                                "amount_in_inr": o.get("amount_in_inr"),
                            }
                        ),
                    },
                )
                for o in live("crm_opportunities")
            ]
        )
        found["tasks"] = ranked(
            [
                (
                    score(t["title"]),
                    {
                        "id": t["id"],
                        "title": t["title"],
                        "status": t["status"],
                        "due_on": t.get("due_on"),
                        "assignee_id": t["assignee_id"],
                        "updated_at": t["updated_at"],
                    },
                )
                for t in live("tasks")
            ]
        )
        found["notes"] = ranked(
            [
                (
                    score(n["body"][:200]),
                    {
                        "id": n["id"],
                        "kind": n["kind"],
                        "snippet": n["body"][:200],
                        "created_at": n.get("created_at"),
                        "updated_at": n["updated_at"],
                    },
                )
                for n in live("crm_notes")
            ]
        )
        # 041: campaigns by name, never a budget.
        found["campaigns"] = ranked(
            [
                (
                    score(c["name"]),
                    {
                        "id": c["id"],
                        "name": c["name"],
                        "status_id": c.get("status_id"),
                        "starts_on": c.get("starts_on"),
                        "ends_on": c.get("ends_on"),
                        "owner_id": c.get("owner_id"),
                        "updated_at": c["updated_at"],
                    },
                )
                for c in live("crm_campaigns")
            ]
        )
        return found

    def merge_companies(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """crm_merge_companies (035): both companies re-checked in the
        actor's workspace, then everything moves, all or nothing."""
        self.applied.append((actor, [{"op": "merge", **plan}]))
        ws, who = actor["workspace_id"], actor["user_id"]
        keep_id, gone_id = plan.get("survivor_id"), plan.get("merged_id")
        if keep_id == gone_id:
            raise store.refusal_for("CRM22", "Pick two different companies to merge.")
        keep = self.rows["crm_companies"].get(keep_id)
        gone = self.rows["crm_companies"].get(gone_id)
        if not keep or not gone or keep["workspace_id"] != ws or gone["workspace_id"] != ws:
            raise store.refusal_for("CRM04", "No such company.")
        for row in (keep, gone):
            if row.get("merged_into_id"):
                raise store.refusal_for(
                    "CRM22", f"“{row['name']}” was already merged into another company."
                )
        for row in (keep, gone):
            if row.get("deleted_at"):
                raise store.refusal_for(
                    "CRM22", f"“{row['name']}” is in the Trash; restore it before merging."
                )
        expect = plan.get("expect") or {}
        for row, key in ((keep, "survivor_updated_at"), (gone, "merged_updated_at")):
            if expect.get(key) and _at(expect[key]) != _at(row["updated_at"]):
                raise store.refusal_for(
                    "CRM09", "One of these companies changed meanwhile; reload and try again."
                )
        snapshot = {t: {k: dict(v) for k, v in rows.items()} for t, rows in self.rows.items()}
        audit_len = len(self.audit)
        moved = dict.fromkeys(
            ("people", "deals", "tasks", "notes", "links", "domains", "emails", "campaign_members"),
            0,
        )
        try:
            changes = plan.get("survivor_changes") or {}
            if changes:
                self._update(actor, {"table": "crm_companies"}, keep, dict(changes))
            moved["projects"] = sum(
                1 for p in self.rows["projects"].values() if p.get("crm_company_id") == gone_id
            )
            for table, key in (("crm_people", "people"), ("crm_opportunities", "deals")):
                for row in list(self.rows[table].values()):
                    if row["workspace_id"] == ws and row.get("company_id") == gone_id:
                        self._update(actor, {"table": table}, row, {"company_id": keep_id})
                        moved[key] += 1
            for table, owner, key in (
                ("task_links", "task_id", "tasks"),
                ("crm_note_links", "note_id", "notes"),
            ):
                for link in list(self.rows[table].values()):
                    if not (
                        link["workspace_id"] == ws
                        and link["record_type"] == "company"
                        and link["record_id"] == gone_id
                    ):
                        continue
                    if not any(
                        other[owner] == link[owner]
                        and other["record_type"] == "company"
                        and other["record_id"] == keep_id
                        for other in self.rows[table].values()
                    ):
                        self._one(
                            actor,
                            {
                                "op": "insert",
                                "table": table,
                                "values": {
                                    owner: link[owner],
                                    "record_type": "company",
                                    "record_id": keep_id,
                                },
                            },
                            {},
                        )
                    self._delete(actor, table, link)
                    moved[key] += 1
            # 039: email links, a log, move without audit rows.
            for link in list(self.rows["crm_email_links"].values()):
                if not (
                    link["workspace_id"] == ws
                    and link["record_type"] == "company"
                    and link["record_id"] == gone_id
                ):
                    continue
                if any(
                    other["message_id"] == link["message_id"]
                    and other["record_type"] == "company"
                    and other["record_id"] == keep_id
                    for other in self.rows["crm_email_links"].values()
                ):
                    del self.rows["crm_email_links"][link["id"]]
                else:
                    link["record_id"] = keep_id
                moved["emails"] += 1
            # 062: email summary links, as email links.
            for link in list(self.rows["crm_email_summary_links"].values()):
                if not (
                    link["workspace_id"] == ws
                    and link["record_type"] == "company"
                    and link["record_id"] == gone_id
                ):
                    continue
                if any(
                    other["summary_id"] == link["summary_id"]
                    and other["record_type"] == "company"
                    and other["record_id"] == keep_id
                    for other in self.rows["crm_email_summary_links"].values()
                ):
                    del self.rows["crm_email_summary_links"][link["id"]]
                else:
                    link["record_id"] = keep_id
            # 061: the merged company's open review items move, their dedupe
            # key's target recomputed; a clash with the survivor's expires.
            for item in sorted(
                self.rows["crm_review_items"].values(), key=lambda r: (r["created_at"], r["id"])
            ):
                if not (
                    item["workspace_id"] == ws
                    and item["record_type"] == "company"
                    and item["record_id"] == gone_id
                    and item["status"] == "open"
                ):
                    continue
                head = f"{item['source_kind']}|{item['source_key']}|{item['type']}|company:"
                key = item["dedupe_key"]
                if key.startswith(head + gone_id):
                    key = head + keep_id + key[len(head + gone_id) :]
                if any(
                    o["id"] != item["id"]
                    and o["workspace_id"] == ws
                    and o["status"] == "open"
                    and o["dedupe_key"] == key
                    for o in self.rows["crm_review_items"].values()
                ):
                    item.update(status="expired", decided_at=_now())
                    self._review_audit(actor, "review.expired", item["id"], "open", "expired")
                else:
                    item.update(record_id=keep_id, dedupe_key=key)
            # 064: enrichment; the survivor's own row wins, else the merged
            # company's moves. A survivor's row only the chosen website
            # queued, for the merged company's site, goes first.
            mine = self.rows["crm_company_enrichment"]
            gone_sites = {e["website"] for e in mine.values() if e["company_id"] == gone_id}
            for e in list(mine.values()):
                if (
                    e["company_id"] == keep_id
                    and e["status"] == "queued"
                    and e.get("requested_by") is None
                    and e["website"] in gone_sites
                ):
                    del mine[e["id"]]
            if any(e["company_id"] == keep_id for e in mine.values()):
                for e in [e for e in mine.values() if e["company_id"] == gone_id]:
                    del mine[e["id"]]
            else:
                for e in mine.values():
                    if e["company_id"] == gone_id:
                        e["company_id"] = keep_id
            # 065: AI field values; the survivor's own value of a field wins,
            # else the merged company's moves, marked for recompute.
            values = self.rows["crm_ai_field_values"]
            kept = {
                v["field_id"]
                for v in values.values()
                if v["record_type"] == "company" and v["record_id"] == keep_id
            }
            for v in list(values.values()):
                if v["record_type"] != "company" or v["record_id"] != gone_id:
                    continue
                if v["field_id"] in kept:
                    del values[v["id"]]
                else:
                    v.update(record_id=keep_id, dirty_at=_now())
            # 041: campaign members; one the survivor has already keeps it.
            members = self.rows["crm_campaign_members"]
            for m in sorted(list(members.values()), key=lambda r: r["id"]):
                if m["workspace_id"] != ws or m.get("company_id") != gone_id:
                    continue
                if any(
                    o["campaign_id"] == m["campaign_id"]
                    and o.get("company_id") == keep_id
                    and o.get("person_id") == m.get("person_id")
                    for o in members.values()
                ):
                    self._delete(actor, "crm_campaign_members", m)
                else:
                    self._update(
                        actor, {"table": "crm_campaign_members"}, m, {"company_id": keep_id}
                    )
                moved["campaign_members"] += 1
            for link in list(self.rows["crm_drive_links"].values()):
                if (
                    link["workspace_id"] == ws
                    and link["record_type"] == "company"
                    and link["record_id"] == gone_id
                ):
                    self._update(actor, {"table": "crm_drive_links"}, link, {"record_id": keep_id})
                    moved["links"] += 1

            def readd(domain: dict[str, Any], primary: bool) -> None:
                self._delete(actor, "crm_company_domains", domain)
                self._one(
                    actor,
                    {
                        "op": "insert",
                        "table": "crm_company_domains",
                        "values": {
                            "company_id": keep_id,
                            "domain": domain["domain"],
                            "is_primary": primary,
                        },
                    },
                    {},
                )

            for domain in sorted(
                (
                    d
                    for d in self.rows["crm_company_domains"].values()
                    if d["company_id"] == gone_id
                ),
                key=lambda d: d["domain"],
            ):
                readd(domain, False)
                moved["domains"] += 1
            wanted = plan.get("primary_domain")
            if wanted:
                mine = [
                    d
                    for d in self.rows["crm_company_domains"].values()
                    if d["company_id"] == keep_id
                ]
                if not any(d["domain"] == wanted for d in mine):
                    raise store.refusal_for(
                        "CRM22", "The primary domain must be one of the two companies' domains."
                    )
                for d in mine:
                    if d["is_primary"] and d["domain"] != wanted:
                        readd(d, False)
                for d in mine:
                    if d["domain"] == wanted and not d["is_primary"]:
                        readd(d, True)
            for project in self.rows["projects"].values():
                if project.get("crm_company_id") == gone_id:
                    project["crm_company_id"] = keep_id
                    self._tva_audit(
                        actor,
                        "project.updated",
                        "projects",
                        project["id"],
                        {"crm_company_id": gone_id},
                        {"crm_company_id": keep_id},
                    )
            self._update(
                actor,
                {"table": "crm_companies", "action": "company.merged"},
                gone,
                {
                    "merged_into_id": keep_id,
                    "deleted_at": _now(),
                    "deleted_by": who,
                    "deleted_group": str(uuid.uuid4()),
                },
            )
            self._audit(
                actor,
                {"table": "crm_companies", "action": "company.merged"},
                keep,
                None,
                {"merged_id": gone_id, "merged_name": gone["name"]},
            )
        except Exception:
            self.rows, self.audit[audit_len:] = snapshot, []
            raise
        return {"survivor": dict(keep), "moved": moved}

    # -- store: the Twenty importer (S8) ------------------------------------

    def text_batches(self, values: Any) -> list[list[str]]:
        return _TEXT_BATCHES(values)

    def list_by_twenty_ids(
        self, workspace_id: str, table: str, twenty_ids: list[str]
    ) -> list[dict[str, Any]]:
        assert table in store.TWENTY_TABLES, table
        wanted = set(twenty_ids)
        return [r for r in self._of(table, workspace_id) if r.get("twenty_id") in wanted]

    def deals_of_companies(self, workspace_id: str, company_ids: list[str]) -> list[dict]:
        return [
            {k: r.get(k) for k in ("id", "company_id", "stage", "twenty_id")}
            for r in self._of("crm_opportunities", workspace_id)
            if r["company_id"] in company_ids and not r.get("deleted_at")
        ]

    def similar_many(
        self, workspace_id: str, kind: str, names: list[str], limit: int = 3
    ) -> list[dict[str, Any]]:
        return [
            {"idx": idx, **match}
            for idx, name in enumerate(names, start=1)
            for match in self.similar(workspace_id, kind, name, limit)
        ]

    def record_import(self, actor: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        """036 `crm_record_import`: the run and its audit row together."""
        ws = actor["workspace_id"]
        if not run.get("id"):
            row = self.add(
                "crm_import_runs",
                workspace_id=ws,
                kind=run["kind"],
                object=run.get("object"),
                file_sha256=run["file_sha256"],
                status="dry_run",
                report=json.loads(json.dumps(run.get("report") or {})),
                options=run.get("options") or {},
                created_by=actor["user_id"],
                created_at=datetime.now(UTC).isoformat(),
                applied_at=None,
            )
            status = "dry_run"
        else:
            row = self.rows["crm_import_runs"].get(run["id"])
            if row is None or row["workspace_id"] != ws:
                raise store.refusal_for("CRM04", "No such import run.")
            status = run["status"]
            row.update(
                status=status,
                report={**row["report"], "applied": run.get("report") or {}},
                options=run.get("options") or row["options"],
                applied_at=datetime.now(UTC).isoformat()
                if status == "applied"
                else row["applied_at"],
            )
        self.audit.append(
            {
                "id": len(self.audit) + 1,
                "at": _now(),
                "actor_id": actor["user_id"],
                "actor_label": actor["kind"],
                "channel": actor["channel"],
                "workspace_id": ws,
                "action": f"import.{status}",
                "target_table": "crm_import_runs",
                "target_id": row["id"],
                "before": None,
                "after": {
                    "kind": row["kind"],
                    "object": row["object"],
                    "file_sha256": row["file_sha256"],
                    "counts": (run.get("report") or {}).get("counts", {}),
                },
            }
        )
        return dict(row)

    def get_import_run(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_import_runs"].get(run_id)
        return dict(row) if row and row["workspace_id"] == workspace_id else None

    def list_import_runs(self, workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = sorted(
            self._of("crm_import_runs", workspace_id), key=lambda r: r["created_at"], reverse=True
        )
        return [
            {
                **{k: r[k] for k in r if k not in ("report", "workspace_id", "updated_at")},
                "counts": r["report"].get("counts"),
            }
            for r in rows[:limit]
        ]

    # -- store: the job log (S9, 037) ----------------------------------------

    def start_job_run(self, job: str, workspace_id: str | None, started_at: datetime) -> dict:
        return dict(
            self.add(
                "crm_job_runs",
                workspace_id=workspace_id,
                job=job,
                started_at=started_at.isoformat(),
                finished_at=None,
                outcome="running",
                counts={},
                error=None,
                error_public=None,
                alerted=False,
            )
        )

    def finish_job_run(self, run_id: str, values: dict[str, Any]) -> None:
        self.rows["crm_job_runs"][run_id].update(json.loads(json.dumps(values)))

    def _runs(self) -> list[dict[str, Any]]:
        # Newest first; a run added later is newer when two share a start.
        rows = list(self.rows["crm_job_runs"].values())
        order = {r["id"]: i for i, r in enumerate(rows)}
        return sorted(rows, key=lambda r: (r["started_at"], order[r["id"]]), reverse=True)

    def job_streak(self, job: str, workspace_id: str | None, limit: int = 200) -> list[dict]:
        return [
            {k: r[k] for k in ("id", "outcome", "alerted", "started_at")}
            for r in self._runs()
            if r["job"] == job and r["workspace_id"] == workspace_id and r["outcome"] != "running"
        ][:limit]

    def last_ok_job_run(self, job: str, workspace_id: str | None) -> str | None:
        for r in self._runs():
            if (
                r["job"] == job
                and r["workspace_id"] == workspace_id
                and r["outcome"] in ("ok", "skipped")
            ):
                return r["finished_at"]
        return None

    def mark_job_alerted(self, run_id: str) -> None:
        self.rows["crm_job_runs"][run_id]["alerted"] = True

    def list_job_runs(self, workspace_id: str, *, job: str | None = None, limit: int = 50):
        return [
            {k: v for k, v in r.items() if k != "updated_at"}
            for r in self._runs()
            if r["workspace_id"] == workspace_id and (job is None or r["job"] == job)
        ][:limit]

    def list_all_job_runs(self, *, job: str | None = None, limit: int = 100) -> list[dict]:
        return [
            {k: v for k, v in r.items() if k not in ("updated_at", "error")}
            for r in self._runs()
            if job is None or r["job"] == job
        ][:limit]

    def latest_job_runs(self) -> list[dict[str, Any]]:
        latest: dict[tuple, dict[str, Any]] = {}
        for r in reversed(self._runs()):
            latest[(r["job"], r["workspace_id"])] = r
        return [
            {k: r[k] for k in ("job", "workspace_id", "started_at", "finished_at", "outcome")}
            for r in latest.values()
        ]

    # -- store: notifications and digests (S9, 037) -------------------------

    def notify(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """crm_notify: members only, once per (person, dedupe key)."""
        added = []
        for r in rows:
            member = any(
                m["workspace_id"] == r["workspace_id"] and m["user_id"] == r["user_id"]
                for m in self.rows["crm_memberships"].values()
            )
            seen = any(
                n["user_id"] == r["user_id"] and n["dedupe_key"] == r["dedupe_key"]
                for n in self.rows["crm_notifications"].values()
            )
            if not member or seen:
                continue
            # 044: the record it opens is held to its workspace, as a link's.
            if r.get("record_id") and r.get("record_type"):
                found = self.rows[TARGETS[r["record_type"]]].get(r["record_id"])
                if not found or found["workspace_id"] != r["workspace_id"]:
                    raise store.refusal_for(
                        "CRM04", f"No such {r['record_type']} in this workspace."
                    )
            row = self.add(
                "crm_notifications",
                workspace_id=r["workspace_id"],
                user_id=r["user_id"],
                kind=r["kind"],
                record_type=r.get("record_type"),
                record_id=r.get("record_id"),
                title=r["title"][:200],
                dedupe_key=r["dedupe_key"],
                created_at=_now(),
                read_at=None,
            )
            added.append(dict(row))
        return added

    def _inbox(self, user_id: str, workspace_ids: list[str]) -> list[dict[str, Any]]:
        rows = [
            dict(n)
            for n in self.rows["crm_notifications"].values()
            if n["user_id"] == user_id and n["workspace_id"] in workspace_ids
        ]
        return sorted(rows, key=lambda n: n["created_at"], reverse=True)

    def list_notifications(
        self,
        user_id: str,
        workspace_ids: list[str],
        *,
        unread_only: bool = False,
        before: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = self._inbox(user_id, workspace_ids)
        if unread_only:
            rows = [n for n in rows if n["read_at"] is None]
        if before:
            rows = [n for n in rows if _at(n["created_at"]) < _at(before)]
        return rows[:limit]

    def unread_count(self, user_id: str, workspace_ids: list[str]) -> int:
        return sum(1 for n in self._inbox(user_id, workspace_ids) if n["read_at"] is None)

    def mark_read(
        self, user_id: str, workspace_ids: list[str], ids: list[str] | None, at: datetime
    ) -> int:
        updated = 0
        for n in self.rows["crm_notifications"].values():
            if (
                n["user_id"] == user_id
                and n["workspace_id"] in workspace_ids
                and n["read_at"] is None
                and (ids is None or n["id"] in ids)
            ):
                n["read_at"] = at.isoformat()
                updated += 1
        return updated

    def claim_digest(
        self, user_id: str, kind: str, local_date: str, workspace_id: str | None
    ) -> str | None:
        """crm_digest_claim: ON CONFLICT DO NOTHING RETURNING id."""
        if any(
            d["user_id"] == user_id
            and d["kind"] == kind
            and d["local_date"] == local_date
            and d["workspace_id"] == workspace_id
            for d in self.rows["crm_digest_sends"].values()
        ):
            return None
        return self.add(
            "crm_digest_sends",
            user_id=user_id,
            kind=kind,
            local_date=local_date,
            workspace_id=workspace_id,
            channels=[],
            sent_at=_now(),
        )["id"]

    def release_digest(self, send_id: str) -> None:
        self.rows["crm_digest_sends"].pop(send_id, None)

    def set_digest_channels(self, send_id: str, channels: list[str]) -> None:
        self.rows["crm_digest_sends"][send_id]["channels"] = list(channels)

    def list_all_memberships(self) -> list[dict[str, Any]]:
        return [
            {k: m[k] for k in ("id", "workspace_id", "user_id", "role")}
            for m in self.rows["crm_memberships"].values()
        ]

    # -- store: flags (S9, 038) ----------------------------------------------

    def flag_rows(
        self,
        workspace_id: str,
        table: str,
        *,
        ids: list[str] | None = None,
        company_ids: list[str] | None = None,
        open_or_flagged: bool = False,
        flagged_only: bool = False,
    ) -> list[dict[str, Any]]:
        assert table in store.FLAG_COLUMNS, table
        columns = [c.strip() for c in store.FLAG_COLUMNS[table].split(",")]
        out = []
        for r in self._of(table, workspace_id):
            r = {**_FLAGS, **r}
            if r.get("deleted_at"):
                continue
            if ids is not None and r["id"] not in ids:
                continue
            if company_ids is not None and r.get("company_id") not in company_ids:
                continue
            if flagged_only and r["flag_level"] == "none":
                continue
            if open_or_flagged and r["flag_level"] == "none":
                if table == "crm_opportunities" and r["stage"] not in OPEN_STAGES:
                    continue
                if table == "tasks" and r["status"] not in ("open", "waiting"):
                    continue
            out.append({c: r.get(c) for c in columns})
        return out

    def flag_facts(
        self,
        workspace_id: str,
        deal_ids: list[str] | None,
        company_ids: list[str] | None,
        person_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """039 crm_flag_facts."""
        activity: dict[str, str] = {}
        for d in self._of("crm_opportunities", workspace_id):
            if d.get("deleted_at") or (deal_ids is not None and d["id"] not in deal_ids):
                continue
            times = [d.get("created_at"), d.get("stage_entered_at")]
            for link in self.rows["crm_note_links"].values():
                note = self.rows["crm_notes"].get(link["note_id"])
                if (
                    link["record_type"] == "opportunity"
                    and link["record_id"] == d["id"]
                    and note
                    and not note.get("deleted_at")
                ):
                    on = note.get("occurred_on")
                    times.append(
                        f"{on}T00:00:00+00:00"
                        if note.get("kind", "note") != "note" and on
                        else note.get("created_at")
                    )
            for link in self.rows["task_links"].values():
                task = self.rows["tasks"].get(link["task_id"])
                if (
                    link["record_type"] == "opportunity"
                    and link["record_id"] == d["id"]
                    and task
                    and not task.get("deleted_at")
                ):
                    times += [task.get("created_at"), task.get("completed_at")]
            known = [_at(t) for t in times if t]
            if known:
                activity[d["id"]] = max(known).isoformat()
        companies = set()
        for link in self.rows["task_links"].values():
            task = self.rows["tasks"].get(link["task_id"])
            if (
                link["workspace_id"] == workspace_id
                and link["record_type"] == "company"
                and task
                and task["status"] in ("open", "waiting")
                and not task.get("deleted_at")
                and (company_ids is None or link["record_id"] in company_ids)
            ):
                companies.add(link["record_id"])
        wanted = {"opportunity": deal_ids, "company": company_ids, "person": person_ids}
        emails: dict[str, dict[str, dict[str, Any]]] = {}
        for link in self._of("crm_email_links", workspace_id):
            ids = wanted[link["record_type"]]
            msg = self.rows["crm_email_messages"].get(link["message_id"])
            if (ids is not None and link["record_id"] not in ids) or msg is None:
                continue
            if msg["is_internal"] or msg["is_auto"]:
                continue
            facts = emails.setdefault(link["record_type"], {}).setdefault(
                link["record_id"], {"in": None, "out": None}
            )
            key = "in" if msg["direction"] == "inbound" else "out"
            if facts[key] is None or _at(msg["sent_at"]) > _at(facts[key]):
                facts[key] = msg["sent_at"]
        return {"activity": activity, "open_task_companies": sorted(companies), "emails": emails}

    def set_flags(
        self, workspace_id: str, table: str, rows: list[dict[str, Any]], at: datetime
    ) -> int:
        """038 crm_set_flags: no audit row, `updated_at` untouched."""
        assert table in store.FLAG_COLUMNS, table
        changed = 0
        for r in rows:
            row = self.rows[table].get(r["id"])
            if row is None or row["workspace_id"] != workspace_id:
                continue
            flags = json.loads(json.dumps(r["flags"]))
            if (row.get("flag_level", "none"), row.get("flags") or []) != (r["flag_level"], flags):
                changed += 1
            row.update(flag_level=r["flag_level"], flags=flags, flags_computed_at=at.isoformat())
        self.flag_writes.append((table, len(rows)))
        return changed

    def flagged_owned(self, workspace_id: str, table: str, owner_id: str) -> list[dict]:
        return [
            r
            for r in self.flag_rows(workspace_id, table, flagged_only=True)
            if self.rows[table][r["id"]].get("owner_id") == owner_id
        ]

    def flag_counts(self, workspace_id: str) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for table in store.FLAG_COLUMNS:
            levels = [r["flag_level"] for r in self.flag_rows(workspace_id, table)]
            counts[table] = {lvl: levels.count(lvl) for lvl in ("red", "amber")}
        return counts

    def stage_moves_since(self, workspace_id: str, since: datetime) -> list[dict[str, Any]]:
        rows = [
            {k: h[k] for k in ("opportunity_id", "from_stage", "to_stage", "at")}
            for h in self._of("crm_stage_history", workspace_id)
            if _at(h["at"]) >= since
        ]
        return sorted(rows, key=lambda h: h["at"])

    def closed_since(self, workspace_id: str, since: str) -> list[dict[str, Any]]:
        rows = [
            {k: d.get(k) for k in ("id", "name", "stage", "amount_in_inr", "won_on", "lost_on")}
            | {"owner_id": d.get("owner_id")}
            for d in self._of("crm_opportunities", workspace_id)
            if not d.get("deleted_at")
            and (str(d.get("won_on") or "") >= since or str(d.get("lost_on") or "") >= since)
        ]
        return sorted(rows, key=lambda d: d["name"])

    def deal_amounts(self, workspace_id: str, ids: list[str]) -> dict[str, Any]:
        return {
            d["id"]: d.get("amount_in_inr")
            for d in self._of("crm_opportunities", workspace_id)
            if d["id"] in ids
        }

    def tasks_due_by(self, workspace_id: str, by: str) -> list[dict[str, Any]]:
        return [
            {k: t.get(k) for k in ("id", "title", "status", "due_on", "assignee_id")}
            for t in self._of("tasks", workspace_id)
            if t["status"] in ("open", "waiting")
            and not t.get("deleted_at")
            and t.get("due_on")
            and str(t["due_on"])[:10] <= by
        ]

    def get_job_run(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        run = self.rows["crm_job_runs"].get(run_id)
        return dict(run) if run and run["workspace_id"] == workspace_id else None

    # -- store: Gmail sync (S10, 039) ----------------------------------------

    def list_mailboxes(self, workspace_id: str, *, active_only: bool = False) -> list[dict]:
        rows = [
            dict(m)
            for m in self._of("crm_mailboxes", workspace_id)
            if not active_only or m["is_active"]
        ]
        return sorted(rows, key=lambda m: m["email"])

    def get_mailbox(self, workspace_id: str, mailbox_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_mailboxes"].get(mailbox_id)
        return dict(row) if row and row["workspace_id"] == workspace_id else None

    def find_mailbox_by_email(self, email: str) -> dict[str, Any] | None:
        return next(
            (
                {"id": m["id"], "workspace_id": m["workspace_id"]}
                for m in self.rows["crm_mailboxes"].values()
                if m["email"] == email
            ),
            None,
        )

    def mailbox_progress(self, workspace_id: str, mailbox_id: str, values: dict) -> None:
        assert set(values) <= store.MAILBOX_PROGRESS, values
        row = self.rows["crm_mailboxes"][mailbox_id]
        assert row["workspace_id"] == workspace_id
        # 039: the audit trigger skips the cursor columns, and so does this.
        row.update(json.loads(json.dumps(values)))

    def mark_candidates(self, workspace_id: str, mailbox_id: str, rows: list[dict]) -> int:
        """071 crm_capture_mark: one row per thread, its latest message
        first; a later message re-opens the decision, an older or the same
        one changes nothing; older than 365 days is not marked; another
        workspace's mailbox is refused (CRM04)."""
        mailbox = self.rows["crm_mailboxes"].get(mailbox_id)
        if mailbox is None or mailbox["workspace_id"] != workspace_id:
            raise store.refusal_for("CRM04", "No such mailbox.")
        cutoff = datetime.now(UTC) - timedelta(days=365)
        latest: dict[str, dict[str, Any]] = {}
        for r in json.loads(json.dumps(rows)):
            at = datetime.fromisoformat(r["sent_at"])
            if at < cutoff:
                continue
            seen = latest.get(r["thread_id"])
            if seen is None or (at, r["message_id"]) > (seen["at"], seen["message_id"]):
                latest[r["thread_id"]] = {**r, "at": at}
        n = 0
        for thread_id, r in sorted(latest.items()):
            row = next(
                (
                    c
                    for c in self.rows["crm_capture_candidates"].values()
                    if c["mailbox_id"] == mailbox_id and c["thread_id"] == thread_id
                ),
                None,
            )
            if row is None:
                self.add(
                    "crm_capture_candidates",
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    thread_id=thread_id,
                    latest_message_id=r["message_id"],
                    latest_message_at=r["at"].isoformat(),
                    **DEFAULTS["crm_capture_candidates"],
                )
                n += 1
            elif r["at"] > datetime.fromisoformat(row["latest_message_at"]):
                row.update(
                    latest_message_id=r["message_id"],
                    latest_message_at=r["at"].isoformat(),
                    **DEFAULTS["crm_capture_candidates"],
                    updated_at=datetime.now(UTC).isoformat(),
                )
                n += 1
        return n

    # -- store: email capture's discovery (spec 010, 072) ----------------------

    def set_capture(self, workspace: dict[str, Any], mode: str) -> dict[str, Any]:
        """The `capture` row with this mode. Not a store name: a set-up helper."""
        row = next(
            r
            for r in self.rows["crm_workspace_settings"].values()
            if r["workspace_id"] == workspace["id"] and r["key"] == "capture"
        )
        row["value"] = {"mode": mode}
        return row

    def add_dry_run(self, workspace: dict[str, Any], **values: Any) -> dict[str, Any]:
        """A dry run as 072 stores it. Not a store name: a set-up helper."""
        return self.add(
            "crm_capture_dry_runs",
            **{
                "workspace_id": workspace["id"],
                "started_at": _now(),
                "started_by": None,
                "finished_at": None,
                "cancelled": False,
                "recall": [],
                "counts": None,
                "applied_at": None,
                "applied_by": None,
                **values,
            },
        )

    def capture_setting(self, workspace_id: str) -> dict[str, Any] | None:
        row = next(
            (
                r
                for r in self.rows["crm_workspace_settings"].values()
                if r["workspace_id"] == workspace_id and r["key"] == "capture"
            ),
            None,
        )
        return json.loads(json.dumps(row["value"])) if row else None

    def pending_candidates(
        self, workspace_id: str, mailbox_ids: list[str], *, newest_first: bool, limit: int = 200
    ) -> list[dict[str, Any]]:
        rows = [
            dict(c)
            for c in self.rows["crm_capture_candidates"].values()
            if c["workspace_id"] == workspace_id
            and c["status"] == "pending"
            and c["mailbox_id"] in set(mailbox_ids)
        ]
        rows.sort(key=lambda c: c["id"])
        rows.sort(key=lambda c: _at(c["latest_message_at"]), reverse=newest_first)
        return rows[:limit]

    def record_candidate(
        self, workspace_id: str, candidate_id: str, message_id: str, values: dict[str, Any]
    ) -> bool:
        """072 crm_capture_record: only while pending on that latest message;
        071's CHECKs as the database holds them."""
        values = json.loads(json.dumps(values))
        status = values["status"]
        assert status in ("pending", "skipped", "ignored", "known", "judged", "failed"), status
        row = self.rows["crm_capture_candidates"].get(candidate_id)
        if (
            row is None
            or row["workspace_id"] != workspace_id
            or row["latest_message_id"] != message_id
            or row["status"] != "pending"
        ):
            return False
        judged = status == "judged"
        finding = (
            values.get("finding") if judged and isinstance(values.get("finding"), dict) else None
        )
        update = {
            "status": status,
            "attempts": values.get("attempts", row["attempts"]),
            "outcome": values.get("outcome") if judged else None,
            "confidence": values.get("confidence") if judged else None,
            "classifier_version": values.get("classifier_version"),
            "judged_message_id": message_id if judged else None,
            "judged_at": datetime.now(UTC).isoformat() if judged else None,
            "finding": finding,
            "dry_run": bool(values.get("dry_run", False)),
        }
        assert 0 <= update["attempts"] <= 3
        if judged:
            assert update["outcome"] in (
                "new_lead",
                "deal_signal",
                "other_business",
                "not_relevant",
            )
            assert update["confidence"] in ("high", "medium", "low")
        if finding is not None:
            assert update["outcome"] in ("new_lead", "deal_signal"), update["outcome"]
            # 071's CHECK: pg_column_size, UTF-8 bytes (not \u escapes).
            assert len(json.dumps(finding, ensure_ascii=False).encode()) <= 4096
        row.update(update, updated_at=datetime.now(UTC).isoformat())
        return True

    def _open_run(self, workspace_id: str) -> dict[str, Any] | None:
        return next(
            (
                r
                for r in self.rows["crm_capture_dry_runs"].values()
                if r["workspace_id"] == workspace_id and r["finished_at"] is None
            ),
            None,
        )

    def _write_capture(self, actor: dict[str, Any], workspace_id: str, mode: str) -> None:
        row = next(
            (
                r
                for r in self.rows["crm_workspace_settings"].values()
                if r["workspace_id"] == workspace_id and r["key"] == "capture"
            ),
            None,
        )
        if row is None:
            self.apply(
                actor,
                [
                    {
                        "op": "insert",
                        "table": "crm_workspace_settings",
                        "values": {"key": "capture", "value": {"mode": mode}},
                    }
                ],
            )
            return
        self.apply(
            actor,
            [
                {
                    "op": "update",
                    "table": "crm_workspace_settings",
                    "id": row["id"],
                    "values": {"value": {"mode": mode}},
                    "action": "setting.updated",
                }
            ],
        )

    def start_dry_run(self, actor: dict[str, Any]) -> str:
        """072 crm_capture_start_dry_run: one open run; the dry run's
        decisions back to pending; the mode Dry run, audited."""
        assert actor["kind"] == "user", actor
        ws_id = actor["workspace_id"]
        if self._open_run(ws_id) is not None:
            raise store.refusal_for("CRM09", "A dry run is already running.")
        run = self.add_dry_run({"id": ws_id}, started_by=actor["user_id"])
        for c in self.rows["crm_capture_candidates"].values():
            if c["workspace_id"] == ws_id and c["dry_run"]:
                c.update(
                    **DEFAULTS["crm_capture_candidates"], updated_at=datetime.now(UTC).isoformat()
                )
        self._write_capture(actor, ws_id, "dry_run")
        return run["id"]

    def set_capture_mode(self, actor: dict[str, Any], workspace_id: str, mode: str) -> dict:
        """072 crm_capture_set_mode: off or live (live only after an applied
        run), audited; an open run is cancelled; live returns the dry run's
        decisions to pending."""
        if actor["kind"] != "user" or actor["workspace_id"] != workspace_id:
            raise store.refusal_for("CRM04", "No such workspace.")
        if mode == "dry_run":
            raise store.refusal_for("CRM22", "Start a dry run from Email capture.")
        if mode not in ("off", "live"):
            raise store.refusal_for("CRM22", "Email capture is off, dry run or live.")
        if mode == "live" and not self.has_applied_dry_run(workspace_id):
            raise store.refusal_for("CRM22", "Run and apply a dry run first.")
        self._write_capture(actor, workspace_id, mode)
        run = self._open_run(workspace_id)
        if run is not None:
            counts = self.capture_report(workspace_id, run["id"])
            run.update(
                finished_at=datetime.now(UTC).isoformat(), cancelled=True, recall=[], counts=counts
            )
        if mode == "live":
            # 072: what a dry run judged and was never applied is judged
            # again live (after the cancelled run's counts are kept).
            for c in self.rows["crm_capture_candidates"].values():
                if c["workspace_id"] == workspace_id and c["dry_run"]:
                    c.update(
                        **DEFAULTS["crm_capture_candidates"],
                        updated_at=datetime.now(UTC).isoformat(),
                    )
        return {"mode": mode, "cancelled_run_id": run["id"] if run else None}

    def finish_dry_run(self, workspace_id: str, run_id: str, recall: list[dict]) -> bool:
        run = self.rows["crm_capture_dry_runs"].get(run_id)
        if (
            run is None
            or run["workspace_id"] != workspace_id
            or run["finished_at"] is not None
            or run["cancelled"]
        ):
            return False
        recall = json.loads(json.dumps(recall))
        assert isinstance(recall, list) and len(json.dumps(recall)) <= 262144
        counts = self.capture_report(workspace_id, run_id)
        run.update(finished_at=datetime.now(UTC).isoformat(), recall=recall, counts=counts)
        return True

    def add_recall(self, workspace_id: str, run_id: str, rows: list[dict]) -> bool:
        """072 crm_capture_recall_add: appended to the open run, a thread
        already there kept as it was."""
        run = self.rows["crm_capture_dry_runs"].get(run_id)
        if (
            run is None
            or run["workspace_id"] != workspace_id
            or run["finished_at"] is not None
            or run["cancelled"]
        ):
            return False
        rows = json.loads(json.dumps(rows))
        assert isinstance(rows, list)
        have = {(r["mailbox_id"], r["thread_id"]) for r in run["recall"]}
        run["recall"] = [
            *run["recall"],
            *(r for r in rows if (r["mailbox_id"], r["thread_id"]) not in have),
        ]
        assert len(json.dumps(run["recall"])) <= 262144
        return True

    def capture_report(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        """072 crm_capture_report: counts only."""
        run = self.rows["crm_capture_dry_runs"].get(run_id)
        if run is None or run["workspace_id"] != workspace_id:
            raise store.refusal_for("CRM04", "No such dry run.")
        if run.get("counts") is not None:
            # A closed run answers with the counts it stored then.
            return json.loads(json.dumps(run["counts"]))
        since = {
            m["id"]: _midnight(
                date.fromisoformat(str(m["capture_start_date"])[:10]), ZoneInfo("Asia/Kolkata")
            )
            for m in self._of("crm_mailboxes", workspace_id)
            if m["capture_source"] == "primary_sent"
        }
        cands = [
            c
            for c in self.rows["crm_capture_candidates"].values()
            if c["workspace_id"] == workspace_id
            and c["mailbox_id"] in since
            and _at(c["latest_message_at"]) >= since[c["mailbox_id"]]
        ]

        def n(status: str) -> int:
            return sum(1 for c in cands if c["status"] == status)

        threads = {
            (e["mailbox_id"], e["thread_id"])
            for e in self.rows["crm_email_messages"].values()
            if e["workspace_id"] == workspace_id
            and e["mailbox_id"] in since
            and _at(e["sent_at"]) >= since[e["mailbox_id"]]
        }
        return {
            "considered": len(cands),
            "pending": n("pending"),
            "skipped_rules": n("skipped"),
            "ignored": n("ignored"),
            "known": n("known"),
            "judged": n("judged"),
            "failed": n("failed"),
            "filed_known_threads": len(threads),
            "by_outcome": {
                outcome: {
                    conf: sum(
                        1
                        for c in cands
                        if c["status"] == "judged"
                        and c["outcome"] == outcome
                        and c["confidence"] == conf
                    )
                    for conf in ("high", "medium", "low")
                }
                for outcome in ("new_lead", "deal_signal", "other_business", "not_relevant")
            },
        }

    def latest_dry_run(self, workspace_id: str) -> dict[str, Any] | None:
        rows = [
            r
            for r in self.rows["crm_capture_dry_runs"].values()
            if r["workspace_id"] == workspace_id
        ]
        order = {r["id"]: i for i, r in enumerate(rows)}
        rows.sort(key=lambda r: (_at(r["started_at"]), order[r["id"]]))
        return json.loads(json.dumps(rows[-1])) if rows else None

    def has_applied_dry_run(self, workspace_id: str) -> bool:
        return any(
            r["workspace_id"] == workspace_id and r["applied_at"]
            for r in self.rows["crm_capture_dry_runs"].values()
        )

    def dry_run_findings(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = [
            json.loads(json.dumps(c))
            for c in self.rows["crm_capture_candidates"].values()
            if c["workspace_id"] == workspace_id
            and c["dry_run"]
            and c["status"] == "judged"
            and c["outcome"] in ("new_lead", "deal_signal")
        ]
        return sorted(rows, key=lambda c: (_at(c["latest_message_at"]), c["id"]))

    def thread_subjects(self, workspace_id: str, mailbox_id: str, thread_ids: list) -> dict:
        found: dict[str, tuple[str, str]] = {}
        for e in self.rows["crm_email_messages"].values():
            if (
                e["workspace_id"] == workspace_id
                and e["mailbox_id"] == mailbox_id
                and e["thread_id"] in set(thread_ids)
                and e.get("subject")
            ):
                seen = found.get(e["thread_id"])
                if seen is None or str(e["sent_at"]) > seen[0]:
                    found[e["thread_id"]] = (str(e["sent_at"]), e["subject"])
        return {t: subject for t, (_, subject) in found.items()}

    # -- store: email capture, live (spec 010, 073) ---------------------------

    def get_dry_run(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        run = self.rows["crm_capture_dry_runs"].get(run_id)
        if run is None or run["workspace_id"] != workspace_id:
            return None
        return json.loads(json.dumps(run))

    def capture_apply_routed(
        self,
        actor: dict[str, Any],
        run_id: str,
        routed: dict[str, bool],
        counts: dict[str, int],
    ) -> bool:
        """074 crm_capture_apply_routed: refused as crm_capture_apply
        refuses; each routed Academy candidate of the run's (still dry-run,
        judged) gains `routed` and a null `duplicate_company_id`; the run's
        stored counts (or the report as it stands) gain the Academy counts."""
        assert actor["kind"] == "user", actor
        ws_id = actor["workspace_id"]
        routed = json.loads(json.dumps(routed))
        counts = json.loads(json.dumps(counts))
        assert isinstance(routed, dict) and all(isinstance(v, bool) for v in routed.values())
        assert set(counts) == {"academy", "academy_unrouted"}
        assert all(isinstance(v, int) and v >= 0 for v in counts.values())
        run = self.rows["crm_capture_dry_runs"].get(run_id)
        if run is None or run["workspace_id"] != ws_id:
            raise store.refusal_for("CRM04", "No such dry run.")
        latest = self.latest_dry_run(ws_id)
        if (
            not run.get("finished_at")
            or run["cancelled"]
            or run.get("applied_at")
            or (latest or {}).get("id") != run_id
        ):
            raise store.refusal_for("CRM22", "This dry run cannot be applied.")
        for c in self.rows["crm_capture_candidates"].values():
            if (
                c["workspace_id"] == ws_id
                and c["id"] in routed
                and c["dry_run"]
                and c["status"] == "judged"
                and (c.get("finding") or {}).get("business") == "academy"
            ):
                c["finding"] = {
                    **c["finding"],
                    "routed": routed[c["id"]],
                    "duplicate_company_id": None,
                }
                c["updated_at"] = datetime.now(UTC).isoformat()
        run["counts"] = {**self.capture_report(ws_id, run_id), **counts}
        return True

    def apply_dry_run(self, actor: dict[str, Any], run_id: str) -> dict[str, Any]:
        """073 crm_capture_apply: the latest run only, finished, not
        cancelled or applied; its candidates live; the mode Live, audited.
        The 7-day window is left to the API's own clock, which refuses
        first (contracts §2.3), so a fixed test clock is not compared with
        the wall clock here; tests/db holds the database's window."""
        assert actor["kind"] == "user", actor
        ws_id = actor["workspace_id"]
        run = self.rows["crm_capture_dry_runs"].get(run_id)
        if run is None or run["workspace_id"] != ws_id:
            raise store.refusal_for("CRM04", "No such dry run.")
        latest = self.latest_dry_run(ws_id)
        finished = _at(run["finished_at"]) if run.get("finished_at") else None
        if (
            finished is None
            or run["cancelled"]
            or run.get("applied_at")
            or (latest or {}).get("id") != run_id
        ):
            raise store.refusal_for("CRM22", "This dry run cannot be applied.")
        applied = datetime.now(UTC).isoformat()
        run.update(applied_at=applied, applied_by=actor["user_id"])
        for c in self.rows["crm_capture_candidates"].values():
            if c["workspace_id"] == ws_id and c["dry_run"]:
                c.update(dry_run=False, updated_at=datetime.now(UTC).isoformat())
        self._write_capture(actor, ws_id, "live")
        return {"applied_at": applied}

    def purge_capture(self, workspace_id: str, before: datetime) -> int:
        """073 crm_capture_purge: every decided candidate updated before."""
        gone = [
            k
            for k, c in self.rows["crm_capture_candidates"].items()
            if c["workspace_id"] == workspace_id
            and c["status"] != "pending"
            and _at(c["updated_at"]) < before
        ]
        for k in gone:
            del self.rows["crm_capture_candidates"][k]
        return len(gone)

    def capture_supersede(self, workspace_id: str, source_key: str, keep: str) -> int:
        """073 crm_capture_supersede: the conversation's other open capture
        items (new leads and discovered deal signals; a deal document's is
        the sync's) expire, one review.expired audit row each, as the sync."""
        actor = {"user_id": None, "kind": "sync", "channel": None, "workspace_id": workspace_id}
        gone = [
            r
            for r in self._of("crm_review_items", workspace_id)
            if r["status"] == "open"
            and (
                r["type"] == "new_lead"
                or (
                    r["type"] == "deal_signal"
                    and ((r.get("suggestion") or {}).get("evidence") or {}).get("origin")
                    == "discovery"
                )
            )
            and r["source_kind"] == "email_thread"
            and r["source_key"] == source_key
            and r["dedupe_key"] != keep
        ]
        for r in gone:
            self.rows["crm_review_items"][r["id"]].update(status="expired", decided_at=_now())
            self._review_audit(actor, "review.expired", r["id"], "open", "expired")
        return len(gone)

    def list_ignores(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = [json.loads(json.dumps(r)) for r in self._of("crm_capture_ignores", workspace_id)]
        return sorted(rows, key=lambda r: (str(r["created_at"]), r["id"]))

    def stored_message_ids(self, workspace_id: str, mailbox_id: str, gmail_ids: list) -> set:
        return {
            m["gmail_message_id"]
            for m in self._of("crm_email_messages", workspace_id)
            if m["mailbox_id"] == mailbox_id and m["gmail_message_id"] in gmail_ids
        }

    def store_emails(self, workspace_id: str, mailbox_id: str, messages: list[dict]) -> list:
        """039 crm_store_emails: each message once; a link outside the
        workspace refuses the page (crm_check_refs, CRM04)."""
        mailbox = self.rows["crm_mailboxes"].get(mailbox_id)
        if mailbox is None or mailbox["workspace_id"] != workspace_id:
            raise store.refusal_for("CRM04", "No such mailbox.")
        snapshot = {
            t: {k: dict(v) for k, v in self.rows[t].items()}
            for t in ("crm_email_messages", "crm_email_links")
        }
        stored = []
        try:
            for m in json.loads(json.dumps(messages)):
                if any(
                    r["mailbox_id"] == mailbox_id and r["gmail_message_id"] == m["gmail_message_id"]
                    for r in self.rows["crm_email_messages"].values()
                ):
                    continue
                links = m.pop("links", [])
                msg = self.add(
                    "crm_email_messages",
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    created_at=_now(),
                    **m,
                )
                for link in links:
                    target = self.rows[TARGETS[link["record_type"]]].get(link["record_id"])
                    if target is None or target["workspace_id"] != workspace_id:
                        raise store.refusal_for(
                            "CRM04", f"No such {link['record_type']} in this workspace."
                        )
                    if not any(
                        r["message_id"] == msg["id"]
                        and r["record_type"] == link["record_type"]
                        and r["record_id"] == link["record_id"]
                        for r in self.rows["crm_email_links"].values()
                    ):
                        self.add(
                            "crm_email_links",
                            workspace_id=workspace_id,
                            message_id=msg["id"],
                            created_at=_now(),
                            **link,
                        )
                stored.append(m["gmail_message_id"])
        except Exception:
            self.rows.update(snapshot)
            raise
        return stored

    def _forget_message(self, message_id: str) -> None:
        del self.rows["crm_email_messages"][message_id]
        for link in list(self.rows["crm_email_links"].values()):
            if link["message_id"] == message_id:
                del self.rows["crm_email_links"][link["id"]]

    def remove_emails(self, workspace_id: str, mailbox_id: str, gmail_ids: list) -> dict:
        gone = [
            m
            for m in self._of("crm_email_messages", workspace_id)
            if m["mailbox_id"] == mailbox_id and m["gmail_message_id"] in gmail_ids
        ]
        records = {
            (link["record_type"], link["record_id"])
            for link in self.rows["crm_email_links"].values()
            if link["message_id"] in {m["id"] for m in gone}
        }
        for m in gone:
            self._forget_message(m["id"])
        # 062: a thread left with no stored message loses its summary; 070:
        # so does a summary whose watermark message was removed.
        left = {
            m["thread_id"]
            for m in self._of("crm_email_messages", workspace_id)
            if m["mailbox_id"] == mailbox_id
        }
        for summary in list(self.rows["crm_email_summaries"].values()):
            if (
                summary["mailbox_id"] == mailbox_id
                and summary["thread_id"] in {m["thread_id"] for m in gone}
                and (
                    summary["thread_id"] not in left
                    or summary.get("watermark_message_id") in gmail_ids
                )
            ):
                self._forget_summary(summary["id"])
        return {
            "removed": len(gone),
            "records": [{"record_type": t, "record_id": r} for t, r in sorted(records)],
        }

    def people_by_emails(self, workspace_id: str, emails: list[str]) -> list[dict[str, Any]]:
        wanted = set(emails)
        return [
            dict(p)
            for p in self._of("crm_people", workspace_id)
            if p.get("primary_email") in wanted or wanted & set(p.get("additional_emails") or [])
        ]

    def email_links(self, workspace_id: str, record_type: str, record_ids: list) -> list:
        return [
            {k: link[k] for k in ("message_id", "record_type", "record_id")}
            for link in self._of("crm_email_links", workspace_id)
            if link["record_type"] == record_type and link["record_id"] in record_ids
        ]

    def mailbox_message_ids(self, workspace_id: str, mailbox_id: str, sent_after: str) -> set:
        return {
            m["gmail_message_id"]
            for m in self._of("crm_email_messages", workspace_id)
            if m["mailbox_id"] == mailbox_id and _at(m["sent_at"]) > _at(sent_after)
        }

    def email_messages(self, workspace_id: str, message_ids: list) -> list[dict[str, Any]]:
        return [
            dict(m) for m in self._of("crm_email_messages", workspace_id) if m["id"] in message_ids
        ]

    # -- store: writes ------------------------------------------------------

    # -- store: campaigns (S11a) ----------------------------------------------

    def campaign_members(
        self, workspace_id: str, *, column: str, ids: list[str], deleted: bool | None = False
    ) -> list[dict[str, Any]]:
        if column not in store.MEMBER_KEYS:
            raise ValueError(f"members are not read by {column}")
        wanted = set(ids)
        rows = [
            r
            for r in self._of("crm_campaign_members", workspace_id)
            if r.get(column) in wanted and (deleted is not False or not r.get("deleted_at"))
        ]
        return sorted(
            rows, key=lambda r: (r["campaign_id"], float(r["position"]), r["created_at"], r["id"])
        )

    # -- store: dashboard, CSV import and export (S11b) ---------------------

    def _columns(self, rows: list[dict[str, Any]], columns: str) -> list[dict[str, Any]]:
        names = [c.strip() for c in columns.split(",")]
        return [{c: r.get(c) for c in names} for r in rows]

    def every_record(self, workspace_id: str, table: str) -> list[dict[str, Any]]:
        live = [r for r in self._of(table, workspace_id) if not r.get("deleted_at")]
        return sorted(live, key=lambda r: r["id"])

    def dashboard_deals(self, workspace_id: str) -> list[dict[str, Any]]:
        live = [r for r in self._of("crm_opportunities", workspace_id) if not r.get("deleted_at")]
        return self._columns(live, store.DASHBOARD_DEAL_COLUMNS)

    def dashboard_companies(self, workspace_id: str) -> list[dict[str, Any]]:
        live = [
            r
            for r in self._of("crm_companies", workspace_id)
            if not r.get("deleted_at") and not r.get("merged_into_id")
        ]
        return self._columns(live, store.DASHBOARD_COMPANY_COLUMNS)

    def stage_history(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._of("crm_stage_history", workspace_id)

    def _between(self, table: str, workspace_id: str, column: str, since: str, until: str):
        return [
            r
            for r in self._of(table, workspace_id)
            if not r.get("deleted_at")
            and r.get(column)
            and _at(since if "T" in since else since + "T00:00:00+00:00")
            <= _at(r[column])
            < _at(until if "T" in until else until + "T00:00:00+00:00")
        ]

    def notes_between(self, workspace_id: str, since: str, until: str) -> list[dict[str, Any]]:
        return self._between("crm_notes", workspace_id, "created_at", since, until)

    def tasks_completed_between(self, workspace_id: str, since: str, until: str) -> list[dict]:
        return self._between("tasks", workspace_id, "completed_at", since, until)

    def emails_between(self, workspace_id: str, since: str, until: str) -> list[dict[str, Any]]:
        return self._between("crm_email_messages", workspace_id, "sent_at", since, until)

    def list_targets(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._of("crm_bookings_targets", workspace_id)
        return sorted(rows, key=lambda t: (t["period_start"], t["period_kind"]), reverse=True)

    def list_csv_mappings(self, workspace_id: str, *, object: str | None = None) -> list[dict]:
        rows = self._of("crm_csv_mappings", workspace_id)
        if object is not None:
            rows = [r for r in rows if r["object"] == object]
        return sorted(rows, key=lambda r: r["name"])

    def get_csv_mapping(self, workspace_id: str, mapping_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_csv_mappings"].get(mapping_id)
        return dict(row) if row and row["workspace_id"] == workspace_id else None

    def record_names(self, workspace_id: str, table: str) -> list[dict[str, Any]]:
        assert table in store.NAME_COLUMNS, table
        live = [r for r in self._of(table, workspace_id) if not r.get("deleted_at")]
        return self._columns(live, store.NAME_COLUMNS[table])

    def record_export(self, actor: dict[str, Any], detail: dict[str, Any]) -> None:
        """043 `crm_record_export`: the export's audit row, nothing else."""
        ws = actor["workspace_id"]
        if actor.get("kind") != "user" or detail.get("kind") not in ("view", "workspace"):
            raise store.refusal_for("CRM22", "An export is of a view or a workspace.")
        if not any(
            m["workspace_id"] == ws and m["user_id"] == actor["user_id"]
            for m in self.rows["crm_memberships"].values()
        ):
            raise store.refusal_for("CRM04", "No such workspace.")
        table = detail.get("table") or "crm_workspaces"
        self.audit.append(
            {
                "id": len(self.audit) + 1,
                "at": _now(),
                "actor_id": actor["user_id"],
                "actor_label": "user",
                "channel": actor["channel"],
                "workspace_id": ws,
                "action": f"export.{detail['kind']}",
                "target_table": table,
                "target_id": ws if table == "crm_workspaces" else None,
                "before": None,
                "after": {k: v for k, v in detail.items() if k != "table"},
            }
        )

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        raise AssertionError(f"FakeCrmStore has no fake for {fn}; call a named store function.")

    def apply(self, actor: dict[str, Any], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """crm_apply (data-model §5.2): all ops or none."""
        self.applied.append((actor, ops))
        snapshot = {t: {k: dict(v) for k, v in rows.items()} for t, rows in self.rows.items()}
        audit_len = len(self.audit)
        refs: dict[str, str] = {}
        try:
            done = []
            for op in ops:
                result = self._one(actor, op, refs)
                if op.get("ref"):
                    refs[op["ref"]] = result["id"]
                done.append(result)
            # FR-CRM-TASK-01, checked after every op as crm_apply does; 048:
            # `p_task_needs_link` (always on from the CRM store), counting
            # CRM links only.
            for op, result in zip(ops, done, strict=True):
                if (
                    op["op"] == "insert"
                    and op["table"] == "tasks"
                    and not self._crm_links(result["id"])
                ):
                    raise store.refusal_for(
                        "CRM22", "A task must be linked to at least one record."
                    )
            for op, result in zip(ops, done, strict=True):
                row = self.rows.get(op["table"], {}).get(result["id"])
                if row is not None:
                    self._check_rows(op["table"], row)
                    result["row"] = dict(row)
            self.workspace_check(touched_tasks(ops, done))
            return done
        except Exception:
            self.rows, self.audit[audit_len:] = snapshot, []
            raise

    def workspace_check(self, task_ids: set[str]) -> None:
        """047's deferred `tasks_workspace_check`, at the end of the call: a
        live task has a workspace exactly when it has a CRM link, the same
        as every CRM link's, and links a project only in a TVA-linked
        workspace (FR-TASK-DATA-05)."""
        for task_id in sorted(task_ids):
            task = self.rows["tasks"].get(task_id)
            if task is None or task.get("deleted_at"):
                continue
            links = [lk for lk in self.rows["task_links"].values() if lk["task_id"] == task_id]
            crm_ws = {lk["workspace_id"] for lk in links if lk["record_type"] != "tva_project"}
            if not crm_ws and task.get("workspace_id") is not None:
                raise store.refusal_for(
                    "CRM22", "A CRM task needs at least one linked record; delete it instead."
                )
            if crm_ws and crm_ws != {task.get("workspace_id")}:
                raise store.refusal_for("CRM22", "A task cannot link records of two workspaces.")
            ws = self.rows["crm_workspaces"].get(task.get("workspace_id") or "")
            if (
                any(lk["record_type"] == "tva_project" for lk in links)
                and ws is not None
                and not ws.get("tva_linked")
            ):
                raise store.refusal_for("CRM22", "Only Nunnari Labs tasks can link a TVA project.")

    def _one(self, actor: dict[str, Any], op: dict[str, Any], refs: dict[str, str]) -> dict:
        ws = actor["workspace_id"]
        crm = op["table"].startswith("crm_") or op["table"] in ("tasks", "task_links")
        table = self.rows.get(op["table"]) if crm else None
        if table is None:
            raise store.refusal_for("CRM22", "That is not a CRM table.")
        values = {
            k: refs[v[5:]] if isinstance(v, str) and v.startswith("$ref:") else v
            for k, v in (op.get("values") or {}).items()
        }
        for k in ("id", "workspace_id", "created_at", "created_by"):
            if k in values:
                raise store.refusal_for("CRM22", f"{k} cannot be set here.")
        if op["table"] == "crm_opportunities" and op["op"] == "update" and "stage" in values:
            raise store.refusal_for("CRM22", "Use a stage move.")
        if op["op"] == "upsert":
            if not values.get("twenty_id"):
                raise store.refusal_for("CRM22", "An upsert needs twenty_id.")
            if op["table"] == "crm_opportunities" and "stage" in values:
                raise store.refusal_for("CRM22", "Use a stage move.")
            found = next(
                (
                    r
                    for r in table.values()
                    if r["workspace_id"] == ws and r.get("twenty_id") == values["twenty_id"]
                ),
                None,
            )
            if found is not None:
                # 027: an unchanged row is not rewritten, so no audit row.
                changed = {
                    k: v
                    for k, v in values.items()
                    if k not in ("twenty_id", "updated_by") and found.get(k) != v
                }
                if changed:
                    self._update(actor, op, found, changed)
                return {"ref": op.get("ref"), "id": found["id"], "row": dict(found)}
        if op["op"] in ("insert", "upsert"):
            row = {
                "id": str(uuid.uuid4()),
                "workspace_id": ws,
                "created_at": _now(),
                "created_by": actor["user_id"],
                "updated_at": _now(),
                "updated_by": actor["user_id"],
                **DEFAULTS.get(op["table"], {}),
                **values,
            }
            if op["table"] == "crm_opportunities":
                row.setdefault("stage_entered_at", row["created_at"])
            # 066: DEFAULT now().
            if op["table"] == "crm_campaign_members":
                row.setdefault("status_changed_at", row["created_at"])
            self._check_refs(op["table"], row)
            self._check_unique(op["table"], row)
            table[row["id"]] = row
            self._audit(actor, {**op, "op": "insert"}, row, None, dict(row))
            # 064's triggers: a new company or domain may queue enrichment.
            if op["table"] == "crm_companies":
                self._queue_enrichment(ws, row["id"])
            elif op["table"] == "crm_company_domains":
                self._queue_enrichment(ws, row["company_id"])
            if op["table"] == "crm_opportunities" and op["op"] == "insert":
                self.add(
                    "crm_stage_history",
                    workspace_id=ws,
                    opportunity_id=row["id"],
                    from_stage=None,
                    to_stage=row["stage"],
                    at=row["stage_entered_at"],
                    by=actor["user_id"],
                    actor_kind=actor["kind"],
                )
            return {"ref": op.get("ref"), "id": row["id"], "row": dict(row)}
        if op["op"] not in ("update", "soft_delete", "restore", "delete"):
            raise AssertionError(f"FakeCrmStore does not fake op {op['op']!r} yet.")
        scope = "id" if op["table"] == "crm_workspaces" else "workspace_id"
        row = table.get(op.get("id"))
        if row is None or row[scope] != ws:
            raise store.refusal_for("CRM04", "No such record in this workspace.")
        if op.get("expect_updated_at") and _at(op["expect_updated_at"]) != _at(row["updated_at"]):
            raise store.refusal_for(
                "CRM09", "This record changed while you were editing it; reload and try again."
            )
        if op["op"] == "delete":
            self._delete(actor, op["table"], row, op.get("action"))
            self._cascade(actor, op["table"], row["id"])
            return {"ref": op.get("ref"), "id": row["id"], "row": dict(row)}
        if op["op"] in ("soft_delete", "restore"):
            if op["table"] not in RECORDS:
                raise store.refusal_for("CRM22", "This kind of record cannot be deleted.")
            deleting = op["op"] == "soft_delete"
            values = {
                "deleted_at": _now() if deleting else None,
                "deleted_by": actor["user_id"] if deleting else None,
                "deleted_group": op.get("deleted_group") if deleting else None,
            }
        elif not values:
            raise store.refusal_for("CRM22", "Nothing to change.")
        if (
            op["table"] == "crm_memberships"
            and row["role"] == "admin"
            and values.get("role", "admin") != "admin"
            and not any(
                m["role"] == "admin" and m["workspace_id"] == ws and m["id"] != row["id"]
                for m in table.values()
            )
        ):
            raise store.refusal_for("CRM22", "A workspace needs at least one admin.")
        self._update(actor, op, row, values)
        return {"ref": op.get("ref"), "id": row["id"], "row": dict(row)}

    def _update(self, actor, op, row, values) -> None:
        candidate = {**row, **values}
        self._check_refs(op["table"], candidate)
        self._check_unique(op["table"], candidate)
        changed = {k for k, v in values.items() if row.get(k) != v} - {"updated_at", "updated_by"}
        before = {k: row.get(k) for k in changed}
        # 066's crm_campaign_members_status_changed: only a status change
        # moves it, on whichever path made it.
        if op["table"] == "crm_campaign_members" and "status" in changed:
            values = {**values, "status_changed_at": _now()}
            changed.add("status_changed_at")
        row.update(values, updated_at=_now())
        if "updated_by" not in values:
            row["updated_by"] = actor["user_id"]
        # 026: a change to nothing but bookkeeping writes no audit row.
        if changed and not changed <= {"position"}:
            self._audit(actor, op, row, before, {k: row.get(k) for k in changed})
        if op["table"] == "crm_opportunities" and "company_id" in changed:
            self._project_follows_company(actor, row)
        # 064's triggers fire on an update of these columns, changed or not.
        if op["table"] == "crm_companies" and "website" in values:
            self._queue_enrichment(row["workspace_id"], row["id"])
        if op["table"] == "crm_company_domains" and {"is_primary", "domain"} & set(values):
            self._queue_enrichment(row["workspace_id"], row["company_id"])

    def _project_follows_company(self, actor, opp: dict[str, Any]) -> None:
        """033's `crm_opportunities_project_company` trigger: the deal's
        project moves to its new company, with a TVA audit row."""
        company = self.rows["crm_companies"].get(opp["company_id"])
        for project in self.rows["projects"].values():
            if project.get("source_opportunity_id") != opp["id"]:
                continue
            before = {"crm_company_id": project.get("crm_company_id"), "client": project["client"]}
            project.update(crm_company_id=opp["company_id"], client=company and company["name"])
            self._tva_audit(
                actor,
                "project.updated",
                "projects",
                project["id"],
                before,
                {"crm_company_id": opp["company_id"], "client": project["client"]},
            )

    def _delete(self, actor, table: str, row: dict[str, Any], action: str | None = None) -> None:
        del self.rows[table][row["id"]]
        self._audit(actor, {"table": table, "op": "delete", "action": action}, row, dict(row), None)

    def _check_refs(self, table: str, row: dict[str, Any]) -> None:
        """The composite foreign keys (23503) and crm_check_refs (CRM04)."""
        ws = row.get("workspace_id")
        if table == "crm_people" and row.get("company_id"):
            company = self.rows["crm_companies"].get(row["company_id"])
            if company is None or company["workspace_id"] != ws:
                raise store.refusal_for("23503", "violates foreign key constraint")
        if table == "crm_company_domains":
            company = self.rows["crm_companies"].get(row["company_id"])
            if company is None or company["workspace_id"] != ws:
                raise store.refusal_for("23503", "violates foreign key constraint")
        if table == "task_links" and row["record_type"] == "tva_project":
            # 047: no workspace; the plain task and project foreign keys.
            if (
                row.get("workspace_id") is not None
                or row.get("project_id") != row["record_id"]
                or row["record_id"] not in self.rows["projects"]
                or row["task_id"] not in self.rows["tasks"]
            ):
                raise store.refusal_for("23503", "violates foreign key constraint")
            return
        if table in ("crm_drive_links", "task_links", "crm_note_links"):
            target = TARGETS.get(row["record_type"])
            found = target and self.rows[target].get(row["record_id"])
            if not found or found["workspace_id"] != ws:
                raise store.refusal_for("CRM04", f"No such {row['record_type']} in this workspace.")
        if table == "crm_favourites":
            rtype = row.get("record_type") or "view"
            target = TARGETS.get(rtype) if row["target_type"] == "record" else "crm_saved_views"
            found = target and self.rows[target].get(row["target_id"])
            if not found or found["workspace_id"] != ws:
                raise store.refusal_for("CRM04", f"No such {rtype} in this workspace.")
        composite = {
            "crm_opportunities": (
                ("company_id", "crm_companies"),
                ("point_of_contact_id", "crm_people"),
            ),
            "crm_opportunity_contacts": (
                ("opportunity_id", "crm_opportunities"),
                ("person_id", "crm_people"),
            ),
            "task_links": (("task_id", "tasks"),),
            "crm_note_links": (("note_id", "crm_notes"),),
            "crm_note_mentions": (("note_id", "crm_notes"),),
            # 041.
            "crm_campaign_members": (
                ("campaign_id", "crm_campaigns"),
                ("company_id", "crm_companies"),
                ("person_id", "crm_people"),
                ("converted_opportunity_id", "crm_opportunities"),
            ),
        }
        if table == "crm_opportunities" and row.get("source_campaign_id"):
            found = self.rows["crm_campaigns"].get(row["source_campaign_id"])
            if found is None or found["workspace_id"] != ws:
                raise store.refusal_for("23503", "violates foreign key constraint")
        if table == "crm_campaigns":
            for column in ("type_id", "status_id", "channel_id"):
                option = self.rows["crm_select_options"].get(row.get(column) or "")
                if row.get(column) and (option is None or option["workspace_id"] != ws):
                    raise store.refusal_for("23503", "violates foreign key constraint")
        for column, parent in composite.get(table, ()):
            if row.get(column):
                found = self.rows[parent].get(row[column])
                if found is None or found["workspace_id"] != ws:
                    raise store.refusal_for("23503", "violates foreign key constraint")
        for column in ("channel_ids",):
            for option_id in row.get(column) or []:
                option = self.rows["crm_select_options"].get(option_id)
                if option is None or option["workspace_id"] != ws:
                    raise store.refusal_for("CRM04", "No such option in this workspace.")

    def _check_rows(self, table: str, row: dict[str, Any]) -> None:
        """030's CHECK constraints on deals, tasks and notes; 073's on the
        ignore list."""

        def broken(name: str) -> None:
            raise CheckViolation(f"{table} violates {name}: {row}")

        if table == "crm_capture_ignores":
            value = row["value"]
            if row["kind"] not in ("address", "domain"):
                broken("crm_capture_ignores_kind_check")
            if not (3 <= len(value) <= 320) or value != value.lower():
                broken("crm_capture_ignores_value_check")
            if row["kind"] == "domain" and "@" in value:
                broken("crm_capture_ignores_domain")
            if row["kind"] == "address" and value.find("@") < 1:
                broken("crm_capture_ignores_address")
            item = row.get("from_item_id")
            if (
                item
                and (self.rows["crm_review_items"].get(item) or {}).get("workspace_id")
                != row["workspace_id"]
            ):
                raise store.refusal_for("23503", "crm_capture_ignores_item")

        if table == "crm_opportunities":
            live = row.get("deleted_at") is None
            if (
                live
                and row["stage"] in OPEN_STAGES
                and not ((row.get("next_step") or "").strip() and row.get("next_step_on"))
            ):
                broken("crm_opportunities_open_next_step")
            if row["stage"] == "closed_lost" and not row.get("lost_reason_id"):
                broken("crm_opportunities_lost_reason_required")
            if (
                live
                and row["stage"] in ("proposal", "negotiation", "closed_won")
                and not row.get("twenty_id")
                and (row.get("amount") is None or row.get("expected_close_on") is None)
            ):
                broken("crm_opportunities_proposal_fields")
            amount = row.get("amount")
            if (amount is None) != (row.get("currency") is None):
                broken("crm_opportunities_amount_currency")
            if (amount is None) != (row.get("amount_in_inr") is None):
                broken("crm_opportunities_amount_in_inr")
            if (amount is None) != (row.get("fx_rate_used") is None):
                broken("crm_opportunities_fx_rate")
            if amount is not None:
                exact = (Decimal(str(amount)) * Decimal(str(row["fx_rate_used"]))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if Decimal(str(row["amount_in_inr"])) != exact:
                    broken("crm_opportunities_fx_inr")
                if (row["currency"] == "INR") != (Decimal(str(row["fx_rate_used"])) == 1):
                    broken("crm_opportunities_fx_inr_at_one")
            if not 0 <= int(row.get("probability", -1)) <= 100:
                broken("probability")
        if table == "crm_saved_views":
            if (row["visibility"] == "private") != (row.get("owner_id") is not None):
                broken("crm_saved_views_owner")
            if row.get("is_default") and row["visibility"] != "shared":
                broken("crm_saved_views_default_shared")
            if row.get("hidden") and not row.get("system_key"):
                broken("crm_saved_views_hidden_seeded")
        if table == "tasks":
            if (row["status"] == "done") != (row.get("completed_at") is not None):
                broken("tasks_completed")
            # 047.
            if row["status"] not in ("open", "waiting", "done", "cancelled"):
                broken("tasks_status")
            if row["status"] in ("open", "waiting") and not row.get("bucket"):
                broken("tasks_bucket_when_open")
            if row["status"] == "waiting" and not (row.get("waiting_on_text") or "").strip():
                broken("tasks_waiting_on")
            tags = row.get("tags") or []
            if len(tags) > 10 or any(not 1 <= len(str(t).strip()) <= 40 for t in tags):
                broken("tasks_tags")
            if row.get("workspace_id") is None and row.get("category_id"):
                broken("tasks_category_crm_only")
        if table == "crm_campaigns":
            budget = row.get("budget")
            for column in ("budget_currency", "budget_in_inr", "budget_fx_rate"):
                if (budget is None) != (row.get(column) is None):
                    broken(f"crm_campaigns_{column}")
            if budget is not None:
                exact = (Decimal(str(budget)) * Decimal(str(row["budget_fx_rate"]))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if Decimal(str(row["budget_in_inr"])) != exact:
                    broken("crm_campaigns_budget_fx_inr")
                if (row["budget_currency"] == "INR") != (Decimal(str(row["budget_fx_rate"])) == 1):
                    broken("crm_campaigns_budget_inr_at_one")
            if (
                row.get("starts_on")
                and row.get("ends_on")
                and str(row["ends_on"]) < str(row["starts_on"])
            ):
                broken("crm_campaigns_dates")
        if table == "crm_campaign_members":
            if not (row.get("company_id") or row.get("person_id")):
                broken("crm_campaign_members_someone")
            if row.get("converted_opportunity_id") and row["status"] != "converted":
                broken("crm_campaign_members_converted")
        if (
            table == "crm_notes"
            and row.get("kind", "note") != "note"
            and not row.get("occurred_on")
        ):
            broken("crm_notes_activity_date")
        if table == "crm_ai_fields":
            # 065.
            if (row["output_type"] == "single_select") != (2 <= len(row["options"]) <= 12):
                broken("crm_ai_fields_options")
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", row["key"]):
                broken("crm_ai_fields_key")
            if row["name"] != row["name"].strip() or not 1 <= len(row["name"]) <= 60:
                broken("crm_ai_fields_name")

    def _check_unique(self, table: str, row: dict[str, Any]) -> None:
        def clash(name: str) -> None:
            raise store.refusal_for(
                "23505", f'duplicate key value violates unique constraint "{name}"'
            )

        pairs = {
            "crm_opportunity_contacts": (
                ("opportunity_id", "person_id"),
                "crm_opportunity_contacts_pair",
            ),
            "task_links": (("task_id", "record_type", "record_id"), "task_links_target"),
            "crm_note_links": (("note_id", "record_type", "record_id"), "crm_note_links_target"),
            "crm_note_mentions": (("note_id", "member_id"), "crm_note_mentions_pair"),
            # 065.
            "crm_ai_fields": (("workspace_id", "key"), "crm_ai_fields_key"),
            # 073.
            "crm_capture_ignores": (
                ("workspace_id", "kind", "value"),
                "crm_capture_ignores_once",
            ),
        }
        if table == "task_links" and row["record_type"] == "tva_project":
            # 047 (TP-13): one project per task.
            if any(
                o["id"] != row["id"]
                and o["task_id"] == row["task_id"]
                and o["record_type"] == "tva_project"
                for o in self.rows[table].values()
            ):
                clash("task_links_one_project")
        if table == "crm_saved_views":
            for other in self.rows[table].values():
                if other["id"] == row["id"] or other["workspace_id"] != row["workspace_id"]:
                    continue
                if (
                    other["object"] == row["object"]
                    and other["name"].lower() == row["name"].lower()
                    and other.get("owner_id") == row.get("owner_id")
                ):
                    clash("crm_saved_views_name")
                if (
                    other["object"] == row["object"]
                    and other.get("is_default")
                    and row.get("is_default")
                    and other["visibility"] == row["visibility"] == "shared"
                ):
                    clash("crm_saved_views_one_default")
            return
        if table == "crm_mailboxes":
            # 071: the capture source CHECK.
            if row.get("capture_source") not in ("labels", "primary_sent"):
                raise CheckViolation(f"crm_mailboxes.capture_source {row.get('capture_source')}")
            # 039: one address in one workspace, across workspaces.
            if any(
                other["id"] != row["id"] and other["email"] == row["email"]
                for other in self.rows[table].values()
            ):
                clash("crm_mailboxes_email")
            return
        if table == "crm_favourites":
            if any(
                other["id"] != row["id"]
                and all(
                    other[c] == row[c]
                    for c in ("workspace_id", "user_id", "target_type", "target_id")
                )
                for other in self.rows[table].values()
            ):
                clash("crm_favourites_target")
            return
        if table == "crm_bookings_targets":
            # 043: one target per workspace, entity, kind and period.
            if any(
                other["id"] != row["id"]
                and other["workspace_id"] == row["workspace_id"]
                and (other.get("entity") or "") == (row.get("entity") or "")
                and other["period_kind"] == row["period_kind"]
                and other["period_start"] == row["period_start"]
                for other in self.rows[table].values()
            ):
                clash("crm_bookings_targets_period")
            return
        if table == "crm_csv_mappings":
            if any(
                other["id"] != row["id"]
                and other["workspace_id"] == row["workspace_id"]
                and other["object"] == row["object"]
                and other["name"].lower() == row["name"].lower()
                for other in self.rows[table].values()
            ):
                clash("crm_csv_mappings_name")
            return
        if table == "crm_campaign_members":
            # 041: NULLS NOT DISTINCT.
            if any(
                other["id"] != row["id"]
                and all(
                    other.get(c) == row.get(c) for c in ("campaign_id", "company_id", "person_id")
                )
                for other in self.rows[table].values()
            ):
                clash("crm_campaign_members_once")
            return
        if table in pairs:
            columns, name = pairs[table]
            if any(
                other["id"] != row["id"] and all(other[c] == row[c] for c in columns)
                for other in self.rows[table].values()
            ):
                clash(name)
            return
        if table not in ("crm_company_domains", "crm_people", "crm_select_options"):
            return
        for other in self.rows[table].values():
            if other["id"] == row["id"] or other["workspace_id"] != row["workspace_id"]:
                continue
            if table == "crm_company_domains":
                if other["domain"] == row["domain"]:
                    clash("crm_company_domains_workspace_domain")
                if (
                    other["company_id"] == row["company_id"]
                    and other["is_primary"]
                    and row["is_primary"]
                ):
                    clash("crm_company_domains_one_primary")
            if table == "crm_people":
                if row.get("primary_email") and other.get("primary_email") == row["primary_email"]:
                    clash("crm_people_workspace_email")
            if table != "crm_select_options" or other["list_key"] != row["list_key"]:
                continue
            if other["label"].lower() == row["label"].lower():
                clash("crm_select_options_label")
            if other.get("is_default") and row.get("is_default"):
                clash("crm_select_options_one_default")

    def _audit_workspace(self, table: str, row: dict[str, Any]) -> str | None:
        """026's workspace of an audit row; 048: a task link with none (a
        project link) takes its task's, if the task is still there."""
        if table == "task_links" and row.get("workspace_id") is None:
            return (self.rows["tasks"].get(row["task_id"]) or {}).get("workspace_id")
        return row.get("workspace_id", row["id"])

    def _audit(self, actor, op, row, before, after) -> None:
        action = op.get("action")
        if not action:
            noun = NOUNS.get(op["table"]) or op["table"].removeprefix("crm_").removesuffix("s")
            if op.get("op") == "insert":
                verb = "created"
            elif op.get("op") == "delete":
                verb = "purged"
            elif (before or {}).get("deleted_at") is None and (after or {}).get("deleted_at"):
                verb = "deleted"
            elif (before or {}).get("deleted_at") and "deleted_at" in (after or {}):
                verb = "restored"
            else:
                verb = "updated"
            action = f"{noun}.{verb}"
        self.audit.append(
            {
                "id": len(self.audit) + 1,
                "at": _now(),
                "actor_id": actor["user_id"],
                "actor_label": actor["kind"],
                "channel": actor["channel"],
                "workspace_id": self._audit_workspace(op["table"], row),
                "action": action,
                "target_table": op["table"],
                "target_id": row["id"],
                "before": before,
                "after": after,
                # 061: the item an approval made this change for.
                **({"suggestion_id": actor["suggestion_id"]} if actor.get("suggestion_id") else {}),
            }
        )

    # -- spec 009: AI settings and the usage log (060) ----------------------

    def set_ai(
        self,
        workspace: dict[str, Any],
        *,
        budget_usd: Any = 0,
        phases: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """The `ai` row 060 seeds (every phase off, budget 0), or one with the
        given budget and phases. Not a store name: a set-up helper."""
        value = {
            "budget_usd": budget_usd,
            "phases": {"1": False, "2": False, "3": False, "4": False, **(phases or {})},
        }
        found = next(
            (
                r
                for r in self.rows["crm_workspace_settings"].values()
                if r["workspace_id"] == workspace["id"] and r["key"] == "ai"
            ),
            None,
        )
        if found is not None:
            found["value"] = value
            return found
        return self.add(
            "crm_workspace_settings", workspace_id=workspace["id"], key="ai", value=value
        )

    def ai_setting(self, workspace_id: str) -> dict[str, Any] | None:
        row = next(
            (
                r
                for r in self.rows["crm_workspace_settings"].values()
                if r["workspace_id"] == workspace_id and r["key"] == "ai"
            ),
            None,
        )
        return json.loads(json.dumps(row["value"])) if row else None

    def insert_ai_usage(self, workspace_id: str, row: dict[str, Any]) -> None:
        """crm_ai_usage's CHECKs, as 060 has them."""
        assert row["job"] in (
            "summaries",
            "meetings",
            "enrichment",
            "ai_fields",
            "followup_drafts",
            # 072 (spec 010).
            "capture",
        ), row["job"]
        assert row["outcome"] in ("ok", "invalid_output", "failed", "timed_out"), row["outcome"]
        assert row["input_tokens"] >= 0 and row["output_tokens"] >= 0
        assert Decimal(str(row["cost_usd"])) >= 0
        assert set(row) <= {
            "job",
            "model",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "outcome",
            "at",
        }, sorted(row)
        self.add("crm_ai_usage", workspace_id=workspace_id, **json.loads(json.dumps(row)))

    def ai_spend(self, workspace_id: str, since: datetime) -> dict[str, Any]:
        """crm_ai_spend: the sums since `since`, as decimal strings."""
        by_job: dict[str, Decimal] = {}
        for r in self.rows["crm_ai_usage"].values():
            if r["workspace_id"] == workspace_id and _at(r["at"]) >= since:
                by_job[r["job"]] = by_job.get(r["job"], Decimal(0)) + Decimal(str(r["cost_usd"]))
        return {
            "total_usd": str(sum(by_job.values(), Decimal(0))),
            "by_job": {job: str(usd) for job, usd in by_job.items()},
        }

    # -- spec 009: the Review Queue (061) -------------------------------------

    def add_review_item(self, workspace: dict[str, Any], **values: Any) -> dict[str, Any]:
        """An item as `crm_review_upsert` stores it, with the deal choices
        given as `deal_choices`. Not a store name: a set-up helper."""
        choices = values.pop("deal_choices", [])
        row = {
            "id": str(uuid.uuid4()),
            "workspace_id": workspace["id"],
            "status": "open",
            "owner_id": None,
            "member_id": None,
            "suggestion": {},
            "current_values": {},
            "source_kind": "email_thread",
            "source_key": f"gmail:mb:{uuid.uuid4().hex[:8]}",
            "source_version": "v1",
            "source_link": {},
            "model": "claude-sonnet-5-5",
            "generated_at": _now(),
            "refusal": None,
            "created_at": _now(),
            "decided_at": None,
            "decided_by": None,
            "result": {},
            "tombstoned_at": None,
            "revision": 1,
            **values,
        }
        target = f"{row['record_type']}:{row['record_id']}" if row.get("record_id") else "thread"
        row.setdefault(
            "dedupe_key", f"{row['source_kind']}|{row['source_key']}|{row['type']}|{target}"
        )
        self.rows["crm_review_items"][row["id"]] = row
        for deal_id in choices:
            self._add_review_deal(row, deal_id)
        return row

    def _add_review_deal(self, item: dict[str, Any], deal_id: str) -> None:
        key = f"{item['id']}:{deal_id}"
        self.rows["crm_review_item_deals"][key] = {
            "id": key,
            "workspace_id": item["workspace_id"],
            "item_id": item["id"],
            "opportunity_id": deal_id,
        }

    def _review_cascade(self) -> None:
        """061's foreign keys: an item goes with its campaign member, a deal
        choice with its deal or its item (CASCADE)."""
        members = self.rows["crm_campaign_members"]
        for item in list(self.rows["crm_review_items"].values()):
            if item.get("member_id") and item["member_id"] not in members:
                del self.rows["crm_review_items"][item["id"]]
        for key, row in list(self.rows["crm_review_item_deals"].items()):
            if (
                row["item_id"] not in self.rows["crm_review_items"]
                or row["opportunity_id"] not in self.rows["crm_opportunities"]
            ):
                del self.rows["crm_review_item_deals"][key]

    def _review_audit(self, actor, action: str, item_id: str, before: str, after: str) -> None:
        """The audit row 061's functions write themselves: no suggestion text."""
        self.audit.append(
            {
                "id": len(self.audit) + 1,
                "at": _now(),
                "actor_id": actor.get("user_id"),
                "actor_label": actor["kind"],
                "channel": actor.get("channel"),
                "workspace_id": actor["workspace_id"],
                "action": action,
                "target_table": "crm_review_items",
                "target_id": item_id,
                "before": {"status": before},
                "after": {"status": after},
                "suggestion_id": item_id,
            }
        )

    def list_review_items(
        self,
        workspace_id: str,
        *,
        status: str = "open",
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows = self._of("crm_review_items", workspace_id)
        if status == "open":
            rows = sorted(
                (r for r in rows if r["status"] == "open"),
                key=lambda r: (_at(r["created_at"]), r["id"]),
            )
        elif status == "decided":
            rows = [
                r
                for r in rows
                if r["status"] != "open"
                and r.get("tombstoned_at") is None
                and (since is None or _at(r["decided_at"]) >= since)
            ]
            rows.sort(key=lambda r: r["id"])
            rows.sort(key=lambda r: _at(r["decided_at"]), reverse=True)
        else:
            rows.sort(key=lambda r: r["id"])
        return json.loads(json.dumps(rows[:limit]))

    def get_review_item(self, workspace_id: str, item_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_review_items"].get(item_id)
        if row is None or row["workspace_id"] != workspace_id:
            return None
        return json.loads(json.dumps(row))

    def list_review_item_deals(self, workspace_id: str, item_ids: list[str]) -> list[dict]:
        wanted = set(item_ids)
        return [
            {"item_id": r["item_id"], "opportunity_id": r["opportunity_id"]}
            for r in self.rows["crm_review_item_deals"].values()
            if r["workspace_id"] == workspace_id and r["item_id"] in wanted
        ]

    def review_upsert(self, actor: dict[str, Any], items: list[dict[str, Any]]) -> list[dict]:
        """crm_review_upsert (061): the open-once key refreshed, a decided
        key from the same source version not made again, each deal choice
        an open deal of the workspace, the target in the workspace."""
        ws = actor["workspace_id"]
        assert actor["kind"] in ("sync", "system"), actor
        out = []
        for given in json.loads(json.dumps(items)):
            assert given["type"] in REVIEW_TYPES, given["type"]
            assert given["source_kind"] in ("email_thread", "meeting", "enrichment", "rule")
            assert len(json.dumps(given.get("suggestion") or {})) <= 8192
            mine = self._of("crm_review_items", ws)
            if any(
                r["dedupe_key"] == given["dedupe_key"]
                and r["status"] != "open"
                and r["source_version"] == given["source_version"]
                for r in mine
            ):
                out.append({"id": None, "created": False})
                continue
            # 073 crm_review_items_target: both or neither, neither only for
            # a new lead that matched no company.
            assert (given.get("record_type") is None) == (given.get("record_id") is None), given
            assert given.get("record_id") is not None or given["type"] == "new_lead", given
            if given.get("record_id") is not None:
                target = TARGETS.get(given["record_type"])
                found = target and self.rows[target].get(given["record_id"])
                if not found or found["workspace_id"] != ws:
                    raise store.refusal_for(
                        "CRM04", f"No such {given['record_type']} in this workspace."
                    )
            for deal_id in given.get("deal_choices") or []:
                deal = self.rows["crm_opportunities"].get(deal_id)
                if (
                    deal is None
                    or deal["workspace_id"] != ws
                    or deal.get("deleted_at")
                    or deal["stage"] not in OPEN_STAGES
                ):
                    raise store.refusal_for("CRM04", "No such open deal in this workspace.")
            existing = next(
                (
                    self.rows["crm_review_items"][r["id"]]
                    for r in mine
                    if r["status"] == "open" and r["dedupe_key"] == given["dedupe_key"]
                ),
                None,
            )
            choices = given.pop("deal_choices", None) or []
            if existing is not None:
                # 069: a refresh that changes what the item shows is a new revision.
                if any(
                    existing.get(name) != given.get(name)
                    for name in ("suggestion", "current_values", "source_version")
                ):
                    existing["revision"] = existing.get("revision", 1) + 1
                for name in (
                    "suggestion",
                    "current_values",
                    "source_version",
                    "model",
                    "generated_at",
                ):
                    existing[name] = given.get(name)
                row, created = existing, False
            else:
                row, created = (
                    self.add_review_item(
                        self.rows["crm_workspaces"][ws],
                        **{
                            k: v
                            for k, v in given.items()
                            if v is not None or k in ("owner_id", "record_type", "record_id")
                        },
                    ),
                    True,
                )
            for key in [
                k
                for k, r in self.rows["crm_review_item_deals"].items()
                if r["item_id"] == row["id"]
            ]:
                del self.rows["crm_review_item_deals"][key]
            for deal_id in choices:
                self._add_review_deal(row, deal_id)
            out.append({"id": row["id"], "created": created})
        return out

    def review_claim(
        self, actor: dict[str, Any], item_id: str, status: str, *, revision: int | None = None
    ) -> dict:
        """crm_review_claim (061, 069): one claim wins, at the revision shown
        when one is given; one audit row."""
        assert status in ("approved", "dismissed"), status
        row = self.rows["crm_review_items"].get(item_id)
        if row is None or row["workspace_id"] != actor["workspace_id"]:
            raise store.refusal_for("CRM04", "No such suggestion.")
        if row["status"] != "open":
            raise store.refusal_for("CRM09", "This suggestion was already decided.")
        if revision is not None and row.get("revision", 1) != revision:
            raise store.refusal_for("CRM09", "This suggestion changed; review it again.")
        row.update(status=status, decided_at=_now(), decided_by=actor["user_id"], refusal=None)
        self._review_audit(actor, f"review.{status}", item_id, "open", status)
        return json.loads(json.dumps(row))

    def review_release(
        self, actor: dict[str, Any], item_id: str, refusal: str | None, result: dict | None
    ) -> None:
        """crm_review_release (061, 068, 074): with a refusal, a claimed item
        whose key another open item holds expires (068), a capture item's
        conversations first joining that open item (074); otherwise it
        reopens, a capture item leaving out every conversation another open
        item holds now, and expiring instead when none is left (074)."""
        row = self.rows["crm_review_items"].get(item_id)
        if row is None or row["workspace_id"] != actor["workspace_id"]:
            raise store.refusal_for("CRM04", "No such suggestion.")
        if refusal is not None:
            if row["status"] != "approved":
                raise store.refusal_for("CRM04", "No such suggestion.")
            ws = row["workspace_id"]
            captured = _captured(row)
            entries = _cap_entries(row) if captured else []
            other = next(
                (
                    r
                    for r in self._of("crm_review_items", ws)
                    if r["dedupe_key"] == row["dedupe_key"]
                    and r["status"] == "open"
                    and r["id"] != item_id
                ),
                None,
            )
            if other is not None:
                if captured:
                    other = self.rows["crm_review_items"][other["id"]]
                    mine = _cap_entries(other)
                    # FR-CAP-GRP-07: a conversation a third open item holds
                    # now stays there (a deal document's entry excepted).
                    held = {
                        e["ref"]
                        for r in self._of("crm_review_items", ws)
                        if r["id"] not in (item_id, other["id"]) and _captured(r, open_only=True)
                        for e in _cap_entries(r)
                    }
                    kept = [
                        e
                        for e in entries
                        if (e.get("origin") or "discovery") == "deal_document"
                        or e["ref"] not in held
                    ]
                    threads = mine
                    for e in kept:
                        threads = _cap_merge(threads, e)
                    newest = _cap_newest(threads)
                    take = not any(_cap_same(newest, e) for e in mine)
                    source = row if take else other
                    suggestion = json.loads(json.dumps(source["suggestion"]))
                    if take:
                        suggestion = _cap_person(row["type"], suggestion, entries, kept)
                    suggestion["evidence"] = _cap_evidence(suggestion.get("evidence"), threads)
                    link = _cap_link(other["source_link"], threads)
                    changed = (
                        other["suggestion"],
                        other["source_version"],
                        other["source_link"],
                    ) != (
                        suggestion,
                        newest["message_id"],
                        link,
                    )
                    other.update(
                        suggestion=suggestion,
                        source_link=link,
                        source_version=newest["message_id"],
                        current_values=json.loads(json.dumps(source["current_values"])),
                    )
                    if changed:
                        other["revision"] = other.get("revision", 1) + 1
                    if take:
                        self._replace_review_deals(other, self._review_deals(row["id"]))
                row.update(status="expired", decided_at=_now(), refusal=refusal[:500])
                self._review_audit(actor, "review.expired", item_id, "approved", "expired")
                return
            if captured:
                held = {
                    e["ref"]
                    for r in self._of("crm_review_items", ws)
                    if r["id"] != item_id and _captured(r, open_only=True)
                    for e in _cap_entries(r)
                }
                kept = [
                    e
                    for e in entries
                    if (e.get("origin") or "discovery") == "deal_document" or e["ref"] not in held
                ]
                if entries and not kept:
                    if "threads" in row["source_link"]:
                        row["source_link"] = {**row["source_link"], "threads": []}
                    row.update(status="expired", decided_at=_now(), refusal=refusal[:500])
                    self._review_audit(actor, "review.expired", item_id, "approved", "expired")
                    return
                if len(kept) < len(entries):
                    row["suggestion"] = _cap_person(row["type"], row["suggestion"], entries, kept)
                    self._cap_set_entries(row, kept)
                    row["revision"] = row.get("revision", 1) + 1
            row.update(status="open", decided_at=None, decided_by=None, refusal=refusal[:500])
            self._review_audit(actor, "review.reopened", item_id, "approved", "open")
        else:
            if row["status"] == "open":
                raise store.refusal_for("CRM04", "No such suggestion.")
            row["result"] = json.loads(json.dumps(result or {}))

    # -- spec 010 R1: one suggestion per company (074) ------------------------

    def _review_deals(self, item_id: str) -> list[str]:
        return sorted(
            r["opportunity_id"]
            for r in self.rows["crm_review_item_deals"].values()
            if r["item_id"] == item_id
        )

    def _replace_review_deals(self, item: dict[str, Any], deal_ids: list[str]) -> None:
        for key in [
            k for k, r in self.rows["crm_review_item_deals"].items() if r["item_id"] == item["id"]
        ]:
            del self.rows["crm_review_item_deals"][key]
        for deal_id in deal_ids:
            self._add_review_deal(item, deal_id)

    def _cap_set_entries(self, row: dict[str, Any], threads: list[dict[str, Any]]) -> None:
        """An item's entries replaced: its link, its totals, its version."""
        row["source_link"] = _cap_link(row["source_link"], threads)
        suggestion = json.loads(json.dumps(row["suggestion"]))
        suggestion["evidence"] = _cap_evidence(suggestion.get("evidence"), threads)
        row["suggestion"] = suggestion
        row["source_version"] = _cap_newest(threads)["message_id"]

    def _cap_actor(self, workspace_id: str) -> dict[str, Any]:
        return {"user_id": None, "kind": "sync", "channel": None, "workspace_id": workspace_id}

    def capture_release_thread(self, workspace_id: str, ref: str, keep: str) -> dict[str, int]:
        """074 crm_capture_release_thread: the conversation leaves every open
        capture item but `keep`'s (a deal document's entry never); an item
        left with none expires, one review.expired audit row each."""
        removed = expired = 0
        for r in sorted(self._of("crm_review_items", workspace_id), key=lambda r: r["id"]):
            row = self.rows["crm_review_items"][r["id"]]
            if not _captured(row, open_only=True) or row["dedupe_key"] == keep:
                continue
            entries = _cap_entries(row)
            kept = [
                e
                for e in entries
                if not (e["ref"] == ref and (e.get("origin") or "discovery") != "deal_document")
            ]
            if len(kept) == len(entries):
                continue
            removed += 1
            if not kept:
                if "threads" in row["source_link"]:
                    row["source_link"] = {**row["source_link"], "threads": []}
                row.update(status="expired", decided_at=_now())
                self._review_audit(
                    self._cap_actor(workspace_id), "review.expired", row["id"], "open", "expired"
                )
                expired += 1
            else:
                row["suggestion"] = _cap_person(row["type"], row["suggestion"], entries, kept)
                self._cap_set_entries(row, kept)
                row["revision"] = row.get("revision", 1) + 1
        return {"removed": removed, "expired": expired}

    def capture_group_upsert(
        self, workspace_id: str, item: dict[str, Any], entry: dict[str, Any]
    ) -> dict[str, Any]:
        """074 crm_capture_group_upsert: skip a conversation state a decided
        item lists (not one consolidation merged, not one a refusal
        expired; a deal document's per company), release the conversation
        elsewhere (not for a deal document), then insert or merge, taking
        the suggestion, current values and deals only from the newest entry."""
        ws = workspace_id
        given = json.loads(json.dumps(item))
        entry = json.loads(json.dumps(entry))
        assert given["type"] in ("new_lead", "deal_signal"), given["type"]
        assert entry.get("ref") and entry.get("message_id"), entry
        document = entry.get("origin") == "deal_document"
        key = given["dedupe_key"]
        for r in self._of("crm_review_items", ws):
            if (
                r["status"] != "open"
                and r["type"] in ("new_lead", "deal_signal")
                and r["source_kind"] == "email_thread"
                and "superseded_by" not in (r.get("result") or {})
                and not (r["status"] == "expired" and r.get("refusal") is not None)
                and (not document or r["dedupe_key"] == key)
                and (
                    any(
                        e.get("ref") == entry["ref"] and e.get("message_id") == entry["message_id"]
                        for e in (r.get("source_link") or {}).get("threads") or []
                    )
                    or (
                        r["source_key"] == entry["ref"]
                        and r["source_version"] == entry["message_id"]
                    )
                )
            ):
                return {"id": None, "created": False, "skipped": True, "released": 0}
        released = 0
        if not document:
            released = self.capture_release_thread(ws, entry["ref"], key)["removed"]
        assert len(json.dumps(given.get("suggestion") or {})) <= 8192
        if given.get("record_id") is not None:
            target = TARGETS.get(given["record_type"])
            found = target and self.rows[target].get(given["record_id"])
            if not found or found["workspace_id"] != ws:
                raise store.refusal_for(
                    "CRM04", f"No such {given['record_type']} in this workspace."
                )
        choices = given.pop("deal_choices", None) or []
        open_row = next(
            (
                self.rows["crm_review_items"][r["id"]]
                for r in self._of("crm_review_items", ws)
                if r["status"] == "open" and r["dedupe_key"] == key
            ),
            None,
        )
        threads = _cap_merge(_cap_entries(open_row), entry) if open_row else [entry]
        newest = _cap_newest(threads)
        take = open_row is None or (
            newest["ref"] == entry["ref"] and newest.get("message_id") == entry["message_id"]
        )
        if take:
            for deal_id in choices:
                deal = self.rows["crm_opportunities"].get(deal_id)
                if (
                    deal is None
                    or deal["workspace_id"] != ws
                    or deal.get("deleted_at")
                    or deal["stage"] not in OPEN_STAGES
                ):
                    raise store.refusal_for("CRM04", "No such open deal in this workspace.")
        if open_row is None:
            suggestion = given.get("suggestion") or {}
            suggestion["evidence"] = _cap_evidence(suggestion.get("evidence"), threads)
            row = self.add_review_item(
                self.rows["crm_workspaces"][ws],
                **{
                    **{
                        k: v
                        for k, v in given.items()
                        if v is not None or k in ("owner_id", "record_type", "record_id")
                    },
                    "suggestion": suggestion,
                    "source_version": entry["message_id"],
                    "source_link": _cap_link(given.get("source_link") or {}, threads),
                },
            )
            created = True
        else:
            row, created = open_row, False
            source = given if take else row
            suggestion = json.loads(json.dumps(source.get("suggestion") or {}))
            suggestion["evidence"] = _cap_evidence(suggestion.get("evidence"), threads)
            values = json.loads(json.dumps(source.get("current_values") or {}))
            link = _cap_link(row["source_link"], threads)
            before = (row["suggestion"], row["current_values"], row["source_version"])
            if before + (row["source_link"],) != (suggestion, values, newest["message_id"], link):
                row["revision"] = row.get("revision", 1) + 1
            row.update(
                suggestion=suggestion,
                current_values=values,
                source_key=given["source_key"],
                source_link=link,
                source_version=newest["message_id"],
            )
            if take:
                row.update(model=given.get("model"), generated_at=given.get("generated_at"))
        if take:
            self._replace_review_deals(row, choices)
        return {
            "id": row["id"],
            "created": created,
            "skipped": False,
            "revision": row.get("revision", 1),
            "released": released,
        }

    def capture_consolidate(self, workspace_id: str, plan: list[dict[str, Any]]) -> dict:
        """074 crm_capture_consolidate: per group, the members still open
        (and an open item holding the key) become one, the newest by
        creation surviving with every entry; the others expire first with
        `superseded_by`, one review.expired audit row each."""
        assert isinstance(plan, list), plan
        groups = gone = rekeyed = 0
        for g in json.loads(json.dumps(plan)):
            members = set(g.get("members") or [])
            rows = sorted(
                (
                    self.rows["crm_review_items"][r["id"]]
                    for r in self._of("crm_review_items", workspace_id)
                    if _captured(r, open_only=True)
                    and r.get("record_id") == g.get("record_id")
                    and (r["id"] in members or r["dedupe_key"] == g["dedupe_key"])
                ),
                key=lambda r: (_at(r["created_at"]), r["id"]),
                reverse=True,
            )
            if not rows:
                continue
            survivor = rows[0]
            if (
                len(rows) == 1
                and survivor["dedupe_key"] == g["dedupe_key"]
                and survivor["source_key"] == g["source_key"]
                and "threads" in survivor["source_link"]
            ):
                continue
            groups += 1
            threads: list[dict[str, Any]] = []
            for r in reversed(rows):
                for e in _cap_entries(r):
                    threads = _cap_merge(threads, e)
            newest = _cap_newest(threads)
            donor = next(
                (r for r in rows if any(_cap_same(newest, e) for e in _cap_entries(r))), survivor
            )
            label = (
                g.get("company_label")
                or donor["suggestion"].get("company_name")
                or ("this company")
            )
            for r in rows[1:]:
                r.update(
                    status="expired",
                    decided_at=_now(),
                    result={
                        "superseded_by": survivor["id"],
                        "reason": f"Merged into one suggestion for {label}."[:500],
                    },
                )
                self._review_audit(
                    self._cap_actor(workspace_id), "review.expired", r["id"], "open", "expired"
                )
                self.audit[-1]["after"]["superseded_by"] = survivor["id"]
                gone += 1
            if (survivor["source_key"], survivor["dedupe_key"]) != (
                g["source_key"],
                g["dedupe_key"],
            ):
                rekeyed += 1
            suggestion = json.loads(json.dumps(donor["suggestion"]))
            survivor.update(
                source_key=g["source_key"],
                dedupe_key=g["dedupe_key"],
                suggestion=suggestion,
                current_values=json.loads(json.dumps(donor["current_values"])),
                revision=survivor.get("revision", 1) + 1,
            )
            self._cap_set_entries(survivor, threads)
            if donor is not survivor:
                self._replace_review_deals(survivor, self._review_deals(donor["id"]))
        return {"groups": groups, "expired": gone, "rekeyed": rekeyed}

    def open_capture_items(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = sorted(
            (r for r in self._of("crm_review_items", workspace_id) if _captured(r, open_only=True)),
            key=lambda r: (_at(r["created_at"]), r["id"]),
        )
        return json.loads(json.dumps(rows))

    def candidates_for_threads(
        self, workspace_id: str, mailbox_id: str, thread_ids: list[str]
    ) -> list[dict[str, Any]]:
        wanted = set(thread_ids)
        return json.loads(
            json.dumps(
                [
                    c
                    for c in self.rows["crm_capture_candidates"].values()
                    if c["workspace_id"] == workspace_id
                    and c["mailbox_id"] == mailbox_id
                    and c["thread_id"] in wanted
                ]
            )
        )

    def review_expire(self, actor: dict[str, Any], before: datetime) -> int:
        """crm_review_expire (061): one `review.expired` audit row each."""
        assert actor["kind"] in ("system", "sync"), actor
        n = 0
        for row in self.rows["crm_review_items"].values():
            if (
                row["workspace_id"] == actor["workspace_id"]
                and row["status"] == "open"
                and _at(row["created_at"]) < before
            ):
                row.update(status="expired", decided_at=_now())
                self._review_audit(actor, "review.expired", row["id"], "open", "expired")
                n += 1
        return n

    def review_tombstone(self, actor: dict[str, Any], before: datetime) -> int:
        """crm_review_tombstone (061): the text goes, the dedupe key stays."""
        assert actor["kind"] in ("system", "sync"), actor
        n = 0
        for row in self.rows["crm_review_items"].values():
            if (
                row["workspace_id"] == actor["workspace_id"]
                and row["status"] != "open"
                and row.get("tombstoned_at") is None
                and _at(row["decided_at"]) < before
            ):
                row.update(
                    suggestion={},
                    current_values={},
                    source_link={},
                    refusal=None,
                    tombstoned_at=_now(),
                )
                n += 1
        return n

    # -- spec 009: email thread summaries (062) -------------------------------

    def _forget_summary(self, summary_id: str) -> None:
        del self.rows["crm_email_summaries"][summary_id]
        for link in list(self.rows["crm_email_summary_links"].values()):
            if link["summary_id"] == summary_id:
                del self.rows["crm_email_summary_links"][link["id"]]

    def _qualifying(self, workspace_id: str) -> list[dict[str, Any]]:
        """062's qualifying messages: not internal-only, not auto, linked to
        a company or person, of an active mailbox."""
        linked = {
            link["message_id"]
            for link in self._of("crm_email_links", workspace_id)
            if link["record_type"] in ("company", "person")
        }
        mailboxes = self.rows["crm_mailboxes"]
        return sorted(
            (
                m
                for m in self._of("crm_email_messages", workspace_id)
                if not m.get("is_internal")
                and not m.get("is_auto")
                and m["id"] in linked
                and mailboxes[m["mailbox_id"]]["is_active"]
            ),
            key=lambda m: (_at(m["sent_at"]), m["gmail_message_id"]),
        )

    def pending_threads(self, workspace_id: str, limit: int) -> list[dict[str, Any]]:
        """crm_pending_threads (062), as the SQL reads it."""
        threads: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for m in self._qualifying(workspace_id):
            threads.setdefault((m["mailbox_id"], m["thread_id"]), []).append(m)
        out = []
        for (mailbox_id, thread_id), msgs in threads.items():
            mailbox = self.rows["crm_mailboxes"][mailbox_id]
            done = mailbox.get("backfilled_at")
            if not done or not any(_at(m["created_at"]) > _at(done) for m in msgs):
                continue
            latest = msgs[-1]
            summary = next(
                (
                    s
                    for s in self.rows["crm_email_summaries"].values()
                    if s["mailbox_id"] == mailbox_id and s["thread_id"] == thread_id
                ),
                None,
            )
            if summary is not None and not (
                latest["gmail_message_id"] != summary["watermark_message_id"]
                and _at(latest["sent_at"]) >= _at(summary["watermark_at"])
            ):
                continue
            ids = {m["id"] for m in msgs}
            links = sorted(
                {
                    (link["record_type"], link["record_id"])
                    for link in self.rows["crm_email_links"].values()
                    if link["message_id"] in ids
                }
            )
            out.append(
                {
                    "mailbox_id": mailbox_id,
                    "thread_id": thread_id,
                    "message_ids": [m["gmail_message_id"] for m in msgs],
                    "messages": [
                        {
                            "gmail_id": m["gmail_message_id"],
                            "direction": m["direction"],
                            "sent_at": m["sent_at"],
                        }
                        for m in msgs
                    ],
                    "latest": {
                        "gmail_id": latest["gmail_message_id"],
                        "sent_at": latest["sent_at"],
                    },
                    "links": [{"record_type": t, "record_id": r} for t, r in links],
                }
            )
        out.sort(key=lambda t: (_at(t["latest"]["sent_at"]), t["mailbox_id"], t["thread_id"]))
        return json.loads(json.dumps(out[:limit]))

    def store_summary(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """crm_store_summary (062): the watermark re-checked (CRM09), the
        summary upserted with 062's CHECKs, its links replaced (each in the
        workspace, CRM04) and its items through crm_review_upsert, in one
        transaction."""
        ws = actor["workspace_id"]
        assert actor["kind"] in ("sync", "system"), actor
        plan = json.loads(json.dumps(plan))
        mailbox = self.rows["crm_mailboxes"].get(plan["mailbox_id"])
        if mailbox is None or mailbox["workspace_id"] != ws:
            raise store.refusal_for("CRM04", "No such mailbox.")
        # The SQL re-check reads qualifying messages whether or not the
        # mailbox is still active.
        linked = {
            link["message_id"]
            for link in self._of("crm_email_links", ws)
            if link["record_type"] in ("company", "person")
        }
        mine = sorted(
            (
                m
                for m in self._of("crm_email_messages", ws)
                if m["mailbox_id"] == plan["mailbox_id"]
                and m["thread_id"] == plan["thread_id"]
                and not m.get("is_internal")
                and not m.get("is_auto")
                and m["id"] in linked
            ),
            key=lambda m: (_at(m["sent_at"]), m["gmail_message_id"]),
        )
        if not mine or mine[-1]["gmail_message_id"] != plan["watermark"]["gmail_id"]:
            raise store.refusal_for("CRM09", "This thread changed while it was summarised.")
        summary = plan["summary"]
        assert summary == summary.strip() and 1 <= len(summary) <= 600, summary
        assert len(plan["asks"]) <= 10 and len(plan["commitments"]) <= 10
        assert plan.get("next_step_on") is None or plan.get("next_step") is not None
        assert plan["sentiment"] in ("positive", "neutral", "concern")
        assert plan.get("next_step") is None or len(plan["next_step"]) <= 500
        tables = ("crm_email_summaries", "crm_email_summary_links", "crm_review_items")
        snapshot = {t: {k: dict(v) for k, v in self.rows[t].items()} for t in tables}
        snapshot["crm_review_item_deals"] = dict(self.rows["crm_review_item_deals"])
        try:
            values = {
                "summary": summary,
                "asks": plan["asks"],
                "commitments": plan["commitments"],
                "next_step": plan.get("next_step"),
                "next_step_on": plan.get("next_step_on"),
                "sentiment": plan["sentiment"],
                "watermark_message_id": plan["watermark"]["gmail_id"],
                "watermark_at": plan["watermark"]["sent_at"],
                "model": plan["model"],
                "generated_at": plan["generated_at"],
                "updated_at": _now(),
            }
            row = next(
                (
                    s
                    for s in self.rows["crm_email_summaries"].values()
                    if s["mailbox_id"] == plan["mailbox_id"] and s["thread_id"] == plan["thread_id"]
                ),
                None,
            )
            if row is None:
                row = self.add(
                    "crm_email_summaries",
                    workspace_id=ws,
                    mailbox_id=plan["mailbox_id"],
                    thread_id=plan["thread_id"],
                    created_at=_now(),
                    **values,
                )
            else:
                row.update(values)
            for link in list(self.rows["crm_email_summary_links"].values()):
                if link["summary_id"] == row["id"]:
                    del self.rows["crm_email_summary_links"][link["id"]]
            for link in plan.get("links") or []:
                target = self.rows[TARGETS[link["record_type"]]].get(link["record_id"])
                if target is None or target["workspace_id"] != ws:
                    raise store.refusal_for(
                        "CRM04", f"No such {link['record_type']} in this workspace."
                    )
                self.add(
                    "crm_email_summary_links",
                    workspace_id=ws,
                    summary_id=row["id"],
                    record_type=link["record_type"],
                    record_id=link["record_id"],
                )
            # A commitment already suggested on the thread under another
            # target is left out (062).
            known = self._of("crm_review_items", ws)
            wanted = [
                i
                for i in plan.get("items") or []
                if not (
                    i["type"] == "task_from_commitment"
                    and any(
                        r["type"] == "task_from_commitment"
                        and r["source_kind"] == i["source_kind"]
                        and r["source_key"] == i["source_key"]
                        and r["source_version"] == i["source_version"]
                        and r["dedupe_key"] != i["dedupe_key"]
                        for r in known
                    )
                )
            ]
            items = self.review_upsert(actor, wanted)
        except Exception:
            self.rows.update(snapshot)
            raise
        return {"id": row["id"], "items": items}

    def _summary_details(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            msgs = sorted(
                (
                    m
                    for m in self.rows["crm_email_messages"].values()
                    if m["mailbox_id"] == r["mailbox_id"] and m["thread_id"] == r["thread_id"]
                ),
                key=lambda m: _at(m["sent_at"]),
            )
            subject = None
            for m in msgs:
                subject = m.get("subject") or subject
            out.append(
                {
                    **r,
                    "links": [
                        {"record_type": lk["record_type"], "record_id": lk["record_id"]}
                        for lk in self.rows["crm_email_summary_links"].values()
                        if lk["summary_id"] == r["id"]
                    ],
                    "subject": subject,
                    "last_at": msgs[-1]["sent_at"] if msgs else None,
                }
            )
        return json.loads(json.dumps(out))

    def summaries_for(
        self,
        workspace_id: str,
        record_type: str | None = None,
        record_id: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._of("crm_email_summaries", workspace_id)
        if record_type and record_id:
            ids = {
                lk["summary_id"]
                for lk in self._of("crm_email_summary_links", workspace_id)
                if lk["record_type"] == record_type and lk["record_id"] == record_id
            }
            rows = [r for r in rows if r["id"] in ids]
        rows.sort(key=lambda r: r["id"])
        rows.sort(key=lambda r: str(r["updated_at"]), reverse=True)
        return self._summary_details(rows[:limit] if limit else rows)

    def summary_by_thread(
        self, workspace_id: str, mailbox_id: str, thread_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        rows = [
            r
            for r in self._of("crm_email_summaries", workspace_id)
            if r["mailbox_id"] == mailbox_id and r["thread_id"] in thread_ids
        ]
        return {r["thread_id"]: r for r in self._summary_details(rows)}

    # -- spec 009: meeting notes (063) ----------------------------------------

    def store_meeting_note(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """crm_store_meeting_note (063): the note inserted through crm_apply's
        audited path with its links, or its body and provenance rewritten for
        a newer transcript while no person has edited it (Q-41); then its
        items through crm_review_upsert, each pointing at the note, in one
        transaction."""
        ws = actor["workspace_id"]
        assert actor["kind"] in ("sync", "system"), actor
        plan = json.loads(json.dumps(plan))
        ref = plan.get("source_ref") or ""
        if not ref.startswith("fireflies:") or ref == "fireflies:":
            raise store.refusal_for("CRM22", "A meeting note needs its transcript.")
        assert len(ref) <= 200
        provenance = plan["ai_provenance"]
        assert isinstance(provenance, dict)
        snapshot = {t: {k: dict(v) for k, v in rows.items()} for t, rows in self.rows.items()}
        audit_len = len(self.audit)
        try:
            existing = next(
                (n for n in self._of("crm_notes", ws) if n.get("source_ref") == ref), None
            )
            created = updated = False
            if existing is None:
                ops = [
                    {
                        "op": "insert",
                        "table": "crm_notes",
                        "ref": "note",
                        "values": {
                            "body": plan["body"],
                            "kind": "meeting",
                            "occurred_on": plan["occurred_on"],
                            "source_ref": ref,
                            "ai_provenance": provenance,
                        },
                    }
                ]
                ops += [
                    {
                        "op": "insert",
                        "table": "crm_note_links",
                        "values": {
                            "note_id": "$ref:note",
                            "record_type": link["record_type"],
                            "record_id": link["record_id"],
                        },
                    }
                    for link in plan.get("links") or []
                ]
                note_id = self.apply(actor, ops)[0]["id"]
                created = True
            else:
                note_id = existing["id"]
                seen = (existing.get("ai_provenance") or {}).get("transcript_updated_at")
                if seen and _at(provenance["transcript_updated_at"]) <= _at(seen):
                    return {"note_id": note_id, "created": False, "updated": False, "items": []}
                if any(
                    r["target_table"] == "crm_notes"
                    and r["target_id"] == note_id
                    and r["actor_label"] == "user"
                    and r["action"] != "note.created"
                    for r in self.audit
                ):
                    return {
                        "note_id": note_id,
                        "created": False,
                        "updated": False,
                        "edited_by_person": True,
                        "items": [],
                    }
                self.apply(
                    actor,
                    [
                        {
                            "op": "update",
                            "table": "crm_notes",
                            "id": note_id,
                            "values": {"body": plan["body"], "ai_provenance": provenance},
                        }
                    ],
                )
                updated = True
            items = []
            for item in plan.get("items") or []:
                if item["type"] == "deal_link":
                    item = {
                        **item,
                        "record_type": "note",
                        "record_id": note_id,
                        "suggestion": {"note_id": note_id},
                        "dedupe_key": item["dedupe_key"].replace("$note", note_id),
                    }
                items.append({**item, "source_link": {"record_type": "note", "record_id": note_id}})
            made = self.review_upsert(actor, items)
        except Exception:
            self.rows, self.audit[audit_len:] = snapshot, []
            raise
        return {"note_id": note_id, "created": created, "updated": updated, "items": made}

    # -- Spec 009: company enrichment (064) ----------------------------------

    def add_enrichment(self, workspace: dict[str, Any], company: dict[str, Any], **values) -> dict:
        """An enrichment row as the job leaves it. Not a store name: a set-up
        helper."""
        found = [
            e
            for e in self.rows["crm_company_enrichment"].values()
            if e["company_id"] == company["id"]
        ]
        for e in found:
            del self.rows["crm_company_enrichment"][e["id"]]
        return self.add(
            "crm_company_enrichment",
            workspace_id=workspace["id"],
            company_id=company["id"],
            **{
                "website": "https://acme.in/",
                "status": "done",
                "failure_reason": None,
                "values": {},
                "model": "claude-sonnet-5-5",
                "generated_at": "2026-10-18T06:00:00+00:00",
                "queued_at": _now(),
                "requested_by": None,
                "created_at": _now(),
                **values,
            },
        )

    def _queue_enrichment(self, workspace_id: str, company_id: str) -> None:
        """064's `crm_queue_enrichment` trigger: the company's site queued, or
        queued again with its old values cleared when it changed (Q-32); the
        same site changes nothing; a company in the Trash is not queued. The
        company row is not touched."""
        company = self.rows["crm_companies"].get(company_id)
        if company is None or company["workspace_id"] != workspace_id or company.get("deleted_at"):
            return
        primary = next(
            (
                d["domain"]
                for d in self.rows["crm_company_domains"].values()
                if d["company_id"] == company_id and d.get("is_primary")
            ),
            None,
        )
        site = enrichment_rules.site(company.get("website"), primary)
        if site is None:
            return
        row = next(
            (
                e
                for e in self.rows["crm_company_enrichment"].values()
                if e["workspace_id"] == workspace_id and e["company_id"] == company_id
            ),
            None,
        )
        fresh = {
            "website": site,
            "status": "queued",
            "failure_reason": None,
            "values": {},
            "model": None,
            "generated_at": None,
            "queued_at": _now(),
            "requested_by": None,
        }
        if row is None:
            self.add(
                "crm_company_enrichment",
                workspace_id=workspace_id,
                company_id=company_id,
                created_at=_now(),
                **fresh,
            )
        elif row["website"] != site:
            row.update(fresh)

    def _enrichment_live(self, workspace_id: str, company_id: str) -> bool:
        company = self.rows["crm_companies"].get(company_id)
        return (
            bool(company)
            and company["workspace_id"] == workspace_id
            and not company.get("deleted_at")
        )

    def queued_enrichment(
        self, workspace_id: str, limit: int, *, before: datetime
    ) -> list[dict[str, Any]]:
        rows = [
            e
            for e in self._of("crm_company_enrichment", workspace_id)
            if e["status"] == "queued"
            and _at(e["queued_at"]) <= before
            and self._enrichment_live(workspace_id, e["company_id"])
        ]
        rows.sort(key=lambda e: (_at(e["queued_at"]), e["id"]))
        return json.loads(json.dumps(rows[:limit], default=str))

    def enrichment_for(
        self, workspace_id: str, company_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        rows = [
            e
            for e in self._of("crm_company_enrichment", workspace_id)
            if company_ids is None or e["company_id"] in company_ids
        ]
        return json.loads(json.dumps(sorted(rows, key=lambda e: e["company_id"]), default=str))

    def write_enrichment(
        self, workspace_id: str, company_id: str, website: str, values: dict[str, Any]
    ) -> bool:
        """The worker's update, only while queued for the same site, under
        064's CHECKs."""
        row = next(
            (
                e
                for e in self.rows["crm_company_enrichment"].values()
                if e["workspace_id"] == workspace_id
                and e["company_id"] == company_id
                and e["website"] == website
                and e["status"] == "queued"
            ),
            None,
        )
        if row is None:
            return False
        after = {**row, **json.loads(json.dumps(values, default=str))}
        assert after["status"] in ("queued", "done", "failed"), after["status"]
        assert (after["status"] == "failed") == (after.get("failure_reason") is not None), after
        assert after.get("failure_reason") in (None, *ENRICHMENT_REASONS), after["failure_reason"]
        assert isinstance(after["values"], dict) and len(json.dumps(after["values"])) <= 4000
        row.update(after)
        return True

    def requeue_enrichment(
        self, workspace_id: str, rows: list[dict[str, Any]], at: datetime
    ) -> None:
        for given in json.loads(json.dumps(rows, default=str)):
            assert self._enrichment_live(workspace_id, given["company_id"])
            values = {
                **given,
                "status": "queued",
                "failure_reason": None,
                "queued_at": at.isoformat(),
            }
            row = next(
                (
                    e
                    for e in self.rows["crm_company_enrichment"].values()
                    if e["workspace_id"] == workspace_id and e["company_id"] == given["company_id"]
                ),
                None,
            )
            if row is None:
                self.add(
                    "crm_company_enrichment",
                    workspace_id=workspace_id,
                    created_at=_now(),
                    **{"values": {}, "model": None, "generated_at": None, **values},
                )
            else:
                row.update(values)

    # -- Spec 009: AI fields (065) --------------------------------------------

    def seed_ai_fields(self, workspace: dict[str, Any]) -> list[dict[str, Any]]:
        """065's seeds in one workspace: ICP fit in Labs only. Not a store
        name: a set-up helper."""
        return [
            self.add(
                "crm_ai_fields",
                workspace_id=workspace["id"],
                created_at=_now(),
                created_by=None,
                updated_by=None,
                enabled=True,
                **json.loads(json.dumps(f)),
            )
            for f in ai_field_rules.defaults_for(workspace["key"])
        ]

    def ai_field(self, workspace: dict[str, Any], key: str) -> dict[str, Any]:
        return next(
            f
            for f in self.rows["crm_ai_fields"].values()
            if f["workspace_id"] == workspace["id"] and f["key"] == key
        )

    def list_ai_fields(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self._of("crm_ai_fields", workspace_id)
        rows.sort(key=lambda f: (float(f.get("position") or 0), f["name"], f["id"]))
        return json.loads(json.dumps(rows, default=str))

    def get_ai_field(self, workspace_id: str, field_id: str) -> dict[str, Any] | None:
        row = self.rows["crm_ai_fields"].get(field_id)
        if row is None or row["workspace_id"] != workspace_id:
            return None
        return json.loads(json.dumps(row, default=str))

    def ai_field_values(
        self,
        workspace_id: str,
        record_type: str | None = None,
        record_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            v
            for v in self._of("crm_ai_field_values", workspace_id)
            if (record_type is None or v["record_type"] == record_type)
            and (record_ids is None or v["record_id"] in record_ids)
        ]
        return json.loads(json.dumps(sorted(rows, key=lambda v: v["id"]), default=str))

    def write_ai_field_value(self, workspace_id: str, row: dict[str, Any]) -> None:
        """The upsert on `(field_id, record_id)`, under 065's CHECKs, its
        composite key and crm_check_refs."""
        given = json.loads(json.dumps(row, default=str))
        field = self.rows["crm_ai_fields"].get(given["field_id"])
        assert field is not None and field["workspace_id"] == workspace_id, "23503"
        record = self.rows[TARGETS[given["record_type"]]].get(given["record_id"])
        if record is None or record["workspace_id"] != workspace_id:
            raise store.refusal_for("CRM04", f"No such {given['record_type']} in this workspace.")
        assert given.get("value_text") is None or len(given["value_text"]) <= 600
        assert given.get("value_number") is None or 0 <= given["value_number"] <= 100
        assert given.get("reason") is None or len(given["reason"]) <= 200
        assert given["inputs_hash"] and given["next_allowed_at"]
        found = next(
            (
                v
                for v in self.rows["crm_ai_field_values"].values()
                if v["field_id"] == given["field_id"] and v["record_id"] == given["record_id"]
            ),
            None,
        )
        if found is None:
            self.add("crm_ai_field_values", workspace_id=workspace_id, **given)
        else:
            found.update(given)

    def mark_ai_fields_dirty(
        self,
        workspace_id: str,
        record_type: str,
        record_ids: list[str],
        at: datetime,
        *,
        field_ids: list[str] | None = None,
    ) -> None:
        for v in self.rows["crm_ai_field_values"].values():
            if (
                v["workspace_id"] == workspace_id
                and v["record_type"] == record_type
                and v["record_id"] in record_ids
                and (field_ids is None or v["field_id"] in field_ids)
                and not v.get("dirty_at")
            ):
                v["dirty_at"] = at.isoformat()

    # -- spec 009: the follow-up rules' facts (066) ---------------------------

    def lead_activity(self, workspace_id: str, company_ids: list[str] | None) -> dict[str, Any]:
        """066 crm_lead_activity: company id → the latest of its last
        contact, linked notes and tasks, lead stage changes and its deals'
        activity (update, stage entry, history, linked notes and tasks)."""

        def note_times(record_type: str, record_id: str) -> list[Any]:
            out = []
            for link in self.rows["crm_note_links"].values():
                note = self.rows["crm_notes"].get(link["note_id"])
                if (
                    link["record_type"] == record_type
                    and link["record_id"] == record_id
                    and note
                    and not note.get("deleted_at")
                ):
                    on = note.get("occurred_on")
                    out.append(
                        f"{on}T00:00:00+00:00"
                        if note.get("kind", "note") != "note" and on
                        else note.get("created_at")
                    )
            return out

        def task_times(record_type: str, record_id: str) -> list[Any]:
            out = []
            for link in self.rows["task_links"].values():
                task = self.rows["tasks"].get(link["task_id"])
                if (
                    link["record_type"] == record_type
                    and link["record_id"] == record_id
                    and task
                    and not task.get("deleted_at")
                ):
                    out += [task.get("created_at"), task.get("completed_at")]
            return out

        found: dict[str, Any] = {}
        for c in self._of("crm_companies", workspace_id):
            if c.get("deleted_at") or (company_ids is not None and c["id"] not in company_ids):
                continue
            times = [c.get("last_contact_at")]
            times += note_times("company", c["id"]) + task_times("company", c["id"])
            times += [
                a["at"]
                for a in self.audit
                if a["target_table"] == "crm_companies"
                and a["target_id"] == c["id"]
                and a["action"] == "company.updated"
                and "lead_stage" in (a.get("after") or {})
            ]
            for d in self._of("crm_opportunities", workspace_id):
                if d.get("company_id") != c["id"] or d.get("deleted_at"):
                    continue
                times += [d.get("updated_at"), d.get("stage_entered_at")]
                times += [
                    h["at"]
                    for h in self.rows["crm_stage_history"].values()
                    if h["opportunity_id"] == d["id"]
                ]
                times += note_times("opportunity", d["id"]) + task_times("opportunity", d["id"])
            known = [_at(t) for t in times if t]
            found[c["id"]] = max(known).isoformat() if known else None
        return found

    def no_reply_candidates(self, workspace_id: str, before: datetime) -> list[dict[str, Any]]:
        """066 crm_no_reply_candidates: the latest counted message of each
        thread, outbound, sent before `before`, with no later inbound on any
        linked live person or company. Ids only."""
        counted = [
            m
            for m in self._of("crm_email_messages", workspace_id)
            if not m["is_internal"] and not m["is_auto"]
        ]
        threads: dict[tuple[str, str], dict[str, Any]] = {}
        for m in counted:
            key = (m["mailbox_id"], m["thread_id"])
            best = threads.get(key)
            if best is None or (_at(m["sent_at"]), m["gmail_message_id"]) > (
                _at(best["sent_at"]),
                best["gmail_message_id"],
            ):
                threads[key] = m
        tables = {"company": "crm_companies", "person": "crm_people"}

        def linked(message_id: str) -> list[tuple[str, str]]:
            out = []
            for link in self._of("crm_email_links", workspace_id):
                row = (
                    self.rows[tables[link["record_type"]]].get(link["record_id"])
                    if (link["record_type"] in tables)
                    else None
                )
                if link["message_id"] == message_id and row and not row.get("deleted_at"):
                    out.append((link["record_type"], link["record_id"]))
            return out

        found = []
        for m in threads.values():
            if m["direction"] != "outbound" or _at(m["sent_at"]) >= before:
                continue
            records = linked(m["id"])
            if not records:
                continue
            answered = any(
                (link["record_type"], link["record_id"]) in records
                and (later := self.rows["crm_email_messages"].get(link["message_id"]))
                and later in counted
                and later["direction"] == "inbound"
                and _at(later["sent_at"]) > _at(m["sent_at"])
                for link in self._of("crm_email_links", workspace_id)
            )
            if answered:
                continue
            found.append(
                {
                    "mailbox_id": m["mailbox_id"],
                    "thread_id": m["thread_id"],
                    "gmail_message_id": m["gmail_message_id"],
                    "sent_at": m["sent_at"],
                    "people": sorted(i for t, i in records if t == "person"),
                    "companies": sorted(i for t, i in records if t == "company"),
                }
            )
        return sorted(found, key=lambda c: (_at(c["sent_at"]), c["mailbox_id"], c["thread_id"]))

    # -- spec 009: contact recency (067) ----------------------------------------

    def contact_facts(self, workspace_id: str, now: datetime) -> dict[str, Any]:
        """067 crm_contact_facts: every live company and person with its
        latest counted inbound and outbound email and latest meeting note in
        the last 91 days, a company's including its live people's."""
        today = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
        since = now - timedelta(days=91)
        contacts: dict[tuple[str, str], list[tuple[str, Any]]] = {}
        for link in self._of("crm_email_links", workspace_id):
            msg = self.rows["crm_email_messages"].get(link["message_id"])
            if (
                link["record_type"] not in ("company", "person")
                or msg is None
                or msg["is_internal"]
                or msg["is_auto"]
                or not since <= _at(msg["sent_at"]) <= now
            ):
                continue
            key = "in" if msg["direction"] == "inbound" else "out"
            contacts.setdefault((link["record_type"], link["record_id"]), []).append(
                (key, msg["sent_at"])
            )
        for link in self._of("crm_note_links", workspace_id):
            note = self.rows["crm_notes"].get(link["note_id"])
            on = note and note.get("occurred_on")
            if (
                link["record_type"] not in ("company", "person")
                or not on
                or note.get("kind") != "meeting"
                or note.get("deleted_at")
                or not since.date() <= date.fromisoformat(str(on)) <= today
            ):
                continue
            contacts.setdefault((link["record_type"], link["record_id"]), []).append(
                ("meeting", str(on))
            )

        def latest(found: list[tuple[str, Any]]) -> dict[str, Any]:
            out: dict[str, Any] = {"in": None, "out": None, "meeting": None}
            for key, value in found:
                rank = (lambda v: v) if key == "meeting" else _at
                if out[key] is None or rank(value) > rank(out[key]):
                    out[key] = value
            return out

        people = [p for p in self._of("crm_people", workspace_id) if not p.get("deleted_at")]
        companies = [
            c
            for c in self._of("crm_companies", workspace_id)
            if not c.get("deleted_at") and not c.get("merged_into_id")
        ]
        return {
            "company": {
                c["id"]: latest(
                    contacts.get(("company", c["id"]), [])
                    + [
                        f
                        for p in people
                        if p.get("company_id") == c["id"]
                        for f in contacts.get(("person", p["id"]), [])
                    ]
                )
                for c in companies
            },
            "person": {p["id"]: latest(contacts.get(("person", p["id"]), [])) for p in people},
        }

    def set_contact_recency(self, workspace_id: str, table: str, rows: list[dict]) -> int:
        """067 crm_set_contact_recency: only `contact_recency` changes; no
        audit row, `updated_at` and `updated_by` untouched."""
        assert table in ("crm_companies", "crm_people"), table
        changed = 0
        for r in rows:
            row = self.rows[table].get(r["id"])
            if row is None or row["workspace_id"] != workspace_id:
                continue
            if row.get("contact_recency") != r["level"]:
                changed += 1
                row["contact_recency"] = r["level"]
        self.recency_writes.append((table, len(rows)))
        return changed


#: 064's failure reasons (FR-CRMAI-ENR-06, FR-CRMAI-LLM-03).
ENRICHMENT_REASONS = (
    "Website did not respond",
    "No company information found",
    "Output did not match the schema",
    "Model call failed",
    "Model call timed out",
    "AI budget reached",
)


def _captured(row: dict[str, Any], *, open_only: bool = False) -> bool:
    """A capture item (074): a new lead or deal signal from an email thread
    keyed `capture:` or, made before 074, `gmail:`."""
    return (
        row["type"] in ("new_lead", "deal_signal")
        and row["source_kind"] == "email_thread"
        and (not open_only or row["status"] == "open")
        and (open_only or str(row["source_key"]).startswith(("capture:", "gmail:")))
    )


def _cap_entries(row: dict[str, Any]) -> list[dict[str, Any]]:
    """074 crm_capture_entries."""
    link = row.get("source_link") or {}
    if isinstance(link.get("threads"), list):
        return json.loads(json.dumps(link["threads"]))
    if not str(row.get("source_key") or "").startswith("gmail:"):
        return []
    s = row.get("suggestion") or {}
    ev = s.get("evidence") or {}
    return [
        {
            "ref": row["source_key"],
            "mailbox_id": link.get("mailbox_id"),
            "thread_id": link.get("thread_id"),
            "message_id": row.get("source_version"),
            "first_at": ev.get("first_at") or ev.get("message_at"),
            "latest_at": ev.get("latest_at") or ev.get("message_at"),
            "message_count": ev.get("message_count") or 0,
            "owner_replied": ev.get("owner_replied"),
            "origin": ev.get("origin") or "discovery",
            "reason": ev.get("reason"),
            "confidence": ev.get("confidence"),
            "person_email": s.get("email") or ev.get("email"),
            "first_name": s.get("first_name"),
            "last_name": s.get("last_name"),
        }
    ]


def _cap_when(value: Any) -> datetime:
    return _at(value) if value else datetime.min.replace(tzinfo=UTC)


def _cap_newest(threads: list[dict[str, Any]]) -> dict[str, Any]:
    """074 crm_capture_newest: the greatest (latest_at, message_id)."""
    return max(
        threads,
        key=lambda e: (
            _cap_when(e.get("latest_at")),
            e.get("message_id") is not None,
            str(e.get("message_id") or ""),
        ),
    )


def _cap_merge(threads: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """074 crm_capture_merge: unique by ref, never replaced by an older one;
    a deal document's entry stays one whichever is kept (PD-39)."""
    if not any(e["ref"] == entry["ref"] for e in threads):
        return [*threads, entry]
    out = []
    for e in threads:
        if e["ref"] != entry["ref"]:
            out.append(e)
            continue
        won = entry if _cap_when(entry.get("latest_at")) >= _cap_when(e.get("latest_at")) else e
        if "deal_document" in (e.get("origin"), entry.get("origin")):
            won = {**won, "origin": "deal_document"}
        out.append(won)
    return out


def _cap_same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """The same conversation state: ref and latest message (074's
    `@> {ref, message_id}`)."""
    return a.get("ref") == b.get("ref") and (
        a.get("message_id") is None or a.get("message_id") == b.get("message_id")
    )


def _cap_person(
    kind: str, suggestion: dict[str, Any], entries: list[dict[str, Any]], kept: list[dict]
) -> dict[str, Any]:
    """074 crm_capture_person: a new lead's person follows its newest entry
    when conversations leave (FR-CAP-GRP-03); the title is dropped."""
    if kind != "new_lead" or not kept or _cap_newest(kept) == _cap_newest(entries):
        return suggestion
    n = _cap_newest(kept)
    out = {
        k: v
        for k, v in (suggestion or {}).items()
        if k not in ("email", "first_name", "last_name", "title")
    }
    for key, field in (
        ("email", "person_email"),
        ("first_name", "first_name"),
        ("last_name", "last_name"),
    ):
        if n.get(field) is not None:
            out[key] = n[field]
    return out


def _cap_evidence(evidence: dict[str, Any] | None, threads: list[dict[str, Any]]) -> dict:
    """074 crm_capture_evidence: the totals."""
    out = dict(evidence or {})
    if not threads:
        return out
    firsts = [e for e in threads if e.get("first_at")]
    latests = [e for e in threads if e.get("latest_at")]
    said = [e["owner_replied"] for e in threads if e.get("owner_replied") is not None]
    newest = _cap_newest(threads)
    out.update(
        first_at=min(firsts, key=lambda e: _at(e["first_at"]))["first_at"] if firsts else None,
        latest_at=max(latests, key=lambda e: _at(e["latest_at"]))["latest_at"] if latests else None,
        message_count=sum(int(e.get("message_count") or 0) for e in threads),
        conversations=len(threads),
        owner_replied=True if any(said) else (False if said else None),
        reason=newest.get("reason"),
        confidence=newest.get("confidence"),
    )
    return out


def _cap_link(link: dict[str, Any], threads: list[dict[str, Any]]) -> dict[str, Any]:
    """074 crm_capture_link: the list, the newest entry's ids on top."""
    out = {k: v for k, v in (link or {}).items() if k not in ("threads", "mailbox_id", "thread_id")}
    if threads:
        newest = _cap_newest(threads)
        if newest.get("mailbox_id") is not None:
            out.update(mailbox_id=newest["mailbox_id"], thread_id=newest["thread_id"])
    else:
        out.update({k: v for k, v in (link or {}).items() if k != "threads"})
    out["threads"] = json.loads(json.dumps(threads))
    return out


def _at(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _trigrams(text: str) -> set[str]:
    """pg_trgm's trigrams: each lower-cased word padded with two spaces before
    and one after."""
    grams: set[str] = set()
    for word in "".join(c if c.isalnum() else " " for c in text.lower()).split():
        padded = f"  {word} "
        grams |= {padded[i : i + 3] for i in range(len(padded) - 2)}
    return grams


def trigram_similarity(a: str, b: str) -> float:
    x, y = _trigrams(a), _trigrams(b)
    return len(x & y) / len(x | y) if x | y else 0.0


# ---------------------------------------------------------------------------
# crm_query (034) in Python
# ---------------------------------------------------------------------------

_QUERY_TABLES = {
    "company": "crm_companies",
    "person": "crm_people",
    "opportunity": "crm_opportunities",
    "task": "tasks",
    "note": "crm_notes",
    "campaign": "crm_campaigns",
    "campaign_member": "crm_campaign_members",
}
_AMOUNT = "Viewers cannot filter, sort or total by amount."
_IDS = ("select", "person", "relation", "fixed_select", "flag")


def touched_tasks(ops: list[dict[str, Any]], done: list[dict[str, Any]]) -> set[str]:
    """The tasks a call wrote or whose links it wrote: the ones 047's
    deferred check looks at when the call commits."""
    found: set[str] = set()
    for op, result in zip(ops, done, strict=True):
        if op["table"] == "tasks":
            found.add(result["id"])
        elif op["table"] == "task_links":
            found.add((result.get("row") or {}).get("task_id") or "")
    return found - {""}


def _refuse(message: str, code: str = "CRM22") -> Exception:
    return store.refusal_for(code, message)


def _like(text: str, pattern: str) -> bool:
    """LIKE with ESCAPE '\\', as Postgres matches it."""
    out, chars = [], iter(pattern)
    for c in chars:
        if c == "\\":
            out.append(re.escape(next(chars, "")))
        elif c == "%":
            out.append(".*")
        elif c == "_":
            out.append(".")
        else:
            out.append(re.escape(c))
    return re.fullmatch("".join(out), text, re.DOTALL) is not None


def _midnight(day: date, zone: Any = UTC) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=zone)


_STAGES = (
    "discovery",
    "qualification",
    "proposal",
    "negotiation",
    "closed_won",
    "closed_lost",
)
#: Each fixed list in the order 026 seeds it (crm_query_rank).
_FIXED_ORDER = {
    "lead_stage": (
        "new",
        "contacted",
        "nurturing",
        "qualified",
        "deal_open",
        "customer",
        "partner",
        "disqualified",
    ),
    "stage": _STAGES,
    "lost_from_stage": _STAGES,
    # 041: a task's statuses, then a campaign member's (one array); 048: the
    # task statuses of 008.
    "status": (
        "open",
        "waiting",
        "done",
        "cancelled",
        "targeted",
        "invited",
        "registered",
        "attended",
        "engaged",
        "meeting_booked",
        "converted",
        "not_interested",
        "unsubscribed",
    ),
    "bucket": ("today", "this_week", "this_month", "later"),
    # 048 (data-model §3.3): most urgent first.
    "priority": ("urgent", "high", "normal", "low"),
}


class FakeQuery:
    def __init__(self, fake: FakeCrmStore, workspace_id: str, kind: str, spec: dict, redact: bool):
        self.fake, self.ws, self.kind, self.spec, self.redact = (
            fake,
            workspace_id,
            kind,
            spec,
            redact,
        )

    # -- values ---------------------------------------------------------------

    def raw(self, f: dict[str, Any], row: dict[str, Any]) -> Any:
        col = f["column_sql"]
        if col == "weighted_amount":
            if row.get("amount_in_inr") is None or row.get("probability") is None:
                return None
            value = Decimal(str(row["amount_in_inr"])) * int(row["probability"]) / 100
            return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if col == "full_name":
            return f"{row['first_name']} {row.get('last_name') or ''}".strip()
        if col == "primary_domain":
            return next(
                (
                    d["domain"]
                    for d in self.fake.rows["crm_company_domains"].values()
                    if d["company_id"] == row["id"] and d["is_primary"]
                ),
                None,
            )
        return row.get(col)

    def typed(self, kind: str, value: Any) -> Any:
        if value is None:
            return None
        if kind in ("number", "currency", "numeric"):
            return Decimal(str(value))
        if kind == "date":
            return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        if kind in ("datetime", "timestamptz"):
            return value if isinstance(value, datetime) else _at(value)
        if kind == "boolean":
            return value if isinstance(value, bool) else str(value).lower() == "true"
        if kind == "integer":
            return int(value)
        if kind == "multi_select":
            return [str(v) for v in value]
        return str(value)

    def value(self, f: dict[str, Any], row: dict[str, Any]) -> Any:
        return self.typed(f["type"], self.raw(f, row))

    def rank(self, f: dict[str, Any], row: dict[str, Any]) -> Any:
        """crm_query_rank: the value in its list's own order."""
        v, typ, col = self.value(f, row), f["type"], f["column_sql"]
        if v is None:
            return None
        if typ == "select":
            option = self.fake.rows["crm_select_options"].get(v)
            return option["position"] if option and option["workspace_id"] == self.ws else None
        if typ == "fixed_select" and col in _FIXED_ORDER:
            order = _FIXED_ORDER[col]
            return order.index(v) + 1 if v in order else None
        if typ == "flag":
            # 038: none, amber, red.
            return ("none", "amber", "red").index(v) + 1 if v in ("none", "amber", "red") else None
        if typ == "person":
            person = self.fake.profiles.get(v) or PEOPLE.get(v)
            return person["display_name"].lower() if person else None
        relations = {
            "company_id": "crm_companies",
            "point_of_contact_id": "crm_people",
            "person_id": "crm_people",
            "campaign_id": "crm_campaigns",
            "converted_opportunity_id": "crm_opportunities",
        }
        if typ == "relation" and col in relations:
            other = self.fake.rows[relations[col]].get(v)
            if other is None or other["workspace_id"] != self.ws:
                return None
            if "first_name" in other:
                return f"{other['first_name']} {other.get('last_name') or ''}".strip().lower()
            return other["name"].lower()
        if typ in ("text", "long_text"):
            return v.lower()
        return v

    @staticmethod
    def rank_type(f: dict[str, Any]) -> str:
        typ = f["type"]
        if typ in ("select", "flag") or (typ == "fixed_select" and f["column_sql"] in _FIXED_ORDER):
            return "integer"
        return {
            "number": "numeric",
            "currency": "numeric",
            "date": "date",
            "datetime": "timestamptz",
            "boolean": "boolean",
        }.get(typ, "text")

    def literal(self, kind: str, value: Any) -> Any:
        if value is None or isinstance(value, (dict, list)):
            raise _refuse("A condition needs a value.")
        try:
            return self.typed(kind, value)
        except (ValueError, InvalidOperation):
            raise _refuse("A condition's value does not fit its field.") from None

    # -- filters --------------------------------------------------------------

    def cond(self, node: Any, depth: int):
        if depth > 8:
            raise _refuse("Filters are nested too deeply.")
        if not isinstance(node, dict):
            raise _refuse("A filter is a condition or a group of them.")
        if "items" in node:
            joiner = node.get("op", "and")
            if joiner not in ("and", "or") or not isinstance(node["items"], list):
                raise _refuse("A filter group joins its conditions with and or or.")
            parts = [self.cond(i, depth + 1) for i in node["items"]]
            if not parts:
                return lambda r: True
            if joiner == "and":
                return lambda r: all(p(r) for p in parts)
            return lambda r: any(p(r) for p in parts)
        f = self.fields.get(node.get("field"))
        if f is None or not f["filterable"]:
            raise _refuse("That field cannot be filtered on.")
        if self.redact and f["is_amount"]:
            raise _refuse(_AMOUNT)
        typ, op, given = f["type"], node.get("operator"), node.get("value")
        get = functools.partial(self.value, f)

        if op in ("is_empty", "is_not_empty"):

            def empty(r):
                v = get(r)
                return not v if typ in ("multi_select", "text", "long_text") else v is None

            return empty if op == "is_empty" else (lambda r: not empty(r))
        if op in ("any_of", "none_of"):
            if typ not in (*_IDS, "multi_select") or not isinstance(given, list):
                raise _refuse("Any of and none of take a list of options.")
            wanted = {str(v) for v in given}

            def hit(r):
                v = get(r)
                return bool(set(v or []) & wanted) if typ == "multi_select" else v in wanted

            return hit if op == "any_of" else (lambda r: not hit(r))
        if op in ("within_last_days", "within_next_days"):
            if (
                typ not in ("date", "datetime")
                or isinstance(given, bool)
                or not isinstance(given, int)
                or not 0 <= given <= 3650
            ):
                raise _refuse("Within takes a date field and a number of days from 0 to 3650.")
            lo, hi = (
                (self.today - timedelta(days=given), self.today + timedelta(days=1))
                if op == "within_last_days"
                else (self.today, self.today + timedelta(days=given + 1))
            )
            return lambda r: self._day_between(get(r), typ, lo, hi)
        if typ == "multi_select":
            raise _refuse("That operator does not fit the field.")
        if op in ("contains", "not_contains"):
            if typ not in ("text", "long_text"):
                raise _refuse("That operator does not fit the field.")
            pattern = "%" + str(self.literal(typ, given)).lower() + "%"

            def has(r):
                v = get(r)
                return v is not None and _like(v.lower(), pattern)

            return has if op == "contains" else (lambda r: not has(r))
        if op in ("is", "is_not"):
            if typ in ("date", "datetime"):
                raise _refuse("That operator does not fit the field.")
            want = self.literal(typ, given)
            if typ in ("text", "long_text"):
                want = want.lower()

            def same(r):
                v = get(r)
                if v is not None and typ in ("text", "long_text"):
                    v = v.lower()
                return v is not None and v == want

            if op == "is":
                return same
            return lambda r: not same(r) if get(r) is not None else want is not None
        if op in ("before", "after", "on") and typ in ("date", "datetime"):
            day = self.literal("date", given)
            if op == "before":
                return lambda r: self._day_between(get(r), typ, date.min, day)
            if op == "after":
                return lambda r: self._day_between(get(r), typ, day + timedelta(days=1), date.max)
            return lambda r: self._day_between(get(r), typ, day, day + timedelta(days=1))
        if op in ("gt", "gte", "lt", "lte") and typ in ("number", "currency", "date", "datetime"):
            want = self.literal("date" if typ == "datetime" else typ, given)
            if typ == "datetime":
                want = _midnight(want, self.tz)
            compare = {
                "gt": lambda a, b: a > b,
                "gte": lambda a, b: a >= b,
                "lt": lambda a, b: a < b,
                "lte": lambda a, b: a <= b,
            }[op]
            return lambda r: get(r) is not None and compare(get(r), want)
        raise _refuse("That operator does not fit the field.")

    def _day_between(self, value: Any, typ: str, lo: date, hi: date) -> bool:
        """lo <= value < hi, a timestamp read from midnight in the person's
        time zone."""
        if value is None:
            return False
        if typ == "datetime":
            low = _midnight(lo, self.tz) if lo != date.min else None
            high = _midnight(hi, self.tz) if hi != date.max else None
            return (low is None or value >= low) and (high is None or value < high)
        return lo <= value < hi

    # -- the query --------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        table = _QUERY_TABLES.get(self.kind)
        if table is None:
            raise _refuse("There is no such list.")
        workspace = self.fake.rows["crm_workspaces"].get(self.ws)
        if workspace is None:
            raise _refuse("No such workspace.", "CRM04")
        linked = bool(workspace.get("tva_linked"))
        self.fields = {
            f["field"]: f
            for f in OBJECT_FIELDS
            if f["object"] == self.kind
            and (f["workspace_scope"] == "all" or (f["workspace_scope"] == "tva_linked") == linked)
        }
        hidden = {f["column_sql"] for f in self.fields.values() if f["is_amount"]}
        spec = self.spec
        try:
            self.today = date.fromisoformat(spec["today"]) if spec.get("today") else date.today()
            self.tz = ZoneInfo(spec.get("tz") or "UTC")
            limit = min(max(int(spec.get("limit") or 50), 1), 200)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            raise _refuse("That list request is not valid.") from None

        keep = lambda r: True  # noqa: E731
        if isinstance(spec.get("filters"), dict):
            keep = self.cond(spec["filters"], 1)
        if spec.get("q"):
            pattern = "%" + spec["q"].lower() + "%"
            columns = {
                "company": ("name",),
                "person": ("first_name", "primary_email"),
                "opportunity": ("name",),
                "task": ("title",),
                "note": ("body",),
                "campaign": ("name",),
                "campaign_member": (),
            }[self.kind]
            domains = self.fake.rows["crm_company_domains"].values()

            def found(r):
                texts = [r.get(c) or "" for c in columns]
                if self.kind == "person":
                    texts[0] = f"{r['first_name']} {r.get('last_name') or ''}"
                if self.kind == "company":
                    texts += [d["domain"] for d in domains if d["company_id"] == r["id"]]
                if self.kind == "campaign_member":
                    # 041: by its company's or person's name.
                    company = self.fake.rows["crm_companies"].get(r.get("company_id") or "")
                    person = self.fake.rows["crm_people"].get(r.get("person_id") or "")
                    texts += [company["name"]] if company else []
                    texts += (
                        [f"{person['first_name']} {person.get('last_name') or ''}"]
                        if person
                        else []
                    )
                return any(_like(t.lower(), pattern) for t in texts)

            base_keep = keep
            keep = lambda r: base_keep(r) and found(r)  # noqa: E731

        for column in spec.get("columns") or []:
            name = column.get("field") if isinstance(column, dict) else column
            f = self.fields.get(name) if isinstance(name, str) else None
            if self.redact and f and f["is_amount"]:
                raise _refuse(_AMOUNT)

        # (how a row gives the key, direction, its SQL type for the cursor)
        keys: list[tuple[Any, str, str]] = []
        group = None
        if spec.get("group_by"):
            group = self.fields.get(spec["group_by"])
            if self.redact and group and group["is_amount"]:
                raise _refuse(_AMOUNT)
            if group is None or not group["groupable"]:
                raise _refuse("That field cannot group a list.")
            boolean = group["type"] == "boolean"

            def group_key(r, f=group):
                v = self.value(f, r)
                return v if v is None or boolean else str(v)

            keys.append((functools.partial(self.rank, group), "asc", self.rank_type(group)))
            keys.append((group_key, "asc", "boolean" if boolean else "text"))
        sorts = spec.get("sorts") or [{"field": "updated_at", "dir": "desc"}]
        for sort in sorts:
            f = self.fields.get(sort.get("field"))
            if f is None or not f["sortable"]:
                raise _refuse("That field cannot be sorted on.")
            if self.redact and f["is_amount"]:
                raise _refuse(_AMOUNT)
            if sort.get("dir", "asc") not in ("asc", "desc"):
                raise _refuse("A sort is asc or desc.")
            keys.append(
                (functools.partial(self.rank, f), sort.get("dir", "asc"), self.rank_type(f))
            )

        def key_of(r):
            return [get(r) for get, _, _ in keys]

        def compare(a: list, b: list) -> int:
            for (_, direction, _), x, y in zip(keys, a, b, strict=False):
                if x == y:
                    continue
                if x is None:
                    return 1
                if y is None:
                    return -1
                lower = x < y
                return (-1 if lower else 1) * (1 if direction == "asc" else -1)
            return (a[-1] > b[-1]) - (a[-1] < b[-1])

        aggs = []
        for total in spec.get("aggregates") or []:
            fn = total.get("fn")
            if fn not in ("count", "sum", "avg", "min", "max"):
                raise _refuse("A total is a count, sum, average, minimum or maximum.")
            if total.get("field") is None:
                if fn != "count":
                    raise _refuse("Which field is totalled?")
                aggs.append(("count", None))
                continue
            f = self.fields.get(total["field"])
            if self.redact and f and f["is_amount"]:
                raise _refuse(_AMOUNT)
            if f is None or not (fn == "count" or f["aggregatable"]):
                raise _refuse("That field cannot be totalled.")
            aggs.append((f"{fn}_{f['field']}", f))

        def totals(rows):
            out = {}
            for name, f in aggs:
                if f is None:
                    out[name] = len(rows)
                    continue
                values = [v for v in (self.value(f, r) for r in rows) if v is not None]
                fn = name.split("_", 1)[0]
                if fn == "count":
                    out[name] = len(values)
                elif not values:
                    out[name] = None
                else:
                    out[name] = {
                        "sum": lambda: sum(values, Decimal(0)),
                        "avg": lambda: sum(values, Decimal(0)) / len(values),
                        "min": lambda: min(values),
                        "max": lambda: max(values),
                    }[fn]()
            return out

        live = [
            dict(r)
            for r in self.fake._of(table, self.ws)
            if not r.get("deleted_at") and not (self.kind == "company" and r.get("merged_into_id"))
        ]
        matched = [r for r in live if keep(r)]
        ordered = sorted(
            ((key_of(r) + [r["id"]], r) for r in matched),
            key=functools.cmp_to_key(lambda a, b: compare(a[0], b[0])),
        )
        after = spec.get("after")
        if after is not None:
            if not isinstance(after, list) or len(after) != len(keys) + 1:
                raise _refuse("That page does not belong to this list.")
            try:
                cursor = [
                    self.typed(kind, v) for (_, _, kind), v in zip(keys, after, strict=False)
                ] + [str(after[-1])]
            except (ValueError, InvalidOperation):
                raise _refuse("That page does not belong to this list.") from None
            ordered = [(k, r) for k, r in ordered if compare(k, cursor) > 0]
        page = ordered[: limit + 1]
        rows = [
            {k: v for k, v in r.items() if not (self.redact and k in hidden)}
            for _, r in page[:limit]
        ]
        groups = None
        if group is not None:
            buckets: dict[Any, list] = {}
            for r in matched:
                buckets.setdefault(tuple(key_of(r)[:2]), []).append(r)
            groups = [
                {"key": k[1], "count": len(v), "aggregates": totals(v)}
                for k, v in sorted(
                    buckets.items(),
                    key=lambda kv: (
                        kv[0][0] is None,
                        kv[0][0] if kv[0][0] is not None else 0,
                        kv[0][1] is None,
                        kv[0][1] if kv[0][1] is not None else "",
                    ),
                )
            ]
        return {
            "rows": rows,
            "total": len(matched) if len(matched) <= 10_000 else None,
            "groups": groups,
            "aggregates": totals(matched) if aggs else None,
            "next": page[limit - 1][0] if len(page) > limit else None,
        }


def public_store_names() -> list[str]:
    """Every function the real store exports — what a fake must replace."""
    return [
        name
        for name, value in vars(store).items()
        if inspect.isfunction(value)
        and value.__module__ == store.__name__
        and name[0] != "_"
        and name != "refusal_for"
    ]


def two_workspaces(fake: FakeCrmStore) -> tuple[dict[str, Any], dict[str, Any]]:
    """Labs and Academy with the cast of spec §3.2: the owner admin of both,
    Deepika a Labs member who used Academy last, Devansh a Labs viewer, Tamil
    (CRM-only) a Labs member. Vinita, a TVA admin, is in neither."""
    labs = fake.add_workspace("labs", "Nunnari Labs", tva_linked=True)
    academy = fake.add_workspace("academy", "Nunnari Academy B2B")
    fake.add_member(labs, OWNER["id"], "admin")
    fake.add_member(academy, OWNER["id"], "admin")
    fake.add_member(labs, MEMBER["id"], "member", last_used_at="2026-10-01T09:00:00+00:00")
    fake.add_member(academy, MEMBER["id"], "member", last_used_at="2026-10-10T09:00:00+00:00")
    fake.add_member(labs, VIEWER["id"], "viewer")
    fake.add_member(labs, SELLER["id"], "member")
    return labs, academy


def client(
    monkeypatch, people: dict[str, dict] | None = None, *, fake: FakeCrmStore | None = None
) -> TestClient:
    """Sessions are tokens named after the profile id: 'Bearer u-owner'. The
    real dependency chain runs; only GoTrue and the profile read are faked.
    With `fake`, the store's membership functions share these profiles."""
    rows = {k: dict(v) for k, v in (people or PEOPLE).items()}
    if fake is not None:
        fake.profiles = rows
    monkeypatch.setattr(
        db,
        "get_profile_by_email_ignoring_case",
        lambda email: next(
            (dict(p) for p in rows.values() if p["email"].lower() == email.strip().lower()), None
        ),
    )
    monkeypatch.setattr(deps, "_verify", lambda token: token)
    monkeypatch.setattr(db, "get_profile", lambda uid: dict(rows[uid]) if uid in rows else None)
    monkeypatch.setattr(
        db,
        "list_profiles",
        lambda *, active_only=False, include_crm_only=False: [
            dict(p)
            for p in rows.values()
            if include_crm_only or p.get("account_type", "staff") == "staff"
        ],
    )
    return TestClient(app)


def as_(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}
