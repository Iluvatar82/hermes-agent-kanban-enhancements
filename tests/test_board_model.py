"""The board-wide model: task executions and the auto-composer."""

from __future__ import annotations

import dataclasses

import pytest


@dataclasses.dataclass
class _Task:
    id: str = "t_1"
    model_override: str | None = None
    provider_override: str | None = None


@pytest.fixture
def model_patch(pkg):
    pkg.board_model.uninstall()
    yield pkg.board_model
    pkg.board_model.uninstall()


def test_unset_by_default(pkg, board_root):
    chosen = pkg.board_model.get_model()
    assert chosen.is_set is False and chosen.model == "" and chosen.provider == ""


def test_set_and_clear(pkg, board_root):
    chosen = pkg.board_model.set_model("qwen3.8-27b@q2_k_xl", "lmstudio")
    assert (chosen.model, chosen.provider, chosen.is_set) == ("qwen3.8-27b@q2_k_xl", "lmstudio", True)
    assert pkg.board_model.get_model().model == "qwen3.8-27b@q2_k_xl"

    cleared = pkg.board_model.set_model("")
    assert cleared.is_set is False and cleared.provider == ""
    assert pkg.board_model.get_model().model == ""


def test_spawn_uses_the_board_model_only_when_the_task_has_none(pkg, board_root, model_patch, monkeypatch):
    kbd = pkg.core.kanban_dispatch()
    spawned = []
    monkeypatch.setattr(kbd, "_default_spawn",
                        lambda task, workspace, *, board=None: spawned.append(task) or 4321)

    assert model_patch.install_spawn_patch() is True
    pkg.board_model.set_model("board-model", "lmstudio")

    kbd._default_spawn(_Task(), "/ws", board=None)
    kbd._default_spawn(_Task(id="t_2", model_override="pinned"), "/ws", board=None)

    assert (spawned[0].model_override, spawned[0].provider_override) == ("board-model", "lmstudio")
    assert spawned[1].model_override == "pinned"  # a task's own choice always wins


def test_spawn_is_untouched_while_unset(pkg, board_root, model_patch, monkeypatch):
    kbd = pkg.core.kanban_dispatch()
    spawned = []
    monkeypatch.setattr(kbd, "_default_spawn",
                        lambda task, workspace, *, board=None: spawned.append(task) or 1)
    model_patch.install_spawn_patch()

    kbd._default_spawn(_Task(), "/ws", board=None)
    assert spawned[0].model_override is None


def test_auto_composer_calls_use_the_board_model(pkg, board_root, model_patch, monkeypatch):
    from agent import auxiliary_client

    seen = []
    monkeypatch.setattr(auxiliary_client, "call_llm",
                        lambda task=None, **kw: seen.append((task, kw.get("model"), kw.get("provider"))))
    assert model_patch.install_aux_patch() is True
    pkg.board_model.set_model("board-model", "lmstudio")

    auxiliary_client.call_llm("kanban_decomposer", messages=[])
    auxiliary_client.call_llm("triage_specifier", messages=[])
    auxiliary_client.call_llm("title_generation", messages=[])          # not the composer
    auxiliary_client.call_llm("kanban_decomposer", model="explicit", messages=[])

    assert seen == [
        ("kanban_decomposer", "board-model", "lmstudio"),
        ("triage_specifier", "board-model", "lmstudio"),
        ("title_generation", None, None),
        ("kanban_decomposer", "explicit", None),
    ]


def test_uninstall_restores_core(pkg, board_root, model_patch, monkeypatch):
    kbd = pkg.core.kanban_dispatch()
    original = kbd._default_spawn
    model_patch.install_spawn_patch()
    assert kbd._default_spawn is not original
    model_patch.uninstall()
    assert kbd._default_spawn is original
