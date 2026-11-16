"""ASR module registry.

No backend is the default and no provider SDK is imported here: built-in entries
are ``LazyModule``s, imported only when their type is selected, so a provider's
optional extra (e.g. ``asr-engine[deepgram]``) is only needed by the module that
uses it. See specs/asr-module-interface.md "Module Registration".
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from asr_engine.modules.base import ASRModule

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LazyModule:
    """A registry entry resolved by import on first use."""

    import_path: str  # "package.module:ClassName"
    extra: str | None = None  # pip extra providing its dependencies; None = core


REGISTRY: dict[str, LazyModule | type[ASRModule]] = {
    # Scripted test double — for tests only, not an ASR backend (specs/modules/fake.md).
    "fake": LazyModule("asr_engine.modules.fake.module:FakeASRModule"),
    "deepgram-v1": LazyModule(
        "asr_engine.modules.deepgram.v1:DeepgramV1Module", extra="deepgram"
    ),
    "deepgram-v2": LazyModule(
        "asr_engine.modules.deepgram.v2:DeepgramV2Module", extra="deepgram"
    ),
    "mistral-realtime": LazyModule(
        "asr_engine.modules.mistral.realtime:MistralRealtimeModule", extra="mistral"
    ),
    # A protocol module: any server speaking OpenAI's Realtime transcription
    # protocol (specs/modules/openai-realtime-compatible.md).
    "openai-realtime-compatible": LazyModule(
        "asr_engine.modules.openai_realtime_compatible.module:"
        "OpenAIRealtimeCompatibleModule",
        extra="openai-compatible",
    ),
    # A protocol module for batch models: any server implementing OpenAI's
    # POST /v1/audio/transcriptions (specs/modules/openai-transcriptions-compatible.md).
    "openai-transcriptions-compatible": LazyModule(
        "asr_engine.modules.openai_transcriptions_compatible.module:"
        "OpenAITranscriptionsCompatibleModule",
        extra="openai-compatible",
    ),
    # No `extra`, as for kyutai below: mlx_audio/module.py imports nothing
    # third-party and raises its own install hint (the mlx-audio extra is Apple
    # Silicon only).
    "mlx-audio": LazyModule("asr_engine.modules.mlx_audio.module:MlxAudioModule"),
    # No `extra`: kyutai/module.py imports nothing third-party, and the module raises its
    # own install hint naming the backend's extra (kyutai-mlx / kyutai-torch).
    "kyutai": LazyModule("asr_engine.modules.kyutai.module:KyutaiModule"),
}


# Old registry keys, kept working: a key used to be spelled with `_`, and module
# names are now written with `-` (specs/asr-module-interface.md "Module names").
RENAMED_MODULE_TYPES: dict[str, str] = {
    "deepgram_v1": "deepgram-v1",
    "deepgram_v2": "deepgram-v2",
    "mistral_realtime": "mistral-realtime",
    "mlx_audio": "mlx-audio",
    "openai_realtime_compatible": "openai-realtime-compatible",
    "openai_transcriptions_compatible": "openai-transcriptions-compatible",
}


def canonical_module_type(module_type: str) -> str:
    """Return the current registry key for *module_type*.

    An old key from ``RENAMED_MODULE_TYPES`` maps to its new key and logs a
    deprecation warning naming it; any other value is returned unchanged.
    """
    new = RENAMED_MODULE_TYPES.get(module_type)
    if new is None:
        return module_type
    log.warning(
        "ASR module type '%s' is deprecated, use '%s' instead", module_type, new
    )
    return new


def _is_own_module(name: str | None) -> bool:
    return name is not None and (name == "asr_engine" or name.startswith("asr_engine."))


def resolve_module_class(module_type: str | None) -> type[ASRModule]:
    """Return the ASRModule class registered under *module_type*.

    An old key from ``RENAMED_MODULE_TYPES`` resolves to its new key, with a
    deprecation warning. Raises ``ValueError`` for an unknown type, and ``ImportError`` naming the extra
    to install when a lazy entry's third-party dependency is missing.
    """
    if module_type is not None:
        module_type = canonical_module_type(module_type)
    entry = REGISTRY.get(module_type) if module_type is not None else None
    if entry is None:
        available = ", ".join(sorted(REGISTRY)) or "(none)"
        raise ValueError(f"Unknown ASR type '{module_type}'. Available: {available}")
    if not isinstance(entry, LazyModule):
        return entry  # registered eagerly as a class

    module_name, _, class_name = entry.import_path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if entry.extra is None or _is_own_module(exc.name):
            raise
        raise ImportError(
            f"ASR module '{module_type}' requires the optional dependency "
            f"'{entry.extra}' which is not installed. Install it with: "
            f"pip install 'asr-engine[{entry.extra}]' "
            f"(from source: uv sync --extra {entry.extra})"
        ) from exc
    return getattr(module, class_name)


def load_module(asr_config: dict) -> ASRModule:
    """Instantiate the ASR module specified by asr_config["type"]."""
    module_cls = resolve_module_class(asr_config.get("type"))
    module_config = {k: v for k, v in asr_config.items() if k != "type"}
    return module_cls(config=module_config)
