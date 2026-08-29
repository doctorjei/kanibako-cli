"""Every default kanibako ships, beside the ARTEFACT that declares it — ``system defaults``.

``config show --effective`` resolves the cascade without reporting a SOURCE, so a user
cannot tell a declared default from a value sitting in their own system file.  This
module answers the other question: what does kanibako declare out of the box, at which
scope, and WHERE IS IT WRITTEN DOWN.  It is the install-wide STATIC view — no box, no
project, no resolution.

⚑ READ-ONLY, AND IT DECLARES NOTHING.  No key is introduced, no default is invented, no
value is computed.  Every value printed is read from the artefact that already carries
it (the shipped manifest, ``core-defaults.yaml``, a plugin's defaults file); every key
comes from a live code carrier or from the manifest.  A row this module cannot source is
a RED TEST, never a fabricated cell.

⚑⚑ THE SOURCE CLASSIFICATION IS NOT A SECOND ONE.  ``tests/test_settings/
test_manifest_conformance.py`` already classifies every manifest ``default:`` row
against its producing code carrier, and that partition is the one used here — the labels
below are the human-plain spelling of its classes.
:mod:`tests.test_settings.test_defaults_inventory` asserts the two partitions EQUAL, so
the moment a row moves between classes there, this listing goes red rather than drifting
into a second opinion about where a default lives.

⚑ WHERE A GROUP'S KEY SET CAN BE READ OFF THE LIVE CARRIER, IT IS.  ``paths_defaults``'
two tables, the two ``settings_launch`` floor builders and ``core-defaults.yaml``'s
``agent_default:`` section all ENUMERATE their own keys, so those five groups are
derived and self-correcting; only the rows whose carrier is a dataclass field, a
read-with-default accessor or a lone constant are named by hand.

⚑ THE LAUNCH-DERIVED ``KANIBAKO_*`` STAMPS ARE NOT LISTED, AND THE OMISSION IS PRINTED.
They are realized per launch by ``commands.start._core_env_default_categories`` from a
resolved project and a resolved agent.  THE DECIDING FACT IS THAT THE SET ITSELF IS NOT
STATIC: two of the four (``KANIBAKO_NAME``, ``KANIBAKO_AGENT``) are not emitted AT ALL for
an unnamed project or an agentless box, so a row printed here would be a fabrication
rather than a default.  The other two DO have fixed values (``kickoff_guest_dest()`` and
``vscode_config.AGENT_MARKERS_DIR``) — but listing half a class is worse than omitting
the whole class by name.
Reaching the launch pair's one spelling would also mean importing the launch command module — by
some margin the largest in the tree — onto the path of a read-only display, and
fabricating a project stand-in to call a private launch function with.  So the section
prints a one-line NOTICE naming the class and the surface that DOES show them, which
costs nothing and lies about nothing.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from kanibako.settings import core_defaults
from kanibako.settings.keyspace_manifest import manifest_doc

#: Substituted for the manifest's ``<agent>`` placeholder when a floor builder needs an
#: agent name.  ONLY the builder's KEY SET is consumed (never a value), and none of the
#: six auth keys taken from it mentions the agent — the name is interpolated into the
#: ``meta.box.agent.*`` mirror ref, which is not a default row.  The inventory test pins
#: that the key set is identical under two different names, so this cannot start
#: mattering silently.
_PROBE_AGENT = "default"

#: The three box modes, in the order the spec tables them.
_MODES = ("primary", "named", "standalone")


class DefaultRow(NamedTuple):
  """One printable default: what it is called, what it is, and who declares it."""

  key: str
  value: str
  scope: str
  source: str
  #: ``((mode-list, value), …)`` for a per-mode default; the ``value`` cell then reads
  #: ``(varies by box mode)`` and these render as continuation lines.
  per_mode: tuple[tuple[str, str], ...] = ()
  #: An INTERNAL bind — generated content at a fixed location, no user-addressable key.
  internal: bool = False


# --------------------------------------------------------------------------- #
# Section 1 — the registry's default-carrying key rows
# --------------------------------------------------------------------------- #

def manifest_default_rows() -> dict[str, dict[str, Any]]:
  """The manifest ``keys:`` rows carrying a ``default:`` — section 1's whole corpus."""
  return {
    str(key): row
    for key, row in manifest_doc()["keys"].items()
    if isinstance(row, dict) and "default" in row
  }


