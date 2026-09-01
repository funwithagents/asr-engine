# MCP resource client

This example connects to the ASR Engine MCP server, subscribes to `asr://utterance`, and prints interim and final transcription updates. It also contains the reusable subscription helpers used by the `asr_to_terminal` example.

## Run

Start the server from the repository root in one terminal:

```bash
cp config.example.json config.json
export DEEPGRAM_API_KEY="..."
uv run asr-engine-mcp --config config.json
```

Then run the client in another terminal:

```bash
uv run python -m examples.mcp_client.asr_resource_client
```

To connect to another server:

```bash
uv run python -m examples.mcp_client.asr_resource_client \
  --server http://192.168.1.10:8000/mcp
```

The CLI runs until interrupted and formats updates as interim or final results.

## Reusable client classes

- `ResourceSubscriber` implements one MCP resource subscription and reads the resource after update notifications.
- `AsrResourceClient` manages the MCP session and reconnects the subscription. It defaults to `asr://utterance` and accepts another URI, such as `asr://segment`.

They are example code rather than part of the installed `asr_engine` package, but can be used as a starting point for another repository-side consumer.

## Delivery semantics

The server's ASR resources are rolling latest-value snapshots, not queues or transcript histories:

1. The client subscribes to `asr://utterance` or `asr://segment`.
2. The server sends `notifications/resources/updated` when the URI changes.
3. The client reads the resource to obtain its current JSON value.

The notification contains the URI, not the transcription payload. Several fast updates can therefore be coalesced before the read completes. Use the resources for current live state; do not rely on them for lossless event delivery.
