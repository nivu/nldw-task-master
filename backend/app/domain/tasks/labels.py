"""Fixed labels — spec 008 FR-TASK-CHG-11; data-model §3.1, §3.3.

A task with a workspace takes its status and bucket labels from that
workspace's option lists, as today. A task with no workspace has no option
list, so its labels are fixed here. Priority and source have no option list
in any workspace: their labels come from here for every task.
"""

from __future__ import annotations

from typing import Final

#: FR-TASK-DATA-03 (T2), in their order.
STATUS: Final = {"open": "Open", "waiting": "Waiting", "done": "Done", "cancelled": "Cancelled"}
#: FR-TASK-DATA-08 (TQ-09).
BUCKET: Final = {
    "today": "Today",
    "this_week": "This week",
    "this_month": "This month",
    "later": "Later",
}
#: Spec §5.1 Priority (TQ-09), most urgent first.
PRIORITY: Final = {"urgent": "Urgent", "high": "High", "normal": "Normal", "low": "Low"}
#: Spec §5.1 Source, FR-TASK-DATA-12.
SOURCE: Final = {
    "manual": "Manual",
    "nldw": "NLDW",
    "voice": "Voice",
    "email_commitment": "Email commitment",
    "meeting_action": "Meeting action",
    "rule": "Rule",
    "import": "Import",
}
