"""Follow-up and deal-health flags — spec 007 §6.2, FR-CRM-FLAG-01 to -05;
Q-20; data-model §3.21.

One function per row of the spec's table, each taking the record's facts,
`now` and the workspace's thresholds, and answering one flag or None:

| Flag | Applies to | Red | Amber |
|---|---|---|---|
| Reply owed | person, company, deal | their latest email is newer than ours and older than 24 h | 12–24 h |
| Client silent | open deal | no inbound for more than 3 days after our last outbound | 2–3 days |
| Next step | open deal | next step date past | today or tomorrow |
| Close date | open deal | expected close past | within 7 days |
| Stale deal | open deal | no stage change, note, task or email for 21 days | 14–21 days |
| Loose end | company in Nurturing | no open task and no next touch | — |
| Task overdue | task | due date past | due today |

A record's level is its worst flag; a company's is the worst across its own
flags and its open deals' (FR-CRM-FLAG-03). Emails that are internal-only,
or an auto-reply or bounce, never count (FR-CRM-FLAG-04): the email flags
read only the outside, human messages. Until email sync supplies messages
(1b, FR-CRM-SYNC), they have none and the email flags are none.

Thresholds come from the workspace's `flag_thresholds` setting, defaulting
to the spec's values. Days are counted in the record owner's time zone,
which the service passes as `today`.

Pure: the service passes the rows, facts and clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any, Final

#: Least to worst. The database sorts them in this order too (038).
LEVELS: Final = ("none", "amber", "red")

#: Spec §6.2, data-model §7.3 `flag_thresholds`.
DEFAULT_THRESHOLDS: Final = {
    "reply_owed_red_hours": 24,
    "reply_owed_amber_hours": 12,
    "silent_red_days": 3,
    "silent_amber_days": 2,
    "close_amber_days": 7,
    "stale_red_days": 21,
    "stale_amber_days": 14,
}

#: The flags, by kind, with the label the dot shows on hover (FR-CRM-FLAG-05:
#: "follow-up" or "deal health", never bare "health").
KINDS: Final = {
    "reply_owed": "Reply owed",
    "client_silent": "Client silent",
    "next_step": "Next step due",
    "close_date": "Close date",
    "stale_deal": "Stale deal",
    "loose_end": "Loose end",
    "task_overdue": "Task overdue",
    "deal": "Deal health",
}

OPEN_STAGES: Final = ("discovery", "qualification", "proposal", "negotiation")


def thresholds(setting: Any) -> dict[str, int]:
    """The workspace's thresholds over the defaults; anything malformed is
    ignored for its default (the settings route refuses it on save)."""
    out = dict(DEFAULT_THRESHOLDS)
    if isinstance(setting, dict):
        for key, value in setting.items():
            if key in out and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                out[key] = value
    return out


def thresholds_refusal(value: Any) -> str | None:
    """FR-CRM-LIST-06 — every threshold a whole number, amber before red."""
    if not isinstance(value, dict):
        return "Flag thresholds must give a number for each threshold."
    unknown = sorted(set(value) - set(DEFAULT_THRESHOLDS))
    if unknown:
        return f"Unknown threshold: {', '.join(unknown)}."
    missing = sorted(set(DEFAULT_THRESHOLDS) - set(value))
    if missing:
        return f"Flag thresholds need {', '.join(missing)}."
    bad = sorted(
        k
        for k, v in value.items()
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 365 * 24
    )
    if bad:
        return f"{', '.join(bad)} must be a whole number from 0."
    for amber, red in (
        ("reply_owed_amber_hours", "reply_owed_red_hours"),
        ("silent_amber_days", "silent_red_days"),
        ("stale_amber_days", "stale_red_days"),
    ):
        if value[amber] > value[red]:
            return f"{amber} cannot be more than {red}."
    return None


def _flag(kind: str, level: str, since: Any, detail: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": KINDS[kind],
        "level": level,
        "since": since.isoformat() if isinstance(since, (date, datetime)) else since,
        "detail": detail,
    }


def _at(value: Any) -> datetime | None:
    if value is None:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _day(value: Any) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def worst(flags: Iterable[dict[str, Any] | str | None]) -> str:
    """FR-CRM-FLAG-03 — the worst level among flags (or levels)."""
    level = "none"
    for f in flags:
        this = f if isinstance(f, str) else (f or {}).get("level", "none")
        if LEVELS.index(this) > LEVELS.index(level):
            level = this
    return level


# ---------------------------------------------------------------------------
# Email facts — FR-CRM-FLAG-04
# ---------------------------------------------------------------------------


def counted(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The messages a flag may read: never internal-only, never an
    auto-reply or bounce."""
    return [m for m in messages if not m.get("is_internal") and not m.get("is_auto")]


