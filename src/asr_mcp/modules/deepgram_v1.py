from __future__ import annotations

import asyncio
import logging

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types.listen_v1results import ListenV1Results

from asr_mcp.modules.base import ASRModule, ASRResult, ConnectedCallback, ResultCallback

log = logging.getLogger(__name__)


class DeepgramV1Module(ASRModule):
    """ASR module backed by the Deepgram Listen v1 WebSocket API.

    Uses Nova-3 (or any v1-compatible model) with is_final-based segment
    detection. Supports language selection, punctuation, and interim results.

    is_final=True (Deepgram segment finality) maps to ASRResult.is_final=True.
    This fires whenever Deepgram commits a transcript chunk — more reliable than
    speech_final, which depends on endpointing configuration and model support.

    Recommended models: nova-3, nova-2, enhanced, base.
    Use deepgram_v2 for Flux / built-in turn detection.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        if not config.get("api_key"):
            raise ValueError("deepgram_v1 module requires 'api_key' in config")

        self._api_key: str = config["api_key"]
        self._model: str = config.get("model", "nova-3")
        self._language: str = config.get("language", "multi")
        self._punctuate: bool = config.get("punctuate", True)
        self._interim_results: bool = config.get("interim_results", True)

        self._stop_event = asyncio.Event()

    async def start(
        self,
        audio_queue: asyncio.Queue[bytes],
        on_result: ResultCallback,
        on_connected: ConnectedCallback | None = None,
    ) -> None:
        self._stop_event.clear()
        client = AsyncDeepgramClient(api_key=self._api_key)
        attempt = 0

        while not self._stop_event.is_set():
            try:
                async with client.listen.v1.connect(
                    model=self._model,
                    encoding="linear16",
                    sample_rate="16000",
                    channels="1",
                    language=self._language,
                    punctuate=str(self._punctuate).lower(),
                    interim_results=str(self._interim_results).lower(),
                ) as conn:
                    attempt = 0
                    if on_connected:
                        on_connected(True)
                    try:
                        async def on_message(msg: object) -> None:
                            if not isinstance(msg, ListenV1Results):
                                return
                            alternatives = msg.channel.alternatives
                            if not alternatives:
                                return
                            transcript = alternatives[0].transcript
                            if not transcript:
                                return
                            confidence = alternatives[0].confidence
                            # is_final=True means Deepgram has committed this segment
                            # and its transcript won't change. speech_final is not used
                            # because it depends on endpointing configuration and is not
                            # reliably set by all models (notably nova-3).
                            is_final = bool(msg.is_final)
                            await on_result(
                                ASRResult(
                                    transcript=transcript,
                                    is_final=is_final,
                                    confidence=confidence,
                                )
                            )

                        async def on_error(error: object) -> None:
                            log.error("Deepgram v1 error: %s", error)

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
                        if on_connected:
                            on_connected(False)

            except Exception as e:
                if self._stop_event.is_set():
                    break
                log.error(
                    "Deepgram v1 connection error (attempt %d): %s", attempt + 1, e
                )
                delay = min(2**attempt, 8)
                attempt += 1
                await self._drain_queue_for(audio_queue, delay)

    _KEEPALIVE_TIMEOUT: float = 5.0

    async def _audio_loop(
        self,
        conn: object,
        audio_queue: asyncio.Queue[bytes],
    ) -> None:
        """Send audio chunks; send KeepAlive on silence. Exits immediately on stop()."""
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
                    await conn.send_media(get_task.result())  # type: ignore[union-attr]
                else:
                    # No audio for _KEEPALIVE_TIMEOUT seconds — keep the connection alive.
                    get_task.cancel()
                    try:
                        await get_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    # v1 has a public send_keep_alive() method.
                    await conn.send_keep_alive()  # type: ignore[union-attr]
        finally:
            stop_task.cancel()
            try:
                await stop_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _drain_queue_for(
        self,
        audio_queue: asyncio.Queue[bytes],
        duration: float,
    ) -> None:
        """Drain audio_queue for *duration* seconds to prevent unbounded growth."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + duration
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(
                    audio_queue.get(), timeout=min(remaining, 0.1)
                )
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop_event.set()
