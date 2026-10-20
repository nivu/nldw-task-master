# Automatic Email Capture — API and MCP contracts

| | |
|---|---|
| **Feature** | `010-email-capture` |
| **Spec** | [`../spec.md`](../spec.md) |
| **Plan** | [`../plan.md`](../plan.md) |
| **Extends** | [`../../009-crm-intelligence/contracts/api.md`](../../009-crm-intelligence/contracts/api.md) and [`../../007-crm/contracts/api.md`](../../007-crm/contracts/api.md); every convention of their §0 holds |

Pydantic models go in `backend/app/schemas/crm.py` (request models
`extra="forbid"`), mirrored by hand in `frontend/lib/api/crm-types.ts`.

## 0. Conventions

| Topic | Rule |
|---|---|
| Base path | `/api/v1/crm/...`, reached only through `frontend/app/api/proxy`; `?workspace=<key>` as `007` §0 |
| Roles | Admin = `CrmAdminDep` (403 "Only a workspace admin can do that."); write = `CrmWriteDep` (Admin or Member); a non-member or unknown workspace is 404 "No such workspace." |
| Errors | RFC 7807 `application/problem+json`: `{type: "about:blank", title, status, detail, instance}`; `detail` is one sentence shown as is. Each new sentence is listed below |
| Text | No route returns message text. Generated strings (`reason`) are plain text, money-masked |
| Keys | The API holds neither the Gmail nor the Claude key: routes that need either only queue a task |
| Dates | `YYYY-MM-DD` (IST); timestamps with offset |

---

## 1. Routes that change

### 1.1 Mailboxes — S1 (FR-CAP-SRC-01, -07)

`GET /crm/mailboxes` (Admin): each mailbox gains

```json
{ "capture_source": "labels", "capture_start_date": "2026-04-01", "docs_checked_at": null }
```

`POST /crm/mailboxes` (Admin), `MailboxIn` gains:

| Field | Type | Default |
|---|---|---|
| `capture_source` | `"labels" \| "primary_sent"` | `"labels"` |
| `capture_start_date` | date | `2026-04-01` |

`PATCH /crm/mailboxes/{id}` (Admin), `MailboxUpdate` gains both, each
optional and not nullable (sent `null` is refused like the other fields). A
change to `email`, `labels`, `capture_source` or `capture_start_date` clears
the sync cursor: the response's `backfilled_at` is `null`. A change to
`capture_source` or `capture_start_date` also clears `docs_checked_at`. A
change of `capture_source` from `primary_sent` to `labels` also makes the
next reconcile look back to the earliest capture start date the mailbox
captured from (including one since moved later), so captured mail with no
label is removed however old it is (plan §5.3, PD-3).

New 422 sentence (`email_rules.mailbox_refusal`):
- "The capture start date cannot be in the future."

