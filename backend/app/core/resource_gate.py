"""Low-memory server concurrency guards (1.6 GiB / 2 GB cloud host protection, Swap: 0B invariant).

Caps concurrent heavy PDF/ZIP document builds and serializes background batch jobs so burst user
traffic never triggers OOM or swap thrashing on memory-constrained production hosts.
"""

from __future__ import annotations

import asyncio
import gc
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager

_MAX_CONCURRENT_PDF_RENDERS = max(1, int(os.environ.get("CYSH_MAX_PDF_CONCURRENCY", "2")))
_PDF_RENDER_SEM = threading.BoundedSemaphore(_MAX_CONCURRENT_PDF_RENDERS)
_BG_HEAVY_LOCK = asyncio.Semaphore(1)


@contextmanager
def pdf_render_slot() -> Generator[None, None, None]:
    """Limit concurrent in-memory PDF layout/font shaping threads and reclaim transient buffers."""
    _PDF_RENDER_SEM.acquire()
    try:
        yield
    finally:
        _PDF_RENDER_SEM.release()
        gc.collect(0)


def background_job_gate() -> asyncio.Semaphore:
    """Single-concurrency gate for background briefing rebuilds and scheduled crawls."""
    return _BG_HEAVY_LOCK
