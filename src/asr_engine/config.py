from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from asr_engine.modules import RENAMED_MODULE_TYPES, canonical_module_type

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

log = logging.getLogger(__name__)


def _optional_positive_int(data: dict[str, Any], key: str, where: str) -> int | None:
    """``data[key]`` as a positive integer, or ``None`` when absent or null."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Invalid {where}.{key}: must be a positive integer or null.")
    return value


def _read_file(path: str) -> str:
    """Read *path* as UTF-8, re-raising a missing file with a config-flavored message.

    UTF-8 explicitly, not the locale default: on Windows that is a legacy code page,
    which would garble non-ASCII values such as trigger words.
    """
    try:
        with open(path, encoding="utf-8") as f:
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
    # The format the audio source produces. ``None`` = no rate (or channel count)
    # of its own: a file uses its own, a microphone or an injected source the
    # module's. See specs/format-conversion.md.
    sample_rate: int | None = None
    channels: int | None = None
    encoding: str = "linear16"


@dataclass
class ModuleConfig:
    type: str = ""
    # The rate the module is fed, when it must differ from the engine's choice
    # (the source's rate if the module supports it, else the module's default).
    sample_rate: int | None = None
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
class VadConfig:
    """The optional ``engine.vad`` block: a VAD module and the gate's parameters.

    See specs/vad.md "Configuration".
    """

    type: str = ""
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 500
    speech_pad_ms: int = 300
    extra: dict[str, Any] = field(default_factory=dict)


_VAD_GATE_FIELDS = (
    "type",
    "threshold",
    "min_speech_duration_ms",
    "min_silence_duration_ms",
    "speech_pad_ms",
)


def _vad_duration(data: dict[str, Any], key: str, default: int, minimum: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        bound = "a positive integer" if minimum > 0 else "a non-negative integer"
        raise ValueError(f"Invalid engine.vad.{key}: {value!r}. Must be {bound}.")
    return value


def _parse_vad(data: object) -> VadConfig | None:
    """Parse and validate the ``engine.vad`` block (``None``/absent → no VAD)."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("Invalid engine.vad: must be an object or null.")
    vad_type = data.get("type")
    if not isinstance(vad_type, str) or not vad_type:
        raise ValueError(
            "Config is missing required field: engine.vad.type "
            "(omit the engine.vad block to run without VAD)"
        )
    threshold = data.get("threshold", 0.5)
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not 0 < threshold < 1
    ):
        raise ValueError(
            f"Invalid engine.vad.threshold: {threshold!r}. "
            f"Must be a number strictly between 0 and 1."
        )
    return VadConfig(
        type=vad_type,
        threshold=float(threshold),
        min_speech_duration_ms=_vad_duration(data, "min_speech_duration_ms", 250, 0),
        min_silence_duration_ms=_vad_duration(data, "min_silence_duration_ms", 500, 1),
        speech_pad_ms=_vad_duration(data, "speech_pad_ms", 300, 0),
        extra={k: v for k, v in data.items() if k not in _VAD_GATE_FIELDS},
    )


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
    vad: VadConfig | None = None  # None: no VAD stage (specs/vad.md)

    @classmethod
    def from_dict(cls, engine_block: dict[str, Any]) -> ASREngineConfig:
        """Build (and validate) an ``ASREngineConfig`` from a raw ``engine`` block dict.

        This is the public in-memory constructor for direct importers of the
        library, and where all engine validation lives: it takes the object under
        the top-level ``"engine"`` key (not the whole config file) and validates it
        — segmentation modes in {utterance, trigger_word, timeout}, encoding in
        {linear16, mulaw}, the audio and module sample rates positive integers,
        ``auto_start_dictation`` requires ``auto_start``, ``module.type`` is
        required, and the optional ``vad`` block's ``type`` and gate parameters. ``from_json``/``from_json_file`` layer JSON string/file loading
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
        if "on_unsupported_format" in audio_data:
            log.warning(
                "engine.audio.on_unsupported_format is no longer used and is "
                "ignored: the engine now converts the audio to a format the "
                "module supports. Remove it from the config."
            )
        audio = AudioConfig(
            device=audio_data.get("device", None),
            audio_file=audio_data.get("audio_file", None),
            trailing_silence_s=audio_data.get("trailing_silence_s", 0.0),
            sample_rate=_optional_positive_int(
                audio_data, "sample_rate", "engine.audio"
            ),
            channels=_optional_positive_int(audio_data, "channels", "engine.audio"),
            encoding=encoding,
        )

        module_data = engine_block.get("module", {})
        module_type = module_data.get("type")
        if not module_type:
            raise ValueError("Config is missing required field: engine.module.type")
        # `type` and `sample_rate` are the engine's; the rest is the module's own.
        extra = {
            k: v for k, v in module_data.items() if k not in ("type", "sample_rate")
        }
        # An old key (`deepgram_v1`) is stored as its new one, with a warning.
        module = ModuleConfig(
            type=canonical_module_type(module_type),
            sample_rate=_optional_positive_int(
                module_data, "sample_rate", "engine.module"
            ),
            extra=extra,
        )

        vad = _parse_vad(engine_block.get("vad"))

        return cls(
            auto_start=auto_start,
            auto_start_dictation=auto_start_dictation,
            listen_default_segmentation_mode=listen_default,
            dictation_default_segmentation_mode=dictation_default,
            segmentation=segmentation,
            sound_feedback=sound_feedback,
            audio=audio,
            module=module,
            vad=vad,
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
    """Raise ValueError if config.module.type is not a key in *registry*.

    An old key from ``RENAMED_MODULE_TYPES`` is checked under its new key.
    """
    module_type = RENAMED_MODULE_TYPES.get(config.module.type, config.module.type)
    if module_type not in registry:
        available = ", ".join(sorted(registry.keys())) or "(none)"
        raise ValueError(f"Unknown ASR type '{module_type}'. Available: {available}")
