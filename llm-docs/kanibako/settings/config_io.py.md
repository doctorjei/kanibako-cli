# Config Document I/O — the one YAML seam, and the document mutators

`config_io` is where a settings-cascade document becomes a `dict` and where a `dict` becomes a file
on disk. Every level of the cascade — the bootstrap `kanibako.cfg`, each scope's own
settings file, the agent files, plus the consolidated name registry and the helper spawn budget
(`spawn.yaml`) — is read by `load_doc` and written by `dump_doc`. Serialization is PyYAML
throughout; there is no hand-rolled serializer. (`pyproject.toml` is Python packaging and is NOT
handled here.)

Beside them live the read-modify-write **document mutators** — read/write/remove at a
`(sections, leaf)` path, plus the scalar rendering `get` displays. They know nothing about the
KEYSPACE: a caller hands them a resolved path into a document and they preserve everything else in
it. Which file, and which nested slot, a config KEY maps to is a different question, answered by
`kanibako.settings.config_keys` and `kanibako.settings.config_dest`.

`load_doc` is one of the highest-fan-in functions in the tree, which is the reason the
parse-failure normalization below is not optional.

## Scope — what "every document" does and does not cover

The two arms above are the route for **settings-cascade** documents. Two in-tree YAML writers sit
outside it:

* `runtime/rig_meta.py` — `/etc/kanibako/rig.yaml`, the truth-in-image metadata for an extended
  rig. It rides inside the image rootfs rather than living in the cascade, and calls
  `yaml.safe_dump` directly.
* `plugins/goose/credentials.py` (agent-goose package) — goose's OWN config and secrets files,
  owned by the agent, not kanibako; it calls `yaml.safe_dump` directly.

## Packaged documents

```python
def parse_packaged(text: str) -> object
def load_packaged_mapping(ref: Traversable, filename: str) -> dict[str, Any]
```
`parse_packaged` parses a YAML file kanibako SHIPS (never a user's file) with the fastest safe
loader (`CSafeLoader` when present). `load_packaged_mapping` reads the shipped document at *ref* and
raises `PackagingError` when it is empty or not a mapping: a broken install, never an empty
document (spec §0). `core_defaults`, `keyspace_manifest` and `agent_defaults` read through it.

## The parse-failure seam

```python
def yaml_problem(exc: yaml.YAMLError) -> str
```
One-line rendering of a YAML parse failure (the problem + where).

`yaml`'s own `str()` is multi-line and cites `"<unicode string>"` as the source (we hand the loader
TEXT, not the file), so it names everything except the file. The caller supplies the file; this
supplies the problem and its line/column. ⚑ **THE ONE RENDERER, AND IT IS PUBLIC BECAUSE IT HAS A
SECOND READER:** a file's own loader (the rig metadata's, an overlay's) wraps the failure in its
own error type and renders it here, so one failure reads in one set of words.

A `yaml.MarkedYAMLError` (a `ScannerError`, a `ParserError`) carries a populated `.problem` and
`.problem_mark`, which is what the `isinstance` arm keys on. The `" ".join(str(exc).split())`
fallback exists for the unmarked case.

```python
def parse_doc_text(text: str) -> object
```
Parse ONE document from *text* under kanibako's reading rules — **THE TEXT DOOR `load_doc` ALSO
OPENS**, so a caller holding YAML instead of a file gets the SAME refusals in the SAME words. It
parses with `_UniqueKeyLoader` (below) and then guards the result. The three refusals —
`DuplicateKeyError`, `CyclicAnchorError` and `DocumentTooDeepError` — are `yaml.YAMLError`s, so one
`except yaml.YAMLError` covers all of them. A flow collection deep enough to exhaust the Python
stack is the same `DocumentTooDeepError`. `runtime/rig_meta.py`, `launch/templates.py`'s
`_equivalent` and `paths.drop_standalone_marker` read through it.

`MAX_DOC_DEPTH` (64) bounds nesting: deeper than that is refused rather than reaching the user as a
bare `RecursionError` traceback from whatever walks it next. No declared key path comes near it.

```python
def load_doc(path: Path | None) -> dict
```
Load a config document → dict. Missing/empty → `{}`; anything else that is not a mapping, a
repeated key, a self-reference, unbounded nesting or an unreadable path raises.

