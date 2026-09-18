"""Shared helpers for e2e tests."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from asr_engine import AudioFormat

log = logging.getLogger(__name__)

# Shared audio fixtures (see fixtures/README.md for the naming convention). The
# default e2e input is the 44.1 kHz mono MP3s; the 16 kHz WAV drives the
# sample-rate-compatibility tests; the 24 kHz WAVs feed modules with a strict
# 24 kHz contract (kyutai). Because file sources are validated, not resampled,
# each fixture is paired with the AudioFormat it must be played at.
FIXTURES_DIR = Path(__file__).parent / "fixtures"

FORMAT_MP3_44100 = AudioFormat(sample_rate=44100)
FORMAT_WAV_16000 = AudioFormat(sample_rate=16000)
FORMAT_WAV_24000 = AudioFormat(sample_rate=24000)

FIXTURE_BLUE = FIXTURES_DIR / "sample_44100_theskyisblue.mp3"  # "the sky is blue"
FIXTURE_BLUE_VALIDATE = (  # "the sky is blue validate" (trigger word)
    FIXTURES_DIR / "sample_44100_theskyisbluevalidate.mp3"
)
FIXTURE_BLUE_WAV_16000 = FIXTURES_DIR / "sample_16000_theskyisblue.wav"
FIXTURE_BLUE_WAV_24000 = FIXTURES_DIR / "sample_24000_theskyisblue.wav"
FIXTURE_BLUE_VALIDATE_WAV_24000 = FIXTURES_DIR / "sample_24000_theskyisbluevalidate.wav"


@dataclass(frozen=True)
class ModuleAudio:
    """The audio a ``MODULES`` row is driven with: a format its module supports,
    and the two conformance fixtures recorded in that format."""

    audio_format: AudioFormat
    blue: Path  # "the sky is blue"
    blue_validate: Path  # "the sky is blue validate"


AUDIO_MP3_44100 = ModuleAudio(FORMAT_MP3_44100, FIXTURE_BLUE, FIXTURE_BLUE_VALIDATE)
AUDIO_WAV_24000 = ModuleAudio(
    FORMAT_WAV_24000, FIXTURE_BLUE_WAV_24000, FIXTURE_BLUE_VALIDATE_WAV_24000
)

# Deepgram's key env var. This name is Deepgram-specific: other modules declare their
# own via ``api_key_env`` in their ``MODULES`` row / provider config, and a module that
# needs no key declares none. The key value never lives in the repo — only the variable
# name does. Tests pass the name to modules via the ``api_key_env`` config field and let
# ``resolve_api_key`` read the value; they never read the literal key themselves.
DEEPGRAM_API_KEY_ENV = "DEEPGRAM_API_KEY"


def require_api_key(module_config: dict) -> None:
    """Skip the calling test unless the key env var *this module config names* is set.

    Reads ``module_config["api_key_env"]`` — the name of the env var the module will
    authenticate with — so nothing here is tied to a specific provider. When that var
    is unset the test skips (e2e is opt-in; the keys may live in ``~/.zshrc`` which a
    non-interactive shell doesn't source — run under ``zsh -ic 'uv run pytest
    tests-e2e'``). Without this guard an unset var would make ``resolve_api_key``
    *raise* and fail the test instead of skipping it. A config with no ``api_key_env``
    (a module needing no key) is never skipped.
    """
    env_name = module_config.get("api_key_env")
    if env_name and not os.environ.get(env_name):
        pytest.skip(
            f"e2e: environment variable '{env_name}' is not set — "
            f"see AGENTS.md 'Live/e2e tests'"
        )


# Local-model modules, by type → the env var that opts their e2e case in. They need
# no API key, so ``require_api_key`` never skips them — and an unguarded run would
# try to download gigabytes of weights inside the per-test timeout.
LOCAL_MODEL_OPT_IN_ENV = {"kyutai": "ASR_ENGINE_E2E_KYUTAI"}


def require_local_model(module_type: str, module_config: dict) -> None:
    """Skip the calling test unless this local-model module is opted in and usable.

    The second skip axis, next to ``require_api_key``: a module listed in
    ``LOCAL_MODEL_OPT_IN_ENV`` runs only when its env var is set to ``1`` (the
    caller vouching that the weights are already downloaded) **and** its backend
    dependency is installed. Any other module is never skipped here.
    """
    env_name = LOCAL_MODEL_OPT_IN_ENV.get(module_type)
    if env_name is None:
        return
    if os.environ.get(env_name) != "1":
        pytest.skip(
            f"e2e: local model '{module_type}' is opt-in — pre-fetch its weights, "
            f"then set {env_name}=1 (see specs/e2e-testing.md)"
        )
    from asr_engine.modules import resolve_module_class

    try:
        resolve_module_class(module_type)(config=module_config)
    except ImportError as exc:
        pytest.skip(f"e2e: {exc}")


def normalize_transcript(text: str) -> str:
    """Normalize a live transcript for robust phrase-level assertions."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 30.0,
    interval: float = 0.1,
) -> None:
    """Wait until a synchronous predicate is true or raise ``TimeoutError``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("condition not met within timeout")


# Scripts for the ``fake`` module (specs/fake-module.md), one per fixture. The fake
# ignores the audio and emits these on its *audio-time* clock, so every event must
# fall inside the audio actually fed (the file plus any trailing silence). Fixture
# durations (soundfile): FIXTURE_BLUE 1.347 s, FIXTURE_BLUE_VALIDATE 1.904 s,
# FIXTURE_BLUE_WAV_16000 1.486 s.
#
# ``delay_s`` shifts a script later on that clock. Servers that auto-start begin
# playing before the test's client has subscribed, so those scenarios delay the
# script into the trailing silence to leave the client time to connect.


def script_blue(delay_s: float = 0.0) -> list[dict]:
    """ "the sky is blue" — fits FIXTURE_BLUE and FIXTURE_BLUE_WAV_16000 at delay 0."""
    return [
        {"text": "the sky is blue", "start_s": 0.1 + delay_s, "end_s": 1.2 + delay_s}
    ]


def script_blue_validate(delay_s: float = 0.0) -> list[dict]:
    """ "the sky is blue" then the trigger word "validate" as its own final — fits
    FIXTURE_BLUE_VALIDATE at delay 0."""
    return [
        {"text": "the sky is blue", "start_s": 0.1 + delay_s, "end_s": 1.1 + delay_s},
        {"text": "validate", "start_s": 1.3 + delay_s, "end_s": 1.7 + delay_s},
    ]


def default_module(script: list[dict]) -> tuple[str, dict]:
    """Return the module type and config for module-agnostic e2e tests.

    The single place the module is chosen for every module-agnostic test (direct
    engine API, MCP resource/tool, asr-to-terminal): the scripted ``fake`` module,
    replaying *script*. It needs no credentials, so these tests never skip, and
    their transcripts are exact. Live backends are covered per module by
    ``test_engine_modules.py`` (``MODULES``).
    """
    return "fake", {"utterances": script}


# Per-module param table for the parametrized engine conformance test
# (``test_engine_modules.py``). Each entry carries the model, a per-module
# ``silence_s`` (the gap the backend needs to finalize an utterance — Flux/EndOfTurn
# needs longer than nova-3/is_final), ``api_key_env`` (the module's own key env var,
# omitted for a module needing no key), and the ``ModuleAudio`` to drive it with (a
# format the module supports). Add a row here when adding a new ASR module so it
# gets e2e conformance coverage.
MODULES = [
    pytest.param(
        "deepgram_v1",
        {"model": "nova-3", "api_key_env": DEEPGRAM_API_KEY_ENV},
        3.0,
        AUDIO_MP3_44100,
        id="deepgram_v1",
    ),
    pytest.param(
        "deepgram_v2",
        {"model": "flux-general-en", "api_key_env": DEEPGRAM_API_KEY_ENV},
        3.0,
        AUDIO_MP3_44100,
        id="deepgram_v2",
    ),
    # Local model: no key; opt-in via ASR_ENGINE_E2E_KYUTAI=1 (require_local_model).
    # silence_s must exceed the model's 0.5 s text delay + the 2.0 s fallback
    # finalize timer, so the default 3.0 s would be marginal.
    pytest.param(
        "kyutai",
        {"hf_repo": "kyutai/stt-1b-en_fr-candle"},
        4.0,
        AUDIO_WAV_24000,
        id="kyutai",
    ),
]


def build_engine(
    audio_source,
    module_type: str,
    module_config: dict,
    *,
    audio_format: AudioFormat = FORMAT_MP3_44100,
    listen_default_segmentation_mode: str = "trigger_word",
    dictation_default_segmentation_mode: str = "trigger_word",
    trigger_words: list[str] | None = None,
    initial_silence_timeout_s: float = 10.0,
    end_of_speech_timeout_s: float = 5.0,
    on_speech_utterance=None,
    on_speech_segment=None,
):
    """Build an ASREngine driven by *audio_source* (no MCP server).

    Sound feedback is disabled. ``audio_source`` is any object matching the
    ``AudioSource`` protocol — e.g. a ``FileAudioSource`` (see
    ``build_file_engine``) or a ``ScriptableAudioSource`` for hand-sequenced audio.
    ``audio_format`` is set explicitly on the engine config so it matches the
    source's own format (the two must agree — files aren't resampled).
    """
    from asr_engine.config import (
        ASREngineConfig,
        AudioConfig,
        ModuleConfig,
        SegmentationConfig,
        SoundFeedbackConfig,
    )
    from asr_engine.engine import ASREngine

    seg = SegmentationConfig(
        initial_silence_timeout_s=initial_silence_timeout_s,
        end_of_speech_timeout_s=end_of_speech_timeout_s,
    )
    if trigger_words is not None:
        seg.trigger_words = trigger_words

    config = ASREngineConfig(
        listen_default_segmentation_mode=listen_default_segmentation_mode,
        dictation_default_segmentation_mode=dictation_default_segmentation_mode,
        segmentation=seg,
        sound_feedback=SoundFeedbackConfig(enabled=False),
        audio=AudioConfig(
            sample_rate=audio_format.sample_rate,
            channels=audio_format.channels,
            encoding=audio_format.encoding,
        ),
        module=ModuleConfig(type=module_type, extra=module_config),
    )
    return ASREngine(
        config,
        on_speech_utterance=on_speech_utterance,
        on_speech_segment=on_speech_segment,
        audio_source=audio_source,
    )


def build_file_engine(
    audio_file: Path | str,
    module_type: str,
    module_config: dict,
    *,
    audio_format: AudioFormat = FORMAT_MP3_44100,
    trailing_silence_s: float = 0.0,
    **kwargs,
):
    """Build an ASREngine that reads *audio_file* through a FileAudioSource.

    Thin wrapper over ``build_engine`` — see it for the accepted keyword args. The
    ``FileAudioSource`` and the engine config are given the same ``audio_format``.
    """
    from asr_engine.audio import FileAudioSource

    source = FileAudioSource(
        str(audio_file),
        audio_format=audio_format,
        trailing_silence_s=trailing_silence_s,
    )
    return build_engine(
        source, module_type, module_config, audio_format=audio_format, **kwargs
    )


async def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    """Poll until a TCP connection to host:port succeeds."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            _, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.05)
    raise TimeoutError(f"Server on port {port} did not start within {timeout} s")


