"""Who decides a booking — FR-APPR-02, FR-APPR-05, and spec Q-05.

Q-05 ("who approves a lead's own leave, and who covers an absent lead?") had no
provisional answer in the source specification. The rule below is our default,
recorded in the spec so it can be overruled rather than discovered.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Person:
    """Just enough of a profile to answer an authorisation question."""

    id: str
    role: str
    lead_id: str | None
    is_active: bool = True


def approver_for(person: Person) -> str | None:
    """The user id that must decide this person's bookings.

    `None` means "no specific person — it falls to any admin", which is the
    case for a lead and for the admin themselves. That is the Q-05 default: a
    lead's leave goes upward to an admin rather than sideways to a peer, and
    nobody approves their own leave.
    """
    return person.lead_id


def can_decide(actor: Person, subject: Person) -> bool:
    """May `actor` approve or reject `subject`'s booking?

    FR-APPR-05: a lead MUST NOT act on bookings outside their own reports; an
    admin MAY act on any.

    The self-approval bar is the load-bearing clause. Without it a lead with no
    lead of their own would satisfy "an admin may act on any" the moment they
    were also made an admin, and the approval step would quietly become a
    formality for exactly the people whose absence is hardest to cover.
    """
    if not actor.is_active:
        return False
    if actor.id == subject.id:
        return False
    if actor.role == "admin":
        return True
    return actor.role == "lead" and subject.lead_id == actor.id


def can_view_reason(actor: Person, subject: Person) -> bool:
    """NFR-05 — reasons are readable by the person, their lead, and admins.

    Deliberately separate from `can_decide`: an admin may decide any booking,
    and may also read any reason, but those are two different permissions and
    conflating them makes it easy to widen one while meaning to widen the
    other. Q-06 further restricts *where* a reason is shown; this answers only
    whether the viewer is entitled to it at all.
    """
    if not actor.is_active:
        return False
    if actor.id == subject.id:
        return True
    if actor.role == "admin":
        return True
    return actor.role == "lead" and subject.lead_id == actor.id
