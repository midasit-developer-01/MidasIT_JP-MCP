# MIDAS NX Open API — MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an LLM (Claude, etc.)
drive a running **MIDAS CIVIL/GEN NX** instance through its Open API.

Instead of one tool per endpoint (**539 endpoints across 10 groups**), it exposes
**15 generic tools** plus a **catalog lookup** so the model discovers the exact
schema/example at call time — including the undocumented `db/IEHP` (inelastic
hinge property).

## Tools

| Tool | Maps to | Purpose |
| --- | --- | --- |
| `midas_lookup(query)` | — | Search the endpoint catalog by keyword |
| `midas_describe(name)` | — | Full schema + example for one endpoint (e.g. `NODE`, `IEHP`, `TABLE`) |
| `midas_db_read(item, item_id?)` | `GET /db/{item}` | Read model data (unwrapped) |
| `midas_db_create(item, assign)` | `POST /db/{item}` | Create records (`{"Assign": ...}`) |
| `midas_db_update(item, assign)` | `PUT /db/{item}` | Update records |
| `midas_db_delete(item, item_id)` | `DELETE /db/{item}/{id}` | Delete one record |
| `midas_doc(name, argument?)` | `POST /doc/{name}` | File/document control (NEW, OPEN, SAVE, ANAL, EXPORT…) |
| `midas_ope(name, argument?, method?)` | `POST /ope/{name}` | Modeling operations (AUTOMESH, DIVIDEELEM, USLC…) |
| `midas_view(name, argument?, method?)` | `POST /view/{name}` | View/display control (ACTIVE, ANGLE, CAPTURE…) |
| `midas_post(name, argument?)` | `POST /post/{name}` | Post-processing/results (TABLE, STEELCODECHECK, PM…) |
| `midas_design(path, …)` | `* /design/{path}` | Structural design / code check (RC/STEEL/SRC/PSC × code standard) |
| `midas_rating(path, …)` | `* /rating/{path}` | Load rating (category × rating standard) |
| `midas_temp(path, …)` | `* /temp/{path}` | Expansion / external-link DB + temporary DB |
| `midas_requestinfo(path, …)` | `* /requestinfo/{path}` | Request metadata (what a request expects) |
| `midas_config(path, …)` | `* /config/{path}` | Project info / program version |

The API has two body conventions: `db/` uses `Assign` (→ `midas_db_*`); the
command groups `doc`/`ope`/`view`/`post` all use `Argument` and each get a
dedicated tool named after their URL segment — so the tool name *is* the group.
Every documented endpoint is reachable through these tools, so there is no raw
escape hatch. The few `ope` endpoints that wrap the payload in a named key
instead of `Argument` (STOR, STORYPROP, STORY_PARAM) take a verbatim `body=` on
`midas_ope` (copy the shape from `midas_describe(name)`).

The five **extended-group** tools (`design`/`rating`/`temp`/`requestinfo`/`config`)
take a `path` — the `midas_lookup` uri after the leading `<group>/`. `design` and
`rating` nest a **code category + standard** in that path (e.g.
`design/RC/KDS-41-20-2022/DCRM-BEAM`), so the same name repeats across standards;
always pass the full path from `midas_lookup`. Their body convention varies per
endpoint — db-style items take `body={"Assign": {…}}`, the rest take
`argument={…}`; reads use `method="GET"`. Copy the exact shape from
`midas_describe`.

Every tool carries MCP [`ToolAnnotations`](https://modelcontextprotocol.io/) hints
(`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`) so a host can,
e.g., auto-approve reads (`midas_lookup`, `midas_db_read`, `midas_post`) but
prompt before destructive writes (`midas_db_delete`, `midas_doc`).

The catalog data lives one-file-per-endpoint under `data/schemas/<group>/<NAME>.json`
(e.g. `data/schemas/db/NODE.json`); each file carries the endpoint's
`uri`/`methods`/`schema`/`example` plus a derived `tables` type summary. The
`design`/`rating` files nest under a code category + standard
(`data/schemas/design/RC/KDS-41-20-2022/DCRM.json`). `catalog.py` scans the whole
`data/schemas` tree **recursively** at startup, so deep code-standard paths are
included. The reference doc (`data/midas-api-reference.md`) is bundled from the
React template repo. (This replaces the old monolithic
`data/midas-api-examples.json`.)

## Client-side validation

Before every `POST`/`PUT` to `/db/{item}`, the client validates the `Assign`
body against that endpoint's bundled JSON Schema (`midas_mcp/hooks/`, using
`jsonschema`). Invalid bodies are **not sent** — the caller gets a
human-readable error naming the offending field, so the model can fix and retry
without a round-trip to the app. Validation **fails open**: if `jsonschema` is
missing or no schema is bundled for the item, the request goes through
unchanged. Disable it with `MidasClient(validate=False)` or `MIDAS_VALIDATE=0`.

## Repository layout

