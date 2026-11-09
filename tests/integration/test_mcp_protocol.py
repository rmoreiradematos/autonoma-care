"""Prove the hospital server speaks MCP, instead of being called as a local function."""

import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def mcp_url(tmp_path: Path) -> str:
    port = _free_port()
    database = tmp_path / "hospital.db"
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_server"],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **dict(**{key: value for key, value in __import__("os").environ.items()}),
            "HOSPITAL_DATABASE_URL": f"sqlite:///{database}",
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "LOG_LEVEL": "WARNING",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 20
    last_error = "server did not start"
    while time.time() < deadline:
        if process.poll() is not None:
            last_error = process.stdout.read() if process.stdout else "process exited"
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError as exc:
            last_error = str(exc)
            time.sleep(0.15)
    else:
        process.kill()
        output = process.stdout.read() if process.stdout else ""
        pytest.fail(f"MCP server did not listen: {last_error}\n{output}")
    try:
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def test_client_discovers_tools_and_reads_resources(mcp_url: str) -> None:
    script = textwrap.dedent(
        f"""
        import asyncio, json
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async def main():
            async with streamable_http_client({mcp_url!r}) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = sorted(tool.name for tool in tools.tools)
                    resources = await session.list_resources()
                    uris = sorted(str(item.uri) for item in resources.resources)
                    search = await session.call_tool("search_patients", {{"query": "Maria Alvarez"}})
                    cancel = await session.call_tool(
                        "cancel_appointment",
                        {{"appointment_id": "APT-ELENA", "reason": "no grant"}},
                    )
                    rules = await session.read_resource("hospital://policies/appointment-rules")
                    print(json.dumps({{
                        "tools": names,
                        "resources": uris,
                        "search_error": search.is_error,
                        "search": search.structured_content,
                        "cancel_error": cancel.is_error,
                        "cancel": cancel.structured_content,
                        "rules": rules.contents[0].text[:40],
                    }}))

        asyncio.run(main())
        """
    )
    completed = subprocess.run([sys.executable, "-c", script], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    import json

    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert "search_patients" in payload["tools"]
    assert "schedule_appointment" in payload["tools"]
    assert "verify_insurance_eligibility" in payload["tools"]
    assert "create_administrative_task" in payload["tools"]
    assert "hospital://policies/appointment-rules" in payload["resources"]
    assert "hospital://policies/insurance-rules" in payload["resources"]
    assert "hospital://directory/doctors" in payload["resources"]
    assert payload["search_error"] is False
    assert payload["search"]["patients"][0]["mrn"] == "MRN-1001"
    assert payload["cancel"]["ok"] is False
    assert payload["cancel"]["code"] == "APPROVAL_REQUIRED"
    assert "Appointment" in payload["rules"] or "appointment" in payload["rules"].lower() or payload["rules"]
