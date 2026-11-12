"""VAD module registry.

Mirrors the ASR registry (``asr_engine.modules``): entries are lazy, so nothing
VAD-related is imported until ``engine.vad.type`` selects one, and a backend's
optional extra is only needed when it is used. See specs/vad.md "Registry".
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from asr_engine.vad.base import VADModule


@dataclass(frozen=True)
class LazyVAD:
    """A registry entry resolved by import on first use."""

    import_path: str  # "package.module:ClassName"
    extra: str | None = None  # pip extra providing its dependencies; None = core


REGISTRY: dict[str, LazyVAD | type[VADModule]] = {
    "silero": LazyVAD("asr_engine.vad.silero.module:SileroVAD", extra="silero"),
}


def _is_own_module(name: str | None) -> bool:
    return name is not None and (name == "asr_engine" or name.startswith("asr_engine."))


def resolve_vad_class(vad_type: str | None) -> type[VADModule]:
    """Return the VADModule class registered under *vad_type*.

    Raises ``ValueError`` for an unknown type, and ``ImportError`` naming the extra
    to install when a lazy entry's third-party dependency is missing.
    """
    entry = REGISTRY.get(vad_type) if vad_type is not None else None
    if entry is None:
        available = ", ".join(sorted(REGISTRY)) or "(none)"
        raise ValueError(f"Unknown VAD type '{vad_type}'. Available: {available}")
    if not isinstance(entry, LazyVAD):
        return entry  # registered eagerly as a class

    module_name, _, class_name = entry.import_path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if entry.extra is None or _is_own_module(exc.name):
            raise
        raise ImportError(
            f"VAD module '{vad_type}' requires the optional dependency "
            f"'{entry.extra}' which is not installed. Install it with: "
            f"pip install 'asr-engine[{entry.extra}]' "
            f"(from source: uv sync --extra {entry.extra})"
        ) from exc
    return getattr(module, class_name)
