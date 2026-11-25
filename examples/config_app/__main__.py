"""Edit an engine config file on a page: Setup and Secrets, Save to the file.

Run it from the repo root::

    uv run python -m examples.config_app FILE [--env PATH] [--host ADDRESS] [--port N]

The file is an engine block (the demo's app is bare), and must exist; the keys go
to the ``.env`` next to it unless ``--env`` names another.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from app_setup.config_file import ConfigFileError
from app_setup.launch import add_config_app_arguments, open_config_app
from app_setup.ui import config_page

from examples.gradio_demo.app import APP


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"{APP.title}: edit a config file")
    add_config_app_arguments(parser)
    options = parser.parse_args(argv)
    try:
        model = open_config_app(options, APP)
    except ConfigFileError as exc:
        parser.error(str(exc))
    config_page(model).launch(server_name=options.host, server_port=options.port)


if __name__ == "__main__":
    main()
