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


def _read_file(path: str) -> str:
    """Read *path*, re-raising a missing file with a config-flavored message."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file not found: {path}")


def _parse_config_json(text: str, source: str | None = None) -> Any:
    """Parse *text* as JSON, raising a clear ``ValueError`` on malformed input.

    *source* (a file path, when the JSON came from a file) is included in the
    error so a bad config file names itself.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        where = f" ({source})" if source else ""
        raise ValueError(f"Config is not valid JSON{where}: {exc}") from exc


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

    @classmethod
    def from_dict(cls, engine_block: dict[str, Any]) -> ASREngineConfig:
        """Build (and validate) an ``ASREngineConfig`` from a raw ``engine`` block dict.

        This is the public in-memory constructor for direct importers of the
        library, and where all engine validation lives: it takes the object under
        the top-level ``"engine"`` key (not the whole config file) and validates it
        — segmentation modes in {utterance, trigger_word, timeout}, encoding in
        {linear16, mulaw}, on_unsupported_format in {error, fallback},
        ``auto_start_dictation`` requires ``auto_start``, and ``module.type`` is
        required. ``from_json``/``from_json_file`` layer JSON string/file loading
        over it, and ``MCPServerConfig.from_dict`` delegates its ``engine`` block
        here, so every entry point validates identically.

        No environment variables are read: ``module`` extra fields are carried
        through unchanged, and the module resolves its own ``api_key``/``api_key_env``
        later at engine construction — so a config with unset credentials still
        builds here.
        """
        auto_start = engine_block.get("auto_start", True)
        auto_start_dictation = engine_block.get("auto_start_dictation", False)
        if auto_start_dictation and not auto_start:
            raise ValueError(
                "Invalid engine config: auto_start_dictation requires auto_start=true."
            )

        listen_default = engine_block.get(
            "listen_default_segmentation_mode", "trigger_word"
        )
        if listen_default not in _SEGMENT_MODES:
            raise ValueError(
                f"Invalid engine.listen_default_segmentation_mode: '{listen_default}'. "
                f"Must be one of {', '.join(_SEGMENT_MODES)}."
            )

        dictation_default = engine_block.get(
            "dictation_default_segmentation_mode", "trigger_word"
        )
        if dictation_default not in _SEGMENT_MODES:
            raise ValueError(
                f"Invalid engine.dictation_default_segmentation_mode: "
                f"'{dictation_default}'. Must be one of {', '.join(_SEGMENT_MODES)}."
            )

        seg_data = engine_block.get("segmentation", {})
        segmentation = SegmentationConfig(
            trigger_words=seg_data.get("trigger_words", list(_DEFAULT_TRIGGER_WORDS)),
            initial_silence_timeout_s=seg_data.get("initial_silence_timeout_s", 10.0),
            end_of_speech_timeout_s=seg_data.get("end_of_speech_timeout_s", 5.0),
        )

        sf_data = engine_block.get("sound_feedback", {})
        sound_feedback = SoundFeedbackConfig(
            enabled=sf_data.get("enabled", True),
            output_device=sf_data.get("output_device", None),
        )

        audio_data = engine_block.get("audio", {})
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

        module_data = engine_block.get("module", {})
        module_type = module_data.get("type")
        if not module_type:
            raise ValueError("Config is missing required field: engine.module.type")
        extra = {k: v for k, v in module_data.items() if k != "type"}
        module = ModuleConfig(type=module_type, extra=extra)

        return cls(
            auto_start=auto_start,
            auto_start_dictation=auto_start_dictation,
            listen_default_segmentation_mode=listen_default,
            dictation_default_segmentation_mode=dictation_default,
            segmentation=segmentation,
            sound_feedback=sound_feedback,
            audio=audio,
            module=module,
        )

    @classmethod
    def from_json(cls, text: str) -> ASREngineConfig:
        """Build an ``ASREngineConfig`` from a JSON string holding an ``engine`` block.

        The JSON's top-level object is the ``engine`` block itself (the shape found
        under a config file's ``"engine"`` key) — **not** a whole MCP server config
        file. Parses the string and delegates to :meth:`from_dict` (same validation).
        """
        return cls.from_dict(_parse_config_json(text))

    @classmethod
    def from_json_file(cls, path: str) -> ASREngineConfig:
        """Build an ``ASREngineConfig`` from a JSON *file* holding an ``engine`` block.

        The file's top-level object is the ``engine`` block itself (``module``,
        ``segmentation``, …) — **not** an MCP ``config.json`` with ``server``/``engine``
        wrappers. To load a whole server config file and take its engine, use
        :meth:`MCPServerConfig.from_json_file` and read ``.engine`` instead.
        """
        return cls.from_dict(_parse_config_json(_read_file(path), source=path))


@dataclass
class MCPServerConfig:
    """A whole MCP server config file: the ``server`` block plus the ``engine`` block.

    Only the ``asr-engine-mcp`` entry point needs this wrapper. A direct importer of
    the library builds an :class:`ASREngineConfig` from the ``engine`` block alone
    and never needs ``server``.
    """

    server: ServerConfig = field(default_factory=ServerConfig)
    engine: ASREngineConfig = field(default_factory=ASREngineConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPServerConfig:
        """Build an ``MCPServerConfig`` from a whole-file dict (``server`` + ``engine``).

        Parses the optional ``server`` block (host/port) and delegates the ``engine``
        block to :meth:`ASREngineConfig.from_dict`, so both entry points run identical
        engine validation.
        """
        server_data = data.get("server", {})
        server = ServerConfig(
            host=server_data.get("host", "127.0.0.1"),
            port=server_data.get("port", 8000),
        )
        engine = ASREngineConfig.from_dict(data.get("engine", {}))
        return cls(server=server, engine=engine)

    @classmethod
    def from_json(cls, text: str) -> MCPServerConfig:
        """Build an ``MCPServerConfig`` from a whole-file JSON string."""
        return cls.from_dict(_parse_config_json(text))

    @classmethod
    def from_json_file(cls, path: str) -> MCPServerConfig:
        """Load and parse the MCP server JSON config file at *path*."""
        return cls.from_dict(_parse_config_json(_read_file(path), source=path))


def validate_asr_type(config: ASREngineConfig, registry: dict) -> None:
    """Raise ValueError if config.module.type is not a key in *registry*."""
    module_type = config.module.type
    if module_type not in registry:
        available = ", ".join(sorted(registry.keys())) or "(none)"
        raise ValueError(f"Unknown ASR type '{module_type}'. Available: {available}")
