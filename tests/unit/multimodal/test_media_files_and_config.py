"""Which files a media tool may open, and how ``[multimodal]`` is read."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.multimodal import config, files

from .conftest import make_png, make_wav


def _check(path: Path | str, roots: list[Path], want=("image",), max_bytes=10_000_000):
    return files.check_file(str(path), want=want, max_bytes=max_bytes, roots=roots)


def test_a_file_inside_an_allowed_folder_is_accepted(media_dir: Path) -> None:
    image = make_png(media_dir / "a.png")
    found = _check(image, [media_dir.resolve()])
    assert found.kind == "image" and found.path == image.resolve() and found.size > 0


def test_a_file_outside_every_allowed_folder_is_refused(tmp_path: Path, media_dir: Path) -> None:
    outside = make_png(tmp_path / "secret.png")
    with pytest.raises(files.MediaFileError, match="outside the folders"):
        _check(outside, [media_dir.resolve()])


def test_a_symlink_out_of_an_allowed_folder_is_refused(tmp_path: Path, media_dir: Path) -> None:
    outside = make_png(tmp_path / "secret.png")
    link = media_dir / "innocent.png"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")
    with pytest.raises(files.MediaFileError, match="outside the folders"):
        _check(link, [media_dir.resolve()])


def test_a_jarvis_chat_drop_folder_is_always_readable(tmp_path: Path) -> None:
    drops = tmp_path / "project" / ".jarvis" / "drops"
    drops.mkdir(parents=True)
    image = make_png(drops / "shot.png")
    assert _check(image, []).kind == "image"


@pytest.mark.parametrize(
    "raw, message",
    [
        ("", "full path"),
        ("relative/pic.png", "not a full path"),
        ("https://example.com/pic.png", "Only files on this computer"),
    ],
)
def test_bad_paths_are_refused_with_a_reason(raw: str, message: str, media_dir: Path) -> None:
    with pytest.raises(files.MediaFileError, match=message):
        _check(raw, [media_dir.resolve()])


def test_a_file_url_is_read_as_a_path(media_dir: Path) -> None:
    image = make_png(media_dir / "a.png")
    assert _check(image.resolve().as_uri(), [media_dir.resolve()]).kind == "image"


def test_the_wrong_kind_of_file_is_refused(media_dir: Path) -> None:
    sound = make_wav(media_dir / "a.wav", 0.2)
    with pytest.raises(files.MediaFileError, match="not a supported image"):
        _check(sound, [media_dir.resolve()])
    text = media_dir / "notes.txt"
    text.write_text("hello")
    with pytest.raises(files.MediaFileError, match="not a supported"):
        _check(text, [media_dir.resolve()], want=("audio",))


def test_webm_counts_as_whatever_kind_was_asked_for(media_dir: Path) -> None:
    clip = media_dir / "clip.webm"
    clip.write_bytes(b"\x1a\x45\xdf\xa3" + b"\0" * 64)
    assert _check(clip, [media_dir.resolve()], want=("video",)).kind == "video"
    assert _check(clip, [media_dir.resolve()], want=("audio",)).kind == "audio"


def test_empty_and_oversized_files_are_refused(media_dir: Path) -> None:
    empty = media_dir / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(files.MediaFileError, match="empty"):
        _check(empty, [media_dir.resolve()])
    image = make_png(media_dir / "big.png", size=(800, 800))
    with pytest.raises(files.MediaFileError, match="limit for image files"):
        _check(image, [media_dir.resolve()], max_bytes=100)


def test_a_missing_file_is_named(media_dir: Path) -> None:
    with pytest.raises(files.MediaFileError, match="No file at"):
        _check(media_dir / "nope.png", [media_dir.resolve()])


def test_allowed_roots_include_the_user_folders_and_configured_ones(tmp_path: Path) -> None:
    roots = files.allowed_roots([str(tmp_path)])
    assert tmp_path.resolve() in roots
    assert (Path.home() / "Pictures").resolve() in roots
    assert (Path.home() / "Downloads").resolve() in roots


# ------------------------------------------------------------ [multimodal]


def test_defaults_without_a_section() -> None:
    cfg = config.from_mapping(None)
    assert cfg.enabled and cfg.server_url == "" and cfg.transcriber == "auto"
    assert cfg.extra_ports == (config.DEFAULT_MEDIA_PORT,)
    assert cfg.video_frames == 8 and cfg.max_video_frames == 16


def test_values_are_coerced_and_bad_ones_keep_the_default() -> None:
    cfg = config.from_mapping(
        {
            "server_url": " http://127.0.0.1:11439 ",
            "extra_ports": [11440, "11441"],
            "server_modalities": ["Vision", "audio"],
            "max_audio_seconds": "60",
            "video_frames": "nope",
            "transcriber": "cloud",
            "unknown_key": 1,
        }
    )
    assert cfg.server_url == "http://127.0.0.1:11439"
    assert cfg.extra_ports == (11440, 11441)
    assert cfg.server_modalities == ("vision", "audio")
    assert cfg.max_audio_seconds == 60.0
    assert cfg.video_frames == 8
    assert cfg.transcriber == "auto"
    assert cfg.extra == {"unknown_key": 1}


def test_the_section_is_read_from_jarvis_toml(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[multimodal]\nserver_url = "http://localhost:9000"\nvideo_frames = 4\n')
    cfg = config.load_media_config(toml)
    assert cfg.server_url == "http://localhost:9000" and cfg.video_frames == 4
    assert config.load_media_config(tmp_path / "missing.toml") == config.MediaConfig()
    broken = tmp_path / "broken.toml"
    broken.write_text("[multimodal\n")
    assert config.load_media_config(broken) == config.MediaConfig()
