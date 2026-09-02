# Settings Expression Resolution — precedence, expansion, and the box-layout contract

This is the pure bottom of the settings stack. It holds the grammar every settings value is written
in — `$VAR` / `~` / `@`-ref expansion, the escape rules, the structured-bind unpackers, the
destination canonicalizer — plus the two-pass precedence walk and the fixed box-layout constants
that both sides of the host/guest boundary have to agree on. Nothing here touches a file, a
container, or a settings module; that is what lets everything else import it.

**Authority:** `specs/settings-keyspace-1.8.0.md` — §0 (files store UNRESOLVED; the two layers),
§1A (the staged bootstrap order and the CLI level), §2 (the cascade bracket), §2a (the category
list, dest-keying, per-entry options). ⚑ **The spec is the LIVE authority; read it first.**

---

## ⚠️ Claims this pass DROPPED as false — do not restore them from git history

The module docstring and its constant comments were the oldest prose in the file and four
assertions in them had gone false. They are recorded here so a future reader who finds them in an
old revision knows they were retired deliberately, not lost.

1. **"This module has no in-tree imports beyond `kanibako.errors`."** FALSE since the `@`-ref
   grammar was composed from the agent-side charset: the module also imports
   `kanibako.agent_ref` (`CANONICAL_SEP`, `SEGMENT_CHAR_CLASS`). The **conclusion** the sentence
   was defending still holds and is restated below under "The import-direction invariant" — but
   it now has to be argued from two edges, not one.
2. **"Every box-side `/home/agent` literal in the tree derives from this constant."** FALSE. It
   reads as an enforced invariant and nothing enforces it. See "The box-layout contract
   constants" for the measured exceptions.
3. **`split_bind` "remains the parser for the CLI-INPUT edge (`config set k=h:b[:opts]`)."**
   FALSE twice over: the bind-shaped `config set` write route is RETIRED
   (`config_keys._bind_route_retired_message` and its two callers refuse it), and
   `settings_configset` does not import `split_bind` at all. Its real consumer is named under
   `split_bind` below.
   ⚑ **This one had already SPREAD**, which is the reason to record it rather than just fix it:
   the same wrong consumer is asserted in `tests/test_settings/test_settings_resolve.py` (the
   section banner above the `split_bind` tests) and in `commands/workset_cmd.run_share_add`'s own
   comment, which calls it "the same parser `config set` and the resolver use". **Both are
   outside this module and were left alone — fixing them is a separate, boarded change.** A
   reader who checks either one will find the false claim corroborated.
4. **The module docstring's cascade framing** — that `resolve_value` walks
   `[box, workset, agent, system]` as the settings cascade. FALSE for the current tree: the
   launch/settings cascade goes through `settings_merge.merge` now, and `resolve_value`'s live
   role is narrower. See "What `resolve_value` is still for".

Two further notes were stale rather than false and were compressed rather than dropped: the
`defer_env` flag's "existing callers are byte-for-byte unaffected" (a migration-moment claim about
an unnameable "before"), and `LevelView` / `ResolvedValue`'s "the structural unpacking of those
leaves lands ... in a later phase" (it landed — `settings_assemble` calls both unpackers today).

---

## What this module actually owns

Four things, and the first line of the old docstring named only two of them:

* **The expression grammar** — `$VAR` / `${VAR}`, bare `@a.b.c` and braced `@{a.b.c}`, a leading
  `~`, and the backslash escape rules. One parser per token family, shared out to every other
  module that needs to recognise the same token.
* **The precedence walk** — `resolve_value` and its three-valued result.
* **The bind value shapes** — `split_bind` (the colon form), `unpack_bind` and `unpack_bind_entry`
  (the structured forms), and `normalize_bind_dest` (destination canonicalization, R-11).
* **The box-layout contract** — `GUEST_HOME`, `GUEST_UID`, `GUEST_GID`,
  `BOX_PINNED_ROOT_RELPATH`, `BOX_PINNED_STATE_RELPATH`. These are pure machinery, NOT settings
  keys, and they live here precisely because this is the module everything can import.

It is format-agnostic and operates only on already-parsed data: the caller parses YAML into the
simple mappings and lists this module consumes. (The old prose said "TOML/YAML" — kanibako's
config files are all YAML.) It performs no file I/O, no mounting, and holds no global mutable
state — the module-level names are a string, three ints, two compiled regexes, and the `UNSET`
sentinel, which is `__slots__`-empty and therefore immutable.

## The import-direction invariant — what keeps this module extractable

