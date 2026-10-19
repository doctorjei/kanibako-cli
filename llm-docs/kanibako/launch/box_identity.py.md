# Box Identity — the standalone name grammar, and the box-name allowlist

`box_identity` answers two separate questions about what a box may be CALLED. The first is
generative: what name does a **standalone** box get, given the directory it was created in? The
second is a refusal: which strings are acceptable as a box name at all, in any mode? Neither
question touches the filesystem — the module is pure (modulo `os.urandom` and the clock, reached
through `kanibako.kuid`) and side-effect free, precisely so the sanitize/cap/collision-regen logic
is directly unit-testable.

⚑ The three box modes are `primary`, `named` and `standalone`. Only the `<kuid>_<leaf>` grammar
below is standalone-specific; the allowlist is not — it governs any string offered as a box name.

## The standalone name — `<kuid>_<leaf>`

A standalone box is named `<kuid>_<leaf>`, the two parts joined with a single `_`.

* **`kuid`** is the box's stable `kanibako.kuid` id — a 25-bit Crockford base32 token (24 data bits
  plus 1 parity bit, so always 5 lowercase characters). The kuid is GENERATED at creation and
  STORED as the settable `workset.kuid` key, which is what makes it the stable cross-move identity
  prefix.
* **`leaf`** is the project-root basename run through `sanitize_cap` (below). It keeps the
  directory's case.

**Only the kuid is stable.** The leaf is re-derived LIVE from the current root basename every time
the name is composed, so it TRACKS directory moves: move the project and the kuid stays put while
the leaf follows the directory. `compose_standalone_name` is the single source
for re-composing a moved box's name from its stored kuid, and it mirrors the join inside
`_generate_with_leaf`.

`standalone_kuid` is the inverse: it returns everything up to the FIRST `_`. That split is
unambiguous even when the leaf itself contains a `_`, because the kuid alphabet never does. It
exists so the create path can persist the generated kuid as the `workset.kuid` key WITHOUT
re-deriving it — the name was composed FROM the kuid, so reading it back out is exact.

### `sanitize_cap`, in full

`sanitize_cap` writes the leaf in its ASCII spelling (`romanize.to_ascii`), turns each run of
whitespace or punctuation (including `-` and `.`) into one `_`, strips `_` from both ends, keeps
case, caps at `_LEAF_CAP` (32), and falls back to `"box"` (`_EMPTY_LEAF_FALLBACK`). The kept
characters are `NAME_CHAR_CLASS` (`A-Za-z0-9_`). A character with no ASCII spelling, such as kanji,
raises `DerivedBoxNameError`; each caller adds its cure.

⚑ Only the kuid half of `<kuid>_<leaf>` is lowercase. The leaf carries the source directory's case
(spec §0 NAMING RULES); the two halves are opposite by design and must not be unified.

### Where the split of responsibility falls

The kuid CODEC lives in `kanibako.kuid`, which is deliberately break-off-ready and contains nothing
kanibako-specific. The `<kuid>_<leaf>` NAME composition and the `workset.kuid` key wiring live here
and in the settings layer.

### The canonical shape, and why it is also an input grammar

`_LEAF_RE` (`^[A-Za-z0-9_]{1,32}$`) plus `kuid.is_valid` on the prefix is the canonical
`<kuid>_<leaf>` shape (`is_canonical_standalone_name`): the prefix, up to the FIRST `_`, must be a
valid kuid — 5 Crockford base32 chars with odd parity — and the leaf must be 1-32 chars of
`[A-Za-z0-9_]`, matching what `sanitize_cap` produces.

⚑ The prefix ALPHABET is *now* the kuid's Crockford set, **not** RFC-4648 `[a-z2-7]`. The retired
`<random24>` slot used the latter, so a validator written against the old alphabet still looks
plausible.

This is the verbatim shape the generator emits, which is why it doubles as an INPUT grammar: it is
exactly what a user may assert by passing a fully-formed `--name`. It is a shape test only:
`kuid.is_valid` accepts any case, and `_canonical_name` gives the STORED spelling — the kuid
canonicalized (`kuid.canonicalize`, lowercase), the leaf as typed. An over-long leaf, a leaf with
illegal characters, or a
prefix that is not a valid kuid all fail the match — and failing it is not an error, it just routes
the string down the "treat the whole thing as a raw leaf" branch below.

### Collision regeneration

A freshly generated kuid prefix can collide with an already-registered standalone box name. The
generator therefore regenerates the kuid — bounded retries, `_MAX_REGEN_ATTEMPTS` — until the
*whole* name is unique within a caller-supplied `existing` set. `existing` is the set of standalone
box names currently registered (`registry.standalone`). Uniqueness compares case-blind
(`identifiers.find_identifier`), because the leaf keeps case: `k3xy0_Foo` is taken when
`k3xy0_foo` is registered.

Exhausting the bound raises `RuntimeError`. That is effectively impossible with a sane `existing`
set; the bound is there to stop a degenerate caller from spinning forever, not because collisions
are expected.

