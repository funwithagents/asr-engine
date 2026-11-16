---
code:
  - src/asr_engine/format_converter.py
  - src/asr_engine/engine.py
  - src/asr_engine/config.py
  - src/asr_engine/audio.py
  - src/asr_engine/modules/base.py
  - pyproject.toml
tests:
  - tests/test_format_converter.py
  - tests/test_engine.py
  - tests/test_config.py
  - tests/modules/test_base.py
  - tests-e2e/test_engine_modules.py
  - tests-e2e/test_mcp_resource.py
---

# Audio Format Conversion

**Status:** Implemented

## Purpose

An audio source and an ASR module do not always agree on a format. A file is recorded at 44.1 kHz and the model takes 24 kHz; a telephony stream is 8 kHz μ-law and the backend wants 16 kHz linear PCM; a device captures at 48 kHz and the user wants to send 16 kHz.

The engine used to require one format for both sides and failed at construction when the module did not support it. It now keeps **two formats** and converts between them:

- the **source format**, `engine.audio_format`: what the audio source produces;
- the **module format**, `engine.module_audio_format`: what the ASR module is fed.

When they are equal nothing changes: no converter is built and the module reads the source's own chunks. When they differ, a converter stage sits between the source and the rest of the pipeline.

```
AudioSource ──► FormatConverter (only when the formats differ) ──► VadGate (optional) ──► ASR module
   source format                          module format                    module format
```

This replaces `engine.audio.on_unsupported_format`: an unsupported format is no longer an error to handle, it is converted.

## Configuration

Three places describe the two formats ([configuration.md](configuration.md) holds the field tables).

| Field | Describes | Default |
|---|---|---|
| `engine.audio.sample_rate`, `channels` | The source: the rate and channel count it produces | `null`: the source has none of its own (see below) |
| `engine.audio.encoding` | The source's encoding | `"linear16"` |
| `engine.module.sample_rate` | The module: the rate it is fed | `null`: the engine chooses |

