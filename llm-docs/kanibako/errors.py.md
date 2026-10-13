# The Error Hierarchy — one base, one clean exit

The exception classes here are almost all bare subclasses. The file is small and the code in it is
trivial, which is exactly why it is worth reading carefully: **a large share of the `raise`
statements in `src/` and `packages/` name one of these classes directly**, so whatever these
one-line descriptors say is what those sites believe they are raising.

The one thing the hierarchy actually *does* is decide how a failure LEAVES the program.
`cli.main`'s top-level dispatch has exactly three arms:

```python
except UserCanceled:      print("Aborted.");            rc = 2
except KanibakoError as e: print(f"Error: {e}", stderr); rc = 1
except KeyboardInterrupt:  print();                      rc = 130
```

There is no catch-all. **Deriving from `KanibakoError` is what turns a failure into a one-line
`Error: …` and exit 1; anything outside the hierarchy reaches the user as a traceback.** That is the
whole contract, and it is why the base class carries a `⚑` marker in the source.

## What is NOT in this file

`KanibakoError` is the base of the hierarchy, not the base of every exception in the tree, and this
file is not the whole hierarchy either. Both facts matter to the `cli.py` contract above.

**Some `KanibakoError` subclasses live next to their subsystem instead of here** — deliberately, and
they exit cleanly like the rest:

* `settings.settings_resolve.SettingsError`, and its child
  `settings.settings_expand._ExpandedShapeError`
* `project.import_reconcile.ImportConflictError`
* `settings.keystore.ReservedKeyError` — also a `KeyError`, for callers that catch one; it prints a
  clean `Error:` line too
* `snapshots`: `UnsafeSnapshotNameError`, `ForeignSnapshotError`, `RestoreRefusedError` (and its child
  `RestoreLinkRefusedError`), `RestoreUndoFailedError`, `SnapshotSafetyError`

**These exception classes are OUTSIDE the hierarchy** and derive straight from `Exception` (the
standalone `scripts/import-directives.py` has its own as well). An instance of one of these escaping
to `cli.py` is a traceback, not an `Error:` line:

* `tweakcc_cache.TweakccCacheError`
* `settings.settings_expand._LenientDefect`
* `settings.settings_views.ViewError`
* `targets.assembly.BindingSourceError`
* `commands.flags.FlagRelevanceError`
* `commands.code_cmd._CodeShimError`
* `browser_sidecar.BrowserSidecarError`
* `bun_sea.BunSEAError`

Others are outside it too. These derive from `OSError` or `ConnectionError`:
`boxfs.SymlinkRefusedError`, `project.workset.StoreRemovalError`, and
`channels.helper_client.FrameTooLarge`. These derive from `yaml.YAMLError`:
`settings.config_io`'s `DuplicateKeyError`, `CyclicAnchorError`, and `DocumentTooDeepError`, which
`config_io.load_doc` re-raises as `ConfigError`.

Several of those are internal sentinels caught close to where they are raised, which is a fine reason
to sit outside the hierarchy. The point is not that they are wrong — it is that **"is it a
`KanibakoError`?" is a question with a user-visible answer**, so a new exception class is a decision,
never a default.

## The root

```class KanibakoError(Exception):```
Base of the kanibako error hierarchy — what `cli.py` catches.

⚑ The source keeps a one-line marker here because this is the line where the mistake gets made: a
new class added to this file that does NOT subclass `KanibakoError` inherits none of the exit
contract and nothing in the file's shape objects.

## Configuration faults — `ConfigError` and its two structured children

```class ConfigError(KanibakoError):```
Configuration missing, malformed, or refused.

⚑ **This is broader than "a config file is bad".** The `raise ConfigError` sites in `agent_ref.py`
refuse a malformed `--agent` REF STRING — a value typed on the command line, with no file anywhere
in the story. Its two
subclasses below are likewise not file faults: one is a *declaration* collision, the other a *scope*
violation. What the family really means is "the configuration the user expressed is not usable" —
whatever surface it was expressed on.

### `CategoryCollisionError`

```class CategoryCollisionError(ConfigError):
    def __init__(self, message: str, *, kind: str, box_dest: str,
                 entries: "tuple[tuple[str, str | None], ...]" = ()) -> None```
Two category declarations target one resolved `box_dest` (spec §0).

A user CONFIGURATION fault, hence a `ConfigError`. It is carried STRUCTURED rather than as message
text alone for two reasons: tests assert on fields instead of on wording, and a CLI seam can enrich
the rendered text with what the pure resolver does not know.

**The seam is `commands/start._annotate_pref_origin`.** It reads `exc.entries`,
asks `settings_prefs.pref_origin` which `pref` REQUEST installed each participant, and appends a line
naming that request, its LEVEL, and the settings FILE it came from — then rebuilds the exception with
the same `kind` / `box_dest` / `entries`. The user-visible payoff: a message naming
`agent.claude.common.~/plugins` is useless to someone whose files only contain
`pref.agent.claude.common`.

