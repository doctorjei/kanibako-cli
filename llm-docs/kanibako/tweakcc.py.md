# tweakcc — the binary transform, and the key that names it

`tweakcc` patches Claude Code's `cli.js` bundle. This module resolves and merges the config
(`resolve_tweakcc_config`, `build_merged_config`, `write_merged_config`); `tweakcc_cache.py` owns
the cache, its key, and its flock refcount; `commands/start.py::_apply_tweakcc` runs the patch
inside a throwaway container.

## `TRANSFORM_NAME` — why the word is spelled on both sides of the seam

`agent.<agent>.transform` (spec §2d, `agent.default.transform | <None>`) is the key naming
WHICH transform an agent runs. The launch gate compares the cascade-resolved value against
`TRANSFORM_NAME`.

Two DIFFERENT facts share the spelling `"tweakcc"`, and they are matched, not shared:

* **core implements a transform called `tweakcc`** — that is this constant;
* **the claude plugin declares that claude wants `tweakcc`** — that is the `transform` row of
  `claude-defaults.yaml`'s `behavior:` section (`default: tweakcc`), which the loader turns into
  the `TargetSetting` the plugin's `setting_descriptors()` returns. ⚑ The value is in the shipped
  FILE, not in plugin code, so a reader looking for the literal should open the YAML.

The plugin does NOT import this constant. Spec §0 makes every non-universal agent specific
PLUGIN-established. Plugins declare `kanibako-cli>=1.8.0.dev0,<2.0`, and the declaration stays in
the data file, so the plugin never depends on a core symbol for it.

## Three things share the word "transform" — keep them apart

| thing | what it is |
|---|---|
| `agent.<agent>.transform` | the KEY naming which transform runs |
| `agent.<agent>.transform_settings` | that transform's CONFIG INPUT (dict; `enabled`, `config`, inline overrides) |
| the `agent.<agent>.caches` entry at `~/.kanibako/cache/tweakcc` | its OUTPUT — the patched binary. An ENTRY of a terminal dest-keyed key, never a key of its own |

Unrelated despite the name: the claude plugin's `transform_cred` hook, a credential-file
CONTENT filter (`packages/agent-claude/src/kanibako/plugins/claude/credentials.py`).

## The gate

`transform_settings` is the INPUT, never the switch. `_apply_tweakcc` runs when the resolved
`transform` names this transform; `resolve_tweakcc_config({}).enabled` is `False`, so a claude
box that has never configured tweakcc still does no patching. A named transform this build
cannot run, and a `transform_settings` set with no transform naming it, are both WARNED — that
warning is what makes a lost plugin declaration loud instead of silent.
