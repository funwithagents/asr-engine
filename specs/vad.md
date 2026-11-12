---
code:
  - src/asr_engine/vad/__init__.py
  - src/asr_engine/vad/base.py
  - src/asr_engine/vad/gate.py
  - src/asr_engine/vad/silero/__init__.py
  - src/asr_engine/vad/silero/module.py
  - src/asr_engine/audio.py
  - src/asr_engine/engine.py
  - src/asr_engine/config.py
  - src/asr_engine/modules/base.py
  - pyproject.toml
tests:
  - tests/vad/scripted.py
  - tests/vad/test_base.py
  - tests/vad/test_gate.py
  - tests/vad/test_silero.py
  - tests/test_engine.py
  - tests/test_config.py
  - tests/test_audio.py
  - tests-e2e/test_engine_modules.py
---

# Voice Activity Detection (VAD)

**Status:** Implemented

## Purpose

VAD is an **optional, pluggable speech gate** between the audio source and the ASR module: it forwards speech and drops silence and noise. With no VAD configured the pipeline is exactly what it is today.

It exists for three reasons:

1. **Cost and privacy.** Every bundled cloud module bills the audio it is sent, silence included ([modules/mistral.md](modules/mistral.md) "Cost consequence": about $0.48 per hour of engine running time). A gate cuts the bill to speech time, and room noise never leaves the machine.
2. **Compute.** A local streaming model ([modules/kyutai.md](modules/kyutai.md)) steps continuously, at a real-time factor of about 0.45 on the reference machine. A gate lets it idle between utterances.
3. **A prerequisite for batch modules.** Non-streaming backends (Parakeet, Qwen3-ASR, Whisper, any `POST /v1/audio/transcriptions` server) transcribe a completed stretch of speech and have no endpointing of their own. The gate's end-of-speech marker is what tells them an utterance ended. See [Fit with planned modules](#fit-with-planned-modules).

