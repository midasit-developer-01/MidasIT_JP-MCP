"""MIDAS NX Open API — MCP server.

Exposes a small set of generic tools so an LLM can drive a running MIDAS
CIVIL/GEN NX instance: catalog lookup (offline), DB CRUD (the `Assign`
convention), and one command tool per API group — doc/ope/view/post (the
`Argument` convention). The tool name mirrors the URL group, so every
documented endpoint is reachable without a raw escape hatch.

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

Auth (OAuth, streamable-http mode)
----------------------------------
  MIDAS_MCP_PUBLIC_URL=https://...  the externally reachable origin. Setting it
                                    turns the server into its own OAuth 2.1
                                    authorization server (see midas_mcp/auth/);
                                    unset = off. One variable, both switch and
                                    address.

MCP clients such as Claude's custom connectors cannot attach a custom header,
so with OAuth on they authorize once through a form that collects the MAPI key
and then send an opaque bearer token. Requests without a valid token get 401.

Precondition: the target MIDAS NX app must be running with a model file open.
"""

from __future__ import annotations

import os
from typing import Any, Callable, TypeVar

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from . import auth, catalog
from .client import MidasClient

INSTRUCTIONS = """\
MIDAS NX Open API — read and edit a live MIDAS CIVIL/GEN NX structural model
(nodes, elements, sections, materials, loads, analysis, design/rating, results)
over its REST API.

CORE RULE — never call an action tool from memory. The API has 572 endpoints and
you select one by NAME at call time; each has its own payload shape. Call
midas_describe first and shape your `assign`/`argument`/`body` on the returned
`example`. That example is the authoritative contract — do not infer the body.

Procedure for any task:
  1. midas_lookup("<keywords>")          find candidate endpoints (group + uri + desc).
  2. read each hit's `desc` and pick     the top hit is NOT always the right one.
                                         If no desc matches what you were asked for,
                                         call midas_lookup again with limit=30 —
                                         about 1 request in 12 has its answer below
                                         rank 10. Never settle for the closest hit.
  3. midas_describe("<name or full uri>") get schema + a working example + notes.
  4. call the matching action tool, copying the example's shape.

Disambiguation — the same NAME exists in several places: across groups
(db/MEMB vs ope/MEMB) and, for design/rating, across code standards (MATD under
design/RC, design/PSC, ...). When a name is not unique, pass the full `uri` from
midas_lookup, or `group=`, so you hit the intended endpoint.

Which tool:
  • db-shaped CRUD (Assign convention): midas_db_read / _create / _update /
    _delete. Pass a bare name under /db ("NODE"), or a full lookup uri to reach
    the same-shaped tables in any group, routed by uri: "temp/db/MPHG",
    "design/PSC/AASHTO-LRFD24/MEMB", "temp/DESIGN/STEEL/.../STBD".
  • command groups (one tool each): midas_doc, midas_ope, midas_view, midas_post.
  • extended groups take a `path` (the lookup uri after "<group>/"): midas_design,
    midas_rating, midas_temp, midas_requestinfo, midas_config. design & rating nest
    a code category + standard in that path (e.g. "RC/KDS-41-20-2022/DCRM-BEAM").

Good to know:
  • midas_lookup and midas_describe are OFFLINE (bundled catalog) — no running app
    or key needed. Use them freely to plan before touching the model.
  • DB writes are schema-checked before sending; an invalid body comes back as a
    field-level error to fix, not silently sent.
  • Reads are safe; midas_db_delete and midas_doc (NEW/OPEN/SAVE/EXPORT) change or
    overwrite data — treat as destructive.
  • Action tools need MIDAS NX running with a model open and a valid MAPI-Key.
"""

# Auth lives in midas_mcp/auth/ and is entirely opt-in (see that package).
# It has to be resolved before FastMCP is constructed: the SDK wires the OAuth
# routes and the bearer middleware from settings passed to __init__.
_auth = auth.from_env()
mcp = FastMCP("midas-nx", instructions=INSTRUCTIONS, **(_auth.fastmcp_kwargs if _auth else {}))
if _auth:
    _auth.install_routes(mcp)

