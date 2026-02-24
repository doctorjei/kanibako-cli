"""kanibako box: project lifecycle management (list, migrate, duplicate, archive, purge, restore)."""

from kanibako.commands.box._duplicate import run_duplicate
from kanibako.commands.box._migrate import run_migrate
from kanibako.commands.box._parser import add_parser, run_info, run_list, run_orphan

__all__ = ["add_parser", "run_duplicate", "run_info", "run_list", "run_migrate", "run_orphan"]
