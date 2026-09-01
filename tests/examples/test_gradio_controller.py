"""Fast tests for the Gradio demo's UI-agnostic DemoController.

The controller owns a real background asyncio loop but drives an ``ASREngine``
that we replace with a ``FakeEngine`` (patched at
``examples.gradio_demo.controller.ASREngine``). So these exercise the
controller's own logic — rebuild-on-config-change, listen cancellation, param
validation, and phase/enablement transitions — without a real audio device,
network, or Gradio. We drive the public methods and assert on ``state()``
snapshots (never private fields), per specs/testing.md.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from typing import Callable, Iterator
from unittest.mock import patch

import pytest

from asr_engine.config import ASREngineConfig, ModuleConfig
from asr_engine.modules import REGISTRY
from examples.gradio_demo.controller import ControllerState, DemoController


class FakeEngine:
    """Stand-in for ASREngine: records calls, awaitable, controllable state.

    Its ``listen`` blocks until cancelled (so the controller sits in the
    ``listening`` phase until ``stop``), and ``start_dictation`` /
    ``stop_dictation`` flip the ``dictating`` flag the controller reads back.
    """

    instances: list["FakeEngine"] = []

    def __init__(
        self, config: ASREngineConfig, *, on_speech_utterance, on_speech_segment
    ):
        self.config = config
        self.on_speech_utterance = on_speech_utterance
        self.on_speech_segment = on_speech_segment
        self._running = False
        self._dictating = False
        self._mode = "utterance"
        self.listen_entered = asyncio.Event()
        self.dictation_calls: list[tuple[str | None, bool]] = []
        self.params_calls: list[tuple[list[str], float, float]] = []
        FakeEngine.instances.append(self)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def listen(self, mode=None, *, on_update=None):
        self._mode = mode or "trigger_word"
        self.listen_entered.set()
        try:
            await asyncio.Event().wait()  # block until the task is cancelled
        finally:
            self._mode = "utterance"

    async def start_dictation(
        self, *, end_on_final_segment=False, segmentation_mode=None
    ):
        self.dictation_calls.append((segmentation_mode, end_on_final_segment))
        self._dictating = True
        self._mode = segmentation_mode or "trigger_word"

    async def stop_dictation(self) -> None:
        self._dictating = False
        self._mode = "utterance"

    async def set_segmentation_params(
        self, *, trigger_words, initial_silence_timeout_s, end_of_speech_timeout_s
    ):
        self.params_calls.append(
            (trigger_words, initial_silence_timeout_s, end_of_speech_timeout_s)
        )

    @property
    def dictating(self) -> bool:
        return self._dictating

    @property
    def segmentation_mode(self) -> str:
        return self._mode

    def status(self) -> dict:
        return {"running": self._running, "connected": self._running}


@contextmanager
def make_controller() -> Iterator[DemoController]:
    """A controller whose engine is FakeEngine, torn down cleanly."""
    FakeEngine.instances.clear()
    base = ASREngineConfig(module=ModuleConfig(type="deepgram_v1"))
    with patch("examples.gradio_demo.controller.ASREngine", FakeEngine):
        with DemoController(base) as controller:
            yield controller


def wait_until(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll a predicate off the controller's loop thread until true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


# --- discovery --------------------------------------------------------------


def test_available_modules_lists_the_registry_sorted():
    with make_controller() as c:
        assert c.available_modules() == sorted(REGISTRY)


# --- rebuild-on-config-change -----------------------------------------------


def test_set_module_while_stopped_updates_pending_config():
    with make_controller() as c:
        c.set_module("deepgram_v2")
        assert c.state().module_type == "deepgram_v2"


def test_next_start_builds_a_fresh_engine_from_the_changed_config():
    with make_controller() as c:
        c.start()  # builds engine #0 from deepgram_v1
        c.stop()
        c.set_device("Mic A")
        c.set_module("deepgram_v2")
        c.start()  # must build a *new* engine from the updated config
        assert len(FakeEngine.instances) == 2
        newest = FakeEngine.instances[-1]
        assert newest.config.module.type == "deepgram_v2"
        assert newest.config.audio.device == "Mic A"


def test_set_module_while_running_raises_and_keeps_config():
    with make_controller() as c:
        c.start()
        with pytest.raises(RuntimeError, match="Stop the engine"):
            c.set_module("deepgram_v2")
        assert c.state().module_type == "deepgram_v1"


# --- listen cancellation ----------------------------------------------------


def test_listen_enters_listening_then_stop_cancels_back_to_stopped():
    with make_controller() as c:
        c.listen()
        assert c.state().phase == "listening"
        # the fake's listen() has actually started running on the loop thread
        wait_until(lambda: FakeEngine.instances[-1].listen_entered.is_set())

        c.stop()  # cancels the listen task

        wait_until(lambda: c.state().phase == "stopped")
        assert "cancelled" in c.state().message.lower()


# --- param validation -------------------------------------------------------


def test_non_positive_timeout_is_rejected_and_leaves_params_intact():
    with make_controller() as c:
        before = c.state()
        c.set_segmentation_params("go, stop", 0.0, 5.0)
        after = c.state()
        assert "greater than 0" in after.message
        assert after.initial_silence_timeout_s == before.initial_silence_timeout_s
        assert after.trigger_words == before.trigger_words


def test_valid_params_are_applied_to_pending_config():
    with make_controller() as c:
        c.set_segmentation_params("alpha, beta", 3.0, 2.0)
        st = c.state()
        assert st.trigger_words == "alpha, beta"
        assert (st.initial_silence_timeout_s, st.end_of_speech_timeout_s) == (3.0, 2.0)


# --- phase / enablement transitions -----------------------------------------


def _enablement(st: ControllerState) -> tuple[bool, bool, bool, bool, bool]:
    return (st.can_start, st.can_stop, st.can_listen, st.config_enabled, st.can_dictate)


def test_stopped_enablement():
    with make_controller() as c:
        # (can_start, can_stop, can_listen, config_enabled, can_dictate)
        assert _enablement(c.state()) == (True, False, True, True, False)


def test_running_enablement_and_phase():
    with make_controller() as c:
        c.start()
        st = c.state()
        assert st.phase == "running"
        assert _enablement(st) == (False, True, False, False, True)


def test_dictation_round_trip_flips_display_phase():
    with make_controller() as c:
        c.start()
        c.start_dictation(mode="timeout")
        st = c.state()
        assert st.phase == "dictating"
        assert st.dictating is True
        assert st.can_stop is True
        assert FakeEngine.instances[-1].dictation_calls == [("timeout", False)]

        c.stop_dictation()
        st = c.state()
        assert st.phase == "running"
        assert st.dictating is False


def test_start_dictation_before_running_is_rejected():
    with make_controller() as c:
        c.start_dictation(mode="timeout")
        assert "Start the engine" in c.state().message
        assert c.state().phase == "stopped"
