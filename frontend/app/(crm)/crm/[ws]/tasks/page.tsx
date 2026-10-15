"use client";

import { useState } from "react";

import { ObjectList } from "@/components/crm/object-list";
import { useCrm } from "@/components/crm/shell";
import { TaskDialog } from "@/components/crm/task-dialog";
import { Button } from "@/components/ui/button";
import { getCrmMembers } from "@/lib/api/crm";
import { useAsync } from "@/lib/use-async";

/**
 * Tasks — spec 007 FR-CRM-TASK-01, -02, FR-CRM-VIEW-01 to -09, -11,
 * FR-CRM-UI-02, -06; Q-57.
 *
 * Tasks on their saved views: Open tasks by owner (a table grouped by
 * assignee) and My week (a kanban by bucket), or any the person saves. Each
 * shows the records it is linked to; a task has no page of its own, so
 * opening one opens the first of them. A Viewer sees the tasks and may
 * change the status of the ones assigned to them; nothing else.
 */
export default function TasksPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const canEdit = bootstrap.role !== "viewer";
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState(0);
  const { data: members } = useAsync(
    () => (canEdit ? getCrmMembers(ws) : Promise.resolve([])),
    [ws, canEdit]
  );

  return (
    <>
      <ObjectList
        kind="task"
        title="Tasks"
        refreshKey={created}
        actions={canEdit && <Button onClick={() => setAdding(true)}>New task</Button>}
      />
      {canEdit && members && (
        <TaskDialog
          open={adding}
          onOpenChange={setAdding}
          workspace={ws}
          bootstrap={bootstrap}
          members={members}
          onCreated={() => setCreated((n) => n + 1)}
        />
      )}
    </>
  );
}
