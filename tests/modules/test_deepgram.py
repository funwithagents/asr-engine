"""Unit tests for DeepgramModule — Plan 05."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.modules.base import ASRResult
from asr_mcp.modules.deepgram import DeepgramModule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_turn_info(
    transcript: str,
    event: str = "Update",
    confidence: float = 0.9,
) -> Any:
    """Build a minimal ListenV2TurnInfo-like object."""
    from deepgram.listen.v2.types.listen_v2turn_info import ListenV2TurnInfo

    return ListenV2TurnInfo.construct(
        type="TurnInfo",
        request_id="req-1",
        sequence_id=1.0,
        event=event,
        turn_index=0.0,
        audio_window_start=0.0,
        audio_window_end=1.0,
        transcript=transcript,
        words=[],
        end_of_turn_confidence=confidence,
    )


# ---------------------------------------------------------------------------
# __init__ — config validation
# ---------------------------------------------------------------------------


def test_init_missing_api_key_raises() -> None:
    with pytest.raises(ValueError, match="api_key"):
        DeepgramModule(config={})


def test_init_empty_api_key_raises() -> None:
    with pytest.raises(ValueError, match="api_key"):
        DeepgramModule(config={"api_key": ""})


def test_init_stores_api_key() -> None:
    m = DeepgramModule(config={"api_key": "sk-test"})
    assert m._api_key == "sk-test"


def test_init_defaults() -> None:
    m = DeepgramModule(config={"api_key": "sk-test"})
    assert m._model == "flux-general-en"
    assert m._eot_threshold == 0.7
    assert m._eot_timeout_ms == 5000


def test_init_custom_values() -> None:
    m = DeepgramModule(
        config={
            "api_key": "sk-test",
            "model": "flux-custom",
            "eot_threshold": 0.5,
            "eot_timeout_ms": 3000,
        }
    )
    assert m._model == "flux-custom"
    assert m._eot_threshold == 0.5
    assert m._eot_timeout_ms == 3000


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_sets_event() -> None:
    m = DeepgramModule(config={"api_key": "sk-test"})
    assert not m._stop_event.is_set()
    await m.stop()
    assert m._stop_event.is_set()


# ---------------------------------------------------------------------------
# _drain_queue_for
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_queue_for_removes_items() -> None:
    m = DeepgramModule(config={"api_key": "sk-test"})
    q: asyncio.Queue[bytes] = asyncio.Queue()
    for _ in range(5):
        q.put_nowait(b"chunk")

    await m._drain_queue_for(q, duration=0.05)
    assert q.empty()


@pytest.mark.asyncio
async def test_drain_queue_for_returns_after_duration() -> None:
    m = DeepgramModule(config={"api_key": "sk-test"})
    q: asyncio.Queue[bytes] = asyncio.Queue()  # empty — will timeout-drain

    import time

    start = time.monotonic()
    await m._drain_queue_for(q, duration=0.15)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.14


# ---------------------------------------------------------------------------
# _audio_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_loop_sends_chunks() -> None:
    m = DeepgramModule(config={"api_key": "sk-test"})
    conn = MagicMock()
    conn.send_media = AsyncMock()
    conn._send = AsyncMock()

    q: asyncio.Queue[bytes] = asyncio.Queue()
    q.put_nowait(b"frame1")
    q.put_nowait(b"frame2")

    async def run():
        # Stop the loop after processing the two frames
        await m._audio_loop(conn, q)

    # After two sends, stop() so the loop exits
    async def stopper():
        # Give the loop time to consume both frames
        while conn.send_media.call_count < 2:
            await asyncio.sleep(0)
        await m.stop()

    await asyncio.gather(run(), stopper())

    assert conn.send_media.call_count == 2
    conn.send_media.assert_any_call(b"frame1")
    conn.send_media.assert_any_call(b"frame2")


@pytest.mark.asyncio
async def test_audio_loop_sends_keepalive_on_timeout() -> None:
    m = DeepgramModule(config={"api_key": "sk-test"})
    conn = MagicMock()
    conn.send_media = AsyncMock()

    keepalive_sent = asyncio.Event()

    async def _track_send(data):
        if data == {"type": "KeepAlive"}:
            keepalive_sent.set()

    conn._send = AsyncMock(side_effect=_track_send)

    q: asyncio.Queue[bytes] = asyncio.Queue()  # empty — will timeout

    async def run():
        await m._audio_loop(conn, q)

    async def stopper():
        await asyncio.wait_for(keepalive_sent.wait(), timeout=10.0)
        await m.stop()

    await asyncio.gather(run(), stopper())

    conn._send.assert_called_with({"type": "KeepAlive"})


@pytest.mark.asyncio
async def test_audio_loop_exits_when_stopped() -> None:
    m = DeepgramModule(config={"api_key": "sk-test"})
    await m.stop()  # pre-set the stop event

    conn = MagicMock()
    conn.send_media = AsyncMock()
    conn._send = AsyncMock()

    q: asyncio.Queue[bytes] = asyncio.Queue()

    # Should return immediately without calling anything
    await m._audio_loop(conn, q)

    conn.send_media.assert_not_called()
    conn._send.assert_not_called()


# ---------------------------------------------------------------------------
# message handler logic (on_message)
# ---------------------------------------------------------------------------
# We test the handler by running start() against a fully mocked SDK connection.
# The mock conn yields one TurnInfo message and then keeps the listen_task alive
# until we call stop().


def _make_mock_conn(messages: list[Any]) -> MagicMock:
    """
    Build a mock AsyncV2SocketClient whose start_listening() emits *messages*
    via EventType.MESSAGE handlers, then blocks until cancelled.
    """
    from deepgram.core.events import EventType

    conn = MagicMock()
    conn.send_media = AsyncMock()
    conn._send = AsyncMock()
    conn.send_close_stream = AsyncMock()
    callbacks: dict = {}

    def register(event, cb):
        callbacks.setdefault(event, []).append(cb)

    conn.on.side_effect = register

    async def start_listening():
        # Emit each message through registered MESSAGE handlers
        for msg in messages:
            for cb in callbacks.get(EventType.MESSAGE, []):
                result = cb(msg)
                if asyncio.iscoroutine(result):
                    await result
        # Block until cancelled (simulates a live WebSocket)
        await asyncio.Event().wait()

    conn.start_listening = start_listening
    return conn


@pytest.mark.asyncio
async def test_on_message_update_emits_interim_result() -> None:
    received: list[ASRResult] = []

    async def on_result(r: ASRResult) -> None:
        received.append(r)

    msg = make_turn_info("hello world", event="Update", confidence=0.8)
    conn = _make_mock_conn([msg])

    m = DeepgramModule(config={"api_key": "sk-test"})

    with patch(
        "asr_mcp.modules.deepgram.AsyncDeepgramClient"
    ) as MockClient:
        mock_client = MockClient.return_value
        mock_client.listen.v2.connect.return_value.__aenter__ = AsyncMock(
            return_value=conn
        )
        mock_client.listen.v2.connect.return_value.__aexit__ = AsyncMock(
            return_value=False
        )

        async def run_and_stop():
            # Wait until the result has been received, then stop
            while not received:
                await asyncio.sleep(0)
            await m.stop()

        await asyncio.gather(
            m.start(asyncio.Queue(), on_result),
            run_and_stop(),
        )

    assert len(received) == 1
    assert received[0].transcript == "hello world"
    assert received[0].is_final is False
    assert received[0].confidence == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_on_message_end_of_turn_emits_final_result() -> None:
    received: list[ASRResult] = []

    async def on_result(r: ASRResult) -> None:
        received.append(r)

    msg = make_turn_info("goodbye", event="EndOfTurn", confidence=0.95)
    conn = _make_mock_conn([msg])

    m = DeepgramModule(config={"api_key": "sk-test"})

    with patch(
        "asr_mcp.modules.deepgram.AsyncDeepgramClient"
    ) as MockClient:
        mock_client = MockClient.return_value
        mock_client.listen.v2.connect.return_value.__aenter__ = AsyncMock(
            return_value=conn
        )
        mock_client.listen.v2.connect.return_value.__aexit__ = AsyncMock(
            return_value=False
        )

        async def run_and_stop():
            while not received:
                await asyncio.sleep(0)
            await m.stop()

        await asyncio.gather(
            m.start(asyncio.Queue(), on_result),
            run_and_stop(),
        )

    assert len(received) == 1
    assert received[0].transcript == "goodbye"
    assert received[0].is_final is True
    assert received[0].confidence == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_on_message_empty_transcript_is_discarded() -> None:
    received: list[ASRResult] = []

    async def on_result(r: ASRResult) -> None:
        received.append(r)

    msg = make_turn_info("", event="Update")
    # Also send a real message so run_and_stop can trigger
    msg2 = make_turn_info("hi", event="Update", confidence=0.5)
    conn = _make_mock_conn([msg, msg2])

    m = DeepgramModule(config={"api_key": "sk-test"})

    with patch(
        "asr_mcp.modules.deepgram.AsyncDeepgramClient"
    ) as MockClient:
        mock_client = MockClient.return_value
        mock_client.listen.v2.connect.return_value.__aenter__ = AsyncMock(
            return_value=conn
        )
        mock_client.listen.v2.connect.return_value.__aexit__ = AsyncMock(
            return_value=False
        )

        async def run_and_stop():
            while not received:
                await asyncio.sleep(0)
            await m.stop()

        await asyncio.gather(
            m.start(asyncio.Queue(), on_result),
            run_and_stop(),
        )

    # Only the non-empty transcript should appear
    assert all(r.transcript != "" for r in received)
    assert len(received) == 1
    assert received[0].transcript == "hi"


@pytest.mark.asyncio
async def test_on_message_non_turn_info_is_ignored() -> None:
    received: list[ASRResult] = []

    async def on_result(r: ASRResult) -> None:
        received.append(r)

    from deepgram.listen.v2.types.listen_v2connected import ListenV2Connected

    connected_msg = ListenV2Connected.construct(
        type="Connected", transaction_key="tk"
    )
    real_msg = make_turn_info("actual", event="Update", confidence=0.7)
    conn = _make_mock_conn([connected_msg, real_msg])

    m = DeepgramModule(config={"api_key": "sk-test"})

    with patch(
        "asr_mcp.modules.deepgram.AsyncDeepgramClient"
    ) as MockClient:
        mock_client = MockClient.return_value
        mock_client.listen.v2.connect.return_value.__aenter__ = AsyncMock(
            return_value=conn
        )
        mock_client.listen.v2.connect.return_value.__aexit__ = AsyncMock(
            return_value=False
        )

        async def run_and_stop():
            while not received:
                await asyncio.sleep(0)
            await m.stop()

        await asyncio.gather(
            m.start(asyncio.Queue(), on_result),
            run_and_stop(),
        )

    assert len(received) == 1
    assert received[0].transcript == "actual"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_deepgram_registered_in_registry() -> None:
    from asr_mcp.modules import REGISTRY

    assert "deepgram" in REGISTRY
    assert REGISTRY["deepgram"] is DeepgramModule