# stdio single-user fallback client (built lazily from env). Never used in http
# mode, where every request must carry its own key.
_env_client: MidasClient | None = None


def _extract_key(ctx: Context | None) -> tuple[str | None, str | None]:
    """Pull (mapi_key, base_url) from the incoming HTTP request headers.

    Returns (None, None) under stdio (no HTTP request) or when no key header is
    present. Header lookup is case-insensitive (Starlette Headers).
    """

    # ------------------------------------------------------
    # stdio 모드(로컬 데스크톱, 프로세스 1개)
    if ctx is None:
        return None, None
    try:
        req = ctx.request_context.request
    except (ValueError, AttributeError):
        return None, None
    headers = getattr(req, "headers", None)
    if not headers:  # stdio: request has no .headers
        return None, None
    # ------------------------------------------------------
    # DePloy 모드 
    key = headers.get("x-midas-mapi-key")
    if not key:
        # Renamed from `auth` to avoid shadowing the imported auth package.
        authorization = headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
            # With OAuth on, the bearer is our opaque token -> resolve it to the
            # stored MAPI key. Otherwise (header-only clients) treat it AS the key.
            if token and _auth is not None:
                key = _auth.mapi_key_for_token(token)
            key = key or token or None
    return key, headers.get("x-midas-base-url")


def _bearer_token(ctx: Context | None) -> str | None:
    """The raw opaque bearer token on this request, if any (OAuth mode only).

    Unlike ``_extract_key`` this does not resolve the token to a key - the
    re-key tool needs the token itself to prove which session is asking.
    """
    if ctx is None:
        return None
    try:
        req = ctx.request_context.request
    except (ValueError, AttributeError):
        return None
    headers = getattr(req, "headers", None)
    if not headers:
        return None
    authorization = headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


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


_T = TypeVar("_T")


async def _offload(thunk: Callable[[], _T]) -> _T:
    """Run a blocking MidasClient call in a worker thread, off the event loop.

    FastMCP invokes a synchronous tool directly on the single asyncio loop, so a
    blocking `requests` call (up to the 60s client timeout) would stall EVERY
    other user's request until it returns. Awaiting it in a thread instead keeps
    the loop free, so concurrent callers are served in parallel (anyio's default
    ~40-thread pool). Safe because each request builds its own per-key
    MidasClient — no shared mutable state crosses threads.

    Scaling note: the ~40-thread pool caps how many blocking calls run at once;
    beyond that, extra calls queue (still far better than full serialization). If
    concurrent in-flight requests routinely exceed ~40, raise the pool once at
    startup, e.g.:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 100
    """
    return await anyio.to_thread.run_sync(thunk)


# --- Re-keying (OAuth mode only): swap your MAPI key without reconnecting -
# Registered only when the server is its own OAuth authorization server. In
# stdio/.mcpb the key comes from env, so there is nothing to re-key here.

async def midas_rekey_link(ctx: Context) -> str:
    """Get a one-time link to replace your MAPI key without reconnecting.

    Use this after renewing your key, or to switch between MIDAS CIVIL and GEN —
    the program follows whichever key you paste, so no other change is needed.
    Open the returned URL in a browser and paste the new key there; your
    connection keeps working and the connector does not need to be removed and
    re-added. The key is entered on the server's own page, so it never passes
    through this chat.
    """
    token = _bearer_token(ctx)
    if _auth is None or token is None:
        return ("Re-keying applies only to the OAuth (remote connector) deployment. "
                "In local .mcpb/stdio mode, update MIDAS_MAPI_KEY in the extension "
                "settings instead.")
    try:
        # start_rekey hits the SQLite auth store (blocking) — offload it so the
        # loop stays free, same as the model-API tools below.
        url = await _offload(lambda: _auth.start_rekey(token))
    except Exception as exc:  # e.g. AuthorizeError when the token is not live
        return f"Could not start re-keying: {exc}"
    return ("Open this link in a browser and paste your new MAPI key, then return "
            f"here — the connection switches over with no reconnect:\n{url}\n\n"
            "The link is valid for a few minutes.")