Only `ENOENT` / `ENOTDIR` read as missing, never `Path.exists()`: a file kanibako cannot reach
(EACCES or EPERM on it or a parent, a link loop — ELOOP) refuses through
`utils.unreadable_path_error`, naming it, because the file may be fine and the cure is to make the
path reachable. Text that is not UTF-8 refuses too. A file that is not parseable YAML raises
`~kanibako.errors.ConfigError` naming the FILE and the parse problem. A file that parses to one
value or a list raises `ConfigError` naming the FILE and which of the two it is (spec §0: never a
silent accept) — it holds no keys to read, and reading it as empty would drop whatever the user
meant by it without a word.

⚑ **THE NORMALIZATION BELONGS HERE and nowhere else:** this is the one seam that knows WHICH file is
being read — `yaml` is handed a string, so its own mark says `"<unicode string>"` — and a raw
`yaml.YAMLError` escaping to the CLI (which converts only `KanibakoError` into a clean rc1) is a
traceback on any verb that touches the cascade, including the BOX-LESS ones (`rig list` / `setup` /
`baseline`) that reach it through `load_merged_config`'s box-scalar resolve. `cli.py` catches
`KanibakoError` (and `UserCanceled`) and nothing broader.

⚑ **PyYAML behavior the `Missing/empty → {}` contract and the non-mapping refusal rest on** — keep,
do not "simplify":

| input | `yaml.safe_load` returns | `load_doc` returns |
|---|---|---|
| `""` (empty file) | `None` | `{}` |
| `"\n\n  \n"` (whitespace only) | `None` | `{}` |
| `"hello"` (bare scalar) | `'hello'` — a `str`, NOT a dict | `ConfigError` (a single value) |
| `"- a\n"` (list) | `['a']` — a `list`, NOT a dict | `ConfigError` (a list) |
| `"a: 1\na: 2\n"` (duplicate key) | `{'a': 2}` — **last wins, no error** | `ConfigError` (both lines) |
| `"﻿a: 1\n"` (BOM) | `{'a': 1}` — BOM is stripped, no error | `{'a': 1}` |
| a leading TAB | raises `ScannerError` | `ConfigError` |

## The repeated-key refusal (`_UniqueKeyLoader`, Q103)

`parse_doc_text` parses with `_UniqueKeyLoader`, a `yaml.SafeLoader` subclass, not
`yaml.safe_load`. Its `construct_document` walks the composed node tree before constructing anything
and raises `DuplicateKeyError(yaml.YAMLError)` at the first mapping that repeats a key, carrying the
key's dotted path (`box.env.A`, `list[1].k` inside a sequence) and both locations; `load_doc` turns
that into a `ConfigError` naming the file. Spec §0: PyYAML's last-wins (table above) is a silent
accept that drops the first spelling. Being a `yaml.YAMLError`, a reader that forgot this subclass
still reads the document as unreadable rather than last-wins.

* **Why the node tree:** the constructed `dict` has already lost the first spelling, and only the
  nodes carry lines and parentage.
* **Deliberately NOT a repeat:** a key beside a merge key (`<<: *base`) — YAML's own override rule.
  The merge VALUE is still checked: a repeat inside an inline map after `<<:`, or inside a map in a
  merge list, is refused (path `x.<<.a`, `x.<<[1].a`). `1:` and `true:` in one mapping ARE a
  repeat (Python hashes them equal, so PyYAML would collapse them into one key too).
* **A bare `=` key** carries YAML's `value` tag, which `SafeConstructor` cannot construct alone;
  `flatten_mapping` reads it as the string `"="`, so the pre-pass does the same without building it
  (`=:` beside `"=":` is a repeat).
* **Where:** both 1-based lines; the columns too when the two share a line (a flow map).
* **Aliases:** each node is visited once (`id` set). A recursive alias (`a: &x [*x]`) — a table
  reachable from inside itself — is REFUSED after the parse (`CyclicAnchorError`); sharing one
  table under two keys (`<<`, or `b: *x`) is legal and loads.
* **What it does not do:** fold case. `Claude:` beside `claude:` is two YAML keys; that collision is
  the agent-node layer's refusal, not this loader's.

