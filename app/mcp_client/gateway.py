"""Official MCP client. Tool calls cross the Streamable HTTP boundary."""

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.errors import ToolTransportError

_RETRYABLE = (TimeoutError, ConnectionError, OSError)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


def parse_call_result(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and "ok" in structured:
        return structured
    text = ""
    for block in getattr(result, "content", None) or []:
        block_text = getattr(block, "text", None)
        if isinstance(block_text, str):
            text = block_text
            break
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and "ok" in parsed:
            return parsed
    if getattr(result, "is_error", False):
        return {"ok": False, "code": "TOOL_ERROR", "message": text or "Tool failed."}
    return {"ok": False, "code": "BAD_TOOL_RESPONSE", "message": text or "Tool returned no structured result."}


class McpGateway:
    """Opens a real MCP session for discovery and for every tool call."""

    def __init__(self, url: str, timeout_seconds: float, max_retries: int):
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    @asynccontextmanager
    async def connect(self):
        async with streamable_http_client(self.url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    async def discover(self) -> tuple[list[ToolSpec], dict[str, str]]:
        async with self.connect() as session:
            listed = await session.list_tools()
            specs = [
                ToolSpec(name=tool.name, description=tool.description or "", input_schema=tool.input_schema)
                for tool in listed.tools
            ]
            resources = await session.list_resources()
            bodies: dict[str, str] = {}
            for resource in resources.resources:
                uri = str(resource.uri)
                payload = await session.read_resource(uri)
                text = ""
                for block in payload.contents:
                    if getattr(block, "text", None):
                        text = block.text
                        break
                bodies[uri] = text
            return specs, bodies

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                async with self.connect() as session:
                    result = await session.call_tool(name, arguments, read_timeout_seconds=self.timeout_seconds)
                return parse_call_result(result)
            except _RETRYABLE as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
        raise ToolTransportError(f"MCP call to {name} failed after {attempts} attempt(s): {last_error}") from last_error

    async def ping(self) -> bool:
        try:
            async with self.connect() as session:
                await session.list_tools()
            return True
        except Exception:
            return False
