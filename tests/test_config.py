"""Unit tests for asr_engine.config."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from asr_engine.config import (
    ASREngineConfig,
    AudioFormatConfig,
    MCPServerConfig,
    ModuleConfig,
    validate_asr_type,
)

# The audio source every test config uses unless the source is what it tests.
_SRC = {"audio_source": {"type": "input-device"}}


def write_config(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def _min(engine_overrides: dict | None = None, **top) -> dict:
    """Minimal valid config dict with a module.type, plus overrides."""
    engine = {**_SRC, "module": {"type": "deepgram"}}
    if engine_overrides:
        engine.update(engine_overrides)
    return {"engine": engine, **top}


# ---------------------------------------------------------------------------
# MCPServerConfig.from_json_file — happy path
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
                "audio_source": {"type": "input-device", "device": "hw:0"},
                "module": {"type": "deepgram", "api_key": "secret", "language": "fr"},
            },
        },
    )
    cfg = MCPServerConfig.from_json_file(path)

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
    assert cfg.engine.audio_source.device == "hw:0"
    assert cfg.engine.module.type == "deepgram"
    assert cfg.engine.module.extra == {"api_key": "secret", "language": "fr"}


def test_load_config_reads_non_ascii_values_as_utf8(tmp_path: Path) -> None:
    """A UTF-8 file with non-ASCII trigger words loads intact on every platform —
    not decoded with the locale's code page (cp1252 on Windows)."""
    path = tmp_path / "config.json"
    data = _min({"segmentation": {"trigger_words": ["validé", "arrête"]}})
    path.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    cfg = MCPServerConfig.from_json_file(str(path))

    assert cfg.engine.segmentation.trigger_words == ["validé", "arrête"]


def test_load_config_defaults(tmp_path: Path) -> None:
    """Omitting optional blocks applies defaults."""
    cfg = MCPServerConfig.from_json_file(write_config(tmp_path, _min()))

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
    assert cfg.engine.audio_source.type == "input-device"
    assert cfg.engine.audio_source.device is None
    # Nothing set on either format: the engine resolves both.
    assert cfg.engine.audio_source.audio_format == AudioFormatConfig()
    assert cfg.engine.module.audio_format == AudioFormatConfig()
    assert cfg.engine.module.extra == {}


def test_load_config_audio_format_fields(tmp_path: Path) -> None:
    cfg = MCPServerConfig.from_json_file(
        write_config(
            tmp_path,
            _min(
                {
                    "audio_source": {
                        "type": "input-device",
                        "audio_format": {
                            "sample_rate": 48000,
                            "channels": 2,
                            "encoding": "mulaw",
                        },
                    }
                }
            ),
        )
    )
    assert cfg.engine.audio_source.audio_format == AudioFormatConfig(48000, 2, "mulaw")


def test_load_config_rejects_unknown_encoding(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        _min(
            {
                "audio_source": {
                    "type": "input-device",
                    "audio_format": {"encoding": "opus"},
                }
            }
        ),
    )
    with pytest.raises(ValueError, match="encoding"):
        MCPServerConfig.from_json_file(path)


def test_the_module_audio_format_is_the_engines_not_the_modules() -> None:
    cfg = ASREngineConfig.from_dict(
        {
            **_SRC,
            "module": {
                "type": "deepgram-v1",
                "audio_format": {"sample_rate": 8000, "encoding": "mulaw"},
                "model": "nova-3",
            },
        }
    )
    assert cfg.module.audio_format == AudioFormatConfig(8000, None, "mulaw")
    # It is not handed to the module with its own fields.
    assert cfg.module.extra == {"model": "nova-3"}


@pytest.mark.parametrize("block", ["audio_source", "module"])
@pytest.mark.parametrize(
    "audio_format, message",
    [
        ({"sample_rate": 0}, "sample_rate: must be a positive integer"),
        ({"sample_rate": "16000"}, "sample_rate: must be a positive integer"),
        ({"sample_rate": 16000.0}, "sample_rate: must be a positive integer"),
        ({"channels": -1}, "channels: must be a positive integer"),
        ({"channels": True}, "channels: must be a positive integer"),
        ({"encoding": "opus"}, "encoding: 'opus'"),
        ({"rate": 16000}, r"unknown field\(s\) rate"),
        ([16000, 1], "must be an object or null"),
    ],
)
def test_an_invalid_audio_format_is_rejected_in_either_block(
    block: str, audio_format: object, message: str
) -> None:
    engine_block: dict = {
        "audio_source": {"type": "input-device"},
        "module": {"type": "fake"},
    }
    engine_block[block]["audio_format"] = audio_format
    with pytest.raises(ValueError, match=f"engine.{block}.audio_format.*{message}"):
        ASREngineConfig.from_dict(engine_block)


