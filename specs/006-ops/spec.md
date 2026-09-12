# Org Operations — V6

| | |
|---|---|
| **Feature** | `006-ops` |
| **Status** | Settled 12 September 2026 — decisions in §15, being built |
| **Source** | Product owner, 12 September 2026: "implement all these features" |
| **Depends on** | `001`–`005`, all in production |

Thirteen additions, grouped. Each is small on its own; together they turn the
portal from a system of record into something that runs the week.

| # | Feature | Who |
|---|---|---|
| 1 | Nudges — nothing logged today, gaps this week, over-allocation next week | everyone / leads / managers |
| 2 | Weekly timesheet sign-off | leads |
| 3 | Client-ready effort statements | managers |
| 4 | Utilisation and bench | leads / managers |
| 5 | Project health | managers |
| 6 | Invoicing milestones | managers |
| 7 | Hiring signal | managers |
| 8 | Onboarding and offboarding checklists | admins |
| 9 | Leave calendar feed, morning Slack post | everyone |
| 10 | Holidays by location, comp-off | everyone / admins |
| 11 | Quarterly self-summary | everyone / leads |
| 12 | OAuth for the claude.ai connector | everyone |
| 13 | Weekly leadership digest | managers / admins |

`003` §9 and `005` §8 govern every money figure here, unchanged.

---

## 1. Nudges — FR-NUDGE

Slack, matched to a person by email (`001` FR-NOTIF); silent without a bot
token. All times Asia/Kolkata. Each is a setting an admin can switch off.

| ID | Requirement |
|---|---|
| FR-NUDGE-01 | 18:00 on a working day: anyone with nothing logged for today, who is not on full-day leave, is told, with a link. Setting `nudge_hour`, `nudges_enabled`. |
| FR-NUDGE-02 | Friday 17:00: each lead receives their reports' missing days for the week. |
| FR-NUDGE-03 | Monday 09:00: each manager receives who is over 100% in the coming week. |
| FR-NUDGE-04 | An admin can send themselves a test message to prove the token works. |

## 2. Timesheet sign-off — FR-SIGN

| ID | Requirement |
|---|---|
| FR-SIGN-01 | A lead confirms a report's week (Monday-dated). A confirmed week is shown as such to the person and in statements. |
| FR-SIGN-02 | A week nobody confirmed by the time its edit window closes (`002` Q-01: end of week + 7 days) is confirmed automatically, marked *auto*. |
| FR-SIGN-03 | A lead may reopen a confirmed week while it is still editable; the person is told. |
| FR-SIGN-04 | Confirmation is audited. |

## 3. Effort statements — FR-STMT

| ID | Requirement |
|---|---|
| FR-STMT-01 | Per project per month: hours by person by day, by phase, totals; **no money**. |
| FR-STMT-02 | Available as CSV and as a printable page. |
| FR-STMT-03 | Weeks not yet confirmed are marked on the statement. |
| FR-STMT-04 | Manager tier. |

## 4. Utilisation and bench — FR-UTIL

| ID | Requirement |
|---|---|
| FR-UTIL-01 | Per person per month: billable hours (projects with a client), internal project hours, activity hours, capacity hours (working days × 8, less approved leave), utilisation = billable ÷ capacity. Q-04. |
| FR-UTIL-02 | Target utilisation is a setting (`utilisation_target`, 80). Below target is shown, never ranked. |
| FR-UTIL-03 | Bench: per person per week for the next N weeks, allocated %; under `bench_threshold` (60) is bench. Manager tier. |
| FR-UTIL-04 | Leads see utilisation for their reports; managers everyone. |

## 5. Project health — FR-HEALTH