# Only expose the tool where it means something (OAuth on); otherwise it would
# be dead weight in the .mcpb tool list.
if _auth is not None:
    midas_rekey_link = mcp.tool(
        annotations=ToolAnnotations(
            title="Get a link to replace your MAPI key",
            readOnlyHint=False,
            destructiveHint=False,  # replaces your own credential; no model data touched
            idempotentHint=False,
            openWorldHint=False,  # talks to this server's own auth store, not the model
        )
    )(midas_rekey_link)


# --- Catalog / discovery (offline: no key needed) ------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="Look up MIDAS endpoints",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=False,  # searches the bundled catalog, not the live app
    )
)
def midas_lookup(query: str, limit: int = 10, group: str | None = None) -> list[dict[str, Any]]:
    """Search the MIDAS API endpoint catalog by keyword (name / uri / description).

    Use this FIRST to find the right endpoint before calling the action tools.
    Each hit carries its `group`, full `uri` and a one-line `desc` — the API
    spans 10 groups (db, design, ope, rating, doc, post, temp, view,
    requestinfo, config).

    READ THE `desc` OF EACH HIT AND PICK FROM IT. Endpoint names are 4-letter
    abbreviations (SECT, CONS, SPLC), so the ranking is tuned to keep the right
    endpoint somewhere in the list rather than always first — the top hit is not
    necessarily the right one.

    IF NO `desc` MATCHES WHAT YOU WERE ASKED FOR, CALL THIS AGAIN WITH
    `limit=30` BEFORE CONCLUDING THE ENDPOINT DOES NOT EXIST. Roughly one
    request in twelve has its answer below rank 10, and `limit=30` finds most
    of them. Do not settle for the closest-looking hit from the first ten.
    A second search costs far less than a wrong endpoint.

    Pass `group` to restrict results to one group; with a group set, a broad or
    empty `query` simply lists that group's endpoints. This matters because a
    name can repeat across groups and, for design/rating, across code standards
    (e.g. MATD lives under db and several design/rating code paths). Then call
    `midas_describe` with a hit's full `uri` (or `name`+`group`) for the schema.
    """
    return catalog.search(query, limit=limit, group=group)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Describe a MIDAS endpoint",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=False,  # reads the bundled catalog, not the live app
    )
)
def midas_describe(name: str, group: str | None = None) -> dict[str, Any]:
    """Get the full schema + example payload for one endpoint (by catalog name).

    e.g. name="NODE", "SECT", "ANAL", "TABLE", "IEHP". Use the returned `example`
    as the shape for `assign` / `argument` when calling the action tools.

    Some names repeat — across groups (db/MEMB vs ope/MEMB) and, for design/
    rating, across code standards (MATD under design/PSC, design/RC, ...). To
    pin exactly one, pass the full `uri` from `midas_lookup` as `name`
    (e.g. name="design/RC/KDS-41-20-2022/MATD"), or pass `group` to narrow.
    """
    return catalog.describe(name, group=group)