def test_a_null_audio_format_or_field_means_not_set() -> None:
    cfg = ASREngineConfig.from_dict(
        {
            "audio_source": {
                "type": "input-device",
                "audio_format": {"sample_rate": None, "encoding": None},
            },
            "module": {"type": "fake", "audio_format": None},
        }
    )
    assert cfg.audio_source.audio_format == AudioFormatConfig()
    assert cfg.module.audio_format == AudioFormatConfig()


def test_a_file_source_carries_its_file_and_trailing_silence() -> None:
    cfg = ASREngineConfig.from_dict(
        {
            "audio_source": {
                "type": "file",
                "audio_file": "speech.mp3",
                "trailing_silence_s": 2.0,
            },
            "module": {"type": "fake"},
        }
    )
    source = cfg.audio_source
    assert (source.type, source.audio_file, source.trailing_silence_s) == (
        "file",
        "speech.mp3",
        2.0,
    )


def test_a_custom_source_takes_no_other_field() -> None:
    cfg = ASREngineConfig.from_dict(
        {"audio_source": {"type": "custom"}, "module": {"type": "fake"}}
    )
    assert cfg.audio_source.type == "custom"


@pytest.mark.parametrize(
    "audio_source, message",
    [
        ("hw:0", "engine.audio_source: must be an object or null"),
        ({"type": "webcam"}, "Invalid engine.audio_source.type: 'webcam'"),
        # Each type takes its own fields only.
        (
            {"type": "input-device", "audio_file": "a.wav"},
            "audio_file does not apply to type 'input-device'",
        ),
        (
            {"type": "file", "audio_file": "a.wav", "device": "hw:0"},
            "device does not apply to type 'file'",
        ),
        (
            {"type": "file", "audio_file": "a.wav", "audio_format": {}},
            "audio_format does not apply to type 'file'",
        ),
        (
            {"type": "custom", "audio_format": {"sample_rate": 16000}},
            "audio_format does not apply to type 'custom', which takes no other field",
        ),
        ({"type": "file"}, "missing required field: engine.audio_source.audio_file"),
    ],
)
def test_an_invalid_audio_source_block_is_rejected(
    audio_source: object, message: str
) -> None:
    block = {"audio_source": audio_source, "module": {"type": "fake"}}
    with pytest.raises(ValueError, match=message):
        ASREngineConfig.from_dict(block)


@pytest.mark.parametrize("audio_source", [None, {}, {"device": "hw:0"}])
def test_the_source_type_defaults_to_the_input_device(
    audio_source: dict | None,
) -> None:
    block: dict = {"module": {"type": "fake"}}
    if audio_source is not None:
        block["audio_source"] = audio_source
    source = ASREngineConfig.from_dict(block).audio_source
    assert source.type == "input-device"
    assert source.device == (audio_source or {}).get("device")
    # The dataclass built by hand has the same default.
    assert ASREngineConfig().audio_source.type == "input-device"


def test_the_old_audio_block_is_rejected_with_its_new_name() -> None:
    with pytest.raises(ValueError, match="engine.audio is now engine.audio_source"):
        ASREngineConfig.from_dict(
            {"audio": {"device": "hw:0"}, "module": {"type": "fake"}}
        )


def test_load_config_partial_server_defaults(tmp_path: Path) -> None:
    cfg = MCPServerConfig.from_json_file(
        write_config(tmp_path, _min(server={"port": 7777}))
    )
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 7777


def test_load_config_custom_trigger_words(tmp_path: Path) -> None:
    cfg = MCPServerConfig.from_json_file(
        write_config(
            tmp_path, _min({"segmentation": {"trigger_words": ["go", "send"]}})
        )
    )
    assert cfg.engine.segmentation.trigger_words == ["go", "send"]


# ---------------------------------------------------------------------------
# MCPServerConfig.from_json_file — error cases
# ---------------------------------------------------------------------------


