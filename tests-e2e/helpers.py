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

_E2E_CONFIG = Path(__file__).parent / "e2e.config.json"


def load_api_key() -> str:
    """Resolve the Deepgram API key for e2e tests from the committed e2e.config.json.

    The config names an environment variable via ``asr.api_key_env`` (see the
    ``resolve_api_key`` helper). When that variable is unset the test is skipped
    rather than failed — e2e is opt-in and needs credentials the shell may not
    have (the keys live in ``~/.zshrc``; a non-interactive shell doesn't source
    it — run under an interactive zsh: ``zsh -ic 'uv run pytest tests-e2e'``).
    """
    with open(_E2E_CONFIG) as f:
        asr = json.load(f)["asr"]

    api_key = asr.get("api_key")
    if api_key:
        return api_key

    env_name = asr.get("api_key_env")
    if env_name:
        value = os.environ.get(env_name)
        if not value:
            pytest.skip(
                f"e2e: environment variable '{env_name}' (from e2e.config.json "
                f"api_key_env) is not set — see AGENTS.md 'Live/e2e tests'"
            )
        return value

    pytest.skip("e2e: e2e.config.json defines neither api_key nor api_key_env")


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
    engine_config: dict | None = None,
    listen_config: dict | None = None,
) -> tuple[asyncio.subprocess.Process, str]:
    """Start an asr-mcp-server subprocess.  Returns (process, tmp_config_path)."""
    config = {
        "server": {"host": "127.0.0.1", "port": port},
        "audio": {
            "audio_file": str(audio_file),
            "trailing_silence_s": trailing_silence_s,
        },
        "asr": {"type": module_type, **module_config},
    }
    if engine_config is not None:
        config["engine"] = engine_config
    if listen_config is not None:
        config["listen"] = listen_config

    # Write a temp config file — the subprocess reads it on startup.
    fd, config_path = tempfile.mkstemp(suffix=".json", prefix="asr_mcp_e2e_")
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)

    log.info(
        "Starting MCP server subprocess on port %d (config: %s)", port, config_path
    )
    proc = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "asr-mcp-server",
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
