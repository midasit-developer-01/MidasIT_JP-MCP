# Build the MIDAS NX MCP server into a self-contained .mcpb bundle.
#
#   PyInstaller freezes the server (interpreter + deps) into one midas-mcp.exe,
#   so the target machine needs NO Python. The exe is OS/arch-specific: run this
#   on the OS you are distributing to (this script builds a win32 bundle).
#
#   Two variants, mirroring the EC2 stacks (deploy/infra-pr.yaml / infra-dev.yaml):
#     external (pr)  - temp endpoints EXCLUDED   -> dist/midas-nx.mcpb
#     internal (dev) - temp endpoints INCLUDED   -> dist/midas-nx-dev.mcpb
#
# Usage:  pwsh mcpb/build.ps1               # external (pr) bundle, no temp
#         pwsh mcpb/build.ps1 -IncludeTemp  # internal (dev) bundle, with temp
param(
    # Bundle the not-yet-official `temp` endpoints. Off by default = external (pr)
    # bundle, matching the Docker/CloudFormation INCLUDE_TEMP=false default. When
    # temp is excluded, server.py skips registering the midas_temp tool too.
    [switch]$IncludeTemp
)
$ErrorActionPreference = "Stop"

$mcpbDir = $PSScriptRoot
$repo    = Split-Path $mcpbDir -Parent
$build   = Join-Path $mcpbDir "build"
$packDir = Join-Path $build "pack"          # clean dir that becomes the .mcpb
$distOut = Join-Path $repo "dist"

# Variant naming: dev bundle gets a distinct filename so it never overwrites the
# external one in dist/.
$variant    = if ($IncludeTemp) { "dev (internal, temp INCLUDED)" } else { "pr (external, temp EXCLUDED)" }
$bundleName = if ($IncludeTemp) { "midas-nx-dev.mcpb" } else { "midas-nx.mcpb" }
Write-Host "==> Building variant: $variant  ->  dist/$bundleName"

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

# Stage the catalog into the build dir so the external (pr) bundle can drop temp
# BEFORE it is frozen. Mirrors the Dockerfile (rm -rf data/schemas/temp): with the
# temp schemas gone, they are absent from the catalog AND server.py skips
# registering the midas_temp tool - no discovery, no call surface.
$dataStage = Join-Path $build "data"
Copy-Item -Recurse -Force (Join-Path $repo "data") $dataStage
if ($IncludeTemp) {
    Write-Host "    temp endpoints INCLUDED (internal/dev bundle)"
} else {
    Remove-Item -Recurse -Force (Join-Path $dataStage "schemas\temp") -ErrorAction SilentlyContinue
    Write-Host "    temp endpoints EXCLUDED (external/pr bundle)"
}

Write-Host "==> [3/4] Freezing midas-mcp.exe with PyInstaller"
# --paths $repo      : find the midas_mcp package
# --add-data data    : catalog JSON (staged, temp already dropped for pr), placed
#                      at _MEIPASS/data (catalog.py finds it there)
# --collect-all mcp  : pull in FastMCP's dynamically-imported submodules
& $py -m PyInstaller `
    --onefile `
    --name midas-mcp `
    --paths $repo `
    --add-data "$dataStage;data" `
    --collect-all mcp `
    --distpath (Join-Path $packDir "server") `
    --workpath (Join-Path $build "work") `
    --specpath $build `
    (Join-Path $mcpbDir "main.py")

Copy-Item (Join-Path $mcpbDir "manifest.json") (Join-Path $packDir "manifest.json") -Force

Write-Host "==> [4/4] Packing .mcpb"
New-Item -ItemType Directory -Force $distOut | Out-Null
& npx --yes @anthropic-ai/mcpb pack $packDir (Join-Path $distOut $bundleName)

Write-Host ""
Write-Host "Done -> $distOut\$bundleName"
