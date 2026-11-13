"""Functional tests for the openai_transcriptions_compatible module
(specs/modules/openai-transcriptions-compatible.md).

The module talks to an in-process server (``httpx.MockTransport``) that parses each
multipart upload the way a real one would. The batch behaviour (buffering,
interims, finals, lifecycle) is pinned in test_batch.py; these tests cover the
HTTP protocol the module adds on top.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any, Callable

import httpx
import numpy as np
import pytest

from asr_engine.audio import END_OF_SPEECH, AudioFormat
from asr_engine.modules import resolve_module_class
from asr_engine.modules.base import AudioQueue, SpeechUtterance
from asr_engine.modules.openai_transcriptions_compatible.module import (
    OpenAITranscriptionsCompatibleModule,
)

LOCAL_URL = "http://localhost:8000/v1/audio/transcriptions"
SAMPLE_RATE = 16000


class Upload:
    """One request as the server saw it: headers, form fields, decoded audio."""

    def __init__(self, request: httpx.Request) -> None:
        self.url = str(request.url)
        self.headers = request.headers
        message = BytesParser(policy=HTTP).parsebytes(
            b"Content-Type: "
            + request.headers["content-type"].encode()
            + b"\r\n\r\n"
            + request.read()
        )
        self.fields: dict[str, str] = {}
        self.wav: bytes = b""
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True)
            assert isinstance(name, str) and isinstance(payload, bytes)
            if name == "file":
                self.wav = payload
            else:
                self.fields[name] = payload.decode()
        with wave.open(io.BytesIO(self.wav)) as wav:
            self.rate = wav.getframerate()
            self.channels = wav.getnchannels()
            self.samples = np.frombuffer(
                wav.readframes(wav.getnframes()), dtype="<i2"
            ).copy()

    @property
    def is_warm_up(self) -> bool:
        return not self.samples.any()  # load() sends pure silence


Reply = Callable[[Upload], httpx.Response]


class Server:
    """Records every upload and answers it with ``reply``."""

    def __init__(self, reply: Reply | None = None) -> None:
        self.uploads: list[Upload] = []
        self.started = time.monotonic()
        self.log: list[str] = []  # "<s since start> <warm-up|speech>", per request
        self.reply: Reply = reply or (
            lambda up: httpx.Response(200, json={"text": f" heard {len(up.samples)} "})
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        upload = Upload(request)
        self.uploads.append(upload)
        kind = "warm-up" if upload.is_warm_up else "speech"
        self.log.append(f"{time.monotonic() - self.started:.2f}s {kind}")
        return self.reply(upload)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def speech(self) -> list[Upload]:
        return [u for u in self.uploads if not u.is_warm_up]


def speech(seconds: float, value: int = 1000) -> bytes:
    n = int(SAMPLE_RATE * seconds)
    samples = (np.arange(n) % 200 - 100).astype("<i2") * (value // 100)
    return samples.tobytes()


class Session:
    """Runs a module's start() and collects what it emits."""

    def __init__(
        self,
        module: OpenAITranscriptionsCompatibleModule,
        seen: Callable[[], object] = list,
    ) -> None:
        self.module = module
        self.seen = seen  # what the server received, for timeout messages
        self.queue: AudioQueue = asyncio.Queue()
        self.utterances: list[SpeechUtterance] = []
        self.connected: list[bool] = []
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Session:
        async def on_utterance(u: SpeechUtterance) -> None:
            self.utterances.append(u)

        self.task = asyncio.create_task(
            self.module.start(
                self.queue,
                on_utterance,
                self.connected.append,
                audio_format=AudioFormat(SAMPLE_RATE),
            )
        )
        try:
            await self.until(lambda: bool(self.connected))
        except BaseException:
            # __aexit__ does not run when __aenter__ raises: stop here, or the
            # start() task outlives the test (on Windows a pending task then
            # blocks the event loop from closing, and the run hangs).
            await self.__aexit__()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.module.stop()
        assert self.task is not None
        await asyncio.wait_for(self.task, 5.0)

    async def until(self, condition: Callable[[], bool], timeout: float = 10.0) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        while not condition():
            if loop.time() - started > timeout:
                raise AssertionError(
                    f"condition not met within {timeout:g}s (connected="
                    f"{self.connected}, utterances={self.utterances}, server saw "
                    f"{self.seen()})"
                )
            await asyncio.sleep(0.005)

    def say(self, *chunks: bytes) -> None:
        for chunk in chunks:
            self.queue.put_nowait(chunk)
        self.queue.put_nowait(END_OF_SPEECH)

    @property
    def finals(self) -> list[str]:
        return [u.transcript for u in self.utterances if u.is_final]


