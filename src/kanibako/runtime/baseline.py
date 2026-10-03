"""Image-baseline manifest: the universal in-box runtime tool contract.

The baseline is a mapping of ``apt-package-name -> [executables]``.  The shipped
default lives in :mod:`kanibako.data` (``rom/settings/image-baseline.yaml``); site and user
overlays are merged on top **additively** (the scoped-category spirit in
:mod:`kanibako.settings.settings_categories`): later layers add new packages or override an
existing package's executable list.  Precedence, least- to most-specific:

    built-in default  <  /etc/kanibako/image-baseline.yaml  <  ~/.config/kanibako/image-baseline.yaml

The package NAME is what ``apt-get install`` consumes (auto-install assumes
apt/debian); the EXECUTABLE values are what ``command -v`` probes (verify is
package-manager-agnostic).  They differ on purpose (e.g. ``ripgrep`` -> ``rg``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from kanibako.errors import ConfigError
from kanibako.settings.bootstrap import SITE_CONFIG_DIR
from kanibako.settings.config_io import load_doc
from kanibako.settings.core_defaults import PACKAGED_SETTINGS_PARTS, packaged_data_dir
from kanibako.settings.paths import user_config_home

# Filename used both for the shipped default (under ``PACKAGED_SETTINGS_PARTS``) and the overlays.
BASELINE_FILENAME = "image-baseline.yaml"


def _read_doc(path: Path) -> dict[str, list[str]]:
    """Parse a baseline YAML file into ``{package: [executables]}``.

    Tolerates a missing/empty file (returns ``{}``).  Normalizes each value to a list of
    strings; a bare string value becomes a single-element list.  ⚑ Read through
    :func:`load_doc`, the one entry point for a user's YAML: a repeated key or a
    non-mapping document is REFUSED BY NAME (ConfigError).  A package whose value is
    neither a name nor a list of them is REFUSED BY NAME too, here: iterating it would
    either raise a bare ``TypeError`` or accept a table's keys as executable names.
    """
    if not path.is_file():
        return {}
    raw = load_doc(path)
    result: dict[str, list[str]] = {}
    for pkg, exes in raw.items():
        if exes is None:
            result[str(pkg)] = []
        elif isinstance(exes, str):
            result[str(pkg)] = [exes]
        elif isinstance(exes, list) and not any(
            isinstance(e, (list, dict)) or e is None or isinstance(e, bool)
            for e in exes
        ):
            result[str(pkg)] = [str(e) for e in exes]
        else:
            raise ConfigError(
                f"the config file {path} sets '{pkg}' to a value that is not an "
                "executable name or a list of them. Fix or remove that entry, "
                "then retry."
            )
    return result


def _shipped_default() -> dict[str, list[str]]:
    """Read the bundled default baseline shipped as package data."""
    ref = packaged_data_dir(*PACKAGED_SETTINGS_PARTS, BASELINE_FILENAME)
    return _read_doc(Path(str(ref)))


def _overlay_paths() -> list[Path]:
    """Overlay locations, in additive merge order (machine then user)."""
    config_home = user_config_home()
    return [
        Path(SITE_CONFIG_DIR) / BASELINE_FILENAME,
        config_home / "kanibako" / BASELINE_FILENAME,
    ]


def load_baseline() -> dict[str, list[str]]:
    """Return the merged baseline ``{package: [executables]}``.

    Starts from the shipped default, then additively merges the machine (``/etc``) and
    user (``~/.config``) overlays: a later layer adds a package or replaces its list.
    """
    merged = _shipped_default()
    for path in _overlay_paths():
        merged.update(_read_doc(path))
    return merged


def packages() -> list[str]:
    """Return the baseline package names in sorted, stable order."""
    return sorted(load_baseline())


def executables() -> list[tuple[str, str]]:
    """Return ``(package, executable)`` pairs, sorted by package then executable."""
    pairs: list[tuple[str, str]] = []
    baseline = load_baseline()
    for pkg in sorted(baseline):
        for exe in baseline[pkg]:
            pairs.append((pkg, exe))
    return pairs


def verify(probe: Callable[[str], bool]) -> list[tuple[str, str]]:
    """Return the ``(package, executable)`` pairs whose executable is missing.

    *probe* answers "is this present?" (a ``command -v`` check); empty means satisfied.
    """
    return [(pkg, exe) for pkg, exe in executables() if not probe(exe)]


def install_command(pkgs: list[str]) -> list[str]:
    """Build the apt-get install argv for *pkgs* (debian).

    Returns ``apt-get install -y --no-install-recommends <pkgs>``.  The caller decides
    where it runs (host vs. in-box); skip it on non-debian (:func:`warn_non_debian`).
    """
    return [
        "apt-get", "install", "-y", "--no-install-recommends", *pkgs,
    ]


def warn_non_debian() -> None:
    """Emit a stderr warning that auto-install only supports apt/debian."""
    print(
        "kanibako baseline: automatic install assumes apt/debian; "
        "install the baseline packages with your distro's package manager.",
        file=sys.stderr,
    )
