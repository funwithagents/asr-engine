---
code:
  - src/asr_engine/modules/fake/__init__.py
  - src/asr_engine/modules/fake/module.py
  - src/asr_engine/modules/__init__.py
tests:
  - tests/modules/test_fake.py
  - tests/test_engine.py
  - tests/conftest.py
---

# Fake ASR Module

**Status:** Implemented

## Purpose

`FakeASRModule` (registry key `"fake"`) is a **scripted test double** for the `ASRModule` interface. Instead of recognizing speech, it replays a configured list of utterances: for each one it emits word-by-word interim `SpeechUtterance`s spread evenly over the utterance's time window, then the full text as a final at the window's end.

It exists so that everything *downstream* of a module — the engine, the `Segmenter`, `listen`, dictation, `AsrTools`, the MCP server, and consumers like `asr-to-terminal` — can be exercised **deterministically, with no network and no credentials**, through the real module-selection path (config → registry → engine), including from a subprocess MCP server that can only be configured by file.

**It is not an ASR backend and must never be presented as one.** It ignores the audio content entirely. Real use means selecting a real module installed from an extra (e.g. `pip install 'asr-engine[deepgram]'` → `deepgram_v1`; see [asr-module-interface.md](../asr-module-interface.md) "Optional dependencies (extras)"). Every place it is documented — its docstring, the README module table, this spec — labels it a test double.

## Why it ships in the package (not in `tests/`)

- **Subprocess servers.** `tests-e2e/` drives the real `asr-engine-mcp` binary, which picks its module from a config file; only a registered module is reachable that way.
- **Downstream users.** Programs built on `import asr_engine` (agents, UIs, bridges) get a keyless, deterministic way to test their own integration — the same role `httpx.MockTransport` plays for HTTP clients.
- **Zero dependencies.** It needs nothing beyond the core install, so it is always available, unlike extras-gated modules.

## Configuration

