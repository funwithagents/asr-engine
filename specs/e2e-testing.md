---
code:
  - src/asr_engine/audio.py
tests:
  - tests-e2e/test_engine_modules.py
  - tests-e2e/test_engine_api.py
  - tests-e2e/test_mcp_resource.py
  - tests-e2e/test_mcp_tools.py
  - tests-e2e/test_asr_to_terminal.py
---

# E2E File-Based Testing

**Status:** Implemented

## Goal

Provide an automated end-to-end test that exercises the full pipeline — from audio source through ASR backend to MCP client — without human interaction or a live microphone. Audio is read from a pre-recorded fixture (WAV or MP3) and fed into the real Deepgram API.

## Scope

The tests cover the complete data path at three boundaries, split by **what can
actually vary when the ASR module changes**:

- **Per module** (`test_engine_modules.py`, parametrized): the live backend contract
  only — connect, emit interim and final `SpeechUtterance` values, finalize on
  silence, remain usable for another utterance, and disconnect cleanly. Adding a
  module means adding one row to `MODULES` in `helpers.py`.
- **Direct engine API, default module** (`test_engine_api.py`): representative
  `listen` and dictation pipelines for programs that import `asr_engine` directly.
- **MCP and consumer APIs, default module** (`test_mcp_resource.py`,
  `test_mcp_tools.py`, `test_asr_to_terminal.py`): resources, tools, server
  lifecycle, and the terminal bridge. These consume engine events independently
  of which module produced them, so repeating them per module only re-tests shared
  code over the network. The default is selected only by
  `helpers.default_module()`.

The `Segmenter` modes and engine/API error paths are exhaustive in the fast tier.
Live e2e keeps representative success paths and does not repeat that deterministic
matrix for each module.

**Test naming:** functions are named for the behavior under test, never the module (the folder already says "e2e"; the module identity shows up only as a parametrize id). So `test_resource_emits_final_transcript`, not `test_e2e_deepgram_v1`.

**Engine-direct** (`test_engine_modules.py`, `test_engine_api.py`) — the
`ASREngine` on its own, with no MCP server:

```
FileAudioSource → asyncio.Queue → ASRModule (Deepgram API)
                                         │
                                    ASREngine (callbacks + public APIs)
                                         │
                                  collected results → assertion
```

The helpers `build_engine(...)` and `build_file_engine(...)` wire an audio source
to an `ASREngine` with sound feedback disabled. `test_engine_modules.py` varies
only the module and observes its utterance/lifecycle contract.
`test_engine_api.py` uses `default_module()` for representative public API flows.

**Through MCP, default module** (`test_mcp_resource.py`, `test_mcp_tools.py`,
`test_asr_to_terminal.py`) — the same pipeline surfaced over the server:

```
FileAudioSource → asyncio.Queue → ASRModule (Deepgram API)
                                         │
                                    ASREngine (utterance/segment callbacks)
                                         │
                                  MCP server (subprocess / in-process)
                                         │
                                  MCP client (in-process)
                                         │
                                  collected results → assertion
```

Unit tests for individual components are out of scope here.

## Audio Source Abstraction

`AudioCapture` (microphone) and the new `FileAudioSource` share the same interface:

| Method | Behaviour |
|--------|-----------|
| `start() -> asyncio.Queue[bytes]` | Open source, return queue |
| `stop() -> None` | Close source |

`ASREngine` accepts an optional `audio_source` at construction time. When provided it is used instead of constructing a source from config.

The **engine** selects the source from config at `start()`: when `audio.audio_file` is set it builds a `FileAudioSource(audio_file, audio_format=<reconciled>, trailing_silence_s=audio.trailing_silence_s)` instead of a live `AudioCapture` (see [configuration.md](configuration.md)); `run_server` just constructs the engine. This is how the subprocess-based e2e tests drive the real `asr-engine-mcp` binary from a WAV fixture without a microphone — `tests-e2e/helpers.start_mcp_server` writes a temp config with those fields.

