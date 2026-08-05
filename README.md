# MIDAS NX Open API — MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an LLM (Claude, etc.)
drive a running **MIDAS CIVIL/GEN NX** instance through its Open API.

Instead of one tool per endpoint (**572 endpoints across 10 groups**), it exposes
**16 generic tools** plus a **catalog lookup** so the model discovers the exact
schema/example at call time — including the undocumented `db/IEHP` (inelastic
hinge property). It also answers the other kind of question — *"where do I click
to do this?"* — from the bundled manual, without touching the running app.

## Tools

| Tool | Maps to | Purpose |
| --- | --- | --- |
| `midas_lookup(query)` | — | Search the endpoint catalog by keyword; each hit carries a one-line `desc` |
| `midas_describe(name)` | — | Full schema + example for one endpoint (e.g. `NODE`, `IEHP`, `TABLE`) |
| `midas_guide(name)` | — | Menu path + dialog field guide for a feature (155 of 572 endpoints) |
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
e.g., auto-approve reads (`midas_lookup`, `midas_guide`, `midas_db_read`,
`midas_post`) but prompt before destructive writes (`midas_db_delete`, `midas_doc`).

The catalog data lives one-file-per-endpoint under `data/schemas/<group>/<NAME>.json`
(e.g. `data/schemas/db/NODE.json`); each file carries the endpoint's
`uri`/`methods`/`schema`/`example` plus a derived `tables` type summary. The
`design`/`rating` files nest under a code category + standard
(`data/schemas/design/RC/KDS-41-20-2022/DCRM.json`). `catalog.py` scans the whole
`data/schemas` tree **recursively** at startup, so deep code-standard paths are
included. The reference doc (`data/midas-api-reference.md`) is bundled from the
React template repo. (This replaces the old monolithic
`data/midas-api-examples.json`.)

## Endpoint search

`midas_lookup` ranks the catalog with BM25 over each endpoint's name, uri and
description (`midas_mcp/search_index.py`). Endpoint names are 4-letter
abbreviations, so a query's words rarely match a name at all — the description
carries the match, and BM25's IDF keeps a common word like `load` (158 of 572
endpoints) from outweighing a rare one like `spectrum` (3). Plurals are stemmed
so `support` finds `Supports`, and the naming phrase each description opens with
(`Constraint Supports (CONS).`) is weighted up, since that is where a query's
identifying words land.

Hits are ranked for **recall, not precision**: the goal is to keep the right
endpoint somewhere in the list, and each hit ships a one-line `desc` so the
model reads them and picks. The top hit is not assumed correct.

### Manual names in the index

The descriptions are DTO-derived, so they name things the way the code does
while a user asks in UI wording. `scripts/merge_manual.py` folds the public
manual's naming (`manual_title`, `feature_name`, `manual_url`) into the schema
files for the 228 endpoints it covers, and those names are indexed alongside the
description's own title. That is what makes "Graphic Files" reach `view/CAPTURE`
and "Activities" reach `view/ACTIVE` — no scorer tuning can, since the words
were simply absent. Only the naming fields are merged; the manual's prose is
left out because it triples a document's indexed length for no measured gain.

Re-run it whenever the manual is re-scraped — it is idempotent:

```
python scripts/merge_manual.py --source <API_Data dir> [--dry-run]
```