⚑ **This module imports NOTHING from `settings/` — in particular not `config.py` and not
`paths.py` — and that is a rule, not an accident.** It is the bottom of the dependency order:
`paths`, `config`, `settings_expand`, `settings_configset`, `settings_assemble`,
`settings_categories`, `store_collapse` and `settings_launch` all import IT. An import in the
other direction would be a cycle, and because every `settings/__init__.py` is deliberately
import-free there is no facade to hide it behind — it would fail loudly at import time, in a
place far from whoever added it. **The rule needs to stay visible AT the import block**, which is
why a one-line `⚑` sits there and only the reasoning lives here.

Its two in-tree edges are `kanibako.errors` (which imports nothing at all) and
`kanibako.agent_ref` (which imports only `re` and `kanibako.errors`). The transitive closure is
therefore those two modules and the standard library — nothing else. **That is what makes it safe
to import `GUEST_HOME` from anywhere, including the agent plugin packages, without
circular-import risk.** The property is worth restating whenever a new import is proposed here: a
third edge is not automatically fine, it has to be checked the same way.

## The three-valued model: `unset` ≠ `""` ≠ set

This is the single idea a future reader most easily loses, because two of the three states are
spelled with things that look like emptiness.

| state | what produced it | what comes back |
|---|---|---|
| **set** | the key appears in some level's `values` with a non-`""` value | `ResolvedValue(value=…, terminal=False)` |
| **terminal suppression** | the key appears in some level's `values` as an explicit `""` | `ResolvedValue(value="", terminal=True)` — a REAL result |
| **unset** | the key appears in no level's `values` and no level's `defaults` | the `UNSET` sentinel |

