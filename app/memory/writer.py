from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.memory.reader import extract_summary, read_text
from app.memory.summarizer import MemorySummarizer
from app.runtime.config import AppConfig


@dataclass(slots=True)
class SavedMemory:
    file_id: str
    path: Path
    scope: str
    session_id: str
    subagent_id: str | None
    created_at: str
    summary_text: str


class MemoryWriter:
    def __init__(self, config: AppConfig, summarizer: MemorySummarizer) -> None:
        self.config = config
        self.summarizer = summarizer

    async def write_conversation(
        self,
        *,
        memory_path: Path,
        session_id: str,
        session_created_at: str,
        source: str,
        memory_type: str,
        user_text: str,
        assistant_text: str,
        extra_meta: dict[str, str] | None = None,
    ) -> SavedMemory:
        created_at = _now_str()
        memory_path.parent.mkdir(parents=True, exist_ok=True)

        existing_body = ""
        existing_summary = ""
        if memory_path.exists():
            existing_text = read_text(memory_path)
            existing_summary = extract_summary(existing_text)
            existing_body = _strip_summary_block(existing_text).rstrip()
        else:
            existing_body = (
                "# Meta\n"
                f"- session_id: {session_id}\n"
                f"- session_started_at: {session_created_at}\n\n"
                "# Conversation\n"
            )

        turn_meta_lines = [
            f"- created_at: {created_at}",
            f"- source: {source}",
            f"- type: {memory_type}",
        ]
        for key, value in (extra_meta or {}).items():
            turn_meta_lines.append(f"- {key}: {value}")

        turn_block = (
            f"\n\n## Turn {created_at}\n"
            "### Meta\n"
            f"{chr(10).join(turn_meta_lines)}\n\n"
            "### User\n"
            f"{user_text.strip()}\n\n"
            "### Assistant\n"
            f"{assistant_text.strip()}\n"
        )

        body = f"{existing_body}{turn_block}".strip() + "\n"
        summary = _normalize_summary(existing_summary)
        final_text = _compose_memory_text(summary, body)
        memory_path.write_text(final_text, encoding="utf-8")

        return SavedMemory(
            file_id=f"memory:{session_id}.md",
            path=memory_path,
            scope="memory",
            session_id=session_id,
            subagent_id=None,
            created_at=created_at,
            summary_text=summary.strip(),
        )

    def update_conversation_summary(
        self,
        *,
        memory_path: Path,
        session_id: str,
        remember_text: str,
    ) -> SavedMemory:
        created_at = _now_str()
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        if memory_path.exists():
            existing_text = read_text(memory_path)
            body = _strip_summary_block(existing_text).rstrip()
        else:
            body = (
                "# Meta\n"
                f"- session_id: {session_id}\n\n"
                "# Conversation\n"
            )
        summary = _normalize_summary(remember_text)
        final_text = _compose_memory_text(summary, body.strip() + "\n")
        memory_path.write_text(final_text, encoding="utf-8")
        return SavedMemory(
            file_id=f"memory:{session_id}.md",
            path=memory_path,
            scope="memory",
            session_id=session_id,
            subagent_id=None,
            created_at=created_at,
            summary_text=summary,
        )

    async def write_submemory(
        self,
        *,
        submemory_dir: Path,
        session_id: str,
        subagent_id: str,
        task: str,
        persona: str,
        content: str,
    ) -> SavedMemory:
        created_at = _now_str()
        agent_dir = submemory_dir / subagent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{datetime.now().strftime(self.config.memory.file_name_format)}.md"
        path = agent_dir / file_name

        body = (
            "# Meta\n"
            f"- created_at: {created_at}\n"
            f"- session_id: {session_id}\n"
            f"- subagent_id: {subagent_id}\n"
            f"- task: {task.strip()}\n"
            f"- persona: {persona.strip()}\n\n"
            "# Content\n"
            f"{content.strip()}\n"
        )
        path.write_text(body, encoding="utf-8")

        full_text = read_text(path)
        summary = _normalize_summary(await self.summarizer.summarize(full_text))
        final_text = f"# Summary\n{summary.strip()}\n\n{full_text}"
        path.write_text(final_text, encoding="utf-8")

        return SavedMemory(
            file_id=f"submemory:{session_id}:{subagent_id}:{file_name}",
            path=path,
            scope="submemory",
            session_id=session_id,
            subagent_id=subagent_id,
            created_at=created_at,
            summary_text=summary.strip(),
        )


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_summary(summary: str) -> str:
    cleaned = summary.strip().strip("*").strip()
    lowered = cleaned.lower()
    if lowered.startswith("# summary"):
        cleaned = cleaned[len("# summary") :].strip()
    elif lowered.startswith("summary:"):
        cleaned = cleaned[len("summary:") :].strip()
    return cleaned


def _strip_summary_block(text: str) -> str:
    if not text.startswith("# Summary"):
        return text
    marker = "\n# "
    next_heading = text.find(marker, len("# Summary"))
    if next_heading < 0:
        return ""
    return text[next_heading + 1 :]


def _compose_memory_text(summary: str, body: str) -> str:
    if summary.strip():
        return f"# Summary\n{summary.strip()}\n\n{body}"
    return body
