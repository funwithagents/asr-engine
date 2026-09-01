# Examples

These examples are runnable consumers of `asr-engine`. They live outside the `asr_engine` package, are not included in the wheel, and are not installed as console scripts.

They demonstrate the two main integration styles:

```text
Direct import:  application ──▶ ASREngine
MCP:            application ──▶ MCP server ──▶ ASREngine
```

| Example | Integration | Purpose |
|---|---|---|
| [Gradio demo](gradio_demo/) | Direct import | Control an in-process engine and inspect live utterances and segments in a browser |
| [MCP client](mcp_client/) | MCP resources | Subscribe to a rolling ASR resource and print its updates |
| [ASR to terminal](asr_to_terminal/) | MCP resources | Type the current server-owned segment into the focused terminal |

## Shared setup

From the repository root:

```bash
uv sync
export DEEPGRAM_API_KEY="..."
```

The Gradio example drives an `ASREngine` directly. The two MCP examples require the server to be running in another terminal:

```bash
cp config.example.json config.json
uv run asr-engine-mcp --config config.json
```

Run examples from the repository root with `python -m` so their cross-example imports resolve correctly. See each example's README for its configuration, system requirements, and complete command line.