def source_groups() -> tuple[tuple[str, frozenset[str]], ...]:
  """``(source label, keys)`` partitioning every default-carrying registry row.

  The labels are what a user is told; the SETS are what the inventory test asserts
  against ``test_manifest_conformance``'s own classification.  Order is irrelevant here
  (rows are sorted for display), but the partition must be exhaustive and disjoint —
  :func:`key_rows` raises if it is not, so an unclassified row cannot print blank.
  """
  from kanibako.settings.paths_defaults import CONFIG_PATH_DEFAULTS, SYSTEM_PATH_DEFAULTS
  from kanibako.settings.settings_launch import auth_chain_floor, workset_anchor_floor

  declared = frozenset(manifest_default_rows())

  def _floor(built: dict[str, object]) -> frozenset[str]:
    """The keys a floor builder declares that are ALSO registry default rows."""
    return frozenset(built) & declared

  return (
    # --- carriers that enumerate their own keys (derived, self-correcting) --- #
    ("paths_defaults.py (config tier)", frozenset(CONFIG_PATH_DEFAULTS)),
    ("paths_defaults.py (system tier)", frozenset(SYSTEM_PATH_DEFAULTS)),
    # ⚑ WIDENED 2026-08-29, from 6 keys to 9. ``workset.{skip_kuid_check,registry,kuid}``
    # were declared rows that no floor emitted, so a whole-value ``@``-ref to any of them
    # resolved to ``__MISSING__`` at launch; they are now built by the same builder (the
    # ``workset.channelroot`` fix of 2026-08-25, R-35 "fix the CODE", applied to the three
    # rows it left behind). The group is DERIVED, so the widening arrived here on its own —
    # what moved by hand is the three labels below that used to claim them.
    ("settings_launch.py (anchor floor)", _floor(workset_anchor_floor(mode="primary"))),
    ("settings_launch.py (auth floor)", _floor(
      auth_chain_floor(mode="primary", agent_name=_PROBE_AGENT))),
    ("core-defaults.yaml (agent_default:)", frozenset(
      f"agent.default.{leaf}" for leaf in core_defaults.behavior_defaults())),
    # The ``env:`` table's own keys, intersected with the registry: the file may ship an
    # env default the registry has not (yet) enumerated, and only the enumerated ones
    # are section-1 rows. ⚑ The label is the SAME string section 3 prints, deliberately
    # — ``box.env.COLORTERM`` is one fact reported by two sections (a DECLARED KEY and
    # a variable a box gets), not two opinions about where it comes from.
    ("core-defaults.yaml (env:)", frozenset(core_defaults.env_default_categories())
     & declared),
    # --- carriers with nothing to enumerate: named, one reason each --- #
    # Fields of the ``KanibakoConfig`` dataclass.
    ("config.py (KanibakoConfig field)", frozenset({"box.image", "box.share_images"})),
    # ``read_*`` accessors whose declared default is what they return with no file.
    # ⚑ ``workset.skip_kuid_check`` LEFT THIS GROUP (2026-08-29) for the anchor floor,
    # which now emits it. ``config.read_workset_skip_kuid_check`` is still the
    # PRE-SNAPSHOT reader and still returns the same ``True``, so the accessor did not
    # stop being a carrier — it stopped being the ONLY one, and the label must name the
    # artefact the keyspace answers from. A conformance case asserts the pair equal.
    ("config.py (read-with-default)", frozenset({"box.enable_vault"})),
    # The one literal emitted by the canon bind producer (spec §2d).
    ("core_defaults.py (canon producer)", frozenset({"agent.default.canon"})),
    # ⚑ THE ``kuid.py (SENTINEL)`` LABEL IS GONE (2026-08-29) — not because the sentinel
    # moved, but because ``workset.kuid`` did: the anchor floor emits it now (as
    # ``kuid.SENTINEL``, by reference), and this partition must stay disjoint. One hop
    # from the floor line reaches the codec, which is where the unmintable-parity reason
    # for the value belongs. Do NOT re-add the label without removing the key from the
    # floor first — a key in two groups raises out of :func:`key_rows`.
    # The channel family — ``workset.channelroot`` + all six ``workset.channels.*``
    # leaves. ⚑ THEY ARE NOT "path join at use" AND MUST NOT GO BACK THERE: each is
    # resolved through its own declared key by ``channels/channels.py``, whose answer
    # the launch installs into the anchor floor. They sat under the join label while
    # three of them had no consumer at all, which is exactly the state that label made
    # look ordinary (R-35, "fix the CODE").
    ("channels/channels.py (channel key derivation)", frozenset({
      "workset.channelroot", "workset.channels.common", "workset.channels.chat",
      "workset.channels.broadcast", "workset.channels.share",
      "workset.channels.mailboxes", "workset.channels.share_global"})),
    # --- rows with no value-carrying artefact at all --- #
    # Realized as a Path JOIN at the point of use — ``project/workset.py``'s
    # ``resolve_workset_workspaces``, one face on ``resolve_workset_dir_key``. It joins a
    # bare LEAF (``workspaces``/``workspace``) onto the root, and the leaf is a fallback,
    # not a spelling of the default: the manifest states the formula and no literal
    # exists anywhere to point at.
    # ⚑ ``workset.template`` IS NOT ONE OF THESE AND MUST NOT COME BACK. It has a join
    # face too (``resolve_workset_template``, the same route), but unlike this one its
    # default IS written out — ``launch/templates.py`` emits the literal, so the layer-3
    # label below is what a reader can go and read.
    # ⚑ NEITHER IS ``workset.registry`` (LEFT 2026-08-29). Its join face
    # (``project/workset_registry.py::resolve_workset_registry_path``) still exists and is
    # still the pre-snapshot route, but the anchor floor now spells the DEFAULT out as the
    # spec's own ``@meta.workset.path/registry.yaml`` — so "no literal to point at", the
    # whole reason for this label, has stopped being true of it.
    ("built-in (path join at use)", frozenset({"workset.workspaces"})),
    ("runtime-probed (podman graphroot)", frozenset({"box.images_store"})),
    # ``default: <None>`` — an ABSENCE. No floor builder installs these at all.
    ("(nothing declares it — unset until you set it)", frozenset({
      "system.agent", "system.setup_completed", "box.shell", "agent.default.model",
      "agent.default.endpoint", "agent.default.run_args", "agent.default.transform"})),
    # ``default: {}`` — the resolver's own initial state for a category arm.
    ("(empty — the category starts with no entries)", frozenset({
      "box.bindings.ro", "box.bindings.rw", "box.masks",
      "agent.default.transform_settings"})),
    # The per-NODE arm of the same producer, spelled ONE @-hop differently from the
    # registry (``@meta.agent.<a>.path`` IS ``@config.agents/<a>``; both resolve to one
    # place). Labelled apart from the ``agent.default`` arm above because they are two
    # rows a reader will otherwise wonder about, not because two producers exist.
    ("core_defaults.py (canon producer, per node)", frozenset({"agent.<agent>.canon"})),
    # The layer-2 SOURCE key, split by ARM for the same reason the canon pair above
    # is: ONE producer (``template_seed_defaults``) emits both, but the per-node arm
    # is spelled one @-hop from the registry, while the default arm is a plain literal
    # with a value oracle. Two labels because a reader would otherwise wonder, and
    # because the arms sit in different conformance classes.
    # ⚑ The per-node arm's HARNESS-vs-NODE divergence was a boarded question and is
    # CLOSED (2026-08-27): it is node-rooted now, and only the @-hop is left.
    ("launch/templates.py (layer-2 seed, default arm)",
     frozenset({"agent.default.template"})),
    ("launch/templates.py (layer-2 seed)", frozenset({"agent.<agent>.template"})),
    # The layer-3 SOURCE key, from the SAME producer as the two arms above
    # (``template_seed_defaults``, under its workset-channels gate) and a plain literal
    # ``@meta.workset.path/template``. Its own label rather than the layer-2 one because
    # the LAYER is what the value is spelled against, and the workset layer is 3.
    ("launch/templates.py (layer-3 seed)", frozenset({"workset.template"})),
  )


