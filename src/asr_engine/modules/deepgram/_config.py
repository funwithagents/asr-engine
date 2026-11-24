"""What the two Deepgram modules declare alike (specs/modules/deepgram.md): their
extra, their key, the import of the SDK, and the probe. Imports nothing
third-party.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asr_engine.modules.base import ASRModule, get_json, key_env_field

PROJECTS_URL = "https://api.deepgram.com/v1/projects"
KEY_FIELD = key_env_field("Deepgram", "DEEPGRAM_API_KEY")


class DeepgramModule(ASRModule):
    """The part of ``deepgram-v1`` and ``deepgram-v2`` that is the same."""

    kind = "hosted"
    extra = "deepgram"

    @classmethod
    def imports(cls, config: dict) -> None:
        try:
            import deepgram  # noqa: F401
        except ImportError as exc:
            raise ImportError(cls.install_hint("deepgram")) from exc

    @classmethod
    def probe(cls, config: dict, secrets: Mapping[str, str] | None = None) -> str:
        # Deepgram's model list answers without a key and leaves Flux out, so the
        # key is proven on the projects list instead; the model is the
        # connection's to accept.
        key = cls.resolve_api_key(config, secrets)
        body: Any = get_json(
            PROJECTS_URL, {"Authorization": f"Token {key}"}, what="Deepgram"
        )
        count = len(body.get("projects", [])) if isinstance(body, dict) else 0
        return (
            f"Deepgram accepted the key ({count} project(s)); the model is checked "
            "when a session connects."
        )
