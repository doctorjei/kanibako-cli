"""Centralized configuration I/O, YAML/native codecs, and document mutators.

A config DOCUMENT is a settings-cascade file, agent file, registry, or
``spawn.yaml``; native tool configurations retain their own codecs. A DOCUMENT
MUTATOR performs read-modify-write at a ``(sections, leaf)`` path without knowing
the KEYSPACE. :mod:`kanibako.settings.config_keys` and
:mod:`kanibako.settings.config_dest` locate keys within files.
Python packaging's ``pyproject.toml`` is excluded.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tomllib
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Callable

import yaml

from kanibako import boxfs
from kanibako._atomic import atomic_write_text
from kanibako.errors import ConfigError, PackagingError


_NULL_TAG = "tag:yaml.org,2002:null"


def _read_none_as_null(cls: type) -> None:
  """Make a PLAIN ``None`` resolve to null for *cls*, a loader or a dumper.

  ⚑ kanibako's own YAML reads ``None`` as ``null``, because a user who types Python's
  spelling means the null, never the four-letter string.  Only the plain scalar: a quoted
  ``'None'`` stays a string, and the dumper (which carries the same resolver) quotes a str
  ``'None'`` so it reads back as written.  Exactly ``None``; ``none``/``NONE`` stay strings.
  """
  cls.add_implicit_resolver(_NULL_TAG, re.compile(r"^None$"), ["N"])  # type: ignore[attr-defined]


class _PackagedLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):  # type: ignore[misc]
  """libyaml where PyYAML has it, else pure Python; a SafeLoader either way."""


_read_none_as_null(_PackagedLoader)

#: The deepest table/list nesting one document may hold, counted so the outermost
#: mapping is 1.  ⚑ A HOST-SAFETY FLOOR, NOT A TASTE CALL: the loader composes and the
#: key-check walks the node tree by recursion, so a document that outruns Python's stack
#: reaches the user as a bare ``RecursionError`` traceback from whatever walked it next.
#: No declared key path comes near this — the keyspace's deepest route is a handful.
MAX_DOC_DEPTH = 64


def parse_standalone_marker(raw: bytes) -> str | None:
  """Read a YamlPlus string marker; invalid documents and other shapes are not markers."""
  from yapyon import AkanError, yamlplus  # type: ignore[import-untyped]

  try:
    value = yamlplus.loads(raw.decode("utf-8"))
  except (UnicodeDecodeError, AkanError):
    return None
  return value if isinstance(value, str) else None


def render_standalone_marker(marker: str) -> str:
  """Emit the standalone marker as a YamlPlus string document."""
  from yapyon import yamlplus  # type: ignore[import-untyped]

  return yamlplus.dumps(marker)  # type: ignore[no-any-return]


def read_packaged_bytes(ref: Traversable) -> bytes:
  """Read exact bytes from a packaged configuration resource."""
  return ref.read_bytes()


def persona_store_location() -> Path:
  """Return PG's public store root without creating it."""
  from persona_grata import AgentStore  # type: ignore[import-untyped]

  return Path(AgentStore().root)


def persona_store_agents(*, root: Path | None = None) -> dict[str, Path]:
  """Read registered names and paths through PG without resolving or fetching secrets."""
  from persona_grata import AgentStore
  from yapyon import AkanError  # type: ignore[import-untyped]

  store = AgentStore(root)
  try:
    return dict(store.agents())
  except AkanError as exc:
    raise ConfigError(f"persona store at {store.root}: {exc}") from exc


def load_persona_agent(name: str, *, root: Path | None = None) -> tuple[Path, dict[str, Any]]:
  """Read a registered agent's current composed configuration through PG."""
  from persona_grata import AgentStore
  from yapyon import AkanError  # type: ignore[import-untyped]

  store = AgentStore(root)
  try:
    return Path(store.locate(name)), store.load(name)
  except AkanError as exc:
    raise ConfigError(f"PG agent {name!r} at {store.root}: {exc}") from exc


def fetch_persona_secret(name: str, *, root: Path | None = None) -> str:
  """Fetch by registered name; child output never enters diagnostics."""
  argv = [sys.executable, "-I", "-m", "persona_grata", f"--get-secret={name}"]
  if root is not None:
    argv.extend(["--store", str(root)])
  try:
    with subprocess.Popen(
      argv,
      stdin=subprocess.DEVNULL,
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
      start_new_session=True,
    ) as process:
      try:
        output, _ = process.communicate(timeout=120)
      except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
          os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
          pass
        process.communicate()
        raise
      status = process.returncode
  except subprocess.TimeoutExpired:
    raise ConfigError(f"PG agent {name!r}: credential retrieval timed out") from None
  except OSError:
    raise ConfigError(f"PG agent {name!r}: could not run credential retrieval") from None
  if status:
    raise ConfigError(f"PG agent {name!r}: credential retrieval failed (exit {status})")
  try:
    token = output.decode("utf-8").removesuffix("\n")
  except UnicodeError:
    raise ConfigError(f"PG agent {name!r}: credential is not UTF-8 text") from None
  if not token.strip() or any(char in token for char in "\x00\r\n"):
    raise ConfigError(f"PG agent {name!r}: credential must be a nonempty single line")
  return token


