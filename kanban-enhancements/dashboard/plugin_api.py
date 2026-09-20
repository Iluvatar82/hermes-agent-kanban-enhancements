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


def _same_file(left: str, right: str) -> bool:
    """Whether two paths name one file, spelling aside. Never raises: a path
    that has since been deleted is simply not a match."""
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False

#: The version of the code THIS PROCESS imported — deliberately a literal and
#: not a read of plugin.yaml, because an update swaps that file while this
#: module stays loaded and the difference is what "restart required" means.
#: tests/test_manifest.py keeps it equal to the manifest.
_PLUGIN_VERSION = "0.6.0"


def _package():
    """The plugin package: the loader's own copy when it is already imported.

    Matched on the RESOLVED ``__file__``. The loader stores the path exactly as
    discovery spelled it, this file resolves its own — on Windows the two can
    differ by case, by an 8.3 short name or by a junction, and a miss meant a
    second, never-registered copy of the package whose patch flags all read
    False while the real ones were live (the "board model is set but patches
    nowhere" banner).
    """
    for module in list(sys.modules.values()):
        candidate = getattr(module, "__file__", None)
        if not candidate or not hasattr(module, "board_control"):
            continue
        if candidate == _PKG_INIT or _same_file(candidate, _PKG_INIT):
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


# --- Updating this plugin from inside it -------------------------------------
# The desktop's own plugin surface can reinstall this plugin, but only one
# profile at a time and only through a modal that never says an update exists —
# its "update available" badge is catalog-only, and this plugin is a plain git
# install. So Kanban+ answers the question itself: what is running, what is on
# disk, what the source repository has, and one POST that closes the gap.
#
# The source is NEVER taken from the request. It comes from Hermes' own install
# metadata (a fork updates from the fork), so this endpoint can only reinstall
# THIS plugin from the place it already came from.


class UpdateBody(BaseModel):
    """``ref`` pins one immutable commit, exactly like ``--ref``; empty = the
    source's default branch. ``all_profiles`` installs into every Hermes
    profile instead of only the one this gateway runs under — what
    ``scripts/update.ps1`` does, from the page."""

    ref: str | None = Field(None, max_length=64)
    all_profiles: bool = False


def _version_payload(check: bool) -> dict[str, Any]:
    pkg = _package()
    source = pkg.self_update.installed_source()
    installed = pkg.self_update.installed_version()
    running = _PLUGIN_VERSION
    latest, error = ("", "")
    if check:
        latest, error = pkg.self_update.fetch_latest_version(source)
    return {
        "running": running,
        "installed": installed,
        "latest": latest or None,
        "source": source,
        "can_update": pkg.self_update.available(),
        "update_available": pkg.self_update.is_newer(latest, installed),
        # On disk but not in memory: the update landed and the gateway is still
        # serving the previous code.
        "restart_required": bool(installed and running and installed != running),
        "check_error": error or None,
    }


@router.get("/version")
def get_version(check: bool = Query(True, description="Also ask the source repository for its version")):
    """What is running, what is installed, and what the source repository has."""
    pkg = _package()
    # The setting only governs the automatic probe; an explicit `check=true` from
    # the update button still asks.
    wanted = check and bool(pkg.core.setting("update_check", True))
    return _version_payload(wanted)


@router.get("/profiles")
def get_profiles():
    """Every Hermes profile and the plugin version installed in it.

    What the update row needs to offer "all profiles" honestly: which profiles
    exist, which one this gateway runs under, and which of them are behind.
    ``supported: false`` means this Hermes cannot be pointed at another
    profile's home from here — the page then keeps the single-profile button.
    """
    pkg = _package()
    return {
        "supported": pkg.profile_update.supported(),
        "running": _PLUGIN_VERSION,
        "profiles": pkg.profile_update.list_profiles(),
    }


async def _update_every_profile(pkg, source: str, ref: str | None) -> dict[str, Any]:
    """Install into every profile, off the event loop (a git clone each)."""
    if not pkg.profile_update.supported():
        raise HTTPException(
            status_code=503,
            detail="this Hermes does not expose hermes_constants.set_hermes_home_override, so Kanban+ "
                   "cannot install into another profile from here; use scripts/update.ps1")
    with _errors_to_500("update failed"):
        rows = await asyncio.get_running_loop().run_in_executor(
            None, lambda: pkg.profile_update.install_all(source, ref))
    if not rows:
        raise HTTPException(status_code=500, detail="no Hermes profile could be resolved")
    failed = [row["name"] for row in rows if not row.get("ok")]
    installed = next((row["installed"] for row in rows if row.get("current")), "")
    return {
        **_version_payload(False),
        # Partial success is still a failure to report: the rows say which
        # profile refused, and nothing pretends the other ones did not land.
        "ok": not failed,
        "installed": installed,
        "restart_required": bool(installed and installed != _PLUGIN_VERSION),
        "profiles": rows,
        "failed": failed,
        "warnings": sorted({warning for row in rows for warning in (row.get("warnings") or [])}),
    }


