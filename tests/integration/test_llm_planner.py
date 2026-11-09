"""The LLM planner is a real HTTP client. A local stub stands in for the provider."""

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.harness.engine import AgentHarness


class _Handler(BaseHTTPRequestHandler):
    seen: dict = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        _Handler.seen = body
        has_tool = any(message.get("role") == "tool" for message in body["messages"])
        if has_tool:
            message = {"role": "assistant", "content": "Found Maria Alvarez, MRN-1001."}
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_search",
                        "type": "function",
                        "function": {
                            "name": "search_patients",
                            "arguments": json.dumps({"query": "Maria Alvarez", "limit": 5}),
                        },
                    }
                ],
            }
        payload = {
            "model": "fake-ops-1",
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args) -> None:
        return


def test_llm_planner_uses_discovered_tools_and_mcp(stack: dict) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = stack["settings"].model_copy(
            update={"llm_base_url": f"http://127.0.0.1:{port}/v1", "llm_api_key": "test-key", "planner_mode": "llm"}
        )
        harness = AgentHarness(settings)
        result = asyncio.run(harness.run("Find the patient named Maria Alvarez.", "llm.operator", "llm"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
    tool_names = [tool["function"]["name"] for tool in _Handler.seen["tools"]]
    assert "search_patients" in tool_names
    assert "cancel_appointment" in tool_names
    assert result["status"] == "completed"
    assert "Maria Alvarez" in result["summary"]
    assert any(event["event_type"] == "tool_call" and event["payload"]["tool"] == "search_patients" for event in result["events"])
    assert any(event["event_type"] == "model_response" and event["payload"]["usage"]["total_tokens"] == 28 for event in result["events"])