### YAML reads that do NOT go through `load_doc`, and why

Every settings-cascade, agent, workset, box and registry file is read by `load_doc` (so by this
loader), and so is `runtime/baseline.py`'s image-baseline manifest. The other reads:

* `settings/core_defaults.py`, `settings/keyspace_manifest.py`, `settings/agent_defaults.py` —
  SHIPPED package data (and plugin declaration files), never user-edited: `load_packaged_mapping`.
* `runtime/rig_meta.py` (the rig metadata, generated by kanibako into an image or a `.rig.tgz`
  export), `launch/templates.py`'s `_equivalent` (which compares two template files) and
  `paths.drop_standalone_marker` (which reads a box-writable workset file through the box-file
  seam, `boxfs.read_text`) hold TEXT, not a path `load_doc` may open: `parse_doc_text`, the same
  rules.
* `dump_doc_commented` here re-parses its own commented output with `yaml.safe_load` — a
  ROUND-TRIP check that the comment changed no content, never a read of a user's file.
* `plugins/goose/credentials.py` (agent-goose package) — goose's OWN config/secrets, owned by the
  agent, not kanibako: `yaml.safe_load`.
* `src/kinemata_views.py` — the conformance tooling's own reader of shipped YAML, outside the
  `kanibako` package: `yaml.safe_load`.

⚑ **The `isinstance(text, str)` guard is a HOST-SAFETY guard, not a type nicety.** A non-`str`
(e.g. a `MagicMock` from an under-mocked test path) fed to `yaml.safe_load` can balloon memory
catastrophically — the project has one recorded incident of >10 GB RSS from exactly this, which is
also why the pytest runner is memory-capped. Guard the host instead of trusting the input. The
one-line `⚑` at that statement is deliberate: deleting the guard is silent until it OOMs the box.

```python
def dump_doc(path: Path, data: dict, *, header: str = "") -> None
```
Serialize *data* to *path* as YAML (creates parent dirs), after *header*.

The write is atomic (temp file in the same dir + `os.replace`) so a crash mid-write can never leave
a torn/corrupt config document on disk. `_atomic.atomic_write_text` additionally `fsync`s before the
rename, creates `path.parent` with `parents=True, exist_ok=True`, and on any failure removes the
temp file and leaves the original intact.

Dump options are pinned: `sort_keys=False` (authoring order survives a round-trip),
`default_flow_style=False` (block style), `allow_unicode=True` (non-ASCII is emitted literally
rather than `\uXXXX`-escaped). The dumper is `_SafeDumper`, a `yaml.SafeDumper` that writes a `str`
SUBCLASS as a plain string (BARE-REF-GUARD: `settings_resolve.DataExpr`); the stock safe
representer refuses a subclass outright.

```python
def dump_doc_commented(path: Path, data: dict, sections: tuple[str, ...], key: str, comment: str) -> None
```
`dump_doc`, with `# comment` ending *key*'s line in the *sections* table. It raises `ConfigError`,
writing nothing, unless that line occurs exactly once, or if the commented text would parse to
different content. `paths.py` writes the standalone `workset.registry` comment through it.

## Document mutators (load → mutate → dump)

```python
def write_root_key(path: Path, key: str, value: object) -> None
def remove_root_key(path: Path, key: str) -> bool
```
Write / remove a TOP-LEVEL scalar key, preserving other content. `remove_root_key` returns True if
it was present.

⚑⚑ **NO KEY ROUTES HERE.** Their only source call sites are the `else` arm of `if dest.sections:` in
`config_interface.set_config_value` and `reset_config_value`, and no `_KEY_ROUTES` entry has an
empty `sections` tuple. Every other `config_dest._key_slot` family also returns a non-empty prefix —
`pref` → `('pref', <scope>)`, `<scope>.secret_path.<VAR>` → `(<scope>, 'secret_path')`,
`<scope>.env.<VAR>` → `(<scope>, 'env')`, an agent setting → `('agent', 'default')`, a terminal
category → `_category_segments(key)[:-1]`. Neither function has a test.

