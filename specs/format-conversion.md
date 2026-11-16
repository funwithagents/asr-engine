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

This replaces the former `on_unsupported_format` setting: an unsupported format is no longer an error to handle, it is converted.

## Configuration

Formats are set with an **`audio_format` object**: three optional fields, `sample_rate`, `channels` and `encoding`. It appears in two places ([configuration.md](configuration.md) holds the field tables):

```json
{
  "audio_source": { "type": "input-device", "audio_format": { "sample_rate": 48000 } },
  "module":       { "type": "deepgram-v1", "audio_format": { "sample_rate": 16000 } }
}
```

In Python it is an `AudioFormatConfig(sample_rate=None, channels=None, encoding=None)`, on `ModuleConfig.audio_format` and `AudioSourceConfig.audio_format`. The resolved formats are `AudioFormat` values.

The dependency runs **one way**: the module format never looks at the source, and a source with nothing to say falls back on the module format.

### The module format

Resolved first, by `resolve_module_audio_format(module, sample_rate=, channels=, encoding=)` in `modules/base.py`:

- each field of `engine.module.audio_format` that is set replaces the module's default (`DEFAULT_SAMPLE_RATE`, `DEFAULT_CHANNELS`, `DEFAULT_ENCODING`);
- a set value must be one the module supports, otherwise `ValueError` at construction listing the supported values.

With no `engine.module.audio_format`, a module is fed its defaults whatever the source is.

### The source format

Whoever produces the audio says what its format is. That depends on `engine.audio_source.type`:

| `type` | The source format is | Fallback |
|---|---|---|
| `"input-device"` | `engine.audio_source.audio_format`: the rate and channel count the device is opened at, and the encoding the captured s16 is encoded to | A field left unset takes the module format's value |
| `"file"` | The file's own rate and channel count, read from its header at construction; the decoded s16 is encoded to the module format's encoding | — |
| `"custom"` | What the source passed to `ASREngine(config, audio_source=...)` declares, through its optional `audio_format` attribute ([audio-source.md](audio-source.md)) | A source that declares nothing must produce the module format |

- `engine.audio_source.audio_format` exists for an input device only, because a device can be opened at any format. Set with `"file"` or `"custom"` it is an error, so it is never written where it has no effect.
- A missing or unreadable `audio_file` fails at construction (`FileNotFoundError` / `ValueError`), not at the first `start()`.
- A custom source's `audio_format` must be an `AudioFormat`, else `TypeError` at construction.

Why an input device follows the module by default, instead of a fixed rate such as 16 kHz: opening the device at the module's format avoids a conversion and, for a 24 kHz module, avoids capturing less than the model can use.

### The source type is explicit

`engine.audio_source.type` defaults to `"input-device"`, and the engine checks it against how it is built:

- `"custom"` with no source passed is an error. The engine does not fall back on a microphone, which on a robot would be the wrong one. The MCP server cannot be given a source, so it refuses this type.
- `"input-device"` or `"file"` with a source passed is an error. The passed source is not silently preferred, and not silently ignored either: a config with no `audio_source` block has the default type, so passing it a source fails until the config says `"custom"`. That check is what makes a default safe.

### Examples

| `audio_source` | Module, `module.audio_format` | Source format | Module format | Converted? |
|---|---|---|---|---|
| input device | `deepgram-v1`, unset | 16 kHz | 16 kHz | No |
| input device | `kyutai` (24 kHz only), unset | 24 kHz | 24 kHz | No: the device is opened at 24 kHz |
| input device, `{"sample_rate": 48000}` | `deepgram-v1`, unset | 48 kHz | 16 kHz | Yes |
| input device | `deepgram-v1`, `{"sample_rate": 8000, "encoding": "mulaw"}` | 8 kHz `mulaw` | 8 kHz `mulaw` | No: the device follows |
| a 44.1 kHz file | `deepgram-v1`, unset | 44.1 kHz | 16 kHz | Yes |
| a 44.1 kHz file | `deepgram-v1`, `{"sample_rate": 44100}` | 44.1 kHz | 44.1 kHz | No |
| a 44.1 kHz stereo file | `mlx-audio`, unset | 44.1 kHz stereo | 16 kHz mono | Yes: downmix and resample |
| custom, declaring 8 kHz `mulaw` (a telephony stream) | `mistral-realtime`, unset | 8 kHz `mulaw` | 16 kHz `linear16` | Yes: decode and resample |
| custom, declaring nothing | `kyutai`, unset | 24 kHz: the source must produce it | 24 kHz | No |

### A custom source that declares nothing

Such a source must produce the module's format, which differs from one module to the next (16 kHz for most, 24 kHz for `kyutai` and `openai-realtime-compatible`). Two things keep that from going wrong silently:

- `engine.audio_format` is valid right after construction, so the source can read what it must produce instead of assuming it;
- every `start()` logs the formats in use at INFO, converted or not.

