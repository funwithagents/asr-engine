"""`asr_engine.setup`: setting the engine up from an application, as data.

See specs/setup.md.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import platform as platform_module
import subprocess
import sys
import textwrap
import tomllib
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from module_setup.contract import check_contract
from packaging.requirements import Requirement

from asr_engine import setup
from asr_engine.config import ASREngineConfig, ConfigError, MissingSecretError
from asr_engine.engine import ASREngine
from asr_engine.modules import REGISTRY
from asr_engine.modules.base import Declared
from asr_engine.modules.kyutai import download as kyutai_download
from asr_engine.modules.kyutai import module as kyutai_module
from asr_engine.modules.mlx_audio import module as mlx_module
from asr_engine.vad import REGISTRY as VAD_REGISTRY
from asr_engine.vad.silero import module as silero

_ROOT = Path(__file__).resolve().parent.parent
_DECLARED: dict[str, type[Declared]] = {
    **REGISTRY,
    **{f"vad:{name}": cls for name, cls in VAD_REGISTRY.items()},
}


def _examples() -> dict[str, dict[str, Any]]:
    """The engine block of every example config, by file name."""
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))["engine"]
        for path in sorted((_ROOT / "examples" / "configs").glob("*.json"))
    }


# A file source, which no example uses: its `audio_file` has no default, so the
# conformance check needs a config that sets it.
_FILE_SOURCE = {
    "module": {"type": "fake"},
    "audio_source": {"type": "file", "audio_file": "speech.wav"},
}


def _extras() -> dict[str, list[str]]:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["optional-dependencies"]


def _files(config: dict) -> list[dict]:
    """`check_setup`'s file items: what is left once the extras and the keys are."""
    return [
        item
        for item in setup.check_setup(config)
        if not item["id"].startswith(("extra:", "key:"))
    ]


# --- The declarations ---------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_DECLARED))
def test_a_module_declares_its_title_kind_extra_and_documented_fields(
    name: str,
) -> None:
    cls = _DECLARED[name]

    assert cls.title
    assert cls.kind in ("hosted", "local", "fixture")
    assert cls.extra is None or cls.extra in _extras()
    assert all(field.description.endswith(".") for field in cls.fields)
    assert all(node["description"] for node in cls.schema_extras.values())
    names = [field.name for field in cls.fields] + list(cls.schema_extras)
    assert len(set(names)) == len(names)


def test_every_extra_a_module_needs_is_declared_and_only_fixtures_need_none() -> None:
    for name, cls in _DECLARED.items():
        extras = cls.required_extras({})
        assert (extras == []) == (cls.kind == "fixture"), name
        for requirement in extras:
            assert Requirement(requirement).name == "asr-engine"
            assert Requirement(requirement).extras <= set(_extras()), name


def test_config_schema_is_stable_and_offers_one_branch_per_registered_module() -> None:
    description = setup.config_schema()

    assert description == setup.config_schema()
    assert (description["contract"], description["distribution"]) == (1, "asr-engine")
    properties = description["schema"]["properties"]
    branches = properties["module"]["oneOf"]
    assert [b["properties"]["type"]["const"] for b in branches] == list(REGISTRY)
    nova = branches[list(REGISTRY).index("deepgram-v1")]
    assert nova["title"] == "Deepgram Nova (hosted)"
    assert nova["properties"]["api_key_env"]["x-secret-env"] is True
    assert nova["properties"]["api_key_env"]["default"] == "DEEPGRAM_API_KEY"
    assert "api_key" not in nova["properties"]
    vads = properties["vad"]["oneOf"]
    assert [b["properties"]["type"]["const"] for b in vads] == list(VAD_REGISTRY)
    device = properties["audio_source"]["oneOf"][0]["properties"]["device"]
    assert device["x-options"] == "input-devices"


def test_the_contract_s_conformance_check_passes_over_the_examples() -> None:
    check_contract(setup, [*_examples().values(), _FILE_SOURCE])


