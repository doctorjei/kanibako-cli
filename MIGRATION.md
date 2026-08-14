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
> **What a leftover key actually does.** The closed-keyspace *resolve* enforcement is a
> deferred follow-on, so in v1.8.0 **a retired or renamed key left stored in your files
> is silently inert at launch** — it is carried into the merged snapshot verbatim,
> resolves to nothing, and produces no error and no warning. The exceptions that DO error
> loudly are exactly two keys — the agent-selection pair (§2.1) — plus the
> typed-at-the-CLI surfaces noted below. When that enforcement lands, every "silently
> inert" row in §2.1 becomes a loud error.

Paths below: `<data>` is your kanibako data directory (default `~/.local/share/kanibako`;
whatever `config.data` points at if you moved it).

---

## 1. One page: what changed and what you must do

v1.8.0 is a deliberate **clean break** (no aliases, no deprecation window). Four released
config surfaces are removed outright: `box.agent_name`, `system.default_agent`, the `shared`
mount category, and `system.base_template`. Directory layouts also move, on the host and
inside boxes. In order of likely impact:

1. **Your first `kanibako start` (or `create`, or `reauth`) after upgrading is a hard error
   until you run `kanibako setup`.** v1.8.0 raises the setup baseline (`SETUP_BCV`), so the
   `setup_completed` marker your v1.7.2 config recorded is too old for the running build and
   the setup-compatibility gate hard-blocks: `Error: This kanibako config (1.7.2) is too old
   to auto-update. Re-run 'kanibako setup' before agent commands.` (rc 1). This is deliberate
   — setup is what installs the new host-store layout (§2.12). Run `kanibako setup` once,
   right after upgrading. Headless: `kanibako setup --refresh-templates` (add
   `--agent <name>` to skip the menu). ⚑ Pass `--refresh-templates` on a headless run: a
   non-interactive setup that cannot ask about the template refresh deliberately records
   nothing, prints `Setup Incomplete` and exits **rc 1**, so the block stays up (§2.12).

2. **Your boxes will refuse to launch until you replace the agent-selection key.** Every box
   that ever chose an agent has `box.agent_name` stored; v1.8.0 refuses to launch such a box
   with an error that names the file and the exact fix (§2.1). One command per box:
   `kanibako box set pref.system.agent=<name>`. The system default moved too:
   `kanibako system set system.agent=<name>`.

3. **Upgrade the agent plugins WITH the base — never the base alone.** Upgrading only
   `kanibako-cli` while keeping v1.7.2-era agent plugins silently deletes your boxes' entire
   instruction/directive chain (no error is printed). Upgrade via the `kanibako` meta package,
   or upgrade the plugins first (§2.6).

4. **Claude plugins and cache will look EMPTY unless you move two directories** before your
   first launch on v1.8.0 (§2.5). Nothing errors — the box just sees empty dirs:
   `mkdir -p <data>/agents/claude/common && mv <data>/agents/claude/{plugins,cache} <data>/agents/claude/common/`

5. **The `commons` channel is now `common`** — on disk (host) and in-box
   (`~/channels/commons` → `~/channels/common`). Move the directories before first launch or
   an empty `common/` is created beside your populated `commons/`, silently (§2.3). Any
   scripts/notes of yours that reference the old path break silently.

6. **Instruction files move into the canon.** New boxes get `~/canon/{bible,handbook,notebook,
   workbook}` with a read-only, root-owned skeleton; `~/playbook` is retired as the entry
   point. Existing boxes keep launching but their own `~/playbook` directives **silently stop
   being loaded** and need hand-triage (§2.4).

7. **Two mounts at one destination now refuse to launch** where the more specific scope used
   to win silently (§2.2). The error says the rule changed and prints the exact YAML cure.
   Default installs are proven collision-free; only hand-added shares/binds can collide.
   After upgrading, `kanibako box show --effective` reports collisions without launching.

8. **Rename the `shared` category to `common` in your settings files** (e.g.
   `agent.claude.shared.plugins` → `agent.claude.common.plugins`). A leftover `shared` entry
   is silently ignored (verified — see the header note), so the bind it declared simply
   stops appearing (§2.1).

9. **Relative host paths in `workset share add` no longer resolve under the workset root at
   launch.** New adds are resolved and stored absolute at write time; **already-stored relative
   sources must be rewritten to absolute paths by hand** or they resolve against the process
   CWD — a wrong-directory mount, not an error (§2.7).

10. **The box template root moved and restructured** (`global/base_template/` →
    `global/template/box/home/`). Existing boxes are untouched (seeded once, long ago). The
    forced `kanibako setup` (item 1) re-creates the NEW tree with **stock packaged content**,
    so new boxes do NOT seed empty — but **any customizations you made in
    `global/base_template/` are orphaned there, silently**: nothing reads the old directory,
    nothing warns about it, and new boxes seed the stock files instead of yours (§2.5).

11. **System-scope binds/caches/secret pointers now live in ONE file** — `global/settings.yaml`,
    not `~/.config/kanibako_config.yaml`. If you ever hand-placed such entries in the config
    file (working around the old broken routing), move them (§2.8).

12. **A symlink anywhere in a template directory now fails box creation loudly** — if you
    symlinked template files into a dotfiles repo, replace them with real files or a bind
    (§2.13).

13. **If you use PERSONA agents, delete persona values you did not write yourself** from
    `agents/<node>/settings.yaml` — the store is now read live and a leftover synced value
    silently outranks it (§2.15). Also: a persona's whole `env` block now reaches the box, a
    rejected token is now a hard error on every `start`, and a generated agent settings file no
    longer carries `model` (§2.15, §2.16).

14. **If you pass flags to a box that may already be running, they are now refused instead of
    silently ignored** (§2.17). `kanibako start -N <running box>` used to reattach you to the OLD
    conversation without a word; it now errors. Same for `--rig`, `-e` (except where a second
    process in the box will apply it — see §2.17), `--browser`, `--share-images`, `--no-helpers`,
    `--no-auto-auth`, `-C`, `-R`, `-M`, `-A`, `-S`, and an
    explicit `--persistent`/`--ephemeral`. The cure is the new **`kanibako --restart [box]`**, which
    stops the box and starts it again with your flags in force. Scripts that start boxes with flags
    are the thing to check. (Two upsides in the same change: a reattach no longer builds images or
    makes network calls it cannot use, and `--entrypoint` against a live box now runs your command
    in it as a second process instead of being dropped.)

