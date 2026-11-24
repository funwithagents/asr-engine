from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from asr_engine.errors import ConfigError, MissingSecretError
from asr_engine.modules.fields import Field, field_problems, read_fields

__all__ = [
    "ASREngineConfig",
    "AudioFormatConfig",
    "AudioSourceConfig",
    "ConfigError",
    "MCPServerConfig",
    "MissingSecretError",
    "ModuleConfig",
    "SegmentationConfig",
    "ServerConfig",
    "SoundFeedbackConfig",
    "VadConfig",
    "engine_problems",
]

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

SEGMENT_MODES = ("utterance", "trigger_word", "timeout")
ENCODINGS = ("linear16", "mulaw")

# A problem in a config document: a JSON Pointer into it ("" for the document as
# a whole) and its message (specs/setup.md "check_config").
Problem = tuple[str, str]


# --- The engine block's own fields, declared once (specs/setup.md) -------------
#
# Read by `from_dict`, which raises on the first problem, and by
# `asr_engine.setup`, which reports them all and draws the form from them.

ENGINE_FIELDS = (
    Field(
        "auto_start",
        "boolean",
        "Start listening as soon as the MCP server starts.",
        default=True,
        advanced=True,
    ),
    Field(
        "auto_start_dictation",
        "boolean",
        "Start a dictation session as soon as the MCP server starts; needs "
        "'auto_start'.",
        default=False,
        advanced=True,
    ),
    Field(
        "listen_default_segmentation_mode",
        "string",
        "How a listen() call cuts the speech into segments, unless it says.",
        default="trigger_word",
        choices=SEGMENT_MODES,
        advanced=True,
    ),
    Field(
        "dictation_default_segmentation_mode",
        "string",
        "How a dictation cuts the speech into segments, unless it says.",
        default="trigger_word",
        choices=SEGMENT_MODES,
        advanced=True,
    ),
)

SEGMENTATION_FIELDS = (
    Field(
        "initial_silence_timeout_s",
        "number",
        "In 'timeout' mode, the silence from a segment's start that closes it, in "
        "seconds.",
        default=10.0,
        exclusive_minimum=0,
    ),
    Field(
        "end_of_speech_timeout_s",
        "number",
        "In 'timeout' mode, the silence after the last word that closes a "
        "segment, in seconds.",
        default=5.0,
        exclusive_minimum=0,
    ),
)
# Not a `Field` kind (a list of strings): checked by `_segmentation_problems`.
TRIGGER_WORDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": "The words that close a segment in 'trigger_word' mode.",
    "default": list(_DEFAULT_TRIGGER_WORDS),
}

SOUND_FEEDBACK_FIELDS = (
    Field(
        "enabled",
        "boolean",
        "Play a short sound when a listen() starts and stops.",
        default=True,
    ),
    Field(
        "output_device",
        ("string", "integer"),
        "The device the sounds play on, by name or index. Absent, the system's "
        "default.",
        minimum=0,
        advanced=True,
    ),
)

AUDIO_FORMAT_FIELDS = (
    Field(
        "sample_rate",
        "integer",
        "The sample rate, in Hz. Absent, the default.",
        exclusive_minimum=0,
    ),
    Field(
        "channels",
        "integer",
        "The number of channels. Absent, the default.",
        exclusive_minimum=0,
    ),
    Field(
        "encoding",
        "string",
        "The sample encoding. Absent, the default.",
        choices=ENCODINGS,
    ),
)

# engine.audio_source.type → the fields that type takes, besides `type` itself.
AUDIO_SOURCE_TYPES: dict[str, tuple[str, ...]] = {
    "input-device": ("device", "audio_format"),
    "file": ("audio_file", "trailing_silence_s"),
    "custom": (),
}
DEFAULT_AUDIO_SOURCE_TYPE = "input-device"

INPUT_DEVICE_FIELDS = (
    Field(
        "device",
        "string",
        "The input device, by name. Absent, the system's default.",
        options="input-devices",
    ),
)
FILE_SOURCE_FIELDS = (
    Field(
        "audio_file",
        "string",
        "The audio file played into the engine (WAV, MP3, …): its path.",
        required=True,
        message="Config is missing required field: engine.audio_source.audio_file "
        "(type 'file'), the path of an audio file",
    ),
    Field(
        "trailing_silence_s",
        "number",
        "Silence added after the file ends, in seconds, so its last words are "
        "finalized.",
        default=0.0,
        minimum=0,
    ),
)

