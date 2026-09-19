"""The board stop switch and its dispatcher guard."""

from __future__ import annotations

import pytest


class _FakeRow(dict):
    def keys(self):  # sqlite3.Row-like
        return super().keys()


class _FakeConn:
    def __init__(self, running=()):
        self.running = list(running)

    def execute(self, sql, params=()):
        class _Cursor:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return self._rows[0] if self._rows else None

        if "COUNT(*)" in sql:
            return _Cursor([[len(self.running)]])
        return _Cursor([_FakeRow(id=task_id) for task_id in self.running])


def test_board_is_running_by_default(pkg, board_root):
    state = pkg.board_control.get_state()
    assert state.stopped is False and state.board == "default"
    assert pkg.board_control.is_stopped() is False


def test_stop_persists_first_then_reclaims(pkg, board_root, monkeypatch):
    seen = []
    order = []
    kb = pkg.core.kanban_db()

    def _reclaim(conn, task_id, *, reason=None, **_kw):
        # The switch must already be on disk when the first worker is killed.
        order.append(pkg.board_control.is_stopped())
        seen.append((task_id, reason))
        return True

    monkeypatch.setattr(kb, "reclaim_task", _reclaim)
    result = pkg.board_control.stop(_FakeConn(["t_a", "t_b"]), reason="Wartung")

    assert [task for task, _ in seen] == ["t_a", "t_b"]
    assert order == [True, True]
    assert result.reclaimed == ["t_a", "t_b"] and result.failed == []
    assert pkg.board_control.is_stopped() is True
    state = pkg.board_control.get_state()
    assert state.reason == "Wartung" and state.stopped_at


def test_stop_can_leave_workers_running(pkg, board_root, monkeypatch):
    kb = pkg.core.kanban_db()
    monkeypatch.setattr(kb, "reclaim_task", lambda *a, **k: pytest.fail("must not reclaim"))
    result = pkg.board_control.stop(_FakeConn(["t_a"]), terminate_workers=False)
    assert result.reclaimed == [] and pkg.board_control.is_stopped() is True


def test_failed_reclaim_is_reported_not_raised(pkg, board_root, monkeypatch):
    kb = pkg.core.kanban_db()
    monkeypatch.setattr(kb, "reclaim_task", lambda *a, **k: False)
    result = pkg.board_control.stop(_FakeConn(["t_a"]))
    assert result.failed == ["t_a"] and result.reclaimed == []


def test_start_clears_the_switch(pkg, board_root):
    pkg.board_control.stop(_FakeConn(), reason="x")
    state = pkg.board_control.start()
    assert state.stopped is False and state.reason is None
    assert pkg.board_control.is_stopped() is False


def test_unreadable_state_means_running(pkg, board_root, monkeypatch):
    """A broken state file must never wedge the board."""
    monkeypatch.setattr(pkg.core, "read_state", lambda board=None: (_ for _ in ()).throw(OSError("boom")))
    assert pkg.board_control.is_stopped() is False


# --- dispatcher guard -------------------------------------------------------

def _recording_dispatch(kbd, calls):
    """A stand-in with core's real keyword signature (the guard checks it)."""

    def dispatch_once(conn, *, spawn_fn=None, ttl_seconds=None, dry_run=False, max_spawn=None,
                      max_in_progress=None, board=None, **rest):
        calls.append({"board": board, "max_in_progress": max_in_progress})
        return kbd.DispatchResult()

    return dispatch_once


@pytest.fixture
def guard(pkg):
    pkg.dispatch_guard.uninstall()
    yield pkg.dispatch_guard
    pkg.dispatch_guard.uninstall()


def test_guard_suppresses_dispatch_while_stopped(pkg, board_root, guard, monkeypatch):
    kbd = pkg.core.kanban_dispatch()
    calls = []
    monkeypatch.setattr(kbd, "dispatch_once", _recording_dispatch(kbd, calls))

    assert guard.install() is True
    pkg.board_control.stop(_FakeConn(), terminate_workers=False)
    result = kbd.dispatch_once(None, board=None)
    assert calls == [] and getattr(result, "board_stopped", False) is True

    pkg.board_control.start()
    kbd.dispatch_once(None, board=None)
    assert len(calls) == 1


def test_guard_applies_the_configured_cap(pkg, board_root, guard, monkeypatch):
    kbd = pkg.core.kanban_dispatch()
    calls = []
    monkeypatch.setattr(kbd, "dispatch_once", _recording_dispatch(kbd, calls))
    monkeypatch.setattr(guard, "configured_cap", lambda: (1, True))

    guard.install()
    kbd.dispatch_once(None, board=None)                    # caller passed nothing
    kbd.dispatch_once(None, board=None, max_in_progress=8)  # stale boot value
    assert [call["max_in_progress"] for call in calls] == [1, 1]


def test_zero_means_unbounded(pkg, board_root, guard, monkeypatch):
    kbd = pkg.core.kanban_dispatch()
    calls = []
    monkeypatch.setattr(kbd, "dispatch_once", _recording_dispatch(kbd, calls))
    monkeypatch.setattr(guard, "configured_cap", lambda: (None, True))

    guard.install()
    kbd.dispatch_once(None, board=None, max_in_progress=4)
    assert calls[0]["max_in_progress"] is None


def test_config_parsing(pkg, guard, monkeypatch):
    import hermes_cli.config as config

    for raw, expected in ((1, (1, True)), (0, (None, True)), (None, (None, False)),
                          ("3", (3, True)), ("nonsense", (None, False)), (-2, (None, False))):
        monkeypatch.setattr(config, "load_config_readonly",
                            lambda _raw=raw: {"kanban": {"max_in_progress": _raw}})
        assert guard.configured_cap() == expected, raw


def test_guard_refuses_an_unknown_dispatch_signature(pkg, guard, monkeypatch, caplog):
    kbd = pkg.core.kanban_dispatch()
    monkeypatch.setattr(kbd, "dispatch_once", lambda conn: None)  # no board/max_in_progress kwargs
    assert guard.install() is False
    assert guard.is_installed() is False
