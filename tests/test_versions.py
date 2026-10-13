"""One version everywhere: the package, the Omarchy plugin manifest and the Meet extension."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import chttr

ROOT = Path(__file__).resolve().parents[1]


def test_versions_agree():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    plugin = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
    extension = json.loads((ROOT / "src/chttr/extension/manifest.json").read_text(encoding="utf-8"))["version"]
    assert chttr.__version__ == pyproject == plugin
    # Chrome only takes 1-4 dot-separated numbers: the extension drops a pre-release suffix
    assert extension == re.match(r"[\d.]+", pyproject).group(0).rstrip(".")
    assert re.fullmatch(r"\d+(\.\d+){0,3}", extension)


def test_changelog_has_an_entry_for_this_version():
    assert f"## [{chttr.__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_configuration_reference_is_up_to_date():
    import subprocess
    import sys

    out = subprocess.run([sys.executable, str(ROOT / "scripts/gen-config-docs.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
