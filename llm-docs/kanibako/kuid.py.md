# kuid — the 25-bit short id, its parity bit, and the sentinel that falls out of it

`kanibako.kuid` is a self-contained Crockford-base32 codec. A **kuid** is a compact,
human-typable box identifier: a 25-bit value packed into exactly five Crockford-base32
characters, lowercased. The module is a codec and nothing else — it mints, encodes, decodes,
folds and validates values, and it knows nothing about what they identify.

## The module is deliberately PURE

It is stdlib-only (`os`, `time`) with ZERO `kanibako` imports, so it can later be extracted
into a standalone library. Nothing kanibako-specific — box, workset or registry concepts —
belongs here. That constraint is the reason the sentinel's call-site contract below reads the
way it does: this module cannot know which callers are allowed to hold a sentinel, so it
refuses to guess.

## Bit layout

With bit 24 the MSB and bit 0 the LSB:

```
  bits [24:15]  (10 bits)  milliseconds within the current second (0-999)
  bits [14:1]   (14 bits)  uniform random
  bit  [0]      ( 1 bit )  ODD parity over the other 24 bits
```

`_MS_BITS` (10) + `_RANDOM_BITS` (14) + the one parity bit == `BITS` (25), and 25 bits is
exactly `CHARS` (5) base32 characters at 5 bits each.

**ULID shout-out.** Like a ULID this is time-prefixed plus random, and therefore sortable
within a second — but shrunk to 25 bits / 5 chars for a terse, typo-tolerant id.

## The parity bit is what creates the sentinel

`generate` chooses the parity bit so the popcount of the whole 25-bit value is ODD (>= 1).
A generated value can therefore NEVER be all-zero, and that reserves the all-zero encoding
`"00000"` as `SENTINEL`.

The sentinel is not a kuid. It is the "no kuid stored here" marker — the default that
`settings/config.py::read_workset_kuid` returns when `workset.kuid` is absent, which is what a
pre-kuid box looks like.

### ⚑ PRESENT-SENTINEL is not INVALID — the distinction callers must keep

`is_valid(SENTINEL)` is **False**, because all-zero has even parity. That is correct and
intended, and it is the one thing about this module that is easy to get backwards:

* **sentinel** means *no kuid was ever stored* — an ordinary, expected state;
* **invalid** means *something is stored and it is wrong* — a real anomaly.

`is_valid` collapses both into `False` because a pure codec has no basis for telling them
apart. So **the sentinel is exempted at the CALL SITE, never special-cased inside
`is_valid`/`decode` here.** The canonical shape of that exemption, in
`settings/paths.py::_flag_invalid_kuid`, tests the sentinel FIRST and only then asks
`is_valid`:

* `value != kuid.SENTINEL` — a pre-kuid box is skipped, not warned about;
* `not read_workset_skip_kuid_check(...)` — `workset.skip_kuid_check` defaults to `True`, so
  checking is OFF unless a user turns it on;
* `not kuid.is_valid(value)` — only now is the stored value judged.

Collapse that first clause into the third and every pre-kuid standalone box starts reporting
a corrupt identity. The check is advisory and never fatal either way: the box still resolves,
and the warning tells the user to fix `workset.kuid` or set `workset.skip_kuid_check`.

## Who reads a kuid

Standalone boxes only. `launch/box_resolve.py` composes a standalone box's LIVE name as
`<stored workset.kuid>_<live dir leaf>`: the kuid is the stable stored prefix and the leaf
tracks the current directory, so a MOVED box keeps its identity. `project/import_reconcile.py`
mirrors that composition, and falls back to the bare leaf when the stored value is `SENTINEL`.
`commands/box/_duplicate.py` mints a FRESH kuid for a duplicate rather than copying the
source's.

## Codec behaviour

**`encode`** writes MSB-first: char `i` holds bits `[5*(CHARS-1-i) .. 5*(CHARS-1-i)+4]`. It
raises `ValueError` unless `0 <= value < 2**BITS`.

**`decode`** canonicalizes its argument first, then maps each char through `ALPHABET`. It
raises `ValueError` on the wrong length, or on a char that is not in the canonicalized
alphabet.

**`canonicalize`** folds user input toward canonical form using Crockford's input rules. It
lowercases; maps `o` to `0` and `i`/`l` to `1`; strips `-`, since Crockford treats hyphens as
ignorable separators. `ALPHABET` is the matching output alphabet — digits `0-9` then `a-z` minus `i`, `l`,
`o` and `u`, the four letters Crockford drops as confusable.

⚑ Canonicalizing is folding, not judging. The result MAY still be an invalid length or
charset; that judgment belongs to `is_valid`/`decode`.

**`is_valid`** is true iff the input canonicalizes to five in-alphabet chars with ODD parity.
It answers `False` rather than raising, so it is safe on arbitrary user input.

**`generate`** builds `ms(10) | random(14) | odd-parity(1)` and encodes it. Its invariants:
always 5 chars, always `is_valid` True, never `SENTINEL`.
