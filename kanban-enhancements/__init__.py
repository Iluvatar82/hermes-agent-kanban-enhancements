"""kanban-enhancements — operator controls for the Hermes kanban board.

What runs where:

* **gateway** (the process with the embedded dispatcher): the dispatcher guard
  that suppresses ticks on a stopped board and applies the live parallel-run
  cap, the board model for spawned workers and for the auto-composer, plus the
  log-stamp reader patch so every existing log surface keeps the classic
  format.
* **kanban worker** (``hermes chat -q "work kanban task …"``): per-line log
  timestamps and the context-window snapshot.
* **CLI**: ``hermes kanban-plus status|stop|start|max-parallel|log|context``.

Nothing here raises into Hermes: every step is guarded and degrades to "feature
off" with a log line.
"""

from __future__ import annotations

from . import (
    board_control,
    board_model,
    cli_commands,
    core,
    dispatch_guard,
    log_stamps,
    profile_update,
    self_update,
    task_skills,
    worker_context,
)
from .core import logger

__all__ = ["register", "ensure_patches", "board_control", "board_model", "dispatch_guard",
           "log_stamps", "profile_update", "self_update", "task_skills", "worker_context"]

_SETTING_KEYS = (
    "stop_terminates_workers",
    "log_timestamps",
    "worker_context_snapshots",
    "context_snapshot_interval_seconds",
    "update_check",
)


def _load_settings(ctx) -> None:
    values = {}
    for key in _SETTING_KEYS:
        try:
            value = ctx.get_config(key)
        except Exception:
            value = None
        if value is not None:
            values[key] = value
    core.set_settings(values)


def ensure_patches() -> dict[str, bool]:
    """Install every core patch that is not already in place; the state after.

    Idempotent, cheap and safe from any thread or any copy of this package: the
    flag lives on the patched function, so "already done" is an attribute read
    and two copies never wrap each other. Called from ``register``, from the
    dispatch-tick hook, from ``GET /state`` and from the CLI — whoever asks
    first repairs a patch that an early import failure cost us.
    """
    guard = bool(core.guarded("dispatcher guard")(dispatch_guard.install)())
    return {"guard": guard, **board_model.install()}


def _repair_patches(**_kwargs) -> None:
    """``on_kanban_dispatch_tick`` observer — see :func:`ensure_patches`."""
    ensure_patches()


def register(ctx) -> None:
    """Plugin entry point. Runs in the gateway, in every worker and in the CLI."""
    _load_settings(ctx)

    # Readers first: whoever reads a worker log in this process should see the
    # classic format even if the stamping half is switched off.
    core.guarded("log-stamp reader patch")(log_stamps.install_read_patch)()

    if core.setting("log_timestamps", True) and core.guarded(
            "worker log timestamps")(log_stamps.install_worker_streams)():
        logger.debug("worker log timestamps active")

    ensure_patches()

    # The same three patches, re-checked once per dispatcher tick. Boot is the
    # worst moment to import the dispatcher and the auxiliary client, and an
    # import that was not ready then used to leave the guard and the board
    # model off for the whole life of the process. The tick fires after the
    # dispatch lock is released, so this costs three attribute reads on a tick
    # where everything is already in place, and repairs it where it is not.
    ctx.register_hook("on_kanban_dispatch_tick", _repair_patches)

    if core.setting("worker_context_snapshots", True):
        ctx.register_hook("post_llm_call", worker_context.record)

    ctx.register_cli_command(
        "kanban-plus",
        help="Kanban board controls: stop/start the board, parallel-run cap, worker log and context",
        setup_fn=cli_commands.setup,
        handler_fn=cli_commands.handle,
        description="Operator controls contributed by the kanban-enhancements plugin.",
    )

    def _unload() -> None:
        dispatch_guard.uninstall()
        board_model.uninstall()
        log_stamps.uninstall_read_patch()

    try:
        ctx.on_unload(_unload)
    except Exception:  # older Hermes without on_unload — nothing to clean up on reload
        logger.debug("ctx.on_unload unavailable", exc_info=True)
