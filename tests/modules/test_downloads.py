"""`asr_engine.modules.downloads`: model downloads with their bytes measured.

A stand-in Hub serves the files: ``hf_hub_download`` writes them into a cache
folder, either through the bar it is given (a ``huggingface_hub`` that takes
``tqdm_class``) or through an ``.incomplete`` file it grows then renames (one that
does not). See specs/setup.md "Progress".
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import huggingface_hub
import pytest

from asr_engine.modules import downloads
from asr_engine.modules.kyutai import download as kyutai_download

_REPO = "kyutai/a-model"
_FILES = {
    "config.json": json.dumps(
        {"mimi_name": "mimi.safetensors", "tokenizer_name": "tok.model"}
    ).encode(),
    "mimi.safetensors": b"m" * 1_200_000,
    "model.safetensors": b"w" * 3_000_000,
    "tok.model": b"t" * 300_000,
}
_WEIGHTS = ["mimi.safetensors", "model.safetensors", "tok.model"]
_CHUNK = 100_000


class _Hub:
    """The Hub and its cache, for one repository."""

    def __init__(self, root: Path, *, with_bar: bool, fail_on: str | None = None):
        self.blobs = root / f"models--{_REPO.replace('/', '--')}" / "blobs"
        self.blobs.mkdir(parents=True)
        self.cached: dict[str, Path] = {}
        self.fetched: list[str] = []
        self.fail_on = fail_on
        self.with_bar = with_bar

    def try_to_load_from_cache(self, repo: str, name: str, **kw: Any) -> Any:
        path = self.cached.get(name)
        return str(path) if path is not None else None

    def get_paths_info(self, repo: str, names: list[str]) -> list[Any]:
        return [
            type("RepoFile", (), {"path": n, "size": len(_FILES[n])})() for n in names
        ]

    def download(self, repo: str, name: str, **kw: Any) -> str:
        if name == self.fail_on:
            raise OSError(f"connection reset while fetching {name}")
        self.fetched.append(name)
        data = _FILES[name]
        partial = self.blobs / f"{name}.incomplete"
        bar = kw.get("tqdm_class")
        with partial.open("wb") as out:
            context = bar(total=len(data), unit="B") if bar else None
            for start in range(0, len(data), _CHUNK):
                chunk = data[start : start + _CHUNK]
                out.write(chunk)
                out.flush()
                if context is not None:
                    context.update(len(chunk))
                    context.update_transfer(len(chunk))
                    context.set_postfix_str("10 MB/s")
                else:
                    time.sleep(0.002)
            if context is not None:
                context.close()
        target = self.blobs / name
        partial.replace(target)  # a file fetched again overwrites, as on Windows
        self.cached[name] = target
        return str(target)


@pytest.fixture(params=[True, False], ids=["bar", "no-bar"])
def hub(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _Hub:
    """Both kinds of ``huggingface_hub``: one whose ``hf_hub_download`` takes a
    bar, and an older one that does not, read from its ``.incomplete`` file."""
    hub = _Hub(tmp_path, with_bar=request.param)

    def with_a_bar(
        repo: str, name: str, *, cache_dir: Any = None, tqdm_class: Any = None
    ) -> str:
        return hub.download(repo, name, tqdm_class=tqdm_class)

    def without(repo: str, name: str, *, cache_dir: Any = None) -> str:
        return hub.download(repo, name)

    monkeypatch.setattr(
        huggingface_hub, "hf_hub_download", with_a_bar if hub.with_bar else without
    )
    monkeypatch.setattr(
        huggingface_hub, "try_to_load_from_cache", hub.try_to_load_from_cache
    )
    monkeypatch.setattr(
        huggingface_hub.HfApi,
        "get_paths_info",
        lambda self, repo, names: hub.get_paths_info(repo, names),
    )
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(downloads, "_POLL_S", 0.001)
    return hub


def _total_mb() -> str:
    return downloads.megabytes(sum(len(_FILES[n]) for n in _WEIGHTS))


def test_a_model_s_bytes_are_reported_against_its_size(hub: _Hub) -> None:
    progress: list[dict[str, Any]] = []

    kyutai_download.download(_REPO, on_progress=progress.append)

    fractions = [event["fraction"] for event in progress]
    assert fractions[0] == 0.0 and fractions[-1] == 1.0
    assert fractions == sorted(fractions)  # never backwards
    assert any(0.1 < f < 0.9 for f in fractions)  # measured as it went
    middle = [e["message"] for e in progress[1:-1]]
    assert middle and all(m.endswith(f" of {_total_mb()} MB") for m in middle)
    if hub.with_bar:  # every chunk counted as it is written
        assert f"Downloading 2.3 of {_total_mb()} MB" in middle
    assert progress[-1]["message"] == f"{_REPO} is ready"
    assert hub.fetched == ["config.json", *_WEIGHTS]
    assert kyutai_download.is_cached(_REPO)


def test_a_file_already_cached_counts_as_received_and_is_not_fetched(
    hub: _Hub,
) -> None:
    hub.download(_REPO, "config.json")
    hub.download(_REPO, "model.safetensors")
    hub.fetched.clear()
    progress: list[dict[str, Any]] = []

    kyutai_download.download(_REPO, on_progress=progress.append)

    assert hub.fetched == ["config.json", "mimi.safetensors", "tok.model"]
    assert progress[-1]["fraction"] == 1.0
    # The cached weights are counted at once: past 3 of 4.5 MB before the rest.
    assert progress[1]["fraction"] is not None and progress[1]["fraction"] > 0.6


def test_a_failed_download_raises_and_leaves_the_model_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub: _Hub
) -> None:
    hub.fail_on = "model.safetensors"
    progress: list[dict[str, Any]] = []

    with pytest.raises(OSError, match="model.safetensors"):
        kyutai_download.download(_REPO, on_progress=progress.append)

    assert progress[-1]["fraction"] != 1.0
    assert not kyutai_download.is_cached(_REPO)


def test_an_unknown_size_is_reported_as_unmeasured(hub: _Hub) -> None:
    hub.get_paths_info = lambda repo, names: []  # the Hub gave no size
    progress: list[dict[str, Any]] = []

    downloads.fetch(_REPO, _WEIGHTS, progress.append, what="the model")

    assert progress[0] == {"fraction": None, "message": "Downloading the model"}
    assert progress[-1] == {"fraction": 1.0, "message": "the model is ready"}
    assert all(event["fraction"] in (None, 1.0) for event in progress)