def key_rows() -> list[DefaultRow]:
  """Section 1 — one row per registry key carrying a ``default:``.

  RAISES on an unclassified or doubly-classified row.  A default with no source is
  precisely the thing this command exists to make impossible to print, so it fails
  loudly here instead of rendering an empty SOURCE cell.
  """
  declared = manifest_default_rows()
  by_key: dict[str, str] = {}
  for label, keys in source_groups():
    for key in keys:
      if key in by_key:
        raise RuntimeError(
          f"{key} is classified twice — {by_key[key]!r} and {label!r}; the source "
          f"partition must be disjoint"
        )
      by_key[key] = label
  unclassified = set(declared) - set(by_key)
  if unclassified:
    raise RuntimeError(
      f"registry rows carrying a default with no source classification: "
      f"{sorted(unclassified)} — classify each in source_groups()"
    )
  stale = set(by_key) - set(declared)
  if stale:
    raise RuntimeError(
      f"source_groups() classifies rows the manifest no longer defaults: {sorted(stale)}"
    )

  rows: list[DefaultRow] = []
  for key, row in declared.items():
    value, per_mode = _render_default(row["default"])
    rows.append(DefaultRow(
      key=key, value=value, scope=_scope_of(key, row),
      source=by_key[key], per_mode=per_mode,
    ))
  return rows


