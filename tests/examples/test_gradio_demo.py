"""The Gradio demo: its session over the app's saved config, and its page.

The controller owns a real background asyncio loop but drives an ``ASREngine``
replaced with a ``FakeEngine`` (patched at
``examples.gradio_demo.controller.ASREngine``), over a real app-setup
``AppModel`` on a temporary folder. So these exercise the session's own logic —
built from the saved config, refused with the config's blockers, the config
locked while it runs, listen cancellation, dictation, phase/enablement — without
an audio device, network or browser. See specs/gradio-demo.md.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import gradio as gr
import pytest
from app_setup.app import AppModel
from app_setup.folder import ConfigFolder
from app_setup.secrets import SecretStore

from asr_engine import setup
from asr_engine.config import ASREngineConfig
from asr_engine.vad.gate import VadState
from examples.gradio_demo.app import APP, DEFAULT_CONFIG, build_page
from examples.gradio_demo.controller import (
    ControllerState,
    DemoController,
)


class FakeEngine:
    """Stand-in for ASREngine: records how it was built, awaitable, controllable.

    Its ``listen`` blocks until cancelled (so the controller sits in the
    ``listening`` phase until ``stop``), and ``start_dictation`` /
    ``stop_dictation`` flip the ``dictating`` flag the controller reads back.
    """

    instances: list[FakeEngine] = []

    def __init__(
        self, config: ASREngineConfig, *, on_speech_utterance, on_speech_segment
    ):
        self.config = config
        self._running = False
        self._dictating = False
        self._mode = "utterance"
        self.dictation_calls: list[tuple[str | None, bool]] = []
        # What the real engine reports: None without engine.vad, else the gate's.
        self.vad_state: VadState | None = None if config.vad is None else VadState()
        FakeEngine.instances.append(self)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def listen(self, mode=None, *, on_update=None):
        self._mode = mode or "trigger_word"
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

    @property
    def dictating(self) -> bool:
        return self._dictating

    @property
    def segmentation_mode(self) -> str:
        return self._mode

    def status(self) -> dict:
        return {"running": self._running, "connected": self._running}


def _model(tmp_path: Path, controller: DemoController | None = None) -> AppModel:
    """The demo's model over a folder of its own, its secrets kept off os.environ,
    locked by *controller*'s session."""
    folder = ConfigFolder(tmp_path, APP.default_config)
    return AppModel(
        APP,
        folder,
        SecretStore(folder.env_path, {}),
        locked=controller.locked if controller is not None else None,
    )


@contextmanager
def session(tmp_path: Path) -> Iterator[tuple[DemoController, AppModel]]:
    """A controller whose engine is FakeEngine, and the app's model it locks."""
    FakeEngine.instances.clear()
    with patch("examples.gradio_demo.controller.ASREngine", FakeEngine):
        with DemoController() as controller:
            yield controller, _model(tmp_path, controller)


def _with(**asr: Any) -> dict[str, Any]:
    """The default config with its `asr` section's keys replaced."""
    return {"asr": {**DEFAULT_CONFIG["asr"], **asr}}


def _silero(tmp_path: Path) -> dict[str, Any]:
    """A Silero gate on a model file of its own, so the machine misses nothing
    (the cached model is not there on a fresh machine). Its extra must import."""
    pytest.importorskip("onnxruntime")
    path = tmp_path / "silero_vad.onnx"
    path.write_bytes(b"onnx")
    return {"type": "silero", "model_path": str(path)}


