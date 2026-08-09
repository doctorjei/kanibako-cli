# Config Keys — the CLI-settable surface & its refusals

⚠️ **PARTIAL MIRROR.** `config_keys.py` is a large module (the closed keyspace's settable-surface
tables plus every CLI refusal). Only the parts whose prose has been migrated out of source appear
here; absence of a symbol below means "not migrated yet", never "does not exist". Migration happens
as files are touched, not in a big-bang pass.

## The refusal family

A CLI verb (`set` / `reset` / `get`) that will not serve a key returns an ERROR STRING naming three
things: the **route** that is closed, the **reason** it is closed, and a **cure that actually
works**. Neighbouring members of this family — `scope_bind_retired_error`,
`agent_node_bind_retired_error`, both built on `_bind_route_retired_message` — hold to the same
shape, so a user who hits two of them reads one story rather than two.

⚑ **The refusal states the verb the USER RAN, not the verb the refusing branch happens to think
about.** Every member takes a `verb` as a REQUIRED keyword — never a defaulted one — for the reason
`_bind_route_retired_message` makes `survives` required: a default lets one door silently inherit
another door's word. Telling someone their `get` failed because the key "is not settable" names an
operation they did not run and mis-describes what failed.

## Functions

```system_key_refusal(key: str, *, verb: str) -> str```
Refuse a CLI *verb* (`set` / `reset` / `read`) on a FILE-ONLY `system.*` key.

STRUCTURAL `system.*` path-tier keys (the `SYSTEM_PATH_DEFAULTS` family — see `is_system_path_key`)
are LAYOUT config, not behavior settings, so they are file-only: reachable in the bootstrap config
file (or via `kanibako setup`) but never through `config set` / `config reset` / `config get`.

The message points at the REAL RESOLVED config file — the `kanibako_config.yaml` `[system]` table
that `resolve_system_paths` actually reads — and never at the command scope's settings file, which
would be wrong-file advice (the F2 lesson). The path is rendered by `_user_config_file_str`, which
is raise-proof: this is an error path and must not turn a clean refusal into a traceback.

* `verb="set"` / `"reset"` — tail is *"Edit the config file directly: `<path>` (or re-run 'kanibako
  setup')."*
* `verb="read"` — tail is *"Its value lives in the config file: `<path>`"*, and it deliberately
  does NOT name `setup`. ⚑ `setup` is a WRITE cure; prescribing it to someone who ran `get` is the
  F6 lie (`_config_key_refusal` omits it for the same reason).

⚑ **KNOWN DELTA, NOT FIXED HERE (2026-08-09).** The manifest declares every `system.channels.*` key
`set: cli+file`, yet this refusal closes the CLI half at all three verbs — and `get_config_value`
can in fact serve the read (it resolves them from the config file before the slot rule is
consulted), so the `get` is refused by `system_cmd`'s `is_known_key` gate rather than by any
inability. Opening it is a user-facing POLICY decision, not a wording one; the verb fix above
deliberately did not take it.

## The `workset.channels.*` family

All six declared leaves (`common`, `chat`, `share`, `broadcast`, `mailboxes`, `share_global` —
`settings_keyspace.DECLARED_WORKSET_CHANNEL_LEAVES`, spec §2c) are `set: cli+file` STRING paths with
no `KEY_TYPES` entry, routed to the SAME `workset: channels:` nested slot, which is where
`settings_assemble._file_partial` reads the whole table into the cascade.

⚑ Three of them — `broadcast`, `mailboxes`, `share_global` — were absent from `KNOWN_CONFIG_KEYS`
and `_KEY_ROUTES` until 2026-08-09, so a CLI `set` answered "unknown config key" while a
hand-authored value read back "(not set)": both halves of a declared key broken, in opposite
directions, which spec §0 forbids. Stating the family as a whole (rather than patching the three)
is what stops a seventh leaf arriving unrouted;
`tests/test_settings/test_config_dest_parity.py::TestChannelTypeRootsRouteUNIFORMLY` pins it.