A source with a fixed format should declare it. The engine then converts for whatever module is selected. The bundled `FileAudioSource`, `ScriptableAudioSource` and `AudioCapture` all declare theirs.

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

- **Construction.** After building the module, the engine checks the source type and resolves both formats (`ASREngine._resolve_formats`): the module's first, then the source's, by the rules above. It then checks the pair is convertible. Every error surfaces here.
- **Properties.** `audio_format` is the source format: what a custom source declared, or must produce when it declared nothing. `module_audio_format` is new. Both are valid right after construction.
- **Start.** One line is logged at INFO naming the format, or both when they differ. When they differ, the selected source (input device, file or custom) is wrapped in a `ConvertedAudioSource`. The VAD gate, when configured, wraps the converter.
- **The module** receives `audio_format=module_audio_format` in `start()`, as before.
- **Stop** stops the outermost wrapper, which stops what it wraps, down to the source.

### Why before the VAD gate

- The converter sees a continuous stream of `bytes` and never handles the end-of-speech marker.
- The gate is unchanged. It is built with the module format, and "pipeline format" in [vad.md](vad.md) now means the module format.
- After the gate, the converter would have to flush the resampler's tail at every marker, or the last milliseconds of an utterance would stay held until the next one. Batch modules decode on the marker, so they would lose them.

The cost: silence is converted too (negligible), and when the gate must itself resample for its VAD, its analysis copy is resampled twice.

### What does not change

- **The `AudioSource` protocol's methods.** A source still puts `bytes` in `engine.audio_format` on a queue it owns. The `audio_format` attribute is an optional addition: a source without it works as before.
- **The `ASRModule` interface.** Modules still read `bytes` (and the marker) in the format `start()` names.
- **What does change for an existing config:**
  - The `engine.audio` block is now `engine.audio_source`, with a `type`. The old block is refused with a message naming the new one. `sample_rate`, `channels` and `encoding` move into `audio_format` (input device only), and `on_unsupported_format` is gone.
  - The module is fed its own default format, not the source's when it happened to support it. A 44.1 kHz file with Deepgram now sends 16 kHz, unless `engine.module.audio_format` says `{"sample_rate": 44100}`.
  - With nothing set, an input device is opened at the module's default rate, not a fixed 16 kHz. It only differs for the 24 kHz modules, which a 16 kHz default made fail before.
  - A source passed to the engine needs `"type": "custom"`. It no longer overrides a device or a file silently.

## Dependency

`soxr` (0.2 MB, numpy only) moves from the `silero` extra to the core dependencies, since the default behaviour depends on it. It publishes wheels for every supported platform. The VAD gate uses the same package for its analysis copy, so the engine no longer checks for it at construction.

## Testing

Fast tier:

- **The converter** (`tests/test_format_converter.py`): a tone resampled between several rate pairs keeps its duration, pitch and level; the output does not depend on the chunking; a frame split across chunks is reassembled; exact downmix and duplication; `mulaw` both ways; all three dimensions at once; the frame counters; a refused channel layout; the wrapper's order, restart and failure behaviour.
- **Resolution** (`tests/modules/test_base.py`): the module's defaults, a requested rate and encoding, the refusal of an unsupported one, and each row of the per-instance rates table.
- **The engine** (`tests/test_engine.py`): the module format independent of the source; the input device following the module, field by field; a file's header read; a custom source that declares its format, and one that does not; the source type checked against how the engine is built; errors at construction; the formats logged at each start; a hand-fed 44.1 kHz source reaching a recording module as 16 kHz audio of the same duration and pitch; equal formats reaching the module untouched; the gate analysing the converted audio.
- **Config** (`tests/test_config.py`): the `audio_source` block (the default type, the fields each type takes, the old `audio` block refused) and the `audio_format` object and its validation.

Live tier ([e2e-testing.md](e2e-testing.md)):

- `test_engine_modules_converted`: modules fed 16 or 24 kHz recognize the 44.1 kHz fixture through the converter. Only a live backend shows that converted speech is still intelligible.
- The keyless server scenarios all play a 44.1 kHz file into the `fake` module, which is fed its 16 kHz default, so they run through the converter too.

## Open questions

All deferrals.

1. **A timeline through the converter.** The staged-pipeline work (spans on utterances) needs positions on one audio timeline. The frame counters are the hook; nothing reads them yet.
2. **Per-instance rates on other modules.** `openai-transcriptions-compatible` keeps the batch base's 16 kHz: its servers resample whatever they receive, so another rate buys nothing.
3. **`ScriptableAudioSource` and mixed files.** It still plays only files recorded in its own format. It could convert each file on `play()`.
4. **Resampler quality as a setting.** `soxr`'s default is used. A lower-latency setting could matter for 48 kHz to 8 kHz.
