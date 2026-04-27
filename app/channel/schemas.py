from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ChannelMessage:
    channel: str
    source_type: str
    user_id: str
    target_id: str
    text: str
    message_id: str
    raw_event: dict[str, Any]
    attachments: list[dict[str, Any]]
    downloaded_files: list[str]


@dataclass(slots=True)
class QQCheckResult:
    ok: bool
    message: str
    gateway_url: str = ""
