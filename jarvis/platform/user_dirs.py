"""The user's own folders — Downloads and Pictures — the way each OS names them.

- **Windows** asks the known-folder API (``SHGetKnownFolderPath``), so a
  Downloads or Pictures folder moved to another drive or into OneDrive is the
  one used.
- **macOS** uses ``~/Downloads`` and ``~/Pictures``.
- **Linux** reads ``XDG_DOWNLOAD_DIR`` / ``XDG_PICTURES_DIR`` from
  ``user-dirs.dirs``: on a Spanish desktop Downloads is ``~/Descargas``, on a
  French one ``~/Téléchargements``. Writing to a hard-coded ``~/Downloads``
  there created a stray folder nobody looks in.

Only stdlib at module scope; the Windows path uses ctypes lazily.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

log = logging.getLogger(__name__)

#: FOLDERID_Downloads {374DE290-123F-4565-9164-39C4925E467B}.
_DOWNLOADS_ID = (0x374DE290, 0x123F, 0x4565, (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
#: FOLDERID_Pictures {33E28130-4E1E-4676-835A-98395C3BC3BB}.
_PICTURES_ID = (0x33E28130, 0x4E1E, 0x4676, (0x83, 0x5A, 0x98, 0x39, 0x5C, 0x3B, 0xC3, 0xBB))
#: FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}.
_DESKTOP_ID = (0xB4BFCC3A, 0xDB2C, 0x424C, (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
#: FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}.
_DOCUMENTS_ID = (0xFDD39AD0, 0x238F, 0x46AF, (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))


def downloads_dir() -> Path:
    """The user's Downloads folder. Never ``None``: ``~/Downloads`` is the floor."""
    fallback = Path.home() / "Downloads"
    try:
        if sys.platform == "win32":
            return windows_known_folder(_DOWNLOADS_ID) or fallback
        if sys.platform == "darwin":
            return fallback
        return xdg_user_dir("DOWNLOAD") or fallback
    except Exception:  # noqa: BLE001 - a probe failure means "use the plain folder"
        log.debug("user-dirs: Downloads folder probe failed", exc_info=True)
        return fallback


def pictures_dir() -> Path | None:
    """The user's Pictures folder path, or ``None`` when Linux points it at home."""
    if sys.platform == "win32":
        return windows_known_folder(_PICTURES_ID) or Path.home() / "Pictures"
    if sys.platform == "darwin":
        return Path.home() / "Pictures"
    found = xdg_user_dir("PICTURES")
    if found is None and not _xdg_names_home("PICTURES"):
        return Path.home() / "Pictures"
    return found


def _known(windows_id: tuple[int, int, int, tuple[int, ...]], xdg: str, plain: str) -> Path:
    fallback = Path.home() / plain
    try:
        if sys.platform == "win32":
            return windows_known_folder(windows_id) or fallback
        if sys.platform == "darwin":
            return fallback
        return xdg_user_dir(xdg) or fallback
    except Exception:  # noqa: BLE001 - a probe failure means "use the plain folder"
        log.debug("user-dirs: %s folder probe failed", plain, exc_info=True)
        return fallback


def desktop_dir() -> Path:
    """The user's Desktop folder, also when OneDrive moved it."""
    return _known(_DESKTOP_ID, "DESKTOP", "Desktop")


def documents_dir() -> Path:
    """The user's Documents folder, also when OneDrive moved it."""
    return _known(_DOCUMENTS_ID, "DOCUMENTS", "Documents")


def named_folders() -> dict[str, Path]:
    """The folders people name by word: "my Downloads", "the desktop".

    Live 2026-10-25 "List the files in my Downloads folder" reached the
    filesystem server as the relative path "Downloads", which it read under
    its own start folder (OneDrive\\Desktop\\Downloads) and found empty.
    """
    folders = {
        "Downloads": downloads_dir(),
        "Desktop": desktop_dir(),
        "Documents": documents_dir(),
    }
    pictures = pictures_dir()
    if pictures is not None:
        folders["Pictures"] = pictures
    return folders


def xdg_user_dir(name: str) -> Path | None:
    """``XDG_<name>_DIR`` from ``user-dirs.dirs``; ``None`` when unset or ``$HOME``.

    xdg-user-dirs points an entry at ``$HOME`` itself when its folder was
    removed; loose files in the home folder are not what anyone wants.
    """
    value = _xdg_entry(name)
    if value is None:
        return None
    path = Path(value)
    return None if path == Path.home() else path


def _xdg_names_home(name: str) -> bool:
    value = _xdg_entry(name)
    return value is not None and Path(value) == Path.home()


def _xdg_entry(name: str) -> str | None:
    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    try:
        text = (config_home / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:  # no user-dirs file is normal; the caller uses its default folder
        return None
    match = re.search(rf'^XDG_{re.escape(name)}_DIR="([^"]*)"', text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).replace("$HOME", str(home))


def windows_known_folder(folder: tuple[int, int, int, tuple[int, ...]]) -> Path | None:
    """A Windows known folder through ``SHGetKnownFolderPath``, or ``None``."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    data1, data2, data3, data4 = folder
    folder_id = _GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    out = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value) if out.value else None
    finally:
        ole32.CoTaskMemFree(out)


__all__ = [
    "desktop_dir",
    "documents_dir",
    "downloads_dir",
    "named_folders",
    "pictures_dir",
    "windows_known_folder",
    "xdg_user_dir",
]
