"""The on-disk names of kanibako's own files, spelled ONCE for the test suite.

🛑 DELIBERATELY NOT IMPORTED FROM ``kanibako.settings.bootstrap``.  This literal is
the PIN: a production rename that it does not follow reds every test asserting a
resolved config path.  Import the product's ``CONFIG_FILE`` here instead and each of
those assertions becomes ``constant == constant`` — satisfied by any value, the
wrong one included.

⚑ So the two spellings are kept in step BY HAND, and the red is how you learn they
are not.
"""

from __future__ import annotations

CONFIG_FILENAME = "kanibako_config.yaml"
