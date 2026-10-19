"use client";

import { useState } from "react";

import { ReviewItemCard } from "@/components/crm/review-item";
import { useCrm } from "@/components/crm/shell";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getCrmMembers, listCrmReview } from "@/lib/api/crm";
import type { ReviewListQuery } from "@/lib/api/crm-types";
import { useAsync } from "@/lib/use-async";

/**
 * Review — spec 009 FR-CRMAI-REV-01 to -03, -07, FR-CRMAI-FLAG-04; plan §5.9.
 *
 * Suggestions waiting for a person's yes, oldest first. A Member sees and
 * decides their own; a workspace Admin also sees everyone's (All) and the
 * items decided or expired in the last 90 days (Decided). Viewers have no
 * queue. An item untouched for 14 days expires on its own.
 */

type Tab = "mine" | "all" | "decided";

const QUERY: Record<Tab, ReviewListQuery> = {
  mine: { scope: "mine", status: "open", limit: 100 },
  all: { scope: "all", status: "open", limit: 100 },
  decided: { scope: "all", status: "decided", limit: 100 },
};

const EMPTY: Record<Tab, string> = {
  mine: "Nothing to review.",
  all: "Nobody has anything to review.",
  decided: "Nothing was decided in the last 90 days.",
};

export default function ReviewPage() {
  const { bootstrap, reload: reloadBootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const zone = bootstrap.me.timezone;
  const admin = bootstrap.role === "admin";
  const viewer = bootstrap.role === "viewer";
  const [tab, setTab] = useState<Tab>("mine");
  const [message, setMessage] = useState<string | null>(null);
  const shown: Tab = admin ? tab : "mine";
  const review = useAsync(
    () => (viewer ? Promise.resolve(null) : listCrmReview(ws, QUERY[shown])),
    [ws, shown, viewer]
  );
  const members = useAsync(() => (viewer ? Promise.resolve([]) : getCrmMembers(ws)), [ws, viewer]);

  if (viewer) {
    return <p className="text-sm text-muted-foreground">Viewers have no review queue.</p>;
  }

  function decided(text: string) {
    setMessage(text);
    review.reload();
    // The sidebar's open count comes from bootstrap.
    reloadBootstrap();
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-heading text-lg font-semibold">Review</h1>
        <p className="text-sm text-muted-foreground">
          Suggestions from email, meetings, enrichment and the follow-up rules. Nothing changes until
          you approve it.
        </p>
      </div>
      {admin && (
        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value as Tab);
            setMessage(null);
          }}
        >
          <TabsList>
            <TabsTrigger value="mine">Mine</TabsTrigger>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="decided">Decided</TabsTrigger>
          </TabsList>
        </Tabs>
      )}
      {message && (
        <p role="status" className="text-sm text-muted-foreground">
          {message}
        </p>
      )}
      {review.error ? (
        <p className="text-sm text-muted-foreground">{review.error}</p>
      ) : !review.data ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : review.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{EMPTY[shown]}</p>
      ) : (
        <div className="space-y-3">
          {review.data.total > review.data.items.length && (
            <p className="text-xs text-muted-foreground">
              Showing {review.data.items.length} of {review.data.total}, oldest first.
            </p>
          )}
          {review.data.items.map((item) => (
            <ReviewItemCard
              key={item.id}
              workspace={ws}
              item={item}
              timeZone={zone}
              lists={bootstrap.lists}
              members={members.data ?? []}
              showOwner={shown !== "mine"}
              labs={bootstrap.workspace.tva_linked}
              numberFormat={bootstrap.me.number_format}
              onDecided={decided}
            />
          ))}
        </div>
      )}
    </div>
  );
}