def module(server: Server, **config: Any) -> OpenAITranscriptionsCompatibleModule:
    return OpenAITranscriptionsCompatibleModule(
        {"url": LOCAL_URL, **config}, transport=server.transport
    )


# ------------------------------------------------------------------- protocol


async def test_each_utterance_is_uploaded_as_a_wav_and_its_text_is_the_final() -> None:
    server = Server()
    mod = module(
        server,
        api_key="sk-test",
        model="whisper-large-v3-turbo",
        language="fr",
        prompt="validate",
        extra_fields={"temperature": 0, "timestamp_granularities": "word"},
    )
    audio = speech(1.0)
    async with Session(mod) as session:
        session.say(audio[:8000], audio[8000:])
        await session.until(lambda: bool(session.finals))

    upload = server.speech[0]
    assert upload.url == LOCAL_URL
    assert upload.headers["authorization"] == "Bearer sk-test"
    assert upload.fields == {
        "model": "whisper-large-v3-turbo",
        "language": "fr",
        "prompt": "validate",
        "temperature": "0",
        "timestamp_granularities": "word",
        "response_format": "json",
    }
    assert (upload.rate, upload.channels) == (SAMPLE_RATE, 1)
    np.testing.assert_array_equal(upload.samples, np.frombuffer(audio, dtype="<i2"))
    assert session.finals == ["heard 16000"]
    assert session.connected == [True, False]


async def test_load_sends_one_warm_up_before_connecting_and_never_again() -> None:
    server = Server()
    mod = module(server)
    for _ in range(2):  # the client is resident across stop()/start()
        async with Session(mod) as session:
            session.say(speech(0.5))
            await session.until(lambda: bool(session.finals))

    assert [(u.is_warm_up, len(u.samples)) for u in server.uploads] == [
        (True, 8000),  # 0.5 s of silence, once
        (False, 8000),
        (False, 8000),
    ]


async def test_without_an_api_key_no_authorization_header_is_sent() -> None:
    server = Server()
    async with Session(module(server)) as session:
        session.say(speech(0.5))
        await session.until(lambda: bool(session.finals))

    assert "authorization" not in server.speech[0].headers
    assert server.speech[0].fields == {
        "model": "gpt-4o-transcribe",
        "response_format": "json",
    }


async def test_interims_are_off_by_default() -> None:
    server = Server()
    async with Session(module(server)) as session:
        session.say(*(speech(0.25) for _ in range(8)))  # 2 s of speech
        await session.until(lambda: bool(session.finals))

    assert len(server.speech) == 1
    assert [u.is_final for u in session.utterances] == [True]


async def test_interims_can_be_turned_on() -> None:
    server = Server()
    async with Session(module(server, interim_interval_s=0.5)) as session:
        for n in (1, 2, 3):  # one interim per half second, each awaited
            session.queue.put_nowait(speech(0.5))
            await session.until(lambda n=n: len(session.utterances) == n)
        session.queue.put_nowait(END_OF_SPEECH)
        await session.until(lambda: bool(session.finals))

    assert [(u.transcript, u.is_final) for u in session.utterances] == [
        ("heard 8000", False),
        ("heard 16000", False),
        ("heard 24000", False),
        ("heard 24000", True),
    ]
    assert [len(u.samples) for u in server.speech] == [8000, 16000, 24000, 24000]


async def test_an_utterance_under_a_tenth_of_a_second_is_not_sent() -> None:
    server = Server()
    async with Session(module(server)) as session:
        session.say(speech(0.05))
        session.say(speech(0.5))
        await session.until(lambda: bool(session.finals))

    assert [len(u.samples) for u in server.speech] == [8000]
    assert session.finals == ["heard 8000"]


