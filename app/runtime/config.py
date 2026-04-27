from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class RuntimeConfig:
    max_subagents: int = 10
    main_agent_max_loops: int | None = None
    subagent_max_loops: int | None = None
    task_loop_budget: int | None = None
    task_max_runtime_seconds: int | None = None
    default_channel: str = "cli"
    auto_create_session: bool = True
    write_summary_after_memory_save: bool = True
    kill_subagents_on_new: bool = True
    graceful_exit_flush: bool = True


@dataclass(slots=True)
class PathsConfig:
    memory_root: str = "./memory"
    submemory_root: str = "./submemory"
    agents_root: str = "./agents"
    skills_root: str = "./skills"
    data_root: str = "./data"
    logs_root: str = "./logs"


@dataclass(slots=True)
class RetrievalConfig:
    backend: str = "sqlite_fts5"
    top_k: int = 5
    auto_retry_with_rewritten_keywords: bool = True
    summary_heading: str = "# Summary"


@dataclass(slots=True)
class CommandsConfig:
    new: str = "/new"
    agents: str = "/agents"
    remember: str = "/remember-"
    callmemory: str = "/callmemory-"
    tasks: str = "/tasks"
    task_run: str = "/task run"
    task_show: str = "/task show"
    task_resume: str = "/task resume"
    kill_subagents: str = "/kill subagents"
    memory_search: str = "/memory search"
    submemory_search: str = "/submemory search"
    confirm_delete: str = "/confirm delete"
    exit: str = "/exit"


@dataclass(slots=True)
class QQChannelConfig:
    enabled: bool = False
    adapter: str = "onebot"
    ws_url: str = ""
    access_token: str = ""


@dataclass(slots=True)
class ChannelConfig:
    cli_enabled: bool = True
    qq: QQChannelConfig = field(default_factory=QQChannelConfig)


@dataclass(slots=True)
class QQBotConfig:
    enabled: bool = False
    app_id: str = ""
    client_secret: str = ""
    client_secret_file: str = ""
    sandbox: bool = False
    remove_at: bool = True
    max_retry: int = 10
    intents: list[str] = field(
        default_factory=lambda: [
            "C2C_MESSAGE_CREATE",
            "GROUP_AT_MESSAGE_CREATE",
            "AT_MESSAGE_CREATE",
        ]
    )
    api_base_url: str = "https://api.sgroup.qq.com"
    token_url: str = "https://bots.qq.com/app/getAppAccessToken"
    account_name: str = "default"


@dataclass(slots=True)
class ChannelsConfig:
    qqbot: QQBotConfig = field(default_factory=QQBotConfig)


@dataclass(slots=True)
class MemoryConfig:
    file_name_format: str = "%Y-%m-%d_%H-%M-%S"
    source_values: list[str] = field(default_factory=lambda: ["cli", "qq"])
    type_values: list[str] = field(
        default_factory=lambda: ["conversation", "long_output", "task_result"]
    )


@dataclass(slots=True)
class ExecutionConfig:
    mode: str = "local"
    local_only: bool = True
    target_platform: str = "linux"
    shell_executable: str = "bash"
    python_executable: str = "current"


