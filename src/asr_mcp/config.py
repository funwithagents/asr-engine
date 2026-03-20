from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_TRIGGER_WORDS: list[str] = [
    "submit", "enter", "validate", "send", "confirm", "go",
    "envoyer", "valider", "confirmer", "soumettre", "entree", "entrée",
]


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class AudioConfig:
    device: str | None = None
    output_device: str | None = None
    audio_file: str | None = None
    trailing_silence_s: float = 0.0


@dataclass
class ASRConfig:
    type: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineConfig:
    auto_start: bool = True


@dataclass
class ListenConfig:
    end_of_utterance_mode: str = "trigger_word"
    trigger_words: list[str] = field(default_factory=lambda: list(_DEFAULT_TRIGGER_WORDS))
    initial_silence_timeout_s: float = 10.0
    end_of_speech_timeout_s: float = 5.0
    sound_feedback: bool = True


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    listen: ListenConfig = field(default_factory=ListenConfig)


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
        output_device=audio_data.get("output_device", None),
        audio_file=audio_data.get("audio_file", None),
        trailing_silence_s=audio_data.get("trailing_silence_s", 0.0),
    )

    asr_data = data.get("asr", {})
    asr_type = asr_data.get("type")
    if not asr_type:
        raise ValueError("Config is missing required field: asr.type")

    extra = {k: v for k, v in asr_data.items() if k != "type"}
    asr = ASRConfig(type=asr_type, extra=extra)

    engine_data = data.get("engine", {})
    engine = EngineConfig(
        auto_start=engine_data.get("auto_start", True),
    )

    listen_data = data.get("listen", {})
    end_of_utterance_mode = listen_data.get("end_of_utterance_mode", "trigger_word")
    if end_of_utterance_mode not in ("trigger_word", "timeout"):
        raise ValueError(
            f"Invalid end_of_utterance_mode: '{end_of_utterance_mode}'. "
            f"Must be 'trigger_word' or 'timeout'."
        )
    trigger_words = listen_data.get("trigger_words", list(_DEFAULT_TRIGGER_WORDS))
    listen = ListenConfig(
        end_of_utterance_mode=end_of_utterance_mode,
        trigger_words=trigger_words,
        initial_silence_timeout_s=listen_data.get("initial_silence_timeout_s", 10.0),
        end_of_speech_timeout_s=listen_data.get("end_of_speech_timeout_s", 5.0),
        sound_feedback=listen_data.get("sound_feedback", True),
    )

    return AppConfig(server=server, audio=audio, asr=asr, engine=engine, listen=listen)


def validate_asr_type(config: AppConfig, registry: dict) -> None:
    """Raise ValueError if config.asr.type is not a key in *registry*."""
    if config.asr.type not in registry:
        available = ", ".join(sorted(registry.keys())) or "(none)"
        raise ValueError(
            f"Unknown ASR type '{config.asr.type}'. Available: {available}"
        )
