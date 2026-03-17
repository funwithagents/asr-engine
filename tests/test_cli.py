"""Unit tests for asr_mcp.cli."""
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
    """cli.main() calls asyncio.run(run_server(config)) with the loaded config."""
    from unittest.mock import AsyncMock
    from asr_mcp import cli

    path = write_config(tmp_path, {"asr": {"type": "deepgram_v1", "api_key": "x"}})
    sys.argv = ["asr-mcp-server", "--config", path]

    mock_run_server = AsyncMock()
    with patch("asr_mcp.cli.run_server", mock_run_server):
        cli.main()

    mock_run_server.assert_called_once()
    config_arg = mock_run_server.call_args[0][0]
    assert config_arg.asr.type == "deepgram_v1"


def test_cli_exits_on_missing_file(tmp_path: Path, capsys) -> None:
    from asr_mcp import cli

    sys.argv = ["asr-mcp-server", "--config", str(tmp_path / "nope.json")]
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_cli_exits_on_invalid_json(tmp_path: Path, capsys) -> None:
    from asr_mcp import cli

    p = tmp_path / "bad.json"
    p.write_text("{not valid")
    sys.argv = ["asr-mcp-server", "--config", str(p)]
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1


def test_cli_exits_on_unknown_asr_type(tmp_path: Path, capsys) -> None:
    """validate_asr_type now runs inside run_server; ValueError propagates to cli."""
    from asr_mcp import cli

    path = write_config(tmp_path, {"asr": {"type": "bogus"}})
    sys.argv = ["asr-mcp-server", "--config", path]
    # run_server raises ValueError; asyncio.run re-raises it; cli catches it
    with patch(
        "asr_mcp.cli.asyncio.run",
        side_effect=ValueError("Unknown ASR type 'bogus'"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
    assert exc_info.value.code == 1
    assert "bogus" in capsys.readouterr().err