**An explicit `""` is a SUPPRESSION and it is TERMINAL.** It wins at its level and does NOT fall
through to a less-specific default. This is the whole point: `""` is how a user says "I do not
want this thing", and falling through to a default would resurrect precisely what they
suppressed. The spec keeps this idiom live and distinguishes it by name from its two neighbours —
present-`None` (the tri-state omit) and the COPY-disable sentinel — and warns that no layer may
reinterpret one as another (§2h, the pref layer's "MUST NOT interpret emptiness AT ALL").

⚑ `UNSET` is a sentinel OBJECT, not `None` and not `""`. `_Unset` exists as a type so that
`repr()` prints `UNSET` in a traceback rather than something that reads like a missing value, and
it is `__slots__ = ()` so nothing can attach state to the singleton. Test it with `is`, and
narrow it out before touching `.value`.

⚑ The emptiness test is `val == ""`, not `val is ""` and not `not val`. That matters for the
structured leaves: an empty list `[]` is falsey but does NOT equal `""`, so a structured category
value can never be mistaken for a terminal suppression.

### Set beats default AT ANY LEVEL — and it is the two-pass shape that says so

`resolve_value` makes **two** full passes over the level list, not one pass consulting `values`
then `defaults` per level:

1. every level's `values`, most-specific first;
2. only then, every level's `defaults`, most-specific first.

So a value set at the LEAST specific level still beats a default declared at the MOST specific
one. A single fused pass would silently invert that, and it is the kind of edit that looks like a
tidy-up. **The two `# Pass 1` / `# Pass 2` comments in the source are load-bearing markers, not
decoration.**

## Expansion is a SEPARATE step, and it happens in a named SPACE

`resolve_value` returns the **raw winning literal** — `@`-refs, `$vars` and `~` all intact. It
does not expand. The caller takes that literal and hands it to `expand_expr` along with a
**space**, and the space is what decides what `~` means:

* `space="host"` → `~` expands to `ctx.host_home`, the invoking user's home on this machine;
* `space="guest"` → `~` expands to `GUEST_HOME`, which is the same on every machine forever.

⚑ **Keeping resolution and expansion apart is the reason one stored value can serve both sides.**
A settings file stores `~/x` UNRESOLVED (§0); the same entry becomes `/home/jei/x` when it names
a host source and `/home/agent/x` when it names a box destination. Fusing the two steps would
force the file to commit to one machine's answer at write time.

There is a third mode, reached by `defer_env=True`, in which the ENVIRONMENT tokens are not
expanded in EITHER space but re-emitted verbatim for a resolver that runs later, in a different
environment. See `expand_expr` below.

## The box-layout contract constants

`GUEST_HOME`, `GUEST_UID` and `GUEST_GID` are one contract with the images: every kanibako image
creates the `agent` user with uid/gid 1000 and home `/home/agent`. They are **pure machinery, not
settings keys** — the images hardwire agent=1000, so no configuration could meaningfully differ,
and the `--userns=keep-id:uid=…,gid=…` mapping (`runtime/container.py`, `KEEP_ID_USERNS`) is
built from the two ids so the calling host user always lands on the in-box agent user.

⚑ **`GUEST_HOME` is the single source of truth for the box-side home, but it is NOT enforced, and
the old comment claiming otherwise was false.** Measured in `src/` on 2026-08-30: ONE `/home/agent`
literal sits in an executable Python path that does not go through the constant —
`commands/start.py` (the `kanibako-entry` mount destination). `channels/helper_listener.py`, named
here before, no longer does: its `helper-init.sh` entrypoint path derives from `GUEST_HOME`. Two
further Python occurrences are `--help` EXAMPLE lines in `commands/workset_cmd.py`, and **seven are
`WORKDIR` / `ENV PATH` lines across the five bundled `containers/Containerfile.template-*` files,
which cannot import a Python constant at all.** The remaining occurrences are prose. **Treat
"derives from `GUEST_HOME`" as the standard to hold new code to, never as a description of the tree
as it stands.**

### The GUEST workspace and vault leaves

`GUEST_WORKSPACE`, `GUEST_VAULT_RO` and `GUEST_VAULT_RW` name the box-side workspace and vault
destinations; `GUEST_WORKSPACE_RELPATH`, `GUEST_VAULT_RELPATH`, `GUEST_VAULT_RO_RELPATH` and
`GUEST_VAULT_RW_RELPATH` are the same leaves without the `GUEST_HOME` prefix. **Both forms exist
because both are needed**: a box-side dest is compared as an absolute string, while the mount-stub
pre-creation in `runtime/container.py` joins the leaf onto a HOST `Path` (`shell_path / …`) and
cannot use an absolute one. Seven names, not three, and that is the price of the pair.

⚑ **These are GUEST constants and they are INDEPENDENT of the host leaves.**
`settings/bootstrap.WORKSPACE_PATH` is a HOST leaf spelled `"workspace"` too, and nothing but
convention ever kept the two apart. This module may import NOTHING from `settings/` (see the top of
the file), so a guest constant *cannot* be expressed in terms of a host one — the independence is
now structural rather than accidental.

⚑ **What this step does NOT buy, despite the obvious guess:** a host-leaf rename could not already
move a guest mount dest. Neither `runtime/container.py` nor `commands/code_cmd.py` imports anything
from `bootstrap`, so the two namespaces were already disjoint in fact. What changes is that
they are now disjoint by CONSTRUCTION, and that three of the five residual false positives fall out
of the future path tripwire.

🛑 **The VALUES are declared key names in the closed keyspace** — `box.bindings.rw[~/workspace]`,
`box.bindings.ro[~/vault/ro]` and `box.bindings.rw[~/vault/rw]`, whose `~` `canonicalize_dest`
expands to `GUEST_HOME`. Respelling one redeclares three keys and hard-errors every existing user
`box.yaml` that carries `~/vault/rw:`. Naming them changed nothing; changing them is a migration.

⚑ **ONE PYTHON CARRIER IS NOT ONE CARRIER.** The five bundled
`containers/Containerfile.template-{jvm,dotnet,systems,js,android}` files each hardwire
`WORKDIR /home/agent/workspace` and cannot be collapsed into these constants at all. A reader who
takes "single source of truth" literally here will be wrong about the images.

⚑ **The two agent-plugin sites are deliberately NOT converted.**
`packages/agent-claude/…/target.py` and `packages/agent-codex/…/target.py` still compose
`f"{GUEST_HOME}/workspace"` through a function-local import. Plugins pin `kanibako-cli` with no
upper bound, so a new plugin on an old core would raise `ImportError` mid-launch — a late failure
bought for no additional guarantee, since `GUEST_HOME` is already the shared carrier there.

⚑ **The workspace PREFIX comparisons keep a trailing slash** (`GUEST_WORKSPACE + "/"`, twice in
`runtime/container.py`). That slash is the separator guard the keyspec requires — `~/foobar` is not
inside `~/foo` — and dropping it also strands the bare workspace dest on the workspace branch as
`project_path / ""`. Both sites are covered by named regression tests in
`tests/test_runtime/test_container.py`; a substitution that only checks the constant's NAME will
not notice.

### `BOX_PINNED_ROOT_RELPATH` — the resolve-before-liveness compromise

The FIXED box-side root for state that CANNOT resolve without a live box but MUST be placed
before the box is live. A mount destination goes into the container runtime's argument list
before anything is running, and a copy category runs at `create` with no container at all — so a
destination containing a `$XDG_*` token would have to be resolved by the HOST, guessing at what
the box would say. That guess was maintained by hand across four separate resolvers and they were
already out of agreement (`start.py` derived the helper dest from `$XDG_STATE_HOME` while
hardcoding the matching `mkdir` at `~/.local/state`). One fixed root deletes the whole class: it
resolves identically on both sides, always. XDG compliance is restored PROPERLY after boot, by
projecting this root onto the box's real XDG locations
(`kanibako.box_supervisor.project_pinned_xdg`) rather than by four resolvers agreeing.

⚑ **HOME-RELATIVE by design.** The three consumers anchor it differently — the box's own `$HOME`
in-box, the box-home BIND SOURCE host-side (`proj.shell_path`), and a `~` expression in the
defaults data — so a leading `~` or an absolute `/home/agent` would be wrong for two of the
three.

⚑ **NARROW.** This is a compromise for the resolve-before-liveness class ONLY. The HOST-side
roots (`config.data`, `system.cache`, `system.runtime`) resolve eagerly with a real environment
to read and do NOT belong here.

`BOX_PINNED_STATE_RELPATH` is the STATE facet of that root — the pinned counterpart of
`$XDG_STATE_HOME/kanibako`. **Further facets (cache, runtime, …) become further names here, never
a second mechanism.**

## The `@`-ref name grammar is COMPOSED, never restated

`_REF_SEG` must admit every character an agent NODE-NAME can contain, because a node-name is a key
segment (`meta.agent.<node>.…`, `agent.<node>.…`). So it is **composed from** the agent-side
charset rather than restating it: `agent_ref.SEGMENT_CHAR_CLASS` is exactly what
`agent_ref._is_segment_safe` admits in one half of `<persona>℘<harness>`, and `_REF_SEG` is that
class plus the `℘` that joins the halves. **The subset relation therefore holds BY
CONSTRUCTION.**

⚑ That composition is the point. These were two independent literals and they **drifted TWICE** —
first missing `℘`, then missing `-` — each time truncating the name mid-segment and leaving the
rest as a literal suffix. `@meta.agent.kimi-k3℘claude.auth.share_support` parsed as the absent
name `meta.agent.kimi` (rendered `""`) plus the literal `-k3℘claude.auth.share_support`, which
then fed `as_bool` and crashed every such launch with "expected bool, got str" — and at the
`@meta.agent.<a>.path` sites it silently produced a garbage path instead. A third drift
(non-ASCII personas, e.g. `漢字℘claude`) was latent until 2026-08-04.

`\w`, inside `SEGMENT_CHAR_CLASS`, is what admits letters and digits in any language. It is still
a NARROW class, not a catch-all: a broad `[^\s@]+` is **REJECTED** because it would swallow `/`
and break embedded `@config.data/...` path refs. `.` is absent from BOTH sides — it is this
grammar's own segment separator, which is why an agent name may not contain one (`agent_ref`
rejects it at parse time).

