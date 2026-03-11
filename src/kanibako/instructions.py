"""Layered instruction file merging.

Merges instruction files (e.g. CLAUDE.md) from three layers:
  1. **Kanibako base** — container environment documentation
  2. **Template** — shell template instructions
  3. **User project** — user's own instructions (highest priority, shown last)

Each layer is concatenated with section markers so the agent can see
where each part comes from.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.log import get_logger

logger = get_logger("instructions")

# Section markers used to delimit layers in merged instruction files.
_MARKER_BASE = "# --- kanibako base ---"
_MARKER_TEMPLATE = "# --- template: {name} ---"
_MARKER_PROJECT = "# --- project ---"

# Sentinel that marks the end of managed sections.  Content after this
# marker is preserved verbatim (not touched by future merges).
_MARKER_END = "# --- end managed ---"


def _read_layer(path: Path) -> str | None:
    """Read a file and return its stripped content, or None if missing/empty."""
    if not path.is_file():
        return None
    content = path.read_text().strip()
    return content if content else None


def merge_instruction_content(
    *,
    base_content: str | None = None,
    template_content: str | None = None,
    template_name: str = "",
    user_content: str | None = None,
) -> str | None:
    """Merge instruction file layers into a single string.

    Returns the merged content with section markers, or None if all
    layers are empty/missing.

    Layer order (top to bottom):
      1. kanibako base
      2. template
      3. user project (last = highest visibility)
    """
    sections: list[str] = []

    if base_content:
        sections.append(f"{_MARKER_BASE}\n\n{base_content}")

    if template_content:
        marker = _MARKER_TEMPLATE.format(name=template_name or "default")
        sections.append(f"{marker}\n\n{template_content}")

    if user_content:
        sections.append(f"{_MARKER_PROJECT}\n\n{user_content}")

    if not sections:
        return None

    return "\n\n".join(sections) + "\n"


def merge_instruction_files(
    *,
    shell_path: Path,
    config_dir_name: str,
    instruction_files: list[str],
    templates_base: Path | None = None,
    agent_name: str = "",
    template_name: str = "standard",
) -> None:
    """Merge instruction files from base/template/user layers.

    For each filename in *instruction_files*:
      1. Read base content from ``templates_base/general/base/{config_dir_name}/{filename}``
      2. Read template content from the resolved template dir ``{config_dir_name}/{filename}``
      3. Read user content already in ``shell_path/{config_dir_name}/{filename}``
         (placed there by template application or pre-existing)

    The merged result replaces the file at ``shell_path/{config_dir_name}/{filename}``.

    If the file only has content from a single layer, it is written without
    section markers (clean output for the common case).
    """
    if not instruction_files:
        return

    config_dir = shell_path / config_dir_name

    for filename in instruction_files:
        dest = config_dir / filename

        # Layer 1: kanibako base — from general/base/{config_dir}/{filename}
        base_content: str | None = None
        if templates_base:
            base_path = templates_base / "general" / "base" / config_dir_name / filename
            base_content = _read_layer(base_path)

        # Layer 2: template — from resolved template dir
        # The template was already applied by apply_shell_template() which
        # copied all files including instruction files.  We need to read
        # the template's *original* version before it was overlaid.
        template_content: str | None = None
        if templates_base:
            from kanibako.templates import resolve_template

            resolved = resolve_template(templates_base, agent_name, template_name)
            if resolved:
                tmpl_path = resolved / config_dir_name / filename
                template_content = _read_layer(tmpl_path)

        # Layer 3: user project — whatever is at the destination *now*.
        # After apply_shell_template(), this is the template's version of
        # the file (which we already captured above).  We need to detect
        # whether the current content is identical to the template layer
        # to avoid duplicating it.
        user_content: str | None = None
        current_content = _read_layer(dest)
        if current_content:
            # If the current file matches the template content exactly,
            # it's not user content — the template just put it there.
            # Same for base content.
            if current_content != template_content and current_content != base_content:
                user_content = current_content
            elif template_content is None and base_content is None:
                # File exists but no template/base layers — treat as user content.
                user_content = current_content

        # Count non-None layers to decide whether to use markers.
        layers = [x for x in (base_content, template_content, user_content) if x]

        if not layers:
            # No content from any layer — skip (don't create empty file).
            continue

        if len(layers) == 1:
            # Single layer — write without markers for clean output.
            merged: str | None = layers[0] + "\n"
        else:
            merged = merge_instruction_content(
                base_content=base_content,
                template_content=template_content,
                template_name=template_name,
                user_content=user_content,
            )

        if merged:
            config_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(merged)
            logger.debug("Merged instruction file: %s (%d layers)", dest, len(layers))
