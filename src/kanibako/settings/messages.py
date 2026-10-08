"""User-visible message text, status tokens and shell file CONTENTS.

⚑ Every ``MSG_``/``WARN_``/``ERR_`` literal here is USER-VISIBLE output.
🛑 PATH LITERALS DO NOT LIVE HERE — they moved to :mod:`kanibako.settings.bootstrap`
(`[R157]`). Do not reintroduce one; add it there and import it.

⚑ NO path literal is defined here. ``RUN_USER_UID_PATH`` is IMPORTED from ``bootstrap`` for
``WARN_RUNDIR_UNUSABLE``'s splice — the single permitted import, and safe because ``bootstrap``
imports nothing and so cannot complete a cycle back to this file.
"""

# ⚑⚑ THE ONE PERMITTED IMPORT, AND IT KEEPS THE GRAPH A DAG: ``bootstrap`` imports nothing
# (pinned by ``test_bootstrap_is_import_free``), so it is a TERMINAL leaf and no path through
# it returns here. ``RUN_USER_UID_PATH`` is spliced into ``WARN_RUNDIR_UNUSABLE`` below.
from kanibako.settings.bootstrap import RUN_USER_UID_PATH


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
WARN_SA_SHADOWED_BY_PATH = ("'%s' resolved to the path %s; the registered standalone box of the same " +
                            "name at %s is shadowed — reach it by its path.")  # name, path, root
WARN_WS_BOX_BAD_NAME = ("box name '%s' does not meet the naming rules (%s); it resolves by its " +
                        "path only, not by that name. Rename it.")           # box name, box_name_reason()

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
# ⚑ The LOUD half of R153: a stale settings table in the Layer-1 file refuses, never drops.
# ⚑ THE CURE IS ORDERED, AND THE ORDER IS LOAD-BEARING: the hand-edit comes FIRST. Every
# verb resolves its paths through this read, ``system set`` included, so a message that
# led with the command would send the user to a command that refuses for this same reason.
ERR_CONFIG_LAYER1_SETTINGS = (
                        "%s carries settings, which it cannot hold:\n  %s\n" +
                        "That file holds the config.* bootstrap paths and nothing else. " +
                        "Delete those lines from it, then set what you meant with " +
                        "'kanibako system set <key>=<value>', which writes the settings file.")
                                                    # the Layer-1 file path, the offending keys
# ⚑⚑ THE OTHER DIRECTION OF THE SAME RULE: an UNDECLARED leaf INSIDE ``config:`` refuses too
# (Convention 0). Spec §1: "The Layer-1 set is exactly the config keys in the table below."
ERR_CONFIG_LAYER1_UNDECLARED = (
                        "%s carries config keys that do not exist:\n  %s\n" +
                        "Layer 1 declares exactly these (spec §1): %s. Fix or delete " +
                        "those lines; config.* keys are only ever set by editing that file.")
                                    # the Layer-1 file path, the offending keys, the declared set
# ⚑ A NON-TABLE ``config:`` resolved to the EMPTY foundation in SILENCE, which is the whole
# store at its default location for a user whose one line meant to move it.
ERR_CONFIG_LAYER1_TABLE = (
                        "%s has a 'config:' entry that is not a table: %s\n" +
                        "It carries the config.* bootstrap paths as keys under it, e.g.\n" +
                        "  config:\n    data: /path/to/kanibako")
                                                    # the Layer-1 file path, the offending value
# ⚑⚑ SPLIT PER DOOR: the REASON is one sentence with one home and every door shares it,
# while the LEAD and the CUE differ — a hand-edited file HAS the lines the read-time lead
# names, and a ``set`` that refused wrote none.  ``config.refuses_null_path_key`` is the
# membership these doors all answer to.
ERR_CONFIG_NULL_PATH_REASON = "kanibako gives a null path key no meaning."
ERR_CONFIG_NULL_PATH_HEAD = (
                        "%s sets these path keys to null:\n  %s\n")
                                                    # the file path, the offending keys
#: The READ-TIME cue — the default, since the lines are already there.
ERR_CONFIG_NULL_PATH_CURE = (
                        ERR_CONFIG_NULL_PATH_REASON + " Delete those lines to use each key's " +
                        "default, or set the path you mean.")
#: The SET door's lead — the keys, and no file, because none holds them.
ERR_CONFIG_NULL_PATH_SET_HEAD = (
                        "the launch refuses a null at these path keys, so this set is refused too:\n  %s\n")
