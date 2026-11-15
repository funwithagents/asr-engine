"""The example server configs in ``examples/config/`` stay valid.

Each file must parse as a whole server config, name a registered ASR module, and
— when it enables a VAD — name a registered VAD and pair it with a module that
supports gating, which is what the engine checks at construction. Nothing is
connected and no model is loaded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from asr_engine.config import MCPServerConfig
from asr_engine.modules import resolve_module_class
from asr_engine.vad import REGISTRY as VAD_REGISTRY

CONFIG_DIR = Path(__file__).parents[2] / "examples" / "config"
CONFIGS = sorted(CONFIG_DIR.glob("*.json"))


def test_there_is_one_example_config_per_module() -> None:
    assert [p.stem for p in CONFIGS] == [
        "deepgram_v1",
        "deepgram_v2",
        "kyutai",
        "mistral_realtime",
        "mlx_audio",
        "openai_realtime_compatible",
    ]


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_example_config_is_a_valid_engine_setup(path: Path) -> None:
    engine = MCPServerConfig.from_json_file(str(path)).engine

    # Named after the module it configures.
    assert engine.module.type == path.stem
    module_cls = resolve_module_class(engine.module.type)

    if engine.vad is None:
        assert module_cls.VAD_GATING != "required"
    else:
        assert engine.vad.type in VAD_REGISTRY
        assert module_cls.VAD_GATING != "unsupported"


def test_the_cloud_examples_gate_with_their_recommended_setting() -> None:
    def vad(name: str):
        return MCPServerConfig.from_json_file(
            str(CONFIG_DIR / f"{name}.json")
        ).engine.vad

    for name in ("deepgram_v1", "mistral_realtime", "openai_realtime_compatible"):
        gate = vad(name)
        assert gate is not None and gate.min_silence_duration_ms == 500
    flux = vad("deepgram_v2")
    assert flux is not None and flux.min_silence_duration_ms == 1500
    assert vad("kyutai") is None
    # A batch module cannot run ungated; the default window suits it.
    batch = vad("mlx_audio")
    assert batch is not None and batch.min_silence_duration_ms == 500
