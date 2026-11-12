"""``SileroVAD``: obtaining the model, the real model's behaviour, and the gate
driven by it.

The download logic is tested against a local ``file://`` URL, so it needs no
network. The real-model tests use the model from the user cache, downloading it
on the first run (2.3 MB, once per machine), and **skip** when it cannot be
obtained — the one place the fast tier touches the network (specs/testing.md).
Everything skips where ``onnxruntime`` has no wheel (Intel macOS).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("onnxruntime")

from asr_engine.audio import (  # noqa: E402
    END_OF_SPEECH,
    AudioFormat,
    ScriptableAudioSource,
    _iter_audio_chunks,
)
from asr_engine.vad import resolve_vad_class  # noqa: E402
from asr_engine.vad.gate import VadGate  # noqa: E402
from asr_engine.vad.silero import module as silero  # noqa: E402
from asr_engine.vad.silero.module import SileroVAD  # noqa: E402

FIXTURES = Path(__file__).parents[2] / "tests-e2e" / "fixtures"
SPEECH_16K = FIXTURES / "sample_16000_theskyisblue.wav"  # speech from 0.06 to 1.18 s
SPEECH_44K = FIXTURES / "sample_44100_theskyisblue.mp3"  # speech from 0.03 to 1.09 s


@pytest.fixture(scope="module")
def vad() -> SileroVAD:
    module = SileroVAD({})
    try:
        module.load()
    except RuntimeError as exc:  # offline and not cached yet
        pytest.skip(f"Silero model unavailable: {exc}")
    return module


_REAL_MODEL = silero.cache_dir() / silero.MODEL_FILENAME  # resolved before any patch


def _real_model_bytes() -> bytes:
    return _REAL_MODEL.read_bytes()  # present: the `vad` fixture loaded it


def _scores(vad: SileroVAD, samples: np.ndarray, rate: int) -> list[float]:
    n = vad.frame_samples(rate)
    vad.reset()
    return [
        vad.process_frame(samples[i : i + n], rate)
        for i in range(0, len(samples) - n + 1, n)
    ]


def test_registered_under_silero():
    assert resolve_vad_class("silero") is SileroVAD


# ------------------------------------------------- obtaining the model (offline)


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    """A stand-in for the upstream file, served over ``file://``, and an empty
    cache directory. Returns the upstream file so a test can corrupt it."""
    source = tmp_path / "upstream" / "silero_vad.onnx"
    source.parent.mkdir()
    source.write_bytes(b"model bytes")
    monkeypatch.setattr(silero, "MODEL_URL", source.as_uri())
    monkeypatch.setattr(
        silero, "MODEL_SHA256", hashlib.sha256(b"model bytes").hexdigest()
    )
    monkeypatch.setenv(silero.CACHE_DIR_ENV, str(tmp_path / "cache"))
    return source


def test_model_is_downloaded_once_into_the_cache_directory(upstream, tmp_path):
    path = silero.ensure_model()
    assert path == tmp_path / "cache" / silero.MODEL_FILENAME
    assert path.read_bytes() == b"model bytes"

    upstream.unlink()  # a second call must not need the network
    assert silero.ensure_model() == path
    assert [p.name for p in path.parent.iterdir()] == [silero.MODEL_FILENAME]


def test_a_download_with_the_wrong_hash_is_rejected_and_not_installed(upstream):
    upstream.write_bytes(b"something else")
    with pytest.raises(RuntimeError, match="SHA-256.*model_path"):
        silero.ensure_model()
    assert list(silero.cache_dir().iterdir()) == []  # no model, no leftover .part


def test_a_corrupted_cached_file_is_replaced(upstream):
    path = silero.ensure_model()
    path.write_bytes(b"truncated")
    assert silero.ensure_model().read_bytes() == b"model bytes"


def test_an_unreachable_source_names_the_model_path_way_out(upstream):
    upstream.unlink()
    with pytest.raises(RuntimeError, match="could not download.*model_path"):
        SileroVAD({}).load()


def test_model_path_loads_a_local_copy_without_touching_the_cache(
    vad, upstream, tmp_path
):
    upstream.unlink()  # any download attempt would fail
    local = tmp_path / "my_silero.onnx"
    local.write_bytes(_real_model_bytes())
    module = SileroVAD({"model_path": str(local)})
    module.load()
    assert module.process_frame(np.zeros(512, dtype=np.float32), 16000) < 0.1
    assert not (tmp_path / "cache").exists()


def test_a_missing_model_path_fails_at_load_naming_the_path(tmp_path):
    module = SileroVAD({"model_path": str(tmp_path / "nope.onnx")})
    with pytest.raises(FileNotFoundError, match="nope.onnx"):
        module.load()


@pytest.mark.parametrize("value", ["", 3, False])
def test_an_invalid_model_path_is_rejected_at_construction(value):
    with pytest.raises(ValueError, match="model_path"):
        SileroVAD({"model_path": value})


# ------------------------------------------------------------- the real model


def test_speech_scores_high_and_silence_low(vad):
    speech, rate = sf.read(SPEECH_16K, dtype="float32")
    assert rate == 16000
    scores = _scores(vad, speech, rate)
    # 32 ms frames: frames 5-33 lie well inside the spoken stretch.
    assert min(scores[5:34]) > 0.5
    assert scores[0] < 0.5  # the lead-in before the first word

    silence = _scores(vad, np.zeros(16000, dtype=np.float32), 16000)
    assert max(silence) < 0.1


def test_reset_makes_two_runs_score_identically(vad):
    speech, rate = sf.read(SPEECH_16K, dtype="float32")
    first = _scores(vad, speech, rate)
    second = _scores(vad, speech, rate)  # _scores resets before each run
    assert first == second

    # Without the reset the carried state changes the answer: the state is real.
    n = vad.frame_samples(rate)
    carried = [vad.process_frame(speech[i : i + n], rate) for i in range(0, 5 * n, n)]
    assert carried != first[:5]


def test_frame_size_follows_the_rate_and_8khz_runs(vad):
    assert vad.frame_samples(16000) == 512
    assert vad.frame_samples(8000) == 256
    speech, _ = sf.read(SPEECH_16K, dtype="float32")
    scores = _scores(vad, speech[::2].copy(), 8000)  # crude 8 kHz decimation
    assert max(scores) > 0.5 and scores[0] < 0.5


def test_a_wrong_frame_length_is_rejected(vad):
    with pytest.raises(ValueError, match="512 samples"):
        vad.process_frame(np.zeros(480, dtype=np.float32), 16000)


def test_process_frame_before_load_raises():
    with pytest.raises(RuntimeError, match="before load"):
        SileroVAD({}).process_frame(np.zeros(512, dtype=np.float32), 16000)


def test_unknown_config_field_is_rejected():
    with pytest.raises(ValueError, match="treshold"):
        SileroVAD({"treshold": 0.4})


async def test_gate_with_silero_forwards_the_spoken_stretch_of_a_44khz_file(vad):
    """The whole chain on real speech: a 44.1 kHz pipeline resampled to 16 kHz
    for analysis, the original bytes forwarded, one marker after the utterance."""
    fmt = AudioFormat(sample_rate=44100)
    source = ScriptableAudioSource(fmt, real_time=False)
    gate = VadGate(
        source,
        vad,
        fmt,
        threshold=0.5,
        min_speech_duration_ms=250,
        min_silence_duration_ms=500,
        speech_pad_ms=300,
        emit_end_of_speech=True,
    )
    queue = gate.start()
    try:
        await source.play(SPEECH_44K, trailing_silence_s=1.0)
        items = []
        while True:
            item = await queue.get()
            items.append(item)
            if item is END_OF_SPEECH:
                break
    finally:
        gate.stop()

    forwarded = b"".join(i for i in items if isinstance(i, bytes))
    seconds = len(forwarded) / 2 / 44100
    # About 1.06 s of speech + up to 0.3 s of pre-roll + the 0.5 s hangover; the
    # leading silence the source fed before the file is gone.
    assert 1.4 < seconds < 2.1
    # The bytes are the source's own, untouched: the stretch from 0.3 s to 1.0 s
    # of the file, as the source decodes it, passes through whole. (Compared with
    # the source's block-wise decode: MP3 decoding is not bit-identical between a
    # whole-file read and a streamed one on every platform.)
    played = b"".join(_iter_audio_chunks(SPEECH_44K, fmt))
    assert played[int(0.3 * 44100) * 2 : 44100 * 2] in forwarded
