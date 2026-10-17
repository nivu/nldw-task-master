"""No task text in the logs — spec 008 NFR-TASK-04; contracts §0 "Logs".

Every task route and tool logs as the portal does, in structured JSON with
ids and outcome only. A run that creates, reads, changes, links, completes,
reopens, deletes and restores tasks through the routes and the tools, a
refusal included, leaves no title or note in any captured log record.

Acceptance criteria (spec §12, phase 1):
- "No task title, note or transcript appears in the logs of a test run that
  writes tasks" — test_no_title_or_note_in_the_logs.
"""

from __future__ import annotations

import asyncio
import logging

from tasks_fake import ID, TaskWorld

from app.mcp import tasks as tools

TITLE = "Zqxtitle dentist"
NOTE = "Zqxnote root canal"


class Ctx:
    def __init__(self, who: str) -> None:
        self.headers = {"authorization": f"Bearer {ID[who]}"}


def test_no_title_or_note_in_the_logs(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    world = TaskWorld(monkeypatch)
    task = world.add("member", title=TITLE, notes=NOTE, tags=["zqxtag"])
    crm = world.add("member", title=TITLE, notes=NOTE, links=[world.link(world.acme, "company")])
    world.call("member", "GET", "")
    world.call("member", "GET", f"/{task['id']}")
    world.call("member", "PATCH", f"/{task['id']}", json={"title": f"{TITLE} 2", "notes": NOTE})
    world.call(
        "member", "POST", f"/{task['id']}/links", json=world.link(world.lumen, "tva_project")
    )
    world.call("member", "POST", f"/{task['id']}/complete")
    world.call("member", "POST", f"/{task['id']}/reopen")
    world.call("member", "DELETE", f"/{task['id']}")
    world.call("member", "POST", f"/{task['id']}/restore")
    world.call("owner", "PATCH", f"/{task['id']}", json={"title": TITLE})  # refused, 404
    world.call("member", "PATCH", f"/{crm['id']}", json={"status": "done"})
    asyncio.run(tools.task_add(Ctx("member"), TITLE, notes=NOTE))
    asyncio.run(tools.task_list(Ctx("member")))
    asyncio.run(tools.task_update(Ctx("member"), crm["id"], {"notes": f"{NOTE} 3"}))

    assert caplog.records, "nothing was logged, so nothing was checked"
    text = "\n".join(
        f"{r.getMessage()} {r.args!r} {getattr(r, '__dict__', {})!r}" for r in caplog.records
    ).lower()
    for secret in ("zqxtitle", "zqxnote", "zqxtag"):
        assert secret not in text