async def start_mcp_server(
    audio_file: Path | str,
    module_type: str,
    module_config: dict,
    port: int,
    trailing_silence_s: float = 0.0,
    engine_overrides: dict | None = None,
    audio_format: AudioFormat = FORMAT_MP3_44100,
) -> tuple[asyncio.subprocess.Process, str]:
    """Start an asr-engine-mcp subprocess.  Returns (process, tmp_config_path).

    The ``engine.audio`` block is written with ``audio_format`` explicitly, so the
    real binary decodes *audio_file* (WAV or MP3) at the matching rate/encoding.
    ``engine_overrides`` is merged into the ``engine`` config block (e.g.
    ``auto_start``, ``auto_start_dictation``, ``dictation_default_segmentation_mode``,
    ``segmentation``, ``listen_default_segmentation_mode``, ``sound_feedback``).
    """
    engine: dict = {
        "audio": {
            "audio_file": str(audio_file),
            "trailing_silence_s": trailing_silence_s,
            "sample_rate": audio_format.sample_rate,
            "channels": audio_format.channels,
            "encoding": audio_format.encoding,
        },
        "module": {"type": module_type, **module_config},
    }
    if engine_overrides:
        engine.update(engine_overrides)
    config = {
        "server": {"host": "127.0.0.1", "port": port},
        "engine": engine,
    }

    # Write a temp config file — the subprocess reads it on startup.
    fd, config_path = tempfile.mkstemp(suffix=".json", prefix="asr_engine_e2e_")
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)

    log.info(
        "Starting MCP server subprocess on port %d (config: %s)", port, config_path
    )
    proc = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "asr-engine-mcp",
        "--config",
        config_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        await _wait_for_port("127.0.0.1", port)
    except TimeoutError:
        proc.terminate()
        await proc.wait()
        os.unlink(config_path)
        raise

    log.info("MCP server subprocess ready on port %d (pid %d)", port, proc.pid)
    return proc, config_path


async def stop_mcp_server(proc: asyncio.subprocess.Process, config_path: str) -> None:
    """Terminate the MCP server subprocess and clean up the temp config."""
    log.info("Stopping MCP server subprocess (pid %d)", proc.pid)
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        log.info("MCP server subprocess stopped cleanly")
    except asyncio.TimeoutError:
        log.warning("MCP server subprocess did not stop within timeout — killing")
        proc.kill()
        await proc.wait()
    finally:
        try:
            os.unlink(config_path)
        except OSError:
            pass
