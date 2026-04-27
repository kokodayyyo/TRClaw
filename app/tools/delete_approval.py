from __future__ import annotations

from pathlib import Path


class DeleteApprovalStore:
    def __init__(self) -> None:
        self._approved_paths: set[str] = set()

    def approve(self, path: Path) -> str:
        normalized = str(path.resolve(strict=False))
        self._approved_paths.add(normalized)
        return normalized

    def is_approved(self, path: Path) -> bool:
        normalized = str(path.resolve(strict=False))
        return normalized in self._approved_paths

    def consume(self, path: Path) -> bool:
        normalized = str(path.resolve(strict=False))
        if normalized not in self._approved_paths:
            return False
        self._approved_paths.remove(normalized)
        return True
