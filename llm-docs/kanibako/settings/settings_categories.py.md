# Category entries — the declaration list, mount options

⚠️ **PARTIAL MIRROR.** `settings_categories.py` is a large module (the category dataclasses, the
two §0 refusal texts, the launch seam's delivery carrier). Only the parts whose prose has been
migrated out of source appear here; absence of a symbol below means "not migrated yet", never "does
not exist". Migration happens as files are touched, not in a big-bang pass.

## `ReconciledCategories` is GONE, and so is the pass that returned it

Cutover 6-R3 deleted `reconcile_categories`, `ReconciledCategories` and the three group resolvers
beneath them. There is no single cross-scope pass any more. §0's table is applied by the per-scope
`store_shape` PRODUCER (rows 3, 5 and row 1 SAME-scope), by the assembly COLLAPSE (rows 2, 4 and
row 1 cross-scope), and by this module's two launch-seam functions for the inputs the collapse
cannot see — `secret_path_deliveries` (a `secret_path` dest has no arm in the store shape) and
`narrow_table_winners` (a narrow resolve, where the collapse returns early).

⚑ **The class went for the same reason its `warnings` field went before it.** That field carried the
§0 row-5 ambiguities until cutover 5-1c, when the per-scope producer became the sole builder; two
feeds of one channel printed one line only because both arms happened to build an EQUAL
`CategoryCollision` and the emitter memoises on `(box_dest, scope)` — a property of the two
constructions, not of the channel. Making a second implementation UNAVAILABLE rather than unused
(P3) is what stops it drifting back; re-adding one now means re-adding a function and a type, which
is a visible design act. Full reasoning and the mutation proof:
`llm-docs/kanibako/commands/start.py.md`, "The row-5 warning lives HERE, and ONLY here".

⚑ **`CategoryCollision` itself still lives in this module**, and that is not leftovers: its message
is spec §0's, written once, beside the two refusal texts (`raise_binding_vs_binding`,
`raise_extension_onto_occupied`) that the producer also raises. `store_shape` imports all three from
here. It is DEFINED here and BUILT there.

⚑ **Row 5's BEHAVIOUR is unchanged in this module.** "Proceed on the existing ordering" is still
`_resolve_mount_group`'s `_most_specific` pick, which is the same pick row 4 makes. Only the
announcement moved. Rows 1 and 3 still RAISE here, and the row-2 mask override still applies here —
none of that is 5-1c's.

## Mount options: one writer, one reader

`CategoryEntry.options` is a podman mount-option string. Exactly two functions know its grammar,
and they are a pair:

```_bind_options(category: str) -> str```
The category-default table — `bindings.ro` is `"ro"`, every other rw bind category
(`bindings.rw`, `caches`, `common`) gets `"Z,U"` (SELinux relabel + userns chown).

```is_read_only(options: str | None) -> bool```
True when *options* carries `ro` as a comma-separated TOKEN. `None` and `""` are False.

⚑ **`is_read_only` is a TOKEN test, never a substring test, and never an equality test.**

* **Not equality.** Two call sites used to ask `options == "ro"` directly — the launch path
  (`commands/start._emit_category_mounts`) and the `workset` binding display
  (`commands/workset_cmd`). That was correct only for as long as `_bind_options` emits exactly one
  of two literals. The collapse folds options into a comma list, and the moment a read-only entry
  is spelled `"ro,Z"` an equality test answers *rw*. In the launch path that answer is not a
  cosmetic mis-label: the rw arm is L7 **guarantee-create**, so a missing read-only source would be
  `mkdir`'d instead of dropped with a warning. The flip keeps arity, passes mypy, and turns no
  existing test red — which is exactly why the predicate exists rather than a second literal
  comparison.
* **Not substring.** `nodirop` and `prod` both CONTAIN `ro`. A substring test calls them read-only
  and skips the guarantee-create. `tests/test_seed_hostdest.py`
  (`TestReadOnlyIsDecidedByTokenNotEquality`) pins that negative on the live launch path;
  `tests/test_settings/test_mount_options.py` pins the predicate itself.

⚑ **Ask this question through the predicate, not by hand.** A new `== "ro"` anywhere is the same
defect re-introduced under a different name.