Unchanged: at least one label is still required in both modes ("Name at least
one Gmail label to sync."); system labels are still refused as labels.

### 1.2 Settings — S2/S3 (FR-CAP-DIS-01)

`GET /crm/settings` (Admin) includes `"capture": {"mode": "off"}`.

`PATCH /crm/settings/capture` (Admin) — `{ "value": { "mode": "off" | "live" } }`.
Written by `crm_capture_set_mode`: a change while a dry run is open
**cancels** that run (`finished_at` set, `cancelled: true`, never
applicable), so a new dry run can start later. Response as the generic
settings PATCH, plus `cancelled_dry_run_id` (`null` when none was open).
`live` is accepted only once the workspace has an applied dry run (E6):
Live is first entered through Apply (§2.3). In S2, where nothing can be
applied, `live` is always refused. Afterwards `live` is accepted also while,
or after, a later dry run that was not applied: that run is cancelled when
open, and every decision it made goes back to pending, so the live job
judges it again (no lead it found is lost).

| Status | `detail` |
|---|---|
| 422 | "Email capture is off, dry run or live." (any other value, unknown or missing keys) |
| 422 | "Start a dry run from Email capture." (`mode: "dry_run"`) |
| 422 | "Run and apply a dry run first." (`live` with no applied dry run) |
| 422 | "Set a mailbox to Primary and Sent first." (`live` with no active `primary_sent` mailbox) |
| 422 | `email_sync.CANNOT_SWITCH_ON` sentence when `CRM_GMAIL_CONFIGURED` is false and mode is not `off` |

### 1.3 Review Queue — S1/S3 (FR-CAP-REV-01 to -04)

`GET /crm/review` — items may now have `type` `new_lead` or `deal_signal`
(§6). `target` is `null` for a `new_lead` with no matched company
(`ReviewItemOut.target: dict | None`). Type labels: `new_lead` "New lead",
`deal_signal` "Deal signal".

`POST /crm/review/{id}/approve` — `ReviewApproveIn` unchanged (`version`,
`deal_id`, `option`, `edits`). Accepted `edits` keys:

| Type | Keys |
|---|---|
| `new_lead` | `company_name` (1–200), `domain` (a domain or `null`), `lead_stage` (`new\|contacted\|qualified`), `first_name`, `last_name`, `email`, `title`, `link_company_id` |
| `deal_signal` | `name`, `next_step`, `next_step_on`, `amount`, `currency`, `expected_close_on`, `proposal_sent_on`, `point_of_contact_id`, `project_name` |

Response `{item, result}`:

| Type | `result` |
|---|---|
| `new_lead` | `{company_id, person_id, linked: bool, filed: "queued"}` |
| `deal_signal` create | `{opportunity_id}` |
| `deal_signal` move | `{opportunity_id, stage, project_id?}` (`project_id` in Labs on entering Proposal) |

New sentences:

| Status | `detail` |
|---|---|
| 422 | "This deal is already at or past that stage." |
| 422 | "Choose a lead stage: New, Contacted or Qualified." |
| 422 | the manual services' own sentences, unchanged (domain taken 409, company not Qualified, missing next step, amount, expected close, point of contact, currency, Closed Won in Labs needs the handoff) |

`POST /crm/review/{id}/dismiss` — optional body `ReviewDismissIn`:

```json
{ "ignore": "address" | "domain" | null }
```

| Status | `detail` |
|---|---|
| 422 | "Only a new lead can add to the ignore list." (`ignore` on another type) |
| 422 | "A public email domain cannot be ignored; ignore the sender instead." |
| 409 | `009`'s "This suggestion was already decided." |

Response `{item, ignore: {id, kind, value} | null}`. An entry that already
exists is reused, not an error.

### 1.4 Jobs — S2

`GET /crm/jobs`, `GET /crm/tva-admin/jobs`, `GET /health/jobs` list job
`capture` ("Email capture", hourly). `GET /crm/jobs?job=capture` is accepted.
`counts` for a `capture` run: `{considered, skipped_rules, ignored, known,
judged, items, low_confidence, failed_items, pending_left}` (`skipped_rules`
counts candidates the rules skipped; a run that stopped part-way also
carries `skipped`, the reason, as every job does). Email sync runs gain
`skipped_bulk`, `candidates`, `deal_signals`, `deal_doc_failed`.

### 1.5 AI overview — S2

`GET /crm/ai` `spend.by_job` gains `capture`.

### 1.6 Run email sync now

`POST /crm/email-sync/run` — the report echoes the new count keys.

---

## 2. New routes — `backend/app/api/crm/capture.py` (Admin only)

### 2.1 `GET /crm/capture` — S2 (FR-CAP-RPT-02, -03)

```json
{
  "mode": "dry_run",
  "live_allowed": true,
  "mailboxes": [{"id": "…", "email": "navaneeth@nunnarilabs.com",
                 "capture_source": "primary_sent", "capture_start_date": "2026-04-01",
                 "backfilled_at": "2026-10-21T02:00:00+05:30"}],
  "ignores": [{"id": "…", "kind": "domain", "value": "vendor.example",
               "from_item_id": null, "created_by_name": "Navaneeth", "created_at": "…"}],
  "dry_run": {
    "id": "…", "started_at": "…", "finished_at": null, "cancelled": false,
    "applied_at": null, "applicable": false,
    "counts": {"considered": 512, "pending": 40, "skipped_rules": 30, "ignored": 4,
               "known": 18, "judged": 410, "failed": 0, "filed_known_threads": 280,
               "by_outcome": {"new_lead": {"high": 9, "medium": 6, "low": 4},
                              "deal_signal": {"high": 2, "medium": 1, "low": 0},
                              "other_business": {"high": 120, "medium": 30, "low": 8},
                              "not_relevant": {"high": 200, "medium": 25, "low": 5}}},
    "estimated_cost_usd": "1.84",
    "findings": [{"candidate_id": "…", "outcome": "new_lead", "confidence": "high",
                  "company_name": "Acme Robotics", "domain": "acme-robotics.com",
                  "domain_from_text": false, "person_name": "Priya Shah", "person_email": "priya@acme-robotics.com",
                  "first_at": "…", "latest_at": "…", "message_count": 2,
                  "owner_replied": true, "reason": "Asked for pricing; you replied.",
                  "duplicate_company": null}],
    "recall": {
      "line": "Found 41 of your 60 labelled conversations; 12 of the rest are outside Primary and Sent.",
      "rows": [{"thread_id": "…", "latest_at": "…", "subject": "Re: Proposal",
                "fate": "filed_known"}]
    }
  }
}
```

- `live_allowed`: whether `PATCH /crm/settings/capture {"mode": "live"}`
  passes its own checks (§1.2): a dry run of the workspace was ever applied
  (not only the latest) and an active mailbox is on Primary and Sent. The
  page enables its Live option from it.
- `dry_run` is `null` when none has run. `counts` are live while it runs,
  and as they stood once it finished or was cancelled. `recall` is empty
  until it finishes.
- `applicable`: finished, not cancelled, not applied, the latest, and
  **finished** within 7 days.
- `cancelled`: the mode was changed away from Dry run before it finished;
  its counts are shown as they stood.
- Recall: each labelled thread is judged as if it had no label (plan §5.7):
  `filed_known` only through a person or company created before the
  thread's first message; `found` is a high- or medium-confidence lead or
  deal signal. `ignored` (every outside person on the ignore list) and
  `known_now` (every message names someone the CRM holds now, made after
  the thread began) are never sent to Claude (spec G-5); the line adds
  "J of the rest name only people added to the CRM after they began, so
  they were not sent to Claude." when J > 0. The line and rows are shown
  only once the run finished and was not cancelled.
- `recall.rows[].subject` is present only when the thread is stored (it is
  already captured mail); otherwise `null`. `fate` ∈ `filed_known`, `found`,
  `judged_otherwise`, `skipped_rules`, `outside_scope`, `ignored`,
  `known_now`.
- `findings` include every confidence; the UI groups low separately
  (FR-CAP-DIS-07).

### 2.2 `POST /crm/capture/dry-run` — S2 (FR-CAP-DIS-10, RPT-01)

No body. Starts a dry run (`crm_capture_start_dry_run`) and sets the mode to
`dry_run`; the hourly job, or the next chained task, does the work. `202`
`{dry_run: {id, started_at}}`.

| Status | `detail` |
|---|---|
| 422 | "Set a mailbox to Primary and Sent first." |
| 422 | "Switch email sync on first." (sync off or not configured) |
| 409 | "A dry run is already running." |

### 2.3 `POST /crm/capture/dry-runs/{id}/apply` — S3 (FR-CAP-RPT-04)

No body. Upserts Review Queue items from the run's high- and
medium-confidence findings (no Claude call), marks the run applied, sets the
mode to `live`. `200` `{applied_at, items_created: n, mode: "live"}`.

| Status | `detail` |
|---|---|
| 404 | "No such dry run." |
| 422 | "This dry run has not finished." |
| 422 | "This dry run was stopped before it finished; run it again." |
| 422 | "This dry run finished more than 7 days ago; run it again." |
| 422 | "A newer dry run exists; apply that one." |
| 409 | "This dry run was already applied." |

### 2.4 `POST /crm/capture/ignores` — S3 (FR-CAP-DIS-03)

`CaptureIgnoreIn`: `{kind: "address" | "domain", value: string}` (trimmed,
lower-cased). `201` `{ignore: {id, kind, value, created_at}}`.

| Status | `detail` |
|---|---|
| 422 | "Enter an email address." / "Enter a domain such as example.com." |
| 422 | "A public email domain cannot be ignored; ignore the sender instead." |
| 422 | "Your own domains cannot be ignored." (a workspace `allowed_domains` value) |
| 409 | "That is already on the ignore list." |

### 2.5 `DELETE /crm/capture/ignores/{id}` — S3

`204`. 404 "No such ignore entry." Removing an entry does not re-open
candidates already `ignored`; they are judged again when a new message
arrives.

---

## 3. MCP — `backend/app/mcp/crm.py`

No new tool; the count stays 21 (`test_crm_mcp.py`).

`crm_review_decide(item_id, decision, version=None, deal_id=None, option=None,
edits=None, ignore: Literal["address", "domain"] | None = None, workspace=None)`:
`ignore` applies to `decision="dismiss"` of a `new_lead` only. Docstring
lines added: "New lead: approving creates the company (at New, then the
suggested lead stage), the person, and files the conversation. Deal signal:
creates a Discovery deal or moves the chosen deal; Proposal needs amount,
currency, expected close and the next step; in Labs, Closed Won is done with
crm_handoff. CONFIRM FIRST."