`scripts/extract_features.py` keeps the rest of each manual article — what the
feature *does*, where it sits in the UI, and how each dialog field works — as
one file per endpoint under `data/features/`. That is what `midas_guide` serves;
see [GUI operation guides](#gui-operation-guides) below.

Only its `menu_path` is indexed. `function` and `usage` are **not**: the eval
set's `function` queries are drawn from that same text, so indexing it would
leave nothing independent to measure against. Menu paths are not the source of
any query — 0 of the 92 `function` queries are fully covered by menu-path terms
— so they can be indexed while the gate stays honest.

### GUI operation guides

When the user asks *how* to do something rather than asking the server to do it,
`midas_guide("db/MATL")` returns the ribbon route, what the feature is for, and
the dialog's controls explained field by field — all offline, from the bundle.

| | |
| --- | --- |
| Coverage | **155 / 572 endpoints** — db 123, ope 11, doc 10, view 5, post 2 |
| Not covered | design, rating and temp; the public manual has no feature article for them |
| Also missing | 37 articles are restricted upstream (HTTP 401 even for the page), costing 44 endpoints — including `doc/ANAL` |
| Layout | `data/features/<uri>.json`, mirroring `data/schemas` — the uri IS the path, so there is no slug to invent |

A `midas_lookup` hit carrying `"guide": true` has one. A miss is a normal
outcome, not an error: it returns the manual link the schema already knows, plus
related guided features (a leaf name repeating under `db` is usually literally
the same dialog), and says plainly that no guide is bundled — the failure being
defended against is the model filling the silence with an invented menu path.

Menu labels are the **English** UI's. On a localized MIDAS NX the tool's
description tells the model to quote the English label and say so rather than
translating it into a button that does not exist.

Top up coverage whenever access to a restricted article appears:

```bash
python scripts/fetch_articles.py --source <API_Data dir> --verify   # parser sanity
python scripts/fetch_articles.py --source <API_Data dir>            # fetch what is reachable
python scripts/extract_features.py --source <API_Data dir>          # regenerate the tree
python -m midas_mcp.hooks.check_features                            # parity gate
```

### When the first ten are not enough

About **1 independent query in 12** has its answer below rank 10 — usually at
rank 13–26, occasionally deeper. `midas_lookup`'s description tells the model to
call again with `limit=30` when no `desc` matches, rather than settle for the
closest-looking hit; measured, that recovers 6 of the 8 current misses for
~1.2K extra tokens. `eval_search` prints how often a retry is needed and what it
recovers, so the instruction's payoff stays a measured number.

Reading a whole group instead was considered and rejected for this purpose: the
`db` index alone is ~8.1K tokens against ~1.8K for a `limit=30` retry, for a
smaller gain. Browsing ("what load types exist?") is still unanswered by search
and still worth building — `midas_guide` covers the adjacent question ("how do I
use this feature?"), not that one.

### Guarding it

`python -m midas_mcp.eval_search` replays `data/eval_queries.json` (372 queries
derived from the public manual) and fails if recall@10 regresses.

**Two of the three query sources are circular** — `api_title` and
`feature_title` are the very fields the merge copies into the index, so they
score ~100% by construction and only prove the wiring works. `function` (the
manual's prose statement of what a feature does) is *not* indexed, so it is the
honest set, and **the floor is applied to it alone**:

| source | n | recall@10 | top-1 | |
| --- | ---: | ---: | ---: | --- |
| `api_title` | 219 | 100.0% | 94.3% | circular |
| `feature_title` | 61 | 100.0% | 91.8% | circular |
| **`function`** | **92** | **91.3%** | **62.0%** | **the gate** |

Before the merge, `function` sat at 85.9% / 42.4%; indexing the guides'
`menu_path` took top-1 from 56.5% to 62.0% on top of that. Both gains are
measured against text the index has never seen. The inflated total is printed
for continuity and means little.

**Still not a benchmark of real usage:** the manual describes what a feature
*is*, a user asks for what they *want to do*, and only 224 of the 572 endpoints
are covered — design/rating/temp have none. That blind spot is load-bearing:
the menu-path change was hand-checked on 10 design/rating/temp queries because
the gate cannot see them (8 unchanged, 2 worse — see `search_index.py`). Queries
from real sessions are what would replace this; mark them `session` and move the
gate onto them.

`python -m midas_mcp.hooks.check_features` is the second data guard: every file
under `data/features/` must name a real endpoint and agree with `_index.json`.
It does **not** gate coverage — most endpoints have no manual article, and that
is expected.

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
  ├─ server.py      #   the 16 FastMCP tools
  ├─ client.py      #   thin REST client for the MIDAS Open API
  ├─ catalog.py     #   offline endpoint lookup over data/schemas
  ├─ search_index.py#   BM25 ranking behind midas_lookup
  ├─ eval_search.py #   recall guard for the ranking (python -m midas_mcp.eval_search)
  ├─ features.py    #   GUI operation guides behind midas_guide (lazy, one file per read)
  ├─ hooks/         #   pre-request validation of DB bodies + the two data guards
  └─ auth/          #   opt-in OAuth for remote mode (MIDAS_MCP_PUBLIC_URL); see auth/README.md
data/
  ├─ schemas/       # endpoint catalog, one file per endpoint (bundled into every build)
  ├─ features/      # GUI guides, same tree shape — <uri>.json + _index.json (155 endpoints)
  ├─ eval_queries.json  # manual-derived query set for the recall guard
  └─ midas-api-reference.md
scripts/
  ├─ merge_manual.py     # folds public-manual naming into data/schemas (idempotent)
  ├─ extract_features.py # writes data/features/ from the manual articles
  └─ fetch_articles.py   # re-fetches manual articles the local scrape is missing (network)
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
pwsh mcpb/build.ps1               # external (pr) bundle — temp excluded
pwsh mcpb/build.ps1 -IncludeTemp  # internal (dev) bundle — temp included
```

Output: `dist/midas-nx.mcpb` (external) or `dist/midas-nx-dev.mcpb` (internal) →
drag onto Claude Desktop (Settings → Extensions), enter the MAPI key when
prompted. The not-yet-official `temp` endpoints (and the `midas_temp` tool) ship
only in the `-IncludeTemp` bundle — mirrors the pr/dev EC2 stacks below.

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
Docker. Two templates share one architecture and differ only in whether the
not-yet-official `temp` endpoints ship, and in network exposure:

- **`deploy/infra-pr.yaml`** — external/public. temp excluded, 80·443 open.
- **`deploy/infra-dev.yaml`** — internal. temp included, 443 restricted to
  `AllowedCidr` (required) while 80 stays open for the ACME challenge.

Upload the one you want to CloudShell and deploy it (external shown; for internal
use `infra-dev.yaml`, a distinct `--stack-name`/`ServiceHostname`, and add
`AllowedCidr=<corp CIDR>`):

```bash
aws cloudformation deploy \
  --region ap-northeast-1 \
  --template-file infra-pr.yaml \
  --stack-name midas-mcp \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      GitHubBranch=<branch> \
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

`deploy/infra-pr.yaml` (external) and `deploy/infra-dev.yaml` (internal) are the
two templates: CodeBuild→ECR→EC2, HTTPS via Caddy on a stable Elastic IP (point
your own DNS at it), weekday auto stop/start. They differ only in `IncludeTemp`,
`EcrRepositoryName`, and the 443 access rule — see [deploy/infra-ec2.md](deploy/infra-ec2.md).

## Example flow (what the model does)

1. `midas_lookup("inelastic hinge")` → `IEHG`, `IEHG-*`, `IEHP`, `IEHC`, … each
   with a one-line `desc`. Reading those picks `IEHP` ("Defines the skeleton
   curve / hysteresis model") over `IEHG` ("Assigns an inelastic hinge to an
   element") — which is why hits carry descriptions rather than uris alone.
2. `midas_describe("IEHP")` → gets the schema/example + the read rule
   (only `COMPONENT_DIR[i]==true` components hold valid values)
3. `midas_db_read("IEHP")` → live data from the open model
4. `midas_doc("ANAL")` → run analysis
5. `midas_post("TABLE", {...})` → pull result tables

## Notes

- `db/IEHP` is **undocumented** but works; inactive hinge components serialize
  uninitialized memory (garbage) — always filter by `COMPONENT_DIR`. See
  `data/midas-api-reference.md` §10-1.
