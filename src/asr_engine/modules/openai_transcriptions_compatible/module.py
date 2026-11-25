"""Batch transcription over HTTP: ``POST /v1/audio/transcriptions``.

Registry key ``openai-transcriptions-compatible``; needs the ``openai-compatible``
extra (``httpx``). A protocol module: ``url``
defaults to hosted OpenAI, and any server implementing the endpoint works (Groq,
speaches, the whisper.cpp server, ``mlx_audio.server``, vLLM's batch models).

A batch module (``BatchASRModule``): the VAD gate ends each utterance, which is
uploaded as one 16 kHz WAV file; the response's ``text`` is the final. Every
request runs on the base class's worker thread, so the HTTP client is synchronous.
See specs/modules/openai-transcriptions-compatible.md.
"""

from __future__ import annotations

import io
import logging
import wave
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import numpy as np

from asr_engine.errors import ConfigError
from asr_engine.modules.base import key_env_field, openai_needs_a_key, probe_listing
from asr_engine.modules.batch import BatchASRModule, batch_fields
from asr_engine.modules.fields import Field

if TYPE_CHECKING:
    import httpx

log = logging.getLogger(__name__)

_LABEL = "openai-transcriptions-compatible"
_TITLE = "OpenAI Transcriptions-compatible"
_MESSAGES = f"{_TITLE} module"  # the prefix of its config messages
DEFAULT_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_MODEL = "gpt-4o-transcribe"
_OPENAI_HOST = "api.openai.com"
_SAMPLE_RATE = 16000

# Hosted OpenAI rejects audio under 0.1 s with a 400 ("Audio file might be
# corrupted or unsupported", measured): shorter utterances are not sent.
_MIN_AUDIO_S = 0.1
# load() sends this much silence once, to check url / key / model and to open
# the connection (the first request pays the TLS handshake, measured 1.6 s vs
# 0.5-1 s after). Its text is discarded.
_WARM_UP_S = 0.5
# Form fields the module sets itself; extra_fields cannot override them.
_RESERVED_FIELDS = frozenset(
    {"file", "model", "language", "prompt", "response_format", "stream"}
)
_STATUS_HINTS = {
    401: "the API key was rejected: check the variable named by api_key_env",
    403: "access was refused: check the API key's permissions and model",
    404: "no such endpoint or model: check url and model",
}


class TranscriptionError(RuntimeError):
    """The server answered a transcription request with an error."""


