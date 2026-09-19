"""The REST namespace served at /api/plugins/kanban-enhancements."""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
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
def opened_boards():
    """Every board slug the API opened a connection for, in order."""
    return []


@pytest.fixture
def client(api, pkg, board_root, opened_boards, monkeypatch):
    class _Cursor:
        def fetchone(self):
            return [0]

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, params=()):
            return _Cursor()

    @contextlib.contextmanager
    def _conn(board=None):
        opened_boards.append(board)
        yield _Conn()

    monkeypatch.setattr(api, "_kanban_conn", _conn)
    app = fastapi.FastAPI()
    app.include_router(api.router, prefix="/api/plugins/kanban-enhancements")
    return TestClient(app)


@pytest.fixture
def boards(pkg, monkeypatch):
    """A two-board install on disk, without touching a real HERMES_HOME."""
    kb = pkg.core.kanban_db()
    known = {"default", "shipping"}
    metas = [
        {"slug": "default", "name": "Default", "description": "", "default_workdir": None,
         "project_id": None, "archived": False},
        {"slug": "shipping", "name": "Shipping", "description": "", "default_workdir": None,
         "project_id": None, "archived": False},
    ]
    monkeypatch.setattr(kb, "board_exists", lambda board=None: str(board or "default") in known)
    monkeypatch.setattr(kb, "list_boards", lambda **kw: [dict(meta) for meta in metas])
    return metas


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


# --- Separate boards --------------------------------------------------------


def test_board_query_opens_that_boards_database(client, boards, opened_boards):
    """``?board=`` must reach the connection: separate boards, separate DBs."""
    assert client.get("/api/plugins/kanban-enhancements/state?board=shipping").status_code == 200
    assert opened_boards == ["shipping"]

    opened_boards.clear()
    client.get("/api/plugins/kanban-enhancements/tasks")
    assert opened_boards == [None]  # omitted = the active board


def test_unknown_board_is_404_and_a_malformed_slug_is_400(client, boards):
    base = "/api/plugins/kanban-enhancements/state"
    assert client.get(f"{base}?board=nope").status_code == 404
    assert client.get(f"{base}?board=../etc").status_code == 400


def test_boards_lists_counts_the_switch_and_the_stop_state(client, pkg, boards, monkeypatch):
    monkeypatch.setattr(pkg.core.kanban_db(), "get_current_board", lambda: "default")
    monkeypatch.setattr("kx_plugin_api._projects_by_id", lambda: {})
    monkeypatch.setattr(pkg.board_control, "is_stopped", lambda board=None: board == "shipping")

    body = client.get("/api/plugins/kanban-enhancements/boards").json()
    by_slug = {entry["slug"]: entry for entry in body["boards"]}
    assert body["current"] == "default"
    assert by_slug["default"]["is_current"] is True and by_slug["shipping"]["is_current"] is False
    # Counts come from a board that has no DB file here — empty, never an error.
    assert by_slug["shipping"]["total"] == 0 and by_slug["shipping"]["counts"] == {}
    # The plugin's own per-board switch rides along so the switcher can flag it.
    assert by_slug["shipping"]["stopped"] is True and by_slug["default"]["stopped"] is False


def test_create_switch_rename_and_archive_a_board(client, pkg, boards, monkeypatch):
    kb = pkg.core.kanban_db()
    created, switched, renamed, removed = {}, [], {}, {}

    def _create(slug, **kwargs):
        created.update({"slug": slug, **kwargs})
        return {"slug": slug, "name": kwargs.get("name") or slug, "project_id": kwargs.get("project_id"),
                "default_workdir": kwargs.get("default_workdir")}

    monkeypatch.setattr(kb, "create_board", _create)
    monkeypatch.setattr(kb, "set_current_board", lambda slug: switched.append(slug))
    monkeypatch.setattr(kb, "get_current_board", lambda: switched[-1] if switched else "default")
    monkeypatch.setattr(kb, "write_board_metadata", lambda slug, **kwargs: renamed.update(
        {"slug": slug, **kwargs}) or {"slug": slug, "name": kwargs.get("name"), "project_id": None})
    monkeypatch.setattr(kb, "remove_board", lambda slug, archive=True: removed.update(
        {"slug": slug, "archive": archive}) or {"slug": slug, "action": "archived", "new_path": "/tmp/x"})

    made = client.post("/api/plugins/kanban-enhancements/boards",
                       json={"slug": "ops", "name": "Ops", "switch": True}).json()
    assert made["board"]["slug"] == "ops" and created["name"] == "Ops" and switched == ["ops"]

    patched = client.patch("/api/plugins/kanban-enhancements/boards/shipping",
                           json={"name": "Versand"}).json()
    assert patched["board"]["name"] == "Versand" and renamed["slug"] == "shipping"

    gone = client.delete("/api/plugins/kanban-enhancements/boards/shipping").json()
    # Archive, not erase — the default for a board the user removes.
    assert gone["result"]["action"] == "archived" and removed == {"slug": "shipping", "archive": True}


def test_removing_the_default_board_is_refused(client, pkg, boards, monkeypatch):
    def _refuse(slug, archive=True):
        raise ValueError("the 'default' board cannot be removed")

    monkeypatch.setattr(pkg.core.kanban_db(), "remove_board", _refuse)
    response = client.delete("/api/plugins/kanban-enhancements/boards/default")
    assert response.status_code == 400 and "cannot be removed" in response.json()["detail"]


def test_rename_of_an_unknown_board_is_404(client, boards):
    assert client.patch("/api/plugins/kanban-enhancements/boards/nope", json={"name": "x"}).status_code == 404


# --- The board's own columns ------------------------------------------------


def _task(task_id, status, **kwargs):
    return types.SimpleNamespace(
        id=task_id, title=f"Task {task_id}", status=status, assignee=kwargs.get("assignee"),
        priority=kwargs.get("priority", 0), tenant=None, started_at=kwargs.get("started_at"),
        created_at=1, completed_at=None)


def test_board_groups_tasks_into_cores_columns(client, pkg, boards, monkeypatch):
    kb = pkg.core.kanban_db()
    monkeypatch.setattr(kb, "list_tasks", lambda conn, **kw: [
        _task("t_1", "running", assignee="dev", started_at=10),
        _task("t_2", "triage"),
        # A status a later Hermes adds lands in todo, the way core falls back.
        _task("t_3", "some-new-status"),
    ])

    body = client.get("/api/plugins/kanban-enhancements/board").json()
    columns = {column["name"]: column["tasks"] for column in body["columns"]}
    assert list(columns) == ["triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done"]
    assert [t["id"] for t in columns["running"]] == ["t_1"]
    assert [t["id"] for t in columns["triage"]] == ["t_2"]
    assert [t["id"] for t in columns["todo"]] == ["t_3"]
    assert columns["running"][0]["assignee"] == "dev" and columns["running"][0]["comment_count"] == 0


def test_board_columns_follow_the_board_query(client, pkg, boards, opened_boards, monkeypatch):
    monkeypatch.setattr(pkg.core.kanban_db(), "list_tasks", lambda conn, **kw: [])

    assert client.get("/api/plugins/kanban-enhancements/board?board=shipping").status_code == 200
    assert opened_boards == ["shipping"]


def test_board_can_include_the_archived_column(client, pkg, boards, monkeypatch):
    monkeypatch.setattr(pkg.core.kanban_db(), "list_tasks", lambda conn, **kw: [])

    body = client.get("/api/plugins/kanban-enhancements/board?include_archived=true").json()
    assert [column["name"] for column in body["columns"]][-1] == "archived"
