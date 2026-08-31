---
code:
  - src/asr_mcp/cli.py
  - src/asr_mcp/engine.py
tests:
---

# Project Overview

**Status:** Implemented

## Goal

Build a real-time Automatic Speech Recognition (ASR) MCP server that:
- Runs continuously, capturing audio and transcribing speech as long as the server is alive
- Exposes transcription results as a live MCP resource that clients can subscribe to
- Supports swappable ASR backend modules configured via a JSON config file
- Exposes control tools (`start`, `stop`, `is_running`, `listen`) to MCP clients
- Is network-accessible via StreamableHTTP transport

## Components

| Component | Description |
|---|---|
| **MCP Server** | StreamableHTTP server exposing resources and tools |
| **ASR Engine** | Background service running continuously, feeding results to the server |
| **ASR Module** | Pluggable backend implementing the ASR interface (first: Deepgram) |
| **Audio Capture** | Reads from system audio input (configurable) |
| **Demo Client** | Standalone Python script that subscribes and logs ASR results |

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
