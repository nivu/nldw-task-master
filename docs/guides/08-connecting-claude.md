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

## Getting a token

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
- If you are a lead: see the team, your pending approvals, and decide them.
- If you are a manager or admin: projects, allocations, effort and money.

Before anything is changed, Claude is instructed to show you exactly what it
is about to do and wait for your yes. If it does not, say no and tell an
admin.

## What Claude never sees

The **reason** on any leave request, including your own. Reasons can be
health information, and a conversation with an assistant is not a place for
them. Where a reason exists, Claude is told to send you to the portal.

## Keeping it safe

- A token is a credential. Treat it like a password: do not paste it into
  chat, do not share it.
- A token cannot create other tokens. If one leaks, revoking it ends the
  matter.
- Every action taken through Claude is logged as coming through a token, so
  it can be told apart from what you did in the browser.
