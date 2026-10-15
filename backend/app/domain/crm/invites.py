"""Who may be invited where, and what an invitation does — spec 007
FR-CRM-ACC-04 to -09, -12, -13, -15; owner decision D11; plan §5.13.

Pure: the person (or None), the workspace and who is inviting come in as
arguments. Each rule answers None (allowed) or a `Refusal` whose sentence is
safe to show. `invite_plan` says what `crm_invite` (data-model §5.13) must
write; the database re-checks only what a concurrent request could change
(Q-75).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

#: Plan §5.13 — never on an allow-list: anyone can hold an address there, so
#: listing one would let a workspace admin invite the whole internet.
PUBLIC_WEBMAIL_DOMAINS: Final = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "yahoo.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
    }
)

#: FR-CRM-ACC-15 — DeepWeaver sells from Sydney.
SYDNEY_DOMAINS: Final = frozenset({"deepweaver.ai"})

INACTIVE = "This person's portal account is inactive; ask a TVA admin."
PROTECTED = "Ask a TVA admin to change this membership."
BYPASS_STAFF_ONLY = "The allow-list can be bypassed only for an existing staff account."
GOOGLE_NOTE = (
    "They sign in with Google as {email}. An address that is not a Google account "
    "cannot sign in, and there is no password to send."
)

_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass(frozen=True)
class Refusal:
    status: int
    detail: str


def domain_of(email: str) -> str:
    return email.strip().lower().rpartition("@")[2]


def default_timezone(email: str) -> str:
    """FR-CRM-ACC-15 — Sydney for a deepweaver.ai invitee, else Kolkata."""
    return "Australia/Sydney" if domain_of(email) in SYDNEY_DOMAINS else "Asia/Kolkata"


def _either(domains: list[str]) -> str:
    if len(domains) == 1:
        return domains[0]
    return f"{', '.join(domains[:-1])} or {domains[-1]}"


def allow_list_refusal(email: str, workspace: dict[str, Any]) -> Refusal | None:
    """FR-CRM-ACC-05, -06 — checked when the invitation is made, never at
    sign-in (`001` FR-AUTH-10 unchanged)."""
    allowed = [d.lower() for d in workspace.get("allowed_domains") or []]
    if domain_of(email) in allowed:
        return None
    if not allowed:
        return Refusal(403, f"{workspace['name']} has no allowed domains; ask a TVA admin.")
    return Refusal(
        403,
        f"Only addresses at {_either(allowed)} can be invited to {workspace['name']}.",
    )


def invite_refusal(
    *,
    email: str,
    workspace: dict[str, Any],
    person: dict[str, Any] | None,
    by_tva_admin: bool,
    bypass_allow_list: bool = False,
    already_member: bool = False,
) -> Refusal | None:
    """FR-CRM-ACC-04, -05, Q-75, in the order a person would want to hear them.

    `person` is the profile the address already has, or None. Only a TVA
    admin may bypass the allow-list, and only for an existing staff account
    (such as a firstname.nl@gmail.com colleague).
    """
    if bypass_allow_list:
        if not by_tva_admin:
            return Refusal(403, "Only a TVA admin can bypass the allow-list.")
        if person is None or person.get("account_type", "staff") != "staff":
            return Refusal(403, BYPASS_STAFF_ONLY)
    elif why := allow_list_refusal(email, workspace):
        return why

    if already_member:
        return Refusal(409, "This person is already a member of this workspace.")
    if person is not None and not person.get("is_active", True):
        if person.get("account_type", "staff") != "crm_only":
            return Refusal(422, INACTIVE)
        if person.get("deactivated_reason") != "crm_no_membership" and not by_tva_admin:
            return Refusal(422, INACTIVE)
    return None


def invite_plan(
    *,
    user_id: str,
    email: str,
    display_name: str,
    role: str,
    person: dict[str, Any] | None,
    timezone: str | None,
) -> dict[str, Any]:
    """The `p_plan` of `crm_invite` (data-model §5.13). A new account is
    CRM-only with time logging off and no lead (FR-CRM-ACC-04); an existing
    one gets the membership only, reactivated first if it was inactive. The
    time zone is set only on an account this invitation creates: an existing
    person's preferences are theirs."""
    return {
        "user_id": user_id,
        "email": email.strip().lower(),
        "display_name": display_name.strip(),
        "role": role,
        "create_profile": person is None,
        "reactivate": person is not None and not person.get("is_active", True),
        "timezone": timezone or default_timezone(email),
    }


def membership_change_refusal(person: dict[str, Any] | None, *, by_tva_admin: bool) -> str | None:
    """FR-CRM-ACC-09, plan §5.13 — the owner's and a TVA admin's memberships
    are changed only by a TVA admin."""
    if by_tva_admin or person is None:
        return None
    if person.get("is_owner") is True:
        return PROTECTED
    if person.get("role") == "admin" and person.get("account_type", "staff") == "staff":
        return PROTECTED
    return None


def allowed_domains(domains: list[str]) -> tuple[list[str], str | None]:
    """FR-CRM-ACC-06 — lower-cased and de-duplicated in the order given, or a
    refusal naming the first bad entry."""
    cleaned: list[str] = []
    for raw in domains:
        domain = raw.strip().lower().lstrip("@")
        if not domain:
            continue
        if domain in PUBLIC_WEBMAIL_DOMAINS:
            return [], f"{domain} is a public email domain and cannot be on an allow-list."
        if not _DOMAIN.match(domain):
            return [], f"{raw.strip()} is not a domain name."
        if domain not in cleaned:
            cleaned.append(domain)
    return cleaned, None
