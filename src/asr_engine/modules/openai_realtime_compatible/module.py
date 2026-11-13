"""Transcription over the OpenAI Realtime API protocol (GA transcription sessions).

Registry key ``openai_realtime_compatible``; needs the ``openai-realtime-compatible``
extra (``websockets``). A protocol module: ``url`` defaults to hosted OpenAI, and
any server speaking the same wire protocol works (LiteLLM, NVIDIA Dynamo, LocalAI).

The server owns the utterance boundary: each utterance is an *item* whose text
arrives as incremental deltas (interims) and closes with a ``completed`` event
carrying the authoritative transcript (the final). Items are emitted in the order
the module first hears of them, since completions are not guaranteed to arrive in
order. See specs/modules/openai-realtime-compatible.md.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

import websockets

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat, EndOfSpeech
from asr_engine.modules.base import (
    ASRModule,
    AudioQueue,
    ConnectedCallback,
    SpeechUtterance,
    UtteranceCallback,
    resolve_api_key,
)

log = logging.getLogger(__name__)

_LABEL = "openai_realtime_compatible"
DEFAULT_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
DEFAULT_MODEL = "gpt-4o-transcribe"
_OPENAI_HOST = "api.openai.com"

# The server rejects a commit on less than 100 ms of buffered audio (measured).
_MIN_COMMIT_S = 0.1
_HANDSHAKE_TIMEOUT_S = 10.0
_CLOSE_TIMEOUT_S = 2.0
_TURN_TYPES = ("server_vad", "semantic_vad")
_NOISE_TYPES = ("near_field", "far_field")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2}$")
_COMMIT_EMPTY = "input_audio_buffer_commit_empty"
_HANDSHAKE_HINTS = {
    401: "the API key was rejected: check api_key / api_key_env",
    403: "access was refused: check url and model",
    404: "no realtime endpoint there: check url",
}

_DELTA = "conversation.item.input_audio_transcription.delta"
_COMPLETED = "conversation.item.input_audio_transcription.completed"
_FAILED = "conversation.item.input_audio_transcription.failed"


class RealtimeConnection(Protocol):
    """The slice of a WebSocket client connection the module uses."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


Connector = Callable[
    [str, dict[str, str]], AbstractAsyncContextManager[RealtimeConnection]
]


class SessionRejected(ConnectionError):
    """The server answered the session setup with an ``error`` event."""


def _websockets_connect(
    url: str, headers: dict[str, str]
) -> AbstractAsyncContextManager[RealtimeConnection]:
    return websockets.connect(
        url, additional_headers=headers, close_timeout=_CLOSE_TIMEOUT_S
    )


def _parse_event(raw: str | bytes) -> dict[str, Any]:
    try:
        event = json.loads(raw)
    except ValueError:
        log.warning("%s: ignoring a non-JSON server message", _LABEL)
        return {}
    return event if isinstance(event, dict) else {}


def _error_of(event: dict[str, Any]) -> tuple[object, object]:
    """``(code, message)`` of an ``error`` (or ``failed``) event."""
    error = event.get("error")
    if isinstance(error, dict):
        return error.get("code"), error.get("message")
    return None, error


def _parse_turn_detection(value: object) -> dict[str, Any] | None:
    """``True`` → server VAD with server defaults; ``False`` → ``None`` (off); an
    object passes through once its ``type`` is known."""
    if value is True:
        return {"type": "server_vad"}
    if value is False:
        return None
    if not isinstance(value, dict) or value.get("type") not in _TURN_TYPES:
        raise ValueError(
            f"{_LABEL} module: 'turn_detection' must be a boolean or an object "
            f"whose 'type' is one of {list(_TURN_TYPES)}"
        )
    return dict(value)


