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

Each format is set by an **`audio_format` object** with the same three optional fields, one in each block ([configuration.md](configuration.md) holds the field tables):

```json
{
  "audio":  { "audio_format": { "sample_rate": 48000, "channels": 1, "encoding": "linear16" } },
  "module": { "type": "deepgram-v1", "audio_format": { "sample_rate": 16000 } }
}
```

| Object | Describes | A field left unset (`null` or absent) |
|---|---|---|
| `engine.module.audio_format` | What the module is fed | The module's own default (`DEFAULT_SAMPLE_RATE`, `DEFAULT_CHANNELS`, `DEFAULT_ENCODING`) |
| `engine.audio.audio_format` | What the source produces | The module format's value. For a file, the rate and channel count are the file's own |

In Python both are an `AudioFormatConfig(sample_rate=None, channels=None, encoding=None)`, on `AudioConfig.audio_format` and `ModuleConfig.audio_format`. The resolved formats are `AudioFormat` values.

The dependency runs **one way**: the module format never looks at the source, and the source falls back on the module format.

### The module format

Resolved first, by `resolve_module_audio_format(module, sample_rate=, channels=, encoding=)` in `modules/base.py`:

- each field of `engine.module.audio_format` that is set replaces the module's default;
- a set value must be one the module supports, otherwise `ValueError` at construction listing the supported values.

With no `engine.module.audio_format`, a module is fed its defaults whatever the source is.

### The source format

Each field of `engine.audio.audio_format` that is set is what the source produces. A field left unset takes the module format's value, so by default the source produces exactly what the module is fed and nothing is converted.

| Source | A set field | An unset field |
|---|---|---|
| Microphone (`AudioCapture`) | The device is opened at that rate and channel count; the captured s16 is encoded to that encoding | The module format's |
| Injected `AudioSource` | A statement about the stream the application feeds | The module format's: the source must produce it |
| File (`audio.audio_file`) | `sample_rate` / `channels` are a check: a file that differs is a `ValueError` at construction. `encoding` is what the decoded file is encoded to | `sample_rate` / `channels`: the file's own, read from its header at construction. `encoding`: the module format's |

- A file's rate and channel count are facts that a setting cannot change, so a set value only verifies the file is the one expected.
- A missing or unreadable `audio_file` fails at construction (`FileNotFoundError` / `ValueError`), not at the first `start()`.

Why the source follows the module, instead of a fixed default such as 16 kHz: a microphone can be opened at any rate, so opening it at the module's avoids a conversion and, for a 24 kHz module, avoids capturing less than the model can use.

### Examples

| `audio.audio_format` | Module, `module.audio_format` | Source format | Module format | Converted? |
|---|---|---|---|---|
| unset | `deepgram-v1`, unset | 16 kHz | 16 kHz | No |
| unset | `kyutai` (24 kHz only), unset | 24 kHz | 24 kHz | No: the device is opened at 24 kHz |
| unset, a 44.1 kHz file | `deepgram-v1`, unset | 44.1 kHz | 16 kHz | Yes |
| unset, a 44.1 kHz file | `deepgram-v1`, `{"sample_rate": 44100}` | 44.1 kHz | 44.1 kHz | No |
| unset, a 44.1 kHz stereo file | `mlx-audio`, unset | 44.1 kHz stereo | 16 kHz mono | Yes: downmix and resample |
| `{"sample_rate": 48000}` | `deepgram-v1`, unset | 48 kHz | 16 kHz | Yes |
| unset | `deepgram-v1`, `{"sample_rate": 8000, "encoding": "mulaw"}` | 8 kHz `mulaw` | 8 kHz `mulaw` | No: the source follows |
| `{"sample_rate": 8000, "encoding": "mulaw"}` (injected telephony stream) | `mistral-realtime`, unset | 8 kHz `mulaw` | 16 kHz `linear16` | Yes: decode and resample |

### An injected source and the implicit format

With nothing set, an injected source must produce the module's format, which differs from one module to the next (16 kHz for most, 24 kHz for `kyutai` and `openai-realtime-compatible`). Two things keep that from going wrong silently:

