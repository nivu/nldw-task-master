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

import inspect
import itertools
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.api import deps
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
    "crm_tasks",
    "crm_task_links",
    "crm_notes",
    "crm_note_links",
    "crm_note_mentions",
)
#: The soft-deletable record tables (data-model §2).
RECORDS = (
    "crm_companies",
    "crm_people",
    "crm_drive_links",
    "crm_opportunities",
    "crm_tasks",
    "crm_notes",
)
#: The audit trigger's noun per table (026 `crm_audit_row`).
NOUNS = {
    "crm_companies": "company",
    "crm_people": "person",
    "crm_opportunities": "opportunity",
    "crm_workspace_settings": "setting",
    "crm_select_options": "option",
}
#: A polymorphic link's record type → its table (027 `crm_check_refs`).
TARGETS = {
    "company": "crm_companies",
    "person": "crm_people",
    "opportunity": "crm_opportunities",
    "task": "crm_tasks",
    "note": "crm_notes",
}
OPEN_STAGES = ("discovery", "qualification", "proposal", "negotiation")


class CheckViolation(AssertionError):
    """A CHECK constraint of 030 that the route should have refused first."""


#: Column defaults the database would fill on insert.
_TRASH = {"deleted_at": None, "deleted_by": None, "deleted_group": None}
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
        "last_inbound_at": None,
        "last_outbound_at": None,
        "last_contact_at": None,
        "position": 0,
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
        **_TRASH,
    },
    "crm_opportunity_contacts": {"role_id": None},
    "crm_tasks": {
        "body": None,
        "status": "todo",
        "due_on": None,
        "bucket": None,
        "category_id": None,
        "completed_at": None,
        "position": 0,
        **_TRASH,
    },
    "crm_notes": {"kind": "note", "occurred_on": None, **_TRASH},
}