### `ScriptableAudioSource` — hand-sequenced playback

`FileAudioSource` plays one file, front to back, the moment the engine starts — it fixes the whole audio timeline up front. `ScriptableAudioSource` (in `audio.py`, the third `AudioSource` implementation) instead lets a test **decide at runtime when each utterance is spoken**, which is what a developer building on `import asr_engine` needs to script a multi-utterance scenario:

- Once `start()`ed it feeds **digital silence** into the queue at real-time cadence, so a streaming backend stays connected and finalizes utterances on the gaps between them.
- `await play(path, *, trailing_silence_s=0.0)` feeds that file's audio followed by the trailing silence, then silence resumes. It **resolves once the audio has been fed onto the queue**, not once the backend has transcribed it — the source can't see the ASR's state, so the test then waits on the engine's own utterance/segment output. The trailing silence is what prompts the backend to finalize the just-played utterance. Queued/concurrent `play` calls run in order.
- It decodes each file via `soundfile` (WAV/MP3/…) and validates its rate/channels against the source's `AudioFormat` (files are not resampled), raising `ValueError` on a mismatch and `RuntimeError` if `play` is called before `start`.
- `real_time=False` drops the per-chunk pacing sleep (feeding as fast as the consumer drains) so the source's own mechanics can be unit-tested deterministically and fast, off the network, in `tests/test_audio.py`.

Because playback is driven by method calls rather than config, this source is for
**in-process / direct-engine** tests (via `ASREngine(config, audio_source=...)` or
the `build_engine` helper) — not the config-driven subprocess server path, which
stays with `FileAudioSource`. The per-module `test_engine_streams` scenario uses it
to prove that one live connection can finalize two sequential utterances. The
default-module dictation scenario uses it to arm dictation before speech begins.

## Audio Fixtures

Committed short-utterance files, named `sample_<rate>_<transcript>.<ext>` so the format and expected transcript are recoverable from the name (see [fixtures/README.md](../tests-e2e/fixtures/README.md)). Centralized in `helpers.py` (`FIXTURE_*` paths + their `AudioFormat`), so tests reference them symbolically and each is played at its matching format.

- **Default input:** the **44.1 kHz mono MP3s** (`sample_44100_theskyisblue.mp3` → `"the sky is blue"`, `sample_44100_theskyisbluevalidate.mp3` → adds the trigger word `validate`). Exercises MP3 decoding through the real `asr-engine-mcp` binary and a non-default sample rate end-to-end.
- **Sample-rate-compat input:** the **16 kHz mono WAV**
  (`sample_16000_theskyisblue.wav`), used by the default-module direct `listen`
  scenario to exercise a second input format once through the live pipeline.
- **Playback rate:** real-time (one ~100 ms chunk every ~100 ms) so the streaming API receives audio at a natural cadence.

Fixtures are not generated by the test suite; they are produced once with an online TTS tool and committed. Because file sources are validated (not resampled), each is played through an engine whose `engine.audio` format matches the file — set **explicitly** by the test helpers, never relying on defaults.

## Test Cases

### Per module — `test_engine_modules.py`

Parametrized over the `MODULES` table in `helpers.py`. Each row carries the module type, model, a per-module `silence_s` (the gap the backend needs to finalize an utterance — for Flux this must exceed its `eot_timeout_ms`, default 2000 ms), and `api_key_env`:

| Module | Model | `silence_s` |
|--------|-------|-------------|
| `deepgram_v1` | `nova-3` | `3.0` |
| `deepgram_v2` | `flux-general-en` | `3.0` |

Each row runs one `test_engine_streams` scenario at 44.1 kHz from MP3:

1. Start and wait for `connected=true`.
2. Play the first fixture followed by `silence_s`; require an interim before a
   matching final utterance.
