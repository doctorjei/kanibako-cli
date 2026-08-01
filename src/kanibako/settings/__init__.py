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