def test_load_config_missing_file() -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        MCPServerConfig.from_json_file("/nonexistent/path/config.json")


def test_load_config_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    with pytest.raises(ValueError, match="not valid JSON"):
        MCPServerConfig.from_json_file(str(p))


def test_load_config_missing_module_type(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"engine": {"module": {"api_key": "x"}}})
    with pytest.raises(ValueError, match="engine.module.type"):
        MCPServerConfig.from_json_file(path)


def test_load_config_empty_module_type(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"engine": {"module": {"type": ""}}})
    with pytest.raises(ValueError, match="engine.module.type"):
        MCPServerConfig.from_json_file(path)


def test_load_config_missing_engine_block(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"server": {"port": 8080}})
    with pytest.raises(ValueError, match="engine.module.type"):
        MCPServerConfig.from_json_file(path)


def test_load_config_invalid_dictation_default_mode(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, _min({"dictation_default_segmentation_mode": "bad_mode"})
    )
    with pytest.raises(ValueError, match="engine.dictation_default_segmentation_mode"):
        MCPServerConfig.from_json_file(path)


def test_load_config_invalid_listen_default_mode(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, _min({"listen_default_segmentation_mode": "bad_mode"})
    )
    with pytest.raises(ValueError, match="engine.listen_default_segmentation_mode"):
        MCPServerConfig.from_json_file(path)


def test_load_config_listen_default_allows_utterance(tmp_path: Path) -> None:
    cfg = MCPServerConfig.from_json_file(
        write_config(tmp_path, _min({"listen_default_segmentation_mode": "utterance"}))
    )
    assert cfg.engine.listen_default_segmentation_mode == "utterance"


def test_load_config_auto_start_dictation_requires_auto_start(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, _min({"auto_start": False, "auto_start_dictation": True})
    )
    with pytest.raises(ValueError, match="auto_start_dictation requires auto_start"):
        MCPServerConfig.from_json_file(path)


def test_load_config_auto_start_dictation_ok_with_auto_start(tmp_path: Path) -> None:
    cfg = MCPServerConfig.from_json_file(
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
            "audio_source": {
                "type": "input-device",
                "device": "hw:0",
                "audio_format": {"sample_rate": 48000, "encoding": "mulaw"},
            },
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
    assert cfg.audio_source.device == "hw:0"
    assert cfg.audio_source.audio_format == AudioFormatConfig(48000, None, "mulaw")
    assert cfg.module.type == "deepgram"
    assert cfg.module.extra == {"api_key": "secret", "language": "fr"}


def test_from_dict_defaults_for_omitted_blocks() -> None:
    """Only module.type is required; every omitted block gets its documented default."""
    cfg = ASREngineConfig.from_dict({**_SRC, "module": {"type": "deepgram"}})

    assert cfg.auto_start is True
    assert cfg.auto_start_dictation is False
    assert cfg.listen_default_segmentation_mode == "trigger_word"
    assert cfg.dictation_default_segmentation_mode == "trigger_word"
    assert cfg.segmentation.initial_silence_timeout_s == 10.0
    assert cfg.segmentation.end_of_speech_timeout_s == 5.0
    assert "submit" in cfg.segmentation.trigger_words
    assert cfg.sound_feedback.enabled is True
    assert cfg.sound_feedback.output_device is None
    assert cfg.audio_source.device is None
    assert cfg.audio_source.audio_format == AudioFormatConfig()
    assert cfg.module.extra == {}


def test_from_dict_builds_without_credentials() -> None:
    """A module block with no api_key still builds — keys resolve later."""
    cfg = ASREngineConfig.from_dict({**_SRC, "module": {"type": "deepgram"}})
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
            {
                "listen_default_segmentation_mode": "bad",
                **_SRC,
                "module": {"type": "deepgram"},
            }
        )


def test_from_dict_invalid_dictation_default_mode() -> None:
    with pytest.raises(ValueError, match="engine.dictation_default_segmentation_mode"):
        ASREngineConfig.from_dict(
            {
                "dictation_default_segmentation_mode": "bad",
                **_SRC,
                "module": {"type": "deepgram"},
            }
        )


def test_from_dict_invalid_encoding() -> None:
    with pytest.raises(ValueError, match="encoding"):
        ASREngineConfig.from_dict(
            {
                "audio_source": {
                    "type": "input-device",
                    "audio_format": {"encoding": "opus"},
                },
                "module": {"type": "deepgram"},
            }
        )


