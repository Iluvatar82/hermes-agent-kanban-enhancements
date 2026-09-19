"""Test harness: import the plugin the way Hermes does, against a real checkout.

The Hermes checkout is found through ``HERMES_AGENT_REPO``, else the default
install location. Without it the whole suite skips — these tests exercise the
seams between this plugin and Hermes, so a stub would only test the stub.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "kanban-enhancements"
PACKAGE_NAME = "kanban_enhancements"


def _hermes_repo() -> Path | None:
    candidates = [os.environ.get("HERMES_AGENT_REPO")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "hermes" / "hermes-agent"))
    candidates.append(str(Path.home() / ".hermes" / "hermes-agent"))
    for candidate in candidates:
        if candidate and (Path(candidate) / "hermes_cli" / "kanban_db.py").is_file():
            return Path(candidate)
    return None


REPO = _hermes_repo()
if REPO is None:  # pragma: no cover - environment dependent
    pytest.skip("no Hermes checkout found (set HERMES_AGENT_REPO)", allow_module_level=True)

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_package():
    if PACKAGE_NAME in sys.modules:
        return sys.modules[PACKAGE_NAME]
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)
    return module


plugin = _load_package()


@pytest.fixture
def pkg():
    return plugin


@pytest.fixture
def board_root(tmp_path, monkeypatch):
    """Point the plugin's board-scoped state and logs at a temp directory."""
    kb = plugin.core.kanban_db()
    root = tmp_path / "kanban"
    logs = root / "logs"
    logs.mkdir(parents=True)
    monkeypatch.setattr(kb, "kanban_db_path", lambda board=None: root / "kanban.db")
    monkeypatch.setattr(kb, "worker_logs_dir", lambda board=None: logs)
    monkeypatch.setattr(kb, "worker_log_path", lambda task_id, *, board=None: logs / f"{task_id}.log")
    monkeypatch.setattr(kb, "get_current_board", lambda: "default")
    return root
