"""REST namespace ``/api/plugins/kanban-enhancements``.

Hermes loads this file on its own (not as part of the plugin package), so the
package is attached by path — reusing the copy the plugin loader already
imported in this process when there is one, so both halves share one state.

The desktop plugin talks only to this namespace: ``ctx.rest`` is scoped to the
plugin's own namespace by design, so everything the page needs is served here —
including the board directory, which is why the ``/boards`` half mirrors core's
own ``plugins/kanban/dashboard/plugin_api.py`` endpoint for endpoint rather than
pointing the page at another plugin's namespace it cannot reach.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


# --- Board plumbing ---------------------------------------------------------
# Separate boards are separate databases. A slug is normalized and validated the
# way core does it (400 malformed, 404 unknown), and EVERY connection is opened
# on that board's own ``kanban.db`` — a ``?board=`` that silently read the active
# board would report the wrong workers and reclaim the wrong tasks.

#: Shared query parameter, so every endpoint documents the override identically.
_BOARD_Q = Query(None, description="Board slug (default: the current board)")


def _kb():
    from hermes_cli import kanban_db as kanban_db

    return kanban_db


@contextmanager
def _value_error_400() -> Iterator[None]:
    """Domain-layer refusals (``ValueError``) are bad requests, not crashes."""
    try:
        yield
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@contextmanager
def _errors_to_500(prefix: str) -> Iterator[None]:
    """Map an unexpected exception to ``500 "<prefix>: <exc>"``."""
    try:
        yield
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{prefix}: {exc}") from exc


def _normalize_slug_or_400(slug: str | None) -> str | None:
    with _value_error_400():
        return _kb()._normalize_board_slug(slug)


def _resolve_board(board: str | None) -> str | None:
    """A ``board`` query param, validated; ``None`` when omitted so the
    connection falls through to the active board."""
    if board is None or board == "":
        return None
    kb = _kb()
    normed = _normalize_slug_or_400(board)
    if normed and normed != kb.DEFAULT_BOARD and not kb.board_exists(normed):
        raise HTTPException(status_code=404, detail=f"board {normed!r} does not exist")
    return normed


def _existing_board_slug(slug: str) -> str:
    """A path slug, normalized and required to exist (400 / 404)."""
    kb = _kb()
    normed = _normalize_slug_or_400(slug)
    if not normed or not kb.board_exists(normed):
        raise HTTPException(status_code=404, detail=f"board {slug!r} does not exist")
    return normed


def _kanban_conn(board: str | None = None):
    """Connection to ``board``'s database (``None`` = the active board)."""
    from hermes_cli import kanban_db_connect as kbc

    return kbc.connect_closing(board=board)


def _running_count(conn) -> int:
    row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()
    return int(row[0] if row else 0)


def _state_payload(board: str | None) -> dict[str, Any]:
    pkg = _package()
    configured, explicit = pkg.dispatch_guard.configured_cap()
    with _kanban_conn(board) as conn:
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
        "plugin_version": "0.2.1",
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
def get_state(board: str | None = _BOARD_Q):
    """Board switch, worker count and the parallel-run cap in one call."""
    return _state_payload(_resolve_board(board))


@router.post("/stop")
def stop_board(payload: StopBody | None = None, board: str | None = _BOARD_Q):
    """Stop the board: persist the switch first, then reclaim running workers."""
    pkg = _package()
    slug = _resolve_board(board)
    with _kanban_conn(slug) as conn:
        result = pkg.board_control.stop(conn, board=slug, reason=(payload.reason if payload else None))
    return {**_state_payload(slug), "reclaimed": result.reclaimed, "failed": result.failed}


