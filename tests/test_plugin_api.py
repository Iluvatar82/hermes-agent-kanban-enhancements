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


def test_state_repairs_the_patches_before_reporting(client, pkg, board_root, monkeypatch):
    """Opening the page is the fix, not just the diagnosis: a patch an early
    import failure cost us is re-installed before ``/state`` describes it."""
    kbd = pkg.core.kanban_dispatch()
    monkeypatch.setattr(kbd, "dispatch_once", _recording_dispatch(kbd))
    monkeypatch.setattr(kbd, "_default_spawn", lambda task, workspace, *, board=None: 1)
    pkg.dispatch_guard.uninstall()
    pkg.board_model.uninstall()
    assert pkg.dispatch_guard.is_installed() is False

    body = client.get("/api/plugins/kanban-enhancements/state").json()
    assert body["guard_active"] is True
    assert body["model_patches"]["spawn"] is True
    assert pkg.dispatch_guard.is_installed() is True


def _recording_dispatch(kbd):
    """A stand-in carrying core's real keyword signature (the guard checks it)."""

    def dispatch_once(conn, *, spawn_fn=None, ttl_seconds=None, dry_run=False, max_spawn=None,
                      max_in_progress=None, board=None, **rest):
        return kbd.DispatchResult()

    return dispatch_once


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


# --- One task: the detail view, its status, its comments --------------------


def _full_task(pkg, **overrides):
    """A real ``kanban_db.Task`` — the detail endpoint runs ``asdict`` on it."""
    kb = pkg.core.kanban_db()
    fields = {
        "id": "t_1", "title": "Ship it", "body": "Do the thing", "assignee": "dev",
        "status": "running", "priority": 3, "created_by": "cli", "created_at": 1,
        "started_at": 2, "completed_at": None, "workspace_kind": "worktree",
        "workspace_path": "/tmp/w", "claim_lock": "secret-lock", "claim_expires": 99,
        "tenant": "acme",
    }
    fields.update(overrides)
    return kb.Task(**fields)


@pytest.fixture
def one_task(pkg, monkeypatch):
    """A board with exactly ``t_1`` on it, and empty related collections."""
    kb = pkg.core.kanban_db()
    task = _full_task(pkg, model_override="qwen", provider_override="lmstudio")
    monkeypatch.setattr(kb, "get_task", lambda conn, task_id: task if task_id == task.id else None)
    monkeypatch.setattr(kb, "latest_summary", lambda conn, task_id: "worker said this")
    monkeypatch.setattr(kb, "list_comments", lambda conn, task_id: [])
    monkeypatch.setattr(kb, "list_events", lambda conn, task_id: [])
    monkeypatch.setattr(kb, "list_runs", lambda conn, task_id: [])
    monkeypatch.setattr(kb, "parent_ids", lambda conn, task_id: ["t_parent"])
    monkeypatch.setattr(kb, "child_ids", lambda conn, task_id: [])
    return task


class _FakeUpdateBody:
    """Stand-in for core's ``UpdateTaskBody``: only the two bits we touch."""

    model_fields = {"status": None, "title": None, "body": None, "assignee": None,
                    "priority": None, "model_override": None, "provider_override": None,
                    "clear_model_override": None}

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def fake_core(api, monkeypatch):
    """Core's kanban API, faked: record what Kanban+ delegates to it."""
    calls = []

    def update_task(task_id, payload, board=None):
        calls.append({"task_id": task_id, "fields": payload.kwargs, "board": board})
        return {"task": {"id": task_id, **payload.kwargs}}

    fake = types.SimpleNamespace(UpdateTaskBody=_FakeUpdateBody, update_task=update_task)
    monkeypatch.setattr(api, "_core_kanban_api", lambda: fake)
    return calls


def test_task_detail_carries_everything_the_view_renders(client, one_task):
    body = client.get("/api/plugins/kanban-enhancements/tasks/t_1").json()

    assert body["task"]["title"] == "Ship it" and body["task"]["body"] == "Do the thing"
    assert body["task"]["model_override"] == "qwen" and body["task"]["provider_override"] == "lmstudio"
    assert body["task"]["workspace_path"] == "/tmp/w" and body["task"]["tenant"] == "acme"
    # Workers hand off through a run summary, not tasks.result — the view needs it.
    assert body["task"]["latest_summary"] == "worker said this"
    assert body["links"] == {"parents": ["t_parent"], "children": []}
    assert body["comments"] == [] and body["events"] == [] and body["runs"] == []


