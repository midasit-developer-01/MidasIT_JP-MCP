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
| `build.ps1` | Freeze the exe and pack the `.mcpb` |
| `build/` | Build scratch (git-ignored) |

## Build

```powershell
pwsh mcpb/build.ps1
# -> dist/midas-nx.mcpb
```

Requirements on the build machine: Python 3.10+, and Node/npx (for
`@anthropic-ai/mcpb pack`). The produced exe is **win32-only** — build on macOS
to ship a macOS bundle, and add the matching platform to `manifest.json`
`compatibility.platforms`.

## Install

Drag `dist/midas-nx.mcpb` onto Claude Desktop (Settings → Extensions), enter
your MIDAS **MAPI Key** when prompted, and enable it. The MIDAS NX app must be
running with a model file open before the tools will work.

## Verify (before packing)

Smoke-test the frozen exe over stdio:

```powershell
$env:MIDAS_MAPI_KEY = "<your key>"
mcpb/build/pack/server/midas-mcp.exe   # Ctrl+C to stop; should start without import errors
```
