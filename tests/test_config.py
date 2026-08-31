"""Unit tests for asr_mcp.config — Plan 02."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asr_mcp.config import (
    AppConfig,
    ASRConfig,
    load_config,
    validate_asr_type,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_config(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


# ---------------------------------------------------------------------------
# load_config — happy path
# ---------------------------------------------------------------------------


def test_load_config_full(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "server": {"host": "0.0.0.0", "port": 9000},
            "audio": {"device": "hw:0"},
            "asr": {"type": "deepgram", "api_key": "secret", "language": "fr"},
        },
    )
    cfg = load_config(path)

    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 9000
    assert cfg.audio.device == "hw:0"
    assert cfg.asr.type == "deepgram"
    assert cfg.asr.extra == {"api_key": "secret", "language": "fr"}


def test_load_config_defaults(tmp_path: Path) -> None:
    """Omitting server and audio blocks should apply defaults."""
    path = write_config(tmp_path, {"asr": {"type": "deepgram"}})
    cfg = load_config(path)

    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.audio.device is None
    assert cfg.asr.extra == {}


def test_load_config_partial_server_defaults(tmp_path: Path) -> None:
    """Partial server block: only overridden field changes."""
    path = write_config(tmp_path, {"server": {"port": 7777}, "asr": {"type": "x"}})
    cfg = load_config(path)

    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 7777


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


def test_load_config_missing_asr_type(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"asr": {"api_key": "x"}})
    with pytest.raises(ValueError, match="asr.type"):
        load_config(path)


def test_load_config_empty_asr_type(tmp_path: Path) -> None:
    """asr.type present but empty string should also raise."""
    path = write_config(tmp_path, {"asr": {"type": ""}})
    with pytest.raises(ValueError, match="asr.type"):
        load_config(path)


def test_load_config_missing_asr_block(tmp_path: Path) -> None:
    """No asr block at all should raise about asr.type."""
    path = write_config(tmp_path, {"server": {"port": 8080}})
    with pytest.raises(ValueError, match="asr.type"):
        load_config(path)


# ---------------------------------------------------------------------------
# load_config — engine and listen blocks
# ---------------------------------------------------------------------------


def test_load_config_engine_and_listen_defaults(tmp_path: Path) -> None:
    """Omitting engine and listen blocks uses defaults."""
    path = write_config(tmp_path, {"asr": {"type": "deepgram"}})
    cfg = load_config(path)

    assert cfg.engine.auto_start is True
    assert cfg.engine.segment_mode == "utterance"
    assert cfg.listen.segment_mode == "trigger_word"
    assert cfg.listen.initial_silence_timeout_s == 10.0
    assert cfg.listen.end_of_speech_timeout_s == 5.0
    assert "submit" in cfg.listen.trigger_words
    assert cfg.listen.sound_feedback is True
    assert cfg.audio.output_device is None


def test_load_config_engine_segment_block(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "asr": {"type": "x"},
            "engine": {
                "segment_mode": "timeout",
                "trigger_words": ["stop"],
                "end_of_speech_timeout_s": 2.0,
            },
        },
    )
    cfg = load_config(path)
    assert cfg.engine.segment_mode == "timeout"
    assert cfg.engine.trigger_words == ["stop"]
    assert cfg.engine.end_of_speech_timeout_s == 2.0


def test_load_config_invalid_engine_segment_mode(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {"asr": {"type": "x"}, "engine": {"segment_mode": "bad_mode"}},
    )
    with pytest.raises(ValueError, match="engine.segment_mode"):
        load_config(path)


def test_load_config_auto_start_false(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, {"asr": {"type": "x"}, "engine": {"auto_start": False}}
    )
    cfg = load_config(path)
    assert cfg.engine.auto_start is False


def test_load_config_listen_timeout_mode(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "asr": {"type": "x"},
            "listen": {
                "segment_mode": "timeout",
                "end_of_speech_timeout_s": 3.0,
            },
        },
    )
    cfg = load_config(path)
    assert cfg.listen.segment_mode == "timeout"
    assert cfg.listen.end_of_speech_timeout_s == 3.0


def test_load_config_custom_trigger_words(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {"asr": {"type": "x"}, "listen": {"trigger_words": ["go", "send"]}},
    )
    cfg = load_config(path)
    assert cfg.listen.trigger_words == ["go", "send"]


def test_load_config_sound_feedback_and_output_device(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "asr": {"type": "x"},
            "audio": {"output_device": "speakers"},
            "listen": {"sound_feedback": False},
        },
    )
    cfg = load_config(path)
    assert cfg.audio.output_device == "speakers"
    assert cfg.listen.sound_feedback is False


def test_load_config_invalid_listen_segment_mode(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {"asr": {"type": "x"}, "listen": {"segment_mode": "bad_mode"}},
    )
    with pytest.raises(ValueError, match="listen.segment_mode"):
        load_config(path)


# ---------------------------------------------------------------------------
# validate_asr_type
# ---------------------------------------------------------------------------


def _cfg(asr_type: str) -> AppConfig:
    return AppConfig(asr=ASRConfig(type=asr_type))


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
