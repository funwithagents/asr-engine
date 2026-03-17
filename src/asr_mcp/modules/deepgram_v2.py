from __future__ import annotations

import asyncio
import logging

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v2.types.listen_v2turn_info import ListenV2TurnInfo

from asr_mcp.modules.base import ASRModule, ASRResult, ResultCallback

log = logging.getLogger(__name__)


class DeepgramV2Module(ASRModule):
    """ASR module backed by the Deepgram Listen v2 (Flux) WebSocket API.

    Uses the Flux model with built-in contextual turn detection.
    EndOfTurn events signal is_final=True; all other TurnInfo events are interim.

    Supported models: flux-general-en (and other flux-* variants).
    Not suitable for non-English or for models outside the Flux family.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        if not config.get("api_key"):
            raise ValueError("deepgram_v2 module requires 'api_key' in config")

        self._api_key: str = config["api_key"]
        self._model: str = config.get("model", "flux-general-en")
        self._eot_threshold: float = config.get("eot_threshold", 0.7)
        self._eot_timeout_ms: int = config.get("eot_timeout_ms", 5000)

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
                async with client.listen.v2.connect(
                    model=self._model,
                    encoding="linear16",
                    sample_rate="16000",
                    eot_threshold=str(self._eot_threshold),
                    eot_timeout_ms=str(self._eot_timeout_ms),
                ) as conn:
                    attempt = 0

                    async def on_message(msg: object) -> None:
                        if not isinstance(msg, ListenV2TurnInfo):
                            return
                        transcript = msg.transcript
                        if not transcript:
                            return
                        is_final = msg.event == "EndOfTurn"
                        await on_result(
                            ASRResult(
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

            except Exception as e:
                if self._stop_event.is_set():
                    break
                log.error(
                    "Deepgram v2 connection error (attempt %d): %s", attempt + 1, e
                )
                delay = min(2**attempt, 8)
                attempt += 1
                await self._drain_queue_for(audio_queue, delay)

    async def _audio_loop(
        self,
        conn: object,
        audio_queue: asyncio.Queue[bytes],
    ) -> None:
        """Send audio chunks; send KeepAlive JSON on 5-second silence."""
        while not self._stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=5.0)
                await conn.send_media(chunk)  # type: ignore[union-attr]
            except asyncio.TimeoutError:
                # No audio for 5 s — keep the connection alive.
                # The v2 SDK has no public send_keep_alive(); use the internal
                # _send() which accepts a dict and serialises it to JSON.
                await conn._send({"type": "KeepAlive"})  # type: ignore[union-attr]

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
