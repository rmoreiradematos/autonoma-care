"""Runtime configuration. Secrets come from the environment, never from source."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    hospital_database_url: str = "sqlite:///./data/hospital.db"
    agent_database_url: str = "sqlite:///./data/agent.db"
    mcp_server_url: str = "http://127.0.0.1:8765/mcp"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    api_host: str = "0.0.0.0"
    api_port: int = 8091
    planner_mode: str = "auto"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    max_iterations: int = 12
    tool_timeout_seconds: float = 15.0
    tool_max_retries: int = 2
    context_token_budget: int = 6000
    demo_mode: bool = True
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def sqlite_file(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.removeprefix("sqlite:///")).expanduser().resolve()
