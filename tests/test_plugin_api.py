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


class _FakeCreateBody:
    """Every field Kanban+'s create hands to core, plus the ``triage`` flag it
    turns the Triage lane into. Narrower than core's real body on purpose: the
    seam test below is what holds the two together."""

    model_fields = dict.fromkeys(
        ["title", "body", "assignee", "priority", "workspace_kind", "workspace_path",
         "parents", "skills", "goal_mode", "model_override", "provider_override", "triage"])

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def fake_core(api, monkeypatch):
    """Core's kanban API, faked: record what Kanban+ delegates to it."""
    calls = []

    def update_task(task_id, payload, board=None):
        calls.append({"task_id": task_id, "fields": payload.kwargs, "board": board})
        return {"task": {"id": task_id, **payload.kwargs}}

    def create_task(payload, board=None):
        calls.append({"create": payload.kwargs, "board": board})
        # What core derives on its own: Triage from the flag, else Ready.
        status = "triage" if payload.kwargs.get("triage") else "ready"
        return {"task": {"id": "t_new", "status": status, "title": payload.kwargs.get("title")}}

    fake = types.SimpleNamespace(
        CreateTaskBody=_FakeCreateBody, UpdateTaskBody=_FakeUpdateBody,
        create_task=create_task, update_task=update_task)
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


def test_patch_writes_skills_itself_and_never_sends_them_to_core(client, pkg, fake_core, monkeypatch):
    """Core's update body has no ``skills`` field — so this half is ours, and
    core must not be handed a field it would refuse."""
    written = []
    monkeypatch.setattr(pkg.task_skills, "set_skills",
                        lambda conn, task_id, skills: written.append((task_id, skills)) or list(skills))

    answer = client.patch("/api/plugins/kanban-enhancements/tasks/t_1", json={"skills": ["python", "review"]})

    assert answer.status_code == 200
    assert written == [("t_1", ["python", "review"])]
    assert answer.json()["task"]["skills"] == ["python", "review"]
    # Nothing was delegated: there was nothing core could do with it.
    assert fake_core == []


def test_patch_splits_a_mixed_edit_between_core_and_this_plugin(client, pkg, fake_core, monkeypatch):
    written = []
    monkeypatch.setattr(pkg.task_skills, "set_skills",
                        lambda conn, task_id, skills: written.append(skills) or list(skills))

    body = client.patch("/api/plugins/kanban-enhancements/tasks/t_1",
                        json={"status": "ready", "skills": ["python"]}).json()

    assert fake_core == [{"task_id": "t_1", "fields": {"status": "ready"}, "board": None}]
    assert written == [["python"]]
    # One task in the answer, carrying both halves.
    assert body["task"]["status"] == "ready" and body["task"]["skills"] == ["python"]


def test_patch_reads_an_empty_skills_list_as_a_clear(client, pkg, fake_core, monkeypatch):
    written = []
    monkeypatch.setattr(pkg.task_skills, "set_skills",
                        lambda conn, task_id, skills: written.append(skills) or None)

    body = client.patch("/api/plugins/kanban-enhancements/tasks/t_1", json={"skills": []}).json()

    assert written == [[]] and body["task"]["skills"] is None


def test_patch_that_does_not_mention_skills_leaves_them_alone(client, pkg, fake_core, monkeypatch):
    monkeypatch.setattr(pkg.task_skills, "set_skills",
                        lambda *a, **k: pytest.fail("skills were written by a patch that never sent them"))

    assert client.patch("/api/plugins/kanban-enhancements/tasks/t_1",
                        json={"body": "new text"}).status_code == 200


def test_a_skill_name_core_refuses_is_a_400_and_an_unknown_task_a_404(client, pkg, fake_core, monkeypatch):
    def refuse(conn, task_id, skills):
        if task_id != "t_1":
            raise LookupError(task_id)
        raise ValueError("skill name cannot contain comma: 'a,b'")

    monkeypatch.setattr(pkg.task_skills, "set_skills", refuse)

    bad = client.patch("/api/plugins/kanban-enhancements/tasks/t_1", json={"skills": ["a,b"]})
    assert bad.status_code == 400 and "comma" in bad.json()["detail"]
    gone = client.patch("/api/plugins/kanban-enhancements/tasks/t_nope", json={"skills": ["a"]})
    assert gone.status_code == 404


