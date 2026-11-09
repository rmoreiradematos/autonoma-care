"""Run the hospital MCP server over Streamable HTTP."""

import logging

from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.database import init_database, session_scope
from mcp_server.seed import seed_if_empty
from mcp_server.server import build_server
from mcp_server.settings import McpSettings, sqlite_path


def main() -> None:
    settings = McpSettings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    path = sqlite_path(settings.hospital_database_url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    init_database(settings.hospital_database_url)
    with session_scope() as session:
        created = seed_if_empty(session)
    if created:
        logging.getLogger("mcp_server").info("seeded synthetic hospital data")
    server = build_server()
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "mcp:*"],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
            "http://mcp:*",
        ],
    )
    server.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=security,
    )


if __name__ == "__main__":
    main()
