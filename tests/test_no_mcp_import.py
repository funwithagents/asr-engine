"""Guard: the engine and tools work without the mcp extra installed.

Runs in a subprocess with `mcp` and `uvicorn` made unimportable, so poisoning
`sys.modules` never leaks into the rest of the suite. Proves that
`import asr_engine`, `MCPServerConfig`, and an `AsrTools` start/stop round trip
on the `fake` module never import the MCP stack, and that `asr-engine-mcp`
exits with the `pip install 'asr-engine[mcp]'` hint (see specs/project.md,
"Dependency strategy for transports").
"""

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import sys

    # As in a core install without the mcp extra.
    for name in ("mcp", "uvicorn"):
        sys.modules[name] = None

    from asr_engine import ASREngine, AsrTools, ScriptableAudioSource
    from asr_engine.config import MCPServerConfig

    cfg = MCPServerConfig.from_dict(
        {
            "engine": {
                "module": {"type": "fake", "utterances": []},
                "sound_feedback": {"enabled": False},
            }
        }
    )
    engine = ASREngine(cfg.engine, audio_source=ScriptableAudioSource(real_time=False))
    tools = AsrTools(engine)


    async def round_trip():
        assert await tools.start() == {"status": "running"}
        assert tools.is_running()["running"] is True
        assert await tools.stop() == {"status": "stopped"}
        assert tools.is_running()["running"] is False


    asyncio.run(round_trip())

    from asr_engine.mcp_server_cli import main

    # The config file does not exist: the extra check must run before it is read.
    sys.argv = ["asr-engine-mcp", "--config", "missing.json"]
    try:
        main()
    except SystemExit as exc:
        assert "asr-engine[mcp]" in str(exc.code), exc.code
    else:
        raise AssertionError("main() ran without the mcp extra")

    # Never imported: still the poison sentinels we planted.
    assert sys.modules["mcp"] is None and sys.modules["uvicorn"] is None
    print("OK")
    """
)


def test_engine_and_tools_work_without_mcp_extra():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
