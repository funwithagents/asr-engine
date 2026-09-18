---
code:
  - src/asr_engine/modules/kyutai.py
  - src/asr_engine/modules/_kyutai_mlx.py
  - src/asr_engine/modules/_kyutai_torch.py
  - src/asr_engine/modules/__init__.py
  - pyproject.toml
  - scripts/benchmark_kyutai.py
tests:
  - tests/modules/test_kyutai.py
  - tests-e2e/test_engine_modules.py
---

# Kyutai STT Module

**Status:** Stable

## Purpose

`kyutai` is the first **local, on-device** ASR module: a streaming speech-to-text model that runs in-process with no network, no API key, and no per-minute cost. Every other real backend in this repo is a cloud websocket ([deepgram-module.md](deepgram-module.md)); this one puts the model inside the process, which is why it stresses parts of the [module interface](asr-module-interface.md) that a cloud backend never touches — lifecycle cost, blocking compute, backpressure, and the absence of a server-side "final" signal.

It is the streaming-native member of the local-backend family sketched in `_todo.md` (whisper, faster-whisper, parakeet). Unlike those, it needs no VAD to chunk it: it consumes audio frame-by-frame and emits text frame-by-frame, so it plugs into the existing always-on pipeline directly. See [vad.md](vad.md) for the separate, orthogonal audio-gating work.

