"""Unit tests for asr_engine.config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asr_engine.config import (
    AppConfig,
    ASREngineConfig,
    ModuleConfig,
    load_config,
    validate_asr_type,
)


def write_config(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def _min(engine_overrides: dict | None = None, **top) -> dict:
    """Minimal valid config dict with a module.type, plus overrides."""
    engine = {"module": {"type": "deepgram"}}
    if engine_overrides:
        engine.update(engine_overrides)
    return {"engine": engine, **top}


# ---------------------------------------------------------------------------
# load_config — happy path
# ---------------------------------------------------------------------------


def test_load_config_full(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "server": {"host": "0.0.0.0", "port": 9000},
            "engine": {
                "auto_start": False,
                "segmentation_mode": "timeout",
                "listen_default_segmentation_mode": "timeout",
                "segmentation": {
                    "trigger_words": ["stop"],
                    "initial_silence_timeout_s": 7.0,
                    "end_of_speech_timeout_s": 2.0,
                },
                "sound_feedback": {"enabled": False, "output_device": "speakers"},
                "logging": {"level": "DEBUG"},
                "audio": {"device": "hw:0"},
                "module": {"type": "deepgram", "api_key": "secret", "language": "fr"},
            },
        },
    )
    cfg = load_config(path)

    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 9000
    assert cfg.engine.auto_start is False
    assert cfg.engine.segmentation_mode == "timeout"
    assert cfg.engine.listen_default_segmentation_mode == "timeout"
    assert cfg.engine.segmentation.trigger_words == ["stop"]
    assert cfg.engine.segmentation.initial_silence_timeout_s == 7.0
    assert cfg.engine.segmentation.end_of_speech_timeout_s == 2.0
    assert cfg.engine.sound_feedback.enabled is False
    assert cfg.engine.sound_feedback.output_device == "speakers"
    assert cfg.engine.logging.level == "DEBUG"
    assert cfg.engine.audio.device == "hw:0"
    assert cfg.engine.module.type == "deepgram"
    assert cfg.engine.module.extra == {"api_key": "secret", "language": "fr"}


def test_load_config_defaults(tmp_path: Path) -> None:
    """Omitting optional blocks applies defaults."""
    cfg = load_config(write_config(tmp_path, _min()))

    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.engine.auto_start is True
    assert cfg.engine.segmentation_mode == "utterance"
    assert cfg.engine.listen_default_segmentation_mode == "trigger_word"
    assert cfg.engine.segmentation.initial_silence_timeout_s == 10.0
    assert cfg.engine.segmentation.end_of_speech_timeout_s == 5.0
    assert "submit" in cfg.engine.segmentation.trigger_words
    assert cfg.engine.sound_feedback.enabled is True
    assert cfg.engine.sound_feedback.output_device is None
    assert cfg.engine.logging.level == "INFO"
    assert cfg.engine.audio.device is None
    assert cfg.engine.module.extra == {}


def test_load_config_partial_server_defaults(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _min(server={"port": 7777})))
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 7777


def test_load_config_custom_trigger_words(tmp_path: Path) -> None:
    cfg = load_config(
        write_config(
            tmp_path, _min({"segmentation": {"trigger_words": ["go", "send"]}})
        )
    )
    assert cfg.engine.segmentation.trigger_words == ["go", "send"]


# ---------------------------------------------------------------------------
# load_config — error cases
# ---------------------------------------------------------------------------


def test_load_config_missing_file() -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        load_config("/nonexistent/path/config.json")


def test_load_config_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_config(str(p))


def test_load_config_missing_module_type(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"engine": {"module": {"api_key": "x"}}})
    with pytest.raises(ValueError, match="engine.module.type"):
        load_config(path)


def test_load_config_empty_module_type(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"engine": {"module": {"type": ""}}})
    with pytest.raises(ValueError, match="engine.module.type"):
        load_config(path)


def test_load_config_missing_engine_block(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"server": {"port": 8080}})
    with pytest.raises(ValueError, match="engine.module.type"):
        load_config(path)


def test_load_config_invalid_segmentation_mode(tmp_path: Path) -> None:
    path = write_config(tmp_path, _min({"segmentation_mode": "bad_mode"}))
    with pytest.raises(ValueError, match="engine.segmentation_mode"):
        load_config(path)


def test_load_config_invalid_listen_default_mode(tmp_path: Path) -> None:
    # "utterance" is not valid for listen.
    path = write_config(
        tmp_path, _min({"listen_default_segmentation_mode": "utterance"})
    )
    with pytest.raises(ValueError, match="engine.listen_default_segmentation_mode"):
        load_config(path)


def test_load_config_invalid_logging_level(tmp_path: Path) -> None:
    path = write_config(tmp_path, _min({"logging": {"level": "LOUD"}}))
    with pytest.raises(ValueError, match="engine.logging.level"):
        load_config(path)


def test_load_config_logging_level_case_insensitive(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _min({"logging": {"level": "debug"}})))
    assert cfg.engine.logging.level == "DEBUG"


# ---------------------------------------------------------------------------
# validate_asr_type
# ---------------------------------------------------------------------------


def _cfg(module_type: str) -> AppConfig:
    return AppConfig(engine=ASREngineConfig(module=ModuleConfig(type=module_type)))


def test_validate_asr_type_known() -> None:
    registry = {"deepgram": object(), "whisper": object()}
    validate_asr_type(_cfg("deepgram"), registry)  # must not raise


def test_validate_asr_type_unknown_lists_available() -> None:
    registry = {"deepgram": object(), "whisper": object()}
    with pytest.raises(ValueError, match="deepgram") as exc_info:
        validate_asr_type(_cfg("unknown"), registry)
    msg = str(exc_info.value)
    assert "whisper" in msg
    assert "unknown" in msg


def test_validate_asr_type_empty_registry() -> None:
    with pytest.raises(ValueError, match="none"):
        validate_asr_type(_cfg("deepgram"), {})
