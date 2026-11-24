"""Setting the engine up from an application, as data (specs/setup.md).

Seven functions over the engine block (the document ``ASREngineConfig.from_dict``
takes): what its config looks like (``config_schema``), which extras a config
needs (``required_extras``), what is wrong in the config itself
(``check_config``), what this machine is missing to run it (``check_setup``: the
extras, the keys, the model files), downloading the files (``prepare``), the
choices of a field (``options``), and one light real call to see it work
(``probe``). They implement the module contract of the ``module-setup`` project
and exchange JSON-compatible data only.

Each is the engine's own part (the audio source, the segmentation, the sound
feedback, the module choice, the VAD gate) plus the method of the same name on
the selected module and on the selected VAD module. All but ``prepare``,
``options`` and ``probe`` run on the base install, with no network. Nothing here
is imported by the engine itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from asr_engine.config import (
    AUDIO_FORMAT_FIELDS,
    ENGINE_FIELDS,
    FILE_SOURCE_FIELDS,
    INPUT_DEVICE_FIELDS,
    SEGMENTATION_FIELDS,
    SOUND_FEEDBACK_FIELDS,
    TRIGGER_WORDS_SCHEMA,
    VAD_GATE_FIELDS,
    ConfigError,
    Problem,
    engine_problems,
)
from asr_engine.modules import REGISTRY, RENAMED_MODULE_TYPES
from asr_engine.modules.base import ASRModule, Declared, format_for
from asr_engine.modules.fields import fields_schema

__all__ = [
    "check_config",
    "check_setup",
    "config_schema",
    "options",
    "prepare",
    "probe",
    "required_extras",
]

_DISTRIBUTION = "asr-engine"
# What the engine reads from a module block itself; the rest is the module's.
_ENGINE_MODULE_KEYS = ("type", "audio_format")
# What the gate reads from a VAD block; the rest is the VAD module's.
_GATE_KEYS = ("type", *(f.name for f in VAD_GATE_FIELDS))


def _vad_registry() -> dict[str, type[Declared]]:
    from asr_engine.vad import REGISTRY as VAD_REGISTRY

    return dict(VAD_REGISTRY)


def _object(
    title: str,
    description: str,
    fields: Any,
    *,
    advanced: bool = False,
    nullable: bool = False,
) -> dict[str, Any]:
    schema = fields_schema(fields)
    node: dict[str, Any] = {
        "type": ["object", "null"] if nullable else "object",
        "title": title,
        "description": description,
        "properties": schema["properties"],
    }
    if schema["required"]:
        node["required"] = schema["required"]
    if advanced:
        node["x-advanced"] = True
    return node


def _branch(name: str, cls: type[Declared]) -> dict[str, Any]:
    schema = cls.schema()
    return {
        "title": f"{cls.title} ({cls.kind})",
        "required": schema["required"],
        "properties": {"type": {"const": name}, **schema["properties"]},
    }


def _segmentation() -> dict[str, Any]:
    node = _object(
        "Segmentation",
        "How the speech is cut into segments.",
        SEGMENTATION_FIELDS,
        advanced=True,
    )
    node["properties"] = {
        "trigger_words": dict(TRIGGER_WORDS_SCHEMA),
        **node["properties"],
    }
    return node


def config_schema() -> dict[str, Any]:
    """The engine block as a JSON Schema, with the contract's envelope. The same
    value on every call, whatever is installed."""
    audio_format = _object(
        "Audio format",
        "The format the audio is taken in. A field left out is the default.",
        AUDIO_FORMAT_FIELDS,
        advanced=True,
    )
    vad_registry = _vad_registry()
    return {
        "contract": 1,
        "distribution": _DISTRIBUTION,
        "title": "Speech recognition",
        "schema": {
            "type": "object",
            "required": ["module"],
            "properties": {
                "module": {
                    "type": "object",
                    "title": "Module",
                    "description": "What turns speech into text: a provider's API "
                    "(hosted) or a model run on this machine (local).",
                    "required": ["type"],
                    "properties": {
                        "audio_format": {
                            **audio_format,
                            "description": "The format the module is fed. A field "
                            "left out is the module's own default.",
                        },
                    },
                    "oneOf": [_branch(name, cls) for name, cls in REGISTRY.items()],
                },
                "audio_source": {
                    "type": "object",
                    "title": "Input",
                    "description": "Where the audio comes from. Left out, the "
                    "system's default input device.",
                    "oneOf": [
                        {
                            "title": "Input device",
                            "properties": {
                                "type": {"const": "input-device"},
                                **fields_schema(INPUT_DEVICE_FIELDS)["properties"],
                                "audio_format": {
                                    **audio_format,
                                    "description": "The format the device is "
                                    "opened at. A field left out follows the "
                                    "module's.",
                                },
                            },
                        },
                        {
                            "title": "Audio file",
                            "required": ["audio_file"],
                            "properties": {
                                "type": {"const": "file"},
                                **fields_schema(FILE_SOURCE_FIELDS)["properties"],
                            },
                        },
                        {
                            "title": "Custom source (given in code)",
                            "description": "An audio source the application passes "
                            "to the engine.",
                            "properties": {"type": {"const": "custom"}},
                        },
                    ],
                },
                "vad": {
                    "type": "object",
                    "title": "Voice activity detection",
                    "description": "A gate before the module that passes speech "
                    "only and tells the module where each utterance ends. Some "
                    "modules need it, some cannot take it.",
                    "required": ["type"],
                    "properties": fields_schema(VAD_GATE_FIELDS)["properties"],
                    "oneOf": [_branch(name, cls) for name, cls in vad_registry.items()],
                },
                "segmentation": _segmentation(),
                "sound_feedback": _object(
                    "Sound feedback",
                    "The sounds played when a listen() starts and stops.",
                    SOUND_FEEDBACK_FIELDS,
                    advanced=True,
                ),
                **fields_schema(ENGINE_FIELDS)["properties"],
            },
        },
    }


def _module_class(config: Any) -> type[ASRModule] | None:
    """The class of the module the engine block selects, or None when it selects
    none that is registered. A renamed type resolves quietly."""
    module = config.get("module") if isinstance(config, dict) else None
    module_type = module.get("type") if isinstance(module, dict) else None
    if not isinstance(module_type, str):
        return None
    return REGISTRY.get(RENAMED_MODULE_TYPES.get(module_type, module_type))


def _vad_class(config: Any) -> type[Declared] | None:
    vad = config.get("vad") if isinstance(config, dict) else None
    vad_type = vad.get("type") if isinstance(vad, dict) else None
    if not isinstance(vad_type, str):
        return None
    return _vad_registry().get(vad_type)


def _module_block(config: dict[str, Any]) -> dict[str, Any]:
    """The module's own block: what the engine hands its constructor."""
    return {k: v for k, v in config["module"].items() if k not in _ENGINE_MODULE_KEYS}


