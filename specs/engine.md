---
code:
  - src/asr_engine/engine.py
  - src/asr_engine/segmenter.py
  - src/asr_engine/speech_utils.py
tests:
  - tests/test_engine.py
  - tests/test_segmenter.py
  - tests/test_speech_utils.py
---

# ASR Engine Specification

**Status:** Implemented

> **Refactor note (2026-08-31):** `ASREngine` becomes the self-contained heart of
> the repo — constructed from a single `ASREngineConfig`, owning audio, module,
> segmentation, sound feedback, and its own logging level, so it is usable
> directly (via `import asr_engine`) without the MCP server. `set_segmentation_mode`
> becomes mode-only, segmentation params move to a new `set_segmentation_params`,
> and `listen` takes just a mode and never mutates the segmentation params.
> Implemented by
> [plans/202608311612_asr-engine-refactor.md](../plans/202608311612_asr-engine-refactor.md).
> (The package is renamed `asr_mcp` → `asr_engine` in the same plan; paths below
> use the new name.)

## Purpose

`ASREngine` owns the live speech pipeline and all *segmentation* logic. It wires
`AudioCapture` to the active ASR module, then exposes two independent streams to
its consumers:

- **Utterances** — one `SpeechUtterance` per ASR event (interim or final),
  passed through unchanged from the module.
- **Segments** — a `SpeechSegment` produced by aggregating utterances according
  to the current *segment mode*.

Segmentation used to live in a client-side `EndOfUtteranceDetector` that the
`listen` tool and `AsrToTerminal` each instantiated. It now lives entirely in
the engine (in the `Segmenter` helper), so every consumer sees the same
segmentation without re-implementing it. See the macro picture in
[architecture.md](architecture.md); this spec is the engine-level detail.

---

## Data types

### `SpeechUtterance`

A single ASR result — atomic, never an aggregation. Defined in
`modules/base.py` (it is the ASR module's output; see
[asr-module-interface.md](asr-module-interface.md)).

```python
@dataclass
class SpeechUtterance:
    transcript: str
    is_final: bool  # False = interim/partial, True = final
    confidence: float | None  # None if not provided by the backend
```

### `SpeechSegment`

An aggregation of one or more utterances, defined in `segmenter.py`.

```python
@dataclass
class SpeechSegment:
    transcript: str
    is_final: bool  # True once the segment is closed
    end_reason: str | None  # why the segment closed; None while open
    utterances: list[SpeechUtterance]  # the committed final utterances so far
```

| `end_reason` value          | When the segment closes |
|-----------------------------|-------------------------|
| `None`                      | Segment still open (interim update) |
| `"utterance"`               | `utterance` mode — every final utterance closes its own segment |
| `"trigger_word"`            | `trigger_word` mode — a final utterance contained a trigger word |
| `"end_of_speech_timeout"`   | `timeout` mode — silence exceeded `end_of_speech_timeout_s` |
| `"initial_silence_timeout"` | `timeout` mode — no event within `initial_silence_timeout_s` |

`utterances` holds the **final** utterances committed to the segment so far
(interims are not stored). In `trigger_word` mode the trigger-word utterance is
**not** included.

---

## Callbacks

The engine drives two async callbacks. Both are public, settable attributes and
both default to a no-op:

```python
UtteranceCallback = Callable[[SpeechUtterance], Awaitable[None]]
SegmentCallback = Callable[[SpeechSegment], Awaitable[None]]

engine.on_speech_utterance: UtteranceCallback
engine.on_speech_segment: SegmentCallback
```

- `on_speech_utterance` fires **for every** ASR event (interim and final),
  regardless of segment mode.
- `on_speech_segment` fires **for every** segment change: an interim update as
  the segment grows, and a final emission when the segment closes.

The two fire independently; a consumer may use either or both.

---

## Segment modes

