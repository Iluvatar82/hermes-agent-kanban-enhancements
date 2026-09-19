"""The board stop switch: persisted per board, enforced in the dispatcher.

Stop is an operator action, not a pause: the switch is written FIRST (so no
dispatcher can spawn in between), then running workers are reclaimed — task back
to ready, worker terminated — exactly like ``hermes kanban reclaim``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import core
from .core import logger


@dataclass
class BoardState:
    board: str
    stopped: bool = False
    stopped_at: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "stopped": self.stopped,
            "stopped_at": self.stopped_at,
            "reason": self.reason,
        }


@dataclass
class ControlResult:
    state: BoardState
    reclaimed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def get_state(board: str | None = None) -> BoardState:
    slug = core.board_slug(board)
    entry = (core.read_state(board).get("boards") or {}).get(slug) or {}
    return BoardState(
        board=slug,
        stopped=bool(entry.get("stopped")),
        stopped_at=entry.get("stopped_at"),
        reason=entry.get("reason"),
    )


def is_stopped(board: str | None = None) -> bool:
    try:
        return get_state(board).stopped
    except Exception:
        logger.debug("board state unreadable — treating the board as running", exc_info=True)
        return False


def _persist(state: BoardState, board: str | None) -> None:
    data = core.read_state(board)
    boards = data.setdefault("boards", {})
    boards[state.board] = {
        "stopped": state.stopped,
        "stopped_at": state.stopped_at,
        "reason": state.reason,
    }
    core.write_state(data, board)


def _running_task_ids(conn) -> list[str]:
    rows = conn.execute("SELECT id FROM tasks WHERE status = 'running'").fetchall()
    return [row["id"] if hasattr(row, "keys") else row[0] for row in rows]


def stop(conn, *, board: str | None = None, reason: str | None = None,
         terminate_workers: bool | None = None) -> ControlResult:
    """Flip the switch, then reclaim what is running."""
    state = BoardState(board=core.board_slug(board), stopped=True,
                       stopped_at=int(time.time()), reason=reason or None)
    _persist(state, board)

    result = ControlResult(state=state)
    if terminate_workers is None:
        terminate_workers = bool(core.setting("stop_terminates_workers", True))
    if not terminate_workers:
        return result

    kb = core.kanban_db()
    for task_id in _running_task_ids(conn):
        try:
            note = f"board stopped: {reason}" if reason else "board stopped"
            ok = kb.reclaim_task(conn, task_id, reason=note)
        except Exception:
            logger.warning("reclaim of %s failed", task_id, exc_info=True)
            ok = False
        (result.reclaimed if ok else result.failed).append(task_id)
    return result


def start(*, board: str | None = None) -> BoardState:
    """Lift the switch. The caller nudges the dispatcher."""
    state = BoardState(board=core.board_slug(board), stopped=False, stopped_at=None, reason=None)
    _persist(state, board)
    return state
