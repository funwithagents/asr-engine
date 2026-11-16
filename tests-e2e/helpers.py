"""Shared helpers for e2e tests."""

from __future__ import annotations

import asyncio
import importlib
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
# 24 kHz contract (kyutai) at their own rate. A ScriptableAudioSource plays files
# recorded in its own format, so each fixture is paired with its AudioFormat; the
# engine converts that format to the module's when they differ (MODULES_CONVERTED).
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
FIXTURE_BLUE_VALIDATE_WAV_16000 = FIXTURES_DIR / "sample_16000_theskyisbluevalidate.wav"
FIXTURE_BLUE_VALIDATE_WAV_24000 = FIXTURES_DIR / "sample_24000_theskyisbluevalidate.wav"


@dataclass(frozen=True)
class ModuleAudio:
    """The audio a ``MODULES`` row is driven with: a format its module supports,
    and the two conformance fixtures recorded in that format."""

    audio_format: AudioFormat
    blue: Path  # "the sky is blue"
    blue_validate: Path  # "the sky is blue validate"


AUDIO_MP3_44100 = ModuleAudio(FORMAT_MP3_44100, FIXTURE_BLUE, FIXTURE_BLUE_VALIDATE)
# 16 kHz mono: the rate batch models take (specs/batch-asr-module.md).
AUDIO_WAV_16000 = ModuleAudio(
    FORMAT_WAV_16000, FIXTURE_BLUE_WAV_16000, FIXTURE_BLUE_VALIDATE_WAV_16000
)
AUDIO_WAV_24000 = ModuleAudio(
    FORMAT_WAV_24000, FIXTURE_BLUE_WAV_24000, FIXTURE_BLUE_VALIDATE_WAV_24000
)

# Deepgram's key env var. This name is Deepgram-specific: other modules declare their
# own via ``api_key_env`` in their ``MODULES`` row / provider config, and a module that
# needs no key declares none. The key value never lives in the repo — only the variable
# name does. Tests pass the name to modules via the ``api_key_env`` config field and let
# ``resolve_api_key`` read the value; they never read the literal key themselves.
DEEPGRAM_API_KEY_ENV = "DEEPGRAM_API_KEY"
# Mistral's, for the mistral-realtime row (same rules).
MISTRAL_API_KEY_ENV = "MISTRAL_API_KEY"
# OpenAI's, for the openai-realtime-compatible and openai-transcriptions-compatible
# rows (same rules).
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


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


# Local-model modules, by type → (the module's download module, the config key
# naming its model repo). They need no API key, so ``require_api_key`` never skips
# them; instead they run only once their weights are cached, because an unguarded
# run would download gigabytes inside the per-test timeout. Each download module
# exposes ``DEFAULT_REPO``, ``is_cached(repo)`` and a ``python -m`` entry point.
LOCAL_MODELS = {
    "kyutai": ("asr_engine.modules.kyutai.download", "hf_repo"),
    "mlx-audio": ("asr_engine.modules.mlx_audio.download", "model"),
}


def require_local_model(module_type: str, module_config: dict) -> None:
    """Skip the calling test unless this local-model module can run here.

    The second skip axis, next to ``require_api_key``: a module listed in
    ``LOCAL_MODELS`` runs only when its backend dependency is installed (the
    module builds) **and** its weights are already in the Hugging Face cache. The
    skip message gives the command that fetches them. Any other module is never
    skipped here.
    """
    entry = LOCAL_MODELS.get(module_type)
    if entry is None:
        return
    from asr_engine.modules import resolve_module_class

    try:
        resolve_module_class(module_type)(config=module_config)
    except ImportError as exc:
        pytest.skip(f"e2e: {exc}")
    download_module, repo_key = entry
    download = importlib.import_module(download_module)
    repo = module_config.get(repo_key, download.DEFAULT_REPO)
    if not download.is_cached(repo):
        pytest.skip(
            f"e2e: local model '{module_type}' needs its weights cached first — "
            f"run: uv run python -m {download_module} {repo}"
        )


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


