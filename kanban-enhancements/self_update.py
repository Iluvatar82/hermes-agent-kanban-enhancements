"""Update this plugin from inside it: which version is where, and install a new one.

Three versions matter and they are genuinely different things:

* **running**  — what this process imported. Changes only on a gateway restart.
* **installed** — what ``plugin.yaml`` on disk says. Changes the moment an
  update lands, which is exactly what makes "restart to load it" knowable
  rather than a hint we print and hope for.
* **latest**   — what the source repository's default branch declares.

Installing is delegated to ``hermes_cli.plugins_cmd.dashboard_install_plugin`` —
the same non-interactive entry point the desktop's own "Install from Git" modal
goes through, so a plugin installed from here is byte-identical to one installed
from there. The SOURCE is never taken from the caller: it comes from Hermes' own
install metadata (so a fork updates from the fork) and falls back to this
repository. The endpoint can therefore only ever reinstall THIS plugin from the
place it already came from.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from . import core

#: Where this plugin came from when Hermes recorded no install metadata — a
#: manual copy, or scripts/install.ps1.
DEFAULT_SOURCE = "https://github.com/Iluvatar82/hermes-agent-kanban-enhancements.git#kanban-enhancements"

#: `version: 0.3.0`, optionally quoted, at the start of a line in plugin.yaml.
_VERSION_RE = re.compile(r"^version:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)

#: `--ref` takes nothing but a full commit SHA, and neither do we.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: A GitHub source we can read a raw file from: `https://github.com/<o>/<r>.git#<subdir>`.
_GITHUB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/#]+?)(?:\.git)?(?:#(.+))?$")

#: The version probe is one small text file; a gateway must not hang on it.
FETCH_TIMEOUT_SECONDS = 6


def manifest_path() -> Path:
    """This plugin's ``plugin.yaml`` — beside the package, wherever it is installed."""
    return Path(__file__).resolve().parent / "plugin.yaml"


def parse_version(text: str) -> str:
    """The ``version:`` field of a plugin manifest, or ``''``."""
    match = _VERSION_RE.search(text or "")
    return match.group(1).strip() if match else ""


def installed_version() -> str:
    """What is ON DISK right now — newer than ``running`` after an update."""
    try:
        return parse_version(manifest_path().read_text(encoding="utf-8"))
    except OSError:
        return ""


def _version_key(version: str) -> tuple:
    """`0.10.0` sorts above `0.9.0`, and a release above its own prereleases.

    Each dotted chunk becomes ``(number, has_no_suffix, suffix)``: the middle
    flag is what puts `1.0.0` above `1.0.0-rc1` — an empty string would
    otherwise sort lowest and invert exactly the case it exists for.
    """
    parts = []
    for chunk in str(version or "").split("."):
        digits = re.match(r"\d+", chunk)
        number = int(digits.group()) if digits else 0
        suffix = chunk[digits.end():] if digits else chunk
        parts.append((number, not suffix, suffix))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is a version worth installing over ``current``."""
    if not latest or not current:
        return False
    if latest == current:
        return False
    # An unparseable pair still answers "different" rather than "same": a
    # version we cannot compare is better flagged than silently ignored.
    return _version_key(latest) > _version_key(current)


def installed_source() -> str:
    """Where Hermes says this plugin came from, else :data:`DEFAULT_SOURCE`.

    Read from the metadata file directly rather than through ``plugins_cmd``:
    the private reader raises on a malformed file, and a broken metadata file
    must cost the update button, not the whole page.
    """
    try:
        from hermes_constants import get_hermes_home

        path = Path(get_hermes_home()) / "plugins" / ".install-metadata.json"
        entry = json.loads(path.read_text(encoding="utf-8")).get(core.PLUGIN_ID)
        source = str((entry or {}).get("source") or "").strip()
        return source or DEFAULT_SOURCE
    except Exception:
        core.logger.debug("no install metadata for %s", core.PLUGIN_ID, exc_info=True)
        return DEFAULT_SOURCE


def raw_manifest_url(source: str) -> str:
    """The `raw.githubusercontent.com` URL of ``source``'s ``plugin.yaml``, or
    ``''`` for a source we cannot read a file out of (a private host, SSH, a
    local path). ``HEAD`` is the repository's own default branch, whatever it
    is called."""
    match = _GITHUB_RE.match(str(source or "").strip())
    if not match:
        return ""
    owner, repo, subdir = match.group(1), match.group(2), (match.group(3) or "").strip("/")
    prefix = f"{subdir}/" if subdir else ""
    return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{prefix}plugin.yaml"


def fetch_latest_version(source: str, timeout: float = FETCH_TIMEOUT_SECONDS) -> tuple[str, str]:
    """``(version, error)`` for the source's default branch. Never raises — a
    gateway with no outbound network still has to render the page."""
    url = raw_manifest_url(source)
    if not url:
        return "", f"cannot read a manifest from {source!r}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - https, built above
            # A manifest is a couple of KiB; cap the read so a wrong URL that
            # serves something enormous cannot be pulled into memory.
            text = response.read(65_536).decode("utf-8", errors="replace")
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"
    version = parse_version(text)
    return (version, "") if version else ("", "no version field in the fetched manifest")


def normalize_ref(ref: str | None) -> str | None:
    """A pin, validated. Raises ``ValueError`` for anything but a full SHA —
    ``hermes plugins install --ref`` refuses branches and tags too."""
    value = str(ref or "").strip().lower()
    if not value:
        return None
    if not _SHA_RE.match(value):
        raise ValueError("ref must be a full 40-character commit SHA (branches and tags are not accepted)")
    return value


def install(source: str, ref: str | None = None) -> dict[str, Any]:
    """Reinstall this plugin from ``source`` through Hermes' own installer.

    ``force`` is what makes it an update: the plugin is already there. Returns
    the installer's own payload (``ok`` plus, on failure, ``error`` and any scan
    findings), with ``installed_version`` added so the caller can tell whether
    the bytes on disk actually moved.
    """
    from hermes_cli.plugins_cmd import dashboard_install_plugin

    result = dashboard_install_plugin(source, force=True, enable=True, ref=ref)
    return {**result, "installed_version": installed_version()}


def available() -> bool:
    """True when this Hermes exposes the installer this module needs."""
    try:
        from hermes_cli.plugins_cmd import dashboard_install_plugin  # noqa: F401

        return True
    except Exception:
        core.logger.warning("hermes_cli.plugins_cmd.dashboard_install_plugin is unavailable", exc_info=True)
        return False
