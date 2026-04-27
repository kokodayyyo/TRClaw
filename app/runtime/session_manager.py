from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.runtime.config import AppConfig
from app.storage.state_store import StateStore


@dataclass(slots=True)
class SessionState:
    session_id: str
    created_at: str
    memory_path: Path
    submemory_dir: Path
    messages: list[dict] = field(default_factory=list)
    active: bool = True


class SessionManager:
    def __init__(self, root: Path, config: AppConfig, state_store: StateStore) -> None:
        self.root = root
        self.config = config
        self.state_store = state_store
        self._current: SessionState | None = None

    @property
    def current(self) -> SessionState:
        if self._current is None:
            raise RuntimeError("No active session")
        return self._current

    def ensure_session(self) -> SessionState:
        if self._current is not None:
            return self._current

        payload = self.state_store.load()
        session_id = payload.get("current_session_id")
        if session_id:
            memory_path = self._memory_root / f"{session_id}.md"
            submemory_dir = self._submemory_root / session_id
            if memory_path.exists() and submemory_dir.exists():
                self._current = SessionState(
                    session_id=session_id,
                    created_at=payload.get("created_at", _now_str()),
                    memory_path=memory_path,
                    submemory_dir=submemory_dir,
                )
                return self._current

        self._current = self.create_session()
        return self._current

    def create_session(self) -> SessionState:
        session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        memory_path = self._memory_root / f"{session_id}.md"
        submemory_dir = self._submemory_root / session_id
        self._memory_root.mkdir(parents=True, exist_ok=True)
        submemory_dir.mkdir(parents=True, exist_ok=True)

        state = SessionState(
            session_id=session_id,
            created_at=_now_str(),
            memory_path=memory_path,
            submemory_dir=submemory_dir,
        )
        self._current = state
        self._persist()
        return state

    def rotate_session(self) -> SessionState:
        if self._current is not None:
            self._current.active = False
        return self.create_session()

    def append_message(self, role: str, content: str) -> None:
        self.current.messages.append({"role": role, "content": content})

    def reset_messages(self) -> None:
        self.current.messages.clear()

    def _persist(self) -> None:
        session = self.current
        self.state_store.save(
            {
                "current_session_id": session.session_id,
                "created_at": session.created_at,
            }
        )

    @property
    def _memory_root(self) -> Path:
        path = (self.root / self.config.paths.memory_root).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def _submemory_root(self) -> Path:
        path = (self.root / self.config.paths.submemory_root).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
