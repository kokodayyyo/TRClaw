from __future__ import annotations

import asyncio
from pathlib import Path

from app.agent.main_agent import MainAgent
from app.agent.profile_loader import AgentProfileLoader
from app.agent.subagent_pool import SubagentPool
from app.agent.subagent_runner import SubagentRunner
from app.channel.cli_channel import CLIChannel
from app.channel.qq_channel import QQChannel
from app.channel.schemas import ChannelMessage
from app.llm.client import OpenAICompatibleClient
from app.memory.summarizer import MemorySummarizer
from app.memory.writer import MemoryWriter
from app.retrieval.repository import MemoryIndexRepository
from app.runtime.command_router import CommandRouter
from app.runtime.config import AppConfig
from app.runtime.session_manager import SessionManager
from app.storage.state_store import StateStore
from app.storage.task_store import TaskStore
from app.runtime.task_manager import TaskManager
from app.tools.delete_approval import DeleteApprovalStore
from app.tools.local_executor import LocalExecutor
from app.tools.registry import build_default_registry
from app.tools.schemas import ToolContext


class AppRuntime:
    def __init__(self, *, root: Path, config: AppConfig) -> None:
        self.root = root
        self.config = config

        data_root = (root / config.paths.data_root).resolve()
        state_store = StateStore(data_root / "runtime_state.json")
        task_store = TaskStore(data_root / "tasks.json")

        self.session_manager = SessionManager(root, config, state_store)
        self.subagent_pool = SubagentPool(max_subagents=config.runtime.max_subagents)
        self.repository = MemoryIndexRepository(data_root / "memory_index.db")
        self.client = OpenAICompatibleClient(config.default_model)
        self.summarizer = MemorySummarizer(self.client)
        self.memory_writer = MemoryWriter(config, self.summarizer)
        self.registry = build_default_registry((root / config.paths.skills_root).resolve())
        self.profile_loader = AgentProfileLoader((root / config.paths.agents_root).resolve())
        self.command_router = CommandRouter(config.commands)
        self.cli = CLIChannel()
        self.executor = LocalExecutor(config.execution)
        self.qq = QQChannel(
            config.channels.qqbot,
            download_root=(root / "Download" / "qqbot").resolve(),
        )
        self.delete_approvals = DeleteApprovalStore()

        tool_context = ToolContext(
            session_id="",
            repository=self.repository,
            session_manager=self.session_manager,
            subagent_pool=self.subagent_pool,
            skills_root=(root / config.paths.skills_root).resolve(),
            executor=self.executor,
            delete_approvals=self.delete_approvals,
        )
        self.subagent_runner = SubagentRunner(
            client=self.client,
            registry=self.registry,
            tool_context=tool_context,
            subagent_pool=self.subagent_pool,
            memory_writer=self.memory_writer,
            profile_loader=self.profile_loader,
            max_loops=config.runtime.subagent_max_loops,
        )
        tool_context.subagent_runner = self.subagent_runner
        self.main_agent = MainAgent(
            client=self.client,
            registry=self.registry,
            tool_context=tool_context,
            profile_loader=self.profile_loader,
            default_max_loops=config.runtime.main_agent_max_loops,
        )
        self.task_manager = TaskManager(
            main_agent=self.main_agent,
            task_store=task_store,
            loop_budget=config.runtime.task_loop_budget,
            max_runtime_seconds=config.runtime.task_max_runtime_seconds,
        )
        self._running = True
        self._response_lock = asyncio.Lock()

    async def run(self) -> None:
        session = self.session_manager.ensure_session()
        self.main_agent.tool_context.session_id = session.session_id
        await self.qq.start(self._handle_channel_message)
        try:
            await self.cli.send_text(
                f"{self.config.app_name} ready. Active session: {session.session_id}"
            )

            while self._running:
                raw = await self.cli.prompt()
                if not raw.strip():
                    continue

                action, payload = self.command_router.route(raw)
                try:
                    if action == "chat":
                        await self._handle_chat(payload)
                    elif action == "new":
                        await self._handle_new()
                    elif action == "agents":
                        await self._handle_agents()
                    elif action == "remember":
                        await self._handle_remember(payload)
                    elif action == "callmemory":
                        await self._handle_callmemory(payload)
                    elif action == "kill_subagents":
                        await self._handle_kill_subagents()
                    elif action == "tasks":
                        await self._handle_tasks()
                    elif action == "task_run":
                        await self._handle_task_run(payload)
                    elif action == "task_show":
                        await self._handle_task_show(payload)
                    elif action == "task_resume":
                        await self._handle_task_resume(payload)
                    elif action == "memory_search":
                        await self._handle_memory_search(payload)
                    elif action == "submemory_search":
                        await self._handle_submemory_search(payload)
                    elif action == "confirm_delete":
                        await self._handle_confirm_delete(payload)
                    elif action == "exit":
                        await self._handle_exit()
                except Exception as exc:
                    await self.cli.send_text(f"Error: {exc}")
        finally:
            await self.qq.stop()

    async def _handle_chat(self, user_text: str) -> None:
        assistant_text = await self._generate_and_store_reply(
            user_text=user_text,
            source="cli",
            memory_type="conversation",
        )
        await self.cli.send_text(assistant_text)

    async def _handle_new(self) -> None:
        killed = self.subagent_pool.kill_all()
        self.session_manager.rotate_session()
        self.main_agent.tool_context.session_id = self.session_manager.current.session_id
        await self.cli.send_text(
            f"Created new session: {self.session_manager.current.session_id} "
            f"(killed {killed} subagents)"
        )

    async def _handle_agents(self) -> None:
        states = self.subagent_pool.list_states()
        if not states:
            await self.cli.send_text("No active subagents in this session.")
            return
        lines = []
        for state in states:
            lines.append(
                f"{state.subagent_id} | status={state.status} | "
                f"task={state.task_prompt or '-'} | persona={state.persona_prompt or '-'}"
            )
        await self.cli.send_text("\n".join(lines))

    async def _handle_kill_subagents(self) -> None:
        killed = self.subagent_pool.kill_all()
        await self.cli.send_text(f"Killed {killed} subagents.")

    async def _handle_remember(self, payload: str) -> None:
        if not payload.strip():
            await self.cli.send_text("Usage: /remember-<content>")
            return
        async with self._response_lock:
            session = self.session_manager.ensure_session()
            saved = self.memory_writer.update_conversation_summary(
                memory_path=session.memory_path,
                session_id=session.session_id,
                remember_text=payload.strip(),
            )
            self.repository.index_saved_memory(saved)
        await self.cli.send_text("Updated conversation summary from your remember request.")

    async def _handle_callmemory(self, payload: str) -> None:
        if not payload.strip():
            await self.cli.send_text("Usage: /callmemory-<query>")
            return
        results = self.repository.search_memory(
            query=payload.strip(),
            top_k=self.config.retrieval.top_k,
        )
        await self.cli.send_text(format_search_results(results))

    async def _handle_tasks(self) -> None:
        tasks = self.task_manager.list_tasks()
        if not tasks:
            await self.cli.send_text("No persisted tasks found.")
            return
        lines = []
        for item in tasks[:20]:
            loop_budget = item.loop_budget if item.loop_budget is not None else "unlimited"
            lines.append(
                f"{item.task_id} | status={item.status} | loops={item.loops_completed}/{loop_budget} | "
                f"updated={item.updated_at} | prompt={item.prompt[:80]}"
            )
        await self.cli.send_text("\n".join(lines))

    async def _handle_task_run(self, prompt: str) -> None:
        if not prompt.strip():
            await self.cli.send_text("Usage: /task run <prompt>")
            return
        async with self._response_lock:
            session = self.session_manager.ensure_session()
            record = await self.task_manager.run_new_task(
                session_id=session.session_id,
                prompt=prompt,
                history=list(session.messages),
            )
            assistant_text = self._format_task_record(record)
            saved = await self._store_interaction(
                user_text=f"/task run {prompt}",
                assistant_text=assistant_text,
                source="cli",
                memory_type="task_result",
            )
            self.task_manager.bind_memory(
                task_id=record.task_id,
                memory_file_id=saved.file_id,
                memory_path=str(saved.path),
            )
        await self.cli.send_text(assistant_text)

    async def _handle_task_show(self, task_id: str) -> None:
        if not task_id.strip():
            await self.cli.send_text("Usage: /task show <task_id>")
            return
        record = self.task_manager.get_task(task_id.strip())
        if record is None:
            await self.cli.send_text(f"Unknown task: {task_id.strip()}")
            return
        await self.cli.send_text(self._format_task_record(record, detailed=True))

    async def _handle_task_resume(self, task_id: str) -> None:
        if not task_id.strip():
            await self.cli.send_text("Usage: /task resume <task_id>")
            return
        async with self._response_lock:
            record = await self.task_manager.resume_task(task_id=task_id.strip())
            assistant_text = self._format_task_record(record)
            saved = await self._store_interaction(
                user_text=f"/task resume {task_id.strip()}",
                assistant_text=assistant_text,
                source="cli",
                memory_type="task_result",
            )
            self.task_manager.bind_memory(
                task_id=record.task_id,
                memory_file_id=saved.file_id,
                memory_path=str(saved.path),
            )
        await self.cli.send_text(assistant_text)

    async def _handle_memory_search(self, query: str) -> None:
        results = self.repository.search_memory(
            query=query,
            top_k=self.config.retrieval.top_k,
        )
        await self.cli.send_text(format_search_results(results))

    async def _handle_submemory_search(self, query: str) -> None:
        results = self.repository.search_submemory(
            query=query,
            top_k=self.config.retrieval.top_k,
        )
        await self.cli.send_text(format_search_results(results))

    async def _handle_exit(self) -> None:
        self._running = False
        await self.cli.send_text("Exiting YClaw.")

    async def _handle_confirm_delete(self, payload: str) -> None:
        if not payload.strip():
            await self.cli.send_text("Usage: /confirm delete <path>")
            return
        approved = self.delete_approvals.approve(Path(payload.strip()))
        await self.cli.send_text(f"Delete approved for next use: {approved}")

    async def _handle_channel_message(self, message: ChannelMessage) -> str | None:
        stripped = message.text.strip()
        if stripped == self.config.commands.new:
            async with self._response_lock:
                killed = self.subagent_pool.kill_all()
                session = self.session_manager.rotate_session()
                self.main_agent.tool_context.session_id = session.session_id
            return (
                f"Created new session: {session.session_id} "
                f"(killed {killed} subagents)"
            )
        if stripped.startswith(self.config.commands.remember):
            remember_text = stripped[len(self.config.commands.remember) :].strip()
            if not remember_text:
                return "Usage: /remember-<content>"
            async with self._response_lock:
                session = self.session_manager.ensure_session()
                saved = self.memory_writer.update_conversation_summary(
                    memory_path=session.memory_path,
                    session_id=session.session_id,
                    remember_text=remember_text,
                )
                self.repository.index_saved_memory(saved)
            return "Updated conversation summary from your remember request."
        if stripped.startswith(self.config.commands.callmemory):
            query = stripped[len(self.config.commands.callmemory) :].strip()
            if not query:
                return "Usage: /callmemory-<query>"
            results = self.repository.search_memory(
                query=query,
                top_k=self.config.retrieval.top_k,
            )
            return format_search_results(results)
        if not message.text.strip():
            if not message.downloaded_files:
                return None
            user_text = f"[qq:{message.source_type}:{message.user_id}] 用户发送了文件。"
        else:
            user_text = f"[qq:{message.source_type}:{message.user_id}] {message.text}"
        if message.downloaded_files:
            attachment_lines = "\n".join(f"- {path}" for path in message.downloaded_files)
            user_text = f"{user_text}\n\n[qq_attachments]\n{attachment_lines}"
        return await self._generate_and_store_reply(
            user_text=user_text,
            source="qq",
            memory_type="conversation",
            extra_meta={
                "qq_source_type": message.source_type,
                "qq_user_id": message.user_id,
                "qq_target_id": message.target_id,
                "qq_message_id": message.message_id,
                "qq_attachment_count": str(len(message.attachments)),
            },
        )

    async def _generate_and_store_reply(
        self,
        *,
        user_text: str,
        source: str,
        memory_type: str,
        extra_meta: dict[str, str] | None = None,
    ) -> str:
        async with self._response_lock:
            session = self.session_manager.ensure_session()
            assistant_text = await self.main_agent.respond(session.messages, user_text)
            await self._store_interaction(
                user_text=user_text,
                assistant_text=assistant_text,
                source=source,
                memory_type=memory_type,
                extra_meta=extra_meta,
            )
            return assistant_text

    async def _store_interaction(
        self,
        *,
        user_text: str,
        assistant_text: str,
        source: str,
        memory_type: str,
        extra_meta: dict[str, str] | None = None,
    ):
        session = self.session_manager.ensure_session()
        self.session_manager.append_message("user", user_text)
        self.session_manager.append_message("assistant", assistant_text)
        saved = await self.memory_writer.write_conversation(
            memory_path=session.memory_path,
            session_id=session.session_id,
            session_created_at=session.created_at,
            source=source,
            memory_type=memory_type,
            user_text=user_text,
            assistant_text=assistant_text,
            extra_meta=extra_meta,
        )
        self.repository.index_saved_memory(saved)
        return saved

    @staticmethod
    def _format_task_record(record, detailed: bool = False) -> str:
        lines = [
            f"task_id: {record.task_id}",
            f"status: {record.status}",
            f"loops: {record.loops_completed}/{record.loop_budget if record.loop_budget is not None else 'unlimited'}",
            f"stop_reason: {record.stop_reason or '-'}",
            f"created_at: {record.created_at}",
            f"updated_at: {record.updated_at}",
            f"prompt: {record.prompt}",
        ]
        if record.last_error:
            lines.append(f"last_error: {record.last_error}")
        if record.memory_file_id:
            lines.append(f"memory_file_id: {record.memory_file_id}")
        if record.memory_path:
            lines.append(f"memory_path: {record.memory_path}")
        if detailed and record.result:
            lines.append("result:")
            lines.append(record.result)
        elif record.result:
            preview = record.result if len(record.result) <= 400 else f"{record.result[:400]}..."
            lines.append(f"result_preview: {preview}")
        return "\n".join(lines)


def format_search_results(results: list) -> str:
    if not results:
        return "No matching memory files found."
    lines = []
    for item in results:
        lines.append(
            f"- {item.file_id}\n"
            f"  time: {item.created_at}\n"
            f"  summary: {item.summary_text}\n"
            f"  path: {item.path}"
        )
    return "\n".join(lines)
