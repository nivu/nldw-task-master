"""Meeting capture's rules — spec 009 FR-CRMAI-MEET-02 to -06, FR-CRMAI-SUM-12;
Q-10, Q-11, Q-26, Q-37; data-model §3.2.1, §3.4; plan §5.6.

Pure. Who at a meeting is internal (an address in the workspace's own
domains) and which external attendees are the workspace's people
(`match`), where a meeting belongs (`placement`: nowhere, in several
workspaces, or in exactly one), which deal its note links to
(`resolve_deal`), the prompt's parts, the note's plain-text body with its
fixed headings (`note_body`), and the Review Queue items a meeting makes
(`items`): one `task_from_meeting` per action item, a `next_step_change`
when the linked deal's next step differs, and a `deal_link` asking which
deal when the note could not be linked to one (FR-CRMAI-MEET-04, -06).

Matching uses attendee metadata only; nothing here sees a transcript
until a meeting has been placed in exactly one workspace.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.domain.crm import ai_rules
from app.domain.crm import review as review_rules
from app.domain.crm.email_rules import is_ours
from app.domain.crm.summaries import OPEN_STAGES

#: What the job records in the usage log and the job log.
JOB: Final = "meetings"
#: The phase switch it runs behind (FR-CRMAI-FLAG-01).
PHASE: Final = "2"
SOURCE_KIND: Final = "meeting"
#: The run is skipped with this while no Fireflies key is set (C5).
NOT_CONNECTED: Final = "Fireflies is not connected"
#: A meeting is dated on the CRM's default day (Asia/Kolkata).
ZONE: Final = ZoneInfo("Asia/Kolkata")
#: Stands for the note's id in a `deal_link` item until the note exists;
#: `crm_store_meeting_note` (063) puts the id in its place.
NOTE_REF: Final = "$note"
#: The most attendees a note's body lists.
ATTENDEES_SHOWN: Final = 50

#: The job's instruction, after the fixed rules every prompt carries.
INSTRUCTION: Final = (
    "Write the meeting note for one recorded call between Nunnari and a client. "
    "You are given the Nunnari team list, the meeting's title, Fireflies' own summary "
    "and the transcript. Answer with: summary, at most five short plain lines on what "
    "was discussed; decisions, what was decided, each paraphrased in at most 200 "
    "characters (may be empty); action_items, what someone agreed to do, each "
    "paraphrased in at most 200 characters, with owner as the number of the Nunnari "
    "team member who took it from the team list, or unknown, and due_on as a date "
    "only when one was stated, else null; next_step and next_step_on, the next step "
    "with the client and its date only when the meeting agreed one, else null. "
    "Paraphrase; never quote the transcript."
)


# ---------------------------------------------------------------------------
# Matching — FR-CRMAI-MEET-02, -03; Q-10, Q-26
# ---------------------------------------------------------------------------


def external(attendees: Iterable[str], our_domains: Iterable[str]) -> list[str]:
    """The attendees outside the workspace's own domains, lower-cased."""
    domains = list(our_domains)
    return sorted({a.strip().lower() for a in attendees if a and not is_ours(a, domains)})


def _emails(person: Mapping[str, Any]) -> set[str]:
    found = [person.get("primary_email"), *(person.get("additional_emails") or [])]
    return {str(e).strip().lower() for e in found if e}


def match(
    attendees: Iterable[str], our_domains: Iterable[str], people: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """The workspace's people (not in the Trash) whose primary or additional
    email is an external attendee's. An internal attendee never matches, and
    an unknown one creates nothing (Q-10)."""
    outside = set(external(attendees, our_domains))
    return sorted(
        (p for p in people if not p.get("deleted_at") and _emails(p) & outside),
        key=lambda p: p["id"],
    )


def placement(matches: Mapping[str, Sequence[Any]]) -> tuple[str, str | None]:
    """Where a meeting belongs, from each workspace's matched people:
    `("none", None)`, `("several", None)` (Q-26: processed nowhere) or
    `("one", workspace_id)`."""
    found = sorted(ws for ws, people in matches.items() if people)
    if not found:
        return "none", None
    if len(found) > 1:
        return "several", None
    return "one", found[0]


# ---------------------------------------------------------------------------
# The deal — FR-CRMAI-MEET-04
# ---------------------------------------------------------------------------


def open_deals(deals: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        (d for d in deals if not d.get("deleted_at") and d.get("stage") in OPEN_STAGES),
        key=lambda d: d["id"],
    )


def resolve_deal(
    deals: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]]]:
    """`(deal, choices)` from the open deals of the matched people's
    companies: the one deal when there is exactly one across them, else
    None with every open deal as a choice for the `deal_link` item. Read
    strictly, so a second company's deal never leaves the note on the
    wrong one."""
    live = open_deals(deals)
    if len(live) == 1:
        return live[0], []
    return None, live