# --- required_extras -------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "extras"),
    [
        ({"module": {"type": "deepgram-v1"}}, ["asr-engine[deepgram]"]),
        (
            {"module": {"type": "mlx-audio"}, "vad": {"type": "silero"}},
            ["asr-engine[mlx-audio]", "asr-engine[silero]"],
        ),
        (
            {"module": {"type": "openai-transcriptions-compatible"}},
            ["asr-engine[openai-compatible]"],
        ),
        (
            {"module": {"type": "kyutai", "backend": "torch"}},
            ["asr-engine[kyutai-torch]"],
        ),
        ({"module": {"type": "kyutai", "backend": "mlx"}}, ["asr-engine[kyutai-mlx]"]),
        ({"module": {"type": "deepgram_v1"}}, ["asr-engine[deepgram]"]),
    ],
)
def test_required_extras_names_the_module_s_extra_then_the_vad_s(
    config: dict, extras: list[str]
) -> None:
    assert setup.required_extras(config) == extras


@pytest.mark.parametrize(
    ("platform", "machine", "extra"),
    [("darwin", "arm64", "kyutai-mlx"), ("linux", "x86_64", "kyutai-torch")],
)
def test_kyutai_s_extra_follows_the_platform_when_its_backend_is_auto(
    platform: str, machine: str, extra: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(platform_module, "machine", lambda: machine)

    assert setup.required_extras({"module": {"type": "kyutai"}}) == [
        f"asr-engine[{extra}]"
    ]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"module": {"type": "fake"}},
        {"module": {"type": "no-such-module"}},
        {"module": "fake"},
        {"module": {"type": 3}},
        {"module": {"type": "fake"}, "vad": {"type": "nope"}},
        [],
    ],
)
def test_required_extras_is_empty_when_nothing_is_needed_or_selected(
    config: Any,
) -> None:
    assert setup.required_extras(config) == []


# --- check_config -----------------------------------------------------------------


def test_every_example_config_is_accepted() -> None:
    refused = {
        name: problems
        for name, block in _examples().items()
        if (problems := setup.check_config(block))
    }

    assert refused == {}


ENGINE_PROBLEMS = [
    ([], ""),
    ({}, "/module/type"),
    ({"module": {}}, "/module/type"),
    ({"module": "fake"}, "/module"),
    ({"module": {"type": "nope"}}, "/module/type"),
    ({"module": {"type": "fake"}, "colour": 1}, ""),
    ({"module": {"type": "fake"}, "auto_start": "yes"}, "/auto_start"),
    (
        {"module": {"type": "fake"}, "auto_start": False, "auto_start_dictation": True},
        "/auto_start_dictation",
    ),
    (
        {"module": {"type": "fake"}, "segmentation": {"trigger_words": "go"}},
        "/segmentation/trigger_words",
    ),
    (
        {"module": {"type": "fake"}, "sound_feedback": {"output_device": 1.5}},
        "/sound_feedback/output_device",
    ),
    (
        {"module": {"type": "fake"}, "audio_source": {"type": "mic"}},
        "/audio_source/type",
    ),
    (
        {"module": {"type": "fake"}, "audio_source": {"type": "file"}},
        "/audio_source/audio_file",
    ),
    (
        {"module": {"type": "fake"}, "audio_source": {"audio_format": {"channels": 0}}},
        "/audio_source/audio_format/channels",
    ),
    (
        {"module": {"type": "fake", "audio_format": {"encoding": "opus"}}},
        "/module/audio_format/encoding",
    ),
    ({"module": {"type": "fake"}, "vad": {}}, "/vad/type"),
    ({"module": {"type": "fake"}, "vad": {"type": "nope"}}, "/vad/type"),
    (
        {"module": {"type": "fake"}, "vad": {"type": "silero", "threshold": 1}},
        "/vad/threshold",
    ),
    ({"module": {"type": "fake"}, "audio": {}}, "/audio"),
]


@pytest.mark.parametrize(("config", "path"), ENGINE_PROBLEMS)
def test_an_engine_level_problem_is_reported_at_its_path(
    config: Any, path: str
) -> None:
    problems = setup.check_config(config)

    assert [problem["path"] for problem in problems] == [path]
    assert problems[0]["message"]