`crm_review_list` is unchanged; it returns the new types.

---

## 4. Structured output sent to Claude — `DiscoveryOutput`

Model `ai_rules.CRM_AI_MODEL`; job `capture`; through `ai.call(ws,
"capture", None, DiscoveryOutput, capture.INSTRUCTION, parts)`.
`claude.api_schema` strips length bounds and sets `additionalProperties:
false`; Pydantic re-validates the bounds.

```python
class DiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["new_lead", "deal_signal", "other_business", "not_relevant"]
    confidence: Literal["high", "medium", "low"]
    company_name: str | None = Field(default=None, max_length=200)
    person_name: str | None = Field(default=None, max_length=200)
    person_email: str | None = Field(default=None, max_length=320)
    person_title: str | None = Field(default=None, max_length=200)
    lead_stage: Literal["contacted", "qualified"] | None = None
    deal_stage: Literal["discovery", "proposal", "closed_won"] | None = None
    website_choice: int | None = Field(default=None, ge=1, le=10)
    reason: str = Field(min_length=1, max_length=160)
```

JSON schema as sent (after `api_schema`):

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["outcome", "confidence", "reason"],
  "properties": {
    "outcome": {"type": "string", "enum": ["new_lead", "deal_signal", "other_business", "not_relevant"]},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "company_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "person_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "person_email": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "person_title": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "lead_stage": {"anyOf": [{"type": "string", "enum": ["contacted", "qualified"]}, {"type": "null"}]},
    "deal_stage": {"anyOf": [{"type": "string", "enum": ["discovery", "proposal", "closed_won"]}, {"type": "null"}]},
    "website_choice": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "reason": {"type": "string"}
  }
}
```

The website (FR-CAP-DIS-05) is **chosen, not typed**: `ai._clean` would drop
a bare domain Claude wrote. The prompt lists the hosts found verbatim in the
thread (`capture.website_choices`: outside participants' non-webmail
domains and hosts in the text, at most 10), and Claude returns the number
or `null`. A number outside the list is treated as `null`. The finding's
`domain` is that host, else `person_email`'s domain unless webmail, else
`null`; a host taken from the text sets `domain_from_text: true` for the
reviewer to confirm (plan §5.6, PD-20). `INSTRUCTION` (in `domain/crm/capture.py`) tells the model: the
workspace sells software and services; outcomes defined as in FR-CAP-DIS-05
with *other business* = vendor, hiring, partner, service provider; one line
reason under 160 characters naming what was asked and whether the owner
replied; never quote the message; fields `null` when unknown.

`parts` (`(label, text)`, each quoted by `ai.call`):

```text
("Message 1 of 2 inbound", "inbound · from Priya Shah <priya@acme-robotics.com> · to navaneeth@nunnarilabs.com · 2026-10-01 09:12 IST · attachments: none\n\n<trimmed text>")
("Message 2 of 2 outbound", "outbound · from navaneeth@nunnarilabs.com · to priya@acme-robotics.com · 2026-10-02 11:40 IST · attachments: Nunnari pricing.pdf\n\n<trimmed text>")
("Websites in this conversation", "1. acme-robotics.com")
("Mailbox owner replied", "yes")
```

A part's label is the `source` attribute of its quoted `<data>` block, and
`ai_rules.quote_untrusted` keeps only letters, digits, spaces, `_`, `.` and
`-` of it, and at most 60 characters. So each message's label is just its
place and direction, and its sender, recipients, date and attachment names
open its text instead (S2 build).

---

## 5. Frontend client — `frontend/lib/api/crm.ts`, `crm-types.ts`

| Function | Route |
|---|---|
| `getCrmCapture(ws)` | `GET /crm/capture` |
| `startCrmCaptureDryRun(ws)` | `POST /crm/capture/dry-run` |
| `applyCrmCaptureDryRun(ws, id)` | `POST /crm/capture/dry-runs/{id}/apply` |
| `addCrmCaptureIgnore(ws, body)` | `POST /crm/capture/ignores` |
| `removeCrmCaptureIgnore(ws, id)` | `DELETE /crm/capture/ignores/{id}` |
| `dismissCrmReview(ws, id, ignore?)` | `POST /crm/review/{id}/dismiss` with `{ignore}` |
| `updateCrmSetting(ws, "capture", {mode})` | existing |
| `updateCrmMailbox(ws, id, {capture_source, capture_start_date})` | existing |

Types: `CrmMailbox`/`CrmMailboxIn` + the two fields; `CrmCaptureOverview`,
`CrmCaptureIgnore`, `CrmCaptureFinding`, `CrmCaptureRecallRow`;
`ReviewItemType` + `"new_lead" | "deal_signal"`; `CrmReviewItem.target`
nullable; `CrmJobName` + `"capture"`; `CrmAiSpendJob` + `"capture"`.

---

## 6. Review Queue payloads

Both use `source_kind: "email_thread"`, `source_key:
"gmail:<mailbox_id>:<thread_id>"`, `source_link: {mailbox_id, thread_id}`.

### 6.1 `new_lead` (made by `crm_ai.capture`)

Stored item (`crm_review_upsert` input):

```json
{
  "type": "new_lead",
  "owner_id": "<mailbox owner or null>",
  "record_type": null, "record_id": null,
  "suggestion": {
    "company_name": "Acme Robotics",
    "domain": "acme-robotics.com", "domain_from_text": false,
    "lead_stage": "contacted",
    "first_name": "Priya", "last_name": "Shah",
    "email": "priya@acme-robotics.com", "title": "Head of Ops",
    "link": false,
    "deal_stage_hint": null,
    "evidence": {
      "first_at": "2026-10-01T09:12:00+05:30", "latest_at": "2026-10-02T11:40:00+05:30",
      "message_count": 2, "owner_replied": true,
      "attachments": ["Nunnari pricing.pdf"],
      "reason": "Asked for pricing; you replied.", "confidence": "high",
      "origin": "discovery"
    }
  },
  "current_values": {},
  "source_version": "<latest gmail message id judged>",
  "dedupe_key": "email_thread|gmail:<mb>:<thread>|new_lead|thread",
  "model": "claude-sonnet-5-5", "generated_at": "…"
}
```

A matched company (FR-CAP-DIS-08): `record_type: "company"`, `record_id`,
`link: true`, `dedupe_key: "email_thread|gmail:<mb>:<thread>|new_lead|company:<id>"`.
Webmail (FR-CAP-DIS-09): `domain: null`.

As returned by `GET /crm/review` (`ReviewItemOut`, `009` contracts §2.2.1;
`target` becomes nullable):

```json
{
  "id": "…", "type": "new_lead", "type_label": "New lead", "status": "open", "version": 1,
  "owner": {"id": "…", "name": "Navaneeth"},
  "target": null,
  "deal_choices": [],
  "suggestion": { "...": "as stored" },
  "current": {},
  "source": {"kind": "email_thread", "label": "Email thread",
             "link": {"mailbox_id": "…", "thread_id": "…", "thread_key": "…:…"}},
  "ai": {"source_kind": "email_thread", "model": "claude-sonnet-5-5", "generated_at": "…"},
  "refusal": null, "created_at": "…", "expires_on": "2026-11-04",
  "decided_at": null, "decided_by": null
}
```

Today's "To review" section and the digest use `title` "New lead: Acme
Robotics · Priya Shah" and `detail` "2 emails · Asked for pricing; you
replied." (from the suggestion, since there is no target name).

### 6.2 `deal_signal`

```json
{
  "type": "deal_signal",
  "record_type": "company", "record_id": "<company id>",
  "deals": ["<open deal id>", "…"],
  "suggestion": {
    "action": "move",
    "stage": "proposal",
    "proposal_sent_on": "2026-10-20",
    "evidence": {
      "origin": "deal_document",
      "document_kinds": ["sow"],
      "direction": "outbound",
      "message_at": "2026-10-20T15:02:00+05:30",
      "message_count": 1, "owner_replied": true,
      "attachments": [], "reason": "SOW sent to the client.", "confidence": null
    }
  },
  "current_values": {},
  "source_version": "<gmail message id>",
  "dedupe_key": "email_thread|gmail:<mb>:<thread>|deal_signal|company:<id>",
  "model": null
}
```

- `action`: `move` (the company has open deals; `deals` are the choices) or
  `create` (Discovery, from discovery on a matched company with no open deal;
  `deals` empty). Discovery suggesting Discovery, or no stage, for a company
  with an open deal makes no item.
- From discovery, `evidence` also has `email` (the sender), `domain` and
  `domain_from_text`; the card warns "taken from the text of an email from
  …, not an address" when the company was matched through a website named
  only in the text.
- `stage`: `discovery` (create only), `proposal`, `closed_won`. Inbound
  deal documents suggest `closed_won` with reason "Check the signed document."
- From discovery: `origin: "discovery"`, `confidence` set, `attachments` from
  the thread, `model` set.
- Today title: "Move to Proposal, proposal sent on 20 Oct" / "Possibly Closed Won:
  check the signed document" / "New deal at Discovery"; detail: company name
  and the reason.

---

## 7. Revision 1 (spec §14; plan "Revision 1"; data-model §9)

No route is added or removed; no MCP tool changes (count stays 21).

### 7.1 `GET /crm/capture` — FR-CAP-RPT-05 to -10

`dry_run.counts` gains `academy` and `academy_unrouted` (computed from the
run's findings, or for an applied run as Apply stored them; present for
every run, 0 when none). `dry_run.findings[]`
gains `business` (`"labs"` | `"academy"`) and, for Academy, `routed`
(boolean or `null` before apply; `true` means handed to an accepting
Academy, whatever the Academy then did — nothing the Academy decided is
returned here); `duplicate_company` is `null` for an
Academy finding. `dry_run.recall` becomes:

```json
{
  "line": "Of the 18 that could be tested, the dry run found 15. 42 could not be tested: 30 involve contacts now in the CRM (filed automatically), 8 were internal-only, 4 are outside Primary and Sent.",
  "summary": {
    "total": 60, "tested": 18, "found": 15, "capped": false,
    "untested": {"known": 30, "ignored": 0, "internal_only": 8, "calendar": 0,
                 "automated": 0, "outside_scope": 4, "not_judged": 0,
                 "skipped_rules": 0},
    "academy_unrouted": 0
  },
  "rows": [{"thread_id": "…", "latest_at": "…", "subject": null, "fate": "internal_only"}]
}
```

- `summary.untested.known` = rows with fate `filed_known` or `known_now`.
- `found` = rows with fate `found` or `academy_unrouted` (Claude found
  both); `tested` = `found` + rows with fate `judged_otherwise`;
  `summary.academy_unrouted` is the part of `found` the Academy could not
  take.
- `capped` is true only when the stored recall carries the `{"capped":
  true}` marker row (the check stopped at 500 with conversations still
  unchecked); marker rows are never returned in `rows`.
- `fate` ∈ `filed_known`, `known_now`, `found`, `judged_otherwise`,
  `ignored`, `internal_only`, `calendar`, `automated`, `outside_scope`,
  `not_judged`, `academy_unrouted`, and `skipped_rules` (runs before 074
  only).
- With none tested: `"None of your 60 labelled conversations could be
  tested: 36 involve contacts now in the CRM (filed automatically), 15 were
  internal-only, 9 were calendar notifications."`
