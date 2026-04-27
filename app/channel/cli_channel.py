from __future__ import annotations

import asyncio
import sys


class CLIChannel:
    async def prompt(self, text: str = "YClaw> ") -> str:
        return await asyncio.to_thread(input, text)

    async def send_text(self, content: str) -> None:
        await asyncio.to_thread(_safe_print, content)


def _safe_print(content: str) -> None:
    try:
        print(content)
    except UnicodeEncodeError:
        fallback = content.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8",
            errors="replace",
        )
        print(fallback)