**The fields.**

* `box_dest` is the collision key — the resolved in-box destination the declarations contend for.
* `entries` is the ordered tuple of `(key, host_src)` pairs that participate. **Its order is the
  order the rendered message names them in**, at both raise sites: `raise_binding_vs_binding` builds
  it from the same `concrete` list it feeds to `_entry_lines`, and `raise_extension_onto_occupied`
  builds it as `(extension, base)` while its message names the extension first. A reordering at
  either raise site silently desynchronizes the structured view from the prose.
* `kind` discriminates which refusal fired. The set is CLOSED at two values, and both live in
  `settings/settings_categories.py`:

`"binding_vs_binding"` — two CONCRETE declarations at one destination.
: `CONCRETE_CATEGORIES` is `("bindings.ro", "bindings.rw", "secret_path")`, so this
  covers two `bindings.{ro,rw}`, a `bindings.*` against a `secret_path`, and any other pairing
  among the three. `~/canon/workbook/specs/system-design-1.8.0.md` § *Collision arbitration*:
  *"TWO MOUNTS AT ONE DESTINATION ARE AN ERROR IN EVERY SCOPE COMBINATION BUT ONE"* — the one
  being `common` and `caches` in one scope, which the store-shape producer decides first, so it
  never reaches this error. Two bindings disagreeing on ro/rw is a semantic disagreement, and
  silently picking one hands the user a read-only mount where they asked for writable.
  ⚑ **The D2 CARVE-OUT is the caller's test, not this error's** (`secret_path_deliveries`): a
  group whose concrete members are ALL `secret_path` is not a collision at all. Their dest is
  `SECRET_MOUNT_DIR/{VAR}` by construction, so two of them at one dest are always the same VAR
  arriving from two scopes — the per-VAR cascade spec §2a documents as a FEATURE.

`"extension_onto_occupied"` — an ABSTRACT declaration derives a binding onto an occupied destination.
: `ABSTRACT_CATEGORIES` is `("common", "caches", "seeded")` — **all
  three**, which is what the rendered message itself says. The explicit binding is the BASE and
  SURVIVES; the derived EXTENSION is refused. § *Collision arbitration* states the refusal as
  SYMMETRIC (after the fold nothing distinguishes an explicit entry from a derived one) — no
  contradiction: this refusal is decided inside ONE scope, at production, before the collapse ever
  sees it. What that section requires of the message is the DIAGNOSIS — name the extending
  declaration, the occupant, and the dest — and that is what the base/extension asymmetry buys.

Both refusals are raised by named helpers rather than inline, so the spec-mandated message text
exists exactly once each: `settings_categories.raise_binding_vs_binding` and
`raise_extension_onto_occupied`.

⚑⚑ **Cite these refusals by name, never as `§0 row-1` / `§0 row-3`.** The collision table in the
system design's § *Collision arbitration* is keyed by the ARRIVING entry (bind / mask /
copy-file / copy-dir), and its numbered refusal list puts bind-vs-bind at **2**;
`extension_onto_occupied` has no number — it is the section's base-vs-extension prose. No row label
remains in `src/` or `packages/`.

### `TemplateScopeError`

```class TemplateScopeError(ConfigError):```
A template/seed copy tried to write OUTSIDE its scope's allowed surface.

**Why it is a hard refusal and never a skip.** The whitelist is a CORRECTNESS property, not a style
rule. A template that could plant `<scope>.yaml` at a scope root would be planting
`meta.<scope>.settings` — the cascade's own last word. At workset scope the same escape reaches
`registry.yaml` (the AUTHORITATIVE box membership), `auth/`, `vault/` and `workspaces/`: the user's
credentials and code. The system design's § *The seed whitelists — what the copier may write*
denies them by name.


**Where it is raised — TWO surfaces, not one.**

`launch/templates.copy_tree` is "the ONE copier" by its own docstring: every
template stage, box seed and host-store fill routes through it, so the defenses cannot be present on
one path and missing on another. It enforces four points, and they are exactly what the system
design's § *The seed whitelists — what the copier may write* demands of the copier — *"WHITELISTS,
per scope. Anything not listed is DENIED by default"* and *"The copier MUST reject `..` components
and MUST NOT follow symlinks out of the destination subtree"*:

1. **Whitelist** — an entry whose first STORE-RELATIVE path component is not in the scope's
   whitelist. Deny-by-default: an unlisted entry is an ERROR, never a silent skip.
   (`_check_whitelist`, called per entry.)
