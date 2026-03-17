"""Unit tests for client.py."""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import mcp.types as types
from pydantic import AnyUrl

from asr_mcp.client import _format_result, _run, main


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

    def test_empty_transcript_interim(self):
        payload = {"transcript": "", "is_final": False, "confidence": None}
        assert _format_result(payload) == "[INTERIM] "

    def test_empty_transcript_final(self):
        payload = {"transcript": "", "is_final": True, "confidence": None}
        assert _format_result(payload) == "[FINAL  ] "

    def test_interim_prefix(self):
        payload = {"transcript": "anything", "is_final": False, "confidence": 0.5}
        result = _format_result(payload)
        assert result.startswith("[INTERIM]")

    def test_final_prefix(self):
        payload = {"transcript": "anything", "is_final": True, "confidence": None}
        result = _format_result(payload)
        assert result.startswith("[FINAL  ]")

    def test_confidence_zero(self):
        payload = {"transcript": "test", "is_final": True, "confidence": 0.0}
        result = _format_result(payload)
        assert "confidence: 0.0" in result

    def test_confidence_one(self):
        payload = {"transcript": "perfect", "is_final": True, "confidence": 1.0}
        result = _format_result(payload)
        assert "confidence: 1.0" in result

    def test_transcript_included_in_output(self):
        transcript = "The quick brown fox"
        payload = {"transcript": transcript, "is_final": True, "confidence": None}
        assert transcript in _format_result(payload)

    def test_interim_does_not_include_confidence(self):
        payload = {"transcript": "partial", "is_final": False, "confidence": 0.9}
        result = _format_result(payload)
        assert "confidence" not in result


# ---------------------------------------------------------------------------
# _format_result — property-style parametrize tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transcript,is_final,confidence",
    [
        ("hello", True, 0.95),
        ("hello", True, None),
        ("partial", False, None),
        ("partial", False, 0.7),
        ("", True, 0.0),
        ("multi word transcript here", False, None),
        ("unicode: café naïve", True, 0.88),
    ],
)
def test_format_result_structure(transcript, is_final, confidence):
    payload = {"transcript": transcript, "is_final": is_final, "confidence": confidence}
    result = _format_result(payload)

    if is_final:
        assert result.startswith("[FINAL  ]")
        assert transcript in result
        if confidence is not None:
            assert f"confidence: {confidence}" in result
        else:
            assert "confidence" not in result
    else:
        assert result.startswith("[INTERIM]")
        assert transcript in result
        assert "confidence" not in result


# ---------------------------------------------------------------------------
# main() — argument parsing
# ---------------------------------------------------------------------------


def test_main_default_server(capsys):
    """main() uses the default server URL when --server is not supplied."""
    with patch("asr_mcp.client._run_with_reconnect", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client"]):
            main()
        called_url = mock_run.call_args[0][0]
    assert called_url == "http://127.0.0.1:8080/mcp"


def test_main_custom_server(capsys):
    """main() forwards the --server argument to _run_with_reconnect."""
    custom_url = "http://192.168.1.10:9000/mcp"
    with patch("asr_mcp.client._run_with_reconnect", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client", "--server", custom_url]):
            main()
        called_url = mock_run.call_args[0][0]
    assert called_url == custom_url


def test_main_keyboard_interrupt_prints_disconnected(capsys):
    """main() prints [INFO] Disconnected on KeyboardInterrupt."""
    with patch("asr_mcp.client._run_with_reconnect", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client"]):
            mock_run.side_effect = KeyboardInterrupt()
            main()
    out = capsys.readouterr().out
    assert "[INFO] Disconnected" in out


# ---------------------------------------------------------------------------
# _on_message handler — notification dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_resource_updated_reads_and_prints(capsys):
    """When a ResourceUpdatedNotification arrives, the resource is read and printed."""
    # Build a fake ClientSession that returns a known payload on read_resource
    fake_payload = {"transcript": "hello world", "is_final": True, "confidence": 0.95}
    fake_content = MagicMock()
    fake_content.text = json.dumps(fake_payload)
    fake_result = MagicMock()
    fake_result.contents = [fake_content]

    mock_session = AsyncMock()
    mock_session.read_resource = AsyncMock(return_value=fake_result)
    mock_session.initialize = AsyncMock()
    mock_session.subscribe_resource = AsyncMock()
    mock_session.unsubscribe_resource = AsyncMock()

    # Build a ResourceUpdatedNotification
    notification = types.ServerNotification(
        root=types.ResourceUpdatedNotification(
            params=types.ResourceUpdatedNotificationParams(
                uri=AnyUrl("asr://result"),
            )
        )
    )

    # Patch streamable_http_client and ClientSession to use our mock
    async def fake_http_client(url):
        rs, ws = MagicMock(), MagicMock()
        yield rs, ws, lambda: None

    class FakeSessionCM:
        def __init__(self, *args, **kwargs):
            self._session = mock_session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *args):
            pass

    captured_handler: list = []

    class CapturingSessionCM(FakeSessionCM):
        def __init__(self, rs, ws, message_handler=None, **kwargs):
            super().__init__()
            captured_handler.append(message_handler)

    with patch("asr_mcp.client.streamable_http_client") as mock_transport:
        with patch("asr_mcp.client.ClientSession", CapturingSessionCM):
            mock_transport.return_value.__aenter__ = AsyncMock(
                return_value=(MagicMock(), MagicMock(), lambda: None)
            )
            mock_transport.return_value.__aexit__ = AsyncMock(return_value=None)

            # Run _run briefly so the handler is registered, then cancel
            task = asyncio.create_task(_run("http://localhost:8080/mcp"))
            await asyncio.sleep(0)  # let the task start

            # Inject the notification via the captured handler
            if captured_handler:
                await captured_handler[0](notification)

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # The read was requested
    mock_session.read_resource.assert_called()

    out = capsys.readouterr().out
    assert "[FINAL  ]" in out
    assert "hello world" in out


@pytest.mark.asyncio
async def test_on_message_non_notification_ignored():
    """Non-notification messages are silently ignored."""
    mock_session = AsyncMock()
    mock_session.read_resource = AsyncMock()

    non_notification = Exception("some error")

    class CapturingSessionCM:
        def __init__(self, rs, ws, message_handler=None, **kwargs):
            self._handler = message_handler

        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    captured_handler: list = []

    class RecordingSessionCM(CapturingSessionCM):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured_handler.append(self._handler)

    with patch("asr_mcp.client.streamable_http_client") as mock_transport:
        with patch("asr_mcp.client.ClientSession", RecordingSessionCM):
            mock_transport.return_value.__aenter__ = AsyncMock(
                return_value=(MagicMock(), MagicMock(), lambda: None)
            )
            mock_transport.return_value.__aexit__ = AsyncMock(return_value=None)

            task = asyncio.create_task(_run("http://localhost:8080/mcp"))
            await asyncio.sleep(0)

            if captured_handler:
                await captured_handler[0](non_notification)

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # read_resource must NOT have been called
    mock_session.read_resource.assert_not_called()
