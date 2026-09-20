"""Per-task skills, editable after the task was created.

This is the one field the task view cannot hand to core. Everything else it
edits goes through core's kanban API (``plugins/kanban/dashboard/plugin_api.py``
— see ``PATCH /tasks/{id}``), but ``skills`` is set exactly once, by
``kanban_db.create_task``, and no surface in Hermes touches it again: not
``UpdateTaskBody``, not ``hermes kanban``, not an agent tool. A skill that was
misspelled — or that simply does not exist — therefore used to cost the whole
task: delete it, type it again.

So this module writes ``tasks.skills`` itself, the way core's own per-task
override writers (``kanban_db.set_model_override``, ``set_reasoning_effort``)
write theirs:

* core's ``_normalize_task_skills`` decides what a name may be, so the page is
  refused for exactly what ``kanban create --skills`` is refused for (a comma
  inside a name, a toolset name where a skill belongs);
* an archived task is refused, a missing one answers ``False``;
* the row and its event share one transaction;
* ``on_kanban_task_updated`` fires only after that transaction committed.

Skills reach the worker as ``--skills`` at spawn, so an edit applies to the
NEXT run — the same contract the model override has, and the reason a running
task may be edited rather than blocked.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import Any

from . import core
from .core import logger

#: The event this writes, named after core's own ``model_override_set``.
EVENT_KIND = "skills_set"


def _fallback_normalize(skills: Iterable[str]) -> list[str]:
    """What core's ``_normalize_task_skills`` does, for a Hermes that moved it.

    The toolset-name check is core's alone and is not guessed at here — this
    only keeps the two invariants a stored value must have: no blanks, no
    duplicates, and never a comma (each name is one ``--skills`` argv slot).
    """
    cleaned: list[str] = []
    for entry in skills:
        name = str(entry or "").strip()
        if not name or name in cleaned:
            continue
        if "," in name:
            raise ValueError(
                f"skill name cannot contain comma: {name!r} "
                "(pass a list of separate names instead of a comma-joined string)")
        cleaned.append(name)
    return cleaned


def normalize(skills: Iterable[str] | None) -> list[str] | None:
    """Core's normalizer where it exists, ours where it does not.

    ``None`` stays ``None`` (the column's "defaults only"); a list comes back
    stripped and deduplicated. Raises ``ValueError`` for a name core refuses.
    """
    if skills is None:
        return None
    core_normalize = getattr(core.kanban_db(), "_normalize_task_skills", None)
    if core_normalize is None:
        logger.debug("kanban_db._normalize_task_skills is gone — using the local fallback")
        return _fallback_normalize(skills)
    return core_normalize(skills)


def _status(conn, task_id: str) -> str | None:
    row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    return str(row["status"] if hasattr(row, "keys") else row[0])


def _record_event(conn, task_id: str, skills: list[str] | None) -> None:
    """Append the event inside the caller's transaction, through core's helper
    when it is there and by hand when it is not — an edit nobody can see in the
    activity feed is an edit nobody can explain later."""
    payload: dict[str, Any] = {"skills": list(skills or [])}
    append = getattr(core.kanban_db(), "_append_event", None)
    if append is not None:
        append(conn, task_id, EVENT_KIND, payload)
        return
    conn.execute(
        "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?, NULL, ?, ?, ?)",
        (task_id, EVENT_KIND, json.dumps(payload), int(time.time())))


def _notify(conn, task_id: str) -> None:
    """``on_kanban_task_updated``, after the commit. Best effort: an observer
    that fails must not undo a write that already landed."""
    notify = getattr(core.kanban_db(), "notify_task_updated", None)
    if notify is None:
        return
    try:
        notify(conn, task_id, ("skills",))
    except Exception:
        logger.debug("on_kanban_task_updated failed after a skills edit", exc_info=True)


def get(conn, task_id: str) -> list[str] | None:
    """The task's skills as stored (``None`` = the column is empty)."""
    row = conn.execute("SELECT skills FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    raw = row["skills"] if hasattr(row, "keys") else row[0]
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        return None
    return [str(entry) for entry in parsed if entry] if isinstance(parsed, list) else None


def set_skills(conn, task_id: str, skills: Iterable[str] | None) -> list[str] | None:
    """Replace a task's skills; the list as stored, or ``None`` when cleared.

    Raises ``LookupError`` for a task that does not exist, ``RuntimeError`` for
    an archived one and ``ValueError`` for a name core would refuse. An empty
    list clears the column back to NULL — "no extras", which is what both NULL
    and ``[]`` mean to the dispatcher, written the way a fresh task carries it.
    """
    kb = core.kanban_db()
    cleaned = normalize(skills)
    stored = json.dumps(cleaned) if cleaned else None

    with kb.write_txn(conn):
        status = _status(conn, task_id)
        if status is None:
            raise LookupError(task_id)
        if status == "archived":
            raise RuntimeError(f"cannot set skills on archived task {task_id}")
        conn.execute("UPDATE tasks SET skills = ? WHERE id = ?", (stored, task_id))
        _record_event(conn, task_id, cleaned)

    _notify(conn, task_id)
    return cleaned or None