| ID | Requirement |
|---|---|
| FR-HEALTH-01 | Per project: budget burn (logged ÷ budget) against timeline elapsed (working days so far ÷ total), margin to date against planned margin, and a red / amber / green per dimension and overall. |
| FR-HEALTH-02 | Rules (§15 Q-05): **burn** green if burn ≤ elapsed + 10 points, amber ≤ +25, red beyond or over budget; **margin** green if to-date margin % ≥ planned − 5 points, amber ≥ −15, red below; **schedule** red if past the timeline end with budget remaining unspent under 90%; overall is the worst. |
| FR-HEALTH-03 | Every input is shown next to the colour, so a red can be argued with. |

## 6. Invoicing milestones — FR-MILE

| ID | Requirement |
|---|---|
| FR-MILE-01 | A manager adds dated amounts to a project: name, due date, amount; marks one invoiced with a date. |
| FR-MILE-02 | When a project has milestones, its monthly revenue is the milestones due that month; otherwise the even spread of `005` §3.2 (Q-02). |
| FR-MILE-03 | If milestones do not sum to the project's revenue, the project and the monthly table say so. |
| FR-MILE-04 | Cash view: invoiced vs due, per month. |

## 7. Hiring signal — FR-HIRE

| ID | Requirement |
|---|---|
| FR-HIRE-01 | Per month for the next N: demand hours (allocations × capacity), supply hours (headcount capacity × target utilisation), shortfall, FTE needed (shortfall ÷ one person's hours at target), and the cost of those FTE at a given annual CTC. |
| FR-HIRE-02 | The CTC to price with is an input to the view, never a person's. |

## 8. Checklists — FR-CHK

| ID | Requirement |
|---|---|
| FR-CHK-01 | Admin templates per kind (onboarding, offboarding), seeded with sensible defaults, editable. |
| FR-CHK-02 | An admin starts a checklist for a person; items carry an owner and a due date; ticking records who and when. |
| FR-CHK-03 | Starting an offboarding checklist does not deactivate the account; that stays a separate, deliberate action. |

## 9. Calendar feed and morning post — FR-FEED

| ID | Requirement |
|---|---|
| FR-FEED-01 | Each person has a private iCal URL (unguessable key derived per person) with approved leave and holidays for the people they may see — category only, never reasons. |
| FR-FEED-02 | The key can be rotated from the Account page. |
| FR-FEED-03 | 09:00 on a working day, a post to a configured Slack channel (`slack_out_channel`) lists who is out, on half day or working from home. Off when the setting is empty. |

## 10. Locations and comp-off — FR-LOC, FR-COMP

| ID | Requirement |
|---|---|
| FR-LOC-01 | Locations are admin-managed; one default exists; each person belongs to one. |
| FR-LOC-02 | A holiday applies everywhere or to one location. A person's calendar, timesheet, capacity and coverage use the holidays that apply to them. |
| FR-COMP-01 | A person who worked a weekend or a holiday claims a comp-off (full or half day) with a note; their lead approves or rejects. |
| FR-COMP-02 | An approved credit is valid for `compoff_valid_days` (90) and lapses nightly after that. |
| FR-COMP-03 | Booking a `compoff` day consumes the oldest valid credit; it draws on no allowance. Withdrawing the booking returns the credit. |
| FR-COMP-04 | Balances show comp-off available; the team view shows comp-off as its own category. |

## 11. Quarterly self-summary — FR-REV

| ID | Requirement |
|---|---|
| FR-REV-01 | For a quarter, the person sees their own hours by project and every note they wrote, grouped by project and month — their own data only. |
| FR-REV-02 | They write a summary and submit it; it can be edited until the quarter's review is closed by their lead. |
| FR-REV-03 | Their lead reads it alongside the same figures. No ratings, no scores, nothing compared across people. |

## 12. OAuth — FR-OAUTH

| ID | Requirement |
|---|---|
| FR-OAUTH-01 | The backend is an OAuth 2.1 authorization server with dynamic client registration, PKCE and protected-resource metadata, as the MCP spec requires; `MCP_PUBLIC_URL`'s origin is the issuer. |
| FR-OAUTH-02 | Authorising sends the person to the portal, where they are already signed in with Google (or sign in), and approve the client by name. |
| FR-OAUTH-03 | An approval issues a personal token (`004`) named for the client, plus a refresh token. It appears in the Account page's list and is revocable there; revoking it ends the connection. |
| FR-OAUTH-04 | Personal tokens keep working unchanged for Claude Code and Desktop. |

## 13. Weekly digest — FR-DIGEST

| ID | Requirement |
|---|---|
| FR-DIGEST-01 | Monday 09:00 to every manager and admin: last week's coverage, who is over-allocated this week, margin to date per active project, people with no CTC, bench. Setting `digest_enabled`. |
| FR-DIGEST-02 | An admin can run it now. |

## Y. Year frames — FR-YEAR

Added 12 September 2026 after the first release of this spec.

| ID | Requirement |
|---|---|
| FR-YEAR-01 | Every month-based view — monthly profit, the resources timeline in months, utilisation, the hiring signal, and a person's leave history — MUST show a whole year at a time, switchable between the **calendar year** (January to December) and the **financial year** (April to March), with the year steppable. |
| FR-YEAR-02 | The choice MUST be remembered on the device and shared by every such view, so switching once carries everywhere. |
| FR-YEAR-03 | The APIs take explicit month ranges (`start`/`end` as YYYY-MM); no endpoint assumes a calendar year. |

---

## 14. Data model (new)

```text
locations               id, name
profiles              + location_id → locations
holidays              + location_id → locations (NULL = everywhere)
bookings.category     + 'compoff'
compoff_credits         user_id, worked_on, days, note, status, expires_on, booking_id, decided_by/at, decision_note
timesheet_confirmations user_id, week_start, status confirmed|auto, confirmed_by/at, note
project_milestones      project_id, name, due_on, amount, invoiced_on
checklist_templates     kind, position, label
checklists              user_id, kind, created_by/at, closed_at
checklist_items         checklist_id, position, label, owner_id, due_on, done_at, done_by
reviews                 user_id, quarter, summary, submitted_at, closed_by/at
oauth_clients           client_id, client_secret, metadata, registered_at
oauth_codes             code, client_id, user_id, code_challenge, redirect_uri, scopes, expires_at
oauth_refresh_tokens    token_hash, client_id, user_id, api_token_id, expires_at, revoked_at
api_tokens            + client_id (NULL for tokens a person issued by hand)
app_settings          + utilisation_target, bench_threshold, nudge_hour, nudges_enabled,
                        slack_out_channel, compoff_valid_days, digest_enabled
```

All new tables: RLS on, no browser grants. Backend only.

---

## 15. Decisions

| ID | Question | Decision |
|---|---|---|
| Q-01 | Channel | **Slack**, bot token supplied by the product owner. |
| Q-02 | Milestones vs even spread | **Milestones override when present.** |
| Q-03 | OAuth now? | **Yes.** |
| Q-04 | Billable | **Projects with a client.** Internal projects and activities are not. |
| Q-05 | Sign-off | **The lead; auto-confirm when the edit window closes.** |
| Q-06 | Locations | **One for now**, modelled per location. |
| Q-07 | Comp-off validity | **90 days.** |
| Q-08 | Statement format | **CSV + printable page.** |
| Q-09 | Health thresholds | As FR-HEALTH-02. Not asked; conservative defaults, shown beside every colour. |
| Q-10 | Feed key | HMAC of the person's id with a server secret; rotation stores a per-person salt. Not asked. |

---

## 16. What this must not become

- Nudges are reminders, not surveillance: sent to the person, never to a
  channel; a lead sees gaps, not a league table of who forgot.
- Utilisation, bench and health are sorted by name or by project; nothing
  ranks people, and utilisation is never shown to the person as a score.
- The quarterly summary is the person's words about their own work. No
  ratings, no comparison, nothing the lead writes that the person cannot see.
- The calendar feed carries categories, never reasons.