- Optional clauses: inside the found sentence, `"…the dry run found 15,
  of which 2 were course enquiries the Academy could not take."`; at the
  end, `" Only the first 500 were checked."` when `capped`.
- Page text per fate (`FATE_TEXT`): Found · Filed as known · Judged
  otherwise · Contacts added later (not sent to Claude) · On the ignore
  list · Skipped: internal only · Skipped: calendar notification · Skipped:
  automated mail · Outside Primary and Sent · Not judged: Claude or Gmail
  failed · Course enquiry, no Academy workspace · Skipped by the rules.

### 7.2 Job counts — §1.4 amended

`capture` runs add `consolidated`, `rekeyed`, `consolidate_failed`,
`items_skipped`, `released`, `academy_routed` (handed over),
`academy_unrouted`. There is no Labs count of the Academy step's outcome
(an ignored or already decided enquiry is logged in the Academy only).
Email sync runs add `invites_rescanned`, `invite_rescan_failed`,
`invite_rescan_restarted`.

### 7.3 `DiscoveryOutput` — §4 amended (FR-CAP-ACAD-01, FR-CAP-INV-04)

```python
    business: Literal["labs", "academy"] = "labs"
```

JSON schema adds `"business": {"type": "string", "enum": ["labs",
"academy"]}` (not required). `INSTRUCTION` adds the two sentences of plan
R1.4 and R1.5 (business; meeting invitations). `parts` may add
`("Conversation type", "meeting invitation")`. `website_choices` never
lists a `MEETING_HOSTS` host. `CLASSIFIER_VERSION = "010.2"`. Outside the
TVA-linked workspace the answer is replaced by `labs`.

