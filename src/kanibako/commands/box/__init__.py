"""kanibako box: project lifecycle management (list, migrate, duplicate, archive, purge, restore)."""

from kanibako.commands.box._duplicate import run_duplicate
from kanibako.commands.box._migrate import run_migrate
from kanibako.commands.box._parser import (
    add_parser,
    run_get,
    run_info,
    run_list,
    run_orphan,
    run_resource_list,
    run_resource_set,
    run_resource_unset,
    run_set,
)

__all__ = [
    "add_parser", "run_duplicate", "run_get", "run_info",
    "run_list", "run_migrate", "run_orphan",
    "run_resource_list", "run_resource_set", "run_resource_unset", "run_set",
]
