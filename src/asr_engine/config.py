from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_TRIGGER_WORDS: list[str] = [
    "submit",
    "enter",
    "validate",
    "send",
    "confirm",
    "go",
    "envoyer",
    "valider",
    "confirmer",
    "soumettre",
    "entree",
    "entrée",
]

_SEGMENT_MODES = ("utterance", "trigger_word", "timeout")
_ENCODINGS = ("linear16", "mulaw")
_UNSUPPORTED_FORMAT_POLICIES = ("error", "fallback")


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class AudioConfig:
    device: str | None = None
    audio_file: str | None = None
    trailing_silence_s: float = 0.0
    sample_rate: int = 16000
    channels: int = 1
    encoding: str = "linear16"
    on_unsupported_format: str = "error"


@dataclass
class ModuleConfig:
    type: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SegmentationConfig:
    trigger_words: list[str] = field(
        default_factory=lambda: list(_DEFAULT_TRIGGER_WORDS)
    )
    initial_silence_timeout_s: float = 10.0
    end_of_speech_timeout_s: float = 5.0


@dataclass
class SoundFeedbackConfig:
    enabled: bool = True
    output_device: str | int | None = None


@dataclass
class ASREngineConfig:
    """Everything an ``ASREngine`` needs — the whole ``engine`` config block."""

    auto_start: bool = True
    auto_start_dictation: bool = False
    listen_default_segmentation_mode: str = "trigger_word"
    dictation_default_segmentation_mode: str = "trigger_word"
    segmentation: SegmentationConfig = field(default_factory=SegmentationConfig)
    sound_feedback: SoundFeedbackConfig = field(default_factory=SoundFeedbackConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    module: ModuleConfig = field(default_factory=ModuleConfig)


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    engine: ASREngineConfig = field(default_factory=ASREngineConfig)


def _parse_engine(engine_data: dict[str, Any]) -> ASREngineConfig:
    auto_start = engine_data.get("auto_start", True)
    auto_start_dictation = engine_data.get("auto_start_dictation", False)
    if auto_start_dictation and not auto_start:
        raise ValueError(
            "Invalid engine config: auto_start_dictation requires auto_start=true."
        )

    listen_default = engine_data.get("listen_default_segmentation_mode", "trigger_word")
    if listen_default not in _SEGMENT_MODES:
        raise ValueError(
            f"Invalid engine.listen_default_segmentation_mode: '{listen_default}'. "
            f"Must be one of {', '.join(_SEGMENT_MODES)}."
        )

    dictation_default = engine_data.get(
        "dictation_default_segmentation_mode", "trigger_word"
    )
    if dictation_default not in _SEGMENT_MODES:
        raise ValueError(
            f"Invalid engine.dictation_default_segmentation_mode: "
            f"'{dictation_default}'. Must be one of {', '.join(_SEGMENT_MODES)}."
        )

    seg_data = engine_data.get("segmentation", {})
    segmentation = SegmentationConfig(
        trigger_words=seg_data.get("trigger_words", list(_DEFAULT_TRIGGER_WORDS)),
        initial_silence_timeout_s=seg_data.get("initial_silence_timeout_s", 10.0),
        end_of_speech_timeout_s=seg_data.get("end_of_speech_timeout_s", 5.0),
    )

    sf_data = engine_data.get("sound_feedback", {})
    sound_feedback = SoundFeedbackConfig(
        enabled=sf_data.get("enabled", True),
        output_device=sf_data.get("output_device", None),
    )

    audio_data = engine_data.get("audio", {})
    encoding = audio_data.get("encoding", "linear16")
    if encoding not in _ENCODINGS:
        raise ValueError(
            f"Invalid engine.audio.encoding: '{encoding}'. "
            f"Must be one of {', '.join(_ENCODINGS)}."
        )
    on_unsupported = audio_data.get("on_unsupported_format", "error")
    if on_unsupported not in _UNSUPPORTED_FORMAT_POLICIES:
        raise ValueError(
            f"Invalid engine.audio.on_unsupported_format: '{on_unsupported}'. "
            f"Must be one of {', '.join(_UNSUPPORTED_FORMAT_POLICIES)}."
        )
    audio = AudioConfig(
        device=audio_data.get("device", None),
        audio_file=audio_data.get("audio_file", None),
        trailing_silence_s=audio_data.get("trailing_silence_s", 0.0),
        sample_rate=audio_data.get("sample_rate", 16000),
        channels=audio_data.get("channels", 1),
        encoding=encoding,
        on_unsupported_format=on_unsupported,
    )

    module_data = engine_data.get("module", {})
    module_type = module_data.get("type")
    if not module_type:
        raise ValueError("Config is missing required field: engine.module.type")
    extra = {k: v for k, v in module_data.items() if k != "type"}
    module = ModuleConfig(type=module_type, extra=extra)

    return ASREngineConfig(
        auto_start=auto_start,
        auto_start_dictation=auto_start_dictation,
        listen_default_segmentation_mode=listen_default,
        dictation_default_segmentation_mode=dictation_default,
        segmentation=segmentation,
        sound_feedback=sound_feedback,
        audio=audio,
        module=module,
    )


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

    engine = _parse_engine(data.get("engine", {}))

    return AppConfig(server=server, engine=engine)


def validate_asr_type(config: AppConfig, registry: dict) -> None:
    """Raise ValueError if config.engine.module.type is not a key in *registry*."""
    module_type = config.engine.module.type
    if module_type not in registry:
        available = ", ".join(sorted(registry.keys())) or "(none)"
        raise ValueError(f"Unknown ASR type '{module_type}'. Available: {available}")
