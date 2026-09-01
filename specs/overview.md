---
code:
  - src/asr_engine/mcp_server_cli.py
  - src/asr_engine/engine.py
tests:
---

# Project Overview

**Status:** Implemented

> **Refactor note (2026-08-31):** the project is reframed from "an ASR MCP
> server" to "a reusable ASR **engine** with an MCP server on top". The
> `ASREngine` (constructed from a single `ASREngineConfig`) is the primary
> product and is usable by direct import; a transport-agnostic tools layer and
> the MCP server are built on it. The package is renamed `asr_mcp` →
> `asr_engine`. Implemented by
> [plans/202608311612_asr-engine-refactor.md](../plans/202608311612_asr-engine-refactor.md).
>
> **Server-only-package update (2026-09-01):** the deliverable is the
> engine + tools + MCP server. The demo client and the `asr-to-terminal` bridge
> are demoted from shipped components to `examples/` (see the Components table).
> To be implemented by
> [plans/202609012100_server-only-package.md](../plans/202609012100_server-only-package.md).

## Goal

Provide a real-time Automatic Speech Recognition (ASR) **engine** that:
- Runs continuously, capturing audio and transcribing speech
- Can be used two ways: **directly** (`import asr_engine`, construct an
  `ASREngine`, consume utterances/segments) or **over MCP** (the bundled server)
- Owns segmentation and sound feedback internally, so consumers don't re-implement
  end-of-utterance logic
- Supports swappable ASR backend modules configured via a JSON config file
- Exposes lifecycle, `listen`, and dictation tools as a transport-agnostic
  layer that in-process agents can register directly and the MCP server surfaces
  over StreamableHTTP

## Components

| Component | Description |
|---|---|
| **ASR Engine** | The core: wires audio + module and owns segmentation and sound feedback; constructed from `ASREngineConfig`. Usable standalone. Logging remains the caller's concern. |
| **Tools layer** | Transport-agnostic `AsrTools` lifecycle, `listen`, and dictation operations over an `ASREngine`; registerable directly by an in-process agent or wrapped by the MCP server |
| **MCP Server** | StreamableHTTP server exposing the resources and the tools layer |
| **ASR Module** | Pluggable backend implementing the ASR interface (first: Deepgram) |
| **Audio Capture** | Reads from system audio input (configurable) |

The three components below are **not part of the library** — they live in
`examples/` as runnable consumers (see [project.md](project.md) "Repo shape"),
outside the wheel, and demonstrate the two usage patterns:

| Example | Description |
|---|---|
| **Gradio demo** (`examples/gradio_demo/`) | Direct-import UI driving an in-process `ASREngine` (see [gradio-demo.md](gradio-demo.md)) |
| **Demo client** (`examples/mcp_client/`) | MCP subscription SDK + a CLI that subscribes and logs ASR results (see [demo-client.md](demo-client.md)) |
| **asr-to-terminal** (`examples/asr_to_terminal/`) | Subscribes to `asr://segment` and types transcripts into the active terminal (see [asr-to-terminal.md](asr-to-terminal.md)) |

## Key Constraints

- Written in Python, project managed with `uv`
- Uses the official MCP Python SDK (`modelcontextprotocol/python-sdk`)
- StreamableHTTP transport (enables remote access on local network)
- Only one ASR module active at a time, selected via config
- ASR runs in the background from server start; not triggered by client connections
- Auto-reconnect on ASR backend connection loss

## Non-goals (v1)

- Multiple simultaneous ASR streams
- Speaker diarization
- Transcript persistence across server restarts
- Authentication / TLS
- More than one audio input simultaneously
