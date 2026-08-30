# Agent Identity — the `AgentConfig` record, the agent store's paths, and category roots

`agent_config` holds what a caller knows about an agent **independently of any file**: the
`AgentConfig` record itself, where the per-agent store lives on the host, and where an abstract
category's sources root. It is the `meta.agent.<agent>.settings` route — paths and a value object,
not I/O.

⚑ **The FILE's shape is not here.** Reading, writing and addressing the per-agent settings file
belong to `kanibako.settings.agent_file`, which is the one module that spells the file's root table
(spec §15-21, `self`). Anything about *how the YAML is laid out* is that module's question, not this
one's.

## Naming

The module is called `agent_config`, not `agents`, to avoid clashing with the `kanibako.agents`
entry-point registry of agent adapters. That warning lives at its own site — the `NB:` comment above
`entry_points(group="kanibako.agents")` in `src/kanibako/targets/__init__.py`, which ends *"Do not
'unify'."* Read it there rather than acting on this pointer alone.

## The `AgentConfig` record

A per-agent configuration as loaded from an agent YAML file. The record models the parts a launch
invocation needs directly and carries the rest opaquely.

### `name`, `run_args`, `state` — the `[agent]` section

`IDENTITY_KEYS` (`name`, `run_args`) are the keys that live directly in the `[agent]` section as
agent **identity**. `state` holds the agent-state knobs beside them — `model`, `access`,
`allow_helpers`, `endpoint`, and so on. Since the S2 flatten, all of them sit FLAT under the file's
root, beside the category tables, because `self` IS `agent.<node>`: there is no per-node sub-table
to nest them in.

### `env` — the ENV category

`agent.<node>.env.<VAR>`, stored flat under `self` for the same reason `secret_path` is.

⚑ **It is NOT a launch-invocation input and not a delivery route.** `_agent_partial` re-roots the
table into the cascade, and the variable reaches the box through the collapse's arbitrated env slots
like every other scope's (MBR-1 P3).

What the field is FOR is the READ side of the `agent` verbs: `agent info` and `agent show` render it
(`commands/agent_cmd.py`, around `:223` and `:531`), and `agent get <node> env.<VAR>` returns it
(around `:489`).

⚑ It is NOT needed to preserve a user's `agent set`. That verb writes through `write_nested_key` and
never builds an `AgentConfig`, and every `agent_file.save` caller persists a FRESHLY GENERATED config
(first-use only) — so there is no read-modify-write round trip for a value to fall out of.

### `secret_path` — the SECRET category

Spec §2a, added 2026-07-06; RENAMED from the rc0–rc2 `env_file`. A mapping of VAR to a host PATH
pointing at secret material — for example a 0600 bearer-token file.

Stored DISCRIMINATED under `agent.<node>.secret_path.<VAR>`: the same first-class category shape that
`config set agent.<node>.secret_path.<VAR>` writes and that `_agent_partial` reads into the launch
cascade. That is what makes it resolve through `system → workset → box → agent` precedence.

The value is a PATH only. At launch the file is ro-bind-mounted arm's-length and exported IN-BOX;
kanibako NEVER reads the secret VALUE — it appears in no snapshot, no keystore, no log, and no argv.

🛑 **A VAR's value is THREE-STATE** (ruling of 2026-08-17). The VAR is either:

* **ABSENT** from the dict — never configured;
* mapped to `None` — **PRESENT-null**, written by `--null` or by a hand-edit, meaning this endpoint
  is deliberately KEYLESS;
* mapped to a path `str`.

⚑ Test membership (`var in cfg.secret_path`) before reading a value. `.get(var)` alone cannot tell
ABSENT from deliberately-keyless: both are falsy/`None`, and they mean different things.

### `category_tables` — the tables this record does not model

The category tables that get no field of their own: `bindings` (the `{ro, rw}` pair, carried whole),
`caches`, `seeded`, `common`, `synced`, `masks`. All flat under the file's root since the S2 flatten,
again because `self` IS `agent.<node>`.

They are not modelled as fields because they ride `_agent_partial` into the launch **cascade**, not
into the launch invocation. They are carried OPAQUELY through the load → write round trip.

⚑ **That round trip has no live producer, MEASURED.** All four `agent_file.save` callers persist a
freshly generated config: both `commands/start.py` sites gate on `agent_cfg_dirty`, which is
first-use-only, and both `cli.py` sites build the config inline. Nothing today loads this file, edits
it and writes it back. The opaque carry protects a shape no caller currently exercises — a guard,
not a running guarantee. That is a reason to keep it, not a reason to trust it.