# --- DB CRUD (per-request key) -------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="Read model DB data",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,  # queries the running MIDAS instance
    )
)
async def midas_db_read(item: str, ctx: Context, item_id: int | None = None) -> Any:
    """Read model DB data. GET /{group}/{item} (all) or /.../{item_id} (one).

    `item` is a bare name under /db ("NODE", "SECT", "IEHP") or a full lookup uri
    to any db-shaped (Assign) endpoint ("temp/db/MPHG", "design/PSC/AASHTO-LRFD24/MATD").
    Response is unwrapped to {id: value, ...} (or the single record).
    """
    c = _client(ctx)
    return await _offload(
        lambda: c.db_read_item(item, item_id) if item_id is not None else c.db_read(item)
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create DB records",
        readOnlyHint=False,
        destructiveHint=False,  # adds/sets records; overwriting an existing id is possible but not the intent
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def midas_db_create(item: str, assign: dict, ctx: Context) -> Any:
    """Create DB records. POST /{group}/{item} with body {"Assign": assign}.

    `item` is a bare name under /db or a full uri to any db-shaped endpoint
    ("temp/db/MPHG", "design/PSC/AASHTO-LRFD24/MEMB"). `assign` maps a
    numeric-string key to a record, e.g. {"1": {"X": 0, "Y": 0, "Z": 0}};
    see `midas_describe(item)` for the shape.
    """
    c = _client(ctx)
    return await _offload(lambda: c.db_create(item, assign))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update DB records",
        readOnlyHint=False,
        destructiveHint=False,  # in-place update of existing records
        idempotentHint=True,  # PUT: same Assign -> same resulting state
        openWorldHint=True,
    )
)
async def midas_db_update(item: str, assign: dict, ctx: Context) -> Any:
    """Update DB records. PUT /{group}/{item} ({"Assign": assign}). `item` is a
    bare name under /db or a full uri ("temp/db/MPHG", "design/PSC/AASHTO-LRFD24/MEMB")."""
    c = _client(ctx)
    return await _offload(lambda: c.db_update(item, assign))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete a DB record",
        readOnlyHint=False,
        destructiveHint=True,  # removes model data
        idempotentHint=True,  # deleting the same id again is a no-op
        openWorldHint=True,
    )
)
async def midas_db_delete(item: str, item_id: int, ctx: Context) -> Any:
    """Delete a single DB record. DELETE /{group}/{item}/{item_id}. `item` is a
    bare name under /db or a full uri ("temp/db/MPHG", "design/PSC/AASHTO-LRFD24/DIDP")."""
    c = _client(ctx)
    return await _offload(lambda: c.db_delete(item, item_id))


# --- Command groups: doc / ope / view / post (per-request key) ------------
# All four share the Argument convention: {method} /{group}/{name} with a
# {"Argument": argument} body (empty for GET). One tool per API group, so the
# tool name IS the URL segment — no guessing which endpoint lives where.

