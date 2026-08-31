"""Unit tests for the demo resource client (asr_resource_client.py).

The MCP tool client used to live in the package (``tool_client.py``); it is now a
test-only helper at ``tests-e2e/mcp_tool_client.py`` and is exercised live by
``tests-e2e/test_mcp_tool_client.py``.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

from asr_engine.asr_resource_client import _format_result, main

# ---------------------------------------------------------------------------
# _format_result — unit tests
# ---------------------------------------------------------------------------


class TestFormatResult:
    def test_interim_result(self):
        payload = {"transcript": "hello how", "is_final": False, "confidence": None}
        assert _format_result(payload) == "[INTERIM] hello how"

    def test_final_with_confidence(self):
        payload = {
            "transcript": "Hello, how are you?",
            "is_final": True,
            "confidence": 0.98,
        }
        assert (
            _format_result(payload)
            == "[FINAL  ] Hello, how are you? (confidence: 0.98)"
        )

    def test_final_without_confidence(self):
        payload = {"transcript": "Hello!", "is_final": True, "confidence": None}
        assert _format_result(payload) == "[FINAL  ] Hello!"

    def test_confidence_zero(self):
        """confidence=0.0 must appear — zero is falsy but still a valid score."""
        payload = {"transcript": "test", "is_final": True, "confidence": 0.0}
        assert "confidence: 0.0" in _format_result(payload)


# ---------------------------------------------------------------------------
# main() — argument parsing
# ---------------------------------------------------------------------------


def test_main_default_server():
    """main() uses the default server URL when --server is not supplied."""
    with patch(
        "asr_engine.asr_resource_client._run_client", new_callable=AsyncMock
    ) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client"]):
            main()
        called_url = mock_run.call_args[0][0]
    assert called_url == "http://127.0.0.1:8000/mcp"


def test_main_custom_server():
    """main() forwards the --server argument to _run_client."""
    custom_url = "http://192.168.1.10:9000/mcp"
    with patch(
        "asr_engine.asr_resource_client._run_client", new_callable=AsyncMock
    ) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client", "--server", custom_url]):
            main()
        called_url = mock_run.call_args[0][0]
    assert called_url == custom_url


def test_main_keyboard_interrupt_prints_disconnected(capsys):
    """main() prints [INFO] Disconnected on KeyboardInterrupt."""
    with patch(
        "asr_engine.asr_resource_client._run_client", new_callable=AsyncMock
    ) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client"]):
            mock_run.side_effect = KeyboardInterrupt()
            main()
    out = capsys.readouterr().out
    assert "[INFO] Disconnected" in out