15. **If anything you run deletes a box directory and lets the next `start` put it back, it now
    errors instead** (§2.18). A launch never rebuilds a box: with the registration intact and the
    box directory gone, `kanibako start` used to silently re-create and re-seed it. It now refuses
    and prints the command that rebuilds it (`kanibako create <workspace>`, or `workset disconnect`
    + `workset connect` for a workset member). In the same section: `kanibako box set
    box.<key>=<value>` from a directory that is **not** a box now errors instead of writing a
    settings file for a box that does not exist.

16. **Your `env` files are no longer read, silently** — and the bare `env.<VAR>` key is refused
    (§2.19). The three docker-style `env` files (`<data>/env`, the workset one, the per-box one)
    were dropped; every `VAR=value` line in them stops reaching your boxes. v1.7.2 seeded
    `COLORTERM=truecolor` into `<data>/env` on first run, so **essentially every pre-existing
    install has one of these files**. A launch that finds a non-empty one now prints a notice
    naming the file and the per-tier cure. Move each var with
    `kanibako system set system.env.<VAR>=<value>` (or the `workset`/`box` equivalent), then
    delete the file.

17. **You can no longer `set` or `reset` a bind entry from the CLI — edit the settings file
    instead** (§2.20). `kanibako box set box.bindings.rw.home=/newhome` and `kanibako system set
    agent.claude.bindings.ro.launcher=/newsrc` both used to work; both now refuse, naming the key
    and the file to edit. **Nothing you have already configured stops working** — the keys are
    still declared, still read at launch, and `config get` still reads them back. Only the write
    verb is gone, and there is no CLI replacement. ⚑ One exception, and it is the example above: a
    binding at the box home is a separate change and does **not** keep mounting (item 19). If a
    script of yours repoints a bind, that is the thing to check. The other mount categories
    (`caches`, `seeded`, `common`, `synced`) are untouched and still settable at every scope.

18. **`workset share add` / `rm` lost their NAME argument** (§2.21). `workset share add WS NAME
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

19. **If you gave a box a custom home with a binding at `~`, that box no longer starts** (§2.32).
    The box home stopped being a binding — it is the foundation the rest of the mount set folds
    over — so an entry at `~` in any settings file is now a second claim on one place and refuses
    the launch by name. Nothing else moves: a binding *inside* home (`~/work`) is unaffected, and
    the mount a box receives at `~` is byte-identical to before. **The cure is `workset.boxes`**,
    the workset-scope key naming where box stores live, plus moving the directory yourself. Home
    also leaves the per-scope `bindings.*` listing in `kanibako box show --effective` and appears
    above it as a labelled foundation line.

20. **If the same environment variable is declared at two scopes, that box no longer starts**
    (§2.33). `system.env.EDITOR` alongside `box.env.EDITOR` used to launch with the innermost
    scope's value and no word about the declaration it discarded; it now refuses, naming both
    keys. A variable is a slot with one value, and each scope acts in turn from the outside in
    — the same rule two bindings at one destination follow. **The cure is one owner:** delete
    one of the two keys. ⚑ Overriding is untouched — the *same* key in more than one file is
    the ordinary cascade and the nearest file still wins. ⚑ **Check your persona if one of the
    keys is not in any of your files:** a persona's store config supplies `env:` entries as
    live agent-scope keys that are never written to disk (§2.33, §2.15).

21. Smaller items: standalone boxes' `box get` got truthful (§2.9); a box suppressed to
    plain-shell keeps stale credential files in its home (§2.10); several never-released or
    expected-empty renames (§2.11); two `--null` CLI bugs fixed (§2.14).

---

## 2. Per-area detail

### 2.1 Settings keys renamed or retired

| old (v1.7.2) | new (v1.8.0) | left in place, it is… |
|---|---|---|
| `box.agent_name` | `pref.system.agent` (workset/box files only) | **hard launch error** (below) |
| `system.default_agent` (stored as `agent: default: default_agent:` in `global/settings.yaml`) | `system.agent` (same file, `system: agent:`) | **hard launch error** (below) |
| `<scope>.shared.<name>` (the `shared` mount category) | `<scope>.common.<name>` | silently ignored (verified) |
| `system.base_template` | `system.template` (and it now names a template ROOT — §2.5) | silently ignored (verified) |
| `@meta.runtime.ws_settings` (reference target) | `@meta.workset.settings` | dangling reference |
| settable `box.agent.*` mirror | read-only `meta.box.agent.*` read-back; write via `pref.agent.<agent>.<key>` | inert; write verbs refuse with the pref cure |

**What a stale stored key actually does, per surface** (measured on the shipped code — this
is what v1.8.0 does, not the eventual closed-keyspace plan):

| surface | behavior |
|---|---|
| launch / `box show --effective` | **silent** — carried inert; no error, no warning; a `shared` bind vanishes from the mounts |
| `box show` (stored view) | **silent** — the undeclared entry is simply not displayed |
| `kanibako box get <box> <stale key>` | prints `(not set)`, rc 0 |
| `kanibako box get <stale key>` (no box argument) | `Error: Unknown project or workset: '<key>'` — the unknown key is taken for a project name |
| `kanibako system get`/`set <stale key>` (typed) | **loud** — `Error: unknown config key: …`, rc 1 |
| `box.agent_name` / `system.default_agent` stored anywhere in the cascade | **hard refusal** at launch and in `box info` (below) |

**The retired agent-selection keys are refused loudly** — the one place v1.8.0 deliberately
errors instead of ignoring, because a guessed agent would silently run a *different* agent and
seed that agent's credentials into your box. The launch error (verified verbatim on a scratch
box; `box info` shows the same refusal in its `Agent:` row):

```
'box.agent_name' is RETIRED and is still set in the box settings file <path> (as `box: agent_name:`).
The RULE CHANGED in kanibako 1.8.0: a box no longer names its agent with a key of its own — it
REQUESTS one at the key that resolves earlier (`pref.system.agent`, spec §2h), and the system
default is now `system.agent` (§2g). Refusing rather than running: kanibako cannot tell which
agent you meant, and guessing would launch a DIFFERENT agent and seed that agent's credentials
into this box.
  Fix: kanibako box set pref.system.agent=<value>   (or `kanibako box set --null pref.system.agent` for a no-agent box)
  then delete the `box: agent_name` entry from <path>.
