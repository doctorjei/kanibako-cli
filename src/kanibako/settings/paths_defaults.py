"""User-visible message text, status tokens and shell file CONTENTS.

⚑ Every ``MSG_``/``WARN_``/``ERR_`` literal here is USER-VISIBLE output.
🛑 PATH LITERALS DO NOT LIVE HERE — they moved to :mod:`kanibako.settings.bootstrap`
(`[R157]`). Do not reintroduce one; add it there and import it.

⚑ ``RUN_USER_UID_PATH`` is the ONE path constant still here, deliberately: it is spliced into
``WARN_RUNDIR_UNUSABLE`` below, and moving it would force this file to grow an import — which
:func:`test_paths_defaults_is_import_free` forbids, because an import here can close the
tree's documented cycle. It moves in its own step, once that splice is resolved.
"""

# ⚑ Spliced into WARN_RUNDIR_UNUSABLE below — see the module docstring.
RUN_USER_UID_PATH = "/run/user/%d"


STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_NO_DATA = "no-data"

# ⚑ KB_INIT ends UNTERMINATED (trailing space, no newline); MSG_DONE closes the line.
MSG_OTS_KB_INIT =       "[One Time Setup] Initializing kanibako in %s... " # project path
MSG_OTS_WS_PROJ_INIT =  "[One Time Setup] Initializing workset project in %s... " # metadata path
MSG_DONE =              "done."

# ⚑ The WARN_* templates are passed to logger.warning() UNFORMATTED — an arg-count slip does not
# raise here.  Args are POSITIONAL; each line below states its own contract.
WARN_RELATIVE_XDG =     "%s=%r is relative (not absolute); ignoring per XDG spec & using default."
WARN_FALLBACK_RT_DIR = ("%s not set; falling back to %s for runtime files " +     # var, dir, var
                        "(helper sockets). Set %s to a per-user runtime dir to silence this.")
# ⚑ RUN_USER_UID_PATH splices a %d IN: the conversions are (%s var, %d uid, %s dir, %s var).
WARN_RUNDIR_UNUSABLE = ("%s not set & " + RUN_USER_UID_PATH + " unusable; falling back to temp " +
                        "dir %s for runtime files. Set %s to persistent per-user runtime dir to " +
                        "silence this.")

WARN_WS_NO_ROOT =       "Warning: workset '%s' root missing: %s" # workset name, root
WARN_WS_BAD_LOAD =      "Warning: failed to load workset '%s': %s" # workset name, exception
WARN_WS_BOX_BAD_NAME = ("box name '%s' does not meet the naming rules (%s); it still resolves, " +
                        "but rename it when convenient.")           # box name, box_name_reason()

# ⚑ KUID FIRST, box name second — the reverse of every other advisory here.
WARN_BOX_BAD_KUID =    ("Warning: invalid KUID '%s' for standalone box '%s' (invalid kuid); it " +
                        "still resolves; fix workset.kuid or set workset.skip_kuid_check=true to " +
                        "silence this.")
WARN_BOX_NO_VAULT =    ("Warning: cannot find vault for box '%s' (expected at %s); it still " +
                        "launches without a vault; recreate the directory or set " +
                        "box.enable_vault=false to silence.")        # box name, the RW vault path

# ⚑ The 1st arg of each pair below is a LAYER DISCRIMINATOR, not a value; BAD_REF's is spliced
# INSIDE the @-sigil, so "" is load-bearing punctuation ("Unknown @-reference:").
ERR_SETTINGS_BAD_PATH = "Unresolvable %s path: %s" # "config" | "system", key
ERR_SETTINGS_BAD_REF =  "Unknown @%s-reference: %s" # "" | "config", ref
ERR_CONFIG_NO_FILE =    "%s is missing. Run any kanibako command to initialize." # config file path
# ⚑ The LOUD half of R153 (Jei, 2026-08-31). A stale settings table in the Layer-1 file used
# to be DROPPED in silence, so a box ran a different image than its owner's file said.
# ⚑ THE CURE IS ORDERED, AND THE ORDER IS LOAD-BEARING: the hand-edit comes FIRST. Every
# verb resolves its paths through this read, ``system set`` included, so a message that
# led with the command would send the user to a command that refuses for this same reason.
ERR_CONFIG_LAYER1_SETTINGS = (
                        "%s carries settings, which it cannot hold:\n  %s\n" +
                        "That file holds the config.* bootstrap paths and nothing else. " +
                        "Delete those lines from it, then set what you meant with " +
                        "'kanibako system set <key>=<value>', which writes the settings file.")
                                                    # the Layer-1 file path, the offending keys
ERR_PROJECT_NO_PATH =   "Project path '%s' does not exist." # the path that does not exist
# ⚑ The two $HOME-guard messages take NO arguments (raised bare).
ERR_PROJECT_NEW_HOME = ("Refusing to create project rooted at $HOME: this would mount the " +
                        "entire home directory as the workspace.\n If you really want a " +
                        "project here, use:\nkanibako create --standalone ~ --allow-home")
ERR_PROJECT_REG_HOME = ("Refusing to register $HOME as a project path: this would mount the " +
                        "entire home directory as the workspace.")
ERR_PROJECT_NAME_USED = "Name '%s' is already registered"
ERR_PROJECT_DIR_IS_WS = ("Name '%s' is already in use by a workset. Box and workset names are " +
                         "separate namespaces, but this bare name would then resolve to the " +
                         "box, shadowing the workset in bare-name lookups. Re-run with --force " +
                         "to create the box under this name anyway.") # name

ERR_WORKSET_NO_PROJECT = "Project '%s' not found in workset '%s'" # project name, workset name
ERR_WORKSET_NO_WORKSET = "No workset found for path: %s" # project dir
ERR_WORKSET_WS_NOT_BOX = ("%s is a workset, not a single project box. Name a project inside it " +
                          "(e.g. '%s/<project>') or run the command from a project workspace " +
                          "under that workset.") # ⚑ the name TWICE — two args, one value
ERR_WORKSET_NOT_IN_BOX = ("Inside workset '%s' but not in a specific project workspace. Change " +
                          "to a project directory under %s/.") # workset name, workspaces dir

# The `~/.shell.d/*.sh` user/template extension point for a box's INTERACTIVE shell.
# ⚑ INTERACTIVE ONLY — it never reaches the agent; use `env.<VAR>` / `secret_path` for that.
# ⚑ SHELL_D_FILE + "/" is _upgrade_shell's already-seamed test: renaming the DIR re-appends.
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