ERR_CONFIG_NULL_PATH = ERR_CONFIG_NULL_PATH_HEAD + ERR_CONFIG_NULL_PATH_CURE
# ⚑ A BOX SCALAR at a ``null`` its own DECLARED DEFAULT gives no meaning to (spec §2b).  The
# split and the shared BUILDER are the path doors' above; the membership is
# ``config.refuses_null_box_scalar``.  The REASON is the launch's own clause: §2h keeps a
# ``None`` for the CONSUMER, and a box scalar whose default is a value has no consumer a
# null could mean anything to.  It is NOT the path-key reason — ``box.image`` is an image
# reference, and "a null path key" would be a false thing to read.
ERR_BOX_SCALAR_NULL_REASON = (
                        "kanibako gives a null no meaning here: this key's default is a value, "
                        "and a null is not a second spelling of it.")
ERR_BOX_SCALAR_NULL_HEAD = (
                        "%s sets these box scalar keys to null:\n  %s\n")
                                                    # the file path, the offending keys
#: The READ-TIME cue — the default, since the lines are already there.
ERR_BOX_SCALAR_NULL_CURE = (
                        ERR_BOX_SCALAR_NULL_REASON + " Delete those lines to use each key's " +
                        "default, or set the value you mean.")
#: The SET door's lead — the keys, and no file, because a ``set`` that refused wrote none.
ERR_BOX_SCALAR_NULL_SET_HEAD = (
                        "the launch refuses a null at these box scalar keys, so this set is "
                        "refused too:\n  %s\n")
                                                    # the offending keys
# ⚑ The BOX STORE at a value the LAUNCH cannot use (spec §0, §2c) — the same
# "one reason, one home" split, and the same carrier: ``config.refuses_box_store_value`` is
# the membership and ``config.null_path_keys_error`` builds the text, so the ``--null`` and
# empty-value doors share ONE builder. The two REASONs are the launch's own clauses.
ERR_BOX_STORE_TRAILING_REASON = (
                        "its trailing separator means the final path segment resolved to "
                        "nothing (an empty @meta.box.name leaves the box root pointing at the "
                        "SHARED box store, so every box in the workset would resolve the same "
                        "home)")
ERR_BOX_STORE_EMPTY_REASON = (
                        "the box root 'meta.box.path' derives from '@workset.boxes', so a "
                        'settings file that sets workset.boxes to null / "" — or removes it '
                        "— leaves every key rooted at the box root pointing somewhere at the "
                        "filesystem root")
#: The SET door's lead — the key, and no file, because a ``set`` that refused wrote none.
ERR_BOX_STORE_SET_HEAD = (
                        "the launch refuses this value at the box store key, so this set is "
                        "refused too:\n  %s\n")
                                                    # the offending key
# ⚑ THE SET DOOR'S HALF of the ordering rule (system-design "Ordering rule") for the SYSTEM PATH TIER;
# scope and why are stated once, on ``config.system_path_ref_error``. ⚑ The @-sigils are
# LITERAL, unlike ``ERR_SETTINGS_BAD_REF``'s spliced one — this names what the user wrote.
ERR_CONFIG_PATH_REF_SCOPE = (
                        "%s is set to %r, which points at '@%s' — outside the system "
                        "path tier. A system path value may reference only @config.* keys "
                        "and the system path keys, so it cannot be read back at that "
                        "stage — it is resolved before every other key. Reference one of "
                        "those keys instead, or set the path you mean.")
                                                    # the key, the value, the offending ref
# ⚑ THE SET DOOR'S ordering rule (system-design "Ordering rule") for every other key;
# scope and why are stated once, on ``config.ref_order_error``.
ERR_CONFIG_REF_ORDER = (
                        "%s is set to %r, which points at '@%s' — a %s key, which resolves "
                        "after the %s keys this one belongs to. A key may reference only "
                        "keys of its own set or of a set resolved before it (system-design "
                        "\"Ordering rule\"). Reference one of those keys instead, or set the "
                        "value you mean.")
                                                    # the key, the value, the ref, its set, the key's set
#: The SET door's per-owner refusal (keyspec §0 "Per-owner resources"), and its words per owner.
ERR_PER_OWNER_SET = (
                        "nothing was written: %s = %r (%s scope, %s) would %s, "
                        "because the value does not reach %s identity. %s Spell "
                        "the identity into the value: %r, or set %s in each %s's own file.")
