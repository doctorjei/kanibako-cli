# Configuration I/O

Yapyon: `load_doc`; YamlPlus: `dump_doc`. Routing: `config_keys`/`config_dest`. PG: `AkanError`→`ConfigError`
(source/cause), private CLI fetches. Excludes `pyproject.toml`.

Standalone markers: `parse_standalone_marker` uses public `yapyon.loads` on strict
UTF-8 bytes; invalid/non-string → `None`. `render_standalone_marker` uses `yamlplus.dumps`.
Token recognition and legacy acceptance stay in `config`; box transport stays unchanged.

## Host transport and native codecs

`read_doc_text` keeps encodings; `write_doc_text` is atomic. `overwrite_doc_text` mkdirs before
`overwrite_doc_text_in_existing_parent`. Host raw reads, in-place writes, and default-encoding
appends follow links without mkdir. Unlink retains `missing_ok`. `copy_doc_file` keeps
copy2 metadata/links/errors; `replace_json_doc` renders before fixed `.json.tmp` write/replace,
without mkdir. Box text/bytes use boxfs; writes replace atomically, except
`overwrite_box_doc_bytes_in_existing_parent`: in-place, existing parents.
`read_packaged_bytes` keeps bytes. Codex surgery, rig validation, and empty bootstraps remain.

JSON str/bytes/bytearray and TOML retain stdlib values/errors; `parse_native_yaml` uses unguarded
SafeLoader. Goose owns mapping/error/size policy. `render_json_text`: compare-before-write,
separators/indent. `dump_json_doc` truncates before streaming, including partial failures.
Persona UTF-8/projection/errors and Tweakcc/Docker policies remain caller-owned.

`load_jsonc` tries JSON, then `_strip_jsonc`: block comments, whole-line `//` comments, trailing
commas before `}`/`]`. Unparseable → `None`; inline `//` stays unsupported (URL ambiguity).
Regex limitations and caller shape/consent/strict-JSON rewrite policies remain.

## Packaged documents

```python
def parse_packaged(text: str) -> object
def load_packaged_mapping(ref: Traversable, filename: str) -> dict[str, Any]
```
`parse_packaged` retains legacy YAML. `load_packaged_mapping` uses public `yapyon.loads`, then
copies nested dicts/lists to plain containers, preserving keys/order/scalars. Empty/nonmapping
resources raise `PackagingError`; parse failures name the resource and retain their cause.
I/O/decoding propagate. No selector/fallback. Callers: `core_defaults`, `keyspace_manifest`, `agent_defaults`.

## Owned reads and native guards

`parse_doc_text(text, *, source=None)` uses public `yapyon.loads`, then
`_plain_containers`: detached plain dictionaries/lists, preserving keys, order,
and scalar values. Whitespace/comment-only text returns `None` before parsing;
explicit `None` also returns `None`. Quoted `{key}` and `{$NAME}` stay strings.
Full Yapyon expressions are supported; no functions/hooks are provided.
There is no legacy fallback, filename inference, or codec selector.

`load_doc(path)` returns `{}` for missing/empty documents. Only `ENOENT`/`ENOTDIR`
mean missing. Unreachable paths (`EACCES`, `EPERM`, `ELOOP`) use
`utils.unreadable_path_error`; other filesystem and UTF-8 failures become
path-naming `ConfigError`s with their causes. Non-text read results return `{}`:
an under-mocked read once exhausted host memory in a parser.

The path supplies Yapyon's source. `AkanError` retains its diagnostic/cause in
`ConfigError`. Matching-source 1-based lines/zero-based columns adapt to native
YAML's shared 1-based brace detector; only that line supplies a quoted cure.
Invalid/missing locations give none. Stack exhaustion is wrapped. Nonmappings name
their shape; `_unnamed_key` refuses nested non-string keys. Callers validate keyspace.

`rig_meta.load_rig_meta` uses the same reader but retains `ValueError` and its
cause; file errors propagate. `kinemata_artifacts` also reads owned text here.
`parse_packaged` remains the explicit legacy YAML reader for `kinemata_views`'s
source walkers; changing those tooling consumers needs its own bounded slice.

### Native template YAML

`parse_template_yaml` independently delegates to `_parse_guarded_yaml`, never
the owned reader. `launch/templates._equivalent` retains YAML error handling.
`_UniqueKeyLoader` checks composed nodes before construction, where duplicate
locations still exist. `DuplicateKeyError` carries the dotted key and both
1-based lines, plus columns on one-line repeats. A bare `=` key uses YAML's
`value`-tag spelling. A key beside `<<: *base` is an override, not a duplicate;
repeats inside the merge value still refuse. Case is not folded.

`_guard_document` refuses self-references and nesting past `MAX_DOC_DEPTH` (64).
Its iterative walk memoizes deepest visits, permitting shared siblings while
bounding work by distinct containers and depths. Native parser stack exhaustion
becomes `DocumentTooDeepError`. These errors remain `yaml.YAMLError`s.
`yaml_problem` renders marked problems with line/column and an unquoted-brace
hint; unmarked errors use whitespace-flattened text. Native `None` normalization
remains on `_UniqueKeyLoader` and `_PackagedLoader`, never on Goose's SafeLoader.

```python
def convert_legacy_doc_text(text: str, *, source: str | None = None) -> str
def dump_doc(path: Path, data: dict, *, header: str = "") -> None
```
Explicit legacy conversion: public `from_yaml(yaml_compatible=True)`→`yamlplus.normalize`,
using `_UniqueKeyLoader`'s dialect/duplicates, `_guard_document`, and source diagnostics.
No I/O or runtime fallback; scalar markers survive, blank/comment-only text stays exact.
Comments retain paths/occurrences; trailing comments may precede values, merge comments move to their mapping.

Both outputs share `_owned_output_data`: detached plain containers, preserved sharing,
tuples→lists, date/datetime/bytes→string-consumer text; other scalars/string subclasses remain.
`_dump_text` uses public `yamlplus.dumps`; native codecs stay native.
`dump_doc` prepends *header*; `_atomic.atomic_write_text` creates parents, writes/fsyncs a
same-directory temp, then `os.replace`s. Failure removes the temp, preserving the original.
`_SafeDumper` remains for `agent_defaults._build_setting_arg`'s invalid-`empty` diagnostic.

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

⚑ `null` names **the YAML the file holds, not the word you type to write it**: `--null` and
`set <key>=None` write it, and `set <key>=null` stores the four-character STRING, which reads back
through this same line.

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