def _vad_block(config: dict[str, Any]) -> dict[str, Any]:
    """The VAD module's own block: what the engine hands its constructor."""
    return {k: v for k, v in config["vad"].items() if k not in _GATE_KEYS}


def required_extras(config: dict[str, Any]) -> list[str]:
    """The extras this config needs: its module's, then its VAD's. Reads the two
    `type`s (and kyutai's `backend`), never raises."""
    found: list[str] = []
    cls = _module_class(config)
    if cls is not None:
        found += cls.required_extras(_module_block(config))
    vad = _vad_class(config)
    if vad is not None:
        found += vad.required_extras(_vad_block(config))
    return found


def _format_problems(cls: type[ASRModule], config: dict[str, Any]) -> list[Problem]:
    """An `engine.module.audio_format` field the selected module does not take."""
    requested = config["module"].get("audio_format") or {}
    try:
        rates = cls.sample_rates(_module_block(config))
    except ConfigError:
        return []  # reported by the module's own check
    found: list[Problem] = []
    for name in ("sample_rate", "channels", "encoding"):
        if requested.get(name) is None:
            continue
        try:
            format_for(cls, rates, **{name: requested[name]})
        except ConfigError as exc:
            found.append((f"/module/audio_format/{name}", str(exc)))
    return found


def check_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every problem of this engine block, each `{"path", "message"}` with a JSON
    Pointer into it: the engine's own, an unknown module or VAD type, the
    selected module's and VAD module's fields, the module's audio format, and
    whether the module runs with (or without) the VAD. Empty when the loader and
    the constructors accept it. Imports nothing, reads no environment."""
    from asr_engine.engine import gating_problem

    problems = engine_problems(config)
    if not isinstance(config, dict):
        return [{"path": path, "message": message} for path, message in problems]
    broken = {path for path, _ in problems}

    cls = None
    if not any(p == "/module" or p.startswith("/module/") for p in broken):
        cls = _module_class(config)
        if cls is None:
            module_type = config.get("module", {}).get("type")
            problems.append(
                (
                    "/module/type",
                    f"Unknown ASR type '{module_type}'. Available: "
                    f"{', '.join(sorted(REGISTRY))}",
                )
            )
        else:
            problems += [
                (f"/module/{name}" if name else "/module", message)
                for name, message in cls.check_config(_module_block(config))
            ]
            problems += _format_problems(cls, config)

    vad = config.get("vad")
    if isinstance(vad, dict) and not any(
        p == "/vad" or p.startswith("/vad/") for p in broken
    ):
        vad_cls = _vad_class(config)
        if vad_cls is None:
            problems.append(
                (
                    "/vad/type",
                    f"Unknown VAD type '{vad.get('type')}'. Available: "
                    f"{', '.join(sorted(_vad_registry()))}",
                )
            )
        else:
            problems += [
                (f"/vad/{name}" if name else "/vad", message)
                for name, message in vad_cls.check_config(_vad_block(config))
            ]
    if cls is not None:
        gating = gating_problem(cls, config["module"]["type"], vad is not None)
        if gating is not None:
            problems.append(("/vad", gating))
    return [{"path": path, "message": message} for path, message in problems]


def _selected(config: dict[str, Any]) -> list[tuple[type[Declared], dict[str, Any]]]:
    """The module, then the VAD module, of a config `check_config` accepts, each
    with its own block."""
    cls = _module_class(config)
    if cls is None:
        raise ConfigError("the config selects no registered ASR module")
    selected: list[tuple[type[Declared], dict[str, Any]]] = [
        (cls, _module_block(config))
    ]
    vad = _vad_class(config)
    if vad is not None:
        selected.append((vad, _vad_block(config)))
    return selected


def check_setup(config: dict[str, Any]) -> list[dict[str, Any]]:
    """What this machine is missing to run this config: the module's extra, its
    key, its model files, then the VAD's extra and model file. Local and fast:
    no network. Never raises."""
    items: list[dict[str, Any]] = []
    for cls, block in _selected(config):
        items += cls.check_setup(block)
    return items


def prepare(
    config: dict[str, Any],
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Download what `check_setup` reports missing and preparable: the module's
    model, the VAD's. Needs the extras installed; blocking and idempotent."""
    for cls, block in _selected(config):
        cls.prepare(block, on_progress)


def options(name: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """The choices of a named list: `"input-devices"`, the machine's input
    devices after the system default. An unknown name is empty."""
    if name != "input-devices":
        return []
    from asr_engine.audio import AudioCapture

    found: list[dict[str, Any]] = [
        {"label": "System default", "fields": {"device": None}}
    ]
    for device in AudioCapture.list_devices():
        found.append({"label": device, "fields": {"device": device}})
    return found


def probe(
    config: dict[str, Any], *, secrets: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Try this config for real, lightly: a hosted module makes one authenticated,
    free listing call (and finds its model there), a local one loads its model and
    releases it, the VAD loads its model and releases it. No microphone, no audio,
    no transcription. Reports `{"ok", "message", "duration_s"}`; never raises.
    *secrets* are keys given in code, looked up before the environment."""
    started = time.monotonic()
    said: list[str] = []
    try:
        problems = check_config(config)
        if problems:
            raise ConfigError(problems[0]["message"])
        for cls, block in _selected(config):
            said.append(cls.probe(block, secrets))
        ok, message = True, " ".join(said)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        ok = False
        message = " ".join([*said, str(exc) or type(exc).__name__])
    return {
        "ok": ok,
        "message": message,
        "duration_s": round(time.monotonic() - started, 3),
    }
