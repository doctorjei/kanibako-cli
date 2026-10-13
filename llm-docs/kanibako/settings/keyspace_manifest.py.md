# The keyspace registry reader — one parse, one copy, one artifact

`settings/keyspace_manifest.py` is a thin reader of the shipped `keyspace-manifest.yaml`, and it
is the ONE way anything in the tree reads that file. It does two things: parse the packaged
document once per process, and hand out a fresh deep copy of it on request. There is no
interpretation layer, no key lookup helper and no validation of the rows themselves — every
consumer walks the plain dict it returns.

## What the manifest IS

The manifest is the MACHINE-READABLE PROJECTION of `~/canon/workbook/specs/settings-keyspace-1.8.0.md`: one row per
declared key, plus the category, bind-default and not-a-key tables. Its top-level sections are
`registry`, `policy`, `category_scopes`, `categories`, `bindmap_shape`, `keys`, `bind_default_entries`,
`category_default_entries`, `pref`, `not_keys` and `plugin_contributed`. It is RELEASE AUTHORITY —
it ships inside the wheel as package data, and its own header carries the ratification record and
the amendment log.

⚑ Some of its rows are MIGRATION DATA rather than a description of today's keyspace — the
retired-key chains that record what a key used to be spelled. They read like leftovers and are
not: deleting one loses the only record of a rename. The manifest's own header and
`~/canon/notebook/directives/PROJECT.md` are the authority for that; this module simply hands the whole document
back untouched.

## It declares no key at runtime

The launch reads one thing from the manifest: the declared default of an unset
`@workset.<referent>` (`workset_dirkeys._declared_default`). The keyspace's LIVE carriers are the
`DECLARED_*` frozensets in `kanibako.settings.settings_keyspace` and the shipped
`core-defaults.yaml`. The manifest's job is to be ASSERTED AGAINST those carriers by
`tests/test_settings/test_manifest_conformance.py`, which is a different relationship from being
their source.

⚑ **The frozensets are not generated FROM the manifest**, and the manifest's own header says so.
Generation would put a 3000-line YAML parse on the CLI hot path, destroy the load-bearing
per-entry commentary inside the `DECLARED_*` frozensets, and narrow nothing that `key_validity`
already refuses. TWO CARRIERS, ASSERTED EQUAL: set-equality catches the same drift one gate-run
later at none of that cost.

The other runtime readers are `settings/defaults_inventory.py`, a read-only display of what
kanibako declares out of the box, and `settings/kinemata_keyspace.py`, which builds the
conformance matchers.

## The parse is CACHED

Both thin loaders cache the parse (`lru_cache(maxsize=1)`) and hand out a deep copy (P8):
`_parse_manifest` here, `core_defaults._parse_doc` there. The manifest is IMMUTABLE PACKAGED DATA —
no code path writes it and no test rewrites it — so the parse happens once per process.

## It reads the INSTALLED artifact, never the checkout

`_parse_manifest` resolves its path through `kanibako.settings.core_defaults.packaged_data_dir` —
the one join into `kanibako.data`. That is what makes the conformance suite a
statement about the artifact that SHIPS. A guard that read a repo-relative path would instead be a
statement about this working tree, which is the wrong subject: it would pass on a developer's
machine while the wheel shipped a stale manifest.

An empty or non-mapping document raises `PackagingError` (a `KanibakoError`) through
`config_io.load_packaged_mapping`, naming the filename and the resolved reference, and says so in
the message: the registry is packaged data, so an empty or malformed
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
