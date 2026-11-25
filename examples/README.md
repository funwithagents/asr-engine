# Examples

These examples are runnable consumers of `asr-engine`. They live outside the `asr_engine` package, are not included in the wheel, and are not installed as console scripts.

They demonstrate the two main integration styles:

```text
Direct import:  application ──▶ ASREngine
MCP:            application ──▶ MCP server ──▶ ASREngine
```

| Example | Integration | Purpose |
|---|---|---|
| [Gradio demo](gradio_demo/) | Direct import | Set the engine up in a browser (module, device, extras, models, keys), then listen and inspect live utterances and segments: `uv run python -m examples.gradio_demo` (or `--config FILE` to run a config file as it is) |
| [Config app](config_app/) | Setup panels | Edit one config file on a page (Setup and Secrets, Save to the file), with no session: `uv run python -m examples.config_app FILE` ([the demo's README](gradio_demo/README.md#three-ways-to-launch)) |
| [MCP client](mcp_client/) | MCP resources | Subscribe to a rolling ASR resource and print its updates |
| [ASR to terminal](asr_to_terminal/) | MCP resources | Type the current server-owned segment into the focused terminal |

## Shared setup

From the repository root:

```bash
uv sync   # the default groups already include every provider extra, the mcp extra and Gradio
export DEEPGRAM_API_KEY="..."
```

The Gradio example drives an `ASREngine` directly. The two MCP examples require the server to be running in another terminal:

```bash
cp examples/mcp/config.json config.json   # the fake module; replace its engine block for a real one
uv run asr-engine-mcp --config config.json
```

[`configs/`](configs/) holds one complete engine config per ASR module (`deepgram-v1`, `deepgram-v2`, `mistral-realtime`, `openai-realtime-compatible`, `openai-transcriptions-compatible`, `kyutai`, `mlx-audio`): the engine block alone, which the demo and the config app take as a file, and which replaces the `engine` block of the MCP server's example, [`mcp/config.json`](mcp/config.json). The cloud ones and `mlx-audio` enable the Silero VAD gate.

The library's own documentation starts at [docs/index.md](../docs/index.md); the server side of the two MCP examples is described in [docs/agents-and-mcp.md](../docs/agents-and-mcp.md).

Run examples from the repository root with `python -m` so their cross-example imports resolve correctly. See each example's README for its configuration, system requirements, and complete command line.
