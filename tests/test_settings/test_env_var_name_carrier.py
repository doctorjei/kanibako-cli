"""The env-variable NAME shape has ONE spelling: ``agent_ref.ENV_VAR_NAME``.

Eight copies of ``[A-Za-z_][A-Za-z0-9_]*`` once lived across the settings stack, the
launch emit and two messages; every regex now composes the constant and every message
interpolates it.  This scan keeps a ninth copy out of shipped source.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CARRIER = _REPO / "src" / "kanibako" / "agent_ref.py"
#: Files allowed to spell the shape themselves.
_INDEPENDENT = {
    # [F1] kinemata_keyspace: the oracle re-implements membership ON PURPOSE.
    _REPO / "src" / "kanibako" / "settings" / "kinemata_keyspace.py",
}
_SHAPE = "[A-Za-z_][A-Za-z0-9_]*"


def _shipped_sources() -> list[Path]:
    roots = [_REPO / "src" / "kanibako", *sorted((_REPO / "packages").glob("*/src"))]
    return sorted(p for root in roots for p in root.rglob("*.py"))


def test_the_constant_is_the_shape() -> None:
    from kanibako.agent_ref import ENV_VAR_NAME
    from kanibako.settings.settings_categories import ENV_KEY_RE, SECRET_VAR_RE

    assert ENV_VAR_NAME == _SHAPE
    assert SECRET_VAR_RE.pattern == f"^{ENV_VAR_NAME}$"
    assert ENV_KEY_RE.match("box.env.FOO_1") is not None
    assert ENV_KEY_RE.match("box.env.1FOO") is None


def test_no_string_literal_respells_the_shape() -> None:
    offenders = []
    for path in _shipped_sources():
        if path == _CARRIER or path in _INDEPENDENT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _SHAPE in node.value
            ):
                offenders.append(f"{path.relative_to(_REPO)}:{node.lineno}")
    assert not offenders, (
        "re-spelled env-name shape; compose agent_ref.ENV_VAR_NAME instead: "
        + ", ".join(offenders)
    )


def test_the_scan_sees_a_respelling() -> None:
    """The walk is not vacuous: an f-string part carrying the shape is a Constant."""
    tree = ast.parse(f'x = r"^{_SHAPE}$"\ny = f"{{a}}{_SHAPE}"\n')
    hits = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _SHAPE in n.value
    ]
    assert len(hits) == 2