The current mode is set with `set_segmentation_mode` (below) and defaults to
`"utterance"`. In every mode the engine emits an interim `SpeechSegment`
(`is_final=False`, `end_reason=None`) whose `transcript` is **the committed
final utterances joined by a space, followed by the current interim utterance**
— so subscribers always see the latest final plus the in-progress text.

### `utterance` mode

Segment == utterance (1:1). Each final utterance closes its own segment:

- Interim utterance → interim segment mirroring it.
- Final utterance → final segment (`is_final=True`, `end_reason="utterance"`,
  `utterances=[that utterance]`), then the segment resets.

No timers, no trigger words.

### `trigger_word` mode

The segment accumulates finals until one contains a trigger word
(case-insensitive substring match via `speech_utils.contains_trigger_word`):

- Interim → interim segment (committed finals + interim).
- Final without a trigger word → append to `utterances`; emit interim segment.
- Final **with** a trigger word → close the segment: `is_final=True`,
  `end_reason="trigger_word"`, `transcript` = committed finals joined (the
  trigger-word utterance is **excluded**). Then reset.

Timers are not used in this mode.

### `timeout` mode

Two timers run concurrently:

| Timer | Starts when | Fires after | `end_reason` |
|---|---|---|---|
| Initial-silence | segment begins (mode set / previous segment closed) | `initial_silence_timeout_s` with no event | `"initial_silence_timeout"` |
| End-of-speech | first event of the segment | `end_of_speech_timeout_s` of silence | `"end_of_speech_timeout"` |

- Every event (interim or final) resets the end-of-speech timer.
- Finals are appended to `utterances` and emit an interim segment.
- Whichever timer fires first closes the segment (`is_final=True` with the
  matching `end_reason`, `transcript` = committed finals joined), then resets and
  a fresh segment begins (initial-silence timer restarts).

---

## `Segmenter`

The engine composes a `Segmenter` (in `segmenter.py`) that implements the mode
semantics above. It is a continuous segmenter — it produces successive segments
for the always-on engine, not a single one.

```python
class Segmenter:
    def __init__(
        self,
        mode: str,  # "utterance" | "trigger_word" | "timeout"
        trigger_words: list[str],
        initial_silence_timeout_s: float,
        end_of_speech_timeout_s: float,
        emit: SegmentCallback,  # called for each segment change
    ) -> None: ...

    async def start(self) -> None:
        """Begin a segment (starts the initial-silence timer in timeout mode)."""

    async def on_utterance(self, utterance: SpeechUtterance) -> None:
        """Feed one utterance; may emit interim and/or final segments."""

    async def stop(self) -> None:
        """Cancel any running timers."""
```

`emit` is wired by the engine to call `self.on_speech_segment`.

---

## `ASREngine`

### Construction

```python
class ASREngine:
    def __init__(
        self,
        config: ASREngineConfig,
        *,
        on_speech_utterance: UtteranceCallback | None = None,
        on_speech_segment: SegmentCallback | None = None,
        audio_source: AudioSource | None = None,
    ) -> None: ...
```

The engine is constructed from a single `ASREngineConfig` (see
[configuration.md](configuration.md)), which carries the audio, module,
`auto_start`, `segmentation_mode`, `segmentation` params,
`listen_default_segmentation_mode`, `sound_feedback`, and `logging` settings.
Both callbacks default to a no-op and are settable afterwards.

At construction the engine:

- Sets the level on the **`asr_engine` package logger** from `config.logging.level`
  (level only — never `basicConfig`, never handler configuration; a direct
  importer keeps full control of formatting and handlers).
- Instantiates the ASR module from `config.module` (raising `ValueError` on an
  unknown `type`).
- Builds its `Segmenter` from `config.segmentation_mode` and the
  `config.segmentation` params.
- Constructs a `SoundFeedback` (or a no-op stub when `sound_feedback.enabled` is
  `false`) from `config.sound_feedback`; the engine plays cues itself inside
  `listen` (see [sound-feedback.md](sound-feedback.md)).

