"""kanibako proxy — a local listener that owns the Anthropic surface.

A stdlib-only HTTP proxy for boxes pointed at an OpenAI-compatible / LiteLLM
gateway that speaks ``/v1/messages`` natively but has been observed to speak it
wrong.  It runs PASSTHROUGH by default for every model and de-streams only the
models it is configured to; ``designs/anthropic-proxy-DESIGN.md`` owns the modes
and the measurement that made FIXUP opt-in rather than always-on.

* ``sse``    — the synthesis: a complete response in, SSE frames out.  Pure, and
  the part that must be exactly right.
* ``server`` — the listener: read the client request, decide the mode by model,
  forward with ``urllib.request``, relay the result.

⚑ ADDITIVE AND INERT.  This package ships wired to nothing: no CLI verb, no
launch integration, no settings key, and no import from anywhere outside it.
Standing up a delivery route is a separate, boarded decision — the additive
route goes in first and nothing is repointed at it until it is proven.

⚑ STDLIB ONLY, and specifically never LiteLLM: LiteLLM is the bug source.

PUBLIC SURFACE: the submodules named in ``__all__``.  Consumers import the
SUBMODULE — ``from kanibako.proxy.server import ProxyServer`` — never a name
re-exported here.

⚑ DELIBERATELY IMPORT-FREE, the uniform rule for every package in this tree:
eager re-exports would load the whole package on any submodule import, and
``sse`` is pure and dependency-light precisely so it can be imported alone.

IN-PACKAGE IMPORTS ARE ABSOLUTE (``from kanibako.proxy.sse import X``), never
relative.
"""

__all__ = [
  "server",
  "sse",
]