def parse_native_yaml(text: str | bytes) -> Any:
  """Parse native YAML without kanibako document guards or normalization."""
  return yaml.safe_load(text)


def parse_packaged(text: str) -> object:
  """Parse a YAML file kanibako SHIPS (never a user's file) with the fastest safe loader."""
  return yaml.load(text, Loader=_PackagedLoader)


def load_packaged_mapping(ref: Traversable, filename: str) -> dict[str, Any]:
  """Read packaged Yapyon into plain containers; malformed/empty/non-mapping data raises.

  I/O and decoding errors propagate; parse failures name the resource and retain their cause.
  """
  from yapyon import AkanError, loads  # type: ignore[import-untyped]

  text = Path(str(ref)).read_text()
  try:
    raw = loads(text, source=str(ref))
  except AkanError as exc:
    raise PackagingError(f"{filename} (read from {ref}) is invalid Yapyon: {exc}") from exc
  if not isinstance(raw, dict):
    raise PackagingError(f"{filename} (read from {ref}) is empty or is not a Yapyon mapping")
  return _plain_containers(raw)  # type: ignore[no-any-return]


def _plain_containers(value: Any) -> Any:
  if isinstance(value, dict):
    return {key: _plain_containers(child) for key, child in value.items()}
  if isinstance(value, list):
    return [_plain_containers(child) for child in value]
  return value


def yaml_problem(exc: yaml.YAMLError) -> str:
  """One-line rendering of a YAML parse failure (the problem + where).

  ⚑ THE ONE RENDERER, AND IT IS PUBLIC BECAUSE IT HAS A SECOND READER: a file's own
  loader (``rig.yaml``'s, an overlay's) wraps the failure in its own error type, and a
  private helper would leave that reader re-spelling the same failure in different words —
  one rule, one place that knows it.
  """
  if isinstance(exc, yaml.MarkedYAMLError) and exc.problem:
    mark = exc.problem_mark
    where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
    return f"{exc.problem}{where}{_unquoted_brace_hint(mark)}"
  return " ".join(str(exc).split())


#: A ``key: value`` or ``- value`` line whose VALUE opens with an unquoted ``{``.
_BRACE_VALUE_RE = re.compile(r"^(\s*(?:-\s+)?(?:[^\s#'\"{-][^#]*?:\s+)?)(\{[^#]*?)\s*(?:\s#.*)?$")


def _unquoted_brace_hint(mark: yaml.Mark | None) -> str:
  """The cure when the failure is at or after an unquoted ``{``-leading value: YAML read
  the ``{`` as a flow mapping, so a ``{$NAME}``/``{a.b}`` reference must be quoted."""
  buffer = getattr(mark, "buffer", None)
  if mark is None or not isinstance(buffer, str):
    return ""
  lines = buffer.rstrip("\0").split("\n")
  m = _BRACE_VALUE_RE.match(lines[mark.line]) if mark.line < len(lines) else None
  if m is None or mark.column < m.end(1):
    return ""
  quoted = "'" + m.group(2).replace("'", "''") + "'"
  return f"; a value starting with '{{' must be quoted: {m.group(1).strip()} {quoted}".rstrip()


class DuplicateKeyError(yaml.YAMLError):
  """A mapping in one document spells the same key twice.

  ⚑ A ``yaml.YAMLError``, so every caller's existing ``except yaml.YAMLError`` already
  means "this document is not readable" — a reader that forgot this subclass would read
  the repeat as last-wins again.
  """

  def __init__(self, dotted: str, first: yaml.Mark, second: yaml.Mark) -> None:
    super().__init__(dotted)
    self.dotted = dotted
    # 1-based, as an editor shows them; the column only where the line cannot tell them apart.
    same_line = first.line == second.line
    self.first, self.second = (
      f"line {m.line + 1}" + (f", column {m.column + 1}" if same_line else "")
      for m in (first, second)
    )

  def __str__(self) -> str:
    return f"sets '{self.dotted}' twice ({self.first} and {self.second})"


class CyclicAnchorError(yaml.YAMLError):
  """A mapping or list is reachable from inside itself (an alias inside its own anchor)."""

  def __init__(self, dotted: str) -> None:
    super().__init__(dotted)
    self.dotted = dotted

  def __str__(self) -> str:
    return f"refers to itself at '{self.dotted}' (a YAML alias inside its own anchor)"