`config.auto_start` is **not** acted on by the engine itself — it is a signal to
the caller (the MCP server) about whether to call `start()` at startup.

### `set_segmentation_mode`

```python
def set_segmentation_mode(self, mode: str) -> None: ...
```

Switches the segment **mode only** (`"utterance"` / `"trigger_word"` /
`"timeout"`), keeping the current segmentation params. Rebuilds the internal
`Segmenter`, discarding any in-progress segment. Raises `ValueError` on an
unknown mode. Safe to call while the engine is running.

### `set_segmentation_params`

```python
def set_segmentation_params(
    self,
    *,
    trigger_words: list[str] | None = None,
    initial_silence_timeout_s: float | None = None,
    end_of_speech_timeout_s: float | None = None,
) -> None: ...
```

Updates the segmentation params (omitted params keep their current values) and
rebuilds the `Segmenter` under the current mode, discarding any in-progress
segment. This is the *only* way to change trigger words / timeouts after
construction — `set_segmentation_mode` and `listen` never touch them.

### Lifecycle

- `async start()` — starts audio capture and the ASR module, and starts the
  segmenter. On each ASR result the engine fires `on_speech_utterance` and feeds
  the utterance to the segmenter (which fires `on_speech_segment`).
- `async stop()` — stops the module, audio capture, and the segmenter's timers.
- `status() -> dict` — `{"running": bool, "connected": bool}` (unchanged).

### `listen`

A blocking, single-shot capture that runs the whole lifecycle and returns the
first closed segment. It is the engine primitive behind the `listen` MCP tool.

```python
async def listen(
    self,
    mode: str | None = None,  # "trigger_word" | "timeout"; None → config default
    *,
    on_update: SegmentCallback | None = None,
) -> SpeechSegment: ...
```

`listen` takes **only a mode** — it never changes the segmentation params, which
stay exactly as configured (or as last set via `set_segmentation_params`). When
`mode` is `None`, the engine's `listen_default_segmentation_mode` is used.

Behaviour:

1. Raise `ValueError` if the engine is already running.
2. Resolve `mode` (falling back to `listen_default_segmentation_mode`).
3. Save the current segment mode and `on_speech_segment` callback.
4. `set_segmentation_mode(mode)` (mode only — params untouched); install an internal
   `on_speech_segment` that forwards every update to `on_update` (if given) and
   resolves a future on the first **final** segment.
5. Play the start sound cue, then `await start()`, await the future, then
   `await stop()` and play the stop cue — always, even on error (try/finally).
6. Restore the saved segment mode and callback.
7. Return the closed `SpeechSegment`.

Sound-feedback cues are played by the engine itself (see
[sound-feedback.md](sound-feedback.md)); callers no longer wrap `listen` with
cue playback.

---

## Usage

### Direct import (no MCP)

`ASREngine(ASREngineConfig(...))` is fully usable on its own: set the two
callbacks (or pass them to the constructor), `await start()`, and consume
utterances/segments — no MCP server required.

### Always-on server

`server.py` sets `on_speech_utterance` and `on_speech_segment` to publish the
`asr://utterance` and `asr://segment` resources. The engine already applied its
`segmentation_mode` from `ASREngineConfig` at construction, so the server no
longer calls `set_segmentation_mode` at startup (see
[configuration.md](configuration.md) and [mcp-server.md](mcp-server.md)).

### `listen` tool

The `listen` tool (via the transport-agnostic tools layer — see
[mcp-server.md](mcp-server.md)) calls `engine.listen(mode=None)`, which uses the
configured `listen_default_segmentation_mode` and plays its own sound-feedback
cues. The tool maps `on_update` to `notifications/progress`.

### `AsrToTerminal`

`AsrToTerminal` no longer segments; it subscribes to the server's `asr://segment`
resource and types each segment, sending Enter when a segment closes
(`is_final=True`). See [asr-to-terminal.md](asr-to-terminal.md).
