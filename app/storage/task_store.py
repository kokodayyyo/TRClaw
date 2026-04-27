from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    session_id: str
    prompt: str
    status: str
    created_at: str
    updated_at: str
    loop_budget: int | None
    loops_completed: int = 0
    max_runtime_seconds: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str | None = None
    result: str | None = None
    stop_reason: str | None = None
    checkpoint_messages: list[dict[str, Any]] = field(default_factory=list)
    memory_file_id: str | None = None
    memory_path: str | None = None


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list_records(self) -> list[TaskRecord]:
        payload = self._read_all()
        items = [self._from_dict(item) for item in payload.get("tasks", [])]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def get(self, task_id: str) -> TaskRecord | None:
        for record in self.list_records():
            if record.task_id == task_id:
                return record
        return None

    def create(
        self,
        *,
        session_id: str,
        prompt: str,
        loop_budget: int | None,
        max_runtime_seconds: int | None,
    ) -> TaskRecord:
        now = _now_str()
        record = TaskRecord(
            task_id=_task_id(),
            session_id=session_id,
            prompt=prompt,
            status="pending",
            created_at=now,
            updated_at=now,
            loop_budget=loop_budget,
            max_runtime_seconds=max_runtime_seconds,
        )
        payload = self._read_all()
        payload.setdefault("tasks", []).append(asdict(record))
        self._write_all(payload)
        return record

    def save(self, record: TaskRecord) -> None:
        payload = self._read_all()
        tasks = payload.setdefault("tasks", [])
        data = asdict(record)
        for index, item in enumerate(tasks):
            if item.get("task_id") == record.task_id:
                tasks[index] = data
                self._write_all(payload)
                return
        tasks.append(data)
        self._write_all(payload)

    def _read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"tasks": []}
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_all(self, payload: dict[str, Any]) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _from_dict(payload: dict[str, Any]) -> TaskRecord:
        return TaskRecord(**payload)


def _task_id() -> str:
    return datetime.now().strftime("task_%Y%m%d_%H%M%S_%f")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
