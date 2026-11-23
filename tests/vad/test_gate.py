"""``VadGate`` on a deterministic VAD and a hand-fed source (specs/vad.md).

The scripted VAD has 10 ms frames and calls a frame speech when it is loud, so
each test places speech by what it feeds and every boundary is a round number.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from asr_engine.audio import END_OF_SPEECH, AudioFormat, linear16_to_mulaw
from asr_engine.segment_timing import GateSpeechTiming
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


async def test_state_follows_the_gate_open_and_closed():
    rig = Rig()
    assert rig.gate.state() == VadState()

    await rig.feed(pcm(quiet(200), loud(100)))  # open, mid-utterance
    assert rig.gate.state() == VadState(speaking=True)

    await rig.feed(pcm(quiet(300, seed=3)))  # closed at 350 ms
    assert rig.gate.state() == VadState()

    await rig.feed(pcm(loud(100, seed=4)))  # open again
    assert rig.gate.state().speaking
    rig.gate.stop()
    assert rig.gate.state() == VadState()  # not speaking once stopped


async def test_state_after_a_failure_reports_failed_open():
    rig = Rig(vad=ScriptedVAD({"fail_at_frame": 25}))
    await rig.feed(pcm(quiet(1000)))
    assert rig.gate.state() == VadState(speaking=True, failed_open=True)
    rig.gate.stop()


# ---------------------------------------------------------------------------
# Speech timing (specs/segment-timing.md) — on a scripted wall clock
# ---------------------------------------------------------------------------


async def feed_paced(rig: Rig, clock, data: bytes, chunk_ms: int = 100) -> None:
    """Feed *data* as a real-time source would: each chunk is received when its
    last sample has been captured, so the audio at X ms since the first call
    happened at ``clock.at_ms(X)``."""
    size = chunk_ms * BYTES_PER_MS
    for i in range(0, len(data), size):
        chunk = data[i : i + size]
        clock.advance_ms(len(chunk) / BYTES_PER_MS)
        rig.source.queue.put_nowait(chunk)
        await settle()


async def test_speech_is_stamped_at_its_first_and_last_frame_not_at_the_gate(clock):
    rig = Rig()
    await feed_paced(rig, clock, UTTERANCE)

    # Speech is 200-300 ms. The gate opened at 230 ms and forwarded from 180 ms;
    # it closed on the frame ending at 350 ms, in the chunk received at 400 ms.
    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200),
        ended_ns=clock.at_ms(300),
        closed_ns=clock.at_ms(400),
    )


async def test_frames_are_placed_by_audio_time_inside_a_chunk(clock):
    """One 600 ms chunk, scored in one burst when it is received: each frame is
    placed by the samples that follow it, not by when it was scored."""
    rig = Rig()
    await feed_paced(rig, clock, UTTERANCE, chunk_ms=600)

    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200),
        ended_ns=clock.at_ms(300),
        closed_ns=clock.at_ms(600),
    )


async def test_no_speech_detected_gives_no_timing(clock):
    rig = Rig()
    await feed_paced(rig, clock, pcm(quiet(300), loud(20), quiet(300, seed=3)))

    assert rig.gate.take_speech_timing() is None  # 20 ms never opened the gate


async def test_speech_without_a_pause_yet_has_a_start_and_no_end(clock):
    rig = Rig()
    await feed_paced(rig, clock, pcm(quiet(200), loud(200)))

    # A final arriving now is mid-speech: nothing has ended.
    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200), ended_ns=None, closed_ns=None
    )

    # The run was left for the next final, which gets its start and its end.
    await feed_paced(rig, clock, pcm(quiet(100, seed=3)))
    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200),
        ended_ns=clock.at_ms(400),
        closed_ns=clock.at_ms(500),
    )


async def test_an_interim_reads_the_start_of_the_speech_without_claiming_it(clock):
    rig = Rig()
    assert rig.gate.peek_speech_start() is None  # nothing detected yet
    await feed_paced(rig, clock, pcm(quiet(200), loud(200)))

    assert rig.gate.peek_speech_start() == clock.at_ms(200)
    assert rig.gate.peek_speech_start() == clock.at_ms(200)  # as often as asked
    # The final that follows is still given the whole run.
    await feed_paced(rig, clock, pcm(quiet(100, seed=3)))
    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200),
        ended_ns=clock.at_ms(400),
        closed_ns=clock.at_ms(500),
    )


async def test_a_final_during_the_hangover_ends_at_the_last_speech_frame(clock):
    """A module with its own end of turn finalises before the gate closes."""
    rig = Rig()
    await feed_paced(rig, clock, pcm(quiet(200), loud(100), quiet(30)), chunk_ms=10)

    assert rig.gate.state().speaking  # 30 ms of silence: still open
    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200), ended_ns=clock.at_ms(300), closed_ns=None
    )


async def test_speech_resumed_before_the_final_keeps_the_earlier_end(clock):
    """The person starts again after a short pause, and the previous turn's
    final arrives then: its speech ended before the pause."""
    rig = Rig()
    audio = pcm(quiet(200), loud(100), quiet(30), loud(50, seed=7))
    await feed_paced(rig, clock, audio, chunk_ms=10)

    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200), ended_ns=clock.at_ms(300), closed_ns=None
    )


async def test_a_late_final_is_given_the_closed_run_not_the_speech_that_followed(
    clock,
):
    rig = Rig()
    second = pcm(quiet(100, seed=4), loud(100, seed=5))
    await feed_paced(rig, clock, UTTERANCE + second)  # 700-800 ms: speaking again

    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200),
        ended_ns=clock.at_ms(300),
        closed_ns=clock.at_ms(400),
    )

    # The second run belongs to the next final.
    await feed_paced(rig, clock, pcm(quiet(100, seed=6)))
    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(700),
        ended_ns=clock.at_ms(800),
        closed_ns=clock.at_ms(900),
    )


async def test_several_runs_before_one_final_span_the_first_start_to_the_last_end(
    clock,
):
    rig = Rig()
    await feed_paced(rig, clock, UTTERANCE + UTTERANCE)

    assert rig.gate.take_speech_timing() == GateSpeechTiming(
        started_ns=clock.at_ms(200),
        ended_ns=clock.at_ms(900),
        closed_ns=clock.at_ms(1000),
    )


async def test_a_second_final_for_the_same_speech_is_given_the_same_run(clock):
    """A backend may split one stretch of speech into several finals."""
    rig = Rig()
    await feed_paced(rig, clock, UTTERANCE)

    first = rig.gate.take_speech_timing()
    assert first is not None and rig.gate.take_speech_timing() == first


async def test_stamps_never_run_backwards_when_chunks_arrive_in_a_burst(clock):
    """A blocked loop delivers several chunks at once: all are stamped at the
    same instant, and the order of the stamps still holds."""
    rig = Rig()
    await rig.feed(UTTERANCE)  # six chunks, one clock value

    timing = rig.gate.take_speech_timing()
    assert timing is not None and timing.ended_ns is not None
    assert timing.started_ns <= timing.ended_ns <= clock.now
    assert timing.closed_ns == clock.now


async def test_timing_holds_through_resampling_within_one_frame(clock):
    """A 44.1 kHz pipeline analysed at 16 kHz: the stamps follow the pipeline's
    audio, to the frame."""
    rate = 44100
    rig = Rig(AudioFormat(sample_rate=rate), vad=Scripted16kVAD({}))
    audio = pcm(quiet(200, rate), loud(100, rate), quiet(300, rate, seed=3))
    chunk = rate // 10 * 2  # 100 ms
    for i in range(0, len(audio), chunk):
        clock.advance_ms(100)
        rig.source.queue.put_nowait(audio[i : i + chunk])
        await settle()

    timing = rig.gate.take_speech_timing()
    assert timing is not None and timing.ended_ns is not None
    frame_ns = 10_000_000
    assert abs(timing.started_ns - clock.at_ms(200)) <= frame_ns
    assert abs(timing.ended_ns - clock.at_ms(300)) <= frame_ns


async def test_a_gate_that_failed_open_gives_no_timing(clock):
    rig = Rig(vad=ScriptedVAD({"fail_at_frame": 45}))
    await feed_paced(rig, clock, UTTERANCE)  # fails at 450 ms, after the run closed

    assert rig.gate.state().failed_open
    assert rig.gate.take_speech_timing() is None
    assert rig.gate.peek_speech_start() is None


async def test_a_restart_forgets_the_previous_run_s_speech(clock):
    rig = Rig()
    await feed_paced(rig, clock, UTTERANCE)
    rig.gate.stop()
    rig.gate.start()

    assert rig.gate.take_speech_timing() is None
