# Category entries — mount options

⚠️ **PARTIAL MIRROR.** `settings_categories.py` is a large module (the category dataclasses, the
reconcile ladder, the collision table). Only the parts whose prose has been migrated out of source
appear here; absence of a symbol below means "not migrated yet", never "does not exist". Migration
happens as files are touched, not in a big-bang pass.

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