@mcp.tool(
    annotations=ToolAnnotations(
        title="Document/file control",
        readOnlyHint=False,
        destructiveHint=True,  # OPEN/NEW/SAVE/EXPORT can overwrite the model or files
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def midas_doc(name: str, ctx: Context, argument: Any | None = None) -> Any:
    """File/document control. POST /doc/{name} with body {"Argument": argument}.

    e.g. name="ANAL" (run analysis), "SAVE", "OPEN", "NEW", "EXPORT". Omit
    `argument` for endpoints that take an empty body.
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("doc", name, argument))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Modeling operation",
        readOnlyHint=False,
        destructiveHint=False,  # transforms/generates model geometry; not a delete
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def midas_ope(name: str, ctx: Context, argument: Any | None = None,
                    method: str = "POST", body: Any | None = None) -> Any:
    """Modeling operation. POST /ope/{name} with body {"Argument": argument}.

    e.g. name="AUTOMESH", "DIVIDEELEM", "USLC". A few are reads (name="SECTPROP"
    / "PROJECTSTATUS" with method="GET"). See `midas_describe(name)` for the shape.

    A few story endpoints wrap the payload in a named key instead of "Argument"
    (STOR, STORYPROP, STORY_PARAM) — for those, pass the exact body via `body`,
    e.g. body={"STOR": {...}} (copy the shape from `midas_describe(name)`).
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("ope", name, argument, method, body))


@mcp.tool(
    annotations=ToolAnnotations(
        title="View/display control",
        readOnlyHint=False,
        destructiveHint=False,  # changes the view / captures images; no model data loss
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def midas_view(name: str, ctx: Context, argument: Any | None = None,
                     method: str = "POST") -> Any:
    """View/display control. POST /view/{name} with body {"Argument": argument}.

    e.g. name="ACTIVE", "ANGLE", "CAPTURE", "DISPLAY". Reads use
    method="GET" (name="SELECT"). See `midas_describe(name)` for the shape.
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("view", name, argument, method))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Post-processing / results",
        readOnlyHint=True,  # extracts results/checks; does not modify the model
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def midas_post(name: str, ctx: Context, argument: Any | None = None) -> Any:
    """Post-processing / results extraction. POST /post/{name} with {"Argument": argument}.

    e.g. name="TABLE" (result table — see `midas_describe("TABLE")` for the
    table type/range shape), "STEELCODECHECK", "PM".
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("post", name, argument))


# --- Extended groups: design / rating / temp / requestinfo / config -------
# Same URL model as doc/ope/... but these live under deeper paths (design and
# rating carry a code category + standard, e.g.
# design/RC/KDS-41-20-2022/DCRM-BEAM). Pass `path` = the `midas_lookup` uri with
# the leading "<group>/" removed (it is stripped for you if you leave it in).
# Body convention varies PER ENDPOINT — copy the exact shape from
# `midas_describe`: db-style endpoints take {"Assign": {...}} (pass via `body`),
# the rest take {"Argument": argument}. Reads use method="GET".

def _sub(group: str, path: str) -> str:
    """Return `path` without a leading '<group>/' so callers may pass either form."""
    p = str(path).lstrip("/")
    pre = group.lower() + "/"
    return p[len(pre):] if p.lower().startswith(pre) else p


@mcp.tool(
    annotations=ToolAnnotations(
        title="Structural design (code check)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def midas_design(path: str, ctx: Context, argument: Any | None = None,
                       method: str = "POST", body: Any | None = None) -> Any:
    """Structural design / code-check endpoints. {method} /design/{path}.

    `path` is the `midas_lookup` uri after "design/", e.g.
    "RC/KDS-41-20-2022/DCRM-BEAM" — it carries the code category
    (RC/STEEL/SRC/PSC/...) and the code standard, so the SAME name exists under
    several standards; use the full path from `midas_lookup`. Copy the body from
    `midas_describe`: db-style items (design conditions/tables data) take
    body={"Assign": {...}}; analysis/report items take argument={...} (sent as
    {"Argument": ...}). Reads use method="GET".
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("design", _sub("design", path), argument, method, body))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Load rating",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def midas_rating(path: str, ctx: Context, argument: Any | None = None,
                       method: str = "POST", body: Any | None = None) -> Any:
    """Load-rating endpoints. {method} /rating/{path}.

    `path` is the `midas_lookup` uri after "rating/", e.g.
    "PSC/AASHTO-LRFR19/DATR" (carries the rating category + standard). db-style
    items take body={"Assign": {...}}; analysis/report items take argument={...}.
    Reads use method="GET". Copy the shape from `midas_describe`.
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("rating", _sub("rating", path), argument, method, body))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Expansion / external-link DB",
        readOnlyHint=False,
        destructiveHint=True,  # some temp endpoints are full-CRUD DB (Assign)
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def midas_temp(path: str, ctx: Context, argument: Any | None = None,
                     method: str = "POST", body: Any | None = None) -> Any:
    """Temp-group endpoints: DB for expansion & external-program connection, and
    temporary DB. {method} /temp/{path}.

    `path` is the `midas_lookup` uri after "temp/", e.g. "SRTN", "SVSL",
    "REBAR_DB". Body convention varies: db-style items (e.g. SRTN, SVSL, MULTI-
    TEST) take body={"Assign": {...}}; the OPEN_TABLE / REBAR_* helpers take
    argument={...} (or none). Reads use method="GET". Copy the shape from
    `midas_describe`.
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("temp", _sub("temp", path), argument, method, body))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Request-info (metadata)",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def midas_requestinfo(path: str, ctx: Context, argument: Any | None = None,
                            method: str = "GET", body: Any | None = None) -> Any:
    """Request-info / metadata endpoints (describe what a request expects).
    {method} /requestinfo/{path}.

    `path` is the `midas_lookup` uri after "requestinfo/", e.g. "POST/TABLE",
    "POST/TABLE/TYPELIST", "POST/TABLE_REQUEST". These take {"Argument": argument}
    (often empty); most are reads (method="GET").
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("requestinfo", _sub("requestinfo", path), argument, method, body))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Project config / version",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def midas_config(path: str, ctx: Context, argument: Any | None = None,
                       method: str = "GET", body: Any | None = None) -> Any:
    """Config endpoints (project info, program version). {method} /config/{path}.

    `path` is the `midas_lookup` uri after "config/", e.g. "PROJECT", "VER".
    Both are reads (method="GET").
    """
    c = _client(ctx)
    return await _offload(lambda: c.command("config", _sub("config", path), argument, method, body))


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
