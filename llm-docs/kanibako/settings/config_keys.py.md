# Config Keys — the CLI-settable surface & its refusals

⚠️ **THE MIRROR IS NOW COMPLETE FOR DISPLACED PROSE** (relocation pass, 2026-08-11). It began as a
PARTIAL mirror of a large module (the closed keyspace's settable-surface tables plus every CLI
refusal); the pass migrated the rest, so every symbol that CARRIED explanatory prose now has an entry
below. Absence of a symbol still means "nothing was displaced from it", never "does not exist" — the
one such symbol today is `ConfigLevel`, whose one-line docstring says all there is. Source keeps
one-line descriptors and `⚑` markers only; the reasons live here.

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

```SETUP_MARKER_KEY = "system.setup_completed"```
The setup VERSION MARKER's key, spelled once.

⚑ **`system_key_refusal` STOOD HERE AND IS DELETED (2026-08-23).** It refused a CLI verb on a
FILE-ONLY `system.*` key — *"'<key>' is a structural config key and cannot be {set,reset,read} from
the CLI"* — and by the end it named an EMPTY SET. The `SYSTEM_PATH_DEFAULTS` family left the
category first (spec §2g declares all eleven ordinary Layer-2 settings keys; §2a names
`system.template` in the CLI-settable list beside `workset.vault_{ro,rw}`), and the setup marker
left it the same day. Its last reachable caller was `system_cmd`'s `get` arm, where the only
spellings still falling into it were UNDECLARED `config.*` ones — for which *"is a structural config
key … its value lives in the config file"* asserts that a key which does not exist DOES. Those
answer `unknown config key` now, which is what spec §0 requires of an undeclared name. 🛑 Do not
reintroduce it for `config.*`: those have `_config_key_refusal`, which spec §2a requires NOT to
mention `setup` — *"`setup` … is NOT how a value is set"*.

**Why the marker's verbs route to the SYSTEM SETTINGS file.** Spec §2g declares
`system.setup_completed` a `system`-tier SETTINGS key and calls it *"PERSISTS, user-resettable"*;
the registry marks it `set: cli+file`. It is stored, written and read as one: `setup` writes it to
`@config.settings` (`setup_cmd._write_setup_marker`) and the 5-band staleness gate reads it from
there (`config.read_setup_completed`), so it routes through `_KEY_ROUTES` beside `system.agent`.

⚑⚑ **THE STORAGE DELTA IS CLOSED (2026-08-26), AND IT IS WHY THE BOOTSTRAP FILE RULE IS GONE.**
Until then the code kept the marker in the `system:` table of `kanibako_config.yaml`, and the verbs
followed that STORAGE through a bespoke destination rule (`config_dest._BOOTSTRAP`, whose only
member it was) — a settings-file route would have been INERT, accepted and invisible to every
consumer. Jei closed it from the other side by moving the storage: *"there is no reason whatsoever
that `system.setup_completed` should go in the config. It should not. It should go in the global
settings file."* The invariant never moved — set, get, reset and the shipped reader name ONE file;
which file changed.

⚑ **THE MOVE COST NOTHING TO MIGRATE.** A marker in the old location simply reads absent, which is
the gate's NON-BLOCKING nudge band: the advisory goes to stderr and the command proceeds unchanged
(measured). There was no deployed installed base to carry — a documentation-only migration, in
keeping with 1.8.0's clean break.

⚑ **THE REFUSAL'S OWN CURE WAS THE ARGUMENT AGAINST IT.** It told the user to hand-edit the config
file's `system:` table — the same table, with the same absence of validation, that the CLI now
writes. Refusing bought no safety; it only withheld `reset`, which is the half spec §2g explicitly
promises.

⚑ **THE `system.channels.*` DELTA (opened 2026-08-09) IS CLOSED (2026-08-23).** The manifest
declared every one of them `set: cli+file` while the old refusal shut all three verbs; all five
leaves are routed and known now, exactly like the `workset.channels.*` six.

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

## What the module owns

`config_interface` exposes four verbs (get / set / reset / show) over a key surface whose families
are recognised by SPELLING: ``pref.<target>``, ``agent.<node>.<leaf>``,
``<scope>.<category>.<name>``, ``<scope>.secret_path.<VAR>``, the bare agent behaviour keys, the
routed scalars, the structural ``system.*`` path tier. This module owns that classification — the
recognizers, the parsers, the per-family display spellings and refusal texts, the scope tables, and
the routing table — so the verbs can dispatch instead of each re-deriving what a key is.

The distinction is the whole reason the module exists: the classification was smeared across
`config_interface` as per-family free-function quintets (recognizer · parser · display · error ·
target) that each verb re-dispatched over independently. Collecting them here makes the family
structure a fact you can see rather than a discipline four verbs have to keep. The KeyKind rewrite
collapses each quintet into one descriptor IN THIS MODULE — and that rewrite has the same constraint
stated below: descriptors own the CLI-facing surface and call into the resolver/keyspace for key
semantics, or the fix manufactures the very duplication it was meant to remove.

### ⚑ NOT the keyspace validator, and never a second one

Two different questions look alike here and must stay apart:

* *"Is this a DECLARED key?"* — spec §0's CLOSED KEYSPACE. That question has one authority,
  :mod:`kanibako.settings.settings_keyspace`, and one answer. The CONSTRAINT on this module is that
  it must reach that authority rather than grow its own answer: no second copy of the key set for
  the purpose, no "not a key" decided here.
  ⚑ Stated honestly: today this module does not import the validator at all. It reaches it
  INDIRECTLY — `_pref_target_error` calls `settings_prefs.validate_pref`, which is where
  `key_validity` / `is_valid_agent_segment` are applied — and every other recognizer here is a SHAPE
  test that deliberately decides nothing about declaredness. So the rule above is a constraint to
  hold, not a description of an existing call; the KeyKind rewrite is where descriptors take the
  direct dependency.
* *"Which CLI-surface FAMILY is this spelling, and which file and nested slot does it map to?"* —
  this module.

### Layering

This module sits BELOW `config_interface` and `config_dest` and above nothing but the keyspace
primitives, so it must not import either of them.

## The QUARANTINE — `KNOWN_CONFIG_KEYS`

`KNOWN_CONFIG_KEYS` is a HAND-MAINTAINED list answering exactly one question: "does this positional
argument LOOK like a config key rather than a project name?" It is not the closed keyspace (that
authority is `settings_keyspace` — see above), it is not DERIVED from anything, and nothing keeps it
in step with the declarations.

⚑ SO IT IS INCOMPLETE, AND THE INCOMPLETENESS IS DELIBERATE. Every bind-shaped category —
``<scope>.bindings.ro`` / ``bindings.rw`` / ``caches`` / ``common`` / ``seeded`` / ``synced`` — is a
DECLARED TERMINAL key whose value is a dest-keyed map, and none of the six is listed in the set, so
`is_known_key` answers `False` for all of them at every scope. ``<scope>.masks`` answers `False` too
(it is a LIST, not a map — a different shape, the same status). All seven are MULTI-FACETED: one
key, many facets inside one value.

⚑ JEI'S RULING (2026-08-08) — the reason nothing in the set changes:

> "I don't think it makes sense to be able to read out or write to an individual key that is
> multi-faceted.  So, don't kill the code, but quarantine it.  We will make a way for it to be
> readable, but for now consider it a 'promise' whose form is unknown (and put it on the backlog).
> That goes for all of the bindings, which now have values of dict() at their keys."

Read out of that, for anyone standing here:

* Reading or writing ONE FACET of a multi-faceted key IS NOT SUPPORTED. The facet's dest is DATA
  inside the value, not a key — and there is no settled surface for the whole map either.
* A readable form is a PROMISE WHOSE SHAPE IS UNDECIDED, on the backlog. Guessing that shape now is
  precisely what the ruling refuses.
* ⚑⚑ DO NOT "FIX" THIS BY DERIVING THE PREDICATE from the declaration SoT. Derivation would make
  ``system get box.caches`` start answering, and would thereby BUILD the surface the ruling has not
  chosen. It was proposed and DECLINED.

The visible cost, recorded so nobody re-discovers it as a bug: the one-positional ``kanibako box get
box.caches`` is read as a PROJECT NAME. That message is wrong, it is KNOWN to be wrong, and the cure
is the promised surface — never a wider list. (The scope nouns' two-positional reads, ``kanibako
box get <box> box.caches`` and its workset/agent siblings, are not gated by this set and do return
the map today. That is where the behaviour currently lives; it is an accident of which door checks
this set, not the chosen design, so do not build on it either. ⚑ Those two doors are no longer
UNGATED — :func:`scope_read_key_error` closes §0's read there — but they still pass ``box.caches``,
because it asks ``key_validity``, which DECLARES it. Declining to invent a refusal for a declared
key is not a decision about the promised map surface, and this paragraph still governs that.)

⚑⚑ **``system get`` JOINED THOSE DOORS ON 2026-08-28, AND THE ASYMMETRY IT LEAVES IS THE ONE TO
KNOW.** It carried an ``is_known_key`` PRE-GATE ahead of :func:`scope_read_key_error`, so it alone
answered "unknown config key" for all seven — a SECOND vocabulary, telling a user that a declared key
does not exist, which §0 forbids as squarely as it forbids fabricating a value. The pre-gate is gone
and the verb now asks ``key_validity`` like its siblings, so ``kanibako system get system.caches``
returns the map. **Read what that did and did not decide:**

* It removed a wrong refusal at the key's OWN noun; it did not CHOOSE the readable form. The
  `repr()` is what falls out of the generic scalar renderer, not a designed rendering, and
  🛑 **THE PROMISED SURFACE IS STILL UNCHOSEN AND STILL JEI'S** — this paragraph governs it.
* 🛑🛑 **CROSS-SCOPE IS OUT, AND THE FIRST CUT OF THIS CHANGE GOT IT WRONG.** Removing the pre-gate
  ALSO made ``system get box.caches`` answer — the exact outcome the DECLINED derivation would have
  produced. **Jei, 2026-08-28: *"i dont see any justification for crosscope 'get'. it makes no sense
  at the cli."*** It is refused again, by :func:`foreign_scope_read_error`. ⚑ THE LESSON FOR THE
  NEXT READER: the evidence offered for the first cut — *"the sibling nouns already print the
  map"* — was a SAME-SCOPE measurement (``workset get <ws> workset.masks``) used to argue a
  CROSS-SCOPE case. **Two different surfaces; the 2026-08-08 decline names the cross-scope one.**
  Weigh the OUTCOME as well as the mechanism, and check which surface a measurement is actually of.

**THE CROSS-SCOPE GATE'S BASIS — THREE WERE TRIED, TWO ARE DEAD.** The full statement lives in
:func:`foreign_scope_read_error`'s docstring; it is summarised here because the two dead ones are
what a reader re-derives.

1. **THE WRITE ROUTE** (`has_no_cli_write_route`) — selects the right rows, REJECTED anyway. It
   derives a `get` gate from whether `set` works, and the two verbs are held apart on purpose:
   **Jei — *"set is different tho"*, and *"i did not say anything about set. set is different, i
   said, specifically."*** Right rows, wrong reason: if `set`'s rules move, `get` follows for a
   reason that has nothing to do with reading.
2. **"DOES THIS NOUN'S OWN FILE CARRY THE KEY?"** — REJECTED BY MEASUREMENT, and the most likely
   to be re-derived because it sounds obviously right. **It distinguishes nothing: a higher tier
   carrying a lower scope's key IS the cascade.** A `box.<category>` table authored in the SYSTEM
   settings file is not inert — through `build_launch_snapshot` → `snapshot_category_entries`,
   with the system file the ONLY file supplied, **21 of 21** combinations reached the box (seven
   categories × three scope tokens), e.g. `CategoryEntry(category='caches', scope='box',
   box_dest='/home/agent/.dflt', host_src='/host/dflt', delivery='MOUNT')`. So the answer is YES
   for `box.caches` at the `system` noun, and the gate would pass the very read the ruling forbids.
   🛑 A corollary worth keeping: **the message must not tell a user this noun cannot STORE the
   key** — it can, and it does.
3. **THE FRAGMENT BASIS** — ADOPTED. A terminal category key is **merged per entry across tiers**
   (spec `:1085`), so one tier's copy is a FRAGMENT, never the value. At its OWN noun the fragment
   is a complete statement of that scope's contribution (R-9's honest read — `system get
   system.caches` stays); at a FOREIGN noun it is a partial map no box sees. A SCALAR is held
   whole by one tier, so `system get box.image` is a complete answer and stays. `get`-native,
   never mentions `set`, derived from `TERMINAL_CATEGORY_TAILS`.
* 🛑 **THE DECLINED FIX IS STILL DECLINED.** Do NOT derive :data:`KNOWN_CONFIG_KEYS` from the
  declaration SoT. What the set still bounds is a PARSER's project-name disambiguation, where
  widening it is not the cure for anything.
* The **WRITE** side is untouched: ``system set system.caches=…`` still refuses, and spec §2a's
  "EVERY BIND-SHAPED CATEGORY → YAML ONLY" is a rule about ``set`` and ``--reset``, which is why the
  read could move without it.
