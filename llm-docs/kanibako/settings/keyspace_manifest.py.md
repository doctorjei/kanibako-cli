# The keyspace registry reader — one parse, one copy, one artefact

`settings/keyspace_manifest.py` is a thin reader of the shipped `keyspace-manifest.yaml`, and it
is the ONE way anything in the tree reads that file. It does two things: parse the packaged
document once per process, and hand out a fresh deep copy of it on request. There is no
interpretation layer, no key lookup helper and no validation of the rows themselves — every
consumer walks the plain dict it returns.

## What the manifest IS

The manifest is the MACHINE-READABLE PROJECTION of `specs/settings-keyspace-1.8.0.md`: one row per
declared key, plus the category, bind-default and not-a-key tables. Its top-level sections are
`registry`, `policy`, `categories`, `bindmap_shape`, `keys`, `bind_default_entries`,
`category_default_entries`, `pref`, `not_keys` and `plugin_contributed`. It is RELEASE AUTHORITY —
it ships inside the wheel as package data, and its own header carries the ratification record and
the amendment log.

⚑ Some of its rows are MIGRATION DATA rather than a description of today's keyspace — the
retired-key chains that record what a key used to be spelled. They read like leftovers and are
not: deleting one loses the only record of a rename. The manifest's own header and
`directives/PROJECT.md` are the authority for that; this module simply hands the whole document
back untouched.

## It declares NOTHING at runtime

Nothing on the launch or resolve path consults the manifest. The keyspace's LIVE carriers are the
`DECLARED_*` frozensets in `kanibako.settings.settings_keyspace` and the shipped
`core-defaults.yaml`. The manifest's job is to be ASSERTED AGAINST those carriers by
`tests/test_settings/test_manifest_conformance.py`, which is a different relationship from being
their source.

⚑ **Generating the frozensets FROM the manifest was measured and DECLINED** (2026-08-15), and the
manifest's own header records the decision. Generation would put a 3000-line YAML parse on the CLI
hot path, destroy the load-bearing per-entry commentary inside the `DECLARED_*` frozensets, and
narrow nothing that `key_validity` already refuses. TWO CARRIERS, ASSERTED EQUAL: set-equality
catches the same drift one gate-run later at none of that cost. The header used to promise
generation; it no longer does.

The only in-tree runtime consumer is `settings/defaults_inventory.py`, a read-only display of what
kanibako declares out of the box — a listing, not a resolution.

## The parse is CACHED, and `core_defaults._load_doc`'s is not

The difference between the two thin loaders is deliberate, not an oversight:

  - `core-defaults.yaml` carries LIVE DEFAULTS whose tests monkeypatch the reader, so re-reading
    per call is load-bearing there.
  - The manifest is IMMUTABLE PACKAGED DATA. No code path writes it and no test rewrites it, and
    parsing its 3000-odd lines costs ~137 ms — so `_parse_manifest` is an `lru_cache(maxsize=1)`
    and the parse happens once per process.

Do not "harmonise" the two by removing this cache or by adding one to `core_defaults`; each shape
answers a property of the file it reads.

## It reads the INSTALLED artefact, never the checkout

`_parse_manifest` resolves its path through `kanibako.settings.core_defaults.packaged_data_dir` —
the ONE `importlib.resources.files()` join in the tree. That is what makes the conformance suite a
statement about the artefact that SHIPS. A guard that read a repo-relative path would instead be a
statement about this working tree, which is the wrong subject: it would pass on a developer's
machine while the wheel shipped a stale manifest.

A missing or non-mapping document raises `RuntimeError` naming the filename and the resolved
reference, and says so in the message: the registry is packaged data, so an empty or malformed
read is a PACKAGING defect, not a configuration one. There is no fallback and no empty-dict
default — a keyspace registry that silently read as `{}` would make every conformance assertion
vacuously true.

## Copy out at the boundary (P8)

`manifest_doc()` returns `copy.deepcopy(_parse_manifest())` — a FRESH COPY every call. The parse is
shared; the document is not. A caller that mutated a shared dict would corrupt every later reader
in the process, and because the parse is cached, that corruption would OUTLIVE the test that caused
it — a failure that surfaces in an unrelated test file and points nowhere near its cause.
`_parse_manifest`'s own return value is the cached original and is never handed out.

`tests/test_settings/test_manifest_conformance.py` pins all three properties together: that
`manifest_doc()` equals the shipped file, that two calls are not the same object, and that
clobbering a value in one returned document does not reach the next one.
