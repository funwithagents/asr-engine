"""UI-agnostic controller for the Gradio ASR demo (see specs/gradio-demo.md).

``DemoController`` owns a dedicated asyncio event loop on a background thread and
drives an in-process ``ASREngine`` directly (no MCP, no ``AsrTools``). Gradio
handlers call its synchronous methods; engine callbacks fire on the loop thread
and update thread-safe rolling state that ``state()`` snapshots for the UI.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Literal

from asr_engine.audio import AudioCapture
from asr_engine.config import ASREngineConfig
from asr_engine.engine import ASREngine
from asr_engine.modules import REGISTRY
from asr_engine.modules.base import SpeechUtterance
from asr_engine.segmenter import SpeechSegment

log = logging.getLogger(__name__)

# Internal lifecycle phases the controller tracks directly.
Phase = Literal["stopped", "running", "listening"]
# What the UI displays: "dictating" is derived from engine.dictating while running.
DisplayPhase = Literal["stopped", "running", "dictating", "listening"]

_LOG_MAXLEN = 200
_LIFECYCLE_TIMEOUT_S = 30.0


def format_utterance(utterance: SpeechUtterance) -> str:
    """One display line for an utterance: interim/final marker + confidence."""
    marker = "FINAL  " if utterance.is_final else "INTERIM"
    text = utterance.transcript or "…"
    if utterance.confidence is not None:
        return f"[{marker}] {text} ({utterance.confidence:.2f})"
    return f"[{marker}] {text}"


def format_segment(segment: SpeechSegment) -> str:
    """One display line for a segment: end_reason (or 'open' while growing)."""
    reason = segment.end_reason or "open"
    return f"[{reason}] {segment.transcript}"


@dataclass(frozen=True)
class ControllerState:
    """Immutable snapshot the UI renders. Built by ``DemoController.state()``."""

    phase: DisplayPhase
    connected: bool
    segmentation_mode: str
    trigger_words: str  # comma-separated, for the UI textbox
    initial_silence_timeout_s: float
    end_of_speech_timeout_s: float
    device: str | None
    module_type: str
    utterance_log: list[str]
    segment_log: list[str]
    last_listen: str
    message: str

    dictating: bool

    # Derived widget enablement (single source of truth for the UI).
    can_start: bool
    can_stop: bool
    can_listen: bool
    config_enabled: bool
    can_dictate: bool


class DemoController:
    """Owns the loop thread, one live ``ASREngine``, and rolling event state.

    Config-changing methods (``set_device`` / ``set_module``) are only valid
    while stopped and force the engine to be rebuilt from the pending config on
    the next ``start`` / ``listen``. ``listen`` runs a single-shot capture in the
    background (so the UI stays responsive) and can be cancelled with ``stop``.
    Usable as a context manager so the loop thread is torn down cleanly.
    """

    def __init__(self, base_config: ASREngineConfig) -> None:
        # Own a private copy so UI edits never mutate the caller's config.
        self._config = copy.deepcopy(base_config)
        self._engine: ASREngine | None = None
        self._phase: Phase = "stopped"
        self._listen_task: asyncio.Task | None = None

        self._lock = threading.Lock()
        self._utterance_log: deque[str] = deque(maxlen=_LOG_MAXLEN)
        self._segment_log: deque[str] = deque(maxlen=_LOG_MAXLEN)
        self._last_listen: SpeechSegment | None = None
        self._message: str = ""

        # Dedicated event loop on a daemon thread; every engine call is marshaled
        # onto it so the engine lives on exactly one loop ("asyncio throughout").
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="asr-demo-loop", daemon=True
        )
        self._thread.start()

    # -- context manager / teardown ----------------------------------------

    def __enter__(self) -> DemoController:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Cancel any capture and shut the loop thread down."""
        try:
            if self._phase == "listening" and self._listen_task is not None:
                # Cancel and await on the loop so the task (and engine.listen's
                # own cleanup) finishes before we stop the loop.
                self._run(self._cancel_listen(), timeout=_LIFECYCLE_TIMEOUT_S)
            elif self._phase == "running" and self._engine is not None:
                self._run(self._engine.stop(), timeout=_LIFECYCLE_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — best-effort teardown
            log.exception("error stopping engine during close()")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=_LIFECYCLE_TIMEOUT_S)
        self._loop.close()

    async def _cancel_listen(self) -> None:
        """Cancel the in-progress listen task and await its completion (on loop)."""
        task = self._listen_task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # -- loop marshalling --------------------------------------------------

    def _run(self, coro, *, timeout: float | None):
        """Run *coro* on the loop thread and block until it completes."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # -- discovery ---------------------------------------------------------

    def available_devices(self) -> list[str]:
        try:
            return AudioCapture.list_devices()
        except Exception:  # noqa: BLE001 — device query is best-effort in a demo
            log.exception("could not list audio input devices")
            return []

    def available_modules(self) -> list[str]:
        return sorted(REGISTRY)

    # -- configuration (only while stopped) --------------------------------

    def set_device(self, device: str | None) -> None:
        self._require_stopped("change the input device")
        self._config.audio.device = device or None
        self._engine = None  # rebuild from the new config on next start

    def set_module(self, module_type: str) -> None:
        self._require_stopped("change the ASR module")
        self._config.module.type = module_type
        self._engine = None

    def _require_stopped(self, action: str) -> None:
        if self._phase != "stopped":
            raise RuntimeError(f"Stop the engine before trying to {action}.")

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._phase != "stopped":
            return  # idempotent from the UI's perspective
        try:
            engine = self._ensure_engine()
            self._run(engine.start(), timeout=_LIFECYCLE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — surface to the UI, don't crash
            self._set_message(f"Start failed: {exc}")
            return
        self._phase = "running"
        self._set_message("Running (always-on).")

    def stop(self) -> None:
        """Stop always-on capture, or cancel an in-progress ``listen``."""
        if self._phase == "running" and self._engine is not None:
            try:
                self._run(self._engine.stop(), timeout=_LIFECYCLE_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001
                self._set_message(f"Stop failed: {exc}")
                return
            self._phase = "stopped"
            self._set_message("Stopped.")
        elif self._phase == "listening" and self._listen_task is not None:
            self._loop.call_soon_threadsafe(self._listen_task.cancel)
            self._set_message("Stopping listen…")

    def listen(self, mode: str | None = None) -> None:
        """Start a single-shot capture in the background (cancelable via ``stop``).

        Non-blocking: the segment result and end-of-listen are reflected in
        ``state()`` once the capture completes (or is cancelled).
        """
        if self._phase != "stopped":
            self._set_message("Stop the always-on engine before using listen.")
            return
        try:
            self._ensure_engine()
        except Exception as exc:  # noqa: BLE001
            self._set_message(f"Listen failed: {exc}")
            return
        self._phase = "listening"
        self._set_message("Listening… (Stop to cancel)")
        self._loop.call_soon_threadsafe(self._spawn_listen, mode)

    def _spawn_listen(self, mode: str | None) -> None:
        """Create the listen task (runs on the loop thread)."""
        self._listen_task = self._loop.create_task(self._listen_runner(mode))

    async def _listen_runner(self, mode: str | None) -> None:
        engine = self._engine
        assert engine is not None  # set by listen() before scheduling this
        try:
            segment = await engine.listen(mode, on_update=self._on_segment)
        except asyncio.CancelledError:
            self._set_message("Listen cancelled.")
            raise
        except Exception as exc:  # noqa: BLE001
            self._set_message(f"Listen failed: {exc}")
        else:
            with self._lock:
                self._last_listen = segment
            self._set_message(f"Heard: {segment.transcript!r}")
        finally:
            self._phase = "stopped"
            self._listen_task = None

    # -- dictation ---------------------------------------------------------

    def start_dictation(
        self, mode: str | None = None, end_on_final_segment: bool = False
    ) -> None:
        """Start a dictation session on the running engine (aggregating mode)."""
        if self._phase != "running":
            self._set_message("Start the engine before dictation.")
            return
        try:
            self._run(
                self._apply_start_dictation(mode, end_on_final_segment),
                timeout=_LIFECYCLE_TIMEOUT_S,
            )
            self._set_message(f"Dictation started ({mode or 'default'}).")
        except ValueError as exc:
            self._set_message(str(exc))

    async def _apply_start_dictation(
        self, mode: str | None, end_on_final_segment: bool
    ) -> None:
        assert self._engine is not None
        await self._engine.start_dictation(
            end_on_final_segment=end_on_final_segment, segmentation_mode=mode
        )

    def stop_dictation(self) -> None:
        """End the active dictation; the engine keeps running."""
        if self._engine is None or not self._engine.dictating:
            self._set_message("No dictation in progress.")
            return
        try:
            self._run(self._apply_stop_dictation(), timeout=_LIFECYCLE_TIMEOUT_S)
            self._set_message("Dictation stopped.")
        except ValueError as exc:
            self._set_message(str(exc))

    async def _apply_stop_dictation(self) -> None:
        assert self._engine is not None
        await self._engine.stop_dictation()

    # -- segmentation params -----------------------------------------------

    def set_segmentation_params(
        self,
        trigger_words: str,
        initial_silence_timeout_s: float,
        end_of_speech_timeout_s: float,
    ) -> None:
        """Update trigger words (comma-separated) and timeouts; safe while running."""
        if self._phase == "listening":
            self._set_message("Cannot change segmentation params while listening.")
            return
        words = [w.strip() for w in trigger_words.split(",") if w.strip()]
        try:
            ist = float(initial_silence_timeout_s)
            eost = float(end_of_speech_timeout_s)
        except (TypeError, ValueError):
            self._set_message("Timeouts must be numbers.")
            return
        if ist <= 0 or eost <= 0:
            self._set_message("Timeouts must be greater than 0.")
            return
        try:
            if self._engine is not None:
                self._run(
                    self._apply_params(words, ist, eost), timeout=_LIFECYCLE_TIMEOUT_S
                )
        except ValueError as exc:
            self._set_message(str(exc))
            return
        seg = self._config.segmentation
        seg.trigger_words = words
        seg.initial_silence_timeout_s = ist
        seg.end_of_speech_timeout_s = eost
        self._set_message("Segmentation params updated.")

    async def _apply_params(self, words: list[str], ist: float, eost: float) -> None:
        assert self._engine is not None
        await self._engine.set_segmentation_params(
            trigger_words=words,
            initial_silence_timeout_s=ist,
            end_of_speech_timeout_s=eost,
        )

    # -- engine construction ----------------------------------------------

    def _ensure_engine(self) -> ASREngine:
        """Build the engine from the pending config if one isn't live yet."""
        if self._engine is None:
            self._engine = ASREngine(
                self._config,
                on_speech_utterance=self._on_utterance,
                on_speech_segment=self._on_segment,
            )
        return self._engine

    def clear_logs(self) -> None:
        """Empty the utterance and segment logs."""
        with self._lock:
            self._utterance_log.clear()
            self._segment_log.clear()
        self._set_message("Cleared utterances & segments.")

    # -- engine callbacks (run on the loop thread) -------------------------

    async def _on_utterance(self, utterance: SpeechUtterance) -> None:
        with self._lock:
            self._utterance_log.append(format_utterance(utterance))

    async def _on_segment(self, segment: SpeechSegment) -> None:
        with self._lock:
            self._segment_log.append(format_segment(segment))

    # -- state snapshot ----------------------------------------------------

    def _set_message(self, message: str) -> None:
        with self._lock:
            self._message = message

    def _current_mode(self) -> str:
        if self._engine is not None:
            return self._engine.segmentation_mode
        return "utterance"  # the always-on mode at rest

    def state(self) -> ControllerState:
        connected = self._engine.status()["connected"] if self._engine else False
        dictating = bool(self._engine and self._engine.dictating)
        with self._lock:
            last_listen = self._last_listen
            message = self._message
            utterance_log = list(self._utterance_log)
            segment_log = list(self._segment_log)
        phase = self._phase
        stopped = phase == "stopped"
        # A running engine with an active dictation displays as "dictating"; the
        # engine flips dictating off on its own when an end-on-final session ends.
        display_phase = "dictating" if (phase == "running" and dictating) else phase
        seg = self._config.segmentation
        return ControllerState(
            phase=display_phase,
            connected=connected,
            segmentation_mode=self._current_mode(),
            trigger_words=", ".join(seg.trigger_words),
            initial_silence_timeout_s=seg.initial_silence_timeout_s,
            end_of_speech_timeout_s=seg.end_of_speech_timeout_s,
            device=self._config.audio.device,
            module_type=self._config.module.type,
            utterance_log=utterance_log,
            segment_log=segment_log,
            last_listen=format_segment(last_listen) if last_listen else "",
            message=message,
            dictating=dictating,
            can_start=stopped,
            can_stop=phase in ("running", "listening"),
            can_listen=stopped,
            config_enabled=stopped,
            can_dictate=phase == "running",
        )