### 7.4 Review payloads — §6 amended (FR-CAP-GRP)

`new_lead` from Labs discovery, grouped:

```json
{
  "type": "new_lead",
  "source_kind": "email_thread",
  "source_key": "capture:domain:acme-robotics.com",
  "dedupe_key": "email_thread|capture:domain:acme-robotics.com|new_lead|thread",
  "source_version": "<newest entry's message id>",
  "source_link": {
    "mailbox_id": "<newest>", "thread_id": "<newest>",
    "threads": [
      {"ref": "gmail:<mb>:<t1>", "mailbox_id": "<mb>", "thread_id": "<t1>", "message_id": "…",
       "first_at": "2026-09-02T10:00:00+05:30", "latest_at": "2026-09-03T12:00:00+05:30",
       "message_count": 2, "owner_replied": false, "origin": "discovery",
       "reason": "Asked about a pilot.", "confidence": "medium",
       "person_email": "ravi@acme-robotics.com", "first_name": "Ravi", "last_name": null},
      {"ref": "gmail:<mb>:<t2>", "…": "…", "message_count": 3, "owner_replied": true}
    ]
  },
  "suggestion": {
    "company_name": "Acme Robotics", "domain": "acme-robotics.com", "…": "as §6.1",
    "evidence": {"first_at": "2026-09-02T10:00:00+05:30", "latest_at": "2026-10-02T11:40:00+05:30",
                 "message_count": 5, "conversations": 2, "owner_replied": true,
                 "attachments": ["Nunnari pricing.pdf"],
                 "reason": "Asked for pricing; you replied.", "confidence": "high",
                 "origin": "discovery"}
  }
}
```

