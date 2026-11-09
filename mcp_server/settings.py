"""Settings for the hospital MCP server. This process does not import the agent app."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class McpSettings(BaseSettings):
    hospital_database_url: str = "sqlite:///./data/hospital.db"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw = url.removeprefix("sqlite:///")
    return Path(raw).expanduser().resolve()
