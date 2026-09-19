"""The one place this plugin reaches into core: a wrapper around ``dispatch_once``.

Core fires ``on_kanban_dispatch_tick`` only AFTER a tick, so an observer cannot
keep a stopped board from spawning. Wrapping the entry point every caller shares
(gateway tick, ``hermes kanban dispatch``, the desktop/dashboard nudge) is the
only way to suppress a tick before it spawns.

Because this is a wrapper around code the plugin does not own, ``install``
verifies the signature it expects first and refuses — loudly, once — when a
Hermes update changed it. A refused install leaves Hermes exactly as shipped.
"""

from __future__ import annotations

import inspect
from typing import Any

from . import board_control, core
from .core import logger

#: Keyword arguments the wrapper reads or forwards. A missing one means core
#: changed shape and the wrapper must not run.
REQUIRED_PARAMS = ("board", "max_in_progress", "dry_run")

_original = None
_installed = False


def configured_cap() -> tuple[int | None, bool]:
    """``(cap, explicit)`` from ``kanban.max_in_progress``.

    ``0`` means explicitly unbounded — core treats it as invalid and silently
    falls back to its memory-derived default, which leaves no way to say "no
    cap" at all.
    """
    try:
        from hermes_cli.config import load_config_readonly

        raw = ((load_config_readonly() or {}).get("kanban") or {}).get("max_in_progress")
    except Exception:
        return None, False
    if raw is None or isinstance(raw, bool):
        return None, False
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, False
    if value < 0:
        return None, False
    return (None if value == 0 else value), True


def effective_cap() -> int | None:
    """The cap this plugin would apply: config when set, else what core derives."""
    cap, explicit = configured_cap()
    if explicit:
        return cap
    kbd = core.kanban_dispatch()
    try:
        return kbd.resolve_max_in_progress(kbd.configured_max_in_progress())
    except Exception:
        return None


def _signature_matches(fn) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return all(name in params for name in REQUIRED_PARAMS)


def install() -> bool:
    """Wrap ``kanban_db_dispatch.dispatch_once``; True when active."""
    global _original, _installed
    if _installed:
        return True
    kbd = core.kanban_dispatch()
    original = getattr(kbd, "dispatch_once", None)
    if original is None or not _signature_matches(original):
        logger.warning(
            "kanban-enhancements: this Hermes build's dispatch_once does not match the expected "
            "signature — board stop/start and the parallel-run cap stay OFF (everything else works). "
            "Please report the Hermes version at "
            "https://github.com/Iluvatar82/hermes-agent-kanban-enhancements/issues")
        return False

    def dispatch_once(conn, **kwargs) -> Any:
        board = kwargs.get("board")
        if board_control.is_stopped(board):
            logger.debug("board %s is stopped — dispatch suppressed", core.board_slug(board))
            result = kbd.DispatchResult()
            result.board_stopped = True
            return result
        cap, explicit = configured_cap()
        if explicit:
            # The live config wins over a value a caller resolved at boot, so a
            # changed cap takes effect on the next tick instead of on restart.
            kwargs["max_in_progress"] = cap
        return original(conn, **kwargs)

    dispatch_once.__doc__ = (original.__doc__ or "") + "\n\nWrapped by the kanban-enhancements plugin."
    dispatch_once.__wrapped__ = original  # type: ignore[attr-defined]
    kbd.dispatch_once = dispatch_once
    _original, _installed = original, True
    logger.info("kanban-enhancements: dispatcher guard active (board stop + live max_in_progress)")
    return True


def uninstall() -> None:
    """Restore core's own ``dispatch_once`` (plugin disable / reload)."""
    global _original, _installed
    if not _installed:
        return
    try:
        core.kanban_dispatch().dispatch_once = _original
    except Exception:
        logger.debug("could not restore dispatch_once", exc_info=True)
    _original, _installed = None, False


def is_installed() -> bool:
    return _installed
