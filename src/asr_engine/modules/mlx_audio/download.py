"""Fetch an mlx-audio STT model ahead of time, and check whether it is cached.

    python -m asr_engine.modules.mlx_audio.download [MODEL]

Downloads the files mlx-audio's loader fetches (its ``get_model_path``: the
repository's files matching its patterns), one by one so their bytes can be
measured; the same files land in the Hugging Face cache. Needs the ``mlx-audio`` extra
(Apple Silicon). Imports nothing third-party at module level.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from asr_engine.modules.downloads import Progress, fetch

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


def download(
    model: str,
    *,
    cache_dir: str | None = None,
    on_progress: Progress | None = None,
) -> None:
    """Download the files of *model* mlx-audio's loader fetches (the repository's
    files matching its patterns) into the Hugging Face cache, their bytes reported
    to *on_progress* against their total size."""
    from huggingface_hub import HfApi
    from huggingface_hub.utils import filter_repo_objects
    from mlx_audio.utils import (  # pyright: ignore[reportMissingImports]
        DEFAULT_ALLOW_PATTERNS,
    )

    files = [
        entry.path
        for entry in HfApi().list_repo_tree(model, recursive=True)
        if getattr(entry, "size", None) is not None  # a file, not a folder
    ]
    names = list(filter_repo_objects(files, allow_patterns=DEFAULT_ALLOW_PATTERNS))
    fetch(model, names, on_progress, cache_dir=cache_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m asr_engine.modules.mlx_audio.download",
        description="Download an mlx-audio STT model into the Hugging Face cache.",
    )
    parser.add_argument("model", nargs="?", default=DEFAULT_REPO)
    args = parser.parse_args(argv)
    download(args.model, on_progress=lambda event: print(event["message"]))
    print(f"{args.model}: cached")
    return 0


if __name__ == "__main__":
    sys.exit(main())
