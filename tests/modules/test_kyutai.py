"""Tests for the Kyutai local STT module (specs/kyutai-module.md).

Every test drives ``KyutaiModule`` through a scripted fake backend injected at the
``KyutaiBackend`` seam, so nothing here needs a Kyutai extra or loads a model.
"""

from __future__ import annotations

import asyncio
import platform
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import ClassVar

import numpy as np
import pytest

from asr_engine.audio import AudioFormat
from asr_engine.modules import kyutai, resolve_module_class
from asr_engine.modules.base import SpeechUtterance, reconcile_audio_format
from asr_engine.modules.kyutai import KyutaiModule, StepResult

FORMAT_24K = AudioFormat(sample_rate=24000)
FRAME_SAMPLES = 1920


def T(text: str, p: float = 0.0) -> StepResult:
    """A step that decodes a text piece."""
    return StepResult(text, p)


def V(p: float) -> StepResult:
    """A padding step carrying an end-of-turn probability."""
    return StepResult(None, p)


PAD = StepResult(None, 0.0)


class ScriptedBackend:
    """A ``KyutaiBackend`` replaying a list of ``StepResult``s, one per frame."""

    SAMPLE_RATE: ClassVar[int] = 24000
    FRAME_SAMPLES: ClassVar[int] = FRAME_SAMPLES

    def __init__(
        self,
        script: list[StepResult] = [],
        *,
        has_vad: bool = True,
        max_steps: int | None = None,
        audio_delay_s: float = 0.0,
        silence_prefix_s: float = 0.0,
        load_gate: threading.Event | None = None,
        step_gate: threading.Event | None = None,
        fail_at_step: int | None = None,
    ) -> None:
        self._script = list(script)
        self._has_vad = has_vad
        self._max_steps = max_steps
        self._audio_delay_s = audio_delay_s
        self._silence_prefix_s = silence_prefix_s
        self._load_gate = load_gate
        self._step_gate = step_gate
        self._fail_at_step = fail_at_step
        self._session_steps = 0
        self.frames: list[np.ndarray] = []
        self.calls: list[str] = []
        self.reset_after_frames: list[int] = []  # len(frames) at each reset()
        self.threads: set[str] = set()

    @property
    def has_vad(self) -> bool:
        return self._has_vad

    @property
    def steps_remaining(self) -> int | None:
        if self._max_steps is None:
            return None
        return self._max_steps - self._session_steps

    @property
    def audio_delay_s(self) -> float:
        return self._audio_delay_s

    @property
    def silence_prefix_s(self) -> float:
        return self._silence_prefix_s

    def load(self) -> None:
        self.threads.add(threading.current_thread().name)
        if self._load_gate is not None:
            assert self._load_gate.wait(timeout=5)
        self.calls.append("load")

    def step(self, frame: np.ndarray) -> StepResult:
        self.threads.add(threading.current_thread().name)
        if self._max_steps is not None and self._session_steps >= self._max_steps:
            raise ValueError(f"reached max-steps {self._max_steps}")
        self.frames.append(frame)
        if self._step_gate is not None:
            assert self._step_gate.wait(timeout=5)
        if self._fail_at_step == len(self.frames):
            raise RuntimeError("scripted step failure")
        self._session_steps += 1
        if self._script:
            result = self._script.pop(0)
        else:
            result = PAD
        if not self._has_vad:
            result = StepResult(result.text, None)
        return result

    def reset(self) -> None:
        self.threads.add(threading.current_thread().name)
        self.calls.append("reset")
        self.reset_after_frames.append(len(self.frames))
        self._session_steps = 0

    def close(self) -> None:
        self.calls.append("close")


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    """Poll until *predicate* holds — the worker is a real thread, so waiting on
    an observable outcome is the only deterministic synchronization."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0.001)


def _pcm(samples: list[int] | np.ndarray) -> bytes:
    return np.asarray(samples, dtype="<i2").tobytes()


class _Harness:
    """Runs a KyutaiModule on a hand-fed queue, recording what it emits."""

    def __init__(self, backend: ScriptedBackend, config: dict | None = None) -> None:
        self.backend = backend
        self.backends_created = 0

        def factory() -> ScriptedBackend:
            self.backends_created += 1
            return self.backend

        self.module = KyutaiModule(config or {}, backend_factory=factory)
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.events: list[tuple[str, bool, float | None]] = []
        self.connected: list[bool] = []
        self.task: asyncio.Task | None = None

    async def _on_utterance(self, u: SpeechUtterance) -> None:
        self.events.append((u.transcript, u.is_final, u.confidence))

    async def start(self, *, wait_connected: bool = True) -> None:
        n_connects = self.connected.count(True)
        self.task = asyncio.create_task(
            self.module.start(
                self.queue,
                self._on_utterance,
                self.connected.append,
                audio_format=FORMAT_24K,
            )
        )
        if wait_connected:
            await _until(lambda: self.connected.count(True) > n_connects)

    async def feed_frames(self, n: int) -> None:
        """Feed *n* whole frames of silence, one chunk per frame."""
        for _ in range(n):
            await self.queue.put(b"\x00" * FRAME_SAMPLES * 2)

    async def run_script(self, n_events: int) -> None:
        """Feed one frame per scripted step and wait for *n_events* utterances."""
        await self.feed_frames(len(self.backend._script))
        await _until(lambda: len(self.events) >= n_events)

    async def stop(self) -> None:
        await self.module.stop()
        assert self.task is not None
        await asyncio.wait_for(self.task, timeout=2.0)


# ---------------------------------------------------------------- audio bridge


async def test_reblocks_chunks_into_whole_frames_carrying_the_remainder():
    samples = np.arange(7680, dtype=np.int16)  # 4 frames' worth, a distinct ramp
    h = _Harness(ScriptedBackend())
    await h.start()

    # Three 100 ms chunks (2400 samples = 1.25 frames each): 3 whole frames plus a
    # 1440-sample remainder, which must be held back, not emitted as a short frame.
    for i in range(3):
        await h.queue.put(_pcm(samples[i * 2400 : (i + 1) * 2400]))
    await _until(lambda: len(h.backend.frames) == 3)
    await _settle()
    assert len(h.backend.frames) == 3

    await h.queue.put(_pcm(samples[7200:]))  # completes the fourth frame
    await _until(lambda: len(h.backend.frames) == 4)
    await h.stop()

    assert [f.shape for f in h.backend.frames] == [(FRAME_SAMPLES,)] * 4
    assert all(f.dtype == np.float32 for f in h.backend.frames)
    np.testing.assert_array_equal(
        np.concatenate(h.backend.frames), samples.astype(np.float32) / 32768.0
    )


async def test_converts_s16_extremes_to_unit_range_float32():
    chunk = [-32768, 32767, 0, 16384] + [0] * (FRAME_SAMPLES - 4)
    h = _Harness(ScriptedBackend())
    await h.start()
    await h.queue.put(_pcm(chunk))
    await _until(lambda: len(h.backend.frames) == 1)
    await h.stop()

    assert h.backend.frames[0][:4].tolist() == [-1.0, 32767 / 32768, 0.0, 0.5]


async def test_feeds_the_models_silence_prefix_before_real_audio():
    h = _Harness(ScriptedBackend(silence_prefix_s=0.24))  # 3 frames
    await h.start()
    await h.queue.put(_pcm([1000] * FRAME_SAMPLES))
    await _until(lambda: len(h.backend.frames) == 4)
    await h.stop()

    assert [float(f[0]) for f in h.backend.frames] == [0.0, 0.0, 0.0, 1000 / 32768]


# ------------------------------------------------------------------ utterances


async def test_accumulates_pieces_into_interims_and_ignores_padding():
    script = [PAD, T(" The"), PAD, T(" sky"), PAD, PAD, T(" is"), T(".")]
    h = _Harness(ScriptedBackend(script), {"vad": False})
    await h.start()
    await h.run_script(n_events=4)
    await h.stop()

    assert h.events == [
        ("The", False, None),
        ("The sky", False, None),
        ("The sky is", False, None),
        ("The sky is.", False, None),
    ]


async def test_vad_rising_edge_closes_one_utterance_across_a_sustained_run():
    script = [
        T(" The"),
        T(" sky"),
        V(0.9),  # rising edge: closes "The sky"
        V(0.95),  # still high: must not close anything again
        V(0.99),
        V(0.2),  # falls back under the threshold, re-arming the latch
        T(" next"),
        V(0.8),
    ]
    h = _Harness(ScriptedBackend(script))
    await h.start()
    await h.run_script(n_events=5)
    await h.stop()

    assert h.events == [
        ("The", False, None),
        ("The sky", False, None),
        ("The sky", True, 0.9),
        ("next", False, None),
        ("next", True, 0.8),
    ]


async def test_vad_final_waits_out_the_text_delay_so_the_tail_is_included():
    # The VAD head tracks the audio; the text runs audio_delay_s behind it. The
    # words spoken just before the edge arrive after it and belong to the final.
    script = [T(" The"), V(0.9), T(" end", 0.95), V(0.97), T(" after", 0.97)]
    h = _Harness(ScriptedBackend(script, audio_delay_s=0.16))  # 2 steps
    await h.start()
    await h.run_script(n_events=4)
    await h.stop()

    assert h.events == [
        ("The", False, None),
        ("The end", False, None),
        ("The end", True, 0.9),  # edge + 2 steps; confidence is the edge's
        ("after", False, None),
    ]


async def test_silence_timer_closes_on_audio_time_and_not_while_text_arrives():
    # 0.4 s = 5 steps. Four silent steps then text: no final. Five: final — and
    # were the timer any slower, "c" would land in the same utterance.
    script = [T(" a"), PAD, PAD, PAD, PAD, T(" b"), PAD, PAD, PAD, PAD, PAD, T(" c")]
    h = _Harness(
        ScriptedBackend(script), {"vad": False, "finalize_after_silence_s": 0.4}
    )
    await h.start()
    await h.run_script(n_events=4)
    await h.stop()

    assert h.events == [
        ("a", False, None),
        ("a b", False, None),
        ("a b", True, None),
        ("c", False, None),
    ]


@pytest.mark.parametrize(
    ("config", "has_vad"),
    [({"vad": False}, True), ({"vad": True}, False)],
    ids=["vad-disabled", "model-without-vad-heads"],
)
async def test_without_a_usable_vad_head_only_the_timer_finalizes(config, has_vad):
    script = [T(" a"), V(0.99), V(0.99), T(" b"), PAD, PAD, PAD, T(" c")]
    h = _Harness(
        ScriptedBackend(script, has_vad=has_vad),
        {**config, "finalize_after_silence_s": 0.24},
    )
    await h.start()
    await h.run_script(n_events=4)
    await h.stop()

    assert h.events == [
        ("a", False, None),
        ("a b", False, None),
        ("a b", True, None),
        ("c", False, None),
    ]


async def test_finalizing_with_nothing_accumulated_emits_nothing():
    script = [V(0.9), V(0.9), V(0.1), PAD, PAD, PAD, T(" hi"), V(0.9)]
    h = _Harness(ScriptedBackend(script), {"finalize_after_silence_s": 0.16})
    await h.start()
    await h.run_script(n_events=2)
    await h.stop()

    assert h.events == [("hi", False, None), ("hi", True, 0.9)]


# ----------------------------------------------------------------- step budget


async def test_low_step_budget_recycles_the_session_at_a_finalization_boundary():
    # max_steps 30 → recycle once <= 4 steps remain (2x the 2-step silence timer).
    script = [T(" a"), V(0.9)] + [T(" x", 0.9)] * 23 + [V(0.1), V(0.9)]
    backend = ScriptedBackend(script, max_steps=30)
    h = _Harness(backend, {"max_steps": 30, "finalize_after_silence_s": 0.16})
    await h.start()
    await h.run_script(n_events=26)
    await _until(lambda: backend.reset_after_frames == [27])

    # The first final (28 steps left) did not recycle; the second (3 left) did.
    # The fresh session has its full budget back: 30 more steps go through.
    backend._script = [PAD] * 28 + [T(" b"), V(0.9)]
    await h.run_script(n_events=28)
    await h.stop()

    assert h.events[:3] == [("a", False, None), ("a", True, 0.9), ("x", False, None)]
    assert h.events[25] == (" ".join(["x"] * 23), True, 0.9)
    assert h.events[26:] == [("b", False, None), ("b", True, 0.9)]
    assert len(backend.frames) == 57


async def test_idle_session_recycles_before_the_budget_runs_out():
    backend = ScriptedBackend([], max_steps=30)
    h = _Harness(backend, {"max_steps": 30, "finalize_after_silence_s": 0.16})
    await h.start()
    await h.feed_frames(40)  # silence only: more steps than one session allows
    await _until(lambda: len(backend.frames) == 40)
    await h.stop()

    assert backend.reset_after_frames[0] < 30  # recycled while idle, never hit 0


async def test_budget_exhausted_mid_utterance_force_finalizes_then_carries_on():
    script = [T(" a"), T(" b"), T(" c"), T(" d"), T(" e"), T(" f")]
    backend = ScriptedBackend(script, max_steps=5)
    h = _Harness(
        backend, {"vad": False, "max_steps": 5, "finalize_after_silence_s": 100}
    )
    await h.start()
    await h.run_script(n_events=7)
    await h.stop()

    assert h.events[4:] == [
        ("a b c d e", False, None),
        ("a b c d e", True, None),  # forced: the sixth step needed a fresh session
        ("f", False, None),
    ]
    assert backend.reset_after_frames[0] == 5


# ---------------------------------------------------------------- backpressure


async def test_lag_past_the_drop_threshold_drops_oldest_frames_and_keeps_newest():
    gate = threading.Event()
    backend = ScriptedBackend(step_gate=gate)
    # warn above 2 queued frames (0.2 s), drop above 5 (0.45 s).
    h = _Harness(backend, {"lag_warn_s": 0.2, "lag_drop_s": 0.45})
    await h.start()

    await h.queue.put(_pcm([0] * FRAME_SAMPLES))
    await _until(lambda: len(backend.frames) == 1)  # frame 0 is stuck in step()
    # Frames 1..7 arrive while the model is stalled. Queueing frame 7 finds six
    # waiting (0.48 s): the oldest are dropped back down to two, then 7 is added.
    for i in range(1, 8):
        await h.queue.put(_pcm([i * 100] * FRAME_SAMPLES))
    await _until(h.queue.empty)
    await _settle()
    gate.set()
    await _until(lambda: len(backend.frames) == 4)
    await _settle()
    await h.stop()

    assert [round(float(f[0]) * 32768) for f in backend.frames] == [0, 500, 600, 700]


# ------------------------------------------------------------------- lifecycle


async def test_connected_only_once_the_model_is_loaded_and_load_time_audio_dropped():
    load_gate = threading.Event()
    backend = ScriptedBackend([T(" hi")], load_gate=load_gate)
    h = _Harness(backend)
    await h.start(wait_connected=False)
    await h.feed_frames(5)  # captured while loading: stale by the time it's ready
    await _until(h.queue.empty)
    assert h.connected == []

    load_gate.set()
    await _until(lambda: h.connected == [True])
    await h.run_script(n_events=1)
    await h.stop()

    assert len(backend.frames) == 1
    assert h.events == [("hi", False, None)]
    assert h.connected == [True, False]


async def test_stop_unblocks_an_idle_start():
    h = _Harness(ScriptedBackend())
    await h.start()
    await h.stop()  # nothing was ever fed; start() must still return promptly

    assert h.connected == [True, False]


async def test_stop_during_load_returns_and_the_next_start_reuses_that_load():
    load_gate = threading.Event()
    backend = ScriptedBackend([T(" hi")], load_gate=load_gate)
    h = _Harness(backend)
    await h.start(wait_connected=False)
    await _settle()
    await h.stop()
    assert h.connected == []

    load_gate.set()
    await h.start()
    await h.run_script(n_events=1)
    await h.stop()

    assert backend.calls.count("load") == 1
    assert h.backends_created == 1


async def test_stop_keeps_the_loaded_backend_and_starts_a_fresh_session():
    backend = ScriptedBackend([T(" one"), T(" two")])
    h = _Harness(backend, {"vad": False})
    await h.start()
    await h.feed_frames(1)
    await _until(lambda: len(h.events) == 1)
    await h.stop()

    await h.start()
    await h.feed_frames(1)
    await _until(lambda: len(h.events) == 2)
    await h.stop()

    # One model, loaded once, reset (not closed) between sessions — and the open
    # utterance from the first session does not leak into the second.
    assert h.backends_created == 1
    assert backend.calls == ["load", "reset", "reset"]
    assert h.events == [("one", False, None), ("two", False, None)]
    assert h.connected == [True, False, True, False]
    assert len(backend.threads) == 1  # every backend call on the one thread


async def test_failed_load_is_retried_with_a_fresh_backend():
    class FailingLoad(ScriptedBackend):
        def load(self) -> None:
            self.calls.append("load-failed")
            raise OSError("no network")

    backends = [FailingLoad(), ScriptedBackend([T(" ok")])]
    created: list[ScriptedBackend] = []

    def factory() -> ScriptedBackend:
        created.append(backends[len(created)])
        return created[-1]

    module = KyutaiModule({}, backend_factory=factory)
    module._BACKOFF_S = (0.01,)
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    connected: list[bool] = []
    events: list[str] = []

    async def on_utterance(u: SpeechUtterance) -> None:
        events.append(u.transcript)

    task = asyncio.create_task(
        module.start(queue, on_utterance, connected.append, audio_format=FORMAT_24K)
    )
    await _until(lambda: connected == [True])
    await queue.put(b"\x00" * FRAME_SAMPLES * 2)
    await _until(lambda: events == ["ok"])
    await module.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert backends[0].calls == ["load-failed", "close"]
    assert backends[1].calls == ["load", "reset"]


async def test_step_failure_disconnects_then_reloads_and_resumes():
    backends = [
        ScriptedBackend([T(" a")], fail_at_step=2),
        ScriptedBackend([T(" b")]),
    ]
    created: list[ScriptedBackend] = []

    def factory() -> ScriptedBackend:
        created.append(backends[len(created)])
        return created[-1]

    module = KyutaiModule({"vad": False}, backend_factory=factory)
    module._BACKOFF_S = (0.01,)
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    connected: list[bool] = []
    events: list[str] = []

    async def on_utterance(u: SpeechUtterance) -> None:
        events.append(u.transcript)

    task = asyncio.create_task(
        module.start(queue, on_utterance, connected.append, audio_format=FORMAT_24K)
    )
    await _until(lambda: connected == [True])
    for _ in range(2):
        await queue.put(b"\x00" * FRAME_SAMPLES * 2)
    await _until(lambda: connected == [True, False, True])
    await queue.put(b"\x00" * FRAME_SAMPLES * 2)
    await _until(lambda: events == ["a", "b"])
    await module.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert "close" in backends[0].calls
    assert connected == [True, False, True, False]


# ---------------------------------------------------------- backend resolution


class _RecordingBackend(ScriptedBackend):
    created: list[tuple[str, dict]] = []
    name = ""

    def __init__(self, **kwargs) -> None:
        super().__init__()
        type(self).created.append((self.name, kwargs))


class RecordingMlxBackend(_RecordingBackend):
    name = "mlx"


class RecordingTorchBackend(_RecordingBackend):
    name = "torch"


@pytest.fixture
def recording_backends(monkeypatch):
    """Point both backend names at recording doubles; returns what gets built."""
    _RecordingBackend.created = []
    monkeypatch.setattr(
        kyutai,
        "_BACKEND_IMPLS",
        {
            "mlx": ("moshi_mlx", "kyutai-mlx", f"{__name__}:RecordingMlxBackend"),
            "torch": ("moshi", "kyutai-torch", f"{__name__}:RecordingTorchBackend"),
        },
    )
    return _RecordingBackend.created


def _platform(monkeypatch, system: str, machine: str, installed: set[str]) -> None:
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setattr(kyutai, "_is_importable", lambda pkg: pkg in installed)


async def _backend_built_by(module: KyutaiModule) -> tuple[str, dict]:
    connected: list[bool] = []
    task = asyncio.create_task(
        module.start(
            asyncio.Queue(),
            lambda u: _noop(),
            connected.append,
            audio_format=FORMAT_24K,
        )
    )
    await _until(lambda: connected == [True])
    await module.stop()
    await asyncio.wait_for(task, timeout=2.0)
    return _RecordingBackend.created[-1]


async def _noop() -> None:
    return None


@pytest.mark.parametrize(
    ("system", "machine", "installed", "expected"),
    [
        ("darwin", "arm64", {"moshi_mlx", "moshi"}, "mlx"),
        ("darwin", "arm64", {"moshi"}, "torch"),  # Apple Silicon without the extra
        ("darwin", "x86_64", {"moshi_mlx", "moshi"}, "torch"),  # Intel Mac
        ("linux", "x86_64", {"moshi"}, "torch"),
    ],
)
async def test_auto_backend_resolves_per_platform(
    monkeypatch, recording_backends, system, machine, installed, expected
):
    _platform(monkeypatch, system, machine, installed)
    name, _ = await _backend_built_by(KyutaiModule({}))
    assert name == expected


async def test_backend_receives_the_model_config(monkeypatch, recording_backends):
    _platform(monkeypatch, "linux", "x86_64", {"moshi_mlx", "moshi"})
    config = {"hf_repo": "kyutai/stt-2.6b-en", "vad": False, "max_steps": 99}

    forced_mlx = await _backend_built_by(KyutaiModule({**config, "backend": "mlx"}))
    torch = await _backend_built_by(KyutaiModule({**config, "device": "cpu"}))

    expected = {"hf_repo": "kyutai/stt-2.6b-en", "vad": False, "max_steps": 99}
    assert forced_mlx == ("mlx", expected)  # "mlx" forced, even off Apple Silicon
    assert torch == ("torch", {**expected, "device": "cpu"})


@pytest.mark.parametrize(
    ("backend", "installed", "extra"),
    [
        ("mlx", {"moshi"}, "kyutai-mlx"),
        ("torch", {"moshi_mlx"}, "kyutai-torch"),
        ("auto", set(), "kyutai-torch"),
    ],
)
def test_unavailable_backend_raises_the_install_hint(
    monkeypatch, backend, installed, extra
):
    _platform(monkeypatch, "linux", "x86_64", installed)
    with pytest.raises(ImportError) as exc_info:
        KyutaiModule({"backend": backend})

    message = str(exc_info.value)
    assert "ASR module 'kyutai' with backend" in message
    assert f"pip install 'asr-engine[{extra}]'" in message
    assert f"uv sync --extra {extra}" in message


def test_registered_and_importable_without_loading_a_backend_package():
    assert resolve_module_class("kyutai") is KyutaiModule
    code = (
        "import sys; from unittest.mock import MagicMock; "
        "sys.modules['sounddevice'] = MagicMock(); "
        "import asr_engine.modules.kyutai; "
        "loaded = {'moshi', 'moshi_mlx', 'mlx', 'torch'} & set(sys.modules); "
        "assert not loaded, loaded"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"backend": "onnx"}, "unknown backend 'onnx'"),
        ({"hf_repo": ""}, "'hf_repo' must be a non-empty string"),
        ({"vad": "yes"}, "'vad' must be a boolean"),
        ({"max_steps": 0}, "'max_steps' must be > 0"),
        ({"max_steps": 12.5}, "'max_steps' must be an integer"),
        ({"vad_threshold": 0}, "'vad_threshold' must be between 0 and 1"),
        ({"vad_threshold": 1.0}, "'vad_threshold' must be between 0 and 1"),
        ({"vad_threshold": "high"}, "'vad_threshold' must be a number"),
        ({"finalize_after_silence_s": -0.1}, "'finalize_after_silence_s' must be >= 0"),
        ({"device": "tpu"}, "unknown device 'tpu'"),
        ({"lag_warn_s": 0}, "'lag_warn_s' must be > 0"),
        ({"lag_warn_s": 5, "lag_drop_s": 5}, "must be greater than 'lag_warn_s'"),
    ],
)
def test_invalid_config_rejected(config, message):
    with pytest.raises(ValueError, match=message):
        KyutaiModule(config, backend_factory=ScriptedBackend)


def test_declares_the_strict_24khz_mono_linear16_contract():
    with pytest.raises(ValueError, match="24000"):
        reconcile_audio_format(AudioFormat(sample_rate=16000), KyutaiModule)
    assert reconcile_audio_format(FORMAT_24K, KyutaiModule) == FORMAT_24K