def _scope_of(key: str, row: dict[str, Any]) -> str:
  """The row's declared ``scope:``, or its key HEAD for the layer-1 config tier.

  The six Layer-1 rows carry ``layer: 1`` instead of a ``scope:`` — they predate the
  scope column and their head IS their scope (``config.*``).  The fallback is pinned to
  exactly those six by the inventory test, so it cannot start absorbing a row that has
  simply lost its scope.
  """
  scope = row.get("scope")
  return str(scope) if scope else key.split(".", 1)[0]


def _render_default(default: Any) -> tuple[str, tuple[tuple[str, str], ...]]:
  """``(cell, continuation rows)`` for one manifest ``default:`` value.

  A per-mode MAP renders as continuation lines with equal arms COLLAPSED — 13 of the 14
  per-mode rows say the same thing for ``primary`` and ``named``, and repeating it makes
  the one row where they differ harder to spot, not easier.
  """
  if isinstance(default, dict) and set(default) == set(_MODES):
    grouped: dict[str, list[str]] = {}
    for mode in _MODES:
      grouped.setdefault(_scalar(default[mode]), []).append(mode)
    return "(varies by box mode)", tuple(
      (", ".join(modes), value) for value, modes in grouped.items()
    )
  return _scalar(default), ()


def _scalar(value: Any) -> str:
  """One default value as text, spelling ABSENCE and EMPTINESS as themselves."""
  if value is None:
    return "<None>"
  if value == {}:
    return "{}"
  if isinstance(value, bool):
    return "true" if value else "false"
  return str(value)


# --------------------------------------------------------------------------- #
# Section 2 — the dest-keyed BIND and COPY defaults
# --------------------------------------------------------------------------- #
#
# ⚑ These are DEST-KEYED ENTRIES, not dotted keys: the thing in brackets is a box
# DESTINATION, which is DATA and contains dots of its own.  Rendered
# ``box.bindings.ro[~/vault/ro]`` exactly as the spec writes them.  THE DEST is never
# split on a dot anywhere in this module; the SCOPE cell is split off the ARM
# (``box.bindings.ro`` -> ``box``), which is a dotted key prefix and not data.

#: Bind dests whose declaring artefact is NOT a ``core-defaults.yaml`` family table.
#: NAMED with a reason each, in the ``BIND_EXEMPTIONS`` spirit: a silent fallback label
#: here would quietly mis-attribute a bind, and the inventory test asserts every entry
#: still names a live manifest row.
_BIND_SOURCES_OUTSIDE_THE_FILE: dict[str, str] = {
  "~/canon/COLLECTION.md": "core_defaults.py (packaged rom tree)",
  "~/canon/bible/ROM_CONTENTS.md": "core_defaults.py (packaged rom tree)",
  "~/canon/bible/general": "core_defaults.py (packaged rom tree)",
  "~/canon/bible/workset": "core_defaults.py (packaged rom tree)",
  "~/canon/bible/box": "core_defaults.py (packaged rom tree)",
  # The plugin's own bible chapter — emitted by CORE from the resolved target.
  "~/canon/bible/agent": "core_defaults.py (plugin rom chapter)",
  # A PLACEHOLDER dest, not a dest: the manifest writes the whole row as the
  # `%if @box.share_images …%` conditional, whose code row is the unconditional
  # `images` entry gated at its injection site.
  "<box_image_dir>": "core-defaults.yaml (images:, gated)",
}

#: Every ``category_default_entries`` arm is built by the one seed-layer function.
_SEED_LAYER_SOURCE = "launch/templates.py (seed layers)"