# Scripts for the ``fake`` module (specs/modules/fake.md), one per fixture. The fake
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
        "deepgram-v1",
        {"model": "nova-3", "api_key_env": DEEPGRAM_API_KEY_ENV},
        3.0,
        AUDIO_MP3_44100,
        id="deepgram-v1",
    ),
    pytest.param(
        "deepgram-v2",
        {"model": "flux-general-en", "api_key_env": DEEPGRAM_API_KEY_ENV},
        3.0,
        AUDIO_MP3_44100,
        id="deepgram-v2",
    ),
    # Voxtral closes a turn ~1.2-2.3 s after speech ends (measured, see
    # specs/modules/mistral.md); its silence fallback is delay + 2.0 s ≈ 2.5 s.
    pytest.param(
        "mistral-realtime",
        {"api_key_env": MISTRAL_API_KEY_ENV},
        3.0,
        AUDIO_MP3_44100,
        id="mistral-realtime",
    ),
    # Hosted OpenAI takes 24 kHz only (measured, see
    # specs/modules/openai-realtime-compatible.md); its server VAD finals land
    # ~1.1 s after speech ends.
    pytest.param(
        "openai-realtime-compatible",
        {"model": "gpt-4o-transcribe", "api_key_env": OPENAI_API_KEY_ENV},
        3.0,
        AUDIO_WAV_24000,
        id="openai-realtime-compatible",
    ),
    # Batch over HTTP: one upload per utterance, VAD-required like mlx-audio
    # (MODULES_UNGATED leaves it out). A final lands one request (~0.5-1 s,
    # measured) after the gate's marker.
    pytest.param(
        "openai-transcriptions-compatible",
        {"model": "gpt-4o-transcribe", "api_key_env": OPENAI_API_KEY_ENV},
        3.0,
        AUDIO_WAV_16000,
        id="openai-transcriptions-compatible",
    ),
    # Local model: no key; runs once its weights are cached (require_local_model).
    # silence_s must exceed the model's 0.5 s text delay + the 2.0 s fallback
    # finalize timer, so the default 3.0 s would be marginal.
    pytest.param(
        "kyutai",
        {"hf_repo": "kyutai/stt-1b-en_fr-candle"},
        4.0,
        AUDIO_WAV_24000,
        id="kyutai",
    ),
    # Local batch model on Apple Silicon, gated like kyutai: it needs the
    # mlx-audio extra (in dev on Apple Silicon) and its weights cached, or
    # require_local_model skips. VAD-required, so it runs only behind the gate
    # (MODULES_UNGATED leaves it out). A final lands one decode (~0.03 s) after
    # the gate's marker.
    pytest.param(
        "mlx-audio",
        {"model": "mlx-community/parakeet-tdt-0.6b-v3"},
        2.0,
        AUDIO_WAV_16000,
        id="mlx-audio",
    ),
]

# Modules declaring VAD_GATING = "required" (batch modules): they produce no final
# without the gate, so they have no ungated conformance case. Named here rather
# than read from the class, so collecting the table imports no module.
VAD_REQUIRED_MODULES = frozenset({"mlx-audio", "openai-transcriptions-compatible"})

# Param table for ``test_engine_streams``: every row a module can run ungated.
MODULES_UNGATED = [row for row in MODULES if row.values[0] not in VAD_REQUIRED_MODULES]


def _vad_cases() -> list:
    """``MODULES`` crossed with the VAD settings each row is run under.

    Every row runs once with Silero's defaults. ``deepgram-v2`` runs twice,
    because Flux has two end-of-utterance paths behind a gate (specs/vad.md
    "Letting the backend end the turn first"): with the recommended 1500 ms
    hangover Flux hears the silence and ends the turn itself; with a 300 ms one
    the gate closes first and the module's injected silence ends it.
    """
    variants = {
        "deepgram-v2": [
            ("flux_ends_turn", {"min_silence_duration_ms": 1500}),
            ("marker_ends_turn", {"min_silence_duration_ms": 300}),
        ]
    }
    cases = []
    for row in MODULES:
        row_id = str(row.id)
        for suffix, vad in variants.get(row_id, [("", {})]):
            case_id = f"{row_id}-{suffix}" if suffix else row_id
            cases.append(
                pytest.param(*row.values, {"type": "silero", **vad}, id=case_id)
            )
    return cases


# Param table for ``test_engine_modules_vad``: (module_type, module_config,
# silence_s, audio, vad_block).
MODULES_VAD = _vad_cases()


