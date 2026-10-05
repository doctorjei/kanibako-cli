"""The early-system record a test builds without a ``std``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import overload

from kanibako.channels.channels import workset_token
from kanibako.settings.paths import BoxMode, resolve_system_paths
from kanibako.settings.workset_dirkeys import EarlyScope, EarlySystem, early_system


@overload
def early_record(home: Path, tier: Mapping[str, str | None] | None = None, *,
                 data_home: Path | None = None) -> EarlySystem: ...
@overload
def early_record(home: Path, tier: Mapping[str, str | None] | None = None, *,
                 mode: BoxMode, name: str | None = None,
                 data_home: Path | None = None) -> EarlyScope: ...
def early_record(home, tier=None, *, mode=None, name=None, data_home=None):
    """The early-system record over *tier* (empty by default) and the default paths under *home*.

    *data_home* defaults to *home*.  Given *mode*, the record comes back as the
    :class:`EarlyScope` of a *mode* box in the workset *name*.
    """
    resolved = resolve_system_paths({}, data_home=home if data_home is None else data_home, home=home)
    record = early_system({} if tier is None else tier, resolved)
    return record if mode is None else EarlyScope(record, workset_token(mode, name))