PER_OWNER_SET_WORDS = {
    "workset": ("working-set", "working set"),
    "partition": ("partition", "working set"),
    "box": ("box", "box"),
    "agent": ("agent", "agent"),
}
#: Who would share the path, by owner and by what the value lacks (``config.identity_gap``).
PER_OWNER_SHARERS = {
    ("workset", "none"): ("give every working set one shared path", "Same-named boxes in "
                          "different working sets, and every standalone box, would share it."),
    ("workset", "standalone"): ("give every standalone box one shared path", "Every standalone "
                                "box would share it."),
    ("partition", "none"): ("give every channel partition one shared path", "Same-named boxes "
                            "in different working sets would share it."),
    ("partition", "workset"): ("leave the channel partition unnamed", "A channel partition is "
                               "keyed by its working set's name, {meta.workset.name}, not its "
                               "path."),
    ("box", "none"): ("give every box one shared path", "Every box would share it."),
    ("box", "paired"): ("give same-named boxes in different working sets one shared path",
                        "Same-named boxes in different working sets would share it."),
    ("box", "own"): ("give the boxes of each working set one shared path", "The boxes of each "
                     "working set would share it."),
    ("box", "own+standalone"): ("give the boxes of each working set one shared path", "The "
                                "boxes of each working set, and every standalone box, would "
                                "share it."),
    ("agent", "none"): ("give different agents one shared path", "Different agents would "
                        "share it."),
}
#: The READ door's twin, for a value the system file already holds; the same words per owner.
ERR_PER_OWNER_READ = (
                        "%s is set to %r in %s, which would %s, "
                        "because the value does not reach %s identity. %s Nothing was changed. "
                        "Spell the identity into the value: kanibako system set "
                        "'%s=%s'; or run kanibako system reset %s, then kanibako workset "
                        "set <workset> '%s=…' in each %s. To stop running boxes "
                        "meanwhile: kanibako stop --all stops every running box even when "
                        "a settings file is refused; it lists them and asks first. On a first "
                        "run (no kanibako.cfg yet), these commands refuse too: edit the "
                        "system settings file by hand.")
                                                    # key, value, file, the words, the cure
#: The LAUNCH twin, for a key or bind entry any containing settings file holds.
ERR_PER_OWNER_LAUNCH = (
                        "%s is set to %r in %s, which would %s, "
                        "because the value does not reach %s identity. %s Nothing was changed. "
                        "Spell the identity into the value: %r, or put it in each %s's "
                        "own file%s.")
                                                    # the key, value, file, words, cure, tail
#: The tail for an undeclared entry: its shared category key, for sharing on purpose.
PER_OWNER_SHARE_TAIL = "; to share one path on purpose, move it to %s"
#: The SET door's lead for ``workset_dirkeys.early_key_set_error``'s refusal.
ERR_WORKSET_EARLY_SET_HEAD = (
                        "nothing was written: this value could not be read back.\n  %s")
                                                    # the reader's refusal
ERR_WORKSET_SET_MAKES_STANDALONE = (
                        "a null workset.registry would make working set '%s' standalone, so "
                        "nothing was written. Rerun with --force to set it anyway.")
WARN_WORKSET_SET_MAKES_STANDALONE = (
                        "Warning: a null workset.registry makes working set '%s' standalone.")
                                                    # the workset name (both)
# ⚑ The §2a bad-ENTRY report, ONE per file the command reads, and the only thing ``get``
# says about them.  ``config.chain_reaches`` separates the two arms below.
WARN_CONFIG_BAD_ENTRIES = "%s stores entries that are not keys (spec §0):\n  %s"
                                                    # the file path, the entries
WARN_CONFIG_ILL_TYPED_ENTRIES = "%s stores values their keys refuse (spec §2a):\n  %s"
                                                    # the file path, the entries
WARN_CONFIG_UNRESOLVABLE_ENTRIES = "%s stores values that do not resolve (spec §2a):\n  %s"
ERR_STORED_NON_SCALAR = "a %s where one %s value goes"   # the shape, the key's type
#: The OUT-of-chain refusal's tail — what it did not do, and the two cures.
ERR_CONFIG_BAD_ENTRIES_TAIL = (
                        "\nNothing was written. Remove those entries by editing the file, or "
                        "rerun with --force to set anyway.")
#: The HARD arm: a bad entry the value's own upstream chain REACHES, which ``--force``
#: does not reach.  Names the broken upstream so it can be repointed or repaired.
ERR_CONFIG_CHAIN_BAD_ENTRY = (
                        "the edited value's own upstream chain reaches %s, a bad entry, so "
                        "this set is refused too (spec §2a):\n%s\n"
                        "Name another upstream, or fix that entry by editing the file. "
                        "--force does not set a value whose own chain is broken.")
                                                    # the broken upstream, the entries