# Modules run on the 44.1 kHz MP3s while fed another rate, so the engine converts
# in between (specs/format-conversion.md): module type → the rate it is fed (its
# default, set explicitly). deepgram-v2 and mistral-realtime share the path.
CONVERTED_RATES: dict[str, int] = {
    "deepgram-v1": 16000,
    "openai-realtime-compatible": 24000,
    "openai-transcriptions-compatible": 16000,
    "kyutai": 24000,
    "mlx-audio": 16000,
}


def _converted_cases() -> list:
    cases = []
    for row in MODULES:
        module_type, module_config, silence_s, _audio = row.values
        module_sample_rate = CONVERTED_RATES.get(str(module_type))
        if module_sample_rate is None:
            continue
        vad = {"type": "silero"} if module_type in VAD_REQUIRED_MODULES else None
        cases.append(
            pytest.param(
                module_type,
                module_config,
                silence_s,
                module_sample_rate,
                vad,
                id=str(row.id),
            )
        )
    return cases


# Param table for ``test_engine_modules_converted``: (module_type, module_config,
# silence_s, module_sample_rate, vad_block).
MODULES_CONVERTED = _converted_cases()


def require_vad_runtime() -> None:
    """Skip the calling test where the Silero VAD cannot run.

    ``onnxruntime`` publishes no Intel macOS wheel, so the ``silero`` extra
    installs without it there (see specs/project.md).
    """
    pytest.importorskip("onnxruntime", reason="e2e: the silero VAD needs onnxruntime")


def build_engine(
    audio_source,
    module_type: str,
    module_config: dict,
    *,
    audio_format: AudioFormat = FORMAT_MP3_44100,
    module_sample_rate: int | None = None,
    listen_default_segmentation_mode: str = "trigger_word",
    dictation_default_segmentation_mode: str = "trigger_word",
    trigger_words: list[str] | None = None,
    initial_silence_timeout_s: float = 10.0,
    end_of_speech_timeout_s: float = 5.0,
    on_speech_utterance=None,
    on_speech_segment=None,
    vad: dict | None = None,
):
    """Build an ASREngine driven by *audio_source* (no MCP server).

    ``vad`` is an ``engine.vad`` block (e.g. ``{"type": "silero"}``); omitted, the
    engine runs ungated.

    Sound feedback is disabled. ``audio_source`` is any object matching the
    ``AudioSource`` protocol — e.g. a ``FileAudioSource`` (see
    ``build_file_engine``) or a ``ScriptableAudioSource`` for hand-sequenced audio.
    ``audio_format`` is what the source produces, set as
    ``engine.audio.audio_format``. ``module_sample_rate`` is the rate in
    ``engine.module.audio_format``; left out, the module
    is fed the source's rate, so the audio reaches it unconverted. Pass another
    rate to put the format converter in between.
    """
    from asr_engine.config import (
        ASREngineConfig,
        AudioConfig,
        AudioFormatConfig,
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
            audio_format=AudioFormatConfig(
                sample_rate=audio_format.sample_rate,
                channels=audio_format.channels,
                encoding=audio_format.encoding,
            )
        ),
        module=ModuleConfig(
            type=module_type,
            audio_format=AudioFormatConfig(
                sample_rate=module_sample_rate or audio_format.sample_rate
            ),
            extra=module_config,
        ),
    )
    if vad is not None:
        # Through from_dict so the block is validated as a config file's would be.
        config.vad = ASREngineConfig.from_dict(
            {"module": {"type": module_type}, "vad": vad}
        ).vad
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
) -> tuple[asyncio.subprocess.Process, str]:
    """Start an asr-engine-mcp subprocess.  Returns (process, tmp_config_path).

    The real binary decodes *audio_file* (WAV or MP3) at the file's own rate and
    converts it to the module's format.
    ``engine_overrides`` is merged into the ``engine`` config block (e.g.
    ``auto_start``, ``auto_start_dictation``, ``dictation_default_segmentation_mode``,
    ``segmentation``, ``listen_default_segmentation_mode``, ``sound_feedback``).
    """
    engine: dict = {
        "audio": {
            "audio_file": str(audio_file),
            "trailing_silence_s": trailing_silence_s,
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
