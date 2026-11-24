"""The errors a config raises (specs/configuration.md "Errors").

Kept apart from ``config.py``, which re-exports them, so the modules can raise
them without importing the config loader (which imports the module registry).
"""

from __future__ import annotations

__all__ = ["ConfigError", "MissingSecretError"]


class ConfigError(ValueError):
    """Invalid configuration: bad JSON, a wrong shape, or a missing/invalid field.

    Subclasses ``ValueError`` — a malformed config is bad input data — so code
    written against the ``ValueError`` the engine raised before keeps working, and
    a caller composing this engine's config with others can catch one uniform
    "bad config" type.
    """


class MissingSecretError(ConfigError):
    """No key under the variable a module's config names (``api_key_env``, or its
    default): neither in the secrets given in code nor in the environment. Carries
    the variable, so a caller can say which key to set without reading the
    message."""

    def __init__(self, variable: str, *, label: str) -> None:
        super().__init__(
            f"{label}: no key under {variable!r}: set the environment variable, "
            "or pass it in `secrets`"
        )
        self.variable = variable
        self.label = label
