"""kanibako: Run AI coding agents in rootless containers with per-project isolation."""

__version__ = "1.8.0"

# Two-tier setup/config compatibility constants for the 5-band setup-completion
# gate (design: ``plans/2026-06-23-setup-version-tiers-NEXT.md``).  Both are
# compared against the recorded ``system.setup_completed`` marker by BASE version
# (PEP 440 via ``packaging.version``), so a dev/rc build of the same base reads
# as the released base, not "from the future".
#
# * ``SETUP_BCV`` — backward-compatible version: the OLDEST setup/config version
#   this build can function with AT ALL.  Below it, the build can no longer
#   auto-fill the gaps → the gate ERRORS (rc1) and the user must re-run setup.
#   Bump only when a setup change can no longer be auto-filled.
# * ``SETUP_FCV`` — forward-compatible version: the oldest version whose setup is
#   COMPLETELY compatible (nothing new since).  At or above it (but below the
#   running build) the gate SILENTLY bumps the marker forward.  Between BCV and
#   FCV the gate NUDGES (non-blocking) to re-run setup.  Bump whenever setup adds
#   a new feature/case.
#
# Invariant: ``SETUP_BCV <= SETUP_FCV <= __version__`` (base versions).  Most
# releases change setup in no way → bump NEITHER → existing configs land
# ``>= FCV`` → silent.
SETUP_BCV = "1.6.0"
SETUP_FCV = "1.8.0"
