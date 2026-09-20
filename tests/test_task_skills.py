"""Editing a task's skills — the field core's kanban API cannot change."""

from __future__ import annotations

import pytest


@pytest.fixture
def conn(pkg, board_root):
    """A real board database with one task on it."""
    from hermes_cli import kanban_db_connect as kbc

    with kbc.connect_closing(board="default") as connection:
        yield connection


@pytest.fixture
def task_id(pkg, conn):
    kb = pkg.core.kanban_db()
    created = kb.create_task(conn, title="Ship it", skills=["python"], triage=True)
    return created["id"] if isinstance(created, dict) else created


def test_a_skill_can_be_replaced_after_the_task_was_created(pkg, conn, task_id):
    """The whole point: a misspelled skill costs an edit, not the task."""
    assert pkg.task_skills.get(conn, task_id) == ["python"]

    assert pkg.task_skills.set_skills(conn, task_id, ["pyton", "review"]) == ["pyton", "review"]
    assert pkg.task_skills.get(conn, task_id) == ["pyton", "review"]

    assert pkg.task_skills.set_skills(conn, task_id, ["python", "review"]) == ["python", "review"]
    assert pkg.task_skills.get(conn, task_id) == ["python", "review"]


def test_an_empty_list_clears_the_column(pkg, conn, task_id):
    assert pkg.task_skills.set_skills(conn, task_id, []) is None
    assert pkg.task_skills.get(conn, task_id) is None
    row = conn.execute("SELECT skills FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["skills"] is None


def test_the_edit_is_stripped_and_deduplicated_like_cores_create(pkg, conn, task_id):
    assert pkg.task_skills.set_skills(conn, task_id, ["  review ", "review", "", "python"]) == [
        "review", "python"]


def test_a_name_core_would_refuse_is_refused_here_too(pkg, conn, task_id):
    """Each skill is one ``--skills`` argv slot, so a comma is never part of a
    name — and a toolset name is not a skill name."""
    with pytest.raises(ValueError):
        pkg.task_skills.set_skills(conn, task_id, ["python, review"])
    with pytest.raises(ValueError):
        pkg.task_skills.set_skills(conn, task_id, ["terminal"])
    # Nothing was written on the way to the refusal.
    assert pkg.task_skills.get(conn, task_id) == ["python"]


def test_the_local_fallback_keeps_the_invariants_without_cores_normalizer(pkg, monkeypatch):
    """A Hermes that moved ``_normalize_task_skills`` degrades to ours."""
    monkeypatch.delattr(pkg.core.kanban_db(), "_normalize_task_skills", raising=False)

    assert pkg.task_skills.normalize([" a ", "a", "", "b"]) == ["a", "b"]
    assert pkg.task_skills.normalize(None) is None
    with pytest.raises(ValueError):
        pkg.task_skills.normalize(["a,b"])


def test_an_edit_lands_in_the_activity_feed(pkg, conn, task_id):
    kb = pkg.core.kanban_db()
    pkg.task_skills.set_skills(conn, task_id, ["review"])

    kinds = [event.kind for event in kb.list_events(conn, task_id)]
    assert pkg.task_skills.EVENT_KIND in kinds


def test_a_task_that_is_not_there_is_a_lookup_error(pkg, conn):
    with pytest.raises(LookupError):
        pkg.task_skills.set_skills(conn, "t_nope", ["python"])


def test_an_archived_task_is_refused(pkg, conn, task_id):
    conn.execute("UPDATE tasks SET status = 'archived' WHERE id = ?", (task_id,))
    conn.commit()

    with pytest.raises(RuntimeError):
        pkg.task_skills.set_skills(conn, task_id, ["python"])


def test_the_observer_fires_after_the_write_and_never_undoes_it(pkg, conn, task_id, monkeypatch):
    """Best effort: a subscriber that raises must not cost the edit."""
    seen = []

    def boom(connection, task, changed_fields, **kwargs):
        seen.append((task, tuple(changed_fields)))
        raise RuntimeError("subscriber exploded")

    monkeypatch.setattr(pkg.core.kanban_db(), "notify_task_updated", boom)

    assert pkg.task_skills.set_skills(conn, task_id, ["review"]) == ["review"]
    assert seen == [(task_id, ("skills",))]
    assert pkg.task_skills.get(conn, task_id) == ["review"]


def test_the_column_this_writes_is_still_the_one_core_reads(pkg, conn, task_id):
    """The seam, against the real checkout: core hands ``tasks.skills`` to the
    worker as ``--skills``, so a rename must fail here and not at the next spawn."""
    kb = pkg.core.kanban_db()
    pkg.task_skills.set_skills(conn, task_id, ["review"])

    assert "skills" in kb.Task.__dataclass_fields__
    assert kb.get_task(conn, task_id).skills == ["review"]
