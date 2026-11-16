# Agents and the MCP server

The engine is the primary API. The tools layer and the MCP server adapt an engine for agent frameworks and remote clients; they do no recognition or segmentation themselves.

```text
MCP client ──▶ MCP server ──▶ AsrTools ──▶ ASREngine ──▶ ASRModule
                              ▲
                     in-process agent
```

## Use `AsrTools` in process

`AsrTools` wraps an existing engine with agent-friendly arguments, dictionary results and lifecycle checks. A framework that accepts Python callables as tools can register the bound methods directly:

```python
from asr_engine import AsrTools

asr_tools = AsrTools(engine)

# Register the bound methods you need with your agent framework.
listen_tool = asr_tools.listen

result = await listen_tool()
```

`listen` returns `{"transcript": ..., "end_reason": ...}` for one closed segment.

| Tool method | Purpose |
|---|---|
| `start` | Start continuous capture and recognition |
| `stop` | Stop capture and recognition |
| `is_running` | Return the running and connection state |
| `listen` | Capture and return one closed segment |
| `start_dictation` | Begin non-blocking aggregation on a running engine |
| `stop_dictation` | End the dictation without stopping the engine |
| `is_dictation_running` | Return the dictation state and segmentation mode |
| `set_dictation_default_segmentation_mode` | Set the default for future dictations |
| `set_listen_default_segmentation_mode` | Set the default for future listens |

`AsrTools` is part of the base install and needs no MCP package. Its contract is in the [tools specification](../specs/tools.md).

## Run the MCP server

The server exposes the `AsrTools` operations as MCP tools over StreamableHTTP. It needs the `mcp` extra plus the extra of your module; without it, `asr-engine-mcp` exits with the install hint.

Its configuration wraps the same [engine block](configuration.md#the-engine-block) with a `server` block:

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000
  },
  "engine": {
    "auto_start": true,
    "module": {
      "type": "deepgram-v1",
      "api_key_env": "DEEPGRAM_API_KEY",
      "model": "nova-3"
    }
  }
}
```

Start from the example for your module in [examples/config/](../examples/config/):

```bash
uv sync --no-default-groups --extra deepgram --extra silero --extra mcp
cp examples/config/deepgram-v2.json config.json
uv run asr-engine-mcp --config config.json
```

There is one complete config per module. All but `kyutai.json` enable the Silero VAD gate with the setting recommended for that module, hence the `silero` extra above; remove their `vad` block to stream everything (the two batch modules need it, see [vad.md](vad.md)).

The default endpoint is `http://127.0.0.1:8000/mcp`. Log verbosity comes from the command line, not from the configuration file:

```bash
uv run asr-engine-mcp --config config.json --log-level DEBUG
```

At startup the server applies the engine block's hosting policy: `auto_start` starts the engine, and `auto_start_dictation` then enters a persistent dictation.

## MCP resources

Besides the tools, the server publishes two subscribable `application/json` resources:

| Resource | Contents | Updated |
|---|---|---|
| `asr://utterance` | The latest interim or final result from the backend | On every utterance event |
| `asr://segment` | The latest growing or closed engine segment | On every segment change |

These are rolling latest-value snapshots, not transcript histories or event logs. A notification tells a client that a URI changed, and the client then reads its current value. Fast consecutive updates can be coalesced.

## Connect a client

Point a StreamableHTTP-compatible client at the endpoint:

```json
{
  "mcpServers": {
    "asr-engine": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Two examples consume the resources: [mcp_client](../examples/mcp_client/) prints the updates of one resource, and [asr_to_terminal](../examples/asr_to_terminal/) types the current segment into the focused terminal.

## Load the server configuration

Library code can read a complete `{server, engine}` file and reuse its engine block. `MCPServerConfig` does not need the `mcp` extra:

```python
from asr_engine import ASREngine
from asr_engine.config import MCPServerConfig

server_config = MCPServerConfig.from_json_file("config.json")
engine = ASREngine(server_config.engine)
```

The server's lifecycle and the resource semantics are in the [MCP server specification](../specs/mcp-server.md).
