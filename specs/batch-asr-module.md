---
code:
  - src/asr_engine/modules/batch.py
tests:
  - tests/modules/test_batch.py
---

# Batch ASR Modules

**Status:** Implemented

## Purpose

Most open speech models are **batch** models: they transcribe a complete stretch of audio and have no streaming state and no endpointing of their own. Examples are Parakeet, Whisper, Qwen3-ASR and SenseVoice, behind runners like mlx-audio, transformers or an HTTP `/v1/audio/transcriptions` server. `BatchASRModule` is the shared base class that turns such a model into an `ASRModule`. The VAD gate cuts the stream into utterances, the base class buffers each one and decodes it, and the subclass only says how to load the model and how to transcribe one buffer.

This is the design [vad.md](vad.md) "Fit with planned modules" sketched. The subclasses are [modules/mlx-audio.md](modules/mlx-audio.md) (local models on Apple Silicon) and [modules/openai-transcriptions-compatible.md](modules/openai-transcriptions-compatible.md) (any `/v1/audio/transcriptions` server over HTTP).

## Contract for subclasses

```python
class BatchASRModule(ASRModule, ABC):
    VAD_GATING = "required"
    SUPPORTED_SAMPLE_RATES = frozenset({16000})
    SUPPORTED_CHANNELS = frozenset({1})
    SUPPORTED_ENCODINGS = frozenset({"linear16"})
    DEFAULT_SAMPLE_RATE, DEFAULT_CHANNELS, DEFAULT_ENCODING = 16000, 1, "linear16"

    LABEL: ClassVar[str]  # registry key, used in log and error messages
    DEFAULT_INTERIM_INTERVAL_S: ClassVar[float] = 0.5  # the field's default

    @abstractmethod
    def load(self) -> None:
        """Load and warm the model. Blocking; runs on the worker thread."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe one utterance: float32 mono at 16 kHz in [-1, 1].
        Blocking; runs on the worker thread."""
```

- **16 kHz mono** is what these models take, and what Silero analyses natively, so the gate needs no resampler. A subclass may override the audio attributes if its model differs.
- **`"required"`**: without the gate's `END_OF_SPEECH` marker nothing ends an utterance, so a config without `engine.vad` is rejected at engine construction ([vad.md](vad.md)).
- `load()` and `transcribe()` are plain blocking calls. The base class owns every asyncio, threading and lifecycle concern.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Utterance boundary | **The VAD marker**, and nothing else (plus the length cap below) | A batch model has no endpointing. The gate already does it, with pre-roll and hangover. |
| Finals | **One decode of the whole utterance on the marker** | The model sees the full context, which is what batch models are good at. |
| Interims | **Progressive re-decode** of the growing buffer, every `interim_interval_s` of buffered audio, at most one in flight | The same approach speech-to-speech uses. Fast models (Parakeet decodes 2 s in ≈ 0.03 s once warm, measured) make it cheap. "At most one in flight" means a slow model shows fewer interims rather than falling behind. `0` disables interims. |
| Interim clock | **Audio buffered**, not wall time | Deterministic in tests, and it means "every half second of speech". The gate only forwards speech, so the two are close. |
| Threading | **One worker thread per module instance**, created once and kept | Model calls are blocking and compute-heavy. Many runners (MLX in particular) are not safe to call from several threads. A single FIFO worker also orders decodes for free. |
| Model lifetime | **Resident across `stop()`/`start()`**, as with `kyutai` | `listen` runs a full start/stop cycle per call. Reloading a model each time would make it unusable. |
| `connected` | **The model is loaded and warmed** | Same meaning as `kyutai`. |
| Long utterances | **`max_utterance_s` cuts the utterance** with a final, and a new one starts | Models have bounded windows (Whisper 30 s), and the gate has no maximum speech length. A cut mid-word is rare and better than a failed or truncated decode. |
| Decode failure | **Logged; the utterance yields no final; the module keeps running** | One bad buffer must not take the model down. Unlike a dropped socket, there is no connection to rebuild. |

## Configuration fields

Every subclass accepts these, beside its own:

