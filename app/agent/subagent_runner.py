from __future__ import annotations

from typing import Any

from app.agent.profile_loader import AgentProfileLoader
from app.agent.subagent import SubagentState
from app.agent.subagent_pool import SubagentPool
from app.llm.client import OpenAICompatibleClient, extract_assistant_message, extract_assistant_text, tool_call_arguments
from app.memory.writer import MemoryWriter
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolContext


class SubagentRunner:
    def __init__(
        self,
        *,
        client: OpenAICompatibleClient,
        registry: ToolRegistry,
        tool_context: ToolContext,
        subagent_pool: SubagentPool,
        memory_writer: MemoryWriter,
        profile_loader: AgentProfileLoader,
        max_loops: int = 8,
    ) -> None:
        self.client = client
        self.registry = registry
        self.tool_context = tool_context
        self.subagent_pool = subagent_pool
        self.memory_writer = memory_writer
        self.profile_loader = profile_loader
        self.max_loops = max_loops

    async def run_task(
        self,
        *,
        session_id: str,
        submemory_dir,
        task_prompt: str,
        persona_prompt: str,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        state = self.subagent_pool.acquire_or_spawn(
            session_id=session_id,
            task_prompt=task_prompt,
            persona_prompt=persona_prompt,
            allowed_tools=allowed_tools or [],
        )
        self.subagent_pool.mark_busy(state.subagent_id, task_prompt=task_prompt, persona_prompt=persona_prompt)
        try:
            result_text = await self._execute(state, task_prompt)
            saved = await self.memory_writer.write_submemory(
                submemory_dir=submemory_dir,
                session_id=session_id,
                subagent_id=state.subagent_id,
                task=task_prompt,
                persona=persona_prompt,
                content=result_text,
            )
            self.tool_context.repository.index_saved_memory(saved)
            self.subagent_pool.mark_idle(state.subagent_id, result_text)
            return {
                "subagent_id": state.subagent_id,
                "result": result_text,
                "memory_file_id": saved.file_id,
                "memory_path": str(saved.path),
            }
        except Exception:
            self.subagent_pool.mark_idle(state.subagent_id, "subagent failed")
            raise

    async def _execute(self, state: SubagentState, task_prompt: str) -> str:
        system_prompt = self.profile_loader.load_subagent_profile().build_subagent_system_prompt(
            persona_prompt=state.persona_prompt or "Task specialist",
            task_prompt=task_prompt,
        )
        tools = self.registry.visible_for_subagent(state.allowed_tools)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(state.context_messages)
        messages.append({"role": "user", "content": task_prompt})

        for _ in range(self.max_loops):
            payload = await self.client.chat(messages, tools=tools)
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
                messages.append(assistant_turn)
                for tool_call in tool_calls:
                    name = tool_call.get("function", {}).get("name", "")
                    args = tool_call_arguments(tool_call)
                    result = await self.registry.dispatch(name, args, self.tool_context)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "name": name,
                            "content": result,
                        }
                    )
                continue
            return extract_assistant_text(payload).strip()
        return "Subagent paused after reaching its tool loop budget."
