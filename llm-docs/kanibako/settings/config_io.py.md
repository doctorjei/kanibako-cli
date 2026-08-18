# Config Document I/O — the one YAML seam, and the document mutators

`config_io` is where a settings-cascade document becomes a `dict` and where a `dict` becomes a file
on disk. Every level of the cascade — the bootstrap `kanibako_config.yaml`, every scope's
`settings.yaml`, the agent files, plus the name registry (`names.yaml`) and the helper spawn budget
(`spawn.yaml`) — is read by `load_doc` and written by `dump_doc`. Serialization is PyYAML
throughout; there is no hand-rolled serializer. (`pyproject.toml` is Python packaging and is NOT
handled here.)

Beside them live the read-modify-write **document mutators** — read/write/remove at a
`(sections, leaf)` path, plus the scalar rendering `get` displays. They know nothing about the
KEYSPACE: a caller hands them a resolved path into a document and they preserve everything else in
it. Which file, and which nested slot, a config KEY maps to is a different question, answered by
`kanibako.settings.config_keys` and `kanibako.settings.config_dest`.

`load_doc` is one of the highest-fan-in functions in the tree (23 other source modules reference it,
plus 401 references across `tests/`), which is the reason the parse-failure normalization below is
not optional.

## Scope — what "every document" does and does not cover

The two arms above are the route for **settings-cascade** documents. Two in-tree YAML writers
deliberately sit outside it, and both say so at their own site:

* `runtime/rig_meta.py` — `/etc/kanibako/rig.yaml`, the truth-in-image metadata for an extended
  rig. It rides inside the image rootfs rather than living in the cascade, and calls
  `yaml.safe_dump` directly.
* `vscode/vscode_config.py` — the goose `config.yaml` `GOOSE_MODE` projection, which carries the
  comment *"⚑ Write via the Path object, NOT `config_io.dump_doc`"*: `atomic_write_text` does a
  REAL `mkdir`, so a mocked path would materialize a stray on-disk dir, and a best-effort re-seed
  is idempotent anyway.

⚑ **`config.yaml` is the AGENT's own file, not kanibako's.** goose owns provider/model in it
(`settings/agent_defaults.py`, `targets/credsync.py`); kanibako only projects into it.

⚑ **CORRECTED 2026-08-18 (relocation pass).** The prior module docstring claimed "*all* kanibako-owned
config files … are serialized … through `load_doc`/`dump_doc`" and enumerated `general.yaml` —
a file that no longer exists anywhere in the tree (`MIGRATION.md`: *"The default `general.yaml`
likewise becomes `general/settings.yaml`"*) — and listed `settings.yaml` twice. See the false-claim
notes at the bottom of this document.

## The naming history

⚑ These mutators were named `_write_toml_key*` / `_remove_toml_key*` while living in
`config_interface`. kanibako has no TOML config file — they called `load_doc`/`dump_doc` (YAML) the
whole time. The names are now honest. (Commit `536e349`, *"Move the config document mutators to
config_io under honest names"*; the old names survive in the tree only inside build/cache
artifacts.)

## The parse-failure seam

```python
def _yaml_problem(exc: yaml.YAMLError) -> str
```
One-line rendering of a YAML parse failure (the problem + where).

`yaml`'s own `str()` is multi-line and cites `"<unicode string>"` as the source (we hand
`yaml.safe_load` TEXT, not the file), so it names everything except the file. The caller supplies
the file; this supplies the problem and its line/column.

⚑ **MEASURED, PyYAML 6.0.3 (2026-08-18):** a `ScannerError` renders as 5 lines, a `ParserError` as
8, and every one contains `in "<unicode string>", line N, column M:`. Both are
`yaml.MarkedYAMLError` subclasses with a populated `.problem` and `.problem_mark`, which is what the
`isinstance` arm keys on. The `" ".join(str(exc).split())` fallback exists for the unmarked case.

```python
def load_doc(path: Path | None) -> dict
```
Load a config document → dict. Missing/empty/non-mapping → `{}`.

