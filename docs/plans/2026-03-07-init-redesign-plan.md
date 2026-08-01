# Init Command Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Merge `init` + `new` into a single `init` command supporting all project modes, with `--image` for setting the container image at creation time.

**Architecture:** The unified `init` replaces both commands. Without `--local`, it calls `resolve_project()` (AC/workset auto-detection). With `--local`, it calls `resolve_decentralized_project()`. The `--image` value (or global default) is always persisted to `project.toml`. The `new` command is removed.

**Tech Stack:** Python, argparse, existing kanibako config/paths modules.

---

### Task 1: Update init parser — merge `new` into `init`, add `--image`

**Files:**
- Modify: `src/kanibako/commands/init.py:13-60`
- Test: `tests/test_init_cmd.py`

**Step 1: Write failing tests for new parser behavior**

In `tests/test_init_cmd.py`, replace `TestInitParser` and remove `test_new_parser`:

```python
class TestInitParser:
    def test_init_parser_local(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--local"])
        assert args.command == "init"
        assert args.local is True
        assert args.path is None
        assert args.image is None

    def test_init_parser_with_path(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", "/tmp/mydir"])
        assert args.command == "init"
        assert args.path == "/tmp/mydir"

    def test_init_parser_with_image(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", "--image", "kanibako-template-jvm-oci"])
        assert args.image == "kanibako-template-jvm-oci"

    def test_init_in_subcommands(self):
        assert "init" in _SUBCOMMANDS

    def test_new_removed_from_subcommands(self):
        assert "new" not in _SUBCOMMANDS
```

**Step 2: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_init_cmd.py::TestInitParser -v`
Expected: FAIL — `args.path` doesn't exist yet, `args.image` doesn't exist, `new` still in subcommands.

**Step 3: Update the parser**

In `src/kanibako/commands/init.py`, replace both `add_init_parser` and `add_new_parser` with a single function:

```python
def add_init_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "init",
        help="Initialize a kanibako project",
        description="Initialize a kanibako project in the current or given directory.",
    )
    p.add_argument(
        "path", nargs="?", default=None,
        help="Project directory (default: cwd). Created if it doesn't exist.",
    )
    p.add_argument(
        "--local", action="store_true",
        help="Use decentralized mode (all state inside the project directory)",
    )
    p.add_argument(
        "-i", "--image", default=None,
        help="Container image to use for this project",
    )
    p.add_argument(
        "--no-vault", action="store_true",
        help="Disable vault directories (shared read-only and read-write mounts)",
    )
    p.add_argument(
        "--distinct-auth", action="store_true",
        help="Use distinct credentials (no sync from host)",
    )
    p.set_defaults(func=run_init)
```

Delete `add_new_parser` entirely.

**Step 4: Update `cli.py`**

In `src/kanibako/cli.py`:
- Change import: `from kanibako.commands.init import add_init_parser` (remove `add_new_parser`)
- Remove `add_new_parser(subparsers)` call
- Remove `"new"` from `_SUBCOMMANDS` set

**Step 5: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_init_cmd.py::TestInitParser -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/kanibako/commands/init.py src/kanibako/cli.py tests/test_init_cmd.py
git commit -m "Merge init + new parsers, add --image flag to init"
```

---

### Task 2: Implement unified `run_init` with all project modes

**Files:**
- Modify: `src/kanibako/commands/init.py:63-124`
- Test: `tests/test_init_cmd.py`

**Step 1: Write failing tests for new run_init behavior**

Replace `TestRunInit`, `TestRunNew`, and related tests:

```python
class TestRunInit:
    def test_init_local_creates_project(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project_dir)])
        rc = run_init(args)

        assert rc == 0
        resolved = project_dir.resolve()
        assert (resolved / ".kanibako").is_dir()

    def test_init_local_cwd(
        self, config_file, credentials_dir, project_dir, monkeypatch, capsys,
    ):
        """init --local with no path uses cwd."""
        monkeypatch.chdir(project_dir)
        parser = build_parser()
        args = parser.parse_args(["init", "--local"])
        rc = run_init(args)

        assert rc == 0
        resolved = project_dir.resolve()
        assert (resolved / ".kanibako").is_dir()

    def test_init_creates_nonexistent_path(
        self, config_file, credentials_dir, tmp_home, capsys,
    ):
        target = tmp_home / "brand-new-project"
        assert not target.exists()
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(target)])
        rc = run_init(args)

        assert rc == 0
        assert target.is_dir()
        assert (target / ".kanibako").is_dir()

    def test_init_already_exists_fails(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project_dir)])
        run_init(args)

        capsys.readouterr()
        rc = run_init(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "already initialized" in captured.err

    def test_init_ac_mode(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """init without --local creates an AC project."""
        parser = build_parser()
        args = parser.parse_args(["init", str(project_dir)])
        rc = run_init(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Initialized" in captured.out

    def test_init_writes_gitignore_for_local(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project_dir)])
        run_init(args)

        gitignore = project_dir.resolve() / ".gitignore"
        assert gitignore.is_file()
        assert ".kanibako/" in gitignore.read_text()

    def test_init_no_gitignore_for_ac(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        """AC mode should not write .gitignore (state is external)."""
        parser = build_parser()
        args = parser.parse_args(["init", str(project_dir)])
        run_init(args)

        gitignore = project_dir.resolve() / ".gitignore"
        assert not gitignore.is_file()
```

