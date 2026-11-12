# ArtifactRegistry

Keep this optional-checker adapter outside `kanibako/` and the wheel. Editable
installs expose `src/`; runtime imports must not reach it.

Anchor the hand-maintained ledger to this module's checkout, never cwd. Its owned
text reader preserves typed records; deriving membership from generated docs
would hide deletions. This adapter costs one dev-only import boundary.

As in `kinemata_manifest.py`, accept `where` without applying it: the public
loader owns selection and naming. Inherit MappingRegistry's complete Entry and
matching semantics; `_ViewRegistry` has a different contract.
