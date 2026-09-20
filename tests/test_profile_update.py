"""Updating every profile from one button: the listing and the install loop."""

from __future__ import annotations

import pytest


@pytest.fixture
def pu(pkg):
    return pkg.profile_update


@pytest.fixture
def homes(pkg, pu, tmp_path, monkeypatch):
    """Three profiles on disk — default (the current one), dev and review —
    with this plugin installed in two of them at different versions."""
    from hermes_cli import profiles

    roots = {
        "default": tmp_path / "hermes",
        "dev": tmp_path / "hermes" / "profiles" / "dev",
        "review": tmp_path / "hermes" / "profiles" / "review",
    }
    for name, home in roots.items():
        (home / "plugins").mkdir(parents=True)
        if name != "review":  # `review` has no copy yet — the update installs one
            directory = pu.plugin_dir(home)
            directory.mkdir(parents=True)
            (directory / "plugin.yaml").write_text(
                f"name: kanban-enhancements\nversion: 0.{'4' if name == 'dev' else '5'}.0\n",
                encoding="utf-8")

    monkeypatch.setattr(profiles, "list_profile_names", lambda: list(roots))
    monkeypatch.setattr(profiles, "get_profile_dir", lambda name: roots[name])
    # The gateway's own home, stubbed HERE rather than on `get_hermes_home` —
    # that function is what the override under test moves, and a stub over it
    # would hide the very thing these tests are checking.
    monkeypatch.setattr(pu, "current_home", lambda: roots["default"])
    return roots


def test_the_listing_names_every_profile_and_what_is_installed_in_it(pu, homes):
    rows = pu.list_profiles()

    assert [row["name"] for row in rows] == ["default", "dev", "review"]
    assert [row["installed"] for row in rows] == ["0.5.0", "0.4.0", ""]
    # Exactly one profile is the one this gateway runs under.
    assert [row["current"] for row in rows] == [True, False, False]


def test_an_install_runs_against_each_profiles_own_home(pu, homes):
    """The whole feature: ONE process, N homes. Each install must see the
    profile's home as ``get_hermes_home()`` — that is what makes it land there
    instead of N times in the gateway's own profile."""
    import hermes_constants

    seen = []

    def installer(source, ref):
        seen.append((source, ref, str(hermes_constants.get_hermes_home())))
        # What the installer landing there looks like: a manifest on disk.
        directory = pu.plugin_dir(hermes_constants.get_hermes_home())
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "plugin.yaml").write_text("version: 0.6.0\n", encoding="utf-8")
        return {"ok": True, "installed_version": "ignored — that is this profile's manifest"}

    rows = pu.install_all("https://github.com/o/r.git#sub", None, installer=installer)

    assert [home for _source, _ref, home in seen] == [str(home) for home in homes.values()]
    assert {row["ok"] for row in rows} == {True}
    # The version reported per row is the one that landed THERE, never the
    # manifest beside the running package.
    assert [row["installed"] for row in rows] == ["0.6.0"] * 3
    assert [row["was"] for row in rows] == ["0.5.0", "0.4.0", ""]


def test_the_home_override_is_put_back_even_when_the_installer_explodes(pu, homes):
    import hermes_constants

    before = str(hermes_constants.get_hermes_home())

    def boom(source, ref):
        raise OSError("disk full")

    rows = pu.install_all("https://github.com/o/r.git#sub", None, installer=boom)

    assert str(hermes_constants.get_hermes_home()) == before
    assert [row["ok"] for row in rows] == [False, False, False]
    assert all("disk full" in row["error"] for row in rows)


def test_one_profile_failing_neither_stops_the_others_nor_hides_itself(pu, homes):
    def installer(source, ref):
        import hermes_constants

        if hermes_constants.get_hermes_home().name == "dev":
            return {"ok": False, "error": "scan blocked: suspicious file"}
        return {"ok": True}

    rows = pu.install_all("https://github.com/o/r.git#sub", None, installer=installer)

    assert [row["ok"] for row in rows] == [True, False, True]
    assert rows[1]["error"] == "scan blocked: suspicious file"


def test_a_refusal_without_a_reason_still_says_something(pu, homes):
    rows = pu.install_all("src", None, installer=lambda source, ref: {"ok": False})

    assert all(row["error"] for row in rows)


def test_without_the_home_override_the_feature_says_so_instead_of_half_doing_it(pu, homes, monkeypatch):
    """No override, no safe way to point one call at another profile: the page
    keeps its single-profile button rather than installing three times into the
    same home."""
    import hermes_constants

    monkeypatch.delattr(hermes_constants, "set_hermes_home_override", raising=False)

    assert pu.supported() is False
    assert pu.list_profiles() == []


def test_the_hermes_seams_this_module_stands_on_still_exist(pu):
    """Against the real checkout: the override is a ContextVar API and the
    profile listing resolves homes — a Hermes that dropped either one must fail
    HERE, not on a button that silently updates one profile three times."""
    import hermes_constants
    from hermes_cli import profiles

    assert pu.supported() is True
    assert "default" in profiles.list_profile_names()
    token = hermes_constants.set_hermes_home_override("/tmp/kanban-plus-probe")
    try:
        assert str(hermes_constants.get_hermes_home()) == "/tmp/kanban-plus-probe"
    finally:
        hermes_constants.reset_hermes_home_override(token)
