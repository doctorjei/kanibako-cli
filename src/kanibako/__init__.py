"""kanibako: Run AI coding agents in rootless containers with per-project isolation."""

__version__ = "1.6.0"

# The oldest build version whose ``setup`` run is still considered current.  The
# setup-completion gate (W1) re-nudges the user to re-run ``kanibako setup`` when
# the recorded ``system.setup_completed`` version is ABSENT or strictly older
# than this constant (PEP 440 comparison via ``packaging.version``).  Bump this
# ONLY when a setup change requires users to re-run setup — routine releases
# leave it untouched so they never trigger a spurious re-nudge.
OLDEST_COMPATIBLE_SETUP_VERSION = "1.6.0"
