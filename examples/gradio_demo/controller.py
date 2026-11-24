"""UI-agnostic controller for the Gradio ASR demo (see specs/gradio-demo.md).

``DemoController`` owns a dedicated asyncio event loop on a background thread and
runs one listening session at a time on an in-process ``ASREngine`` (no MCP, no
``AsrTools``). A session is built from the app's **saved** config when it starts
(Start, or a single-shot Listen) and released when it stops; while it runs the
config is locked (``locked()``), so the Setup panel cannot change it under the
session. Gradio handlers call the synchronous methods; engine callbacks fire on
the loop thread and update thread-safe rolling state that ``state()`` snapshots.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Literal

from app_setup.app import AppModel

from asr_engine.config import ASREngineConfig, ConfigError, VadConfig
from asr_engine.engine import ASREngine
from asr_engine.modules.base import SpeechUtterance
from asr_engine.segmenter import SpeechSegment
from asr_engine.vad.gate import VadState

log = logging.getLogger(__name__)

# The section of the app's config this demo runs: the engine block.
SECTION = "asr"

# Internal lifecycle phases the controller tracks directly.
Phase = Literal["stopped", "starting", "running", "stopping", "listening"]
# What the UI displays: "dictating" is derived from engine.dictating while running.
DisplayPhase = Literal[
    "stopped", "starting", "running", "stopping", "dictating", "listening"
]

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


def format_vad_config(vad: VadConfig | None) -> str:
    """The configured VAD gate, in one line (or why there is none)."""
    if vad is None:
        return "off (no voice activity detection in the config): all audio is sent"
    return (
        f"{vad.type} · threshold {vad.threshold:g} · opens after "
        f"{vad.min_speech_duration_ms} ms of speech · closes after "
        f"{vad.min_silence_duration_ms} ms of silence · "
        f"{vad.speech_pad_ms} ms pre-roll"
    )


def format_voice_activity(state: VadState | None, capturing: bool) -> str:
    """What the gate is doing right now."""
    if state is None:
        return "no VAD"
    if not capturing:
        return "idle (not capturing)"
    if state.failed_open:
        return "FAILED OPEN: the VAD raised, all audio is sent"
    if state.speaking:
        return "SPEECH: audio is sent to the module"
    return "silence: audio is held back"


@dataclass(frozen=True)
class ControllerState:
    """Immutable snapshot the UI renders. Built by ``DemoController.state()``."""

    phase: DisplayPhase
    connected: bool
    segmentation_mode: str
    module_type: str  # the session's module, else the saved config's
    vad: str  # the configured gate, one line
    voice_activity: str  # what the gate is doing right now
    utterance_log: list[str]
    segment_log: list[str]
    last_listen: str
    message: str

    dictating: bool

    # Derived widget enablement (single source of truth for the UI).
    can_start: bool
    can_stop: bool
    can_listen: bool
    can_dictate: bool


def _blocked(blockers: list[str]) -> str:
    """The message of a start refused for *blockers*."""
    return "The saved config cannot run yet:\n" + "\n".join(
        f"- {reason}" for reason in blockers
    )


class DemoController:
    """Owns the loop thread, at most one running session, and rolling event state.

    ``start`` and ``listen`` read the app's saved config: with blockers (its
    problems, what the machine is missing) they say why instead of starting;
    else they build an ``ASREngine`` from its ``asr`` section. ``stop`` stops
    the session and releases the engine, so the next start reads the saved
    config again. ``locked()`` is true from a start until the engine is
    released. Usable as a context manager so the loop thread is torn down
    cleanly.
    """

    def __init__(self) -> None:
        self._engine: ASREngine | None = None
        self._config: ASREngineConfig | None = None  # the session's
        self._phase: Phase = "stopped"
        self._listen_task: asyncio.Task | None = None

        self._lock = threading.Lock()
        self._utterance_log: deque[str] = deque(maxlen=_LOG_MAXLEN)
        self._segment_log: deque[str] = deque(maxlen=_LOG_MAXLEN)
        self._last_listen: SpeechSegment | None = None
        self._message: str = ""
        # Whether the message is the saved config's blockers from a refused start:
        # `refresh_blockers` keeps it current instead of showing a stale reason.
        self._refused = False

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
        """Stop any session and shut the loop thread down."""
        try:
            if self._phase == "listening" and self._listen_task is not None:
                # Cancel and await on the loop so the task (and engine.listen's
                # own cleanup) finishes before we stop the loop.
                self._run(self._cancel_listen(), timeout=_LIFECYCLE_TIMEOUT_S)
            elif self._phase == "running" and self._engine is not None:
                self._run(self._engine.stop(), timeout=_LIFECYCLE_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — best-effort teardown
            log.exception("error stopping engine during close()")
        self._phase = "stopped"
        self._release()
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

    # -- the session's config ----------------------------------------------

    def locked(self) -> bool:
        """Whether a session uses the saved config: from a start until its
        engine is released. The Setup panel refuses to save meanwhile."""
        return self._phase != "stopped" or self._engine is not None

    def _build(self, model: AppModel) -> ASREngine | None:
        """The session's engine, from the saved config; None (with the reason
        as the message) when it cannot run yet."""
        saved = model.saved_config()
        blockers = model.blockers(saved)
        if blockers:
            self._set_message(_blocked(blockers), refused=True)
            return None
        section = model.used_config(saved)[SECTION]
        try:
            # Relative paths are relative to the app's folder, as app-setup's
            # checks read them (it passes the same folder as base_dir).
            config = ASREngineConfig.from_dict(section, base_dir=model.path)
            engine = ASREngine(
                config,
                on_speech_utterance=self._on_utterance,
                on_speech_segment=self._on_segment,
            )
        except (ConfigError, ImportError) as exc:
            self._set_message(f"The engine could not be built: {exc}")
            return None
        self._config = config
        self._engine = engine
        return engine

    def _release(self) -> None:
        """Drop the session's engine: the next start builds one from the saved
        config again, and the config is unlocked."""
        self._engine = None
        self._config = None

    # -- lifecycle ---------------------------------------------------------

    def start(self, model: AppModel) -> None:
        """Start always-on capture with the saved config."""
        if self._phase != "stopped":
            return  # idempotent from the UI's perspective
        # Starting until it runs: Start and Stop are both greyed meanwhile.
        self._phase = "starting"
        engine = self._build(model)
        if engine is None:
            self._phase = "stopped"
            return
        try:
            self._run(engine.start(), timeout=_LIFECYCLE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — surface to the UI, don't crash
            self._release()
            self._phase = "stopped"
            self._set_message(f"Start failed: {exc}")
            return
        self._phase = "running"
        self._set_message("Running (always-on).")

    def stop(self) -> None:
        """Stop always-on capture and release the engine, or cancel a ``listen``."""
        if self._phase == "running" and self._engine is not None:
            self._phase = "stopping"
            try:
                self._run(self._engine.stop(), timeout=_LIFECYCLE_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001
                self._phase = "running"
                self._set_message(f"Stop failed: {exc}")
                return
            self._phase = "stopped"
            self._release()
            self._set_message("Stopped.")
        elif self._phase == "listening" and self._listen_task is not None:
            # Message first: the loop thread may cancel the task and set "Listen
            # cancelled." before this thread runs again, and must not be overwritten.
            self._set_message("Stopping listen…")
            self._loop.call_soon_threadsafe(self._listen_task.cancel)

    def listen(self, model: AppModel, mode: str | None = None) -> None:
        """Start a single-shot capture with the saved config, in the background
        (cancelable via ``stop``).

        Non-blocking: the segment result and end-of-listen are reflected in
        ``state()`` once the capture completes (or is cancelled).
        """
        if self._phase != "stopped":
            self._set_message("Stop the always-on session before using listen.")
            return
        if self._build(model) is None:
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
            self._release()
            self._phase = "stopped"
            self._listen_task = None

    # -- dictation ---------------------------------------------------------

    def start_dictation(
        self, mode: str | None = None, end_on_final_segment: bool = False
    ) -> None:
        """Start a dictation session on the running engine (aggregating mode)."""
        if self._phase != "running":
            self._set_message("Start the session before dictation.")
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
        """End the active dictation; the session keeps running."""
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

    def _set_message(self, message: str, *, refused: bool = False) -> None:
        with self._lock:
            self._message = message
            self._refused = refused

    def refresh_blockers(self, model: AppModel) -> None:
        """When the message is the blockers of a refused start, make them the
        saved config's current ones: the page calls it when the Listen tab is
        selected, after a save, and when a key changes. Any other message is
        left alone."""
        with self._lock:
            if not self._refused:
                return
        blockers = model.blockers(model.saved_config())
        if blockers:
            self._set_message(_blocked(blockers), refused=True)
        else:
            self._set_message(
                "The saved config has nothing missing any more: Start can run it."
            )

    def _shown_config(self, model: AppModel) -> tuple[str, VadConfig | None]:
        """The module and the gate shown: the session's, else the saved config's."""
        if self._config is not None:
            return self._config.module.type, self._config.vad
        section = model.saved_config().get(SECTION)
        try:
            config = ASREngineConfig.from_dict(section)  # type: ignore[arg-type]
        except ConfigError:
            module = section.get("module") if isinstance(section, dict) else None
            module_type = module.get("type") if isinstance(module, dict) else None
            return str(module_type or "?"), None
        return config.module.type, config.vad

    def state(self, model: AppModel) -> ControllerState:
        engine = self._engine
        connected = engine.status()["connected"] if engine else False
        dictating = bool(engine and engine.dictating)
        module_type, vad = self._shown_config(model)
        vad_state = engine.vad_state if engine else None
        if engine is None and vad is not None:
            vad_state = VadState()  # configured, but no session yet
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
        return ControllerState(
            phase=display_phase,
            connected=connected,
            segmentation_mode=engine.segmentation_mode if engine else "utterance",
            module_type=module_type,
            vad=format_vad_config(vad),
            voice_activity=format_voice_activity(vad_state, capturing=not stopped),
            utterance_log=utterance_log,
            segment_log=segment_log,
            last_listen=format_segment(last_listen) if last_listen else "",
            message=message,
            dictating=dictating,
            can_start=stopped,
            can_stop=phase in ("running", "listening"),
            can_listen=stopped,
            can_dictate=phase == "running",
        )
