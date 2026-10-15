---
title: Connecting Claude
summary: Using the portal from Claude Code or Claude Desktop with a personal token.
order: 8
---

# Connecting Claude

The portal can be used from Claude — "am I free on Friday? book it", "what
did the team log this week?", "how is Project X tracking against budget?" —
through a personal access token. Claude then acts **as you**, with exactly
the access you have here and nothing more.

## From claude.ai

Add the portal as a custom connector using the address shown under Account →
Connect Claude. claude.ai sends you to the portal, where you sign in with
Google if you are not already, and asks you to **Allow** the connection. That
issues a token on your behalf; it appears in your token list and can be
revoked there, which ends the connection.

## Getting a token (Claude Code and Claude Desktop)

1. Open **Account** and find **Connect Claude**.
2. Give the token a name that says where it will live, such as *Claude on my
   laptop*, and choose **Create token**.
3. Copy the token straight away. It is shown once and never again. If you
   lose it, revoke it and create another.

Tokens last 90 days. Revoke one at any time from the same place; it stops
working on the next request.

## Adding it to Claude

**Claude Code** — the page shows a one-line command to paste into a terminal.
It registers the portal as a tool server called *nunnari-portal*.

**Claude Desktop** — under Settings → Connectors, add a custom connector with
the address shown on the page and a header of
`Authorization: Bearer <your token>`.

## What Claude can do

Everything you can do in the portal, and nothing you cannot:

- Read your calendar, balances and history; book or withdraw a day.
- Log your day against projects or activities; read your week.
- If you are a lead: see the team, your pending approvals (leave and comp-off
  together; an admin sees everyone's), and decide them;
  confirm or reopen your reports' weeks; see hours on every project and per
  project category, and the company-wide capacity forecast; create projects
  and allocate your own reports (never money).
- If you are a manager or admin: projects, allocations, effort, money —
  including profit by month and the allocation timeline.
- If you are an admin: people, roles, CTC periods, allowances, holidays,
  backfill and policy.
- If the owner has authorised you, whatever your role: the company dashboard
  (`ceo_dashboard`).

## The sales CRM from Claude

If you belong to a CRM workspace, the same connection carries the `crm_*`
tools, with the role you hold in each workspace:

| Your role in the workspace | Claude can |
|---|---|
| Any (Admin, Member, Viewer) | list your workspaces (`crm_workspaces`), run Today (`crm_today`), search (`crm_search`), read any record (`crm_get`) and lists by filter or saved view (`crm_list`). A Viewer gets no amounts |
| Admin or Member | create and update records (`crm_create`, `crm_update`), set a deal's next step (`crm_set_next_step`), add tasks and notes (`crm_add_task`, `crm_add_note`), move deals (`crm_move_stage`), do the Labs handoff (`crm_handoff`), merge companies (`crm_merge`), work campaign members (`crm_campaign_members`), complete tasks (`crm_complete_task`); a Viewer may complete a task assigned to them |
| Admin, and Members for their own numbers | read the dashboard (`crm_dashboard`) |
| Admin only | another member's Today, imports (`crm_import`) and running email sync now (`crm_run_email_sync`) |

Linking a Labs deal to an existing TVA project also needs you to be a TVA
manager or admin. Deleting records, settings, invitations, saved views and
exports stay in the web app.

If you belong to more than one workspace, Claude reads from the one you used
last, but every change must name its workspace, or it is refused with your
workspaces listed.

**CRM-only accounts** connect Claude from the CRM's own **Account** page
(`/crm/account`), the same way; their token reaches the CRM tools only.

Before anything is changed, Claude is instructed to show you exactly what it
is about to do and wait for your yes. If it does not, say no and tell an
admin.

## Leave reasons

Claude sees the **reason** on a leave request exactly where the portal shows
it, and only to the same people: you, on your own requests; your lead and
admins, only while the request is waiting for a decision, as on the portal's
approvals screen. Once it is approved or rejected, they no longer see it. A
lead or manager who is not your lead never sees it, and the team-day list
shows the category only, whatever the admin settings say, as in the portal.

## Keeping it safe

- A token is a credential. Treat it like a password: do not paste it into
  chat, do not share it.
- A token cannot create other tokens. If one leaks, revoking it ends the
  matter.
- Every action taken through Claude is logged as coming through a token, so
  it can be told apart from what you did in the browser.
- The portal never writes a token into its logs, even one pasted into an
  address by mistake. Revoke it anyway if that happens.