@router.post("/update")
async def update_plugin(payload: UpdateBody | None = None):
    """Reinstall this plugin from its own source, then report what landed.

    Installing REPLACES the directory this module was imported from, so the
    gateway keeps serving the old code until it restarts — ``restart_required``
    in the response says so rather than leaving the caller to guess.
    ``all_profiles`` does the same for every profile on the machine and answers
    with one row per profile.
    """
    pkg = _package()
    if not pkg.self_update.available():
        raise HTTPException(
            status_code=503,
            detail="this Hermes does not expose hermes_cli.plugins_cmd.dashboard_install_plugin, "
                   "so Kanban+ cannot update itself; use `hermes plugins install ... --force --enable`")
    with _value_error_400():
        ref = pkg.self_update.normalize_ref(payload.ref if payload else None)
    source = pkg.self_update.installed_source()
    if payload is not None and payload.all_profiles:
        return await _update_every_profile(pkg, source, ref)
    with _errors_to_500("update failed"):
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: pkg.self_update.install(source, ref))
    if not result.get("ok"):
        # The installer reports a refusal (a blocked scan, a bad ref, a
        # network failure) in its payload rather than by raising.
        detail = str(result.get("error") or "the installer refused the update")
        raise HTTPException(status_code=502, detail=detail)
    installed = str(result.get("installed_version") or "")
    return {
        **_version_payload(False),
        "ok": True,
        "installed": installed,
        "restart_required": bool(installed and installed != _PLUGIN_VERSION),
        "warnings": result.get("warnings") or [],
    }


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
    # Repair before reporting. The patches are installed at plugin load, which
    # in a gateway is the one moment the modules they wrap may not be
    # importable yet; re-installing here is idempotent (a marker read when
    # everything is in place) and means opening the page fixes a board model
    # that would otherwise have stayed inert until the next gateway restart.
    pkg.ensure_patches()
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
        "plugin_version": _PLUGIN_VERSION,
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


@router.get("/assignees")
def list_assignees(board: str | None = _BOARD_Q):
    """Profiles on disk plus everyone this board already assigns to — the same
    union core's picker offers, so a fresh profile is pickable before it has a
    task."""
    from hermes_cli import kanban_db as kb

    slug = _resolve_board(board)
    with _kanban_conn(slug) as conn:
        return {"assignees": kb.known_assignees(conn)}


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


# --- One task: the detail view, its status, its comments --------------------
# The desktop page's task view mirrors core's own task drawer, so it needs the
# same payload. Reading it is pure ``kanban_db``; WRITING a status is not — the
# transition rules (reclaim a running worker, re-gate ``ready`` on its parents,
# close the open run, refuse a locked lane) live in core's kanban plugin API and
# are far too much machinery to re-implement here from private helpers. So a
# status change is DELEGATED to that module, and when a Hermes update moves it
# the page says so instead of writing a half-correct row.

#: Columns the detail view has no business showing: two are the worker's claim
#: credentials, the third is a dedupe key for the writer, not the reader.
_TASK_PRIVATE_FIELDS = ("claim_lock", "claim_expires", "idempotency_key")

#: A years-old task can carry thousands of events; the view shows a feed, not an
#: archive, so only the newest slice travels (still oldest-first, like core's).
_MAX_EVENTS = 200

#: Where the dashboard parks core's kanban plugin API once it mounts it
#: (``_mount_plugin_api_routes`` in ``hermes_cli/web_server_dashboard.py``).
_CORE_API_MODULE = "hermes_dashboard_plugin_kanban"
_CORE_API_SYNTHETIC = "hermes_kanban_core_api_for_plus"