@dataclass(slots=True)
class ModelProfile:
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: int = 120
    temperature: float = 0.2
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    thinking: dict[str, Any] | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppConfig:
    root: Path
    app_name: str
    debug: bool
    timezone: str
    runtime: RuntimeConfig
    paths: PathsConfig
    retrieval: RetrievalConfig
    commands: CommandsConfig
    channel: ChannelConfig
    channels: ChannelsConfig
    memory: MemoryConfig
    execution: ExecutionConfig
    models: dict[str, ModelProfile]
    default_profile: str
    tools_config: dict[str, Any]

    @property
    def default_model(self) -> ModelProfile:
        return self.models[self.default_profile]

    @classmethod
    def load(cls, root: Path) -> "AppConfig":
        app_path = _resolve_config_file(root / "config", "app")
        models_path = _resolve_config_file(root / "config", "models")
        tools_path = _resolve_config_file(root / "config", "tools")
        channels_data = cls.load_channels_yaml(root)

        app_data = _load_yaml(app_path)
        models_data = _load_yaml(models_path)
        tools_data = _load_yaml(tools_path)

        app_meta = app_data.get("app", {})
        runtime = RuntimeConfig(**app_data.get("runtime", {}))
        paths = PathsConfig(**app_data.get("paths", {}))
        retrieval = RetrievalConfig(**app_data.get("retrieval", {}))
        commands = CommandsConfig(**app_data.get("commands", {}))

        channel_data = app_data.get("channel", {})
        channel = ChannelConfig(
            cli_enabled=channel_data.get("cli", {}).get("enabled", True),
            qq=QQChannelConfig(**channel_data.get("qq", {})),
        )
        qqbot = _parse_qqbot_config(channels_data.get("channels", {}).get("qqbot", {}), root)
        channels = ChannelsConfig(qqbot=qqbot)
        memory = MemoryConfig(**app_data.get("memory", {}))
        execution = ExecutionConfig(**app_data.get("execution", {}))

        default_profile = models_data["default_profile"]
        profiles = {}
        for name, payload in models_data.get("profiles", {}).items():
            profiles[name] = ModelProfile(name=name, **payload)

        return cls(
            root=root,
            app_name=app_meta.get("name", "YClaw"),
            debug=bool(app_meta.get("debug", False)),
            timezone=app_meta.get("timezone", "Asia/Shanghai"),
            runtime=runtime,
            paths=paths,
            retrieval=retrieval,
            commands=commands,
            channel=channel,
            channels=channels,
            memory=memory,
            execution=execution,
            models=profiles,
            default_profile=default_profile,
            tools_config=tools_data,
        )

    @staticmethod
    def load_channels_yaml(root: Path) -> dict[str, Any]:
        channels_dir = root / "config"
        primary = channels_dir / "channels.yaml"
        example = channels_dir / "channels.example.yaml"
        if primary.exists():
            return _load_yaml(primary)
        if example.exists():
            return _load_yaml(example)
        return {}


def _resolve_config_file(config_dir: Path, base_name: str) -> Path:
    primary = config_dir / f"{base_name}.yaml"
    example = config_dir / f"{base_name}.example.yaml"
    if primary.exists():
        return primary
    if example.exists():
        return example
    raise FileNotFoundError(f"Missing config file for {base_name!r} in {config_dir}")


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in config file: {path}")
    return data


def dump_yaml_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)


def _parse_qqbot_config(payload: dict[str, Any], root: Path) -> QQBotConfig:
    app_id = str(payload.get("appId", "")).strip() or os.getenv("QQBOT_APP_ID", "")
    client_secret = str(payload.get("clientSecret", "")).strip()
    client_secret_file = str(payload.get("clientSecretFile", "")).strip()
    if not client_secret and client_secret_file:
        secret_path = Path(client_secret_file)
        if not secret_path.is_absolute():
            secret_path = (root / client_secret_file).resolve()
        if secret_path.exists():
            client_secret = secret_path.read_text(encoding="utf-8").strip()
    if not client_secret:
        client_secret = os.getenv("QQBOT_CLIENT_SECRET", "")

    return QQBotConfig(
        enabled=bool(payload.get("enabled", False)),
        app_id=app_id,
        client_secret=client_secret,
        client_secret_file=client_secret_file,
        sandbox=bool(payload.get("sandbox", False)),
        remove_at=bool(payload.get("removeAt", True)),
        max_retry=int(payload.get("maxRetry", 10)),
        intents=list(payload.get("intents", ["C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "AT_MESSAGE_CREATE"])),
        api_base_url=str(payload.get("apiBaseUrl", "https://api.sgroup.qq.com")).rstrip("/"),
        token_url=str(payload.get("tokenUrl", "https://bots.qq.com/app/getAppAccessToken")).rstrip("/"),
        account_name=str(payload.get("account", "default")),
    )