@pytest.mark.parametrize(("config", "path"), ENGINE_PROBLEMS)
def test_the_loader_refuses_what_check_config_reports_with_the_same_message(
    config: Any, path: str
) -> None:
    message = setup.check_config(config)[0]["message"]
    if path in ("/module/type", "/vad/type") and "Unknown" in message:
        # The registries are the engine's to read, at construction.
        with pytest.raises(ConfigError) as error:
            ASREngine(ASREngineConfig.from_dict(config))
    else:
        with pytest.raises(ConfigError) as error:
            ASREngineConfig.from_dict(config)
    assert str(error.value) == message


def test_engine_and_module_problems_are_reported_together() -> None:
    problems = setup.check_config(
        {
            "module": {"type": "deepgram-v2", "eot_threshold": 2, "model": ""},
            "segmentation": {"end_of_speech_timeout_s": 0},
            "vad": {"type": "silero", "model_path": 3},
        }
    )

    assert [problem["path"] for problem in problems] == [
        "/segmentation/end_of_speech_timeout_s",
        "/module/model",
        "/module/eot_threshold",
        "/vad/model_path",
    ]


# What `check_config` refuses, the constructor refuses with the same message: the
# module's own block (the engine strips `type` and `audio_format`).
MODULE_PROBLEMS = [
    ({"type": "fake", "utterances": "hi"}, "/module/utterances"),
    ({"type": "fake", "utterances": [{"text": "a"}]}, "/module"),
    ({"type": "deepgram-v1", "api_key": "sk-1"}, "/module/api_key"),
    ({"type": "deepgram-v1", "punctuate": "yes"}, "/module/punctuate"),
    ({"type": "deepgram-v1", "api_key_env": ""}, "/module/api_key_env"),
    ({"type": "deepgram-v2", "eot_timeout_ms": 0}, "/module/eot_timeout_ms"),
    ({"type": "mistral-realtime", "streaming_delay_ms": 1500}, "/module"),
    ({"type": "mistral-realtime", "context_bias": "go"}, "/module"),
    ({"type": "mistral-realtime", "idle_close_s": 0}, "/module/idle_close_s"),
    ({"type": "openai-realtime-compatible"}, "/module"),
    (
        {
            "type": "openai-realtime-compatible",
            "api_key_env": "OPENAI_API_KEY",
            "url": "http://x",
        },
        "/module",
    ),
    (
        {
            "type": "openai-realtime-compatible",
            "api_key_env": "OPENAI_API_KEY",
            "noise_reduction": "loud",
        },
        "/module/noise_reduction",
    ),
    ({"type": "openai-transcriptions-compatible"}, "/module"),
    (
        {
            "type": "openai-transcriptions-compatible",
            "url": "http://localhost:8000/v1/audio/transcriptions",
            "extra_fields": {"model": "x"},
        },
        "/module",
    ),
    ({"type": "mlx-audio", "interim_interval_s": -1}, "/module/interim_interval_s"),
    ({"type": "kyutai", "backend": "onnx"}, "/module/backend"),
    ({"type": "kyutai", "lag_warn_s": 5, "lag_drop_s": 5}, "/module"),
]


@pytest.mark.parametrize(("module", "path"), MODULE_PROBLEMS)
def test_check_config_and_the_constructor_refuse_the_same_config_with_the_same_message(
    module: dict[str, Any], path: str
) -> None:
    vad = (
        {"type": "silero"}
        if REGISTRY[module["type"]].VAD_GATING == "required"
        else None
    )
    problems = setup.check_config({"module": module, "vad": vad})

    assert [problem["path"] for problem in problems] == [path]
    block = {k: v for k, v in module.items() if k != "type"}
    with pytest.raises(ConfigError) as error:
        REGISTRY[module["type"]](block)
    assert str(error.value) == problems[0]["message"]


