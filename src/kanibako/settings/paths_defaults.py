# Variables instead of hardcoded values.
XDG_DATA_HOME = "XDG_DATA_HOME"
XDG_CONFIG_HOME = "XDG_CONFIG_HOME"
XDG_RUNTIME_DIR = "XDG_RUNTIME_DIR"
XDG_STATE_HOME = "XDG_STATE_HOME"
XDG_CACHE_HOME = "XDG_CACHE_HOME"


# Spec defaults for the XDG base dirs that HAVE one.  ⚑ ``XDG_RUNTIME_DIR`` is
# deliberately ABSENT — no spec default; :func:`resolve_xdg` handles it (fallback + warn).
XDG_SPEC_DEFAULTS: dict[str, str] = {
    XDG_DATA_HOME:             '.local/share',
    XDG_CONFIG_HOME:           '.config',
    XDG_STATE_HOME:            '.local/state',
    XDG_CACHE_HOME:            '.cache'}


# ---------------------------------------------------------------------------
# Layer 1 — the CONFIG-key FOUNDATION (spec §1; config keys finalized at 5)
# ---------------------------------------------------------------------------
# Bootstrap keys from ``kanibako_config.yaml``, resolved FLAT — not by the keyspace pipeline.
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
# SETTINGS keys, not bootstrap config: each ``@``-refs a Layer-1 config key or an XDG base.
SYSTEM_PATH_DEFAULTS: dict[str, str] = {
   "system.backup":                 "@config.data/backup",
   "system.channelroot":            "@config.data/channels",
   "system.template":               "@config.data/global/template",
   "system.canon":                  "@config.data/global/canon",
   "system.cache":                  "$XDG_CACHE_HOME/kanibako",
   "system.runtime":                "$XDG_RUNTIME_DIR/kanibako",
   # Channels skeleton (the type-roots derive from system.channelroot).
   "system.channels.common":        "@system.channelroot/common",
   "system.channels.chat":          "@system.channelroot/chat",
   "system.channels.broadcast":     "@system.channels.chat/broadcast.md",
   "system.channels.mailboxes":     "@system.channelroot/mailboxes",
   "system.channels.share":         "@system.channelroot/share"}


SETTINGS_FILE = "settings.yaml"
PROFILE_FILE = ".profile"
BASHRC_FILE = ".bashrc"
SHELL_D_FILE = ".shell.d"
IGNORE_FILE = ".gitignore"


BOXES_PATH = "boxes"
HOME_PATH = "home"
KANIBAKO_PATH = "kanibako"
LOGS_PATH = "logs"
RO_PATH = "ro"
RW_PATH = "rw"
VAULT_PATH = "vault"
RUN_USER_UID_PATH = "/run/user/%d"

# The STANDALONE box-store dir name — ``@meta.box.path`` and half the §5 marker.
STANDALONE_META_DIR = 'box_data'


STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_NO_DATA = "no-data"

MSG_OTS_KB_INIT =       "[One Time Setup] Initializing kanibako in %s... " # Was {project_path}
MSG_OTS_WS_PROJ_INIT =  "[One Time Setup] Initializing workset project in %s..." # was {metadata_path}
MSG_DONE =              "done."

WARN_RELATIVE_XDG =     "%s=%r is relative (not absolute); ignoring per XDG spec & using default."
WARN_FALLBACK_RT_DIR = ("%s not set; falling back to %s for runtime files " +
                        "(helper sockets). Set %s to a per-user runtime dir to silence this.")
WARN_RUNDIR_UNUSABLE = ("%s not set & " + RUN_USER_UID_PATH + " unusable; falling back to temp " +
                        "dir %s for runtime files. Set %s to persistent per-user runtime dir to " +
                        "silence this.")

WARN_WS_NO_ROOT =       "Warning: workset '%s' root missing: %s" # Was {ws_name}, {root}
WARN_WS_BAD_LOAD =      "Warning: failed to load workset '%s': %s" # Was {ws_name}, {exc}
WARN_WS_BOX_BAD_NAME = ("box name '%s' does not meet the naming rules (%s); it still resolves, " +
                        "but rename it when convenient.")

WARN_BOX_BAD_KUID =    ("Warning: invalid KUID '%s' for standalone box '%s' (invalid kuid); it " +
                        "still resolves; fix workset.kuid or set workset.skip_kuid_check=true to " +
                        "silence this.")
WARN_BOX_NO_VAULT =    ("Warning: cannot find vault for box '%s' (expected at %s); it still " +
                        "launches without a vault; recreate the directory or set " +
                        "box.enable_vault=false to silence.")

ERR_SETTINGS_BAD_PATH = "Unresolvable %s path: %s" # Was "config" | "system", {key}
ERR_SETTINGS_BAD_REF =  "Unknown @%s-reference: %s" # Was "" | "config", {ref}
ERR_CONFIG_NO_FILE =    "%s is missing. Run any kanibako command to initialize." # Was {config_file}
ERR_PROJECT_NO_PATH =   "Project path '%s' does not exist." # Was {project_path}
ERR_PROJECT_NEW_HOME = ("Refusing to create project rooted at $HOME: this would mount the " +
                        "entire home directory as the workspace.\n If you really want a " +
                        "project here, use:\nkanibako create --standalone ~ --allow-home")
ERR_PROJECT_REG_HOME = ("Refusing to register $HOME as a project path: this would mount the " +
                        "entire home directory as the workspace.")
ERR_PROJECT_NAME_USED = "Name '%s' is already registered"
ERR_PROJECT_DIR_IS_WS = ("Name '%s' is already in use by a workset. Box and workset names are " +
                         "separate namespaces, but this bare name would then resolve to the " +
                         "box, shadowing the workset in bare-name lookups. Re-run with --force " +
                         "to create the box under this name anyway.") # was {name}

ERR_WORKSET_NO_PROJECT = "Project '%s' not found in workset '%s'" # was {project_name}, {ws.name}
ERR_WORKSET_NO_WORKSET = "No workset found for path: %s" # Was {project_dir}
ERR_WORKSET_WS_NOT_BOX = ("%s is a workset, not a single project box. Name a project inside it " +
                          "(e.g. '%s/<project>') or run the command from a project workspace " +
                          "under that workset.") # Was raw_name, raw_name
ERR_WORKSET_NOT_IN_BOX = ("Inside workset '%s' but not in a specific project workspace. Change " +
                          "to a project directory under %s/.") # Was {ws.name}, {ws.workspaces_dir}

# The `~/.shell.d/*.sh` user/template extension point for a box's INTERACTIVE shell.
# ⚑ INTERACTIVE ONLY — it never reaches the agent; use `env.<VAR>` / `secret_path` for that.
_SHELL_D_SOURCE_LINE =   'for _f in ~/.shell.d/*.sh; do [ -r "$_f" ] && . "$_f"; done\nunset _f'

BASHRC_CONTENTS =        ("# kanibako shell environment\n" +
                          "[ -f /etc/bashrc ] && . /etc/bashrc\n" +
                          'export PS1="${KANIBAKO_PS1:-(kanibako) \\u@\\h:\\w\\$ }"\n' +
                          "# Source user init scripts\n%s\n" % _SHELL_D_SOURCE_LINE)

SHELL_D_CONTENTS =        "# Source user init scripts\n%s\n" % _SHELL_D_SOURCE_LINE

PROFILE_CONTENTS =       ("# kanibako login profile\n" +
                          "[ -f ~/.bashrc ] && . ~/.bashrc\n")

UNREGISTERED_MARKER = "__unregistered__"
KIND_PROJECT = "project"
KIND_WORKSET = "workset"
