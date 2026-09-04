"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createAllocation,
  createProject,
  deleteAllocation,
  errorMessage,
  setProjectPhase,
  updateProject,
} from "@/lib/api/portal";
import type { AllocationRow, Phase, PortalUser, Project } from "@/lib/api/types";
import { PHASE_LABEL } from "@/lib/api/types";

const PHASES: Phase[] = ["pre", "delivery", "support"];
const today = () => new Date().toISOString().slice(0, 10);

/**
 * Projects, phases and allocations — spec 002 §5.1, §5.2.
 *
 * Two things here read as ordinary UI and are actually the product's opinions:
 *
 * Projects archive, never delete (FR-PROJ-04). Effort logged against a finished
 * project is precisely the history the analytics exist to report on.
 *
 * An allocation over 100% is recorded and then reported, not refused
 * (FR-ALLOC-04). Over-allocating somebody mid-crunch is a real thing an admin
 * does, and a product that cannot record it cannot warn about it either.
 */
export function ProjectsPanel({
  projects,
  allocations,
  people,
  onDone,
  onError,
}: {
  projects: Project[];
  allocations: AllocationRow[];
  people: PortalUser[];
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add a project</CardTitle>
          <CardDescription>
            Give it phases once it exists. Effort is logged against a phase, so
            &ldquo;400 hours&rdquo; can tell a delivery that overran apart from a
            year of unbudgeted support.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              try {
                await createProject({ name, client: client.trim() || null });
                onDone(`Added ${name}.`);
                setName("");
                setClient("");
              } catch (err) {
                onError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="pname">Name</Label>
              <Input id="pname" value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pclient">Client</Label>
              <Input
                id="pclient"
                value={client}
                onChange={(e) => setClient(e.target.value)}
                placeholder="Internal"
              />
            </div>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Adding…" : "Add"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {projects.map((project) => (
        <Card key={project.id} className={project.is_archived ? "opacity-60" : undefined}>
          <CardContent className="space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{project.name}</span>
              <span className="text-xs text-muted-foreground">
                {project.client ?? "Internal"}
              </span>
              {project.is_archived && <Badge variant="outline">archived</Badge>}
              <div className="ml-auto flex gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setExpanded(expanded === project.id ? null : project.id)}
                >
                  {expanded === project.id ? "Hide" : "Phases & people"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    try {
                      await updateProject(project.id, { is_archived: !project.is_archived });
                      onDone(
                        `${project.name} ${project.is_archived ? "restored" : "archived"}.`
                      );
                    } catch (err) {
                      onError(errorMessage(err));
                    }
                  }}
                >
                  {project.is_archived ? "Restore" : "Archive"}
                </Button>
              </div>
            </div>

            {(project.phases ?? []).length > 0 && (
              <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                {(project.phases ?? []).map((phase) => (
                  <span key={phase.id} className="rounded bg-muted px-2 py-1">
                    {PHASE_LABEL[phase.phase]}: {phase.starts_on} → {phase.ends_on}
                    {phase.budget_hours && ` · ${phase.budget_hours}h`}
                  </span>
                ))}
              </div>
            )}

            {expanded === project.id && (
              <div className="space-y-4 border-t pt-3">
                <PhaseForm project={project} onDone={onDone} onError={onError} />
                <AllocationForm
                  project={project}
                  people={people}
                  allocations={allocations.filter((a) => a.project_id === project.id)}
                  onDone={onDone}
                  onError={onError}
                />
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function PhaseForm({
  project,
  onDone,
  onError,
}: {
  project: Project;
  onDone: (m: string) => void;
  onError: (m: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("delivery");
  const [startsOn, setStartsOn] = useState(today());
  const [endsOn, setEndsOn] = useState(today());
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          await setProjectPhase(project.id, {
            phase,
            starts_on: startsOn,
            ends_on: endsOn,
            budget_hours: budget.trim() || null,
          });
          onDone(`${PHASE_LABEL[phase]} set for ${project.name}.`);
        } catch (err) {
          onError(errorMessage(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="space-y-1.5">
        <Label className="text-xs">Phase</Label>
        <select
          className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          value={phase}
          onChange={(e) => setPhase(e.target.value as Phase)}
        >
          {PHASES.map((p) => (
            <option key={p} value={p}>
              {PHASE_LABEL[p]}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs">From</Label>
        <Input type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} required />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs">To</Label>
        <Input type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} required />
      </div>
      <div className="space-y-1.5">
        {/* Q-05 — hours, not money. Money needs per-person rates, which is
            salary-adjacent data in a system every lead can read. */}
        <Label className="text-xs">Budget (hours)</Label>
        <Input
          type="number"
          min="0"
          step="1"
          className="w-28"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          placeholder="optional"
        />
      </div>
      <Button type="submit" variant="outline" size="sm" disabled={busy}>
        {busy ? "Saving…" : "Set phase"}
      </Button>
    </form>
  );
}

function AllocationForm({
  project,
  people,
  allocations,
  onDone,
  onError,
}: {
  project: Project;
  people: PortalUser[];
  allocations: AllocationRow[];
  onDone: (m: string) => void;
  onError: (m: string) => void;
}) {
  const [userId, setUserId] = useState("");
  const [startsOn, setStartsOn] = useState(today());
  const [endsOn, setEndsOn] = useState(today());
  const [percent, setPercent] = useState("50");
  const [busy, setBusy] = useState(false);

  return (
    <div className="space-y-2">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          try {
            await createAllocation({
              project_id: project.id,
              user_id: userId,
              starts_on: startsOn,
              ends_on: endsOn,
              percent,
            });
            onDone(`Allocated to ${project.name}.`);
            setUserId("");
          } catch (err) {
            onError(errorMessage(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="space-y-1.5">
          <Label className="text-xs">Person</Label>
          <select
            className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            required
          >
            <option value="">Choose…</option>
            {people
              .filter((p) => p.is_active)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">From</Label>
          <Input type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">To</Label>
          <Input type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          {/* Q-02 — a share of CAPACITY, so it shrinks with approved leave
              without anybody editing the allocation. */}
          <Label className="text-xs">% of capacity</Label>
          <Input
            type="number"
            min="1"
            max="100"
            step="5"
            className="w-24"
            value={percent}
            onChange={(e) => setPercent(e.target.value)}
            required
          />
        </div>
        <Button type="submit" variant="outline" size="sm" disabled={busy || !userId}>
          {busy ? "Saving…" : "Allocate"}
        </Button>
      </form>

      {allocations.length > 0 && (
        <div className="space-y-1">
          {allocations.map((allocation) => (
            <div key={allocation.id} className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">
                {allocation.display_name} @{allocation.percent}% · {allocation.starts_on} →{" "}
                {allocation.ends_on}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6"
                onClick={async () => {
                  try {
                    await deleteAllocation(allocation.id);
                    // FR-ALLOC-05 — removing intent never removes recorded fact.
                    onDone("Allocation removed. Hours already logged are kept.");
                  } catch (err) {
                    onError(errorMessage(err));
                  }
                }}
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