class DocumentTooDeepError(yaml.YAMLError):
  """A document nests deeper than :data:`MAX_DOC_DEPTH`.

  *dotted* is the path of the offending table, or ``""`` when the refusal belongs to
  the document as a whole (a flow collection the loader gave up composing).
  """

  def __init__(self, dotted: str, limit: int) -> None:
    super().__init__(dotted)
    self.dotted, self.limit = dotted, limit

  def __str__(self) -> str:
    at = f" at '{self.dotted}'" if self.dotted else ""
    return f"nests deeper than {self.limit} levels{at}"


_MERGE_TAG = "tag:yaml.org,2002:merge"
_VALUE_TAG = "tag:yaml.org,2002:value"


class _UniqueKeyLoader(yaml.SafeLoader):
  """``SafeLoader`` that refuses a mapping repeating a key, at any depth."""

  def construct_document(self, node: yaml.Node) -> object:
    # ⚑ Checked on the node tree, before construction, because PyYAML keeps the LAST of a
    # repeated key without a word; the tree is also the only place the key's path and lines exist.
    self._check_duplicates(node, "", set())
    return super().construct_document(node)

  def _check_duplicates(self, node: yaml.Node, where: str, seen_nodes: set[int]) -> None:
    if id(node) in seen_nodes:  # an alias revisits a node (and can be recursive)
      return
    seen_nodes.add(id(node))
    if isinstance(node, yaml.SequenceNode):
      for i, item in enumerate(node.value):
        self._check_duplicates(item, f"{where}[{i}]", seen_nodes)
    elif isinstance(node, yaml.MappingNode):
      first_mark: dict[object, yaml.Mark] = {}
      for key_node, value_node in node.value:
        if key_node.tag == _MERGE_TAG:
          # ``<<`` is YAML's own override mechanism, not a repeated key — but the maps it
          # merges are still maps, and a repeat inside one is still lost.
          self._check_duplicates(value_node, f"{where}.<<" if where else "<<", seen_nodes)
          continue
        # A bare ``=`` key is tagged ``value``; SafeConstructor reads it as the string
        # ``"="`` (``flatten_mapping``) but cannot construct it on its own.
        key = "=" if key_node.tag == _VALUE_TAG else self.construct_object(key_node, deep=True)
        dotted = f"{where}.{key}" if where else str(key)
        try:
          if key in first_mark:
            raise DuplicateKeyError(dotted, first_mark[key], key_node.start_mark)
          first_mark[key] = key_node.start_mark
        except TypeError:  # unhashable key: SafeConstructor refuses it itself
          pass
        self._check_duplicates(value_node, dotted, seen_nodes)


_read_none_as_null(_UniqueKeyLoader)


def _guard_document(data: object) -> None:
  """Refuse a document that CONTAINS ITSELF, or that nests deeper than MAX_DOC_DEPTH.

  ⚑⚑ ITERATIVE, WITH AN EXPLICIT STACK, and that is the whole point: the recursion a
  recursive guard would use is exactly what a cyclic anchor drives to the stack limit.
  ⚑ AN ANCESTOR, NOT A SIBLING.  Two keys MAY name one table — the ``<<`` merge key
  does it by name, and ``a: &x {…}`` + ``b: *x`` does it by alias — so sharing is legal
  and loads; only a path back INTO ITSELF is a document that never finishes.

  ⚑⚑ THE WALK IS LINEAR IN DISTINCT CONTAINERS, NOT IN PATHS.  A document that fans out
  over shared aliases — the billion-laughs shape, each level nine aliases to the level
  below — reaches one table by 9**n PATHS, and walking every path turned a few hundred
  bytes of a user's ``rig.yaml`` into minutes of host CPU, on a file that arrives inside
  an image bundle, from whoever built the bundle.  ``reached`` memoizes each container at
  the DEEPEST depth the walk reached it at, so a container already walked at this depth
  or deeper is not walked again; the work is bounded by the number of distinct containers
  times the number of distinct depths one can reach each at.

  ⚑ DEEPEST, NOT FIRST, AND THAT IS THE CYCLE GUARD.  The memo may decide only how many
  times a container is walked, never whether it is CHECKED: the ancestor test runs when a
  child is pushed, before the child is popped, so it fires for every sighting. And a loop
  can only close by reaching a container DEEPER than some earlier sighting of it, so a
  memo that remembered the first sighting would skip exactly the re-walk that finds the
  loop. ``MAX_DOC_DEPTH`` bounds how deep that can go, which is why a walk that keeps
  failing to settle ends at one of the two refusals rather than loading a cycle.
  """
  stack: list[tuple[object, str, int, frozenset[int]]] = [(data, "", 1, frozenset())]
  reached: dict[int, int] = {}  # id(container) -> the deepest depth it was walked at
  while stack:
    value, dotted, depth, ancestors = stack.pop()
    # Scalars are leaves: a table's own depth is what the limit is about.
    if isinstance(value, dict):
      kids: list[tuple[str, object]] = [
        (f"{dotted}.{k}" if dotted else str(k), v) for k, v in value.items()
      ]
    elif isinstance(value, list):
      kids = [(f"{dotted}[{i}]", item) for i, item in enumerate(value)]
    else:
      continue
    # Measured BEFORE the memo: a memo entry only ever holds a depth this check passed,
    # so a container arriving past the limit can never be one this walk already covered.
    if depth > MAX_DOC_DEPTH:
      raise DocumentTooDeepError(dotted, MAX_DOC_DEPTH)
    # `id` is a stable identity here: the whole document is alive and reachable from
    # `data` for the length of the walk, so no two live containers can share one.
    walked_at = reached.get(id(value))
    if walked_at is not None and walked_at >= depth:
      continue  # this subtree was walked at this depth or deeper already
    reached[id(value)] = depth
    chain = ancestors | {id(value)}
    for child_path, child in kids:
      if isinstance(child, (dict, list)) and id(child) in chain:
        raise CyclicAnchorError(child_path)
      stack.append((child, child_path, depth + 1, chain))


