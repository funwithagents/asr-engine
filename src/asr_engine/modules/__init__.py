"""ASR module registry.

No backend is the default. Every module file imports its provider's library only
when it is used (``imports()``, then the connection or the model load), so the
registry holds the classes themselves: what each module declares — its title, its
extra, its fields — is readable on the base install (specs/setup.md), and a
provider's optional extra (e.g. ``asr-engine[deepgram]``) is only needed by the
module that uses it. See specs/asr-module-interface.md "Module Registration".
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from asr_engine.errors import ConfigError
from asr_engine.modules.base import ASRModule
from asr_engine.modules.deepgram.v1 import DeepgramV1Module
from asr_engine.modules.deepgram.v2 import DeepgramV2Module
from asr_engine.modules.fake.module import FakeASRModule
from asr_engine.modules.kyutai.module import KyutaiModule
from asr_engine.modules.mistral.realtime import MistralRealtimeModule
from asr_engine.modules.mlx_audio.module import MlxAudioModule
from asr_engine.modules.openai_realtime_compatible.module import (
    OpenAIRealtimeCompatibleModule,
)
from asr_engine.modules.openai_transcriptions_compatible.module import (
    OpenAITranscriptionsCompatibleModule,
)

log = logging.getLogger(__name__)


REGISTRY: dict[str, type[ASRModule]] = {
    # Scripted test double, and the module a demo starts on: it needs nothing
    # (specs/modules/fake.md).
    "fake": FakeASRModule,
    "deepgram-v1": DeepgramV1Module,
    "deepgram-v2": DeepgramV2Module,
    "mistral-realtime": MistralRealtimeModule,
    # A protocol module: any server speaking OpenAI's Realtime transcription
    # protocol (specs/modules/openai-realtime-compatible.md).
    "openai-realtime-compatible": OpenAIRealtimeCompatibleModule,
    # A protocol module for batch models: any server implementing OpenAI's
    # POST /v1/audio/transcriptions (specs/modules/openai-transcriptions-compatible.md).
    "openai-transcriptions-compatible": OpenAITranscriptionsCompatibleModule,
    # Apple Silicon only (the mlx-audio extra).
    "mlx-audio": MlxAudioModule,
    # One key over two backends, each with its own extra (kyutai-mlx /
    # kyutai-torch): `KyutaiModule.required_extras` picks it.
    "kyutai": KyutaiModule,
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


def resolve_module_class(module_type: str | None) -> type[ASRModule]:
    """Return the ASRModule class registered under *module_type*.

    An old key from ``RENAMED_MODULE_TYPES`` resolves to its new key, with a
    deprecation warning. Raises ``ConfigError`` for an unknown type. A missing
    extra is the module's to report, at construction (``imports()``).
    """
    if module_type is not None:
        module_type = canonical_module_type(module_type)
    entry = REGISTRY.get(module_type) if module_type is not None else None
    if entry is None:
        available = ", ".join(sorted(REGISTRY)) or "(none)"
        raise ConfigError(f"Unknown ASR type '{module_type}'. Available: {available}")
    return entry


def load_module(
    asr_config: dict, *, secrets: Mapping[str, str] | None = None
) -> ASRModule:
    """Instantiate the ASR module specified by asr_config["type"]; *secrets* go to
    a module that reads a key."""
    module_cls = resolve_module_class(asr_config.get("type"))
    module_config = {k: v for k, v in asr_config.items() if k != "type"}
    if module_cls.takes_secrets():
        return module_cls(config=module_config, secrets=secrets)  # pyright: ignore[reportCallIssue]
    return module_cls(config=module_config)