**Do not read that as "dead, delete it."** The pair is the structural complement of the nested pair:
`if dest.sections: … else: …` is total by construction, and removing the `else` would turn a future
root-level route into an `IndexError` rather than a write. It is the symmetry that is load-bearing,
not the current reachability.

⚑ **They are not for `KanibakoConfig` fields.** `KanibakoConfig`'s fields (`box_image`,
`box_shell`, `box_share_images`, `box_enable_vault`) are **in-memory** names; on disk they are
NESTED (`box:` → `image:`). Nothing writes them through this function, and the reader walks IN
through the declared dotted spellings, so a root-level `box_image:` is not read as that field at
all — the on-disk shape is the ONLY shape.

```python
def refuse_scalar_sections(path: Path, sections: tuple[str, ...], *, data: dict | None = None) -> None
```
RAISE `ConfigError` when *sections* walks into a value that is not a table. Every section walked is
a namespace, so a value where a table belongs refuses, `null` included (spec §0). An absent section,
or a *path* that is not a file, is a writer's to create, so neither refuses. *data* is the
ALREADY-READ document, not re-read. The box, workset and lifecycle writers (`config`, `paths`,
`commands/box/`) call it before writing.

```python
def write_nested_key(path: Path, sections: tuple[str, ...], key: str, value: object) -> None
```
Write *key* into a nested table (e.g. `("system", "path")`), creating ABSENT intermediates.

Preserves other content. An ABSENT intermediate is created as a fresh table. A PRESENT intermediate
that is not a table raises `ConfigError` naming the file, the dotted section path, and the value
found; the file is left untouched. Replacing it by a table would discard it without a word
(`system: /x` plus `system set system.agent=shell` would destroy `/x`) — a silent discard spec §0
forbids.
⚑ **A present `null` is refused too, deliberately.** Every section this function walks is a
NAMESPACE, never a category: a category is always the leaf written, never walked through. A value
at a namespace, an empty line included, is not a setting the closed keyspace accepts
(`settings_keyspace.FINDING_VERDICTS` holds `NAMESPACE`), so writing a table over it would
silently discard an invalid entry instead of reporting it.
The cure the message names is a hand edit: no reset verb reaches a section that is not a table
(`reset <leaf>` finds no table to remove from; `reset --all` leaves it in place, and
`reset <scope>` refuses the scope name — all three probed). This is the live write route for
essentially every settable key, since every routing slot resolves to a non-empty `sections`
prefix (see `write_root_key` above).

```python
def remove_nested_key(path: Path, sections: tuple[str, ...], key: str) -> bool
```
Remove *key* from a nested table, pruning now-empty intermediates. Returns True if found.

The walk records the chain of tables on the way down so the prune can run bottom-up afterwards; it
stops at the first non-empty ancestor, so a sibling key anywhere up the chain keeps its whole
prefix alive. A `sections` element that is absent, or present but not a dict, is a miss — `False`,
with the file left untouched.

⚑ Both removers short-circuit on `not path.exists()` **before** `load_doc`, which matters only for
the return value: `load_doc` would answer `{}` for a missing file anyway, so the early return is a
readability choice, not a correctness one.

```python
def count_leaves(node: object) -> int
```
Count the leaf entries under a nested table — the unit every `reset --all` reports.

A `dict` recurses; anything else (scalar, list, `None`) is ONE leaf, so an empty table counts 0.
⚑ It lives HERE, below both of its callers, because they sit on opposite sides of an import edge:
`config_interface.reset_all` (the scope files) imports `agent_file`, whose `clear_overrides`
(`agent reset --all`) counts the same way, so every reset reports one unit (a two-variable `env:`
table is 2).

## Stored-value reads (the `get` model's stored-at-noun read + its rendering)

```python
def render_stored_scalar(v: object) -> str
```
Render a stored scalar for `get` output, keeping §2h's empty idioms apart: present-`None` → `null`,
terminal `""` → `""`, bool → lowercase, else `str(v)`.

The single rendering function, shared by the stored read below and by `config show` / `--effective`
(`config_interface`) so one value cannot display two ways. `commands/agent_cmd.py` and
`settings/config_display.py` name it for the same reason.

