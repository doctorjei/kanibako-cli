# The Error Hierarchy — one base, one clean exit

Fifteen exception classes, almost all of them bare subclasses. The file is small and the code in it
is trivial, which is exactly why it is worth reading carefully: **its fan-in is 289 non-test call
sites, the second-highest in the tree**, so whatever these one-line descriptors say is what several
hundred `raise` sites believe they are raising.

The one thing the hierarchy actually *does* is decide how a failure LEAVES the program.
`cli.py`'s top-level dispatch (`cli.py:523-534`) has exactly three arms:

```python
except UserCancelled:      print("Aborted.");            rc = 2
except KanibakoError as e: print(f"Error: {e}", stderr); rc = 1
except KeyboardInterrupt:  print();                      rc = 130
```

There is no catch-all. **Deriving from `KanibakoError` is what turns a failure into a one-line
`Error: …` and exit 1; anything outside the hierarchy reaches the user as a traceback.** That is the
whole contract, and it is why the base class carries the only `⚑` marker left in the source.

## What is NOT in this file

`KanibakoError` is the base of the hierarchy, not the base of every exception in the tree, and this
file is not the whole hierarchy either. Both facts matter to the `cli.py` contract above.

**Two `KanibakoError` subclasses live next to their subsystem instead of here** — deliberately, and
they exit cleanly like the rest:

* `SettingsError` — `settings/settings_resolve.py:117`
* `ImportConflictError` — `project/import_reconcile.py:58`

**Nine exception classes are OUTSIDE the hierarchy** and derive straight from `Exception` (or
`KeyError`). An instance of one of these escaping to `cli.py` is a traceback, not an `Error:` line:

| Class | Where |
|---|---|
| `TweakccCacheError` | `tweakcc_cache.py:36` |
| `_LenientDefect` | `settings/settings_expand.py:131` |
| `ReservedKeyError` (a `KeyError`) | `settings/keystore.py:33` |
| `ViewError` | `settings/settings_views.py:107` |
| `BindingSourceError` | `targets/assembly.py:51` |
| `FlagRelevanceError` | `commands/flags.py:479` |
| `_CodeShimError` | `commands/code_cmd.py:90` |
| `BrowserSidecarError` | `browser_sidecar.py:182` |
| `BunSEAError` | `bun_sea.py:40` |

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

⚑ **This is broader than "a config file is bad", and the older descriptor said only that.** Of the
20 `raise ConfigError` sites in `src/`, five are in `agent_ref.py` and refuse a malformed `--agent`
REF STRING — a value typed on the command line, with no file anywhere in the story. Its two
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

**The seam is `commands/start._annotate_pref_origin` (`start.py:7284`).** It reads `exc.entries`,
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
  either raise site silently desynchronises the structured view from the prose.
* `kind` discriminates which refusal fired. The set is CLOSED at two values, and both live in
  `settings/settings_categories.py`:

`"binding_vs_binding"` — two CONCRETE declarations at one destination.
: `CONCRETE_CATEGORIES` is `("bindings.ro", "bindings.rw", "secret_path")`
  (`settings_categories.py:446`), so this covers two `bindings.{ro,rw}`, a `bindings.*` against a
  `secret_path`, and any other pairing among the three. Spec §0: *"TWO MOUNTS AT ONE DESTINATION ARE
  NOW AN ERROR IN EVERY SCOPE COMBINATION"* — unconditional, any scope, any mode, because two
  bindings disagreeing on ro/rw is a semantic disagreement and silently picking one hands the user a
  read-only mount where they asked for writable.
  ⚑ **The D2 CARVE-OUT is the caller's test, not this error's** (`settings_categories.py:440-445`): a
  group whose concrete members are ALL `secret_path` is not a collision at all. Their dest is
  `SECRET_MOUNT_DIR/{VAR}` by construction, so two of them at one dest are always the same VAR
  arriving from two scopes — the per-VAR cascade spec §2a documents as a FEATURE.

`"extension_onto_occupied"` — an ABSTRACT declaration derives a binding onto an occupied destination.
: `ABSTRACT_CATEGORIES` is `("common", "caches", "seeded")` (`settings_categories.py:260`) — **all
  three**, which is what the rendered message itself says. The explicit binding is the BASE and
  SURVIVES; the derived EXTENSION is refused. Spec §0 states the collapse-time refusal as SYMMETRIC
  (after the fold nothing distinguishes an explicit entry from a derived one) — no contradiction:
  this refusal is decided inside ONE scope, at production, before the collapse ever sees it. What
  spec §0 requires of the message is the DIAGNOSIS — name the extending declaration, the occupant,
  and the dest — and that is what the base/extension asymmetry buys.

