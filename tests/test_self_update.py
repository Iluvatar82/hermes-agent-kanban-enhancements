"""Updating the plugin from inside it: the version arithmetic and the seam."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def su(pkg):
    return pkg.self_update


def test_a_manifest_version_is_read_however_it_is_quoted(su):
    assert su.parse_version("name: x\nversion: 0.3.0\ntags:\n") == "0.3.0"
    assert su.parse_version("version: '1.2.3'") == "1.2.3"
    assert su.parse_version('version: "1.2.3"  # pinned') == "1.2.3"
    assert su.parse_version("no version here") == ""


def test_the_plugins_own_manifest_parses(su, pkg):
    """The regex is only worth having if it reads the file it ships beside."""
    assert su.installed_version() == su.parse_version(su.manifest_path().read_text(encoding="utf-8"))
    assert su.installed_version().count(".") == 2


def test_newer_compares_numerically_not_alphabetically(su):
    assert su.is_newer("0.10.0", "0.9.0") is True
    assert su.is_newer("0.3.1", "0.3.0") is True
    assert su.is_newer("0.3.0", "0.3.0") is False
    assert su.is_newer("0.2.9", "0.3.0") is False
    # A prerelease sorts below the release it leads to.
    assert su.is_newer("1.0.0", "1.0.0-rc1") is True
    assert su.is_newer("1.0.0-rc1", "1.0.0") is False


def test_an_unknown_version_is_never_an_update(su):
    """A failed probe returns '' — that must not read as "something newer"."""
    assert su.is_newer("", "0.3.0") is False
    assert su.is_newer("0.4.0", "") is False


def test_the_raw_manifest_url_points_at_the_sources_default_branch(su):
    assert su.raw_manifest_url("https://github.com/o/r.git#sub/dir") == (
        "https://raw.githubusercontent.com/o/r/HEAD/sub/dir/plugin.yaml")
    assert su.raw_manifest_url("https://github.com/o/r") == (
        "https://raw.githubusercontent.com/o/r/HEAD/plugin.yaml")
    # Anything we cannot read a file out of says so instead of guessing a URL.
    assert su.raw_manifest_url("git@github.com:o/r.git#sub") == ""
    assert su.raw_manifest_url("https://gitlab.com/o/r.git") == ""
    assert su.raw_manifest_url("") == ""


def test_this_plugins_own_default_source_resolves_to_a_url(su):
    assert su.raw_manifest_url(su.DEFAULT_SOURCE).endswith("/kanban-enhancements/plugin.yaml")


def test_a_ref_must_be_a_full_sha(su):
    sha = "e6b17af599f98d1605b4f216878250bc734388da"
    assert su.normalize_ref(sha) == sha
    assert su.normalize_ref(f"  {sha.upper()}  ") == sha
    assert su.normalize_ref(None) is None
    assert su.normalize_ref("   ") is None
    for bad in ("main", "v1.0", sha[:12], f"{sha}0", "../../etc"):
        with pytest.raises(ValueError):
            su.normalize_ref(bad)


def test_the_source_comes_from_hermes_own_install_metadata(su, tmp_path, monkeypatch):
    """A fork must update from the fork, not from this repository."""
    import hermes_constants

    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    recorded = {"kanban-enhancements": {"source": "https://github.com/fork/other.git#sub"}}
    (home / "plugins" / ".install-metadata.json").write_text(json.dumps(recorded), encoding="utf-8")

    assert su.installed_source() == "https://github.com/fork/other.git#sub"


def test_a_missing_or_broken_metadata_file_falls_back(su, tmp_path, monkeypatch):
    import hermes_constants

    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    assert su.installed_source() == su.DEFAULT_SOURCE

    (home / "plugins" / ".install-metadata.json").write_text("{not json", encoding="utf-8")
    assert su.installed_source() == su.DEFAULT_SOURCE


def test_a_probe_that_cannot_reach_the_network_reports_it(su, monkeypatch):
    def boom(*a, **k):
        raise OSError("no route to host")

    monkeypatch.setattr(su.urllib.request, "urlopen", boom)
    version, error = su.fetch_latest_version(su.DEFAULT_SOURCE)
    assert version == "" and "no route to host" in error


def test_the_installer_this_module_delegates_to_still_exists(su):
    """The seam, against the real checkout: Kanban+ calls the same entry point
    the desktop's own install modal goes through."""
    import inspect

    from hermes_cli.plugins_cmd import dashboard_install_plugin

    assert su.available() is True
    params = inspect.signature(dashboard_install_plugin).parameters
    assert {"identifier", "force", "enable", "ref"} <= set(params)