def test_task_detail_never_ships_the_workers_claim_credentials(client, one_task):
    task = client.get("/api/plugins/kanban-enhancements/tasks/t_1").json()["task"]

    assert "claim_lock" not in task and "claim_expires" not in task and "idempotency_key" not in task


def test_task_detail_trims_a_long_event_feed_to_the_newest(client, api, pkg, one_task, monkeypatch):
    kb = pkg.core.kanban_db()
    events = [kb.Event(id=n, task_id="t_1", kind="status", payload=None, created_at=n) for n in range(500)]
    monkeypatch.setattr(kb, "list_events", lambda conn, task_id: events)

    feed = client.get("/api/plugins/kanban-enhancements/tasks/t_1").json()["events"]
    assert len(feed) == api._MAX_EVENTS
    # Still oldest-first, but starting where the cap left off.
    assert feed[0]["id"] == 500 - api._MAX_EVENTS and feed[-1]["id"] == 499


def test_task_detail_is_404_for_a_task_that_is_not_there(client, one_task):
    assert client.get("/api/plugins/kanban-enhancements/tasks/t_nope").status_code == 404


def test_patch_delegates_the_move_to_cores_kanban_api(client, fake_core, boards):
    body = client.patch("/api/plugins/kanban-enhancements/tasks/t_1?board=shipping",
                        json={"status": "ready"})

    assert body.status_code == 200
    # The picked board must reach core too — separate boards, separate DBs.
    assert fake_core == [{"task_id": "t_1", "fields": {"status": "ready"}, "board": "shipping"}]


def test_patch_sends_only_the_fields_the_caller_actually_set(client, fake_core):
    client.patch("/api/plugins/kanban-enhancements/tasks/t_1", json={"body": "new text"})

    # `clear_model_override` has a default, but an unsent default is not an edit.
    assert fake_core[0]["fields"] == {"body": "new text"}


def test_patch_with_nothing_to_change_is_a_400(client, fake_core):
    assert client.patch("/api/plugins/kanban-enhancements/tasks/t_1", json={}).status_code == 400


def test_patch_refuses_a_field_this_hermes_does_not_know(client, api, monkeypatch):
    class _Narrow:
        model_fields = {"status": None}

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(api, "_core_kanban_api",
                        lambda: types.SimpleNamespace(UpdateTaskBody=_Narrow, update_task=lambda *a, **k: {}))

    answer = client.patch("/api/plugins/kanban-enhancements/tasks/t_1", json={"body": "x"})
    assert answer.status_code == 501 and "body" in answer.json()["detail"]


def test_patch_says_so_when_cores_kanban_api_is_out_of_reach(client, api, monkeypatch):
    monkeypatch.setattr(api, "_core_kanban_api", lambda: None)

    answer = client.patch("/api/plugins/kanban-enhancements/tasks/t_1", json={"status": "todo"})
    assert answer.status_code == 503 and "kanban" in answer.json()["detail"]


def test_a_comment_lands_on_the_task(client, pkg, one_task, monkeypatch):
    written = []
    monkeypatch.setattr(pkg.core.kanban_db(), "add_comment",
                        lambda conn, task_id, author, body: written.append((task_id, author, body)) or 7)

    answer = client.post("/api/plugins/kanban-enhancements/tasks/t_1/comments", json={"body": "look here"})
    assert answer.json() == {"id": 7, "task_id": "t_1"}
    assert written == [("t_1", "desktop", "look here")]


def test_a_comment_on_an_unknown_task_is_404_and_an_empty_one_is_refused(client, one_task):
    assert client.post("/api/plugins/kanban-enhancements/tasks/t_nope/comments",
                       json={"body": "hi"}).status_code == 404
    assert client.post("/api/plugins/kanban-enhancements/tasks/t_1/comments",
                       json={"body": ""}).status_code == 422


def test_the_patch_fields_are_the_fields_cores_kanban_api_accepts(api):
    """The delegation seam itself, against the real checkout: a Hermes that
    renamed one of these must fail HERE and not at the first drag on a board."""
    core = api._core_kanban_api()
    assert core is not None, "core's kanban plugin API could not be loaded"
    assert set(api.TaskPatchBody.model_fields) <= set(core.UpdateTaskBody.model_fields)
    assert callable(core.update_task)


