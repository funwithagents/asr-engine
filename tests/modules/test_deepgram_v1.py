"""Unit tests for DeepgramV1Module — Plan 05."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_mcp.modules.base import SpeechUtterance
from asr_mcp.modules.deepgram_v1 import DeepgramV1Module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_v1_results(
    transcript: str,
    is_final: bool = True,
    confidence: float = 0.9,
) -> Any:
    """Build a minimal ListenV1Results-like object via .construct().

    Nested objects are passed as plain dicts so that UncheckedBaseModel.construct()
    can call model_construct(**dict) on them correctly (passing model instances fails
    because **instance is not a mapping).
    """
    from deepgram.listen.v1.types.listen_v1results import ListenV1Results

    return ListenV1Results.construct(
        type="Results",
        channel_index=[0],
        duration=1.0,
        start=0.0,
        is_final=is_final,
        speech_final=None,
        channel={
            "alternatives": [
                {"transcript": transcript, "confidence": confidence, "words": []}
            ]
        },
        metadata={
            "request_id": "req-1",
            "model_uuid": "uuid-1",
            "model_info": {"name": "nova-3", "version": "1.0", "arch": "general"},
        },
        from_finalize=None,
        entities=None,
    )


def _make_mock_conn(messages: list[Any]) -> MagicMock:
    """Mock AsyncV1SocketClient: emit messages then block until cancelled."""
    from deepgram.core.events import EventType

    conn = MagicMock()
    conn.send_media = AsyncMock()
    conn.send_keep_alive = AsyncMock()
    conn.send_close_stream = AsyncMock()
    callbacks: dict = {}

    def register(event, cb):
        callbacks.setdefault(event, []).append(cb)

    conn.on.side_effect = register

    async def start_listening():
        for msg in messages:
            for cb in callbacks.get(EventType.MESSAGE, []):
                result = cb(msg)
                if asyncio.iscoroutine(result):
                    await result
        await asyncio.Event().wait()

    conn.start_listening = start_listening
    return conn


# ---------------------------------------------------------------------------
# __init__ — config validation
# ---------------------------------------------------------------------------


def test_init_missing_api_key_raises() -> None:
    with pytest.raises(ValueError, match="api_key"):
        DeepgramV1Module(config={})


def test_init_empty_api_key_raises() -> None:
    with pytest.raises(ValueError, match="api_key"):
        DeepgramV1Module(config={"api_key": ""})


def test_init_defaults() -> None:
    m = DeepgramV1Module(config={"api_key": "sk-test"})
    assert m._model == "nova-3"
    assert m._language == "multi"
    assert m._punctuate is True
    assert m._interim_results is True


def test_init_custom_values() -> None:
    m = DeepgramV1Module(
        config={
            "api_key": "sk-test",
            "model": "nova-2",
            "language": "fr",
            "punctuate": False,
            "interim_results": False,
        }
    )
    assert m._model == "nova-2"
    assert m._language == "fr"
    assert m._punctuate is False
    assert m._interim_results is False


# ---------------------------------------------------------------------------
# _drain_queue_for
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_queue_for_removes_items() -> None:
    m = DeepgramV1Module(config={"api_key": "sk-test"})
    q: asyncio.Queue[bytes] = asyncio.Queue()
    for _ in range(5):
        q.put_nowait(b"chunk")
    await m._drain_queue_for(q, duration=0.05)
    assert q.empty()


@pytest.mark.asyncio
async def test_drain_queue_for_returns_after_duration() -> None:
    import time

    m = DeepgramV1Module(config={"api_key": "sk-test"})
    q: asyncio.Queue[bytes] = asyncio.Queue()
    start = time.monotonic()
    await m._drain_queue_for(q, duration=0.15)
    assert time.monotonic() - start >= 0.14


# ---------------------------------------------------------------------------
# _audio_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_loop_sends_chunks() -> None:
    m = DeepgramV1Module(config={"api_key": "sk-test"})
    conn = MagicMock()
    conn.send_media = AsyncMock()
    conn.send_keep_alive = AsyncMock()

    q: asyncio.Queue[bytes] = asyncio.Queue()
    q.put_nowait(b"frame1")
    q.put_nowait(b"frame2")

    async def stopper():
        while conn.send_media.call_count < 2:
            await asyncio.sleep(0)
        await m.stop()

    await asyncio.gather(m._audio_loop(conn, q), stopper())

    assert conn.send_media.call_count == 2
    conn.send_media.assert_any_call(b"frame1")
    conn.send_media.assert_any_call(b"frame2")


@pytest.mark.asyncio
async def test_audio_loop_sends_keepalive_on_timeout() -> None:
    m = DeepgramV1Module(config={"api_key": "sk-test"})
    m._KEEPALIVE_TIMEOUT = 0.05
    conn = MagicMock()
    conn.send_media = AsyncMock()

    keepalive_sent = asyncio.Event()

    async def _track_keepalive():
        keepalive_sent.set()

    conn.send_keep_alive = AsyncMock(side_effect=_track_keepalive)

    q: asyncio.Queue[bytes] = asyncio.Queue()

    async def stopper():
        await asyncio.wait_for(keepalive_sent.wait(), timeout=5.0)
        await m.stop()

    await asyncio.gather(m._audio_loop(conn, q), stopper())
    conn.send_keep_alive.assert_called()


@pytest.mark.asyncio
async def test_audio_loop_exits_when_stopped() -> None:
    m = DeepgramV1Module(config={"api_key": "sk-test"})
    await m.stop()

    conn = MagicMock()
    conn.send_media = AsyncMock()
    conn.send_keep_alive = AsyncMock()

    await m._audio_loop(conn, asyncio.Queue())

    conn.send_media.assert_not_called()
    conn.send_keep_alive.assert_not_called()


# ---------------------------------------------------------------------------
# on_message dispatch
# ---------------------------------------------------------------------------


async def _run_with_messages(messages, module_config=None):
    received: list[SpeechUtterance] = []

    async def on_result(r: SpeechUtterance) -> None:
        received.append(r)

    conn = _make_mock_conn(messages)
    m = DeepgramV1Module(config=module_config or {"api_key": "sk-test"})

    with patch("asr_mcp.modules.deepgram_v1.AsyncDeepgramClient") as MockClient:
        mc = MockClient.return_value
        mc.listen.v1.connect.return_value.__aenter__ = AsyncMock(return_value=conn)
        mc.listen.v1.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        async def run_and_stop():
            while not received:
                await asyncio.sleep(0)
            await m.stop()

        await asyncio.gather(m.start(asyncio.Queue(), on_result), run_and_stop())

    return received


@pytest.mark.asyncio
async def test_on_message_interim_emits_non_final_result() -> None:
    received = await _run_with_messages(
        [make_v1_results("hello", is_final=False, confidence=0.8)]
    )
    assert len(received) == 1
    assert received[0].transcript == "hello"
    assert received[0].is_final is False
    assert received[0].confidence == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_on_message_is_final_emits_final_result() -> None:
    """is_final=True → SpeechUtterance.is_final=True (segment committed by Deepgram)."""
    received = await _run_with_messages(
        [make_v1_results("goodbye", is_final=True, confidence=0.95)]
    )
    assert len(received) == 1
    assert received[0].transcript == "goodbye"
    assert received[0].is_final is True
    assert received[0].confidence == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_on_message_empty_transcript_is_discarded() -> None:
    received = await _run_with_messages(
        [
            make_v1_results("", is_final=False),
            make_v1_results("hi", is_final=False, confidence=0.5),
        ]
    )
    assert len(received) == 1
    assert received[0].transcript == "hi"


@pytest.mark.asyncio
async def test_on_message_non_results_is_ignored() -> None:
    from deepgram.listen.v1.types.listen_v1metadata import ListenV1Metadata

    metadata_msg = ListenV1Metadata.construct(
        type="Metadata",
        transaction_key="tk",
        request_id="req-1",
        sha256="abc",
        created="2024-01-01",
        duration=0.0,
        channels=1,
    )
    received = await _run_with_messages(
        [metadata_msg, make_v1_results("actual", is_final=False, confidence=0.7)]
    )
    assert len(received) == 1
    assert received[0].transcript == "actual"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_deepgram_v1_registered_in_registry() -> None:
    from asr_mcp.modules import REGISTRY

    assert "deepgram_v1" in REGISTRY
    assert REGISTRY["deepgram_v1"] is DeepgramV1Module
