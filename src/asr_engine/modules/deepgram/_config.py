"""Validation of the Deepgram modules' own config fields (specs/modules/deepgram.md).

Shared by ``deepgram-v1`` and ``deepgram-v2`` so both fail at construction, with a
message naming the field, rather than as an SDK ``TypeError`` or a rejected
connection once the engine starts. Imports nothing third-party.
"""

from __future__ import annotations

import math


def non_empty_str(config: dict, key: str, default: str, label: str) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} module: '{key}' must be a non-empty string")
    return value


def boolean(config: dict, key: str, default: bool, label: str) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{label} module: '{key}' must be true or false")
    return value


def number_in(
    config: dict, key: str, default: float, low: float, high: float, label: str
) -> float:
    value = config.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(
            f"{label} module: '{key}' must be a number from {low} to {high}"
        )
    return float(value)


def positive_int(config: dict, key: str, default: int, label: str) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} module: '{key}' must be a positive integer")
    return value