# ---------------------------------------------------------------------------
# The prompt — FR-CRMAI-LLM-04, FR-CRMAI-SUM-12
# ---------------------------------------------------------------------------


def prompt_parts(
    title: str, content: Mapping[str, Any], team: Sequence[Mapping[str, Any]]
) -> list[tuple[str, str]]:
    """The quoted parts of the prompt: the team list the model names owners
    from, the title, Fireflies' summary and the transcript's sentences."""
    listed = "\n".join(f"{i}. {p['name']} <{p['email']}>" for i, p in enumerate(team, 1))
    said = "\n".join(
        f"{s.get('speaker') or 'Speaker'}: {s.get('text') or ''}"
        for s in content.get("sentences") or []
    )
    fireflies = "\n".join(
        part for part in (content.get("overview"), content.get("action_items")) if part
    )
    return [
        ("Nunnari team", listed or "(nobody)"),
        ("Meeting title", title or "(untitled)"),
        ("Fireflies summary", fireflies or "(none)"),
        ("Transcript", said or "(empty)"),
    ]


def action_items(
    answered: Sequence[Mapping[str, Any]], team: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The action items as stored: the owner a member id of this workspace,
    or unknown (FR-CRMAI-SUM-12)."""
    out = []
    for a in answered:
        owner = ai_rules.resolve_owner(a.get("owner"), team)
        due = a.get("due_on")
        out.append(
            {
                "text": a["text"],
                "owner_id": owner,
                "owner_unknown": owner is None,
                "due_on": due.isoformat() if isinstance(due, date) else due,
            }
        )
    return out


# ---------------------------------------------------------------------------
# The note — FR-CRMAI-MEET-05; Q-11
# ---------------------------------------------------------------------------


def source_ref(transcript_id: str) -> str:
    """Data-model §3.4 `source_ref`, also the items' `source_key`."""
    return f"fireflies:{transcript_id}"


def meeting_day(at: str) -> str:
    """The note's date: the day the meeting started, in Asia/Kolkata."""
    return datetime.fromisoformat(at).astimezone(ZONE).date().isoformat()


def transcript_link(url: Any) -> str | None:
    """The Fireflies transcript link, the one URL a note may carry
    (FR-CRMAI-LLM-04), or None."""
    if isinstance(url, str) and ai_rules.allowed_url(url) and "fireflies.ai/view/" in url:
        return url[:500]
    return None


def _outside(text: str) -> str:
    """Invite text as generated text is stored: links dropped, money masked."""
    return ai_rules.mask_money(ai_rules.drop_urls(text))


def note_body(
    *,
    title: str,
    attendees: Sequence[str],
    summary: str,
    decisions: Sequence[str],
    actions: Sequence[Mapping[str, Any]],
    owner_names: Mapping[str, str],
    transcript_url: str | None,
) -> str:
    """The note's plain-text body under fixed headings: the title, then
    Attendees, Summary, Decisions, Action items and Transcript. The title and
    the attendees come from the invite, which an outsider can write: money
    is masked and links dropped in them as in generated text
    (FR-CRMAI-LLM-04, -05), so the transcript link stays the only URL."""
    shown = [_outside(a) for a in list(attendees)[:ATTENDEES_SHOWN]]
    more = len(attendees) - len(shown)
    lines = [f"Meeting: {_outside(title).strip() or '(untitled)'}", "", "Attendees"]
    lines += [f"- {a}" for a in shown] or ["- (none listed)"]
    if more > 0:
        lines.append(f"- and {more} more")
    lines += ["", "Summary", summary.strip(), "", "Decisions"]
    lines += [f"- {d}" for d in decisions] or ["- None"]
    lines += ["", "Action items"]
    for a in actions:
        owner = owner_names.get(a.get("owner_id") or "") or "unknown"
        due = f"; due {a['due_on']}" if a.get("due_on") else ""
        lines.append(f"- {a['text']} (owner: {owner}{due})")
    if not actions:
        lines.append("- None")
    lines += ["", "Transcript", transcript_url or "(no link)"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Review Queue items — FR-CRMAI-MEET-04, -06; data-model §3.2.1
# ---------------------------------------------------------------------------


def _day(value: Any) -> str | None:
    if value in ("", None):
        return None
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def items(
    *,
    transcript_id: str,
    version: str,
    model: str,
    generated_at: str,
    next_step: str | None,
    next_step_on: Any,
    actions: Sequence[Mapping[str, Any]],
    deal: Mapping[str, Any] | None,
    choices: Sequence[Mapping[str, Any]],
    companies: Sequence[Mapping[str, Any]],
    people: Sequence[Mapping[str, Any]],
    roles: Mapping[str, str],
    active: set[str],
) -> list[dict[str, Any]]:
    """The items one meeting makes, as `crm_store_meeting_note` takes them
    (it points each at the note).

    `deal` is the note's one deal (`id, owner_id, company_id, next_step,
    next_step_on`) or None, `choices` the open deals to pick from when
    there is none, `companies` and `people` the matched live companies and
    people (`id, owner_id`). `version` is the transcript's last update."""
    key = source_ref(transcript_id)
    common = {
        "source_kind": SOURCE_KIND,
        "source_key": key,
        "source_link": {},
        "model": model,
        "generated_at": generated_at,
    }
    companies = sorted(companies, key=lambda c: c["id"])
    company_owners = {c["id"]: c.get("owner_id") for c in companies}
    out: list[dict[str, Any]] = []

    # FR-CRMAI-MEET-06: a changed next step on the linked deal.
    suggested = {"next_step": next_step, "next_step_on": _day(next_step_on)}
    if deal is not None and next_step:
        current = {
            "next_step": deal.get("next_step") or None,
            "next_step_on": _day(deal.get("next_step_on")),
        }
        if current != suggested:
            out.append(
                {
                    **common,
                    "type": "next_step_change",
                    "owner_id": review_rules.owner_for(
                        "next_step_change",
                        roles=roles,
                        active=active,
                        deal_owner=deal.get("owner_id"),
                    ),
                    "record_type": "opportunity",
                    "record_id": deal["id"],
                    "suggestion": suggested,
                    "current_values": {deal["id"]: current},
                    "source_version": version,
                    "dedupe_key": review_rules.dedupe_key(
                        SOURCE_KIND, key, "next_step_change", "opportunity", deal["id"]
                    ),
                    "deal_choices": [deal["id"]],
                }
            )

    # FR-CRMAI-MEET-04: no single deal, so ask which one.
    if deal is None and choices:
        first = sorted(choices, key=lambda d: d["id"])[0]
        out.append(
            {
                **common,
                "type": "deal_link",
                "owner_id": review_rules.owner_for(
                    "deal_link",
                    roles=roles,
                    active=active,
                    company_owner=company_owners.get(first.get("company_id") or ""),
                ),
                "record_type": "note",
                "record_id": None,
                "suggestion": {},
                "current_values": {},
                "source_version": version,
                "dedupe_key": review_rules.dedupe_key(
                    SOURCE_KIND, key, "deal_link", "note", NOTE_REF
                ),
                "deal_choices": sorted(d["id"] for d in choices),
            }
        )

    # FR-CRMAI-MEET-06: each action item, on the deal, else the company,
    # else the person the meeting was with.
    if deal is not None:
        target = ("opportunity", deal["id"])
        deal_owner = deal.get("owner_id")
        company_owner = company_owners.get(deal.get("company_id") or "")
    elif companies:
        target, deal_owner = ("company", companies[0]["id"]), None
        company_owner = companies[0].get("owner_id")
    elif people:
        target = ("person", sorted(people, key=lambda p: p["id"])[0]["id"])
        deal_owner, company_owner = None, None
    else:
        return out
    for a in actions:
        # The action's fingerprint is its source version, so an updated
        # transcript does not suggest a decided action again.
        fingerprint = review_rules.commitment_fingerprint(a["text"], a.get("due_on"))
        out.append(
            {
                **common,
                "type": "task_from_meeting",
                "owner_id": review_rules.owner_for(
                    "task_from_meeting",
                    roles=roles,
                    active=active,
                    commitment_owner=a.get("owner_id"),
                    deal_owner=deal_owner,
                    company_owner=company_owner,
                ),
                "record_type": target[0],
                "record_id": target[1],
                "suggestion": {
                    "title": a["text"],
                    "due_on": a.get("due_on"),
                    "owner_id": a.get("owner_id"),
                    "owner_unknown": a.get("owner_id") is None,
                },
                "current_values": {},
                "source_version": fingerprint,
                "dedupe_key": review_rules.dedupe_key(
                    SOURCE_KIND,
                    key,
                    "task_from_meeting",
                    target[0],
                    target[1],
                    fingerprint=fingerprint,
                ),
                "deal_choices": [],
            }
        )
    return out
