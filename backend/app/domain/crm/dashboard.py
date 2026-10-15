"""The pipeline dashboard — spec 007 FR-CRM-DASH-01 to -04, FR-CRM-CAP-02,
FR-CRM-CHG-13; Q-26, Q-39, Q-40; contracts §2.10 `GET /crm/dashboard`.

One fixed dashboard per workspace. Every panel is computed here from rows
the service reads (deals, companies, stage history, flagged records,
activity, targets) and the clock it passes, so a panel's arithmetic lives in
one place:

| Panel | From |
|---|---|
| Open pipeline by stage | open deals: count, amount in INR, weighted (amount × probability) |
| Bookings | deals won this month, quarter and financial year (April–March), against the Admin's target for the workspace and per entity; green, amber or red only where a target exists |
| Won and lost this quarter | closed deals of the quarter, with the lost reasons |
| Win rate and average deal size | deals closed in the date range, by entity and by channel |
| Time in stage, days to close | `crm_stage_history`: each stay that ended in the range; won deals' first stage to their win |
| Follow-up counts | the stored flags (FR-CRM-FLAG), by kind, coloured by the worst level |
| Lead funnel | companies by lead stage, Partner left out, each stage's conversion to the next |
| Pipeline by channel, by source campaign | open deals |
| Activity | emails in and out, notes and completed tasks per owner per week, owners in name order |
| Academy only | pipeline by institution type and by programme (Q-40) |
| Labs only | the capacity check, totals only, a week with fewer than 3 people hidden (Q-39) |

Amounts are INR. Per-owner figures are in name order, never by value, so
the activity panel is not a league table (FR-CRM-DASH-04, Q-26). Pure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.domain.crm import flags as flag_rules
from app.domain.crm import objects
from app.domain.crm.pipeline import CLOSED, OPEN
from app.domain.crm.settings import STAGE_LABELS, STAGES

#: Bookings at or above this share of the target's pace are amber, not red.
AMBER_SHARE: Final = Decimal("0.8")
#: Q-39 — a capacity week supplied by fewer people says nothing.
MIN_CONTRIBUTORS: Final = 3
HIDDEN: Final = "fewer than 3 people"
#: The activity panel shows at most this many weeks, the latest of the range.
ACTIVITY_WEEKS: Final = 13
#: The lead funnel, in order. Partner is left out (FR-CRM-DASH-02); a
#: Disqualified company left the funnel and is counted beside it.
FUNNEL: Final = ("new", "contacted", "nurturing", "qualified", "deal_open", "customer")
#: The flag kinds the dashboard counts, in the spec's order (FR-CRM-DASH-02).
FLAG_PANEL: Final = (
    ("reply_owed", "Replies owed"),
    ("client_silent", "Silent clients"),
    ("next_step", "Overdue next steps"),
    ("close_date", "Overdue close dates"),
    ("stale_deal", "Stale deals"),
    ("loose_end", "Loose ends"),
)
#: Of these two kinds only the red level is overdue; amber is "due soon".
_OVERDUE_ONLY: Final = frozenset({"next_step", "close_date"})
PERIOD_KINDS: Final = ("month", "quarter", "fy")
PERIOD_LABELS: Final = {
    "month": "This month",
    "quarter": "This quarter",
    "fy": "This financial year",
}

_CENT = Decimal("0.01")
_ZONE = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Periods — the financial year runs April to March
# ---------------------------------------------------------------------------


def fy_start(on: date) -> date:
    return date(on.year if on.month >= 4 else on.year - 1, 4, 1)


def quarter_start(on: date) -> date:
    return date(on.year, ((on.month - 1) // 3) * 3 + 1, 1)


def month_start(on: date) -> date:
    return on.replace(day=1)


def period_start(kind: str, on: date) -> date:
    return {"month": month_start, "quarter": quarter_start, "fy": fy_start}[kind](on)


def period_end(kind: str, start: date) -> date:
    """The period's last day."""
    months = {"month": 1, "quarter": 3, "fy": 12}[kind]
    year, month = divmod(start.month - 1 + months, 12)
    return date(start.year + year, month + 1, 1) - timedelta(days=1)


