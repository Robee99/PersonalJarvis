"""Provisioning preserves other plugins and uses the native enable command."""

from pathlib import Path

from jarvis.cli_ctl.hermes_controls import install_controls


def test_provisioning_is_repeatable_and_does_not_export_credentials(tmp_path: Path):
    commands = []

    def run(argv):
        commands.append(argv)
        return 0, "enabled"

    assert install_controls(home=tmp_path, argv=["hermes"], run=run)[0] == "changed"
    assert install_controls(home=tmp_path, argv=["hermes"], run=run)[0] == "ok"
    assert commands == [["hermes", "plugins", "enable", "jarvis-control"]] * 2
    assert (tmp_path / "plugins/jarvis-control/plugin.yaml").is_file()


def test_unmanaged_plugin_is_preserved(tmp_path: Path):
    target = tmp_path / "plugins/jarvis-control"
    target.mkdir(parents=True)
    target.joinpath("__init__.py").write_text("custom plugin", encoding="utf-8")

    def forbidden(_argv):
        raise AssertionError("Unmanaged code must not be enabled or overwritten")

    status, _ = install_controls(home=tmp_path, argv=["hermes"], run=forbidden)
    assert status == "failed"
    assert target.joinpath("__init__.py").read_text() == "custom plugin"
