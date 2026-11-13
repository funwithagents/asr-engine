"""Functional tests for the openai_realtime_compatible module
(specs/modules/openai-realtime-compatible.md).

The module is driven through its ``connect`` seam with a scripted fake connection:
it records every client message, answers the session handshake, and serves the
server events each test pushes. No network.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Callable

import pytest

from asr_engine.audio import END_OF_SPEECH, AudioFormat
from asr_engine.modules import resolve_module_class
from asr_engine.modules.base import AudioQueue, SpeechUtterance
from asr_engine.modules.openai_realtime_compatible.module import (
    OpenAIRealtimeCompatibleModule,
)

FORMAT = AudioFormat(sample_rate=24000)
MS_100 = 24000 // 10 * 2  # bytes of 100 ms of 24 kHz mono linear16

# --------------------------------------------------------------------------- fakes


def delta(item_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "conversation.item.input_audio_transcription.delta",
        "item_id": item_id,
        "content_index": 0,
        "delta": text,
    }


def completed(item_id: str, transcript: str) -> dict[str, Any]:
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": item_id,
        "content_index": 0,
        "transcript": transcript,
    }


def committed(item_id: str) -> dict[str, Any]:
    return {
        "type": "input_audio_buffer.committed",
        "item_id": item_id,
        "previous_item_id": None,
    }


SPEECH_STARTED = {"type": "input_audio_buffer.speech_started", "item_id": "x"}


class FakeConnection:
    """One scripted server session.

    Starts with ``session.created`` queued and answers ``session.update`` with
    ``session.updated`` (or with ``update_reply`` when given). Each client
    ``input_audio_buffer.commit`` is answered with the next list in ``on_commit``.
    """

    def __init__(
        self,
        *,
        update_reply: dict[str, Any] | None = None,
        on_commit: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._incoming: asyncio.Queue[dict[str, Any] | BaseException] = asyncio.Queue()
        self._update_reply = update_reply
        self._on_commit = list(on_commit or [])
        self.push({"type": "session.created", "session": {}})

    def push(self, *events: dict[str, Any]) -> None:
        for event in events:
            self._incoming.put_nowait(event)

    def drop(self) -> None:
        self._incoming.put_nowait(ConnectionError("connection dropped"))

    async def send(self, message: str) -> None:
        if self.closed:
            raise ConnectionError("send on a closed connection")
        msg = json.loads(message)
        self.sent.append(msg)
        if msg["type"] == "session.update":
            self.push(self._update_reply or {"type": "session.updated", "session": {}})
        elif msg["type"] == "input_audio_buffer.commit" and self._on_commit:
            self.push(*self._on_commit.pop(0))

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
            if m["type"] == "input_audio_buffer.append"
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
        self.module = OpenAIRealtimeCompatibleModule(
            {"api_key": "test-key", **config}, connect=self.connector
        )
        self.module._BACKOFF_S = (0.01,)
        self.queue: AudioQueue = asyncio.Queue()
        self.utterances: list[SpeechUtterance] = []
        self.connected: list[bool] = []
        self.task: asyncio.Task | None = None

    async def start(self, *, wait_connected: bool = True) -> None:
        async def on_utterance(u: SpeechUtterance) -> None:
            self.utterances.append(u)

        self.task = asyncio.create_task(
            self.module.start(
                self.queue, on_utterance, self.connected.append, audio_format=FORMAT
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

    assert h.conn.sent[0] == {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {"model": "gpt-4o-transcribe"},
                    "turn_detection": {"type": "server_vad"},
                }
            },
        },
    }
    assert h.connector.calls[0] == (
        "wss://api.openai.com/v1/realtime?intent=transcription",
        {"Authorization": "Bearer test-key"},
    )


async def test_session_update_carries_configured_fields(harnesses) -> None:
    h = harnesses(
        {
            "url": "ws://localhost:4000/v1/realtime?model=chirp&intent=transcription",
            "model": "chirp_3",
            "language": "fr",
            "prompt": "valider",
            "noise_reduction": "far_field",
            "turn_detection": {"type": "semantic_vad", "eagerness": "high"},
        }
    )
    await h.start()

    assert h.conn.sent[0]["session"]["audio"]["input"] == {
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": {"model": "chirp_3", "language": "fr", "prompt": "valider"},
        "turn_detection": {"type": "semantic_vad", "eagerness": "high"},
        "noise_reduction": {"type": "far_field"},
    }
    url, _ = h.connector.calls[0]
    assert url == "ws://localhost:4000/v1/realtime?model=chirp&intent=transcription"


async def test_turn_detection_false_sends_null(harnesses) -> None:
    h = harnesses({"turn_detection": False})
    await h.start()

    assert h.conn.sent[0]["session"]["audio"]["input"]["turn_detection"] is None


async def test_a_server_without_key_gets_no_authorization_header() -> None:
    connector = FakeConnector()
    module = OpenAIRealtimeCompatibleModule(
        {"url": "ws://gpu-host:8000/v1/realtime"}, connect=connector
    )
    connected: list[bool] = []

    async def on_utterance(u: SpeechUtterance) -> None:
        pass

    task = asyncio.create_task(
        module.start(
            asyncio.Queue(), on_utterance, connected.append, audio_format=FORMAT
        )
    )
    await _until(lambda: connected == [True])
    await module.stop()
    await asyncio.wait_for(task, 1.0)

    assert connector.calls[0] == ("ws://gpu-host:8000/v1/realtime", {})


async def test_connected_waits_for_session_updated(harnesses) -> None:
    conn = FakeConnection(update_reply={"type": "conversation.item.added"})
    h = harnesses({}, conn)
    await h.start(wait_connected=False)
    await _until(lambda: conn.types() == ["session.update"])
    await asyncio.sleep(0.02)
    assert h.connected == []

    conn.push({"type": "session.updated", "session": {}})
    await _until(lambda: h.connected == [True])


async def test_a_rejected_session_update_retries_and_logs_the_server_message(
    harnesses, caplog
) -> None:
    rejected = FakeConnection(
        update_reply={
            "type": "error",
            "error": {
                "code": "integer_below_min_value",
                "message": "Expected a value >= 24000",
            },
        }
    )
    h = harnesses({}, rejected)
    with caplog.at_level(logging.ERROR):
        await h.start()

    assert len(h.connector.connections) == 2
    assert h.conn.types() == ["session.update"]
    assert "Expected a value >= 24000" in caplog.text


# ----------------------------------------------------------------------- streaming


async def test_audio_is_sent_as_base64_in_order(harnesses) -> None:
    h = harnesses()
    await h.start()
    chunks = [b"\x01\x02" * 10, b"\x03\x04" * 20, b"\x05\x06"]
    for chunk in chunks:
        h.queue.put_nowait(chunk)
    await _until(lambda: len(h.conn.audio()) == 3)

    assert h.conn.audio() == chunks


async def test_deltas_give_cumulative_interims_and_completed_the_final(
    harnesses,
) -> None:
    h = harnesses()
    await h.start()
    h.conn.push(
        committed("a"),
        delta("a", "The"),
        delta("a", " sky"),
        delta("a", " is blue,"),
        completed("a", "The sky is blue, validate."),
    )
    await _until(lambda: len(h.finals()) == 1)

    assert h.interims() == ["The", "The sky", "The sky is blue,"]
    assert h.finals() == ["The sky is blue, validate."]


async def test_finals_leave_in_item_order(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.conn.push(
        committed("a"),
        committed("b"),
        delta("a", "first"),
        delta("b", "second"),
        completed("b", "second one"),
    )
    await _until(lambda: h.interims() == ["first"])
    await asyncio.sleep(0.02)
    # b is complete but waits behind a; its deltas never surfaced as interims.
    assert h.finals() == []

    h.conn.push(completed("a", "first one"))
    await _until(lambda: len(h.finals()) == 2)

    assert h.finals() == ["first one", "second one"]
    assert h.interims() == ["first"]


async def test_a_later_item_surfaces_its_text_once_it_is_the_oldest(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.conn.push(
        committed("a"),
        committed("b"),
        delta("b", "second"),
        completed("a", "first"),
    )
    await _until(lambda: h.finals() == ["first"])
    await _until(lambda: h.interims() == ["second"])

    h.conn.push(delta("b", " part"))
    await _until(lambda: h.interims() == ["second", "second part"])


async def test_streaming_deltas_before_the_commit_are_interims(harnesses) -> None:
    # gpt-realtime-whisper streams words before any commit (measured).
    h = harnesses({"turn_detection": False})
    await h.start()
    h.conn.push(delta("a", " The"), delta("a", " sky"))
    await _until(lambda: h.interims() == ["The", "The sky"])

    h.conn.push(committed("a"), delta("a", "."), completed("a", "The sky."))
    await _until(lambda: h.finals() == ["The sky."])


async def test_a_failed_item_closes_with_its_text(harnesses, caplog) -> None:
    h = harnesses()
    await h.start()
    with caplog.at_level(logging.WARNING):
        h.conn.push(
            committed("a"),
            delta("a", "half a"),
            {
                "type": "conversation.item.input_audio_transcription.failed",
                "item_id": "a",
                "content_index": 0,
                "error": {"code": "x", "message": "boom"},
            },
            committed("b"),
            completed("b", "next"),
        )
        await _until(lambda: len(h.finals()) == 2)

    assert h.finals() == ["half a", "next"]
    assert "boom" in caplog.text


async def test_an_empty_transcript_emits_nothing(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.conn.push(committed("a"), completed("a", "  "), committed("b"))
    h.conn.push(completed("b", "after"))
    await _until(lambda: len(h.finals()) == 1)

    assert h.finals() == ["after"]


# ---------------------------------------------------------------------- VAD gating


async def test_marker_commits_a_long_enough_buffer_without_turn_detection(
    harnesses,
) -> None:
    h = harnesses({"turn_detection": False})
    await h.start()
    h.queue.put_nowait(b"\x00" * MS_100)
    h.queue.put_nowait(END_OF_SPEECH)
    await _until(lambda: "input_audio_buffer.commit" in h.conn.types())

    h.conn.push(committed("a"), completed("a", "hello"))
    await _until(lambda: h.finals() == ["hello"])

    # The next utterance goes out on the same session.
    h.queue.put_nowait(b"\x00" * MS_100)
    h.queue.put_nowait(END_OF_SPEECH)
    await _until(lambda: h.conn.types().count("input_audio_buffer.commit") == 2)
    assert len(h.connector.connections) == 1


async def test_marker_clears_a_too_short_buffer_without_turn_detection(
    harnesses,
) -> None:
    h = harnesses({"turn_detection": False})
    await h.start()
    h.queue.put_nowait(b"\x00" * (MS_100 - 2))
    h.queue.put_nowait(END_OF_SPEECH)
    await _until(lambda: "input_audio_buffer.clear" in h.conn.types())

    # And an empty buffer gets neither.
    h.queue.put_nowait(END_OF_SPEECH)
    h.queue.put_nowait(b"\x00\x00")
    await _until(lambda: len(h.conn.audio()) == 2)
    assert "input_audio_buffer.commit" not in h.conn.types()
    assert h.conn.types().count("input_audio_buffer.clear") == 1


async def test_marker_commits_while_the_server_turn_is_open(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.conn.push(SPEECH_STARTED)
    h.queue.put_nowait(b"\x00" * 10)
    await _until(lambda: len(h.conn.audio()) == 1)
    h.queue.put_nowait(END_OF_SPEECH)

    await _until(lambda: "input_audio_buffer.commit" in h.conn.types())
    assert "input_audio_buffer.clear" not in h.conn.types()


async def test_marker_clears_once_the_server_committed_the_turn(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.conn.push(SPEECH_STARTED, committed("a"))
    await _until(lambda: "a" in h.module._items)
    h.queue.put_nowait(b"\x00" * MS_100 * 3)  # trailing silence (the hangover)
    h.queue.put_nowait(END_OF_SPEECH)

    await _until(lambda: "input_audio_buffer.clear" in h.conn.types())
    assert "input_audio_buffer.commit" not in h.conn.types()


async def test_a_marker_while_reconnecting_is_dropped(harnesses) -> None:
    h = harnesses({"turn_detection": False}, ConnectionError("refused"))
    h.module._BACKOFF_S = (0.2,)
    await h.start(wait_connected=False)
    await _until(lambda: len(h.connector.calls) == 1)
    h.queue.put_nowait(b"\x00" * MS_100)
    h.queue.put_nowait(END_OF_SPEECH)
    await _until(lambda: h.connected[-1:] == [True])
    h.queue.put_nowait(b"\x01\x01")
    await _until(lambda: len(h.conn.audio()) == 1)

    assert h.conn.audio() == [b"\x01\x01"]
    assert "input_audio_buffer.commit" not in h.conn.types()


async def test_a_commit_race_is_logged_at_debug_only(harnesses, caplog) -> None:
    h = harnesses()
    await h.start()
    with caplog.at_level(logging.INFO):
        h.conn.push(
            {
                "type": "error",
                "error": {
                    "code": "input_audio_buffer_commit_empty",
                    "message": "buffer too small",
                },
            },
            committed("a"),
            completed("a", "ok"),
        )
        await _until(lambda: h.finals() == ["ok"])

    assert "buffer too small" not in caplog.text


# ---------------------------------------------------------------------------- stop


async def test_stop_commits_and_delivers_the_last_final(harnesses) -> None:
    conn = FakeConnection(
        on_commit=[[committed("a"), delta("a", "last"), completed("a", "last words")]]
    )
    h = harnesses({"turn_detection": False}, conn)
    await h.start()
    h.queue.put_nowait(b"\x00" * MS_100)
    await _until(lambda: len(conn.audio()) == 1)

    await h.stop()

    assert conn.types()[-1] == "input_audio_buffer.commit"
    assert h.finals() == ["last words"]
    assert h.connected == [True, False]
    assert conn.closed


async def test_stop_flushes_items_that_never_complete(harnesses) -> None:
    conn = FakeConnection(on_commit=[[committed("a"), delta("a", "never done")]])
    h = harnesses({"turn_detection": False, "stop_timeout_s": 0.05}, conn)
    await h.start()
    h.queue.put_nowait(b"\x00" * MS_100)
    await _until(lambda: len(conn.audio()) == 1)

    await h.stop()

    assert h.finals() == ["never done"]
    assert h.connected == [True, False]


async def test_stop_does_not_commit_trailing_silence(harnesses) -> None:
    h = harnesses()
    await h.start()
    h.conn.push(SPEECH_STARTED, committed("a"), completed("a", "said"))
    await _until(lambda: h.finals() == ["said"])
    h.queue.put_nowait(b"\x00" * MS_100 * 5)
    await _until(lambda: len(h.conn.audio()) == 1)

    await h.stop()

    assert "input_audio_buffer.commit" not in h.conn.types()
    assert h.finals() == ["said"]


# ----------------------------------------------------------------------- reconnect


async def test_a_dropped_socket_flushes_open_items_and_reconnects(harnesses) -> None:
    h = harnesses()
    await h.start()
    first = h.conn
    first.push(committed("a"), delta("a", "cut off"))
    await _until(lambda: h.interims() == ["cut off"])

    first.drop()
    await _until(lambda: len(h.connector.connections) == 2)
    await _until(lambda: h.connected == [True, False, True])

    assert h.finals() == ["cut off"]
    assert h.conn.types()[0] == "session.update"


async def test_a_failed_connect_retries(harnesses) -> None:
    h = harnesses({}, ConnectionError("refused"), ConnectionError("refused"))
    await h.start()

    assert len(h.connector.calls) == 3


# ---------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "config, field",
    [
        ({"url": "https://api.openai.com/v1/realtime"}, "url"),
        ({"url": 3}, "url"),
        ({"model": ""}, "model"),
        ({"language": "english"}, "language"),
        ({"prompt": ["a"]}, "prompt"),
        ({"turn_detection": "on"}, "turn_detection"),
        ({"turn_detection": {"threshold": 0.4}}, "turn_detection"),
        ({"turn_detection": {"type": "push_to_talk"}}, "turn_detection"),
        ({"noise_reduction": "studio"}, "noise_reduction"),
        ({"stop_timeout_s": 0}, "stop_timeout_s"),
        ({"stop_timeout_s": True}, "stop_timeout_s"),
    ],
)
def test_invalid_config_is_rejected(config: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        OpenAIRealtimeCompatibleModule({"api_key": "k", **config})


def test_hosted_openai_requires_a_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        OpenAIRealtimeCompatibleModule({})


def test_a_named_key_variable_must_be_set(monkeypatch) -> None:
    monkeypatch.delenv("ASR_TEST_UNSET_KEY", raising=False)
    with pytest.raises(ValueError, match="ASR_TEST_UNSET_KEY"):
        OpenAIRealtimeCompatibleModule(
            {"url": "ws://localhost/v1/realtime", "api_key_env": "ASR_TEST_UNSET_KEY"}
        )


def test_the_registry_resolves_the_module() -> None:
    assert (
        resolve_module_class("openai_realtime_compatible")
        is OpenAIRealtimeCompatibleModule
    )