_THRESHOLD_MESSAGE = (
    "Invalid engine.vad.threshold: must be a number strictly between 0 and 1."
)
VAD_GATE_FIELDS = (
    Field(
        "threshold",
        "number",
        "The speech probability above which a frame is speech, strictly between "
        "0 and 1.",
        default=0.5,
        exclusive_minimum=0,
        maximum=1,
        message=_THRESHOLD_MESSAGE,
    ),
    Field(
        "min_speech_duration_ms",
        "integer",
        "The speech needed to open an utterance, in milliseconds.",
        default=250,
        minimum=0,
        advanced=True,
    ),
    Field(
        "min_silence_duration_ms",
        "integer",
        "The silence that ends an utterance, in milliseconds.",
        default=500,
        minimum=1,
    ),
    Field(
        "speech_pad_ms",
        "integer",
        "The audio kept from before the speech started, in milliseconds.",
        default=300,
        minimum=0,
        advanced=True,
    ),
)

SERVER_FIELDS = (
    Field(
        "host",
        "string",
        "The address the MCP server listens on.",
        default="127.0.0.1",
    ),
    Field(
        "port",
        "integer",
        "The port the MCP server listens on.",
        default=8000,
        minimum=0,
        maximum=65535,
    ),
)


def _names(fields: tuple[Field, ...], *others: str) -> tuple[str, ...]:
    return (*(f.name for f in fields), *others)


ENGINE_KEYS = _names(
    ENGINE_FIELDS, "segmentation", "sound_feedback", "audio_source", "module", "vad"
)


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
    """Parse *text* as JSON, raising a clear ``ConfigError`` on malformed input.

    *source* (a file path, when the JSON came from a file) is included in the
    error so a bad config file names itself.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        where = f" ({source})" if source else ""
        raise ConfigError(f"Config is not valid JSON{where}: {exc}") from exc


# --- Every problem of an engine block ------------------------------------------


def _at(pointer: str, problems: list[tuple[str, str]]) -> list[Problem]:
    return [(f"{pointer}/{name}", message) for name, message in problems]


def _block_problems(
    data: Any, pointer: str, where: str, fields: tuple[Field, ...], *others: str
) -> list[Problem]:
    """The problems of an optional object: absent or null is fine; an object
    holds only *fields* and *others* (the caller checks those), each field valid.
    *where* is the object's dotted path, for the messages."""
    if data is None:
        return []
    if not isinstance(data, dict):
        return [(pointer, f"Invalid {where}: must be an object or null.")]
    known = _names(fields, *others)
    unknown = sorted(str(key) for key in set(data) - set(known))
    if unknown:
        return [
            (
                pointer,
                f"Invalid {where}: unknown field(s) {', '.join(unknown)}. "
                f"Fields: {', '.join(known)}.",
            )
        ]
    return _at(pointer, field_problems(data, fields, label=where))


def _segmentation_problems(data: Any) -> list[Problem]:
    where = "engine.segmentation"
    found = _block_problems(
        data, "/segmentation", where, SEGMENTATION_FIELDS, "trigger_words"
    )
    if not found and isinstance(data, dict) and "trigger_words" in data:
        words = data["trigger_words"]
        if not isinstance(words, list) or not all(
            isinstance(word, str) and word.strip() for word in words
        ):
            found.append(
                (
                    "/segmentation/trigger_words",
                    f"Invalid {where}.trigger_words: {words!r}. "
                    f"Must be a list of non-empty strings.",
                )
            )
    return found