def test_hosted_openai_needs_its_key_variable_named_and_a_local_server_does_not() -> (
    None
):
    hosted = setup.check_config({"module": {"type": "openai-realtime-compatible"}})
    local = setup.check_config(
        {
            "module": {
                "type": "openai-realtime-compatible",
                "url": "ws://localhost:8000/v1/realtime",
            }
        }
    )

    assert hosted == [
        {
            "path": "/module",
            "message": "OpenAI Realtime-compatible module: api.openai.com needs a "
            "key: name its variable in 'api_key_env', usually 'OPENAI_API_KEY'",
        }
    ]
    assert local == []


def test_a_module_that_needs_a_vad_and_one_that_cannot_take_it_are_refused() -> None:
    needs = setup.check_config({"module": {"type": "mlx-audio"}})
    assert [p["path"] for p in needs] == ["/vad"]
    assert "needs a VAD" in needs[0]["message"]


def test_an_audio_format_the_module_does_not_take_is_refused() -> None:
    problems = setup.check_config(
        {"module": {"type": "deepgram-v1", "audio_format": {"sample_rate": 22050}}}
    )

    assert [p["path"] for p in problems] == ["/module/audio_format/sample_rate"]
    with pytest.raises(ConfigError) as error:
        ASREngine(
            ASREngineConfig.from_dict(
                {
                    "module": {
                        "type": "deepgram-v1",
                        "audio_format": {"sample_rate": 22050},
                    },
                    "sound_feedback": {"enabled": False},
                }
            )
        )
    assert str(error.value) == problems[0]["message"]


def test_a_renamed_type_is_accepted_though_the_schema_does_not_offer_it() -> None:
    assert setup.check_config({"module": {"type": "deepgram_v1"}}) == []


_NO_PROVIDER_SCRIPT = textwrap.dedent(
    """
    import sys
    from unittest.mock import MagicMock

    sys.modules["sounddevice"] = MagicMock()  # no PortAudio needed
    for name in (
        "deepgram", "websockets", "httpx", "onnxruntime", "mlx_audio", "moshi",
        "moshi_mlx", "huggingface_hub",
    ):
        sys.modules[name] = None

    from asr_engine import setup

    setup.config_schema()
    for config in (
        {"module": {"type": "fake"}},
        {"module": {"type": "deepgram-v2"}, "vad": {"type": "silero"}},
        {"module": {"type": "kyutai"}},
    ):
        assert setup.check_config(config) == [], config
    assert setup.check_config({"module": {"type": "deepgram-v1", "model": ""}})
    assert setup.required_extras({"module": {"type": "deepgram-v1"}}) == [
        "asr-engine[deepgram]"
    ]

    # Without its provider, every module with an extra reports that extra as
    # missing, with the hint its constructor raises; one with a key then its
    # key, read from its default variable, and nothing else: its files are
    # looked up only once the extra imports.
    from asr_engine.modules import REGISTRY
    from asr_engine.vad import REGISTRY as VAD_REGISTRY

    for name, cls in [*REGISTRY.items(), *VAD_REGISTRY.items()]:
        extras = cls.required_extras({})
        if not extras:
            continue
        items = cls.check_setup({})
        assert [i["id"] for i in items[1:]] == [
            f"key:{f.default}" for f in cls.fields if f.secret == "env" and f.default
        ], (name, items)
        assert (items[0]["id"], items[0]["status"]) == (
            f"extra:{extras[0]}", "missing"
        ), (name, items)
        try:
            cls.imports({})
        except ImportError as exc:
            assert items[0]["detail"] == str(exc), name
        assert extras[0] in items[0]["detail"], name
    print("OK")
    """
)


