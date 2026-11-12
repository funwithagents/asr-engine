"""Voxtral 3 realtime transcription over Mistral's WebSocket API.

Registry key ``voxtral_realtime``; needs the ``mistral`` extra (``websockets``).
The server streams append-only, already-final text deltas and turn events on the
same delayed timeline. The module accumulates deltas into interims and closes an
utterance shortly after ``turn.end`` (or after a no-new-text timer). It owns the
socket directly rather than going through the ``mistralai`` SDK, whose documented
stream events omit the turn events. See specs/modules/mistral.md.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from contextlib import AbstractAsyncContextManager
from typing import Any, Callable, Literal, Protocol
from urllib.parse import urlencode

import websockets

from asr_engine.audio import DEFAULT_AUDIO_FORMAT, AudioFormat
from asr_engine.modules.base import (
    ASRModule,
    ConnectedCallback,
    SpeechUtterance,
    UtteranceCallback,
    resolve_api_key,
)

log = logging.getLogger(__name__)

_LABEL = "voxtral_realtime"
DEFAULT_URL = "wss://api.mistral.ai/v1/audio/transcriptions/realtime"
DEFAULT_MODEL = "voxtral-mini-transcribe-realtime-3"

# Server defaults for turn_detection, used to check the server's cross-field
# constraints when a config overrides only some keys.
_TURN_DEFAULTS: dict[str, float] = {
    "start_threshold": 0.5,
    "eager_end_threshold": 0.5,
    "end_threshold": 0.33,
    "min_silence_ms": 500,
    "end_timeout_ms": 3000,
}
_TURN_THRESHOLDS = ("start_threshold", "eager_end_threshold", "end_threshold")
_TURN_DURATIONS = ("min_silence_ms", "end_timeout_ms")

_SERVER_IDLE_KILL_S = (
    30.0  # measured: the server drops a session after ~30 s of no audio
)
_HANDSHAKE_TIMEOUT_S = 10.0
_CLOSE_TIMEOUT_S = 2.0
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2}$")
_HANDSHAKE_HINTS = {
    401: "the API key was rejected: check api_key / api_key_env",
    403: "access was refused: check model",
}

SessionEnd = Literal["stop", "idle"]


class RealtimeConnection(Protocol):
    """The slice of a WebSocket client connection the module uses."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


Connector = Callable[
    [str, dict[str, str]], AbstractAsyncContextManager[RealtimeConnection]
]


