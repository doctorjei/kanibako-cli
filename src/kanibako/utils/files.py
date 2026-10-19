"""File and path helpers: path_hash."""

from __future__ import annotations

import hashlib


def path_hash(project_path: str) -> str:
  """SHA-256 hex digest of the project path string."""
  return hashlib.sha256(project_path.encode()).hexdigest()
