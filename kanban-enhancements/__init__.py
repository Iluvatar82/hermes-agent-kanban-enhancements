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
    self_update,
    worker_context,
)
from .core import logger

__all__ = ["register", "board_control", "board_model", "dispatch_guard", "log_stamps", "self_update",
           "worker_context"]

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


def register(ctx) -> None:
    """Plugin entry point. Runs in the gateway, in every worker and in the CLI."""
    _load_settings(ctx)

    # Readers first: whoever reads a worker log in this process should see the
    # classic format even if the stamping half is switched off.
    core.guarded("log-stamp reader patch")(log_stamps.install_read_patch)()

    if core.setting("log_timestamps", True) and core.guarded(
            "worker log timestamps")(log_stamps.install_worker_streams)():
        logger.debug("worker log timestamps active")

    core.guarded("dispatcher guard")(dispatch_guard.install)()
    core.guarded("board model (task executions)")(board_model.install_spawn_patch)()
    core.guarded("board model (auto-composer)")(board_model.install_aux_patch)()

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