@router.post("/start")
def start_board(board: str | None = _BOARD_Q):
    """Lift the switch and dispatch once, within the configured cap."""
    pkg = _package()
    slug = _resolve_board(board)
    pkg.board_control.start(board=slug)
    spawned: list = []
    try:
        kbd = pkg.core.kanban_dispatch()
        with _kanban_conn(slug) as conn:
            spawned = [entry[0] for entry in kbd.dispatch_once(
                conn, board=slug, max_in_progress=pkg.dispatch_guard.effective_cap()).spawned]
    except Exception:
        spawned = []
    return {**_state_payload(slug), "spawned": spawned}


@router.put("/max-parallel")
def set_max_parallel(payload: MaxParallelBody, board: str | None = _BOARD_Q):
    """Write ``kanban.max_in_progress``; ``0`` means explicitly unbounded."""
    slug = _resolve_board(board)
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
    return _state_payload(slug)


@router.put("/model")
def set_board_model(payload: BoardModelBody, board: str | None = _BOARD_Q):
    """Set (or clear) the model every task run and the auto-composer use."""
    pkg = _package()
    slug = _resolve_board(board)
    pkg.board_model.set_model(payload.model, payload.provider, board=slug)
    return _state_payload(slug)


#: The live board columns, in core's own order (``BOARD_COLUMNS`` in
#: ``plugins/kanban/dashboard/plugin_api.py``). A status a later Hermes adds
#: lands in ``todo``, the same fallback core uses.
BOARD_COLUMNS = ("triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done")


def _task_row(task) -> dict[str, Any]:
    """The card fields every surface in this plugin renders."""
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "assignee": task.assignee,
        "priority": getattr(task, "priority", 0),
        "tenant": getattr(task, "tenant", None),
        "started_at": task.started_at,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


@router.get("/tasks")
def list_tasks(board: str | None = _BOARD_Q, limit: int = Query(40, ge=1, le=200)):
    """Running tasks first, then the most recently touched ones."""
    from hermes_cli import kanban_db as kb

    slug = _resolve_board(board)
    with _kanban_conn(slug) as conn:
        running = [_task_row(t) for t in kb.list_tasks(conn, status="running")]
        recent = [_task_row(t) for t in kb.list_tasks(conn, limit=limit)]
    seen = {row["id"] for row in running}
    return {"running": running, "recent": [row for row in recent if row["id"] not in seen]}


@router.get("/board")
def get_board(board: str | None = _BOARD_Q, include_archived: bool = Query(False)):
    """The board's own columns for the picked board — the lanes the page draws.

    Same grouping core's ``GET /board`` does (column order, ``todo`` fallback,
    priority-then-age ordering inside a lane), with the per-card rollups the
    page actually renders and none of the ones it does not.
    """
    from hermes_cli import kanban_db as kb

    slug = _resolve_board(board)
    names = [*BOARD_COLUMNS, "archived"] if include_archived else list(BOARD_COLUMNS)
    columns: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    comment_counts: dict[str, int] = {}
    progress: dict[str, dict[str, int]] = {}
    with _kanban_conn(slug) as conn:
        # Per-column ordering (priority DESC, created_at ASC) comes from list_tasks.
        tasks = kb.list_tasks(conn, include_archived=include_archived)
        # Two aggregates rather than two queries per card.
        try:
            comment_counts = {row["task_id"]: int(row["n"]) for row in conn.execute(
                "SELECT task_id, COUNT(*) AS n FROM task_comments GROUP BY task_id").fetchall()}
            for row in conn.execute(
                    "SELECT l.parent_id AS pid, t.status AS cstatus FROM task_links l "
                    "JOIN tasks t ON t.id = l.child_id").fetchall():
                entry = progress.setdefault(row["pid"], {"done": 0, "total": 0})
                entry["total"] += 1
                entry["done"] += int(row["cstatus"] == "done")
        except Exception:  # a Hermes whose schema moved these — cards lose a chip, not the lane
            _package().core.logger.debug("card rollups unavailable", exc_info=True)
    for task in tasks:
        card = _task_row(task)
        card["comment_count"] = comment_counts.get(task.id, 0)
        card["progress"] = progress.get(task.id)  # None when the task has no children
        columns[task.status if task.status in columns else "todo"].append(card)
    return {
        "columns": [{"name": name, "tasks": columns[name]} for name in names],
        "now": int(time.time()),
    }