def test_an_archived_task_refuses_a_skills_edit(client, pkg, fake_core, monkeypatch):
    def refuse(conn, task_id, skills):
        raise RuntimeError("cannot set skills on archived task t_1")

    monkeypatch.setattr(pkg.task_skills, "set_skills", refuse)

    answer = client.patch("/api/plugins/kanban-enhancements/tasks/t_1", json={"skills": ["a"]})
    assert answer.status_code == 400 and "archived" in answer.json()["detail"]


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
    renamed one of these must fail HERE and not at the first drag on a board.

    Minus the fields Kanban+ writes itself — ``skills`` is in this body
    precisely BECAUSE core's has no such field.
    """
    core = api._core_kanban_api()
    assert core is not None, "core's kanban plugin API could not be loaded"
    delegated = set(api.TaskPatchBody.model_fields) - api._OWN_PATCH_FIELDS
    assert delegated <= set(core.UpdateTaskBody.model_fields)
    assert callable(core.update_task)


def test_the_field_kanban_plus_writes_itself_is_still_one_core_cannot(api):
    """The other half of that seam: the day core's update body grows a
    ``skills`` field, this plugin should hand it over instead of writing the
    column by hand — and this is the test that says so."""
    core = api._core_kanban_api()
    assert core is not None, "core's kanban plugin API could not be loaded"
    assert api._OWN_PATCH_FIELDS.isdisjoint(core.UpdateTaskBody.model_fields)


# --- Adding a task to a lane ------------------------------------------------


_NEW = {"title": "Ship it", "body": "Do the thing", "priority": 3}


def _post_task(client, **extra):
    return client.post("/api/plugins/kanban-enhancements/tasks", json={**_NEW, **extra})


def test_create_delegates_to_core_and_then_moves_into_the_lane(client, fake_core):
    """Core derives Ready; landing in the lane that was clicked is the move."""
    answer = _post_task(client, status="todo")

    assert answer.status_code == 200
    created, moved = fake_core
    # `status` is this plugin's field, not core's — it must not be forwarded.
    assert "status" not in created["create"]
    assert created["create"]["title"] == "Ship it" and created["create"]["priority"] == 3
    assert moved == {"task_id": "t_new", "fields": {"status": "todo"}, "board": None}
    assert answer.json()["task"]["status"] == "todo"


def test_create_reaches_the_picked_board_on_both_calls(client, fake_core, boards):
    """Separate boards, separate DBs — a create and its move must not split."""
    client.post("/api/plugins/kanban-enhancements/tasks?board=shipping",
                json={**_NEW, "status": "todo"})

    assert [call["board"] for call in fake_core] == ["shipping", "shipping"]


def test_create_in_triage_is_a_flag_core_reads_and_not_a_move(client, fake_core):
    answer = _post_task(client, status="triage")

    assert answer.status_code == 200
    assert fake_core == [{"create": {**_NEW, "triage": True}, "board": None}]
    assert answer.json()["task"]["status"] == "triage"


def test_create_in_ready_is_the_status_core_already_gave_it(client, fake_core):
    answer = _post_task(client, status="ready")

    assert answer.status_code == 200 and len(fake_core) == 1  # no move
    assert answer.json()["task"]["status"] == "ready"


def test_create_refuses_a_lane_the_dispatcher_hands_out(client, fake_core):
    """Refused HERE, not after the fact: creating the task and then failing to
    move it would leave a card in a lane nobody asked for."""
    for lane in ("running", "review", "scheduled"):
        answer = _post_task(client, status=lane)
        assert answer.status_code == 400, lane

    assert fake_core == []


def test_create_keeps_the_task_when_the_lane_refuses_the_move(client, api, fake_core, monkeypatch):
    core = api._core_kanban_api()

    def refuse(task_id, payload, board=None):
        raise fastapi.HTTPException(status_code=409, detail="blocked needs a reason")

    monkeypatch.setattr(core, "update_task", refuse)
    answer = _post_task(client, status="blocked")

    # The task exists; losing it over a refused move would be the worse answer.
    assert answer.status_code == 200
    body = answer.json()
    assert body["task"]["status"] == "ready"
    assert "blocked needs a reason" in body["warning"]


def test_create_needs_a_title(client, fake_core):
    assert client.post("/api/plugins/kanban-enhancements/tasks", json={"title": ""}).status_code == 422
    assert client.post("/api/plugins/kanban-enhancements/tasks", json={}).status_code == 422
    # A whitespace-only title goes through: core refuses it for every surface,
    # and one rule in one place beats the same rule in two.
    assert client.post("/api/plugins/kanban-enhancements/tasks", json={"title": "  "}).status_code == 200
    assert fake_core[0]["create"]["title"] == "  "


def test_create_says_so_when_cores_kanban_api_is_out_of_reach(client, api, monkeypatch):
    monkeypatch.setattr(api, "_core_kanban_api", lambda: None)

    assert _post_task(client, status="todo").status_code == 503


def test_the_create_fields_are_the_fields_cores_kanban_api_accepts(api):
    """The other half of the delegation seam, against the real checkout."""
    core = api._core_kanban_api()
    assert core is not None, "core's kanban plugin API could not be loaded"
    ours = set(api.TaskCreateBody.model_fields) - {"status"}  # ours alone: the lane
    assert ours | {"triage"} <= set(core.CreateTaskBody.model_fields)
    assert callable(core.create_task)


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


# --- Updating every profile ---------------------------------------------------


def test_profiles_lists_what_is_installed_where(client, pkg, monkeypatch):
    rows = [{"name": "default", "home": "/h", "current": True, "installed": "0.5.0"},
            {"name": "dev", "home": "/h/profiles/dev", "current": False, "installed": "0.4.0"}]
    monkeypatch.setattr(pkg.profile_update, "list_profiles", lambda: rows)
    monkeypatch.setattr(pkg.profile_update, "supported", lambda: True)

    body = client.get("/api/plugins/kanban-enhancements/profiles").json()
    assert body["supported"] is True and body["profiles"] == rows


def test_update_all_profiles_installs_into_each_of_them(client, no_probe, pkg, api, monkeypatch):
    calls = []

    def install_all(source, ref):
        calls.append((source, ref))
        return [{"name": "default", "current": True, "ok": True, "installed": "0.9.9", "was": "0.5.0"},
                {"name": "dev", "current": False, "ok": True, "installed": "0.9.9", "was": "0.4.0"}]

    monkeypatch.setattr(pkg.profile_update, "supported", lambda: True)
    monkeypatch.setattr(pkg.profile_update, "install_all", install_all)

    body = client.post("/api/plugins/kanban-enhancements/update", json={"all_profiles": True}).json()

    # Still the recorded source, never one from the request.
    assert calls == [("https://github.com/o/r.git#sub", None)]
    assert body["ok"] is True and body["failed"] == []
    assert [row["name"] for row in body["profiles"]] == ["default", "dev"]
    # The gateway's own profile decides whether a restart is pending.
    assert body["installed"] == "0.9.9" and body["restart_required"] is True


def test_one_profile_refusing_makes_the_whole_update_a_failure_that_names_it(
        client, no_probe, pkg, monkeypatch):
    monkeypatch.setattr(pkg.profile_update, "supported", lambda: True)
    monkeypatch.setattr(pkg.profile_update, "install_all", lambda source, ref: [
        {"name": "default", "current": True, "ok": True, "installed": "0.9.9", "warnings": ["custom source"]},
        {"name": "dev", "current": False, "ok": False, "installed": "0.4.0", "error": "scan blocked"}])

    body = client.post("/api/plugins/kanban-enhancements/update", json={"all_profiles": True}).json()

    assert body["ok"] is False and body["failed"] == ["dev"]
    assert body["warnings"] == ["custom source"]


def test_update_all_profiles_says_so_when_this_hermes_cannot_reach_them(client, no_probe, pkg, monkeypatch):
    monkeypatch.setattr(pkg.profile_update, "supported", lambda: False)

    answer = client.post("/api/plugins/kanban-enhancements/update", json={"all_profiles": True})
    assert answer.status_code == 503 and "update.ps1" in answer.json()["detail"]


def test_a_plain_update_still_touches_only_this_profile(client, no_probe, pkg, monkeypatch):
    monkeypatch.setattr(pkg.self_update, "install",
                        lambda source, ref: {"ok": True, "installed_version": "0.9.9"})
    monkeypatch.setattr(pkg.profile_update, "install_all",
                        lambda *a, **k: pytest.fail("a single-profile update reached every profile"))

    body = client.post("/api/plugins/kanban-enhancements/update", json={}).json()
    assert body["ok"] is True and "profiles" not in body