3. Confirm the engine remains running and connected.
4. Play the second fixture on the same stream; require a later final containing
   its distinctive `validate` token.
5. Stop and require both `running=false` and `connected=false`.

It does not assert `SpeechSegment`, `listen`, dictation, or MCP behavior: those are
owned by shared engine/API layers.

### Default module — direct engine API

Built from `helpers.default_module()` (`deepgram_v1`/`nova-3`):

- `test_listen_pipeline` — timeout-mode `listen` over the 16 kHz WAV; asserts the
  resolved format, final segment/reason, a recognizable phrase, and stopped state.
- `test_dictation_pipeline` — persistent trigger-word dictation over a
  `ScriptableAudioSource`; asserts a trigger-word segment, trigger exclusion,
  explicit stop/revert, and that the underlying engine remains connected until
  it is stopped.

### Default module — MCP and consumer APIs

Also built from `helpers.default_module()`; each scenario runs once because it
exercises shared adapters or consumers:

| Test file | Covers | Port(s) |
|-----------|--------|---------|
| `test_mcp_resource.py` | `asr://utterance` yields a final transcript; `asr://segment` aggregates under `auto_start_dictation` | `18001`–`18002` |
| `test_mcp_tools.py` | `listen` tool (trigger_word, timeout, streaming progress) and dictation control | `18003`–`18006` |
| `test_asr_to_terminal.py` | asr-to-terminal bridge: typing, submit, timeout | `18101`–`18103` |

Deterministic tool guard errors stay in `tests/test_engine.py`,
`tests/test_tools.py`, and `tests/test_server.py`; they do not consume live audio
and therefore do not belong in the live tier.

## Assertion Strategy

1. A scenario expecting completion must receive at least one final result.
2. Transcripts are normalized with `helpers.normalize_transcript()`:
   - converted to lowercase
   - all characters that are not `[a-z0-9 ]` are removed
   - leading/trailing whitespace stripped
3. Assertions use robust properties: a non-empty transcript, containment of the
   fixture's stable phrase or distinctive token, or exclusion of a trigger word.

Exact transcript equality is avoided because live service segmentation and output
can vary between runs and models.

## Infrastructure

- **Engine-direct tests** (`test_engine_modules.py`, `test_engine_api.py`): no
  server. `helpers.build_engine` / `build_file_engine` construct an `ASREngine`
  in-process around a `ScriptableAudioSource` or `FileAudioSource`.
- **Condition polling:** `helpers.wait_until()` provides the shared monotonic,
  asynchronous polling loop used by live scenarios waiting for callbacks or state
  transitions.
- **Through-MCP tests** (`test_mcp_resource.py`, `test_mcp_tools.py`, `test_asr_to_terminal.py`): `helpers.start_mcp_server` spawns a real `asr-engine-mcp` **subprocess** (`uv run asr-engine-mcp --config <temp>`) on a dedicated port, writing a temp JSON config with `audio.audio_file`, the `module` block, and any `engine` overrides. It waits until the TCP port accepts connections (`_wait_for_port`); `stop_mcp_server` terminates the process and removes the temp config.
- **Clients:** `AsrResourceClient` (subscribes to `asr://utterance`) for the resource path, `McpToolClient` (single tool call, optional progress callback) for the `listen` tool, and `AsrToTerminal` with an injected in-memory `RecordingTyper` for the terminal bridge.
- **Timeout:** 30 seconds per test (covers real-time audio playback + API round-trip).
- **API key:** tests never read the literal key. Each module config carries
  `api_key_env` — the name of the env var it authenticates with — and the module's
  own `resolve_api_key` reads it. `default_module()` and the `MODULES` rows set that
  name; `require_api_key()` skips when it is unset. A keyless module is never
  skipped. See AGENTS.md "Live/e2e tests".

## Non-Goals

- Mocking the Deepgram API — the test must hit the real backend.
- Testing lifecycle races or backend reconnection logic.
- Running in CI without a real API key.
