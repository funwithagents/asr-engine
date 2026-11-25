"""The example configs stay valid.

Each ``examples/configs/<module>.json`` is an engine block, exactly what the setup
contract describes: ``check_config`` finds no problem in it, it names the module
it is named after, and — when it enables a VAD — pairs it with a module that
supports gating. ``examples/mcp/config.json`` is a whole MCP server config, and
its engine block can be replaced by any of them. Nothing is connected and no
model is loaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asr_engine import setup
from asr_engine.config import ASREngineConfig, MCPServerConfig
from asr_engine.modules import resolve_module_class
from asr_engine.vad import REGISTRY as VAD_REGISTRY

EXAMPLES = Path(__file__).parents[2] / "examples"
CONFIG_DIR = EXAMPLES / "configs"
CONFIGS = sorted(CONFIG_DIR.glob("*.json"))
MCP_CONFIG = EXAMPLES / "mcp" / "config.json"


def test_there_is_one_example_config_per_module() -> None:
    assert [p.stem for p in CONFIGS] == [
        "deepgram-v1",
        "deepgram-v2",
        "kyutai",
        "mistral-realtime",
        "mlx-audio",
        "openai-realtime-compatible",
        "openai-transcriptions-compatible",
    ]


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_example_config_is_an_engine_block_the_contract_accepts(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))

    assert setup.check_config(document) == []
    engine = ASREngineConfig.from_json_file(str(path))
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
        return ASREngineConfig.from_json_file(str(CONFIG_DIR / f"{name}.json")).vad

    for name in ("deepgram-v1", "mistral-realtime", "openai-realtime-compatible"):
        gate = vad(name)
        assert gate is not None and gate.min_silence_duration_ms == 500
    flux = vad("deepgram-v2")
    assert flux is not None and flux.min_silence_duration_ms == 1500
    assert vad("kyutai") is None
    # A batch module cannot run ungated; the default window suits it.
    for name in ("mlx-audio", "openai-transcriptions-compatible"):
        batch = vad(name)
        assert batch is not None and batch.min_silence_duration_ms == 500


def test_the_mcp_example_is_a_server_config_ready_to_run() -> None:
    config = MCPServerConfig.from_json_file(str(MCP_CONFIG))
    document = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))

    assert (config.server.host, config.server.port) == ("0.0.0.0", 8000)
    # The fake module: nothing to install, no key.
    assert config.engine.module.type == "fake"
    assert setup.check_config(document["engine"]) == []


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_any_example_config_replaces_the_mcp_example_s_engine_block(
    path: Path,
) -> None:
    server = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))["server"]
    engine = json.loads(path.read_text(encoding="utf-8"))

    config = MCPServerConfig.from_dict({"server": server, "engine": engine})

    assert config.engine.module.type == path.stem