ERR_PROJECT_NO_PATH =   "Project path '%s' does not exist." # the path that does not exist
ERR_PROJECT_BAD_DESIGNATION = "Invalid box designation %r: it is neither a box name nor a path."
# ⚑ The two $HOME-guard messages take NO arguments (raised bare).
ERR_PROJECT_NEW_HOME = ("Refusing to create project rooted at $HOME: this would mount the " +
                        "entire home directory as the workspace.\n If you really want a " +
                        "project here, use:\nkanibako create --standalone ~ --allow-home")
ERR_PROJECT_REG_HOME = ("Refusing to register $HOME as a project path: this would mount the " +
                        "entire home directory as the workspace.")
ERR_PROJECT_NAME_USED = "Name '%s' is already registered"
# ⚑ "one record per project" (spec § Detection & import) asked at the PATH, not the name.
ERR_PROJECT_PATH_IS_NAMED_BOX = ("Refusing to create a box at %s: it is already the workspace of " +
                         "named box '%s' in workset '%s', and one path is one project's record. " +
                         "Use that box, or free the path first:\n"
                         "  kanibako box show %s/%s\n"
                         "  kanibako workset disconnect %s %s --force") # path, box, workset (ws, box, ws, box)

ERR_WORKSET_NO_PROJECT = "Project '%s' not found in workset '%s'" # project name, workset name
ERR_WS_CONNECT_PATH_IS_PRIMARY_BOX = (
    "it is already the workspace of primary box '%s'; to make it a member of '%s' "
    "instead, convert that box (in place, or under another name), move it out of the "
    "way, or drop the box:\n"
    "  kanibako box convert %s --workset %s%s\n"
    "  kanibako box convert %s --workset %s --name <member> --move\n"
    "  kanibako box move %s <path>\n"
    "  kanibako box rm %s"
) # box, ws, box, ws, ' --name <leaf>' when in-tree under another name, box, ws, box, box
ERR_WORKSET_NO_WORKSET = "No workset found for path: %s" # project dir
ERR_WORKSET_WS_NOT_BOX = ("'%s' is a workset, not a single project box. Name a project inside it " +
                          "(e.g. '%s/<project>') or run the command from a project workspace " +
                          "under that workset.") # ⚑ the name TWICE — two args, one value
ERR_WORKSET_NOT_IN_BOX = ("Inside workset '%s' but not in a specific project workspace. Change " +
                          "to a project directory under %s/.") # workset name, workspaces dir
ERR_WORKSET_MEMBER_NAME_CONFLICT = ("In working set '%s' a box's name IS its member name, so '%s' and " +
                          "'%s' are two names for one box; pass the member name alone.") # workset name, --name, identifier
ERR_WORKSET_MEMBER_NAME_TAKEN = ("Project '%s' already exists in working set '%s'; member names are " +
                          "compared case-blind.") # stored spelling, workset name
ERR_WORKSET_MEMBER_NO_RECOVER = ("--recover found no interrupted 'create' of '%s' in working set " +
                          "'%s', so there is nothing to resume.") # member name, workset name
# ⚑ A null ``workset.workspaces`` HAS a meaning (no workspace dir, spec §2c), unlike the
# null path keys ERR_CONFIG_NULL_PATH refuses — so this names what cannot be created there.
ERR_WORKSET_NULL_WORKSPACES = ("%s sets workset.workspaces to null, so it has no workspace " +
                               "directory and cannot hold %s.\nConnect a directory outside " +
                               "it instead, delete that line to use the default, or set the " +
                               "path you mean.") # the workset.yaml path, what was refused
# ⚑ The STANDALONE face: a lone box's root has no "outside" member to connect, so the cure
# is the key alone (``refuse_null_workspaces(..., standalone=True)``).
ERR_STANDALONE_NULL_WORKSPACES = ("%s sets workset.workspaces to null, so this standalone box has " +
                                  "no workspace directory and cannot hold %s.\nDelete that line " +
                                  "to use the default, or set the path you mean.")
                                                    # the workset.yaml path, what was refused
# ⚑ The LAUNCH face (Q106): the workspace bind is mounted at every launch, so a box whose
# ``meta.box.workspace`` resolves through a null ``workset.workspaces`` cannot run.
ERR_NULL_WORKSPACE_BIND = ("Cannot launch box '%s': %s sets workset.workspaces to null, so the " +
                           "box has no workspace to mount at ~/workspace.\nDelete that line " +
                           "to use the default, or set the path you mean.")
                                                    # the box label, the workset.yaml path

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