def test_the_task_fields_the_detail_view_reads_still_exist(pkg):
    """Same seam on the read side: the view renders these by name."""
    fields = set(pkg.core.kanban_db().Task.__dataclass_fields__)
    assert {"body", "result", "model_override", "provider_override", "workspace_kind",
            "workspace_path", "branch_name", "created_by", "worker_pid", "last_failure_error",
            "consecutive_failures"} <= fields


# --- Updating this plugin from inside it ------------------------------------


@pytest.fixture
def no_probe(api, pkg, monkeypatch):
    """Never touch the network from a test; the probe has its own tests."""
    monkeypatch.setattr(pkg.self_update, "fetch_latest_version", lambda source, **kw: ("0.9.9", ""))
    monkeypatch.setattr(pkg.self_update, "installed_source", lambda: "https://github.com/o/r.git#sub")
    return pkg


def test_version_reports_running_installed_and_latest(client, no_probe, api):
    body = client.get("/api/plugins/kanban-enhancements/version").json()

    assert body["running"] == api._PLUGIN_VERSION
    assert body["installed"] == api._package().self_update.installed_version()
    assert body["latest"] == "0.9.9" and body["update_available"] is True
    assert body["source"] == "https://github.com/o/r.git#sub"
    assert body["restart_required"] is False and body["check_error"] is None


def test_version_can_skip_the_network_probe(client, no_probe):
    body = client.get("/api/plugins/kanban-enhancements/version?check=false").json()

    assert body["latest"] is None and body["update_available"] is False


def test_the_update_check_setting_turns_the_probe_off(client, no_probe, pkg):
    pkg.core.set_settings({"update_check": False})
    try:
        assert client.get("/api/plugins/kanban-enhancements/version").json()["latest"] is None
    finally:
        pkg.core.set_settings({})


def test_a_newer_version_on_disk_than_in_memory_asks_for_a_restart(client, no_probe, pkg, monkeypatch):
    """The update landed but the gateway still serves the old module — the one
    state the page must not silently swallow."""
    monkeypatch.setattr(pkg.self_update, "installed_version", lambda: "99.0.0")

    body = client.get("/api/plugins/kanban-enhancements/version?check=false").json()
    assert body["restart_required"] is True and body["installed"] == "99.0.0"


def test_update_uses_the_recorded_source_never_the_request(client, no_probe, pkg, monkeypatch):
    calls = []

    def _install(source, ref):
        calls.append((source, ref))
        return {"ok": True, "installed_version": "0.9.9"}

    monkeypatch.setattr(pkg.self_update, "install", _install)

    body = client.post("/api/plugins/kanban-enhancements/update", json={"source": "https://evil.example/x.git"})

    assert body.status_code == 200 and body.json()["ok"] is True
    # The bogus `source` in the body is ignored; the recorded one is used.
    assert calls == [("https://github.com/o/r.git#sub", None)]


def test_update_passes_a_pin_through_and_refuses_anything_but_a_sha(client, no_probe, pkg, monkeypatch):
    sha = "e6b17af599f98d1605b4f216878250bc734388da"
    calls = []
    monkeypatch.setattr(pkg.self_update, "install",
                        lambda source, ref: calls.append(ref) or {"ok": True, "installed_version": "0.9.9"})

    assert client.post("/api/plugins/kanban-enhancements/update", json={"ref": sha}).status_code == 200
    assert calls == [sha]

    refused = client.post("/api/plugins/kanban-enhancements/update", json={"ref": "main"})
    assert refused.status_code == 400 and "40-character" in refused.json()["detail"]


def test_an_installer_refusal_is_reported_not_swallowed(client, no_probe, pkg, monkeypatch):
    monkeypatch.setattr(pkg.self_update, "install",
                        lambda source, ref: {"ok": False, "error": "scan blocked: suspicious file"})

    answer = client.post("/api/plugins/kanban-enhancements/update", json={})
    assert answer.status_code == 502 and "scan blocked" in answer.json()["detail"]


def test_update_says_so_when_this_hermes_has_no_installer(client, no_probe, pkg, monkeypatch):
    monkeypatch.setattr(pkg.self_update, "available", lambda: False)

    answer = client.post("/api/plugins/kanban-enhancements/update", json={})
    assert answer.status_code == 503 and "plugins install" in answer.json()["detail"]
