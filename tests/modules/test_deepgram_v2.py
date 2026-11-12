"""Unit tests for DeepgramV2Module — Plan 05."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_engine.modules.base import AudioQueue, SpeechUtterance
from asr_engine.modules.deepgram.v2 import DeepgramV2Module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_turn_info(
    transcript: str,
    event: str = "Update",
    confidence: float = 0.9,
) -> Any:
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


def _make_mock_conn(messages: list[Any]) -> MagicMock:
    """Mock AsyncV2SocketClient: emit messages then block until cancelled."""
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
        DeepgramV2Module(config={})


def test_init_empty_api_key_raises() -> None:
    with pytest.raises(ValueError, match="api_key"):
        DeepgramV2Module(config={"api_key": ""})


def test_init_defaults() -> None:
    m = DeepgramV2Module(config={"api_key": "sk-test"})
    assert m._model == "flux-general-en"
    assert m._eot_threshold == 0.7
    assert m._eot_timeout_ms == 2000


def test_init_custom_values() -> None:
    m = DeepgramV2Module(
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
# _drain_queue_for
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_queue_for_removes_items() -> None:
    m = DeepgramV2Module(config={"api_key": "sk-test"})
    q: AudioQueue = asyncio.Queue()
    for _ in range(5):
        q.put_nowait(b"chunk")
    await m._drain_queue_for(q, duration=0.05)
    assert q.empty()


@pytest.mark.asyncio
async def test_drain_queue_for_returns_after_duration() -> None:
    import time

    m = DeepgramV2Module(config={"api_key": "sk-test"})
    q: AudioQueue = asyncio.Queue()
    start = time.monotonic()
    await m._drain_queue_for(q, duration=0.15)
    assert time.monotonic() - start >= 0.14


# ---------------------------------------------------------------------------
# _audio_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_loop_sends_chunks() -> None:
    m = DeepgramV2Module(config={"api_key": "sk-test"})
    conn = MagicMock()
    conn.send_media = AsyncMock()
    conn._send = AsyncMock()

    q: AudioQueue = asyncio.Queue()
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
    m = DeepgramV2Module(config={"api_key": "sk-test"})
    m._KEEPALIVE_TIMEOUT = 0.05
    conn = MagicMock()
    conn.send_media = AsyncMock()

    keepalive_sent = asyncio.Event()

    async def _track_send(data):
        if data == {"type": "KeepAlive"}:
            keepalive_sent.set()

    conn._send = AsyncMock(side_effect=_track_send)

    q: AudioQueue = asyncio.Queue()

    async def stopper():
        await asyncio.wait_for(keepalive_sent.wait(), timeout=5.0)
        await m.stop()

    await asyncio.gather(m._audio_loop(conn, q), stopper())
    conn._send.assert_called_with({"type": "KeepAlive"})


@pytest.mark.asyncio
async def test_audio_loop_exits_when_stopped() -> None:
    m = DeepgramV2Module(config={"api_key": "sk-test"})
    await m.stop()

    conn = MagicMock()
    conn.send_media = AsyncMock()
    conn._send = AsyncMock()

    await m._audio_loop(conn, asyncio.Queue())

    conn.send_media.assert_not_called()
    conn._send.assert_not_called()


# ---------------------------------------------------------------------------
# on_message dispatch
# ---------------------------------------------------------------------------


async def _run_with_messages(messages, module_config=None):
    received: list[SpeechUtterance] = []

    async def on_result(r: SpeechUtterance) -> None:
        received.append(r)

    conn = _make_mock_conn(messages)
    m = DeepgramV2Module(config=module_config or {"api_key": "sk-test"})

    with patch("asr_engine.modules.deepgram.v2.AsyncDeepgramClient") as MockClient:
        mc = MockClient.return_value
        mc.listen.v2.connect.return_value.__aenter__ = AsyncMock(return_value=conn)
        mc.listen.v2.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        async def run_and_stop():
            while not received:
                await asyncio.sleep(0)
            await m.stop()

        await asyncio.gather(m.start(asyncio.Queue(), on_result), run_and_stop())

    return received


@pytest.mark.asyncio
async def test_on_message_update_emits_interim_result() -> None:
    received = await _run_with_messages(
        [make_turn_info("hello world", event="Update", confidence=0.8)]
    )
    assert len(received) == 1
    assert received[0].transcript == "hello world"
    assert received[0].is_final is False
    assert received[0].confidence == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_on_message_end_of_turn_emits_final_result() -> None:
    received = await _run_with_messages(
        [make_turn_info("goodbye", event="EndOfTurn", confidence=0.95)]
    )
    assert len(received) == 1
    assert received[0].transcript == "goodbye"
    assert received[0].is_final is True
    assert received[0].confidence == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_on_message_empty_transcript_is_discarded() -> None:
    received = await _run_with_messages(
        [
            make_turn_info("", event="Update"),
            make_turn_info("hi", event="Update", confidence=0.5),
        ]
    )
    assert len(received) == 1
    assert received[0].transcript == "hi"


@pytest.mark.asyncio
async def test_on_message_non_turn_info_is_ignored() -> None:
    from deepgram.listen.v2.types.listen_v2connected import ListenV2Connected

    connected = ListenV2Connected.construct(type="Connected", transaction_key="tk")
    received = await _run_with_messages(
        [connected, make_turn_info("actual", event="Update", confidence=0.7)]
    )
    assert len(received) == 1
    assert received[0].transcript == "actual"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_deepgram_v2_registered_in_registry() -> None:
    from asr_engine.modules import resolve_module_class

    assert resolve_module_class("deepgram_v2") is DeepgramV2Module


# ---------------------------------------------------------------------------
# VAD gating — the end-of-speech marker (specs/vad.md)
# ---------------------------------------------------------------------------


class _LiveConn:
    """A connection the test drives: records sent media, delivers server events."""

    def __init__(self) -> None:
        self.media: list[bytes] = []
        self._callbacks: list[Any] = []
        self.listening = asyncio.Event()

    def on(self, event: Any, callback: Any) -> None:
        from deepgram.core.events import EventType

        if event == EventType.MESSAGE:
            self._callbacks.append(callback)

    async def start_listening(self) -> None:
        self.listening.set()
        await asyncio.Event().wait()

    async def send_media(self, data: bytes) -> None:
        self.media.append(data)

    async def _send(self, message: dict) -> None:
        pass

    async def deliver(self, msg: Any) -> None:
        for callback in self._callbacks:
            await callback(msg)


class _GatedRun:
    """A DeepgramV2Module running on a ``_LiveConn`` and a hand-fed queue."""

    def __init__(self, audio_format: Any = None, **config: Any) -> None:
        from asr_engine.audio import AudioFormat

        self.audio_format = audio_format or AudioFormat(sample_rate=16000)
        self.conn = _LiveConn()
        self.module = DeepgramV2Module(config={"api_key": "sk-test", **config})
        self.queue: AudioQueue = asyncio.Queue()
        self.utterances: list[SpeechUtterance] = []

    async def __aenter__(self) -> "_GatedRun":
        async def on_result(r: SpeechUtterance) -> None:
            self.utterances.append(r)

        self._patch = patch("asr_engine.modules.deepgram.v2.AsyncDeepgramClient")
        mc = self._patch.start().return_value
        mc.listen.v2.connect.return_value.__aenter__ = AsyncMock(return_value=self.conn)
        mc.listen.v2.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        self._task = asyncio.create_task(
            self.module.start(self.queue, on_result, audio_format=self.audio_format)
        )
        await asyncio.wait_for(self.conn.listening.wait(), 2.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.module.stop()
        await asyncio.wait_for(self._task, 2.0)
        self._patch.stop()

    async def feed(self, *items: Any) -> None:
        for item in items:
            self.queue.put_nowait(item)
        for _ in range(20):
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_end_of_speech_with_an_open_turn_sends_silence_past_the_eot_timeout():
    from asr_engine.audio import END_OF_SPEECH

    assert DeepgramV2Module.VAD_GATING == "supported"
    async with _GatedRun(eot_timeout_ms=1000) as run:
        await run.feed(b"speech")
        await run.conn.deliver(make_turn_info("the sky", event="Update"))
        await run.feed(END_OF_SPEECH)

        # One burst: eot_timeout_ms (1.0 s) + the 0.5 s margin of 16 kHz linear16.
        assert run.conn.media == [b"speech", b"\x00" * int(1.5 * 16000) * 2]

        # Flux then ends the turn on the silence; a stray second marker (no audio
        # in between) sends nothing more.
        await run.conn.deliver(make_turn_info("the sky", event="EndOfTurn"))
        await run.feed(END_OF_SPEECH)
        assert len(run.conn.media) == 2
        assert [(u.transcript, u.is_final) for u in run.utterances] == [
            ("the sky", False),
            ("the sky", True),
        ]


@pytest.mark.asyncio
async def test_end_of_speech_after_flux_ended_the_turn_sends_nothing():
    """The recommended longer hangover: Flux hears the silence the gate lets
    through and ends the turn itself, so the marker costs nothing."""
    from asr_engine.audio import END_OF_SPEECH

    async with _GatedRun() as run:
        await run.feed(b"speech")
        await run.conn.deliver(make_turn_info("the sky", event="Update"))
        await run.feed(b"hangover silence")
        await run.conn.deliver(make_turn_info("the sky", event="EndOfTurn"))
        await run.feed(END_OF_SPEECH)

        assert run.conn.media == [b"speech", b"hangover silence"]


@pytest.mark.asyncio
async def test_a_turn_reported_after_the_marker_is_flushed_when_it_appears():
    """The gate can close before Flux's first transcript for the utterance
    arrives: the silence goes out as soon as the turn is known to be open."""
    from asr_engine.audio import END_OF_SPEECH

    async with _GatedRun() as run:
        await run.feed(b"speech", END_OF_SPEECH)
        assert run.conn.media == [b"speech"]  # no turn known yet

        await run.conn.deliver(make_turn_info("the", event="StartOfTurn"))
        assert len(run.conn.media) == 2 and set(run.conn.media[1]) == {0}
        await run.conn.deliver(make_turn_info("the sky", event="Update"))
        assert len(run.conn.media) == 2  # once per gap


@pytest.mark.asyncio
async def test_each_gated_utterance_left_open_gets_its_own_flush_in_mulaw():
    from asr_engine.audio import END_OF_SPEECH, AudioFormat

    fmt = AudioFormat(sample_rate=8000, encoding="mulaw")
    async with _GatedRun(fmt, eot_timeout_ms=500) as run:
        silence = b"\xff" * 8000  # 1.0 s of μ-law silence, 1 byte per sample
        for chunk in (b"first", b"second"):
            await run.feed(chunk)
            await run.conn.deliver(make_turn_info("words", event="Update"))
            await run.feed(END_OF_SPEECH)
            await run.conn.deliver(make_turn_info("words", event="EndOfTurn"))

        assert run.conn.media == [b"first", silence, b"second", silence]


@pytest.mark.asyncio
async def test_end_of_speech_while_reconnecting_is_discarded() -> None:
    from asr_engine.audio import END_OF_SPEECH

    m = DeepgramV2Module(config={"api_key": "sk-test"})
    q: AudioQueue = asyncio.Queue()
    q.put_nowait(b"chunk")
    q.put_nowait(END_OF_SPEECH)

    await m._drain_queue_for(q, 0.05)
    assert q.empty()
    await m.finalize()  # no connection: nothing to flush, and no error
