"""MLX backend for the ``kyutai`` module (Apple Silicon) — over ``moshi_mlx``.

Ported from upstream's ``stt_from_mic_mlx.py`` (kyutai-labs/delayed-streams-modeling).
Needs the ``kyutai-mlx`` extra. Only ``modules/kyutai.py`` imports this file, and
only once this backend is selected. See specs/kyutai-module.md.
"""

# moshi_mlx / mlx publish no Linux wheels, so these imports don't resolve there;
# where they do, moshi_mlx ships py.typed without re-exporting its public names and
# sentencepiece/rustymimi's stubs reject the documented constructor arguments.
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
# pyright: reportPrivateImportUsage=false, reportCallIssue=false

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

import mlx.core as mx
import mlx.nn as nn
import numpy as np
import rustymimi
import sentencepiece
from huggingface_hub import hf_hub_download
from moshi_mlx import models, utils

from asr_engine.modules.kyutai import StepResult

log = logging.getLogger(__name__)

_PADDING_TOKENS = (0, 3)
# Index of the end-of-turn head among the model's extra (semantic VAD) heads;
# the others predict pauses of other lengths. Matches upstream's scripts.
_END_OF_TURN_HEAD = 2


class MlxKyutaiBackend:
    """One Kyutai STT model resident in MLX. Not thread-safe: the module makes
    every call from its single backend thread."""

    SAMPLE_RATE: ClassVar[int] = 24000
    FRAME_SAMPLES: ClassVar[int] = 1920

    def __init__(self, *, hf_repo: str, vad: bool, max_steps: int) -> None:
        self._hf_repo = hf_repo
        self._want_vad = vad
        self._max_steps = max_steps
        self._has_vad = False
        self._audio_delay_s = 0.0
        self._silence_prefix_s = 0.0
        self._other_codebooks = 0
        self._model: Any = None
        self._gen: Any = None
        self._text_tokenizer: Any = None
        self._audio_tokenizer: Any = None

    @property
    def has_vad(self) -> bool:
        return self._has_vad

    @property
    def steps_remaining(self) -> int | None:
        return self._max_steps - self._gen.step_idx

    @property
    def audio_delay_s(self) -> float:
        return self._audio_delay_s

    @property
    def silence_prefix_s(self) -> float:
        return self._silence_prefix_s

    def load(self) -> None:
        with open(hf_hub_download(self._hf_repo, "config.json")) as f:
            raw_config = json.load(f)
        mimi_weights = hf_hub_download(self._hf_repo, raw_config["mimi_name"])
        moshi_weights = hf_hub_download(
            self._hf_repo, raw_config.get("moshi_name", "model.safetensors")
        )
        tokenizer_path = hf_hub_download(self._hf_repo, raw_config["tokenizer_name"])

        lm_config = models.LmConfig.from_config_dict(raw_config)
        model = models.Lm(lm_config)
        model.set_dtype(mx.bfloat16)
        if moshi_weights.endswith(".q4.safetensors"):
            nn.quantize(model, bits=4, group_size=32)
        elif moshi_weights.endswith(".q8.safetensors"):
            nn.quantize(model, bits=8, group_size=64)
        if self._hf_repo.endswith("-candle"):
            # The -candle repos hold PyTorch-layout weights — and are the only
            # ones carrying the VAD heads.
            model.load_pytorch_weights(moshi_weights, lm_config, strict=True)
        else:
            model.load_weights(moshi_weights, strict=True)

        self._text_tokenizer = sentencepiece.SentencePieceProcessor(tokenizer_path)
        self._other_codebooks = lm_config.other_codebooks
        mimi_codebooks = max(lm_config.generated_codebooks, self._other_codebooks)
        self._audio_tokenizer = rustymimi.Tokenizer(
            mimi_weights, num_codebooks=mimi_codebooks
        )
        model.warmup()
        self._model = model

        stt_config = raw_config.get("stt_config", {})
        self._audio_delay_s = float(stt_config.get("audio_delay_seconds", 0.0))
        self._silence_prefix_s = float(
            stt_config.get("audio_silence_prefix_seconds", 0.0)
        )
        self._has_vad = self._want_vad and lm_config.extra_heads_num_heads > 0
        self._gen = self._new_generator()
        log.info("Kyutai MLX model %s loaded (VAD: %s)", self._hf_repo, self._has_vad)

    def _new_generator(self) -> Any:
        return models.LmGen(
            model=self._model,
            max_steps=self._max_steps,
            text_sampler=utils.Sampler(top_k=25, temp=0),
            audio_sampler=utils.Sampler(top_k=250, temp=0.8),
            check=False,
        )

    def step(self, frame: np.ndarray) -> StepResult:
        tokens = self._audio_tokenizer.encode_step(frame[None, None, :])
        tokens = mx.array(tokens).transpose(0, 2, 1)[:, :, : self._other_codebooks]
        end_of_turn_p: float | None = None
        if self._has_vad:
            text_token, vad_heads = self._gen.step_with_extra_heads(tokens[0])
            end_of_turn_p = float(vad_heads[_END_OF_TURN_HEAD][0, 0, 0].item())
        else:
            text_token = self._gen.step(tokens[0])
        token_id = int(text_token[0].item())
        if token_id in _PADDING_TOKENS:
            return StepResult(None, end_of_turn_p)
        piece = self._text_tokenizer.id_to_piece(token_id).replace("▁", " ")
        return StepResult(piece, end_of_turn_p)

    def reset(self) -> None:
        if self._model is None:
            return
        for cache in self._model.transformer_cache:
            cache.reset()
        self._audio_tokenizer.reset()
        self._gen = self._new_generator()

    def close(self) -> None:
        self._model = self._gen = None
        self._text_tokenizer = self._audio_tokenizer = None