def parse_doc_text(text: str) -> object:
  """Parse owned configuration text with the same guards as :func:`load_doc`."""
  return _parse_guarded_yaml(text)


def parse_template_yaml(text: str) -> object:
  """Parse native template YAML independently of the owned configuration codec."""
  return _parse_guarded_yaml(text)


def _parse_guarded_yaml(text: str) -> object:
  try:
    data = yaml.load(text, Loader=_UniqueKeyLoader)  # a SafeLoader: no arbitrary objects
  except RecursionError as exc:
    # ⚑ A flow collection long enough to outrun the Python stack is the SAME refusal
    # the depth guard makes by measurement; the loader cannot say where it gave up.
    raise DocumentTooDeepError("", MAX_DOC_DEPTH) from exc
  _guard_document(data)
  return data


def read_doc_text(path: Path, *, encoding: str | None = None) -> str:
  """Read host configuration text; box-writable paths require the box-file seam."""
  return path.read_text(encoding=encoding)


def read_doc_text_capped(path: Path, *, cap: int, encoding: str | None = None) -> str:
  """Read at most ``cap + 1`` characters of host configuration text.

  The extra character is the point: a caller that passes its own limit can tell a
  WHOLE file from an OVERSIZED one by ``len(result) > cap``, without ever reading
  the rest. Use this where a config file has a declared shape (one line, one path)
  and an unbounded read would mean slurping an arbitrary file off disk.

  Raises the same ``OSError`` / ``UnicodeDecodeError`` a plain read does, so the
  caller's handling is unchanged.
  """
  with open(path, encoding=encoding) as stream:
    return stream.read(cap + 1)


def write_doc_text(path: Path, text: str) -> None:
  """Atomically write host configuration text; box-writable paths require the box-file seam."""
  atomic_write_text(path, text)


def read_doc_bytes(path: Path) -> bytes:
  """Read raw host bytes through links; box paths require the box-file seam."""
  return path.read_bytes()


def overwrite_doc_bytes_in_existing_parent(path: Path, data: bytes) -> int:
  """Overwrite host bytes in place through links; never create parents."""
  return path.write_bytes(data)


def append_doc_text_in_existing_parent(path: Path, text: str) -> None:
  """Append host text through links with default encoding; never create parents."""
  with open(path, "a") as stream:
    stream.write(text)


def unlink_doc_file(path: Path, *, missing_ok: bool = False) -> None:
  """Unlink the host leaf, not its link target, with native missing-file behavior."""
  path.unlink(missing_ok=missing_ok)


def read_box_doc_bytes(path: Path, root: Path) -> bytes:
  """Read box configuration bytes through the explicit-root no-follow seam."""
  return boxfs.read_bytes(path, root)


def write_box_doc_bytes(path: Path, root: Path, data: bytes) -> None:
  """Atomically replace box configuration bytes through the no-follow seam."""
  boxfs.write_bytes(path, root, data)


def overwrite_box_doc_bytes_in_existing_parent(path: Path, root: Path, data: bytes) -> int:
  """Overwrite box bytes in place through the explicit-root seam; never create parents."""
  return boxfs.write_bytes_in_place(path, root, data)


def read_box_doc_text(path: Path, root: Path) -> str:
  """Read UTF-8 box configuration text through the no-follow box-file seam."""
  return boxfs.read_text(path, root)


