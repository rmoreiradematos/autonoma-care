"""FastAPI application for the healthcare operations agent."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import Settings, sqlite_file
from app.database import agent_session, init_agent_database
from app.errors import DomainError
from app.harness.engine import AgentHarness
from app.logging_config import configure_logging
from app.memory.store import MemoryStore, seed_semantic_defaults
from mcp_server.database import init_database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for url in (settings.hospital_database_url, settings.agent_database_url):
            path = sqlite_file(url)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
        init_database(settings.hospital_database_url)
        init_agent_database(settings.agent_database_url)
        with agent_session() as session:
            seed_semantic_defaults(MemoryStore(session))
        app.state.settings = settings
        app.state.harness = AgentHarness(settings)
        yield

    app = FastAPI(
        title="Healthcare Operations Agent",
        version="0.1.0",
        description=(
            "Administrative automation for a synthetic hospital. "
            "The agent plans work, calls a custom MCP server, and pauses for human approval "
            "on cancellations, reschedules, and patient-information changes. "
            "It does not diagnose or recommend treatment."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.harness = AgentHarness(settings)
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def _domain_error(_request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message})

    return app


app = create_app()
