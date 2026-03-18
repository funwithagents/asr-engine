# MCP Server Specification

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

## Lifecycle

1. Server starts and reads config
2. Audio capture initializes and starts the input stream
3. ASR module connects to the backend
4. MCP HTTP server starts accepting connections
5. On shutdown (SIGINT / SIGTERM): audio capture stops, ASR connection closes cleanly, HTTP server shuts down