def wait_until(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll a predicate off the controller's loop thread until true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


# --- The app's declaration -------------------------------------------------------


def test_the_demo_s_default_config_is_ready_on_any_machine(tmp_path: Path) -> None:
    model = _model(tmp_path)

    assert model.saved_config() == DEFAULT_CONFIG
    assert setup.check_config(DEFAULT_CONFIG["asr"]) == []
    assert model.blockers(model.saved_config()) == []


def test_the_default_config_is_the_committed_file_written_into_a_new_folder(
    tmp_path: Path,
) -> None:
    import json

    from examples.gradio_demo.app import DEFAULT_CONFIG_FILE

    model = _model(tmp_path / "new")

    assert model.saved_config() == json.loads(DEFAULT_CONFIG_FILE.read_text())
    assert model.saved_config()["asr"]["module"]["type"] == "fake"


def test_the_page_puts_listen_first_and_opens_on_setup(tmp_path: Path) -> None:
    with DemoController() as controller:
        page = build_page(_model(tmp_path, controller), controller)

    tabs = [b.label for b in page.blocks.values() if isinstance(b, gr.Tab)]
    [selector] = [b for b in page.blocks.values() if isinstance(b, gr.Tabs)]
    buttons = [b.value for b in page.blocks.values() if isinstance(b, gr.Button)]
    assert tabs == ["Listen", "Setup", "Secrets"]
    assert selector.selected == "setup"
    assert {"Start", "Stop", "Listen (single-shot)", "Start dictation"} <= set(buttons)
    assert buttons.count("Test") == 1  # the asr section's own checks


# --- A session over the saved config ------------------------------------------


def test_start_builds_the_engine_from_the_saved_section_and_locks_the_config(
    tmp_path: Path,
) -> None:
    with session(tmp_path) as (controller, model):
        controller.start(model)
        state = controller.state(model)

        assert (state.phase, state.connected, state.module_type) == (
            "running",
            True,
            "fake",
        )
        [engine] = FakeEngine.instances
        assert engine.config == ASREngineConfig.from_dict(DEFAULT_CONFIG["asr"])
        assert controller.locked()
        with pytest.raises(RuntimeError):
            model.save(_with(sound_feedback={"enabled": False}))

        controller.stop()

        assert controller.state(model).phase == "stopped"
        assert not controller.locked()


def test_a_config_saved_between_sessions_is_the_one_the_next_session_runs(
    tmp_path: Path,
) -> None:
    with session(tmp_path) as (controller, model):
        controller.start(model)
        controller.stop()
        model.save(_with(vad=_silero(tmp_path)))
        controller.start(model)

        first, second = FakeEngine.instances
        assert first.config.vad is None
        assert second.config.vad is not None and second.config.vad.type == "silero"


def test_a_config_with_problems_says_why_and_starts_nothing(tmp_path: Path) -> None:
    with session(tmp_path) as (controller, model):
        model.save(_with(segmentation={"end_of_speech_timeout_s": 0}))

        controller.start(model)
        controller.listen(model, "utterance")
        state = controller.state(model)

        assert state.phase == "stopped"
        assert state.message.startswith("The saved config cannot run yet:")
        assert "end_of_speech_timeout_s" in state.message
        assert FakeEngine.instances == []
        assert not controller.locked()


def test_a_refused_start_s_reasons_follow_the_saved_config_and_the_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reasons a start was refused are the saved config's current blockers
    once the page refreshes them (a save, the tab selected, a key changed), not
    the ones kept from the attempt."""
    monkeypatch.setenv("DEMO_ASR_KEY", "")  # recorded, so the key saved below is undone
    monkeypatch.delenv("DEMO_ASR_KEY")
    with session(tmp_path) as (controller, _):
        # Keys saved on the Secrets tab reach the process's environment, where
        # the engine's checks read them (monkeypatch takes the variable out again).
        folder = ConfigFolder(tmp_path, APP.default_config)
        model = AppModel(
            APP, folder, SecretStore(folder.env_path), locked=controller.locked
        )
        model.save(
            _with(
                module={"type": "deepgram-v1", "api_key_env": "DEMO_ASR_KEY"},
                segmentation={"end_of_speech_timeout_s": 0},
            )
        )
        controller.start(model)
        refused = controller.state(model).message
        # A config with a problem is not asked about the machine yet.
        assert "end_of_speech_timeout_s" in refused and "DEMO_ASR_KEY" not in refused

        # The problem fixed and saved: the key it names is what is left.
        model.save(_with(module={"type": "deepgram-v1", "api_key_env": "DEMO_ASR_KEY"}))
        assert controller.state(model).message == refused  # until refreshed
        controller.refresh_blockers(model)
        left = controller.state(model).message
        assert left.startswith("The saved config cannot run yet:")
        assert "DEMO_ASR_KEY" in left and "end_of_speech_timeout_s" not in left

        # The key saved on the Secrets tab: nothing is missing any more.
        revision = model.secrets.revision
        model.secrets.set("DEMO_ASR_KEY", "a-key")
        assert model.secrets.revision != revision  # what the page's tick watches
        controller.refresh_blockers(model)
        assert controller.state(model).message == (
            "The saved config has nothing missing any more: Start can run it."
        )
        assert FakeEngine.instances == []


def test_a_refresh_leaves_any_other_message_alone(tmp_path: Path) -> None:
    with session(tmp_path) as (controller, model):
        controller.start(model)
        controller.stop()
        model.save(_with(segmentation={"end_of_speech_timeout_s": 0}))

        controller.refresh_blockers(model)

        assert controller.state(model).message == "Stopped."


def test_the_listen_tab_says_when_the_setup_tab_has_unsaved_changes(
    tmp_path: Path,
) -> None:
    from app_setup.ui import unsaved_notice

    model = _model(tmp_path)
    saved = model.saved_config()

    assert unsaved_notice(model, saved) == ""
    edited = _with(module={"type": "fake", "utterances": []})
    assert "Start uses the saved config" in unsaved_notice(model, edited)
    model.save(edited)
    assert unsaved_notice(model, edited) == ""


def test_an_engine_that_cannot_be_built_is_said_and_unlocks(tmp_path: Path) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ImportError("an extra is missing")

    with DemoController() as controller:
        model = _model(tmp_path, controller)
        with patch("examples.gradio_demo.controller.ASREngine", refuse):
            controller.start(model)

        assert controller.state(model).message == (
            "The engine could not be built: an extra is missing"
        )
        assert not controller.locked()


def test_listen_runs_one_capture_locked_then_stop_cancels_and_releases(
    tmp_path: Path,
) -> None:
    with session(tmp_path) as (controller, model):
        controller.listen(model, "timeout")
        wait_until(lambda: controller.state(model).segmentation_mode == "timeout")
        assert controller.state(model).phase == "listening"
        assert controller.locked()

        controller.stop()
        wait_until(lambda: controller.state(model).phase == "stopped")

        assert controller.state(model).message == "Listen cancelled."
        assert not controller.locked()


def test_listen_is_refused_while_a_session_runs(tmp_path: Path) -> None:
    with session(tmp_path) as (controller, model):
        controller.start(model)
        controller.listen(model, "utterance")

        assert controller.state(model).phase == "running"
        assert len(FakeEngine.instances) == 1


# --- Enablement and dictation --------------------------------------------------


def _enablement(state: ControllerState) -> tuple[bool, bool, bool, bool]:
    return (state.can_start, state.can_stop, state.can_listen, state.can_dictate)


def test_what_can_be_pressed_follows_the_session(tmp_path: Path) -> None:
    with session(tmp_path) as (controller, model):
        assert _enablement(controller.state(model)) == (True, False, True, False)
        controller.start(model)
        assert _enablement(controller.state(model)) == (False, True, False, True)


def test_start_and_stop_are_greyed_while_the_session_starts_and_stops(
    tmp_path: Path,
) -> None:
    gate = threading.Event()

    class SlowEngine(FakeEngine):
        async def start(self) -> None:
            await asyncio.to_thread(gate.wait, 5)
            await super().start()

        async def stop(self) -> None:
            await asyncio.to_thread(gate.wait, 5)
            await super().stop()

    with DemoController() as controller:
        model = _model(tmp_path, controller)
        with patch("examples.gradio_demo.controller.ASREngine", SlowEngine):
            starting = threading.Thread(target=controller.start, args=(model,))
            starting.start()
            wait_until(lambda: controller.state(model).phase == "starting")
            during_start = controller.state(model)
            gate.set()
            starting.join(5)
            gate.clear()
            stopping = threading.Thread(target=controller.stop)
            stopping.start()
            wait_until(lambda: controller.state(model).phase == "stopping")
            during_stop = controller.state(model)
            gate.set()
            stopping.join(5)

        assert (during_start.can_start, during_start.can_stop) == (False, False)
        assert (during_stop.can_start, during_stop.can_stop) == (False, False)
        assert controller.state(model).phase == "stopped"


def test_dictation_round_trip_flips_the_displayed_phase(tmp_path: Path) -> None:
    with session(tmp_path) as (controller, model):
        controller.start(model)
        controller.start_dictation(mode="timeout", end_on_final_segment=True)
        dictating = controller.state(model)
        controller.stop_dictation()

        assert (dictating.phase, dictating.segmentation_mode) == (
            "dictating",
            "timeout",
        )
        assert FakeEngine.instances[0].dictation_calls == [("timeout", True)]
        assert controller.state(model).phase == "running"


def test_dictation_before_a_session_is_refused(tmp_path: Path) -> None:
    with session(tmp_path) as (controller, model):
        controller.start_dictation(mode="utterance")

        assert controller.state(model).message == "Start the session before dictation."


# --- The gate's view -------------------------------------------------------------


def test_without_a_gate_the_view_says_there_is_none(tmp_path: Path) -> None:
    with session(tmp_path) as (controller, model):
        state = controller.state(model)

        assert state.vad.startswith("off")
        assert state.voice_activity == "no VAD"


def test_the_saved_gate_is_shown_before_a_session_then_the_session_s(
    tmp_path: Path,
) -> None:
    with session(tmp_path) as (controller, model):
        model.save(_with(vad={**_silero(tmp_path), "min_silence_duration_ms": 900}))
        before = controller.state(model)
        controller.start(model)
        FakeEngine.instances[0].vad_state = VadState(speaking=True)
        running = controller.state(model)

        assert "closes after 900 ms of silence" in before.vad
        assert before.voice_activity == "idle (not capturing)"
        assert running.voice_activity == "SPEECH: audio is sent to the module"


# --- Paths -------------------------------------------------------------------------


def test_a_relative_path_runs_against_the_app_s_folder(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    with session(folder) as (controller, model):
        (folder / "speech.wav").write_bytes(b"")
        model.save(
            _with(
                audio_source={"type": "file", "audio_file": "speech.wav"},
            )
        )
        before = model.blockers(model.saved_config())
        controller.start(model)

        assert before == []  # app-setup checks the file in the app's folder
        [engine] = FakeEngine.instances
        assert engine.config.audio_source.audio_file == str(folder / "speech.wav")
