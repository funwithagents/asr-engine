"""Gradio demo UI for the ASR engine (see specs/gradio-demo.md).

A thin wiring layer over ``DemoController``: it builds the Gradio page, binds
each widget to a controller method, and polls ``controller.state()`` on a timer
to refresh results and widget enablement. All logic lives in the controller.

Run it from the repo root::

    uv run python -m examples.gradio_demo.app --config config.json
"""

from __future__ import annotations

import argparse

import gradio as gr

from asr_engine.config import ASREngineConfig, ModuleConfig, load_config

from .controller import ControllerState, DemoController

_SEG_MODES = ["utterance", "trigger_word", "timeout"]
_POLL_INTERVAL_S = 0.5


def _default_config() -> ASREngineConfig:
    """Minimal built-in config when no ``--config`` is given: Deepgram via env."""
    return ASREngineConfig(
        module=ModuleConfig(
            type="deepgram_v1", extra={"api_key_env": "DEEPGRAM_API_KEY"}
        )
    )


def build_ui(controller: DemoController) -> gr.Blocks:
    """Assemble the Gradio Blocks page bound to *controller*."""
    devices = controller.available_devices()
    modules = controller.available_modules()
    # Default the device to the first available one rather than the system default.
    if devices and controller.state().device is None:
        controller.set_device(devices[0])
    initial = controller.state()

    with gr.Blocks(title="ASR Engine Demo") as demo:
        gr.Markdown("# ASR Engine Demo\nDrive an in-process `ASREngine` directly.")

        with gr.Row():
            device_dd = gr.Dropdown(
                choices=devices, value=initial.device, label="Input device"
            )
            module_dd = gr.Dropdown(
                choices=modules, value=initial.module_type, label="ASR module"
            )

        with gr.Row():
            start_btn = gr.Button("Start", variant="primary")
            stop_btn = gr.Button("Stop")
            listen_btn = gr.Button("Listen (single-shot)")

        with gr.Row():
            mode_radio = gr.Radio(
                choices=_SEG_MODES,
                value=initial.segmentation_mode,
                label="Segmentation mode",
            )

        with gr.Row():
            trigger_words_tb = gr.Textbox(
                value=initial.trigger_words,
                label="Trigger words (comma-separated)",
                info="used in trigger_word mode",
            )
            initial_silence_num = gr.Number(
                value=initial.initial_silence_timeout_s,
                label="Initial-silence timeout (s)",
                info="timeout mode",
            )
            eos_num = gr.Number(
                value=initial.end_of_speech_timeout_s,
                label="End-of-speech timeout (s)",
                info="timeout mode",
            )
            apply_params_btn = gr.Button("Apply params")

        with gr.Row():
            state_box = gr.Textbox(
                label="Engine state",
                info="stopped · running (always-on) · listening (single-shot)",
                interactive=False,
            )
            connected_box = gr.Textbox(
                label="Backend connected",
                info="ASR provider socket state",
                interactive=False,
            )
        message_box = gr.Textbox(
            label="Last activity",
            info="result of your most recent action",
            interactive=False,
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
            device_dd,
            module_dd,
            mode_radio,
            start_btn,
            stop_btn,
            listen_btn,
            state_box,
            connected_box,
            message_box,
            utt_log,
            seg_log,
            last_listen,
            trigger_words_tb,
            initial_silence_num,
            eos_num,
            apply_params_btn,
        ]

        def render() -> list:
            return _render(controller.state())

        def on_start() -> list:
            controller.start()
            return render()

        def on_stop() -> list:
            controller.stop()
            return render()

        def on_listen() -> list:
            controller.listen()
            return render()

        def on_device(value: str | None) -> list:
            controller.set_device(value)
            return render()

        def on_module(value: str) -> list:
            controller.set_module(value)
            return render()

        def on_mode(value: str) -> list:
            controller.set_segmentation_mode(value)
            return render()

        def on_apply_params(words: str, ist: float, eost: float) -> list:
            controller.set_segmentation_params(words, ist, eost)
            return render()

        def on_clear() -> list:
            controller.clear_logs()
            return render()

        start_btn.click(on_start, outputs=outputs)
        stop_btn.click(on_stop, outputs=outputs)
        listen_btn.click(on_listen, outputs=outputs)
        device_dd.change(on_device, inputs=device_dd, outputs=outputs)
        module_dd.change(on_module, inputs=module_dd, outputs=outputs)
        mode_radio.change(on_mode, inputs=mode_radio, outputs=outputs)
        apply_params_btn.click(
            on_apply_params,
            inputs=[trigger_words_tb, initial_silence_num, eos_num],
            outputs=outputs,
        )
        clear_btn.click(on_clear, outputs=outputs)

        gr.Timer(_POLL_INTERVAL_S).tick(render, outputs=outputs)

    return demo


def _render(state: ControllerState) -> list:
    """Map a ControllerState to gr.update()s for every output, in ``outputs`` order."""
    return [
        gr.update(interactive=state.config_enabled),  # device_dd
        gr.update(interactive=state.config_enabled),  # module_dd
        gr.update(interactive=state.can_set_mode, value=state.segmentation_mode),
        gr.update(interactive=state.can_start),  # start_btn
        gr.update(interactive=state.can_stop),  # stop_btn
        gr.update(interactive=state.can_listen),  # listen_btn
        gr.update(value=state.phase),  # state_box
        gr.update(value="yes" if state.connected else "no"),
        gr.update(value=state.message),
        gr.update(value="\n".join(state.utterance_log)),
        gr.update(value="\n".join(state.segment_log)),
        gr.update(value=state.last_listen),
        # Param inputs: toggle enablement only — never overwrite the value, or the
        # timer would clobber what the user is typing.
        gr.update(interactive=state.can_set_mode),  # trigger_words_tb
        gr.update(interactive=state.can_set_mode),  # initial_silence_num
        gr.update(interactive=state.can_set_mode),  # eos_num
        gr.update(interactive=state.can_set_mode),  # apply_params_btn
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Gradio demo for the ASR engine.")
    parser.add_argument(
        "--config", help="Path to a JSON config file (only its `engine` block is used)."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind the UI to.")
    parser.add_argument(
        "--port", type=int, default=7860, help="Port to bind the UI to."
    )
    args = parser.parse_args()

    config = load_config(args.config).engine if args.config else _default_config()

    with DemoController(config) as controller:
        ui = build_ui(controller)
        ui.launch(server_name=args.host, server_port=args.port)


if __name__ == "__main__":
    main()
