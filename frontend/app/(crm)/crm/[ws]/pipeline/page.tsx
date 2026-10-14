"use client";

import Link from "next/link";
import { Gauge } from "lucide-react";

import { ObjectList } from "@/components/crm/object-list";
import { useCrm } from "@/components/crm/shell";
import { Button } from "@/components/ui/button";

/**
 * Pipeline — spec 007 FR-CRM-UI-02, FR-CRM-VIEW-01, -08, -11, FR-CRM-CAP-02.
 *
 * The open deals as a kanban by stage: the seeded `pipeline` view, its
 * columns headed by the count, the INR sum and the weighted sum (none of it
 * money for a Viewer, who gets the notice instead). Dragging a card to
 * another stage runs the stage form, the lost form or, in Labs, the
 * handoff. Labs admins and staff members reach the capacity check from here;
 * the server refuses it to anyone else whatever this page shows.
 */
export default function PipelinePage() {
  const { bootstrap } = useCrm();
  const capacity =
    bootstrap.workspace.tva_linked &&
    bootstrap.me.account_type === "staff" &&
    (bootstrap.role === "admin" || bootstrap.role === "member");

  return (
    <ObjectList
      kind="opportunity"
      title="Pipeline"
      lockedView="pipeline"
      actions={
        capacity && (
          <Button
            variant="outline"
            nativeButton={false}
            render={<Link href={`/crm/${bootstrap.workspace.key}/capacity`} />}
          >
            <Gauge className="size-4" />
            Capacity
          </Button>
        )
      }
    />
  );
}