Both refusals are raised by named helpers rather than inline, so the spec-mandated message text
exists exactly once each: `settings_categories.raise_binding_vs_binding` and
`raise_extension_onto_occupied`, three callers apiece.

⚑⚑ **KNOWN DELTA — the `§0 row-1` / `§0 row-3` numbering does not resolve.** The old docstring said
*kind* "discriminates the §0 table row that fired" and labelled the two kinds Row 1 and Row 3. The
live spec `~/canon/workbook/specs/settings-keyspace-1.8.0.md` §0 has no such numbering: its collision
table (L113-118) is keyed by the ARRIVING entry (bind / mask / copy-file / copy-dir), and its
numbered refusal list (L138-148) reads 1 = an existing MASK parent refuses every arrival, 2 =
bind-vs-bind, 3 = mask-vs-mask. So `binding_vs_binding` is refusal **2**, and `extension_onto_occupied`
has no numbered refusal at all — it is stated as prose at L164-169. **The numbering is stale
throughout, not just here**: `settings_categories.py:884` and `:919` carry the same `§0 row-1` /
`§0 row-3` spelling. Not repaired by this pass (it is a code-comment fix across another module);
recorded so nobody treats the labels as resolvable citations.

### `TemplateScopeError`

```class TemplateScopeError(ConfigError):```
A template/seed copy tried to write OUTSIDE its scope's allowed surface.

**Why it is a hard refusal and never a skip.** The whitelist is a CORRECTNESS property, not a style
rule. A template that could plant `settings.yaml` at a scope root would be planting
`meta.<scope>.settings` — the cascade's own last word. At workset scope the same escape reaches
`registry.yaml` (the AUTHORITATIVE box membership), `auth/`, `vault/` and `workspaces/`: the user's
credentials and code. Spec §2a's note on this names those three as *"USER DATA AND SECRETS"*
(`settings-keyspace-1.8.0.md:924`).

**Where it is raised — TWO surfaces, not one.**

`launch/templates.copy_tree` (`templates.py:358`) is "the ONE copier" by its own docstring: every
template stage, box seed and host-store fill routes through it, so the defences cannot be present on
one path and missing on another. It enforces four points, and they are exactly what spec §2a demands
of the copier — *"WHITELISTS, per scope. Anything not listed is DENIED by default"* (L904) and *"The
copier MUST reject `..` components and MUST NOT follow symlinks out of the destination subtree"*
(L929, restated for the seed dests at L966-967):

1. **Whitelist** — an entry whose first STORE-RELATIVE path component is not in the scope's
   whitelist. Deny-by-default: an unlisted entry is an ERROR, never a silent skip.
   (`_check_whitelist`, called at `templates.py:446`.)
2. **Destination containment** — a resolved destination outside the scope store root, the `..`
   escape. (`_assert_contained`, called at `:429` for the root and `:453` for each parent dir.)
3. **Source symlink** (`:433`) — checked BEFORE `is_file()`, which would follow it. Without this,
   `x -> ~/.ssh/id_ed25519` would have its TARGET's bytes copied into a box home.
4. **Destination-leaf symlink** (`:461`) — a destination whose real path escapes the subtree through
   a symlinked intermediate directory, where the escape is the leaf itself and the parent check above
   therefore cannot see it.

⚑ **`launch/templates.stage_layers` (`templates.py:187`) raises it too, at `:229`** — the older
docstring said the error came from "the one shared copier" and that was FALSE. The second raise is
deliberate and the staging code says why: a symlink in a staged layer would already have been
resolved into the staging dir as a PLAIN FILE by the time the shared copier looked at it, so the
copier's own symlink defence could never fire. Staging has to refuse it where the link is still a
link.

`commands/workset_cmd.py:359` is the only in-tree catcher.

## Plain leaf errors

```class ProjectError(KanibakoError):```
Project cannot be resolved, or its name/location is refused.

⚑ Broader than the older *"path does not exist or cannot be resolved"*. Several raise sites are
REFUSALS of a perfectly resolvable project: `paths.py:970` (name already used), `:974` (the directory
IS a workset), `:966` (registration under HOME). `project/names.py` adds the ambiguous-name and
unknown-name refusals.

```class ContainerError(KanibakoError):```
Container runtime or image operation failed.

39 sites across the runtime and image layers; the descriptor is accurate and nothing was displaced.

```class ArchiveError(KanibakoError):```
Archive creation, extraction, or validation failed.

⚑⚑ **DEAD AS OF THIS PASS — it has no raiser, no catcher and no importer anywhere in `src/`,
`tests/` or `packages/`.** `commands/archive.py` raises and catches `GitError` instead. The source
descriptor now says `(no in-tree raiser)` so the next reader is not misled into thinking the archive
path signals through it. It was left DECLARED rather than deleted: this is a prose pass, and the name
is public API in a package whose plugin wheels depend on `kanibako-cli` with no upper bound. Deleting
it is a code decision for someone else.