* 🛑🛑 **IT ALSO UN-GATED THE *AGENT*-SCOPE TERMINALS, WHICH IS A DIFFERENT AND WORSE CASE — see
  :func:`agent_category_read_error`.** ``agent.<node>.caches`` is declared too, but its value lives
  in ``agents/<node>/agent.yaml``, which the file-scope nouns never open, so falling through
  answered "(not set)" over a table on disk. ``is_known_key`` had been holding that line by
  accident. **The lesson generalises: this predicate's `False` answers were load-bearing in places
  nobody had enumerated, so removing a gate that consults it needs the un-gated set MEASURED, not
  reasoned about.** The replacement refusal names ``kanibako agent get <node> <category>``, which
  does serve the read.

⚑ The quarantine COMMENT travels with the set — do not copy the hand-maintained pattern anywhere
else, and do not split the block from the data.

### The inventory, key by key

The notes below travelled out of the set literal itself; each names WHAT the key is, WHERE it is
read back, and (where one exists) the RETIRED spelling it replaced.

**The bare agent behaviour keys** (`model`, `allow_helpers`, `access`, `endpoint`, `bootstrap`,
`continue_mode`) are all the same shape: the BARE key is the any-agent `agent.default` tier, and a
per-agent override is the persona key `agent.<agent>.<key>` (spec §2d).

* `allow_helpers` — spec §2d ``agent.default.allow_helpers | true``. Gates the helper
  hub/socket/listener at launch (start.py). Was a flat scopeless top-level scalar (1.7.0-rc clean
  break — no back-compat for the old bare-config-field form).
* `access` — the agent-scope PERMISSION TIER, spec §2d ``agent.default.access | full``, enum
  ``restricted|editing|full``. Redeemed by each descriptor's `access_realization.setting_key` at
  launch (claude/codex FLAG rows, goose GOOSE_MODE ENV rows) and by the PROJECTED surfaces; the
  per-launch ``-S``/``-A`` flags override it for the ARGV only (``-S`` ⇒ restricted, ``-A`` ⇒ full),
  never for the projection (spec §1A projected-surface exception). SUPERSEDES the boolean
  `auto_approve` (R-41, 1.8.0: mapping true→full / false→restricted; a stored `auto_approve` is
  REFUSED at launch by `settings_assemble.refuse_retired_behavior_keys`, never mapped silently). An
  UNKNOWN value is rejected at SET time (:func:`access_value_error`) and at launch — never treated
  as permissive.
* `endpoint` (persona) — alternate harness base-URL, a sibling of `model` (block B).
* `bootstrap` — spec §2d ``agent.default.bootstrap | tmux``; "bootstrap STAYS a key". Names the
  in-box multiplexer program for the persistent/reattachable session; the ``none`` sentinel means
  ephemeral / no-reattach (foreground single-use). Consumed by start.py's persistence-mode heuristic
  + bootstrap-wrap (consumer default ``tmux`` when unset). RELOCATED from the retired BOX-scope
  ``box.bootstrap_program`` key (1.7.0-rc clean break — no alias for the old box key).
* `continue_mode` — spec §2d ``agent.default.continue_mode | true``; "continue vs fresh; resume
  removed". Coerced to bool (default True): true ⇒ continue the most-recent conversation, false ⇒
  start fresh. It is the PERSISTED FALLBACK for the continue-vs-fresh decision at launch (start.py's
  `resolve_mode` seam); the per-launch ``-N``/``-C``/``-R`` flags OVERRIDE it (ephemeral wins),
  mirroring how ``-M`` overrides `model` and ``-S``/``-A`` override `access`. REPLACES the dead
  `start_mode` leaf (never read at launch; spec §3 "``start_mode`` fully covered by ``continue_mode``
  + ``auto_approve``" — 1.7.0-rc clean break, no alias; `auto_approve` has itself since been
  superseded by `access`, R-41).

**Box keys.** ⚑ There is NO `box.agent_name` (P7): the agent SELECTION is the §2h request
`pref.system.agent` (spec §2b RETIRED the box key).

* `box.images_store` — the host image-store root behind the shared-images bind (spec §2b; B3): a
  USER key whose DEFAULT is the runtime-probed podman graphroot, injected as a floor scalar at the
  launch seam (`core_defaults.image_default_categories`). A STRING path — no `KEY_TYPES` entry (only
  bools are coerced, cf. `box.share_images`).
* `box.enable_vault` — ⚑ `mode` is NO LONGER a settable config-set key (block B1, spec §2b / §0):
  the project mode is the RO identity anchor `meta.box.mode` (surfacing the runtime-resolved
  `@meta.runtime.project_type`), set by the construct-time/bootstrap layer (`[project].mode` at box
  creation), NOT overridable via `config set`. The mode is not persisted to disk (P8b sparse create
  wrote no ``project:`` section; `read_project_meta`/`write_project_meta` were deleted in P8c) — it
  derives from `box_resolve` at resolve time. On vault: `enable_vault` migrated to the box-scope key
  `box.enable_vault` (P2 clean break — no `vault.enabled` alias). The old bare `vault.ro`/`vault.rw`
  keys are REMOVED (dead residue): P8 deleted the ``project:`` settings section + its reader
  (`read_project_meta`), so a set landed in a section NOTHING reads — a silent dead write. The vault
  override surface is now the repointable core bind ``box.bindings.{ro,rw}.vault`` (spec §2c).
* `box.canon` — the per-BOX canon contribution root (spec §2b). ⚑ ``@box.canon`` is NOT ``~/canon``:
  it is ``<box_dir>/canon`` on the HOST, whose ``handbook/`` is ONE CHAPTER bound ro at
  ``~/canon/handbook/box``. The assembled guest view lives under the box HOME and arrives through the
  home bind. Same word, adjacent paths, opposite directions of travel.

**Workset keys.**

* `workset.registry` — per-workset registry location (settings-conformance P3). A NORMAL settable
  STRING-path key (default ``@meta.workset.path/registry.yaml``), NOT a config-locate key — routes to
  the ``workset:`` table nested slot ``registry`` (the same nested-settings pattern as `box.image`).
  ADDITIVE: nothing consumes it yet (the launch/create cutover is P4/P5).
* `workset.auth.path`, `workset.boxes`, `workset.vault_ro`, `workset.vault_rw`, `workset.logs` —
  workset-scope LAYOUT anchors (settings-conformance P6a). These path anchors are floor-materialized
  (`settings_launch.workset_anchor_floor` / start.py) as OVERRIDABLE base-level defaults, but were
  NOT reachable through the settable surface — a Type-A "meta ⟺ not-settable" violation. Jei ruled
  them SETTABLE: they are per-workset REPOINTABLE dirs (the same nested-settings STRING-path keys as
  `workset.auth.share_allowed`/`workset.registry`). A ``config set workset workset.boxes=…`` writes
  an EXPLICIT workset-level value that WINS over the base floor default by cascade precedence
  (workset ⊐ base). NO `KEY_TYPES` entry (all STRING paths, no bool coercion); routed to the
  ``workset:`` nested slot. Downward-default-able from a containing scope per R2.
* `workset.workspaces` / `workset.channelroot` — the two RESOLVED workset dir keys (§3.3: real and
  USED; manifest ``set: cli+file``). Declared in the keyspace and consumed live
  (`resolve_workset_workspaces` / `resolve_workset_channelroot` read the ``workset:`` nested slot this
  route writes), but absent from the set — so ``workset set workset.workspaces=…`` refused with
  "unknown config key" and a repoint required a settings-file edit (bifrost A1). Same shape as the
  sibling anchors: STRING paths (no `KEY_TYPES`), routed to the ``workset:`` nested slot.
* `workset.template` — per-workset template SOURCE (template-trio, spec §2c; Q3 2026-07-09). A NORMAL
  settable STRING-path key (default ``@meta.workset.path/template``); the layer-3 seed
  ``workset.seeded = {~/: (@workset.template/box/home,)}`` reads it, so repointing this key reroutes
  the workset template seed. Routed to the ``workset:`` nested slot (same pattern as
  `workset.registry`); a STRING path (no `KEY_TYPES`). STANDALONE has no workset tier (source
  `<None>`).
* `workset.canon` — per-workset CANON CONTRIBUTION root (spec §2c ALL PROJECTS). Same shape and same
  reason as `workset.template`: a NORMAL settable STRING-path key (default
  ``@meta.workset.path/canon``) read by the ro ``canon_hb_workset`` bind as its SOURCE, so repointing
  it moves the workset's handbook chapter. ⚑ It is NOT a seed dest — no seed layer ever targeted it
  (the retired handbook layers targeted ``@box.canon/handbook``, and they are gone as of
  2026-08-07g). Routed to the ``workset:`` nested slot.
* `workset.kuid` / `workset.skip_kuid_check` — workset kuid + advisory-check toggle
  (settings-conformance P6d). `workset.kuid` is the workset's stable id (Crockford-base32; sentinel
  ``"00000"`` for primary/named unless set — a STANDALONE box GENERATES a real one at creation,
  stored here); `workset.skip_kuid_check` (bool, default TRUE) gates the advisory ``Warning: invalid
  KUID``. Both settable `workset.*` keys routed to the ``workset:`` nested slot (same pattern as
  `workset.registry`).

**Layer-1 `config.*` foundation** (bootstrap paths; ``[config]`` table, spec §1).

* `config.journal` — the lifecycle-journal location (§3.3 ruling "needs to be recognized"):
  resolved/consumed as ``std.journal`` all along, but absent from this list, so the config verbs
  treated the key name as a project name. Recognition here gives it EXACT sibling parity: the
  get/show path reads it through `is_config_file_only_key`'s ``config.`` branch, and set/reset refuse it
  with the ruled bootstrap-file message like the other five.

**Layer-2 `system.*` path SETTINGS** (the ``system:`` table of the system SETTINGS file, spec §2g).
``global`` is ELIMINATED (children inline ``@config.data/global/...``). ⚑ All eleven — the six
top-level roots plus the five ``system.channels.*`` leaves — are here and in `_KEY_ROUTES` since
2026-08-23; before that the whole family was refused as structural and the five channel leaves were
not even recognised, so `get` answered *"unknown config key"* for a declared key.

* `system.template` — M-11: ``system.base_template`` → ``system.template``. The old spelling is
  RETIRED, not aliased — it is not a declared key any more (spec §0's closed keyspace), so ``config
  set system.base_template`` correctly refuses.
* `system.agent` (spec §2g) — the CURRENT agent's name: a system-scope SETTING (behavior, not a
  config path), so it routes to the ``system:`` table of the SYSTEM SETTINGS file, NOT the
  ``[system]`` config table. ⮕ P7 RENAMED it from ``system.default_agent`` AND relocated it out of
  the reserved ``agent.default`` table, where it had been an undeclared key riding the AGENT tier of
  the real cascade; it is now an ordinary `_KEY_ROUTES` entry and the four-site special case is gone.

## `DYNAMIC_PREFIXES`

The RETIRED bare env-var prefix (R-39, spec §2a: the env family is SCOPED — ``<scope>.env.<VAR>``; a
bare ``env.<VAR>`` is not a key). Kept ONLY so the spelling stays RECOGNISED as key-shaped:
`is_known_key` must not read it as a project name, and the verbs refuse it with the cure
(:func:`bare_env_retired_error`) rather than fail as an unknown key.

## The typed writer routing table — `_KEY_ROUTES` (the H1/H2 core)

The single source of truth for HOW every non-dynamic, non-env config key is stored. `get`/`set`/
`reset` all consult this table so the same key set is recognised on every path (no "get-validated,
set-unguarded" asymmetry that crashed H1). A key absent from here (and not `env.` / `agent.*` /
`system.path.*`) is UNKNOWN — the writer returns an error string, never raises.

Each entry maps the canonical key → the nested config location it lands in: `(sections_tuple,
leaf_name)`. An empty `sections` tuple means a top-level scalar field (e.g. `allow_helpers`). This is
the *currently advertised* key set; later phases (4) extend it with the new categories
(masks/bindings/synced/caches) without touching the routing mechanism.

Per-entry notes displaced from the table:

* `box.images_store` (B3) — routes to the ``box:`` table nested slot ``images_store``, the SAME
  nested-settings pattern as `box.image`, and exactly the file shape the launch cascade reads when
  resolving the ``@box.images_store`` host_src of the shared-images bind.
* The **auth-sharing 3-tier chain** (`system`/`workset`/`box.auth.*`) are ordinary SETTINGS keys:
  each routes to its nested ``<scope>.auth.<leaf>`` slot in the command-scope settings file (the same
  nested-settings pattern as `box.image` etc.), NOT the ``[project]`` meta table.
* `system.agent` — the agent SELECTION default (spec §2g). An ORDINARY settings-tier route (P7): the
  ``system:`` table of the system settings file, which is exactly where `assemble_levels` reads the
  system tier and where `config.read_system_agent` reads it back.
