from __future__ import annotations

from datetime import datetime

from app.agent.main_agent import AgentRunResult, MainAgent
from app.storage.task_store import TaskRecord, TaskStore


class TaskManager:
    def __init__(
        self,
        *,
        main_agent: MainAgent,
        task_store: TaskStore,
        loop_budget: int,
        max_runtime_seconds: int,
    ) -> None:
        self.main_agent = main_agent
        self.task_store = task_store
        self.loop_budget = loop_budget
        self.max_runtime_seconds = max_runtime_seconds

    def list_tasks(self) -> list[TaskRecord]:
        return self.task_store.list_records()

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.task_store.get(task_id)

    async def run_new_task(self, *, session_id: str, prompt: str, history: list[dict]) -> TaskRecord:
        record = self.task_store.create(
            session_id=session_id,
            prompt=prompt,
            loop_budget=self.loop_budget,
            max_runtime_seconds=self.max_runtime_seconds,
        )
        return await self._execute_record(record, history=history, user_input=prompt)

    async def resume_task(self, *, task_id: str) -> TaskRecord:
        record = self.task_store.get(task_id)
        if record is None:
            raise RuntimeError(f"Unknown task: {task_id}")
        if record.status == "completed":
            return record
        return await self._execute_record(record, history=None, user_input=None)

    def bind_memory(self, *, task_id: str, memory_file_id: str, memory_path: str) -> None:
        record = self.get_task(task_id)
        if record is None:
            return
        record.memory_file_id = memory_file_id
        record.memory_path = memory_path
        record.updated_at = _now_str()
        self.task_store.save(record)

    async def _execute_record(
        self,
        record: TaskRecord,
        *,
        history: list[dict] | None,
        user_input: str | None,
    ) -> TaskRecord:
        remaining_loops = max(record.loop_budget - record.loops_completed, 0)
        if remaining_loops <= 0:
            record.status = "paused"
            record.stop_reason = "loop_budget_exhausted"
            record.updated_at = _now_str()
            self.task_store.save(record)
            return record

        base_loops = record.loops_completed
        record.status = "running"
        record.started_at = record.started_at or _now_str()
        record.updated_at = _now_str()
        record.last_error = None
        self.task_store.save(record)

        async def checkpoint(messages: list[dict], loops_used: int, stop_reason: str) -> None:
            record.checkpoint_messages = [dict(message) for message in messages]
            record.loops_completed = base_loops + loops_used
            record.stop_reason = stop_reason
            record.updated_at = _now_str()
            self.task_store.save(record)

        try:
            result = await self.main_agent.execute(
                history=history,
                user_input=user_input,
                max_loops=remaining_loops,
                max_runtime_seconds=record.max_runtime_seconds,
                initial_messages=record.checkpoint_messages or None,
                checkpoint_handler=checkpoint,
            )
        except Exception as exc:
            record.status = "failed"
            record.last_error = str(exc)
            record.updated_at = _now_str()
            self.task_store.save(record)
            raise

        self._apply_result(record, base_loops, result)
        self.task_store.save(record)
        return record

    @staticmethod
    def _apply_result(record: TaskRecord, base_loops: int, result: AgentRunResult) -> None:
        record.checkpoint_messages = [dict(message) for message in result.messages]
        record.loops_completed = base_loops + result.loops_used
        record.result = result.text or record.result
        record.stop_reason = result.stop_reason
        record.updated_at = _now_str()
        if result.status == "completed":
            record.status = "completed"
            record.completed_at = _now_str()
            return
        if result.status == "paused":
            record.status = "paused"
            return
        record.status = "failed"


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
