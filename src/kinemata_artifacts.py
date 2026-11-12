"""Dev-only artifact ledger adapter for the public import loader."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from kinemata.adapters.mapping import MappingRegistry
from kinemata.contract import at_path, field_path

from kanibako.settings.config_io import parse_doc_text, read_doc_text


class ArtifactRegistry(MappingRegistry):
  """Read ledger records; the public loader owns selection and naming."""

  def __init__(
    self,
    *,
    section: str | list[str],
    clause_field: str | None = None,
    where: object = None,
  ) -> None:
    document = parse_doc_text(
      read_doc_text(
        Path(__file__).resolve().parents[1] / "generated-artifacts.yaml", encoding="utf-8"
      )
    )
    source = at_path(document, field_path(section)) if isinstance(document, Mapping) else None
    if not isinstance(source, Mapping):
      raise ValueError(f"artifact section {section!r} is missing or is not a mapping")
    super().__init__(source, clause_field=clause_field)
