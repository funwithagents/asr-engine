---
code:
  - src/asr_engine/modules/mistral/__init__.py
  - src/asr_engine/modules/mistral/realtime.py
  - src/asr_engine/modules/__init__.py
  - pyproject.toml
  - scripts/probe_mistral_realtime.py
tests:
  - tests/modules/test_mistral_realtime.py
  - tests-e2e/test_engine_modules.py
---

# Mistral Voxtral Realtime Module

**Status:** Implemented

## Purpose

`mistral-realtime` is a **cloud streaming** backend over Mistral's Voxtral 3 realtime transcription API (`voxtral-mini-transcribe-realtime-3`, released 2026-10-20, *Public Preview*). Compared with Deepgram it adds 45 languages with auto-detection, `context_bias` phrase boosting (useful for trigger words), and a per-session latency/accuracy trade-off (`target_streaming_delay_ms`). It costs $0.008 per audio minute.

Structurally it sits between the two existing real backends:

- **Transport like [deepgram.md](deepgram.md):** one authenticated WebSocket per session, audio streamed in, events streamed out, reconnect with backoff.
- **Text semantics like [kyutai.md](kyutai.md):** text arrives as append-only, already-final deltas behind a fixed streaming delay, and there is no server-side "final transcript" per utterance. The module builds interims and finals from the deltas, closing an utterance on the server's `turn.end` event.