def write_box_doc_text(path: Path, root: Path, text: str) -> None:
  """Atomically replace box configuration text through the no-follow box-file seam."""
  boxfs.write_text(path, root, text)


def overwrite_doc_text(path: Path, text: str) -> None:
  """Overwrite host text in place, following links; box paths require the box-file seam."""
  path.parent.mkdir(parents=True, exist_ok=True)
  overwrite_doc_text_in_existing_parent(path, text)


def overwrite_doc_text_in_existing_parent(path: Path, text: str) -> None:
  """Overwrite host text in place with default encoding; never create parents."""
  path.write_text(text)


def copy_doc_file(src: Path, dst: Path) -> None:
  """Copy host configuration with copy2 metadata/link semantics; do not create parents."""
  shutil.copy2(str(src), str(dst))


def replace_json_doc(path: Path, data: Any) -> None:
  """Render before fixed-sibling ``.json.tmp`` write/replace; parents must exist."""
  tmp = path.with_suffix(".json.tmp")
  overwrite_doc_text_in_existing_parent(tmp, render_json_text(data, indent=2) + "\n")
  tmp.replace(path)


def parse_json_text(text: str | bytes | bytearray) -> Any:
  """Parse native JSON without shape checks or error normalization."""
  return json.loads(text)


def parse_toml_text(text: str) -> dict[str, Any]:
  """Parse native TOML without shape checks or error normalization."""
  return tomllib.loads(text)


def _strip_jsonc(text: str) -> str:
  """Best-effort strip of JSONC (comments + trailing commas) to plain JSON."""
  text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
  # ⚑ WHOLE-LINE ``//`` only, deliberately: an inline ``//`` may be inside a string value.
  text = re.sub(r"(?m)^\s*//.*$", "", text)
  text = re.sub(r",(\s*[}\]])", r"\1", text)
  return text


def load_jsonc(text: str) -> object | None:
  """Parse JSONC text, returning the object or ``None`` if unparseable."""
  try:
    return parse_json_text(text)
  except ValueError:
    pass
  try:
    return parse_json_text(_strip_jsonc(text))
  except ValueError:
    return None


def load_json_doc(path: Path) -> Any:
  """Read native host JSON without shape checks or error normalization."""
  with open(path) as stream:
    return json.load(stream)


def dump_json_doc(path: Path, data: Any, *, indent: int | None = None) -> None:
  """Stream native host JSON after truncation, preserving in-place write behavior."""
  path.parent.mkdir(parents=True, exist_ok=True)
  with open(path, "w") as stream:
    json.dump(data, stream, indent=indent)


def render_json_text(
  data: Any, *, separators: tuple[str, str] | None = None, indent: int | None = None
) -> str:
  """Render native JSON with unchanged standard-library defaults."""
  return json.dumps(data, separators=separators, indent=indent)


def load_doc(path: Path | None) -> dict:
  """Load a config document → dict. Missing/empty → {}; any other non-mapping, a repeated key,
  a key that is not a ``str``, a self-reference, or unbounded nesting raises.

  ⚑ Only ``ENOENT``/``ENOTDIR`` read as missing, never ``Path.exists()``: a file kanibako
  cannot reach (EACCES on it or a parent, a link loop) refuses, naming it."""
  if path is None:
    return {}
  try:
    text = read_doc_text(path)
  except (FileNotFoundError, NotADirectoryError):
    return {}
  except OSError as exc:
    if exc.errno in (errno.EACCES, errno.EPERM, errno.ELOOP):
      # ⚑ The file may be fine: the cure is to make the path reachable, not to edit it.
      from kanibako.utils.files import unreadable_path_error

      raise unreadable_path_error(path, exc) from exc
    raise ConfigError(
      f"the config file {path} could not be read ({exc.strerror}). "
      "Fix or remove the file, then retry."
    ) from exc
  except UnicodeDecodeError as exc:
    raise ConfigError(
      f"the config file {path} is not valid UTF-8 text. Fix or remove the file, then retry."
    ) from exc
  # ⚑ HOST-SAFETY GUARD, not a type nicety: a non-str fed to the yaml loader can OOM the box.
  if not isinstance(text, str):
    return {}
  # ⚑ THE PARSE-FAILURE NORMALIZATION BELONGS HERE — this is the one seam that knows the FILE.
  try:
    data = parse_doc_text(text)
  except DuplicateKeyError as dup:
    # ⚑ Spec §0: never a silent accept — PyYAML would keep the second and drop the first.
    raise ConfigError(
      f"the config file {path} sets '{dup.dotted}' twice ({dup.first} and "
      f"{dup.second}). Remove one of the two, then retry."
    ) from None
  except (CyclicAnchorError, DocumentTooDeepError) as exc:
    raise ConfigError(
      f"the config file {path} {exc}. Fix or remove the file, then retry."
    ) from None
  except yaml.YAMLError as exc:
    raise ConfigError(
      f"the config file {path} is not valid YAML: {yaml_problem(exc)}. "
      "Fix or remove the file, then retry."
    ) from exc
  if data is None:
    return {}
  # ⚑ Spec §0: a document that is one value or a list holds no keys to read, and reading it
  # as empty would silently drop whatever the user meant by it.
  if not isinstance(data, dict):
    shape = "a list" if isinstance(data, list) else "a single value"
    raise ConfigError(
      f"the config file {path} is {shape}, not a mapping of keys. "
      "Fix or remove the file, then retry."
    )
  where = _unnamed_key(data)
  if where is not None:
    raise ConfigError(
      f"the config file {path} has a key at '{where[0]}' that YAML reads as "
      f"{render_stored_scalar(where[1])}, not a name. Quote that key, then retry."
    )
  return data