- `engine.audio_format` is valid right after construction, so a source can read what it must produce instead of assuming it;
- every `start()` logs the formats in use at INFO, converted or not.

A source with a fixed format of its own should state it in `engine.audio.audio_format`. The engine then converts for whatever module is selected.

### The older flat fields

`engine.audio.sample_rate`, `channels` and `encoding` used to sit directly in the `audio` block. They are still read, with a warning naming `engine.audio.audio_format`; setting both forms is an error. `engine.audio.on_unsupported_format` is no longer read: a config that still carries it loads, with a warning.

### Per-instance rates

`ASRModule` exposes two instance properties the resolution reads: `default_sample_rate` (the rate the module is fed unless `engine.module.audio_format.sample_rate` is set) and `supported_sample_rates` (what that field may be set to). They return the class attributes by default.

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

- **Construction.** After building the module, the engine resolves both formats (`ASREngine._resolve_formats`): the module's first, then the source's, by the rules above. It then checks the pair is convertible. Every error surfaces here.
- **Properties.** `audio_format` is the source format, and stays what an injected source must produce. `module_audio_format` is new. Both are valid right after construction.
- **Start.** One line is logged at INFO naming the format, or both when they differ. When they differ, the selected source (microphone, file or injected) is wrapped in a `ConvertedAudioSource`. The VAD gate, when configured, wraps the converter.
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
- **What does change for an existing config:**
  - The module is fed its own default format, not `engine.audio`'s when it happened to support it. A config that set 44.1 kHz in `engine.audio` with Deepgram now sends 16 kHz, unless `engine.module.audio_format` says `{"sample_rate": 44100}`.
  - With nothing set, the source's rate is the module's default, not a fixed 16 kHz. It only differs for the 24 kHz modules, which a 16 kHz default made fail before.

## Dependency

`soxr` (0.2 MB, numpy only) moves from the `silero` extra to the core dependencies, since the default behaviour depends on it. It publishes wheels for every supported platform. The VAD gate uses the same package for its analysis copy, so the engine no longer checks for it at construction.

## Testing

Fast tier:

- **The converter** (`tests/test_format_converter.py`): a tone resampled between several rate pairs keeps its duration, pitch and level; the output does not depend on the chunking; a frame split across chunks is reassembled; exact downmix and duplication; `mulaw` both ways; all three dimensions at once; the frame counters; a refused channel layout; the wrapper's order, restart and failure behaviour.
- **Resolution** (`tests/modules/test_base.py`): the module's defaults, a requested rate and encoding, the refusal of an unsupported one, and each row of the per-instance rates table.
- **The engine** (`tests/test_engine.py`): the module format independent of the source; the source following the module, field by field; a file's header read and checked; errors at construction; the formats logged at each start; a hand-fed 44.1 kHz source reaching a recording module as 16 kHz audio of the same duration and pitch; equal formats reaching the module untouched; the gate analysing the converted audio.
- **Config** (`tests/test_config.py`): the `audio_format` object in both blocks and its validation, the older flat fields, and the warning on the removed one.

Live tier ([e2e-testing.md](e2e-testing.md)):

- `test_engine_modules_converted`: modules fed 16 or 24 kHz recognize the 44.1 kHz fixture through the converter. Only a live backend shows that converted speech is still intelligible.
- The keyless server scenarios all play a 44.1 kHz file into the `fake` module, which is fed its 16 kHz default, so they run through the converter too.

## Open questions

All deferrals.

1. **A timeline through the converter.** The staged-pipeline work (spans on utterances) needs positions on one audio timeline. The frame counters are the hook; nothing reads them yet.
2. **Per-instance rates on other modules.** `openai-transcriptions-compatible` keeps the batch base's 16 kHz: its servers resample whatever they receive, so another rate buys nothing.
3. **`ScriptableAudioSource` and mixed files.** It still plays only files recorded in its own format. It could convert each file on `play()`.
4. **Resampler quality as a setting.** `soxr`'s default is used. A lower-latency setting could matter for 48 kHz to 8 kHz.
