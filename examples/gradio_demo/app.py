"""The engine's demo page (see specs/gradio-demo.md).

The demo's own Listen tab next to app-setup's Setup and Secrets panels, over
one section, ``asr``, the engine block: the Setup panel chooses and configures a
module and an input device, installs their extras and downloads their models;
the Listen tab starts a session from the saved config, listens once, runs a
dictation, and shows the utterances and segments. While a session runs the
config is locked: the Setup panel cannot save under it.

Run it from the repo root::

    uv run python -m examples.gradio_demo [--folder PATH] [--host ADDRESS] [--port N]
"""

from __future__ import annotations

import argparse
import json
import logging
import warnings
from collections.abc import Sequence
from pathlib import Path

import gradio as gr
from app_setup.app import App, AppModel, Section
from app_setup.folder import ConfigFolder, default_folder
from app_setup.ui import secrets_panel, setup_panel

import asr_engine.setup

from .controller import SECTION, ControllerState, DemoController

__all__ = ["APP", "DEFAULT_CONFIG", "DEFAULT_CONFIG_FILE", "build_page", "main"]

log = logging.getLogger(__name__)

# The first config of a new folder, a committed file (one field per line, for
# review): the fake module needs nothing installed and no key, so the page works
# before any choice; it replays its script on the microphone's audio clock.
DEFAULT_CONFIG_FILE = Path(__file__).with_name("default_config.json")
DEFAULT_CONFIG = json.loads(DEFAULT_CONFIG_FILE.read_text(encoding="utf-8"))

APP = App(
    name="asr-engine-demo",
    title="asr-engine — set up speech recognition",
    host="asr-engine",
    sections=(Section(SECTION, asr_engine.setup, title="Speech recognition"),),
    default_config=DEFAULT_CONFIG,
)

_SEG_MODES = ["utterance", "trigger_word", "timeout"]
# Also how fast the voice-activity box follows the gate; 0.25 s keeps a short
# utterance (the gate stays open at least min_silence_duration_ms) visible.
_POLL_INTERVAL_S = 0.25


def _listen_tab(model: AppModel, controller: DemoController) -> None:
    """Start/Stop, Listen, dictation, and what the engine heard."""
    gr.Markdown(
        "Start a session with the **saved** config (the Setup tab changes it while "
        "nothing runs). Listen captures one segment, then stops."
    )
    with gr.Row():
        start_btn = gr.Button("Start", variant="primary")
        stop_btn = gr.Button("Stop")

    with gr.Row():
        state_box = gr.Textbox(
            label="Session",
            info="stopped · starting · running · dictating · stopping · listening (single-shot)",
            interactive=False,
        )
        module_box = gr.Textbox(
            label="Module",
            info="the session's, else the saved config's",
            interactive=False,
        )
        connected_box = gr.Textbox(
            label="Backend connected",
            info="the module's connection, or its loaded model",
            interactive=False,
        )

    with gr.Row():
        vad_config_box = gr.Textbox(
            label="Voice activity detection",
            info="the gate in the config",
            interactive=False,
            scale=2,
        )
        voice_activity_box = gr.Textbox(
            label="Voice activity",
            info="is audio reaching the module right now?",
            interactive=False,
        )

    with gr.Row():
        with gr.Row():
            seg_mode_dd = gr.Dropdown(
                choices=_SEG_MODES,
                value="trigger_word",
                label="Segmentation mode",
                info="used by both Listen and Start dictation",
            )
            end_on_final_cb = gr.Checkbox(
                value=False,
                label="End dictation on first final segment",
            )
        with gr.Column():
            with gr.Row():
                start_dict_btn = gr.Button("Start dictation")
                stop_dict_btn = gr.Button("Stop dictation")
            with gr.Row():
                listen_btn = gr.Button("Listen (single-shot)")

    message_box = gr.Textbox(
        label="Last activity",
        info="the result of your most recent action",
        interactive=False,
        lines=2,
    )

    with gr.Row():
        with gr.Column():
            gr.Markdown("### Utterances (interim + final)")
            utt_log = gr.Textbox(
                label="", lines=16, max_lines=16, autoscroll=True, interactive=False
            )
        with gr.Column():
            gr.Markdown("### Segments (interim + closed)")
            seg_log = gr.Textbox(
                label="", lines=16, max_lines=16, autoscroll=True, interactive=False
            )
    clear_btn = gr.Button("Clear utterances & segments")
    last_listen = gr.Textbox(label="Last listen result", interactive=False)

    outputs = [
        start_btn,
        stop_btn,
        listen_btn,
        start_dict_btn,
        stop_dict_btn,
        state_box,
        module_box,
        connected_box,
        vad_config_box,
        voice_activity_box,
        message_box,
        utt_log,
        seg_log,
        last_listen,
    ]

    def render() -> list:
        return _render(controller.state(model))

    def on_start() -> list:
        controller.start(model)
        return render()

    def on_stop() -> list:
        controller.stop()
        return render()

    def on_listen(mode: str) -> list:
        controller.listen(model, mode)
        return render()

    def on_start_dictation(mode: str, end_on_final: bool) -> list:
        controller.start_dictation(mode=mode, end_on_final_segment=end_on_final)
        return render()

    def on_stop_dictation() -> list:
        controller.stop_dictation()
        return render()

    def on_clear() -> list:
        controller.clear_logs()
        return render()

    def busy() -> tuple[dict, dict]:
        # Both buttons greyed at once, before the handler runs: a session is
        # starting or stopping until the next render says otherwise.
        return gr.update(interactive=False), gr.update(interactive=False)

    start_btn.click(busy, outputs=[start_btn, stop_btn], queue=False).then(
        on_start, outputs=outputs
    )
    stop_btn.click(busy, outputs=[start_btn, stop_btn], queue=False).then(
        on_stop, outputs=outputs
    )
    listen_btn.click(on_listen, inputs=seg_mode_dd, outputs=outputs)
    start_dict_btn.click(
        on_start_dictation, inputs=[seg_mode_dd, end_on_final_cb], outputs=outputs
    )
    stop_dict_btn.click(on_stop_dictation, outputs=outputs)
    clear_btn.click(on_clear, outputs=outputs)
    gr.Timer(_POLL_INTERVAL_S).tick(render, outputs=outputs)


