from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


ToolHandler = Callable[[dict[str, Any], "ToolContext"], Awaitable[dict[str, Any] | str]]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    args_schema: dict[str, Any]
    handler: ToolHandler
    visible_to_main_agent: bool = True
    visible_to_subagents: bool = True
    confirm_required: bool = False
    timeout: int = 120
    tags: list[str] = field(default_factory=list)

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_schema,
            },
        }


@dataclass(slots=True)
class ToolContext:
    session_id: str
    repository: Any
    session_manager: Any
    subagent_pool: Any
    skills_root: Any
    executor: Any = None
    subagent_runner: Any = None
    delete_approvals: Any = None
