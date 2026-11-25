"""Unit tests for DeepgramV2Module — Plan 05."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asr_engine.config import ConfigError, MissingSecretError
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


def test_init_without_the_key_in_its_variable_names_the_variable(monkeypatch) -> None:
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    with pytest.raises(MissingSecretError, match="DEEPGRAM_API_KEY") as error:
        DeepgramV2Module(config={})
    assert error.value.variable == "DEEPGRAM_API_KEY"


def test_init_a_key_given_in_code_is_used_before_the_environment() -> None:
    module = DeepgramV2Module(config={}, secrets={"DEEPGRAM_API_KEY": "from-code"})
    assert module._api_key == "from-code"


def test_init_a_key_written_in_the_config_is_refused() -> None:
    with pytest.raises(ConfigError, match="keep the key in an environment variable"):
        DeepgramV2Module(config={"api_key": "sk-test"})


@pytest.mark.asyncio
async def test_connects_with_the_default_settings() -> None:
    assert await _connect_params({}) == {
        "model": "flux-general-en",
        "encoding": "linear16",
        "sample_rate": "16000",
        "eot_threshold": "0.7",
        "eot_timeout_ms": "2000",
    }


@pytest.mark.asyncio
async def test_connects_with_the_configured_settings() -> None:
    params = await _connect_params(
        {"model": "flux-custom", "eot_threshold": 0.5, "eot_timeout_ms": 3000}
    )
    fields = ("model", "eot_threshold", "eot_timeout_ms")
    assert {k: params[k] for k in fields} == {
        "model": "flux-custom",
        "eot_threshold": "0.5",
        "eot_timeout_ms": "3000",
    }


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"model": 3}, "model"),
        ({"eot_threshold": 0.95}, "eot_threshold"),
        ({"eot_threshold": 0.4}, "eot_threshold"),
        ({"eot_threshold": "0.7"}, "eot_threshold"),
        ({"eot_threshold": True}, "eot_threshold"),
        ({"eot_threshold": float("nan")}, "eot_threshold"),
        ({"eot_timeout_ms": "2000"}, "eot_timeout_ms"),
        ({"eot_timeout_ms": 0}, "eot_timeout_ms"),
        ({"eot_timeout_ms": 1500.5}, "eot_timeout_ms"),
    ],
)
def test_init_a_field_of_the_wrong_type_or_range_fails_at_construction(
    fields: dict, named: str
) -> None:
    # Before reaching the SDK, where it would surface as an unrelated TypeError
    # or a rejected connection once the engine has started.
    with pytest.raises(ValueError, match=f"Deepgram Flux module '{named}'"):
        DeepgramV2Module(config={**fields})


# ---------------------------------------------------------------------------
# _drain_queue_for
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_queue_for_removes_items() -> None:
    m = DeepgramV2Module(config={})
    q: AudioQueue = asyncio.Queue()
    for _ in range(5):
        q.put_nowait(b"chunk")
    await m._drain_queue_for(q, duration=0.05)
    assert q.empty()


@pytest.mark.asyncio
async def test_drain_queue_for_returns_after_duration() -> None:
    import time

    m = DeepgramV2Module(config={})
    q: AudioQueue = asyncio.Queue()
    start = time.monotonic()
    await m._drain_queue_for(q, duration=0.15)
    assert time.monotonic() - start >= 0.14


# ---------------------------------------------------------------------------
# _audio_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_loop_sends_chunks() -> None:
    m = DeepgramV2Module(config={})
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
    m = DeepgramV2Module(config={})
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


class _RecordingWebsocket:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_keep_alive_reaches_the_socket_through_the_real_sdk_client() -> None:
    # Pins the private SDK call: an SDK release that removes or changes the v2
    # client's _send() fails here rather than on a live connection.
    import json

    from deepgram.listen.v2.socket_client import AsyncV2SocketClient

    from asr_engine.modules.deepgram.v2 import send_keep_alive

    websocket = _RecordingWebsocket()
    await send_keep_alive(AsyncV2SocketClient(websocket=websocket))  # type: ignore[arg-type]

    assert [json.loads(message) for message in websocket.sent] == [
        {"type": "KeepAlive"}
    ]


@pytest.mark.asyncio
async def test_audio_loop_exits_when_stopped() -> None:
    m = DeepgramV2Module(config={})
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
    m = DeepgramV2Module(config=module_config or {})

    with patch("deepgram.AsyncDeepgramClient") as MockClient:
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

    assert resolve_module_class("deepgram-v2") is DeepgramV2Module


# ---------------------------------------------------------------------------
# VAD gating — the end-of-speech marker (specs/vad.md)
# ---------------------------------------------------------------------------


class _FluxSocket:
    """The websocket under the SDK's real ``AsyncV2SocketClient``: records what
    the module sends (audio as bytes, control messages as parsed JSON) and
    serves the server messages a test pushes, as JSON text frames."""

    def __init__(self) -> None:
        self.sent: list[bytes | dict] = []
        self.listening = asyncio.Event()
        self._incoming: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, data: str | bytes) -> None:
        import json

        self.sent.append(data if isinstance(data, bytes) else json.loads(data))

    def __aiter__(self) -> "_FluxSocket":
        self.listening.set()
        return self

    async def __anext__(self) -> str:
        return await self._incoming.get()

    def push(self, message: dict) -> None:
        import json

        self._incoming.put_nowait(json.dumps(message))


def _flux_conn(socket: _FluxSocket | None = None) -> Any:
    from deepgram.listen.v2.socket_client import AsyncV2SocketClient

    return AsyncV2SocketClient(websocket=socket or _FluxSocket())  # type: ignore[arg-type]


_FORCE_END_TURN = {"type": "ForceEndTurn"}


def _turn_info(transcript: str, event: str) -> dict:
    return {
        "type": "TurnInfo",
        "request_id": "req-1",
        "sequence_id": 1,
        "event": event,
        "turn_index": 0,
        "audio_window_start": 0.0,
        "audio_window_end": 1.0,
        "transcript": transcript,
        "words": [],
        "end_of_turn_confidence": 0.9,
    }


class _GatedRun:
    """A DeepgramV2Module on the SDK's real socket client and a hand-fed queue."""

    def __init__(self, **config: Any) -> None:
        self.socket = _FluxSocket()
        self.module = DeepgramV2Module(config={**config})
        self.queue: AudioQueue = asyncio.Queue()
        self.utterances: list[SpeechUtterance] = []

    async def __aenter__(self) -> "_GatedRun":
        async def on_result(r: SpeechUtterance) -> None:
            self.utterances.append(r)

        self._patch = patch("deepgram.AsyncDeepgramClient")
        mc = self._patch.start().return_value
        mc.listen.v2.connect.return_value.__aenter__ = AsyncMock(
            return_value=_flux_conn(self.socket)
        )
        mc.listen.v2.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        self._task = asyncio.create_task(self.module.start(self.queue, on_result))
        await asyncio.wait_for(self.socket.listening.wait(), 2.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.module.stop()
        await asyncio.wait_for(self._task, 2.0)
        self._patch.stop()

    async def _settle(self) -> None:
        for _ in range(20):
            await asyncio.sleep(0)

    async def feed(self, *items: Any) -> None:
        for item in items:
            self.queue.put_nowait(item)
        await self._settle()

    async def server(self, *messages: dict) -> None:
        for message in messages:
            self.socket.push(message)
        await self._settle()


@pytest.mark.asyncio
async def test_end_of_speech_with_an_open_turn_forces_the_end_of_the_turn():
    from asr_engine.audio import END_OF_SPEECH

    assert DeepgramV2Module.VAD_GATING == "supported"
    async with _GatedRun() as run:
        await run.feed(b"speech")
        await run.server(_turn_info("the sky", "Update"))
        await run.feed(END_OF_SPEECH)

        # ForceEndTurn, and no silence: the audio sent is the audio fed.
        assert run.socket.sent == [b"speech", _FORCE_END_TURN]

        # Flux ends the turn; a stray second marker (no audio in between)
        # sends nothing more.
        await run.server(_turn_info("the sky is blue", "EndOfTurn"))
        await run.feed(END_OF_SPEECH)
        assert run.socket.sent == [b"speech", _FORCE_END_TURN]
        assert [(u.transcript, u.is_final) for u in run.utterances] == [
            ("the sky", False),
            ("the sky is blue", True),
        ]


@pytest.mark.asyncio
async def test_end_of_speech_after_flux_ended_the_turn_sends_nothing():
    """The recommended longer hangover: Flux hears the silence the gate lets
    through and ends the turn itself, so the marker sends nothing."""
    from asr_engine.audio import END_OF_SPEECH

    async with _GatedRun() as run:
        await run.feed(b"speech")
        await run.server(_turn_info("the sky", "Update"))
        await run.feed(b"hangover silence")
        await run.server(_turn_info("the sky", "EndOfTurn"))
        await run.feed(END_OF_SPEECH)

        assert run.socket.sent == [b"speech", b"hangover silence"]


@pytest.mark.asyncio
async def test_a_turn_reported_after_the_marker_is_ended_when_it_appears():
    """The gate can close before Flux's first transcript for the utterance
    arrives, and Flux ignores ForceEndTurn with no turn open: it goes out as
    soon as the turn is known to be open."""
    from asr_engine.audio import END_OF_SPEECH

    async with _GatedRun() as run:
        await run.feed(b"speech", END_OF_SPEECH)
        assert run.socket.sent == [b"speech"]  # no turn known yet

        await run.server(_turn_info("the", "StartOfTurn"))
        assert run.socket.sent == [b"speech", _FORCE_END_TURN]
        await run.server(_turn_info("the sky", "Update"))
        assert run.socket.sent == [b"speech", _FORCE_END_TURN]  # once per gap


@pytest.mark.asyncio
async def test_each_gated_utterance_left_open_gets_its_own_force_end_turn():
    from asr_engine.audio import END_OF_SPEECH

    async with _GatedRun() as run:
        for chunk in (b"first", b"second"):
            await run.feed(chunk)
            await run.server(_turn_info("words", "Update"))
            await run.feed(END_OF_SPEECH)
            await run.server(_turn_info("words", "EndOfTurn"))

        assert run.socket.sent == [
            b"first",
            _FORCE_END_TURN,
            b"second",
            _FORCE_END_TURN,
        ]
        assert [u.is_final for u in run.utterances] == [False, True, False, True]


@pytest.mark.asyncio
async def test_a_force_end_turn_that_crosses_flux_end_of_turn_is_harmless():
    """The open-turn flag can lag the server: Flux may already be ending the
    turn when ForceEndTurn goes out. It answers with a warning, and the
    connection carries on with the next utterance."""
    from asr_engine.audio import END_OF_SPEECH

    async with _GatedRun() as run:
        await run.feed(b"first")
        await run.server(_turn_info("hello", "Update"))
        await run.feed(END_OF_SPEECH)
        await run.server(
            _turn_info("hello", "EndOfTurn"),
            {
                "type": "Warning",
                "request_id": "req-1",
                "sequence_id": 2,
                "code": "FORCE_END_TURN_NO_ACTIVE_TURN",
                "description": "Received ForceEndTurn while no turn was active; "
                "the request was ignored.",
            },
        )
        await run.feed(b"second")
        await run.server(_turn_info("again", "EndOfTurn"))

        assert run.socket.sent == [b"first", _FORCE_END_TURN, b"second"]
        assert [(u.transcript, u.is_final) for u in run.utterances] == [
            ("hello", False),
            ("hello", True),
            ("again", True),
        ]


@pytest.mark.asyncio
async def test_end_of_speech_while_reconnecting_is_discarded() -> None:
    from asr_engine.audio import END_OF_SPEECH

    m = DeepgramV2Module(config={})
    q: AudioQueue = asyncio.Queue()
    q.put_nowait(b"chunk")
    q.put_nowait(END_OF_SPEECH)

    await m._drain_queue_for(q, 0.05)
    assert q.empty()
    await m.finalize()  # no connection: nothing to flush, and no error


async def _connect_params(config: dict) -> dict:
    """The parameters the module opens its Deepgram connection with."""
    m = DeepgramV2Module(config={**config})
    with patch("deepgram.AsyncDeepgramClient") as MockClient:
        connect = MockClient.return_value.listen.v2.connect
        connect.return_value.__aenter__ = AsyncMock(return_value=_flux_conn())
        connect.return_value.__aexit__ = AsyncMock(return_value=False)

        async def on_result(r: SpeechUtterance) -> None:
            pass

        task = asyncio.create_task(m.start(asyncio.Queue(), on_result))
        while not connect.called:
            await asyncio.sleep(0)
        await m.stop()
        await task
    return connect.call_args.kwargs
