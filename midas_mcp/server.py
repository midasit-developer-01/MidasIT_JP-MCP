"""MIDAS NX Open API — MCP server.

Exposes a small set of generic tools (DB CRUD + DOC/POST + catalog lookup)
so an LLM can drive a running MIDAS CIVIL/GEN NX instance.

Transports
----------
stdio (default) — one process per user, single key from env. Local/desktop use::

    midas-mcp                       # or: python -m midas_mcp.server

streamable-http — one shared server, MANY clients, key supplied PER REQUEST via
an HTTP header. For remote (AWS) multi-user deployments::

    MIDAS_MCP_TRANSPORT=streamable-http MIDAS_MCP_PORT=8080 midas-mcp

Auth (env)
----------
  MIDAS_MAPI_KEY  stdio mode — key from [API Settings] in MIDAS NX
  MIDAS_BASE_URL  optional override; else derived from the key

Auth (per-request headers, streamable-http mode)
------------------------------------------------
  X-MIDAS-MAPI-Key: <key>          each user sends THEIR OWN key
  Authorization: Bearer <key>      accepted as an alternative
  X-MIDAS-Base-URL: <url>          optional per-request base-url override

In http mode the server never falls back to a server-wide key: a request with
no key header gets a clean {"error": ...} instead of acting as someone else.

Precondition: the target MIDAS NX app must be running with a model file open.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from . import catalog
from .client import MidasClient

mcp = FastMCP("midas-nx")

# stdio single-user fallback client (built lazily from env). Never used in http
# mode, where every request must carry its own key.
_env_client: MidasClient | None = None


def _extract_key(ctx: Context | None) -> tuple[str | None, str | None]:
    """Pull (mapi_key, base_url) from the incoming HTTP request headers.

    Returns (None, None) under stdio (no HTTP request) or when no key header is
    present. Header lookup is case-insensitive (Starlette Headers).
    """
    if ctx is None:
        return None, None
    try:
        req = ctx.request_context.request
    except (ValueError, AttributeError):
        return None, None
    headers = getattr(req, "headers", None)
    if not headers:  # stdio: request has no .headers
        return None, None
    key = headers.get("x-midas-mapi-key")
    if not key:
        auth = headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip() or None
    return key, headers.get("x-midas-base-url")


def _client(ctx: Context | None = None) -> MidasClient:
    """Resolve the client for this call.

    - http mode: build a fresh, per-request client from the caller's key header.
      No header -> a keyless client (returns a clean "key not set" error).
    - stdio mode: reuse one env-configured singleton.
    """
    key, base = _extract_key(ctx)
    if key:
        return MidasClient(mapi_key=key, base_url=base)

    # If we're in an HTTP request but no key was supplied, do NOT fall back to a
    # server-wide key — return a keyless client so the caller gets an auth error
    # instead of accidentally using someone else's session.
    if ctx is not None:
        try:
            if getattr(ctx.request_context.request, "headers", None) is not None:
                return MidasClient(mapi_key="")
        except (ValueError, AttributeError):
            pass

    global _env_client
    if _env_client is None:
        _env_client = MidasClient()
    return _env_client


# --- Catalog / discovery (offline: no key needed) ------------------------

@mcp.tool()
def midas_lookup(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Search the MIDAS API endpoint catalog by keyword (name / uri / description).

    Use this FIRST to find the right endpoint and its item name before calling
    the DB/DOC/POST tools. Covers all documented endpoints plus the undocumented
    `db/IEHP` (inelastic hinge property definition). Returns compact hits; call
    `midas_describe` with a hit's `name` to get the full schema + example.
    """
    return catalog.search(query, limit=limit)


@mcp.tool()
def midas_describe(name: str) -> dict[str, Any]:
    """Get the full schema + example payload for one endpoint (by catalog name).

    e.g. name="NODE", "SECT", "ANAL", "TABLE", "IEHP". Use the returned `example`
    as the shape for `assign` / `argument` when calling the action tools.
    """
    return catalog.describe(name)


# --- DB CRUD (per-request key) -------------------------------------------

@mcp.tool()
def midas_db_read(item: str, ctx: Context, item_id: int | None = None) -> Any:
    """Read model DB data. GET /db/{item} (all) or /db/{item}/{item_id} (one).

    `item` is the endpoint name, e.g. "NODE", "ELEM", "SECT", "MATL", "IEHP".
    Response is unwrapped to {id: value, ...} (or the single record).
    """
    c = _client(ctx)
    return c.db_read_item(item, item_id) if item_id is not None else c.db_read(item)


@mcp.tool()
def midas_db_create(item: str, assign: dict, ctx: Context) -> Any:
    """Create DB records. POST /db/{item} with body {"Assign": assign}.

    `assign` maps a numeric-string key to a record, e.g.
    {"1": {"X": 0, "Y": 0, "Z": 0}}. Check `midas_describe(item)` for the shape.
    """
    return _client(ctx).db_create(item, assign)


@mcp.tool()
def midas_db_update(item: str, assign: dict, ctx: Context) -> Any:
    """Update DB records. PUT /db/{item} with body {"Assign": assign}."""
    return _client(ctx).db_update(item, assign)


@mcp.tool()
def midas_db_delete(item: str, item_id: int, ctx: Context) -> Any:
    """Delete a single DB record. DELETE /db/{item}/{item_id}."""
    return _client(ctx).db_delete(item, item_id)


# --- DOC / POST (per-request key) ----------------------------------------

@mcp.tool()
def midas_doc(name: str, ctx: Context, argument: Any | None = None) -> Any:
    """Document/file control. POST /doc/{name} with body {"Argument": argument}.

    e.g. name="ANAL" (run analysis), "SAVE", "OPEN", "EXPORT". Omit `argument`
    for endpoints that take an empty body.
    """
    return _client(ctx).doc(name, argument)


@mcp.tool()
def midas_post_table(argument: dict, ctx: Context) -> Any:
    """Extract a pre/post-processing table. POST /post/TABLE with {"Argument": argument}.

    See `midas_describe("TABLE")` for the Argument shape (table type, range, ...).
    """
    return _client(ctx).post_table(argument)


# --- Escape hatch (per-request key) --------------------------------------

@mcp.tool()
def midas_request(method: str, endpoint: str, ctx: Context, body: Any | None = None) -> Any:
    """Raw request for any endpoint not covered above (OPE/VIEW/etc).

    `method` in GET/POST/PUT/DELETE; `endpoint` is the path after the base URL,
    e.g. "/view/SELECT", "/ope/SECTPROP". `body` is the full JSON body as-is.
    """
    return _client(ctx).request(method, endpoint, body)


def main() -> None:
    """Entry point. Transport chosen by MIDAS_MCP_TRANSPORT (default: stdio)."""
    transport = os.environ.get("MIDAS_MCP_TRANSPORT", "stdio").lower()
    if transport in ("streamable-http", "sse"):
        mcp.settings.host = os.environ.get("MIDAS_MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MIDAS_MCP_PORT", "8080"))
        # stateless: no server-side session state between requests — each HTTP
        # call is self-contained (carries its own key), which suits horizontal
        # scaling behind a load balancer.
        mcp.settings.stateless_http = True
        mcp.run(transport=transport)  # type: ignore[arg-type]
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
