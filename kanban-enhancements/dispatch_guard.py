"""The one place this plugin reaches into core: a wrapper around ``dispatch_once``.

Core fires ``on_kanban_dispatch_tick`` only AFTER a tick, so an observer cannot
keep a stopped board from spawning. Wrapping the entry point every caller shares
(gateway tick, ``hermes kanban dispatch``, the desktop/dashboard nudge) is the
only way to suppress a tick before it spawns.

Because this is a wrapper around code the plugin does not own, ``install``
verifies the signature it expects first and refuses — loudly, once — when a
Hermes update changed it. A refused install leaves Hermes exactly as shipped.

``install`` is also **retryable**. It used to run exactly once, from
``register()``, during gateway boot — and boot is the worst moment to import
``hermes_cli.kanban_db_dispatch``: an import that is not ready yet left the
guard off for the whole life of the process with no way back. So the state
lives on the patched function (``core.PATCH_MARKER``) instead of in a module
global, every call re-checks it, and the dispatch-tick hook, the REST state
endpoint and the CLI all call ``install`` again. Whoever asks first repairs it.
"""

from __future__ import annotations

import inspect
from typing import Any

from . import board_control, core
from .core import logger

#: Keyword arguments the wrapper reads or forwards. A missing one means core
#: changed shape and the wrapper must not run.
REQUIRED_PARAMS = ("board", "max_in_progress", "dry_run")

#: Name under which this plugin's ``dispatch_once`` wrapper is stamped.
PATCH_NAME = "dispatch_once"

#: The refusal above is logged once per process, not once per retry.
_warned = False


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


def _dispatch_module():
    """``hermes_cli.kanban_db_dispatch``, or None while it cannot be imported.

    Returning None rather than raising is what makes a retry meaningful: an
    import that is not ready during gateway boot is a "not yet", not a "never".
    """
    try:
        return core.kanban_dispatch()
    except Exception:
        logger.debug("kanban_db_dispatch unavailable", exc_info=True)
        return None


def install() -> bool:
    """Wrap ``kanban_db_dispatch.dispatch_once``; True when the guard is active.

    Safe to call repeatedly and from any copy of this package: the marker on
    the live function is the single source of truth, so a second call is three
    attribute reads and a second copy never double-wraps the first one's work.
    """
    global _warned
    kbd = _dispatch_module()
    if kbd is None:
        return False
    if core.patched(kbd, "dispatch_once", PATCH_NAME):
        return True
    original = getattr(kbd, "dispatch_once", None)
    if original is None or not _signature_matches(original):
        if not _warned:
            _warned = True
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
    kbd.dispatch_once = core.stamp(dispatch_once, PATCH_NAME, original)
    logger.info("kanban-enhancements: dispatcher guard active (board stop + live max_in_progress)")
    return True


def uninstall() -> None:
    """Restore core's own ``dispatch_once`` (plugin disable / reload).

    Only ever unwraps OUR wrapper, and only by the original it recorded on
    itself — never a module global, which in a gateway holding two copies of
    this package would restore the wrong function (or clobber a live guard the
    other copy still needs).
    """
    kbd = _dispatch_module()
    if kbd is None or not core.patched(kbd, "dispatch_once", PATCH_NAME):
        return
    original = core.original_of(kbd, "dispatch_once")
    if original is not None:
        kbd.dispatch_once = original


def is_installed() -> bool:
    """Whether the guard is live IN THIS PROCESS — read off the dispatcher
    itself, so it is the truth whichever copy of this package installed it."""
    kbd = _dispatch_module()
    return kbd is not None and core.patched(kbd, "dispatch_once", PATCH_NAME)
