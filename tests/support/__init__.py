"""Non-shipping, tests-only support package.

Modules here are helpers for the test suite ONLY. They live under ``tests/`` so
they are never included in the built wheel (``setuptools`` ships ``src/`` only)
and are not type-checked (mypy runs against ``src/kanibako/`` only). They ARE
linted by ruff (``ruff check ... tests/``), like every other test module.
"""
