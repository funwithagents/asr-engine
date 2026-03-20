"""Unit tests for client.py and asr_resource_client.py."""
from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.asr_resource_client import _format_result, main
from asr_mcp.tool_client import McpToolClient


# ---------------------------------------------------------------------------
# _format_result — unit tests
# ---------------------------------------------------------------------------


class TestFormatResult:
    def test_interim_result(self):
        payload = {"transcript": "hello how", "is_final": False, "confidence": None}
        assert _format_result(payload) == "[INTERIM] hello how"

    def test_final_with_confidence(self):
        payload = {"transcript": "Hello, how are you?", "is_final": True, "confidence": 0.98}
        assert _format_result(payload) == "[FINAL  ] Hello, how are you? (confidence: 0.98)"

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
    with patch("asr_mcp.asr_resource_client._run_client", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client"]):
            main()
        called_url = mock_run.call_args[0][0]
    assert called_url == "http://127.0.0.1:8000/mcp"


def test_main_custom_server():
    """main() forwards the --server argument to _run_client."""
    custom_url = "http://192.168.1.10:9000/mcp"
    with patch("asr_mcp.asr_resource_client._run_client", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client", "--server", custom_url]):
            main()
        called_url = mock_run.call_args[0][0]
    assert called_url == custom_url


def test_main_keyboard_interrupt_prints_disconnected(capsys):
    """main() prints [INFO] Disconnected on KeyboardInterrupt."""
    with patch("asr_mcp.asr_resource_client._run_client", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client"]):
            mock_run.side_effect = KeyboardInterrupt()
            main()
    out = capsys.readouterr().out
    assert "[INFO] Disconnected" in out


# ---------------------------------------------------------------------------
# McpToolClient
# ---------------------------------------------------------------------------


def _make_tool_result(payload: dict, is_error: bool = False):
    content_item = MagicMock()
    content_item.text = json.dumps(payload)
    result = MagicMock()
    result.isError = is_error
    result.content = [content_item]
    return result


@pytest.mark.asyncio
async def test_mcp_tool_client_success():
    """Successful tool call returns parsed dict."""
    expected = {"transcript": "hello", "end_reason": "trigger_word"}
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=_make_tool_result(expected))

    with patch("asr_mcp.tool_client.streamable_http_client") as mock_http, \
         patch("asr_mcp.tool_client.ClientSession") as mock_session_cls:
        mock_http.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        client = McpToolClient("http://127.0.0.1:8000/mcp")
        result = await client.call_tool("listen", {})

    assert result == expected


@pytest.mark.asyncio
async def test_mcp_tool_client_progress_callback_forwarded():
    """progress_callback kwarg is forwarded to the underlying session.call_tool."""
    expected = {"transcript": "hello", "end_reason": "trigger_word"}
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=_make_tool_result(expected))

    async def my_progress(_progress, _total, _message):
        pass

    with patch("asr_mcp.tool_client.streamable_http_client") as mock_http, \
         patch("asr_mcp.tool_client.ClientSession") as mock_session_cls:
        mock_http.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        client = McpToolClient("http://127.0.0.1:8000/mcp")
        await client.call_tool("listen", {}, progress_callback=my_progress)

    mock_session.call_tool.assert_called_once_with(
        "listen", {}, progress_callback=my_progress
    )


@pytest.mark.asyncio
async def test_mcp_tool_client_error_raises():
    """Tool error response raises RuntimeError."""
    error_item = MagicMock()
    error_item.text = "A listen session is already in progress."
    error_result = MagicMock()
    error_result.isError = True
    error_result.content = [error_item]

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=error_result)

    with patch("asr_mcp.tool_client.streamable_http_client") as mock_http, \
         patch("asr_mcp.tool_client.ClientSession") as mock_session_cls:
        mock_http.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        client = McpToolClient("http://127.0.0.1:8000/mcp")
        with pytest.raises(RuntimeError, match="already in progress"):
            await client.call_tool("listen", {})
