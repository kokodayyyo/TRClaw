from __future__ import annotations

from dataclasses import asdict
import json
import shutil
from pathlib import Path
from typing import Any

from app.memory.reader import read_text
from app.tools.schemas import ToolContext, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def visible_for_main_agent(self) -> list[dict[str, Any]]:
        return [
            spec.to_openai_tool()
            for spec in self._tools.values()
            if spec.visible_to_main_agent
        ]

    def visible_for_subagent(self, allowed_names: list[str] | None = None) -> list[dict[str, Any]]:
        allowed = set(allowed_names or [])
        items = []
        for spec in self._tools.values():
            if not spec.visible_to_subagents:
                continue
            if allowed and spec.name not in allowed:
                continue
            items.append(spec.to_openai_tool())
        return items

    async def dispatch(self, name: str, args: dict[str, Any], context: ToolContext) -> str:
        result = await self.get(name).handler(args, context)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, indent=2)


def build_default_registry(skills_root: Path) -> ToolRegistry:
    registry = ToolRegistry()

    def resolve_path(raw_path: str, *, cwd: str | None = None) -> Path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve(strict=False)
        base = Path(cwd).expanduser().resolve(strict=False) if cwd else Path.cwd().resolve(strict=False)
        return (base / candidate).resolve(strict=False)

    async def search_memory(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        top_k = int(args.get("top_k", 5))
        results = context.repository.search_memory(query=query, top_k=top_k)
        return {"results": [asdict(result) for result in results]}

    async def search_submemory(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        top_k = int(args.get("top_k", 5))
        subagent_id = args.get("subagent_id")
        results = context.repository.search_submemory(
            query=query,
            top_k=top_k,
            subagent_id=subagent_id,
        )
        return {"results": [asdict(result) for result in results]}

    async def read_memory(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        file_id = str(args.get("file_id", "")).strip()
        document = context.repository.get_document(file_id)
        if not document:
            return {"error": f"Unknown memory file_id: {file_id}"}
        path = Path(document["path"])
        return {
            "file_id": file_id,
            "path": str(path),
            "content": read_text(path),
        }

    async def read_file(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_path(str(args.get("path", "")), cwd=args.get("cwd"))
        if not path.exists():
            return {"error": f"Path does not exist: {path}"}
        if path.is_dir():
            return {"error": f"Path is a directory, not a file: {path}"}
        text = path.read_text(encoding="utf-8", errors="replace")
        max_chars = int(args.get("max_chars", 20000))
        truncated = text[:max_chars]
        return {
            "path": str(path),
            "content": truncated,
            "truncated": len(text) > len(truncated),
            "size": len(text),
        }

    async def write_file(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_path(str(args.get("path", "")), cwd=args.get("cwd"))
        content = str(args.get("content", ""))
        mode = str(args.get("mode", "overwrite"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append" and path.exists():
            with path.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            path.write_text(content, encoding="utf-8")
        return {"path": str(path), "mode": mode, "bytes_written": len(content.encode("utf-8"))}

    async def list_directory(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_path(str(args.get("path", ".")), cwd=args.get("cwd"))
        recursive = bool(args.get("recursive", False))
        if not path.exists():
            return {"error": f"Path does not exist: {path}"}
        if not path.is_dir():
            return {"error": f"Path is not a directory: {path}"}
        items = path.rglob("*") if recursive else path.iterdir()
        results = []
        for item in items:
            results.append(
                {
                    "path": str(item),
                    "type": "dir" if item.is_dir() else "file",
                }
            )
            if len(results) >= 200:
                break
        return {"path": str(path), "results": results}

    async def run_terminal_command(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        command = str(args.get("command", "")).strip()
        cwd = args.get("cwd")
        timeout = int(args.get("timeout", 120))
        result = await context.executor.run_shell(command, cwd=resolve_path(cwd, cwd=None) if cwd else None, timeout=timeout)
        return {
            "command": " ".join(result.command),
            "cwd": result.cwd,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def run_python_code(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        code = str(args.get("code", ""))
        cwd = args.get("cwd")
        timeout = int(args.get("timeout", 120))
        command = context.executor.python_command(["-c", code])
        result = await context.executor.run(command, cwd=resolve_path(cwd, cwd=None) if cwd else None, timeout=timeout)
        return {
            "command": " ".join(result.command),
            "cwd": result.cwd,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def delete_path(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_path(str(args.get("path", "")), cwd=args.get("cwd"))
        recursive = bool(args.get("recursive", False))
        if not path.exists():
            return {"error": f"Path does not exist: {path}"}
        if not context.delete_approvals or not context.delete_approvals.consume(path):
            return {
                "error": (
                    "Deletion requires explicit user confirmation. "
                    f"Ask the user to run /confirm delete {path}"
                )
            }
        try:
            if path.is_dir():
                if recursive:
                    shutil.rmtree(path)
                else:
                    path.rmdir()
            else:
                path.unlink()
            return {"deleted": str(path), "recursive": recursive}
        except OSError as exc:
            return {"error": f"Delete failed for {path}: {exc}"}

    async def list_subagents(_: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        items = [
            {
                "subagent_id": state.subagent_id,
                "status": state.status,
                "task_prompt": state.task_prompt,
                "persona_prompt": state.persona_prompt,
                "allowed_tools": state.allowed_tools,
                "updated_at": state.updated_at,
            }
            for state in context.subagent_pool.list_states()
        ]
        return {"results": items}

    async def list_skills(_: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if not context.skills_root.exists():
            return {"results": []}
        items = [item.name for item in context.skills_root.iterdir() if item.is_dir()]
        return {"results": sorted(items)}

    async def run_subagent_task(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        task = str(args.get("task", "")).strip()
        persona = str(args.get("persona", "Focused specialist")).strip()
        allowed_tools = list(args.get("allowed_tools", []))
        session = context.session_manager.ensure_session()
        return await context.subagent_runner.run_task(
            session_id=session.session_id,
            submemory_dir=session.submemory_dir,
            task_prompt=task,
            persona_prompt=persona,
            allowed_tools=allowed_tools,
        )

    registry.register(
        ToolSpec(
            name="search_memory",
            description="Search the top matching main memory summaries.",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            handler=search_memory,
            tags=["memory"],
        )
    )
    registry.register(
        ToolSpec(
            name="search_submemory",
            description="Search the top matching subagent memory summaries.",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                    "subagent_id": {"type": "string"},
                },
                "required": ["query"],
            },
            handler=search_submemory,
            tags=["memory", "subagent"],
        )
    )
    registry.register(
        ToolSpec(
            name="read_memory",
            description="Read the full markdown content of a memory file by file_id.",
            args_schema={
                "type": "object",
                "properties": {"file_id": {"type": "string"}},
                "required": ["file_id"],
            },
            handler=read_memory,
            tags=["memory"],
        )
    )
    registry.register(
        ToolSpec(
            name="list_subagents",
            description="List the currently active subagents in this session.",
            args_schema={"type": "object", "properties": {}},
            handler=list_subagents,
            tags=["subagent"],
        )
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description="Read a local file from the current machine.",
            args_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "cwd": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 20000},
                },
                "required": ["path"],
            },
            handler=read_file,
            tags=["filesystem"],
        )
    )
    registry.register(
        ToolSpec(
            name="write_file",
            description="Write or append content to a local file on the current machine.",
            args_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "cwd": {"type": "string"},
                    "content": {"type": "string"},
                    "mode": {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
            tags=["filesystem"],
        )
    )
    registry.register(
        ToolSpec(
            name="list_directory",
            description="List files and directories on the current machine.",
            args_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "cwd": {"type": "string"},
                    "recursive": {"type": "boolean", "default": False},
                },
            },
            handler=list_directory,
            tags=["filesystem"],
        )
    )
    registry.register(
        ToolSpec(
            name="run_terminal_command",
            description="Run a terminal command on the current machine.",
            args_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer", "default": 120},
                },
                "required": ["command"],
            },
            handler=run_terminal_command,
            tags=["terminal", "execution"],
        )
    )
    registry.register(
        ToolSpec(
            name="run_python_code",
            description="Run Python code using the current Python environment on the current machine.",
            args_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer", "default": 120},
                },
                "required": ["code"],
            },
            handler=run_python_code,
            tags=["python", "execution"],
        )
    )
    registry.register(
        ToolSpec(
            name="list_skills",
            description="List installed skill directories.",
            args_schema={"type": "object", "properties": {}},
            handler=list_skills,
            tags=["skills"],
        )
    )
    registry.register(
        ToolSpec(
            name="run_subagent_task",
            description="Spawn or reuse a subagent to complete a focused task and save the result to submemory.",
            args_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "persona": {"type": "string"},
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["task"],
            },
            handler=run_subagent_task,
            visible_to_subagents=False,
            tags=["subagent"],
        )
    )
    registry.register(
        ToolSpec(
            name="delete_path",
            description="Delete a local file or directory, but only after the user has explicitly confirmed it.",
            args_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "cwd": {"type": "string"},
                    "recursive": {"type": "boolean", "default": False},
                },
                "required": ["path"],
            },
            handler=delete_path,
            confirm_required=True,
            tags=["filesystem", "delete"],
        )
    )
    return registry
