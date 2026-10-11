"""kanibako box: project lifecycle management (create, list, config, remap, move, convert, duplicate, archive, extract, purge, vault).

Import-free on purpose (CONVENTIONS § Import structure): import each symbol from its
defining submodule.

* ``_parser``    — ``add_parser`` and the create/list/ps/rm/info/get/set/show/reset verbs.
* ``_lifecycle`` — move, remap, and convert.
* ``_duplicate`` — duplicate.
"""
