"""Task UI copy — spec 008 NFR-TASK-05.

Team-facing copy frames tasks as helping each other, never monitoring: the
words "Employee" and "compliance" do not appear in the task components or
the task pages.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
FILES = sorted(
    [
        *(FRONTEND / "components" / "tasks").rglob("*.tsx"),
        FRONTEND / "app" / "(portal)" / "tasks" / "page.tsx",
        FRONTEND / "app" / "(crm)" / "crm" / "[ws]" / "my-tasks" / "page.tsx",
    ]
)
BANNED = re.compile(r"employee|compliance", re.IGNORECASE)


def test_the_task_ui_files_exist():
    assert len(FILES) >= 11
    assert all(f.exists() for f in FILES)


@pytest.mark.parametrize("path", FILES, ids=[str(f.relative_to(FRONTEND)) for f in FILES])
def test_no_employee_or_compliance(path):
    found = BANNED.findall(path.read_text(encoding="utf-8"))
    assert found == [], f"{path.relative_to(FRONTEND)} says {sorted(set(found))}"
