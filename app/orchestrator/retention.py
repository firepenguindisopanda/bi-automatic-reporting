"""Deleting what is no longer needed.

Every job left a PDF, a DOCX, its stage rows and two copies of every stage's
output behind it, for ever. Finished jobs untouched for ``RETENTION_DAYS`` are
now removed - rows first, then files - and any report file older than that
which no row points at is swept too, so a crash between the two leaves
nothing behind for long.
"""

import asyncio
import logging
import time
from pathlib import Path

from app.config import settings
from app.orchestrator.job_store import JobStore

logger = logging.getLogger(__name__)

REPORT_SUFFIXES = frozenset({".pdf", ".docx"})


async def sweep(store: JobStore, days: int, report_dir: Path) -> tuple[int, int]:
    """One pass. Returns (rows' report paths released, files deleted)."""
    if days <= 0:
        return 0, 0
    paths = await store.purge_finished_before(days)
    deleted = 0
    for raw in paths:
        deleted += _unlink(Path(raw))

    # Orphans: a report file older than the window that no surviving row needs.
    # A row can outlive its file's age - a report resent last week was created
    # last month - so anything still referenced is kept regardless.
    cutoff = time.time() - days * 86400
    in_use = {_key(Path(p)) for p in await store.report_paths_in_use()}
    if report_dir.is_dir():
        for path in report_dir.iterdir():
            if _key(path) in in_use:
                continue
            if path.suffix.lower() in REPORT_SUFFIXES and path.is_file():
                try:
                    if path.stat().st_mtime < cutoff:
                        deleted += _unlink(path)
                except OSError:
                    continue
    if paths or deleted:
        logger.info("Retention: purged %d job file reference(s), deleted %d file(s)", len(paths), deleted)
    return len(paths), deleted


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


def _unlink(path: Path) -> int:
    try:
        path.unlink()
        return 1
    except FileNotFoundError:
        return 0
    except OSError:
        logger.warning("Retention could not delete %s", path, exc_info=True)
        return 0


async def retention_loop(store: JobStore) -> None:
    """Sweep shortly after startup, then every ``RETENTION_SWEEP_HOURS``."""
    if settings.retention_days <= 0:
        return
    await asyncio.sleep(60)
    while True:
        try:
            await store.init()
            await sweep(store, settings.retention_days, Path(settings.report_output_dir))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - housekeeping must never take the app down
            logger.warning("Retention sweep failed", exc_info=True)
        await asyncio.sleep(max(1, settings.retention_sweep_hours) * 3600)
