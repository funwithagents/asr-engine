"""Unit tests for client.py."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from asr_mcp.client import _format_result, main


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


def test_main_default_server():
    """main() uses the default server URL when --server is not supplied."""
    with patch("asr_mcp.client._run_client", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client"]):
            main()
        called_url = mock_run.call_args[0][0]
    assert called_url == "http://127.0.0.1:8080/mcp"


def test_main_custom_server():
    """main() forwards the --server argument to _run_client."""
    custom_url = "http://192.168.1.10:9000/mcp"
    with patch("asr_mcp.client._run_client", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client", "--server", custom_url]):
            main()
        called_url = mock_run.call_args[0][0]
    assert called_url == custom_url


def test_main_keyboard_interrupt_prints_disconnected(capsys):
    """main() prints [INFO] Disconnected on KeyboardInterrupt."""
    with patch("asr_mcp.client._run_client", new_callable=AsyncMock) as mock_run:
        with patch.object(sys, "argv", ["asr-mcp-client"]):
            mock_run.side_effect = KeyboardInterrupt()
            main()
    out = capsys.readouterr().out
    assert "[INFO] Disconnected" in out
