from __future__ import annotations

import argparse
from pathlib import Path

from app.channel.qq_channel import QQChannel
from app.runtime.config import AppConfig, dump_yaml_file


async def run_channel_command(*, root: Path, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="main.py")
    subparsers = parser.add_subparsers(dest="command")

    channels_parser = subparsers.add_parser("channels")
    channels_subparsers = channels_parser.add_subparsers(dest="channels_command")

    add_parser = channels_subparsers.add_parser("add")
    add_parser.add_argument("--channel", required=True)
    add_parser.add_argument("--token")
    add_parser.add_argument("--token-file")
    add_parser.add_argument("--app-id")

    check_parser = channels_subparsers.add_parser("check")
    check_parser.add_argument("--channel", required=True)

    args = parser.parse_args(argv)

    if args.command != "channels":
        parser.print_help()
        return 1

    if args.channel != "qqbot":
        raise SystemExit("Only qqbot is currently supported.")

    if args.channels_command == "add":
        return _handle_channels_add(root=root, token=args.token, token_file=args.token_file, app_id=args.app_id)
    if args.channels_command == "check":
        return await _handle_channels_check(root=root)

    channels_parser.print_help()
    return 1


def _handle_channels_add(
    *,
    root: Path,
    token: str | None,
    token_file: str | None,
    app_id: str | None,
) -> int:
    channels_path = root / "config" / "channels.yaml"
    payload = AppConfig.load_channels_yaml(root)
    channels = payload.setdefault("channels", {})
    qqbot = channels.setdefault("qqbot", {})
    qqbot["enabled"] = True

    if token:
        if ":" not in token:
            raise SystemExit('Expected --token in the form "AppID:AppSecret".')
        parsed_app_id, parsed_secret = token.split(":", 1)
        qqbot["appId"] = parsed_app_id
        qqbot["clientSecret"] = parsed_secret
        qqbot.pop("clientSecretFile", None)
    elif token_file:
        if app_id:
            qqbot["appId"] = app_id
        qqbot["clientSecretFile"] = token_file
        qqbot.pop("clientSecret", None)
    else:
        raise SystemExit("Either --token or --token-file is required.")

    dump_yaml_file(channels_path, payload)
    print(f"Saved QQ Bot channel config to {channels_path}")
    return 0


async def _handle_channels_check(*, root: Path) -> int:
    config = AppConfig.load(root)
    channel = QQChannel(config.channels.qqbot)
    result = await channel.check_connection()
    if result.ok:
        print(f"QQ Bot check passed. Gateway URL: {result.gateway_url}")
        return 0
    print(f"QQ Bot check failed: {result.message}")
    return 1