**Upstream:** [kyutai-labs/delayed-streams-modeling](https://github.com/kyutai-labs/delayed-streams-modeling). Model weights CC-BY 4.0, Python code MIT.

## The models

| HF repo | Params | Languages | Delay | Semantic VAD heads |
|---|---|---|---|---|
| `kyutai/stt-1b-en_fr-candle` | ~1B | en + fr | 0.5 s | **yes** (`extra_heads_num_heads: 4`) |
| `kyutai/stt-1b-en_fr` / `-mlx` | ~1B | en + fr | 0.5 s | no |
| `kyutai/stt-2.6b-en` / `-mlx` | ~2.6B | en | 2.5 s | no |

Verified from each repo's `config.json`. Two consequences drive the defaults below:

- **Only the `-candle` repo carries the VAD heads.** The `-mlx` and plain repos do not, on either backend. The MLX backend loads `-candle` weights via `model.load_pytorch_weights(...)` rather than `load_weights(...)` — this is exactly what upstream's `stt_from_mic_mlx.py` does when `--vad` is passed.
- **`stt_config` carries the timing contract:** `audio_delay_seconds` (0.5 / 2.5) is how far behind the audio the text runs; `audio_silence_prefix_seconds` (0.0 / 1.0) is the silence the model wants before real audio starts.

### Measured (the viability gate)

`scripts/benchmark_kyutai.py` on an Apple M5 Pro (24 GB), `moshi-mlx` 0.3.0 / `mlx` 0.26.5, `kyutai/stt-1b-en_fr-candle` in bfloat16:

| Measure | Result |
|---|---|
| Real-time factor | **≈ 0.45** (mean step 35 ms against the 80 ms frame budget; p95 ≈ 55 ms) |
| Load + warm-up (weights cached) | ≈ 2 s; the first-run download was ≈ 40 s |
| `-candle` weights in MLX | load via `load_pytorch_weights(...)`, confirmed |
| Extra heads | `step_with_extra_heads` returns **4**; `vad_heads[2][0, 0, 0]` is the end-of-turn probability |
| VAD head vs text | the head crosses 0.5 **at or before** the utterance's last text tokens arrive — see [Utterance semantics](#utterance-semantics) |

RTF < 1 with margin, so the design below holds as specced; the quantized variants stay optional. Re-run the script before trusting other hardware.

**Default: `kyutai/stt-1b-en_fr-candle`** — English + French (matching the project's default trigger words), the lowest delay, and the only variant with a usable end-of-turn signal.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Registry shape | **One key, `kyutai`**, with `backend: "auto" \| "mlx" \| "torch"` | One `config.json` works unchanged on an Apple Silicon Mac and on a collaborator's Linux box. The backend is a deployment detail, not a different ASR. |
| Finalization | **Semantic VAD head, silence timer as fallback** | The VAD head is the direct analogue of Flux's `EndOfTurn`. The timer covers the 2.6B model and `vad: false`. |
| Sample rate | **Strict 24 kHz** (`SUPPORTED_SAMPLE_RATES = {24000}`) | Honours the pipeline rule that the engine validates rather than resamples. |
| Extras | Both in `all`; only `kyutai-mlx` in the `dev` group | `all` is the public "everything" target and must stay complete. `moshi` drags in torch (~2.5 GB), which no contributor should pay for on a plain `uv sync` — so the split happens in `dev`, not in `all`. |

## Architecture: the backend seam

The MLX and PyTorch inference loops are the same loop. Per 80 ms frame:

```python
audio_tokens = <encode frame with Mimi>
text_token   = gen.step(audio_tokens)                    # no VAD
text_token, vad_heads = gen.step_with_extra_heads(...)   # with VAD
piece = tokenizer.id_to_piece(text_token).replace("▁", " ")  # skip tokens 0 and 3
```

Everything else — re-blocking, float conversion, text accumulation, finalization, threading, lifecycle — is backend-independent. So the module splits at exactly that line:

```
KyutaiModule  (modules/kyutai.py)          ← ASRModule; all shared behaviour
      │  selects one of
      ├── MlxKyutaiBackend   (_kyutai_mlx.py)    moshi_mlx
      └── TorchKyutaiBackend (_kyutai_torch.py)  moshi
```

```python
@dataclass(frozen=True)
class StepResult:
    text: str | None  # decoded piece; None for padding tokens (0, 3)
    end_of_turn_p: (
        float | None
    )  # VAD head probability; None when the model has no heads


class KyutaiBackend(Protocol):
    """One loaded model instance. Synchronous and blocking by design — the module
    owns the worker thread, the backend owns only the maths."""

    SAMPLE_RATE: ClassVar[int]  # 24000
    FRAME_SAMPLES: ClassVar[int]  # 1920 (80 ms)

    @property
    def has_vad(self) -> bool: ...
    @property
    def steps_remaining(self) -> int | None: ...  # None = unbounded
    @property
    def audio_delay_s(self) -> float: ...  # stt_config.audio_delay_seconds
    @property
    def silence_prefix_s(self) -> float: ...  # stt_config.audio_silence_prefix_seconds

    def load(self) -> None:
        """Download (if needed), load weights, warm up. Seconds to minutes."""

    def step(self, frame: np.ndarray) -> StepResult:
        """One FRAME_SAMPLES float32 frame in, one StepResult out."""

    def reset(self) -> None:
        """Start a fresh streaming session; keeps the weights resident."""

    def close(self) -> None: ...
```

`has_vad` and the two `stt_config` timings are valid once `load()` has returned. `has_vad` is false when the repo declares no heads **or** the backend was built with `vad: false` (it then skips computing them).

This seam does triple duty: it is the MLX/PyTorch split, it is where a scripted fake backend is injected for the fast test tier (see [Testing](#testing)), and it is the only place that imports `moshi*`.

### Backend selection

`backend: "auto"` resolves to `mlx` when `sys.platform == "darwin"` and `platform.machine() == "arm64"` **and** `moshi_mlx` imports; otherwise `torch`. `"mlx"` / `"torch"` force one and raise if unavailable.

Because the registry's `LazyModule` carries a single `extra`, `kyutai` is registered with `extra=None` and `modules/kyutai.py` imports **neither** backend at top level. The module resolves the backend in `__init__` and raises its own `ImportError` naming the right extra:

```
ASR module 'kyutai' with backend 'mlx' requires the optional dependency
'kyutai-mlx' which is not installed. Install it with:
pip install 'asr-engine[kyutai-mlx]' (from source: uv sync --extra kyutai-mlx)
```

This deliberately mirrors the registry's own message so the two read alike.

## Audio contract

Mimi consumes **24 kHz mono float32 in 1920-sample (80 ms) frames**. The pipeline delivers ~100 ms chunks of s16 or μ-law. The module bridges the two:

```python
SUPPORTED_SAMPLE_RATES = frozenset({24000})
SUPPORTED_CHANNELS = frozenset({1})
SUPPORTED_ENCODINGS = frozenset({"linear16"})
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1
DEFAULT_ENCODING = "linear16"
```

- **Re-blocking.** A 100 ms chunk at 24 kHz is 2400 frames = 4800 bytes = 1.25 Mimi frames, so chunk and frame boundaries never align. The module keeps a byte buffer, emits every whole 1920-sample frame, and carries the remainder to the next chunk.
- **Conversion.** s16 → float32 via `np.frombuffer(chunk, "<i2").astype(np.float32) / 32768.0`.
- **Silence prefix.** At session start — and again after every `reset()` — the module feeds `stt_config.audio_silence_prefix_seconds` of zero frames (0 for the 1B model, 1.0 s = 12 frames for the 2.6B) and discards their results.

**Config consequence:** `engine.audio.sample_rate` must be `24000`. The default is 16000, so a Kyutai config that omits it fails fast at engine construction with the reconciliation error — correct behaviour, and worth an explicit example in [configuration.md](configuration.md).

## Lifecycle

This is where Kyutai departs most from the cloud modules, and the departures are forced, not stylistic.

| Concern | Cloud modules | `kyutai` |
|---|---|---|
| `start()` cost | open a websocket (ms) | load + warm a model (seconds), after a first-run multi-GB download |
| `connected` means | websocket is open | **model is loaded and warmed** |
| `stop()` | close the socket | stop feeding, `reset()` the session — **weights stay resident** |
| Reconnection | reconnect the socket | retry a failed `load()` with the same backoff |

**The weights must outlive `stop()`.** `engine.listen()` runs a full `start()`/`stop()` cycle per call; reloading a 1B model each time would make `listen` unusable. The backend is therefore created once (lazily, on first `start()`) and kept until the module is garbage-collected; `stop()` only halts the worker and calls `reset()`.

**Loading happens off the event loop**, on the worker thread, with `on_connected(True)` fired only once warm-up completes. The audio queue is **drained while the model loads**: audio captured during a first-run download would otherwise be transcribed minutes late. A load interrupted by `stop()` carries on in the background and the next `start()` awaits that same load rather than starting another.

A load failure (no network on first run, corrupt cache) is retried on the [standard backoff ladder](asr-module-interface.md#reconnection-contract) while the audio queue is drained, exactly as a cloud module retries a socket. A **`step()` failure** mid-session is treated the same way — the analogue of a dropped socket: `on_connected(False)`, the backend is `close()`d and discarded, and the retry loads a fresh one.

**`stop()` waits for the teardown.** The engine cancels the `start()` task as soon as `stop()` returns, so `stop()` blocks until `start()` has joined the worker and reset the session (bounded by a timeout).

### Threading

`gen.step()` blocks and is compute-heavy, so it cannot run on the event loop — this is the first module in the repo that isn't pure asyncio.

```
asyncio task                worker thread
───────────                 ─────────────
drain audio_queue
  → convert + re-block
  → queue.Queue[np.ndarray] ──▶ backend.step(frame)
                                  │
  on_utterance(...)  ◀── loop.call_soon_threadsafe / run_coroutine_threadsafe
```

`stop()` sets a stop event, drops a sentinel on the frame queue, and joins the worker with a timeout.

**One thread for every backend call.** The worker is a single-thread executor created with the backend and kept as long as it: `load()`, every `step()`, `reset()` and `close()` all run on it, across sessions. MLX streams and PyTorch streaming state are thread-sensitive, and this makes "which thread touched the model" a non-question; backends need no locking. The worker `reset()`s the backend as it leaves a session, so every session starts fresh.

### Step budget

MLX's `LmGen` preallocates `max_steps` and **raises `ValueError("reached max-steps N")`** once `step_idx` reaches it (verified in `moshi_mlx/models/generate.py`). At 12.5 Hz the default 4096 steps is **327 s ≈ 5.5 minutes** — well within an always-on session, so this is a live failure mode, not a theoretical one.

The module tracks `steps_remaining` and calls `backend.reset()` **at a boundary** once the budget falls below a margin — `max(2 × finalize_after_silence_s, 10 % of max_steps)`, about 33 s for the defaults — so a recycle never lands mid-utterance. A boundary is a finalization, or an **idle** stretch (nothing accumulated and no text for the silence timer's length), which is what recycles an always-on session nobody is speaking to. If the budget is exhausted while an utterance is still open, the worker resets before the next step and the module force-finalizes what it has and logs a warning. `max_steps` is configurable. Verified on the real model with `max_steps: 60`: recycles at boundaries leave the following transcripts intact.

### Backpressure

A cloud backend can't fall behind; a local model can. If the real-time factor exceeds 1.0, the audio queue grows without bound and latency diverges silently. The module tracks its lag (frames queued × 80 ms) and:

- logs a warning above `lag_warn_s` (default 2.0 s);
- above `lag_drop_s` (default 10.0 s), drops the oldest frames back down to `lag_warn_s` and logs at error level, so the transcript has a hole rather than an ever-growing delay.

Dropping is the right failure here: for dictation, stale text is worse than missing text.

## Utterance semantics

The model emits one text token per 80 ms frame and **never revises** what it has emitted. There is no server-side "final". Mapping onto `SpeechUtterance`:

- **Interim** — every step whose token is not padding (`0` or `3`) appends its piece to the open utterance and emits `SpeechUtterance(transcript=<accumulated>, is_final=False, confidence=None)`. Up to 12.5/s, comparable to Deepgram's interim rate.
- **Final** — emitted on whichever comes first:

  | Trigger | Condition | `confidence` |
  |---|---|---|
  | Semantic VAD | `end_of_turn_p > vad_threshold`, **rising edge only**; the final is emitted `audio_delay_seconds` later | `end_of_turn_p` at the edge |
  | Silence timer | `finalize_after_silence_s` of steps with no new text | `None` |

  The rising-edge latch plays the role of upstream's `last_print_was_vad` flag: the head stays above threshold for many consecutive frames, and without the latch every one of them would close an utterance. The latch clears when the probability falls back under the threshold.

  **The final waits out the text delay.** The VAD head tracks the *audio*, while the text runs `audio_delay_seconds` behind it — so the edge arrives **before** the utterance's last words do. Measured on the fixtures: the edge lands at 2.24 s and `" validate."` at 2.32–2.48 s. Finalizing on the edge itself would cut every utterance's tail off and re-emit it as a stray second final once the silence timer expired. So the edge only *arms* the final, which is emitted `ceil(audio_delay_seconds / 0.08)` steps later (7 steps = 0.56 s for the 1B model): every piece arriving in that window is, by construction, text for audio before the edge. Upstream never hits this because its scripts only print an `[end of turn]` marker into a continuous stream.

  **Both timers count steps, not seconds.** One step is always 80 ms of audio, so this is an audio-time clock like the [`fake` module](fake-module.md)'s — deterministic in tests, and immune to the model running faster or slower than real time.

- After a final the accumulator resets. **An empty accumulator emits nothing** — matching the Deepgram modules, which discard empty transcripts.

The `Segmenter` and everything downstream then behave exactly as they do for a cloud module: the module's finals are what `utterance` / `trigger_word` mode close on.

### Latency

Text for audio at time *t* arrives at *t + audio_delay_seconds*. On the VAD path a final lands at roughly `speech_end + (time for the head to cross the threshold) + delay` — **measured at ≈ 1 s** after speech ends for the 1B defaults. On the timer path it is `speech_end + delay + finalize_after_silence_s`, about 2.5 s. Any file-based source must set `trailing_silence_s` above that or no final will ever arrive.

## Configuration

```json
{
  "engine": {
    "audio": { "sample_rate": 24000 },
    "module": {
      "type": "kyutai",
      "backend": "auto",
      "hf_repo": "kyutai/stt-1b-en_fr-candle",
      "vad": true
    }
  }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `backend` | string | `"auto"` | `"auto"` \| `"mlx"` \| `"torch"`. `auto` = MLX on Apple Silicon when importable, else PyTorch. |
| `hf_repo` | string | `"kyutai/stt-1b-en_fr-candle"` | Hugging Face repo to load weights from. |
| `vad` | boolean | `true` | Use the semantic VAD head to finalize. Ignored with a warning when the repo declares no `extra_heads_num_heads`. |
| `vad_threshold` | float | `0.5` | End-of-turn probability above which an utterance closes. |
| `finalize_after_silence_s` | float | `2.0` | Fallback: close an utterance after this long with no new text. |
| `max_steps` | int | `4096` | Generator step budget before a session recycle (~5.5 min). |
| `device` | string | `"auto"` | **`torch` backend only.** `"auto"` \| `"cuda"` \| `"mps"` \| `"cpu"`. |
| `lag_warn_s` / `lag_drop_s` | float | `2.0` / `10.0` | Backpressure thresholds. |

**No `api_key` / `api_key_env`** — the module authenticates with nothing. This matters for e2e: `helpers.require_api_key` never skips a keyless module (see [Testing](#testing)).

Validation in `__init__` (per the [constructor contract](asr-module-interface.md#module-constructor-contract)): unknown `backend`; unavailable backend (→ the `ImportError` above); empty `hf_repo`; non-boolean `vad`; non-integer or non-positive `max_steps`; `vad_threshold` outside `(0, 1)`; negative `finalize_after_silence_s`; unknown `device`; non-positive `lag_warn_s`; `lag_drop_s <= lag_warn_s`.

## Installation

```toml
[project.optional-dependencies]
kyutai-mlx = [
  "moshi-mlx>=0.3; sys_platform == 'darwin' and platform_machine == 'arm64'",
]
kyutai-torch = ["moshi>=0.2.11"]

# Public "everything" target — every extra, no exceptions.
all = ["asr-engine[deepgram,mcp,kyutai-mlx,kyutai-torch]"]

[dependency-groups]
# Contributors get the MLX backend, not torch. See specs/project.md.
dev = ["asr-engine[deepgram,mcp,kyutai-mlx]", ...]
```

- The **environment marker is load-bearing**, not cosmetic: `mlx` publishes no Linux wheels, so an unmarked `kyutai-mlx` inside `all` would break `uv sync` on Linux outright. With the marker the extra resolves to nothing there.
- **Both extras join `all`; only `kyutai-mlx` joins `dev`.** This is what the `all`-vs-`dev` split in [project.md](project.md) exists for: `pip install 'asr-engine[all]'` stays honest and ships the torch backend, while a plain `uv sync` does not drag ~2.5 GB of torch into every contributor's venv. On an Apple Silicon machine `dev` therefore gets a working, type-checked, e2e-runnable MLX path; on Linux the marker makes `kyutai-mlx` resolve to nothing, so a Linux contributor working on this module adds `--extra kyutai-torch` explicitly.
- Consequence: `_kyutai_torch.py` is not type-checkable in a default dev environment and needs a file-level pyright suppression for the missing `moshi` import. The same is true of `_kyutai_mlx.py` on Linux, so **both** backend files carry one.
- Both packages pin **`sounddevice==0.5`**, which this project depends on unpinned. It has bitten once already: 0.5.0's `query_devices()` types differently from 0.5.5's under pyright, so `AudioCapture.list_devices()` annotates the result as `Any`.
- **The pins ripple through the dev venv.** `moshi-mlx` also caps `numpy` (< 2.3), `huggingface-hub` (< 1) and others, so adding it to the `dev` group moved the lock back on numpy, pydantic, starlette, websockets and gradio (6.26 → 5.38 in the synced venv). Every test tier passes on the older set; it is the price of type-checking and e2e-testing this module by default.
- `moshi_mlx` ships `py.typed` without re-exporting its public names, and the `sentencepiece` / `rustymimi` stubs reject their documented constructor arguments, so `_kyutai_mlx.py`'s suppression also covers `reportPrivateImportUsage` and `reportCallIssue`.

## Testing

### Fast tier — `tests/modules/test_kyutai.py`

The [speed rule](testing.md) forbids loading a real model here, so every test drives `KyutaiModule` with a **scripted fake backend** (a list of `StepResult`s) injected through the seam. That keeps the fast tier passing with *no* Kyutai extra installed — which is also what makes the seam worth having.

- **Re-blocking:** a sequence of 4800-byte chunks yields exactly-1920-sample frames with the remainder carried; a partial final chunk is not emitted.
- **Conversion:** s16 extremes map to the expected float32 values.
- **Accumulation:** a scripted token sequence produces the exact interim transcripts, and padding tokens (0, 3) produce no event.
- **VAD finalization:** a sustained above-threshold run closes **one** utterance, not one per frame (the rising-edge latch); `confidence` carries the probability; the final waits out `audio_delay_s`, so text arriving after the edge is included.
- **Timer finalization:** with `vad: false`, silence closes the utterance at the configured time; with text still arriving, it does not.
- **Empty suppression:** a finalization with nothing accumulated emits no utterance.
- **Step budget:** a backend reporting a low budget triggers `reset()` at a finalization boundary and when idle, and a mid-utterance exhaustion force-finalizes first.
- **Backpressure:** lag past `lag_drop_s` drops the oldest frames and keeps the newest.
- **Lifecycle:** `on_connected(True)` only after `load()` returns, with load-time audio dropped; `stop()` unblocks an idle `start()`, and one interrupted mid-load; `stop()` does **not** discard the loaded backend, and every backend call lands on one thread; a failed `load()` or `step()` is retried with a fresh backend.
- **Backend selection:** `auto` resolves per monkeypatched platform; a forced-unavailable backend raises the install-hint `ImportError`.
- **Validation:** one test per branch above.

### E2E tier

A `MODULES` row in `tests-e2e/helpers.py` gives it the standard `test_engine_streams` conformance run, on the MLX backend. It needed two things from [e2e-testing.md](e2e-testing.md):

1. **24 kHz fixtures.** File sources are validated, not resampled, so `sample_24000_theskyisblue.wav` and `sample_24000_theskyisbluevalidate.wav` were resampled from the 44.1 kHz MP3s, and each `MODULES` row now names the `ModuleAudio` (format + fixtures) it is driven with.
2. **A second skip axis.** `require_api_key` skips on a missing key; this module has no key, so it would never skip and would instead try to download gigabytes inside the per-test timeout. `require_local_model(...)` skips unless `ASR_ENGINE_E2E_KYUTAI=1` is set (the caller vouching the weights are pre-fetched) **and** the backend imports.

```bash
uv run python scripts/benchmark_kyutai.py                              # pre-fetches the weights
zsh -ic 'ASR_ENGINE_E2E_KYUTAI=1 uv run pytest tests-e2e -k kyutai'
```

Its `silence_s` is 4.0 s: it must exceed `audio_delay_seconds + finalize_after_silence_s` (≈ 2.5 s) so that even the timer path finalizes inside the trailing silence.

## Files

| Path | Role |
|---|---|
| `src/asr_engine/modules/kyutai.py` | `KyutaiModule` + `KyutaiBackend` protocol + `StepResult` |
| `src/asr_engine/modules/_kyutai_mlx.py` | MLX backend (`moshi_mlx`) |
| `src/asr_engine/modules/_kyutai_torch.py` | PyTorch backend (`moshi`) — **unverified**, see Open questions |
| `scripts/benchmark_kyutai.py` | Real-time-factor benchmark (MLX); also pre-fetches the weights |
| `tests/modules/test_kyutai.py` | Fast tier, on the scripted fake backend |
| `tests-e2e/fixtures/sample_24000_*.wav` | 24 kHz conformance fixtures |
| `config.kyutai.example.json` | A complete server config for this module |

## Non-goals

- **The Rust server.** `moshi-server` over websockets is upstream's production path and would be a *different* module (`kyutai_server`) with a cloud-like lifecycle. It needs cargo + CUDA and a separately managed process; out of scope here.
- **Prompting.** Upstream's text/audio prompt feature is explicitly experimental and "very sensitive to the prompt provided".
- **Batching.** The engine is single-stream; the model's 400-streams-per-H100 batching has no consumer here.

## Open questions

- **Word-level timestamps.** The model returns them and `SpeechUtterance` has nowhere to put them. Adding fields touches every module and every consumer — deferred until something needs them.
- **The PyTorch/Linux path ships unverified.** No Linux + CUDA machine was available, and `kyutai-torch` is not in the dev environment, so `_kyutai_torch.py` is written from upstream's reference script, type-checked only under suppression, and has never been run. Everything *above* the seam is covered (fast tier on a scripted backend, e2e on MLX); the file itself needs a real run by a Linux user. Older, slower Apple Silicon is likewise unmeasured — see [Measured](#measured-the-viability-gate).
- **Does `reset()` hurt accuracy?** Recycling the generator discards the model's context. Upstream never resets (it simply dies at `max_steps`). Recycles at a boundary showed no damage on the short fixtures; the cost of a forced mid-utterance reset, and any effect on long-form context, is unmeasured.
- **Should a retracted end-of-turn cancel the armed final?** If the head crosses the threshold and falls back within `audio_delay_seconds` (the speaker resumed), the armed final still fires, splitting the utterance there. Cancelling on the fall would be the analogue of Flux's `TurnResumed`; deferred until real use shows the split is a problem.
