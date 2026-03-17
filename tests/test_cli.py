"""Unit tests for asr_mcp.cli."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def write_config(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_cli_prints_banner(tmp_path: Path, capsys) -> None:
    from asr_mcp import cli

    path = write_config(
        tmp_path,
        {"server": {"host": "0.0.0.0", "port": 9090}, "asr": {"type": "fake"}},
    )
    import asr_mcp.modules as mod

    fake_module_class = MagicMock(return_value=MagicMock())
    original = dict(mod.REGISTRY)
    mod.REGISTRY["fake"] = fake_module_class
    try:
        sys.argv = ["asr-mcp-server", "--config", path]
        with patch("asr_mcp.cli.asyncio.run", side_effect=lambda coro: coro.close()):
            cli.main()
    finally:
        mod.REGISTRY.clear()
        mod.REGISTRY.update(original)

    out = capsys.readouterr().out
    assert "0.0.0.0" in out
    assert "9090" in out
    assert "fake" in out


def test_cli_exits_on_missing_file(tmp_path: Path, capsys) -> None:
    from asr_mcp import cli

    sys.argv = ["asr-mcp-server", "--config", str(tmp_path / "nope.json")]
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_cli_exits_on_unknown_asr_type(tmp_path: Path, capsys) -> None:
    from asr_mcp import cli

    path = write_config(tmp_path, {"asr": {"type": "bogus"}})
    sys.argv = ["asr-mcp-server", "--config", path]
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