def _audio_source_problems(engine_block: dict[str, Any]) -> list[Problem]:
    if "audio" in engine_block:
        return [
            (
                "/audio",
                "engine.audio is now engine.audio_source, with a 'type' "
                '("input-device", "file" or "custom"), e.g. '
                '"audio_source": {"type": "input-device", "device": null}.',
            )
        ]
    data = engine_block.get("audio_source")
    if data is None:
        return []
    if not isinstance(data, dict):
        return [
            ("/audio_source", "Invalid engine.audio_source: must be an object or null.")
        ]
    source_type = data.get("type", DEFAULT_AUDIO_SOURCE_TYPE)
    if source_type not in AUDIO_SOURCE_TYPES:
        return [
            (
                "/audio_source/type",
                f"Invalid engine.audio_source.type: '{source_type}'. "
                f"Must be one of {', '.join(AUDIO_SOURCE_TYPES)}.",
            )
        ]
    allowed = AUDIO_SOURCE_TYPES[source_type]
    misplaced = sorted(set(data) - {"type", *allowed})
    if misplaced:
        takes = ", ".join(allowed) if allowed else "no other field"
        return [
            (
                "/audio_source",
                f"Invalid engine.audio_source: {', '.join(misplaced)} does not apply "
                f"to type '{source_type}', which takes {takes}.",
            )
        ]
    label = "engine.audio_source"
    if source_type == "input-device":
        return _at(
            "/audio_source", field_problems(data, INPUT_DEVICE_FIELDS, label=label)
        ) + _block_problems(
            data.get("audio_format"),
            "/audio_source/audio_format",
            "engine.audio_source.audio_format",
            AUDIO_FORMAT_FIELDS,
        )
    if source_type == "file":
        return _at(
            "/audio_source", field_problems(data, FILE_SOURCE_FIELDS, label=label)
        )
    return []


def _module_problems(data: Any) -> list[Problem]:
    """The engine's part of ``engine.module``: an object with a ``type``, and the
    ``audio_format`` the engine reads. The rest is the module's to check."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return [("/module", "Invalid engine.module: must be an object.")]
    module_type = data.get("type")
    found: list[Problem] = []
    if not module_type:
        found.append(
            ("/module/type", "Config is missing required field: engine.module.type")
        )
    elif not isinstance(module_type, str):
        found.append(
            (
                "/module/type",
                f"Invalid engine.module.type: {module_type!r}. Must be a string.",
            )
        )
    return found + _block_problems(
        data.get("audio_format"),
        "/module/audio_format",
        "engine.module.audio_format",
        AUDIO_FORMAT_FIELDS,
    )


def _vad_problems(data: Any) -> list[Problem]:
    """The engine's part of ``engine.vad``: a ``type`` and the gate's parameters.
    The rest is the VAD module's to check."""
    if data is None:
        return []
    if not isinstance(data, dict):
        return [("/vad", "Invalid engine.vad: must be an object or null.")]
    found: list[Problem] = []
    vad_type = data.get("type")
    if not isinstance(vad_type, str) or not vad_type:
        found.append(
            (
                "/vad/type",
                "Config is missing required field: engine.vad.type "
                "(omit the engine.vad block to run without VAD)",
            )
        )
    found += _at("/vad", field_problems(data, VAD_GATE_FIELDS, label="engine.vad"))
    threshold = data.get("threshold")
    if not isinstance(threshold, bool) and threshold == 1:
        found.append(("/vad/threshold", _THRESHOLD_MESSAGE))
    return found


def engine_problems(engine_block: Any) -> list[Problem]:
    """Every problem of the engine's own part of an ``engine`` block, each a JSON
    Pointer into the block and a message; empty when it is accepted.

    This is what ``ASREngineConfig.from_dict`` refuses, in the same words (it
    raises the first). The module's own fields, and the VAD module's, are theirs
    to check: ``asr_engine.setup.check_config`` adds them.
    """
    if not isinstance(engine_block, dict):
        return [("", "Invalid engine config: must be an object.")]
    found: list[Problem] = []
    unknown = sorted(str(key) for key in set(engine_block) - {*ENGINE_KEYS, "audio"})
    if unknown:
        found.append(
            (
                "",
                f"Invalid engine: unknown field(s) {', '.join(unknown)}. "
                f"Fields: {', '.join(ENGINE_KEYS)}.",
            )
        )
    own = field_problems(engine_block, ENGINE_FIELDS, label="engine")
    found += _at("", own)
    if (
        not own
        and engine_block.get("auto_start_dictation", False)
        and not engine_block.get("auto_start", True)
    ):
        found.append(
            (
                "/auto_start_dictation",
                "Invalid engine config: auto_start_dictation requires auto_start=true.",
            )
        )
    found += _segmentation_problems(engine_block.get("segmentation"))
    found += _block_problems(
        engine_block.get("sound_feedback"),
        "/sound_feedback",
        "engine.sound_feedback",
        SOUND_FEEDBACK_FIELDS,
    )
    found += _audio_source_problems(engine_block)
    found += _module_problems(engine_block.get("module"))
    found += _vad_problems(engine_block.get("vad"))
    return found