A file that is not parseable YAML raises `~kanibako.errors.ConfigError` naming the FILE and the
parse problem.

⚑ **THE NORMALIZATION BELONGS HERE and nowhere else:** this is the one seam that knows WHICH file is
being read — `yaml` is handed a string, so its own mark says `"<unicode string>"` — and a raw
`yaml.YAMLError` escaping to the CLI (which converts only `KanibakoError` into a clean rc1) is a
traceback on any verb that touches the cascade, including the BOX-LESS ones (`rig list` / `setup` /
`baseline`) that reach it through `load_merged_config`'s box-scalar resolve (B6-Editor S-3).

*Verified 2026-08-18: `cli.py` catches `KanibakoError` (and `UserCancelled`) and nothing broader —
`errors.py` carries a `⚑` saying exactly that. `commands/image.py` (`rig list`),
`commands/setup_cmd.py` and `commands/baseline_cmd.py` all call `load_merged_config`.*

⚑ **MEASURED PyYAML behaviour the `Missing/empty/non-mapping → {}` contract rests on** — keep, do
not "simplify":

| input | `yaml.safe_load` returns | `load_doc` returns |
|---|---|---|
| `""` (empty file) | `None` | `{}` |
| `"\n\n  \n"` (whitespace only) | `None` | `{}` |
| `"hello"` (bare scalar) | `'hello'` — a `str`, NOT a dict | `{}` |
| `"a: 1\na: 2\n"` (duplicate key) | `{'a': 2}` — **last wins, no error** | `{'a': 2}` |
| `"﻿a: 1\n"` (BOM) | `{'a': 1}` — BOM is stripped, no error | `{'a': 1}` |
| a leading TAB | raises `ScannerError` | `ConfigError` |

⚑ **The `isinstance(text, str)` guard is a HOST-SAFETY guard, not a type nicety.** A non-`str`
(e.g. a `MagicMock` from an under-mocked test path) fed to `yaml.safe_load` can balloon memory
catastrophically — the project has one recorded incident of >10 GB RSS from exactly this, which is
also why the pytest runner is memory-capped. Guard the host instead of trusting the input. The
one-line `⚑` at that statement is deliberate: deleting the guard is silent until it OOMs the box.

```python
def dump_doc(path: Path, data: dict) -> None
```
Serialize *data* to *path* as YAML (creates parent dirs).

The write is atomic (temp file in the same dir + `os.replace`) so a crash mid-write can never leave
a torn/corrupt config document on disk. `_atomic.atomic_write_text` additionally `fsync`s before the
rename, creates `path.parent` with `parents=True, exist_ok=True`, and on any failure removes the
temp file and leaves the original intact.

Dump options are pinned: `sort_keys=False` (authoring order survives a round-trip),
`default_flow_style=False` (block style), `allow_unicode=True` (non-ASCII is emitted literally
rather than `\uXXXX`-escaped).

## Document mutators (load → mutate → dump)

```python
def write_root_key(path: Path, key: str, value: object) -> None
def remove_root_key(path: Path, key: str) -> bool
```
Write / remove a TOP-LEVEL scalar key, preserving other content. `remove_root_key` returns True if
it was present.

⚑⚑ **NO KEY ROUTES HERE TODAY — MEASURED 2026-08-18.** Their only source call sites are the `else`
arm of `if dest.sections:` in `config_interface.set_config_value` (line ~752) and
`reset_config_value` (line ~906), and **0 of the 30 `_KEY_ROUTES` entries has an empty `sections`
tuple.** Every other `config_dest._key_slot` family also returns a non-empty prefix — `pref` →
`('pref', <scope>)`, `<scope>.secret_path.<VAR>` → `(<scope>, 'secret_path')`, `<scope>.env.<VAR>` →
`(<scope>, 'env')`, an agent setting → `('agent', 'default')`, a terminal category →
`_category_segments(key)[:-1]`. Neither function has a test.

