"""Image-baseline manifest: the universal in-box runtime tool contract.

The baseline is a mapping of ``apt-package-name -> [executables]``.  The shipped
default lives in :mod:`kanibako.data` (``image-baseline.yaml``); site and user
overlays are merged on top **additively** (the scoped-commons spirit in
:mod:`kanibako.settings_shares`): later layers add new packages or override an
existing package's executable list.  Precedence, least- to most-specific:

    built-in default  <  /etc/kanibako/image-baseline.yaml  <  ~/.config/kanibako/image-baseline.yaml

The package NAME is what ``apt-get install`` consumes (auto-install assumes
apt/debian); the EXECUTABLE values are what ``command -v`` probes (verify is
package-manager-agnostic).  They differ on purpose (e.g. ``ripgrep`` -> ``rg``).
"""

from __future__ import annotations

import importlib.resources
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

from kanibako.paths import xdg

# Filename used both for the shipped default (in kanibako.data) and the overlays.
BASELINE_FILENAME = "image-baseline.yaml"


def _read_doc(path: Path) -> dict[str, list[str]]:
    """Parse a baseline YAML file into ``{package: [executables]}``.

    Tolerates a missing/empty file (returns ``{}``).  Normalizes each value to
    a list of strings; a bare string value becomes a single-element list.
    """
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[str]] = {}
    for pkg, exes in raw.items():
        if exes is None:
            result[str(pkg)] = []
        elif isinstance(exes, str):
            result[str(pkg)] = [exes]
        else:
            result[str(pkg)] = [str(e) for e in exes]
    return result


def _shipped_default() -> dict[str, list[str]]:
    """Read the bundled default baseline shipped as package data."""
    ref = importlib.resources.files("kanibako.data").joinpath(BASELINE_FILENAME)
    return _read_doc(Path(str(ref)))


def _overlay_paths() -> list[Path]:
    """Overlay locations, in additive merge order (machine then user)."""
    config_home = xdg("XDG_CONFIG_HOME", ".config")
    return [
        Path("/etc/kanibako") / BASELINE_FILENAME,
        config_home / "kanibako" / BASELINE_FILENAME,
    ]


def load_baseline() -> dict[str, list[str]]:
    """Return the merged baseline ``{package: [executables]}``.

    Starts from the shipped default, then additively merges the machine
    (``/etc``) and user (``~/.config``) overlays if present: a later layer adds
    a new package or replaces an existing package's executable list.  Missing
    overlay files are treated as empty levels.
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

    *probe* answers "is this executable present?" (typically a ``command -v``
    check inside an image).  An empty result means the baseline is satisfied.
    """
    return [(pkg, exe) for pkg, exe in executables() if not probe(exe)]


def install_command(pkgs: list[str]) -> list[str]:
    """Build the apt-get install argv for *pkgs* (debian).

    Returns the full argv (``apt-get install -y --no-install-recommends ...``).
    The caller decides where it runs (host vs. in-box) and on non-debian
    systems should skip it (see :func:`warn_non_debian`).
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
