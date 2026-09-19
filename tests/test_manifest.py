"""``plugin.yaml`` against the Hermes that has to install and load it.

The manifest is the one file no code path here imports, so nothing else would
catch a field the running Hermes refuses. These tests read it the way Hermes
does — the installer's limit and the loader's parser, both from the checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

MANIFEST = Path(__file__).resolve().parent.parent / "kanban-enhancements" / "plugin.yaml"


@pytest.fixture
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_the_installer_accepts_this_manifest_version(manifest):
    """A manifest_version above the INSTALLER's cap makes `hermes plugins install`
    and the desktop's "Install from Git" refuse the plugin — the loader's higher
    cap does not help, because nothing gets installed in the first place."""
    from hermes_cli import plugins_cmd

    declared = manifest.get("manifest_version", 1)
    assert declared <= plugins_cmd._SUPPORTED_MANIFEST_VERSION, (
        f"plugin.yaml declares manifest_version {declared}, but this Hermes' installer "
        f"supports up to {plugins_cmd._SUPPORTED_MANIFEST_VERSION}"
    )


def test_the_loader_still_reads_every_setting(manifest):
    """Dropping the version declaration must not cost us the settings schema:
    the loader parses those fields regardless of the version."""
    from hermes_cli import plugins_manifest

    parsed = plugins_manifest._parse_manifest_v2_fields(manifest, "kanban-enhancements")
    assert set(parsed["config_schema"]) == {
        "stop_terminates_workers", "log_timestamps",
        "worker_context_snapshots", "context_snapshot_interval_seconds",
    }
    assert parsed["license"] == "MIT" and parsed["homepage"].startswith("https://")
    assert "kanban" in parsed["tags"]


def test_no_unknown_manifest_fields(manifest):
    """An unknown field loads with a warning and does nothing — a silent typo."""
    from hermes_cli import plugins_manifest

    assert not set(manifest) - plugins_manifest._KNOWN_MANIFEST_FIELDS


def test_the_declared_hermes_requirement_is_satisfiable(manifest):
    """`requires_hermes` is enforced before import: a typo disables the plugin."""
    from hermes_cli import plugins_manifest

    assert plugins_manifest.version_satisfies(manifest["requires_hermes"], "0.21.3")


def test_one_version_in_all_three_places(manifest):
    """One plugin, three manifests: a version that drifts misreports in the UI
    (dashboard/manifest.json) or in ``GET /state`` (plugin_version)."""
    import json
    import re

    dashboard = json.loads(
        (MANIFEST.parent / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    served = re.search(r'"plugin_version": "([^"]+)"',
                       (MANIFEST.parent / "dashboard" / "plugin_api.py").read_text(encoding="utf-8"))
    assert dashboard["version"] == manifest["version"]
    assert served is not None and served.group(1) == manifest["version"]