```

The cure is level-appropriate, with your own stored value interpolated so it is copy-pasteable:

- `box.agent_name` in a **workset or box** settings file:
  `kanibako box set pref.system.agent=<value>` (or `kanibako box set --null pref.system.agent`
  for a no-agent box)
- `box.agent_name` in a **system or agent** file: REMOVE it — a request may be written ONLY in
  a workset or box settings file (spec §2h), so this key has no equivalent at that scope. If
  you meant the host-wide default: `kanibako system set system.agent=<value>`. If you
  meant one box, set the request in THAT box's settings file.
- `system.default_agent` (anywhere): `kanibako system set system.agent=<value>`

Notes:
- An **empty** leaf (`box: agent_name:` with no value) still counts as the retired key and is
  refused the same way (verified).
- The new on-disk shape of a request is a **nested table** in the box/workset `settings.yaml`
  (`pref: {system: {agent: <name>}}`) — never a dotted literal; `config set` writes it for you.
  Suppression ("this box runs no agent") has its own spelling: `kanibako box set --null
  pref.system.agent`. `--null` writes a real YAML `null`; the sibling `reset` VERB
  (`kanibako box reset <box> <key>`) instead *removes* the entry. ⚑ There is no `--reset` flag.
- A stale `box: {agent_name: ""}` row may also sit in `~/.config/kanibako_config.yaml` — old
  versions wrote it into every freshly-initialised host. Nothing ever read it there; it is
  inert and safe to delete for tidiness. It does **not** trigger the refusal (verified).
- **`shared` → `common`:** rename the category token in your settings files, keeping scope,
  agent name, entry name, and value (`shared:` table → `common:`). There is no alias.

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
  limited to hand-written `workset share` / `config set` / settings-file entries that
  duplicate a destination.
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
2. If you ever ran `config set …channels.commons <path>`: the stored value is now **orphaned**
   — the launch silently ignores it and reverts to the default location. Edit the settings
   file and rename the nested `channels: commons:` slot to `common:`. (Typing the *old key* at
   the CLI is loud — `Error: unknown config key: workset.channels.commons` — but nothing at
   launch tells you about a stored one.)
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

  The first three rows leave the box entirely — a handbook chapter is host content,
  contributed by a scope, not stored in the box. Triaging is yours to do: one box's playbook
  cannot be promoted wholesale into the shared handbook without imposing it on every other
  box. The ruling on record: existing-box migration stays deferred; new boxes only.
- **The playbook-equivalent tree is now read-only in-box.** An agent that edits its
  `~/playbook` today cannot edit `~/canon/handbook` tomorrow; its own writing goes to the
  notebook and workbook. Do not report this as a regression — edit the handbook host-side.
- ⚑ **A box store now holds TWO different `canon` directories** — do not confuse them:
  `<box_dir>/home/canon/` is the box's assembled guest view (`~/canon`), while
  `<box_dir>/canon/` is the box's *contribution* root whose `handbook/` is one chapter bound
  read-only at `~/canon/handbook/box`. A file placed in the wrong one is shadowed by the
  mounts and never read.
- An old box also keeps a stale, empty `home/playbook/kanibako/**` stub tree on disk —
  harmless residue, and a handy "this box predates the canon" marker.

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

1. Your first `start` / `create` / `reauth` hits the setup-compatibility gate: a hard rc 1
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
with `unknown config key`; a stored value is silently inert — verified). Re-point via the new
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

### 2.6 The kickoff transition — why upgrade order matters to YOU

The "kickoff" is the file that boots a box's whole instruction chain
(`~/.config/kanibako/kickoff.md`). In v1.7.2 each agent plugin shipped it. In v1.8.0 the base
package also ships it as a core bind (pointing at the canon), the plugins ship a
**transition-safe copy carrying both the new and the old import**, and the base *yields* to a
plugin-supplied kickoff during the overlap (the yield is keyed on the delivery destination, so
the two can never collide into a launch error). All of this is shipped and test-pinned.

What you will see, and what to avoid:

- **Mixed versions work, with one visible cost:** one `unresolved import` warning on stderr at
  every box launch (whichever import line does not match the installed base). That warning is
  the intended signal that your install is mid-transition; it disappears once base and plugins
  are both on their v1.8.0-era releases (and fully once the later plugin cleanup release lands).
- **The one fatal combination: new base + your old v1.7.2 plugins.** The old plugin kickoff
  carries only the old import (`~/playbook/…`), which the new base no longer provides —
  **every directive in every box silently stops loading**, no error anywhere. The plugins do
  not pin a base version, so `pip install -U kanibako-cli` alone puts you exactly there.
  **Cure: upgrade via the `kanibako` meta package, or upgrade the three agent plugins before
  (or with) the base.**
- No-agent (plain-shell) boxes: the kickoff file is bound but nothing consumes it yet; no
  action, no breakage.

### 2.7 Workset shares: relative host paths

`kanibako workset share add` documented that *"a relative host_src is resolved under the
working set root"*. That launch-time join is gone: in v1.8.0 the command resolves a relative
path **at write time** and stores it absolute (telling you when it rewrote what you typed).
⚑ A bare-relative source can no longer reach a bind category through `config set` at all — that
write route is gone for all six bind-shaped categories (§2.20), so the key itself is refused rather
than the source shape. A bare-relative source authored **by hand** in the settings YAML is still a
defect, and still resolves against the process CWD at launch — see the warning below.

**Already-stored relative sources are NOT rewritten for you.** At launch they pass through
as-is and resolve against whatever the process CWD happens to be — a plausibly
*wrong-directory* mount, which is worse than an error. Check every workset `settings.yaml` for
`workset.bindings.{ro,rw}` entries whose source does not start with `/`, `~`, `$`, or `@`, and
rewrite each to the absolute path it used to resolve to: `<workset root>/<relative>`.

One behavior change on an already-broken shape: `share add` on the **default** workset now
refuses a relative source (it never had a root to join under — the old behavior was a silent
CWD-dependent path, not a feature).

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
`kanibako system get <key>` — it now answers. The structural path keys (`system.cache`,
`system.channels.*`, `system.setup_completed`, …) belong in the config file and must **stay**.

One loud case: `kanibako system set` against a bind that exists *only* in the config file is
now refused with *"cannot create key … it must already exist in the cascade"*. That refusal is
correct (the launch never read that file), and the cure is the move above — after it, the same
`set` succeeds. A stale entry you *don't* touch is simply inert, exactly as it already was.

Box and workset scopes are unaffected. Agent-scope binds already routed correctly.

### 2.9 Standalone boxes: reads got truthful

Standalone boxes gain a real box-scope settings file, `<root>/box_data/settings.yaml` (absent
until first written); the project-root `settings.yaml` is the workset tier. No data
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

Each of these is expected to find nothing in real stores; listed so a grep of your own files
is quick. All are silently inert if left (see the header note), except where noted.

- **Bare `agent.<category>.*` keys** (e.g. `agent.common.plugins` with no agent name): an
  internal launch-built form that should never have been persisted. If a settings file carries
  one, discriminate it: `agent.<agent>.<category>.<rest>`. (`agent.default.*` is legitimate —
  leave it.) A double-prefixed relic like `agent.claude.agent.goose.…` should be unwound.
- **`<data>/agents/<agent>/share/` is deleted** (it was only ever a join root and was verified
  empty on inspection). If yours has content, it belongs to a hand-set relative agent
  binding — absolutise that binding (§2.7's rule); don't just delete the dir.
- **`workset.{boxes,vault_ro,vault_rw,logs}` overrides become live** where they were inert
  (standalone: all four; `workset.logs` for the helper log: all modes). If a
  settings file of yours sets one, the corresponding mount now moves — silently, since the new
  location is guarantee-created. A broken `workset.logs` override is visible: the launch logs
  `read-only source <path> does not exist; dropping mount`. Note an override moves the
  **mount** only; kanibako's own internal writes still target the default location, so an
  override is not yet a supported way to relocate a box.
- **`@meta.runtime.ws_settings`** in any settings file: replace with `@meta.workset.settings`
  (identical resolved value).

### 2.12 The upgrade gate: nothing new appears until `kanibako setup` runs

The v1.8.0 host stores — `global/template/{box,workset,agent}`, `global/canon/handbook`, and
the restructured `agents/<agent>/{template,canon/handbook}` stores — are installed by first-run
init or by `kanibako setup`, **never by `pip install`** (installing a package runs no code),
and the lazy first-run installer never re-fires on an already-initialised host. The designed
trigger for an upgrade is `setup`, and the **setup-compatibility gate forces it**: v1.8.0
raises the setup baseline (`SETUP_BCV`), so the `setup_completed` marker your v1.7.2 config
recorded is too old for the running build and every `start` / `box start` / `create` /
`box create` / `reauth` / `agent reauth` hard-errors (rc 1) with `This kanibako config (1.7.2)
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
  `agents/<node>/settings.yaml` byte-identical, and `kanibako create` no longer imports anything.

**What you must do: delete persona values you did not write yourself.** This is the one action this
change requires, and nothing warns you about it.

The agent settings file outranks the live store, so any `endpoint`, `model` or `secret_path.<VAR>`
that the old sync wrote into `agents/<node>/settings.yaml` (kanibako ≤ `v1.8.0-rc1`) keeps
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

⚑ **This is the one that can bite you.** v1.7.2 wrote `COLORTERM=truecolor` into `<data>/env`
on first run, so **essentially every pre-existing install has such a file**, and anything you
ever added to one with `kanibako <noun> set env.FOO=…` reached your box yesterday and does not
today. So that it cannot pass unannounced, a launch that finds a non-empty legacy file prints:

```
Notice: these env files are NO LONGER READ — values in them do not reach the box.
  /home/you/.local/share/kanibako/env
    move values with: kanibako system set system.env.<VAR>=<value>
  Delete the file(s) once migrated to silence this notice.
```

There is no new persisted state — the file's *existence* is the signal, so the notice
self-clears the moment you migrate or delete the file, and never appears on a box created after
the upgrade. The `COLORTERM` line is *ours*, not yours; migrating or deleting it is safe.

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
key and delete the file:

```console
$ kanibako system set system.env.<VAR>=<value>            # <data>/env
$ kanibako workset set <workset> workset.env.<VAR>=<value>  # <workset>/env
$ kanibako box set <path> box.env.<VAR>=<value>             # <box>/env
```

⚑ **A `$VAR` in an env VALUE is refused at set time.** These values go through kanibako's
expansion grammar, which knows only `$AGENT`, `$WORKSET` and `$XDG_*` — and a `config set` has
no live agent or workset, so in practice only `$XDG_*` resolves. A shell variable your `env`
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

### 2.20 Bind entries are edited in the settings file, not with `config set`

**What changed.** Two CLI routes are retired:

```
kanibako box set     box.bindings.rw.home=/newhome                # was: "Set … host source to …"
kanibako system set  agent.claude.bindings.ro.launcher=/newsrc    # was: exit 0
```

Both now refuse, naming the key and pointing at the settings file. `config reset` refuses the same
keys symmetrically — a reset is a write.

**What did NOT change, and this is the part worth reading.** The keys are **not** retired:

- they are still declared keys;
- they are still read by the launch cascade, so **every binding you already have keeps mounting**;
- they are still authored by hand in the settings YAML;
- **`config get` still reads them** at the box and workset nouns, naming the subject
  (`kanibako box get <box> box.bindings.ro`). ⚑ It is not a complete read surface — see the
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
`agents/<node>/settings.yaml`. For a box- or workset-scope bind you can read the current value
first with `kanibako box get <box> <key>` — naming the subject, which is required. ⚑ There is no
read-back for an agent-node bind, and the `system` noun refuses these keys outright; see §2.23.

**⚑ This now covers EVERY bind-shaped category, not just the two arms.** `caches`, `seeded`,
`common` and `synced` have lost their `config set` route as well — including the source-only
repoint, which used to let you change an entry's host source without touching its destination.
All six bind-shaped categories are **YAML-only**.

```
kanibako box set  box.caches.sock=/new/sock          # was: repointed the host source
kanibako box set  agent.claude.common.plugins=/new   # was: repointed the host source
```

Both now refuse, naming the key and pointing at the settings file, exactly as the two arms do.
`config reset` refuses them symmetrically.

**The test for whether a script of yours is affected** is therefore *not* "does the key contain
`bindings.ro`/`bindings.rw`" any more — it is **"is the key bind-shaped at all"**, i.e. does it name
`bindings.ro`, `bindings.rw`, `caches`, `seeded`, `common` or `synced`. If it does, and the script
*writes* it, the write now refuses.

**What is genuinely unaffected, for all six.** The categories are still declared, still read by the
launch cascade so every entry keeps being delivered, still authored by hand in the settings YAML,
and **readable at the box and workset nouns** — but ⚑ read them at the CATEGORY key now
(`kanibako box get <box> box.caches`), which returns the whole map. The per-entry spelling
`box.caches.<name>` is not a key any more, so `get` no longer reads one either; §2.23 shows the
file shape and records what the read surface still cannot do.

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
`no share 'x'` → `no share at 'x'`. `share list --effective` output is unchanged.

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
a box's, and `agents/<node>/settings.yaml`. Check for a `caches:`, `seeded:`, `common:` or `synced:`
table whose sub-keys are names rather than paths. (`bindings.ro` / `bindings.rw` already moved to
this shape earlier in v1.8.0 — see §2.20 and §2.21.)

**Two entries that shared a destination cannot both survive.** The destination is now the identity,
so a category cannot hold two entries at one path. If you had two names pointing at the same
destination, keep the one you meant; kanibako refuses the pair rather than silently dropping one.
(*Different* categories, or different scopes, at one destination are unaffected — those are
different keys, and the collision table in §2.2 decides between them exactly as before.)

**Reading a category.** `config get` reads the CATEGORY key and returns the whole map — but you
must **name the subject box or workset**. Given a single positional argument, kanibako reads
`box.caches` as a *project name*, not as a key:

```
kanibako box get <box> box.caches                  # the map
kanibako workset get <workset> workset.caches      # the map
```

This closes a gap: `box.bindings.ro`, `box.bindings.rw` and `box.masks` previously read back
`(not set)` even when set, because nothing claimed the bare key.

**⚠️ Known limitation — a category key is not individually readable yet.** A dest-keyed category is
*one key with many facets inside one value*, and kanibako has no settled surface for reading or
writing one facet of such a key. A readable form is planned; its shape is not decided, so do not
build a workflow on today's behaviour. Three things you will hit while making the edits above:

- `kanibako system get box.caches` — and any category key under the `system` noun — refuses with
  `Error: unknown config key`, even though the key is declared and the two nouns above read it.
- `agents/<node>/settings.yaml` edits **cannot be read back at all** right now: `kanibako agent get
  <node> agent.<node>.caches` answers `(not set)` whatever the file says.
- `... get <subject> box.caches.<destination>` is **not** a key. It does not refuse; it prints
  whatever happens to sit at that dotted path, which is `(not set)` for any destination containing
  a `.` — and most guest paths do (`~/.cache/uv`).

**So how do you check the edit you just made?** For a box or workset, use the two-positional read
above to confirm the YAML parsed into the shape you meant — it *echoes* the stored map and does not
validate it — and then run `kanibako box show <box> --effective` on a box the edit applies to.
That resolves the real launch snapshot, so a malformed entry in the box, workset **or** system tier
is refused there by name. For an agent node neither check is available today: read the file back
yourself, and rely on the launch to refuse a bad entry.

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
and `agents/<node>/settings.yaml`. Check for a `masks:` table written with `-` bullets.

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

**What you must do.** Give the variable **one owner**: keep the key at the scope the value belongs to
and delete the other one. `kanibako box show --effective` resolves the same settings and reports the
same refusal without starting anything, so you can find them before a launch does.

**⚑ One of the two keys may be one you never wrote in any file — check your persona.** If the box
runs a **persona**, that persona's store config supplies its `env:` entries as live agent-scope keys
(`agent.<agent>.env.<VAR>`) on every launch. They are resolution inputs, not file contents — nothing
is written to `agents/<node>/settings.yaml` — so grepping your settings files for the second key
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
`agents/<node>/settings.yaml`, or the system settings file:

```console
$ kanibako agent set claude env.DISABLE_AUTOUPDATER=0
```

```yaml
# agents/claude/settings.yaml — the same thing, written by hand.  ⚑ `self:` IS
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
# agents/claude/settings.yaml
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
`agents/claude/settings.yaml`, a table written like this:

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
# agents/claude/settings.yaml — the whole of it
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

⚑ **`bindings:` is not affected.** It still lives at `self: <node>: bindings:` and still works;
that table is on its own track.

`kanibako box show --effective` resolves the same settings a launch does, so it will show you the
resulting values (and report the refusal, if any) without starting anything.

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
   The base carries a transition arm (`templates._packaged_agent_store`): a plugin with no
   `data/base` falls back to its legacy `data/template`, installed to the destination that
   reproduces the old delivery byte-for-byte — so an old published plugin keeps working
   against the new base. The `synced` credential destinations becoming host-side paths is
   still open plugin-package work (the base-side applier already branches on `dest_space`).
5. **Build hygiene:** `rm -rf build/ packages/*/build` before any local wheel build — stale
   `build/lib/` trees ship deleted files (CI builds clean; local builds do not).
6. **`Binding.key`'s user override is now settings-file-only — the type is UNCHANGED.**
   `kanibako.targets.base.Binding` keeps its shape, its fields and its place in the plugin API;
   nothing to port. What changed is the *documentation you give your users*: the override key
   `agent.<name>.bindings.{ro,rw}.<key>` is no longer settable with `kanibako system set` (§2.20).
   It is still a real key — still declared, still beating your descriptor's own source at launch,
   still readable with `config get` — so the mechanism your `Binding` relies on is intact. ⚑ If your
   plugin's README or error strings tell a user to run `kanibako system set agent.<you>.bindings…`,
   that instruction now fails; point them at `agents/<node>/settings.yaml` instead. There is no CLI
   verb to substitute, so do not invent one.
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

### 3.1 Core module paths moved (package-ification) — shims ship for one release

v1.8.0 promotes the flat `src/kanibako/*.py` modules into coarse domain subpackages
(`kanibako.settings`, `kanibako.launch`, `kanibako.runtime`, `kanibako.vscode`,
`kanibako.channels`). **The plugin-facing surface — `kanibako.targets.base`, the
`kanibako.agents` entry-point group, and the `kanibako.plugins.*` namespace scan — did not
move**, so a plugin written against the documented interface (`docs/writing-targets.md`) is
unaffected.

Four core modules that the first-party plugins import *did* move. Because the plugins depend
on `kanibako-cli` with no upper bound, an already-published plugin can land beside the new
base. **The retirement is therefore two-stage** (ruling, 2026-08-01):

| Legacy path | New path | Imported by |
|---|---|---|
| `kanibako.vscode_config` | `kanibako.vscode.vscode_config` | claude, codex, goose |
| `kanibako.settings_resolve` | `kanibako.settings.settings_resolve` | claude, codex (`GUEST_HOME`) |
| `kanibako.agent_defaults` | `kanibako.settings.agent_defaults` | claude, codex, goose |
| `kanibako.agent_config` | `kanibako.settings.agent_config` | claude, codex, goose |

**Stage 1 — v1.8.0 (this release): the aliases WORK, and they say so.** Each old path keeps a
re-export shim covering the full public surface of the moved module, so an already-published
plugin keeps running unchanged. Importing one emits a `FutureWarning` naming the old path,
the new path, and what to do:

> `kanibako.agent_defaults moved to kanibako.settings.agent_defaults; this compatibility alias
> exists for plugins built against kanibako-cli < 1.8.0 and will be REMOVED in the next
> release — upgrade your kanibako-agent-* packages.`

`FutureWarning`, not `DeprecationWarning`, because the latter is hidden by default outside
`__main__` — a silent notice would be no notice. It fires only on the OLD path; correctly
updated code never sees it.

**Stage 2 — the release AFTER v1.8.0: the aliases are GONE.** The four shim files are deleted
and replaced by a named refuse-and-exit at plugin discovery: a plugin still importing an old
path is refused *by name*, with the upgrade instruction, instead of failing as a bare
`ModuleNotFoundError` from inside an entry-point load.

**Plugin authors: switch to the new paths now** — a one-line edit per import site — and in the
same release pin `kanibako-cli >= 1.8.0`, exactly as item 3 requires for the KICKOFF deletion.

⚑ **REMOVAL GATE — the same shape as item 3 above.** Stage 2 happens only once
`kanibako-agent-claude`, `-codex` AND `-goose` have all **published** releases importing the
new paths *and* carrying the `kanibako-cli >= 1.8.0` floor pin. Until then, deleting a shim
silently breaks agent detection for anyone on an older plugin.
`tests/test_plugin_import_compat.py` pins stage 1 (the aliases resolve, re-export the same
objects, and warn; the new paths stay silent).

**Version numbers.** This release ships as `kanibako-cli` **1.8.0**, `kanibako` (meta)
**1.8.0**, `kanibako-agent-claude` **1.8.0**, `kanibako-agent-codex` **0.3.0** and
`kanibako-agent-goose` **0.3.0**. The plugins version independently and do not adopt the
base's number (codex and goose never have). The KICKOFF-deletion follow-up (item 3) is **the
release after v1.8.0** for each plugin, and must carry the `kanibako-cli >= 1.8.0` floor
pin.

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
| Primary store | scattered `boxes/`, `comms/`, `share_*/` under data root | **PRIMARY workset** is a real dir at `@system.primary_workset` |
| Per-box meta file | `project.yaml` (mode/layout/paths/...) | per-box `settings.yaml` (`[project]` + `[resolved]` sections); all modes (§9) |
| Registry | `names.yaml` + `worksets.yaml` + `connected.yaml` | one `registry.yaml` (`@system.registry`) |
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
/etc/kanibako/config_base.yaml      defaults (overridable)
~/.config/kanibako.yaml             user global
/etc/kanibako/config_required.yaml  mandatory (NOT overridable)
```

