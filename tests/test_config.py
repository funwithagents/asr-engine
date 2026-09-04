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
                "listen_default_segmentation_mode": "timeout",
                "dictation_default_segmentation_mode": "timeout",
                "segmentation": {
                    "trigger_words": ["stop"],
                    "initial_silence_timeout_s": 7.0,
                    "end_of_speech_timeout_s": 2.0,
                },
                "sound_feedback": {"enabled": False, "output_device": "speakers"},
                "audio": {"device": "hw:0"},
                "module": {"type": "deepgram", "api_key": "secret", "language": "fr"},
            },
        },
    )
    cfg = load_config(path)

    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 9000
    assert cfg.engine.auto_start is False
    assert cfg.engine.auto_start_dictation is False
    assert cfg.engine.listen_default_segmentation_mode == "timeout"
    assert cfg.engine.dictation_default_segmentation_mode == "timeout"
    assert cfg.engine.segmentation.trigger_words == ["stop"]
    assert cfg.engine.segmentation.initial_silence_timeout_s == 7.0
    assert cfg.engine.segmentation.end_of_speech_timeout_s == 2.0
    assert cfg.engine.sound_feedback.enabled is False
    assert cfg.engine.sound_feedback.output_device == "speakers"
    assert cfg.engine.audio.device == "hw:0"
    assert cfg.engine.module.type == "deepgram"
    assert cfg.engine.module.extra == {"api_key": "secret", "language": "fr"}


def test_load_config_defaults(tmp_path: Path) -> None:
    """Omitting optional blocks applies defaults."""
    cfg = load_config(write_config(tmp_path, _min()))

    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.engine.auto_start is True
    assert cfg.engine.auto_start_dictation is False
    assert cfg.engine.listen_default_segmentation_mode == "trigger_word"
    assert cfg.engine.dictation_default_segmentation_mode == "trigger_word"
    assert cfg.engine.segmentation.initial_silence_timeout_s == 10.0
    assert cfg.engine.segmentation.end_of_speech_timeout_s == 5.0
    assert "submit" in cfg.engine.segmentation.trigger_words
    assert cfg.engine.sound_feedback.enabled is True
    assert cfg.engine.sound_feedback.output_device is None
    assert cfg.engine.audio.device is None
    assert cfg.engine.audio.sample_rate == 16000
    assert cfg.engine.audio.channels == 1
    assert cfg.engine.audio.encoding == "linear16"
    assert cfg.engine.audio.on_unsupported_format == "error"
    assert cfg.engine.module.extra == {}


def test_load_config_audio_format_fields(tmp_path: Path) -> None:
    cfg = load_config(
        write_config(
            tmp_path,
            _min(
                {
                    "audio": {
                        "sample_rate": 48000,
                        "channels": 1,
                        "encoding": "mulaw",
                        "on_unsupported_format": "fallback",
                    }
                }
            ),
        )
    )
    assert cfg.engine.audio.sample_rate == 48000
    assert cfg.engine.audio.encoding == "mulaw"
    assert cfg.engine.audio.on_unsupported_format == "fallback"


def test_load_config_rejects_unknown_encoding(tmp_path: Path) -> None:
    path = write_config(tmp_path, _min({"audio": {"encoding": "opus"}}))
    with pytest.raises(ValueError, match="encoding"):
        load_config(path)


def test_load_config_rejects_unknown_unsupported_policy(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, _min({"audio": {"on_unsupported_format": "resample"}})
    )
    with pytest.raises(ValueError, match="on_unsupported_format"):
        load_config(path)


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


def test_load_config_invalid_dictation_default_mode(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, _min({"dictation_default_segmentation_mode": "bad_mode"})
    )
    with pytest.raises(ValueError, match="engine.dictation_default_segmentation_mode"):
        load_config(path)


def test_load_config_invalid_listen_default_mode(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, _min({"listen_default_segmentation_mode": "bad_mode"})
    )
    with pytest.raises(ValueError, match="engine.listen_default_segmentation_mode"):
        load_config(path)


def test_load_config_listen_default_allows_utterance(tmp_path: Path) -> None:
    cfg = load_config(
        write_config(tmp_path, _min({"listen_default_segmentation_mode": "utterance"}))
    )
    assert cfg.engine.listen_default_segmentation_mode == "utterance"


def test_load_config_auto_start_dictation_requires_auto_start(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, _min({"auto_start": False, "auto_start_dictation": True})
    )
    with pytest.raises(ValueError, match="auto_start_dictation requires auto_start"):
        load_config(path)


def test_load_config_auto_start_dictation_ok_with_auto_start(tmp_path: Path) -> None:
    cfg = load_config(
        write_config(tmp_path, _min({"auto_start": True, "auto_start_dictation": True}))
    )
    assert cfg.engine.auto_start_dictation is True


# ---------------------------------------------------------------------------
# ASREngineConfig.from_dict — the public in-memory constructor
# ---------------------------------------------------------------------------


