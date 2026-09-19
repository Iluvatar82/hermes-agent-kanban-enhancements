"""A model for the whole board.

When a board model is set, everything the board starts by itself runs on it:

* **task executions** — the dispatcher spawns ``hermes -p <profile> -m <model>``
  for every task that carries no model override of its own, so a task the user
  pinned to a specific model keeps it;
* **the auto-composer** — the auxiliary calls that decompose a triage task into
  children and that specify a task (``kanban_decomposer`` / ``triage_specifier``)
  run on the same model instead of the ``auxiliary.*`` config.

Unset means "inherit", exactly like the per-task override: nothing is touched.
A task that pins its OWN model keeps it — the board model fills the gap, it
does not overrule a deliberate per-task choice.

Both patches are retryable and their state lives on the patched function
(``core.PATCH_MARKER``), not in a module global. Installing them once from
``register()`` meant that an import which was not ready during gateway boot —
``agent.auxiliary_client`` is a big lazy module — left the board model inert
for the whole life of the process, reported as "set but patches nowhere". Now
every caller re-checks, and the dispatch tick, the REST state endpoint and the
CLI each repair what is missing.
"""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass
from typing import Any

from . import core
from .core import logger

#: Auxiliary task names that make up the "auto-composer".
COMPOSER_AUX_TASKS = ("kanban_decomposer", "triage_specifier")

#: Names the two wrappers are stamped with on the functions they replace.
SPAWN_PATCH = "kanban_db_dispatch._default_spawn"
AUX_PATCH = "auxiliary_client.call_llm"


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

def _dispatch_module():
    """``hermes_cli.kanban_db_dispatch``, or None while it cannot be imported."""
    try:
        return core.kanban_dispatch()
    except Exception:
        logger.debug("kanban_db_dispatch unavailable", exc_info=True)
        return None


def _aux_module():
    """``agent.auxiliary_client``, or None while it cannot be imported."""
    try:
        from agent import auxiliary_client

        return auxiliary_client
    except Exception:
        logger.debug("auxiliary client unavailable", exc_info=True)
        return None


def install_spawn_patch() -> bool:
    """Give every spawned worker the board model unless the task pins its own.

    Idempotent and retryable — see the module docstring.
    """
    kbd = _dispatch_module()
    if kbd is None:
        return False
    if core.patched(kbd, "_default_spawn", SPAWN_PATCH):
        return True
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

    kbd._default_spawn = core.stamp(_default_spawn, SPAWN_PATCH, original)
    logger.info("kanban-enhancements: board model applies to task runs")
    return True


# --- auto-composer (decompose / specify) ------------------------------------

def install_aux_patch() -> bool:
    """Route the composer's auxiliary calls to the board model.

    The board is not a parameter of ``call_llm``, and it does not need to be:
    core's auto-decomposer pins ``HERMES_KANBAN_BOARD`` around the call, and
    ``get_current_board()`` reads it — so ``get_model()`` resolves the board the
    composer is actually working on, not whichever one was last switched to.
    """
    auxiliary_client = _aux_module()
    if auxiliary_client is None:
        return False
    if core.patched(auxiliary_client, "call_llm", AUX_PATCH):
        return True
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

    auxiliary_client.call_llm = core.stamp(call_llm, AUX_PATCH, original)
    logger.info("kanban-enhancements: board model applies to the auto-composer")
    return True


def install() -> dict[str, bool]:
    """Install whatever is not in place yet; the live state afterwards."""
    return {"spawn": bool(core.guarded("board model (task executions)")(install_spawn_patch)()),
            "auto_composer": bool(core.guarded("board model (auto-composer)")(install_aux_patch)())}


def _restore(owner, attr: str, name: str) -> None:
    """Unwrap ``owner.attr`` — but only our own wrapper, and only to the
    original it recorded on itself (a module global would restore the wrong
    function in a gateway that holds two copies of this package)."""
    if owner is None or not core.patched(owner, attr, name):
        return
    original = core.original_of(owner, attr)
    if original is not None:
        setattr(owner, attr, original)


def uninstall() -> None:
    _restore(_dispatch_module(), "_default_spawn", SPAWN_PATCH)
    # Only a module that is already imported can be carrying our patch, so an
    # unload never drags the big auxiliary client into memory to find nothing.
    _restore(sys.modules.get("agent.auxiliary_client"), "call_llm", AUX_PATCH)


def is_installed() -> dict[str, bool]:
    """Whether each patch is live IN THIS PROCESS, read off the patched
    functions — the truth whichever copy of this package installed them."""
    kbd = _dispatch_module()
    auxiliary_client = _aux_module()
    return {"spawn": kbd is not None and core.patched(kbd, "_default_spawn", SPAWN_PATCH),
            "auto_composer": (auxiliary_client is not None
                              and core.patched(auxiliary_client, "call_llm", AUX_PATCH))}