```json
{
  "type": "fake",
  "utterances": [
    {"text": "the sky is blue", "start_s": 0.5, "end_s": 1.5},
    {"text": "the sky is blue validate", "start_s": 3.0, "end_s": 4.2, "confidence": 0.9}
  ]
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `utterances` | list of objects | no | `[]` | The script. An empty script is valid: the module connects, drains audio, and emits nothing (useful for lifecycle tests). |
| `utterances[].text` | string | yes | — | The transcript the "ASR" understood for this utterance. Emitted verbatim as the final. |
| `utterances[].start_s` | float | yes | — | Utterance start, in seconds on the module's [audio clock](#clock-audio-time). |
| `utterances[].end_s` | float | yes | — | Utterance end (time of the final), same clock. |
| `utterances[].confidence` | float / null | no | `null` | Carried on every `SpeechUtterance` (interims and final) of this utterance. |

No `api_key` / `api_key_env`: the module authenticates with nothing, so `tests-e2e/helpers.require_api_key` never skips it.

### Validation (`ValueError` in `__init__`)

Per the [module constructor contract](../asr-module-interface.md#module-constructor-contract), each of these is its own error with a message naming the offending utterance index:

- `utterances` is not a list, or an entry is not an object;
- `text` missing, not a string, or blank after stripping;
- `start_s` / `end_s` missing or not a number;
- `start_s < 0`;
- `end_s <= start_s`;
- utterances out of order or overlapping: `start_s` of entry *k* < `end_s` of entry *k−1* (touching — equal — is allowed).

## Behavior

### Clock: audio time

The module's clock is **audio time**, not wall time: elapsed seconds = bytes consumed from `audio_queue` ÷ the reconciled `AudioFormat`'s byte rate (`sample_rate × channels × bytes_per_sample`). This is the settled choice because:

- the module already must drain `audio_queue` (interface contract), so the clock costs nothing;
- with a **real-time source** (`AudioCapture`, `FileAudioSource`, `ScriptableAudioSource()`), audio time tracks wall time, so a live mic or a subprocess server replays the script at natural speed;
- with a **fast source** (`ScriptableAudioSource(real_time=False)`), audio time runs as fast as the loop drains, so fast-tier tests replay a multi-second script in milliseconds (the [testing.md](../testing.md) speed rule);
- replay is deterministic regardless of scheduler jitter: the same bytes always produce the same events in the same order.

Consequence: **the script only advances while audio flows.** If the source stops feeding (e.g. a `FileAudioSource` past its end plus `trailing_silence_s`), pending events are never emitted. Scripts must fit inside the audio the test provides. Wall-clock engine timers (`timeout` segmentation mode) still run on wall time, so timeout-mode tests should use a real-time source.

The clock resets to 0 on every `start()`: each start is a fresh "connection" that replays the script from the beginning. A `listen` (which starts and stops the engine) therefore replays the script each time.

### Emission schedule

For an utterance with `words = text.split()` and `n = len(words)`, `d = end_s − start_s`:

| Event | Time | `SpeechUtterance` |
|---|---|---|
| interim *i*, for *i* = 1 … *n*−1 | `start_s + i · d / n` | `transcript=" ".join(words[:i])`, `is_final=False` |
| final | `end_s` | `transcript=text` (verbatim), `is_final=True` |

- The *n*-th word's slot coincides with `end_s`, where the final replaces it — so no interim ever carries the complete text.
- A one-word utterance emits only the final.
- `confidence` is the utterance's configured value on every event.

Example — `{"text": "the sky is blue", "start_s": 1.0, "end_s": 2.0}` (n = 4, step 0.25 s):

| t (s) | transcript | is_final |
|---|---|---|
| 1.25 | `the` | false |
| 1.50 | `the sky` | false |
| 1.75 | `the sky is` | false |
| 2.00 | `the sky is blue` | true |

### Tick resolution

After each chunk is consumed the clock advances by that chunk's duration, then every not-yet-emitted event with `time <= clock` is emitted **in schedule order**, each awaited through `on_utterance` before the next. Timing resolution is therefore one chunk (~100 ms); several events due within one chunk are emitted back-to-back. An event is never emitted before its time, and never skipped.

### Lifecycle

- `start()` resets the clock and schedule, calls `on_connected(True)` immediately (there is no backend), then drains `audio_queue` — advancing the clock and emitting due events — until `stop()`. After the last event it keeps draining silently (the interface requires `start()` to run until stopped).
- `stop()` makes `start()` return promptly even while blocked on an empty queue, and calls `on_connected(False)`.
- No reconnection: there is no connection to lose, so the [reconnection contract](../asr-module-interface.md#reconnection-contract) is vacuously satisfied.

### Audio format

Declares support for **any** format: `SUPPORTED_SAMPLE_RATES = SUPPORTED_CHANNELS = SUPPORTED_ENCODINGS = None`, defaults 16000 / 1 / `linear16`. It never inspects sample values, so any reconciled format works; it uses the format only to convert bytes to seconds (`mulaw` = 1 byte/sample, `linear16` = 2).

## Test fixture

The fast tier gets a shared fixture in `tests/conftest.py` so tests don't hand-wire the module:

```python
@pytest.fixture
def fake_engine_factory() -> Callable[..., ASREngine]:
    """Build an ASREngine on the `fake` module fed by a fast silence source.

    fake_engine_factory(utterances, **engine_overrides) -> ASREngine
    """
```

It builds an `ASREngineConfig` with `module.type="fake"`, the given `utterances`, sound feedback disabled, and an injected `ScriptableAudioSource(real_time=False)`, forwarding `on_speech_utterance` / `on_speech_segment` and segmentation overrides. Engine-level tests use it instead of patching `REGISTRY` with ad-hoc mocks wherever the test is about downstream behavior (utterance → segment → tools), not about module-loading itself.

## Testing

`tests/modules/test_fake.py` (fast tier), driving `FakeASRModule` directly with a hand-fed queue:

- **Schedule:** a multi-word utterance yields the exact interim sequence and final at the right audio times (assert on `(clock_at_emit, transcript, is_final)` tuples), including the one-word and confidence cases.
- **Tick batching:** events due within one chunk are emitted in order; nothing emits early.
- **Audio clock:** the same script under `linear16` and `mulaw` (different byte rates) emits at the same audio times; no audio fed → no events.
- **Restart:** stop then start replays the script from t = 0.
- **Lifecycle:** `on_connected(True)` on start, `stop()` unblocks an idle `start()` and reports `False`.
- **Validation:** one test per error branch listed above.

Plus one engine-level test through the fixture: a two-utterance script in `trigger_word` mode produces the expected closed segment through the real `Segmenter`.

## Open questions

- **Scripted connection loss** (e.g. an `on_connected(False)` at time *t*) to test consumers' `connected` handling — deferred until a test needs it.
