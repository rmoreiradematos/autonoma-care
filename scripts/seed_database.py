"""Create the hospital database and load synthetic fixtures."""

from mcp_server.database import init_database, session_scope
from mcp_server.seed import seed_if_empty
from mcp_server.settings import McpSettings, sqlite_path


def main() -> None:
    settings = McpSettings()
    path = sqlite_path(settings.hospital_database_url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    init_database(settings.hospital_database_url)
    with session_scope() as session:
        created = seed_if_empty(session)
    print("seeded synthetic hospital data" if created else "hospital data already present")


if __name__ == "__main__":
    main()