def _websockets_connect(
    url: str, headers: dict[str, str]
) -> AbstractAsyncContextManager[RealtimeConnection]:
    return websockets.connect(
        url, additional_headers=headers, close_timeout=_CLOSE_TIMEOUT_S
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive_number(config: dict, key: str, default: float) -> float:
    value = config.get(key, default)
    if not _is_number(value) or value <= 0:
        raise ValueError(f"{_LABEL} module: '{key}' must be a positive number")
    return float(value)


def _parse_turn_detection(value: object) -> dict[str, Any] | None:
    """``True`` → server defaults (``{}``); ``False`` → omitted; an object is
    validated against the server's documented ranges and constraints."""
    if value is True:
        return {}
    if value is False:
        return None
    if not isinstance(value, dict):
        raise ValueError(
            f"{_LABEL} module: 'turn_detection' must be a boolean or an object"
        )
    unknown = sorted(set(value) - set(_TURN_DEFAULTS))
    if unknown:
        raise ValueError(
            f"{_LABEL} module: unknown 'turn_detection' key(s) {unknown}; "
            f"expected {sorted(_TURN_DEFAULTS)}"
        )
    for key in _TURN_THRESHOLDS:
        if key in value and (not _is_number(value[key]) or not 0 < value[key] < 1):
            raise ValueError(
                f"{_LABEL} module: turn_detection.{key} must be a number in (0, 1)"
            )
    for key in _TURN_DURATIONS:
        if key in value and (not _is_int(value[key]) or value[key] <= 0):
            raise ValueError(
                f"{_LABEL} module: turn_detection.{key} must be a positive integer"
            )
    merged = {**_TURN_DEFAULTS, **value}
    if not (
        merged["start_threshold"]
        >= merged["eager_end_threshold"]
        > merged["end_threshold"]
    ):
        raise ValueError(
            f"{_LABEL} module: turn_detection needs start_threshold >= "
            f"eager_end_threshold > end_threshold (server defaults fill omitted keys: "
            f"0.5 / 0.5 / 0.33)"
        )
    if merged["end_timeout_ms"] < merged["min_silence_ms"]:
        raise ValueError(
            f"{_LABEL} module: turn_detection needs end_timeout_ms >= min_silence_ms "
            f"(server defaults fill omitted keys: 3000 / 500)"
        )
    return dict(value)


def _parse_event(raw: str | bytes) -> dict[str, Any]:
    try:
        event = json.loads(raw)
    except ValueError:
        log.warning("%s: ignoring a non-JSON server message", _LABEL)
        return {}
    return event if isinstance(event, dict) else {}


async def _cancel(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


class VoxtralRealtimeModule(ASRModule):
    """ASR module backed by Mistral's Voxtral 3 realtime transcription API."""

    SUPPORTED_SAMPLE_RATES = frozenset({16000, 24000, 44100})  # probe-verified
    SUPPORTED_CHANNELS = frozenset({1})
    SUPPORTED_ENCODINGS = frozenset({"linear16"})
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1
    DEFAULT_ENCODING = "linear16"

    _BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

    def __init__(self, config: dict, *, connect: Connector | None = None) -> None:
        super().__init__(config)
        self._api_key = resolve_api_key(config, _LABEL)

        self._model = config.get("model", DEFAULT_MODEL)
        if not isinstance(self._model, str) or not self._model:
            raise ValueError(f"{_LABEL} module: 'model' must be a non-empty string")
        self._url = config.get("url", DEFAULT_URL)
        if not isinstance(self._url, str) or not self._url.startswith(
            ("ws://", "wss://")
        ):
            raise ValueError(
                f"{_LABEL} module: 'url' must be a ws:// or wss:// URL string"
            )
        self._language = config.get("language")
        if self._language is not None and (
            not isinstance(self._language, str)
            or not _LANGUAGE_RE.match(self._language)
        ):
            raise ValueError(
                f"{_LABEL} module: 'language' must be null or a two-letter "
                f"ISO 639-1 code (e.g. 'en')"
            )
        self._delay_ms = config.get("streaming_delay_ms", 480)
        if not _is_int(self._delay_ms) or not (
            80 <= self._delay_ms <= 1200 or self._delay_ms == 2400
        ):
            raise ValueError(
                f"{_LABEL} module: 'streaming_delay_ms' must be an integer in "
                f"80-1200, or 2400"
            )
        context_bias = config.get("context_bias", [])
        if not isinstance(context_bias, list) or not all(
            isinstance(p, str) and p for p in context_bias
        ):
            raise ValueError(
                f"{_LABEL} module: 'context_bias' must be a list of non-empty strings"
            )
        self._context_bias: list[str] = list(context_bias)
        self._turn_detection = _parse_turn_detection(config.get("turn_detection", True))

        final_margin_ms = config.get("final_margin_ms", 100)
        if not _is_int(final_margin_ms) or final_margin_ms < 0:
            raise ValueError(
                f"{_LABEL} module: 'final_margin_ms' must be a non-negative integer"
            )
        self._final_margin_s = final_margin_ms / 1000
        self._silence_s = _positive_number(config, "finalize_after_silence_s", 2.0)
        self._stop_timeout_s = _positive_number(config, "stop_timeout_s", 5.0)
        self._idle_close_s = _positive_number(config, "idle_close_s", 20.0)
        if self._idle_close_s >= _SERVER_IDLE_KILL_S:
            raise ValueError(
                f"{_LABEL} module: 'idle_close_s' must be below "
                f"{_SERVER_IDLE_KILL_S:.0f} (the server's idle timeout)"
            )

        self._connect: Connector = connect or _websockets_connect

        self._stop_event = asyncio.Event()
        self._stopped = asyncio.Event()
        self._running = False
        self._connected = False
        self._handshake_ok = False
        self._on_utterance: UtteranceCallback | None = None
        self._on_connected: ConnectedCallback | None = None

        # Utterance assembly. The lock serializes every emission and every change
        # to the accumulator, so an interim and a timer-driven final never interleave.
        self._accum = ""
        self._emit_lock = asyncio.Lock()
        self._timers: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ lifecycle

    async def start(
        self,
        audio_queue: asyncio.Queue[bytes],
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
        session_update = self._session_update(audio_format)
        url = f"{self._url}?{urlencode({'model': self._model})}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        attempt = 0
        pending: bytes | None = None
        try:
            while not self._stop_event.is_set():
                try:
                    end = await self._run_session(
                        url, headers, session_update, audio_queue, pending
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    pending = None
                    await self._finalize()
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
                attempt = 0
                if end == "stop":
                    break
                # Parked after an idle gap: stay "connected" and reopen a session
                # with the next chunk, which goes out first once it is ready.
                pending = await self._next_chunk(audio_queue)
                if pending is None:
                    break
        finally:
            await self._cancel_timers()
            self._set_connected(False)
            self._running = False
            self._stopped.set()

    async def stop(self) -> None:
        """End the session (flushing its final) and return once it has ended.

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

    # -------------------------------------------------------------------- session

    def _session_update(self, audio_format: AudioFormat) -> dict[str, Any]:
        session: dict[str, Any] = {
            "audio_format": {
                "encoding": "pcm_s16le",
                "sample_rate": audio_format.sample_rate,
            },
            "target_streaming_delay_ms": self._delay_ms,
        }
        if self._language is not None:
            session["language"] = self._language
        if self._context_bias:
            session["context_bias"] = list(self._context_bias)
        if self._turn_detection is not None:
            session["turn_detection"] = dict(self._turn_detection)
        return {"type": "session.update", "session": session}

    async def _run_session(
        self,
        url: str,
        headers: dict[str, str],
        session_update: dict[str, Any],
        audio_queue: asyncio.Queue[bytes],
        pending: bytes | None,
    ) -> SessionEnd:
        """One server session: handshake, stream until stop/idle, then flush.

        Returns how it ended; raises on any connection or protocol failure.
        """
        self._handshake_ok = False
        async with self._connect(url, headers) as conn:
            created = _parse_event(
                await asyncio.wait_for(conn.recv(), _HANDSHAKE_TIMEOUT_S)
            )
            if created.get("type") != "session.created":
                raise ConnectionError(
                    f"expected session.created, got {created.get('type')!r}: {created}"
                )
            await conn.send(json.dumps(session_update))
            self._handshake_ok = True
            self._set_connected(True)

            receiver = asyncio.create_task(self._receive(conn))
            sender = asyncio.create_task(self._send_audio(conn, audio_queue, pending))
            try:
                await asyncio.wait(
                    {receiver, sender}, return_when=asyncio.FIRST_COMPLETED
                )
                if not sender.done():
                    # The server ended or broke the session before we asked it to.
                    exc = receiver.exception()
                    raise exc or ConnectionError("server ended the session")
                end = sender.result()  # re-raises a failed send
                await conn.send(json.dumps({"type": "input_audio.end"}))
                try:
                    await asyncio.wait_for(receiver, self._stop_timeout_s)
                except asyncio.TimeoutError:
                    log.warning(
                        "%s: no transcription.done within %.1fs",
                        _LABEL,
                        self._stop_timeout_s,
                    )
                except Exception as exc:
                    log.debug("%s: session closed while flushing: %s", _LABEL, exc)
                await self._finalize()
                return end
            finally:
                await _cancel(receiver, sender)

    async def _send_audio(
        self,
        conn: RealtimeConnection,
        audio_queue: asyncio.Queue[bytes],
        pending: bytes | None,
    ) -> SessionEnd:
        if pending is not None:
            await self._append(conn, pending)
        while True:
            chunk = await self._next_chunk(audio_queue, timeout=self._idle_close_s)
            if self._stop_event.is_set():
                return "stop"
            if chunk is None:
                log.info(
                    "%s: no audio for %.0fs; parking the session",
                    _LABEL,
                    self._idle_close_s,
                )
                return "idle"
            await self._append(conn, chunk)

    @staticmethod
    async def _append(conn: RealtimeConnection, chunk: bytes) -> None:
        audio = base64.b64encode(chunk).decode("ascii")
        await conn.send(json.dumps({"type": "input_audio.append", "audio": audio}))

    async def _receive(self, conn: RealtimeConnection) -> None:
        """Apply server events until ``transcription.done``."""
        while True:
            event = _parse_event(await conn.recv())
            kind = event.get("type")
            if kind == "transcription.text.delta":
                await self._on_delta(event.get("text"))
            elif kind == "turn.end":
                self._arm("turn", self._final_margin_s, restart=False)
            elif kind == "transcription.done":
                return
            elif kind == "error":
                error = event.get("error") or {}
                log.warning(
                    "%s server error %s: %s",
                    _LABEL,
                    error.get("code") if isinstance(error, dict) else None,
                    error.get("message") if isinstance(error, dict) else error,
                )
            elif kind == "session.updated":
                log.debug("%s session: %s", _LABEL, event.get("session"))

    # --------------------------------------------------------- utterance assembly

    async def _on_delta(self, text: object) -> None:
        if not isinstance(text, str) or not text:
            return
        async with self._emit_lock:
            self._accum += text  # deltas carry their own spacing
            transcript = self._accum.strip()
            if transcript:
                await self._emit(transcript, is_final=False)
        self._arm("silence", self._silence_s, restart=True)

    def _arm(self, name: str, delay: float, *, restart: bool) -> None:
        current = self._timers.get(name)
        if current is not None:
            if not restart:
                return
            current.cancel()
        self._timers[name] = asyncio.create_task(self._fire(name, delay))

    async def _fire(self, name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        # Detach before finalizing: a timer that has fired commits to its final. A
        # delta that lands while it waits for the emit lock re-arms a fresh silence
        # timer instead of cancelling this one and postponing the final.
        if self._timers.get(name) is asyncio.current_task():
            del self._timers[name]
        await self._finalize()

    async def _finalize(self) -> None:
        """Emit the accumulated text as a final (if any) and reset."""
        async with self._emit_lock:
            current = asyncio.current_task()
            for task in self._timers.values():
                if task is not current:
                    task.cancel()
            self._timers.clear()
            transcript = self._accum.strip()
            self._accum = ""
            if transcript:
                await self._emit(transcript, is_final=True)

    async def _cancel_timers(self) -> None:
        timers = list(self._timers.values())
        self._timers.clear()
        await _cancel(*timers)

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
        self, audio_queue: asyncio.Queue[bytes], timeout: float | None = None
    ) -> bytes | None:
        """The next chunk, or ``None`` on stop or after *timeout* seconds."""
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

    async def _drain_for(self, audio_queue: asyncio.Queue[bytes], delay: float) -> None:
        """Discard audio for *delay* seconds (or until stop) while reconnecting."""
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