def _unnamed_key(data: dict) -> tuple[str, object] | None:
  """``(table path, key)`` of a mapping key in *data* that is not a ``str``, else None.

  ⚑ Config documents only (:func:`load_doc`), never :func:`parse_doc_text`: a template
  file kanibako merely compares may hold ``on:`` or ``8080:`` keys legitimately.
  Iterative, and each container is walked once, so a shared alias costs nothing.
  """
  stack: list[tuple[object, str]] = [(data, "")]
  seen: set[int] = set()
  while stack:
    value, dotted = stack.pop()
    if id(value) in seen:
      continue
    seen.add(id(value))
    if isinstance(value, dict):
      for k, v in value.items():
        if not isinstance(k, str):
          return (dotted or "(top level)", k)
        stack.append((v, f"{dotted}.{k}" if dotted else k))
    elif isinstance(value, list):
      stack.extend((item, f"{dotted}[{i}]") for i, item in enumerate(value))
  return None


class _SafeDumper(yaml.SafeDumper):
  """``yaml.SafeDumper`` that writes a ``str`` SUBCLASS as a plain string.

  BARE-REF-GUARD: ``settings_resolve.DataExpr`` is a ``str`` subclass, which the stock
  safe representer refuses outright.
  """


_SafeDumper.add_multi_representer(str, yaml.SafeDumper.represent_str)
_read_none_as_null(_SafeDumper)


def _dump_text(data: dict) -> str:
  return yaml.dump(
    data,
    Dumper=_SafeDumper,
    sort_keys=False,
    default_flow_style=False,
    allow_unicode=True,
  )


def dump_doc(path: Path, data: dict, *, header: str = "") -> None:
  """Serialize *data* to *path* as YAML, atomically (creates parent dirs), after *header*."""
  write_doc_text(path, header + _dump_text(data))


# ---------------------------------------------------------------------------
# Document mutators (load → mutate → dump)
# ---------------------------------------------------------------------------


# ⚑ No key routes to empty ``sections`` — this pair is the nested pair's
# structural complement, not dead code.  See llm-docs.
def write_root_key(path: Path, key: str, value: object) -> None:
  """Write a TOP-LEVEL scalar key, preserving other content."""
  data = load_doc(path)
  data[key] = value
  dump_doc(path, data)


def remove_root_key(path: Path, key: str) -> bool:
  """Remove a TOP-LEVEL scalar key.  Returns True if it was present."""
  from kanibako.utils.files import stat_unless_gone  # lazy: config_io stays import-light

  if stat_unless_gone(path) is None:
    return False
  data = load_doc(path)
  if key not in data:
    return False
  del data[key]
  dump_doc(path, data)
  return True


def refuse_scalar_sections(
  path: Path,
  sections: tuple[str, ...],
  *,
  data: dict | None = None,
) -> None:
  """RAISE :class:`ConfigError` when *sections* walks into a value that is not a table.

  Every section walked is a namespace, so a value where a table belongs refuses,
  ``null`` included (spec §0).  ⚑ THE SHAPE RULE, STATED HERE: an absent section, or a
  *path* that is not a file, is a writer's to create, so neither refuses.  *data* is the
  ALREADY-READ doc, not re-read.
  """
  from kanibako.utils.files import is_file_unless_gone  # lazy: config_io stays import-light

  if not is_file_unless_gone(path):
    return
  node = load_doc(path) if data is None else data
  for depth, sec in enumerate(sections):
    sec = _resolved_section(node, sec)
    if sec not in node:
      return
    child = node[sec]
    if not isinstance(child, dict):
      dotted = ".".join((*sections[:depth], sec))
      raise ConfigError(
        f"the config file {path} holds {render_stored_scalar(child)} at '{dotted}', "
        f"where a table of keys belongs, so '{dotted}.' keys cannot be written under it. "
        f"Fix or delete '{dotted}' in that file by hand, then retry."
      )
    node = child