@router.get("/tasks/{task_id}/log")
def task_log(task_id: str, board: str | None = _BOARD_Q,
             tail: int = Query(1_048_576, ge=1, le=8_388_608),
             timestamps: bool = Query(True)):
    """Worker log. ``timestamps=true`` keeps each line's ``[<ISO time>] `` stamp."""
    from hermes_cli import kanban_db as kb

    pkg = _package()
    slug = _resolve_board(board)
    try:
        content = kb.read_worker_log(task_id, tail_bytes=tail, board=slug, timestamps=timestamps)
    except TypeError:
        # A Hermes build whose read_worker_log has no ``timestamps`` keyword and
        # a process where our reader patch is not installed.
        content = kb.read_worker_log(task_id, tail_bytes=tail, board=slug)
        if not timestamps and content:
            content = pkg.log_stamps.strip_timestamps(content)
    path = kb.worker_log_path(task_id, board=slug)
    size = path.stat().st_size if path.exists() else 0
    return {
        "task_id": task_id,
        "exists": content is not None,
        "content": content or "",
        "size_bytes": size,
        "truncated": bool(size > tail),
    }


@router.get("/tasks/{task_id}/context")
def task_context(task_id: str, board: str | None = _BOARD_Q):
    """The worker's latest context-window snapshot (written every ~15 s)."""
    pkg = _package()
    slug = _resolve_board(board)
    snapshot = pkg.worker_context.read_snapshot(task_id, slug)
    if not snapshot:
        return {"task_id": task_id, "available": False}
    live = False
    try:
        from hermes_cli import kanban_db as kb

        with _kanban_conn(slug) as conn:
            task = kb.get_task(conn, task_id)
        live = bool(task and task.status == "running")
    except Exception:
        live = False
    return {**snapshot, "available": True, "live": live}


# --- Boards: the board directory itself -------------------------------------
# Same contract as core's kanban plugin API (``plugins/kanban/dashboard/
# plugin_api.py``), because the desktop half of THIS plugin can only talk to
# THIS namespace — a board the page can switch to is a board it must be able to
# list, create, rename, scope, transfer and archive here.

class CreateBoardBody(BaseModel):
    slug: str
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    default_workdir: str | None = None
    # Project (id or slug) scoping the board: default_workdir mirrors its primary repo.
    project_id: str | None = None
    switch: bool = False


