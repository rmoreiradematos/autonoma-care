"""Build the hospital MCP server. Transport startup lives in __main__."""

from mcp.server.mcpserver import MCPServer

from mcp_server.resources import register_resources
from mcp_server.tools import register_tools


def build_server() -> MCPServer:
    server = MCPServer(
        "hospital-operations",
        title="Hospital Operations",
        instructions=(
            "Synthetic administrative systems for a fictional hospital. "
            "Tools manage patients, schedules, insurance fixtures, and operations tasks. "
            "They do not diagnose, treat, or recommend clinical care. "
            "Sensitive writes reject calls that do not present a valid approval grant."
        ),
        version="0.1.0",
    )
    register_tools(server)
    register_resources(server)
    return server