def write_nested_key(
  path: Path,
  sections: tuple[str, ...],
  key: str,
  value: object,
) -> None:
  """Write *key* into a nested table (e.g. ``("system", "path")``), creating ABSENT intermediates.

  Raises ConfigError when an intermediate is present but not a table, ``null`` included:
  every section walked is a namespace, where no value is a setting the closed keyspace
  accepts (spec §0) — :func:`refuse_scalar_sections` owns that refusal.

  ⚑ A NODE SECTION is stored as a human writes it, ``persona+harness`` -- see
  :func:`_resolved_section`, which this walk and ``stored_leaf_object``'s share.
  """
  data = load_doc(path)
  refuse_scalar_sections(path, sections, data=data)
  node = data
  for sec in sections:
    sec = _resolved_section(node, sec)
    node = node.setdefault(sec, {})
  node[key] = value
  dump_doc(path, data)


def _resolved_section(table: dict, section: str) -> str:
  """*section* as *table* spells it -- a node's SEPARATOR, resolved to whichever the file holds.

  A node already in the table WINS, so a write cannot leave one node in two spellings and a
  read reaches a file stored either way. 🛑 THE SEPARATOR ALONE: a node's CASE is Q87's fold,
  and that fold WARNS -- a walk that silently reached a capital node would make that a lie.
  """
  from kanibako.agent_ref import CANONICAL_SEP, display_agent_ref

  if CANONICAL_SEP not in section:
    return section
  user_facing = display_agent_ref(section)
  for spelling in table:
    if isinstance(spelling, str) and display_agent_ref(spelling) == user_facing:
      return spelling
  return user_facing


def remove_nested_key(
  path: Path,
  sections: tuple[str, ...],
  key: str,
) -> bool:
  """Remove *key* from a nested table, pruning now-empty intermediates.  True if found."""
  from kanibako.utils.files import stat_unless_gone  # lazy: config_io stays import-light

  if stat_unless_gone(path) is None:
    return False

  data = load_doc(path)

  # Walk to the innermost table, recording the chain for pruning.
  # ⚑ *walked* is the RESOLVED spelling, so the prune below deletes the key the walk matched.
  chain: list[dict] = [data]
  walked: list[str] = []
  node = data
  for sec in sections:
    resolved = _resolved_section(node, sec)
    child = node.get(resolved)
    if not isinstance(child, dict):
      return False
    walked.append(resolved)
    node = child
    chain.append(node)

  if key not in node:
    return False
  del node[key]

  # Prune empty tables bottom-up.
  for i in range(len(walked) - 1, -1, -1):
    if not chain[i + 1]:
      del chain[i][walked[i]]
    else:
      break
  dump_doc(path, data)
  return True


def count_leaves(node: object) -> int:
  """Count the leaf entries under a nested table — the unit every ``reset --all`` reports."""
  if isinstance(node, dict):
    return sum(count_leaves(v) for v in node.values())
  return 1


# ---------------------------------------------------------------------------
# Stored-value reads (the ``get`` model's stored-at-noun read + its rendering)
# ---------------------------------------------------------------------------


def render_stored_scalar(v: object) -> str:
  """Render a stored scalar for ``get`` output, keeping §2h's empty idioms apart.

  ⚑⚑ TOTAL, AND THAT IS THE POINT (P3).  Absence is reported ABOVE any render — by
  :func:`read_stored_leaf`, which never reaches here for a leaf that is not in the file —
  so a renderer able to answer ``None`` could only collapse "not set" into a STORED value.
  It used to answer ``None`` for a stored ``""`` and did exactly that: a terminal empty
  string read back "(not set)", which spec §2h forbids in as many words (``""`` ≠ unset).

  THE THREE STORED SPELLINGS, each its own: a present-``None`` (the tri-state OMIT) is
  ``null``; a terminal ``""`` is ``""``; a bool is lowercase, the spelling ``set`` accepts
  back.  ⚑ EACH NAMES THE YAML THE FILE HOLDS, WHICH IS NOT THE SAME AS THE WORD YOU TYPE
  TO WRITE IT.  ``null`` is the file's null, and the write spellings for it are the ``--null``
  FLAG and ``set <key>=None``: ``set <key>=null`` stores the four-character STRING, a
  different value that reads back through this same line.  A ``get`` may only teach values
  the CLI reads.
  """
  if v is None:
    return "null"
  if v == "":
    return '""'
  if isinstance(v, bool):
    return str(v).lower()
  return str(v)