class RenameBoardBody(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    # For both fields: ``None`` = leave unchanged; "" = clear; value = validate/resolve + set.
    default_workdir: str | None = None
    project_id: str | None = None


# Board transfer exchanges filesystem PATHS, not bytes: the picker runs on the
# machine hosting this backend, so the backend reads and writes the archive.

class ExportBoardBody(BaseModel):
    output: str = ""  # empty → staging path under the kanban root
    attachments: bool = True
    logs: bool = False


class ImportBoardBody(BaseModel):
    archive: str  # path to a board .tar.gz on the backend's filesystem
    slug: str | None = None  # override the archive's slug; collisions auto-suffix
    switch: bool = False


def _board_display_kwargs(payload: BaseModel) -> dict[str, Any]:
    """Display-metadata fields shared by create_board / write_board_metadata."""
    return {"name": payload.name, "description": payload.description,
            "icon": payload.icon, "color": payload.color}


def _resolve_project(ref: str | None) -> tuple[str | None, str | None, str | None]:
    """Resolve a project id/slug to ``(id, name, primary_path)``; ``(None,)*3``
    for a falsy ref, 400 when a non-empty ref does not resolve."""
    if not ref or not ref.strip():
        return None, None, None
    with _errors_to_500("projects unavailable"):
        from hermes_cli import projects_db as pdb

        with pdb.connect_closing() as pconn:
            proj = pdb.get_project(pconn, ref.strip())
    if proj is None:
        raise HTTPException(status_code=400, detail=f"project {ref!r} does not exist")
    return proj.id, proj.name, (proj.primary_path or None)


def _projects_by_id() -> dict[str, Any]:
    """Every project id -> Project (archived included), for annotation."""
    try:
        from hermes_cli import projects_db as pdb

        with pdb.connect_closing() as pconn:
            return {p.id: p for p in pdb.list_projects(pconn, include_archived=True)}
    except Exception:
        return {}


def _board_counts(slug: str) -> dict[str, int]:
    """``{status: count}`` for a board; ``{}`` on a missing or empty DB."""
    try:
        if not _kb().kanban_db_path(board=slug).exists():
            return {}
        with _kanban_conn(slug) as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        return {row["status"]: int(row["n"]) for row in rows}
    except Exception:
        return {}


def _default_workspace_kind(board: dict[str, Any]) -> str:
    """Recommend a non-destructive task workspace from board metadata."""
    workdir = str(board.get("default_workdir") or "").strip()
    if not workdir:
        return "scratch"
    try:
        from hermes_cli import kanban_db_workspace as kbw

        return "worktree" if kbw._git_toplevel(Path(workdir)) else "dir"
    except Exception:
        return "dir"


def _annotate_board_meta(meta: dict) -> dict:
    meta["default_workspace_kind"] = _default_workspace_kind(meta)
    _, meta["project_name"], _ = _resolve_project(meta.get("project_id"))
    return meta


def _validate_workdir(raw: str) -> str:
    """A board's default_workdir must be an absolute, existing directory."""
    requested = Path(raw).expanduser()
    if not requested.is_absolute():
        raise HTTPException(status_code=400, detail="Project directory must be an absolute path.")
    if not requested.is_dir():
        raise HTTPException(status_code=400, detail="Project directory must be an existing directory.")
    return str(requested.resolve())


@router.get("/projects")
def list_kanban_projects():
    """Live (non-archived) projects available for board scoping."""
    with _errors_to_500("failed to list projects"):
        from hermes_cli import projects_db as pdb

        with pdb.connect_closing() as pconn:
            projects = pdb.list_projects(pconn, include_archived=False)
    return {"projects": [
        {"id": p.id, "slug": p.slug, "name": p.name,
         "primary_path": p.primary_path or "", "icon": p.icon or "", "color": p.color or ""}
        for p in projects]}


@router.get("/boards")
def list_boards(include_archived: bool = Query(False)):
    """Every board on disk with task counts, this plugin's stop switch and the
    active slug."""
    pkg = _package()
    kb = _kb()
    boards = kb.list_boards(include_archived=include_archived)
    current = kb.get_current_board()
    proj_map = _projects_by_id()
    for entry in boards:
        slug = entry["slug"]
        entry["is_current"] = (slug == current)
        entry["counts"] = _board_counts(slug)
        # Live cards only — archived tasks are hidden from every default board
        # view, so counting them in the switcher badge would visibly disagree.
        entry["total"] = sum(n for status, n in entry["counts"].items() if status != "archived")
        entry["default_workspace_kind"] = _default_workspace_kind(entry)
        # This plugin's own per-board state, so the switcher can flag a board
        # that is stopped without a round trip per row.
        entry["stopped"] = pkg.board_control.is_stopped(slug)
        pid = entry["project_id"] = entry.get("project_id") or None
        proj = proj_map.get(pid) if pid else None
        entry["project_name"] = proj.name if proj else None
    return {"boards": boards, "current": current}


@router.post("/boards")
def create_board_endpoint(payload: CreateBoardBody):
    """Create a board. Idempotent — a ``slug`` collision returns the existing one."""
    kb = _kb()
    default_workdir = _validate_workdir(payload.default_workdir) if payload.default_workdir else None
    # A chosen project's primary repo becomes the default workdir unless one was passed explicitly.
    project_id, _name, primary_path = _resolve_project(payload.project_id)
    if primary_path and not default_workdir:
        default_workdir = primary_path
    with _value_error_400():
        meta = kb.create_board(
            payload.slug, default_workdir=default_workdir, project_id=project_id,
            **_board_display_kwargs(payload))
    if payload.switch:
        with _value_error_400():
            kb.set_current_board(meta["slug"])
    return {"board": _annotate_board_meta(meta), "current": kb.get_current_board()}


@router.patch("/boards/{slug}")
def rename_board(slug: str, payload: RenameBoardBody):
    """Update display metadata / default workdir / project scope (slug is immutable)."""
    kb = _kb()
    normed = _existing_board_slug(slug)
    # write_board_metadata treats a falsy value as "clear", so pass "" through.
    default_workdir: str | None = None
    if payload.default_workdir is not None:
        raw = payload.default_workdir.strip()
        default_workdir = _validate_workdir(raw) if raw else ""
    # A resolved project mirrors its repo into default_workdir unless the caller set it explicitly.
    project_id: str | None = None
    if payload.project_id is not None:
        if payload.project_id.strip():
            project_id, _name, primary_path = _resolve_project(payload.project_id)
            if primary_path and default_workdir is None:
                default_workdir = primary_path
        else:
            project_id = ""  # clear the scope
    with _value_error_400():
        meta = kb.write_board_metadata(
            normed, default_workdir=default_workdir, project_id=project_id,
            **_board_display_kwargs(payload))
    return {"board": _annotate_board_meta(meta)}


@router.delete("/boards/{slug}")
def delete_board(slug: str, delete: bool = Query(False, description="Hard-delete instead of archive")):
    """Archive (default) or hard-delete a board. ``default`` cannot be removed."""
    kb = _kb()
    with _value_error_400():
        result = kb.remove_board(slug, archive=not delete)
    return {"result": result, "current": kb.get_current_board()}


async def _run_transfer(fn, log_label: str):
    """Run a blocking kanban_transfer call off the event loop, mapping its
    errors to 404 (missing path) / 400 (invalid) / 500."""
    try:
        return await asyncio.get_running_loop().run_in_executor(None, fn)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _package().core.logger.warning("%s failed", log_label, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/boards/{slug}/export")
async def export_board_endpoint(slug: str, body: ExportBoardBody):
    """Write ``slug`` to a portable archive; return the path written."""
    from hermes_cli import kanban_transfer

    kb = _kb()
    normed = _existing_board_slug(slug)
    output = (body.output or "").strip()
    if not output:
        staging = kb.kanban_home() / "kanban" / "board-exports"
        try:
            staging.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not create export directory: {exc}") from exc
        output = str(staging / f"{normed}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz")
    return await _run_transfer(
        lambda: kanban_transfer.export_board(
            normed, output, include_attachments=body.attachments, include_logs=body.logs),
        f"POST /boards/{normed}/export")


@router.post("/boards/import")
async def import_board_endpoint(body: ImportBoardBody):
    """Import a board archive as a NEW board; return the landed board."""
    from hermes_cli import kanban_transfer

    archive = (body.archive or "").strip()
    if not archive:
        raise HTTPException(status_code=400, detail="archive path is required")
    slug = (body.slug or "").strip() or None
    result = await _run_transfer(
        lambda: kanban_transfer.import_board(archive, slug, activate=body.switch),
        "POST /boards/import")
    return {**result, "current": _kb().get_current_board()}


@router.post("/boards/{slug}/switch")
def switch_board(slug: str):
    """Persist ``slug`` as the active board, for CLI parity — the desktop page
    picks its board client-side and only sends this when asked to."""
    kb = _kb()
    normed = _existing_board_slug(slug)
    kb.set_current_board(normed)
    return {"current": normed}
