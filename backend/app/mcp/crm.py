"""The CRM's MCP tools — spec 007 FR-CRM-MCP; contracts §3; plan §5.10.

Each tool is one in-process call to a `/crm` route as the token's owner, as
every NLDW tool is (`server.py`): the route's guard, the workspace rule of
D13 and the Viewer redaction all apply unchanged, and nothing is checked or
removed here (FR-CRM-MCP-07). Descriptions open with who may call, in
capitals (FR-CRM-MCP-11); every write says CONFIRM FIRST (FR-CRM-MCP-05).

Imported at the bottom of `server.py`, after `mcp`, `_api` and the
annotations exist, and imports only from it.
"""

from __future__ import annotations

from app.mcp.server import READ, Context, _api, mcp


@mcp.tool(annotations=READ)
async def crm_workspaces(ctx: Context) -> list:
    """ANY MEMBER OF THE WORKSPACE. The CRM workspaces you belong to, your role in
    each and which one you used last. Reads default to that one; a write from
    someone in more than one workspace must name it."""
    return await _api(ctx, "GET", "/crm/workspaces")
