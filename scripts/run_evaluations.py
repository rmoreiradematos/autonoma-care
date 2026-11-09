"""Run the deterministic evaluation suite against a temporary MCP server."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.database import agent_session, init_agent_database
from app.evaluations.report import render_markdown
from app.evaluations.runner import run_suite
from app.harness.engine import AgentHarness
from app.memory.store import MemoryStore, seed_semantic_defaults
from mcp_server.database import init_database


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    root = Path(os.environ.get("EVAL_DIR", "reports")).resolve()
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    hospital = work / "hospital.db"
    agent = work / "agent.db"
    port = _port()
    env = os.environ.copy()
    env.update(
        {
            "HOSPITAL_DATABASE_URL": f"sqlite:///{hospital}",
            "AGENT_DATABASE_URL": f"sqlite:///{agent}",
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
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
        deadline = time.time() + 20
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(process.stdout.read() if process.stdout else "mcp exited")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.15)
        else:
            raise RuntimeError("MCP server did not start")
        settings = Settings(
            hospital_database_url=env["HOSPITAL_DATABASE_URL"],
            agent_database_url=env["AGENT_DATABASE_URL"],
            mcp_server_url=f"http://127.0.0.1:{port}/mcp",
            planner_mode="deterministic",
            llm_api_key="",
            log_level="WARNING",
        )
        init_database(settings.hospital_database_url)
        init_agent_database(settings.agent_database_url)
        with agent_session() as session:
            seed_semantic_defaults(MemoryStore(session))
        report = asyncio.run(run_suite(AgentHarness(settings)))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    markdown = render_markdown(report)
    root.mkdir(parents=True, exist_ok=True)
    (root / "evaluation-report.md").write_text(markdown)
    (root / "evaluation-report.json").write_text(json.dumps(report, indent=2))
    print(markdown)
    if report["summary"]["passed"] != report["summary"]["scenarios"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
