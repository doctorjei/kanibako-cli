"""Bootstrap path literals — THE single home for hardcoded path values.

⚑⚑ EVERY path literal in the tree belongs here and NOWHERE ELSE (`[R157]`, his rule
2026-09-02): all paths except the config file and the ``/etc`` files are key-derived, and
whatever literal survives is stored ONCE, in one constant, in the appropriate area.

🛑 STRUCTURAL EXCEPTIONS THAT CANNOT IMPORT THIS FILE, and are guarded by a drift test that
reads the shipped bytes instead: the PID-1 pair (``box_supervisor``/``box_lifecycle``, pinned
flat and stdlib-only), the bundled ``Containerfile.template-*``, and seeded in-box scripts.

⚑ THIS FILE MUST STAY IMPORT-FREE. ``settings/paths.py`` imports it and ``project/workset.py``
imports that, so an import added here can close the tree's documented cycle — the same
invariant ``messages`` carries, and for the same reason.

⚑ ``RUN_USER_UID_PATH`` lives here even though ``WARN_RUNDIR_UNUSABLE`` splices it: the
message file imports it back, which is safe precisely because this module is a terminal leaf.
"""

XDG_DATA_HOME = "XDG_DATA_HOME"
XDG_CONFIG_HOME = "XDG_CONFIG_HOME"
XDG_RUNTIME_DIR = "XDG_RUNTIME_DIR"
XDG_STATE_HOME = "XDG_STATE_HOME"
XDG_CACHE_HOME = "XDG_CACHE_HOME"


# Spec defaults for the XDG base dirs that HAVE one (home-relative suffixes).
# ⚑ ``XDG_RUNTIME_DIR`` is deliberately ABSENT — no spec default; :func:`resolve_xdg` handles it.
XDG_SPEC_DEFAULTS: dict[str, str] = {
    XDG_DATA_HOME:             '.local/share',
    XDG_CONFIG_HOME:           '.config',
    XDG_STATE_HOME:            '.local/state',
    XDG_CACHE_HOME:            '.cache'}


# ---------------------------------------------------------------------------
# The BOOTSTRAP FILES — the paths that cannot be key-derived
# ---------------------------------------------------------------------------
# The user bootstrap config file, resolved under ``$XDG_CONFIG_HOME`` (spec §1).
CONFIG_FILE = "kanibako.cfg"

# The machine-wide site directory and its two base files.
# ⚑ Compose both from the DIR — it is spelled once.
SITE_CONFIG_DIR = "/etc/kanibako"
SITE_CONFIG_FILE = "base.cfg"
SITE_SETTINGS_FILE = "settings_base.yaml"


# ---------------------------------------------------------------------------
# Layer 1 — the CONFIG-key FOUNDATION (spec §1)
# ---------------------------------------------------------------------------
# Bootstrap keys from ``kanibako.cfg``, resolved FLAT — not by the keyspace pipeline.
# ⚑ The set may GROW; spec §1 states no count. Its SIZE is pinned by test_manifest_conformance.
CONFIG_PATH_DEFAULTS: dict[str, str] = {
    "config.data":                  "$XDG_DATA_HOME/kanibako",
    "config.settings":              "@config.data/global/settings.yaml",
    "config.agents":                "@config.data/agents",
    "config.primary_workset":       "@config.data/primary_workset",
    "config.registry":              "@config.data/global/registry.yaml",
    # The LIFECYCLE JOURNAL — the TRANSIENT truth beside the steady-state registry.
    "config.journal":               "@config.data/global/journal.yaml"}


# ---------------------------------------------------------------------------
# Layer 2 — system-scope SETTINGS keys that are PATHS (spec §1/§2g)
# ---------------------------------------------------------------------------
# SETTINGS keys, not bootstrap config: each ``@``-refs a Layer-1 config key, an XDG base,
# or another key in THIS table.  ⚑ THIS TABLE IS THE FLOOR, NOT THE STORE: every key here is
# CLI-settable at the system scope (``config_keys._KEY_ROUTES``) and a set lands in the ``system:``
# table of the SYSTEM SETTINGS file, which the cascade layers OVER these defaults.
SYSTEM_PATH_DEFAULTS: dict[str, str] = {
   "system.backup":                 "@config.data/backup",
   "system.channelroot":            "@config.data/channels",
   "system.template":               "@config.data/global/template",
   "system.canon":                  "@config.data/global/canon",
   "system.cache":                  "$XDG_CACHE_HOME/kanibako",
   "system.runtime":                "$XDG_RUNTIME_DIR/kanibako",
   # Channels skeleton.  ⚑ ORDER-DEPENDENT: broadcast refs chat, the rest ref channelroot.
   "system.channels.common":        "@system.channelroot/common",
   "system.channels.chat":          "@system.channelroot/chat",
   "system.channels.broadcast":     "@system.channels.chat/broadcast.md",
   "system.channels.mailboxes":     "@system.channelroot/mailboxes",
   "system.channels.share":         "@system.channelroot/share"}


PROFILE_FILE = ".profile"
BASHRC_FILE = ".bashrc"
SHELL_D_FILE = ".shell.d"
IGNORE_FILE = ".gitignore"


BOXES_PATH = "boxes"
CHANNELS_PATH = "channels"
HOME_PATH = "home"
KANIBAKO_PATH = "kanibako"
LOGS_PATH = "logs"
RO_PATH = "ro"
RW_PATH = "rw"
VAULT_PATH = "vault"
# ⚑⚑ SINGULAR vs PLURAL, and they are DIFFERENT DIRS: ``workspaces`` is the primary/named
# workset's container of per-box workspaces, ``workspace`` is the STANDALONE box's single
# one.  Reading one for the other silently repoints every box of that mode — spell the
# constant, never the string.
WORKSPACES_PATH = "workspaces"
WORKSPACE_PATH = "workspace"
RUN_USER_UID_PATH = "/run/user/%d"

# The STANDALONE box-store dir name — ``@meta.box.path`` and half the detection
# marker (``system-design-1.8.0.md`` § "Detection & import").
# ⚑ THE only carrier: ``project/import_reconcile`` used to hand-keep a second
# spelling and now imports this one.  Import it; never re-spell the string.
STANDALONE_META_DIR = 'box_data'


# The two PROJECT KINDS. Fundamental to kanibako's core, and path-shaped in practice:
# a kind decides which tree a box resolves into.
# The box-store leaf for a box with no registry entry — used as a PATH component
# (``std.boxes / UNREGISTERED_MARKER``), which is what puts it here.
UNREGISTERED_MARKER = "__unregistered__"

KIND_PROJECT = "project"
KIND_WORKSET = "workset"