```class GitError(KanibakoError):```
Git check failed (uncommitted changes, unpushed commits, etc.).

Exactly two raise sites, both in `git.py` (`:34`, `:74`), and they are exactly the two the descriptor
names. Caught in `commands/archive.py` (`:80`, `:87`).

```class WorksetError(KanibakoError):```
Workset creation, loading, or manipulation failed.

26 raise sites; nothing displaced.

```class LegacyWorksetIdentityError(WorksetError):```
A workset root's `settings.yaml` still carries a RETIRED workset identity table — either the
`workset.meta` spelling (1.6.0/1.7.x) or the `meta.workset` one (the unreleased 1.8.0 tree). A 1.8.0
workset has no identity table anywhere under its root; it is named by the global registry.

One raise site, `project/workset.py:refuse_retired_workset_identity`, reached from `_load_workset`
and from `settings/paths.py`'s ancestor walk. ⚑ **The subclass exists so ONE blanket catch can
re-raise it:** `commands/box/_parser.py:_resolve_standalone_target` swallows every exception out of
`detect_project_mode` as "a non-project path is simply a miss", and an un-migrated workset root in
the ancestor walk is a named thing to fix, not a miss. Cure: MIGRATION.md §2.43.

```class LegacyRegistryIdentityError(WorksetError):```
A per-workset `registry.yaml` still carries a RETIRED `workset:` identity table or `projects:` map
(the unreleased 1.8.0 tree only). One raise site,
`project/workset_registry.py:_refuse_retired_registry_sections`, reached from `_load_raw` — the
module's ONE read seam, so every read of that file passes it. Cure: MIGRATION.md §2.43.

```class UserCancelled(KanibakoError):```
User cancelled an interactive prompt.

⚑ It is the ONE member with its own `cli.py` arm: `Aborted.` on stdout and **exit 2**, not the
generic `Error: …` / exit 1. `utils.confirm_prompt` raises it both when the answer is not literally
`yes` and on `EOFError` / `KeyboardInterrupt` at the prompt, so a piped-in non-answer cancels rather
than crashes.

```class SubjectConflictError(KanibakoError):```
A positional box subject and `--box` named DIFFERENT targets (§Design 8).

Raised in one place, `commands/flags.py:472`, under the general rule stated at `flags.py:452`:
wherever a positional and `--box` coexist, both supplied with DIFFERENT strings is this error.

## The agent-resolution family

```class AgentResolutionError(KanibakoError):```
Agent could not be resolved for an agent-requiring command.

**The `str()` of the exception — and of every subclass — IS the user-facing message.** Callers do not
reformat it; they let it reach `cli.py`, which prints it verbatim behind `Error: `. That is why each
raise site in `settings/config.py` writes a multi-line message with an install command in it, and why
`commands/agent_cmd.py:602` and `commands/start.py:556` both note that the error "surfaces verbatim".
Reword one of these messages and you have reworded user-facing output.

All three subclasses are raised from one function, `settings/config.py`'s `resolve_agent`.

⚑ The count rule that picks between the two gates runs on `real_installed = installed -
_PSEUDO_AGENTS` (`config.py:597`). **Pseudo targets — `no_agent` — do not count toward it.** An
explicitly NAMED harness is still validated against the FULL `installed` set, which is why the
not-installed case below reads a different set from the two count cases.

```class NoAgentSelectedError(AgentResolutionError):```
Gate-2a: 2+ REAL agents installed but none was chosen (no default).

`config.py:624`. The fall-through case: nothing resolved a name and the real-agent count is 2 or
more. The message prescribes `kanibako setup` or `kanibako shell`.

```class NoAgentInstalledError(AgentResolutionError):```
Gate-2b: zero REAL agent plugins are installed.

`config.py:619`. ⚑ The older descriptor said "zero agent plugins", which misreads a box that HAS the
pseudo `no_agent` target as having one — the count rule excludes it, so this gate fires there. Test
`tests/test_agent_resolution.py:151` pins exactly that case.

```class AgentNotInstalledError(AgentResolutionError):```
A name resolved (explicit `--agent`, cascade, or default) but that agent adapter is not installed.

`config.py:609`. ⚑ The older descriptor said "(cascade/default)" and omitted the commonest trigger:
`resolve_agent` reads `explicit_agent` FIRST (`config.py:600`), so a typo'd `--agent` lands here. What
must be installed is the HARNESS (`harness_of(node)`), never the composite node-name — a persona
segment is free-form, so `navigator+claude` requires `claude` to be installed and asks nothing of
`navigator`.
