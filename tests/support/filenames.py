"""The on-disk names of kanibako's own files, spelled ONCE for the test suite.

🛑 DELIBERATELY NOT IMPORTED FROM ``kanibako.settings.bootstrap``.  These literals are
the PIN: a production rename that they do not follow reds every test asserting a
resolved config path.  Import the product's ``CONFIG_FILE`` here instead and each of
those assertions becomes ``constant == constant`` — satisfied by any value, the
wrong one included.

⚑ So each pair of spellings is kept in step BY HAND, and the red is how you learn they
are not.
"""

from __future__ import annotations

CONFIG_FILENAME = "kanibako.cfg"
SITE_CONFIG_FILENAME = "base.cfg"
SITE_SETTINGS_FILENAME = "settings_base.yaml"
