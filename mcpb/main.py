"""PyInstaller entry point for the MIDAS NX MCP server (.mcpb binary bundle).

Frozen into a single `midas-mcp.exe` so the bundle needs NO Python on the
target machine. Always runs in stdio mode (the local / .mcpb default) — it never
touches the streamable-http path used by the EC2 deployment, so the two tracks
stay independent. Auth key comes from MIDAS_MAPI_KEY, injected by Claude Desktop
from the user_config in manifest.json.
"""

from midas_mcp.server import main

if __name__ == "__main__":
    main()