**Do not read that as "dead, delete it."** The pair is the structural complement of the nested pair:
`if dest.sections: … else: …` is total by construction, and removing the `else` would turn a future
root-level route into an `IndexError` rather than a write. It is the symmetry that is load-bearing,
not the current reachability.

⚑ **The prior docstring's claim — "*used for flat `KanibakoConfig` fields that live at the document
root*" — is FALSE and was dropped.** `KanibakoConfig`'s fields (`box_image`, `box_shell`,
`paths_project_toml`, `box_share_images`, `config_paths`) are the **flattened in-memory** names
produced by `config._flatten_toml`; on disk they are NESTED (`box:` → `image:`), which is precisely
why the flattener exists. Nothing writes them through this function.

```python
def write_nested_key(path: Path, sections: tuple[str, ...], key: str, value: object) -> None
```
Write *key* into a nested table (e.g. `("system", "path")`), creating intermediates.

Preserves other content. Any missing — or present-but-not-a-dict — intermediate is replaced with a
fresh table on the way down, so a scalar sitting where a section belongs does not raise; it is
overwritten. This is the live write route for essentially every settable key, since every routing
slot resolves to a non-empty `sections` prefix (see `write_root_key` above).

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

## Stored-value reads (the `get` model's stored-at-noun read + its rendering)

```python
def render_stored_scalar(v: object) -> str | None
```
Render a stored scalar for `get` output: bools lowercase, empty → None.

The single rendering function, shared by the stored read below and by `config show` / `--effective`
(`config_interface`, line ~1041) so one value cannot display two ways. `commands/agent_cmd.py`
names it for the same reason.