⚑ **TOTAL, AND THE TYPE IS THE RULE (P3).** Spec §2h names `""` as a terminal value that is **not**
unset, so a leaf set to `""` must not read back `(not set)`, the answer reserved for a key the file
does not mention. Absence is reported by the READ, above any rendering; a renderer able to answer
`None` could only collapse "not set" into a value that is really there.

🛑 **A DOOR THAT JUDGES EMPTINESS MUST NOT JUDGE THE RENDERING.** These spellings are not
injective on purpose: a stored `""` and a stored `'""'` both render `""`. Such a door reads
`stored_leaf_object` below.

⚑ `null` names **the YAML the file holds, not the word you type to write it**: `--null` is the write
spelling, and `set <key>=null` stores the three-character STRING, which reads back through this same
line. Reading a `get` line back is not always retyping it.

```python
def stored_leaf_object(noun_file, sections, leaf, *, default: object = None) -> object
```
The RAW value stored at `sections/leaf` in *noun_file*, or *default* when absent.

⚑ **THE ONE WALK.** Both rendering readers below are this plus their own renderer, so the two cannot
grow separate ideas of where a leaf lives.

🛑 *default* is what "not there" looks like **to the caller**, and the caller owns that choice
because the raw answer cannot make it: a present-`None` is indistinguishable from the `None`
default. The two readers below pass the module-private `_ABSENT` sentinel because they must tell
absence from a present-`None`; `agent_file.stored_leaf_value` takes the `None` default because its
one door treats both as "the file names no value here".

```python
def read_stored_leaf(noun_file, sections, leaf, *, render=render_stored_scalar) -> str | None
```
Return the value STORED at `sections/leaf` in *noun_file* (the `get` model's stored-at-noun read),
or `None` when absent / no file.

A root-level scalar (empty *sections*, e.g. a flat config field) reads the document root.

⚑ **`None` MEANS ABSENT AND NOTHING ELSE, AND THE TOTAL `render` TYPE IS WHAT GUARANTEES IT.** A
leaf that is not there returns above without rendering anything, and no renderer this signature
accepts can produce that answer for a value that IS there — so a caller cannot be handed an
ambiguous result and does not have to know the rule.

⚑ `render` is a PARAMETER because the scalar convention is not universal: `agent_file.read_leaf`
passes `stored_leaf_display` for its one list-valued leaf (`run_args`), because a shape rule belongs
with the module that owns the shape and `str()` on a list would print the Python repr
`['--e', '--f']` at the user. Every other caller takes the default.

⚑ Spec §2a's read-verb rule: **plain `get` = stored-at-noun, `--effective` = cascade.** This
function is the stored-at-noun half. It never consults the cascade, which is why a system-scope
`get system.agent` and a pref'd box's `--effective` MAY disagree and both be correct.

```python
def read_stored_pref(noun_file, sections, leaf, *, render=render_stored_pref) -> str | None
```
Read a stored `pref` REQUEST, rendering all THREE empty idioms apart.

absent → `None` (`"(not set)"`) · present-`None` → `"null"` · `""` → `'""'` · else the value.

*`~/canon/workbook/specs/settings-keyspace-1.8.0.md` §2h:* "`config get
pref.system.agent` returns the REQUEST" *and* "`--effective` shows BOTH the request and the
resulting value — so *"why did `system.agent` resolve to zippity"* is answerable from the snapshot
instead of by reading files. This is what closes the 'I set it and nothing happened' failure family."

⚑ **`render_stored_pref` AGREES WITH `render_stored_scalar`, STRING FOR STRING, AND IS STILL NOT IT
— do not fold them.** They answer different QUESTIONS. `render_stored_scalar` **decides**
what a scalar leaf reads back as; `render_stored_pref` may only **forward** — *"the pref layer MUST
NOT interpret emptiness AT ALL … otherwise it becomes a FOURTH place deciding what 'empty' means"*
(§2h). A request also carries whatever shape its TARGET holds, so a rule the scalar convention may
adopt about non-scalars is one this side must not inherit. Folding them would put one branch under
two authorities; **the agreement is the point, not a reason to merge.**

⚑ The `""` test precedes the `bool` test safely (`False == ""` is `False`), but the ORDER is not
arbitrary: `None` must be checked before anything that would stringify it.