## Where the store lives

`agents_dir(data_path, paths_agents="agents")` returns the agents directory under a data path, and
`agent_config_path(data_path, agent_id, paths_agents="agents")` is a convenience wrapper for callers
that hold a *data_path* rather than a resolved agents root — it just delegates to
`agent_settings_path`.

`store_dirname(node)` is THE one place a node becomes a DIRECTORY, and it is the reason no other
module has to know the two spellings differ. A persona node is `navigator℘claude` inside a key —
`℘` exists solely so the node is spellable in a dot-separated key path (`agent_ref`) — but a store
directory is not a key, so it carries the `+` the user typed: `agents/navigator+claude/`. The
function DELEGATES to `agent_ref.display_agent_ref` rather than repeating the substitution. Five
independent compositions of the store path go through it: `agent_settings_path`,
`settings_launch.meta_agent_path_floor`'s VALUE,
`core_defaults.canon_default_categories`' value + store probe, `launch.templates.
template_seed_defaults`' VALUE, and EVERY link `commands.start.
ensure_persona_share_symlinks` lays. ⚑ Miss one and the store SPLITS with no error raised anywhere —
every path is create-if-absent, so the box gets two half-stores; each site is mutation-guarded by a
literal-spelling assertion in its own test file.

`agent_settings_path(agents_root, agent_id)` is `@meta.agent.<agent>.settings`. Callers pass the
CANONICAL node; the `+` dirname is applied inside.

The per-agent SETTINGS cascade file lives **inside** the per-agent store dir
(`@meta.agent.<agent>.path` = `agents/<agent>/`) as `agent.yaml`. It is NOT the old sibling
`agents/<agent>.yaml` file (decision D-2026-06-22). The layout parallels the per-agent template dir
`agents/<agent>/template` and the per-category store dirs `agents/<agent>/{common,caches,seeded}/`.

## The per-agent HOST LAYOUT of the abstract categories (spec §2a)

`AGENT_CATEGORY_DIRNAME` is THE single source of the agent-store category layout, read by the
declaration-time ref builder (`agent_defaults.load_common`), so the dirname is spelled ONCE (design
principle P10: no duplicated shared data).

⚑ **THE PERSONA SHIM IS NO LONGER A CONSUMER.** It re-roots WHOLE store-relative paths
(`agent_representation.harness_store_leaf`), which is generic over categories and over anything
else a plugin names, so it composes `agents/<store_dirname>/<leaf>` directly and never asks what a
category's dirname is. The two still land on ONE directory, because `@meta.agent.<a>.path` IS
`@config.agents/<store_dirname>` (`settings_launch.meta_agent_path_floor`).

⚑ `agent_category_root` — the resolved twin of `agent_category_root_ref` — lost its last production
caller with that change and is exercised only by `TestLayoutSingleSource`.

It maps a category to the FIXED sub-dirname under the per-agent store root, and it is DERIVED from
`ABSTRACT_CATEGORIES` rather than re-typed: the dirname IS the category name, so spelling the three
names a second time would be two copies of one fact.

⚑ **"The root dirname is FIXED MACHINERY, not a key"** (spec §2a). It is not user-settable, because
every reasonable want is served better by an absolute `bindings` entry or by moving the scope root
(`config.agents`) — both of which ARE keys.

### `agent_category_dirname(category)`

An undeclared category is not one of the abstract three and is REFUSED rather than silently taking a
bare root — the closed-keyspace rule, spec §0. The concrete `bindings.{ro,rw}` categories take NO
root at any scope (§2a), so asking for their dirname is a caller bug, and the `ValueError` says so
and names the declared set.

### `agent_category_root_ref` — the stored ref (and the twin that is GONE)

`agent_category_root_ref(agent, category)` is the self-resolving `@`-ref:
`@meta.agent.<agent>.path/<dirname>`. This is the AGENT row of the spec's DECLARATION-ROOT table
(§2a), read from the single copy of that table in
`kanibako.settings.settings_categories.DECLARATION_ROOT_REF`. This is what a loader STORES, so the
stored value resolves on its own with no layer prepending anything later.

⚑ **`agent_category_root` — the RESOLVED twin — is GONE**, and the module carries a tombstone
saying why. Its one consumer was the persona symlink shim, which stopped asking "where does this
CATEGORY store?" when the re-root went generic: it carries a WHOLE store-relative path
(`common/plugins`, `seedsrc`) that names no category at all. A resolved store path is what
`@meta.agent.<a>.path` already resolves to (`settings_launch.meta_agent_path_floor` defines the
anchor as `@config.agents/<store_dirname>`), so a second composer beside it would be a second answer
to one layout question — free to drift, and silently, because every path here is create-if-absent.
A caller needing a real `Path` composes `agents_root / store_dirname(node) / <rel>`; the
`store_dirname` call IS the shared fact, and it is the one that must not be re-spelled.
`TestLayoutSingleSource` moved with the subject: it now pins the ref builder against
`meta_agent_path_floor`, two producers that are both still live.

## Declaration-time rooting

`root_relative_source(src, root_ref)` is THE declaration-time rooting, implemented ONCE (spec §2a): a
self-resolving source is emitted VERBATIM, and a bare relative leaf becomes `<root_ref>/<src>`.

⚑ An ABSOLUTE (or `~` / `$var` / `@`-ref) source in an abstract category is LEGAL and is NOT
root-joined. The root is a **default for relative sources, not a universal law** (spec §2a; the
spec's own `caches.transform` worked example is an `@system.cache`-rooted identity mount).

It is applied ONLY by the ABSTRACT-category declaration loaders. `bindings.{ro,rw}` take no root at
any scope, so a relative source there is a DEFECT rather than a shorthand, and is refused where it is
declared.

### `is_self_resolving(src)` and the escape cases

`_SELF_RESOLVING_TOKENS` is the tuple of TOKEN prefixes that make a `host_src` resolve on its own when
UNESCAPED (spec §2a): `~`, `$`, `@`. A leading `/` is handled separately.

`is_self_resolving` is true iff *src* is ABSOLUTE, or begins with an unescaped `~` / `$` / `@`.
Anything else is a BARE RELATIVE leaf: meaningful only under a root, and a DEFECT wherever no root
exists.

⚑ **Escapes are read the way the RESOLVER reads them, and the two leading-escape cases fall on
OPPOSITE sides.** This is why the test cannot be a plain first-character check:

* `\/foo` unescapes to `/foo`, which is ABSOLUTE. The retired post-expand join never joined it — it
  tested the *unescaped* string for a leading `/` — so calling it relative here would DIVERGE from
  the behaviour this phase preserves.
* `\~foo` unescapes to the literal `~foo`, a plain relative dir that merely starts with a tilde, NOT
  a home reference. The retired join did not join that one either, but only because `~foo` expands
  home-ward before the test; the answer (leave it alone, treat as relative) matches anyway.

So a leading `/` is tested AFTER unescaping, while the token prefixes count only when they are NOT
escaped. That asymmetry is the whole content of the function.

## Stored path values — [R147]'s predicate and its refusal

A path key's STORED value must say ON ITS OWN where it points ([R147], 2026-08-29): a bare relative
is AMBIGUOUS and is REFUSED rather than anchored. `is_unambiguous_path_value` is that test and
`ambiguous_path_value_error` is the wording every seam refuses with. Both live here so the rule has
ONE predicate and ONE message wherever it fires: `config_interface._bare_relative_path_error` at set
time,
`paths._refuse_bare_relative` on the Layer-1/Layer-2 read, and
`workset_dirkeys.resolve_workset_dir_key` on the workset dir keys.

### `is_unambiguous_path_value(value)`

True for an absolute path, `~`, an `@`-ref, or a `$XDG_*` variable. Anything else — a bare leaf,
`./x`, `../x` — is ambiguous.

⚑⚑ **THE TEST IS ON THE STORED SPELLING, BEFORE EXPANSION, and that is the load-bearing part.**
[R147] rules on what is a legal value to have WRITTEN, so `$XDG_DATA_HOME/kanibako` is legal even in
an environment where that variable answers something odd. Testing the EXPANDED value would refuse it
there — reporting a KEY defect for an ENVIRONMENT one, with a "did you mean" line that pastes the
token back into itself. A SOURCE that expands to a relative path is a different rule at a different
layer (`settings_expand._refuse_relative_host_src`), with its own message; the two are not one check
and must not be merged.

⚑ **It is NARROWER than `is_self_resolving`, on purpose, and the difference is `$VAR`.** That
predicate rules on a BIND SOURCE, where a declaration may name any variable the launch namespace
supplies. This one rules on a path a USER typed, and the keyspace's non-XDG variables (`$AGENT`,
`$WORKSET`) expand to a bare NAME — so `$AGENT/logs` is exactly as relative as `logs`. A `$` is
therefore PARSED (`settings_resolve.match_var`) and the variable's own name tested for the `XDG_`
prefix; a malformed `$…` answers False rather than raising. The leading-escape cases fall the way
they do above: `\/foo` unescapes to an absolute path, `\~foo` to a plain relative dir.

### `ambiguous_path_value_error(key, value, ...)`

⚑ **Naming BOTH readings is the rule, not decoration.** The user had two plausible meanings that
land in DIFFERENT directories, and a message that only said "be absolute" would move the guess onto
the user instead of removing it. The text therefore prints the *anchor* reading and the
run-from-directory reading as two resolved paths, one per line, before naming the legal forms.

*anchor* is the RESOLVED other candidate; *anchor_ref* is that root's legal spelling
(`@meta.workset.path`, `$XDG_DATA_HOME`), offered so the user can paste the fix; *where* names the
file the value was read from, when the seam has one. ⚑ An UNRESOLVABLE anchor is passed as its own
ref spelling with *anchor_ref* left unset — the reading is still named, and in a pasteable form,
without the line saying the same thing twice.

`anchor_label` introduces that reading, and the two constants are NOT interchangeable.
`DEFAULT_ROOT_LABEL` (*"this key's default root"*) is for a key whose own declared default sits
under the anchor: every Layer-1/Layer-2 path key and every workset dir key. `DECLARATION_ROOT_LABEL`
(*"this key's scope root"*) is for a path key that declares no root of its own — `box.images_store`
(runtime-probed from podman) and the whole `secret_path.<VAR>` family — whose other reading is the
spec §2a DECLARATION ROOT and is introduced as one (`config_keys.path_key_anchor` picks between
them). ⚑ **The label is not decoration either:** calling a declaration root "this key's default
root" would tell the reader a default exists to fall back to, which is the single thing a message
about an unset, ambiguous value must not invent.

⚑ `secret_path.<VAR>` reaches this refusal at SET time only. At launch it is refused by
`settings_launch`'s own §2a SOURCE message, because it is the one path key with no declared default
— so there is no second candidate anchor for a two-readings message to name.

---

## Relocation pass, 2026-08-20

Source went from **79.1%** comment characters (9563/12088) to **59.4%** (3620/6092). Everything above
was MOVED out of `src/kanibako/settings/agent_config.py`, not deleted.

`prose-relocation-check.py`: **133 prose lines at HEAD, 120 removed, 0 scoring below 0.6** against
this document — no removed line is orphaned. `prose-pass-check.py`: AST identical with docstrings
stripped, **10 docstring-bearing symbols, added=[] removed=[]**, string-literal multiset identical.

**Kept in source under the keep test** — deleting any of these would let a future edit break
something silently at that exact line:

* the module docstring's `⚑` pointing the file's shape at `agent_file` (the obvious next edit is to
  start spelling the root table here);
* the three-state `secret_path` note, shortened to the membership test (`.get()` alone reads as
  correct);
* `env` is the READ side of the `agent` verbs, not a delivery route (the obvious mistake is wiring it
  into the launch invocation);
* `category_tables` is carried opaquely and has no live producer (the obvious mistake is deleting it
  as dead);
* `is_self_resolving`'s "not a plain first-char test" (the function looks exactly like a candidate
  for `src[0] in "~$@/"`);
* `root_relative_source`'s "absolute sources are legal and are NOT joined" (the obvious
  simplification is to join unconditionally);
* `AGENT_CATEGORY_DIRNAME`'s "derived, not re-typed" (the obvious edit is to write the three names
  out);
* `agent_settings_path`'s "not the old sibling `agents/<agent>.yaml`" and
  `agent_category_dirname`'s "an undeclared category is REFUSED".

**Deliberate drops — duplication only, no content lost.** Both survive above, so the relocation
check finds no orphan; recorded here because they were cut rather than relocated line-for-line:

* the S2-flatten rationale (flat under the file's root because `self` IS `agent.<node>`) was stated
  three times in the class docstring, once each for the `[agent]` section, `env` and
  `category_tables`. One carrier now, in the `env` and `category_tables` sections above.
* `agent_config_path`'s "Return the path to an agent's config (settings) file", which its own name
  and its surviving one-line docstring already say.