def target_refusal(
    period_kind: str, start: date, entity: str | None, *, has_entity: bool, workspace: str
) -> str | None:
    """A bookings target's period starts on its first day (043), and an
    entity is Labs's (FR-CRM-WS-06)."""
    if entity is not None and not has_entity:
        return f"Entity is not used in {workspace}."
    if start.day != 1:
        return "A target's period starts on the 1st."
    if period_kind == "quarter" and start.month not in (1, 4, 7, 10):
        return "A quarter starts on 1 April, 1 July, 1 October or 1 January."
    if period_kind == "fy" and start.month != 4:
        return "A financial year starts on 1 April."
    return None


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Filters:
    """FR-CRM-DASH-01. `owner_id` is forced to the caller for a Member."""

    entity: str | None = None
    owner_id: str | None = None
    channel_id: str | None = None
    since: date | None = None
    until: date | None = None


@dataclass
class Facts:
    """What the panels read, gathered by the service."""

    today: date
    deals: list[dict[str, Any]]
    companies: list[dict[str, Any]]
    history: list[dict[str, Any]] = field(default_factory=list)
    flagged: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    emails: list[dict[str, Any]] = field(default_factory=list)
    targets: list[dict[str, Any]] = field(default_factory=list)
    #: Option id → label; person id → display name; campaign id → name.
    options: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    campaigns: dict[str, str] = field(default_factory=dict)
    lead_stage_labels: dict[str, str] = field(default_factory=dict)
    stage_labels: dict[str, str] = field(default_factory=dict)
    #: The workspace's members: the activity panel's rows.
    members: list[str] = field(default_factory=list)


