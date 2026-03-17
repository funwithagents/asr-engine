# Configuration

## Config File

The server reads a JSON config file at startup, passed as a CLI argument:

```bash
uv run asr-mcp-server --config config.json
```

## Schema

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "audio": {
    "device": null
  },
  "asr": {
    "type": "<module_type>",
    ...module-specific fields...
  }
}
```

### `server` block

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `"127.0.0.1"` | Host to bind the HTTP server to |
| `port` | integer | `8080` | Port to bind the HTTP server to |

### `audio` block

| Field | Type | Default | Description |
|---|---|---|---|
| `device` | string or null | `null` | Audio input device name or index. `null` = system default |

### `asr` block

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | Identifies which ASR module to load (e.g. `"deepgram"`) |
| *(other fields)* | any | depends | Module-specific configuration, parsed by the module |

## Example: Deepgram config

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "audio": {
    "device": null
  },
  "asr": {
    "type": "deepgram",
    "api_key": "YOUR_DEEPGRAM_API_KEY",
    "language": "en-US",
    "model": "nova-2"
  }
}
```

## Validation

- The server must fail fast at startup with a clear error message if:
  - The config file is missing or not valid JSON
  - Required fields (`asr.type`) are absent
  - The specified `asr.type` is unknown
  - Module-specific required fields (e.g. `api_key`) are missing
  - The specified audio device cannot be found
