"""Dev-only manifest views, importable through the editable install's src path."""

from __future__ import annotations

from collections.abc import Mapping

from kinemata.adapters.mapping import MappingRegistry
from kinemata.contract import at_path, field_path

from kanibako.settings.keyspace_manifest import manifest_doc


class ManifestRegistry(MappingRegistry):
  """Read a manifest section; the public loader owns selection and naming."""

  def __init__(
    self,
    *,
    section: str | list[str],
    clause_field: str | None = None,
    where: object = None,
  ) -> None:
    source = at_path(manifest_doc(), field_path(section))
    if not isinstance(source, Mapping):
      raise ValueError(f"manifest section {section!r} is missing or is not a mapping")
    super().__init__(source, clause_field=clause_field)
