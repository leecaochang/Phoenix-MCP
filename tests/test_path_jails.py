"""Every path jail refuses a sibling whose name starts with the jail's own name.

A prefix check on the bare directory name passes `config_private` for `config`, so
each jail has to compare against the directory plus a separator. The cases go
through each jail's real resolver, with both a dot-dot path and a symlink where the
resolver's own argument rules would otherwise hide the comparison.
"""

import os
from pathlib import Path

import pytest

from custom_components.phoenix_mcp.tools.blueprint import _resolve_blueprint_path
from custom_components.phoenix_mcp.tools.config_files import _resolve_fs_path, _resolve_yaml_read_path
from custom_components.phoenix_mcp.tools.esphome import _resolve_esphome_path
from custom_components.phoenix_mcp.yaml_includes import _resolve_include_path


@pytest.fixture
def config(hass, tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    hass.config.config_dir = str(root)
    return root


def _sibling(config: Path, relative: str) -> Path:
    """A secret file beside a jail, sharing the jail's name as a prefix."""
    target = config / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret: value\n")
    return target


def _link_into(jail: Path, target: Path) -> str:
    """A symlink inside a jail, pointing at the directory holding the sibling."""
    jail.mkdir(parents=True, exist_ok=True)
    (jail / "link").symlink_to(target.parent)
    return f"link/{target.name}"


def test_include_jail(config):
    outside = config.parent / "config_private"
    outside.mkdir()
    (outside / "x.yaml").write_text("secret: value\n")
    containing = str(config / "configuration.yaml")
    assert _resolve_include_path(containing, "../config_private/x.yaml", str(config)) is None
    assert _resolve_include_path(containing, "packages/ok.yaml", str(config)) == str(config / "packages/ok.yaml")


def test_blueprint_jail(hass, config):
    target = _sibling(config, "blueprints/automation_private/x.yaml")
    link = _link_into(config / "blueprints/automation", target)
    assert _resolve_blueprint_path(hass, "automation", link) is None
    resolved = _resolve_blueprint_path(hass, "automation", "mine/ok.yaml")
    assert resolved is not None and resolved[0].endswith(os.path.join("automation", "mine", "ok.yaml"))


def test_esphome_jail(hass, config):
    target = _sibling(config, "esphome_private/x.yaml")
    (config / "esphome").mkdir()
    assert _resolve_esphome_path(hass, "../esphome_private/x.yaml") is None
    assert _resolve_esphome_path(hass, _link_into(config / "esphome", target)) is None
    assert _resolve_esphome_path(hass, "device.yaml") is not None


def test_config_file_jail(hass, config):
    (config / "www").mkdir()
    _sibling(config, "www2/private.txt")
    assert _resolve_fs_path(hass, "www2/private.txt") is None
    assert _resolve_fs_path(hass, "www/../www2/private.txt") is None
    assert _resolve_fs_path(hass, "www/ok.txt") is not None


def test_yaml_read_jail(hass, config):
    outside = config.parent / "config_private"
    outside.mkdir()
    (outside / "x.yaml").write_text("secret: value\n")
    (config / "ok.yaml").write_text("fine: true\n")
    assert _resolve_yaml_read_path(hass, "../config_private/x.yaml") is None
    assert _resolve_yaml_read_path(hass, "ok.yaml") is not None
