"""The documentation's runnable examples, executed as written (specs/project.md,
"Documentation checks").

Each Python block a user is likely to copy is extracted from its Markdown file
and run with the `fake` module standing in for the provider it names and a fast
silence source standing in for the microphone, so the imports, names and calls
it shows cannot drift from the library.
"""

import ast
import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest

import asr_engine
from asr_engine import ASREngine, ASREngineConfig, ScriptableAudioSource
from asr_engine.modules import REGISTRY
from asr_engine.modules.fake.module import FakeASRModule

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _REPO_ROOT / "docs"
_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")

# What the scripted speaker says in every example: a sentence, then the trigger word.
_SCRIPT = [
    {"text": "turn on the lights", "start_s": 0.2, "end_s": 1.0},
    {"text": "send", "start_s": 1.2, "end_s": 1.4},
]
_DEEPGRAM_BLOCK = {
    "audio_source": {"type": "microphone"},
    "sound_feedback": {"enabled": False},
    "segmentation": {"trigger_words": ["send"]},
    "module": {"type": "deepgram-v1", "api_key_env": "DEEPGRAM_API_KEY"},
}


def _python_block(path: Path, heading: str) -> str:
    """The first fenced `python` block of `path` under the Markdown heading
    `heading` (headings inside code blocks are not headings)."""
    in_section = in_fence = in_python = False
    level = 0
    block: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            if in_python:
                return "\n".join(block)
            in_fence = not in_fence
            in_python = in_fence and in_section and line.strip() == "```python"
            continue
        if in_python:
            block.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match:
            if match.group(2) == heading:
                in_section, level = True, len(match.group(1))
            elif in_section and len(match.group(1)) <= level:
                break
    raise AssertionError(f"no python block under {heading!r} in {path.name}")