def _wav(audio: np.ndarray) -> bytes:
    """float32 mono in [-1, 1] → a 16 kHz 16-bit WAV file (the exact inverse of
    the base class's int16 → float32 conversion)."""
    pcm = np.clip(np.round(audio * 32768.0), -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(_SAMPLE_RATE)
        out.writeframes(pcm.tobytes())
    return buf.getvalue()


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    return response.text[:300]


def _form_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class OpenAITranscriptionsCompatibleModule(BatchASRModule):
    """Any server implementing OpenAI's ``/v1/audio/transcriptions`` endpoint."""

    LABEL = _LABEL
    DEFAULT_INTERIM_INTERVAL_S = 0.0  # each interim is a billed re-upload

    kind = "hosted"
    title = _TITLE
    extra = "openai-compatible"
    fields = (
        key_env_field("OpenAI-compatible server", None),
        Field(
            "url",
            "string",
            "The server's transcriptions endpoint, an http:// or https:// URL.",
            default=DEFAULT_URL,
        ),
        Field("model", "string", "The transcription model.", default=DEFAULT_MODEL),
        Field(
            "language",
            "string",
            "The language, as an ISO 639-1 code ('en'). Absent, it is detected.",
        ),
        Field(
            "prompt",
            "string",
            "A text to guide the transcription (vocabulary, spelling).",
            advanced=True,
        ),
        Field(
            "request_timeout_s",
            "number",
            "How long one transcription request may take, in seconds.",
            default=30.0,
            exclusive_minimum=0,
            advanced=True,
        ),
        *batch_fields(DEFAULT_INTERIM_INTERVAL_S),
    )
    schema_extras = {
        "extra_fields": {
            "type": "object",
            "additionalProperties": True,
            "description": "More form fields sent with each request (a server's "
            "own parameters): string keys, string, number or boolean values.",
            "x-advanced": True,
        },
    }

    @classmethod
    def config_rules(cls, values: dict[str, Any], config: dict) -> None:
        url = values["url"]
        if not url.startswith(("http://", "https://")):
            raise ConfigError(
                f"{_MESSAGES}: 'url' must be an http:// or https:// URL string"
            )
        if values["api_key_env"] is None and urlsplit(url).hostname == _OPENAI_HOST:
            raise ConfigError(openai_needs_a_key(_MESSAGES))
        cls._extra_fields(config.get("extra_fields", {}))

    @classmethod
    def imports(cls, config: dict) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise ImportError(cls.install_hint("openai-compatible")) from exc

    @classmethod
    def probe(cls, config: dict, secrets: Mapping[str, str] | None = None) -> str:
        values = cls.validated(config)
        return probe_listing(cls, config, secrets, values["url"], values["model"])

    def __init__(
        self,
        config: dict,
        *,
        secrets: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(config)
        values = self._values
        self.imports(config)
        self._url: str = values["url"]
        # No variable named: no key is sent (a local server); hosted OpenAI is
        # refused that way by config_rules.
        self._api_key: str | None = (
            self.resolve_api_key(config, secrets)
            if values["api_key_env"] is not None
            else None
        )
        self._fields: dict[str, str] = {"model": values["model"]}
        for key in ("language", "prompt"):
            if values[key] is not None:
                self._fields[key] = values[key]
        self._fields.update(self._extra_fields(config.get("extra_fields", {})))
        self._fields["response_format"] = "json"

        self._request_timeout_s: float = values["request_timeout_s"]
        self._transport = transport
        self._client: httpx.Client | None = None

    @staticmethod
    def _extra_fields(value: object) -> dict[str, str]:
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, (str, int, float, bool))
            for k, v in value.items()
        ):
            raise ConfigError(
                f"{_MESSAGES}: 'extra_fields' must be an object of string keys "
                f"to string, number or boolean values"
            )
        reserved = sorted(_RESERVED_FIELDS.intersection(value))
        if reserved:
            raise ConfigError(
                f"{_MESSAGES}: 'extra_fields' cannot set {reserved}: use the "
                f"module's own fields"
            )
        return {k: _form_value(v) for k, v in value.items()}

    # --------------------------------------------------------- BatchASRModule

    def load(self) -> None:
        import httpx

        if self._client is None:
            headers = (
                {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            )
            self._client = httpx.Client(
                headers=headers,
                timeout=self._request_timeout_s,
                transport=self._transport,
            )
        self._post(np.zeros(int(_SAMPLE_RATE * _WARM_UP_S), dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < _SAMPLE_RATE * _MIN_AUDIO_S:
            return ""
        return self._post(audio)

    def _post(self, audio: np.ndarray) -> str:
        import httpx

        assert self._client is not None, "transcribe() before load()"
        try:
            response = self._client.post(
                self._url,
                files={"file": ("audio.wav", _wav(audio), "audio/wav")},
                data=self._fields,
            )
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"request to {self._url} failed: {exc}") from exc
        if response.status_code >= 400:
            hint = _STATUS_HINTS.get(response.status_code)
            raise TranscriptionError(
                f"HTTP {response.status_code} from {self._url}: "
                f"{_error_message(response)}" + (f" ({hint})" if hint else "")
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise TranscriptionError(
                f"{self._url} answered with non-JSON: {response.text[:200]!r}"
            ) from exc
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise TranscriptionError(f"{self._url} answered without a 'text' string")
        return text
