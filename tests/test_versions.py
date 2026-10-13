"""One version everywhere: the package, the Omarchy plugin manifest and the Meet extension."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import chttr

ROOT = Path(__file__).resolve().parents[1]


def test_versions_agree():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    plugin = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
    extension = json.loads((ROOT / "src/chttr/extension/manifest.json").read_text(encoding="utf-8"))["version"]
    assert chttr.__version__ == pyproject == plugin == extension


def test_changelog_has_an_entry_for_this_version():
    assert f"## [{chttr.__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