CONFIG precedence: `config_base < ~/.config/kanibako.yaml < config_required`.

### 1.2 SETTINGS files — behavior

`agent.*`, `box.*`, `workset.*` and the category keys (WHAT happens) are set **only**
in settings files:

```
/etc/kanibako/settings_base.yaml      defaults
<scope>/settings.yaml                 per-scope (system / workset / box)
/etc/kanibako/settings_required.yaml  mandatory cap
```

### 1.3 The 6-tier settings cascade (box wins; `*_required` is the cap)

```
settings_base  <  system  <  agent.<agent>  <  workset  <  box  <  settings_required
```

- **`box` wins** among the normal tiers.
- **`*_required.yaml` sits ABOVE box** — it is an absolute admin cap that overrides
  everything (standard lockdown semantics). "Box wins" applies only among the
  normal tiers.

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
Move each var to `<scope>.env.<VAR>` in the scope's settings.yaml. Env precedence:
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

| Old `system.path.*` | New `system.*` | Notes |
|---|---|---|
| `system.path.data` | `system.data` | rename only |
| `system.path.crabs` | `system.agents` | + crab→agent (§2) |
| `system.path.comms` | `system.channelroot` | renamed + rebuilt (see §7); type roots under `system.channels.*` |
| `system.path.templates` | `system.base_template` | re-pointed to `@system.global/base_template` |
| `system.path.ws_hints` | `system.registry` | absorbed into the consolidated registry (§5) |
| `system.path.boxes` | **DELETED** | → `@system.primary_workset/boxes` (§4) |
| `system.path.share_ro` | **DELETED** | subsumed by `@workset.vault_ro` / category `shared` |
| `system.path.share_rw` | **DELETED** | subsumed by `@workset.vault_rw` / category `shared` |
| — | `system.backup` | NEW (`@system.data/backup`) |
| — | `system.global` | NEW (`@system.data/global`; holds `settings.yaml`, `registry.yaml`) |
| — | `system.settings` | NEW (`@system.global/settings.yaml`, the "system"-tier settings file) |
| — | `system.primary_workset` | NEW (`@system.data/primary_workset`; the PRIMARY workset root) |
| — | `system.cache` | NEW (`$XDG_CACHE_HOME/kanibako`; **not** under data) |
| — | `system.runtime` | NEW (`$XDG_RUNTIME_DIR/kanibako`; helper sockets; **not** under data) |
| — | `system.channels.{commons,chat,broadcast,mailboxes,share}` | NEW sub-keys (detailed in §7) |

