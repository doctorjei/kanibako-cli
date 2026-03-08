"""kanibako box: project lifecycle management (create, list, config, migrate, duplicate, archive, purge, restore)."""

from kanibako.commands.box._duplicate import run_duplicate
from kanibako.commands.box._migrate import run_migrate
from kanibako.commands.box._parser import (
    _check_container_running,
    _format_credential_age,
    add_parser,
    run_config,
    run_create,
    run_info,
    run_list,
    run_rm,
)

__all__ = [
    "_check_container_running", "_format_credential_age",
    "add_parser", "run_config", "run_create", "run_duplicate",
    "run_info", "run_list", "run_migrate", "run_rm",
]
