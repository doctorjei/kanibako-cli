# `src/kanibako/commands/image.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/image.py.md`.


## Variables

```
_TEMPLATE_PREFIX = 'kanibako-template-'
_PREP_STATUS = {'none': 'prepped', 'pull': 'unprepped', 'build': 'unprepped', 'missing': 'missing'}
_KNOWN_SUFFIXES = {'min', 'oci', 'lxc', 'vm'}
_FALLBACK_REGISTRY = 'ghcr.io/doctorjei'
```

## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run_extend(args: argparse.Namespace) -> int
def run_list(args: argparse.Namespace) -> int
def run_info(args: argparse.Namespace) -> int
def run_rm(args: argparse.Namespace) -> int
def resolve_image_reference(name: str, runtime: ContainerRuntime, configured_image: str) -> str
def resolve_image_name(name: str, configured_image: str) -> str
def run_prep(args: argparse.Namespace) -> int
def run_update(args: argparse.Namespace) -> int
def run_add(args: argparse.Namespace) -> int
def run_export(args: argparse.Namespace) -> int
def run_import(args: argparse.Namespace) -> int
def _confirm(prompt: str) -> bool
def _bare_repo(repo: str) -> str
def _extract_registry_prefix(image: str) -> str | None
def _build_template(runtime: ContainerRuntime, res: RigResolution, name: str, containers_dir: Path, std) -> int
def _pull_one(runtime: ContainerRuntime, image: str, std=None) -> int
def _update_one(runtime: ContainerRuntime, image: str, std=None) -> int
def _update_all(runtime: ContainerRuntime, std=None) -> int
```
