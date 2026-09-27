# Settings Drops — which scope tables a settings file may carry
_spec §0 directional enforcement and §2h's `pref:` site rule, derived once from `SCOPE_CONTAINMENT`_

`settings_drops` derives, from the containment order `system ⊃ agent ⊃ workset ⊃ box`
(`kb_store.SCOPE_CONTAINMENT`), which top-level scope tables a settings file may carry and which
scopes a command may write. It holds no state and reads no file.

## Why it is its own module

The containment rule has readers on opposite sides of import edges: `settings_assemble` (the
cascade, which drops and warns), `config_keys` (the `config set` direction guard) and
`agent_file` (the per-agent file's stray-root refusal). The assembler imports `agent_file`, so the rule cannot
live in the assembler. Here, below all of them — it imports only `kb_store` and `settings_prefs` —
it has one derivation, and every reader imports it. Before this module, `config_keys` sliced the
containment order itself and `settings_assemble` kept a private copy of the same rule: two
derivations of one fact.

## Functions

```containing_scopes(file_scope: str) -> frozenset[str]```
The scopes that CONTAIN *file_scope*: the head of `SCOPE_CONTAINMENT` before it. `system` has
none. A top-level table naming one of these in a *file_scope* file is an UPWARD write the cascade
drops. Raises `ValueError` for a token outside the order (`base`); `upward_scope_drop_set` guards
that case.

```contained_scopes(file_scope: str) -> tuple[str, ...]```
The scopes *file_scope* CONTAINS: the tail of `SCOPE_CONTAINMENT` after it, in order. A file's
tables for these are overridable defaults, never drops (spec §0).

```writable_scopes(level: str) -> frozenset[str]```
The scope tokens a *level* command may WRITE: *level* itself plus `contained_scopes(level)`.
`config_keys._SCOPE_WRITE_ALLOWED` is this, per `ConfigLevel`; its readers are
`config_keys._scope_direction_error` (the `set` refusal) and
`config_interface._clear_writable_tables` (which tables `reset --all` clears). For every scope,
`writable_scopes` and `containing_scopes` partition the order.

```upward_scope_drop_set(file_scope: str) -> frozenset[str]```
The top-level tokens directional enforcement removes from a *file_scope* file: the containing
scopes, plus `meta` and the reserved derivations node (`kb_store.BINDING_DERIVATIONS_NODE`) at
every level. `base` is not in the order and takes no containing scopes. This is the RULE without
the warning; `settings_assemble._drop_upward_scopes` and `_warn_upward_drops` add the warning.

```cascade_drop_set(level: str) -> frozenset[str]```
Every top-level token the cascade drops from a *level* file before the merge:
`upward_scope_drop_set(level)`, plus `pref` wherever §2h forbids writing a request
(`settings_prefs.PREF_LEGAL_LEVELS`). `settings_assemble.cascade_view` filters with it. The
per-agent file's own contribution rule (root table only) is `cascade_view`'s, not this set's.
