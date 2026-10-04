"""Load the vosk package once per process, and back off a model that will not load.

Two failure modes made a broken vosk install burn CPU and wreck the process
environment (installed Windows app, 2026-10):

* ``vosk/__init__.py`` runs ``os.environ["PATH"] = dlldir + os.pathsep +
  os.environ["PATH"]`` on Windows every time it EXECUTES, and a failed import
  is not cached in ``sys.modules`` — so every ``from vosk import Model`` after
  a failure re-ran the module and prepended the directory again. The frozen
  build had no ``_internal\\vosk`` folder (its native ``libvosk.dll`` was not
  collected), the detect loop retried on every ~30 ms audio chunk, and PATH
  grew until Windows refused it ("the environment variable is longer than
  32767 characters"), which then broke every later subprocess too.
* The detect loop retried a model that failed to load on every chunk and
  logged a warning each time (4,353 lines a day).

:class:`VoskRuntime` imports vosk at most once per process: the DLL directory is
registered with ``os.add_dll_directory`` once, PATH is restored after the
import so it holds the vosk directory at most once, and a failed import is
remembered instead of re-executed. :class:`LoadBackoff` spaces out retries of a
model that failed (exponential, jittered, capped) and logs once per state
change. :meth:`VoskRuntime.available` is a cheap, import-free probe — package
AND native library on disk — so the wake plan can pick another engine honestly
instead of arming a detector that can never load.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import random
import sys
import threading
import time
from collections.abc import Callable, MutableMapping
from pathlib import Path
from types import ModuleType
from typing import Any

log = logging.getLogger("jarvis.wake.vosk")

# The file names vosk's own ``open_dll`` loads, per platform (sic: "dyld").
_NATIVE_LIBRARY = {
    "win32": "libvosk.dll",
    "linux": "libvosk.so",
    "darwin": "libvosk.dyld",
}


class VoskUnavailable(RuntimeError):
    """vosk, its native library, or one model cannot be used right now."""


def native_library_name(platform: str | None = None) -> str | None:
    """The native library vosk loads on ``platform`` (None: unsupported)."""
    return _NATIVE_LIBRARY.get(platform or sys.platform)


class VoskRuntime:
    """Process-wide, idempotent access to the ``vosk`` module.

    Every dependency is injectable so tests drive the Windows path with fakes
    on any OS; the module-level default uses the real ones.
    """

    def __init__(
        self,
        module_name: str = "vosk",
        *,
        platform: str | None = None,
        environ: MutableMapping[str, str] | None = None,
        add_dll_directory: Callable[[str], Any] | None = None,
        find_spec: Callable[[str], Any] = importlib.util.find_spec,
        import_module: Callable[[str], ModuleType] = importlib.import_module,
    ) -> None:
        self._module_name = module_name
        self._platform = platform
        self._environ = os.environ if environ is None else environ
        self._add_dll_directory = (
            getattr(os, "add_dll_directory", None)
            if add_dll_directory is None
            else add_dll_directory
        )
        self._find_spec = find_spec
        self._import_module = import_module
        self._lock = threading.Lock()
        self._failure: BaseException | None = None
        # Directories already registered, and the handles that keep them
        # registered (closing a handle removes the directory again).
        self._dll_dirs: set[str] = set()
        self._dll_handles: list[Any] = []
        self._logged_reasons: set[str] = set()
        self.import_attempts = 0

    @property
    def platform(self) -> str:
        return self._platform or sys.platform

    def package_dir(self) -> Path | None:
        """Where the package lives, found WITHOUT importing it."""
        try:
            spec = self._find_spec(self._module_name)
        except (ImportError, ValueError) as exc:
            # ValueError: a module object without __spec__ (a stand-in that is
            # already imported). Nothing on disk to point at, which is fine.
            log.debug("vosk-runtime: no spec for %s (%s).", self._module_name, exc)
            return None
        if spec is None:
            return None
        locations = list(getattr(spec, "submodule_search_locations", None) or ())
        if locations:
            return Path(locations[0])
        origin = getattr(spec, "origin", None)
        return Path(origin).parent if origin else None

    def available(self) -> bool:
        """True when vosk can plausibly load: package and native library on disk.

        Import-free (the real import pulls requests/tqdm/srt and the native
        library), so it is cheap enough for the wake-plan resolver. A frozen
        build that bundled vosk's Python modules but not its native folder
        answers False here, and the wake plan falls through to another engine.
        """
        if self._failure is not None:
            return False
        package = self.package_dir()
        if package is None:
            # An already-imported module with no location on disk is a
            # stand-in (tests) or a vendored copy: it IS loaded, so usable.
            return self._module_name in sys.modules
        library = native_library_name(self.platform)
        if library is None:
            self._note_unavailable(f"no native vosk library for {self.platform}")
            return False
        if not (package / library).is_file():
            self._note_unavailable(f"native library {package / library} is missing")
            return False
        return True

    def load(self) -> ModuleType:
        """Return the vosk module, importing it at most once per process.

        Raises :class:`VoskUnavailable` (chained to the original error) when the
        import failed, now or earlier; a failed import is never re-executed.
        """
        existing = sys.modules.get(self._module_name)
        if existing is not None:
            return existing
        with self._lock:
            existing = sys.modules.get(self._module_name)
            if existing is not None:
                return existing
            if self._failure is not None:
                raise VoskUnavailable(
                    f"vosk could not be imported earlier: {self._failure}"
                ) from self._failure
            package = self.package_dir()
            self._register_dll_directory(package)
            saved_path = self._environ.get("PATH")
            self.import_attempts += 1
            try:
                module = self._import_module(self._module_name)
            except Exception as exc:  # noqa: BLE001 — remembered, logged, re-raised
                self._failure = exc
                self._restore_path(saved_path, keep_added=False)
                self._note_unavailable(f"import failed: {exc}")
                raise VoskUnavailable(f"vosk could not be imported: {exc}") from exc
            self._restore_path(saved_path, keep_added=True)
            return module

    def _register_dll_directory(self, package: Path | None) -> None:
        """``os.add_dll_directory`` for the vosk folder, once per process."""
        if self.platform != "win32" or self._add_dll_directory is None or package is None:
            return
        key = str(package)
        if key in self._dll_dirs or not package.is_dir():
            return
        try:
            self._dll_handles.append(self._add_dll_directory(key))
        except OSError as exc:
            log.debug("vosk-runtime: add_dll_directory(%s) failed: %s", key, exc)
            return
        self._dll_dirs.add(key)

    def _restore_path(self, saved: str | None, *, keep_added: bool) -> None:
        """Undo vosk's unconditional PATH prepend, keeping each new entry once."""
        if saved is None:
            return
        current = self._environ.get("PATH", "")
        if current == saved:
            return
        sep = os.pathsep
        before = saved.split(sep) if saved else []
        known = {entry.casefold() for entry in before}
        added: list[str] = []
        if keep_added:
            for entry in current.split(sep):
                folded = entry.casefold()
                if entry and folded not in known:
                    known.add(folded)
                    added.append(entry)
        self._environ["PATH"] = sep.join(added + before) if added else saved

    def _note_unavailable(self, reason: str) -> None:
        if reason in self._logged_reasons:
            return
        self._logged_reasons.add(reason)
        log.warning(
            "vosk-kws: vosk is unavailable (%s); the wake word uses another engine.",
            reason,
        )