def _run(source: str, namespace: dict[str, Any]) -> dict[str, Any]:
    """Execute a block, awaiting it when it uses top-level `await` as the
    docs' snippets do (they run inside the reader's async code)."""
    code = compile(source, "<docs>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    if code.co_flags & inspect.CO_COROUTINE:
        asyncio.run(eval(code, namespace))
    else:
        exec(code, namespace)
    return namespace


class _ScriptedDeepgram(FakeASRModule):
    """Stands in for `deepgram-v1`: takes its config, replays `_SCRIPT`."""

    def __init__(self, config: dict) -> None:
        super().__init__(config={"utterances": _SCRIPT})


class _FastSilence(ScriptableAudioSource):
    """`ScriptableAudioSource()` as the examples write it, without the real-time wait."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["real_time"] = False
        super().__init__(*args, **kwargs)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The examples name deepgram-v1: the scripted module answers for it, and no
    # example waits in real time. The files they load hold the same engine block.
    monkeypatch.setitem(REGISTRY, "deepgram-v1", _ScriptedDeepgram)
    monkeypatch.setattr(asr_engine, "ScriptableAudioSource", _FastSilence)
    (tmp_path / "asr.json").write_text(json.dumps(_DEEPGRAM_BLOCK), encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"server": {"port": 8000}, "engine": _DEEPGRAM_BLOCK}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)


def _config(source_type: str = "microphone") -> ASREngineConfig:
    """The examples' config: on the microphone as they write it, or on a custom
    source for the examples (and checks) that pass one."""
    return ASREngineConfig.from_dict(
        {**_DEEPGRAM_BLOCK, "audio_source": {"type": source_type}}
    )


def _engine(**callbacks: Any) -> ASREngine:
    """An engine as the examples expect to find one: built, not started."""
    return ASREngine(_config("custom"), audio_source=_FastSilence(), **callbacks)


def test_readme_quick_start(capsys: pytest.CaptureFixture[str]):
    _run(_python_block(_REPO_ROOT / "README.md", "Quick start"), {})
    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    assert lines == [
        "hello",
        "hello from",
        "hello from the",
        "hello from the engine (final)",
    ]


def test_readme_real_speech_variant():
    async def on_segment(segment: object) -> None: ...

    source = _python_block(_REPO_ROOT / "README.md", "With real speech")
    namespace = _run(
        source,
        {
            "ASREngine": ASREngine,
            "ASREngineConfig": ASREngineConfig,
            "on_segment": on_segment,
        },
    )
    assert namespace["config"].module.type == "deepgram-v1"
    assert namespace["engine"].on_speech_segment is on_segment


def test_configuration_from_an_application_config():
    source = _python_block(_DOCS / "configuration.md", "Creating the configuration")
    namespace = _run(source, {"application_config": {"asr": _DEEPGRAM_BLOCK}})
    assert namespace["config"].segmentation.trigger_words == ["send"]
    assert namespace["engine"].audio_format.sample_rate == 16000


def test_configuration_loaders_agree():
    source = _python_block(_DOCS / "configuration.md", "Loading from other sources")
    # The block rebinds `config` three times; keep each value to compare them.
    source = re.sub(r"^config = ", "configs += ", source, flags=re.MULTILINE)
    source = re.sub(r"^(configs \+= )(.+)$", r"\1[\2]", source, flags=re.MULTILINE)
    namespace = _run(
        source,
        {
            "engine_data": _DEEPGRAM_BLOCK,
            "engine_json": json.dumps(_DEEPGRAM_BLOCK),
            "configs": [],
        },
    )
    assert len(namespace["configs"]) == 3
    assert all(config == _config() for config in namespace["configs"])


def test_configuration_dataclasses():
    source = _python_block(_DOCS / "configuration.md", "Building it in Python")
    config = _run(source, {})["config"]
    assert config.module.type == "deepgram-v1"
    assert ASREngine(config).audio_format.sample_rate == 16000


def test_callbacks_receive_utterances_and_segments(capsys: pytest.CaptureFixture[str]):
    source = _python_block(_DOCS / "running-the-engine.md", "Utterances and segments")
    namespace = _run(source, {"config": _config()})

    async def hear_the_script() -> None:
        # The example's engine reads the microphone; feed the same callbacks
        # from the fast source, until the script's second final has arrived.
        finals = 0
        done = asyncio.Event()

        async def on_segment(segment: Any) -> None:
            nonlocal finals
            await namespace["on_segment"](segment)
            finals += segment.is_final
            if finals == len(_SCRIPT):
                done.set()

        engine = ASREngine(
            _config("custom"),
            on_speech_utterance=namespace["on_utterance"],
            on_speech_segment=on_segment,
            audio_source=_FastSilence(),
        )
        await engine.start()
        await asyncio.wait_for(done.wait(), timeout=5)
        await engine.stop()

    asyncio.run(hear_the_script())
    out = capsys.readouterr().out.splitlines()
    assert "utterance turn on False" in out
    assert "utterance turn on the lights True" in out
    assert "segment turn on the lights True utterance" in out
    assert namespace["engine"].on_speech_segment is namespace["on_segment"]


def test_listen_for_one_segment(capsys: pytest.CaptureFixture[str]):
    source = _python_block(_DOCS / "running-the-engine.md", "Listen for one segment")
    namespace = _run(source, {"engine": _engine()})
    assert namespace["segment"].transcript == "turn on the lights"
    assert capsys.readouterr().out.strip() == "turn on the lights trigger_word"


def test_dictation_on_a_running_engine():
    source = _python_block(
        _DOCS / "running-the-engine.md", "Dictation on a running engine"
    )
    states: list[bool] = []
    engine = _engine()
    original = engine.stop_dictation

    async def stop_dictation() -> None:
        states.append(engine.dictating)
        await original()

    engine.stop_dictation = stop_dictation  # type: ignore[method-assign]
    _run(source, {"engine": engine})
    assert states == [True]  # the session was still open when the example ended it
    assert not engine.dictating
    assert not engine.status()["running"]


def test_custom_audio_source():
    source = _python_block(_DOCS / "audio-input.md", "Injecting your own source")
    heard: list[str] = []
    namespace = _run(source, {"config": _config("custom")})
    engine, pushed = namespace["engine"], namespace["source"]

    async def on_segment(segment: Any) -> None:
        if segment.is_final:
            heard.append(segment.transcript)

    async def speak() -> None:
        engine.on_speech_segment = on_segment
        await engine.start()
        second = bytes(engine.audio_format.sample_rate * 2)  # 1 s of linear16 silence
        for _ in range(2):
            pushed.push(second)
        for _ in range(100):
            if len(heard) == 2:
                break
            await asyncio.sleep(0.01)
        await engine.stop()

    asyncio.run(speak())
    assert heard == ["turn on the lights", "send"]


def test_tools_in_process():
    source = _python_block(_DOCS / "agents-and-mcp.md", "Use `AsrTools` in process")
    namespace = _run(source, {"engine": _engine()})
    assert namespace["result"] == {
        "transcript": "turn on the lights",
        "end_reason": "trigger_word",
    }


def test_engine_from_an_mcp_server_config():
    source = _python_block(_DOCS / "agents-and-mcp.md", "Load the server configuration")
    namespace = _run(source, {})
    assert namespace["server_config"].server.port == 8000
    assert namespace["engine"].audio_format.sample_rate == 16000


def test_testing_guide_example():
    source = _python_block(
        _DOCS / "testing-your-application.md", "A deterministic test"
    )
    _run(source, {})["test_the_command_is_heard"]()
