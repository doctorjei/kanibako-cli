# Box Identity — the standalone name grammar, and the box-name blocklist

`box_identity` answers two separate questions about what a box may be CALLED. The first is
generative: what name does a **standalone** box get, given the directory it was created in? The
second is a refusal: which strings are acceptable as a box name at all, in any mode? Neither
question touches the filesystem — the module is pure (modulo `os.urandom` and the clock, reached
through `kanibako.kuid`) and side-effect free, precisely so the sanitize/cap/collision-regen logic
is directly unit-testable.

⚑ The three box modes are `primary`, `named` and `standalone`. Only the `<kuid>_<leaf>` grammar
below is standalone-specific; the blocklist is not — it governs any string offered as a box name.

## The standalone name — `<kuid>_<leaf>`

A standalone box is named `<kuid>_<leaf>`, the two parts joined with a single `_`.

* **`kuid`** is the box's stable `kanibako.kuid` id — a 25-bit Crockford base32 token (24 data bits
  plus 1 parity bit, so always 5 lowercased characters). It REPLACES the former `<random24>` slot
  (settings-conformance P6d): the kuid is GENERATED at creation and STORED as the settable
  `workset.kuid` key, which is what makes it the stable cross-move identity prefix.
* **`leaf`** is the project-root basename, sanitized so only portable filename characters survive
  (`[^A-Za-z0-9._-]` → `_`), lowercased, and capped at 32 characters. An empty leaf — an empty
  basename, or one whose characters are all illegal — falls back to `"box"`.

**Only the kuid is stable.** The leaf is re-derived LIVE from the current root basename every time
the name is composed, so it TRACKS directory moves: move the project and the kuid stays put while
the leaf follows the directory (spec 2026-07-04). `compose_standalone_name` is the single source
for re-composing a moved box's name from its stored kuid, and it mirrors the join inside
`_generate_with_leaf`.

`standalone_kuid` is the inverse: it returns everything up to the FIRST `_`. That split is
unambiguous even when the leaf itself contains a `_`, because the kuid alphabet never does. It
exists so the create path can persist the generated kuid as the `workset.kuid` key WITHOUT
re-deriving it — the name was composed FROM the kuid, so reading it back out is exact.

### `sanitize_cap`, in full

Non-portable characters — anything outside `[A-Za-z0-9._-]` — become `_`. The result is then
lowercased, because every box name is lowercase, and capped at `_LEAF_CAP` (32) characters. If what
is left is empty, it falls back to `_EMPTY_LEAF_FALLBACK`, the string `"box"`.

The lowercasing is not cosmetic: it is what makes the sanitized alphabet and the canonical-shape
alphabet the same set, so a generated name always matches the grammar that validates a supplied
one.

### Where the split of responsibility falls (D6a)

The kuid CODEC lives in `kanibako.kuid`, which is deliberately break-off-ready and contains nothing
kanibako-specific. The `<kuid>_<leaf>` NAME composition and the `workset.kuid` key wiring live here
and in the settings layer.

### The canonical shape, and why it is also an input grammar

`_LEAF_RE` (`^[a-z0-9._-]{1,32}$`) plus `kuid.is_valid` on the prefix is the canonical
`<kuid>_<leaf>` shape: the prefix, up to the FIRST `_`, must be a valid kuid — 5 Crockford base32
chars with odd parity — and the leaf must be 1-32 chars of `[a-z0-9._-]`, i.e. lowercase, matching
what `sanitize_cap` produces.

⚑ The prefix ALPHABET is *now* the kuid's Crockford set, **not** RFC-4648 `[a-z2-7]`. The retired
`<random24>` slot used the latter, so a validator written against the old alphabet still looks
plausible.

This is the verbatim shape the generator emits, which is why it doubles as an INPUT grammar: it is
exactly what a user may assert by passing a fully-formed `--name`. The match is case-sensitive, so
callers lowercase a supplied name first. An over-long leaf, a leaf with illegal characters, or a
prefix that is not a valid kuid all fail the match — and failing it is not an error, it just routes
the string down the "treat the whole thing as a raw leaf" branch below.

### Collision regeneration (design-review D-M13)