---

## Classes

```python
class SettingsError(KanibakoError)
```
Raised on unknown variable, unresolvable/cyclic `@`-ref, or depth-cap.

Also the refusal for a malformed structured bind leaf — see `unpack_bind` / `unpack_bind_entry`.

```python
class _Unset
```
Sentinel type for "no value resolved" (distinct from an explicit `""`).

`UNSET` is its one instance. See "The three-valued model" above for why the distinction exists and
how to test for it.

```python
@dataclass(frozen=True)
class ResolveCtx:
    agent_name: str | None
    workset_name: str | None
    host_home: str
    xdg: dict[str, str]
    config: Mapping[str, str] = field(default_factory=dict)
```
Context for variable expansion.

*xdg* maps XDG variable names (e.g. `"XDG_DATA_HOME"`) to host paths. Every host-side `ResolveCtx`
gets its `xdg=` from one builder in `paths.py`, deliberately — an ad-hoc environment read here was
a real defect once.

*config* is the Layer-1 CONFIG-key FOUNDATION (spec §1): the resolved `config.*` bootstrap paths
keyed by their full dotted name (`config.data`, `config.settings`, `config.agents`,
`config.primary_workset`, `config.registry`). It is consulted by the `@config.*` ref route —
**config is NOT a settings cascade level** (spec §1A / JC-2), it is a foundation injected here
exactly the way `$XDG_*` is. Empty when no foundation is available (behavior-only contexts where
`@config.*` never appears).

⚑ The dataclass is frozen; do not mutate *xdg* / *config* in place. Frozen protects rebinding, not
the dicts themselves.

```python
@dataclass(frozen=True)
class LevelView:
    name: str
    values: Mapping[str, object]
    defaults: Mapping[str, object] = field(default_factory=dict)
```
A single precedence level's explicitly-set values and declared defaults.

