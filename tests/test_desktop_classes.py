"""Every Tailwind class the desktop plugin ships must be one Hermes generates.

Tailwind v4 builds the app's stylesheet by SCANNING SOURCE AT BUILD TIME. A
plugin loaded from disk at runtime is not in that scan, so a class only has a
rule behind it if the same string happens to appear in Hermes' own source. An
invented arbitrary value produces nothing at all, silently — which is how the
task drawer shipped with `w-96 max-w-[55%]` and no width, the context meter
with `h-[3px]` and no height, and the meta table with no label column.

Plain utilities (`flex`, `px-3`, `truncate`) are not checked: Tailwind's core
set is generated from the app's own thousands of uses, and a miss there is
vanishingly unlikely. Arbitrary values — anything in `[...]` — are checked
against the checkout, because those exist only where somebody wrote them.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from .conftest import PLUGIN_DIR, REPO

PLUGIN_JS = PLUGIN_DIR / "desktop" / "plugin.js"

#: `text-[0.75rem]`, `grid-cols-[auto_minmax(0,1fr)]`, `[writing-mode:vertical-rl]`.
_ARBITRARY_RE = re.compile(r"[A-Za-z0-9:/-]*-?\[[^\]\s'\"]+\]")

_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
_LINE_COMMENT_RE = re.compile(r"^\s*//.*$", re.MULTILINE)
_STRING_RE = re.compile(r"'([^'\\]*)'")


def _shipped_source() -> str:
    """The file without its comments — those discuss classes, they do not ship."""
    text = PLUGIN_JS.read_text(encoding="utf-8")
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text))


def _class_value(source: str, start: int) -> str:
    """The whole value of the ``className:`` property at ``start`` — the plain
    string, or the entire ``cn(...)`` call, brackets and nesting included."""
    depth, index = 0, start
    while index < len(source):
        char = source[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            break
        elif char == "'":
            index = source.index("'", index + 1)
        index += 1
    return source[start:index]


def class_names(source: str) -> set[str]:
    """Every class this file puts in a ``className``. Scanning the whole file
    instead would read JS array indexing and regex literals as classes."""
    names: set[str] = set()
    for match in re.finditer(r"className:\s*", source):
        for literal in _STRING_RE.findall(_class_value(source, match.end())):
            names.update(literal.split())
    return names


def _hermes_emits(needle: str) -> bool:
    """Whether Hermes' own source contains this exact class string."""
    found = subprocess.run(
        ["grep", "-rlF", "--include=*.tsx", "--include=*.ts", "--include=*.jsx",
         "--include=*.js", "--include=*.css", "--include=*.html", needle, str(REPO)],
        capture_output=True, text=True, check=False).stdout
    return any("node_modules" not in line for line in found.splitlines())


def test_every_arbitrary_class_has_a_rule_behind_it():
    names = class_names(_shipped_source())
    used = sorted(name for name in names if _ARBITRARY_RE.fullmatch(name))
    # A guard that finds nothing because its scanner broke is worse than none:
    # it must reach both plain `className:` strings and the ones inside a cn().
    assert len(names) > 100 and {"ml-auto", "ring-1"} <= names
    assert len(used) > 5, used

    missing = [name for name in used if not _hermes_emits(name)]
    assert missing == [], (
        "Hermes generates no rule for these — put the value in an inline style instead: "
        + ", ".join(missing))


@pytest.mark.parametrize("gone", ["w-96", "max-w-[55%]", "h-[3px]", "min-h-(--"])
def test_the_classes_that_had_no_rule_are_gone(gone):
    """Named one by one so a regression says which one came back."""
    assert gone not in _shipped_source()