@pytest.mark.parametrize(
    "response, logged",
    [
        (
            httpx.Response(401, json={"error": {"message": "Incorrect API key"}}),
            "HTTP 401 from http://localhost:8000/v1/audio/transcriptions: "
            "Incorrect API key (the API key was rejected",
        ),
        (httpx.Response(500, text="upstream exploded"), "HTTP 500"),
        (httpx.Response(200, text="The sky is blue"), "non-JSON"),
        (httpx.Response(200, json={"transcript": "x"}), "without a 'text' string"),
    ],
    ids=["status-with-hint", "status-plain-body", "non-json", "no-text"],
)
async def test_a_failed_request_drops_its_utterance_and_the_next_one_works(
    response: httpx.Response, logged: str, caplog: pytest.LogCaptureFixture
) -> None:
    replies = iter([response])

    def reply(upload: Upload) -> httpx.Response:
        if upload.is_warm_up:
            return httpx.Response(200, json={"text": ""})
        return next(replies, httpx.Response(200, json={"text": "second"}))

    server = Server(reply)
    with caplog.at_level(logging.ERROR):
        async with Session(module(server)) as session:
            session.say(speech(0.5))
            await session.until(lambda: len(server.speech) == 1)
            session.say(speech(0.5))
            await session.until(lambda: bool(session.finals))

    assert session.finals == ["second"]
    assert logged in caplog.text


async def test_a_failed_warm_up_is_retried_before_connecting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    replies = iter(
        [httpx.Response(404, json={"error": {"message": "model_not_found"}})]
    )
    server = Server(lambda up: next(replies, httpx.Response(200, json={"text": "ok"})))
    mod = module(server)
    mod._BACKOFF_S = (0.01,)
    with caplog.at_level(logging.ERROR):
        async with Session(mod, lambda: server.log) as session:
            session.say(speech(0.5))
            await session.until(lambda: bool(session.finals))

    assert [u.is_warm_up for u in server.uploads] == [True, True, False]
    assert "no such endpoint or model: check url and model" in caplog.text
    assert session.connected == [True, False]


async def test_an_unreachable_server_is_retried_before_connecting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    started = time.monotonic()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{time.monotonic() - started:.2f}s")
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"text": ""})

    mod = OpenAITranscriptionsCompatibleModule(
        {"url": LOCAL_URL}, transport=httpx.MockTransport(handler)
    )
    mod._BACKOFF_S = (0.01,)
    with caplog.at_level(logging.ERROR):
        async with Session(mod, lambda: calls) as session:
            assert session.connected == [True]

    assert len(calls) == 2
    assert "connection refused" in caplog.text


# ------------------------------------------------------------------ config


def test_hosted_openai_requires_an_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        OpenAITranscriptionsCompatibleModule({})


def test_the_api_key_can_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("TEST_TRANSCRIBE_KEY", "sk-env")
    server = Server()
    mod = OpenAITranscriptionsCompatibleModule(
        {"api_key_env": "TEST_TRANSCRIBE_KEY"}, transport=server.transport
    )
    mod.load()

    assert server.uploads[0].url == "https://api.openai.com/v1/audio/transcriptions"
    assert server.uploads[0].headers["authorization"] == "Bearer sk-env"


@pytest.mark.parametrize(
    "config, field",
    [
        ({"url": "ws://localhost/v1/audio/transcriptions"}, "url"),
        ({"url": 3}, "url"),
        ({"model": ""}, "model"),
        ({"model": None}, "model"),
        ({"language": ""}, "language"),
        ({"prompt": 3}, "prompt"),
        ({"extra_fields": ["temperature"]}, "extra_fields"),
        ({"extra_fields": {"temperature": None}}, "extra_fields"),
        ({"extra_fields": {"model": "other"}}, "extra_fields"),
        ({"extra_fields": {"response_format": "text"}}, "extra_fields"),
        ({"request_timeout_s": 0}, "request_timeout_s"),
        ({"interim_interval_s": -1}, "interim_interval_s"),
        ({"api_key_env": "TEST_TRANSCRIBE_UNSET_KEY"}, "TEST_TRANSCRIBE_UNSET_KEY"),
    ],
)
def test_invalid_config_is_rejected(config: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        OpenAITranscriptionsCompatibleModule({"url": LOCAL_URL, **config})


def test_the_registry_resolves_the_module() -> None:
    cls = resolve_module_class("openai_transcriptions_compatible")
    assert cls is OpenAITranscriptionsCompatibleModule
    assert cls.VAD_GATING == "required"
