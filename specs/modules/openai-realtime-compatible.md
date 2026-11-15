---
code:
  - src/asr_engine/modules/openai_realtime_compatible/__init__.py
  - src/asr_engine/modules/openai_realtime_compatible/module.py
  - src/asr_engine/modules/__init__.py
  - pyproject.toml
  - scripts/probe_openai_realtime.py
tests:
  - tests/modules/test_openai_realtime_compatible.py
  - tests-e2e/test_engine_modules.py
---

# OpenAI Realtime-Compatible Module

**Status:** Implemented

## Purpose

`openai_realtime_compatible` is a **cloud or self-hosted streaming** backend that speaks the transcription mode of the **OpenAI Realtime API** (GA): one WebSocket per session, base64 PCM16 audio in, `conversation.item.input_audio_transcription.*` events out. Its `url` defaults to hosted OpenAI. Any server implementing the same wire protocol works too: LiteLLM's proxy (e.g. Google Chirp behind it), NVIDIA Dynamo, LocalAI and others.

It is a **protocol module**, not a provider module. The server, the model behind it and its pricing are chosen by `url` and `model`, and the module makes no assumption beyond the protocol. Hence the name: the key `openai_realtime` reads as "OpenAI's Realtime API", and the `_compatible` suffix says the module talks to anything compatible with it. That leaves `openai_realtime` free for an OpenAI-only module if OpenAI's own API ever diverges from what compatible servers implement. The same split already exists in the sibling `tts-engine` project (`openai` is the vendor module, `openai-compatible` the endpoint module).

Compared with the existing backends:

- **Transport like [mistral.md](mistral.md):** a raw WebSocket owned by the module, a JSON session handshake, reconnection with backoff, and fake-connection fast tests.
- **Text semantics unlike Mistral's:** the server owns the utterance boundary. Each utterance is an **item** (`item_id`), its text arrives as incremental deltas, and the server closes it with a `completed` event carrying the authoritative transcript. The module maps deltas to interims and `completed` to finals. It needs no timers.

