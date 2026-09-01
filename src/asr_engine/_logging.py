from __future__ import annotations

import logging


def setup_logging(level: int | str = logging.INFO) -> None:
    """Configure the root logger for scripts and entry points (application layer).

    Library modules never call this — they only acquire a logger. Entry points and
    scripts call it once at startup. ``level`` accepts a numeric level or a level
    name (e.g. ``"DEBUG"``); ``basicConfig`` resolves either.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