def money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(_CENT, rounding=ROUND_HALF_UP), "f")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def _day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _moment(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _local_day(value: Any) -> date:
    """A timestamp's day in the company time zone, for weeks."""
    return _moment(value).astimezone(_ZONE).date()


def _pct(part: int, whole: int) -> str | None:
    if not whole:
        return None
    return format((Decimal(part) * 100 / whole).quantize(Decimal("0.1"), ROUND_HALF_UP), "f")


def _weighted(deal: dict[str, Any]) -> Decimal:
    return _dec(deal.get("amount_in_inr")) * _dec(deal.get("probability")) / 100


def deal_matches(deal: dict[str, Any], f: Filters) -> bool:
    if f.entity and deal.get("entity") != f.entity:
        return False
    if f.owner_id and deal.get("owner_id") != f.owner_id:
        return False
    return not (f.channel_id and deal.get("channel_id") != f.channel_id)


def company_matches(company: dict[str, Any], f: Filters) -> bool:
    if f.entity and f.entity not in (company.get("entities") or []):
        return False
    if f.owner_id and company.get("owner_id") != f.owner_id:
        return False
    return not (f.channel_id and f.channel_id not in (company.get("channel_ids") or []))


def _range(f: Filters, today: date) -> tuple[date, date]:
    """The date range: the filter's, or the financial year to date."""
    return f.since or fy_start(today), f.until or today


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------


def _pipeline_row(deals: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(deals),
        "amount_in_inr": money(sum((_dec(d.get("amount_in_inr")) for d in deals), Decimal(0))),
        "weighted": money(sum((_weighted(d) for d in deals), Decimal(0))),
    }


def pipeline_by_stage(deals: list[dict[str, Any]], facts: Facts) -> dict[str, Any]:
    """Open deals per stage, in pipeline order, and their total."""
    open_deals = [d for d in deals if d["stage"] in OPEN]
    return {
        "stages": [
            {
                "stage": stage,
                "stage_label": facts.stage_labels.get(stage, STAGE_LABELS[stage]),
                **_pipeline_row([d for d in open_deals if d["stage"] == stage]),
            }
            for stage in OPEN
        ],
        "total": _pipeline_row(open_deals),
    }


def colour(booked: Decimal, target: Decimal, start: date, end: date, today: date) -> str:
    """Green at or above the target's pace for the days gone, amber within
    `AMBER_SHARE` of it, red below. A period over is held to the whole
    target; one not begun to none."""
    days = (end - start).days + 1
    gone = min(max((today - start).days + 1, 0), days)
    pace = target * gone / days
    if booked >= pace:
        return "green"
    if booked >= pace * AMBER_SHARE:
        return "amber"
    return "red"


def _target(targets: list[dict[str, Any]], kind: str, start: date, entity: str | None) -> Any:
    return next(
        (
            t
            for t in targets
            if t["period_kind"] == kind
            and _day(t["period_start"]) == start
            and (t.get("entity") or None) == entity
        ),
        None,
    )


def _won_in(deals: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    return [
        d
        for d in deals
        if d["stage"] == "closed_won" and (won := _day(d.get("won_on"))) and start <= won <= end
    ]


def _figure(booked: Decimal, target: Any, start: date, end: date, today: date) -> dict[str, Any]:
    """`{value, target, colour?}`: no colour without a target (FR-CRM-DASH-02)."""
    out: dict[str, Any] = {"value": money(booked), "target": None}
    if target is not None:
        aim = _dec(target["amount_inr"])
        out["target"] = money(aim)
        out["colour"] = colour(booked, aim, start, end, today)
    return out


def bookings(
    deals: list[dict[str, Any]], facts: Facts, f: Filters, *, has_entity: bool
) -> list[dict[str, Any]]:
    """Won this month, quarter and FY against the workspace's target and,
    in Labs, each entity's. A target is the workspace's whole aim, so an
    owner or channel filter shows the figure without its colour."""
    narrowed = bool(f.owner_id or f.channel_id)
    out = []
    for kind in PERIOD_KINDS:
        start = period_start(kind, facts.today)
        end = period_end(kind, start)
        won = _won_in(deals, start, end)
        whole = None if narrowed or f.entity else _target(facts.targets, kind, start, None)
        row: dict[str, Any] = {
            "period": kind,
            "label": PERIOD_LABELS[kind],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "count": len(won),
            **_figure(
                sum((_dec(d.get("amount_in_inr")) for d in won), Decimal(0)),
                whole,
                start,
                end,
                facts.today,
            ),
        }
        if has_entity:
            row["by_entity"] = []
            for entity, label in objects.ENTITIES.items():
                if f.entity and f.entity != entity:
                    continue
                mine = [d for d in won if d.get("entity") == entity]
                aim = None if narrowed else _target(facts.targets, kind, start, entity)
                row["by_entity"].append(
                    {
                        "entity": entity,
                        "entity_label": label,
                        "count": len(mine),
                        **_figure(
                            sum((_dec(d.get("amount_in_inr")) for d in mine), Decimal(0)),
                            aim,
                            start,
                            end,
                            facts.today,
                        ),
                    }
                )
        out.append(row)
    return out


def won_lost_quarter(deals: list[dict[str, Any]], facts: Facts) -> dict[str, Any]:
    """This quarter's closed deals and why the lost ones were lost."""
    start = quarter_start(facts.today)
    end = period_end("quarter", start)
    won = _won_in(deals, start, end)
    lost = [
        d
        for d in deals
        if d["stage"] == "closed_lost" and (on := _day(d.get("lost_on"))) and start <= on <= end
    ]
    reasons: dict[str | None, int] = {}
    for d in lost:
        reasons[d.get("lost_reason_id")] = reasons.get(d.get("lost_reason_id"), 0) + 1
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "won": {
            "count": len(won),
            "amount_in_inr": money(sum((_dec(d.get("amount_in_inr")) for d in won), Decimal(0))),
        },
        "lost": {
            "count": len(lost),
            "amount_in_inr": money(sum((_dec(d.get("amount_in_inr")) for d in lost), Decimal(0))),
        },
        "lost_reasons": sorted(
            (
                {
                    "lost_reason_id": key,
                    "label": facts.options.get(key or "") or "No reason",
                    "count": count,
                }
                for key, count in reasons.items()
            ),
            key=lambda r: (-r["count"], r["label"].casefold()),
        ),
    }


def _closed_in(deals: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    out = []
    for d in deals:
        on = _day(d.get("won_on")) if d["stage"] == "closed_won" else None
        if d["stage"] == "closed_lost":
            on = _day(d.get("lost_on"))
        if on is not None and start <= on <= end:
            out.append(d)
    return out


def _rate_row(deals: list[dict[str, Any]]) -> dict[str, Any]:
    won = [d for d in deals if d["stage"] == "closed_won"]
    lost = [d for d in deals if d["stage"] == "closed_lost"]
    sized = [_dec(d["amount_in_inr"]) for d in won if d.get("amount_in_inr") is not None]
    return {
        "won": len(won),
        "lost": len(lost),
        "win_rate": _pct(len(won), len(won) + len(lost)),
        "average_deal_size": money(sum(sized, Decimal(0)) / len(sized)) if sized else None,
    }


def win_rates(
    deals: list[dict[str, Any]], facts: Facts, f: Filters, *, has_entity: bool
) -> dict[str, Any]:
    """Deals closed in the range: win rate (won ÷ closed) and the average
    won deal in INR, overall, by entity (Labs) and by channel."""
    start, end = _range(f, facts.today)
    closed = _closed_in(deals, start, end)
    out: dict[str, Any] = {"overall": _rate_row(closed)}
    if has_entity:
        out["by_entity"] = [
            {
                "entity": entity,
                "entity_label": label,
                **_rate_row([d for d in closed if d.get("entity") == entity]),
            }
            for entity, label in objects.ENTITIES.items()
        ]
    channels = sorted(
        {d.get("channel_id") for d in closed},
        key=lambda c: (c is None, (facts.options.get(c or "") or "").casefold()),
    )
    out["by_channel"] = [
        {
            "channel_id": c,
            "label": facts.options.get(c or "") or "No channel",
            **_rate_row([d for d in closed if d.get("channel_id") == c]),
        }
        for c in channels
    ]
    return out


def _days(start: datetime, end: datetime) -> Decimal:
    return Decimal((end - start).total_seconds()) / Decimal(86400)


def time_in_stage(deals: list[dict[str, Any]], facts: Facts, f: Filters) -> dict[str, Any]:
    """FR-CRM-DASH-02 — the average days a deal spent in each stage, over
    the stays that ended in the range (a deal moved out of the stage then),
    and the average days from a deal's first stage to its win, over the
    deals won in the range. Both from `crm_stage_history`."""
    start, end = _range(f, facts.today)
    wanted = {d["id"] for d in deals}
    by_deal: dict[str, list[dict[str, Any]]] = {}
    for h in facts.history:
        if h["opportunity_id"] in wanted:
            by_deal.setdefault(h["opportunity_id"], []).append(h)
    stays: dict[str, list[Decimal]] = {s: [] for s in STAGES if s not in CLOSED}
    for entries in by_deal.values():
        entries.sort(key=lambda h: _moment(h["at"]))
        for here, nxt in zip(entries, entries[1:], strict=False):
            stage = here["to_stage"]
            left = _moment(nxt["at"])
            if stage in stays and start <= left.astimezone(_ZONE).date() <= end:
                stays[stage].append(_days(_moment(here["at"]), left))
    closes: list[Decimal] = []
    for d in _won_in(deals, start, end):
        entries = by_deal.get(d["id"]) or []
        first = _moment(entries[0]["at"]) if entries else _moment(d["created_at"])
        won = next(
            (_moment(h["at"]) for h in reversed(entries) if h["to_stage"] == "closed_won"), None
        )
        if won is None:
            continue
        closes.append(_days(first, won))

    def average(values: list[Decimal]) -> str | None:
        if not values:
            return None
        return format((sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.1")), "f")

    return {
        "stages": [
            {
                "stage": stage,
                "stage_label": facts.stage_labels.get(stage, STAGE_LABELS[stage]),
                "average_days": average(values),
                "stays": len(values),
            }
            for stage, values in stays.items()
        ],
        "days_to_close": {"average_days": average(closes), "deals": len(closes)},
    }


def flag_counts(flagged: dict[str, list[dict[str, Any]]], f: Filters) -> list[dict[str, Any]]:
    """Follow-up and deal-health flags by kind, over the records the
    filters keep: how many records carry each, coloured by the worst level
    among them (green when none). Next step and close date count only when
    overdue (red)."""
    worst: dict[str, str] = {}
    counts: dict[str, int] = {}
    for table, rows in flagged.items():
        for row in rows:
            if f.owner_id and row.get("owner_id") != f.owner_id:
                continue
            if table == "crm_opportunities" and not deal_matches(row, f):
                continue
            if table == "crm_companies" and not company_matches(row, f):
                continue
            if table == "crm_people" and (f.entity or f.channel_id):
                continue
            seen: set[str] = set()
            for flag in row.get("flags") or []:
                kind, level = flag.get("kind"), flag.get("level")
                if kind in _OVERDUE_ONLY and level != "red":
                    continue
                if kind in seen or kind not in dict(FLAG_PANEL):
                    continue
                seen.add(kind)
                counts[kind] = counts.get(kind, 0) + 1
                if flag_rules.LEVELS.index(level or "none") > flag_rules.LEVELS.index(
                    worst.get(kind, "none")
                ):
                    worst[kind] = level
    return [
        {
            "kind": kind,
            "label": label,
            "value": counts.get(kind, 0),
            "colour": worst.get(kind, "green") if counts.get(kind) else "green",
        }
        for kind, label in FLAG_PANEL
    ]


def lead_funnel(companies: list[dict[str, Any]], facts: Facts) -> dict[str, Any]:
    """Companies per lead stage in funnel order, Partner left out. A stage's
    `reached` counts the companies at it or past it; `conversion` is the
    share of those that went on to the next stage."""
    at = {s: 0 for s in FUNNEL}
    disqualified = 0
    for c in companies:
        stage = c.get("lead_stage")
        if stage in at:
            at[stage] += 1
        elif stage == "disqualified":
            disqualified += 1
    reached = {s: sum(at[t] for t in FUNNEL[i:]) for i, s in enumerate(FUNNEL)}
    stages = []
    for i, stage in enumerate(FUNNEL):
        nxt = FUNNEL[i + 1] if i + 1 < len(FUNNEL) else None
        stages.append(
            {
                "lead_stage": stage,
                "label": facts.lead_stage_labels.get(stage, stage.replace("_", " ").capitalize()),
                "count": at[stage],
                "reached": reached[stage],
                "conversion": _pct(reached[nxt], reached[stage]) if nxt else None,
            }
        )
    return {"stages": stages, "disqualified": disqualified}


def pipeline_by(
    deals: list[dict[str, Any]],
    key: Any,
    labels: dict[str, str],
    *,
    none_label: str,
    id_name: str,
) -> list[dict[str, Any]]:
    """Open deals grouped by `key(deal)`: by label, the unset group last."""
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for d in deals:
        if d["stage"] in OPEN:
            groups.setdefault(key(d), []).append(d)
    return [
        {id_name: k, "label": labels.get(k or "") or none_label, **_pipeline_row(rows)}
        for k, rows in sorted(
            groups.items(),
            key=lambda kv: (kv[0] is None, (labels.get(kv[0] or "") or "").casefold()),
        )
    ]


def _weeks(f: Filters, today: date) -> list[date]:
    start, end = _range(f, today)
    last = end - timedelta(days=end.weekday())
    first = max(start - timedelta(days=start.weekday()), last - timedelta(weeks=ACTIVITY_WEEKS - 1))
    weeks = []
    while first <= last:
        weeks.append(first)
        first += timedelta(weeks=1)
    return weeks


def activity(facts: Facts, f: Filters) -> dict[str, Any]:
    """Q-26, FR-CRM-DASH-04 — per owner per week: emails in and out (of the
    mailboxes they own, never internal or automatic mail), notes they wrote
    and tasks they completed. Owners in name order, never by a figure."""
    weeks = _weeks(f, facts.today)
    if not weeks:
        return {"weeks": [], "owners": []}
    start, end = weeks[0], weeks[-1] + timedelta(days=6)
    counts: dict[tuple[str, date], dict[str, int]] = {}

    def bump(owner: str | None, at: Any, what: str) -> None:
        if not owner or (f.owner_id and owner != f.owner_id):
            return
        day = _local_day(at)
        if not start <= day <= end:
            return
        week = day - timedelta(days=day.weekday())
        cell = counts.setdefault(
            (owner, week), {"emails_in": 0, "emails_out": 0, "notes": 0, "tasks": 0}
        )
        cell[what] += 1

    for e in facts.emails:
        if e.get("is_internal") or e.get("is_auto"):
            continue
        bump(
            e.get("owner_id"),
            e["sent_at"],
            "emails_in" if e["direction"] == "inbound" else "emails_out",
        )
    for n in facts.notes:
        bump(n.get("created_by"), n["created_at"], "notes")
    for t in facts.tasks:
        bump(t.get("assignee_id"), t["completed_at"], "tasks")

    owners = set(facts.members) | {o for o, _ in counts}
    if f.owner_id:
        owners = {f.owner_id}
    rows = []
    for owner in sorted(owners, key=lambda o: ((facts.names.get(o) or "").casefold(), o)):
        cells = [
            {
                "week_start": w.isoformat(),
                **counts.get((owner, w), {"emails_in": 0, "emails_out": 0, "notes": 0, "tasks": 0}),
            }
            for w in weeks
        ]
        rows.append(
            {
                "owner_id": owner,
                "name": facts.names.get(owner),
                "weeks": cells,
                "totals": {
                    k: sum(c[k] for c in cells)
                    for k in ("emails_in", "emails_out", "notes", "tasks")
                },
            }
        )
    return {"weeks": [w.isoformat() for w in weeks], "owners": rows}


def capacity_weeks(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """FR-CRM-CAP-02, Q-39 — the capacity check's weeks: totals only, a week
    with fewer than 3 contributors hidden. Free hours are negative when
    confirmed work already exceeds supply: that is the staffing problem the
    check exists to show."""
    return [
        {"week_start": w["week_start"], "hidden": HIDDEN}
        if w["contributors"] < MIN_CONTRIBUTORS
        else {
            "week_start": w["week_start"],
            "tentative_hours": w["tentative_hours"],
            "free_hours": str(Decimal(w["supply_hours"]) - Decimal(w["confirmed_hours"])),
        }
        for w in rows
    ]


def build(facts: Facts, f: Filters, *, has_entity: bool, academy: bool) -> dict[str, Any]:
    """Every panel of FR-CRM-DASH-02 (and -03 for Academy) over the
    filtered deals and companies."""
    deals = [d for d in facts.deals if deal_matches(d, f)]
    companies = [c for c in facts.companies if company_matches(c, f)]
    since, until = _range(f, facts.today)
    out: dict[str, Any] = {
        "range": {"from": since.isoformat(), "to": until.isoformat()},
        "pipeline_by_stage": pipeline_by_stage(deals, facts),
        "bookings": bookings(deals, facts, f, has_entity=has_entity),
        "won_lost_quarter": won_lost_quarter(deals, facts),
        "win_rate": win_rates(deals, facts, f, has_entity=has_entity),
        "time_in_stage": time_in_stage(deals, facts, f),
        "flags": flag_counts(facts.flagged, f),
        "lead_funnel": lead_funnel(companies, facts),
        "pipeline_by_channel": pipeline_by(
            deals,
            lambda d: d.get("channel_id"),
            facts.options,
            none_label="No channel",
            id_name="channel_id",
        ),
        "pipeline_by_campaign": pipeline_by(
            deals,
            lambda d: d.get("source_campaign_id"),
            facts.campaigns,
            none_label="No campaign",
            id_name="campaign_id",
        ),
        "activity": activity(facts, f),
    }
    if academy:
        types = {c["id"]: c.get("institution_type_id") for c in facts.companies}
        out["pipeline_by_institution_type"] = pipeline_by(
            deals,
            lambda d: types.get(d.get("company_id")),
            facts.options,
            none_label="No institution type",
            id_name="institution_type_id",
        )
        out["pipeline_by_programme"] = pipeline_by(
            deals,
            lambda d: d.get("programme_id"),
            facts.options,
            none_label="No programme",
            id_name="programme_id",
        )
    return out
