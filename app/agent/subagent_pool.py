from __future__ import annotations

from datetime import datetime

from app.agent.subagent import SubagentState


class SubagentPool:
    def __init__(self, max_subagents: int) -> None:
        self.max_subagents = max_subagents
        self._agents: dict[str, SubagentState] = {}

    def spawn(
        self,
        *,
        session_id: str,
        task_prompt: str,
        persona_prompt: str,
        allowed_tools: list[str] | None = None,
    ) -> SubagentState:
        if len(self._agents) >= self.max_subagents:
            raise RuntimeError(f"Subagent limit reached: {self.max_subagents}")

        subagent_id = f"subagent_{len(self._agents) + 1:02d}"
        state = SubagentState(
            subagent_id=subagent_id,
            session_id=session_id,
            task_prompt=task_prompt,
            persona_prompt=persona_prompt,
            allowed_tools=list(allowed_tools or []),
        )
        self._agents[subagent_id] = state
        return state

    def acquire_or_spawn(
        self,
        *,
        session_id: str,
        task_prompt: str,
        persona_prompt: str,
        allowed_tools: list[str] | None = None,
    ) -> SubagentState:
        for state in self.list_states():
            if state.status == "idle":
                state.session_id = session_id
                state.task_prompt = task_prompt
                state.persona_prompt = persona_prompt
                state.allowed_tools = list(allowed_tools or [])
                state.updated_at = _now_str()
                return state
        return self.spawn(
            session_id=session_id,
            task_prompt=task_prompt,
            persona_prompt=persona_prompt,
            allowed_tools=allowed_tools,
        )

    def mark_busy(
        self,
        subagent_id: str,
        *,
        task_prompt: str | None = None,
        persona_prompt: str | None = None,
    ) -> None:
        state = self._agents[subagent_id]
        state.status = "busy"
        if task_prompt is not None:
            state.task_prompt = task_prompt
        if persona_prompt is not None:
            state.persona_prompt = persona_prompt
        state.updated_at = _now_str()

    def mark_idle(self, subagent_id: str, result: str | None = None) -> None:
        state = self._agents[subagent_id]
        state.status = "idle"
        state.last_result = result
        state.updated_at = _now_str()

    def list_states(self) -> list[SubagentState]:
        return sorted(self._agents.values(), key=lambda item: item.subagent_id)

    def kill_all(self) -> int:
        count = len(self._agents)
        for state in self._agents.values():
            state.status = "killed"
            state.updated_at = _now_str()
        self._agents.clear()
        return count


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
