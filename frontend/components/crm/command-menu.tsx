"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Command } from "cmdk";
import { Briefcase, Building2, CheckSquare, Link2, NotebookPen, Plus, Search, User, Zap } from "lucide-react";

import { useCrm } from "@/components/crm/shell";
import { ViewIcon } from "@/components/crm/view-switcher";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { createCrmRecord, listCrmViews, searchCrm } from "@/lib/api/crm";
import type {
  CrmCompany,
  CrmDuplicates,
  CrmPerson,
  CrmSearchResult,
  CrmTask,
  SearchHit,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { OBJECT_PATH } from "@/lib/crm/view-fields";

/**
 * The command menu — spec 007 FR-CRM-SRCH-01 to -03; plan §5.8.
 *
 * Cmd/Ctrl+K anywhere inside the CRM opens one search across the current
 * workspace's companies, people, deals, tasks and notes, with its saved views
 * by name. From it the person jumps to a record or a view, creates a record
 * named after what they typed, or runs a quick action of the record page they
 * are on. A record page names itself and offers its quick actions with
 * `useQuickActions`; a new task needs a record to link to (FR-CRM-TASK-01), so
 * it is offered only there. The server decides every result (one workspace,
 * no Trash, no amounts for a Viewer).
 */

export interface QuickAction {
  id: string;
  label: string;
  run: () => void;
}

/** The record page that is open: a new task from the menu links to it. */
export interface OpenRecord {
  record_type: "company" | "person" | "opportunity";
  record_id: string;
}

interface Offered {
  record: OpenRecord;
  actions: QuickAction[];
}

const QuickActionsContext = createContext<((offered: Offered | null) => void) | null>(null);

/** Name the open record and offer its quick actions in the command menu while
 *  mounted; `record` is null until the page has loaded it. */
export function useQuickActions(record: OpenRecord | null, actions: QuickAction[]) {
  const set = useContext(QuickActionsContext);
  const recordType = record?.record_type;
  const recordId = record?.record_id;
  useEffect(() => {
    set?.(recordType && recordId ? { record: { record_type: recordType, record_id: recordId }, actions } : null);
    return () => set?.(null);
  }, [set, recordType, recordId, actions]);
}

const MIN_CHARS = 2;
const DEBOUNCE_MS = 150;

const GROUPS: { key: keyof Omit<CrmSearchResult, "views">; heading: string; icon: typeof Building2 }[] = [
  { key: "companies", heading: "Companies", icon: Building2 },
  { key: "people", heading: "People", icon: User },
  { key: "opportunities", heading: "Opportunities", icon: Briefcase },
  { key: "tasks", heading: "Tasks", icon: CheckSquare },
  { key: "notes", heading: "Notes", icon: NotebookPen },
];

function hitHref(ws: string, hit: SearchHit): string {
  if (hit.record_type === "task" || hit.record_type === "note") {
    if (hit.opens) return `/crm/${ws}/${OBJECT_PATH[hit.opens.record_type]}/${hit.opens.record_id}`;
    return `/crm/${ws}/tasks`;
  }
  return `/crm/${ws}/${OBJECT_PATH[hit.record_type]}/${hit.id}`;
}

function hitDetail(hit: SearchHit): string | null {
  const parts = [
    hit.domain,
    hit.email,
    hit.company_name,
    hit.lead_stage_label,
    hit.stage_label,
    hit.status_label,
    hit.kind_label,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

type Creatable = "companies" | "people" | "tasks";

const itemClass =
  "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm data-[selected=true]:bg-muted";

export function CommandMenu({ children }: { children: React.ReactNode }) {
  const { bootstrap } = useCrm();
  const router = useRouter();
  const pathname = usePathname();
  const ws = bootstrap.workspace.key;
  const canEdit = bootstrap.role !== "viewer";
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<CrmSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicates, setDuplicates] = useState<{ object: Creatable; found: CrmDuplicates } | null>(null);
  const [busy, setBusy] = useState(false);
  const [offered, setOffered] = useState<Offered | null>(null);
  const [views, setViews] = useState<CrmSearchResult["views"]>([]);
  const asked = useRef(0);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The saved views, for jumping before anything is typed.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    listCrmViews(ws)
      .then((all) => {
        if (!cancelled) setViews(all.filter((v) => !v.hidden));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [open, ws]);

  // Only the newest answer is shown: a slow earlier one never overwrites it.
  useEffect(() => {
    const text = q.trim();
    if (text.length < MIN_CHARS) return;
    const ask = ++asked.current;
    const timer = window.setTimeout(() => {
      searchCrm(ws, text)
        .then((result) => {
          if (ask !== asked.current) return;
          setFound(result);
          setError(null);
        })
        .catch((err) => {
          if (ask === asked.current) setError(errorMessage(err));
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [q, ws]);

  const close = useCallback(() => {
    setOpen(false);
    setQ("");
    setFound(null);
    setError(null);
    setDuplicates(null);
  }, []);

  // A page change closes the menu; the next page offers its own actions.
  const [shownPath, setShownPath] = useState(pathname);
  if (shownPath !== pathname) {
    setShownPath(pathname);
    if (open) close();
  }

  function go(href: string) {
    close();
    router.push(href);
  }

  async function create(object: Creatable, checkDuplicates: boolean) {
    const text = q.trim();
    const [first, ...rest] = text.split(/\s+/);
    const fields =
      object === "companies"
        ? { name: text, owner_id: bootstrap.me.id }
        : object === "people"
          ? { first_name: first, ...(rest.length ? { last_name: rest.join(" ") } : {}), owner_id: bootstrap.me.id }
          : { title: text, links: offered ? [offered.record] : [], assignee_id: bootstrap.me.id };
    setBusy(true);
    setError(null);
    try {
      const made = await createCrmRecord<CrmCompany | CrmPerson | CrmTask>(ws, object, fields, checkDuplicates);
      if ("created" in made) {
        setDuplicates({ object, found: made.duplicates });
        return;
      }
      go(object === "tasks" ? `/crm/${ws}/tasks` : `/crm/${ws}/${object}/${made.id}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const text = q.trim();
  const searching = text.length >= MIN_CHARS;
  const hits = searching ? found : null;
  const shownViews = searching
    ? (hits?.views ?? [])
    : views.slice(0, 8);

  return (
    <QuickActionsContext.Provider value={setOffered}>
      {children}
      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
        <DialogContent className="top-[20%] translate-y-0 gap-0 p-0 sm:max-w-xl" showCloseButton={false}>
          <DialogTitle className="sr-only">Search the CRM</DialogTitle>
          <Command shouldFilter={false} label="Search the CRM" className="flex flex-col">
            <div className="flex items-center gap-2 border-b px-3">
              <Search className="size-4 shrink-0 text-muted-foreground" />
              <Command.Input
                autoFocus
                value={q}
                onValueChange={(value) => {
                  setQ(value);
                  setDuplicates(null);
                }}
                placeholder={`Search ${bootstrap.workspace.name}…`}
                aria-label="Search the CRM"
                className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
            </div>
            <Command.List className="max-h-[60vh] overflow-y-auto p-2">
              {error && (
                <p role="alert" className="px-2 py-1.5 text-sm text-destructive">
                  {error}
                </p>
              )}
              {searching && hits && <Command.Empty className="px-2 py-6 text-center text-sm text-muted-foreground">Nothing found.</Command.Empty>}

              {duplicates && (
                <Command.Group heading="Already in the CRM" className="text-xs text-muted-foreground">
                  {duplicates.found.exact && (
                    <Command.Item
                      value={`dup-${duplicates.found.exact.id}`}
                      onSelect={() => go(`/crm/${ws}/${duplicates.object}/${duplicates.found.exact!.id}`)}
                      className={itemClass}
                    >
                      {duplicates.found.exact.name ?? duplicates.found.exact.value}
                      <span className="text-xs text-muted-foreground">same {duplicates.found.exact.field.replace("_", " ")}</span>
                    </Command.Item>
                  )}
                  {duplicates.found.similar.map((s) => (
                    <Command.Item
                      key={s.id}
                      value={`dup-${s.id}`}
                      onSelect={() => go(`/crm/${ws}/${duplicates.object}/${s.id}`)}
                      className={itemClass}
                    >
                      {s.name}
                    </Command.Item>
                  ))}
                  {!duplicates.found.exact && (
                    <Command.Item
                      value="create-anyway"
                      disabled={busy}
                      onSelect={() => create(duplicates.object, false)}
                      className={itemClass}
                    >
                      <Plus className="size-4" /> Create “{text}” anyway
                    </Command.Item>
                  )}
                </Command.Group>
              )}

              {offered && (
                <Command.Group heading="This record" className="text-xs text-muted-foreground">
                  {offered.actions.map((a) => (
                    <Command.Item
                      key={a.id}
                      value={`action-${a.id}`}
                      onSelect={() => {
                        close();
                        a.run();
                      }}
                      className={itemClass}
                    >
                      <Zap className="size-4" /> {a.label}
                    </Command.Item>
                  ))}
                  <Command.Item
                    value="action-copy-link"
                    onSelect={() => {
                      void navigator.clipboard?.writeText(window.location.href).catch(() => {});
                      close();
                    }}
                    className={itemClass}
                  >
                    <Link2 className="size-4" /> Copy link
                  </Command.Item>
                </Command.Group>
              )}

              {hits &&
                GROUPS.map(({ key, heading, icon: Icon }) =>
                  hits[key].length ? (
                    <Command.Group key={key} heading={heading} className="text-xs text-muted-foreground">
                      {hits[key].map((hit) => (
                        <Command.Item
                          key={hit.id}
                          value={`${key}-${hit.id}`}
                          onSelect={() => go(hitHref(ws, hit))}
                          className={itemClass}
                        >
                          <Icon className="size-4 shrink-0" />
                          <span className="truncate text-foreground">{hit.name}</span>
                          {hitDetail(hit) && (
                            <span className="truncate text-xs text-muted-foreground">{hitDetail(hit)}</span>
                          )}
                        </Command.Item>
                      ))}
                    </Command.Group>
                  ) : null
                )}

              {shownViews.length > 0 && (
                <Command.Group heading="Views" className="text-xs text-muted-foreground">
                  {shownViews.map((v) => (
                    <Command.Item
                      key={v.id}
                      value={`view-${v.id}`}
                      onSelect={() =>
                        go(
                          v.system_key === "pipeline"
                            ? `/crm/${ws}/pipeline`
                            : `/crm/${ws}/${OBJECT_PATH[v.object]}?view=${v.id}`
                        )
                      }
                      className={itemClass}
                    >
                      <ViewIcon name={v.icon} />
                      <span className="truncate text-foreground">{v.name}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              )}

              {canEdit && searching && !duplicates && (
                <Command.Group heading="Create" className="text-xs text-muted-foreground">
                  {(
                    [
                      ["companies", "company"],
                      ["people", "person"],
                      ...(offered ? ([["tasks", "task"]] as const) : []),
                    ] as const
                  ).map(([object, noun]) => (
                    <Command.Item
                      key={object}
                      value={`create-${object}`}
                      disabled={busy}
                      onSelect={() => create(object, object !== "tasks")}
                      className={itemClass}
                    >
                      <Plus className="size-4" /> New {noun} “{text}”
                    </Command.Item>
                  ))}
                  <Command.Item
                    value="create-opportunities"
                    onSelect={() => go(`/crm/${ws}/opportunities`)}
                    className={itemClass}
                  >
                    <Plus className="size-4" /> New opportunity…
                  </Command.Item>
                </Command.Group>
              )}
            </Command.List>
          </Command>
        </DialogContent>
      </Dialog>
    </QuickActionsContext.Provider>
  );
}
