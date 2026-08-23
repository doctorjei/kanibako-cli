"""The registry's ``set:`` column, ASSERTED BY RUNNING THE VERB.

⚑ THE DEFECT CLASS THIS EXISTS TO MAKE IMPOSSIBLE (P15): a DECLARED, CLI-settable key
re-classified in CODE as a "structural"/file-only key and refused.  That is how
``system.template`` — named in spec §2a's own CLI-settable list, beside
``workset.vault_{ro,rw}`` and ``agent.<agent>.canon`` — spent the 1.8.0 cycle unsettable,
answering a refusal that told the user to hand-edit ``kanibako_config.yaml``.  Nothing
went red, because every existing pin asked the code what it believed rather than asking
the registry what is true.

⚑ DERIVED, NEVER LISTED (P13).  The expectation is the manifest's own ``set:`` column
(``cli+file`` · ``file`` · ``never``, legend at the head of its ``keys:`` table), so
declaring a key, retiring one or re-classifying one moves this test with NO edit here.
There is no allow-list and no exemption: a row the code disagrees with is a FINDING.

⚑ IT REDS ON ITS OWN EMPTINESS.  Every bucket is asserted non-empty, and every row this
file does not exercise must PROVE it is parametric (a ``<placeholder>`` spelling the
registry cannot resolve to one CLI key without inventing the discriminator).

⚑ EFFECT-BASED, like its ``Bench`` neighbour: it runs the REAL ``set_config_value``
against real files.  A predicate-level pin would only re-state the code's own belief,
which is the thing that was wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config_keys import (
    AGENT_DEFAULT_SUB,
    ConfigLevel,
    _is_agent_setting,
)
from kanibako.settings.keyspace_manifest import manifest_doc

from tests.test_settings.test_config_dest_parity import Bench


# A benign, well-typed value per declared ``type:``.  A TYPE table, not a key list —
# it does not grow when the keyspace does.
_VALUE_FOR_TYPE: dict[str, str] = {
    "path": "/tmp/set-column-probe",
    "str": "probe",
    "bool": "true",
    "enum": "full",            # the most permissive ACCESS_TIERS member
    "int": "1",
    "dict": "probe",
    "list": "probe",
    "version_marker": "1.8.0",
}

# Which command scope OWNS a key, by its scope token (spec §0 directional rule: a file
# may write its own scope and any scope it contains, never upward).  ``config.*`` and
# ``agent.*`` are reachable at the system scope only.
_SCOPE_FOR_TOKEN: dict[str, ConfigLevel] = {
    "config": ConfigLevel.system,
    "system": ConfigLevel.system,
    "agent": ConfigLevel.system,
    "meta": ConfigLevel.system,
    "workset": ConfigLevel.workset,
    "box": ConfigLevel.box,
    "pref": ConfigLevel.box,
}


def _cli_spelling(key: str) -> str:
    """The spelling a user TYPES for registry row *key*.

    The one divergence is the any-agent tier: the registry spells it
    ``agent.default.<leaf>`` and the CLI spells it BARE (``model``, ``access``, …).
    ⚑ Taken from the code's OWN rule (:func:`_is_agent_setting`), not from a list, so a
    leaf entering or leaving that family carries this with it.
    """
    prefix = f"agent.{AGENT_DEFAULT_SUB}."
    if key.startswith(prefix):
        leaf = key[len(prefix):]
        if _is_agent_setting(leaf):
            return leaf
    return key


def _static_rows() -> "dict[str, dict]":
    """Registry rows whose spelling names ONE key — the rows a CLI verb can be handed."""
    return {
        str(k): v for k, v in manifest_doc()["keys"].items()
        if isinstance(v, dict) and "<" not in str(k)
    }


def _rows_with(set_column: str) -> "dict[str, dict]":
    return {k: v for k, v in _static_rows().items() if v.get("set") == set_column}


def _refusal(tmp: Path, key: str, row: dict) -> "str | None":
    """The ``Error: …`` text a real ``set`` answers for *key*, or ``None`` if it took."""
    value = _VALUE_FOR_TYPE.get(str(row.get("type")), "probe")
    scope = _SCOPE_FOR_TOKEN[key.split(".", 1)[0]]
    message = Bench(tmp).set(scope, _cli_spelling(key), value)
    return message if message.startswith("Error:") else None


class TestSetColumnIsWhatTheVerbDoes:

    def test_every_cli_settable_row_accepts_a_set(self, tmp_path):
        """``set: cli+file`` ⇒ the CLI verb WRITES it. No key is silently structural."""
        findings: list[str] = []
        for i, (key, row) in enumerate(sorted(_rows_with("cli+file").items())):
            refusal = _refusal(tmp_path / f"c{i}", key, row)
            if refusal is not None:
                findings.append(f"  {key} -> {refusal.splitlines()[0]}")
        assert not findings, (
            "the registry declares these keys CLI-settable and the CLI refuses them "
            "(spec §2a: only `config.*` and the bind-shaped category terminals are "
            "refused):\n" + "\n".join(findings)
        )

    def test_every_file_only_row_is_refused(self, tmp_path):
        """``set: file`` ⇒ REFUSED — the §2a carve-outs, and only those."""
        accepted = [
            key for i, (key, row) in enumerate(sorted(_rows_with("file").items()))
            if _refusal(tmp_path / f"f{i}", key, row) is None
        ]
        assert not accepted, (
            f"the registry declares these keys file-only and the CLI wrote them: {accepted}"
        )

    def test_every_never_settable_row_is_refused(self, tmp_path):
        """``set: never`` ⇒ REFUSED — the derived/RO ``meta.*`` group (§0 construct-set)."""
        accepted = [
            key for i, (key, row) in enumerate(sorted(_rows_with("never").items()))
            if _refusal(tmp_path / f"n{i}", key, row) is None
        ]
        assert not accepted, (
            f"the registry declares these keys never-settable and the CLI wrote them: "
            f"{accepted}"
        )


class TestTheDerivationIsNotVacuous:
    """Each guard above must have something to guard, and nothing may be dropped quietly."""

    @pytest.mark.parametrize("set_column", ["cli+file", "file", "never"])
    def test_each_bucket_has_members(self, set_column):
        assert _rows_with(set_column), f"no registry row carries set: {set_column}"

    def test_the_column_admits_no_fourth_value(self):
        """A new ``set:`` value would fall through all three guards unexercised."""
        declared = {str(row.get("set")) for row in _static_rows().values()}
        assert declared == {"cli+file", "file", "never"}, sorted(declared)

    def test_every_unexercised_row_is_parametric(self):
        """A row this file skips must SAY it is parametric in its own spelling."""
        skipped = {
            str(k) for k, v in manifest_doc()["keys"].items()
            if isinstance(v, dict)
        } - set(_static_rows())
        assert all("<" in k for k in skipped), sorted(k for k in skipped if "<" not in k)
        assert skipped, "no parametric rows found — the placeholder convention moved"

    def test_the_static_set_did_not_collapse(self):
        """A filter bug that emptied the row set would pass every guard above."""
        assert len(_static_rows()) >= 80
