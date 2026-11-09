"""OpenAI-compatible tool-calling planner. The harness still executes the tools."""

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class LlmDecision:
    text: str
    calls: list[dict[str, Any]]
    raw_message: dict[str, Any]
    usage: dict[str, Any]
    model: str


class OpenAICompatiblePlanner:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmDecision:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        message = body["choices"][0]["message"]
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {"_raw": raw_arguments}
            else:
                arguments = raw_arguments
            calls.append({"id": call.get("id") or "", "name": function.get("name") or "", "arguments": arguments})
        return LlmDecision(
            text=message.get("content") or "",
            calls=calls,
            raw_message=message,
            usage=body.get("usage") or {},
            model=body.get("model") or self.model,
        )


def openai_tools(specs: list) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
        }
        for spec in specs
    ]
