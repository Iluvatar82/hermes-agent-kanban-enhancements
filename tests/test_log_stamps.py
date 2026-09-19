"""Per-line worker-log timestamps."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

STAMP = "[2026-09-17T10:23:45+02:00] "


def _stream(pkg, inner=None):
    return pkg.log_stamps.TimestampedStream(inner or io.StringIO(), clock=lambda: STAMP)


def test_format_stamp_is_iso_seconds_with_offset(pkg):
    moment = datetime(2026, 9, 17, 10, 23, 45, 999, tzinfo=timezone(timedelta(hours=2)))
    assert pkg.log_stamps.format_stamp(moment) == STAMP
    assert pkg.log_stamps.STAMP_RE.match(pkg.log_stamps.format_stamp())


def test_every_line_gets_one_stamp_across_partial_writes(pkg):
    stream = _stream(pkg)
    stream.write("Query: work kanban task t_1\n")
    stream.write("  preparing")
    stream.write(" terminal\n\nlast")
    stream.writelines(["\n", "tail\n"])
    assert stream._inner.getvalue() == (
        f"{STAMP}Query: work kanban task t_1\n"
        f"{STAMP}  preparing terminal\n"
        f"{STAMP}\n"
        f"{STAMP}last\n"
        f"{STAMP}tail\n"
    )


def test_wrapper_delegates_everything_else(pkg):
    inner = io.StringIO()
    stream = _stream(pkg, inner)
    assert stream.isatty() is False
    assert stream.write("") == 0
    stream.flush()
    assert stream.getvalue() == ""


def test_strip_timestamps_keeps_other_brackets(pkg):
    strip = pkg.log_stamps.strip_timestamps
    assert strip(f"{STAMP}[2;3m x\n{STAMP}  [exit 128]\n[not a stamp] y\n") == (
        "[2;3m x\n  [exit 128]\n[not a stamp] y\n")
    assert strip("plain\r\nlines\r\n") == "plain\r\nlines\r\n"


def test_only_a_real_file_is_stamped(pkg, tmp_path):
    """A terminal-tool child writes into a pipe — it must never be stamped."""
    is_file = pkg.log_stamps._is_redirected_to_file
    with open(tmp_path / "worker.log", "w", encoding="utf-8") as handle:
        assert is_file(handle) is True
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(write_fd, "w") as pipe:
            assert is_file(pipe) is False
    finally:
        os.close(read_fd)
    assert is_file(io.StringIO()) is False
    assert is_file(None) is False


def test_a_worker_log_never_doubles_a_carriage_return(pkg, tmp_path):
    """A text stream on Windows rewrites every ``\n`` as ``\r\n``, so a line the
    worker echoed from a child process (already CRLF) reached the file as
    ``\r\r\n`` — which the desktop log read as an empty line, every line.
    ``newline="\r\n"`` reproduces that translation on any platform."""
    log = tmp_path / "t_4.log"
    with open(log, "w", encoding="utf-8", newline="\r\n") as handle:
        stream = pkg.log_stamps._wrap(handle)
        assert isinstance(stream, pkg.log_stamps.TimestampedStream)
        stream.write("echoed from a child\r\n")
        stream.write("written right here\n")
    # Read the bytes, not the text: `read_text` would undo the very translation
    # this test is about.
    raw = log.read_bytes().decode("utf-8")
    # The child's own CRLF survives — that is data. Nothing is added to it.
    assert pkg.log_stamps.strip_timestamps(raw) == "echoed from a child\r\nwritten right here\n"


def test_worker_streams_are_stamped_in_a_real_process(pkg, tmp_path):
    """End to end: a child with HERMES_KANBAN_TASK set and stdout on a file."""
    script = (
        "import sys, importlib.util;"
        f"spec = importlib.util.spec_from_file_location('kx', r'{pkg.__file__}',"
        f" submodule_search_locations=[r'{os.path.dirname(pkg.__file__)}']);"
        "mod = importlib.util.module_from_spec(spec); sys.modules['kx'] = mod;"
        "spec.loader.exec_module(mod);"
        "mod.log_stamps.install_worker_streams();"
        "print('hello'); sys.stderr.write('warn\\n')"
    )
    log = tmp_path / "t_1.log"
    env = {**os.environ, "HERMES_KANBAN_TASK": "t_1", "HERMES_KANBAN_BOARD": "default"}
    with open(log, "w", encoding="utf-8") as handle:
        subprocess.run([sys.executable, "-c", script], stdout=handle, stderr=subprocess.STDOUT,
                       env=env, check=True, cwd=str(tmp_path))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert [pkg.log_stamps.strip_timestamps(line) for line in lines] == ["hello", "warn"]
    assert all(pkg.log_stamps.STAMP_RE.match(line) for line in lines)


def test_read_patch_strips_unless_asked(pkg, board_root, monkeypatch):
    kb = pkg.core.kanban_db()
    (board_root / "logs" / "t_2.log").write_text(f"{STAMP}one\n{STAMP}two\n", encoding="utf-8")
    monkeypatch.setattr(kb, "read_worker_log",
                        lambda task_id, *, tail_bytes=None, board=None: f"{STAMP}one\n{STAMP}two\n")
    pkg.log_stamps._read_patched = False
    assert pkg.log_stamps.install_read_patch() is True
    try:
        assert kb.read_worker_log("t_2") == "one\ntwo\n"
        assert kb.read_worker_log("t_2", timestamps=True) == f"{STAMP}one\n{STAMP}two\n"
    finally:
        pkg.log_stamps.uninstall_read_patch()


def test_new_run_starts_on_a_fresh_line(pkg, board_root):
    log = board_root / "logs" / "t_3.log"
    log.write_bytes(b"crashed mid-li")
    pkg.log_stamps._start_on_a_fresh_line("t_3")
    assert log.read_bytes() == b"crashed mid-li\n"
    pkg.log_stamps._start_on_a_fresh_line("t_3")
    assert log.read_bytes() == b"crashed mid-li\n"  # idempotent
