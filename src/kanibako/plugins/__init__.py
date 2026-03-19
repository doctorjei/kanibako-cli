"""Namespace package for kanibako plugins.

Uses pkgutil.extend_path so that plugin packages installed in separate
source trees (e.g. editable installs from packages/agent-claude/src/)
merge into a single kanibako.plugins namespace.
"""

import pkgutil

__path__ = pkgutil.extend_path(__path__, __name__)