Also **deleted from the top level** (now under the PRIMARY workset): `system.boxes`,
`system.logs`, `system.vault_ro`, `system.vault_rw`.

### 3.2 `system.default_agent` (renamed setting)

The old default-agent selector `system.agent` is renamed to **`system.default_agent`**
(to avoid the one-character clash with the `system.agents` store directory). It is a
**setting** (behavior), not config — it lives in the settings file set despite its
`system.*` name. `box.agent` falls back to it.

The system tier of these behavior settings now lives in **`global/settings.yaml`**
(`@system.settings`), separate from the `~/.config/kanibako.yaml` CONFIG file (which
holds only `system.*` layout/path keys). The `kanibako system get` / `system show`
commands READ / SHOW `system.*` keys (e.g. `system.default_agent`, `system.data`) but
— as of the W1 overhaul (see §10) — `system set` **refuses to set them**: all
`system.*`-prefixed keys are file-only. Non-`system.` settings (e.g. `model`) stay CLI-settable at the global tier
and are written to `global/settings.yaml`. **No automatic migration:** if you
previously set `system.default_agent` in `kanibako.yaml`, choose it with
**`kanibako setup`** (which writes it for you) or move the `[agent.default]` table
into `global/settings.yaml` by hand — a stale `[agent]` table in `kanibako.yaml` is
no longer read by the system settings tier. See **§10** for the full file-only rule
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

