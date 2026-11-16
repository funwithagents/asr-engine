---
code:
  - src/asr_engine/audio.py
  - src/asr_engine/engine.py
tests:
  - tests/test_audio.py
  - tests/test_engine.py
---

# Audio Source Extension Point

**Status:** Implemented

## Purpose

`AudioSource` is the **supported, stable public seam** for feeding an `ASREngine` audio from something other than the local microphone or a file — an external, already-captured PCM stream. A program that `import asr_engine` implements the Protocol and passes an instance to `ASREngine(config, audio_source=...)`; the engine then drives that source instead of opening a `sounddevice` `InputStream`.

The motivating case: an on-device voice processor (e.g. a robot's echo-cancelled, beamformed mic array) exposes a processed audio stream that **must** be read from its own daemon rather than the raw USB device. A caller bridges that stream into a small custom `AudioSource` and injects it — **no new capture backend in `asr_engine`, no hardware dependency**. The engine stays hardware-agnostic; the hardware-specific source lives in the caller's code.

This spec blesses that seam as public API and pins the contract a custom source must honor. The bundled implementations (`AudioCapture` — the default mic source; `FileAudioSource`; `ScriptableAudioSource`) all satisfy it. The end-to-end audio-format contract lives in [asr-module-interface.md](asr-module-interface.md) and [architecture.md](architecture.md); this spec is the source-side view.

## The Protocol (Decided)

```python
class AudioSource(Protocol):
    def start(self) -> asyncio.Queue[bytes]: ...
    def stop(self) -> None: ...

    # Optional: the format of the chunks the source produces.
    audio_format: AudioFormat
```

- **Injection:** `ASREngine(config, *, audio_source: AudioSource | None = None)`, with `engine.audio_source.type` set to `"custom"` in the config ([configuration.md](configuration.md)). The two must agree: a source passed with another type, or `"custom"` with no source, is a `ValueError` at construction. So a passed source never silently replaces a configured device or file, and a missing one never silently becomes the microphone. With `"microphone"` or `"file"`, the engine builds an `AudioCapture` or a `FileAudioSource` itself.
- **`audio_format` (optional attribute).** A source that knows the format of its stream declares it as an `AudioFormat`, readable before `start()`. The engine takes it as `engine.audio_format` and converts it for the module when the module is fed another format ([format-conversion.md](format-conversion.md)). A source without the attribute produces the format the engine resolved (see the [chunk contract](#chunk-contract)). The bundled sources all declare theirs. Because it is optional, it is not part of the `typing.Protocol` class in the code: the engine reads it with `getattr`, and anything other than an `AudioFormat` is a `TypeError` at construction.
- **Stability:** this is the blessed extension point. The Protocol surface (`start()` returning the queue, `stop()`, the optional `audio_format`) is kept **stable**; a breaking change to it is a versioned change, not a silent one. Callers may depend on it long-term.
- **`start()`** opens/arms the source, spawns whatever background work feeds audio, and **returns the `asyncio.Queue[bytes]` it owns and will feed**. The engine reads this queue (via the ASR module) — it does not create or wrap it.
- **`stop()`** halts feeding and releases the source's resources.
- **VAD does not change this contract.** When `engine.vad` is configured the engine wraps the source — injected or not — in its internal speech gate ([vad.md](vad.md)), which reads the source's queue (or the format converter's, when the engine converts) and feeds the ASR module from a queue of its own. The source still puts only `bytes` on an `asyncio.Queue[bytes]`; the gate's end-of-speech marker never appears on a source's queue, and a source cannot emit one.

## Chunk contract

Each queue element is **one chunk of raw PCM bytes** in the engine's source format, `engine.audio_format`:

- **Encoding** matches `engine.audio_format.encoding`: `linear16` is **signed 16-bit little-endian** PCM; `mulaw` is G.711 μ-law (1 byte/sample).
- **Channels** match `engine.audio_format.channels` — **mono** unless `channels > 1`, in which case samples are interleaved.
- **Sample rate** matches `engine.audio_format.sample_rate`.
- **Chunk size:** **any reasonable size the backend can drain is accepted.** The `~CHUNK_MS` / `AudioFormat.frames_per_chunk` figure is the *target* the bundled mic/file sources chunk to (it sets `AudioCapture`'s `blocksize` and the file block size), **not** a requirement imposed on a custom source. The ASR module forwards whatever bytes arrive on the queue straight to the backend.
- **The source must produce chunks in `engine.audio_format`.** There are two ways to honour that:
  - **Declare the format** (the `audio_format` attribute). `engine.audio_format` is then exactly what the source declared, whatever module is selected, and the engine converts. This is the way for a stream with a fixed format (a robot's 48 kHz microphone array, 8 kHz telephony audio).
  - **Declare nothing, and read `engine.audio_format`.** It is then the module's format, which depends on the module (16 kHz for most, 24 kHz for `kyutai` and `openai-realtime-compatible`), so the source must read it rather than assume a rate. Nothing is converted.

  The source never converts for the module. Each `start()` logs the formats in use.

## `engine.audio_format` is authoritative and available pre-`start()`

The engine resolves the source format in `__init__` ([format-conversion.md](format-conversion.md) "The source format"), from the source's declaration when there is one, and exposes it as the read-only `audio_format` property. It is therefore **valid immediately after construction, before `start()`** — a custom source reads `engine.audio_format` up front to know exactly what to produce (rate/channels/encoding). `engine.module_audio_format` is what the module is fed; a source does not need it.

## Lifecycle

The engine owns the source's lifecycle:

- On capture start (`start()` and `listen()`), the engine calls the source's `start()`, takes the returned queue, and hands it to the ASR module.
- The engine calls the source's `stop()` on `engine.stop()` **and** in `listen()`'s teardown — **always, even on error** (`listen()` stops in a `finally`). A custom source can rely on `stop()` running to unwind its own draining of the upstream stream.
- **The injected source is reused across start/stop cycles.** One engine may `listen()` then `start()` (or repeat either), so the same source instance's `start()` may be called again after `stop()`. `start()` must return a fresh, usable queue on each call; `stop()` must leave the source restartable. The bundled sources all do this (each `start()` allocates a new queue and feeder task).

## Queue ownership & backpressure

- **The source owns the queue** returned by `start()`, so it chooses whether it is bounded or unbounded. The bundled `AudioCapture` and `FileAudioSource` use an unbounded `asyncio.Queue()`; `ScriptableAudioSource` uses a bounded one.
- **The consumer drains continuously.** The ASR module reads the queue while connected, and **keeps draining during reconnect backoff** (it drains-to-discard so the queue can't grow unbounded while the socket is down). So an **unbounded queue fed with `put_nowait` never wedges** the engine — the recommended default for a custom source.
- A source that prefers a **bounded** queue may drop chunks on `QueueFull`; that trade-off (bounded memory vs dropped audio under stall) is the source's own policy, not the engine's.

## Sound feedback on headless / redirected-audio setups

`ASREngine` plays the `listen` start/stop cues itself via `SoundFeedback`, which uses `sounddevice` against `config.sound_feedback.output_device` (see [sound-feedback.md](sound-feedback.md)). When there is no usable local output device, or output must go through a different path than the injected capture, set **`engine.sound_feedback.enabled = false`**: the engine installs `NoOpSoundFeedback`, and `listen()`'s `play_start` / `play_stop` calls become no-ops — **local playback is fully disabled**, no `sounddevice` output is touched. This is the supported way to run the engine with a custom audio source and no local speaker.

## Open questions

- **Custom feedback-player injection (deferred).** A constructor seam to route `listen` cues through a caller-provided player (mirroring `audio_source`), instead of the local `sounddevice` path — so a caller could play cues through the same redirected output as its capture. Deferred: `sound_feedback.enabled = false` is the current answer for headless/redirected setups, and a caller that wants cues can emit its own around `listen`.
