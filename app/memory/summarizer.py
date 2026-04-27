from __future__ import annotations

from app.llm.client import OpenAICompatibleClient


class MemorySummarizer:
    def __init__(self, client: OpenAICompatibleClient) -> None:
        self.client = client

    async def summarize(self, text: str) -> str:
        return await self.client.summarize_markdown(text)