```python
def read_stored_leaf(noun_file: "Path | None", sections: tuple[str, ...], leaf: str) -> str | None
```
Return the value STORED at `sections/leaf` in *noun_file* (the `get` model's stored-at-noun read),
or `None` when absent / no file.

A root-level scalar (empty *sections*, e.g. a flat config field) reads the document root. Bools
render lowercase `"true"`/`"false"` (matching `set`'s coercion + `show`'s rendering); a stored empty
string reads as `None` (`"(not set)"`), preserving the prior "empty ⇒ unset" convention.

⚑ Spec §2a's read-verb rule: **plain `get` = stored-at-noun, `--effective` = cascade.** This
function is the stored-at-noun half. It never consults the cascade, which is why a system-scope
`get system.agent` and a pref'd box's `--effective` MAY disagree and both be correct.

```python
def read_stored_pref(noun_file: "Path | None", sections: tuple[str, ...], leaf: str) -> str | None
```
Read a stored `pref` REQUEST, rendering all THREE empty idioms apart.

⚑ The general `read_stored_leaf` renders a stored `""` as `None` (`"(not set)"`) — the
"empty ⇒ unset" convention. That convention is WRONG for a pref: §2h designates `get` as the verb
that *"returns the REQUEST"*, and the three idioms it must forward untouched (present-`None`,
terminal `""`, and absence) are three DIFFERENT requests. Collapsing two of them into one display
makes the suppression request — the only channel a box has to drop something its agent declares —
indistinguishable from having asked nothing.

absent → `None` (`"(not set)"`) · present-`None` → `"null"` · `""` → `'""'` · else the value.

*Spec check, 2026-08-18: `specs/settings-keyspace-1.8.0.md` §2h reads verbatim* "`config get
pref.system.agent` returns the REQUEST" *and continues* "`--effective` shows BOTH the request and the
resulting value — so *"why did `system.agent` resolve to zippity"* is answerable from the snapshot
instead of by reading files. This is what closes the 'I set it and nothing happened' failure family."

⚑ **THE FOUR-BRANCH TAIL IS THE WHOLE POINT — do not refactor it into `render_stored_scalar`.**
The `None` and `""` arms are exactly what that function collapses. The `""` test precedes the
`bool` test safely (`False == ""` is `False`), but the ORDER is not arbitrary: `None` must be
checked before anything that would stringify it.

---

## FALSE CLAIMS FOUND AND DROPPED (relocation pass, 2026-08-18)

Recorded here rather than relocated, because relocating a drifted claim launders it into a document
that reads as current. Each was measured, not inferred.

1. **`general.yaml`** (module docstring file list) — the file does not exist. Whole-repo search
   (`command grep -rn -I`, excluding `.git`) finds it in exactly four places: this docstring, its
   copy in the stale `build/lib/` artifact, `CHANGELOG.md:2216` (history), and `MIGRATION.md:2927`,
   which states it *becomes* `general/settings.yaml`. Zero references in `src/` (other than the
   docstring itself) and zero in `tests/`. **Dropped.**
2. **`settings.yaml` listed TWICE** in the same parenthetical list. **Dropped one.**
3. **`config.yaml` listed as a kanibako-owned config file.** It is goose's own agent config, already
   covered by the list's own "agent configs", and one of its write sites deliberately bypasses
   `dump_doc`. **Dropped; replaced by the scope section above.**
4. **"*All* kanibako-owned config files … are serialized … through `load_doc`/`dump_doc`."** —
   `runtime/rig_meta.py:67` and `vscode/vscode_config.py:803` both call `yaml.safe_dump` directly,
   each with a documented reason. **Rewritten to the true scope (settings-cascade documents).**
   The adjacent sentence "there is no hand-rolled serializer" is TRUE and was kept.
5. **`load_doc`'s parenthetical `("Configuration file missing or malformed")`** — a quotation of
   `ConfigError`'s docstring that no longer matches it. Live `errors.py` reads
   `"""Configuration missing, malformed, or refused."""`; the quoted wording survives only in
   `build/lib/kanibako/errors.py`. It was misleading twice over, since it is also not the message
   `load_doc` actually raises (`the config file {path} is not valid YAML: …`). **Dropped.**
6. **`write_root_key`'s "used for flat `KanibakoConfig` fields that live at the document root"** —
   false on both halves; see the measurement under that symbol. **Dropped.**

### Completeness sweep

`prose-relocation-check.py`: **69 prose lines at HEAD, 55 removed, 0 scoring below 0.6** against
this document — no removed line is orphaned. 14 prose lines remain in source: the trimmed module
docstring, the ten one-line symbol descriptors (`prose-pass-check` check 2 counts 11 docstring-bearing
symbols — the module plus ten functions, added=[] removed=[]), the two section banners, and the `⚑`
keep-test one-liners below.

**Deliberate content drops: 6, all listed above** — 5 false claims (items 1, 3, 4, 5, 6) and 1 plain
duplication (item 2). Nothing else was dropped: every other pre-pass prose line was relocated, in
substance, into a section above.

**Three `⚑` one-liners were KEPT IN SOURCE** under the keep test, because deleting each would let a
future edit break something silently at that exact line:

* at the `isinstance(text, str)` guard — deleting the guard is silent until it OOMs the box;
* above the `try:` — the parse-failure normalization is what stops a raw `yaml.YAMLError` reaching
  the CLI as a traceback, and it looks like boilerplate;
* at `read_stored_pref`'s four-branch tail — it looks exactly like a candidate for collapsing into
  `render_stored_scalar`, which is the one thing it must not do.

A fourth was ADDED above `write_root_key`, pointing here: the pair currently has no reachable route
and reads as dead code.

### Packaging

`llm-docs/` is **not shipped**, verified rather than assumed: `pyproject.toml` has
`[tool.setuptools.packages.find] where = ["src"]`, there is no `MANIFEST.in`, the wheel staging tree
`build/lib/` contains no `llm-docs` path, and the sdist manifest
`src/kanibako_cli.egg-info/SOURCES.txt` (218 entries) has zero `llm-docs` entries.

### Kept and MARKED, per the invert-the-drop rule for library behaviour

All PyYAML claims (`str()` shape, `"<unicode string>"`, empty-file `None`, non-mapping passthrough)
were **re-measured on PyYAML 6.0.3 rather than assumed**, and the measurements are tabulated above.
None needed dropping; all four were true.
