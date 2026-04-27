from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.request_builder import build_chat_payload
from app.runtime.config import ModelProfile


class OpenAICompatibleClient:
    def __init__(self, profile: ModelProfile) -> None:
        self.profile = profile

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict[str, Any]:
        payload = build_chat_payload(messages=messages, tools=tools, profile=self.profile)
        headers = {"Content-Type": "application/json"}
        if self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"
        headers.update(self.profile.extra_headers)

        url = f"{self.profile.base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=self.profile.timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text
                raise RuntimeError(
                    f"Model request failed: {exc.response.status_code} {detail}"
                ) from exc

    async def summarize_markdown(self, text: str) -> str:
        prompt = (
            "Read the conversation record and write a short # Summary section in 1-3 sentences. "
            "Keep it compact and factual. Use the same language as the source text when possible."
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text[:12000]},
        ]
        try:
            payload = await self.chat(messages, tools=None)
            return extract_assistant_text(payload).strip() or fallback_summary(text)
        except Exception:
            return fallback_summary(text)


def extract_assistant_text(response: dict[str, Any]) -> str:
    choices = response.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(part for part in parts if part)
    return ""


def extract_assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices", [])
    if not choices:
        return {"role": "assistant", "content": ""}
    message = dict(choices[0].get("message", {}))
    message.setdefault("role", "assistant")
    if "reasoning_content" in choices[0]:
        message["reasoning_content"] = choices[0].get("reasoning_content")
    elif "reasoning_content" in message:
        message["reasoning_content"] = message.get("reasoning_content")
    return message


def fallback_summary(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:240] + ("..." if len(collapsed) > 240 else "")


def tool_call_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    function_data = tool_call.get("function", {})
    raw = function_data.get("arguments") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_arguments": raw}
