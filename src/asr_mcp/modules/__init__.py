from __future__ import annotations

from asr_mcp.modules.base import ASRModule

# Maps asr.type string → ASRModule subclass.
# Deepgram will be added in plan 05.
REGISTRY: dict[str, type[ASRModule]] = {}


def load_module(asr_config: dict) -> ASRModule:
    """Instantiate the ASR module specified by asr_config["type"]."""
    asr_type = asr_config.get("type")
    if asr_type not in REGISTRY:
        raise ValueError(
            f"Unknown ASR module type {asr_type!r}. "
            f"Available: {sorted(REGISTRY)}"
        )
    module_config = {k: v for k, v in asr_config.items() if k != "type"}
    return REGISTRY[asr_type](config=module_config)
