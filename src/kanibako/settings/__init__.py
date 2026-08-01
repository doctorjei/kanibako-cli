"""kanibako settings — the keyspace, the config engine, and the layout tier.

The single route by which anything is bound, copied, shared or synced into a
box, and the single surface by which a user configures it.  Spec:
``specs/settings-keyspace-1.6.0-target.md``.

The resolver chain, in build order:

* ``settings_keyspace``   — the CLOSED KEYSPACE: what IS a key, and the one
  validator that answers it (spec §0).
* ``settings_store``      — the KeyStore: the resolved-keyspace data structure.
* ``settings_assemble``   — per-scope settings files → ordered partials.
* ``settings_merge``      — the depth-sensitive per-name union of those partials.
* ``settings_expand``     — eager build-time expansion of tokens to terminals.
* ``settings_resolve``    — the expression engine (``@``-refs, ``$vars``, ``~``)
  and the ``GUEST_HOME``/``GUEST_UID``/``GUEST_GID`` image contract.
* ``settings_views``      — the typed 3-tier read surface over the snapshot.
* ``settings_categories`` — ``reconcile_categories``: THE single binding route.
* ``settings_launch``     — the ONE resolve per launch.
* ``settings_prefs``      — ``pref.*`` requests to set an earlier-resolving key
  (spec §2h).
* ``settings_cli_level``  — the §1A CLI level: one builder, one guard.
* ``settings_configset``  — ``config set`` validation + the raw write-back.

The config engine and the layout tier:

* ``config``          — the Layer-1 bootstrap config (spec §1): the flat
  resolver for the paths needed BEFORE the keyspace pipeline can run.
* ``config_io``       — centralized YAML load/dump for every kanibako config
  document (both layers), plus the document mutators the config verbs write
  through.
* ``config_keys``     — the CLI-facing key TAXONOMY: which family a key
  spelling belongs to, and the scope/routing tables.  ⚑ NOT the closed-keyspace
  validator — that is ``settings_keyspace``, which this module calls into.
* ``config_interface`` — the unified get/set/reset/show engine behind the
  ``config`` verbs at every scope.
* ``core_defaults``   — the declarative core category defaults.
* ``agent_defaults``  — the per-agent twin: each plugin's shipped defaults file.
* ``agent_config``    — the ``meta.agent.<agent>.settings`` file route.
* ``agent_select``    — which agent a box runs (spec §1A/§2g/§2h).
* ``agent_representation`` — agent descriptor → KeyStore representation.
* ``paths``           — XDG resolution, project layout, mode detection, and the
  ``system.path.*`` tier.

⚑ Why no separate ``config/`` package, despite spec §1's two-layer model: that
model partitions by RESOLUTION ORDER, and the modules do not partition along it.
``config_io`` serves both layers; ``paths`` straddles (Layer-1 XDG/layout AND
the Layer-2 ``system.path.*`` tier) and imports ``settings_resolve`` at module
scope; and ``config_interface`` — the module whose NAME says config — is a
Layer-2 CLI engine.  A ``config/`` directory holding ``paths`` but not
``config_interface`` would be exactly the two-forms-for-one-thing trap.
Re-decide after the KeyKind phase decomposes ``config_interface``.

⚑ ``settings_launch`` is the launch-time settings SNAPSHOT — a settings
artifact the launcher consumes.  It is NOT part of ``kanibako.launch``, which is
the box lifecycle.

PUBLIC SURFACE: the submodules named in ``__all__``.  Consumers outside this
package import the SUBMODULE — ``from kanibako.settings.settings_store import
KeyStore`` — never a name re-exported here.

⚑ DELIBERATELY IMPORT-FREE, and here it matters most.  This package holds most
of the tree's one large import cycle, every arc of which is broken today by a
function-local import (``config`` → ``config_interface``, ``config_interface`` →
``settings_configset``, ``settings_prefs`` → ``settings_assemble``, ``paths`` →
``workset``/``box_resolve``, ``core_defaults`` → ``launch.templates``).  An
eager re-export facade would load all of it on any submodule import, so those
deferrals would become no-ops — and the NEXT person to promote one to module
scope would get no error.  That silently disarms a working tripwire on the
highest-risk surface in the tree.  It would also pull the 3800-line
``config_interface`` into ``kanibako --help``.  See
``plans/refactor-packageification-PLAN.md`` §4.3; promoting this to a real
facade is gated on breaking that cycle (the ``paths`` split, LaunchPlan/KeyKind).

IN-PACKAGE IMPORTS ARE ABSOLUTE (``from kanibako.settings.settings_merge import
merge``), never relative — §4.4 of that plan.
"""

__all__ = [
    "agent_config",
    "agent_defaults",
    "agent_representation",
    "agent_select",
    "config",
    "config_interface",
    "config_io",
    "config_keys",
    "core_defaults",
    "paths",
    "settings_assemble",
    "settings_categories",
    "settings_cli_level",
    "settings_configset",
    "settings_expand",
    "settings_keyspace",
    "settings_launch",
    "settings_merge",
    "settings_prefs",
    "settings_resolve",
    "settings_store",
    "settings_views",
]