A module can also say which rates it accepts. That is a class declaration (`SUPPORTED_SAMPLE_RATES`, `DEFAULT_SAMPLE_RATE`), except for a protocol module whose servers differ: `openai-realtime-compatible` reads `supported_sample_rates` and `default_sample_rate` from its own config ([below](#per-instance-rates)).

### The source format

| Source | `sample_rate` / `channels` unset (`null`) | `sample_rate` / `channels` set |
|---|---|---|
| Microphone (`AudioCapture`) | The module's: the device is opened at the module format, and no converter is needed | The device is opened at that rate and channel count |
| File (`audio.audio_file`) | The file's own, read from its header at construction | A check: a file that differs is a `ValueError` at construction |
| Injected `AudioSource` | The module's: the source produces the module format, as before | What the source produces |

- A file's rate and channel count are facts. Setting them in `engine.audio` cannot change them, so a set value only verifies the file is the one expected.
- A missing or unreadable `audio_file` fails at construction (`FileNotFoundError` / `ValueError`), not at the first `start()`.
- `encoding` always has a value. The bundled microphone and file sources capture or decode s16 and encode to it themselves.

### The module format

Resolved per dimension by `resolve_module_audio_format(module, sample_rate=, channels=, encoding=, requested_sample_rate=)` in `modules/base.py`:

- **Rate.**
  1. `engine.module.sample_rate` when set. It must be a rate the module supports, otherwise `ValueError` at construction listing the supported rates.
  2. Otherwise the source's rate, when the source has one and the module supports it. No resampling.
  3. Otherwise the module's default rate.
- **Channels.** The source's count when the module supports it, otherwise the module's default.
- **Encoding.** The source's encoding when the module supports it, otherwise the module's default.

A source with no rate or channel count of its own then takes the module's, so the two formats are equal.

Examples, with no `engine.module.sample_rate`:

| Source | Module | Module format | Converted? |
|---|---|---|---|
| Microphone, nothing set | `kyutai` (24 kHz only) | 24 kHz | No: the device is opened at 24 kHz |
| 44.1 kHz file | `deepgram-v1` (takes 44.1 kHz) | 44.1 kHz | No |
| 44.1 kHz file | `openai-realtime-compatible` (24 kHz only) | 24 kHz | Yes |
| 44.1 kHz stereo file | `mlx-audio` (16 kHz mono) | 16 kHz mono | Yes: downmix and resample |
| Microphone at `audio.sample_rate: 48000` | `deepgram-v1` with `module.sample_rate: 16000` | 16 kHz | Yes |
| Injected 8 kHz `mulaw` source (`audio` says so) | `mistral-realtime` (`linear16`, 16/24/44.1 kHz) | 16 kHz `linear16` | Yes: decode and resample |

So `kyutai` and `openai-realtime-compatible` need no `sample_rate` anywhere, and `engine.module.sample_rate` is only for choosing a rate other than the one the engine would pick.

### Per-instance rates

`ASRModule` exposes two instance properties the resolution reads: `supported_sample_rates` and `default_sample_rate`. They return the class attributes by default.

A protocol module pointed at another server overrides them from its config through `parse_sample_rate_overrides(config, cls, label)`:

| Config | Supported rates | Default rate |
|---|---|---|
| neither field | the class's | the class's |
| `supported_sample_rates: [...]` | that list | the class default if the list holds it; else the list's only value; a longer list needs `default_sample_rate` |
| `default_sample_rate: N` | `{N}` | `N` |
| both | that list | `N`, which must be in the list |

A malformed field or a default outside the list is a `ValueError` at module construction. Only `openai-realtime-compatible` uses this today ([modules/openai-realtime-compatible.md](modules/openai-realtime-compatible.md)).

## The converter

`src/asr_engine/format_converter.py` holds two classes.

**`AudioFormatConverter(source, target)`** is the conversion itself: stateful, synchronous, no queue. `convert(chunk) -> bytes` takes the next chunk of the stream and returns it in the target format. In order:

1. **Whole frames only.** Bytes of an incomplete frame (one sample per channel) are carried to the next chunk.
2. **Decode** `mulaw` to s16.
3. **Channels.** Several channels to mono: the average. Mono to several: the sample duplicated on each. Any other mapping is refused (`check_convertible` raises `ValueError`, which the engine surfaces at construction).
4. **Resample** with `soxr`'s streaming resampler (int16, its default "high quality" setting), imported only when the rates differ.
5. **Encode** to `mulaw` when the target asks for it.

The resampler keeps its filter state across chunks, so the output is one continuous signal whatever the chunk sizes. It holds back a short tail until the next chunk arrives: 5 to 30 ms for the usual rate pairs, about 120 ms for 48 kHz to 8 kHz (measured). The cost is about 0.02 ms per 100 ms chunk on the reference machine.

`frames_in` and `frames_out` count the frames read and written. They map a position in the converted stream back to the source's, which a timeline of the audio will need (see [Open questions](#open-questions)).

**`ConvertedAudioSource(inner, source_format, target_format)`** wraps an `AudioSource` and has the same shape, like `VadGate`:

- `start()` starts the inner source, and returns a fresh unbounded `asyncio.Queue[bytes]` fed with the converted chunks by a task.
- `stop()` cancels the task and stops the inner source.
- Each `start()` builds a new `AudioFormatConverter`, so no audio of one run leaks into the next.
- A chunk that fails to convert is dropped and the stream goes on. The failure is logged once, with its traceback.

Output chunk sizes vary slightly from one chunk to the next. The module contract already accepts any chunk size.

## Engine integration

- **Construction.** After building the module, the engine resolves both formats (`ASREngine._resolve_formats`): it reads the file header when the source is a file, applies the rules above, and checks the pair is convertible. Every error surfaces here.
- **Properties.** `audio_format` is the source format, and stays what an injected source must produce. `module_audio_format` is new. Both are valid right after construction.
- **Start.** When the formats differ, the selected source (microphone, file or injected) is wrapped in a `ConvertedAudioSource`, and one line is logged at INFO naming both formats. The VAD gate, when configured, wraps the converter.
- **The module** receives `audio_format=module_audio_format` in `start()`, as before.
- **Stop** stops the outermost wrapper, which stops what it wraps, down to the source.

### Why before the VAD gate

- The converter sees a continuous stream of `bytes` and never handles the end-of-speech marker.
- The gate is unchanged. It is built with the module format, and "pipeline format" in [vad.md](vad.md) now means the module format.
- After the gate, the converter would have to flush the resampler's tail at every marker, or the last milliseconds of an utterance would stay held until the next one. Batch modules decode on the marker, so they would lose them.

The cost: silence is converted too (negligible), and when the gate must itself resample for its VAD, its analysis copy is resampled twice.

### What does not change

- **The `AudioSource` protocol.** A source still puts `bytes` in `engine.audio_format` on a queue it owns.
- **The `ASRModule` interface.** Modules still read `bytes` (and the marker) in the format `start()` names.
- **A configuration that worked.** A config whose formats agreed resolves to the same single format. `engine.audio.on_unsupported_format` is no longer read: a config that still carries it loads, with a warning.

## Dependency

`soxr` (0.2 MB, numpy only) moves from the `silero` extra to the core dependencies, since the default behaviour depends on it. It publishes wheels for every supported platform. The VAD gate uses the same package for its analysis copy, so the engine no longer checks for it at construction.

## Testing

Fast tier:

- **The converter** (`tests/test_format_converter.py`): a tone resampled between several rate pairs keeps its duration, pitch and level; the output does not depend on the chunking; a frame split across chunks is reassembled; exact downmix and duplication; `mulaw` both ways; all three dimensions at once; the frame counters; a refused channel layout; the wrapper's order, restart and failure behaviour.
- **Resolution** (`tests/modules/test_base.py`): each rule of the module format, and each row of the per-instance rates table.
- **The engine** (`tests/test_engine.py`): both formats for each source kind; a file's header read and checked; errors at construction; a hand-fed 44.1 kHz source reaching a recording module as 16 kHz audio of the same duration and pitch; equal formats reaching the module untouched; the gate analysing the converted audio.
- **Config** (`tests/test_config.py`): the new fields, their validation, and the warning on the removed one.

Live tier ([e2e-testing.md](e2e-testing.md)):

- `test_engine_modules_converted`: each module that is not fed 44.1 kHz recognizes the 44.1 kHz fixture through the converter. Only a live backend shows that converted speech is still intelligible.
- One keyless scenario through the real server: a 44.1 kHz file, no rate in `engine.audio`, and a `fake` module fed 16 kHz.

## Open questions

All deferrals.

1. **A timeline through the converter.** The staged-pipeline work (spans on utterances) needs positions on one audio timeline. The frame counters are the hook; nothing reads them yet.
2. **`engine.module.channels` / `encoding`.** Only the rate can be chosen for the module today. The other two follow the source or the module's default. Add them when a use appears (sending `mulaw` to Deepgram from a linear source, to halve the upload).
3. **Per-instance rates on other modules.** `openai-transcriptions-compatible` keeps the batch base's 16 kHz: its servers resample whatever they receive, so another rate buys nothing.
4. **`ScriptableAudioSource` and mixed files.** It still plays only files recorded in its own format. It could convert each file on `play()`.
5. **Resampler quality as a setting.** `soxr`'s default is used. A lower-latency setting could matter for 48 kHz to 8 kHz.