def _core_kanban_api():
    """Core's kanban plugin API module, or ``None`` when it cannot be reached.

    Normally it is already in ``sys.modules``: both namespaces are mounted into
    the same FastAPI app by the same loader. The by-path load is the fallback
    for a process that imported ours but not core's.
    """
    module = sys.modules.get(_CORE_API_MODULE) or sys.modules.get(_CORE_API_SYNTHETIC)
    if module is not None and hasattr(module, "update_task"):
        return module
    try:
        from hermes_cli.plugins import get_bundled_plugins_dir

        api_path = get_bundled_plugins_dir() / "kanban" / "dashboard" / "plugin_api.py"
        if not api_path.is_file():
            return None
        spec = importlib.util.spec_from_file_location(_CORE_API_SYNTHETIC, api_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        # Registered before exec so pydantic can resolve the module's own
        # postponed annotations by name — the same order the loader uses.
        sys.modules[_CORE_API_SYNTHETIC] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(_CORE_API_SYNTHETIC, None)
            raise
    except Exception:
        _package().core.logger.warning("core kanban API unavailable", exc_info=True)
        return None
    return module if hasattr(module, "update_task") else None


def _core_kanban_api_or_503():
    core = _core_kanban_api()
    if core is None or not hasattr(core, "UpdateTaskBody"):
        raise HTTPException(
            status_code=503,
            detail="the core kanban plugin API is not available in this process, "
                   "so Kanban+ cannot move a task; enable the bundled 'kanban' plugin")
    return core


class TaskPatchBody(BaseModel):
    """What the task view may change. Mirrors the subset of core's
    ``UpdateTaskBody`` this plugin's page actually offers; unsent fields are
    left alone (``exclude_unset`` below), and ``clear_model_override`` is the
    explicit "back to the profile's model" signal a ``None`` cannot carry.

    ``skills`` is the one exception and :data:`_OWN_PATCH_FIELDS` names it:
    core's update body has no such field, so that half is applied by this
    plugin's own ``task_skills`` writer (which is why it may be edited at all).
    """

    status: str | None = Field(None, max_length=50)
    title: str | None = Field(None, max_length=500)
    body: str | None = Field(None, max_length=100_000)
    assignee: str | None = Field(None, max_length=200)
    priority: int | None = Field(None, ge=-1_000_000, le=1_000_000)
    model_override: str | None = Field(None, max_length=200)
    provider_override: str | None = Field(None, max_length=100)
    clear_model_override: bool = False
    #: ``[]`` clears the task's skills; an unsent field leaves them alone.
    skills: list[str] | None = Field(None, max_length=50)


#: Patch fields Kanban+ writes ITSELF instead of handing to core — they are
#: deliberately absent from ``UpdateTaskBody``, so the drift test that holds
#: this body against core's excludes exactly these.
_OWN_PATCH_FIELDS = frozenset({"skills"})


# The lanes the dispatcher hands out. Core refuses a bare status move into
# any of them with a 409, so an add that targets one is refused HERE instead
# of creating the task and then failing to move it.
_DISPATCHER_LANES = frozenset({"review", "running", "scheduled"})


class TaskCreateBody(BaseModel):
    """What the board's per-lane ``+`` offers. Every field but ``status`` is a
    field of core's own ``CreateTaskBody`` and is handed to it untouched;
    ``status`` is the lane the button sat in, which core's create does not take
    — it derives ``triage``/``todo``/``ready`` itself, and the rest is a move."""

    title: str = Field(..., min_length=1, max_length=500)
    body: str | None = Field(None, max_length=100_000)
    assignee: str | None = Field(None, max_length=200)
    priority: int = Field(0, ge=-1_000_000, le=1_000_000)
    status: str | None = Field(None, max_length=50)
    workspace_kind: str | None = Field(None, max_length=50)
    workspace_path: str | None = Field(None, max_length=4_096)
    parents: list[str] = Field(default_factory=list, max_length=50)
    skills: list[str] | None = Field(None, max_length=50)
    goal_mode: bool = False
    model_override: str | None = Field(None, max_length=200)
    provider_override: str | None = Field(None, max_length=100)


class TaskCommentBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=20_000)
    author: str = Field("desktop", max_length=100)


def _task_detail_dict(task) -> dict[str, Any]:
    """Every ``Task`` field the view can render, minus the private ones."""
    from dataclasses import asdict

    detail = asdict(task)
    for field in _TASK_PRIVATE_FIELDS:
        detail.pop(field, None)
    return detail


def _require_task(conn, task_id: str):
    from hermes_cli import kanban_db as kb

    task = kb.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} does not exist")
    return task


@router.get("/tasks/{task_id}")
def get_task_detail(task_id: str, board: str | None = _BOARD_Q):
    """One task with everything the detail view shows: the row itself, its
    comments, its activity feed, its run history and its dependency links."""
    from dataclasses import asdict

    from hermes_cli import kanban_db as kb

    slug = _resolve_board(board)
    with _kanban_conn(slug) as conn:
        task = _require_task(conn, task_id)
        detail = _task_detail_dict(task)
        # Workers hand off through ``task_runs.summary`` and leave ``result``
        # NULL, so a done task looks empty without this.
        detail["latest_summary"] = kb.latest_summary(conn, task_id)
        comments = [asdict(c) for c in kb.list_comments(conn, task_id)]
        events = [asdict(e) for e in kb.list_events(conn, task_id)][-_MAX_EVENTS:]
        runs = [asdict(r) for r in kb.list_runs(conn, task_id)]
        links = {"parents": kb.parent_ids(conn, task_id), "children": kb.child_ids(conn, task_id)}
    return {"task": detail, "comments": comments, "events": events, "runs": runs, "links": links}