def stored_leaf_object(
  noun_file: "Path | None",
  sections: tuple[str, ...],
  leaf: str,
  *,
  default: object = None,
) -> object:
  """The RAW value stored at ``sections/leaf`` in *noun_file*, or *default* when absent.

  ⚑⚑ THE UNRENDERED READ, AND THE ONLY WALK: :func:`read_stored_leaf` and
  :func:`read_stored_pref` are each this plus their own renderer, which is what keeps the
  two from growing separate ideas of where a leaf lives.
  ⚑⚑ A DOOR THAT JUDGES THE VALUE READS HERE, NEVER THE RENDERING.  The renderings are
  deliberately not injective — a stored ``""`` and a stored ``'""'`` both read back ``""``
  (§2h) — so ``rendered == '""'`` cannot tell a user's two-character label from an empty
  one, and a door asking that question must ask it of the object.
  🛑 *default* IS WHAT "NOT THERE" LOOKS LIKE TO THE CALLER, and the caller owns that
  choice because the raw answer cannot make it: a present-``None`` is indistinguishable
  from the ``None`` default.  Pass a sentinel to tell them apart; leave it ``None`` only
  where absent and present-``None`` mean the same thing to you.
  """
  from kanibako.utils.files import stat_unless_gone  # lazy: config_io stays import-light

  if noun_file is None or stat_unless_gone(noun_file) is None:
    return default
  node: object = load_doc(noun_file)
  for sec in sections:
    if not isinstance(node, dict):
      return default
    node = node.get(_resolved_section(node, sec))
  if not isinstance(node, dict) or leaf not in node:
    return default
  return node[leaf]


#: :func:`stored_leaf_object`'s "not there" answer for the two rendering readers below, which
#: must tell absence from a present-``None``.  Private: a caller outside this module hands in
#: its own, so this object never travels (P11).
_ABSENT = object()


def read_stored_leaf(
  noun_file: "Path | None",
  sections: tuple[str, ...],
  leaf: str,
  *,
  render: "Callable[[object], str]" = render_stored_scalar,
) -> str | None:
  """The value STORED at ``sections/leaf`` in *noun_file*, or ``None`` when absent / no file.

  ⚑ ``None`` MEANS ABSENT AND NOTHING ELSE, and the TOTAL *render* is what guarantees it:
  a leaf that is not there returns above without rendering anything, and no renderer this
  signature accepts can produce that answer for a value that IS there.  The type carries
  the rule, so no caller has to know it (P3).
  ⚑ *render* is a parameter because the scalar convention is not universal — a leaf whose
  stored shape is not a scalar (``agent_file``'s argv list) renders by a rule that belongs
  with the file that owns the shape, not here.  The default keeps every other caller on the
  scalar convention.
  """
  v = stored_leaf_object(noun_file, sections, leaf, default=_ABSENT)
  return None if v is _ABSENT else render(v)


def render_stored_pref(v: object) -> str:
  """Render a stored ``pref`` REQUEST, keeping all THREE empty idioms apart (spec §2h).

  ⚑ IT AGREES WITH :func:`render_stored_scalar` TODAY, STRING FOR STRING, AND IS STILL NOT
  IT.  They answer different questions: that one DECIDES what a scalar leaf reads back as,
  while this one may only FORWARD — *"the pref layer MUST NOT interpret emptiness AT ALL …
  otherwise it becomes a FOURTH place deciding what 'empty' means"* (§2h).  A request also
  carries whatever shape its TARGET holds, so a rule the scalar convention may adopt about
  values that are not scalars is a rule this side must NOT inherit.  Folding them would put
  one branch under two authorities; the agreement is the point, not a reason to merge.
  """
  if v is None:
    return "null"
  if v == "":
    return '""'
  if isinstance(v, bool):
    return str(v).lower()
  return str(v)


def read_stored_pref(
  noun_file: "Path | None",
  sections: tuple[str, ...],
  leaf: str,
  *,
  render: "Callable[[object], str]" = render_stored_pref,
) -> str | None:
  """Read a stored ``pref`` REQUEST, rendering all THREE empty idioms apart (spec §2h).

  ⚑ *render* IS HANDED IN FOR THE SAME REASON IT IS ON :func:`read_stored_leaf`, and the
  two must not grow separate conventions: a leaf that is not there returns ``None`` above
  without rendering anything, and the TOTAL renderer type is what keeps that the only way
  this answers ``None``.  A pref REQUEST carries whatever shape its TARGET key holds —
  ``pref.agent.default.run_args`` carries the argv LIST — and a shape that is not a scalar
  renders by a rule belonging to the file that owns it, not here.  The default keeps every
  other caller on the three pref idioms.
  """
  v = stored_leaf_object(noun_file, sections, leaf, default=_ABSENT)
  return None if v is _ABSENT else render(v)
