from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.runtime.config import ExecutionConfig


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    cwd: str | None
    returncode: int
    stdout: str
    stderr: str


class LocalExecutor:
    def __init__(self, config: ExecutionConfig) -> None:
        self.config = config
        if self.config.mode != "local":
            raise ValueError("Only local execution mode is supported in this stage.")
        if not self.config.local_only:
            raise ValueError("This executor is intentionally restricted to local-only execution.")

    async def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 120,
    ) -> CommandResult:
        final_command = self._prepare_command(command)
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                final_command,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Command timed out after {timeout}s: {shlex.join(final_command)}")

        return CommandResult(
            command=final_command,
            cwd=str(cwd) if cwd else None,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def python_command(self, args: list[str]) -> list[str]:
        executable = (
            sys.executable if self.config.python_executable == "current" else self.config.python_executable
        )
        return [executable, *args]

    async def run_shell(self, command: str, *, cwd: Path | None = None, timeout: int = 120) -> CommandResult:
        shell_command = self._shell_command(command)
        return await self.run(shell_command, cwd=cwd, timeout=timeout)

    def _prepare_command(self, command: list[str]) -> list[str]:
        if not command:
            raise ValueError("Command cannot be empty.")
        return command

    def _shell_command(self, command: str) -> list[str]:
        shell_executable = self.config.shell_executable
        if shell_executable and shutil.which(shell_executable):
            if shell_executable.lower().endswith("powershell") or "pwsh" in shell_executable.lower():
                return [shell_executable, "-Command", command]
            return [shell_executable, "-lc", command]
        if os.name == "nt":
            return ["powershell", "-Command", command]
        return ["sh", "-lc", command]
