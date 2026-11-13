"""Fetch an mlx-audio STT model ahead of time, and check whether it is cached.

    python -m asr_engine.modules.mlx_audio.download [MODEL]

Downloads through mlx-audio's own ``get_model_path``, the call its loader makes,
so the same files land in the Hugging Face cache. Needs the ``mlx-audio`` extra
(Apple Silicon). Imports nothing third-party at module level.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_REPO = "mlx-community/parakeet-tdt-0.6b-v3"


def is_cached(model: str, *, cache_dir: str | None = None) -> bool:
    """Whether *model* is a local path, or a repo whose cached snapshot holds a
    config and weights.

    Looks at the cache directly: mlx-audio downloads only some file patterns, and
    ``snapshot_download(local_files_only=True)`` treats such a snapshot as
    incomplete.
    """
    if Path(model).expanduser().exists():
        return True
    from huggingface_hub import try_to_load_from_cache

    config_path = try_to_load_from_cache(model, "config.json", cache_dir=cache_dir)
    if not isinstance(config_path, str):
        return False
    return any(Path(config_path).parent.glob("*.safetensors"))


def download(model: str) -> None:
    """Download *model* the way mlx-audio's loader does."""
    from mlx_audio.utils import get_model_path  # pyright: ignore[reportMissingImports]

    get_model_path(model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m asr_engine.modules.mlx_audio.download",
        description="Download an mlx-audio STT model into the Hugging Face cache.",
    )
    parser.add_argument("model", nargs="?", default=DEFAULT_REPO)
    args = parser.parse_args(argv)
    download(args.model)
    print(f"{args.model}: cached")
    return 0


if __name__ == "__main__":
    sys.exit(main())
