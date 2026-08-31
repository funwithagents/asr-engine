"""Shared helpers for e2e tests."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import pytest

log = logging.getLogger(__name__)

# Environment variable holding the Deepgram API key for e2e tests. The key never
# lives in the repo — only its variable name does, here.
API_KEY_ENV = "DEEPGRAM_API_KEY"


def load_api_key() -> str:
    """Resolve the Deepgram API key for e2e tests from ``$DEEPGRAM_API_KEY``.

    When that variable is unset the test is skipped rather than failed — e2e is
    opt-in and needs credentials the shell may not have (the keys live in
    ``~/.zshrc``; a non-interactive shell doesn't source it — run under an
    interactive zsh: ``zsh -ic 'uv run pytest tests-e2e'``).
    """
    value = os.environ.get(API_KEY_ENV)
    if not value:
        pytest.skip(
            f"e2e: environment variable '{API_KEY_ENV}' is not set — "
            f"see AGENTS.md 'Live/e2e tests'"
        )
    return value


def build_file_engine(
    audio_file: Path | str,
    module_type: str,
    module_config: dict,
    *,
    segmentation_mode: str = "utterance",
    listen_default_segmentation_mode: str = "trigger_word",
    trigger_words: list[str] | None = None,
    initial_silence_timeout_s: float = 10.0,
    end_of_speech_timeout_s: float = 5.0,
    trailing_silence_s: float = 0.0,
    on_speech_utterance=None,
    on_speech_segment=None,
):
    """Build an ASREngine that reads *audio_file* through a FileAudioSource.

    Drives the engine directly (no MCP server). Sound feedback is disabled.
    """
    from asr_engine.audio import FileAudioSource
    from asr_engine.config import (
        ASREngineConfig,
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
        segmentation_mode=segmentation_mode,
        listen_default_segmentation_mode=listen_default_segmentation_mode,
        segmentation=seg,
        sound_feedback=SoundFeedbackConfig(enabled=False),
        module=ModuleConfig(type=module_type, extra=module_config),
    )
    source = FileAudioSource(str(audio_file), trailing_silence_s=trailing_silence_s)
    return ASREngine(
        config,
        on_speech_utterance=on_speech_utterance,
        on_speech_segment=on_speech_segment,
        audio_source=source,
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

    ``engine_overrides`` is merged into the ``engine`` config block (e.g.
    ``auto_start``, ``segmentation_mode``, ``segmentation``,
    ``listen_default_segmentation_mode``, ``sound_feedback``).
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
