"""Ask a :class:`HelperHub` a question without its socket."""

from __future__ import annotations

import json
import socket
from unittest.mock import MagicMock

from kanibako.channels.helper_listener import HelperHub


def ask(hub: HelperHub, request: dict) -> dict:
    """Route *request* through the hub's dispatch, as its socket reader does.

    For tests of a handler, not of the wire: a socket gave the reply a wall-clock
    deadline that a loaded single-process run can miss.  The JSON round trip keeps
    the check that the request and reply would go on the wire.
    """
    resp, _ = hub._dispatch(MagicMock(spec=socket.socket), json.loads(json.dumps(request)), None)
    assert resp is not None
    return json.loads(json.dumps(resp))