This spec covers the gate and the end-of-speech signal it sends to the ASR module. Feeding VAD decisions to the `Segmenter` is deferred (see [Open questions](#open-questions)).

## Design principles

- **Optional, and absent when off.** `engine.vad` omitted means no VAD object is built and nothing VAD-related is imported.
- **Pluggable like ASR modules.** A `VADModule` ABC and a lazy registry keyed by `engine.vad.type`, one module active at a time, dependencies in an optional extra.
- **Thin modules, shared gate.** A `VADModule` only scores one frame ("how likely is this speech?"). Reframing, format conversion, hysteresis, pre-roll and marker emission live once in the shared `VadGate`, the way the `Segmenter` sits over every `ASRModule`.
- **The public `AudioSource` seam does not change.** [audio-source.md](audio-source.md) calls it stable: its queue stays `asyncio.Queue[bytes]`. Only the internal gate emits the end-of-speech marker.
- **ASR modules opt in.** A module declares whether it handles the marker. A module that does not is never sent one, and combining it with VAD fails at engine construction rather than silently producing no finals.

## Architecture placement

```
AudioSource (mic / file / injected)
        │  PCM chunks in engine.audio_format (~100 ms)
        ▼
   VadGate ── VADModule.process_frame() on an analysis copy
        │  speech-only PCM in engine.audio_format, then END_OF_SPEECH
        ▼
   ASRModule ─▶ SpeechUtterance ─▶ Segmenter ─▶ SpeechSegment
```

The gate wraps whichever source the engine selected, including an injected `audio_source`: VAD is a processing stage, independent of where audio comes from. The `Segmenter` is untouched and still derives segments from ASR utterances only.

## Which modules to use with VAD

Every bundled module supports the gate. Whether to turn it on depends on what the gate replaces:

| Module | Gate | What gating changes |
|---|---|---|
| `deepgram_v1` | **Worth it when cost or privacy matters** | Deepgram has an explicit flush message, so a final arrives right after the marker with the connection kept open. Nothing of value is lost. |
| `mistral_realtime` | **Worth it when cost or privacy matters** | Bills per audio minute. The server's `turn.end` no longer fires (it needs to hear the silence), so finals come from the marker instead. Gaps over `idle_close_s` (20 s) park the session, and the next utterance pays a handshake before its audio is sent (the audio waits in the queue, none is lost). |
| `deepgram_v2` (Flux) | **Worth it when cost or privacy matters, with a longer hangover** | Flux's value is its own contextual turn detection, which works on the silence after speech. With the default 500 ms hangover the gate would usually cut that silence before Flux decides. With `min_silence_duration_ms` raised to about 1500, Flux hears enough real silence to end the turn itself, and the gate only removes the long idle stretches. See [Letting the backend end the turn first](#letting-the-backend-end-the-turn-first). |
| `kyutai` | **Supported, rarely needed** | Free and local, with a semantic end-of-turn head. Gating saves compute and stretches the step budget. The same longer hangover keeps its semantic end-of-turn in charge. Worth it on battery or thermally limited hardware. |
| `fake` | Supported, for tests | Its audio-time clock counts gated audio only. |
| Batch modules (planned) | **Required** | They have no other end-of-utterance signal. |

Rule of thumb: turn the gate on for any billed backend; give a backend that has its own turn detection a longer hangover so that it still decides; and the gate is mandatory for a batch backend.

### Letting the backend end the turn first

The gate keeps forwarding real audio for `min_silence_duration_ms` after the speaker stops. That window decides who ends the utterance:

- **Shorter than the backend's own end-of-turn latency** (the 500 ms default against Flux or Kyutai): the gate closes first, and the marker forces the final. Turn boundaries are the gate's acoustic ones, so a thinking pause of half a second splits the utterance.
- **Longer than it** (about 1500 ms): the backend hears the silence it needs and ends the turn by its own, contextual judgement, typically well inside the window. The marker then arrives with no turn open and costs nothing. Only when the backend is still undecided at the end of the window does the marker force the final.

The 1500 ms figure is measured: on real silence Flux sent `EndOfTurn` 0.17 s and 1.16 s after the speech ended on two fixtures ([modules/deepgram.md](modules/deepgram.md) "VAD gating"), and Kyutai's semantic end-of-turn lands about 1 s after it ([modules/kyutai.md](modules/kyutai.md) "Latency").

The price of the longer window is that much silence billed per utterance, against all the idle time between utterances saved. For an engine left running, that is nearly the whole bill either way.

So the recommended configuration for `deepgram_v2` is:

```json
"vad": { "type": "silero", "min_silence_duration_ms": 1500 }
```

## `VADModule` interface

New file `src/asr_engine/vad/base.py`.

```python
class VADModule(ABC):
    # Declared at import time; __init_subclass__ raises TypeError when a concrete
    # subclass omits one, or when DEFAULT_SAMPLE_RATE is not in SUPPORTED_SAMPLE_RATES.
    SUPPORTED_SAMPLE_RATES: ClassVar[frozenset[int]]  # e.g. {8000, 16000}
    DEFAULT_SAMPLE_RATE: ClassVar[int]  # used when the pipeline rate is unsupported

    def __init__(self, config: dict) -> None:
        self.config = config  # the engine.vad block minus the gate's own fields

    @abstractmethod
    def frame_samples(self, sample_rate: int) -> int:
        """Samples per process_frame call at this analysis rate."""

    @abstractmethod
    def load(self) -> None:
        """Load the model. Blocking and idempotent; the model stays resident."""

    @abstractmethod
    def process_frame(self, frame: np.ndarray, sample_rate: int) -> float:
        """Speech probability in [0, 1] for one mono float32 frame of exactly
        frame_samples(sample_rate) samples in [-1, 1]. A VAD with a yes/no
        answer returns 0.0 or 1.0."""

    @abstractmethod
    def reset(self) -> None:
        """Clear streaming state (e.g. a recurrent state)."""
```

- **The module only ever sees mono float32 at one of its own rates.** The gate does every conversion, so a module has no encoding or channel capability to declare.
- **Frame size depends on the rate** (Silero: 512 samples at 16 kHz, 256 at 8 kHz), hence a method rather than a constant.
- **`process_frame` is synchronous and runs on the event loop.** That is a contract: it must cost well under its frame's duration (Silero: under 1 ms per 32 ms frame). A VAD that cannot meet it does not fit this interface.
- **`load()` is blocking and idempotent**; the engine runs it in a thread on first start and keeps the module for its own lifetime, so `listen()`'s start/stop cycles do not reload it (the same reasoning as Kyutai's resident weights).
- **Config contract** as for ASR modules: validate in `__init__`, raise `ValueError` naming the field.

### Registry

`src/asr_engine/vad/__init__.py` mirrors [the ASR registry](asr-module-interface.md#module-registration): lazy entries, nothing imported until a type is selected.

```python
REGISTRY: dict[str, LazyVAD | type[VADModule]] = {
    "silero": LazyVAD("asr_engine.vad.silero.module:SileroVAD", extra="silero"),
}


def resolve_vad_class(vad_type: str) -> type[VADModule]: ...
```

`resolve_vad_class` behaves like `resolve_module_class`: an unknown type raises `ValueError("Unknown VAD type '<t>'. Available: …")`; a missing third-party package raises `ImportError` naming the extra and the install command. A test or a caller can register a class eagerly (`patch.dict(REGISTRY, {"scripted": ScriptedVAD})`).

One package per backend, as for ASR modules: `vad/silero/`.

## `VadGate`

New file `src/asr_engine/vad/gate.py`. Internal: the engine builds it, callers never do.

```python
class VadGate:
    def __init__(
        self,
        inner: AudioSource,
        module: VADModule,  # already loaded
        audio_format: AudioFormat,  # the pipeline format
        *,
        threshold: float,
        min_speech_duration_ms: int,
        min_silence_duration_ms: int,
        speech_pad_ms: int,
        emit_end_of_speech: bool,
    ) -> None: ...

    def start(self) -> asyncio.Queue[bytes | EndOfSpeech]: ...
    def stop(self) -> None: ...
```

- `start()` resets the module, starts the inner source, and spawns one task that drains the inner queue and feeds a **new, unbounded** output queue, which it returns.
- `stop()` cancels the task and stops the inner source. Both are synchronous, and the gate is restartable: each `start()` returns a fresh queue and starts from the silence state.
- The gate drains the inner queue continuously, so the inner source's backpressure behaviour is unchanged. Its own output queue is unbounded for the same reason [audio-source.md](audio-source.md) gives: modules drain continuously.
- A failure inside the gate task (a `process_frame` exception) is logged at error level and the gate **fails open**: it forwards everything from then on, starting with the frame that failed, and emits no more markers, so a broken VAD degrades to "no VAD" instead of to silence. Nothing already forwarded is repeated.

### Gating state machine

Two states with hysteresis, so that a click does not open the gate and a short pause does not close it.

| Parameter | Meaning |
|---|---|
| `threshold` | A frame whose probability is at or above it counts as speech. |
| `min_speech_duration_ms` | Consecutive speech needed to open the gate. |
| `min_silence_duration_ms` | Consecutive non-speech needed to close it (the hangover). |
| `speech_pad_ms` | Audio kept from **before** the detected onset (pre-roll). |

Durations are counted in whole analysis frames, rounded up (Silero's frames are 32 ms, so the 250 ms default is 8 frames, 256 ms). At least one speech frame is needed to open, so `min_speech_duration_ms: 0` opens on the first.

- **Silence:** nothing is forwarded. The gate keeps the last `speech_pad_ms + min_speech_duration_ms` of audio in a ring buffer. After `min_speech_duration_ms` of consecutive speech it opens: it forwards the whole ring buffer, then keeps forwarding.

  The ring holds both durations because the onset is only confirmed `min_speech_duration_ms` after it happened. A ring of `speech_pad_ms` alone would leave almost no audio from before the first word.

- **Speech:** every frame is forwarded, including the pauses. After `min_silence_duration_ms` of consecutive non-speech the gate closes: it flushes what it has, emits one `END_OF_SPEECH` marker (when `emit_end_of_speech` is true), calls `module.reset()`, and returns to silence.

  The audio forwarded during the hangover is the utterance's trailing silence, so there is no separate post-roll. A final is requested `min_silence_duration_ms` after the speaker stops.

- **On `stop()`** an open utterance gets no marker. The module's own `stop()` flushes what it has, as it does today.

### Audio formats: pipeline format and analysis format

Two formats are in play, and the gate keeps them apart:

- **Pipeline format** (`engine.audio_format`): what the source produces and the ASR module consumes. It is reconciled against the ASR module exactly as today. VAD never changes it, and the bytes the gate forwards are the source's bytes, untouched.
- **Analysis format**: mono float32 at a rate the VAD module supports. The gate builds an analysis **copy** of the incoming audio and uses it only to decide.

The analysis rate is chosen automatically: the pipeline rate when the VAD module supports it (no resampling), otherwise the module's `DEFAULT_SAMPLE_RATE`. There is no configuration for it.

For each incoming chunk the gate decodes it to s16 (`mulaw` through a new `mulaw_to_linear16` helper in `audio.py`), averages the channels to mono, resamples when the rates differ, and cuts the result into `frame_samples` frames, carrying the remainder to the next chunk. Resampling uses `soxr`'s streaming resampler, imported only when resampling is actually needed; when it is missing the engine fails at construction with an install hint.

So any pipeline format an ASR module accepts works with any VAD: a 44.1 kHz `linear16` pipeline for Deepgram and a 24 kHz one for Kyutai are both analysed at 16 kHz by Silero, and a 16 kHz pipeline is analysed as it is.

**Mapping decisions back to pipeline audio.** Analysis frame *k* covers a fixed span of analysis samples. The gate cuts the pipeline byte stream at the pipeline sample nearest each frame boundary (always on a whole sample across all channels), so each frame owns one contiguous slice of the original bytes. Inside a speech region no sample is dropped or duplicated. The resampler's internal delay (a few milliseconds) is ignored: it is far below `speech_pad_ms`.

**Output chunking.** Forwarded audio is coalesced: once per incoming chunk, and before a marker, the gate puts what it has decided to forward on its queue, so nothing is held back and a module sending one message per chunk keeps about its usual message rate. A long run (the pre-roll at an onset) is split into `CHUNK_MS` chunks, the remainder joining the last one, so every chunk is shorter than two `CHUNK_MS`. ASR modules accept any chunk size ([audio-source.md](audio-source.md) "Chunk contract").

## End of speech

When the gate closes, the ASR module must produce its final **now**. Waiting for the backend to notice would not work: the gate has just stopped sending the audio that every backend's own endpointing listens to.

### The marker

```python
# src/asr_engine/audio.py
class EndOfSpeech:
    """In-band marker: the utterance whose audio precedes it is complete."""


END_OF_SPEECH = EndOfSpeech()  # the only instance; compare with `is`
```

The marker travels **on the audio queue, right after the utterance's last chunk**. An out-of-band call would race the audio still waiting in the queue and could finalize before the module had sent the last words. On the queue, ordering is free.

- Only `VadGate` emits it. `AudioSource` implementations, bundled or custom, still put only `bytes`.
- The `ASRModule.start` parameter widens to `audio_queue: asyncio.Queue[bytes | EndOfSpeech]` (the `AudioQueue` alias in `modules/base.py`). The engine passes a plain source's `Queue[bytes]` through a `cast` (a queue the module only reads from is safe to widen).

### The module contract

Two additions to `ASRModule` (also in [asr-module-interface.md](asr-module-interface.md) "VAD gating"):

```python
VadGating = Literal["unsupported", "supported", "required"]


class ASRModule(ABC):
    # "unsupported": never sent a marker; VAD + this module is a config error.
    # "supported":   handles the marker; works with or without VAD.
    # "required":    produces no final without it; no VAD + this module is a config error.
    VAD_GATING: ClassVar[VadGating] = "unsupported"

    async def finalize(self) -> None:
        """The audio received so far ends an utterance: emit its final and be
        ready for the next one. Default: no-op."""
```

- `VAD_GATING` has a default, unlike the audio-format attributes, so an existing third-party module keeps working unchanged: it is `"unsupported"`, it never receives a marker, and its queue holds only `bytes` as before. (Its `start` override annotated `Queue[bytes]` now draws a type-checker complaint about the narrower parameter; runtime behaviour is unaffected.)
- A module that declares `"supported"` or `"required"` handles the marker **in its own drain loop**, where its connection or model is in scope: on `END_OF_SPEECH` it calls `self.finalize()` instead of sending audio. The engine never calls `finalize()` itself.
- A marker that arrives while the module is discarding audio (reconnect backoff, model loading) is discarded with it.
- `finalize()` may return before the final has been emitted (it can arrive through the module's normal receive path). It must not block the drain loop for long.

**The lifecycle is asymmetric on purpose.** `start()` and `stop()` are still called once per engine run. Per utterance the module gets one marker at the end and nothing at the start: an utterance begins with the first chunk after the previous marker. During a gap no chunk arrives and the module waits on the queue as it would on an idle source.

### Engine checks at construction

| Configuration | Result |
|---|---|
| VAD configured, module `"unsupported"` | `ValueError`: the module does not support VAD gating |
| No VAD, module `"required"` | `ValueError`: the module needs `engine.vad` |
| VAD configured, module `"supported"` or `"required"` | gate built with `emit_end_of_speech=True` |
| No VAD, module `"unsupported"` or `"supported"` | today's pipeline |

### Per-module `finalize()`

Each bundled module is `"supported"`. The detail is in each module's spec; in short:

| Module | `finalize()` | Final arrives |
|---|---|---|
| `deepgram_v1` | `send_finalize()` on the open connection (the SDK exposes it separately from `send_close_stream()`). Deepgram answers with a normal `is_final` result on the existing receive path. Connection and context are kept. | Right after the marker |
| `deepgram_v2` | Flux has no flush message; its turn ends on silence it hears. The module tracks whether a turn is open (a transcript received since the last `EndOfTurn`). **No turn open:** Flux already ended it during the hangover, so `finalize()` does nothing. **Turn open:** send one burst of encoded silence covering `eot_timeout_ms` plus a margin through `send_media`, so Flux ends the turn on its timeout path at the latest. Connection kept either way. | Usually before the marker (Flux's own `EndOfTurn`); otherwise up to `eot_timeout_ms` after it |
| `mistral_realtime` | `input_audio.end` would end the session, so the flush is local. The text of audio already sent only arrives once the server receives about one streaming delay of further audio (measured), so: send one burst of silence of `streaming_delay_ms` + 200 ms, then emit the accumulated text as a final once no delta has arrived for `streaming_delay_ms + final_margin_ms` (each delta restarts the wait). | About 0.5 s + `streaming_delay_ms + final_margin_ms` after the marker |
| `kyutai` | The timers count model steps, and no audio means no steps. Pad the partial frame with zeros, feed `ceil(audio_delay_s / 0.08)` + 2 silent frames to the worker, then force a final of what has accumulated (`confidence=None`). Silence is free here. A semantic-VAD final armed in the meantime is absorbed: an empty accumulator emits nothing. | About the model's text delay after the marker |
| `fake` | Ignores the marker. Its script, not the audio, decides its finals. | Per script |

Two of these rested on backend behaviour that only a live run could show. Both were measured before the code was written; the tables are in the module specs:

- **Flux** ([modules/deepgram.md](modules/deepgram.md) "VAD gating"): on real silence its own `EndOfTurn` arrives 0.2–1.2 s after the speech; with audio simply stopped the turn never ends; a burst of silence sent in one unpaced message ends it about half a second later. So the burst works, and the `send_close_stream()` fallback was not needed. If a transcript for the utterance only arrives after the marker, the burst is sent then.
- **Voxtral** ([modules/mistral.md](modules/mistral.md) "Measured"): the tail text of audio already sent does **not** arrive without further audio, so the fallback is what was built — the silence burst before the wait. It is billed: about 0.7 s per utterance at the default delay.

**During the gaps** nothing new is needed. Deepgram's keep-alive already fires after 5 s without a chunk. Mistral's idle parking already ends the session cleanly after 20 s and reopens it on the next chunk. Kyutai and `fake` simply wait.

**Injected silence and μ-law.** `audio._silence(fmt, duration_s)` builds correctly encoded silence of any length for any pipeline format (μ-law silence is `0xFF`, not zero bytes).

### Gating and the segmentation modes

Unchanged. The `timeout` mode closes a segment after a time with no ASR utterance; it never looked at the audio. With the gate there is no audio in a pause, hence no utterances, hence the same timers. `utterance` and `trigger_word` close on the module's finals, which the marker now triggers.

One visible difference: with VAD on, the number and boundaries of finals follow the gate's `min_silence_duration_ms`, not the backend's own endpointing. A larger value gives fewer, longer finals.

## Engine integration

- **Construction.** When `config.vad` is set, the engine resolves the VAD class (unknown type → `ValueError`, missing extra → `ImportError` with the install hint), instantiates it (config validation), applies the [checks above](#engine-checks-at-construction), and verifies `soxr` is importable when the analysis rate differs from the pipeline rate. It does not load the model.
- **Start.** In `_start_with_segmentation_mode`, after choosing the base source: load the VAD model in a thread on first use (`asyncio.to_thread(vad.load)`), wrap the base source in a `VadGate`, and start the gate in place of the source. The queue it returns goes to the module as today.
- **Stop** and `listen()` teardown stop the gate, which stops the inner source. The VAD module stays loaded.
- `engine.audio_format` is unchanged by VAD.
- `status()` gains `"vad": <type> | None`.

## Configuration

A new optional `engine.vad` block (also in [configuration.md](configuration.md)). Omitted or `null` means no VAD.

```json
"vad": {
  "type": "silero",
  "threshold": 0.5,
  "min_speech_duration_ms": 250,
  "min_silence_duration_ms": 500,
  "speech_pad_ms": 300
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | — (required when the block is present) | VAD module to load, a key of the VAD registry |
| `threshold` | float | `0.5` | Probability at or above which a frame is speech. In (0, 1). |
| `min_speech_duration_ms` | int | `250` | Consecutive speech before the gate opens. ≥ 0. |
| `min_silence_duration_ms` | int | `500` | Consecutive non-speech before it closes and the final is requested. > 0. |
| `speech_pad_ms` | int | `300` | Audio kept from before the onset. ≥ 0. |
| *(other fields)* | any | — | Passed to the VAD module, like `engine.module`'s extra fields |

`ASREngineConfig` gains `vad: VadConfig | None = None`, where `VadConfig` carries `type`, the four gate parameters, and `extra`.

**Validation** in `ASREngineConfig.from_dict`, so every constructor shares it: `type` is a non-empty string; `threshold` is a number (not a bool) strictly between 0 and 1; the three durations are ints (not bools) within the bounds above. Each failure raises `ValueError` naming `engine.vad.<field>`. As for `engine.module.type`, an unknown `type` or a missing extra surfaces at engine construction, not at parse time.

Any pipeline `encoding` and `sample_rate` the ASR module supports is valid with VAD on.

## Built-in module: `silero`

`src/asr_engine/vad/silero/`. [Silero VAD](https://github.com/snakers4/silero-vad) is the de facto open-source VAD (MIT).

- **Runtime: `onnxruntime`, not the `silero-vad` package.** `module.py` imports `onnxruntime` at its top, so a missing extra surfaces from the registry as the install hint; the package's `__init__.py` imports nothing. The PyPI package depends on `torch`, which this project keeps out of the default environment ([project.md](project.md)). The module runs the ONNX model directly with `onnxruntime` and numpy, porting the small amount of state handling from upstream's ONNX wrapper: a recurrent state tensor carried between calls, and a short context of the previous frame's last samples prepended to each input. `reset()` zeroes both.
- **The model is downloaded on first use, not shipped.** Neither the repo nor the wheel contains it. `load()` fetches `silero_vad.onnx` (2.3 MB) from a **pinned upstream tag** (`v6.2.3`, a `raw.githubusercontent.com` URL) with the standard library, into the user cache directory, and checks it against a **SHA-256 pinned in the module**. The tag, URL and hash are constants in `module.py` and change together.
  - **Cache directory:** `$ASR_ENGINE_CACHE_DIR` when set; otherwise `asr-engine/` under `%LOCALAPPDATA%` on Windows, and under `$XDG_CACHE_HOME` or `~/.cache` elsewhere. The file name carries the tag, so a model update does not reuse a stale file.
  - **Integrity:** the download goes to a temporary file and is moved into place only once its hash matches, so an interrupted or tampered download never leaves a model behind. A cached file whose hash no longer matches is downloaded again.
  - **`model_path`** (`engine.vad.model_path`, optional): the path of a local copy of the model, used as is, with no download and no hash check. This is the way to run on a machine without network access, or to pin your own copy. A path that does not exist fails at `load()`.
  - **Failure:** when the model can be neither found nor downloaded, `load()` raises a `RuntimeError` naming the URL and the `model_path` way out. It surfaces from the engine's first `start()` / `listen()`, since that is where the model is loaded; construction stays offline.
  - The download happens in the thread that loads the model, once per machine. Later starts, and every later process, read the cached file.
- `SUPPORTED_SAMPLE_RATES = {8000, 16000}`, `DEFAULT_SAMPLE_RATE = 16000`, `frame_samples` 256 / 512.
- The inference session is created with one intra-op thread: the model is tiny and must not spin up a thread pool on the event loop's thread.
- **Config:** one field beyond the gate's, `model_path` (below); any other unknown field raises `ValueError`, so a misspelt gate parameter is not silently ignored.
- **Cost:** about 0.1 ms per 32 ms frame on the reference machine (Apple Silicon), far inside the `process_frame` contract.

### Installation

```toml
[project.optional-dependencies]
silero = [
    "onnxruntime; sys_platform != 'darwin' or platform_machine != 'x86_64'",
    "soxr",
]
all = ["asr-engine[deepgram,mcp,kyutai-mlx,kyutai-torch,mistral,silero]"]

[dependency-groups]
dev = ["asr-engine[deepgram,mcp,kyutai-mlx,mistral,silero]", ...]
```

- `soxr` (0.2 MB, numpy only) sits in the VAD backend's extra, not in core: only the gate uses it, and only when the pipeline rate is not one the VAD supports.
- Both packages publish wheels for Linux x86_64 and arm64, macOS arm64 and Windows x86_64. **`onnxruntime` publishes no Intel macOS wheel**, hence its marker: on that platform the extra installs without it, the environment still syncs, and selecting `silero` raises the registry's install hint ([project.md](project.md) notes the caveat; the platform is best effort). The Silero tests skip there.
- The extra is light, so it joins `dev`, and the fast tier exercises it on every CI platform.

## Fit with planned modules

Checked against the modules considered for later (batch and realtime-protocol ASR backends, other VADs):

- **Batch modules** (`parakeet-tdt`, `qwen3-asr`, the generic `openai` HTTP module, Whisper variants). They declare `VAD_GATING = "required"`. Their shared base class buffers the chunks it receives, decodes on `finalize()`, emits one final and clears the buffer. Progressive re-decoding for interims needs no onset signal: the buffer starts growing with the first chunk after a marker. These models take 16 kHz mono, which Silero analyses natively, so with them the gate needs no resampler.
- **A maximum utterance length.** Batch models have a bounded input window (30 s for Whisper). The gate can enforce a `max_speech_duration_s` by emitting a marker mid-speech and staying open. Not built in v1, since no bundled module needs it; it belongs to the batch base-class spec.
- **`openai-realtime` / `vllm-realtime`.** Streaming with server-side turn detection, so the gate is optional, as for Deepgram. The protocol has an explicit commit message for the input buffer, which is what `finalize()` would send (to be confirmed when that module is specced).
- **Smart Turn** (a semantic end-of-turn model run on top of Silero in speech-to-speech). It fits as an optional check inside the gate, consulted before a marker is emitted, so the modules' contract would not change. Deferred.
- **FireRed Stream-VAD**, WebRTC VAD, TEN VAD: further `VADModule` backends. The registry takes them without an interface change.
- **Pipeline resampling** (source rate ≠ ASR rate) stays a separate concern. The gate's analysis copy is never the ASR feed: reusing it would tie the VAD's rate to the ASR's and break "forward the original bytes".

## Testing

Per [testing.md](testing.md).

### Fast tier: `tests/vad/`

A **deterministic `VADModule`** defined in the tests (`tests/vad/scripted.py`: 10 ms frames, a frame is speech when it is loud, and it records the frames it was given) drives the gate with a hand-fed inner queue, so each test places speech by the audio it feeds and every boundary is a round number. `tests/test_engine.py` reuses it.

- **Gating:** silence is dropped; a speech burst is forwarded with exactly the expected pre-roll and hangover; the bytes forwarded are the source's bytes.
- **Hysteresis:** a burst shorter than `min_speech_duration_ms` does not open the gate; a pause shorter than `min_silence_duration_ms` does not close it.
- **Marker:** exactly one `END_OF_SPEECH` per utterance, after its last chunk; none when `emit_end_of_speech` is false; none on `stop()` mid-utterance.
- **Formats:** with a 44.1 kHz `mulaw` pipeline and a 16 kHz VAD, the VAD receives mono float32 frames of the right length while the forwarded bytes are the original μ-law; a stereo pipeline is downmixed for analysis only; a 16 kHz pipeline is not resampled.
- **Reframing:** chunk sizes that are not a multiple of the frame lose and duplicate nothing across boundaries.
- **Lifecycle:** `stop()` then `start()` yields a fresh queue in the silence state; a `process_frame` failure makes the gate fail open.
- **`VADModule` and registry:** a subclass missing a declared attribute raises at import; unknown type and missing extra raise the documented errors.
- **Silero's model download**, against a local `file://` stand-in for upstream (no network): downloaded once into the cache directory; a wrong hash is rejected and nothing is installed; a corrupted cached file is replaced; an unreachable source raises naming `model_path`; `model_path` loads a local copy without touching the cache.
- **Silero, on the real model** (from the user cache, downloaded on the first run; it loads in milliseconds, so it stays inside the speed rule): a 16 kHz speech fixture scores above the threshold on most frames and digital silence below it; `reset()` makes two identical runs score identically; the gate driven by it forwards the spoken stretch of a 44.1 kHz file and one marker. These read the fixtures in `tests-e2e/fixtures/`, and skip where `onnxruntime` is missing or when the model is neither cached nor downloadable — the one place the fast tier may touch the network ([testing.md](testing.md)).
- **`mulaw_to_linear16`** round-trips against `linear16_to_mulaw` within G.711's quantization.

Elsewhere in the fast tier:

- `tests/test_config.py`: the `vad` block parses with defaults; one case per validation rule.
- `tests/test_engine.py`: no `vad` → the module receives the source's queue; `vad` + a recording module registered as `"supported"` → it receives speech then one marker per utterance; the two construction errors; the VAD model is loaded once across two start/stop cycles; `fake` + VAD runs a script end to end.
- `tests/modules/test_*.py`, on each module's existing fake connection or backend: `deepgram_v1` sends one Finalize per marker and no media for it; `deepgram_v2` sends the expected amount of silence when a turn is open and nothing when the turn already ended; `mistral_realtime` emits the final after the deadline, including text that arrives inside it; `kyutai` steps the flush frames and emits one final; `fake` ignores the marker. Every module discards a marker received while reconnecting.

### Live tier: `tests-e2e/`

Kept small, since the gate's behaviour is pinned deterministically above. One new parametrized scenario, `test_engine_modules_vad`, runs each `MODULES` row with `vad: {"type": "silero"}` on the row's existing fixtures: play an utterance with trailing silence through `ScriptableAudioSource` and assert that exactly one final arrives, containing the whole phrase (nothing clipped at either end), then that a second utterance yields its own final (the module survived a gated gap). The existing skip axes apply, plus a skip when `onnxruntime` is missing. No new fixtures: the 44.1 kHz MP3s and 24 kHz WAVs exercise the resampling path. Detail in [e2e-testing.md](e2e-testing.md).

`deepgram_v2` runs the scenario twice, since it has two paths: with the recommended 1500 ms hangover (Flux ends the turn itself) and with a 300 ms one (the marker's injected silence ends it). Both must yield one complete final per utterance.

## Files

| Path | Role |
|---|---|
| `src/asr_engine/vad/__init__.py` | Lazy registry, `resolve_vad_class` |
| `src/asr_engine/vad/base.py` | `VADModule` ABC |
| `src/asr_engine/vad/gate.py` | `VadGate` |
| `src/asr_engine/vad/silero/__init__.py`, `module.py` | `SileroVAD`, and the pinned download of its model |
| `tests/vad/` | Fast tier for the above (`scripted.py` holds the shared test doubles) |
| `scripts/probe_deepgram_flux_silence.py`, `scripts/probe_mistral_realtime.py` | The live measurements behind the Flux and Voxtral `finalize()` designs |

Changed: `audio.py` (`EndOfSpeech`, `mulaw_to_linear16`, `_silence`), `modules/base.py` (`VAD_GATING`, `finalize()`, the `AudioQueue` type), every ASR module (marker handling), `engine.py`, `config.py`, `pyproject.toml`. `EndOfSpeech`, `END_OF_SPEECH` and `VadConfig` are exported from `asr_engine`.

The contract and each module's side of it are also recorded where a reader of those components looks first: [asr-module-interface.md](asr-module-interface.md) "VAD gating", [engine.md](engine.md), [configuration.md](configuration.md), [architecture.md](architecture.md), [project.md](project.md), [e2e-testing.md](e2e-testing.md), [ci.md](ci.md) and the four module specs. [audio-source.md](audio-source.md) only gains a sentence saying the gate may wrap a source; its contract does not change.

## Open questions

Deferrals only.

- **VAD as a segmentation signal.** Delivering the gate's boundaries to the `Segmenter` (a new `end_reason`, or "whichever of VAD and ASR ends first"). Revisit with the first batch module.
- **Voice-activity callback.** An `on_voice_activity(bool)` engine callback for a "speaking" indicator in a UI. The gate knows the state; nothing consumes it yet.
- **A custom source emitting `END_OF_SPEECH`.** A push-to-talk source could force a final by putting the marker on its queue. That widens the public `AudioSource` contract, so it waits for a caller who needs it.
- **An onset marker.** No backend needs to be told that speech started. The marker channel makes a `START_OF_SPEECH` a small addition if one does.
- **`max_speech_duration_s`** and **Smart Turn**: see [Fit with planned modules](#fit-with-planned-modules).
- **Constructor-injected `VADModule`**, mirroring `audio_source`. Deferred until a caller needs more than the registry.
- **A shared model cache.** The cache directory and the verified download live in the Silero module. A second downloaded model (another VAD, Smart Turn) would move them to a shared helper.