* `box.enable_vault` — `enable_vault` is the box-scope key (P2 clean break): it routes to the
  ``box:`` table nested slot ``enable_vault`` (the same nested-settings pattern as `box.image`), read
  back by `read_box_enable_vault()` from ``box.enable_vault`` — NO ``project`` fallback. The old bare
  `vault.ro`/`vault.rw` keys are REMOVED (dead residue): they routed to the ``project:`` section P8
  DELETED (reader `read_project_meta` gone) — a silent dead write. The vault override surface is the
  repointable core bind ``box.bindings.{ro,rw}.vault`` (spec §2c), not a bare key here. ``mode`` was
  removed from the settable routing table (block B1, spec §2b / §0 meta-RO): the project mode is the
  RO identity anchor `meta.box.mode`, never via `config set`; it is not persisted to disk (P8b sparse
  create) and derives from `box_resolve` at resolve time.
* `workset.registry` (settings-conformance P3) — the per-workset registry file location, routed to
  the ``workset:`` table nested slot ``registry`` (same nested-settings pattern as `box.image`). A
  STRING path — NO `KEY_TYPES` entry (no bool coercion); written sparsely on set. ADDITIVE: no
  consumer wiring yet (P4/P5).
* The **workset-scope LAYOUT anchors** (P6a) — the per-workset REPOINTABLE dirs floor-materialized in
  `workset_anchor_floor`, now settable. Each routes to its nested ``workset.<...>`` slot in the
  command-scope settings file — the SAME nested-settings pattern as `workset.auth.share_allowed` /
  `workset.registry` — so a set-value lands where `assemble_levels` mirrors it and OUT-PRECEDES the
  base floor default at launch. STRING paths (no `KEY_TYPES`).
* `workset.workspaces` / `workset.channelroot` (§3.3, bifrost A1) — routed to the ``workset:`` nested
  slot, EXACTLY where `resolve_workset_workspaces` / `resolve_workset_channelroot` (and
  `resolve_workset_registry_path`'s sibling pattern) read the repoint back. STRING paths (no
  `KEY_TYPES`).
* `workset.template` (template-trio, spec §2c; Q3) — the layer-3 seed source, routed to the
  ``workset:`` table slot (same nested-settings pattern as `workset.registry`). STRING path (no
  `KEY_TYPES` / no bool coerce).
* `workset.canon` / `box.canon` — the per-scope CANON CONTRIBUTION roots (spec §2c/§2b), routed
  exactly like `workset.template` / `box.image` — the ``workset:`` and ``box:`` table slots. STRING
  paths (no `KEY_TYPES` / no bool coerce).
* `workset.kuid` / `workset.skip_kuid_check` (P6d) — the same nested-settings pattern as
  `workset.registry`, routed to the ``workset:`` table slot. `workset.kuid` is a STRING (no
  `KEY_TYPES` entry); `workset.skip_kuid_check` is a bool (see `KEY_TYPES`). A standalone box's kuid
  is written here sparsely at create (`paths.establish_standalone`); primary/named default to the
  sentinel/true.
* ⚑ `allow_helpers` is NO LONGER a routed top-level scalar: it moved to the agent keyspace (spec §2d)
  — the bare key routes through `_is_agent_setting` to the `agent.default` tier, per-agent via the
  `_PERSONA_STATE_LEAVES` form ``agent.<agent>.allow_helpers``, exactly like `model`.

## `KEY_TYPES` — the values coerced before writing (the H2 fix)

Boolean keys parse true/false/1/0/yes/no (case-insensitive) to a Python bool so the loader reads back
a real bool (``set box.share_images false`` actually disables it). Build this extensibly — later
phases add `vault_enabled` etc. The truth table itself lives in `config` (shared with the box.meta
writer); see `config.coerce_bool`.

NOTE: the agent-scope scalars (`allow_helpers` / `access`) are NOT here — the bare key routes through
`_is_agent_setting` (verbatim string write, like `model`) and the launch reader coerces at read; this
table only governs the ROUTED `_KEY_ROUTES` writer + the category `validate_config_set` path.
`access` is an ENUM, not a bool: its set-time guard is :func:`access_value_error` (which REFUSES an
unknown value outright rather than coercing it), not a `KEY_TYPES` coercion. `workset.skip_kuid_check`
(P6d) gates the advisory invalid-KUID warning (default true, set via config).

## The scope-direction guard (block B4, spec §0 directional view/set + §2a)

`_SCOPE_NAMESPACES` is the recognized SCOPE namespaces a key may live in (its TOP-LEVEL dotted
token). A key whose first segment is NOT one of these (the un-prefixed scalars `model` /
`continue_mode` / `access` / `allow_helpers`) is SCOPELESS — it always writes to the command scope's
OWN file, so the direction guard does not apply to it. (The RETIRED bare ``env.<VAR>`` is scopeless
in SHAPE too, but it never reaches this guard: set/reset refuse it in the preamble — R-39. The live
env family is ``<scope>.env.<VAR>``, whose token IS a namespace and IS guarded.) `config` is a real
namespace (`config.*` keys exist) but no `config.*` key actually REACHES this guard: set/reset
short-circuit `config.*` earlier with the file-only refusal (B2).

