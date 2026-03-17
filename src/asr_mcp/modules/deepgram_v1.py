from __future__ import annotations

import asyncio
import logging

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types.listen_v1results import ListenV1Results

from asr_mcp.modules.base import ASRModule, ASRResult, ResultCallback

log = logging.getLogger(__name__)


class DeepgramV1Module(ASRModule):
    """ASR module backed by the Deepgram Listen v1 WebSocket API.

    Uses Nova-3 (or any v1-compatible model) with speech_final-based utterance
    detection. Supports language selection, punctuation, and interim results.

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
                        # speech_final=True means end of utterance (speaker paused).
                        # is_final=True means the segment transcript won't change,
                        # but the utterance may continue.
                        is_final = bool(msg.speech_final)
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

            except Exception as e:
                if self._stop_event.is_set():
                    break
                log.error(
                    "Deepgram v1 connection error (attempt %d): %s", attempt + 1, e
                )
                delay = min(2**attempt, 8)
                attempt += 1
                await self._drain_queue_for(audio_queue, delay)

    async def _audio_loop(
        self,
        conn: object,
        audio_queue: asyncio.Queue[bytes],
    ) -> None:
        """Send audio chunks; send KeepAlive on 5-second silence."""
        while not self._stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=5.0)
                await conn.send_media(chunk)  # type: ignore[union-attr]
            except asyncio.TimeoutError:
                # v1 has a public send_keep_alive() method.
                await conn.send_keep_alive()  # type: ignore[union-attr]

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
