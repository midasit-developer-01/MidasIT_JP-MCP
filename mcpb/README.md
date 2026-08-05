# Local `.mcpb` bundle (MIDAS NX MCP server)

Packages the server as a **self-contained `.mcpb`** for one-click install into
Claude Desktop. Uses the **`binary`** type: PyInstaller freezes the interpreter
and all dependencies into `midas-mcp.exe`, so the target machine needs **no
Python** and there is no `pydantic-core` version-matching problem.

This track shares only the core (`../midas_mcp/`, `../data/`) with the AWS/EC2
track. It always runs in **stdio** mode and never touches the streamable-http
path, so the two deployments stay independent.

## Files (source — committed)

| File | Purpose |
|------|---------|
| `manifest.json` | Bundle metadata + the MAPI-key config prompt shown at install |
| `main.py` | PyInstaller entry point → `midas_mcp.server:main` (stdio) |
| `build.ps1` | Freeze the exe and pack the `.mcpb` (`-IncludeTemp` for the internal/dev variant) |
| `build/` | Build scratch (git-ignored) |

## Build

Two variants, mirroring the EC2 stacks (`deploy/infra-pr.yaml` / `infra-dev.yaml`):

```powershell
pwsh mcpb/build.ps1               # external (pr): temp EXCLUDED -> dist/midas-nx.mcpb
pwsh mcpb/build.ps1 -IncludeTemp  # internal (dev): temp INCLUDED -> dist/midas-nx-dev.mcpb
```

The not-yet-official `temp` endpoints are gated by **file presence**: without
`-IncludeTemp`, the script stages `../data` and drops `schemas/temp` before
PyInstaller freezes it (the same as the Dockerfile's `rm -rf data/schemas/temp`).
With those schemas gone they are absent from the catalog and `server.py` skips
registering the `midas_temp` tool — no discovery, no call surface. No runtime env
var is involved.

Requirements on the build machine: Python 3.10+, and Node/npx (for
`@anthropic-ai/mcpb pack`). The produced exe is **win32-only** — build on macOS
to ship a macOS bundle, and add the matching platform to `manifest.json`
`compatibility.platforms`.

## Install

Drag the bundle you built — `dist/midas-nx.mcpb` (external) or
`dist/midas-nx-dev.mcpb` (internal) — onto Claude Desktop (Settings →
Extensions), enter your MIDAS **MAPI Key** when prompted, and enable it. The
MIDAS NX app must be running with a model file open before the tools will work.

## Verify (before packing)

Smoke-test the frozen exe over stdio:

```powershell
$env:MIDAS_MAPI_KEY = "<your key>"
mcpb/build/pack/server/midas-mcp.exe   # Ctrl+C to stop; should start without import errors
```