A freshly generated kuid prefix can collide with an already-registered standalone box name. The
generator therefore regenerates the kuid — bounded retries, `_MAX_REGEN_ATTEMPTS` — until the
*whole* name is unique within a caller-supplied `existing` set. `existing` is the set of standalone
box names currently registered (`registry.standalone`).

Exhausting the bound raises `RuntimeError`. That is effectively impossible with a sane `existing`
set; the bound is there to stop a degenerate caller from spinning forever, not because collisions
are expected.

## Resolving a user-supplied `--name`

`resolve_standalone_name` has exactly three branches, and the enumeration is the whole contract:

1. **`supplied` is empty** → `make_standalone_box_name`: a fresh kuid prefix joined to
   `sanitize_cap(root.name)`, with whole-name collision regen.
2. **Otherwise `supplied` is lowercased first, and then**: if it does NOT match the canonical
   `<kuid>_<leaf>` shape → the WHOLE supplied string is treated as a raw leaf,
   `<fresh-kuid>_<sanitize_cap(supplied)>`, again with collision regen. An over-long or
   illegal-character name lands here rather than being rejected.
3. **`supplied` DOES match the canonical shape** → the user is asserting a full canonical id
   verbatim. If it is free in `existing` it is returned as-is; if it is taken, the call raises
   `kanibako.errors.ProjectError` with guidance to retry without the `<kuid>_` prefix.

Branch 3's taken case is the ONLY refusable input. That is what `validate_standalone_name` exists
for: it raises the SAME `ProjectError` for the same case, and is a no-op for branches 1 and 2
because both are always satisfiable. Callers run it BEFORE any filesystem mutation, so a doomed
standalone `create` refuses up front instead of leaving a half-created tree (BUG-A).

⚑ The refusal message is spelled out in both functions. If one changes, the other must change with
it — a pre-flight that refuses with different words than the real path is worse than no pre-flight.

## The box-name blocklist (W1 Phase D, §Design 8)

A box name is REJECTED if it contains any blocked character or violates a structural rule;
**everything else is permitted**. That is the design decision, and it is why this is a blocklist and
not an allowlist: unicode letters and digits, and interior `.`, ARE allowed.

The blocked sets are defined by standard categories rather than enumerated, so the rule is
COMPLETE:

* Control characters `U+0000-U+001F` and `U+007F`.
* All whitespace — ASCII space plus any Unicode whitespace, via `str.isspace`.
* ASCII punctuation EXCEPT `_ - .`. This single set subsumes both the Windows-reserved characters
  `< > : " / \ | ? *` and the POSIX shell metacharacters, which is the point of deriving it from
  `string.punctuation` instead of listing the two hostile sets by hand.
* Structural rules: the name is not `.` or `..`; no leading `-` (it would collide with CLI flags)
  and no leading `.` (hidden/relative); no trailing `.` and no trailing whitespace (Windows);
  length 1-64.

⚑ **Uppercase ASCII is NOT blocked.** It is folded to lowercase by the `--name` invariant (R2)
BEFORE validation runs, so a name is validated post-fold and uppercase is never itself a violation.
Every entry point into this family assumes that fold has already happened.

⚑ The trailing-whitespace check is redundant with the per-character whitespace block above. It is
kept explicit anyway, because the Windows-portability intent is not recoverable from the general
check — a later edit that narrows the whitespace block would silently drop it.

### The three entry points

The blocklist has one implementation, `_box_name_violation`, returning a human-readable reason or
`None`. Three public wrappers exist because three callers need three different failure modes:

* `is_valid_box_name` — returns `True` when a name passes the §Design 8 box-name blocklist, and
  never raises. The non-raising companion to `validate_box_name`, for the "flag, don't reject"
  case: pre-existing non-conforming boxes still resolve, they just get warned about.
* `box_name_reason` — the reason string, for composing that warning message. `settings/paths.py`
  is its caller, on a project folder name.
* `validate_box_name` — raises `ProjectError`. Enforced at creation and at `--name`, i.e. on NEW
  names only (`commands/box/_lifecycle.py`, `commands/box/_parser.py`).

That split is the point of having three wrappers rather than one: refusing a NEW name costs the
user a retype, while refusing an EXISTING one would make a box already on disk unreachable — hence
the warn-only path.