*name* is the level name (e.g. `"box"`). *values* holds values the user explicitly set at this
level; *defaults* holds defaults declared at this level. The split into two mappings is what makes
the two-pass "set beats default at any level" rule expressible at all.

A value is typically a scalar `str` (a behavior setting, an `env` value), but for the
path-delivery CATEGORIES it may be a STRUCTURED leaf — a binding pair/tuple
`[host_src, box_dest[, opts]]`, a dest-keyed entry `[src[, opts]]`, or a `masks` list (spec §2a;
preserved at load by `settings.config.read_categories`). **Hence `object`, not `str`.**

```python
@dataclass(frozen=True)
class ResolvedValue:
    value: object
    level: str
    is_default: bool = False
    terminal: bool = False
```
A resolved (but not yet expanded) value plus its provenance.

*value* is the raw winning literal (`@`-refs / `$vars` / `~` intact). *level* names the level that
supplied it. *is_default* is True when the value came from a declared default rather than an
explicit set. *terminal* is True when the winning value was an explicit `""`.

*value* is `object` for the same reason `LevelView.values` is: a CATEGORY value may be a
structured leaf. Consumers that need a string narrow at their own site.

⚑ **`terminal=True` is not "empty", it is "suppressed".** A consumer that treats it as "no value"
and substitutes its own fallback has reintroduced exactly the fall-through the flag exists to
prevent.

---

## Functions

```python
_unescape(s: str) -> str
```
Resolve backslash escapes consistently.

