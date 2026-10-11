"""Which local files a media tool may open, and what kind of media each one is.

The caller is a model (Hermes), and a model can be talked into asking for any
path. So a path is accepted only when it resolves (symlinks followed) to a
regular file inside one of the folders below, carries a media file extension
of the requested kind, and is under the size limit:

* the user's own media folders: Pictures, Videos, Music, Downloads, Desktop,
  Documents (and their OneDrive copies on Windows),
* Hermes's media caches (``<hermes home>/cache``: images, audio and video the
  gateway received from a chat platform),
* Jarvis's data folder and any ``.jarvis/drops`` folder (files dropped into a
  Jarvis chat),
* the folders listed in ``[multimodal].allowed_dirs``.

Everything else is refused with a message naming those folders, so the model
can tell the user what to do instead of guessing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

Kind = Literal["image", "audio", "video"]

EXTENSIONS: dict[str, frozenset[str]] = {
    "image": frozenset(
        {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".jfif"}
    ),
    "audio": frozenset(
        {".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma", ".webm", ".amr"}
    ),
    "video": frozenset(
        {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".wmv", ".mpg", ".mpeg", ".3gp", ".ts"}
    ),
}

#: User folders, relative to the home directory.
_HOME_FOLDERS = ("Pictures", "Videos", "Music", "Downloads", "Desktop", "Documents", "Movies")


class MediaFileError(ValueError):
    """The file cannot be used; ``str(exc)`` is the reason the model reads."""


@dataclass(frozen=True)
class MediaFile:
    path: Path
    kind: Kind
    size: int


def default_roots() -> list[Path]:
    """The folders a media tool may read without any configuration."""
    roots: list[Path] = []
    home = Path.home()
    for folder in _HOME_FOLDERS:
        roots.append(home / folder)
        roots.append(home / "OneDrive" / folder)
    try:
        from jarvis.core.paths import user_data_dir

        roots.append(user_data_dir())
    except Exception as exc:  # noqa: BLE001 — without the data folder the other roots still apply
        log.info("multimodal: Jarvis data folder unknown (%s)", exc)
    try:
        from jarvis.plugins.brain.hermes_inventory import find_hermes_home

        hermes = find_hermes_home()
        if hermes is not None:
            roots.append(hermes / "cache")
            # Older Hermes versions keep the caches at the home's top level.
            roots += [hermes / name for name in ("image_cache", "audio_cache", "video_cache")]
    except Exception as exc:  # noqa: BLE001 — without Hermes's caches the other roots still apply
        log.info("multimodal: Hermes home unknown (%s)", exc)
    return roots


def _resolved(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):  # an unresolvable root (bad drive, loop) is simply not a root
        return None


def allowed_roots(extra: tuple[str, ...] | list[str] = ()) -> list[Path]:
    roots = [Path(item) for item in extra if str(item).strip()] + default_roots()
    seen: dict[str, Path] = {}
    for root in roots:
        resolved = _resolved(root)
        if resolved is not None:
            seen.setdefault(str(resolved).lower(), resolved)
    return list(seen.values())


def _in_drops(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    return any(a == ".jarvis" and b == "drops" for a, b in zip(parts, parts[1:], strict=False))


def _inside(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            if path.is_relative_to(root):
                return True
        except ValueError:  # different drives on Windows: not inside this root
            continue
    return False


def kind_for(path: Path, prefer: tuple[Kind, ...] = ("image", "audio", "video")) -> Kind | None:
    """The media kind of ``path`` by extension; ``prefer`` decides ``.webm`` (audio or video)."""
    suffix = path.suffix.lower()
    for kind in (*prefer, "image", "audio", "video"):
        if suffix in EXTENSIONS[kind]:
            return kind
    return None


def check_file(
    raw: str,
    *,
    want: tuple[Kind, ...],
    max_bytes: int,
    roots: list[Path],
) -> MediaFile:
    """Validate ``raw`` as a readable local media file of one of the ``want`` kinds."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise MediaFileError("Give the full path of a file on this computer.")
    if "://" in text and not text.lower().startswith("file://"):
        raise MediaFileError(
            "Only files on this computer can be read; download the file first and pass its path."
        )
    if text.lower().startswith("file://"):
        text = text[7:]
        if len(text) > 2 and text[0] == "/" and text[2] == ":":  # file:///C:/...
            text = text[1:]
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise MediaFileError(f"'{text}' is not a full path. Give the absolute path of the file.")
    resolved = _resolved(path)
    if resolved is None or not resolved.exists():
        raise MediaFileError(f"No file at {path}.")
    if not resolved.is_file():
        raise MediaFileError(f"{path} is not a file.")
    if not (_inside(resolved, roots) or _in_drops(resolved)):
        existing = [root for root in roots if root.is_dir()] or roots
        shown = ", ".join(str(root) for root in existing[:8])
        raise MediaFileError(
            f"{path} is outside the folders media tools may read ({shown}, a chat's "
            ".jarvis/drops folder). Ask the user to move the file into one of them, or add "
            "its folder to allowed_dirs under [multimodal] in jarvis.toml."
        )
    kind = kind_for(resolved, want)
    if kind is None or kind not in want:
        wanted = " or ".join(want)
        allowed = sorted(set().union(*(EXTENSIONS[k] for k in want)))
        raise MediaFileError(
            f"{resolved.name} is not a supported {wanted} file (accepted: {', '.join(allowed)})."
        )
    size = resolved.stat().st_size
    if size == 0:
        raise MediaFileError(f"{resolved.name} is empty.")
    if size > max_bytes:
        raise MediaFileError(
            f"{resolved.name} is {size / 1e6:.0f} MB; the limit for {kind} files is "
            f"{max_bytes / 1e6:.0f} MB."
        )
    return MediaFile(path=resolved, kind=kind, size=size)