2. **Destination containment** — a resolved destination outside the scope store root, the `..`
   escape. (`_assert_contained`, called once for the root and once for each entry's parent dir.)
3. **Source symlink** — checked BEFORE `is_file()`, which would follow it. Without this,
   `x -> ~/.ssh/id_ed25519` would have its TARGET's bytes copied into a box home.
4. **Destination-leaf symlink** — a destination whose real path escapes the subtree through
   a symlinked intermediate directory, where the escape is the leaf itself and the parent check above
   therefore cannot see it.

⚑ **`launch/templates.stage_layers` reaches it through the copier too**: it stages each layer
with `copy_tree`, so a symlink in a layer is refused by the copier while it is still a link.

`commands/workset_cmd.run_create` is the only in-tree catcher.

## Plain leaf errors

```class ProjectError(KanibakoError):```
Project cannot be resolved, or its name/location is refused.

⚑ Broader than *"path does not exist or cannot be resolved"*. Several raise sites are
REFUSALS of a perfectly resolvable project, each with its own message constant in
`settings/messages.py`: `ERR_PROJECT_NAME_USED`, `ERR_PROJECT_REG_HOME` (registration of HOME), and
`ERR_PROJECT_PATH_IS_NAMED_BOX` (the path is already a named box's workspace). `project/names.py`
adds the unknown-name refusal; the ambiguous-name one is `AmbiguousNameError`, below.

```class BoxNameError(ProjectError):
    def __init__(self, message: str, name: str) -> None
    def with_cure(self, cure: str) -> str```
A new box name breaks the box-name rule; each caller adds its cure.

Raised by `launch/box_identity.py`'s name check; it carries the refused `name`, and `with_cure`
appends the caller's cure to the message. The box verbs (`box create`, `box duplicate`, the
`box` parser, `workset` commands) catch it and report it with their own cure.

```class DerivedBoxNameError(BoxNameError):```
A box name taken from a directory breaks the box-name rule.

Raised where a name is DERIVED rather than typed: `settings/paths.py` (a box named for its
directory), `launch/box_identity.py`, and `launch/box_resolve.standalone_box_name` (a moved
standalone whose new leaf has no ASCII spelling — every door refuses with the one cure).

```class LegacyBoxNameError(ProjectError):```
A box typed by a stored name the box-name rule refuses: it is reached by its path only.

Raised by `settings/paths.refuse_legacy_box`, the one carrier of that refusal, which the
designation cascade runs for every verb that looks a box up by name.

```class UnmarkedStandaloneError(ProjectError):```
A registered standalone root lost its marker: refused, never "no box".

Raised in `commands/box/_lifecycle.py` when a registered standalone root has no marker left; the
same module catches it where it reports the refusal.

```class AmbiguousNameError(ProjectError):```
A bare box NAME matched several boxes, or only a member of a workset outside the path space.

Raised by `project/names.py`; its message names `<workset>/<box>`. ⚑ A caller that catches
`ProjectError` to mean "no such box" must exclude it, so an ambiguity is never read as a miss.

```class ContainerError(KanibakoError):```
Container runtime or image operation failed.

Raised across the runtime and image layers.

```class LabelQueryError(ContainerError):```
A runtime that is present failed to say which containers carry a box's labels.

Raised by `runtime/container.py` when `ps` does not run or fails. A destructive verb (`stop`,
`box rm`) refuses on it: the box may be running under another name.

```class PackagingError(KanibakoError):```
kanibako's packaged data is missing or incomplete; the install is broken.

Raised by `settings/core_defaults.py` when a shipped data file is missing or malformed. No in-tree
caller catches it by name, so it reaches `cli.py` as an `Error:` line.

```class ArchiveError(KanibakoError):```
Archive creation, extraction, or validation failed.

No in-tree raiser, catcher, or importer; kept declared as public API. `commands/archive.py`
catches `GitError`, which `git.py` raises.

```class GitError(KanibakoError):```
Git check failed (uncommitted changes, unpushed commits, etc.).

Exactly two raise sites, `git.check_uncommitted` and `git.check_unpushed`, and they are exactly the
two the descriptor names. Caught twice in `commands/archive._archive_one`.

```class WorksetError(KanibakoError):```
Workset creation, loading, or manipulation failed.

Raised across the workset layer.

```class LegacyWorksetIdentityError(WorksetError):```
A workset root's `workset.yaml` still carries a RETIRED workset identity table — either the
`workset.meta` spelling (1.6.0/1.7.x) or the `meta.workset` one (the unreleased 1.8.0 tree). A 1.8.0
workset has no identity table anywhere under its root; it is named by the global registry.

One raise site, `project/workset.py:refuse_retired_workset_identity`, reached from `_load_workset`
and from `settings/paths.py`'s ancestor walk. ⚑ **The subclass exists so ONE blanket catch can
re-raise it:** `commands/box/_parser.py:_resolve_standalone_target` swallows every exception out of
`detect_project_mode` as "a non-project path is simply a miss", and an un-migrated workset root in
the ancestor walk is a named thing to fix, not a miss. Cure: MIGRATION.md § "A workset root no
longer carries an identity table; an un-migrated root refuses".

```class ReservedWorksetNameError(WorksetError):```
A registered workset carries a name 1.8 reserves; the message carries the cure.

Raised by `project/workset.py` for a registered workset whose name is in `RESERVED_WORKSET_NAMES`
(the default workset's identifiers, the channel partition tokens, and the primary and standalone
container-name segments); the message says to register it again under its directory name.
`settings/paths.py` and `launch/box_resolve.py` re-raise it ahead of their `WorksetError` catch,
so a reserved name is never read as "no workset".

```class LegacyRegistryIdentityError(WorksetError):```
A per-workset `registry.yaml` still carries a RETIRED `workset:` identity table or `projects:` map
(the unreleased 1.8.0 tree only). One raise site,
`project/workset_registry.py:_refuse_retired_registry_sections`, reached from `_load_raw` — the
module's ONE read seam, so every read of that file passes it. Cure: MIGRATION.md § "A workset
root no longer carries an identity table; an un-migrated root refuses".

```class UserCanceled(KanibakoError):```
User canceled an interactive prompt.

⚑ It is the ONE member with its own `cli.py` arm: `Aborted.` on stdout and **exit 2**, not the
generic `Error: …` / exit 1. `utils.confirm_prompt` raises it both when the answer is not literally
`yes` and on `EOFError` / `KeyboardInterrupt` at the prompt, so a piped-in non-answer cancels rather
than crashes.

```class SubjectConflictError(KanibakoError):```
A positional box subject and `--box` named DIFFERENT targets.

Raised in one place, `commands/flags.resolve_subject_value`, under the general rule its docstring
states: wherever a positional and `--box` coexist, both supplied with DIFFERENT strings is this error.

## The agent-resolution family

```class AgentResolutionError(KanibakoError):```
Agent could not be resolved for an agent-requiring command.

**The `str()` of the exception — and of every subclass — IS the user-facing message.** Callers do not
reformat it; they let it reach `cli.py`, which prints it verbatim behind `Error: `. That is why each
raise site writes a multi-line message with a cure in it, and why
comments in `commands/agent_cmd.py` and `commands/start.py` note that the error "surfaces verbatim".
Reword one of these messages and you have reworded user-facing output.

⚑ **Only ONE of these carries an install command — `AgentNotInstalledError`.** With no plugin
installed the launch answers `AgentUnsetError`, whose cure is `kanibako setup` at every installed
count, and `commands/setup_cmd.py`'s Step 2 prints the tailored
`install_method.install_command(...)`. Do not add one here: the two refusals are deliberately one
sentence each about what is UNSET, not a report on what is installed.

Two subclasses are raised from `settings/config.py`'s `resolve_agent`; the third,
`AgentNoDefaultError`, is raised one seam earlier, in `settings/agent_select.py`'s `select_agent`,
because only that function can see the difference between *unset* and *present-`None`*.

🛑 **THERE IS NO INSTALLED-AGENT COUNT RULE** (keyspec §2b). Nothing
auto-selects an agent, not even when exactly one plugin is installed, and the installed set is read
for ONE question only — *is this NAME installed?* — which is why it is read inside the named-ref
branch and is not in scope on the refusal path.

```class AgentUnsetError(AgentResolutionError):```
`system.agent` is UNSET at every tier: setup has never chosen one.

The fall-through case in `resolve_agent`: nothing resolved a name, at any count. The message
prescribes `kanibako setup`, names `--agent <name>` for a single run, and points at `kanibako shell`
for reaching the container without an agent.

```class AgentNoDefaultError(AgentResolutionError):```
`system.agent` resolved to present-`None`: no default is set, so an agent must be named.

Raised in `select_agent`, not here — `resolve_agent` receives `None` for BOTH absent arguments and
cannot tell the two states apart. ⚑ **It is reachable by TYPO:** YAML reads `null`, `Null`, `NULL`,
`~` and a key left blank after its colon as Python `None`. `None`/`none` are STRINGS and land on
`AgentNotInstalledError` instead, so these three messages must stay mutually unmistakable.

```class AgentNotInstalledError(AgentResolutionError):```
A name resolved (explicit `--agent`, cascade, or default) but that agent adapter is not installed.

Raised by `config.resolve_agent`. ⚑ The commonest trigger is an explicit `--agent`:
`resolve_agent` reads `explicit_agent` FIRST, so a typo'd `--agent` lands here. What
must be installed is the HARNESS (`harness_of(node)`), never the composite node-name — a persona
segment is free-form, so `navigator+claude` requires `claude` to be installed and asks nothing of
`navigator`.
