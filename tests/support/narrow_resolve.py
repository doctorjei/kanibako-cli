"""The dests an INJECTED default-category table declares — for narrow-resolve tests.

A NARROW resolve (``include_base_families=False``) emits only its OWN injected
table's dests: ``commands.start._resolve_launch_snapshot`` takes them as
*narrow_bind_dests* and answers with ``LaunchDeliveries.narrow_bindings``.  The two
LIVE narrow callers read theirs out of the shipped defaults document
(``core_defaults.helper_bind_dests`` / ``image_bind_dests``); a test that injects a
table BUILT IN PYTHON — the channel table, a plugin's ``default_common()`` — has no
such reader, so this is the same rule for those: read the dests from the SAME rows
that declare the binds, normalized with the SAME function that keys the emitter's
map (``core_defaults._table_bind_dests``' pattern, and its reason — a dest spelled
twice is a dest that can drift).

⚑ WHICH ARMS COUNT is asked of ``settings_categories``' own delivery table rather
than answered from a list here: a table's MOUNT arms are dest-keyed, and a second
enumeration of which categories those are is exactly the drift this avoids.

⚑ TEST SUPPORT, not a second production spelling: production tables are declarative
and already have their reader.
"""

from __future__ import annotations


def table_bind_dests(table) -> "frozenset[str]":
  """The normalized box dests every MOUNT arm of *table* names."""
  from kanibako.settings.settings_categories import MOUNT, _DELIVERY
  from kanibako.settings.settings_resolve import normalize_bind_dest

  dests: set[str] = set()
  for arm, entries in table.items():
    # An arm key is ``<scope>[.<agent>].<category>``; the category is the tail,
    # and ``bindings.ro`` / ``bindings.rw`` are two-segment tails.
    parts = arm.split(".")
    category = next(
      (c for c in (".".join(parts[-2:]), parts[-1]) if _DELIVERY.get(c) == MOUNT),
      None,
    )
    if category is None or not isinstance(entries, dict):
      continue
    dests |= {normalize_bind_dest(str(dest)) for dest in entries}
  return frozenset(dests)