def test_from_dict_full_block() -> None:
    """A full engine block yields the expected scalars and nested sub-blocks."""
    cfg = ASREngineConfig.from_dict(
        {
            "auto_start": False,
            "listen_default_segmentation_mode": "timeout",
            "dictation_default_segmentation_mode": "utterance",
            "segmentation": {
                "trigger_words": ["stop"],
                "initial_silence_timeout_s": 7.0,
                "end_of_speech_timeout_s": 2.0,
            },
            "sound_feedback": {"enabled": False, "output_device": "speakers"},
            "audio": {"device": "hw:0", "sample_rate": 48000, "encoding": "mulaw"},
            "module": {"type": "deepgram", "api_key": "secret", "language": "fr"},
        }
    )

    assert cfg.auto_start is False
    assert cfg.listen_default_segmentation_mode == "timeout"
    assert cfg.dictation_default_segmentation_mode == "utterance"
    assert cfg.segmentation.trigger_words == ["stop"]
    assert cfg.segmentation.initial_silence_timeout_s == 7.0
    assert cfg.segmentation.end_of_speech_timeout_s == 2.0
    assert cfg.sound_feedback.enabled is False
    assert cfg.sound_feedback.output_device == "speakers"
    assert cfg.audio.device == "hw:0"
    assert cfg.audio.sample_rate == 48000
    assert cfg.audio.encoding == "mulaw"
    assert cfg.module.type == "deepgram"
    assert cfg.module.extra == {"api_key": "secret", "language": "fr"}


def test_from_dict_defaults_for_omitted_blocks() -> None:
    """Only module.type is required; every omitted block gets its documented default."""
    cfg = ASREngineConfig.from_dict({"module": {"type": "deepgram"}})

    assert cfg.auto_start is True
    assert cfg.auto_start_dictation is False
    assert cfg.listen_default_segmentation_mode == "trigger_word"
    assert cfg.dictation_default_segmentation_mode == "trigger_word"
    assert cfg.segmentation.initial_silence_timeout_s == 10.0
    assert cfg.segmentation.end_of_speech_timeout_s == 5.0
    assert "submit" in cfg.segmentation.trigger_words
    assert cfg.sound_feedback.enabled is True
    assert cfg.sound_feedback.output_device is None
    assert cfg.audio.device is None
    assert cfg.audio.sample_rate == 16000
    assert cfg.audio.channels == 1
    assert cfg.audio.encoding == "linear16"
    assert cfg.audio.on_unsupported_format == "error"
    assert cfg.module.extra == {}


def test_from_dict_builds_without_credentials() -> None:
    """A module block with no api_key still builds — keys resolve later."""
    cfg = ASREngineConfig.from_dict({"module": {"type": "deepgram"}})
    assert cfg.module.type == "deepgram"
    assert "api_key" not in cfg.module.extra


def test_from_dict_missing_module_type() -> None:
    with pytest.raises(ValueError, match="engine.module.type"):
        ASREngineConfig.from_dict({"segmentation": {"trigger_words": ["go"]}})


def test_from_dict_empty_module_type() -> None:
    with pytest.raises(ValueError, match="engine.module.type"):
        ASREngineConfig.from_dict({"module": {"type": ""}})


def test_from_dict_invalid_listen_default_mode() -> None:
    with pytest.raises(ValueError, match="engine.listen_default_segmentation_mode"):
        ASREngineConfig.from_dict(
            {"listen_default_segmentation_mode": "bad", "module": {"type": "deepgram"}}
        )


def test_from_dict_invalid_dictation_default_mode() -> None:
    with pytest.raises(ValueError, match="engine.dictation_default_segmentation_mode"):
        ASREngineConfig.from_dict(
            {
                "dictation_default_segmentation_mode": "bad",
                "module": {"type": "deepgram"},
            }
        )


def test_from_dict_invalid_encoding() -> None:
    with pytest.raises(ValueError, match="encoding"):
        ASREngineConfig.from_dict(
            {"audio": {"encoding": "opus"}, "module": {"type": "deepgram"}}
        )


def test_from_dict_invalid_unsupported_format_policy() -> None:
    with pytest.raises(ValueError, match="on_unsupported_format"):
        ASREngineConfig.from_dict(
            {
                "audio": {"on_unsupported_format": "resample"},
                "module": {"type": "deepgram"},
            }
        )


def test_from_dict_auto_start_dictation_requires_auto_start() -> None:
    with pytest.raises(ValueError, match="auto_start_dictation requires auto_start"):
        ASREngineConfig.from_dict(
            {
                "auto_start": False,
                "auto_start_dictation": True,
                "module": {"type": "deepgram"},
            }
        )


def test_from_dict_matches_load_config(tmp_path: Path) -> None:
    """load_config produces the same engine config as from_dict on that file's block."""
    engine_block = {
        "auto_start": False,
        "listen_default_segmentation_mode": "timeout",
        "segmentation": {"trigger_words": ["stop"], "end_of_speech_timeout_s": 2.0},
        "audio": {"device": "hw:0", "encoding": "mulaw"},
        "module": {"type": "deepgram", "api_key": "secret", "language": "fr"},
    }
    path = write_config(tmp_path, {"server": {"port": 9000}, "engine": engine_block})

    assert load_config(path).engine == ASREngineConfig.from_dict(engine_block)


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