| Field | Type | Default | Description |
|---|---|---|---|
| `interim_interval_s` | float | `0.5` (the class's `DEFAULT_INTERIM_INTERVAL_S`) | Re-decode the utterance for an interim after this much more buffered audio. `0` disables interims. A subclass whose decodes are billed per request sets its default to `0` ([openai-transcriptions-compatible](modules/openai-transcriptions-compatible.md)). |
| `max_utterance_s` | float | `30.0` | Cut an utterance that grows this long: decode it as a final and start a new one |
| `stop_timeout_s` | float | `10.0` | How long `stop()` waits for the last decodes |

Validation in `__init__`: `interim_interval_s` is a number ≥ 0; `max_utterance_s` and `stop_timeout_s` are numbers > 0 (never booleans). Each failure raises `ValueError` naming the field.

## Runtime

```
asyncio task (start)                         worker thread (1 per instance)
────────────────────                         ─────────────────────────────
load (first start only) ───────────────────▶ load()
on_connected(True)
drain audio_queue:
  bytes ──▶ buffer += chunk
           buffered ≥ max_utterance_s ─────▶ transcribe(buffer)  → final
           buffered − last_interim ≥ interval
             and no interim in flight ─────▶ transcribe(buffer)  → interim
  END_OF_SPEECH ──▶ finalize() ────────────▶ transcribe(buffer)  → final
stop ──▶ flush buffer as final, await decodes
on_connected(False)
```

- **`finalize()`** takes the buffer, clears it, bumps the **utterance generation**, and schedules the final decode. It returns at once, so the drain loop is never blocked by a decode (the [`finalize()` contract](asr-module-interface.md)). An empty buffer schedules nothing.
- **Finals are emitted in order.** Each final's emission waits for the previous final's. Decodes are already FIFO on the single worker.
- **An interim is emitted only if its utterance is still open** (same generation) and its text is non-empty. An interim whose utterance was finalized meanwhile is dropped, so no interim ever follows its own final.
- **An empty final emits nothing**, like every other module.
- **The audio conversion** is `int16 little-endian → float32 / 32768`, done on the event loop when a decode is scheduled. It is a copy of the buffer, so the buffer can keep growing meanwhile.

## Lifecycle

- **`start()`** creates the worker on first use and loads the model there, unless a previous `start()` already did. It drains the queue while loading, since audio captured during a first-run download would be transcribed minutes late. A load failure is logged and retried on the [standard backoff](asr-module-interface.md#reconnection-contract) (1, 2, 4, 8 s), still draining. Once loaded, it calls `on_connected(True)` and runs the drain loop above.
- **`stop()`** sets the stop event and returns once `start()` has finished: the buffer flushed as a final, every scheduled decode emitted, and `on_connected(False)` called. It is bounded by `stop_timeout_s`; a decode still running after that is abandoned, and its result discarded. A load interrupted by `stop()` carries on in the background, and the next `start()` awaits that same load.
- **Markers while loading** are dropped along with the audio they close.

## Testing

### Fast tier: `tests/modules/test_batch.py`

A test subclass with a scripted `transcribe()` returns a text derived from the audio it is given (its length, and a marker byte), and can block on a `threading.Event` to hold a decode in flight. No model and no sleeping beyond a few milliseconds.

- **Load:** `on_connected(True)` only after `load()` returns. A second `start()` after `stop()` doesn't reload. A failed load retries, and audio sent meanwhile is drained, not transcribed.
- **Finals:** a marker yields one final over exactly the audio since the previous marker. Finals of back-to-back utterances arrive in order, even when the first decode is slower. An empty final emits nothing. An empty buffer at a marker schedules no decode.
- **Interims:** one interim per `interim_interval_s` of buffered audio. None while one is in flight. None after its utterance's final. None with `interim_interval_s: 0`.
- **Length cap:** past `max_utterance_s` a final is emitted and the next audio starts a new utterance.
- **Decode failure:** logged, no final, and the next utterance still transcribes.
- **Stop:** flushes the buffer as a final before returning, and ends with `on_connected(False)`.
- **Engine:** an engine built with a batch module and no `engine.vad` raises (already enforced by `VAD_GATING`; one case pins it for this class).
- **Validation:** one case per rule.

### Live tier

Through each subclass's `MODULES` row ([e2e-testing.md](e2e-testing.md)). A `"required"` module has no ungated conformance case. It runs only in `test_engine_modules_vad`.

## Files

- `src/asr_engine/modules/batch.py`: `BatchASRModule`
- `tests/modules/test_batch.py`

## Open questions

None load-bearing. Deferrals:

- **Language detection carried across utterances**, as speech-to-speech does for short progressive windows. It depends on the model. A subclass can do it in `transcribe()` when one needs it.
- **A wall-clock interim timer** for slow models on long utterances. The audio clock is enough while the only subclass decodes in milliseconds.
- **Cancelling an in-flight interim** when the final is scheduled. Runners expose no cancellation, and the single worker would still finish it first. Its result is already dropped.
