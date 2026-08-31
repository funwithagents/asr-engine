from __future__ import annotations

from asr_mcp.modules.base import ASRModule
from asr_mcp.modules.deepgram_v1 import DeepgramV1Module
from asr_mcp.modules.deepgram_v2 import DeepgramV2Module

REGISTRY: dict[str, type[ASRModule]] = {
    "deepgram_v1": DeepgramV1Module,
    "deepgram_v2": DeepgramV2Module,
}


def load_module(asr_config: dict) -> ASRModule:
    """Instantiate the ASR module specified by asr_config["type"]."""
    asr_type = asr_config.get("type")
    if asr_type not in REGISTRY:
        raise ValueError(
            f"Unknown ASR module type {asr_type!r}. Available: {sorted(REGISTRY)}"
        )
    module_config = {k: v for k, v in asr_config.items() if k != "type"}
    return REGISTRY[asr_type](config=module_config)