def test_schema_config_and_extras_need_no_provider_library_and_check_setup_says_what_is_missing() -> (
    None
):
    result = subprocess.run(
        [sys.executable, "-c", _NO_PROVIDER_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
        # The environment as is (Windows needs its own variables to start Python),
        # with the keys the autouse fixture set.
        env=dict(os.environ),
    )

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- check_setup and prepare ----------------------------------------------------------


def _installs_here(extra: str) -> bool:
    """Whether the extra brings a package on this platform: one whose packages all
    carry another platform's marker (mlx-audio, Apple Silicon only) installs
    nothing."""
    return any(
        (marker := Requirement(text).marker) is None or marker.evaluate()
        for text in _extras()[extra]
    )


@pytest.mark.parametrize(
    "name",
    sorted(n for n, c in _DECLARED.items() if c.extra is not None),
)
def test_an_extra_is_ready_where_it_installs_and_missing_with_its_hint_elsewhere(
    name: str,
) -> None:
    cls = _DECLARED[name]
    assert cls.extra is not None
    items = cls.check_setup(
        {"api_key_env": "OPENAI_API_KEY"} if cls.takes_secrets() else {}
    )

    if _installs_here(cls.extra):
        status, detail = "ready", "Installed."
    else:
        with pytest.raises(ImportError) as error:
            cls.imports({})
        status, detail = "missing", str(error.value)
        assert f"asr-engine[{cls.extra}]" in detail
    assert items[0] == {
        "id": f"extra:asr-engine[{cls.extra}]",
        "title": f"{cls.title} extra",
        "status": status,
        "detail": detail,
        "preparable": False,
    }


def test_a_key_variable_is_missing_until_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_ASR_KEY", raising=False)
    config = {"module": {"type": "deepgram-v1", "api_key_env": "DEMO_ASR_KEY"}}

    assert setup.check_setup(config)[1] == {
        "id": "key:DEMO_ASR_KEY",
        "title": "DEMO_ASR_KEY",
        "status": "missing",
        "detail": "Set the environment variable DEMO_ASR_KEY (named by 'api_key_env').",
        "preparable": False,
    }

    monkeypatch.setenv("DEMO_ASR_KEY", "")
    assert setup.check_setup(config)[1]["status"] == "missing"

    monkeypatch.setenv("DEMO_ASR_KEY", "secret")
    assert setup.check_setup(config)[1]["status"] == "ready"


def test_every_provider_module_reads_its_key_from_the_provider_s_usual_variable() -> (
    None
):
    defaults = {
        name: cls.key_variable({})
        for name, cls in REGISTRY.items()
        if cls.takes_secrets()
    }

    assert defaults == {
        "deepgram-v1": "DEEPGRAM_API_KEY",
        "deepgram-v2": "DEEPGRAM_API_KEY",
        "mistral-realtime": "MISTRAL_API_KEY",
        # A compatible server often needs no key: no variable unless one is named.
        "openai-realtime-compatible": None,
        "openai-transcriptions-compatible": None,
    }


def test_a_compatible_server_names_no_key_by_default() -> None:
    config = {
        "module": {
            "type": "openai-transcriptions-compatible",
            "url": "http://localhost:8000/v1/audio/transcriptions",
        },
        "vad": {"type": "silero"},
    }

    assert [
        i["id"] for i in setup.check_setup(config) if i["id"].startswith("key:")
    ] == []


def test_a_module_without_files_has_no_item_and_nothing_to_prepare() -> None:
    config = {"module": {"type": "fake"}}

    assert setup.check_setup(config) == []
    setup.prepare(config)


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for the Silero model file, served over ``file://``, and an empty
    cache directory."""
    source = tmp_path / "upstream" / "silero_vad.onnx"
    source.parent.mkdir()
    source.write_bytes(b"model bytes")
    monkeypatch.setattr(silero, "MODEL_URL", source.as_uri())
    monkeypatch.setattr(
        silero, "MODEL_SHA256", hashlib.sha256(b"model bytes").hexdigest()
    )
    monkeypatch.setenv(silero.CACHE_DIR_ENV, str(tmp_path / "cache"))
    return source


@pytest.mark.skipif(not _installs_here("silero"), reason="no onnxruntime here")
def test_the_vad_model_is_missing_until_prepared(upstream: Path) -> None:
    config = {"module": {"type": "fake"}, "vad": {"type": "silero"}}

    before = _files(config)
    assert [(i["id"], i["status"], i["preparable"]) for i in before] == [
        (f"silero:{silero.MODEL_FILENAME}", "missing", True)
    ]

    progress: list[dict[str, Any]] = []
    setup.prepare(config, progress.append)

    assert [p["fraction"] for p in progress] == [None, 1.0]
    assert [i["status"] for i in _files(config)] == ["ready"]
    upstream.unlink()
    setup.prepare(config)  # everything is ready: nothing more is fetched


@pytest.mark.skipif(not _installs_here("silero"), reason="no onnxruntime here")
def test_a_local_vad_model_is_ready_or_missing_and_never_preparable(
    tmp_path: Path, upstream: Path
) -> None:
    local = tmp_path / "silero.onnx"
    config = {
        "module": {"type": "fake"},
        "vad": {"type": "silero", "model_path": str(local)},
    }

    missing = _files(config)[0]
    assert (missing["status"], missing["preparable"]) == ("missing", False)
    assert str(local) in missing["detail"]
    setup.prepare(config)
    assert not (tmp_path / "cache").exists()

    local.write_bytes(b"onnx")
    assert _files(config)[0]["status"] == "ready"


def test_a_local_model_is_missing_until_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache: set[str] = set()
    downloads: list[str] = []
    monkeypatch.setattr(
        kyutai_module.KyutaiModule, "imports", classmethod(lambda cls, c: None)
    )
    monkeypatch.setattr(kyutai_download, "is_cached", lambda repo: repo in cache)

    def fetch(repo: str) -> None:
        downloads.append(repo)
        cache.add(repo)

    monkeypatch.setattr(kyutai_download, "download", fetch)
    config = {"module": {"type": "kyutai", "hf_repo": "kyutai/a-model"}}

    assert [(i["id"], i["status"], i["preparable"]) for i in _files(config)] == [
        ("kyutai:kyutai/a-model", "missing", True)
    ]
    setup.prepare(config)
    setup.prepare(config)

    assert downloads == ["kyutai/a-model"]
    assert [i["status"] for i in _files(config)] == ["ready"]


def test_an_mlx_audio_model_on_disk_is_never_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mlx_module.MlxAudioModule, "imports", classmethod(lambda cls, c: None)
    )
    monkeypatch.setattr(
        mlx_module,
        "download",
        lambda model: pytest.fail("a local model is not fetched"),
    )
    model = tmp_path / "parakeet"
    config = {
        "module": {"type": "mlx-audio", "model": str(model)},
        "vad": {"type": "silero"},
    }

    missing = [i for i in _files(config) if i["id"].startswith("mlx-audio:")][0]
    assert (missing["status"], missing["preparable"]) == ("missing", False)
    setup.prepare(config)

    model.mkdir()
    ready = [i for i in _files(config) if i["id"].startswith("mlx-audio:")][0]
    assert ready["status"] == "ready"


# --- options --------------------------------------------------------------------


def test_input_devices_are_the_machine_s_inputs_after_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from asr_engine.audio import AudioCapture

    monkeypatch.setattr(AudioCapture, "list_devices", staticmethod(lambda: ["USB mic"]))

    assert setup.options("input-devices", {}) == [
        {"label": "System default", "fields": {"device": None}},
        {"label": "USB mic", "fields": {"device": "USB mic"}},
    ]


def test_an_unknown_list_is_empty() -> None:
    assert setup.options("voices", {"module": {"type": "fake"}}) == []


# --- probe ----------------------------------------------------------------------


class _Server:
    """Stands in for `urllib.request.urlopen`: records each request and answers
    with the next scripted body, or raises the next scripted error."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[tuple[str, dict[str, str]]] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        self.requests.append((request.full_url, dict(request.header_items())))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch):
    def install(*answers: Any) -> _Server:
        fake = _Server(*answers)
        monkeypatch.setattr("urllib.request.urlopen", fake)
        return fake

    return install


def test_probe_on_the_fixture_needs_nothing() -> None:
    result = setup.probe({"module": {"type": "fake"}})

    assert result["ok"] is True
    assert result["duration_s"] >= 0


def test_probe_proves_a_deepgram_key_on_one_free_listing(server) -> None:
    fake = server({"projects": [{"project_id": "p"}]})

    result = setup.probe({"module": {"type": "deepgram-v2"}})

    assert result["ok"] is True, result
    assert "accepted the key" in result["message"]
    assert fake.requests == [
        ("https://api.deepgram.com/v1/projects", {"Authorization": "Token k"})
    ]


def test_probe_says_a_key_was_refused_and_raises_nothing(server) -> None:
    server(urllib.error.HTTPError("u", 401, "Unauthorized", {}, None))  # type: ignore[arg-type]

    result = setup.probe({"module": {"type": "deepgram-v1"}})

    assert result == {
        "ok": False,
        "message": "Deepgram refused the key (HTTP 401).",
        "duration_s": result["duration_s"],
    }


def test_probe_finds_the_configured_model_in_the_provider_s_list(server) -> None:
    fake = server(
        {"data": [{"id": "voxtral-mini-transcribe-realtime-3"}]},
        {"data": [{"id": "voxtral-small-latest"}]},
    )
    config = {"module": {"type": "mistral-realtime"}}

    listed = setup.probe(config)
    unlisted = setup.probe(config)

    assert listed["ok"] is True
    assert fake.requests[0] == (
        "https://api.mistral.ai/v1/models",
        {"Authorization": "Bearer k"},
    )
    assert unlisted["ok"] is False
    assert "does not list the model" in unlisted["message"]


def test_probe_lists_a_compatible_server_s_models_without_a_key_unless_named(
    server,
) -> None:
    fake = server(
        {"data": [{"id": "whisper"}]}, {"data": [{"id": "gpt-4o-transcribe"}]}
    )

    local = setup.probe(
        {
            "module": {
                "type": "openai-realtime-compatible",
                "url": "ws://localhost:8000/v1/realtime?intent=transcription",
                "model": "whisper",
            }
        }
    )
    hosted = setup.probe(
        {
            "module": {
                "type": "openai-transcriptions-compatible",
                "api_key_env": "OPENAI_API_KEY",
            },
            "vad": {"type": "silero"},
        }
    )

    assert local["ok"] is True
    assert fake.requests[0] == ("http://localhost:8000/v1/models", {})
    assert fake.requests[1] == (
        "https://api.openai.com/v1/models",
        {"Authorization": "Bearer k"},
    )
    assert hosted["message"].startswith(
        "The server at api.openai.com answered and lists the model 'gpt-4o-transcribe'."
    )


def test_probe_reports_a_config_that_cannot_run_and_raises_nothing() -> None:
    result = setup.probe({"module": {"type": "deepgram-v1", "model": ""}})

    assert result["ok"] is False
    assert (
        result["message"] == "Deepgram Nova module 'model' must be a non-empty string"
    )


def test_probe_uses_the_keys_given_in_code_before_the_environment(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    fake = server({"projects": []})

    result = setup.probe(
        {"module": {"type": "deepgram-v1"}}, secrets={"DEEPGRAM_API_KEY": "code"}
    )

    assert result["ok"] is True
    assert fake.requests[0][1] == {"Authorization": "Token code"}


# --- Keys given in code -------------------------------------------------------------


def test_an_engine_hands_its_secrets_to_a_module_that_reads_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    config = ASREngineConfig.from_dict(
        {"module": {"type": "deepgram-v1"}, "sound_feedback": {"enabled": False}}
    )

    with pytest.raises(MissingSecretError) as error:
        ASREngine(config)
    engine = ASREngine(config, secrets={"DEEPGRAM_API_KEY": "from-code"})

    assert error.value.variable == "DEEPGRAM_API_KEY"
    assert engine._asr_module._api_key == "from-code"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "name", sorted(n for n, c in REGISTRY.items() if c.takes_secrets())
)
def test_a_module_that_reads_a_key_accepts_the_secrets(name: str) -> None:
    assert "secrets" in inspect.signature(REGISTRY[name].__init__).parameters


def test_a_module_that_reads_no_key_keeps_its_one_argument_constructor() -> None:
    config = ASREngineConfig.from_dict(
        {"module": {"type": "fake"}, "sound_feedback": {"enabled": False}}
    )

    ASREngine(config, secrets={"ANY": "x"})  # the fake module is not handed them
