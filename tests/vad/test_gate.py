"""``VadGate`` on a deterministic VAD and a hand-fed source (specs/vad.md).

The scripted VAD has 10 ms frames and calls a frame speech when it is loud, so
each test places speech by what it feeds and every boundary is a round number.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from asr_engine.audio import END_OF_SPEECH, AudioFormat, linear16_to_mulaw
from asr_engine.vad.gate import VadGate, VadState, analysis_sample_rate
from tests.vad.scripted import (
    FORMAT_16K,
    HandFedSource,
    Scripted16kVAD,
    ScriptedVAD,
    loud,
    pcm,
    quiet,
    settle,
)

# Gate parameters used throughout: open after 30 ms of speech, close after 50 ms
# of silence, keep 20 ms of pre-roll.
GATE = dict(
    threshold=0.5,
    min_speech_duration_ms=30,
    min_silence_duration_ms=50,
    speech_pad_ms=20,
)
BYTES_PER_MS = 32  # 16 kHz mono linear16


class Rig:
    """A gate over a hand-fed source, with what it forwarded."""

    def __init__(
        self,
        audio_format: AudioFormat = FORMAT_16K,
        *,
        vad: ScriptedVAD | None = None,
        emit_end_of_speech: bool = True,
        **gate_overrides: int,
    ) -> None:
        self.source = HandFedSource()
        self.vad = vad or ScriptedVAD({})
        self.gate = VadGate(
            self.source,
            self.vad,
            audio_format,
            emit_end_of_speech=emit_end_of_speech,
            **{**GATE, **gate_overrides},  # type: ignore[arg-type]
        )
        self.out: asyncio.Queue = self.gate.start()

    async def feed(self, data: bytes, chunk_bytes: int = 3200) -> None:
        for i in range(0, len(data), chunk_bytes):
            self.source.queue.put_nowait(data[i : i + chunk_bytes])
        await settle()

    def items(self) -> list:
        items = []
        while not self.out.empty():
            items.append(self.out.get_nowait())
        return items


def audio_of(items: list) -> bytes:
    return b"".join(i for i in items if isinstance(i, bytes))


# One utterance: 200 ms quiet, 100 ms loud, 300 ms quiet. Speech spans frames
# 20-29. The gate opens on the third speech frame (22) and forwards the ring:
# 2 pre-roll frames + the 3 that confirmed the onset, i.e. from 180 ms. It closes
# after the fifth silent frame (34), i.e. at 350 ms.
UTTERANCE = pcm(quiet(200), loud(100), quiet(300, seed=3))
EXPECTED = UTTERANCE[180 * BYTES_PER_MS : 350 * BYTES_PER_MS]


async def test_forwards_speech_with_preroll_and_hangover_then_one_marker():
    rig = Rig()
    await rig.feed(UTTERANCE)
    items = rig.items()

    assert audio_of(items) == EXPECTED  # the source's own bytes, nothing else
    assert items[-1] is END_OF_SPEECH
    assert sum(i is END_OF_SPEECH for i in items) == 1
    rig.gate.stop()


async def test_silence_is_dropped_entirely():
    rig = Rig()
    await rig.feed(pcm(quiet(1000)))
    assert rig.items() == []
    rig.gate.stop()


async def test_a_burst_shorter_than_min_speech_does_not_open_the_gate():
    rig = Rig()
    await rig.feed(pcm(quiet(200), loud(20), quiet(300)))  # 2 frames < 3
    assert rig.items() == []
    rig.gate.stop()


async def test_a_pause_shorter_than_min_silence_does_not_close_the_gate():
    # 100 ms speech, a 40 ms pause (4 frames < 5), 100 ms speech: one utterance.
    stream = pcm(
        quiet(200), loud(100), quiet(40, seed=5), loud(100, seed=6), quiet(300)
    )
    rig = Rig()
    await rig.feed(stream)
    items = rig.items()

    # Opens at 180 ms as above; the second burst ends at 440 ms; closes at 490 ms.
    assert audio_of(items) == stream[180 * BYTES_PER_MS : 490 * BYTES_PER_MS]
    assert [i for i in items if not isinstance(i, bytes)] == [END_OF_SPEECH]
    rig.gate.stop()


async def test_each_utterance_is_followed_by_its_own_marker():
    second = pcm(quiet(200, seed=7), loud(60, seed=8), quiet(300, seed=9))
    rig = Rig()
    await rig.feed(UTTERANCE + second)
    items = rig.items()

    markers = [n for n, i in enumerate(items) if i is END_OF_SPEECH]
    assert len(markers) == 2 and markers[-1] == len(items) - 1
    assert audio_of(items[: markers[0]]) == EXPECTED
    # Second utterance: speech at 200-260 ms of `second` → forwarded 180-310 ms.
    assert audio_of(items[markers[0] + 1 :]) == second[180 * 32 : 310 * 32]
    # The VAD's streaming state is cleared between utterances.
    assert rig.vad.resets == 1 + 2  # start() + one per closed utterance
    rig.gate.stop()


async def test_no_marker_when_end_of_speech_is_disabled():
    rig = Rig(emit_end_of_speech=False)
    await rig.feed(UTTERANCE)
    items = rig.items()
    assert audio_of(items) == EXPECTED
    assert all(isinstance(i, bytes) for i in items)
    rig.gate.stop()


async def test_stop_mid_utterance_emits_no_marker_and_stops_the_source():
    rig = Rig()
    await rig.feed(pcm(quiet(200), loud(100)))  # still speaking
    rig.gate.stop()
    await settle()

    items = rig.items()
    assert items and all(isinstance(i, bytes) for i in items)
    assert rig.source.stops == 1


@pytest.mark.parametrize("chunk_bytes", [2, 223, 640, 1001, 6400])
async def test_chunk_sizes_off_the_frame_grid_lose_and_duplicate_nothing(chunk_bytes):
    # Includes odd sizes, which split a 16-bit sample across two chunks.
    rig = Rig()
    await rig.feed(UTTERANCE, chunk_bytes=chunk_bytes)
    items = rig.items()
    assert audio_of(items) == EXPECTED
    assert items[-1] is END_OF_SPEECH
    rig.gate.stop()


async def test_forwarded_audio_is_chunked_to_about_chunk_ms():
    # A 300 ms pre-roll comes out at the onset in one go: it must be split.
    rig = Rig(speech_pad_ms=300)
    await rig.feed(pcm(quiet(500), loud(400), quiet(300)), chunk_bytes=320)
    chunks = [i for i in rig.items() if isinstance(i, bytes)]

    full = FORMAT_16K.chunk_bytes  # 100 ms
    assert max(len(c) for c in chunks) < 2 * full
    assert len(chunks[0]) == full  # the pre-roll was cut into whole chunks
    assert len(b"".join(chunks)) == (300 + 400 + 50) * BYTES_PER_MS
    rig.gate.stop()


# ---------------------------------------------------------------------- formats


async def test_a_supported_pipeline_rate_is_analysed_as_is_without_resampling():
    samples = np.concatenate([quiet(200), loud(100), quiet(300, seed=3)])
    rig = Rig()
    await rig.feed(samples.tobytes())

    assert analysis_sample_rate(ScriptedVAD, 16000) == 16000
    assert set(rig.vad.rates) == {16000}
    assert all(f.shape == (160,) and f.dtype == np.float32 for f in rig.vad.frames)
    # Exactly the source samples, scaled to [-1, 1]: no resampler in the path.
    seen = np.concatenate(rig.vad.frames)
    np.testing.assert_array_equal(seen, samples[: len(seen)].astype(np.float32) / 32768)
    rig.gate.stop()


async def test_mulaw_at_44100_is_analysed_at_16k_while_the_original_bytes_pass():
    rate = 44100
    fmt = AudioFormat(sample_rate=rate, encoding="mulaw")
    stream = linear16_to_mulaw(
        pcm(quiet(300, rate), loud(200, rate), quiet(400, rate, seed=3))
    )
    rig = Rig(fmt, vad=Scripted16kVAD({}))
    await rig.feed(stream, chunk_bytes=fmt.chunk_bytes)
    items = rig.items()

    assert analysis_sample_rate(Scripted16kVAD, rate) == 16000
    assert set(rig.vad.rates) == {16000}
    assert all(f.shape == (160,) and f.dtype == np.float32 for f in rig.vad.frames)

    # The forwarded bytes are one contiguous stretch of the original μ-law stream…
    forwarded = audio_of(items)
    start = stream.find(forwarded)
    assert forwarded and start >= 0
    # …covering the speech with its pre-roll and hangover, to within one frame
    # plus the resampler's few milliseconds of delay.
    per_ms = rate / 1000  # 1 byte per μ-law sample
    assert start == pytest.approx(280 * per_ms, abs=15 * per_ms)
    assert start + len(forwarded) == pytest.approx(550 * per_ms, abs=15 * per_ms)
    assert items[-1] is END_OF_SPEECH
    rig.gate.stop()


async def test_stereo_is_downmixed_for_analysis_only():
    fmt = AudioFormat(sample_rate=16000, channels=2)
    left = np.concatenate([quiet(200), loud(100), quiet(300, seed=3)])

    def interleave(a: np.ndarray, b: np.ndarray) -> bytes:
        return np.stack([a, b], axis=1).astype("<i2").tobytes()

    # Both channels in phase: the mono mix is loud and the gate opens. Every cut
    # lands on a whole stereo sample, and the stereo bytes pass through.
    in_phase = interleave(left, left)
    rig = Rig(fmt)
    await rig.feed(in_phase)
    assert audio_of(rig.items()) == in_phase[180 * 64 : 350 * 64]
    assert all(f.shape == (160,) for f in rig.vad.frames)
    rig.gate.stop()

    # Opposite phase: the channels average to silence, so nothing opens it —
    # the analysis copy really is the mean of the channels, not one of them.
    rig = Rig(fmt)
    await rig.feed(interleave(left, -left))
    assert rig.items() == []
    rig.gate.stop()


# -------------------------------------------------------------------- lifecycle


async def test_restart_returns_a_fresh_queue_in_the_silence_state():
    rig = Rig()
    await rig.feed(pcm(quiet(200), loud(100)))  # left open mid-utterance
    first_queue = rig.out
    rig.gate.stop()

    rig.out = rig.gate.start()
    assert rig.out is not first_queue and rig.out.empty()
    assert rig.source.starts == 2

    # Silence right after the restart: the previous run's open gate is forgotten.
    await rig.feed(pcm(quiet(300)))
    assert rig.items() == []
    await rig.feed(UTTERANCE)
    # Frame positions count from the restart: 300 ms of quiet came first.
    restarted = pcm(quiet(300)) + UTTERANCE
    assert audio_of(rig.items()) == restarted[480 * 32 : 650 * 32]
    rig.gate.stop()


async def test_a_vad_failure_fails_open_and_forwards_everything_after_it(caplog):
    # The VAD raises on its 26th frame (250-260 ms), with the gate closed.
    stream = pcm(quiet(1000))
    rig = Rig(vad=ScriptedVAD({"fail_at_frame": 25}))
    await rig.feed(stream)
    items = rig.items()

    # Everything from the failing frame on is forwarded, ungated and unmarked.
    assert audio_of(items) == stream[250 * BYTES_PER_MS :]
    assert all(isinstance(i, bytes) for i in items)
    assert "VAD gate failed" in caplog.text

    # And it stays open: later audio passes straight through.
    await rig.feed(b"\x01\x02" * 800)
    assert audio_of(rig.items()) == b"\x01\x02" * 800
    rig.gate.stop()


async def test_a_failure_mid_utterance_keeps_the_speech_already_forwarded():
    # Fails on frame 25, with the gate open since frame 22.
    rig = Rig(vad=ScriptedVAD({"fail_at_frame": 25}))
    await rig.feed(UTTERANCE)
    # From the pre-roll (180 ms) to the end, each byte exactly once.
    assert audio_of(rig.items()) == UTTERANCE[180 * BYTES_PER_MS :]
    rig.gate.stop()


# ------------------------------------------------------------------- state()


async def test_state_reports_speech_and_counts_received_and_forwarded_audio():
    rig = Rig()
    assert rig.gate.state() == VadState()

    await rig.feed(pcm(quiet(200), loud(100)))  # open, mid-utterance
    st = rig.gate.state()
    assert st.speaking and not st.failed_open and st.utterances == 0
    assert st.received_s == pytest.approx(0.3)
    assert st.forwarded_s == pytest.approx(0.12)  # from 180 ms (pre-roll) to 300 ms

    await rig.feed(pcm(quiet(300, seed=3)))  # closes at 350 ms
    st = rig.gate.state()
    assert not st.speaking and st.utterances == 1
    assert st.received_s == pytest.approx(0.6)
    assert st.forwarded_s == pytest.approx(0.17)
    assert st.forwarded_s == pytest.approx(len(audio_of(rig.items())) / 32000)

    rig.gate.stop()
    assert rig.gate.state().utterances == 1  # totals kept after stop
    rig.out = rig.gate.start()
    assert rig.gate.state() == VadState()  # and reset by the next run


async def test_state_after_a_failure_reports_failed_open_and_counts_everything():
    rig = Rig(vad=ScriptedVAD({"fail_at_frame": 25}))
    await rig.feed(pcm(quiet(1000)))
    st = rig.gate.state()
    assert st.failed_open and st.speaking
    assert st.forwarded_s == pytest.approx(0.75)  # from the failing frame on
    rig.gate.stop()