**Upstream docs:** [Realtime transcription guide](https://developers.openai.com/api/docs/guides/realtime-transcription), [Voice activity detection](https://developers.openai.com/api/docs/guides/realtime-vad), [client events](https://developers.openai.com/api/reference/resources/realtime/client-events), [server events](https://developers.openai.com/api/reference/resources/realtime/server-events). Compatible servers: [LiteLLM](https://docs.litellm.ai/docs/providers/vertex_transcription), [NVIDIA Dynamo](https://docs.nvidia.com/dynamo/dev/speech/speech-to-text), [LocalAI](https://localai.io/docs/features/openai-realtime/).

### Measured (live probe, hosted OpenAI, 2026-11-13)

`scripts/probe_openai_realtime.py` streams a fixture at real-time pace and logs every server event against wall time since the first chunk. Runs used `sample_24000_theskyisbluevalidate.wav` ("The sky is blue, validate.", 1.9 s of speech).

| Measure | Result |
|---|---|
| Connect | `wss://api.openai.com/v1/realtime?intent=transcription`, `Authorization: Bearer <key>`, no `OpenAI-Beta` header. The GA `session.update` (`session.type = "transcription"`) is accepted and answered with `session.updated`. |
| `session.created` defaults | `turn_detection: {type: server_vad, threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 200}`, `transcription: null` (no model until the client sets one) |
| Session lifetime | `expires_at` = creation + ≈ 3600 s |
| **Sample rates** | **24000 only.** 16000 is rejected ("Expected a value >= 24000"), 44100 and 48000 too ("Expected a value <= 24000"). The error arrives as an `error` event, not a close. The session then runs on its defaults with no transcription model and never transcribes. |
| Server VAD (`gpt-4o-transcribe`) | `speech_stopped` + `committed` ≈ 0.3 s after speech end. The deltas arrive **in a burst after the commit**, ≈ 0.6 s later, then `completed` ≈ 0.2 s after them. Final ≈ **1.1 s** after speech end. No text arrives during speech. |
| Manual commit (`turn_detection: null`) | `committed` 0.13 s after `input_audio_buffer.commit`, deltas 0.35 s, `completed` **0.6 s** after it |
| `gpt-realtime-whisper` (`turn_detection: null`) | **True streaming:** one delta per word *during* speech, already carrying the item's `item_id` before any commit. The tail arrives after the commit, then `completed`. Turn detection is unsupported for this model (per the guide). |
| `whisper-1` | One delta with the whole transcript, immediately followed by `completed` |
| Deltas | **Incremental** (`"The"`, `" sky"`, `" is"`, …). They carry their own spacing. The completed `transcript` equals their concatenation, up to a leading space. |
| Empty or short commit | `error` `{code: "input_audio_buffer_commit_empty", "… Expected at least 100ms of audio …"}`. A 50 ms buffer is "empty" too. The session continues. |
| `input_audio_buffer.clear` | Answered with `input_audio_buffer.cleared` |
| Idle (no audio sent) | Socket still open after 70 s. No idle kill observed. |
| Extra events | `conversation.item.added` / `conversation.item.done` per item (ignored) |

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Registry key | **`openai_realtime_compatible`** | A protocol module: any server speaking OpenAI's Realtime transcription protocol. See [Purpose](#purpose). |
| Package / extra | `modules/openai_realtime_compatible/module.py`, extra **`openai-realtime-compatible`** (`websockets>=14`) | A protocol module has no provider to name its extra after, so the extra is named after the module key (`_` → `-`), the rule in [asr-module-interface.md](../asr-module-interface.md) "Optional dependencies". Installing it reads as what you get. |
| Transport | **Raw `websockets`, not the `openai` SDK** | About eight JSON message types. The SDK's realtime client is built around OpenAI's conversation sessions and pulls in a much larger dependency for nothing used here. Owning the socket also keeps the module server-neutral, as with `mistral_realtime`. |
| Session shape | **GA `session.update`, `session.type = "transcription"`** | What hosted OpenAI documents and accepts (measured), and what Dynamo and LiteLLM accept. The beta `transcription_session.update` is no longer documented. |
| Handshake | **Wait for `session.updated`; an `error` instead fails the session** | Measured: a rejected `session.update` (e.g. an unsupported rate) leaves a live session with no transcription model that silently never transcribes. Treating it as a failed handshake surfaces the server's message at error level and retries with the standard backoff, as `mistral_realtime` does for a rejected handshake. |
| Utterance boundary | **The server's**: `completed` closes an item | It carries the authoritative transcript. With server turn detection the server commits on silence. Without it, the VAD gate's `finalize()` or `stop()` commits. |
| Turn detection | **On by default** (`{"type": "server_vad"}`, server-default parameters) | Without it nothing commits on a continuous stream, and no final ever comes. `false` is for models that don't support it (`gpt-realtime-whisper`) or servers that don't (Dynamo), and then needs `engine.vad`. |
| Sample rate | **`{24000}`** | The only rate hosted OpenAI accepts (measured), and the protocol's documented rate. Compatible servers that accept more (LiteLLM: 8–48 kHz) still accept 24 kHz. A 16 kHz or 44.1 kHz input is a resampling concern of the engine (a deferral), not of this module. |
| Idle sessions | **No parking** | No idle kill observed in 70 s, and the 60 min session expiry is covered by reconnection. Parking stays a `mistral_realtime` concern. |
| Missing key | **Required for hosted OpenAI, optional elsewhere** | A local server (Dynamo, LocalAI) usually needs no key. A key-less config pointed at `api.openai.com` can only fail, so it is rejected at construction. |

## API surface used

| Direction | Message | Use |
|---|---|---|
| connect | `url` as configured (default `wss://api.openai.com/v1/realtime?intent=transcription`), header `Authorization: Bearer <key>` when a key is configured | One connection = one session |
| ← | `session.created` | First message (10 s handshake timeout) |
| → | `session.update` `{session: {type: "transcription", audio: {input: {format, transcription, turn_detection, noise_reduction?}}}}` | Sent once, before any audio |
| ← | `session.updated` | The session is live: `on_connected(True)`. An `error` instead fails the handshake. |
| → | `input_audio_buffer.append` `{audio: "<base64 pcm16>"}` | One per audio-queue chunk |
| → | `input_audio_buffer.commit` | `finalize()` (VAD marker) and `stop()`, when the buffer holds ≥ 100 ms |
| → | `input_audio_buffer.clear` | `finalize()` when the buffer holds < 100 ms, or when the server's own turn detection already committed the utterance |
| ← | `input_audio_buffer.speech_started` / `committed` | Track whether the server has an open turn (for `finalize()`), and register the item in commit order |
| ← | `conversation.item.input_audio_transcription.delta` `{item_id, delta}` | Incremental text for one item |
| ← | `conversation.item.input_audio_transcription.completed` `{item_id, transcript}` | The item's final text |
| ← | `conversation.item.input_audio_transcription.failed` `{item_id, error}` | Logged at warning level; the item closes with whatever text it had |
| ← | `error` `{error: {code, message}}` | Logged at warning level, except `input_audio_buffer_commit_empty` (debug: an expected race, see [VAD gating](#vad-gating)) |
| ← | anything else (`speech_stopped`, `conversation.item.*`, `cleared`, …) | Ignored |

No event used here carries a confidence value.

## Utterance semantics

Each server **item** is one utterance. Items are tracked in the order the module first hears of them (a `committed`, or a `delta` for a streaming model that sends text before the commit), because the guide warns that completions of different items are not guaranteed to arrive in order.

- **Interim:** a `delta` appends verbatim to its item's text. If that item is the **oldest open item**, the module emits `SpeechUtterance(transcript=<item text, stripped>, is_final=False, confidence=None)`. A later item's deltas accumulate silently until it becomes the oldest.
- **Final:** a `completed` records the item's transcript. Then, while the oldest item is completed, the module emits its final (`transcript` stripped, `confidence=None`) and drops it. When the next item becomes the oldest and already has text, its interim is emitted at once.
- **Failed:** a `failed` item is completed with the text its deltas accumulated (often none). This keeps a stream of interims from being left without a final.
- **Empty suppression:** an item whose final text is empty emits nothing, matching the other modules.
- **Emission is serialized** behind one lock, so finals always leave in item order.

With turn detection on, the server cuts a continuous stream into items on ≈ 200 ms of silence (its default), so a sentence with a pause may arrive as two finals. The `Segmenter` downstream joins them as it does for every module.

### Latency

On the server-VAD path a final lands ≈ 1.1 s after speech end (measured, `gpt-4o-transcribe`). On the commit path (VAD marker) it lands ≈ 0.6 s after the commit, which itself follows the gate's `min_silence_duration_ms`. A file-based e2e source must set `trailing_silence_s` above both.

**Interims depend on the model.** `gpt-4o-transcribe` and `whisper-1` only send text once the item is committed, so their interims arrive in a burst just before the final. `gpt-realtime-whisper` streams words during speech. The module handles both the same way.

## Configuration fields

| Field | Type | Default | Description |
|---|---|---|---|
| `api_key` / `api_key_env` | string | — | Resolved via `resolve_api_key`. **Required when `url`'s host is `api.openai.com`**, optional elsewhere (no `Authorization` header is sent without one). |
| `url` | string | `"wss://api.openai.com/v1/realtime?intent=transcription"` | The full WebSocket URL, query included, used verbatim. Each server documents its own query (LiteLLM: `?model=<name>&intent=transcription`, Dynamo: none). |
| `model` | string | `"gpt-4o-transcribe"` | Sent as `transcription.model`. Hosted OpenAI also offers `gpt-4o-mini-transcribe`, `whisper-1`, `gpt-realtime-whisper`, … |
| `language` | string \| null | `null` (auto-detect) | ISO 639-1 two-letter code, sent as `transcription.language` |
| `prompt` | string \| null | `null` | Sent as `transcription.prompt`: vocabulary and style hints (e.g. the trigger words). Not every model accepts it. |
| `turn_detection` | bool \| object | `true` | `true` sends `{"type": "server_vad"}` (server defaults). `false` sends `null`: utterances end only on VAD markers or `stop()`, so it needs `engine.vad`. An object is passed through after validation. |
| `noise_reduction` | string \| null | `null` | `"near_field"` or `"far_field"`, sent as `noise_reduction: {type}`. Omitted when null. |
| `stop_timeout_s` | float | `5.0` | How long `stop()` waits for the last items to complete |

**Validation in `__init__`** (per the [constructor contract](../asr-module-interface.md#module-constructor-contract)). Each failure raises `ValueError` naming the field:

- `url` is a string starting with `ws://` or `wss://`;
- the API key resolves, when `api_key` or `api_key_env` is given, and one of them is given when `url`'s host is `api.openai.com`;
- `model` is a non-empty string;
- `language` is null or two ASCII letters;
- `prompt` is null or a string;
- `turn_detection` is a bool, or an object whose `type` is `"server_vad"` or `"semantic_vad"` (other keys pass through; the server validates them and a rejection fails the handshake with its message);
- `noise_reduction` is null, `"near_field"` or `"far_field"`;
- `stop_timeout_s > 0`.

### Examples

Hosted OpenAI:

```json
{
  "type": "openai_realtime_compatible",
  "api_key_env": "OPENAI_API_KEY",
  "model": "gpt-4o-transcribe",
  "language": "en",
  "prompt": "validate"
}
```

A streaming model without server turn detection (needs `engine.vad`):

```json
{
  "type": "openai_realtime_compatible",
  "api_key_env": "OPENAI_API_KEY",
  "model": "gpt-realtime-whisper",
  "turn_detection": false
}
```

A self-hosted server:

```json
{
  "type": "openai_realtime_compatible",
  "url": "ws://gpu-host:8000/v1/realtime",
  "model": "Qwen/Qwen3-ASR-0.6B",
  "turn_detection": false
}
```

## Audio format

```python
SUPPORTED_SAMPLE_RATES = frozenset(
    {24000}
)  # the only rate hosted OpenAI accepts (measured)
SUPPORTED_CHANNELS = frozenset({1})
SUPPORTED_ENCODINGS = frozenset({"linear16"})
DEFAULT_SAMPLE_RATE, DEFAULT_CHANNELS, DEFAULT_ENCODING = 24000, 1, "linear16"
```

The module sends `format: {"type": "audio/pcm", "rate": 24000}`. Our `linear16` is s16 little-endian, which is what `audio/pcm` means, so chunks pass through unchanged apart from base64. The microphone captures at 24 kHz natively. A file source at another rate is rejected by the engine's reconciliation, or falls back as configured.

## Lifecycle

- **`start()`** connects, waits for `session.created`, sends `session.update`, waits for `session.updated`, calls `on_connected(True)`, then runs two tasks until the session ends: a **sender** draining `audio_queue` into `input_audio_buffer.append` (and calling `finalize()` on an `END_OF_SPEECH` marker), and a **receiver** applying the [utterance semantics](#utterance-semantics). With `turn_detection: false` it logs once at info level that finals need `engine.vad` or `stop()`.
- **`stop()`** asks the running session to end and **returns only once it has**, so the last finals are delivered before the engine cancels the module task (as in `mistral_realtime`). Ending the session first applies `finalize()`'s commit-or-clear decision ([VAD gating](#vad-gating)), so a trailing utterance is committed and trailing silence is not. Then it waits up to `stop_timeout_s` for every open item to complete. Then it emits each remaining item's accumulated text as a final, in order, and closes the socket. `on_connected(False)` follows. A `stop()` while reconnecting returns at once.
- **Reconnection** follows the [reconnection contract](../asr-module-interface.md#reconnection-contract) (1, 2, 4, 8 s backoff, draining `audio_queue` meanwhile). On connection loss, every open item's accumulated text is emitted as a final first, and `on_connected(False)` is called. A reconnect is a **new session**: the buffer and open items of the old one are gone. A rejected handshake logs at error level with the cause: HTTP 401 → "check `api_key` / `api_key_env`", HTTP 403/404 → "check `url` and `model`", a `session.update` `error` → the server's message.

## VAD gating

`VAD_GATING = "supported"` ([vad.md](../vad.md)).

**Why gate.** Hosted OpenAI bills audio by duration, silence included, so without a gate the bill follows the engine's running time, as with Deepgram and Voxtral. A gate cuts it to speech time and keeps room noise on the machine. For models without server turn detection (`gpt-realtime-whisper`, and servers like Dynamo) the gate is what ends each utterance, so `turn_detection: false` needs it.

**`finalize()`** reacts to the `END_OF_SPEECH` marker. The protocol has the exact operation the gate needs: `input_audio_buffer.commit` turns the buffered audio into an item, and the server then transcribes it and sends `completed`. No silence burst or local timer is needed. Two cases need care, because the server rejects a commit on less than 100 ms of audio:

1. **Server turn detection on (the default).** The gate forwards its hangover (`min_silence_duration_ms`, 500 ms by default), and the server, hearing more than its own 200 ms of silence, usually commits before the marker arrives. The module tracks the server's turn: open on `speech_started`, closed on `committed`. If the turn is open at the marker, it **commits** (the gate ended the utterance first). If it is closed, the speech is already committed and only trailing silence is buffered, so it **clears** the buffer. Committing that silence would cost a transcription request and risk a hallucinated final.
2. **Server turn detection off.** The module counts the bytes appended since its last commit or clear. At ≥ 100 ms it **commits**, below that it **clears**.

Both counters reset when the session (re)starts. Should the two sides still race (the server commits between the module's decision and its commit), the server answers `input_audio_buffer_commit_empty`, which is logged at debug level and is otherwise harmless.

A marker read while reconnecting is dropped, as in every module.

## Installation

```toml
[project.optional-dependencies]
openai-realtime-compatible = ["websockets>=14"]
all = ["asr-engine[deepgram,mcp,mistral,mlx-audio,openai-realtime-compatible,silero]"]  # Kyutai: own env, see project.md

[dependency-groups]
dev = ["asr-engine[deepgram,mcp,mistral,openai-realtime-compatible,silero]", "mlx-audio[stt]>=0.6; …", ...]  # light: joins dev
```

The module file imports `websockets` at top level, so without the extra the registry's `ImportError` names `openai-realtime-compatible`.

## Testing

### Fast tier: `tests/modules/test_openai_realtime_compatible.py`

The module talks to a **scripted fake connection** injected through a keyword-only `connect` constructor argument, with the same `(url, headers) → async context manager → connection` shape as `mistral_realtime`. No network.

- **Handshake:** the first client message is `session.update` with the GA transcription shape, the reconciled rate, `model`, and `language`/`prompt`/`noise_reduction` when set, and `turn_detection` per config (`{"type": "server_vad"}` / `null` / passed through). The URL is used verbatim. The header carries the key, and is absent without one. `on_connected(True)` waits for `session.updated`, and an `error` instead retries the handshake.
- **Audio:** queue chunks arrive as base64 that decodes back to the exact bytes, in order.
- **Interims and finals:** a delta sequence yields the exact cumulative interims, and `completed` the final with its own transcript.
- **Ordering:** when a later item completes first, its final waits for the earlier item's. A later item's deltas surface as interims only once it is the oldest.
- **Failed item:** closes with its accumulated text. Empty completed text emits nothing.
- **VAD gating:** with turn detection off, a marker after ≥ 100 ms commits, and after less it clears. With turn detection on, a marker while the server's turn is open commits, and after the server's `committed` it clears. A marker while reconnecting is dropped.
- **Stop:** commits uncommitted audio, waits for open items to complete, flushes the incomplete ones as finals after `stop_timeout_s`, and ends with `on_connected(False)`.
- **Reconnect:** a dropped socket flushes open items as finals, calls `on_connected(False)`, reconnects and re-sends `session.update`.
- **Validation:** one case per invalid-config rule above.

### Live tier: `tests-e2e/test_engine_modules.py`

A `MODULES` row: type `openai_realtime_compatible`, `api_key_env: OPENAI_API_KEY`, the 24 kHz WAV `ModuleAudio` (as Kyutai), and a `silence_s` of 3.0, above the measured ≈ 1.1 s server-VAD path. The VAD case runs on the same row through `MODULES_VAD`. [ci.md](../ci.md) gains the `OPENAI_API_KEY` secret and an expected-skips row. Verify with `zsh -ic 'uv run pytest tests-e2e -k openai_realtime_compatible'`.

## Files

- `src/asr_engine/modules/openai_realtime_compatible/__init__.py`: imports nothing (keeps the registry lazy)
- `src/asr_engine/modules/openai_realtime_compatible/module.py`: `OpenAIRealtimeCompatibleModule`
- `src/asr_engine/modules/__init__.py`: `"openai_realtime_compatible": LazyModule("asr_engine.modules.openai_realtime_compatible.module:OpenAIRealtimeCompatibleModule", extra="openai-realtime-compatible")`
- `pyproject.toml`: the extra, `all`, `dev`
- `scripts/probe_openai_realtime.py`: the live probe behind [Measured](#measured-live-probe-hosted-openai-2026-11-13)
- `examples/config/openai_realtime_compatible.json`: a complete server config, with the VAD gate on
- `tests/modules/test_openai_realtime_compatible.py`, `tests-e2e/helpers.py` (`MODULES` row), `.github/workflows/ci.yml` (secret mapping)
- Spec and doc touch-ups: [asr-module-interface.md](../asr-module-interface.md) (registry snippet, extras table and naming rule), [configuration.md](../configuration.md), [ci.md](../ci.md), [e2e-testing.md](../e2e-testing.md), [vad.md](../vad.md) (module tables), [project.md](../project.md) (package and extras lists), `README.md`, `AGENTS.md`

## Open questions

None load-bearing. Deferrals:

- **Resampling.** The 24 kHz-only contract excludes 16 kHz and 44.1 kHz file sources. Forward resampling in the engine (`soxr` is already in the `silero` extra) would lift it for every module at once.
- **Compatible servers' rates.** LiteLLM accepts 8–48 kHz. A config field widening `SUPPORTED_SAMPLE_RATES` per instance needs the capability check to move from class to instance. Not worth it until a user needs it.
- **`vllm_realtime`.** vLLM's `/v1/realtime` speaks its own dialect (`session.update {model}`, `commit {final}`, `transcription.delta`/`done`), so it is a separate module. When it is built, the WebSocket driver (session loop, reconnection, queue helpers) shared with this module and `mistral_realtime` should move to a common internal file.
- **`semantic_vad`, `include: logprobs`, diarization (`gpt-4o-transcribe-diarize`).** Accepted or passable, but nothing downstream consumes confidence or speakers yet.
- **Live probes against LiteLLM, Dynamo and LocalAI.** The documented shapes match, but only hosted OpenAI was probed.