A backslash before any character yields that character literally (`\:` → `:`, `\\` → `\`,
`\x` → `x`). A trailing lone backslash is kept literal — there is nothing for it to escape, and
raising there would make an unremarkable path illegal.

```python
split_bind(value: str) -> tuple[str, str | None]
```
Split `host_src:guest_dest` into its two halves at the FIRST UNESCAPED `:`.

Scans left-to-right; a backslash escapes the next character (so `\:` is a literal colon, `\\` a
literal backslash). With no unescaped colon, returns `(unescaped(value), None)` for a plain
scalar. Each returned half has its escapes resolved.

Linux container paths only — no Windows drive-letter / URI special-casing. Use `\:` to embed a
literal colon in either half.

⚑ **Its one live consumer is `workset share add` / `workset share rm`**
(`commands/workset_cmd.run_share_add`), which is a CLI edge whose grammar is exactly two fields —
so it calls `split_bind` a second time on the already-unescaped guest half to catch a stray third
field. **It is NOT the `config set` parser**, and the retired prose that said so should not come
back: the bind-shaped `config set` write route is closed by
`config_keys._bind_route_retired_message` and `settings_configset` does not import this function.
Storage and the category-load path are pure structured and use the unpackers below.

```python
unpack_bind(value: object) -> tuple[str, str, str | None]
```
Unpack a STRUCTURED **name-keyed** category binding leaf into `(host_src, box_dest, options)`.

The structural successor to `split_bind` on the CATEGORY path (spec §2a "REPRESENTATION"): a
`bindings.*` / `caches` / `seeded` / `common` / `synced` value is a STRUCTURED PAIR — a YAML list
or Python `tuple` — not a colon-joined `"host:box"` string.

* **2 elements** `[host_src, box_dest]` → `(host_src, box_dest, None)`; the caller falls back to
  the category-default mount options.
* **3 elements** `[host_src, box_dest, options]` → the explicit options string OVERRIDES the
  category default for this entry only (the spec's per-entry options channel — e.g. `"z"`, or
  `""` for a live socket, where any relabel would break the shared socket topology).

Each element is narrowed to `str` (a YAML scalar may parse as `int`). A non-list/tuple value, or
a list/tuple of the wrong arity, raises `SettingsError` — **the structured shape is load-bearing
and a malformed leaf is a configuration error, not something to silently re-derive.**

⚑ The category list above is the spec's (§0, line-76 enumeration:
`masks · bindings.ro · bindings.rw · caches · seeded · common · synced · env · secret_path`).
**The name is `common`. `shared` was renamed to it on 2026-07-29 and is no longer a key** — under
the closed-keyspace rule a stored `<scope>.shared.<name>` is an ERROR, not a silent accept. The
docstring here named the dead spelling until this pass.

```python
unpack_bind_entry(value: object) -> tuple[str, str | None]
```
Unpack a **DEST-KEYED** binding entry leaf into `(src, options)`.

The dest-keyed sibling of `unpack_bind` (disk-store rework R-3/R-6). Where a name-keyed arm stores
`{name: [host_src, box_dest[, opts]]}`, a dest-keyed arm stores `{box_dest: [src[, opts]]}` — the
destination is the mapping KEY, so the VALUE carries one element fewer:

* **1 element** `[src]` → `(src, None)`; caller falls back to the category-default options.
* **2 elements** `[src, options]` → the explicit options override this entry only.

Each element is narrowed to `str`. Wrong type or wrong arity raises `SettingsError`.

⚑ A BARE scalar (`{dest: src}`) is deliberately NOT accepted: the ruled shape is 1-or-2
ELEMENTS, the exact transposition of the 2-or-3 rule, and admitting a second spelling of the same
entry is precisely the duplicate-form confusion CONVENTIONS §0 opens with.

⚑⚑ **This function is NOT interchangeable with `unpack_bind`, and the two must NEVER be chosen by
ARITY.** Both accept a 2-element list and the meanings are OPPOSITE — `[a, b]` is `(host, box)`
there and `(src, opts)` here. **The caller picks the unpacker from the NODE the value came from**
(a name-keyed bind node vs a dest-keyed arm), never from the value's shape; see
`settings.kb_store.BindEntry` for the full rule. Both node shapes exist during the P5→P8 bridge,
so both unpackers are live today — `settings_assemble` calls one on each branch, adjacent lines
apart, which is exactly where an arity-based "simplification" would land.

```python
normalize_bind_dest(dest: str) -> str
```
Canonicalize a binding DESTINATION — the map key of a dest-keyed arm (R-11).

⚑⚑ **DESTINATIONS ONLY. NEVER CALL THIS ON A `host_src`.** The asymmetry is the whole ruling
(spec §2a, "THE DESTINATION IS CANONICALIZED; THE SOURCE IS NOT"): a dest is a GUEST path and the
guest home is FIXED MACHINERY (`GUEST_HOME`, not a settings key), so expanding it yields the same
absolute path on every host, forever. A `host_src`'s `~` is the INVOKING USER's home —
absolutizing THAT would bake one machine's `/home/<user>` into a settings file shared across users
and machines (§0 "files store entries UNRESOLVED").

Two normalizations, both identity-preserving for an already-canonical dest. The function is
**IDEMPOTENT**, and is applied at every producer AND again on read, deliberately:

* a LEADING `~` expands to `GUEST_HOME` (`~` → `/home/agent`, `~/x` → `/home/agent/x`). A `~`
  anywhere else is literal, exactly as `expand_expr` treats it;
* a TRAILING `/` is dropped (never from a bare `/`). **This half is not cosmetic:** `~` and `~/`
  were the two spellings that, under dest-keying, became two dict entries at ONE destination —
  the collision that triggered R-11 in the first place.

⚑ Everything else is carried VERBATIM, including an `@`-ref or `$var` destination. Those cannot
be absolutized here — they resolve later, in `settings_expand` — so a dest-keyed arm is unique in
the UNRESOLVED namespace only, and the RESOLVED-`box_dest` collision check in
`settings_categories` stays (design §2b-CAVEAT). Nothing is REFUSED here either: a non-absolute
literal dest is a spec violation, but refusing it is not R-11's job and a refusal keyed on "does
not start with `/`" would fire on every legitimate `@`-ref dest.

```python
match_var(expr: str, i: int) -> tuple[str, int]
```
Parse the `$VAR` / `${VAR}` reference starting at index *i* (the `$`).

Returns `(var_name, end_index)` — the name WITHOUT its `$` (and without the braces, when braced),
plus the index one past the whole token. Raises `SettingsError` on a malformed reference.

⚑ **PRECONDITION: `expr[i] == "$"`.** The caller has already dispatched on that character; this
function does NOT re-verify it and will happily parse a name starting at `i + 1` regardless of
what `expr[i]` actually is.

**The SINGLE parser for the `$` token family**, exactly as `match_ref` is for `@`: shared by
`_expand_var` (which resolves the name), `_scan_var_span` (which re-emits the source span for
`defer_env`) and `settings_configset._scan_tokens` (which collects names for set-time
validation). Those three carried THREE copies of this ten-line parse before; one grammar, one
copy, so a change to the token shape cannot land in some of them and not others.

```python
match_ref(expr: str, i: int) -> tuple[str, int]
```
Parse the `@`-reference starting at index *i* (the `@`), in either spelling.

Returns `(ref_name, end_index)` — the dotted name WITHOUT its `@` (and without the braces, when
braced), plus the index one past the whole token. Raises `SettingsError` on a malformed reference.

⚑ **PRECONDITION: `expr[i] == "@"`.** Every caller dispatches on that character before calling,
and this function does NOT re-verify it — `match_ref("xa.b", 0)` returns `("a.b", 4)` rather than
raising. It is stated because this is a **cross-module seam**, not a file-private helper: a new
caller that scans for `@` differently must still hand over the index OF the `@`.

TWO SPELLINGS, one meaning:

* **BARE** `@a.b.c` — the dotted name runs greedily and ends at the first character outside the
  name set (`@a.b/x` refs `a.b`). This is the NORMAL spelling and is unchanged.
* **BRACED** `@{a.b.c}` — the same name, delimited explicitly, so a LITERAL SUFFIX may follow it:
  `@{a.b.c}x` resolves `a.b.c` then appends `x`. Braces are needed ONLY where the next character
  would otherwise be eaten by the greedy name match — the spec's
  `@workset.logs/@{meta.box.name}.jsonl` is exactly that case, since bare `@meta.box.name.jsonl`
  parses as the single (absent) name `meta.box.name.jsonl` and silently loses both the box name
  and the extension. Where both forms are expressible they resolve identically.

**The SINGLE parser for both spellings**, shared by the scanner (`_expand_ref`), the whole-value
shape test (`settings_expand._is_whole_value_ref`) and set-time validation
(`settings_configset._scan_tokens`) — one grammar, not three (seam S25).

The braced form deliberately MIRRORS `${...}` (`_expand_var` / `_scan_var_span`): optional brace,
the same name regex, a required closing brace, and a distinct "unterminated" error. Same idea,
same spelling, so the two token families cannot drift apart. **NESTING IS NOT SUPPORTED and fails
loudly** (`@{a@{b}}` is unterminated): a substituted value is a LEAF and is never re-scanned, so a
nested form would imply a second, contradictory model of when expansion happens.

```python
expand_expr(
    expr: str, *, space: Literal["host", "guest"], ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    chain: tuple[str, ...] = (), defer_env: bool = False,
) -> str
```
Expand a single path/scalar expression (one bind half) in the named *space*.

Single left-to-right scan emitting literal vs expanded segments. ⚑ **A substituted value is a
LEAF — it is not re-scanned**, which is what prevents double-expansion and expansion loops.

Grammar:

* **Escapes:** `\@`→`@`, `\$`→`$`, `\\`→`\`; a backslash before any other char yields that char
  literally.
* **`~`:** ONLY when it is the FIRST character of *expr*. Expands to `ctx.host_home`
  (`space=="host"`) or `GUEST_HOME` (`space=="guest"`). A `~` elsewhere is literal.
* **`$VAR` / `${VAR}`:** name = `[A-Za-z_][A-Za-z0-9_]*`. `AGENT` → `ctx.agent_name`, `WORKSET` →
  `ctx.workset_name`, `XDG_*` → `ctx.xdg[name]`. Unknown names, or known names whose context
  value is `None`/missing, raise `SettingsError`.
* **`@`-ref:** two spellings, parsed by `match_ref` and resolved IDENTICALLY. Cycle-guarded
  against *chain*, capped at `MAX_REF_DEPTH` (64), and substitutes
  `lookup(ref_name, chain + (ref_name,))`; the result is a leaf.

⚑ *lookup* is a REQUIRED keyword with no default. There is deliberately no ambient fallback — a
caller must state what an `@`-ref means in its tier, even when the answer is "refuse them".

### `defer_env` — the box-side deferral

When `True`, the ENVIRONMENT tokens `~` and `$VAR` / `${VAR}` are NOT expanded; they are emitted
VERBATIM — the exact source span, `${...}` braces included — to be resolved later in a DIFFERENT
environment. `@`-refs (CONFIG) still expand normally, and escapes are still honored.

This is the box-side deferral of the KeyStore expansion pass (design §6a): `$XDG` / `~` name the
**box** environment, host ≠ box, so a `box_dest` env token stays deferred (block 3 / S17). Its one
consumer is `settings_expand`, which passes `defer_env=(space == "defer")`.

⚑⚑ **The escape rule INVERTS under deferral, and this is the subtle half.** Host-side, `\x` → `x`
(the backslash is consumed). Deferred, an escape of an ENVIRONMENT-significant char (`$`, `~`,
`\`) is carried **VERBATIM**, because the BOX resolver — not this host pass — re-scans for `$VAR`
and `~`, and must see the same escape to honor the user's "literal, NOT a var" intent. Consuming
it would turn `\$` into `$` and let the box re-expand exactly what the user escaped. `\@` is
STILL unescaped to `@`: this pass OWNS `@`-refs on BOTH sides, the box never processes `@`, so a
literal `@` is the correct box-side residue. **This box-side escape contract is spec-silent and
has been flagged to the director** — it is a real semantic decision living only in code.

```python
_scan_var_span(expr: str, i: int) -> tuple[str, int]
```
Return the VERBATIM `$VAR` / `${VAR}` source span starting at *i*, plus the index past it.

The `defer_env` twin of `_expand_var`. It recognizes the SAME token shape `_expand_var` resolves —
so deferral and expansion agree on token boundaries — but emits the source text unchanged
(`$XDG_STATE_HOME` / `${XDG_STATE_HOME}`) rather than a resolved value. A malformed reference
raises identically, because **both delegate the parse to `match_var`**: a deferred token must
still be well-formed at build time, just not resolved here, and "identically" is now structural
rather than two copies that happen to agree.

```python
_expand_var(expr: str, i: int, ctx: ResolveCtx) -> tuple[str, int]
```
Expand a `$VAR` or `${VAR}` starting at index *i*.

Parses via the shared `match_var`; only the RESOLUTION of the name is this function's own.

```python
_resolve_var(name: str, ctx: ResolveCtx) -> str
```
Resolve a variable name against the context namespace.

`AGENT` and `WORKSET` are refused with a "not set in this context" message when the context field
is `None`; an `XDG_`-prefixed name is refused the same way when absent from `ctx.xdg`; anything
else is an "Unknown variable". ⚑ The three messages are distinct on purpose — "not set here" and
"no such variable" are different user mistakes with different cures.

```python
_expand_ref(expr, i, lookup, chain) -> tuple[str, int]
```
Expand an `@ref` starting at index *i*.

Both spellings are parsed by the shared `match_ref`; everything below — the cycle guard, the depth
cap, the `lookup` substitution — is **spelling-agnostic and identical for the two**. A ref already
present in *chain* raises with the full cycle rendered (`a -> b -> a`), and a chain at
`MAX_REF_DEPTH` raises naming the ref that hit the cap, so both errors are actionable rather than
merely correct.

```python
resolve_value(key, *, levels: list[LevelView], ctx, lookup) -> ResolvedValue | _Unset
```
Resolve *key* by precedence over *levels* (most-specific first); returns the RAW literal or `UNSET`.

Does NOT expand — the caller expands the result via `expand_expr` with the appropriate *space*.
The two-pass structure and the three-valued result are described in full under "The three-valued
model" above; that section, not this entry, is the thing to read before editing the body.

⚑ *ctx* and *lookup* are accepted for signature stability and immediately `del`'d — the pure
precedence logic does not use them. They are in the signature so that a caller composing
`resolve_value` with `expand_expr` passes one context object to both.

### What `resolve_value` is still for

⚑ **It is no longer the settings cascade.** `settings_merge.merge` is: it walks
`settings_assemble`'s ordered `KeyStore` partials with a depth-sensitive 3-state
`__MISSING__` / present-`None` model, which replaced both this function's `""`-as-terminal
convention and its separate `defaults` pass (assembly folds the floor INTO the `base` level, so
there is no separate defaults dict downstream).

`resolve_value` is **NOT retired** — it still serves the `config.*` / `system.*` FOUNDATION path
tier, and `paths.py` is its only production caller, at four sites across two functions. Note what
those calls look like: each passes a **single-element** `levels` list
(`[LevelView("config", …)]`, `[LevelView("system", …)]`), so in production today the
most-specific-first walk has one level to walk and the `defaults=` mapping is doing the real
work. **The multi-level `[box, workset, agent, system]` shape survives only in the test suite**,
which is why the retired docstring describing it as the live cascade read so plausibly.

```python
_no_lookup(ref: str, chain: tuple[str, ...]) -> str
```
`@`-ref lookup that refuses: behavior settings carry no cross-refs.

Behavior settings are plain scalars (model, bootstrap, autonomy, …) used verbatim — there is no
`@`-ref expansion in that tier, so any `@`-ref is a configuration error and this raises naming it.

⚑ **KNOWN DELTA, NOT FIXED IN THIS PASS.** This function has **no callers** — not in `src/`, not
in `packages/`, not in the tests. `settings_launch` defines its own `_no_lookup` with the same
name and job at module scope and passes THAT one to `expand_expr`. Two live definitions of one
idea is the P10 duplication rule's exact shape, and the older docstring calling this the
"**Default** `@`-ref lookup" made it worse: `expand_expr`'s `lookup` parameter is a required
keyword with no default, so this is not installed as the default of anything. Deciding which copy
survives is a code change and belongs in its own commit.
