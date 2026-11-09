"""Start the real MCP server for integration tests and evaluations."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.database import agent_session, init_agent_database
from app.harness.engine import AgentHarness
from app.memory.store import MemoryStore, seed_semantic_defaults
from mcp_server.database import init_database

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            pytest.fail(f"MCP server exited early:\n{output}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.15)
    output = ""
    if process.stdout:
        process.kill()
        output = process.stdout.read()
    pytest.fail(f"MCP server did not listen on {port}\n{output}")


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("hospital-stack")
    hospital = root / "hospital.db"
    agent = root / "agent.db"
    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "HOSPITAL_DATABASE_URL": f"sqlite:///{hospital}",
            "AGENT_DATABASE_URL": f"sqlite:///{agent}",
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_SERVER_URL": f"http://127.0.0.1:{port}/mcp",
            "PLANNER_MODE": "deterministic",
            "LLM_API_KEY": "",
            "LOG_LEVEL": "WARNING",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_server"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_port(port, process)
        settings = Settings(
            hospital_database_url=env["HOSPITAL_DATABASE_URL"],
            agent_database_url=env["AGENT_DATABASE_URL"],
            mcp_server_url=env["MCP_SERVER_URL"],
            planner_mode="deterministic",
            llm_api_key="",
            log_level="WARNING",
            max_iterations=12,
        )
        init_database(settings.hospital_database_url)
        init_agent_database(settings.agent_database_url)
        with agent_session() as session:
            seed_semantic_defaults(MemoryStore(session))
        yield {"settings": settings, "harness": AgentHarness(settings), "process": process}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