Two independent distribution tracks share one core:

```
midas_mcp/          # server source (stdio + streamable-http)  ── shared core
  ├─ server.py      #   the 15 FastMCP tools
  ├─ client.py      #   thin REST client for the MIDAS Open API
  ├─ catalog.py     #   offline endpoint lookup over data/schemas
  └─ hooks/         #   pre-request JSON-Schema validation of DB bodies
data/
  ├─ schemas/       # endpoint catalog, one file per endpoint (bundled into every build)
  └─ midas-api-reference.md
mcpb/               # track 1: .mcpb bundle for Claude Desktop (PyInstaller, win32)
Dockerfile          # track 2: container image for remote (streamable-http) deploy
deploy/             # track 2: CloudFormation templates + AWS runbook
```

## Preconditions

MIDAS NX running + a model file open; a valid MAPI-Key from [API Settings] in
the app. Without them the API returns auth/connection errors.

End users don't run this repo directly — they install the `.mcpb` bundle or
connect to the remote server (see **Build** below).

## Local development (optional)

Only needed when changing the server source; builds don't require this
(`build.ps1` and `docker build` install deps themselves).

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e .
cp .env.example .env                                 # then fill MIDAS_MAPI_KEY
midas-mcp                                            # run stdio server directly
```

To test the dev version against Claude Code without building:

```bash
claude mcp add midas-nx -e MIDAS_MAPI_KEY=<YOUR_KEY> -- midas-mcp
```

## Build

### Track 1 — `.mcpb` bundle (Claude Desktop one-click install)

Freezes the server + interpreter into a self-contained `midas-mcp.exe`
(PyInstaller), so the target machine needs **no Python**. Requires Python 3.10+
and Node/npx on the build machine. Details: [mcpb/README.md](mcpb/README.md).

```powershell
pwsh mcpb/build.ps1
```

Output: `dist/midas-nx.mcpb` → drag onto Claude Desktop
(Settings → Extensions), enter the MAPI key when prompted.

Smoke-test the frozen exe before shipping:

```powershell
$env:MIDAS_MAPI_KEY = "<your key>"; mcpb/build/pack/server/midas-mcp.exe   # Ctrl+C to stop
```

The exe is OS/arch-specific — this script produces a **win32** bundle.

### Track 2 — Docker image (remote streamable-http server)

The image runs the server in `streamable-http` mode on port 8080; no key is
baked in (each request carries its own `X-MIDAS-MAPI-Key` header).

```powershell
docker build -t midas-mcp .
```

```powershell
docker run --rm -p 8080:8080 midas-mcp
```

### Track 2 — deploy to AWS (EC2 via CloudFormation)

Nothing is built or uploaded from your machine — no local AWS CLI, no local
Docker. Upload **only** `deploy/infra-ec2.yaml` to CloudShell and deploy it:

```bash
aws cloudformation deploy \
  --region ap-northeast-2 \
  --template-file infra-ec2.yaml \
  --stack-name midas-mcp \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      GitHubBranch=<branch> \
      ZoneDomain=<your-domain.com> \
      ServiceHostname=mcp.<your-domain.com> \
      AcmeEmail=<you@example.com>
```

That one command creates everything and kicks off the first build:

```
git push --> CodeBuild (native arm64) --> ECR --> EventBridge
                                                       |
                                                  SSM RunCommand
                                                       v
                                              EC2 pulls & restarts
```

CodeBuild clones this repo from GitHub and builds the `Dockerfile`, so
`Dockerfile`, `.dockerignore` and `deploy/` **must stay committed**. To ship a
change: push, then rerun the build (`RebuildCommand` stack output) — or supply
`GitHubToken` once to get a webhook that rebuilds on every push. Any push to the
watched ECR tag redeploys, whoever made it.

Step-by-step, parameter reference and troubleshooting:
[deploy/RUNBOOK.md](deploy/RUNBOOK.md).

| Template | Use |
| --- | --- |
| `deploy/infra-ec2.yaml` | Persistent stack: CodeBuild→ECR→EC2, HTTPS via Caddy, Route 53 domain, weekday auto stop/start |
| `deploy/infra.yaml` | Older throwaway test stack (HTTP :8080, IP-restricted, builds from an S3 source zip) |

## Example flow (what the model does)

1. `midas_lookup("inelastic hinge")` → finds `IEHP`, `IEHG`, `IEHC`, `FIMP`
2. `midas_describe("IEHP")` → gets the schema/example + the read rule
   (only `COMPONENT_DIR[i]==true` components hold valid values)
3. `midas_db_read("IEHP")` → live data from the open model
4. `midas_doc("ANAL")` → run analysis
5. `midas_post("TABLE", {...})` → pull result tables

## Notes

- `db/IEHP` is **undocumented** but works; inactive hinge components serialize
  uninitialized memory (garbage) — always filter by `COMPONENT_DIR`. See
  `data/midas-api-reference.md` §10-1.
