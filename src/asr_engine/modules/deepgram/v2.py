from __future__ import annotations

import asyncio
import logging

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v2.types.listen_v2turn_info import ListenV2TurnInfo

from asr_engine.audio import (
    DEFAULT_AUDIO_FORMAT,
    AudioFormat,
    EndOfSpeech,
    _silence,
)
from asr_engine.modules.base import (
    ASRModule,
    AudioQueue,
    ConnectedCallback,
    SpeechUtterance,
    UtteranceCallback,
    resolve_api_key,
)

log = logging.getLogger(__name__)


class DeepgramV2Module(ASRModule):
    """ASR module backed by the Deepgram Listen v2 (Flux) WebSocket API.

    Uses the Flux model with built-in contextual turn detection.
    EndOfTurn events signal is_final=True; all other TurnInfo events are interim.

    Supported models: flux-general-en (and other flux-* variants).
    Not suitable for non-English or for models outside the Flux family.
    """

    # Same format support as deepgram_v1 — Flux accepts the raw encoding +
    # sample_rate parameters the same way (see specs/modules/deepgram.md).
    SUPPORTED_SAMPLE_RATES = frozenset({8000, 16000, 24000, 44100, 48000})
    SUPPORTED_CHANNELS = frozenset({1})
    SUPPORTED_ENCODINGS = frozenset({"linear16", "mulaw"})
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1
    DEFAULT_ENCODING = "linear16"
    VAD_GATING = "supported"

    # Silence sent past eot_timeout_ms when a gated utterance leaves a turn open,
    # so Flux's timeout path is certain to be reached.
    _FLUSH_MARGIN_S = 0.5

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._api_key: str = resolve_api_key(config, "deepgram_v2")
        self._model: str = config.get("model", "flux-general-en")
        self._eot_threshold: float = config.get("eot_threshold", 0.7)
        self._eot_timeout_ms: int = config.get("eot_timeout_ms", 2000)

        self._stop_event = asyncio.Event()

        # VAD gating (specs/vad.md). Flux has no flush message: a turn ends only
        # on silence it hears. These track whether the gate closed on an open turn.
        self._conn: object | None = None
        self._flush_silence = b""
        self._turn_open = False  # a transcript arrived since the last EndOfTurn
        self._gated = False  # a marker arrived and no audio has followed it
        self._flushed = False  # the silence for this gap has been sent

    async def start(
        self,
        audio_queue: AudioQueue,
        on_utterance: UtteranceCallback,
        on_connected: ConnectedCallback | None = None,
        *,
        audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
    ) -> None:
        self._stop_event.clear()
        self._flush_silence = _silence(
            audio_format, self._eot_timeout_ms / 1000 + self._FLUSH_MARGIN_S
        )
        client = AsyncDeepgramClient(api_key=self._api_key)
        attempt = 0

        while not self._stop_event.is_set():
            try:
                async with client.listen.v2.connect(
                    model=self._model,
                    encoding=audio_format.encoding,
                    sample_rate=str(audio_format.sample_rate),
                    eot_threshold=str(self._eot_threshold),
                    eot_timeout_ms=str(self._eot_timeout_ms),
                ) as conn:
                    attempt = 0
                    self._conn = conn
                    self._turn_open = self._gated = self._flushed = False
                    if on_connected:
                        on_connected(True)
                    try:

                        async def on_message(msg: object) -> None:
                            if not isinstance(msg, ListenV2TurnInfo):
                                return
                            is_final = msg.event == "EndOfTurn"
                            transcript = msg.transcript
                            if is_final:
                                self._turn_open = False
                            elif transcript:
                                self._turn_open = True
                                # The gate may have closed before Flux reported
                                # this turn: flush now that it is known to be open.
                                await self._flush_open_turn()
                            if not transcript:
                                return
                            await on_utterance(
                                SpeechUtterance(
                                    transcript=transcript,
                                    is_final=is_final,
                                    confidence=msg.end_of_turn_confidence,
                                )
                            )

                        async def on_error(error: object) -> None:
                            log.error("Deepgram v2 error: %s", error)

                        conn.on(EventType.MESSAGE, on_message)
                        conn.on(EventType.ERROR, on_error)

                        listen_task = asyncio.create_task(conn.start_listening())
                        audio_task = asyncio.create_task(
                            self._audio_loop(conn, audio_queue)
                        )

                        done, pending = await asyncio.wait(
                            {listen_task, audio_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in pending:
                            task.cancel()
                            try:
                                await task
                            except (asyncio.CancelledError, Exception):
                                pass

                        for task in done:
                            exc = task.exception()
                            if exc is not None:
                                raise exc
                    finally:
                        self._conn = None
                        if on_connected:
                            on_connected(False)

            except Exception as e:
                if self._stop_event.is_set():
                    break
                log.error(
                    "Deepgram v2 connection error (attempt %d): %s", attempt + 1, e
                )
                delay = min(2**attempt, 8)
                attempt += 1
                await self._drain_queue_for(audio_queue, delay)

    _KEEPALIVE_TIMEOUT: float = 5.0

    async def _audio_loop(
        self,
        conn: object,
        audio_queue: AudioQueue,
    ) -> None:
        """Send audio chunks; flush on an end-of-speech marker; send KeepAlive
        JSON on silence. Exits immediately on stop()."""
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            while True:
                get_task = asyncio.create_task(audio_queue.get())
                done, _ = await asyncio.wait(
                    {get_task, stop_task},
                    timeout=self._KEEPALIVE_TIMEOUT,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done or self._stop_event.is_set():
                    get_task.cancel()
                    try:
                        await get_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    break
                if get_task in done:
                    item = get_task.result()
                    if isinstance(item, EndOfSpeech):
                        await self.finalize()
                    else:
                        self._gated = self._flushed = False
                        await conn.send_media(item)  # type: ignore[union-attr]
                else:
                    # No audio for _KEEPALIVE_TIMEOUT seconds — keep the connection alive.
                    # The v2 SDK has no public send_keep_alive(); use the internal
                    # _send() which accepts a dict and serialises it to JSON.
                    get_task.cancel()
                    try:
                        await get_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    await conn._send({"type": "KeepAlive"})  # type: ignore[union-attr]
        finally:
            stop_task.cancel()
            try:
                await stop_task
            except (asyncio.CancelledError, Exception):
                pass

    async def finalize(self) -> None:
        """End of a VAD-gated utterance: make sure Flux ends the turn.

        No turn open — Flux already ended it on the silence the gate let through
        (the recommended longer hangover): nothing to do, nothing billed. Turn
        open: send one burst of silence covering ``eot_timeout_ms`` plus a margin,
        so the turn ends on Flux's timeout path at the latest (measured: a burst
        sent faster than real time ends the turn in about half a second). See
        specs/vad.md "Per-module finalize()".
        """
        self._gated = True
        await self._flush_open_turn()

    async def _flush_open_turn(self) -> None:
        conn = self._conn
        if conn is None or not (self._gated and self._turn_open) or self._flushed:
            return
        self._flushed = True
        await conn.send_media(self._flush_silence)  # type: ignore[attr-defined]

    async def _drain_queue_for(
        self,
        audio_queue: AudioQueue,
        duration: float,
    ) -> None:
        """Drain audio_queue for *duration* seconds to prevent unbounded growth.

        End-of-speech markers are discarded with the audio they follow."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + duration
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(audio_queue.get(), timeout=min(remaining, 0.1))
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop_event.set()
