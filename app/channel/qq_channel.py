from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
from pathlib import Path
import time
from urllib.parse import urlparse
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from app.channel.schemas import ChannelMessage, QQCheckResult
from app.runtime.config import QQBotConfig


LOGGER = logging.getLogger(__name__)

INTENT_BITS = {
    "GUILDS": 1 << 0,
    "GUILD_MEMBERS": 1 << 1,
    "GUILD_MESSAGES": 1 << 9,
    "GUILD_MESSAGE_REACTIONS": 1 << 10,
    "DIRECT_MESSAGE": 1 << 12,
    "MESSAGE_AUDIT": 1 << 27,
    "FORUMS": 1 << 28,
    "AT_MESSAGE_CREATE": 1 << 30,
    "PUBLIC_GUILD_MESSAGES": 1 << 30,
    "AUDIO_ACTION": 1 << 29,
    "INTERACTION": 1 << 26,
    "C2C_MESSAGE_CREATE": 1 << 25,
    "GROUP_AT_MESSAGE_CREATE": 1 << 25,
}


class QQChannel:
    def __init__(self, config: QQBotConfig, *, download_root: Path | None = None) -> None:
        self.config = config
        self.enabled = config.enabled
        self.download_root = (download_root or Path.cwd() / "Download" / "qqbot").resolve()
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._runner: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._handler: Callable[[ChannelMessage], Awaitable[str | None]] | None = None
        self._access_token: str = ""
        self._access_token_expiry: float = 0
        self._sequence: int | None = None

    async def start(self, handler: Callable[[ChannelMessage], Awaitable[str | None]]) -> None:
        if not self.enabled:
            return
        self._handler = handler
        self._session = aiohttp.ClientSession()
        self._runner = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._heartbeat:
            self._heartbeat.cancel()
            self._heartbeat = None
        if self._runner:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._runner
            self._runner = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None

    async def check_connection(self) -> QQCheckResult:
        if not self.config.enabled:
            return QQCheckResult(False, "QQ Bot is disabled in config.")
        if not self.config.app_id or not self.config.client_secret:
            return QQCheckResult(False, "Missing appId or clientSecret.")
        created_here = self._session is None or self._session.closed
        try:
            token = await self._ensure_access_token()
            gateway_url = await self._fetch_gateway_url(token)
            return QQCheckResult(True, "ok", gateway_url=gateway_url)
        except Exception as exc:
            return QQCheckResult(False, str(exc))
        finally:
            if created_here and self._session:
                await self._session.close()
                self._session = None

    async def send_text(self, target: str, content: str) -> None:
        source_type, target_id = _parse_target(target)
        event = ChannelMessage(
            channel="qq",
            source_type=source_type,
            user_id="",
            target_id=target_id,
            text="",
            message_id="",
            raw_event={},
            attachments=[],
            downloaded_files=[],
        )
        await self.reply(event, content)

    async def reply(self, event: ChannelMessage, content: str) -> None:
        if not self.enabled:
            return
        token = await self._ensure_access_token()
        session = await self._ensure_session()
        url = self._message_url(event)
        payload = {"content": content}
        if event.message_id:
            payload["msg_id"] = event.message_id
        headers = {
            "Authorization": f"QQBot {token}",
            "X-Union-Appid": self.config.app_id,
            "Content-Type": "application/json",
        }
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status >= 400:
                detail = await response.text()
                raise RuntimeError(f"QQ send failed ({response.status}): {detail}")

    async def _run_loop(self) -> None:
        retry = 0
        while self.enabled and retry <= self.config.max_retry:
            try:
                token = await self._ensure_access_token()
                gateway_url = await self._fetch_gateway_url(token)
                session = await self._ensure_session()
                self._ws = await session.ws_connect(gateway_url, heartbeat=None)
                async for message in self._ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_ws_payload(json.loads(message.data), token)
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                retry += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retry += 1
                LOGGER.exception("QQ channel loop error: %s", exc)
                await asyncio.sleep(min(retry * 2, 10))

    async def _handle_ws_payload(self, payload: dict[str, Any], token: str) -> None:
        op = payload.get("op")
        data = payload.get("d") or {}
        if payload.get("s") is not None:
            self._sequence = payload["s"]

        if op == 10:
            interval_ms = int(data.get("heartbeat_interval", 30000))
            await self._identify(token)
            self._heartbeat = asyncio.create_task(self._heartbeat_loop(interval_ms))
            return
        if op != 0:
            return

        event_type = payload.get("t", "")
        normalized = self._normalize_event(event_type, data)
        if normalized and self._handler:
            normalized = await self._attach_downloads(normalized, token)
            response = await self._handler(normalized)
            if response:
                await self.reply(normalized, response)

    async def _identify(self, token: str) -> None:
        if not self._ws:
            return
        payload = {
            "op": 2,
            "d": {
                "token": f"QQBot {token}",
                "intents": _intents_to_bitmask(self.config.intents),
                "shard": [0, 1],
                "properties": {
                    "$os": "linux",
                    "$browser": "yclaw",
                    "$device": "yclaw",
                },
            },
        }
        await self._ws.send_json(payload)

    async def _heartbeat_loop(self, interval_ms: int) -> None:
        while self._ws and not self._ws.closed:
            await asyncio.sleep(interval_ms / 1000)
            await self._ws.send_json({"op": 1, "d": self._sequence})

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _ensure_access_token(self) -> str:
        if self._access_token and time.time() < self._access_token_expiry:
            return self._access_token
        if not self.config.app_id or not self.config.client_secret:
            raise RuntimeError("QQ Bot credentials are missing.")
        session = await self._ensure_session()
        payload = {"appId": self.config.app_id, "clientSecret": self.config.client_secret}
        async with session.post(
            self.config.token_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as response:
            data = await response.json()
            if response.status >= 400:
                raise RuntimeError(f"Token request failed: {data}")
        token = data.get("access_token") or data.get("accessToken")
        if not token:
            raise RuntimeError(f"Token response missing access token: {data}")
        expires_in = int(data.get("expires_in") or data.get("expiresIn") or 7200)
        self._access_token = token
        self._access_token_expiry = time.time() + max(expires_in - 60, 60)
        return self._access_token

    async def _fetch_gateway_url(self, token: str) -> str:
        session = await self._ensure_session()
        headers = {
            "Authorization": f"QQBot {token}",
            "X-Union-Appid": self.config.app_id,
        }
        url = f"{self.config.api_base_url}/gateway/bot"
        async with session.get(url, headers=headers) as response:
            data = await response.json()
            if response.status >= 400:
                raise RuntimeError(f"Gateway request failed: {data}")
        gateway_url = data.get("url")
        if not gateway_url:
            raise RuntimeError(f"Gateway response missing url: {data}")
        return gateway_url

    def _normalize_event(self, event_type: str, data: dict[str, Any]) -> ChannelMessage | None:
        content = str(data.get("content", "")).strip()
        if self.config.remove_at:
            content = _strip_qq_mentions(content).strip()

        if event_type == "C2C_MESSAGE_CREATE":
            target_id = str(data.get("author", {}).get("id", ""))
            return ChannelMessage(
                channel="qq",
                source_type="c2c",
                user_id=target_id,
                target_id=target_id,
                text=content,
                message_id=str(data.get("id", "")),
                raw_event=data,
                attachments=_extract_attachments(data),
                downloaded_files=[],
            )
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            group_openid = str(data.get("group_openid", ""))
            user_id = str(data.get("author", {}).get("member_openid", "") or data.get("author", {}).get("id", ""))
            return ChannelMessage(
                channel="qq",
                source_type="group",
                user_id=user_id,
                target_id=group_openid,
                text=content,
                message_id=str(data.get("id", "")),
                raw_event=data,
                attachments=_extract_attachments(data),
                downloaded_files=[],
            )
        if event_type == "AT_MESSAGE_CREATE":
            channel_id = str(data.get("channel_id", ""))
            user_id = str(data.get("author", {}).get("id", ""))
            return ChannelMessage(
                channel="qq",
                source_type="channel",
                user_id=user_id,
                target_id=channel_id,
                text=content,
                message_id=str(data.get("id", "")),
                raw_event=data,
                attachments=_extract_attachments(data),
                downloaded_files=[],
            )
        if event_type == "DIRECT_MESSAGE_CREATE":
            guild_id = str(data.get("guild_id", ""))
            user_id = str(data.get("author", {}).get("id", ""))
            return ChannelMessage(
                channel="qq",
                source_type="direct_message",
                user_id=user_id,
                target_id=guild_id,
                text=content,
                message_id=str(data.get("id", "")),
                raw_event=data,
                attachments=_extract_attachments(data),
                downloaded_files=[],
            )
        return None

    async def _attach_downloads(self, event: ChannelMessage, token: str) -> ChannelMessage:
        if not event.attachments:
            return event
        paths: list[str] = []
        for index, attachment in enumerate(event.attachments, start=1):
            try:
                path = await self._download_attachment(event, attachment, token, index)
            except Exception as exc:
                LOGGER.warning("Failed to download QQ attachment for %s: %s", event.message_id, exc)
                continue
            if path:
                paths.append(str(path))
        event.downloaded_files = paths
        return event

    async def _download_attachment(
        self,
        event: ChannelMessage,
        attachment: dict[str, Any],
        token: str,
        index: int,
    ) -> Path | None:
        url = str(
            attachment.get("url")
            or attachment.get("download_url")
            or attachment.get("proxy_url")
            or ""
        ).strip()
        if not url:
            return None
        if url.startswith("//"):
            url = f"https:{url}"
        elif url.startswith("/"):
            url = f"{self.config.api_base_url}{url}"

        file_name = _attachment_filename(attachment, event.message_id, index, url)
        target_dir = self.download_root
        target_dir.mkdir(parents=True, exist_ok=True)
        path = _dedupe_path(target_dir / file_name)

        session = await self._ensure_session()
        headers = {
            "Authorization": f"QQBot {token}",
            "X-Union-Appid": self.config.app_id,
        }
        async with session.get(url, headers=headers) as response:
            if response.status >= 400:
                detail = await response.text()
                raise RuntimeError(f"download failed ({response.status}): {detail}")
            with path.open("wb") as handle:
                async for chunk in response.content.iter_chunked(65536):
                    handle.write(chunk)
        return path

    def _message_url(self, event: ChannelMessage) -> str:
        base = self.config.api_base_url
        if event.source_type == "c2c":
            return f"{base}/v2/users/{event.target_id}/messages"
        if event.source_type == "group":
            return f"{base}/v2/groups/{event.target_id}/messages"
        if event.source_type == "channel":
            return f"{base}/channels/{event.target_id}/messages"
        if event.source_type == "direct_message":
            return f"{base}/dms/{event.target_id}/messages"
        raise ValueError(f"Unsupported QQ source type: {event.source_type}")


def _parse_target(target: str) -> tuple[str, str]:
    if target.startswith("qqbot:c2c:"):
        return ("c2c", target.split(":", 2)[2])
    if target.startswith("qqbot:group:"):
        return ("group", target.split(":", 2)[2])
    if target.startswith("qqbot:channel:"):
        return ("channel", target.split(":", 2)[2])
    raise ValueError(f"Unsupported QQ target format: {target}")


def _intents_to_bitmask(intents: list[str]) -> int:
    bitmask = 0
    for name in intents:
        bitmask |= INTENT_BITS.get(name, 0)
    return bitmask


def _strip_qq_mentions(content: str) -> str:
    for prefix in ("<@!", "<@"):
        if prefix in content:
            while True:
                start = content.find(prefix)
                if start < 0:
                    break
                end = content.find(">", start)
                if end < 0:
                    break
                content = (content[:start] + content[end + 1 :]).strip()
    return content


def _extract_attachments(data: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = data.get("attachments") or []
    if isinstance(attachments, list):
        return [item for item in attachments if isinstance(item, dict)]
    return []


def _attachment_filename(attachment: dict[str, Any], message_id: str, index: int, url: str) -> str:
    original = str(
        attachment.get("filename")
        or attachment.get("file_name")
        or attachment.get("name")
        or ""
    ).strip()
    if not original:
        path = urlparse(url).path
        original = Path(path).name
    if not original:
        suffix = mimetypes.guess_extension(str(attachment.get("content_type") or "").split(";")[0].strip()) or ""
        original = f"qq_attachment_{message_id or 'message'}_{index}{suffix}"
    safe_name = original.replace("\\", "_").replace("/", "_").replace(":", "_")
    return safe_name


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
