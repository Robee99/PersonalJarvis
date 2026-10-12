"""Wiring: one brain map per data directory, an on-demand run, a periodic tick.

Nothing opens at boot. The server registers a :class:`BrainMapService` (a few
attribute assignments) and a tick task that sleeps first; the database is
opened on the first query or run (AP-26). The tick runs the deterministic
rules only — no model, no provider, so it can never bill a key
(``jarvis/brain/background_policy.py``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .extractor import Extractor
from .indexer import BrainMapIndexer, RunStats
from .sources import SourceReader, default_readers
from .store import BrainMapStore

log = logging.getLogger(__name__)

#: The first tick waits until the app has settled after start.
TICK_INITIAL_DELAY_S = 180.0
#: Then one incremental pass every so often. Unchanged sources cost a few
#: indexed SELECTs and a stat per wiki page.
TICK_INTERVAL_S = 900.0


class BrainMapBusy(RuntimeError):
    """A run is already in progress."""


class BrainMapService:
    def __init__(
        self,
        *,
        data_dir: Path,
        vault_root: Callable[[], Path | None] | Path | None = None,
        outputs_roots: Callable[[], Sequence[Path]] | Sequence[Path] = (),
        readers: Sequence[SourceReader] | None = None,
        extractor: Extractor | None = None,
        clock: Callable[[], float] = time.time,
        db_path: Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = Path(db_path) if db_path is not None else self.data_dir / "jarvis.db"
        self._vault_root = vault_root
        self._outputs_roots = outputs_roots
        self._readers = readers
        self._extractor = extractor
        self._clock = clock
        self._lock = threading.Lock()
        self._store: BrainMapStore | None = None
        self._indexer: BrainMapIndexer | None = None

    def _make_readers(self) -> Sequence[SourceReader]:
        if self._readers is not None:
            return self._readers
        vault = self._vault_root() if callable(self._vault_root) else self._vault_root
        roots = self._outputs_roots() if callable(self._outputs_roots) else self._outputs_roots
        return default_readers(data_dir=self.data_dir, vault_root=vault, outputs_roots=tuple(roots))

    @property
    def store(self) -> BrainMapStore:
        with self._lock:
            if self._store is None:
                self._store = BrainMapStore(self.db_path)
            return self._store

    @property
    def indexer(self) -> BrainMapIndexer:
        store = self.store
        with self._lock:
            if self._indexer is None:
                self._indexer = BrainMapIndexer(
                    store, self._make_readers, extractor=self._extractor, clock=self._clock
                )
            return self._indexer

    def reindex(self, *, trigger: str = "manual", use_model: bool = False) -> RunStats:
        indexer = self.indexer
        if indexer.busy:
            raise BrainMapBusy("a brain map run is already in progress")
        return indexer.run(trigger=trigger, use_model=use_model)

    def last_run(self) -> dict[str, Any] | None:
        with self.store.read() as conn:
            row = conn.execute(
                "SELECT * FROM brain_map_runs WHERE finished_ms > 0 ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
            self._store = None
            self._indexer = None


def service_from_config(
    cfg: Any, *, outputs_roots: Callable[[], Sequence[Path]] | Sequence[Path] = ()
) -> BrainMapService:
    """The production service for one app config. Cheap: opens nothing."""
    from jarvis.memory.wiki.db_path import resolve_wiki_db_path

    db_path = resolve_wiki_db_path(getattr(getattr(cfg, "memory", None), "data_dir", None))

    def vault() -> Path | None:
        wiki = getattr(cfg, "wiki_integration", None)
        if wiki is None or not getattr(wiki, "enabled", False):
            return None
        from jarvis.memory.wiki.vault_root import resolve_vault_root

        root = resolve_vault_root(getattr(wiki, "vault_root", None)).path
        return root if root.is_dir() else None

    return BrainMapService(
        data_dir=db_path.parent, db_path=db_path, vault_root=vault, outputs_roots=outputs_roots
    )


# ---------------------------------------------------------------------------
# the process-wide instance (REST routes and MCP tools share it)
# ---------------------------------------------------------------------------

_shared: BrainMapService | None = None
_shared_lock = threading.Lock()


def set_shared_service(service: BrainMapService | None) -> None:
    global _shared
    with _shared_lock:
        _shared = service


def shared_service() -> BrainMapService:
    """The registered service, or one over the default data directory."""
    global _shared
    with _shared_lock:
        if _shared is None:
            from jarvis.core import config as cfg

            _shared = BrainMapService(data_dir=Path(cfg.DATA_DIR))
        return _shared


async def tick_loop(
    service: BrainMapService,
    *,
    initial_delay_s: float = TICK_INITIAL_DELAY_S,
    interval_s: float = TICK_INTERVAL_S,
) -> None:
    """Deterministic incremental passes forever; cancelled on shutdown."""
    await asyncio.sleep(initial_delay_s)
    while True:
        try:
            await asyncio.to_thread(service.reindex, trigger="tick", use_model=False)
        except BrainMapBusy:  # the running pass covers this tick
            log.debug("brain map tick skipped: a run is in progress")
        except Exception:  # noqa: BLE001 — a failed pass waits for the next tick, never ends the loop
            log.warning("brain map tick failed", exc_info=True)
        await asyncio.sleep(interval_s)


__all__ = [
    "TICK_INITIAL_DELAY_S",
    "TICK_INTERVAL_S",
    "BrainMapBusy",
    "BrainMapService",
    "service_from_config",
    "set_shared_service",
    "shared_service",
    "tick_loop",
]
