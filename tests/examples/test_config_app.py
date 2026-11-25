"""The config app: one engine config file edited with the Setup and Secrets
panels, written back as the engine block alone (see specs/gradio-demo.md)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import gradio as gr
import pytest
from app_setup.launch import add_config_app_arguments, open_config_app
from app_setup.ui import config_page

from asr_engine.config import ASREngineConfig
from examples.config_app.__main__ import main
from examples.gradio_demo.app import APP, DEFAULT_CONFIG


def _open(argv: list[str]):
    """The config app's model, from its command line (what `main` builds)."""
    parser = argparse.ArgumentParser()
    add_config_app_arguments(parser)
    return open_config_app(parser.parse_args(argv), APP)


def test_the_config_app_saves_the_engine_block_to_its_file(tmp_path: Path) -> None:
    path = tmp_path / "speech.json"
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    model = _open([str(path)])

    page = config_page(model)
    model.save({"asr": {**DEFAULT_CONFIG, "sound_feedback": {"enabled": False}}})

    tabs = [b.label for b in page.blocks.values() if isinstance(b, gr.Tab)]
    assert tabs == ["Setup", "Secrets"]
    written = json.loads(path.read_text())
    assert written == {**DEFAULT_CONFIG, "sound_feedback": {"enabled": False}}
    # What it saved is what the library reads as it is.
    assert not ASREngineConfig.from_json_file(str(path)).sound_feedback.enabled


def test_the_config_app_refuses_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nowhere.json"

    with pytest.raises(SystemExit):
        main([str(missing)])

    assert f"config file {missing} does not exist" in capsys.readouterr().err
    assert not missing.exists()