_clock = itertools.count(1)


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
                [("todo", "To do"), ("in_progress", "In progress"), ("done", "Done")],
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

    def install(self, monkeypatch) -> FakeCrmStore:
        for name in public_store_names():
            monkeypatch.setattr(store, name, getattr(self, name))
        return self

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
        del self.rows["crm_memberships"][m["id"]]
        self._audit(
            actor, {"action": "membership.removed", "table": "crm_memberships"}, m, dict(m), None
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
                "crm_tasks": ("title",),
                "crm_notes": ("body",),
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
            "crm_tasks": "task",
            "crm_notes": "note",
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
                if kinds[table]:
                    for links in ("crm_drive_links", "crm_task_links", "crm_note_links"):
                        for link in list(self.rows[links].values()):
                            if link["record_type"] == kinds[table] and link["record_id"] == target:
                                self._delete(actor, links, link)
                self._cascade(actor, table, target)
                self._delete(actor, table, row)
        except Exception:
            self.rows, self.audit[audit_len:] = snapshot, []
            raise
        return {"purged": len(items)}

    def _cascade(self, actor, table: str, target: str) -> None:
        """The foreign-key actions a purge sets off (029, 030)."""
        if table == "crm_companies":
            if any(d["company_id"] == target for d in self.rows["crm_opportunities"].values()):
                raise store.refusal_for("23503", "violates foreign key constraint")
            for d in list(self.rows["crm_company_domains"].values()):
                if d["company_id"] == target:
                    self._delete(actor, "crm_company_domains", d)
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
        if table == "crm_tasks":
            for link in list(self.rows["crm_task_links"].values()):
                if link["task_id"] == target:
                    self._delete(actor, "crm_task_links", link)
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

    def _transition(self, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        ws, who = actor["workspace_id"], actor["user_id"]
        if isinstance(plan.get("project"), dict):
            raise store.refusal_for(
                "CRM22", "This move needs the TVA link, which is not built yet."
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
        for task_id in plan.get("close_tasks") or []:
            task = self.rows["crm_tasks"].get(task_id)
            on_deal = any(
                link["task_id"] == task_id
                and link["record_type"] == "opportunity"
                and link["record_id"] == opp["id"]
                for link in self.rows["crm_task_links"].values()
            )
            if task is None or task["workspace_id"] != ws or task.get("deleted_at") or not on_deal:
                raise store.refusal_for("CRM04", "No such task on this deal.")
        for t in plan.get("create_tasks") or []:
            if not any(
                m["workspace_id"] == ws and m["user_id"] == t["assignee_id"]
                for m in self.rows["crm_memberships"].values()
            ):
                raise store.refusal_for(
                    "CRM04", "A task's assignee must be a member of this workspace."
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
            task = self.rows["crm_tasks"][task_id]
            if task["status"] != "done":
                self._update(
                    actor,
                    {"table": "crm_tasks"},
                    task,
                    {"status": "done", "completed_at": _now()},
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
        return {
            "opportunity": dict(opp),
            "company": company_row,
            "tasks_created": [],
            "tasks_closed": closed,
            "note_id": note_id,
        }

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
            if r["target_table"] == table and r["target_id"] == record_id:
                return True
            if r["target_table"] in ("crm_drive_links", "crm_task_links", "crm_note_links") and any(
                x.get("record_id") == record_id and x.get("record_type") == record_type
                for x in sides
            ):
                return True
            if r["target_id"] in (related or {}).get(r["target_table"], []):
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

    # -- store: writes ------------------------------------------------------

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
            # FR-CRM-TASK-01, checked after every op as crm_apply does.
            for op, result in zip(ops, done, strict=True):
                if (
                    op["op"] == "insert"
                    and op["table"] == "crm_tasks"
                    and not any(
                        link["task_id"] == result["id"]
                        for link in self.rows["crm_task_links"].values()
                    )
                ):
                    raise store.refusal_for(
                        "CRM22", "A task must be linked to at least one record."
                    )
            for op, result in zip(ops, done, strict=True):
                row = self.rows.get(op["table"], {}).get(result["id"])
                if row is not None:
                    self._check_rows(op["table"], row)
                    result["row"] = dict(row)
            return done
        except Exception:
            self.rows, self.audit[audit_len:] = snapshot, []
            raise

    def _one(self, actor: dict[str, Any], op: dict[str, Any], refs: dict[str, str]) -> dict:
        ws = actor["workspace_id"]
        table = self.rows.get(op["table"])
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
        if op["op"] == "insert":
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
            self._check_refs(op["table"], row)
            self._check_unique(op["table"], row)
            table[row["id"]] = row
            self._audit(actor, op, row, None, dict(row))
            if op["table"] == "crm_opportunities":
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
        row.update(values, updated_at=_now())
        if "updated_by" not in values:
            row["updated_by"] = actor["user_id"]
        # 026: a change to nothing but bookkeeping writes no audit row.
        if changed and not changed <= {"position"}:
            self._audit(actor, op, row, before, {k: row.get(k) for k in changed})

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
        if table in ("crm_drive_links", "crm_task_links", "crm_note_links"):
            target = TARGETS.get(row["record_type"])
            found = target and self.rows[target].get(row["record_id"])
            if not found or found["workspace_id"] != ws:
                raise store.refusal_for("CRM04", f"No such {row['record_type']} in this workspace.")
        composite = {
            "crm_opportunities": (
                ("company_id", "crm_companies"),
                ("point_of_contact_id", "crm_people"),
            ),
            "crm_opportunity_contacts": (
                ("opportunity_id", "crm_opportunities"),
                ("person_id", "crm_people"),
            ),
            "crm_task_links": (("task_id", "crm_tasks"),),
            "crm_note_links": (("note_id", "crm_notes"),),
            "crm_note_mentions": (("note_id", "crm_notes"),),
        }
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
        """030's CHECK constraints on deals, tasks and notes."""

        def broken(name: str) -> None:
            raise CheckViolation(f"{table} violates {name}: {row}")

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
        if table == "crm_tasks" and (row["status"] == "done") != (
            row.get("completed_at") is not None
        ):
            broken("crm_tasks_completed")
        if (
            table == "crm_notes"
            and row.get("kind", "note") != "note"
            and not row.get("occurred_on")
        ):
            broken("crm_notes_activity_date")

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
            "crm_task_links": (("task_id", "record_type", "record_id"), "crm_task_links_target"),
            "crm_note_links": (("note_id", "record_type", "record_id"), "crm_note_links_target"),
            "crm_note_mentions": (("note_id", "member_id"), "crm_note_mentions_pair"),
        }
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
                "workspace_id": row.get("workspace_id", row["id"]),
                "action": action,
                "target_table": op["table"],
                "target_id": row["id"],
                "before": before,
                "after": after,
            }
        )


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
