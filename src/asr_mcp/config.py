from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class AudioConfig:
    device: str | None = None
    audio_file: str | None = None
    trailing_silence_s: float = 0.0


@dataclass
class ASRConfig:
    type: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)


def load_config(path: str) -> AppConfig:
    """Load and parse the JSON config file at *path*."""
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Config file is not valid JSON ({path}): {exc}") from exc

    server_data = data.get("server", {})
    server = ServerConfig(
        host=server_data.get("host", "127.0.0.1"),
        port=server_data.get("port", 8000),
    )

    audio_data = data.get("audio", {})
    audio = AudioConfig(
        device=audio_data.get("device", None),
        audio_file=audio_data.get("audio_file", None),
        trailing_silence_s=audio_data.get("trailing_silence_s", 0.0),
    )

    asr_data = data.get("asr", {})
    asr_type = asr_data.get("type")
    if not asr_type:
        raise ValueError("Config is missing required field: asr.type")

    extra = {k: v for k, v in asr_data.items() if k != "type"}
    asr = ASRConfig(type=asr_type, extra=extra)

    return AppConfig(server=server, audio=audio, asr=asr)


def validate_asr_type(config: AppConfig, registry: dict) -> None:
    """Raise ValueError if config.asr.type is not a key in *registry*."""
    if config.asr.type not in registry:
        available = ", ".join(sorted(registry.keys())) or "(none)"
        raise ValueError(
            f"Unknown ASR type '{config.asr.type}'. Available: {available}"
        )
