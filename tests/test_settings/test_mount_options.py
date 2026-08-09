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
