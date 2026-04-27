from __future__ import annotations

import re
from pathlib import Path


SUMMARY_PATTERN = re.compile(r"^# Summary\s*(.*?)\s*(?:^# |\Z)", re.DOTALL | re.MULTILINE)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_summary(markdown: str) -> str:
    match = SUMMARY_PATTERN.search(markdown)
    if not match:
        return ""
    return match.group(1).strip()
