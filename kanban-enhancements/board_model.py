"""A model for the whole board.

When a board model is set, everything the board starts by itself runs on it:

* **task executions** — the dispatcher spawns ``hermes -p <profile> -m <model>``
  for every task that carries no model override of its own, so a task the user
  pinned to a specific model keeps it;
* **the auto-composer** — the auxiliary calls that decompose a triage task into
  children and that specify a task (``kanban_decomposer`` / ``triage_specifier``)
  run on the same model instead of the ``auxiliary.*`` config.

Unset means "inherit", exactly like the per-task override: nothing is touched.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from . import core
from .core import logger

#: Auxiliary task names that make up the "auto-composer".
COMPOSER_AUX_TASKS = ("kanban_decomposer", "triage_specifier")

_spawn_original = None
_spawn_patched = False
_call_llm_original = None
_call_llm_patched = False


@dataclass
class BoardModel:
    board: str
    model: str = ""
    provider: str = ""

    @property
    def is_set(self) -> bool:
        return bool(self.model.strip())

    def as_dict(self) -> dict[str, Any]:
        return {"board": self.board, "model": self.model, "provider": self.provider}


def get_model(board: str | None = None) -> BoardModel:
    slug = core.board_slug(board)
    entry = (core.read_state(board).get("models") or {}).get(slug) or {}
    return BoardModel(board=slug, model=str(entry.get("model") or ""),
                      provider=str(entry.get("provider") or ""))


def set_model(model: str, provider: str = "", *, board: str | None = None) -> BoardModel:
    """Set the board model; an empty ``model`` clears it back to "inherit"."""
    value = BoardModel(board=core.board_slug(board), model=(model or "").strip(),
                       provider=(provider or "").strip())
    data = core.read_state(board)
    models = data.setdefault("models", {})
    if value.is_set:
        models[value.board] = {"model": value.model, "provider": value.provider}
    else:
        models.pop(value.board, None)
        value.provider = ""
    core.write_state(data, board)
    return value


# --- task executions --------------------------------------------------------

def install_spawn_patch() -> bool:
    """Give every spawned worker the board model unless the task pins its own."""
    global _spawn_original, _spawn_patched
    if _spawn_patched:
        return True
    kbd = core.kanban_dispatch()
    original = getattr(kbd, "_default_spawn", None)
    if original is None:
        logger.warning("kanban-enhancements: no _default_spawn in this Hermes build — "
                       "the board model will not apply to task executions")
        return False

    def _default_spawn(task, workspace, *, board=None):
        chosen = get_model(board)
        if chosen.is_set and not getattr(task, "model_override", None):
            try:
                task = dataclasses.replace(
                    task, model_override=chosen.model,
                    provider_override=chosen.provider or getattr(task, "provider_override", None))
            except Exception:
                logger.debug("could not apply the board model to %s", getattr(task, "id", "?"), exc_info=True)
        return original(task, workspace, board=board)

    _default_spawn.__wrapped__ = original  # type: ignore[attr-defined]
    kbd._default_spawn = _default_spawn
    _spawn_original, _spawn_patched = original, True
    return True


# --- auto-composer (decompose / specify) ------------------------------------

def install_aux_patch() -> bool:
    """Route the composer's auxiliary calls to the board model."""
    global _call_llm_original, _call_llm_patched
    if _call_llm_patched:
        return True
    try:
        from agent import auxiliary_client
    except Exception:
        logger.debug("auxiliary client unavailable", exc_info=True)
        return False
    original = getattr(auxiliary_client, "call_llm", None)
    if original is None:
        return False

    def call_llm(task=None, *, provider=None, model=None, **kwargs):
        # Only the board's own composer calls, and only when the caller did not
        # already pin a model itself.
        if task in COMPOSER_AUX_TASKS and model is None:
            chosen = get_model()
            if chosen.is_set:
                model = chosen.model
                provider = provider or (chosen.provider or None)
        return original(task, provider=provider, model=model, **kwargs)

    call_llm.__wrapped__ = original  # type: ignore[attr-defined]
    auxiliary_client.call_llm = call_llm
    _call_llm_original, _call_llm_patched = original, True
    return True


def uninstall() -> None:
    global _spawn_original, _spawn_patched, _call_llm_original, _call_llm_patched
    if _spawn_patched:
        try:
            core.kanban_dispatch()._default_spawn = _spawn_original
        except Exception:
            logger.debug("could not restore _default_spawn", exc_info=True)
        _spawn_original, _spawn_patched = None, False
    if _call_llm_patched:
        try:
            from agent import auxiliary_client

            auxiliary_client.call_llm = _call_llm_original
        except Exception:
            logger.debug("could not restore call_llm", exc_info=True)
        _call_llm_original, _call_llm_patched = None, False


def is_installed() -> dict[str, bool]:
    return {"spawn": _spawn_patched, "auto_composer": _call_llm_patched}
