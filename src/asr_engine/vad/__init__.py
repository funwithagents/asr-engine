"""VAD module registry.

Mirrors the ASR registry (``asr_engine.modules``): a module file imports its
backend's library only when the model loads, so the registry holds the classes,
whose declarations (title, extra, fields) the setup checks read, and a backend's
optional extra is only needed when it is used. See specs/vad.md "Registry".
"""

from __future__ import annotations

from asr_engine.errors import ConfigError
from asr_engine.vad.base import VADModule
from asr_engine.vad.silero.module import SileroVAD

REGISTRY: dict[str, type[VADModule]] = {
    "silero": SileroVAD,
}


def resolve_vad_class(vad_type: str | None) -> type[VADModule]:
    """Return the VADModule class registered under *vad_type*.

    Raises ``ConfigError`` for an unknown type. A missing extra is the module's to
    report, at construction (``imports()``).
    """
    entry = REGISTRY.get(vad_type) if vad_type is not None else None
    if entry is None:
        available = ", ".join(sorted(REGISTRY)) or "(none)"
        raise ConfigError(f"Unknown VAD type '{vad_type}'. Available: {available}")
    return entry