def _bind_family_by_dest() -> dict[str, str]:
  """``{box_dest: 'core-defaults.yaml (<table>:)'}`` — the label for a declared dest.

  The dest→table read itself belongs to the module that owns the file
  (:func:`~kanibako.settings.core_defaults.bind_dest_families`); all this adds is the
  human-plain spelling of the answer.
  """
  return {
    dest: f"core-defaults.yaml ({table}:)"
    for dest, table in core_defaults.bind_dest_families().items()
  }


def bind_rows() -> list[DefaultRow]:
  """Section 2 — the dest-keyed bind arms and the seed-layer copy arms.

  Internal (``user_key: false``) entries are INCLUDED and marked: they are part of what
  a box gets, and leaving them out would make the listing disagree with the box.  They
  carry a ``value:`` rather than a ``default:`` precisely because there is no
  user-addressable key to override.
  """
  doc = manifest_doc()
  family = _bind_family_by_dest()
  rows: list[DefaultRow] = []

  for arm, entries in doc["bind_default_entries"].items():
    for dest, entry in entries.items():
      internal = entry.get("user_key") is False
      raw = entry["value"] if internal else entry["default"]
      value, per_mode = _render_default(raw)
      source = _BIND_SOURCES_OUTSIDE_THE_FILE.get(str(dest)) or family.get(str(dest))
      if source is None:
        raise RuntimeError(
          f"{arm}[{dest}] has no declaring artefact — add it to "
          f"_BIND_SOURCES_OUTSIDE_THE_FILE with a reason, or fix the dest"
        )
      rows.append(DefaultRow(
        key=f"{arm}[{dest}]", value=value, scope=str(arm).split(".", 1)[0],
        source=source, per_mode=per_mode, internal=internal,
      ))

  # ``parametric_keys`` is a SHAPE declaration sitting beside the arms, not an arm.
  for arm, entries in doc["category_default_entries"].items():
    if arm == "parametric_keys":
      continue
    for dest, entry in entries.items():
      internal = entry.get("user_key") is False
      raw = entry["value"] if internal else entry["default"]
      value, per_mode = _render_default(raw)
      rows.append(DefaultRow(
        key=f"{arm}[{dest}]", value=value, scope=str(arm).split(".", 1)[0],
        source=_SEED_LAYER_SOURCE, per_mode=per_mode, internal=internal,
      ))
  return rows


# --------------------------------------------------------------------------- #
# Section 3 — the env-instance defaults a box actually gets
# --------------------------------------------------------------------------- #
#
# ⚑ THIS SECTION IS A REPORT ON THE LIVE ENV FLOOR, NOT A REGISTRY CLAIM.  The values
# are read from the same two emitters the launch reads, so it lists what a box actually
# gets — which is why it lists EVERY var, including the one the registry also carries.
# ⚑⚑ THE REGISTRY ENUMERATES ONE env INSTANCE and no longer "deliberately lacks" them:
# spec §2b declares ``box.env.COLORTERM`` (the core-shipped default), so it is a
# section-1 DECLARED KEY as well, printed there with the SAME source label.  That
# overlap is the point — the two sections answer different questions about one value,
# and dropping it from here would make "the environment variables a box gets" omit the
# only one kanibako itself ships.  Every OTHER env instance (each plugin's declared
# vars) is a family member with no registry row, and this is where it is reported.


class PluginConsultation(NamedTuple):
  """What section 3 asked of the installed plugins — printed, so a thin install is legible."""

  consulted: tuple[str, ...]
  declaring: tuple[str, ...]


def env_rows() -> tuple[list[DefaultRow], PluginConsultation]:
  """Section 3 — core's static env floor plus every INSTALLED plugin's declared vars.

  A cli-only install consults kanibako's own ``no_agent`` target and declares nothing; a
  plugin that fails to load is not allowed
  to take the listing down with it, since ``system defaults`` must work on a broken
  install (that is when a user most needs to read it).
  """
  rows = [
    DefaultRow(
      key=key, value=value, scope=key.split(".", 1)[0],
      source="core-defaults.yaml (env:)",
    )
    for key, value in sorted(core_defaults.env_default_categories().items())
  ]

  consulted: list[str] = []
  declaring: list[str] = []
  for name, cls in sorted(_installed_targets().items()):
    consulted.append(name)
    try:
      envs = cls().default_envs()
    except Exception:
      # A plugin that cannot even be instantiated is reported by ``system diagnose``;
      # here it simply declares nothing, and the consulted list still names it.
      continue
    if not envs:
      continue
    declaring.append(name)
    for key, value in sorted(envs.items()):
      # Scope from the key HEAD like every other row here — ``load_envs`` builds these
      # already DISCRIMINATED (``agent.<agent>.env.<VAR>``), so the head is the answer
      # and a hardcoded "agent" would merely be the same answer that cannot be checked.
      rows.append(DefaultRow(
        key=key, value=str(value), scope=key.split(".", 1)[0],
        source=f"{name} plugin defaults (env:)",
      ))
  return rows, PluginConsultation(tuple(consulted), tuple(declaring))