**Step 2: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_init_cmd.py::TestRunInit -v`
Expected: FAIL

**Step 3: Implement unified `run_init`**

Replace `run_init` and delete `run_new` in `src/kanibako/commands/init.py`:

```python
def run_init(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    vault_enabled = not getattr(args, "no_vault", False)
    auth = "distinct" if getattr(args, "distinct_auth", False) else None
    project_dir = args.path

    # Create directory if it doesn't exist
    if project_dir is not None:
        target = Path(project_dir)
        if not target.exists():
            target.mkdir(parents=True)

    if args.local:
        proj = resolve_decentralized_project(
            std, config, project_dir, initialize=True,
            vault_enabled=vault_enabled, auth=auth,
        )
    else:
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
            vault_enabled=vault_enabled if vault_enabled is not True else None,
        )

    if not proj.is_new:
        print(f"Error: project already initialized in {proj.project_path}", file=sys.stderr)
        return 1

    # Persist image setting
    image = args.image or config.container_image
    from kanibako.config import write_project_config
    project_toml = proj.metadata_path / "project.toml"
    write_project_config(project_toml, image)

    # Write .gitignore for decentralized projects
    if args.local:
        _write_project_gitignore(proj.project_path)

    mode = "decentralized" if args.local else "account-centric"
    print(f"Initialized {mode} project in {proj.project_path}")
    return 0
```

Note: the import of `resolve_project` needs to be added to the top of the file.

**Step 4: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_init_cmd.py::TestRunInit -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/kanibako/commands/init.py tests/test_init_cmd.py
git commit -m "Implement unified run_init with AC/local modes and --image"
```

---

### Task 3: Update remaining tests and add image persistence tests

**Files:**
- Modify: `tests/test_init_cmd.py`

**Step 1: Write tests for image persistence and remaining flags**

```python
class TestInitImage:
    def test_init_persists_image(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        from kanibako.config import load_merged_config
        parser = build_parser()
        args = parser.parse_args([
            "init", "--local", str(project_dir),
            "--image", "kanibako-template-jvm-oci",
        ])
        run_init(args)

        project_toml = project_dir.resolve() / ".kanibako" / "project.toml"
        merged = load_merged_config(config_file, project_toml)
        assert merged.container_image == "kanibako-template-jvm-oci"

    def test_init_default_image_persisted(
        self, config_file, credentials_dir, project_dir, capsys,
    ):
        from kanibako.config import load_merged_config
        parser = build_parser()
        args = parser.parse_args(["init", "--local", str(project_dir)])
        run_init(args)

        project_toml = project_dir.resolve() / ".kanibako" / "project.toml"
        merged = load_merged_config(config_file, project_toml)
        assert "kanibako" in merged.container_image  # default image
```

Update `TestInitNoVault` and `TestInitDistinctAuth` to use new parser
format (positional `path` instead of `-p`). Remove all `run_new` references.

**Step 2: Run full test file**

Run: `~/.venv/bin/pytest tests/test_init_cmd.py -v`
Expected: PASS

**Step 3: Run full test suite + mypy**

Run: `~/.venv/bin/pytest tests/ -x -q --ignore=tests/integration`
Run: `~/.venv/bin/mypy src/kanibako/commands/init.py src/kanibako/cli.py --ignore-missing-imports`
Expected: All pass

**Step 4: Commit**

```bash
git add tests/test_init_cmd.py
git commit -m "Add image persistence tests, update init tests for unified command"
```

---

### Task 4: Clean up references to `new` command

**Files:**
- Search: all files referencing `new` command, `run_new`, `add_new_parser`
- Modify: README, any docs referencing `kanibako new`

**Step 1: Search for stale references**

```bash
grep -rn "kanibako new\b" src/ tests/ README.md docs/
grep -rn "run_new\|add_new_parser" src/ tests/
```

**Step 2: Update any found references**

Replace `kanibako new --local <path>` with `kanibako init --local <path>` in docs.

**Step 3: Run full test suite**

Run: `~/.venv/bin/pytest tests/ -x -q --ignore=tests/integration`
Expected: All pass

**Step 4: Commit**

```bash
git commit -am "Remove stale references to kanibako new command"
```
