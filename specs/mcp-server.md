---
code:
  - src/asr_mcp/server.py
  - src/asr_mcp/cli.py
tests:
  - tests/test_server.py
  - tests/test_cli.py
---

# MCP Server Specification

**Status:** Implemented

## Transport

- Protocol: **StreamableHTTP**
- Default endpoint: `http://<host>:<port>/mcp`
- Host and port are read from the config file

## Resources

### `asr://result`

The single rolling resource holding the latest ASR utterance.

- **URI:** `asr://result`
- **MIME type:** `application/json`
- **Updated:** every time the ASR module emits an interim or final result
- **Subscriptions:** clients may subscribe to receive push notifications on each update

#### Resource payload schema

```json
{
  "type": "object",
  "properties": {
    "transcript": {
      "type": "string",
      "description": "The transcribed text of the current utterance"
    },
    "is_final": {
      "type": "boolean",
      "description": "true = final result for this utterance, false = interim/partial"
    },
    "confidence": {
      "type": "number",
      "description": "Confidence score between 0 and 1. null if not provided by the backend."
    },
    "timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 UTC timestamp of when the result was emitted by the server"
    }
  },
  "required": ["transcript", "is_final", "timestamp"]
}
```

#### Example payloads

Interim result:
```json
{
  "transcript": "hello how are",
  "is_final": false,
  "confidence": null,
  "timestamp": "2026-03-17T10:23:45.123Z"
}
```

Final result:
```json
{
  "transcript": "Hello, how are you?",
  "is_final": true,
  "confidence": 0.98,
  "timestamp": "2026-03-17T10:23:47.456Z"
}
```

## Tools

### `start`

Starts audio capture and ASR streaming.

- **Input:** none
- **Output:** `{ "status": "running" }`

### `stop`

Stops audio capture and ASR streaming.

- **Input:** none
- **Output:** `{ "status": "stopped" }`

### `is_running`

Returns the current operational state of the ASR engine.

- **Input:** none
- **Output:**
```json
{
  "running": true,
  "connected": true
}
```

| Field | Type | Description |
|---|---|---|
| `running` | boolean | Whether the ASR engine has been started |
| `connected` | boolean | Whether the ASR backend WebSocket is currently connected |

### `listen`

Provides a blocking, single-shot speech capture session. Designed for use when
`engine.auto_start` is `false`. Manages the full engine lifecycle internally:
start → collect → stop → return.

- **Input:** none
- **Output:**
```json
{
  "transcript": "hello how are you",
  "end_reason": "trigger_word"
}
```

| Field | Type | Description |
|---|---|---|
| `transcript` | string | Accumulated text of all committed finals (space-joined). Empty string if no speech detected. |
| `end_reason` | string | `"trigger_word"`, `"end_of_speech_timeout"`, or `"initial_silence_timeout"` |

**Error responses:**
- If the ASR engine is already running (either because `auto_start` is `true` or
  because `start` was called manually): tool error `"ASR is already running. Stop it before calling listen."`
- If a `listen` call is already in progress: tool error `"A listen session is already in progress."`

#### Engine lifecycle within `listen`

```
listen called
  └─ error if engine.is_running
  └─ engine.start()
  └─ collect until end condition
  └─ engine.stop()   ← always, even on error (try/finally)
  └─ return {transcript, end_reason}
```

#### End conditions

Controlled by the `listen` config block (see [Configuration](configuration.md)).

**Mode: `trigger_word`**

The session ends when any final ASR result contains one of the configured trigger
words (case-insensitive substring match, same logic as `asr-to-terminal` submit
word detection).

- The utterance containing the trigger word is **not** included in the transcript
  (it fires the action, not the text — same behaviour as `asr-to-terminal`).
- No timeouts apply in this mode. The session waits indefinitely.
- `end_reason` = `"trigger_word"`.

**Mode: `timeout`**

Two independent timers govern the session:

| Timer | Config field | Default | Reset trigger | Fires when |
|---|---|---|---|---|
| Initial silence | `listen.initial_silence_timeout_s` | `10.0` | Never | No ASR event received since session start |
| End-of-speech | `listen.end_of_speech_timeout_s` | `5.0` | Every interim or final ASR event | No ASR event received for the configured duration |

- The initial-silence timer starts when the session starts.
- The end-of-speech timer starts after the first ASR event and resets on every
  subsequent event (interim or final).
- Whichever fires first ends the session.
- `end_reason` = `"initial_silence_timeout"` or `"end_of_speech_timeout"` accordingly.

#### Transcript accumulation

| Event | Action |
|---|---|
| Interim result | Track as in-progress; do not commit yet. |
| Final result — no trigger word | Append to the accumulated finals list. |
| Final result — trigger word detected | Do not append; end the session. |

The returned `transcript` is `" ".join(committed_finals)`.

#### Streaming

The `listen` tool emits `notifications/progress` messages as each final ASR
result is committed to the transcript. This allows callers to display
incremental output without waiting for the full session to complete.

- **One notification per committed final** — interim results and the
  trigger-word utterance (which ends the session) do not produce notifications.
- **`progress` field** — the count of committed finals so far (monotonically
  increasing; useful for clients that display a progress bar).
- **`total` field** — always `None` (utterance length is unknown in advance).
- **`message` field** — the full accumulated transcript up to and including
  the latest committed final (space-joined), not just the new fragment.
- **Opt-in** — clients that omit `progressToken` receive no notifications;
  the final return value of `listen` is unchanged.

SDK callback signature for `McpToolClient.call_tool`:

```python
async def on_progress(
    progress: float, total: float | None, message: str | None
) -> None: ...


result = await client.call_tool("listen", progress_callback=on_progress)
```

#### Internal implementation: `EndOfUtteranceDetector`

The session logic lives in `EndOfUtteranceDetector`, a shared class also used
by `AsrToTerminal`. See [end-of-utterance-detector.md](end-of-utterance-detector.md)
for the full specification.

```python
class EndOfUtteranceDetector:
    def __init__(
        self,
        mode: str,  # "trigger_word" or "timeout"
        trigger_words: list[str],
        initial_silence_timeout_s: float,  # timeout mode only
        end_of_speech_timeout_s: float,  # timeout mode only
        on_final_committed: ...,  # optional streaming callback
    ) -> None: ...

    async def on_result(self, result: ASRResult) -> None:
        """Feed an ASR result into the session."""

    async def wait(self) -> UtteranceResult:
        """Block until the session ends and return the result."""
```

`UtteranceResult` is a dataclass with fields `transcript: str` and `end_reason: str`.

#### Shared trigger word detection

Both `listen` and `AsrToTerminal` detect trigger/submit words using the same
function, extracted to `src/asr_mcp/speech_utils.py`:

```python
def contains_trigger_word(transcript: str, words: list[str]) -> bool:
    """Case-insensitive substring match of any word against transcript."""
```

`AsrToTerminal._contains_submit_word` is replaced by a call to this function.

## Lifecycle

### With `engine.auto_start = true` (default)

1. Server starts and reads config
2. Audio capture initializes and starts the input stream
3. ASR module connects to the backend
4. MCP HTTP server starts accepting connections
5. On shutdown (SIGINT / SIGTERM): audio capture stops, ASR connection closes cleanly, HTTP server shuts down

### With `engine.auto_start = false`

1. Server starts and reads config
2. ASR engine is **initialized** (config validated, audio device verified, ASR module instantiated) but **not started**
3. MCP HTTP server starts accepting connections
4. ASR starts only when a client calls `start` or `listen`
5. On shutdown: if engine is running, it is stopped cleanly before the HTTP server shuts down