- Linked: `source_key: "capture:linked"`, `dedupe_key:
  "email_thread|capture:linked|new_lead|company:<id>"`.
- `deal_signal` (either origin): `source_key: "capture:deal"`,
  `dedupe_key: "email_thread|capture:deal|deal_signal|company:<id>"`,
  `source_link.threads` as above with `origin` per entry.
- `GET /crm/review` returns `source.link` as stored plus `thread_key` (from
  the top level, unchanged); `threads` is passed through.
- Title unchanged; `detail` "2 conversations · 5 emails · Asked for
  pricing; you replied." (one conversation: "5 emails · …" as today).
- Approve result: `{company_id, person_id, people: 2, linked, filed:
  "queued", threads_filed: 2}`: one person made or linked per distinct
  entry `person_email` (`person_id` is the latest sender's);
  `threads_filed` counts the entries whose sender is a known person after
  approval.
- Money figures are masked in `suggestion` strings and in every
  `threads[].reason` before save (FR-CRMAI-LLM-05).
- A consolidated item: `status: "expired"`, `result: {"superseded_by":
  "<id>", "reason": "Merged into one suggestion for HearSight."}`.

`new_lead` in the Academy (FR-CAP-ACAD-04):

```json
{
  "type": "new_lead", "owner_id": "<mailbox owner if Academy admin/member, else null>",
  "record_type": null, "record_id": null,
  "source_kind": "email_thread",
  "source_key": "capture:domain:college.example.edu",
  "dedupe_key": "email_thread|capture:domain:college.example.edu|new_lead|thread",
  "source_version": "<gmail message id>",
  "source_link": {"threads": [{"ref": "gmail:navaneeth@nunnarilabs.com:<thread>",
                               "thread_id": "<thread>", "message_id": "…",
                               "first_at": "…", "latest_at": "…", "message_count": 2,
                               "owner_replied": true, "origin": "academy_routing",
                               "reason": "Asked about the AI course for students.",
                               "confidence": "high", "person_email": "…",
                               "first_name": "…", "last_name": "…"}]},
  "suggestion": {"company_name": "…", "domain": "college.example.edu", "domain_from_text": false,
                 "lead_stage": "contacted", "first_name": "…", "last_name": "…",
                 "email": "…", "title": null, "link": false, "deal_stage_hint": null,
                 "evidence": {"…": "totals as above", "origin": "academy_routing",
                              "mailbox_email": "navaneeth@nunnarilabs.com"}}
}
```

- No Labs uuid in any field; `source.link` has no `thread_key`.
- Approve result: `{company_id, person_id, people, note_id, linked,
  threads_filed: 0}`; the note (masked) is linked to the company and every
  person. The card shows "From the Labs mailbox navaneeth@nunnarilabs.com; no
  conversation is stored in the Academy." instead of "Open conversation".
- Dismiss with `ignore` writes the Academy's ignore list (§1.3 unchanged).

### 7.4a Settings — §1.2 amended (FR-CAP-ACAD-03)

`GET /crm/settings` (Admin) includes `"capture_routing": {"accept_routed":
true}` in every workspace; it is read only in the Academy. `PATCH
/crm/settings/capture_routing` (Admin of that workspace) — `{ "value": {
"accept_routed": true | false } }`, through the generic settings route and
`VALIDATORS["capture_routing"]`; audited. The Academy's Settings → Email
capture shows it as *Accept course enquiries from Labs*.

### 7.5 Frontend types — §5 amended

`CrmCaptureRecallFate` + `internal_only | calendar | automated |
not_judged | academy_unrouted`; `CrmCaptureRecall` + `summary`;
`CrmCaptureFinding` + `business`, `routed`; capture counts + `academy`,
`academy_unrouted`; `CrmReviewItem.source.link` + `threads?:
CrmCaptureThreadRef[]`; evidence + `conversations?`, `mailbox_email?`;
result + `threads_filed?`, `people?`, `note_id?`, `superseded_by?`;
`CrmCaptureThreadRef` + `person_email`, `first_name?`, `last_name?`;
settings + `capture_routing: {accept_routed: boolean}`.
