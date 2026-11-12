"""Text-only YAML conversion; PyYAML is optional and imported on demand.

The result proves typed record values, not alias identity or source layout.
Yapyon's export limits apply unless the caller supplies explicit limits; an
application must reconcile those defaults before adopting this converter.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
import json
import math

import yapyon  # type: ignore[import-untyped]


class ConversionError(ValueError):
  """Source cannot be converted without losing its selected YAML meaning."""


@dataclass(frozen=True)
class Note:
  kind: str
  message: str
  path: tuple = ()
  line: int | None = None


@dataclass(frozen=True)
class Comment:
  text: str
  path: tuple
  line: int
  column: int
  placement: str = "value"
  relocated: bool = False


@dataclass(frozen=True)
class Conversion:
  text: str
  data: object
  notes: tuple[Note, ...]
  comments: tuple[Comment, ...]
  verified: bool
  yaml_verified: bool | None


@dataclass
class _Occurrence:
  kind: str
  start: int
  end: int
  line: int
  column: int
  value: object = None
  tag: str | None = None
  style: str | None = None
  flow: bool = False
  anchor: str | None = None
  children: list = field(default_factory=list)
  path: tuple = ()
  boundary: int = 0
  code_end: int = 0
  relocated: bool = False


class _Lines:
  def __init__(self, text, line_breaks="\n"):
    self.ends = [i for i, c in enumerate(text) if c in line_breaks]
    self.starts = [0] + [i + 1 for i in self.ends]
    self.ends.append(len(text))

  def line(self, index):
    return bisect.bisect_right(self.starts, index) - 1

  def end(self, index):
    return self.ends[self.line(index)]

  def offset(self, where):
    return self.starts[where[1] - 1] + where[2]


def _same(left, right, seen=None):
  if seen is None:
    seen = set()
  pair = (id(left), id(right))
  if pair in seen:
    return True
  seen.add(pair)
  if isinstance(left, dict) and isinstance(right, dict):
    return len(left) == len(right) and all(
      _same(k, rk, seen) and _same(v, rv, seen)
      for (k, v), (rk, rv) in zip(left.items(), right.items())
    )
  if isinstance(left, list) and isinstance(right, list):
    return len(left) == len(right) and all(_same(v, rv, seen) for v, rv in zip(left, right))
  if type(left) is not type(right):
    return False
  if type(left) is float:
    return (math.isnan(left) and math.isnan(right)) or left.hex() == right.hex()
  return left == right


def _quoted(value):
  text = json.dumps(value, ensure_ascii=False)
  return "".join(
    f"\\u{ord(c):04x}"
    if 0x7F <= ord(c) <= 0x9F or ord(c) in (0x2028, 0x2029, 0xFEFF, 0xFFFE, 0xFFFF)
    else c
    for c in text
  )


def _compatible(handle):
  if handle.kind == "mapping":
    keys = handle.keys()
    skeleton = yapyon.parse("{" + ", ".join(_quoted(key) + ": 0" for key in keys) + "}")
    for key in keys:
      _compatible(handle[key])
      skeleton[key].replace(handle[key])
    handle.replace(skeleton)
  elif handle.kind == "sequence":
    for child in handle:
      _compatible(child)
  elif handle.kind == "scalar":
    if handle.type == "str":
      handle.replace(yapyon.parse(_quoted(handle.value)))
    elif handle.type == "float":
      text = repr(handle.value)
      if "e" in text:
        mantissa, exponent = text.split("e")
        if "." not in mantissa:
          text = mantissa + ".0e" + exponent
      handle.replace(yapyon.parse(text))
    elif handle.type == "bytes":
      raise ConversionError(
        f"at {handle.path!r}: bytes have no verified YAML-readable Yapyon spelling"
      )


def _tags(node, yaml):
  result, seen, pending = {}, set(), [node] if node is not None else []
  while pending:
    node = pending.pop()
    if id(node) in seen:
      continue
    seen.add(id(node))
    result[(node.start_mark.index, node.end_mark.index)] = node.tag
    if isinstance(node, yaml.MappingNode):
      pending.extend(n for pair in node.value for n in pair)
    elif isinstance(node, yaml.SequenceNode):
      pending.extend(node.value)
  return result


def _occurrences(text, yaml, Loader):
  tags = _tags(yaml.compose(text, Loader=Loader), yaml)
  events = iter(yaml.parse(text, Loader=Loader))
  anchors = {}
  next(events)
  first = next(events)
  if isinstance(first, yaml.events.StreamEndEvent):
    return None, anchors
  if not isinstance(first, yaml.events.DocumentStartEvent):
    raise ConversionError("expected one YAML document")

  def consume(event):
    start, end = event.start_mark.index, event.end_mark.index
    kind = (
      "alias"
      if isinstance(event, yaml.events.AliasEvent)
      else "scalar"
      if isinstance(event, yaml.events.ScalarEvent)
      else "mapping"
      if isinstance(event, yaml.events.MappingStartEvent)
      else "sequence"
    )
    node = _Occurrence(
      kind,
      start,
      end,
      event.start_mark.line,
      event.start_mark.column,
      value=getattr(event, "value", None),
      tag=tags.get((start, end), getattr(event, "tag", None)),
      style=getattr(event, "style", None),
      flow=bool(getattr(event, "flow_style", False)),
      anchor=getattr(event, "anchor", None),
    )
    if node.anchor is not None and kind != "alias":
      anchors[node.anchor] = node
    if kind in ("mapping", "sequence"):
      end_type = yaml.events.MappingEndEvent if kind == "mapping" else yaml.events.SequenceEndEvent
      child = next(events)
      while not isinstance(child, end_type):
        key = consume(child)
        if kind == "mapping":
          node.children.append((key, consume(next(events))))
        else:
          node.children.append(key)
        child = next(events)
      node.end = child.end_mark.index
    return node

  return consume(next(events)), anchors


def _recover(text, yaml, Loader, keep_comments):
  root, anchors = _occurrences(text, yaml, Loader)
  tokens = list(yaml.scan(text, Loader=Loader))
  lines = _Lines(text, "\n\x85\u2028\u2029")
  values, collections, prefixes, notes = [], [], [], []
  marker_tokens = defaultdict(list)
  for token in tokens:
    if isinstance(token, yaml.tokens.BlockEntryToken):
      marker_tokens[token.start_mark.column].append(token)
  marker_positions = {
    column: [t.start_mark.index for t in entries] for column, entries in marker_tokens.items()
  }

  def key_node(key):
    return anchors[key.anchor] if key.kind == "alias" else key

  def walk(node, path, boundary=None, relocated=False):
    node.path, node.relocated = path, relocated
    node.boundary = node.start if boundary is None else boundary
    values.append(node)
    if node.kind == "alias":
      notes.append(
        Note(
          "alias",
          "alias expanded to independent values; object identity is not preserved",
          path,
          node.line + 1,
        )
      )
    if node.kind in ("scalar", "alias"):
      node.code_end = node.end
      return
    collections.append(node)
    if node.kind == "mapping":
      for key, value in node.children:
        original = key_node(key)
        merge = original.tag == "tag:yaml.org,2002:merge"
        if merge:
          child_path = path
          notes.append(
            Note(
              "merge",
              "YAML merge materialized; its comments move to the receiving mapping",
              path,
              key.line + 1,
            )
          )
        else:
          if original.kind != "scalar" or original.tag != "tag:yaml.org,2002:str":
            raise ConversionError("cannot associate a non-string YAML key without changing it")
          child_path = path if relocated else path + (original.value,)
        prefixes.append((key.end, child_path, relocated or merge))
        walk(value, child_path, key.end, relocated or merge)
    else:
      markers = ()
      if not node.flow and node.children:
        positions = marker_positions.get(node.column, ())
        first = bisect.bisect_left(positions, node.start)
        last = bisect.bisect_left(positions, node.end)
        markers = marker_tokens.get(node.column, ())[first:last]
      for index, child in enumerate(node.children):
        child_path = path if relocated else path + (index,)
        boundary = child.start
        if not node.flow and index < len(markers):
          marker = markers[index]
          boundary = marker.end_mark.index
          prefixes.append((boundary, child_path, relocated))
        walk(child, child_path, boundary, relocated)
    ends = (
      [n.code_end for pair in node.children for n in pair[1:]]
      if node.kind == "mapping"
      else [n.code_end for n in node.children]
    )
    node.code_end = node.end if node.flow else max(ends, default=node.start)

  if root is not None:
    walk(root, ())
  if not keep_comments:
    return (), tuple(notes)
  protected = bytearray(len(text))
  for token in tokens:
    start, end = token.start_mark.index, token.end_mark.index
    protected[start:end] = b"\1" * (end - start)
    if isinstance(token, yaml.tokens.ScalarToken) and token.style in ("|", ">"):
      newline = lines.end(start)
      header = text.find("#", start, newline)
      if header >= 0:
        protected[header:newline] = b"\0" * (newline - header)
  blocks = sorted((n for n in values if n.style in ("|", ">")), key=lambda n: n.start)
  block_starts = [n.start for n in blocks]
  collection_stream = sorted(collections, key=lambda n: (n.boundary, -n.end))
  child_tables = {}
  for node in collections:
    children = [pair[1] for pair in node.children] if node.kind == "mapping" else node.children
    child_tables[id(node)] = ([n.start for n in children], children)
  active, next_collection = [], 0
  preceding = sorted(
    [(n.code_end, n.path, n.relocated) for n in values] + prefixes, key=lambda v: (v[0], len(v[1]))
  )
  ends = [entry[0] for entry in preceding]
  comments, index = [], 0
  while index < len(text):
    if text[index] != "#" or protected[index]:
      index += 1
      continue
    end = lines.end(index)
    line = lines.line(index)
    column = index - lines.starts[line]
    path, placement, relocated = (), "value", False
    block_index = bisect.bisect_right(block_starts, index) - 1
    block = blocks[block_index] if block_index >= 0 else None
    if block is not None and index >= block.end:
      block = None
    while active and active[-1].end <= index:
      active.pop()
    while (
      next_collection < len(collection_stream)
      and collection_stream[next_collection].boundary <= index
    ):
      node = collection_stream[next_collection]
      next_collection += 1
      if node.end > index:
        active.append(node)
    if root is None or index < root.start:
      placement = "header"
    elif index >= root.code_end and line > lines.line(root.code_end - 1):
      placement = "footer"
    elif block:
      node = block
      path, relocated = node.path, node.relocated
    else:
      before = bisect.bisect_right(ends, index) - 1
      if before >= 0 and lines.line(max(0, preceding[before][0] - 1)) == line:
        _, path, relocated = preceding[before]
      else:
        node = next(
          (n for n in reversed(active) if n is root or n.flow or n.column <= column), None
        )
        if node is not None:
          starts, children = child_tables[id(node)]
          child_index = bisect.bisect_right(starts, index)
          chosen = children[child_index] if child_index < len(children) else node
          path, relocated = chosen.path, chosen.relocated
        else:
          placement = "header" if index < root.code_end else "footer"
    comments.append(Comment(text[index:end], path, line + 1, column, placement, relocated))
    index = end
  return tuple(comments), tuple(notes)


def _handle(tree, path):
  handle = tree.root
  for step in path:
    handle = handle[step]
  return handle


def _place(text, comments, parse_options):
  tree, lines = yapyon.parse(text, **parse_options), _Lines(text)
  groups, header, footer = defaultdict(list), [], []
  for ordinal, comment in enumerate(comments):
    if comment.placement == "header":
      header.append((ordinal, comment))
    elif comment.placement == "footer":
      footer.append((ordinal, comment))
    else:
      try:
        where = _handle(tree, comment.path).where
      except (KeyError, IndexError, TypeError) as exc:
        raise ConversionError(
          f"comment path {comment.path!r} does not survive conversion"
        ) from exc
      groups[lines.offset(where)].append((ordinal, comment, where[2]))
  chunks, placed, cursor, length = [], {}, 0, 0

  def append(part):
    nonlocal length
    chunks.append(part)
    length += len(part)

  def put(ordinal, comment):
    start = length
    append(comment.text)
    placed[ordinal] = (start, length)

  for ordinal, comment in header:
    put(ordinal, comment)
    append("\n")
  for coordinate, entries in sorted(groups.items()):
    append(text[cursor:coordinate])
    append(" ")
    for ordinal, comment, column in entries:
      put(ordinal, comment)
      append("\n" + " " * column)
    cursor = coordinate
  append(text[cursor:])
  for ordinal, comment in footer:
    put(ordinal, comment)
    append("\n")
  changed = "".join(chunks)
  final, final_lines = yapyon.parse(changed, **parse_options), _Lines(changed)
  if sorted(placed) != list(range(len(comments))):
    raise ConversionError("comment occurrence accounting failed")
  for ordinal, comment in enumerate(comments):
    start, end = placed[ordinal]
    if changed[start:end] != comment.text:
      raise ConversionError("comment text changed during insertion")
  for entries in groups.values():
    last_end = placed[entries[-1][0]][1]
    for _, comment, _ in entries:
      target = final_lines.offset(_handle(final, comment.path).where)
      if changed[last_end:target].strip():
        raise ConversionError(f"comment association changed at {comment.path!r}")
  return changed


def from_yaml(
  text: str,
  *,
  source: str | None = None,
  Loader=None,
  keep_comments: bool = True,
  yaml_compatible: bool = False,
  max_digits: int | None = None,
  max_depth: int | None = None,
  max_size: int | None = None,
) -> Conversion:
  """Convert one safe YAML document without file access or type coercion.

  ``Loader`` may be a SafeLoader subclass supplying the caller's dialect;
  application shape/depth policies remain the caller's responsibility.
  ``yaml_compatible`` requires both readers to preserve typed, ordered data.
  Explicit size bounds include comments and adapted spelling; export limits
  otherwise pass through. Comment text/order survives at each destination;
  trailing comments may move before values, merge comments to their mapping.
  """
  if not isinstance(text, str):
    raise TypeError("from_yaml expects text")
  try:
    import yaml
  except ImportError as exc:
    raise ConversionError("YAML conversion requires optional PyYAML") from exc
  Loader = yaml.SafeLoader if Loader is None else Loader
  safe: tuple[type, ...] = (yaml.SafeLoader,)
  if hasattr(yaml, "CSafeLoader"):
    safe += (yaml.CSafeLoader,)
  if not isinstance(Loader, type) or not issubclass(Loader, safe):
    raise TypeError("Loader must be a SafeLoader subclass")
  text = text.replace("\r\n", "\n").replace("\r", "\n")
  label = f"{source}: " if source else ""
  try:
    data = yaml.load(text, Loader=Loader)
    options = {
      k: v
      for k, v in (("max_digits", max_digits), ("max_depth", max_depth), ("max_size", max_size))
      if v is not None
    }
    read_options = {} if max_digits is None else {"max_digits": max_digits}
    tree = yapyon.parse(yapyon.dumps(data, **options), **read_options)
    if yaml_compatible:
      _compatible(tree.root)
    emitted = yapyon.emit(tree)
    comments, notes = _recover(text, yaml, Loader, keep_comments)
    converted = _place(emitted, comments, read_options)
    if max_size is not None and len(converted.encode("utf-8")) > max_size:
      raise ConversionError("converted UTF-8 exceeds explicit max_size")
    if not _same(data, yapyon.loads_record(converted, **read_options)):
      raise ConversionError("typed/order-sensitive Yapyon verification failed")
    if yaml_compatible and not _same(data, yaml.load(converted, Loader=Loader)):
      raise ConversionError("typed/order-sensitive YAML verification failed")
  except (yaml.YAMLError, yapyon.AkanError, TypeError, ValueError, RecursionError) as exc:
    if isinstance(exc, ConversionError) and not label:
      raise
    raise ConversionError(label + str(exc)) from exc
  return Conversion(converted, data, notes, comments, True, True if yaml_compatible else None)
