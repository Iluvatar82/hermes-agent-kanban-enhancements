"""Worker context-window snapshots.

A kanban worker runs in its own process, so nothing outside it knows how full
its context window is. The worker writes a small snapshot next to its log —
``<logs>/<task>.context.json`` — which the dashboard and the desktop page read.

Written from the ``post_llm_call`` hook: that hook runs under a copied context,
so the live agent is reachable, and it carries the conversation history the
estimate needs. Never blocks a turn for longer than a few milliseconds.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from . import core
from .core import logger

_last_write_monotonic = 0.0


def snapshot_path(task_id: str, board: str | None = None):
    return core.kanban_db().worker_logs_dir(board=board) / f"{task_id}.context.json"


def read_snapshot(task_id: str, board: str | None = None) -> dict | None:
    try:
        raw = json.loads(snapshot_path(task_id, board).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _active_agent():
    """The live agent for this turn (bound for every turn, not just subagents)."""
    try:
        from agent.subagent_lifecycle import get_active_subagent_parent

        return get_active_subagent_parent()
    except Exception:
        return None


def _write(payload: dict[str, Any], task_id: str, board: str | None) -> None:
    path = snapshot_path(task_id, board)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def record(*, conversation_history: list | None = None, force: bool = False, **_kwargs) -> bool:
    """Hook callback. True when a snapshot was written."""
    global _last_write_monotonic
    task_id = (os.environ.get("HERMES_KANBAN_TASK") or "").strip()
    if not task_id or not core.setting("worker_context_snapshots", True):
        return False

    interval = float(core.setting("context_snapshot_interval_seconds", 15) or 0)
    now = time.monotonic()
    if not force and (now - _last_write_monotonic) < interval:
        return False

    agent = _active_agent()
    if agent is None:
        return False
    try:
        from agent.context_breakdown import compute_session_context_breakdown

        messages = list(conversation_history or getattr(agent, "_session_messages", None) or [])
        payload = dict(compute_session_context_breakdown(agent, messages))
    except Exception:
        logger.debug("context breakdown unavailable", exc_info=True)
        return False

    run_id = (os.environ.get("HERMES_KANBAN_RUN_ID") or "").strip()
    payload.update({
        "task_id": task_id,
        "run_id": int(run_id) if run_id.isdigit() else None,
        "profile": os.environ.get("HERMES_PROFILE") or None,
        "worker_session_id": getattr(agent, "session_id", None) or os.environ.get("HERMES_SESSION_ID"),
        "message_count": len(messages),
        "updated_at": int(time.time()),
        "source": "kanban-enhancements",
    })
    try:
        _write(payload, task_id, os.environ.get("HERMES_KANBAN_BOARD") or None)
    except OSError:
        logger.debug("could not write the context snapshot", exc_info=True)
        return False
    _last_write_monotonic = now
    return True
