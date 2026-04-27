from __future__ import annotations

from app.runtime.config import CommandsConfig


class CommandRouter:
    def __init__(self, commands: CommandsConfig) -> None:
        self.commands = commands

    def route(self, text: str) -> tuple[str, str]:
        stripped = text.strip()
        if stripped == self.commands.new:
            return ("new", "")
        if stripped == self.commands.agents:
            return ("agents", "")
        if stripped.startswith(self.commands.remember):
            return ("remember", stripped[len(self.commands.remember) :].strip())
        if stripped.startswith(self.commands.callmemory):
            return ("callmemory", stripped[len(self.commands.callmemory) :].strip())
        if stripped == self.commands.tasks:
            return ("tasks", "")
        if stripped == self.commands.kill_subagents:
            return ("kill_subagents", "")
        if stripped == self.commands.exit:
            return ("exit", "")
        if stripped.startswith(self.commands.task_run):
            return ("task_run", stripped[len(self.commands.task_run) :].strip())
        if stripped.startswith(self.commands.task_show):
            return ("task_show", stripped[len(self.commands.task_show) :].strip())
        if stripped.startswith(self.commands.task_resume):
            return ("task_resume", stripped[len(self.commands.task_resume) :].strip())
        if stripped.startswith(self.commands.memory_search):
            return ("memory_search", stripped[len(self.commands.memory_search) :].strip())
        if stripped.startswith(self.commands.submemory_search):
            return ("submemory_search", stripped[len(self.commands.submemory_search) :].strip())
        if stripped.startswith(self.commands.confirm_delete):
            return ("confirm_delete", stripped[len(self.commands.confirm_delete) :].strip())
        return ("chat", stripped)
