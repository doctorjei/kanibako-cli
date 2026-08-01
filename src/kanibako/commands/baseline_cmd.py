"""kanibako baseline: inspect, verify, and install the image baseline contract.

The baseline is the universal set of in-box runtime tools kanibako relies on
(see :mod:`kanibako.runtime.baseline`).  ``list`` feeds a Containerfile (``apt-get
install $(kanibako baseline list)``), ``verify`` probes an image for the
required executables, and ``install`` runs the apt-get install of the package
names.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from kanibako.runtime import baseline as baseline_mod


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "baseline",
        help="Inspect/verify the universal in-box tool baseline",
        description=(
            "Inspect, verify, or install the image baseline — the universal set "
            "of in-box runtime tools kanibako relies on across all box variants."
        ),
    )
    sub = p.add_subparsers(dest="baseline_command", metavar="COMMAND")

    # baseline list [--executables]
    list_p = sub.add_parser(
        "list",
        help="Print baseline package names (or executables with --executables)",
    )
    list_p.add_argument(
        "--executables", action="store_true",
        help="Print 'package: exe1 exe2' per line instead of just package names",
    )
    list_p.set_defaults(func=run_list)

    # baseline verify [IMAGE] [--all] [--only PKG ...] [--skip PKG ...]
    verify_p = sub.add_parser(
        "verify",
        help="Probe an image for the baseline executables",
    )
    verify_p.add_argument(
        "image", nargs="?", default=None,
        help="Image to probe (default: the configured box_image)",
    )
    verify_p.add_argument(
        "--all", action="store_true", dest="all_images",
        help="Probe every local kanibako-* image",
    )
    verify_p.add_argument(
        "--only", nargs="+", default=None, metavar="PKG",
        help="Only check these baseline packages",
    )
    verify_p.add_argument(
        "--skip", nargs="+", default=None, metavar="PKG",
        help="Skip these baseline packages",
    )
    verify_p.set_defaults(func=run_verify)

    # baseline install [--only PKG ...] [--skip PKG ...]
    install_p = sub.add_parser(
        "install",
        help="apt-get install the baseline packages (debian; current environment)",
    )
    install_p.add_argument(
        "--only", nargs="+", default=None, metavar="PKG",
        help="Only install these baseline packages",
    )
    install_p.add_argument(
        "--skip", nargs="+", default=None, metavar="PKG",
        help="Skip these baseline packages",
    )
    install_p.add_argument(
        "--dry-run", action="store_true",
        help="Print the install command instead of running it",
    )
    install_p.set_defaults(func=run_install)

    # Default to list when 'baseline' is run bare.
    p.set_defaults(func=run_list)


def _filter_packages(
    pkgs: list[str], only: list[str] | None, skip: list[str] | None,
) -> list[str]:
    """Apply --only / --skip filters to a sorted package list."""
    result = pkgs
    if only:
        only_set = set(only)
        result = [p for p in result if p in only_set]
    if skip:
        skip_set = set(skip)
        result = [p for p in result if p not in skip_set]
    return result


def run_list(args: argparse.Namespace) -> int:
    """Print the baseline.

    Default: package names, space-separated on one line — a Containerfile
    consumes this verbatim via ``$(kanibako baseline list)``, so stdout stays
    clean (no logging).  With ``--executables``: ``package: exe1 exe2`` lines.
    """
    if getattr(args, "executables", False):
        baseline = baseline_mod.load_baseline()
        for pkg in sorted(baseline):
            exes = " ".join(baseline[pkg])
            print(f"{pkg}: {exes}")
        return 0

    pkgs = baseline_mod.packages()
    print(" ".join(pkgs))
    return 0


def _make_probe(runtime, image: str):
    """Return a ``probe(exe) -> bool`` that runs an ephemeral container.

    Each probe runs ``<runtime> run --rm <image> sh -lc 'command -v <exe>'``
    and reports success on a zero exit code.
    """
    def probe(exe: str) -> bool:
        result = subprocess.run(
            [
                runtime.cmd, "run", "--rm", image,
                "sh", "-lc", f"command -v {exe}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    return probe


def _verify_image(runtime, image: str, pkgs: list[str]) -> list[tuple[str, str]]:
    """Probe *image* for the executables of *pkgs*. Return missing pairs."""
    baseline = baseline_mod.load_baseline()
    wanted = {pkg: baseline[pkg] for pkg in pkgs if pkg in baseline}
    probe = _make_probe(runtime, image)
    missing: list[tuple[str, str]] = []
    for pkg in sorted(wanted):
        for exe in wanted[pkg]:
            if not probe(exe):
                missing.append((pkg, exe))
    return missing


def run_verify(args: argparse.Namespace) -> int:
    """Probe one or all images for the baseline executables.

    Exit code: 1 if any executable is missing in any probed image, else 0.
    """
    from kanibako.config import config_file_path, load_merged_config
    from kanibako.runtime.container import ContainerRuntime
    from kanibako.errors import ContainerError
    from kanibako.paths import xdg

    try:
        runtime = ContainerRuntime()
    except ContainerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    pkgs = _filter_packages(
        baseline_mod.packages(),
        getattr(args, "only", None),
        getattr(args, "skip", None),
    )

    # Determine the image(s) to probe.
    if getattr(args, "all_images", False):
        images = [repo for repo, _size in runtime.list_local_images()]
        if not images:
            print("No local kanibako images found.", file=sys.stderr)
            return 1
    else:
        image = getattr(args, "image", None)
        if not image:
            config_home = xdg("XDG_CONFIG_HOME", ".config")
            cf = config_file_path(config_home)
            merged = load_merged_config(cf, None)
            image = merged.box_image
        images = [image]

    any_missing = False
    for image in images:
        missing = _verify_image(runtime, image, pkgs)
        print(f"Baseline verify: {image}")
        if not missing:
            print("  [ok] all baseline executables present")
        else:
            any_missing = True
            for pkg, exe in missing:
                print(f"  [!!] {pkg}: missing '{exe}'")
        print()

    return 1 if any_missing else 0


def run_install(args: argparse.Namespace) -> int:
    """apt-get install the baseline packages in the CURRENT environment.

    This targets the environment the command runs in (e.g. inside a box, or a
    host that uses apt) — it does NOT shell into a separate box.  Use it from a
    Containerfile/box where ``apt-get`` is available and you have privileges.
    Non-debian systems are warned and skipped.
    """
    import shutil

    pkgs = _filter_packages(
        baseline_mod.packages(),
        getattr(args, "only", None),
        getattr(args, "skip", None),
    )
    if not pkgs:
        print("Nothing to install.", file=sys.stderr)
        return 0

    cmd = baseline_mod.install_command(pkgs)

    if getattr(args, "dry_run", False):
        print(" ".join(cmd))
        return 0

    if shutil.which("apt-get") is None:
        baseline_mod.warn_non_debian()
        return 1

    result = subprocess.run(cmd)
    return result.returncode
