from __future__ import annotations

from app.runtime.config import ModelProfile


def build_chat_payload(
    *,
    messages: list[dict],
    tools: list[dict] | None,
    profile: ModelProfile,
) -> dict:
    payload: dict = {
        "model": profile.model,
        "messages": messages,
        "temperature": profile.temperature,
    }
    if tools:
        payload["tools"] = tools
    if profile.max_tokens is not None:
        payload["max_tokens"] = profile.max_tokens
    if profile.reasoning_effort is not None:
        payload["reasoning_effort"] = profile.reasoning_effort
    if profile.thinking is not None:
        payload["thinking"] = profile.thinking
    if profile.extra_body:
        payload.update(profile.extra_body)
    return payload