| Old `ProjectMode` / `project.yaml mode` | New `box.mode` | `workset.meta.name` |
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
`@system.primary_workset`.

**Before** (scattered under the data root):

```
$XDG_DATA_HOME/kanibako/
├── boxes/<box>/{shell, vault, ...}     # box home + maybe in-tree vault
├── comms/
└── share_ro/  share_rw/
```

**After**:

```
$XDG_DATA_HOME/kanibako/primary_workset/   ← @system.primary_workset (= @workset.meta.root)
├── settings.yaml
├── boxes/<box>/{home/ → ~/ , settings.yaml}
├── vault/{ro,rw}/<box>/                    → ~/vault/{ro,rw}
└── logs/<box>.jsonl
# the box WORKSPACE stays external: box.meta.workspace = your real project dir → ~/workspace
```

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
~/code/<wsname>/               ← workset.meta.root
├── settings.yaml              ← workset.meta.settings
├── boxes/<box>/{home/ → ~/ , settings.yaml}
├── workspaces/<box>/          → ~/workspace
├── vault/{ro,rw}/<box>/       → ~/vault/{ro,rw}
└── logs/<box>.jsonl
```

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
**single `<root>/settings.yaml`** = `@workset.meta.settings`, mirroring a box's
`settings.yaml` which carries `box.meta.*` alongside its settings:

| Old file → key | New location |
| --- | --- |
| `<root>/workset.yaml` (`name`, `created`, `group_auth`, `projects`) | `<root>/settings.yaml` under `workset.meta.*` |
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
~/scratch/myproj/             ← @workset.meta.root  (workset.meta.name: __STANDALONE__)
├── settings.yaml             ← box.meta.settings   (box metadata; AT THE ROOT)
├── workspace/                → ~/workspace          (a SUBDIR, not the root)
├── box_data/                 ├─ home/ → ~/          └─ <box.name>.jsonl   (helper log)
└── vault/{ro,rw}/            → ~/vault/{ro,rw}
```