class LoadBackoff:
    """Exponential, jittered, capped retry spacing per key, logged per state change.

    The failure counter resets when the key loads (AP-19: per unit of work), so
    a model that recovers starts fresh if it ever breaks again.
    """

    def __init__(
        self,
        *,
        base_s: float = 2.0,
        cap_s: float = 300.0,
        jitter: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
        logger: logging.Logger = log,
    ) -> None:
        self._base_s = float(base_s)
        self._cap_s = float(cap_s)
        self._jitter = max(0.0, min(1.0, float(jitter)))
        self._clock = clock
        self._rand = rand
        self._log = logger
        self._lock = threading.Lock()
        # key -> (consecutive failures, monotonic time the next try is allowed)
        self._state: dict[str, tuple[int, float]] = {}

    def remaining(self, key: str) -> float:
        """Seconds until ``key`` may be tried again (0.0: try now)."""
        with self._lock:
            entry = self._state.get(key)
        if entry is None:
            return 0.0
        return max(0.0, entry[1] - self._clock())

    def failures(self, key: str) -> int:
        with self._lock:
            entry = self._state.get(key)
        return 0 if entry is None else entry[0]

    def check(self, key: str) -> None:
        """Raise :class:`VoskUnavailable` while ``key`` is backing off."""
        wait = self.remaining(key)
        if wait > 0.0:
            raise VoskUnavailable(
                f"model {key} is unusable; next retry in {wait:.0f} s"
            )

    def failed(self, key: str, exc: BaseException) -> float:
        """Record a failure; return the delay before the next try."""
        with self._lock:
            count = self._state.get(key, (0, 0.0))[0] + 1
            delay = min(self._cap_s, self._base_s * (2 ** min(count - 1, 30)))
            delay *= 1.0 + self._jitter * (2.0 * self._rand() - 1.0)
            delay = max(0.0, min(self._cap_s, delay))
            self._state[key] = (count, self._clock() + delay)
        if count == 1:
            self._log.warning(
                "vosk-kws: model %s unusable (%s); retrying with backoff "
                "(next in %.0f s, at most every %.0f s).",
                key, exc, delay, self._cap_s,
            )
        else:
            self._log.debug(
                "vosk-kws: model %s still unusable after %d attempts (%s); next in %.0f s.",
                key, count, exc, delay,
            )
        return delay

    def succeeded(self, key: str) -> None:
        with self._lock:
            entry = self._state.pop(key, None)
        if entry is not None:
            self._log.info(
                "vosk-kws: model %s usable again after %d failed attempt(s).",
                key, entry[0],
            )

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


_DEFAULT_RUNTIME = VoskRuntime()


def load_vosk() -> ModuleType:
    """The process-wide vosk module (see :meth:`VoskRuntime.load`)."""
    return _DEFAULT_RUNTIME.load()


def vosk_runtime_available() -> bool:
    """Cheap probe: can vosk plausibly load here (see :meth:`VoskRuntime.available`)."""
    return _DEFAULT_RUNTIME.available()


__all__ = [
    "LoadBackoff",
    "VoskRuntime",
    "VoskUnavailable",
    "load_vosk",
    "native_library_name",
    "vosk_runtime_available",
]
