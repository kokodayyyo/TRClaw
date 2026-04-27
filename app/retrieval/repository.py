from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import unicodedata

from app.memory.reader import extract_summary, read_text
from app.memory.writer import SavedMemory


@dataclass(slots=True)
class SearchResult:
    file_id: str
    file_name: str
    created_at: str
    scope: str
    session_id: str
    subagent_id: str | None
    summary_text: str
    path: str
    score: float


class MemoryIndexRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.json_path = db_path.with_suffix(".json")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite_enabled = True
        self._fts_enabled = True
        try:
            self._init_db()
        except sqlite3.Error:
            self._sqlite_enabled = False
            self._fts_enabled = False
            self._ensure_json_index()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    file_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    subagent_id TEXT,
                    file_name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    summary_text TEXT NOT NULL
                )
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
                    USING fts5(
                        file_id UNINDEXED,
                        file_name,
                        summary_text,
                        tokenize='unicode61'
                    )
                    """
                )
            except sqlite3.OperationalError:
                self._fts_enabled = False
            connection.commit()

    def index_saved_memory(self, saved: SavedMemory) -> None:
        if not self._sqlite_enabled:
            self._json_upsert(saved)
            return
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM documents WHERE file_id = ?", (saved.file_id,))
                if self._fts_enabled:
                    connection.execute("DELETE FROM documents_fts WHERE file_id = ?", (saved.file_id,))
                connection.execute(
                    """
                    INSERT INTO documents (
                        file_id, scope, session_id, subagent_id, file_name, path, created_at, summary_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        saved.file_id,
                        saved.scope,
                        saved.session_id,
                        saved.subagent_id,
                        saved.path.name,
                        str(saved.path),
                        saved.created_at,
                        saved.summary_text,
                    ),
                )
                if self._fts_enabled:
                    connection.execute(
                        """
                        INSERT INTO documents_fts (file_id, file_name, summary_text)
                        VALUES (?, ?, ?)
                        """,
                        (saved.file_id, saved.path.name, saved.summary_text),
                    )
                connection.commit()
        except sqlite3.Error:
            self._switch_to_json()
            self._json_upsert(saved)

    def reindex_markdown_file(
        self,
        *,
        file_id: str,
        scope: str,
        session_id: str,
        path: Path,
        subagent_id: str | None = None,
    ) -> None:
        markdown = read_text(path)
        summary = extract_summary(markdown)
        created_at = ""
        for line in markdown.splitlines():
            if line.startswith("- created_at:"):
                created_at = line.split(":", 1)[1].strip()
                break
        saved = SavedMemory(
            file_id=file_id,
            path=path,
            scope=scope,
            session_id=session_id,
            subagent_id=subagent_id,
            created_at=created_at,
            summary_text=summary,
        )
        self.index_saved_memory(saved)

    def get_document(self, file_id: str) -> dict | None:
        if not self._sqlite_enabled:
            return self._load_json_index().get(file_id)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT file_id, scope, session_id, subagent_id, file_name, path, created_at, summary_text
                    FROM documents
                    WHERE file_id = ?
                    """,
                    (file_id,),
                ).fetchone()
        except sqlite3.Error:
            self._switch_to_json()
            return self._load_json_index().get(file_id)
        return dict(row) if row else None

    def search_memory(self, query: str, top_k: int = 5) -> list[SearchResult]:
        return self._search(query=query, scope="memory", top_k=top_k)

    def search_submemory(
        self,
        query: str,
        top_k: int = 5,
        subagent_id: str | None = None,
    ) -> list[SearchResult]:
        return self._search(
            query=query,
            scope="submemory",
            top_k=top_k,
            subagent_id=subagent_id,
        )

    def _search(
        self,
        *,
        query: str,
        scope: str,
        top_k: int,
        subagent_id: str | None = None,
    ) -> list[SearchResult]:
        candidates = self._load_candidates(scope=scope, subagent_id=subagent_id)
        if not candidates:
            return []
        return self._rank_candidates(
            candidates=candidates,
            query=query,
            top_k=top_k,
        )

    def _switch_to_json(self) -> None:
        self._sqlite_enabled = False
        self._fts_enabled = False
        self._ensure_json_index()

    def _ensure_json_index(self) -> None:
        if self.json_path.exists():
            return
        self._save_json_index({})

    def _load_json_index(self) -> dict[str, dict]:
        self._ensure_json_index()
        with self.json_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save_json_index(self, payload: dict[str, dict]) -> None:
        with self.json_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _json_upsert(self, saved: SavedMemory) -> None:
        payload = self._load_json_index()
        payload[saved.file_id] = {
            "file_id": saved.file_id,
            "scope": saved.scope,
            "session_id": saved.session_id,
            "subagent_id": saved.subagent_id,
            "file_name": saved.path.name,
            "path": str(saved.path),
            "created_at": saved.created_at,
            "summary_text": saved.summary_text,
        }
        self._save_json_index(payload)

    def _json_search(
        self,
        *,
        query: str,
        scope: str,
        top_k: int,
        subagent_id: str | None = None,
    ) -> list[SearchResult]:
        payload = self._load_json_index()
        candidates = [
            dict(record)
            for record in payload.values()
            if record["scope"] == scope and (not subagent_id or record.get("subagent_id") == subagent_id)
        ]
        return self._rank_candidates(candidates=candidates, query=query, top_k=top_k)

    def _load_candidates(self, *, scope: str, subagent_id: str | None) -> list[dict]:
        if not self._sqlite_enabled:
            payload = self._load_json_index()
            return [
                dict(record)
                for record in payload.values()
                if record["scope"] == scope and (not subagent_id or record.get("subagent_id") == subagent_id)
            ]
        try:
            with self._connect() as connection:
                params: list[object] = [scope]
                where = ["scope = ?"]
                if subagent_id:
                    where.append("subagent_id = ?")
                    params.append(subagent_id)
                sql = f"""
                    SELECT
                        file_id,
                        file_name,
                        created_at,
                        scope,
                        session_id,
                        subagent_id,
                        summary_text,
                        path,
                        0.0 AS score
                    FROM documents
                    WHERE {' AND '.join(where)}
                """
                rows = connection.execute(sql, params).fetchall()
        except sqlite3.Error:
            self._switch_to_json()
            payload = self._load_json_index()
            return [
                dict(record)
                for record in payload.values()
                if record["scope"] == scope and (not subagent_id or record.get("subagent_id") == subagent_id)
            ]
        return [dict(row) for row in rows]

    def _rank_candidates(self, *, candidates: list[dict], query: str, top_k: int) -> list[SearchResult]:
        prepared = _prepare_query(query)
        if not prepared["normalized"]:
            ranked = [SearchResult(score=float(candidate.get("score", 0.0)), **candidate) for candidate in candidates]
            ranked.sort(key=lambda item: item.created_at, reverse=True)
            return ranked[:top_k]

        rows: list[SearchResult] = []
        for candidate in candidates:
            score = _score_candidate(candidate, prepared)
            if score <= 0:
                continue
            rows.append(SearchResult(score=score, **candidate))

        rows.sort(
            key=lambda item: (
                item.score,
                _safe_datetime(item.created_at),
                len(item.summary_text),
            ),
            reverse=True,
        )
        return rows[:top_k]


SYNONYM_GROUPS = [
    {"qq", "qqbot", "qq机器人", "qq bot", "qq channel"},
    {"记忆", "记住", "回忆", "recall", "memory"},
    {"子代理", "subagent", "agent", "子agent"},
    {"终端", "命令行", "shell", "terminal", "powershell", "bash"},
    {"python", "py"},
]

ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9._:/-]*")
CJK_BLOCK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")


def _prepare_query(query: str) -> dict[str, object]:
    normalized = _normalize_text(query)
    tokens = _tokenize(normalized)
    expanded_tokens = _expand_tokens(tokens)
    phrases = _query_phrases(normalized)
    return {
        "normalized": normalized,
        "tokens": tokens,
        "expanded_tokens": expanded_tokens,
        "phrases": phrases,
        "char_ngrams": _char_ngrams(normalized, size=2),
    }


def _score_candidate(candidate: dict, prepared: dict[str, object]) -> float:
    summary_norm = _normalize_text(str(candidate.get("summary_text", "")))
    file_norm = _normalize_text(str(candidate.get("file_name", "")))
    path_norm = _normalize_text(str(candidate.get("path", "")))
    if not summary_norm and not file_norm and not path_norm:
        return 0.0

    score = 0.0
    normalized_query = str(prepared["normalized"])
    phrases = list(prepared["phrases"])
    tokens = list(prepared["tokens"])
    expanded_tokens = list(prepared["expanded_tokens"])
    query_ngrams = set(prepared["char_ngrams"])

    if normalized_query and normalized_query in summary_norm:
        score += 120.0
    if normalized_query and (normalized_query in file_norm or normalized_query in path_norm):
        score += 80.0

    for phrase in phrases:
        if phrase in summary_norm:
            score += 36.0 + min(len(phrase), 12)
        if phrase in file_norm or phrase in path_norm:
            score += 22.0 + min(len(phrase), 10)

    matched_core_tokens = 0
    for token in tokens:
        if token in summary_norm:
            matched_core_tokens += 1
            score += 18.0 + min(len(token), 8) * 1.5
        elif token in file_norm or token in path_norm:
            matched_core_tokens += 1
            score += 12.0 + min(len(token), 8)

    matched_expanded_tokens = 0
    for token in expanded_tokens:
        if token in tokens:
            continue
        if token in summary_norm:
            matched_expanded_tokens += 1
            score += 8.0 + min(len(token), 6)
        elif token in file_norm or token in path_norm:
            matched_expanded_tokens += 1
            score += 5.0 + min(len(token), 6) * 0.8

    if tokens:
        coverage = matched_core_tokens / max(len(tokens), 1)
        score += coverage * 35.0
        if matched_core_tokens == len(tokens):
            score += 30.0
        elif matched_core_tokens >= max(1, len(tokens) - 1):
            score += 12.0

    if query_ngrams and summary_norm:
        summary_ngrams = _char_ngrams(summary_norm, size=2)
        overlap = len(query_ngrams & summary_ngrams)
        if overlap:
            score += min(overlap * 4.0, 28.0)

    score += _recency_bonus(str(candidate.get("created_at", "")))
    return score


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = normalized.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    normalized = re.sub(r"[“”\"'`]+", " ", normalized)
    normalized = re.sub(r"[\-_/\\|,;:!?()[\]{}<>@#$%^&*=+~]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _tokenize(normalized: str) -> list[str]:
    if not normalized:
        return []
    tokens: list[str] = []
    seen: set[str] = set()

    for token in ASCII_TOKEN_PATTERN.findall(normalized):
        _push_token(tokens, seen, token)

    for block in CJK_BLOCK_PATTERN.findall(normalized):
        if len(block) <= 4:
            _push_token(tokens, seen, block)
        for size in (2, 3):
            for index in range(len(block) - size + 1):
                _push_token(tokens, seen, block[index : index + size])
        for char in block:
            _push_token(tokens, seen, char)

    for token in normalized.split():
        _push_token(tokens, seen, token)
    return tokens


def _expand_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    seen = set(tokens)
    for token in tokens:
        expanded.append(token)
        for group in SYNONYM_GROUPS:
            if token in group:
                for alias in group:
                    if alias not in seen:
                        expanded.append(alias)
                        seen.add(alias)
    return expanded


def _query_phrases(normalized: str) -> list[str]:
    phrases = [normalized] if normalized else []
    parts = [part for part in normalized.split(" ") if part]
    if len(parts) >= 2:
        phrases.extend([" ".join(parts[:2]), " ".join(parts[-2:])])
    return list(dict.fromkeys([part for part in phrases if len(part) >= 2]))


def _char_ngrams(text: str, *, size: int) -> set[str]:
    compact = text.replace(" ", "")
    if len(compact) < size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _push_token(tokens: list[str], seen: set[str], token: str) -> None:
    cleaned = token.strip()
    if not cleaned or cleaned in seen:
        return
    if len(cleaned) == 1 and not _is_cjk(cleaned):
        return
    seen.add(cleaned)
    tokens.append(cleaned)


def _is_cjk(text: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in text)


def _recency_bonus(created_at: str) -> float:
    dt = _safe_datetime(created_at)
    if dt is None:
        return 0.0
    age_days = max((datetime.now() - dt).days, 0)
    return max(12.0 - min(age_days, 12), 0.0)


def _safe_datetime(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.min
