"""asr-to-terminal example: type live transcripts into the active terminal.

Runnable consumer of the ASR MCP server, not part of the ``asr_engine`` package.
Subscribes to ``asr://segment`` (via ``examples.mcp_client``) and injects
keystrokes with ``xdotool``/``ydotool``. See specs/asr-to-terminal.md.
"""
