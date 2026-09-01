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

> **Dictation update (2026-09-01):** the always-on segmentation mode is fixed to
> `utterance`; the configurable `segmentation_mode` is gone. Aggregation is done
> through *dictation* sessions (`start_dictation`/`stop_dictation`) or `listen`,
> both of which temporarily switch the mode and revert to `utterance` when they
> end. `set_segmentation_mode` becomes the private `_set_segmentation_mode`,
> called only by `listen` and the dictation methods; `listen` now also accepts
> `utterance` mode. New setters `set_listen_default_segmentation_mode` and
> `set_dictation_default_segmentation_mode`. A new `auto_start_dictation` config
> flag lets a caller start the server directly in a persistent dictation (like
> `auto_start`, it is a signal to the caller, not acted on by the engine). To be
> implemented by
> [plans/202609012010_dictation-sessions.md](../plans/202609012010_dictation-sessions.md).

> **Refactor note (2026-08-31):** `ASREngine` becomes the self-contained heart of
> the repo — constructed from a single `ASREngineConfig`, owning audio, module,
> segmentation and sound feedback, so it is usable
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
`auto_start`, `auto_start_dictation`, `segmentation` params,
`listen_default_segmentation_mode`, `dictation_default_segmentation_mode`, and
`sound_feedback` settings. Both callbacks default to a no-op and are settable
afterwards.

The engine's segmentation mode always **begins as `utterance`** — there is no
configurable always-on mode. Aggregation happens only inside a `listen` call or a
dictation session, both of which set a mode and revert to `utterance` when they
end. `config.dictation_default_segmentation_mode` and
`config.listen_default_segmentation_mode` are stored as the defaults those two
paths fall back to; neither changes the mode at construction.

The engine **never configures logging** — it does not set levels, add handlers,
or call `basicConfig`. It only acquires a module logger like every other library
module (`log = logging.getLogger(__name__)`); the application layer owns level
and handler configuration (see [project.md](project.md)).

At construction the engine:

- Instantiates the ASR module from `config.module` (raising `ValueError` on an
  unknown `type`).
- **Reconciles the audio format:** builds the desired `AudioFormat` from
  `config.audio` (`sample_rate`/`channels`/`encoding`) and resolves it against the
  module class's declared support via `reconcile_audio_format(...)`, using
  `config.audio.on_unsupported_format` (`"error"` fails fast here; `"fallback"`
  uses module defaults with a warning — see
  [asr-module-interface.md](asr-module-interface.md)). The resolved format is
  exposed as the read-only `audio_format` property.
- Builds its `Segmenter` in `utterance` mode from the `config.segmentation`
  params.
- Constructs a `SoundFeedback` (or a no-op stub when `sound_feedback.enabled` is
  `false`) from `config.sound_feedback`; the engine plays cues itself inside
  `listen` (see [sound-feedback.md](sound-feedback.md)).

At `start()` the engine selects its own audio source from config: an injected
`audio_source` if given, else a `FileAudioSource` when `config.audio.audio_file`
is set, else a live `AudioCapture` on `config.audio.device`. The capture layer
and the module are both given the reconciled `audio_format`.

`config.auto_start` and `config.auto_start_dictation` are **not** acted on by the
engine itself — they are signals to the caller (the MCP server). `auto_start`
says whether to call `start()` at startup; `auto_start_dictation` says whether to
follow that `start()` with `start_dictation(end_on_final_segment=False)`, so the
always-on `asr://segment` stream aggregates continuously from startup in
`dictation_default_segmentation_mode`. (The engine can't act on either itself:
`listen()` reuses `start()`, so a `start()` that auto-entered dictation would
clobber the mode `listen` just set.)

### `_start_with_segmentation_mode` (private)

```python
async def _start_with_segmentation_mode(self, segmentation_mode: str) -> None: ...
```

The internal start primitive that both the public `start()` and `listen` call, so
the segment mode is set **as part of** starting rather than as a separate step:

- Public `start()` is exactly `await self._start_with_segmentation_mode("utterance")` — the always-on
  stream is `utterance`.
- `listen` is `await self._start_with_segmentation_mode(mode)` with its resolved mode.

