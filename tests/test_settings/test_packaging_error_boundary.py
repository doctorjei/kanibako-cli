"""Every packaging-defect raise reaches the user as an ``Error:`` line, not a traceback.

⚑ WHY THIS FILE EXISTS.  ``cli.main`` catches :class:`~kanibako.errors.KanibakoError`
and ONLY that base (``errors.py`` ``KanibakoError`` docstring).  A packaging defect
raised as a bare ``RuntimeError`` therefore escapes the handler and reaches the user
as a Python traceback — the one output shape kanibako never intends to print.  The
canon-root guards already raised :class:`~kanibako.errors.PackagingError`; these pins
cover the REST of the packaging-defect raises, which were still ``RuntimeError``.

⚑ ONE SWEEP, ONE SHAPE.  The cases below share a shape — a reader whose only input
is the packaged data — so they are one table rather than a dozen tests: arrange the
defect, drive the reader, assert the class.  A reader that moves to ``RuntimeError``
again fails here.

⚑ WHAT IS DELIBERATELY NOT HERE.  ``pseudo_tier_default``'s non-addressable
``agent_id`` raise stays a ``RuntimeError`` — an agent id outside
``ADDRESSABLE_PSEUDO_AGENTS`` is a CALLER's bad argument, not a broken install, and
``tests/test_settings/test_settings_launch.py`` pins it as one.  It is left as a
``RuntimeError`` precisely because ``PackagingError`` is NOT a ``RuntimeError``
(v1.8.0 is a clean break, no alias and no subclassing), so that existing pin FAILS if
the verdict is ever revisited silently.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from kanibako.errors import PackagingError
from kanibako.settings import core_defaults


def _proj_no_project_path(vault_root):
    """A ``ProjectPaths`` stand-in whose ``project_path`` is unprobed (the Q106 shape).

    ``core_default_categories`` reads exactly three attributes off *proj*, so a plain
    namespace is enough — and it keeps the ``meta_ref`` refusal driven by the REAL
    unprobed probe rather than a mock's return value.
    """
    return types.SimpleNamespace(
        project_path=None,
        vault_ro_path=vault_root / "ro",
        vault_rw_path=vault_root / "rw",
    )


def _core_entry_without_meta_ref():
    """A shipped ``core:`` entry that names ``project_path`` with no ``meta_ref``.

    Every entry the file ACTUALLY ships carries one (the workspace at ``meta_ref``,
    the vaults at ``mode_meta_ref``), so this shape cannot arise from a correct
    install — which is what makes the refusal a packaging defect and not a box state.
    """
    return {"core": [{
        "key": "workspace", "category": "bindings.rw", "source": "project_path",
        "box_dest": "~/workspace", "options": "Z,U",
    }]}


#: ``(case id, doc-or-None, the packaged path to fake, call, message the refusal owes)``.
#: ``doc`` is what ``_load_doc`` returns — ``None`` means "leave the real file alone",
#: which is the case for the one defect that is about a FILE on disk, not its text.
_MOVED_RAISES = [
    (
        "behavior_default_absent_key",
        {"agent_default": {}},
        None,
        lambda: core_defaults.behavior_default("access"),
        "agent_default.access",
    ),
    (
        "behavior_default_none_row",
        {"agent_default": {"model": None}},
        None,
        lambda: core_defaults.behavior_default("model"),
        "as <None>",
    ),
    (
        "pseudo_tier_default_absent_row",
        {"agent_shell": {}},
        None,
        lambda: core_defaults.pseudo_tier_default("shell", "no_such_key"),
        "declares no 'agent_shell.no_such_key'",
    ),
    (
        "pseudo_tier_default_none_row",
        {"agent_shell": {"model": None}},
        None,
        lambda: core_defaults.pseudo_tier_default("shell", "model"),
        "as <None>",
    ),
    (
        "env_section_is_not_a_mapping",
        {"env": "TERM"},
        None,
        lambda: core_defaults.env_default_categories(),
        "must be scope-keyed tables",
    ),
    (
        "env_scope_is_not_a_table",
        {"env": {"box": "truecolor"}},
        None,
        lambda: core_defaults.env_default_categories(),
        "env.box",
    ),
    (
        "env_scope_head_is_not_declared",
        {"env": {"sytem": {"TERM": "x"}}},
        None,
        lambda: core_defaults.env_default_categories(),
        "is not a scope",
    ),
    (
        "env_var_is_not_an_env_name",
        {"env": {"box": {"1BAD-NAME": "x"}}},
        None,
        lambda: core_defaults.env_default_categories(),
        "env-var name",
    ),
    (
        "core_entry_has_no_meta_ref",
        _core_entry_without_meta_ref(),
        None,
        lambda: core_defaults.core_default_categories(
            None,
            _proj_no_project_path(Path("/nonexistent-vault-root")),
            enable_vault=True,
            mode="standalone",
            guarantee_create=False,
        ),
        "no value for that source",
    ),
    (
        "kickoff_is_not_exactly_one_entry",
        {"kickoff": []},
        None,
        lambda: core_defaults.kickoff_box_dest(),
        "EXACTLY ONE 'kickoff:' entry",
    ),
    (
        "packaged_kickoff_loader_is_missing",
        None,
        "kickoff",
        lambda: core_defaults.kickoff_default_categories(None),
        "packaged kickoff loader is missing",
    ),
    (
        "seed_lands_in_the_managed_canon_region",
        None,
        None,
        lambda: core_defaults.assert_canon_bind_seed_disjoint(
            {"canon/charter"}, {"canon/charter/general/ROM_GENERAL.md"},
        ),
        "EACCES AT CREATE",
    ),
    (
        "internal_bind_row_is_missing",
        {"kani": [], "kickoff": [], "images": []},
        None,
        lambda: core_defaults.internal_bind_keys(),
        "has no 'images' row keyed 'images_conf'",
    ),
]


@pytest.mark.parametrize(
    # ``case`` is the row's human name — it rides in the tuple because ``ids``
    # below turns each one into the test name pytest reports.
    ("case", "doc", "packaged_path", "call", "expected"),
    _MOVED_RAISES,
    ids=[case[0] for case in _MOVED_RAISES],
)
def test_a_packaging_defect_raises_packaging_error(
    monkeypatch, tmp_path, case, doc, packaged_path, call, expected,
):
    """⚑ MUTATION PROOF — change ANY case's raise back to ``RuntimeError`` and this goes red.

    The message is asserted alongside the class because the class is what ``cli.main``
    dispatches on and the message is what the user reads; a reword must not quietly
    drop the file-and-key the refusal owes.
    """
    if doc is not None:
        monkeypatch.setattr(core_defaults, "_load_doc", lambda: doc)
    if packaged_path == "kickoff":
        real = core_defaults.packaged_data_dir

        def _fake(*parts: str):
            if tuple(parts) == core_defaults.KICKOFF_PACKAGED_PARTS:
                return tmp_path / "gone" / "KICKOFF.md"
            return real(*parts)

        monkeypatch.setattr(core_defaults, "packaged_data_dir", _fake)

    with pytest.raises(PackagingError, match=expected):
        call()


def test_the_keyspace_manifest_defect_raises_packaging_error(monkeypatch, tmp_path):
    """The registry is RELEASE AUTHORITY and ships in the wheel — an unparseable one is packaging.

    Separate from the sweep only because it needs its own module's ``lru_cache``
    cleared on BOTH sides: the bad result would otherwise outlive the monkeypatch and
    hand the next reader a cached list.
    """
    from kanibako.settings import keyspace_manifest

    bad = tmp_path / "keyspace-manifest.yaml"
    bad.write_text("- not\n- a mapping\n")
    monkeypatch.setattr(
        keyspace_manifest, "packaged_data_dir", lambda *p: bad
    )
    keyspace_manifest._parse_manifest.cache_clear()
    try:
        with pytest.raises(PackagingError, match="is empty or is not a YAML mapping"):
            keyspace_manifest.manifest_doc()
    finally:
        keyspace_manifest._parse_manifest.cache_clear()


def test_a_packaging_defect_reaches_the_user_as_an_error_line_not_a_traceback(
    monkeypatch, capsys,
):
    """⚑ THE USER-FACING CLAIM.  rc 1, one ``Error:`` line, and NO traceback.

    Everything below ``args.func`` is the REAL production chain —
    ``agent_cmd._label_floor`` → ``core_defaults.behavior_default`` → the packaged
    ``agent_default:`` table — with only the TABLE monkeypatched, which is the defect.
    The unit pins above prove each raise carries the class; only a real
    ``cli.main`` dispatch proves the class is the thing that buys the clean exit.

    MUTATION: change one case's raise back to ``RuntimeError`` and ``main`` stops
    catching it — the exception escapes ``main`` instead of ``SystemExit(1)``, so this
    test errors rather than asserting a traceback was printed.
    """
    from unittest.mock import MagicMock, patch

    from kanibako.cli import main
    from kanibako.commands import agent_cmd

    # THE PACKAGING DEFECT: the shipped floor has no ``label`` row to read.
    monkeypatch.setattr(core_defaults, "_load_doc", lambda: {"agent_default": {}})

    with (
        patch("kanibako.cli.build_parser") as mock_bp,
        patch("kanibako.cli._setup_nudge"),
        pytest.raises(SystemExit) as exc_info,
    ):
        args = MagicMock()
        args.command = "agent"
        args.agent_command = "info"
        # The adapter is the only seam: below it the production read runs for real.
        args.func = lambda _ns: agent_cmd._label_floor("claude")
        mock_bp.return_value.parse_args.return_value = args
        main(["agent", "info", "claude"])

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Error: " in err, f"expected an Error: line, got stderr {err!r}"
    assert "agent_default.label" in err, err
    assert "Traceback" not in err, f"a traceback reached the user: {err!r}"