`_SCOPE_CONTAINMENT` is the CONTAINMENT order (spec §0 "Directional view/set across CONTAINMENT
levels", repaired 2026-07-02): ``system ⊃ agent ⊃ workset ⊃ box``, OUTERMOST first. The single source
the write-allow sets derive from — it lives in `kb_store` (the stack leaf) so the RESOLVE-time
drop (`settings_assemble`) shares the SAME tuple without an import cycle; the module-level name here
is the module-local alias.

`_SCOPE_WRITE_ALLOWED` is which key-scope namespaces a COMMAND scope is allowed to WRITE (spec §0 +
§2a "Scope-direction guard": command-scope ≥ key-scope). A scope writes its OWN namespace AND that of
every scope it CONTAINS — the write lands in the COMMAND scope's file as an overridable default (the
contained scope always wins per the cascade); writing UPWARD is refused. Derived as each scope's
TAIL-SLICE of the containment order (one source, no per-scope hand list). ``meta.*`` is RO
everywhere. ``config.*`` is NOT writable from ANY command scope (block B2 — it is bootstrap/file-only
and is refused BEFORE this guard, so it appears in no allow-set; the older JC-B4-1 "system owns
config.*" rule is superseded). ``box.agent.*`` (the §2b B5 downward-tweak mirror) is the BOX
namespace — the guard keys on the TOP-LEVEL token (``box``), so ``box set box.agent.X`` is a legal
SAME-scope write.

`_SETTINGS_SCOPE_TOKENS` is the scope tokens whose prefixed keys are SETTINGS keys stored in a
SETTINGS file (a downward write keeps the key's scope token, nested in the COMMAND scope's settings
file — spec §0; the form `assemble_levels` mirrors). ``system`` is INCLUDED (F2 fix): a routed
``system.*`` SETTINGS key (the auth chain `system.auth.share_allowed`) lands in the system SETTINGS
file (``@config.settings``) — the file the launch cascade's system tier reads — NOT the Layer-1
`kanibako_config.yaml`. ⚑ Since 2026-08-23 the ``system.*`` PATH tier reaches this routing too, and
lands in the same file. ⚑ Since 2026-08-26 ``system.setup_completed`` does too — it was the one key
routed elsewhere (to the Layer-1 config file, where its shipped reader used to look), and its
storage moved to `@config.settings` with the rest. **No key routes to `kanibako_config.yaml`.**

**`_dot_to_flat` / `_FLAT_TO_CANONICAL` / `_route_key` ARE DELETED, and the absence is the fix.**
They let the write verbs accept the flat underscore form of a routed key (``box_image`` for
``box.image``). That was an UNDECLARED second user-facing surface: `key_validity` refuses the
spelling, `is_known_key` refuses it, and `get` refused it — while `set` took it and then PRINTED it
back in the confirmation, teaching the form no other verb served. It also chose the DESTINATION
FILE. `config_dest._dest` reads the scope token off the key as typed, and the flat form's first
dotted token is the whole string, so it missed `_SETTINGS_SCOPE_TOKENS` and fell to the Layer-1
`kanibako_config.yaml` — the bootstrap floor `config.load_merged_config` puts UNDER every tier —
while `box.image` went to the settings tier ABOVE it. A spelling silently selected precedence.

⚑ **Do not reintroduce a spelling normaliser here.** Spec §0: the keyspace is CLOSED and a key has
one spelling; a second one is not an alias, it is a second keyspace. The class guard is
`tests/test_settings/test_config_dest_parity.py::TestOneSpellingPerKey`, which derives the
mutation itself so it cannot go green by the production flattener disappearing.

## Per-persona agent keys (block B1)

``agent.<node>.<key>``, set on the agent's OWN settings file ``agents/<node>/agent.yaml``.

`_PERSONA_STATE_LEAVES` is the per-persona agent leaves this module RECOGNISES: the FLAT
agent-state knobs plus the ``env.`` section — the EXACT shape `agent_file.load`
reads back (`AgentConfig.state` / `.env`), so a value `set` here is what the launch snapshot resolves
for the persona (endpoint via `effective_behavior`). The former ``env_file.`` section is RENAMED to
the DISCRIMINATED ``agent.<node>.secret_path.<VAR>`` SECRET category (routed by
`_is_agent_node_secret_key` → `_node_secret_target`, NOT here — a clean break; ``env_file`` only
shipped rc0-rc2, no alias).

⚑⚑ **IT IS THE EFFECTIVE SET, NOT THE CORE ONE (2026-08-29).** §0 puts the agent specifics in the
PLUGINS, so the vocabulary is core §2d UNIONED with `setting_descriptors()`, through the SAME lazy
union `key_class` judges with (`settings_keyspace.effective_agent_leaves`). Core-only, it was a
SECOND vocabulary disagreeing with the judge, and both §0 breaches were **measured on a store built
through the product path**: `kanibako system set agent.goose.provider=x` answered *"Error: unknown
config key: agent.goose.provider"* at rc 1 for a leaf the goose target declares, and `kanibako
system get agent.goose.provider` answered *"(not set)"* at **rc 0 over the value stored in that
node's own file** — a fabricated default masking real data. The `agent` noun read the same key back
fine the whole time, which is what "two carriers of one vocabulary" looks like from the outside.

⚑ **THE LAZINESS IS THE CONTRACT, NOT AN OPTIMISATION.** The plugin half arrives as
`PLUGIN_DECLARED_LEAVES` — a `Collection` that DISCOVERS ON THE FIRST QUESTION ASKED — so the core
set is consulted first and a plain `agent.claude.model` never imports a plugin. Materialising it
(`frozenset(...)`, or calling `plugin_declared_leaves()` at a call site) re-arms the cost measured
2026-08-25 at **+67 ms on every settings-resolving command**, 73% of the whole resolve. Counted
`discover_targets()` calls before and after this change: identical on every arm, and **0** on a cold
`load_merged_config` (n=20).

⚑ **ONE SUPPLIER FOR THE MODULE — `plugin_declared_leaves()`, over `settings_prefs.
default_valid_agents().leaves`.** `agent_key_reason` reached that value a second way and
`_PERSONA_STATE_LEAVES` did not reach it at all; both go through the one function now. **Not the
`settings_keyspace_probe` memo**, deliberately: that one is primed at `pytest_configure` and has no
reset seam, so production behaviour would become unpatchable from a test. `reset_discovery_cache`
is the seam this side keeps.

Two of its leaves carry their own reason:

* `template` — per-agent template SOURCE (template-trio, spec §2a/§2d; Q2 2026-07-09 "agent = persona
  + harness"). A settable STRING-path leaf on the agent (persona+harness) node — default
  ``@config.agents/<harness>/template`` — read by the layer-2 seed ``agent.<a>.seeded = {~/:
  (@agent.<a>.template/box/home,)}``, so repointing it reroutes the agent template seed.
* `canon` — per-agent CANON CONTRIBUTION root (spec §2d), the source of the ro ``canon_hb_agent``
  bind, i.e. WHERE THIS AGENT'S HANDBOOK CHAPTER LIVES. A settable STRING-path leaf on the agent
  (persona+harness) node, wired for the same reason `template` is: both are per-agent path SOURCES a
  user may legitimately relocate, and both default off the agent's store. ⚑ Its FLOOR default is
  chosen per J-1: the node's own store when that store provides a canon, else the `agent.default`
  tier — and a value set HERE beats the floor either way, which is what makes the tier a fallback
  rather than a ceiling.

`AGENT_DEFAULT_SUB` is the RESERVED any-agent tier name (mirrors `settings_assemble._AGENT_DEFAULT_SUB`
/ `config.read_agent_settings`: "no real agent may be named default"). It is NOT a persona node — an
``agent.default.<key>`` write is refused (the any-agent default is the BARE key), so nothing lands at
a never-read ``agents/default/``.

## Per-node DESCRIPTOR bind keys (item-0) — `_AGENT_NODE_BIND_RE`

``agent.<node>.bindings.{ro,rw}.<name>``. ⮕ The CLI WRITE route is RETIRED (R-9); the key stays
declared, hand-authorable in the node's settings file, delivered at launch, and READABLE via `config
get`.

The regex matches the per-node descriptor delivery bind (claude launcher/share …). ``<node>`` is
NON-greedy so the FIRST ``.bindings.{ro,rw}.`` segment splits node from name (a bind literally NAMED
``model`` — the name group — is thus ``agent.<node>.bindings.ro.model``, disambiguated from the
persona state leaf ``agent.<node>.model`` by the ``bindings.{ro,rw}`` segment). NOTE: the agent tier
is DISCRIMINATED — ``agent.<node>`` is the ONLY agent form (§2d / §0); an undiscriminated
``agent.<category>`` is not a key and ``BIND_KEY_RE`` refuses it.

⚑ THIS IS THE AGENT-SCOPE READ PARSER, NOT THE RECOGNISER. The recogniser is
`settings_categories.AGENT_BIND_KEY_RE` (the derived twin of `SCOPE_BIND_KEY_RE`, covering all six).
What this parser uniquely owns is the two jobs that RESOLVE rather than recognise: `config get` reads
the key through it, and `resolve_key` canonicalizes its node segment through it.

⚑ It spells the two arms LITERALLY rather than importing the alternation, because the node group has
to be split non-greedily around them. The literal is pinned as a SUBSET of
`settings_categories.RETIRED_BIND_CATEGORIES` by `test_config_interface.TestAgentNodeBindRouting` so
the two cannot drift.

⚑⚑ IT IS DELIBERATELY NARROWER THAN THAT SET, AND THE SHAPE CUTOVER DID NOT CHANGE THAT — it
CONFIRMED it. The older note said widening this parser "belongs with the shape cutover"; the cutover
(2026-08-08c) landed and the answer turned out to be that there is nothing to widen it TO. This parser
picks a READ route (:func:`config_dest._node_bind_target` -> the agent file's address rule), and — decisively —
a per-entry key under those four is not a key at ALL now, so a widened parser would invent a read for
a spelling the keyspace refuses. ⚑ **THE REASON MOVED AT S3, THE ANSWER DID NOT.** It used to be a
STORAGE fact — the file-shape SoT had a nested table for ``bindings.<arm>.<name>`` and none for the
other four, so widening would have resolved ``agent.claude.common.plugins`` to the dotted leaf
``self."common.plugins"`` and answered a silent "(not set)". Since S3 the address rule reads EVERY
category flat with the destination whole, so widening would no longer mis-address them — it would
ADMIT them. These two arms are the only per-entry spellings whose READ survived R-9, which is exactly
what :func:`agent_read_key_error` carves out of the ``agent`` noun's §0 read gate. ⚑ DO NOT WIDEN IT. The four are RECOGNISED and
refused at the agent scope through `AGENT_BIND_KEY_RE` — see :func:`agent_node_bind_retired_error`.

The regex does not match the ``box.agent.bindings.*`` box-mirror form (a ``box`` top-token).

## Per-node SECRET keys — `_AGENT_NODE_SECRET_RE`

``agent.<node>.secret_path.<VAR>`` — the per-node SECRET category (spec §2a, 2026-07-06). Like the
descriptor bind key it is DISCRIMINATED (node in the key) and stored UNDER the
``agent.<node>.secret_path`` sub-table in the node's OWN settings file — the shape `_agent_partial`
reads into the launch cascade — but the value is a SCALAR host PATH, not a `Bind` tuple (so it routes
via a plain scalar write — never the category write route, which no longer exists at all: DS-BL1 =
(a)). ``<node>`` is NON-greedy so the FIRST ``.secret_path.`` splits node from VAR; VAR is the
env-name shape (no dots).

## The LIVE scoped env family — `_SCOPE_ENV_RE`

``<scope>.env.<VAR>`` for the non-agent scopes (system/workset/box) — spec §2a L383
``<scope>.env.<VAR> | value | scoped env var``; §2a L496 "Scalars (incl. ``env.<VAR>``, whose value is
scalar) → full CLI set". The AGENT scope form ``agent.<node>.env.<VAR>`` is DISCRIMINATED and routed
by `_is_persona_agent_key` (the node file); this covers the other three, which write a scalar to the
COMMAND scope's OWN settings file at ``<scope>.env.<VAR>`` — the shape `_file_partial` reads into the
cascade and `settings_launch._emit_scope_node` delivers as a ``category="env"`` entry. Deliberately
the SIBLING-EXACT twin of `_SCOPE_SECRET_RE`: same three scopes, same VAR shape (no dots), same
NOUN-file destination. VAR matching is CASE-SENSITIVE (spec §0 "Reserved key names" / §2a L386) —
there is no case-folding anywhere on this path.

## The scoped SECRET family — `_SCOPE_SECRET_RE`

``<scope>.secret_path.<VAR>`` for the NON-agent scopes (system/workset/box). The AGENT scope form
``agent.<node>.secret_path.<VAR>`` is DISCRIMINATED and routed by `_is_agent_node_secret_key` (the
node file); this covers the other three, which write a scalar to the COMMAND scope's OWN settings
file at ``<scope>.secret_path.<VAR>`` (the shape `_file_partial` reads into the cascade).

## Scopes that cannot write a BARE agent key — `_NO_BARE_AGENT_KEY_SCOPES`

The command scopes that CANNOT write a BARE agent behavior key: a bare key (`_is_agent_setting`)
targets the any-agent `agent.default` tier, which both box (agent ⊃ box) and workset (agent ⊃
workset) CONTAIN — so a bare write from either is UPWARD and is DROPPED at launch by
`settings_assemble._drop_upward_scopes` (a silent no-op the CLI reported as "Set"). The two differ in
the CURE: a BOX has a single active agent, so it is redirected to the spec §2h request
``pref.agent.<active>.<key>`` (`box_agent_redirect_key`); a WORKSET spans many boxes/agents, so there
is no single agent to redirect TO — it simply refuses (configure at system scope for all agents, or
per-box via the §2h request).

## Two deleted helpers, and why they must not come back

**`_floor_bind_display` USED TO LIVE HERE.** It rendered the reverted-to descriptor FLOOR `(value,
tier)` for a reset of a launch-only bind, reading the `default_categories` registry the set path
folded. R-9 retired both bind CLI write routes, so no key that reaches a reset branch could have a
floor entry, and the whole set-time floor thread — this function, the parameter on five
`config_interface` entry points, `core_defaults.core_default_bind_keys` and its placeholder sentinel,
and the three handler call sites — was deleted together. Resets now always take the cleared-only
honest form on the category branch.

**`_agent_scope_node` IS GONE (DS-BL1 = (a)).** It answered "which agent NODE does this discriminated
agent-scope CATEGORY key name?", and its sole caller (`config_interface._category_set_lookups`) used
the answer to anchor the ``meta.agent.<a>.path`` store root so a category SET's rooted-source hint
would itself resolve. No bind-shaped category reaches a set any more — the write verbs refuse all six
by name — and every key that still reaches that function is a SCALAR, for which the predicate answered
``""`` by construction. Deleted rather than left to answer a question nobody asks.

## Functions (continued)

In source order. :data:`SETUP_MARKER_KEY` and the deleted `system_key_refusal` are documented
under **Functions**, above.

```_coerce_value(canonical: str, value: "str | None") -> object | str | None```
Coerce *value* to the typed form declared for *canonical* in `KEY_TYPES`.

Returns the typed Python value (e.g. a real `bool`) on success, or an ``"Error: ..."`` string when a
bool key is given an unparseable value. Scalars (no `KEY_TYPES` entry) pass through unchanged as the
raw string. A `None` *value* is an explicit present-None request (`--null`) and is never coerced.

```_scope_direction_error(canonical: str, command_scope: "ConfigLevel | None") -> str | None```
Enforce the §0 directional-WRITE rule for `config set` (block B4).

A `config set` writes keys of the command scope's OWN namespace AND of any scope it CONTAINS
(command-scope ≥ key-scope over ``system ⊃ agent ⊃ workset ⊃ box`` — a downward write is an
overridable DEFAULT stored in the command scope's file); writing UPWARD (a CONTAINING scope's key) is
REFUSED (spec §0 "Directional view/set" + §2a "Scope-direction guard", repaired 2026-07-02).
``meta.*`` is a TOP-LEVEL read-only namespace — refused from EVERY scope.

Returns an ``Error: …`` string when the write is REFUSED, or `None` when it is permitted (so the
caller proceeds to dispatch).

*command_scope* is the scope the `config set` was issued at (threaded by each caller; see the 4
command handlers). When `None` the guard is skipped (no command-scope context available — preserves
callers that do not supply one).

The guard keys on the key's TOP-LEVEL dotted token. A SCOPELESS key (the un-prefixed scalars) is
always permitted — it writes to the command scope's own file by construction. (The RETIRED bare
``env.*`` is scopeless in shape too but never arrives here: the verbs refuse it earlier — R-39.)

```resolve_key(raw: str) -> str```
Return the canonical config key for a user-supplied key name.

Most config keys are already canonical (dot-notation like ``box.image`` or ``box.enable_vault``) and
pass through unchanged; this is the single canonicalization seam every get/set/reset path routes
through. ⚑ It does NOT normalise spellings — an underscore form is not a key and passes through to
be refused by name (see the deleted `_route_key` above).

The ONE canonicalization it performs (block B1): for a per-persona agent key ``agent.<node>.<key>`` it
canonicalizes the ``<node>`` SEGMENT ``+`` -> ``℘`` (``agent.navigator+claude.endpoint`` ->
``agent.navigator℘claude.endpoint``), so the write/get/reset all target the canonical
``agents/<node>/`` slot the resolver reads. The node segment is canonicalized as a WHOLE via
:func:`canonicalize_agent_ref` (agent_ref design law: never re-split a ref on the raw separator); the
tail (``endpoint`` / ``env.<VAR>`` / ``secret_path.<VAR>``) is preserved verbatim. A malformed node is
left RAW here — the set/reset persona branch surfaces the parse error (and a bad node never silently
swaps). Applied ONLY to the ``agent.<node>.*`` node segment, never blindly to all keys.

The per-node DESCRIPTOR bind key ``agent.<node>.bindings.{ro,rw}.<name>`` (item-0) is canonicalized
the SAME way (``<node>`` ``+`` -> ``℘``) and is matched BEFORE the persona form — a bind named after a
persona state leaf (``agent.<node>.bindings.ro.model``) would otherwise be mis-parsed by
:func:`_parse_persona_agent_key` (``model`` is a state leaf). The ``bindings.{ro,rw}`` category
segment + the bind name are preserved verbatim.

⚑ That arm outlives R-9's retirement of the bind CLI WRITE route, and both surviving readers need it:
`config get` targets the canonical ``agents/<node>/`` slot, and the write verbs' refusal quotes the
CANONICAL key. Dropping it would make a ``+``-form bind key fall through to the persona branch and get
re-rooted as the wrong key on its way to the wrong message.

```_parse_persona_agent_key(key: str) -> "tuple[str, str] | None"```
Split an ``agent.<node>.<tail>`` persona key into ``(node_raw, tail)``.

Returns `None` when *key* is not a settable per-persona agent key. The settable *tail* forms are a
FLAT state leaf (``endpoint`` / ``model`` / ``continue_mode`` / ``access`` / ``allow_helpers``) or a
sectioned ``env.<VAR>`` pointer. The SECRET pointer ``secret_path.<VAR>`` is NOT parsed here — it is
matched EARLIER (`_is_agent_node_secret_key`) and stored DISCRIMINATED (spec §2a; it replaced the
rc-only ``env_file.<VAR>``, which routed here). The node segment is returned VERBATIM (possibly a
``+`` form, possibly itself dotted — a persona/harness segment may contain ``.``) for
:func:`canonicalize_agent_ref` to canonicalize as a WHOLE.

Parsed from the RIGHT: the closed set of settable tails is unambiguous, so everything left of a
recognised tail is the node. ``env`` is matched BEFORE the flat leaves so ``agent.<node>.env.MODEL``
is an env var named ``MODEL``, never mis-split as the state leaf ``model``.

```_is_persona_agent_key(key: str) -> bool```
True iff *key* is a settable per-persona ``agent.<node>.<key>`` key (B1).

```is_access_key(canonical: str) -> bool```
True iff *canonical* is the auth-critical ``access`` permission key.

Matches both DIRECT settable forms: the BARE any-agent `agent.default` tier key (``canonical ==
"access"``, routed via :func:`_is_agent_setting`) and a per-persona override ``agent.<node>.access``
(routed via :func:`_is_persona_agent_key`). Used to WRITE-VALIDATE the value at `config set` time
(:func:`access_value_error`): ``access`` decides whether the box's agent prompts at all, so an
unrecognised value must be REJECTED at the write, never stored to be re-read at launch. Only
``access`` gets this guard (Jei: only the auth-critical key), not ``allow_helpers`` / ``model``.

⚑ It answers False for the §2h REQUEST spelling ``pref.agent.<node>.access``, and that is correct
rather than a gap: this predicate names TARGET keys, and a pref is not its target. The request form is
guarded at its TARGET, by `config_interface._pref_value_error` — which calls THIS function on the
target it extracts, so both spellings redeem the one truth table. (That call is what closes the hole;
before it, ``box set pref.agent.claude.access=fll`` — the very command the RQ-2 refusal prescribes —
was accepted and stored.)

⚑ R-41 (1.8.0): this is the RENAMED `is_auto_approve_key`. The rename follows the KEY, not the name —
the guard exists because this key is the permission axis, so it must travel with the axis when the
axis is respelled.

```access_value_error(canonical: str, value: str) -> str | None```
The LOUD refusal for an illegal ``access`` value, or `None` when legal.

The single set-time validator for the permission tier, shared by BOTH set paths
(`config_interface.validate_config_set` and the `agent set` verb) so there is one message and one
truth table. Legal values are exactly :data:`~kanibako.settings.settings_keyspace.ACCESS_TIERS`; the
message NAMES the key and lists them.

⚑ Never lenient, never permissive-by-default: an unknown value is refused outright rather than coerced
or defaulted (a typo must not be the difference between an agent that prompts and one that does not).
Matching is EXACT — no case folding — because the stored value is what the launch resolver reads back
and the launch resolver is exact too.

```parse_agent_node_bind_key(key: str) -> "tuple[str, str, str] | None"```
Split ``agent.<node>.bindings.{ro,rw}.<name>`` into ``(node_raw, cat, name)``.

Returns `None` when *key* is not a per-node descriptor bind key. ``cat`` is the ``bindings.ro`` /
``bindings.rw`` segment; ``node_raw`` is VERBATIM (possibly a ``+`` form) for
:func:`canonicalize_agent_ref` to canonicalize as a WHOLE. Parsed BEFORE
:func:`_parse_persona_agent_key` everywhere so a bind named after a persona state leaf
(``agent.claude.bindings.ro.model``) is a BIND, never mis-split as the state key
``agent.claude.model``.

```_is_agent_node_bind_key(key: str) -> bool```
True iff *key* is a per-node descriptor bind ``agent.<node>.bindings.*`` key (item-0).

⮕ **R-9 RETIRED THIS ROUTE'S CLI WRITE HALF** (disk-store rework step 1, the agent-scope step). The
two ``bindings`` arms are becoming a TERMINAL key whose VALUE is a dest-keyed map, and *the inner
map's keys are not part of the keyspace*, so there is no dotted key left for `config set` / `config
reset` to name. The key itself is NOT retired: still declared, still authored in
``agents/<node>/agent.yaml``, still delivered at launch, still READ by `config get`. Only the CLI
write route is gone — a KNOWN, ACCEPTED loss (backlog DS-BL1).

So this predicate now has three jobs, all live: recognise the retired spelling so the write verbs
refuse it BY NAME with a cure (:func:`agent_node_bind_retired_error`) instead of degrading to "unknown
config key" (spec §0 refuses loudly, never quietly); route the surviving `config get`; and keep the
key out of the persona branch. Checked BEFORE :func:`_is_persona_agent_key` in every dispatch.

```_parse_agent_node_secret_key(key: str) -> "tuple[str, str] | None"```
Split ``agent.<node>.secret_path.<VAR>`` into ``(node_raw, var)``, or `None`.

``node_raw`` is VERBATIM (possibly a ``+`` form) for :func:`canonicalize_agent_ref` to canonicalize as
a WHOLE. Parsed BEFORE :func:`_parse_persona_agent_key` so a secret pointer never falls through to the
(now env_file-less) persona branch.

```_is_agent_node_secret_key(key: str) -> bool```
True iff *key* is a per-node ``agent.<node>.secret_path.<VAR>`` key (SECRET category).

Checked BEFORE the persona + path-category branches in dispatch.

```_persona_display_key(canonical: str) -> str```
Render a canonical persona key for USER-FACING output (``℘`` -> ``+``).

```_node_secret_display_key(canonical: str) -> str```
Render a canonical ``agent.<node>.secret_path.<VAR>`` key for USER-FACING output (``℘`` -> ``+`` on
the node segment).

```_is_bare_env_key(key: str) -> bool```
The RETIRED bare docker-``.env`` spelling ``env.<VAR>`` (R-39, spec §2a).

⮕ **R-39 RETIRED THE BARE SPELLING.** The keyspace env family is SCOPED — ``<scope>.env.<VAR>``,
matched by :func:`_is_scope_env_key` — and the bare form wrote the docker ``.env`` FILE instead: an
undiscriminated variant that silently meant something different from the discriminated key (Code
Convention 0's failure mode). This predicate now exists ONLY to RECOGNISE the retired spelling so set
/ reset / get can refuse it with the cure (:func:`bare_env_retired_error`) rather than fail as an
unknown key — the same recognise-to-refuse role as :func:`_is_box_agent_key` (P7).

```_is_scope_env_key(key: str) -> bool```
True iff *key* is a NON-agent ``<scope>.env.<VAR>`` key (system/workset/box) — settable to the command
scope's own settings file.

SHAPE only. The spec §0 RESERVED-NAME floor is enforced at WRITE time by :func:`scope_env_var_error`,
not here: a reserved VAR must be refused by NAME ("'get' is a RESERVED key name"), and excluding it
from the shape test would instead report the far less useful "unknown config key".

```scope_env_var_error(canonical: str) -> str | None```
Refuse a ``<scope>.env.<VAR>`` WRITE whose VAR is a RESERVED name.

Spec §0: an ``env.<VAR>`` name may not be a public `dict` method name nor match the dunder pattern,
and the refusal is due "loudly at write/`config set` time" — this is that site (set AND reset; a reset
is a write, and "No override for box.env.get" would imply the name was legal).

Returns an ``Error: …`` string when refused, else `None` (including for every non-scope-env key, so
the verbs can apply it unconditionally).

```bare_env_retired_error(key: str, *, verb: str, command_scope: "ConfigLevel | None" = None) -> str | None```
The refusal + cure for a RETIRED bare ``env.<VAR>`` op (R-39, spec §2a), or `None` when *key* is not a
bare env key.

The cure NAMES the DISCRIMINATED key for the command scope (``box`` when no scope is threaded — the
box tier is where the old spelling's writes bit) and quotes the user's spelling. That key is REAL and
REACHABLE: this same change routed ``<scope>.env.<VAR>`` through get / set / reset
(:func:`_is_scope_env_key`), so the cure is an instruction the user can follow verbatim, not a pointer
at an unimplemented key. The one scope that cannot be named verbatim is ``agent``: it is
DISCRIMINATED, so the cure carries an explicit ``<agent>`` placeholder rather than the illegal bare
``agent.env.<VAR>``.

⚑ The AGENT scope arm, in full: ``agent.env.<VAR>`` is NOT a key (spec §0).
``ENV_KEY_RE``/``BIND_KEY_RE`` (`settings_categories`) spell the agent scope ``agent.<node>`` and
REFUSE the bare ``agent.`` form outright, so ``command_scope.value`` would hand the user a SECOND
illegal spelling to replace the first — a cure that cannot be followed. The agent arm names the
discriminated form with an explicit ``<agent>`` placeholder instead. (The ``agent`` NOUN's own verbs
never reach here: ``agent set <node> env.FOO=bar`` passes a key TAIL under an already-named node,
which the agent file's write address rule puts at the DECLARED ``agent.<node>.env.FOO``. That arm exists so the
GENERIC engine cannot emit the illegal spelling if an agent-scope caller is ever wired into it.)

⚑ The docker ``.env`` FILES the bare spelling used to write are RETIRED OUTRIGHT (Jei's 2026-08-02
RQ-1 re-ruling; the ratified manifest records the files as DROPPED): the launch-side three-tier read
is gone, so a hand edit no longer reaches the box either. The message says so — a cure that implied
"edit the file instead" would send the user to a surface nothing reads.

*verb* is the op word for the message (``"set"`` / ``"reset"`` / ``"read"``). Like
:func:`bare_agent_key_scope_error` this gates itself — `None` for every other key — so every verb door
applies it uniformly and unconditionally.

```_is_agent_setting(key: str) -> bool```
True iff *key* is the BARE CLI spelling of an ``agent.default.<leaf>`` key (spec §2d).

⚑⚑ **BOTH THIS AND `_PERSONA_STATE_LEAVES` ARE DERIVED FROM `DECLARED_AGENT_LEAVES` (2026-08-23),
and the hand-kept copies they replace are the exact defect P13 warns about.** Each carried its own
list of leaves, and BOTH had fallen behind the declaration: `agent.default.{run_args,transform,
transform_settings}` answered *"unknown config key"* at every spelling, and
`agent.default.{template,canon}` answered a refusal whose cure — *"set the any-agent default with
the bare key"* — itself answered *"unknown config key"*. A refusal that prescribes a failing
command is worse than no cure. Deriving them makes the bare spelling the one the CLI serves for the
whole declared set, which is what makes that cure TRUE.

⚑ **RECOGNITION AND SETTABILITY ARE NOW SEPARATE.** `_PERSONA_STATE_LEAVES` is the WHOLE declared
set, so a declared key is refused BY NAME rather than degraded to "unknown" (spec §0); this
predicate is `SCALAR_AGENT_LEAVES` — the declared set MINUS `TABLE_VALUED_AGENT_LEAVES`. The one
table-valued member, `transform_settings`, is refused by :func:`agent_leaf_table_error` at both
spellings.

⚑⚑ **AND THEY NOW DIFFER IN A SECOND WAY — AN OPEN ASYMMETRY, NOT A DESIGN.** Since 2026-08-29
`_PERSONA_STATE_LEAVES` is the EFFECTIVE (plugin-aware) set while this predicate is still core
`SCALAR_AGENT_LEAVES`, so the BARE spelling of a PLUGIN-declared leaf is not settable. `key_class`
declares `agent.default.provider` a key, `agent_default_tier_leaf` claims its slot and the READ is
honest — but `set agent.default.provider=x` meets the reserved-any-agent-tier refusal, whose cure
names the bare `provider`, and that bare spelling answers *"unknown config key"*. **It is the
broken-cure shape this section already documents, one layer out.** Widening it is NOT a matter of
deriving one more set: `KNOWN_CONFIG_KEYS` (the quarantine) would have to widen with it and
deriving THAT was proposed and DECLINED, so the fix is a ruled decision, not a refactor.

```agent_leaf_table_error(canonical: str, *, verb: str) -> str | None```
Refuse a WRITE at a declared agent leaf whose value is a TABLE (spec §2d).

⚑ **ORDER IS LOAD-BEARING: it runs in the set/reset PREAMBLE, before the persona branch.** Placed
after it, `agent.default.transform_settings` would first meet the reserved-any-agent-tier refusal,
whose cure names the bare spelling — which this rule then refuses. That is the same broken-cure
shape the derivation above exists to end.

⚑ **IT NAMES THE KEY AND THE SHAPE, never "unknown config key".** The key is declared, `config get`
reads it, and it is hand-authored in YAML today; what the CLI cannot do is express a map as a
scalar. The FILE-shape twin of this fact is `agent_file.table_value_error`, and the two are pinned
against each other by `tests/test_settings/test_agent_leaf_shape.py` — a scalar stored at
`self.transform_settings` used to crash every subsequent `agent_file.load`, i.e. every launch.

⚑ **THE REGISTRY DISAGREES, AND THE CHECK IS HELD RED RATHER THAN EXEMPTED.**
`agent.default.transform_settings` carries `set: cli+file` while being the ONLY dict-typed row that
does (the manifest's three other dict rows are `set: file`), and spec §2a's *"what remains
CLI-settable: scalars…"* excludes it. Changing that row is the registry's owner's call, not the
code's — so `tests/test_settings/test_set_column_conformance.py` names it as a FINDING.

```_is_box_agent_key(key: str) -> bool```
The RETIRED box-scoped agent mirror ``box.agent.<key>`` (spec §2b).

⮕ **P7 RETIRED THE SETTABLE MIRROR.** ``box.agent.<key>`` was the box's box-scoped override of its
active agent's settings subtree. Spec §2b replaces it with the RO read-back
``meta.box.agent.<key>`` (readable, never settable — ``meta.*`` is RO by contract), and a box tweaks
its agent with the §2h request ``pref.agent.<agent>.<key>``, which targets the AGENT tier properly
instead of smuggling a box-scope key into it.

This predicate now exists ONLY to RECOGNISE the retired spelling so set / reset / get can refuse it
with the cure (:func:`box_agent_retired_error`) rather than fail as an unknown key — a user who has
the old form in muscle memory must be TOLD what replaced it.

```box_agent_retired_error(canonical: str, *, verb: str, active_agent: str | None = None) -> str```
The refusal + cure for a RETIRED ``box.agent.<key>`` op (P7, spec §2b).

⚑ The pointer names what ``--effective`` ACTUALLY RENDERS — the ``pref`` block, which prints each
REQUEST beside the value it produced. It deliberately does NOT promise ``meta.box.agent.<key>``: that
key is real in the snapshot (the RO read-back) but no renderer emits it today, and a cure that points
at output the user will not find is worse than no pointer.

```box_agent_redirect_key(canonical: str, command_scope: "ConfigLevel | None", active_agent: str | None = None) -> str | None```
The canonical ``pref.agent.<active>.<key>`` request a BARE agent behavior key redirects to at BOX
command scope, or `None` when this case does not apply.

⮕ **P7 RETARGETED THIS.** It used to name the settable ``box.agent.<key>`` mirror, which spec §2b
retired; the box-scoped way to set an agent value is now the §2h request. *active_agent* is the box's
resolved agent NODE — required, because the request targets a DISCRIMINATED agent slot (there is no
bare ``agent.<key>``, §0). With no resolvable agent there is nothing to redirect to, so this returns
`None` and the caller falls through to the ordinary bare-key refusal, which names the shape.

A BARE agent behavior key — the WHOLE :func:`_is_agent_setting` family (``model`` / ``access`` /
``bootstrap`` / ``endpoint`` / ``allow_helpers`` / ``continue_mode``), uniformly, NOT a per-key list —
targets the any-agent `agent.default` tier. From a BOX that is an UPWARD write (agent ⊃ box in the
containment order): the §0 directional rule REFUSES it (spec §2h replaced the old "box tweaks its
agent through its own mirror" device). The old code wrote ``agent.default.<key>`` into the BOX
settings file, which `settings_assemble._drop_upward_scopes` then DROPPED at launch (a box file may
not set a containing ``agent`` table) — a silent no-op the CLI still reported as "Set". So the bare
form at box scope is REDIRECTED to the box's request ``pref.agent.<active>.<key>``: `set`/`reset`
REFUSE (the value lives at, and is set/reset at, the request), `get` reads/names the request.

Fires ONLY for the bare form at BOX command scope (the mirror is box-specific). A WORKSET bare agent
key is caught by :func:`bare_agent_key_scope_error` (refuse, no mirror). The already-qualified
``box.agent.<key>`` is `_is_box_agent_key` (NOT `_is_agent_setting`) — a legal SAME-scope box write; a
per-agent ``agent.<name>.<key>`` is `_is_persona_agent_key`; a bare key at SYSTEM scope is a DOWNWARD
write (agent is a scope the system CONTAINS). None of those match, so all stay unaffected.

```bare_agent_key_scope_error(canonical: str, command_scope: "ConfigLevel | None", *, verb: str, active_agent: str | None = None) -> str | None```
Error string refusing a WRITE-shaped op on a BARE agent behavior key at a scope that cannot write it
(box / workset), or `None` when it is permitted.

A BARE agent behavior key (:func:`_is_agent_setting`, the whole family — uniform, NOT a per-key list)
targets the any-agent `agent.default` tier, which both BOX (agent ⊃ box) and WORKSET (agent ⊃ workset)
CONTAIN. A bare write from either is UPWARD — `settings_assemble._drop_upward_scopes` DROPS it at
launch, a silent no-op the old CLI reported as "Set". So it is refused HERE, uniformly for `set` /
`reset` (writes) at both scopes, and for the workset `get` (the box `get` instead REDIRECTS via
:func:`box_agent_redirect_key`).

*verb* is the op word for the message (``"set"`` / ``"reset"`` / ``"read"``). *active_agent* names the
box's resolved agent in the cure so the suggested command is COPY-PASTEABLE
(``pref.agent.claude.model``) rather than a shape to fill in; the placeholder is used only where no
agent resolves.

* **BOX** — a box has a single active agent, so the refusal TEACHES the §2h request
  ``pref.agent.<agent>.<key>`` (the box-scoped tweak surface since P7 retired the ``box.agent.*``
  mirror).
* **WORKSET** — a workset spans multiple boxes/agents, so there is deliberately no single "the agent".
  The refusal points at system scope (all agents) or the per-box request. ⚑ WORKSET keeps the
  PLACEHOLDER on purpose: naming one box's resolved agent there would be a lie.

Returns `None` for every other scope — a bare key at SYSTEM scope is a legit DOWNWARD write;
``agent`` / ``system`` (no command scope) is unconstrained here.

```is_known_key(arg: str) -> bool```
Return True if *arg* looks like a config key (not a project name).

⚑ IT ANSWERS `False` FOR SEVEN DECLARED KEYS AT EVERY SCOPE, ON PURPOSE — the six bind-shaped category
terminals and ``<scope>.masks``. Read **The QUARANTINE** above before treating that as a bug: an
individual read/write of a multi-faceted key is NOT SUPPORTED, the readable form is a promise whose
shape is undecided (Jei, 2026-08-08), and DERIVING this predicate from the declaration SoT — the
obvious fix — was proposed and DECLINED because it would build that undecided surface.

⚑⚑ **NO VERB'S VOCABULARY IS ON IT SINCE 2026-08-28.** ``system get`` was the last read gate that
consulted it, and those seven `False` answers reached the user as "unknown config key" — a refusal
of a DECLARED key, which is the §0 fault, not the quarantine's intent. What is left are the
DISAMBIGUATION callers in ``commands/box/_parser.py``, whose question genuinely is "key or project
name". 🛑 **Do not re-wire this into a gate**, and do not read its narrowness as a refusal rule:
the predicate answers a PARSER, and the closed keyspace answers everything else.

The branch-by-branch reasons, in dispatch order:

* **Bare ``env.<VAR>``** — RETIRED (R-39) but still KEY-SHAPED on purpose: the verbs must refuse it
  with the cure, which requires the positional-vs-key disambiguator to read it as a key, never as a
  project name.
* **``pref.<target-key>``** — the §2h REQUEST family. SHAPE-only here: this is the positional-vs-key
  disambiguator, and no project is named ``pref.…``. The three filters run in the set / get / reset
  branches.
* **``agent.<node>.bindings.{ro,rw}.<name>``** — the per-node DESCRIPTOR bind key (item-0), whose CLI
  WRITE route is RETIRED (R-9). ⚑ The agent-scope spelling of the OTHER four bind-shaped categories is
  retired too (DS-BL1 = (a)) and is recognised by the FINAL branch, not here — this parser is the
  ``bindings`` arms only (see `_AGENT_NODE_BIND_RE`). Kept KEY-SHAPED for the same reason as the
  file-scope spelling and the bare ``env.<VAR>`` one: the positional-vs-key disambiguator must read it
  as a key so the verbs can refuse it with the cure (`agent_node_bind_retired_error`) rather than
  mistake it for a project name. It is also still READABLE (`config get`), which on its own makes it a
  key here. Recognised on the ``+``-form too, before canonicalization, and checked BEFORE the persona
  form so a bind named after a state leaf is recognised as the bind.
* **``agent.<node>.secret_path.<VAR>``** — the per-node SECRET category (spec §2a): a settable key
  (recognised on the ``+``-form too, before canonicalization). Checked here so get/show + the
  project-name heuristic treat it as a KEY. Also the NON-agent ``<scope>.secret_path.<VAR>`` scope
  form.
* **``<scope>.env.<VAR>``** (system/workset/box) — the LIVE env family (spec §2a L383). Its agent twin
  ``agent.<node>.env.<VAR>`` is recognised by the per-persona arm.
* **``agent.<node>.<key>``** — the per-persona agent key (block B1): a settable key (recognised on the
  ``+``-form too, before canonicalization) so get/show + the project-name heuristic treat it as a KEY,
  never a project name.
* **``box.agent.<key>``** — the box-scoped agent mirror (block B5, spec §2b): a settable box-scope key
  (so the get/show paths + the project-name heuristic treat it as a KEY, never a project name).
* **``{system,workset,box}.<bind-shaped category>.<name>``** — the RETIRED scope-level bind route (R-9
  for the two ``bindings`` arms; DS-BL1 = (a) for ``caches`` / ``seeded`` / ``common`` / ``synced``,
  which this same predicate now covers because its regex reads ``RETIRED_BIND_CATEGORIES``). Kept
  KEY-SHAPED for the same reason as the bare ``env.<VAR>`` spelling: the disambiguator must read it as
  a key so the verbs can refuse it with the cure (`scope_bind_retired_error`) rather than mistake it
  for a project name. It is also still READABLE (`config get`), which on its own makes it a key here.
* **``agent.<node>.<bind-shaped category>.<name>``** — the AGENT spelling of the branch above, RETIRED
  the same way and refused through the same door (`agent_node_bind_retired_error`). Recognized for the
  ONE reason that survives the shape flip: the disambiguator must read it as a KEY so the verbs can
  refuse it by name. ⚑ It does NOT mean the key is readable — a per-entry spelling is not a key at any
  scope now (see :func:`_is_path_category_key`); recognition here is what keeps the refusal from
  degrading into "unknown config key", or worse into a PROJECT NAME. ⚑ This branch read
  `_is_path_category_key` until 2026-08-08c, when that predicate's regex began failing closed and took
  the whole agent-scope arm down with it silently. The recogniser is now derived from the same
  ``RETIRED_BIND_CATEGORIES`` the file-scope branch uses.

```is_config_file_only_key(key: str) -> bool```
Keys whose value is READ from the bootstrap config file rather than a settings file.

ONE family and no more: the Layer-1 ``[config]`` foundation keys (``config.*``, spec §1).

⚑⚑ **THAT IS THE DEFINITION, NOT A COINCIDENCE (2026-08-26).** ``system.setup_completed`` was the
last non-``config.*`` member; its STORAGE moved to ``@config.settings`` (spec §2g), so what is left
is exactly the set of keys whose job is to LOCATE the files everything else lives in. Jei:
*"kanibako_config.yaml <-- cannot have settings. Period."*

⚑⚑ **IT IS A READ ROUTE NOW, NOT ALSO A REFUSAL (2026-08-23).** It used to double as *"and
therefore the write verbs refuse it"*, which is how `system.setup_completed` — declared
`set: cli+file`, *"PERSISTS, user-resettable"* (spec §2g) — ended up unsettable AND unresettable.
`config.*` is refused earlier by `_config_key_refusal`, its own ruled message. What is left here is
one question: does a READ come from the config file.

⚑⚑ **THE ``SYSTEM_PATH_DEFAULTS`` FAMILY LEFT THIS PREDICATE ON 2026-08-23, AND THE NAME CHANGED
WITH IT** (it was `is_system_path_key`, which after the narrowing would have answered False for
every system path key — a name that lies). All eleven are ordinary Layer-2 settings keys (spec
§2g), the manifest marks each ``set: cli+file``, and §2a names ``system.template`` in the
CLI-settable list; they route through `_KEY_ROUTES` to the system SETTINGS file now, like their
``workset.*`` twins. ⚑⚑ **AND SINCE 2026-08-26 `kanibako_config.yaml`'s ``[system]`` table IS NOT A
LAYER AT ALL**: `load_system_config` filters its config-file reads to ``config.*``, so the FLOOR is
`paths_defaults.SYSTEM_PATH_DEFAULTS` alone and a ``system:`` table hand-written into the bootstrap
file moves no path. ⚑⚑ **THE STORAGE HALF IS CLOSED (2026-08-23).**
`load_system_config` layers the SYSTEM SETTINGS file's ``system:`` table over that floor, filtered to
`SYSTEM_PATH_DEFAULTS`, so a repoint now reaches every `StandardPaths` field as well as the cascade.
It did not before: `config set system.canon=/tmp/mycanon` moved the cascade value and left
`std.canon` on the default — accepted, persisted, half-effective. The guard is
`tests/test_settings/test_repoint_reaches_std_paths.py`, which asserts the EFFECT (does the
repointed path come back out of `load_std_paths`), because every destination pin was green
throughout.

The F2/F3 fix: this is a PRECISE family membership check, NOT a ``system.*``-wide catch-all. A
``system.*`` SETTINGS key (the auth chain `system.auth.share_allowed`, `system.agent`, categories,
env) is NOT this family — `resolve_system_paths` drops unknown ``[system]`` entries, so routing such a
key to the config file was a write-only no-op; the launch reads them from the system SETTINGS file
(``@config.settings``). Those keys now fall through to their settings-tier routing.

``system.setup_completed`` IS NOT in this family any more (2026-08-26). It was, for exactly as long
as its shipped reader read the ``[system]`` table of ``kanibako_config.yaml``; that reader now reads
``@config.settings``, which is what spec §2g always declared it to be, so the divergence flagged
here is closed rather than carried.

The ``config.`` branch is still consulted on the READ/show path. The set/reset paths now short-circuit
``config.*`` earlier with the ruled refusal (block B2), so that branch no longer reaches
`system_key_refusal` for a ``config.*`` set/reset. The `SYSTEM_PATH_DEFAULTS` import is LAZY:
`config_interface` ↔ `paths` would cycle at module load.

```_user_config_file_str() -> "Path | str"```
The RESOLVED user bootstrap config file, for refusal messages.

Rendered (JC-B2-1) so a non-default ``$XDG_CONFIG_HOME`` shows the user's real file. This is an ERROR
path — it must never itself raise: if XDG/``$HOME`` resolution fails (`xdg` falls back to
`Path.home()`, which raises when ``$HOME`` is unset), fall back to the documented literal default
rather than turning a clean refusal into a traceback.

```_config_key_refusal(canonical: str, *, action: str) -> str```
Error string refusing a CLI set/reset of a ``config.*`` foundation key.

RATIONALE (Jei, load-bearing): ``config.*`` keys LOCATE the files everything else is stored in
(``config.settings`` IS where the settings file lives; ``config.registry`` IS the registry). A key
cannot live IN the file it locates → they live in the bootstrap config file, resolved BEFORE anything
loads. So the CLI is a *settings* manager: it READS ``config.*`` (to find where to write settings) but
NEVER WRITES them — there is no coherent file to write them to. The bootstrap config file is a
HUMAN/ADMIN hand-edited surface. The message deliberately does NOT mention `setup` (naming it would
wrongly imply it is how you set a ``config.*`` value).

*action* is ``"set"`` or ``"reset"`` — selects the verb (a `set` can only be done by editing the file;
a `reset` is a change, so it says "changed") while pointing at the SAME resolved config file.

The path is RENDERED via :func:`_user_config_file_str` (JC-B2-1: the user's real resolved file, with a
raise-proof fallback — see that helper).

```_is_scope_secret_key(key: str) -> bool```
True iff *key* is a NON-agent ``<scope>.secret_path.<VAR>`` SECRET-category key (system/workset/box) —
settable to the command scope's own settings file.

```_is_pref_key(key: str) -> bool```
True iff *key* is a ``pref.<target-key>`` REQUEST key (spec §2h).

SHAPE ONLY — deliberately not a validity test. Its job on the :func:`is_known_key` path is the
positional-vs-key DISAMBIGUATOR (is this argument a config key or a project name?), and for that the
prefix is both sufficient and correct: no project is named ``pref.something``. The real validation runs
in the set / get / reset branches, where a bad request can be reported with a reason instead of
silently reinterpreted as a project.

```_pref_level(command_scope: "ConfigLevel | None") -> str | None```
The pref LEVEL name for a command scope, or `None` where a pref is illegal (spec §2h — workset and box
ONLY).

```_pref_write_site_error(canonical: str, command_scope: "ConfigLevel | None", *, verb: str = "set") -> str | None```
Refuse a ``pref.*`` WRITE outside the workset / box scopes (spec §2h).

⚑ *"Where a pref may be written. Workset (L3.2) and box (L4.2) levels ONLY — never base, system or
agent. **This is what BOUNDS the recursion**, so it is a hard rule, not a convenience. ``config set
pref.<key> <value>`` at base/system/agent scope must RAISE, not silently write a dead entry."*

Checked BEFORE the three TARGET filters: a user at the system scope must be told the FILE is wrong
regardless of the target's quality — fixing the target first would only surface this error afterwards.

Returns an ``Error: …`` string when refused, else `None`. ⚑ The redirect hint ("Set '<target>'
directly at the <scope> scope instead") is appended for a scope-prefixed target but NOT for a
YAML-only one (:func:`has_no_cli_write_route`): there is no direct set to redirect to, and naming one
would prescribe a command that refuses.

```_pref_target_error(canonical: str, command_scope: "ConfigLevel | None") -> str | None```
Run the three §2h filters on the ``pref.*`` TARGET KEY at SET time.

Same predicate the launch path applies TO THE KEY. ⚑ The KEY is only half of a request: the VALUE is
checked separately by :func:`_pref_value_error`, against the TARGET's shape and resolution. An earlier
version of this docstring claimed set-time and launch-time validation were equivalent — they were not,
and the gap was real: a scalar at a bind-shaped target, and an unresolvable ``@``-ref, were both
accepted here and failed at LAUNCH.

```_pref_sections_leaf(canonical: str) -> "tuple[tuple[str, ...], str]"```
The nested write location for a pref: ``(("pref", *head), leaf)``.

```_scope_bind_match(key: str) -> "re.Match[str] | None"```
The :data:`SCOPE_BIND_KEY_RE` match for *key* — the ONE parse site, so the predicate below and the
message it feeds can never disagree about what they matched.

```_agent_bind_match(key: str) -> "re.Match[str] | None"```
The :data:`~kanibako.settings.settings_categories.AGENT_BIND_KEY_RE` match for *key* — the ONE parse
site for the agent-scope retired spelling, so the predicate below and the message it feeds can never
disagree about what they matched (the file-scope twin is :func:`_scope_bind_match`).

```_is_agent_scope_bind_key(key: str) -> bool```
The RETIRED AGENT-scope bind route ``agent.<node>.<bind-shaped category>.<name>``.

The EXACT counterpart of :func:`_is_scope_bind_key`, over the same derived category set, for the one
scope that regex cannot cover (the node segment needs a non-greedy split). Its job is the same and it
is the whole job: RECOGNISE the retired spelling so the write verbs refuse it BY NAME with a cure
(:func:`agent_node_bind_retired_error`) and so :func:`is_known_key` does not mistake a key for a
project name — spec §0 refuses loudly, never quietly.

⚑ IT IS A SUPERSET of :func:`_is_agent_node_bind_key`, deliberately. That predicate covers the
``bindings`` arms alone because it also picks a READ route (the agent file's address rule) and is the
one carve-out in the ``agent`` noun's §0 read gate; this one picks none, so it can cover all six. Where both matter the narrow one is checked FIRST — recognition
may be broad, resolution may not.

⚑ It answers False for an UNDISCRIMINATED ``agent.<category>.<name>``: the agent tier is discriminated
(spec §0/§2d), so that spelling is not a key and must not be dignified with a retired-route message
that implies it once was one.

```_is_scope_bind_key(key: str) -> bool```
The RETIRED SCOPE-level bind route ``{system,workset,box}.bindings.{ro,rw}.<name>``.

⮕ **R-9 RETIRED THE SCOPE-LEVEL CLI ROUTE** (disk-store rework step 1). The two ``bindings`` arms are
becoming a TERMINAL key whose VALUE is a dest-keyed map, and *the inner map's keys are not part of the
keyspace* — so there is no dotted key left for `config set` / `config reset` to name. The keys
themselves are NOT retired: they are still declared, still authored in the settings YAML, still
delivered at launch. Only the CLI route is gone, and it is a KNOWN, ACCEPTED loss (backlog DS-BL1).

Like :func:`_is_bare_env_key` and :func:`_is_box_agent_key`, this predicate exists to RECOGNISE the
retired spelling so the write verbs refuse it BY NAME with a cure (:func:`scope_bind_retired_error`)
instead of degrading to "unknown config key" — spec §0 refuses loudly, never quietly.

⚑ It does NOT cover the AGENT scope. Those spellings are retired too (the SAME door,
:func:`agent_node_bind_retired_error`), but a node segment needs a non-greedy split +
``℘``-canonicalization, so they have their own recogniser: :func:`_is_agent_scope_bind_key`.

```_retired_because(category: str) -> str```
WHY a bind-shaped category has no CLI write route — the one clause that differs BY CATEGORY rather
than by scope, so neither door invents its own story.

Two clauses, and what differs between them is now the PROVENANCE, not the outcome:

* ``bindings.{ro,rw}`` — the SHAPE removed the key first (R-5/R-6/R-9, 2026-08-06c). The arm is a
  single TERMINAL key holding a dest-keyed map, so a per-name dotted key does not exist to name.
* ``caches`` / ``seeded`` / ``common`` / ``synced`` — the RULING removed the route first: DS-BL1 = (a)
  (Jei, 2026-08-07g) made every bind-shaped category YAML-only *"uniformly"*, an accepted user-surface
  loss, while their per-name key was still real. The SHAPE caught up on 2026-08-08c — they are
  dest-keyed TERMINAL keys too now, so a per-name key does not exist for them either.

⚑⚑ THIS DOCSTRING USED TO SAY THE OPPOSITE, and the correction is the point: it claimed the four
"still carry a per-name key that `config get` reads back fine", and warned that giving them the shape
reason would be confidently wrong. After the flip the shape reason is TRUE for all six; what is left
of the split is history, and history is why the two clauses still read differently rather than a live
difference in what the user can do.

⚑ The two RETURNED strings are UNCHANGED by that correction, deliberately:
`tests/test_settings/test_config_interface.py`'s
`TestCategoryConfigSet.test_the_refusal_states_the_RULING_not_the_shape` pins the two wordings apart on
the OLD justification, so collapsing them is a behaviour+test change that belongs to whoever owns that
file — recorded here, not smuggled in.

```has_no_cli_write_route(target: str) -> bool```
True iff *target* has NO `config set` route at all, so a message must NOT tell a user to "set it
directly".

⚑ THIS EXISTS BECAUSE A CURE THAT NAMES A NONEXISTENT COMMAND IS WORSE THAN NO CURE. The `pref`
refusals append "Set '<target>' directly at the <scope> scope instead" for any scope-prefixed target,
which is true for a SCALAR and false for every YAML-only one: the bind-shaped categories (R-9 for the
two ``bindings`` arms, DS-BL1 = (a) for the other four) and ``masks``.

Covers every spelling a YAML-only target can take: the file-scope per-entry key, the agent-scope
per-entry key, and a bare TERMINAL key (``<scope>.{caches,seeded,common,synced}`` /
``<scope>.bindings.<arm>`` / ``<scope>.masks``).

⚑ The agent-scope term is :func:`_is_agent_scope_bind_key`, not :func:`_is_path_category_key`: the
latter fails closed for every key since 2026-08-08c, so this predicate had silently stopped covering
``agent.<node>.{caches,seeded,common,synced}.<name>`` — and the pref refusal was appending "Set it
directly at the agent scope instead", prescribing a command that refuses. That is exactly the failure
this function exists to prevent.

⚑ The terminal term is the WHOLE-KEY predicate (QC): *target* is a canonical scope-rooted key, so the
category must sit where the SCOPE ends. ⚑ THE ``system.channels.*`` PATHS ARE NOT COVERED BY THIS
FUNCTION AND NEVER WERE — the suffix test claimed ``system.channels.common`` alone, by coincidence
of spelling, while its five siblings fell through. They are ordinary routed scalars (2026-08-23),
not a category, and no pref can name one: §2h admits only ``system.agent`` and ``agent.<agent>.**``
as targets. Adding that family here is a separate call, not a silent widening of this one.

```_bind_route_retired_message(display_key: str, *, verb: str, route: str, why: str, cure: str, survives: str) -> str```
THE refusal text for a retired bind-shaped CLI write route — ONE wording for both scopes, so the two
doors cannot drift into two stories about one ruling.

*display_key* is the key as the USER should see and retype it — for the agent form that is the ``+``
spelling, never the ``℘`` canonical one, because the message ends by handing the key back in a `config
get` the user is meant to run. *route* is the retired SPELLING as a shape
(``'box.bindings.ro.<name>'``); *why* is :func:`_retired_because`'s per-CATEGORY clause; *cure* is the
one sentence that differs between the two scopes — WHICH file to hand-edit, because the file genuinely
differs (a scope settings file vs the node's own ``agents/<node>/agent.yaml``).

⚑ The cure is HONEST about the loss. There is no equivalent CLI spelling to redirect to — that is
precisely what R-9 and DS-BL1 accepted — so the message names the settings FILE as the surface, which
is real and reachable (the launch cascade reads that tuple today, exactly as written). Prescribing a
command that does not exist would be worse than naming the loss.

*survives* is the closing sentence: WHAT the user still has. ⚑ IT IS A REQUIRED PARAMETER, not a
default, because the honest answer genuinely differs by door and a default would let a door inherit
the wrong one silently. It used to be a hardcoded "reading it back still works", which the 2026-08-08c
shape flip made false at the agent scope: the per-entry AGENT spelling has no read route at all now
(`_is_path_category_key` answers False for every key), so a message promising one would prescribe a
cure the user cannot verify — the F6 lie in a new place.

```scope_bind_retired_error(canonical: str, *, verb: str) -> str | None```
The refusal + cure for a RETIRED file-scope bind-shaped WRITE
(``{system,workset,box}.<bind-shaped category>.<name>``), or `None` when *canonical* is not one.

⚑ It covers ALL SIX categories, and it widened WITHOUT AN EDIT HERE:
:data:`~kanibako.settings.settings_categories.SCOPE_BIND_KEY_RE` is built from
``RETIRED_BIND_CATEGORIES``, which is derived as the difference from the (now empty)
``SETTABLE_BIND_CATEGORIES`` — DS-BL1 = (a).

*verb* is the op word for the message (``"set"`` / ``"reset"``). Gates itself — `None` for every other
key — so every verb door applies it uniformly.

⚑ The closing sentence still points the FILE-scope read at the per-entry spelling, because at these
three scopes a slot is still claimed for it (`config_dest._key_slot`'s `_is_scope_bind_key` term) —
unlike the agent scope, where nothing claims one. That asymmetry is a fact about the read routing, not
a difference in the ruling.

```agent_node_bind_retired_error(canonical: str, *, verb: str) -> str | None```
The refusal + cure for a RETIRED AGENT-scope bind-shaped WRITE ``agent.<node>.<bind-shaped
category>.<name>``, or `None` when *canonical* is not one.

The SIBLING of :func:`scope_bind_retired_error` — same ruling, same wording, one different cure: the
tuple lives in the NODE's own settings file ``agents/<node>/agent.yaml``, under the flat
``self.<category>`` table (the shape `settings_assemble._agent_partial` reads into the launch
cascade, re-rooting ``self`` to ``agent.<node>``), not in a scope table. Naming the scope file
here would send a user to edit a file the launch never reads for this key.

⚑ **THE NODE IS NOT IN THE TABLE SPELLING, and its absence is the point (S2):** the file IS that
node's, so ``self:`` expands to ``agent.<node>`` and the category table sits DIRECTLY under it — a
``self.<node>.<category>`` table is REFUSED by the boundary now. The node stays in the PATH the
message also prints, which is what tells the user which file to open. ⚑ The spelling comes from
``agent_file.file_spelling(category)``, never a literal here.

⚑ ONE PARSER, ALL SIX. Recognition comes from
:data:`~kanibako.settings.settings_categories.AGENT_BIND_KEY_RE`, the derived agent-scope twin of
``SCOPE_BIND_KEY_RE``, so the agent door covers exactly the categories the file door does and neither
can quietly stop covering its share. ⚑⚑ IT USED TO BE TWO — `parse_agent_node_bind_key` for the
``bindings`` arms and ``BIND_KEY_RE`` for the other four — and the second half went silently DEAD on
2026-08-08c when the shape flip emptied ``BIND_KEY_RE``'s non-terminal complement and it began
compiling ``(?!)``. The four then had NO agent-scope recogniser at all: ``config set
agent.claude.caches.pip`` answered "unknown config key", and ``box reset agent.claude.caches.pip``
re-read the key as a PROJECT NAME. Recognition is derived from ONE source now precisely so a
membership change cannot silently unhook a door again.

⚑ WHAT SURVIVES DIFFERS BY ARM, and the message says which. A ``bindings`` arm keeps its per-entry
READ (:func:`_is_agent_node_bind_key` routes it to the node's file), so the message offers it. The
other four have NO per-entry read at any scope any more, so their message names the TERMINAL key — the
whole dest-keyed map — instead of promising a `config get` that would answer for a key that does not
exist.

The node is rendered in its USER-FACING ``+`` spelling (:func:`display_agent_ref`) — ``℘`` is a
keyspace-internal separator and must never reach a message.

```agent_key_reason(node: str, tail: str) -> str | None```
The §0 reason *tail* is not a declared key of agent *node*, or `None` when it is — **the `agent`
noun's closed keyspace, spelled ONCE** (S3, defect D-5).

⚑ **ONE CONSTRUCTION, THREE CONSUMERS**: the verb's WRITE gate, its READ gate, and the LAUNCH
boundary's passthrough refusal (`agent_file.state_level`). A REASON rather than a message, for the
reason `config_dest.NodeRouteRefusal` gives — the rule is one, but a verb owes a cure and a refused
launch owes a file to open.

⚑⚑ **`key_validity`, NEVER `is_known_key`, AND THE DIFFERENCE IS MEASURED.**
`is_known_key("agent.claude.self.model")` is **True** — `_parse_persona_agent_key` splits on the LAST
segment and reads the node as ``claude.self`` — so a literal `is_known_key` gate would leave ruling
55's exact hole open while REFUSING ``run_args`` and ``name``, both live and both pinned.
`is_known_key` answers *"is this key-SHAPED, as opposed to a project name"*; §0 asks *"is this a
DECLARED key"*, and only `key_validity` answers that. **Do not "simplify" this to the other
predicate.**

⚑ **The node is supplied AS the valid-agent set** (`valid_agents=(node,)`): it is the on-disk store
dir, known good, so an agent-DISCOVERY result must never be able to refuse a key on an agent the user
is demonstrably running. The PLUGIN-declared leaves are unioned in the OTHER direction — without it
a legitimate ``agent.goose.provider`` would be refused, at the verb AND at the launch. Both
directions are pinned, with the union's own mutation proof.

⚑ **THE UNION ARRIVES AS `PLUGIN_DECLARED_LEAVES`, NOT AS `default_valid_agents().leaves` READ
HERE** (2026-08-29). Same value, but reached through the module's ONE supplier, which is what stops
this gate and `_PERSONA_STATE_LEAVES` from drifting into two vocabularies again — and passing it
rather than materialising it means a core §2d leaf is answered without importing a single plugin.

⚑ **THE IDENTITY RESIDUE**: `name` / `run_args` are FILE-identity fields of `AgentConfig`, not
keyspace leaves (`agent_file._MODELED_KEYS` already says so). `run_args` happens to be a declared §2d
leaf too; `name` is not, so the `IDENTITY_KEYS` allowlist is what keeps a shipped, pinned surface
working — refusing it would be a breaking change no ruling asks for.

```agent_write_key_error(node, tail, *, verb) -> str | None``` ·
```agent_read_key_error(node, tail) -> str | None```
The two verb-facing wrappers. WRITE is the reason with the verb's wording. READ is the same, PLUS the
ONE carve-out: ``agent.<node>.bindings.{ro,rw}.<name>`` is not a declared key — the arm is terminal
and its entries are destinations inside the value — but `config get` reads it anyway (R-9: the read
survived the write), and the hand edit that refusal prescribes is only checkable if the read-back
works. Two verbs over ONE file must not disagree, so the carve-out is taken from the SAME predicate
`get_config_value` branches on (`_is_agent_node_bind_key`) rather than restated.

⚑ Reading an undeclared key is an error under §0 exactly as writing one is — which is why the read
gate exists at all: ``agent get claude self.model`` REFUSES instead of answering "(not set)".

*verb* is the op word (``"set"`` / ``"reset"``). Gates itself — `None` for every other key — so every
verb door applies it uniformly.

```_is_path_category_key(key: str) -> bool```
True iff *key* is a PER-NAME PATH-TUPLE category key.

⚑⚑ **IT IS NOW FALSE FOR EVERY KEY, AND THAT IS THE CORRECT ANSWER** (2026-08-08c). It is
:data:`~kanibako.settings.settings_categories.BIND_KEY_RE`, which is built from the NON-TERMINAL bind
categories — and that complement EMPTIED when ``caches`` / ``seeded`` / ``common`` / ``synced`` went
dest-keyed, so the regex compiles its fail-closed never-matching form. There is no per-name dotted key
under ANY bind-shaped category left to match, at any scope.

⚑ IT IS KEPT, NOT INLINED TO `False`, and the difference matters: this is the ONE place that asks
"does a per-entry bind key exist here", and it must keep asking the REGEX. Re-admitting a per-name
category would then be an edit to ``_NON_TERMINAL_BIND_CATEGORIES`` alone, not a hunt through
hardcoded answers. ⚑ Its DELETION (with :func:`_has_dedicated_route`'s already-removed term) is a ruled
follow-up, not a drive-by: its remaining call sites read it.

⚑⚑ IT IS NO LONGER A RECOGNISER, AND THAT WAS A REPAIR. `is_known_key` and
:func:`has_no_cli_write_route` both used it as the AGENT-scope arm of a "recognise the retired
spelling" chain, so when the regex began failing closed those two arms went dead with it — no refusal
by name, and a key re-read as a project name. Both now ask :func:`_is_agent_scope_bind_key`, which
answers RECOGNITION from ``RETIRED_BIND_CATEGORIES``. This predicate answers only EXISTENCE, and the
honest answer is False.

⮕ **READ-ONLY SINCE DS-BL1 = (a)** (Jei, 2026-08-07g — *"accept the loss uniformly"*). This USED to
mean "settable via `config set`" (the source-only RAW repoint, S24); every bind-shaped category is
YAML-only, so the repoint route is gone and the write verbs refuse these keys BY NAME in their
preamble (:func:`scope_bind_retired_error` at the file scopes, :func:`agent_node_bind_retired_error` at
the agent scope).

**A key that IS still recognised** is recognised elsewhere: the RETIRED per-name spellings by
:func:`_is_scope_bind_key` (file scopes) / :func:`_is_agent_node_bind_key` (agent scope), and the
DECLARED terminal keys by :func:`~kanibako.settings.settings_keyspace.is_terminal_category_tail`.

``env`` (scalar) is NOT matched here — the live ``<scope>.env.<VAR>`` arm is routed by the earlier
:func:`_is_scope_env_key` branch as a plain scalar write, and the bare spelling is refused before
dispatch (R-39); ``masks`` (a keyed list) is YAML-only (spec §2a) and is NOT matched here.

```_has_dedicated_route(canonical: str) -> bool```
Does SOME `set_config_value` branch claim *canonical*?

⚑ MIRRORS THE DISPATCH CHAIN in `set_config_value`, in the same order. A branch added there without a
term here would make this say "no route" for a key that in fact has one — so the two must be edited
together, and `TestSetDispatchCoverage` fails if they drift. PREAMBLE guards are NOT terms: a key
refused before the dispatch (the bare-agent scalars at box/workset; the retired bare ``env.<VAR>``,
R-39) never reaches the probe this feeds, so a term for it here would be a second spelling of the
refusal. **Every BIND-SHAPED category is now in exactly that position** — the two ``bindings.{ro,rw}``
arms (R-9) and, since DS-BL1 = (a), ``caches`` / ``seeded`` / ``common`` / ``synced`` as well, at every
scope. None has a term here: all are refused in the verb preamble and never reach the dispatch. ⚑ The
`_is_path_category_key` term that used to sit here went with the repoint route it claimed; do NOT
restore it "so categories are covered" — it would report a route for a key nothing writes.

Its ONE job is to keep :func:`_probes_at_set_time` off keys nothing handles, so an unknown key still
reaches the routing table at the bottom of the dispatch and is reported as ``unknown config key:
<key>`` — the error §0 requires, which NAMES the key, rather than a resolution complaint about a value
on a key that does not exist.

```_probes_at_set_time(canonical: str) -> bool```
Does a `config set` of *canonical* run the E3 RESOLUTION probe?

The test is **"does this value reach the expander"**, NOT "is it a scalar". A value the expander never
sees carries no ``@``/``$`` SYNTAX — those characters are DATA in it — so probing would refuse
legitimate input with no correct spelling available.

Excluded, and why:

* **Keys nothing claims.** They must reach the routing table and be reported as an unknown KEY (see
  :func:`_has_dedicated_route`).

Every RETIRED bind-shaped route is in the SAME position as the bare env spelling: refused in the verb
preamble (:func:`scope_bind_retired_error` / :func:`agent_node_bind_retired_error`), so none reaches
this predicate and none needs an exclusion term of its own. ⚑ That now includes ``caches`` / ``seeded``
/ ``common`` / ``synced`` (DS-BL1 = (a); name-keyed at the time, terminal since 2026-08-08c), whose
`_is_path_category_key` exclusion used to be the first term here: it existed because the category path
ran its OWN probe through `validate_config_set`'s category arm, and probing twice would duplicate the
diagnosis. There is no category path any more (and QA′ deleted that arm), so the exclusion went with it
rather than being left to look like a live rule.

The docker ``.env`` family (bare ``env.<VAR>``, written VERBATIM to a file the expander never saw) WAS
the third exclusion. R-39 retired the spelling: it is refused in the verb preamble
(:func:`bare_env_retired_error`) and never reaches this predicate, so the exclusion went with the
route. Every scalar whose value the expander DOES see stays LOUD: ``box.shell``, ``workset.boxes``,
``<scope>.secret_path.<VAR>`` and the rest all probe, because for them a dangling ref really does
resolve to ``""`` silently at launch.

⚑ The KEYSPACE env arm ``<scope>.env.<VAR>`` (``box.env.FOO``) is a DIFFERENT key from the retired bare
``env.FOO`` and is deliberately NOT excluded — it IS host-expanded at launch
(`settings_launch._emit_scope_node` reads it off the EXPANDED snapshot), so a dangling ref in it fails
silently exactly the way the probe exists to catch. It now HAS a dispatch branch
(:func:`_is_scope_env_key`), so it reaches the fall-through and probes — the intended direction. Do NOT
"fix" a probe complaint on it by widening :func:`_is_bare_env_key`: that would drag the live scoped
keys into the retired spelling's refusal.

⚑ The ``pref.*`` early return, in full: the GENERIC probe is a NO-OP at a ``pref.*`` path and must not
run there — `expand` carries the ``pref`` subtree through unexpanded (spec §2h), so nothing in it is
ever resolved and no defect can be recorded. Worse, applying the candidate at the pref path WRITES the
target's leaf names into a `KeyStore`, so a target whose leaf is a RESERVED name (``…common.get``)
raised `ReservedKeyError` straight out of this function — breaking `set_config_value`'s "returns an
error string, never raises" contract. The pref route runs the REAL probe at the TARGET path instead
(:func:`_pref_value_error`).
