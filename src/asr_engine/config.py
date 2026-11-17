from __future__ import annotations

import json
import math
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


_AUDIO_FORMAT_FIELDS = ("sample_rate", "channels", "encoding")


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


def _as_object(data: Any, where: str, fields: tuple[str, ...]) -> dict[str, Any]:
    """Return *data* as a config object holding only *fields* (``None`` = empty).

    *where* is the object's dotted path, used in the error messages. An unknown
    key is an error, so a misspelled field is not silently ignored.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid {where}: must be an object or null.")
    unknown = sorted(str(key) for key in set(data) - set(fields))
    if unknown:
        raise ValueError(
            f"Invalid {where}: unknown field(s) {', '.join(unknown)}. "
            f"Fields: {', '.join(fields)}."
        )
    return data


def _as_bool(data: dict[str, Any], key: str, default: bool, where: str) -> bool:
    """A real boolean: ``"false"`` is a non-empty string, and so would be true."""
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Invalid {where}.{key}: {value!r}. Must be true or false.")
    return value


def _as_seconds(
    data: dict[str, Any], key: str, default: float, where: str, *, allow_zero: bool
) -> float:
    """A finite duration in seconds, positive (or non-negative with *allow_zero*)."""
    value = data.get(key, default)
    valid = (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and (value >= 0 if allow_zero else value > 0)
    )
    if not valid:
        bound = "a non-negative number" if allow_zero else "a positive number"
        raise ValueError(f"Invalid {where}.{key}: {value!r}. Must be {bound}.")
    return float(value)


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class AudioFormatConfig:
    """An ``audio_format`` config object: a rate, a channel count and an encoding.

    Every field is optional; ``None`` means "not set", and the engine resolves it
    (specs/format-conversion.md). The resolved value is an ``AudioFormat``.
    """

    sample_rate: int | None = None
    channels: int | None = None
    encoding: str | None = None  # "linear16" | "mulaw"


def _parse_audio_format(data: Any, where: str) -> AudioFormatConfig:
    """Parse and validate an ``audio_format`` object (absent or null = nothing set).

    *where* is the object's dotted path (``engine.module.audio_format``), used in
    the error messages. An unknown key is an error, so a typo is not silently
    ignored.
    """
    if data is None:
        return AudioFormatConfig()
    if not isinstance(data, dict):
        raise ValueError(f"Invalid {where}: must be an object or null.")
    unknown = sorted(set(data) - set(_AUDIO_FORMAT_FIELDS))
    if unknown:
        raise ValueError(
            f"Invalid {where}: unknown field(s) {', '.join(unknown)}. "
            f"Fields: {', '.join(_AUDIO_FORMAT_FIELDS)}."
        )
    for key in ("sample_rate", "channels"):
        value = data.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise ValueError(
                f"Invalid {where}.{key}: must be a positive integer or null."
            )
    encoding = data.get("encoding")
    if encoding is not None and encoding not in _ENCODINGS:
        raise ValueError(
            f"Invalid {where}.encoding: '{encoding}'. "
            f"Must be one of {', '.join(_ENCODINGS)}, or null."
        )
    return AudioFormatConfig(
        sample_rate=data.get("sample_rate"),
        channels=data.get("channels"),
        encoding=encoding,
    )


# engine.audio_source.type → the fields that type takes, besides `type` itself.
AUDIO_SOURCE_TYPES: dict[str, tuple[str, ...]] = {
    "input-device": ("device", "audio_format"),
    "file": ("audio_file", "trailing_silence_s"),
    "custom": (),
}
DEFAULT_AUDIO_SOURCE_TYPE = "input-device"


@dataclass
class AudioSourceConfig:
    """The ``engine.audio_source`` block: where the audio comes from.

    ``type`` selects the fields that apply, and defaults to ``"input-device"``:

    - ``"input-device"``: ``device``, and ``audio_format`` (what the device is opened
      at; a field left unset follows the module's format).
    - ``"file"``: ``audio_file`` and ``trailing_silence_s``. The format is the
      file's own.
    - ``"custom"``: none. The application passes the source to
      ``ASREngine(config, audio_source=...)``, and the source says what it
      produces (specs/audio-source.md).
    """

    type: str = DEFAULT_AUDIO_SOURCE_TYPE
    device: str | None = None
    audio_file: str | None = None
    trailing_silence_s: float = 0.0
    audio_format: AudioFormatConfig = field(default_factory=AudioFormatConfig)


def _parse_audio_source(engine_block: dict[str, Any]) -> AudioSourceConfig:
    if "audio" in engine_block:
        raise ValueError(
            "engine.audio is now engine.audio_source, with a 'type' "
            '("input-device", "file" or "custom"), e.g. '
            '"audio_source": {"type": "input-device", "device": null}.'
        )
    data = engine_block.get("audio_source")
    if data is None:
        return AudioSourceConfig()
    if not isinstance(data, dict):
        raise ValueError("Invalid engine.audio_source: must be an object or null.")
    source_type = data.get("type", DEFAULT_AUDIO_SOURCE_TYPE)
    if source_type not in AUDIO_SOURCE_TYPES:
        raise ValueError(
            f"Invalid engine.audio_source.type: '{source_type}'. "
            f"Must be one of {', '.join(AUDIO_SOURCE_TYPES)}."
        )
    allowed = AUDIO_SOURCE_TYPES[source_type]
    misplaced = sorted(set(data) - {"type", *allowed})
    if misplaced:
        takes = ", ".join(allowed) if allowed else "no other field"
        raise ValueError(
            f"Invalid engine.audio_source: {', '.join(misplaced)} does not apply "
            f"to type '{source_type}', which takes {takes}."
        )
    if source_type == "file" and not data.get("audio_file"):
        raise ValueError(
            "Config is missing required field: engine.audio_source.audio_file "
            "(type 'file')"
        )
    device = data.get("device")
    if device is not None and not isinstance(device, str):
        raise ValueError(
            f"Invalid engine.audio_source.device: {device!r}. "
            f"Must be a device name or null."
        )
    audio_file = data.get("audio_file")
    if audio_file is not None and not isinstance(audio_file, str):
        raise ValueError(
            f"Invalid engine.audio_source.audio_file: {audio_file!r}. "
            f"Must be a path, as a string."
        )
    return AudioSourceConfig(
        type=source_type,
        device=device,
        audio_file=audio_file,
        trailing_silence_s=_as_seconds(
            data, "trailing_silence_s", 0.0, "engine.audio_source", allow_zero=True
        ),
        audio_format=_parse_audio_format(
            data.get("audio_format"), "engine.audio_source.audio_format"
        ),
    )


@dataclass
class ModuleConfig:
    type: str = ""
    # The format the module is fed. A field left unset is the module's default.
    audio_format: AudioFormatConfig = field(default_factory=AudioFormatConfig)
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


_ENGINE_FIELDS = (
    "auto_start",
    "auto_start_dictation",
    "listen_default_segmentation_mode",
    "dictation_default_segmentation_mode",
    "segmentation",
    "sound_feedback",
    "audio_source",
    "module",
    "vad",
)
_SEGMENTATION_FIELDS = (
    "trigger_words",
    "initial_silence_timeout_s",
    "end_of_speech_timeout_s",
)
_SOUND_FEEDBACK_FIELDS = ("enabled", "output_device")
_SERVER_FIELDS = ("host", "port")


def _parse_segmentation(data: Any) -> SegmentationConfig:
    """Parse and validate ``engine.segmentation`` (absent or null = the defaults)."""
    where = "engine.segmentation"
    block = _as_object(data, where, _SEGMENTATION_FIELDS)
    words = block.get("trigger_words", list(_DEFAULT_TRIGGER_WORDS))
    if not isinstance(words, list) or not all(
        isinstance(word, str) and word.strip() for word in words
    ):
        raise ValueError(
            f"Invalid {where}.trigger_words: {words!r}. "
            f"Must be a list of non-empty strings."
        )
    return SegmentationConfig(
        trigger_words=list(words),
        initial_silence_timeout_s=_as_seconds(
            block, "initial_silence_timeout_s", 10.0, where, allow_zero=False
        ),
        end_of_speech_timeout_s=_as_seconds(
            block, "end_of_speech_timeout_s", 5.0, where, allow_zero=False
        ),
    )


def _parse_sound_feedback(data: Any) -> SoundFeedbackConfig:
    """Parse and validate ``engine.sound_feedback`` (absent or null = the defaults)."""
    where = "engine.sound_feedback"
    block = _as_object(data, where, _SOUND_FEEDBACK_FIELDS)
    device = block.get("output_device")
    if device is not None and (
        isinstance(device, bool) or not isinstance(device, (str, int))
    ):
        raise ValueError(
            f"Invalid {where}.output_device: {device!r}. "
            f"Must be a device name, a device index or null."
        )
    return SoundFeedbackConfig(
        enabled=_as_bool(block, "enabled", True, where),
        output_device=device,
    )


def _parse_server(data: Any) -> ServerConfig:
    """Parse and validate the ``server`` block (absent or null = the defaults)."""
    block = _as_object(data, "server", _SERVER_FIELDS)
    host = block.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host:
        raise ValueError(f"Invalid server.host: {host!r}. Must be a non-empty string.")
    port = block.get("port", 8000)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError(
            f"Invalid server.port: {port!r}. Must be an integer from 0 to 65535."
        )
    return ServerConfig(host=host, port=port)


@dataclass
class ASREngineConfig:
    """Everything an ``ASREngine`` needs — the whole ``engine`` config block."""

    auto_start: bool = True
    auto_start_dictation: bool = False
    listen_default_segmentation_mode: str = "trigger_word"
    dictation_default_segmentation_mode: str = "trigger_word"
    segmentation: SegmentationConfig = field(default_factory=SegmentationConfig)
    sound_feedback: SoundFeedbackConfig = field(default_factory=SoundFeedbackConfig)
    audio_source: AudioSourceConfig = field(default_factory=AudioSourceConfig)
    module: ModuleConfig = field(default_factory=ModuleConfig)
    vad: VadConfig | None = None  # None: no VAD stage (specs/vad.md)

    @classmethod
    def from_dict(cls, engine_block: dict[str, Any]) -> ASREngineConfig:
        """Build (and validate) an ``ASREngineConfig`` from a raw ``engine`` block dict.

        This is the public in-memory constructor for direct importers of the
        library, and where all engine validation lives: it takes the object under
        the top-level ``"engine"`` key (not the whole config file) and validates it
        — segmentation modes in {utterance, trigger_word, timeout}, encoding in
        the two ``audio_format`` objects (positive rates and channel counts,
        encoding in {linear16, mulaw}),
        ``auto_start_dictation`` requires ``auto_start``, ``module.type`` is
        required, and the optional ``vad`` block's ``type`` and gate parameters. ``from_json``/``from_json_file`` layer JSON string/file loading
        over it, and ``MCPServerConfig.from_dict`` delegates its ``engine`` block
        here, so every entry point validates identically.

        No environment variables are read: ``module`` extra fields are carried
        through unchanged, and the module resolves its own ``api_key``/``api_key_env``
        later at engine construction — so a config with unset credentials still
        builds here.
        """
        if not isinstance(engine_block, dict):
            raise ValueError("Invalid engine config: must be an object.")
        # `audio` is the old name of `audio_source`: _parse_audio_source refuses
        # it with a message naming the new block, so it is let through here.
        _as_object(engine_block, "engine", (*_ENGINE_FIELDS, "audio"))

        auto_start = _as_bool(engine_block, "auto_start", True, "engine")
        auto_start_dictation = _as_bool(
            engine_block, "auto_start_dictation", False, "engine"
        )
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

        segmentation = _parse_segmentation(engine_block.get("segmentation"))
        sound_feedback = _parse_sound_feedback(engine_block.get("sound_feedback"))

        module_data = engine_block.get("module")
        if module_data is None:
            module_data = {}
        if not isinstance(module_data, dict):
            raise ValueError("Invalid engine.module: must be an object.")
        module_type = module_data.get("type")
        if not module_type:
            raise ValueError("Config is missing required field: engine.module.type")
        if not isinstance(module_type, str):
            raise ValueError(
                f"Invalid engine.module.type: {module_type!r}. Must be a string."
            )
        # `type` and `audio_format` are the engine's; the rest is the module's own.
        extra = {
            k: v for k, v in module_data.items() if k not in ("type", "audio_format")
        }
        # An old key (`deepgram_v1`) is stored as its new one, with a warning.
        module = ModuleConfig(
            type=canonical_module_type(module_type),
            audio_format=_parse_audio_format(
                module_data.get("audio_format"), "engine.module.audio_format"
            ),
            extra=extra,
        )

        audio_source = _parse_audio_source(engine_block)
        vad = _parse_vad(engine_block.get("vad"))

        return cls(
            auto_start=auto_start,
            auto_start_dictation=auto_start_dictation,
            listen_default_segmentation_mode=listen_default,
            dictation_default_segmentation_mode=dictation_default,
            segmentation=segmentation,
            sound_feedback=sound_feedback,
            audio_source=audio_source,
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
        if not isinstance(data, dict):
            raise ValueError("Invalid config: must be an object.")
        _as_object(data, "config", ("server", "engine"))
        server = _parse_server(data.get("server"))
        engine_block = data.get("engine")
        engine = ASREngineConfig.from_dict({} if engine_block is None else engine_block)
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
