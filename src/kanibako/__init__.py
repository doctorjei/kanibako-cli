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
# ⚑ 1.6.0 → 1.8.0 (R-38 rider, 2026-08-01).  1.8.0 restructures the packaged
# template root (M-11: ``system.base_template`` → ``system.template`` + the ``box/``
# subtree) — the can't-auto-fill class BCV exists for — and the HARD block that used
# to be delivered by the now-retired template-staleness gate must not degrade to a
# nudge.  With BCV still at 1.6.0 a 1.7.x marker landed in the BCV..FCV NUDGE band;
# at 1.8.0 it lands below BCV and the gate raises (verified 2026-08-02 against
# ``setup_compat_gate``'s band order and pinned by
# ``TestSetupCompatGate::test_v1_7_era_marker_is_hard_blocked``).
SETUP_BCV = "1.8.0"
SETUP_FCV = "1.8.0"
