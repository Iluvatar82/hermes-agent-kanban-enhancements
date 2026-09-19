"""REST namespace ``/api/plugins/kanban-enhancements``.

Hermes loads this file on its own (not as part of the plugin package), so the
package is attached by path — reusing the copy the plugin loader already
imported in this process when there is one, so both halves share one state.

The desktop plugin talks only to this namespace: ``ctx.rest`` is scoped to the
plugin's own namespace by design, so everything the page needs is served here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

_PKG_DIR = Path(__file__).resolve().parent.parent
_PKG_INIT = str(_PKG_DIR / "__init__.py")
_SYNTHETIC_NAME = "hermes_kanban_enhancements"

router = APIRouter()


def _package():
    """The plugin package: the loader's own copy when it is already imported."""
    for module in list(sys.modules.values()):
        if getattr(module, "__file__", None) == _PKG_INIT and hasattr(module, "board_control"):
            return module
    existing = sys.modules.get(_SYNTHETIC_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        _SYNTHETIC_NAME, _PKG_INIT, submodule_search_locations=[str(_PKG_DIR)])
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("kanban-enhancements package could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_SYNTHETIC_NAME] = module
    spec.loader.exec_module(module)
    return module


def _kanban_conn():
    from hermes_cli import kanban_db_connect as kbc

    return kbc.connect_closing()


def _running_count(conn) -> int:
    row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()
    return int(row[0] if row else 0)


def _state_payload(board: str | None) -> dict[str, Any]:
    pkg = _package()
    configured, explicit = pkg.dispatch_guard.configured_cap()
    with _kanban_conn() as conn:
        running = _running_count(conn)
    state = pkg.board_control.get_state(board)
    chosen = pkg.board_model.get_model(board)
    return {
        **state.as_dict(),
        "model": chosen.model,
        "provider": chosen.provider,
        "model_patches": pkg.board_model.is_installed(),
        "running": running,
        "max_in_progress": configured if explicit else None,
        "max_in_progress_set": explicit,
        "effective_max_in_progress": pkg.dispatch_guard.effective_cap(),
        "guard_active": pkg.dispatch_guard.is_installed(),
        "plugin_version": "0.1.0",
    }


class StopBody(BaseModel):
    reason: str | None = Field(None, max_length=500)


class BoardModelBody(BaseModel):
    """Empty ``model`` clears the board model back to "inherit"."""

    model: str = Field("", max_length=200)
    provider: str = Field("", max_length=100)


class MaxParallelBody(BaseModel):
    value: int = Field(..., ge=0, le=2_147_483_647)


@router.get("/state")
def get_state(board: str | None = Query(None)):
    """Board switch, worker count and the parallel-run cap in one call."""
    return _state_payload(board)


@router.post("/stop")
def stop_board(payload: StopBody | None = None, board: str | None = Query(None)):
    """Stop the board: persist the switch first, then reclaim running workers."""
    pkg = _package()
    with _kanban_conn() as conn:
        result = pkg.board_control.stop(conn, board=board, reason=(payload.reason if payload else None))
    return {**_state_payload(board), "reclaimed": result.reclaimed, "failed": result.failed}


@router.post("/start")
def start_board(board: str | None = Query(None)):
    """Lift the switch and dispatch once, within the configured cap."""
    pkg = _package()
    pkg.board_control.start(board=board)
    spawned: list = []
    try:
        kbd = pkg.core.kanban_dispatch()
        with _kanban_conn() as conn:
            spawned = [entry[0] for entry in kbd.dispatch_once(
                conn, board=board, max_in_progress=pkg.dispatch_guard.effective_cap()).spawned]
    except Exception:
        spawned = []
    return {**_state_payload(board), "spawned": spawned}


@router.put("/max-parallel")
def set_max_parallel(payload: MaxParallelBody, board: str | None = Query(None)):
    """Write ``kanban.max_in_progress``; ``0`` means explicitly unbounded."""
    try:
        from hermes_cli.config import load_config, save_config

        cfg = load_config() or {}
        section = cfg.setdefault("kanban", {})
        if not isinstance(section, dict):
            section = cfg["kanban"] = {}
        section["max_in_progress"] = int(payload.value)
        save_config(cfg)
    except Exception as exc:  # managed config, read-only file, …
        raise HTTPException(status_code=500, detail=f"could not save config: {exc}") from exc
    return _state_payload(board)


@router.put("/model")
def set_board_model(payload: BoardModelBody, board: str | None = Query(None)):
    """Set (or clear) the model every task run and the auto-composer use."""
    pkg = _package()
    pkg.board_model.set_model(payload.model, payload.provider, board=board)
    return _state_payload(board)


@router.get("/tasks")
def list_tasks(board: str | None = Query(None), limit: int = Query(40, ge=1, le=200)):
    """Running tasks first, then the most recently touched ones."""
    from hermes_cli import kanban_db as kb

    def _row(task) -> dict[str, Any]:
        return {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "assignee": task.assignee,
            "started_at": task.started_at,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
        }

    with _kanban_conn() as conn:
        running = [_row(t) for t in kb.list_tasks(conn, status="running")]
        recent = [_row(t) for t in kb.list_tasks(conn, limit=limit)]
    seen = {row["id"] for row in running}
    return {"running": running, "recent": [row for row in recent if row["id"] not in seen]}


@router.get("/tasks/{task_id}/log")
def task_log(task_id: str, board: str | None = Query(None),
             tail: int = Query(1_048_576, ge=1, le=8_388_608),
             timestamps: bool = Query(True)):
    """Worker log. ``timestamps=true`` keeps each line's ``[<ISO time>] `` stamp."""
    from hermes_cli import kanban_db as kb

    pkg = _package()
    try:
        content = kb.read_worker_log(task_id, tail_bytes=tail, board=board, timestamps=timestamps)
    except TypeError:
        # A Hermes build whose read_worker_log has no ``timestamps`` keyword and
        # a process where our reader patch is not installed.
        content = kb.read_worker_log(task_id, tail_bytes=tail, board=board)
        if not timestamps and content:
            content = pkg.log_stamps.strip_timestamps(content)
    path = kb.worker_log_path(task_id, board=board)
    size = path.stat().st_size if path.exists() else 0
    return {
        "task_id": task_id,
        "exists": content is not None,
        "content": content or "",
        "size_bytes": size,
        "truncated": bool(size > tail),
    }


@router.get("/tasks/{task_id}/context")
def task_context(task_id: str, board: str | None = Query(None)):
    """The worker's latest context-window snapshot (written every ~15 s)."""
    pkg = _package()
    snapshot = pkg.worker_context.read_snapshot(task_id, board)
    if not snapshot:
        return {"task_id": task_id, "available": False}
    live = False
    try:
        from hermes_cli import kanban_db as kb

        with _kanban_conn() as conn:
            task = kb.get_task(conn, task_id)
        live = bool(task and task.status == "running")
    except Exception:
        live = False
    return {**snapshot, "available": True, "live": live}
