from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AgentProfile:
    role: str
    long_memory: str
    tools: str

    def build_main_system_prompt(self) -> str:
        sections = [
            "# Role Card",
            self.role.strip() or "No role card provided.",
            "",
            "# Long-Term Memory",
            self.long_memory.strip() or "No long-term memory provided.",
            "",
            "# Tool List",
            self.tools.strip() or "No tool list provided.",
        ]
        return "\n".join(sections).strip()

    def build_subagent_system_prompt(self, *, persona_prompt: str, task_prompt: str) -> str:
        sections = [
            "# Role Card",
            self.role.strip() or "No subagent role card provided.",
            "",
            "# Dynamic Persona",
            persona_prompt.strip() or "Focused specialist",
            "",
            "# Tool List",
            self.tools.strip() or "No subagent tool list provided.",
            "",
            "# Task",
            task_prompt.strip(),
            "",
            "Return a concise but complete result for the main agent.",
        ]
        return "\n".join(sections).strip()


class AgentProfileLoader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load_main_profile(self) -> AgentProfile:
        return self._load_profile(self.root / "main", include_long_memory=True)

    def load_subagent_profile(self) -> AgentProfile:
        return self._load_profile(self.root / "subagent", include_long_memory=False)

    def _load_profile(self, directory: Path, *, include_long_memory: bool) -> AgentProfile:
        return AgentProfile(
            role=_read_markdown(directory / "role.md"),
            long_memory=_read_markdown(directory / "long_memory.md") if include_long_memory else "",
            tools=_read_markdown(directory / "tools.md"),
        )


def _read_markdown(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
