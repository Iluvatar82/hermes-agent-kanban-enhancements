"""The REST namespace served at /api/plugins/kanban-enhancements."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "kanban-enhancements"


@pytest.fixture
def api(pkg):
    """Load dashboard/plugin_api.py the way Hermes mounts it."""
    spec = importlib.util.spec_from_file_location("kx_plugin_api", PLUGIN_DIR / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["kx_plugin_api"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("kx_plugin_api", None)


@pytest.fixture
def client(api, pkg, board_root, monkeypatch):
    class _Conn:
        def execute(self, sql, params=()):
            class _Cursor:
                def fetchone(self):
                    return [0]

                def fetchall(self):
                    return []

            return _Cursor()

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(api, "_kanban_conn", lambda: _Ctx())
    app = fastapi.FastAPI()
    app.include_router(api.router, prefix="/api/plugins/kanban-enhancements")
    return TestClient(app)


def test_api_reuses_the_loaded_package(api, pkg):
    """One state, not two copies: the mounted API must find the loader's package."""
    assert api._package() is pkg


def test_state_reports_board_and_cap(client, pkg, board_root, monkeypatch):
    monkeypatch.setattr(pkg.dispatch_guard, "configured_cap", lambda: (2, True))
    monkeypatch.setattr(pkg.dispatch_guard, "effective_cap", lambda: 2)

    body = client.get("/api/plugins/kanban-enhancements/state").json()
    assert body["stopped"] is False and body["running"] == 0
    assert body["max_in_progress"] == 2 and body["max_in_progress_set"] is True
    assert body["model"] == "" and "guard_active" in body and "model_patches" in body


def test_stop_and_start_roundtrip(client, pkg, board_root, monkeypatch):
    kb = pkg.core.kanban_db()
    monkeypatch.setattr(kb, "reclaim_task", lambda *a, **k: True)

    stopped = client.post("/api/plugins/kanban-enhancements/stop", json={"reason": "Wartung"}).json()
    assert stopped["stopped"] is True and stopped["reason"] == "Wartung"
    assert pkg.board_control.is_stopped() is True

    monkeypatch.setattr(pkg.core.kanban_dispatch(), "dispatch_once",
                        lambda conn, **kw: pkg.core.kanban_dispatch().DispatchResult())
    started = client.post("/api/plugins/kanban-enhancements/start").json()
    assert started["stopped"] is False and started["spawned"] == []


def test_model_put_and_clear(client, pkg, board_root):
    set_body = client.put("/api/plugins/kanban-enhancements/model",
                          json={"model": "qwen", "provider": "lmstudio"}).json()
    assert set_body["model"] == "qwen" and set_body["provider"] == "lmstudio"

    cleared = client.put("/api/plugins/kanban-enhancements/model", json={"model": "", "provider": ""}).json()
    assert cleared["model"] == "" and pkg.board_model.get_model().is_set is False


def test_max_parallel_rejects_a_negative_value(client):
    assert client.put("/api/plugins/kanban-enhancements/max-parallel", json={"value": -1}).status_code == 422


def test_task_log_serves_stamps_and_strips_on_request(client, pkg, board_root, monkeypatch):
    kb = pkg.core.kanban_db()
    stamped = "[2026-09-17T10:23:45+02:00] one\n[2026-09-17T10:23:46+02:00] two\n"
    (board_root / "logs" / "t_1.log").write_text(stamped, encoding="utf-8")
    monkeypatch.setattr(kb, "read_worker_log",
                        lambda task_id, *, tail_bytes=None, board=None: stamped)  # no timestamps kwarg

    keep = client.get("/api/plugins/kanban-enhancements/tasks/t_1/log").json()
    assert keep["content"] == stamped and keep["exists"] is True and keep["size_bytes"] > 0

    plain = client.get("/api/plugins/kanban-enhancements/tasks/t_1/log?timestamps=false").json()
    assert plain["content"] == "one\ntwo\n"


def test_task_context_reports_unavailable(client):
    body = client.get("/api/plugins/kanban-enhancements/tasks/t_nope/context").json()
    assert body == {"task_id": "t_nope", "available": False}
