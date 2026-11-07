# XDG directories

`utils/xdg.py` owns base-directory resolution, runtime fallback, and host maps.
Signatures: [`api-docs`](../../../api-docs/kanibako/utils/xdg.py.md).

## Base directories

`resolve_xdg(var_name, spec_default_suffix)` honors a nonempty absolute environment
value, returning its resolved path. Relative values warn and use the default; empty
or unset values use the default. A supplied suffix is joined to `Path.home()`.
`xdg` is the spec-backed wrapper; `user_config_home` and `default_data_home` select
the config and data defaults from `settings.bootstrap.XDG_SPEC_DEFAULTS`.
Absolute values and spec defaults create no directories.

## Runtime fallback

With no suffix, `resolve_xdg` delegates to `_fallback_runtime_dir`. It first tries
`/run/user/<uid>/kanibako` when the parent is owned by us and writable. Otherwise it
tries the stable per-uid `kanibako-runtime-<uid>` directory under the temp root;
an untrusted stable entry causes a fresh `mkdtemp` directory instead.

`_runtime_base_usable` shares the ownership, directory, and write/execute-access checks.
Its defaults follow links and allow nonprivate modes for `/run/user`; the stable
entry instead uses `lstat`, refuses links, and requires no group/other access.
Only a newly created temp directory is chmodded to `0700`; preexisting entries are
never repaired or written through. Runtime selection can mkdir and always warns on
first selection, through the unchanged `kanibako.paths` logger and existing message templates.

`_runtime_fallback_cache` is process-local, keyed by `(variable name, environment value)`.
A cached directory is reused while it exists, avoiding repeated selection/warnings.
The stable name also permits reuse across processes without weakening its trust check.

## Host maps

`host_xdg_map(data_home=None)` is the canonical full `ResolveCtx.xdg` builder. Every
spec-backed variable comes from `resolve_xdg`; runtime resolution adds its fallback
and warning. The expression resolver reads the supplied map, never the environment.

`spec_default_xdg_map(data_home)` omits runtime resolution and creates nothing. Read-only
path queries and ancestor walks use it instead of triggering runtime mkdir/warnings.
Both builders honor a supplied, already-resolved data-home override; `None` reads the
live data environment. Bootstrap constants and warning templates retain their existing owners.
