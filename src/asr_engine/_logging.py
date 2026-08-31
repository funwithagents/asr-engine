from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with a standard format for scripts and entry points."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
