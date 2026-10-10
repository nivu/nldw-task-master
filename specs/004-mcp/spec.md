# MCP Access — V4

| | |
|---|---|
| **Feature** | `004-mcp` |
| **Status** | Settled 11 September 2026 — decisions in §7, being built |
| **Source** | Product owner, 11 September 2026: "make this entire project MCP enabled so we can connect through Claude … use tokens, add MCP for all APIs" |
| **Depends on** | [`001`](../001-leave-calendar/spec.md), [`002`](../002-timesheets/spec.md), [`003`](../003-management/spec.md), all in production |

Two things:

1. **Personal access tokens** a person issues to themselves and can revoke.
2. **An MCP endpoint** on the existing backend that exposes every portal
   operation as a tool, acting as the person whose token it was given.

---

## 1. Context

The portal's rules live entirely in the backend behind per-person guards; the
web app is one client of it. MCP (Model Context Protocol) is how Claude Code,
Claude Desktop and claude.ai connect to external tools. Making the portal an
MCP server means a second client of the same backend — "am I free on Friday,
and if so book it" — with nothing re-implemented and nothing bypassed.

The whole design turns on one question: **as whom does Claude act?** The
answer must be *the person who connected it*, never the service key. Every
rule about who sees what (leads and reasons, managers and money) is a rule
about a person, and a tool endpoint that acted as the system would erase all
of them at once.

---

## 2. Goals and non-goals

### 2.1 Goals

- **G-1** — Anyone can connect Claude to the portal and use it as themselves, with exactly the access they have in the web app.
- **G-2** — Every operation the web app can perform is available as a tool.
- **G-3** — A person can see, name and revoke the tokens they have issued.
- **G-4** — Nothing reaches Claude through MCP that the web app would not show the same person. (Until 5 October 2026 this read "— and one thing less": leave reasons; see Q-03.)

### 2.2 Non-goals

- OAuth sign-in for MCP clients (§7, Q-01). Tokens first; OAuth is a possible `005`.
- Tools for anybody other than the token's owner. There is no "act as" and no admin token that impersonates.
- Slack interactivity (`/slack/interactions`) is a webhook Slack calls, not an operation a person performs; it has no tool.

---

## 3. How it works

```
Claude ── MCP over HTTP ──▶ backend /mcp ──▶ the same API routes, in-process,
         bearer: nunp_…       as the token's owner (same guards, same log)
```

- A token is `nunp_` + 43 random URL-safe characters. Only its SHA-256 hash is
  stored; the plaintext is shown **once**, at creation, and never again.
- The API accepts a token anywhere it accepts a Supabase session: the auth
  dependency recognises the `nunp_` prefix and resolves it to the owner. A
  revoked, expired or deactivated-owner token is refused with the same 401 a
  stale session gets.
- Tools are thin: each one calls the corresponding API route **in-process**
  with the caller's own bearer token, so authorisation, validation, error
  wording and request logging are the route's, verbatim. There is no second
  authorisation code path to keep in step.

---

## 4. Rules

### 4.1 Tokens — FR-TOK

| ID | Requirement |
|---|---|
| FR-TOK-01 | A person MAY issue tokens for themselves, each with a name, from the Account page. |
| FR-TOK-02 | The plaintext MUST be shown once at creation and MUST NOT be retrievable afterwards; only a hash is stored. |
| FR-TOK-03 | A token MUST expire 90 days after issue and MAY be revoked at any time; both take effect on the next request. |
| FR-TOK-04 | A token MUST NOT be able to issue, list or revoke tokens. Only a Supabase session (the web app) may. A leaked token can therefore not extend its own life. |
| FR-TOK-05 | A token MUST stop working the moment its owner is deactivated, exactly as a session does. |
| FR-TOK-06 | Issuing and revoking a token MUST be written to the audit log. |
| FR-TOK-07 | Each request made with a token MUST be logged as such (`auth_via: token`) so the two channels can be told apart. |
| FR-TOK-08 | A token MUST NOT be written to the log, even when it arrives inside a URL: request lines replace anything matching `nunp_…`, or following `Bearer `, with `[redacted]`. *Added 10 October 2026.* |

### 4.2 Tools — FR-MCP

