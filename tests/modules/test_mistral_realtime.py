"""Functional tests for the mistral_realtime module (specs/modules/mistral.md).

The module is driven through its ``connect`` seam with a scripted fake connection:
it records every client message and serves the server events each test pushes.
No network; the timers run on small configured values.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest

from asr_engine.audio import AudioFormat
from asr_engine.modules import resolve_module_class
from asr_engine.modules.base import SpeechUtterance
from asr_engine.modules.mistral.realtime import MistralRealtimeModule

# --------------------------------------------------------------------------- fakes


def _delta(text: str) -> dict[str, Any]:
    return {"type": "transcription.text.delta", "text": text}


TURN_END = {"type": "turn.end", "time_s": 1.0, "end_reason": "threshold"}


class FakeConnection:
    """One scripted server session.

    Starts with ``session.created`` queued (unless ``created=False``). When the
    client sends ``input_audio.end`` it answers with ``on_end`` events then
    ``transcription.done`` (unless ``answer_end=False``).
    """

    def __init__(
        self,
        *,
        created: bool = True,
        on_end: list[dict[str, Any]] | None = None,
        answer_end: bool = True,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._incoming: asyncio.Queue[dict[str, Any] | BaseException] = asyncio.Queue()
        self._on_end = on_end or []
        self._answer_end = answer_end
        if created:
            self.push({"type": "session.created", "session": {}})

    def push(self, *events: dict[str, Any]) -> None:
        for event in events:
            self._incoming.put_nowait(event)

    def drop(self) -> None:
        """Simulate the socket breaking."""
        self._incoming.put_nowait(ConnectionError("connection dropped"))

    async def send(self, message: str) -> None:
        if self.closed:
            raise ConnectionError("send on a closed connection")
        msg = json.loads(message)
        self.sent.append(msg)
        if msg["type"] == "input_audio.end" and self._answer_end:
            self.push(*self._on_end, {"type": "transcription.done", "text": ""})

    async def recv(self) -> str:
        item = await self._incoming.get()
        if isinstance(item, BaseException):
            raise item
        return json.dumps(item)

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]

    def audio(self) -> list[bytes]:
        return [
            base64.b64decode(m["audio"])
            for m in self.sent
            if m["type"] == "input_audio.append"
        ]


class _FailingConnect:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> FakeConnection:
        raise self._exc

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeConnector:
    """Hands out the scripted connections (or connect failures) in order, then
    fresh default connections."""

    def __init__(self, *script: FakeConnection | BaseException) -> None:
        self._script = list(script)
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.connections: list[FakeConnection] = []

    def __call__(self, url: str, headers: dict[str, str]) -> Any:
        self.calls.append((url, headers))
        item = self._script.pop(0) if self._script else FakeConnection()
        if isinstance(item, BaseException):
            return _FailingConnect(item)
        self.connections.append(item)
        return item


async def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0.002)


class Harness:
    """A module wired to a fake connector, an audio queue and recording callbacks."""

    def __init__(
        self, config: dict[str, Any], *script: FakeConnection | BaseException
    ) -> None:
        self.connector = FakeConnector(*script)
        self.module = MistralRealtimeModule(
            {"api_key": "test-key", **config}, connect=self.connector
        )
        self.module._BACKOFF_S = (0.01,)
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.utterances: list[SpeechUtterance] = []
        self.connected: list[bool] = []
        self.task: asyncio.Task | None = None

    async def start(
        self, audio_format: AudioFormat = AudioFormat(), *, wait_connected: bool = True
    ) -> None:
        async def on_utterance(u: SpeechUtterance) -> None:
            self.utterances.append(u)

        self.task = asyncio.create_task(
            self.module.start(
                self.queue,
                on_utterance,
                self.connected.append,
                audio_format=audio_format,
            )
        )
        if wait_connected:
            await _until(lambda: self.connected[-1:] == [True])

    async def stop(self) -> None:
        await self.module.stop()
        assert self.task is not None
        await asyncio.wait_for(self.task, 1.0)

    @property
    def conn(self) -> FakeConnection:
        return self.connector.connections[-1]

    def finals(self) -> list[str]:
        return [u.transcript for u in self.utterances if u.is_final]

    def interims(self) -> list[str]:
        return [u.transcript for u in self.utterances if not u.is_final]


@pytest.fixture
async def harnesses():
    """Build harnesses with ``make(config, *script)``; tasks are cleaned up."""
    made: list[Harness] = []

    def make(config: dict[str, Any] | None = None, *script: Any) -> Harness:
        h = Harness(config or {}, *script)
        made.append(h)
        return h

    yield make
    for h in made:
        if h.task is not None and not h.task.done():
            h.task.cancel()
            try:
                await h.task
            except (asyncio.CancelledError, Exception):
                pass


# ----------------------------------------------------------------------- handshake


async def test_session_update_is_first_and_carries_defaults(harnesses) -> None:
    h = harnesses()
    await h.start()
    await _until(lambda: len(h.conn.sent) >= 1)

    assert h.conn.sent[0] == {
        "type": "session.update",
        "session": {
            "audio_format": {"encoding": "pcm_s16le", "sample_rate": 16000},
            "target_streaming_delay_ms": 480,
            "turn_detection": {},
        },
    }
    url, headers = h.connector.calls[0]
    assert url.startswith("wss://api.mistral.ai/v1/audio/transcriptions/realtime?")
    assert parse_qs(urlparse(url).query) == {
        "model": ["voxtral-mini-transcribe-realtime-3"]
    }
    assert headers == {"Authorization": "Bearer test-key"}


async def test_session_update_carries_configured_fields(harnesses) -> None:
    h = harnesses(
        {
            "model": "voxtral-mini-transcribe-realtime-3-0",
            "url": "ws://localhost:9000/realtime",
            "language": "fr",
            "context_bias": ["valider", "Mistral"],
            "streaming_delay_ms": 2400,
            "turn_detection": {"min_silence_ms": 300, "end_timeout_ms": 2500},
        }
    )
    await h.start(AudioFormat(sample_rate=24000))
    await _until(lambda: len(h.conn.sent) >= 1)

    assert h.conn.sent[0]["session"] == {
        "audio_format": {"encoding": "pcm_s16le", "sample_rate": 24000},
        "target_streaming_delay_ms": 2400,
        "language": "fr",
        "context_bias": ["valider", "Mistral"],
        "turn_detection": {"min_silence_ms": 300, "end_timeout_ms": 2500},
    }
    url, _ = h.connector.calls[0]
    assert (
        url == "ws://localhost:9000/realtime?model=voxtral-mini-transcribe-realtime-3-0"
    )


async def test_turn_detection_false_is_omitted(harnesses) -> None:
    h = harnesses({"turn_detection": False})
    await h.start()
    await _until(lambda: len(h.conn.sent) >= 1)

    assert "turn_detection" not in h.conn.sent[0]["session"]


async def test_audio_chunks_follow_the_session_update_as_base64(harnesses) -> None:
    h = harnesses()
    await h.start()
    chunks = [b"\x01\x02\x03\x04", b"\xff\xfe", b"\x00" * 3200]
    for chunk in chunks:
        h.queue.put_nowait(chunk)
    await _until(lambda: len(h.conn.audio()) == 3)

    assert h.conn.types() == ["session.update"] + ["input_audio.append"] * 3
    assert h.conn.audio() == chunks


# ---------------------------------------------------------------------- utterances


async def test_deltas_build_cumulative_interims(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.conn.push(_delta("The"), _delta(" sky"), _delta(" is"), _delta(","))
    await _until(lambda: len(h.utterances) == 4)

    assert h.interims() == ["The", "The sky", "The sky is", "The sky is,"]
    assert all(u.confidence is None for u in h.utterances)
    assert h.finals() == []


async def test_turn_end_closes_the_utterance_after_the_margin(harnesses) -> None:
    h = harnesses({"final_margin_ms": 50})
    await h.start()
    # The turn's last punctuation lands right after turn.end: inside the margin.
    h.conn.push(_delta("The"), _delta(" sky"), TURN_END, _delta("."))
    await _until(lambda: h.finals() != [])
    assert h.finals() == ["The sky."]

    # Text after the final is new speech: it opens a new utterance.
    h.conn.push(_delta(" Validate"))
    await _until(lambda: h.interims()[-1:] == ["Validate"])
    assert h.finals() == ["The sky."]


async def test_a_second_turn_end_does_not_postpone_the_final(harnesses) -> None:
    h = harnesses({"final_margin_ms": 200})
    await h.start()
    loop = asyncio.get_running_loop()
    first_turn_end = loop.time()
    h.conn.push(_delta("Hello"), TURN_END)
    await asyncio.sleep(0.15)
    h.conn.push(TURN_END)  # re-arming would push the final out to ~350 ms
    await _until(lambda: h.finals() != [])

    # Due at ~200 ms; 300 ms leaves slack for a slow loop yet excludes a re-arm.
    assert loop.time() - first_turn_end < 0.3
    assert h.finals() == ["Hello"]


async def test_turn_end_with_nothing_accumulated_emits_nothing(harnesses) -> None:
    h = harnesses({"final_margin_ms": 10})
    await h.start()
    h.conn.push(TURN_END, _delta(""))
    await asyncio.sleep(0.08)

    assert h.utterances == []


async def test_silence_timer_finalizes_without_turn_detection(harnesses) -> None:
    h = harnesses({"turn_detection": False, "finalize_after_silence_s": 0.05})
    await h.start()
    h.conn.push(_delta("Hello"), _delta(" there."))
    await _until(lambda: h.finals() != [])

    assert h.finals() == ["Hello there."]


async def test_text_still_arriving_holds_the_silence_timer(harnesses) -> None:
    h = harnesses({"turn_detection": False, "finalize_after_silence_s": 0.15})
    await h.start()
    words = ["one", " two", " three", " four", " five", " six"]
    for word in words:  # 0.3 s of steady text, each gap well under 0.15 s
        h.conn.push(_delta(word))
        await asyncio.sleep(0.05)
    assert h.finals() == []

    await _until(lambda: h.finals() != [])
    assert h.finals() == ["one two three four five six"]


async def test_silence_timer_closes_text_the_server_opened_no_turn_for(
    harnesses,
) -> None:
    # Seen live at 1200 ms delay: new speech after turn.end with no turn events.
    h = harnesses({"final_margin_ms": 10, "finalize_after_silence_s": 0.1})
    await h.start()
    h.conn.push(_delta("The sky is blue."), TURN_END)
    await _until(lambda: h.finals() == ["The sky is blue."])
    h.conn.push(_delta(" Validate."))
    await _until(lambda: len(h.finals()) == 2)

    assert h.finals() == ["The sky is blue.", "Validate."]


# ----------------------------------------------------------------------- lifecycle


async def test_stop_flushes_trailing_text_and_disconnects(harnesses) -> None:
    conn = FakeConnection(on_end=[_delta(" blue.")])
    h = harnesses({}, conn)
    await h.start()
    h.conn.push(_delta("The sky is"))
    await _until(lambda: h.interims() == ["The sky is"])

    await h.stop()

    assert conn.types()[-1] == "input_audio.end"
    assert h.finals() == ["The sky is blue."]
    assert h.connected == [True, False]
    assert conn.closed


async def test_stop_returns_when_transcription_done_never_comes(harnesses) -> None:
    h = harnesses({"stop_timeout_s": 0.1}, FakeConnection(answer_end=False))
    await h.start()
    h.conn.push(_delta("Hi"))
    await _until(lambda: h.interims() == ["Hi"])

    loop = asyncio.get_running_loop()
    began = loop.time()
    await h.stop()

    assert loop.time() - began < 1.0
    assert h.finals() == ["Hi"]
    assert h.connected == [True, False]


async def test_stop_while_reconnecting_returns_at_once(harnesses) -> None:
    h = harnesses({}, ConnectionRefusedError("down"))
    h.module._BACKOFF_S = (30.0,)
    await h.start(wait_connected=False)
    await _until(lambda: len(h.connector.calls) == 1)

    await asyncio.wait_for(h.stop(), 1.0)

    assert h.connected == []


async def test_idle_parks_the_session_and_reopens_on_the_next_chunk(harnesses) -> None:
    first, second = FakeConnection(), FakeConnection()
    h = harnesses({"idle_close_s": 0.1}, first, second)
    await h.start()
    h.queue.put_nowait(b"\x01\x01")
    first.push(_delta("Hi"))
    await _until(lambda: first.closed)

    assert first.types() == ["session.update", "input_audio.append", "input_audio.end"]
    assert h.finals() == ["Hi"]
    assert h.connected == [True]  # parked, not disconnected
    assert len(h.connector.calls) == 1  # nothing reopens without audio

    h.queue.put_nowait(b"\x02\x02")
    await _until(lambda: second.audio() == [b"\x02\x02"])

    assert second.types() == ["session.update", "input_audio.append"]
    assert h.connected == [True]


async def test_dropped_connection_flushes_and_reconnects(harnesses) -> None:
    first, second = FakeConnection(), FakeConnection()
    h = harnesses({}, first, second)
    await h.start()
    first.push(_delta("Hi"))
    await _until(lambda: h.interims() == ["Hi"])

    first.drop()
    await _until(lambda: len(second.sent) >= 1)

    assert h.finals() == ["Hi"]
    assert h.connected == [True, False, True]
    assert second.types() == ["session.update"]

    h.queue.put_nowait(b"\x03\x03")
    await _until(lambda: second.audio() == [b"\x03\x03"])


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(ConnectionRefusedError("refused"), id="connect-raises"),
        pytest.param(
            FakeConnection(created=False), id="error-instead-of-session-created"
        ),
    ],
)
async def test_failed_handshake_retries(harnesses, failure: Any) -> None:
    if isinstance(failure, FakeConnection):
        failure.push({"type": "error", "error": {"message": "nope", "code": 1}})
    h = harnesses({}, failure)
    await h.start()

    assert len(h.connector.calls) == 2
    assert h.connected == [True]
    await _until(lambda: len(h.conn.sent) >= 1)
    assert h.conn.types() == ["session.update"]


class _RejectedHandshake(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.response = type("Response", (), {"status_code": status_code})()


@pytest.mark.parametrize(
    ("status", "hint"), [(401, "api_key_env"), (403, "check model")]
)
async def test_rejected_handshake_logs_the_likely_cause(
    harnesses, caplog: pytest.LogCaptureFixture, status: int, hint: str
) -> None:
    h = harnesses({}, _RejectedHandshake(status))
    with caplog.at_level(logging.ERROR, logger="asr_engine.modules.mistral.realtime"):
        await h.start()

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(f"HTTP {status}" in m and hint in m for m in errors), errors


async def test_server_error_event_does_not_end_the_session(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.conn.push({"type": "error", "error": {"message": "slow down", "code": 9}})
    h.conn.push(_delta("Still here"))
    await _until(lambda: h.interims() == ["Still here"])

    assert len(h.connector.calls) == 1
    assert h.connected == [True]


# ---------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("config", "field"),
    [
        ({"api_key": None}, "api_key"),
        ({"model": ""}, "model"),
        ({"url": "https://api.mistral.ai/v1/audio"}, "url"),
        ({"language": "english"}, "language"),
        ({"language": "e1"}, "language"),
        ({"streaming_delay_ms": 79}, "streaming_delay_ms"),
        ({"streaming_delay_ms": 1500}, "streaming_delay_ms"),
        ({"streaming_delay_ms": 480.0}, "streaming_delay_ms"),
        ({"streaming_delay_ms": True}, "streaming_delay_ms"),
        ({"context_bias": "validate"}, "context_bias"),
        ({"context_bias": ["ok", ""]}, "context_bias"),
        ({"turn_detection": "on"}, "turn_detection"),
        ({"turn_detection": {"min_silence": 300}}, "turn_detection"),
        ({"turn_detection": {"end_threshold": 1.2}}, "end_threshold"),
        ({"turn_detection": {"min_silence_ms": 0}}, "min_silence_ms"),
        ({"turn_detection": {"min_silence_ms": 2.5}}, "min_silence_ms"),
        ({"turn_detection": {"eager_end_threshold": 0.6}}, "start_threshold"),
        ({"turn_detection": {"end_threshold": 0.5}}, "eager_end_threshold"),
        ({"turn_detection": {"min_silence_ms": 4000}}, "end_timeout_ms"),
        ({"final_margin_ms": -1}, "final_margin_ms"),
        ({"finalize_after_silence_s": 0}, "finalize_after_silence_s"),
        ({"stop_timeout_s": -1}, "stop_timeout_s"),
        ({"idle_close_s": 30}, "idle_close_s"),
        ({"idle_close_s": 0}, "idle_close_s"),
    ],
)
def test_invalid_config_raises_naming_the_field(config: dict, field: str) -> None:
    with pytest.raises(ValueError, match=field):
        MistralRealtimeModule({"api_key": "k", **config})


def test_valid_edge_values_are_accepted() -> None:
    for delay in (80, 1200, 2400):
        MistralRealtimeModule({"api_key": "k", "streaming_delay_ms": delay})
    MistralRealtimeModule(
        {
            "api_key": "k",
            "language": "EN",
            "final_margin_ms": 0,
            "idle_close_s": 29.5,
            "turn_detection": {
                "start_threshold": 0.7,
                "eager_end_threshold": 0.4,
                "end_threshold": 0.2,
                "min_silence_ms": 300,
                "end_timeout_ms": 300,
            },
        }
    )


def test_api_key_resolves_from_the_named_env_var(monkeypatch) -> None:
    monkeypatch.setenv("TEST_MISTRAL_KEY", "from-env")
    connector = FakeConnector()
    module = MistralRealtimeModule(
        {"api_key_env": "TEST_MISTRAL_KEY"}, connect=connector
    )

    async def run() -> None:
        task = asyncio.create_task(
            module.start(asyncio.Queue(), _noop_utterance, audio_format=AudioFormat())
        )
        await _until(lambda: len(connector.calls) == 1)
        await module.stop()
        await asyncio.wait_for(task, 1.0)

    asyncio.run(run())
    assert connector.calls[0][1] == {"Authorization": "Bearer from-env"}


async def _noop_utterance(_: SpeechUtterance) -> None:
    return None


def test_registry_resolves_mistral_realtime() -> None:
    assert resolve_module_class("mistral_realtime") is MistralRealtimeModule
