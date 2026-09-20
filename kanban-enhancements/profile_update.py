"""Install this plugin into EVERY Hermes profile, not only the gateway's own.

``self_update`` reinstalls the plugin the way the desktop's own "Install from
Git" modal does — and, like that modal, it reaches exactly one Hermes home: the
one the process runs under. But this plugin has to be installed per profile to
do its whole job: the gateway's profile runs the dispatcher (stop switch, cap,
board model), and EVERY profile that runs kanban workers needs its own copy for
the log timestamps and the context snapshots. That is what
``scripts/update.ps1`` is for, and it is the one part of updating that the page
used to have to send people to a shell for.

So this module does what the script does, in-process:

* ``hermes_cli.profiles`` lists the live profiles and resolves each one's home —
  the same listing ``hermes profile list`` prints, so a ghost shell or a
  tombstoned profile is never installed into;
* ``hermes_constants.set_hermes_home_override`` points ONE call at that home.
  It is a ``ContextVar``, not ``os.environ``: a gateway serving several
  profiles must not have its environment rewritten under its other threads,
  and the override is reset in a ``finally`` whatever the installer does;
* the installer is core's ``dashboard_install_plugin`` through
  ``self_update.install``, so every profile gets a byte-identical install and
  the source still comes from Hermes' own metadata, never from a request.

A Hermes without the override cannot be pointed at another home safely, so
:func:`supported` answers ``False`` there and the page keeps its single-profile
button instead of half-doing it.

One profile failing neither stops the others nor hides itself — every profile
gets a row in the result, which is the contract ``update.ps1`` has too.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import core, self_update
from .core import logger


def _constants():
    import hermes_constants

    return hermes_constants


def _profiles():
    from hermes_cli import profiles

    return profiles


def supported() -> bool:
    """True when this Hermes can be pointed at another profile's home.

    Both halves have to be there: the context-local home override, and the
    profile listing that says which homes exist.
    """
    try:
        constants = _constants()
        profiles = _profiles()
    except Exception:
        logger.debug("profile-wide updates unavailable", exc_info=True)
        return False
    return all(
        callable(getattr(owner, name, None))
        for owner, name in (
            (constants, "set_hermes_home_override"),
            (constants, "reset_hermes_home_override"),
            (profiles, "list_profile_names"),
            (profiles, "get_profile_dir"),
        )
    )


@contextmanager
def hermes_home(path: str | Path) -> Iterator[None]:
    """Run the block against ``path`` as the Hermes home, then put it back."""
    constants = _constants()
    token = constants.set_hermes_home_override(str(path))
    try:
        yield
    finally:
        constants.reset_hermes_home_override(token)


def current_home() -> Path:
    return Path(_constants().get_hermes_home())


def _same_home(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:  # a home that vanished under us is not the current one
        return False


def plugin_dir(home: str | Path) -> Path:
    """Where this plugin lives inside ``home``."""
    return Path(home) / "plugins" / core.PLUGIN_ID


def installed_in(home: str | Path) -> str:
    """The plugin version installed in ``home``, or ``''`` when it is not.

    Read from that home's ``plugin.yaml`` rather than from the installer's
    answer: ``self_update.installed_version`` reads the manifest beside the
    RUNNING package, which is a different profile's file.
    """
    try:
        return self_update.parse_version(
            (plugin_dir(home) / "plugin.yaml").read_text(encoding="utf-8"))
    except OSError:
        return ""


def list_profiles() -> list[dict[str, Any]]:
    """Every live profile: its name, its home, and the version installed there.

    ``default`` first, then the named ones in the order Hermes lists them.
    Empty when this Hermes has no profile API — :func:`supported` says so, and
    the page hides the action rather than offering an empty one.
    """
    if not supported():
        return []
    profiles = _profiles()
    here = current_home()
    rows: list[dict[str, Any]] = []
    for name in profiles.list_profile_names():
        try:
            home = Path(profiles.get_profile_dir(name))
        except Exception:
            logger.debug("profile %s has no resolvable home", name, exc_info=True)
            continue
        rows.append({
            "name": name,
            "home": str(home),
            "current": _same_home(home, here),
            "installed": installed_in(home),
        })
    return rows


def _install_one(profile: dict[str, Any], source: str, ref: str | None,
                 installer: Callable[[str, str | None], dict] | None) -> dict[str, Any]:
    """One profile's install, never raising: the row says what happened."""
    home = profile["home"]
    row: dict[str, Any] = {
        "name": profile["name"],
        "home": home,
        "current": profile["current"],
        "was": profile["installed"],
    }
    install = installer or self_update.install
    try:
        with hermes_home(home):
            result = install(source, ref) or {}
    except Exception as exc:
        logger.warning("update of profile %s failed", profile["name"], exc_info=True)
        return {**row, "ok": False, "installed": installed_in(home), "error": f"{type(exc).__name__}: {exc}"}

    ok = bool(result.get("ok"))
    return {
        **row,
        "ok": ok,
        # The version that ended up on disk THERE — the installer reports the
        # one beside the running package, which is this profile's, not that one's.
        "installed": installed_in(home),
        "error": None if ok else str(result.get("error") or "the installer refused the update"),
        "warnings": [str(warning) for warning in (result.get("warnings") or [])],
    }


def install_all(source: str, ref: str | None = None, *,
                installer: Callable[[str, str | None], dict] | None = None) -> list[dict[str, Any]]:
    """Reinstall this plugin into every live profile; one row per profile.

    Blocking (a git clone per profile) — callers on an event loop run it off to
    a thread. ``installer`` is the seam the tests use; production passes none
    and gets :func:`self_update.install`.
    """
    return [_install_one(profile, source, ref, installer) for profile in list_profiles()]
