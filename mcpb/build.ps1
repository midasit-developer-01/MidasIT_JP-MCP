# Build the MIDAS NX MCP server into a self-contained .mcpb bundle.
#
#   PyInstaller freezes the server (interpreter + deps) into one midas-mcp.exe,
#   so the target machine needs NO Python. The exe is OS/arch-specific: run this
#   on the OS you are distributing to (this script builds a win32 bundle).
#
#   Output: dist/midas-nx.mcpb  (drag onto Claude Desktop to install)
#
# Usage:  pwsh mcpb/build.ps1
$ErrorActionPreference = "Stop"

$mcpbDir = $PSScriptRoot
$repo    = Split-Path $mcpbDir -Parent
$build   = Join-Path $mcpbDir "build"
$packDir = Join-Path $build "pack"          # clean dir that becomes the .mcpb
$distOut = Join-Path $repo "dist"

# Use the repo venv (it has mcp + requests) so PyInstaller can see and bundle
# them; fall back to bare `python` otherwise. We install the runtime deps (not
# the project itself — PyInstaller finds midas_mcp via --paths) so there is no
# console-script exe to lock, and so a bare-python fallback still has the deps.
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Write-Host "==> Using interpreter: $py"

Write-Host "==> [1/4] Installing locked runtime deps + pyinstaller"
# constraints.txt is the single pinned lock shared with the Docker build (which
# uses it as `pip install . -c constraints.txt`). Here it doubles as the install
# list (`-r`) so the .mcpb bundle gets the exact same versions as the container.
& $py -m pip install --quiet -r (Join-Path $repo "constraints.txt") pyinstaller

Write-Host "==> [2/4] Cleaning previous build"
Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $packDir "server") | Out-Null

Write-Host "==> [3/4] Freezing midas-mcp.exe with PyInstaller"
# --paths $repo      : find the midas_mcp package
# --add-data data    : catalog JSON, placed at _MEIPASS/data (catalog.py finds it there)
# --collect-all mcp  : pull in FastMCP's dynamically-imported submodules
& $py -m PyInstaller `
    --onefile `
    --name midas-mcp `
    --paths $repo `
    --add-data "$repo\data;data" `
    --collect-all mcp `
    --distpath (Join-Path $packDir "server") `
    --workpath (Join-Path $build "work") `
    --specpath $build `
    (Join-Path $mcpbDir "main.py")

Copy-Item (Join-Path $mcpbDir "manifest.json") (Join-Path $packDir "manifest.json") -Force

Write-Host "==> [4/4] Packing .mcpb"
New-Item -ItemType Directory -Force $distOut | Out-Null
& npx --yes @anthropic-ai/mcpb pack $packDir (Join-Path $distOut "midas-nx.mcpb")

Write-Host ""
Write-Host "Done -> $distOut\midas-nx.mcpb"
