"""Batch transcription over HTTP: ``POST /v1/audio/transcriptions``.

Registry key ``openai_transcriptions_compatible``; needs the
``openai-transcriptions-compatible`` extra (``httpx``). A protocol module: ``url``
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
from typing import Any
from urllib.parse import urlsplit

import httpx
import numpy as np

from asr_engine.modules.base import resolve_api_key
from asr_engine.modules.batch import BatchASRModule

log = logging.getLogger(__name__)

_LABEL = "openai_transcriptions_compatible"
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
    401: "the API key was rejected: check api_key / api_key_env",
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

    def __init__(
        self, config: dict, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        super().__init__(config)
        self._url = config.get("url", DEFAULT_URL)
        if not isinstance(self._url, str) or not self._url.startswith(
            ("http://", "https://")
        ):
            raise ValueError(
                f"{_LABEL} module: 'url' must be an http:// or https:// URL string"
            )
        self._api_key: str | None = None
        if config.get("api_key") or config.get("api_key_env"):
            self._api_key = resolve_api_key(config, _LABEL)
        elif urlsplit(self._url).hostname == _OPENAI_HOST:
            raise ValueError(
                f"{_LABEL} module: hosted OpenAI ({_OPENAI_HOST}) requires "
                f"'api_key' or 'api_key_env' in config"
            )

        self._fields: dict[str, str] = {
            "model": self._string(config, "model", DEFAULT_MODEL, required=True)
        }
        for key in ("language", "prompt"):
            value = self._string(config, key, None)
            if value is not None:
                self._fields[key] = value
        self._fields.update(self._extra_fields(config.get("extra_fields", {})))
        self._fields["response_format"] = "json"

        self._request_timeout_s = self._number(config, "request_timeout_s", 30.0)
        self._transport = transport
        self._client: httpx.Client | None = None

    @staticmethod
    def _string(
        config: dict, key: str, default: str | None, *, required: bool = False
    ) -> Any:
        value = config.get(key, default)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value:
            kind = "a non-empty string" if required else "null or a non-empty string"
            raise ValueError(f"{_LABEL} module: '{key}' must be {kind}")
        return value

    @staticmethod
    def _extra_fields(value: object) -> dict[str, str]:
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, (str, int, float, bool))
            for k, v in value.items()
        ):
            raise ValueError(
                f"{_LABEL} module: 'extra_fields' must be an object of string keys "
                f"to string, number or boolean values"
            )
        reserved = sorted(_RESERVED_FIELDS.intersection(value))
        if reserved:
            raise ValueError(
                f"{_LABEL} module: 'extra_fields' cannot set {reserved}: use the "
                f"module's own fields"
            )
        return {k: _form_value(v) for k, v in value.items()}

    # --------------------------------------------------------- BatchASRModule

    def load(self) -> None:
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
