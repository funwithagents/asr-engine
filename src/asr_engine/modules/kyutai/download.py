"""Fetch the Kyutai STT weights ahead of time, and check whether they are cached.

    python -m asr_engine.modules.kyutai.download [HF_REPO]

The files are exactly those both backends load: ``config.json`` and the mimi,
model and tokenizer files it names, all from the one Hugging Face repo. Run it in
an environment with a Kyutai extra (see specs/modules/kyutai.md "Installation").
Imports nothing third-party at module level, so ``module.py`` can import it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

DEFAULT_REPO = "kyutai/stt-1b-en_fr-candle"


def weight_files(raw_config: dict[str, Any]) -> list[str]:
    """The files ``config.json`` names, beside itself."""
    return [
        raw_config["mimi_name"],
        raw_config.get("moshi_name", "model.safetensors"),
        raw_config["tokenizer_name"],
    ]


def is_cached(repo: str, *, cache_dir: str | None = None) -> bool:
    """Whether every file the backends load is already in the Hugging Face cache."""
    from huggingface_hub import try_to_load_from_cache

    config_path = try_to_load_from_cache(repo, "config.json", cache_dir=cache_dir)
    if not isinstance(config_path, str):
        return False
    with open(config_path, encoding="utf-8") as f:
        raw_config = json.load(f)
    return all(
        isinstance(try_to_load_from_cache(repo, name, cache_dir=cache_dir), str)
        for name in weight_files(raw_config)
    )


def download(repo: str, *, cache_dir: str | None = None) -> None:
    """Download every file the backends load into the Hugging Face cache."""
    from huggingface_hub import hf_hub_download

    config_path = hf_hub_download(repo, "config.json", cache_dir=cache_dir)
    with open(config_path, encoding="utf-8") as f:
        raw_config = json.load(f)
    for name in weight_files(raw_config):
        hf_hub_download(repo, name, cache_dir=cache_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m asr_engine.modules.kyutai.download",
        description="Download a Kyutai STT model into the Hugging Face cache.",
    )
    parser.add_argument("repo", nargs="?", default=DEFAULT_REPO)
    args = parser.parse_args(argv)
    download(args.repo)
    print(f"{args.repo}: cached")
    return 0


if __name__ == "__main__":
    sys.exit(main())