def _raise_first(problems: list[Problem]) -> None:
    if problems:
        raise ConfigError(problems[0][1])


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


def _read(data: Any, fields: tuple[Field, ...], label: str) -> dict[str, Any]:
    """The values of an object whose problems were found empty."""
    return read_fields(data or {}, fields, label=label, error=ConfigError)


def _audio_format(data: Any) -> AudioFormatConfig:
    return AudioFormatConfig(**_read(data, AUDIO_FORMAT_FIELDS, "audio_format"))


def _audio_source(data: Any) -> AudioSourceConfig:
    if data is None:
        return AudioSourceConfig()
    source_type = data.get("type", DEFAULT_AUDIO_SOURCE_TYPE)
    if source_type == "input-device":
        values = _read(data, INPUT_DEVICE_FIELDS, "engine.audio_source")
        return AudioSourceConfig(
            type=source_type,
            device=values["device"],
            audio_format=_audio_format(data.get("audio_format")),
        )
    if source_type == "file":
        values = _read(data, FILE_SOURCE_FIELDS, "engine.audio_source")
        return AudioSourceConfig(
            type=source_type,
            audio_file=values["audio_file"],
            trailing_silence_s=values["trailing_silence_s"],
        )
    return AudioSourceConfig(type=source_type)


def _vad(data: Any) -> VadConfig | None:
    if data is None:
        return None
    values = _read(data, VAD_GATE_FIELDS, "engine.vad")
    return VadConfig(
        type=data["type"],
        **values,
        extra={k: v for k, v in data.items() if k not in ("type", *values)},
    )


def _parse_server(data: Any) -> ServerConfig:
    """Parse and validate the ``server`` block (absent or null = the defaults)."""
    _raise_first(_block_problems(data, "/server", "server", SERVER_FIELDS))
    return ServerConfig(**_read(data, SERVER_FIELDS, "server"))


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

        Every problem is a ``ConfigError`` (a ``ValueError``): the first of
        ``engine_problems``. No environment variables are read: ``module`` extra
        fields are carried through unchanged, and the module resolves its key
        (``api_key_env``) later at engine construction — so a config with unset
        credentials still builds here.
        """
        _raise_first(engine_problems(engine_block))
        # Valid from here on: each part is read with its defaults.
        from asr_engine.modules import canonical_module_type

        values = _read(engine_block, ENGINE_FIELDS, "engine")
        segmentation = engine_block.get("segmentation") or {}
        sound_feedback = engine_block.get("sound_feedback")
        module_data = engine_block.get("module") or {}
        return cls(
            **values,
            segmentation=SegmentationConfig(
                trigger_words=list(
                    segmentation.get("trigger_words", _DEFAULT_TRIGGER_WORDS)
                ),
                **_read(segmentation, SEGMENTATION_FIELDS, "engine.segmentation"),
            ),
            sound_feedback=SoundFeedbackConfig(
                **_read(sound_feedback, SOUND_FEEDBACK_FIELDS, "engine.sound_feedback")
            ),
            audio_source=_audio_source(engine_block.get("audio_source")),
            # `type` and `audio_format` are the engine's; the rest is the
            # module's own. An old key (`deepgram_v1`) is stored as its new one,
            # with a warning.
            module=ModuleConfig(
                type=canonical_module_type(module_data["type"]),
                audio_format=_audio_format(module_data.get("audio_format")),
                extra={
                    k: v
                    for k, v in module_data.items()
                    if k not in ("type", "audio_format")
                },
            ),
            vad=_vad(engine_block.get("vad")),
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
            raise ConfigError("Invalid config: must be an object.")
        unknown = sorted(str(key) for key in set(data) - {"server", "engine"})
        if unknown:
            raise ConfigError(
                f"Invalid config: unknown field(s) {', '.join(unknown)}. "
                f"Fields: server, engine."
            )
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
    """Raise ConfigError if config.module.type is not a key in *registry*.

    An old key from ``RENAMED_MODULE_TYPES`` is checked under its new key.
    """
    from asr_engine.modules import RENAMED_MODULE_TYPES

    module_type = RENAMED_MODULE_TYPES.get(config.module.type, config.module.type)
    if module_type not in registry:
        available = ", ".join(sorted(registry.keys())) or "(none)"
        raise ConfigError(f"Unknown ASR type '{module_type}'. Available: {available}")
