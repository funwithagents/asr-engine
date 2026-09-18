"""PyTorch backend for the ``kyutai`` module (Linux/CUDA, or anywhere without MLX)
— over ``moshi``.

Ported from upstream's ``stt_from_file_pytorch.py`` (kyutai-labs/delayed-streams-modeling).
Needs the ``kyutai-torch`` extra. Only ``module.py`` imports this file, and only
once this backend is selected. See specs/modules/kyutai.md.

UNVERIFIED: written from the upstream reference script without a Linux + CUDA
machine to run it on (the fast tier covers the module through a scripted backend,
and the e2e tier ran on the MLX backend only). It needs a real run by a user of
this backend — see the spec's open questions.
"""

# `kyutai-torch` is deliberately not in the dev group (~2.5 GB of torch), so these
# imports don't resolve in a default contributor environment.
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from __future__ import annotations

import logging
from contextlib import ExitStack
from typing import Any, ClassVar

import numpy as np
import torch
from moshi.models import LMGen, loaders

from asr_engine.modules.kyutai.backend import StepResult

log = logging.getLogger(__name__)

_PADDING_TOKENS = (0, 3)
# Index of the end-of-turn head among the model's extra (semantic VAD) heads.
_END_OF_TURN_HEAD = 2


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class TorchKyutaiBackend:
    """One Kyutai STT model resident in PyTorch. Not thread-safe: the module makes
    every call from its single backend thread."""

    SAMPLE_RATE: ClassVar[int] = 24000
    FRAME_SAMPLES: ClassVar[int] = 1920

    def __init__(self, *, hf_repo: str, vad: bool, max_steps: int, device: str) -> None:
        self._hf_repo = hf_repo
        self._want_vad = vad
        # PyTorch's LMGen has no max_steps wall: the budget is unbounded here.
        del max_steps
        self._device_name = device
        self._device = "cpu"
        self._has_vad = False
        self._audio_delay_s = 0.0
        self._silence_prefix_s = 0.0
        self._mimi: Any = None
        self._lm_gen: Any = None
        self._text_tokenizer: Any = None
        self._streaming: ExitStack | None = None

    @property
    def has_vad(self) -> bool:
        return self._has_vad

    @property
    def steps_remaining(self) -> int | None:
        return None

    @property
    def audio_delay_s(self) -> float:
        return self._audio_delay_s

    @property
    def silence_prefix_s(self) -> float:
        return self._silence_prefix_s

    def load(self) -> None:
        self._device = _resolve_device(self._device_name)
        info = loaders.CheckpointInfo.from_hf_repo(self._hf_repo)
        self._mimi = info.get_mimi(device=self._device)
        self._text_tokenizer = info.get_text_tokenizer()
        lm = info.get_moshi(device=self._device, dtype=torch.bfloat16)
        self._lm_gen = LMGen(lm, temp=0, temp_text=0.0)

        stt_config = info.stt_config or {}
        self._audio_delay_s = float(stt_config.get("audio_delay_seconds", 0.0))
        self._silence_prefix_s = float(
            stt_config.get("audio_silence_prefix_seconds", 0.0)
        )
        has_heads = info.raw_config.get("extra_heads_num_heads", 0) > 0
        self._has_vad = self._want_vad and has_heads
        self._enter_streaming()
        # Warm up (CUDA graphs / kernel compilation), then start clean.
        self.step(np.zeros(self.FRAME_SAMPLES, dtype=np.float32))
        self.reset()
        log.info(
            "Kyutai PyTorch model %s loaded on %s (VAD: %s)",
            self._hf_repo,
            self._device,
            self._has_vad,
        )

    def _enter_streaming(self) -> None:
        self._streaming = ExitStack()
        self._streaming.enter_context(self._mimi.streaming(1))
        self._streaming.enter_context(self._lm_gen.streaming(1))

    def step(self, frame: np.ndarray) -> StepResult:
        with torch.no_grad():
            chunk = torch.from_numpy(frame).to(self._device)[None, None, :]
            audio_tokens = self._mimi.encode(chunk)
            end_of_turn_p: float | None = None
            if self._has_vad:
                out = self._lm_gen.step_with_extra_heads(audio_tokens)
                if out is None:
                    return StepResult(None, None)
                text_tokens, vad_heads = out
                if vad_heads:
                    end_of_turn_p = float(
                        vad_heads[_END_OF_TURN_HEAD][0, 0, 0].cpu().item()
                    )
            else:
                text_tokens = self._lm_gen.step(audio_tokens)
                if text_tokens is None:
                    return StepResult(None, None)
            token_id = int(text_tokens[0, 0, 0].cpu().item())
        if token_id in _PADDING_TOKENS:
            return StepResult(None, end_of_turn_p)
        piece = self._text_tokenizer.id_to_piece(token_id).replace("▁", " ")
        return StepResult(piece, end_of_turn_p)

    def reset(self) -> None:
        if self._streaming is None:
            return
        self._streaming.close()
        self._enter_streaming()

    def close(self) -> None:
        if self._streaming is not None:
            self._streaming.close()
            self._streaming = None
        self._mimi = self._lm_gen = self._text_tokenizer = None