def _installed_targets() -> dict[str, Any]:
  """The discovered agent plugins; an empty mapping when discovery itself fails."""
  try:
    from kanibako.targets import discover_targets

    return dict(discover_targets())
  except Exception:
    return {}


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

#: Ceiling on the DEFAULT column.  Widths are otherwise measured from the rows present,
#: which is right until ONE long value (the helper socket's two-part entry, the image
#: bind's whole ``%if …%`` conditional) stretches the SOURCE column of all 33 bind rows
#: by 25 characters of pure padding.  A value over the cap OVERFLOWS — its own SOURCE
#: shifts right while every other row stays aligned, which is the cheap way round: two
#: ragged rows beat thirty-three padded ones.
_VALUE_COLUMN_CAP = 40


def _print_table(rows: list[DefaultRow], out: Any) -> None:
  """One aligned row per default, widths measured from the rows actually present."""
  if not rows:
    print("  (none)", file=out)
    return
  key_w = max(len(r.key) for r in rows)
  val_w = min(max(len(r.value) for r in rows), _VALUE_COLUMN_CAP)
  scope_w = max(len(r.scope) for r in rows)
  print(
    f"  {'KEY'.ljust(key_w)}  {'DEFAULT'.ljust(val_w)}  "
    f"{'SCOPE'.ljust(scope_w)}  SOURCE",
    file=out,
  )
  for row in rows:
    mark = "  [internal]" if row.internal else ""
    print(
      f"  {row.key.ljust(key_w)}  {row.value.ljust(val_w)}  "
      f"{row.scope.ljust(scope_w)}  {row.source}{mark}",
      file=out,
    )
    for modes, value in row.per_mode:
      print(f"  {' ' * key_w}    {modes}: {value}", file=out)


def _sorted(rows: list[DefaultRow]) -> list[DefaultRow]:
  """Group by SCOPE in cascade order, then by key.

  Scope first because it is the column a reader navigates BY — it is also the column
  that says which verb writes the key (``system set`` / ``box set``) — while SOURCE is
  the answer being looked up, not the index.  The scope stays on every line rather than
  becoming a heading, so one line still carries the whole answer for a ``grep``.
  """
  order = {name: i for i, name in enumerate(
    ("config", "system", "agent", "workset", "box"))}
  return sorted(rows, key=lambda r: (order.get(r.scope, len(order)), r.key))


def print_defaults(out: Any) -> None:
  """Render the whole listing — the ``kanibako system defaults`` body."""
  keys = _sorted(key_rows())
  binds = _sorted(bind_rows())
  envs, plugins = env_rows()

  print("Defaults kanibako ships. Nothing here is stored in your settings —", file=out)
  print("these are the values in force when you have set nothing.", file=out)

  print(f"\nDECLARED KEYS ({len(keys)})\n", file=out)
  _print_table(keys, out)

  print(f"\nBIND AND COPY ENTRIES ({len(binds)})", file=out)
  print("The bracketed part is a box DESTINATION, not a key segment.\n", file=out)
  _print_table(binds, out)

  print(f"\nENVIRONMENT VARIABLES ({len(envs)})\n", file=out)
  _print_table(_sorted(envs), out)
  if plugins.consulted:
    declaring = ", ".join(plugins.declaring) if plugins.declaring else "none"
    print(
      f"\n  Consulted {len(plugins.consulted)} installed agent target(s): "
      f"{', '.join(plugins.consulted)}. Declaring variables: {declaring}.",
      file=out,
    )
  else:
    # ⚑ DISCOVERY ITSELF FAILED — not a thin install: ``no_agent`` is kanibako-cli's
    # own entry point, so a working install always consults at least one target.
    print("\n  No agent targets could be discovered, so no plugin variables are listed.", file=out)

  print(
    "\nNot listed: the KANIBAKO_* variables kanibako derives per launch from the box "
    "it is\nstarting. They have no static value — see 'kanibako box show --effective'.",
    file=out,
  )
