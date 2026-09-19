"""``hermes kanban-plus …`` — the board controls from a terminal."""

from __future__ import annotations

import argparse
import json
import sys

from . import board_control, board_model, core, dispatch_guard, worker_context


def _conn(board: str | None = None):
    """Connection to ``--board``'s own database — separate boards are separate
    databases, so the current one must never answer for the chosen one."""
    from hermes_cli import kanban_db_connect as kbc

    return kbc.connect_closing(board=board)


def _board(args: argparse.Namespace) -> str | None:
    return getattr(args, "board", None)


def _print_state(state, cap, running: int, as_json: bool) -> None:
    if as_json:
        print(json.dumps({**state.as_dict(), "effective_max_in_progress": cap, "running": running}))
        return
    mode = "STOPPED" if state.stopped else "running"
    print(f"Board {state.board}: {mode}")
    if state.stopped and state.reason:
        print(f"  reason: {state.reason}")
    print(f"  workers running: {running}")
    print(f"  max parallel runs: {'unbounded' if cap is None else cap}")
    chosen = board_model.get_model(state.board)
    print(f"  board model: {chosen.model or 'inherit (profile default)'}"
          + (f" (provider {chosen.provider})" if chosen.provider else ""))
    if not dispatch_guard.is_installed():
        print("  ! dispatcher guard inactive — stop/start and the cap are NOT enforced")


def _running_count(conn) -> int:
    row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()
    return int(row[0] if row else 0)


def _cmd_status(args) -> int:
    board = _board(args)
    with _conn(board) as conn:
        _print_state(board_control.get_state(board), dispatch_guard.effective_cap(),
                     _running_count(conn), bool(getattr(args, "json", False)))
    return 0


def _cmd_stop(args) -> int:
    board = _board(args)
    with _conn(board) as conn:
        result = board_control.stop(conn, board=board, reason=getattr(args, "reason", None))
    print(f"Board {result.state.board} stopped.")
    if result.reclaimed:
        print(f"  reclaimed: {', '.join(result.reclaimed)}")
    if result.failed:
        print(f"  could not reclaim: {', '.join(result.failed)}")
    return 0


def _cmd_start(args) -> int:
    board = _board(args)
    state = board_control.start(board=board)
    spawned: list = []
    try:
        kbd = core.kanban_dispatch()
        with _conn(board) as conn:
            spawned = [entry[0] for entry in kbd.dispatch_once(
                conn, board=board, max_in_progress=dispatch_guard.effective_cap()).spawned]
    except Exception:
        pass
    print(f"Board {state.board} started." + (f" Spawned: {', '.join(spawned)}" if spawned else ""))
    return 0


def _cmd_max_parallel(args) -> int:
    value = getattr(args, "value", None)
    if value is None:
        cap, explicit = dispatch_guard.configured_cap()
        shown = "unbounded" if cap is None and explicit else ("not set" if not explicit else str(cap))
        print(f"kanban.max_in_progress: {shown}")
        effective = dispatch_guard.effective_cap()
        print(f"effective: {'unbounded' if effective is None else effective}")
        return 0
    from hermes_cli.config import load_config, save_config

    cfg = load_config() or {}
    section = cfg.setdefault("kanban", {})
    if not isinstance(section, dict):
        section = cfg["kanban"] = {}
    section["max_in_progress"] = int(value)
    save_config(cfg)
    print(f"kanban.max_in_progress = {int(value)}" + (" (unbounded)" if int(value) == 0 else ""))
    return 0


def _cmd_model(args) -> int:
    value = getattr(args, "model", None)
    board = _board(args)
    if value is None:
        chosen = board_model.get_model(board)
        print(f"board model: {chosen.model or 'inherit (profile default)'}"
              + (f" (provider {chosen.provider})" if chosen.provider else ""))
        return 0
    chosen = board_model.set_model("" if value in {"-", "unset", "inherit"} else value,
                                   getattr(args, "provider", "") or "", board=board)
    print(f"board model: {chosen.model or 'inherit (profile default)'}"
          + (f" (provider {chosen.provider})" if chosen.provider else ""))
    return 0


def _cmd_log(args) -> int:
    kb = core.kanban_db()
    text = kb.read_worker_log(args.task_id, tail_bytes=getattr(args, "tail", None),
                              board=_board(args), timestamps=not getattr(args, "no_timestamps", False))
    if text is None:
        print(f"(no log for {args.task_id})", file=sys.stderr)
        return 1
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def _cmd_context(args) -> int:
    snapshot = worker_context.read_snapshot(args.task_id, _board(args))
    if snapshot is None:
        print(f"(no context snapshot for {args.task_id})", file=sys.stderr)
        return 1
    print(json.dumps(snapshot, indent=2) if getattr(args, "json", False) else
          f"{snapshot.get('context_used')}/{snapshot.get('context_max')} tokens "
          f"({snapshot.get('context_percent')}%) · {snapshot.get('model') or 'unknown model'}")
    return 0


def setup(parser: argparse.ArgumentParser) -> None:
    """Build ``hermes kanban-plus`` (called by Hermes with our subparser)."""
    parser.add_argument("--board", help="Board slug (default: the current board)")
    subs = parser.add_subparsers(dest="kx_command")

    status = subs.add_parser("status", help="Board state, worker count and the parallel-run cap")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_cmd_status)

    stop = subs.add_parser("stop", help="Stop the board and reclaim running workers")
    stop.add_argument("reason", nargs="?", help="Why (shown in the UI)")
    stop.set_defaults(func=_cmd_stop)

    start = subs.add_parser("start", help="Start the board again and dispatch once")
    start.set_defaults(func=_cmd_start)

    mp = subs.add_parser("max-parallel", help="Show or set kanban.max_in_progress (0 = unbounded)")
    mp.add_argument("value", nargs="?", type=int)
    mp.set_defaults(func=_cmd_max_parallel)

    model = subs.add_parser(
        "model", help="Show or set the board model (applies to task runs and the auto-composer)")
    model.add_argument("model", nargs="?", help="Model id, or 'unset' to inherit the profile's model")
    model.add_argument("--provider", help="Pin the provider too")
    model.set_defaults(func=_cmd_model)

    log = subs.add_parser("log", help="Worker log with its per-line write timestamps")
    log.add_argument("task_id")
    log.add_argument("--tail", type=int, help="Only the last N bytes")
    log.add_argument("--no-timestamps", action="store_true")
    log.set_defaults(func=_cmd_log)

    ctx = subs.add_parser("context", help="The worker's latest context-window snapshot")
    ctx.add_argument("task_id")
    ctx.add_argument("--json", action="store_true")
    ctx.set_defaults(func=_cmd_context)


def handle(args: argparse.Namespace) -> int:
    """Dispatch to the subcommand; no subcommand prints status."""
    func = getattr(args, "func", None)
    if func is None or func is handle:
        return _cmd_status(args)
    return func(args)