**Upstream docs:** [Transcribe in realtime](https://docs.mistral.ai/studio/audio/speech-to-text/transcribe-in-realtime), [Turn detection](https://docs.mistral.ai/studio/audio/speech-to-text/turn-detection), [Model page](https://docs.mistral.ai/models/voxtral-mini-transcribe-realtime-3-0.md), [Migrate from Voxtral 2](https://docs.mistral.ai/studio/audio/migrate-from-voxtral-2).

### Measured (live probe, 2026-11-12)

`scripts/probe_mistral_realtime.py` streams a fixture at real-time pace and logs every server event against wall time since the first chunk. Nine runs on the `sample_*_theskyisbluevalidate` fixtures ("The sky is blue, validate.") and a joined 11 s, four-sentence recording (`hellohowareyoutoday` · 2.5 s · `Iamveryhappytomeetyou` · 0.4 s · `theskyisblue` · 2.5 s · `letsvalidatethis`):

| Measure | Result |
|---|---|
| Resolved model | `voxtral-mini-transcribe-realtime-3-0-4` (echoed in `session.created`) |
| Sample rates | **16000, 24000 and 44100 all transcribe correctly**, with identical text |
| **Turn events vs. text** | **Both run on the same delayed timeline.** A turn event for audio time *t* arrives at about `t + streaming_delay + 0.1 s` (`turn.start` for *t* = 0 arrived at 0.62 / 1.29 / 2.57 s for delays 480 / 1200 / 2400 ms). The upstream docs say turn events "don't wait for the streaming delay"; the measurements say they do. |
| A turn's tail text | **Always arrived at or before its `turn.end`** (at 2400 ms the final `.` arrived in the same millisecond) |
| Text after `turn.end` | Always **new speech**, never the closing turn's tail: ≥ 300 ms after `turn.end` (" Validate." at 1200 and 2400 ms delay, where the model split the fixture into two turns) |
| New speech without a `turn.start` | Seen once (" Validate." at 1200 ms): text arrived after `turn.end`, and no further turn event followed |
| Multi-sentence | One `turn.end` per 2.5 s pause; the 0.4 s pause stayed inside one turn; no text straddled two turns |
| `turn.end` after speech end | ≈ 1.2–2.3 s at the default delay |
| `eager_end` / `resume` | Fired 2–3 times **inside** single sentences |
| `input_audio.end` → `transcription.done` | ≈ 0.7–1.4 s |
| Idle (no audio sent) | ≈ **30 s**, then `error` `{code: 3804, "Timeout waiting for response from streaming transcription."}` and close `1011 "Upstream connection error"` |
| Bad API key | Handshake rejected: **HTTP 401** |
| Unknown model | Handshake rejected: **HTTP 403** |
| Undocumented session field | `verbatim: false` (left at default) |

A second probe session the same day, for VAD gating (`--trailing-silence`, `--idle`, `--burst-silence` on the 16 kHz "the sky is blue" fixture, delay 480 ms):

| Measure | Result |
|---|---|
| Stop sending right after the speech | **The tail text does not arrive.** " blue." came only 6 s later, when `input_audio.end` was sent. The server emits text for audio time *t* only once it has received audio up to about *t* + the streaming delay. |
| Stop sending after 0.3 s of real silence | Same: part of the tail (".") still missing until `input_audio.end` |
| Stop sending after 0.5 s of real silence | Tail complete 0.14 s after the last chunk |
| Stop, then 0.6 s of silence in **one unpaced message** | Tail complete **0.46 s** after the burst (0.56 s in a second run) |
| Same with a 1.0 s burst | Tail complete 0.22 s after the burst |
| `turn.end` in these runs | Not emitted until more audio arrived: a short burst does not end the turn. Enough silence does (see [VAD gating](#vad-gating)) |

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Registry key | **`mistral-realtime`** | The repo's convention: a **cloud** module is named after its provider and API (`deepgram-v1`, `deepgram-v2`), a **local** module after its model family (`kyutai`). So a future batch module for Mistral's file-transcription API (diarization, word timestamps) would be `mistral-batch` in the same package, and an on-device module over the open-weights Voxtral Realtime model would be `voxtral`, without either being confused with this one. |
| Package / extra | `modules/mistral/realtime.py`, extra **`mistral`** | Follows the checklist in [asr-module-interface.md](../asr-module-interface.md): a provider-named package, a provider-named extra. |
| Transport | **Raw `websockets`, not the `mistralai[realtime]` SDK** | The protocol is about six documented JSON message types. The SDK's documented `transcribe_stream` events don't include the `turn.*` events this design finalizes on, and its `connect()` surface is undocumented beyond opening a session. Owning the socket gives direct control of `input_audio.end`, reconnects and turn events. |
| Finalization | **`turn.end` closes the utterance after a short margin; a no-new-text timer as fallback** | Measured: a turn's text always precedes its `turn.end`, and text after it is new speech (see [Utterance semantics](#utterance-semantics)). |
| Turn detection | **On by default** (`turn_detection: {}` sent) | Without it the only end-of-utterance signal is the fallback timer. |
| Diarization | **Out of scope** | The API rejects `diarize` combined with `turn_detection`, and `SpeechUtterance` has no speaker field. |
| Sample rate | **`{16000, 24000, 44100}`** | The docs state only the 16 kHz default. These three are the rates the probe verified live (the fixture rates). 8000 / 48000 join only after a passing probe. |
| Idle sessions | **Park after `idle_close_s` (20 s) without audio, reopen on the next chunk** | The server kills a session after ≈ 30 s without audio (measured). The engine's own sources stream continuously, but the [`AudioSource` contract](../audio-source.md) doesn't promise that: an injected source may send speech only (push-to-talk, a network stream), and a file source runs dry. Parking turns that gap into a clean session end instead of a server error, a reconnect backoff and a spurious `connected: false`. Padding the gap with silence would bill audio that carries nothing. |
| Rejected handshake (401 / 403) | **Retry with the standard backoff, logging an error that names the likely cause** | The engine runs `start()` as a background task nobody awaits, so raising would fail silently. Retrying keeps the [reconnection contract](../asr-module-interface.md#reconnection-contract) (a key rotated in place recovers by itself), and the 8 s backoff ceiling keeps the request rate trivial. |

## API surface used

| Direction | Message | Use |
|---|---|---|
| connect | `wss://api.mistral.ai/v1/audio/transcriptions/realtime?model=<model>`, header `Authorization: Bearer <key>` | One connection = one session |
| ← | `session.created` | First message; the session is live: `on_connected(True)` |
| → | `session.update` `{audio_format, target_streaming_delay_ms, language?, context_bias?, turn_detection?}` | Sent **once, before any audio**. Settings lock at the first `input_audio.append`, so a later update is a server error. |
| ← | `session.updated` | Logged at debug level (it echoes the applied settings) |
| → | `input_audio.append` `{"audio": "<base64 pcm_s16le>"}` | One per audio-queue chunk (JSON text frame, not binary) |
| ← | `transcription.text.delta` `{text}` | Append-only, final on arrival, carries its own spacing and punctuation |
| ← | `turn.start` / `turn.eager_end` / `turn.resume` | Ignored |
| ← | `turn.end` `{time_s, end_reason}` | Closes the utterance (after `final_margin_ms`) |
| → | `input_audio.end` | Only when ending a session (stop or idle). It ends the session and is the API's only flush. |
| ← | `transcription.done` `{text, …}` | End of session after `input_audio.end` |
| ← | `error` `{error: {message, code}}` | Logged at warning level. A connection close that follows is handled by reconnection. |

No transcript event carries a confidence value or timestamps.

## Utterance semantics

The model streams text that it **never revises**, about `target_streaming_delay_ms` behind the audio, and its turn events run on that same delayed timeline. Mapping onto `SpeechUtterance`:

- **Interim:** every non-empty `transcription.text.delta` is appended verbatim (no separator added) to the open utterance, and the module emits `SpeechUtterance(transcript=<accumulated, stripped>, is_final=False, confidence=None)`.
- **Final:** emitted on whichever of these comes first:

  | Trigger | Condition | Emitted |
  |---|---|---|
  | Turn end | `turn.end` received | `final_margin_ms` (default 100) later |
  | Silence timer | `finalize_after_silence_s` with no new delta while the accumulator is non-empty | immediately |

  `confidence` is always `None`.

  **Why a margin and not the streaming delay.** Kyutai has to hold its final for the full text delay, because its end-of-turn head runs ahead of its text. Voxtral's doesn't: `turn.end` is emitted on the delayed timeline, after `min_silence_ms` of silence, so the turn's last words have already arrived (measured at every delay from 480 to 2400 ms). Text that arrives after `turn.end` is new speech, at least 300 ms later in the probe. Waiting the full delay would merge it into the closing final: at 2400 ms the next word " Validate." arrived 700 ms after `turn.end`. The margin only absorbs jitter between a turn's last delta and its `turn.end` on the same socket.

  **The silence timer covers speech without turn events.** At 1200 ms delay the server once streamed the text of a new word after `turn.end` without opening a new turn, so no `turn.end` ever closed it. The timer finalizes such text, and is also the only path with `turn_detection: false`.

  **Wall clock, not audio clock.** Unlike Kyutai, the module doesn't step the model, so it has no audio-step clock to count. Counting audio *sent* would stall whenever the input stops (a speech-only injected source, an exhausted file), and a pending final would never fire. Both timers are `asyncio` tasks on the event loop. The fast tier keeps them deterministic by setting small values rather than by sleeping long.

- After a final, the accumulator resets and both timers are cancelled. **An empty accumulator emits nothing**, matching the other modules, which discard empty transcripts.
- Emission is serialized: an interim and a timer-driven final never interleave, and a final always carries every delta received before it.

The `Segmenter` and everything downstream then behave as they do for every other module.

### Latency

On the turn path a final lands at `turn.end + final_margin_ms`: measured `turn.end` at ≈ 1.2–2.3 s after speech end at the default delay, so about 1.3–2.4 s. On the timer path it is `speech_end + streaming_delay_ms + finalize_after_silence_s`, about 2.5 s. A file-based e2e source must set `trailing_silence_s` above both.

## Configuration fields

| Field | Type | Default | Description |
|---|---|---|---|
| `api_key_env` | string | `"MISTRAL_API_KEY"` | Name of the env var holding the key; the key itself is never in the config ([setup.md](../setup.md) "Keys") |
| `model` | string | `"voxtral-mini-transcribe-realtime-3"` | The API also accepts `-3-0` (pinned) and `-latest` |
| `url` | string | `"wss://api.mistral.ai/v1/audio/transcriptions/realtime"` | Endpoint override (proxies, a future self-hosted server) |
| `language` | string \| null | `null` (auto-detect) | ISO 639-1 two-letter code |
| `streaming_delay_ms` | int | `480` | Sent as `target_streaming_delay_ms`. Must be 80–1200 or 2400. Lower is faster, higher is more accurate. |
| `context_bias` | list[string] | `[]` | Words and phrases to favour (e.g. the trigger words). Omitted from `session.update` when empty. |
| `turn_detection` | bool \| object | `true` | `true` sends `{}` (server defaults). `false` omits it and relies on the silence timer. An object is passed through after validation. |
| `final_margin_ms` | int | `100` | Delay between `turn.end` and the final (see [Utterance semantics](#utterance-semantics)) |
| `finalize_after_silence_s` | float | `2.0` | Fallback final with no new text |
| `idle_close_s` | float | `20.0` | Park the session after this long with no audio. Must stay under the server's ≈ 30 s idle kill. |
| `stop_timeout_s` | float | `5.0` | How long ending a session waits for `transcription.done` |

**Validation in `__init__`** (per the [constructor contract](../asr-module-interface.md#module-constructor-contract)), so that config mistakes fail at construction rather than as a server `error` mid-session. They are declared as `fields` (title "Mistral Voxtral Realtime"), `context_bias` and `turn_detection` as `schema_extras`, and the rules a `Field` cannot state are `config_rules` ([setup.md](../setup.md) "On a module"). Each failure raises `ConfigError` naming the field:

- no `api_key` is written in the config, and the key resolves (`MissingSecretError` naming the variable otherwise);
- `model` is a non-empty string, and `url` is a string starting with `ws://` or `wss://`;
- `language` is null or two ASCII letters;
- `streaming_delay_ms` is an int (not a bool) in 80–1200 or exactly 2400;
- `context_bias` is a list of non-empty strings;
- `turn_detection` is a bool, or an object whose only keys are `start_threshold`, `eager_end_threshold`, `end_threshold`, `min_silence_ms` and `end_timeout_ms`. Thresholds are numbers in (0, 1), durations are positive ints, and the server's own constraints hold: `start_threshold ≥ eager_end_threshold > end_threshold` and `end_timeout_ms ≥ min_silence_ms`, evaluated against server defaults for omitted keys (0.5 / 0.5 / 0.33 / 500 / 3000);
- `final_margin_ms` is an int ≥ 0; `finalize_after_silence_s > 0`; `stop_timeout_s > 0`; `0 < idle_close_s < 30`.

### Example

```json
{
  "type": "mistral-realtime",
  "api_key_env": "MISTRAL_API_KEY",
  "language": "en",
  "context_bias": ["validate"]
}
```

## Audio format

```python
SUPPORTED_SAMPLE_RATES = frozenset({16000, 24000, 44100})  # probe-verified only
SUPPORTED_CHANNELS = frozenset({1})
SUPPORTED_ENCODINGS = frozenset({"linear16"})
DEFAULT_SAMPLE_RATE, DEFAULT_CHANNELS, DEFAULT_ENCODING = 16000, 1, "linear16"
```

The module sends `audio_format: {"encoding": "pcm_s16le", "sample_rate": <reconciled rate>}` in `session.update`. Our `linear16` is s16 little-endian on every supported platform, so chunks pass through unchanged apart from base64. `mulaw` is not offered by the API, and the engine's reconciliation rejects it or falls back as configured.

## Lifecycle

- **`start()`** connects, waits for `session.created` (10 s handshake timeout), sends `session.update`, calls `on_connected(True)`, then runs two tasks until the session ends: a **sender** draining `audio_queue` into `input_audio.append`, and a **receiver** applying the [utterance semantics](#utterance-semantics).
- **Ending a session** (on stop or idle) sends `input_audio.end` and waits up to `stop_timeout_s` for `transcription.done`, so deltas still in flight land in the accumulator. It then emits whatever has accumulated as a final and closes the socket. `transcription.done.text` is the whole session transcript, so it isn't re-emitted.
- **`stop()`** asks the running session to end as above and **returns only once it has**, so the flushed final is delivered before the engine cancels the module task (the engine calls `module.stop()` and then cancels `start()`). It then calls `on_connected(False)`. A `stop()` while reconnecting returns at once.
- **Idle parking.** When no chunk arrives for `idle_close_s`, the session ends as above. The module stays *running* and `on_connected` is **not** set to `False`, because nothing is wrong and `is_running` should not report an outage. The next chunk opens a fresh session (connect → `session.created` → `session.update`), and is sent first once it is ready. A connect failure here enters normal reconnection. Parking only happens when the input actually stops (a speech-only injected source, an exhausted file source, a stalled device). The live mic and `ScriptableAudioSource` stream continuously, silence included, and never park.
- **Reconnection** follows the [reconnection contract](../asr-module-interface.md#reconnection-contract) (1, 2, 4, 8 s backoff, draining `audio_queue` meanwhile). On connection loss the module first emits the accumulator as a final, since every delta was already final text, and calls `on_connected(False)`. A reconnect is a **new session**: settings are re-sent, and context from the old session is lost. A rejected handshake logs at error level with the cause: HTTP 401 → "check the variable named by `api_key_env`", HTTP 403 → "check `model`".

## Endpointing, with and without a VAD stage

The module needs **no VAD in front of it**. End-of-utterance comes from the server's own `turn_detection` (`turn.end`), with the local silence timer as fallback. Both work on a continuous stream that includes silence, which is exactly what the mic delivers. This module, like `deepgram-v1`/`deepgram-v2` and `kyutai`, owns its endpointing.

**Cost consequence.** Without a VAD stage the engine streams everything it captures, silence included, and Voxtral bills per audio minute ($0.008). The bill is therefore the engine's *running time*, not speech time: about $0.48 per hour of `start()`→`stop()`, regardless of how much is said. That is bounded by the engine lifecycle (capture runs only between engine `start()` and `stop()`, which `listen` wraps) and is the same model as Deepgram's.

### VAD gating

`VAD_GATING = "supported"` ([vad.md](../vad.md)). With `engine.vad` set, the module receives only speech, the bill follows speech time, and room noise never leaves the machine. The gate removes the silence the server's turn detection needs, so on the gate's `END_OF_SPEECH` marker the module **gives that silence back**: the final still comes from the server's own `turn.end`, as it does ungated. No local timer decides when a gated utterance is over.

On a marker the send loop calls `finalize()`, which sends **2.0 s of silence** (`_FLUSH_SILENCE_S`, a module constant) as ordinary chunks of about 100 ms, one `input_audio.append` each. That silence does two things:

- it releases the text still behind the streaming delay (the probe above: the tail does not arrive without further audio);
- it is the pause the server's turn detection waits for, so the server sends `turn.end`, and the module emits the final `final_margin_ms` later on the [turn-end path](#utterance-semantics).

The silence timer (`finalize_after_silence_s`) stays the fallback, as ungated: if `turn.end` never comes, the final is emitted that long after the last delta. With `turn_detection: false` it is the only path.

`input_audio.end` would also end the turn, but it ends the session too, so it is not used: the session is kept and the next utterance goes out on it.

**Measured** (gated by Silero at its defaults, server turn detection at its defaults, the 44.1 kHz "the sky is blue" fixture):

| Silence sent on the marker | `turn.end` received | After the marker |
|---|---|---|
| 1.0 s | 0 of 3 | — |
| 1.5 s | 4 of 5 | 0.9–1.9 s |
| **2.0 s** | **9 of 9** | **0.57–1.91 s** |
| 2.5 s | 8 of 9 | 0.8–1.9 s |
| 3.5 s, in one message | 0 of 2 | rejected: "Audio chunk exceeds maximum size of 262144 bytes", and the server closes the connection |

Every `turn.end` came after the turn's last delta. From the end of the speech, the final arrived after a median of **1.23 s** (1.06–1.62 s over 10 runs; the marker itself comes `min_silence_duration_ms` after the speaker stops), against a median of 2.0 s ungated (1.8–2.6 s, once 4.9 s), where the server waits on real silence.

**Cost:** the silence is billed, 2.0 s per gated utterance (about $0.00027 at $0.008 per minute).

**Why not a local timer.** An earlier design sent `streaming_delay_ms` + 200 ms of silence and emitted the final once no delta had arrived for `streaming_delay_ms + final_margin_ms` (580 ms). The server answers only as it processes the silence, and a slow answer (" blue." up to 1.06 s after the marker) came after that window: the utterance was split into two finals (`"The sky is"`, `"blue."`), which made the live tier flaky. The server's `turn.end` is the signal that the turn's text is complete, so the module waits for it rather than guessing.

**Turn-detection settings.** 2.0 s is measured at the server's defaults (`min_silence_ms` 500). A larger `min_silence_ms` may need more silence than the module sends; the silence timer then closes the utterance instead.

During the gaps nothing new is needed: [idle parking](#lifecycle) ends the session after `idle_close_s` without a chunk and reopens one with the next. A marker read while parked (nothing is open to finalize) or while reconnecting is dropped.

## Installation

```toml
[project.optional-dependencies]
mistral = ["websockets>=14"]  # >=14: websockets.connect(..., additional_headers=…) is the asyncio client
```

The extra is light, so it is in both `all` and the `dev` group ([project.md](../project.md) holds what each one names).

`websockets` is already present transitively, but the extra names it so the module never depends on another extra's dependency tree. The module file imports `websockets` only in `imports()` and when it connects, so without the extra the module's config is still checked, and building it raises the `ImportError` of `imports()`, naming `mistral`.

Its `probe` lists `GET /v1/models` on the `url`'s host with the key and finds the configured `model` there ([setup.md](../setup.md) "probe").

## Testing

### Fast tier: `tests/modules/test_mistral_realtime.py`

The module talks to a **scripted fake connection** (records every client message, serves server events the test pushes) injected through a keyword-only `connect` constructor argument: a callable `(url, headers) → async context manager → connection` with `send(str)`, `recv() → str`. The default is `websockets.connect`. No network, no sleeping beyond a few tens of milliseconds of configured timer values.

- **Handshake:** the first client message is `session.update`, sent before any `input_audio.append`, and carries the reconciled rate, the delay, `language`/`context_bias` when set, and `turn_detection` per config (`{}` / omitted / passed through). The URL carries the model and the header the bearer key.
- **Audio:** queue chunks arrive as base64 that decodes back to the exact bytes, in order.
- **Interims:** a delta sequence yields the exact cumulative transcripts, with no separators added.
- **Turn end:** a delta arriving within the margin joins the closing final; a delta after it opens a new utterance instead of reopening the old one.
- **Timer fallback:** with `turn_detection: false`, silence closes the utterance. With text still arriving, it doesn't.
- **Empty suppression:** a `turn.end` with nothing accumulated emits nothing.
- **Stop flush:** `stop()` sends `input_audio.end`, takes deltas that arrive before `transcription.done` into the final, and returns within `stop_timeout_s` when `done` never comes; `on_connected` ends `False`.
- **Idle parking:** with no chunk for `idle_close_s`, the accumulator is flushed as a final, `input_audio.end` is sent, and `on_connected` is not called with `False`. The next chunk opens a new session whose first message is `session.update`, and that chunk's bytes follow it.
- **Reconnect:** a dropped socket emits the accumulator as a final, calls `on_connected(False)`, reconnects, and re-sends `session.update`. A failed handshake retries.
- **VAD gating:** an `END_OF_SPEECH` marker sends 2.0 s of silence as ordinary chunks, and no `input_audio.end`; the final comes from the server's `turn.end`, with the tail that arrived after the marker, and no local wait emits it before; without `turn.end` the silence timer emits it; the session is kept and a second utterance gets its own final; nothing accumulated emits nothing; a marker is dropped while reconnecting and does not reopen a parked session.
- **Validation:** one case per invalid-config rule above (scenario-style, per [testing.md](../testing.md)).

### Live tier: `tests-e2e/test_engine_modules.py`

A `MODULES` row: type `mistral-realtime`, `api_key_env: MISTRAL_API_KEY`, the 44.1 kHz MP3 `ModuleAudio` (as Deepgram), and `silence_s` of 3.0 s, above both the measured turn path (≤ 2.4 s) and the timer path (≈ 2.5 s). [ci.md](../ci.md) gains the `MISTRAL_API_KEY` secret and an expected-skips row. Verify with `zsh -ic 'uv run pytest tests-e2e -k mistral-realtime'`.

## Files

- `src/asr_engine/modules/mistral/__init__.py`: imports nothing
- `src/asr_engine/modules/mistral/realtime.py`: `MistralRealtimeModule`
- `src/asr_engine/modules/__init__.py`: `"mistral-realtime": MistralRealtimeModule`
- `pyproject.toml`: the `mistral` extra, `all`, `dev`
- `examples/configs/mistral-realtime.json`: a complete engine config for this module, with the VAD gate on
- `tests/modules/test_mistral_realtime.py`, `tests-e2e/helpers.py` (`MODULES` row), `.github/workflows/ci.yml` (secret mapping)
- Spec and doc touch-ups: [asr-module-interface.md](../asr-module-interface.md) (registry snippet, extras table), [configuration.md](../configuration.md) (example), [ci.md](../ci.md), [project.md](../project.md) (package list), `README.md` (module table and section)

## Open questions

None load-bearing. Deferrals:

- **Maximum session length.** Undocumented, and not probed beyond ≈ 75 s. If a session limit exists, the server's close lands mid-stream and reconnection recovers it, at the cost of one flushed final and a gap of about a second. If that proves disruptive, add Kyutai-style proactive recycling at an utterance boundary.
- **`turn.eager_end`** could start a speculative final earlier, retracted on `turn.resume`. `SpeechUtterance` has no retraction, and the probe saw it fire mid-sentence routinely, so this waits for a use case.
- **Self-hosting.** The previous generation (Voxtral Mini 4B Realtime 2602) has open weights (Apache 2.0) and runs on vLLM's `/v1/realtime`. Whether Voxtral 3 has open weights, or whether vLLM speaks this exact protocol, is unconfirmed. `url` keeps the door open.
- **Batch Voxtral** (diarization, word timestamps) as a separate module under `modules/mistral/`. It would declare `VAD_GATING = "required"`: the gate's end-of-speech marker is what chunks the audio into utterances ([vad.md](../vad.md) "Fit with planned modules").
