"""Shared helpers for e2e tests."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def load_api_key() -> str:
    config_path = Path(__file__).parent.parent / "config.json"
    with open(config_path) as f:
        data = json.load(f)
    return data["asr"]["api_key"]


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

    # Write a temp config file — the subprocess reads it on startup.
    fd, config_path = tempfile.mkstemp(suffix=".json", prefix="asr_mcp_e2e_")
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)

    log.info("Starting MCP server subprocess on port %d (config: %s)", port, config_path)
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "asr-mcp-server",
        "--config", config_path,
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
