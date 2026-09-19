"""The worker context-window snapshot."""

from __future__ import annotations

import json

import pytest


class _Agent:
    session_id = "sess_1"
    _session_messages = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def _reset(pkg):
    pkg.worker_context._last_write_monotonic = 0.0
    yield
    pkg.worker_context._last_write_monotonic = 0.0


def _arrange(pkg, monkeypatch, breakdown=None):
    from agent import context_breakdown

    monkeypatch.setattr(pkg.worker_context, "_active_agent", lambda: _Agent())
    monkeypatch.setattr(context_breakdown, "compute_session_context_breakdown",
                        lambda agent, messages=None: breakdown or {
                            "categories": [{"id": "conversation", "label": "Conversation", "tokens": 500}],
                            "context_max": 1000, "context_used": 500, "context_percent": 50,
                            "estimated_total": 500, "model": "qwen"})
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "7")
    monkeypatch.setenv("HERMES_PROFILE", "dev_developer")


def test_snapshot_written_next_to_the_log(pkg, board_root, monkeypatch):
    _arrange(pkg, monkeypatch)
    assert pkg.worker_context.record(conversation_history=[{"role": "user", "content": "hi"}]) is True

    payload = json.loads((board_root / "logs" / "t_1.context.json").read_text(encoding="utf-8"))
    assert payload["task_id"] == "t_1" and payload["run_id"] == 7
    assert payload["profile"] == "dev_developer" and payload["context_percent"] == 50
    assert payload["categories"][0]["tokens"] == 500 and payload["updated_at"]
    assert pkg.worker_context.read_snapshot("t_1")["context_used"] == 500


def test_throttled_between_turns(pkg, board_root, monkeypatch):
    _arrange(pkg, monkeypatch)
    assert pkg.worker_context.record() is True
    assert pkg.worker_context.record() is False        # inside the interval
    assert pkg.worker_context.record(force=True) is True


def test_not_a_worker_writes_nothing(pkg, board_root, monkeypatch):
    _arrange(pkg, monkeypatch)
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    assert pkg.worker_context.record() is False
    assert not list((board_root / "logs").glob("*.context.json"))


def test_missing_agent_is_not_an_error(pkg, board_root, monkeypatch):
    _arrange(pkg, monkeypatch)
    monkeypatch.setattr(pkg.worker_context, "_active_agent", lambda: None)
    assert pkg.worker_context.record() is False


def test_switched_off_by_settings(pkg, board_root, monkeypatch):
    _arrange(pkg, monkeypatch)
    pkg.core.set_settings({"worker_context_snapshots": False})
    try:
        assert pkg.worker_context.record() is False
    finally:
        pkg.core.set_settings({})


def test_read_snapshot_missing_is_none(pkg, board_root):
    assert pkg.worker_context.read_snapshot("t_nope") is None