async def _cancel(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@dataclass
class _Item:
    """One server item (= one utterance) still waiting to be emitted."""

    text: str = ""  # accumulated deltas
    final: str | None = None  # set by completed/failed


class OpenAIRealtimeCompatibleModule(ASRModule):
    """ASR module for any server speaking the OpenAI Realtime transcription protocol."""

    SUPPORTED_SAMPLE_RATES = frozenset({24000})  # the only rate hosted OpenAI accepts
    SUPPORTED_CHANNELS = frozenset({1})
    SUPPORTED_ENCODINGS = frozenset({"linear16"})
    DEFAULT_SAMPLE_RATE = 24000
    DEFAULT_CHANNELS = 1
    DEFAULT_ENCODING = "linear16"
    VAD_GATING = "supported"

    _BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

    def __init__(self, config: dict, *, connect: Connector | None = None) -> None:
        super().__init__(config)
        self._url = config.get("url", DEFAULT_URL)
        if not isinstance(self._url, str) or not self._url.startswith(
            ("ws://", "wss://")
        ):
            raise ValueError(
                f"{_LABEL} module: 'url' must be a ws:// or wss:// URL string"
            )
        self._api_key: str | None = None
        if config.get("api_key") or config.get("api_key_env"):
            self._api_key = resolve_api_key(config, _LABEL)
        elif urlsplit(self._url).hostname == _OPENAI_HOST:
            raise ValueError(
                f"{_LABEL} module: hosted OpenAI ({_OPENAI_HOST}) requires "
                f"'api_key' or 'api_key_env' in config"
            )

        self._model = config.get("model", DEFAULT_MODEL)
        if not isinstance(self._model, str) or not self._model:
            raise ValueError(f"{_LABEL} module: 'model' must be a non-empty string")
        self._language = config.get("language")
        if self._language is not None and (
            not isinstance(self._language, str)
            or not _LANGUAGE_RE.match(self._language)
        ):
            raise ValueError(
                f"{_LABEL} module: 'language' must be null or a two-letter "
                f"ISO 639-1 code (e.g. 'en')"
            )
        self._prompt = config.get("prompt")
        if self._prompt is not None and not isinstance(self._prompt, str):
            raise ValueError(f"{_LABEL} module: 'prompt' must be null or a string")
        self._turn_detection = _parse_turn_detection(config.get("turn_detection", True))
        self._noise_reduction = config.get("noise_reduction")
        if self._noise_reduction is not None and self._noise_reduction not in (
            _NOISE_TYPES
        ):
            raise ValueError(
                f"{_LABEL} module: 'noise_reduction' must be null or one of "
                f"{list(_NOISE_TYPES)}"
            )
        stop_timeout_s = config.get("stop_timeout_s", 5.0)
        if (
            not isinstance(stop_timeout_s, (int, float))
            or isinstance(stop_timeout_s, bool)
            or stop_timeout_s <= 0
        ):
            raise ValueError(
                f"{_LABEL} module: 'stop_timeout_s' must be a positive number"
            )
        self._stop_timeout_s = float(stop_timeout_s)

        self._connect: Connector = connect or _websockets_connect

        self._stop_event = asyncio.Event()
        self._stopped = asyncio.Event()
        self._running = False
        self._connected = False
        self._handshake_ok = False
        self._on_utterance: UtteranceCallback | None = None
        self._on_connected: ConnectedCallback | None = None
        self._conn: RealtimeConnection | None = None
        self._min_commit_bytes = 0

        # Per-session state. Items in the order first heard of; the emit lock
        # serializes every emission and every change to them.
        self._items: dict[str, _Item] = {}
        self._closed_items: set[str] = set()
        self._emit_lock = asyncio.Lock()
        self._server_turn_open = False  # speech_started seen, committed not yet
        self._uncommitted_bytes = 0  # appended since our last commit/clear
        self._awaiting_commits = 0  # our commits not yet acknowledged
        self._settled = asyncio.Event()  # no open item, no commit in flight

    # ------------------------------------------------------------------ lifecycle

    async def start(
        self,
        audio_queue: AudioQueue,
        on_utterance: UtteranceCallback,
        on_connected: ConnectedCallback | None = None,
        *,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        self._stop_event.clear()
        self._stopped.clear()
        self._running = True
        self._on_utterance = on_utterance
        self._on_connected = on_connected
        self._min_commit_bytes = int(
            audio_format.sample_rate
            * _MIN_COMMIT_S
            * audio_format.channels
            * audio_format.bytes_per_sample
        )
        session_update = self._session_update(audio_format)
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        if self._turn_detection is None:
            log.info(
                "%s: turn_detection is off; finals come only from engine.vad "
                "end-of-speech markers or stop()",
                _LABEL,
            )
        attempt = 0
        try:
            while not self._stop_event.is_set():
                try:
                    await self._run_session(headers, session_update, audio_queue)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._flush_items()
                    self._set_connected(False)
                    if self._stop_event.is_set():
                        break
                    if self._handshake_ok:
                        attempt = 0  # the session was up: restart the backoff
                    delay = self._BACKOFF_S[min(attempt, len(self._BACKOFF_S) - 1)]
                    attempt += 1
                    self._log_failure(exc, attempt, delay)
                    await self._drain_for(audio_queue, delay)
                    continue
                break  # the session ended on stop
        finally:
            self._set_connected(False)
            self._running = False
            self._stopped.set()

    async def stop(self) -> None:
        """End the session (committing and flushing its last items) and return
        once it has ended.

        The engine cancels ``start()`` right after this returns, so the flush has
        to complete here. Bounded: a session that won't end is abandoned.
        """
        self._stop_event.set()
        if not self._running:
            return
        try:
            await asyncio.wait_for(
                self._stopped.wait(), self._stop_timeout_s + _CLOSE_TIMEOUT_S + 1
            )
        except asyncio.TimeoutError:
            log.warning("%s: session did not end in time; abandoning it", _LABEL)

    async def finalize(self) -> None:
        """End of a VAD-gated utterance: commit the buffered audio, so the server
        transcribes it as one item — or clear it when there is nothing to commit.

        See specs/modules/openai-realtime-compatible.md "VAD gating".
        """
        conn = self._conn
        if conn is None:
            return
        await self._commit_or_clear(conn)

    # -------------------------------------------------------------------- session

    def _session_update(self, audio_format: AudioFormat) -> dict[str, Any]:
        transcription: dict[str, Any] = {"model": self._model}
        if self._language is not None:
            transcription["language"] = self._language
        if self._prompt is not None:
            transcription["prompt"] = self._prompt
        audio_input: dict[str, Any] = {
            "format": {"type": "audio/pcm", "rate": audio_format.sample_rate},
            "transcription": transcription,
            "turn_detection": (
                dict(self._turn_detection) if self._turn_detection else None
            ),
        }
        if self._noise_reduction is not None:
            audio_input["noise_reduction"] = {"type": self._noise_reduction}
        return {
            "type": "session.update",
            "session": {"type": "transcription", "audio": {"input": audio_input}},
        }

    async def _run_session(
        self,
        headers: dict[str, str],
        session_update: dict[str, Any],
        audio_queue: AudioQueue,
    ) -> None:
        """One server session: handshake, stream until stop, then flush.

        Returns on stop; raises on any connection or protocol failure.
        """
        self._handshake_ok = False
        self._reset_session_state()
        async with self._connect(self._url, headers) as conn:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _HANDSHAKE_TIMEOUT_S
            await self._expect(conn, "session.created", deadline)
            await conn.send(json.dumps(session_update))
            await self._expect(conn, "session.updated", deadline)
            self._handshake_ok = True
            self._conn = conn
            self._set_connected(True)

            receiver = asyncio.create_task(self._receive(conn))
            sender = asyncio.create_task(self._send_audio(conn, audio_queue))
            try:
                await asyncio.wait(
                    {receiver, sender}, return_when=asyncio.FIRST_COMPLETED
                )
                if not sender.done():
                    # The server ended or broke the session before we asked it to.
                    exc = receiver.exception()
                    raise exc or ConnectionError("server ended the session")
                sender.result()  # re-raises a failed send
                await self._commit_or_clear(conn)
                await self._wait_settled(receiver)
                await self._flush_items()
            finally:
                self._conn = None
                await _cancel(receiver, sender)

    async def _expect(
        self, conn: RealtimeConnection, kind: str, deadline: float
    ) -> None:
        """Read events until *kind*; an ``error`` rejects the session setup."""
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"no {kind} within {_HANDSHAKE_TIMEOUT_S}s")
            event = _parse_event(await asyncio.wait_for(conn.recv(), remaining))
            if event.get("type") == kind:
                if kind == "session.updated":
                    log.debug("%s session: %s", _LABEL, event.get("session"))
                return
            if event.get("type") == "error":
                code, message = _error_of(event)
                raise SessionRejected(
                    f"server rejected the session ({code}): {message}"
                )

    async def _wait_settled(self, receiver: asyncio.Task) -> None:
        """Wait (bounded) until every open item has completed."""
        waiter = asyncio.create_task(self._settled.wait())
        try:
            await asyncio.wait(
                {waiter, receiver},
                timeout=self._stop_timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not waiter.done():
                if receiver.done():
                    log.debug("%s: session closed while flushing", _LABEL)
                else:
                    log.warning(
                        "%s: items still open after %.1fs; flushing them",
                        _LABEL,
                        self._stop_timeout_s,
                    )
        finally:
            await _cancel(waiter)

    async def _send_audio(
        self, conn: RealtimeConnection, audio_queue: AudioQueue
    ) -> None:
        while True:
            chunk = await self._next_chunk(audio_queue)
            if chunk is None:
                return  # stop
            if isinstance(chunk, EndOfSpeech):
                await self.finalize()
            else:
                audio = base64.b64encode(chunk).decode("ascii")
                await conn.send(
                    json.dumps({"type": "input_audio_buffer.append", "audio": audio})
                )
                self._uncommitted_bytes += len(chunk)

    async def _commit_or_clear(self, conn: RealtimeConnection) -> None:
        """Commit the buffer if it holds an utterance the server has not
        committed itself; otherwise clear what it holds."""
        if self._turn_detection is not None:
            commit = self._server_turn_open
        else:
            commit = self._uncommitted_bytes >= self._min_commit_bytes
        if commit:
            self._awaiting_commits += 1
            self._server_turn_open = False
            self._refresh_settled()
            await conn.send(json.dumps({"type": "input_audio_buffer.commit"}))
        elif self._uncommitted_bytes:
            await conn.send(json.dumps({"type": "input_audio_buffer.clear"}))
        self._uncommitted_bytes = 0

    async def _receive(self, conn: RealtimeConnection) -> None:
        """Apply server events until the connection fails."""
        while True:
            event = _parse_event(await conn.recv())
            kind = event.get("type")
            item_id = event.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                item_id = None
            if kind == _DELTA:
                if item_id is not None:
                    await self._on_delta(item_id, event.get("delta"))
            elif kind == _COMPLETED:
                if item_id is not None:
                    await self._on_done(item_id, event.get("transcript"))
            elif kind == _FAILED:
                code, message = _error_of(event)
                log.warning(
                    "%s: transcription failed for %s (%s): %s",
                    _LABEL,
                    item_id,
                    code,
                    message,
                )
                if item_id is not None:
                    await self._on_done(item_id, None)
            elif kind == "input_audio_buffer.speech_started":
                self._server_turn_open = True
            elif kind == "input_audio_buffer.committed":
                self._server_turn_open = False
                if self._turn_detection is not None:
                    self._uncommitted_bytes = 0  # the server committed on its own
                self._awaiting_commits = max(0, self._awaiting_commits - 1)
                if item_id is not None:
                    async with self._emit_lock:
                        self._register(item_id)
                self._refresh_settled()
            elif kind == "error":
                code, message = _error_of(event)
                if code == _COMMIT_EMPTY:
                    # Our commit raced the server's own (see the spec's VAD gating).
                    log.debug("%s: commit on an empty buffer: %s", _LABEL, message)
                    self._awaiting_commits = max(0, self._awaiting_commits - 1)
                    self._refresh_settled()
                else:
                    log.warning("%s server error %s: %s", _LABEL, code, message)

    # --------------------------------------------------------- utterance assembly

    def _register(self, item_id: str) -> _Item | None:
        """The open item *item_id*, created on first sight; ``None`` once emitted."""
        if item_id in self._closed_items:
            return None
        item = self._items.get(item_id)
        if item is None:
            item = self._items[item_id] = _Item()
            self._refresh_settled()
        return item

    async def _on_delta(self, item_id: str, delta: object) -> None:
        if not isinstance(delta, str) or not delta:
            return
        async with self._emit_lock:
            item = self._register(item_id)
            if item is None or item.final is not None:
                return
            item.text += delta  # deltas carry their own spacing
            transcript = item.text.strip()
            if transcript and next(iter(self._items)) == item_id:
                await self._emit(transcript, is_final=False)

    async def _on_done(self, item_id: str, transcript: object) -> None:
        async with self._emit_lock:
            item = self._register(item_id)
            if item is None:
                return
            item.final = (
                transcript.strip() if isinstance(transcript, str) else item.text.strip()
            )
            popped = False
            while self._items:
                head_id, head = next(iter(self._items.items()))
                if head.final is None:
                    # A new oldest item shows the text it gathered meanwhile.
                    if popped and head.text.strip():
                        await self._emit(head.text.strip(), is_final=False)
                    break
                del self._items[head_id]
                self._closed_items.add(head_id)
                popped = True
                if head.final:
                    await self._emit(head.final, is_final=True)
            self._refresh_settled()

    async def _flush_items(self) -> None:
        """Emit every open item as a final, in order (session end or loss)."""
        async with self._emit_lock:
            items = list(self._items.items())
            self._items.clear()
            self._awaiting_commits = 0
            for item_id, item in items:
                self._closed_items.add(item_id)
                transcript = item.final if item.final is not None else item.text.strip()
                if transcript:
                    await self._emit(transcript, is_final=True)
            self._refresh_settled()

    def _reset_session_state(self) -> None:
        self._items.clear()
        self._closed_items.clear()
        self._server_turn_open = False
        self._uncommitted_bytes = 0
        self._awaiting_commits = 0
        self._refresh_settled()

    def _refresh_settled(self) -> None:
        if not self._items and self._awaiting_commits == 0:
            self._settled.set()
        else:
            self._settled.clear()

    async def _emit(self, transcript: str, *, is_final: bool) -> None:
        if self._on_utterance is None:
            return
        try:
            await self._on_utterance(
                SpeechUtterance(
                    transcript=transcript, is_final=is_final, confidence=None
                )
            )
        except Exception:
            log.exception("%s: utterance callback failed", _LABEL)

    # -------------------------------------------------------------------- helpers

    def _set_connected(self, connected: bool) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        if self._on_connected is not None:
            self._on_connected(connected)

    async def _next_chunk(
        self, audio_queue: AudioQueue, timeout: float | None = None
    ) -> bytes | EndOfSpeech | None:
        """The next queue item, or ``None`` on stop or after *timeout* seconds."""
        if self._stop_event.is_set():
            return None
        get_task = asyncio.create_task(audio_queue.get())
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            await asyncio.wait(
                {get_task, stop_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            await _cancel(*(t for t in (get_task, stop_task) if not t.done()))
        # Checked after the cancel, not from wait()'s done set: a get that completed
        # in between still holds a dequeued chunk, which must not be dropped.
        if get_task.done() and not get_task.cancelled():
            return get_task.result()
        return None

    async def _drain_for(self, audio_queue: AudioQueue, delay: float) -> None:
        """Discard audio (and end-of-speech markers) for *delay* seconds, or until
        stop, while reconnecting."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + delay
        while not self._stop_event.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await self._next_chunk(audio_queue, timeout=remaining)

    def _log_failure(self, exc: BaseException, attempt: int, delay: float) -> None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        hint = _HANDSHAKE_HINTS.get(status) if isinstance(status, int) else None
        log.error(
            "%s connection error (attempt %d, retrying in %.0fs): %s%s",
            _LABEL,
            attempt,
            delay,
            exc or type(exc).__name__,
            f" — {hint}" if hint else "",
        )