def _render(state: ControllerState) -> list:
    """Map a ControllerState to gr.update()s for every output, in ``outputs`` order."""
    # Show the active mode alongside the phase while it differs from the resting
    # utterance mode, so the user can see what dictation/listen is running.
    phase_display = state.phase
    if state.phase in ("dictating", "listening"):
        phase_display = f"{state.phase} · {state.segmentation_mode}"
    return [
        gr.update(interactive=state.can_start),  # start_btn
        gr.update(interactive=state.can_stop),  # stop_btn
        gr.update(interactive=state.can_listen),  # listen_btn
        gr.update(interactive=state.can_dictate and not state.dictating),
        gr.update(interactive=state.dictating),  # stop_dict_btn
        gr.update(value=phase_display),  # state_box
        gr.update(value=state.module_type),
        gr.update(value="yes" if state.connected else "no"),
        gr.update(value=state.vad),
        gr.update(value=state.voice_activity),
        gr.update(value=state.message),
        gr.update(value="\n".join(state.utterance_log)),
        gr.update(value="\n".join(state.segment_log)),
        gr.update(value=state.last_listen),
    ]


def build_page(model: AppModel, controller: DemoController) -> gr.Blocks:
    """The demo's page: its Listen tab, then app-setup's Setup and Secrets
    panels. It opens on Setup."""
    with gr.Blocks(title=model.title) as page:
        gr.Markdown(f"# {model.title}")
        with gr.Tabs(selected="setup"):
            with gr.Tab("Listen", id="listen"):
                _listen_tab(model, controller)
            with gr.Tab("Setup", id="setup"):
                setup = setup_panel(model)
            with gr.Tab("Secrets", id="secrets"):
                secrets_panel(model, setup.draft)
    return page


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    folder = default_folder(APP.name)
    parser = argparse.ArgumentParser(description=APP.title)
    parser.add_argument(
        "--folder",
        type=Path,
        default=folder,
        help=f"the demo's folder: its config and its keys (default: {folder})",
    )
    parser.add_argument("--host", default="127.0.0.1", help="the address to serve on")
    parser.add_argument("--port", type=int, default=None, help="the port to serve on")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    options = _arguments(argv)
    # This example is an application entry point, so it configures logging itself
    # (deliberately not importing the library's private _logging.setup_logging).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # gradio reads starlette's deprecated 422 constant on every queued event,
    # rejected or not (it builds its error map each time): noise, not a
    # rejection. A headless browser saw no request of the page refused.
    warnings.filterwarnings("ignore", message=".*HTTP_422_UNPROCESSABLE_ENTITY.*")
    with DemoController() as controller:
        # Building the model makes the folder ready and loads its .env. The
        # config is locked while a session runs.
        model = AppModel(
            APP,
            ConfigFolder(options.folder, APP.default_config),
            locked=controller.locked,
        )
        log.info("folder: %s", model.path)
        build_page(model, controller).launch(
            server_name=options.host, server_port=options.port
        )


if __name__ == "__main__":
    main()
