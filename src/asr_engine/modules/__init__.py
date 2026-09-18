"""ASR module registry.

No backend is the default and no provider SDK is imported here: built-in entries
are ``LazyModule``s, imported only when their type is selected, so a provider's
optional extra (e.g. ``asr-engine[deepgram]``) is only needed by the module that
uses it. See specs/asr-module-interface.md "Module Registration".
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from asr_engine.modules.base import ASRModule


@dataclass(frozen=True)
class LazyModule:
    """A registry entry resolved by import on first use."""

    import_path: str  # "package.module:ClassName"
    extra: str | None = None  # pip extra providing its dependencies; None = core


REGISTRY: dict[str, LazyModule | type[ASRModule]] = {
    # Scripted test double — for tests only, not an ASR backend (specs/fake-module.md).
    "fake": LazyModule("asr_engine.modules.fake:FakeASRModule"),
    "deepgram_v1": LazyModule(
        "asr_engine.modules.deepgram_v1:DeepgramV1Module", extra="deepgram"
    ),
    "deepgram_v2": LazyModule(
        "asr_engine.modules.deepgram_v2:DeepgramV2Module", extra="deepgram"
    ),
    # No `extra`: kyutai.py imports nothing third-party, and the module raises its
    # own install hint naming the backend's extra (kyutai-mlx / kyutai-torch).
    "kyutai": LazyModule("asr_engine.modules.kyutai:KyutaiModule"),
}


def _is_own_module(name: str | None) -> bool:
    return name is not None and (name == "asr_engine" or name.startswith("asr_engine."))


def resolve_module_class(module_type: str | None) -> type[ASRModule]:
    """Return the ASRModule class registered under *module_type*.

    Raises ``ValueError`` for an unknown type, and ``ImportError`` naming the extra
    to install when a lazy entry's third-party dependency is missing.
    """
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
