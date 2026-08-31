"""What the SEEDED claude template may declare about the MODEL — which is nothing.

WHY THIS MODULE EXISTS
----------------------
The template was once refreshed from a working box's real ``~/.claude/settings.json``,
and the paste carried that box's ``"model": "default"`` in with it. ``default`` is not
one of Claude Code's model aliases, so the harness put the word on the wire, the
endpoint had no such model, and the VS Code panel of every box created from that seed
answered each prompt with *"There's an issue with the selected model (default). It may
not exist or you may not have access to it."* The panel was broken on arrival, and a
box is seeded ONCE — so the boxes that got it stayed broken.

⚑ THE RULE, NOT THE VALUE. The cure is not a better literal: no literal can be right.
A box's model is CASCADE-RESOLVED (``agent.<node>.model``) and delivered by kanibako at
launch, so a value frozen into the seed is stale the moment the cascade says anything
else — and a value naming a real model would be a silent pin rather than a loud 404,
which is worse. With the key ABSENT the harness picks the default the account actually
has. So the assertion is on the key's absence, and it takes no position on which model
is right.

⚑ Its sibling ``test_claude_seed_hooks`` asserts what the hook commands in these same
bytes must say; both read through the one ``shipped_settings`` reader.
"""

from __future__ import annotations

from tests.test_claude_seed_hooks import shipped_settings


def test_shipped_seed_declares_no_model():
    """A seeded model cannot track the cascade, so the template must name none."""
    settings = shipped_settings()
    # ⚑ The absence assertion below passes vacuously on an empty object, which a broken
    # packaging step could produce — so prove there is a template here to be wrong.
    assert settings, "the shipped claude seed template parsed to an empty object"
    assert "model" not in settings, (
        "the shipped claude seed pins a model "
        f"({settings['model']!r}); the box's model is resolved through "
        "agent.<node>.model and delivered at launch, so any literal here either goes "
        "stale or silently overrides the cascade"
    )