⚑ **Two changes from earlier 1.6.0 dev builds (drift H + I):**

- **`settings.yaml` moved from `box_data/settings.yaml` to `<root>/settings.yaml`.**
  The `box_data/` directory is now ONLY the marker dir + home + helper log; the box
  metadata file is at the project root, alongside `workspace/` and `vault/`.
- **The workspace is now a `<root>/workspace/` subdir, not the project root.** The
  root holds the kanibako artifacts (`settings.yaml`, `box_data/`, `vault/`); your
  actual project files live under `workspace/` (mounted as `~/workspace`).

⚑ **The standalone walk marker is now a `box_data/` directory PLUS a
`<root>/settings.yaml`** declaring `mode: standalone`. (A NAMED workset root also
carries `<root>/settings.yaml`, but with a `workset.meta` identity and NO
`box_data/` dir, so the two never collide.) The old in-tree `.kanibako`/`kanibako`
dotdir marker is gone. When hand-editing a standalone tree, place `settings.yaml`
at the root, keep a `box_data/` dir beside it, and put your files under
`workspace/`. Drop any `layout:` field; the mode token stays `standalone`.

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
is detailed in §9 — read that section before hand-editing it.

Drop `layout` entirely; translate `mode` per §4.1; the path fields are derived from
the fixed per-mode tables, not user-edited. (Where the file lives: primary →
`@system.primary_workset/boxes/<box>/settings.yaml`; named →
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
| PRIMARY | `@system.primary_workset/logs/<box>.jsonl` |
| NAMED | `@workset.meta.root/logs/<box>.jsonl` |
| STANDALONE | `@workset.meta.root/box_data/<box>.jsonl` |

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
`@system.registry` (`@system.global/registry.yaml`).

| Old file | New `registry.yaml` section |
|---|---|
| `{data}/names.yaml` `[projects]` | `projects:` |
| `{data}/names.yaml` `[worksets]` | `worksets:` |
| `{data}/worksets.yaml` (== `ws_hints`) | `worksets:` / `workset_roots:` (name→root) |
| `{data}/connected.yaml` | `connected:` |
| (standalone boxes — previously unregistered) | `standalone:` (NEW) |
| `{data}/rigs.yaml` | `rigs:` |
| `{data}/image-shells.yaml` | `image_shells:` |

Steps:

1. Create `@system.global/` if it does not exist.
2. Merge the contents of `names.yaml`, `worksets.yaml`, `connected.yaml`,
   `rigs.yaml`, and `image-shells.yaml` into the appropriate sections of
   `@system.global/registry.yaml`.
3. Remove the old `names.yaml` / `worksets.yaml` / `connected.yaml` /
   `rigs.yaml` / `image-shells.yaml`.

The registry is now a **derived, rebuildable index** — losing it no longer orphans
boxes (see §6). On purge, names are now unregistered (no dangling entries), and a
same-name convert reuses the existing name instead of auto-suffixing.

---

## 6. Drop-in detection & import (NEW behavior)

On-disk metadata is now **authoritative**; the registry is just a rebuildable index.
All three modes are **self-describing on disk and drop-in importable.**

What this means for you:

- **Detection is an ancestor-walk**, not a registry lookup. Standalone is detected by
  walking up for a `box_data/` dir + a root `settings.yaml` with `mode: standalone`;
  named by a workset-root `settings.yaml` carrying `workset.meta`; primary by
  reconciling the central boxes dir against the registry.
- **You can move or copy a box/workset/project tree** to a new location or machine and
  kanibako re-discovers it.
- **Import is automatic with an alert, no confirmation.** When kanibako finds an
  on-disk entity that is not in the registry, it registers it, tells you it was
  imported, and proceeds.
- **Name collision = refuse.** If an import's name collides with an
  already-registered project/workset, kanibako refuses, leaves the tree untouched,
  and prints a clear error. (A future `rename` mechanism — not in 1.6.0 — will
  resolve collisions.)

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

| Old `system.path.comms` | New `system.channelroot` + `system.channels.*` |
|---|---|
| `system.path.comms` (one dir) | `system.channelroot` (`@system.data/channels`) — ROOT-path leaf; sub-keys below under the `system.channels.*` branch |
| — | `system.channels.commons` (`@system.channelroot/commons`) |
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
| **Commons** | a scope | read-write | `commons/` (system + workset) |
| **Chat** | a scope | read-append\* | `chat/*.md` (system + workset); default `general.md` |
| **Broadcast** | a scope | read-append\* | `chat/broadcast.md` (system + workset) |

\* Permissions are **by convention, not enforced** in 1.6.0 — every channel is
read-write-mounted. Any box can technically read or overwrite any other box's
mailbox/share/commons/chat. This is the deliberate single-operator box↔box trust
stance; box↔HOST isolation is unaffected. (Future helper-mediated enforcement will
tighten the write paths without moving the in-box paths.)

### 7.3 In-box layout: `~/comms/` → `~/channels/`

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

The top-level section token in a per-agent YAML file is renamed from `crab` to
`agent` (the last on-disk `crab` token in the config layer):

```yaml
# Before                         # After
crab:                            agent:
  model: opus                      model: opus
```

⚑ **Hard break, no back-read.** A file with a `crab:` section is not recognized
until you rename the section to `agent:`. (This is in addition to the cascade-level
and key renames in §2.)

### 9.2 Per-box meta file `project.yaml` → `settings.yaml`

The per-box metadata file is renamed `project.yaml` → **`settings.yaml`** in **every**
mode (primary, named, and standalone). See §4.6 for where each mode's file lives;
§4.5 for the standalone walk marker (`box_data/` dir + `<root>/settings.yaml`).

⚑ **What the file actually contains (on-disk format).** The per-box `settings.yaml`
stores construct-time box metadata in two YAML sections, `project:` and `resolved:`
— these are the *physical* on-disk shape you would see if you opened the file. (The
keyspace documents this metadata as the logical `box.meta.*` / `workset.meta.*`
model; the on-disk layout uses these two sections rather than nested `box.meta.*`
tables. The logical keyspace names are the model; the sections below are the disk
reality.)

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

When migrating an old `project.yaml`: rename the file to `settings.yaml`, **drop any
`layout:` field**, translate `mode` per §4.1, and keep the `project:` / `resolved:`
section layout shown above. You normally do not hand-edit the `resolved:` section —
it is derived construct-time state, regenerated by kanibako. The `name` under
`project:` is what lets a moved/copied tree keep its identity on drop-in import (§6).

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
- To change a structural path (e.g. `system.data`): edit `~/.config/kanibako.yaml`.

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
`env` file is no longer auto-moved. If you have files at the old paths, move
them yourself:

| What        | Old location (no longer read/moved)        | Current location                |
|-------------|--------------------------------------------|---------------------------------|
| Main config | `$XDG_CONFIG_HOME/kanibako/kanibako.yaml`  | `$XDG_CONFIG_HOME/kanibako.yaml`|
| Global env  | `$XDG_CONFIG_HOME/kanibako/env`            | `<data>/env` (`@system.data/env`) |

```bash
mv ~/.config/kanibako/kanibako.yaml ~/.config/kanibako.yaml
# Find your data dir with `kanibako system get system.data` (prints
# `system.data=<path>`), then move the env file under it:
mv ~/.config/kanibako/env <data>/env
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
