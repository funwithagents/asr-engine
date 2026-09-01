"""Unit tests for asr_engine.mcp_server_cli."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def write_config(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_cli_runs_server(tmp_path: Path) -> None:
    """mcp_server_cli.main() calls asyncio.run(run_server(config)) with the loaded config."""
    from unittest.mock import AsyncMock

    from asr_engine import mcp_server_cli

    path = write_config(
        tmp_path, {"engine": {"module": {"type": "deepgram_v1", "api_key": "x"}}}
    )
    sys.argv = ["asr-engine-mcp", "--config", path]

    mock_run_server = AsyncMock()
    with patch("asr_engine.mcp_server_cli.run_server", mock_run_server):
        mcp_server_cli.main()

    mock_run_server.assert_called_once()
    config_arg = mock_run_server.call_args[0][0]
    assert config_arg.engine.module.type == "deepgram_v1"
    # Default level is INFO and reaches run_server.
    assert mock_run_server.call_args.kwargs["log_level"] == "INFO"


def test_cli_passes_log_level(tmp_path: Path) -> None:
    """--log-level is configured and forwarded to run_server."""
    from unittest.mock import AsyncMock

    from asr_engine import mcp_server_cli

    path = write_config(
        tmp_path, {"engine": {"module": {"type": "deepgram_v1", "api_key": "x"}}}
    )
    sys.argv = ["asr-engine-mcp", "--config", path, "--log-level", "DEBUG"]

    mock_run_server = AsyncMock()
    with (
        patch("asr_engine.mcp_server_cli.run_server", mock_run_server),
        patch("asr_engine.mcp_server_cli.setup_logging") as mock_setup,
    ):
        mcp_server_cli.main()

    mock_setup.assert_called_once_with("DEBUG")
    assert mock_run_server.call_args.kwargs["log_level"] == "DEBUG"


def test_cli_rejects_invalid_log_level(tmp_path: Path) -> None:
    """An unknown --log-level is rejected by argparse (exit code 2)."""
    from asr_engine import mcp_server_cli

    path = write_config(
        tmp_path, {"engine": {"module": {"type": "deepgram_v1", "api_key": "x"}}}
    )
    sys.argv = ["asr-engine-mcp", "--config", path, "--log-level", "LOUD"]
    with pytest.raises(SystemExit) as exc_info:
        mcp_server_cli.main()
    assert exc_info.value.code == 2


def test_cli_exits_on_missing_file(tmp_path: Path, capsys) -> None:
    from asr_engine import mcp_server_cli

    sys.argv = ["asr-engine-mcp", "--config", str(tmp_path / "nope.json")]
    with pytest.raises(SystemExit) as exc_info:
        mcp_server_cli.main()
    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_cli_exits_on_invalid_json(tmp_path: Path, capsys) -> None:
    from asr_engine import mcp_server_cli

    p = tmp_path / "bad.json"
    p.write_text("{not valid")
    sys.argv = ["asr-engine-mcp", "--config", str(p)]
    with pytest.raises(SystemExit) as exc_info:
        mcp_server_cli.main()
    assert exc_info.value.code == 1


def test_cli_exits_on_unknown_asr_type(tmp_path: Path, capsys) -> None:
    """validate_asr_type now runs inside run_server; ValueError propagates to cli."""
    from asr_engine import mcp_server_cli

    path = write_config(tmp_path, {"engine": {"module": {"type": "bogus"}}})
    sys.argv = ["asr-engine-mcp", "--config", path]
    # run_server raises ValueError; asyncio.run re-raises it; cli catches it
    with patch(
        "asr_engine.mcp_server_cli.asyncio.run",
        side_effect=ValueError("Unknown ASR type 'bogus'"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            mcp_server_cli.main()
    assert exc_info.value.code == 1
    assert "bogus" in capsys.readouterr().err
