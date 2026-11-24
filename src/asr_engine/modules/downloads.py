"""Downloading model files with their progress measured (specs/setup.md "Progress").

``ByteProgress`` turns the bytes received against the bytes expected into the
setup contract's progress events, ``{"fraction", "message"}``; the Silero VAD's
download feeds it directly.

``fetch`` downloads a list of a repository's files into the Hugging Face cache,
one by one with ``hf_hub_download`` (the call the modules' loaders make, so the
same files land in the same place), and reports the bytes received against the
total the Hub gives for those files.

How the bytes are measured depends on the installed ``huggingface_hub``: one whose
``hf_hub_download`` takes ``tqdm_class`` reports every chunk written through it;
an older one (the Kyutai MLX extra pins one) has no hook, and the size of the
download's ``.incomplete`` file in the cache is read twice a second instead.

Imports ``huggingface_hub`` only when called, so a module can import this file on
the base install.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any

Progress = Callable[[dict[str, Any]], None]

_POLL_S = 0.5
_MB = 1_000_000


def megabytes(n: float) -> str:
    """A size in megabytes, in a few digits: "0.4", "12", "1093"."""
    mb = n / _MB
    return f"{mb:.1f}" if mb < 10 else f"{mb:.0f}"


class ByteProgress:
    """The bytes of one item received so far against the total expected, reported
    as progress events. Thread-safe; reports at most once per percent."""

    def __init__(self, on_progress: Progress | None, what: str, total: int | None):
        self._on_progress = on_progress
        self._what = what
        self._total = total if total else None
        self._done = 0
        self._partial = 0
        self._reported = -1.0
        self._lock = threading.Lock()

    def start(self) -> None:
        self._emit(0.0 if self._total else None, f"Downloading {self._what}")

    def add(self, n: int) -> None:
        """*n* more bytes received (negative when a retry starts over)."""
        with self._lock:
            self._done += n
        self._report()

    def set_partial(self, n: int) -> None:
        """The bytes of the file being downloaded so far, when read from its size."""
        with self._lock:
            self._partial = n
        self._report()

    def finish(self) -> None:
        self._emit(1.0, f"{self._what} is ready")

    def _report(self) -> None:
        if self._total is None:
            return
        with self._lock:
            received = max(0, min(self._done + self._partial, self._total))
            fraction = received / self._total
            if fraction < 1.0 and fraction - self._reported < 0.01:
                return
            self._reported = fraction
        self._emit(
            min(fraction, 0.99),
            f"Downloading {megabytes(received)} of {megabytes(self._total)} MB",
        )

    def _emit(self, fraction: float | None, message: str) -> None:
        if self._on_progress is not None:
            self._on_progress({"fraction": fraction, "message": message})


def _counting_bar(progress: ByteProgress) -> type:
    """A stand-in for ``tqdm`` that adds every byte written to *progress* and draws
    nothing. Having ``update_transfer`` makes ``huggingface_hub`` use this one bar
    per file for both its network and its disk counts; only ``update`` (the bytes
    written) is counted, so a file is counted once."""

    class _Bar:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.total = kwargs.get("total")
            self.n = kwargs.get("initial", 0) or 0
            if self.n:
                progress.add(int(self.n))

        def __enter__(self) -> _Bar:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def update(self, n: float | None = 1) -> None:
            self.n += n or 0
            progress.add(int(n or 0))

        def update_transfer(self, n: float | None = 1) -> None:
            return None

        def __getattr__(self, name: str) -> Callable[..., None]:
            # refresh, close, set_postfix_str, set_description…: nothing to draw.
            return lambda *args, **kwargs: None

    return _Bar


def _takes_a_bar(download: Callable[..., Any]) -> bool:
    try:
        return "tqdm_class" in inspect.signature(download).parameters
    except (TypeError, ValueError):
        return False


def _incomplete_bytes(blobs: Path) -> int:
    """The bytes of the downloads in progress in a repository's cache folder."""
    total = 0
    try:
        for path in blobs.glob("*.incomplete"):
            try:
                total += path.stat().st_size
            except OSError:
                pass  # finished and renamed meanwhile
    except OSError:
        pass
    return total


def file_sizes(repo: str, names: Sequence[str]) -> dict[str, int]:
    """The size of each of *repo*'s files *names*, as the Hub lists them."""
    from huggingface_hub import HfApi

    return {
        info.path: int(size)
        for info in HfApi().get_paths_info(repo, list(names))
        if (size := getattr(info, "size", None)) is not None
    }


def fetch(
    repo: str,
    names: Sequence[str],
    on_progress: Progress | None,
    *,
    what: str | None = None,
    cache_dir: str | None = None,
) -> None:
    """Download *repo*'s files *names* into the Hugging Face cache, reporting the
    bytes received against their total size. A file already cached counts as
    received. Raises what ``huggingface_hub`` raises."""
    from huggingface_hub import constants, hf_hub_download, try_to_load_from_cache

    sizes = file_sizes(repo, names)
    total = sum(sizes.get(name, 0) for name in names) if sizes else None
    progress = ByteProgress(on_progress, what or repo, total)
    progress.start()
    bar = _counting_bar(progress) if _takes_a_bar(hf_hub_download) else None
    blobs = (
        Path(cache_dir or constants.HF_HUB_CACHE)
        / f"models--{repo.replace('/', '--')}"
        / "blobs"
    )
    missing = [
        name
        for name in names
        if not isinstance(try_to_load_from_cache(repo, name, cache_dir=cache_dir), str)
    ]
    # What is already in the cache counts as received, before the rest arrives.
    if cached := sum(sizes.get(name, 0) for name in names if name not in missing):
        progress.add(cached)
    for name in missing:
        if bar is not None:
            hf_hub_download(repo, name, cache_dir=cache_dir, tqdm_class=bar)
        else:
            _watch(
                blobs,
                progress,
                partial(hf_hub_download, repo, name, cache_dir=cache_dir),
            )
            progress.add(sizes.get(name, 0))  # complete: its bytes, counted once
    progress.finish()


def _watch(blobs: Path, progress: ByteProgress, download: Callable[[], Any]) -> None:
    """Run *download*, reading the size of its ``.incomplete`` file meanwhile."""
    stop = threading.Event()

    def poll() -> None:
        while not stop.wait(_POLL_S):
            progress.set_partial(_incomplete_bytes(blobs))

    watcher = threading.Thread(target=poll, daemon=True)
    watcher.start()
    try:
        download()
    finally:
        stop.set()
        watcher.join()
        progress.set_partial(0)