def test_from_dict_auto_start_dictation_requires_auto_start() -> None:
    with pytest.raises(ValueError, match="auto_start_dictation requires auto_start"):
        ASREngineConfig.from_dict(
            {
                "auto_start": False,
                "auto_start_dictation": True,
                **_SRC,
                "module": {"type": "deepgram"},
            }
        )


def test_from_dict_matches_from_json_file(tmp_path: Path) -> None:
    """Loading a file yields the same engine config as from_dict on that file's block."""
    engine_block = {
        "auto_start": False,
        "listen_default_segmentation_mode": "timeout",
        "segmentation": {"trigger_words": ["stop"], "end_of_speech_timeout_s": 2.0},
        "audio_source": {"type": "input-device", "device": "hw:0"},
        "module": {"type": "deepgram", "api_key": "secret", "language": "fr"},
    }
    path = write_config(tmp_path, {"server": {"port": 9000}, "engine": engine_block})

    assert MCPServerConfig.from_json_file(path).engine == ASREngineConfig.from_dict(
        engine_block
    )


# ---------------------------------------------------------------------------
# from_json / from_json_file — the string and engine-block-file constructors
# ---------------------------------------------------------------------------


def test_engine_from_json_parses_engine_block() -> None:
    """ASREngineConfig.from_json takes a JSON *engine block* (no server wrapper)."""
    cfg = ASREngineConfig.from_json(
        json.dumps(
            {
                "listen_default_segmentation_mode": "timeout",
                **_SRC,
                "module": {"type": "deepgram", "language": "fr"},
            }
        )
    )
    assert cfg.listen_default_segmentation_mode == "timeout"
    assert cfg.module.type == "deepgram"
    assert cfg.module.extra == {"language": "fr"}


def test_engine_from_json_matches_from_dict() -> None:
    block = {
        "audio_source": {"type": "input-device", "device": "hw:0"},
        "module": {"type": "deepgram"},
    }
    assert ASREngineConfig.from_json(json.dumps(block)) == ASREngineConfig.from_dict(
        block
    )


def test_engine_from_json_rejects_malformed_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        ASREngineConfig.from_json("{not valid")


def test_engine_from_json_file_reads_engine_block(tmp_path: Path) -> None:
    """from_json_file on the engine config reads a file that *is* the engine block."""
    p = tmp_path / "engine.json"
    p.write_text(
        json.dumps({**_SRC, "module": {"type": "deepgram", "model": "nova-3"}})
    )
    cfg = ASREngineConfig.from_json_file(str(p))
    assert cfg.module.type == "deepgram"
    assert cfg.module.extra == {"model": "nova-3"}


def test_engine_from_json_file_missing_file() -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        ASREngineConfig.from_json_file("/nonexistent/engine.json")


def test_mcp_from_dict_parses_server_and_engine() -> None:
    cfg = MCPServerConfig.from_dict(
        {"server": {"host": "0.0.0.0", "port": 9000}, "engine": _min()["engine"]}
    )
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 9000
    assert cfg.engine.module.type == "deepgram"


def test_mcp_from_json_matches_from_json_file(tmp_path: Path) -> None:
    """from_json (string) and from_json_file (path) produce the same whole config."""
    data = {"server": {"port": 7000}, "engine": _min()["engine"]}
    path = write_config(tmp_path, data)
    assert MCPServerConfig.from_json(
        json.dumps(data)
    ) == MCPServerConfig.from_json_file(path)


def test_mcp_from_json_names_file_in_parse_error(tmp_path: Path) -> None:
    """A malformed config *file* names itself in the error."""
    p = tmp_path / "bad.json"
    p.write_text("{not valid")
    with pytest.raises(ValueError, match=r"not valid JSON.*bad\.json"):
        MCPServerConfig.from_json_file(str(p))


# ---------------------------------------------------------------------------
# validate_asr_type
# ---------------------------------------------------------------------------


def _cfg(module_type: str) -> ASREngineConfig:
    return ASREngineConfig(module=ModuleConfig(type=module_type))


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


def test_validate_asr_type_accepts_an_old_module_name() -> None:
    validate_asr_type(_cfg("deepgram_v1"), {"deepgram-v1": object()})  # no raise