`_start_with_segmentation_mode` builds the `Segmenter` for `segmentation_mode` (raising `ValueError` on
an unknown mode *before* any capture starts), then starts audio capture, the ASR
module, and the segmenter, and marks the engine running. There is **no public
`set_segmentation_mode`**: at rest the mode is `utterance`, and the only ways to
run in another mode are `listen` (which passes it to `_start_with_segmentation_mode`) and a dictation
session (which swaps it live via `_set_segmentation_mode` below).

### `_set_segmentation_mode` (private)

```python
async def _set_segmentation_mode(self, mode: str) -> None: ...
```

The **live, in-run** mode swap, used only by `start_dictation` /
`stop_dictation` to change the mode of an already-running engine (`_start_with_segmentation_mode` sets
the mode at startup; `listen` never calls this). It switches the segment mode
only, keeping the current segmentation params, and rebuilds the internal
`Segmenter`, discarding any in-progress segment. Because the switch happens while
the engine is running, it is **correctness-critical** that it:

1. **Validates first** — build the candidate `Segmenter` (which raises
   `ValueError` on an unknown mode) *before* mutating any stored state, so a bad
   mode leaves the engine untouched.
2. **Stops the old segmenter before swapping** — `await self._segmenter.stop()`
   so a `timeout`-mode segmenter's timers can't fire through the engine callback
   after the switch (otherwise a stale timeout segment could close after the mode
   already changed — see `_analysis.md` item 1).
3. Swaps in the candidate and `await`s its `start()`.

Mode switches and dictation state transitions are serialized by a single engine
lock so overlapping `listen` / dictation calls can't interleave (see
`_analysis.md` item 2).

### `set_segmentation_params`

```python
async def set_segmentation_params(
    self,
    *,
    trigger_words: list[str] | None = None,
    initial_silence_timeout_s: float | None = None,
    end_of_speech_timeout_s: float | None = None,
) -> None: ...
```

Updates the segmentation params (omitted params keep their current values) and
rebuilds the `Segmenter` under the current mode, discarding any in-progress
segment (stopping the old segmenter before swapping, like `_set_segmentation_mode`).
This is the *only* way to change trigger words / timeouts after construction —
dictation and `listen` never touch them.

### `segmentation_mode`

```python
@property
def segmentation_mode(self) -> str: ...
```

Read-only accessor for the **current** segment mode (`"utterance"` /
`"trigger_word"` / `"timeout"`). It reads `utterance` at rest and reflects the
active mode while a dictation session or a `listen` is in progress (both revert
to `utterance` when they end). Complements `status()` (which reports only
`running`/`connected`) so a direct consumer can display the active mode without
shadowing engine state.

### `dictating`

```python
@property
def dictating(self) -> bool: ...
```

Read-only: whether a dictation session is currently active. `False` at rest and
during a `listen`; `True` between `start_dictation` and the dictation ending
(either `stop_dictation`, or the first final segment when
`end_on_final_segment=True`).

### Dictation

A dictation session is a **non-blocking** override of the always-on segmentation
mode on an already-running engine — the long-running counterpart to the one-shot
`listen`. Unlike `listen`, it never starts or stops the engine and never replaces
the public `on_speech_segment` callback: segments keep flowing to whatever
consumers are attached (e.g. the `asr://segment` resource) throughout.

```python
async def start_dictation(
    self,
    end_on_final_segment: bool = True,
    segmentation_mode: str | None = None,
) -> None: ...


async def stop_dictation(self) -> None: ...
```

`start_dictation`:

1. Raise `ValueError("ASR is not running. Start it before calling start_dictation.")`
   if the engine is not running.
2. Raise `ValueError("Dictation is already in progress.")` if already dictating.
3. Resolve the mode: `segmentation_mode` if given, else
   `dictation_default_segmentation_mode`. `_set_segmentation_mode(mode)` (which
   raises `ValueError` on an unknown mode).
4. Record dictation state (`dictating=True`, remembering `end_on_final_segment`)
   and return immediately. Segments continue to flow through `on_speech_segment`.

While dictating, the engine watches the segment stream: if
`end_on_final_segment` is `True`, the **first** closed segment
(`is_final=True`, any `end_reason`) ends the dictation. The auto-end is scheduled
*after* the public callback has fired for that final segment (so consumers still
see it), and reverts the mode to `utterance`. This detection is internal to the
engine's segment path — it does **not** hijack `on_speech_segment` the way
`listen` does.

