"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { errorMessage, flagUnrecognised, getTeamConsumption, getTeamDay } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";
import type { PersonBalances, TeamDay, TeamMemberDay } from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The lead's view — FR-LEAD.
 *
 * G-1: a lead can see, at a glance, who is available today and who is not.
 * FR-LEAD-05: the default is today, reachable without navigation or filtering,
 * so this page loads showing today and needs no interaction to be useful.
 *
 * There are no reasons anywhere on this page. Q-06: the roster shows the
 * category only. A reason — particularly for sick leave — is personal data
 * (NFR-05) and appears on the approvals screen, where a lead needs it to
 * decide, and nowhere else. The API does not even send it here.
 */
export default function TeamPage() {
  const [day, setDay] = useState<string>("");
  const [notice, setNotice] = useState<string | null>(null);

  const { data, error, setError, reload } = useAsync<{
    day: TeamDay;
    consumption: PersonBalances[];
  }>(
    async () => {
      const [teamDay, usage] = await Promise.all([
        getTeamDay(day || undefined),
        getTeamConsumption(),
      ]);
      return { day: teamDay, consumption: usage.people };
    },
    [day]
  );

  async function flag(person: TeamMemberDay) {
    if (!data) return;
    try {
      await flagUnrecognised({ user_id: person.user_id, date: data.day.date });
      setNotice(`${person.display_name} flagged as absent on ${data.day.date}.`);
      reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (error) {
    return (
      <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading the team…</p>;

  const { day: teamDay, consumption } = data;
  const isToday = teamDay.date === teamDay.today;
  const pretty = new Date(`${teamDay.date}T00:00:00`).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-heading text-lg font-semibold">
            {isToday ? "Today" : pretty}
          </h1>
          <p className="text-sm text-muted-foreground">
            {isToday && `${pretty} · `}
            {teamDay.holiday
              ? teamDay.holiday
              : teamDay.is_weekend
                ? "Weekend"
                : `${teamDay.summary.present} in, ${
                    teamDay.people.length - teamDay.summary.present
                  } out`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Input
            type="date"
            value={teamDay.date}
            onChange={(e) => setDay(e.target.value)}
            className="w-auto"
          />
          {!isToday && (
            <Button variant="outline" onClick={() => setDay("")}>
              Today
            </Button>
          )}
        </div>
      </div>

      {notice && (
        <div className="rounded-md bg-muted p-3 text-sm">{notice}</div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Present" value={teamDay.summary.present} />
        <Stat label="WFH" value={teamDay.summary.wfh} />
        <Stat label="Casual" value={teamDay.summary.casual} />
        <Stat label="Sick" value={teamDay.summary.sick} />
        <Stat label="Unrecognised" value={teamDay.summary.unrecognised} />
      </div>

      <Card>
        <CardContent className="divide-y p-0">
          {teamDay.people.length === 0 && (
            <p className="p-4 text-sm text-muted-foreground">
              Nobody reports to you yet.
            </p>
          )}
          {teamDay.people.map((person) => (
            <div key={person.user_id} className="flex items-center gap-3 p-3">
              <span className="flex-1 text-sm font-medium">{person.display_name}</span>

              {person.state === "present" ? (
                <>
                  <Badge variant="outline">Present</Badge>
                  {/* FR-LEAD-03. V1 has no attendance data, so an
                      unrecognised absence is set by hand, after the fact. */}
                  {teamDay.date < teamDay.today && !teamDay.is_weekend && !teamDay.holiday && (
                    <Button variant="ghost" size="sm" onClick={() => flag(person)}>
                      Mark absent
                    </Button>
                  )}
                </>
              ) : (
                <>
                  <span className="text-sm text-muted-foreground">
                    {person.category_label ?? "Absent, not booked"}
                    {person.duration === "0.5" && " (half day)"}
                  </span>
                  {/* FR-LEAD-02 — agreed absences must be distinguishable
                      from merely requested ones. */}
                  <Badge
                    variant={
                      person.state === "approved"
                        ? "secondary"
                        : person.state === "pending"
                          ? "outline"
                          : "destructive"
                    }
                  >
                    {person.state}
                  </Badge>
                </>
              )}
            </div>
          ))}
        </CardContent>
      </Card>

      {/* FR-LEAD-04 — per-person consumption for the current period. */}
      <section className="space-y-2">
        <h2 className="font-heading text-sm font-semibold">This month</h2>
        <Card>
          <CardContent className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="p-3 font-medium">Person</th>
                  {(["wfh", "casual", "sick"] as const).map((category) => (
                    <th key={category} className="p-3 text-right font-medium">
                      {CATEGORY_LABEL[category]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {consumption.map((person) => (
                  <tr key={person.user_id}>
                    <td className="p-3">{person.display_name}</td>
                    {(["wfh", "casual", "sick"] as const).map((category) => {
                      const balance = person.balances.find((b) => b.category === category);
                      return (
                        <td key={category} className="p-3 text-right tabular-nums">
                          <span className="font-medium">{balance?.used ?? "0.0"}</span>
                          <span className="text-muted-foreground">
                            {" "}
                            / {balance?.remaining ?? "0.0"} left
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="p-3">
        <p className={cn("text-2xl font-semibold tabular-nums", value === 0 && "text-muted-foreground")}>
          {value}
        </p>
        <p className="text-xs text-muted-foreground">{label}</p>
      </CardContent>
    </Card>
  );
}
