from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.runtime.app_runtime import AppRuntime
from app.runtime.channel_cli import run_channel_command
from app.runtime.config import AppConfig


async def _main() -> None:
    root = Path(__file__).resolve().parent
    if len(sys.argv) > 1:
        exit_code = await run_channel_command(root=root, argv=sys.argv[1:])
        raise SystemExit(exit_code)
    config = AppConfig.load(root)
    runtime = AppRuntime(root=root, config=config)
    await runtime.run()


if __name__ == "__main__":
    asyncio.run(_main())