`stop_dictation`:

- Raise `ValueError("No dictation in progress.")` if not dictating.
- Clear dictation state and `_set_segmentation_mode("utterance")`. The engine
  keeps running.

Both are serialized with `listen` and mode changes by the engine lock. A
dictation and a `listen` can never overlap: `listen` requires the engine stopped,
`start_dictation` requires it running.

### `set_dictation_default_segmentation_mode` / `set_listen_default_segmentation_mode`

```python
def set_dictation_default_segmentation_mode(self, mode: str) -> None: ...
def set_listen_default_segmentation_mode(self, mode: str) -> None: ...
```

Update the stored default mode that `start_dictation(segmentation_mode=None)` /
`listen(mode=None)` fall back to. Each validates `mode` against
`"utterance"` / `"trigger_word"` / `"timeout"` (raising `ValueError` otherwise)
and does **not** change the engine's current mode — only the default a future
session resolves against.

### Lifecycle

- `async start()` — `await self._start_with_segmentation_mode("utterance")`: the always-on stream is
  `utterance`. The caller applies `auto_start_dictation` by calling
  `start_dictation` *after* `start()`.
- `async stop()` — stops the module, audio capture, and the segmenter's timers,
  clears any dictation state (a stopped engine is never dictating), and resets the
  stored segment mode to `utterance` so the `segmentation_mode` getter reads
  `utterance` at rest (the next `_start_with_segmentation_mode` sets the mode again anyway).
- `status() -> dict` — `{"running": bool, "connected": bool}` (unchanged).

### `listen`

A blocking, single-shot capture that runs the whole lifecycle and returns the
first closed segment. It is the engine primitive behind the `listen` MCP tool.

```python
async def listen(
    self,
    mode: str | None = None,  # "utterance" | "trigger_word" | "timeout"; None → default
    *,
    on_update: SegmentCallback | None = None,
) -> SpeechSegment: ...
```

`listen` takes **only a mode** — it never changes the segmentation params, which
stay exactly as configured (or as last set via `set_segmentation_params`). When
`mode` is `None`, the engine's `listen_default_segmentation_mode` is used. All
three modes are accepted, including `"utterance"` (in which `listen` returns after
the first final utterance).

Behaviour:

1. Raise `ValueError` if the engine is already running.
2. Resolve `mode` (falling back to `listen_default_segmentation_mode`).
3. Save the current `on_speech_segment` callback, then install an internal
   `on_speech_segment` that forwards every update to `on_update` (if given) and
   resolves a future on the first **final** segment.
4. Play the start sound cue, then `await self._start_with_segmentation_mode(mode)` (which sets the mode
   as it starts — no separate mode step), await the future, then `await stop()`
   and play the stop cue — always, even on error (try/finally).
5. Restore the saved callback. (`stop()` already reset the stored mode to
   `utterance`; `listen` never touches the segmentation params.)
6. Return the closed `SpeechSegment`.

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
`asr://utterance` and `asr://segment` resources. The always-on stream is
`utterance` mode; if `config.auto_start_dictation` is set, the server calls
`start_dictation(end_on_final_segment=False)` right after `start()` so the
`asr://segment` stream aggregates continuously (see
[configuration.md](configuration.md) and [mcp-server.md](mcp-server.md)).

### Dictation tools

The `start_dictation` / `stop_dictation` tools (via the tools layer) let an MCP
client switch the always-on stream into an aggregating mode without stopping the
engine — the long-running counterpart to `listen`. `asr-to-terminal` does not
call them itself; it relies on the server's `auto_start_dictation` (see
[asr-to-terminal.md](asr-to-terminal.md)).

### `listen` tool

The `listen` tool (via the transport-agnostic tools layer — see
[mcp-server.md](mcp-server.md)) calls `engine.listen(mode=None)`, which uses the
configured `listen_default_segmentation_mode` and plays its own sound-feedback
cues. The tool maps `on_update` to `notifications/progress`.

### `AsrToTerminal`

`AsrToTerminal` no longer segments; it subscribes to the server's `asr://segment`
resource and types each segment, sending Enter when a segment closes
(`is_final=True`). See [asr-to-terminal.md](asr-to-terminal.md).
