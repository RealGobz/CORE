"""
toolsets/connections.py
------------------------
Factory functions that create MCPToolset instances for each MCP server.

Usage:
    from toolsets.connections import make_mongo_toolset

    mongo_tools = make_mongo_toolset(MONGO_URI)
    agent = build_root_agent(mongo_tools, rules_tools)
"""

import os
import sys
import json
from pathlib import Path

# 1. Import the official MCP Server Parameters from the standard 'mcp' library
from mcp.client.stdio import StdioServerParameters

# 2. Import the ADK Toolset and ADK Connection wrapper
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioConnectionParams

# Root of the project (one level up from this file)
PROJECT_ROOT = Path(__file__).parent.parent


# ── MongoDB MCP Server ────────────────────────────────────────────────────────

def make_mongo_toolset(mongo_uri: str) -> MCPToolset:
    """
    Returns an MCPToolset backed by the OFFICIAL MongoDB MCP server.
    Connects via stdio using npx to avoid Windows/Docker pipe freezes.

    The MongoDB MCP server exposes:
      - find        → query documents from any collection
      - insert-many → write documents to any collection
      - update-many → bulk update with filter + update spec
      - aggregate   → run aggregation pipelines

    The Root Agent uses this for:
      • READ  — vessels, ports, regulations (find)
      • WRITE — rerouting_logs (insert-many)
    """
    npx_command = "npx.cmd" if os.name == "nt" else "npx"

    return MCPToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=npx_command,
                args=[
                    "-y",
                    "@modelcontextprotocol/server-mongodb",
                    mongo_uri
                ],
                env=None
            )
        )
    )


# ── Custom Rules MCP Server ───────────────────────────────────────────────────

def make_rules_toolset() -> MCPToolset:
    """
    Returns an MCPToolset backed by the custom FastMCP rules server.
    Exposes deterministic business-logic tools:
      - find_closest_alternative_ports
      - calculate_route_cost
      - build_rerouting_manifest
      - build_escalation_record
    """
    rules_server_path = PROJECT_ROOT / "mcp_servers" / "rules_server.py"
    return MCPToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=[str(rules_server_path)],
                env=None
            )
        )
    )
