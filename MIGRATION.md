# kanibako migration guides

kanibako ships **no migration code**: each breaking change set is a manual runbook. Read
the guide for the release you are upgrading **to**; if you are skipping releases, work
through them oldest-first.

| Upgrading to | Guide |
|---|---|
| **v1.8.0** (from v1.7.2) | [Migrating to kanibako v1.8.0](#migrating-to-kanibako-v180) |
| **1.6.0** (from pre-1.6.0) | [Migrating to kanibako 1.6.0](#migrating-to-kanibako-160) |

1.7.x needed no runbook — those releases moved no on-disk state. Their breaking CLI and
config notes are in the [CHANGELOG](CHANGELOG.md).

---

# Migrating to kanibako v1.8.0

> **Audience:** a kanibako user on **v1.7.2** (the previous production release).
>
> v1.8.0 ships **no migration code**, by design: everything below is a manual step or a
> behavior note. Back up `~/.config/kanibako*` and your data directory, read the whole
> guide, then work top-to-bottom.
>
> **What a leftover key actually does.** It **stops the command** (§2.47). The settings keyspace
> is closed, and in v1.8.0 that is enforced where the settings are *resolved* — so a retired or
> renamed key left stored in your files is not carried through inert any more: every command that
> builds the resolved view refuses and names the entry. A handful of keys carry a message written
> for them instead of the generic one, with the cure spelled out (§2.1). The retired `workset.meta`
> identity table refuses earlier still (§2.43, and it is not a cascade key at all: it is the marker
> that makes a directory a workset root, and no longer lives in a settings file).
> ⚑ **The two surfaces that do not resolve handle it differently, and neither is silent**:
> `box show` without `--effective` never resolves, so it prints the entry MARKED as not a key
> rather than refusing, and `box get` / `workset get` refuse an undeclared name outright — rc 1,
> where v1.7.2 answered `(not set)` at rc 0 (§2.48).

Paths below: `<data>` is your kanibako data directory (default `~/.local/share/kanibako`;
whatever `config.data` points at if you moved it).

---

## 1. One page: what changed and what you must do

v1.8.0 is a deliberate **clean break** (no aliases, no deprecation window). Four released
config surfaces are removed outright: `box.agent_name`, `system.default_agent`, the `shared`
mount category, and `system.base_template`. Directory layouts also move, on the host and
inside boxes. In order of likely impact:

1. **Your first `kanibako start` (or `create`, or `agent reauth`) after upgrading is a hard error
   until you run `kanibako setup`.** v1.8.0 raises the setup baseline (`SETUP_BCV`), so the
   `setup_completed` marker your v1.7.2 config recorded is too old for the running build and
   the setup-compatibility gate hard-blocks: `Error: This kanibako config (1.7.2) is too old
   to auto-update. Re-run 'kanibako setup' before agent commands.` (rc 1). This is deliberate
   — setup is what installs the new host-store layout (§2.12). Run `kanibako setup` once,
   right after upgrading. Headless: `kanibako setup --refresh-templates` (add
   `--agent <name>` to skip the menu). ⚑ Pass `--refresh-templates` on a headless run: a
   non-interactive setup that cannot ask about the template refresh deliberately records
   nothing, prints `Setup Incomplete` and exits **rc 1**, so the block stays up (§2.12).

2. **Every settings file except the system one must be renamed by hand, or it is silently not
   read** (§2.45). Each cascade tier's file was called `settings.yaml`; each is now named for its
   tier — `box.yaml`, `workset.yaml`, `agent.yaml`. There is no compatibility read and no warning:
   a file left under the old name is invisible, and the box launches on defaults as though you had
   never configured it, which looks exactly like kanibako losing your configuration. It is the
   largest hand-migration in this release by file count. 🛑 `<data>/global/settings.yaml` is the
   system tier and must **not** be renamed.

3. **Your boxes will refuse to launch until you replace the agent-selection key.** Every box
   that ever chose an agent has `box.agent_name` stored; v1.8.0 refuses to launch such a box
   with an error that names the file and the exact fix (§2.1). One command per box:
   `kanibako box set pref.system.agent=<name>`. The system default moved too:
   `kanibako system set system.agent=<name>`. The older spelling `box.agent` — scalar (the
   pre-v1.6.0 `box.crab`) or table (the settable agent mirror) — is refused the same way, with
   its own cure per shape (§2.1).

4. **Upgrade the agent plugins WITH the base — never the base alone.** Upgrading only
   `kanibako-cli` while keeping v1.7.2-era agent plugins silently deletes your boxes' entire
   instruction/directive chain (no error is printed). Upgrade via the `kanibako` meta package,
   or upgrade the plugins first (§2.6). ⚑ A pre-1.8.0 plugin also **will not load at all** on
   this base — the flat core modules it imports are deleted, so kanibako skips that agent with a
   named warning (§3.1 lists the affected plugin versions). The plugins pin no upper bound on
   `kanibako-cli`, so this is what an unpinned `pip install --upgrade kanibako-cli` gives you.

5. **Claude plugins and cache will look EMPTY unless you move two directories** before your
   first launch on v1.8.0 (§2.5). Nothing errors — the box just sees empty dirs:
   `mkdir -p <data>/agents/claude/common && mv <data>/agents/claude/{plugins,cache} <data>/agents/claude/common/`

6. **The `commons` channel is now `common`** — on disk (host) and in-box
   (`~/channels/commons` → `~/channels/common`). Move the directories before first launch or
   an empty `common/` is created beside your populated `commons/`, silently (§2.3). Any
   scripts/notes of yours that reference the old path break silently.

7. **Instruction files move into the canon.** New boxes get `~/canon/{bible,handbook,notebook,
   workbook}` with a read-only, root-owned skeleton; `~/playbook` is retired as the entry
   point. Existing boxes keep launching but their own `~/playbook` directives **silently stop
   being loaded** and need hand-triage (§2.4).

8. **Two mounts at one destination now refuse to launch** where the more specific scope used
   to win silently (§2.2). The error says the rule changed and prints the exact YAML cure.
   Default installs are proven collision-free; only hand-added shares/binds can collide.
   After upgrading, `kanibako box show --effective` reports collisions without launching.

9. **Rename the `shared` category to `common` in your settings files** (e.g.
   `agent.claude.shared.plugins` → `agent.claude.common.plugins`). A leftover `shared` entry
   is not a key, so it stops the command rather than quietly dropping the bind it declared
   (§2.1, §2.47).

10. **Relative host paths in `workset share add` no longer resolve under the workset root at
    launch.** New adds are resolved and stored absolute at write time; **already-stored relative
    sources must be rewritten to absolute paths by hand** (§2.7). A bare-relative source anywhere
    in `bindings.ro`, `bindings.rw` or `synced` is now **refused by name** rather than passed to
    podman, which never mounted the directory you meant — it created a named volume. In `common`,
    `caches` and `seeded` a bare leaf is instead rooted under the declaring scope's store, so those
    entries start working (§2.50).

11. **The box template root moved and restructured** (`global/base_template/` →
    `global/template/box/home/`). Existing boxes are untouched (seeded once, long ago). The
    forced `kanibako setup` (item 1) re-creates the NEW tree with **stock packaged content**,
    so new boxes do NOT seed empty — but **any customizations you made in
    `global/base_template/` are orphaned there, silently**: nothing reads the old directory,
    nothing warns about it, and new boxes seed the stock files instead of yours (§2.5).

12. **System-scope binds/caches/secret pointers now live in ONE file** — `global/settings.yaml`,
    not `~/.config/kanibako_config.yaml`. If you ever hand-placed such entries in the config
    file (working around the old broken routing), move them (§2.8).

13. **A symlink anywhere in a template directory now fails box creation loudly** — if you
    symlinked template files into a dotfiles repo, replace them with real files or a bind
    (§2.13).

14. **If you use PERSONA agents, delete persona values you did not write yourself** from
    `agents/<node>/agent.yaml` — the store is now read live and a leftover synced value
    silently outranks it (§2.15). Also: a persona's whole `env` block now reaches the box, a
    rejected token is now a hard error on every `start`, and a generated agent settings file no
    longer carries `model` (§2.15, §2.16).

15. **If you pass flags to a box that may already be running, they are now refused instead of
    silently ignored** (§2.17). `kanibako start -N <running box>` used to reattach you to the OLD
    conversation without a word; it now errors. Same for `--rig`, `-e` (except where a second
    process in the box will apply it — see §2.17), `--browser`, `--share-images`, `--no-helpers`,
    `--no-auto-auth`, `-C`, `-R`, `-M`, `-A`, `-S`, and an
    explicit `--persistent`/`--ephemeral`. The cure is the new **`kanibako --restart [box]`**, which
    stops the box and starts it again with your flags in force. Scripts that start boxes with flags
    are the thing to check. (Two upsides in the same change: a reattach no longer builds images or
    makes network calls it cannot use, and `--entrypoint` against a live box now runs your command
    in it as a second process instead of being dropped.)

16. **If anything you run deletes a box directory and lets the next `start` put it back, it now
    errors instead** (§2.18). A launch never rebuilds a box: with the registration intact and the
    box directory gone, `kanibako start` used to silently re-create and re-seed it. It now refuses
    and prints the command that rebuilds it (`kanibako create <workspace>`, or `workset disconnect`
    + `workset connect` for a workset member). In the same section: `kanibako box set
    box.<key>=<value>` from a directory that is **not** a box now errors instead of writing a
    settings file for a box that does not exist.

17. **Your `env` files are no longer read, silently** — and the bare `env.<VAR>` key is refused
    (§2.19). The three docker-style `env` files (`<data>/env`, the workset one, the per-box one)
    were dropped; every `VAR=value` line in them stops reaching your boxes. v1.7.2 seeded
    `COLORTERM=truecolor` into `<data>/env` on first run, so **essentially every pre-existing
    install has one of these files**. A launch that finds a non-empty one now prints a notice
    naming the file and the per-tier cure. Move each var with
    `kanibako system set system.env.<VAR>=<value>` (or the `workset`/`box` equivalent), then
    delete the file. ⚑ **One exception, and it is the `COLORTERM` line kanibako put there
    itself: do NOT move that one** — `COLORTERM=truecolor` is a declared default now (§2.42),
    so it needs no key at all, and re-creating it at `system` scope would *refuse* your
    launches as a contested variable (§2.33). Just delete the line with the file.

18. **You can no longer `set` or `reset` a bind entry from the CLI — edit the settings file
    instead** (§2.20). `kanibako box set box.bindings.rw.home=/newhome` and `kanibako system set
    agent.claude.bindings.ro.launcher=/newsrc` both used to work; both now refuse, naming the key
    and the file to edit. **Nothing you have already configured stops working** — the keys are
    still declared, still read at launch, and the matching `get` still reads them back
    (`kanibako box get <box> box.bindings.rw`). Only the write verb is gone, and there is no CLI
    replacement. ⚑ One exception, and it is the example above: a binding at the box home is a
    separate change and does **not** keep mounting (item 20). If a script of yours repoints a
    bind, that is the thing to check. The other mount categories
    (`caches`, `seeded`, `common`, `synced`) are untouched and still settable at every scope.

19. **`workset share add` / `rm` lost their NAME argument** (§2.21). `workset share add WS NAME
    host:guest` is now `workset share add WS host:guest`, and `workset share rm WS NAME` is now
    `workset share rm WS DEST` — the box destination, exactly as `share list` prints it. ⚑ **The
    stored shape changed too, and an old entry is MISREAD rather than rejected** — a two-element
    value now means `[source, options]`, so an old `name: [src, dest]` is read as a share at the
    NAME with the destination taken for mount options. Nothing lands in the wrong place: the launch
    fails at the container runtime rather than mounting, but it fails without naming the cause.
    **Re-add each existing share** (one command
    each), or edit the workset file to key on the destination. Scripts that call `workset share` are
    the thing to check, and so is anything that parses `share list`, whose columns are now
    `DEST / MODE / SOURCE`.

20. **If you gave a box a custom home with a binding at `~`, that box no longer starts** (§2.32).
    The box home stopped being a binding — it is the foundation the rest of the mount set folds
    over — so an entry at `~` in any settings file is now a second claim on one place and refuses
    the launch by name. Nothing else moves: a binding *inside* home (`~/work`) is unaffected, and
    the mount a box receives at `~` is byte-identical to before. **The cure is `workset.boxes`**,
    the workset-scope key naming where box stores live, plus moving the directory yourself. Home
    also leaves the per-scope `bindings.*` listing in `kanibako box show --effective` and appears
    above it as a labelled foundation line.

21. **If the same environment variable is declared at two scopes, that box no longer starts**
    (§2.33). `system.env.EDITOR` alongside `box.env.EDITOR` used to launch with the innermost
    scope's value and no word about the declaration it discarded; it now refuses, naming both
    keys. A variable is a slot with one value, and each scope acts in turn from the outside in
    — the same rule two bindings at one destination follow. **The cure is one owner:** delete
    one of the two keys. ⚑ Overriding is untouched — the *same* key in more than one file is
    the ordinary cascade and the nearest file still wins. ⚑ **Check your persona if one of the
    keys is not in any of your files:** a persona's store config supplies `env:` entries as
    live agent-scope keys that are never written to disk (§2.33, §2.15).

22. **Every NAMED workset needs a one-time hand edit to its root `workset.yaml`, or it stops
    resolving** (§2.43). The identity moved OUT of the root settings file and into the root
    `registry.yaml`, where the box membership already lives: `workset: {meta: {…}}` becomes a
    `workset:` table plus a name-keyed `projects:` map in `registry.yaml`. Until you do it, every
    command that has to resolve that workset **refuses**, naming both files and the exact move.
    ⚑ **This one is high on the impact list even though it sits low on this page:** it hits every
    workset made by v1.6.0 or v1.7.x, and there is no auto-migration. Primary-mode and standalone
    boxes have no such table and need nothing.

23. **A key kanibako does not declare, sitting in any settings file, now stops the command
    instead of resolving to nothing** (§2.47). It used to parse, merge, resolve — and then be read
    by nobody, with no error and no warning. Every command that resolves settings refuses now,
    naming every offending entry at once and the files the resolve loaded. The cure is a
    hand-edit: `box reset` cannot remove what is not a key, and `box show --effective` resolves
    through the same seam, so it refuses too. Most often this is a typo or a key retired by this
    release, so the fix is one deleted line. **`box get` and `workset get` refuse such a name too
    now — rc 1 where v1.7.2 said `(not set)` at rc 0 — and `show` lists the offending lines so you
    know which ones to delete** (§2.48). **`kanibako setup` stops at Step 3 and exits 1** when
    settings will not resolve, where it used to name a wrong cause and finish with `Setup Complete`
    at rc 0; `system diagnose` and `rig diagnose` print the refusal instead of `cannot check`
    (§2.49).

24. **An agent or persona name containing a `.` now hard-errors, and that node is stuck**
    (§2.52). `kimi.k3+claude` was legal in v1.7.2. A node name is a keyspace segment and `.` is
    the key-path separator, so it is refused now — by *every* command that parses the ref,
    including any that might have fixed it. The rename is by hand and in order: the node's store
    directory under `<data>/agents/`, then `pref.system.agent` in each box's file and
    `system.agent` in the system one. Only persona names are affected; no plugin harness name has
    a dot.

25. **A `box:` table you once wrote into the SYSTEM settings file now steers every box** (§2.53).
    `kanibako system set box.image=…`, `box.share_images=…` and `box.shell=…` were accepted and
    stored in v1.7.2 and then read by nothing — the box scalars resolved on a path that never
    consulted that file. v1.8.0 resolves all three through the cascade, where the system file is a
    real level. Read `<data>/global/settings.yaml` before your first launch and delete anything
    under `box:` you did not mean to keep.

26. **Boxes created before v1.8.0 will not follow a changed default image; boxes created after it
    will** (§2.54). `create` used to store the resolved image into the new box's own settings file
    whether or not you passed `--image`; it now stores only what you pass explicitly. Nothing in
    the CLI labels the two halves — the presence of `image:` in a box's own `box.yaml` is the only
    tell. `--share-images` moved the other way and now *does* persist at create, and passing
    either flag to an already-existing box now prints a notice instead of doing nothing quietly.

27. **If `config.data` does not end in `kanibako`, three state stores moved with it** (§2.55).
    Saved `kanibako code --remote` tunnel contexts are the part you notice — re-run the command
    once per context. Default installs are unaffected.

28. Smaller items: standalone boxes' `box get` got truthful (§2.9); a box suppressed to
    plain-shell keeps stale credential files in its home (§2.10); several never-released or
    expected-empty renames (§2.11); two `--null` CLI bugs fixed (§2.14); a customized helper
    entrypoint script moves to `~/canon/notebook/scripts/helper-init.sh` (§2.44).

---

## 2. Per-area detail

### 2.1 Settings keys renamed or retired

| old (v1.7.2) | new (v1.8.0) | left in place, it is… |
|---|---|---|
| `box.agent_name` | `pref.system.agent` (workset/box files only) | **hard launch error** (below) |
| `box.agent` (scalar — the v1.6.0 rename of `box.crab`) | `pref.system.agent` (workset/box files only) | **hard launch error** (below) |
| `system.default_agent` (stored as `agent: default: default_agent:` in `global/settings.yaml`) | `system.agent` (same file, `system: agent:`) | **hard launch error** (below) |
| `<scope>.shared.<name>` (the `shared` mount category) | `<scope>.common.<name>` | **hard refusal** at the resolve (§2.47) |
| `system.base_template` | `system.template` (and it now names a template ROOT — §2.5) | **hard refusal** at the resolve (§2.47) |
| `@meta.runtime.ws_settings` (reference target) | `@meta.workset.settings` | dangling reference |
| settable `box.agent.*` mirror (a `box: agent:` **table**) | read-only `meta.box.agent.*` read-back; write via `pref.agent.<agent>.<key>` | **hard launch error** (below); write verbs refuse with the pref cure |
| `auto_approve` (the boolean permission switch) | `access` — a tier, `restricted` \| `editing` \| `full` | **hard launch error** of its own, mapping your stored boolean to a tier (below) |

**What a stale stored key actually does, per surface** (measured on the shipped code):

| surface | behavior |
|---|---|
| launch / `box show --effective` | **hard refusal**, naming the entry and the files the resolve loaded (§2.47) |
| `box show` / `workset show` / `system show` (stored view) | **listed and marked** — the entry is printed under an `(undeclared …)` heading naming the file (§2.48) |
| `kanibako box get <box> <stale key>` | **loud** — refused naming the key and why, rc 1 (§2.48) |
| `kanibako workset get <workset> <stale key>` | **loud** — refused naming the key and why, rc 1 (§2.48) |
| `kanibako box get <stale key>` (no box argument) | `Error: Unknown project or workset: '<key>'` — the unknown key is taken for a project name |
| `kanibako system get <stale key>` (typed) | **loud** — refused naming the key and why, rc 1 (§2.48) |
| `kanibako system set <stale key>` (typed) | **loud** — `Error: unknown config key: …`, rc 1 |
| `box.agent_name` / `box.agent` / `system.default_agent` stored anywhere in the cascade | **hard refusal** at launch and in `box info`, carrying that key's OWN message and cure (below) |
| `auto_approve` stored in the system settings file | **hard refusal**, likewise with its own message and cure (below) |

**The retired agent-selection keys get a refusal of their own**, carrying the cure rather than
just the name, because a guessed agent would silently run a *different* agent and seed that
agent's credentials into your box. The launch error (verified verbatim on a scratch box; `box
info` and `box show --effective` stop with the same message instead of printing their report):

```
'box.agent_name' is RETIRED and is still set in the box settings file <path> (as `box: agent_name:`).
The RULE CHANGED in kanibako 1.8.0: a box no longer names its agent with a key of its own — it
REQUESTS one at the key that resolves earlier (`pref.system.agent`, spec §2h), and the system
default is now `system.agent` (§2g). Refusing rather than running: kanibako cannot tell which
agent you meant, and guessing would launch a DIFFERENT agent and seed that agent's credentials
into this box.
  Fix: kanibako box set <box> pref.system.agent=<value>   (or `kanibako box set <box> --null pref.system.agent` for a no-agent box)
  then delete the `box: agent_name` entry from <path>.
```

⚑ **This is the message you get, even though the check that stops you is §2.47's.** A retired key
is an undeclared key too, so the closed-keyspace refusal reaches it first — and before printing
its own text it asks whether anything more specific is known about the file, which for these keys
there is. It asks only of the tables the file actually contributes: a block your settings drop
before the merge (§0 directional enforcement, or a `pref:` outside a workset or box file) never
supplies the message, because a cure for a line that was already doing nothing would send you to
fix the wrong thing. One consequence is visible in the cure: the refusal happens before kanibako has settled
which box it is looking at, so the verb's subject is always a `<box>` / `<workset>` placeholder.
Fill it in from the file path the message prints.

The cure is level-appropriate, with your own stored value interpolated so it is copy-pasteable.
It names the verb that matches the file it found the key in, and always carries that verb's
subject:

- `box.agent_name` in a **box** settings file:
  `kanibako box set <box> pref.system.agent=<value>` (or
  `kanibako box set <box> --null pref.system.agent` for a no-agent box)
- `box.agent_name` in a **workset** settings file:
  `kanibako workset set <workset> pref.system.agent=<value>` (or
  `kanibako workset set <workset> --null pref.system.agent`)
- `box.agent_name` in a **system or agent** file: REMOVE it — a request may be written ONLY in
  a workset or box settings file (spec §2h), so this key has no equivalent at that scope. If
  you meant the host-wide default: `kanibako system set system.agent=<value>`. If you
  meant one box, set the request in THAT box's settings file:
  `kanibako box set <box> pref.system.agent=<value>`.
- `system.default_agent` (anywhere): `kanibako system set system.agent=<value>`

**`box.agent` is refused too, and which refusal you get depends on the value's shape.** One
spelling in your file, two different retired keys behind it — kanibako tells them apart by what
the leaf holds, because they were retired for different reasons and the cures point at different
places:

- A **scalar** (`box: {agent: claude}`) is the old agent-*selection* key — `box.crab` renamed to
  `box.agent` in v1.6.0, then to `box.agent_name`. It gets the same message and the same
  `pref.system.agent` cure as `box.agent_name` above, with `box.agent` as the name.
- A **table** (`box: {agent: {model: sonnet}}`) is the settable agent *mirror*, the row in the
  table at the top of this section. You were not naming an agent, you were tweaking one, so the
  message says so and the cure is the `pref.agent.<agent>.<key>` request:

```
'box.agent' is RETIRED and is still set in the box settings file <path> (as `box: agent:`).
The RULE CHANGED in kanibako 1.8.0: a box no longer carries a SETTABLE mirror of its agent's
settings — it REQUESTS a tweak with `pref.agent.<agent>.<key>` (spec §2h) and reads the effective
value back at the read-only `meta.box.agent.<key>` (§2b). Refusing rather than running: an
undeclared key is not read at all, so this box would come up on the agent's UNTWEAKED settings and
every override in this table would silently vanish.
  Fix: kanibako box set <box> pref.agent.<agent>.model=sonnet
  then delete the `box: agent` entry from <path>.
```

The mirror cure is level-appropriate the same way the selection cure is, and it names **every**
leaf your table holds, one request each:

- In a **workset or box** settings file: `kanibako box set <box> pref.agent.<agent>.<key>=<value>`,
  or `kanibako workset set <workset> pref.agent.<agent>.<key>=<value>` from a workset file.
  ⚑ `<agent>` stays a placeholder — this check runs *before* kanibako picks an agent, so
  substitute the agent you mean yourself.
- In a **system or agent** file: REMOVE it — a request may be written only in a workset or box
  file (spec §2h). To tweak an agent everywhere, set it on the agent itself:
  `kanibako agent set <agent> <key>=<value>`.

⚑ **This is the change most likely to surprise you**, because until now a `box: agent:` table was
*inert*: the box launched, the table was silently discarded, and nothing told you your override
was doing nothing. That silence is what was fixed — the box refuses to launch instead. If you have
been running such a box and were happy with it, you were running the agent's untweaked settings all
along; the refusal tells you what you actually asked for and how to ask for it now.

**`auto_approve` gets its own refusal too, and it is a permission key**, which is why it is not
left to the generic §2.47 message: an undeclared key is not read at all, so a box you deliberately
restricted would have come up at the default tier — permissive — with nothing said. The refusal
translates your stored boolean rather than making you look the mapping up (`true` → `full`,
`false` → `restricted`), and the cure matches the file it found the key in:

```
'auto_approve' is RETIRED and is still set in the system settings file <path> (as `agent.claude.auto_approve`).
The RULE CHANGED in kanibako 1.8.0: the permission axis is no longer a boolean — it is the TIER key
`access` (restricted | editing | full, default full). Refusing rather than running: an undeclared key
is not read at all, so this box would come up at the DEFAULT tier and a deliberately restricted box
would silently run permissive.
  Your stored `auto_approve: true` means `access: full` (true → full, false → restricted).
  Fix: kanibako system set access=full
  then delete the `agent.claude.auto_approve` entry from <path>.
```

An unparseable stored value maps to no tier, and the message then names the three you may choose
from rather than guessing one for you. Where the key sits decides the cure, and the VERB is the
tier — `kanibako agent set <agent> access=<tier>` for the agent's own `agent.yaml`,
`kanibako box set <box> pref.agent.<agent>.access=<tier>` in a box file,
`kanibako workset set <workset> pref.agent.<agent>.access=<tier>` in a workset file, and
`kanibako system set access=<tier>` for the system or `/etc` base file. Pasting a `box set` line
at a workset would either hunt for a box named after the key or write to whatever box your cwd
resolves to, so the verb is not interchangeable.

⚑ **Two spellings are answered differently, and one of them is not answered at all.** A request
written as `pref.agent.<agent>.auto_approve` still stops the launch, but as a *request whose target
is not a key* — it names the file and the level without translating your boolean, so set
`pref.agent.<agent>.access` instead. An `agent: <name>: auto_approve:` table in a **workset or box**
file is a different case: a file may not set a containing scope's keys (§0), so that table was never
read at all. It is dropped with a warning rather than refused — if you put one there, it has been
doing nothing since you wrote it, and deleting it changes nothing.

Notes:
- An **empty** leaf (`box: agent_name:` with no value) still counts as the retired key and is
  refused the same way (verified).
- The new on-disk shape of a request is a **nested table** in the box's `box.yaml` or the workset's
  `workset.yaml` (`pref: {system: {agent: <name>}}`) — never a dotted literal; `kanibako box set`
  (or `kanibako workset set <workset>`) writes it for you.
  Suppression ("this box runs no agent") has its own spelling: `kanibako box set --null
  pref.system.agent`. `--null` writes a real YAML `null`; the sibling `reset` VERB
  (`kanibako box reset <box> <key>`) instead *removes* the entry. ⚑ There is no `--reset` flag.
- A stale `box: {agent_name: ""}` row may also sit in `~/.config/kanibako_config.yaml` — old
  versions wrote it into every freshly-initialised host. Nothing ever read it there; it is
  inert and safe to delete for tidiness. It does **not** trigger the refusal (verified).
- **`shared` → `common`:** rename the category token in your settings files, keeping scope,
  agent name, entry name, and value (`shared:` table → `common:`). There is no alias.
- **Underscore key spellings are gone — type the dots.** `kanibako system set box_image=…` used to
  be accepted as a second spelling of `box.image`, and so did the flat form of every other routed
  key (`box_enable_vault`, `system_agent`, `workset_channels_broadcast`, …). It only ever half
  worked: `set` and `reset` took it, `get` answered `Error: unknown config key: box_image` for the
  same string, and the two spellings wrote **different files** — the flat form landed in
  `~/.config/kanibako_config.yaml`, which sits *underneath* the system settings file the dotted
  form writes, so which spelling you typed decided which value won. The flat form is now refused by
  name at every verb, and a successful `set` echoes the dotted key back. **Nothing on disk changes**
  — no release ever stored the underscore spelling, only accepted it as input — so the only thing
  to update is any script or habit that types one.

### 2.2 Mount collisions are now hard errors (a working config can start failing)

Until v1.8.0, two entries landing on one in-box destination were resolved silently (the more
specific scope won). v1.8.0 refuses instead. Your files did not change; **the rule did** —
and the error says so. Two cases flip from silent to fatal:

**Two explicit bindings at one destination** (verbatim template):

```
Two bindings target the same box destination '<box_dest>':
<the colliding keys and their sources, one per line>
A destination may be bound exactly once. Choosing one silently would give you a
read-only mount where the other declaration asked for read-write.

⚑ THIS RULE CHANGED IN kanibako 1.8.0. Until 1.8.0 the more specific scope won, silently — a
configuration that launched before can refuse to launch now. Your files did not change; the
rule did.

To change what occupies a destination you must SUPPRESS the entry you do not
want and then declare the one you do. An override is not enough: these are two
different KEYS, so both survive the cascade. Set the unwanted key to null in the
settings file for its scope (a file may write its own scope and the scopes it
contains):

Either entry may be the one you keep — the block below suppresses '<key>';
use whichever key you do NOT want.

<scope>:
  <group>:
    <name>: null
```

**A `common`/`caches` entry landing on a destination an explicit binding already occupies**
(verbatim template; the explicit binding survives, the derived extension is refused):

```
'<extension key>' extends onto '<box_dest>', which
'<base key>' already binds.
'common', 'caches' and 'seeded' are ABSTRACT declarations: each derives a
bindings.rw entry. The explicit binding is the BASE and survives; the derived
extension is refused.

⚑ THIS RULE CHANGED IN kanibako 1.8.0. Until 1.8.0 a 'common' silently overrode a binding at
the same destination, and a 'caches' silently lost to one — two abstractions, two opposite
silent outcomes. Both are now refused.

<the same suppress-then-add block as above>
```

(When the entry being suppressed is agent-scope, the message adds: *"⚑ In the per-agent
settings file itself the node is spelled 'self.<node>' rather than 'agent.<node>'; the form
above is what a CONTAINING scope's file writes."*)

Practical notes:
- **The cure is a hand-edit of the owning scope's YAML file.** There is deliberately no CLI
  verb for suppression of another scope's key; the message prints the exact YAML.
- A same-scope abstraction-vs-abstraction overlap now **warns** at every launch (not fatal).
- **Default installs cannot hit this** — all twelve shipped configurations were verified
  collision-free byte-for-byte, with standing tests keeping them that way. Exposure is
  limited to `workset share` entries, `set`s you ran at any scope, and hand-written
  settings-file entries that duplicate a destination.
- **Check before you hit it:** `kanibako box show --effective` (new in 1.8.0) resolves the
  real launch pipeline and *reports* any collision in its output instead of raising — run it
  per box after upgrading.

### 2.3 Channel rename: `commons` → `common`

One word now names both the mount category and the channel. Three things move at once:

| what | old | new |
|---|---|---|
| system channel dir (host) | `<channelroot>/commons` | `<channelroot>/common` |
| workset channel dir (host) | `<workset>/channels/commons` | `<workset>/channels/common` |
| in-box path (every agent, every session) | `~/channels/commons` (and `~/channels/workset/commons`) | `~/channels/common` (and `~/channels/workset/common`) |
| settings keys | `system.channels.commons`, `workset.channels.commons` | `…channels.common` |

What you must do, **before your first launch on v1.8.0**:
1. `mv commons common` at the system channel root and in every workset's `channels/` dir.
   (If you launch first, an empty `common/` is guarantee-created beside your populated
   `commons/` — content still on disk, invisible to every box, no error.)
2. If you ever set a `…channels.commons` key (`kanibako workset set <workset>
   workset.channels.commons=<path>`, or `kanibako system set system.channels.commons=<path>`):
   the stored value is now **orphaned** — the launch silently ignores it and reverts to the
   default location. Edit the settings file and rename the nested `channels: commons:` slot to
   `common:`. (Typing the *old key* at the CLI is loud — `Error: unknown config key:
   workset.channels.commons` — but nothing at launch tells you about a stored one.)
3. Fix your own boxes' notes/scripts that reference `~/channels/commons` — they break silently.

The packaged agent guide (now the canon bible) is updated by the upgrade itself.

### 2.4 The canon books — where your instruction files now live

The per-box instructional layout (`~/playbook` + `~/notebook` + `~/workbook`, entered via the
playbook) is replaced by a four-book **canon** under one root, entered at
`~/canon/COLLECTION.md`:

| book | in-box path | contents | writable? |
|---|---|---|---|
| bible | `~/canon/bible/` | packaged core guidance, per-chapter (`general/`, `workset/`, `box/`), plus a per-agent chapter (`bible/agent/`, from your agent plugin) | **no** (bound read-only from the packages) |
| handbook | `~/canon/handbook/` | host-side guidance, assembled from per-scope chapters: `general/` from the system store, `agent/`, `workset/`, `box/` from each scope's own `canon/handbook` contribution | **no** (bound read-only from the host) |
| notebook | `~/canon/notebook/` | box-specific directives/procedures (seeded at create) | yes (box-owned) |
| workbook | `~/canon/workbook/` | box working state (devnotes, plans, tasks…; seeded at create) | yes (box-owned) |

The delivery is a set of **sibling binds onto mountpoints the box home already contains**: the
bible and `COLLECTION.md` come from the base package, the `bible/agent` chapter from your agent
plugin, the handbook chapters from the host (`<data>/global/canon/handbook` for `general`;
`<data>/agents/<agent>/canon/handbook`, `<workset>/canon/handbook`, and the box store's own
`canon/handbook` for the per-scope chapters — each per-scope chapter is skip-if-absent). The
host-side handbook is ordinary user-owned content: edit it freely; that is where host-shared
guidance now lives.

What a v1.7.2 user needs to know:

- **New boxes get a protected skeleton.** `box create` builds a root-owned, mode-555 canon
  skeleton in the box home (chapter mountpoints plus 0-byte import-fallback files, so the
  entry chain resolves even before content is bound). Consequences you should not mistake for
  bugs: an agent cannot `mkdir` under `~/canon` itself; `notebook/` and `workbook/` are
  writable but not deletable (their parent is 555). Deleting a box tree from the host needs
  the escalation kanibako's own verbs (`rm --purge`, `extract`, `move`, `duplicate`) already
  perform. If the root-owning step cannot run on your host, `create` says so loudly —
  *"canon books at <path> are left writable from inside the box (podman unshare chown did not
  succeed). The box works normally…"* (verified) — and the box is fully functional, just
  without the write protection.
- **Existing boxes keep launching** (including on LXC — the launch self-heals missing canon
  mountpoints rather than dying on crun's mkdir limitation), and they receive the new bible +
  `COLLECTION.md` automatically at their next launch after the upgrade, because those are
  package binds. **But** the directive chain now *enters* at `~/canon/COLLECTION.md`, whose
  notebook import points at `~/canon/notebook` — which an existing box never had seeded. Net
  effect: an existing box gains the new bible/handbook and **silently stops loading its own
  `~/playbook`/`~/notebook` directives.** Nothing errors; you will see an
  `unresolved import` warning per launch on stderr for the missing notebook (and one more
  while base/plugin versions are mixed — §2.6).
- **Migrating an existing box is a hand job, deliberately.** The recipe:

  | from (box home, still on disk) | to |
  |---|---|
  | `home/playbook/general/**` | `<data>/global/canon/handbook/general/**` (host) |
  | `home/playbook/agents/default/**` | `<data>/agents/<agent>/canon/handbook/**` (host) |
  | `home/playbook/workset/**` | `<workset_path>/canon/handbook/**` (host) |
  | `home/notebook/**` | `home/canon/notebook/**` (in the box home) |
  | `home/workbook/**` | `home/canon/workbook/**` (in the box home) |
  | `home/playbook/box/**`, `home/playbook/agents/directives/**` | no single destination — triage by hand, see below |
  | `home/playbook/CONTENTS.md` | nothing to carry — the canon index (`~/canon/COLLECTION.md`) supersedes it |

  The first three rows leave the box entirely — a handbook chapter is host content,
  contributed by a scope, not stored in the box. Triaging is yours to do: one box's playbook
  cannot be promoted wholesale into the shared handbook without imposing it on every other
  box. The ruling on record: existing-box migration stays deferred; new boxes only.
- ⚑ **A box older than the three-part handbook keeps everything under `home/playbook/box/`,
  and the mapped rows do not reach it.** Those rows assume a box that already had
  `home/notebook/` and `home/workbook/` split out as their own trees. On an older box you
  will instead find `home/playbook/box/directives/**` holding that box's own directives
  *alongside* its `devnotes.md` and `tasks.md` — real, hand-written content that matches no
  row above and is therefore easy to leave behind. Send the directives to
  `home/canon/notebook/` and the devnotes/tasks to `home/canon/workbook/` — the same two
  destinations the table already gives that content, just reached from a different source
  path. There is deliberately no mechanical rule here: which of a box's directives belong in
  a shared handbook chapter and which are only ever this box's own is a judgement call, and
  only you can make it. An older box spells the agents scope `agents/directives/**` rather
  than `agents/default/**`, and needs the same treatment.
- **The playbook-equivalent tree is now read-only in-box.** An agent that edits its
  `~/playbook` today cannot edit `~/canon/handbook` tomorrow; its own writing goes to the
  notebook and workbook. Do not report this as a regression — edit the handbook host-side.
- ⚑ **A box store now holds TWO different `canon` directories** — do not confuse them:
  `<box_dir>/home/canon/` is the box's assembled guest view (`~/canon`), while
  `<box_dir>/canon/` is the box's *contribution* root whose `handbook/` is one chapter bound
  read-only at `~/canon/handbook/box`. A file placed in the wrong one is shadowed by the
  mounts and never read.
- An old box also keeps a stale `home/playbook/kanibako/**` stub tree on disk. It is not
  necessarily empty — on a box of any age it holds kanibako's own environment guide and
  helper scripts — but every file in it is kanibako-owned and delivered by the running
  release, so there is still nothing to carry forward: harmless residue, and a handy "this
  box predates the canon" marker.

### 2.5 Template and per-agent store moves

**(a) Claude commons — the one that loses visible data if skipped.** The per-agent shared
dirs move one level down, into a `common/` subdir of the agent's store dir:

| | old | new |
|---|---|---|
| claude plugins | `<data>/agents/claude/plugins` | `<data>/agents/claude/common/plugins` |
| claude cache | `<data>/agents/claude/cache` | `<data>/agents/claude/common/cache` |

Do this before your first launch on v1.8.0:

```
mkdir -p <data>/agents/claude/common
mv <data>/agents/claude/plugins <data>/agents/claude/common/plugins
mv <data>/agents/claude/cache   <data>/agents/claude/common/cache
```

If you skip it, the launch guarantee-creates an **empty** `common/plugins` and binds it over
`~/.claude/plugins` — every installed claude plugin appears gone, with no message. The old dir
is still on disk; move it and relaunch. (If those dirs are mount points on your host — e.g.
NFS — re-point the mount instead of `mv`.) Any *user-declared* agent commons entry with a
relative source moves the same way; absolute sources are unaffected. Persona stores also carry
stale top-level symlinks (`agents/<persona>/<leaf>`) that should be swept in the same pass —
they are harmless but will dangle if the old harness dirs are later removed.

**(b) The box template root — verified end-to-end on a simulated upgraded store.**
`system.base_template` is retired; the new key `system.template` names a template **root**,
and the box-home seed lives two levels down (`global/template/box/home/`). What actually
happens when you upgrade a store that still has `global/base_template/` (the sequence is
forced, not optional):

1. Your first `start` / `create` / `agent reauth` hits the setup-compatibility gate: a hard rc 1
   error telling you to run `kanibako setup` (§2.12). Setup is what rebuilds the template
   store, and a setup run that could not rebuild it records no completion (§2.12), so the
   gate keeps erroring: you cannot create a box on the unmigrated store, and the "new box
   seeds empty" hazard is **unreachable without an informed decline**. The one way out is
   the deliberate one — decline the refresh at the interactive prompt, which setup warns you
   leaves the store "out of date … an unblessed state you're choosing knowingly".
2. `kanibako setup` re-creates the NEW tree — `global/template/{box,workset,agent}` and
   `global/canon/handbook` — with **stock packaged content** (reported as
   `Templates refreshed (N added, M updated)`), and records the setup completion.
3. Your old `global/base_template/` is **orphaned but preserved**: untouched on disk, read by
   nothing, and mentioned by nothing — setup does not warn about it. New boxes now seed the
   stock content, **not yours**. That masking is the real exposure: if you customized
   `base_template/`, your customizations silently stop reaching new boxes.

To carry customizations forward, note the packaged payload also **restructured**: the new
`template/box/home/` seeds the canon notebook/workbook skeleton; the old `playbook/`,
`notebook/`, `workbook/` roots are gone from the package. So:
- home dotfiles and files you added → `global/template/box/home/<same relative path>`;
- old `base_template/playbook/**` guidance has **no matching template destination** — that
  content belongs in the host handbook now (`global/canon/handbook/**`, §2.4's recipe).
Then remove `global/base_template/` when you have taken what you want.

If you had *set* `system.base_template` explicitly: the key is gone (typed `set`/`get` refuse
with `unknown config key`, and a stored value stops the resolve — §2.47). Re-point via the new
key, and note it names the **root** (`…/template`), not the box dir.

Agent-level and workset-level template dirs restructure the same way: the seed sources are now
`<data>/agents/<agent>/template/box/home/` and `<workset>/template/box/home/`. `kanibako
setup` self-heals the store **layout** (create-if-absent, never rewriting your files), but any
content you placed at the old flat paths needs the same hand-move as above.

**(c) The box's own handbook chapter has one repoint route: `<scope>.template`.** Beside
`box/home/`, each template root has a `box/canon/handbook/` subtree, and `box create` copies all
three of them — system, then agent, then workset, later overlaying earlier, per file — into the
new box's `canon/handbook` directory, which is then bound read-only into the box at
`~/canon/handbook/box`. Nothing about that changed; what changed is **how you repoint it**:

- **To change what a new box's handbook chapter starts with**, edit
  `<data>/global/template/box/canon/handbook/`, `<data>/agents/<agent>/template/box/canon/handbook/`
  or `<workset>/template/box/canon/handbook/` — or move the whole root by setting
  `system.template`, `agent.<agent>.template` or `workset.template`. These are ordinary settable
  keys and are the only route.
- **To change where the chapter is written and read**, set `box.canon`; the copy and the bind both
  follow it, as before.
- **The copy is no longer a `seeded` entry, so the cascade cannot reroute it.** kanibako used to
  declare three `seeded` entries for the handbook (`system.seeded.handbook`,
  `agent.<agent>.seeded.handbook`, `workset.seeded.handbook`, all writing
  `@box.canon/handbook`); an override of one in a settings file repointed that layer's *source*.
  Those entries are **gone**, and so is that override route. The handbook templates are *host*
  templates — they fill a host directory that a separate read-only bind later delivers — so they
  are copied host-side at create rather than routed through the box's seed category.
- **DELETE any leftover `handbook:` entry under a `seeded:` table in your settings files** —
  do not merely stop relying on it. It no longer names the handbook layer, but neither does
  it go inert: it still parses as an ordinary *user-declared* `seeded` copy, and its
  destination is now read as a **box-side** path instead of a host one. Either way the copy
  is mishandled silently — it is dropped with a `skipping` warning, or, on a host whose own
  user home is `/home/agent` (the same path the box uses), it is written *underneath* the
  box home, where the read-only `~/canon/handbook/box` mount hides it while `create` reports
  success. Delete the entry and repoint `<scope>.template` instead.

This affects nobody upgrading from **v1.7.2**, which had neither those entries nor the box
handbook chapter; it is written down because the `1.8.0rc1` prerelease declared them.

**(d) A persona's store directory is renamed — rename it by hand or the persona starts over.**
A persona node is spelled `navigator+claude` when you type it and `navigator℘claude` inside a
settings key; only the second is legal in a key, because a key path is split on `.` into name
segments and `+` is not one of them. v1.7.2 let that internal spelling reach the disk, so a
persona's store sat at `<data>/agents/navigator℘claude/`. It is the `+` form now, everywhere
kanibako composes it — `agent.yaml`, the per-node `canon` and `template` stores, and the
symlinks sharing the harness's plugins and cache. Do this before your first launch on v1.8.0:

```
mv '<data>/agents/<persona>℘<harness>' '<data>/agents/<persona>+<harness>'
```

If you skip it, nothing reports an error: every store path is create-if-absent, so kanibako
makes a fresh empty directory beside the old one and the persona launches with no settings and
no canon of its own. The old directory is still on disk — move it and relaunch. Keys need no
change: `agent.<node>.*` still canonicalises internally, and both spellings still address the
same store.

### 2.6 The kickoff — upgrade base and plugins TOGETHER

The "kickoff" is the file that boots a box's whole instruction chain
(`~/.config/kanibako/kickoff.md`). In v1.7.2 each agent plugin shipped it. In v1.8.0 the base
package also ships it as a core bind (pointing at the canon), and the base *yields* to a
plugin-supplied kickoff (the yield is keyed on the delivery destination, so the two can never
collide into a launch error).

⚑ **Every kickoff now carries exactly ONE import, `@~/canon/COLLECTION.md`.** Earlier v1.8.0
prereleases shipped plugin kickoffs carrying a second, pre-canon import as a transition aid;
that line is gone, along with the tree it addressed. Its content moved into the canon, which
the one remaining import already reaches — nothing is lost by its removal.

What that costs you, and what to avoid:

- **Base and plugins must move together.** A plugin now needs a base that binds the canon
  (`kanibako-cli` 1.8.0 or newer), and the base needs plugins whose kickoff points at the canon.
  Either half alone leaves a box whose kickoff resolves nothing: **every directive in every box
  silently stops loading**, no error anywhere. The plugins do not pin a base version, so
  `pip install -U kanibako-cli` alone puts you exactly there.
  **Cure: upgrade via the `kanibako` meta package, or upgrade the three agent plugins and the
  base in one step.**
- **No more launch warning.** A mid-transition install used to print one `unresolved import`
  line on stderr at every box launch. With a single import in every kickoff there is nothing
  left to warn about, so if you still see one, something in your chain is addressing content
  that is not there.
- No-agent (plain-shell) boxes: the kickoff file is bound but nothing consumes it yet; no
  action, no breakage.

### 2.7 Workset shares: relative host paths

`kanibako workset share add` documented that *"a relative host_src is resolved under the
working set root"*. That launch-time join is gone: in v1.8.0 the command resolves a relative
path **at write time** and stores it absolute (telling you when it rewrote what you typed).
⚑ A bare-relative source can no longer reach a bind category through a `set` at all — that write
route is gone at every scope for all six bind-shaped categories (§2.20), so the key itself is
refused rather than the source shape. A bare-relative source authored **by hand** into a bind
category in the settings YAML is a defect too, and is now **refused by name** wherever it is
declared (§2.50).

**Already-stored relative sources are NOT rewritten for you, and they now stop the command.** They
used to pass through to podman as you typed them, which never mounted the directory you meant —
§2.50 has the detail and the message you will see. Check every workset's `workset.yaml` for
`workset.bindings.{ro,rw}` entries whose source does not start with `/`, `~`, `$`, or `@`, and
rewrite each to the absolute path it was supposed to resolve to: `<workset root>/<relative>`.

One behavior change on an already-broken shape: `share add` on the **default** workset now
refuses a relative source (it never had a root to join under — the old behavior silently made a
named volume or a CWD-relative path, not a feature).

### 2.8 System-scope config now lives in ONE file

At system scope, mount-category keys (`system.bindings.{ro,rw}.*`, `caches`, `seeded`,
`common`, `synced`) and `secret_path.<VAR>` pointers now read, write, and reset in the **system
settings file** (`<data>/global/settings.yaml`) — the file the launch actually reads. In
v1.7.2 the verbs disagreed: `set` wrote to `~/.config/kanibako_config.yaml`, `get` read the
settings file, and the launch never saw the value.

If you ever worked around that by hand-editing `~/.config/kanibako_config.yaml`: look there
for a `system:` table containing `bindings:`, `caches:`, `seeded:`, `common:`, `synced:`, or
`secret_path:` and move those sub-tables **verbatim** into the `system:` table of
`<data>/global/settings.yaml`. The shape is identical; only the file changes. Confirm with
`kanibako system get <key>` — it now answers. Only the `config.*` bootstrap keys belong in the
config file and must **stay** there.

The eleven `system.*` **path** keys — `system.template`, `system.canon`, `system.backup`,
`system.cache`, `system.runtime`, `system.channelroot` and the five `system.channels.*`
type-roots — are settings keys, and `kanibako system set` accepts them (it used to refuse them as
"structural config keys" and send you to the config file). A set lands in the `system:` table of
`<data>/global/settings.yaml`, and `get`/`reset` read and clear it there. **If you hand-placed any
of these in `~/.config/kanibako_config.yaml`, leave them.** They still apply — that table is the
floor the settings file layers over — but `kanibako system get` reports what the *settings* file
says, so it answers `(not set)` until you set one. To see what is actually in effect, use
`kanibako system show --effective`.

**These repoints now move the directories they name, not just the cascade.** If you set one of
these eleven keys on an earlier 1.8.0 build, check it. The value was stored and the launch honoured
it — binds, seeds and `show --effective` all moved — but kanibako's own path resolver read the
config file only, so anything asking directly for "the template root" or "the channel root" still
got the default. A `system.template` repoint did not move the seed source; a `system.channelroot`
repoint did not move the channel tree. **Nothing is required of you** — the stored value was always
the one you meant, and it now takes effect everywhere. But if you set one of these and worked
around it by *also* moving files by hand or by pointing something else at the old location, that
workaround may now be doing the opposite of what you want: run `kanibako system get <key>`,
confirm it names the directory you actually want, and undo the workaround.

**`system.setup_completed` is settable and resettable now, and it MOVED.** It used to be refused by
every verb with advice to hand-edit `~/.config/kanibako_config.yaml`. It now lives in the `system:`
table of `<data>/global/settings.yaml`, and `kanibako system set system.setup_completed=…`,
`kanibako system get system.setup_completed` and `kanibako system reset system.setup_completed` all
work against it directly. Clearing it is the supported way to get back to *"setup has never run"*.
⚠️ There is no validation on the value, exactly as there was none on the hand-edit: a marker newer
than your installed kanibako makes the next command stop and tell you to upgrade or re-run
`kanibako setup`.

One loud case: `kanibako system set` against a bind-shaped key (`bindings.{ro,rw}.*`, `caches`,
`seeded`, `common`, `synced`) is refused **unconditionally, wherever the entry lives** — not with
a cascade-existence message (no such string — *"cannot create key … it must already exist in the
cascade"* — exists anywhere in the code; a repo-wide search turns up only this line). The real,
current refusal and its cure are §2.20's (*Bind entries are edited in the settings file, not from
the CLI*): the *write verb* for these categories is retired outright, full stop, so moving the
entry to `<data>/global/settings.yaml` does **not** make a subsequent `set` succeed — the fix is
to edit that file directly. A stale entry you *don't* touch is simply inert, exactly as it
already was.

Box and workset scopes are unaffected. Agent-scope binds already routed correctly.

### 2.9 Standalone boxes: reads got truthful

Standalone boxes gain a real box-scope settings file, `<root>/box_data/box.yaml` (absent
until first written); the project-root `workset.yaml` is the workset tier. No data
moves and existing boxes resolve identically, but two read surfaces change on a box whose
`box.*` values sit in the root file (every standalone box created before v1.8.0):

- `kanibako box get box.<key>` prints `(not set)` where it used to print the value — a plain
  `get` reports what is stored *at box scope*; the value still resolves and the launch still
  uses it (`box show --effective` shows it).
- `box show --effective` drops the `(override)` marker on such values (they are now
  workset-tier defaults, not box overrides).

To make a value a genuine box override again: `kanibako box set <root> box.<key>=<value>`
(this now writes the box tier, which wins). Also note: previously-inert `workset.{boxes,
vault_ro,vault_rw,logs}` entries in a standalone root file become **live** and would silently
relocate the box home/vault mounts — remove any you did not mean (§2.11).

### 2.10 Credential residue on de-agented boxes

`create` seeds the resolved agent's credentials into the box home as part of forming the box.
If you later suppress the box to plain-shell (`kanibako box set --null pref.system.agent`),
those credential files **remain in the box home and simply go stale** — a plain-shell launch
runs no credential lifecycle, so nothing refreshes or writes them back. This is accepted,
documented behavior (boxes are trusted-user surfaces; a created box stays fully formed
and inspectable). Manual cleanup if you want the residue gone: remove the agent's credential
files from the box home under its config dir — e.g. `<box_dir>/home/.claude/…`, `…/.codex/…`,
`…/.config/goose/…`.

### 2.11 Housekeeping: renames you almost certainly don't carry

Each of these is expected to find nothing in real stores; listed so a grep of your own files is
quick. **They are four different kinds of thing and they behave differently — each bullet says
which**; only the first is a stale key that can stop the resolve, and only in some files.

- **Bare `agent.<category>.*` keys** (e.g. `agent.common.plugins` with no agent name): an
  internal launch-built form that should never have been persisted. **Not a key — and what that
  costs you depends on which file it is in**, because a top-level `agent:` table is only ever an
  input at two of them (verified for `agent.common.plugins` in each):
  - the system's `<data>/global/settings.yaml` and the machine-wide `/etc/kanibako/settings_base.yaml`
    **stop the resolve, naming the key** (§2.47);
  - a **`box.yaml` or `workset.yaml`** may not set a containing scope's keys (§0), so the whole
    `agent:` table is dropped before the merge — a warning in the launch log, and the resolve
    continues;
  - an agent's own **`agent.yaml`** contributes its `self:` table and nothing else, so a top-level
    `agent:` there is not an input at all and **nothing is printed**.

  Grep for it wherever it might be: only the first case tells you it is there. If a settings file
  carries one, discriminate it: `agent.<agent>.<category>.<rest>`. (`agent.default.*` is
  legitimate — leave it.) A double-prefixed relic like `agent.claude.agent.goose.…` should be
  unwound.
- **`<data>/agents/<agent>/share/` is deleted** (it was only ever a join root and was verified
  empty on inspection). **A DIRECTORY, not a key** — nothing refuses, it is simply gone. If
  yours has content, it belongs to a hand-set relative agent binding — absolutise that binding
  (§2.7's rule); don't just delete the dir.
- **`workset.{boxes,vault_ro,vault_rw,logs}` overrides become live** where they were inert
  (standalone: all four; `workset.{vault_ro,vault_rw}` and `workset.logs`: all modes). **These ARE declared
  keys — do not delete one** on the strength of this section; the change is that a value you
  already set now takes effect. The corresponding mount moves silently, since the new location
  is guarantee-created. A broken `workset.logs` override is visible: the launch logs
  `read-only source <path> does not exist; dropping mount`. Note an override moves the
  **mount** only; kanibako's own internal writes still target the default location, so an
  override is not yet a supported way to relocate a box.
  ⚑ **`workset.{vault_ro,vault_rw}` now also steer the verbs that DELETE.** `box rm --purge`,
  `kanibako clean --purge`, `box move` and `box convert` remove the vault at the resolved
  location, not the old composed one — check the value before you purge a box you set it on.
  Two safeguards limit what that can take. For a primary or named box only the per-box
  `<box-name>` directory under the arm is ever removed, never the arm itself. For a **standalone**
  box the arm *is* the vault, so an arm pointing outside the box's own root is treated as yours:
  it is kept and named on screen (`Kept vault: <path>`), and you remove it yourself. An arm inside
  the root is deleted with the box.
  🛑 **A value that cannot be resolved now stops these commands instead of being ignored.** In
  1.7.2 both keys were accepted and never read, so an unresolvable one — `@config.registry/ro`,
  say — sat in a settings file doing nothing. It is read now, and a purge or move refuses by name
  **before deleting anything** rather than part-removing the box. If `box rm --purge` exits 1
  saying `workset.vault_ro is set to ... which cannot be resolved`, fix or unset the value and run
  it again; nothing was removed.
- **`@meta.runtime.ws_settings`** in any settings file: replace with `@meta.workset.settings`
  (identical resolved value). **This one is a reference INSIDE a value, not a key path**, so the
  closed keyspace never judges it and nothing refuses. It is the quietest of the four: an
  absent referent propagates absence (§6b), so a whole-value `@meta.runtime.ws_settings` makes
  the key holding it disappear from the snapshot, and one embedded in a longer string expands
  to nothing. Grep for it — you will not be told.

### 2.12 The upgrade gate: nothing new appears until `kanibako setup` runs

The v1.8.0 host stores — `global/template/{box,workset,agent}`, `global/canon/handbook`, and
the restructured `agents/<agent>/{template,canon/handbook}` stores — are installed by first-run
init or by `kanibako setup`, **never by `pip install`** (installing a package runs no code),
and the lazy first-run installer never re-fires on an already-initialised host. The designed
trigger for an upgrade is `setup`, and the **setup-compatibility gate forces it**: v1.8.0
raises the setup baseline (`SETUP_BCV`), so the `setup_completed` marker your v1.7.2 config
recorded is too old for the running build and every `start` / `box start` / `create` /
`box create` / `agent reauth` hard-errors (rc 1) with `This kanibako config (1.7.2)
is too old to auto-update. Re-run 'kanibako setup' before agent commands.`

⚑ The separate per-digest **template-staleness gate of the v1.7.x line is retired**. It read
and wrote an undeclared `system.templates_stamp` key — a closed-keyspace violation — and
hard-blocked hosts whose only sin was never having recorded a digest. Its protection folds
into the band above: packaged content that changes with a RELEASE is announced by a
`SETUP_FCV` nudge or a `SETUP_BCV` block. The accepted loss is that template drift *within one
version* (a dev build, or a plugin pip-installed after first run) is no longer detected; the
cure is the same `kanibako setup`. A `[system] templates_stamp` leaf left in your config is
inert — read by nothing, and no error.

**What clears the block.** Only a `setup` run that reached a settled template state: one that
refreshed the store, one that found nothing to do, or an interactive one where you were asked
and knowingly declined. A HEADLESS run with no `--refresh-templates` cannot ask, so it
refreshes nothing, prints `Setup Incomplete`, exits **rc 1**, and records **no** completion —
the block stays up rather than being cleared against a store setup never touched. `kanibako
setup --refresh-templates` is the headless path that both refreshes and completes.

In the window before setup runs, non-gated verbs (e.g. `shell`, config/list commands) still
work; a launch in that window has no `@system.canon/handbook` yet, so the two non-optional
handbook binds are dropped with one warning each and `~/canon/handbook` is empty in-box.
Nothing is lost — run setup and relaunch. Setup reports what it added, what it refreshed in
the shipped staging, and which files it KEPT because your copy differs from the shipped one
(your own files are never overwritten; `--refresh-templates` consents to refreshing *shipped*
files only).

### 2.13 A symlink in a template directory now fails loudly

The template/seed copier now refuses, rather than follows, a symlink on either side of a copy
(spec §2a: a template layer must not reach outside its subtree — a symlinked source could
exfiltrate e.g. an SSH key into a box home). If your `global/template/box/home/`,
`agents/<agent>/template/box/home/`, or `<workset>/template/box/` symlinks a config file into
a dotfiles repo — a perfectly ordinary pattern before this rule — **`box create` /
`kanibako setup` now fails loudly**, naming the offending path (`TemplateScopeError`). Nothing
is lost; the operation is just interrupted. Check with `find <template_root> -type l`. Cure:
replace the symlink with a real file (`cp --dereference`), or deliver the content through a
`bindings.ro`/`bindings.rw` key instead — a bind is the right mechanism for "keep this live".

### 2.14 Fixed: two `--null` CLI bugs

- **A flag now works wherever you type it.** `kanibako box set <box> --null <key>` failed with
  `unrecognized arguments: <key>` — a flag written *between* two positionals stranded
  everything after it, so `--null` (and `--force`, `--box`, `--agent`) only worked before
  them. Every subcommand now accepts its flags in any position; a `--` still ends flag
  parsing.
- **`kanibako agent set --null <key>` performed a silent read** — it printed the current value
  and exited 0 without writing anything. It is now an explicit refusal naming both cures:
  `agent reset <agent> <key>` to clear the agent's own value, or `--null
  pref.agent.<agent>.<key>` from a box or workset to suppress what the agent declares.
  (Suppression at agent scope is not supported: the per-agent settings file is read back with
  every value coerced to a string, so a null there would return as the text `None`.)

### 2.15 Personas: the store is read live, and stray values in the agent file now win

If you use persona agents (`<persona>+<harness>`, e.g. `navigator+codex`), the way their endpoint,
model and bearer token reach a box has changed shape.

- **Before:** every start reparsed the persona-grata store, verified the values with a probe, and
  on PASS wrote endpoint, model and the token pointer into `agents/<node>/settings.yaml`. The
  launch then resolved that file. On a FAIL it kept the previously-written values and launched.
- **Now:** the store is read fresh on every launch and resolved directly, as a cascade level below
  the agent settings file and above the harness defaults. **Nothing is written.** A launch leaves
  `agents/<node>/agent.yaml` byte-identical, and `kanibako create` no longer imports anything.

**What you must do: delete persona values you did not write yourself.** This is the one action this
change requires, and nothing warns you about it.

The agent settings file outranks the live store, so any `endpoint`, `model` or `secret_path.<VAR>`
that the old sync wrote into `agents/<node>/agent.yaml` (kanibako ≤ `v1.8.0-rc1`) keeps
overriding the store — you edit the persona's `settings.json` and nothing changes, silently. Remove
those keys. A value that MATCHES the store is always safe to delete (the live tier supplies it). A
value that DIFFERS is now, by definition, a deliberate user override: keep it if you meant it.

**Behaviour changes to expect:**

- **A broken store config now blocks the launch** instead of silently running on stale values. The
  error names the cause — malformed JSON, no endpoint, an unusable token pointer. There is no
  last-known-good to fall back on, because nothing is kept.
- **A token the endpoint positively rejects (401/403) is a hard error.** A persona `start` that
  LAUNCHES a box probes the endpoint with a minimal request first, and it applies to
  keyspace-configured personas with no store entry at all — not only store personas. An UNREACHABLE
  endpoint is *not* an error: it warns and proceeds. `kanibako create` only ever warns, so a fixable
  credential never blocks a create. ⚑ A `start` that merely **reattaches to an already-running box
  does NOT probe** (§2.17): its agent is already running and authenticated, so there is nothing for
  the probe to protect — and a probe there could refuse to reconnect you to a working box.
- ⚑ **NEW: a persona's whole `env` block is now delivered into the box.** Previously kanibako read
  exactly three values out of a persona's `settings.json` (endpoint, model, token var) and ignored
  the rest of its `env`. Now every string-valued entry in that block is exported inside the box,
  minus `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`, which ride their own channels. **If you
  put a variable in a persona's `settings.json` for the benefit of the host harness, it now also
  reaches the container.** Review those blocks before upgrading. (Claude personas only — the codex
  reader carries no `env`.)
- Deleting a value from the store now simply stops it resolving on the next launch.

### 2.16 A generated agent settings file no longer carries a model default

`kanibako setup` and first use used to seed a new agent settings file with the plugin's own default
(claude wrote `model: opus`, codex wrote `model: gpt-5.5`). A stored default outranks the defaults
floor, so every install seeded that way stayed pinned to the value current when it was created, even
after the plugin's default moved on. New agent settings files no longer carry it.

⚑ **This is not persona-only** — it applies to bare agents too. **`kanibako agent get <agent> model`
on a fresh install now reports `(not set)` where 1.7.2 reported `opus`.** Nothing about resolution
changed; `agent get` reads the file and the file no longer states a value the floor already
supplies.

**Existing files are not touched.** A `model: opus` line left in place keeps working and keeps
pinning; deleting it changes nothing today and un-pins you from future default evolution. A
hand-typed pin is indistinguishable from the seed, so if you meant it, leave it.

⚑ **One real behaviour change, codex personas only.** The launch resolve deliberately drops the
harness `model` default so that an unset persona model surfaces an actionable error rather than
shipping an own-endpoint default into a third-party provider block. On a first launch there is no
settings file, so the seed had been re-supplying exactly the value that exclusion dropped. A fresh
codex-persona box whose store config names no model now refuses at the pre-flight instead of
silently running against `gpt-5.5`. Set `agent.<node>.model`, or name a model in the store config.

⚑ **A missing model is not, by itself, invalid.** Some endpoints do not require one — a provider may
serve a single model or apply its own default. Whether a model is required is declared per harness;
codex declares that it is, because an omitted key there means "use codex's own moving
recommendation", not "no model".

---

### 2.17 Reattaching to a running box: flags are now refused instead of ignored

Starting a box that is **already running** reattaches you to it. Previously that reattach still ran
the whole launch preamble — it could resolve, **build or pull an image**, spawn a throwaway
container to probe the launch baseline, and make a network call to verify a persona endpoint —
and then attach to the running session, which none of that work could affect. Worst case, a persona
whose token had since been revoked got a **hard error and no reattach**, locking you out of a box
whose agent was up and working. v1.8.0 skips all of it: a reattach now does only what reattaching
needs (refresh credentials, print the config notice, attach).

**What you must do:** nothing, unless you pass flags to a box that is already up.

**The behaviour change to expect:** flags that a running container cannot adopt are now **refused
by name, with a nonzero exit**, where most of them were previously accepted and silently dropped.

| Flag | Previously | Now |
|---|---|---|
| `--rig`/`--image`, `--browser`, `--share-images`, `--no-helpers`, `--no-auto-auth` | silently ignored (`--image` was even recorded, then ignored) | error |
| `-e`/`--env` | silently ignored | error — *unless* it reaches a second process in the box, which does apply it: `--entrypoint`, or `kanibako shell --persistent` at a box that is running an agent. ⚑ **Known defect:** paired with `--detach` it is still accepted and still dropped (see below) |
| `-N`, `-C`, `-R`, `-M`, `-A`, `-S` | **silently ignored** — `kanibako start -N <running box>` reattached to the OLD conversation | error |
| `--persistent`, `--ephemeral` (typed explicitly) | reattached / hit a generic error | error, leaving the running session untouched |
| `--entrypoint` | silently ignored; you got the agent session instead | **runs the command as a second process in the box** |
| `--attach`, `--detach`, `--print-container`, `--warm-only` | honoured | unchanged |

⚑ **If you script `kanibako start` with flags, check whether the box may already be running.** A
script that passed `-N` (or `--rig`) to a live box was silently getting something other than what it
asked for; it now gets a clear failure instead.

⚑⚑ **`--detach` and `--warm-only` refuse the per-run flags too.** At a box that is already running,
a detached invocation runs nothing — the box is already up, so kanibako says so and exits. A
`--entrypoint` or `-e` passed alongside it is therefore **refused by name**, where 1.7.2 accepted
both and silently dropped them at rc 0. If you have a script doing
`kanibako start --detach --entrypoint <cmd> -e VAR=x <box>` and believing the command ran, it never
did; drop `--detach` to actually run it as a second process in the box.

**The cure, and the new flag:** `kanibako --restart [box]` stops the box and starts it again with
your flags in force. It is the one thing that bypasses these refusals — passing it *is* the
statement "I know this needs a fresh container". `kanibako stop` followed by `kanibako start` does
the same thing by hand.

```console
$ kanibako start -N mybox
Error: Box 'mybox' is already running, so -N/--new cannot be applied to it (a running
box keeps the container and the agent session it was launched with).
  Restart it: kanibako --restart mybox
  Or stop it: kanibako stop mybox

$ kanibako --restart mybox      # stop, then start fresh with -N in force
```

---

### 2.18 A launch never rebuilds anything

Two places used to materialise something rather than tell you it was missing. Both now refuse.

**A registered box whose directory is gone.** A box's registration and its box directory are
separate things: `box rm` without `--purge` drops one, and deleting `<data>/…/boxes/<name>` (or
losing the volume it lived on) drops the other. With the registration intact and the directory
gone, `kanibako start` used to silently re-create the directory and re-seed the home, printing
nothing about it. That is a repair, not a launch — and a repair has to be asked for by name — so
the launch now errors before touching the filesystem:

```console
$ kanibako start
Error: box 'myproj' is registered, but its box directory is gone
(/home/you/.local/share/kanibako/primary_workset/boxes/myproj).
  A launch will not rebuild it — rebuilding a box is a repair, and a repair has to be
  asked for by name.
  Rebuild it:  kanibako create /home/you/myproj
```

**What you must do:** nothing up front. If you hit the error, run the `Rebuild it:` line — it
rebuilds the box in place and keeps its registration. For a **workset member** the rebuild is
`kanibako workset disconnect <workset> <box> && kanibako workset connect <workset> <workspace>`
(`create` refuses inside a workset member with "project already initialized"); the message prints
the right one for the box you are launching.

⚑ **Check scripts and cleanup jobs that delete box directories** and rely on the next `start` to
put them back — they now need the explicit rebuild command. A dedicated `repair` verb is planned;
when it lands it replaces the `Rebuild it:` line and nothing else about this error changes.

**Unaffected**, because they materialise a box legitimately: `kanibako create`, `kanibako box extract`,
and the **first launch of a box added with `workset connect`** — connect registers the box and
creates its directory but deliberately never seeds it, so that first `start` is the box's real
materialisation and still works exactly as before.

**A box-config verb with no box.** `kanibako box set box.<key>=<value>` with no box named, run
from a directory that is not a box, used to write `boxes/__unregistered__/settings.yaml` and exit
**0** — a settings file for a box that does not exist, which no launch ever reads. The same held
for `box get`, `box show` and `box reset`. All four now error:

```console
$ cd ~/somewhere-that-is-not-a-box
$ kanibako box set box.image=myimage:1
Error: no box at /home/you/somewhere-that-is-not-a-box — kanibako has no box registered
for this directory, and a setting has to belong to a box.
  Name the box:   kanibako box set <box> <key>=<value>
  Or make one:    kanibako create
```

**What you must do:** if you have a stray `<data>/<workset>/boxes/__unregistered__/` directory
from before the upgrade, delete it — nothing reads it. Any values you meant to set are still
unset; re-run the command with the box named.

### 2.19 The `env` family: the `env` FILES are gone and `env.<VAR>` is refused

Three things moved together, and they had to — any two without the third leaves the env family
unusable.

**1. The three `env` FILES are no longer read at all.** v1.7.2 layered `<data>/env`, the
workset's `env` and the box's `env` into every container environment. v1.8.0 does not read
them: the whole reader is deleted. Nothing about the files changed — they simply reach nothing.

⚑ **This is the one that can bite you, and kanibako does not warn you about it.** v1.7.2 wrote
`COLORTERM=truecolor` into `<data>/env` on first run, so **essentially every pre-existing install
has such a file**, and anything you ever added to one with `kanibako <noun> set env.FOO=…` reached
your box yesterday and does not today. **Nothing is printed when a stale file is found** — the
files simply reach nothing, silently. Check the three locations yourself:

```
<data>/env                    e.g. /home/you/.local/share/kanibako/env
<workset-root>/env
<box-metadata>/env
```

Move any values you still want with `kanibako system set system.env.<VAR>=<value>` — or the
`workset`/`box` noun for the other two tiers — and delete the files. Nothing reads them, so
leaving them in place is harmless; they are simply dead.

⚑ **The `COLORTERM` line in that file is *ours*, not yours — DELETE it, do not migrate it.**
`COLORTERM=truecolor` is a declared default in v1.8.0 (§2.42): it reaches every box with no key
stored anywhere, so moving it to `system.env.COLORTERM` the way the other lines migrate would create
a *second* declaration of a variable kanibako already declares at box scope, and that refuses the
launch (§2.33). Every other line in the file migrates exactly as described.

**2. The bare `env.<VAR>` spelling is RETIRED and refuses by name.** It was an undiscriminated
variant that meant something *different* from the key of the same name: it wrote a file, not a
setting. `kanibako box set <box> env.EDITOR=vim` now errors with the cure:

```
Error: 'env.EDITOR' cannot be set — the bare env.<VAR> spelling is RETIRED (the env family is
scoped, spec §2a). Use 'box.env.EDITOR', which is stored in the box settings file and exported
into the box at launch. The docker .env files the bare spelling wrote are no longer read at all.
```

**3. `<scope>.env.<VAR>` is now reachable from the config verbs** — `box.env.FOO`,
`workset.env.FOO`, `system.env.FOO`, at `set`, `get` and `reset`. The key was already declared
and its launch-side delivery already worked; only the verbs were missing, so it used to error as
an unknown key. The per-agent form is `kanibako agent set <agent> env.FOO=bar` (the agent noun
takes the tail under an already-named agent, and is not affected by the refusal above).

**What you must do.** For each of the three files, move every `VAR=value` line to the matching
key and delete the file — **except the `COLORTERM` line kanibako seeded itself, which is deleted and
not moved** (see above, and §2.42):

```console
$ kanibako system set system.env.<VAR>=<value>            # <data>/env
$ kanibako workset set <workset> workset.env.<VAR>=<value>  # <workset>/env
$ kanibako box set <path> box.env.<VAR>=<value>             # <box>/env
```

⚑ **A `$VAR` in an env VALUE is refused at set time.** These values go through kanibako's
expansion grammar, which knows only `$AGENT`, `$WORKSET` and `$XDG_*` — and a `set` at the CLI
has no live agent or workset, so in practice only `$XDG_*` resolves. A shell variable your `env`
file carried happily is now an error, and the message names it:

```console
$ kanibako box set <box> box.env.MY_PATH='$HOME/bin'
Error: 'box.env.MY_PATH': Unknown variable: $HOME
$ kanibako box set <box> box.env.MY_PATH='$AGENT/bin'
Error: 'box.env.MY_PATH': Variable $AGENT is not set in this context.
```

**The cure is to escape the `$`**: `\$` stores the backslash form in the settings file and
kanibako unescapes it to the plain literal `$HOME/bin`, which becomes the variable's value
verbatim — kanibako never substitutes it.

```console
$ kanibako box set <box> box.env.MY_PATH='\$HOME/bin'
Set box.env.MY_PATH=\$HOME/bin
```

An `@`-reference is validated the same way (`box.env.X=@meta.nope.key/x` is refused as a
dangling reference), and a lone unescaped `$` is refused as a malformed value.

---

### 2.20 Bind entries are edited in the settings file, not from the CLI

**What changed.** Two CLI routes are retired:

```
kanibako box set     box.bindings.rw.home=/newhome                # was: "Set … host source to …"
kanibako system set  agent.claude.bindings.ro.launcher=/newsrc    # was: exit 0
```

Both now refuse, naming the key and pointing at the settings file. The matching `reset`
(`kanibako box reset`, `kanibako system reset`) refuses the same keys symmetrically — a reset is
a write.

**What did NOT change, and this is the part worth reading.** The keys are **not** retired:

- they are still declared keys;
- they are still read by the launch cascade, so **every binding you already have keeps mounting**;
- they are still authored by hand in the settings YAML;
- **`box get`, `workset get` and `system get` all still read them at their OWN scope**, naming
  the subject (`kanibako box get <box> box.bindings.ro`, or `kanibako system get
  system.bindings.ro`). ⚑ The `system` noun was the odd one out for most of the 1.8.0 cycle — it
  answered `Error: unknown config key: system.bindings.ro` for a key it does in fact store — and
  it reads its own scope like the other two nouns now. ⚑ **Ask a noun for another scope's
  category key and it is refused** (`kanibako system get box.caches`): the noun already names
  the scope, so there is nothing for a cross-scope read to mean. The refusal names the key and
  the noun that does read it. ⚑ This is still not a complete read surface — see the
  known-limitation note in §2.23 before relying on it.

Only the *write verb* is gone.

⚑ **One exception has appeared since, and it is the example above.** A binding at the box home
(`~`) does *not* keep mounting — home stopped being a binding at all, and an entry there now refuses
the launch. See §2.32; everything in this entry holds for every other destination.

**Why there is no replacement command.** A `bindings.{ro,rw}` arm **is** a single key whose
value is a map keyed by the mount **destination**, and the destinations inside that map are values,
not key segments. So there is no per-entry key for `set` to name — not a route that moved, a route
that no longer has anything to address. Rather than invent a spelling that would have to be retired
again, the refusal names the real surface: the file. §2.23 covers the stored shape itself.

**The cure.** Edit the settings file for the scope you want, and re-launch the box. For a box-scope
bind that is the box's own settings file; for an agent-node bind it is
`agents/<node>/agent.yaml`. For a box- or workset-scope bind you can read the current value
first with `kanibako box get <box> <key>` — naming the subject, which is required. An agent-node
bind reads back on its own noun, with the node as the subject and the rest as the key:
`kanibako agent get <node> "bindings.ro.~/.ro/x"`. ⚑ The `system` noun reads an *entry* but
refuses the whole-category key; see §2.23.

**⚑ This now covers EVERY bind-shaped category, not just the two arms.** `caches`, `seeded`,
`common` and `synced` have lost their CLI `set` route as well — including the source-only
repoint, which used to let you change an entry's host source without touching its destination.
All six bind-shaped categories are **YAML-only**.

```
kanibako box set  box.caches.sock=/new/sock          # was: repointed the host source
kanibako box set  agent.claude.common.plugins=/new   # was: repointed the host source
```

Both now refuse, naming the key and pointing at the settings file, exactly as the two arms do.
The matching `reset` refuses them symmetrically.

**The test for whether a script of yours is affected** is therefore *not* "does the key contain
`bindings.ro`/`bindings.rw`" any more — it is **"is the key bind-shaped at all"**, i.e. does it name
`bindings.ro`, `bindings.rw`, `caches`, `seeded`, `common` or `synced`. If it does, and the script
*writes* it, the write now refuses.

**What is genuinely unaffected, for all six.** The categories are still declared, still read by the
launch cascade so every entry keeps being delivered, still authored by hand in the settings YAML,
and **readable at the box and workset nouns** — but ⚑ read them at the CATEGORY key now
(`kanibako box get <box> box.caches`), which returns the whole map. The per-entry spelling
`box.caches.<destination>` is no longer a *key* — it has no `set` — but `get` still reads that far
into the value, so an entry you have just hand-edited can be checked one at a time; §2.23 shows the
file shape and records where that read stops.

**Why the repoint went too.** The same reason the arms lost theirs: these categories **are** single
keys whose value is a map keyed by the mount **destination**, so there is no per-entry key left for
`set` to name. Keeping a write route for four categories while the other two refused would have
meant two rules for one shape.

---

### 2.21 `workset share`: the destination identifies a share, not a name

**What changed.**

```
# before                                    # now
workset share add WS NAME host:guest        workset share add WS host:guest
workset share rm  WS NAME                   workset share rm  WS DEST
```

`rm` takes the **box destination**, typed exactly as `share list` prints it. The raw listing's
columns change from `NAME / MODE / BIND(host:dest)` to **`DEST / MODE / SOURCE`**, and the messages
follow it: `Added rw share 'data'` → `Added rw share at '/home/agent/data'`, and the error
`no share 'x'` → `no share at 'x'`. `share list --effective` still prints `source -> dest [mode]` for
every share a box actually receives; a share that a `masks` entry or another binding swallows now
prints as `dest [mode] (declared: source)` with the reason it produces no mount indented beneath. If
the working set's declarations collide, `--effective` refuses instead of listing — one line saying it
cannot answer and why, then the refusal a launch gives, at rc 1.

**⚑ ACTION REQUIRED IF YOU HAVE EXISTING WORKSET SHARES — re-add them.** The stored shape changed
too, and at launch an old entry is **not** rejected: it is misread. A share written by an earlier
version looks like

```yaml
workset:
  bindings:
    rw:
      mydata: [/host/data, /home/agent/data]   # name -> [source, destination]
```

and now parses as **destination `mydata`** with `/home/agent/data` read as **mount options**, because
the destination is the key and a two-element value means `[source, options]`. Both shapes are legally
two elements, so nothing can tell them apart.

Nothing gets mounted somewhere unexpected: a path is not a valid mount option, so the launch fails
at the container runtime instead. But it fails *without naming this as the cause*, which is why the
entry has to be rewritten rather than left to be discovered.

**The fix takes one command per share** — re-add it, which rewrites the entry in the new shape:

```
kanibako workset share list <WS>                 # see what you have
kanibako workset share add  <WS> /host/data:/home/agent/data --mode rw
```

Or edit the workset settings file by hand: key on the destination and drop it from the value —
`{/home/agent/data: [/host/data]}`. **`share list` will not print an old entry at all** — it refuses
the file and names the offending entry, because a share whose key is a bare name has no honest
destination to show in the `DEST` column. That refusal is how you find them.

**Why the name went.** It never identified anything. Two shares at one destination were already an
error, and that error was always decided on the **destination** — never on the name. So the name was
a label that could not change any outcome, while making it possible to write two entries that looked
distinct and were not. Making the destination the identity turns a rule that was enforced by hand
into one the data structure enforces by construction.

**Re-adding is how you repoint.** `share add` at a destination that already has a share replaces its
source, and says `Updated` rather than `Added`. There is no separate edit verb.

**One thing that did NOT change, so you are not surprised by it.** A destination is unique *within
one mode*. Adding the same destination `--mode rw` and then `--mode ro` still puts one destination in
both arms, which is a launch-time collision — the same outcome as before, when it took two different
names to produce it. The share help text used to claim two shares could never target one
destination; that claim was wrong before this change and has been corrected.

### 2.22 New fixed box directory `~/.kanibako/`; the helper socket and log moved into it

**What changed.** Inside a box, the helper hub socket and the helper message log are now mounted at
fixed paths under a new directory, and no longer follow that box's `$XDG_STATE_HOME`:

| Inside the box | v1.7.x | v1.8.0 |
|---|---|---|
| helper socket | `$XDG_STATE_HOME/kanibako/helper.sock` | `~/.kanibako/state/helper.sock` |
| helper message log | `$XDG_STATE_HOME/kanibako/helpers.jsonl` | `~/.kanibako/state/helpers.jsonl` |

`~` is the box's home, so in practice `/home/agent/.kanibako/state/`. Both filenames are unchanged,
and the **host-side** log path is untouched.

**You are still served at the XDG location.** Once the box is up, kanibako points
`$XDG_STATE_HOME/kanibako` at `~/.kanibako/state` with a symlink, so anything that resolves the XDG
way finds the same files. With no `XDG_STATE_HOME` set that means `~/.local/state/kanibako` — the
exact path v1.7.x used — keeps working. The projection is made **after** the box is live, which is
the only time the box's own XDG settings can actually be read.

**⚑ The one case that needs a hand: a box created before v1.8.0.** Such a box already has a real
directory at `~/.local/state/kanibako` (it was a mount destination), and kanibako will **not** delete
a directory you own. It leaves it alone and logs a warning, so the symlink is not created and that
old path keeps showing stale, empty files. Nothing breaks — `kanibako helper` and `kanibako fork`
read `~/.kanibako/state` directly — but to get the XDG path served again, remove the stale directory
inside the box once and relaunch:

```
rm -rf ~/.local/state/kanibako     # inside the box; it holds nothing but dead mountpoints
```

**Who else this affects: a box that sets `XDG_STATE_HOME` to a non-default absolute path.** The
mount itself no longer follows that setting — but the post-boot symlink does, so a reader using
`$XDG_STATE_HOME/kanibako/helper.sock` still lands on the socket. Only something that ran *before*
the box finished starting would see the difference.

**Why.** A mount destination is written into the container runtime's arguments before the box
exists, and a `seeded` copy runs at `create` with no container at all — so a destination containing
`$XDG_STATE_HOME` had to be resolved by the **host**, guessing what the box would say. That guess
was maintained by hand in four places, and they were already out of step: kanibako derived the mount
destination from `$XDG_STATE_HOME` while hardcoding the matching directory creation at
`~/.local/state`, so a box that set the variable got its directory made in one place and its socket
mounted in another. `~/.kanibako/` is a fixed location that resolves identically on both sides,
always — and it is the general answer for anything else in this class, so the same fix does not have
to be re-invented per feature. XDG compliance comes back afterwards, properly, from inside the live
box.

---

### 2.23 Bind entries are keyed by DESTINATION; entry names are gone

**What changed.** Every bind-shaped category — `bindings.ro`, `bindings.rw`, `caches`, `seeded`,
`common`, `synced` — is now a **single key** whose value is a map from the box **destination** to
`[host_src]`. The entry NAME no longer exists anywhere.

```yaml
# v1.7.x — name-keyed: the name is the key, the destination is inside the value
box:
  caches:
    npm:  ["/host/npm-cache", "~/.npm"]
    pip:  ["/host/pip-cache", "~/.cache/pip", "Z,U"]

# v1.8.0 — dest-keyed: the destination IS the key
box:
  caches:
    "~/.npm":       ["/host/npm-cache"]
    "~/.cache/pip": ["/host/pip-cache", "Z,U"]
```

The value's optional second element is still the mount options, exactly as before.

**⚑ You must edit your settings files. There is no shim.** v1.8.0 is a deliberate clean break, so
kanibako does not read the old shape and does not rewrite it for you. A file still in the old shape
is **refused loudly**, naming the entry and the category — it is not silently ignored, and it does
not half-load.

**Where to look.** Any settings YAML you have hand-written: the system settings file, a workset's,
a box's, and `agents/<node>/agent.yaml`. Check for a `caches:`, `seeded:`, `common:` or `synced:`
table whose sub-keys are names rather than paths. (`bindings.ro` / `bindings.rw` already moved to
this shape earlier in v1.8.0 — see §2.20 and §2.21.)

**Two entries that shared a destination cannot both survive.** The destination is now the identity,
so a category cannot hold two entries at one path. If you had two names pointing at the same
destination, keep the one you meant; kanibako refuses the pair rather than silently dropping one.
(*Different* categories, or different scopes, at one destination are unaffected — those are
different keys, and the collision table in §2.2 decides between them exactly as before.)

**Reading a category.** `get` reads the CATEGORY key and returns the whole map — but you
must **name the subject box or workset**. Given a single positional argument, kanibako reads
`box.caches` as a *project name*, not as a key:

```
kanibako box get <box> box.caches                     # the map
kanibako workset get <workset> workset.caches         # the map
kanibako box get <box> "box.caches.~/.cache/uv"       # one entry, by its destination
kanibako agent get <node> caches                      # the agent node's own map
kanibako agent get <node> "bindings.ro.~/.ro/x"       # one bindings entry
```

Quote a destination at the shell: most of them contain `~` and `/`. ⚑ On the **`agent`** noun the
key is spelled as a **tail** — the node is the subject, so `agent get claude caches`, never `agent
get claude agent.claude.caches`, which repeats the node inside the key and refuses.

This closes a gap: `box.bindings.ro`, `box.bindings.rw` and `box.masks` previously read back
`(not set)` even when set, because nothing claimed the bare key.

**⚠️ Known limitation — three gaps in that read surface,** all real at v1.8.0:

- **A category key is readable only at its OWN noun.** `kanibako system get system.caches` works;
  `kanibako system get box.caches` is refused and points you at `kanibako box get <box>
  box.caches`. The reason is that these tables are merged *entry by entry* across tiers, so any one
  noun holds a fragment rather than the value — asking the `system` noun for `box.caches` would
  print a partial map no box ever sees. There is no one place to see every scope's category tables
  side by side; for that, use `kanibako box show <box> --effective`, which resolves the whole
  cascade for a real box.

- **A *per-agent* category key is not readable at a file-scope noun.** `kanibako system get
  agent.<agent>.caches` — and the other six categories under `agent.<agent>` — is refused, because
  the table lives in `agents/<agent>/agent.yaml` and the file-scope nouns do not open that file.
  The refusal points at `kanibako agent get <agent> caches`, which does read it, so the surface
  exists; it is just at a different noun. (The any-agent tier is not affected: everything under
  `agent.default.` — the seven `<category>` tables *and* the behaviour leaves, `agent.default.model`
  and the rest — is stored in the system settings file and reads at `system get` like any other
  system key. There is no `agents/default/agent.yaml`, so `kanibako agent get default …` is not the
  spelling for it.)
- **`masks` has no per-destination read anywhere.** `kanibako box get <box> "box.masks.~/.m"` is
  refused by name — a mask's value is a marker rather than a source, and nothing claims the
  per-entry slot. Read the whole `masks` map instead.

⚑ The per-destination read of the other five categories is a read *into* one key's value, not a
key of its own — which is why there is no matching `set`, and why the `agent` noun refuses
`caches.<destination>` **by name** (only its `bindings` arms answer a per-entry read). That is a
refusal, not a silent miss: it tells you the entry lives in the file.

**So how do you check the edit you just made?** Use the read above to confirm the YAML parsed into
the shape you meant — it *echoes* the stored map and does not validate it — and then run `kanibako
box show <box> --effective` on a box the edit applies to. That resolves the real launch snapshot,
so a malformed entry in the box, workset **or** system tier is named there. An agent node's file
echoes back the same way (`kanibako agent get <node> caches`), but only the echo half:
`kanibako agent show <node> --effective` does not report category entries at all, so for the
validating half rely on the launch to refuse a bad entry.

**Seed and sync destinations moved to guest spelling.** The three template seed layers
(`system.seeded` / `agent.<agent>.seeded` / `workset.seeded`) target `~/` instead of a host path
under the box store, and kanibako resolves that to the box store when the copy runs.
**Nothing about where your files land changes.** If you had declared a `seeded` or `synced` entry
with an absolute *host* destination, respell it as the guest path you actually want written.

**⚑ `seeded` and `synced` are still COPIES.** They share a way of writing an entry down with
`bindings`; that says nothing about what is done with it. A seed still copies once at `create` and
never clobbers existing content, and a `synced` entry is still re-copied per launch behind its mtime
gate.

**Why.** A destination can be bound exactly once, so it is the thing that actually identifies an
entry — while a name was free-floating: two names could claim one destination with nothing able to
tell which was meant, and renaming an entry silently created a second one. Keying by destination
makes the ambiguity **unrepresentable** rather than merely detected, and it makes one shape serve
all six categories instead of two shapes that were 2-element-legal with opposite meanings.

---

### 2.24 `masks` is a map keyed by destination; a list is refused

**What changed.** `<scope>.masks` names the box paths to hide behind a tmpfs. In v1.7.x it was
written as a **list**; it is now a **map keyed by the box destination**, like every other
destination-keyed category (§2.23) — but its value is not a source, because a mask has none. The
value is a three-state marker.

```yaml
# v1.7.x — a list of destinations
box:
  masks:
    - "~/secret"
    - "~/workspace/private"

# v1.8.0 — keyed by destination
box:
  masks:
    "~/secret":            true
    "~/workspace/private": true
```

**⚑ Edit your settings files.** Until now a list was **silently dropped**: no tmpfs was mounted, no
warning was printed, and the path you meant to hide stayed readable inside the box. It is now
refused by name, so a leftover list stops the launch and tells you what to write instead. If you
carried a `masks:` list forward from v1.7.x, **it has not been masking anything** — check what that
path exposed before you assume the mask was in force.

**The three states.** `true` (or a bare key with no value) masks the path. `null` **unmasks** it —
it removes a mask inherited from a wider scope, which a list had no way to express. Omitting the key
inherits whatever the wider scope said.

```yaml
box:
  masks:
    "~/shared-secret": null   # the workset masks this; this box does not
```

**Where to look.** Any settings YAML you hand-wrote: the system settings file, a workset's, a box's,
and `agents/<node>/agent.yaml`. Check for a `masks:` table written with `-` bullets.

**Why.** Keying by destination is what makes the containment rules in §2.2 decidable at all — a mask
and a bind at one path, or a mask inside another mask, are questions about destinations. It also
makes the per-entry cascade real: a box can now override *one* inherited mask instead of replacing a
whole list, and unmasking becomes expressible rather than impossible.

---

### 2.25 A mask now hides its path instead of making it read-only

**What changed.** A `masks` entry mounts a tmpfs over the box path. That tmpfs was created with
podman's default `tmpcopyup`, which **copies whatever already sits at the destination up into the
new tmpfs** — so everything under a masked path stayed plainly visible inside the box, merely
read-only. The tmpfs is now mounted `notmpcopyup` and the masked path shows **empty**.

**⚠️ This changes what your existing masks do, at the next launch, with no config change.**

```
# before                                # now
$ ls ~/private                          $ ls ~/private
notes.md  keys.txt                       (empty)
$ cat ~/private/keys.txt                $ cat ~/private/keys.txt
<the file's contents>                   cat: ...: No such file or directory
```

**What you must do.**

- **If you were relying on reading through a mask** — some boxes ended up using a mask as a
  "read-only bind of the box's own home directory" — that no longer works, and it never was what a
  mask meant. Declare a `bindings.ro` entry for the path instead: it is the category that means
  *put this here, read-only*.
- **If you were masking a path to hide it** — the common case — nothing to do; it now does what you
  asked. **Check what was exposed in the meantime**: anything under a masked path has been readable
  inside every box that mask applied to.

**Nothing on the host moves or is deleted.** A mask has never touched host content; it only decides
what the box sees. Whatever lives under the masked path in the box's home directory is still there
on the host, untouched, and is visible again the moment the mask is removed.

**Why.** A mask says NOTHING MAY BE HERE — it is the inverse of a binding, not a variant of one. A
void with the old contents copied into it is not a void, and the difference is a security one: the
paths people mask are the ones they most want gone.

---

### 2.26 Masks now work in a box with the vault disabled

**What changed.** If your box has the vault turned off (`box.enable_vault` false), **every mask you
declared was silently discarded** — no tmpfs, no warning, nothing in the log. `<scope>.masks` is an
ordinary key that has nothing to do with the vault, so a declared mask is now emitted either way.

**⚠️ This changes what a vault-disabled box sees, at the next launch, with no config change.** A
path you asked to mask has been readable all along in these boxes; now it is actually hidden.

```
# a box with the vault disabled and `box.masks: {~/private: true}`
# before                                # now
$ ls ~/private                          $ ls ~/private
notes.md  keys.txt                       (empty)
```

**What you must do.**

- **If you have a vault-disabled box with masks declared** — the mask starts working. **Check what
  was exposed in the meantime**: anything under it has been readable inside that box for as long as
  the mask has been declared.
- **If you were (knowingly or not) depending on the mask NOT applying** — remove the mask entry
  rather than turning the vault back on. The vault was never what suppressed it.
- **Every other box is unaffected.** Boxes with the vault enabled already emitted their masks.

**Why.** The gate was left-over wiring, not a decision. When the only mask was a single hardcoded
tmpfs over `~/workspace/vault`, it genuinely was part of the vault, and it sat in the same block as
the vault's own mounts. Those mounts moved out to the category resolver and the default mask was
dropped, but the `if vault enabled` wrapper stayed behind and kept gating a user key on an unrelated
setting. A declared category that disappears without a word is the one outcome the closed keyspace
forbids — the same defect as §2.24, on a different axis.

---

### 2.27 A mask hides the binds nested under it

**What changed.** Your box's mounts are now assembled by folding every scope's declarations over the
box home, in scope order, into one map keyed by destination. In that fold a mask **clears everything
at or inside its destination**. Until now a bind declared *inside* a masked directory was mounted
anyway and stayed plainly visible through the mask.

**⚠️ This changes what a box with a mask over a bind sees, at the next launch, with no config
change.**

```
# box.masks: {~/private: true}   +   box.bindings.ro.~/private/notes: /host/notes
# before                                # now
$ ls ~/private                          $ ls ~/private
notes                                    (empty)
```

**What you must do.**

- **If you declared a mask and a bind underneath it and wanted both** — they were never compatible;
  the mask was simply not enforced. Move the bind to a destination outside the masked directory.
- **If the bind is the one you want** — remove the mask, or mask a narrower path that does not
  contain the bind.
- **A box with no mask, or with no bind under a mask, is unaffected.** Same destinations, same
  sources. One ordering note: the whole mount set is now emitted shallowest-first — the agent's own
  delivery binds (its binary, launcher and shared install dir) included, where they used to be
  emitted ahead of everything else — so a nested mount always follows the mount it sits inside.

**Scope direction decides which of two things happens, and both are new.** The fold applies each
scope in turn — `system`, then `agent`, then `workset`, then `box` — and a mask clears what is
already there. So the sweep above is what you get when the mask is declared at the **same scope as
the bind or a more specific one**, which is the ordinary case (both in your box's settings file, or a
box mask over a workset bind): the bind is dropped and the box starts.

The reverse — a mask at a **broader** scope than the bind nested inside it, say a workset mask over a
box bind — **stops the launch** with an error naming the bind, the mask and the destination. It is
the same rule; the fold simply meets that arrangement in the other order, and a declaration it has
already placed is refused rather than silently removed. See §2.31, which lists this among the
arrangements that now refuse.

Either way, a bind inside a mask is not a supported arrangement and has not been one — until now it
survived only because the mask was never enforced against it.

**A mask and a bind at the SAME destination.** This is the other half of the same rule, and it moved:
the declaration at the more specific scope takes the destination. A `box.bindings.ro` entry at the
destination of a mask your agent or workset declares is now mounted there, where the mask used to win
that destination outright and hide it. The masks a box receives are read off the assembled mount set,
so a mask that lost its destination is not mounted over the bind that took it.

```
# agent.<agent>.masks: {~/contested: true}  +  box.bindings.ro.~/contested: /host/dir
# before                                # now
$ ls ~/contested                        $ ls ~/contested
 (empty — the mask won)                  (the contents of /host/dir)
```

**Two cosmetic differences you may notice.** Read-write mounts now carry an explicit `rw` in their
options (`Z,U,rw` where `podman inspect` used to show `Z,U`) — podman's default either way, so
nothing about access changes. And the warning for a read-only bind dropped because its source is
missing now names the destination as kanibako resolves it (`/home/agent/canon`) rather than as you
spelled it (`~/canon`). ⚑ The same is true of the two warnings on the **create-time seed** path (a
seed whose source is missing, and a seed whose destination falls outside the box home): they name
the resolved destination too, for the same reason — the seeds are assembled by the same fold, and an
entry's destination is now its identity, so there is no separate name left to print.

**Why.** A mask is the inverse of a bind, not a peer of it: it exists to make a path empty inside the
box. Emitting a mask and then mounting something into the space it was supposed to empty left the
guarantee half-kept, and which half you got depended on path depth. Assembling the whole mount set
once, in one place, is what makes "a mask is a void" a property of the result rather than a hope
about ordering. This is the same correction as §2.24–§2.26, on the last axis where a mask could still
be quietly defeated.

### 2.28 A missing bind source is handled by destination, not by who declared it

**What changed.** When a bind's host source does not exist at launch, kanibako has three answers:

| what happens | which destinations |
|---|---|
| the launch stops with a clean error | the agent's own delivery binds — its binary, launcher and shared install dir |
| the bind is dropped silently | the optional canon chapters, and the agent's best-effort shares |
| the bind is dropped with a warning | everything else |

Which answer a bind got used to depend on how it was **declared** rather than on where it lands: the
agent's delivery binds were emitted separately, carrying their own rule, and the silent case was a
flag on the declaration itself. The whole mount set is assembled in one place now (§2.27), so the
answer is attached to the **destination** and applies to whichever declaration wins it — at any scope.

**⚠️ This changes one case, and it is one you would have to have gone looking for:** a bind of your
own at one of the agent's delivery destinations. Repointing `~/.local/bin/<agent>` (or the agent's
launcher or install dir) at a source that is not there no longer warns and starts the box anyway —
it stops the launch:

```
Error: <agent> mount source disappeared before launch: binding '/home/agent/.local/bin/<agent>'
source missing: /your/path
```

**What you must do.**

- **If you repoint one of the agent's delivery destinations**, make sure the source exists. That is
  the same safe-fail the agent's own binds have always had — a box whose agent binary did not mount
  is a box that cannot run its agent, and it is better to hear that than to be dropped into it.
- **If you do not repoint them, nothing changes.** Every other destination keeps the behaviour it had:
  a read-only bind with a missing source is dropped with a warning, a read-write one has its source
  directory created.

**Why.** The policy was never a fact about *who declared* a bind — it is a fact about *what lives at
that destination*. Carrying it on the route meant one destination could be answered two ways
depending on which emitter reached it, and it is exactly the destination the agent needs that must
not be the one that degrades quietly.

---

### 2.29 A `synced` entry lands inside the bind that covers it, and is applied later in the launch

**What changed.** `<scope>.synced` entries — the files re-copied into the box on every launch, which
is how host credentials reach it — are delivered from the assembled mount set now (§2.27) rather than
from a pair of hardwired paths. Three consequences, in the order you are likely to notice them.

**1. A synced destination inside one of your own binds now arrives where the box can see it.** A
synced destination is written box-side (`~/…`), and until now exactly two of them worked: anything
under `~/workspace` was written into the project directory, and everything else under `~` was written
into the box's home directory. If you pointed a synced entry at a path that some *other* bind covers
— a vault path, a channel path, a directory you bind yourself — the copy was written underneath the
mount rather than into it, so the box never saw the file and nothing said so. The destination is now
resolved through whichever bind covers it, and the copy lands in that bind's host source:

```
box:
  bindings.rw:
    "~/notes": ["/srv/notes"]
  synced:
    "~/notes/today.md": ["/srv/inbox/today.md"]     # -> /srv/notes/today.md, visible in the box
```

**⚠️ If you were relying on the old landing spot, the file moves.** Nothing that was *visible in the
box* changes location — the old path was shadowed by definition — but a host-side script that read
the copy out of the box home directory will not find it there any more.

**2. A synced destination inside a read-only bind, or under a `masks` entry, is skipped with a
warning.** There is no host location to deliver to in either case: a mask is a tmpfs with no source
at all, and writing into a read-only bind's source would put content on the host that the box is
mounted read-only against. Both used to be written under the box home, i.e. behind the mount, where
nothing read them. A destination that no bind covers at all is skipped the same way:

```
synced /home/agent/private/notes.md: /home/agent/private is a mask (tmpfs, no host source); skipping
synced /home/agent/vault/ro/x: /home/agent/vault/ro is bound read-only; skipping
synced /srv/x: no binding covers this destination; skipping
```

These three messages replace the single `guest_dest … is outside /home/agent` warning, and they name
the destination as kanibako resolves it (`/home/agent/x`, not `~/x`) for the same reason §2.27's do.

**One arrangement is not skipped, and it is the mask's *own* destination.** A synced entry whose
source is a single **file**, aimed at exactly the path a mask names, is delivered — and that mask is
**not mounted for the box at all**. One file filling one void is total, so nothing is left
half-hidden. A **directory** copied at that same destination is still skipped, since it would leave
the mask partly populated; and a mask *above* the destination still skips both.

```
box:
  masks:
    "~/.config/agent/creds.json": true    # a void at one file path
    "~/private": true                     # a void at a directory
  synced:
    # a FILE at the mask's own point -> delivered, and no tmpfs is mounted there
    "~/.config/agent/creds.json": ["/srv/creds.json"]
    # a DIRECTORY at a mask's own point -> skipped, and the mask still applies
    "~/private": ["/srv/private"]
    # anything UNDER a mask -> skipped, file or directory alike
    "~/private/notes.md": ["/srv/notes.md"]
```

**⚠️ A mask you rely on stops applying if a synced file is aimed at its exact destination.** Declaring
both was previously a contradiction that resolved in the mask's favour and delivered nothing; now the
file wins the destination outright. If you meant the void, remove the synced entry — or aim it
somewhere the mask does not name.

**3. Synced entries are applied later in the launch, after credential sync.** The pass now runs once
the mount set is final — which is also after the plugin's own credential refresh, and after the three
checks that can abort a launch (an unusable host agent binary, a failed authentication check, an
agent bind whose source vanished). Two differences follow: a launch that fails one of those checks no
longer refreshes your synced files on the way out, and where a `synced` entry and the plugin's
credential sync write the *same* host file, the `synced` entry is applied second and wins. It used to
be applied first and lose.

**4. A synced destination that is exactly a bind's destination is now allowed, and both are
delivered.** This one used to refuse the launch: *"Category collision at '…': a 'synced' copy and a
'binding' mount target the same destination. A copy cannot override a live mount"*. It no longer
does, and nothing else refuses it in its place. Consequence 1 above is the reason — the copy is
written *through* the bind that covers it, so at the exact destination it is written into that
bind's own host source:

```
box:
  bindings.rw:
    "~/notes": ["/srv/notes"]
  synced:
    "~/notes": ["/srv/inbox/notes"]     # -> contents copied into /srv/notes; the bind stays
```

**The mount is not replaced and the rest of the bound directory is untouched** — the copy overwrites
what it names and nothing else. If the covering bind is read-only the copy is still skipped with the
warning consequence 2 describes; a `masks` entry skips it too, except at the mask's own destination
with a file source, which consequence 2 spells out.

**⚠️ A configuration that refused to launch now launches.** If you declared this pair deliberately,
expecting the error to stop you, it will not any more. There is no configuration that launched before
and refuses now.

**What you must do.** Nothing, unless you have a `synced` entry aimed at a destination covered by
some bind other than home or the workspace. Check the launch warnings once: the three messages above
name every entry that is now being skipped, and each names the bind that decided it.

**Why.** A destination means whatever the box's mount set says it means; that is the whole point of
assembling the mounts in one place. Resolving a copy against two hardwired paths instead made the
answer right for the two binds that were hardwired and silently wrong for every other one — and
"silently wrong" for `synced` means the wrong credentials, or none, with a launch that reports
success.

---

### 2.30 `synced` is written once at box creation, and a `seeded` entry sharing that destination is kept

**What changed.** Two things, and they only make sense together.

**1. `box create` now writes every `synced` entry once, unconditionally.** It happens immediately
after the box is seeded, into the bind that covers the destination (§2.29), before anything is
launched. A launch still re-copies synced entries, still only when the host source is newer than the
copy already in the box — that is unchanged.

**2. A destination declared under both `seeded` and `synced` now keeps both entries.** The seed is
applied first, at create, and the sync then overwrites it. Until now the seed entry was discarded and
only the sync was ever delivered.

**Why the two go together.** A launch decides whether to re-copy a synced entry by comparing
timestamps: if the copy in the box is at least as new as the host source, there is nothing to do.
**That comparison only means anything if the copy in the box was written by the sync.** Nothing made
that true. `seeded` entries are copied once, when the box is created, and the copy preserves the
source file's own timestamp — so if a seed source happened to be newer than a sync source aimed at
the same place, the seed's content sat at that destination looking up to date, and every launch
thereafter skipped the sync. Permanently, with nothing in the log, at a destination that is usually a
credential.

Writing the sync once at creation, irrespective of timestamps, makes the assumption true from the
box's first moment: the destination holds sync-written content, and the launch check compares against
the sync's own previous write. The arbitration that used to paper over this — dropping the seed entry
— is no longer needed, so a declared entry is no longer thrown away.

**What you must do.** Nothing for an existing box: this changes what happens at `create`, and an
existing box has already been created.

For a **new** box, check for any destination you have declared under both categories:

```yaml
box:
  seeded:
    "~/.config/tool/config.toml": ["/etc/skel/tool.toml"]   # applied first, at create
  synced:
    "~/.config/tool/config.toml": ["~/host/tool.toml"]      # then overwrites it, at create
```

Both are delivered now, in that order, so the sync's content is what the box ends up with — the same
end state the old arbitration produced, reached by delivering both rather than by discarding one. The
difference is visible only if the two sources are directories: the seed's files that the sync does not
also carry now survive underneath, where before they were never written at all.

⚑ The refusal for a `seeded` destination outside the box home is unchanged, and a `synced` entry at
the same destination does not excuse it. It is still reported by name.

**Why.** The timestamp check is an optimisation — it exists so an unchanged source costs nothing —
and an optimisation was deciding which of two declared entries a box received. Making the sync write
its own destination once at creation fixes the assumption the check was built on, instead of deleting
one of the user's entries to keep the assumption from being tested.

### 2.31 A mount set that cannot be assembled now stops the launch

**What changed.** A box's mounts are assembled by folding every scope's declarations over the box
home in scope order (§2.27). That fold has always had rules about what an arrangement may be, and
until now it applied them silently: when a configuration broke one, the fold was abandoned, the
launch quietly fell back to the older mount-resolution route, and the box started anyway — usually
with the arrangement you asked for, sometimes with an arrangement nobody asked for. **The fold's
refusals now stop the launch, by name, with the reason.** Your files did not change; the rule did.

**This cannot happen on a default install.** Every arrangement below requires a binding, mask, seed
or sync entry that you wrote. Nothing kanibako ships declares one, in any box mode, with or without
an agent.

**The six arrangements that now refuse:**

- **A binding at or above another binding's destination.** A binding may nest INSIDE another; it may
  not take another's exact destination, nor sit above one. Mount order follows the path, not the
  order you declared things in, so the one underneath could never be reached.
- **A binding inside a mask.** A mask is a void; a binding inside it would be swallowed. (A mask
  INSIDE a binding is fine, and is what §2.27 describes.)
- **A mask on another mask** — at its point or inside it. A void within a void hides nothing the
  outer one is not hiding already.
- **A mask at or above the box home.** Home is the foundation everything else folds over. A mask
  there would leave the box with no home at all.
- **A `seeded` entry with a destination outside the box home.** Seeds are copied into the home store
  before any binding folds, so a destination outside it has nowhere to land. Give it a destination
  inside home, deliver it as a binding, or declare it `synced` — which is not home-only (§2.29).
- **A binding whose options contradict its arm.** `ro` in the options of a `bindings.rw` entry, or
  `rw` in a `bindings.ro` one. The mode IS the arm — declare it in the arm that means it.

**And one more, which is new rather than newly-enforced: nothing may be bound at the box home.**
Home is not a binding — it is the foundation the whole set folds over, and kanibako builds it for
every box (§2.32). A `bindings.ro` or `bindings.rw` entry at `~` is a second claim on that one
place, and it now refuses instead of producing a box whose real home is ambiguous. There is no
binding to suppress and nothing to override; §2.32 has the cure.

**What you must do.** Nothing, unless you declare one of the arrangements above. To find out before
you hit it, `kanibako box show --effective` resolves the same settings and reports the same refusal
without starting anything.

**The cure is the same one §2.2 describes**, and the message says so: SUPPRESS the entry you do not
want and then declare the one you do. An override is not enough — these are two different keys, so
both survive the cascade. Set the unwanted key to null in the settings file for its scope:

```yaml
box:
  bindings:
    ro:
      "~/somewhere": null      # suppressed
    rw:
      "~/somewhere": ["/host/path"]
```

**Why.** A refusal that is not raised is a rule that is not enforced. The fold decides what the box
receives; if it rejects an arrangement and the launch proceeds on a different route, then the rule
the fold was applying was never a rule — and the box you get is decided by which route happened to
run. Two of the arrangements above (a binding under a mask, a mask over home) produced a box that
does not match any reading of the configuration. It is better to be told.

---

### 2.32 You can no longer repoint a box's home with a binding

**What changed.** Giving a box a custom home used to be a binding: you wrote an entry at `~` in a
box (or workset, or system) settings file, it overrode the one kanibako ships, and it won.

```yaml
box:
  bindings:
    rw:
      "~": ["/somewhere/else"]     # used to work; now REFUSED
```

**That is gone, and it now refuses the launch by name.** The box home stopped being a binding at
all. It is the *foundation* — the one place the rest of the mount set is folded over, established
before any binding is considered — so there is nothing at `~` to override, and an entry there is a
second claim on the same place. The refusal is the same one you get for any two bindings at one
destination (§2.31), and it names both.

**What you must do instead.** Move the box's *store*, which is what its home is derived from. Boxes
live under `workset.boxes`, and a box's home is a directory inside its own place there:

```yaml
workset:
  boxes: /somewhere/else          # box homes become /somewhere/else/<box>/home
```

`workset.boxes` is a **workset-scope** key: write it in the workset settings file (or the system
one), not a box's — a box settings file may not write a scope that contains it, and a top-level
`workset:` table there is dropped with a warning. It relocates the store for the whole workset,
which is the level a box store belongs to. For a lone box, a **standalone** project is the shape
that gives one box its own store to point wherever you like. **Move the existing directory yourself
before the next launch** — kanibako creates a home where it does not find one, so a repoint without
a move gives you an empty box home and leaves the old one where it was.

**If you only wanted a directory from elsewhere inside the box, you did not need this** — bind it
at a destination *inside* home (`~/work`, `~/somewhere`), which is unaffected and always was. This
entry is only about `~` itself.

**Where home shows up now.** `kanibako box show --effective` lists it first, on its own line,
labelled as the foundation rather than as a settings key:

```
  (foundation) meta.box.home = /data/ws/boxes/mybox/home -> /home/agent
  box.bindings.rw./home/agent/workspace = /code/myproject -> /home/agent/workspace  [Z,U]
```

It is no longer among the per-scope `bindings.*` lines, because it is no longer one of them.
`meta.box.home` is a derived, read-only value: it is shown so you can see what your box gets, and
it is not something you can set. Set `workset.boxes` and read `meta.box.home` back to check.

**Why.** There can only be one home, and everything else in the box is somewhere inside it. While
home was one binding among many, that had to be enforced with rules — and the rules could be
argued with: suppress the shipped entry and declare your own, override it at a deeper scope, put a
mask over it. Making home the foundation removes the question instead of answering it. The path a
box's home comes from is now stated in exactly one place, and relocating a box is a property of
where the box lives rather than of its mount table.

---

### 2.33 An environment variable may be declared at ONE scope only

**What changed.** `<scope>.env.<VAR>` entries used to be gathered from every scope and handed to the
launch in scope order, where the last one seen won. So a variable declared at two scopes — say

```yaml
# in the system settings file
system:
  env:
    EDITOR: nano
```

```yaml
# in a box settings file
box:
  env:
    EDITOR: vim
```

started the box with `EDITOR=vim` and nothing said the other declaration existed. **kanibako now
refuses that launch and names both keys:**

```
Error: the environment variable 'EDITOR' is claimed by two keys: 'system.env.EDITOR' at the
'system' scope already holds it, and 'box.env.EDITOR' at the 'box' scope names it again. …
```

**Why.** A variable is a slot with one value, and kanibako assembles a box by letting each scope act
in turn from the outside in — system, then agent, then workset, then box — with the first one to
claim a place keeping it. That is the same rule two bindings at one destination have always
followed, and the refusal is how a slot says it is taken. Two declarations for one variable meant one
of them could never take effect, and you were not told which.

**This cannot happen on a default install.** Nothing kanibako ships declares an `env` entry at two
scopes.

**⚑ But kanibako ships FIVE `env` declarations of its own, and your key can contest any of them.**
Each is an ordinary key at exactly one scope, so a key of yours naming the same variable at a
*different* scope is the second declaration this section refuses — even though only one of the two
keys is in a file you wrote:

| kanibako's key | scope | see |
|---|---|---|
| `box.env.COLORTERM` | **box** | §2.42 |
| `system.env.KANIBAKO_NAME` | **system** | §2.36 |
| `system.env.KANIBAKO_AGENT` | **system** | §2.36 |
| `system.env.KANIBAKO_DIRECTIVE_SEED` | **system** | §2.36 |
| `system.env.KANIBAKO_AGENT_MARKERS_DIR` | **system** | §2.36 |

**The cure is always the same, and it is a re-spelling, not a move:** write your value on
*kanibako's own key* — `box.env.COLORTERM`, `system.env.KANIBAKO_NAME`, and so on — in whichever
settings file is nearest the box. That is the *same* key, so it is the ordinary cascade and the
nearer file wins; a `COLORTERM` at system scope, or a `KANIBAKO_NAME` at box scope, is the contest.
The two sections above spell out what each of these variables is for and what changing it costs.

⚑ **A plugin's own variables are agent-scope keys and count too**, and they are not in the table
above because which ones exist depends on which plugins you have installed. Two kinds: the ones a
plugin ships as declared `agent.<node>.env.<VAR>` defaults (claude's
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` and `DISABLE_AUTOUPDATER`), and the *realized* ones
kanibako computes from a setting and installs at the same scope (`ANTHROPIC_BASE_URL`,
`GOOSE_MODEL`, `GOOSE_MODE`, `GOOSE_PROVIDER`, `OPENAI_HOST`). Naming either at some *other* scope
is this refusal. §2.34 covers the first kind and §2.40 the second, each with its own cure.

**What you must do.** Give the variable **one owner**: keep the key at the scope the value belongs to
and delete the other one. `kanibako box show --effective` resolves the same settings and reports the
same refusal without starting anything, so you can find them before a launch does.

**⚑ One of the two keys may be one you never wrote in any file — check your persona.** If the box
runs a **persona**, that persona's store config supplies its `env:` entries as live agent-scope keys
(`agent.<agent>.env.<VAR>`) on every launch. They are resolution inputs, not file contents — nothing
is written to `agents/<node>/agent.yaml` — so grepping your settings files for the second key
will not find it. A persona that sets `EDITOR` plus your own `box.env.EDITOR` is exactly this
refusal, and the message names the agent-scope key. **The cure there is one of two things:** delete
your own key and let the persona own the variable, or remove that variable from the persona's store
config and keep yours. The "keep the key at the scope the value belongs to" advice above is about
keys in **settings files**; a persona value is not one, so moving it between scopes is not an
option.

**Overriding a value is a different thing, and it still works exactly as it did.** The rule above is
about two *different keys*. The **same** key written in more than one file is the ordinary cascade and
is untouched: a system file may write `box.env.EDITOR` as a default for every box, and a box's own
file may write `box.env.EDITOR` and win.

```yaml
# system settings file — a default for every box
box:
  env:
    EDITOR: nano
```

```yaml
# this box's settings file — wins, and no refusal: it is the SAME key
box:
  env:
    EDITOR: vim
```

So the cure for a refusal is usually one line: move the value you want onto the key you are keeping,
in whichever file is nearest the box.

---

### 2.34 An agent's own environment variables are settings now, and you can override them

**What changed.** Every agent plugin sets a few environment variables its harness needs — claude
disables its in-box self-updater and its non-essential traffic, goose turns off the OS keyring it
cannot reach in a box and names the context files it should load, and each agent names the file
kanibako writes your flattened guidance to (`KANIBAKO_DIRECTIVE_FINAL`). Those used to be handed to
the container on a private path that ran *above* your whole configuration, so no settings key could
touch them.

**They are ordinary settings now.** Each one is a declared default at the agent scope:

```
agent.claude.env.DISABLE_AUTOUPDATER                      = 1
agent.claude.env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = 1
agent.claude.env.KANIBAKO_DIRECTIVE_FINAL                 = ~/.claude/CLAUDE.md
agent.codex.env.KANIBAKO_DIRECTIVE_FINAL                  = ~/.codex/AGENTS.md
agent.goose.env.GOOSE_DISABLE_KEYRING                     = true
agent.goose.env.CONTEXT_FILE_NAMES                        = [".additionalContext.md","AGENTS.md",".goosehints"]
agent.goose.env.KANIBAKO_DIRECTIVE_FINAL                  = ~/.config/goose/.additionalContext.md
```

**To override one, write the same key** in a settings file that may set it — the agent's own
`agents/<node>/agent.yaml`, or the system settings file:

```console
$ kanibako agent set claude env.DISABLE_AUTOUPDATER=0
```

```yaml
# agents/claude/agent.yaml — the same thing, written by hand.  ⚑ `self:` IS
# `agent.claude`, so the env table sits DIRECTLY under it: there is no second
# `claude:` level, and this is the shape the command above writes.
self:
  env:
    DISABLE_AUTOUPDATER: "0"
```

That was not possible at all before this release. ⚑ **And the agent file's half of it needed one
more fix to work** — see §2.35: until that landed, a value written in the flat `self: env:` table
above was delivered *below* the plugin's declared default rather than above it, so this override
would not have taken effect. (A hand-written second `claude:` level under `self:` did reach the
cascade and did beat the plugin default. That spelling is refused outright now — §2.35 has the
move.)

**What you must do — one case only: if you already set the same variable at another scope.** Because
these are ordinary keys, they take part in §2.33's one-owner rule. A configuration like

```yaml
# a box settings file
box:
  env:
    DISABLE_AUTOUPDATER: "0"
```

used to launch, with the plugin's value silently winning and yours discarded. **It now refuses the
launch and names both keys.** The cure is the one in §2.33 — give the variable one owner — and here
that means dropping your `box.env.*` key and writing the plugin's key instead, with the value you
want. Nothing else in your configuration is affected: a variable no plugin declares is untouched, and
overriding by the same key is the ordinary cascade.

**The variables kanibako does NOT declare are deliberate.** goose's `GOOSE_PROVIDER` / `GOOSE_MODEL`
stay undeclared, because goose owns those in its own persistent config and a kanibako default would
overwrite your choice on every launch. Set them yourself with `agent.goose.env.GOOSE_MODEL` if you
want kanibako to own them.

### 2.35 An agent's own environment variables now resolve where an agent-scope key should

**What changed.** A variable set in an agent's own settings file —

```console
$ kanibako agent set claude env.EDITOR=vim
```

```yaml
# agents/claude/agent.yaml
self:
  env:
    EDITOR: vim
```

— was handed to the container on a path of its own rather than resolved through the settings
cascade with every other key. It arrived *underneath* all of them. **It is an ordinary
`agent.claude.env.EDITOR` key now**, resolving where the agent scope resolves: above `system`,
below `workset`, and expanded like anything else.

**Four things change for you, all of them things that used to happen silently.**

1. **A `system.env.EDITOR` twin now refuses the launch** where it used to win in silence. Two
   keys at two scopes naming one variable is §2.33's one-owner rule, and the agent file is an
   ordinary participant in it now — before, the system value simply arrived and yours did not.
   See the first "what you must do" bullet below. Where there is *no* twin, what changed is the
   cascade position: the agent scope outranks system, and the delivery agrees with that now.
2. **A plugin's declared default no longer beats it.** §2.34 says you override a plugin variable
   by writing the same key in a nearer file; from the agent file's flat `env:` table — the one
   `agent set` writes — that did not work, because the plugin's value came through the cascade
   and yours came in underneath it. It works now, and the `agent set` command above is the
   shortest way to do it. (A hand-written second `<node>:` level *did* override, and is refused
   now; see the third bullet below.)
3. **A persona's stored value no longer beats it.** A persona's store config supplies `env:`
   entries as live agent-scope keys, and the rule has always been that your file wins — the file
   holds your own edits and nothing else, while a persona value is re-read on every launch. The
   env half did not honour that. **If you were relying on a persona value while also having the
   same variable in the agent file, the file's value is what you get now.**
4. **`~` and `$VAR` in the value are expanded**, exactly as in a value written in any other
   settings file. Before, an agent-file `env` value reached the box as literal text, so
   `EDITOR: ~/bin/ed` delivered the tilde itself. ⚑ **Expansion resolves against kanibako's own
   namespace, not your shell's, and an unknown name is REFUSED rather than passed through** — this
   is how `<scope>.env.<VAR>` has always behaved and the agent file simply joins it. So an
   agent-file `env` value containing something like `$HOME` **stops the launch with `Unknown
   variable: $HOME`** where it previously delivered those six characters verbatim (and your box's
   own shell may well have expanded them later, which is why it can look like a regression). The
   host paths kanibako does know are spelled as `@`-references (`@config.data`, `@meta.box.home`);
   for anything meant to be resolved *by the box*, escape it — see below.

**What you must do.** Nothing, unless one of these is true:

- **You have the same variable at another scope as well.** An agent-file `env.EDITOR` plus a
  `box.env.EDITOR` are two keys for one slot, which §2.33 refuses — and the box used to take the
  box-scope value without a word. Give the variable one owner.
- **You wrote `~` or `$NAME` into an agent-file `env` value and meant it to be delivered as
  written** (for the box's own shell to resolve, or as a literal). Escape it — `\~`, `\$` — which
  is both the fix for a launch that now refuses and the way to keep an intended literal intact.
- **You wrote an `env:` or `secret_path:` table under a second `<node>:` level by hand**
  (`self: claude: env:`). **That spelling is refused by name and stops the launch.** See the box
  below — it is the one part of this section that can affect a file you never edited.

#### `self:` is an alias, so nothing nests under it

This one is broader than `env`, and it is the case that can bite a file you never hand-edited.

**`self:` is not a key.** It is an alias that stands for `agent.<that agent>`. So in
`agents/claude/agent.yaml`, a table written like this:

```yaml
self:
  claude:            # ← a second `claude:`
    env:
      EDITOR: vim
```

reads `agent.claude.claude.env.EDITOR` — `self` already *is* `agent.claude`, so the node is named
twice. There is no such key and there never was one. **kanibako now refuses it by name and tells
you the key your spelling reads**, rather than quietly resolving it as though you had written it
the short way.

**It applies to `env:` and `secret_path:` alike, and to any second level** — a literal `default:`
included (`self: default: env:` reads `agent.claude.default.env`, equally impossible). The fix is
always the same: **move the table up one level.**

```yaml
# agents/claude/agent.yaml — the whole of it
self:
  env:
    EDITOR: vim
  secret_path:
    ANTHROPIC_AUTH_TOKEN: ~/.config/claude/token
```

The `agent set` verb writes exactly that shape, so these two are the shortest cure:

```console
$ kanibako agent set claude env.EDITOR=vim
$ kanibako agent set claude secret_path.ANTHROPIC_AUTH_TOKEN=~/.config/claude/token
```

⚑ **Entries you wrote under `self: default:`** — meaning "for every agent" — have no agent-file
spelling at all, because the flat table above belongs to *this* agent. Write that tier in the
**system** settings file:

```yaml
# the system settings file
agent:
  default:
    env:
      EDITOR: vim
```

**Why refuse it rather than keep accepting it?** Because it was never one spelling — it was two,
and the other one won. A file carrying both a nested and a flat table of the same category lost
the nested one *wholesale*: an entry spelled only under `<node>:` was not overridden, it was
absent, with no message. `secret_path` is the one to check first, since that spelling predates the
move to the flat table and may sit in a file you have not opened in a while — and a silently
dropped token pointer looks like an auth failure, not a config error.

⚑ **`bindings:` moved too, and its own section has the detail.** At the time this section was
first written it was the one table still nested; it is flat now, like everything else, and the
nested spelling is refused with the rest. See **§2.37**, which covers every category in one go.

`kanibako box show --effective` resolves the same settings a launch does, so it will show you the
resulting values (and report the refusal, if any) without starting anything.

### 2.36 The four `KANIBAKO_*` variables kanibako sets for itself are settings now

**What changed.** Every box has always come up with four variables kanibako sets from the launch
itself:

| variable | what it carries |
|---|---|
| `KANIBAKO_NAME` | the box's name — what the channel system addresses your mailbox by |
| `KANIBAKO_AGENT` | the resolved agent this box is running, in the spelling you type (`navigator+claude`) |
| `KANIBAKO_DIRECTIVE_SEED` | the in-box path of the kickoff file your guidance chain starts at |
| `KANIBAKO_AGENT_MARKERS_DIR` | the in-box directory agent sessions write their liveness markers to |

They were written onto the container's environment *after* your settings had been resolved, so they
sat above every settings file, above a persona, and above `-e`. **They are ordinary
`system.env.<VAR>` keys now** — kanibako derives them at launch and enters them at the system scope's
floor, and they reach the box through the same one channel every other variable does. `kanibako box
show --effective` lists them among the box's environment variables, as `env KANIBAKO_NAME = …` —
the bare variable name, not a dotted key, because those rows report the merged environment rather
than any one key.

**What you must do — one case only: if you set one of these four at another scope.** They are
ordinary keys, so they take part in §2.33's one-owner rule. A configuration like

```yaml
# a box settings file
box:
  env:
    KANIBAKO_NAME: something-else
```

used to launch, with kanibako's own value written over yours a moment later and nothing said. **It
now refuses the launch and names both keys.** The cure is §2.33's: give the variable one owner —
delete the `box.env.*` key and write `system.env.KANIBAKO_NAME` instead, in whichever settings file
you like.

**Two things you could not do before, and now can.**

- **Override one by writing the same key.** `system.env.KANIBAKO_AGENT_MARKERS_DIR` in your system
  settings file wins over kanibako's derived value, because it is the same key in a nearer file —
  the ordinary cascade, and nothing refuses.
- **`-e` reaches them.** `kanibako start -e KANIBAKO_NAME=scratch` now wins for that launch. It used
  to lose in silence: the variable was set on the container after the `-e` values were merged in, so
  the flag appeared to be accepted and had no effect.

⚑ **They are overridable because the settings system has one rule and these variables are not an
exception to it — not because overriding them is usually a good idea.** Three of the four are read
back by kanibako itself: `KANIBAKO_AGENT` is what `kanibako stop`, `kanibako code` and the
credential watcher inspect to learn which agent a running box carries, and
`KANIBAKO_AGENT_MARKERS_DIR` must name the same directory the in-box supervisor is watching.
`KANIBAKO_DIRECTIVE_SEED` must name the file kanibako binds your kickoff to, or the flatten step at
agent start finds nothing. Change one and you are telling kanibako something about the box that has
to be true.

⚑ **On a PERSONA box the value of `KANIBAKO_AGENT` changed spelling.** v1.7.2 stamped the internal
form, `navigator℘claude`; it is the typable `navigator+claude` now — the same node, written the way
you write it everywhere else. Nothing inside kanibako cares (every reader accepts both, so a box
still running under the old version keeps working), and a bare agent such as `claude` is unchanged.
**If your own scripts or directives compare `$KANIBAKO_AGENT` against a literal, check the
separator.** Comparing against the harness alone — `${KANIBAKO_AGENT##*+}` — is the sturdier habit.

### 2.37 An agent's settings file has ONE level: everything sits directly under `self:`

**Read this if you have ever hand-edited an `agents/<agent>/agent.yaml`, or if a box that
started yesterday refuses today naming a `self.…` table.** This is the section §2.35's `env` and
`secret_path` note grew into: the same rule, now applied to *every* category, `bindings` included.

**What changed.** `self:` is not a key. It is an alias standing for `agent.<that agent>` — the file
already belongs to one agent, so its root already *is* that agent's node. Anything written under a
second level inside it therefore names the node twice:

```yaml
# agents/claude/agent.yaml — REFUSED
self:
  claude:                       # ← a second `claude:`
    bindings:
      ro:
        ~/ref: [/store/ref]
```

`self: claude: bindings:` reads `agent.claude.claude.bindings`. There is no such key and there
never was one. **Every category is written flat now**, and the nested spelling **refuses the
launch, naming the table it found and the key your spelling reads:**

```yaml
# agents/claude/agent.yaml — the whole shape
self:
  model: opus                   # behaviour keys: directly under the root
  env:
    EDITOR: vim
  secret_path:
    ANTHROPIC_AUTH_TOKEN: ~/.config/claude/token
  bindings:
    ro:
      ~/ref: [/store/ref]
  caches:
    ~/.cache/uv: [/store/caches/uv]
  seeded:
    ~: [/store/template]
  common:
    ~/.claude/plugins: [/store/agents/claude/common/plugins]
  synced:
    ~/.config/x: [/store/x]
  masks:
    ~/.ssh: true
```

**What you must do.** Nothing, unless one of these is true:

- **You have a `self: <agent>:` level in an agent settings file.** Delete that level and move what
  was inside it up one — the content itself does not change, only its depth. `bindings:` is the
  one to look for first: it was the last table still written that way, so a file that has not been
  touched since 1.7.2 will have it nested.
- **You have a `self: default:` level**, meaning "for every agent". That tier has **no agent-file
  spelling at all** — the flat tables above belong to *this* agent. Write it in the **system**
  settings file, which is where that tier lives:

  ```yaml
  # the system settings file
  agent:
    default:
      caches:
        ~/.cache/uv: [/store/caches/uv]
  ```

- **You have agent-file *state* nested** — `self: claude: model: opus`. Same cure, one level up
  (`self: model: opus`), and the same refusal if you leave it. This one had a second failure of its
  own: a flat `model:` in the same file silently beat the nested one, so the nested value was not
  overridden, it was ignored.

**Why refuse it rather than keep accepting it?** Because it was never one spelling — it was two,
and the flat one won without saying so. A file carrying both a nested and a flat table of one
category lost the nested one *wholesale*: entries spelled only under `<agent>:` were not
overridden, they were absent, with no message. A refusal that names the table is the smallest
change that makes that impossible.

The refusal prints the fix for the table it found, so you do not have to work it out from here.
`kanibako box show --effective` resolves the same settings a launch does, so you can check a file —
and see the refusal, if any — without starting anything.

### 2.38 The `agent` verbs joined the closed keyspace: `set`, `get` and `reset` refuse what is not a key

**Read this if a script drives `kanibako agent set`/`get`/`reset`, or if you have ever stored a
custom key in an agent's file through the CLI.**

**What changed.** `agent set` used to accept nearly anything — `agent set claude shell=zsh`,
`self.model=opus`, `anything.at.all=x` all returned 0 and stored the text. None of it was a key:
most of it nothing read, some rode into the box's launch snapshot delivered-but-unread, and two
accepted spellings actively broke the file — a `bindings.*` write stored a shape the next launch
refuses, and a scalar `transform_settings` crashed every later `agent` command. The verbs now
answer with the same vocabulary the settings engine uses everywhere else:

- **`agent set <agent> <key>=<value>` refuses an undeclared `<key>` by name**, rc 1, file
  unchanged. The live keys still write: the state keys (`model`, `access`, `endpoint`, …),
  `name`, `run_args`, `env.<VAR>`, `secret_path.<VAR>`, and every plugin-declared key
  (`agent set goose provider=…`).
- **The bind-shaped categories (`bindings`, `caches`, `seeded`, `common`, `synced`) refuse with
  the retirement message.** Those tables are hand-edited in the file; the message shows the shape
  to write.
- **A table-valued key given a scalar (`transform_settings=oops`) refuses naming the expected
  shape.** Previously it stored, and every later `agent` command — and every launch — crashed on
  the file.
- **`agent get` and `agent reset` speak the same vocabulary as `set`.** Reading an undeclared key
  is an error too, not "(not set)". One deliberate read carve-out: `agent get <agent>
  bindings.ro.<dest>` still answers, because `kanibako system get agent.<agent>.bindings.ro.<dest>`
  serves that same read, and two verbs must not disagree about one file.
- **`agent reset <agent> <table>`** (a whole category, or `transform_settings`) **refuses** —
  `set` cannot create those, so any such table is hand-authored and the hand-edit is the honest
  cure. `agent reset --all <agent>` still clears everything and remains the recovery for any file
  the gates refuse.
- **The launch snapshot's "forward-compat" passthrough is closed.** An undeclared scalar already
  sitting in an agent file used to ride into the box unread; it now refuses the LAUNCH by name.
  `agent list` and `agent info` still display such a file, so you can see what to fix without
  starting anything.

Also fixed in the same pass: **a dotted destination reads back whole** — `agent get claude
"bindings.ro.~/.cache/uv"` prints the same entry `kanibako system get
"agent.claude.bindings.ro.~/.cache/uv"` prints, where it used to answer "(not set)"; and the write
route that fractured such a dest across YAML levels is gone (it refuses with the retirement
message instead).

### 2.39 `-e` overrides the key, not the environment

**Read this if you use `kanibako start -e VAR=value`.** For a well-formed flag, what reaches the
box is unchanged. What changed is *how* — and a `-e` item kanibako used to drop in silence now
stops the launch.

**What changed.** `-e VAR=value` used to be written onto the container's environment after your
settings had been resolved — a last layer pasted on top of the finished result. **It is a level of
the settings cascade now**: the value overrides *the key that owns that variable*, for that launch
only, and it is applied while kanibako is deciding the box's variables rather than after.

That is the same move §2.34 and §2.36 made for the variables plugins and kanibako itself set.
Every mechanism that puts a variable in a box now goes through one channel, and `-e` is the CLI
level of it — which is what lets kanibako tell you *which key* a `-e` is overriding, and refuse a
`-e` it cannot honour instead of appearing to accept it.

**Nothing to do in the ordinary case.** `-e EDITOR=vim` on a box with no `env.EDITOR` key anywhere
behaves exactly as before: the variable is injected for that launch as an ephemeral CLI entry,
belonging to no settings file and stored nowhere. `-e` naming a variable a key *does* own — at any
scope, including the `system.env.KANIBAKO_*` stamps of §2.36 — replaces that key's value for the
launch and leaves the file alone.

**The break: a malformed `-e` item now stops the launch instead of being ignored.**

| you type | before | now |
|---|---|---|
| `-e JUST_A_NAME` | silently ignored | refused, naming the item, before the box is touched |
| `-e =value` | set a variable whose name was the empty string | refused, naming the item |
| `-e 2FA=x`, `-e A-B=x`, `-e A.B=x` | passed straight to the container runtime | refused, naming the item |

**What you must do:** if a script or shell alias carries a `-e` item kanibako was quietly dropping,
it will now fail the launch until the item is fixed. Each message names the offending item and the
cure. A variable name is a letter or underscore followed by letters, digits or underscores —
the same shape an `<scope>.env.<VAR>` key is held to; `-e` takes a *variable*, never a dotted key.
An empty value is still legal and still means "set it to nothing": `-e QUIET=`.

⚑ **`-e` is per-launch and writes nothing.** It never touches a settings file, and `kanibako box
show --effective` — which reports stored configuration — does not show it. To make a value stick,
write the key.

---

### 2.40 Realized variables are settings entries, and setting one by hand now refuses

**Read this if any settings file of yours sets `GOOSE_MODE`, `GOOSE_MODEL`, `GOOSE_PROVIDER`,
`OPENAI_HOST` or `ANTHROPIC_BASE_URL` as an `env` key.** Nothing kanibako ships sets any of them
that way, so a default install is unaffected — but if you set one by hand, your box will now refuse
to launch until you move the value.

**What a *realized* variable is.** Some environment variables are not values you store — they are
values kanibako **computes** from settings you already have. goose does not take a model on the
command line, so `agent.<agent>.model` is delivered to it *as* `GOOSE_MODEL`; goose's permission
mode is `GOOSE_MODE`, computed from the `access` key with `-S` / `-A` folded in; claude's
`endpoint` is delivered as `ANTHROPIC_BASE_URL`. The variable is a *rendering* of the key, the way
`--model opus` on claude's command line is.

| variable | agent | derived from | set when |
|---|---|---|---|
| `GOOSE_MODE` | goose | `access` (+ `-S` / `-A`) | **always** — every goose launch |
| `GOOSE_MODEL` | goose | `model` | when `model` resolves to a value |
| `GOOSE_PROVIDER` | goose | `provider` | when `provider` resolves to a value |
| `OPENAI_HOST` | goose | `endpoint` | when `endpoint` resolves to a value |
| `ANTHROPIC_BASE_URL` | claude | `endpoint` | when `endpoint` resolves to a value |

codex realizes none, and neither does a no-agent (`kanibako shell`) box.

**What changed.** These five used to be written onto the container's environment *after* your
settings had been resolved — the last layer, above every file. They are **ordinary agent-scope
settings entries now**, decided in the same pass as every other variable. Three consequences, and
all three are the point:

- `kanibako box show --effective` and the launch read the same channel, so a variable can no longer
  reach a box without having gone through it.
- `-e VAR=value` overrides a realized variable for one launch exactly as it overrides any key —
  which it could only do by careful ordering before, and now does by construction.
- **A key naming a realized variable refuses the launch** instead of being silently overwritten a
  moment later, which is the break below.

**The break: `<scope>.env.<VAR>` naming one of the five now stops the launch.**

Before, such a key was accepted, then discarded — the realization was written over it on the way
out, and nothing said so. Now the launch stops with a message naming **both** the key you wrote and
the key that drives the variable.

| what you have | what happens now | the cure |
|---|---|---|
| `agent.goose.env.GOOSE_MODE: auto` (or at any other scope) | refuses on **every** goose launch | set `access` (`kanibako agent set goose access=full`), or pass `-S` / `-A` per launch |
| `agent.goose.env.GOOSE_MODEL: …` | refuses **whenever `model` resolves to a value** | set `model` instead — `kanibako agent set goose model=…`, or `-M` per launch |
| `agent.goose.env.GOOSE_PROVIDER: …` | refuses whenever `provider` resolves | set `provider` |
| `agent.goose.env.OPENAI_HOST: …` / `agent.claude.env.ANTHROPIC_BASE_URL: …` | refuses whenever `endpoint` resolves | set `endpoint` (or use a persona, which supplies it) |

⚑ **`GOOSE_MODE` is the total one.** The other four are conditional on an *unrelated* key, which is
worth knowing before it surprises you: the same settings file launches or refuses depending on
whether `model` / `provider` / `endpoint` resolves to a value. If you set both `model` and
`env.GOOSE_MODEL`, the file that worked yesterday refuses today — that is the point, because
yesterday one of the two was being thrown away.

⚑ **There is no `env` spelling for a realized variable at any scope.** A twin at a *different*
scope (`box.env.GOOSE_MODE` against the agent-scope realization) meets the ordinary one-owner
refusal of §2.33; at the *same* scope you get the message above. Both say the same thing: drive the
variable through its key.

⚑ **For one launch and no file change, `-e` still works** — `kanibako start -e GOOSE_MODE=chat`
overrides the realized value for that run, writing nothing. It is not a way around the refusal
above, though: a *declared* twin is refused before the flag is applied.

⚑ **`kanibako box show --effective` does not list realized variables.** It reports stored
configuration, and a realization depends on the flags of a launch that has not happened —
displaying one would report a permission tier your next `-S` may not use. Read the driving keys
(`access`, `model`, `provider`, `endpoint`) there instead.

### 2.41 `create --standalone` no longer registers the box; `--register` opts in

**Read this if a script of yours creates a standalone box and then addresses it by NAME.**
`kanibako create --standalone` used to write a `registry.standalone` entry as part of the
create. It no longer does. The new **`--register`** flag asks for the entry, and
**`--name` is ignored without it**.

**Why.** A standalone box keeps its whole identity inside its own directory — that is what
makes it drop-in portable. The registry entry buys exactly one thing: addressing the box by
name *from some other directory*. Registering by default assigned a global name to a box
whose whole point is to move freely, so the entry is now something you ask for.

| what you ran on v1.7.2 | what you get on v1.8.0 | the cure |
|---|---|---|
| `kanibako create --standalone ~/proj` | box created, **no registry entry** | add `--register`, or `kanibako box register ~/proj` afterwards |
| `kanibako create --standalone --name proj ~/proj` | box created, **`--name` ignored**, no entry | add `--register` — with it, `--name` sources the entry's name |
| `kanibako create --standalone --register --name proj ~/proj` | box created **and** registered as before | nothing |

```bash
# Register at create (the v1.7.2 behavior, now explicit):
kanibako create --standalone --register --name myproj ~/myproj

# Or create independent and opt in later — index-only, nothing is re-seeded:
kanibako create --standalone ~/myproj
kanibako box register ~/myproj
```

**Nothing about the box itself changed** — same directory layout, same identity, same
`workset.kuid`. Only the global index entry is withheld. Working *inside* the box needs no
entry at any time: `kanibako start` from the box's own directory (or any subdirectory)
resolves it from its in-tree marker, exactly as before.

⚑ **What actually breaks is a bare NAME used from elsewhere.** `kanibako start <name>`,
`kanibako box info <name>` and `--box <name>` are the registry's only readers, and with no
entry they report the token as unresolvable rather than finding the box.

⚑ **The box is not permanently invisible.** Drop-in detection (§6 of the 1.6.0 runbook
below) still indexes a standalone
box the first time kanibako resolves one from its own tree, so an unregistered box acquires
an entry on first use and is addressable by name from then on. `--register` is what makes
that entry exist *immediately*, and it is the only way to choose the name.

⚑ **`--register` is standalone-only.** A default-mode box's registration is its workset
membership, which is not optional; the flag is accepted and does nothing there.

---

### 2.42 `COLORTERM` is a declared default — nothing writes it into your settings any more

**Read this if you turned truecolor OFF, or if you set `COLORTERM` yourself.** For everyone else
this section is good news you do not have to act on.

**What changed.** v1.7.2 wrote `COLORTERM=truecolor` into `<data>/env` the first time it ran, and
that file was the value's only delivery path — the file §2.19 says is no longer read. v1.8.0 does
not write the value anywhere at all: **`COLORTERM=truecolor` is declared as a default at `box`
scope**, in kanibako's own defaults file, and it simply resolves. No settings file mentions it, and
no first run creates it.

**⚑ This closes a gap §2.19 leaves open.** The old write only ever fired on a genuinely fresh host,
so upgrading from v1.7.2 never produced the replacement key — you would have lost truecolor unless
you migrated that `env`-file line by hand. A declared default applies to every box on every install,
new or upgraded, so **the `COLORTERM` line is now the one entry in a legacy `env` file that needs no
cure at all.** Delete the file (§2.19) and truecolor keeps working.

**A value you already stored still wins.** `box.env.COLORTERM` written in any settings file is the
*same key* kanibako declares, so it is the ordinary cascade and the nearer file wins — no refusal,
nothing to change:

```yaml
# this box's settings file — wins over the declared default
box:
  env:
    COLORTERM: 256color
```

**⚑ A `COLORTERM` key at any OTHER scope now refuses the launch.** Because kanibako's declaration
sits at box scope, `system.env.COLORTERM`, `workset.env.COLORTERM` and
`agent.<node>.env.COLORTERM` are each a *second* scope naming one variable, which is the contested
slot §2.33 refuses — and the message names kanibako's key alongside yours. **The cure is to
re-spell it at box scope:**

```bash
kanibako system reset system.env.COLORTERM
kanibako box set <box> box.env.COLORTERM=<your value>
```

**⚑ To turn truecolor OFF there is no longer a line to delete.** Under v1.7.2 you removed the
`COLORTERM` line from `<data>/env`; a declared default has no line, so disabling it takes an
explicit override. Three spellings, and they do different things:

| what you run | what the box gets |
|---|---|
| `kanibako box set <box> --null box.env.COLORTERM` | **`COLORTERM` is not set at all** — the variable is absent from the box's environment |
| `kanibako box set <box> box.env.COLORTERM=` | **`COLORTERM` is set and EMPTY** (`COLORTERM=`) — present, so a program that only tests whether the variable exists still sees it |
| `kanibako box set <box> box.env.COLORTERM=256color` | that value, verbatim |

The `--null` row is the exact equivalent of deleting the old file line. The empty row is not: most
terminal-capability checks read the *value*, but not all of them do.

**Where it lives.** The declaration is one line in kanibako's packaged `core-defaults.yaml`, under
its `env:` section. That file ships inside the package and an upgrade replaces it, so it is not a
configuration surface: the settings keys above are how you override the value, which is the point of
a default rather than a written one.

---

### 2.43 A workset root no longer carries an identity table; an un-migrated root refuses

**Read this if you have a NAMED workset** — one you made with `kanibako workset create`. Every
workset root written by v1.6.0 or v1.7.x needs a one-time hand edit before it will resolve on
v1.8.0. Primary-mode and standalone boxes are unaffected: neither has this table.

**What changed.** A workset has always been *identified* by the global registry: `workset create`
writes a `name → root` entry into the `worksets:` section of `~/.local/share/kanibako/global/registry.yaml`,
and that entry is what `kanibako workset list` reads and what resolves a bare workset name. v1.6.0
and v1.7.x *also* wrote a copy of the name — plus a `created` stamp and a `projects` list — into the
workset root's own `settings.yaml`, nested as `workset.meta`. v1.8.0 keeps the registry entry and
drops the copy. A workset root now holds exactly two kinds of file: `registry.yaml` with its box
MEMBERSHIP, as flat `name: path` rows under `boxes:`, and `workset.yaml` with SETTINGS ONLY —
sparse, optional, and absent entirely on a workset created from scratch.

`created` is **gone**, not relocated. Nothing records when a workset was made.

**What you must do.** In each workset root, fold the old `projects` list into `registry.yaml`'s
`boxes:` section and delete the identity table from `workset.yaml` — the file v1.7.x called
`settings.yaml`, so do §2.45's rename first or this refusal never fires. Most rows are already in
`boxes:` — kanibako has written one there for every box it created since v1.6.0 — so in practice
this is usually a delete and a check.

```yaml
# v1.7.x — <workset root>/settings.yaml
workset:
  meta:
    name: research
    created: 2026-03-11T09:22:41+00:00
    projects:
      - name: notes
        source_path: /home/you/worksets/research/workspaces/notes
  bindings:
    rw:
      ~/data: /host/data
```

```yaml
# v1.8.0 — <workset root>/registry.yaml: MEMBERSHIP, flat, one row per box
boxes:
  notes: /home/you/worksets/research/workspaces/notes
```

```yaml
# v1.8.0 — <workset root>/workset.yaml, with the identity gone
workset:
  bindings:
    rw:
      ~/data: /host/data
```

The workset is still called `research`, because the global registry still says so. You do not write
its name anywhere, and there is nowhere left to write it.

⚑ **Nothing else moves.** The top-level `workset:` table in `workset.yaml` stays exactly where it
is — it is still where this workset's own settings live (`workset.bindings`, `workset.workspaces`,
`workset.channelroot`, `workset.auth`, …). Delete only the `meta:` table from inside it. **If that
leaves the file empty, you may delete the file.** Any `boxes:` row already in `registry.yaml` is
already correct: leave it. Where a name appears in both the old list and `boxes:`, the `boxes:`
value is the path the box actually ran on — keep that one. Nothing else on disk changes: no
directory moves, no global registry entry changes, no box is re-seeded.

**What you see if you don't.** kanibako refuses by name on any command that has to resolve the
workset — `start`, `box info`, `workset` verbs, and any command run from inside the workset tree
(verbatim; `<path>` is the workset root's `workset.yaml`, `<registry>` its `registry.yaml`):

```
'workset.meta' is a RETIRED location for a named workset's identity table and is still the shape of <path>.
THE RULE: a workset has NO identity table on disk under its root. Its name lives in ONE place — the `worksets:` section of the global registry, which maps that name to this directory and is what `kanibako workset list` reads. This file carries SETTINGS ONLY, is sparse, and may be absent entirely; MEMBERSHIP lives in <registry> as flat `boxes:` entries, `name: path`. kanibako 1.6.0 and 1.7.x kept the name, a `created` stamp and a `projects` list here, so every workset root those releases created carries them. Refusing rather than running: 1.8.0 reads this file as ordinary settings, so it would drop the table as an unsettable `meta` namespace and ignore the `projects` list — your connected boxes would stop resolving with nothing printed to say why.
  Fix, BY HAND:

    1. Each entry of the `projects` LIST becomes one flat entry of the `boxes:` section in <registry>, keyed by its `name`, with its `source_path` as the value. An entry already there is already correct — leave it:

         boxes:
           <project name>: <its source_path>

    2. Delete the `workset.meta` table from <path> — name, created stamp and projects together. NOTHING replaces it: `workset create` already registered this workset under its name in the global registry, and `created` is not recorded anywhere in 1.8.0.

  Everything else in <path> stays put: the top-level `workset:` table is still where this workset's own SETTINGS live (`workset.bindings`, `workset.workspaces`, `workset.auth`, …), so delete only the `meta:` table from inside it. If nothing is left, delete the file outright — a workset root no longer needs one. kanibako 1.8.0 ships no automatic migration for this — see MIGRATION.md §2.43.
```

A root that spelled the table `meta.workset` instead gets the same refusal naming that spelling; the
cure is identical.

⚑ **A root with no identity table anywhere is not an error.** A settings file with no such table —
an ordinary box's `box.yaml`, or a cascade-only file at some directory in the walk — is simply
not carrying one, exactly as a freshly created v1.8.0 workset root is not, and neither is a plain
directory. Only a table still sitting in a `workset.yaml` refuses.

**If you ran a v1.8.0 development build.** One unreleased dev build put the identity table into the
workset root's `registry.yaml` instead, beside a `projects:` map that repeated every member path a
second time. Neither belongs there, and both refuse — the two path copies drift apart, and a
disconnect that dropped one while orphaning the other locked that workspace out of its own workset
under any name. No released version ever wrote this shape, so skip this unless you built from a
`main` checkout between 2026-08-21 and 2026-08-22:

```
'workset:' and 'projects:' sections are RETIRED from a workset registry and still the shape of <registry>.
THE RULE: this file records MEMBERSHIP and nothing else — one flat `boxes:` entry per member, `name: path`, the path written EXACTLY ONCE. A workset's IDENTITY is not on disk under its root at all: it is the entry in the GLOBAL registry's `worksets:` section mapping the workset's name to this directory, which is what `kanibako workset list` reads. An unreleased 1.8.0 development build wrote an identity table into this file, and every member path a second time under `projects:`. Refusing rather than running: the two path copies drift — a disconnect dropped the `projects:` row and orphaned the `boxes:` one, which then refused to let that workspace be registered again under any name.
  Fix, BY HAND:

    1. Each `projects:` entry becomes a flat `boxes:` entry under the SAME name, with its `source_path` as the value. Where a name is in BOTH, keep the `boxes:` value — that is the path the box actually ran with. Then delete `projects:` outright:

         boxes:
           <box name>: <the path>

    2. Delete the `workset:` table. NOTHING replaces it — this workset is already named by the global registry, and no file under its root records a name, a created stamp or anything else about the workset itself.

  Leave the rest of this file as it is. kanibako 1.8.0 ships no automatic migration for this — see MIGRATION.md §2.43.
```

---

### 2.44 Helper boxes: the entrypoint script moved out of `playbook/`

A spawned helper's directory layout carried a `playbook/scripts/` directory holding
`helper-init.sh`, the entrypoint wrapper. `playbook` was the pre-canon name for what is now the
canon **handbook**, and the wrapper level carried nothing else, so both halves are gone:

| before | now |
|---|---|
| `~/helpers/<n>/playbook/scripts/helper-init.sh` (inside a helper) | `~/helpers/<n>/scripts/helper-init.sh` |
| `~/playbook/scripts/helper-init.sh` (a PARENT's own override copy) | `~/canon/notebook/scripts/helper-init.sh` |

**If you never customized `helper-init.sh`, there is nothing to do** — kanibako copies its bundled
default into the new location at spawn.

**If you did**, move your copy to `~/canon/notebook/scripts/helper-init.sh`. That is the canon's
own address for a reusable script, and it is agent-writable, so a box can put one there itself.
A copy left at the old path is read by nothing and reported by nothing — helpers will silently
spawn with the stock wrapper instead of yours.

⚑ **A helper root gets a flat `scripts/`, not `canon/notebook/scripts/`.** A helper home is not a
box: it has no canon binds and no canon skeleton, and giving it a `canon/` directory would make
the launch materialize one. The parent is a real box, which is why only the parent side is
canon-addressed.

---

### 2.45 Each settings file is now named for its tier: `box.yaml`, `workset.yaml`, `agent.yaml`

**Read this if you have ever written a settings file**, which is very nearly everyone. This is the
largest hand-migration in v1.8.0 by file count — it touches every box, every workset and every
agent you have, on every machine.

**What changed.** All four levels of the settings cascade used to be stored in a file called
`settings.yaml`; which tier a given file belonged to was something you worked out from where it
sat. Each per-tier file is now named for its own tier:

| tier | v1.7.2 | v1.8.0 |
|---|---|---|
| box, primary mode | `<data>/primary_workset/boxes/<box>/settings.yaml` | `box.yaml` |
| box, in a named workset | `<workset root>/boxes/<box>/settings.yaml` | `box.yaml` |
| box, standalone project | `<project root>/box_data/settings.yaml` | `box.yaml` |
| workset, primary mode | `<data>/primary_workset/settings.yaml` | `workset.yaml` |
| workset, named | `<workset root>/settings.yaml` | `workset.yaml` |
| **standalone project root** | `<project root>/settings.yaml` | `workset.yaml` |
| agent | `<data>/agents/<agent>/settings.yaml` | `agent.yaml` |
| **system** | `<data>/global/settings.yaml` | **unchanged** |

🛑 **The system tier keeps `settings.yaml`, and renaming it breaks your install.** It is the only
file of its kind, so nothing about it was ambiguous and nothing about it changed. Four filenames
are now in play, not three.

⚑ **The standalone row is the one people misread.** A standalone project's *root* file is the
**workset** tier, not the box tier — its box file is the one under `box_data/`. The two were
indistinguishable while both were called `settings.yaml`, and telling them apart is most of the
reason for this change.

**What you must do.** Rename each file. Nothing inside them changes — same keys, same shape, same
values — so this is `mv` and nothing else:

```bash
# Boxes and worksets under the default primary-mode layout.
data=~/.local/share/kanibako          # or wherever config.data points
for f in "$data"/primary_workset/boxes/*/settings.yaml; do mv "$f" "${f%/*}/box.yaml"; done
mv "$data"/primary_workset/settings.yaml "$data"/primary_workset/workset.yaml

# Agents.
for f in "$data"/agents/*/settings.yaml; do mv "$f" "${f%/*}/agent.yaml"; done

# Each NAMED workset root (repeat per root; `kanibako workset list` prints them).
mv <workset root>/settings.yaml           <workset root>/workset.yaml
for f in <workset root>/boxes/*/settings.yaml; do mv "$f" "${f%/*}/box.yaml"; done

# Each STANDALONE project (repeat per project).
mv <project root>/settings.yaml           <project root>/workset.yaml
mv <project root>/box_data/settings.yaml  <project root>/box_data/box.yaml
```

Not every file exists — a settings file is written only once something is stored at that scope, so
plenty of boxes and worksets have none. Any `mv` that reports a missing source is a scope you never
configured, and there is nothing to carry.

⚑ **If you repointed `workset.boxes` or moved a workset root**, the paths above are not where your
files are. This lists every candidate under a store, and deliberately skips `global/`, which must
keep its name:

```bash
find "$data" <workset root> <project root> -name settings.yaml -not -path '*/global/*'
```

**What you see if you don't.** Nothing. v1.8.0 is a clean break with no compatibility read: a file
left under the old name is not read, not reported, and not mentioned at launch. The box starts on
defaults, exactly as though you had never configured it. **The failure looks like kanibako losing
your configuration**, so if a box comes up without its binds, its `env`, or its agent, check for a
file still called `settings.yaml` before anything else.

⚑ **A named workset root needs `[§2.43]`'s edit as well, and the order matters.** That refusal —
the retired `workset.meta` identity table — is raised by the code that *reads* the workset file,
which is now `workset.yaml`. A root still holding both the old table and the old filename is
silently ignored rather than refused, so **rename first**: the rename is what surfaces the refusal
that tells you the rest.

⚑ **Why this was worth a break.** The filenames now carry what prose used to. Both the spec and the
code had passages whose only job was to say which `settings.yaml` was meant; those are shorter or
gone. It also flushed out a class of our own tests that passed only because two tiers happened to
spell their filename the same way.

### 2.46 Five more bare agent keys are recognised, so five more names stop reading as a box

**Read this if you have a box named `template`, `canon`, `run_args`, `transform` or
`transform_settings`.**

The any-agent defaults are set by their bare names: `kanibako system set model=opus` sets
`agent.default.model`. Six of them worked — `model`, `access`, `endpoint`, `bootstrap`,
`allow_helpers`, `continue_mode` — and the rest of the declared set did not, though the settings
spec declares them all alike. `agent.default.template` answered a refusal telling you to *"set the
any-agent default with the bare key"*, and `template` then answered `unknown config key`; `run_args`
and `transform` had no working spelling at all. All of them are recognised now.

**What that costs you.** `kanibako box get <token>` reads a lone token as a *key* when it is one
and as a *box name* otherwise, so five names moved from the second reading to the first — exactly as
`model` and `access` already had. If you have a box called `template`, `kanibako box get
template` now reports the agent key instead of that box's settings.

**The cure is the two-word form, which has always worked and is never ambiguous:**

```
kanibako box get template <key>             # the box named "template"
kanibako box set template box.image=…       # …and setting one of its keys
```

`kanibako box show`, `kanibako start template` and every other verb that takes a box name are
unaffected — the collision exists only where a single positional could be either thing.

**`transform_settings` is recognised but still not settable**, because its value is a table rather
than a scalar. It is refused by name now, saying so and pointing at the settings file, instead of
being denied as a key that does not exist. Edit it in `<data>/global/settings.yaml` under
`agent: default: transform_settings:`, or in an agent's own `agent.yaml` under `self:`.

### 2.47 An undeclared key in a settings file now stops the command, and the cure is a hand-edit

**Read this if you have ever hand-edited a settings file, or are carrying one forward from an
earlier kanibako.**

**What changed.** A key kanibako does not declare used to resolve. A `box.yaml` carrying

```yaml
box:
  zippity: wibble
```

parsed, merged and came out of the cascade as `wibble` — and then nothing read it. No error, no
warning, and nothing in `box show` that marked the line as dead. The settings keyspace is closed:
*setting* an undeclared key was already an error that named the key, and so was *reading* one at
`system get`. *Resolving* one was the third case, and it is one now too. (`box get` and `workset
get` were the hole left in the reading case; §2.48 closes it, and gives `box show` the marked line
this paragraph says it lacked.)

**Which commands.** The ones that build the resolved snapshot. They all build the same one, so they
all stop at the same place. Measured on the shipped code: `kanibako` / `start`, `shell`, `box info`,
`box show --effective`, `system show --effective`, `rig list`.

⚑ **A key kanibako RETIRED stops you here too, but with its own message.** Before printing the
generic text below, the refusal asks whether the file carries a spelling it has a cure for; §2.1
lists the ones it does, and each carries its own reason and a command you can paste. The generic
message is the answer for a key nothing more specific is known about.

**And which do not, which is worth knowing when a command stays quiet.** `box show` WITHOUT
`--effective` prints the stored file and never resolves, so it never carries THIS message — it
marks the line instead (§2.48); `box diagnose` does not resolve settings. `setup`,
`system diagnose` and `rig diagnose` DO resolve, and each prints this message in full: `setup`
stops at Step 3 and exits 1, the two diagnostics report it on one check row and carry on (§2.49).
Here is the message, as `kanibako box show --effective` prints it:

```
Error: the settings resolved for this box carry 2 entries that are not settings keys (spec §0 — the keyspace is CLOSED):
  - box.zippity: 'zippity' is not a declared box key (declared: canon, enable_vault, image, images_store, share_images, shell, plus the §2a categories)
  - workset.frob: 'frob' is not a declared workset key (declared: boxes, canon, channelroot, kuid, logs, registry, skip_kuid_check, template, vault_ro, vault_rw, workspaces, plus the §2a categories)
kanibako will not resolve settings that carry them: an undeclared key has no meaning to give the box, and passing it through would be the very 'anything goes' behaviour the closed keyspace replaces.
  Fix: remove them BY HAND from the settings file that carries them — this resolve loaded:
    - /home/you/.local/share/kanibako/worksets/demo/boxes/scratch/box.yaml
    - /home/you/.local/share/kanibako/worksets/demo/workset.yaml
    - /home/you/.local/share/kanibako/global/settings.yaml
  'kanibako box reset' cannot remove what is not a key, and 'kanibako box show --effective' resolves through this same seam, so it refuses too.
```

It names **every** offending entry, not the first one — the cure is an edit, and a message that
revealed one line per attempt would turn one edit into several launches.

**What you need to do.** Open the file and delete the line. There is no CLI cure, and the message
says so rather than letting you find out: `box reset` cannot remove what is not a key, and
`box show --effective` resolves through the same seam, so it refuses as well. The message lists
the files this resolve loaded; which of them carried the entry it cannot say, because the snapshot
is the merge of all of them. By tier those are a box's `box.yaml`, a workset's `workset.yaml`, an
agent's `agent.yaml`, the system's `<data>/global/settings.yaml` (§2.45) and the machine-wide
`/etc/kanibako/settings_base.yaml`. Only the files that are actually **there** are listed — most
machines have no base file, and the sample above is from one of them — so a path in that list is
always a path you can open.

**Two things this deliberately does not refuse.**

- **An agent whose plugin is not installed on this machine — the table AND the keys under it.**
  `agent: goose: …` in a file on a claude-only install still resolves, and so does `agent: goose:
  provider: …` — and so does the request spelling of either, `pref: agent: goose: …`, which is
  judged by exactly the same rule as its target. The two go together: an agent's keys are declared by its own plugin, so where the
  plugin is absent there is no list to check a key against — and checking it anyway rejected
  `provider`, a real goose key, for being missing from a list that could not contain it. A config
  you share between machines would refuse on the machine that lacks the plugin, naming a cause that
  was not the reason.
  What is conceded is bounded by what could *be* an agent, not by what you have installed. A §2a
  category token can never name one — kanibako declares that list itself — so `agent: common: …`,
  `agent: env: …`, `agent: seeded: …` and the rest of §2.11's undiscriminated relics refuse on
  every machine — wherever the `agent:` table holding one is read at all, which is not every
  settings file; §2.11 says which. So does `agent: default: …`: the all-agents tier is kanibako's
  own, not a plugin's.
  The cost that remains is stated rather than hidden, and it is irreducible: **there is no list of
  every agent that will ever exist**, so a name kanibako has never heard of is indistinguishable
  from a harness you have not installed. `agent: goose: zippity: …` resolves on a claude-only
  install, and so does a typo'd `agent: clade: zippity: …`. Install goose and `zippity` refuses
  like any other undeclared key.
- **Data that lives inside a declared key** — a bind destination, a `caches`/`seeded`/`synced`
  destination, a `masks` entry. Those are values addressed inside a key you already declared, not
  key paths of their own, and they are not judged as key paths. Your own paths and filenames stay
  yours.

**Scope.** §2.38 closed this same passthrough for the per-agent `agent.yaml` file. This is the same
rule applied to every settings file and to the whole resolved snapshot.

### 2.48 `box get` and `workset get` refuse a name that is not a key, and `show` marks the entry

**Read this if a script calls `kanibako box get` or `kanibako workset get`, or if you have a
settings file you hand-edited and never checked.**

**What changed — the break.** Both verbs used to answer `(not set)` at **rc 0** for any name at
all. `kanibako box get scratch zippity` said the same thing about a typo, a key retired in 1.8.0
and a real key you had simply not set. They now **refuse an undeclared name, rc 1**, printing the
key and the reason:

```
$ kanibako box get scratch box.zippity
Error: 'box.zippity' cannot be read: 'zippity' is not a declared box key (declared: canon,
enable_vault, image, images_store, share_images, shell, plus the §2a categories). If your settings
file carries this entry, 'kanibako box show <box>' lists it as undeclared; removing it means
editing that file by hand.
```

This is the reading third of the closed keyspace, which `system get` (and, since §2.38, `agent
get`) already enforced. `(not set)` is now what it says: **a declared key with no value stored at
this noun**, still rc 0.

**What you must do.** A script that reads a key and branches on rc will now see 1 where it saw 0 —
but only for a name that was never a key, where the `(not set)` it used to get was meaningless. If
you were using `box get` as a spell-checker for your own key names, it is a much better one now.

**Three things still read, and they are the ones people ask about.**

- **Hand-authored bind and category entries.** `box get <box> box.bindings.ro.<dest>` and
  `box get <box> box.{caches,seeded,common,synced}.<dest>` still print the stored value at both
  scopes. Those are not declared keys — the CLI write route for them retired in 1.8.0 — but the
  read is kept on purpose: the retirement message tells you to hand-edit the table, and you can
  only check a hand edit if the read-back works. (`box.masks.<dest>` is not in this group and
  never was: `masks` never had entry names, so it takes the ordinary refusal.)
- **`pref.*` requests.** `box get <box> pref.system.agent` answers exactly as before.
- **`config.*` keys.** Unchanged.

**And `show` now tells you what is in your file.** Because the only cure for an undeclared entry
is a hand edit (§2.47), the stored view — `box show`, `workset show`, `system show`, all three,
without `--effective` — lists such entries under their own heading, naming the file to open:

```
$ kanibako box show scratch
  box_image = myimage
  pref.system.agent = claude
  (undeclared — stored in /home/you/.local/share/kanibako/worksets/demo/boxes/scratch/box.yaml, not keys (spec §0); remove one by editing that file)
    box.auth.nope = 2
    box.zippity = wibble
```

That block is a **display of file content**, not a read of a key: nothing is resolved, no default
is invented, and the value is echoed exactly as your file spells it. It is what makes §2.47's
"open the file and delete the line" a cure you can actually follow — before this you had to open
the YAML to discover the line existed.

It marks only what the keyspace does not declare. Data inside a declared key — a bind destination,
a `masks` entry, an `env.<VAR>` name — is not marked, and neither is a table that IS declared but
that this file's tier is not allowed to set (an `agent:` table in a `box.yaml`, §2.11): that is a
different fact, and calling it "undeclared" would be false.

### 2.49 `kanibako setup` stops at a settings error instead of finishing, and the diagnostics name it

**Read this if a script runs `kanibako setup`, or if `system diagnose` or `rig diagnose` ever told
you your rig was "not configured".**

**What changed.** Five places resolved settings inside a catch-all that reported every failure as
something else. §2.47 named three of them as a defect; this is the repair. All of them now print the
refusal itself — and `kanibako setup` **stops** rather than reporting and running on.

**The break: `kanibako setup` exits 1 at Step 3 where it used to exit 0.** The old run said the
configuration was *not initialized yet* — the inverse of the truth, since the configuration is
initialized and is the broken thing — promised a rig pull that would not happen, then went on
through Step 4 and Step 5 and closed with `Setup Complete` / `You're ready to go!` at **rc 0** over
a store no command could resolve. It now looks like this:

```
$ kanibako setup --agent claude --refresh-templates

Kanibako Setup
========================================

Step 1: Container Runtime
  [ok] /usr/bin/podman (podman version 5.4.2)

Step 2: Agent Detection
  [ok] Shell (image default; no host binary needed)
  [ok] Claude Code detected

Step 3: Container Rig
  [!!] Settings error -- setup cannot continue (reported below).
       Nothing has been written. Fix the file the error names,
       then re-run `kanibako setup`.

Error: the settings resolved for this box carry 1 entry that is not a settings key (spec §0 — the keyspace is CLOSED):
  - box.zippity: 'zippity' is not a declared box key (declared: canon, enable_vault, image, images_store, share_images, shell, plus the §2a categories)
kanibako will not resolve settings that carry it: an undeclared key has no meaning to give the box, and passing it through would be the very 'anything goes' behaviour the closed keyspace replaces.
  Fix: remove it BY HAND from the settings file that carries it — this resolve loaded:
    - /home/you/.local/share/kanibako/global/settings.yaml
  'kanibako box reset' cannot remove what is not a key, and 'kanibako box show --effective' resolves through this same seam, so it refuses too.
```

**Nothing is written when it stops.** The abort precedes Step 4's system-agent write, Step 5's
template refresh, and — the one that matters most — the `setup_completed` marker. That marker is
what lifts the upgrade gate of §2.12, so a run that wrote it over an unresolvable store would tell
every later command that setup had succeeded.

**What you must do.** Open the file the error names, delete the line, re-run `kanibako setup`. A
script written as `kanibako setup && kanibako start` used to walk straight past this into a launch
that then failed for a cause the setup output had denied; it now stops where it should. A script
written as `kanibako setup || exit 1` used to see rc 0 and carry on.

**`system diagnose` and `rig diagnose` report and keep going, rc 0 unchanged.** The `[--] Image:
cannot check (not configured)` line becomes a `[!!]` row with the refusal quoted underneath,
alongside every other check:

```
$ kanibako system diagnose
Kanibako System Diagnostics
========================================

[ok] Container runtime: /usr/bin/podman (podman version 5.4.2)
[!!] Image: settings error -- reported below
        the settings resolved for this box carry 1 entry that is not a settings key (spec §0 — the keyspace is CLOSED):
          - box.zippity: 'zippity' is not a declared box key (declared: canon, enable_vault, image, images_store, share_images, shell, plus the §2a categories)
        kanibako will not resolve settings that carry it: an undeclared key has no meaning to give the box, and passing it through would be the very 'anything goes' behaviour the closed keyspace replaces.
          Fix: remove it BY HAND from the settings file that carries it — this resolve loaded:
            - /home/you/.local/share/kanibako/global/settings.yaml
          'kanibako box reset' cannot remove what is not a key, and 'kanibako box show --effective' resolves through this same seam, so it refuses too.
[ok] Agent: Claude Code: (/home/you/.local/bin/claude)
```

The `rig diagnose` row is `Configured image` and the baseline probe's row is `Baseline`; both read
the same way. `rig diagnose`'s baseline probe stops after the refusal, because there is no resolved
image left to probe.

The `Storage` and `Journal` rows of `system diagnose` behave the same way, and they answer to a
wider trigger than a settings refusal: a config file that is not valid YAML. Both used to print
`cannot check` over an error that already named the file and the fix, so one malformed
`kanibako_config.yaml` could yield one honest line and two bland ones in the same run. rc is
unchanged at 0.

**`kanibako code` warns and still attaches, rc 0 unchanged.** When a settings error stops it
resolving the box's image, VS Code opens without the box's workspace folder and without the agent
extension. That was already true; you now hear about it at the default log level —
`VS Code will attach without the box's workspace folder or agent extension: the box image could not
be resolved.`, followed by the refusal.

**Unchanged, and deliberately: a failure kanibako did not foresee still reads the old way.** Only
errors kanibako raises on purpose — the ones whose text is already written for you — are reported
like this. Anything else still produces `cannot check (not configured)`, and `setup` still runs on
past it to its summary, which is what that line was written for.

### 2.50 A bare-relative host source is refused, and a bare leaf is rooted where it is declared

**Read this if you hand-wrote a source in a `bindings.ro`, `bindings.rw` or `synced` entry, or a
bare leaf in a `common`, `caches` or `seeded` entry, in any settings file.**

**What changed.** v1.8.0 said category sources are rooted at their declaration rather than at
assembly (§2.7). Exactly one loader did it — the one that reads an agent plugin's own bundled file.
Every other path stored the string you typed and passed it to podman unchanged. Both halves of the
rule now hold at all four scopes and in your own files: an abstract category's bare leaf is rooted
where it is declared, and a source that cannot resolve on its own is refused where it is declared.

**Why a refusal and not a warning: a bare-relative source never bound your directory.** podman reads
a source beginning with neither `.` nor `/` as the name of a **named volume**, and creates it —
`--rm` never removes it. So

```yaml
box:
  bindings:
    rw:
      "~/notes": ["mynotes"]
```

did not mount your `mynotes` directory. It made a volume called `mynotes` in your rootless container
store, mounted that empty volume at `~/notes` inside the box, and left the volume behind after the
box was gone. The `rw` arm also created a directory named `mynotes` in whatever directory you
happened to run `kanibako` from. A `./mynotes` spelling is refused too, and is no better: it
resolves against a working directory rather than naming a place.

**The three concrete categories refuse it by name, at every scope** — `bindings.ro`, `bindings.rw`
and `synced` have no declaration root at any scope, so no later layer can supply the one a relative
source needs:

```
$ kanibako system show --effective
Error: bindings.ro entry at '/home/you/notes' declares a bare-relative host source 'mynotes'; a source must fully resolve on its own — absolute, ~, $var or an @-ref. bindings.ro takes NO root at any scope (spec §2a), so no later layer may supply the missing one: a relative source resolves against whatever directory kanibako happens to be run from, and for a MOUNT podman reads a source beginning with neither '.' nor '/' as the name of a NAMED VOLUME rather than as a host path at all. Spell the source out.
```

⚑ `synced` is a copy rather than a mount, so podman never saw its source: a bare-relative one was
read as a path under whatever directory `kanibako` was run from. Wrong in a quieter way, and refused
by the same rule.

**The cure is to spell the source out** — an absolute path, `~/…`, `$VAR/…`, or an `@`-ref:

```yaml
box:
  bindings:
    rw:
      "~/notes": ["/home/you/notes"]     # or ~/notes, $HOME/notes, or an @-ref
```

⚑ **`masks` and `secret_path` are untouched.** A mask declares no source, and a `secret_path` is a
scalar rather than a bind map; neither goes through this rule.

**The three abstract categories go the other way: a bare leaf is now rooted for you.** `common`,
`caches` and `seeded` do have a declaration root — the store of the scope that declares the entry —
so a bare leaf is joined under it when the file is read, and what is stored resolves on its own:

| Declared at | A bare leaf `L` in category `C` becomes |
|---|---|
| `system:` | `@config.data/C/L` |
| `self:` (an agent's `agent.yaml`) | `@meta.agent.<agent>.path/C/L` |
| `workset:` | `@meta.workset.path/C/L` |
| `box:` | `@meta.box.path/C/L` |

A source that already resolves on its own is stored exactly as you wrote it — the root is a default
for a relative source, not a prefix applied to everything. A `pref.agent.<name>.<category>` request
is rooted the way the key it targets would be, so a request and a direct declaration store the same
string.

**What you must do.** Look through every settings file for a source that starts with none of
`/`, `~`, `$`, `@`. There will usually be none — nothing kanibako ships teaches this spelling, so it
only appears in a file you wrote.

- **In `bindings.ro`, `bindings.rw` or `synced`:** the next command that resolves settings refuses
  until you rewrite it. §2.7 told you to rewrite already-stored relative workset sources by hand and
  warned they would otherwise pass through silently; they no longer pass through, but the rewrite is
  the same one.
- **In `common`, `caches` or `seeded`:** nothing refuses, and the entry starts working — which is
  the change. It now reads `<scope root>/<category>/<leaf>` on disk. `common` and `caches` are
  mounts, so before this the leaf was a volume name: whatever the box wrote through such an entry is
  in that volume, not in the directory it now reads. `podman volume ls` lists the leftovers under
  the exact source string you typed, and `podman volume rm <name>` removes one once you have taken
  out anything worth keeping. `seeded` is a copy, so it had no volume — its source was read under
  whatever directory `kanibako` was run from, and usually was not there at all.

### 2.51 The six `workset.channels.*` keys are read, and three of them did nothing before

**Read this if you ever ran `kanibako workset set` on a `workset.channels.*` key. If you never
repointed one, nothing here applies to you and nothing is required.**

**What changed.** The workset channel family declares `common`, `chat`, `share`, `broadcast`,
`mailboxes` and `share_global`. kanibako read none of them: it resolved `workset.channelroot` and
joined the directory names on by hand. Those joins are each key's documented default, so a default
setup behaved correctly and still does — the keys only ever mattered if you changed one. Each leaf
is now resolved through its own key.

| If you set | What happened before | What happens now |
|---|---|---|
| `workset.channels.broadcast`, `.mailboxes`, `.share_global` | Nothing. The value was accepted, written to `workset.yaml` and read back by `kanibako workset get`, and no path moved | The value is used |
| `workset.channels.chat`, `.share` | The mount followed your override; the rest of kanibako did not | Mount, seeded files and the `meta.box.*` addresses agree |
| `workset.channels.common` | Honoured | Unchanged |
| `workset.channelroot` | Honoured, but a settings file referencing `@workset.channelroot` got nothing back | The launch resolves it, so the reference works |

**What to do.**

1. **If you repointed `chat`, move your existing logs.** This is the one case that leaves data
   behind. `general.md` and `broadcast.md` were being written and rotated under
   `<channelroot>/chat` — a directory that was mounted nowhere — while `~/channels/workset/chat`
   in every box followed your override and stayed empty. Those logs are still on the host at
   `<channelroot>/chat`. Move them into the directory your `chat` key names; nothing moves them
   for you.
2. **If you repointed `mailboxes`, check where your mail actually is.** Repointing it looked like
   it had relocated your box's inbox and had not, so your mail is under the *default* mailboxes
   directory. It will be read from your repointed path now.
3. **If you repointed `broadcast` or `share_global`, expect the path to take effect** on the next
   launch. Anything written under the old default stays there.

**One new refusal.** A repoint that cannot be resolved before the launch snapshot is built now
fails by name instead of quietly falling back to the default. This is the same closed-keyspace
behaviour `workset.channelroot` already had; it now covers the five leaves as well. A setting that
resolves is unaffected.

---

### 2.52 An agent or persona name may no longer contain a dot

**Read this if any agent node of yours is spelled with a `.`** — in practice that means a persona,
because a harness name comes from the plugin (`claude`, `codex`, `goose`) and none of those has
one. A ref like `kimi.k3+claude` was legal in v1.7.2 and is refused now.

**What changed.** The characters an agent-ref segment may contain went from `-`, `.` and `_` to
`-` and `_`. A node name is a **keyspace segment**, and `.` is the settings key-path separator, so
`agent.kimi.k3+claude.model` could not be told apart from a genuine nested key — and the same
charset admitted `..`, a segment that resolved as a path component pointing above the agents
directory. (Unicode letters were admitted in the same change, so a persona named in any script
works.)

**What you see.** Every command that parses an agent ref stops, whether the ref comes from a flag,
from `pref.system.agent` in a settings file, or from `system.agent`:

```
Error: invalid agent ref 'kimi.k3+claude': persona segment 'kimi.k3' must be non-empty & contain
only letters & digits (any language), '-', & '_' (no separator); '.' is reserved as settings
key-path separator and cannot appear in an agent name
```

A bare (non-composite) name gets the sibling message, `invalid agent name '…'`, with the same
trailing note about the dot. **The node is stuck:** the refusal happens while the ref is being
parsed, before anything can act on it, so there is no command that will operate on the old name —
including one that might have renamed it.

**What you must do — a hand rename, in this order.** There is no `kanibako agent rename`; the node
name appears in a directory name and in settings values, and you move both yourself.

1. **Stop any box running that agent.** A running box carries the old name in `KANIBAKO_AGENT`.
2. **Choose a name with no dot** — `kimi-k3` for `kimi.k3`; the hyphen is still legal.
3. **Rename the node's store directory.** List `<data>/agents/` first and rename whichever entry
   carries the old persona name, **keeping the separator character that entry already uses**:

   ```bash
   ls <data>/agents/
   mv '<data>/agents/<the old entry>'  '<data>/agents/<same name, dot replaced>'
   ```

   A persona node's directory joins persona and harness with `+`, the same character you type in a
   ref. Some stores carry an internal separator there instead, so read the name rather than assuming
   it; the rename only replaces the dot in the persona part and leaves the join alone. Everything
   inside — `agent.yaml`, `common/`, `caches/` — moves with it and needs no edit.
4. **Rewrite the selection key wherever it names the old ref** — **by hand, in the file.** In each
   box's `box.yaml` (`pref: {system: {agent: kimi.k3+claude}}`) and, if it names it, `system.agent`
   in `<data>/global/settings.yaml`. Edit the YAML directly rather than reaching for
   `kanibako box set`: the value sitting in the file is the ref that no longer parses, so the file
   is the surface you can be sure of.
5. **Rewrite any `agent.<node>.*` tables** keyed by the old node in any settings file, to the new
   node spelling.
6. **Change it everywhere else you have typed it** — `--agent` in scripts, and the persona's own
   entry in your persona store if you use one. The ref you type must match the store entry.

⚑ **Step 3 before step 4, and neither alone.** A renamed directory with an un-rewritten
`pref.system.agent` selects an agent whose store is gone; a rewritten key with an un-renamed
directory launches a box on an empty store, which looks like the agent losing its configuration.

---

### 2.53 A `box:` table in the SYSTEM settings file now takes effect

**Read this if you ever ran `kanibako system set box.image=…` (or `box.share_images=…`, or
`box.shell=…`), or hand-wrote a `box:` table into `<data>/global/settings.yaml`.** If you never
did, this section cannot fire.

**What changed.** Those writes were accepted and stored — but the three box scalars were resolved
by a separate flat path that started at `~/.config/kanibako_config.yaml` and overlaid only the
workset and box files. **The system settings file was not in that chain**, so the value you stored
sat in the file and steered nothing. v1.8.0 resolves all three through the keyspace cascade, in
which the system settings file is a real level between `base` and `agent.default`. The three keys:

| key | what a stale value now does |
|---|---|
| `box.image` | selects the rig for every box that does not set its own |
| `box.share_images` | turns the host image store's read-only share on or off for those boxes |
| `box.shell` | selects the in-box shell for those boxes |

**How a user notices.** A `system set box.image=…` you ran months ago, experimenting, starts
choosing the rig for your whole install on the first launch after upgrading. Nothing announces it,
because from v1.8.0's point of view the value was always meant to be read.

**What you must do.** Open `<data>/global/settings.yaml` and look for a top-level `box:` table.
Anything under it is now live. **Read the file** — it is the authority on what the system tier
holds, and `kanibako system show` prints that tier's stored overrides as a CLI view of the same
thing. Then remove what you did not mean to keep, with `kanibako system reset box.<key>` or by
deleting the lines. To confirm what a box ends up with afterwards, `kanibako box info <box>` prints
the resolved `Image:` row.

⚑ **This is a value taking effect, not a refusal.** A `box:` table in the system file is legal and
always was — v1.8.0 is the release that started honouring it.

---

### 2.54 `create` no longer stores the image it picked, and `--share-images` now does

**Read this if you have boxes made before v1.8.0 and expect a change to the default rig to reach
them.** It will not reach the old ones, and it will reach the new ones.

**What changed.** v1.7.2's `box create` wrote an image into the new box's own settings file
**whether or not you passed one** — it stored `--image` if you gave it, and the resolved default if
you did not. v1.8.0 persists only what you passed *explicitly*: `-i` / `--image` / `--rig`, and —
this is new — `--share-images`.

**The consequence is a fleet that splits in two, with nothing in the CLI to label the halves:**

- a box created on **v1.7.2** carries a **pinned** `box.image` in its own settings file and will
  keep using that image no matter what you change the default to;
- a box created on **v1.8.0** with no `--image` is **floating**: it has no `box.image` of its own
  and follows the cascade, so a changed default reaches it on the next launch.

`kanibako box info <box>` prints the resolved `Image:` row for both, identically. **The way to tell
them apart is the box's own file:** open its `box.yaml` and look for an `image:` entry under `box:`.
Present means pinned; absent means floating.

**What you must do — decide which you want, per box.**

- **To unpin an old box** so it follows the default: delete the `image:` line from the `box:` table
  in that box's `box.yaml`.
- **To pin a new box** so it never moves: `kanibako box set <box> box.image=<rig>`.
- **To set the default for everything floating:** `kanibako system set box.image=<rig>` — which,
  since §2.53, now actually works.

**⚑ `--share-images` at create now persists, where it used to last one launch.** If you have a
create script that passes `--share-images` habitually, it is writing `box.share_images: true` into
each new box's settings file from now on rather than applying for that launch only.

**⚑ Passing either flag to an EXISTING box now says so out loud.** It never persisted for an
existing box and still does not, but the silence is gone — the launch prints a notice naming the
flag and the cure:

```
Notice: --image X applies to THIS launch only — box 'myproj' already exists, so its stored
image is unchanged.
  To persist it: kanibako box set /path/to/myproj box.image=X
```

Scripts that start existing boxes with `--image` or `--share-images` will see this on **every**
launch. It goes to stderr, and the behaviour it describes is unchanged from v1.7.2 — only the
announcement is new. (Flags refused outright against a *running* box are a different change; see
§2.17.)

---

### 2.55 State files follow a non-default `config.data` leaf

**Read this ONLY if `config.data` points somewhere whose last path segment is not `kanibako`** —
for example `config.data: ~/.local/share/kani-test`. On a default install nothing moves and this
section is a no-op.

**What changed.** Three state stores were hardcoded under `$XDG_STATE_HOME/kanibako/`. They now sit
under `$XDG_STATE_HOME/<the last segment of config.data>/`, so a second store no longer writes its
state into the first store's directory:

| what | old path | new path |
|---|---|---|
| held-over baseline warnings | `$XDG_STATE_HOME/kanibako/launch-issues.<box>` | `$XDG_STATE_HOME/<leaf>/launch-issues.<box>` |
| held-over bind-shadow warnings | `$XDG_STATE_HOME/kanibako/launch-shadows.<box>` | `$XDG_STATE_HOME/<leaf>/launch-shadows.<box>` |
| `code --remote` tunnel contexts and dispatch log | `$XDG_STATE_HOME/kanibako/vscode-remote/` | `$XDG_STATE_HOME/<leaf>/vscode-remote/` |

**How a user notices.** Two ways, both quiet:

- **A warning that was waiting to be printed never appears.** The first two files hold a warning
  raised during a launch so it can be shown *after* the session closes. One written before the
  upgrade is orphaned at the old path — it is not lost data, just a message you will not see. The
  next launch writes a fresh one at the new path.
- **A saved `kanibako code --remote` context stops resolving.** The context store moved, so a
  context you established before the upgrade is invisible: by name it does not exist, and the
  generated dispatch wrapper still on disk points at the old directory.

**What you must do.** Re-run `kanibako code --remote` for each context you use — that regenerates
the wrapper against the new directory and re-establishes the tunnel. The old
`$XDG_STATE_HOME/kanibako/vscode-remote/` tree can then be deleted; nothing reads it.

---

### 2.56 A `synced` destination must be covered by a mount

**Read this ONLY if you declare `synced` entries.** Nothing kanibako ships declares one, and a
destination under `~` is always covered, so most configurations are a no-op here.

**What changed.** A `synced` entry is applied last, after the mount set is final, and each
destination resolves *through* the mount containing it — that is what decides where on the host the
file actually lands. If nothing is bound at or above the destination there is no such mount, and the
copy went into the container's own ephemeral storage, which is discarded when the box stops.
Kanibako logged `no binding covers this destination; skipping` and continued. It now refuses the
launch instead.

The reason for the change is what a `synced` entry usually holds. It is a credential far more often
than not, so skipping one produced a box that started cleanly and then failed to authenticate inside
the agent, pointing at nothing. A refusal at assembly is the cheaper failure by a wide margin.

```yaml
# box.yaml — REFUSED: nothing is bound at or above /data
box:
  synced:
    "/data/z": ["/host/creds/z"]

# box.yaml — accepted: the destination is inside a mount you declared
box:
  bindings:
    rw:
      "/data": ["/host/data"]
  synced:
    "/data/z": ["/host/creds/z"]

# box.yaml — accepted: ~ is always covered, because home is the foundation
box:
  synced:
    "~/.aws/credentials": ["/host/creds/aws"]
```

**How a user notices.** The launch stops with a message naming the source, the destination and the
cure:

```
the synced copy of '/host/creds/z' targets '/data/z', which NO mount covers. A 'synced'
copy is applied LAST, after the bind map is final, and it resolves THROUGH the mount
containing its destination - so with nothing bound at or above '/data/z' the copy would
be written into the container's own ephemeral storage and lost the moment the box stops,
silently. ...
```

**What you must do.** One of two things, whichever fits:

- **Bind the area.** Add a `bindings.rw` entry at or above the destination. The copy then lands in
  that binding's host source and persists.
- **Move the destination under `~`.** The home binding always exists, so anything inside it is
  covered.

**What is unchanged**, so that you do not "fix" something that was never broken:

| case | still |
|---|---|
| a `synced` destination **at a binding's exact path** | accepted — the copy writes through into that binding's source, and the binding remains |
| a `synced` destination **inside** a binding | accepted, and always was |
| a destination covered by a **`masks`** entry | judged by the mask rules, which already refused a mask as a parent; this rule does not touch it |
| **`seeded`** | unchanged. A seed is copied at *creation*, when the only mount that exists is home — so its rule was, and remains, that its destination lies inside home. That is the same requirement at a different moment, not a second one. |

⚑ If you were spelling a destination with `$XDG_DATA_HOME` or another XDG variable, note that those
are expanded on the **host**, so `$XDG_DATA_HOME/z` becomes a path like `/data/z` — a directory on
your own machine, which nothing in the box binds by default. Such an entry is refused by the rule
above unless you bind that path yourself. Deliberately mirroring your host layout into the box that
way is fine; what is refused is a destination that resolves nowhere.

### 2.57 A plugin-declared agent setting is a key at every door, not just at `agent`

**Read this if you use a setting an agent plugin declares rather than kanibako itself — goose's
`provider` is the shipped example — or if a script of yours works around one of the commands below.
Nothing to change on disk; two commands that used to fail now work.**

**What changed.** *The agent verbs joined the closed keyspace* (see that section above) already
told you that `kanibako agent set goose provider=openrouter` works and that plugin-declared keys
are real keys. They are — at the `agent` verbs. Spelled out in full at the config verbs they were
not:

| command | before | now |
|---|---|---|
| `kanibako system set agent.goose.provider=openrouter` | `Error: unknown config key: agent.goose.provider`, rc 1 | `Set agent.goose.provider=openrouter`, rc 0 |
| `kanibako system get agent.goose.provider` | `agent.goose.provider: (not set)`, **rc 0, with the value on disk** | the stored value, rc 0 |
| `kanibako system reset agent.goose.provider` | `Error: unknown config key: agent.goose.provider`, rc 1 | `Cleared …`, rc 0 |

The `get` row is the one worth pausing on: the answer was not an error, it was a wrong answer. A
value you had set with `kanibako agent set` — and which every launch read — was reported as unset.

**Why.** Kanibako kept two lists of what an agent setting may be called. The one that decides
whether a key is a key had the installed plugins' declarations folded into it; the one that
recognises the `agent.<agent>.<setting>` spelling before a command dispatches on it did not. So the
same name was a key on one path and not a name at all on another. There is one list now.

**What is unchanged.** A setting no agent declares is still refused, still by name, still rc 1 —
this widens the vocabulary to what the plugins actually declare, and no further. Nothing stored
changes; nothing that worked stops working. If you scripted around the failures above by calling
`kanibako agent set <agent> <setting>=<value>` instead, that command was correct before and is
correct now — keep it if you prefer it.

⚑ **One rough edge remains, and it is not new: the BARE spelling of a plugin-declared setting is
still not settable.** `kanibako system set provider=x` answers `unknown config key`, and
`kanibako system set agent.default.provider=x` refuses with a cure that names that bare spelling.
The bare form addresses the all-agents fallback tier, and a plugin's setting belongs to one agent,
so whether it should be settable there at all is an open question rather than an oversight. Set it
on the agent — `kanibako agent set goose provider=…`, or the full
`kanibako system set agent.goose.provider=…` — which is where it takes effect either way.

---

### 2.58 A repointed `workset.boxes` or `workset.logs` now takes effect

**Read this ONLY if you have set `workset.boxes` or `workset.logs` in a `workset.yaml`.** Neither
is set by default, and nothing kanibako ships sets them, so on an ordinary install this section is
a no-op.

**What changed.** Both keys were only half-honoured. The directory walk that identifies a workset
root read them correctly, so a workset whose store you had moved was still *found* — but the code
that composed the actual paths spelled `boxes` and `logs` by hand instead of reading the setting.
So the box trees were created, moved, duplicated, converted, purged and deleted under the default
`<workset root>/boxes/`, and each box's helper log was written under the default
`<workset root>/logs/`, whatever you had written in the settings file. Both are now resolved
everywhere: the box store used by `create`, `box move`, `box duplicate`, `box convert`,
`workset connect`, `workset disconnect`, `clean --purge` and `workset rm --purge`; the primary
workset's own box and log roots; and the host-side writer for a named box's helper log.

There is a second half to the helper-log case worth stating plainly. The *mount* into the box has
always been built from the setting, so with a repoint in place the box read
`~/.kanibako/state/helpers.jsonl` from the directory you named while kanibako wrote the file to the
default one. The log inside the box was therefore permanently empty. Those two now name one file.

**How a user notices.** Nothing errors, before or after — which is the difficulty. Before the
change your boxes lived at the default location and the setting was decoration; after it, kanibako
looks where the setting says.

**What you must do.** For each workset with one of these keys set:

1. Look under the workset root for a `boxes/` (or `logs/`) directory that the setting says should
   not exist. If it holds your box trees, that is the data kanibako has been using.
2. Stop every box in that workset.
3. Move the contents into the directory the setting names, creating it if needed. For `boxes`,
   move each `<box-name>/` tree whole — it holds the box's home directory. For `logs`, move the
   `<box-name>.jsonl` files, or simply delete them; a helper log is a message record, not state,
   and a new one is created on the next launch.
4. Start a box and confirm it comes up with its files intact.

If you would rather not move anything, the equivalent fix is to delete the key from the
`workset.yaml` — the default is the location your data is already in.

⚑ **Two limits are stated rather than fixed, so you can plan around them.**

- **A standalone box's helper log still ignores a `workset.logs` repoint.** The default for that
  mode is expressed in terms of the box's own directory rather than the workset root, which the
  pre-launch resolver cannot follow; the log stays inside the box's `box_data/`.
- **`kanibako workset rm --purge` does not delete box trees under a `workset.boxes` you pointed
  OUTSIDE the workset root.** The purge deletes the workset root and nothing beyond it, by design —
  it will not remove a directory you nominated elsewhere. Those trees survive the purge and are
  yours to delete by hand.

---

### 2.59 A `run_args` stored as a string now takes effect

`agent.<agent>.run_args` is held in the agent's settings file as a list of words. Two commands
write it, and until now they wrote two different shapes: `kanibako agent set <agent>
run_args="--verbose --debug"` split the line into words, while `kanibako system set
agent.<agent>.run_args="--verbose --debug"` stored it verbatim as one string. The reader took a
list or nothing, so the string was dead text — the command reported success and no launch ever saw
those arguments.

Both commands write the list now, and the reader accepts either shape, so nothing on disk has to
be edited. **The consequence to check for is a string you set once, found had no effect, and
worked around or forgot.** Those arguments start reaching the agent's command line at the next
launch.

```yaml
# agents/<agent>/agent.yaml — what the full spelling used to write.  Ignored by every launch.
self:
  run_args: --verbose --debug

# The same file, unchanged on disk, is now read as the two arguments it spells — and is
# rewritten in the stored shape the next time kanibako writes that file.
self:
  run_args:
  - --verbose
  - --debug
```

**What to do:** run `kanibako agent show <agent>` for each agent you have. Its `run_args` line now
shows what a launch will actually pass, where a string showed no line at all; if what you see is
not what you want, `kanibako agent reset <agent> run_args` removes it. If you added the same flags
somewhere else to compensate — a shell alias, a per-box setting — remove one of the two, or the
agent gets them twice.

⚑ **Splitting is on whitespace, and there is no quoting.** `run_args="--flag 'two words'"` is four
arguments, not two; that is unchanged, and it is why an argument containing a space is written
into the list by hand. ⚑ An explicitly empty `run_args=` means *no arguments* and is still a
different thing from a key you never set: `kanibako agent get <agent> run_args` answers with an
empty line for the first and `(not set)` for the second.

---

### 2.60 `box convert --standalone` recognises a repointed directory as kanibako's

**Read this if you have converted a box to standalone, or intend to.** A standalone box keeps its
live workspace in a subdirectory of the project root, so the convert sweeps everything else at the
root down into it. Everything it must *not* sweep — the box directory, the vault, the canon tree —
it used to recognise by literal directory name.

**What changed.** A name cannot describe a directory you have moved. Three consequences followed,
and only the first needed a repointed setting to appear:

- With `workset.vault_ro` set to `store/ro`, the root held a directory called `store`. That name
  was in no list, so the sweep treated it as your project content and moved it into the workspace.
  The box then opened a vault that was empty. `workset.workspaces` had the same gap.
- `workset.canon` was in no list at all, repointed or not. Converting a box out of standalone and
  back moved the canon tree into the workspace on a completely default layout.
- A setting pointed at an **absolute** path could not be described by a name list under any
  spelling. With `workset.workspaces` pointed outside the root, the convert filled a `workspace/`
  directory the box never opens, and left your files where nothing binds them.

The sweep now resolves `workset.workspaces`, `workset.vault_ro`, `workset.vault_rw` and
`workset.canon` and compares directories, so a repointed one is recognised wherever you put it, and
the workspace is filled at the path the box actually reads.

**How a user notices.** Two new behaviours, both on `box convert --standalone`:

- A directory kept because one of those four settings resolves into it is reported on standard
  error, naming the setting: `Note: left /path/store at the standalone root — workset.vault_ro
  resolves inside it.` The default layout is silent, exactly as before.
- A `workset.yaml` at the root carrying a value kanibako cannot resolve now **stops the convert**
  and names the key, rather than guessing which of your directories to move. The refusal lands
  before anything is copied or moved, so the tree is untouched — fix the key and run the convert
  again.

**What you must do.** Nothing, unless a past convert already displaced a directory. Nothing was
deleted: look in the workspace subdirectory for the directory a `workset.*` setting names, and move
it back up to the project root. Then run `kanibako box show` and confirm the vault and canon paths
point at directories with your files in them.

---

### 2.61 `kanibako agent set` now validates the value you give it

**Read this if you have ever set an agent setting to a value containing `@` or `$`.** Kanibako
checks such a value before writing it: `@config.canon` has to name a real key, `$HOME` has to be a
well-formed variable, and if either fails the command refuses and writes nothing. `kanibako agent
set` skipped that check — it reached the agent's settings file by a route of its own — so it
accepted values that the same setting's other spelling refused:

```
$ kanibako agent set claude canon=@bogus.ref
Set canon=@bogus.ref                          # exit 0, written to the file

$ kanibako system set agent.claude.canon=@bogus.ref
Error: 'agent.claude.canon': dangling @-reference '@bogus.ref' (no such config key
in the keyspace)                              # exit 1, nothing written
```

Both commands give the second answer now.

**What to do.** Nothing on disk changes, and a value with no `@` or `$` in it is unaffected. What
is worth a look is a setting you wrote through `agent set` and never saw take effect: an
unresolvable reference is not an error at launch, it resolves to an empty string, so the box came
up with the setting pointed at nothing. Run `kanibako agent show <agent>` for each agent you have
and re-enter any value carrying an `@` or a `$` — a value that cannot resolve now says so instead
of being accepted.

⚑ **A script that calls `kanibako agent set` can now exit non-zero where it used to exit 0.** The
exit code is the only signal that changed; a value that was already valid behaves exactly as before.

⚑ **`name` is not affected**, because it is not a setting — it is the agent's display name, and
`kanibako agent set <agent> name=…` writes it exactly as it always has.

### 2.63 `box convert` out of standalone reads the box's root instead of counting up from the workspace

**Read this if you have set `workset.workspaces` on a standalone box.** A standalone box is the one
kind whose live workspace is a subdirectory of its root, so converting it to any other mode lifts
those files back up to the root. The directory they were lifted *into* was the workspace's own
parent — which is the root only when `workset.workspaces` is left at its default.

**What changed.** The root is now read off the box (it is where `box_data/` and the root
`workset.yaml` live), so the lift aims at it in every layout. Two things follow, and the second is
the reason this is here rather than only in the changelog:

- With `workset.workspaces` set **one level deeper** (`@meta.workset.path/nested/deep`), the files
  used to land in `nested/` and the box was registered there, one level below the directory you
  converted. They now land at the root. The interposed directory is removed once emptied; if it
  holds anything else of yours, it stays.
- With `workset.workspaces` set to an **absolute path**, the lift emptied that directory, deleted
  it, and left your files loose in its parent — somewhere kanibako was never pointed at. It is now
  kept: a converted box's workspace is simply its project directory, and that directory may be
  anywhere, so nothing has to move. The convert reports the keep and names the setting — `Note:
  left the workspace at /path/work — workset.workspaces pointed it outside /path/root, so it is
  yours and the box keeps it as its project directory.` — and registers the box at that path.

**Also fixed, same command.** `kanibako box convert --standalone --name <new>` on a box that is
*already* standalone renamed it by building a second standalone tree inside the box's own workspace
and then removing the first box's `box_data/` and vault. If you ran that, your files are under a
doubled workspace directory and the box's home is gone; the box that remains is the nested one.
Move the workspace contents back up and re-register with `kanibako box register` if you want the
old root back. A rename now changes the identity and touches nothing else.

**What you must do.** Nothing, unless a past convert already scattered a workspace. Nothing was
deleted except empty directories: look in the parent of the path `workset.workspaces` names, move
the files back into it, and run `kanibako box show` to confirm the workspace path holds them.

---

## 3. For plugin authors

⚑ **THREE PERSONA SURFACES ON `Target` CHANGED SHAPE in 1.8.0 — a plugin built against 1.7.x needs
updating, and one of them fails at IMPORT time:**

- **`probe_verdict` is REMOVED** from `kanibako.targets.base`, replaced by `probe_outcome`. It was
  public in released 1.7.2 and the published `kanibako-agent-claude` 1.7.2 imports it at module
  scope, so an OLD plugin wheel against the NEW base raises `ImportError` out of every command that
  resolves an agent — not just persona ones. This is why the publish ORDER below matters; upgrade
  the plugins with the base.
- **`Target.read_persona_settings`** returns `PersonaReadOutcome` (a tri-state: usable config ·
  present-but-unusable with a named cause · this harness has no persona reader) instead of
  `PersonaSettings | None`.
- **`Target.verify_persona`** returns `PersonaProbeOutcome` (PASS · REJECTED · INCONCLUSIVE ·
  NOT_APPLICABLE, each carrying a reason where one is meaningful) instead of `bool | None`.
  ⚑ Build the probe request WITHOUT a `model` key when the persona names no model, rather than
  declining to probe or substituting a default id: some endpoints do not require one, and a server
  with a hardwired model can reject an id it does not serve.
- **`PersonaSpec.host_dir_adopt` is removed** along with the legacy claude host-dir path (§2.11's
  sibling entry in the CHANGELOG). A `persona:` block that still declares it keeps loading — the
  key is accepted and ignored, deliberately, so a version-skewed wheel does not break.

The three agent plugins (`kanibako-agent-claude`, `-codex`, `-goose`) version and publish
independently of the base and depend on **`kanibako-cli`** with **no version pin**; only the
`kanibako` meta package moves the set together. That makes ordering load-bearing:

1. **At the v1.8.0 (canon) release: publish the three plugins FIRST, then the base.** A new
   base beside an old published plugin delivers the plugin's legacy-only kickoff, which
   resolves nothing — every box's directive chain silently gone. Publishing plugins first
   makes that combination unreachable for anyone tracking releases.
2. **v1.8.0-era plugins keep shipping their kickoff** (`data/KICKOFF.md`, with both the canon
   and legacy imports) and the `managed_pointer` binding. The base's new kickoff bind
   **yields** whenever the resolved target declares a delivery at that destination (keyed on
   the destination, not the key name), so the overlap cannot collide.
3. **The deletion release — the release after v1.8.0:** delete `data/KICKOFF.md` +
   `managed_pointer` from the plugin **and, in the same release, pin `kanibako-cli >= 1.8.0`**
   in the plugin's `pyproject.toml`. The pin is not optional: without it, a plugin-only
   upgrade against an older base ships **no kickoff at all** — total, silent directive loss.
   With the pin, the worst case is a loud pip resolution failure. Only after those releases
   are *published* may the base's transition gate (and the `data/template` fallback arm, item
   4) be removed.
4. **Data layout in the plugin packages (shipped):** the payload dir is now `data/base/`
   — it stamps the whole agent STORE root, of which the template is one entry:
   `data/base/template/box/home/<files>` (was `data/template/<files>`) and
   `data/base/canon/handbook/directives/SYS_AGENT.md` (the plugin's handbook chapter). The
   bible chapter ships at `data/rom/directives/ROM_AGENT.md`, bound at `~/canon/bible/agent`.
   There is **no** transition arm: `templates._packaged_agent_store` reads `data/base` only, so
   a plugin still shipping `data/template` contributes NOTHING — silently, with no error.
   Republish against `data/base`. The `synced` credential destinations becoming host-side paths is
   still open plugin-package work. ⚑ **Correction: the base-side applier does not branch on a
   `dest_space` field** — there is no such field, and its absence is deliberate
   (`settings/settings_categories.py:527`: "THERE IS NO `dest_space` FIELD, AND ITS ABSENCE IS
   THE DESIGN"). The real mechanism is longest-prefix cover over the final bind map
   (`commands/start.py:8659`, `_synced_host_dest`): a `synced` guest dest resolves to a host
   path by finding the bind that covers it, not by consulting a separate namespace field.
5. **Build hygiene:** `rm -rf build/ packages/*/build` before any local wheel build — stale
   `build/lib/` trees ship deleted files (CI builds clean; local builds do not).
6. **`Binding.key`'s user override is now settings-file-only — the type is UNCHANGED.**
   `kanibako.targets.base.Binding` keeps its shape, its fields and its place in the plugin API;
   nothing to port. What changed is the *documentation you give your users*: the override key
   `agent.<name>.bindings.{ro,rw}.<key>` is no longer settable with `kanibako system set` (§2.20).
   It is still a real key — still declared, still beating your descriptor's own source at launch,
   still readable with `kanibako agent get <name> bindings.<ro|rw>.<key>` — so the mechanism your
   `Binding` relies on is intact. ⚑ If your plugin's README or error strings tell a user to run
   `kanibako system set agent.<you>.bindings…`, that instruction now fails; point them at
   `agents/<node>/agent.yaml` instead. There is no CLI verb to substitute, so do not invent one.
7. **BREAKING: `Target.default_category_binds()`, `default_common()` and `default_seeds()` declare
   EVERY category keyed by DESTINATION.** **Before** — one key per entry, carrying the
   entry NAME:

   ```python
   def default_category_binds(self) -> dict[str, BindDefault]:
       return {
           f"agent.{self.name}.bindings.ro.launcher":
               ("@system.cache/launcher", "~/.local/bin/launcher", "ro"),
       }
   ```

   **After** — the category is a TERMINAL key whose whole value is a map keyed by the box
   destination. `agent.<agent>.bindings.{ro,rw}` for an armed category, and plain
   `agent.<agent>.{caches,seeded,common,synced}` for the rest:

   ```python
   from kanibako.settings.settings_resolve import normalize_bind_dest
   from kanibako.targets.base import CategoryBindDefaults

   def default_category_binds(self) -> CategoryBindDefaults:
       return {
           f"agent.{self.name}.bindings.ro": {
               normalize_bind_dest("~/.local/bin/launcher"):
                   ("@system.cache/launcher", "ro"),
           },
       }
   ```

   Four things to carry across:

   - **The entry name is gone**, and all entries of one category live under **one** key — so build
     the map, don't emit a key apiece. A destination is data, not a key segment.
   - **Normalise every destination** with `normalize_bind_dest` (it is idempotent, and it is for
     destinations *only* — never call it on a `host_src`). This is not cosmetic. The arm key is
     matched as a **string** when tables merge, but it is resolved to a real path later, so an
     unnormalised `~/x` is a different key from `/home/agent/x` and the same destination. Two
     consequences: an override written at the canonical spelling — by a user, or by another scope —
     does **not** replace your entry, it becomes a *second* one; and both then resolve to one
     destination at launch, where bindings are act-once and two of them is a hard
     `CategoryCollisionError` ("Two bindings target the same box destination"). You get a named
     launch failure, not a silent double mount — but you get it from the user's machine, not yours.
   - **`common`, `caches`, `seeded` and `synced` moved the same way**, one segment shallower —
     `agent.<agent>.common` → `{box_dest: (host_src[, options])}`. The `BindDefault` type alias
     (the old `(host_src, box_dest[, options])` tuple) is **gone**; `CategoryBindDefaults` is now a
     uniform `dict[str, BindArm]` and `default_common()` / `default_seeds()` return the same shape.
     ⚑ `seeded` and `synced` are still COPIES — the shared shape is about how an entry is written
     down, never about what is done with it.
   - **`kanibako.settings.core_defaults.add_bind` builds the map for you**, for all six: it
     normalises the destination and refuses a second entry at one destination. Use it rather than
     hand-rolling a dict.
   - **A user now overrides one of your entries by its DESTINATION**, since that is the key. If
     your docs tell users how to repoint a bind you declare, the spelling changed.

   **If you do nothing:** the old dotted key is **refused by name** when the launch floor is
   assembled — it is not silently ignored, and there is no shim. ⚑ You will hit it at *import* time
   first: `BindDefault` no longer exists in `kanibako.targets.base`, so an override still annotated
   `dict[str, BindDefault]` fails to import **even if it declares no binds at all**. Change the
   annotation to `CategoryBindDefaults` (or `dict[str, BindArm]`).

   **If you declare binds in your `<agent>-defaults.yaml` `category_binds:` section** (what all
   three first-party plugins do, via `kanibako.settings.agent_defaults.load_category_binds`), you
   get the map shape for free — with one edit: **delete every `key:` line**, from `category_binds:`
   rows and from `common:` rows alike. A `key:` is now refused, naming the file, the category key
   and the destination. It is refused rather than dropped on purpose: ignoring it would let a plugin
   written against the retired contract keep loading while producing a different key than it
   declared. ⚑ A `common:` row's `host_src` keeps its old meaning — the store leaf that gets rooted
   under `@meta.agent.<agent>.path/common/` — and it is now also what tells kanibako which store dir
   a persona shares with its harness, a job the retired `key:` used to do.

8. **BREAKING: `container_env:` moves out of `descriptor:` and becomes the file's top-level `env:`
   section.** A plugin's environment variables are settings keys now
   (`agent.<agent>.env.<VAR>`, §2.34), not a private channel into the container — so they are
   declared where the rest of your agent-scope keys are declared, and a user can override one by
   name.

   **Before** — under `descriptor:`, where nothing in the keyspace could reach it:

   ```yaml
   descriptor:
     container_env:
       MY_AGENT_NO_UPDATE: "1"
   ```

   **After** — a top-level section, beside `common:` and `category_binds:`:

   ```yaml
   env:
     MY_AGENT_NO_UPDATE: "1"
   ```

   ⚑ **A `container_env:` left under `descriptor:` is REFUSED by name**, naming the file and the
   replacement — the same treatment `safe_bypass:` gets, and for the same reason: left unread it
   would load your plugin as an agent whose required variables are silently absent, which is a box
   that misbehaves rather than one that fails.

   **Values must be STRINGS** — quote `"1"` and `"true"`. An unquoted `1` or `true` is refused
   rather than coerced, because YAML would hand you an int or a bool whose `str()` is not what you
   wrote (`True`). `$GUEST_HOME` is expanded by the loader exactly as it is in a `box_dest`.

   **If you build your descriptor by hand** rather than from a defaults file, implement the new
   `Target.default_envs()` — it returns `{"agent.<agent>.env.<VAR>": "value"}`, DISCRIMINATED under
   your own name, the same shape rule `default_common()` follows. A plugin that ships a defaults
   file gets it from `kanibako.settings.agent_defaults.load_envs`, which validates each key against
   the closed keyspace and refuses an illegal variable name by name.

   ⚑ **`PluginDescriptor` has no `container_env` field any more**, so
   `PluginDescriptor(..., container_env={...})` is a `TypeError` rather than a value that quietly
   goes nowhere. `default_envs()` is the only route a plugin has into the box environment; the
   descriptor's `settings` and `access_realization` still realize RESOLVED values onto the env
   channel, which is a different job.

### 3.1 Core module paths moved (package-ification) — the flat compatibility shims are DELETED

v1.8.0 promotes the flat `src/kanibako/*.py` modules into coarse domain subpackages
(`kanibako.settings`, `kanibako.launch`, `kanibako.runtime`, `kanibako.vscode`,
`kanibako.channels`). **The plugin-facing surface — `kanibako.targets.base`, the
`kanibako.agents` entry-point group, and the `kanibako.plugins.*` namespace scan — did not
move**, so a plugin written against the documented interface (`docs/writing-targets.md`) is
unaffected.

Four core modules that the first-party plugins import *did* move, **and the old flat paths are
gone in this release**:

| Legacy path (REMOVED) | New path | Imported by |
|---|---|---|
| `kanibako.vscode_config` | `kanibako.vscode.vscode_config` | claude, codex, goose |
| `kanibako.settings_resolve` | `kanibako.settings.settings_resolve` | claude, codex (`GUEST_HOME`) |
| `kanibako.agent_defaults` | `kanibako.settings.agent_defaults` | claude, codex, goose |
| `kanibako.agent_config` | `kanibako.settings.agent_config` | claude, codex, goose |

⚑ **There is no shim and no deprecation window.** A development build briefly carried re-export
aliases at the four old paths that warned and kept working; **they do not ship.** v1.8.0 is a
deliberate clean break, and an alias that keeps working *is* a deprecation window. Importing a
legacy path now raises `ModuleNotFoundError: No module named 'kanibako.agent_defaults'`.

**⚑ That table is the four modules the FIRST-PARTY plugins import — it is not the whole move.**
Package-ification moved most of the flat `src/kanibako/*.py` tree, so a plugin of your own that
imports any other core module by its flat path (`kanibako.paths`, `kanibako.config`,
`kanibako.settings_launch`, …) breaks the same way and is not listed above. The rule, rather than
the list: **a module that now lives in one of the new subpackages — `kanibako.settings`,
`kanibako.launch`, `kanibako.runtime`, `kanibako.vscode`, `kanibako.channels`, `kanibako.project`,
`kanibako.formats`, `kanibako.proxy` — no longer answers at `kanibako.<name>`.** Import it from its
package. (`kanibako.commands` and `kanibako.targets` were already packages in v1.7.2 and did not
move.) The plugin-facing surface named at the top of this section did not move either, so an
import you break here is one that reached past it.

**Two things went away outright — there is no new path to switch to:**

- **`kanibako.deprecation` is DELETED.** It held a deprecation registry, a `@deprecated` decorator
  and a CI gate. Its registry has been empty since the pre-public clean break, so it had nothing to
  track; a plugin that imports it gets `ModuleNotFoundError: No module named
  'kanibako.deprecation'` and fails to load by name. **Delete the import** — there is no
  replacement, and under v1.8.0's clean-break policy there is nothing for one to do.
- **`StandardPaths.share_ro` / `.share_rw` no longer exist as attributes.** In v1.7.2 they were
  properties that raised `NotImplementedError` naming their replacement; now they raise a plain
  `AttributeError`, so the cure is no longer in the traceback. It is unchanged: the vault
  (`@workset.vault_ro` / `@workset.vault_rw`) and the `common` category — the latter being what
  §2.1's `shared` → `common` rename produced.

#### This bites USERS, not just plugin authors

The plugins declare `dependencies = ["kanibako-cli"]` with **no upper bound**. So *old plugin
beside new core* is not an exotic combination — it is what you get by default if you upgrade
`kanibako-cli` (or install a plugin) without pinning. **These published plugin versions import
at least one removed path and will not load on v1.8.0:**

| Removed module | `kanibako-agent-claude` | `kanibako-agent-codex` | `kanibako-agent-goose` |
|---|---|---|---|
| `kanibako.agent_config` | every `1.7.0` → `1.8.0rc1` | `0.2.0`–`0.3.0` | `0.2.0`–`0.3.0` |
| `kanibako.agent_defaults` | `1.7.0` → `1.8.0rc1` | `0.2.1`–`0.3.0` | `0.2.1`, `0.3.0` |
| `kanibako.settings_resolve` | `1.7.2`, `1.7.2rc3`–`rc5`, `1.8.0rc1` | `0.2.3`–`0.3.0` | — |
| `kanibako.vscode_config` | `1.7.2`, `1.7.2rc3`–`rc5`, `1.8.0rc1` | `0.2.3`–`0.3.0` | `0.3.0` |

`kanibako.agent_config` is the widest: **every** `kanibako-agent-claude` from `1.7.0` through
`1.8.0rc1` imports it. **Clean (unaffected):** `kanibako-agent-claude` `1.8.0.dev95` / `dev98`,
`kanibako-agent-codex` `0.4.0`, `kanibako-agent-goose` `0.4.0`.

**What you see.** kanibako does not die — a plugin that cannot import is skipped by name and
every other agent, plus `kanibako setup`, keeps working:

> `Warning: 'kanibako-agent-goose' failed to load and is being SKIPPED: ModuleNotFoundError: No
> module named 'kanibako.agent_defaults'`
> `  The 'goose' agent is unavailable; every other agent, and 'kanibako setup', still work.
> This usually means the package was built against a different kanibako-cli — upgrade it, or
> uninstall it if you do not use it. Installing the 'kanibako' meta package pins a compatible
> set; see MIGRATION.md for the plugin versions this release breaks.`

**The cure — upgrade the plugin:**

```bash
pip install --upgrade kanibako-agent-claude   # or -codex / -goose, whichever was named
```

or install the **`kanibako` meta package**, which pins a compatible set of all of them:

```bash
pip install --upgrade kanibako
```

A plugin you do not actually use can simply be uninstalled — that clears the warning too.

#### Plugin authors

**Switch to the new paths** — a one-line edit per import site — and in the same release pin
`kanibako-cli >= 1.8.0`, exactly as item 3 requires for the KICKOFF deletion. There is nothing
to be compatible *with*: no version of kanibako-cli offers both spellings, so a floor pin is the
only honest way to say which core your wheel needs.
`tests/test_plugin_import_compat.py` pins this contract (the legacy paths raise, the new paths
import and stay silent, and a stale plugin degrades to the named warning above rather than a
traceback).

**Version numbers.** This release ships as `kanibako-cli` **1.8.0**, `kanibako` (meta)
**1.8.0**, `kanibako-agent-claude` **1.8.0**, `kanibako-agent-codex` **0.4.0** and
`kanibako-agent-goose` **0.4.0**. The plugins version independently and do not adopt the
base's number (codex and goose never have). The KICKOFF-deletion follow-up (item 3) is **the
release after v1.8.0** for each plugin, and must carry the `kanibako-cli >= 1.8.0` floor
pin.

9. **`Target.apply_state()` is REMOVED.** A target used to translate its agent-state values into
   `(cli_args, env_vars)`: claude's turned `model` into `--model <value>`, goose's turned
   `provider` and `model` into `GOOSE_PROVIDER` and `GOOSE_MODEL`. The descriptor does that job —
   a state key reaches the box as the `SettingArg` you declare for it — and core stopped
   dispatching the hook before v1.8.0, leaving a concrete method that returned `[], {}` and that
   nothing called.

   **Nothing breaks at import, and an override is not an error.** Python does not object to a
   method that overrides nothing, so a plugin defining `apply_state` keeps loading and keeps
   working exactly as it does today. ⚑ **That is the hazard, not the reassurance:** if you
   implemented it, your translation has not been reaching the box for some time — silently, with
   no warning and no failed launch — and this removal does not change that. It stops the ABC
   advertising a seam that goes nowhere. The one case that does break is calling
   `super().apply_state(...)`, now an `AttributeError`.

   **Declare the route instead.** In your `<agent>-defaults.yaml`, a `descriptor:` `settings:` row
   names the state key and the channel it travels on:

   ```yaml
   descriptor:
     settings:
       - setting_key: model
         channel: env
         env_var: MY_AGENT_MODEL     # env channel: emitted only when the resolved value is non-empty
       - setting_key: provider
         channel: flag
         flag: ["--provider"]        # flag channel: contributes an argv token
   ```

   The two channels are the whole vocabulary (`Channel.FLAG`, `Channel.ENV`), and the value that
   reaches a row is the RESOLVED one off the settings cascade — so a user can override it by key.
   That is why the translation moved out of plugin code: a value computed inside your target was
   one nothing in the keyspace could name, and therefore one nobody could change.

---

# Migrating to kanibako 1.6.0

> This is a **manual** runbook: 1.6.0 ships **no migration code**. You edit your
> on-disk state from the old (≤ pre-revamp) layout to the new layout *before*
> installing 1.6.0. The sections below describe each old→new change as a set of
> numbered steps with before→after tables and tree diagrams.

1.6.0 is **one breaking change set**. Read the whole document before touching
anything, take a backup of `~/.config/kanibako*` and `~/.local/share/kanibako/`,
then work top-to-bottom.

---

## 0. Quick orientation — what changed

| Area | Old (pre-1.6.0) | New (1.6.0) |
|---|---|---|
| Config files | one `kanibako.yaml` (paths + behavior mixed) | **config** files (`system.*` only) vs **settings** files (behavior), separate |
| Vocabulary | `crab` everywhere | `agent` (the tool) for config/dirs/commands; `crab` only = runtime concept |
| System paths | `system.path.*` | `system.*` (`.path` dropped), restructured |
| Modes | `ProjectMode{default,workset,standalone}` × `ProjectLayout{simple,default,robust}` | `box.mode{primary,named,standalone}`, **no layouts** |
| Primary store | scattered `boxes/`, `comms/`, `share_*/` under data root | **PRIMARY workset** is a real dir at `@system.primary_workset` (superseded in v1.8.0 → `@config.primary_workset`, §2.1) |
| Per-box meta file | `project.yaml` (mode/layout/paths/...) | per-box `settings.yaml` (`[project]` + `[resolved]` sections); all modes (§9) |
| Registry | `names.yaml` + `worksets.yaml` + `connected.yaml` | one `registry.yaml` (`@system.registry`, superseded in v1.8.0 → `@config.registry`, §2.1); see §5 for the section list, also superseded |
| Detection | registry-driven | **on-disk authoritative**, walk-detected, drop-in importable |
| Comm system | single `~/comms/` mount (`mailbox/<box>`, `broadcast.log`) | **channels** — 5 types under `~/channels/` (`mailboxes/<ws>/<box>`, `chat/broadcast.md`) (§7) |
| Templates | shell-variant tree + CLAUDE.md merge + host-config import | **layered seed-once** (base→agent→workset); host-config import **removed** (§8) |
| Per-agent YAML section | `crab:` | `agent:` (§9) |
| Box-side vault dest | `~/share-ro` / `~/share-rw` | `~/vault/ro` / `~/vault/rw` (§4.7, §9) |
| Agent selection | arbitrary auto-pick among installed agents | cascade + installed-count rule; **2+ agents with no choice = error** (§10) |
| Choosing a default agent | `kanibako system config system.default_agent …` | `kanibako setup` / edit the file — `system.*` is file-only (§10) *(superseded in v1.8.0 — see the [v1.8.0 guide](#migrating-to-kanibako-v180))* |
| Targeting a non-cwd box | `refresh -p/--project` | `--box <name-or-path>` (universal); `-p/--project` removed (§10) |

---

## 1. Config vs settings split

Behavior and layout used to live in the same `kanibako.yaml` cascade. They are now
two separate file sets.

### 1.1 CONFIG files — layout only (`system.*`)

`system.*` keys (WHERE things live) are now set **only** in config files:

```
/etc/kanibako/config_base.yaml  defaults (overridable)
~/.config/kanibako.yaml         user global
```

CONFIG precedence: `config_base < ~/.config/kanibako.yaml`.

### 1.2 SETTINGS files — behavior

`agent.*`, `box.*`, `workset.*` and the category keys (WHAT happens) are set **only**
in settings files:

```
/etc/kanibako/settings_base.yaml  defaults
<scope>/settings.yaml             per-scope (system / workset / box)
```

⚑ Since v1.8.0 each per-scope file is named for its own tier — `box.yaml`,
`workset.yaml`, `agent.yaml` — while the system tier keeps `settings.yaml` (§2.45).

### 1.3 The 5-tier settings cascade (box wins)

```
settings_base  <  system  <  agent.<agent>  <  workset  <  box
```

**`box` wins** — it is the top of the cascade.

### 1.4 Migration steps

1. Split your single `~/.config/kanibako.yaml`:
   - Keep all `system.*` keys in `~/.config/kanibako.yaml` (this is now a **config**
     file). Apply the renames in §3.
   - Move all behavior keys (`box.*`, agent settings, scoped shares/seeds, env) into
     a **settings** file at the appropriate scope. Apply the renames in §2 and the
     category mapping below.
2. The old `[system.path]` table and the behavior tables can no longer co-exist in
   one file. Anything that is not `system.*` must leave `~/.config/kanibako.yaml`.

### 1.5 Category keys — the unified path-delivery primitive

The old scoped shares/seeds (`<scope>.path.share_ro/share_rw/seeded.<name>`),
`shared.<name>` caches, and `env.<VAR>` collapse into one category set, available at
every scope (`system` / `agent.<agent>` / `workset` / `box`):

| Old | New category |
|---|---|
| `<scope>.path.share_ro.<name>` | `<scope>.bindings.ro.<name>` (`host_src: box_dest`) |
| `<scope>.path.share_rw.<name>` | `<scope>.bindings.rw.<name>` |
| `<scope>.path.seeded.<name>` | `<scope>.seeded.<name>` (one-time copy at init) |
| `[shared].<name>` (cache dirs) | `<scope>.caches.<name>` — **see ⚑⚑ below** |
| `env.<VAR>` (`.env` files) | `<scope>.env.<VAR>` — **see ⚑ below** |
| `resource.<path>` | **DROPPED** (subsumed by the box/workset category keys) |

Additional categories: `<scope>.masks` (list of box paths to hide via tmpfs),
`<scope>.shared` (rw across all instances in scope), `<scope>.synced` (two-way
cred sync, applied by the agent plugin).

⚑⚑ **THE `[shared]` CACHE TABLE IS RETIRED → the `caches` category.** The old
`[shared]` table carried an *implicit* scope (it meant different things depending
on which file it appeared in). The replacement makes scope **explicit in the key**:

| Old `[shared]` location | Meant | New key |
|---|---|---|
| `[shared]` in the config cascade (`kanibako.yaml` / workset `config.yaml` / `settings.yaml`) | GLOBAL cache, mounted from `shared/global/<name>` | `system.caches.<name>` |
| `[shared]` in an agent file (`agents/<agent>/settings.yaml`, formerly `agents/<agent>.yaml` / `crabs/<agent>.yaml`) | per-agent cache, mounted from `shared/<agent>/<name>` | `agent.<agent>.caches.<name>` |

Two behavioral changes you must account for when hand-migrating:

- **The host source is now SPELLED OUT, not auto-rooted.** The old `[shared]`
  value was only the *box-side* path; the host dir was implicitly
  `<shared-store>/global/<name>` (or `<shared-store>/<agent>/<name>`). A `caches`
  entry names BOTH sides explicitly (`host_src: box_dest`) — there is no longer a
  shared-store dir under which sources are auto-located. Point each cache at the
  real host directory you want mounted.
- **Lazy → guarantee-create.** The legacy `[shared]` block mounted a cache *only
  if the host dir already existed* (a missing dir was silently skipped). The
  `caches` category emitter (rw) **creates a missing source** before binding it.
  If you relied on the lazy "skip when absent" behavior, drop the key instead.

There is **no migration code** — move your `[shared]` entries to `caches` keys by
hand (and migrate any existing on-disk cache dirs yourself if you want to keep
their contents).

⚑ **Claude `plugins` + `cache` MOVED on disk (no migration code).** They were
served from the retired global shared store at `<data>/shared/<agent>/plugins`;
they are now AGENT-scope `agent.claude.shared.{plugins,cache}` entries rooted at
the per-agent store dir — host `<data>/agents/claude/{plugins,cache}`, bound rw
to `~/.claude/{plugins,cache}` in the box. The top-level `<data>/shared/` dir no
longer exists. If you want to keep installed plugins, move
`<data>/shared/<agent>/plugins` → `<data>/agents/claude/plugins` by hand (else a
fresh empty dir is created on next launch).

**Category precedence** (when two categories target the same `box_dest`, later wins):
`seed → cache → binding → shared → synced → masks`. `seed`/`synced` are file copies;
`cache`/`binding`/`shared`/`masks` are mounts. A `synced` and a `binding` naming the
same dest is a **config error** (a copy can't override a live mount).

⚑ **`.env` FILES ARE RETIRED.** Dropping a project `.env` file no longer works.
Move each var to `<scope>.env.<VAR>` in the scope's settings.yaml (superseded in v1.8.0
→ the tier's own file, `box.yaml` / `workset.yaml` / `agent.yaml`, §2.45). Env precedence:
`system < agent < workset < box`, below CLI `-e`.

---

## 2. crab → agent rename

The tool, its config, its directories, and the config-facing commands are now
called **agent**. ("crab" survives only as the runtime concept and as internal
code symbols.) Apply every rename below to your on-disk files and your habits.

### 2.1 Keys

| Old key | New key |
|---|---|
| `box.crab` (alias `crab`) | `box.agent` (alias `agent`) |
| `crab.<agent>.<key>` | `agent.<agent>.<key>` |
| `crab.default.<key>` | `agent.default.<key>` |
| `crab.<agent>.binding.<key>` | `agent.<agent>.binding.<key>` |
| scope `crab` in `crab.path.share_*` / `crab.path.seeded.*` | scope `agent` → now `agent.<agent>.bindings.*` / `.seeded.*` (and see §1.5) |
| settings-cascade level `crab` | level `agent.<agent>` |
| `system.path.crabs` | `system.agents` (drop `.path` and re-spell; see §3) |

### 2.2 Directories

| Old | New |
|---|---|
| `@system.path.data/crabs/` (per-agent store) | `@system.data/agents/` (`<agent>/{plugins,cache,template,...}`) |

### 2.3 Environment / expansion variable

| Old | New |
|---|---|
| `$CRAB` (settings-reference var, = active agent name) | `$AGENT` |

### 2.4 CLI commands

The `crab` command is **CUT**. Its verbs split between `agent` (config) and `box`
(runtime):

| Old command | New command |
|---|---|
| `crab list` | `agent list` |
| `crab info` | `agent info` |
| `crab config` | `agent set` / `agent get` / `agent show` / `agent reset` |
| `crab reauth` | `agent reauth` |
| `crab helper` | `box helper` |
| `crab fork` | `box fork` |
| `crab diagnose` | `box diagnose` |

Update any scripts, aliases, or muscle memory that invoked `crab ...`.

### 2.5 Per-agent settings file moved INTO the store dir

The per-agent settings file (`name`, `run_args`, `model`, `env`, … — the
`agent.<agent>` cascade tier) now lives **inside** the per-agent store directory
as `settings.yaml`, instead of as a sibling file next to it. This makes
`agents/<agent>/` a uniform store dir (alongside `template/`, and the
`plugins/`/`cache/` stores) rather than a `<agent>.yaml` file sitting next to an
`<agent>/` directory.

⚑ **The filename is superseded in v1.8.0 — see §2.45 (line 2774).** The per-agent file
is now `agent.yaml`; only the leaf name changed, so the store-dir move described here is
still the right move. If you are coming to v1.8.0 from pre-1.6.0, run the loop below and
then §2.45's agent rename — a file left as `agents/<agent>/settings.yaml` is not read at
all, and the box comes up as though you had never configured that agent.

| Old | New |
|---|---|
| `@system.data/agents/<agent>.yaml` (sibling file) | `@system.data/agents/<agent>/settings.yaml` (inside the store dir) |

This is the `agent.<agent>.meta.settings` key (= `@agent.<agent>.meta.path/settings.yaml`,
where `@agent.<agent>.meta.path` = `@system.data/agents/<agent>`).

**Manual move (no automatic migration):** for each per-agent file you already
have, move it into the matching store dir, e.g.

```sh
cd "${XDG_DATA_HOME:-$HOME/.local/share}/kanibako/agents"
for f in *.yaml; do
  [ -e "$f" ] || continue
  name="${f%.yaml}"
  mkdir -p "$name"
  mv "$f" "$name/settings.yaml"
done
```

(The default `general.yaml` likewise becomes `general/settings.yaml`.) If you
skip the move, kanibako simply regenerates a fresh default settings file in the
new location and your old `<agent>.yaml` overrides are ignored until moved.

> `agent.<agent>.meta.name` (the plugin's identifier, e.g. `claude`) is now
> REQUIRED — every agent plugin must declare a non-empty name; one that does not
> is rejected at launch rather than silently misbehaving.

---

## 3. `system.*` structural reorg

`system.path.*` becomes `system.*` (the `.path` infix is dropped) and the tree is
restructured. The PRIMARY workset (§4) absorbs the old top-level box/log/vault dirs.

### 3.1 Key renames / new / deleted

⚑ **Superseded in v1.8.0 (see §2.1, line 197).** Five of the "New `system.*`" spellings
below moved again, to a `config.*` prefix (`config.data`, `config.agents`,
`config.registry`, `config.settings`, `config.primary_workset` —
`settings_keyspace.py` `DECLARED_CONFIG_LEAVES`); they are marked below. The rest of
this table (the `system.channelroot`/`system.channels.*`, `system.backup`,
`system.cache`, `system.runtime` rows) is still the live spelling.

| Old `system.path.*` | New `system.*` (1.6.0) | Notes |
|---|---|---|
| `system.path.data` | `system.data` → **`config.data`** | rename only |
| `system.path.crabs` | `system.agents` → **`config.agents`** | + crab→agent (§2) |
| `system.path.comms` | `system.channelroot` | renamed + rebuilt (see §7); type roots under `system.channels.*` |
| `system.path.templates` | `system.base_template` → **`system.template`** | ⚑ **superseded in v1.8.0 — see §2.5 (line 460).** (1.6.0 re-pointed it to `@system.global/base_template`; the live key names a template ROOT, not that file — `@system.global` itself is also gone, see the row below.) |
| `system.path.ws_hints` | `system.registry` → **`config.registry`** | absorbed into the consolidated registry (§5) |
| `system.path.boxes` | **DELETED** | → `@config.primary_workset/boxes` (§4) |
| `system.path.share_ro` | **DELETED** | subsumed by `@workset.vault_ro` / category `shared` |
| `system.path.share_rw` | **DELETED** | subsumed by `@workset.vault_rw` / category `shared` |
| — | `system.backup` | NEW (`@system.data/backup`) |
| — | `system.global` | NEW (`@system.data/global`; holds `settings.yaml`, `registry.yaml`) |
| — | `system.settings` → **`config.settings`** | NEW (`@config.data/global/settings.yaml`, the "system"-tier settings file — `@system.global` does not resolve today; `global` is a literal path segment under `config.data`, not an addressable key: `settings/paths_defaults.py`) |
| — | `system.primary_workset` → **`config.primary_workset`** | NEW (`@system.data/primary_workset`; the PRIMARY workset root) |
| — | `system.cache` | NEW (`$XDG_CACHE_HOME/kanibako`; **not** under data) |
| — | `system.runtime` | NEW (`$XDG_RUNTIME_DIR/kanibako`; helper sockets; **not** under data) |
| — | `system.channels.{commons,chat,broadcast,mailboxes,share}` | NEW sub-keys (detailed in §7) |

Also **deleted from the top level** (now under the PRIMARY workset): `system.boxes`,
`system.logs`, `system.vault_ro`, `system.vault_rw`.

### 3.2 `system.default_agent` (renamed setting)

⚑ **This rename direction is BACKWARDS — superseded in v1.8.0, see §2.1 (line 202).**
The rest of this subsection describes the 1.6.0-era rename (`system.agent` →
`system.default_agent`) as if it were still live. It is not: as of v1.8.0,
**`system.default_agent` is the RETIRED spelling and `system.agent` is the live
key** — the exact reverse of the claim below. `system.default_agent` stored
anywhere is refused with a hard launch error naming
`kanibako system set system.agent=<value>` as the cure
(`settings/settings_assemble.py:77,95-97`). Do not follow the "no automatic
migration" instructions in this subsection; use §2.1's cure instead.

The old default-agent selector `system.agent` is renamed to **`system.default_agent`**
(to avoid the one-character clash with the `system.agents` store directory). It is a
**setting** (behavior), not config — it lives in the settings file set despite its
`system.*` name. `box.agent` falls back to it.

The system tier of these behavior settings now lives in **`global/settings.yaml`**
(`@config.settings`), separate from the `~/.config/kanibako_config.yaml` CONFIG file
(which holds only `system.*` layout/path keys). The `kanibako system get` / `system
show` commands READ / SHOW `system.*` keys (e.g. `system.agent`, `config.data`) but
— as of the W1 overhaul (see §10) — `system set` **refuses to set them**: all
`system.*`-prefixed keys are file-only. Non-`system.` settings (e.g. `model`) stay CLI-settable at the global tier
and are written to `global/settings.yaml`. **No automatic migration:** if you
previously set the default agent in `kanibako_config.yaml`, choose it with
**`kanibako setup`** (which writes it for you) or move the `[agent.default]` table
into `global/settings.yaml` by hand — a stale `[agent]` table in `kanibako_config.yaml`
is no longer read by the system settings tier. See **§10** for the full file-only rule
and the new agent-resolution behavior.

### 3.2a Box-level `[paths]` keys removed

The old box-level `[paths]` config table is gone. None of its keys are settable any
more (`config set paths.* ` is rejected as unknown):

| Old key | Status | Notes |
|---|---|---|
| `paths.shell` | **DELETED** (dead) | nothing read it |
| `paths.vault` | **DELETED** | superseded by the vault bindings (`box.bindings.{ro,rw}.vault`) |
| `paths.shared` | **DELETED** | was the shared-store dir-name leaf. The top-level `<data>/shared/` store is gone entirely (§1.5): per-agent plugins/caches now live under the per-agent store `agents/<agent>/{plugins,cache}` (`agent.<agent>.shared.*`). There is no shared-store dir to rename. |

Remove any `[paths]` table from your config/settings files.

### 3.3 XDG resolution

Every `$XDG_*` default resolves per the freedesktop Base Directory spec: honor the
var iff set **and absolute** (a relative value is ignored → spec default), else:
`XDG_DATA_HOME→~/.local/share`, `XDG_CONFIG_HOME→~/.config`,
`XDG_STATE_HOME→~/.local/state`, `XDG_CACHE_HOME→~/.cache`.
⚑ `XDG_RUNTIME_DIR` has **no** spec default; when unset, kanibako falls back to a
suitable dir and **warns** — it is not silently substituted.

### 3.4 New `@system.data` tree

⚑ **`@system.data` superseded in v1.8.0 — see §2.1 (line 197), now `@config.data`.**
The tree below is otherwise still the 1.6.0-era layout described in this section;
later v1.8.0 moves inside it (e.g. `base_template/` → `template/`, §2.5 line 460, and
the per-tier settings filenames, §2.45 line 2774) are not reflected in the diagram —
the two `settings.yaml` files under `primary_workset/` are now `workset.yaml` and
`box.yaml`, while `global/settings.yaml` keeps its name.

```
$XDG_DATA_HOME/kanibako/
├── global/            ├─ base_template/   ├─ settings.yaml   └─ registry.yaml
├── agents/            └─ <agent>/{plugins, cache, template/}
├── primary_workset/   ├─ settings.yaml  ├─ boxes/<box>/{home,settings.yaml}
│                       ├─ vault/{ro,rw}/<box>/   └─ logs/<box>.jsonl
├── channels/          ├─ commons   ├─ chat/   ├─ mailboxes/<ws>/<box>   └─ share/<ws>/<box>
└── backup/
# siblings OUTSIDE data:  $XDG_CACHE_HOME/kanibako   ·   $XDG_RUNTIME_DIR/kanibako
```

---

## 4. Worksets & modes

The old two-axis model (`ProjectMode` × `ProjectLayout`) becomes a single
three-mode model and **layouts are removed**.

### 4.1 Mode rename

| Old `ProjectMode` / `project.yaml mode` | New `box.mode` | `meta.workset.name` |
|---|---|---|
| `default` (synthesized `__default__` workset) | `primary` | `__PRIMARY__` |
| `workset` (a named workset) | `named` | `<your workset name>` |
| `standalone` | `standalone` | `__STANDALONE__` |

⚑ The old `project.yaml mode="default"` is **not back-read** — there is a hard break.
A pre-existing box keyed to the old vocabulary will not be recognized until you
convert it.

### 4.2 Layouts removed

`ProjectLayout{simple,default,robust}` is **gone**. Remove every `layout:` field
from your on-disk metadata. There is now one fixed per-mode path policy:

- The PRIMARY-mode "vault inside the workspace" arrangements (simple/default) and the
  robust **human-vault symlinks** are removed. Vault is **always** at
  `@workset.vault_{ro,rw}`.

### 4.3 PRIMARY workset is now a real directory

The PRIMARY (formerly "default") workset is no longer virtual. Its boxes, vault, and
logs move out of the scattered data-root locations into one tree under
`@system.primary_workset` (superseded in v1.8.0 → `@config.primary_workset`, §3.1).

**Before** (scattered under the data root):

```
$XDG_DATA_HOME/kanibako/
├── boxes/<box>/{shell, vault, ...}     # box home + maybe in-tree vault
├── comms/
└── share_ro/  share_rw/
```

**After**:

```
$XDG_DATA_HOME/kanibako/primary_workset/   ← @config.primary_workset (= @meta.workset.path)
├── settings.yaml
├── boxes/<box>/{home/ → ~/ , settings.yaml}
├── vault/{ro,rw}/<box>/                    → ~/vault/{ro,rw}
└── logs/<box>.jsonl
# the box WORKSPACE stays external: meta.box.workspace = your real project dir → ~/workspace
```

⚑ **Both `settings.yaml` files above are superseded in v1.8.0 — see §2.45 (line 2774):**
the workset-root file is now `workset.yaml` and the per-box file `box.yaml`.

Move each primary box's home dir to `primary_workset/boxes/<box>/home/`, its vault to
`primary_workset/vault/{ro,rw}/<box>/`, and its log to `primary_workset/logs/<box>.jsonl`.

**Per-box state-dir leaf renamed `shell` → `home`.** The on-disk per-box state
directory is now `boxes/<box>/home/` (was `boxes/<box>/shell/`) for PRIMARY and NAMED
boxes, matching STANDALONE (which already used `home`). No auto-migration: manually
`mv boxes/<box>/shell boxes/<box>/home` for each existing box. (The persisted
`box.shell` *setting* — the login shell — and the stored-path `shell` metadata key are
unchanged; only the directory leaf moved.)

### 4.4 NAMED workset layout

```
~/code/<wsname>/               ← meta.workset.path
├── registry.yaml              ← box membership ONLY (v1.8.0; see §2.43)
├── settings.yaml              ← meta.workset.settings (OPTIONAL in v1.8.0)
├── boxes/<box>/{home/ → ~/ , settings.yaml}
├── workspaces/<box>/          → ~/workspace
├── vault/{ro,rw}/<box>/       → ~/vault/{ro,rw}
└── logs/<box>.jsonl
```

⚑ **Both `settings.yaml` files above are superseded in v1.8.0 — see §2.45 (line 2774):**
the workset-root file is now `workset.yaml` and the per-box file `box.yaml`.

**NAMED workset vault path order changed `vault/<box>/{ro,rw}` → `vault/{ro,rw}/<box>`.**
The ro/rw split now nests ABOVE the box name, matching PRIMARY and STANDALONE. No
auto-migration: for each named-workset box, move `vault/<box>/ro` → `vault/ro/<box>` and
`vault/<box>/rw` → `vault/rw/<box>`.

**NAMED workset name uniqueness:** a workset name is now a user-typed shared address
and must be unique. On a collision at create/import time, kanibako **refuses** (it
does not auto-suffix). The names `__PRIMARY__` and `__STANDALONE__` (and legacy
`default`) are **reserved** and cannot be used.

**Workset files consolidated `workset.yaml` + `config.yaml` → one `settings.yaml`.**
A NAMED workset previously kept its identity/marker/project-list in a
`<root>/workset.yaml` and its cascade settings (image, `agent.*`, `workset.bindings.*`,
`standalone`, `enable_vault`) in a separate `<root>/config.yaml`. Both now live in a
**single `<root>/settings.yaml`** = `@meta.workset.settings`, mirroring a box's
`settings.yaml` which carries `meta.box.*` alongside its settings:

| Old file → key | New location |
| --- | --- |
| `<root>/workset.yaml` (`name`, `created`, `group_auth`, `projects`) | `<root>/settings.yaml` under `workset.meta.*` (superseded in v1.8.0 → `<root>/registry.yaml`, §2.43) |
| `<root>/config.yaml` (`box.*`, `agent.*`, `workset.bindings.*`, `standalone`, `enable_vault`) | `<root>/settings.yaml` (top level — unchanged key shapes) |

The identity lives under the `workset.meta` table so it never collides with the
cascade-settings tables in the same file; the settings readers ignore `workset.meta`
and the identity reader ignores everything else. **Detection** of a drop-in/unregistered
NAMED workset root now keys on `<root>/settings.yaml` carrying a `workset.meta` identity
(was: presence of `workset.yaml`). No auto-migration: for each existing NAMED workset,
fold `workset.yaml`'s keys under a `workset:`→`meta:` table in `settings.yaml` and merge
the old `config.yaml` keys in at the top level, then remove both old files. (The
default/synthesized workset is unaffected: its `group_auth` still lives in the
data-root `config.yaml` `[project]` section, which is the system/default config file,
not a NAMED-workset file.)

⚑ **`workset.meta` superseded in v1.8.0 — see §2.43.** The identity does not live in
`settings.yaml` at all any more; it is the `workset:` table of `<root>/registry.yaml`, and a root
left on the 1.6.0/1.7.x shape **hard-refuses**. If you are coming from a `workset.yaml`, skip the
fold described above and go straight to §2.43: put `name` / `created` / `projects` into
`registry.yaml` and merge only the old `config.yaml` keys into `settings.yaml`.

⚑ **And that settings file is itself `workset.yaml` in v1.8.0 — see §2.45 (line 2774).**
It carries the same name as the retired pre-1.6.0 identity file but is a different file:
it holds the old `config.yaml` keys only, never `name` / `created` / `projects`, which go
to `registry.yaml`. A NAMED workset's per-box files become `box.yaml` at the same time.

### 4.5 STANDALONE layout & identity

Standalone metadata moves from the in-tree `.kanibako`/`kanibako` dotdir to the
project root: the box `settings.yaml` lives **at the root**, the live workspace is
a **`workspace/` subdir** (NOT the root itself), and a `box_data/` marker dir
holds the agent home + the helper log.

**Before**:

```
~/scratch/myproj/
├── .kanibako/   (or kanibako/)   ← metadata dotdir
└── ...                           ← project files mounted directly as ~/workspace
```

**After**:

```
~/scratch/myproj/             ← @meta.workset.path  (meta.workset.name: __STANDALONE__)
├── settings.yaml             ← meta.box.settings   (box metadata; AT THE ROOT)
├── workspace/                → ~/workspace          (a SUBDIR, not the root)
├── box_data/                 ├─ home/ → ~/          └─ <box.name>.jsonl   (helper log)
└── vault/{ro,rw}/            → ~/vault/{ro,rw}
```

⚑ **Every `settings.yaml` in this subsection is superseded in v1.8.0 — see §2.45 (line
2774), and read the standalone row there before you act on this one.** A standalone
project's ROOT file is the WORKSET tier and is now `workset.yaml`; the box file is
`box_data/box.yaml`. §2.45 calls the standalone row the one people misread, because the
root file and the box file were indistinguishable while both were called
`settings.yaml`.

⚑ **Two changes from earlier 1.6.0 dev builds (drift H + I):**

- **`settings.yaml` moved from `box_data/settings.yaml` to `<root>/settings.yaml`.**
  The `box_data/` directory is now ONLY the marker dir + home + helper log; the box
  metadata file is at the project root, alongside `workspace/` and `vault/`.
- **The workspace is now a `<root>/workspace/` subdir, not the project root.** The
  root holds the kanibako artifacts (`settings.yaml`, `box_data/`, `vault/`); your
  actual project files live under `workspace/` (mounted as `~/workspace`).

⚑ **The standalone walk marker is now a `box_data/` directory PLUS a
`<root>/settings.yaml`** — presence alone, not any field inside the file. (A
NAMED workset root also carries `<root>/settings.yaml`, but with a
`workset.meta` identity — superseded in v1.8.0: the identity moved to
`<root>/registry.yaml` and the settings file became optional, §2.43 — and NO
`box_data/` dir, so the two never collide.) The
old in-tree `.kanibako`/`kanibako` dotdir marker is gone. When hand-editing a
standalone tree, place `settings.yaml` at the root, keep a `box_data/` dir
beside it, and put your files under `workspace/`. Drop any `layout:` field —
and drop `mode` too: nothing writes or reads a `mode` token on a standalone
box's `settings.yaml` (`launch/box_resolve.py:standalone_settings_present`
tests presence only, deliberately not `project.mode`; see §9.2 for the full
`project:`-table correction).

⚑ **No automatic migration (pre-public):** there is no on-disk migrator. To move a
pre-existing standalone tree to the new shape by hand: move `box_data/settings.yaml`
up to `<root>/settings.yaml`, create `<root>/workspace/` and move your project files
into it (leaving `settings.yaml`, `box_data/`, and `vault/` at the root), and keep
the `<box>.jsonl` helper log inside `box_data/`.

**Standalone box identity** is now `<random24>_<leaf>` — a 24-bit random token plus a
sanitized, length-capped leaf of the project dir name (e.g. `a1b2c3_myproj`). The
random token is regenerated on a whole-name collision. Standalone boxes are now
**registered** in `registry.yaml` (a `standalone` section), where they previously
were not.

### 4.6 `project.yaml` → per-box `settings.yaml`

The old per-project `project.yaml` (mode/layout/workspace/shell/vault_ro/vault_rw/
group_auth/metadata/...) is replaced by a per-box **`settings.yaml`** in **every**
mode. Its on-disk shape (the `[project]` + `[resolved]` sections it actually carries)
is detailed in §9 — read that section before hand-editing it. (Both filenames are
superseded in v1.8.0, which names each settings file for its own tier — `box.yaml` for
the per-box file, `workset.yaml` for a standalone root: §2.45, line 2774.)

Drop `layout` entirely; translate `mode` per §4.1; the path fields are derived from
the fixed per-mode tables, not user-edited. (Where the file lives: primary →
`@config.primary_workset/boxes/<box>/settings.yaml`; named →
`<wsroot>/boxes/<box>/settings.yaml`; standalone → `<root>/settings.yaml` (at the
project root, NOT inside `box_data/` — see §4.5).)

### 4.7 Box-side vault path moved: `~/share-ro` / `~/share-rw` → `~/vault/ro` / `~/vault/rw`

⚑ **User-visible box-layout break.** Inside the box, the vault is now mounted at
`~/vault/ro` (read-only) and `~/vault/rw` (read-write) — the keyspace §2c box dests.
Previously it was mounted at the legacy `~/share-ro` / `~/share-rw`. The host-side
vault SOURCE (`@workset.vault_{ro,rw}`) is unchanged.

⚑ The old in-workspace vault MASK (`~/workspace/vault`, an unconditional read-only
tmpfs) is **REMOVED** in 1.6.0. It existed only to hide the vault back when the vault
lived inside the workspace; now that the vault lives outside the workspace there is
nothing in `~/workspace` to mask, so no mask is applied by default. Boxes can still
declare explicit tmpfs masks via the `box.masks` (or `<scope>.masks`) category.

Any in-box scripts, aliases, or agent instructions that reference `~/share-ro` /
`~/share-rw` must be updated to `~/vault/ro` / `~/vault/rw`. (Host-side snapshot
tooling — `kanibako vault snapshot/restore` — is unaffected; it operates on the
host vault rw directory, not the box dest.)

### 4.8 Helper message log relocated: shared `data/logs/<id>/` → per-box, per-mode

The per-box helper message log (the host source of the read-only
`$XDG_STATE_HOME/kanibako/helpers.jsonl` bind inside the box) previously landed in a
SINGLE shared host location for every mode:
`@system.data/logs/<box-or-hash>/helper-messages.jsonl`. It now lives inside each
box's own workset/box tree, one file per box:

| Mode | New host helper-log path |
|---|---|
| PRIMARY | `@system.primary_workset/logs/<box>.jsonl` (superseded in v1.8.0 → `@config.primary_workset/...`, §3.1) |
| NAMED | `@meta.workset.path/logs/<box>.jsonl` |
| STANDALONE | `@meta.workset.path/box_data/<box>.jsonl` |

The box-side dest is unchanged (`$XDG_STATE_HOME/kanibako/helpers.jsonl`, read-only),
and the in-box `kanibako box helper log` command is unaffected. NAMED worksets now get
a `logs/` dir created at workset-creation time (alongside `boxes/`, `workspaces/`,
`vault/`). The shared top-level `data/logs/` tree (and the `.../helper-messages.jsonl`
filename) is gone. No auto-migration: the log is regenerated on the next launch; to
preserve history, move `data/logs/<id>/helper-messages.jsonl` to the per-mode path
above (renaming it `<box>.jsonl`). The standalone log now stays inside `box_data/`,
so a moved/imported standalone tree carries its helper log with it.

---

## 5. Registry consolidation

The separate name/registry stores merge into one `registry.yaml` at
`@config.registry` (`@config.data/global/registry.yaml` today; `@system.global`
does not resolve — see §3.1, the `system.global` row, line 2931).

⚑ **Superseded in v1.8.0.** The table below reflects the 1.6.0-era merge; the
sections have moved again since. The canonical, current section list is
`("worksets", "standalone", "deregistered", "rigs", "image_shells")`
(`project/registry_store.py:85-91`):

- **`projects:` is RETIRED** — it is dropped from the file on the next save, not
  carried forward. Default-mode ("primary") box identity now lives in the
  PRIMARY workset's own per-workset `boxes:` membership, not in a top-level
  registry section.
- **`connected:` is GONE.** What it tracked is now the same per-workset `boxes:`
  entry mentioned above — there is no separate top-level section for it.
- **`workset_roots` is not a separate section.** Name→root resolution folds into
  `worksets:` itself.
- The table below also omits the live **`deregistered:`** section (present in
  every current registry).

| Old file | New `registry.yaml` section (1.6.0 — see correction above for the current set) |
|---|---|
| `{data}/names.yaml` `[projects]` | `projects:` |
| `{data}/names.yaml` `[worksets]` | `worksets:` |
| `{data}/worksets.yaml` (== `ws_hints`) | `worksets:` / `workset_roots:` (name→root) |
| `{data}/connected.yaml` | `connected:` |
| (standalone boxes — previously unregistered) | `standalone:` (NEW) |
| `{data}/rigs.yaml` | `rigs:` |
| `{data}/image-shells.yaml` | `image_shells:` |

Steps:

1. Create `@config.data/global/` if it does not exist.
2. Merge the contents of `names.yaml`, `worksets.yaml`, `connected.yaml`,
   `rigs.yaml`, and `image-shells.yaml` into the appropriate sections of
   `@config.data/global/registry.yaml`.
3. Remove the old `names.yaml` / `worksets.yaml` / `connected.yaml` /
   `rigs.yaml` / `image-shells.yaml`.

The registry is now a **derived, rebuildable index** — losing it no longer orphans
boxes (see §6). On purge, names are now unregistered (no dangling entries), and a
same-name convert reuses the existing name instead of auto-suffixing.

---

## 6. Drop-in detection & import (NEW behavior)

On-disk metadata is now **authoritative**; the registry is just a rebuildable index.
All three modes are **drop-in importable purely from their on-disk layout** — ⚑ but
"self-describing" overstates it for STANDALONE identity specifically: a standalone
box's on-disk `settings.yaml` no longer carries its own name (P8b deleted the stored
`project.name`, along with the rest of the `project:` table — see §9.2). Its identity
is instead COMPOSED at import time from the stored `workset.kuid` plus the live
directory leaf (`project/import_reconcile.py`,
`launch/box_identity.py:compose_standalone_name`), not read verbatim off disk.

What this means for you:

- **Detection is an ancestor-walk**, not a registry lookup. Standalone is detected by
  walking up for a `box_data/` dir + a root `settings.yaml` (in v1.8.0 that root file is
  `workset.yaml`, §2.45 line 2774) — presence only, no
  `mode` field is read (§4.5); named by a workset root's four-directory skeleton
  (`boxes/`, the workspaces dir, `vault/`, `logs/`) — also presence only in v1.8.0,
  where a workset root carries no identity table at all (§2.43); primary by
  reconciling the central boxes dir against the registry.
- **A detected workset takes its DIRECTORY's name** (v1.8.0). Nothing under the root
  records a name any more, so the import uses the root's directory basename — the same
  default `workset create` applies when you give it a path and no `--name`. Rename the
  directory before you move it if you want a different name.
- **You can move or copy a box/workset/project tree** to a new location or machine and
  kanibako re-discovers it.
- **Import is automatic with an alert, no confirmation.** When kanibako finds an
  on-disk entity that is not in the registry, it registers it, tells you it was
  imported, and proceeds.
- **Name collision = refuse.** If an import's name collides with an
  already-registered entity **of the same kind** — a workset against a workset, a box
  against a box — kanibako refuses, leaves the tree untouched, and prints a clear
  error. (A future `rename` mechanism — not in 1.6.0 — will resolve collisions.)
  ⚑ A workset name colliding with a primary BOX name is a different case and does
  **not** refuse: the import proceeds and warns. Bare-name resolution prefers the box,
  so reach the workset as `kanibako workset <cmd> <name>`.

Practical upshot for migration: after you have hand-moved trees into the new layout
(§3–§5), you do **not** strictly need to hand-edit the registry for every box — a
correctly laid-out tree will be detected and imported on access. The registry merge in
§5 is still recommended so existing names/roots are preserved and collisions are
avoided.

---

## 7. Channels (the comm-system rebuild)

The single legacy `comms` mount is replaced by the **channels** system: 5 channel
types across 2 scopes (system + workset), surfaced in-box under `~/channels/` and
`~/channels/workset/`, with per-instance partitioning keyed by the workset name.

### 7.1 Key + path renames

⚑ **`system.channels.commons` superseded in v1.8.0 — see §2.3 (line 326).** The
channel itself was renamed again, `commons` → `common`, on both host paths and
the settings key.

| Old `system.path.comms` | New `system.channelroot` + `system.channels.*` |
|---|---|
| `system.path.comms` (one dir) | `system.channelroot` (`@config.data/channels`) — ROOT-path leaf; sub-keys below under the `system.channels.*` branch |
| — | `system.channels.commons` (`@system.channelroot/commons`) — see the ⚑ above, now `system.channels.common` |
| — | `system.channels.chat` (`@system.channelroot/chat`; dir of `*.md` logs) |
| — | `system.channels.broadcast` (`@system.channels.chat/broadcast.md`) |
| — | `system.channels.mailboxes` (`@system.channelroot/mailboxes`; partitioned `/<ws>/<box>`) |
| — | `system.channels.share` (`@system.channelroot/share`; partitioned `/<ws>/<box>`) |

(The `system.path.comms` → `system.channelroot` rename is also listed in §3.1; this
section details the sub-keys and the in-box layout. The root leaf is `channelroot`
so the key is a scalar XOR a subtree — the type roots live under `system.channels.*`.)

### 7.2 The 5 channel types

| Type | Owner | Other-box perms\* | Where (host) |
|---|---|---|---|
| **Mailbox** | a box | write-only\* | system `mailboxes/<ws>/<box>` |
| **Share** | a box | read-only\* | system `share/<ws>/<box>` + workset `channels/share/<box>` |
| **Commons** | a scope | read-write | `commons/` (system + workset) — renamed `common/` in v1.8.0, §2.3 |
| **Chat** | a scope | read-append\* | `chat/*.md` (system + workset); default `general.md` |
| **Broadcast** | a scope | read-append\* | `chat/broadcast.md` (system + workset) |

\* Permissions are **by convention, not enforced** in 1.6.0 — every channel is
read-write-mounted. Any box can technically read or overwrite any other box's
mailbox/share/commons/chat. This is the deliberate single-operator box↔box trust
stance; box↔HOST isolation is unaffected. (Future helper-mediated enforcement will
tighten the write paths without moving the in-box paths.)

### 7.3 In-box layout: `~/comms/` → `~/channels/`

⚑ **`commons/` superseded in v1.8.0 — see §2.3 (line 326).** Every `commons/` path
in the diagram below (both the system and workset trees) is `common/` as of
v1.8.0.

**Before** (single mount):

```
~/comms/
├── mailbox/<box>/      ← flat, keyed by box name
└── broadcast.log       ← top-level, .log
```

**After** (the channels tree):

```
~/channels/                      ~/channels/workset/   (primary/named only; standalone OMITS)
├── commons/                     ├── commons/
├── chat/                        ├── chat/
│   ├── general.md               │   ├── general.md
│   └── broadcast.md             │   └── broadcast.md
├── share/                       └── share/
├── mailboxes/<ws>/<box>/
└── inbox/                       ← own mailbox alias (== mailboxes/<ws>/<self>)
```

⚑ Three structural breaks to migrate by hand:

1. **`~/comms/mailbox/<box>/` → `~/channels/mailboxes/<ws>/<box>/`.** Mailboxes are
   now plural (`mailboxes/`) and **partitioned by workset name** first. `<ws>` is
   the workset-name token: `__PRIMARY__` (primary mode), the workset name (named
   mode), or `__STANDALONE__` (standalone). Move each old `mailbox/<box>` dir to
   `mailboxes/<ws>/<box>` under the host channels root. (Workset-name uniqueness —
   §4.4 — is what makes the `<ws>` partition unambiguous.)
2. **`broadcast.log` → `chat/broadcast.md`.** Both a **location** change (now inside
   the `chat/` dir) and a **format** change (`.log` → `.md`). Move the old
   `broadcast.log` content into `chat/broadcast.md`.
3. **Own inbox.** A box's own mailbox is additionally surfaced at `~/channels/inbox`
   (the same host dir as `~/channels/mailboxes/<ws>/<self>`). No migration needed —
   it is created on box launch.

The host roots live under `@system.channelroot` (system scope) and
`<wsroot>/channels` (workset scope, primary/named only). The Share type also has a
**system** publication dir (`share/<ws>/<box>`) and, for primary/named boxes, a
**workset-local** one (`channels/share/<box>`).

### 7.4 Box-side helper socket / log dest (XDG-aware)

> ⚑ **Superseded in v1.8.0 — see §2.22.** The XDG-awareness described below was reverted:
> both destinations moved to the fixed `~/.kanibako/state/`, and the XDG location is served
> from there by a symlink made after the box boots. The filename change
> (`helper-messages.jsonl` → `helpers.jsonl`) still stands. If you are upgrading past
> v1.8.0, do **not** follow this section's advice to derive the path from `$XDG_STATE_HOME`
> — read §2.22 instead.

The in-box helper socket and message-log destinations are now XDG-aware (they
honor `$XDG_STATE_HOME` if it is set and absolute, else fall back to
`~/.local/state`):

| Old (hardcoded) | New (XDG-aware) |
|---|---|
| `/home/agent/.local/state/kanibako/helper.sock` | `$XDG_STATE_HOME/kanibako/helper.sock` |
| `/home/agent/.local/state/kanibako/helper-messages.jsonl` | `$XDG_STATE_HOME/kanibako/helpers.jsonl` |

The in-box message-log filename changed `helper-messages.jsonl` → `helpers.jsonl`.
The **host-side** log filename (`<box>.jsonl`, under the workset/box logs dir) is
unchanged. In-box tooling/scripts that referenced the old literal socket/log paths
should use the XDG-derived path. No host-side hand-migration is required — these are
recreated per box launch.

The **host-side** helper socket basename is now `<box>-<ws>.sock` (was `<box>.sock`),
where `<ws>` is the workset-name token (`__PRIMARY__` / `<named>` / `__STANDALONE__`),
so a project name reused across worksets gets a distinct socket. It lives under
`@system.runtime` and is recreated per launch — no hand-migration needed.

### 7.5 Move / convert relocates the owning box's partition (best-effort)

A box's mailbox/share partition key is the workset name, so moving a box between
worksets (or converting between modes) changes its channel address. `box move` /
`box convert` now relocate the box's **own** mailbox + system-share partition to the
new address on a **best-effort** basis. Stale cross-box references to the box's old
address may break — there is **no forwarding marker**. Scope-owned channels
(commons/chat) are not relocated; the box simply stops mounting the old workset's
local channels and starts mounting the new one's.

---

## 8. Templates & host-config removal

Three overlapping ad-hoc seeding mechanisms (the shell-variant template, the
CLAUDE.md instruction merge, and the per-agent host-config import) collapse into
**one layered seed-once** model. This is the headline behavior break in this
section: **your host agent config no longer flows into boxes.**

### 8.1 Layered seed-once template

⚑ **`@system.base_template` superseded in v1.8.0 — see §2.5 (line 460).** The key
is retired; the live key is `system.template`, and it now names a template
**root** two levels up from the box-home seed (`global/template/box/home/`), not
the box-home dir directly.

On box creation, three template layers are copied into the box home `~/` in order
(later overlays earlier; absent layers are skipped), **once** — never re-seeded, so
any edits you make inside a box afterward survive:

```
1. base    @system.base_template          → ~/    (always)
2. agent   @agent.<agent>.template         → ~/    (= @system.agents/<agent>/template; if box.agent set)
3. workset @workset.template               → ~/    (= <wsroot>/template; optional, primary/named only)
```

Per-file rule: plain ordered copy, **last layer wins**, seed-once. There is **no
per-file merge of any file** (see the CLAUDE.md change below).

### 8.2 Content moves (a rename, not a loss)

⚑ **`@system.base_template` superseded in v1.8.0 — see §2.5 (line 460).** Same
correction as §8.1 above: the live key is `system.template`, naming the template
root, not the flat box-home dir this row implies.

| Old on-disk content | New location |
|---|---|
| `templates/<agent>/standard/*` | `@agent.<agent>.template` (= `@system.agents/<agent>/template`) |
| `templates/general/{base,standard}/*` | `@system.base_template` (flat — no `general/`, no variant subdir) |

Hand-move your existing template content accordingly. The base template is now
**flat** (no `general/base` vs `general/standard` split).

### 8.3 Shell-variant selector dropped

The template-variant selector (`crab.shell` / `template_name`, default `"standard"`)
is **gone**. There is one fixed `@agent.<agent>.template` per agent — no variant
subdirectory. `box.shell` now means **only the login shell**. (If a need for
variants reappears it can return later as a creation-time `box create --template
<variant>` flag, not a cascade setting.) Remove any `shell:`/`template_name:`
variant key from your agent config.

### 8.4 CLAUDE.md is now a plain template file

The instruction-merge machinery (section-marker concatenation of base / template /
project layers of `CLAUDE.md`) is **deleted**. `CLAUDE.md` is now an ordinary
template file: plain ordered copy, last-wins, seed-once. Base-layer guidance lives
in a separate non-colliding file (`INSTRUCTIONS.md`) so it never clobbers the agent
template's `CLAUDE.md`. If you relied on the merge markers, fold your content
directly into the appropriate template-layer `CLAUDE.md`.

### 8.5 ⚑ Host-config IMPORT removed (the headline break)

kanibako no longer copies your **host** agent config into a fresh box. Each agent's
import is removed:

| Agent | Host source no longer imported | What replaces it |
|---|---|---|
| claude | `~/.claude.json` (the `oauthAccount` / `hasCompletedOnboarding` / `installMethod` allowlist) | a curated static `.claude.json` onboarding stub (`{"hasCompletedOnboarding": true}`) in the claude template + your synced `~/.claude/.credentials.json` |
| codex | `~/.codex/config.toml` | a curated `config.toml` in the codex template (or codex's built-in defaults) |
| goose | `~/.config/goose/config.yaml` (the provider/model/**extensions**/**instructions** allowlist) | provider/model via the `agent.goose.env.GOOSE_PROVIDER` / `GOOSE_MODEL` settings; extensions/instructions = the **curated goose template set** |

What you must now do instead:

- **claude:** authentication flows from the **synced** `~/.claude/.credentials.json`
  (still synced every launch) plus the template's onboarding stub. Your host
  `~/.claude.json` settings (including `oauthAccount`) no longer seed the box.
- **goose:** set provider/model as settings (`agent.goose.env.GOOSE_PROVIDER`,
  `agent.goose.env.GOOSE_MODEL`) rather than relying on your host `config.yaml`.
  ⚑ **Your host goose `extensions` and `instructions` are NOT carried into boxes** —
  this is an accepted loss; boxes use the template's curated set. Place any extension
  config you want in boxes into the goose template (`@agent.goose.template`).
- **codex:** codex runs on built-in defaults; place any custom config in the codex
  template (`@agent.codex.template`).
- **any agent:** to ship custom per-agent config into boxes, put it in that agent's
  template dir (`@system.agents/<agent>/template`) — it seeds via layer 2.

**Credential SYNC** (separate from host-config import): claude `.credentials.json`,
codex `auth.json`, and goose `secrets.yaml` are two-way synced on every launch.

> **Update (post-1.6.0):** goose additionally two-way syncs `~/.config/goose/config.yaml`
> and `~/.config/goose/custom_providers/`. This is *not* the removed host-config
> import (which seeded your host config into a fresh box at init). Instead, when you
> run `goose configure` **inside a box**, the provider/model selection and any custom
> provider definition now persist back to the host so the host-side auth check sees a
> configured goose on the next start (no repeated `goose configure` prompt) — parity
> with how claude/codex write their config back. The values these files reference are
> env-var *names*; the secret value still lives only in `secrets.yaml`.

---

## 9. Agent descriptors, the per-box meta file & box-side vault dest

The agent descriptor model is finalized in 1.6.0; most of it is internal (the
plugin contract). The user-visible pieces are three on-disk / box-layout changes.

### 9.1 Per-agent YAML section `crab:` → `agent:`

⚑ **Superseded in v1.8.0 — see §2.37 (line 2094).** The top-level section token
renamed from `crab` to `agent` at 1.6.0, as shown below, but that is no longer
the file's root spelling either: v1.8.0 renamed it again, to **`self:`**
(`settings/agent_file.py:36`). Renaming a `crab:` section to `agent:` today still
leaves the file unrecognized — go straight to `self:`, and read §2.37 for the
full current shape (every category flat under one `self:` level).

```yaml
# 1.6.0-era                      # superseded again in v1.8.0
crab:                            self:
  model: opus                      model: opus
```

⚑ **Hard break, no back-read.** A file with a `crab:` **or** `agent:` section is
not recognized until you rename the section to `self:`. (This is in addition to
the cascade-level and key renames in §2.)

### 9.2 Per-box meta file `project.yaml` → `settings.yaml`

The per-box metadata file is renamed `project.yaml` → **`settings.yaml`** in **every**
mode (primary, named, and standalone). See §4.6 for where each mode's file lives;
§4.5 for the standalone walk marker (`box_data/` dir + `<root>/settings.yaml`).
(Both filenames are superseded in v1.8.0, which names each settings file for its own
tier — `box.yaml` for the per-box file, `workset.yaml` for a standalone root: §2.45.)

⚑ **What the file actually contained at 1.6.0 (on-disk format, now superseded — see the
correction below).** At 1.6.0 the per-box `settings.yaml` stored construct-time box
metadata in two YAML sections, `project:` and `resolved:` — the *physical* on-disk
shape you would have seen if you opened the file. (The keyspace documented this
metadata as the logical `meta.box.*` / `meta.workset.*` model; the on-disk layout used
these two sections rather than nested `meta.box.*` tables.)

```yaml
project:
  mode: primary            # primary | named | standalone  (was project.mode; NO layout field)
  enable_vault: true
  group_auth: true
  name: <box name>
resolved:
  workspace: <project dir>
  shell: <login shell>
  vault_ro: <host vault ro path>
  vault_rw: <host vault rw path>
  metadata: <metadata path>
  project_hash: <hash>
```

🛑 **CORRECTION (as of 1.8.0): this shape is DEAD, not a target to "keep."** The
identity/construct-time rework since 1.6.0 (P8b/P8c) deleted the readers AND writers of
the `project:` / `resolved:` sections outright (`read_project_meta` /
`write_project_meta` no longer exist). **Nothing in the current code reads a `project:`
table, ever, for any of its four members:**

| Old field (`project:` table) | Current reality |
|---|---|
| `mode` | not read; identity comes from the registry + on-disk layout walk, never a stored mode field (`launch/box_resolve.py`) |
| `enable_vault` | **not read** — `box.enable_vault` (§2b) is sparse-written/read from a `box:` table only (`settings/config.py` `write_box_enable_vault`/`read_box_enable_vault`). **A stored `project.enable_vault: false` is silently ignored and the box launches with the vault ENABLED** — move it by hand, do not leave it under `project:`. |
| `group_auth` | not read; superseded by `box.auth.global_enabled` / `box.auth.workset_enabled` (§2b, 2026-07-01) |
| `name` | not read; a standalone box's identity is composed `<kuid>_<dir-leaf>` from the stored `workset.kuid` plus the live directory name, never from a stored `project.name` (`project/import_reconcile.py`, `launch/box_identity.py:compose_standalone_name`) |

The `resolved:` section is likewise dead — it is not written or read anywhere in current
code.

**What to actually do when migrating an old `project.yaml`:** rename the file to
`box.yaml` (§2.45), then **discard the `project:` / `resolved:` sections entirely** — do
NOT carry that layout forward. Recover any value you still need under its current key:
only a **non-default** `enable_vault: false` needs recovering, written under a `box:`
table instead:

```yaml
box:
  enable_vault: false
```

`mode`, `group_auth`, and `name` are self-deriving and need no manual carry-forward. A
moved/copied tree keeps its identity on drop-in import via the mechanism in §6, not via
a carried `name` field.

### 9.3 Box-side vault dest `~/share-ro` / `~/share-rw` → `~/vault/ro` / `~/vault/rw`

This change is detailed in **§4.7** — cross-reference it, not duplicated here. In
short: inside the box, the vault is now mounted at `~/vault/ro` / `~/vault/rw`
(previously `~/share-ro` / `~/share-rw`). Update any in-box scripts, aliases, or
agent instructions accordingly. The host-side vault source is unchanged; the old
in-workspace `~/workspace/vault` tmpfs mask is REMOVED (see §4.7).

### 9.4 Host agent-config import removed

The removal of host agent-config import (claude `.claude.json`/`oauthAccount`, codex
`config.toml`, goose `extensions`/`instructions`) is detailed in **§8.5** —
cross-reference it. It is the user-facing behavior change that the finalized
descriptors carry; the descriptor model change itself is internal.

---

## 10. Agent selection, blanket flags & file-only `system.*`

The 1.6.0 pre-public clean-house also overhauls how an agent is chosen and how a
command targets a box. These are **breaking** and require action on upgrade.

### 10.1 No agent is auto-picked when 2+ are installed (BREAKING)

Previously, `kanibako` (i.e. `start`) with no explicit or configured agent would
**arbitrarily launch one of the installed agents** — the first in plugin-discovery
order (the "goose-by-luck" footgun, since the `kanibako` meta-package installs all
three). That arbitrary pick is **removed**.

The new resolution cascade (highest precedence first) is
`--agent > box > workset > system default`. If nothing resolves, the **installed
count** decides — with **no ordering and no tie-break**:

| Installed agents | Behavior |
|---|---|
| exactly 1 | used implicitly (unambiguous — no change for single-agent users) |
| 0 | **error**: install a plugin (or use `kanibako shell`) |
| **2+** | **error**: pick one — run `kanibako setup` or pass `--agent <name>` |

**What you must do:** if you run the meta-package (2+ agents) and relied on the
implicit pick, choose a default once with **`kanibako setup`**, or pass
`--agent <name>` per invocation. This applies uniformly to every agent-requiring
command (`start`, `box start`, `agent reauth`, …) — not just launch.

`kanibako shell` is the **sole** no-agent path: it never resolves an agent and never
errors on agent selection (the emergency-recovery hatch into the container).

### 10.2 `setup` now selects a default agent

`kanibako setup` gained an interactive step (the **only** interactive prompt in the
CLI): on a TTY it lists detected agents in a numbered menu and writes your pick as
the host-global default. A "skip" option is offered; with 2+ agents installed skip is
**gated** — it warns that a naked launch will then fail and requires an explicit
`y`/`yes`, otherwise it re-prompts. With exactly one agent, skip is harmless.

- Non-interactive: `kanibako setup --agent <name>` (validated against installed
  plugins; unknown name is an actionable error, no prompt).
- Non-TTY without `--agent` (CI / headless): selection is skipped gracefully — no
  prompt — with a note to set it later.

`setup` also records a **completion marker** (`system.setup_completed`, the build
version). Agent-requiring commands print a **non-blocking** stderr nudge when setup
has never been run (or the recorded version predates a setup-affecting change), then
proceed — it never blocks the single-agent happy path.

### 10.3 `system.*` config keys are file-only (BREAKING)

`kanibako system set system.<key>=<value>` (and `system reset`) no longer SET any
`system.`-prefixed key — including `system.default_agent`. The CLI still **reads and
shows** them; it refuses to set them and points you at the config file:

- To set the default agent: run **`kanibako setup`** (it writes it for you), or edit
  `global/settings.yaml`'s `[agent.default] default_agent` directly.
  > ⮕ **SUPERSEDED IN v1.8.0** ([v1.8.0 guide](#migrating-to-kanibako-v180) §2.1): the
  > key is now `system.agent`, it lives in that
  > file's `system:` table, and it IS CLI-settable — `kanibako system set
  > system.agent=<name>`. Only the layout-PATH keys stay file-only.
- To change a structural path (e.g. `config.data`, formerly spelled `system.data` — §2.1,
  §3.1): edit `~/.config/kanibako_config.yaml`.

Non-`system.`-prefixed settings (e.g. `model`, `box.image`) remain CLI-settable at
every scope, including the global tier (`kanibako system set model=opus`).

**What you must do:** replace any scripted `kanibako system set system.*=…`
invocations with a `setup` run or a direct file edit.

### 10.4 Blanket `--agent` and `--box` flags

Two flags now parse on every command (passing one to an unrelated command is an
error, not a silent no-op):

- **`--agent <name>`** — uniform, top-precedence, **ephemeral** (this invocation only)
  agent override. Pulls that agent's whole config; never persisted.
- **`--box <name-or-path>`** — universal **subject/anchor** selector: act on a box
  that is not your cwd. The value is a path OR a box name (**name takes precedence**);
  it replaces the need to `cd` into the box. It coexists with the box-command
  destination group (`--default/--standalone/--workset` on `move`/`convert`), which is
  an orthogonal axis: `kanibako box convert --box mybox --standalone` works.

⚑ **Parsing is universal; the help is not.** Each flag is listed in a command's `--help` only where
it applies — offering `--box` under `rig list`, or `--agent` under `system get`, would advertise a
flag that command answers with an error. Passing one anyway still reaches that error, and the error
names the commands the flag does apply to, so a misplaced flag is told where to go rather than
merely rejected.

### 10.5 `-p`/`--project` removed (clean break)

The old `refresh -p/--project` flag is **removed outright** with no deprecation alias.
Use `--box <name-or-path>` instead.

### 10.6 Box-name constraints (NEW)

New box names (at creation / `--name`) are validated against a blocklist. Allowed:
unicode letters/digits plus interior `_`, `-`, `.`. Blocked: control characters, all
whitespace, every ASCII punctuation char except `_ - .`, the names `.` and `..`, a
leading `-` or `.`, a trailing `.` or whitespace, and length over 64. Uppercase ASCII
folds to lowercase. Pre-existing non-conforming boxes still resolve but are **flagged**
(warned), not rejected — rename them at your convenience.

## 11. Removed deprecation shims (rename-class; clean break)

The transitional aliases that bridged the old naming to the frozen
`box` / `agent` / `rig` model are **removed outright** — there are no deprecation
warnings or fallbacks left. Update to the canonical forms:

| Old (removed)                     | Use instead                                  |
|-----------------------------------|----------------------------------------------|
| `kanibako image ...`              | `kanibako rig ...`                            |
| `kanibako container ...`          | `kanibako box ...`                            |
| `kanibako rig create <name>`      | `kanibako rig extend <name> --from <rig>`     |
| `kanibako rig create --template T`| `kanibako rig prep T`                         |
| `kanibako rig rebuild [<img>]`    | `kanibako rig prep [<name>] --force`          |
| `... config image[=...]`          | `... config box.image[=...]`                  |
| `... config agent[=...]`          | `... config box.agent[=...]`                  |

The `image` / `agent` short config-key names no longer resolve to anything; only
the canonical `box.image` / `box.agent` keys are recognized (setting an `image`
or `agent` key is now an "unknown key" error). Image-freshness notices that used
to suggest `kanibako rig rebuild` now point to `kanibako rig update`.

---

## 12. Removed data-relocation shims (clean break; no auto-migration)

The auto-handlers that silently moved or read your old on-disk state are
**removed outright**. They may strand state created by older versions — migrate
it by hand using the tables below.

### 12.1 Snapshot `.tar.xz` archives → directory snapshots

Vault snapshots are now **directory snapshots only** (`reflink` / `hardlink`).
The legacy compressed-archive format is no longer created, listed, restored, or
pruned; a leftover `*.tar.xz` file under `<vault>/.versions/` is simply ignored
(not shown by `kanibako box vault list`, never picked up by `prune`).

To recover data from an old archive, extract it manually:

```bash
# Recover a legacy archive into a directory snapshot (named by its timestamp).
cd <vault>/.versions
mkdir 20260221T103000Z
tar -xJf 20260221T103000Z.tar.xz -C 20260221T103000Z
rm 20260221T103000Z.tar.xz      # optional: it is otherwise ignored
```

After that the directory is a normal snapshot, restorable with
`kanibako box vault restore 20260221T103000Z`.

### 12.2 Config & env files at old locations

`config_file_path` now resolves **only** the current location, and the global
`env` file is no longer read by anything — the whole system-tier env reader is
deleted (§2.19). If you have files at the old paths, move them yourself:

| What        | Old location (no longer read/moved)        | Current location                |
|-------------|--------------------------------------------|---------------------------------|
| Main config | `$XDG_CONFIG_HOME/kanibako/kanibako.yaml`  | `$XDG_CONFIG_HOME/kanibako_config.yaml` |
| Global env  | `$XDG_CONFIG_HOME/kanibako/env`            | not a file any more — the `system.env.<VAR>` setting key (§2.19) |

```bash
mv ~/.config/kanibako/kanibako.yaml ~/.config/kanibako_config.yaml
# There is no system-tier env FILE to move the old one to (§2.19: the reader
# that used to load it is deleted). Move each VAR=value line to a key instead:
kanibako system set system.env.<VAR>=<value>
```

(`$XDG_CONFIG_HOME` defaults to `~/.config`. Adjust if you set it explicitly.)

---

## 13. Binary transforms are selected by the `transform` key

Which binary transform runs for an agent is now chosen by the
`agent.<agent>.transform` key. The claude plugin declares `transform: tweakcc`,
so **claude boxes are unaffected** — the patcher runs exactly as before.

Previously the patcher was gated only on `transform_settings` being non-empty,
so **any** agent with that table set had claude's tweakcc patcher run against
its binary. It now runs only when `transform` names `tweakcc`.

| If you have                                                   | What changes                                                                    | What to do                                                                                    |
|-----------------------------------------------------------------|----------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| A claude box (with or without `transform_settings`)              | Nothing                                                                           | Nothing                                                                                           |
| A goose or codex box with a `transform_settings` table            | claude's patcher no longer runs against that agent's binary, and kanibako warns | Remove the `transform_settings` table from that agent's settings, or set `agent.<agent>.transform` if a transform exists for it |

Running claude's binary patcher against a non-claude binary was never intended;
the key makes the choice explicit rather than inferred.

## 14. The flattened directives file: no generated headings, a new link form

Kanibako assembles your directive tree — the kickoff plus everything it imports —
into the single file it hands the agent. The **sources you edit are unchanged**;
what changed is the shape of the assembled artifact.

### What changed

Each imported chapter used to be emitted under a machine-generated heading
(`## canon_bible_general_directives_ROM_GENERAL_md`), and every reference to it
became a link to that anchor. Chapters are now emitted under **their own**
headings, so the assembled file reads as one outline rather than a list of
slugs.

A new import form both includes a file **and** links to it:

```markdown
1.1 [Identity & Environment](@general/directives/ROM_GENERAL.md)
```

which produces the heading `## 1.1 Identity & Environment` and a link that
resolves to it. Outside a numbered list the display text is used on its own
(`[Release Notes](@notes.md)` → `## Release Notes`). Heading depth is the level
of the heading enclosing the list, plus how deeply the row is nested within it,
so an included chapter always sits **beneath** the section that included it.

Two kinds of file now contribute **no section**: one that is only comments and
whitespace, and an index that is nothing but imports. Their imports are still
followed. When a chapter that a numbered row points at is left out, **the row is
dropped and the rows after it renumber**, so a reader never sees a number
pointing at a section that is not in the file.

### What you need to do

| If you have | What changes | What to do |
|---|---|---|
| Directive sources using plain `@path` imports | Nothing — `@path` still includes the file exactly as before; it produces no link and no heading | Nothing |
| A hand-written link to a generated anchor, e.g. `[see](#canon_handbook_general_directives_rules_CANON_md)` | That anchor no longer exists, so the link goes nowhere | Point it at the chapter's own heading (`#canon-structure`), or convert the import to `[Display Text](@path)` and link to the heading that generates |
| A stub chapter that is only comments (the stock `ROM_AGENT.md` / `SYS_AGENT.md` placeholders) | It no longer contributes an empty section, and a numbered row pointing at it is dropped | Nothing, unless you want the section: give the file real content and it reappears on the next reload |
| A numbered index whose rows you refer to by number elsewhere | Numbers can shift when an empty chapter's row is dropped | Refer to chapters by name rather than by number, or give the empty chapter content |

Nothing here requires an edit to a working directive tree. The one case that
breaks silently is a hand-written `#canon_*_md` fragment link, because the
anchor it names is no longer generated.

### Why

The generated headings were an artifact of assembly, not something an author
chose: they named files rather than subject matter, they nested by accident of
path depth rather than by meaning, and a placeholder chapter produced a heading
with nothing under it. Letting each chapter carry its own heading, and leaving
out what has no content, makes the assembled file say what is actually in it.
