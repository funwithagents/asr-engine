"""Functional tests for the mlx_audio module (specs/modules/mlx-audio.md).

mlx-audio itself is never imported: the module's ``load_model`` seam takes a fake
loader. The batch behaviour (buffering, interims, finals, lifecycle) is pinned in
test_batch.py; these tests cover what the module adds on top.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pytest

from asr_engine.audio import END_OF_SPEECH, AudioFormat
from asr_engine.modules import resolve_module_class
from asr_engine.modules.base import AudioQueue, SpeechUtterance
from asr_engine.modules.mlx_audio import module as mlx_module
from asr_engine.modules.mlx_audio.module import MlxAudioModule


class FakeRunner:
    def __init__(self) -> None:
        self.audio: list[np.ndarray] = []

    def transcribe(self, audio: np.ndarray) -> str:
        self.audio.append(audio)
        return "  the sky is blue  "


class FakeLoader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.runner = FakeRunner()

    def __call__(self, model: str, generate_kwargs: dict[str, Any]) -> FakeRunner:
        self.calls.append((model, generate_kwargs))
        return self.runner


async def test_the_configured_model_is_loaded_and_its_text_is_the_final() -> None:
    loader = FakeLoader()
    module = MlxAudioModule(
        {
            "model": "mlx-community/Qwen3-ASR-0.6B-8bit",
            "generate_kwargs": {"language": "English"},
            "interim_interval_s": 0,
        },
        load_model=loader,
    )
    queue: AudioQueue = asyncio.Queue()
    utterances: list[SpeechUtterance] = []
    connected: list[bool] = []

    async def on_utterance(u: SpeechUtterance) -> None:
        utterances.append(u)

    task = asyncio.create_task(
        module.start(
            queue, on_utterance, connected.append, audio_format=AudioFormat(16000)
        )
    )
    for _ in range(300):  # audio sent while loading is dropped
        if connected:
            break
        await asyncio.sleep(0.01)
    samples = np.array([0, 16384, -16384, 32767], dtype="<i2")
    queue.put_nowait(samples.tobytes())
    queue.put_nowait(END_OF_SPEECH)
    for _ in range(300):
        if utterances:
            break
        await asyncio.sleep(0.01)
    await module.stop()
    await asyncio.wait_for(task, 2.0)

    assert loader.calls == [
        ("mlx-community/Qwen3-ASR-0.6B-8bit", {"language": "English"})
    ]
    assert [(u.transcript, u.is_final) for u in utterances] == [
        ("the sky is blue", True)
    ]
    np.testing.assert_allclose(
        loader.runner.audio[0], [0.0, 0.5, -0.5, 32767 / 32768], rtol=1e-6
    )
    assert connected == [True, False]


def test_the_default_model_is_parakeet() -> None:
    loader = FakeLoader()
    module = MlxAudioModule({}, load_model=loader)
    module.load()

    assert loader.calls == [("mlx-community/parakeet-tdt-0.6b-v3", {})]


def test_the_install_hint_is_raised_off_apple_silicon(monkeypatch) -> None:
    monkeypatch.setattr(mlx_module, "_on_apple_silicon", lambda: False)
    with pytest.raises(ImportError, match=r"asr-engine\[mlx-audio\]"):
        MlxAudioModule({})


def test_the_install_hint_is_raised_without_the_package(monkeypatch) -> None:
    monkeypatch.setattr(mlx_module, "_on_apple_silicon", lambda: True)
    monkeypatch.setattr(mlx_module, "_mlx_audio_installed", lambda: False)
    with pytest.raises(ImportError, match="kyutai-mlx"):
        MlxAudioModule({})


def test_the_module_builds_where_the_package_is_installed(monkeypatch) -> None:
    monkeypatch.setattr(mlx_module, "_on_apple_silicon", lambda: True)
    monkeypatch.setattr(mlx_module, "_mlx_audio_installed", lambda: True)
    assert MlxAudioModule({}).VAD_GATING == "required"


@pytest.mark.parametrize(
    "config, field",
    [
        ({"model": ""}, "model"),
        ({"model": 3}, "model"),
        ({"generate_kwargs": ["language"]}, "generate_kwargs"),
        ({"generate_kwargs": {1: "x"}}, "generate_kwargs"),
        ({"interim_interval_s": -0.5}, "interim_interval_s"),
    ],
)
def test_invalid_config_is_rejected(config: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        MlxAudioModule(config, load_model=FakeLoader())


def test_the_registry_resolves_the_module_without_mlx_audio() -> None:
    assert resolve_module_class("mlx_audio") is MlxAudioModule
