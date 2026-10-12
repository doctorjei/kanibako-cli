"""The one urllib handler that refuses to follow a redirect."""

from __future__ import annotations

import urllib.request


class NoRedirects(urllib.request.HTTPRedirectHandler):
  """Refuse every redirect: urllib would re-send the caller's headers cross-origin.

  The 3xx surfaces as an ``HTTPError`` instead, ``Location`` intact — following it
  is the caller's decision, made with the caller's own credentials.  Used by
  ``kanibako.targets.base.http_probe`` and the Anthropic proxy
  (``kanibako.proxy.server``).
  """

  def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
    return None
