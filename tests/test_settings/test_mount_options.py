"""``settings_categories.is_read_only`` — the ONE reader of a mount's ``ro`` flag.

Two call sites used to ask this question by string EQUALITY against ``"ro"``
(``commands/start._emit_category_mounts``, ``commands/workset_cmd``).  That was
correct only because ``_bind_options`` emits exactly ``"ro"`` or ``"Z,U"`` today.
The collapse folds options into a comma list, at which point a read-only entry
spelled ``"ro,Z"`` reads as rw — and the start.py site would then ``mkdir`` a
missing read-only source instead of dropping the mount.  A shape flip that keeps
arity fails no type check, so these tests are the guard.

⚑ TOKEN, NOT SUBSTRING.  ``"rbind"`` and ``"nodirop"`` both CONTAIN ``ro``; a
substring test would call them read-only.  That negative is the whole reason the
predicate splits on commas rather than searching.
"""

from __future__ import annotations

import pytest

from kanibako.settings.settings_categories import is_read_only


@pytest.mark.parametrize("options", ["ro", " ro ", "ro,Z", "ro,Z,U", "Z,U,ro", "Z,ro,U"])
def test_ro_token_anywhere_in_the_list_is_read_only(options):
  assert is_read_only(options) is True


@pytest.mark.parametrize("options", [None, "", ",", " ", "Z,U", "rw", "Z"])
def test_absent_ro_token_is_not_read_only(options):
  assert is_read_only(options) is False


@pytest.mark.parametrize("options", ["rbind", "nodirop", "rro", "ro2", "prod,rbind"])
def test_a_token_merely_containing_ro_is_not_matched(options):
  """The substring trap: containing ``ro`` is not carrying the ``ro`` FLAG."""
  assert is_read_only(options) is False


def test_todays_bind_options_values_read_exactly_as_they_always_did():
  """Byte-identical to the retired ``== "ro"`` on the values emitted today."""
  # Spelled as literals, not imported from the category-default helper: this pins
  # the VALUES, not the function that happens to produce them.
  assert is_read_only("ro") is True  # bindings.ro
  assert is_read_only("Z,U") is False  # bindings.rw / caches / common


# --------------------------------------------------------------------------- #
# ⚑⚑ ``None`` vs ``""`` — THREE OPTION STATES, AND ONE OF THEM IS A LIVE SOCKET #
# --------------------------------------------------------------------------- #


def test_an_explicitly_empty_options_entry_does_NOT_take_the_category_default(
  tmp_path,
):
  """🛑 THE HELPER SOCKET. ``opts or _bind_options(category)`` breaks the box.

  ``settings_launch._emit_bind`` reads ``opts if opts is not None else
  _bind_options(category)``, and the ``is not None`` is LOAD-BEARING: a declared
  ``options: ""`` means *explicitly no options*, while ``None`` means *unset, take
  the category default*. ``helper_sock`` is declared ``bindings.rw`` with
  ``options: ""`` DELIBERATELY (``core-defaults.yaml:386-395``) — it is a unix
  socket the hub listens on, and the ``Z,U`` relabel/chown the rw default would
  add breaks the shared socket topology.

  ⚑ THIS IS THE MUTANT THIS TEST EXISTS FOR: flip that test to truthiness and the
  socket silently acquires ``Z,U``. The mount is still emitted, at the same arity,
  with the same source and dest — so every count-and-shape assertion in the suite
  stays green while the box stops working
  [[same-arity-shape-flip-passes-silently]]. Only the VALUE catches it.
  """
  from kanibako.commands.start import _bind_map_from_mounts, _emit_category_mounts
  from kanibako.settings import core_defaults
  from kanibako.settings.settings_categories import reconcile_categories
  from kanibako.settings.settings_launch import (
    build_launch_snapshot,
    snapshot_category_entries,
  )
  from kanibako.settings.settings_resolve import ResolveCtx

  sock = tmp_path / "helper.sock"
  sock.touch()  # the producer gates each entry on ``.exists()``
  log = tmp_path / "helpers.jsonl"
  log.touch()
  ctx = ResolveCtx(
    agent_name="claude", workset_name=None, host_home=str(tmp_path),
    xdg={"XDG_DATA_HOME": str(tmp_path / "data")},
  )
  snap = build_launch_snapshot(
    agent_name="claude", ctx=ctx,
    system_path=None, agent_path=None, workset_path=None, box_path=None,
    default_categories=core_defaults.helper_default_categories(
      socket_path=sock, log_path=log,
    ),
  )
  entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
  sock_dest = "/home/agent/.kanibako/state/helper.sock"
  log_dest = "/home/agent/.kanibako/state/helpers.jsonl"
  options = {e.box_dest: e.options for e in entries}

  # THE MUTANT'S TARGET, at the seam that decides it: an explicit "" survives the
  # category default, and a rw entry that declares NOTHING would take ``Z,U``.
  assert options[sock_dest] == "", (
    "helper_sock must carry NO options — a Z/U relabel breaks the socket"
  )
  # NON-VACUOUS: the sibling helper LOG in the SAME table carries its declared
  # options, so "" is not simply what this path produces for everything. (Its own
  # ``@``-ref source needs the workset floor this narrow harness omits, so it is
  # pinned here at the entry rather than at the emitted Mount.)
  assert options[log_dest] == "ro", options

  # And the value survives DELIVERY: the falsy-drop in ``Mount.to_volume_arg``
  # yields the 2-field form, because ``src:dst:`` is rejected outright by podman.
  rec = reconcile_categories(entries)
  by_dest = {
    m.destination: m for m in _emit_category_mounts(
      _bind_map_from_mounts(rec.mounts), label="options-fidelity",
    )
  }
  assert by_dest[sock_dest].options == "", sorted(by_dest)
  assert by_dest[sock_dest].to_volume_arg() == f"{sock}:{sock_dest}"
