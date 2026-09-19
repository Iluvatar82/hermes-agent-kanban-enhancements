"""Shared plumbing: settings, lazy core imports, board-scoped state.

Every core import is lazy and guarded. A Hermes update that moves or renames
something must degrade this plugin to a no-op with a log line, never break the
host — see ``guarded``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

PLUGIN_ID = "kanban-enhancements"
STATE_FILENAME = "kanban-enhancements.json"

logger = logging.getLogger(f"hermes.plugins.{PLUGIN_ID}")

_settings: dict[str, Any] = {}
_settings_lock = threading.Lock()


def set_settings(values: dict[str, Any]) -> None:
    with _settings_lock:
        _settings.clear()
        _settings.update(values or {})


def setting(key: str, default: Any = None) -> Any:
    with _settings_lock:
        value = _settings.get(key, default)
    return default if value is None else value


#: Attribute stamped on every function this plugin installs over a core one.
#: The flag that says "the patch is in" therefore lives on the PATCHED OBJECT,
#: not in a module global: a gateway can hold several copies of this package
#: (one per Hermes home — ``plugins_loader`` gives every scope its own module
#: name), and a global would make one copy's answer a lie about the process.
PATCH_MARKER = "__kanban_enhancements_patch__"


def stamp(wrapper: Callable, name: str, original: Callable) -> Callable:
    """Mark ``wrapper`` as this plugin's replacement for ``original``."""
    setattr(wrapper, PATCH_MARKER, name)
    wrapper.__wrapped__ = original  # type: ignore[attr-defined]
    return wrapper


def patched(owner: Any, attr: str, name: str) -> bool:
    """True when ``owner.attr`` is the patch called ``name`` — whichever copy of
    this package installed it."""
    return getattr(getattr(owner, attr, None), PATCH_MARKER, None) == name


def original_of(owner: Any, attr: str) -> Any:
    """What ``owner.attr`` wrapped, so an uninstall can restore it even when
    another copy of this package did the wrapping."""
    return getattr(getattr(owner, attr, None), "__wrapped__", None)


def guarded(what: str) -> Callable:
    """Decorator: never let this plugin raise into Hermes."""

    def wrap(fn: Callable) -> Callable:
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.warning("%s failed — continuing without it", what, exc_info=True)
                return None

        inner.__name__ = getattr(fn, "__name__", "inner")
        inner.__doc__ = fn.__doc__
        return inner

    return wrap


def kanban_db():
    from hermes_cli import kanban_db as kb

    return kb


def kanban_dispatch():
    from hermes_cli import kanban_db_dispatch as kbd

    return kbd


def board_slug(board: str | None = None) -> str:
    """Normalized slug for ``board``, falling back to the current board."""
    kb = kanban_db()
    slug = board
    if not slug:
        try:
            slug = kb.get_current_board()
        except Exception:
            slug = None
    normalize = getattr(kb, "_normalize_board_slug", None)
    if normalize is not None:
        with contextlib.suppress(Exception):
            slug = normalize(slug) or slug
    return str(slug or "default")


def state_path(board: str | None = None) -> Path:
    """State file beside the board's ``kanban.db`` — board-scoped, survives updates."""
    return kanban_db().kanban_db_path(board=board).parent / STATE_FILENAME


def read_state(board: str | None = None) -> dict[str, Any]:
    try:
        raw = json.loads(state_path(board).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_state(data: dict[str, Any], board: str | None = None) -> None:
    """Atomic write (temp file + os.replace) so a crash can't leave half a file."""
    path = state_path(board)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