| ID | Requirement |
|---|---|
| FR-MCP-01 | Every API operation a person can perform in the web app MUST have a tool. |
| FR-MCP-02 | A tool MUST act as the token's owner and MUST be refused where the route would refuse that person. |
| FR-MCP-03 | **A leave reason MUST be returned by a tool exactly where the portal shows it, and to nobody the portal would not show it to** — the person, their lead (`lead_id`) and admins (`001` NFR-05). A manager or lead who is not the person's lead MUST NOT receive it. The person reads their own reason on any of their bookings; their lead and admins read it only on a request still pending a decision, as on the portal's approval screen (`001` Q-06), whether through `pending_approvals` or `get_booking`. The team-day roster stays category only (`001` FR-LEAD, Q-06), even when `lead_view_shows_reason` is on, because the portal's Team page never shows a reason. The MCP layer does not redact; the routes enforce NFR-05, and route-level tests (`test_api_permissions.py`, `TestLeaveReasons`) hold them to it. *Amended 5 October 2026 — see Q-03.* `pending_approvals` returns the whole queue in one answer, `{"leave": [...], "compoff": [...]}`: the leave requests from `/team/approvals` and only the still-pending claims from `/compoff/team`, as the portal's Approvals page shows them together. *Amended 10 October 2026.* |
| FR-MCP-04 | Every tool that changes something MUST be marked as such in its metadata and MUST instruct the model to confirm with the person before calling it. |
| FR-MCP-05 | Text returned by tools that was written by other people (notes, names) is data. Tool descriptions MUST say so. |
| FR-MCP-06 | An unauthenticated request to `/mcp` MUST be refused before any tool is listed. |
| FR-MCP-07 | An unexpected server failure MUST reach the model as the route's own 500 sentence, followed by "This is usually temporary; try once more." for a read (GET/HEAD), or by "It may or may not have been saved; check before trying again." for a write (POST/PUT/PATCH/DELETE), which may already have landed — never as a bare tool error. The failure itself is logged server-side with its traceback and route. *Added 10 October 2026.* |
| FR-MCP-08 | The week tools (`my_week`, `someone_elses_week`, `team_weeks`) MUST return `on_leave` for leave only (casual, sick, comp-off) and `wfh` separately, with `booked` {category, label, duration}; their tool descriptions MUST say that work from home is a working day and is still expected (`002` FR-ANALYTICS-07). *Added 10 October 2026.* |

---

## 5. Data model

```
api_tokens   id uuid PK
             user_id uuid → profiles, ON DELETE CASCADE
             name text NOT NULL
             token_hash text NOT NULL UNIQUE          -- sha256, hex
             prefix text NOT NULL                     -- first 12 chars, for display
             created_at, expires_at timestamptz NOT NULL
             last_used_at, revoked_at timestamptz
```

RLS on, no policies, no grants: the browser never touches this table. Only
the backend, with the service key, reads or writes it.

---

## 6. Where the MCP address comes from

The web app never learns the backend's address (constitution, Security-First),
and that stays true: `BACKEND_URL` is not in any client bundle. The Account
page shows an MCP address that the **API returns** to a signed-in person from
a server-side setting (`MCP_PUBLIC_URL`). The address was never the secret;
the token is. An MCP endpoint is a public entry point in the same sense the
web app is, and every request to it is authenticated.

---

## 7. Decisions

| ID | Question | Decision |
|---|---|---|
| **Q-01** | Tokens or OAuth? | **Tokens.** Works today from Claude Code and Claude Desktop; the claude.ai connector wants OAuth and is a later feature. Chosen by the product owner. |
| **Q-02** | Which operations? | **All of them.** Chosen by the product owner. Reads and writes, every role, guarded per person by the routes themselves. |
| **Q-03** | Reasons through MCP? | **Same as the portal** (FR-MCP-03): to the person, their lead and admins; the team roster stays category only (`001` Q-06). Decided by the product owner (Navaneeth), 5 October 2026, reversing the original answer — **Never**, proposed with the design and accepted on 11 September 2026. Rationale (the owner's): people do not put sensitive personal information in a leave reason, so the portal's own rule (NFR-05) is the right boundary for both channels. |
| **Q-04** | Token lifetime? | **90 days**, revocable earlier. Not asked; a token that never expires is a credential nobody remembers issuing. |
| **Q-05** | Money through MCP? | **Yes, for managers and admins**, because the route guards already decide that and "all APIs" was the instruction. `003` §9 still governs: tools return what the page shows, sorted by name. |

---

## 8. What this must not become

An MCP endpoint is the shortest path from "a tool" to "a system that does
things nobody asked for". Three things keep it honest:

- A tool never has more access than the person holding the token. If that
  stops being true, the fix is to delete the tool, not to add a check.
- Writes are confirmed, not inferred. "Book me Friday" shows the booking
  before it exists.
- A token is a credential. It is shown once, expires, is revocable, cannot
  mint another, and its use is logged separately from the web app's.
