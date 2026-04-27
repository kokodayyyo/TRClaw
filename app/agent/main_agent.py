from __future__ import annotations

from dataclasses import dataclass
import asyncio
import time
from typing import Any

from app.agent.profile_loader import AgentProfileLoader
from app.llm.client import OpenAICompatibleClient, extract_assistant_message, extract_assistant_text, tool_call_arguments
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolContext


@dataclass(slots=True)
class AgentRunResult:
    status: str
    text: str
    loops_used: int
    messages: list[dict[str, Any]]
    stop_reason: str


class MainAgent:
    def __init__(
        self,
        *,
        client: OpenAICompatibleClient,
        registry: ToolRegistry,
        tool_context: ToolContext,
        profile_loader: AgentProfileLoader,
        default_max_loops: int | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.tool_context = tool_context
        self.profile_loader = profile_loader
        self.default_max_loops = default_max_loops

    async def respond(self, history: list[dict[str, Any]], user_input: str) -> str:
        result = await self.execute(
            history=history,
            user_input=user_input,
            max_loops=self.default_max_loops,
        )
        if result.text:
            return result.text
        if result.stop_reason == "loop_budget_exceeded":
            return (
                "Main agent paused after reaching its tool loop budget "
                f"({result.loops_used}/{self.default_max_loops})."
            )
        if result.stop_reason == "runtime_budget_exceeded":
            return "Main agent paused after reaching its runtime budget."
        return "Main agent paused before producing a final answer."

    async def execute(
        self,
        *,
        history: list[dict[str, Any]] | None,
        user_input: str | None,
        max_loops: int | None,
        max_runtime_seconds: int | None = None,
        initial_messages: list[dict[str, Any]] | None = None,
        checkpoint_handler: Any = None,
    ) -> AgentRunResult:
        tools = self.registry.visible_for_main_agent()
        system_prompt = self.profile_loader.load_main_profile().build_main_system_prompt()
        if initial_messages is not None:
            working_messages = [dict(message) for message in initial_messages]
        else:
            working_messages = [{"role": "system", "content": system_prompt}]
            working_messages.extend(history or [])
            if user_input is not None:
                working_messages.append({"role": "user", "content": user_input})

        started_at = time.monotonic()
        loops_used = 0

        while True:
            if max_loops is not None and loops_used >= max_loops:
                return AgentRunResult(
                    status="paused",
                    text="",
                    loops_used=loops_used,
                    messages=working_messages,
                    stop_reason="loop_budget_exceeded",
                )
            if max_runtime_seconds is not None and (time.monotonic() - started_at) >= max_runtime_seconds:
                return AgentRunResult(
                    status="paused",
                    text="",
                    loops_used=loops_used,
                    messages=working_messages,
                    stop_reason="runtime_budget_exceeded",
                )
            payload = await self.client.chat(working_messages, tools=tools)
            loops_used += 1
            assistant_message = extract_assistant_message(payload)
            tool_calls = assistant_message.get("tool_calls") or []
            if tool_calls:
                assistant_turn = {
                    "role": "assistant",
                    "content": assistant_message.get("content"),
                    "tool_calls": tool_calls,
                }
                if assistant_message.get("reasoning_content") is not None:
                    assistant_turn["reasoning_content"] = assistant_message.get("reasoning_content")
                working_messages.append(assistant_turn)
                results = await _dispatch_tool_calls(tool_calls, self.registry, self.tool_context)
                working_messages.extend(results)
                if checkpoint_handler is not None:
                    await checkpoint_handler(working_messages, loops_used, "tool_round")
                continue
            text = extract_assistant_text(payload).strip()
            assistant_turn = {
                "role": "assistant",
                "content": assistant_message.get("content") or text,
            }
            if assistant_message.get("reasoning_content") is not None:
                assistant_turn["reasoning_content"] = assistant_message.get("reasoning_content")
            working_messages.append(assistant_turn)
            if checkpoint_handler is not None:
                await checkpoint_handler(working_messages, loops_used, "completed")
            return AgentRunResult(
                status="completed",
                text=text,
                loops_used=loops_used,
                messages=working_messages,
                stop_reason="completed",
            )


async def _dispatch_tool_calls(tool_calls: list[dict[str, Any]], registry: ToolRegistry, context: ToolContext) -> list[dict[str, Any]]:
    async def one(tool_call: dict[str, Any]) -> dict[str, Any]:
        name = tool_call.get("function", {}).get("name", "")
        args = tool_call_arguments(tool_call)
        result = await registry.dispatch(name, args, context)
        return {
            "role": "tool",
            "tool_call_id": tool_call.get("id"),
            "name": name,
            "content": result,
        }

    return await asyncio.gather(*(one(tool_call) for tool_call in tool_calls))