## Resolving a user-supplied `--name`

`resolve_standalone_name` has exactly three branches, and the enumeration is the whole contract:

1. **`supplied` is empty** → `make_standalone_box_name`: a fresh kuid prefix joined to
   `sanitize_cap(root.name)`, with whole-name collision regen.
2. **`supplied` does NOT match the canonical `<kuid>_<leaf>` shape** → the WHOLE supplied string
   is treated as a raw leaf: `<kuid>_<sanitize_cap(supplied)>`. The kuid is the `box_kuid`
   argument (a box's STORED kuid) when given, else fresh with collision regen. An over-long or
   illegal-character name lands here rather than being rejected.
3. **`supplied` DOES match the canonical shape** → the user is asserting a full canonical id. Its
   stored spelling (`_canonical_name`: kuid canonicalized, leaf as typed) is returned if free in
   `existing`; if a case-blind match is taken, the call raises `kanibako.errors.ProjectError` naming
   the STORED spelling, with guidance to retry without the `<kuid>_` prefix.

The case the user typed survives every branch; only the kuid half is canonicalized.

Branch 3's taken case is the ONLY refusable input. That is what `validate_standalone_name` exists
for: it raises the SAME `ProjectError` for the same case, and is a no-op for branches 1 and 2
because both are always satisfiable. Callers run it BEFORE any filesystem mutation, so a doomed
standalone `create` refuses up front instead of leaving a half-created tree (BUG-A).

⚑ Both functions raise the one refusal `_refuse_taken` builds, so the pre-flight cannot drift from
the real path's words.

### Moving and naming an existing standalone box

* `carry_standalone_name(carried_kuid, root, existing, *, own_name=None)` re-composes a MOVING
  box's name from its stored kuid and the new leaf. It re-mints the kuid only when the whole
  composed name is taken by ANOTHER box (case-blind); `own_name` is excluded, so a leaf-preserving
  move keeps its kuid. Caller: `settings/paths.py`.
* `refuse_nonleaf_standalone_name(supplied, root, *, box_kuid=None)` refuses a standalone `--name`:
  standalone identity is machine-generated (keyspec §2c `meta.box.name`), so a typed name has
  nothing to set. No-ops: the bare leaf, and (with `box_kuid`) the composed `<kuid>_<leaf>`.
  Callers: `commands/box/_parser.py`, `_lifecycle.py`, `_duplicate.py`.
* `refuse_standalone_rename(supplied, current_name)` is the same rule for a door that holds the
  composed name rather than a root (`commands/restore.py`). No-ops: that name and its leaf half,
  by EXACT equality.

Both refusals come from `_refuse_directory_name`: "rename the directory to rename the box".

## The box-name allowlist (spec §0 NAMING RULES)

A box name follows the name rule, `identifiers.name_flaw`: 1–64 ASCII letters, digits, or `_`, not
starting with a digit. `_box_name_violation` returns `"box name <flaw>; <NAME_CHARSET>"` or `None`.
The one exception is a canonical standalone name: its kuid may begin with a digit, so it is held to
the charset and length but not to the first character, which kanibako chose, not the user.

⚑ A name is validated AS THE USER TYPED IT. Uppercase is not a violation and is not folded
upstream; collision is what compares case-blind, and that happens elsewhere. With no `-` in a name,
the container name `kb-<W>-<B>` reads back without escaping.

### The entry points

Several public wrappers exist because callers need different failure modes:

* `is_valid_box_name` — `True` when the name passes; never raises. The "flag, don't reject"
  companion to `validate_box_name`: pre-existing non-conforming boxes still resolve, they just get
  warned about. Also used by `channels/helper_listener.py`.
* `box_name_reason` — the reason string, for composing a warning message. Callers:
  `settings/paths.py` and `utils/__init__.py`.
* `validate_box_name` — raises `BoxNameError` (a `ProjectError` subclass carrying the name). Enforced
  at creation and at `--name`, i.e. on NEW names only. Callers: `commands/box/_lifecycle.py`,
  `_parser.py`, `_duplicate.py`, and `commands/workset_cmd.py`.
* `classify_designation(value) -> Designation` — classifies a box designation by shape alone
  (system-design § Detection & import): `ABSENT` (empty or `None`), `IDENTIFIER` (a valid box name,
  which is also a relative path), `PATH` (fails the name rule but can name a path), or `INVALID`
  (holds a NUL). The filesystem is not consulted.
* `name_spelling(name)` — the spelling a refusal offers, and whether it is the ASCII one, or
  `None`: an ASCII name is offered its `_` spelling; a non-ASCII name its ASCII spelling, in `_`
  too, when one exists.
* `box_name_cure(command, name)` — the cure line: `<command> --name <spelling>`, else
  `<command> --name <new-name>`. The spelling is offered, never applied: a typed name is the
  user's.

Refusing a NEW name costs the user a retype, while refusing an EXISTING one would make a box
already on disk unreachable — hence the warn-only path.