def _latest(messages: list[dict[str, Any]], direction: str) -> datetime | None:
    times = [_at(m["sent_at"]) for m in messages if m.get("direction") == direction]
    return max((t for t in times if t), default=None)


def reply_owed(
    messages: Iterable[dict[str, Any]], now: datetime, t: dict[str, int]
) -> dict[str, Any] | None:
    """Person, company, deal: their latest email is newer than ours and
    older than `reply_owed_red_hours` (red), or than the amber hours."""
    mine = counted(messages)
    theirs, ours = _latest(mine, "inbound"), _latest(mine, "outbound")
    if theirs is None or (ours is not None and ours >= theirs):
        return None
    age = now - theirs
    if age > timedelta(hours=t["reply_owed_red_hours"]):
        level = "red"
    elif age >= timedelta(hours=t["reply_owed_amber_hours"]):
        level = "amber"
    else:
        return None
    hours = int(age.total_seconds() // 3600)
    return _flag("reply_owed", level, theirs, f"Their email has waited {hours} h for a reply.")


def client_silent(
    deal: dict[str, Any], messages: Iterable[dict[str, Any]], now: datetime, t: dict[str, int]
) -> dict[str, Any] | None:
    """Open deal: no inbound after our last outbound, for more than
    `silent_red_days` (red) or at least `silent_amber_days` (amber)."""
    if deal.get("stage") not in OPEN_STAGES:
        return None
    mine = counted(messages)
    ours, theirs = _latest(mine, "outbound"), _latest(mine, "inbound")
    if ours is None or (theirs is not None and theirs > ours):
        return None
    quiet = now - ours
    if quiet > timedelta(days=t["silent_red_days"]):
        level = "red"
    elif quiet >= timedelta(days=t["silent_amber_days"]):
        level = "amber"
    else:
        return None
    return _flag("client_silent", level, ours, f"No reply for {quiet.days} days since ours.")


# ---------------------------------------------------------------------------
# Deal, company and task facts
# ---------------------------------------------------------------------------


def next_step(deal: dict[str, Any], today: date) -> dict[str, Any] | None:
    """Open deal: the next step's date is past (red), or today or tomorrow
    (amber)."""
    due = _day(deal.get("next_step_on"))
    if deal.get("stage") not in OPEN_STAGES or due is None:
        return None
    if due < today:
        return _flag("next_step", "red", due, f"Next step was due {due.isoformat()}.")
    if due <= today + timedelta(days=1):
        return _flag("next_step", "amber", due, f"Next step is due {due.isoformat()}.")
    return None


def close_date(deal: dict[str, Any], today: date, t: dict[str, int]) -> dict[str, Any] | None:
    """Open deal: expected close is past (red), or within
    `close_amber_days` (amber)."""
    close = _day(deal.get("expected_close_on"))
    if deal.get("stage") not in OPEN_STAGES or close is None:
        return None
    if close < today:
        return _flag("close_date", "red", close, f"Expected to close {close.isoformat()}.")
    if close <= today + timedelta(days=t["close_amber_days"]):
        return _flag("close_date", "amber", close, f"Expected to close {close.isoformat()}.")
    return None


def stale_deal(
    deal: dict[str, Any], last_activity: Any, now: datetime, t: dict[str, int]
) -> dict[str, Any] | None:
    """Open deal: no stage change, note, task or email for `stale_red_days`
    (red) or `stale_amber_days` (amber). `last_activity` is the latest of
    those (the service reads it, and the latest email, into one time)."""
    if deal.get("stage") not in OPEN_STAGES:
        return None
    since = _at(last_activity) or _at(deal.get("stage_entered_at")) or _at(deal.get("created_at"))
    if since is None:
        return None
    idle = now - since
    if idle >= timedelta(days=t["stale_red_days"]):
        level = "red"
    elif idle >= timedelta(days=t["stale_amber_days"]):
        level = "amber"
    else:
        return None
    return _flag("stale_deal", level, since, f"Nothing has happened for {idle.days} days.")


def loose_end(company: dict[str, Any], has_open_task: bool) -> dict[str, Any] | None:
    """Company in Nurturing with no open task and no next touch: red (the
    spec's table has no amber)."""
    if (
        company.get("lead_stage") != "nurturing"
        or company.get("next_touch_on")
        or company.get("deleted_at")
        or has_open_task
    ):
        return None
    return _flag("loose_end", "red", None, "Nurturing, with no open task and no next touch.")


def task_overdue(task: dict[str, Any], today: date) -> dict[str, Any] | None:
    """Open task: due date past (red) or today (amber)."""
    due = _day(task.get("due_on"))
    if task.get("status") == "done" or task.get("deleted_at") or due is None:
        return None
    if due < today:
        return _flag("task_overdue", "red", due, f"Was due {due.isoformat()}.")
    if due == today:
        return _flag("task_overdue", "amber", due, "Due today.")
    return None


# ---------------------------------------------------------------------------
# A record's flags
# ---------------------------------------------------------------------------


def _done(flags: list[dict[str, Any] | None]) -> dict[str, Any]:
    kept = [f for f in flags if f]
    return {"flag_level": worst(kept), "flags": kept}


def for_deal(
    deal: dict[str, Any],
    *,
    now: datetime,
    today: date,
    t: dict[str, int],
    last_activity: Any = None,
    messages: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """`{flag_level, flags}` of a deal. A closed or deleted deal has none."""
    if deal.get("stage") not in OPEN_STAGES or deal.get("deleted_at"):
        return _done([])
    mails = list(messages)
    latest_mail = _latest(counted(mails), "inbound"), _latest(counted(mails), "outbound")
    activity = max((a for a in (_at(last_activity), *latest_mail) if a is not None), default=None)
    return _done(
        [
            reply_owed(mails, now, t),
            client_silent(deal, mails, now, t),
            next_step(deal, today),
            close_date(deal, today, t),
            stale_deal(deal, activity, now, t),
        ]
    )


def for_company(
    company: dict[str, Any],
    *,
    now: datetime,
    t: dict[str, int],
    has_open_task: bool,
    open_deals: Iterable[dict[str, Any]] = (),
    messages: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """`{flag_level, flags}` of a company: its own flags, and one `deal`
    entry per flagged open deal, so its level is the worst of both
    (FR-CRM-FLAG-03). `open_deals` carry their computed `flag_level`."""
    if company.get("deleted_at"):
        return _done([])
    own = [reply_owed(messages, now, t), loose_end(company, has_open_task)]
    deals = [
        {
            "kind": "deal",
            "label": KINDS["deal"],
            "level": d["flag_level"],
            "since": None,
            "detail": d.get("name") or "",
            "record_id": d["id"],
        }
        for d in sorted(open_deals, key=lambda d: (d.get("name") or "", d["id"]))
        if d.get("flag_level", "none") != "none"
    ]
    return _done([*own, *deals])


def for_person(
    person: dict[str, Any], *, now: datetime, t: dict[str, int], messages: Iterable = ()
) -> dict[str, Any]:
    """`{flag_level, flags}` of a person: Reply owed only."""
    if person.get("deleted_at"):
        return _done([])
    return _done([reply_owed(messages, now, t)])


def for_task(task: dict[str, Any], *, today: date) -> dict[str, Any]:
    return _done([task_overdue(task, today)])


def changed(row: dict[str, Any], computed: dict[str, Any]) -> bool:
    """Whether a row's stored flags differ from `computed`."""
    return (
        row.get("flag_level", "none") != computed["flag_level"]
        or (row.get("flags") or []) != computed["flags"]
    )