def test_an_old_module_name_is_stored_as_its_new_name(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="asr_engine.modules"):
        old = ASREngineConfig.from_dict(
            {**_SRC, "module": {"type": "mistral_realtime"}}
        )
    new = ASREngineConfig.from_dict({**_SRC, "module": {"type": "mistral-realtime"}})
    assert old.module.type == new.module.type == "mistral-realtime"
    assert "use 'mistral-realtime' instead" in caplog.text


def test_validate_asr_type_empty_registry() -> None:
    with pytest.raises(ValueError, match="none"):
        validate_asr_type(_cfg("deepgram"), {})


# ---------------------------------------------------------------------------
# engine.vad — the optional VAD block (specs/vad.md)
# ---------------------------------------------------------------------------


def _engine_with_vad(vad: object) -> dict:
    return {**_SRC, "module": {"type": "fake"}, "vad": vad}


def test_vad_is_off_when_the_block_is_omitted_or_null() -> None:
    assert ASREngineConfig.from_dict({**_SRC, "module": {"type": "fake"}}).vad is None
    assert ASREngineConfig.from_dict(_engine_with_vad(None)).vad is None


def test_vad_block_gets_the_documented_defaults() -> None:
    vad = ASREngineConfig.from_dict(_engine_with_vad({"type": "silero"})).vad
    assert vad is not None
    assert vad.type == "silero"
    assert vad.threshold == 0.5
    assert vad.min_speech_duration_ms == 250
    assert vad.min_silence_duration_ms == 500
    assert vad.speech_pad_ms == 300
    assert vad.extra == {}


def test_vad_block_carries_gate_fields_and_passes_the_rest_to_the_module() -> None:
    vad = ASREngineConfig.from_dict(
        _engine_with_vad(
            {
                "type": "silero",
                "threshold": 0.3,
                "min_speech_duration_ms": 0,
                "min_silence_duration_ms": 1500,
                "speech_pad_ms": 0,
                "model_variant": "tiny",
            }
        )
    ).vad
    assert vad is not None
    assert (vad.threshold, vad.min_speech_duration_ms) == (0.3, 0)
    assert (vad.min_silence_duration_ms, vad.speech_pad_ms) == (1500, 0)
    assert vad.extra == {"model_variant": "tiny"}


@pytest.mark.parametrize(
    "block, field",
    [
        ({}, "engine.vad.type"),
        ({"type": ""}, "engine.vad.type"),
        ({"type": 3}, "engine.vad.type"),
        ({"type": "silero", "threshold": 0}, "engine.vad.threshold"),
        ({"type": "silero", "threshold": 1}, "engine.vad.threshold"),
        ({"type": "silero", "threshold": "0.5"}, "engine.vad.threshold"),
        ({"type": "silero", "threshold": True}, "engine.vad.threshold"),
        (
            {"type": "silero", "min_speech_duration_ms": -1},
            "engine.vad.min_speech_duration_ms",
        ),
        (
            {"type": "silero", "min_speech_duration_ms": 2.5},
            "engine.vad.min_speech_duration_ms",
        ),
        (
            {"type": "silero", "min_silence_duration_ms": 0},
            "engine.vad.min_silence_duration_ms",
        ),
        (
            {"type": "silero", "min_silence_duration_ms": True},
            "engine.vad.min_silence_duration_ms",
        ),
        ({"type": "silero", "speech_pad_ms": -5}, "engine.vad.speech_pad_ms"),
        ("silero", "engine.vad"),
    ],
)
def test_vad_block_validation_names_the_field(block: object, field: str) -> None:
    with pytest.raises(ValueError, match=field.replace(".", r"\.")):
        ASREngineConfig.from_dict(_engine_with_vad(block))


def test_vad_type_is_not_checked_against_the_registry_at_parse_time() -> None:
    """Like module.type: an unknown VAD surfaces at engine construction."""
    vad = ASREngineConfig.from_dict(_engine_with_vad({"type": "not_a_vad"})).vad
    assert vad is not None and vad.type == "not_a_vad"


def test_vad_block_is_parsed_from_a_whole_server_config_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"engine": _engine_with_vad({"type": "silero", "threshold": 0.6})}),
        encoding="utf-8",
    )
    vad = MCPServerConfig.from_json_file(str(path)).engine.vad
    assert vad is not None and vad.threshold == 0.6
