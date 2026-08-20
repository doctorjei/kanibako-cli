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

`agent_settings_path(agents_root, agent_id)` is `@meta.agent.<agent>.settings`.

The per-agent SETTINGS cascade file lives **inside** the per-agent store dir
(`@meta.agent.<agent>.path` = `agents/<agent>/`) as `settings.yaml`. It is NOT the old sibling
`agents/<agent>.yaml` file (decision D-2026-06-22). The layout parallels the per-agent template dir
`agents/<agent>/template` and the per-category store dirs `agents/<agent>/{common,caches,seeded}/`.

## The per-agent HOST LAYOUT of the abstract categories (spec §2a)

`AGENT_CATEGORY_DIRNAME` is THE single source of the agent-store category layout. Both consumers read
it from here — the declaration-time ref builder (`agent_defaults.load_common`) and the persona symlink
shim (`commands.start.ensure_persona_share_symlinks`) — so the dirname is spelled ONCE and the two
cannot drift (design principle P10: no duplicated shared data).

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

### `agent_category_root` and `agent_category_root_ref` — the resolved twin and the stored ref

`agent_category_root(agents_root, agent, category)` is the REAL host dir:
`<agents_root>/<agent>/<dirname>`. Used where a caller needs an actual `pathlib.Path` — the persona
shim — and never to build a stored value.

`agent_category_root_ref(agent, category)` is the self-resolving `@`-ref:
`@meta.agent.<agent>.path/<dirname>`. This is the AGENT row of the spec's DECLARATION-ROOT table
(§2a), read from the single copy of that table in
`kanibako.settings.settings_categories.DECLARATION_ROOT_REF`. This is what a loader STORES, so the
stored value resolves on its own with no layer prepending anything later.

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