@router.post("/tasks")
def create_task(payload: TaskCreateBody, board: str | None = _BOARD_Q):
    """Create a task in a lane — the board's per-lane ``+``.

    Delegated to core's kanban API so the defaults, the validation and the
    dispatcher-presence warning are the ones every other surface gets. Core
    derives the new task's status (``triage`` when the lane is Triage, else
    ``ready``, or ``todo`` behind an unfinished parent), so landing it in the
    lane that was clicked is a second call — the same two steps core's own
    board page takes.
    """
    core = _core_kanban_api_or_503()
    if not hasattr(core, "CreateTaskBody"):
        raise HTTPException(
            status_code=501, detail="this Hermes' kanban API cannot create tasks")

    slug = _resolve_board(board)
    fields = payload.model_dump(exclude_unset=True)
    target = (fields.pop("status", None) or "").strip() or None
    if target in _DISPATCHER_LANES:
        raise HTTPException(
            status_code=400,
            detail=f"{target} is the dispatcher's to hand out; create the task in ready instead")
    # Triage is not a move: core's create reaches it through this flag alone.
    if target == "triage":
        fields["triage"] = True
    unknown = sorted(set(fields) - set(core.CreateTaskBody.model_fields))
    if unknown:
        raise HTTPException(
            status_code=501,
            detail=f"this Hermes' kanban API does not accept: {', '.join(unknown)}")

    with _errors_to_500("could not create the task"):
        result = core.create_task(core.CreateTaskBody(**fields), board=slug)
    task = (result or {}).get("task")
    # A lane core did not derive on its own is a move, and it goes through the
    # same seam a drag does — transition rules included. A refused move leaves
    # the task where it landed rather than losing it: the response says where,
    # and the page surfaces the warning the way it surfaces core's own.
    if task and target and task.get("status") != target:
        try:
            with _errors_to_500("could not move the new task"):
                core.update_task(task["id"], core.UpdateTaskBody(status=target), board=slug)
            task["status"] = target
        except HTTPException as exc:
            result["warning"] = (
                f"created in {task.get('status')}, but the move to {target} was refused: {exc.detail}")
    return result


def _write_skills(task_id: str, skills: list[str] | None, slug: str | None) -> list[str] | None:
    """The half core cannot do: ``tasks.skills``, written by this plugin.

    404 for a task that is gone, 400 for a name core refuses or an archived
    task — the same answers the delegated half gives for the same mistakes.
    """
    pkg = _package()
    with _kanban_conn(slug) as conn:
        try:
            return pkg.task_skills.set_skills(conn, task_id, skills)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=f"task {task_id} does not exist") from exc
        except (RuntimeError, ValueError) as exc:
            # A name core refuses, or an archived task: the caller's mistake,
            # and the message is the one `kanban create --skills` would print.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"could not update the task: {exc}") from exc


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, payload: TaskPatchBody, board: str | None = _BOARD_Q):
    """Move a task to another column (drag & drop, the card menu, the status
    menu) or edit one of its fields — delegated to core's kanban API so the
    transition rules are the ones the rest of Hermes enforces.

    ``skills`` is the one field core's API does not carry; it is written here
    (see ``task_skills``) and left out of what is handed to core. A patch that
    carries both does the delegated half first, so a refused status move never
    leaves the skills edited on a task that did not move.
    """
    core = _core_kanban_api_or_503()
    slug = _resolve_board(board)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to change")
    # `skills: null` is a clear, not "unsent" — the sent/unsent line is the
    # model's own field set, never the value.
    edits_skills = "skills" in payload.model_fields_set
    skills = fields.pop("skills", None)
    unknown = sorted(set(fields) - set(core.UpdateTaskBody.model_fields))
    if unknown:
        raise HTTPException(
            status_code=501,
            detail=f"this Hermes' kanban API does not accept: {', '.join(unknown)}")

    result: dict[str, Any] = {}
    if fields:
        with _errors_to_500("could not update the task"):
            result = core.update_task(task_id, core.UpdateTaskBody(**fields), board=slug) or {}
    if edits_skills:
        stored = _write_skills(task_id, skills or [], slug)
        task = result.get("task")
        if isinstance(task, dict):
            task["skills"] = stored
        else:
            result = {**result, "task": {"id": task_id, "skills": stored}}
    return result


@router.post("/tasks/{task_id}/comments")
def add_task_comment(task_id: str, payload: TaskCommentBody, board: str | None = _BOARD_Q):
    """Add a comment. A running worker reads new comments from its context, so
    this is also how an operator nudges one mid-run."""
    from hermes_cli import kanban_db as kb

    slug = _resolve_board(board)
    with _kanban_conn(slug) as conn:
        _require_task(conn, task_id)
        with _value_error_400():
            comment_id = kb.add_comment(conn, task_id, payload.author, payload.body)
    return {"id": comment_id, "task_id": task_id}


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
