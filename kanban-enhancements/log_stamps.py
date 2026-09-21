"""Per-line write timestamps for kanban worker logs.

A worker's stdout/stderr are redirected into ``<logs>/<task>.log``. This wraps
both streams so every line starts with ``[<ISO-8601 local time>] `` — the moment
the line was written, not the moment a buffer happened to flush.

Readers keep the classic format: ``read_worker_log`` is wrapped to strip the
stamps unless the caller asks for them, so ``hermes kanban log``, the task
drawer and the reap excerpt look exactly as before.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import sys
from collections.abc import Callable
from datetime import datetime

from . import core
from .core import logger

STAMP_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\] ", re.MULTILINE)

#: The marker the live ``read_worker_log`` carries while this plugin wraps it.
READ_PATCH = "kanban_db.read_worker_log"


def format_stamp(now: datetime | None = None) -> str:
    """``[2026-09-17T10:23:45+02:00] `` — local time with its UTC offset."""
    moment = now or datetime.now().astimezone()
    return f"[{moment.isoformat(timespec='seconds')}] "


def strip_timestamps(text: str) -> str:
    return STAMP_RE.sub("", text) if "[" in text else text


class TimestampedStream:
    """Text stream that prefixes every line with its write time.

    Everything except ``write``/``writelines`` is delegated, so ``isatty``,
    ``fileno``, ``encoding``, ``flush`` and ``reconfigure`` behave as before.
    """

    def __init__(self, inner, clock: Callable[[], str] = format_stamp):
        self._inner = inner
        self._clock = clock
        self._at_line_start = True

    def write(self, data):
        if not isinstance(data, str) or not data:
            return self._inner.write(data)
        stamp = self._clock()
        parts = data.split("\n")
        out: list[str] = []
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            if last and not part:
                break  # data ended with a newline: the next write starts a line
            if self._at_line_start:
                out.append(stamp)
            out.append(part)
            if last:
                self._at_line_start = False
            else:
                out.append("\n")
                self._at_line_start = True
        self._inner.write("".join(out))
        return len(data)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _is_redirected_to_file(stream) -> bool:
    """True only for a real file — never a terminal and never a captured pipe.

    This is what keeps terminal-tool children (whose stdout is a pipe the tool
    reads) out of the stamping path without any environment plumbing.
    """
    try:
        if stream is None or stream.isatty():
            return False
        return stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
    except (AttributeError, OSError, ValueError):
        return False


def _wrap(stream):
    if isinstance(stream, TimestampedStream) or not _is_redirected_to_file(stream):
        return stream
    # LF only, whatever the platform thinks a line ends with. A text stream on
    # Windows rewrites every ``\n`` as ``\r\n``, so a line the worker echoed
    # from a child process (already CRLF) landed in the file as ``\r\r\n`` —
    # a carriage return every reader then had to guess its way around.
    with contextlib.suppress(AttributeError, OSError, TypeError, ValueError):
        stream.reconfigure(newline="\n")
    # One line per write keeps the file in write order and the UI live.
    with contextlib.suppress(AttributeError, OSError, ValueError):
        stream.reconfigure(line_buffering=True)
    return TimestampedStream(stream)


def _start_on_a_fresh_line(task_id: str) -> None:
    """A previous run may have died mid-line; don't stamp into its tail."""
    try:
        path = core.kanban_db().worker_log_path(task_id, board=os.environ.get("HERMES_KANBAN_BOARD") or None)
        size = path.stat().st_size
        if not size:
            return
        with open(path, "rb") as handle:
            handle.seek(size - 1)
            needs_newline = handle.read(1) != b"\n"
        if needs_newline:
            with open(path, "ab") as handle:
                handle.write(b"\n")
    except Exception:
        logger.debug("could not check the log tail", exc_info=True)


def install_worker_streams() -> bool:
    """Stamp this process's log lines when it is a kanban worker. True if active."""
    task_id = (os.environ.get("HERMES_KANBAN_TASK") or "").strip()
    if not task_id:
        return False
    if not (_is_redirected_to_file(sys.stdout) or _is_redirected_to_file(sys.stderr)):
        return False
    _start_on_a_fresh_line(task_id)
    sys.stdout = _wrap(sys.stdout)
    sys.stderr = _wrap(sys.stderr)
    return isinstance(sys.stdout, TimestampedStream) or isinstance(sys.stderr, TimestampedStream)


def install_read_patch() -> bool:
    """Wrap ``read_worker_log`` so existing readers keep the unstamped log.

    Safe to call repeatedly and from any copy of this package. A gateway that
    serves several profiles imports the plugin once per Hermes home, all in one
    process — and this used to be guarded by a module global, so every copy
    wrapped the previous copy's wrapper. The outer one forwarded only
    ``tail_bytes``/``board``, so the inner one stripped the stamps even for a
    caller that asked for them: the Kanban+ log lost its whole time gutter as
    soon as a second profile's copy loaded. The marker on the live function is
    now the single source of truth, exactly like the dispatcher guard's.
    """
    kb = core.kanban_db()
    if core.patched(kb, "read_worker_log", READ_PATCH):
        return True
    original = getattr(kb, "read_worker_log", None)
    if original is None:
        return False

    def read_worker_log(task_id, *, tail_bytes=None, board=None, timestamps=False, **extra):
        text = original(task_id, tail_bytes=tail_bytes, board=board, **extra)
        if text is None or timestamps:
            return text
        return strip_timestamps(text)

    kb.read_worker_log = core.stamp(read_worker_log, READ_PATCH, original)
    return True


def uninstall_read_patch() -> None:
    """Restore core's ``read_worker_log`` — only ever OUR wrapper, by the
    original it recorded on itself, whichever copy of this package installed it."""
    kb = core.kanban_db()
    if not core.patched(kb, "read_worker_log", READ_PATCH):
        return
    original = core.original_of(kb, "read_worker_log")
    if original is not None:
        kb.read_worker_log = original


def read_patch_installed() -> bool:
    """Whether the reader patch is live in THIS process, read off the function itself."""
    try:
        return core.patched(core.kanban_db(), "read_worker_log", READ_PATCH)
    except Exception:
        return False
