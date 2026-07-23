# MIDAS NX Open API — MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an LLM (Claude, etc.)
drive a running **MIDAS CIVIL/GEN NX** instance through its Open API.

Instead of one tool per endpoint (~256), it exposes a small set of **generic
tools** plus a **catalog lookup** so the model discovers the exact schema/example
at call time — including the undocumented `db/IEHP` (inelastic hinge property).

## Tools

| Tool | Maps to | Purpose |
| --- | --- | --- |
| `midas_lookup(query)` | — | Search the endpoint catalog by keyword |
| `midas_describe(name)` | — | Full schema + example for one endpoint (e.g. `NODE`, `IEHP`, `TABLE`) |
| `midas_db_read(item, item_id?)` | `GET /db/{item}` | Read model data (unwrapped) |
| `midas_db_create(item, assign)` | `POST /db/{item}` | Create records (`{"Assign": ...}`) |
| `midas_db_update(item, assign)` | `PUT /db/{item}` | Update records |
| `midas_db_delete(item, item_id)` | `DELETE /db/{item}/{id}` | Delete one record |
| `midas_doc(name, argument?)` | `POST /doc/{name}` | File/doc control (ANAL, SAVE, EXPORT…) |
| `midas_post_table(argument)` | `POST /post/TABLE` | Extract pre/post tables |
| `midas_request(method, endpoint, body?)` | any | Escape hatch (OPE/VIEW/…) |

The catalog data (`data/midas-api-examples.json`, `data/midas-api-reference.md`)
is bundled from the React template repo.

## Repository layout

Two independent distribution tracks share one core:

```
midas_mcp/          # server source (stdio + streamable-http)  ── shared core
data/               # endpoint catalog (bundled into every build)
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

The EC2 instance pulls a **source zip** from S3 and builds the image on boot.
Regenerate the zip after any source change:

```powershell
Compress-Archive -Force -DestinationPath deploy/midas-mcp-src.zip -Path Dockerfile,.dockerignore,pyproject.toml,README.md,requirements.txt,midas_mcp,data
```

Then follow [deploy/RUNBOOK.md](deploy/RUNBOOK.md) step by step (CloudShell:
upload zip → S3 → `aws cloudformation deploy` → verify → **tear down**).
Two templates:

| Template | Use |
| --- | --- |
| `deploy/infra.yaml` | Throwaway test stack (HTTP :8080, IP-restricted) |
| `deploy/infra-ec2.yaml` | Persistent stack (HTTPS via Caddy, Route 53 domain, weekday auto stop/start) |

## Example flow (what the model does)

1. `midas_lookup("inelastic hinge")` → finds `IEHP`, `IEHG`, `IEHC`, `FIMP`
2. `midas_describe("IEHP")` → gets the schema/example + the read rule
   (only `COMPONENT_DIR[i]==true` components hold valid values)
3. `midas_db_read("IEHP")` → live data from the open model
4. `midas_doc("ANAL")` → run analysis
5. `midas_post_table({...})` → pull result tables

## Notes

- `db/IEHP` is **undocumented** but works; inactive hinge components serialize
  uninitialized memory (garbage) — always filter by `COMPONENT_DIR`. See
  `data/midas-api-reference.md` §10-1.
