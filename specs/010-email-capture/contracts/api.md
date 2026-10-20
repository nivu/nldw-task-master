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
applied, `live` is always refused.

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
  deal signal.
- `recall.rows[].subject` is present only when the thread is stored (it is
  already captured mail); otherwise `null`. `fate` ∈ `filed_known`, `found`,
  `judged_otherwise`, `skipped_rules`, `outside_scope`.
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
