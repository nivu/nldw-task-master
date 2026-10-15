"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createToken, errorMessage, listTokens, revokeToken } from "@/lib/api/portal";
import type { TokenCreated, TokenList } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";

/**
 * Connect Claude — spec 004; spec 007 FR-CRM-ACC-14.
 *
 * Shared by the TVA account page and the CRM account page: a CRM-only
 * seller issues and revokes tokens through the same shared routes (contracts
 * §1), and those tokens reach only what the person may.
 *
 * A token is shown exactly once, in the response that created it, and the
 * page says so before the person clicks. There is no "show again": a person
 * who lost it issues a new one and revokes the old, which is the right habit
 * for a credential anyway.
 */
export function TokenManager() {
  const { data, error, setError, reload } = useAsync<TokenList>(() => listTokens(), []);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<TokenCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const mcpUrl = data?.mcp_url ?? null;
  const live = (data?.tokens ?? []).filter((t) => t.active);
  const dead = (data?.tokens ?? []).filter((t) => !t.active);

  async function issue(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setFresh(await createToken(name.trim()));
      setName("");
      reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be refused; the text is on screen regardless.
    }
  }

  const when = (iso: string | null) =>
    iso
      ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })
      : "never";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Connect Claude</CardTitle>
        <CardDescription>
          A personal access token lets Claude Code or Claude Desktop use the portal
          as you — the same access you have here, nothing more. Tokens last 90
          days, can be revoked below, and are shown once when created.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && (
          <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {!mcpUrl && data && (
          <p className="text-sm text-muted-foreground">
            MCP is not enabled on this deployment. An admin sets its address on the
            server.
          </p>
        )}

        {fresh && mcpUrl && (
          <div className="space-y-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            <p className="font-medium">
              Copy this token now. It will not be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 overflow-x-auto rounded bg-background px-2 py-1 font-mono text-xs">
                {fresh.token}
              </code>
              <Button size="sm" variant="outline" onClick={() => copy(fresh.token)}>
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <p>
              <span className="font-medium">Claude Code</span> — run this in a terminal:
            </p>
            <pre className="overflow-x-auto rounded bg-background p-2 font-mono text-xs">
              {`claude mcp add --transport http nunnari-portal ${mcpUrl} --header "Authorization: Bearer ${fresh.token}"`}
            </pre>
            <p>
              <span className="font-medium">Claude Desktop</span> — Settings → Connectors →
              Add custom connector, with the address{" "}
              <code className="font-mono text-xs">{mcpUrl}</code> and the header{" "}
              <code className="font-mono text-xs">Authorization: Bearer &lt;token&gt;</code>.
            </p>
            <Button size="sm" variant="ghost" onClick={() => setFresh(null)}>
              I have saved it
            </Button>
          </div>
        )}

        {mcpUrl && (
          <form className="flex flex-wrap items-end gap-3" onSubmit={issue}>
            <div className="space-y-1.5">
              <Label htmlFor="token-name">Name</Label>
              <Input
                id="token-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Claude on my laptop"
                maxLength={80}
                required
              />
            </div>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Creating…" : "Create token"}
            </Button>
          </form>
        )}

        {live.length > 0 && (
          <div className="divide-y rounded-md border">
            {live.map((token) => (
              <div
                key={token.id}
                data-testid="token-row"
                className="flex flex-wrap items-center gap-3 p-3 text-sm"
              >
                <span className="font-medium">{token.name}</span>
                <code className="font-mono text-xs text-muted-foreground">{token.prefix}…</code>
                <span className="text-xs text-muted-foreground">
                  last used {when(token.last_used_at)} · expires {when(token.expires_at)}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto"
                  onClick={async () => {
                    try {
                      await revokeToken(token.id);
                      reload();
                    } catch (err) {
                      setError(errorMessage(err));
                    }
                  }}
                >
                  Revoke
                </Button>
              </div>
            ))}
          </div>
        )}

        {dead.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {dead.length} revoked or expired token{dead.length === 1 ? "" : "s"}{" "}
            {dead.map((t) => (
              <Badge key={t.id} variant="outline" className="mr-1">
                {t.name}
              </Badge>
            ))}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
