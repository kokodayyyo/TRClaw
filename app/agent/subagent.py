from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class SubagentState:
    subagent_id: str
    session_id: str
    status: str = "idle"
    task_prompt: str | None = None
    persona_prompt: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    context_messages: list[dict] = field(default_factory=list)
    last_result: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
